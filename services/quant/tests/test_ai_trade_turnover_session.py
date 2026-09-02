"""开仓侧换手纪律与兜底核实的会话级集成测试（turnover-budget §6.1 · S 组）。

脱网、桩回调、桩 LLM。重点把守纯函数单测里看不见的六件事：
1. **令牌只在新 epoch 消耗**，加仓不重复消耗（S-1）；
2. **决策链位置**：冷却之后、仓位/刹车之前（S-3）；
3. **[P0-1] 反手的退出腿真的成交了**——断言到 executor 层，不只断言 decision（S-14）；
4. **[P1-3] 冷却到期补推一次 status**（S-15）；
5. **[P0-3] 空仓 prompt 不再说反话**，且未装配时逐字节回退（S-13）；
6. **[P2-2] 轨迹计数不落后一轮**（S-17）。
"""
from __future__ import annotations

import asyncio
import hashlib
import json

import pytest

from app.agent.llm.base import StreamEvent
from app.ai_auto.decider import AiTradeDecider
from app.ai_auto.hold_horizon import (
    PROFILES,
    RELEASE_GATES,
    HorizonRule,
    gated_kind,
    resolve_horizon_rule,
)
from app.ai_auto.hold_horizon_text import format_horizon_for_prompt
from app.ai_auto.horizon_runtime import HorizonRuntime
from app.ai_auto.session import AiTradeSession
from app.ai_auto.turnover_discipline import init_state, rule_from_horizon
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
_NOW = 1_700_000_000.0


def _rule(settings: Settings, profile: str = "intraday_swing") -> HorizonRule:
    return resolve_horizon_rule({"profile": profile}, settings, bar="1H")


class _StubStrategy(BaseStrategy):
    meta = StrategyMeta(key="_stub_turnover_session", name="Stub", description="t",
                        default_params={}, params_schema={})

    def warmup(self) -> int:
        return 1

    def on_candle(self, df, i, ctx):
        return []


class _StubLLM:
    def __init__(self, actions: list[str]):
        self._actions = list(actions)

    async def stream(self, *, system, messages, tools):
        if tools:
            action = self._actions.pop(0) if self._actions else "hold"
            args = {"action": action, "reason": "r", "confidence": 0.7}
            if action in ("open_long", "open_short"):
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


def _make_session(actions: list[str], *, settings: Settings | None = None,
                  horizon_rule: HorizonRule | None = None, creds=None):
    settings = settings or Settings(ai_trade_funding_enabled=False)
    if horizon_rule is None:
        horizon_rule = _rule(settings)

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
        return []

    executor = LiveExecutor(
        creds=creds or AccountCredentials("ak", "sk-000000", "pp-000000"), account_id=1,
        inst_id="BTC-USDT-SWAP", td_mode="cross", pos_mode="long_short_mode",
        leverage=3.0, budget_usd=1000.0, risk=RiskParams(stop_loss_pct=0.05), ct_val=0.01,
        lot_sz=0.01, min_sz=0.01, settings=settings, place_order_fn=place_order_fn,
        close_position_fn=close_position_fn, positions_fn=positions_fn, fills_fn=fills_fn)
    decider = AiTradeDecider(_StubLLM(actions), "sys", context_refresh_rounds=50,
                             settings=settings)
    spec = PaperStrategySpec(1, "S", "atomic", _StubStrategy({}), {})
    session = AiTradeSession("aitrade_turnover", 1, [spec], "BTC-USDT-SWAP", "1H",
                             executor, decider, priming=False, priming_bars=10,
                             context_refresh_rounds=50, horizon_rule=horizon_rule)

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


def _drain(q: asyncio.Queue) -> list[dict]:
    out = []
    while not q.empty():
        out.append(q.get_nowait())
    return out


# ---------------- S-1 / S-4：令牌消耗的单位是「往返」 ----------------
def test_a_token_is_consumed_per_epoch_not_per_fill():
    """S-1 / D-4：开仓成交减一枚；**同 epoch 的加仓成交不再减**（预算的单位是往返）。"""
    settings = Settings()
    rule = _rule(settings)
    runtime = HorizonRuntime(rule, turnover_rule=rule_from_horizon(rule, settings))
    capacity = runtime.turnover_rule.capacity
    runtime.note_open({"side": "long"}, _NOW, seq=1)
    assert runtime.turnover_state.tokens == pytest.approx(capacity - 1.0)
    runtime.note_open({"side": "long"}, _NOW + 30, seq=1)      # 加仓：同一 epoch
    assert runtime.turnover_state.tokens == pytest.approx(capacity - 1.0)
    assert runtime.turnover_state.opens_counted == 1
    runtime.note_close({"side": "close"}, _NOW + 7200)
    runtime.note_open({"side": "short"}, _NOW + 7200, seq=2)   # 新 epoch：再减一枚
    assert runtime.turnover_state.opens_counted == 2
    assert runtime.stats()["opensCounted"] == 2


