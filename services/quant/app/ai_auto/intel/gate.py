"""情报的六道准入闸、置信度求解、整理动作复核表与寿命求解（ai-trade-market-intel §3.3.4 / §3.4.3）。

**纯核心**：无 IO、无网络、无 DB、无时钟（`now` 一律由调用方传入）。这是本特性「真实性由服务端裁决，
不由模型自证」这条承诺的落点——模型能编造文字，但编造不出一个后端语料里不存在的编号。

三条贯穿本模块的判断：

1. **拒收不截断**（G-3）。摘要超长一律拒并计数，绝不悄悄砍到 160 字——「模型系统性写太长」是一个
   应当可观测的事实，而不是一个被 schema 悄悄压平的假象。
2. **去重的作用域必须与 DB 唯一约束一致**（评审 P0-3）。`uq_ai_intel_dedup` 跨全部 status，闸⑤ 因此
   也跨全部 status：命中 `archived` / `muted` 走**复活**分支。作用域比约束窄的后果不是「多了一条重复
   情报」，而是整轮事务撞 `IntegrityError` 回滚、round 记 `failed`、退避加倍。
3. **寿命必须跟着层级走**（不变式 I-3 / 评审 P0-2）。`expires_for_tier` 是本仓库里 `expires_ts` 的
   **唯一**求解式；任何写 `tier` 的地方都必须在同一条 UPDATE 里调它重算。不这么做的后果不是某条
   情报早退休了，而是整座多级库在事件发生 72 小时后被确定性清理**整层扫空**。
"""
from __future__ import annotations

import hashlib
import math
import re
import time
import unicodedata
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from . import taxonomy as tax
from .taxonomy import IntelLimits

if TYPE_CHECKING:                       # 只为类型标注；运行期以鸭子类型消费（corpus 不反向依赖本模块）
    from .corpus import Corpus

# 摘要里的数字 token（闸②）。千分位在比对前去掉；`%` 保留在 token 里但比对时两种形态都试。
_NUMBER_RE = re.compile(r"[-+]?\d[\d,]*(?:\.\d+)?%?")
_PUNCT_RE = re.compile(r"[\s　!-/:-@\[-`{-~·—…、。《》「」『』【】（）？！，：；“”‘’]+")
_REF_RE = re.compile(r"^D([0-9]{1,2})$")

# 溢出淘汰与注入排序用的影响力权重。**与 `taxonomy.IMPACT_W` 刻意不同**：那一张表服务置信度求解
# （`low` 不加分是对的），而这里 `low → 0` 会让全部低影响条目的 score 恒为 0 ⇒ 淘汰顺序与注入顺序
# 在它们之间完全退化成任意——一个算得出来却分不出高下的排序等于没有排序。
# （实施过程发现的方案缺陷 #1，已登记在 spec 的《实施过程发现的方案缺陷》一节。）
SCORE_IMPACT_W: dict[str, float] = {"low": 0.34, "medium": 0.67, "high": 1.0}


# ---------------- 领域对象（与 DB 行 1:1，与 wire 1:1 但键名 camelCase） ----------------
@dataclass(frozen=True)
class IntelSource:
    """一条情报的单个来源（归一化后的绝对 URL，仅 http/https）。"""

    url: str
    title: str = ""
    publisher: str = ""
    domain: str = ""
    published_ts: int = 0        # 0 = 未知


@dataclass(frozen=True)
class IntelItem:
    """一条已过闸的情报（纯核心表示，§3.1.2）。"""

    item_id: int | None
    inst_id: str
    tier: str
    category: str
    headline: str
    summary: str
    stance: str
    impact: str
    confidence: float
    event_ts: int
    captured_ts: int
    expires_ts: int
    sources: tuple[IntelSource, ...] = ()
    domain_count: int = 0
    status: str = "active"
    pinned: bool = False
    # 用户手改过。**与 `pinned` 并列的第二把免死金牌**——`review_actions` 是纯核心、只读传进来的
    # 这个对象，漏了它金牌就静默失效（评审 P1-5）。
    user_edited: bool = False
    is_scheduled: bool = False
    undated: bool = False
    origin: str = "agent"
    dedup_key: str = ""
    updated_ts: int = 0

    @property
    def protected(self) -> bool:
        """两把免死金牌：自动路径（TTL / 溢出 / 整理轮 archive）一律跳过它。"""
        return bool(self.pinned or self.user_edited)


