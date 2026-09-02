"""回测侧的四处接线（ai-trade-execution-trend-verdict 轴 F / §7.6）。

RK-8 的教训：**只在一侧接线**，用户拿回测调出来的参数在实盘不生效，且不报错。故本文件的装配
判据**同时覆盖 system 与 messages 两侧**（deep-reasoning-chain 的成文教训）。

另有一条评审 RP1-9 的专属护栏：被基调闸拦下的回测轮**必须带 `counter_trend_*` 降级码**——
只插调用而不把 `trend_reason` 并进 `gate_reason` 那条九元链，被拦轮会变成「决策是 hold、没有
任何降级码」，回测的漏斗与统计当场对不上且不报任何错。
"""
from __future__ import annotations

import inspect
import json

from sqlalchemy import select

from app.ai_auto.backtest import AiBacktestRunner, AiBacktestSpec, _BacktestJob
from app.agent.llm.base import StreamEvent
from app.config import Settings
from app.db import repositories as repo
from app.db.models import Strategy
from app.db.session import dispose_engine, get_sessionmaker, init_db
from app.paper.strategy_factory import build_paper_strategy_spec

from tests.conftest import make_synthetic_candles


class _CapturingLLM:
    """记下每一次 `system` 与 `messages`，供两侧装配判据对拍。"""

    def __init__(self, decide_fn) -> None:
        self._decide_fn = decide_fn
        self.calls = 0
        self.systems: list[str] = []
        self.user_texts: list[str] = []

    async def stream(self, *, system, messages, tools):
        if not tools:
            yield StreamEvent("text_delta", text="摘要")
            yield StreamEvent("message_done", stop_reason="end")
            return
        self.calls += 1
        self.systems.append(system)
        self.user_texts.append("\n".join(
            block.text for msg in messages for block in msg.content
            if getattr(block, "text", None)))
        yield StreamEvent("tool_use_start", id="t", name="submit_trade_decision")
        yield StreamEvent("tool_use_args_delta", id="t",
                          partial_json=json.dumps(self._decide_fn(self.calls)))
        yield StreamEvent("message_done", stop_reason="tool_use")


def _always_short(_call: int) -> dict:
    """每轮都提议**开多**且自报 0.6 置信度：在强空基调下这是逆势轮，且 0.6 < 逆势下界 0.65。

    置信度刻意**高于** `ai_trade_min_confidence`（默认 0）而低于逆势下界——否则它会先死在
    置信度门槛上，基调闸根本不会被走到。
    """
    return {"action": "open_long", "sizeFraction": 0.5, "confidence": 0.6, "reason": "r"}


async def _seed_spec():
    await init_db()
    async with get_sessionmaker()() as s:
        sid = (await s.execute(
            select(Strategy.id).order_by(Strategy.id.asc()).limit(1))).scalar_one()
        row = await repo.get_strategy_row(s, sid)
    return sid, build_paper_strategy_spec(row)


def _spec(sid, paper_spec, candles, settings) -> AiBacktestSpec:
    return AiBacktestSpec(
        inst_id="BTC-USDT-SWAP", bar="1H", strategy_ids=[sid], strategy_names=["S"],
        specs=[paper_spec], start_ts=candles[60]["ts"], end_ts=candles[-1]["ts"],
        decision_every_bars=40, initial_capital=10000.0, leverage=3.0,
        stop_loss_pct=0.05, take_profit_pct=0.1, take_profit_usd=None, stop_loss_usd=None,
        context_refresh_rounds=3, min_consensus=0, planned_rounds=6,
        settings=settings, rest=object(), provider=object(),
        reflection_enabled=False, profit_rule=None)


# 强空基调的固定断面。**刻意钉死**而不是指望合成 K 线恰好走出想要的形状：本文件守的是
# 「四处接线接上了没有」，不是打分函数（那由 `test_ai_trade_trend_verdict.py` 守）。
_FORCED_VERDICT = {
    "bar": "1H", "asOfTs": 0, "score": -0.4, "execScore": -0.7,
    "direction": "down", "bias": "short", "strength": "strong", "verdictConfidence": 0.7,
    "components": [{"key": "regime", "label": "体制结构", "score": -0.9, "weight": 0.26,
                    "detail": "forced"}],
    "missing": [], "availableWeight": 1.0, "execAvailableWeight": 1.0,
    "higherTimeframe": {"status": "bearish", "agree": True, "bullishBars": [],
                        "bearishBars": ["1D"]},
    "tacticalAlignment": None, "conflict": "none", "bonusWithheld": False,
    "counterGate": {"armed": True, "minConfidence": 0.65, "sizeMult": 0.5},
    "degraded": False, "degradeReason": None,
}


def _patch(monkeypatch, candles, llm, *, forced_verdict: bool = False):
    async def fake_range(*_a, **_k):
        return candles

    async def fake_htf(*_a, **_k):
        return {}

    monkeypatch.setattr("app.ai_auto.backtest.load_range_candles", fake_range)
    monkeypatch.setattr("app.ai_auto.backtest.load_htf_frames", fake_htf)
    monkeypatch.setattr("app.ai_auto.backtest.make_llm_client_from_config", lambda cfg: llm)
    if forced_verdict:
        monkeypatch.setattr("app.ai_auto.backtest.build_trend_verdict",
                            lambda inputs, rule: dict(_FORCED_VERDICT) if rule.enabled else None)


