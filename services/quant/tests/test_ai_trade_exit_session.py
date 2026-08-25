"""离场优先级的会话端到端用例（ai-trade-exit-priority-and-commitment · #31）。

七条：
  ① 被 G2 拦下 → 下一轮 `final` + 证据 + 账本达标 ⇒ **当轮放行成交**（A-1）
  ② `pending`（只可能是 `ledger_short`）→ 复核官 `confirm` ⇒ 放行且 `level == "adjudicated"`
  ③ 复核官真实 `abort` ⇒ 降级 hold，且 `degrade` 取**原门禁码**而非 `reflection_abort`
  ④ `reflection_timeout` ⇒ **fail-open 放行且 `degrade is None`**（F-3 的回归用例 + 【R:P1-3】）
  ⑤ `quota_exhausted` / `evidence_missing` / `min_hold_floor` 三种降档**一律不挂起**（【R:P0-1】）
  ⑥ 限价 close **不武装**轴 C（【R:P0-3】）
  ⑦ override 之后紧接一次利润保护平仓 ⇒ 后者归 `profitGuard`（【R:P0-6】打标槽生命周期）

会话侧只做编排，判定全在纯核心；本文件因此**直接驱动编排点**，不去重测纯核心已冻结的判定表。
"""
from __future__ import annotations

import asyncio
import json

import pytest

from app.agent.llm.base import StreamEvent
from app.ai_auto.decider import AiTradeDecider, ReflectionVerdict, TradeDecision
from app.ai_auto.exit_commitment import EXIT_STUCK_CODE
from app.ai_auto.exit_priority import LEVEL_ADJUDICATED, LEVEL_EXPEDITE, LEVEL_OVERRIDE
from app.ai_auto.hold_horizon import HorizonRule, resolve_horizon_rule
from app.ai_auto.session import AiTradeSession
from app.config import Settings
from app.db.session import dispose_engine, init_db
from app.engine.simulator import RiskParams
from app.live_auto.executor import LiveExecutor
from app.okx.accounts import AccountCredentials
from app.okx.types import Candle
from app.paper.strategy_factory import PaperStrategySpec
from app.strategies.base import BaseStrategy, StrategyMeta

_START_TS = 1_700_000_000
_STEP = 3600
_PX = 30_000.0
_EVIDENCE = "1H 中轨失守且量能连续两根放大，持仓理由已失效"


class _StubStrategy(BaseStrategy):
    meta = StrategyMeta(key="_stub_exit_session", name="Stub", description="t",
                        default_params={}, params_schema={})

    def warmup(self) -> int:
        return 1

    def on_candle(self, df, i, ctx):
        return []


class _StubLLM:
    """恒开多 + 恒确认的桩（本文件只需要把仓位开起来；离场路径全部直接驱动编排点）。

    **反思那一支必须一起桩掉**：本会话开着 `reflection_enabled`（离场路径要用它），若桩只认决策
    工具，开仓轮会被 `reflection_no_tool` fail-closed 降级成 hold，`_open` 当场失败。
    """

    async def stream(self, *, system, messages, tools):
        if not tools:
            yield StreamEvent("text_delta", text="摘要")
            yield StreamEvent("message_done", stop_reason="end")
            return
        name = getattr(tools[0], "name", "submit_trade_decision")
        args = ({"verdict": "confirm", "reason": "ok"} if name == "submit_trade_reflection"
                else {"action": "open_long", "reason": "r", "confidence": 0.7,
                      "sizeFraction": 0.5})
        yield StreamEvent("tool_use_start", id="t", name=name)
        yield StreamEvent("tool_use_args_delta", id="t",
                          partial_json=json.dumps(args, ensure_ascii=False))
        yield StreamEvent("message_done", stop_reason="tool_use")


def _candle(idx: int, price: float = _PX) -> Candle:
    return Candle(ts=_START_TS + idx * _STEP, o=price, h=price + 5, l=price - 5,
                  c=price, vol=100.0, confirm=True)


