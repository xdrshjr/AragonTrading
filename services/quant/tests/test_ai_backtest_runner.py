"""AiBacktestRunner 单测（ai-auto-trading-backtest §2.2 / §7.1，核心）。

桩 LLMClient 产 `tool_use_start/args_delta/message_done` 脚本化决策序列 + 合成行情：暖机段不决策；决策根
对齐 `decisionEveryBars`；决策→`main.submit`→次根开盘成交（trades/equity 非空）；`compute_metrics` 七项 +
`aiStats` 正确；每 `contextRefreshRounds` 轮出一次 `summary`；到 `max_rounds` 停决策续盯市 + `result.notes`
标注；取消 → `state=cancelled`。OKX 取数打桩（不触网），落库走 conftest 临时 DB。
"""
from __future__ import annotations

import json

from sqlalchemy import select

from app.ai_auto.backtest import AiBacktestRunner, AiBacktestSpec, _BacktestJob
from app.agent.llm.base import StreamEvent
from app.ai_auto.prompts import REFLECTION_TOOL_NAME
from app.config import Settings
from app.db import repositories as repo
from app.db.models import Strategy
from app.db.session import dispose_engine, get_sessionmaker, init_db
from app.engine.backtester import candles_to_df
from app.paper.strategy_factory import build_paper_strategy_spec

from tests.conftest import make_synthetic_candles


class _StubLLM:
    """按决策函数逐次产决策工具流；tools 为空（摘要）时产一段固定文本。"""

    def __init__(self, decide_fn):
        self._decide_fn = decide_fn
        self.decision_calls = 0

    async def stream(self, *, system, messages, tools):
        if not tools:                                  # 摘要请求（tools=[]）
            yield StreamEvent("text_delta", text="综合执行摘要")
            yield StreamEvent("message_done", stop_reason="end")
            return
        self.decision_calls += 1
        args = self._decide_fn(self.decision_calls)
        yield StreamEvent("tool_use_start", id="t", name="submit_trade_decision")
        yield StreamEvent("tool_use_args_delta", id="t", partial_json=json.dumps(args))
        yield StreamEvent("message_done", stop_reason="tool_use")


def _alternating(call: int) -> dict:
    """奇数轮开多、偶数轮平仓（制造真实成交序列）。"""
    if call % 2 == 1:
        return {"action": "open_long", "sizeFraction": 0.5, "confidence": 0.8, "reason": f"r{call}"}
    return {"action": "close", "reason": f"r{call}"}


async def _seed_spec():
    await init_db()
    async with get_sessionmaker()() as s:
        sid = (await s.execute(select(Strategy.id).order_by(Strategy.id.asc()).limit(1))).scalar_one()
        row = await repo.get_strategy_row(s, sid)
    return sid, build_paper_strategy_spec(row)


def _spec(sid, paper_spec, candles, *, every=40, refresh=3, max_rounds=400,
          tp_usd=None, sl_usd=None, reflection_enabled=False,
          profit_rule=None) -> AiBacktestSpec:
    start_ts = candles[60]["ts"]          # 前 60 根作暖机（regime min_bars=60），用户区间自 60 起
    end_ts = candles[-1]["ts"]
    return AiBacktestSpec(
        inst_id="BTC-USDT-SWAP", bar="1H", strategy_ids=[sid], strategy_names=["S"],
        specs=[paper_spec], start_ts=start_ts, end_ts=end_ts, decision_every_bars=every,
        initial_capital=10000.0, leverage=3.0, stop_loss_pct=0.05, take_profit_pct=0.1,
        take_profit_usd=tp_usd, stop_loss_usd=sl_usd, context_refresh_rounds=refresh,
        min_consensus=0, planned_rounds=6,
        settings=Settings(ai_backtest_max_rounds=max_rounds), rest=object(), provider=object(),
        reflection_enabled=reflection_enabled, profit_rule=profit_rule)


def _patch_loaders(monkeypatch, candles, llm):
    async def fake_range(*_a, **_k):
        return candles

    async def fake_htf(*_a, **_k):
        return {}

    monkeypatch.setattr("app.ai_auto.backtest.load_range_candles", fake_range)
    monkeypatch.setattr("app.ai_auto.backtest.load_htf_frames", fake_htf)
    monkeypatch.setattr("app.ai_auto.backtest.make_llm_client_from_config", lambda cfg: llm)