async def _run(monkeypatch, settings, *, forced_verdict: bool = False
               ) -> tuple[_CapturingLLM, dict]:
    sid, paper = await _seed_spec()
    candles = make_synthetic_candles(300)
    llm = _CapturingLLM(_always_short)
    _patch(monkeypatch, candles, llm, forced_verdict=forced_verdict)
    runner = AiBacktestRunner(_spec(sid, paper, candles, settings))
    job = _BacktestJob("aibt_trend", runner)
    await runner.run(job)
    assert job.state == "completed", job.error
    return llm, job.result


async def test_backtest_arms_both_prompt_sides_and_the_gate(monkeypatch):
    """轴 F 的四处接线：第七段宪章 + S1/S2/S12（**system 侧**）、基调段（**messages 侧**）、
    门禁与 `gate_reason` 九元合成（结果侧）。"""
    try:
        settings = Settings(_env_file=None, ai_backtest_max_rounds=400,
                            ai_trade_reasoning_depth_backtest="standard")
        llm, result = await _run(monkeypatch, settings, forced_verdict=True)
        assert llm.calls > 0
        system = llm.systems[0]
        # ① 第七段宪章进了 system prompt（实盘与回测同源，两处只填一处就分叉）。
        assert "趋势基调条款" in system
        # ② 《审议协议》的 S1 被整体替换为「基调先行」。
        assert "First state the trend verdict for the execution bar 1H" in system
        # ③ 基调段进了 messages 侧的决策 prompt——**必须两侧都断言**（RK-8）。
        assert any("【本轮趋势基调" in text for text in llm.user_texts)
        # ④ 被基调闸拦下的轮**带降级码**（评审 RP1-9 的九元合成护栏）：模型每轮都提议开空且
        #    自报置信度 0.05，逆势轮必然撞上置信度下界。
        digest = result.get("decisionsDigest") or []
        degrades = [row.get("degradeReason") or "" for row in digest]
        assert any(text.startswith("counter_trend_") for text in degrades), degrades
        # 被拦轮的最终动作必须是 hold（闸只会更保守，绝不改方向、绝不放大）。
        blocked = [row for row in digest
                   if str(row.get("degradeReason") or "").startswith("counter_trend_")]
        assert all(row["action"] == "hold" for row in blocked)
    finally:
        await dispose_engine()


async def test_backtest_master_switch_reverts_both_sides(monkeypatch):
    """回测独立总闸：关掉 ⇒ 两段 prompt 与 payload **全部**逐字回退（实盘侧不受影响）。"""
    try:
        settings = Settings(_env_file=None, ai_backtest_max_rounds=400,
                            ai_trade_reasoning_depth_backtest="standard",
                            ai_trade_trend_verdict_backtest_enabled=False)
        llm, _ = await _run(monkeypatch, settings)
        system = llm.systems[0]
        assert "趋势基调条款" not in system
        assert "First state the trend verdict" not in system
        assert all("【本轮趋势基调" not in text for text in llm.user_texts)
    finally:
        await dispose_engine()


def test_backtest_notes_disclose_the_tactical_gap():
    """§3.8 / 评审 RP2-4：回测 payload 结构性没有战术周期证据，**必须如实披露**。

    把一条结构性不可达的差异说成「同构」，会让用户拿回测里的多空比去推断实盘。
    """
    source = inspect.getsource(AiBacktestRunner._trend_notes)
    # ⚠️ 断言**不得**写出 `tacticalContext` 这个字面 key：`test_ai_backtest_runner.py` 的 G-2 是
    # 一道**源码字面扫描**门禁（`backtest.py` 全文不得出现那几个键名），本轮的披露文案因此改用
    # 中文说法。照 camelCase 去断言，等于逼着实现把那个 key 写回 `backtest.py`、把 G-2 弄红。
    assert "战术周期证据段" in source and "比实盘略宽" in source
    assert "同一个纯核心" in source


def test_gate_reason_chain_has_nine_terms_in_the_frozen_order():
    """评审 RP1-9：`gate_reason` 是**九元**合成，且 `trend_reason` 在刹车之后、共识之前。

    位置与实盘 `degrade or reason` 的链序、以及 `open_funnel.LAYERS` 的层序逐字同构。
    """
    source = inspect.getsource(AiBacktestRunner)
    head = source.index("gate_reason = (")
    chain = source[head:source.index(")", head)]
    order = [name for name in ("base_reason", "horizon_reason", "cooldown_reason",
                               "turnover_reason", "backstop_reason", "brake_reason",
                               "trend_reason", "consensus_reason", "edge_reason")
             if name in chain]
    assert order == ["base_reason", "horizon_reason", "cooldown_reason", "turnover_reason",
                     "backstop_reason", "brake_reason", "trend_reason", "consensus_reason",
                     "edge_reason"]