def _rule(settings: Settings, **over) -> HorizonRule:
    """由**真实解析路径**装配契约——绝不手搓一个 `HorizonRule`，否则测的不是生产那一份。"""
    base = resolve_horizon_rule({"profile": "intraday_swing"}, settings, bar="1H")
    from dataclasses import replace
    return replace(base, **over) if over else base


def _make_session(settings: Settings, *, horizon_rule=None, profit_rule=None,
                  reflection: bool = True):
    async def place_order_fn(order):
        return {"ok": True, "ordId": "x", "clOrdId": "aragonx"}

    async def close_position_fn(close):
        return {"ok": True, "clOrdId": "aragoncx", "closedSz": 0.1}

    async def positions_fn():
        ex = session.executor
        if ex.projected == "flat":
            return []
        return [{"instId": "BTC-USDT-SWAP", "pos": ex.entry_qty_sz, "upl": 0.0}]

    async def fills_fn():
        return []

    executor = LiveExecutor(
        creds=AccountCredentials("ak", "sk-000000", "pp-000000"), account_id=1,
        inst_id="BTC-USDT-SWAP", td_mode="cross", pos_mode="long_short_mode",
        leverage=3.0, budget_usd=1000.0, risk=RiskParams(), ct_val=0.01,
        lot_sz=0.01, min_sz=0.01, settings=settings, place_order_fn=place_order_fn,
        close_position_fn=close_position_fn, positions_fn=positions_fn, fills_fn=fills_fn)
    decider = AiTradeDecider(_StubLLM(), "sys", context_refresh_rounds=50,
                             settings=settings, reflection_enabled=reflection,
                             allow_exit_priority=True)
    spec = PaperStrategySpec(1, "S", "atomic", _StubStrategy({}), {})
    session = AiTradeSession("aitrade_exit", 1, [spec], "BTC-USDT-SWAP", "1H", executor,
                             decider, priming=False, priming_bars=10,
                             context_refresh_rounds=50,
                             horizon_rule=horizon_rule or _rule(settings),
                             profit_rule=profit_rule)

    async def no_htf(*_a, **_k):
        return []

    session._window._safe_get_candles = no_htf
    return session


async def _settle(session) -> None:
    task = session._decision_task
    if task is not None:
        try:
            await task
        except asyncio.CancelledError:
            pass


async def _open(session, idx: int = 0) -> None:
    await session.feed_candle(_candle(idx))
    await _settle(session)
    assert session.executor.projected == "long"


def _close(**over) -> TradeDecision:
    base = {"action": "close", "size_fraction": None, "confidence": 0.9, "reason": "exit"}
    return TradeDecision(**{**base, **over})


async def _run_exit_round(session, decision: TradeDecision, *, streak: int = 1,
                          hold_s: float = 3600.0):
    """驱动一轮离场：账本推到 `streak`、把开仓墙钟往回拨到 `hold_s` 之前，再走门禁 + 反思。"""
    from dataclasses import replace as dc_replace
    import time as _time

    now = _time.time()
    session._horizon.state = dc_replace(
        session._horizon.state, opened_wall=now - hold_s,
        opened_wall_estimated=False, exit_confirm_streak=streak,
        first_exit_intent_wall=now - hold_s)
    session._resolve_exit_priority(decision)
    gated, degrade, pending = await session._apply_horizon_gate(1, _START_TS, decision, None)
    gated, degrade, _ = await session._reflect_before_execute(
        1, _START_TS, {}, gated, degrade, pending=pending)
    session._note_exit_priority_round()
    return gated, degrade, pending


