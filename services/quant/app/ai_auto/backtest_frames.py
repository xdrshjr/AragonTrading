"""AI 回测逐策略帧构造（全区间 prepare-once，ai-auto-trading-backtest §2.2 / R3）。

**为何不复用实盘 `market_window.MarketWindow`**（R3，一等约束）：`MarketWindow` 是**有界滚动窗口**
（`deque(maxlen=window_max)`）且经**实盘 REST**（`prefill_htf`/`maybe_refresh_htf`）取**近端** HTF —— 用于
历史回测将引入**未来函数 / 错时污染**，且 `build_strategy_frames` 每根重跑 `prepare`（O(n²)）。回测改为
**全区间一次性建帧**：每策略在整段区间上 `prepare`(+ 历史区间 `attach_timeframes`) 一次，所得帧按**绝对
索引 `i`** 供 `evaluate_signal_board(specs, shadows, dfs, i)` 逐根读取（镜像 `agent/services._execute_and_persist`
的 MTF 装配路径，HTF 走 `load_htf_frames` 的历史区间取数，严格因果、无未来函数）。
"""
from __future__ import annotations

import pandas as pd

from ..engine.backtester import candles_to_df
from ..okx.types import Candle
from ..paper.strategy_factory import PaperStrategySpec


def build_backtest_frames(specs: list[PaperStrategySpec], range_rows: list[Candle],
                          htf_frames: dict[str, list[Candle]], bar: str) -> list[pd.DataFrame]:
    """为每个策略构造整段区间的因果帧（一次 `prepare` + 可选 `attach_timeframes`），按绝对索引对齐。

    镜像 `engine.backtester.run_backtest` 的建帧路径：`df=candles_to_df(range) → prepare → attach_timeframes`，
    但对多策略只把基础帧建一次、各策略在自己的副本上 `prepare`，杜绝跨策略污染（与 `run_compare` 每策略
    独立 `run_backtest` 等价）。HTF 帧按策略 `timeframes()` 各取子集并 `candles_to_df` 一份（与
    `run_backtest` 的 `{b: candles_to_df(cs)}` 逐字节一致）。

    Args:
        specs: 逐策略运行规格（`build_paper_strategy_spec` 产物）。
        range_rows: 全区间（含暖机段）已确认 K 线，升序（`load_range_candles` 产物）。
        htf_frames: 高周期帧并集 `{htf_bar: 历史区间 Candle 列表}`（`load_htf_frames` 产物，可空）。
        bar: 基础周期（标准 OKX bar）。

    Returns:
        与 `specs` 一一对齐的 `list[pd.DataFrame]`；每帧长度等于区间根数，含各策略 `prepare`(+MTF) 后的
        指标列，供 `evaluate_signal_board` 按绝对索引 `i` 逐根读取。
    """
    base_df = candles_to_df(range_rows)
    dfs: list[pd.DataFrame] = []
    for spec in specs:
        strategy = spec.strategy
        df_k = strategy.prepare(base_df.copy(), bar)
        member_frames = {tf: candles_to_df(htf_frames[tf])
                         for tf in strategy.timeframes() if tf in htf_frames}
        if member_frames:   # 仅声明了高周期且取到真实历史数据才走 MTF 路径（与 run_backtest 一致）
            df_k = strategy.attach_timeframes(df_k, bar, member_frames)
        dfs.append(df_k)
    return dfs
