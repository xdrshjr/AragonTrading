"""会话层的暂停 / 继续（ai-trade-pause-resume §3.1）：判据重定向表 + `pause()` 动作顺序。

本文件守的是本轮**最危险**的一条不变式 I-1：`paused` 只封锁「增风险」侧（起新决策轮 / 开仓 /
加仓 / 挂新限价单），**减风险侧一律照旧**（保护性平仓、会话止盈止损、盯市 reconcile、交易所侧
兜底核实）。反例说明为什么它必须被用例钉死：如果暂停顺手把利润保护也停了，那么「用户暂停后去
睡觉」这条路径上，一个已经浮盈 8% 的仓位会失去它唯一的本地退出执行者——而用户以为自己做的是
一个**降低**风险的动作。

行情 / OKX / LLM 全程桩驱动（直接注入 `feed_candle`），铁律：占位假凭证。
"""
from __future__ import annotations

import asyncio
import inspect
import json
import re
import time
from pathlib import Path

from app.agent.llm.base import StreamEvent
from app.ai_auto.decider import AiTradeDecider
from app.ai_auto.profit_guard import ProfitGuardRule
from app.ai_auto.session import AiTradeSession
from app.config import Settings
from app.db.session import dispose_engine, get_sessionmaker, init_db
from app.engine.simulator import RiskParams
from app.live_auto.executor import LiveExecutor
from app.okx.accounts import AccountCredentials
from app.okx.types import Candle
from app.paper.strategy_factory import PaperStrategySpec
from app.strategies.base import BaseStrategy, StrategyMeta

_START_TS = 1_700_000_000
_STEP = 3600
_PG_ENTRY = 0.07245
_PG_LEV = 5.0
_PG_QTY = 100.0


class _StubStrategy(BaseStrategy):
    meta = StrategyMeta(key="_stub_pause_sess", name="Stub", description="t",
                        default_params={}, params_schema={})

    def warmup(self) -> int:
        return 1

    def on_candle(self, df, i, ctx):
        return []


class _StubLLM:
    async def stream(self, *, system, messages, tools):
        if tools:
            args = {"action": "open_long", "reason": "r", "confidence": 0.9,
                    "sizeFraction": 0.5}
            yield StreamEvent("tool_use_start", id="t", name="submit_trade_decision")
            yield StreamEvent("tool_use_args_delta", id="t",
                              partial_json=json.dumps(args, ensure_ascii=False))
            yield StreamEvent("message_done", stop_reason="tool_use")
        else:
            yield StreamEvent("text_delta", text="摘要")
            yield StreamEvent("message_done", stop_reason="end")


async def _no_trade_fee(*_a, **_k):
    return None


async def _no_position_history(*_a, **_k):
    return []


def _candle(idx: int, price: float = 30000.0, *, confirm: bool = True) -> Candle:
    return Candle(ts=_START_TS + idx * _STEP, o=price, h=price + 5, l=price - 5,
                  c=price, vol=100.0, confirm=confirm)


