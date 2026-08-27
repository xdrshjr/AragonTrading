"""行情断连自愈的**会话级**用例（ai-trade-feed-self-healing §7.3 / §7.4）。

本文件的第一条用例是**原 bug 的稳定复现**（CLAUDE.md §七：修 bug 必须先写一个能稳定复现该 bug 的
测试用例）：一条死掉的 K 线通道会把会话锁进一个**没有出口的吸收态**——三条唤醒源全被
`_stale_degraded` 挡住，而唯一能清除它的 `_last_candle_wall` 前移，其唯一写点是那条已经死掉的
WebSocket 自己。

第二条与第三条是本轮**最容易实现成「绿着但没修好」**的一处（【R:P0-2】）：自愈成功之后**必须真的
发生一轮决策**，而不是只把横幅抹掉。它们必须跑两遍——`wake_interval_s > 0` 与 `== 0`——因为墙钟
定时唤醒恰好会盖住「唤醒交接根本没接上」这个缺陷，而 `trend_hold` 档的常见配置正是 0。

行情 / OKX / LLM 全程桩驱动，铁律：占位假凭证。
"""
from __future__ import annotations

import asyncio
import json
import time

from app.agent.llm.base import StreamEvent
from app.ai_auto import feed_watchdog as fw
from app.ai_auto.decider import AiTradeDecider
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
# 换链门槛拉到天上 ⇒ 会话级换链恒不触发，把「REST 活性补取」这条路径单独隔离出来测。
_BACKFILL_ONLY = dict(ai_trade_feed_relink_factor=1000.0)


class _StubStrategy(BaseStrategy):
    meta = StrategyMeta(key="_stub_feed_selfheal", name="Stub", description="t",
                        default_params={}, params_schema={})

    def warmup(self) -> int:
        return 1

    def on_candle(self, df, i, ctx):
        return []


class _StubLLM:
    async def stream(self, *, system, messages, tools):
        if tools:
            args = {"action": "hold", "reason": "r", "confidence": 0.5}
            yield StreamEvent("tool_use_start", id="t", name="submit_trade_decision")
            yield StreamEvent("tool_use_args_delta", id="t",
                              partial_json=json.dumps(args, ensure_ascii=False))
            yield StreamEvent("message_done", stop_reason="tool_use")
        else:
            yield StreamEvent("text_delta", text="摘要")
            yield StreamEvent("message_done", stop_reason="end")


class _FakeWS:
    """`OKXWSClient` 的最小替身：只要 `is_alive` / `start` / `stop` 三个面。"""

    def __init__(self, alive: bool = True):
        self.is_alive = alive
        self.started = 0
        self.stopped = 0

    def start(self) -> None:
        self.started += 1

    async def stop(self) -> None:
        # 真实的 `stop()` 会 cancel 并 await 全部 endpoint task，必然让出一次事件循环；替身若
        # 同步返回，`_rearm_feed` 的并发用例就永远撞不上那个窗口（等于用例在保护一段没被走到的路）。
        await asyncio.sleep(0)
        self.stopped += 1


def _candle(idx: int, price: float = _PX, *, ts: int | None = None) -> Candle:
    stamp = _START_TS + idx * _STEP if ts is None else ts
    return Candle(ts=stamp, o=price, h=price + 5, l=price - 5, c=price, vol=100.0,
                  confirm=True)


async def _no_trade_fee(*_a, **_k):
    return None


async def _no_position_history(*_a, **_k):
    return []


