"""同一标的库存内部的立场矛盾检测（ai-trade-intel-actionability 轴 C，§3.3.1）。

**纯核心**：无 IO、无网络、无 DB、无时钟（`now` 一律由调用方传入）。

库里可以同时躺着「ETF 资金连续第 5 日净流入」（bullish/high）与「ETF 单日净流出创年内新高」
（bearish/high）两条都 active、都未被证伪、都会被注入的情报。本轮之前的注入端把它们各自当成
独立证据平铺给模型，**不告诉模型它们互相矛盾**。

**刻意不自动裁决**（§十 非目标 9）：确定性层没有能力判断两条相反的报道里哪一条是对的，它唯一
诚实的动作是**让矛盾对模型和用户都可见**——误判的后果上限因此只是「prompt 里多一句提示」，
不删除、不降级、不停注入。

`items` 走**鸭子类型**（`gate.IntelItem` 的只读字段）而不是 import `gate`：本模块被 `disclosure`
与 `service` 消费，而 `disclosure` 已经 import `gate`——多一条边只会让包内依赖图更难读。
"""
from __future__ import annotations

from dataclasses import dataclass

from .quotes import normalize_for_match

# 「同一件事」的时间窗（小时）：层级越高，两条报道相隔越久仍可能在讲同一件事。
CONFLICT_WINDOW_H: dict[str, float] = {"ultra": 24.0, "mid": 240.0, "long": 1440.0}

# 一次注入至多标注几对。**刻意不做成配置项**（§十一 的括号段）：20 对矛盾摊在 prompt 里等于
# 没说，而一个没人会调的旋钮只会给 `.env.example` 的双向对齐门禁增加一行负担。
MAX_CONFLICT_PAIRS = 6

# `keep_both` 的理由字数下限（轴 C 的举证责任，G-21）。与 `efficacy.WEAK_RECORD_MIN_REASON_CHARS`
# 同为 20 是巧合而不是耦合：一个是「升长期」的门槛，一个是「宣布两条相反的情报可以同时成立」
# 的门槛，日后各调各的。
KEEP_BOTH_MIN_REASON_CHARS = 20

# 自适应停用词的触发比例：出现在该标的 60% 以上 active 条目里的字符组没有区分度
# （标的名、「价格」、「市场」这类）。**不维护任何词典**——一张要人去养的停用词表，
# 三个月后一定是错的。
_STOPWORD_RATIO = 0.6
_MIN_ITEMS_FOR_STOPWORDS = 4


@dataclass(frozen=True)
class ConflictPair:
    """一对「同类别 + 反立场 + 主题高度重叠 + 事件时刻同窗」的条目。

    `a_id` / `b_id` 恒为 ``(min, max)``：它是一个**无序对**的规范形态。注入端的重排按两者在
    定稿列表里的**位置**决定谁在前，与这里的命名无关（§3.3.2）。
    """

    a_id: int
    b_id: int
    overlap: float
    window_h: float
    category: str

    def wire(self, *, open_rounds: int = 0) -> dict:
        """`GET /intel/{instId}` 的 `conflicts` 行（camelCase）。"""
        return {"aId": int(self.a_id), "bId": int(self.b_id),
                "overlap": round(float(self.overlap), 4),
                "windowH": round(float(self.window_h), 2),
                "category": self.category, "openRounds": int(open_rounds)}


def topic_tokens(headline: str, summary: str) -> frozenset[str]:
    """标题 + 摘要 → 字符 2-gram 组。

    **复用 `quotes.normalize_for_match`**（NFKC / 引号族 / 空白折叠 / casefold），不写第二个
    归一器。中文没有空格分词，字符 2-gram 是这个仓库里**唯一不引入分词依赖**的可行选择——
    为一句主题重叠度去养一份分词词典，代价远大于收益。
    """
    text = normalize_for_match(f"{headline or ''} {summary or ''}")
    compact = "".join(ch for ch in text if not ch.isspace())
    if len(compact) < 2:
        return frozenset()
    return frozenset(compact[i:i + 2] for i in range(len(compact) - 1))


def overlap(a: frozenset[str], b: frozenset[str]) -> float:
    """Jaccard 相似度；任一侧为空 ⇒ ``0.0``（空集与空集不算「高度重叠」）。"""
    if not a or not b:
        return 0.0
    union = len(a | b)
    return 0.0 if union == 0 else len(a & b) / float(union)


def _ref_ts(item) -> int:
    """事件参照时刻：``event_ts if event_ts > 0 else captured_ts``。

    与 `gate.score_item` / `alert.age_minutes` 同一条口径（spec §3.3.1 判据 4 只写了
    `event_ts`，见《实施过程发现的方案缺陷》I-2）：直接用 `event_ts` 会让**无日期条目**
    （`event_ts == 0`）永远与任何东西相距半个世纪从而永不成对，而两条无日期条目又会被判成
    「时刻完全相同」——一个方向错、一个方向也错。
    """
    return int(item.event_ts) if int(item.event_ts or 0) > 0 else int(item.captured_ts or 0)


def _stopwords(token_map: dict[int, frozenset[str]]) -> frozenset[str]:
    """出现在 `_STOPWORD_RATIO` 以上条目里的字符组（自适应停用表）。

    条目太少时**不启用**：三条情报里出现两次的字符组不能说明它没有区分度，而把它停掉会让
    刚起步的库一对矛盾都找不到。
    """
    total = len(token_map)
    if total < _MIN_ITEMS_FOR_STOPWORDS:
        return frozenset()
    frequency: dict[str, int] = {}
    for tokens in token_map.values():
        for token in tokens:
            frequency[token] = frequency.get(token, 0) + 1
    ceiling = _STOPWORD_RATIO * total
    return frozenset(token for token, count in frequency.items() if count > ceiling)