def test_a_reverse_consumes_a_token_because_it_is_a_real_turnover():
    """S-4：反手产生一个新 epoch ⇒ 照样消耗一枚（它确实是一次完整的换手）。"""
    settings = Settings()
    rule = _rule(settings)
    runtime = HorizonRuntime(rule, turnover_rule=rule_from_horizon(rule, settings))
    runtime.note_open({"side": "long"}, _NOW, seq=1)
    runtime.note_close({}, _NOW + 7200)                        # 反手的平仓腿
    runtime.note_open({"side": "short"}, _NOW + 7200, seq=2)   # 反手的开仓腿
    assert runtime.stats()["opensCounted"] == 2
    assert runtime.stats()["budgetVetoes"] == 0


def test_a_fresh_session_starts_with_a_full_bucket():
    """S-16 [P1-5]：令牌桶是**会话**内存态、启动即满桶 ⇒ 停会话重开即回满。

    这条用例的存在本身就是那句自认（「本会话每日最多 N 次」）的凭据：口径弱不是问题，
    弹窗里写一句做不到的「每日」才是问题。
    """
    settings = Settings()
    rule = _rule(settings)
    turnover = rule_from_horizon(rule, settings)
    assert init_state(turnover, now_wall=_NOW).tokens == turnover.capacity
    runtime = HorizonRuntime(rule, turnover_rule=turnover)
    for seq in range(1, 4):
        runtime.note_open({"side": "long"}, _NOW, seq=seq)
        runtime.note_close({}, _NOW + 60)
    assert runtime.turnover_state.tokens < 1.0                 # 已用尽（期间只补回零头）
    reborn = HorizonRuntime(rule, turnover_rule=rule_from_horizon(rule, settings))
    assert reborn.turnover_state.tokens == turnover.capacity   # 新会话 ⇒ 回满
    wire = reborn.refresh(_facts_flat()).get("turnover")
    assert wire["budgetScope"] == "session"


def _facts_flat(now_wall: float = _NOW):
    from app.ai_auto.hold_horizon import HorizonFacts

    return HorizonFacts(seq=0, side="flat", entry_px=0.0, mark_px=_PX, leverage=3.0,
                        now_wall=now_wall)


# ---------------- S-13 / 放行条件 8b：[P0-3] 空仓 prompt ----------------
def test_flat_prompt_is_byte_identical_when_the_layer_is_not_installed():
    """S-13 前半 / G-1：`turnover` 整键缺席 ⇒ 空仓那一句**逐字节**与本轮之前相同。"""
    settings = Settings(ai_trade_horizon_turnover_enabled=False,
                        ai_trade_backstop_check_enabled=False)
    rule = _rule(settings)
    runtime = HorizonRuntime(rule, turnover_rule=rule_from_horizon(rule, settings))
    wire = runtime.refresh(_facts_flat())
    assert "turnover" not in wire and "backstop" not in wire
    text = format_horizon_for_prompt(wire)
    expected = ("【交易周期 · 日内波段（4 – 12 小时）】\n"
                "  当前空仓：本层**不约束开仓**；开仓后将按上述目标周期约束你的主动平仓。")
    assert text == expected
    assert hashlib.sha256(text.encode()).hexdigest() == \
        hashlib.sha256(expected.encode()).hexdigest()


def test_flat_prompt_never_claims_opens_are_unconstrained_once_installed():
    """S-13 后半 / 放行条件 8b：装配后**绝不**再出现「不约束开仓」四个字。

    不改这条早退，落地后的形态是：每轮告诉模型「开仓不受限」→ 模型开仓 → 被降级为 hold →
    模型把「被拦」当成新证据自我怀疑。那正是 `hold-inertia` 的教训，而且是系统主动说反话。
    """
    settings = Settings()
    rule = _rule(settings)
    runtime = HorizonRuntime(rule, turnover_rule=rule_from_horizon(rule, settings))
    runtime.note_open({"side": "long"}, _NOW, seq=1)
    runtime.note_close({}, _NOW + 7200)                 # 冷却激活
    text = format_horizon_for_prompt(runtime.refresh(_facts_flat()))
    assert "不约束开仓" not in text
    assert "换手纪律" in text and "做多开仓会被拦截" in text or "多头开仓会被拦截" in text
    assert "平仓 `close`、观望 `hold`、撤单 `cancel_order` 不受任何影响" in text