def _make_session(*, settings=None, profit_rule=None, positions=None):
    """脱网会话：所有 OKX 写 / 读缝均为桩，`positions_fn` 可注入交易所侧持仓断面。"""
    settings = settings or Settings()
    calls = {"orders": [], "closes": [], "cancels": [], "positions": 0}

    async def place_order_fn(order):
        calls["orders"].append(order)
        return {"ok": True, "ordId": "x", "clOrdId": "aragonx"}

    async def close_position_fn(close):
        calls["closes"].append(close)
        return {"ok": True, "clOrdId": "aragoncx"}

    async def positions_fn():
        calls["positions"] += 1
        if positions is None:
            ex = session.executor
            if ex.projected == "flat":
                return []
            return [{"instId": "BTC-USDT-SWAP", "pos": ex.entry_qty_sz, "upl": 0.0}]
        return positions() if callable(positions) else positions

    async def cancel_order_fn(cancel):
        calls["cancels"].append(cancel)
        return {"outcome": "canceled"}

    executor = LiveExecutor(
        creds=AccountCredentials("ak", "sk-000000", "pp-000000"), account_id=1,
        inst_id="BTC-USDT-SWAP", td_mode="cross", pos_mode="long_short_mode",
        leverage=3.0, budget_usd=1000.0, risk=RiskParams(), ct_val=0.01,
        lot_sz=0.01, min_sz=0.01, settings=settings,
        place_order_fn=place_order_fn, close_position_fn=close_position_fn,
        positions_fn=positions_fn, cancel_order_fn=cancel_order_fn)
    decider = AiTradeDecider(_StubLLM(), "sys", context_refresh_rounds=10, settings=settings)
    spec = PaperStrategySpec(1, "S", "atomic", _StubStrategy({}), {})
    session = AiTradeSession("aitrade_pause", 1, [spec], "BTC-USDT-SWAP", "1H", executor,
                             decider, priming=False, priming_bars=10,
                             profit_rule=profit_rule)

    async def no_htf_data(*_args, **_kwargs):
        return []

    session._window._safe_get_candles = no_htf_data
    session._cost._fetch_fee_rate = _no_trade_fee
    session._cost._fetch_position_history = _no_position_history
    return session, calls


def _open_long(session, *, entry: float = _PG_ENTRY, qty: float = _PG_QTY) -> None:
    ex = session.executor
    ex.leverage = _PG_LEV
    ex.projected = "long"
    ex.entry_price = entry
    ex.entry_qty_sz = qty
    ex.entry_bar = 0
    ex.entry_position_pct = 0.5
    session._last_live_px = entry
    session._last_px_wall = time.time()


def _drain(q: asyncio.Queue) -> list[dict]:
    out: list[dict] = []
    while not q.empty():
        out.append(q.get_nowait())
    return out


# ---------------- G-1：暂停期盯市循环仍活，reconcile 仍在跑 ----------------
async def test_pause_keeps_monitor_alive_and_reconcile_running():
    """G-1（D-5 的直接回归守卫）：`pause()` 后 monitor 任务仍存活，且 tick 内 reconcile 被调用。"""
    await init_db()
    session, calls = _make_session()
    try:
        _open_long(session)
        session._monitor_task = asyncio.create_task(session._monitor_loop())
        await session.pause("user")

        assert session.state == "paused" and session.is_active
        await asyncio.sleep(0)
        assert not session._monitor_task.done()      # 循环判据是 is_active，不是 == "running"

        before = calls["positions"]
        await session._monitor_tick()
        assert calls["positions"] > before           # executor.reconcile 真的读了一次持仓
    finally:
        session._cancel_task("_monitor_task")
        await dispose_engine()


# ---------------- G-2 / G-32：暂停期 K 线只入窗、只步进影子，绝不起决策轮 ----------------
async def test_paused_candle_feeds_window_but_never_starts_a_decision_round(monkeypatch):
    """G-2 + G-32：窗口 +1、`_last_px_wall` 刷新、影子步进一次、HTF/战术零调用、决策任务恒 None。"""
    await init_db()
    session, _ = _make_session()
    try:
        await session.feed_candle(_candle(0))        # running：建立基线（会起一轮）
        session._cancel_task("_decision_task")
        session._deciding = False
        await session.pause("user")

        htf = {"n": 0}
        tactical = {"n": 0}

        async def _htf(*_a, **_k):
            htf["n"] += 1

        async def _tactical(*_a, **_k):
            tactical["n"] += 1

        monkeypatch.setattr(session._window, "maybe_refresh_htf", _htf)
        monkeypatch.setattr(session._window, "maybe_refresh_tactical", _tactical)
        board_calls = {"n": 0}
        from app.ai_auto import session as session_module

        original_board = session_module.evaluate_signal_board   # 先取原函数，否则 spy 递归自己

        def _spy_board(*args, **kwargs):
            board_calls["n"] += 1
            return original_board(*args, **kwargs)

        monkeypatch.setattr(session_module, "evaluate_signal_board", _spy_board)

        bars_before = len(session._window.candles)
        q = session.subscribe()
        await session.feed_candle(_candle(1, price=30100.0))

        assert len(session._window.candles) == bars_before + 1      # row 16：仍要入窗
        assert session._last_live_px == 30100.0                     # 判定价真源被刷新
        assert session._last_px_wall > 0
        assert board_calls["n"] == 1                                # row 17 插点不得上移
        assert session._last_bar_index == session._window.cur_index()
        assert htf["n"] == 0 and tactical["n"] == 0                 # row 12b：暂停期不烧 REST
        assert session._decision_task is None                       # row 17：绝不起决策轮
        assert [e["type"] for e in _drain(q) if e["type"] == "candle"] == ["candle"]
    finally:
        await dispose_engine()


