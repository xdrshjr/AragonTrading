"""实时模拟会话 (spec §6.4 / §4.4)。

数据源为真实 OKX WS；仅在 confirm=1 的已确认 K 线上驱动 on_candle（避免重复信号）。
复用 SimBroker；维护滚动窗口 + 周期落库 + WS fan-out。
"""
from __future__ import annotations

import asyncio
import time
from collections import deque

from ..config import get_settings
from ..db import repositories as repo
from ..db.session import get_sessionmaker
from ..engine.backtester import candles_to_df
from ..engine.simulator import SimBroker
from ..okx.rest_client import get_rest_client
from ..okx.types import BAR_SECONDS, Candle
from ..okx.ws_client import OKXWSClient
from ..strategies.base import BaseStrategy

_WINDOW_MAX = 600
_REPLAY_BASE_INTERVAL = 0.2   # 回放每根基准间隔（秒）；实际间隔 = BASE / speed (F5)
_DEFAULT_PRIMING_BARS = 300   # 预热回放回看根数默认值（修复 P1-2：低频策略也有 seeded 动态）
_SNAPSHOT_EQUITY_MAX = 1000   # snapshot 回放净值上限（订阅即重放末 N 条，R7）
_INTRABAR_MIN_INTERVAL = 1.0  # 瞬时盯市两次推送的最小间隔（秒），节流防洪泛 (R3)
_INTRABAR_PRICE_EPS = 1e-4    # 瞬时盯市价格相对变动阈值（1bp），低于此且未到间隔则跳过


