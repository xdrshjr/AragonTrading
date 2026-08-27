"""行情自愈**动作层**的用例（ai-trade-feed-self-healing §六 #20b）。

会话用最小替身（本文件不测判定表，那是 `test_ai_trade_feed_watchdog.py` 的事；也不测端到端，
那是 `test_ai_trade_feed_selfheal.py` 的事），只测三件事：

  · **五个动作各自吞异常**——它们跑在守着真实资金的 `_monitor_tick` 里，一步抛出就会跳过本 tick
    余下全部步骤，而 `_maybe_interval_wake` 恰好是最后一行（`interval-wake-dead-monitor-loop`
    那一轮的全部教训）。
  · **人工 `/relink` 的硬死线**——前端对本路径落 `FAST_MS = 20_000`，而它最该被点击的场景就是
    「网络仍不通」。
  · **`relink_attempts` 的复位点只有一个**：`feed_candle` 的确认根分支（证据驱动）。
"""
from __future__ import annotations

import asyncio
import time

import pytest

from app.ai_auto import feed_watchdog as fw
from app.ai_auto.feed_runtime import AiTradeFeedSelfHealDisabled, FeedRuntime
from app.config import Settings

_BAR_S = 3600


class _Executor:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.bar_anchor_ts = 0
        self.flattens = 0

    async def flatten(self, *_a, **_kw):
        self.flattens += 1
        return {"ok": True}


class _Window:
    def __init__(self):
        self.last_confirmed_ts = 1_700_000_000


class _Wake:
    def __init__(self):
        self.bar_drops = 0

    def note_bar_drop(self, _now) -> None:
        self.bar_drops += 1


class _Session:
    """`FeedRuntime` 需要的最小会话面（其余一律不碰）。"""

    def __init__(self, settings: Settings | None = None):
        self.executor = _Executor(settings or Settings())
        self.inst_id = "BTC-USDT-SWAP"
        self.bar = "1H"
        self.group_id = "g1"
        self.session_id = "s1"
        self._window = _Window()
        self._wake = _Wake()
        self._ws = None
        self._stale_degraded = False
        self._last_candle_wall = time.time() - 21 * 3600
        self.pushed: list[dict] = []
        self.rearms: list[bool] = []
        self.cleared = 0
        self.catchups = 0

    def _link_state(self) -> str:
        return "unknown"

    def _status_payload(self) -> dict:
        return {"sessionId": self.session_id, "state": "running"}

    async def _push(self, event: dict) -> None:
        self.pushed.append(event)

    async def _rearm_feed(self, *, force: bool = False) -> None:
        self.rearms.append(force)

    async def _clear_stale_if_recovered(self) -> None:
        self.cleared += 1
        self._stale_degraded = False

    def _maybe_catchup_wake(self) -> None:
        self.catchups += 1


def _plan(action: str, stage: str = fw.STAGE_BACKFILL) -> fw.FeedPlan:
    return fw.FeedPlan(action, stage, "文案", None)


# ---------------- 五个动作各自吞异常 ----------------
@pytest.mark.parametrize("action", [fw.ACTION_CLEAR, fw.ACTION_DEGRADE, fw.ACTION_BACKFILL,
                                    fw.ACTION_RELINK, fw.ACTION_ESCALATE])
async def test_every_feed_action_failure_is_swallowed(action, monkeypatch):
    """任何一个动作抛异常都**只降级不外抛**，且如实记进 `lastOutcome`。"""
    session = _Session()

    async def boom(*_a, **_kw):
        raise RuntimeError("网络炸了")

    monkeypatch.setattr("app.ai_auto.feed_runtime.fetch_missing", boom)
    session._rearm_feed = boom
    session._clear_stale_if_recovered = boom
    session._push = boom
    runtime = FeedRuntime(session)

    await runtime.apply(_plan(action))          # 绝不外抛

    assert runtime.last_outcome == "failed"
    assert "自愈动作失败" in (runtime.last_outcome_text or "")


async def test_a_cancelled_action_is_still_cancelled():
    """`CancelledError` 是 `BaseException`，停止流程的取消语义必须逐字保留。"""
    session = _Session()

    async def cancelled(*_a, **_kw):
        raise asyncio.CancelledError()

    session._clear_stale_if_recovered = cancelled
    runtime = FeedRuntime(session)
    with pytest.raises(asyncio.CancelledError):
        await runtime.apply(_plan(fw.ACTION_CLEAR, fw.STAGE_FRESH))


