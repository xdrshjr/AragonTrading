"""成本锚定收益锁：纯核心 / 运行时 / 回测（ai-trade-profit-lock-and-entry-unblock 轴 B，spec §7.2）。

B-T5b 是评审 RV-P0-3 的复现用例：v1 的「连续 N 次越线」数的是调用次数，且 stale 间隙不清越线
起点——断连前一次越线加上恢复后一次越线就会立即平仓。
"""
from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

import pandas as pd
import pytest

from app.ai_auto.backtest import AiBacktestRunner
from app.ai_auto.backtest_manager import AiBacktestManager, AiBacktestStartRequest
from app.ai_auto.exit_attribution import classify_backtest
from app.ai_auto.profit_guard import PositionFacts, ProfitGuardRule, ProfitGuardState, build_snapshot
from app.ai_auto.profit_lock import (
    MIN_GAP_BPS,
    REASON_LOCK_BREAKEVEN,
    REASON_LOCK_TRAILING,
    LockFacts,
    LockState,
    ProfitLockRule,
    derive_lock,
    evaluate_lock,
    resolve_profit_lock_rule,
)
from app.ai_auto.profit_lock_bar import LockBar, evaluate_lock_bar
from app.ai_auto.profit_lock_runtime import ProfitLockRuntime
from app.config import Settings
from app.engine.indicators import atr

_RULE = ProfitLockRule()
_EPOCH = ("long", 1)


def _facts(peak: float, mark: float, *, side: str = "long", now: float = 1_000.0,
           cost: float | None = 10.0, atr_bps: float | None = 40.0,
           opened: float = 0.0) -> LockFacts:
    return LockFacts(side=side, entry_px=100.0, peak_px=peak, mark_px=mark, opened_wall=opened,
                     now_wall=now, cost_bps=cost, atr_bps=atr_bps, qty_coin=1.0)


def _bps(px: float, side: str = "long") -> float:
    return (px - 100.0) / 100.0 * 1e4 * (1 if side == "long" else -1)


def test_stage1_arms_at_cost_multiple():
    """B-T1：cost=10、ATR=40 ⇒ arm=max(15,14)=15；14.9 不武装、15 武装；锁线 = entry×(1+12/1e4)。"""
    below = derive_lock(_RULE, _facts(100.149, 100.149), LockState(_EPOCH))
    assert below.stage == 0 and below.lock_px is None
    armed = derive_lock(_RULE, _facts(100.15, 100.15), LockState(_EPOCH))
    assert armed.stage == 1
    assert armed.lock_px == pytest.approx(100.0 * (1 + 12 / 1e4))


@pytest.mark.parametrize("cost", [2.0, 5.0, 10.0, 20.0, 40.0])
@pytest.mark.parametrize("buffer", [0.0, 2.0, 5.0, 10.0])
def test_arm_never_places_mark_below_line(cost, buffer):
    """B-T2：武装瞬间 mark − lock ≥ MIN_GAP_BPS。"""
    rule = replace(_RULE, buffer_bps=buffer, arm_atr_mult=0.0)
    for step in range(0, 4000):
        peak = 100.0 + step * 0.001
        state = derive_lock(rule, _facts(peak, peak, cost=cost, atr_bps=None), LockState(_EPOCH))
        if state.stage >= 1:
            assert _bps(peak) - _bps(state.lock_px) >= MIN_GAP_BPS - 1e-6
            return
    pytest.fail("从未武装")


def test_stage2_keeps_half_of_mfe_monotonic():
    """B-T3：峰值 60 bps ⇒ 锁 30；回落后峰值不变、锁线不降。"""
    state = derive_lock(_RULE, _facts(100.6, 100.6), LockState(_EPOCH))
    assert state.stage == 2 and _bps(state.lock_px) == pytest.approx(30.0)
    later = derive_lock(_RULE, _facts(100.6, 100.4, cost=5.0), state)
    assert later.lock_px == pytest.approx(state.lock_px)