# ---------------- ① A-1：final + 证据 + 账本 ⇒ 当轮放行 ----------------
async def test_final_with_evidence_clears_all_three_gates_in_the_same_round():
    """复现用户场景：G2 未满足、模型第 2 轮给出 `final` + 证据 ⇒ **当轮成交**。"""
    await init_db()
    settings = Settings(ai_trade_funding_enabled=False)
    session = _make_session(settings)
    try:
        await _open(session)
        # 先证伪：不声明确信度时确实被 G1 拦住（`intraday_swing` 的 min_hold 是 2 小时）
        gated, degrade, _ = await _run_exit_round(session, _close(), streak=2, hold_s=600.0)
        assert gated.action == "hold" and degrade.startswith("horizon_min_hold(")
        # 再放行：final + 证据 + 账本 2/2 + 配额充足 + 已过 60s 地板
        gated, degrade, pending = await _run_exit_round(
            session, _close(exit_conviction="final", exit_evidence=_EVIDENCE),
            streak=2, hold_s=600.0)
        assert gated.action == "close" and degrade is None and pending is False
        assert session._exit_priority.level == LEVEL_OVERRIDE
        assert session._horizon.exit_overrides_used == 1
        assert session._pending_exit_priority == LEVEL_OVERRIDE   # 打标槽已置位
    finally:
        await dispose_engine()


async def test_override_skips_the_reviewer_entirely():
    """X-5：`override` 跳过反思——复核官是无状态单轮调用，推翻不了它看不见的跨轮账本。"""
    await init_db()
    settings = Settings(ai_trade_funding_enabled=False)
    session = _make_session(settings)
    calls = {"n": 0}

    async def _reflect(*_a, **_k):
        calls["n"] += 1
        return ReflectionVerdict("abort", None, None, "别平", None, ran=True)

    try:
        # 桩必须在开仓**之后**装：开仓轮同样过反思，提前装一个 abort/timeout 的桩会把仓开不出来。
        await _open(session)
        session.decider.reflect = _reflect
        gated, _, _ = await _run_exit_round(
            session, _close(exit_conviction="final", exit_evidence=_EVIDENCE),
            streak=2, hold_s=600.0)
        assert gated.action == "close"
        assert calls["n"] == 0
    finally:
        await dispose_engine()


# ---------------- ② / ③ pending 的两条裁决 ----------------
async def test_pending_confirm_becomes_adjudicated():
    """账本不足 ⇒ 挂起 ⇒ 复核官 `confirm` ⇒ 放行且档位为 **`adjudicated`**（【R:P1-2】）。"""
    await init_db()
    settings = Settings(ai_trade_funding_enabled=False)
    session = _make_session(settings)

    async def _reflect(*_a, **_k):
        return ReflectionVerdict("confirm", None, 0.8, "同意离场", None, ran=True)

    try:
        # 桩必须在开仓**之后**装：开仓轮同样过反思，提前装一个 abort/timeout 的桩会把仓开不出来。
        await _open(session)
        session.decider.reflect = _reflect
        gated, degrade, pending = await _run_exit_round(
            session, _close(exit_conviction="final", exit_evidence=_EVIDENCE),
            streak=1, hold_s=600.0)
        assert pending is True
        assert gated.action == "close" and degrade is None
        assert session._exit_priority.level == LEVEL_ADJUDICATED
        assert session._horizon.exit_overrides_used == 1     # X-4b：与 override 共用配额
    finally:
        await dispose_engine()


async def test_pending_abort_degrades_with_the_original_gate_code():
    """真实 `abort` ⇒ 降级 hold，`degrade` 取**原门禁码**（用户要知道的是「差几轮确认」）。"""
    await init_db()
    settings = Settings(ai_trade_funding_enabled=False)
    session = _make_session(settings)

    async def _reflect(*_a, **_k):
        return ReflectionVerdict("abort", None, None, "还不该走", None, ran=True)

    try:
        # 桩必须在开仓**之后**装：开仓轮同样过反思，提前装一个 abort/timeout 的桩会把仓开不出来。
        await _open(session)
        session.decider.reflect = _reflect
        gated, degrade, pending = await _run_exit_round(
            session, _close(exit_conviction="final", exit_evidence=_EVIDENCE),
            streak=1, hold_s=600.0)
        assert pending is True and gated.action == "hold"
        assert degrade.startswith("horizon_min_hold(")
        assert not degrade.startswith("reflection_")
        assert session._horizon.exit_overrides_used == 0
    finally:
        await dispose_engine()


