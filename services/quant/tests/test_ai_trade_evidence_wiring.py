"""实证准入的会话接线（ai-trade-evidence-gated-capital §7.3，W-1..W-16）。

真会话 + 假 LLM：用例直接驱动 `_run_decision_round` / `feed_candle`，行情与 OKX 全程桩驱动。
`_execute_decision` 用替身记录「最终到达执行层的是什么动作」——这正是本层唯一要保证的事。
"""
from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.agent.llm.base import StreamEvent
from app.ai_auto.decider import AiTradeDecider
from app.ai_auto.evidence_gate import evidence_rule_wire, rule_from_settings
from app.ai_auto.evidence_runtime import EvidenceRuntime, EvidenceSnapshot
from app.ai_auto.gate_veto import build_gate_veto_note
from app.ai_auto.decider import TradeDecision
from app.ai_auto.open_funnel import LAYERS, classify_block
from app.ai_auto.prompts import DECISION_TOOL_NAME, REFLECTION_TOOL_NAME
from app.ai_auto.session import AiTradeSession
from app.api.ai_auto import _resolve_evidence_gate
from app.config import Settings
from app.db.session import dispose_engine, init_db
from app.engine.simulator import RiskParams
from app.live_auto.executor import LiveExecutor
from app.okx.accounts import AccountCredentials
from app.okx.types import Candle
from app.paper.strategy_factory import PaperStrategySpec
from app.schemas import AiTradeEvidenceGateIn, AiTradeGroupRow, AiTradeGroupStartIn
from tests.test_ai_trade_session import (_no_funding_refresh, _no_position_history,
                                         _no_trade_fee, _StubStrategy)

_INST = "BTC-USDT-SWAP"
_BAR_S = 3600
_T0 = 1_700_000_000 // _BAR_S * _BAR_S
_WAKE = {"kind": "heartbeat", "trigger": "interval"}


def _script(name: str, args: dict) -> list[StreamEvent]:
    return [StreamEvent("tool_use_start", id=f"{name}-1", name=name),
            StreamEvent("tool_use_args_delta", id=f"{name}-1",
                        partial_json=json.dumps(args, ensure_ascii=False)),
            StreamEvent("tool_use_end", id=f"{name}-1"),
            StreamEvent("message_done", stop_reason="tool_use")]


class _LLM:
    """决策轮给出 `action`；反思轮计数（W-6 断言被本层拦下的轮不调反思）。"""

    def __init__(self, action: str = "open_long", confidence: float = 0.8):
        self.action, self.confidence = action, confidence
        self.reflections = 0

    async def stream(self, *, system, messages, tools):
        names = {tool.name for tool in tools}
        if not names:
            yield StreamEvent("text_delta", text="摘要")
            yield StreamEvent("message_done", stop_reason="end_turn")
            return
        if REFLECTION_TOOL_NAME in names:
            self.reflections += 1
            for event in _script(REFLECTION_TOOL_NAME, {"verdict": "confirm", "reason": "ok"}):
                yield event
            return
        args = {"action": self.action, "reason": "r", "confidence": self.confidence}
        if self.action.startswith("open_"):
            args["sizeFraction"] = 0.5
        for event in _script(DECISION_TOOL_NAME, args):
            yield event


def _settings(**over) -> Settings:
    base = {"ai_trade_funding_enabled": False, "ai_trade_decision_max_retries": 0,
            "ai_trade_retry_base_ms": 0, "ai_trade_retry_max_ms": 0}
    return Settings(**{**base, **over})


class _Clock:
    def __init__(self, now: float):
        self.now = now

    def __call__(self) -> float:
        return self.now