def test_short_side_symmetric():
    """B-T4。"""
    state = derive_lock(_RULE, _facts(99.4, 99.4, side="short"), LockState(("short", 1)))
    assert state.stage == 2 and _bps(state.lock_px, "short") == pytest.approx(30.0)
    state, verdict = evaluate_lock(
        replace(_RULE, confirm_s=0), _facts(99.4, 99.71, side="short"), state)
    assert verdict is not None and verdict.reason_code == REASON_LOCK_TRAILING


def test_confirm_requires_continuous_breach_by_wall_clock():
    """B-T5：越线→回线→越线不触发；同一秒两次评估不触发；持续 ≥ confirm_s 才触发。"""
    state = derive_lock(_RULE, _facts(100.2, 100.2), LockState(_EPOCH))
    breach, safe = 100.10, 100.18
    state, v = evaluate_lock(_RULE, _facts(100.2, breach, now=1000), state)
    assert v is None and state.breach_since_wall == 1000
    state, v = evaluate_lock(_RULE, _facts(100.2, breach, now=1000), state)
    assert v is None, "同一墙钟秒内的第二次调用不是「持续」"
    state, v = evaluate_lock(_RULE, _facts(100.2, safe, now=1004), state)
    assert v is None and state.breach_since_wall is None
    state, v = evaluate_lock(_RULE, _facts(100.2, breach, now=1006), state)
    assert v is None
    state, v = evaluate_lock(_RULE, _facts(100.2, breach, now=1013), state)
    assert v is None
    state, v = evaluate_lock(_RULE, _facts(100.2, breach, now=1014), state)
    assert v is not None and v.reason_code == REASON_LOCK_BREAKEVEN


def test_stale_gap_resets_breach():
    """B-T5b：越线 → stale 3 次 → 恢复后越线一次 ⇒ 不触发。"""
    runtime = ProfitLockRuntime(_RULE)
    assert runtime.tick(_facts(100.2, 100.2, now=900), epoch=_EPOCH, stale=False) is None
    assert runtime.tick(_facts(100.2, 100.10, now=1000), epoch=_EPOCH, stale=False) is None
    for now in (1005, 1010, 1015):
        assert runtime.tick(_facts(100.2, 100.10, now=now), epoch=_EPOCH, stale=True) is None
    assert runtime.snapshot()["status"] == "unavailable"
    assert runtime.tick(_facts(100.2, 100.10, now=1020), epoch=_EPOCH, stale=False) is None
    assert runtime.tick(_facts(100.2, 100.10, now=1028), epoch=_EPOCH, stale=False) is not None


def test_min_hold_blocks_trigger_but_tracks_peak():
    """B-T6。"""
    state, v = evaluate_lock(_RULE, _facts(100.2, 100.05, now=30), LockState(_EPOCH))
    assert v is None and state.stage == 1 and state.breach_since_wall is None


def test_stale_price_fails_open():
    """B-T7。"""
    runtime = ProfitLockRuntime(replace(_RULE, confirm_s=0))
    runtime.tick(_facts(100.2, 100.2, now=900), epoch=_EPOCH, stale=False)
    assert runtime.tick(_facts(100.2, 99.0, now=1000), epoch=_EPOCH, stale=True) is None
    assert runtime.snapshot()["unavailableReason"] == "stale_px"


def test_cost_unknown_fails_open():
    """B-T8。"""
    runtime = ProfitLockRuntime(replace(_RULE, confirm_s=0))
    assert runtime.tick(_facts(100.2, 99.0, cost=None), epoch=_EPOCH, stale=False) is None
    assert runtime.snapshot()["unavailableReason"] == "cost_unknown"


def test_reverse_resets_epoch():
    """B-T9：long→short 原子反手，旧锁线不得带入新 epoch。"""
    runtime = ProfitLockRuntime(replace(_RULE, confirm_s=0))
    runtime.tick(_facts(100.6, 100.6, now=900), epoch=_EPOCH, stale=False)
    assert runtime.state.lock_px is not None
    verdict = runtime.tick(_facts(100.0, 100.3, side="short", now=1000),
                           epoch=("short", 2), stale=False)
    assert verdict is None and runtime.state.lock_px is None and runtime.state.stage == 0


