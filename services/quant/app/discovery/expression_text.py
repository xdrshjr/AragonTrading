"""表达式 → 中文一句话（strategy-factor-discovery §2.3.4）—— UI 与 prompt 的**唯一真源**。

LLM 审查 prompt、候选表、详情抽屉、入库因子的 ``name`` / ``notes`` 全部读本模块，杜绝
「prompt 里的解释和 UI 里的解释不是一回事」。渲染是纯函数、无 IO、绝不抛：遇到未登记的
字段 / 算子一律回落其英文名（宁可露出原名，也不静默吞成空串）。
"""
from __future__ import annotations

from .expression import FIELDS, OPERATORS, Node, parse_expression

# 候选表 ``describe_text`` 列上限（String(512)，§5.3）；超出即截断并加省略号。
DESCRIBE_MAX_CHARS = 512

FIELD_CN: dict[str, str] = {
    "close": "收盘价",
    "open": "开盘价",
    "high": "最高价",
    "low": "最低价",
    "typical": "典型价",
    "volume": "成交量",
    "turnover": "成交额",
    "ret": "收益率",
    "overnight": "跨根跳空",
    "amplitude": "振幅",
    "body": "实体",
    "up_shadow": "上影线",
    "down_shadow": "下影线",
    "hl_ratio": "高低比",
    "hour_sin": "时段正弦",
    "hour_cos": "时段余弦",
}

# 一元时序算子的中文后缀（渲染为「N 根 X 的<后缀>」）。
_WINDOW_SUFFIX: dict[str, str] = {
    "ts_mean": "均值",
    "ts_std": "标准差",
    "ts_sum": "累和",
    "ts_max": "最大值",
    "ts_min": "最小值",
    "ema": "指数均值",
    "ts_zscore": "标准分",
    "ts_rank": "时序分位",
    "ts_skew": "偏度",
    "delta": "差分",
    "roc": "变化率",
    "ts_delay": "滞后值",
}

# 二元非窗口算子的中缀词。
_INFIX: dict[str, str] = {
    "add": "加上",
    "sub": "减去",
    "mul": "乘以",
    "div": "除以",
}

# 一元非窗口算子的后缀词。
_UNARY_SUFFIX: dict[str, str] = {
    "neg": "的相反数",
    "abs": "的绝对值",
    "sign": "的方向",
    "log1p": "的对数",
}


def field_cn(name: str) -> str:
    """字段中文名（未登记回落英文名，绝不返回空串）。"""
    return FIELD_CN.get(name) or name


def op_cn(op: str) -> str:
    """算子中文名（未登记回落算子名）。"""
    spec = OPERATORS.get(op)
    return (spec.cn if spec and spec.cn else op)


def _needs_bracket(node: Node) -> bool:
    """二元子式作为另一个二元算子的操作数时加括号，避免中文歧义。"""
    return node.op in _INFIX


def describe(node: Node) -> str:
    """把表达式树渲染成一句中文（纯函数，绝不抛）。"""
    if node.op == "field":
        return field_cn(node.field)
    if node.op == "const":
        value = float(node.const or 0.0)
        return f"{value:g}"
    if node.op in _WINDOW_SUFFIX:
        inner = describe(node.args[0])
        return f"{node.window} 根{inner}{_WINDOW_SUFFIX[node.op]}"
    if node.op == "ts_corr":
        left, right = describe(node.args[0]), describe(node.args[1])
        return f"{node.window} 根{left}与{right}的滚动相关"
    if node.op in _UNARY_SUFFIX:
        return f"{describe(node.args[0])}{_UNARY_SUFFIX[node.op]}"
    if node.op in _INFIX:
        left, right = node.args[0], node.args[1]
        left_text = f"（{describe(left)}）" if _needs_bracket(left) else describe(left)
        right_text = f"（{describe(right)}）" if _needs_bracket(right) else describe(right)
        return f"{left_text} {_INFIX[node.op]} {right_text}"
    inner = "、".join(describe(child) for child in node.args)
    return f"{op_cn(node.op)}({inner})"


def describe_text(node: Node, *, limit: int = DESCRIBE_MAX_CHARS) -> str:
    """``describe`` 的有界版本（落库列 String(512) 的唯一入口，超出截断并标省略号）。"""
    text = describe(node)
    return text if len(text) <= limit else f"{text[: max(0, limit - 1)]}…"


def describe_expression(text: str, *, limit: int = DESCRIBE_MAX_CHARS) -> str:
    """规范式字符串 → 中文（解析失败回落原文截断，供 UI 兜底渲染历史行）。"""
    try:
        return describe_text(parse_expression(text), limit=limit)
    except Exception:  # noqa: BLE001 —— 渲染绝不抛：解析失败也要给用户看见原始表达式
        raw = text or ""
        return raw if len(raw) <= limit else f"{raw[: max(0, limit - 1)]}…"


def vocabulary() -> dict:
    """字段 / 算子词表（``GET /discovery/vocabulary`` 与 prompt 同源，§2.11）。"""
    return {
        "fields": [{"key": key, "cn": field_cn(key), "dimension": dim}
                   for key, dim in FIELDS.items()],
        "operators": [{"key": key, "cn": op_cn(key), "arity": spec.arity,
                       "windowed": spec.windowed} for key, spec in OPERATORS.items()],
    }