# ---------------- G-3（冻结）：判据点位清单，不写死任何计数 ----------------
_SESSION_PATH = Path(__file__).resolve().parents[1] / "app" / "ai_auto" / "session.py"

# 每一行 `self.state` 的出现都必须在这份清单里有归属说明。**刻意不写死总数**（v1 的 `== 19` 实测
# 应为 16，这类门禁一旦「第一天就红」就会被顺手注释掉，比没有更坏）。
_STATE_SITES = {
    'self.state = "running"': "初始化 / resume 赋值（非判定）",
    'self.state = "paused"': "pause() 赋值（非判定）",
    'self.state = "stopped"': "stop() 赋值（非判定）",
    "return self.state in _ACTIVE_STATES": "is_active 的唯一定义（§3.1.1）",
    'if self.state != "running":': "增风险侧的门：feed_candle 决策起点 / _execute_* 内层复检",
    'if (self.wake_interval_s <= 0 or self.state != "running"': "定时唤醒（增风险侧入口）",
    'if (self.state != "running" or self._deciding': "补跑唤醒（增风险侧入口）",
    'return (self.state == "running" and not self._stale_degraded': "_preview_ready（翻转唤醒的门）",
    'if self.state == "running":': "row 12 / 12b：暂停期跳过只服务决策轮的 REST 取数",
    'if self.state != "paused":': "resume() 的前置校验（生命周期转移，非风控判据）",
    '"state": self.state': "status / info / pause·resume 回执的回显（非判定）",
}


def _source_lines() -> list[str]:
    """按 `\\r?\\n` 切行（core.autocrlf=true：写死 `\\n` 在干净检出上恒红）。"""
    return re.split(r"\r?\n", _SESSION_PATH.read_text(encoding="utf-8"))


def test_state_predicate_sites_are_frozen_and_classified():
    """G-3 ①：`self.state` 的**每一处**出现都必须能在点位清单里找到归属（两边都没有即红）。"""
    unclassified = []
    for line in _source_lines():
        stripped = line.strip()
        # `` 词界：`self.state_member_row()` 不是一处 `self.state` 判据点。
        if not re.search(r"self\.state", stripped) or stripped.startswith("#"):
            continue
        if not any(key.strip().rstrip("\n") in stripped for key in _STATE_SITES):
            unclassified.append(stripped)
    assert unclassified == [], f"未归类的 self.state 判据点：{unclassified}"


def test_no_third_form_of_state_predicate():
    """G-3 ②：只允许四种形态；禁止 `state != "stopped"` / `state in (` 这类自造判据。"""
    source = _SESSION_PATH.read_text(encoding="utf-8")
    assert 'state != "stopped"' not in source
    assert re.search(r"self\.state\s+in\s+\(", source) is None
    assert re.search(r"self\.state\s+not\s+in\s+", source) is None


def test_is_active_is_the_only_active_state_definition():
    """G-3 ②补：`_ACTIVE_STATES` 只被 `is_active` 一处消费（避免第二份「活跃」定义）。"""
    source = _SESSION_PATH.read_text(encoding="utf-8")
    assert source.count("_ACTIVE_STATES") == 2      # 常量定义 + is_active 各一次


