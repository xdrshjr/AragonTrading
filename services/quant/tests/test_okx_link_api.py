"""链路健康只读端点单测（okx-link-resilience §4.1 / §4.2 / §4.3）。

两条纪律在这里被钉死：

- **不声明 `response_model`**：声明之后加法键会被 FastAPI 静默剔除，本仓库已为此栽过一次
  （`edgeSource` 那回）——一个「后端算了但前端永远拿不到」的字段比没有更糟。逐键断言。
- **本端点永不 502**：登记簿是本地状态，取不到就是 `unknown`。一个用来观察故障的端点，
  自己不许在故障时也跟着倒下。
"""
from __future__ import annotations

from httpx import ASGITransport, AsyncClient

from app.okx.clock import get_server_clock
from app.okx.link_state import get_link_registry

NOW = 1_786_000_000.0


def _load_app():
    from app.main import app
    return app


async def _client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=_load_app()), base_url="http://test")


class _FakeRest:
    """替身公共 REST：`health()` / `get_server_time_ms()` 可编程，并记录被调次数。"""

    def __init__(self, *, healthy: bool = True, raises: bool = False):
        self.healthy = healthy
        self.raises = raises
        self.health_calls = 0
        self.time_calls = 0
        self.last_ok_ts = 1
        self.last_latency_ms = 12

    async def health(self) -> bool:
        self.health_calls += 1
        if self.raises:
            raise RuntimeError("探活自己炸了")
        if self.healthy:
            get_link_registry().note_ok("rest:public", kind="restPublic",
                                        label="行情接口(REST)", latency_ms=28.0)
        return self.healthy

    async def get_server_time_ms(self) -> float:
        self.time_calls += 1
        if self.raises:
            raise RuntimeError("取不到服务器时间")
        import time
        return time.time() * 1000.0


async def test_connectivity_returns_the_full_wire_shape():
    reg = get_link_registry()
    reg.note_ok("rest:public", kind="restPublic", label="行情接口(REST)",
                latency_ms=28.0, now=NOW)
    reg.note_ws("ws:business:BTC-USDT-SWAP:1H:chart", kind="wsBusiness",
                label="实时流(K 线 · BTC-USDT-SWAP 1H)", event="message", now=NOW)

    async with await _client() as client:
        resp = await client.get("/api/okx/connectivity")

    assert resp.status_code == 200
    body = resp.json()
    assert set(body) == {"ts", "overall", "accountState", "summary", "channels",
                         "clockOffsetMs", "clockSyncedTs"}
    row = body["channels"][0]
    assert set(row) == {"key", "kind", "label", "state", "lastOkTs", "lastErrorTs",
                        "lastErrorCode", "lastErrorMessage", "latencyMs",
                        "consecutiveFailures", "reconnects1h", "resubscribes1h",
                        "gapRepairs1h", "lastMessageTs", "circuit", "circuitOpenUntilTs"}
    assert row["key"] == "rest:public" and row["latencyMs"] == 28


async def test_an_empty_registry_reports_unknown_rather_than_failing():
    async with await _client() as client:
        body = (await client.get("/api/okx/connectivity")).json()
    assert body["overall"] == "unknown"
    assert body["accountState"] == "unknown"
    assert body["channels"] == []
    assert body["summary"]


async def test_a_broken_private_channel_leaves_overall_on_the_market_side():
    """[评审 P1-5] 没配实盘的用户不该在行情正常时看到「OKX 不可达」。"""
    reg = get_link_registry()
    reg.note_ok("rest:public", kind="restPublic", label="行情接口(REST)", now=NOW)
    for _ in range(3):
        reg.note_failure("rest:private", kind="restPrivate", label="账户接口",
                         code="OKX_AUTH_KEY", message="key 无效", now=NOW)

    async with await _client() as client:
        body = (await client.get("/api/okx/connectivity")).json()

    assert body["overall"] == "up"
    assert body["accountState"] == "down"


async def test_a_closed_channel_disappears_from_the_endpoint():
    """[评审 P0-3] 用户换标的后旧通道不许继续拖着面板。"""
    reg = get_link_registry()
    reg.note_ws("ws:business:OLD:1H:chart", kind="wsBusiness", label="旧", event="connected",
                now=NOW - 9999)
    reg.close_channel("ws:business:OLD:1H:chart")

    async with await _client() as client:
        body = (await client.get("/api/okx/connectivity")).json()

    assert body["channels"] == []


async def test_probe_forces_a_health_check_and_a_clock_sync(monkeypatch):
    fake = _FakeRest()
    monkeypatch.setattr("app.api.okx_link.get_rest_client", lambda: fake)

    async with await _client() as client:
        body = (await client.post("/api/okx/connectivity/probe")).json()

    assert fake.health_calls == 1 and fake.time_calls == 1
    assert body["overall"] == "up"
    assert body["clockSyncedTs"] is not None
    assert get_server_clock().synced_ts is not None


async def test_probe_never_502s_even_when_everything_fails(monkeypatch):
    """一个用来观察故障的端点，自己不许在故障时也跟着倒下。"""
    monkeypatch.setattr("app.api.okx_link.get_rest_client", lambda: _FakeRest(raises=True))

    async with await _client() as client:
        resp = await client.post("/api/okx/connectivity/probe")

    assert resp.status_code == 200
    assert resp.json()["overall"] in ("unknown", "up", "idle", "degraded", "down")


async def test_health_carries_an_additive_link_summary(monkeypatch):
    """`/api/health` 未声明 `response_model`，故加法键不会被静默剔除（F-21）。"""
    fake = _FakeRest()
    monkeypatch.setattr("app.api.market.get_rest_client", lambda: fake)

    async with await _client() as client:
        body = (await client.get("/api/health")).json()

    # 既有键一字不变
    for key in ("status", "okx", "okxLatencyMs", "lastOkTs", "wsConfigured",
                "cachedFallbackEnabled", "maxPaperSessions"):
        assert key in body
    assert set(body["link"]) == {"overall", "summary"}
    assert body["link"]["overall"] == "up"
