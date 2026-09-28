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

import httpx

from ..config import Settings, get_settings
from .call_stats import get_call_stats
from .client_pool import POOL_LIMITS, fingerprint, get_client_pool
from .clock import get_server_clock
from .error_http import _redact, describe_http_error
from .link_state import get_link_registry
from .rate_limit import get_limiters
from .rest_client import OKXError
from .retry import classify_okx_code

# 私有链路在健康登记簿里的通道键与中文标签（私有面单列，**不参与** overall，见 link_state）。
PRIVATE_LINK_KEY = "rest:private"
PRIVATE_LINK_LABEL = "账户接口"


class OKXPrivateError(OKXError):
    """OKX 私有接口鉴权 / 业务 / 连通失败。

    子类化既有 ``OKXError``，复用其 ``code/message`` 构造，从而被 ``main.okx_error_handler``
    自动捕获并统一回 502 `{error:{code,message}}`（P1-ErrHandler）。

    ``http_status``（silent-degradation-repair 轴 A-1，**只读加法字段**）：这一次失败对应的
    HTTP 状态码，没有即 ``None``。它不是可选项，而是 A-2 参数阶梯能不能成立的前提——今天
    ``_get`` 把「HTTP 400 参数错」与「429 退避耗尽」**都**抛成
    ``OKXPrivateError("OKX_HTTP_ERROR", …)``，阶梯若以「HTTP 非 2xx」为推进判据，就是在一个
    **不携带该信息的入参**上做判定（本仓成文的缺陷形状）。

    三条形状约束：

    1. **默认 ``None``、位置参数签名一字不动**——既有构造点有数十处（含 ``trading.py`` 的六个
       ``_map_trade_code`` 映射点），它们一个都不改。
    2. 只在**真的知道 HTTP 状态**的分支里填（``_get`` / ``_post`` 的状态分支、429 退避耗尽
       分支）；传输层失败（``OKX_UNREACHABLE``）留 ``None``——那里确实没有状态码，编一个
       进去就是造第二句假话。
    3. **不进任何 wire**：它是给同进程调用方做分支用的，不是给前端看的。
    """

    def __init__(self, code: str, message: str, *, http_status: int | None = None,
                 business_rejected: bool = False):
        super().__init__(code, message)
        self.http_status = http_status
        self.business_rejected = business_rejected