# run 1640 第 1 笔往返（生产库 candles_cache，BTC-USDT-SWAP 30m）：真实 Agent 在 +83 bps 的峰值后
# 以 −0.25 USDT 离场。前 30 根用于 ATR，入场根 ts=1788931800、入场价 79088.0。
_RUN_1640_BARS = [
    (1788877800, 78310.0, 78598.3, 78205.6, 78525.9), (1788879600, 78525.9, 78679.8, 78419.0, 78420.2),
    (1788881400, 78420.2, 78873.1, 78412.7, 78866.0), (1788883200, 78865.9, 78883.3, 78535.3, 78598.7),
    (1788885000, 78598.7, 78940.0, 78584.5, 78698.0), (1788886800, 78698.0, 78860.3, 78502.4, 78558.1),
    (1788888600, 78558.0, 78625.3, 78460.0, 78465.8), (1788890400, 78465.7, 78782.1, 78456.2, 78697.7),
    (1788892200, 78697.6, 78743.9, 78232.0, 78407.4), (1788894000, 78407.4, 78493.9, 78300.6, 78442.3),
    (1788895800, 78442.3, 78485.4, 78280.0, 78410.1), (1788897600, 78410.0, 78585.4, 78371.4, 78553.5),
    (1788899400, 78553.6, 78553.6, 78381.4, 78450.1), (1788901200, 78450.0, 78582.7, 78432.2, 78496.2),
    (1788903000, 78496.3, 78534.4, 78370.0, 78459.9), (1788904800, 78459.8, 78646.8, 78420.0, 78468.7),
    (1788906600, 78468.7, 78545.6, 78434.8, 78526.2), (1788908400, 78526.3, 78534.4, 78397.8, 78435.8),
    (1788910200, 78435.9, 78437.0, 78368.5, 78420.7), (1788912000, 78420.8, 78711.0, 78413.2, 78588.8),
    (1788913800, 78588.8, 78761.3, 78533.4, 78740.4), (1788915600, 78740.4, 78834.2, 78595.5, 78700.0),
    (1788917400, 78700.0, 78851.0, 78659.1, 78851.0), (1788919200, 78851.0, 78888.0, 78647.1, 78784.1),
    (1788921000, 78784.0, 78807.8, 78686.7, 78686.7), (1788922800, 78686.7, 78686.8, 78514.4, 78541.6),
    (1788924600, 78541.6, 78650.0, 78534.5, 78593.7), (1788926400, 78593.8, 78919.3, 78593.7, 78854.0),
    (1788928200, 78854.0, 79199.0, 78835.1, 79136.0), (1788930000, 79136.1, 79377.2, 79020.8, 79091.2),
    (1788931800, 79091.3, 79102.7, 78805.0, 78907.0), (1788933600, 78907.0, 79275.0, 78825.0, 79126.0),
    (1788935400, 79126.1, 79210.0, 79083.2, 79126.8), (1788937200, 79126.9, 79363.0, 79118.8, 79246.6),
    (1788939000, 79246.6, 79263.0, 79136.0, 79247.4), (1788940800, 79247.5, 79688.0, 79220.8, 79655.6),
    (1788942600, 79655.5, 79744.4, 79551.3, 79665.1), (1788944400, 79665.0, 79680.0, 79376.4, 79387.8),
    (1788946200, 79387.9, 79412.9, 78860.0, 78920.0),
]


def test_green_to_red_replay():
    """B-T10：回测保守成交序下，真实的盈转亏往返改为以 ≥ 0 的净额离场。"""
    df = pd.DataFrame(_RUN_1640_BARS, columns=["ts", "o", "h", "l", "c"])
    atr_series = atr(df, 14)
    entry, cost, first = 79088.0, 10.4, 30
    state, exit_px = None, None
    for i in range(first, len(df)):
        row = df.iloc[i]
        result = evaluate_lock_bar(state, _RULE, LockBar(
            seq=first, side="long", entry_px=entry, qty_coin=0.31, ts=int(row.ts),
            opened_ts=int(df.ts[first]), open_px=row.o, high_px=row.h, low_px=row.l,
            close_px=row.c, cost_bps=cost, atr_bps=float(atr_series.iloc[i - 1]) / entry * 1e4))
        state = result.state
        if result.verdict is not None:
            exit_px = result.fill_px
            break
    assert exit_px is not None, "收益锁从未触发"
    assert (exit_px - entry) / entry * 1e4 - cost >= 0