def test_backstop_line_is_byte_identical_when_axis_b_is_not_installed():
    """放行条件 8：总闸关 ⇒ 兜底那一行逐字回退（不开轴 B 的会话 prompt 一个字节都不变）。"""
    from app.ai_auto.hold_horizon import HorizonFacts

    settings = Settings(ai_trade_backstop_check_enabled=False)
    rule = _rule(settings)
    runtime = HorizonRuntime(rule, turnover_rule=None)
    facts = HorizonFacts(seq=1, side="long", entry_px=_PX, mark_px=_PX + 500,
                         leverage=3.0, now_wall=_NOW, exchange_sl_px=29000.0,
                         exchange_tp_px=32000.0)
    runtime.note_open({"side": "long"}, _NOW - 90000, seq=1)
    text = format_horizon_for_prompt(runtime.refresh(facts))
    assert "交易所侧兜底：止盈 32000.000000 / 止损 29000.000000（进程退出后仍生效）。" in text
    assert "尚未向交易所核实" not in text


# ---------------- S-6：G-2 冻结门禁 ----------------
def test_round_one_gate_matrix_is_frozen():
    """S-6 / G-2：豁免矩阵与三道闸的判定式在本轮**一个字符都不动**。

    行为断言而不是源码哈希：哈希由本轮自己生成，证伪能力等于零。
    """
    assert RELEASE_GATES == {
        "loss_release": ("G1", "G2", "G3"),
        "stale_px": ("G1", "G2", "G3"),
        "epoch_estimated": ("G1",),
        "max_hold_bars": ("G1", "G2", "G3"),
        # exit-priority §4.2.3 新增的第五条豁免（轴 A 的第二把钥匙）。**只解 G2/G3**：
        # 按默认派生式，四个预设的 `pending_max = 2 × confirm_window` 与 `min_hold` 完全相等，
        # 故解 G1 在预设上恒为死码；而 `custom` 允许两者脱钩，一旦脱钩解 G1 就是一个真实的洞。
        # 上面四条的**逐格取值在本轮一个字符都没动**——这才是本断言真正冻结的东西。
        "exit_pending": ("G2", "G3"),
        # ai-trade-loss-and-liveness-hardening 轴 B：`loss_release` 的**同位替换**（同一个 `if`
        # 分支按交易所兜底覆盖态返回两者之一），**不占新位次、既有四行逐字未动**。
        # 只解 G2/G3 的理由：真止损位已由交易所侧 OCO 兜底，`loss_release` 实际提供的是一条
        # 「在半止损位由 LLM 自由裁量提前割肉」的通道——实测 13 笔合计 −40.89 USDT。
        "loss_release_covered": ("G2", "G3"),
    }
    for action, side, expected in [
            ("close", "long", "close"), ("close", "short", "close"),
            ("close", "flat", None), ("hold", "long", None),
            ("cancel_order", "long", None),
            ("open_long", "flat", None), ("open_short", "flat", None),
            ("open_long", "long", None), ("open_short", "short", None),
            ("open_long", "short", "reverse"), ("open_short", "long", "reverse")]:
        assert gated_kind(action, side, gate_reverse=True) == expected, (action, side)


# ---------------- S-9：验收卡自认 ----------------
def test_acceptance_row_three_discloses_that_it_became_a_gate():
    """S-9 / §2.2.6：武装后第 3 行 note 追加自认句（含「本会话」口径）；未武装 ⇒ 逐字节不变。"""
    from app.ai_auto.acceptance_text import turnover_note

    plain = turnover_note(1.2, 6.0, 6.0)
    assert turnover_note(1.2, 6.0, 6.0, gate_armed=False, gate_vetoes=0) == plain
    armed = turnover_note(1.2, 6.0, 6.0, gate_armed=True, gate_vetoes=7)
    assert armed.startswith(plain)
    assert "本会话" in armed and "会话重启后预算重新计算" in armed
    assert "实际拦下 7 次" in armed and "请以第 1、4、5 行为准" in armed


