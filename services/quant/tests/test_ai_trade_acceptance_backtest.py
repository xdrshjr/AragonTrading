"""回测侧的验收闭环接线（ai-trade-horizon-acceptance-loop §6.1 W-5 / W-6 / W-11）。

守的核心是一条连坐关系：**资金费与退出来源不得挂在周期模式的开关下**。
`_track_horizon_epoch` 首行就是 `if self.horizon_rule is None: return`，挂在它里面会让「装 vs
不装周期模式的对照回测」——上线观察窗口 L1 必做的那一臂——拿不到任何资金费与退出分布。
"""
from __future__ import annotations

import json

from sqlalchemy import select

from app.agent.llm.base import StreamEvent
from app.ai_auto.backtest import AiBacktestRunner, AiBacktestSpec, _BacktestJob
from app.ai_auto.hold_horizon import HorizonRule
from app.config import Settings
from app.db import repositories as repo
from app.db.models import Strategy
from app.db.session import dispose_engine, get_sessionmaker, init_db
from app.paper.strategy_factory import build_paper_strategy_spec

from tests.conftest import make_synthetic_candles

_RULE = HorizonRule(profile_id="intraday_swing", title="日内波段",
                    min_hold_s=7200.0, exit_confirm_rounds=2, confirm_window_s=3600.0,
                    hysteresis_k=1.5, target_hold_min_s=14400.0,
                    target_hold_max_s=43200.0, expected_hold_bars=12)


class _StubLLM:
    """开一根、下一根就想平——正是本轮要观测的那种抢跑退出模式。"""

    def __init__(self):
        self.calls = 0

    async def stream(self, *, system, messages, tools):
        if not tools:
            yield StreamEvent("text_delta", text="摘要")
            yield StreamEvent("message_done", stop_reason="end")
            return
        self.calls += 1
        args = ({"action": "open_long", "sizeFraction": 1.0, "confidence": 0.8,
                 "reason": "r"} if self.calls % 2 else {"action": "close", "reason": "r"})
        yield StreamEvent("tool_use_start", id="t", name="submit_trade_decision")
        yield StreamEvent("tool_use_args_delta", id="t", partial_json=json.dumps(args))
        yield StreamEvent("message_done", stop_reason="tool_use")


class _StubRest:
    """公共历史费率端点的可注入替身；`rows=None` ⇒ 模拟取不到曲线。"""

    def __init__(self, rows: list[dict] | None):
        self.rows = rows
        self.calls = 0

    async def get_funding_rate_history(self, inst_id, *, before_ms=None, after_ms=None,
                                       limit=100):
        self.calls += 1
        if self.rows is None:
            raise RuntimeError("public endpoint unavailable")
        return list(self.rows) if self.calls == 1 else []


def _curve_rows(candles: list[dict]) -> list[dict]:
    """按 8h 一个结算点铺一条覆盖整段回测区间的真实费率曲线。"""
    start, end = int(candles[0]["ts"]), int(candles[-1]["ts"])
    return [{"fundingTs": ts, "fundingRate": 0.0001, "realizedRate": 0.0001}
            for ts in range(start, end + 1, 28800)]


async def _seed_spec():
    await init_db()
    async with get_sessionmaker()() as s:
        sid = (await s.execute(
            select(Strategy.id).order_by(Strategy.id.asc()).limit(1))).scalar_one()
        row = await repo.get_strategy_row(s, sid)
    return sid, build_paper_strategy_spec(row)


def _patch_loaders(monkeypatch, candles, llm):
    async def fake_range(*_a, **_k):
        return candles

    async def fake_htf(*_a, **_k):
        return {}

    monkeypatch.setattr("app.ai_auto.backtest.load_range_candles", fake_range)
    monkeypatch.setattr("app.ai_auto.backtest.load_htf_frames", fake_htf)
    monkeypatch.setattr("app.ai_auto.backtest.make_llm_client_from_config",
                        lambda cfg: llm)


async def _run(monkeypatch, *, horizon_rule, rows: list[dict] | None, settings=None):
    sid, paper = await _seed_spec()
    candles = make_synthetic_candles(200)
    settings = settings or Settings(ai_trade_funding_enabled=True)
    _patch_loaders(monkeypatch, candles, _StubLLM())
    rest = _StubRest(_curve_rows(candles) if rows == "auto" else rows)
    spec = AiBacktestSpec(
        inst_id="BTC-USDT-SWAP", bar="1H", strategy_ids=[sid], strategy_names=["S"],
        specs=[paper], start_ts=candles[60]["ts"], end_ts=candles[-1]["ts"],
        decision_every_bars=2, initial_capital=10000.0, leverage=3.0,
        stop_loss_pct=0.05, take_profit_pct=0.1, take_profit_usd=None,
        stop_loss_usd=None, context_refresh_rounds=50, min_consensus=0,
        planned_rounds=20, settings=settings, rest=rest, provider=object(),
        horizon_rule=horizon_rule)
    runner = AiBacktestRunner(spec)
    await runner.run(_BacktestJob("aibt_acc", runner))
    return runner


