"""LiveAutoSession —— 实时自动交易会话（live-auto-trading §2.1 / §2.5 / §2.6）。

镜像 `PaperSession` 的逐根驱动（OKX 公共 WS + 暖机 priming + 确认根流水线 + WS 快照/事件扇出），
但把 `SimBroker` 替换为 `LiveExecutor`：每根已确认 K 线把策略信号翻译为**真实 OKX 下单 / 平仓**。

与 paper 的关键差异：
- priming 仅暖机指标窗口，**绝不下任何真单**（前置门禁已保证起始无持仓，真实下单从首个实时确认根起）。
- 每根 reconcile 真实持仓校正 projected + 计未实现盈亏；会话级金额止盈止损达阈即自动平仓 + 停。
- 停止默认平掉自管持仓（不留无人管理的真实仓），并经 `finalize_run` 落 Run 终态。
"""
from __future__ import annotations

import asyncio
import time
from collections import deque

from ..db import repositories as repo
from ..db.session import get_sessionmaker
from ..engine.backtester import candles_to_df
from ..engine.metrics import _empty_metrics
from ..okx.rest_client import get_rest_client
from ..okx.types import BAR_SECONDS, Candle
from ..okx.ws_client import OKXWSClient
from ..strategies.base import BaseStrategy
from ..paper.session import PAPER_WINDOW_MAX
from .executor import LiveExecutor

_SNAPSHOT_EQUITY_MAX = 1000   # snapshot 回放净值上限（订阅即重放末 N 条）


