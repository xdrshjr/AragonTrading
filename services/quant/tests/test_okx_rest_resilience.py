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

import httpx
import pytest

from app.config import Settings
from app.okx.link_state import get_link_registry
from app.okx.rest_client import OKXError, OKXRestClient


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

    async def get(self, path, params=None, headers=None):
        self.calls.append((path, params or {}))
        item = self._script[min(len(self.calls) - 1, len(self._script) - 1)]
        if isinstance(item, Exception):
            raise item
        return item

    async def aclose(self) -> None:
        return None


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
    client._client = fake        # 注入：绕过真实 httpx
    return client, fake


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
