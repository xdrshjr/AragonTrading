"""开仓漏斗的**会话级**贯通（轴 C · G-10b / G-12 / R-12）。

纯函数单测看不见的那件事：**漏斗在真实调用序下是否闭合**。合入前 v1 的归类只看 `degrade`，而
持仓周期铁律层的作用域是**平仓与反手**（`hold_horizon.gated_kind` 第一条就是 `action == "close"`），
一轮 `close` 被 `horizon_min_hold` 拦下时 `degrade` 非空、而 `intents` 没有 +1 ⇒ `sum(blocked)`
会超过 `intents` ⇒ 这张图的**全部**价值（它闭合）当场失效。
"""
from __future__ import annotations

from dataclasses import replace

from app.ai_auto.decider import AiTradeDecider, TradeDecision
from app.ai_auto.discipline import preset_table
from app.ai_auto.session import AiTradeSession
from app.ai_auto.signals import StrategySignalView, compute_consensus
from app.ai_auto.sizing_runtime import build_sizing_runtime
from app.config import Settings
from app.db.session import dispose_engine, init_db
from app.engine.simulator import RiskParams
from app.live_auto.executor import LiveExecutor
from app.okx.accounts import AccountCredentials
from app.okx.types import BAR_SECONDS
from app.paper.strategy_factory import PaperStrategySpec
from app.strategies.base import BaseStrategy, StrategyMeta


class _Stub(BaseStrategy):
    meta = StrategyMeta(key="_stub_funnel", name="Stub", description="t",
                        default_params={}, params_schema={})

    def warmup(self) -> int:
        return 1

    def on_candle(self, df, i, ctx):
        return []


def _session() -> AiTradeSession:
    settings = Settings(_env_file=None, **preset_table("balanced"))
    executor = LiveExecutor(
        creds=AccountCredentials("ak", "sk-000000", "pp-000000"), account_id=1,
        inst_id="BTC-USDT-SWAP", td_mode="cross", pos_mode="long_short_mode",
        leverage=3.0, budget_usd=1000.0, risk=RiskParams(), ct_val=0.01, lot_sz=0.01,
        min_sz=0.01, settings=settings, bar_seconds=BAR_SECONDS["1H"])
    decider = AiTradeDecider(object(), "sys", context_refresh_rounds=50, settings=settings)
    return AiTradeSession(
        "aitrade_funnel", 1,
        [PaperStrategySpec(1, "S", "atomic", _Stub({}), {})],
        "BTC-USDT-SWAP", "1H", executor, decider, priming=False, priming_bars=10,
        context_refresh_rounds=50,
        sizing=build_sizing_runtime(settings, "BTC-USDT-SWAP", BAR_SECONDS["1H"],
                                    budget_usd=1000.0),
        discipline_preset="balanced")


def _round(session: AiTradeSession, stage1: str, *, stage1_degrade: str | None = None,
           gate: str | None = None, size_fraction: float | None = 0.5,
           skipped: bool = False, filled: bool = False) -> None:
    """按 `_run_decision_round` 的**真实调用序**喂一轮。

    序：`note_intent(第一阶段动作, 第一阶段 degrade)` → … 各层门禁 … → `note_sizing`（第 11 步）→
    `note_executed`（执行回执）→ `note_round(degrade or gate_reason)`（轮末归类）→
    `end_round`（`finally` 兜底）。

    **两个 degrade 必须分开**：`note_intent` 看到的只是 LLM 阶段的降级（`llm_error` /
    `low_confidence` / …），各层门禁的 `gate_reason` 要到第 788 行才被归并进 `degrade`。
    把门禁码提前喂给 `note_intent` 会让分母凭空消失——这正是真实调用序里不会发生的事。
    """
    funnel = session._discipline.funnel
    funnel.note_intent(stage1, stage1_degrade)
    degrade = stage1_degrade or gate
    action = "hold" if degrade else stage1
    funnel.note_sizing(action, size_fraction)
    funnel.note_executed(action, skipped)
    if filled:
        # directional-symmetry 轴 D：成交腿自带方向（本记点排在 `note_executed` 之后，轮级
        # `_intent_side` 已被清），故这里传的必须是**成交腿自己的 side**。
        funnel.note_filled(stage1[len("open_"):] if stage1.startswith("open_") else "")
    session._discipline.note_round(degrade)
    funnel.end_round()


