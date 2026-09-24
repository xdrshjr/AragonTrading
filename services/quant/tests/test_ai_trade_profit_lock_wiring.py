"""收益锁与下单前复检的接线（ai-trade-profit-lock-and-entry-unblock，spec §7.3）。

会话、API 解析、冷续跑、统计四条通路各自至少一条「照最自然的写法实现就会红」的断言。
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.agent.llm.base import StreamEvent
from app.ai_auto.decider import AiTradeDecider, TradeDecision
from app.ai_auto.hold_horizon import HorizonRule
from app.ai_auto.profit_guard import ProfitGuardRule
from app.ai_auto.profit_lock import ProfitLockRule, lock_rule_wire
from app.ai_auto.session import AiTradeSession
from app.api.ai_auto import _resolve_profit_lock
from app.config import Settings
from app.db.ai_auto_repositories import LOCK_REENTRY_WINDOW_S, _green_to_red_keys, _Trip
from app.db.session import dispose_engine, init_db
from app.engine.simulator import RiskParams
from app.live_auto.executor import LiveExecutor
from app.okx.accounts import AccountCredentials
from app.paper.strategy_factory import PaperStrategySpec
from app.schemas import AiTradeGroupRow, AiTradeGroupStartIn
from app.strategies.base import BaseStrategy, StrategyMeta

_REPO = Path(__file__).resolve().parents[3]
_ENTRY = 0.07245
_QTY = 100.0
_LOCK = ProfitLockRule(min_hold_s=0.0, confirm_s=0.0)


class _StubStrategy(BaseStrategy):
    meta = StrategyMeta(key="_stub_profit_lock", name="Stub", description="t",
                        default_params={}, params_schema={})

    def warmup(self) -> int:
        return 1

    def on_candle(self, df, i, ctx):
        return []


class _HoldLLM:
    async def stream(self, *, system, messages, tools):
        if tools:
            yield StreamEvent("tool_use_start", id="t", name="submit_trade_decision")
            yield StreamEvent("tool_use_args_delta", id="t", partial_json=json.dumps(
                {"action": "hold", "reason": "r", "confidence": 0.7}))
            yield StreamEvent("message_done", stop_reason="tool_use")
        else:
            yield StreamEvent("text_delta", text="摘要")
            yield StreamEvent("message_done", stop_reason="end")


async def _offline(*_a, **_k):
    return None


def _make_session(*, profit_rule=None, profit_lock_rule=_LOCK, horizon_rule=None):
    settings = Settings(ai_trade_funding_enabled=False)

    async def place_order_fn(order):
        return {"ok": True, "ordId": "x", "clOrdId": "aragonx"}

    async def close_position_fn(close):
        return {"ok": True, "clOrdId": "aragoncx"}

    async def positions_fn():
        ex = session.executor
        return [] if ex.projected == "flat" else [
            {"instId": "DOGE-USDT-SWAP", "pos": ex.entry_qty_sz, "upl": 0.0}]

    executor = LiveExecutor(
        creds=AccountCredentials("ak", "sk-000000", "pp-000000"), account_id=1,
        inst_id="DOGE-USDT-SWAP", td_mode="cross", pos_mode="long_short_mode",
        leverage=5.0, budget_usd=1000.0, risk=RiskParams(), ct_val=1.0,
        lot_sz=1.0, min_sz=1.0, settings=settings,
        place_order_fn=place_order_fn, close_position_fn=close_position_fn,
        positions_fn=positions_fn)
    decider = AiTradeDecider(_HoldLLM(), "sys", context_refresh_rounds=10, settings=settings)
    spec = PaperStrategySpec(1, "S", "atomic", _StubStrategy({}), {})
    session = AiTradeSession("aitrade_lock", 1, [spec], "DOGE-USDT-SWAP", "1H", executor,
                             decider, priming=False, profit_rule=profit_rule,
                             profit_lock_rule=profit_lock_rule, horizon_rule=horizon_rule)
    session._cost._fetch_fee_rate = _offline
    session._cost._fetch_position_history = _offline
    session._cost.refresh_funding = _offline
    return session


def _open_long(session, entry: float = _ENTRY) -> None:
    ex = session.executor
    ex.projected, ex.entry_price, ex.entry_qty_sz, ex.entry_bar = "long", entry, _QTY, 0
    ex.entry_position_pct = 0.5
    session._last_live_px = entry
    session._last_px_wall = time.time()


def _bps_px(bps: float) -> float:
    return _ENTRY * (1 + bps / 1e4)


async def _drive_to_lock_exit(session) -> list[dict]:
    """峰值 +60 bps ⇒ 利润锁锁 30 bps；价格回落到 +20 bps ⇒ 触发。"""
    q = session.subscribe()
    _open_long(session)
    await session._maybe_profit_exit(1)          # 建 epoch（peak = entry）
    session._last_live_px = _bps_px(60)
    await session._maybe_profit_exit(2)          # 推峰值、武装
    session._last_live_px = _bps_px(20)
    await session._maybe_profit_exit(3)
    out = []
    while not q.empty():
        out.append(q.get_nowait())
    return out


def test_default_start_arms_lock():
    """W-T1：请求不带 profitLock ⇒ 解析出规则；manager 两处回执都挂了 profitLock。"""
    rule = _resolve_profit_lock(None, Settings())
    assert rule is not None and lock_rule_wire(rule)["keepRatio"] == 0.5
    source = (_REPO / "services/quant/app/ai_auto/manager.py").read_text(encoding="utf-8")
    assert len(re.findall(r'"profitLock": lock_rule_wire\(', source)) == 2


def test_explicit_disable():
    """W-T2。"""
    assert _resolve_profit_lock(_as_in({"enabled": False}), Settings()) is None


def test_ops_switch_off_forces_none():
    """W-T3。"""
    from app.schemas import AiTradeProfitLockIn

    off = Settings(ai_trade_profit_lock_enabled=False)
    assert _resolve_profit_lock(AiTradeProfitLockIn(enabled=True), off) is None


def test_invalid_trail_mult_is_422():
    from fastapi import HTTPException

    from app.schemas import AiTradeProfitLockIn

    with pytest.raises(HTTPException) as exc:
        _resolve_profit_lock(AiTradeProfitLockIn(armCostMult=5.0, trailCostMult=3.0), Settings())
    assert exc.value.status_code == 422
    assert "trailCostMult" in exc.value.detail["message"]


async def test_lock_arms_without_profit_guard():
    """W-T4 + W-T5：保护层未装配也能追踪峰值并锁平；平仓走唯一的 close 路径、explain 归保护层。"""
    await init_db()
    session = _make_session()
    try:
        events = await _drive_to_lock_exit(session)
        trades = [e["data"] for e in events if e["type"] == "trade"]
        assert [t["side"] for t in trades] == ["close"]
        assert trades[0]["reason"] == "profit_lock_trailing"
        explain = trades[0]["explain"]
        assert explain["kind"] == "profit_guard"
        assert explain["profitLock"]["lockStage"] == 2
        assert {"lockPx", "costBps"} <= set(explain["profitLock"])
        assert trades[0]["pnl"] > 0
        assert session._lock.exits == 1 and session._lock.armed_epochs == 1
        status = [e["data"] for e in events if e["type"] == "status"][-1]
        assert status["profitLock"] == {"exits": 1, "armedEpochs": 1}
    finally:
        await dispose_engine()


async def test_guard_exit_trail_unchanged():
    """W-T5b：保护层平仓的 explain 与改前逐键相等（无 profitLock 键）。"""
    await init_db()
    session = _make_session(profit_rule=ProfitGuardRule(min_hold_s=0), profit_lock_rule=None)
    try:
        q = session.subscribe()
        _open_long(session)
        await session._maybe_profit_exit(1)
        session._last_live_px = _ENTRY * (1 + 0.10 / 5)
        await session._maybe_profit_exit(2)
        session._last_live_px = _ENTRY * (1 + 0.06 / 5)
        await session._maybe_profit_exit(3)
        trades = []
        while not q.empty():
            event = q.get_nowait()
            if event["type"] == "trade":
                trades.append(event["data"])
        assert trades and trades[0]["reason"].startswith("profit_guard_")
        assert "profitLock" not in trades[0]["explain"]
    finally:
        await dispose_engine()


async def test_lock_evaluated_when_guard_unavailable():
    """W-T5c：保护层快照 unavailable（判定价超龄）⇒ 收益锁仍被评估，并清掉越线起点。"""
    await init_db()
    session = _make_session(profit_rule=ProfitGuardRule(min_hold_s=0),
                            profit_lock_rule=ProfitLockRule(min_hold_s=0, confirm_s=8))
    try:
        _open_long(session)
        await session._maybe_profit_exit(1)
        session._last_live_px = _bps_px(60)
        await session._maybe_profit_exit(2)
        session._last_live_px = _bps_px(20)
        await session._maybe_profit_exit(3)             # 首次越线：只记起点
        assert session._lock.state.breach_since_wall is not None
        session._last_px_wall = time.time() - 10_000    # 判定价超龄 ⇒ 保护层 unavailable
        await session._maybe_profit_exit(4)
        assert session._lock.state.breach_since_wall is None
        assert session._lock.snapshot()["status"] == "unavailable"
        assert session.executor.projected == "long"
    finally:
        await dispose_engine()


async def test_horizon_does_not_block_lock_exit():
    """W-T6：最短持仓 120 分钟之内，收益锁照样能平。"""
    await init_db()
    rule = HorizonRule(profile_id="custom", title="锁", min_hold_s=7200.0,
                       exit_confirm_rounds=2, hysteresis_k=1.5, target_hold_min_s=14400.0,
                       target_hold_max_s=43200.0, confirm_window_s=3600.0, expected_hold_bars=12)
    session = _make_session(horizon_rule=rule)
    try:
        await _drive_to_lock_exit(session)
        assert session.executor.projected == "flat"
    finally:
        await dispose_engine()


async def test_guard_and_lock_same_tick_close_once():
    """W-T7：保护层本 tick 已平 ⇒ 收益锁不再判定，只成交一次。"""
    await init_db()
    session = _make_session(profit_rule=ProfitGuardRule(min_hold_s=0))
    try:
        q = session.subscribe()
        _open_long(session)
        await session._maybe_profit_exit(1)
        session._last_live_px = _ENTRY * (1 + 0.10 / 5)   # 200 bps：两层都武装
        await session._maybe_profit_exit(2)
        session._last_live_px = _ENTRY * (1 + 0.02 / 5)   # 两层都越线
        await session._maybe_profit_exit(3)
        closes = []
        while not q.empty():
            event = q.get_nowait()
            if event["type"] == "trade" and event["data"]["side"] == "close":
                closes.append(event["data"])
        assert len(closes) == 1 and closes[0]["reason"].startswith("profit_guard_")
        assert session._lock.exits == 0
    finally:
        await dispose_engine()


def _rebuild_wire(start_wire: dict | None) -> dict:
    """与 `resume._rebuild_request` 同一行补键规则（源码断言锁住它们一致）。"""
    return {"profitLock": {"enabled": False}, **(start_wire or {})}


def test_resume_old_run_without_key_stays_unarmed():
    """W-T8：老 wire 缺键 ⇒ 续跑后规则为 None。"""
    source = (_REPO / "services/quant/app/ai_auto/resume.py").read_text(encoding="utf-8")
    assert 'wire = {"profitLock": {"enabled": False}, **(row["startWire"] or {})}' in source
    wire = _rebuild_wire({"bar": "1H"})
    assert _resolve_profit_lock(_as_in(wire["profitLock"]), Settings()) is None
    assert "profitLock" in AiTradeGroupStartIn.model_fields


def _as_in(raw):
    from app.schemas import AiTradeProfitLockIn

    return raw if isinstance(raw, AiTradeProfitLockIn) else AiTradeProfitLockIn(**raw)


def test_resume_new_run_restores_rule():
    """W-T9：新 wire 自带键（含 null）⇒ 按原样解析。"""
    for raw in (None, {"enabled": True, "keepRatio": 0.6}):
        wire = _rebuild_wire({"profitLock": raw})
        rule = _resolve_profit_lock(None if wire["profitLock"] is None
                                    else _as_in(wire["profitLock"]), Settings())
        assert rule is not None
    assert _resolve_profit_lock(_as_in({"keepRatio": 0.6}), Settings()).keep_ratio == 0.6


def test_old_client_new_start_without_key_arms():
    """W-T9b：非续跑的新启动缺键 ⇒ 按默认开启（不能用 model_fields_set 判 legacy）。"""
    assert _resolve_profit_lock(None, Settings()) is not None


def test_group_row_declares_profit_lock():
    """W-T10：response_model 未声明的键会被静默剔除。"""
    assert "profitLock" in AiTradeGroupRow.model_fields


def _trade(**kw):
    base = {"side": "long", "ts": 0, "reason": "", "peak_pnl": None, "pnl": 0.0, "run_id": 1}
    return SimpleNamespace(**{**base, **kw})


def _trip(entry_ts, exit_ts, *, side="long", pnl=0.0, peak=None, reason="agent_close",
          run_id=1):
    return _Trip(entry=_trade(side=side, ts=entry_ts, run_id=run_id),
                 exit=_trade(side="close", ts=exit_ts, pnl=pnl, peak_pnl=peak, reason=reason,
                             run_id=run_id),
                 leg_fee_usd=0.0, cost_basis="net")


def test_summarize_pairs_green_to_red_keys():
    """W-T11：曾浮盈却净亏 ⇒ 计入盈转亏，回吐 = Σ(peak − gross)。"""
    keys = _green_to_red_keys([
        _trip(0, 10, pnl=-1.0, peak=2.0),
        _trip(20, 30, pnl=1.5, peak=2.0),
        _trip(40, 50, pnl=-0.5, peak=None),
        _trip(60, 70, pnl=0.2, peak=1.0, reason="profit_lock_breakeven"),
    ])
    assert keys["greenToRedCount"] == 1
    assert keys["greenToRedGivebackUsd"] == pytest.approx(3.0)
    assert keys["lockExitCount"] == 1 and keys["lockReentryCount"] == 0


def test_lock_reentry_count():
    """W-T14：锁平后 1800s 内同向重开 ⇒ 1；反向或超窗或跨 run ⇒ 0。"""
    lock = _trip(0, 100, pnl=0.1, peak=1.0, reason="profit_lock_trailing")
    same = _trip(100 + LOCK_REENTRY_WINDOW_S, 3000)
    assert _green_to_red_keys([lock, same])["lockReentryCount"] == 1
    assert _green_to_red_keys([lock, _trip(200, 300, side="short")])["lockReentryCount"] == 0
    late = _trip(101 + LOCK_REENTRY_WINDOW_S, 3000)
    assert _green_to_red_keys([lock, late])["lockReentryCount"] == 0
    other_run = _trip(200, 300, run_id=2)
    assert _green_to_red_keys([lock, other_run])["lockReentryCount"] == 0
    front = (_REPO / "apps/web/lib/aiTradeCapture.ts").read_text(encoding="utf-8")
    assert re.search(rf"LOCK_REENTRY_WINDOW_S\s*=\s*{LOCK_REENTRY_WINDOW_S}\b", front)


def test_lock_status_text_has_no_markdown_stars():
    """W-T12：后端推送的收益锁 status message 不含 `**`（该缺陷已复发 4 次）。"""
    from app.ai_auto.profit_lock import build_verdict, LockFacts, LockState
    from app.ai_auto.profit_lock_runtime import describe_lock_exit

    facts = LockFacts(side="long", entry_px=1.0, peak_px=1.01, mark_px=1.005, opened_wall=0,
                      now_wall=1, cost_bps=10.0, qty_coin=1.0)
    verdict = build_verdict(facts, LockState(("long", 1), lock_px=1.002, stage=1))
    for closed in (True, False):
        assert "**" not in describe_lock_exit(verdict, closed=closed)


async def test_identity_veto_carries_readings():
    """W-T13：持仓序变化 ⇒ 码不变、executed.preSubmitCheck 说清是身份变了；下一轮读数已复位。"""
    await init_db()
    session = _make_session(profit_lock_rule=None)
    try:
        session._round_execution_identity = session._execution_identity()
        session._position_seq += 1
        decision = TradeDecision("open_long", 0.5, 0.8, "r")
        executed = await session._execute_decision(decision, _ENTRY, 0, 1)
        assert executed["executionContextChanged"] == "pre_submit_context_changed"
        assert executed["preSubmitCheck"] == {"kind": "identity", "changed": ["positionSeq"]}
        session._round_execution_identity = session._execution_identity()
        session._prepare_open_context(decision, _ENTRY)
        assert session._pre_submit.last is None
    finally:
        await dispose_engine()


def test_prepare_open_context_single_call_site():
    """R-6：`_prepare_open_context` 改签名后全仓只有一个调用点。"""
    source = (_REPO / "services/quant/app/ai_auto/session.py").read_text(encoding="utf-8")
    assert len(re.findall(r"self\._prepare_open_context\(", source)) == 1