# ---------------- G-4：暂停期利润保护**照旧平仓**（I-1 的招牌用例）----------------
async def test_profit_guard_still_closes_position_while_paused():
    """G-4：暂停期浮盈自峰值回吐超阈 ⇒ **真的产生平仓成交**。"""
    await init_db()
    rule = ProfitGuardRule()
    session, _ = _make_session(profit_rule=rule)
    try:
        _open_long(session)
        await session.pause("user")
        peak = _PG_ENTRY * (1 + 0.10 / _PG_LEV)
        session._last_live_px = peak
        session._refresh_profit_state(time.time())
        session._guard.state.opened_wall -= 300.0
        session._last_live_px = _PG_ENTRY + (peak - _PG_ENTRY) * (1 - rule.giveback_pct)

        q = session.subscribe()
        await session._maybe_profit_exit(_START_TS)

        trades = [e["data"] for e in _drain(q) if e["type"] == "trade"]
        assert [t["side"] for t in trades] == ["close"]
        assert session.state == "paused"          # 保护性平仓不改变生命周期
    finally:
        await dispose_engine()


# ---------------- G-5：暂停期会话止损触发 ⇒ 进入 stopped 并回调 W5 ----------------
async def test_session_stop_loss_still_fires_while_paused_and_notifies_state_row():
    """G-5：暂停期会话止损触发 ⇒ `state == "stopped"` 且 W5 回调被调用（状态表置 ended 的落点）。"""
    await init_db()
    session, _ = _make_session()
    notified = {"n": 0}

    async def _on_stopped(_session):
        notified["n"] += 1

    try:
        session.stop_loss_usd = 10.0
        session.on_stopped = _on_stopped
        await session.pause("user")
        session.executor.realized_pnl_usd = -50.0

        await session._maybe_session_exit(30000.0, _START_TS)

        assert session.state == "stopped" and session.stop_reason == "session_stop_loss"
        assert notified["n"] == 1
    finally:
        await dispose_engine()


# ---------------- G-6：pause 与持锁在飞的决策轮竞态 ----------------
async def test_pause_drains_exec_lock_and_emits_no_new_order():
    """G-6：`pause()` 与一个持锁在飞的决策轮竞态 ⇒ 没有任何新订单发出，`_deciding` 最终为 False。"""
    await init_db()
    session, calls = _make_session()
    released = asyncio.Event()

    async def _hold_lock():
        async with session._exec_lock:
            await released.wait()

    try:
        holder = asyncio.create_task(_hold_lock())
        await asyncio.sleep(0)
        pause_task = asyncio.create_task(session.pause("user"))
        await asyncio.sleep(0)
        assert not pause_task.done()          # ② 正卡在排空 _exec_lock 上
        assert session.state == "paused"       # ① 已先置态：此后没拿到锁的在飞轮不可能再下单
        released.set()
        await holder
        await pause_task

        assert calls["orders"] == [] and calls["closes"] == []
        assert session._deciding is False
    finally:
        await dispose_engine()


# ---------------- G-7：暂停时撤掉未成交挂单 ----------------
async def test_pause_cancels_resting_order_and_reports_outcome():
    """G-7：暂停时存在未成交限价挂单 ⇒ `cancel_resting` 被调用一次，回执含 `restingCancelled`。"""
    await init_db()
    from app.live_auto.resting import build_resting

    session, calls = _make_session()
    try:
        session.executor.resting = build_resting(
            cl_ord_id="aragon18f", ord_id="o1", intent="open", side="long", px=30000.0,
            sz=1.0, strength=1.0, reason="test", now_ts=_START_TS, ttl_s=600.0)
        result = await session.pause("user")

        assert len(calls["cancels"]) == 1
        assert result["restingCancelled"] == {"outcome": "cancelled", "clOrdId": "aragon18f"}
    finally:
        await dispose_engine()


