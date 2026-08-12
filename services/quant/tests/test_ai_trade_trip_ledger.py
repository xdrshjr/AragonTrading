"""轴 A 纯核心：结算算术 / None 退化 / 口径同构 / 绝不抛（realized-edge-calibration §七 G-1~G-5b）。"""
from __future__ import annotations

import ast
import pathlib

from app.ai_auto.attribution import AttributionLedger
from app.ai_auto.exit_attribution import ExitLedger
from app.ai_auto.trip_ledger import (
    RoundEstimate,
    SettleContext,
    merge_open_leg,
    open_snapshot,
    settle,
    trip_meta_wire,
)


def _open(**over) -> dict:
    row = {"price": 100.0, "qty": 10.0, "fee": 0.0, "ts": 100, "side": "long",
           "stopLossPrice": 95.0, "takeProfitPrice": 110.0}
    row.update(over)
    return row


def _snap(*, ct_val: float = 0.01, estimate=None, atr_px=None, **over):
    return open_snapshot(_open(**over), seq=3, now_wall=1.0, atr_px=atr_px,
                         ct_val=ct_val, estimate=estimate)


def test_g1_settlement_arithmetic_is_exact():
    """G-1：entry=100 / exit=101 / qty=10 / ctVal=0.01 / 平仓腿 fee=0.05 / netMode。"""
    trip = settle(_snap(), {"price": 101.0, "qty": 10.0, "fee": 0.05, "pnl": 0.05, "ts": 200},
                  SettleContext(net_mode=True, ct_val=0.01, exit_source="agent"))
    assert trip.notional_usd == 10.0
    assert trip.move_bps == 100.0
    assert trip.realized_cost_bps == 50.0           # 双腿 = 0.0（开）+ 0.05（平）
    assert trip.gross_pnl_usd == trip.net_pnl_usd + 0.05
    assert trip.r_multiple == round(0.05 / 0.5, 4)  # risk = |100−95| × 10 × 0.01 = 0.5
    assert trip.planned_rr == 2.0
    assert trip.exit_source == "agent"


def test_g2_none_degradation_never_becomes_zero():
    """G-2：`ct_val==0` / 无止损 / 零位移三态下，对应读数恒 None 且**绝不为 0**。"""
    no_ct = settle(_snap(ct_val=0.0), {"price": 101.0, "pnl": 0.0, "fee": 0.0, "ts": 200},
                   SettleContext(net_mode=True, ct_val=0.0))
    assert no_ct.notional_usd is None and no_ct.realized_net_bps is None
    assert no_ct.r_multiple is None and no_ct.risk_usd is None
    assert no_ct.move_bps == 100.0        # 位移是纯价格比例，与合约乘数无关

    no_stop = settle(_snap(stopLossPrice=None), {"price": 101.0, "pnl": 0.1, "fee": 0.0, "ts": 2},
                     SettleContext(net_mode=True, ct_val=0.01))
    assert no_stop.r_multiple is None and no_stop.planned_rr is None

    flat = settle(_snap(), {"price": 100.0, "pnl": 0.0, "fee": 0.0, "ts": 200},
                  SettleContext(net_mode=True, ct_val=0.01))
    assert flat.move_bps == 0.0 and flat.stop_atr_mult is None


def test_g4_settlement_never_raises_and_module_has_no_raise():
    """G-4：畸形入参恒返回 None 且不抛；且模块内**没有任何 `raise` 语句**（AST 断言）。"""
    ctx = SettleContext(net_mode=True, ct_val=0.01)
    assert settle(_snap(), {"price": None}, ctx) is None
    assert settle(_snap(), {"price": "abc", "qty": "x", "pnl": float("nan")}, ctx) is None
    assert settle(None, {"price": 101.0}, ctx) is None
    assert open_snapshot({"price": 0.0, "qty": 1.0}, seq=1, now_wall=0.0, atr_px=None,
                         ct_val=0.01, estimate=None) is None

    source = pathlib.Path(__file__).resolve().parents[1] / "app/ai_auto/trip_ledger.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    assert not [n for n in ast.walk(tree) if isinstance(n, ast.Raise)]


