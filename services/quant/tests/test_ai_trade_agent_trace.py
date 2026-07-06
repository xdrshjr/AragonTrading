"""AI 自动交易「决策认知轨迹」单测（ai-auto-trading-agent-trace §九）。

覆盖：决策器 emit 合并 / 空转等价（T1/T2）、会话六 phase 装配（T3）、snapshot.agentTraces 防重复
回放与环形容量（T4/RV-1）、共识降级如实呈现 + 守卫跳过不发轨迹（T5/RV-2）、摘要 phase 与顶层一致
（T6）、轮异常兜底 error phase（T7）。行情 / OKX / LLM 全程桩驱动（直接注入 feed_candle）。
"""
from __future__ import annotations

import asyncio
import json

from app.agent.llm.base import StreamEvent
from app.ai_auto.decider import AiTradeDecider
from app.ai_auto.session import AiTradeSession
from app.ai_auto.trace import new_round_trace
from app.config import Settings
from app.db import repositories as repo
from app.db.session import dispose_engine, get_sessionmaker, init_db
from app.engine.simulator import RiskParams
from app.live_auto.executor import LiveExecutor
from app.okx.accounts import AccountCredentials
from app.okx.types import Candle
from app.paper.strategy_factory import PaperStrategySpec
from app.strategies.base import BaseStrategy, StrategyMeta

_START_TS = 1_700_000_000
_STEP = 3600


class _StubStrategy(BaseStrategy):
    meta = StrategyMeta(key="_stub_ai_trace", name="Stub", description="t",
                        default_params={}, params_schema={})

    def warmup(self) -> int:
        return 1

    def on_candle(self, df, i, ctx):
        return []


class _EmitCollector:
    """收集 decider 经 emit 扇出的合并轨迹片段（`{phase, delta|name}`）。"""

    def __init__(self):
        self.parts: list[dict] = []

    async def __call__(self, part: dict) -> None:
        self.parts.append(part)


class _ScriptedLLM:
    """按预置脚本逐次响应 stream（供决策器级 T1/T2）。"""

    def __init__(self, scripts: list[list[StreamEvent]]):
        self._scripts = list(scripts)

    async def stream(self, *, system, messages, tools):
        script = self._scripts.pop(0) if self._scripts else [
            StreamEvent("message_done", stop_reason="end")]
        for ev in script:
            yield ev


class _TraceLLM:
    """会话级桩：决策流先出 text_delta（分析）再出工具；摘要流出文本（供 T3~T7）。"""

    def __init__(self, action="open_long", size=0.5, analysis="综合研判：多数策略偏多",
                 summary="综合摘要：偏多"):
        self.action = action
        self.size = size
        self.analysis = analysis
        self.summary = summary

    async def stream(self, *, system, messages, tools):
        if tools:
            yield StreamEvent("text_delta", text=self.analysis)
            args = {"action": self.action, "reason": "r", "confidence": 0.7}
            if self.action in ("open_long", "open_short"):
                args["sizeFraction"] = self.size
            yield StreamEvent("tool_use_start", id="t", name="submit_trade_decision")
            yield StreamEvent("tool_use_args_delta", id="t",
                              partial_json=json.dumps(args, ensure_ascii=False))
            yield StreamEvent("message_done", stop_reason="tool_use")
        else:
            yield StreamEvent("text_delta", text=self.summary)
            yield StreamEvent("message_done", stop_reason="end")


def _payload(round_no: int) -> dict:
    return {"round": round_no, "barTs": _START_TS + round_no, "lastPx": 30000.0,
            "position": None, "sessionPnlUsd": 0.0, "budgetUsd": 100.0,
            "takeProfitUsd": None, "stopLossUsd": None,
            "signalBoard": [{"strategyId": 1, "name": "S", "stance": "flat",
                             "latestSignal": "open_long", "strength": 1.0, "reason": "r"}]}


def _decider(scripts, *, rounds=10, settings=None):
    return AiTradeDecider(_ScriptedLLM(scripts), "sys", context_refresh_rounds=rounds,
                          settings=settings or Settings())


def _candle(idx: int, price: float = 30000.0) -> Candle:
    return Candle(ts=_START_TS + idx * _STEP, o=price, h=price + 5, l=price - 5,
                  c=price, vol=100.0, confirm=True)


