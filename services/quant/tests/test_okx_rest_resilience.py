"""公共 REST 韧性单测（okx-link-resilience §2.3）——根因 C 的三处现场 + 熔断豁免。

三处既有缺陷的复现：
1. 一个 400（参数不合法）白白空转三次、等 ~2.8s，最后报 `OKX_UNREACHABLE`——把「你传错了」
   说成「连不上交易所」；
2. 429 耗尽后 `last_exc` 是 `None`，用户看到一句带 `None` 的串，且丢失了「其实是被限频」；
3. 退避是纯确定性指数，多标的分组同时失败时在同一毫秒一起重试。

以及本轮最容易自伤的一处：**熔断必须放过探针**。`/api/health` 的 `okx` 键经 `okxUp` →
`shouldStreamMarket` 会**卸载浏览器行情流**——熔断挡住 `health()` 就等于用一个本地开关主动拆掉
图表实时流，再叠加冷却翻倍，网络早已恢复而界面还黑着最长 120 秒。
"""
from __future__ import annotations

import asyncio
import contextlib
import time

import anyio
import httpx
import pytest

from app.config import Settings
from app.okx.link_state import get_link_registry
from app.okx.rest_client import _Breaker, OKXError, OKXRestClient


class _Resp:
    def __init__(self, status_code: int, body: dict | None = None, headers: dict | None = None):
        self.status_code = status_code
        self._body = body or {}
        self.headers = headers or {}

    def json(self) -> dict:
        return self._body


class _FakeHttp:
    """替身 `httpx.AsyncClient`：按序列返回响应或抛异常，并记录每次请求。"""

    def __init__(self, script: list) -> None:
        self._script = list(script)
        self.calls: list[tuple[str, dict]] = []
        self.closed = 0            # 「失效即换代」把它关了几次

    async def get(self, path, params=None, headers=None):
        self.calls.append((path, params or {}))
        item = self._script[min(len(self.calls) - 1, len(self._script) - 1)]
        if isinstance(item, Exception):
            raise item
        return item

    async def aclose(self) -> None:
        self.closed += 1


_OK_BODY = {"code": "0", "data": [{"ts": "1", "o": "1", "h": "1", "l": "1", "c": "1", "v": "1"}]}
# K 线端点回的是**数组**行（`parse_okx_candle` 按位取），与上面的 dict 行不是同一形状。
_OK_CANDLES = {"code": "0", "data": [["1700000000000", "1", "1", "1", "1", "1", "1", "1", "1"]]}


def _client(script: list, **over) -> tuple[OKXRestClient, _FakeHttp]:
    base = dict(okx_link_rest_max_attempts=3, okx_link_rest_budget_s=25.0,
                okx_link_public_rate_per_s=0.0, okx_link_public_burst=0.0,
                okx_link_breaker_failures=0, okx_link_breaker_cooldown_s=20.0)
    base.update(over)
    client = OKXRestClient(settings=Settings(**base), rand=lambda lo, hi: 0.0)
    fake = _FakeHttp(script)
    # 注入必须挂在**建构缝**上：`_attempt` 传输失败即把 `_client` 置空换代，只塞 `_client`
    # 的话下一跳会去构造**真实** `httpx.AsyncClient` 打真网络（单测打外网 + 断言必红）。
    client._new_client = lambda: fake
    client._client = fake        # 注入：绕过真实 httpx
    return client, fake


def _breaker(**over) -> _Breaker:
    base = dict(okx_link_breaker_failures=2, okx_link_breaker_cooldown_s=20.0,
                okx_link_rest_budget_s=25.0)
    base.update(over)
    return _Breaker(Settings(**base))


# ---------------- 错误分级 ----------------


async def test_a_400_is_raised_on_the_first_attempt():
    """终态错误首次即抛，且**只发一次**请求（今天它会空转三次再报错误的原因）。"""
    client, fake = _client([_Resp(400)])

    with pytest.raises(OKXError) as exc:
        await client._request("/api/v5/market/candles", {"instId": "X"})

    assert exc.value.code == "OKX_HTTP_ERROR"
    assert "400" in exc.value.message
    assert len(fake.calls) == 1


