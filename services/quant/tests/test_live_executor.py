"""LiveExecutor 纯/桩单测（live-auto-trading §2.2/§2.3）：信号→真实订单映射、张数/名义、
止盈止损、max_hold 计时平仓、失败不崩、net_mode 反手清算确认(R5)、LIVE_NO_POSITION 良性(R8)。

OKX 调用全程经注入桩，不触网；铁律：占位假凭证，绝不写真实凭证。
"""
from __future__ import annotations

import pytest

from app.config import Settings
from app.engine.simulator import RiskParams
from app.live_auto.executor import LiveExecutor
from app.okx.accounts import AccountCredentials
from app.okx.private_client import OKXPrivateError

_REF_PX = 30000.0


class _Sig:
    def __init__(self, side: str, strength: float = 1.0, reason: str = ""):
        self.side = side
        self.strength = strength
        self.reason = reason


def _make_executor(**over):
    """构造带桩 OKX 回调的 executor；返回 (executor, calls 记录)。"""
    calls = {"orders": [], "closes": [], "reads": 0}
    positions_seq = over.pop("positions_seq", None)
    raise_on_open = over.pop("raise_on_open", None)
    raise_on_close = over.pop("raise_on_close", None)

    async def place_order_fn(order):
        calls["orders"].append(order)
        if raise_on_open:
            raise raise_on_open
        return {"ok": True, "ordId": "x1", "clOrdId": "aragonx"}

    async def close_position_fn(close):
        calls["closes"].append(close)
        if raise_on_close:
            raise raise_on_close
        return {"ok": True, "clOrdId": "aragoncx"}

    async def positions_fn():
        calls["reads"] += 1
        if positions_seq:
            idx = min(calls["reads"] - 1, len(positions_seq) - 1)
            return positions_seq[idx]
        return []

    executor = LiveExecutor(
        creds=AccountCredentials("ak-fake", "sk-fake-000000", "pp-fake-000000"),
        account_id=1, inst_id="BTC-USDT-SWAP", td_mode="cross",
        pos_mode=over.pop("pos_mode", "long_short_mode"),
        leverage=over.pop("leverage", 3.0), budget_usd=over.pop("budget_usd", 1000.0),
        risk=over.pop("risk", RiskParams()), ct_val=over.pop("ct_val", 0.01),
        lot_sz=over.pop("lot_sz", 0.01), min_sz=over.pop("min_sz", 0.01),
        settings=Settings(), place_order_fn=place_order_fn,
        close_position_fn=close_position_fn, positions_fn=positions_fn)
    return executor, calls


def _trade_events(events):
    return [e for e in events if e["type"] == "trade"]


async def test_open_long_from_flat_places_one_order():
    ex, calls = _make_executor()
    events = await ex.apply([_Sig("long")], ref_px=_REF_PX, bar_index=0, ts=1000)
    assert len(calls["orders"]) == 1
    assert ex.projected == "long"
    trades = _trade_events(events)
    assert len(trades) == 1 and trades[0]["data"]["side"] == "long"


async def test_open_position_pct_uses_snapped_order_size_and_position_payload():
    ex, calls = _make_executor(
        leverage=2.0,
        budget_usd=1000.0,
        ct_val=1.0,
        lot_sz=1.0,
        min_sz=1.0,
    )
    events = await ex.apply(
        [_Sig("long", strength=0.4)],
        ref_px=33.0,
        bar_index=0,
        ts=1000,
    )

    assert calls["orders"][0].sz == 24.0
    expected = 24.0 * 33.0 / (1000.0 * 2.0)
    trade = _trade_events(events)[0]["data"]
    assert trade["positionPct"] == pytest.approx(expected)
    assert ex.entry_position_pct == pytest.approx(expected)
    assert ex.position_payload(34.0)["positionPct"] == pytest.approx(expected)


async def test_close_inherits_entry_position_pct_then_clears_state():
    ex, _ = _make_executor()
    opened = _trade_events(
        await ex.apply([_Sig("long", 0.4)], ref_px=_REF_PX, bar_index=0, ts=1000)
    )[0]["data"]

    closed = _trade_events(
        await ex.apply([_Sig("close")], ref_px=_REF_PX, bar_index=1, ts=2000)
    )[0]["data"]

    assert closed["positionPct"] == pytest.approx(opened["positionPct"])
    assert ex.entry_position_pct is None


