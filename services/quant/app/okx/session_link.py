"""三处 session 共用的链路事件处理与断线补根装配（okx-link-resilience §2.2.4 / §2.2.5）。

**为什么实体在这里而不在 session 里**：`ai_auto/session.py` 已 1790 行、`live_auto/session.py` 490、
`paper/session.py` 457，前者是 CLAUDE.md 800 行红线的两倍多。三处要的又是同一件事——「链路断了
告诉用户、链路回来了把缺的 K 线补上」——所以整段逻辑下沉到本文件，三处 session 各自只留一行绑定。

**本轮最重要的安全决定：只补证据，不补决策。**
补进来的根只进窗口、只刷指标、只推 `candle` 事件；**绝不**驱动信号、下单、LLM 决策或会话止盈止损。
理由很直白：`live_auto` 的确认根直接下真单，`ai_auto` 的确认根就是一轮付费 LLM 决策——按十分钟前的
价下单是纯损失，补 10 根 = 10 次基于过期价的决策。

**ai_auto 的四个字段禁写**（`_last_candle_wall` / `_last_px_wall` / `_live_row` / `_last_live_px`）：
它们的语义是「**实时**证据的年龄」，而补根拿到的是**历史**证据。其中 `_last_px_wall` 是利润保护层
新鲜度豁免的唯一真源——补根若照抄 `feed_candle` 的确认根分支，一次 10 分钟断线之后保护层会认为
判定价是「0 秒前的」，据此执行棘轮与兑现。同一形状的坑上一轮已在 `ai-trade-profit-protection`
的 D-1 付过一次代价。`live_auto` 的 `_last_candle_wall` 同理（它是行情停滞看门狗的基准）。
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from ..config import get_settings
from .gap_repair import fetch_missing, missing_bar_count
from .link_state import get_link_registry
from .types import BAR_SECONDS, Candle
from .ws_supervisor import StreamEvent

logger = logging.getLogger("aragon.okx.session_link")

IngestFn = Callable[[object, list[Candle]], Awaitable[int]]


# ---------- 三处 session 各自的补根口径（实体，session 侧只留绑定） ----------

async def ingest_live_auto(session, candles: list[Candle]) -> int:
    """live_auto：只进窗口 + 推 candle；**不**驱动信号 / 不 reconcile / 不动会话止盈止损。

    `_last_candle_wall` 由 `_append_candle` 顺手写，这里**快照后还原**：它是行情停滞看门狗的基准，
    用历史根去喂它等于告诉看门狗「刚刚才收到实时数据」。还原而不是重写一份 append 逻辑，是为了
    让行的形状始终只有 `_append_candle` 一个真源。
    """
    wall = session._last_candle_wall
    appended = 0
    for candle in candles:
        if session._is_duplicate(candle):
            continue
        session._append_candle(candle)
        session._bar_index += 1
        appended += 1
        await session._push({"type": "candle", "data": {**session._candles[-1], "confirm": True}})
    session._last_candle_wall = wall
    if appended:
        # 高周期窗口**补完之后统一刷一次**：逐根刷会在补 300 根时打出 300 次 REST。
        await session._maybe_refresh_htf(int(candles[-1].ts))
    return appended


async def ingest_ai_auto(session, candles: list[Candle]) -> int:
    """ai_auto：只进窗口 + 刷指标 + 推 candle；**不**触发决策轮、**不**调 `note_new_bar`。

    禁写四字段见模块 docstring。`_atr_px` 与 `_wake.note_atr(...)` 必须**同进同出**：
    `session.py:658-660` 的注释写明「ATR 判据与参照断面共用这一个数（唯一入口）」，
    只刷前者会把这个唯一入口劈成两份，唤醒器据旧 ATR 判定。
    """
    from ..ai_auto.profit_guard import safe_atr

    appended = 0
    for candle in candles:
        if session._window.is_duplicate(candle):
            continue
        row = session._window.append_confirmed(candle)
        appended += 1
        await session._push({"type": "candle", "data": {**row, "confirm": True}})
    if not appended:
        return 0
    session._atr_px = safe_atr(session._window.base_df(),
                               session.executor.settings.ai_trade_profit_guard_atr_period)
    session._wake.note_atr(session._atr_px)
    session._sizing.note_candles([c["c"] for c in session._window.candles],
                                 int(candles[-1].ts))
    return appended


async def ingest_paper(session, candles: list[Candle]) -> int:
    """paper：走既有回放路径（模拟撮合，无真金白银，补齐反而使净值曲线连续、真实）。"""
    appended = 0
    for candle in candles:
        await session._process_confirmed(candle, seeded=False, emit_candle=True)
        appended += 1
    return appended


@dataclass(frozen=True)
class LinkFlavor:
    """一种 session 的链路口径：怎么补根、从哪读「已见过的最后一根」、消息里怎么称呼自己。"""

    name: str
    subject: str
    ingest: IngestFn
    last_ts: Callable[[object], int | None]


FLAVORS: dict[str, LinkFlavor] = {
    "live_auto": LinkFlavor("live_auto", "自动交易", ingest_live_auto,
                            lambda s: s._last_confirmed_ts),
    "ai_auto": LinkFlavor("ai_auto", "AI 自动交易", ingest_ai_auto,
                          lambda s: s._window.last_confirmed_ts),
    "paper": LinkFlavor("paper", "模拟交易", ingest_paper,
                        lambda s: s._last_confirmed_ts),
}


class SessionLink:
    """把 supervisor 的链路状态事件翻译成「用户看得懂的一句话」+「一次补根」。

    迟滞：**状态变化才推**（沿用既有唤醒计数「变化才推」的范式），避免重连风暴刷屏。
    与既有 `_check_staleness`（阈值 = bar × 3）并联而非替换：那是「连接活着但没有新根」，
    这是「我们根本收不到数据」——1H 周期下前者要 3 小时才开口，后者 20 秒。
    """

    def __init__(self, session, flavor: LinkFlavor) -> None:
        self._session = session
        self._flavor = flavor
        self._down_since: float | None = None
        self._alerted = False
        self._pending_repair = False
        self._repair_task: asyncio.Task | None = None

    async def on_stream(self, event: StreamEvent) -> None:
        """WS 链路状态回调。**绝不外抛**：一个观测回调不该打死刚刚才恢复的连接。"""
        try:
            if getattr(self._session, "state", "running") != "running":
                return
            if event.state == "live":
                await self._on_recovered(event)
                return
            await self._on_down(event)
        except Exception as exc:  # noqa: BLE001 —— 观测层失败只记日志
            logger.warning("session link handler failed (%s): %s", event.key, exc)

    # ---------- 断开 / 恢复 ----------
    async def _on_down(self, event: StreamEvent) -> None:
        now = time.time()
        if self._down_since is None:
            self._down_since = now
        elapsed = now - self._down_since
        threshold = float(self._settings().okx_link_down_alert_s)
        if self._alerted or elapsed < threshold:
            return
        self._alerted = True
        await self._push_message(
            f"实时链路已断开 {int(elapsed)} 秒，正在重连（第 {max(1, event.attempt)} 次尝试）；"
            "交易所侧止盈止损仍生效")

    async def _on_recovered(self, event: StreamEvent) -> None:
        was_down, self._alerted, self._down_since = self._alerted, False, None
        bar_seconds = BAR_SECONDS.get(getattr(self._session, "bar", ""), 60)
        needs_repair = self._pending_repair or event.downtime_s >= bar_seconds
        if not (was_down or needs_repair):
            return
        # 补根在 session 侧的**独立 task** 里跑：一次取数失败绝不许回传给 supervisor。
        self._pending_repair = needs_repair
        if needs_repair and (self._repair_task is None or self._repair_task.done()):
            self._repair_task = asyncio.create_task(self._repair(event.key))
        elif was_down:
            await self._push_message("实时链路已恢复")

    # ---------- 补根 ----------
    async def _repair(self, key: str) -> None:
        """拉缺根 → 交给本 flavor 的 ingest → 如实说出补了几根 / 没补上 / 被上限截掉几根。"""
        since_ts = self._flavor.last_ts(self._session)
        max_bars = int(self._settings().okx_link_gap_repair_max_bars)
        dropped = self._dropped_bars(since_ts, max_bars)
        try:
            candles = await fetch_missing(self._session.inst_id, self._session.bar,
                                          since_ts=since_ts, max_bars=max_bars)
            appended = await self._ingest(candles) if candles else 0
        except Exception as exc:  # noqa: BLE001 —— 补根失败**禁止静默留洞**
            self._pending_repair = True   # 保留同一个 since_ts，下一次恢复事件再试一次
            _note_ws(key, "gap_repair_failed", f"since_ts={since_ts}: {exc}")
            # 文案里不写 markdown 星号：这条串直接进前端状态行，`**粗体**` 会显示成字面星号。
            await self._push_message("实时链路已恢复，但缺失的 K 线未能补齐，"
                                     "指标窗口可能不连续（下次重连会再试一次）")
            return
        self._pending_repair = False
        if appended:
            _note_ws(key, "gap_repaired", "")
        if dropped:
            _note_ws(key, "gap_truncated", f"dropped={dropped} cap={max_bars}")
        await self._push_message(_repaired_text(appended, dropped, max_bars))

    async def _ingest(self, candles: list[Candle]) -> int:
        """在会话自己的锁内补根——`paper` / `live_auto` 的确认根管线本就跑在 `self._lock` 里。

        补根跑在独立 task 上，而 WS 收发泵**同时**还在送实时帧；不持同一把锁，一根实时确认根就
        可能插进补根序列中间，让后面的历史根被去重判据整段丢掉（更糟的是 paper 那条会有两次
        `_process_confirmed` 交错改同一条净值曲线）。`ai_auto` 没有这把锁，按既有口径直调。
        """
        lock = getattr(self._session, "_lock", None)
        if lock is None:
            return await self._flavor.ingest(self._session, candles)
        async with lock:
            return await self._flavor.ingest(self._session, candles)

    def _dropped_bars(self, since_ts: int | None, max_bars: int) -> int:
        """空洞比上限大出几根（**禁止静默截断**：补不全这件事必须有展示落点）。"""
        bar_seconds = BAR_SECONDS.get(getattr(self._session, "bar", ""), 60)
        missing = missing_bar_count(since_ts, bar_seconds, time.time())
        return max(0, missing - max_bars)

    # ---------- 辅助 ----------
    def _settings(self):
        """会话级 settings；paper 会话没有 executor，回落进程级设置。"""
        executor = getattr(self._session, "executor", None)
        return executor.settings if executor is not None else get_settings()

    async def _push_message(self, message: str) -> None:
        await self._session._push({"type": "status", "data": {
            **self._session._status_payload(), "message": message}})


def _repaired_text(appended: int, dropped: int, max_bars: int) -> str:
    """恢复后的那一句。截断如实说出来——「已补齐 300 根」底下藏着一个更大的洞是在说谎。"""
    head = f"实时链路已恢复，已补齐 {appended} 根缺失 K 线" if appended else "实时链路已恢复"
    if not dropped:
        return head
    return f"{head}；缺口超过上限 {max_bars} 根，最早的 {dropped} 根未补齐"


def _note_ws(key: str, event: str, detail: str) -> None:
    """把补根结果记进登记簿；``label=""`` 保留通道既有中文（值域属于建连方）。"""
    kind = "wsBusiness" if ":business:" in key else "wsPublic"
    get_link_registry().note_ws(key, kind=kind, label="", event=event,  # type: ignore[arg-type]
                                detail=detail)


def make_session_link(session, flavor: str) -> SessionLink:
    """三处 session 唯一需要写的一行：`self._link = make_session_link(self, "live_auto")`。"""
    return SessionLink(session, FLAVORS[flavor])
