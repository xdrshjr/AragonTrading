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
from .market_window import MarketWindow
from .signals import StrategySignalView, evaluate_signal_board, make_shadow_broker

_SNAPSHOT_EQUITY_MAX = 1000
_DECISIONS_MAX = 50

logger = logging.getLogger(__name__)

_ACTION_LABEL = {"open_long": "开多", "open_short": "开空", "close": "平仓", "hold": "观望"}


class AiTradeSession:
    """一个 (multi-strategy, inst, bar) 的 AI 自动交易会话（单活跃独占，真实资金）。"""

    def __init__(self, session_id: str, run_id: int, specs: list[PaperStrategySpec],
                 inst_id: str, bar: str, executor: LiveExecutor, decider: AiTradeDecider, *,
                 take_profit_usd: float | None = None, stop_loss_usd: float | None = None,
                 priming: bool = True, priming_bars: int = 300,
                 context_refresh_rounds: int = 10):
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

        self.state = "running"
        self.stop_reason: str | None = None
        self.started_ts = int(time.time())
        self.rounds_completed = 0
        self.flatten_result: dict | None = None
        self.flatten_failed = False
        self.residual: dict | None = None
        self.degraded = False

        self._window = MarketWindow(inst_id, bar, specs, priming_bars)
        self._shadows = [make_shadow_broker() for _ in specs]
        self._exec_lock = asyncio.Lock()      # P1-1：串行化一切 executor 写
        self._deciding = False                # P1-2：决策轮 in-flight 守卫
        self._decision_task: asyncio.Task | None = None
        self._monitor_task: asyncio.Task | None = None
        self._last_candle_wall = 0.0
        self._stale_degraded = False
        self._degraded_seen = False
        self._last_status_side = executor.projected
        self._warmed_bars = 0

        self._equity: deque[dict] = deque(maxlen=_SNAPSHOT_EQUITY_MAX)
        self._peak_equity = executor.budget_usd
        self._decisions: deque[dict] = deque(maxlen=_DECISIONS_MAX)
        self._last_decision: dict | None = None
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
        self._last_candle_wall = time.time()
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
        for i in range(len(self._window.candles)):
            evaluate_signal_board(self._specs, self._shadows, frames, i)  # 仅种入影子，丢弃视图
            if i % 50 == 0:
                await asyncio.sleep(0)                   # 周期让出事件循环，避免暖机长阻塞

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
            await self._push({"type": "candle", "data": {
                "ts": candle.ts, "o": candle.o, "h": candle.h, "l": candle.l,
                "c": candle.c, "vol": candle.vol, "confirm": False}})
            return
        if self._window.is_duplicate(candle):
            return
        row = self._window.append_confirmed(candle)
        self._last_candle_wall = time.time()
        await self._window.maybe_refresh_htf(int(candle.ts))
        frames = self._window.build_strategy_frames()
        i = self._window.cur_index()
        ref_px = float(row["c"])
        ts = int(row["ts"])
        board = evaluate_signal_board(self._specs, self._shadows, frames, i)  # 步进影子（快，无 LLM）
        await self._push({"type": "candle", "data": {**row, "confirm": True}})
        if self._deciding:                       # P1-2：上一轮未完成→跳过本轮，不并发
            await self._emit_decision_skipped(ts, board)
            return
        self._deciding = True
        self._decision_task = asyncio.create_task(self._run_decision_round(board, i, ref_px, ts))

    async def _run_decision_round(self, board: list[StrategySignalView], i: int,
                                  ref_px: float, ts: int) -> None:
        """一个决策轮：装配输入 → AI 决策 → 映射 Signal 守护执行 → 记录/摘要 → 推事件。"""
        try:
            round_no = self.rounds_completed + 1
            payload = self._build_decision_payload(round_no, ts, ref_px, board)
            decision, degrade = await self.decider.decide(payload)
            executed = await self._execute_decision(decision, ref_px, i, ts)
            self.rounds_completed = round_no
            summary = await self.decider.commit_round(payload, decision, executed)
            await self._emit_decision(round_no, ts, decision, board, executed)
            if degrade:
                await self._emit_degraded(ts, degrade)
            if summary is not None:
                await self._emit_summary(round_no, ts, summary)
            await self._post_round(ref_px, ts)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 —— 决策轮异常不崩会话：记 error 事件，等下一根
            logger.warning("ai-trade decision round failed: %s", exc)
            await self._push({"type": "error", "data": {
                "ts": ts, "code": "AI_TRADE_ROUND_FAILED", "message": "本轮决策异常，已跳过"}})
        finally:
            self._deciding = False

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
        """决策 → Signal（open_long/short 带 sizeFraction+AI explain；close 带 explain；hold=[]）。"""
        action = decision.action
        if action == "hold":
            return []
        explain = self._ai_explain(action, decision.reason, decision.confidence, board_digest)
        if action == "open_long":
            return [Signal(side="long", strength=decision.size_fraction or 0.0,
                           reason=decision.reason, explain=explain)]
        if action == "open_short":
            return [Signal(side="short", strength=decision.size_fraction or 0.0,
                           reason=decision.reason, explain=explain)]
        return [Signal(side="close", reason=decision.reason or "ai_close", explain=explain)]

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
                                board: list[StrategySignalView]) -> dict:
        return {
            "round": round_no, "barTs": ts, "lastPx": ref_px,
            "position": self.executor.position_payload(ref_px),
            "sessionPnlUsd": round(self.executor.session_pnl_usd, 4),
            "budgetUsd": self.executor.budget_usd,
            "takeProfitUsd": self.take_profit_usd, "stopLossUsd": self.stop_loss_usd,
            "signalBoard": [self._view_full(v) for v in board]}

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
                             board: list[StrategySignalView], executed: dict) -> None:
        data = {"ts": ts, "round": round_no, "action": decision.action,
                "sizeFraction": decision.size_fraction, "confidence": decision.confidence,
                "reason": decision.reason, "signalBoard": [self._view_slim(v) for v in board],
                "executed": executed}
        self._last_decision = data
        self._decisions.append(data)
        await self._push({"type": "decision", "data": data})

    async def _emit_decision_skipped(self, ts: int, board: list[StrategySignalView]) -> None:
        data = {"ts": ts, "round": self.rounds_completed, "action": "hold",
                "sizeFraction": None, "confidence": None, "reason": "上一轮决策仍在进行，本轮跳过",
                "signalBoard": [self._view_slim(v) for v in board],
                "executed": {"ok": True, "skipped": True, "reason": "decision_in_flight"}}
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
                "stopReason": self.stop_reason,
                "degraded": self.executor.degraded or self._stale_degraded or self.degraded,
                "flattenFailed": self.flatten_failed, "residual": self.residual}

    def snapshot(self) -> dict:
        mark = self._ref_px()
        return {"trades": list(self.executor.trades),
                "equity": list(self._equity),
                "position": self.executor.position_payload(mark),
                "status": self._status_payload(),
                "decisions": list(self._decisions),
                "contextSummary": self.decider.summary}

    def info(self) -> dict:
        return {"sessionId": self.session_id, "runId": self.run_id, "state": self.state,
                "instId": self.inst_id, "bar": self.bar,
                "strategyIds": self.strategy_ids, "strategyNames": self.strategy_names,
                "budgetUsd": self.executor.budget_usd, "leverage": self.executor.leverage,
                "takeProfitUsd": self.take_profit_usd, "stopLossUsd": self.stop_loss_usd,
                "contextRefreshRounds": self.context_refresh_rounds,
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
        sm = get_sessionmaker()
        async with sm() as s:
            await repo.finalize_run(s, self.run_id, status="stopped", metrics=metrics,
                                    end_ts=end_ts)