def _make_session(settings: Settings, *, wake_interval_s: float = 120.0):
    """脱网会话：OKX 写 / 读缝均为桩；决策轮替换成计数器（本文件不测决策内容）。"""
    async def place_order_fn(order):
        return {"ok": True, "ordId": "x", "clOrdId": "aragonx"}

    async def close_position_fn(close):
        return {"ok": True, "clOrdId": "aragoncx"}

    async def positions_fn():
        return []

    executor = LiveExecutor(
        creds=AccountCredentials("ak", "sk-000000", "pp-000000"), account_id=1,
        inst_id="BTC-USDT-SWAP", td_mode="cross", pos_mode="long_short_mode",
        leverage=3.0, budget_usd=1000.0, risk=RiskParams(), ct_val=0.01,
        lot_sz=0.01, min_sz=0.01, settings=settings, place_order_fn=place_order_fn,
        close_position_fn=close_position_fn, positions_fn=positions_fn)
    decider = AiTradeDecider(_StubLLM(), "sys", context_refresh_rounds=50, settings=settings)
    spec = PaperStrategySpec(1, "S", "atomic", _StubStrategy({}), {})
    session = AiTradeSession("aitrade_feed", 1, [spec], "BTC-USDT-SWAP", "1H", executor,
                             decider, priming=False, priming_bars=10,
                             context_refresh_rounds=50, wake_interval_s=wake_interval_s)

    async def no_htf(*_a, **_k):
        return []

    session._window._safe_get_candles = no_htf
    session._cost._fetch_fee_rate = _no_trade_fee
    session._cost._fetch_position_history = _no_position_history
    session._ws = _FakeWS(alive=True)
    session.rounds = []

    async def _record_round(board, i, ref_px, ts, wake):
        session.rounds.append({"barIndex": i, "px": ref_px, "wake": wake})
        session._deciding = False

    session._run_decision_round = _record_round
    return session


async def _settle(session) -> None:
    task = session._decision_task
    if task is not None:
        try:
            await task
        except asyncio.CancelledError:
            pass
    await asyncio.sleep(0)


async def _seed_one_bar(session) -> None:
    """喂一根确认根：`_last_board` / `_last_candle_wall` 就位（自愈的全部前置条件）。"""
    await session.feed_candle(_candle(0))
    await _settle(session)
    session.rounds.clear()


class _FakeLink:
    """`SessionLink` 的最小替身：自愈层只读它的 `channel_state`，`_rearm_feed` 只读 `on_stream`。"""

    def __init__(self, state: str = "unknown"):
        self.state = state

    def channel_state(self, _owner: str) -> str:
        return self.state

    async def on_stream(self, _event) -> None:
        return None


def _stub_rest(monkeypatch, candles):
    """替换取数层并计数。**必须打 `feed_runtime` 的名字**：那是它 import 进来的绑定。"""
    calls = {"n": 0}

    async def fetch(*_a, **_kw):
        calls["n"] += 1
        return list(candles() if callable(candles) else candles)

    monkeypatch.setattr("app.ai_auto.feed_runtime.fetch_missing", fetch)
    return calls


def _fresh_bar() -> list[Candle]:
    """一根「刚刚收线」的根（年龄 < 一根 ⇒ 活性补取判它是当下的证据）。"""
    ts = int(time.time()) - _STEP - 30
    return [_candle(0, price=_PX + 10, ts=ts)]


def _stale_bar() -> list[Candle]:
    """一根 20 小时前收线的根（REST 也拿不到当下的数据 ⇒ 一个字段都不许写）。"""
    ts = int(time.time()) - _STEP - 20 * 3600
    return [_candle(0, price=_PX + 10, ts=ts)]


def _go_stale(session, hours: float = 21.0) -> None:
    session._last_candle_wall = time.time() - hours * 3600


async def _ticks(session, n: int, *, pause_s: float = 0.0) -> None:
    """跑 n 个盯市 tick。`pause_s` 让墙钟真的走一小段——冷却判据读的是 `time.time()`，
    紧凑循环里跑 20 个 tick 墙钟几乎不动，冷却会把动作全部挡在门外。"""
    for _ in range(n):
        await session._check_staleness(int(time.time()))
        await _settle(session)
        if pause_s > 0:
            await asyncio.sleep(pause_s)


# ---------------- ① 复现：没有自愈层，一条死掉的 feed 会把会话永久锁死 ----------------
async def test_a_dead_feed_locks_the_session_forever_without_selfheal(monkeypatch):
    """总闸关时**必须仍然复现**原 bug：降级、零决策、`_last_candle_wall` 一动不动。"""
    await init_db()
    settings = Settings(ai_trade_feed_selfheal_enabled=False, ai_trade_funding_enabled=False)
    session = _make_session(settings)
    calls = _stub_rest(monkeypatch, _fresh_bar)
    try:
        await _seed_one_bar(session)
        _go_stale(session)
        frozen = session._last_candle_wall

        await _ticks(session, 8)

        assert session._stale_degraded is True
        assert session.rounds == [], "吸收态：三条唤醒源全部停摆"
        assert session._last_candle_wall == frozen, "唯一的解锁写点是那条已经死掉的 WS"
        assert calls["n"] == 0, "总闸关 ⇒ 一次 REST 都不打"
    finally:
        session._cancel_task("_monitor_task")
        await dispose_engine()


