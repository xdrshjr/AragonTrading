"""影子意图账本纯核心（ai-trade-evidence-gated-capital §7.1，L-1..L-16）。"""
from __future__ import annotations

from dataclasses import replace

import pytest

from app.ai_auto.profit_lock import ProfitLockRule
from app.ai_auto.profit_lock_bar import LockBar, evaluate_lock_bar
from app.ai_auto.shadow_ledger import (
    RefPolicy,
    advance_shadow,
    open_shadow,
    replay_shadow,
)

BAR = "30m"
BAR_S = 1800
T0 = 1_790_000_000 // BAR_S * BAR_S
POLICY = RefPolicy()


def _shadow(side="long", *, px=100.0, atr_px=0.6, cost_bps=10.0, policy=POLICY):
    return open_shadow(inst_id="ETH-USDT-SWAP", bar=BAR, side=side, now_wall=T0 + 10, px=px,
                       atr_px=atr_px, cost_bps=cost_bps, policy=policy, run_id=1, round_no=1,
                       fallback_cost_bps=12.0, shadow_id="s1")


def _bar(k, o, h, low, c):
    return {"ts": T0 + k * BAR_S, "o": o, "h": h, "l": low, "c": c}


def _run(shadow, bars):
    for row in bars:
        shadow = advance_shadow(shadow, row, POLICY)
    return shadow


def _tp_path():
    """武装后跳空高开直达止盈；新锁线（101.3）始终低于本根低点，路径无根内歧义。"""
    return [_bar(1, 100.0, 100.5, 99.95, 100.45), _bar(2, 102.0, 102.6, 101.95, 102.5)]


def _mirror(rows):
    return [{"ts": r["ts"], "o": 200 - r["o"], "h": 200 - r["l"], "l": 200 - r["h"],
             "c": 200 - r["c"]} for r in rows]


def test_l1_long_take_profit():
    out = _run(_shadow(), _tp_path())
    assert out.status == "matured" and out.exit_kind == "tp"
    assert out.exit_px == pytest.approx(102.4)
    assert out.net_bps == pytest.approx(out.tp_bps - 10.0)


def test_l2_gap_through_stop_fills_at_open():
    out = _run(_shadow(), [_bar(1, 98.5, 98.7, 98.0, 98.2)])
    assert out.exit_kind == "sl" and out.exit_px == pytest.approx(98.5)
    assert out.net_bps == pytest.approx(-150.0 - 10.0)


def test_l3_stop_checked_before_take_profit_in_same_bar():
    out = _run(_shadow(), [_bar(1, 100.0, 102.5, 98.7, 101.5)])
    assert out.exit_kind == "sl" and out.exit_px == pytest.approx(out.sl_px)


def _lock_path():
    return [_bar(1, 100.0, 100.5, 99.95, 100.4), _bar(2, 100.3, 100.35, 100.0, 100.1)]


def test_l4_profit_lock_fill_matches_round1_core():
    shadow = _shadow()
    out = _run(shadow, _lock_path())
    state, expected = None, None
    for row in _lock_path():
        res = evaluate_lock_bar(state, POLICY.lock, LockBar(
            seq=0, side="long", entry_px=100.0, qty_coin=1.0, ts=row["ts"],
            opened_ts=shadow.opened_bar_ts, open_px=row["o"], high_px=row["h"],
            low_px=row["l"], cost_bps=10.0, atr_bps=shadow.atr_bps, close_px=row["c"]))
        state = res.state
        if res.verdict is not None:
            expected = res.fill_px
            break
    assert expected is not None
    assert out.exit_kind == "lock" and out.exit_px == expected


def test_l5_l15_time_exit_by_timestamp_on_24th_bar():
    rows = [_bar(k, 100.0, 100.0, 100.0, 100.0) for k in range(1, 24)]
    mid = _run(_shadow(), rows)
    assert mid.status == "open" and mid.bars_seen == 23
    out = advance_shadow(mid, _bar(24, 100.0, 100.05, 100.0, 100.05), POLICY)
    assert out.exit_kind == "time" and out.exit_px == pytest.approx(100.05)
    assert out.net_bps == pytest.approx(5.0 - 10.0)


def test_l6_intent_bar_is_not_evaluated():
    shadow = _shadow()
    assert advance_shadow(shadow, _bar(0, 90.0, 110.0, 90.0, 95.0), POLICY) is shadow


@pytest.mark.parametrize("rows", [_tp_path(), _lock_path(),
                                  [_bar(1, 98.5, 98.7, 98.0, 98.2)],
                                  [_bar(k, 100.0, 100.0, 100.0, 100.0) for k in range(1, 25)]])
def test_l7_short_is_mirror_of_long(rows):
    long_out = _run(_shadow("long"), rows)
    short_out = _run(_shadow("short"), _mirror(rows))
    assert short_out.exit_kind == long_out.exit_kind
    assert short_out.net_bps == pytest.approx(long_out.net_bps)