async def test_reverse_close_keeps_old_pct_and_new_open_uses_new_pct():
    ex, _ = _make_executor()
    first = _trade_events(
        await ex.apply([_Sig("long", 0.4)], ref_px=_REF_PX, bar_index=0, ts=1000)
    )[0]["data"]
    reversed_trades = _trade_events(
        await ex.apply([_Sig("short", 0.7)], ref_px=_REF_PX, bar_index=1, ts=2000)
    )

    assert reversed_trades[0]["data"]["positionPct"] == pytest.approx(
        first["positionPct"]
    )
    assert reversed_trades[1]["data"]["positionPct"] != pytest.approx(
        first["positionPct"]
    )
    assert ex.entry_position_pct == pytest.approx(
        reversed_trades[1]["data"]["positionPct"]
    )


@pytest.mark.parametrize(
    ("sz", "ct_val", "price", "budget", "leverage"),
    [
        (float("nan"), 1.0, 1.0, 1.0, 1.0),
        (float("inf"), 1.0, 1.0, 1.0, 1.0),
        (1.0, 0.0, 1.0, 1.0, 1.0),
        (1.0, 1.0, -1.0, 1.0, 1.0),
        (1.0, 1.0, 1.0, 0.0, 1.0),
        (1.0, 1.0, 1.0, 1.0, float("inf")),
    ],
)
def test_position_pct_for_size_rejects_nonfinite_or_nonpositive_inputs(
    sz, ct_val, price, budget, leverage
):
    assert LiveExecutor._position_pct_for_size(
        sz=sz,
        ct_val=ct_val,
        reference_price=price,
        member_budget_usd=budget,
        leverage=leverage,
    ) is None


def test_compute_sz_returns_zero_when_finite_inputs_overflow_intermediate():
    ex, _ = _make_executor(budget_usd=1e308, leverage=1e308)
    assert ex._compute_sz(1.0, 1.0) == 0.0


async def test_same_side_signal_is_skipped():
    ex, calls = _make_executor()
    await ex.apply([_Sig("long")], ref_px=_REF_PX, bar_index=0, ts=1000)
    await ex.apply([_Sig("long")], ref_px=_REF_PX, bar_index=1, ts=2000)
    assert len(calls["orders"]) == 1   # 同向不加仓


async def test_close_signal_closes_position():
    ex, calls = _make_executor()
    await ex.apply([_Sig("long")], ref_px=_REF_PX, bar_index=0, ts=1000)
    events = await ex.apply([_Sig("close")], ref_px=_REF_PX, bar_index=1, ts=2000)
    assert len(calls["closes"]) == 1
    assert ex.projected == "flat"
    assert _trade_events(events)[0]["data"]["side"] == "close"


async def test_reverse_long_to_short_closes_then_opens():
    ex, calls = _make_executor()
    await ex.apply([_Sig("long")], ref_px=_REF_PX, bar_index=0, ts=1000)
    await ex.apply([_Sig("short")], ref_px=_REF_PX, bar_index=1, ts=2000)
    assert len(calls["closes"]) == 1 and len(calls["orders"]) == 2
    assert ex.projected == "short"


async def test_sz_below_min_is_skipped():
    ex, calls = _make_executor(budget_usd=0.0001, min_sz=1.0, lot_sz=1.0)
    events = await ex.apply([_Sig("long")], ref_px=_REF_PX, bar_index=0, ts=1000)
    assert len(calls["orders"]) == 0
    assert ex.projected == "flat"
    assert any(e["type"] == "status" for e in events)


async def test_attach_algo_take_profit_stop_loss_symmetry():
    risk = RiskParams(stop_loss_pct=0.02, take_profit_pct=0.04)
    ex, calls = _make_executor(risk=risk)
    await ex.apply([_Sig("long")], ref_px=100.0, bar_index=0, ts=1000)
    order = calls["orders"][0]
    assert order.sl_trigger_px == pytest.approx(98.0)
    assert order.tp_trigger_px == pytest.approx(104.0)
    # 空头对称
    ex2, calls2 = _make_executor(risk=risk)
    await ex2.apply([_Sig("short")], ref_px=100.0, bar_index=0, ts=1000)
    order2 = calls2["orders"][0]
    assert order2.sl_trigger_px == pytest.approx(102.0)
    assert order2.tp_trigger_px == pytest.approx(96.0)


async def test_max_hold_bars_time_exit():
    ex, calls = _make_executor(risk=RiskParams(max_hold_bars=2))
    await ex.apply([_Sig("long")], ref_px=_REF_PX, bar_index=0, ts=1000)
    await ex.apply([], ref_px=_REF_PX, bar_index=1, ts=2000)
    assert ex.projected == "long"   # 未到 max_hold
    events = await ex.apply([], ref_px=_REF_PX, bar_index=2, ts=3000)
    assert ex.projected == "flat"
    assert len(calls["closes"]) == 1
    assert _trade_events(events)[0]["data"]["reason"] == "max_hold"