def _make_session(*, action="open_long", size=0.5, run_id=1, context_rounds=10,
                  settings=None):
    settings = settings or Settings()
    calls = {"orders": [], "closes": []}

    async def place_order_fn(order):
        calls["orders"].append(order)
        return {"ok": True, "ordId": "x", "clOrdId": "aragonx"}

    async def close_position_fn(close):
        calls["closes"].append(close)
        return {"ok": True, "clOrdId": "aragoncx"}

    async def positions_fn():
        ex = session.executor
        if ex.projected == "flat":
            return []
        return [{"instId": "BTC-USDT-SWAP", "pos": ex.entry_qty_sz, "upl": 0.0}]

    executor = LiveExecutor(
        creds=AccountCredentials("ak", "sk-000000", "pp-000000"), account_id=1,
        inst_id="BTC-USDT-SWAP", td_mode="cross", pos_mode="long_short_mode",
        leverage=3.0, budget_usd=1000.0, risk=RiskParams(), ct_val=0.01,
        lot_sz=0.01, min_sz=0.01, settings=settings,
        place_order_fn=place_order_fn, close_position_fn=close_position_fn,
        positions_fn=positions_fn)
    decider = AiTradeDecider(_TraceLLM(action, size), "sys",
                             context_refresh_rounds=context_rounds, settings=settings)
    spec = PaperStrategySpec(1, "S", "atomic", _StubStrategy({}), {})
    session = AiTradeSession("aitrade_trace", run_id, [spec], "BTC-USDT-SWAP", "1H", executor,
                             decider, priming=False, priming_bars=10,
                             context_refresh_rounds=context_rounds)
    return session, calls


async def _settle(session) -> None:
    task = session._decision_task
    if task is not None:
        try:
            await task
        except asyncio.CancelledError:
            pass


def _drain(q: asyncio.Queue) -> list[dict]:
    out: list[dict] = []
    while not q.empty():
        out.append(q.get_nowait())
    return out


def _stub_round_trace(session, round_no: int) -> dict:
    trace = new_round_trace(round_no, _START_TS, 30000.0,
                            {"position": None, "marketContext": None, "consensus": None}, [])
    session._traces.append(trace)
    session._round_trace = trace
    return trace


# ---------------- T1/T2：决策器 emit 合并 / 空转等价 ----------------
async def test_decider_emit_coalesces_analysis_and_marks_tool_start():
    """T1：100 段单字 text_delta 被合并为远少于 token 数的 analysis 片段，拼接文本无损；tool_call_start 就位。"""
    script = ([StreamEvent("text_delta", text="a") for _ in range(100)]
              + [StreamEvent("tool_use_start", id="t", name="submit_trade_decision"),
                 StreamEvent("tool_use_args_delta", id="t",
                             partial_json=json.dumps({"action": "hold", "reason": "r"})),
                 StreamEvent("message_done", stop_reason="tool_use")])
    d = _decider([script])
    collector = _EmitCollector()
    decision, degrade = await d.decide(_payload(1), emit=collector)
    assert decision.action == "hold" and degrade is None
    analysis = [p for p in collector.parts if p.get("phase") == "analysis"]
    starts = [p for p in collector.parts if p.get("phase") == "tool_call_start"]
    assert starts and starts[0]["name"] == "submit_trade_decision"
    assert 1 <= len(analysis) < 100                        # 合并：条数远少于 token 数
    assert "".join(p["delta"] for p in analysis) == "a" * 100


async def test_decider_emit_none_is_behaviorally_equivalent():
    """T2：接 emit 与不接 emit（None）返回的 (decision, degrade) 逐字一致（保护回测 / 既有路径零回归）。"""
    def build():
        return _decider([[StreamEvent("text_delta", text="分析"),
                          StreamEvent("tool_use_start", id="t", name="submit_trade_decision"),
                          StreamEvent("tool_use_args_delta", id="t",
                                      partial_json=json.dumps({"action": "open_long",
                                                               "sizeFraction": 0.4, "reason": "r"})),
                          StreamEvent("message_done", stop_reason="tool_use")]])
    dec_none, deg_none = await build().decide(_payload(1))                 # emit 缺省 None
    dec_emit, deg_emit = await build().decide(_payload(1), emit=_EmitCollector())
    assert (dec_none.action, dec_none.size_fraction, deg_none) == \
           (dec_emit.action, dec_emit.size_fraction, deg_emit)
    assert dec_none.action == "open_long"


# ---------------- T3：会话六 phase 装配 ----------------
async def test_session_weaves_round_phases_in_order():
    await init_db()
    session, calls = _make_session(action="open_long")
    try:
        q = session.subscribe()
        await session.feed_candle(_candle(0))
        await _settle(session)
        traces = [e["data"] for e in _drain(q) if e["type"] == "agent_trace"]
        phases = [t["phase"] for t in traces]
        assert phases[0] == "round_start"
        assert phases.index("round_start") < phases.index("analysis")
        assert "tool_call_start" in phases
        assert phases.index("tool_call") < phases.index("result")
        tool_call = next(t for t in traces if t["phase"] == "tool_call")
        assert tool_call["input"]["action"] == "open_long"       # 门禁后决策
        result = next(t for t in traces if t["phase"] == "result")
        assert result["executed"]["action"] == "open_long"       # 展示 = 真实执行
        assert session.executor.projected == "long"
    finally:
        await dispose_engine()