@dataclass(frozen=True)
class MergeOp:
    """闸⑤ 的产物：把新来源并进一条已存在的条目（`dup`）或让归档条目复活（`revived`）。"""

    item_id: int
    dedup_key: str
    sources: tuple[IntelSource, ...]
    domain_count: int
    expires_ts: int
    revive: bool
    updated_ts: int


@dataclass(frozen=True)
class AdmitResult:
    """一次采集轮的闸结果。`rejects` 是**直方图**（拒收码 → 条数），逐轮落 `ai_intel_rounds`。"""

    accepted: tuple[IntelItem, ...]
    merges: tuple[MergeOp, ...]
    rejects: dict[str, int]

    @property
    def total_rejected(self) -> int:
        return sum(self.rejects.values())


# ---------------- 寿命：`expires_ts` 的唯一求解式（不变式 I-3） ----------------
def expires_for_tier(*, tier: str, event_ts: int, captured_ts: int, pinned: bool,
                     limits: IntelLimits) -> int:
    """按**目标 tier** 求 `expires_ts`。置顶恒 0（不过期）。

    取 ``max(event_ts, captured_ts)`` 而不是 v1 的 ``event_ts``：已排期的未来事件（今晚的 CPI、
    下周的解锁）若按 `event_ts + TTL` 计，会因为「采集得早」而先过期——那正是本特性头号用例
    （评审 P0-1）。

    Args:
        tier: 目标层级（**新** tier，不是旧的）。
        event_ts / captured_ts: 事件时刻与采集时刻（墙钟秒 UTC）。
        pinned: 用户置顶 ⇒ 恒 0。
        limits: 三层 TTL 的来源。

    Returns:
        过期墙钟秒；``0`` 表示不过期。
    """
    if pinned:
        return 0
    ttl = limits.ttl_s.get(tier, limits.ttl_s["ultra"])
    return int(max(int(event_ts or 0), int(captured_ts or 0)) + ttl)


def score_item(item: IntelItem, *, now: float) -> float:
    """溢出淘汰与注入排序共用的打分：置信度 × 影响力 × 时间衰减（§3.4.4 第 2 步）。

    未来事件（`is_scheduled`）不衰减：一件还没发生的事不会因为「我们早就知道了」而变得不重要。
    """
    ref = item.event_ts if item.event_ts > 0 else item.captured_ts
    age_days = max(0.0, (float(now) - float(ref or now)) / 86400.0)
    half_life = tax.HALF_LIFE_DAYS.get(item.tier, 1.0)
    decay = math.exp(-age_days / half_life) if half_life > 0 else 1.0
    return float(item.confidence) * SCORE_IMPACT_W.get(item.impact, 0.5) * decay


# ---------------- 去重键 ----------------
def normalize_headline(headline: str) -> str:
    """去空白、去标点、全角转半角、转小写（`dedup_key` 的输入）。"""
    folded = unicodedata.normalize("NFKC", headline or "")
    return _PUNCT_RE.sub("", folded).lower()


def dedup_key(headline: str, urls: list[str] | tuple[str, ...]) -> str:
    """`sha1(归一标题 + "|" + 排序后的规范 URL).hexdigest()[:40]`。

    同一条消息被三家媒体报道时应当变成**一条**情报而不是三条，故 URL 一并进键；标题归一后再入键，
    使「主网 v2 升级已定档 9 月 3 日」与「主网 V2 升级已定档 9月3日」判为同一条。
    """
    canonical = sorted({(u or "").strip().lower() for u in urls if u})
    raw = f"{normalize_headline(headline)}|{','.join(canonical)}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:40]