# ---------------- ② / ③ 自愈之后**必须真的发生一轮决策**（5a / 5b，【R:P0-2】） ----------------
async def test_selfheal_actually_runs_a_round_when_the_wall_clock_wake_is_armed(monkeypatch):
    """5a：`wake_interval_s > 0`。这一档即使唤醒交接写错了也可能被墙钟定时唤醒盖过去。"""
    await _assert_selfheal_wakes(monkeypatch, wake_interval_s=120.0)


async def test_selfheal_actually_runs_a_round_when_there_is_no_wall_clock_wake(monkeypatch):
    """5b：`wake_interval_s == 0`（`trend_hold` 档的常见配置）——**本轮真正的护栏**。

    只写 5a 的话，照「让既有唤醒自然接手」写出来的实现会通过 5a，而这一档的现场会是
    「横幅消失了、但一轮决策都没有」——比修复前更糟，且没有任何用例会红。
    """
    await _assert_selfheal_wakes(monkeypatch, wake_interval_s=0.0)


async def _assert_selfheal_wakes(monkeypatch, *, wake_interval_s: float) -> None:
    await init_db()
    settings = Settings(ai_trade_funding_enabled=False, **_BACKFILL_ONLY)
    session = _make_session(settings, wake_interval_s=wake_interval_s)
    _stub_rest(monkeypatch, _fresh_bar)
    try:
        await _seed_one_bar(session)
        _go_stale(session)

        await _ticks(session, 1)
        assert session._stale_degraded is True          # L1：先说话
        await _ticks(session, 1)                        # L2：补取 ⇒ 解锁 ⇒ 补跑

        assert session._stale_degraded is False
        assert time.time() - session._last_candle_wall < 5.0
        assert session.rounds, (
            "自愈之后必须真的发生一轮决策（靠 note_bar_drop + 直调 _maybe_catchup_wake），"
            "而不是只把横幅抹掉")
        assert session._feed.last_source == "rest"
    finally:
        session._cancel_task("_monitor_task")
        await dispose_engine()


# ---------------- ④ L-1 的核心护栏：REST 也拿不到当下的根 ⇒ 一个字段都不写 ----------------
async def test_rest_backfill_with_a_stale_last_bar_writes_nothing(monkeypatch):
    """诚实优先于自愈：假装「我刚拿到数据」比什么都不做更危险。"""
    await init_db()
    settings = Settings(ai_trade_funding_enabled=False, **_BACKFILL_ONLY)
    session = _make_session(settings)
    _stub_rest(monkeypatch, _stale_bar)
    try:
        await _seed_one_bar(session)
        _go_stale(session)
        frozen = (session._last_candle_wall, session._last_px_wall, session._last_live_px)

        await _ticks(session, 4)

        assert session._stale_degraded is True
        assert (session._last_candle_wall, session._last_px_wall,
                session._last_live_px) == frozen
        assert session._feed.last_outcome == "no_data"
        assert session._feed.last_source == "ws", (
            "没拿到当下的根 ⇒ 来源不许改口说是 rest（暖机那一根来自 WS，它才是真源）")
        assert session.rounds == []
    finally:
        session._cancel_task("_monitor_task")
        await dispose_engine()


