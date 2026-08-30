"""情报战绩与信源信誉的层级收缩先验（ai-trade-intel-actionability 轴 B，§3.2.2）。

**纯核心**：无 IO、无网络、无 DB、无时钟（`now` 一律由调用方传入）。

本模块把两条**已经在产出、但只被显示**的证据流回流进系统：

| 流 | 来源 | 回答的问题 |
|---|---|---|
| B1 战绩 | `ai_intel_citations` 里 `role="entry"` 且已结算、非 `void` 的行 | 采信这一类情报之后，钱是赚了还是赔了 |
| B2 信誉 | `ai_intel_items.verdict` + `sources_json` 的域名 | 这一类情报 / 这个域名说的话，事后有多大比例被证实 |

三条必须写死的性质：

1. **样本为 0 ⇒ 权重恒为 0.0**，且 `accuracy()` 返回 ``None`` 而不是 ``0.0``。「没数据」与
   「全错」是两件事，写成同一个值就是把一次没跑过的统计伪装成一次很差的成绩。
2. **B1 与 B2 分开求解、分开展示，只在 `weight` 上合成**：它们的可证伪性完全不同（B1 混杂了
   仓位与时机，B2 只关于「这句话是不是真的」）。缺哪一半就只用另一半，两半都缺则 ``None``。
3. **`scale` 不是常数**：`net_usd` 的量纲随账户大小变化，取该标的已结算引用 `|net_usd|` 的
   **中位数**（不是均值——一笔离群的大额往返会让均值失去意义）。

⚠️ **本模块绝不 import `gate`**（评审 P1-3）。`gate.score_item` 要在运行期读本模块的产物，
反过来 import 就是 `gate ↔ efficacy` 循环。修法不是延迟 import（那只是把环藏起来），而是**让
先验层根本不需要认识条目**：`solve()` 收纯行 dict，`weight_for` 只收一个类别串，`gate.py` 侧
直接读 ``eff.weights.get(item.category, 0.0)``。

⚠️ **四个消费点里没有一个是门禁**：先验只改变**顺序**与**举证责任**，绝不改变任何一条情报
能不能进库、能不能被注入。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from .quotes import normalize_for_match
from .taxonomy import IntelLimits

# 一条历史战绩为负的类别，升长期时额外要写足的理由字数（轴 B 消费点 #2）。
# 它是一道**举证责任**，不是一张免死金牌：写足了照样能升（与 `_ARCHIVE_PROFITABLE_MIN_CHARS`
# 同款裁决）。归一之后再数——一串空格凑不出 20 个字。
WEAK_RECORD_MIN_REASON_CHARS = 20


@dataclass(frozen=True)
class Stat:
    """一个桶的两组原始计数（B1 三项 + B2 三项）。**不做任何比值**——比值在 `accuracy` 里。"""

    n: int = 0                 # B1：已结算且非 void 的入场引用数
    wins: int = 0
    net_usd: float = 0.0
    confirmed: int = 0         # B2
    refuted: int = 0
    unresolved: int = 0

    @property
    def samples(self) -> int:
        """该桶的总样本量（B1 结算数 + B2 判定数）。收缩强度与 `k_min` 都拿它比。"""
        return int(self.n) + int(self.confirmed) + int(self.refuted) + int(self.unresolved)


@dataclass(frozen=True)
class Efficacy:
    """一次求解的全部产物（只读；调用方缓存它，注入端与 REST 共读同一份）。

    `Efficacy` 是**纯函数的产物**：同一批 ``(citation_rows, item_rows, limits, now)`` 在任何
    地方求解都得到同一个值。这一条性质是 §3.2.4「两处各求一次不构成两个真源」的全部依据，
    由 **G-28** 冻结——否则它就退化成一句好听话。
    """

    overall: Stat = field(default_factory=Stat)
    by_category: dict[str, Stat] = field(default_factory=dict)
    by_domain: dict[str, Stat] = field(default_factory=dict)
    weights: dict[str, float] = field(default_factory=dict)          # category -> [-max, +max]
    domain_weights: dict[str, float] = field(default_factory=dict)
    scale: float = 1.0                                               # `net_usd` 的量纲基准

    @property
    def sample_n(self) -> int:
        return self.overall.samples


def accuracy(stat: Stat) -> float | None:
    """核查准确率 = ``confirmed / (confirmed + refuted)``；分母 0 ⇒ ``None``。

    **`unresolved` 不进分母**：它的含义是「查不出来」，把它算进去会让准确率结构性偏低，而那
    是一句关于**核查能力**的话，不是一句关于**信源**的话。

    Returns:
        0..1 的比值，或 ``None``（= 样本不足，REST 上是 ``null``，**绝不是 0.0**）。
    """
    denominator = int(stat.confirmed) + int(stat.refuted)
    return None if denominator <= 0 else float(stat.confirmed) / float(denominator)


def _median_abs(values: list[float]) -> float:
    """`|net_usd|` 的中位数（空表 ⇒ 0.0）。均值会被一笔离群的大额往返带跑。"""
    if not values:
        return 0.0
    ordered = sorted(abs(float(v)) for v in values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2.0


def _raw_b1(stat: Stat, *, scale: float) -> float | None:
    """B1 的原始分（[-1, 1]）；无结算样本 ⇒ ``None``（**不是 0.0**）。"""
    if stat.n <= 0:
        return None
    win_rate_centered = 2.0 * (float(stat.wins) / float(stat.n)) - 1.0
    return 0.6 * win_rate_centered + 0.4 * math.tanh(float(stat.net_usd) / max(1.0, scale))


def _raw_b2(stat: Stat) -> float | None:
    """B2 的原始分（[-1, 1]）；无判定样本 ⇒ ``None``。"""
    total = int(stat.confirmed) + int(stat.refuted) + int(stat.unresolved)
    if total <= 0:
        return None
    return float(int(stat.confirmed) - int(stat.refuted)) / float(total)


def raw_score(stat: Stat, *, scale: float) -> float | None:
    """B1 与 B2 的合成原始分；两半都缺 ⇒ ``None``，缺一半就只用另一半。"""
    b1 = _raw_b1(stat, scale=scale)
    b2 = _raw_b2(stat)
    if b1 is None and b2 is None:
        return None
    if b1 is None:
        return b2
    if b2 is None:
        return b1
    return 0.5 * b1 + 0.5 * b2


def _shrunk_weight(stat: Stat, *, parent: float, limits: IntelLimits,
                   scale: float) -> float:
    """两级收缩 + 硬封顶。样本不足（``samples < k_min``）⇒ **严格 0.0**（G-14）。

    ``shrunk = (n_b * raw_b + k_min * parent) / (n_b + k_min)``，``parent`` 链是
    ``category → overall → 0.0（中性）``。封顶是 `efficacy_max_weight` 的**绝对值**：
    先验只该微调排序，绝不该把一条情报压到看不见（R-6）。
    """
    k_min = max(1, int(limits.efficacy_k_min))
    if stat.samples < k_min:
        return 0.0
    raw = raw_score(stat, scale=scale)
    if raw is None:
        return 0.0
    shrunk = (stat.samples * raw + k_min * parent) / float(stat.samples + k_min)
    cap = abs(float(limits.efficacy_max_weight))
    return max(-cap, min(cap, shrunk))


def weight_for(category: str, eff: Efficacy, *, limits: IntelLimits) -> float:
    """一个类别的注入排序加分项（**只收类别串**，见模块 docstring 的 P1-3 定稿）。

    Args:
        category: `taxonomy.CATEGORIES` 的元素。
        eff: `solve` 的产物。
        limits: 提供 `efficacy_max_weight`（在读点上再夹一次，使封顶不依赖构造时的那一次）。

    Returns:
        ``[-max_weight, +max_weight]``；未知类别 / 样本不足 ⇒ 严格 ``0.0``。
    """
    cap = abs(float(limits.efficacy_max_weight))
    return max(-cap, min(cap, float(eff.weights.get(category, 0.0))))


def normalized_reason_len(reason: str) -> int:
    """归一后的理由长度（`promote_weak_record` 与 `keep_both_thin_note` 的**唯一**度量）。

    复用 `quotes.normalize_for_match`（NFKC / 引号族 / 空白折叠），**不写第二个归一器**——一串
    全角空格或重复标点凑出来的「理由」不该算数，而本仓库已经有一个把这件事做对了的实现。
    """
    return len(normalize_for_match(reason or "").strip())


def _bump(bucket: dict[str, list], key: str) -> list:
    return bucket.setdefault(key, [0, 0, 0.0, 0, 0, 0, []])


def _finalize(raw_bucket: list) -> tuple[Stat, list[float]]:
    return (Stat(n=raw_bucket[0], wins=raw_bucket[1], net_usd=round(raw_bucket[2], 6),
                 confirmed=raw_bucket[3], refuted=raw_bucket[4], unresolved=raw_bucket[5]),
            list(raw_bucket[6]))


def _collect_b1(citation_rows: list[dict], category_of: dict[int, str],
                buckets: dict[str, list], overall: list) -> None:
    """把已结算的**入场**引用行摊进 overall 与类别桶。

    只认 ``role == "entry"``：「情报影响了这笔开仓」可证伪，「情报影响了这次继续持有」不可。
    ``outcome == "void"`` 的行**一条都不进**——结算尝试确实发生过、净额确实不可得，把一次失败
    伪装成一次平局比缺一个数更坏（与 `ai_intel_citation_repositories` 的既有裁决同源）。
    """
    for row in citation_rows or ():
        if str(row.get("role") or "") != "entry" or not row.get("settled"):
            continue
        outcome = str(row.get("outcome") or "")
        if outcome == "void":
            continue
        net = float(row.get("netPnlUsd") or 0.0)
        category = category_of.get(int(row.get("itemId") or 0), "")
        targets = [overall] + ([_bump(buckets, category)] if category else [])
        for target in targets:
            target[0] += 1
            target[1] += 1 if outcome == "win" else 0
            target[2] += net
            target[6].append(net)


def _collect_b2(item_rows: list[dict], buckets: dict[str, list],
                domains: dict[str, list], overall: list) -> None:
    """把条目的核查判定摊进 overall、类别桶与**域名**桶。

    域名一条判定会被算进它的**每一个**来源域名：一条被三家媒体报道的情报被证伪时，三家都该
    记这一笔——「谁跟着报了一条后来被推翻的消息」正是信源信誉要回答的问题。
    """
    slot = {"confirmed": 3, "refuted": 4, "unresolved": 5}
    for row in item_rows or ():
        index = slot.get(str(row.get("verdict") or ""))
        if index is None:
            continue
        category = str(row.get("category") or "")
        targets = [overall] + ([_bump(buckets, category)] if category else [])
        seen: set[str] = set()
        for source in row.get("sources") or ():
            domain = str((source or {}).get("domain") or "") if isinstance(source, dict) else ""
            if domain and domain not in seen:
                seen.add(domain)
                targets.append(_bump(domains, domain))
        for target in targets:
            target[index] += 1


def solve(citation_rows: list[dict], item_rows: list[dict], *,
          limits: IntelLimits, now: float) -> Efficacy:
    """求解一个标的的战绩与信誉先验（**纯函数**：同一批入参在哪里求都得到同一个值）。

    Args:
        citation_rows: `list_intel_citations` 的 wire 行（camelCase：`itemId` / `role` /
            `settled` / `outcome` / `netPnlUsd`）。
        item_rows: `load_intel_scope` 的 wire 行（camelCase：`id` / `category` / `verdict` /
            `sources[].domain`）。
        limits: 阈值聚合；`efficacy_enabled=False` ⇒ **返回空 `Efficacy`**（全部权重为 0，
            四个消费点因此逐字节回退）。
        now: 墙钟秒。**当前不参与任何判据**，留在签名里是因为 §3.2.2 把「纯函数、无时钟」写成
            了 ``(citation_rows, item_rows, limits, now)`` 这个四元组的性质，G-28 按它对拍。

    Returns:
        `Efficacy`。样本为 0 的桶权重恒为 ``0.0``，`accuracy` 恒为 ``None``。
    """
    del now                              # 见 docstring：签名的一部分，判据里刻意不用
    if not limits.efficacy_enabled:
        return Efficacy()
    category_of = {int(row.get("id") or 0): str(row.get("category") or "")
                   for row in item_rows or ()}
    overall_raw: list = [0, 0, 0.0, 0, 0, 0, []]
    category_raw: dict[str, list] = {}
    domain_raw: dict[str, list] = {}
    _collect_b1(citation_rows, category_of, category_raw, overall_raw)
    _collect_b2(item_rows, category_raw, domain_raw, overall_raw)
    overall, overall_nets = _finalize(overall_raw)
    scale = max(1.0, _median_abs(overall_nets))
    by_category = {key: _finalize(value)[0] for key, value in category_raw.items()}
    by_domain = {key: _finalize(value)[0] for key, value in domain_raw.items()}
    parent = raw_score(overall, scale=scale) or 0.0
    weights = {key: _shrunk_weight(stat, parent=parent, limits=limits, scale=scale)
               for key, stat in by_category.items()}
    domain_weights = {key: _shrunk_weight(stat, parent=parent, limits=limits, scale=scale)
                      for key, stat in by_domain.items()}
    return Efficacy(overall=overall, by_category=by_category, by_domain=by_domain,
                    weights=weights, domain_weights=domain_weights, scale=scale)


def stat_wire(stat: Stat) -> dict:
    """一个桶的 wire（camelCase）。`accuracy` **可为 ``null``**（= 样本不足）。"""
    return {
        "settled": int(stat.n),
        "wins": int(stat.wins),
        "netPnlUsd": round(float(stat.net_usd), 6),
        "confirmed": int(stat.confirmed),
        "refuted": int(stat.refuted),
        "unresolved": int(stat.unresolved),
        "samples": int(stat.samples),
        "accuracy": accuracy(stat),
    }
