"""因子组合搜索器（discovery-strategy-axes §2.3.2）—— 成员 / 权重 / 阈值。

**权重与方向的三条硬约束**：
① 权重严格归一（``sum == 1.0``，六位小数残差补给最大分量——不补的话残差每次读写放大）；
② 成员 ``direction`` 一律从目录**冻结继承**，搜索器不得改写，更不得写 ``auto``
   （``validate_members`` 见 ``auto`` 直接 400）；
③ 权重的**烘焙**在 ``ComboGenome.baked_members`` 一处完成（BL-12），搜索器只负责产出数值。
"""
from __future__ import annotations

from ..genomes import ComboGenome, ComboMemberRef

THRESHOLD_LADDERS: dict[str, tuple] = {
    "enterLong": (0.1, 0.15, 0.2, 0.25, 0.3, 0.35, 0.4, 0.5, 0.6),
    "enterShort": (0.1, 0.15, 0.2, 0.25, 0.3, 0.35, 0.4, 0.5, 0.6),
    "exitBand": (0.0, 0.05, 0.1, 0.15, 0.2, 0.25),
}
PERTURB_MAX_STEPS = 2
WEIGHT_DIGITS = 6


class ComboSearcher:
    """``factor_combo`` 轴的搜索器（成员来自 ``ctx.members`` 目录）。"""

    kind = "factor_combo"

    def seed(self, rng, ctx) -> ComboGenome | None:
        size = _pick_size(rng, ctx)
        if size is None:
            return None
        members = tuple(rng.sample(list(ctx.members), size))
        return ComboGenome(members=members, weights=_dirichlet(rng, size),
                           thresholds=_seed_thresholds(rng))

    def mutate(self, rng, parent: ComboGenome, ctx) -> ComboGenome | None:
        """三选一：换掉一个成员 / 重掷一个权重后重归一 / 动一个阈值。"""
        if parent is None or not parent.members:
            return None
        choice = rng.randrange(3)
        if choice == 0:
            return _swap_member(rng, parent, ctx)
        if choice == 1:
            weights = list(parent.weights)
            weights[rng.randrange(len(weights))] = max(0.05, rng.random())
            return ComboGenome(members=parent.members, weights=_normalize(weights),
                               thresholds=dict(parent.thresholds))
        return ComboGenome(members=parent.members, weights=parent.weights,
                           thresholds=_move_threshold(rng, parent.thresholds, step=None))

    def crossover(self, rng, a: ComboGenome, b: ComboGenome, ctx) -> ComboGenome | None:
        """成员并集后随机截断到 ``comboSize``，权重取两父本对应权重的均值后重归一。"""
        if a is None or b is None:
            return None
        by_key: dict[str, tuple] = {}
        for genome in (a, b):
            for member, weight in zip(genome.members, genome.weights):
                key = member.name or member.factor_key
                current = by_key.get(key)
                by_key[key] = (member, (current[1] + float(weight)) / 2.0 if current
                               else float(weight))
        size = _pick_size(rng, ctx, cap=len(by_key))
        if size is None:
            return None
        picked = rng.sample(list(by_key.values()), size)
        return ComboGenome(members=tuple(item[0] for item in picked),
                           weights=_normalize([item[1] for item in picked]),
                           thresholds=dict(a.thresholds))

    def perturb(self, rng, parent: ComboGenome, ctx, momentum) -> ComboGenome | None:
        """只动阈值，沿动量方向走 1–2 格（结构与权重都不变）。"""
        if parent is None:
            return None
        step = int(momentum.step_for("factor_combo", 0, base_step=1)) if momentum else 1
        step = max(-PERTURB_MAX_STEPS, min(PERTURB_MAX_STEPS, step)) or 1
        _ = ctx
        return ComboGenome(members=parent.members, weights=parent.weights,
                           thresholds=_move_threshold(rng, parent.thresholds, step=step))

    def from_llm(self, payload: dict, ctx) -> ComboGenome | None:
        if not isinstance(payload, dict):
            return None
        catalog = {member.name: member for member in ctx.members}
        members: list[ComboMemberRef] = []
        for item in (payload.get("members") or []):
            name = str((item or {}).get("name") or "")
            if name not in catalog:
                return None                    # 成员不在目录内即拒，绝不凭空造成员
            members.append(catalog[name])
        low, high = ctx.combo_size
        if not (low <= len(members) <= high) or len(set(m.name for m in members)) != len(members):
            return None
        weights = payload.get("weights")
        if not isinstance(weights, list) or len(weights) != len(members):
            return None
        try:
            values = [float(w) for w in weights]
        except (TypeError, ValueError):
            return None
        return ComboGenome(members=tuple(members), weights=_normalize(values),
                           thresholds=_clamp_thresholds(payload.get("thresholds")))