def test_acceptance_input_carries_the_gate_disclosure_from_the_runtime():
    """自认的两个字段由运行时的原始计数器一路带到验收卡（后端算了 ≠ 用户看得见）。"""
    from app.ai_auto.horizon_acceptance import build_input, resolve_thresholds

    settings = Settings()
    rule = _rule(settings)
    runtime = HorizonRuntime(rule, turnover_rule=rule_from_horizon(rule, settings))
    runtime.note_open({"side": "long"}, _NOW, seq=1)
    runtime.note_close({}, _NOW + 20000)
    parts = runtime.acceptance_input_parts()
    assert parts["turnoverGateArmed"] is True
    inp = build_input(horizon_parts=parts, lifetime_cost={}, exit_stats=None,
                      funding_stats=None, wall_seconds=86400.0)
    assert inp.turnover_gate_armed is True
    assert resolve_thresholds(settings, rule).max_turnover_per_day == rule.turnover_per_day


# ---------------- S-15：[P1-3] 冷却到期补推 ----------------
def test_cooldown_expiry_fires_exactly_once():
    """S-15 [P1-3]：由激活翻为未激活的那一 tick 恰好返回一次 True（前端 chip 不会停在「冷却中」）。"""
    settings = Settings()
    rule = _rule(settings)
    runtime = HorizonRuntime(rule, turnover_rule=rule_from_horizon(rule, settings))
    runtime.note_open({"side": "long"}, _NOW, seq=1)
    runtime.note_close({}, _NOW + 7200)
    cooldown_s = runtime.turnover_rule.reentry_cooldown_s
    assert runtime.expire_turnover_cooldown(_NOW + 7200 + 1) is False      # 仍在冷却
    assert runtime.expire_turnover_cooldown(_NOW + 7200 + cooldown_s + 1) is True
    assert runtime.expire_turnover_cooldown(_NOW + 7200 + cooldown_s + 2) is False
    wire = runtime.refresh(_facts_flat(_NOW + 7200 + cooldown_s + 2))["turnover"]
    assert wire["cooldownActive"] is False
    assert runtime.refresh(_facts_flat(_NOW + 7201))["turnover"]["cooldownActive"] is True


# ---------------- S-17：[P2-2] 轨迹计数不落后一轮 ----------------
def test_veto_counters_are_mutated_in_place_so_the_trace_is_current():
    """S-17 [P2-2]：trace 存的是同一个 dict 引用，故被拦轮的 `budgetVetoes` 必须已经 +1。"""
    settings = Settings()
    rule = _rule(settings)
    runtime = HorizonRuntime(rule, turnover_rule=rule_from_horizon(rule, settings))
    for seq in range(1, 4):                       # 打空令牌桶（容量 3）
        runtime.note_open({"side": "long"}, _NOW, seq=seq)
        runtime.note_close({}, _NOW + 60)
    snapshot = runtime.refresh(_facts_flat())
    traced = snapshot["turnover"]                 # 轨迹持有的正是这个引用
    assert traced["budgetVetoes"] == 0
    from app.ai_auto.decider import TradeDecision

    runtime.turnover_state = runtime.turnover_state.__class__(
        **{**runtime.turnover_state.__dict__, "cooldown_until_wall": 0.0})
    snapshot = runtime.refresh(_facts_flat())
    traced = snapshot["turnover"]
    gated, degrade = runtime.turnover_gate(TradeDecision("open_long", 0.5, 0.7, "s"),
                                           None, position_side="flat", now_wall=_NOW)
    assert gated.action == "hold" and degrade.startswith("horizon_turnover_budget")
    assert traced["budgetVetoes"] == 1            # 同一个 dict，已就地 +1


# ---------------- S-3 / S-14：决策链 ----------------
async def test_turnover_gate_sits_between_cooldown_and_sizing(monkeypatch):
    """S-3：插入位序——冷却之后、仓位/刹车之前（`degrade or reason` 的归因才正确）。"""
    order: list[str] = []
    await init_db()
    session = _make_session(["hold"])
    original_cooldown = session._apply_profit_cooldown_gate
    original_turnover = session._horizon.turnover_gate
    original_sizing = session._sizing.gate

    def cooldown(*a, **kw):
        order.append("cooldown")
        return original_cooldown(*a, **kw)

    def turnover(*a, **kw):
        order.append("turnover")
        return original_turnover(*a, **kw)

    def sizing(*a, **kw):
        order.append("sizing")
        return original_sizing(*a, **kw)

    monkeypatch.setattr(session, "_apply_profit_cooldown_gate", cooldown)
    monkeypatch.setattr(session._horizon, "turnover_gate", turnover)
    monkeypatch.setattr(session._sizing, "gate", sizing)
    try:
        await session.feed_candle(_candle(0))
        await _settle(session)
        assert order == ["cooldown", "turnover", "sizing"]
    finally:
        await dispose_engine()