async def test_open_failure_does_not_crash_and_keeps_flat():
    ex, calls = _make_executor(raise_on_open=OKXPrivateError("51008", "余额不足"))
    events = await ex.apply([_Sig("long")], ref_px=_REF_PX, bar_index=0, ts=1000)
    assert ex.projected == "flat"
    errors = [e for e in events if e["type"] == "error"]
    assert errors and errors[0]["data"]["code"] == "51008"


async def test_net_mode_reverse_waits_for_clearing_then_opens():
    # 反手 close 后净仓立即清零 → confirm 通过即 open
    ex, calls = _make_executor(pos_mode="net_mode", positions_seq=[[]])
    await ex.apply([_Sig("long")], ref_px=_REF_PX, bar_index=0, ts=1000)
    await ex.apply([_Sig("short")], ref_px=_REF_PX, bar_index=1, ts=2000)
    assert len(calls["closes"]) == 1 and len(calls["orders"]) == 2
    assert ex.projected == "short"


async def test_net_mode_reverse_skips_open_when_not_cleared(monkeypatch):
    # 净仓持续未清算 → 本根跳过 open（仅 close），交下一根重试（R5）
    monkeypatch.setattr("app.live_auto.executor.asyncio.sleep", _no_sleep)
    stuck = [{"instId": "BTC-USDT-SWAP", "pos": 5.0}]
    ex, calls = _make_executor(pos_mode="net_mode", positions_seq=[stuck])
    await ex.apply([_Sig("long")], ref_px=_REF_PX, bar_index=0, ts=1000)
    events = await ex.apply([_Sig("short")], ref_px=_REF_PX, bar_index=1, ts=2000)
    assert len(calls["closes"]) == 1 and len(calls["orders"]) == 1   # open 被跳过
    assert any(e["type"] == "status" for e in events)


async def test_close_no_position_is_benign():
    ex, calls = _make_executor(raise_on_close=OKXPrivateError("LIVE_NO_POSITION", "无可平持仓"))
    await ex.apply([_Sig("long")], ref_px=_REF_PX, bar_index=0, ts=1000)
    events = await ex.apply([_Sig("close")], ref_px=_REF_PX, bar_index=1, ts=2000)
    assert ex.projected == "flat"
    assert not [e for e in events if e["type"] == "error"]
    assert any(e["type"] == "status" for e in events)


async def test_reconcile_swallows_read_error_and_emits_status():
    # H2：reconcile 读取抛 OKXError 不冒泡，返回 status 事件、projected 不变、失败计数 +1。
    ex, _ = _make_executor()
    await ex.apply([_Sig("long")], ref_px=_REF_PX, bar_index=0, ts=1000)
    assert ex.projected == "long"

    async def boom():
        raise OKXPrivateError("OKX_UNREACHABLE", "网络抖动")

    ex._read_positions = boom
    events = await ex.reconcile(_REF_PX, ts=2000)
    assert ex.projected == "long"                       # 持仓状态机不被污染
    assert any(e["type"] == "status" for e in events)
    assert not [e for e in events if e["type"] == "error"]   # 未达阈不推 error
    assert ex._reconcile_fail_streak == 1


async def test_reconcile_degrades_after_consecutive_failures_then_recovers():
    # H2：连续失败达阈 → degraded + 推一次 error；成功 reconcile 后 degraded 清零并推恢复 status。
    ex, _ = _make_executor()
    await ex.apply([_Sig("long")], ref_px=_REF_PX, bar_index=0, ts=1000)

    async def boom():
        raise OKXPrivateError("OKX_UNREACHABLE", "网络抖动")

    ex._read_positions = boom
    max_fail = ex.settings.live_auto_reconcile_max_failures
    errors = []
    for i in range(max_fail):
        errors += [e for e in await ex.reconcile(_REF_PX, ts=2000 + i) if e["type"] == "error"]
    assert ex.degraded is True
    assert len(errors) == 1 and errors[0]["data"]["code"] == "LIVE_AUTO_RECONCILE_DEGRADED"

    async def ok():
        return [{"instId": "BTC-USDT-SWAP", "pos": ex.entry_qty_sz, "upl": 0.0}]

    ex._read_positions = ok
    recover = await ex.reconcile(_REF_PX, ts=3000)
    assert ex.degraded is False and ex._reconcile_fail_streak == 0
    assert any(e["type"] == "status" for e in recover)


