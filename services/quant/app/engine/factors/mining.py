"""因子挖掘（mining.py）—— 库 × 小参数网格扫描并按 RankIC IR 排名 (spec §2.4)。

纯计算、无 IO、严格因果：对候选集合逐一 ``compute_factor`` + ``evaluate_factor``，按主排序键
``abs(RankIC IR @ 主 horizon)`` 降序排名。不兼容当前 bar 的候选（如 ``px_vwap_dist`` 在非日内）
**直接跳过**（标 ``note="bar_incompatible"``），绝不让单个候选异常拖垮整轮扫描。工作量上界
（候选数 × 区间根数）由调用方（service 层）按 ``MINE_WORK_UNIT_LIMIT`` 护栏，超限抛 413
``FACTOR_MINE_TOO_LARGE``（绝不静默少算，评审 C-P1-3）。
"""
from __future__ import annotations

import pandas as pd

from .evaluation import DEFAULT_HORIZONS, evaluate_factor_key
from .library import FACTOR_LIBRARY, is_bar_compatible

# 工作量上界（候选数 × 有效根数）；超限由 service 抛 413（沿用 compare 413 语义，评审 C-P1-3）。
MINE_WORK_UNIT_LIMIT = 3_000_000

# 库 × 小参数网格（v1 固定、显式、有界；每因子取默认 + 至多一两个邻近值）。
_MINE_GRID: dict[str, list[dict]] = {
    "mom_roc": [{"win": 10}, {"win": 20}, {"win": 40}],
    "mom_ema_slope": [{"period": 12}, {"period": 20}],
    "trend_ema_dist": [{"period": 30}, {"period": 60}, {"period": 120}],
    "trend_adx_proxy": [{"period": 14}, {"period": 28}],
    "rev_rsi": [{"period": 7}, {"period": 14}],
    "rev_boll_z": [{"period": 20, "std": 2.0}],
    "vol_atr_pct": [{"period": 14}],
    "vol_realized": [{"win": 20}],
    "volu_zscore": [{"win": 20}],
    "volu_obv_slope": [{"win": 20}],
    "liq_amihud": [{"win": 20}],
    "px_vwap_dist": [{}],
}


def default_candidates(bar: str, categories: list[str] | None = None) -> list[dict]:
    """整库 × 小参数网格展开为候选 ``[{factorKey, params, direction, category}]``。

    Args:
        bar: 当前周期（不兼容候选仍纳入，挖掘时按 note 跳过，便于前端如实展示）。
        categories: 可选类别白名单（缺省全库）。
    """
    out: list[dict] = []
    for key, spec in FACTOR_LIBRARY.items():
        if categories and spec.category not in categories:
            continue
        for params in _MINE_GRID.get(key, [{}]):
            out.append({"factorKey": key, "params": dict(params),
                        "direction": spec.direction_hint, "category": spec.category})
    return out


def mining_workload(candidate_count: int, effective_bars: int) -> int:
    """工作量单位（候选数 × 有效根数），service 据此与 ``MINE_WORK_UNIT_LIMIT`` 比较。"""
    return max(0, int(candidate_count)) * max(0, int(effective_bars))


def _rank_one(df: pd.DataFrame, candidate: dict, *, bar: str,
              horizons: tuple[int, ...], primary: int) -> dict:
    """评估单候选并投影为 RankedFactor dict（不兼容 bar 直接返回占位，绝不抛错）。"""
    key = candidate["factorKey"]
    params = candidate.get("params") or {}
    direction = candidate.get("direction", "long")
    base = {"factorKey": key, "params": params, "direction": direction,
            "category": candidate.get("category", "")}
    if not is_bar_compatible(key, bar):
        return {**base, "ic": None, "rankIc": None, "ir": None, "monotonicity": None,
                "coverage": 0.0, "occurrences": 0, "note": "bar_incompatible"}
    result = evaluate_factor_key(df, key, params, direction, bar=bar, horizons=horizons)
    stat = next((h for h in result.horizons if h.h == primary), None)
    return {**base, "direction": result.direction,
            "ic": stat.ic if stat else None, "rankIc": stat.rankIc if stat else None,
            "ir": stat.ir if stat else None, "monotonicity": stat.monotonicity if stat else None,
            "coverage": result.coverage, "occurrences": result.occurrences, "note": result.note}


def mine_factors(df: pd.DataFrame, candidates: list[dict], *, bar: str = "",
                 horizons: tuple[int, ...] = DEFAULT_HORIZONS) -> list[dict]:
    """对候选集合逐一评估并按 ``abs(RankIC IR @ 主 horizon)`` 降序排名（纯计算，严格因果）。

    Returns:
        ``[RankedFactor]`` 全量（截断 topN 与 truncated 标志由 service 处理，不在此静默截断）。
    """
    sorted_h = sorted({int(h) for h in horizons if int(h) > 0}) or list(DEFAULT_HORIZONS)
    primary = sorted_h[0]
    ranked = [_rank_one(df, cand, bar=bar, horizons=tuple(sorted_h), primary=primary)
              for cand in candidates]
    ranked.sort(key=lambda row: abs(row["ir"]) if row.get("ir") is not None else -1.0, reverse=True)
    return ranked