def _wire(session: AiTradeSession) -> dict:
    return session._status_payload()["openFunnel"]


async def test_sizing_hold_preserves_original_open_intent(monkeypatch):
    session = _session()
    decision = TradeDecision(action="open_long", size_fraction=0.5,
                             reason="测试", confidence=0.9)
    session._discipline.funnel.note_intent(decision.action, None)
    monkeypatch.setattr(session._sizing, "resolve_live", lambda *a, **kw: (
        replace(decision, action="hold", size_fraction=None), None,
        {"floorVerdict": "zero"}))
    result = await session._apply_sizing(1, 100, 100, decision)
    assert result.action == "hold"
    assert session._discipline.funnel.sizing_zeroed == 1


def test_integrity_is_identical_across_status_and_rediscovery(monkeypatch):
    session = _session()
    monkeypatch.setattr("app.ai_auto.session.time.time", lambda: 1700000000)
    status = session._status_payload()
    assert status["integrity"] == session.info()["integrity"]
    assert status["integrity"] == session.snapshot()["status"]["integrity"]
    assert status["integrity"]["schemaVersion"] == 2
    assert status["integrity"]["economics"]["winRate"] is None


async def test_optional_enrichment_failure_still_persists_trade(monkeypatch):
    session = _session()
    persisted = []

    def unavailable(*args, **kwargs):
        raise RuntimeError("富化不可用")

    async def persist(trade):
        persisted.append(trade.copy())

    monkeypatch.setattr(session, "_inject_explain_meta", unavailable)
    monkeypatch.setattr(session, "_persist_trade", persist)
    trade = {"side": "long", "price": 100, "qty": 1, "ts": 1,
             "fee": 0, "pnl": 0, "positionAfter": 1}
    await session._dispatch([{"type": "trade", "data": trade}])
    assert len(persisted) == 1
    assert persisted[0]["price"] == 100
    assert persisted[0]["explain"]["meta"]["accountingEventId"]


async def test_stop_reports_unresolved_accepted_order(monkeypatch):
    from dataclasses import asdict
    from app.ai_auto.open_attempts import OpenAttemptManager
    from app.live_auto.resting import RestingOrder
    from app.db.ai_trade_state_repositories import _has_pending

    session = _session()
    resting = RestingOrder("fixed", "accepted", "open", "long",
                           100, 1, 0.5, "测试", 1, 100)
    session.executor.resting = resting

    async def saved(snapshot):
        return True

    async def not_verified(*args, **kwargs):
        return {"ok": True, "closedSz": 0}

    async def no_io(*args, **kwargs):
        return None

    attempts = OpenAttemptManager(
        account_id=1, run_id=1, inst_id=session.inst_id,
        quota=session._entry_quota, persist_callback=saved)
    attempts._extras["adoptedOrder"] = {
        "clOrdId": "fixed", "resting": asdict(resting)}
    session.executor.open_attempts = attempts
    monkeypatch.setattr(session.executor, "flatten", not_verified)
    monkeypatch.setattr(session._cost, "reconcile_tick", no_io)
    monkeypatch.setattr(session, "_finalize_run", no_io)
    result = await session.stop()
    assert not result["ok"]
    assert session.flatten_failed
    assert _has_pending(session.state_member_row())


# ============================ G-10b：闭合不变式 ============================
async def test_the_funnel_closes_on_a_mixed_event_stream():
    """含「持仓中模型要 close、被 `horizon_min_hold` 拦下」的混合流后仍恒闭合。"""
    await init_db()
    try:
        session = _session()
        _round(session, "open_long", gate="edge_below_cost(edge=8.1bps,cost=13.0bps,k=1.5)")
        _round(session, "open_short", gate="low_consensus(1/3)")
        _round(session, "hold")
        _round(session, "close", gate="horizon_min_hold")     # ← 平仓侧，**不进开仓分母**
        _round(session, "close", gate="horizon_backstop_uncovered(40%)")
        _round(session, "open_long", filled=True)
        wire = _wire(session)
        assert wire["intents"] == 3
        assert sum(wire["blocked"].values()) + wire["submitted"] + wire["sizingZeroed"] \
            <= wire["intents"]
        assert "horizon" not in wire["blocked"]          # 那两轮压根没记 intent
        assert "exchangeBackstop" not in wire["blocked"]
        assert wire["filled"] == 1 and wire["submitted"] == 1
    finally:
        await dispose_engine()


