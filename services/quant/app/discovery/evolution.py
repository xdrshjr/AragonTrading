"""五维演化与自适应预算（strategy-factor-discovery §2.5）—— 纯函数 + 有界状态。

三块内容，全部可单测、可复现（同种子同输入 ⇒ 同输出）：
- **预算**：默认 ``mutate 25% / crossover 25% / perturb 15% / random 15% / llm 20%``（与报告一致），
  冷启动切「random / llm 各 50%」，此后每 ``adaptEvery`` 轮按计数器跑一次 ``adapt_budget``。
- **父本选择**：锦标赛（tournament size 3）而非全局最优——避免过早收敛到某一条「富矿脉」。
- **数据驱动特征分布**（报告的「第一次自适应」）：随机探索的字段抽样权重 =
  ``1 + 2 × 该字段在 kept 候选中的出现率``；窗口分布以 kept 窗口的中位数为中心。
"""
from __future__ import annotations

import math
from collections import OrderedDict
from dataclasses import dataclass, field

from .expression import FIELDS, WINDOW_LADDER, fields_of, windows_of, Node

SOURCE_KEYS: tuple[str, ...] = ("mutate", "crossover", "perturb", "random", "llm")
DEFAULT_BUDGET: dict[str, float] = {
    "mutate": 0.25, "crossover": 0.25, "perturb": 0.15, "random": 0.15, "llm": 0.20}

BUDGET_MIN, BUDGET_MAX = 0.05, 0.40
ADAPT_STEP = 0.05
DEFAULT_COLD_START_MIN = 5       # kept 数低于此值即冷启动（全随机 + LLM）
DEFAULT_ADAPT_EVERY = 10
DEFAULT_MECH_PROBE_AFTER = 50    # 跑满这么多轮后，0 覆盖机制族才触发 llm 加权
MOMENTUM_BETA = 0.7
MOMENTUM_CAP = 64                # 动量键上限（LRU；进检查点，必须有界）
MOMENTUM_MAX_STEP = 4


# ============================ 预算 ============================
def cold_start_budget() -> dict[str, float]:
    """冷启动预算：``random`` 与 ``llm`` 各 50%，其余为 0（无父本可变异 / 交叉）。"""
    return {"mutate": 0.0, "crossover": 0.0, "perturb": 0.0, "random": 0.5, "llm": 0.5}


def _scaled_to_unit(values: dict[str, float]) -> dict[str, float]:
    """按总量缩放到 1.0 并保留 6 位小数；**余数补给最大的分量**，保证严格求和为 1。

    不补余数的话 ``round`` 会留下 1e-6 量级的缺口，落进检查点后每次读写再放大，最终表现为
    「预算加起来不是 100%」这种让人看半天的小毛病。
    """
    total = sum(values.values())
    if total <= 0.0:
        return dict(DEFAULT_BUDGET)
    scaled = {key: round(value / total, 6) for key, value in values.items()}
    residual = round(1.0 - sum(scaled.values()), 6)
    if residual:
        top = max(scaled, key=lambda key: scaled[key])
        scaled[top] = round(scaled[top] + residual, 6)
    return scaled


def normalize_budget(raw: object) -> dict[str, float]:
    """把任意输入夹到 ``[0, 1]`` 并归一化到 1.0（缺键 / 非法值 / 非 dict 一律回落默认）。

    容忍非 dict 是刻意的：检查点可能被外部改坏，而断点损坏不该让一条长跑任务崩掉。
    """
    data = raw if isinstance(raw, dict) else {}
    merged = {}
    for key in SOURCE_KEYS:
        try:
            merged[key] = max(0.0, float(data.get(key, DEFAULT_BUDGET[key])))
        except (TypeError, ValueError):
            merged[key] = DEFAULT_BUDGET[key]
    return _scaled_to_unit(merged)


def _clamp_and_normalize(budget: dict[str, float]) -> dict[str, float]:
    """先把各分量夹到 ``[0.05, 0.40]``，再归一化到 1.0（§2.5 的收口顺序，不可颠倒）。"""
    clamped = {key: min(BUDGET_MAX, max(BUDGET_MIN, float(budget.get(key, 0.0))))
               for key in SOURCE_KEYS}
    return _scaled_to_unit(clamped)


@dataclass(frozen=True)
class BudgetCounters:
    """自适应预算的全部输入（纯计数器 ⇒ ``adapt_budget`` 可单测、可复现）。"""

    near_kept_rate: float = 0.0        # 近邻操作（mutate/crossover/perturb）的入库率
    global_kept_rate: float = 0.0      # 全局入库率
    dedupe_reject_rate: float = 0.0    # 因「与已入库高相关」（F10）被拒的比例
    uncovered_families: int = 0        # 0 覆盖的机制族数
    covered_families: int = 0          # 已覆盖的机制族数
    iteration: int = 0
    kept_rate_trend: tuple[float, ...] = ()   # 最近 3 个调整窗口的入库率（旧 → 新）