def test_l8_invalid_atr_voids():
    out = _shadow(atr_px=None)
    assert out.status == "void" and out.void_reason == "atr_unavailable"
    assert out.net_bps is None
    assert _shadow(px=float("nan")).void_reason == "px_unavailable"


def test_l9_cost_floor_lifts_stop_and_keeps_one_to_two():
    out = _shadow(atr_px=0.3, cost_bps=80.0)
    assert out.sl_bps == pytest.approx(160.0)
    assert out.tp_bps >= 2 * out.sl_bps


def test_l9b_missing_cost_uses_fallback():
    assert _shadow(cost_bps=None).cost_bps == pytest.approx(12.0)


def test_l10_version_sensitive_to_every_parameter():
    base = RefPolicy()
    variants = [replace(base, sl_atr_mult=2.5), replace(base, tp_atr_mult=5.0),
                replace(base, max_hold_bars=12), replace(base, sl_cost_floor_mult=3.0)]
    for name in ("arm_cost_mult", "arm_atr_mult", "buffer_bps", "trail_cost_mult",
                 "trail_atr_mult", "keep_ratio", "min_hold_s"):
        variants.append(replace(base, lock=replace(base.lock, **{
            name: getattr(base.lock, name) + 0.1})))
    versions = {v.version() for v in variants}
    assert base.version() not in versions and len(versions) == len(variants)
    reordered = RefPolicy(lock=ProfitLockRule(), max_hold_bars=24, tp_atr_mult=4.0,
                          sl_atr_mult=2.0)
    assert reordered.version() == base.version()


def test_l11_finished_shadow_is_returned_as_is():
    out = _run(_shadow(), [_bar(1, 98.5, 98.7, 98.0, 98.2)])
    assert advance_shadow(out, _bar(2, 100.0, 101.0, 99.0, 100.0), POLICY) is out


def test_l12_lock_epoch_is_constant_so_drawdown_is_reachable():
    out = _run(_shadow(), _lock_path())
    assert out.exit_kind == "lock" and out.bars_seen == 2
    # 反证：若按 bars_seen 当 seq，第二根会重置 epoch，回撤判定永远走不到。
    shadow, state = _shadow(), None
    for seq, row in enumerate(_lock_path()):
        res = evaluate_lock_bar(state, POLICY.lock, LockBar(
            seq=seq, side="long", entry_px=100.0, qty_coin=1.0, ts=row["ts"],
            opened_ts=shadow.opened_bar_ts, open_px=row["o"], high_px=row["h"],
            low_px=row["l"], cost_bps=10.0, atr_bps=shadow.atr_bps, close_px=None))
        state = res.state
        assert res.verdict is None


def test_l13_duplicate_and_out_of_order_bars_are_skipped():
    one = _run(_shadow(), [_bar(1, 100.0, 100.1, 99.9, 100.0)])
    assert advance_shadow(one, _bar(1, 100.0, 100.1, 99.9, 100.0), POLICY) is one
    assert advance_shadow(one, _bar(0, 100.0, 100.1, 99.9, 100.0), POLICY) is one


def test_l14_gap_voids_instead_of_producing_an_optimistic_sample():
    one = _run(_shadow(), [_bar(1, 100.0, 100.1, 99.9, 100.0)])
    # 第 2 根缺失（其间止损可能已被穿过）；第 3 根是一根大阳线。
    out = advance_shadow(one, _bar(3, 101.0, 103.0, 100.9, 102.9), POLICY)
    assert out.status == "void" and out.void_reason == "gap" and out.net_bps is None


def test_l16_replay_is_idempotent():
    rows = [_bar(k, 100.0 + 0.01 * k, 100.1 + 0.01 * k, 99.9, 100.0) for k in range(1, 11)]
    once = replay_shadow(_shadow(), rows, POLICY)
    half = _run(_shadow(), rows[:5])
    assert replay_shadow(half, rows, POLICY) == once
    assert replay_shadow(once, rows, POLICY) == once
    assert _run(_shadow(), rows) == once


def test_l17_intrabar_relock_after_arming_is_conservative():
    """IMP-1：锁已武装后，新高抬起的锁线被同根不利极值越过 ⇒ 按新锁线（或开盘价）成交。"""
    rows = [_bar(1, 100.0, 100.5, 99.95, 100.45), _bar(2, 100.45, 102.6, 100.4, 102.5)]
    out = _run(_shadow(), rows)
    assert out.exit_kind == "lock" and out.exit_px == pytest.approx(100.45)
    # 武装那一根不做这一步：冲高之前的低点不构成回撤。
    first = _run(_shadow(), rows[:1])
    assert first.status == "open"
