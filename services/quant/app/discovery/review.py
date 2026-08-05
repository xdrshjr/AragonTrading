"""审查层第一道：确定性规则（strategy-factor-discovery §2.6）—— 纯函数 + 中文拒绝原因。

九条规则按「先便宜后昂贵」排序：前八条是纯结构判定（O(节点数)），只有幸存者才付出
``constant_or_empty`` 的求值代价。⚠️ ``constant_or_empty`` **不是纯函数规则**（评审 P2-3）：
它需要真正在数据窗上求值，故 ① 单独排在规则链末位；② 与验证层**共用同一份记忆化求值结果**
（记忆化键 = ``to_text``），求值发生一次、两处复用，严禁审查算一遍验证再算一遍；
③ 随验证层一起在 ``run_in_threadpool`` 中执行，绝不在事件循环里跑。
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .expression import (
    ExpressionError,
    Node,
    infer_dimension,
    subtrees,
    to_text,
    validate_node,
)
from .genomes import Candidate

# 「同算子直接嵌套即退化」的算子集：这些算子幂等或近似幂等，套两层不产生新信息。
# 刻意**不含** ``ema`` / ``delta`` / ``ts_std`` / ``ts_sum`` / ``ts_skew`` / ``ts_delay``——
# 双重平滑、二阶差分等是真实存在的技术，把它们一并禁掉等于无谓地砍掉搜索空间。
DEGENERATE_SAME_OP = frozenset({
    "ts_rank", "ts_mean", "ts_zscore", "ts_max", "ts_min", "sign", "abs", "neg", "log1p"})

DEFAULT_VARIANT_CAP = 6          # 同骨架已入库变体数上限
BLACKLIST_MIN_TESTED = 20        # 骨架进失败模式库所需的最少测试次数
DEFAULT_MIN_COVERAGE = 0.6       # constant_or_empty 的有限值比例下限

# 规则 → 中文拒绝原因（UI 与 prompt 共用真源；候选表的 rejectReason 存英文码，展示读这里）。
REVIEW_REASON_CN: dict[str, str] = {
    "degenerate_outer": "外层算子退化（同算子直接嵌套，不产生新信息）",
    "homogeneous_simplify": "可被恒等化简（如 x−x、x÷x、x+0、x×1）",
    "dimension_mismatch": "跨量纲运算（加减两侧量纲不同 / 乘除量纲不可约）",
    "complexity_out_of_range": "复杂度越界（深度 / 节点数 / 窗口种类超限）",
    "duplicate": "本任务已测过同一表达式",
    "fsa_banned": "结构骨架已被频繁子树规避机制冻结",
    "variant_cap": "同骨架的已入库变体数已达上限",
    "skeleton_blacklisted": "该骨架屡试不中，已进失败模式库",
    "constant_or_empty": "在数据窗上近乎常数或有效值过少",
    # 策略轴的确定性规则（discovery-strategy-axes §2.7）。
    "degenerate_params": "全部可挖参数都等于模板默认值（等于没挖）",
    "degenerate_members": "去重后成员不足 2 个，或存在权重过小的摆设成员",
    "duplicate_members": "存在两个完全相同的成员",
    "risk_inconsistent": "止损不小于止盈（必亏结构）",
    "window_inconsistent": "快窗不小于慢窗（参数对语义颠倒）",
}

# 「快窗 / 慢窗」语义参数对声明表（``window_inconsistent`` 判据）。用声明表而非猜名字：
# 猜错了会把合法候选误杀，而误杀在长跑里表现为「入库率偏低」，查不出来。
FAST_SLOW_PAIRS: dict[str, tuple[tuple[str, str], ...]] = {
    "ema_cross_scalper": (("fast", "slow"),),
    "trend_pullback_long": (("htf_ema_fast", "htf_ema_slow"),),
    "trend_pullback_short": (("htf_ema_fast", "htf_ema_slow"),),
    "trend_band_swing": (("trend_ema_fast", "trend_ema_slow"),),
    "bollinger_reversion": (("rsi_os", "rsi_ob"),),
}
MIN_MEMBER_WEIGHT = 0.05
MIN_COMBO_MEMBERS = 2


@dataclass
class ReviewContext:
    """审查所需的 run 级状态（全部只读，由 runtime 每轮装配）。"""

    tested_hashes: set[str] = field(default_factory=set)
    banned_skeletons: set[str] = field(default_factory=set)
    kept_skeleton_counts: dict[str, int] = field(default_factory=dict)
    skeleton_stats: dict[str, dict] = field(default_factory=dict)
    variant_cap: int = DEFAULT_VARIANT_CAP
    min_coverage: float = DEFAULT_MIN_COVERAGE


def _is_degenerate_outer(node: Node) -> bool:
    """任一节点与其**直接**子节点同算子且属幂等集 ⇒ 退化。"""
    for _path, sub in subtrees(node):
        if sub.op not in DEGENERATE_SAME_OP:
            continue
        if any(child.op == sub.op for child in sub.args):
            return True
    return False


def _is_const(node: Node, value: float) -> bool:
    return node.op == "const" and abs(float(node.const or 0.0) - value) < 1e-12


def _is_homogeneous(node: Node) -> bool:
    """存在可被恒等化简的子式（``sub(x,x)`` / ``div(x,x)`` / ``add(x,0)`` / ``mul(x,1)`` 等）。"""
    for _path, sub in subtrees(node):
        if len(sub.args) != 2:
            continue
        left, right = sub.args
        same = to_text(left) == to_text(right)
        if sub.op in ("sub", "div") and same:
            return True
        if sub.op == "add" and (_is_const(left, 0.0) or _is_const(right, 0.0)):
            return True
        if sub.op == "mul" and any(_is_const(arg, v) for arg in sub.args for v in (0.0, 1.0)):
            return True
        if sub.op == "sub" and _is_const(right, 0.0):
            return True
        if sub.op == "div" and _is_const(right, 1.0):
            return True
    return False


def _shared_reason(ctx: ReviewContext, genome_hash: str, skeleton_key: str) -> str:
    """四条**全轴共用**的规则：去重 / FSA 冻结 / 同骨架变体上限 / 失败模式库。"""
    if genome_hash in ctx.tested_hashes:
        return "duplicate"
    if skeleton_key in ctx.banned_skeletons:
        return "fsa_banned"
    if int(ctx.kept_skeleton_counts.get(skeleton_key, 0)) >= max(1, int(ctx.variant_cap)):
        return "variant_cap"
    stats = ctx.skeleton_stats.get(skeleton_key) or {}
    if int(stats.get("tested", 0)) >= BLACKLIST_MIN_TESTED and int(stats.get("kept", 0)) == 0:
        return "skeleton_blacklisted"
    return ""


def _structural_reason(node: Node, ctx: ReviewContext, genome_hash: str,
                       skeleton_key: str) -> str:
    """前八条纯结构规则（顺序即代价，命中即返回码）。"""
    if _is_degenerate_outer(node):
        return "degenerate_outer"
    if _is_homogeneous(node):
        return "homogeneous_simplify"
    try:
        infer_dimension(node)
    except ExpressionError:
        return "dimension_mismatch"
    try:
        validate_node(node)
    except ExpressionError:
        return "complexity_out_of_range"
    return _shared_reason(ctx, genome_hash, skeleton_key)


def review_structure(candidate: Candidate, ctx: ReviewContext) -> str:
    """跑前八条纯结构规则，返回拒绝码（``""`` 即通过本道）。"""
    return _structural_reason(candidate.genome.node, ctx, candidate.genome_hash,
                              candidate.skeleton)


# ============================ 策略轴的确定性规则（§2.7） ============================
def _param_rules(genome) -> str:
    """``strategy_param``：退化参数 / 必亏风控 / 快慢窗颠倒。"""
    from ..strategies.base import get_strategy_class

    cls = get_strategy_class(genome.template_key)
    if cls is None:
        return "degenerate_params"
    defaults = dict(cls.meta.default_params)
    schema = dict(cls.meta.params_schema)
    if all(_close(defaults.get(key), value) for key, value in (genome.params or {}).items()):
        return "degenerate_params"
    stop, take = genome.params.get("stop_loss_pct"), genome.params.get("take_profit_pct")
    if "stop_loss_pct" in schema and "take_profit_pct" in schema:
        if stop is not None and take is not None and float(stop) >= float(take):
            return "risk_inconsistent"
    for fast_key, slow_key in FAST_SLOW_PAIRS.get(genome.template_key, ()):
        fast, slow = genome.params.get(fast_key), genome.params.get(slow_key)
        if fast is not None and slow is not None and float(fast) >= float(slow):
            return "window_inconsistent"
    return ""


def _close(left, right) -> bool:
    try:
        return abs(float(left) - float(right)) < 1e-9
    except (TypeError, ValueError):
        return left == right


def _combo_rules(genome) -> str:
    """``factor_combo``：成员去重后不足 2 个 / 权重过小的摆设成员 / 完全相同的两个成员。"""
    from .genomes import canonical_json

    keys = [f"{m.factor_key}|{canonical_json(m.params)}" for m in genome.members]
    if len(set(keys)) < len(keys):
        return "duplicate_members"
    if len(set(keys)) < MIN_COMBO_MEMBERS:
        return "degenerate_members"
    if any(float(weight) < MIN_MEMBER_WEIGHT for weight in genome.weights):
        return "degenerate_members"
    return ""


def _rotation_rules(genome) -> str:
    """``strategy_combo``：成员去重后不足 2 个（引擎侧同样要求 ≥2，提前拦下省一次回测）。"""
    from .genomes import canonical_json

    keys = {f"{m.template_key}|{canonical_json(m.params)}" for m in genome.members}
    return "degenerate_members" if len(keys) < MIN_COMBO_MEMBERS else ""


_STRATEGY_RULES = {"strategy_param": _param_rules, "factor_combo": _combo_rules,
                   "strategy_combo": _rotation_rules}


def review_strategy(candidate: Candidate, ctx: ReviewContext) -> str:
    """策略轴的确定性规则链（先跑共用四条，再跑本轴专属的，命中即返回码）。"""
    reason = _shared_reason(ctx, candidate.genome_hash, candidate.skeleton)
    if reason:
        return reason
    rule = _STRATEGY_RULES.get(candidate.kind)
    return rule(candidate.genome) if rule else ""


def review_values(values: np.ndarray, ctx: ReviewContext) -> str:
    """第九条 ``constant_or_empty``：有限值比例不足或方差为 0（**需要求值**，故排末位）。

    Args:
        values: 与数据窗等长的因子值——**必须**是验证层记忆化缓存里的那一份，
            绝不为审查单独再算一遍（§2.6）。
    """
    arr = np.asarray(values, dtype=float)
    finite = arr[np.isfinite(arr)]
    if len(arr) == 0 or len(finite) / len(arr) < float(ctx.min_coverage):
        return "constant_or_empty"
    return "constant_or_empty" if float(np.std(finite)) == 0.0 else ""


def filter_rules(candidates: list[Candidate],
                 ctx: ReviewContext) -> tuple[list[Candidate], list[tuple[Candidate, str]]]:
    """批量跑纯结构规则；**同批内**的重复表达式也会被 ``duplicate`` 挡住。

    Returns:
        ``(通过者, [(被拒者, 拒绝码)])``。同批去重靠一份局部 seen 集——同一轮里生成两条
        一模一样的表达式，第二条必须被挡住，否则唯一索引会在 commit 时整轮回滚。
    """
    passed: list[Candidate] = []
    rejected: list[tuple[Candidate, str]] = []
    seen: set[str] = set()
    for candidate in candidates:
        reason = (review_structure(candidate, ctx) if candidate.kind == "factor"
                  else review_strategy(candidate, ctx))
        if not reason and candidate.genome_hash in seen:
            reason = "duplicate"
        if reason:
            rejected.append((candidate, reason))
            continue
        seen.add(candidate.genome_hash)
        passed.append(candidate)
    return passed, rejected


def reason_cn(code: str) -> str:
    """拒绝码 → 中文说明（未登记码原样返回，绝不吞成空串）。"""
    return REVIEW_REASON_CN.get(code, code)
