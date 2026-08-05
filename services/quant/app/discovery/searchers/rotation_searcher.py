"""策略轮动搜索器（discovery-strategy-axes §2.3.3）—— 成员来自上游 kept 池。

**BL-11（评审 P0-2）承重**：``turnoverBps`` 是 ``engine/combo.py::_accrue`` 扣的**换手成本**，
不是策略行为参数。把它交给搜索器等于在优化目标里放一个「把成本调成 0」的旋钮——搜索会系统性
收敛到 ``turnoverBps=0``，挖出来的轮动组合「个个都赚」。它与 ``htf_seconds``（BL-6）是同一条
规则（D-16）的两个实例：凡「schema 里是数值、语义上属于成本模型或数据源选择」的参数一律冻结。

本轴**不接 LLM 生成**（D-14：成员必须来自本 run kept 池，模型无法凭空引用），LLM 份额按上游
``_fallback`` 规则回落随机探索。
"""
from __future__ import annotations

from ..backtest_lane import FROZEN_POLICY_KEYS
from ..genomes import RotationGenome

# 轮动策略里可挖的 **4 个**数值参数（``selectionMetric`` 是 enum、``regimeGating`` 是 bool，
# 由通用规则自动冻结；``turnoverBps`` 由 ``FROZEN_POLICY_KEYS`` 显式冻结）。
POLICY_LADDERS: dict[str, tuple] = {
    "rebalanceBars": (4, 8, 12, 24, 36, 48, 72, 120, 168, 240),
    "lookbackBars": (24, 48, 72, 120, 168, 240, 336, 504, 720, 1000),
    "minDwellBars": (1, 4, 8, 12, 24, 36, 48, 72, 120, 240),
    "switchMarginPct": (0.0, 0.02, 0.05, 0.08, 0.1, 0.15, 0.2, 0.3, 0.5),
}
MINABLE_POLICY_KEYS: tuple[str, ...] = tuple(POLICY_LADDERS)
PERTURB_MAX_STEPS = 4


class RotationSearcher:
    """``strategy_combo`` 轴的搜索器（成员 = ``ctx.rotation_pool`` 里的 ``ParamGenome``）。"""

    kind = "strategy_combo"

    def seed(self, rng, ctx) -> RotationGenome | None:
        members = _pick_members(rng, ctx)
        if members is None:
            return None
        return RotationGenome(members=members, policy=_seed_policy(rng))

    def mutate(self, rng, parent: RotationGenome, ctx) -> RotationGenome | None:
        """换一个成员 / 重掷一个轮动参数（各 50%）。"""
        if parent is None or not parent.members:
            return None
        if rng.random() < 0.5:
            spare = _spare_members(parent, ctx)
            if spare:
                members = list(parent.members)
                members[rng.randrange(len(members))] = spare[rng.randrange(len(spare))]
                return RotationGenome(members=tuple(members), policy=dict(parent.policy))
        return RotationGenome(members=parent.members,
                              policy=_move_policy(rng, parent.policy, step=None))

    def crossover(self, rng, a: RotationGenome, b: RotationGenome, ctx) -> RotationGenome | None:
        """成员并集截断 + 轮动参数逐项随机取父。"""
        if a is None or b is None:
            return None
        pool = list({_member_key(m): m for m in (*a.members, *b.members)}.values())
        low, high = int(ctx.rotation_size[0]), min(int(ctx.rotation_size[1]), len(pool))
        if high < low:
            return None
        picked = rng.sample(pool, rng.randint(low, high))
        policy = {key: (a.policy if rng.random() < 0.5 else b.policy).get(
            key, POLICY_LADDERS[key][0]) for key in MINABLE_POLICY_KEYS}
        return RotationGenome(members=tuple(picked), policy=policy)

    def perturb(self, rng, parent: RotationGenome, ctx, momentum) -> RotationGenome | None:
        if parent is None:
            return None
        _ = ctx
        step = int(momentum.step_for("rotation", 0, base_step=1)) if momentum else 1
        step = max(-PERTURB_MAX_STEPS, min(PERTURB_MAX_STEPS, step)) or 1
        return RotationGenome(members=parent.members,
                              policy=_move_policy(rng, parent.policy, step=step))

    def from_llm(self, payload: dict, ctx) -> RotationGenome | None:
        """D-14：本轴不接 LLM 生成（成员必须来自本 run kept 池）。"""
        _ = (payload, ctx)
        return None


def _member_key(member) -> str:
    from ..genomes import canonical_json

    return f"{member.template_key}|{canonical_json(member.params)}"


def _pick_members(rng, ctx):
    """锦标赛式互异取样：同模板可重复出现，但**参数必须不同**（靠 ``_member_key`` 去重）。"""
    unique = list({_member_key(m): m for m in ctx.rotation_pool}.values())
    low, high = int(ctx.rotation_size[0]), min(int(ctx.rotation_size[1]), len(unique))
    if high < low:
        return None
    return tuple(rng.sample(unique, rng.randint(low, high)))


def _spare_members(parent: RotationGenome, ctx) -> list:
    used = {_member_key(member) for member in parent.members}
    return [member for member in ctx.rotation_pool if _member_key(member) not in used]


def _seed_policy(rng) -> dict:
    return {key: ladder[rng.randrange(len(ladder))]
            for key, ladder in POLICY_LADDERS.items()}


def _move_policy(rng, policy: dict, *, step: int | None) -> dict:
    key = MINABLE_POLICY_KEYS[rng.randrange(len(MINABLE_POLICY_KEYS))]
    ladder = POLICY_LADDERS[key]
    current = policy.get(key, ladder[len(ladder) // 2])
    index = min(range(len(ladder)), key=lambda i: abs(float(ladder[i]) - float(current)))
    delta = step if step is not None else rng.choice([-2, -1, 1, 2])
    out = {k: policy.get(k, POLICY_LADDERS[k][len(POLICY_LADDERS[k]) // 2])
           for k in MINABLE_POLICY_KEYS}
    out[key] = ladder[max(0, min(len(ladder) - 1, index + delta))]
    return out


def assert_no_cost_params(policy: dict) -> dict:
    """搜索器产物里**绝不允许**出现成本类键（BL-11 的运行期断言，配 500 次采样门禁用例）。"""
    leaked = FROZEN_POLICY_KEYS.intersection(policy)
    if leaked:
        raise ValueError(f"轮动搜索器产出了成本类参数（禁止挖掘）：{sorted(leaked)}")
    return policy
