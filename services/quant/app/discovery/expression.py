"""因子表达式 AST（strategy-factor-discovery §2.3）—— 纯 numpy/pandas，零反向依赖。

本模块是挖掘产物的**唯一基因表示**：字段表 / 算子表 / 量纲规则 / 解析 / 规范式 / 哈希 /
骨架 / 子树枚举 / 记忆化求值全部在此收口。它**只依赖 numpy 与 pandas**——因为
``engine.factors.library._expr_factor`` 会延迟导入它，任何反向依赖都会形成
``engine ↔ discovery`` 回边（评审 P2-7）。

严格因果（R-7）：所有时序算子只用 rolling / ewm / shift(+N)，**绝不**出现 ``shift(-n)``；
数值异常（除零 / overflow / 非有限）一律就地置 NaN 并向上传播，绝不抛出。
"""
from __future__ import annotations

import hashlib
import math
import re
from dataclasses import dataclass, field as dc_field

import numpy as np
import pandas as pd

# 窗口梯子：把连续参数空间离散化，使「同结构不同窗口」的哈希不爆炸，也让参数扰动有
# 明确的「走一格」语义（§2.3.1）。窗口**只能**取梯子上的值。
WINDOW_LADDER: tuple[int, ...] = (3, 5, 8, 13, 20, 30, 45, 60, 90, 120, 180, 240)

# 复杂度护栏（§2.3.1）。MIN_NODES 即报告的「最小复杂度门槛」。
MAX_DEPTH = 5
MAX_NODES = 15
MIN_NODES = 3
MAX_DISTINCT_WINDOWS = 4

# 量纲（§2.3.1）：跨量纲运算在审查层被拒，故此处只需可判定。
DIM_PRICE = "price"
DIM_VOLUME = "volume"
DIM_QUOTE = "quote"
DIM_RATIO = "ratio"

# 字段表（叶子）：名称 → 量纲。全部从 ts/o/h/l/c/vol 派生，严格因果。
FIELDS: dict[str, str] = {
    "close": DIM_PRICE,
    "open": DIM_PRICE,
    "high": DIM_PRICE,
    "low": DIM_PRICE,
    "typical": DIM_PRICE,
    "volume": DIM_VOLUME,
    "turnover": DIM_QUOTE,
    "ret": DIM_RATIO,
    "overnight": DIM_RATIO,
    "amplitude": DIM_RATIO,
    "body": DIM_RATIO,
    "up_shadow": DIM_RATIO,
    "down_shadow": DIM_RATIO,
    "hl_ratio": DIM_RATIO,
    "hour_sin": DIM_RATIO,
    "hour_cos": DIM_RATIO,
}


class ExpressionError(ValueError):
    """表达式非法（解析失败 / 未知算子 / 窗口越梯 / 量纲冲突 / 复杂度越界）。

    消息一律带定位线索（出错位置与期望符号），供审查层如实回显给用户与 LLM。
    """


@dataclass(frozen=True)
class OpSpec:
    """单个算子的元信息（元数 / 是否带窗口 / 是否可交换 / 中文名）。"""

    arity: int
    windowed: bool
    commutative: bool = False
    cn: str = ""


OPERATORS: dict[str, OpSpec] = {
    "ts_mean": OpSpec(1, True, cn="均值"),
    "ts_std": OpSpec(1, True, cn="标准差"),
    "ts_sum": OpSpec(1, True, cn="求和"),
    "ts_max": OpSpec(1, True, cn="最大值"),
    "ts_min": OpSpec(1, True, cn="最小值"),
    "ema": OpSpec(1, True, cn="指数均值"),
    "ts_zscore": OpSpec(1, True, cn="标准分"),
    "ts_rank": OpSpec(1, True, cn="时序分位"),
    "ts_skew": OpSpec(1, True, cn="偏度"),
    "delta": OpSpec(1, True, cn="差分"),
    "roc": OpSpec(1, True, cn="变化率"),
    "ts_delay": OpSpec(1, True, cn="滞后"),
    "ts_corr": OpSpec(2, True, cn="滚动相关"),
    "add": OpSpec(2, False, commutative=True, cn="加"),
    "sub": OpSpec(2, False, cn="减"),
    "mul": OpSpec(2, False, commutative=True, cn="乘"),
    "div": OpSpec(2, False, cn="除"),
    "neg": OpSpec(1, False, cn="取负"),
    "abs": OpSpec(1, False, cn="绝对值"),
    "sign": OpSpec(1, False, cn="符号"),
    "log1p": OpSpec(1, False, cn="对数"),
}