async def test_a_500_is_retried_then_succeeds():
    client, fake = _client([_Resp(500), _Resp(200, _OK_BODY)])
    data = await client._request("/api/v5/market/candles", {"instId": "X"})
    assert data and len(fake.calls) == 2


async def test_exhausted_429_raises_rate_limited_without_a_none_in_the_message():
    """429 耗尽 ⇒ `OKX_RATE_LIMITED`，message 里**不许**出现 `None`。"""
    client, fake = _client([_Resp(429), _Resp(429), _Resp(429)])

    with pytest.raises(OKXError) as exc:
        await client._request("/api/v5/market/candles", {"instId": "X"})

    assert exc.value.code == "OKX_RATE_LIMITED"
    assert "None" not in exc.value.message
    assert "限频" in exc.value.message
    assert len(fake.calls) == 3


async def test_retry_after_header_is_honoured_when_larger_than_the_backoff(monkeypatch):
    """`Retry-After` 存在即取它与算法退避的**较大者**（尊重交易所指令）。"""
    slept: list[float] = []

    async def fake_sleep(delay: float) -> None:
        slept.append(delay)

    monkeypatch.setattr("app.okx.rest_client.asyncio.sleep", fake_sleep)
    client, _ = _client([_Resp(429, headers={"Retry-After": "3"}), _Resp(200, _OK_BODY)])

    await client._request("/api/v5/market/candles", {"instId": "X"})

    assert slept == [3.0]


async def test_the_wall_clock_budget_stops_retrying_early(monkeypatch):
    """单次调用总预算（含等待）：AI 会话 5s 盯市循环不该被一串重试阻塞一分钟。"""
    async def fake_sleep(_delay: float) -> None:
        return None

    monkeypatch.setattr("app.okx.rest_client.asyncio.sleep", fake_sleep)
    client, fake = _client([_Resp(500)] * 5, okx_link_rest_max_attempts=5,
                           okx_link_rest_budget_s=0.0)

    with pytest.raises(OKXError):
        await client._request("/api/v5/market/candles", {"instId": "X"})

    assert len(fake.calls) == 1, "预算不足以支撑下一次退避时必须立即停止"


async def test_the_last_attempt_is_not_followed_by_a_pointless_backoff(monkeypatch):
    """最后一次尝试之后再退避，等的是一次**不会发生**的重试——纯拖延（默认最长 8 秒）。

    这条延的不是别处，正是断线期间用户等待错误浮现的那几秒；而 AI 会话的 5s 盯市循环也在同一条
    路径上排队。
    """
    slept: list[float] = []

    async def fake_sleep(delay: float) -> None:
        slept.append(delay)

    monkeypatch.setattr("app.okx.rest_client.asyncio.sleep", fake_sleep)
    client, fake = _client([_Resp(500)] * 3, okx_link_rest_max_attempts=3)

    with pytest.raises(OKXError):
        await client._request("/api/v5/market/candles", {"instId": "X"})

    assert len(fake.calls) == 3
    assert len(slept) == 2, "3 次尝试之间只该有 2 次退避"


async def test_a_transport_error_is_retryable_and_a_parse_error_is_not():
    client, fake = _client([httpx.ConnectError("boom"), _Resp(200, _OK_BODY)])
    assert await client._request("/api/v5/market/candles", {"instId": "X"})
    assert len(fake.calls) == 2

    client2, fake2 = _client([RuntimeError("not a transport error")])
    with pytest.raises(OKXError) as exc:
        await client2._request("/api/v5/market/candles", {"instId": "X"})
    assert exc.value.code == "OKX_UNREACHABLE"
    assert len(fake2.calls) == 1


async def test_a_terminal_business_code_is_not_retried():
    client, fake = _client([_Resp(200, {"code": "51000", "msg": "参数错误"})])
    with pytest.raises(OKXError) as exc:
        await client._request("/api/v5/market/candles", {"instId": "X"})
    assert exc.value.code == "51000"
    assert len(fake.calls) == 1


# ---------------- 熔断 ----------------


