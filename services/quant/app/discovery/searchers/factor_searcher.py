"""因子轴搜索器（strategy-factor-discovery §2.4 / §2.5）—— 五维演化的产物工厂。

产物**必须合法**（过 ``validate_node`` 的复杂度与量纲护栏）或返回 ``None``：审查层的规则是
第二道防线，不是第一道。生成走「量纲驱动的递归生长」而非「先随便长再筛」——后者在量纲规则下
的成功率极低，会让整轮预算白白空转。

所有随机性都来自调用方传入的 ``rng``（``random.Random``），故同种子必然复现同一批候选，
这是 §2.10「同 seed 同 iteration 复现同批候选」的前提。
"""
from __future__ import annotations

from ..evolution import SearchContext
from ..expression import (
    DIM_PRICE,
    DIM_RATIO,
    FIELDS,
    MAX_DEPTH,
    OPERATORS,
    WINDOW_LADDER,
    ExpressionError,
    Node,
    const_node,
    field_node,
    is_valid,
    op_node,
    parse_expression,
    replace_at,
    set_window_at,
    subtrees,
    validate_node,
)
from ..genomes import ExprGenome
from ..mechanisms import get_family

# 各量纲可用的字段（生长期按目标量纲取叶子；无可用字段时回落常数）。
_FIELDS_BY_DIM: dict[str, tuple[str, ...]] = {}
for _key, _dim in FIELDS.items():
    _FIELDS_BY_DIM.setdefault(_dim, ())
    _FIELDS_BY_DIM[_dim] = _FIELDS_BY_DIM[_dim] + (_key,)

# 恒产出 ratio 的算子（归一化出口）：目标量纲为 ratio 时可用，且对子树量纲无约束。
_RATIO_PRODUCERS = ("ts_zscore", "ts_rank", "ts_skew", "roc", "ts_corr", "sign")
# 量纲不变的一元算子：子树与自身同量纲。
_PRESERVING = ("ts_mean", "ts_std", "ts_sum", "ts_max", "ts_min", "ema", "delta",
               "ts_delay", "neg", "abs")
# 二元结构算子（量纲规则见 §2.3.1）。
_BINARY = ("add", "sub", "mul", "div")

_LEAF_PROB = 0.34          # 生长期在深度未耗尽时直接收成叶子的概率
_MAX_BUILD_ATTEMPTS = 24   # 单次生成的重试上限（超出即返回 None，绝不死循环）
_CONST_CHOICES = (0.0, 0.5, 1.0, 2.0)


