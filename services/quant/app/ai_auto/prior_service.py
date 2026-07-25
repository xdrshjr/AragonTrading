"""先验快照重建的 service 编排（取数 + 线程池计算 + 回执整形）。

**本文件是 §8.2 G-6「超了就再下沉一层而不是再放宽」的产物**（见 spec §十四 实施发现 I-3）：
v2 给 `api/ai_auto.py` 的净增预算是 +58 行，而「两个端点的守门 + `_resolve_prior_snapshots`」按本仓库
的注释密度已经吃满，取数 / HTF 装帧 / 线程池卸载再放进路由层就把路由写成了 service。仓库既有同款
分层（`factors/service.py`、`ai_auto/backtest.py`）：路由只做守门与编排，取数与重计算下沉一层。

CPU 密集件一律经 `run_in_threadpool` 调度（沿用 `run_backtest` 范式），绝不阻塞事件循环。
"""
from __future__ import annotations

import time

from fastapi import HTTPException
from fastapi.concurrency import run_in_threadpool

from ..agent.services import load_htf_frames, load_range_candles
from ..config import Settings
from ..engine.backtester import candles_to_df
from ..okx.rest_client import get_rest_client
from ..okx.types import BAR_SECONDS
from .backtest_frames import build_backtest_frames
from .prior_builder import build_prior_snapshot

# 实得根数下限 `max(segments × 60, 300)`：不足即 422 **不假数据**（C1 惯例，绝不用短样本硬产统计）。
_MIN_BARS_PER_SEGMENT = 60
_MIN_BARS_FLOOR = 300
_HTF_WARMUP_BARS = 80
_DEFAULT_SEGMENTS = 6


async def rebuild_prior_snapshot(*, inst_id: str, bar: str, specs: list,
                                 fingerprints: dict[int, str], settings: Settings,
                                 lookback: int, segments: int | None = None) -> dict:
    """取真实 K 线 → 建策略帧 → 事件研究 → 返回可直接落库的快照（两次重计算均卸载到线程池）。

    Args:
        inst_id / bar: 目标标的与周期（`bar` 的合法性由调用方在取数前守门）。
        specs: 选中策略的运行规格；空列表 = 只建 `signal_edge` 侧。
        fingerprints: `{strategyId: 指纹}`（C-6 校验字段的来源）。
        settings: 运行配置（提供持有期、分段隔离、衰减与三条 `usable` 门槛）。
        lookback: 回看根数（上限已由调用方在**取数前**判定，413 快速失败）。
        segments: 分段数；缺省 6（`DEFAULT_IC_SEGMENTS`）。

    Returns:
        §5.2 的快照 dict。

    Raises:
        HTTPException: 422 `AI_PRIOR_INSUFFICIENT_BARS`——实得根数不足下限。
    """
    seg = int(segments or _DEFAULT_SEGMENTS)
    bar_sec = BAR_SECONDS[bar]
    end_ts = int(time.time())
    start_ts = end_ts - lookback * bar_sec
    rows = await load_range_candles(get_rest_client(), inst_id=inst_id, bar=bar,
                                    start_ts=start_ts, end_ts=end_ts, max_bars=lookback)
    need = max(seg * _MIN_BARS_PER_SEGMENT, _MIN_BARS_FLOOR)
    if len(rows) < need:
        raise HTTPException(422, detail={
            "code": "AI_PRIOR_INSUFFICIENT_BARS",
            "message": f"可用根数不足：需 ≥{need} 根，实得 {len(rows)} 根"})
    htf = await _load_htf(inst_id, bar, specs, bar_sec, start_ts, end_ts)
    frames = await run_in_threadpool(build_backtest_frames, specs, rows, htf, bar)
    return await run_in_threadpool(
        build_prior_snapshot, candles_to_df(rows), specs, frames, inst_id=inst_id, bar=bar,
        horizon_bars=settings.ai_trade_cost_expected_hold_bars, segments=seg,
        embargo_bars=settings.ai_trade_prior_embargo_bars,
        half_life_days=settings.ai_trade_prior_decay_half_life_days,
        min_samples=settings.ai_trade_prior_min_samples,
        min_runs=settings.ai_trade_prior_min_runs, built_at_ts=end_ts,
        fingerprints=fingerprints, min_segments=settings.ai_trade_prior_min_segments)


async def _load_htf(inst_id: str, bar: str, specs: list, bar_sec: int, start_ts: int,
                    end_ts: int) -> dict:
    """策略声明的高周期帧（镜像 `ai_auto/backtest._load_htf`）；无声明 ⇒ `{}`（零额外请求）。"""
    tfs = sorted({tf for spec in specs for tf in spec.strategy.timeframes()
                  if tf != bar and BAR_SECONDS.get(tf, 0) > bar_sec},
                 key=lambda tf: BAR_SECONDS[tf])
    if not tfs:
        return {}
    return await load_htf_frames(get_rest_client(), inst_id=inst_id, start_ts=start_ts,
                                 end_ts=end_ts, base_seconds=bar_sec, timeframes=tfs,
                                 htf_warmup_bars=_HTF_WARMUP_BARS)


def prior_snapshot_out(snapshot: dict) -> dict:
    """快照原文 → `AiTradePriorSnapshotOut` 形状（条目计数在此派生，绝不另存一份易漂移的计数）。"""
    entries = snapshot.get("entries") or []
    head = {key: snapshot.get(key) for key in
            ("instId", "bar", "builtAtTs", "asOfTs", "horizonBars", "costBasis", "bars",
             "segments", "purgeBars", "embargoBars")}
    return {**head, "entryCount": len(entries),
            "usableCount": sum(1 for e in entries if e.get("usable")),
            "coverage": snapshot.get("coverage") or {}, "entries": entries}