# 量纲不变（入参量纲即出参量纲）的一元时序算子。
_DIM_PRESERVING = ("ts_mean", "ts_std", "ts_sum", "ts_max", "ts_min", "ema",
                   "delta", "ts_delay")
# 恒产出 ratio 的算子（归一化出口）。
_DIM_RATIO_OUT = ("ts_zscore", "ts_rank", "ts_skew", "roc", "ts_corr", "sign")


@dataclass(frozen=True)
class Node:
    """表达式树节点（不可变，可哈希；``args`` 为 tuple 以保持可哈希）。"""

    op: str                              # "field" | "const" | OPERATORS 中的算子名
    field: str = ""                      # op == "field"
    const: float | None = None           # op == "const"
    window: int | None = None            # 时序算子窗口（必须落在 WINDOW_LADDER 上）
    args: tuple["Node", ...] = dc_field(default_factory=tuple)


# ============================ 构造器（供搜索器使用） ============================
def field_node(name: str) -> Node:
    """构造字段叶子节点（未知字段即 ``ExpressionError``）。"""
    if name not in FIELDS:
        raise ExpressionError(f"未知字段：{name}")
    return Node(op="field", field=name)


def const_node(value: float) -> Node:
    """构造常数叶子节点（非有限值即 ``ExpressionError``）。"""
    number = float(value)
    if not math.isfinite(number):
        raise ExpressionError(f"常数必须有限：{value}")
    return Node(op="const", const=number)


def op_node(op: str, args: tuple[Node, ...], window: int | None = None) -> Node:
    """构造算子节点（元数 / 窗口合法性一次性校验，交换律算子在此规范化子序）。"""
    spec = OPERATORS.get(op)
    if spec is None:
        raise ExpressionError(f"未知算子：{op}")
    if len(args) != spec.arity:
        raise ExpressionError(f"算子 {op} 需要 {spec.arity} 个参数，收到 {len(args)}")
    if spec.windowed:
        if window is None or int(window) not in WINDOW_LADDER:
            raise ExpressionError(f"算子 {op} 的窗口 {window} 不在窗口梯子上")
        window = int(window)
    else:
        window = None
    ordered = tuple(sorted(args, key=to_text)) if spec.commutative else tuple(args)
    return Node(op=op, args=ordered, window=window)


# ============================ 规范式 / 哈希 / 骨架 ============================
def _format_const(value: float) -> str:
    text = f"{float(value):g}"
    return text


def to_text(node: Node) -> str:
    """前缀式规范序列化（参数内联）；交换律算子的子树按字典序排序 ⇒ 交换即同哈希。"""
    if node.op == "field":
        return node.field
    if node.op == "const":
        return _format_const(node.const or 0.0)
    parts = [to_text(child) for child in node.args]
    if OPERATORS[node.op].commutative:
        parts.sort()
    if node.window is not None:
        parts.append(str(node.window))
    return f"{node.op}({','.join(parts)})"


def expr_hash(node: Node) -> str:
    """规范式的 sha256 前 16 位——**已测集合的唯一键**（D-3 的物理去重键）。"""
    return hashlib.sha256(to_text(node).encode("utf-8")).hexdigest()[:16]


def skeleton(node: Node) -> str:
    """结构骨架：叶子 → ``?``、窗口 → ``_``（FSA 频繁子树规避的键，§2.8）。"""
    if node.op in ("field", "const"):
        return "?"
    parts = [skeleton(child) for child in node.args]
    if OPERATORS[node.op].commutative:
        parts.sort()
    if node.window is not None:
        parts.append("_")
    return f"{node.op}({','.join(parts)})"


# ============================ 结构度量与子树枚举 ============================
def node_count(node: Node) -> int:
    """节点总数（含叶子）。"""
    return 1 + sum(node_count(child) for child in node.args)


def depth(node: Node) -> int:
    """树深（叶子为 1）。"""
    return 1 + max((depth(child) for child in node.args), default=0)