async def test_the_breaker_opens_and_then_refuses_to_send(monkeypatch):
    async def fake_sleep(_delay: float) -> None:
        return None

    monkeypatch.setattr("app.okx.rest_client.asyncio.sleep", fake_sleep)
    client, fake = _client([_Resp(500)] * 20, okx_link_rest_max_attempts=1,
                           okx_link_breaker_failures=2)

    for _ in range(2):
        with pytest.raises(OKXError):
            await client._request("/api/v5/market/candles", {"instId": "X"})
    sent_before = len(fake.calls)

    with pytest.raises(OKXError) as exc:
        await client._request("/api/v5/market/candles", {"instId": "X"})

    assert exc.value.code == "OKX_CIRCUIT_OPEN"
    assert len(fake.calls) == sent_before, "熔断打开期间**一个请求都不许发**"
    assert get_link_registry().snapshot()["channels"][0]["circuit"] == "open"


async def test_probe_calls_are_never_blocked_by_an_open_breaker(monkeypatch):
    """[评审 P0-5] 熔断的作用是省掉注定失败的批量取数，**不是**拦住用来判断「是否已恢复」的探针。"""
    async def fake_sleep(_delay: float) -> None:
        return None

    monkeypatch.setattr("app.okx.rest_client.asyncio.sleep", fake_sleep)
    client, fake = _client([_Resp(500), _Resp(500), _Resp(200, _OK_BODY), _Resp(200, _OK_BODY),
                            _Resp(200, _OK_CANDLES)],
                           okx_link_rest_max_attempts=1, okx_link_breaker_failures=2)
    for _ in range(2):
        with pytest.raises(OKXError):
            await client._request("/api/v5/market/candles", {"instId": "X"})
    sent_before = len(fake.calls)

    # 探针三兄弟：health / 服务器时间 / 补根取数
    assert await client.health() is True
    assert await client.get_server_time_ms() >= 0
    await client.get_candles("BTC-USDT-SWAP", "1H", limit=1, probe=True)

    assert len(fake.calls) > sent_before, "探针必须仍然发得出去"


async def test_a_successful_probe_resets_the_breaker_and_its_cooldown(monkeypatch):
    """任一次成功即**清零失败计数并把冷却时长复位**（不再翻倍累积）。"""
    async def fake_sleep(_delay: float) -> None:
        return None

    monkeypatch.setattr("app.okx.rest_client.asyncio.sleep", fake_sleep)
    client, _ = _client([_Resp(500)] * 2 + [_Resp(200, _OK_BODY)] * 10,
                        okx_link_rest_max_attempts=1, okx_link_breaker_failures=2,
                        okx_link_breaker_cooldown_s=20.0)
    for _ in range(2):
        with pytest.raises(OKXError):
            await client._request("/api/v5/market/candles", {"instId": "X"})
    assert client._breaker.opened_until > 0

    await client.health()

    assert client._breaker.failures == 0
    assert client._breaker.opened_until == 0.0
    assert client._breaker._cooldown == 20.0, "冷却时长必须复位，否则网络恢复了界面还黑着"
    # 熔断已关 ⇒ 普通取数立刻可用（不必等冷却）
    assert await client._request("/api/v5/market/candles", {"instId": "X"})


async def test_breaker_failures_zero_disables_it_entirely(monkeypatch):
    async def fake_sleep(_delay: float) -> None:
        return None

    monkeypatch.setattr("app.okx.rest_client.asyncio.sleep", fake_sleep)
    client, fake = _client([_Resp(500)] * 40, okx_link_rest_max_attempts=1,
                           okx_link_breaker_failures=0)
    for _ in range(10):
        with pytest.raises(OKXError) as exc:
            await client._request("/api/v5/market/candles", {"instId": "X"})
        assert exc.value.code != "OKX_CIRCUIT_OPEN"
    assert len(fake.calls) == 10


# ---------------- 既有观测口径不变 ----------------


async def test_last_ok_ts_and_latency_semantics_are_unchanged():
    """`last_ok_ts` / `last_latency_ms` 被 `/api/health` 读取，语义与写入时机一字不变。"""
    client, _ = _client([_Resp(200, _OK_BODY)] * 3)
    assert client.last_ok_ts is None

    assert await client.health() is True

    assert client.last_ok_ts is not None
    assert isinstance(client.last_latency_ms, int)
    row = get_link_registry().snapshot()["channels"][0]
    assert row["key"] == "rest:public" and row["state"] == "up"