class PaperSession:
    """一个 (strategy, inst, bar) 的实时模拟会话。

    source="live"：订阅真实 OKX WS，仅在已确认 K 线上驱动撮合。
    source="replay"：拉取真实 OKX 历史 K 线，用可调倍速顺序驱动同一撮合管线，
    立即演示"模拟快速交易"（数据仍为真实历史，非合成，F5）。
    """

    def __init__(self, session_id: str, run_id: int, strategy: BaseStrategy,
                 inst_id: str, bar: str, initial_capital: float,
                 source: str = "live", speed: float = 1.0, replay_bars: int = 300,
                 priming: bool = True, priming_bars: int = _DEFAULT_PRIMING_BARS):
        self.session_id = session_id
        self.run_id = run_id
        self.strategy = strategy
        self.inst_id = inst_id
        self.bar = bar
        self.state = "running"
        self.source = source
        self.speed = speed
        self.replay_bars = replay_bars
        # 预热回放（warm-start priming，B1）：live 启动时对近端真实历史做一次因果微回测，
        # 立即建立「当前持仓 / 成交 / 净值」(seeded)，使「点击即见动态」。
        self.priming = priming
        self.priming_bars = priming_bars

        settings = get_settings()
        self.broker = SimBroker(
            initial_capital,
            taker_fee_bps=settings.default_taker_fee_bps,
            slippage_bps=settings.default_slippage_bps,
            leverage=settings.default_leverage,
        )
        self._candles: deque[dict] = deque(maxlen=_WINDOW_MAX)
        # 多周期（MTF §3.6/§6.4）：仅当策略声明了【严格高于基础周期】的高周期时才有开销。
        base_seconds = BAR_SECONDS.get(bar, 60)
        self._htf_bars: list[str] = [tf for tf in strategy.timeframes()
                                     if BAR_SECONDS.get(tf, 0) > base_seconds]
        self._htf_windows: dict[str, deque] = {
            b: deque(maxlen=_WINDOW_MAX) for b in self._htf_bars}
        self._htf_last_bucket: dict[str, int] = {}   # 各高周期上次刷新所在桶（跨边界才刷新）
        self._htf_warmup = strategy.htf_warmup_bars()
        self._last_confirmed_ts: int | None = None
        self._bar_index = -1
        self._trade_cursor = 0
        self._subscribers: set[asyncio.Queue] = set()
        self._ws: OKXWSClient | None = None
        self._replay_task: asyncio.Task | None = None
        self._lock = asyncio.Lock()
        # 预热边界标注（诚实硬规则）：seeded 成交数与预热根数；snapshot/status 据此区分预热段。
        self._seeded_trade_count = 0
        self._warmed_bars = 0
        # 瞬时盯市节流游标（intrabar transient equity）。
        self._last_intrabar_mono = 0.0
        self._last_intrabar_price: float | None = None

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
        """live：预热历史窗口（priming 回放或纯预填）后订阅实时 WS；replay：加速回放真实历史。"""
        if self.source == "replay":
            await self._push({"type": "status", "data": self._status_payload()})
            self._replay_task = asyncio.create_task(self._run_replay())
            return

        # 高周期窗口须在预热回放前就位，使早段 seeded 基础根也能因果对齐（§6.4-1）。
        await self._prefill_htf(self.priming_bars if self.priming else 60)
        if self.priming:
            await self._prime_history()
        else:
            await self._load_warmup_window()
        self._ws = OKXWSClient(self.inst_id, self.bar, self.feed_candle)
        self._ws.start()
        await self._push({"type": "status", "data": self._status_payload()})

    async def _prime_history(self) -> None:
        """从空窗逐根回放近端真实历史，建立 seeded 持仓/成交/净值（修复 P1-1）。

        关键：不整窗预填后回放，而是每次仅 append 一根并立即处理，使 `i=len-1` 恰为当根、
        `prepare` 在增量增长的窗口上算指标，等价于对近端真实历史做一次因果微回测。
        回看根数夹取到 `[warmup+5, _WINDOW_MAX]`（修复 P1-2：低频策略也有现实概率体现持仓）。
        """
        bars = max(self.strategy.warmup() + 5, min(self.priming_bars, _WINDOW_MAX))
        try:
            history = await get_rest_client().get_candles(self.inst_id, self.bar, limit=bars)
            for c in history:
                if self.state != "running":
                    break
                await self._process_confirmed(
                    Candle(ts=c.ts, o=c.o, h=c.h, l=c.l, c=c.c, vol=c.vol, confirm=True),
                    seeded=True, emit_candle=False)
        except Exception:  # noqa: BLE001 —— 预热失败不阻塞，等实时 K 线
            pass
        # 预热结束：记录 seeded 边界，回写 run.config 显式标注预热段（诚实硬规则，§7.3）。
        self._seeded_trade_count = len(self.broker.trades)
        self._warmed_bars = len(self._candles)
        await self._persist_seeded_meta()

    async def _load_warmup_window(self) -> None:
        """priming=False：沿用 v1 既有行为——整窗预填窗口但不回放（仅供指标暖机）。"""
        warmup = max(self.strategy.warmup() + 5, 60)
        try:
            history = await get_rest_client().get_candles(self.inst_id, self.bar, limit=warmup)
            for c in history:
                self._candles.append({"ts": c.ts, "o": c.o, "h": c.h, "l": c.l, "c": c.c, "vol": c.vol})
            if history:
                self._last_confirmed_ts = history[-1].ts
        except Exception:  # noqa: BLE001 —— 预热失败不阻塞，等实时 K 线
            pass

    # ---------- 多周期（MTF）高周期窗口（§6.4）----------
    async def _prefill_htf(self, base_span_bars: int) -> None:
        """启动时一次性预填各高周期窗口，覆盖 base_span_bars 根基础区间 + 暖机（§6.4-1）。

        取数失败不阻塞（降级为空窗 → 后续 `attach_timeframes` 走子类缺失回退）。
        """
        if not self._htf_bars:
            return
        base_seconds = BAR_SECONDS.get(self.bar, 60)
        for b in self._htf_bars:
            secs = BAR_SECONDS.get(b)
            if not secs:
                continue
            span_htf = (int(base_span_bars) * base_seconds) // secs + 1
            limit = min(_WINDOW_MAX, max(self._htf_warmup, span_htf) + 5)
            try:
                candles = await get_rest_client().get_candles(self.inst_id, b, limit=limit)
            except Exception:  # noqa: BLE001 —— 取数失败：窗口留空，安全降级（绝不假数据）
                continue
            win = self._htf_windows[b]
            for c in candles:
                if getattr(c, "confirm", True):
                    win.append({"ts": c.ts, "o": c.o, "h": c.h, "l": c.l,
                                "c": c.c, "vol": c.vol})
            if win:
                self._htf_last_bucket[b] = int(win[-1]["ts"]) // secs

    async def _maybe_refresh_htf(self, base_ts: int) -> None:
        """实时段按高周期边界限频刷新窗口：仅当基础 ts 跨过高周期边界才取一次（§6.4-2）。

        未跨边界复用窗口（避免每根都打 OKX，限频 R1/R3）；取数失败吞为窗口不更新。
        """
        for b in self._htf_bars:
            secs = BAR_SECONDS.get(b)
            if not secs:
                continue
            bucket = base_ts // secs
            if self._htf_last_bucket.get(b) == bucket:
                continue   # 未跨边界：复用窗口
            try:
                candles = await get_rest_client().get_candles(
                    self.inst_id, b, limit=self._htf_warmup + 5)
            except Exception:  # noqa: BLE001 —— 取数失败：窗口不更新（降级，不阻塞撮合）
                continue
            win = self._htf_windows[b]
            existing = {row["ts"] for row in win}
            for c in candles:
                if getattr(c, "confirm", True) and c.ts not in existing:
                    win.append({"ts": c.ts, "o": c.o, "h": c.h, "l": c.l,
                                "c": c.c, "vol": c.vol})
            self._htf_last_bucket[b] = bucket

    async def _run_replay(self) -> None:
        """拉真实历史 K 线，按可调倍速顺序经 feed_candle 驱动撮合，跑完自动结算 (F5/C3)。"""
        interval = max(0.0, _REPLAY_BASE_INTERVAL / self.speed)
        try:
            # 回放是固定历史区间：高周期窗口一次性预填即可，逐根回放不再刷新（§6.4-1/4）。
            await self._prefill_htf(self.replay_bars)
            history = await get_rest_client().get_candles(
                self.inst_id, self.bar, limit=self.replay_bars)
            for c in history:
                if self.state != "running":
                    break
                # 强制 confirm=True：每根历史 K 线都驱动撮合（演示"快速交易"）
                await self.feed_candle(Candle(ts=c.ts, o=c.o, h=c.h, l=c.l, c=c.c,
                                              vol=c.vol, confirm=True))
                await asyncio.sleep(interval)
        except asyncio.CancelledError:
            raise   # 被 stop() 取消：交由 finally 结算
        except Exception:  # noqa: BLE001 —— 取数失败等，仍进入 finally 结算
            pass
        finally:
            await self.stop()   # 正常跑完或被取消都自动结算（stop 幂等，不会重复结算）

    async def stop(self) -> None:
        # 幂等：非 running 直接返回，避免回放自动 stop 与手动 /stop、shutdown 重复结算 (C3)
        if self.state != "running":
            return
        self.state = "stopped"
        # 取消回放任务并等待（除非由回放任务自身触发 stop，避免自我 await 死锁，C3）
        task = self._replay_task
        if task is not None and not task.done() and task is not asyncio.current_task():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        if self._ws is not None:
            await self._ws.stop()
        # 结算：仍持仓按最新收盘平
        if self._candles:
            last = self._candles[-1]
            self.broker.finalize(ts=int(last["ts"]), close=float(last["c"]))
        await self._persist_final()
        await self._push({"type": "status", "data": self._status_payload()})

    # ---------- 核心：每根已确认 K 线驱动 ----------
    async def feed_candle(self, candle: Candle) -> None:
        """OKX WS 回调；也可被测试直接注入（acceptance #6）。"""
        if self.state != "running":
            return
        # 未确认 K 线：推送成形根供前端绘制 + 节流瞬时盯市（净值/浮盈逐笔跳动，B2）
        if not candle.confirm:
            await self._push({"type": "candle", "data": {
                "ts": candle.ts, "o": candle.o, "h": candle.h, "l": candle.l,
                "c": candle.c, "vol": candle.vol, "confirm": False}})
            await self._maybe_push_intrabar(candle)
            return
        async with self._lock:
            await self._on_confirmed(candle)

    async def _on_confirmed(self, candle: Candle) -> None:
        """实时段：驱动一根已确认 K 线（seeded=False，照常推 candle）。"""
        await self._process_confirmed(candle, seeded=False, emit_candle=True)

    async def _process_confirmed(self, candle: Candle, *, seeded: bool, emit_candle: bool) -> None:
        """驱动一根已确认 K 线的撮合管线（预热回放与实时段共用，B1）。

        seeded=True 为预热回放（warm-start）；其成交/逐根净值照常落库（真实因果撮合），
        但不逐根推 candle（emit_candle=False，前端已由 REST 历史绘出）。
        """
        # 去重：同一根确认 K 线只处理一次（衔接预热末根与首个 live 根，R6）
        if self._last_confirmed_ts is not None and candle.ts <= self._last_confirmed_ts \
                and self._candles and self._candles[-1]["ts"] == candle.ts:
            return
        row = {"ts": candle.ts, "o": candle.o, "h": candle.h, "l": candle.l,
               "c": candle.c, "vol": candle.vol}
        if self._candles and self._candles[-1]["ts"] == candle.ts:
            self._candles[-1] = row
        else:
            self._candles.append(row)
        self._last_confirmed_ts = candle.ts
        self._bar_index += 1
        i = len(self._candles) - 1

        df = self.strategy.prepare(candles_to_df(list(self._candles)), self.bar)
        if self._htf_bars:
            # 【P1-3】按需 REST 刷新仅限实时段(source=="live" 且非 seeded)；
            # replay/priming 的历史区间已在启动时一次性预填，绝不逐根刷新（限频 R1/R3）。
            if self.source == "live" and not seeded:
                await self._maybe_refresh_htf(int(candle.ts))
            frames = {b: candles_to_df(list(win))
                      for b, win in self._htf_windows.items() if win}
            if frames:
                df = self.strategy.attach_timeframes(df, self.bar, frames)
        r = df.iloc[i]
        risk = self.strategy.risk_params()

        # SimBroker 四阶段
        self.broker.fill_pending(open_price=float(r["o"]), ts=int(r["ts"]), bar_index=i)
        self.broker.apply_risk(i, r, risk)
        self.broker.mark(ts=int(r["ts"]), close=float(r["c"]))

        await self._flush_new_trades(seeded=seeded)
        eq = self.broker.equity_points[-1]
        await self._persist_equity(eq)
        await self._push({"type": "equity", "data": equity_out_record(eq)})
        if emit_candle:
            await self._push({"type": "candle", "data": {**row, "confirm": True}})

        if i >= self.strategy.warmup():
            ctx = self.broker.context()
            signals = self.strategy.on_candle(df, i, ctx)
            if signals:
                self.broker.submit(signals)
                for s in signals:
                    await self._push({"type": "signal", "data": {
                        "ts": int(r["ts"]), "side": s.side, "reason": s.reason}})

    async def _maybe_push_intrabar(self, candle: Candle) -> None:
        """未确认 tick：节流推送瞬时盯市 equity(transient) 与可选 position。

        瞬时净值**不落库、不计指标**（前端按 ts upsert 仅用于绘动，P1-3/§7.3）。
        节流：价格相对变动 ≥ 1bp 或距上次推送 ≥ 1s 才推，避免洪泛（R3）。
        """
        price = float(candle.c)
        last = self._last_intrabar_price
        moved = last is None or abs(price - last) >= max(abs(last), 1.0) * _INTRABAR_PRICE_EPS
        elapsed = (time.monotonic() - self._last_intrabar_mono) >= _INTRABAR_MIN_INTERVAL
        if not (moved or elapsed):
            return
        self._last_intrabar_mono = time.monotonic()
        self._last_intrabar_price = price
        eq = self.broker.equity_at(price)
        dd = min(eq / self.broker.peak_equity - 1.0, 0.0) if self.broker.peak_equity > 0 else 0.0
        await self._push({"type": "equity", "data": {
            "ts": candle.ts, "equity": eq, "drawdown": dd, "transient": True}})
        pos = self._position_payload(price)
        if pos is not None:
            await self._push({"type": "position", "data": pos})

    async def _flush_new_trades(self, *, seeded: bool = False) -> None:
        trades = self.broker.trades
        while self._trade_cursor < len(trades):
            t = trades[self._trade_cursor]
            self._trade_cursor += 1
            await self._persist_trade(t)
            await self._push({"type": "trade", "data": _trade_record_out(t, seeded=seeded)})

    # ---------- 快照 / 状态（订阅即重放，B3）----------
    def _status_payload(self) -> dict:
        """订阅即发的状态，显式标注预热边界（warmedBars/seededTrades，诚实硬规则）。"""
        return {"state": self.state, "warmedBars": self._warmed_bars,
                "seededTrades": self._seeded_trade_count,
                "initialCapital": self.broker.initial_capital}

    def _position_payload(self, mark: float | None = None) -> dict | None:
        """当前持仓便捷读数（可选，P2-3）；权威视图仍由前端 deriveOpenPosition 推导。"""
        pos = self.broker.pos
        if pos.side == "flat":
            return None
        if mark is None:
            mark = float(self._candles[-1]["c"]) if self._candles else pos.entry_price
        unrealized = self.broker.equity_at(mark) - self.broker.cash
        return {"side": pos.side, "size": pos.qty, "avgEntry": pos.entry_price,
                "markPrice": mark, "unrealizedPnl": unrealized}

    def snapshot(self) -> dict:
        """全部成交 + 末 N 条净值 + 当前持仓 + 状态，供 ws 订阅/重连即重放（R7）。

        seeded 标记：索引 `< _seeded_trade_count` 的成交为预热回填（§6.2 / P2-5）。
        """
        n = self._seeded_trade_count
        trades = [_trade_record_out(t, seeded=(idx < n))
                  for idx, t in enumerate(self.broker.trades)]
        equity = [equity_out_record(e) for e in self.broker.equity_points[-_SNAPSHOT_EQUITY_MAX:]]
        return {"trades": trades, "equity": equity,
                "position": self._position_payload(), "status": self._status_payload()}

    # ---------- 持久化 ----------
    async def _persist_seeded_meta(self) -> None:
        """priming 结束后把 warmedBars/seededTrades 写回 run.config，显式标注预热边界（§7.3）。"""
        sm = get_sessionmaker()
        async with sm() as s:
            run = await repo.get_run(s, self.run_id)
            if run is None:
                return
            import json
            cfg = json.loads(run.config_json or "{}")
            cfg["seededTrades"] = self._seeded_trade_count
            cfg["warmedBars"] = self._warmed_bars
            run.config_json = json.dumps(cfg, ensure_ascii=False)
            await s.commit()

    async def _persist_trade(self, t) -> None:
        sm = get_sessionmaker()
        async with sm() as s:
            await repo.append_trade(s, self.run_id, t)

    async def _persist_equity(self, e) -> None:
        sm = get_sessionmaker()
        async with sm() as s:
            await repo.append_equity(s, self.run_id, e)

    async def _persist_final(self) -> None:
        from ..engine.metrics import compute_metrics
        metrics = compute_metrics(self.broker.equity_points, self.broker.trades, self.bar,
                                  self.broker.initial_capital)
        sm = get_sessionmaker()
        async with sm() as s:
            run = await repo.get_run(s, self.run_id)
            if run is not None:
                import json
                run.status = "stopped"
                run.metrics_json = json.dumps(metrics, ensure_ascii=False)
                if self._candles:
                    run.end_ts = int(self._candles[-1]["ts"])
                await s.commit()

    def info(self) -> dict:
        last_eq = self.broker.equity_points[-1].equity if self.broker.equity_points else self.broker.cash
        return {
            "sessionId": self.session_id, "runId": self.run_id, "instId": self.inst_id,
            "bar": self.bar, "state": self.state, "equity": last_eq,
            "positionSide": self.broker.pos.side,
        }


# ---- 小工具：dataclass -> dict（避免 schemas 循环导入）----
def equity_out_record(e) -> dict:
    return {"ts": e.ts, "equity": e.equity, "drawdown": e.drawdown}


def _trade_record_out(t, seeded: bool = False) -> dict:
    return {"ts": t.ts, "side": t.side, "price": t.price, "qty": t.qty, "fee": t.fee,
            "pnl": t.pnl, "positionAfter": t.position_after, "reason": t.reason,
            "seeded": seeded}