def _session(llm, settings: Settings, *, snapshot=None, armed=True, reflection=False,
             clock=None, persist=None):
    async def place_order_fn(order):
        return {"ok": True, "ordId": "x", "clOrdId": "aragonx"}

    async def close_position_fn(close):
        return {"ok": True, "clOrdId": "aragoncx"}

    async def positions_fn():
        return []

    executor = LiveExecutor(
        creds=AccountCredentials("ak", "sk-000000", "pp-000000"), account_id=1,
        inst_id=_INST, td_mode="cross", pos_mode="long_short_mode", leverage=3.0,
        budget_usd=1000.0, risk=RiskParams(), ct_val=0.01, lot_sz=0.01, min_sz=0.01,
        settings=settings, place_order_fn=place_order_fn,
        close_position_fn=close_position_fn, positions_fn=positions_fn)
    decider = AiTradeDecider(llm, "sys", context_refresh_rounds=10, settings=settings,
                             reflection_enabled=reflection, reflection_llm=llm, summary_llm=llm)
    rule = rule_from_settings(settings) if armed else None
    session = AiTradeSession(
        "aitrade_evidence", 7, [PaperStrategySpec(1, "S", "atomic", _StubStrategy({}), {})],
        _INST, "1H", executor, decider, priming=False, priming_bars=10,
        context_refresh_rounds=10, evidence_rule=rule,
        evidence_snapshot=snapshot if snapshot is not None else EvidenceSnapshot())
    if clock is not None or persist is not None:
        session._evidence = EvidenceRuntime(
            rule, snapshot if snapshot is not None else EvidenceSnapshot(), inst_id=_INST,
            bar="1H", run_id=7, group_id=None, fallback_cost_bps=12.0,
            now=clock or time.time, persist=persist or _no_persist)
    else:
        session._evidence._persist = _no_persist
    session._cost._fetch_fee_rate = _no_trade_fee
    session._cost._fetch_position_history = _no_position_history
    session._cost.refresh_funding = _no_funding_refresh
    session._last_live_px = 30000.0
    session._atr_px = 300.0
    return session


async def _no_persist(batch, **_kw):
    return None


def _spy_execute(session):
    seen: list[str] = []

    async def execute(decision, ref_px, i, ts):
        seen.append(decision.action)
        return {"traded": decision.action.startswith("open_"), "action": decision.action}

    session._execute_decision = execute
    return seen


async def _round(session, *, ts=_T0):
    pushed: list[dict] = []

    async def capture(event):
        pushed.append(event)

    session._push = capture
    await session._run_decision_round([], 0, 30000.0, ts, _WAKE)
    return pushed


def _degrades(pushed) -> str:
    return json.dumps(pushed, ensure_ascii=False, default=str)


def _proven(side="long", n=12, value=20.0):
    now = time.time()
    return EvidenceSnapshot(samples={side: [(now - 3600 * i, value + (i % 2)) for i in range(n)]},
                            pooled={}, open_shadows=[], as_of=now)


# ---------------- W-1 / W-2 / W-3 / W-4 ----------------
async def test_w1_strict_empty_evidence_blocks_open_and_records_a_shadow():
    await init_db()
    try:
        session = _session(_LLM("open_long"), _settings())
        seen = _spy_execute(session)
        pushed = await _round(session)
    finally:
        await dispose_engine()
    assert seen == ["hold"], "strict + 无证据：开仓不得到达执行层"
    assert "evidence_unproven(" in _degrades(pushed)
    shadow = session._evidence._open_for("long")
    assert shadow is not None and shadow.status == "open"
    assert session._evidence.stats()["shadows"] == 1


async def test_w2_w3_repeats_dedupe_and_opposite_side_opens_new_shadow():
    await init_db()
    try:
        llm = _LLM("open_long")
        session = _session(llm, _settings())
        _spy_execute(session)
        for k in range(3):
            await _round(session, ts=_T0 + k)
        assert session._evidence._open_for("long").repeats == 2
        assert session._evidence.stats()["shadows"] == 1
        llm.action = "open_short"
        await _round(session, ts=_T0 + 9)
    finally:
        await dispose_engine()
    assert session._evidence._open_for("short") is not None
    assert session._evidence.stats()["shadows"] == 2


