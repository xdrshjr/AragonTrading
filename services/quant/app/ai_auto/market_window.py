"""AiTradeSession 的行情窗口 / 多周期 / 逐策略建帧助手（P2-4：从会话拆分，控 ≤800 行/文件）。

镜像 `LiveAutoSession` / `PaperSession` 的滚动窗口 + 高周期（MTF）窗口管理，但服务**多策略**：
`_window_max` 与高周期集合取所有选中策略的**并集**上界；`build_strategy_frames` 为每个策略各自
`prepare`(+`attach_timeframes`) 出一份帧（不同策略指标列不同，绝不交叉污染）。窗口只承载 K 线，
不驱动任何 broker / 决策（回放与决策由会话协调）。
"""
from __future__ import annotations

from collections import deque

import pandas as pd

from ..engine.backtester import candles_to_df
from ..okx.rest_client import get_rest_client
from ..okx.types import BAR_SECONDS, Candle
from ..paper.session import PAPER_WINDOW_MAX
from ..paper.strategy_factory import PaperStrategySpec
from .multi_timeframe_context import decision_timeframes

_CONTEXT_WARMUP = 80


class MarketWindow:
    """共享 K 线滚动窗口 + 各高周期窗口 + 逐策略建帧（多策略并集上界）。"""

    def __init__(self, inst_id: str, bar: str, specs: list[PaperStrategySpec],
                 priming_bars: int, *, context_enabled: bool = True):
        self.inst_id = inst_id
        self.bar = bar
        self._specs = specs
        base_seconds = BAR_SECONDS.get(bar, 60)
        warmup = max((s.strategy.warmup() for s in specs), default=20)
        htf_warmup = max((s.strategy.htf_warmup_bars() for s in specs), default=60)
        self._context_bars = decision_timeframes(bar) if context_enabled else ()
        self._window_max = min(
            max(warmup + 5, priming_bars, htf_warmup + 5,
                _CONTEXT_WARMUP if self._context_bars else 0),
            PAPER_WINDOW_MAX,
        )
        self._candles: deque[dict] = deque(maxlen=self._window_max)
        strategy_bars: set[str] = set()
        for spec in specs:
            for tf in spec.strategy.timeframes():
                if BAR_SECONDS.get(tf, 0) > base_seconds:
                    strategy_bars.add(tf)
        self._strategy_bars = tuple(
            sorted(strategy_bars, key=lambda b: BAR_SECONDS.get(b, 0)))
        htf_set = strategy_bars | set(self._context_bars)
        self._htf_bars = sorted(htf_set, key=lambda b: BAR_SECONDS.get(b, 0))
        self._htf_windows: dict[str, deque] = {b: deque(maxlen=self._window_max)
                                               for b in self._htf_bars}
        self._htf_next_refresh_ts: dict[str, int] = {}
        self._last_confirmed_ts: int | None = None
        self._htf_warmup = htf_warmup

    # ---------- 只读 ----------
    @property
    def window_max(self) -> int:
        return self._window_max

    @property
    def candles(self) -> deque[dict]:
        return self._candles

    @property
    def last_confirmed_ts(self) -> int | None:
        return self._last_confirmed_ts

    def cur_index(self) -> int:
        """当前窗口位置索引（`len-1`）；暖机与实时段统一用它驱动影子 broker（避免滑窗漂移）。"""
        return len(self._candles) - 1

    def base_df(self) -> pd.DataFrame:
        """当前基础周期窗口 → `candles_to_df`（供 E1 市场体制判定，与逐策略帧解耦）。

        用公有 `candles` 属性构造，不碰逐策略 `prepare`/MTF（体制判定只需原始 OHLCV）。
        """
        return candles_to_df(list(self._candles))

    def context_frames(self) -> dict[str, pd.DataFrame]:
        """Return raw OHLCV copies for decision-context bars only."""
        return {
            bar: candles_to_df(list(self._htf_windows[bar])).copy()
            for bar in self._context_bars
            if self._htf_windows.get(bar)
        }

    # ---------- 追加 ----------
    def is_duplicate(self, candle: Candle) -> bool:
        return (self._last_confirmed_ts is not None and candle.ts <= self._last_confirmed_ts
                and bool(self._candles) and self._candles[-1]["ts"] == candle.ts)

    def append_confirmed(self, candle: Candle) -> dict:
        """追加一根已确认 K 线（同 ts 覆盖），返回该行 dict。"""
        row = {"ts": candle.ts, "o": candle.o, "h": candle.h, "l": candle.l,
               "c": candle.c, "vol": candle.vol}
        return self._append_row(row)

    def _append_row(self, row: dict) -> dict:
        if self._candles and self._candles[-1]["ts"] == row["ts"]:
            self._candles[-1] = row
        else:
            self._candles.append(row)
        self._last_confirmed_ts = row["ts"]
        return row

    # ---------- 暖机取数 ----------
    async def fetch_priming_rows(self, priming_bars: int) -> list[dict]:
        """拉近端真实历史行（**不追加**，交由会话逐根回放种入各策略影子），失败回空表（降级）。"""
        bars = min(max(self._max_warmup() + 5, priming_bars), self._window_max)
        try:
            history = await get_rest_client().get_candles(self.inst_id, self.bar, limit=bars)
        except Exception:  # noqa: BLE001 —— 暖机取数失败不阻塞，等实时 K 线
            return []
        return [{"ts": c.ts, "o": c.o, "h": c.h, "l": c.l, "c": c.c, "vol": c.vol}
                for c in history]

    def prime_append(self, row: dict) -> int:
        """暖机逐根追加（返回窗口位置索引 i），供会话回放进各策略影子。"""
        self._append_row(row)
        return self.cur_index()

    def _max_warmup(self) -> int:
        return max((s.strategy.warmup() for s in self._specs), default=20)

    # ---------- 逐策略建帧 ----------
    def build_strategy_frames(self) -> list[pd.DataFrame]:
        """为每个策略各自 `prepare`(+MTF `attach_timeframes`) 出一份帧（互不污染）。"""
        rows = list(self._candles)
        frames_by_bar = {b: candles_to_df(list(win)) for b, win in self._htf_windows.items() if win}
        out: list[pd.DataFrame] = []
        for spec in self._specs:
            df = spec.strategy.prepare(candles_to_df(rows), self.bar)
            strat_htf = {b for b in spec.strategy.timeframes() if b in frames_by_bar}
            if strat_htf:
                df = spec.strategy.attach_timeframes(
                    df, self.bar, {b: frames_by_bar[b] for b in strat_htf})
            out.append(df)
        return out

    # ---------- 高周期窗口（镜像 LiveAutoSession）----------
    async def prefill_htf(self, base_span_bars: int) -> None:
        """启动时一次性预填各高周期窗口（取数失败留空，安全降级）。"""
        if not self._htf_bars:
            return
        base_seconds = BAR_SECONDS.get(self.bar, 60)
        for b in self._htf_bars:
            secs = BAR_SECONDS.get(b)
            if not secs:
                continue
            span_htf = (int(base_span_bars) * base_seconds) // secs + 1
            warmup = max(
                self._htf_warmup,
                _CONTEXT_WARMUP if b in self._context_bars else 0,
                span_htf,
            )
            limit = min(PAPER_WINDOW_MAX, warmup + 5)
            candles = await self._safe_get_candles(b, limit)
            if not candles:
                continue
            win = self._htf_windows[b]
            existing = {row["ts"] for row in win}
            for c in sorted(candles, key=lambda item: item.ts):
                if getattr(c, "confirm", True) and c.ts not in existing:
                    win.append({"ts": c.ts, "o": c.o, "h": c.h, "l": c.l, "c": c.c, "vol": c.vol})
                    existing.add(c.ts)
            latest_raw = max(candles, key=lambda item: item.ts)
            self._htf_next_refresh_ts[b] = int(latest_raw.ts) + secs

    async def maybe_refresh_htf(self, decision_as_of_ts: int) -> None:
        """Refresh on exchange-reported bar anchors; failures retry next base bar."""
        for b in self._htf_bars:
            secs = BAR_SECONDS.get(b)
            if not secs:
                continue
            next_refresh = self._htf_next_refresh_ts.get(b)
            if next_refresh is not None and decision_as_of_ts < next_refresh:
                continue
            candles = await self._safe_get_candles(
                b, min(PAPER_WINDOW_MAX, max(
                    self._htf_warmup,
                    _CONTEXT_WARMUP if b in self._context_bars else 0,
                ) + 5))
            if not candles:
                continue
            win = self._htf_windows[b]
            existing = {row["ts"] for row in win}
            for c in sorted(candles, key=lambda item: item.ts):
                if getattr(c, "confirm", True) and c.ts not in existing:
                    win.append({"ts": c.ts, "o": c.o, "h": c.h, "l": c.l, "c": c.c, "vol": c.vol})
                    existing.add(c.ts)
            latest_raw = max(candles, key=lambda item: item.ts)
            self._htf_next_refresh_ts[b] = int(latest_raw.ts) + secs

    async def _safe_get_candles(self, bar: str, limit: int) -> list | None:
        """Fetch one timeframe; failures return None so refresh is not advanced."""
        try:
            return await get_rest_client().get_candles(self.inst_id, bar, limit=limit)
        except Exception:  # noqa: BLE001 —— 取数失败：窗口不更新（安全降级，绝不假数据）
            return None