# ---------------- L2：补取的两条分支 ----------------
async def test_a_fresh_backfill_hands_the_wake_over_through_the_existing_guards(monkeypatch):
    """自愈层**绝不**直接起决策轮：三步交接（清降级 → 记待办 → 直调补跑）一件都不能少。

    直调 `_maybe_catchup_wake` 不是旁路——四道守卫连同 `plan_catchup` 的待办上限与 `should_skip`
    全写在那个函数内部，直调它 = 走完全部既有守卫。
    """
    session = _Session()
    session._stale_degraded = True
    ts = int(time.time()) - _BAR_S - 60          # 刚收线 1 分钟

    async def ingest(*_a, **_kw):
        return {"appended": 2, "fresh": True, "lastTs": ts}

    monkeypatch.setattr("app.ai_auto.feed_runtime.fetch_missing",
                        lambda *_a, **_kw: _async([object()]))
    monkeypatch.setattr("app.ai_auto.feed_runtime.ingest_ai_auto_live", ingest)
    runtime = FeedRuntime(session)

    await runtime.apply(_plan(fw.ACTION_BACKFILL))

    assert runtime.last_outcome == "ok" and runtime.last_source == "rest"
    assert session.cleared == 1 and session._wake.bar_drops == 1 and session.catchups == 1
    assert runtime.backfill_attempts == 1


async def test_a_backfill_that_finds_nothing_new_says_so_and_stays_degraded(monkeypatch):
    session = _Session()
    session._stale_degraded = True

    async def ingest(*_a, **_kw):
        return {"appended": 1, "fresh": False, "lastTs": 1}

    monkeypatch.setattr("app.ai_auto.feed_runtime.fetch_missing",
                        lambda *_a, **_kw: _async([object()]))
    monkeypatch.setattr("app.ai_auto.feed_runtime.ingest_ai_auto_live", ingest)
    runtime = FeedRuntime(session)

    await runtime.apply(_plan(fw.ACTION_BACKFILL))

    assert runtime.last_outcome == "no_data"
    assert runtime.last_source is None, "没拿到当下的根 ⇒ 来源不许改口说是 rest"
    assert session.cleared == 0 and session.catchups == 0
    assert session._stale_degraded is True


# ---------------- L4：升级只出声一次，且**绝不平仓** ----------------
async def test_escalation_speaks_once_and_never_flattens():
    session = _Session()
    runtime = FeedRuntime(session)

    for _ in range(5):
        await runtime.apply(_plan(fw.ACTION_ESCALATE, fw.STAGE_ESCALATED))

    said = [e for e in session.pushed if e["type"] == "status"]
    assert len(said) == 1, "不许每 5 秒刷一次屏"
    assert session.executor.flattens == 0, "L-2：自愈层不新增任何自动平仓路径"


# ---------------- 复位点（【R:P1-11】） ----------------
async def test_relink_attempts_reset_only_on_a_ws_confirmed_bar(monkeypatch):
    """补取成功清掉 `_stale_degraded` **不**复位计数；只有 WS 真的又送来一根确认根才复位。

    放在 `_feed_clear` 是错的：补取成功时是 `_backfill` 自己清的标志，此后 `plan()` 只会返回
    `clear` / `none`，`_clear` 再也走不到 ⇒ 计数永不复位 ⇒ 一个早已痊愈的会话会带着陈年计数
    走到 `escalate`。
    """
    session = _Session()
    session._stale_degraded = True
    runtime = FeedRuntime(session)
    await runtime.apply(_plan(fw.ACTION_RELINK, fw.STAGE_RELINK))
    assert runtime.relink_attempts == 1

    ts = int(time.time()) - _BAR_S - 30

    async def ingest(*_a, **_kw):
        return {"appended": 1, "fresh": True, "lastTs": ts}

    monkeypatch.setattr("app.ai_auto.feed_runtime.fetch_missing",
                        lambda *_a, **_kw: _async([object()]))
    monkeypatch.setattr("app.ai_auto.feed_runtime.ingest_ai_auto_live", ingest)
    await runtime.apply(_plan(fw.ACTION_BACKFILL))
    assert runtime.relink_attempts == 1, "补取成功不是「WS 活过来了」的证据"

    runtime.note_ws_bar()
    assert runtime.relink_attempts == 0 and runtime.last_source == "ws"


# ---------------- 人工兜底：硬死线（【R:P1-5】） ----------------
async def test_manual_relink_returns_within_the_deadline(monkeypatch):
    """REST 替身永挂 ⇒ 在死线内返回 `ok: false` 的完整回执，**不抛、不 504**。"""
    session = _Session(Settings(ai_trade_feed_relink_deadline_s=0.2))

    async def never(*_a, **_kw):
        await asyncio.Event().wait()

    monkeypatch.setattr("app.ai_auto.feed_runtime.fetch_missing", never)
    runtime = FeedRuntime(session)

    started = time.monotonic()
    receipt = await runtime.relink_now()

    assert time.monotonic() - started < 2.0
    assert receipt["ok"] is False and receipt["relinked"] is True
    assert receipt["instId"] == "BTC-USDT-SWAP" and receipt["fresh"] is False
    assert "交易所仍未返回更新的 K 线" in receipt["message"]
    assert session.rearms == [True], "人工路径必须**强制**换代，不吃 `is_alive` 短路"
    codes = [e["data"]["code"] for e in session.pushed if e["type"] == "error"]
    assert "AI_TRADE_FEED_RELINK_FAILED" in codes