# ---------------- T4：快照回放 + RV-1 防重复 + 环形容量 ----------------
async def test_snapshot_freezes_inflight_analysis_no_double_count():
    """RV-1：快照对在飞轮做防御性拷贝——冻结后再喂 delta，快照对象 analysis 不变（无二次追加）。"""
    await init_db()
    session, _ = _make_session(action="hold")
    try:
        emit = session._make_trace_emit(1, _START_TS)
        _stub_round_trace(session, 1)
        await emit({"phase": "analysis", "delta": "前半段"})
        snap = session.snapshot()
        assert snap["agentTraces"][-1]["analysis"] == "前半段"
        await emit({"phase": "analysis", "delta": "后半段"})
        assert snap["agentTraces"][-1]["analysis"] == "前半段"        # 快照冻结，不随后续 delta 变
        assert session._round_trace["analysis"] == "前半段后半段"     # 原对象仍原地累积
    finally:
        await dispose_engine()


async def test_traces_ring_capacity_drops_oldest():
    session, _ = _make_session(action="hold")
    for r in range(25):
        _stub_round_trace(session, r)
    traces = session.snapshot()["agentTraces"]
    assert len(traces) == 20
    assert traces[0]["round"] == 5                               # 0..4 被环形丢弃


# ---------------- T5：共识降级 + RV-2 守卫跳过不发轨迹 ----------------
async def test_consensus_downgrade_reflected_in_tool_call_and_result():
    await init_db()
    session, calls = _make_session(action="open_long",
                                   settings=Settings(ai_trade_min_consensus=1))
    try:
        q = session.subscribe()
        await session.feed_candle(_candle(0))
        await _settle(session)
        assert calls["orders"] == []                            # 门禁在 apply 之前拦下
        traces = [e["data"] for e in _drain(q) if e["type"] == "agent_trace"]
        tool_call = next(t for t in traces if t["phase"] == "tool_call")
        assert tool_call["input"]["action"] == "hold"           # 降级后如实呈现 hold
        result = next(t for t in traces if t["phase"] == "result")
        assert result["degradeReason"] and result["degradeReason"].startswith("low_consensus")
    finally:
        await dispose_engine()


async def test_guard_skip_emits_no_agent_trace():
    """RV-2：`_deciding` 守卫跳过的 K 线不产生 agent_trace（仅左栏 decision{skipped} 照旧）。"""
    await init_db()
    session, calls = _make_session(action="open_long")
    try:
        q = session.subscribe()
        session._deciding = True                                # 模拟上一轮仍在进行
        await session.feed_candle(_candle(0))
        events = _drain(q)
        assert not [e for e in events if e["type"] == "agent_trace"]
        skipped = [e for e in events if e["type"] == "decision"
                   and e["data"]["executed"].get("skipped")]
        assert skipped and session._decision_task is None
    finally:
        await dispose_engine()


# ---------------- T6：摘要 phase 与顶层 summary 一致 ----------------
async def test_summary_phase_matches_top_level_summary():
    await init_db()
    session, _ = _make_session(action="hold", context_rounds=1)
    try:
        q = session.subscribe()
        await session.feed_candle(_candle(0))
        await _settle(session)
        events = _drain(q)
        top = [e for e in events if e["type"] == "summary"]
        trace_sum = [e["data"] for e in events
                     if e["type"] == "agent_trace" and e["data"]["phase"] == "summary"]
        assert top and trace_sum
        assert top[0]["data"]["summary"] == trace_sum[0]["summary"]
        assert session._round_trace["summary"] == top[0]["data"]["summary"]
    finally:
        await dispose_engine()


# ---------------- T7：轮异常兜底 error phase ----------------
async def test_round_exception_emits_error_trace_and_survives(monkeypatch):
    await init_db()
    session, _ = _make_session(action="hold")
    try:
        q = session.subscribe()

        async def _boom(*_a, **_k):
            raise RuntimeError("boom")

        monkeypatch.setattr(session, "_execute_decision", _boom)
        await session.feed_candle(_candle(0))
        await _settle(session)
        traces = [e["data"] for e in _drain(q) if e["type"] == "agent_trace"]
        err = [t for t in traces if t["phase"] == "error"]
        assert err and err[0]["message"]
        assert session._round_trace["state"] == "error"
        assert session.state == "running"                       # 会话不崩，等下一根
    finally:
        await dispose_engine()


async def test_run_finalizes_repo_for_summary_round(monkeypatch):
    """健全性：确保 run 行存在时 T6 路径 commit_round 触发的 finalize 不受轨迹织入影响。"""
    await init_db()
    sm = get_sessionmaker()
    async with sm() as s:
        run = await repo.create_run(s, strategy_id=1, inst_id="BTC-USDT-SWAP", bar="1H",
                                    mode="ai-trade", config={"mode": "ai-trade"})
        run_id = run.id
    session, _ = _make_session(action="hold", run_id=run_id, context_rounds=1)
    try:
        await session.feed_candle(_candle(0))
        await _settle(session)
        assert session.rounds_completed == 1
        assert session.decider.summary == "综合摘要：偏多"
    finally:
        await dispose_engine()