async def test_funding_and_exits_survive_without_a_horizon_rule(monkeypatch):
    """W-11 [P1-4]：未装配周期模式的对照回测里，资金费与退出分布**照常产出**。"""
    try:
        runner = await _run(monkeypatch, horizon_rule=None, rows="auto")
        stats = runner._aggregate_stats()
        assert stats["horizon"] is None                 # 周期层确实没装
        assert stats["horizonAcceptance"] is None       # 没有契约就没有验收卡
        assert stats["funding"] is not None             # 但资金费不被连坐
        assert stats["funding"]["basis"] != "unavailable"
        assert stats["exits"]["totalExits"] > 0         # 退出分布同样不被连坐
    finally:
        await dispose_engine()


async def test_acceptance_card_is_built_when_the_rule_is_armed(monkeypatch):
    """W-5：装了周期契约的回测出一张与实盘**逐键同形**的卡（同一个 builder）。"""
    try:
        runner = await _run(monkeypatch, horizon_rule=_RULE, rows="auto")
        stats = runner._aggregate_stats()
        card = stats["horizonAcceptance"]
        assert card is not None
        assert [row["key"] for row in card["rows"]] == [
            "avgHold", "withinTargetRate", "turnoverPerDay", "feeToGross",
            "netAfterFunding"]
        assert card["exitMix"] is stats["exits"]        # 同一对象，不得二次装配
        assert card["funding"] is stats["funding"]
        assert stats["acceptanceInput"]["roundTrips"] >= 1
    finally:
        await dispose_engine()


async def test_unavailable_curve_is_reported_as_such_not_as_zero(monkeypatch):
    """W-6：取不到费率曲线 ⇒ `basis == "unavailable"`、`fundingCostUsd` **不是 0**、notes 免责。"""
    try:
        runner = await _run(monkeypatch, horizon_rule=_RULE, rows=None)
        stats = runner._aggregate_stats()
        assert stats["funding"]["basis"] == "unavailable"
        assert stats["funding"]["fundingCostUsd"] is None
        notes = runner._build_notes(__import__("pandas").DataFrame({"ts": [0], "c": [1.0]}), 0)
        assert any("未能取到历史资金费曲线" in n for n in notes)
        # 第 5 行必须判样本不足而不是通过。
        row = next(r for r in stats["horizonAcceptance"]["rows"]
                   if r["key"] == "netAfterFunding")
        assert row["verdict"] == "insufficient"
    finally:
        await dispose_engine()


async def test_backtest_funding_never_touches_the_equity_curve(monkeypatch):
    """§2.2.4 第 4 点：资金费只进 `aiStats.funding` 与验收卡，**绝不**改权益曲线与 metrics。

    `SimBroker` 被全仓库所有回测共用，在其中扣资金费会改变**每一个**既有策略回测的结果。
    """
    try:
        with_curve = await _run(monkeypatch, horizon_rule=_RULE, rows="auto")
        equity_with = [round(e.equity, 8) for e in with_curve.main.equity_points]
        funding = with_curve._aggregate_stats()["funding"]
    finally:
        await dispose_engine()
    try:
        without = await _run(monkeypatch, horizon_rule=_RULE, rows=None)
        equity_without = [round(e.equity, 8) for e in without.main.equity_points]
    finally:
        await dispose_engine()
    assert funding["basis"] != "unavailable"     # 前一次确实计提了资金费
    assert equity_with == equity_without         # 而权益曲线逐点不变


async def test_exit_sources_cover_agent_and_deterministic_reasons(monkeypatch):
    """轴 B 的回测打标真的落到位：Agent 主动平仓与区间收尾平仓都能被认出来。"""
    try:
        runner = await _run(monkeypatch, horizon_rule=_RULE, rows="auto")
        sources = {row["source"] for row in runner._aggregate_stats()["exits"]["bySource"]}
        assert "unknown" not in sources, "有平仓成交没能定源——打标槽或 reason 映射漏了一条"
        assert sources & {"agent", "windowEnd", "exchangeOco", "timeExit"}
    finally:
        await dispose_engine()


async def test_wall_seconds_starts_at_the_first_decision_bar(monkeypatch):
    """[P1-3]：回测的墙钟分母是**首决策根 → 末根**，暖机段不算（否则换手率被稀释成恒过）。"""
    try:
        runner = await _run(monkeypatch, horizon_rule=_RULE, rows="auto")
        candles = make_synthetic_candles(200)
        full_span = float(candles[-1]["ts"] - candles[0]["ts"])
        assert runner._wall_seconds < full_span
        assert runner._wall_seconds > 0
    finally:
        await dispose_engine()