# ---------------- 置信度：**服务端算，不是模型自报**（§3.3.5） ----------------
def solve_confidence(*, domain_count: int, has_published: bool, impact: str, undated: bool,
                     all_same_domain: bool) -> float:
    """按求解式给出 0.05..0.95 的置信度。

    模型的工具 schema 里**没有** `confidence` 字段：让模型给自己的产出打分，得到的永远是 0.8。
    """
    value = (0.30
             + 0.15 * min(int(domain_count), 3)
             + 0.10 * (1.0 if has_published else 0.0)
             + 0.10 * tax.IMPACT_W.get(impact, 0.5)
             - 0.20 * (1.0 if undated else 0.0)
             - 0.15 * (1.0 if all_same_domain else 0.0))
    return round(min(0.95, max(0.05, value)), 4)


# ---------------- 闸② 的数字接地 ----------------
def _numeric_core(token: str) -> str:
    return token.replace(",", "").rstrip("%")


def _date_tokens(event_ts: int, published: list[int]) -> set[str]:
    """可豁免的日期数字集合（评审 P2-12）。

    模型用中文写日期（「9 月 3 日」）而语料是英文（「September 3」）时，逐 token 硬匹配会把一条
    完全真实的情报判成 `unsourced_number`。因此只豁免**能被解释成日期分量**且与 `event_ts` /
    任一被引用文档 `published_ts` 同日的数字。除此之外**不做任何量级换算**——`1.2万` ↔ `12000`
    一律判不通过，猜错量级比拒收危险得多。
    """
    out: set[str] = set()
    for ts in [event_ts, *published]:
        if not ts:
            continue
        dt = datetime.fromtimestamp(int(ts), tz=timezone.utc)
        out.update({str(dt.year), str(dt.year % 100), str(dt.month), str(dt.day),
                    f"{dt.month:02d}", f"{dt.day:02d}"})
    return out


def _ungrounded_numbers(summary: str, corpus_text: str, *, event_ts: int,
                        published: list[int]) -> list[str]:
    """摘要里在被引用文档中找不到的数字 token（空列表 = 全部接地）。"""
    haystack = corpus_text.replace(",", "")
    exempt = _date_tokens(event_ts, published)
    missing: list[str] = []
    for token in _NUMBER_RE.findall(summary or ""):
        core = _numeric_core(token)
        if not core or core in exempt:
            continue
        if core in haystack or core.lstrip("+-") in haystack:
            continue
        missing.append(token)
    return missing


# ---------------- 六道闸 ----------------
def _bump(counter: dict[str, int], code: str) -> None:
    counter[code] = counter.get(code, 0) + 1


def _resolve_event_ts(raw_event_ts: int, published: list[int], *, captured_ts: int
                      ) -> tuple[int, bool]:
    """事件时刻的回落链：模型给的值 → 被引用文档里**最早的**非零 `published_ts` → 0。

    Returns:
        ``(event_ts, undated)``。`undated` 为真表示一个可用的发布时间都没有——这样的条目仍然
        入库（无日期的背景资料仍有价值），但置信度扣分且**永不可升级**。
    """
    if raw_event_ts and raw_event_ts > 0:
        return int(raw_event_ts), False
    known = sorted(ts for ts in published if ts > 0)
    if known:
        return int(known[0]), False
    return 0, True


def _check_shape(proposal: dict, *, limits: IntelLimits) -> str | None:
    """值域（闸前置）+ 长度（闸③）+ 内容形态（闸⑥）。返回拒收码或 ``None``（通过）。

    抽成独立函数是为了让 `_admit_one` 留在 CLAUDE.md 的 50 行阈值内，也让「哪些判据**不需要
    语料**」这件事在结构上一目了然——这三道闸只看模型自己写的字，与检索结果无关。
    """
    headline = str(proposal.get("headline") or "").strip()
    summary = str(proposal.get("summary") or "").strip()
    if (str(proposal.get("category") or "") not in tax.CATEGORIES
            or str(proposal.get("stance") or "neutral") not in tax.STANCES
            or str(proposal.get("impact") or "medium") not in tax.IMPACTS):
        return "bad_field"
    # 闸③：长度。**拒收不截断**（G-3）。
    if len(headline) > limits.headline_max_chars or len(summary) > limits.summary_max_chars:
        return "too_long"
    if len(summary) < limits.summary_min_chars or not headline:
        return "too_short"
    # 闸⑥：指令剥离。前五道闸没有一道看内容形态（评审 P1-4）。
    if tax.has_imperative(headline) or tax.has_imperative(summary):
        return "imperative_text"
    return None