async def test_a_failure_is_recorded_in_the_registry():
    client, _ = _client([_Resp(400)])
    with pytest.raises(OKXError):
        await client._request("/api/v5/market/candles", {"instId": "X"})
    row = get_link_registry().snapshot()["channels"][0]
    assert row["lastErrorCode"] == "OKX_HTTP_ERROR"
    assert row["consecutiveFailures"] == 1


# ---------------- 失效即换代（okx-public-rest-circuit-open-never-recovers F1′） ----------------


async def test_a_transport_failure_swaps_the_public_client():
    """主因的正面修复：公共 `AsyncClient` 是进程级永不重建的单例，坏了只能靠重启进程。

    私有侧早有这剂解药（`client_pool.invalidate`：失效即换代），公共侧被漏掉了。
    """
    client, fake = _client([httpx.ConnectError("boom")], okx_link_rest_max_attempts=1)

    with pytest.raises(OKXError):
        await client._request("/api/v5/market/candles", {"instId": "X"})

    assert client._client is None, "下一跳必须建一条全新的连接池"
    assert fake.closed == 1, "旧客户端必须被关掉，否则连接与槽位一起泄漏"


async def test_a_non_httpx_transport_failure_also_swaps_the_client():
    """谓词必须是「进了 `client.get` 的 except 分支」，**不能**写成 `httpx.TransportError`。

    生产日志里真实发生过 5 次 `anyio.ClosedResourceError`（代理 CONNECT 隧道在 TLS 握手中途被
    拆掉），它根本不是 httpx 异常——用 `httpx.TransportError` 当谓词的写法，恰好会在这个**已知
    会发生**的类上静默失效（私有侧现在就正栽在这上面）。
    """
    client, fake = _client([anyio.ClosedResourceError()], okx_link_rest_max_attempts=1)

    with pytest.raises(OKXError):
        await client._request("/api/v5/market/candles", {"instId": "X"})

    assert client._client is None
    assert fake.closed == 1


async def test_business_failures_never_swap_the_client():
    """换代只许留在 `_attempt` 里：`_note_fail` 是**所有**失败的统一收口（含 4xx 与业务码），
    把换代放那儿等于每一次 400 / 51000 都拆一次连接池。"""
    client, fake = _client([_Resp(400)])
    with pytest.raises(OKXError):
        await client._request("/api/v5/market/candles", {"instId": "X"})
    assert client._client is fake and fake.closed == 0

    client2, fake2 = _client([_Resp(200, {"code": "51000", "msg": "参数错误"})])
    with pytest.raises(OKXError):
        await client2._request("/api/v5/market/candles", {"instId": "X"})
    assert client2._client is fake2 and fake2.closed == 0


async def test_a_cancelled_public_read_neither_swaps_the_client_nor_feeds_the_breaker():
    """`CancelledError` 是 `BaseException`，天然不进 `except Exception`——**必须保持这样**。

    `market_window._fetch_tactical` 每 5 秒用 `asyncio.wait_for(…, 8s)` 放弃一次公共取数；把取消
    也当成换代信号等于每 5 秒重建一次连接池，而取消并不泄漏连接槽（repro 已证 `pool` 恒为 0）。
    """
    class _Hanging(_FakeHttp):
        async def get(self, path, params=None, headers=None):
            self.calls.append((path, params or {}))
            await asyncio.Event().wait()

    client, _ = _client([_Resp(200, _OK_BODY)], okx_link_breaker_failures=5)
    hanging = _Hanging([_Resp(200, _OK_BODY)])
    client._new_client = lambda: hanging
    client._client = hanging

    with pytest.raises((TimeoutError, asyncio.TimeoutError)):
        await asyncio.wait_for(
            client._request("/api/v5/market/candles", {"instId": "X"}), 0.05)

    assert client._client is hanging and hanging.closed == 0
    assert client._breaker.failures == 0, "取消走不到 `_note_fail`，熔断计数一次都不该动"