def test_breakeven_offset_never_below_cost():
    """B-T11（B-4）：5× 杠杆、cost=10 ⇒ 保本线净额 ≥ +2 bps；未给成本下界 ⇒ 与旧值逐位相等。"""
    rule = ProfitGuardRule(arm_return_pct=0.05, giveback_pct=0.4, breakeven_arm_return_pct=0.03,
                           breakeven_offset_return_pct=0.002, min_hold_s=0, max_px_age_s=90)
    state = ProfitGuardState(seq=1, side="long", opened_wall=0.0, peak_px=100.7)
    base = PositionFacts(seq=1, side="long", entry_px=100.0, leverage=5.0, qty_coin=1.0,
                         mark_px=100.6, now_wall=100.0)
    legacy = build_snapshot(rule, state, base)["breakevenPx"]
    assert legacy == pytest.approx(100.0 * (1 + 0.002 / 5))
    floored = build_snapshot(rule, state, replace(base, cost_floor_bps=10.0))["breakevenPx"]
    assert _bps(floored) - 10.0 >= 2.0 - 1e-6


def test_floored_breakeven_waits_until_peak_clears_the_line():
    """终审补（B-4）：高杠杆下保证金口径的武装点（20× 下 3% ≈ 15 bps）可能低于「成本 + 2」。

    若照常武装，抬高后的保本线落在峰值**上方**，mark 恒已越线 ⇒ 武装即市价平仓，把一笔正在
    走强的仓位在武装瞬间扫掉。与收益锁的 MIN_GAP 同一不变式：峰值须高出保本线 ≥ 3 bps 才武装。
    """
    rule = ProfitGuardRule(arm_return_pct=0.05, giveback_pct=0.4, breakeven_arm_return_pct=0.03,
                           breakeven_offset_return_pct=0.002, min_hold_s=0, max_px_age_s=90)
    state = ProfitGuardState(seq=1, side="long", opened_wall=0.0, peak_px=100.16)
    base = PositionFacts(seq=1, side="long", entry_px=100.0, leverage=20.0, qty_coin=1.0,
                         mark_px=100.16, now_wall=100.0, cost_floor_bps=20.0)
    early = build_snapshot(rule, state, base)
    assert early["breakevenPx"] is None and early["status"] != "breakeven_armed"
    later = build_snapshot(rule, replace(state, peak_px=100.3), replace(base, mark_px=100.3))
    assert later["breakevenPx"] is not None
    assert _bps(later["breakevenPx"]) == pytest.approx(22.0)


class _FakeBroker:
    def __init__(self, side: str = "long"):
        self.pos = SimpleNamespace(side=side, entry_bar=0, entry_price=100.0, qty=1.0)
        self.closes: list[tuple] = []

    def close_at_price(self, px, ts, reason):
        self.closes.append((px, ts, reason))
        self.pos = SimpleNamespace(side="flat", entry_bar=-1, entry_price=0.0, qty=0.0)


def _lock_stub(rule=_RULE):
    return SimpleNamespace(
        profit_lock_rule=rule, main=_FakeBroker(), _lock_bar_state=None,
        _pending_exit_source=None, _atr_at=lambda i: None,
        _bar_ts=lambda index, default=0: 0, _position_cost_bps=lambda side, ts: 10.0)


def _row(ts, o, h, low):
    return pd.Series({"ts": ts, "o": o, "h": h, "l": low, "c": o})


def test_backtest_bar_conservative_order():
    """B-T12：同一根内先触及锁线再创新高 ⇒ 按锁线离场，不按新高重算。"""
    # 第 1 根是开仓根（持有 0s < min_hold），故收盘复核也不会在这一根上提前成交。
    stub = _lock_stub()
    AiBacktestRunner._apply_profit_lock_bar(stub, 0, _row(0, 100.0, 100.2, 99.9))
    AiBacktestRunner._apply_profit_lock_bar(stub, 1, _row(1800, 100.2, 100.6, 100.05))
    assert stub.main.closes, "越线未成交"
    px, _, reason = stub.main.closes[0]
    assert _bps(px) == pytest.approx(12.0) and reason == REASON_LOCK_BREAKEVEN


