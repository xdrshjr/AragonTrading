"""成本记账与最小 edge 门禁的会话级集成测试（ai-trade-cost-accounting-and-edge-gate §7.3）。

脱网、桩回调、桩 LLM。重点把守四件在纯函数单测里看不见的事：
1. **插入位序**：门禁落在共识之后、反思之前 ⇒ 被拦轮的 `decider.reflect` 调用次数**为 0**
   （门禁本身就是省钱项，这是可证伪断言）；
2. **优先级合成**：铁律与 edge 同时否决 ⇒ 报**铁律**的码（先手胜出）；
3. **统计合并落点**（RV-1）：新键只在 `_finalize_run` 的调用点出现，`_compute_ai_stats` 本身
   **不含**新键——这条能证伪「改了函数内部」；
4. **零 IO 保证**：决策轮期间桩 funding / fills 的调用次数恒为 0。
"""
from __future__ import annotations

import asyncio
import json

from app.agent.llm.base import StreamEvent
from app.ai_auto.base_rules import MaBandRule
from app.ai_auto.decider import AiTradeDecider
from app.ai_auto.session import _compute_ai_stats, AiTradeSession
from app.config import Settings
from app.db import repositories as repo
from app.db.session import dispose_engine, get_sessionmaker, init_db
from app.engine.simulator import RiskParams
from app.live_auto.executor import LiveExecutor
from app.live_auto.fee_ledger import build_fee_ledger
from app.okx.accounts import AccountCredentials
from app.okx.types import Candle
from app.paper.strategy_factory import PaperStrategySpec
from app.strategies.base import BaseStrategy, StrategyMeta

_START_TS = 1_700_000_000
_STEP = 3600
_PX = 30_000.0


class _StubStrategy(BaseStrategy):
    meta = StrategyMeta(key="_stub_cost_gate", name="Stub", description="t",
                        default_params={}, params_schema={})

    def warmup(self) -> int:
        return 1

    def on_candle(self, df, i, ctx):
        return []


class _StubLLM:
    def __init__(self, action="open_long"):
        self.action = action

    async def stream(self, *, system, messages, tools):
        if tools:
            args = {"action": self.action, "reason": "r", "confidence": 0.7}
            if self.action in ("open_long", "open_short"):
                args["sizeFraction"] = 0.5
            yield StreamEvent("tool_use_start", id="t", name="submit_trade_decision")
            yield StreamEvent("tool_use_args_delta", id="t",
                              partial_json=json.dumps(args, ensure_ascii=False))
            yield StreamEvent("message_done", stop_reason="tool_use")
        else:
            yield StreamEvent("text_delta", text="摘要")
            yield StreamEvent("message_done", stop_reason="end")


def _candle(idx: int, price: float = _PX) -> Candle:
    return Candle(ts=_START_TS + idx * _STEP, o=price, h=price + 5, l=price - 5,
                  c=price, vol=100.0, confirm=True)


def _gate_settings(**over) -> Settings:
    """打开门禁的运行配置（k_min=2.0；资金费率总闸关 ⇒ 成本置信度 ok、零网络）。"""
    base = {"ai_trade_edge_gate_k_min": 2.0, "ai_trade_funding_enabled": False}
    base.update(over)
    return Settings(**base)


