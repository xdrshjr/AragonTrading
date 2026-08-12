"""OKX V5 公共 REST 客户端 (spec §7)。

职责：拉取真实 K 线 / 标的，归一化（升序 + 秒级 UTC ts），节流 + 重试 + 代理透传。
硬约束 (C1)：取数失败必须抛出明确错误，严禁静默回退到假数据。
"""
from __future__ import annotations

import asyncio
import random
import time
from collections.abc import Callable

import httpx

from ..config import Settings, get_settings
from .link_state import get_link_registry
from .rate_limit import get_limiters
from .retry import (
    RetryPolicy,
    classify_exception,
    classify_okx_code,
    classify_status,
    next_delay,
    parse_retry_after,
)
from .types import Candle, Instrument, _optional_float, parse_okx_candle


# 合约元数据缓存 TTL（秒）：ctVal/lotSz/tickSz 等极少变动，1 小时足够新鲜又免重复往返。
_INST_META_TTL_SECONDS = 3600.0
# 资金费率缓存 TTL（秒）：结算以小时计，30s 已足够新鲜，且天然给多标的分组的并发盯市去重。
_FUNDING_TTL_SECONDS = 30.0
# 结算周期缺失时的保守回落（OKX 主流永续为 8h）。
_DEFAULT_FUNDING_INTERVAL_S = 28800.0

# 公共读链路在健康登记簿里的通道键与中文标签（值域属于后端，前端只转发）。
PUBLIC_LINK_KEY = "rest:public"
PUBLIC_LINK_LABEL = "行情接口(REST)"
# 熔断冷却的绝对上限：再长下去「等它自愈」就变成「界面一直黑着」。
_BREAKER_COOLDOWN_MAX_S = 120.0


class OKXError(Exception):
    """OKX 取数失败（连通性 / 业务错误码）。上层据此返回显式错误态。"""

    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(f"[{code}] {message}")


def infer_inst_type(inst_id: str) -> str:
    """由 instId 推断 OKX ``instType``（下单仅涉合约：``-SWAP`` 后缀=SWAP，否则按段数判 FUTURES/SPOT）。"""
    if inst_id.endswith("-SWAP"):
        return "SWAP"
    parts = inst_id.split("-")
    return "FUTURES" if len(parts) >= 3 else "SPOT"


class _RateLimiter:
    """公共读节流（C10）。**类名保留**以免外部引用断裂，内部改由进程级令牌桶承担。

    与旧的「0.12s 最小间隔」相比只多了一件事：允许 burst 容量内的小突发。首屏一次性发出的
    4 个公共请求不再被排成一条队，而持续速率仍被压在同一水平（默认 8/s ≈ 1/0.12s）。
    """

    def __init__(self, settings: Settings | None = None):
        cfg = settings or get_settings()
        self._bucket = get_limiters().bucket(
            "public", cfg.okx_link_public_rate_per_s, cfg.okx_link_public_burst)

    async def acquire(self) -> None:
        await self._bucket.acquire()