# ---------------- ⑤ 两个墙钟字段取值不同（【R:P0-3】） ----------------
async def test_rest_backfill_stamps_px_wall_at_bar_close_not_now(monkeypatch):
    """把 `_last_px_wall` 写成 `now` 等于告诉利润保护层「这个价是 0 秒前的」。

    `ts + bar_seconds` 让 `px_age_s` 恰好等于真实价龄：价真的新 ⇒ 保护层照常工作；价其实旧 ⇒
    保护层自己判 `unavailable` 并零动作（fail-open），不需要本层替它做任何决定。
    """
    await init_db()
    settings = Settings(ai_trade_funding_enabled=False, **_BACKFILL_ONLY)
    session = _make_session(settings)
    bar = _fresh_bar()
    _stub_rest(monkeypatch, bar)
    try:
        await _seed_one_bar(session)
        _go_stale(session)
        await _ticks(session, 2)

        assert session._last_candle_wall != session._last_px_wall, "两者语义不同，取值必须不同"
        assert session._last_px_wall == float(bar[0].ts + _STEP)
        assert time.time() - session._last_px_wall > 25.0, "价龄必须是**真实**价龄，不是 0"
    finally:
        session._cancel_task("_monitor_task")
        await dispose_engine()


async def test_rest_backfill_does_not_unlock_a_stale_profit_guard(monkeypatch):
    """承接上一条：补取后立刻取一次保护快照 ⇒ 判定价超龄时仍为 `unavailable`、零平仓。"""
    from app.ai_auto.profit_guard import ProfitGuardRule

    await init_db()
    settings = Settings(ai_trade_funding_enabled=False, **_BACKFILL_ONLY)
    session = _make_session(settings)
    session._guard.rule = ProfitGuardRule(max_px_age_s=90.0)
    # 一根「刚收线 30 分钟」的根：活性补取判它**不新鲜**（> 1 根？否——1H 下 30 分钟 < 一根），
    # 故它会被写进 `_last_px_wall`，但价龄 1800s 远超保护层的 90s 上限。
    ts = int(time.time()) - _STEP - 1800
    _stub_rest(monkeypatch, [_candle(0, price=_PX + 10, ts=ts)])
    try:
        await _seed_one_bar(session)
        _go_stale(session)
        await _ticks(session, 2)

        assert session._last_px_wall == float(ts + _STEP)
        px_age = time.time() - session._last_px_wall
        assert px_age > 90.0, "保护层看到的必须是真实价龄，据此自己判 unavailable"
        assert session.executor.projected == "flat"
    finally:
        session._cancel_task("_monitor_task")
        await dispose_engine()


# ---------------- ⑥ 【R:P0-1】：被 REST 吊着的死链仍然会被换链，且恢复卡从不消失 ----------------
async def test_a_dead_ws_masked_by_rest_still_gets_relinked(monkeypatch):
    """REST 每次都拿得到「刚刚收线的那一根」，而 WS 是死的。

    这条一红就说明判定表第 2 行被挪到了新鲜度判据之后：横幅可以消失（行情确实新鲜），但恢复卡
    **不许**消失——否则用户失去了唯一的知情通道，一个看得见的停摆变成了看不见的停摆。
    """
    await init_db()
    settings = Settings(ai_trade_funding_enabled=False,
                        ai_trade_feed_relink_min_interval_s=0.001)
    session = _make_session(settings)
    _stub_rest(monkeypatch, _fresh_bar)
    monkeypatch.setattr("app.ai_auto.session.OKXWSClient",
                        lambda *_a, **_k: _FakeWS(alive=False))
    session._link = _FakeLink()
    try:
        await _seed_one_bar(session)
        session._ws = _FakeWS(alive=False)
        _go_stale(session)

        keys: list[object] = []
        for _ in range(20):
            await session._check_staleness(int(time.time()))
            await _settle(session)
            keys.append(session.lifecycle_wire().get("feedRecovery", "MISSING"))
            session._last_candle_wall = time.time()      # REST 持续把行情压成「新鲜」
            await asyncio.sleep(0.003)                   # 让墙钟真的走过换链冷却

        assert session._feed.relink_attempts >= 1, "死掉的 WS 必须被换链，哪怕行情看起来是新鲜的"
        assert all(k not in (None, "MISSING") for k in keys), (
            "恢复卡在整个 REST 吊命期间必须保持可见")
        assert session._feed.stage == fw.STAGE_ESCALATED, "换不活就必须升级，不能无限 relink"
    finally:
        session._cancel_task("_monitor_task")
        await dispose_engine()