def windows_of(node: Node) -> list[int]:
    """自顶向下收集全部窗口取值（含重复，供「不同窗口种类 ≤4」护栏与扰动定位）。"""
    out: list[int] = [] if node.window is None else [int(node.window)]
    for child in node.args:
        out.extend(windows_of(child))
    return out


def fields_of(node: Node) -> list[str]:
    """收集全部字段叶子名（含重复；机制族分类与数据驱动分布共用）。"""
    if node.op == "field":
        return [node.field]
    out: list[str] = []
    for child in node.args:
        out.extend(fields_of(child))
    return out


def ops_of(node: Node) -> list[str]:
    """收集全部算子名（含重复；机制族分类与审查规则共用）。"""
    if node.op in ("field", "const"):
        return []
    out = [node.op]
    for child in node.args:
        out.extend(ops_of(child))
    return out


def subtrees(node: Node, path: tuple[int, ...] = ()) -> list[tuple[tuple[int, ...], Node]]:
    """枚举 ``(路径, 子树)``（含根，前序）——交叉与变异的替换点全集。"""
    out: list[tuple[tuple[int, ...], Node]] = [(path, node)]
    for index, child in enumerate(node.args):
        out.extend(subtrees(child, path + (index,)))
    return out


def replace_at(node: Node, path: tuple[int, ...], new_node: Node) -> Node:
    """把 ``path`` 处的子树替换为 ``new_node``（纯函数，返回新树；交换律子序自动规范化）。"""
    if not path:
        return new_node
    index = path[0]
    if index >= len(node.args):
        raise ExpressionError(f"替换路径越界：{path}")
    children = list(node.args)
    children[index] = replace_at(children[index], path[1:], new_node)
    return op_node(node.op, tuple(children), node.window)


def set_window_at(node: Node, path: tuple[int, ...], window: int) -> Node:
    """把 ``path`` 处节点的窗口改为 ``window``（参数扰动唯一入口；非窗口节点即报错）。"""
    target = node
    for index in path:
        target = target.args[index]
    if target.window is None:
        raise ExpressionError("目标节点无窗口参数，无法扰动")
    return replace_at(node, path, op_node(target.op, target.args, int(window)))


# ============================ 量纲推断与复杂度校验 ============================
def _dim_binary(op: str, left: str, right: str) -> str:
    """二元算子量纲规则（§2.3.1 表）：违反即 ``ExpressionError``（审查层据此拒绝）。"""
    if op in ("add", "sub"):
        if left != right:
            raise ExpressionError(f"{op} 两侧量纲必须相同（{left} vs {right}）")
        return left
    if op == "mul":
        if left == DIM_RATIO:
            return right
        if right == DIM_RATIO:
            return left
        raise ExpressionError(f"mul 至少一侧须为 ratio（{left} vs {right}）")
    if op == "div":
        if left == right:
            return DIM_RATIO
        if right == DIM_RATIO:
            return left
        raise ExpressionError(f"div 异量纲且分母非 ratio（{left} vs {right}）")
    raise ExpressionError(f"未知二元算子：{op}")


def infer_dimension(node: Node) -> str:
    """推断整棵树的量纲；任一处违反 §2.3.1 规则即抛 ``ExpressionError``。"""
    if node.op == "field":
        return FIELDS[node.field]
    if node.op == "const":
        return DIM_RATIO
    child_dims = [infer_dimension(child) for child in node.args]
    if node.op in _DIM_RATIO_OUT:
        return DIM_RATIO
    if node.op in _DIM_PRESERVING:
        return child_dims[0]
    if node.op in ("neg", "abs"):
        return child_dims[0]
    if node.op == "log1p":
        if child_dims[0] != DIM_RATIO:
            raise ExpressionError("log1p 只接受 ratio 量纲入参")
        return DIM_RATIO
    return _dim_binary(node.op, child_dims[0], child_dims[1])


