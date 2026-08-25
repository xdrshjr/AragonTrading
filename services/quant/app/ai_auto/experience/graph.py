"""上下文图：内存结构 + 由事件物化 + 由 DB 行构建 + 增量并入（§4.5）。

图是**物化派生物**，事件才是真源。这一性质是本设计的可修复性来源：图算错了不是数据丢了，重算即可。

**物化必须是「按 `key` upsert」，不是「全量替换」**（评审 P0-2）。全量替换会同时制造两个静默故障：

1. 抹掉用户意志。`pinned` / `muted` / `mute_reason` 是长在 `stat` 节点上的**人类动作**，而
   `DELETE /nodes/{id}` 对 `stat` 节点回 409 时给出的可执行提示正是「改用静音」——用户照做，
   下一次物化那条经验原样复活并重新进 prompt。产品告诉用户的做法产品自己不兑现，比丢数据更糟。
2. 让 `iron` 永不可达。`iron` 的第三条门槛是「最近两次物化中效应符号一致」，全量替换后没有任何
   地方存得下上一次的符号 ⇒ 每次物化都看不到历史 ⇒ L0 恒空 ⇒ 整个特性一个字都不喂。

因此本模块只产出一份**物化计划**（`MaterializedGraph`），由数据层的**单点 upsert 函数**落地；
编排层不得写第二份 SQL——两份必漂移，而这里漂移的是用户亲手按下的「静音」。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field, replace

from . import taxonomy as tax
from .aggregate import (
    Bucket,
    BucketStats,
    EventView,
    compute_stats,
    cost_regime_cuts,
    enumerate_buckets,
    lesson_key,
    render_label,
)
from .promote import PromoteRule, evidence_weight, resolve_scope, resolve_tier, roll_sign

REL_APPLIES_IN = "applies_in"
REL_CO_OCCURS = "co_occurs"
REL_CONTRADICTS = "contradicts"
REL_REFINES = "refines"

_CONTRADICT_JACCARD = 0.6


@dataclass
class GraphNode:
    """图上的一个节点（语境或经验）。`id` 为 DB 主键；纯内存物化计划里恒为 0。"""

    key: str
    kind: str = "lesson"
    axis: str | None = None
    value: str | None = None
    label: str = ""
    detail: str = ""
    source: str = "stat"
    scope: str = "symbol"
    tier: str = "candidate"
    pinned: bool = False
    muted: bool = False
    mute_reason: str | None = None
    support: int = 0
    weight: float = 0.0
    stats: dict = field(default_factory=dict)
    signature: dict = field(default_factory=dict)
    first_ts: int = 0
    last_seen_ts: int = 0
    prev_sign: int = 0
    sign_streak: int = 0
    id: int = 0


@dataclass(frozen=True)
class GraphEdge:
    """一条有向边（`co_occurs` 无向但存两条，读侧因此只做单向索引查找）。"""

    src_key: str
    dst_key: str
    rel: str
    weight: float = 0.0
    support: int = 0


@dataclass
class MaterializedGraph:
    """一次物化的产物：待 upsert 的节点 + 待整体替换的边 + 分位点缓存。"""

    nodes: list[GraphNode] = field(default_factory=list)
    edges: list[GraphEdge] = field(default_factory=list)
    cost_regime_cuts: dict = field(default_factory=dict)
    seen_lesson_keys: set = field(default_factory=set)


class Graph:
    """会话内存图（只读检索用）。启动期一次性载入，会话内零 IO（不变式 I-3）。"""

    def __init__(self, nodes: list[GraphNode], edges: list[GraphEdge], *,
                 as_of_ts: int = 0) -> None:
        self.as_of_ts = int(as_of_ts)
        self.nodes: dict[str, GraphNode] = {n.key: n for n in nodes}
        self.applies_in: dict[str, list[tuple[str, float]]] = {}
        self.lessons_by_context: dict[str, list[str]] = {}
        self.co_occurs: dict[str, list[tuple[str, float]]] = {}
        self.contradicts: dict[str, list[str]] = {}
        self.refines: dict[str, list[str]] = {}
        for edge in edges:
            self._index(edge)
        for table in (self.applies_in, self.co_occurs):
            for key in table:
                table[key].sort(key=lambda item: (-item[1], item[0]))

    def _index(self, edge: GraphEdge) -> None:
        if edge.rel == REL_APPLIES_IN:
            self.applies_in.setdefault(edge.src_key, []).append((edge.dst_key, edge.weight))
            self.lessons_by_context.setdefault(edge.dst_key, []).append(edge.src_key)
        elif edge.rel == REL_CO_OCCURS:
            self.co_occurs.setdefault(edge.src_key, []).append((edge.dst_key, edge.weight))
        elif edge.rel == REL_CONTRADICTS:
            self.contradicts.setdefault(edge.src_key, []).append(edge.dst_key)
        elif edge.rel == REL_REFINES:
            self.refines.setdefault(edge.src_key, []).append(edge.dst_key)

    @property
    def is_empty(self) -> bool:
        """图上一条经验都没有 ⇒ 整层不装配（I-2 的零回归路径之一）。"""
        return not any(n.kind == "lesson" for n in self.nodes.values())


def build_graph(node_rows: list[dict], edge_rows: list[dict], *, as_of_ts: int = 0) -> Graph:
    """由数据层的行字典构建内存图（行 → dataclass 的**唯一**转换点）。"""
    nodes = [node_from_row(row) for row in node_rows]
    by_id = {row["id"]: row["key"] for row in node_rows}
    edges = [GraphEdge(src_key=by_id[row["src_id"]], dst_key=by_id[row["dst_id"]],
                       rel=row["rel"], weight=float(row.get("weight") or 0.0),
                       support=int(row.get("support") or 0))
             for row in edge_rows
             if row.get("src_id") in by_id and row.get("dst_id") in by_id]
    return Graph(nodes, edges, as_of_ts=as_of_ts)


def node_from_row(row: dict) -> GraphNode:
    """DB 行 → `GraphNode`（键名逐字对齐 `AiExperienceNode` 的列）。"""
    return GraphNode(
        key=row["key"], kind=row.get("kind") or "lesson", axis=row.get("axis"),
        value=row.get("value"), label=row.get("label") or "", detail=row.get("detail") or "",
        source=row.get("source") or "stat", scope=row.get("scope") or "symbol",
        tier=row.get("tier") or "candidate", pinned=bool(row.get("pinned")),
        muted=bool(row.get("muted")), mute_reason=row.get("mute_reason"),
        support=int(row.get("support") or 0), weight=float(row.get("weight") or 0.0),
        stats=row.get("stats") or {}, signature=row.get("signature") or {},
        first_ts=int(row.get("first_ts") or 0), last_seen_ts=int(row.get("last_seen_ts") or 0),
        prev_sign=int(row.get("prev_sign") or 0), sign_streak=int(row.get("sign_streak") or 0),
        id=int(row.get("id") or 0))


@dataclass(frozen=True)
class MaterializeOptions:
    """物化的全部旋钮（聚合形参；全部来自 `Settings`）。"""

    rule: PromoteRule
    co_min_weight: float = 0.15
    co_min_support: int = 5
    cost_regime_min_samples: int = 30
    node_cap: int = 4000
    # ★ E-2（评审 P0-2）：`narrative` / `manual` / `imported` 三类经验出生即 `weight == 0.0`，
    # 乘进 `score` 恒为 0 并被 `retrieve` 的 `score > 0` 剔除。给它们一个起始证据权重，语义是
    # 「没有统计支撑、但有人明确写下来」——刻意低于一条 n=8 / |t|=2.0 的统计经验。
    manual_seed_weight: float = 0.35


def materialize(events: list[EventView], options: MaterializeOptions,
                existing: dict[str, GraphNode] | None = None) -> MaterializedGraph:
    """事件全集 → 物化计划（**纯函数**：同一批事件恒得同一份计划，A-15 的幂等由此而来）。

    Args:
        events: 参与统计的事件（调用方已按 `origin='live'` 过滤）。
        options: 阈值与上限。
        existing: 库里既有的经验节点（按 key），用于前滚 `prev_sign` / `sign_streak` 与
            保留 `first_ts`。缺席即视作首次物化。

    Returns:
        `MaterializedGraph`：待 upsert 的节点（语境 + 经验）、全量重算的边、分位点缓存。
    """
    prior = existing or {}
    cuts = cost_regime_cuts(events, min_samples=options.cost_regime_min_samples)
    buckets = enumerate_buckets(events, cuts)
    plan = MaterializedGraph(cost_regime_cuts=cuts)
    lessons: list[tuple[GraphNode, Bucket]] = []
    for key in sorted(buckets):                        # 决定性：按 bucket key 全序
        bucket = buckets[key]
        stats = compute_stats(bucket, events, cuts)
        if stats.n <= 0:
            continue
        node = _lesson_node(bucket, stats, options.rule, prior)
        lessons.append((node, bucket))
    lessons.sort(key=lambda item: (-item[0].weight, item[0].key))
    lessons = lessons[:max(1, options.node_cap)]
    plan.nodes.extend(node for node, _ in lessons)
    plan.seen_lesson_keys = {node.key for node, _ in lessons}
    context_support = _context_support(events, cuts)
    plan.nodes.extend(_context_nodes(context_support))
    plan.edges.extend(_applies_in_edges(lessons, events, cuts))
    plan.edges.extend(_co_occurs_edges(events, cuts, options))
    plan.edges.extend(_contradicts_edges([node for node, _ in lessons], options.rule))
    _attach_seeded_lessons(plan, prior, options)          # ★ E-2（评审 P0-2）
    return plan


_SEEDED_SOURCES = ("narrative", "manual", "imported")


def _attach_seeded_lessons(plan: MaterializedGraph, prior: dict[str, GraphNode],
                           options: MaterializeOptions) -> None:
    """★ E-2（评审 P0-2）· 把 `narrative` / `manual` / `imported` 三类经验**接进图**。

    这三类经验今天在检索层**结构性不可达**：`retrieval._collect` 只收「与激活语境有 `applies_in`
    边」的节点，而它们一条边都没有；`_score` 又把恒为 0 的 `weight` 乘进去，`retrieve` 随后
    `score > 0` 把它们全部剔除。后果是蒸馏、手工新增、导入、**置顶**四条产品能力全部
    「写得进、读不出」——I-4 白纸黑字承诺的「升 `iron` 只有一条路径：用户显式置顶」在代码里
    根本不存在。

    **必须在物化这一步做**：`replace_edges` 先 `delete(AiExperienceEdge)` 全清再插入本计划里的边，
    任何在别处插的 `applies_in` 都会被下一次物化抹掉。

    本函数只做两件事，**绝不改写它们的内容与用户意志**（`label` / `detail` / `pinned` / `muted` /
    `tier` / `support` / `stats` 逐字照抄原值，只把 `weight` 抬到起始证据权重）：
      ① 按 `signature` 逐轴生成 `applies_in` 边（`w = 1.0`）；语境节点缺席则顺带建出来；
      ② `weight = manual_seed_weight`，使 `_collect` 的按权重截断不会先把它们切掉。
    """
    existing_keys = {node.key for node in plan.nodes}
    for key in sorted(prior):
        old = prior[key]
        if old.source not in _SEEDED_SOURCES or not old.signature:
            continue
        if key not in existing_keys:
            seeded = replace(old, weight=float(options.manual_seed_weight))
            plan.nodes.append(seeded)
            existing_keys.add(key)
        for axis in sorted(old.signature):
            value = old.signature[axis]
            if axis not in tax.AXES or not value:
                continue
            context_key = tax.canonical_key(axis, value)
            if context_key not in existing_keys:
                plan.nodes.append(GraphNode(
                    key=context_key, kind="context", axis=axis, value=value, source="context",
                    label=f"{tax.AXIS_LABEL_ZH.get(axis, axis)}·{tax.label_of(axis, value)}",
                    support=0, weight=1.0, tier="normal"))
                existing_keys.add(context_key)
            plan.edges.append(GraphEdge(src_key=key, dst_key=context_key,
                                        rel=REL_APPLIES_IN, weight=1.0, support=old.support))


def _lesson_node(bucket: Bucket, stats: BucketStats, rule: PromoteRule,
                 prior: dict[str, GraphNode]) -> GraphNode:
    """一个桶 → 一个经验节点（含符号历史前滚与用户意志保留）。"""
    key = lesson_key(bucket, stats.template)
    old = prior.get(key)
    roll = roll_sign(old.prev_sign if old else 0, old.sign_streak if old else 0,
                     stats.meanNetBps)
    stats.prevSign = roll.sign
    stats.signStreak = roll.streak
    wire = stats.wire()
    scope = resolve_scope(wire, bucket.signature, rule)
    pinned = bool(old.pinned) if old else False
    node = GraphNode(
        key=key, kind="lesson", label=render_label(bucket, stats),
        source=(old.source if old else "stat"), scope=scope,
        tier=resolve_tier(wire, rule, source=(old.source if old else "stat"),
                          pinned=pinned, sign_streak=roll.streak),
        pinned=pinned, muted=bool(old.muted) if old else False,
        mute_reason=(old.mute_reason if old else None),
        support=stats.n, weight=evidence_weight(wire, rule),
        stats=wire, signature=dict(bucket.signature),
        first_ts=(old.first_ts if old and old.first_ts else stats.firstTs),
        last_seen_ts=stats.lastTs, prev_sign=roll.sign, sign_streak=roll.streak,
        id=(old.id if old else 0))
    if roll.flipped:
        # 符号翻转 = 降档，而不是就地改数。版本链与 `contradicts` 由 `service` 在落库时补，
        # 用户因此能看到「这条经验曾经是反过来的，在 X 月被 N 笔新样本推翻」。
        node.tier = "normal" if node.tier == "iron" else node.tier
        node.stats["flipped"] = True
    return node


def _context_support(events: list[EventView], cuts: dict) -> dict[str, tuple[str, str, int]]:
    """每个 `axis:value` 语境的支撑事件数（语境节点的 `support`）。"""
    out: dict[str, tuple[str, str, int]] = {}
    for event in events:
        for axis, value in _event_axes(event, cuts).items():
            key = tax.canonical_key(axis, value)
            axis_v, value_v, count = out.get(key, (axis, value, 0))
            out[key] = (axis_v, value_v, count + 1)
    return out


def _event_axes(event: EventView, cuts: dict) -> dict[str, str]:
    """一条事件点亮的全部语境（九轴 + `exit` + 现算的 `cost_regime`）。"""
    from .aggregate import label_cost_regime

    axes = dict(event.context)
    axes.setdefault(tax.AXIS_SYMBOL, event.inst_id)
    axes.setdefault(tax.AXIS_BAR, event.bar)
    axes[tax.AXIS_COST_REGIME] = label_cost_regime(event, cuts)[0]
    return {axis: value for axis, value in axes.items() if axis in tax.AXES}


def _context_nodes(support: dict[str, tuple[str, str, int]]) -> list[GraphNode]:
    return [GraphNode(key=key, kind="context", axis=axis, value=value, source="context",
                      label=f"{tax.AXIS_LABEL_ZH.get(axis, axis)}·{tax.label_of(axis, value)}",
                      support=count, weight=1.0, tier="normal")
            for key, (axis, value, count) in sorted(support.items())]


def _applies_in_edges(lessons: list[tuple[GraphNode, Bucket]], events: list[EventView],
                      cuts: dict) -> list[GraphEdge]:
    """`applies_in(lesson → context)`：`w = 桶内该语境的事件数 / 桶的事件数`。

    一阶桶恒为 1；高阶桶给出该语境在这条经验里的份额——于是决策轮拿不到 `side` 时（评审 P1-2），
    不匹配的轴自然减权，无需任何额外补丁。
    """
    edges: list[GraphEdge] = []
    for node, bucket in lessons:
        total = len(bucket.event_ids)
        if total <= 0:
            continue
        counts: dict[str, int] = {}
        for index in bucket.event_ids:
            for axis, value in _event_axes(events[index], cuts).items():
                counts[tax.canonical_key(axis, value)] = counts.get(
                    tax.canonical_key(axis, value), 0) + 1
        for context_key in sorted(bucket.signature):
            key = tax.canonical_key(context_key, bucket.signature[context_key])
            edges.append(GraphEdge(src_key=node.key, dst_key=key, rel=REL_APPLIES_IN,
                                   weight=round(counts.get(key, 0) / total, 6),
                                   support=counts.get(key, 0)))
    return edges


def _co_occurs_edges(events: list[EventView], cuts: dict,
                     options: MaterializeOptions) -> list[GraphEdge]:
    """`co_occurs(context ↔ context)`：归一化点互信息（npmi > 0 且支撑数 ≥ `co_min_support`）。

    只做**一跳**扩散的理由写在 `retrieval` 里；这里只负责把边算对。无向边存两条有向边。
    """
    total = len(events)
    if total <= 0:
        return []
    single: dict[str, int] = {}
    pairs: dict[tuple[str, str], int] = {}
    for event in events:
        keys = sorted(tax.canonical_key(a, v) for a, v in _event_axes(event, cuts).items())
        for key in keys:
            single[key] = single.get(key, 0) + 1
        for i, left in enumerate(keys):
            for right in keys[i + 1:]:
                pairs[(left, right)] = pairs.get((left, right), 0) + 1
    edges: list[GraphEdge] = []
    for (left, right), support in sorted(pairs.items()):
        if support < options.co_min_support:
            continue
        npmi = _npmi(single.get(left, 0), single.get(right, 0), support, total)
        if npmi is None or npmi < options.co_min_weight:
            continue
        edges.append(GraphEdge(left, right, REL_CO_OCCURS, weight=npmi, support=support))
        edges.append(GraphEdge(right, left, REL_CO_OCCURS, weight=npmi, support=support))
    return edges


def _npmi(count_a: int, count_b: int, count_ab: int, total: int) -> float | None:
    """`ln(p(a,b)/(p(a)p(b))) / (−ln p(a,b))`；退化输入 ⇒ None（不编一个 0 混进边表）。"""
    if count_a <= 0 or count_b <= 0 or count_ab <= 0 or total <= 0:
        return None
    p_ab = count_ab / total
    if p_ab >= 1.0:
        return None
    p_a, p_b = count_a / total, count_b / total
    denominator = -math.log(p_ab)
    if denominator <= 0:
        return None
    return round(math.log(p_ab / (p_a * p_b)) / denominator, 6)


def _contradicts_edges(lessons: list[GraphNode], rule: PromoteRule) -> list[GraphEdge]:
    """`contradicts(l1 ↔ l2)`：语境 Jaccard ≥0.6 但净额符号相反且两侧 `|t|` 均达阈。

    把一个未解决的冲突当成事实讲给交易员是产品在撒谎；讲给风控复核官才是对的——这两条经验因此
    被挡在 L0/L1 之外、打包成一条「争议」送 L2（`retrieval` 步骤 4）。
    """
    edges: list[GraphEdge] = []
    significant = [n for n in lessons
                   if abs(float(n.stats.get("tStat") or 0.0)) >= rule.t_threshold
                   and n.stats.get("meanNetBps") is not None]
    for i, left in enumerate(significant):
        for right in significant[i + 1:]:
            left_mean = float(left.stats["meanNetBps"])
            right_mean = float(right.stats["meanNetBps"])
            if left_mean * right_mean >= 0:
                continue
            if _jaccard(left.signature, right.signature) < _CONTRADICT_JACCARD:
                continue
            edges.append(GraphEdge(left.key, right.key, REL_CONTRADICTS, weight=1.0))
            edges.append(GraphEdge(right.key, left.key, REL_CONTRADICTS, weight=1.0))
    return edges


def _jaccard(left: dict, right: dict) -> float:
    """两个语境签名的 Jaccard（按 `axis:value` 对）。"""
    left_set = {f"{a}:{v}" for a, v in left.items()}
    right_set = {f"{a}:{v}" for a, v in right.items()}
    union = left_set | right_set
    return (len(left_set & right_set) / len(union)) if union else 0.0