async def test_concurrent_transport_failures_swap_the_client_exactly_once():
    """故障期必然是多路并发同时失败：旧客户端只许被关一次。"""
    gate = asyncio.Event()

    class _Stalling(_FakeHttp):
        async def get(self, path, params=None, headers=None):
            self.calls.append((path, params or {}))
            await gate.wait()
            raise httpx.ConnectError("boom")

    client, _ = _client([_Resp(500)], okx_link_rest_max_attempts=1)
    stale = _Stalling([_Resp(500)])
    built: list[_FakeHttp] = []

    def factory() -> _FakeHttp:
        built.append(_FakeHttp([_Resp(500)]))
        return built[-1]

    client._new_client = factory
    client._client = stale

    tasks = [asyncio.create_task(client._request("/api/v5/market/candles", {"instId": "X"}))
             for _ in range(3)]
    while len(stale.calls) < 3:          # 三个都停在 gate 上 ⇒ 都持有同一个旧客户端
        await asyncio.sleep(0)
    gate.set()
    results = await asyncio.gather(*tasks, return_exceptions=True)

    assert all(isinstance(r, OKXError) for r in results)
    assert stale.closed == 1, "身份比对：三个并发失败只许换一次代"
    assert client._client is None and built == []


async def test_a_late_failure_does_not_close_the_client_someone_else_just_built():
    """没有身份比对就会出现「A 换代 → B 把 A 刚建的新 client 关掉 → C 再换一次」的连环拆台。"""
    client, stale = _client([_Resp(500)])
    fresh = _FakeHttp([_Resp(500)])
    client._new_client = lambda: fresh

    await client._invalidate_client(stale)          # 第一个失败者：换代
    assert client._client is None and stale.closed == 1
    assert await client._get_client() is fresh      # 下一跳建全新的

    await client._invalidate_client(stale)          # 后到的那个仍持有旧引用

    assert client._client is fresh, "不许把别人刚建好的新客户端拆掉"
    assert fresh.closed == 0 and stale.closed == 1


async def test_the_unreachable_message_names_the_exception_class():
    """httpx 全部超时类的 `str()` 都是空串：只格式化 `{exc}` 的话，1748 行日志冒号后什么都没有。"""
    client, _ = _client([httpx.ReadTimeout("")], okx_link_rest_max_attempts=1)

    with pytest.raises(OKXError) as exc:
        await client._request("/api/v5/public/time", {})

    assert "ReadTimeout" in exc.value.message
    assert get_link_registry().snapshot()["channels"][0]["lastErrorMessage"].count("ReadTimeout")


# ---------------- 熔断去棘轮（F2） ----------------


async def test_opening_the_breaker_clears_the_failure_count(monkeypatch):
    """`failures` 不清零的话，此后**每一次**失败都满足 `failures >= threshold`，
    于是每一次失败都把冷却窗口重新推满——窗口再也不会真正走完。"""
    async def fake_sleep(_delay: float) -> None:
        return None

    monkeypatch.setattr("app.okx.rest_client.asyncio.sleep", fake_sleep)
    client, _ = _client([_Resp(500)] * 20, okx_link_rest_max_attempts=1,
                        okx_link_breaker_failures=2)

    for _ in range(2):
        with pytest.raises(OKXError):
            await client._request("/api/v5/market/candles", {"instId": "X"})

    assert client._breaker.opened_until > 0
    assert client._breaker.failures == 0


async def test_probe_failures_no_longer_ratchet_the_cooldown(monkeypatch):
    """探针被豁免熔断，正是为了让它把熔断治好——它的失败不许反过来给熔断上膛。

    生产里这就是把中断从「几十秒」拖成「17 小时」的放大器：`/api/health`（10~15s 一次）与
    链路清扫协程（80s 一次）的失败把冷却顶到 120s 封顶，且每一次都重新开闸。
    """
    async def fake_sleep(_delay: float) -> None:
        return None

    monkeypatch.setattr("app.okx.rest_client.asyncio.sleep", fake_sleep)
    client, _ = _client([_Resp(500)] * 40, okx_link_rest_max_attempts=1,
                        okx_link_breaker_failures=2, okx_link_breaker_cooldown_s=20.0)
    for _ in range(2):
        with pytest.raises(OKXError):
            await client._request("/api/v5/market/candles", {"instId": "X"})

    for _ in range(4):
        client._breaker.opened_until = time.monotonic() - 0.001    # 冷却刚好走完
        with contextlib.suppress(OKXError):
            # 生产里翻开半开态的是**普通公共读**（探针从不申请半开名额）
            client._breaker.ensure_closed_or_raise("/api/v5/market/ticker")
        assert await client.health() is False                      # 15s 一次
        with pytest.raises(OKXError):
            await client.get_server_time_ms()                      # 80s 一次

    assert client._breaker._cooldown == 20.0, "20 → 40 → 80 → 120 的单调上膛必须消失"
    assert client._breaker.failures == 0, "探针失败一次都不该进熔断计数"