def validate_node(node: Node) -> None:
    """结构 + 量纲联合校验（复杂度护栏 + 量纲规则），非法即 ``ExpressionError``。"""
    count = node_count(node)
    if count > MAX_NODES:
        raise ExpressionError(f"节点数 {count} 超过上限 {MAX_NODES}")
    if count < MIN_NODES:
        raise ExpressionError(f"节点数 {count} 低于最小复杂度门槛 {MIN_NODES}")
    tree_depth = depth(node)
    if tree_depth > MAX_DEPTH:
        raise ExpressionError(f"树深 {tree_depth} 超过上限 {MAX_DEPTH}")
    distinct = len(set(windows_of(node)))
    if distinct > MAX_DISTINCT_WINDOWS:
        raise ExpressionError(f"不同窗口种类 {distinct} 超过上限 {MAX_DISTINCT_WINDOWS}")
    infer_dimension(node)


def is_valid(node: Node) -> bool:
    """``validate_node`` 的布尔封装（生成器批量筛候选用，不吞定位信息以外的东西）。"""
    try:
        validate_node(node)
    except ExpressionError:
        return False
    return True


# ============================ 解析（严格，带定位信息） ============================
_TOKEN_RE = re.compile(r"\s*(?:(?P<punc>[(),])|(?P<num>-?\d+(?:\.\d+)?)|(?P<name>[A-Za-z_][A-Za-z0-9_]*))")


def _tokenize(text: str) -> list[tuple[str, str, int]]:
    tokens: list[tuple[str, str, int]] = []
    pos = 0
    while pos < len(text):
        if text[pos].isspace():
            pos += 1
            continue
        match = _TOKEN_RE.match(text, pos)
        if match is None:
            raise ExpressionError(f"第 {pos} 位出现非法字符 {text[pos]!r}")
        kind = match.lastgroup or ""
        tokens.append((kind, match.group(kind), match.start(kind)))
        pos = match.end()
    return tokens


class _Parser:
    """递归下降解析器（一次性构造，解析后即弃）。"""

    def __init__(self, text: str):
        self.text = text
        self.tokens = _tokenize(text)
        self.index = 0

    def peek(self) -> tuple[str, str, int] | None:
        return self.tokens[self.index] if self.index < len(self.tokens) else None

    def take(self, expect: str = "") -> tuple[str, str, int]:
        token = self.peek()
        if token is None:
            raise ExpressionError(f"表达式在末尾意外结束（期望 {expect or 'token'}）")
        self.index += 1
        return token

    def expect_punc(self, punc: str) -> None:
        kind, value, at = self.take(punc)
        if kind != "punc" or value != punc:
            raise ExpressionError(f"第 {at} 位期望 {punc!r}，实际 {value!r}")

    def parse(self) -> Node:
        node = self.parse_node()
        if self.index != len(self.tokens):
            _kind, value, at = self.tokens[self.index]
            raise ExpressionError(f"第 {at} 位出现多余内容 {value!r}")
        return node

    def parse_node(self) -> Node:
        kind, value, at = self.take("字段 / 常数 / 算子")
        if kind == "num":
            return const_node(float(value))
        if kind != "name":
            raise ExpressionError(f"第 {at} 位期望标识符，实际 {value!r}")
        nxt = self.peek()
        if nxt is None or nxt[0] != "punc" or nxt[1] != "(":
            return field_node(value)
        return self.parse_call(value, at)

    def parse_call(self, op: str, at: int) -> Node:
        spec = OPERATORS.get(op)
        if spec is None:
            raise ExpressionError(f"第 {at} 位出现未知算子 {op!r}")
        self.expect_punc("(")
        args: list[Node] = []
        for i in range(spec.arity):
            if i:
                self.expect_punc(",")
            args.append(self.parse_node())
        window: int | None = None
        if spec.windowed:
            self.expect_punc(",")
            kind, value, pos = self.take("窗口整数")
            if kind != "num" or "." in value:
                raise ExpressionError(f"第 {pos} 位期望窗口整数，实际 {value!r}")
            window = int(value)
        self.expect_punc(")")
        return op_node(op, tuple(args), window)


def parse_expression(text: str) -> Node:
    """严格解析规范式；任何非法 token / 未知算子 / 窗口越梯即 ``ExpressionError``（含定位）。"""
    raw = (text or "").strip()
    if not raw:
        raise ExpressionError("表达式为空")
    return _Parser(raw).parse()