# ---------------- ④ F-3：close 的反思 fail-closed 反转为 fail-open ----------------
@pytest.mark.parametrize("code", ["reflection_timeout", "reflection_llm_error",
                                  "reflection_no_tool", "reflection_invalid"])
async def test_infrastructure_failures_no_longer_lock_a_real_position(code):
    """**F-3 的回归用例**：一次 LLM 超时（最坏 3×45s）不该锁死一笔真实持仓。

    【R:P1-3】放行**不写 `degrade`**：`degrade` 的语义是「本轮被降级」，把放行塞进去会让闸计数
    把一次成功离场记成一次被拦。事实挂在 `exitPriority.failOpenReason` 与专属计数上。
    """
    await init_db()
    settings = Settings(ai_trade_funding_enabled=False)
    session = _make_session(settings)

    async def _reflect(*_a, **_k):
        return ReflectionVerdict("abort", None, None, code, code, ran=True)

    try:
        # 桩必须在开仓**之后**装：开仓轮同样过反思，提前装一个 abort/timeout 的桩会把仓开不出来。
        await _open(session)
        session.decider.reflect = _reflect
        gated, degrade, _ = await _run_exit_round(session, _close(), streak=2,
                                                  hold_s=99999.0)
        assert gated.action == "close"                 # ← 修法前这里是 hold
        assert degrade is None                         # A-3：放行绝不进 degrade
        assert session._exit_priority.wire["failOpenReason"] == code
        assert session._horizon.stats()["exitFailOpenRounds"] == 1
    finally:
        await dispose_engine()


async def test_a_real_abort_is_still_honoured():
    """R-3 缓解①：**模型的真实终态裁决**不在 fail-open 范围内，逐字不变。"""
    await init_db()
    settings = Settings(ai_trade_funding_enabled=False)
    session = _make_session(settings)

    async def _reflect(*_a, **_k):
        return ReflectionVerdict("abort", None, None, "现在走是错的", None, ran=True)

    try:
        # 桩必须在开仓**之后**装：开仓轮同样过反思，提前装一个 abort/timeout 的桩会把仓开不出来。
        await _open(session)
        session.decider.reflect = _reflect
        gated, degrade, _ = await _run_exit_round(session, _close(), streak=2,
                                                  hold_s=99999.0)
        assert gated.action == "hold" and degrade == "reflection_abort"
    finally:
        await dispose_engine()


async def test_fail_open_switch_is_a_verbatim_rollback():
    """C-8 一键回退：总闸关 ⇒ 逐字回退今天的 fail-closed。"""
    await init_db()
    settings = Settings(ai_trade_funding_enabled=False,
                        ai_trade_exit_reflection_fail_open=False)
    session = _make_session(settings)

    async def _reflect(*_a, **_k):
        return ReflectionVerdict("abort", None, None, "reflection_timeout",
                                 "reflection_timeout", ran=True)

    try:
        # 桩必须在开仓**之后**装：开仓轮同样过反思，提前装一个 abort/timeout 的桩会把仓开不出来。
        await _open(session)
        session.decider.reflect = _reflect
        gated, degrade, _ = await _run_exit_round(session, _close(), streak=2,
                                                  hold_s=99999.0)
        assert gated.action == "hold" and degrade == "reflection_timeout"
    finally:
        await dispose_engine()