def test_only_one_caller_gets_the_half_open_slot():
    """`_half_open` 原本是所有并发调用共享的一个布尔：冷却一过，**所有**在途调用都被当成
    「那一个半开探针」一起放出去。"""
    breaker = _breaker()
    breaker.note_failure()
    breaker.note_failure()
    assert breaker.opened_until > 0
    breaker.opened_until = time.monotonic() - 0.001      # 冷却刚好走完

    breaker.ensure_closed_or_raise("/api/v5/market/ticker")   # 第一个：拿到名额

    with pytest.raises(OKXError) as exc:
        breaker.ensure_closed_or_raise("/api/v5/market/ticker")
    assert exc.value.code == "OKX_CIRCUIT_OPEN"


def test_the_half_open_slot_expires_instead_of_wedging_the_breaker():
    """名额持有者可能被 `asyncio.wait_for` 取消而永远走不到 `note_ok` / `note_failure`——
    名额必须自己过期，否则一个用来自愈的机制会变成第二个「永远合不上」的死结。"""
    breaker = _breaker()
    breaker.note_failure()
    breaker.note_failure()
    breaker.opened_until = time.monotonic() - 0.001
    breaker.ensure_closed_or_raise("/api/v5/market/ticker")

    breaker._half_open_until = time.monotonic() - 0.001   # 租期到点（持有者被取消了）

    breaker.ensure_closed_or_raise("/api/v5/market/ticker")   # 必须能再发一个名额


def test_a_successful_half_open_trial_fully_closes_the_breaker():
    """棘轮从来不能阻止恢复，只能推迟恢复：任一次成功即完全复位（含冷却时长与半开名额）。"""
    breaker = _breaker()
    breaker.note_failure()
    breaker.note_failure()
    breaker.opened_until = time.monotonic() - 0.001
    breaker.ensure_closed_or_raise("/api/v5/public/time")

    breaker.note_ok()

    assert breaker.failures == 0 and breaker.opened_until == 0.0
    assert breaker._cooldown == 20.0 and breaker._half_open_until == 0.0
    breaker.ensure_closed_or_raise("/api/v5/public/time")     # 熔断已合：不再抛


# ---------------- 链路清扫协程互不连坐（F5-a） ----------------


async def test_the_link_sweeper_sweeps_the_pool_even_when_the_clock_sync_fails(monkeypatch):
    """时钟同步走的是**公共读**，公共链路一断它必抛——而 `pool.sweep()` 原本排在同一个 `try`
    里的它后面，于是跟着一起被跳过：生产上公共读死了三天，私有连接池的清扫也整整三天一次都
    没跑过。两件维护必须各自收口。"""
    from app import main as main_mod

    swept = asyncio.Event()

    class _Clock:
        def is_stale(self, _interval: float) -> bool:
            return True

        async def sync(self, _fetch) -> None:
            raise OKXError("OKX_UNREACHABLE", "无法连接 OKX (/api/v5/public/time): PoolTimeout: ")

    class _Pool:
        async def sweep(self, _now: float) -> int:
            swept.set()
            return 0

    class _Rest:
        async def get_server_time_ms(self) -> float:
            raise AssertionError("时钟同步的取数由 `_Clock.sync` 替身代劳，这里不该被调到")

    monkeypatch.setattr(main_mod, "get_server_clock", lambda: _Clock())
    monkeypatch.setattr(main_mod, "get_client_pool", lambda: _Pool())
    monkeypatch.setattr(main_mod, "get_rest_client", lambda: _Rest())

    task = asyncio.create_task(main_mod._okx_link_sweeper())
    try:
        await asyncio.wait_for(swept.wait(), 2.0)
    finally:
        task.cancel()
        await task