def test_g5_net_pnl_is_isomorphic_across_the_three_ledgers():
    """G-5：同一笔平仓喂三个账本，**净额逐位相等**（守「绝不写第三种口径」）。"""
    close = {"price": 101.0, "qty": 10.0, "fee": 0.07, "pnl": 0.13, "ts": 200, "side": "close"}
    attribution = AttributionLedger(net_mode=False, ct_val=0.01)
    attribution.record_close(close)
    exits = ExitLedger(net_mode=False)
    exits.record(close, hold_seconds=100.0, source="agent")
    trip = settle(_snap(), close, SettleContext(net_mode=False, ct_val=0.01))
    assert trip.net_pnl_usd == round(attribution._trips[-1].net_pnl_usd, 6)
    assert trip.net_pnl_usd == round(0.13 - 0.07, 6)
    assert round(exits.net_usd["agent"], 6) == trip.net_pnl_usd


def test_g5b_round_trip_fee_is_both_legs_and_unknown_fee_omits_cost_keys():
    """G-5b：`fee_usd == 开腿 + 平腿`；任一腿缺 `fee` 键 ⇒ 成本类读数恒 None 且 wire 整键省略。"""
    trip = settle(_snap(fee=0.03), {"price": 101.0, "fee": 0.05, "pnl": 0.1, "ts": 200},
                  SettleContext(net_mode=True, ct_val=0.01))
    assert trip.fee_usd == round(0.03 + 0.05, 6) and trip.fee_known
    assert trip.realized_cost_bps == round(0.08 / 10.0 * 1e4, 4)

    snap = _snap()
    snap = snap.__class__(**{**snap.__dict__, "open_fee_known": False})
    unknown = settle(snap, {"price": 101.0, "pnl": 0.1, "ts": 200},   # 平仓腿也缺 fee 键
                     SettleContext(net_mode=True, ct_val=0.01))
    assert unknown.fee_known is False
    assert unknown.fee_usd is None and unknown.realized_cost_bps is None
    wire = trip_meta_wire(unknown)
    assert "realizedCostBps" not in wire and "roundTripFeeUsd" not in wire


def test_record_close_close_leg_fee_semantics_are_untouched():
    """G-5b 的另一半：既有 `RoundTrip.fee_usd`（**只记平仓腿**）逐位未被本轮改动。"""
    ledger = AttributionLedger(net_mode=True, ct_val=0.01)
    ledger.record_close({"price": 101.0, "qty": 10.0, "fee": 0.05, "pnl": 0.1, "ts": 200})
    assert ledger._trips[-1].fee_usd == 0.05


def test_partial_open_fills_are_idempotent_and_keep_the_estimate():
    """接缝 2：同一 `seq` 的第二次开仓成交只累加量与腿费，**绝不重置估计 / ts / 止损**。"""
    est = RoundEstimate(est_edge_bps=40.0, est_cost_bps=13.0, edge_source="atr",
                        cost_confidence="ok", decision_confidence=0.6, wake_kind="candle")
    first = _snap(estimate=est)
    merged = merge_open_leg(first, _open(price=102.0, qty=10.0, fee=0.02, ts=999), 0.01)
    assert merged.qty == 20.0 and merged.entry_px == 101.0
    assert merged.open_fee_usd == 0.02 and merged.ts == 100
    assert merged.estimate is est and merged.stop_px == 95.0
    # 任一腿缺 fee ⇒ 此后不可再翻真
    assert merge_open_leg(merged, {"price": 100.0, "qty": 1.0}, 0.01).open_fee_known is False


def test_stop_atr_mult_uses_the_open_moment_atr():
    """约束 4：`stopAtrMult` = 计划止损距离 / 开仓那一刻的 ATR；ATR 不可用 ⇒ None。"""
    with_atr = settle(_snap(atr_px=0.5), {"price": 101.0, "pnl": 0.1, "fee": 0.0, "ts": 2},
                      SettleContext(net_mode=True, ct_val=0.01))
    assert with_atr.stop_atr_mult == 10.0
    without = settle(_snap(atr_px=float("nan")), {"price": 101.0, "pnl": 0.1, "fee": 0.0, "ts": 2},
                     SettleContext(net_mode=True, ct_val=0.01))
    assert without.stop_atr_mult is None
