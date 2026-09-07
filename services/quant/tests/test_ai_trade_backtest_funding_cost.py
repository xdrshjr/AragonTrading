"""轴 B · 回测净额真值（ai-trade-backstop-repair-and-net-backtest §2.3 / §6.1 F 组）。

守的是一条会**污染验收路径**的偏差：回测的记账自上一轮起已含真实资金费，但成本估算的两个入口
（`_horizon_facts` → G3 换手迟滞、`evaluate_edge_gate` → 最小 edge 门禁）仍硬传
`funding_enabled=False`。于是回测按「资金费为 0」判定、实盘按真实资金费判定——**回测系统性地比
实盘乐观，而且模式越长越乐观**，拿它去挑周期模式，挑出来的一定是最长的那个。

三条不可动摇的判据在本文件里各有守门用例：
1. **无未来函数**（F-1~3）：`snapshot_at` 只取 `ts <= 目标` 的最后一点。
2. **曲线不可用即逐字回退 `(None, False)`**（F-5~7，§九 D-8）：`(None, True)` 在 `cost_model` 里
   是「数据缺失 ⇒ degraded ⇒ 门禁收紧」，那会让一次观测层的网络抖动静默改变回测判定。
3. **notes 必须自认口径**（F-12~13b）：旧那句「已知欠账：成本估算仍不含资金费」在本轮之后是假话，
   必须被撤下——一条在事实变化后没被撤下的免责，比没有免责更坏。
"""
from __future__ import annotations

import json

from sqlalchemy import select

from app.agent.llm.base import StreamEvent
from app.ai_auto.backtest import AiBacktestRunner, AiBacktestSpec, _BacktestJob
from app.ai_auto.cost_model import (
    backtest_params,
    estimate_round_trip_cost,
    funding_periods,
    query_for_preview,
)
from app.ai_auto.funding import FundingSnapshot
from app.ai_auto.funding_ledger import BacktestFundingCurve
from app.ai_auto.hold_horizon import HorizonRule, derive_expected_hold_bars
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

_BAR_SECONDS = 3600


class _StubLLM:
    """开一根、下一根就想平——本特性链要观测的那种抢跑退出模式。"""

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