async def test_a_reversal_blocked_by_horizon_does_count_as_an_open_intent():
    """对照组：**反手**（`open_long` 遇空头仓）被 horizon 拦下时确实是一次开仓意图，必须计入。

    这条防止实现节点一刀切「按层名把 horizon / backstop 排除」——判据只能来自
    「本轮记没记过 intent」这个事实。
    """
    await init_db()
    try:
        session = _session()
        _round(session, "open_long", gate="horizon_turnover_hysteresis(2.0)")
        wire = _wire(session)
        assert wire["intents"] == 1
        assert wire["blocked"] == {"horizon": 1}
    finally:
        await dispose_engine()


async def test_low_confidence_is_counted_once_and_is_not_an_llm_failure():
    """G-10c：`blocked["confidence"] == 1` 且 `llmDegraded == 0`（**不得两处都计**）。"""
    await init_db()
    try:
        session = _session()
        _round(session, "hold", stage1_degrade="low_confidence")
        wire = _wire(session)
        assert wire["blocked"] == {"confidence": 1}
        assert wire["llmDegraded"] == 0
    finally:
        await dispose_engine()


async def test_llm_failures_land_in_their_own_bucket_and_never_in_intents():
    await init_db()
    try:
        session = _session()
        _round(session, "hold", stage1_degrade="llm_error")
        _round(session, "hold", stage1_degrade="decision_timeout")
        wire = _wire(session)
        assert wire["intents"] == 0 and wire["llmDegraded"] == 2
        assert wire["blocked"] == {}
    finally:
        await dispose_engine()


async def test_sizing_zeroed_sits_before_submitted_and_never_in_blocked():
    """G-11 补充：过了全部门禁但仓位解到 0 ⇒ `sizingZeroed == 1` 且 `blocked` 不变。"""
    await init_db()
    try:
        session = _session()
        _round(session, "open_long", size_fraction=0.0)
        wire = _wire(session)
        assert wire["sizingZeroed"] == 1
        assert wire["blocked"] == {} and wire["submitted"] == 0
    finally:
        await dispose_engine()


# ============================ R-12：轮级标志绝不跨轮泄漏 ============================
async def test_the_round_flag_is_cleared_even_when_a_round_blows_up():
    """决策轮在 `note_intent` 之后抛异常时，`finally` 的 `end_round` 必须已经清零标志。

    否则下一轮的**平仓侧**降级会被错记成开仓拦截——那正是 P0-3 的失败形状换了个入口复发。
    """
    await init_db()
    try:
        session = _session()
        funnel = session._discipline.funnel
        funnel.note_intent("open_long", None)      # 本轮记过 intent…
        funnel.end_round()                          # …然后异常路径直接走到 finally
        funnel.note_intent("close", None)           # 下一轮是平仓意图
        session._discipline.note_round("horizon_min_hold")
        funnel.end_round()
        wire = _wire(session)
        assert wire["intents"] == 1
        assert wire["blocked"] == {}                # 泄漏的话这里会是 {"horizon": 1}
    finally:
        await dispose_engine()


# ============================ G-12 / 呈现落点 ============================
async def test_status_payload_carries_the_three_new_keys():
    """「后端算了但用户看不见 = 死数据」：三个新键必须挂在整体 spread 的那条 status 上。"""
    await init_db()
    try:
        session = _session()
        payload = session._status_payload()
        assert "openFunnel" in payload and "tradability" in payload
        assert payload["summaryHealth"] is not None
        assert payload["openFunnel"]["conversion"] is None     # 零意愿 ⇒ null，绝不画成 0%
        assert payload["tradability"] is None                  # 未探测 ⇒ None（不造数）
        assert session.info()["summaryHealth"] is not None     # [P1-9] 两处装配
    finally:
        await dispose_engine()


async def test_tradability_snapshot_flows_from_start_receipt_into_status():
    """启动回执与会话 status 用的是**同一份对象**（同一 `asOfTs`，[P1-10]）。"""
    await init_db()
    try:
        session = _session()
        session.note_tradability({"level": "infeasible", "bar": "1m", "asOfTs": 1786600000,
                                  "vetoRate": {"market": 0.986}, "headline": "h",
                                  "advice": "a", "samples": 200, "source": "cache"})
        status = session._status_payload()["tradability"]
        assert status["level"] == "infeasible" and status["asOfTs"] == 1786600000
        assert status["vetoRateMarket"] == 0.986
    finally:
        await dispose_engine()