async def test_full_run_produces_trades_metrics_aistats_and_persists(monkeypatch):
    try:
        sid, paper = await _seed_spec()
        candles = make_synthetic_candles(300)
        llm = _StubLLM(_alternating)
        _patch_loaders(monkeypatch, candles, llm)
        runner = AiBacktestRunner(_spec(sid, paper, candles, every=40, refresh=3))
        job = _BacktestJob("aibt_test", runner)
        await runner.run(job)

        assert job.state == "completed"
        result = job.result
        assert result is not None
        # 决策根对齐 decisionEveryBars=40：i=60,100,140,180,220,260（i<n-1=299）→ 6 轮
        assert result["aiStats"]["decisionRounds"] == 6
        assert result["aiStats"]["actionCounts"]["open_long"] == 3
        assert result["aiStats"]["actionCounts"]["close"] == 3
        # 决策→次根开盘成交：有成交与逐根权益
        assert len(result["trades"]) > 0
        assert len(result["equity"]) > 0
        # compute_metrics 七项俱在
        for key in ("totalReturn", "winRate", "profitFactor", "maxDrawdown", "sharpe",
                    "tradeCount", "avgPnl"):
            assert key in result["metrics"]
        # 平均置信度只计有值者（开多 0.8，平仓无置信度）
        assert abs(result["aiStats"]["avgConfidence"] - 0.8) < 1e-9
        # 每 contextRefreshRounds=3 轮一次摘要 → 第 3、6 轮共 2 次
        assert result["aiStats"]["summaryCount"] == 2
        assert result["aiStats"]["reachedRoundCap"] is False
        # 落 Run（mode=ai-backtest）+ trades + equity
        assert job.run_id is not None
        async with get_sessionmaker()() as s:
            run = await repo.get_run(s, job.run_id)
            assert run is not None and run.mode == "ai-backtest" and run.status == "completed"
    finally:
        await dispose_engine()


async def test_load_htf_merges_decision_bars_with_80_bar_warmup(monkeypatch):
    try:
        sid, paper = await _seed_spec()
        candles = make_synthetic_candles(100)
        monkeypatch.setattr(
            "app.ai_auto.backtest.make_llm_client_from_config",
            lambda _cfg: _StubLLM(lambda _call: {"action": "hold", "reason": "h"}),
        )
        runner = AiBacktestRunner(_spec(sid, paper, candles))
        captured = {}

        async def fake_htf(*_args, **kwargs):
            captured.update(kwargs)
            return {}

        monkeypatch.setattr("app.ai_auto.backtest.load_htf_frames", fake_htf)
        await runner._load_htf(candles[0]["ts"], 3600)

        assert captured["timeframes"] == ["4H", "12H", "1D"]
        assert captured["htf_warmup_bars"] >= 80
    finally:
        await dispose_engine()


async def test_load_htf_skips_decision_bars_when_context_is_disabled(monkeypatch):
    try:
        sid, paper = await _seed_spec()
        candles = make_synthetic_candles(100)
        monkeypatch.setattr(
            "app.ai_auto.backtest.make_llm_client_from_config",
            lambda _cfg: _StubLLM(lambda _call: {"action": "hold", "reason": "h"}),
        )
        runner = AiBacktestRunner(_spec(sid, paper, candles))
        runner.settings.ai_trade_multi_timeframe_context = False
        calls = []

        async def fake_htf(*_args, **kwargs):
            calls.append(kwargs)
            return {}

        monkeypatch.setattr("app.ai_auto.backtest.load_htf_frames", fake_htf)
        result = await runner._load_htf(candles[0]["ts"], 3600)

        assert result == {}
        assert calls == []
    finally:
        await dispose_engine()


async def test_backtest_decider_and_digest_share_multi_timeframe_context(monkeypatch):
    try:
        sid, paper = await _seed_spec()
        candles = make_synthetic_candles(180)
        llm = _StubLLM(lambda _call: {"action": "hold", "reason": "h"})
        _patch_loaders(monkeypatch, candles, llm)
        expected = {"executionBar": "1H", "asOfTs": 123, "frames": []}
        calls = []

        def fake_context(**kwargs):
            calls.append(kwargs)
            return expected

        monkeypatch.setattr(
            "app.ai_auto.backtest.build_multi_timeframe_context", fake_context)
        runner = AiBacktestRunner(_spec(sid, paper, candles, every=60, refresh=100))
        original_decide = runner.decider.decide
        seen_payloads = []

        async def capture_decide(payload, *args, **kwargs):
            seen_payloads.append(payload)
            return await original_decide(payload, *args, **kwargs)

        runner.decider.decide = capture_decide
        job = _BacktestJob("aibt_mtf", runner)
        await runner.run(job)

        assert calls
        assert all(call["as_of_ts"] == decision["ts"] + 3600
                   for call, decision in zip(calls, job.result["decisionsDigest"]))
        assert all(payload["multiTimeframeContext"] is expected for payload in seen_payloads)
        assert all(decision["multiTimeframeContext"] is expected
                   for decision in job.result["decisionsDigest"])
    finally:
        await dispose_engine()


