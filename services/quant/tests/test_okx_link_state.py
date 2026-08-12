"""链路健康登记簿单测（okx-link-resilience §2.5.1）。

本文件守的是三条**诚实纪律**——它们各自对应一种「可见性组件自己说谎」的失效：

1. `idle`：空闲不等于坏掉（否则没人开浏览器时面板会无中生有地报警）。
2. `close_channel`：通道有生命周期（否则用户换三次标的顶栏就永久变红）。
3. `overall` 只由行情面决定（否则没配实盘的用户在行情正常时天天看到「OKX 不可达」）。

一枚会说谎的芯片等于没有芯片——用户会学会忽略它，等真断线时没人再看。
"""
from __future__ import annotations

from app.okx.link_state import (
    ChannelHealth,
    LinkRegistry,
    StateThresholds,
    derive_state,
    get_link_registry,
)

_TH = StateThresholds()
NOW = 10_000.0


def _health(**kw) -> ChannelHealth:
    base = {"key": "rest:public", "kind": "restPublic", "label": "行情接口(REST)"}
    return ChannelHealth(**{**base, **kw})


# ---------------- derive_state ----------------


def test_no_samples_is_unknown_not_up():
    """没有样本就别假装有结论。"""
    assert derive_state(_health(), NOW, _TH) == "unknown"


def test_consecutive_failures_or_open_circuit_is_down():
    assert derive_state(_health(last_error_ts=NOW, consecutive_failures=2), NOW, _TH) == "down"
    assert derive_state(_health(last_ok_ts=NOW, circuit="open"), NOW, _TH) == "down"


def test_a_recent_failure_is_degraded():
    h = _health(last_ok_ts=NOW - 5, last_error_ts=NOW - 10, consecutive_failures=1)
    assert derive_state(h, NOW, _TH) == "degraded"


def test_rest_idle_is_idle_not_degraded():
    """[评审 P1-4] 「很久没被调用」是**闲**，不是坏——诚实原则是双向的。"""
    h = _health(last_ok_ts=NOW - 600)          # 十分钟没成功，但期间**零失败**
    assert derive_state(h, NOW, _TH) == "idle"


def test_ws_silence_is_degraded_under_the_same_condition():
    """WS 侧保留「静默即 degraded」：它本就该每 15s 有一帧入站（心跳保证了最小节律）。"""
    h = _health(key="ws:business:X:1H:chart", kind="wsBusiness", label="实时流",
                last_ok_ts=NOW - 600, last_message_ts=NOW - 600)
    assert derive_state(h, NOW, _TH) == "degraded"


def test_a_healthy_rest_channel_is_up():
    assert derive_state(_health(last_ok_ts=NOW - 3), NOW, _TH) == "up"


def test_thresholds_of_zero_switch_the_judgement_off():
    th = StateThresholds(ws_silence_s=0.0, rest_idle_s=0.0)
    ws = _health(key="ws:public:X:1H:chart", kind="wsPublic", label="盘口",
                 last_message_ts=NOW - 9999)
    assert derive_state(ws, NOW, th) == "up"
    assert derive_state(_health(last_ok_ts=NOW - 9999), NOW, th) == "up"


# ---------------- 登记簿 ----------------


def test_note_ok_clears_failures_and_tracks_latency_ewma():
    reg = LinkRegistry()
    reg.note_failure("rest:public", kind="restPublic", label="L", code="X", message="m", now=NOW)
    reg.note_ok("rest:public", kind="restPublic", label="L", latency_ms=100.0, now=NOW + 1)
    reg.note_ok("rest:public", kind="restPublic", label="L", latency_ms=200.0, now=NOW + 2)

    row = reg.snapshot(now=NOW + 3)["channels"][0]
    assert row["consecutiveFailures"] == 0
    assert row["latencyMs"] == 130          # 100*0.7 + 200*0.3，四舍五入到整毫秒


def test_error_message_is_truncated_and_code_is_kept():
    reg = LinkRegistry()
    reg.note_failure("rest:public", kind="restPublic", label="L",
                     code="OKX_RATE_LIMITED", message="x" * 500, now=NOW)
    row = reg.snapshot(now=NOW)["channels"][0]
    assert row["lastErrorCode"] == "OKX_RATE_LIMITED"
    assert len(row["lastErrorMessage"]) == 200


def test_rolling_counters_only_count_the_last_hour():
    reg = LinkRegistry()
    key = "ws:business:BTC-USDT-SWAP:1H:chart"
    reg.note_ws(key, kind="wsBusiness", label="K 线", event="connected", now=NOW - 7200)
    reg.note_ws(key, kind="wsBusiness", label="K 线", event="connected", now=NOW - 60)
    reg.note_ws(key, kind="wsBusiness", label="K 线", event="gap_repaired", now=NOW - 30)

    row = reg.snapshot(now=NOW)["channels"][0]
    assert row["reconnects1h"] == 1         # 两小时前那次不计
    assert row["gapRepairs1h"] == 1


def test_channel_cap_evicts_the_least_recently_updated_ws_channel():
    reg = LinkRegistry()
    reg.note_ok("rest:public", kind="restPublic", label="REST", now=NOW)
    for i in range(40):
        reg.note_ws(f"ws:business:S{i}:1H:chart", kind="wsBusiness", label=f"S{i}",
                    event="connected", now=NOW + i)
    keys = {r["key"] for r in reg.snapshot(now=NOW + 100)["channels"]}
    assert len(keys) <= 32
    assert "rest:public" in keys            # REST 只有两条，永远保留
    assert "ws:business:S0:1H:chart" not in keys