def _is_declining(trend: tuple[float, ...]) -> bool:
    """入库率是否连续下降（需要至少 3 个窗口，样本不足一律判否，绝不外推）。"""
    if len(trend) < 3:
        return False
    recent = trend[-3:]
    return recent[0] > recent[1] > recent[2]


def adapt_budget(budget: dict[str, float], counters: BudgetCounters, *,
                 mech_probe_after: int = DEFAULT_MECH_PROBE_AFTER) -> dict[str, float]:
    """按 §2.5 的四条规则调整预算（纯函数；夹紧 + 归一化在末尾统一收口）。

    | 触发条件 | 动作 |
    |---|---|
    | 近邻入库率 > 全局入库率 × 2 | ``mutate`` / ``perturb`` 各 +0.05（探索转利用） |
    | F10 高相关拒绝比例 > 0.30 | ``random`` / ``llm`` 各 +0.05（结构同质化，须扩源） |
    | 存在 0 覆盖机制族且已跑满 ``mech_probe_after`` 轮 | ``llm`` +0.05（补空白方向） |
    | 已覆盖 ≥3 族且入库率连续 3 窗下降 | ``crossover`` +0.05（尝试组合已有信号源） |
    """
    out = dict(normalize_budget(budget))
    if counters.near_kept_rate > counters.global_kept_rate * 2.0 > 0.0:
        out["mutate"] += ADAPT_STEP
        out["perturb"] += ADAPT_STEP
    if counters.dedupe_reject_rate > 0.30:
        out["random"] += ADAPT_STEP
        out["llm"] += ADAPT_STEP
    if counters.uncovered_families > 0 and counters.iteration >= mech_probe_after:
        out["llm"] += ADAPT_STEP
    if counters.covered_families >= 3 and _is_declining(counters.kept_rate_trend):
        out["crossover"] += ADAPT_STEP
    return _clamp_and_normalize(out)


def allocate_operations(budget: dict[str, float], count: int,
                        rng) -> list[str]:
    """把本轮候选名额按预算切给五种操作（最大余额法 + 确定性洗牌，可复现）。

    用最大余额法而非逐个抽样：候选数常只有几十个，逐个抽样会让 15% 的份额经常颗粒无收，
    表现为「某个来源整轮没出现」——那不是预算，是运气。
    """
    if count <= 0:
        return []
    weights = normalize_budget(budget)
    exact = {key: weights[key] * count for key in SOURCE_KEYS}
    base = {key: int(math.floor(value)) for key, value in exact.items()}
    remainder = count - sum(base.values())
    order = sorted(SOURCE_KEYS, key=lambda key: (-(exact[key] - base[key]), key))
    for key in order[:remainder]:
        base[key] += 1
    plan = [key for key in SOURCE_KEYS for _ in range(base[key])]
    rng.shuffle(plan)
    return plan


# ============================ 父本选择 ============================
def tournament_select(rng, pool: list, *, size: int = 3):
    """锦标赛选择（size 默认 3）：随机取 ``size`` 个，返回 ``score`` 最高者。

    刻意**不用**全局最优——全局最优会让搜索迅速塌缩到单一结构家族，这正是报告里 FSA 要救的
    问题；锦标赛保留了「次优但不同」的父本被选中的概率。
    """
    if not pool:
        return None
    picks = [pool[rng.randrange(len(pool))] for _ in range(max(1, min(size, len(pool))))]
    return max(picks, key=lambda entry: float(getattr(entry, "score", 0.0) or 0.0))


# ============================ 数据驱动特征分布 ============================
@dataclass
class SearchContext:
    """生成层的采样分布与引导信息（每轮由 ``build_search_context`` 重算）。"""

    field_weights: dict[str, float] = field(default_factory=dict)
    window_weights: dict[int, float] = field(default_factory=dict)
    mechanism_hint: str = ""

    def weighted_fields(self) -> tuple[list[str], list[float]]:
        keys = list(self.field_weights) or list(FIELDS)
        return keys, [max(0.0, float(self.field_weights.get(k, 1.0))) for k in keys]

    def weighted_windows(self) -> tuple[list[int], list[float]]:
        keys = list(self.window_weights) or list(WINDOW_LADDER)
        return keys, [max(0.0, float(self.window_weights.get(k, 1.0))) for k in keys]