async def test_w4_low_confidence_round_creates_no_shadow():
    await init_db()
    try:
        session = _session(_LLM("open_long", confidence=0.01),
                           _settings(ai_trade_min_confidence=0.5))
        _spy_execute(session)
        await _round(session)
    finally:
        await dispose_engine()
    assert session._evidence.stats()["intents"] == 0
    assert session._evidence._open_for("long") is None


# ---------------- W-5 / W-6 ----------------
async def test_w5_proven_bucket_trades_and_marks_shadow_traded():
    await init_db()
    try:
        session = _session(_LLM("open_long"), _settings(), snapshot=_proven())
        seen = _spy_execute(session)
        await _round(session)
    finally:
        await dispose_engine()
    assert seen == ["open_long"]
    assert session._evidence._open_for("long").traded is True
    wire = session._status_payload()["evidence"]["sides"]["long"]
    assert wire["state"] == "proven" and wire["gating"] == "live"
    assert wire["sessionTraded"] == 1 and wire["sessionShadows"] == 1


async def test_w6_blocked_round_skips_reflection():
    await init_db()
    try:
        llm = _LLM("open_long")
        session = _session(llm, _settings(), reflection=True)
        _spy_execute(session)
        await _round(session)
        assert llm.reflections == 0, "被实证准入拦下的轮已是 hold，反思必须直通"
        proven = _session(llm, _settings(), snapshot=_proven(), reflection=True)
        _spy_execute(proven)
        await _round(proven)
    finally:
        await dispose_engine()
    assert llm.reflections == 1


# ---------------- W-7 / W-8：确认根推进（暂停期同样推进） ----------------
def _candle(ts: int, o: float, h: float, low: float, c: float) -> Candle:
    return Candle(ts=ts, o=o, h=h, l=low, c=c, vol=1.0, confirm=True)


async def test_w7_w8_confirmed_bar_advances_shadow_even_when_paused():
    await init_db()
    clock = _Clock(_T0 + 10)
    try:
        session = _session(_LLM("open_long"), _settings(), clock=clock)
        _spy_execute(session)
        await _round(session)
        assert session._evidence.status_wire()["sides"]["long"]["n"] == 0
        session.state = "paused"
        clock.now = _T0 + 2 * _BAR_S
        await session.feed_candle(_candle(_T0 + _BAR_S, 30000, 30010, 28000, 28100))
    finally:
        await dispose_engine()
    wire = session._evidence.status_wire()["sides"]["long"]
    assert wire["n"] == 1 and wire["openShadow"] is False
    assert session._evidence.stats()["matured"] == 1


# ---------------- W-9：总闸关 ⇒ 逐字节回退 ----------------
async def test_w9_master_switch_off_reverts_the_chain():
    await init_db()
    try:
        settings = _settings(ai_trade_evidence_gate_enabled=False)
        assert _resolve_evidence_gate(None, settings) is None
        session = _session(_LLM("open_long"), settings, armed=False)
        seen = _spy_execute(session)
        await _round(session)
        decision = TradeDecision("open_long", 0.5, 0.8, "x")
        out, degrade = session._apply_evidence_gate(decision, None)
    finally:
        await dispose_engine()
    assert "evidence" not in session._status_payload()
    assert seen == ["open_long"]
    assert out is decision and degrade is None


# ---------------- W-10 / W-11：回执不被吞、冷续跑老 wire 不装配 ----------------
async def test_w10_group_row_keeps_evidence_gate_through_fastapi():
    app = FastAPI()
    wire = evidence_rule_wire(rule_from_settings(Settings()))

    @app.get("/row", response_model=AiTradeGroupRow)
    async def row() -> dict:
        return {"groupId": "g", "bar": "1H", "strategyIds": [1], "strategyNames": ["S"],
                "totalBudgetUsd": 1.0, "leverage": 1.0, "contextRefreshRounds": 1,
                "createdTs": 1, "members": [], "evidenceGate": wire}

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
        body = (await client.get("/row")).json()
    assert body["evidenceGate"] == wire and body["evidenceGate"]["refPolicy"].startswith("ref-v1")


