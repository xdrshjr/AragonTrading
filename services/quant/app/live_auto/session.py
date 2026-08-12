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
import logging
import time
from collections import deque

from ..db import repositories as repo
from ..db.session import get_sessionmaker
from ..engine.backtester import candles_to_df
from ..engine.metrics import _empty_metrics
from ..okx.rest_client import get_rest_client
from ..okx.types import BAR_SECONDS, Candle
from ..okx.session_link import make_session_link
from ..okx.ws_client import OKXWSClient
from ..strategies.base import BaseStrategy
from ..paper.session import PAPER_WINDOW_MAX
from .executor import LiveExecutor

_SNAPSHOT_EQUITY_MAX = 1000   # snapshot 回放净值上限（订阅即重放末 N 条）

logger = logging.getLogger(__name__)


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
        self.flatten_failed = False           # H4：停止平仓失败标志（诚实上报，不假报干净）
        self.residual: dict | None = None     # H4：平仓失败残留仓 {side,size}
        self._monitor_task: asyncio.Task | None = None  # H1：独立盯市循环任务
        self._last_candle_wall = 0.0          # H3/R-5：最近确认根墙钟（start 订阅 WS 时初始化）
        self._stale_degraded = False          # H3：行情停滞降级态（限频推送，避免刷屏）
        self._degraded_seen = False           # H4：会话期是否曾降级（落 Run metrics）
        # A1：上次已随 status 事件推送的持仓方向。开/平/反手/外部平仓改变 executor.projected 后，
        # 据此补推一条带 positionSide 的全量 status，使前端持仓真源刷新，修复持仓恒显「空仓」。
        self._last_status_side = executor.projected

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
        # R-3：净值缓冲有界（盯市循环秒级追加，maxlen 自动裁顶，杜绝多日实盘进程内存泄漏）。
        self._equity: deque[dict] = deque(maxlen=_SNAPSHOT_EQUITY_MAX)
        self._peak_equity = executor.budget_usd
        self._subscribers: set[asyncio.Queue] = set()
        self._ws: OKXWSClient | None = None
        self._lock = asyncio.Lock()

    @property
    def poll_interval_s(self) -> int:
        """盯市循环基准间隔（秒）；与 reconcile / 看门狗共用，源自会话级 settings。"""
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

    # ---------- 事件分发 + 可持久审计（auto-trade-explainability §2.5）----------
    async def _dispatch(self, events: list[dict]) -> None:
        """统一分发 executor 事件：trade 事件先注入 explain.meta + best-effort 落库，再 WS 扇出。

        三处原内联 `_push`（`_on_confirmed` / `_monitor_tick` / `_handle_flatten_result`）统一改走本方法，
        使每笔成交（含 v2 新增的 external_close）都带可追溯元信息并持久化到 `trades.explain_json`。
        """
        for event in events:
            if event.get("type") == "trade" and isinstance(event.get("data"), dict):
                trade = event["data"]
                self._inject_explain_meta(trade)
                await self._persist_trade(trade)
            await self._push(event)

    def _inject_explain_meta(self, trade: dict) -> None:
        """给 trade.explain 注入会话级可追溯元信息（strategyName/kind/instId/bar/barTs + best-effort 单号）。"""
        explain = trade.get("explain")
        if not isinstance(explain, dict):
            return
        meta = dict(explain.get("meta") or {})
        meta.setdefault("strategyName", self.strategy_name)
        meta.setdefault("strategyKind", self.strategy_kind)
        meta.setdefault("instId", self.inst_id)
        meta.setdefault("bar", self.bar)
        meta.setdefault("barTs", trade.get("ts"))
        if trade.get("clOrdId"):
            meta.setdefault("clOrdId", trade["clOrdId"])
        if trade.get("ordId"):
            meta.setdefault("ordId", trade["ordId"])
        explain["meta"] = meta

    async def _persist_trade(self, trade: dict) -> None:
        """落 trades 行 + explain_json（短生命周期 session，镜像 append_trade 自带 commit）。

        best-effort：落库失败仅 `logger.warning`，**绝不**因审计落库中断真实交易 / 平仓（H4 诚实降级）。
        """
        try:
            sm = get_sessionmaker()
            async with sm() as s:
                await repo.append_live_trade(s, self.run_id, trade)
        except Exception as exc:  # noqa: BLE001 —— best-effort 审计落库，失败不阻塞热路径
            logger.warning("append_live_trade failed (run_id=%s): %s", self.run_id, exc)

    # ---------- lifecycle ----------
    async def start(self) -> None:
        """暖机窗口（仅热指标，不下单）后订阅实时 WS。"""
        await self._prefill_htf(self.priming_bars if self.priming else 60)
        await self._prime_window()
        # 链路事件与断线补根的实体全在 okx/session_link.py（本文件只留绑定，见 spec §3.2 注）。
        self._link = make_session_link(self, "live_auto")
        self._ws = OKXWSClient(self.inst_id, self.bar, self.feed_candle,
                               on_stream=self._link.on_stream, owner=f"live:{self.session_id}")
        self._ws.start()
        # R-5：订阅 WS 即以墙钟初始化看门狗基准，避免首根实时确认根到来前误判停滞。
        self._last_candle_wall = time.time()
        # H1：独立盯市循环（reconcile + 盯市 + 会话止盈止损 + 推送），与收线信号驱动解耦。
        self._monitor_task = asyncio.create_task(self._monitor_loop())
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
        self._cancel_monitor_task()
        if self._ws is not None:
            await self._ws.stop()
        ref_px = float(self._candles[-1]["c"]) if self._candles else self.executor.entry_price
        ts = int(self._candles[-1]["ts"]) if self._candles else self.started_ts
        try:
            self.flatten_result = await self.executor.flatten(ref_px, ts)
        except Exception as exc:  # noqa: BLE001 —— 平仓失败如实记录，仍完成停止流程
            self.flatten_result = {"ok": False, "message": str(exc)}
        await self._handle_flatten_result(ts)
        await self._finalize_run(ts)
        await self._push({"type": "status", "data": self._status_payload()})
        return self.flatten_result

    def _cancel_monitor_task(self) -> None:
        """取消盯市任务，但**绝不取消自身**（R-2，P0）。

        会话金额止损正是在盯市循环内经 `_maybe_session_exit → stop()` 触发，此刻盯市任务即当前
        任务；若直接 `cancel()` 等于取消自身，`CancelledError` 会在随后 `flatten` 的 await 抛出，
        中断真实平仓并跳过 `_finalize_run`，重演 H4「停止却留下无人管理真实仓」漏洞。循环内触发
        的停止靠 `_monitor_loop` 的 `while self.state=="running"` 守卫自然退出，无需自取消。
        """
        task = self._monitor_task
        if task is not None and task is not asyncio.current_task():
            task.cancel()
        self._monitor_task = None

    async def _handle_flatten_result(self, ts: int) -> None:
        """据 flatten 结果诚实置终态（H4）：成功推成交；失败置 flattenFailed + residual 并推 error。"""
        result = self.flatten_result or {}
        if result.get("trade"):
            await self._dispatch([{"type": "trade", "data": result["trade"]}])
        if result.get("ok"):
            return
        self.flatten_failed = True
        self.residual = result.get("residual")
        await self._push({"type": "error", "data": {
            "ts": ts, "code": "LIVE_AUTO_FLATTEN_FAILED",
            "message": "自动平仓失败，请立即到下方实盘面板手动平仓", "residual": self.residual}})

    # ---------- H1：独立盯市循环（reconcile / 盯市 / 会话止盈止损 / 推送）----------
    async def _monitor_loop(self) -> None:
        """按 `poll_interval_s` 独立轮询；不驱动策略信号（信号唯一驱动源仍是确认根）。

        循环内触发的 `stop`（会话金额止损 / 停滞自动平仓）靠 `while self.state=="running"` 自然
        退出（R-2，绝不自取消）；`CancelledError`（外部停止取消本任务）安静吞掉。
        """
        try:
            while self.state == "running":
                await asyncio.sleep(self.poll_interval_s)
                async with self._lock:
                    if self.state != "running":
                        break
                    await self._monitor_tick()
        except asyncio.CancelledError:
            pass

    async def _monitor_tick(self) -> None:
        """单轮盯市（持 `self._lock`，与 `_on_confirmed` 互斥，杜绝并发改 projected）。"""
        now_ts = int(time.time())
        mark = await self._latest_mark_px()
        await self._dispatch(await self.executor.reconcile(mark, now_ts))
        await self._push_status_if_side_changed()
        await self._emit_equity(mark, now_ts)
        await self._emit_position(mark)
        await self._check_staleness(now_ts)
        self._degraded_seen = (self._degraded_seen or self.executor.degraded
                               or self._stale_degraded)
        await self._maybe_session_exit(mark, now_ts)

    async def _latest_mark_px(self) -> float:
        """盯市标记价：优先最近确认根收盘；缺则 REST get_ticker。

        R-4：`get_ticker` 无行情时**返回 None 而非抛错**，故对 None 与异常一并回落自管 entry_price，
        绝不把 None 标记价传入 reconcile / 净值数学。
        """
        if self._candles:
            return float(self._candles[-1]["c"])
        try:
            px = await get_rest_client().get_ticker(self.inst_id)
        except Exception:  # noqa: BLE001 —— 取价失败回落 entry_price（降级，不阻塞盯市）
            px = None
        return float(px) if px is not None else self.executor.entry_price

    async def _check_staleness(self, now_ts: int) -> None:
        """行情停滞看门狗（H3/R-5）：墙钟比对最近确认根，超阈推降级 status（默认仅告警不平仓）。"""
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
                "message": "行情连接异常，自动交易暂停响应 K 线（交易所侧止盈止损仍生效）"}})

    async def _clear_stale_if_recovered(self) -> None:
        """行情恢复（墙钟回到阈值内）：消停滞降级态并推一条「已恢复」status。"""
        if not self._stale_degraded:
            return
        self._stale_degraded = False
        await self._push({"type": "status", "data": {
            **self._status_payload(), "message": "行情已恢复，自动交易盯市恢复正常"}})

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
        await self._dispatch(events)
        await self._push_status_if_side_changed()
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
        self._last_candle_wall = time.time()   # H3：刷新看门狗墙钟（确认根到达即心跳）

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

    async def _push_status_if_side_changed(self) -> None:
        """持仓方向（executor.projected）较上次推送变化时补推一条全量 status（A1）。

        `status.positionSide` 是持仓单一真源（经 reconcile 用真实 OKX 持仓校正），但正常开/平/反手
        路径只推 trade / position、不刷新 status，导致前端 positionSide 冻结在连接时的 flat、持仓恒显
        「空仓」。在持锁的 `_on_confirmed` / `_monitor_tick` 派发 executor 事件**之后**调用本方法：
        方向变化即推全量 `_status_payload()`（含 positionSide），覆盖同轮可能出现的轻量 status；
        方向不变则不推，避免冗余 WS 流量。读写 `_last_status_side` 在持锁路径内串行，无并发风险。
        """
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
                "stopReason": self.stop_reason,
                "degraded": self.executor.degraded or self._stale_degraded,
                "flattenFailed": self.flatten_failed, "residual": self.residual}

    def snapshot(self) -> dict:
        mark = float(self._candles[-1]["c"]) if self._candles else self.executor.entry_price
        return {"trades": list(self.executor.trades),
                "equity": list(self._equity),   # R-3：deque 不支持切片，maxlen 已自动裁顶
                "position": self.executor.position_payload(mark),
                "status": self._status_payload()}

    def info(self) -> dict:
        return {"sessionId": self.session_id, "runId": self.run_id, "state": self.state,
                "strategyId": self.strategy_id, "strategyName": self.strategy_name,
                "instId": self.inst_id, "bar": self.bar,
                "budgetUsd": self.executor.budget_usd, "leverage": self.executor.leverage,
                "takeProfitUsd": self.take_profit_usd, "stopLossUsd": self.stop_loss_usd,
                "positionSide": self.executor.projected,
                "sessionPnlUsd": self.executor.session_pnl_usd, "startedTs": self.started_ts,
                "degraded": self.executor.degraded or self._stale_degraded,
                "flattenFailed": self.flatten_failed}

    # ---------- 持久化 ----------
    async def _finalize_run(self, end_ts: int) -> None:
        budget = self.executor.budget_usd
        realized = self.executor.realized_pnl_usd
        metrics = {**_empty_metrics(),
                   "totalReturn": (realized / budget) if budget > 0 else 0.0,
                   "tradeCount": len(self.executor.trades),
                   "avgPnl": realized / max(1, len(self.executor.trades)),
                   "flattenFailed": self.flatten_failed,   # H4：平仓失败留痕
                   "degradedSeen": self._degraded_seen}     # H2/H3：会话期是否曾降级
        sm = get_sessionmaker()
        async with sm() as s:
            await repo.finalize_run(s, self.run_id, status="stopped", metrics=metrics,
                                    end_ts=end_ts)