async def test_hint_switches_to_the_bar_advice_when_the_probe_says_infeasible():
    """G-13：`tradability_level == "infeasible"` ⇒ 文案**必须含「时间级别」且不只建议改预设**。"""
    await init_db()
    try:
        session = _session()
        session.rounds_completed = 20
        session.note_tradability({"level": "infeasible", "bar": "1m"})
        hint = session.discipline_wire()["hint"]
        assert hint is not None
        assert "时间级别" in hint and "换预设解决不了" in hint
    finally:
        await dispose_engine()


async def test_hint_names_the_top_blocker_once_there_are_real_intents():
    await init_db()
    try:
        session = _session()
        session.rounds_completed = 20
        for _ in range(9):
            _round(session, "open_long",
                   gate="edge_below_cost(edge=8.1bps,cost=13.0bps,k=1.5)")
        hint = session.discipline_wire()["hint"]
        assert hint is not None and "最小 edge 门禁" in hint and "9 次" in hint
    finally:
        await dispose_engine()


# ============ 方向轴的会话级贯通（ai-trade-directional-symmetry 轴 A / D） ============
#
# 纯函数看不见的那件事：**在真实调用序下，一个「信号板全多头 + 模型要做空」的轮次到底走到哪
# 一步**。合入前它恒停在共识门禁；本节断言它能一路走到「委托真的发出」，且方向、仓位、计数、
# 注记四件事全部对得上。

_SHORT_EVIDENCE = ("净立场偏多但 4H 结构已在关键位破位，成交量在反弹段持续萎缩；我推翻策略 15 "
                   "的因子得分与策略 28 的趋势带立场——二者的前提在当前体制下都不成立。若价格"
                   "重新站回中轨并伴随放量，我立刻认错离场。")


def _consensus_session(*, contrarian: bool, symmetry: bool = True) -> AiTradeSession:
    """信号板全多头（1 票多 / 0 票空）+ balanced 预设（`min_consensus=1`）的会话。

    ⚠️ 两个总闸必须在**构造之前**定好：`ConsensusRule` 是会话级不可变快照（`__init__` 里从
    `executor.settings` 派生一次），事后改 settings 不会改它——生产路径也正是这个语义。
    """
    settings = Settings(_env_file=None, **preset_table("balanced"))
    settings = settings.model_copy(update={
        "ai_trade_consensus_contrarian_enabled": contrarian,
        "ai_trade_directional_symmetry_enabled": symmetry})
    executor = LiveExecutor(
        creds=AccountCredentials("ak", "sk-000000", "pp-000000"), account_id=1,
        inst_id="BTC-USDT-SWAP", td_mode="cross", pos_mode="long_short_mode",
        leverage=3.0, budget_usd=1000.0, risk=RiskParams(), ct_val=0.01, lot_sz=0.01,
        min_sz=0.01, settings=settings, bar_seconds=BAR_SECONDS["1H"])
    decider = AiTradeDecider(object(), "sys", context_refresh_rounds=50, settings=settings)
    return AiTradeSession(
        "aitrade_dir", 1,
        [PaperStrategySpec(1, "S", "atomic", _Stub({}), {})],
        "BTC-USDT-SWAP", "1H", executor, decider, priming=False, priming_bars=10,
        context_refresh_rounds=50,
        sizing=build_sizing_runtime(settings, "BTC-USDT-SWAP", BAR_SECONDS["1H"],
                                    budget_usd=1000.0),
        discipline_preset="balanced")


def _long_board():
    return compute_consensus([StrategySignalView(
        strategy_id=1, name="S", stance="long", latest_signal="none",
        strength=0.0, reason="", risk_params=RiskParams())])