_EXT_KEYS = ("annualizedReturn", "annualizedVolatility", "sortino", "calmar", "maxDrawdownBars",
             "maxDrawdownSecs", "timeInMarketPct", "avgWin", "avgLoss", "payoffRatio", "largestWin",
             "largestLoss", "maxConsecutiveWins", "maxConsecutiveLosses", "avgHoldBars",
             "tradesPerWeek")
_BENCH_KEYS = ("equity", "totalReturn", "maxDrawdown", "sharpe", "annualizedReturn", "alpha")


async def test_extended_metrics_and_benchmark_present_persisted_and_in_snapshot(monkeypatch):
    """[B1/B2] result/落库/GET 快照均含 extendedMetrics + benchmark；alpha 语义正确；在场根分母对齐。"""
    try:
        sid, paper = await _seed_spec()
        candles = make_synthetic_candles(300)
        _patch_loaders(monkeypatch, candles, _StubLLM(_alternating))
        runner = AiBacktestRunner(_spec(sid, paper, candles, every=40, refresh=3))
        job = _BacktestJob("aibt_ext", runner)
        await runner.run(job)

        result = job.result
        ext, bench = result["extendedMetrics"], result["benchmark"]
        assert all(k in ext for k in _EXT_KEYS)
        assert all(k in bench for k in _BENCH_KEYS)
        # alpha == 策略 totalReturn − 基准 totalReturn
        assert abs(bench["alpha"] - (result["metrics"]["totalReturn"] - bench["totalReturn"])) < 1e-9
        # 在场根计数为分母 len(equity) 的子集（0 ≤ bars_in_market ≤ 权益点数）
        assert 0 <= runner._bars_in_market <= len(result["equity"])
        assert ext["timeInMarketPct"] == round(runner._bars_in_market / len(result["equity"]), 6)
        # [P2-3] GET 快照（status_dict.result）亦透传两键
        snap = job.status_dict()["result"]
        assert "extendedMetrics" in snap and "benchmark" in snap
        # 落库 metrics_json 含两键（自由 JSON，无迁移）
        async with get_sessionmaker()() as s:
            run = await repo.get_run(s, job.run_id)
            metrics_json = json.loads(run.metrics_json)
            assert "extendedMetrics" in metrics_json and "benchmark" in metrics_json
    finally:
        await dispose_engine()


async def test_analytics_derivation_failure_does_not_break_result(monkeypatch):
    """[P2-2] 派生抛错 → 回落空 dict、主 result/落库/终态不受影响（warning 已在实现内记）。"""
    try:
        sid, paper = await _seed_spec()
        candles = make_synthetic_candles(300)
        _patch_loaders(monkeypatch, candles, _StubLLM(_alternating))

        def _boom(*_a, **_k):
            raise RuntimeError("synthetic derive failure")

        monkeypatch.setattr("app.ai_auto.backtest.compute_extended_metrics", _boom)
        monkeypatch.setattr("app.ai_auto.backtest.compute_buy_hold", _boom)
        runner = AiBacktestRunner(_spec(sid, paper, candles, every=40, refresh=3))
        job = _BacktestJob("aibt_boom", runner)
        await runner.run(job)

        assert job.state == "completed"                 # 分析层异常不阻断主流程
        assert job.result["extendedMetrics"] == {}      # 回落空 dict
        assert job.result["benchmark"] == {}
        assert "totalReturn" in job.result["metrics"]   # 既有七项照常
        assert job.run_id is not None                   # 落库照常
    finally:
        await dispose_engine()


