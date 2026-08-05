"""区间取数辅助（discovery-asset-ledger §2.7）—— **一个实现，两个调用点**。

``promote._load_backtest_window`` 与体检守护要的是**同一件事**：取基础区间 K 线 + 该候选
所需的高周期帧。复制一份的代价不是多几行代码，而是两份取数口径迟早漂移（暖机根数、
高周期过滤规则、``max_bars`` 护栏），而漂移了没人看得出来——一键回测与体检会在同一条资产上
给出不同的数字，用户无从判断哪个是对的。

缺帧**不在这里**兜底：由调用方的 ``assembler.preflight(ctx)`` fail-closed 拦下（BL-6），
本模块只负责取数，绝不用无帧的结果假装取到了。
"""
from __future__ import annotations


async def load_window(rest, *, inst_id: str, bar: str, start_ts: int, end_ts: int,
                      timeframes: list[str], settings) -> tuple[list, dict]:
    """取 ``[start_ts, end_ts]`` 的基础 K 线 + 高于 ``bar`` 的那几档高周期帧。

    Args:
        rest: OKX REST 客户端。
        inst_id: 标的（体检时取**资产自己的**，绝不取当前活跃 run 的，AL-12）。
        bar: 基础周期。
        start_ts: 区间左端（秒级 UTC）。
        end_ts: 区间右端（秒级 UTC，须为已确认根）。
        timeframes: 需要的周期清单；等于 ``bar`` 或更低的一律忽略。
        settings: 取 ``backtest_max_bars`` 护栏。

    Returns:
        ``(candles, htf_frames)``；无需高周期时 ``htf_frames`` 为空字典（**不取**多余的帧）。
    """
    from ..agent.services import load_htf_frames, load_range_candles
    from ..engine.mtf import bar_to_seconds

    candles = await load_range_candles(rest, inst_id=inst_id, bar=bar,
                                       start_ts=int(start_ts), end_ts=int(end_ts),
                                       max_bars=int(settings.backtest_max_bars))
    base = bar_to_seconds(bar)
    needed = sorted({tf for tf in timeframes
                     if tf != bar and bar_to_seconds(tf) > base})
    if not needed:
        return candles, {}
    frames = await load_htf_frames(rest, inst_id=inst_id, start_ts=int(start_ts),
                                   end_ts=int(end_ts), base_seconds=base,
                                   timeframes=needed, htf_warmup_bars=64)
    return candles, frames