def _make_session(*, settings=None, action="open_long", run_id=1, base_rule=None,
                  risk=None, with_ledger=True):
    settings = settings or Settings()
    io_calls = {"funding": 0, "fills": 0, "reflect": 0}

    async def place_order_fn(order):
        return {"ok": True, "ordId": "x", "clOrdId": "aragonx"}

    async def close_position_fn(close):
        return {"ok": True, "clOrdId": "aragoncx"}

    async def positions_fn():
        ex = session.executor
        if ex.projected == "flat":
            return []
        return [{"instId": "BTC-USDT-SWAP", "pos": ex.entry_qty_sz, "upl": 0.0}]

    async def fills_fn():
        io_calls["fills"] += 1
        return []

    async def funding_fn(_inst_id):
        io_calls["funding"] += 1
        return {"fundingRate": 0.0001, "fundingTs": 1, "nextFundingTs": 28801,
                "intervalS": 28800.0}

    executor = LiveExecutor(
        creds=AccountCredentials("ak", "sk-000000", "pp-000000"), account_id=1,
        inst_id="BTC-USDT-SWAP", td_mode="cross", pos_mode="long_short_mode",
        leverage=3.0, budget_usd=1000.0, risk=risk or RiskParams(), ct_val=0.01,
        lot_sz=0.01, min_sz=0.01, settings=settings, place_order_fn=place_order_fn,
        close_position_fn=close_position_fn, positions_fn=positions_fn, fills_fn=fills_fn,
        fee_ledger=build_fee_ledger(settings, ct_val=0.01) if with_ledger else None)
    decider = AiTradeDecider(_StubLLM(action), "sys", context_refresh_rounds=50,
                             settings=settings)

    async def counting_reflect(*args, **kwargs):
        io_calls["reflect"] += 1
        raise AssertionError("被门禁拦下的轮次不应触发反思")

    decider.reflect = counting_reflect
    spec = PaperStrategySpec(1, "S", "atomic", _StubStrategy({}), {})
    session = AiTradeSession("aitrade_cost", run_id, [spec], "BTC-USDT-SWAP", "1H", executor,
                             decider, priming=False, priming_bars=10,
                             context_refresh_rounds=50, base_rule=base_rule)

    async def no_htf(*_a, **_k):
        return []

    session._window._safe_get_candles = no_htf
    # 资金费率取数桩：绝不触网；调用计数用于「决策轮内零 IO」的可证伪断言。
    session._cost.refresh_funding = lambda now: session._cost.funding.refresh(funding_fn, now)
    return session, io_calls


async def _settle(session) -> None:
    task = session._decision_task
    if task is not None:
        try:
            await task
        except asyncio.CancelledError:
            pass


def _drain(q: asyncio.Queue) -> list[dict]:
    out = []
    while not q.empty():
        out.append(q.get_nowait())
    return out


def _traces(events, phase: str) -> list[dict]:
    return [e["data"] for e in events
            if e["type"] == "agent_trace" and e["data"]["phase"] == phase]


def _pin_atr(monkeypatch, value: float | None) -> None:
    """钉住确认根 ATR：`feed_candle` 每根都会重算 `_atr_px`，直接赋值会被下一根覆盖。"""
    monkeypatch.setattr("app.ai_auto.session.safe_atr", lambda *_a, **_k: value)


async def _run_metrics(sm, run_id: int) -> dict:
    async with sm() as session:
        run = await repo.get_run(session, run_id)
    return json.loads(run.metrics_json or "{}")


# ---------- 用例 1：插入位序 + 被拦轮零反思 ----------
async def test_edge_veto_lands_as_hold_without_calling_reflection(monkeypatch):
    await init_db()
    session, io_calls = _make_session(settings=_gate_settings())
    _pin_atr(monkeypatch, 1.0)                   # 1 / 30000 ≈ 0.33bps ≪ 2 × 13bps 阈值
    try:
        q = session.subscribe()
        await session.feed_candle(_candle(0))
        await _settle(session)
        decision = [e["data"] for e in _drain(q) if e["type"] == "decision"][-1]
        assert decision["action"] == "hold"
        assert decision["degradeReason"].startswith("edge_below_cost(")
        assert io_calls["reflect"] == 0          # 门禁省钱项：被拦轮零反思 LLM 调用
        assert session.executor.projected == "flat"
    finally:
        await dispose_engine()