async def test_decision_bars_align_to_interval(monkeypatch):
    """决策根严格对齐 decisionEveryBars（相邻决策 barIndex 差恒为间隔）。"""
    try:
        sid, paper = await _seed_spec()
        candles = make_synthetic_candles(300)
        _patch_loaders(monkeypatch, candles, _StubLLM(lambda c: {"action": "hold", "reason": "h"}))
        runner = AiBacktestRunner(_spec(sid, paper, candles, every=30, refresh=100))
        job = _BacktestJob("aibt_align", runner)
        await runner.run(job)
        digest = job.result["decisionsDigest"]
        bar_indices = [d["barIndex"] for d in digest]
        diffs = {b - a for a, b in zip(bar_indices, bar_indices[1:])}
        assert diffs == {30}                       # 相邻决策恒隔 30 根
    finally:
        await dispose_engine()


async def test_round_cap_stops_deciding_but_keeps_marking(monkeypatch):
    """到 max_rounds → 停决策、续逐根盯市到区间末、result.notes 标注、reachedRoundCap=True。"""
    try:
        sid, paper = await _seed_spec()
        candles = make_synthetic_candles(300)
        _patch_loaders(monkeypatch, candles, _StubLLM(lambda c: {"action": "hold", "reason": "h"}))
        runner = AiBacktestRunner(_spec(sid, paper, candles, every=1, refresh=100, max_rounds=5))
        job = _BacktestJob("aibt_cap", runner)
        await runner.run(job)
        assert job.state == "completed"
        assert job.result["aiStats"]["decisionRounds"] == 5      # 到顶即停决策
        assert job.result["aiStats"]["reachedRoundCap"] is True
        assert any("决策上限" in n for n in job.result["notes"])
        # 盯市不停：权益点远多于 5（续盯市到区间末）
        assert len(job.result["equity"]) > 5
    finally:
        await dispose_engine()


async def test_cancel_before_run_yields_cancelled_state(monkeypatch):
    """setup 前置取消 → state=cancelled（协作式取消，绝不假完成）。"""
    try:
        sid, paper = await _seed_spec()
        candles = make_synthetic_candles(300)
        _patch_loaders(monkeypatch, candles, _StubLLM(_alternating))
        runner = AiBacktestRunner(_spec(sid, paper, candles))
        job = _BacktestJob("aibt_cancel", runner)
        job.request_cancel()
        await runner.run(job)
        assert job.state == "cancelled"
    finally:
        await dispose_engine()


async def test_session_stop_loss_halts_decisions_and_notes(monkeypatch):
    """会话级 USD 止损触发 → 停决策、续盯市、result.notes 记因。"""
    try:
        sid, paper = await _seed_spec()
        candles = make_synthetic_candles(300)
        # 每轮开多满仓，制造敞口；极小 stop_loss_usd 使早触发会话止损
        _patch_loaders(monkeypatch, candles,
                       _StubLLM(lambda c: {"action": "open_long", "sizeFraction": 1.0,
                                           "confidence": 0.9, "reason": "r"}))
        runner = AiBacktestRunner(_spec(sid, paper, candles, every=1, refresh=100, sl_usd=1.0))
        job = _BacktestJob("aibt_sess", runner)
        await runner.run(job)
        assert job.state == "completed"
        # 触发会话止损后不再决策，末段仅盯市 → notes 记因
        assert any("会话级" in n for n in job.result["notes"])
    finally:
        await dispose_engine()


# ---------- 下单前二次反思（ai-trade-pre-trade-reflection §6.2 回测同步）----------
class _ReflectStubLLM:
    """回测桩：决策流 submit_trade_decision（奇开多/偶平仓）；反思流 submit_trade_reflection（固定裁决）。"""

    def __init__(self, verdict: str):
        self._verdict = verdict
        self.reflection_calls = 0
        self.decision_calls = 0

    async def stream(self, *, system, messages, tools):
        if not tools:                                  # 摘要流
            yield StreamEvent("text_delta", text="综合执行摘要")
            yield StreamEvent("message_done", stop_reason="end")
            return
        if tools[0].name == REFLECTION_TOOL_NAME:      # 反思流
            self.reflection_calls += 1
            args = {"verdict": self._verdict, "reason": "复核结论"}
            yield StreamEvent("tool_use_start", id="rf", name=REFLECTION_TOOL_NAME)
            yield StreamEvent("tool_use_args_delta", id="rf", partial_json=json.dumps(args))
            yield StreamEvent("message_done", stop_reason="tool_use")
            return
        self.decision_calls += 1                       # 决策流
        args = _alternating(self.decision_calls)
        yield StreamEvent("tool_use_start", id="d", name="submit_trade_decision")
        yield StreamEvent("tool_use_args_delta", id="d", partial_json=json.dumps(args))
        yield StreamEvent("message_done", stop_reason="tool_use")


