"""会话侧链路事件与补根单测（okx-link-resilience §2.2.4 / §2.2.5）——本轮最重的安全断言。

**只补证据，不补决策。** 补进来的根只进窗口、只刷指标、只推 `candle` 事件；绝不驱动信号、下单、
LLM 决策或会话止盈止损。理由很直白：`live_auto` 的确认根**直接下真单**，`ai_auto` 的确认根就是
一轮付费 LLM 决策——按十分钟前的价下单是纯损失，补 10 根 = 10 次基于过期价的决策。

以及 [评审 P0-4] 的四字段禁写：`_last_candle_wall` / `_last_px_wall` / `_live_row` /
`_last_live_px` 的语义是「**实时**证据的年龄」，而补根拿到的是**历史**证据。其中 `_last_px_wall`
是利润保护层新鲜度豁免的唯一真源——照抄 `feed_candle` 的确认根分支，一次 10 分钟断线之后保护层
会认为判定价是「0 秒前的」，据此执行棘轮与兑现。
"""
from __future__ import annotations

import ast
import asyncio
import inspect
import textwrap
import time
from pathlib import Path

import pytest

from app.config import Settings
from app.okx import session_link as sl
from app.okx.link_state import get_link_registry
from app.okx.types import Candle
from app.okx.ws_supervisor import StreamEvent

_KEY = "ws:business:BTC-USDT-SWAP:1H:live:s1"


def _candle(ts: int, c: float = 100.0) -> Candle:
    return Candle(ts=ts, o=c, h=c, l=c, c=c, vol=1.0, confirm=True)


def _event(state: str, *, attempt: int = 1, downtime: float = 0.0) -> StreamEvent:
    return StreamEvent(key=_KEY, state=state, attempt=attempt, downtime_s=downtime,
                       detail="test")


class _Executor:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.applied = 0

    async def apply(self, *_a, **_kw):
        self.applied += 1
        return []

    async def reconcile(self, *_a, **_kw):
        self.applied += 1
        return []


class _FakeSession:
    """三处 session 的最小共同面（`_push` / `_status_payload` / `state` / `inst_id` / `bar`）。"""

    def __init__(self, settings: Settings | None = None):
        self.state = "running"
        self.inst_id = "BTC-USDT-SWAP"
        self.bar = "1H"
        self.executor = _Executor(settings or Settings(okx_link_down_alert_s=0.0))
        self.pushed: list[dict] = []

    async def _push(self, event: dict) -> None:
        self.pushed.append(event)

    def _status_payload(self) -> dict:
        return {"sessionId": "s1", "state": self.state}

    def messages(self) -> list[str]:
        return [e["data"]["message"] for e in self.pushed
                if e["type"] == "status" and e["data"].get("message")]


# ---------------- 降级 / 恢复消息（变化才推） ----------------


async def test_a_link_outage_pushes_exactly_one_degraded_message():
    """迟滞：状态变化才推，否则一次重连风暴会把状态行刷屏。"""
    session = _FakeSession(Settings(okx_link_down_alert_s=0.0))
    link = sl.SessionLink(session, sl.FLAVORS["live_auto"])

    for attempt in range(5):
        await link.on_stream(_event("degraded", attempt=attempt))

    assert len(session.messages()) == 1
    assert "实时链路已断开" in session.messages()[0]
    assert "交易所侧止盈止损仍生效" in session.messages()[0]


async def test_no_message_before_the_alert_threshold():
    """一次 2 秒的抖动不该惊动用户（默认阈值 20s）。"""
    session = _FakeSession(Settings(okx_link_down_alert_s=999.0))
    link = sl.SessionLink(session, sl.FLAVORS["live_auto"])

    await link.on_stream(_event("connecting", attempt=0))
    await link.on_stream(_event("degraded", attempt=1))

    assert session.messages() == []