def _iso_timestamp() -> str:
    """ISO8601 毫秒 UTC，带尾 `Z`，形如 ``2026-06-22T08:15:42.123Z``（OKX 签名头要求）。

    **签名与输出格式一字不变**，只是时间源改走 ``ServerClock``：本机时钟漂移（尤其 Windows
    笔记本休眠唤醒后）会让全部私有请求同时撞 ``50102``，而此前 ``OKX_TIME_SKEW`` 之后没有任何
    自愈动作。偏移未同步时 offset=0，输出与旧实现逐字节相同。
    """
    return get_server_clock().now_iso()


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
        self._settings = (settings or get_settings()).model_copy(deep=True)
        self._client: httpx.AsyncClient | None = None
        # 本实例从池里取过几份引用，`aclose()` 须**逐一**归还。F3：按**世代**分桶——换代之后手里
        # 那几份旧世代引用必须还到退役条目上，还到新一代身上就是在减别人的计数（见 `pool.release`）。
        self._pool_holds: dict[int, int] = {}

    def _fingerprint(self) -> str:
        """本实例在连接池里的身份：账户 + 端点 + 模拟盘标志 + 超时档。不同账户绝不复用连接。"""
        return fingerprint(self._api_key, self._settings.okx_rest_base,
                           self._settings.okx_account_demo,
                           self._settings.okx_account_timeout_seconds)

    async def get_leverage_info(self, inst_id: str, mgn_mode: str) -> list:
        """读取当前杠杆；匹配唯一方向由调用方负责，禁止猜测默认值。"""
        return await self._get("/api/v5/account/leverage-info", {
            "instId": inst_id, "mgnMode": mgn_mode})

    async def get_max_size(self, inst_id: str, td_mode: str,
                           px: str | None = None) -> list:
        """读取现有模式及杠杆下的可开张数，不设置杠杆。"""
        params = {"instId": inst_id, "tdMode": td_mode}
        if px is not None:
            params["px"] = str(px)
        return await self._get("/api/v5/account/max-size", params)

    def _new_client(self) -> httpx.AsyncClient:
        timeout = self._settings.okx_account_timeout_seconds
        return httpx.AsyncClient(
            base_url=self._settings.okx_rest_base,
            timeout=httpx.Timeout(timeout, connect=min(8.0, timeout)),
            proxy=self._settings.proxy_url,
            limits=POOL_LIMITS,
            headers={"User-Agent": "aragon-trading/0.1"},
        )

    async def _get_client(self) -> httpx.AsyncClient:
        """从连接池取共享客户端。**绝不把它记进 `self._client`**（P1-1，阻断级）。

        实例级缓存与连接池根本冲突：``invalidate(fp)`` 关掉底层 ``AsyncClient`` 之后，任何仍持有
        旧引用的实例下一次请求会撞 ``RuntimeError: Cannot send a request, as the client has been
        closed.``——而 ``invalidate`` 的触发条件正是「传输层异常」，也就是故障期间必然发生，于是
        「换代自愈」会变成「换代自爆」。同 fp 的复用由池内保证，那是池唯一该管的事。

        ``self._client`` 仍被**读**：既有签名测试直接给实例塞一个 MockTransport 客户端
        （``tests/test_okx_signing.py:66``），那条注入口保留。
        """
        if self._client is not None:
            return self._client
        client, generation = await get_client_pool().acquire_hold(
            self._fingerprint(), self._new_client,
            idle_ttl_s=self._settings.okx_link_pool_idle_ttl_s)
        # 每一跳都取一次 ⇒ 归还也必须是同样的次数、且必须还给同一个世代（见 `aclose`）。
        self._pool_holds[generation] = self._pool_holds.get(generation, 0) + 1
        return client

    async def aclose(self) -> None:
        """用完即还（语义对外不变，只是不再销毁 TCP 连接）。TTL=0 时池内退化为「用完即关」。

        **还多少取决于取过多少**，两处都不能想当然：重试循环里每一跳都会 `acquire` 一次，只还一次
        会让引用计数只增不减（于是 `sweep` 永远清扫不到、`okx_link_pool_idle_ttl_s=0` 那条「用完
        即关」的退路也永远不生效）；反过来，一次都没取过就无脑 `release`（`probe_account` 一类
        「构造了但没发请求」的分支）会去减掉**别的实例**正持有的那份引用——TTL=0 下那等于当场
        关掉别人在用的连接。
        """
        if self._client is not None:      # 外部注入的替身：谁建谁关
            await self._client.aclose()
            self._client = None
            return
        holds, self._pool_holds = self._pool_holds, {}
        pool, fp = get_client_pool(), self._fingerprint()
        for generation, count in holds.items():
            for _ in range(count):
                await pool.release(fp, generation=generation)

    async def _invalidate_pooled(self) -> None:
        """传输层异常即整池换代（笔记本休眠唤醒后所有池化 socket 都是半死的，解药就是这一行）。

        F3：**不再丢弃** `_pool_holds`。换代后池把旧条目移入退役表、等它的引用归零才真正关闭，
        所以这几份旧世代引用必须照常归还（带上世代，池据此减到退役条目上）——丢弃它们会让那条
        连接一直挂到宽限期才被兜底关掉。「不能拿它们去减新一代的计数」这条纪律由 `release` 的
        世代校验直接保证，不再需要在调用方这边靠清零来近似。
        """
        if self._client is None:
            await get_client_pool().invalidate(self._fingerprint())

    async def _acquire_slot(self) -> None:
        """按账户独立的私有令牌桶（多标的分组启动瞬间不至于把私有限频一次打爆）。"""
        cfg = self._settings
        bucket = get_limiters().bucket(f"private:{self._fingerprint()}",
                                       cfg.okx_link_private_rate_per_s, cfg.okx_link_private_burst)
        await bucket.acquire()

    async def _sync_clock_once(self) -> None:
        """``50102/50104`` 自愈：拉一次交易所时间对齐偏移；失败静默（时钟修不好不该变成另一种错）。"""
        from .rest_client import get_rest_client

        try:
            await get_server_clock().sync(get_rest_client().get_server_time_ms)
        except Exception:  # noqa: BLE001 —— 自愈是尽力而为，失败仍按原错误上抛
            return

    async def _maybe_heal_clock(self, okx_code: str) -> None:
        """``50102/50104``（签名时间戳过期）即拉一次交易所时间对齐偏移。

        本机时钟漂移（Windows 笔记本休眠唤醒后尤甚）会让**全部**私有请求在同一瞬间一起失败，
        而此前 ``OKX_TIME_SKEW`` 之后没有任何自愈动作——用户只能自己去改系统时间。对齐之后
        **下一次**调用即恢复；本次仍按原错误上抛（不在这里偷偷重试，写路径的重试语义归
        ``write_safety`` 管，读路径的调用方本就会再来一次）。
        """
        if classify_okx_code(okx_code) == "clock_skew":
            await self._sync_clock_once()

    def _note_link_ok(self) -> None:
        get_link_registry().note_ok(PRIVATE_LINK_KEY, kind="restPrivate",
                                    label=PRIVATE_LINK_LABEL)

    def _note_link_fail(self, code: str, message: str) -> None:
        """私有面失败只进 ``accountState``，**不**参与 ``overall``（没配实盘的用户不该看到顶栏变红）。"""
        get_link_registry().note_failure(PRIVATE_LINK_KEY, kind="restPrivate",
                                         label=PRIVATE_LINK_LABEL, code=code, message=message)

    # ---------- 轴 A-1 / A-3：把「交易所到底说了什么」带出来，并按状态分级登记 ----------
    def _http_detail(self, status: int, body: bytes | str | None) -> str:
        """非 2xx 的对外描述。总闸关 ⇒ 逐字回到 ``f"OKX HTTP {status}"``。

        产物**一律再过一次 ``_redact``**：OKX 的错误体本不含密钥，但「凡是要进日志的外部串都过
        脱敏」是本仓既有纪律，不在这里破例。
        """
        if not self._settings.okx_http_error_detail_enabled:
            return f"OKX HTTP {status}"
        return _redact(describe_http_error(status, body), self._secret_key, self._passphrase)

    def _note_http_status(self, path: str, status: int, detail: str) -> None:
        """按 HTTP 状态把这一次非 2xx 记进链路健康登记簿（轴 A-3 的分级表）。

        | 状态 | 记法 |
        |---|---|
        | 5xx / 传输层 | ``note_failure``（今天的行为，见调用方） |
        | 429 | ``note_failure``（今天的行为，已被 ``tests/test_okx_signing.py`` 冻结） |
        | 4xx（非 429/401/403） | ``note_rejected``——请求到了、被业务拒了，链路是好的 |
        | 401 / 403 | ``note_failure``，但 ``code`` 换成 ``OKX_AUTH_REJECTED`` |

        ``okx_link_treat_4xx_as_link_failure=True`` ⇒ **逐字节回到今天**（一律 ``note_failure``
        且 ``code`` 恒为 ``OKX_HTTP_ERROR``、message 恒为 ``f"{path}: HTTP {status}"``）。
        """
        if self._settings.okx_link_treat_4xx_as_link_failure:
            self._note_link_fail("OKX_HTTP_ERROR", f"{path}: HTTP {status}")
            return
        if status in (401, 403):
            self._note_link_fail("OKX_AUTH_REJECTED", f"{path}: {detail}")
            return
        if 400 <= status < 500:
            get_link_registry().note_rejected(PRIVATE_LINK_KEY, kind="restPrivate",
                                              label=PRIVATE_LINK_LABEL, status=status)
            return
        self._note_link_fail("OKX_HTTP_ERROR", f"{path}: {detail}")

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

        ``requestPath`` 须含 query string 并与签名串一致。对**传输层失败**重试，并对 HTTP ``429``
        （限流）做指数退避重试（镜像 ``rest_client._request``）——签名私有路径同样需自愈，否则多标的
        自动交易启动瞬间撞限频会把 ``OKX HTTP 429`` 原样抛到 UI。非 429 的 4xx/5xx 仍立即上抛。

        F2（ai-trade-interval-wake-dead-monitor-loop §8.2）：**传输失败的谓词是「结构」而不是
        「类型」**。此前 ``try`` 罩着整个成功路径、``except`` 写 ``httpx.TransportError``；而生产
        日志证明真实发生过的失败类里有 ``anyio.ClosedResourceError``（代理 CONNECT 隧道在 TLS 握手
        中途被拆，httpcore 的 ``start_tls`` 恰好漏了这一条映射），它根本不是 httpx 异常 ⇒ 它会
        **原样穿透**本方法，把 docstring 承诺的「一律抛 ``OKXPrivateError``」变成一句空话，下游
        ``executor.reconcile`` 的 ``except OKXError`` 降级链路整条空转。

        故 ``try`` 收窄到**只包住那一次网络调用**，再把谓词放宽到「进了这个 except 分支」：这样
        ``except`` 的唯一来源就是 ``client.get``，它在语义上**就等于**传输层失败。**严禁**只把关键字
        换成 ``except Exception`` 而不收窄 ``try``——那会把 ``code != "0"`` 的鉴权 / 业务错误
        （``OKXPrivateError`` 也是 ``Exception``）当成传输失败重试三次并坍缩成 ``OKX_UNREACHABLE``，
        直接推翻本 docstring 的承诺，也让 ``_maybe_heal_clock`` 的时钟自愈与服务层的语义码全部失效。
        """
        query = httpx.QueryParams(params or {})
        request_path = path + (f"?{query}" if query else "")
        last_exc: Exception | None = None
        rate_limited = False
        for attempt in range(3):
            await self._acquire_slot()
            client = await self._get_client()   # 每跳都问池要（换代后立刻拿到新连接，P1-1）
            ts = _iso_timestamp()
            headers = self._headers(ts, "GET", request_path)
            try:
                resp = await client.get(path, params=params, headers=headers)
            except Exception as exc:  # noqa: BLE001 —— 谓词见 docstring：进了这里即传输层失败
                get_call_stats().note("GET", path, transport_fail=True)   # 轴 D-3：签名 GET 出口
                last_exc = exc
                rate_limited = False
                await self._invalidate_pooled()   # 抖动即整池换代，而非让后续请求各自超时一遍
                continue
            get_call_stats().note("GET", path, status=resp.status_code)
            if resp.status_code == 429:   # 限流：退避后重试，不直接抛
                rate_limited = True
                await asyncio.sleep(0.5 * (2 ** attempt))
                continue
            try:
                resp.raise_for_status()
            except httpx.HTTPStatusError as exc:  # 非 429 的 4xx/5xx：语义不变，立即上抛
                status = exc.response.status_code
                detail = self._http_detail(status, exc.response.content)
                self._note_http_status(path, status, detail)
                raise OKXPrivateError("OKX_HTTP_ERROR", detail, http_status=status) from exc
            body = resp.json()
            if str(body.get("code")) != "0":
                await self._maybe_heal_clock(str(body.get("code")))
                raise OKXPrivateError(str(body.get("code")),
                                      str(body.get("msg") or "OKX error"))
            self._note_link_ok()
            return body.get("data", [])
        if rate_limited:   # 退避耗尽仍 429：显式回 OKX_HTTP_ERROR，避免 last_exc=None 的误导串
            # 【有意的不对齐，spec §2.3.5 / §十一 D-1】公共侧改抛 OKX_RATE_LIMITED，私有侧**一字不动**：
            # 它被 tests/test_okx_signing.py:95 逐字冻结，而那条用例本轮必须未经改动仍绿。
            self._note_link_fail("OKX_HTTP_ERROR", f"{path}: HTTP 429")
            # `http_status=429`：这条分支**真的知道**自己是 429，故如实填（A-2 的 `should_advance`
            # 据它判「不推进」）。message 与登记逐字不动——它被 tests/test_okx_signing.py:95 冻结。
            raise OKXPrivateError("OKX_HTTP_ERROR", "OKX HTTP 429", http_status=429)
        self._note_link_fail("OKX_UNREACHABLE", f"{path}: {last_exc}")
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