async def test_manual_relink_ignores_cooldowns_but_still_counts():
    """冷却对人工路径不生效（用户点了就是要现在试），但计数照常累加。"""
    session = _Session(Settings(ai_trade_feed_relink_deadline_s=0.2))
    runtime = FeedRuntime(session)
    runtime.last_relink_wall = time.time()      # 刚换过链

    async def empty(*_a, **_kw):
        return []

    import app.ai_auto.feed_runtime as fr
    original, fr.fetch_missing = fr.fetch_missing, empty
    try:
        await runtime.relink_now()
    finally:
        fr.fetch_missing = original
    assert runtime.relink_attempts == 1 and session.rearms == [True]


async def test_manual_relink_is_refused_when_the_master_switch_is_off():
    session = _Session(Settings(ai_trade_feed_selfheal_enabled=False))
    runtime = FeedRuntime(session)
    with pytest.raises(AiTradeFeedSelfHealDisabled) as exc:
        await runtime.relink_now()
    assert exc.value.code == "AI_TRADE_FEED_SELFHEAL_DISABLED"


# ---------------- 读数：从未 start() 过的会话不该被判「链路已死」 ----------------
async def test_a_session_that_never_started_is_not_reported_as_a_dead_link():
    """**F-10**：`_link is None` ⇒ `_rearm_feed` 是**有文档的零动作**（重建会造出一条没人处理其
    链路事件的野连接）。判它「链路已死」只会产出一个永远不会成功的动作，外加一句「正在重建
    连接」的假话——而那正是本轮要根除的那类不诚实。

    这条一红，`resume()` 之后的行情陈旧看门狗会被换链动作整个挤掉（两条既有用例
    `test_resume_does_not_reset_the_stale_clock` / `..._into_the_future` 是它的下游）。
    """
    session = _Session()
    session._ws = None                      # 从未 start()：`_link` 与 `_ws` 都还没有
    runtime = FeedRuntime(session)

    r = runtime.readings(feed_age_s=1.0, stale_after_s=10_800.0, now_wall=time.time())
    assert r.ws_alive is True and r.link_state == "unknown"

    # 反过来：**已经**装配过链路的会话，死掉的客户端必须如实报出来。
    session._link = object()
    session._ws = type("W", (), {"is_alive": False})()
    assert runtime.readings(feed_age_s=1.0, stale_after_s=10_800.0,
                            now_wall=time.time()).ws_alive is False


# ---------------- wire 的键存在性三档 ----------------
async def test_recovery_key_has_three_distinguishable_tiers():
    """总闸关 ⇒ **整键缺席**；已装配且正常 ⇒ `null`；正在自愈 ⇒ 对象。

    前两档前端表现一致（不渲染），但 wire 里**可被反证**——那正是 `member_lifecycle_wire` 那句
    「前端要能区分『没数据』与『后端没上线这个键』」要求的。
    """
    off = FeedRuntime(_Session(Settings(ai_trade_feed_selfheal_enabled=False)))
    assert off.recovery_key() == {}

    on = FeedRuntime(_Session())
    assert on.recovery_key() == {"feedRecovery": None}

    on.stage = fw.STAGE_RELINK
    on.relink_attempts = 2
    on.next_attempt_in_s = 38.0
    on.last_outcome, on.last_outcome_text = "no_data", "REST 未取到更新的 K 线"
    wire = on.recovery_key()["feedRecovery"]
    assert wire["state"] == "recovering" and wire["stage"] == "relink"
    assert wire["attempts"] == {"backfill": 0, "relink": 2}
    assert wire["nextAttemptInS"] == 38
    assert "已重连 2 次" in wire["recoveryText"] and "**" not in wire["recoveryText"]


async def test_an_escalated_wire_never_counts_down():
    runtime = FeedRuntime(_Session())
    runtime.stage = fw.STAGE_ESCALATED
    runtime.relink_attempts = 3
    wire = runtime.recovery_key()["feedRecovery"]
    assert wire["state"] == "stuck" and wire["nextAttemptInS"] is None
    assert "秒后" not in wire["recoveryText"]


async def _async(value):
    return value