async def test_recovery_pushes_a_message_and_rearms_the_alert():
    session = _FakeSession(Settings(okx_link_down_alert_s=0.0, okx_link_gap_repair_max_bars=0))
    link = sl.SessionLink(session, sl.FLAVORS["live_auto"])
    session._last_confirmed_ts = None

    await link.on_stream(_event("degraded"))
    await link.on_stream(_event("live", downtime=1.0))
    await asyncio.sleep(0)

    assert any("已恢复" in m for m in session.messages())

    await link.on_stream(_event("degraded"))
    assert len([m for m in session.messages() if "已断开" in m]) == 2, "恢复后须重新武装告警"


async def test_a_stopped_session_is_never_touched():
    session = _FakeSession()
    session.state = "stopped"
    link = sl.SessionLink(session, sl.FLAVORS["live_auto"])

    await link.on_stream(_event("degraded"))

    assert session.pushed == []


async def test_the_handler_never_raises_into_the_supervisor():
    """一个观测回调不该打死刚刚才恢复的连接。"""
    class _Boom(_FakeSession):
        async def _push(self, event: dict) -> None:
            raise RuntimeError("推送炸了")

    link = sl.SessionLink(_Boom(Settings(okx_link_down_alert_s=0.0)),
                          sl.FLAVORS["live_auto"])
    await link.on_stream(_event("degraded"))     # 不外抛即通过


# ---------------- live_auto 补根：不下单 ----------------


class _LiveSession(_FakeSession):
    def __init__(self):
        super().__init__(Settings(okx_link_down_alert_s=0.0))
        self._candles: list[dict] = []
        self._last_confirmed_ts: int | None = 1_700_000_000
        self._last_candle_wall = 12345.0
        self._bar_index = 7
        self.htf_refreshes: list[int] = []

    def _is_duplicate(self, candle: Candle) -> bool:
        return (self._last_confirmed_ts is not None and candle.ts <= self._last_confirmed_ts
                and bool(self._candles) and self._candles[-1]["ts"] == candle.ts)

    def _append_candle(self, candle: Candle) -> None:
        self._candles.append({"ts": candle.ts, "o": candle.o, "h": candle.h,
                              "l": candle.l, "c": candle.c, "vol": candle.vol})
        self._last_confirmed_ts = candle.ts
        import time as _t
        self._last_candle_wall = _t.time()      # 既有实现顺手写它——补根必须还原

    async def _maybe_refresh_htf(self, ts: int) -> None:
        self.htf_refreshes.append(ts)


async def test_live_auto_backfill_never_places_an_order():
    """R-5：补根把过期行情喂进下单链路 = 按十分钟前的价下真单。"""
    session = _LiveSession()
    candles = [_candle(1_700_003_600 + i * 3600) for i in range(3)]

    appended = await sl.ingest_live_auto(session, candles)

    assert appended == 3
    assert session.executor.applied == 0, "补根**绝不**调 executor.apply / reconcile"
    assert session._bar_index == 10
    assert [e["type"] for e in session.pushed] == ["candle"] * 3
    assert all(e["data"]["confirm"] is True for e in session.pushed)


async def test_live_auto_backfill_leaves_the_staleness_watchdog_baseline_alone():
    """`_last_candle_wall` 是行情停滞看门狗的基准：用历史根喂它 = 告诉它「刚收到实时数据」。"""
    session = _LiveSession()
    before = session._last_candle_wall

    await sl.ingest_live_auto(session, [_candle(1_700_003_600)])

    assert session._last_candle_wall == before


async def test_live_auto_refreshes_htf_once_after_the_whole_backfill():
    """逐根刷会在补 300 根时打出 300 次 REST（[评审 P2-3]）。"""
    session = _LiveSession()
    candles = [_candle(1_700_003_600 + i * 3600) for i in range(5)]

    await sl.ingest_live_auto(session, candles)

    assert session.htf_refreshes == [candles[-1].ts]


async def test_live_auto_backfill_is_idempotent():
    session = _LiveSession()
    candles = [_candle(1_700_003_600)]
    assert await sl.ingest_live_auto(session, candles) == 1
    assert await sl.ingest_live_auto(session, candles) == 0, "补根天然幂等（既有去重判据）"