class _Breaker:
    """公共读熔断器（§2.3.3）。**只作用于 `rest:public`**；私有读写永不熔断。

    豁免名单（探针类调用）是这个机制能默认打开的前提：``health()`` / ``get_server_time_ms()`` /
    补根取数 / 手动探测端点一律不被它阻挡，且它们任一次成功都会**清零失败计数并把冷却时长复位**
    （不再翻倍累积）。理由见 spec §2.3.3：``/api/health`` 的 ``okx`` 键会经
    ``okxUp → shouldStreamMarket`` **卸载浏览器行情流**——熔断挡住探针，就等于本地开关自己制造
    了一次最长 120 秒的真实断线，与本轮目标正相反。
    """

    def __init__(self, settings: Settings):
        self._threshold = int(settings.okx_link_breaker_failures)
        self._base_cooldown = float(settings.okx_link_breaker_cooldown_s)
        self._cooldown = self._base_cooldown
        self.failures = 0
        self.opened_until = 0.0
        self._half_open = False

    @property
    def enabled(self) -> bool:
        return self._threshold > 0

    def ensure_closed_or_raise(self, path: str) -> None:
        """熔断打开期间**立即**抛错、不发请求；冷却结束放行一个半开探针。"""
        if not self.enabled or self.opened_until <= 0.0:
            return
        remaining = self.opened_until - time.monotonic()
        if remaining > 0:
            raise OKXError("OKX_CIRCUIT_OPEN",
                           f"交易所连接暂时熔断，{int(remaining) + 1} 秒后自动重试 ({path})")
        self._half_open = True

    def note_ok(self) -> None:
        """任一次成功即完全复位（含冷却时长），避免「网络已恢复而冷却还在翻倍」。"""
        self.failures = 0
        self.opened_until = 0.0
        self._cooldown = self._base_cooldown
        self._half_open = False
        get_link_registry().note_circuit(PUBLIC_LINK_KEY, "closed", None)

    def note_failure(self) -> None:
        """半开探针失败即重新打开并翻倍冷却；否则累计到阈值再打开。"""
        if not self.enabled:
            return
        if self._half_open:
            self._cooldown = min(_BREAKER_COOLDOWN_MAX_S, self._cooldown * 2)
            self._open()
            return
        self.failures += 1
        if self.failures >= self._threshold:
            self._open()

    def _open(self) -> None:
        self._half_open = False
        self.opened_until = time.monotonic() + self._cooldown
        get_link_registry().note_circuit(PUBLIC_LINK_KEY, "open", time.time() + self._cooldown)