async def test_pause_with_flatten_reports_a_failed_flatten_instead_of_swallowing_it():
    """RV-2：「平仓后暂停」若平仓失败，必须与 `stop()` 走**同一条**收尾。

    否则它会成为全仓唯一一条「平仓失败却静默」的路径：没有 `flatten_failed`、没有 `residual`、
    没有 `AI_TRADE_FLATTEN_FAILED` 强提示——用户以为自己空仓关机了，交易所上其实还留着一笔
    无人看管的真实仓位。
    """
    await init_db()
    session, calls = _make_session()

    async def _failing_flatten(_px, _ts):
        return {"ok": False, "code": "LIVE_CLOSE_FAILED",
                "residual": {"side": "long", "size": 0.6}}

    try:
        _open_long(session)
        session.executor.flatten = _failing_flatten
        q = session.subscribe()
        result = await session.pause("user", flatten=True)

        assert session.state == "paused"            # 暂停仍然完成（减风险动作恒可执行）
        assert result["flattened"]["ok"] is False
        assert session.flatten_failed is True
        assert session.residual == {"side": "long", "size": 0.6}
        errors = [e["data"]["code"] for e in _drain(q) if e["type"] == "error"]
        assert errors == ["AI_TRADE_FLATTEN_FAILED"]
    finally:
        await dispose_engine()


# ---------------- pause 幂等 + 时钟重置 + 「读不到 ≠ 没有」 ----------------
async def test_pause_is_idempotent_and_resume_rearms_the_decision_clock():
    """幂等：非 running 直接回上次结果；`resume()` 让首轮定时唤醒在 ~wake_interval_s 之后。

    ⚠️ 本用例在 ai-trade-lifecycle-truth B-4 之后改了断言：`_last_candle_wall` / `_last_px_wall`
    **不再被无条件重置为 now**（那正是 D-7 的根因——续跑后一条已经死了的行情要再过 `bar × 3`
    才被说出来）。它们现在只被「扣掉暂停这一段」，且绝不越过 now。只有决策计时器仍然重置，
    理由与 `start()` 同一条（首轮唤醒在 ~wake_interval_s 后，而不是 t=0 连打）。
    """
    await init_db()
    session, _ = _make_session()
    try:
        first = await session.pause("user")
        second = await session.pause("user")
        assert second is first

        session._last_decision_started_wall = 0.0
        session._last_candle_wall = session._last_px_wall = 0.0
        await session.resume()

        assert session.state == "running" and session.pause_reason is None
        assert session._last_decision_started_wall > 0
        assert session._deciding is False
    finally:
        session._cancel_task("_monitor_task")
        await dispose_engine()


# ---------------- 用例 8 / 22（D-7 + [评审 R0-3]）：诚实的陈旧时钟 ----------------
async def test_resume_does_not_reset_the_stale_clock():
    """用例 8（D-7）：`resume()` **不再**把陈旧时钟抹平。

    行情在暂停之前就已经断了 110 分钟的话，续跑后那个年龄必须**仍然是 110 分钟**，
    使下一个 `_check_staleness` 立即出声——而不是再骗用户一个完整的 `bar × 3` 窗口。
    """
    await init_db()
    session, _ = _make_session()
    try:
        now = time.time()
        session._last_candle_wall = session._last_px_wall = now - 5 * 3600.0   # 5 小时前
        await session.pause("user")
        session.paused_ts = int(now - 600)                                     # 暂停了 10 分钟
        await session.resume()

        age = time.time() - session._last_candle_wall
        assert 17_300 < age < 17_500, age        # ≈4 小时 50 分（5 小时 − 10 分钟），不是 0
        # 1H 周期的陈旧阈值 = 3600 × live_auto_staleness_factor(3) = 3 小时，故这一刻**已经**超了。
        await session._check_staleness(int(time.time()))
        assert session._stale_degraded is True   # 立即判陈旧，不用再等 bar×3
    finally:
        session._cancel_task("_monitor_task")
        await dispose_engine()


