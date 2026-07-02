"""因子挖掘（mining.py）—— 库 × 小参数网格扫描并按 RankIC IR 排名 (spec §2.4)。

纯计算、无 IO、严格因果：对候选集合逐一 ``compute_factor`` + ``evaluate_factor``，按主排序键
``abs(RankIC IR @ 主 horizon)`` 降序排名。不兼容当前 bar 的候选（如 ``px_vwap_dist`` 在非日内）
**直接跳过**（标 ``note="bar_incompatible"``），绝不让单个候选异常拖垮整轮扫描。工作量上界
（候选数 × 区间根数）由调用方（service 层）按 ``MINE_WORK_UNIT_LIMIT`` 护栏，超限抛 413
``FACTOR_MINE_TOO_LARGE``（绝不静默少算，评审 C-P1-3）。
"""
from __future__ import annotations

import json
from collections import OrderedDict

import numpy as np
import pandas as pd

from .evaluation import DEFAULT_HORIZONS, evaluate_factor_key
from .library import FACTOR_LIBRARY, is_bar_compatible

# 工作量上界（候选数 × 有效根数）；超限由 service 抛 413（沿用 compare 413 语义，评审 C-P1-3）。
MINE_WORK_UNIT_LIMIT = 3_000_000

# 落库过滤阈值（H5）：默认仅过滤 ir=None 与零覆盖；调高 MINE_PERSIST_MIN_ABS_IR 可只留强因子。
MINE_PERSIST_MIN_ABS_IR = 0.0
MINE_MIN_COVERAGE = 0.0

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


# ============================ 标的组聚合（组内均值排名，factor-lab-symbol-groups §3.4） ============================
def _merge_key(item: dict) -> str:
    """归并键 = ``factorKey | 规范化(params)``（**不含 direction**，C-3）：避免同一候选在不同标的上方向不同被拆键。"""
    return f"{item.get('factorKey', '')}|{json.dumps(item.get('params') or {}, sort_keys=True)}"


def _wmean(pairs: list[tuple]) -> float | None:
    """``(value, weight)`` 列表 → 加权均值；跳过 None / 非有限值 / 非正权重；无有效对 → None（绝不臆造 0）。"""
    num = den = 0.0
    for value, weight in pairs:
        if value is None:
            continue
        fv, fw = float(value), float(weight)
        if not (np.isfinite(fv) and fw > 0.0):
            continue
        num += fv * fw
        den += fw
    return num / den if den > 0.0 else None


def _mean(values: list) -> float | None:
    """非空有限值的简单均值；全空 → None。"""
    finite = [float(v) for v in values if v is not None and np.isfinite(float(v))]
    return sum(finite) / len(finite) if finite else None


def _round6(value: float | None) -> float | None:
    return None if value is None else round(float(value), 6)


def aggregate_mine_results(per_symbol_ranked: list[list[dict]], symbols: list[str],
                           top_n: int) -> list[dict]:
    """把逐标的挖掘排名按 ``(factorKey, canonical(params))`` 归并为「组内均值」候选并按 abs(组 IR) 降序取 topN。

    归并键**不含 direction**（C-3）：同一候选的方向统一取自**焦点标的**（``per_symbol_ranked[0]``）的解析结果，
    焦点缺该候选则回落聚合 IR 符号。组分 = 各标的 IR **非空按 coverage 加权均值**（全空丢弃该候选），
    ``rankIc``/``ic``/``monotonicity`` 同法，``coverage`` 取逐标的均值，``occurrences`` 求和。

    Args:
        per_symbol_ranked: 逐标的 ``mine_factors`` 产物（顺序即 ``symbols`` 顺序，[0] 为焦点标的）。
        symbols: 参与聚合的有效标的（回显用；实际归并以逐标的排名为准）。
        top_n: 截断上限。

    Returns:
        归并后的 ``[RankedFactor]``（≤ topN；每候选回填统一 ``direction``）。
    """
    _ = symbols
    if not per_symbol_ranked:
        return []
    focus_dir = {_merge_key(item): item.get("direction", "long") for item in per_symbol_ranked[0]}
    grouped: "OrderedDict[str, dict]" = OrderedDict()
    for ranked in per_symbol_ranked:
        for item in ranked:
            key = _merge_key(item)
            bucket = grouped.get(key)
            if bucket is None:
                bucket = {"base": item, "ir": [], "rankIc": [], "ic": [], "mono": [], "cov": [], "occ": 0}
                grouped[key] = bucket
            cov = float(item.get("coverage") or 0.0)
            bucket["ir"].append((item.get("ir"), cov))
            bucket["rankIc"].append((item.get("rankIc"), cov))
            bucket["ic"].append((item.get("ic"), cov))
            bucket["mono"].append((item.get("monotonicity"), cov))
            bucket["cov"].append(item.get("coverage"))
            bucket["occ"] += int(item.get("occurrences") or 0)
    out: list[dict] = []
    for key, bucket in grouped.items():
        ir = _wmean(bucket["ir"])
        if ir is None:
            continue   # 全标的该候选 IR 皆空 → 丢弃（绝不臆造 0）
        base = bucket["base"]
        direction = focus_dir.get(key) or ("short" if ir < 0 else "long")
        out.append({
            "factorKey": base["factorKey"], "params": base.get("params") or {},
            "direction": direction, "category": base.get("category", ""),
            "ic": _round6(_wmean(bucket["ic"])), "rankIc": _round6(_wmean(bucket["rankIc"])),
            "ir": _round6(ir), "monotonicity": _round6(_wmean(bucket["mono"])),
            "coverage": round(_mean(bucket["cov"]) or 0.0, 4), "occurrences": bucket["occ"],
            "note": "",
        })
    out.sort(key=lambda row: abs(row["ir"]) if row.get("ir") is not None else -1.0, reverse=True)
    return out[:top_n]