# ============================ 求值上下文与算子实现 ============================
def build_context(df: pd.DataFrame) -> dict[str, np.ndarray]:
    """从 K 线 DataFrame（列 ts/o/h/l/c/vol，升序）派生全部字段数组（严格因果）。

    ``hour_sin`` / ``hour_cos`` 在非日内周期（中位 ts 间隔 ≥ 1 天）恒 0——日内周期性对日线
    没有意义，返回常数比返回噪声诚实。
    """
    work = df.reset_index(drop=True)
    close = work["c"].to_numpy(dtype=float)
    open_ = work["o"].to_numpy(dtype=float)
    high = work["h"].to_numpy(dtype=float)
    low = work["l"].to_numpy(dtype=float)
    vol = work["vol"].to_numpy(dtype=float)
    ts = work["ts"].to_numpy(dtype=float)
    prev_close = _shift(close, 1)
    ctx: dict[str, np.ndarray] = {
        "close": close, "open": open_, "high": high, "low": low,
        "typical": (high + low + close) / 3.0,
        "volume": vol, "turnover": vol * close,
        "ret": _safe_div(close, prev_close) - 1.0,
        "overnight": _safe_div(open_, prev_close) - 1.0,
        "amplitude": _safe_div(high - low, close),
        "body": _safe_div(close - open_, close),
        "up_shadow": _safe_div(high - np.maximum(open_, close), close),
        "down_shadow": _safe_div(np.minimum(open_, close) - low, close),
        "hl_ratio": _safe_div(high, low) - 1.0,
    }
    ctx["hour_sin"], ctx["hour_cos"] = _hour_cycles(ts)
    return {key: _finite(value) for key, value in ctx.items()}