# ---------------- ⑤【R:P0-1】三种降档一律不挂起 ----------------
@pytest.mark.parametrize("over,hold_s,used", [
    ({"exit_conviction": "final", "exit_evidence": None}, 3600.0, 0),      # evidence_missing
    ({"exit_conviction": "final", "exit_evidence": _EVIDENCE}, 3600.0, 6),  # quota_exhausted
    ({"exit_conviction": "final", "exit_evidence": _EVIDENCE}, 10.0, 0),    # min_hold_floor
])
async def test_only_ledger_short_may_pend(over, hold_s, used):
    """配额 / 证据 / 时间地板三种降档**根本不进入裁决通路**——它们不是判断题。

    v1 让**任何**降档的 `final` 都挂起、而复核官一句 `confirm` 就放行，等于一次删掉 X-4 四个
    合取项中的三个（`overrides_used` 还会越过上限继续自增）。
    """
    await init_db()
    settings = Settings(ai_trade_funding_enabled=False)
    session = _make_session(settings)

    async def _reflect(*_a, **_k):
        return ReflectionVerdict("confirm", None, 0.8, "同意", None, ran=True)

    try:
        # 桩必须在开仓**之后**装：开仓轮同样过反思，提前装一个 abort/timeout 的桩会把仓开不出来。
        await _open(session)
        session.decider.reflect = _reflect
        session._horizon.exit_overrides_used = used
        gated, _, pending = await _run_exit_round(session, _close(**over), streak=2,
                                                  hold_s=hold_s)
        assert pending is False
        assert session._exit_priority.level != LEVEL_ADJUDICATED
        assert session._exit_priority.level == LEVEL_EXPEDITE
    finally:
        await dispose_engine()


async def test_a10_quota_exhausted_never_trades():
    """A-10：配额调到 1 的会话里，第 2 次 `final` 不成交、不挂起、`downgradeReason=quota`。"""
    await init_db()
    settings = Settings(ai_trade_funding_enabled=False,
                        ai_trade_exit_override_max_per_session=1)
    session = _make_session(settings)
    try:
        await _open(session)
        first, _, _ = await _run_exit_round(
            session, _close(exit_conviction="final", exit_evidence=_EVIDENCE),
            streak=2, hold_s=600.0)
        assert first.action == "close" and session._horizon.exit_overrides_used == 1
        second, degrade, pending = await _run_exit_round(
            session, _close(exit_conviction="final", exit_evidence=_EVIDENCE),
            streak=2, hold_s=600.0)
        # 第 2 次：配额已尽 ⇒ 降为 expedite（只解 G2）⇒ 仍被 G1 拦下 ⇒ **没有成交**
        assert pending is False and second.action == "hold"
        assert degrade.startswith("horizon_min_hold(")
        assert session._exit_priority.downgrade_reason == "quota_exhausted"
        assert session._horizon.stats()["exitFinalDowngraded"] == 1
    finally:
        await dispose_engine()


# ---------------- ⑥【R:P0-3】限价 close 不武装轴 C ----------------
async def test_a_limit_close_never_arms_the_commitment_layer():
    """限价平仓当轮**必然**没有成交事件——照「未见成交即武装」写会撤掉模型自己挂的限价单。"""
    await init_db()
    settings = Settings(ai_trade_funding_enabled=False)
    session = _make_session(settings)
    try:
        await _open(session)
        limit = _close(ord_type="limit", limit_px=29900.0)
        session._arm_exit_commitment(limit, {"closedFilled": False, "skipped": False})
        assert session._exit_commit.armed is False
        # 市价同款断面 ⇒ 武装（证明上面那条不是因为别的原因空转）
        session._arm_exit_commitment(_close(), {"closedFilled": False, "skipped": False})
        assert session._exit_commit.armed is True
    finally:
        await dispose_engine()


async def test_a_filled_or_skipped_close_never_arms():
    await init_db()
    settings = Settings(ai_trade_funding_enabled=False)
    session = _make_session(settings)
    try:
        await _open(session)
        session._arm_exit_commitment(_close(), {"closedFilled": True, "skipped": False})
        assert session._exit_commit.armed is False
        session._arm_exit_commitment(_close(), {"closedFilled": False, "skipped": True})
        assert session._exit_commit.armed is False
    finally:
        await dispose_engine()