def test_w11_cold_resume_of_old_wire_stays_unarmed():
    source = (Path(__file__).resolve().parents[1] / "app/ai_auto/resume.py").read_text(
        encoding="utf-8")
    assert 'wire.setdefault("evidenceGate", {"enabled": False})' in source
    wire = {"profitLock": {"enabled": False}, **{"bar": "1H"}}
    wire.setdefault("evidenceGate", {"enabled": False})
    body = AiTradeGroupStartIn.model_validate({**wire, "accountId": 1, "strategyIds": [1],
                                               "members": [{"instId": _INST,
                                                            "allocationPct": 100.0}],
                                               "capitalUsd": 100.0})
    assert _resolve_evidence_gate(body.evidenceGate, Settings()) is None
    new = {"evidenceGate": None}
    new.setdefault("evidenceGate", {"enabled": False})
    assert new["evidenceGate"] is None, "新 wire 恒带该键（含 null）⇒ 不被覆盖"
    assert _resolve_evidence_gate(AiTradeEvidenceGateIn(policy="balanced"),
                                  Settings()).policy == "balanced"


# ---------------- W-12 / W-13：漏斗与否决注记 ----------------
def test_w12_funnel_layer_position_and_classification():
    assert classify_block("evidence_unproven(n=0/10,stats=unavailable)") == "evidenceGate"
    assert classify_block("evidence_disproven(n=12,mean=-6.2bps)") == "evidenceGate"
    assert LAYERS.index("edgeGate") < LAYERS.index("evidenceGate") < LAYERS.index("reflection")


def test_w13_gate_veto_note_names_the_layer():
    stage1 = TradeDecision("open_long", 0.5, 0.8, "model")
    note = build_gate_veto_note(stage1, "evidence_unproven(n=3/10,mean=+1.0bps)")
    assert note is not None and note["vetoedBy"] == "evidenceGate"


# ---------------- W-14 / W-16：写库失败与挂住都不拖垮 ----------------
async def test_w14_persist_failure_is_counted_not_raised():
    async def boom(batch, **_kw):
        raise RuntimeError("db down")

    await init_db()
    try:
        session = _session(_LLM("open_long"), _settings(), persist=boom)
        seen = _spy_execute(session)
        await _round(session)
    finally:
        await dispose_engine()
    assert seen == ["hold"]
    assert session._evidence.stats()["dropped"] >= 1


async def test_w16_hung_persist_is_bounded_on_stop_and_pause_paths():
    async def hang(batch, **_kw):
        await asyncio.sleep(3600)

    await init_db()
    try:
        session = _session(_LLM("open_long"), _settings(), persist=hang)
        session._evidence._persist = _no_persist
        _spy_execute(session)
        await _round(session)
        session._evidence._persist = hang
        session._evidence._dirty = {"x": session._evidence._open_for("long")}
        started = time.monotonic()
        await session._persist_evidence_bounded()
        elapsed = time.monotonic() - started
    finally:
        await dispose_engine()
    assert elapsed < 3.0
    assert session._evidence.stats()["dropped"] == 1


# ---------------- W-15：反手降级为 close（保留退出腿） ----------------
async def test_w15_reverse_is_downgraded_to_close_and_short_shadow_recorded():
    await init_db()
    try:
        session = _session(_LLM("open_long"), _settings())
        session.executor.projected = "short"
        seen = _spy_execute(session)
        pushed = await _round(session)
    finally:
        await dispose_engine()
    assert seen == ["close"], "持空时被拦的反手必须保留退出腿"
    assert "reverse→close" in _degrades(pushed)
    assert session._evidence._open_for("long") is not None


@pytest.mark.parametrize("policy", ["balanced", "observe"])
async def test_non_strict_policies_let_unproven_open_through(policy):
    await init_db()
    try:
        session = _session(_LLM("open_long"),
                           _settings(ai_trade_evidence_default_policy=policy))
        seen = _spy_execute(session)
        await _round(session)
    finally:
        await dispose_engine()
    assert seen == ["open_long"]
    assert session._evidence._open_for("long") is not None