def _hour_cycles(ts: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """UTC 小时的正余弦（日内周期性）；非日内周期恒 0（诚实降级，绝不返回噪声）。"""
    zeros = np.zeros(len(ts))
    if len(ts) < 2:
        return zeros, zeros.copy()
    step = float(np.median(np.diff(ts)))
    if not math.isfinite(step) or step >= 86400.0:
        return zeros, zeros.copy()
    hour = (ts // 3600.0) % 24.0
    angle = 2.0 * math.pi * hour / 24.0
    return np.sin(angle), np.cos(angle)


def _finite(values: np.ndarray) -> np.ndarray:
    """把 ±inf 归一为 NaN（数值异常一律降级为缺失，绝不外泄 inf 污染下游统计）。"""
    out = np.asarray(values, dtype=float)
    return np.where(np.isfinite(out), out, np.nan)


def _safe_div(num: np.ndarray, den: np.ndarray) -> np.ndarray:
    """逐元素除法，分母为 0 / NaN → NaN（绝不抛、绝不产生 inf）。"""
    with np.errstate(divide="ignore", invalid="ignore"):
        out = np.asarray(num, dtype=float) / np.where(
            np.asarray(den, dtype=float) == 0.0, np.nan, den)
    return _finite(out)


def _shift(values: np.ndarray, periods: int) -> np.ndarray:
    """因果位移 ``x[i-periods]``（periods > 0）；越界补 NaN。**绝不**接受负 periods。"""
    if periods <= 0:
        raise ExpressionError("位移必须为正（严禁前视）")
    out = np.full(len(values), np.nan)
    if periods < len(values):
        out[periods:] = values[:-periods]
    return out


def _roll(values: np.ndarray, window: int, how: str) -> np.ndarray:
    series = pd.Series(values).rolling(window=window, min_periods=window)
    if how == "std":
        return series.std(ddof=0).to_numpy()
    return getattr(series, how)().to_numpy()


def _ts_rank(values: np.ndarray, window: int) -> np.ndarray:
    """窗口内时序分位 ``[0,1]``（报告 ``rank_cs`` 的时序对应物）；窗内含 NaN → NaN。"""
    n = len(values)
    out = np.full(n, np.nan)
    if n < window:
        return out
    view = np.lib.stride_tricks.sliding_window_view(values, window)
    valid = np.isfinite(view).all(axis=1)
    last = view[:, -1][:, None]
    with np.errstate(invalid="ignore"):
        ranks = (view <= last).sum(axis=1) / float(window)
    out[window - 1:] = np.where(valid, ranks, np.nan)
    return out


def _ts_zscore(values: np.ndarray, window: int) -> np.ndarray:
    mean = _roll(values, window, "mean")
    std = _roll(values, window, "std")
    return _safe_div(values - mean, np.where(std == 0.0, np.nan, std))


def _apply_unary(op: str, arg: np.ndarray, window: int | None) -> np.ndarray:
    if op in ("ts_mean", "ts_sum", "ts_max", "ts_min", "ts_skew"):
        return _roll(arg, int(window or 0), op.replace("ts_", ""))
    if op == "ts_std":
        return _roll(arg, int(window or 0), "std")
    if op == "ema":
        span = int(window or 1)
        return pd.Series(arg).ewm(span=span, adjust=False, min_periods=span).mean().to_numpy()
    if op == "ts_zscore":
        return _ts_zscore(arg, int(window or 0))
    if op == "ts_rank":
        return _ts_rank(arg, int(window or 0))
    if op == "delta":
        return arg - _shift(arg, int(window or 0))
    if op == "roc":
        return _safe_div(arg, _shift(arg, int(window or 0))) - 1.0
    if op == "ts_delay":
        return _shift(arg, int(window or 0))
    if op == "neg":
        return -arg
    if op == "abs":
        return np.abs(arg)
    if op == "sign":
        return np.sign(arg)
    if op == "log1p":
        with np.errstate(divide="ignore", invalid="ignore"):
            return np.log1p(np.where(arg > -1.0, arg, np.nan))
    raise ExpressionError(f"未实现的一元算子：{op}")


def _apply_binary(op: str, left: np.ndarray, right: np.ndarray,
                  window: int | None) -> np.ndarray:
    if op == "ts_corr":
        span = int(window or 0)
        return (pd.Series(left).rolling(window=span, min_periods=span)
                .corr(pd.Series(right)).to_numpy())
    if op == "add":
        return left + right
    if op == "sub":
        return left - right
    if op == "mul":
        return left * right
    if op == "div":
        return _safe_div(left, right)
    raise ExpressionError(f"未实现的二元算子：{op}")


def evaluate(node: Node, ctx: dict[str, np.ndarray],
             memo: dict[str, np.ndarray] | None = None) -> np.ndarray:
    """求值整棵树（记忆化键 = ``to_text``，交叉产生的共享子树只算一次）。

    Args:
        node: 已通过 ``validate_node`` 的表达式树。
        ctx: ``build_context`` 产物。
        memo: 跨候选复用的记忆化表（同一 run 的同窗口数据可安全共享）。

    Returns:
        与 ctx 中字段等长的 ``np.ndarray``；任何数值异常就地置 NaN，**绝不抛**。
    """
    cache = memo if memo is not None else {}
    key = to_text(node)
    hit = cache.get(key)
    if hit is not None:
        return hit
    length = len(next(iter(ctx.values()))) if ctx else 0
    if node.op == "field":
        out = ctx.get(node.field)
        out = np.full(length, np.nan) if out is None else out
    elif node.op == "const":
        out = np.full(length, float(node.const or 0.0))
    else:
        args = [evaluate(child, ctx, cache) for child in node.args]
        try:
            with np.errstate(all="ignore"):
                out = (_apply_unary(node.op, args[0], node.window) if len(args) == 1
                       else _apply_binary(node.op, args[0], args[1], node.window))
        except (ExpressionError, ValueError, TypeError, FloatingPointError):
            out = np.full(length, np.nan)
    out = _finite(out)
    cache[key] = out
    return out


def evaluate_expression_text(df: pd.DataFrame, text: str) -> pd.Series:
    """规范式 → 与 df 等长、index ``0..n-1`` 的因子值 Series（``expr`` 桥的求值入口）。

    降级契约（§2.2，评审 P1-1 承重）：解析 / 校验 / 求值任一步失败一律返回**与 df 等长、
    index 0..n-1 的全 NaN Series**——既有 ``test_factor_library`` 的因果性 / 形状用例会以
    ``params=None`` 遍历到本条目，返回空 Series 或长度不符即打红。
    """
    work = df.reset_index(drop=True)
    empty = pd.Series(np.nan, index=work.index, dtype="float64")
    try:
        node = parse_expression(text)
        validate_node(node)
        values = evaluate(node, build_context(work))
    except (ExpressionError, KeyError, ValueError, TypeError):
        return empty
    if len(values) != len(work):
        return empty
    return pd.Series(values, index=work.index, dtype="float64")