async def test_sufficient_edge_passes_the_gate_and_opens(monkeypatch):
    await init_db()
    session, _ = _make_session(settings=_gate_settings())
    _pin_atr(monkeypatch, 500.0)                 # ≈167bps ≫ 26bps 阈值
    try:
        q = session.subscribe()
        await session.feed_candle(_candle(0))
        await _settle(session)
        events = _drain(q)
        assert session.executor.projected == "long"
        assert _traces(events, "edge_gate")[0]["status"] == "pass"
    finally:
        await dispose_engine()


# ---------- 用例 2：优先级合成（铁律先手胜出）----------
async def test_base_rule_reason_wins_over_edge_reason(monkeypatch):
    await init_db()
    session, _ = _make_session(settings=_gate_settings(),
                               base_rule=MaBandRule(period=20))   # 单根 ⇒ 通道不可用 ⇒ fail-closed
    _pin_atr(monkeypatch, 1.0)
    try:
        q = session.subscribe()
        await session.feed_candle(_candle(0))
        await _settle(session)
        decision = [e["data"] for e in _drain(q) if e["type"] == "decision"][-1]
        assert decision["degradeReason"] == "base_rule_band_unavailable"
    finally:
        await dispose_engine()


# ---------- 用例 3：trace 事件的发/不发 ----------
async def test_edge_gate_emits_exactly_one_event_per_round_when_enabled(monkeypatch):
    await init_db()
    session, _ = _make_session(settings=_gate_settings())
    _pin_atr(monkeypatch, 500.0)
    try:
        q = session.subscribe()
        await session.feed_candle(_candle(0))
        await _settle(session)
        events = _traces(_drain(q), "edge_gate")
        assert len(events) == 1
        assert events[0]["costBps"] == 13.0 and events[0]["kMin"] == 2.0
        assert events[0]["costSnapshot"] is not None      # §5.4 会话内观测通道
    finally:
        await dispose_engine()


async def test_gate_disabled_emits_zero_events_and_is_a_passthrough(monkeypatch):
    await init_db()
    session, _ = _make_session()                 # k_min 默认 0
    _pin_atr(monkeypatch, 1.0)                   # 若门禁生效，这个 edge 必被否决
    try:
        q = session.subscribe()
        await session.feed_candle(_candle(0))
        await _settle(session)
        events = _drain(q)
        assert _traces(events, "edge_gate") == []
        assert session.executor.projected == "long"      # 空转 ⇒ 开仓照常落地
    finally:
        await dispose_engine()


# ---------- 用例 4：连续不可用告警（fail-open）----------
async def test_twenty_unavailable_rounds_alert_once_and_never_block_opens(monkeypatch):
    await init_db()
    session, _ = _make_session(settings=_gate_settings())
    _pin_atr(monkeypatch, None)                  # 估算不可用（ATR 暖机未满的真实断面）
    try:
        q = session.subscribe()
        opened = 0
        for i in range(20):
            session.executor.projected = "flat"  # 每轮回到空仓，使 open 真正被执行
            await session.feed_candle(_candle(i))
            await _settle(session)
            opened += 1 if session.executor.projected == "long" else 0
        events = _drain(q)
        alerts = [e for e in events
                  if e["type"] == "error"
                  and e["data"]["code"] == "AI_TRADE_EDGE_GATE_UNAVAILABLE"]
        assert len(alerts) == 1                  # 恰一条，不刷屏
        assert opened == 20                      # fail-open：这 20 轮开仓全部放行
        assert all(t["status"] == "unavailable" for t in _traces(events, "edge_gate"))
    finally:
        await dispose_engine()


async def test_hold_rounds_never_pollute_the_gate_counters(monkeypatch):
    """观望轮照发 trace 事件（透明），但**不进** `edgeGate` 计数、也不推进不可用连击（§十二 I-8）。"""
    await init_db()
    session, _ = _make_session(settings=_gate_settings(), action="hold")
    _pin_atr(monkeypatch, None)                  # 估算不可用，但本轮根本没有开仓意图
    try:
        q = session.subscribe()
        for i in range(3):
            await session.feed_candle(_candle(i))
            await _settle(session)
        assert len(_traces(_drain(q), "edge_gate")) == 3      # 每轮一条，不静默
        gate = session._cost.snapshot()["edgeGate"]
        assert gate == {"pass": 0, "veto": 0, "unavailable": 0, "vetoRate": None}
        assert session._cost.unavailable_streak == 0
    finally:
        await dispose_engine()