class OKXRestClient:
    """异步 OKX 公共 REST 客户端。"""

    def __init__(self, settings: Settings | None = None,
                 rand: Callable[[float, float], float] = random.uniform):
        self._settings = settings or get_settings()
        self._limiter = _RateLimiter(self._settings)
        self._breaker = _Breaker(self._settings)
        self._rand = rand              # 退避随机源：测试注入确定性替身
        self._client: httpx.AsyncClient | None = None
        self._last_ok_ts: int | None = None       # 最近一次成功取数的秒级 UTC (F2)
        self._last_latency_ms: int | None = None   # 最近一次 health 探活往返毫秒 (F2)
        self._last_request_ms: float | None = None  # 最近一次成功请求耗时（喂登记簿 EWMA，非 F2 口径）
        # 合约元数据进程内缓存（instId -> (到期 monotonic, meta)）；元数据低频变动，短 TTL (C1-1)。
        self._inst_meta_cache: dict[str, tuple[float, dict]] = {}
        # 资金费率进程内缓存（instId -> (到期 monotonic, 归一读数)）；镜像上一行的既有写法。
        self._funding_cache: dict[str, tuple[float, dict]] = {}

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

    def _policy(self, retries: int | None) -> RetryPolicy:
        """本次调用的重试预算；``retries`` 显式给值时覆盖 ``okx_link_rest_max_attempts``。"""
        cfg = self._settings
        return RetryPolicy(
            max_attempts=max(1, int(retries if retries is not None
                                    else cfg.okx_link_rest_max_attempts)),
            base_delay_s=0.4,
            max_delay_s=8.0,
            budget_s=float(cfg.okx_link_rest_budget_s),
        )

    async def _request(self, path: str, params: dict, *, retries: int | None = None,
                       probe: bool = False) -> list:
        """GET + 令牌桶 + 错误分级 + 去相关抖动退避 + 总预算 + 熔断；返回 OKX ``data`` 数组。

        ``probe=True`` 即**豁免熔断**（探针类调用：health / 服务器时间 / 补根取数 / 手动探测）。
        终态错误（4xx、非暂时态业务码）**首次即抛**，不再空转三次把「你传错了」说成「连不上」。
        """
        policy = self._policy(retries)
        deadline = time.monotonic() + policy.budget_s
        if not probe:
            self._breaker.ensure_closed_or_raise(path)
        delay, rate_limited, last_exc = 0.0, False, None
        for attempt in range(policy.max_attempts):
            await self._limiter.acquire()
            outcome, data, exc, retry_after = await self._attempt(path, params)
            if outcome == "ok":
                self._note_ok()
                return data if data is not None else []
            rate_limited = rate_limited or outcome == "rate_limited"
            last_exc = exc or last_exc
            if outcome == "terminal":
                self._note_fail(path, exc)
                raise exc if isinstance(exc, OKXError) else OKXError(
                    "OKX_UNREACHABLE", f"无法连接 OKX ({path}): {exc}")
            if attempt == policy.max_attempts - 1:
                break     # 最后一次尝试之后不再退避：那是在等一次不会发生的重试，白拖最长 8 秒
            delay = max(next_delay(delay, policy, self._rand), retry_after or 0.0)
            if time.monotonic() + delay >= deadline:
                break
            await asyncio.sleep(delay)
        self._note_fail(path, last_exc)
        if rate_limited:
            raise OKXError("OKX_RATE_LIMITED",
                           f"请求过于频繁：{path} 已退避重试 {policy.max_attempts} 次仍被交易所限频")
        raise OKXError("OKX_UNREACHABLE", f"无法连接 OKX ({path}): {last_exc}")

    async def _attempt(self, path: str, params: dict) -> tuple[str, list | None,
                                                               Exception | None, float | None]:
        """单次尝试：返回 ``(处置, data, 异常, Retry-After 秒)``，不自己决定重不重试。"""
        client = await self._get_client()
        started = time.monotonic()
        try:
            resp = await client.get(path, params=params)
        except Exception as exc:  # noqa: BLE001 —— 分级交给 classify_exception，此处只搬运
            return classify_exception(exc), None, exc, None
        outcome = classify_status(resp.status_code)
        if outcome != "ok":
            retry_after = parse_retry_after(resp.headers.get("Retry-After"))
            return (outcome, None,
                    OKXError("OKX_HTTP_ERROR", f"OKX HTTP {resp.status_code} ({path})"),
                    retry_after)
        body = resp.json()
        code = str(body.get("code"))
        if code != "0":
            biz = classify_okx_code(code)
            exc = OKXError(code, str(body.get("msg") or "OKX error"))
            # clock_skew 对**公共**读没有自愈动作（公共请求不签名），按终态如实上抛。
            return ("terminal" if biz in ("terminal", "clock_skew") else biz), None, exc, None
        self._last_ok_ts = int(time.time())   # 秒级 UTC，最近成功取数 (F2)
        self._last_request_ms = (time.monotonic() - started) * 1000.0
        return "ok", body.get("data", []), None, None

    def _note_ok(self) -> None:
        self._breaker.note_ok()
        get_link_registry().note_ok(PUBLIC_LINK_KEY, kind="restPublic", label=PUBLIC_LINK_LABEL,
                                    latency_ms=self._last_request_ms)

    def _note_fail(self, path: str, exc: Exception | None) -> None:
        self._breaker.note_failure()
        code = exc.code if isinstance(exc, OKXError) else "OKX_UNREACHABLE"
        get_link_registry().note_failure(PUBLIC_LINK_KEY, kind="restPublic",
                                         label=PUBLIC_LINK_LABEL, code=code,
                                         message=f"{path}: {exc}")

    async def health(self) -> bool:
        """轻量探活：能取到一根 K 线即视为 up，并记录一次往返时延 (F2)。

        **熔断豁免**（§2.3.3）：本方法的结果经 ``/api/health`` → 前端 ``okxUp`` →
        ``shouldStreamMarket`` 决定要不要卸载浏览器行情流。让熔断把它按死，等于用一个本地开关
        主动拆掉图表实时流——它本身就是探针，探针必须永远发得出去。
        """
        start = time.monotonic()
        try:
            data = await self._request(
                "/api/v5/market/candles",
                {"instId": self._settings.default_inst, "bar": "1H", "limit": 1},
                retries=1, probe=True,
            )
            if data:
                self._last_latency_ms = int((time.monotonic() - start) * 1000)
                return True
            self._last_latency_ms = None
            return False
        except OKXError:
            self._last_latency_ms = None
            return False

    async def get_server_time_ms(self) -> float:
        """``GET /api/v5/public/time`` → 交易所毫秒时间戳（服务器时钟对齐的唯一数据源）。

        **熔断豁免**：它是 ``50102`` 自愈的唯一数据源，被熔断挡住等于时钟偏差永远修不好。
        """
        rows = await self._request("/api/v5/public/time", {}, retries=1, probe=True)
        if not rows:
            raise OKXError("OKX_UNREACHABLE", "OKX 未返回服务器时间")
        return float(rows[0]["ts"])

    async def get_candles(
        self,
        inst_id: str,
        bar: str,
        limit: int = 300,
        *,
        before: int | None = None,
        after: int | None = None,
        probe: bool = False,
    ) -> list[Candle]:
        """拉取历史 K 线，返回**升序**（旧→新）。

        本接口入参（秒级，C2）：
          - ``before`` = 只取**早于**该时刻的更早历史（左滚回溯，前端唯一用法）；
          - ``after``  = 只取**晚于**该时刻的更新历史。
        ⚠️ OKX 游标语义与直觉相反（已实测）：OKX ``after=ts`` 返回早于 ts 的更早记录，
        ``before=ts`` 返回晚于 ts 的更新记录。故此处把本接口的 ``before`` 映射到 OKX ``after``，
        ``after`` 映射到 OKX ``before``，且每次请求只用**单一方向**游标，避免两端冲突使翻页停滞。
        无 before/after 且 ``limit<=300`` 时走 /candles 单次返回；否则走 history-candles 翻页累积。
        ``probe=True`` 供断线补根使用：补根与公共读故障高度同源，**恰好**发生在熔断最可能打开的
        窗口里，被熔断挡住等于空洞永远补不上（§2.3.3 豁免名单）。
        """
        if before is None and after is None and limit <= 300:
            params = {"instId": inst_id, "bar": bar, "limit": str(min(limit, 300))}
            rows = await self._request("/api/v5/market/candles", params, probe=probe)
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
            rows = await self._request("/api/v5/market/history-candles", p, probe=probe)
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

    async def get_instrument_meta(self, inst_type: str, inst_id: str) -> dict:
        """合约元数据（``GET /api/v5/public/instruments``，公共无签名，C1-1）。

        下单数学全依赖 ``ctVal/lotSz/minSz/tickSz/maxLever``；``ctType/settleCcy`` 供标的范围守门
        （C1-4）。进程内按 instId 短 TTL 缓存（元数据低频变动）。未找到该标的抛 ``OKXError``。
        """
        cached = self._inst_meta_cache.get(inst_id)
        if cached and cached[0] > time.monotonic():
            return cached[1]
        rows = await self._request(
            "/api/v5/public/instruments", {"instType": inst_type, "instId": inst_id})
        row = next((r for r in rows if r.get("instId") == inst_id), rows[0] if rows else None)
        if row is None:
            raise OKXError("INSTRUMENT_NOT_FOUND", f"未找到标的元数据：{inst_id}")
        meta = {
            "ctVal": _optional_float(row.get("ctVal")) or 0.0,
            "ctValCcy": str(row.get("ctValCcy") or ""),
            "lotSz": _optional_float(row.get("lotSz")) or 0.0,
            "minSz": _optional_float(row.get("minSz")) or 0.0,
            "tickSz": _optional_float(row.get("tickSz")) or 0.0,
            "maxLever": _optional_float(row.get("lever")) or 0.0,
            "ctType": str(row.get("ctType") or ""),
            "settleCcy": str(row.get("settleCcy") or ""),
        }
        self._inst_meta_cache[inst_id] = (time.monotonic() + _INST_META_TTL_SECONDS, meta)
        return meta

    async def get_funding_rate(self, inst_id: str) -> dict | None:
        """永续资金费率（``GET /api/v5/public/funding-rate``，公共无签名）。

        供成本模型的 ``fundingCarryBps`` 项（ai-trade-cost-accounting-and-edge-gate §3.5，D-2）。
        进程内按 instId 短 TTL 缓存，完全镜像 ``_inst_meta_cache`` 的既有写法——5s 盯市 × 60s TTL
        下每分钟至多一次公共请求，配额压力可忽略。

        Returns:
            ``{"fundingRate", "fundingTs", "nextFundingTs", "intervalS", "intervalKnown"}``；该标的
            无资金费率（非永续）或返回为空时 ``None``。``intervalS`` 由两个结算时点相减得出，缺失
            回落 8h；``intervalKnown`` 则如实说出**这个数到底是不是交易所给的**
            （ai-trade-horizon-acceptance-loop [P0-1]）。

        为什么必须多一个布尔键：``intervalS`` 有兜底值，因此它**永远表达不出「不知道」**——
        ``interval_s <= 0`` 那种判据在生产里恒不成立，据它写的「取不到就不计提」分支是死码。
        资金费账本（``ai_auto/funding_ledger.py``）只认 ``intervalKnown``；成本模型继续按既有的
        8h 保守估算走，两层对同一个未知给出不同处置是有意的（spec §九 D-9）。
        """
        cached = self._funding_cache.get(inst_id)
        if cached and cached[0] > time.monotonic():
            return cached[1]
        rows = await self._request("/api/v5/public/funding-rate", {"instId": inst_id})
        row = next((r for r in rows if r.get("instId") == inst_id), rows[0] if rows else None)
        if row is None:
            return None
        funding_ts = int(_optional_float(row.get("fundingTime")) or 0.0) // 1000
        next_ts = int(_optional_float(row.get("nextFundingTime")) or 0.0) // 1000
        span = next_ts - funding_ts
        out = {
            "fundingRate": _optional_float(row.get("fundingRate")) or 0.0,
            "fundingTs": funding_ts,
            "nextFundingTs": next_ts,
            "intervalS": max(60.0, float(span)) if span > 0 else _DEFAULT_FUNDING_INTERVAL_S,
            # 加法键：既有 `intervalS` 的兜底值**一字不改**（改它会连带改写 cost_model 的资金费期数
            # 与 prompt 文案，越过「只加观测」的边界并打破 prompt 逐字节冻结）。
            "intervalKnown": span > 0,
        }
        self._funding_cache[inst_id] = (time.monotonic() + _FUNDING_TTL_SECONDS, out)
        return out

    async def get_funding_rate_history(self, inst_id: str, *, before_ms: int | None = None,
                                       after_ms: int | None = None,
                                       limit: int = 100) -> list[dict]:
        """永续**历史**资金费率（``GET /api/v5/public/funding-rate-history``，公共无签名）。

        供 AI 回测按真实费率曲线计提资金费（ai-trade-horizon-acceptance-loop §2.2.4）：回测侧
        ``SimBroker`` 全文没有任何资金费扣减路径，``trend_hold``（1–3 天，跨 3～9 个结算点）下这会
        让回测系统性优于实盘，而那正是 L1 放行门槛所依赖的那份回测。

        与 ``get_funding_rate`` 同姿态（同一个 ``_request``、同样的公共端点、同样不缓存整段曲线——
        曲线由调用方一次性取完后自己持有）。**不做任何平铺假设**：历史数据自带每一次真实结算时刻，
        相邻两点之差即该期的真实周期（OKX 会动态改成 1/2/4/8h）。

        Args:
            inst_id: 永续合约 instId。
            before_ms: 只取该毫秒时间戳**之后**的记录（OKX ``before`` 语义）。
            after_ms: 只取该毫秒时间戳**之前**的记录（OKX ``after`` 语义，用于向更早翻页）。
            limit: 单页条数（OKX 上限 100）。

        Returns:
            ``[{"fundingTs": 秒, "fundingRate": float, "realizedRate": float | None}, …]``，
            **按 ts 升序**（OKX 原样返回是倒序）。取不到即空列表。
        """
        params: dict = {"instId": inst_id, "limit": str(int(limit))}
        if before_ms is not None:
            params["before"] = str(int(before_ms))
        if after_ms is not None:
            params["after"] = str(int(after_ms))
        rows = await self._request("/api/v5/public/funding-rate-history", params)
        out: list[dict] = []
        for row in rows or []:
            ts = int(_optional_float(row.get("fundingTime")) or 0.0) // 1000
            if ts <= 0:
                continue
            realized = _optional_float(row.get("realizedRate"))
            out.append({"fundingTs": ts,
                        "fundingRate": _optional_float(row.get("fundingRate")) or 0.0,
                        "realizedRate": realized})
        out.sort(key=lambda r: r["fundingTs"])
        return out


# 进程级单例（FastAPI 启动时创建，关闭时 aclose）
_rest_client: OKXRestClient | None = None


def get_rest_client() -> OKXRestClient:
    global _rest_client
    if _rest_client is None:
        _rest_client = OKXRestClient()
    return _rest_client