async def test_a_blocked_reverse_still_executes_its_exit_leg():
    """S-14 [P0-1] / 放行条件 6：断言到 **executor 层**——只断言 decision 证明不了持仓真的走掉了。

    这是本轮唯一一条安全不变式的实证：换手预算绝不把一笔已有持仓留在原地。

    契约刻意把 G1/G2/G3 全部关掉、只留 G5：第 1 轮的最短持仓窗口对反手本就会先降级为 `hold`，
    不关掉它，本用例断言的就不是换手闸的行为（这正是「判据取自不携带该信息的断面」那类错）。
    """
    await init_db()
    settings = Settings(ai_trade_funding_enabled=False)
    rule = resolve_horizon_rule(
        {"profile": "intraday_swing", "minHoldSeconds": 0, "exitConfirmRounds": 1,
         "turnoverHysteresisK": 0}, settings, bar="1H")
    assert rule.turnover_per_day == 6.0 and rule.reentry_cooldown_s == 0.0
    session = _make_session(["open_long", "open_short"], settings=settings,
                            horizon_rule=rule)
    try:
        await session.feed_candle(_candle(0))
        await _settle(session)
        assert session.executor.projected == "long"
        # 人为把令牌打空：下一轮的反手必须只否掉再入场腿。
        state = session._horizon.turnover_state
        session._horizon.turnover_state = state.__class__(
            **{**state.__dict__, "tokens": 0.0, "cooldown_until_wall": 0.0})
        q = session.subscribe()
        await session.feed_candle(_candle(1, _PX + 100))
        await _settle(session)
        events = _drain(q)
        decision = [e["data"] for e in events if e["type"] == "decision"][-1]
        assert decision["action"] == "close"
        assert "reverse→close" in (decision["degradeReason"] or "")
        assert session.executor.projected == "flat"      # ★ 退出腿真的执行了
        assert any(e["type"] == "trade" and e["data"]["side"] == "close"
                   for e in events)
    finally:
        await dispose_engine()


async def test_status_and_trace_carry_the_new_nested_wires():
    """[P1-1]/[P1-2]：新 wire 挂在 `horizon.turnover`（不是 `tradingHorizon`），status 与 trace 同源。"""
    await init_db()
    session = _make_session(["open_long"])
    try:
        q = session.subscribe()
        await session.feed_candle(_candle(0))
        await _settle(session)
        status = session._status_payload()["horizon"]
        assert "turnover" in status and status["turnover"]["budgetScope"] == "session"
        assert "backstop" in status
        traces = [e["data"] for e in _drain(q)
                  if e["type"] == "agent_trace" and e["data"]["phase"] == "horizon"]
        assert traces and "turnover" in traces[-1]["horizon"]
    finally:
        await dispose_engine()


async def test_no_request_is_sent_while_flat_or_without_credentials():
    """S-12 / B-10：无仓 ⇒ 一个请求都不发；`basis` 如实降级为 `local(not_run)`。"""
    await init_db()
    session = _make_session(["hold"])
    calls = {"n": 0}

    async def counting_fetch(*_a, **_kw):
        calls["n"] += 1
        return []

    session._backstop.creds = AccountCredentials("ak", "sk-000000", "pp-000000")
    import app.ai_auto.exchange_backstop as eb

    original = eb.fetch_algo_backstop
    eb.fetch_algo_backstop = counting_fetch
    try:
        await session._monitor_tick()
        await asyncio.sleep(0)
        assert session.executor.projected == "flat"
        assert calls["n"] == 0
        wire = session._backstop.wire(_NOW)
        assert wire["basis"] == "local" and wire["unavailableReason"] == "not_run"
    finally:
        eb.fetch_algo_backstop = original
        await dispose_engine()


async def test_monitor_tick_is_never_blocked_by_a_slow_backstop_fetch():
    """S-11：造一个 3s 才返回的假取数，断言盯市 tick 耗时 < 1s（单飞后台任务）。"""
    await init_db()
    session = _make_session(["hold"])

    async def slow_fetch(*_a, **_kw):
        await asyncio.sleep(3.0)
        return []

    import app.ai_auto.exchange_backstop as eb

    original = eb.fetch_algo_backstop
    eb.fetch_algo_backstop = slow_fetch
    session._backstop._side = "long"
    try:
        loop = asyncio.get_running_loop()
        started = loop.time()
        session._backstop.schedule_check(_NOW, side="long", position_sz=3.0,
                                         local_sl_px=29000.0, local_tp_px=None)
        elapsed = loop.time() - started
        assert elapsed < 1.0
        assert session._backstop._task is not None
        session._backstop.close()
        session._backstop._task.cancel()
    finally:
        eb.fetch_algo_backstop = original
        await dispose_engine()


