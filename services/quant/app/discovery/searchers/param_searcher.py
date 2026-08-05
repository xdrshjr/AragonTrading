"""常规策略参数搜索器（discovery-strategy-axes §2.3.1）—— 梯子 + 五维演化。

搜索空间 = 每个可挖模板的 ``params_schema`` 中类型为 ``int`` / ``number`` 且不在
``FROZEN_PARAM_KEYS`` 里的条目，各自离散成一条梯子。基因 = 每个可挖参数在自己梯子上的下标。

产物必须**合法或 ``None``**：未知模板 / 参数越界 / 类型不符一律返回 ``None``，绝不放宽护栏
迁就模型（与因子轴 ``FactorSearcher.from_llm`` 同一姿态）。
"""
from __future__ import annotations

from ..genomes import ParamGenome

MAX_MUTATED_PARAMS = 3
PERTURB_MAX_STEPS = 4


class ParamSearcher:
    """``strategy_param`` 轴的搜索器（无状态；全部随机性来自传入的 ``rng``）。"""

    kind = "strategy_param"

    def seed(self, rng, ctx) -> ParamGenome | None:
        spec = _pick_template(rng, ctx)
        if spec is None:
            return None
        params = {param.key: param.ladder[rng.randrange(len(param.ladder))]
                  for param in spec.params}
        return ParamGenome(template_key=spec.key, params=params)

    def mutate(self, rng, parent: ParamGenome, ctx) -> ParamGenome | None:
        spec = _template_of(ctx, parent.template_key)
        if spec is None or not spec.params:
            return None
        params = dict(parent.params)
        count = min(len(spec.params), rng.randint(1, MAX_MUTATED_PARAMS))
        for param in rng.sample(list(spec.params), count):
            params[param.key] = _reroll(rng, param, params.get(param.key))
        return ParamGenome(template_key=parent.template_key, params=params)

    def crossover(self, rng, a: ParamGenome, b: ParamGenome, ctx) -> ParamGenome | None:
        """同模板才交叉；跨模板退化为对第一个父本变异并**留痕**（绝不静默丢失一格名额）。"""
        if a is None or b is None:
            return None
        if a.template_key != b.template_key:
            ctx.degraded["crossover_degraded"] = ctx.degraded.get("crossover_degraded", 0) + 1
            return self.mutate(rng, a, ctx)
        spec = _template_of(ctx, a.template_key)
        if spec is None:
            return None
        params = {param.key: (a.params if rng.random() < 0.5 else b.params).get(
            param.key, param.default) for param in spec.params}
        return ParamGenome(template_key=a.template_key, params=params)

    def perturb(self, rng, parent: ParamGenome, ctx, momentum) -> ParamGenome | None:
        """结构不变，只沿动量方向在梯子上走 1–4 格（复用上游 ``MomentumTracker``）。"""
        spec = _template_of(ctx, parent.template_key)
        if spec is None or not spec.params:
            return None
        params = dict(parent.params)
        param = spec.params[rng.randrange(len(spec.params))]
        slot = spec.params.index(param)
        step = int(momentum.step_for(parent.template_key, slot, base_step=1)) if momentum else 1
        step = max(-PERTURB_MAX_STEPS, min(PERTURB_MAX_STEPS, step)) or 1
        index = _index_of(param, params.get(param.key))
        params[param.key] = param.ladder[max(0, min(len(param.ladder) - 1, index + step))]
        return ParamGenome(template_key=parent.template_key, params=params)

    def from_llm(self, payload: dict, ctx) -> ParamGenome | None:
        if not isinstance(payload, dict):
            return None
        spec = _template_of(ctx, str(payload.get("templateKey") or ""))
        if spec is None:
            return None
        raw = payload.get("params")
        if not isinstance(raw, dict):
            return None
        params: dict = {}
        for param in spec.params:
            value = raw.get(param.key, param.default)
            snapped = _snap(param, value)
            if snapped is None:
                return None
            params[param.key] = snapped
        return ParamGenome(template_key=spec.key, params=params)


def _pick_template(rng, ctx):
    minable = [spec for spec in ctx.templates if spec.minable]
    return minable[rng.randrange(len(minable))] if minable else None


def _template_of(ctx, template_key: str):
    return next((spec for spec in ctx.templates
                 if spec.key == template_key and spec.minable), None)


def _index_of(param, value) -> int:
    """当前取值在梯子上的下标（不在梯子上时取最近的一格，绝不抛）。"""
    if value in param.ladder:
        return param.ladder.index(value)
    try:
        target = float(value)
    except (TypeError, ValueError):
        return 0
    return min(range(len(param.ladder)), key=lambda i: abs(float(param.ladder[i]) - target))


def _reroll(rng, param, current):
    """重掷一格且**不等于原值**（等于原值的「变异」只是白付一次回测）。"""
    if len(param.ladder) < 2:
        return param.ladder[0]
    index = _index_of(param, current)
    choices = [i for i in range(len(param.ladder)) if i != index]
    return param.ladder[choices[rng.randrange(len(choices))]]


def _snap(param, value):
    """把外部给的值吸附到梯子上；越界或类型不符一律 ``None``（护栏不放宽）。"""
    try:
        target = float(value)
    except (TypeError, ValueError):
        return None
    low, high = float(param.ladder[0]), float(param.ladder[-1])
    if target < low - 1e-9 or target > high + 1e-9:
        return None
    return param.ladder[_index_of(param, target)]