async def test_reflection_abort_blocks_submit_and_counts(monkeypatch):
    """reflection_enabled=True + 反思全 abort → 所有交易意愿被否决为 hold（无成交）、reflectionAborts>0。"""
    try:
        sid, paper = await _seed_spec()
        candles = make_synthetic_candles(300)
        llm = _ReflectStubLLM("abort")
        _patch_loaders(monkeypatch, candles, llm)
        runner = AiBacktestRunner(
            _spec(sid, paper, candles, every=40, refresh=3, reflection_enabled=True))
        job = _BacktestJob("aibt_reflect_abort", runner)
        await runner.run(job)
        assert job.state == "completed"
        stats = job.result["aiStats"]
        # 6 决策轮，奇数轮 open_long 被反思否决 → 反思只在交易意愿轮触发（open_long=3、close=3 → 6 次）
        assert llm.reflection_calls == 6
        assert stats["reflectionRuns"] == 6
        assert stats["reflectionAborts"] == 6
        assert stats["reflectionAdjustments"] == 0
        # 全部意愿被否 → 主账户从未真正成交（无 trades）
        assert len(job.result["trades"]) == 0
        assert stats["actionCounts"]["hold"] == 6      # 全轮最终动作为 hold（否决后）
    finally:
        await dispose_engine()


async def test_reflection_confirm_lets_trades_land(monkeypatch):
    """reflection_enabled=True + 反思全 confirm → 交易照常落地，reflectionRuns 计数、Aborts=0。"""
    try:
        sid, paper = await _seed_spec()
        candles = make_synthetic_candles(300)
        llm = _ReflectStubLLM("confirm")
        _patch_loaders(monkeypatch, candles, llm)
        runner = AiBacktestRunner(
            _spec(sid, paper, candles, every=40, refresh=3, reflection_enabled=True))
        job = _BacktestJob("aibt_reflect_confirm", runner)
        await runner.run(job)
        assert job.state == "completed"
        stats = job.result["aiStats"]
        assert stats["reflectionRuns"] == 6
        assert stats["reflectionAborts"] == 0
        assert len(job.result["trades"]) > 0           # confirm → 开多/平仓照常成交
    finally:
        await dispose_engine()


async def test_reflection_disabled_default_no_calls_zero_counts(monkeypatch):
    """零回归门禁：默认 reflection_enabled=False（直接构造 spec）→ 无反思调用、三计数恒 0、行为等价旧版。"""
    try:
        sid, paper = await _seed_spec()
        candles = make_synthetic_candles(300)
        llm = _ReflectStubLLM("abort")                 # 即便脚本备了 abort，也绝不被调用
        _patch_loaders(monkeypatch, candles, llm)
        runner = AiBacktestRunner(_spec(sid, paper, candles, every=40, refresh=3))  # 默认关
        job = _BacktestJob("aibt_reflect_off", runner)
        await runner.run(job)
        assert job.state == "completed"
        stats = job.result["aiStats"]
        assert llm.reflection_calls == 0
        assert stats["reflectionRuns"] == 0
        assert stats["reflectionAborts"] == 0
        assert stats["reflectionAdjustments"] == 0
        assert len(job.result["trades"]) > 0           # 与旧行为一致：交易照常落地
    finally:
        await dispose_engine()


async def test_reflection_vetoed_note_lands_in_backtest_journal(monkeypatch):
    """F2（ai-trade-reflection-hold-inertia-fix）：回测反思否决轮 journal 保真被否决意图——
    decision 记最终 hold（R-P1-3 不变式），reflection 注记带原提议动作；trigger=None（回测/legacy 同形）。"""
    try:
        sid, paper = await _seed_spec()
        candles = make_synthetic_candles(300)
        llm = _ReflectStubLLM("abort")
        _patch_loaders(monkeypatch, candles, llm)
        # refresh=10 > 6 轮 → 全程不触发摘要，journal 不被清空，可直接断言全部 6 条。
        runner = AiBacktestRunner(
            _spec(sid, paper, candles, every=40, refresh=10, reflection_enabled=True))
        job = _BacktestJob("aibt_reflect_note", runner)
        await runner.run(job)
        assert job.state == "completed"
        journal = runner.decider._journal
        assert len(journal) == 6
        first = journal[0]                             # 首轮提议 open_long 被否决
        assert first["decision"]["action"] == "hold"
        note = first["reflection"]
        assert note["vetoed"] is True and note["failClosed"] is False
        assert note["proposedAction"] == "open_long"
        assert note["finalAction"] == "hold"
        assert first["trigger"] is None                # 回测 payload 无 trigger 键
        assert all(e["reflection"]["vetoed"] is True for e in journal)   # 全轮意愿均被否决
    finally:
        await dispose_engine()


