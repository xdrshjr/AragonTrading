"""OKX V5 公共 REST 客户端 (spec §7)。

职责：拉取真实 K 线 / 标的，归一化（升序 + 秒级 UTC ts），节流 + 重试 + 代理透传。
硬约束 (C1)：取数失败必须抛出明确错误，严禁静默回退到假数据。
"""
from __future__ import annotations

import asyncio
import time

import httpx

from ..config import Settings, get_settings
from .types import Candle, Instrument, _optional_float, parse_okx_candle


class OKXError(Exception):
    """OKX 取数失败（连通性 / 业务错误码）。上层据此返回显式错误态。"""

    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(f"[{code}] {message}")


class _RateLimiter:
    """简单的最小请求间隔节流（令牌桶近似，C10）。"""

    def __init__(self, min_interval: float = 0.12):
        self._min_interval = min_interval
        self._last = 0.0
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            now = time.monotonic()
            wait = self._min_interval - (now - self._last)
            if wait > 0:
                await asyncio.sleep(wait)
            self._last = time.monotonic()


class OKXRestClient:
    """异步 OKX 公共 REST 客户端。"""

    def __init__(self, settings: Settings | None = None):
        self._settings = settings or get_settings()
        self._limiter = _RateLimiter()
        self._client: httpx.AsyncClient | None = None
        self._last_ok_ts: int | None = None       # 最近一次成功取数的秒级 UTC (F2)
        self._last_latency_ms: int | None = None   # 最近一次 health 探活往返毫秒 (F2)

    @property
    def last_ok_ts(self) -> int | None:
        return self._last_ok_ts

    @property
    def last_latency_ms(self) -> int | None:
        return self._last_latency_ms

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self._settings.okx_rest_base,
                timeout=httpx.Timeout(20.0, connect=10.0),
                proxy=self._settings.proxy_url,
                headers={"User-Agent": "aragon-trading/0.1"},
            )
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def _request(self, path: str, params: dict, *, retries: int = 3) -> list:
        """GET 请求 + 节流 + 429/网络指数退避；返回 OKX `data` 数组。"""
        client = await self._get_client()
        last_exc: Exception | None = None
        for attempt in range(retries):
            await self._limiter.acquire()
            try:
                resp = await client.get(path, params=params)
                if resp.status_code == 429:
                    await asyncio.sleep(0.5 * (2 ** attempt))
                    continue
                resp.raise_for_status()
                body = resp.json()
                if str(body.get("code")) != "0":
                    raise OKXError(str(body.get("code")), str(body.get("msg") or "OKX error"))
                self._last_ok_ts = int(time.time())   # 秒级 UTC，最近成功取数 (F2)
                return body.get("data", [])
            except (httpx.TransportError, httpx.HTTPStatusError) as exc:
                last_exc = exc
                await asyncio.sleep(0.4 * (2 ** attempt))
        raise OKXError("OKX_UNREACHABLE", f"无法连接 OKX ({path}): {last_exc}")

    async def health(self) -> bool:
        """轻量探活：能取到一根 K 线即视为 up，并记录一次往返时延 (F2)。"""
        start = time.monotonic()
        try:
            data = await self._request(
                "/api/v5/market/candles",
                {"instId": self._settings.default_inst, "bar": "1H", "limit": 1},
                retries=1,
            )
            if data:
                self._last_latency_ms = int((time.monotonic() - start) * 1000)
                return True
            self._last_latency_ms = None
            return False
        except OKXError:
            self._last_latency_ms = None
            return False

    async def get_candles(
        self,
        inst_id: str,
        bar: str,
        limit: int = 300,
        *,
        before: int | None = None,
        after: int | None = None,
    ) -> list[Candle]:
        """拉取历史 K 线，返回**升序**（旧→新）。

        本接口入参（秒级，C2）：
          - ``before`` = 只取**早于**该时刻的更早历史（左滚回溯，前端唯一用法）；
          - ``after``  = 只取**晚于**该时刻的更新历史。
        ⚠️ OKX 游标语义与直觉相反（已实测）：OKX ``after=ts`` 返回早于 ts 的更早记录，
        ``before=ts`` 返回晚于 ts 的更新记录。故此处把本接口的 ``before`` 映射到 OKX ``after``，
        ``after`` 映射到 OKX ``before``，且每次请求只用**单一方向**游标，避免两端冲突使翻页停滞。
        无 before/after 且 ``limit<=300`` 时走 /candles 单次返回；否则走 history-candles 翻页累积。
        """
        if before is None and after is None and limit <= 300:
            params = {"instId": inst_id, "bar": bar, "limit": str(min(limit, 300))}
            rows = await self._request("/api/v5/market/candles", params)
            candles = [parse_okx_candle(r) for r in rows]
            candles.sort(key=lambda c: c.ts)
            return candles

        # 翻页累积（history-candles，每次 ≤100），统一向**更早**方向回溯。
        collected: dict[int, Candle] = {}
        cursor_older = int(before) if before is not None else None   # OKX after 游标起点
        newer_bound = int(after) if after is not None else None      # OKX before 边界（仅首页生效）
        max_pages = max(1, limit // 100 + 2)                         # 页数上限，防死循环
        for _ in range(max_pages):
            if len(collected) >= limit:
                break
            p: dict[str, str] = {"instId": inst_id, "bar": bar, "limit": "100"}
            if cursor_older is not None:
                p["after"] = str(cursor_older * 1000)
            if newer_bound is not None:
                p["before"] = str(newer_bound * 1000)
            rows = await self._request("/api/v5/market/history-candles", p)
            if not rows:
                break
            page = [parse_okx_candle(r) for r in rows]
            for c in page:
                collected[c.ts] = c
            oldest = min(c.ts for c in page)
            # 游标必须严格向更早推进，否则停止（防死循环）
            if cursor_older is not None and oldest >= cursor_older:
                break
            cursor_older = oldest
            newer_bound = None  # 翻页后持续向更早回溯，不再固定 newer 边界
            if len(page) < 100:
                break
        candles = sorted(collected.values(), key=lambda c: c.ts)
        return candles[-limit:] if len(candles) > limit else candles

    async def get_instruments(self, inst_type: str = "SWAP") -> list[Instrument]:
        """从 tickers 拉取该类型**全量**标的并携带 24h 行情，按成交额降序。

        OKX `tickers` 单次即返回该 instType 全量且自带行情（last/open24h/vol*），
        无需逐标的取价；`changePct=(last-open24h)/open24h`（open24h 为 0/缺失则 None），
        按 `volCcy24h` 降序（缺失视为 0）让主流币天然靠前，取代旧的「字母序+截断」。
        """
        rows = await self._request("/api/v5/market/tickers", {"instType": inst_type})
        out: list[Instrument] = []
        for r in rows:
            inst_id = r.get("instId", "")
            parts = inst_id.split("-")
            if len(parts) < 2:
                continue
            base, quote = parts[0], parts[1]
            last = _optional_float(r.get("last"))
            open24h = _optional_float(r.get("open24h"))
            change_pct = (last - open24h) / open24h if last is not None and open24h else None
            out.append(Instrument(
                instId=inst_id,
                instType=inst_type,
                base=base,
                quote=quote,
                last=last,
                open24h=open24h,
                vol24h=_optional_float(r.get("vol24h")),
                volCcy24h=_optional_float(r.get("volCcy24h")),
                changePct=change_pct,
            ))
        # 按 24h 成交额降序（缺失视为 0）；同额时回落 instId 稳定排序
        out.sort(key=lambda i: (-(i.volCcy24h or 0.0), i.instId))
        return out

    async def get_ticker(self, inst_id: str) -> float | None:
        """单标的最新价。"""
        rows = await self._request("/api/v5/market/ticker", {"instId": inst_id})
        if not rows:
            return None
        return float(rows[0]["last"])


# 进程级单例（FastAPI 启动时创建，关闭时 aclose）
_rest_client: OKXRestClient | None = None


def get_rest_client() -> OKXRestClient:
    global _rest_client
    if _rest_client is None:
        _rest_client = OKXRestClient()
    return _rest_client