def _resolve_refs(proposal: dict, corpus: "Corpus") -> list | None:
    """闸①：归属。任一编号不在本轮语料里即回 ``None``。

    模型能编 URL，编不出后端语料里不存在的编号——这是本特性可信度的物理来源。
    """
    refs = proposal.get("sourceRefs") or []
    if not isinstance(refs, (list, tuple)) or not refs:
        return None
    docs = []
    for ref in refs:
        key = str(ref or "").strip().upper()
        if not _REF_RE.match(key) or key not in corpus.index:
            return None
        docs.append(corpus.index[key])
    return docs


def _admit_one(proposal: dict, corpus: "Corpus", *, inst_id: str, limits: IntelLimits,
               now: float, rejects: dict[str, int]) -> IntelItem | None:
    """单条提案过六道闸；任一不过即计数并回 ``None``。"""
    shape_reject = _check_shape(proposal, limits=limits)
    if shape_reject:
        _bump(rejects, shape_reject)
        return None
    docs = _resolve_refs(proposal, corpus)
    if docs is None:
        _bump(rejects, "bad_ref")
        return None
    headline = str(proposal.get("headline") or "").strip()
    summary = str(proposal.get("summary") or "").strip()
    category = str(proposal.get("category") or "").strip()
    stance = str(proposal.get("stance") or "neutral").strip()
    impact = str(proposal.get("impact") or "medium").strip()

    published = [int(getattr(doc, "published_ts", 0) or 0) for doc in docs]
    captured_ts = int(now)
    event_ts, undated = _resolve_event_ts(int(proposal.get("eventTs") or 0), published,
                                          captured_ts=captured_ts)

    # 闸②：数字接地。幻觉最常见的落点是数字。
    corpus_text = "\n".join(f"{getattr(d, 'title', '')}\n{getattr(d, 'snippet', '')}"
                            for d in docs)
    if _ungrounded_numbers(summary, corpus_text, event_ts=event_ts, published=published):
        _bump(rejects, "unsourced_number")
        return None

    # 闸④：**双向**时效窗。落在未来一侧 ⇒ `is_scheduled`（今晚的 CPI、下周的解锁）。
    is_scheduled = False
    if event_ts > 0:
        if event_ts > captured_ts:
            if event_ts - captured_ts > limits.forward_window_s:
                _bump(rejects, "stale_event")
                return None
            is_scheduled = True
        elif captured_ts - event_ts > tax.TIER_BACKWARD_WINDOW_S["ultra"]:
            _bump(rejects, "stale_event")
            return None

    sources, confidence = _solve_sources(docs, impact=impact, undated=undated)
    # 一切新采集条目的唯一落点是 `ultra`：升进中期 / 长期只能由整理轮提议并经服务端复核。
    return IntelItem(
        item_id=None, inst_id=inst_id, tier="ultra", category=category, headline=headline,
        summary=summary, stance=stance, impact=impact, confidence=confidence,
        event_ts=event_ts, captured_ts=captured_ts,
        expires_ts=expires_for_tier(tier="ultra", event_ts=event_ts, captured_ts=captured_ts,
                                    pinned=False, limits=limits),
        sources=sources, domain_count=len({s.domain for s in sources if s.domain}),
        status="active", pinned=False, user_edited=False, is_scheduled=is_scheduled,
        undated=undated, origin="agent",
        dedup_key=dedup_key(headline, [s.url for s in sources]), updated_ts=captured_ts)