# ---------------- ai_auto 补根：四字段禁写 + 不触发决策 ----------------


class _Window:
    def __init__(self):
        self.candles = [{"ts": 1_700_000_000, "c": 100.0}]
        self.last_confirmed_ts = 1_700_000_000
        self.htf_refreshes = 0

    def is_duplicate(self, candle: Candle) -> bool:
        return candle.ts <= self.last_confirmed_ts

    def append_confirmed(self, candle: Candle) -> dict:
        row = {"ts": candle.ts, "o": candle.o, "h": candle.h, "l": candle.l,
               "c": candle.c, "vol": candle.vol}
        self.candles.append(row)
        self.last_confirmed_ts = candle.ts
        return row

    def base_df(self):
        import pandas as pd
        return pd.DataFrame(self.candles)


class _Wake:
    def __init__(self):
        self.atr_calls: list = []
        self.new_bars = 0

    def note_atr(self, value) -> None:
        self.atr_calls.append(value)

    def note_new_bar(self) -> None:
        self.new_bars += 1


class _Sizing:
    def __init__(self):
        self.calls = 0

    def note_candles(self, _closes, _ts) -> None:
        self.calls += 1


class _AiSession(_FakeSession):
    def __init__(self):
        super().__init__(Settings(okx_link_down_alert_s=0.0))
        self._window = _Window()
        self._wake = _Wake()
        self._sizing = _Sizing()
        self._atr_px = 1.5
        # 【禁写清单】四个「实时证据年龄」字段
        self._last_candle_wall = 111.0
        self._last_px_wall = 222.0
        self._live_row = {"ts": 9, "c": 99.0}
        self._last_live_px = 99.0
        self.decision_rounds = 0

    async def _run_decision_round(self, *_a, **_kw):
        self.decision_rounds += 1


async def test_ai_auto_backfill_leaves_all_four_realtime_age_fields_untouched():
    """[评审 P0-4] 逐个断言补根前后取值**相等**。"""
    session = _AiSession()
    before = (session._last_candle_wall, session._last_px_wall,
              session._live_row, session._last_live_px)

    await sl.ingest_ai_auto(session, [_candle(1_700_003_600 + i * 3600) for i in range(3)])

    assert (session._last_candle_wall, session._last_px_wall,
            session._live_row, session._last_live_px) == before


async def test_ai_auto_backfill_never_triggers_a_decision_round():
    """一根一次 LLM 决策：补 10 根 = 10 次付费决策，且全部基于过期价。"""
    session = _AiSession()

    await sl.ingest_ai_auto(session, [_candle(1_700_003_600 + i * 3600) for i in range(10)])

    assert session.decision_rounds == 0
    assert session._wake.new_bars == 0, "补根不解锁同方向翻转的防抖锁存"


async def test_ai_auto_keeps_atr_and_note_atr_in_lockstep():
    """[评审 P1-9] 「ATR 判据与参照断面共用这一个数（唯一入口）」——只刷一半就是把入口劈成两份。"""
    session = _AiSession()

    await sl.ingest_ai_auto(session, [_candle(1_700_003_600 + i * 3600, c=100.0 + i)
                                      for i in range(4)])

    assert session._wake.atr_calls, "补完必须同步唤醒器的 ATR"
    assert session._wake.atr_calls[-1] is session._atr_px
    assert session._sizing.calls == 1, "仓位尺度也只在补完之后刷一次"


async def test_ai_auto_backfill_with_nothing_new_does_nothing():
    session = _AiSession()
    assert await sl.ingest_ai_auto(session, [_candle(1_699_000_000)]) == 0
    assert session._wake.atr_calls == []


# ---------------- paper 补根：走既有回放 ----------------