# ---------------- ⑦【R:P0-6】打标槽的生命周期 ----------------
async def test_the_stamp_slot_is_consumed_once_and_never_leaks():
    """override 之后紧接一次利润保护平仓 ⇒ 后者必须归 `profitGuard`。

    `_inject_explain_meta` 跑在 `_dispatch` 的**每一条**成交事件上（含限价平仓在后续盯市 tick 上
    被异步采纳的那一笔）。槽若不随本次 close 清空，一次 override 之后的下一笔**任意**平仓都会被
    记成 `agentOverride`，且错误方向是**系统性高估**（override 之后的仓位往往由保护层平掉）。
    """
    await init_db()
    settings = Settings(ai_trade_funding_enabled=False)
    session = _make_session(settings)
    try:
        session._pending_exit_priority = LEVEL_OVERRIDE
        agent_close = {"kind": "atomic", "side": "close"}
        assert session._consume_exit_source(agent_close) == "agentOverride"
        # 消费即清空 ⇒ 紧接着的保护层平仓归 profitGuard
        assert session._consume_exit_source({"kind": "profit_guard"}) == "profitGuard"
        # 槽在场时也只认 atomic：保护层 / OCO / 会话停止一律不认
        session._pending_exit_priority = LEVEL_OVERRIDE
        assert session._consume_exit_source({"kind": "profit_guard"}) == "profitGuard"
        session._pending_exit_priority = LEVEL_OVERRIDE
        assert session._consume_exit_source(
            {"kind": "external_close", "source": "oco"}) == "exchangeOco"
    finally:
        await dispose_engine()


async def test_the_stamp_slot_is_voided_when_the_position_identity_changes():
    """**已武装的**离场意图因持仓身份变化而作废 ⇒ 槽一并作废（下一笔持仓绝不继承上一笔的档位）。

    判据是「已武装的意图被作废」而不是「本轮未武装」：后者在每一个盯市 tick 上都成立，而
    `_note_exit_priority_round()` 与 `_execute_decision` 之间隔着一次真实下单的 await——tick 落在
    那个窗口里就会把槽抹掉，一次 override 平仓被随机记成普通 `agent`（节点 #3 复核补的判据）。
    """
    await init_db()
    settings = Settings(ai_trade_funding_enabled=False)
    session = _make_session(settings)
    try:
        await _open(session)
        session._arm_exit_commitment(_close(), {"closedFilled": False, "skipped": False})
        assert session._exit_commit.armed is True
        session._pending_exit_priority = LEVEL_OVERRIDE
        session.executor.projected = "flat"         # 持仓身份变化（归 flat）
        await session._settle_exit_commitment()
        assert session._exit_commit.armed is False
        assert session._pending_exit_priority is None
    finally:
        await dispose_engine()


async def test_a_plain_tick_never_wipes_the_stamp_slot_mid_flight():
    """未武装的普通 tick **绝不**动打标槽——它恰好会落在下单 await 的窗口里。"""
    await init_db()
    settings = Settings(ai_trade_funding_enabled=False)
    session = _make_session(settings)
    try:
        await _open(session)
        session._pending_exit_priority = LEVEL_OVERRIDE   # `_execute_decision` 正在下单
        await session._settle_exit_commitment()
        assert session._pending_exit_priority == LEVEL_OVERRIDE
    finally:
        await dispose_engine()


# ---------------- 【R:P1-6】中途离场失败绝不留终态红条（真实会话，非桩）----------------
async def test_a_mid_flight_exit_failure_never_sets_the_terminal_flag():
    """`/exit` 失败 ⇒ 推 `AI_TRADE_MANUAL_EXIT_FAILED`，`flatten_failed` **保持 False**。

    `flatten_failed` / `residual` 进 `_status_payload` 是面板上的**终态**红条，且全仓没有任何复位点
    （只在 `__init__` 置 False）——一次中途失败会让此后每一次 status 都宣称「AI 自动交易停止时平仓
    失败」，即便下一轮 Agent 自己已经把仓平掉了。端点侧的用例跑在会话桩上，**这一条必须落在真实
    会话上**，否则守的只是桩自己的行为（节点 #3 复核补）。
    """
    await init_db()
    settings = Settings(ai_trade_funding_enabled=False)
    session = _make_session(settings)
    pushed: list[dict] = []

    async def _capture(event):
        pushed.append(event)

    try:
        session._push = _capture
        failed = {"ok": False, "code": "51023", "message": "no position",
                  "residual": {"side": "long", "size": 1.0}}
        await session._handle_flatten_result(_START_TS, result=failed, terminal=False)
        assert session.flatten_failed is False and session.residual is None
        codes = [e["data"].get("code") for e in pushed if e["type"] == "error"]
        assert codes == ["AI_TRADE_MANUAL_EXIT_FAILED"]
        # 证伪：停止路径（默认 terminal=True）**照旧**置终态字段，逐字未动
        pushed.clear()
        await session._handle_flatten_result(_START_TS, result=failed)
        assert session.flatten_failed is True
        assert [e["data"].get("code") for e in pushed if e["type"] == "error"] == [
            "AI_TRADE_FLATTEN_FAILED"]
    finally:
        await dispose_engine()