# ---------- 用例 6 / 7：归因写入 + 决策轮零 IO ----------
async def test_round_trip_attribution_and_zero_io_inside_the_decision_round(monkeypatch):
    await init_db()
    session, io_calls = _make_session(settings=_gate_settings(), action="open_long")
    _pin_atr(monkeypatch, 500.0)
    try:
        for i in range(3):
            session.executor.projected = "flat"
            await session.feed_candle(_candle(i))
            await _settle(session)
            await session._dispatch(await session.executor.apply(
                [_CloseSig()], ref_px=_PX + 100, bar_index=i, ts=_START_TS + i))
        stats = session._cost.snapshot()
        assert stats["cost"]["roundTrips"] == 3
        assert stats["cost"]["closeFeeCoverage"] == 1.0
        # 决策**输入装配**（payload / 成本 / edge）零网络 IO：资金费率恒读内存快照。
        assert io_calls["funding"] == 0
        # 实测审计挂在 `reconcile` 上，而 `_execute_decision` 也会调 reconcile（它本就是网络操作）；
        # 故它可能落在决策轮的**执行**阶段，但被 60s 节流封顶——3 轮至多 1 次（spec §十二 I-4）。
        assert io_calls["fills"] <= 1
    finally:
        await dispose_engine()


async def test_multi_symbol_group_request_count_is_bounded_by_throttle(monkeypatch):
    """用例 11（RV-11）：5 成员 / 60 秒模拟时钟 ⇒ funding 与 fills 各 ≤ 5 次。

    这是**调用次数上界**的断言，不是 429 的证伪——脱网桩测不可能证伪限流（§8 R-9）。
    """
    await init_db()
    sessions = []
    try:
        for _ in range(5):
            session, io = _make_session(
                settings=_gate_settings(ai_trade_funding_enabled=True))
            sessions.append((session, io))
        for session, _ in sessions:
            for _ in range(12):                  # 60s / 5s 盯市周期 = 12 个 tick
                await session._monitor_tick()
        assert sum(io["funding"] for _, io in sessions) <= 5
        assert sum(io["fills"] for _, io in sessions) <= 5
    finally:
        await dispose_engine()


async def test_monitor_tick_is_the_only_place_that_touches_the_network():
    await init_db()
    # 资金费率总闸开 + 门禁开 ⇒ 缓存被构造为启用态（默认 k_min=0 时它压根不发请求，见 funding 单测）。
    session, io_calls = _make_session(
        settings=_gate_settings(ai_trade_funding_enabled=True))
    try:
        await session._monitor_tick()
        assert io_calls["funding"] == 1        # 资金费率只在盯市 tick 上刷新
        await session._monitor_tick()
        assert io_calls["funding"] == 1        # TTL 去重
    finally:
        await dispose_engine()


# ---------- 用例 8 / 9：统计合并落点与 best-effort 兜底 ----------
async def test_new_stats_land_at_the_call_site_not_inside_compute_ai_stats(monkeypatch):
    await init_db()
    sm = get_sessionmaker()
    async with sm() as s:
        run = await repo.create_run(s, strategy_id=1, inst_id="BTC-USDT-SWAP", bar="1H",
                                    mode="ai-trade", config={"mode": "ai-trade"})
        run_id = run.id
    session, _ = _make_session(settings=_gate_settings(), run_id=run_id)
    _pin_atr(monkeypatch, 500.0)
    try:
        await session.feed_candle(_candle(0))
        await _settle(session)
        await session.stop()
        ai_stats = (await _run_metrics(sm, run_id))["aiStats"]
        assert {"decisionRounds", "holdRate", "wakeSkips", "profitGuardExits"} <= set(ai_stats)
        assert {"cost", "edgeGate", "decisionConversionRate"} <= set(ai_stats)
        # 可证伪「改了 _compute_ai_stats 内部」：它的返回值本身**不含**任何新键。
        assert not {"cost", "edgeGate", "decisionConversionRate"} & set(
            _compute_ai_stats(session._decisions, session.rounds_completed))
    finally:
        await dispose_engine()