class _PaperSession(_FakeSession):
    def __init__(self):
        super().__init__(Settings(okx_link_down_alert_s=0.0))
        self._last_confirmed_ts = 1_700_000_000
        self.processed: list[tuple[int, bool, bool]] = []

    async def _process_confirmed(self, candle: Candle, *, seeded: bool,
                                 emit_candle: bool) -> None:
        self.processed.append((candle.ts, seeded, emit_candle))


async def test_paper_backfill_reuses_the_existing_replay_path():
    """模拟撮合无真金白银，补齐反而使净值曲线连续、真实。"""
    session = _PaperSession()

    appended = await sl.ingest_paper(session, [_candle(1_700_003_600 + i * 3600)
                                               for i in range(3)])

    assert appended == 3
    assert all(seeded is False and emit is True for _, seeded, emit in session.processed)


# ---------------- 补根的失败路径（[评审 P1-7]） ----------------


async def test_a_failing_backfill_never_leaves_a_silent_hole(monkeypatch):
    """空洞留着会让后续指标窗口不连续，而那是最难查的一类错——**禁止静默留洞**。"""
    async def boom(*_a, **_kw):
        raise RuntimeError("取数失败")

    monkeypatch.setattr("app.okx.session_link.fetch_missing", boom)
    session = _LiveSession()
    link = sl.SessionLink(session, sl.FLAVORS["live_auto"])

    await link.on_stream(_event("degraded"))
    await link.on_stream(_event("live", downtime=99999.0))
    await asyncio.sleep(0.05)

    assert any("未能补齐" in m for m in session.messages())
    assert session.state == "running", "session 不崩"
    row = get_link_registry().snapshot()["channels"][0]
    assert row["lastErrorCode"] == "GAP_REPAIR_FAILED"


async def test_a_failed_backfill_is_retried_on_the_next_recovery(monkeypatch):
    """保留同一个 `since_ts`，下一次恢复事件**再试一次**（不新增第二条调度路径）。"""
    calls = {"n": 0}

    async def flaky(*_a, **_kw):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("第一次失败")
        return [_candle(1_700_003_600)]

    monkeypatch.setattr("app.okx.session_link.fetch_missing", flaky)
    session = _LiveSession()
    link = sl.SessionLink(session, sl.FLAVORS["live_auto"])

    await link.on_stream(_event("degraded"))
    await link.on_stream(_event("live", downtime=99999.0))
    await asyncio.sleep(0.05)
    await link.on_stream(_event("live", downtime=0.0))     # 断得很短，但欠账还在
    await asyncio.sleep(0.05)

    assert calls["n"] == 2
    assert any("已补齐 1 根" in m for m in session.messages())


async def test_a_truncated_backfill_says_so_instead_of_silently_dropping_bars(monkeypatch):
    """**禁止静默截断**：一句「已补齐 2 根」底下藏着一个更大的洞，就是在说谎。

    洞比 `okx_link_gap_repair_max_bars` 大时，最早的那几根**补不回来**；这件事必须同时落在
    登记簿（`gap_truncated`）与用户看得到的那一行里，否则后续指标窗口不连续而没人知道为什么。
    """
    async def two_bars(*_a, **_kw):
        return [_candle(1_700_003_600), _candle(1_700_007_200)]

    monkeypatch.setattr("app.okx.session_link.fetch_missing", two_bars)
    session = _LiveSession()
    session.executor.settings = Settings(okx_link_down_alert_s=0.0,
                                         okx_link_gap_repair_max_bars=2)
    session._last_confirmed_ts = int(time.time()) - 10 * 3600     # 洞 ≈ 9 根，上限 2 根
    link = sl.SessionLink(session, sl.FLAVORS["live_auto"])

    await link.on_stream(_event("degraded"))
    await link.on_stream(_event("live", downtime=99999.0))
    await asyncio.sleep(0.05)

    message = [m for m in session.messages() if "已补齐" in m][-1]
    assert "缺口超过上限 2 根" in message and "未补齐" in message
    assert get_link_registry().snapshot()["channels"][0]["lastErrorCode"] == "GAP_TRUNCATED"