async def test_resume_never_pushes_the_feed_clock_into_the_future():
    """用例 22（[评审 R0-3]）：夹取不可省。

    暂停期 WS 不停、`feed_candle` 的顶部判据是 `is_active`，故这两个时钟在暂停期**照常被刷新
    到当前**。无夹取地 `+= paused_span` 会把它们推到**未来** ⇒ 接下来整整 paused_span 里行情
    真的死了也判不出陈旧 —— 在一条风控读数上开 fail-open，与 D-7 的立论正好反向。
    """
    await init_db()
    session, _ = _make_session()
    try:
        await session.pause("user")
        session.paused_ts = int(time.time() - 3 * 3600)          # 暂停了 3 小时
        session._last_candle_wall = session._last_px_wall = time.time()   # WS 一直在刷
        await session.resume()

        now = time.time()
        assert session._last_candle_wall <= now + 0.001
        assert session._last_px_wall <= now + 0.001
        # 随后人为静默 stale_after + 1 秒即判陈旧（**不是**要再等 3 小时）。
        stale_after = 3600 * session.executor.settings.live_auto_staleness_factor
        session._last_candle_wall = now - stale_after - 1
        await session._check_staleness(int(now))
        assert session._stale_degraded is True
    finally:
        session._cancel_task("_monitor_task")
        await dispose_engine()


# ---------------- 用例 9 / 23（[评审 R0-4]）：`_rearm_feed` 换代而不是重启 ----------------
class _FakeWS:
    """`OKXWSClient` 的最小替身：只要 `is_alive` / `start` / `stop` 三个面。"""

    def __init__(self, alive: bool = True):
        self.is_alive = alive
        self.started = 0
        self.stopped = 0

    def start(self) -> None:
        self.started += 1

    async def stop(self) -> None:
        self.stopped += 1


async def test_rearm_feed_is_a_noop_while_the_client_is_alive():
    """用例 23 上半：`is_alive` 为真 ⇒ **对象未被更换**（正常热续跑路径零动作）。"""
    await init_db()
    session, _ = _make_session()
    try:
        ws = _FakeWS(alive=True)
        session._ws = ws
        await session._rearm_feed()
        assert session._ws is ws and ws.stopped == 0 and ws.started == 0
    finally:
        await dispose_engine()


async def test_rearm_feed_builds_a_new_client_when_the_old_one_died(monkeypatch):
    """用例 9 / 23 下半：`is_alive` 为假 ⇒ 换**新对象**并 `start()`；旧的先被 `stop()` 收尸。

    [评审 R0-4]：**绝不能** `stop()` 之后再 `start()` 同一个客户端——`_stop` 是 latched
    `asyncio.Event`，重启出来的 task 会立刻退出，结果是「续跑之后行情永久不来」。
    """
    await init_db()
    session, _ = _make_session()
    try:
        built: list[_FakeWS] = []

        def _factory(*_a, **_k):
            ws = _FakeWS(alive=True)
            built.append(ws)
            return ws

        monkeypatch.setattr("app.ai_auto.session.OKXWSClient", _factory)
        # `_rearm_feed` 只取 `_link.on_stream`（装配与 `start()` 逐字同源）。
        session._link = type("L", (), {"on_stream": staticmethod(lambda e: None)})()
        dead = _FakeWS(alive=False)
        session._ws = dead
        await session._rearm_feed()

        assert dead.stopped == 1                    # 旧的被收尸
        assert session._ws is not dead              # **换代**，不是重启
        assert built and built[0].started == 1      # 新的已经起来了
    finally:
        await dispose_engine()


# ---------------- B-5：`resume()` 回执与 status 带上生命周期证据 ----------------
async def test_resume_receipt_carries_the_lifecycle_evidence():
    """L-2：回执语义从「我把它置成 running 了」变成「这是它武装到什么程度的证据」。"""
    await init_db()
    session, _ = _make_session()
    try:
        await session.pause("user")
        session._ws = _FakeWS(alive=True)
        receipt = await session.resume()

        assert receipt["ok"] is True and receipt["state"] == "running"
        wire = receipt["lifecycle"]
        assert wire["state"] == "running"
        assert set(wire) >= {"armed", "reason", "reasonText", "warmedBars", "feedAgeS",
                             "staleAfterS", "linkState", "nextWakeInS", "nextBarInS"}
        # 早退回执必须说**为什么**（D-1 的整条根因就是那份连 reason 都没有的回执被丢弃）。
        again = await session.resume()
        assert again["ok"] is False and again["reason"]
    finally:
        session._cancel_task("_monitor_task")
        await dispose_engine()


