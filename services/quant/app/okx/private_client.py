"""OKX V5 私有签名 REST 客户端（设置中心 · 实盘账户）。

职责：以**单条凭证**（api_key / secret_key / passphrase）按 OKX V5 鉴权协议发只读 GET
（account/config | balance | positions），校验凭证整体可用并聚合账户详情。

硬约束：
- 只读（R-NoOrder）：本客户端**不暴露任何下单 / 撤单 / 划转写方法**，守 Phase 1「real trading
  disabled」红线。
- 显式错误（C1）：业务错误码（`code != "0"`）或连通失败一律抛 ``OKXPrivateError``，**绝不**静默
  回退假数据。``OKXPrivateError`` 子类化既有 ``OKXError``，复用 ``main.okx_error_handler`` 统一回
  502 `{error:{code,message}}`，无新增 handler、无裸 500 漏洞（P1-ErrHandler）。
- 密钥不外泄（C7）：secret / passphrase 仅用于签名头，绝不进日志。
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
from datetime import datetime, timezone

import httpx

from ..config import Settings, get_settings
from .rest_client import OKXError


class OKXPrivateError(OKXError):
    """OKX 私有接口鉴权 / 业务 / 连通失败。

    子类化既有 ``OKXError``，复用其 ``code/message`` 构造，从而被 ``main.okx_error_handler``
    自动捕获并统一回 502 `{error:{code,message}}`（P1-ErrHandler）。
    """


def _iso_timestamp() -> str:
    """ISO8601 毫秒 UTC，带尾 `Z`，形如 ``2026-06-22T08:15:42.123Z``（OKX 签名头要求）。"""
    now = datetime.now(timezone.utc)
    return now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{now.microsecond // 1000:03d}Z"


def _sign(secret: str, ts: str, method: str, request_path: str, body: str = "") -> str:
    """``base64(HMAC_SHA256(secret, ts+METHOD+requestPath+body))``。

    ``ts`` 须与 ``OK-ACCESS-TIMESTAMP`` 头**完全一致**；``request_path`` **含 query string**；
    GET 的 ``body`` 为空串。
    """
    prehash = f"{ts}{method.upper()}{request_path}{body}"
    mac = hmac.new(secret.encode("utf-8"), prehash.encode("utf-8"), hashlib.sha256)
    return base64.b64encode(mac.digest()).decode("utf-8")


class OKXPrivateClient:
    """以单条凭证构造的 OKX V5 私有 REST 客户端（只读 GET）。

    复用 ``settings.okx_rest_base`` 与 ``settings.proxy_url``（OKX 地区受限，须透传代理，延续既有
    约定）。实例**不缓存到进程单例**（不同账户密钥不同），由调用方按需构造并 ``aclose``。
    """

    def __init__(self, api_key: str, secret_key: str, passphrase: str,
                 settings: Settings | None = None):
        self._api_key = api_key
        self._secret_key = secret_key
        self._passphrase = passphrase
        self._settings = settings or get_settings()
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            timeout = self._settings.okx_account_timeout_seconds
            self._client = httpx.AsyncClient(
                base_url=self._settings.okx_rest_base,
                timeout=httpx.Timeout(timeout, connect=min(8.0, timeout)),
                proxy=self._settings.proxy_url,
                headers={"User-Agent": "aragon-trading/0.1"},
            )
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def __aenter__(self) -> "OKXPrivateClient":
        return self

    async def __aexit__(self, *exc) -> None:
        await self.aclose()

    def _headers(self, ts: str, method: str, request_path: str, body: str = "") -> dict[str, str]:
        """组装 5 个 ``OK-ACCESS-*`` + ``Content-Type``。实盘默认不发 ``x-simulated-trading``。"""
        headers = {
            "OK-ACCESS-KEY": self._api_key,
            "OK-ACCESS-SIGN": _sign(self._secret_key, ts, method, request_path, body),
            "OK-ACCESS-TIMESTAMP": ts,
            "OK-ACCESS-PASSPHRASE": self._passphrase,
            "Content-Type": "application/json",
        }
        if self._settings.okx_account_demo:
            headers["x-simulated-trading"] = "1"
        return headers

    async def _get(self, path: str, params: dict | None = None) -> list:
        """签名 GET：返回 OKX ``data`` 数组；``code != "0"`` 或连通失败抛 ``OKXPrivateError``。

        ``requestPath`` 须含 query string 并与签名串一致。对 ``httpx.TransportError`` 重试，并对
        HTTP ``429``（限流）做指数退避重试（镜像 ``rest_client._request``）——签名私有路径同样需自愈，
        否则多标的自动交易启动瞬间撞限频会把 ``OKX HTTP 429`` 原样抛到 UI。非 429 的 4xx/5xx 仍立即上抛。
        """
        client = await self._get_client()
        query = httpx.QueryParams(params or {})
        request_path = path + (f"?{query}" if query else "")
        last_exc: Exception | None = None
        rate_limited = False
        for attempt in range(3):
            ts = _iso_timestamp()
            headers = self._headers(ts, "GET", request_path)
            try:
                resp = await client.get(path, params=params, headers=headers)
                if resp.status_code == 429:   # 限流：退避后重试，不直接抛
                    rate_limited = True
                    await asyncio.sleep(0.5 * (2 ** attempt))
                    continue
                resp.raise_for_status()
                body = resp.json()
                if str(body.get("code")) != "0":
                    raise OKXPrivateError(str(body.get("code")),
                                          str(body.get("msg") or "OKX error"))
                return body.get("data", [])
            except httpx.TransportError as exc:  # 仅传输层异常重试，鉴权/业务错误立即上抛
                last_exc = exc
                rate_limited = False
            except httpx.HTTPStatusError as exc:  # 非 429 的 4xx/5xx：语义不变，立即上抛
                raise OKXPrivateError("OKX_HTTP_ERROR",
                                      f"OKX HTTP {exc.response.status_code}") from exc
        if rate_limited:   # 退避耗尽仍 429：显式回 OKX_HTTP_ERROR，避免 last_exc=None 的误导串
            raise OKXPrivateError("OKX_HTTP_ERROR", "OKX HTTP 429")
        raise OKXPrivateError("OKX_UNREACHABLE", f"无法连接 OKX ({path}): {last_exc}")

    async def get_account_config(self) -> dict:
        """``GET /api/v5/account/config`` → 单行账户配置（UID / 层级 / 权限）。"""
        data = await self._get("/api/v5/account/config")
        return data[0] if data else {}

    async def get_balance(self) -> dict:
        """``GET /api/v5/account/balance`` → 单行余额（总权益 + 分币种 details）。"""
        data = await self._get("/api/v5/account/balance")
        return data[0] if data else {}

    async def get_positions(self) -> list:
        """``GET /api/v5/account/positions`` → 逐持仓数组（无持仓返回空数组）。"""
        return await self._get("/api/v5/account/positions")
