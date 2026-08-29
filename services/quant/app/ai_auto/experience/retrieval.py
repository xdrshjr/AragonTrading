"""检索：种子激活扩散 → 打分 → 反例压制 → MMR 去冗（§4.6）—— 纯函数、零 IO、决定性。

**决定性**是硬要求：所有排序都以 `(score, node.key)` 二元组为键，杜绝浮点并列时的不稳定顺序。
同图同种子跑 100 次，结果序列必须完全一致（用例 A-6）——一个每次给出不同经验的 Agent，
用户没有任何办法判断它到底在依据什么下单。

**种子在决策轮只有 7 轴，不是 9 轴**（评审 P1-2）：`ContextSeed` 在 `_build_decision_payload`
时刻装配，而 `side`（本轮要开多还是开空）与 `confidence`（模型置信度）此刻**都还不存在**——
两者要到 `decide()` 返回之后才有。已有持仓时可以额外把 `side` 置为**当前持仓方向**（那是一个
已发生的事实，不是提议）。到了反思轮，`side` 与 `confidence` 才补齐——那也正好是反例最需要
精准的时刻。

**一跳扩散是刻意的**：两跳的收益是把「震荡→高波动→急跌」这种传递性带进来，代价是相关性被稀释
到无法解释，还会把不同标的的语境串起来。一条不可解释的经验仍然是产品缺陷，哪怕它不会亏钱。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from . import taxonomy as tax
from .graph import Graph, GraphNode

# 势均力敌的**绝对**覆盖率差阈：`gap = |m1 − m2|`（★ E-14）。
#
# 这个阈值此前是**相对**的，理由逐字写着「`match = Σ A(c)·w_applies` 无量纲上界，会随激活语境
# 数增长」。W-E 的覆盖率归一之后那个前提就没了：`match ∈ [0,1]` 且满匹配恒等于 1.0，继续用
# 相对判据会让一大批满匹配的矛盾对同时取 1.0 ⇒ `rel = 0` ⇒ 双双被踢出 L0/L1。
# 留着那句注释就是在源码里留一句假话（`v1:I-2` 的成文教训），故一并改成绝对量纲：
# 「两条结论的覆盖率相差不到 0.25 ⇒ 谁也压不倒谁 ⇒ 都不进纲要，打包成一条争议送 L2」。
# 方向仍是单侧安全的（说得更少，与 J-1 一致）。
_DISPUTE_GAP = 0.25


@dataclass(frozen=True)
class ContextSeed:
    """本轮的语境种子（决策轮 7~8 轴 / 反思轮 8~9 轴 / 预演全 11 轴）。"""

    axes: dict[str, str] = field(default_factory=dict)
    now_wall: float = 0.0

    def keys(self) -> list[str]:
        """按 `AXES` 顺序展开成 `axis:value` 列表（决定性排序的一半）。"""
        return [tax.canonical_key(axis, self.axes[axis])
                for axis in tax.AXES if axis in self.axes and self.axes[axis]]


@dataclass(frozen=True)
class RetrievalConfig:
    """检索旋钮（全部来自 `Settings`）。"""

    alpha: float = 0.5
    co_min_weight: float = 0.15
    max_contexts: int = 24
    candidate_cap: int = 200
    mmr_lambda: float = 0.5
    half_life_days: float = 30.0
    l0_max: int = 3
    l1_max: int = 3
    # ★ E-2（评审 P0-2）· 证据的两条下限。没有它们，「用户显式置顶」这条 I-4 白纸黑字承诺的
    # 升 `iron` 路径在代码里根本不存在——被置顶的手工经验 `weight == 0.0`，`score` 恒 0，
    # 在 `retrieve` 的 `score > 0` 那一行被剔除，而 §7.1 那句「置顶的经验不受证据门槛约束」是假的。
    manual_seed_weight: float = 0.35        # 有人明确写下来 ≠ 没有证据
    pinned_evidence_floor: float = 0.60     # 用户置顶 = 用户提供的证据

    @classmethod
    def from_settings(cls, settings) -> "RetrievalConfig":
        return cls(
            alpha=float(settings.ai_trade_experience_seed_expand_alpha),
            co_min_weight=float(settings.ai_trade_experience_co_edge_min_weight),
            max_contexts=int(settings.ai_trade_experience_max_expanded_contexts),
            candidate_cap=int(settings.ai_trade_experience_candidate_cap),
            mmr_lambda=float(settings.ai_trade_experience_mmr_lambda),
            half_life_days=float(settings.ai_trade_experience_half_life_days),
            l0_max=int(settings.ai_trade_experience_l0_max),
            l1_max=int(settings.ai_trade_experience_l1_max),
            manual_seed_weight=float(settings.ai_trade_experience_manual_seed_weight),
            pinned_evidence_floor=float(settings.ai_trade_experience_pinned_evidence_floor))


@dataclass
class ScoredLesson:
    """一条候选经验及其打分明细（明细随 wire 下发，使「为什么是这三条」可核对）。"""

    node: GraphNode
    match: float
    evidence: float
    recency: float
    scope: float
    score: float


@dataclass
class RetrievalResult:
    """一次检索的全部产物。"""

    l0: list[ScoredLesson] = field(default_factory=list)
    l1: list[ScoredLesson] = field(default_factory=list)
    disputed: list[tuple[str, str]] = field(default_factory=list)
    suppressed: list[str] = field(default_factory=list)
    activation: dict[str, float] = field(default_factory=dict)
    # 轴 E-2：`side` 轴方向退化时**有样本的那一侧**（`long` / `short`）；未退化 / 未开启 ⇒ None。
    side_degeneracy: str | None = None

    def ids(self) -> tuple[str, ...]:
        """L0∪L1 的 key 元组——档位判定（§4.8）拿它与上轮比对，判断语境是否真的移动了。"""
        return tuple([item.node.key for item in self.l0] + [item.node.key for item in self.l1])


def side_degeneracy(graph: Graph) -> str | None:
    """经验图谱的 `side` 轴是否**方向退化**；退化 ⇒ 返回**有样本的那一侧**，否则 `None`。

    判据：`side` 轴上恰有一侧的语境节点 `support > 0`，另一侧缺席或为 0
    （directional-symmetry 轴 E-2）。

    为什么这件事必须被特判：不做空 ⇒ 无做空样本 ⇒ `side:short` 语境节点在图里根本不存在 ⇒
    检索在结构上**只可能**召回做多教训 ⇒ 模型读到的经验永远说「做多是有效的」。而「做空侧没有
    教训」与「做空侧的教训是负面的」在模型看来是不可区分的两件事——这是一个不会自己走出来的
    吸收态。本函数把它变成一个可判定、可披露的事实。
    """
    support = {}
    for side in ("long", "short"):
        node = graph.nodes.get(tax.canonical_key(tax.AXIS_SIDE, side))
        support[side] = int(getattr(node, "support", 0) or 0) if node is not None else 0
    if support["long"] > 0 and support["short"] <= 0:
        return "long"
    if support["short"] > 0 and support["long"] <= 0:
        return "short"
    return None


def _axis_of(context_key: str) -> str:
    """语境 key（`axis:value`）→ 轴 id；不含冒号的 key 原样返回（不猜、不抛）。"""
    return context_key.split(":", 1)[0]


def activate(graph: Graph, seed: ContextSeed, cfg: RetrievalConfig) -> dict[str, float]:
    """步骤 1 · 种子激活扩散（一跳，有界）。

    `A(c)=1.0` for `c ∈ seed`；`A(c')=max_{c∈seed} α·w_co(c,c')` for 一跳邻居。
    按 `A` 降序保留前 `max_contexts` 个扩散语境（种子本身恒保留）。
    """
    activation = {key: 1.0 for key in seed.keys() if key in graph.nodes}
    expanded: dict[str, float] = {}
    for key in sorted(activation):
        for neighbour, weight in graph.co_occurs.get(key, ()):
            if neighbour in activation or weight < cfg.co_min_weight:
                continue
            value = cfg.alpha * float(weight)
            if value > expanded.get(neighbour, 0.0):
                expanded[neighbour] = value
    top = sorted(expanded.items(), key=lambda item: (-item[1], item[0]))[:max(0, cfg.max_contexts)]
    activation.update(dict(top))
    return activation


def _score(node: GraphNode, activation: dict[str, float], graph: Graph,
           seed: ContextSeed, cfg: RetrievalConfig,
           drop_axes: frozenset[str] = frozenset()) -> ScoredLesson:
    """步骤 3 · 打分 `match · evidence · recency · scope · (pinned ? 1.5 : 1)`。

    ★ **E-2（F-5 下半）· `match` 的语义订正为「这条经验自身的条件被当前语境满足的比例」**，
    值域 `[0,1]`。此前 `match = Σ A(c)·w`（而 `w` 恒为 1）= **命中的轴数**，于是：
      · 签名越宽分越高（实测三条四轴经验 `match` 全部 = 4.0）；
      · **一半条件不成立的经验与完全匹配的经验同分**——四轴命中两轴 = 两轴命中两轴。
    覆盖率归一之后，前者 0.5、后者 1.0。这是需求里「精准」二字在打分函数里的落点。

    **分母用「边权之和」而不是 `len(node.signature)`**：签名与边可能因物化撕裂而不同步
    （`v1:E-14` 刻意保留了撕裂可见这一形态），用边算才是用「图上真的有什么」算；且
    `_attach_seeded_lessons` 给 manual / imported / narrative 补的边权同样是 1.0，两者一致。

    `drop_axes`（directional-symmetry 轴 E-2）：该轴的边**整轴退出评分**——既不进分子也不进
    分母。方向退化时把 `side` 留在分母里，会让「本轮是做空」这个语境对每一条（清一色做多的）
    经验都判为不匹配，于是覆盖率被一个结构性缺样本的轴系统性压低。默认空集 ⇒ 逐字节回退。
    """
    edges = [(context_key, weight) for context_key, weight in graph.applies_in.get(node.key, ())
             if _axis_of(context_key) not in drop_axes]
    denominator = sum(float(weight) for _context, weight in edges) or 1.0
    match = sum(activation.get(context_key, 0.0) * float(weight)
                for context_key, weight in edges) / denominator
    recency = _recency(node.last_seen_ts, seed.now_wall, cfg.half_life_days)
    scope = _scope_factor(node, seed)
    evidence = _evidence(node, cfg)
    score = match * evidence * recency * scope * (1.5 if node.pinned else 1.0)
    return ScoredLesson(node=node, match=round(match, 6), evidence=round(evidence, 6),
                        recency=round(recency, 6), scope=scope, score=round(score, 9))


def _evidence(node: GraphNode, cfg: RetrievalConfig) -> float:
    """★ E-2（评审 P0-2）· 证据强度，带两条下限。

    统计经验的 `weight` 是 `sat(support)·conf(|t|)`；而 `narrative` / `manual` / `imported`
    出生即 `support=0, weight=0.0`，乘进 `score` 恒为 0 并被 `retrieve` 的 `score > 0` 剔除。
    于是蒸馏、手工新增、导入、**置顶**四条产品能力全部「写得进、读不出」。

    两条下限（取最大值，不叠加）：
      · `pinned` ⇒ `pinned_evidence_floor`——**用户置顶 = 用户提供的证据**，这是 I-4 承诺的
        「升 `iron` 只有一条路径」在代码里的唯一落点；
      · `source != "stat"` ⇒ `manual_seed_weight`——有人明确写下来，不等于没有证据。
    """
    evidence = float(node.weight)
    if node.pinned:
        evidence = max(evidence, float(cfg.pinned_evidence_floor))
    if node.source != "stat":
        evidence = max(evidence, float(cfg.manual_seed_weight))
    return evidence


def _recency(last_seen_wall: int, now_wall: float, half_life_days: float) -> float:
    """`0.5 ** ((now − lastSeen)/(halflife·86400))`。

    `last_seen_ts` 是该经验**最近一条支撑事件**的墙钟，**不是**最近一次物化的时刻（评审 P1-7）——
    后者会让每次重建都把时钟拨回 now ⇒ 衰减恒为 1 ⇒ 三年前的教训与昨天同权，且不会报错。
    """
    if last_seen_wall <= 0 or now_wall <= 0 or half_life_days <= 0:
        return 1.0
    elapsed = max(0.0, float(now_wall) - float(last_seen_wall))
    return 0.5 ** (elapsed / (half_life_days * 86400.0))


def _scope_factor(node: GraphNode, seed: ContextSeed) -> float:
    """跨标的污染的**硬闸**（R-4）：本标的 1.0 / 通用经验 0.7 / 其余 0.0。

    「PEPE 1m 的教训用在 BTC 1H」不会报错，只会用别的标的的历史去开真实仓位。
    """
    seed_symbol = seed.axes.get(tax.AXIS_SYMBOL)
    lesson_symbol = node.signature.get(tax.AXIS_SYMBOL)
    if lesson_symbol is None:
        return 1.0 if node.scope == "symbol" else 0.7
    return 1.0 if (seed_symbol and lesson_symbol == seed_symbol) else 0.0


def retrieve(graph: Graph, seed: ContextSeed, cfg: RetrievalConfig,
             *, direction_debias: bool = False) -> RetrievalResult:
    """完整检索链；空图 / 全零候选 ⇒ 空结果（调用方据此整段不渲染，绝不产出空壳）。

    `direction_debias`（directional-symmetry 轴 E-2，keyword-only）：`True` 且图上的 `side` 轴
    方向退化时，该轴**整轴退出评分**（`_score` 的 `drop_axes`）。**只加不减**：退化判据不成立
    时 `drop_axes` 为空集 ⇒ 打分逐字节回退。退化事实本身由 `RetrievalResult.side_degeneracy`
    带出去，在注入抬头如实声明——**静默地改掉打分而不告诉读者**，是本轮要根除的那种不诚实。
    """
    result = RetrievalResult()
    if graph is None or graph.is_empty:
        return result
    degenerate = side_degeneracy(graph) if direction_debias else None
    result.side_degeneracy = degenerate
    drop_axes = frozenset({tax.AXIS_SIDE}) if degenerate else frozenset()
    activation = activate(graph, seed, cfg)
    result.activation = activation
    candidates = _collect(graph, activation, cfg)
    scored = [_score(node, activation, graph, seed, cfg, drop_axes) for node in candidates]
    scored = [item for item in scored if item.score > 0]          # 评审 P2-5：零分先剔除
    if not scored:
        return result
    scored.sort(key=lambda item: (-item.score, item.node.key))
    kept, disputed, suppressed = _suppress(scored, graph)
    result.disputed = disputed
    result.suppressed = suppressed
    iron = [item for item in kept if item.node.tier == "iron" or item.node.pinned]
    normal = [item for item in kept if item.node.tier == "normal" and not item.node.pinned]
    result.l0 = _mmr(iron, cfg.l0_max, cfg.mmr_lambda)
    picked_keys = {item.node.key for item in result.l0}
    result.l1 = _mmr([item for item in normal if item.node.key not in picked_keys],
                     cfg.l1_max, cfg.mmr_lambda)
    return result


def _collect(graph: Graph, activation: dict[str, float], cfg: RetrievalConfig) -> list[GraphNode]:
    """步骤 2 · 候选收集：与激活语境有 `applies_in` 边、且未静音的经验节点。"""
    keys: set[str] = set()
    for context_key in sorted(activation):
        keys.update(graph.lessons_by_context.get(context_key, ()))
    nodes = [graph.nodes[key] for key in sorted(keys)
             if key in graph.nodes and not graph.nodes[key].muted
             and graph.nodes[key].tier != "archived"]
    nodes.sort(key=lambda node: (-node.weight, node.key))
    return nodes[:max(1, cfg.candidate_cap)]


def _suppress(scored: list[ScoredLesson],
              graph: Graph) -> tuple[list[ScoredLesson], list[tuple[str, str]], list[str]]:
    """步骤 4 · 反例压制。

    - 覆盖率差 `> 0.25` ⇒ 保留高分者，低分者进 `suppressed`（UI 可见，prompt 不出现）；
    - 覆盖率差 `≤ 0.25`（势均力敌）⇒ **两条都不进 L0/L1**，打包成一条「争议」送 L2。

    ★ **E-14**：判据在 W-E 之后是**绝对**的（`match ∈ [0,1]` 是覆盖率），见 `_DISPUTE_GAP`。
    """
    by_key = {item.node.key: item for item in scored}
    dropped: set[str] = set()
    disputed: list[tuple[str, str]] = []
    suppressed: list[str] = []
    for item in scored:
        if item.node.key in dropped:
            continue
        for rival_key in sorted(graph.contradicts.get(item.node.key, ())):
            rival = by_key.get(rival_key)
            if rival is None or rival_key in dropped:
                continue
            gap = abs(item.match - rival.match)
            if gap > _DISPUTE_GAP:
                loser = rival if item.score >= rival.score else item
                dropped.add(loser.node.key)
                suppressed.append(loser.node.key)
            else:
                dropped.update({item.node.key, rival_key})
                disputed.append(tuple(sorted((item.node.key, rival_key))))
    return ([item for item in scored if item.node.key not in dropped],
            sorted(set(disputed)), sorted(set(suppressed)))


def _mmr(items: list[ScoredLesson], limit: int, lam: float) -> list[ScoredLesson]:
    """步骤 5 · MMR 去冗：保证 k 条覆盖不同坐标轴，而不是同一条教训的三个切面。

    `norm(x) = x / max(scores)`，分母恒 >0（零分候选已在 `retrieve` 里剔除，评审 P2-5）。
    """
    if limit <= 0 or not items:
        return []
    best = max(item.score for item in items)
    if best <= 0:
        return []
    picked: list[ScoredLesson] = []
    pool = list(items)
    while pool and len(picked) < limit:
        def rank(candidate: ScoredLesson) -> tuple[float, str]:
            redundancy = max((_signature_jaccard(candidate.node, chosen.node)
                              for chosen in picked), default=0.0)
            value = (1 - lam) * (candidate.score / best) - lam * redundancy
            return (-value, candidate.node.key)

        pool.sort(key=rank)
        picked.append(pool.pop(0))
    return picked


def _signature_jaccard(left: GraphNode, right: GraphNode) -> float:
    left_set = {f"{a}:{v}" for a, v in (left.signature or {}).items()}
    right_set = {f"{a}:{v}" for a, v in (right.signature or {}).items()}
    union = left_set | right_set
    return (len(left_set & right_set) / len(union)) if union else 0.0


def most_conflicting(graph: Graph, result: RetrievalResult, action: str) -> ScoredLesson | None:
    """反思轮的反例：与提议动作**最相冲突**的 1 条（§4.1 / §4.8 的 L2）。

    判据：提议开多 ⇒ 找 `side=long` 且净额均值为负、`|t|` 最大的一条；开空同理；**平仓 ⇒ 取
    净额均值为负、`|t|` 最大的一条**（★ D-1）。

    ★ **D-1（F-1）· `close` 分支此前不看符号**，取的是 `|meanNetBps|` 最大的那条——实测会挑中
    一条 **+88bps 的盈利经验**放在「与本次提议最相冲突的一条」这个标题下交给复核官。而复核官的
    裁决空间只有 `confirm` / `abort` / 缩仓，把一条**支持**平仓的证据标注成「最相冲突」，唯一
    可能的行为后果是**否掉一次本该确认的平仓**。这不是排序不佳，是陈述不实（`v1:I-9`）。
    这一轮确实没有反例时，正确的产品行为是**什么都不说**，而不是找一条最响的话来填满这个位置。

    Args:
        graph: 会话内存图。
        result: 本轮检索结果（反例只从**已检索到**的候选里挑，绝不另起一次全图扫描）。
        action: `open_long` | `open_short` | `close`。

    Returns:
        最相冲突的一条；没有可用反例 ⇒ None（整段不渲染）。
    """
    want_side = {"open_long": "long", "open_short": "short"}.get(action)
    pool = list(result.l0) + list(result.l1)
    if not pool:
        return None
    if want_side is None:
        negatives = [item for item in pool
                     if float(item.node.stats.get("meanNetBps") or 0.0) < 0]
        if not negatives:
            return None
        return max(negatives, key=lambda item: (abs(float(item.node.stats.get("tStat") or 0.0)),
                                                item.node.key))
    conflicting = [item for item in pool
                   if item.node.signature.get(tax.AXIS_SIDE) == want_side
                   and float(item.node.stats.get("meanNetBps") or 0.0) < 0]
    if not conflicting:
        conflicting = [item for item in pool
                       if float(item.node.stats.get("meanNetBps") or 0.0) < 0]
    if not conflicting:
        return None
    return max(conflicting, key=lambda item: (abs(float(item.node.stats.get("tStat") or 0.0)),
                                              item.node.key))