# ---------------- G-10：防滥用可证伪 ----------------
async def test_g10_eight_consecutive_finals_burn_the_quota_and_then_stop_trading():
    """G-10：连续 8 轮 `final` ⇒ 前 6 轮放行、后 2 轮降档为 `high` 且**真的没成交**。

    最后半句是本条的要害：只断言 `downgradeReason` 与计数是不够的——若挂起分支对
    `quota_exhausted` 也开着，后 2 轮会挂起待裁决、复核官一句 `confirm` 就照样成交，这条门禁
    在那种设计下**必然是假绿**。故这里同时断言「后 2 轮 `pending is False` 且被门禁拦回 hold」。
    """
    await init_db()
    settings = Settings(ai_trade_funding_enabled=False)      # 默认配额 6 次
    session = _make_session(settings)
    try:
        await _open(session)
        outcomes = []
        for _ in range(8):
            gated, degrade, pending = await _run_exit_round(
                session, _close(exit_conviction="final", exit_evidence=_EVIDENCE),
                streak=2, hold_s=600.0)
            outcomes.append((gated.action, degrade, pending,
                             session._exit_priority.level,
                             session._exit_priority.downgrade_reason))
        released = [o for o in outcomes[:6]]
        blocked = [o for o in outcomes[6:]]
        assert all(action == "close" and level == LEVEL_OVERRIDE and reason is None
                   for action, _, _, level, reason in released)
        for action, degrade, pending, level, reason in blocked:
            assert level == LEVEL_EXPEDITE and reason == "quota_exhausted"
            assert pending is False                # ← 配额用尽**绝不进入裁决通路**
            assert action == "hold"                # ← 且真的没成交（G1 仍然拦着）
            assert degrade.startswith("horizon_min_hold(")
        stats = session._horizon.stats()
        assert stats["exitFinalDeclared"] == 8
        assert stats["exitFinalDowngraded"] == 2
        assert stats["exitOverrideRounds"] == 6
        assert session._horizon.exit_overrides_used == 6      # 配额绝不越过上限
    finally:
        await dispose_engine()


async def test_the_adjudicated_level_is_re_attached_to_the_live_snapshot():
    """裁决重算之后，**快照里挂的必须是新那一份 wire**。

    `adjudicate` 产出的是新对象、新 wire dict；不重挂，`status` 与下一轮 prompt 会显示一个比实际
    更低的档位（而这一轮其实是按 `adjudicated` 放行的）——那正是【R:P1-2】要根除的「`level` 长期
    说谎」，只是换了个载体。
    """
    await init_db()
    settings = Settings(ai_trade_funding_enabled=False)
    session = _make_session(settings)

    async def _reflect(*_a, **_k):
        return ReflectionVerdict("confirm", None, 0.8, "同意离场", None, ran=True)

    try:
        await _open(session)
        session.decider.reflect = _reflect
        await _run_exit_round(
            session, _close(exit_conviction="final", exit_evidence=_EVIDENCE),
            streak=1, hold_s=600.0)
        snap = session._horizon.last_snapshot
        assert snap["exitPriority"]["level"] == LEVEL_ADJUDICATED
        assert snap["exitPriority"] is session._exit_priority.wire
        assert snap["exitPriority"]["pendingReason"].startswith("horizon_min_hold(")
    finally:
        await dispose_engine()