async def test_reflection_disabled_backtest_journal_reflection_none(monkeypatch):
    """零回归伴生：反思关闭时回测 journal 恒带 reflection/trigger 两键且值为 None（形状稳定 R-06）。"""
    try:
        sid, paper = await _seed_spec()
        candles = make_synthetic_candles(300)
        llm = _ReflectStubLLM("abort")
        _patch_loaders(monkeypatch, candles, llm)
        runner = AiBacktestRunner(_spec(sid, paper, candles, every=40, refresh=10))  # 默认关
        job = _BacktestJob("aibt_reflect_none", runner)
        await runner.run(job)
        assert job.state == "completed"
        journal = runner.decider._journal
        assert len(journal) == 6
        assert all(e["reflection"] is None and e["trigger"] is None for e in journal)
    finally:
        await dispose_engine()


# ---------- 【基础策略】回测与实盘同源（ai-trade-base-strategy-ma-band §6.1 用例 25 / P1-4）----------
async def test_backtest_applies_same_gate_as_live(monkeypatch):
    """回测调**同一个** `apply_ma_band_gate` 纯核心：同一 band + 同一决策 ⇒ 同一 degradeReason。

    回测若不同源，AI 回测会系统性地比实盘更容易开仓，用户据此调出的参数在实盘直接失真。本用例逐轮
    比对回测产出的降级码与直接调纯核心的结果——严禁在 backtest.py 里复制一份判定逻辑。
    """
    from app.ai_auto.base_rules import MaBandRule, apply_ma_band_gate, build_ma_band
    from app.ai_auto.decider import TradeDecision

    try:
        sid, paper = await _seed_spec()
        candles = make_synthetic_candles(300)
        llm = _StubLLM(lambda call: {"action": "open_long", "sizeFraction": 0.5,
                                     "confidence": 0.9, "reason": f"r{call}"})
        _patch_loaders(monkeypatch, candles, llm)
        rule = MaBandRule()
        spec = _spec(sid, paper, candles, every=40)
        spec.base_rule = rule
        runner = AiBacktestRunner(spec)
        job = _BacktestJob("bt_base_rule", runner)
        await runner.run(job)

        decisions = list(runner._decisions_digest)
        assert decisions, "回测未产出决策"
        main_df = candles_to_df(candles)
        for d in decisions:
            i = next(k for k in range(len(main_df)) if int(main_df.iloc[k]["ts"]) == d["ts"])
            band = build_ma_band(main_df.iloc[:i + 1], rule)
            expected = apply_ma_band_gate(
                TradeDecision("open_long", 0.5, 0.9, "r"), band, rule,
                last_px=float(main_df.iloc[i]["c"]))[1]
            assert d["degradeReason"] == expected
            if expected is not None:
                assert d["action"] == "hold"        # 被否轮真的没下单
    finally:
        await dispose_engine()


async def test_backtest_without_base_rule_is_unchanged(monkeypatch):
    """未装配（默认 None）⇒ payload 无 `baseRules` 键、无任何 base_rule 降级（既有回测零回归）。"""
    try:
        sid, paper = await _seed_spec()
        candles = make_synthetic_candles(300)
        llm = _StubLLM(_alternating)
        _patch_loaders(monkeypatch, candles, llm)
        runner = AiBacktestRunner(_spec(sid, paper, candles, every=40))
        await runner.run(_BacktestJob("bt_no_base_rule", runner))
        assert runner.base_rule is None
        assert not [d for d in runner._decisions_digest
                    if (d.get("degradeReason") or "").startswith("base_rule_")]
    finally:
        await dispose_engine()


# ============ F3 回测接入利润保护（ai-trade-profit-protection-adaptive §2.3 / §7.3-5）============
def _pg_rule(**over):
    from app.ai_auto.profit_guard import ProfitGuardRule

    base = {"arm_return_pct": 0.001, "giveback_pct": 0.2, "breakeven_arm_return_pct": 0.0,
            "min_hold_s": 0.0}
    base.update(over)
    return ProfitGuardRule(**base)