# ---------------- ⑦ 换链是**换代**而不是重启 ----------------
async def test_relink_replaces_the_client_object_and_stops_the_old_one(monkeypatch):
    """`OKXMarketWSClient._stop` 是 latched `asyncio.Event`：`stop()` 之后再 `start()` 同一个
    对象，重启出来的 task 会立刻退出 —— 「换链之后行情永久不来」比断线本身更静默。"""
    await init_db()
    settings = Settings(ai_trade_funding_enabled=False)
    session = _make_session(settings)
    built: list[_FakeWS] = []

    def _factory(*_a, **_k):
        ws = _FakeWS(alive=True)
        built.append(ws)
        return ws

    monkeypatch.setattr("app.ai_auto.session.OKXWSClient", _factory)
    session._link = _FakeLink()
    try:
        alive = _FakeWS(alive=True)
        session._ws = alive
        await session._rearm_feed()                  # force=False ⇒ 逐字保持既有语义
        assert session._ws is alive and alive.stopped == 0

        await session._rearm_feed(force=True)        # 自愈路径 ⇒ 跳过 is_alive 短路
        assert alive.stopped == 1 and session._ws is not alive
        assert built and built[0].started == 1
    finally:
        await dispose_engine()


async def test_two_concurrent_rearms_never_leak_a_client(monkeypatch):
    """换代必须**串行**：本轮把 `_rearm_feed` 从「只有 `resume()` 一个调用点」变成三个
    （盯市 tick 的 L3 / 人工【立即重连】/ 续跑），而它中途会把 `session._ws` 置空。

    没有串行闸时，后到的那条会看见 `None` ⇒ 跳过收尸 ⇒ 两条新连接同时在线，先到的那条的赋值
    随即被覆盖——留下一条**没人持有、也没人停得掉**的连接一直烧配额，直到进程退出。
    """
    await init_db()
    session = _make_session(Settings(ai_trade_funding_enabled=False))
    built: list[_FakeWS] = []

    def _factory(*_a, **_k):
        ws = _FakeWS(alive=True)
        built.append(ws)
        return ws

    monkeypatch.setattr("app.ai_auto.session.OKXWSClient", _factory)
    session._link = _FakeLink()
    try:
        first = _FakeWS(alive=True)
        session._ws = first
        await asyncio.gather(session._rearm_feed(force=True),
                             session._rearm_feed(force=True))

        assert len(built) == 2 and session._ws is built[1]
        assert first.stopped == 1, "第一条必须被收尸"
        assert built[0].stopped == 1, "被第二次换代顶掉的那条同样必须被收尸（否则就是野连接）"
        assert built[1].stopped == 0 and built[1].started == 1
    finally:
        await dispose_engine()


# ---------------- ⑧ 冷却 ----------------
async def test_backfill_and_relink_respect_their_cooldowns(monkeypatch):
    """冷却内的 tick 一次 REST 都不打（否则 5 标的 × 5 秒 = 每分钟 60 次公共读）。"""
    await init_db()
    settings = Settings(ai_trade_funding_enabled=False, **_BACKFILL_ONLY)
    session = _make_session(settings)
    calls = _stub_rest(monkeypatch, _stale_bar)
    try:
        await _seed_one_bar(session)
        _go_stale(session)
        await _ticks(session, 10)
        assert calls["n"] == 1, f"60 秒冷却内只许打一次，实际 {calls['n']}"
    finally:
        session._cancel_task("_monitor_task")
        await dispose_engine()


# ---------------- ⑨ L-2：自愈层绝不平仓 ----------------
async def test_escalated_never_flattens(monkeypatch):
    """自愈的全部权力边界是「重新拿到数据」，拿不到就如实说不知道。"""
    await init_db()
    settings = Settings(ai_trade_funding_enabled=False,
                        ai_trade_feed_relink_min_interval_s=0.001,
                        ai_trade_feed_backfill_interval_s=0.001)
    session = _make_session(settings)
    _stub_rest(monkeypatch, [])
    session._link = _FakeLink()
    flattens = {"n": 0}

    async def _count_flatten(*_a, **_kw):
        flattens["n"] += 1
        return {"ok": True}

    session.executor.flatten = _count_flatten
    try:
        await _seed_one_bar(session)
        session._ws = _FakeWS(alive=False)
        _go_stale(session)
        await _ticks(session, 12, pause_s=0.003)

        assert session._feed.stage == fw.STAGE_ESCALATED
        assert flattens["n"] == 0
        assert session.state == "running", "自愈绝不停会话"
    finally:
        session._cancel_task("_monitor_task")
        await dispose_engine()