# ---------------- [评审 P0-3] 通道生命周期 ----------------


def test_closed_channel_leaves_channels_and_stops_dragging_overall_down():
    """只增不删 = 顶栏永久变红。用户每换一次标的就会拆掉旧 WS、建一条新的。"""
    reg = LinkRegistry()
    old = "ws:business:BTC-USDT-SWAP:1H:chart"
    new = "ws:business:ETH-USDT-SWAP:1H:chart"
    reg.note_ws(old, kind="wsBusiness", label="旧", event="connected", now=NOW - 5000)
    reg.note_ws(new, kind="wsBusiness", label="新", event="message", now=NOW)
    reg.note_ok("rest:public", kind="restPublic", label="REST", now=NOW)

    assert reg.snapshot(now=NOW)["overall"] == "degraded"      # 旧键静默五千秒，拖着 overall

    reg.close_channel(old)

    snap = reg.snapshot(now=NOW)
    assert old not in {r["key"] for r in snap["channels"]}
    assert snap["overall"] == "up"


def test_a_closed_channel_revives_on_the_next_note():
    """换回原来的标的是常态：同键再上报即复活。"""
    reg = LinkRegistry()
    key = "ws:business:BTC-USDT-SWAP:1H:chart"
    reg.note_ws(key, kind="wsBusiness", label="K 线", event="connected", now=NOW)
    reg.close_channel(key)
    assert reg.snapshot(now=NOW)["channels"] == []

    reg.note_ws(key, kind="wsBusiness", label="K 线", event="message", now=NOW + 1)

    assert [r["key"] for r in reg.snapshot(now=NOW + 1)["channels"]] == [key]


def test_owner_segment_keeps_two_subscribers_of_the_same_symbol_apart():
    """不加 owner 段，一个 live_auto 会话与一个浏览器图表会**共写同一条记录**，状态来回翻。"""
    reg = LinkRegistry()
    chart = "ws:business:BTC-USDT-SWAP:1H:chart"
    live = "ws:business:BTC-USDT-SWAP:1H:live:sess-1"
    reg.note_ws(chart, kind="wsBusiness", label="图表", event="message", now=NOW)
    reg.note_failure(live, kind="wsBusiness", label="会话", code="WS_PONG_TIMEOUT",
                     message="心跳应答超时", now=NOW)

    rows = {r["key"]: r["state"] for r in reg.snapshot(now=NOW)["channels"]}
    assert rows[chart] == "up"
    assert rows[live] == "degraded"


# ---------------- [评审 P1-5] overall 只由行情面决定 ----------------


def test_a_broken_private_channel_never_reddens_overall():
    """没配实盘 / 密钥填错 / IP 未白名单都会让私有面 down——它不许冒充行情断线。"""
    reg = LinkRegistry()
    reg.note_ok("rest:public", kind="restPublic", label="行情接口(REST)", now=NOW)
    reg.note_failure("rest:private", kind="restPrivate", label="账户接口",
                     code="OKX_AUTH_KEY", message="bad key", now=NOW)
    reg.note_failure("rest:private", kind="restPrivate", label="账户接口",
                     code="OKX_AUTH_KEY", message="bad key", now=NOW)

    snap = reg.snapshot(now=NOW)
    assert snap["overall"] == "up"
    assert snap["accountState"] == "down"
    assert "账户接口" in snap["summary"]      # 藏起来同样是说谎：它出现在摘要句里


def test_idle_does_not_drag_overall_below_up():
    reg = LinkRegistry()
    reg.note_ok("rest:public", kind="restPublic", label="REST", now=NOW - 600)
    snap = reg.snapshot(now=NOW)
    assert snap["channels"][0]["state"] == "idle"
    assert snap["overall"] == "up"


def test_snapshot_wire_shape_is_complete():
    """逐键断言：端点**不声明 `response_model`**，键的完整性只能靠这条用例守。"""
    reg = LinkRegistry()
    reg.note_ok("rest:public", kind="restPublic", label="行情接口(REST)",
                latency_ms=28.0, now=NOW)
    snap = reg.snapshot(now=NOW)
    assert set(snap) == {"ts", "overall", "accountState", "summary", "channels"}
    assert set(snap["channels"][0]) == {
        "key", "kind", "label", "state", "lastOkTs", "lastErrorTs", "lastErrorCode",
        "lastErrorMessage", "latencyMs", "consecutiveFailures", "reconnects1h",
        "resubscribes1h", "gapRepairs1h", "lastMessageTs", "circuit", "circuitOpenUntilTs"}


def test_note_circuit_only_touches_an_existing_channel():
    reg = LinkRegistry()
    reg.note_circuit("rest:public", "open", NOW + 20)      # 通道还不存在 ⇒ no-op，不炸
    reg.note_ok("rest:public", kind="restPublic", label="REST", now=NOW)
    reg.note_circuit("rest:public", "open", NOW + 20)
    row = reg.snapshot(now=NOW)["channels"][0]
    assert row["circuit"] == "open" and row["state"] == "down"


def test_reset_clears_everything_and_registry_is_a_singleton():
    assert get_link_registry() is get_link_registry()
    reg = LinkRegistry()
    reg.note_ok("rest:public", kind="restPublic", label="REST", now=NOW)
    reg.reset()
    assert reg.snapshot(now=NOW)["channels"] == []