# ---------------- 轴 C 的会话侧编排（W-6 的四步收尾） ----------------
async def test_the_commitment_retry_actually_closes_the_position():
    """W-6：到点重试走**既有**平仓路径，成交后意图清空、持仓归 flat。

    四步收尾（apply → reconcile → `_dispatch` → `_push_status_if_side_changed`）缺一不可：
    第三步才是落库 / 打 `exitSource` / 喂经验层 / 推 WS 的地方，漏掉它这笔交易在所有账本上都不存在。
    """
    await init_db()
    settings = Settings(ai_trade_funding_enabled=False)
    session = _make_session(settings)
    try:
        await _open(session)
        session._arm_exit_commitment(_close(), {"closedFilled": False, "skipped": False})
        assert session._exit_commit.armed
        # 把上次尝试的墙钟拨到重试间隔之前，使本 tick 到点
        from dataclasses import replace as dc_replace
        session._exit_commit = dc_replace(session._exit_commit,
                                          last_attempt_wall=0.0, armed_wall=1.0)
        before = len(session.executor.trades)
        await session._settle_exit_commitment()
        assert session.executor.projected == "flat"
        assert len(session.executor.trades) == before + 1       # 成交真的落到了账本上
        assert session._exit_commit.armed is False              # 完成即清空
    finally:
        await dispose_engine()


async def test_the_commitment_never_retries_while_a_close_order_is_resting():
    """【R:P0-3】的第二层防线在**会话侧**同样成立：有平仓挂单在飞 ⇒ 本 tick 一单不发。"""
    await init_db()
    settings = Settings(ai_trade_funding_enabled=False)
    session = _make_session(settings)
    try:
        await _open(session)
        session._arm_exit_commitment(_close(), {"closedFilled": False, "skipped": False})
        from dataclasses import replace as dc_replace
        session._exit_commit = dc_replace(session._exit_commit,
                                          last_attempt_wall=0.0, armed_wall=1.0)
        # 伪造一张平仓挂单在飞（`_resting_close()` 的唯一判据）
        from app.live_auto.resting import build_resting
        session.executor.resting = build_resting(
            cl_ord_id="c1", ord_id="o1", intent="close", side="long", px=29900.0,
            sz=0.1, strength=0.0, reason="limit exit", now_ts=_START_TS, ttl_s=300.0)
        assert session._resting_close() is True
        before = len(session.executor.trades)
        await session._settle_exit_commitment()
        assert session.executor.projected == "long"             # 一单都没发
        assert len(session.executor.trades) == before
        assert session._exit_commit.armed is True               # 意图仍在，等挂单自己收敛
    finally:
        await dispose_engine()


async def test_a_stuck_commitment_escalates_exactly_once():
    """到顶即推一条 `AI_TRADE_EXIT_STUCK`，且**只推一次**（此后由面板的一键离场接手）。"""
    await init_db()
    settings = Settings(ai_trade_funding_enabled=False)
    session = _make_session(settings)
    pushed: list[dict] = []

    async def _capture(event):
        pushed.append(event)

    try:
        await _open(session)
        session._push = _capture
        session._arm_exit_commitment(_close(), {"closedFilled": False, "skipped": False})
        from dataclasses import replace as dc_replace
        session._exit_commit = dc_replace(session._exit_commit, attempts=5)
        await session._report_exit_commitment([], _START_TS, 1.0)
        assert session._exit_commit.stuck is True
        codes = [e["data"].get("code") for e in pushed if e["type"] == "error"]
        assert codes == [EXIT_STUCK_CODE]
        # 已 stuck ⇒ 后续 tick 不再重试、不再重复告警
        pushed.clear()
        await session._settle_exit_commitment()
        assert [e for e in pushed if e["type"] == "error"] == []
    finally:
        await dispose_engine()
