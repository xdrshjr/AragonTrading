"""因子组合得分（scoring.py）—— 因果滚动 z-score 加权复合 (spec §2.6)。

这是「策略用得分做交易决策」的**唯一数值来源**，回测与实时共用同一函数。归一化契约
（评审 C-P0-1）：``causal_zscore`` 是训练端 ``build_factor_matrix`` 与打分端 ``compose_score``
**共用**的唯一因果标准化（同窗口、同 clip）——权重在该变换后的特征上学习又在同一变换下使用，
两端量纲一致。方向必须是冻结的 ``long/short``（``compose_score`` 绝不接受 ``auto``，杜绝打分期
按全样本 IC 现算符号造成的前视泄漏，评审 C-P1-1）。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .library import compute_factor

DEFAULT_NORM_WINDOW = 200
DEFAULT_CLIP = 4.0


def resolve_norm_contract(*, norm_stats: dict | None, thresholds: dict | None) -> tuple[int, float]:
    """打分阶段归一化参数 ``(normWindow, clip)`` 的**唯一解析口径**（归一化契约 H1）。

    训练产物 ``norm_stats={normWindow,clip}`` 是「权重所依赖的归一化变换」的真相；打分端必须取同值，
    否则把已训练权重作用到不同量纲的特征上（评审 C-P0-1 的复活路径）。仅当组合无训练权重
    （``equal`` 基线 / 未训练 / 旧组合无 ``normStats``）时，才回落到 ``thresholds.normWindow`` + 默认 clip。

    Args:
        norm_stats: 训练写入的契约 ``{normWindow, clip}``（缺 ``clip`` 时回默认）。
        thresholds: 组合阈值（含 ``normWindow``），仅在无训练契约时作回落来源。

    Returns:
        ``(norm_window, clip)``：``norm_stats.normWindow`` 存在即优先取它，否则取 ``thresholds.normWindow``。
    """
    stats = norm_stats or {}
    if stats.get("normWindow") is not None:
        clip = stats.get("clip")
        return int(stats["normWindow"]), float(clip) if clip is not None else DEFAULT_CLIP
    return int((thresholds or {}).get("normWindow", DEFAULT_NORM_WINDOW)), DEFAULT_CLIP


def causal_zscore(series: pd.Series, norm_window: int, clip: float = DEFAULT_CLIP) -> pd.Series:
    """因果滚动 z-score（窗口 ``norm_window``，只用 ≤ i 数据；归一化契约唯一真源 C-P0-1）。

    标准差为 0 的窗口走 NaN 安全口径（绝不除零）；``clip`` > 0 时把 z 夹到 ``[-clip, clip]``
    抑制极端值（暖机段 NaN 由上层统一按「跳过」处理）。
    """
    window = max(int(norm_window), 2)
    mean = series.rolling(window=window, min_periods=window).mean()
    std = series.rolling(window=window, min_periods=window).std(ddof=0).replace(0.0, np.nan)
    z = (series - mean) / std
    if clip and clip > 0:
        z = z.clip(lower=-clip, upper=clip)
    return z


def member_names(members: list[dict]) -> list[str]:
    """成员稳定列名（重复 factorKey 追加 #k 去重，训练 / 打分共用以对齐权重，镜像 build_members）。"""
    names: list[str] = []
    seen: dict[str, int] = {}
    for member in members:
        base = member.get("name") or member.get("factorKey") or "factor"
        count = seen.get(base, 0)
        seen[base] = count + 1
        names.append(base if count == 0 else f"{base}#{count + 1}")
    return names


def directional_values(df: pd.DataFrame, member: dict, *, bar: str = "") -> pd.Series:
    """单成员方向化因子值（``dir·value``；方向必须冻结为 long/short，绝不接受 auto）。

    Raises:
        ValueError: ``direction`` 非 ``long`` / ``short``（打分期前视泄漏防线，评审 C-P1-1）。
    """
    direction = member.get("direction", "long")
    if direction not in ("long", "short"):
        raise ValueError(f"compose_score 不接受方向 {direction!r}：组合成员方向须冻结为 long/short")
    mult = -1.0 if direction == "short" else 1.0
    raw = compute_factor(df, member["factorKey"], member.get("params"), bar=bar)
    return mult * raw


def compose_score(df: pd.DataFrame, members: list[dict], weights: dict[str, float] | None = None,
                  *, norm_window: int = DEFAULT_NORM_WINDOW, squash: bool = True,
                  bar: str = "", clip: float = DEFAULT_CLIP) -> pd.Series:
    """因果滚动 z-score 加权复合得分（与 df 等长，暖机段 NaN）。

    Args:
        df: ``candles_to_df`` 产物（列 ts/o/h/l/c/vol，升序）。
        members: ``[{factorKey, params, weight, direction}]``（方向已冻结）。
        weights: 训练产出的权重 ``{name: float}``；缺省回落各成员自带 ``weight``。
        norm_window: 因果 z-score 窗口（须与训练端取同值，归一化契约）。
        squash: 默认 True，用 ``tanh`` 压缩到 ``[-1,1]`` 使阈值有恒定基准（评审 C-P1-4）。

    Returns:
        与 ``df`` 等长的复合得分 ``pd.Series``（任一成员暖机 NaN 即该根 NaN，需全部成员就绪）。
    """
    work = df.reset_index(drop=True)
    if not members:
        return pd.Series(np.nan, index=work.index)
    names = member_names(members)
    total = pd.Series(0.0, index=work.index)
    used = False
    for name, member in zip(names, members):
        raw_weight = (weights or {}).get(name, member.get("weight"))
        weight = float(raw_weight) if raw_weight is not None else 0.0
        if weight == 0.0:
            continue
        z = causal_zscore(directional_values(work, member, bar=bar), norm_window, clip)
        total = total + weight * z
        used = True
    if not used:
        return pd.Series(np.nan, index=work.index)
    if squash:
        total = pd.Series(np.tanh(total.to_numpy(dtype=float)), index=work.index)
    return total


def score_points(df: pd.DataFrame, score: pd.Series) -> list[dict]:
    """得分序列 → ``[{ts, score}]``（剔除暖机 NaN，上图 / 当前打分用）。"""
    work = df.reset_index(drop=True)
    out: list[dict] = []
    for ts, value in zip(work["ts"].to_numpy(), score.to_numpy(dtype=float)):
        if np.isfinite(value):
            out.append({"ts": int(ts), "score": round(float(value), 6)})
    return out