async def test_enrichment_failure_never_blocks_run_finalisation(monkeypatch):
    await init_db()
    sm = get_sessionmaker()
    async with sm() as s:
        run = await repo.create_run(s, strategy_id=1, inst_id="BTC-USDT-SWAP", bar="1H",
                                    mode="ai-trade", config={"mode": "ai-trade"})
        run_id = run.id
    session, _ = _make_session(run_id=run_id)

    def boom(_ai_stats):
        raise ValueError("cost stats key clash with existing aiStats: holdRate")

    session._cost.merge_into = boom
    try:
        await session.stop()
        async with sm() as s:
            run = await repo.get_run(s, run_id)
        assert run.status == "stopped"                 # 真实资金平仓后的落库收尾绝不被阻断
        metrics = await _run_metrics(sm, run_id)
        assert "aiStats" not in metrics                # 退回不含富化字段的基础形态
    finally:
        await dispose_engine()


# ---------- 用例 10：会话内可观测通道（L0/L1 每 10 轮一次）----------
async def test_cost_snapshot_rides_status_event_every_ten_rounds_when_gate_is_off():
    await init_db()
    session, _ = _make_session()                       # k_min=0 ⇒ 门禁不发事件
    try:
        q = session.subscribe()
        for i in range(10):
            session.executor.projected = "flat"
            await session.feed_candle(_candle(i))
            await _settle(session)
        snapshots = [e["data"]["costSnapshot"] for e in _drain(q)
                     if e["type"] == "status" and "costSnapshot" in e["data"]]
        assert len(snapshots) == 1                     # 第 10 轮恰一条
        assert snapshots[0]["cost"] == session._cost.snapshot()["cost"]   # 同一 build 产物
    finally:
        await dispose_engine()


# ---------- 用例 5：A0-2 连带影响（会话止盈止损判据变严）----------
def test_net_pnl_is_strictly_below_gross_for_the_same_close():
    """同一笔平仓的净额恒低于毛额一个往返成本。

    这是 A0-2 全部连带影响的**单一来源**：`session_pnl_usd` 由 `_close_pnl` 累加，而会话级金额
    止盈止损（`session.py` 的 `_maybe_session_exit`）只是拿它与常数阈值比大小，故净额恒低 ⇒
    同样行情下更早触发止损、更晚触发止盈。断言只钉这条源头不等式，不复述下游的单调性。
    """
    gross = _executor_pnl(net=False)
    net = _executor_pnl(net=True)
    assert net < gross                                  # 净额恒 ≤ 毛额（差额 = 双边费用）


def _executor_pnl(*, net: bool) -> float:
    settings = Settings(ai_trade_net_pnl_enabled=net)
    executor = LiveExecutor(
        creds=AccountCredentials("ak", "sk-000000", "pp-000000"), account_id=1,
        inst_id="BTC-USDT-SWAP", td_mode="cross", pos_mode="long_short_mode", leverage=3.0,
        budget_usd=1000.0, risk=RiskParams(), ct_val=0.01, lot_sz=0.01, min_sz=0.01,
        settings=settings, fee_ledger=build_fee_ledger(settings, ct_val=0.01))
    executor.projected, executor.entry_price, executor.entry_qty_sz = "long", _PX, 10.0
    executor.entry_fee_usd = executor.fee_ledger.leg_fee(sz=10.0, px=_PX, ord_type="market")
    return executor._close_pnl(_PX + 100.0)


class _CloseSig:
    side = "close"
    strength = 1.0
    reason = "test_close"