async def test_status_payload_carries_lifecycle_and_the_master_switch_removes_it():
    """用例 12 的会话侧一半：总闸关 ⇒ `lifecycle` 键**整键缺席**（而不是下发一个 null）。"""
    await init_db()
    session, _ = _make_session()
    try:
        assert "lifecycle" in session._status_payload()
        session.executor.settings.ai_trade_lifecycle_truth_enabled = False
        assert "lifecycle" not in session._status_payload()
    finally:
        await dispose_engine()


async def test_stopped_session_reports_why_it_is_not_armed():
    """判定顺序的会话侧落点：`stopped` 排第一，且 `reasonText` 说得出原因。"""
    await init_db()
    session, _ = _make_session()
    try:
        session.state = "stopped"
        session.stop_reason = "session_stop_loss"
        wire = session.lifecycle_wire()
        assert wire["armed"] is False and wire["reason"] == "session_stopped"
        assert "触发会话止损" in wire["reasonText"]
    finally:
        await dispose_engine()


async def test_pause_marks_position_unknown_when_exchange_read_fails():
    """诚实不变式 I-4：读持仓失败 ⇒ **不阻断暂停**，但回执 `positionUnknown=True`（读不到 ≠ 没有）。"""
    await init_db()

    def _boom():
        raise RuntimeError("okx down")

    session, _ = _make_session(positions=_boom)
    try:
        result = await session.pause("user")
        assert session.state == "paused"          # 暂停是减风险动作，恒可执行
        assert result["positionUnknown"] is True and result["position"] is None
    finally:
        await dispose_engine()


async def test_pause_snapshots_exchange_identity_keys():
    """[P0-1]：暂停回执的持仓带 `posId` / `openCTime`——它们是续跑时唯一能回答「这还是不是同一笔」的东西。"""
    await init_db()
    rows = [{"instId": "BTC-USDT-SWAP", "pos": 0.6, "posSide": "long", "avgPx": 61234.5,
             "upl": 12.44, "posId": "1899472349827", "cTimeS": 1_723_598_880}]
    session, _ = _make_session(positions=rows)
    try:
        _open_long(session)
        result = await session.pause("user")
        assert result["position"]["posId"] == "1899472349827"
        assert result["position"]["openCTime"] == 1_723_598_880
        assert result["position"]["side"] == "long" and result["position"]["size"] == 0.6
    finally:
        await dispose_engine()


async def test_snapshot_run_metrics_does_not_write_end_ts():
    """§3.1.3 ⑦ / [P2-5]：落 metrics + 置 `paused`，但**不写 end_ts**（run 未终结）。"""
    await init_db()
    from app.db import repositories as repo

    session, _ = _make_session()
    try:
        sm = get_sessionmaker()
        async with sm() as s:
            run = await repo.create_run(s, strategy_id=1, inst_id="BTC-USDT-SWAP", bar="1H",
                                        mode="ai-trade", config={}, status="running")
            run_id = run.id
        session.run_id = run_id
        await session.snapshot_run_metrics(status="paused")

        async with sm() as s:
            from app.db.models import StrategyRun
            row = await s.get(StrategyRun, run_id)
            assert row.status == "paused" and row.end_ts is None
            assert json.loads(row.metrics_json)["roundsCompleted"] == 0
    finally:
        await dispose_engine()


def test_stop_and_pause_stay_structurally_different():
    """防后人「顺手统一」：stop 停 WS / 平仓 / 关兜底 / 落终态四件事，pause 一件都不做。"""
    pause_src = inspect.getsource(AiTradeSession.pause)
    for forbidden in ("_ws.stop", "_backstop.close", "_repair.close", "_finalize_run"):
        assert forbidden not in pause_src
