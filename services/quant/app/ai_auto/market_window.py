"""AiTradeSession 的行情窗口 / 多周期 / 逐策略建帧助手（P2-4：从会话拆分，控 ≤800 行/文件）。

镜像 `LiveAutoSession` / `PaperSession` 的滚动窗口 + 高周期（MTF）窗口管理，但服务**多策略**：
`_window_max` 与高周期集合取所有选中策略的**并集**上界；`build_strategy_frames` 为每个策略各自
`prepare`(+`attach_timeframes`) 出一份帧（不同策略指标列不同，绝不交叉污染）。窗口只承载 K 线，
不驱动任何 broker / 决策（回放与决策由会话协调）。
"""
from __future__ import annotations

import asyncio
import time
from collections import deque
from dataclasses import dataclass

import pandas as pd

from ..engine.backtester import candles_to_df
from ..okx.rest_client import get_rest_client
from ..okx.types import BAR_SECONDS, Candle
from ..paper.session import PAPER_WINDOW_MAX
from ..paper.strategy_factory import PaperStrategySpec
from .multi_timeframe_context import decision_timeframes

_CONTEXT_WARMUP = 80
# 单发 `/market/candles` 的 limit 硬上限：`get_rest_client().get_candles` 在 `limit<=300` 且无游标时
# 走单发，超过即改走 history-candles **翻页**（一次预填可能打出 4~5 个请求，直接违背配额目标）。
_SINGLE_PAGE_LIMIT_MAX = 300
# 刷新时至少要覆盖的根数余量（比「上次刷新以来可能新收的根数」多留一点，防锚点抖动漏根）。
_TACTICAL_REFRESH_SLACK = 10
_TACTICAL_REFRESH_MIN = 20


def build_frames_for(specs: list[PaperStrategySpec], rows, htf_rows,
                     bar: str) -> list[pd.DataFrame]:
    """逐策略建帧的**唯一真源**：`MarketWindow.build_strategy_frames` 与轴 C 的预演共用。

    抽出来是为了让预演走的是**同一条**建帧路径——复制粘贴一份就等于预演的不是同一个系统，而这类
    分叉在本仓库有前科（同一个量两处各算一次）。行为与抽取前逐字不变（同参对拍）。

    Args:
        specs: 策略清单（预演时必须传**克隆体**：`prepare` 会写策略内部状态）。
        rows: 基础周期 K 线行序列（`list` / `deque` 均可）。
        htf_rows: 各高周期窗口 `{bar: rows}`（空窗自动略过）。
        bar: 基础（执行）周期名。

    Returns:
        与 `specs` 一一对齐的帧列表。
    """
    base_rows = list(rows)
    frames_by_bar = {b: candles_to_df(list(win)) for b, win in htf_rows.items() if win}
    out: list[pd.DataFrame] = []
    for spec in specs:
        df = spec.strategy.prepare(candles_to_df(base_rows), bar)
        strat_htf = {b for b in spec.strategy.timeframes() if b in frames_by_bar}
        if strat_htf:
            df = spec.strategy.attach_timeframes(
                df, bar, {b: frames_by_bar[b] for b in strat_htf})
        out.append(df)
    return out


@dataclass(frozen=True)
class WindowOptions:
    """`MarketWindow` 的可选装配项（聚合结构体，守 CLAUDE.md「形参 ≤5」）。

    `tactical_bars` 由 `WakeRuntime` 解析后**传入**——战术周期集合的唯一真源在运行时那侧，窗口只
    消费不自算。两处各算一次，一旦某处的 `wake_interval_s` 口径漂移（例如一处用秒、一处用分钟），
    就会出现「窗口在取 1m/5m，prompt 却宣称在看 5m/15m」这类无声失真。
    """

    context_enabled: bool = True
    tactical_bars: tuple[str, ...] = ()
    tactical_window_bars: int = 120
    tactical_min_refresh_s: float = 20.0
    tactical_fetch_timeout_s: float = 8.0


