"""三种策略基因的中文渲染（discovery-strategy-axes §2.3）—— **唯一真源**。

与上游 ``expression_text`` 同一纪律：UI 详情、LLM prompt、入库实体名读的都是这里的输出，
绝不各写一份。渲染只列**被挖动过**的参数（与模板默认值不同的那些），最多 5 项——把 20 个
参数原样铺出来等于没有渲染。
"""
from __future__ import annotations

from ..strategies.base import get_strategy_class

MAX_PARAM_ITEMS = 5
DESCRIBE_MAX = 512
_EPS = 1e-9


def template_name(template_key: str) -> str:
    """模板中文名（未知模板回落 key 本身，绝不吞成空串）。"""
    cls = get_strategy_class(template_key)
    return str(cls.meta.name) if cls is not None else str(template_key)


def _schema_of(template_key: str) -> dict:
    cls = get_strategy_class(template_key)
    return dict(cls.meta.params_schema) if cls is not None else {}


def _defaults_of(template_key: str) -> dict:
    cls = get_strategy_class(template_key)
    return dict(cls.meta.default_params) if cls is not None else {}


def _same(left, right) -> bool:
    try:
        return abs(float(left) - float(right)) < _EPS
    except (TypeError, ValueError):
        return left == right


def _format(value) -> str:
    if isinstance(value, bool):
        return "是" if value else "否"
    if isinstance(value, float):
        text = f"{value:.4f}".rstrip("0").rstrip(".")
        return text or "0"
    return str(value)


def param_diff(genome) -> list[dict]:
    """被挖动过的参数（默认值 vs 挖到的值）——详情抽屉的参数对照表与渲染共用。"""
    schema, defaults = _schema_of(genome.template_key), _defaults_of(genome.template_key)
    out: list[dict] = []
    for key, value in (genome.params or {}).items():
        default = defaults.get(key)
        if _same(default, value):
            continue
        entry = schema.get(key) if isinstance(schema.get(key), dict) else {}
        out.append({"key": key, "label": str(entry.get("label") or key),
                    "default": default, "value": value})
    return out


def describe_param_genome(genome) -> str:
    """``ParamGenome`` → 「暴涨动量突破 · 突破窗口 34 / 放量倍数 1.8」。"""
    diff = param_diff(genome)
    if not diff:
        return f"{template_name(genome.template_key)} · 全部参数取模板默认值"
    parts = [f"{item['label']} {_format(item['value'])}" for item in diff[:MAX_PARAM_ITEMS]]
    tail = " / …" if len(diff) > MAX_PARAM_ITEMS else ""
    return f"{template_name(genome.template_key)} · {' / '.join(parts)}{tail}"[:DESCRIBE_MAX]


def member_label(member) -> str:
    """成员的展示名：库内因子取中文名，``expr`` 成员取表达式的中文渲染。"""
    if member.factor_key == "expr":
        from .expression_text import describe_expression

        return describe_expression(str(member.params.get("expr") or ""))
    from ..engine.factors import factor_meta

    meta = factor_meta(member.factor_key)
    return str(meta["cn"]) if meta else member.factor_key


def describe_combo_genome(genome) -> str:
    """``ComboGenome`` → 「因子组合（3 成员）：隔夜溢价均值差 0.42 + 振幅扩张 0.33」。"""
    parts: list[str] = []
    for member, weight in list(zip(genome.members, genome.weights))[:MAX_PARAM_ITEMS]:
        parts.append(f"{member_label(member)} {float(weight):.2f}")
    tail = " + …" if len(genome.members) > MAX_PARAM_ITEMS else ""
    return f"因子组合（{len(genome.members)} 成员）：{' + '.join(parts)}{tail}"[:DESCRIBE_MAX]


def describe_rotation_genome(genome) -> str:
    """``RotationGenome`` → 「策略轮动（3 成员）：顺势回调多头 / 双EMA · 再平衡 24 根」。"""
    names = [template_name(member.template_key) for member in genome.members]
    rebalance = genome.policy.get("rebalanceBars")
    suffix = f" · 再平衡 {_format(rebalance)} 根" if rebalance is not None else ""
    return f"策略轮动（{len(names)} 成员）：{' / '.join(names)}{suffix}"[:DESCRIBE_MAX]


def describe_genome(kind: str, genome) -> str:
    """按轴分派中文渲染（未知轴回落空串，由调用方决定兜底）。"""
    if kind == "strategy_param":
        return describe_param_genome(genome)
    if kind == "factor_combo":
        return describe_combo_genome(genome)
    if kind == "strategy_combo":
        return describe_rotation_genome(genome)
    return ""
