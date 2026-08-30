"""整理轮复核的三条**判据**（ai-trade-market-intel §3.4.3 + intel-actionability 轴 B / 轴 C）。

**纯核心**：无 IO、无网络、无 DB、无时钟（`now` 一律由调用方传入）。

本模块是从 `gate.py` 下沉出来的：actionability 轮给它加了战绩举证责任与 `keep_both` 之后，
`gate.py` 实测 851 行，越过 CLAUDE.md 的 800 行阈值，而 spec §十二 对越线给的处置就是
「把 `_review_*` 系列外移到 `intel/review.py`（纯搬运、零语义变化，阈值不放宽）」。

⚠️ **本模块绝不 import `gate`**：`gate` 反过来要在模块级 import 它，一个方向的边就够了。
落地形态是三条判据**只返回码**（`str | None`），由 `gate.review_actions` 拿着码去构造
`CurationAction`——判据不认识领域对象，因此也不需要认识 `gate`。条目走鸭子类型。
"""
from __future__ import annotations

from dataclasses import dataclass

from . import taxonomy as tax
from .conflict import KEEP_BOTH_MIN_REASON_CHARS
from .efficacy import WEAK_RECORD_MIN_REASON_CHARS, normalized_reason_len
from .taxonomy import IntelLimits


@dataclass(frozen=True)
class ReviewContext:
    """整理轮复核的两样**可选证据**（actionability 轴 B + 轴 C）。

    做成一个聚合结构体而不是给 `review_actions` 再塞两个 keyword 形参：那会让它到 6 个，
    越 CLAUDE.md「形参 ≤5」——而 spec §3.2.3 的方框正是为同一件事否掉了「往 `_review_promote`
    里塞 `reason` 与 `eff`」的写法。见 spec《实施过程发现的方案缺陷》I-3。

    ``ReviewContext()``（两项皆空）⇒ **两条新判据整段不参与** ⇒ 复核结果与本轮合入前逐字节
    相同，与 `score_item` 的 `limits=None` 同款范式。
    """

    eff: object | None = None                 # `efficacy.Efficacy`；None ⇒ 举证责任不生效
    conflicts: tuple = ()                     # 本轮列出的 `conflict.ConflictPair`
    # 先验的绝对封顶（`limits.efficacy_max_weight`）。**举证责任的门槛由它定，不由观察到的
    # 最大权重定**：后者会让一个所有类别权重都很小的库变得异常敏感（max 0.02 时门槛就成了
    # -0.01），而 spec §3.2.3 #2 写死的是 `-0.5 × efficacy_max_weight`。
    max_weight: float = 0.25

    @property
    def pair_ids(self) -> frozenset[tuple[int, int]]:
        """本轮列出的矛盾对（规范化成 ``(min, max)``，`keep_both` 的合法性判据）。"""
        return frozenset((int(p.a_id), int(p.b_id)) for p in self.conflicts or ())


def review_promote(item, to_tier: str | None, *, limits: IntelLimits,
                   now: float) -> str | None:
    """升级复核；返回驳回码或 `None`（通过）。

    **签名与函数体自 4b9c352 起一个字节未改**（评审 P0-2）：本轮新增的战绩举证责任**不在这里**
    ——它的判据需要 `reason`，而 `reason` 只存在于 `review_actions` 循环里的那个 `entry` dict，
    再塞两个 keyword 形参还会越 CLAUDE.md 的 5 个上限。改名去掉前导下划线是因为它现在跨模块被
    调用（同包里 import 一个下划线私有名，等于把它的签名变成公开契约却没有任何地方说明这件事）。
    """
    target = to_tier or tax.next_tier(item.tier)
    if target is None or tax.TIER_ORDER.get(target, -1) != tax.TIER_ORDER.get(item.tier, -1) + 1:
        return "promote_insufficient_evidence"
    if item.status != "active" or item.undated:
        return "promote_insufficient_evidence"
    if item.domain_count < limits.promote_min_domains:
        return "promote_insufficient_evidence"
    if now - item.captured_ts < limits.promote_min_age_s:
        return "promote_insufficient_evidence"
    if item.impact == "low":
        return "promote_insufficient_evidence"
    if target == "long":
        # **`abs()` 不是笔误**（closed-loop 评审 P0-1）：`now - event_ts >= 3d` 对未来事件恒为
        # 负，照 v1 写法一条已排期的解锁 / 议息**永远升不进长期**——而那恰恰是长期层语义里
        # 点名的内容。
        if abs(now - item.event_ts) < limits.promote_long_min_gap_s:
            return "promote_insufficient_evidence"
        if item.impact != "high" and item.category not in ("macro", "project"):
            return "promote_insufficient_evidence"
        # evidence-grounding §3.2.4：**仅 `strict`**（`promote_requires_quote`）。soft / off 下
        # 该判据整条不存在 ⇒ 升级路径逐字节不变。
        if limits.promote_requires_quote and not item.quoted:
            return "need_quote"
    return None


def weak_record_code(item, action_kind: str, to_tier: str | None, reason: str, *,
                     ctx: ReviewContext) -> str | None:
    """升长期时的战绩举证责任（actionability 轴 B 消费点 #2）；不触发 ⇒ ``None``。

    判据只有一条：**目标类别的历史战绩显著为负时，理由要写足**。它是一道举证责任，不是一张
    门禁——写足了照样能升，而 `review_promote` 里那 8 条既有数值门槛一个字节不动。

    「显著为负」取 ``weight < -0.5 × ctx.max_weight``：先验本身已被硬封顶在 ±max_weight，
    取一半是为了让这条判据只在**明确**为负的类别上生效，而不是在任何一个略微为负的类别上。

    Args:
        item: 目标条目（鸭子类型，只读 `category`）。
        action_kind: 复核通过的动作名。
        to_tier: 目标层级。
        reason: 模型给的理由原文（归一后数长度）。
        ctx: 战绩先验；`ctx.eff is None` ⇒ 整条判据不参与。

    Returns:
        ``"promote_weak_record"`` 或 ``None``。
    """
    if ctx.eff is None or action_kind != "promote" or to_tier != "long":
        return None
    weights = getattr(ctx.eff, "weights", {}) or {}
    floor = -0.5 * abs(float(ctx.max_weight))
    if floor >= 0.0 or float(weights.get(item.category, 0.0)) >= floor:
        return None
    if normalized_reason_len(reason) >= WEAK_RECORD_MIN_REASON_CHARS:
        return None
    return "promote_weak_record"


def keep_both_code(item_id: int, pair_id: int, reason: str, *,
                   ctx: ReviewContext) -> str | None:
    """`keep_both` 的两条服务端判据（actionability 轴 C，G-21）；通过 ⇒ ``None``。

    通过时调用方**不改任何条目的 tier / status**：`keep_both` 是一次**有记录的不作为**，
    只把双方的 `curation_note` 与 `last_curated_ts` 写上，而不是一次静默的忽略。
    """
    key = (min(int(item_id), int(pair_id)), max(int(item_id), int(pair_id)))
    if not pair_id or key not in ctx.pair_ids:
        return "keep_both_bad_pair"
    if normalized_reason_len(reason) < KEEP_BOTH_MIN_REASON_CHARS:
        return "keep_both_thin_note"
    return None