class LiveAutoSession:
    """一个 (strategy, inst, bar) 的实时自动交易会话（单会话独占，真实资金）。"""

    def __init__(self, session_id: str, run_id: int, strategy: BaseStrategy, inst_id: str,
                 bar: str, executor: LiveExecutor, *, take_profit_usd: float | None = None,
                 stop_loss_usd: float | None = None, priming: bool = True,
                 priming_bars: int = 300, strategy_name: str | None = None,
                 strategy_kind: str = "atomic", strategy_id: int | None = None):
        self.session_id = session_id
        self.run_id = run_id
        self.strategy = strategy
        self.strategy_id = strategy_id
        self.inst_id = inst_id
        self.bar = bar
        self.executor = executor
        self.take_profit_usd = take_profit_usd
        self.stop_loss_usd = stop_loss_usd
        self.priming = priming
        self.priming_bars = priming_bars
        self.strategy_name = strategy_name or ""
        self.strategy_kind = strategy_kind

        self.state = "running"
        self.stop_reason: str | None = None
        self.started_ts = int(time.time())
        self.flatten_result: dict | None = None

        self._window_max = min(max(strategy.warmup() + 5, priming_bars,
                                   strategy.htf_warmup_bars() + 5), PAPER_WINDOW_MAX)
        self._candles: deque[dict] = deque(maxlen=self._window_max)
        base_seconds = BAR_SECONDS.get(bar, 60)
        self._htf_bars = [tf for tf in strategy.timeframes()
                          if BAR_SECONDS.get(tf, 0) > base_seconds]
        self._htf_windows = {b: deque(maxlen=self._window_max) for b in self._htf_bars}
        self._htf_last_bucket: dict[str, int] = {}
        self._last_confirmed_ts: int | None = None
        self._bar_index = -1
        self._warmed_bars = 0
        self._equity: list[dict] = []
        self._peak_equity = executor.budget_usd
        self._subscribers: set[asyncio.Queue] = set()
        self._ws: OKXWSClient | None = None
        self._lock = asyncio.Lock()

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

    # ---------- lifecycle ----------
    async def start(self) -> None:
        """暖机窗口（仅热指标，不下单）后订阅实时 WS。"""
        await self._prefill_htf(self.priming_bars if self.priming else 60)
        await self._prime_window()
        self._ws = OKXWSClient(self.inst_id, self.bar, self.feed_candle)
        self._ws.start()
        await self._push({"type": "status", "data": self._status_payload()})

    async def _prime_window(self) -> None:
        """拉近端真实历史填窗暖机指标（**不下任何真单**，真实下单从首个实时确认根起）。"""
        bars = min(max(self.strategy.warmup() + 5, self.priming_bars), self._window_max)
        try:
            history = await get_rest_client().get_candles(self.inst_id, self.bar, limit=bars)
        except Exception:  # noqa: BLE001 —— 暖机失败不阻塞，等实时 K 线
            return
        for c in history:
            self._candles.append({"ts": c.ts, "o": c.o, "h": c.h, "l": c.l,
                                  "c": c.c, "vol": c.vol})
        if history:
            self._last_confirmed_ts = history[-1].ts
        self._warmed_bars = len(self._candles)

    async def stop(self, reason: str = "user_stop") -> dict:
        """平掉自管持仓 + 停 WS + 落 Run 终态（幂等：非 running 直接返回上次结果）。"""
        if self.state != "running":
            return self.flatten_result or {"ok": True, "closedSz": 0}
        self.state = "stopped"
        self.stop_reason = reason
        if self._ws is not None:
            await self._ws.stop()
        ref_px = float(self._candles[-1]["c"]) if self._candles else self.executor.entry_price
        ts = int(self._candles[-1]["ts"]) if self._candles else self.started_ts
        try:
            self.flatten_result = await self.executor.flatten(ref_px, ts)
        except Exception as exc:  # noqa: BLE001 —— 平仓失败如实记录，仍完成停止流程
            self.flatten_result = {"ok": False, "message": str(exc)}
        if self.flatten_result.get("trade"):
            await self._push({"type": "trade", "data": self.flatten_result["trade"]})
        await self._finalize_run(ts)
        await self._push({"type": "status", "data": self._status_payload()})
        return self.flatten_result

    # ---------- 核心：每根已确认 K 线驱动 ----------
    async def feed_candle(self, candle: Candle) -> None:
        """OKX WS 回调；也可被测试直接注入。未确认根仅推成形根供前端绘制。"""
        if self.state != "running":
            return
        if not candle.confirm:
            await self._push({"type": "candle", "data": {
                "ts": candle.ts, "o": candle.o, "h": candle.h, "l": candle.l,
                "c": candle.c, "vol": candle.vol, "confirm": False}})
            return
        async with self._lock:
            await self._on_confirmed(candle)

    async def _on_confirmed(self, candle: Candle) -> None:
        if self._is_duplicate(candle):
            return
        self._append_candle(candle)
        self._bar_index += 1
        i = len(self._candles) - 1
        await self._maybe_refresh_htf(int(candle.ts))
        df = self._build_frame()
        r = df.iloc[i]
        ref_px = float(r["c"])
        ts = int(r["ts"])

        events = await self._drive_signals(df, i, ref_px, ts)
        events += await self.executor.reconcile(ref_px, ts)
        for event in events:
            await self._push(event)
        await self._emit_equity(ref_px, ts)
        await self._push({"type": "candle", "data": {**self._candles[-1], "confirm": True}})
        await self._emit_position(ref_px)
        await self._maybe_session_exit(ref_px, ts)

    async def _drive_signals(self, df, i: int, ref_px: float, ts: int) -> list[dict]:
        """暖机阈值后取信号并经 executor 下真单；推 signal 诊断事件。"""
        if i < self.strategy.warmup():
            return []
        ctx = self.executor.context(i, ref_px)
        signals = self.strategy.on_candle(df, i, ctx)
        if not signals:
            return []
        for s in signals:
            await self._push({"type": "signal", "data": {
                "ts": ts, "side": s.side, "reason": s.reason}})
        return await self.executor.apply(signals, ref_px=ref_px, bar_index=i, ts=ts)

    async def _maybe_session_exit(self, ref_px: float, ts: int) -> None:
        """会话级金额阈值（§2.3-3）：累计盈亏达 +TP 或 ≤ −SL → 自动平仓 + 停会话。"""
        pnl = self.executor.session_pnl_usd
        reason = None
        if self.take_profit_usd is not None and pnl >= self.take_profit_usd:
            reason = "session_take_profit"
        elif self.stop_loss_usd is not None and pnl <= -self.stop_loss_usd:
            reason = "session_stop_loss"
        if reason is not None:
            await self.stop(reason)

    # ---------- 窗口 / 帧 ----------
    def _is_duplicate(self, candle: Candle) -> bool:
        return (self._last_confirmed_ts is not None and candle.ts <= self._last_confirmed_ts
                and bool(self._candles) and self._candles[-1]["ts"] == candle.ts)

    def _append_candle(self, candle: Candle) -> None:
        row = {"ts": candle.ts, "o": candle.o, "h": candle.h, "l": candle.l,
               "c": candle.c, "vol": candle.vol}
        if self._candles and self._candles[-1]["ts"] == candle.ts:
            self._candles[-1] = row
        else:
            self._candles.append(row)
        self._last_confirmed_ts = candle.ts

    def _build_frame(self):
        df = self.strategy.prepare(candles_to_df(list(self._candles)), self.bar)
        if self._htf_bars:
            frames = {b: candles_to_df(list(win))
                      for b, win in self._htf_windows.items() if win}
            if frames:
                df = self.strategy.attach_timeframes(df, self.bar, frames)
        return df

    async def _maybe_refresh_htf(self, base_ts: int) -> None:
        """实时段按高周期边界限频刷新窗口：仅当基础 ts 跨过高周期边界才取一次（对齐 paper）。"""
        for b in self._htf_bars:
            secs = BAR_SECONDS.get(b)
            if not secs:
                continue
            bucket = base_ts // secs
            if self._htf_last_bucket.get(b) == bucket:
                continue
            try:
                candles = await get_rest_client().get_candles(
                    self.inst_id, b, limit=min(self._window_max, self.strategy.htf_warmup_bars() + 5))
            except Exception:  # noqa: BLE001 —— 取数失败：窗口不更新（降级，不阻塞撮合）
                continue
            win = self._htf_windows[b]
            existing = {row["ts"] for row in win}
            for c in candles:
                if getattr(c, "confirm", True) and c.ts not in existing:
                    win.append({"ts": c.ts, "o": c.o, "h": c.h, "l": c.l, "c": c.c, "vol": c.vol})
            self._htf_last_bucket[b] = bucket

    async def _prefill_htf(self, base_span_bars: int) -> None:
        """启动时一次性预填各高周期窗口（取数失败不阻塞，降级空窗）。"""
        if not self._htf_bars:
            return
        base_seconds = BAR_SECONDS.get(self.bar, 60)
        for b in self._htf_bars:
            secs = BAR_SECONDS.get(b)
            if not secs:
                continue
            span_htf = (int(base_span_bars) * base_seconds) // secs + 1
            limit = min(self._window_max, max(self.strategy.htf_warmup_bars(), span_htf) + 5)
            try:
                candles = await get_rest_client().get_candles(self.inst_id, b, limit=limit)
            except Exception:  # noqa: BLE001 —— 取数失败：窗口留空，安全降级
                continue
            win = self._htf_windows[b]
            for c in candles:
                if getattr(c, "confirm", True):
                    win.append({"ts": c.ts, "o": c.o, "h": c.h, "l": c.l, "c": c.c, "vol": c.vol})
            if win:
                self._htf_last_bucket[b] = int(win[-1]["ts"]) // secs

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

    def _status_payload(self) -> dict:
        return {"state": self.state, "warmedBars": self._warmed_bars,
                "sessionPnlUsd": self.executor.session_pnl_usd,
                "positionSide": self.executor.projected,
                "budgetUsd": self.executor.budget_usd, "leverage": self.executor.leverage,
                "stopReason": self.stop_reason}

    def snapshot(self) -> dict:
        mark = float(self._candles[-1]["c"]) if self._candles else self.executor.entry_price
        return {"trades": list(self.executor.trades),
                "equity": self._equity[-_SNAPSHOT_EQUITY_MAX:],
                "position": self.executor.position_payload(mark),
                "status": self._status_payload()}

    def info(self) -> dict:
        return {"sessionId": self.session_id, "runId": self.run_id, "state": self.state,
                "strategyId": self.strategy_id, "strategyName": self.strategy_name,
                "instId": self.inst_id, "bar": self.bar,
                "budgetUsd": self.executor.budget_usd, "leverage": self.executor.leverage,
                "takeProfitUsd": self.take_profit_usd, "stopLossUsd": self.stop_loss_usd,
                "positionSide": self.executor.projected,
                "sessionPnlUsd": self.executor.session_pnl_usd, "startedTs": self.started_ts}

    # ---------- 持久化 ----------
    async def _finalize_run(self, end_ts: int) -> None:
        budget = self.executor.budget_usd
        realized = self.executor.realized_pnl_usd
        metrics = {**_empty_metrics(),
                   "totalReturn": (realized / budget) if budget > 0 else 0.0,
                   "tradeCount": len(self.executor.trades),
                   "avgPnl": realized / max(1, len(self.executor.trades))}
        sm = get_sessionmaker()
        async with sm() as s:
            await repo.finalize_run(s, self.run_id, status="stopped", metrics=metrics,
                                    end_ts=end_ts)