def _solve_sources(docs, *, impact: str, undated: bool
                   ) -> tuple[tuple[IntelSource, ...], float]:
    """把被引用文档折成来源元组，并**在服务端求解**置信度。

    `undated` 的封顶（≤0.4）写在这里而不是调用点：置信度只有这一个产出口，两处各夹一次必漂移。
    """
    sources = tuple(IntelSource(url=doc.url, title=doc.title, publisher=doc.publisher,
                                domain=doc.domain, published_ts=int(doc.published_ts or 0))
                    for doc in docs)
    domains = {s.domain for s in sources if s.domain}
    confidence = solve_confidence(
        domain_count=len(domains),
        has_published=any(int(getattr(d, "published_ts", 0) or 0) > 0 for d in docs),
        impact=impact, undated=undated, all_same_domain=len(domains) <= 1)
    return sources, (min(confidence, 0.4) if undated else confidence)


def _merge_existing(candidate: IntelItem, prior: IntelItem, *, limits: IntelLimits,
                    now: float) -> MergeOp:
    """闸⑤：把新来源并进已存在的条目。**不改 tier、不改 pinned**。

    命中 `archived` / `muted` 走**复活**分支（回 `active`、重算 `expires_ts`），命中
    `active` / `superseded` 只做合并——两者都必须与 DB 唯一约束同作用域（评审 P0-3）。
    """
    seen = {s.url for s in prior.sources}
    merged = prior.sources + tuple(s for s in candidate.sources if s.url not in seen)
    domains = {s.domain for s in merged if s.domain}
    revive = prior.status in ("archived", "muted")
    return MergeOp(
        item_id=int(prior.item_id or 0), dedup_key=prior.dedup_key, sources=merged,
        domain_count=len(domains),
        expires_ts=expires_for_tier(tier=prior.tier, event_ts=prior.event_ts,
                                    captured_ts=prior.captured_ts, pinned=prior.pinned,
                                    limits=limits),
        revive=revive, updated_ts=int(now))


def admit(proposals: list[dict], corpus: "Corpus", existing: dict[str, IntelItem], *,
          inst_id: str, limits: IntelLimits, now: float | None = None) -> AdmitResult:
    """六道闸 + 去重 + 条目数封顶。

    Args:
        proposals: 模型 `submit_market_intel` 的 `items` 原样。
        corpus: 本轮语料（只读 `index` / 文档的 title/snippet/url/domain/published_ts）。
        existing: 该 `inst_id` 下**跨全部 status** 的 `dedup_key → IntelItem`（评审 P0-3）。
        inst_id: 恒为完整 instId，不做资产级归并。
        limits: 阈值聚合。
        now: 墙钟秒；缺省取 `time.time()`（纯核心不自带时钟是为了可测，这里只是便利默认）。

    Returns:
        `AdmitResult`。被截断到 `items_per_round` 的部分计 `over_cap`——配置项必须有服务端落点，
        否则它只是一句写在 prompt 里的建议（评审 P2-5）。
    """
    wall = time.time() if now is None else float(now)
    rejects: dict[str, int] = {}
    accepted: list[IntelItem] = []
    merges: list[MergeOp] = []
    seen_keys: set[str] = set()
    for proposal in proposals or []:
        if not isinstance(proposal, dict):
            _bump(rejects, "bad_field")
            continue
        item = _admit_one(proposal, corpus, inst_id=inst_id, limits=limits, now=wall,
                          rejects=rejects)
        if item is None:
            continue
        prior = existing.get(item.dedup_key)
        if prior is not None and prior.item_id:
            merges.append(_merge_existing(item, prior, limits=limits, now=wall))
            continue
        if item.dedup_key in seen_keys:      # 同一轮内模型报了两条同题
            continue
        seen_keys.add(item.dedup_key)
        accepted.append(item)
    accepted.sort(key=lambda it: score_item(it, now=wall), reverse=True)
    if len(accepted) > limits.items_per_round:
        for _ in accepted[limits.items_per_round:]:
            _bump(rejects, "over_cap")
        accepted = accepted[:limits.items_per_round]
    return AdmitResult(tuple(accepted), tuple(merges), rejects)


# ---------------- 整理轮的服务端复核表（§3.4.3） ----------------
@dataclass(frozen=True)
class CurationAction:
    """一个已通过复核的动作（`apply_curation` 的唯一入参形态）。"""

    item_id: int
    action: str                 # promote|demote|archive|merge|keep
    to_tier: str | None
    merge_into_id: int | None
    reason: str
    expires_ts: int             # promote / demote 时**同一条 UPDATE** 里写的新寿命（I-3）