def test_a_stopped_session_never_writes_state_from_an_in_flight_check():
    """§2.3.1 约束 5：`stop()` 之后在途任务回来也不再写 `last`、不再推事件。"""
    from app.ai_auto.exchange_backstop import BackstopRuntime

    runtime = BackstopRuntime(Settings(), None, "BTC-USDT-SWAP")
    runtime.close()
    runtime.schedule_check(_NOW, side="long", position_sz=3.0, local_sl_px=1.0,
                           local_tp_px=None)
    assert runtime._task is None and runtime.last_orders is None


# ---------------- S-10：回测不装配轴 B ----------------
def test_backtest_never_reports_a_verified_backstop():
    """S-10 / D-5：回测没有交易所——显示一个「已核实」的绿勾，正是本轮要根除的那类谎言的镜像。"""
    settings = Settings()
    rule = _rule(settings)
    runtime = HorizonRuntime(rule, record_exits=False,
                             turnover_rule=rule_from_horizon(rule, settings))
    snapshot = runtime.refresh(_facts_flat())
    assert "backstop" not in snapshot
    assert "turnover" in snapshot                 # 轴 A 照常装配（回测有它自己的总闸）


def test_backtest_master_switch_removes_the_turnover_layer_entirely():
    """回测侧独立总闸：关 ⇒ 两道新闸整层空转、`turnover` 整键缺席（G-3 逐字回退）。"""
    settings = Settings(ai_trade_horizon_turnover_backtest_enabled=False)
    rule = _rule(settings)
    turnover = (rule_from_horizon(rule, settings)
                if settings.ai_trade_horizon_turnover_backtest_enabled else None)
    runtime = HorizonRuntime(rule, record_exits=False, turnover_rule=turnover)
    assert "turnover" not in runtime.refresh(_facts_flat())


def test_backtest_notes_disclose_all_three_caveats():
    """§2.4.4：三句免责（冷却粒度 / 回测无轴 B / 反手降级改变退出来源分布）。"""
    from app.ai_auto.hold_horizon_text import format_backtest_notes

    settings = Settings()
    rule = resolve_horizon_rule({"profile": "hourly_swing"}, settings, bar="5m")
    turnover = rule_from_horizon(rule, settings)
    notes = format_backtest_notes(rule, 300, turnover=turnover)
    joined = "\n".join(notes)
    assert "回测中恒不生效" in joined                 # 90s 冷却 < 一根 5m
    assert "回测**不装配**交易所侧兜底核实" in joined
    assert "记为「Agent 主动平仓」而非「Agent 反手」" in joined
    # 未装配 ⇒ 三句整体省略（既有回测 notes 逐字节不变）。
    assert format_backtest_notes(rule, 300, turnover=None) == \
        format_backtest_notes(rule, 300)


def test_profiles_wire_carries_previews_and_ops_flags():
    """§4.1：三个预览字段 + 三个运维标志；`custom` 恒 `null`（前端不硬抄任何一个数）。"""
    from app.ai_auto.hold_horizon_profiles import profiles_wire

    wire = profiles_wire(Settings())
    assert wire["turnoverGateEnabled"] is True and wire["backstopCheckEnabled"] is True
    assert wire["backstopPollSeconds"] == 120.0
    by_id = {p["id"]: p for p in wire["profiles"]}
    assert by_id["intraday_swing"]["reentryCooldownSeconds"] == 3600.0
    assert by_id["intraday_swing"]["turnoverBudgetPerDay"] == 6.0
    assert by_id["intraday_swing"]["turnoverBudgetCapacity"] == 3.0
    for key in ("reentryCooldownSeconds", "turnoverBudgetPerDay",
                "turnoverBudgetCapacity"):
        assert by_id["custom"][key] is None
    for profile_id, profile in PROFILES.items():
        if profile.target_hold_min_s:
            assert by_id[profile_id]["turnoverBudgetPerDay"] == \
                resolve_horizon_rule({"profile": profile_id}, Settings(),
                                     bar=profile.bar).turnover_per_day