def find_conflicts(items: list, *, min_overlap: float, max_pairs: int,
                   now: float) -> list[ConflictPair]:
    """在当前库存里找出成对的立场矛盾。

    四条判据**全部满足**才成对：

    1. 两条都是 ``status == "active"`` 且 ``verdict != "refuted"``（被证伪的一方**自动解除
       矛盾**——它已经不进注入了）；
    2. `category` 相同（跨类别的相反立场不是矛盾：宏观偏空 + 项目偏多是常态）；
    3. `stance` 一个 `bullish` 一个 `bearish`（`neutral` 不参与）；
    4. 主题重叠 ≥ `min_overlap` **且** 事件时刻落在 ``min(window(a.tier), window(b.tier))`` 内。

    Args:
        items: 该标的的库存（本函数自行过滤非 active 与已证伪）。
        min_overlap: Jaccard 阈值（`limits.conflict_min_overlap`）。
        max_pairs: 结果上限（`MAX_CONFLICT_PAIRS`）；按 `overlap` 降序取前 N。
        now: 墙钟秒。**当前不参与任何判据**——两条判据用的都是条目之间的相对时刻。留在签名里
            是因为 §3.3.1 把「无时钟纯核心」写成了这个四元组的性质，且日后若要加「太老的矛盾
            不必再提」这类判据，它就在手边。

    Returns:
        至多 `max_pairs` 个 `ConflictPair`，按 `overlap` 降序（同分时按 `(a_id, b_id)` 升序，
        保证**确定性**）。
    """
    del now                              # 见 docstring：签名的一部分，判据里刻意不用
    live = [it for it in items or ()
            if getattr(it, "status", "") == "active"
            and getattr(it, "verdict", "") != "refuted"
            and int(getattr(it, "item_id", 0) or 0) > 0]
    if len(live) < 2:
        return []
    token_map = {int(it.item_id): topic_tokens(it.headline, it.summary) for it in live}
    stop = _stopwords(token_map)
    if stop:
        token_map = {key: frozenset(value - stop) for key, value in token_map.items()}
    pairs: list[ConflictPair] = []
    for index, left in enumerate(live):
        for right in live[index + 1:]:
            pair = _pair_of(left, right, token_map=token_map, min_overlap=min_overlap)
            if pair is not None:
                pairs.append(pair)
    pairs.sort(key=lambda p: (-p.overlap, p.a_id, p.b_id))
    return pairs[:max(0, int(max_pairs))]


def _pair_of(left, right, *, token_map: dict[int, frozenset[str]],
             min_overlap: float) -> ConflictPair | None:
    """两条条目是否构成一对矛盾；不构成 ⇒ ``None``。"""
    if left.category != right.category:
        return None
    stances = {getattr(left, "stance", ""), getattr(right, "stance", "")}
    if stances != {"bullish", "bearish"}:
        return None
    window_h = min(CONFLICT_WINDOW_H.get(left.tier, 24.0),
                   CONFLICT_WINDOW_H.get(right.tier, 24.0))
    if abs(_ref_ts(left) - _ref_ts(right)) > window_h * 3600.0:
        return None
    score = overlap(token_map.get(int(left.item_id), frozenset()),
                    token_map.get(int(right.item_id), frozenset()))
    if score < float(min_overlap):
        return None
    a_id, b_id = sorted((int(left.item_id), int(right.item_id)))
    return ConflictPair(a_id, b_id, score, window_h, left.category)


def pair_index(conflicts) -> dict[int, int]:
    """``item_id → 对方 item_id``（注入端渲染标注用的快查表）。

    一条条目理论上可以同时属于两对；本表按 `conflicts` 的顺序**先到先得**（`find_conflicts`
    已按 `overlap` 降序），故它指向的恒是重叠度最高的那一对。
    """
    index: dict[int, int] = {}
    for pair in conflicts or ():
        index.setdefault(int(pair.a_id), int(pair.b_id))
        index.setdefault(int(pair.b_id), int(pair.a_id))
    return index


def open_rounds_for(pair: ConflictPair, items_by_id: dict[int, object], *,
                    curate_round_ts: list[int]) -> int:
    """这一对已经悬置了几轮整理（**不落库**，由两端的 `last_curated_ts` 与轮流水推算）。

    新增一列去记它会立刻带来「这一列谁写、谁清」的问题，而 `ai_intel_items` 上已经有 14 个后加
    列都带着「绝不进 `_item_values`」的警告（§3.3.3 的方框）。推算的代价是一次**已经在做**的
    查询（整理轮的存根本来就要读）。

    Args:
        pair: 目标矛盾对。
        items_by_id: 该标的的条目索引（`item_id → IntelItem`）。
        curate_round_ts: 该标的最近若干次整理轮的 `started_ts`（顺序无关）。

    Returns:
        ``>= 0``：在两端「最后一次被整理」之后又跑过几次整理轮。两端任一从未被整理过 ⇒ 全部
        整理轮都算（= 这一对一次都没被裁决过）。
    """
    stamps = [int(getattr(items_by_id.get(key), "last_curated_ts", 0) or 0)
              for key in (pair.a_id, pair.b_id)]
    since = min(stamps)
    return sum(1 for ts in curate_round_ts or () if int(ts or 0) > since)
