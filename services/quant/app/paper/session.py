"""实时模拟会话 (spec §6.4 / §4.4)。

数据源为真实 OKX WS；仅在 confirm=1 的已确认 K 线上驱动 on_candle（避免重复信号）。
复用 SimBroker；维护滚动窗口 + 周期落库 + WS fan-out。
"""
from __future__ import annotations

import asyncio
from collections import deque

from ..config import get_settings
from ..db import repositories as repo
from ..db.session import get_sessionmaker
from ..engine.backtester import candles_to_df
from ..engine.simulator import SimBroker
from ..okx.rest_client import get_rest_client
from ..okx.types import Candle
from ..okx.ws_client import OKXWSClient
from ..strategies.base import BaseStrategy

_WINDOW_MAX = 600
_REPLAY_BASE_INTERVAL = 0.2   # 回放每根基准间隔（秒）；实际间隔 = BASE / speed (F5)


class PaperSession:
    """一个 (strategy, inst, bar) 的实时模拟会话。

    source="live"：订阅真实 OKX WS，仅在已确认 K 线上驱动撮合。
    source="replay"：拉取真实 OKX 历史 K 线，用可调倍速顺序驱动同一撮合管线，
    立即演示"模拟快速交易"（数据仍为真实历史，非合成，F5）。
    """

    def __init__(self, session_id: str, run_id: int, strategy: BaseStrategy,
                 inst_id: str, bar: str, initial_capital: float,
                 source: str = "live", speed: float = 1.0, replay_bars: int = 300):
        self.session_id = session_id
        self.run_id = run_id
        self.strategy = strategy
        self.inst_id = inst_id
        self.bar = bar
        self.state = "running"
        self.source = source
        self.speed = speed
        self.replay_bars = replay_bars

        settings = get_settings()
        self.broker = SimBroker(
            initial_capital,
            taker_fee_bps=settings.default_taker_fee_bps,
            slippage_bps=settings.default_slippage_bps,
            leverage=settings.default_leverage,
        )
        self._candles: deque[dict] = deque(maxlen=_WINDOW_MAX)
        self._last_confirmed_ts: int | None = None
        self._bar_index = -1
        self._trade_cursor = 0
        self._subscribers: set[asyncio.Queue] = set()
        self._ws: OKXWSClient | None = None
        self._replay_task: asyncio.Task | None = None
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
        """live：预热历史窗口后订阅实时 WS；replay：后台任务加速回放真实历史。"""
        if self.source == "replay":
            await self._push({"type": "status", "data": {"state": "running"}})
            self._replay_task = asyncio.create_task(self._run_replay())
            return

        warmup = max(self.strategy.warmup() + 5, 60)
        try:
            history = await get_rest_client().get_candles(self.inst_id, self.bar, limit=warmup)
            for c in history:
                self._candles.append({"ts": c.ts, "o": c.o, "h": c.h, "l": c.l, "c": c.c, "vol": c.vol})
            if history:
                self._last_confirmed_ts = history[-1].ts
        except Exception:  # noqa: BLE001 —— 预热失败不阻塞，等实时 K 线
            pass
        self._ws = OKXWSClient(self.inst_id, self.bar, self.feed_candle)
        self._ws.start()
        await self._push({"type": "status", "data": {"state": "running"}})

    async def _run_replay(self) -> None:
        """拉真实历史 K 线，按可调倍速顺序经 feed_candle 驱动撮合，跑完自动结算 (F5/C3)。"""
        interval = max(0.0, _REPLAY_BASE_INTERVAL / self.speed)
        try:
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
        await self._push({"type": "status", "data": {"state": "stopped"}})

    # ---------- 核心：每根已确认 K 线驱动 ----------
    async def feed_candle(self, candle: Candle) -> None:
        """OKX WS 回调；也可被测试直接注入（acceptance #6）。"""
        if self.state != "running":
            return
        # 未确认 K 线：仅推送给前端绘制实时蜡烛，不驱动撮合
        if not candle.confirm:
            await self._push({"type": "candle", "data": {
                "ts": candle.ts, "o": candle.o, "h": candle.h, "l": candle.l,
                "c": candle.c, "vol": candle.vol, "confirm": False}})
            return
        async with self._lock:
            await self._on_confirmed(candle)

    async def _on_confirmed(self, candle: Candle) -> None:
        # 去重：同一根确认 K 线只处理一次
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
        r = df.iloc[i]
        risk = self.strategy.risk_params()

        # SimBroker 四阶段
        self.broker.fill_pending(open_price=float(r["o"]), ts=int(r["ts"]), bar_index=i)
        self.broker.apply_risk(i, r, risk)
        self.broker.mark(ts=int(r["ts"]), close=float(r["c"]))

        await self._flush_new_trades()
        eq = self.broker.equity_points[-1]
        await self._persist_equity(eq)
        await self._push({"type": "equity", "data": equity_out_record(eq)})
        await self._push({"type": "candle", "data": {**row, "confirm": True}})

        if i >= self.strategy.warmup():
            ctx = self.broker.context()
            signals = self.strategy.on_candle(df, i, ctx)
            if signals:
                self.broker.submit(signals)
                for s in signals:
                    await self._push({"type": "signal", "data": {
                        "ts": int(r["ts"]), "side": s.side, "reason": s.reason}})

    async def _flush_new_trades(self) -> None:
        trades = self.broker.trades
        while self._trade_cursor < len(trades):
            t = trades[self._trade_cursor]
            self._trade_cursor += 1
            await self._persist_trade(t)
            await self._push({"type": "trade", "data": _trade_record_out(t)})

    # ---------- 持久化 ----------
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


def _trade_record_out(t) -> dict:
    return {"ts": t.ts, "side": t.side, "price": t.price, "qty": t.qty, "fee": t.fee,
            "pnl": t.pnl, "positionAfter": t.position_after, "reason": t.reason}
