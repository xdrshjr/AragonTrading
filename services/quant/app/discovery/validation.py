"""验证层（strategy-factor-discovery §2.7）—— 硬编码、fail-closed、纯计算。

D-4：达标与否**绝不**由 LLM 说了算。本层只做三件事：① 按 §2.7.0 的 IS/OOS 求值与定号协议
算出两段指标；② 跑 11 条联合过滤；③ 维护 kept 池（值序列 + 分段 IC 序列）供 F10 双通道去重。

三条承重条款（评审 P0-1 / P0-2 / P1-2），改动须回文档：
- **全窗求值一次 → 同步切片 → IS 段定号 → 两段传同一冻结字面量**。严禁在本层调
  ``evaluate_factor_key``（它内部重算 ⇒ 暖机重置 + ``auto`` 重定号，F6 立刻空转）。
- **F8 的 ``roundTripCost`` 必须先除 1e4**（settings 是 bps，``topBottomSpread`` 是小数）。
- **F9 的 ``turnover_proxy`` 由本层自算**（仓库无此指标），不可判定即拒。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from ..engine.factors.evaluation import (
    _paired,
    _spearman,
    evaluate_factor,
    forward_return,
    resolve_direction,
)
from .expression import Node, evaluate, parse_expression, to_text, validate_node

# 分段数：12 段既满足 F4「有效分段 ≥6」，又让 F5 的同号占比与 F10 的 IC 序列相关有足够点数，
# 且 ≤ §5.3 的「分段 IC ≤24 点」上界。
IC_SEGMENTS = 12
QUANTILES = 5

# OOS 段可判定所需的最少配对样本（本层实现补齐，见 §十一 I-1）：F6 是最强的样本外防线，
# 样本太少时它的「同号」判据只是噪声。不可判定即拒（fail-closed），绝不放行。
MIN_OOS_SAMPLE_RATIO = 0.2

DEFAULT_THRESHOLDS: dict[str, float] = {
    "minAbsIc": 0.03,          # F2
    "minAbsIr": 0.5,           # F3（经 F11 多重检验惩罚抬高）
    "minAbsT": 2.0,            # F4
    "minCoverage": 0.6,        # F1
    "minSamples": 200,         # F1
    "minSignStability": 0.6,   # F5
    "oosRetain": 0.5,          # F6
    "minMonotonicity": 0.6,    # F7
    "costMult": 2.0,           # F8
    "maxTurnover": 0.35,       # F9
    "maxIcCorr": 0.70,         # F10 通道一
    "maxValueCorr": 0.85,      # F10 通道二
    "trialPenaltyK": 0.15,     # F11
}

# 11 条闸的中文名（UI 拒绝原因映射与 prompt 共用真源）。
FILTER_LABELS: dict[str, str] = {
    "F1": "覆盖率或样本量不足",
    "F2": "样本内 RankIC 太弱",
    "F3": "样本内 IR 未达（含多重检验惩罚）门槛",
    "F4": "分段 t 值不显著",
    "F5": "分段 RankIC 同号稳定性不足",
    "F6": "样本外反号或强度衰减过大",
    "F7": "分位单调性不足",
    "F8": "成本后 top-bottom 价差不足",
    "F9": "换手代理过高（疑似高频噪声）",
    "F10": "与已入库因子高度相关",
    "F11": "多重检验惩罚后门槛未达",
    # 策略轴 S1–S9 + 装配失败（discovery-strategy-axes §2.6.2）。前端 ``REJECT_REASON_CN``
    # 是**另一张**表，必须同步增补——只改这里，用户会在监控页看到裸英文码（评审 P1-5）。
    "S1": "样本量不足",
    "S2": "样本内亏损",
    "S3": "夏普未达（含多重检验惩罚）",
    "S4": "回撤超限",
    "S5": "Calmar 不足",
    "S6": "样本外崩塌",
    "S7": "交易过密或盈亏比不足",
    "S8": "与已入库策略高度相关",
    "S9": "成本压力下不再盈利",
    "S0": "装配失败",
}


@dataclass(frozen=True)
class ValidationContext:
    """一次验证所需的全部只读输入（聚合传参，避免位置参数堆叠）。"""

    df_full: pd.DataFrame
    horizon: int
    is_ratio: float = 0.7
    fee_bps: float = 5.0
    slip_bps: float = 2.0
    thresholds: dict = field(default_factory=lambda: dict(DEFAULT_THRESHOLDS))
    memo: dict = field(default_factory=dict)
    # ---- discovery-asset-ledger §2.4（两个**带默认值**的可选字段；默认值下行为逐位不变）----
    split_at: int | None = None      # None ⇒ 走 split_index(is_ratio)
    frozen_direction: str = ""       # "" / 非法值 ⇒ 走 resolve_direction(auto)


@dataclass
class KeptEntry:
    """已入库候选在内存中的去重基准（值序列 + 分段 IC 序列）。

    **不落库**：值序列有几千个点，落进检查点会破坏 §2.10 的 64 KB 有界目标；而 rolling 窗口
    右移后旧值即失效。故它由 ``rebuild_kept_pool`` 在续跑与数据刷新两处**用同一个实现**重建。
    """

    candidate_id: int
    expr_text: str
    direction: str
    values: np.ndarray
    ic_series: list[float]
    score: float
    mechanism: str = ""
    skeleton: str = ""


@dataclass
class ValidationOutcome:
    """单候选验证结果（``kept`` 时携带进 kept 池所需的值与 IC 序列）。"""

    verdict: str
    reject_reason: str
    direction: str
    metrics: dict
    series: dict
    values: np.ndarray | None = None
    ic_series: list[float] = field(default_factory=list)
    score: float = 0.0
    # 策略轴的去重基准（IS 段逐根收益率）。与因子轴的 ``values`` 同性质、同生命周期，
    # 但量纲完全不同，故单列一个字段而非复用——两条轴共用一个字段名迟早被读错。
    bar_returns: list = field(default_factory=list)


# ============================ F9：换手代理（本层自算） ============================
def turnover_proxy(values: np.ndarray, *, quantiles: int = QUANTILES) -> float | None:
    """分位档位的逐根切换率——因子隐含的换仓频率代理，``[0,1]``，越大越高频。

    以 ``pd.qcut`` 把有限值分成 ``quantiles`` 档（与 F7 单调性同一分档口径），统计相邻两根
    之间档位发生变化的比例。

    Returns:
        切换率；有限样本 < 2 或分档退化 → ``None``（F9 不可判定 ⇒ **拒**，fail-closed）。
    """
    finite = np.asarray(values, dtype=float)
    finite = finite[np.isfinite(finite)]
    if len(finite) < 2:
        return None
    try:
        codes = pd.qcut(finite, quantiles, labels=False, duplicates="drop")
    except ValueError:
        return None
    codes = np.asarray(codes, dtype=float)
    if not np.isfinite(codes).all() or len(set(codes.tolist())) < 2:
        return None
    return float((codes[1:] != codes[:-1]).mean())


# ============================ F11：多重检验惩罚 ============================
def penalized_gate(base: float, trials: int, k: float) -> float:
    """多重检验惩罚的**唯一**实现（BL-4）：``base × (1 + k·log10(max(trials,10)/10))``。

    因子轴的 ``required_ir`` 与策略轴的 ``required_sharpe`` 都必须调它。两份公式迟早会漂移，
    而漂移了没人看得出来（上游「估计量同源律」）。``trials`` 恒 ≥10 以避免 log 定义域问题；
    ``k=0`` 即退化为无惩罚。
    """
    return float(base) * (1.0 + float(k) * math.log10(max(int(trials), 10) / 10.0))


def required_ir(trials: int, thresholds: dict) -> float:
    """门槛随累计试验数抬高（§2.7 F11 / R-1）；返回值与抽取前**逐位相同**。

    跑几万个候选时纯噪声也能凑出 IR>0.5，这条惩罚是最直接的解药。默认值一律引
    ``DEFAULT_THRESHOLDS``——硬写字面量会在默认值调整时与真源静默漂移（评审 P2-1）。
    """
    return penalized_gate(
        float(thresholds.get("minAbsIr", DEFAULT_THRESHOLDS["minAbsIr"])), trials,
        float(thresholds.get("trialPenaltyK", DEFAULT_THRESHOLDS["trialPenaltyK"])))


# ============================ F8：往返成本（量纲承重） ============================
def round_trip_cost(fee_bps: float, slip_bps: float) -> float:
    """往返成本（**小数**）= ``2 × (fee_bps + slip_bps) / 1e4``。

    ⚠️ 承重（评审 P0-2）：``settings.default_taker_fee_bps`` / ``default_slippage_bps`` 的单位
    是 **bps**（``engine/simulator.py`` 也是 ``/1e4`` 后才用），而 ``topBottomSpread`` 是**小数
    收益差**。漏掉这个 ``/1e4`` 会让门槛变成 2800%，**没有任何候选能通过**，而且在 24/7 长跑
    里表现为「搜索很难」而不是报错——最难被发现的一类失效。生产默认 = 0.0014（14 bps 往返）。
    """
    return 2.0 * (float(fee_bps) + float(slip_bps)) / 1e4


# ============================ 分段 IC 序列（F5 / F10 共用） ============================
def segment_ic_series(values: np.ndarray, close: np.ndarray, horizon: int, *,
                      dir_mult: float, segments: int = IC_SEGMENTS) -> list[float]:
    """按时间等切 K 段的逐段 RankIC 序列（已按冻结方向乘过 ``dir_mult``）。

    估计量同源律：Spearman 与前瞻收益一律复用 ``engine.factors.evaluation`` 的实现，
    **绝不**在本层另写一份——两份估计量迟早会漂移，而漂移了没人看得出来。
    """
    fwd = forward_return(np.asarray(close, dtype=float), int(horizon))
    paired_values, paired_ret = _paired(np.asarray(values, dtype=float), fwd)
    out: list[float] = []
    total = len(paired_values)
    for j in range(segments):
        i0, i1 = j * total // segments, (j + 1) * total // segments
        if i1 - i0 < 2:
            continue
        ic = _spearman(paired_values[i0:i1], paired_ret[i0:i1])
        if ic is not None:
            out.append(round(dir_mult * float(ic), 6))
    return out


def sign_stability(ic_series: list[float]) -> float | None:
    """分段 RankIC 的同号占比（F5）；空序列 → None（不可判定 ⇒ 拒）。"""
    if not ic_series:
        return None
    positive = sum(1 for ic in ic_series if ic > 0.0)
    return positive / len(ic_series)


# ============================ §2.7.0：IS/OOS 求值与定号协议（承重） ============================
@dataclass
class SplitEvaluation:
    """全窗求值 + 同步切片 + IS 段定号后的两段评估产物。"""

    direction: str
    values: np.ndarray
    coverage: float
    res_is: object
    res_oos: object
    ic_is: list[float]
    ic_oos: list[float]


def split_index(total: int, is_ratio: float) -> int:
    """IS/OOS 切点下标（**唯一口径**：df 与 values 必须用同一个切点，绝不各切各的）。"""
    return max(1, min(max(total - 1, 1), int(total * float(is_ratio))))


def evaluate_split(node: Node, ctx: ValidationContext) -> SplitEvaluation:
    """按 §2.7.0 的**唯一合法实现**求值两段（下游照此写，不得变体）。

    ① 在【全窗】求值一次——rolling 暖机只发生一次，两段共用同一条序列（否则 OOS 段前
    ``max_window`` 根全 NaN，且两段算的**不是同一条序列**，评审 P1-6）；
    ② df 与 values **同步**按下标切片；
    ③ 在【IS 段】一次性定号并冻结；
    ④ 两段都传**同一个冻结字面量**——严禁再传 ``"auto"``，否则 ``rankIc`` 被构造成两段都
    非负，F6 的「同号」判据恒真，最强的样本外防线整条空转（评审 P0-1）。

    ``ctx.split_at`` / ``ctx.frozen_direction``（discovery-asset-ledger §2.4 · AL-1）只被持续
    体检用到：体检要在「挖掘窗口右端之后新产生的那一段」上判级，且**必须**用登记时冻结的
    方向——``auto`` 是在 IS 段（体检语境下即暖机段）定号的，行情一翻面它就跟着翻，衰减会被
    判成健康，且与 ``retention`` 的分母不同源。两个字段取默认值时本函数逐位不变。
    """
    work = ctx.df_full.reset_index(drop=True)
    values = evaluate(node, _context_of(ctx), ctx.memo)
    split = (int(ctx.split_at) if ctx.split_at is not None
             else split_index(len(work), ctx.is_ratio))
    split = max(1, min(split, max(len(work) - 1, 1)))
    df_is, v_is = work.iloc[:split], values[:split]
    df_oos, v_oos = work.iloc[split:], values[split:]
    close_is = df_is["c"].to_numpy(dtype=float)
    direction = (ctx.frozen_direction if ctx.frozen_direction in ("long", "short")
                 else resolve_direction(v_is, close_is, "auto",
                                        primary_horizon=ctx.horizon))
    dir_mult = -1.0 if direction == "short" else 1.0
    res_is = evaluate_factor(df_is, pd.Series(v_is), direction, factor_key="expr",
                             horizons=(ctx.horizon,), quantiles=QUANTILES,
                             ic_segments=IC_SEGMENTS)
    res_oos = evaluate_factor(df_oos, pd.Series(v_oos), direction, factor_key="expr",
                              horizons=(ctx.horizon,), quantiles=QUANTILES,
                              ic_segments=IC_SEGMENTS)
    coverage = float(np.isfinite(values).mean()) if len(values) else 0.0
    return SplitEvaluation(
        direction=direction, values=values, coverage=round(coverage, 4),
        res_is=res_is, res_oos=res_oos,
        ic_is=segment_ic_series(v_is, close_is, ctx.horizon, dir_mult=dir_mult),
        ic_oos=segment_ic_series(v_oos, df_oos["c"].to_numpy(dtype=float), ctx.horizon,
                                 dir_mult=dir_mult))


_CONTEXT_CACHE_KEY = "__discovery_field_context__"


def _context_of(ctx: ValidationContext) -> dict:
    """字段上下文（挂在 ``memo`` 上按 run 复用；字段派生只做一次）。"""
    from .expression import build_context

    cached = ctx.memo.get(_CONTEXT_CACHE_KEY)
    if cached is None:
        cached = build_context(ctx.df_full)
        ctx.memo[_CONTEXT_CACHE_KEY] = cached
    return cached


# ============================ 11 条联合过滤 ============================
def _stat(result) -> object | None:
    horizons = getattr(result, "horizons", None) or []
    return horizons[0] if horizons else None


def _abs(value) -> float:
    return abs(float(value)) if value is not None else 0.0


def _check_sample_gates(split: SplitEvaluation, thresholds: dict) -> str:
    """F1：覆盖率 + IS/OOS 配对样本量（OOS 样本不足即不可判定 ⇒ 拒，fail-closed）。"""
    min_samples = int(thresholds.get("minSamples", DEFAULT_THRESHOLDS["minSamples"]))
    min_oos = max(2, int(min_samples * MIN_OOS_SAMPLE_RATIO))
    if split.coverage < float(thresholds.get("minCoverage", 0.6)):
        return "F1"
    if int(getattr(split.res_is, "occurrences", 0)) < min_samples:
        return "F1"
    if int(getattr(split.res_oos, "occurrences", 0)) < min_oos:
        return "F1"
    return ""


def _check_is_gates(split: SplitEvaluation, thresholds: dict, ir_gate: float) -> str:
    """F2 / F3(+F11) / F4 / F5 / F7 / F8：样本内的强度、显著性、稳定性、单调性与成本。"""
    stat = _stat(split.res_is)
    if stat is None:
        return "F2"
    if _abs(stat.rankIc) < float(thresholds.get("minAbsIc", 0.03)):
        return "F2"
    if _abs(stat.ir) < ir_gate:
        return "F3" if ir_gate <= float(thresholds.get("minAbsIr", 0.5)) else "F11"
    if stat.tStat is None or _abs(stat.tStat) < float(thresholds.get("minAbsT", 2.0)):
        return "F4"
    stability = sign_stability(split.ic_is)
    if stability is None or stability < float(thresholds.get("minSignStability", 0.6)):
        return "F5"
    if stat.monotonicity is None or float(stat.monotonicity) < float(
            thresholds.get("minMonotonicity", 0.6)):
        return "F7"
    return ""


def _check_oos_gate(split: SplitEvaluation, thresholds: dict) -> str:
    """F6：样本外与样本内**同号**且保留 ``oosRetain`` 的强度。

    IS 的 ``rankIc`` 因定号而恒非负（这是选择的代价，由 F11 约束）；OOS 在**冻结方向**下
    可正可负，故本判据是真正的样本外检验，而非装饰。
    """
    stat_is, stat_oos = _stat(split.res_is), _stat(split.res_oos)
    if stat_is is None or stat_oos is None:
        return "F6"
    ic_is, ic_oos = stat_is.rankIc, stat_oos.rankIc
    if ic_is is None or ic_oos is None:
        return "F6"
    if float(ic_is) * float(ic_oos) <= 0.0:
        return "F6"
    retain = float(thresholds.get("oosRetain", 0.5))
    return "" if abs(float(ic_oos)) >= retain * abs(float(ic_is)) else "F6"


def _check_cost_gate(split: SplitEvaluation, ctx: ValidationContext) -> str:
    """F8：成本后 top-bottom 价差 > ``costMult × roundTripCost``（量纲见 ``round_trip_cost``）。"""
    stat = _stat(split.res_is)
    spread = getattr(stat, "topBottomSpread", None) if stat else None
    gate = float(ctx.thresholds.get("costMult", 2.0)) * round_trip_cost(
        ctx.fee_bps, ctx.slip_bps)
    return "" if (spread is not None and float(spread) > gate) else "F8"


def _pool_correlations(values: np.ndarray, ic_series: list[float],
                       pool: list[KeptEntry]) -> tuple[float, float]:
    """与 kept 池的最大 |值相关| 与最大 |分段 IC 相关|（F10 双通道的分子）。"""
    max_value_corr = 0.0
    max_ic_corr = 0.0
    for entry in pool:
        mask = np.isfinite(values) & np.isfinite(entry.values)
        if int(mask.sum()) >= 2:
            corr = _pearson_safe(values[mask], entry.values[mask])
            max_value_corr = max(max_value_corr, abs(corr))
        length = min(len(ic_series), len(entry.ic_series))
        if length >= 3:
            corr = _pearson_safe(np.asarray(ic_series[:length], dtype=float),
                                 np.asarray(entry.ic_series[:length], dtype=float))
            max_ic_corr = max(max_ic_corr, abs(corr))
    return round(max_ic_corr, 6), round(max_value_corr, 6)


def _pearson_safe(a: np.ndarray, b: np.ndarray) -> float:
    """Pearson 相关；方差退化 / 非有限 → 0.0（视为不相关，绝不臆造高相关把候选误杀）。"""
    if len(a) < 2 or float(np.std(a)) == 0.0 or float(np.std(b)) == 0.0:
        return 0.0
    value = float(np.corrcoef(a, b)[0, 1])
    return value if math.isfinite(value) else 0.0


# ============================ 主入口 ============================
def validate_candidate(node: Node, ctx: ValidationContext, *, pool: list[KeptEntry],
                       trials: int) -> ValidationOutcome:
    """跑完 11 条联合过滤，返回 ``kept`` / ``rejected`` 与全量指标（fail-closed）。

    Args:
        node: 已过审查层的表达式树。
        ctx: 数据窗 / 前瞻 / 成本 / 阈值（``memo`` 跨候选复用求值缓存）。
        pool: 当前已入库池（含**本轮**刚入库者——同轮双胞胎必须能被对方挡住）。
        trials: 截至本候选的累计试验数（F11 多重检验惩罚的自变量）。

    Returns:
        ``ValidationOutcome``；``kept`` 时携带值序列与分段 IC 序列供进池。
    """
    split = evaluate_split(node, ctx)
    thresholds = ctx.thresholds
    ir_gate = max(float(thresholds.get("minAbsIr", 0.5)), required_ir(trials, thresholds))
    proxy = turnover_proxy(split.values)
    ic_corr, value_corr = _pool_correlations(split.values, split.ic_is, pool)
    reason = (_check_sample_gates(split, thresholds)
              or _check_is_gates(split, thresholds, ir_gate)
              or _check_oos_gate(split, thresholds)
              or _check_cost_gate(split, ctx)
              or _check_turnover_gate(proxy, thresholds)
              or _check_dedupe_gate(ic_corr, value_corr, thresholds))
    metrics = _build_metrics(split, ctx, trials=trials, ir_gate=ir_gate, proxy=proxy,
                             ic_corr=ic_corr, value_corr=value_corr)
    stat = _stat(split.res_is)
    score = _abs(getattr(stat, "ir", None))
    if reason:
        return ValidationOutcome(verdict="rejected", reject_reason=reason,
                                 direction=split.direction, metrics=metrics,
                                 series=_build_series(split), score=score)
    return ValidationOutcome(verdict="kept", reject_reason="", direction=split.direction,
                             metrics=metrics, series=_build_series(split),
                             values=split.values, ic_series=split.ic_is, score=score)


def _check_turnover_gate(proxy: float | None, thresholds: dict) -> str:
    """F9：不可判定即拒（fail-closed）；超上限即拒。"""
    if proxy is None:
        return "F9"
    return "" if proxy <= float(thresholds.get("maxTurnover", 0.35)) else "F9"


def _check_dedupe_gate(ic_corr: float, value_corr: float, thresholds: dict) -> str:
    """F10：与**每一条**已入库因子的分段 IC 相关与值序列相关**双通道**都要够低。"""
    if ic_corr >= float(thresholds.get("maxIcCorr", 0.70)):
        return "F10"
    return "" if value_corr < float(thresholds.get("maxValueCorr", 0.85)) else "F10"


def _side_metrics(result, ic_series: list[float]) -> dict:
    stat = _stat(result)
    return {
        "rankIc": getattr(stat, "rankIc", None),
        "ic": getattr(stat, "ic", None),
        "ir": getattr(stat, "ir", None),
        "tStat": getattr(stat, "tStat", None),
        "monotonicity": getattr(stat, "monotonicity", None),
        "topBottomSpread": getattr(stat, "topBottomSpread", None),
        "signStability": sign_stability(ic_series),
        "samples": int(getattr(result, "occurrences", 0)),
        "bars": int(getattr(result, "bars", 0)),
    }


def _build_metrics(split: SplitEvaluation, ctx: ValidationContext, *, trials: int,
                   ir_gate: float, proxy: float | None, ic_corr: float,
                   value_corr: float) -> dict:
    """候选全量指标（camelCase，落 ``metrics_json`` 并直接喂 UI 详情抽屉）。"""
    return {
        "direction": split.direction,
        "coverage": split.coverage,
        "horizon": int(ctx.horizon),
        "trials": int(trials),
        "requiredIr": round(ir_gate, 6),
        "turnoverProxy": None if proxy is None else round(proxy, 6),
        "roundTripCost": round(round_trip_cost(ctx.fee_bps, ctx.slip_bps), 8),
        "costGate": round(float(ctx.thresholds.get("costMult", 2.0))
                          * round_trip_cost(ctx.fee_bps, ctx.slip_bps), 8),
        "maxIcCorr": ic_corr,
        "maxValueCorr": value_corr,
        "is": _side_metrics(split.res_is, split.ic_is),
        "oos": _side_metrics(split.res_oos, split.ic_oos),
    }


def _build_series(split: SplitEvaluation) -> dict:
    """详情用的**有界**序列（分段 IC ≤24 点、分位 ≤10 桶，§5.3）。"""
    buckets = getattr(split.res_is, "horizons", None) or []
    quantiles = []
    if buckets:
        quantiles = [{"q": b.q, "meanRet": b.meanRet, "count": b.count}
                     for b in (buckets[0].quantiles or [])][:10]
    return {"isIc": split.ic_is[:24], "oosIc": split.ic_oos[:24], "quantiles": quantiles}


# ============================ kept 池重建 / 刷新复评（同一实现，§2.10 承重） ============================
@dataclass
class RebuildResult:
    """重建产物：可用的 kept 池 + 复评不达标者（调用方决定记降级还是仅记录）。"""

    entries: list[KeptEntry] = field(default_factory=list)
    failures: list[tuple[int, str]] = field(default_factory=list)
    errors: list[tuple[int, str]] = field(default_factory=list)


@dataclass(frozen=True)
class KeptRow:
    """待重建的一条已入库候选（只取重建必需的四个字段，避免把 ORM 行拖进纯计算层）。"""

    candidate_id: int
    expr_text: str
    mechanism: str = ""
    skeleton: str = ""


def rebuild_kept_pool(rows: list[KeptRow], ctx: ValidationContext) -> RebuildResult:
    """在当前数据窗上重建 kept 池的去重基准，并顺带做 F2/F3/F6 轻量复评。

    **一个函数，两个调用点，不许有第二份实现**（§2.10 承重，评审 P1-4 / R-17）：
    - 续跑 / ``resume``：进第一轮之前必须先跑本函数，否则 F10 的值级去重无基准，
      每次重启都会开始收一批同质因子——而这恰是本特性的核心卖点。
    - 数据刷新后复评：窗口右移即基准失效，同样用本函数重算，并按返回的 ``failures``
      累计降级。

    Returns:
        ``RebuildResult``：``entries`` 为可用基准；``failures`` 为复评不达标（F2/F3/F6）；
        ``errors`` 为解析 / 求值异常（调用方须如实上报，绝不静默丢弃）。
    """
    result = RebuildResult()
    for row in rows:
        try:
            node = parse_expression(row.expr_text)
            validate_node(node)     # 陈旧 / 被外部改坏的表达式不得成为去重基准
            split = evaluate_split(node, ctx)
        except Exception as exc:  # noqa: BLE001 —— 单条重建失败不得拖垮整池，但必须如实记录
            result.errors.append((row.candidate_id, f"{type(exc).__name__}:{exc}"))
            continue
        stat = _stat(split.res_is)
        score = _abs(getattr(stat, "ir", None))
        reason = _light_revalidate(split, ctx.thresholds)
        if reason:
            result.failures.append((row.candidate_id, reason))
        result.entries.append(KeptEntry(
            candidate_id=row.candidate_id, expr_text=to_text(node),
            direction=split.direction, values=split.values, ic_series=split.ic_is,
            score=score, mechanism=row.mechanism, skeleton=row.skeleton))
    return result


def _light_revalidate(split: SplitEvaluation, thresholds: dict) -> str:
    """刷新后的轻量复评：**只**重算 F2 / F3 / F6（不重跑全部 11 条，§2.9）。"""
    stat = _stat(split.res_is)
    if stat is None or _abs(stat.rankIc) < float(thresholds.get("minAbsIc", 0.03)):
        return "F2"
    if _abs(stat.ir) < float(thresholds.get("minAbsIr", 0.5)):
        return "F3"
    return _check_oos_gate(split, thresholds)


def normalize_thresholds(raw: dict | None) -> dict:
    """叠加默认阈值（缺字段回落默认，单一口径；服务层与验证层共用）。"""
    merged = dict(DEFAULT_THRESHOLDS)
    for key, value in (raw or {}).items():
        if key in DEFAULT_THRESHOLDS:
            try:
                merged[key] = float(value)
            except (TypeError, ValueError):
                continue
    return merged