def _curve_rows(candles: list[dict], *, rate: float = 0.0001) -> list[dict]:
    """按 8h 一个结算点铺一条覆盖整段回测区间的真实费率曲线。"""
    start, end = int(candles[0]["ts"]), int(candles[-1]["ts"])
    return [{"fundingTs": ts, "fundingRate": rate, "realizedRate": rate}
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


async def _run(monkeypatch, *, settings, rows="auto", horizon_rule=_RULE):
    sid, paper = await _seed_spec()
    candles = make_synthetic_candles(200)
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
    await runner.run(_BacktestJob("aibt_fc", runner))
    return runner


def _settings(**over) -> Settings:
    return Settings(ai_trade_funding_enabled=True, ai_trade_funding_ledger_enabled=True,
                    **over)


def _fingerprint(runner: AiBacktestRunner) -> tuple:
    """一次回测的可对拍指纹：全部 metrics + 逐轮降级码序列（判定链是否改变的唯一证据）。"""
    metrics = runner.main.metrics() if hasattr(runner.main, "metrics") else None
    return (metrics,
            tuple(round(e.equity, 8) for e in runner.main.equity_points),
            tuple(d.get("degradeReason") for d in runner._decisions_digest),
            tuple((t.side, round(float(t.pnl or 0.0), 8)) for t in runner.main.trades))


# ==================== F-1~4 · `snapshot_at` 纯查表 ====================
def test_snapshot_at_never_reads_the_future():
    """F-1：只取 `ts <= 目标` 的最后一点——回测里唯一不可饶恕的错误是未来函数。"""
    curve = BacktestFundingCurve([{"fundingTs": 1000, "fundingRate": 0.0001},
                                  {"fundingTs": 29800, "fundingRate": 0.0009}])
    assert curve.snapshot_at(29799) is None  # 第二点尚不可见，周期不可推。
    assert curve.snapshot_at(29800).rate == 0.0009      # 恰好可见（闭区间）
    assert curve.snapshot_at(99999) is None  # 历史前缀已经过期。


def test_snapshot_at_returns_none_before_the_first_point():
    """F-2：曲线起点之前没有可见费率 —— 绝不拿未来那一点补上（[P2-1] 覆盖计数的成因）。"""
    curve = BacktestFundingCurve([{"fundingTs": 1000, "fundingRate": 0.0001},
                                  {"fundingTs": 29800, "fundingRate": 0.0002}])
    assert curve.snapshot_at(999) is None


def test_snapshot_at_requires_two_points():
    """F-3：单点 / 空曲线 ⇒ `None`（单点算不出周期，算不出周期就表达不了「几期」）。"""
    assert BacktestFundingCurve([{"fundingTs": 1, "fundingRate": 0.1}]).snapshot_at(9) is None
    assert BacktestFundingCurve([]).snapshot_at(9) is None


def test_snapshot_at_reports_historical_observed_interval():
    """F-4：历史间隔可观察，但下一次安排没有获得交易所确认。"""
    curve = BacktestFundingCurve([{"fundingTs": 3600, "fundingRate": 0.0001},
                                  {"fundingTs": 18000, "fundingRate": 0.0002}])
    snap = curve.snapshot_at(20000)
    assert isinstance(snap, FundingSnapshot)
    assert snap.interval_s == curve.interval_hint == 14400.0
    assert snap.interval_known is False
    assert snap.interval_source == "historical_observed"
    assert snap.next_funding_ts == 18000 + 14400


# ==================== F-5~7 · `_cost_funding` 与等价门禁 ====================
async def test_cost_funding_falls_back_when_the_master_switch_is_off(monkeypatch):
    """F-5：运维总闸关 ⇒ `(None, False)`——「有意不建模」，与本轮之前逐字节相同。"""
    try:
        runner = await _run(monkeypatch,
                            settings=_settings(ai_trade_funding_cost_backtest_enabled=False))
        assert runner._cost_funding(runner.end_ts) == (None, False)
        assert runner._funding_cost_covered == 0
    finally:
        await dispose_engine()


async def test_cost_funding_falls_back_when_the_curve_is_unavailable(monkeypatch):
    """F-6：按 v2 设计有意修订为缺失仍开启，沿既有门禁降级。"""
    try:
        runner = await _run(monkeypatch, settings=_settings(), rows=None)
        assert runner._curve.available is False
        assert runner._cost_funding(runner.end_ts) == (None, True)
    finally:
        await dispose_engine()


async def test_master_switch_off_is_byte_identical_to_the_pre_round_path(monkeypatch):
    """F-7 等价门禁：总闸关的一次回测 ≡ 把 `_cost_funding` 换成本轮之前那条硬编码路径。

    对拍口径是 metrics + 权益曲线 + **逐轮降级码序列** + 成交序列——只比 pnl 是看不出门禁判定
    有没有漂移的（G3 变严会改变交易序列，而交易序列变化后 pnl 的方向推不出来，[P2-2]）。
    """
    off = _settings(ai_trade_funding_cost_backtest_enabled=False)
    try:
        baseline = _fingerprint(await _run(monkeypatch, settings=off))
    finally:
        await dispose_engine()
    monkeypatch.setattr(AiBacktestRunner, "_cost_funding",
                        lambda self, ts: (None, False))     # 本轮之前的那条路径
    try:
        legacy = _fingerprint(await _run(monkeypatch, settings=_settings()))
    finally:
        await dispose_engine()
    assert baseline == legacy


# ==================== F-8~9 · 口径方向（与 `_horizon_facts` 同一对纯核心） ====================
def test_funding_makes_the_round_trip_cost_strictly_higher_for_a_paying_side():
    """F-8：总闸开且曲线可用 ⇒ 多头（付费方向）的 `cost_bps` **严格大于**关闭时。

    这里直接跑 `_horizon_facts` 用的那**同一对**纯核心（`query_for_preview` +
    `estimate_round_trip_cost`），而不是复制一遍公式——两处各算一份正是本仓库反复出事的形态。
    """
    curve = BacktestFundingCurve([{"fundingTs": 3600, "fundingRate": 0.0001},
                                  {"fundingTs": 32400, "fundingRate": 0.0001}])
    params = backtest_params(_settings(), expected_hold_bars=24)
    on = estimate_round_trip_cost(
        query_for_preview(ord_type="market", side="long", bar_seconds=_BAR_SECONDS,
                          funding=curve.snapshot_at(40000), funding_enabled=True), params)
    off = estimate_round_trip_cost(
        query_for_preview(ord_type="market", side="long", bar_seconds=_BAR_SECONDS,
                          funding=None, funding_enabled=False), params)
    assert on.total_bps > off.total_bps
    assert on.confidence == "degraded"  # 有历史值，但下一次安排未确认。
    assert on.funding_carry_bps > 0


def test_a_positive_rate_costs_a_short_position_nothing():
    """F-9：空头 + 正费率 ⇒ `funding_carry_bps == 0`（**只取不利侧**，绝不给 edge 折扣）。"""
    curve = BacktestFundingCurve([{"fundingTs": 3600, "fundingRate": 0.0001},
                                  {"fundingTs": 32400, "fundingRate": 0.0001}])
    cost = estimate_round_trip_cost(
        query_for_preview(ord_type="market", side="short", bar_seconds=_BAR_SECONDS,
                          funding=curve.snapshot_at(40000), funding_enabled=True),
        backtest_params(_settings(), expected_hold_bars=24))
    assert cost.funding_carry_bps == 0.0


# ==================== F-10~11 · edge 门禁那一半（[P1-9] 必须先把 k_min 调正） ====================
async def test_edge_gate_receives_the_real_funding_snapshot(monkeypatch):
    """F-10 [P1-9]：**先把 `k_min` 设成正数**，再断言 edge 门禁的两个入参真的换了。

    默认 `ai_trade_edge_gate_k_min == 0.0`，而 `evaluate_edge_gate` 第一句就是
    `k_min <= 0 ⇒ return None`——不设正值的话这条是一句恒过的空用例。
    """
    seen: list[dict] = []
    real = __import__("app.ai_auto.backtest", fromlist=["evaluate_edge_gate"]).evaluate_edge_gate

    def spy(decision, **kwargs):
        seen.append(kwargs)
        return real(decision, **kwargs)

    monkeypatch.setattr("app.ai_auto.backtest.evaluate_edge_gate", spy)
    try:
        await _run(monkeypatch, settings=_settings(ai_trade_edge_gate_k_min=0.5))
    finally:
        await dispose_engine()
    assert seen, "edge 门禁一次都没被调用——用例本身失效了"
    assert any(k["funding_enabled"] is True and isinstance(k["funding"], FundingSnapshot)
               for k in seen)


async def test_edge_gate_funding_input_preserves_historical_uncertainty(monkeypatch):
    """F-11：喂进门禁的历史快照仍保留安排未确认的降级。"""
    seen: list[dict] = []
    real = __import__("app.ai_auto.backtest", fromlist=["evaluate_edge_gate"]).evaluate_edge_gate

    def spy(decision, **kwargs):
        seen.append(kwargs)
        return real(decision, **kwargs)

    monkeypatch.setattr("app.ai_auto.backtest.evaluate_edge_gate", spy)
    settings = _settings(ai_trade_edge_gate_k_min=0.5)
    try:
        await _run(monkeypatch, settings=settings)
    finally:
        await dispose_engine()
    armed = next(k for k in seen if k["funding"] is not None)
    cost = estimate_round_trip_cost(
        query_for_preview(ord_type="market", side="long", bar_seconds=_BAR_SECONDS,
                          funding=armed["funding"], funding_enabled=armed["funding_enabled"]),
        armed["params"])
    assert cost.confidence == "degraded"


# ==================== F-12~13b · notes 自认口径（宿主是 `_funding_notes`） ====================
async def test_funding_notes_declare_the_armed_cost_basis(monkeypatch):
    """F-12：含资金费的一臂必须报出**结算周期、期数与覆盖轮数**三个数。

    「按 8h 假设」这句话本轮之后不该再出现在任何一份回测结论里。
    """
    try:
        runner = await _run(monkeypatch, settings=_settings())
        notes = runner._funding_notes()
    finally:
        await dispose_engine()
    basis = notes[0]
    assert "往返成本估算已含资金费" in basis
    assert "按最近历史间隔估计" in basis
    assert f"覆盖 {runner._funding_cost_covered}/{runner._funding_cost_rounds} 个决策轮" in basis
    assert "轮降级" in basis
    # 反向冻结：本轮之后那句欠账披露是假话，**必须已被撤下**。
    assert not any("已知欠账" in n for n in notes)
    assert not any("成本估算仍不含资金费" in n for n in notes)


async def test_funding_notes_declare_the_fallback_and_its_reason(monkeypatch):
    """F-13：回退臂必须说清**为什么**不含（总闸关 / 曲线不可得），并写明方向是「比实盘宽松」。"""
    try:
        off = await _run(monkeypatch,
                         settings=_settings(ai_trade_funding_cost_backtest_enabled=False))
        off_note = off._funding_notes()[0]
    finally:
        await dispose_engine()
    try:
        blind = await _run(monkeypatch, settings=_settings(), rows=None)
        blind_note = blind._funding_notes()[0]
    finally:
        await dispose_engine()
    assert "**不含**资金费" in off_note and "已关闭" in off_note
    assert "资金费证据缺失" in blind_note and "历史资金费曲线不可得" in blind_note
    assert "保持开启并降级" in blind_note
    assert "不得据此为长周期模式背书" in off_note


async def test_a_curve_outside_the_range_says_so_instead_of_blaming_the_fetch(monkeypatch):
    """曲线在手、但本次区间的决策轮**全落在它起点之前** ⇒ 必须分开说，不得报成「曲线不可得」。

    这是 [P2-1] 的同一条：一个全局布尔说不清「起点之前那几根本来就没有可见费率」这件事。而把它
    渲染成一次取数失败更贵——它会让人先去查网络、再去怀疑总闸，**一条指错方向的免责比没有免责更坏**。
    """
    future = [{"fundingTs": 4_000_000_000 + i * 28_800, "fundingRate": 0.0001,
               "realizedRate": 0.0001} for i in range(3)]
    try:
        runner = await _run(monkeypatch, settings=_settings(), rows=future)
        note = runner._funding_notes()[0]
        assert runner._curve.available is True        # 曲线拿到了，只是不覆盖本区间
        assert runner._funding_cost_covered == 0
    finally:
        await dispose_engine()
    assert "资金费证据缺失" in note
    assert "没有任何决策轮读到可见的历史结算点" in note
    assert "历史资金费曲线不可得" not in note


async def test_cost_basis_note_survives_the_control_arm(monkeypatch):
    """F-13b [P1-6]：未装配周期契约的**对照臂**同样要有口径句。

    它挂在 `_funding_notes()` 而不是 `hold_horizon_text.format_backtest_notes`——后者第一句就是
    `rule is None ⇒ []`，而对照臂恰恰是验收时最需要对拍的那条曲线。
    """
    try:
        runner = await _run(monkeypatch, settings=_settings(), horizon_rule=None)
        notes = runner._funding_notes()
    finally:
        await dispose_engine()
    assert any("往返成本估算" in n for n in notes)


async def test_edge_gate_clause_is_conditional_on_k_min(monkeypatch):
    """[P1-9]：`k_min == 0`（默认）时 notes **不得**宣称 edge 门禁也据此判定——那是一句没核实的承诺。"""
    try:
        default = await _run(monkeypatch, settings=_settings())
        assert "edge 门禁" not in default._funding_notes()[0]
    finally:
        await dispose_engine()
    try:
        armed = await _run(monkeypatch, settings=_settings(ai_trade_edge_gate_k_min=0.5))
        assert "与最小 edge 门禁" in armed._funding_notes()[0]
    finally:
        await dispose_engine()


# ==================== F-14~16 · 期数随契约变化（不回退全局常数） ====================
def test_long_horizon_crosses_multiple_settlement_points():
    """F-14：`trend_hold`（1–3 天 / 4H 执行）跨 ≥2 个结算点——这正是回测此前系统性乐观的量级。"""
    bars = derive_expected_hold_bars(259200.0, "4H")
    query = query_for_preview(ord_type="market", side="long", bar_seconds=14400,
                              funding=FundingSnapshot(rate=0.0001, funding_ts=0,
                                                      next_funding_ts=28800, interval_s=28800.0,
                                                      fetched_wall=0.0, interval_known=True),
                              funding_enabled=True)
    assert funding_periods(query, backtest_params(_settings(), expected_hold_bars=bars)) >= 2


def test_hourly_swing_stays_at_one_settlement_point():
    """F-15：`hourly_swing`（6m–2h / 5m 执行）恒 1 期——短周期模式下这条口径差别可以忽略。"""
    bars = derive_expected_hold_bars(7200.0, "5m")
    query = query_for_preview(ord_type="market", side="long", bar_seconds=300,
                              funding=FundingSnapshot(rate=0.0001, funding_ts=0,
                                                      next_funding_ts=28800, interval_s=28800.0,
                                                      fetched_wall=0.0, interval_known=True),
                              funding_enabled=True)
    assert funding_periods(query, backtest_params(_settings(), expected_hold_bars=bars)) == 1


async def test_expected_hold_bars_comes_from_the_contract(monkeypatch):
    """F-16：前瞻窗口取自契约而非全局常数 `ai_trade_cost_expected_hold_bars`（默认 8）。"""
    try:
        armed = await _run(monkeypatch, settings=_settings())
        assert armed._horizon_hold_bars == _RULE.expected_hold_bars != 8
    finally:
        await dispose_engine()
    try:
        control = await _run(monkeypatch, settings=_settings(), horizon_rule=None)
        assert control._horizon_hold_bars is None      # 未装配 ⇒ 逐字回退全局常数
    finally:
        await dispose_engine()
