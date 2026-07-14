"""AiTradeSession —— AI 自动交易会话（ai-auto-trading §2.1 / §2.6）。

镜像 `LiveAutoSession` 的逐根驱动（OKX 公共 WS + 暖机 priming + 确认根流水线 + monitor 循环 +
reconcile + 会话级止盈止损 + flatten/stop + WS 扇出），但把「单策略信号 → executor.apply」替换为
「**多策略信号板（每策略影子 SimBroker 回放，P0-1）→ AI 决策器综合研判 → 映射 Signal → 同一个
LiveExecutor 守护执行**」，并按用户设定轮数做滚动摘要式上下文压缩。

并发安全（本方案相对 live-auto 的新增关键点）：
- `_exec_lock`（P1-1）：串行化**一切** executor 写路径（决策 apply、会话止盈止损平仓、stop flatten、
  reconcile 的 projected 写回），杜绝长 LLM 决策轮与 5s monitor 循环并发改 `projected`。
- `_deciding`（P1-2）：in-flight 守卫。决策轮以后台任务运行；上一轮未完成时新确认根到来则本轮跳过
  （推 `decision{skipped:"decision_in_flight"}`），不排队、不并发。影子 broker 每根仍同步步进
  （快，无 LLM），仅 LLM 决策被守卫。
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections import deque

from ..db import repositories as repo
from ..db.session import get_sessionmaker
from ..engine.metrics import _empty_metrics
from ..live_auto.executor import LiveExecutor
from ..live_auto.explain import build_atomic_explain
from ..okx.rest_client import get_rest_client
from ..okx.types import BAR_SECONDS, Candle
from ..okx.ws_client import OKXWSClient
from ..paper.strategy_factory import PaperStrategySpec
from ..strategies.base import Signal
from .decider import AiTradeDecider, TradeDecision
from .market_context import build_market_context
from .market_digest import build_market_digest
from .market_window import MarketWindow
from .multi_timeframe_context import build_multi_timeframe_context
from .prompts import DECISION_TOOL_NAME
from .signals import (
    Consensus,
    StrategySignalView,
    apply_consensus_gate,
    compute_consensus,
    decision_to_signals,
    evaluate_signal_board,
    make_shadow_broker,
)
from .trace import _TRACES_MAX, TraceEmit, new_round_trace, trace_event

_SNAPSHOT_EQUITY_MAX = 1000
_DECISIONS_MAX = 50
# ai-trade-stats-dashboard §3.3：finalize 时权益快照下采样上限（≤此值原样，超出等距抽样保留首尾），
# 避免长会话 metrics_json 膨胀（R-8）。决策摘要沿用 _DECISIONS_MAX（=50）。
_EQUITY_DIGEST_MAX = 500
# 研判思维链有界摘录长度（ai-trade-reasoning-chain §2.5，RC-2/RC-3）：进 journal（摘要素材）与 decision
# 事件（_decisions 环形 + WS snapshot 重放）的一律为此摘录；完整全文只走 agent_trace 流与 _traces(≤20)。
_ANALYSIS_EXCERPT_CHARS = 400

logger = logging.getLogger(__name__)

_ACTION_LABEL = {"open_long": "开多", "open_short": "开空", "close": "平仓", "hold": "观望"}


class AiTradeSession:
    """一个 (multi-strategy, inst, bar) 的 AI 自动交易会话（单活跃独占，真实资金）。"""

    def __init__(self, session_id: str, run_id: int, specs: list[PaperStrategySpec],
                 inst_id: str, bar: str, executor: LiveExecutor, decider: AiTradeDecider, *,
                 take_profit_usd: float | None = None, stop_loss_usd: float | None = None,
                 priming: bool = True, priming_bars: int = 300,
                 context_refresh_rounds: int = 10, wake_interval_s: float = 0.0):
        self.session_id = session_id
        self.run_id = run_id
        self._specs = specs
        self.strategy_ids = [s.strategy_id for s in specs]
        self.strategy_names = [s.strategy_name for s in specs]
        self.inst_id = inst_id
        self.bar = bar
        self.executor = executor
        self.decider = decider
        self.take_profit_usd = take_profit_usd
        self.stop_loss_usd = stop_loss_usd
        self.priming = priming
        self.priming_bars = priming_bars
        self.context_refresh_rounds = context_refresh_rounds
        # 墙钟定时唤醒周期（秒；0=关闭）。默认 0 → 定时唤醒关闭，既有直接构造会话的单测行为逐字不变
        # （与 position_step_pct=0.0 同零回归范式，仅经启动请求显式开启）。
        self.wake_interval_s = wake_interval_s

        self.state = "running"
        self.stop_reason: str | None = None
        self.started_ts = int(time.time())
        self.rounds_completed = 0
        self.flatten_result: dict | None = None
        self.flatten_failed = False
        self.residual: dict | None = None
        self.degraded = False

        self._window = MarketWindow(
            inst_id,
            bar,
            specs,
            priming_bars,
            context_enabled=executor.settings.ai_trade_multi_timeframe_context,
        )
        self._shadows = [make_shadow_broker() for _ in specs]
        self._exec_lock = asyncio.Lock()      # P1-1：串行化一切 executor 写
        self._deciding = False                # P1-2：决策轮 in-flight 守卫
        self._decision_task: asyncio.Task | None = None
        self._monitor_task: asyncio.Task | None = None
        self._last_candle_wall = 0.0
        # 墙钟定时唤醒复用态（wake-interval §五）：最近一次确认根信号板快照 + 其窗口索引 + 最近实时价 +
        # 最近一轮决策开始的墙钟（定时器基准，任一轮决策开始即刷新以与 K 线唤醒合流去重）。
        self._last_board: list[StrategySignalView] | None = None
        self._last_bar_index = 0
        self._last_live_px: float | None = None
        self._last_decision_started_wall = 0.0
        self._stale_degraded = False
        self._degraded_seen = False
        self._last_status_side = executor.projected
        self._warmed_bars = 0

        self._equity: deque[dict] = deque(maxlen=_SNAPSHOT_EQUITY_MAX)
        self._peak_equity = executor.budget_usd
        self._decisions: deque[dict] = deque(maxlen=_DECISIONS_MAX)
        self._last_decision: dict | None = None
        # 决策认知轨迹（ai-auto-trading-agent-trace §3.3）：瞬态环形，仅内存 / 快照回放。
        self._traces: deque[dict] = deque(maxlen=_TRACES_MAX)
        self._round_trace: dict | None = None   # 当前在飞轮次（原地累积；snapshot 出口防御性拷贝冻结）
        self._subscribers: set[asyncio.Queue] = set()
        self._ws: OKXWSClient | None = None

    @property
    def poll_interval_s(self) -> int:
        return self.executor.settings.live_auto_poll_interval_s

    # ---------- fan-out ----------
    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=1000)
        self._subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._subscribers.discard(q)

    async def _push(self, event: dict) -> None:
        for q in list(self._subscribers):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                pass

    async def _dispatch(self, events: list[dict]) -> None:
        """统一分发 executor 事件：trade 注入 explain.meta + best-effort 落库，再 WS 扇出。"""
        for event in events:
            if event.get("type") == "trade" and isinstance(event.get("data"), dict):
                trade = event["data"]
                self._inject_explain_meta(trade)
                await self._persist_trade(trade)
            await self._push(event)

    def _inject_explain_meta(self, trade: dict) -> None:
        explain = trade.get("explain")
        if not isinstance(explain, dict):
            return
        meta = dict(explain.get("meta") or {})
        meta.setdefault("source", "ai")
        meta.setdefault("instId", self.inst_id)
        meta.setdefault("bar", self.bar)
        meta.setdefault("barTs", trade.get("ts"))
        if trade.get("clOrdId"):
            meta.setdefault("clOrdId", trade["clOrdId"])
        if trade.get("ordId"):
            meta.setdefault("ordId", trade["ordId"])
        explain["meta"] = meta

    async def _persist_trade(self, trade: dict) -> None:
        try:
            sm = get_sessionmaker()
            async with sm() as s:
                await repo.append_live_trade(s, self.run_id, trade)
        except Exception as exc:  # noqa: BLE001 —— best-effort 审计落库，失败不阻塞热路径
            logger.warning("append_live_trade failed (run_id=%s): %s", self.run_id, exc)

    # ---------- lifecycle ----------
    async def start(self) -> None:
        """暖机（仅热指标 + 种入影子立场，不下任何真单）后订阅实时 WS 与 monitor 循环。"""
        await self._prime()
        self._ws = OKXWSClient(self.inst_id, self.bar, self.feed_candle)
        self._ws.start()
        now = time.time()
        self._last_candle_wall = now
        # 首次定时唤醒在启动后约 wake_interval_s 触发（而非 t=0 立刻，R-8）。
        self._last_decision_started_wall = now
        self._monitor_task = asyncio.create_task(self._monitor_loop())
        await self._push({"type": "status", "data": self._status_payload()})

    async def _prime(self) -> None:
        """预填窗口 + 逐根回放种入各策略影子（每策略独立，P0-1）；真实决策从首个实时确认根起。"""
        await self._window.prefill_htf(self.priming_bars if self.priming else 60)
        rows = await self._window.fetch_priming_rows(self.priming_bars if self.priming else 60)
        for row in rows:
            self._window.prime_append(row)
        self._warmed_bars = len(self._window.candles)
        if not self.priming or not rows:
            return
        frames = self._window.build_strategy_frames()   # 各策略在整段暖机窗口上的因果帧
        board: list[StrategySignalView] | None = None
        n = len(self._window.candles)
        for i in range(n):
            board = evaluate_signal_board(self._specs, self._shadows, frames, i)  # 仅种入影子
            if i % 50 == 0:
                await asyncio.sleep(0)                   # 周期让出事件循环，避免暖机长阻塞
        # 捕获暖机末根返回视图供定时唤醒复用（不额外多调一次 evaluate_signal_board，避免末根被双重步进）：
        # 首次定时唤醒可在会话启动 ~wake_interval_s 后即触发，无需干等第一根实时确认 K 线收盘。
        self._last_board = board
        self._last_bar_index = n - 1
        self._last_live_px = self._ref_px()

    async def stop(self, reason: str = "user_stop") -> dict:
        """平掉自管持仓 + 停 WS/monitor/决策 + 落 Run 终态（幂等：非 running 直接返回上次结果）。"""
        if self.state != "running":
            return self.flatten_result or {"ok": True, "closedSz": 0}
        self.state = "stopped"
        self.stop_reason = reason
        self._cancel_task("_monitor_task")
        self._cancel_task("_decision_task")
        if self._ws is not None:
            await self._ws.stop()
        ref_px = self._ref_px()
        ts = self._ref_ts()
        try:
            async with self._exec_lock:
                self.flatten_result = await self.executor.flatten(ref_px, ts)
        except Exception as exc:  # noqa: BLE001 —— 平仓失败如实记录，仍完成停止流程
            self.flatten_result = {"ok": False, "message": str(exc)}
        await self._handle_flatten_result(ts)
        await self._finalize_run(ts)
        await self._push({"type": "status", "data": self._status_payload()})
        return self.flatten_result

    def _cancel_task(self, attr: str) -> None:
        """取消命名后台任务，但绝不取消自身（会话止损在 monitor/决策任务内触发 stop，R-2）。"""
        task = getattr(self, attr, None)
        if task is not None and task is not asyncio.current_task():
            task.cancel()
        setattr(self, attr, None)

    async def _handle_flatten_result(self, ts: int) -> None:
        result = self.flatten_result or {}
        if result.get("trade"):
            await self._dispatch([{"type": "trade", "data": result["trade"]}])
        if result.get("ok"):
            return
        self.flatten_failed = True
        self.residual = result.get("residual")
        await self._push({"type": "error", "data": {
            "ts": ts, "code": "AI_TRADE_FLATTEN_FAILED",
            "message": "AI 自动交易停止时平仓失败，请立即到下方实盘面板手动平仓",
            "residual": self.residual}})

    def _ref_px(self) -> float:
        c = self._window.candles
        return float(c[-1]["c"]) if c else self.executor.entry_price

    def _ref_ts(self) -> int:
        c = self._window.candles
        return int(c[-1]["ts"]) if c else self.started_ts

    # ---------- monitor 循环 ----------
    async def _monitor_loop(self) -> None:
        try:
            while self.state == "running":
                await asyncio.sleep(self.poll_interval_s)
                if self.state != "running":
                    break
                await self._monitor_tick()
        except asyncio.CancelledError:
            pass

    async def _monitor_tick(self) -> None:
        """单轮盯市：reconcile（经 _exec_lock）+ 盯市 + staleness + 会话止盈止损（经 _exec_lock）。"""
        now_ts = int(time.time())
        mark = await self._latest_mark_px()
        async with self._exec_lock:
            events = await self.executor.reconcile(mark, now_ts)
        await self._dispatch(events)
        await self._push_status_if_side_changed()
        await self._emit_equity(mark, now_ts)
        await self._emit_position(mark)
        await self._check_staleness(now_ts)
        self._degraded_seen = (self._degraded_seen or self.executor.degraded
                               or self._stale_degraded)
        await self._maybe_session_exit(mark, now_ts)
        await self._maybe_interval_wake()

    async def _maybe_interval_wake(self) -> None:
        """墙钟定时唤醒（第三触发源，wake-interval §2.2）：满足全部条件时用「最近确认根信号板 + 当前
        实时价」触发一轮标准决策。in-flight/关闭/非运行/行情陈旧/尚无信号板 → 一律不唤醒。"""
        if (self.wake_interval_s <= 0 or self.state != "running" or self._deciding
                or self._stale_degraded or self._last_board is None):
            return
        now = time.time()
        if now - self._last_decision_started_wall < self.wake_interval_s:
            return
        self._deciding = True                 # 同步认领槽位：check→set 间无 await（与 feed_candle 同范式，P1-2）
        ref_px = self._current_px()           # 纯同步取实时价（不 await）
        self._last_decision_started_wall = now
        self._decision_task = asyncio.create_task(
            self._run_decision_round(self._last_board, self._last_bar_index, ref_px,
                                     int(now), trigger="interval"))

    def _current_px(self) -> float:
        """定时唤醒取实时价：优先最近未确认/确认根实时价（`is not None` 判定，防 0 价异常断面误回落），
        回落到最近确认收盘价（`_ref_px`）。价恒 > 0 时二者等价，此处更防御性。"""
        return self._last_live_px if self._last_live_px is not None else self._ref_px()

    async def _latest_mark_px(self) -> float:
        c = self._window.candles
        if c:
            return float(c[-1]["c"])
        try:
            px = await get_rest_client().get_ticker(self.inst_id)
        except Exception:  # noqa: BLE001 —— 取价失败回落 entry_price（降级，不阻塞盯市）
            px = None
        return float(px) if px is not None else self.executor.entry_price

    async def _check_staleness(self, now_ts: int) -> None:
        if self.state != "running" or self._last_candle_wall <= 0:
            return
        settings = self.executor.settings
        stale_after = BAR_SECONDS.get(self.bar, 60) * settings.live_auto_staleness_factor
        elapsed = now_ts - self._last_candle_wall
        if elapsed <= stale_after:
            await self._clear_stale_if_recovered()
            return
        if settings.live_auto_flatten_on_stale and elapsed > stale_after * 4:
            await self.stop("stale_feed")
            return
        if not self._stale_degraded:
            self._stale_degraded = True
            await self._push({"type": "status", "data": {
                **self._status_payload(),
                "message": "行情连接异常，AI 自动交易暂停响应 K 线（交易所侧止盈止损仍生效）"}})

    async def _clear_stale_if_recovered(self) -> None:
        if not self._stale_degraded:
            return
        self._stale_degraded = False
        await self._push({"type": "status", "data": {
            **self._status_payload(), "message": "行情已恢复，AI 自动交易盯市恢复正常"}})

    # ---------- 核心：每根已确认 K 线 = 一个决策轮 ----------
    async def feed_candle(self, candle: Candle) -> None:
        """OKX WS 回调（也可被测试直接注入）。确认根：步进影子信号板 → 触发一轮 AI 决策。"""
        if self.state != "running":
            return
        if not candle.confirm:
            self._last_live_px = float(candle.c)   # 两根 K 线之间的当前价（供定时唤醒研判最新价位）
            await self._push({"type": "candle", "data": {
                "ts": candle.ts, "o": candle.o, "h": candle.h, "l": candle.l,
                "c": candle.c, "vol": candle.vol, "confirm": False}})
            return
        if self._window.is_duplicate(candle):
            return
        row = self._window.append_confirmed(candle)
        self._last_candle_wall = time.time()
        decision_as_of_ts = int(candle.ts) + BAR_SECONDS[self.bar]
        await self._window.maybe_refresh_htf(decision_as_of_ts)
        frames = self._window.build_strategy_frames()
        i = self._window.cur_index()
        ref_px = float(row["c"])
        ts = int(row["ts"])
        board = evaluate_signal_board(self._specs, self._shadows, frames, i)  # 步进影子（快，无 LLM）
        self._last_board = board                 # 存最近确认根信号板快照 + 索引 + 实时价（供定时唤醒复用）
        self._last_bar_index = i
        self._last_live_px = ref_px
        await self._push({"type": "candle", "data": {**row, "confirm": True}})
        if self._deciding:                       # P1-2：上一轮未完成→跳过本轮，不并发
            await self._emit_decision_skipped(ts, board)
            return
        self._deciding = True
        self._last_decision_started_wall = time.time()   # 任一轮决策开始即刷新计时器，与定时唤醒合流去重
        self._decision_task = asyncio.create_task(
            self._run_decision_round(board, i, ref_px, ts, trigger="candle"))

    async def _run_decision_round(self, board: list[StrategySignalView], i: int,
                                  ref_px: float, ts: int, trigger: str = "candle") -> None:
        """一个决策轮：装配输入 → AI 决策 → 映射 Signal 守护执行 → 记录/摘要 → 推事件（含认知轨迹）。

        `trigger`（默认 "candle"，K 线唤醒；"interval"=墙钟定时唤醒）透传到 payload / round_start 轨迹 /
        decision 事件，供 prompt 与前端区分唤醒来源；决策安全性不依赖它。
        """
        round_no = self.rounds_completed + 1     # RV-5：try 之前绑定，except 可安全引用
        try:
            payload = self._build_decision_payload(round_no, ts, ref_px, board, trigger)
            consensus = compute_consensus(board)                 # E2：每轮只算一次（供门禁 + wire）
            payload["consensus"] = self._consensus_wire(consensus)
            await self._begin_round_trace(round_no, ts, ref_px, payload, board)  # RV-7：consensus 之后
            decision, degrade = await self.decider.decide(
                payload, emit=self._make_trace_emit(round_no, ts))
            decision, degrade = self._apply_consensus_gate(decision, consensus, degrade)  # E2 门禁
            executed = await self._execute_decision(decision, ref_px, i, ts)
            self.rounds_completed = round_no
            # RC-2/RC-3：研判全文只走 agent_trace/_traces；进 journal 与 decision 事件的取有界摘录（≤400）。
            # 降级轮 last_analysis 恒为空串（RC-1）→ excerpt=None（journal/事件不带残段）。
            excerpt = self.decider.last_analysis[:_ANALYSIS_EXCERPT_CHARS] or None
            # R-P1-3：commit_round 收门禁后的最终 decision，journal/摘要据此记真实动作（保持上下文纯净）。
            summary = await self.decider.commit_round(payload, decision, executed, analysis=excerpt)
            await self._emit_tool_call_trace(round_no, ts, decision)
            await self._emit_result_trace(round_no, ts, executed, degrade)
            await self._emit_decision(round_no, ts, decision, board, executed,
                                      market_context=payload.get("marketContext"),
                                      consensus=payload.get("consensus"),
                                      degrade_reason=degrade, analysis=excerpt,
                                      market_digest=payload.get("marketDigest"),
                                      multi_timeframe_context=payload.get("multiTimeframeContext"),
                                      trigger=trigger)
            if degrade:
                await self._emit_degraded(ts, degrade)
            if summary is not None:
                await self._emit_summary_trace(round_no, ts, summary)
                await self._emit_summary(round_no, ts, summary)
            await self._post_round(ref_px, ts)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 —— 决策轮异常不崩会话：记 error 事件，等下一根
            logger.warning("ai-trade decision round failed: %s", exc)
            await self._emit_error_trace(round_no, ts, "本轮决策异常，已跳过")
            await self._push({"type": "error", "data": {
                "ts": ts, "code": "AI_TRADE_ROUND_FAILED", "message": "本轮决策异常，已跳过"}})
        finally:
            self._deciding = False

    # ---------- 决策认知轨迹（ai-auto-trading-agent-trace §3.3）----------
    async def _begin_round_trace(self, round_no: int, ts: int, ref_px: float,
                                 payload: dict, board: list[StrategySignalView]) -> None:
        """round_start：装配在飞轮次（引用入环、后续原地累积）+ 推输入快照（RV-7：须在 consensus 之后）。"""
        slim = [self._view_slim(v) for v in board]
        self._round_trace = new_round_trace(round_no, ts, ref_px, payload, slim)
        self._traces.append(self._round_trace)
        trace = self._round_trace
        await self._push(trace_event(
            "round_start", round_no, ts, lastPx=ref_px, position=trace["position"],
            signalBoard=trace["signalBoard"], marketContext=trace["marketContext"],
            multiTimeframeContext=trace["multiTimeframeContext"],
            consensus=trace["consensus"], marketDigest=trace["marketDigest"],
            trigger=trace["trigger"]))

    def _make_trace_emit(self, round_no: int, ts: int) -> TraceEmit:
        """返回注入 decider 的 emit 闭包：补 round/ts、原地累积 thinking/analysis、扇出 agent_trace。"""
        async def _emit(part: dict) -> None:
            phase = part.get("phase")
            data = {"phase": phase, "round": round_no, "ts": ts}
            trace = self._round_trace
            if phase == "retry":                 # §4.4：重试面包屑——重置本轮累积 + 计数后扇出
                self._apply_retry_marker(trace, part, data)
                await self._push({"type": "agent_trace", "data": data})
                return
            delta = part.get("delta")
            if delta is not None:
                data["delta"] = delta
                if trace is not None and phase in ("thinking", "analysis"):
                    trace[phase] += delta
            name = part.get("name")
            if name is not None:
                data["name"] = name
                if trace is not None and phase == "tool_call_start":
                    trace["toolCallPending"] = True
            await self._push({"type": "agent_trace", "data": data})
        return _emit

    @staticmethod
    def _apply_retry_marker(trace: dict | None, part: dict, data: dict) -> None:
        """retry 阶段（§4.4）：把面包屑字段写入外发 `data`，并把在飞轮次累积重置——
        丢弃失败尝试的 thinking/analysis、复位 toolCallPending（R-1：清失败尝试残留的工具挂起指示）、
        state 回 deciding、累加本轮重试计数并记 lastRetryReason。"""
        data["attempt"] = part.get("attempt")
        data["maxAttempts"] = part.get("maxAttempts")
        data["reason"] = part.get("reason")
        if trace is None:
            return
        trace["thinking"] = ""
        trace["analysis"] = ""
        trace["toolCallPending"] = False
        trace["state"] = "deciding"
        trace["retries"] = int(trace.get("retries", 0)) + 1
        trace["lastRetryReason"] = part.get("reason")

    async def _emit_tool_call_trace(self, round_no: int, ts: int,
                                    decision: TradeDecision) -> None:
        """tool_call：门禁后最终决策工具入参（与真实执行一致，降级则如实呈现 hold）。"""
        tool_input = {"action": decision.action, "sizeFraction": decision.size_fraction,
                      "confidence": decision.confidence, "reason": decision.reason}
        if self._round_trace is not None:
            self._round_trace["toolCall"] = tool_input
            self._round_trace["toolCallPending"] = False
        await self._push(trace_event("tool_call", round_no, ts,
                                     name=DECISION_TOOL_NAME, input=tool_input))

    async def _emit_result_trace(self, round_no: int, ts: int, executed: dict,
                                 degrade: str | None) -> None:
        """result：守护执行结果 + 降级原因；state=skipped→"skipped"，否则"done"。"""
        state = "skipped" if executed.get("skipped") else "done"
        if self._round_trace is not None:
            self._round_trace["executed"] = executed
            self._round_trace["degradeReason"] = degrade
            self._round_trace["state"] = state
        await self._push(trace_event("result", round_no, ts,
                                     executed=executed, degradeReason=degrade))

    async def _emit_summary_trace(self, round_no: int, ts: int, summary: str) -> None:
        """summary：到期上下文刷新（历史执行摘要），呼应「上下文纯净」机制（G2）。"""
        if self._round_trace is not None:
            self._round_trace["summary"] = summary
        await self._push(trace_event("summary", round_no, ts, summary=summary))

    async def _emit_error_trace(self, round_no: int, ts: int, message: str) -> None:
        """error：本轮决策异常兜底（不崩会话），state="error"。"""
        if self._round_trace is not None:
            self._round_trace["errorMessage"] = message
            self._round_trace["state"] = "error"
        await self._push(trace_event("error", round_no, ts, message=message))

    def _apply_consensus_gate(self, decision: TradeDecision, consensus: Consensus,
                              degrade: str | None) -> tuple[TradeDecision, str | None]:
        """薄封装 `signals.apply_consensus_gate`（§2.8/R1）：传 `settings.ai_trade_min_consensus`。

        对外行为逐字不变——门禁降级则 `degradeReason` 归并为 `degrade or gate_reason`（保留决策器
        先前的回落原因，如 timeout），否则透传原 `degrade`。共识门禁纯核心已上移至 `signals` 供回测共用。
        """
        decision, gate_reason = apply_consensus_gate(
            decision, consensus, self.executor.settings.ai_trade_min_consensus)
        return decision, (degrade or gate_reason)

    async def _execute_decision(self, decision: TradeDecision, ref_px: float, i: int,
                                ts: int) -> dict:
        """映射决策为 Signal 并经 LiveExecutor 守护执行（一切写经 _exec_lock 串行，P1-1）。"""
        signals = self._decision_to_signals(decision, board_digest=self._board_digest())
        if not signals:
            return {"ok": True, "action": decision.action, "skipped": False,
                    "side": self.executor.projected, "qty": self.executor.entry_qty_sz,
                    "positionAfter": self.executor.projected}
        async with self._exec_lock:
            if self.state != "running":       # stop() 抢先置 stopped：本轮不再下单（避免停止竞态下多此一举的开仓+随即被 flatten 平掉，白付手续费）
                return {"ok": True, "action": decision.action, "skipped": True,
                        "reason": "stopped", "side": self.executor.projected,
                        "qty": self.executor.entry_qty_sz,
                        "positionAfter": self.executor.projected}
            events = await self.executor.apply(signals, ref_px=ref_px, bar_index=i, ts=ts)
            events += await self.executor.reconcile(ref_px, ts)
        await self._dispatch(events)
        await self._push_status_if_side_changed()
        traded = any(e.get("type") == "trade" for e in events)
        return {"ok": True, "action": decision.action, "skipped": False, "traded": traded,
                "side": self.executor.projected, "qty": self.executor.entry_qty_sz,
                "positionAfter": self.executor.projected}

    def _decision_to_signals(self, decision: TradeDecision, *, board_digest: str) -> list[Signal]:
        """决策 → Signal：调纯核心 `signals.decision_to_signals` + 补 AI explain 富化（§2.8/R1）。

        对外行为逐字不变——实盘的 `board_digest`/atomic-explain 富化保留（回测不需该富化，直接用纯核心）。
        `open_long/short` 带 sizeFraction，`close` 带理由，均补 `explain`；`hold=[]`。
        """
        signals = decision_to_signals(decision)
        if not signals:
            return signals
        explain = self._ai_explain(decision.action, decision.reason, decision.confidence,
                                   board_digest)
        for sig in signals:
            sig.explain = explain
        return signals

    @staticmethod
    def _ai_explain(action: str, reason: str, confidence: float | None,
                    digest: str) -> dict:
        """AI 决策归因（透传到 trade.explain，面板展示「AI 决策」来源 + 置信度 + 信号板摘要）。"""
        side = {"open_long": "long", "open_short": "short", "close": "close"}[action]
        headline = f"AI 决策 · {_ACTION_LABEL.get(action, action)}"
        explain = build_atomic_explain(side, reason or headline,
                                       summary="AI 综合多策略信号板研判后的自动交易决策")
        explain["meta"] = {"source": "ai", "confidence": confidence, "signalBoardDigest": digest}
        return explain

    def _board_digest(self) -> str:
        """把当前影子立场压成一行短摘要（随 explain 落库，供归因展示）。"""
        return ", ".join(f"{s.pos.side}" for s in self._shadows)

    async def _post_round(self, ref_px: float, ts: int) -> None:
        await self._emit_equity(ref_px, ts)
        await self._emit_position(ref_px)
        await self._maybe_session_exit(ref_px, ts)

    async def _maybe_session_exit(self, ref_px: float, ts: int) -> None:
        """会话级金额阈值：累计盈亏 ≥ +TP 或 ≤ −SL → 自动平仓（经 _exec_lock）+ 停会话。"""
        pnl = self.executor.session_pnl_usd
        reason = None
        if self.take_profit_usd is not None and pnl >= self.take_profit_usd:
            reason = "session_take_profit"
        elif self.stop_loss_usd is not None and pnl <= -self.stop_loss_usd:
            reason = "session_stop_loss"
        if reason is not None:
            await self.stop(reason)

    # ---------- decision / summary 事件 ----------
    def _build_decision_payload(self, round_no: int, ts: int, ref_px: float,
                                board: list[StrategySignalView],
                                trigger: str = "candle") -> dict:
        return {
            "round": round_no, "barTs": ts, "lastPx": ref_px, "trigger": trigger,
            "position": self.executor.position_payload(ref_px),
            "sessionPnlUsd": round(self.executor.session_pnl_usd, 4),
            "budgetUsd": self.executor.budget_usd,
            "takeProfitUsd": self.take_profit_usd, "stopLossUsd": self.stop_loss_usd,
            "marketContext": build_market_context(         # E1：只读；数据不足/禁用/异常 → None
                self._window.base_df(), self.bar, self.executor.settings),
            "marketDigest": build_market_digest(           # R1：价格走势/成交量/因子证据；同样安全降级 → None
                self._window.base_df(), self.bar, self.executor.settings),
            "multiTimeframeContext": self._build_multi_timeframe_context(ts, ref_px),
            "signalBoard": [self._view_full(v) for v in board]}

    def _build_multi_timeframe_context(self, ts: int, ref_px: float) -> dict | None:
        try:
            return build_multi_timeframe_context(
                execution_bar=self.bar,
                frames=self._window.context_frames(),
                as_of_ts=ts + BAR_SECONDS[self.bar],
                last_px=ref_px,
                settings=self.executor.settings,
            )
        except ValueError as exc:
            logger.warning(
                "invalid multi-timeframe context contract: bar=%s ts=%s price=%s (%s)",
                self.bar, ts, ref_px, exc,
            )
            return None

    @staticmethod
    def _consensus_wire(consensus: Consensus) -> dict:
        """`Consensus` dataclass → camelCase wire dict（供 prompt 与 decision 事件，E2）。"""
        return {"longVotes": consensus.long_votes, "shortVotes": consensus.short_votes,
                "flatVotes": consensus.flat_votes, "total": consensus.total,
                "netStance": consensus.net_stance, "agreement": consensus.agreement}

    @staticmethod
    def _view_full(v: StrategySignalView) -> dict:
        risk = v.risk_params
        return {"strategyId": v.strategy_id, "name": v.name, "stance": v.stance,
                "latestSignal": v.latest_signal, "strength": round(v.strength, 4),
                "reason": v.reason, "stopLossPct": risk.stop_loss_pct,
                "takeProfitPct": risk.take_profit_pct, "maxHoldBars": risk.max_hold_bars}

    @staticmethod
    def _view_slim(v: StrategySignalView) -> dict:
        return {"strategyId": v.strategy_id, "name": v.name, "stance": v.stance,
                "latestSignal": v.latest_signal, "strength": round(v.strength, 4),
                "reason": v.reason}

    async def _emit_decision(self, round_no: int, ts: int, decision: TradeDecision,
                             board: list[StrategySignalView], executed: dict, *,
                             market_context: dict | None = None,
                             consensus: dict | None = None,
                             degrade_reason: str | None = None,
                             analysis: str | None = None,
                             market_digest: dict | None = None,
                             multi_timeframe_context: dict | None = None,
                             trigger: str = "candle") -> None:
        """推 decision 事件；E1/E2/E3 + R1 研判摘录/证据摘要搭顺风车下发（旧前端忽略未知字段，向前兼容）。

        `analysis` 为有界摘录（≤`_ANALYSIS_EXCERPT_CHARS`，供左栏决策卡首段；完整思维链见 agent_trace/
        snapshot.agentTraces，RC-3）；`market_digest` 为本轮价格走势/成交量/因子证据（小、~10 数值）；
        `trigger` 为本轮唤醒来源（candle/interval，供前端区分周期唤醒）。
        """
        data = {"ts": ts, "round": round_no, "action": decision.action,
                "sizeFraction": decision.size_fraction, "confidence": decision.confidence,
                "reason": decision.reason, "signalBoard": [self._view_slim(v) for v in board],
                "executed": executed, "marketContext": market_context,
                "consensus": consensus, "degradeReason": degrade_reason,
                "analysis": analysis, "marketDigest": market_digest,
                "multiTimeframeContext": multi_timeframe_context, "trigger": trigger}
        self._last_decision = data
        self._decisions.append(data)
        await self._push({"type": "decision", "data": data})

    async def _emit_decision_skipped(self, ts: int, board: list[StrategySignalView]) -> None:
        data = {"ts": ts, "round": self.rounds_completed, "action": "hold",
                "sizeFraction": None, "confidence": None, "reason": "上一轮决策仍在进行，本轮跳过",
                "signalBoard": [self._view_slim(v) for v in board],
                "executed": {"ok": True, "skipped": True, "reason": "decision_in_flight"},
                "marketContext": None, "consensus": None, "degradeReason": None,
                "analysis": None, "marketDigest": None,
                "multiTimeframeContext": None, "trigger": "candle"}
        self._last_decision = data
        self._decisions.append(data)
        await self._push({"type": "decision", "data": data})

    async def _emit_degraded(self, ts: int, reason: str) -> None:
        self.degraded = True
        await self._push({"type": "status", "data": {
            **self._status_payload(),
            "message": f"AI 决策降级为观望（{reason}）：本轮未下单"}})

    async def _emit_summary(self, round_no: int, ts: int, summary: str) -> None:
        await self._push({"type": "summary", "data": {"ts": ts, "round": round_no,
                                                      "summary": summary}})

    # ---------- 净值 / 持仓 / 状态 ----------
    async def _emit_equity(self, ref_px: float, ts: int) -> None:
        equity = self.executor.budget_usd + self.executor.session_pnl_usd
        self._peak_equity = max(self._peak_equity, equity)
        dd = (equity / self._peak_equity - 1.0) if self._peak_equity > 0 else 0.0
        record = {"ts": ts, "equity": equity, "drawdown": min(dd, 0.0)}
        self._equity.append(record)
        await self._push({"type": "equity", "data": record})

    async def _emit_position(self, ref_px: float) -> None:
        pos = self.executor.position_payload(ref_px)
        if pos is not None:
            await self._push({"type": "position", "data": pos})

    async def _push_status_if_side_changed(self) -> None:
        side = self.executor.projected
        if side == self._last_status_side:
            return
        self._last_status_side = side
        await self._push({"type": "status", "data": self._status_payload()})

    def _status_payload(self) -> dict:
        return {"state": self.state, "warmedBars": self._warmed_bars,
                "sessionPnlUsd": self.executor.session_pnl_usd,
                "positionSide": self.executor.projected,
                "budgetUsd": self.executor.budget_usd, "leverage": self.executor.leverage,
                "roundsCompleted": self.rounds_completed,
                "roundsSinceSummary": self.decider.rounds_since_summary,
                "contextRefreshRounds": self.context_refresh_rounds,
                "wakeIntervalMinutes": self._wake_interval_minutes(),
                "stopReason": self.stop_reason,
                "degraded": self.executor.degraded or self._stale_degraded or self.degraded,
                "flattenFailed": self.flatten_failed, "residual": self.residual}

    def _wake_interval_minutes(self) -> float | None:
        """墙钟定时唤醒周期回执换算（秒→分，关闭态存 None，与分组/config 口径统一，RN-7）。"""
        return round(self.wake_interval_s / 60, 4) if self.wake_interval_s > 0 else None

    def snapshot(self) -> dict:
        mark = self._ref_px()
        # RV-1：在飞轮 _round_trace 是被原地 mutate 的引用；对每轮做浅拷贝（顶层键即冻结，
        # thinking/analysis 为不可变 str 且 mutate 走键重绑定，不回灌拷贝出的 dict），避免快照握手
        # await 期间决策任务续写导致重连客户端对同段文字二次追加（每段增量恰好下发一次）。
        return {"trades": list(self.executor.trades),
                "equity": list(self._equity),
                "position": self.executor.position_payload(mark),
                "status": self._status_payload(),
                "decisions": list(self._decisions),
                "contextSummary": self.decider.summary,
                "agentTraces": [dict(t) for t in self._traces]}

    def info(self) -> dict:
        return {"sessionId": self.session_id, "runId": self.run_id, "state": self.state,
                "instId": self.inst_id, "bar": self.bar,
                "strategyIds": self.strategy_ids, "strategyNames": self.strategy_names,
                "budgetUsd": self.executor.budget_usd, "leverage": self.executor.leverage,
                "takeProfitUsd": self.take_profit_usd, "stopLossUsd": self.stop_loss_usd,
                "contextRefreshRounds": self.context_refresh_rounds,
                "wakeIntervalMinutes": self._wake_interval_minutes(),
                "roundsCompleted": self.rounds_completed,
                "roundsSinceSummary": self.decider.rounds_since_summary,
                "positionSide": self.executor.projected,
                "sessionPnlUsd": self.executor.session_pnl_usd, "startedTs": self.started_ts,
                "lastDecision": self._last_decision, "contextSummary": self.decider.summary,
                "degraded": self.executor.degraded or self._stale_degraded or self.degraded,
                "flattenFailed": self.flatten_failed}

    # ---------- 持久化 ----------
    async def _finalize_run(self, end_ts: int) -> None:
        budget = self.executor.budget_usd
        realized = self.executor.realized_pnl_usd
        metrics = {**_empty_metrics(),
                   "totalReturn": (realized / budget) if budget > 0 else 0.0,
                   "tradeCount": len(self.executor.trades),
                   "avgPnl": realized / max(1, len(self.executor.trades)),
                   "roundsCompleted": self.rounds_completed,
                   "finalSummary": self.decider.summary,
                   "flattenFailed": self.flatten_failed,
                   "degradedSeen": self._degraded_seen}
        # ai-trade-stats-dashboard §3.3 / P1-1 / R-4：加法富化 metrics（供历史详情画权益曲线 + AI 决策概况）。
        # 三段 digest 构造包在就地 try/except——调用点 stop() 的 flatten try 不覆盖本函数，故富化一旦抛错
        # 会中断 stop() 收尾并使 run 卡在 running。异常只 warning 并退回不含富化字段的基础 metrics，无论如何
        # 都照常 finalize_run，绝不阻断真实资金平仓后的落库收尾（保持既有 best-effort 落库风格）。
        try:
            metrics["equityDigest"] = _downsample_equity(self._equity, cap=_EQUITY_DIGEST_MAX)
            metrics["decisionsDigest"] = _digest_decisions(self._decisions, cap=_DECISIONS_MAX)
            metrics["aiStats"] = _compute_ai_stats(self._decisions, self.rounds_completed)
        except Exception as exc:  # noqa: BLE001 —— 富化对 finalize 是加法且可失败，绝不阻断落库收尾
            logger.warning("ai-trade finalize enrichment skipped for run %s: %s", self.run_id, exc)
            for key in ("equityDigest", "decisionsDigest", "aiStats"):
                metrics.pop(key, None)
        sm = get_sessionmaker()
        async with sm() as s:
            await repo.finalize_run(s, self.run_id, status="stopped", metrics=metrics,
                                    end_ts=end_ts)


# ---------- ai-trade-stats-dashboard §3.3：finalize metrics 富化纯函数（模块级、可单测、绝不抛）----------
_DECISION_REASON_CHARS = 160        # decisionsDigest.reason 截断长度（剥离全文，只留近况提示）
_ACTION_KEYS = ("open_long", "open_short", "close", "hold")   # actionCounts 的规范键（匹配 decision.action 枚举）


def _downsample_equity(records, cap: int) -> list[dict]:
    """权益快照下采样：≤cap 原样，>cap 等距抽样并**保留首尾**（供历史详情画权益 / 回撤图）。

    records 为 ``{ts, equity, drawdown}`` 序列（``self._equity`` deque）；空输入回 ``[]``。
    纯函数、绝不抛（防御性 ``dict(r)`` 拷贝，避免回灌内部引用）。
    """
    items = [dict(r) for r in records]
    n = len(items)
    if cap < 2 or n <= cap:
        return items
    step = (n - 1) / (cap - 1)
    idx = sorted({int(round(i * step)) for i in range(cap)})   # i=0→首、i=cap-1→末，去重后 ≤cap 点
    return [items[i] for i in idx]


def _executed_flag(payload) -> bool:
    """决策是否真正落到成交：跳过轮 / 无成交回执 → False（``traded`` 为 executor 真实成交标记）。绝不抛。"""
    if not isinstance(payload, dict) or payload.get("skipped"):
        return False
    return bool(payload.get("traded", False))


def _digest_decisions(records, cap: int) -> list[dict]:
    """取最近 ≤cap 条决策的紧凑摘要，剥离 signalBoard / analysis 等大字段（供详情近况列表）。空→[]。"""
    recent = list(records)[-cap:] if cap > 0 else []
    out: list[dict] = []
    for d in recent:
        reason = d.get("reason")
        out.append({
            "ts": d.get("ts"), "round": d.get("round"), "action": d.get("action"),
            "executed": _executed_flag(d.get("executed")),
            "confidence": d.get("confidence"),
            "reason": reason[:_DECISION_REASON_CHARS] if isinstance(reason, str) else None,
        })
    return out


def _compute_ai_stats(records, rounds: int) -> dict:
    """镜像 backtest ``_aggregate_stats`` 口径子集（P2-5）：动作计数 / 观望占比 / 平均置信度 / 降级轮数。

    计数在**保留的 ≤50 条决策样本**上求（``_decisions`` 环形）；``decisionRounds`` 取会话真实完成轮数
    ``rounds``（可 > 样本量）。``actionCounts`` 键匹配 ``decision.action`` 实际枚举；``avgConfidence`` 跳过
    ``None``；``degradedRounds`` 计 ``degradeReason`` 非空条数（``_decisions`` 无布尔 ``degraded`` 字段）。绝不抛。
    """
    decisions = list(records)
    counts = {k: 0 for k in _ACTION_KEYS}
    confidences: list[float] = []
    degraded = 0
    for d in decisions:
        action = d.get("action")
        if action in counts:
            counts[action] += 1
        conf = d.get("confidence")
        if isinstance(conf, (int, float)):
            confidences.append(float(conf))
        if d.get("degradeReason"):
            degraded += 1
    sample = len(decisions)
    return {
        "decisionRounds": int(rounds),
        "actionCounts": counts,
        "holdRate": round(counts["hold"] / sample, 4) if sample else 0.0,
        "avgConfidence": round(sum(confidences) / len(confidences), 4) if confidences else 0.0,
        "degradedRounds": degraded,
    }