# ---------------- ⑩ 总闸关 ⇒ wire 逐字回退 ----------------
async def test_selfheal_disabled_removes_the_key_entirely(monkeypatch):
    """总闸关 ⇒ `feedRecovery` **整键缺席**（而不是下发一个 null）；开 ⇒ 键在。"""
    await init_db()
    off = _make_session(Settings(ai_trade_feed_selfheal_enabled=False,
                                 ai_trade_funding_enabled=False))
    on = _make_session(Settings(ai_trade_funding_enabled=False))
    try:
        assert "feedRecovery" not in off.lifecycle_wire()
        wire = on.lifecycle_wire()
        assert "feedRecovery" in wire and wire["feedRecovery"] is None
        # 其余键集合逐字相等 —— 总闸关不许顺手改动本轮之外的任何一个键。
        assert set(off.lifecycle_wire()) | {"feedRecovery"} == set(wire)
    finally:
        await dispose_engine()


# ---------------- 轴 C：断流期间的判定价回落（§7.4） ----------------
async def test_mark_px_falls_back_to_ticker_when_the_decision_price_is_stale(monkeypatch):
    """RC-6：断流期间 `reconcile` 与会话止盈止损全程吃一个 21 小时前的价。"""
    await init_db()
    session = _make_session(Settings(ai_trade_funding_enabled=False,
                                     ai_trade_mark_px_fallback_after_s=180.0))
    hits = {"n": 0}

    class _Rest:
        async def get_ticker(self, _inst):
            hits["n"] += 1
            return 31_000.0

    monkeypatch.setattr("app.ai_auto.session.get_rest_client", lambda: _Rest())
    try:
        await _seed_one_bar(session)
        assert await session._latest_mark_px() == _PX and hits["n"] == 0   # 新鲜 ⇒ 零 REST

        session._last_px_wall = time.time() - 600.0
        assert await session._latest_mark_px() == 31_000.0 and hits["n"] == 1
        # **纯读**：三个墙钟字段一个都不许被这条回落改写（否则会在真实价与化石价之间来回摆）。
        assert session._last_px_wall < time.time() - 500.0
    finally:
        await dispose_engine()


async def test_mark_px_fallback_uses_last_px_wall_not_last_candle_wall(monkeypatch):
    """本仓库第三次踩这个坑的护栏：`_last_candle_wall` 在 12H/1D 下几十小时不跳。"""
    await init_db()
    session = _make_session(Settings(ai_trade_funding_enabled=False,
                                     ai_trade_mark_px_fallback_after_s=180.0))
    hits = {"n": 0}

    class _Rest:
        async def get_ticker(self, _inst):
            hits["n"] += 1
            return 31_000.0

    monkeypatch.setattr("app.ai_auto.session.get_rest_client", lambda: _Rest())
    try:
        await _seed_one_bar(session)
        session._last_candle_wall = time.time() - 6 * 3600     # 6 小时没有新根
        session._last_px_wall = time.time() - 5.0              # 但实时价是 5 秒前的
        assert await session._latest_mark_px() == _PX
        assert hits["n"] == 0, "判定价新鲜就绝不该每 5 秒打一次 REST"
    finally:
        await dispose_engine()


async def test_mark_px_fallback_after_zero_is_byte_identical(monkeypatch):
    """「关得掉的取值」：`= 0` ⇒ 逐字回退旧行为（窗口非空即用最后一根收盘价）。"""
    await init_db()
    session = _make_session(Settings(ai_trade_funding_enabled=False,
                                     ai_trade_mark_px_fallback_after_s=0.0))
    hits = {"n": 0}

    class _Rest:
        async def get_ticker(self, _inst):
            hits["n"] += 1
            return 31_000.0

    monkeypatch.setattr("app.ai_auto.session.get_rest_client", lambda: _Rest())
    try:
        await _seed_one_bar(session)
        session._last_px_wall = time.time() - 99_999.0
        assert await session._latest_mark_px() == _PX and hits["n"] == 0
    finally:
        await dispose_engine()
