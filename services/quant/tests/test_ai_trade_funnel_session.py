"""开仓漏斗的**会话级**贯通（轴 C · G-10b / G-12 / R-12）。

纯函数单测看不见的那件事：**漏斗在真实调用序下是否闭合**。合入前 v1 的归类只看 `degrade`，而
持仓周期铁律层的作用域是**平仓与反手**（`hold_horizon.gated_kind` 第一条就是 `action == "close"`），
一轮 `close` 被 `horizon_min_hold` 拦下时 `degrade` 非空、而 `intents` 没有 +1 ⇒ `sum(blocked)`
会超过 `intents` ⇒ 这张图的**全部**价值（它闭合）当场失效。
"""
from __future__ import annotations

from app.ai_auto.decider import AiTradeDecider
from app.ai_auto.discipline import preset_table
from app.ai_auto.session import AiTradeSession
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
        funnel.note_filled()
    session._discipline.note_round(degrade)
    funnel.end_round()


def _wire(session: AiTradeSession) -> dict:
    return session._status_payload()["openFunnel"]


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