@dataclass(frozen=True)
class ReviewResult:
    approved: tuple[CurationAction, ...]
    rejects: dict[str, int]
    counts: dict[str, int]      # 动作直方图（含被驳回的），逐轮落 `ai_intel_rounds`


def _review_promote(item: IntelItem, to_tier: str | None, *, limits: IntelLimits,
                    now: float) -> str | None:
    """升级复核；返回驳回码或 `None`（通过）。"""
    target = to_tier or tax.next_tier(item.tier)
    if target is None or tax.TIER_ORDER.get(target, -1) != tax.TIER_ORDER.get(item.tier, -1) + 1:
        return "promote_insufficient_evidence"
    if item.status != "active" or item.undated:
        return "promote_insufficient_evidence"
    if item.domain_count < limits.promote_min_domains:
        return "promote_insufficient_evidence"
    if now - item.captured_ts < limits.promote_min_age_s:
        return "promote_insufficient_evidence"
    if item.impact == "low":
        return "promote_insufficient_evidence"
    if target == "long":
        # **`abs()` 不是笔误**（评审 P0-1）：`now - event_ts ≥ 3d` 对未来事件恒为负，照 v1 写法
        # 一条已排期的解锁 / 议息**永远升不进长期**——而那恰恰是长期层语义里点名的内容。
        if abs(now - item.event_ts) < limits.promote_long_min_gap_s:
            return "promote_insufficient_evidence"
        if item.impact != "high" and item.category not in ("macro", "project"):
            return "promote_insufficient_evidence"
    return None


def review_actions(actions: list[dict], items: dict[int, IntelItem], *, limits: IntelLimits,
                   now: float | None = None) -> ReviewResult:
    """模型提议 → 服务端裁决。**模型只提议，服务端裁决，两边的分歧全部写进审计。**

    Args:
        actions: 模型 `curate_intel_library` 的 `actions` 原样。
        items: 该标的当前全部候选条目（`item_id → IntelItem`）。
        limits: 阈值聚合（升级门槛 / 单轮动作上限 / TTL）。
        now: 墙钟秒。

    Returns:
        `ReviewResult`：通过的动作、驳回码直方图、动作直方图。
    """
    wall = time.time() if now is None else float(now)
    rejects: dict[str, int] = {}
    counts: dict[str, int] = {}
    approved: list[CurationAction] = []
    raw = list(actions or [])
    if len(raw) > limits.curate_max_actions:
        rejects["truncated_actions"] = len(raw) - limits.curate_max_actions
        raw = raw[:limits.curate_max_actions]
    for entry in raw:
        if not isinstance(entry, dict):
            _bump(rejects, "bad_action")
            continue
        item = items.get(int(entry.get("id") or 0))
        if item is None or item.item_id is None:
            _bump(rejects, "unknown_item")
            continue
        _bump(counts, str(entry.get("action") or "").strip() or "unknown")
        action, code = _review_one(entry, item, items, limits=limits, now=wall)
        if code:
            _bump(rejects, code)
        elif action is not None:
            approved.append(action)
    return ReviewResult(tuple(approved), rejects, counts)