async def test_the_backfill_runs_inside_the_session_lock():
    """补根跑在**独立 task** 上，而 WS 收发泵同时还在送实时帧。

    不持同一把锁，一根实时确认根就能插进补根序列中间，让后面的历史根被去重判据整段丢掉；
    paper 那条更糟——两次 `_process_confirmed` 交错改的是同一条净值曲线。
    """
    session = _PaperSession()
    session._lock = asyncio.Lock()
    held: list[bool] = []

    async def record(_candle_row, *, seeded, emit_candle):
        held.append(session._lock.locked())

    session._process_confirmed = record
    link = sl.SessionLink(session, sl.FLAVORS["paper"])

    assert await link._ingest([_candle(1_700_003_600)]) == 1
    assert held == [True], "补根必须与既有确认根管线互斥"


async def test_a_short_blip_does_not_trigger_a_backfill(monkeypatch):
    """中断不足一根 bar 就没有洞可补——不为一次 2 秒的抖动打一发 REST。"""
    calls = {"n": 0}

    async def counting(*_a, **_kw):
        calls["n"] += 1
        return []

    monkeypatch.setattr("app.okx.session_link.fetch_missing", counting)
    session = _LiveSession()
    link = sl.SessionLink(session, sl.FLAVORS["live_auto"])

    await link.on_stream(_event("degraded"))
    await link.on_stream(_event("live", downtime=2.0))
    await asyncio.sleep(0.05)

    assert calls["n"] == 0
    assert any(m == "实时链路已恢复" for m in session.messages())


# ---------------- [评审 P1-8] 三处 session 只许留绑定 ----------------


_SESSION_FILES = {
    "live_auto": Path(__file__).resolve().parents[1] / "app" / "live_auto" / "session.py",
    "ai_auto": Path(__file__).resolve().parents[1] / "app" / "ai_auto" / "session.py",
    "paper": Path(__file__).resolve().parents[1] / "app" / "paper" / "session.py",
}


@pytest.mark.parametrize("flavor", sorted(_SESSION_FILES))
def test_sessions_only_bind_and_never_reimplement_the_link_logic(flavor):
    """链路处理**强制**下沉到 `okx/session_link.py`：`ai_auto/session.py` 已 1790 行。

    判据用 AST：三处 session 里不许出现任何链路实体（补根 / 取数 / 登记簿写入），
    只许出现一次 `make_session_link(...)` 绑定。
    """
    source = _SESSION_FILES[flavor].read_text(encoding="utf-8")
    tree = ast.parse(source)

    binds = [n for n in ast.walk(tree)
             if isinstance(n, ast.Call) and getattr(n.func, "id", "") == "make_session_link"]
    assert len(binds) == 1, "有且只有一次绑定"

    forbidden = ("fetch_missing", "get_link_registry", "ingest_live_auto",
                 "ingest_ai_auto", "ingest_paper")
    for name in forbidden:
        assert name not in source, f"{flavor}/session.py 不许自己实现链路实体：{name}"


def test_the_link_binding_costs_each_session_at_most_ten_lines():
    """spec §3.2 的硬预算：三处 session 各留 ≤10 行。"""
    for flavor, path in _SESSION_FILES.items():
        lines = [ln for ln in path.read_text(encoding="utf-8").splitlines()
                 if "session_link" in ln or "make_session_link" in ln or "on_stream" in ln
                 or "self._link" in ln]
        assert len(lines) <= 10, f"{flavor} 的链路绑定占了 {len(lines)} 行"


def test_ingest_functions_stay_within_the_size_guidelines():
    """CLAUDE.md：单方法 ≤50 行。补根口径是本轮最该被读懂的一段代码。"""
    for fn in (sl.ingest_live_auto, sl.ingest_ai_auto, sl.ingest_paper):
        body = textwrap.dedent(inspect.getsource(fn))
        assert len(body.splitlines()) <= 50, f"{fn.__name__} 超过 50 行"