async def test_profit_rule_none_keeps_backtest_byte_identical(monkeypatch):
    """G-3：`profit_rule=None`（既有直构造用例）⇒ 保护层全路径空转，payload / notes / 统计逐字回退。"""
    try:
        sid, paper = await _seed_spec()
        candles = make_synthetic_candles(300)
        llm = _StubLLM(_alternating)
        _patch_loaders(monkeypatch, candles, llm)
        runner = AiBacktestRunner(_spec(sid, paper, candles))
        job = _BacktestJob("aibt_off", runner)
        await runner.run(job)

        result = job.result
        assert result["aiStats"]["profitGuardExits"] == 0
        # RV-1：未装配必须**可分辨**——只看 exits==0 分不出「装了没触发」，UI 会照挂免责卡片。
        assert result["aiStats"]["profitGuardEnabled"] is False
        assert all("利润保护" not in note for note in result["notes"])
        assert runner._guard_state is None and runner._cooldown is None
        assert runner._atr_series is None                 # 未装配时连 ATR 都不算
        assert "profitGuard" not in runner._run_config() or runner._run_config()["profitGuard"] is None
    finally:
        await dispose_engine()


async def test_assembled_profit_rule_fires_and_notes_the_conservative_caveat(monkeypatch):
    """装配后：跑出保护性平仓（`profitGuardExits > 0`）+ notes 带 §2.3.5 免责 + config 可复现参数。"""
    try:
        sid, paper = await _seed_spec()
        candles = make_synthetic_candles(300)
        llm = _StubLLM(lambda call: (
            {"action": "open_long", "sizeFraction": 0.5, "confidence": 0.8, "reason": "r"}
            if call == 1 else {"action": "hold", "reason": "h"}))
        _patch_loaders(monkeypatch, candles, llm)
        runner = AiBacktestRunner(_spec(sid, paper, candles, profit_rule=_pg_rule()))
        job = _BacktestJob("aibt_on", runner)
        await runner.run(job)

        result = job.result
        assert result["aiStats"]["profitGuardExits"] > 0
        assert result["aiStats"]["profitGuardEnabled"] is True
        assert any("bar 内极值保守近似" in note for note in result["notes"])
        assert any("不得据此推断实盘一定更好或更差" in note for note in result["notes"])
        guard_cfg = runner._run_config()["profitGuard"]
        assert guard_cfg is not None and guard_cfg["givebackPct"] == 0.2
        # 保护平仓的成交理由是保护码本身（绝不借 stop_loss 的手平仓，归因不撒谎）。
        assert any(t["reason"].startswith("profit_guard_")
                   for t in result["trades"] if t["side"] == "close")
    finally:
        await dispose_engine()


async def test_atr_series_uses_previous_bar_and_degrades_on_nan(monkeypatch):
    """无未来函数：第 i 根取 `iloc[i-1]`；`i-1<0` 或 NaN（暖机不足）⇒ None ⇒ 上下界整体不生效。"""
    try:
        sid, paper = await _seed_spec()
        candles = make_synthetic_candles(300)
        llm = _StubLLM(lambda _call: {"action": "hold", "reason": "h"})
        _patch_loaders(monkeypatch, candles, llm)
        runner = AiBacktestRunner(_spec(sid, paper, candles, profit_rule=_pg_rule()))
        job = _BacktestJob("aibt_atr", runner)
        await runner.run(job)

        import pandas as pd
        from app.engine.indicators import atr

        expected = atr(candles_to_df(candles), 14)
        assert runner._atr_series is not None
        assert runner._atr_at(0) is None                       # i-1 < 0
        assert runner._atr_at(5) is None                       # min_periods=14 前恒 NaN
        assert pd.isna(expected.iloc[4])
        assert runner._atr_at(100) == expected.iloc[99]        # 严格取上一根
    finally:
        await dispose_engine()


async def test_backtest_cooldown_gate_composes_three_reasons(monkeypatch):
    """§2.3.3a：冷却门禁与实盘**同一纯函数**、落在铁律与共识之间，`gate_reason` 三元合成。"""
    try:
        sid, paper = await _seed_spec()
        candles = make_synthetic_candles(300)
        llm = _StubLLM(lambda _call: {"action": "open_long", "sizeFraction": 0.5,
                                      "confidence": 0.8, "reason": "r"})
        _patch_loaders(monkeypatch, candles, llm)
        runner = AiBacktestRunner(_spec(sid, paper, candles, every=1,
                                        profit_rule=_pg_rule(cooldown_s=36_000.0)))
        job = _BacktestJob("aibt_cool", runner)
        await runner.run(job)

        degraded = [d for d in job.result["decisionsDigest"]
                    if (d.get("degradeReason") or "").startswith("profit_cooldown(")]
        assert degraded, "保护平仓后的同向开仓必须被冷却门禁降级为 hold"
        assert all(d["action"] == "hold" for d in degraded)
        assert job.result["aiStats"]["degradedRounds"] >= len(degraded)
    finally:
        await dispose_engine()


