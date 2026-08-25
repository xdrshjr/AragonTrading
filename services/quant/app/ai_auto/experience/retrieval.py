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

_EPS = 1e-9
# 势均力敌的相对差异阈：`rel = |m1−m2| / max(m1,m2,ε)`。用**相对**判据的理由见 §4.6 步骤 4——
# `match = Σ A(c)·w_applies` 无量纲上界，会随激活语境数增长，绝对阈值在种子多的轮次形同虚设。
_DISPUTE_REL = 0.25


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

    def ids(self) -> tuple[str, ...]:
        """L0∪L1 的 key 元组——档位判定（§4.8）拿它与上轮比对，判断语境是否真的移动了。"""
        return tuple([item.node.key for item in self.l0] + [item.node.key for item in self.l1])


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
           seed: ContextSeed, cfg: RetrievalConfig) -> ScoredLesson:
    """步骤 3 · 打分 `match · evidence · recency · scope · (pinned ? 1.5 : 1)`。"""
    match = 0.0
    for context_key, weight in graph.applies_in.get(node.key, ()):
        match += activation.get(context_key, 0.0) * float(weight)
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


def retrieve(graph: Graph, seed: ContextSeed, cfg: RetrievalConfig) -> RetrievalResult:
    """完整检索链；空图 / 全零候选 ⇒ 空结果（调用方据此整段不渲染，绝不产出空壳）。"""
    result = RetrievalResult()
    if graph is None or graph.is_empty:
        return result
    activation = activate(graph, seed, cfg)
    result.activation = activation
    candidates = _collect(graph, activation, cfg)
    scored = [_score(node, activation, graph, seed, cfg) for node in candidates]
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

    - `rel > 0.25` ⇒ 保留高分者，低分者进 `suppressed`（UI 可见，prompt 不出现）；
    - `rel ≤ 0.25`（势均力敌）⇒ **两条都不进 L0/L1**，打包成一条「争议」送 L2。
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
            high = max(item.match, rival.match, _EPS)
            rel = abs(item.match - rival.match) / high
            if rel > _DISPUTE_REL:
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

    判据：提议开多 ⇒ 找 `side=long` 且净额均值为负、`|t|` 最大的一条；开空同理；平仓 ⇒ 取
    L0/L1 里净额均值绝对值最大的一条（复核官需要知道「这一类平仓历史上的净额长什么样」）。

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
        return max(pool, key=lambda item: (abs(float(item.node.stats.get("meanNetBps") or 0.0)),
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