# ============================ 纯函数助手 ============================
def _pick_size(rng, ctx, *, cap: int | None = None) -> int | None:
    low, high = int(ctx.combo_size[0]), int(ctx.combo_size[1])
    available = len(ctx.members) if cap is None else min(cap, len(ctx.members) or cap)
    high = min(high, available)
    if high < low:
        return None
    return rng.randint(low, high)


def _dirichlet(rng, size: int) -> tuple:
    """Dirichlet(1) 采样 = 归一化的独立指数分布样本（严格为正，避免 0 权重成员）。"""
    raw = [-1.0 * _log(rng.random()) for _ in range(size)]
    return _normalize(raw)


def _log(value: float) -> float:
    import math

    return math.log(max(value, 1e-12))


def _normalize(values: list) -> tuple:
    """归一化到严格和为 1.0（残差补给最大分量，上游 I-4 同款处理）。"""
    positive = [max(0.0, float(v)) for v in values]
    total = sum(positive)
    if total <= 0.0:
        even = round(1.0 / len(positive), WEIGHT_DIGITS)
        positive = [even] * len(positive)
        total = sum(positive)
    scaled = [round(v / total, WEIGHT_DIGITS) for v in positive]
    residual = round(1.0 - sum(scaled), WEIGHT_DIGITS)
    if residual:
        top = max(range(len(scaled)), key=lambda i: scaled[i])
        scaled[top] = round(scaled[top] + residual, WEIGHT_DIGITS)
    return tuple(scaled)


def _seed_thresholds(rng) -> dict:
    return {key: ladder[rng.randrange(len(ladder))]
            for key, ladder in THRESHOLD_LADDERS.items()}


def _move_threshold(rng, thresholds: dict, *, step: int | None) -> dict:
    key = list(THRESHOLD_LADDERS)[rng.randrange(len(THRESHOLD_LADDERS))]
    ladder = THRESHOLD_LADDERS[key]
    current = thresholds.get(key, ladder[len(ladder) // 2])
    index = min(range(len(ladder)), key=lambda i: abs(float(ladder[i]) - float(current)))
    delta = step if step is not None else rng.choice([-2, -1, 1, 2])
    return {**thresholds, key: ladder[max(0, min(len(ladder) - 1, index + delta))]}


def _clamp_thresholds(raw) -> dict:
    data = raw if isinstance(raw, dict) else {}
    out: dict = {}
    for key, ladder in THRESHOLD_LADDERS.items():
        try:
            value = float(data.get(key, ladder[len(ladder) // 2]))
        except (TypeError, ValueError):
            value = float(ladder[len(ladder) // 2])
        out[key] = min(max(value, float(ladder[0])), float(ladder[-1]))
    return out


def _swap_member(rng, parent: ComboGenome, ctx) -> ComboGenome | None:
    used = {member.name for member in parent.members}
    spare = [member for member in ctx.members if member.name not in used]
    if not spare:
        return None
    members = list(parent.members)
    members[rng.randrange(len(members))] = spare[rng.randrange(len(spare))]
    return ComboGenome(members=tuple(members), weights=_normalize(list(parent.weights)),
                       thresholds=dict(parent.thresholds))