async def test_short_reaches_the_order_when_the_evidence_is_there():
    """信号板全多、模型要做空 ⇒ 逆势通路放行：方向为 short、仓位减半、漏斗记 contrarianPassed。"""
    await init_db()
    try:
        session = _consensus_session(contrarian=True)
        decision = TradeDecision("open_short", 0.5, 0.72, "r",
                                 contrarian_evidence=_SHORT_EVIDENCE)
        session._discipline.note_intent("open_short", None)
        out, degrade = session._apply_consensus_gate(decision, _long_board(), None)
        assert out.action == "open_short" and degrade is None
        assert out.size_fraction == 0.25                      # 强制降档 ×0.5
        session._discipline.funnel.note_executed(out.action, False)
        session._discipline.note_round(degrade)
        session._discipline.funnel.end_round()
        wire = session._status_payload()["openFunnel"]
        assert wire["bySide"]["short"]["submitted"] == 1
        assert wire["directionBalance"]["contrarianPassed"] == 1
        assert wire["directionBalance"]["shortIntents"] == 1
        assert wire["blocked"] == {}                          # 逆势放行不是降级
        assert session.discipline_wire()["contrarian"]["remaining"] == 3
        assert session._entry_quota.used == 0  # 门禁放行不代表交易所已接收
    finally:
        await dispose_engine()


async def test_short_is_blocked_and_the_note_keeps_the_direction():
    """同一场景关掉逆势通路 ⇒ 降级 hold、方向轴记在 short 上、注记里的提议仍是 `open_short`。"""
    await init_db()
    try:
        session = _consensus_session(contrarian=False)
        stage1 = TradeDecision("open_short", 0.5, 0.72, "r",
                               contrarian_evidence=_SHORT_EVIDENCE)
        session._discipline.note_intent("open_short", None)
        out, degrade = session._apply_consensus_gate(stage1, _long_board(), None)
        assert out.action == "hold" and degrade.startswith("low_consensus")
        session._discipline.funnel.note_executed(out.action, False)
        session._discipline.note_round(degrade)
        session._discipline.funnel.end_round()
        wire = session._status_payload()["openFunnel"]
        assert wire["bySide"]["short"]["blocked"] == {"consensus": 1}
        assert wire["directionBalance"]["verdict"] == "insufficient"   # 样本不足，不下结论
        # ★ 轴 B：`commit_round` 收的注记必须带方向——合入前这条路径完全静默。
        note = session._gate_veto_note(stage1, degrade, None)
        assert note["vetoedBy"] == "consensus"
        assert note["proposedAction"] == "open_short"
        assert note["layerLabel"] == "同向共识门槛"
    finally:
        await dispose_engine()


async def test_llm_stage_round_never_shows_a_fabricated_gate_veto():
    """★ 第一阶段模型侧的码：注记与 `tool_call` 相位**同时闭嘴**（两处共用同一判定）。

    `low_confidence` 轮此刻 `stage1_decision.action` 已被 `_map_decision` 洗成 hold。若相位侧
    漏传 `llm_degrade`，`classify_block` 会把它归到 `confidence` 那一层，卡片上就会出现
    「模型提议的是『观望』，被『置信度门槛』降级」——一条凭空捏造的门禁事实。
    """
    await init_db()
    try:
        session = _consensus_session(contrarian=True)
        hold = TradeDecision("hold", None, None, "low_confidence")
        for code in ("low_confidence", "no_tool_call", "llm_error"):
            assert session._gate_veto_note(hold, code, code) is None, code
            assert session._trace_veto_layer(hold, hold, code, code) is None, code
        # 对照组：确定性门禁的码两处都必须认得出，且认的是同一层。
        stage1 = TradeDecision("open_short", 0.5, 0.72, "r")
        note = session._gate_veto_note(stage1, "low_consensus(0/1)", "low_confidence")
        layer = session._trace_veto_layer(hold, stage1, "low_consensus(0/1)", "low_confidence")
        assert note["vetoedBy"] == layer == "consensus"
    finally:
        await dispose_engine()


async def test_direction_axis_absent_when_master_switch_off():
    """总闸关 ⇒ `openFunnel` 两键整键缺席、注记回落四键 ⇒ wire 逐字节回退。"""
    await init_db()
    try:
        session = _consensus_session(contrarian=True, symmetry=False)
        session._discipline.note_intent("open_short", None)
        session._discipline.note_round("low_consensus(0/1)")
        session._discipline.funnel.end_round()
        wire = session._status_payload()["openFunnel"]
        assert "bySide" not in wire and "directionBalance" not in wire
        assert "contrarian" not in session.discipline_wire()
        # 总闸关时的注记只认持仓周期层，且恒为四键（`layerLabel` 不出现）。
        stage1 = TradeDecision("open_short", 0.5, 0.72, "r")
        assert session._gate_veto_note(stage1, "low_consensus(0/1)", None) is None
    finally:
        await dispose_engine()