class MarketWindow:
    """共享 K 线滚动窗口 + 各高周期窗口 + 战术周期窗口 + 逐策略建帧（多策略并集上界）。"""

    def __init__(self, inst_id: str, bar: str, specs: list[PaperStrategySpec],
                 priming_bars: int, *, options: WindowOptions = WindowOptions()):
        self.inst_id = inst_id
        self.bar = bar
        self._specs = specs
        self._opt = options
        base_seconds = BAR_SECONDS.get(bar, 60)
        warmup = max((s.strategy.warmup() for s in specs), default=20)
        htf_warmup = max((s.strategy.htf_warmup_bars() for s in specs), default=60)
        self._context_bars = decision_timeframes(bar) if options.context_enabled else ()
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
        # 战术周期窗口（严格短于执行周期，与 `_htf_bars` 集合**不可能相交**——后者只收
        # `BAR_SECONDS[tf] > base_seconds`，故不存在窗口争用）。刻意**不复用** `_window_max`
        # （多策略并集上界，最大 1200 根）：战术窗口只服务指标计算，120 根已 ≥ market_digest 的
        # _MIN_BARS=35 且 ≥ classify_regime 的 60。
        self._tactical_bars = tuple(
            sorted(options.tactical_bars, key=lambda b: BAR_SECONDS.get(b, 0)))
        self._tactical_windows: dict[str, deque] = {
            b: deque(maxlen=options.tactical_window_bars) for b in self._tactical_bars}
        self._tactical_next_refresh_ts: dict[str, int] = {}
        self._tactical_last_fetch_wall: dict[str, float] = {}

    # ---------- 只读 ----------
    @property
    def window_max(self) -> int:
        return self._window_max

    @property
    def tactical_bars(self) -> tuple[str, ...]:
        return self._tactical_bars

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
        return build_frames_for(self._specs, list(self._candles), self._htf_windows, self.bar)

    # ---------- 预演快照（wake-scheduler-closed-loop 轴 C）----------
    def preview_snapshot(self, live_row: dict | None, *, now_wall: float,
                         last_px_wall: float, stale_after_s: float):
        """未确认根预演所需的只读快照；任一前置闸不过 ⇒ None（不预演，绝不猜）。

        必须在**事件循环线程**取：`_candles` / `_htf_windows` 是 `deque`，另一侧 `feed_candle`
        随时可能 append，把 deque 直接交给工作线程会抛 `RuntimeError: deque mutated`。

        闸 ①（重复根）：`live_row["ts"] <= _last_confirmed_ts` ⇒ None。确认根落地之后仍可能收到一条
        **同 ts** 的未确认推送（`feed_candle` 的未确认分支不做 ts 校验），把它追加到 `rows` 末尾会让
        帧里出现两根同 ts 的 K 线、指标在重复根上计算 ⇒ 产生一个纯属数据缺陷的「翻转」，并据此起一轮
        真实资金决策。

        闸 ②（判定价新鲜度）：`now_wall - last_px_wall > stale_after_s` ⇒ None。**必须读
        `_last_px_wall`（每次写实时价即刷新）而不是 `_last_candle_wall`（只在确认根更新）**——后者在
        12H/1D 执行周期下几十小时不跳，WS 静默时 `_stale_degraded` 还没触发、`_live_row` 已冻结不动，
        而防抖器仍在按墙钟累积「已连续保持 45 秒」。这是利润保护层 D-1 踩过的同款坑。

        Args:
            live_row: 当前未确认根（`{ts,o,h,l,c,vol}`）；None ⇒ None。
            now_wall: 当前墙钟。
            last_px_wall: 最近一次收到任意价格推送的墙钟（`session._last_px_wall`）。
            stale_after_s: 判定价新鲜度上限（秒）。

        Returns:
            `signal_preview.PreviewSnapshot` 或 None。
        """
        from .signal_preview import PreviewSnapshot     # 延迟导入：与 signal_preview 互为依赖
        if not live_row or not self._candles:
            return None
        if self._last_confirmed_ts is not None and int(live_row["ts"]) <= self._last_confirmed_ts:
            return None
        if last_px_wall <= 0 or now_wall - last_px_wall > stale_after_s:
            return None
        row = dict(live_row)
        return PreviewSnapshot(
            rows=[*self._candles, row],
            htf_rows={b: list(win) for b, win in self._htf_windows.items() if win},
            bar=self.bar,
            row_key=(row["ts"], row["c"], row["h"], row["l"]),
            bar_ts=int(row["ts"]))

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

    # ---------- 战术周期窗口（ai-trade-wake-aware-context §2.2）----------
    def tactical_frames(self) -> dict[str, pd.DataFrame]:
        """各战术周期的原始 OHLCV 拷贝（镜像 `context_frames`，供战术证据装配）。"""
        return {
            bar: candles_to_df(list(self._tactical_windows[bar])).copy()
            for bar in self._tactical_bars
            if self._tactical_windows.get(bar)
        }

    async def prefill_tactical(self) -> None:
        """启动时一次性预填各战术周期窗口（取数失败留空，安全降级，绝不阻塞暖机）。

        `limit` 必须 ≤300：超过即改走 history-candles 翻页（见 `_SINGLE_PAGE_LIMIT_MAX`）。
        """
        for bar in self._tactical_bars:
            if bar not in BAR_SECONDS:
                continue
            limit = min(_SINGLE_PAGE_LIMIT_MAX, self._opt.tactical_window_bars + 5)
            candles = await self._fetch_tactical(bar, limit, time.time())
            if candles:
                self._absorb_tactical(bar, candles)

    async def maybe_refresh_tactical(self, now_ts: int, now_wall: float) -> None:
        """按交易所报出的收线锚点刷新战术窗口；失败不推进锚点（下 tick 重试）。

        三闸门（缺一不可，按序判定）：① 锚点闸——未到下一根收线即跳过（正常节奏 = 每收一根请求一次）；
        ② 配额闸——距上次取数不足 `tactical_min_refresh_s` 即跳过（硬护栏：即便锚点算错也不会让 5 秒
        盯市把 REST 打成每 5 秒一发，多标的分组会直接撞 429）；③ 单 tick 节流闸——一次调用**最多刷新
        一个**周期（本方法跑在 5 秒盯市 tick 上，而同一个 tick 还守着利润保护棘轮与会话止损这些真实
        资金路径，取数绝不能把它们挤停）。
        """
        bar = self._next_tactical_bar(now_ts, now_wall)
        if bar is None:
            return
        secs = BAR_SECONDS[bar]
        limit = min(_SINGLE_PAGE_LIMIT_MAX,
                    max(_TACTICAL_REFRESH_MIN,
                        int(self._opt.tactical_min_refresh_s // secs) + _TACTICAL_REFRESH_SLACK))
        candles = await self._fetch_tactical(bar, limit, now_wall)
        if candles:
            self._absorb_tactical(bar, candles)

    def _next_tactical_bar(self, now_ts: int, now_wall: float) -> str | None:
        """过完锚点闸与配额闸的候选里取 `BAR_SECONDS` 最小的一个（其余留给下一 tick）。"""
        for bar in self._tactical_bars:                  # 构造期已按 BAR_SECONDS 升序
            if bar not in BAR_SECONDS:
                continue
            next_refresh = self._tactical_next_refresh_ts.get(bar)
            if next_refresh is not None and now_ts < next_refresh:
                continue
            last_fetch = self._tactical_last_fetch_wall.get(bar)
            if last_fetch is not None and now_wall - last_fetch < self._opt.tactical_min_refresh_s:
                continue
            return bar
        return None

    async def _fetch_tactical(self, bar: str, limit: int, now_wall: float) -> list | None:
        """带硬超时的战术取数；超时/异常一律 None（不推进锚点 ⇒ 下 tick 重试）。

        `asyncio.wait_for` 的超时会取消内层协程并在**本调用点**抛 `TimeoutError`（`_safe_get_candles`
        的 `except Exception` 捕不到被取消时抛的 `CancelledError`），故超时必须在此显式收口。默认 8s
        ⇒ 单 tick 最坏增量 8s，仍在 5 秒盯市可容忍的抖动量级内。
        """
        self._tactical_last_fetch_wall[bar] = now_wall   # 配额闸按「发起时刻」记，失败同样受节流
        try:
            return await asyncio.wait_for(self._safe_get_candles(bar, limit),
                                          self._opt.tactical_fetch_timeout_s)
        except (TimeoutError, asyncio.TimeoutError):
            return None

    def _absorb_tactical(self, bar: str, candles: list) -> None:
        """入窗（只收确认根，按 ts 去重覆盖）并把锚点推到「最新一根 + 一根」。

        只收 `confirm` 为真的根：OKX `/market/candles` 会把**未收线的当前根**作为最新一条返回，
        收进来会让指标 repaint——正是本特性最需要避免的失真。
        """
        win = self._tactical_windows[bar]
        existing = {row["ts"] for row in win}
        for c in sorted(candles, key=lambda item: item.ts):
            if getattr(c, "confirm", True) and c.ts not in existing:
                win.append({"ts": c.ts, "o": c.o, "h": c.h, "l": c.l, "c": c.c, "vol": c.vol})
                existing.add(c.ts)
        latest_raw = max(candles, key=lambda item: item.ts)
        self._tactical_next_refresh_ts[bar] = int(latest_raw.ts) + BAR_SECONDS[bar]