def _review_one(entry: dict, item: IntelItem, items: dict[int, IntelItem], *,
                limits: IntelLimits, now: float) -> tuple[CurationAction | None, str | None]:
    """单个动作的复核。返回 ``(通过的动作 | None, 驳回码 | None)``。

    `keep` 恒通过且**不产生任何写入**——它的意义是让模型显式表达「我看过它并决定不动」，
    从而把「没提到」与「决定不动」区分开。
    """
    action = str(entry.get("action") or "").strip()
    reason = str(entry.get("reason") or "").strip()[:120]
    to_tier = entry.get("toTier")
    to_tier = str(to_tier) if to_tier in tax.TIERS else None
    if action == "keep":
        return None, None
    if action == "archive":
        if item.protected:
            return None, "archive_protected"          # 两把免死金牌（评审 P1-5）
        return CurationAction(item.item_id, "archive", None, None, reason, 0), None
    if action == "merge":
        target_id = int(entry.get("mergeIntoId") or 0)
        other = items.get(target_id)
        if (other is None or other.item_id == item.item_id or other.status != "active"
                or other.inst_id != item.inst_id):
            return None, "merge_bad_target"
        return CurationAction(item.item_id, "merge", None, target_id, reason, 0), None
    if action == "promote":
        code = _review_promote(item, to_tier, limits=limits, now=now)
        if code:
            return None, code
        target = to_tier or tax.next_tier(item.tier) or item.tier
    elif action == "demote":
        target = to_tier or tax.prev_tier(item.tier)
        if target is None or tax.TIER_ORDER.get(target, -1) != tax.TIER_ORDER.get(
                item.tier, -1) - 1:
            return None, "demote_skips_tier"
    else:
        return None, "bad_action"
    # promote / demote：写 tier 与重算 expires_ts 在**同一条 UPDATE** 里完成（不变式 I-3）。
    return CurationAction(
        item.item_id, action, target, None, reason,
        expires_for_tier(tier=target, event_ts=item.event_ts, captured_ts=item.captured_ts,
                         pinned=item.pinned, limits=limits)), None


# ---------------- 确定性清理（§3.4.4，**不依赖模型**） ----------------
@dataclass(frozen=True)
class CleanupPlan:
    """三步纯规则清理的产物（TTL / 溢出 / 已排期失效）。全部跳过两把免死金牌。"""

    expire_ids: tuple[int, ...]
    overflow_ids: tuple[int, ...]
    lapsed: tuple[CurationAction, ...]

    @property
    def counts(self) -> dict[str, int]:
        return {"ttl": len(self.expire_ids), "overflow": len(self.overflow_ids),
                "lapsed_schedule": len(self.lapsed)}


def plan_cleanup(items: list[IntelItem], *, limits: IntelLimits, now: float) -> CleanupPlan:
    """按当前库存算出三步清理动作。

    这三步是「噪声或无效的短线情报可以进行整理或清理」这条需求的**兜底实现**：模型哑火时噪声
    照样被清。`expires_ts` 恒由**当前 tier** 求出（不变式 I-3）——只要它没随升级重算，第 1 步就会
    在事件发生 72 小时后把中期与长期两层一并扫空。
    """
    expire: list[int] = []
    lapsed: list[CurationAction] = []
    survivors: list[IntelItem] = []
    for item in items:
        if item.status != "active" or item.item_id is None:
            continue
        if item.protected:
            survivors.append(item)
            continue
        if item.expires_ts > 0 and now > item.expires_ts:
            expire.append(item.item_id)
            continue
        # 已排期事件失效（评审 P0-1）：没有这一步，「永远的明天」会在库里堆成一排再也不会发生的
        # 预告，而它们的 `stance` 还在持续喂给交易 Agent。
        if (item.is_scheduled and item.event_ts > 0
                and now > item.event_ts + limits.lapse_grace_s
                and item.updated_ts <= item.event_ts):
            target = tax.prev_tier(item.tier)
            if target is None:
                expire.append(item.item_id)
            else:
                lapsed.append(CurationAction(
                    item.item_id, "demote", target, None, "lapsed_schedule",
                    expires_for_tier(tier=target, event_ts=item.event_ts,
                                     captured_ts=item.captured_ts, pinned=False,
                                     limits=limits)))
                survivors.append(replace(item, tier=target))
            continue
        survivors.append(item)
    overflow: list[int] = []
    for tier in tax.TIERS:
        cap = limits.max_active.get(tier, 40)
        rows = [it for it in survivors if it.tier == tier and not it.protected]
        held = len([it for it in survivors if it.tier == tier])
        if held <= cap:
            continue
        rows.sort(key=lambda it: score_item(it, now=now))
        overflow.extend(int(it.item_id) for it in rows[:held - cap] if it.item_id)
    return CleanupPlan(tuple(expire), tuple(overflow), tuple(lapsed))