def test_backtest_close_below_new_lock_fills_at_line():
    """第 3 步收盘复核：武装根内冲高回落、收盘已在锁线下方 ⇒ 按锁线成交，不拖到下一根开盘。"""
    bar = dict(seq=0, side="long", entry_px=100.0, qty_coin=1.0, opened_ts=0,
               cost_bps=10.0, atr_bps=None)
    state = evaluate_lock_bar(None, _RULE, LockBar(ts=0, open_px=100.0, high_px=100.01,
                                                   low_px=99.99, close_px=100.0, **bar)).state
    result = evaluate_lock_bar(state, _RULE, LockBar(ts=1800, open_px=100.0, high_px=100.2,
                                                     low_px=99.99, close_px=100.05, **bar))
    assert result.verdict is not None and _bps(result.fill_px) == pytest.approx(12.0)
    no_close = evaluate_lock_bar(state, _RULE, LockBar(ts=1800, open_px=100.0, high_px=100.2,
                                                       low_px=99.99, **bar))
    assert no_close.verdict is None, "close_px 缺省 ⇒ 逐字回退两步序"


def test_backtest_lock_exit_attributed_profit_guard():
    """B-T12b：回测锁平归入 profitGuard；同一根保护层已平 ⇒ 收益锁不再成交。"""
    stub = _lock_stub()
    AiBacktestRunner._apply_profit_lock_bar(stub, 0, _row(0, 100.0, 100.2, 99.9))
    AiBacktestRunner._apply_profit_lock_bar(stub, 1, _row(1800, 100.1, 100.1, 100.0))
    assert stub._pending_exit_source == "profitGuard"
    reason = stub.main.closes[0][2]
    assert classify_backtest(reason, stamped=stub._pending_exit_source) == "profitGuard"
    assert classify_backtest(reason, stamped=None) == "unknown"
    guarded = _lock_stub()
    AiBacktestRunner._apply_profit_lock_bar(guarded, 0, _row(0, 100.0, 100.2, 99.9))
    guarded.main.close_at_price(100.1, 1800, "profit_guard_breakeven")   # 保护层同根先成交
    AiBacktestRunner._apply_profit_lock_bar(guarded, 1, _row(1800, 100.1, 100.1, 100.0))
    assert len(guarded.main.closes) == 1 and guarded._lock_bar_state is None


def test_backtest_request_carries_lock_rule(monkeypatch):
    """B-T12c：回测请求带规则 ⇒ 引擎 spec 拿到它；回测总闸关 ⇒ None。"""
    from app.api.ai_auto import _resolve_backtest_profit_lock

    monkeypatch.setattr("app.ai_auto.backtest_manager.get_rest_client", lambda: object())
    settings = Settings()
    rule = _resolve_backtest_profit_lock(None, settings)
    assert isinstance(rule, ProfitLockRule)
    req = AiBacktestStartRequest(
        specs=[], inst_id="BTC-USDT-SWAP", bar="30m", start_ts=0, end_ts=1, settings=settings,
        context_refresh_rounds=10, decision_every_bars=1, initial_capital=1000.0, leverage=3.0,
        profit_lock_rule=rule)
    spec = AiBacktestManager()._build_spec(req, provider=object(), planned=1)
    assert spec.profit_lock_rule is rule
    off = Settings(ai_trade_profit_lock_backtest_enabled=False)
    assert _resolve_backtest_profit_lock(None, off) is None


def test_resolve_rule_defaults_and_validation():
    settings = Settings()
    assert resolve_profit_lock_rule(None, settings) == ProfitLockRule()
    assert resolve_profit_lock_rule({"enabled": False}, settings) is None
    assert resolve_profit_lock_rule(None, Settings(ai_trade_profit_lock_enabled=False)) is None
    with pytest.raises(ValueError, match="trailCostMult"):
        resolve_profit_lock_rule({"armCostMult": 4.0, "trailCostMult": 3.0}, settings)