def build_search_context(kept_nodes: list[Node], *, mechanism_hint: str = "") -> SearchContext:
    """由 kept 候选的结构统计构造采样分布（报告的「数据驱动特征分布」，第一次自适应）。

    - 字段权重 = ``1 + 2 × 该字段在 kept 中的出现率``（出现率 = 用到该字段的 kept 条数 / kept 总数）。
    - 窗口权重 = 以 kept 窗口**中位数**在梯子上的位置为中心的三角核（距离越远权重越低，下限 0.2）。

    kept 为空（冷启动）时两者都退化为均匀分布——绝不凭空给某个字段先验加权，尤其是报告里
    最强的 ``overnight``：它在 7×24 永续上极弱，照抄先验就是把 A 股结论硬套过来。
    """
    field_weights = {key: 1.0 for key in FIELDS}
    window_weights = {win: 1.0 for win in WINDOW_LADDER}
    if not kept_nodes:
        return SearchContext(field_weights, window_weights, mechanism_hint)
    total = len(kept_nodes)
    for key in FIELDS:
        hits = sum(1 for node in kept_nodes if key in set(fields_of(node)))
        field_weights[key] = 1.0 + 2.0 * (hits / total)
    all_windows = [w for node in kept_nodes for w in windows_of(node)]
    if all_windows:
        ordered = sorted(all_windows)
        median = ordered[len(ordered) // 2]
        center = min(range(len(WINDOW_LADDER)),
                     key=lambda i: abs(WINDOW_LADDER[i] - median))
        span = max(1, len(WINDOW_LADDER) - 1)
        for i, win in enumerate(WINDOW_LADDER):
            window_weights[win] = max(0.2, 1.0 - abs(i - center) / span)
    return SearchContext(field_weights, window_weights, mechanism_hint)


# ============================ 动量追踪（参数扰动方向） ============================
class MomentumTracker:
    """``(骨架, 窗口槽位)`` → EWMA 梯度；状态**有界**（LRU ≤64），随检查点持久化。

    ``g ← β·g + (1-β)·(Δscore / Δladder_index)``（β=0.7）；步长
    ``step = clip(round(base_step × |g| / (mean|g| + ε)), 1, 4)``，方向取 ``sign(g)``。
    """

    def __init__(self, cap: int = MOMENTUM_CAP, beta: float = MOMENTUM_BETA):
        self._cap = int(cap)
        self._beta = float(beta)
        self._state: "OrderedDict[str, dict]" = OrderedDict()

    @staticmethod
    def key_of(skeleton: str, slot: int) -> str:
        return f"{skeleton}#{int(slot)}"

    def observe(self, skeleton: str, slot: int, *, delta_score: float,
                delta_index: int, iteration: int) -> None:
        """记录一次「窗口走了 ``delta_index`` 格、分数变了 ``delta_score``」的观察。"""
        if not skeleton or delta_index == 0:
            return
        key = self.key_of(skeleton, slot)
        entry = self._state.get(key) or {"g": 0.0, "seenIter": iteration}
        gradient = float(delta_score) / float(delta_index)
        entry["g"] = self._beta * float(entry["g"]) + (1.0 - self._beta) * gradient
        entry["seenIter"] = int(iteration)
        self._state[key] = entry
        self._state.move_to_end(key)
        while len(self._state) > self._cap:
            self._state.popitem(last=False)

    def step_for(self, skeleton: str, slot: int, *, base_step: int = 1) -> int:
        """给出该槽位的扰动步数与方向（无观察记录 → ``+1``，即最保守的「走一格」）。"""
        entry = self._state.get(self.key_of(skeleton, slot))
        if entry is None:
            return base_step
        gradient = float(entry.get("g", 0.0))
        if gradient == 0.0:
            return base_step
        magnitudes = [abs(float(e.get("g", 0.0))) for e in self._state.values()]
        mean_magnitude = sum(magnitudes) / len(magnitudes) if magnitudes else 0.0
        scale = abs(gradient) / (mean_magnitude + 1e-9)
        size = int(min(MOMENTUM_MAX_STEP, max(1, round(base_step * scale))))
        return size if gradient > 0 else -size

    def to_list(self) -> list[dict]:
        """序列化进检查点（≤64 条，键即 ``骨架#槽位``）。"""
        return [{"key": key, "g": round(float(entry["g"]), 6),
                 "seenIter": int(entry["seenIter"])} for key, entry in self._state.items()]

    @classmethod
    def from_list(cls, raw: list | None, *, cap: int = MOMENTUM_CAP) -> "MomentumTracker":
        tracker = cls(cap=cap)
        for item in (raw or [])[-cap:]:
            if not isinstance(item, dict) or not item.get("key"):
                continue
            tracker._state[str(item["key"])] = {
                "g": float(item.get("g", 0.0) or 0.0),
                "seenIter": int(item.get("seenIter", 0) or 0)}
        return tracker

    def __len__(self) -> int:
        return len(self._state)