class FactorSearcher:
    """表达式轴搜索器：``seed`` / ``mutate`` / ``crossover`` / ``perturb`` / ``from_llm``。"""

    kind = "factor"

    # ---------- 冷启动 / 随机探索 ----------
    def seed(self, rng, ctx: SearchContext) -> ExprGenome | None:
        """按数据驱动分布随机生成一棵合法表达式树（机制引导时偏好该族字段）。"""
        for _ in range(_MAX_BUILD_ATTEMPTS):
            depth_budget = rng.randint(2, MAX_DEPTH)
            node = self._grow(rng, ctx, DIM_RATIO, depth_budget)
            if node is not None and is_valid(node):
                return ExprGenome(node)
        return None

    # ---------- 变异 ----------
    def mutate(self, rng, parent: ExprGenome, ctx: SearchContext) -> ExprGenome | None:
        """替换父本中的一个子树（保留其余结构骨架，同时引入新信号源）。

        替换出的子树**与被替换处同量纲**，否则整棵树立刻量纲不合法，变异等于空转。
        """
        points = subtrees(parent.node)
        for _ in range(_MAX_BUILD_ATTEMPTS):
            path, target = points[rng.randrange(len(points))]
            dim = self._dim_of(target)
            if dim is None:
                continue
            budget = max(1, MAX_DEPTH - len(path))
            replacement = self._grow(rng, ctx, dim, budget)
            if replacement is None:
                continue
            try:
                candidate = replace_at(parent.node, path, replacement)
            except ExpressionError:
                continue
            if is_valid(candidate) and candidate != parent.node:
                return ExprGenome(candidate)
        return None

    # ---------- 交叉 ----------
    def crossover(self, rng, a: ExprGenome, b: ExprGenome,
                  ctx: SearchContext) -> ExprGenome | None:
        """把 a 的某个子树换成 b 的一个**同量纲**子树（融合不同来源的结构特征）。"""
        _ = ctx
        a_points, b_points = subtrees(a.node), subtrees(b.node)
        for _ in range(_MAX_BUILD_ATTEMPTS):
            path, target = a_points[rng.randrange(len(a_points))]
            dim = self._dim_of(target)
            _donor_path, donor = b_points[rng.randrange(len(b_points))]
            if dim is None or self._dim_of(donor) != dim:
                continue
            try:
                candidate = replace_at(a.node, path, donor)
            except ExpressionError:
                continue
            if is_valid(candidate) and candidate != a.node:
                return ExprGenome(candidate)
        return None

    # ---------- 参数扰动 ----------
    def perturb(self, rng, parent: ExprGenome, ctx: SearchContext,
                momentum) -> ExprGenome | None:
        """结构完全不变，只把某个时序算子的窗口沿动量方向在梯子上走 1–4 格。"""
        _ = ctx
        slots = [(path, node) for path, node in subtrees(parent.node)
                 if node.window is not None]
        if not slots:
            return None
        from ..expression import skeleton as skeleton_of

        key = skeleton_of(parent.node)
        for _ in range(_MAX_BUILD_ATTEMPTS):
            slot_index = rng.randrange(len(slots))
            path, node = slots[slot_index]
            current = WINDOW_LADDER.index(int(node.window))
            step = momentum.step_for(key, slot_index) if momentum is not None else 1
            if step == 0:
                step = 1 if rng.random() < 0.5 else -1
            target = current + step
            if not 0 <= target < len(WINDOW_LADDER):
                target = current - step
            if not 0 <= target < len(WINDOW_LADDER) or target == current:
                continue
            try:
                candidate = set_window_at(parent.node, path, WINDOW_LADDER[target])
            except ExpressionError:
                continue
            if is_valid(candidate):
                return ExprGenome(candidate)
        return None

    # ---------- LLM 产出解析 ----------
    def from_llm(self, payload: dict, ctx: SearchContext) -> ExprGenome | None:
        """解析 LLM 给出的表达式；任何非法一律 ``None``（绝不为迁就模型放宽护栏）。"""
        _ = ctx
        text = str((payload or {}).get("expr") or "").strip()
        if not text:
            return None
        try:
            node = parse_expression(text)
            validate_node(node)
        except ExpressionError:
            return None
        return ExprGenome(node)

    # ============================ 内部：量纲驱动的递归生长 ============================
    @staticmethod
    def _dim_of(node: Node) -> str | None:
        from ..expression import infer_dimension

        try:
            return infer_dimension(node)
        except ExpressionError:
            return None

    def _pick_window(self, rng, ctx: SearchContext) -> int:
        keys, weights = ctx.weighted_windows()
        return int(rng.choices(keys, weights=weights, k=1)[0])

    def _pick_field(self, rng, ctx: SearchContext, dim: str) -> str | None:
        """按数据驱动权重（叠加机制族偏好）在该量纲的字段里抽一个。"""
        pool = _FIELDS_BY_DIM.get(dim, ())
        if not pool:
            return None
        family = get_family(ctx.mechanism_hint) if ctx.mechanism_hint else None
        preferred = set(family.fields) if family else set()
        weights = [max(0.01, float(ctx.field_weights.get(key, 1.0)))
                   * (3.0 if key in preferred else 1.0) for key in pool]
        return str(rng.choices(list(pool), weights=weights, k=1)[0])

    def _leaf(self, rng, ctx: SearchContext, dim: str) -> Node | None:
        name = self._pick_field(rng, ctx, dim)
        if name is not None:
            return field_node(name)
        return const_node(_CONST_CHOICES[rng.randrange(len(_CONST_CHOICES))]) \
            if dim == DIM_RATIO else None

    def _grow(self, rng, ctx: SearchContext, dim: str, budget: int) -> Node | None:
        """生成一棵**量纲恰为 ``dim``** 的子树（深度预算 ``budget``；失败返回 None）。"""
        if budget <= 1 or rng.random() < _LEAF_PROB:
            return self._leaf(rng, ctx, dim)
        op = self._pick_operator(rng, ctx, dim)
        if op is None:
            return self._leaf(rng, ctx, dim)
        args = self._grow_args(rng, ctx, op, dim, budget - 1)
        if args is None:
            return self._leaf(rng, ctx, dim)
        window = self._pick_window(rng, ctx) if OPERATORS[op].windowed else None
        try:
            return op_node(op, args, window)
        except ExpressionError:
            return self._leaf(rng, ctx, dim)

    def _pick_operator(self, rng, ctx: SearchContext, dim: str) -> str | None:
        """列出能产出 ``dim`` 的算子并按机制族偏好加权抽取。"""
        pool = list(_PRESERVING) + list(_BINARY)
        if dim == DIM_RATIO:
            pool += list(_RATIO_PRODUCERS) + ["log1p"]
        family = get_family(ctx.mechanism_hint) if ctx.mechanism_hint else None
        preferred = set(family.ops) if family else set()
        weights = [3.0 if op in preferred else 1.0 for op in pool]
        return str(rng.choices(pool, weights=weights, k=1)[0]) if pool else None

    def _grow_args(self, rng, ctx: SearchContext, op: str, dim: str,
                   budget: int) -> tuple[Node, ...] | None:
        """按 §2.3.1 的量纲规则给算子生成合法子树（任一子树失败即整体失败）。"""
        if op in _RATIO_PRODUCERS and op != "ts_corr":
            child_dim = self._any_dim(rng)
            child = self._grow(rng, ctx, child_dim, budget)
            return (child,) if child is not None else None
        if op == "ts_corr":
            left = self._grow(rng, ctx, self._any_dim(rng), budget)
            right = self._grow(rng, ctx, self._any_dim(rng), budget)
            return (left, right) if left is not None and right is not None else None
        if op == "log1p":
            child = self._grow(rng, ctx, DIM_RATIO, budget)
            return (child,) if child is not None else None
        if op in _PRESERVING:
            child = self._grow(rng, ctx, dim, budget)
            return (child,) if child is not None else None
        return self._grow_binary_args(rng, ctx, op, dim, budget)

    def _grow_binary_args(self, rng, ctx: SearchContext, op: str, dim: str,
                          budget: int) -> tuple[Node, ...] | None:
        """``add`` / ``sub``：两侧同量纲；``mul``：一侧 ratio；``div``：分母 ratio 或同量纲。"""
        if op in ("add", "sub"):
            left = self._grow(rng, ctx, dim, budget)
            right = self._grow(rng, ctx, dim, budget)
            return (left, right) if left is not None and right is not None else None
        if op == "mul":
            other = self._grow(rng, ctx, dim, budget)
            ratio = self._grow(rng, ctx, DIM_RATIO, budget)
            return (other, ratio) if other is not None and ratio is not None else None
        # div：目标为 ratio 时可「同量纲相除」，否则只能「本量纲 ÷ ratio」。
        if dim == DIM_RATIO and rng.random() < 0.5:
            shared = self._any_dim(rng, exclude_ratio=True)
            left = self._grow(rng, ctx, shared, budget)
            right = self._grow(rng, ctx, shared, budget)
        else:
            left = self._grow(rng, ctx, dim, budget)
            right = self._grow(rng, ctx, DIM_RATIO, budget)
        return (left, right) if left is not None and right is not None else None

    @staticmethod
    def _any_dim(rng, *, exclude_ratio: bool = False) -> str:
        pool = [d for d in _FIELDS_BY_DIM if not (exclude_ratio and d == DIM_RATIO)]
        return str(rng.choice(pool)) if pool else DIM_PRICE