async def test_flatten_bounded_retry_returns_residual_on_persistent_failure(monkeypatch):
    # H4：平仓持续失败 → 有界重试后返回 ok=False + residual，projected 未被假平（残留仓如实保留）。
    monkeypatch.setattr("app.live_auto.executor.asyncio.sleep", _no_sleep)
    ex, calls = _make_executor(raise_on_close=OKXPrivateError("51008", "余额不足"))
    await ex.apply([_Sig("long")], ref_px=_REF_PX, bar_index=0, ts=1000)
    entry_position_pct = ex.entry_position_pct
    result = await ex.flatten(_REF_PX, ts=2000)
    assert result["ok"] is False
    assert result["residual"] == {"side": "long", "size": ex.entry_qty_sz}
    assert ex.projected == "long"                      # 残留仓未被改 flat
    assert ex.entry_position_pct == entry_position_pct
    assert len(calls["closes"]) == ex.settings.live_auto_flatten_max_retries + 1


async def test_flatten_succeeds_on_retry_after_transient_failure(monkeypatch):
    # H4：首次平仓失败、重试成功 → ok=True，不留 residual。
    monkeypatch.setattr("app.live_auto.executor.asyncio.sleep", _no_sleep)
    ex, calls = _make_executor()
    await ex.apply([_Sig("long")], ref_px=_REF_PX, bar_index=0, ts=1000)
    attempts = {"n": 0}

    async def flaky_close(close):
        calls["closes"].append(close)
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise OKXPrivateError("OKX_UNREACHABLE", "瞬时抖动")
        return {"ok": True, "clOrdId": "aragoncx"}

    ex._close_position = flaky_close
    result = await ex.flatten(_REF_PX, ts=2000)
    assert result["ok"] is True and ex.projected == "flat"
    assert attempts["n"] == 2


# ---------- per-trade 收益率 pnlPct（auto-trade-pnl-records §3.2 / §7.1）----------
async def test_close_trade_carries_pnl_pct_for_long_profit():
    # 多头盈利平仓携带 pnlPct = (exit/entry - 1) × leverage（保证金回报口径）；开仓行不含 pnlPct。
    ex, _ = _make_executor(leverage=3.0)
    open_trade = _trade_events(
        await ex.apply([_Sig("long")], ref_px=30000.0, bar_index=0, ts=1000))[0]["data"]
    assert "pnlPct" not in open_trade
    close = _trade_events(
        await ex.apply([_Sig("close")], ref_px=31500.0, bar_index=1, ts=2000))[0]["data"]
    assert close["side"] == "close" and close["pnl"] > 0
    assert close["pnlPct"] == pytest.approx(0.15)   # (31500/30000 - 1) × 3


async def test_close_trade_pnl_pct_sign_correct_for_short_win():
    # 空头盈利（价跌为盈）平仓：pnlPct 为正、符号正确。
    ex, _ = _make_executor(leverage=3.0)
    await ex.apply([_Sig("short")], ref_px=30000.0, bar_index=0, ts=1000)
    close = _trade_events(
        await ex.apply([_Sig("close")], ref_px=28500.0, bar_index=1, ts=2000))[0]["data"]
    assert close["pnl"] > 0 and close["pnlPct"] == pytest.approx(0.15)


async def test_close_trade_pnl_pct_scales_with_leverage():
    # 同价差下 leverage=5 的 pnlPct ≈ leverage=1 的 5 倍（杠杆敏感）。
    ex1, _ = _make_executor(leverage=1.0)
    await ex1.apply([_Sig("long")], ref_px=30000.0, bar_index=0, ts=1000)
    c1 = _trade_events(
        await ex1.apply([_Sig("close")], ref_px=31500.0, bar_index=1, ts=2000))[0]["data"]
    ex5, _ = _make_executor(leverage=5.0)
    await ex5.apply([_Sig("long")], ref_px=30000.0, bar_index=0, ts=1000)
    c5 = _trade_events(
        await ex5.apply([_Sig("close")], ref_px=31500.0, bar_index=1, ts=2000))[0]["data"]
    assert c5["pnlPct"] == pytest.approx(c1["pnlPct"] * 5)


async def test_close_return_pct_returns_none_on_nonpositive_leverage_without_zero_division():
    # v2（P1-2）：杠杆 / 名义价值非正 → 先校验后相除返回 None，绝不抛 ZeroDivisionError；close 省略 pnlPct。
    ex, _ = _make_executor(leverage=3.0)
    await ex.apply([_Sig("long")], ref_px=30000.0, bar_index=0, ts=1000)
    ex.leverage = 0.0                                  # 退化断面：守卫须真正生效
    assert ex._close_return_pct(31500.0) is None
    close = _trade_events(
        await ex.apply([_Sig("close")], ref_px=31500.0, bar_index=1, ts=2000))[0]["data"]
    assert "pnlPct" not in close and ex.projected == "flat"   # 退化省略键、平仓正常完成


async def _no_sleep(*_args, **_kwargs):
    return None
