"""策略轴硬闸 S1–S9 与 kept 池（discovery-strategy-axes §2.6）—— fail-closed、纯计算。

与上游 D-4 同一姿态：达标与否**绝不**由 LLM 说了算。评估**按 ID 顺序**，首个未通过即返回；
最贵的 S9（一次额外 IS 回测）排在最后，只有已过 S1–S8 的候选才付这个代价。

**不可判定即拒**：任一指标为 ``None`` / ``NaN`` 一律 ``rejected``，理由形如
``S5:calmar_undefined``。fail-closed 是对的，但它的前提是「输入齐全」——缺字段时的
fail-closed 会伪装成「市场没机会」，所以 BL-13 的并集出口是这条纪律的必要条件。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from .validation import _pearson_safe, penalized_gate

DEFAULT_STRATEGY_THRESHOLDS: dict[str, float] = {
    "minTrades": 20.0,          # S1
    "minSharpe": 0.8,           # S3
    "maxDrawdown": 0.35,        # S4
    "minCalmar": 1.0,           # S5
    "oosRetain": 0.4,           # S6
    "maxTradeRate": 0.05,       # S7
    "minProfitFactor": 1.1,     # S7
    "maxEquityCorr": 0.85,      # S8
    "costStressMult": 2.0,      # S9
    "trialPenaltyK": 0.15,      # S3 的多重检验惩罚系数
}

# S 闸的中文名（``validation.FILTER_LABELS`` 是后端唯一真源；此处只保留逐条闸的短标签，
# 供详情抽屉的「闸位阶梯」直接显示，不重复造第二张拒绝原因表）。
GATE_LABELS: dict[str, str] = {
    "S1": "样本量", "S2": "样本内收益", "S3": "样本内夏普", "S4": "最大回撤",
    "S5": "Calmar", "S6": "样本外留存", "S7": "交易密度/盈亏比", "S8": "与已入库相关性",
    "S9": "成本压力",
}

OOS_TRADE_FLOOR = 3
OOS_TRADE_RATIO = 0.25


def normalize_strategy_thresholds(raw: dict | None) -> dict:
    """叠加策略轴默认阈值（缺字段回落默认，服务层与验证层共用单一口径）。"""
    merged = dict(DEFAULT_STRATEGY_THRESHOLDS)
    for key, value in (raw or {}).items():
        if key in DEFAULT_STRATEGY_THRESHOLDS:
            try:
                merged[key] = float(value)
            except (TypeError, ValueError):
                continue
    return merged


def required_sharpe(trials: int, thresholds: dict) -> float:
    """S3 的门槛（BL-4：与因子轴 F3/F11 **同一个** ``penalized_gate`` 实现）。"""
    base = float(thresholds.get("minSharpe", DEFAULT_STRATEGY_THRESHOLDS["minSharpe"]))
    k = float(thresholds.get("trialPenaltyK",
                             DEFAULT_STRATEGY_THRESHOLDS["trialPenaltyK"]))
    return max(base, penalized_gate(base, trials, k))


@dataclass
class GateReport:
    """逐条闸的实际值与门槛（落 ``series_json.gates``，详情抽屉显示「卡在哪、差多少」）。"""

    reason: str = ""
    gates: list = field(default_factory=list)
    max_equity_corr: float = 0.0
    required_sharpe: float = 0.0

    def record(self, gate_id: str, value, gate, passed: bool) -> None:
        self.gates.append({"id": gate_id, "label": GATE_LABELS.get(gate_id, gate_id),
                           "value": _round(value), "gate": _round(gate), "passed": passed})


def _round(value) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return round(number, 6) if math.isfinite(number) else None


def _metric(metrics: dict, key: str) -> float | None:
    """取指标；``None`` / 非有限一律回 ``None``（触发不可判定即拒）。"""
    value = (metrics or {}).get(key)
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _fail(report: GateReport, gate_id: str, field_name: str, value, gate) -> GateReport:
    report.record(gate_id, value, gate, False)
    report.reason = f"{gate_id}:{field_name}_undefined" if value is None else gate_id
    return report


def _check_sample(report: GateReport, is_m: dict, oos_m: dict, thresholds: dict) -> bool:
    """S1：IS 平仓笔数达标，且 OOS 也要有足够笔数才谈得上「样本外」。"""
    min_trades = float(thresholds.get("minTrades", 20.0))
    oos_floor = max(OOS_TRADE_FLOOR, min_trades * OOS_TRADE_RATIO)
    is_trades, oos_trades = _metric(is_m, "tradeCount"), _metric(oos_m, "tradeCount")
    if is_trades is None or oos_trades is None:
        _fail(report, "S1", "tradeCount", None, min_trades)
        return False
    if is_trades < min_trades or oos_trades < oos_floor:
        _fail(report, "S1", "tradeCount", min(is_trades, oos_trades), min_trades)
        return False
    report.record("S1", is_trades, min_trades, True)
    return True


def _check_is_side(report: GateReport, is_m: dict, thresholds: dict, trials: int) -> bool:
    """S2 / S3 / S4 / S5：样本内的收益、夏普（含惩罚）、回撤与 Calmar。"""
    checks = (
        ("S2", "totalReturn", _metric(is_m, "totalReturn"), 0.0, lambda v, g: v > g),
        ("S3", "sharpe", _metric(is_m, "sharpe"), report.required_sharpe,
         lambda v, g: v >= g),
        ("S4", "maxDrawdown", _metric(is_m, "maxDrawdown"),
         float(thresholds.get("maxDrawdown", 0.35)), lambda v, g: abs(v) <= g),
        ("S5", "calmar", _metric(is_m, "calmar"), float(thresholds.get("minCalmar", 1.0)),
         lambda v, g: v >= g),
    )
    for gate_id, name, value, gate, ok in checks:
        if value is None or not ok(value, gate):
            _fail(report, gate_id, name, value, gate)
            return False
        report.record(gate_id, value, gate, True)
    return True


def _check_oos(report: GateReport, is_m: dict, oos_m: dict, thresholds: dict) -> bool:
    """S6：本轮最强的样本外防线（OOS 夏普为正、留存足够，且 OOS 收益为正）。"""
    retain = float(thresholds.get("oosRetain", 0.4))
    is_sharpe, oos_sharpe = _metric(is_m, "sharpe"), _metric(oos_m, "sharpe")
    oos_return = _metric(oos_m, "totalReturn")
    gate = retain * max(0.0, is_sharpe or 0.0)
    if oos_sharpe is None or is_sharpe is None or oos_return is None:
        _fail(report, "S6", "sharpe", None, gate)
        return False
    if oos_sharpe <= 0.0 or oos_sharpe < gate or oos_return <= 0.0:
        _fail(report, "S6", "sharpe", oos_sharpe, gate)
        return False
    report.record("S6", oos_sharpe, gate, True)
    return True


def _check_density(report: GateReport, is_m: dict, thresholds: dict) -> bool:
    """S7：交易密度（按根数）与盈亏比。``tradeRate`` 由 BL-13 的并集出口显式派生。"""
    max_rate = float(thresholds.get("maxTradeRate", 0.05))
    min_pf = float(thresholds.get("minProfitFactor", 1.1))
    rate, profit_factor = _metric(is_m, "tradeRate"), _metric(is_m, "profitFactor")
    if rate is None or rate > max_rate:
        _fail(report, "S7", "tradeRate", rate, max_rate)
        return False
    if profit_factor is None or profit_factor < min_pf:
        _fail(report, "S7", "profitFactor", profit_factor, min_pf)
        return False
    report.record("S7", profit_factor, min_pf, True)
    return True


def _check_dedupe(report: GateReport, returns: list, pool: list, thresholds: dict) -> bool:
    """S8：与 kept 池中**每一条**的逐根收益序列相关性都要够低。"""
    cap = float(thresholds.get("maxEquityCorr", 0.85))
    report.max_equity_corr = max_return_corr(returns, pool)
    if report.max_equity_corr >= cap:
        _fail(report, "S8", "equityCorr", report.max_equity_corr, cap)
        return False
    report.record("S8", report.max_equity_corr, cap, True)
    return True


def max_return_corr(returns: list, pool: list) -> float:
    """与池内每一条逐根收益序列的最大 |Pearson|（长度不等时按较短者对齐前缀）。"""
    current = np.asarray(returns, dtype=float)
    best = 0.0
    for entry in pool:
        other = np.asarray(entry.bar_returns, dtype=float)
        length = min(len(current), len(other))
        if length < 2:
            continue
        best = max(best, abs(_pearson_safe(current[:length], other[:length])))
    return round(best, 6)


def check_gates(is_metrics: dict, oos_metrics: dict, *, thresholds: dict, trials: int,
                pool: list, returns: list) -> GateReport:
    """跑 S1–S8（S9 需要额外一次回测，由调用方在本函数通过后单独跑）。"""
    report = GateReport(required_sharpe=required_sharpe(trials, thresholds))
    _ = (_check_sample(report, is_metrics, oos_metrics, thresholds)
         and _check_is_side(report, is_metrics, thresholds, trials)
         and _check_oos(report, is_metrics, oos_metrics, thresholds)
         and _check_density(report, is_metrics, thresholds)
         and _check_dedupe(report, returns, pool, thresholds))
    return report


def check_stress(report: GateReport, stress_metrics: dict) -> GateReport:
    """S9：以 ``costStressMult ×`` 成本重跑 IS 段后仍须盈利（排在最后，只对幸存者收费）。"""
    total_return = _metric(stress_metrics, "totalReturn")
    if total_return is None or total_return <= 0.0:
        _fail(report, "S9", "totalReturn", total_return, 0.0)
        return report
    report.record("S9", total_return, 0.0, True)
    return report


# ============================ kept 池与重建（BL-5） ============================
@dataclass
class StrategyKeptEntry:
    """策略轴的去重基准（IS 段逐根收益序列，与因子轴的 ``values`` 同性质，**不落库**）。"""

    candidate_id: int
    kind: str
    genome_hash: str
    genome: dict
    bar_returns: list
    score: float
    family: str = ""
    skeleton: str = ""


@dataclass
class StrategyRebuildResult:
    """重建产物：可用池 + 复评不达标者 + 装配/回测异常（调用方须如实上报）。"""

    entries: list = field(default_factory=list)
    failures: list = field(default_factory=list)
    errors: list = field(default_factory=list)


@dataclass(frozen=True)
class StrategyKeptRow:
    """待重建的一条已入库策略候选（只取重建必需字段，不把 ORM 行拖进纯计算层）。"""

    candidate_id: int
    kind: str
    genome_hash: str
    genome: dict
    skeleton: str = ""
    family: str = ""


def rebuild_strategy_pool(rows: list, ctx, *, build_assembler, thresholds: dict,
                          run_split) -> StrategyRebuildResult:
    """在当前窗口上重建策略 kept 池的去重基准（**一个实现，两个调用点**，BL-5）。

    续跑与数据刷新都调本函数：窗口一右移，逐根收益序列就与新窗口不再等长，S8 会失去基准，
    每次重启后都会开始收一批同质策略。重建时**必须复跑一次装配 + 回测**（上游 I-2 的教训：
    解析得通不等于仍合法；被外部改坏或跨版本遗留的基因进池就成了一条错误的去重基准）。

    Args:
        rows: ``StrategyKeptRow`` 列表。
        ctx: ``LaneContext``。
        build_assembler: ``(kind, genome) -> assembler``（避免本模块反向依赖 Lane 层）。
        thresholds: 策略阈值（轻量复评 S2/S3/S6 用）。
        run_split: ``(assembler, ctx) -> SplitBacktest``（注入以便测试替身）。

    Returns:
        ``StrategyRebuildResult``；``entries`` 为可用基准，``failures`` 为复评不达标，
        ``errors`` 为异常。全部失败且无可用条目时由调用方转 ``paused(rebuild_failed)``。
    """
    result = StrategyRebuildResult()
    for row in rows:
        try:
            assembler = build_assembler(row.kind, row.genome)
            split = run_split(assembler, ctx)
        except Exception as exc:  # noqa: BLE001 —— 单条重建失败不得拖垮整池，但必须如实记录
            result.errors.append((row.candidate_id, f"{type(exc).__name__}:{exc}"))
            continue
        if split.reject_reason:
            result.errors.append((row.candidate_id, split.reject_reason))
            continue
        reason = light_revalidate(split.is_metrics, split.oos_metrics, thresholds)
        if reason:
            result.failures.append((row.candidate_id, reason))
        result.entries.append(StrategyKeptEntry(
            candidate_id=row.candidate_id, kind=row.kind, genome_hash=row.genome_hash,
            genome=dict(row.genome), bar_returns=_returns_of(split),
            score=float(split.is_metrics.get("sharpe") or 0.0),
            family=row.family, skeleton=row.skeleton))
    return result


def _returns_of(split) -> list:
    from .backtest_lane import bar_returns

    return bar_returns(split.is_points)


def light_revalidate(is_metrics: dict, oos_metrics: dict, thresholds: dict) -> str:
    """刷新后的轻量复评：只重算 **S2 / S3 / S6**（对位因子轴的 F2 / F3 / F6）。

    不重跑全部九条：刷新只是窗口右移，回撤 / 密度 / 相关性等口径在新窗口上重算意义不大，
    而这三条正是「这条策略在新窗口上还成立吗」的核心判据。
    """
    total_return = _metric(is_metrics, "totalReturn")
    if total_return is None or total_return <= 0.0:
        return "S2"
    sharpe = _metric(is_metrics, "sharpe")
    if sharpe is None or sharpe < float(thresholds.get("minSharpe", 0.8)):
        return "S3"
    report = GateReport()
    return "" if _check_oos(report, is_metrics, oos_metrics, thresholds) else "S6"