# ============ C5 回测承诺退出线（ai-trade-profit-protection-commitment §2.5）============
def test_backtest_diff_notes_include_commitment_caveat():
    """C5：回测差异清单追加承诺退出线免责句（bar 内极值保守 + holdExitPx 来自回测 LLM）。"""
    from app.ai_auto.backtest import _PROFIT_GUARD_BACKTEST_NOTE

    assert "承诺退出线同样为 bar 内极值保守近似" in _PROFIT_GUARD_BACKTEST_NOTE
    assert "holdExitPx 来自回测 LLM" in _PROFIT_GUARD_BACKTEST_NOTE


async def test_backtest_commitment_records_via_same_ratchet(monkeypatch):
    """C5/[P1-2]：回测 holdExitPx 经**同一** clamp+ratchet 记进 runner；更松被拒、更紧覆盖、epoch 复位三清。"""
    try:
        sid, paper = await _seed_spec()
        candles = make_synthetic_candles(300)
        _patch_loaders(monkeypatch, candles, _StubLLM(lambda _c: {"action": "hold", "reason": "h"}))
        runner = AiBacktestRunner(_spec(sid, paper, candles,
                                        profit_rule=_pg_rule(commit_enabled=True)))
        from app.ai_auto.decider import TradeDecision
        from app.ai_auto.profit_guard import STATUS_ARMED, STATUS_IDLE

        snap = {"status": STATUS_ARMED, "side": "long", "livePx": 100.0, "entryPx": 98.0,
                "breakevenPx": None, "atrPx": None}
        runner._record_commitment(TradeDecision("hold", None, None, "h", hold_exit_px=99.0), snap)
        assert runner._committed_exit_px == 99.0
        runner._record_commitment(TradeDecision("hold", None, None, "h", hold_exit_px=98.5), snap)
        assert runner._committed_exit_px == 99.0                # 更松被拒
        runner._record_commitment(TradeDecision("hold", None, None, "h", hold_exit_px=99.5), snap)
        assert runner._committed_exit_px == 99.5                # 更紧覆盖
        # epoch 复位（flat / 保护平仓）⇒ 承诺线三清（[P0-1]）
        runner._reset_guard_epoch()
        assert runner._committed_exit_px is None and runner._committed_exit_basis is None
        # 门控：未武装 / 非 hold ⇒ 不记
        runner._record_commitment(
            TradeDecision("hold", None, None, "h", hold_exit_px=99.0),
            {**snap, "status": STATUS_IDLE})
        assert runner._committed_exit_px is None
        runner._record_commitment(TradeDecision("close", None, None, "x"), snap)
        assert runner._committed_exit_px is None
    finally:
        await dispose_engine()


async def test_backtest_commitment_disabled_keeps_committed_none(monkeypatch):
    """G-4：commit_enabled=False（或不给 holdExitPx）⇒ runner 承诺线恒 None ⇒ 判定逐字回退 v2。"""
    try:
        sid, paper = await _seed_spec()
        candles = make_synthetic_candles(300)
        _patch_loaders(monkeypatch, candles, _StubLLM(lambda _c: {"action": "hold", "reason": "h"}))
        runner = AiBacktestRunner(_spec(sid, paper, candles,
                                        profit_rule=_pg_rule(commit_enabled=False)))
        from app.ai_auto.decider import TradeDecision
        from app.ai_auto.profit_guard import STATUS_ARMED

        snap = {"status": STATUS_ARMED, "side": "long", "livePx": 100.0, "entryPx": 98.0,
                "breakevenPx": None, "atrPx": None}
        runner._record_commitment(TradeDecision("hold", None, None, "h", hold_exit_px=99.0), snap)
        assert runner._committed_exit_px is None               # 承诺关 ⇒ 不记
        # 决策器也不该露出 holdExitPx（回测 allow_hold_exit 随 commit_enabled）
        assert "holdExitPx" not in runner.decider._tool.input_schema["properties"]
    finally:
        await dispose_engine()
