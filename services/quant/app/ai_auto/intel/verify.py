"""核查轮的纯核心：候选选取 + 判定复核表 + 确定性后果计划（closed-loop 轴 C）。

**纯核心**：无 IO、无网络、无 DB、无时钟（`now` 一律由调用方传入）。本模块是「入口有闸、出口
也有闸」这条承诺的落点——入库时六道闸判的是**它当时是不是被如实转述**，本模块判的是**它后来
是不是被证实**。

三条贯穿本模块的判断：

1. **「没找到证据」不是「被推翻」。** 无有效 ``D`` 引用的非 ``unresolved`` 判定一律降级为
   ``unresolved`` 并计 ``verdict_no_ref``（G-7）。这是 R-3 那条「本轮最危险的失效模式」的服务端硬闸。
2. **两击制**（评审 P1-12/P1-13）。首击只写 verdict + 罚置信度 + **立即停止注入**，
   ``tier`` / ``status`` / ``expires_ts`` 一个字节都不动；隔 ``verify_recheck_h`` 的第二次独立
   核查仍判 ``refuted`` 才降一层。**任何情形都不因 refuted 归档**——归档留给那五条不依赖模型
   判断的确定性规则。
3. **撤销出口不能是旋转门**（评审 P0-4）。用户点「我不同意」之后该条目对候选池的三条判据
   **全部**不再成立（`user_edited` 兼任判定锁，且 `verify_count` 不被清零）。
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from . import taxonomy as tax
from .gate import IntelItem, expires_for_tier
from .taxonomy import IntelLimits

# 核查候选的编号（与语料的 ``D`` **分开命名空间**，否则模型会在两张表之间串号，而串号不报错）。
ITEM_REF_RE = re.compile(r"^C[0-9]{1,2}$")
DOC_REF_RE = re.compile(r"^D[0-9]{1,2}$")

# 复核驳回 / 降级码（逐轮进 `ai_intel_rounds.actions_json`）。
VERDICT_REJECT_CODES: tuple[str, ...] = (
    "bad_item_ref",      # itemRef 不在 C 表 ⇒ 整条丢弃
    "bad_verdict",       # verdict 值域外 ⇒ 整条丢弃
    "bad_ref",           # refs 里有不在 D 表的编号 ⇒ **该编号**丢弃，不丢整条
    "verdict_no_ref",    # 非 unresolved 却指不出任何有效 D 编号 ⇒ 降为 unresolved（G-7）
    "verdict_no_reason",  # reason 空白 ⇒ 降为 unresolved
    "dup_item_ref",      # 同一 itemRef 出现多次 ⇒ 取第一条
    # evidence-grounding §3.3.3：仅 strict —— `refuted` 是本层**唯一**会立刻停止一条情报注入的
    # 判定，它的举证责任应当高于 `confirmed`。指不出一段已验证原文的证伪，降为 unresolved。
    "refute_without_quote",
)


def format_item_ref(index: int) -> str:
    """第 k 条候选（k 从 1 计）的编号。"""
    return f"C{int(index)}"


@dataclass(frozen=True)
class VerdictPlan:
    """一条判定通过复核后的**确定性后果**（写段只执行，不再判断）。

    Attributes:
        item_id: 目标条目。
        verdict: 落库的判定（``unresolved`` 时**不覆盖**已有的 ``confirmed``，见 `overwrite_verdict`）。
        reason: ≤120 字的一句话理由。
        refs: 依据的语料编号（已过滤掉不在 D 表里的）。
        confidence: 重算后的置信度；``None`` ⇒ 不写这一列。
        to_tier: 第二击降层的目标层；``None`` ⇒ **不动 tier**（首击与受保护条目恒 None）。
        expires_ts: 与 `to_tier` 同一条 UPDATE 里写的新寿命（不变式 I-3）；`to_tier` 为 None 时无意义。
        overwrite_verdict: ``unresolved`` 判定是否覆盖既有 verdict。恒 False——
            「这次没查出来」不该抹掉「上次查实了」。
        verify_count: 落库的新核查次数（**恒为旧值 +1**，绝不清零）。
        resolve_schedule: 轴 D 的兑现位（evidence-grounding §3.4.2）。为真 ⇒ 写段把
            ``is_scheduled`` 置假并落 ``schedule_resolved_ts = now``，该条目从此退出「已排期
            事件失效」这条确定性清理。**带默认值 ⇒ 既有构造零改动**。
    """

    item_id: int
    verdict: str
    reason: str
    refs: tuple[str, ...] = ()
    confidence: float | None = None
    to_tier: str | None = None
    expires_ts: int = 0
    overwrite_verdict: bool = True
    verify_count: int = 1
    resolve_schedule: bool = False


@dataclass(frozen=True)
class VerifyReview:
    """一次核查轮的复核产物。`counts` 逐轮落 `ai_intel_rounds.actions_json`。"""

    plans: tuple[VerdictPlan, ...]
    counts: dict[str, int]


def _bump(counter: dict[str, int], code: str) -> None:
    counter[code] = counter.get(code, 0) + 1


# ---------------- 谁被送去核查（§3.3.1） ----------------
def _priority(item: IntelItem, *, limits: IntelLimits, now: float) -> int | None:
    """候选优先级（1 最高，`None` = 不入池）。

    三条判据里的 ``verify_count == 0`` 与 ``not user_edited`` 是评审 P0-4 的修法，不是冗余：
    唯一的撤销出口是 `PATCH clearVerdict` 把 `verdict` 清成 ``""`` 并置 `user_edited`；若 P2
    只看 ``verdict == ""``，这条目会**立刻**回到候选池最前面并在下一轮被重新判成 ``refuted``——
    用户的显式意愿被系统静默撤销。
    """
    if item.user_edited:
        return None
    # P1：已排期事件的时刻已过 ——「解锁到底发生了没有」是本层最硬的可证伪问题。
    if item.is_scheduled and item.event_ts > 0 and item.event_ts <= now:
        return 1
    # P2：从没核查过的中长期条目 —— 它们是被反复注入的那些，最值得被复核。
    if (item.tier in ("mid", "long") and not item.verdict and item.verify_count == 0
            and now - item.captured_ts >= limits.verify_min_age_h * 3600.0):
        return 2
    # P3：上次没判出来 / 只被判过一次 refuted，给它有限次重来（收 `refuted` 是两击制的前提）。
    if (item.verdict in ("unresolved", "refuted")
            and now - item.verdict_ts >= limits.verify_recheck_h * 3600.0
            and item.verify_count < limits.verify_max_attempts):
        return 3
    return None


def plan_verification(items, *, limits: IntelLimits, now: float) -> tuple[IntelItem, ...]:
    """选出本轮的核查候选（截断到 `limits.verify_max_items`）。

    候选池 = ``status == "active"`` 且 ``origin == "agent"`` 的条目：**用户手写的条目不送验证**
    ——用户写下的东西轮不到模型去判真假（手动路径见 `POST /intel/items/{itemId}/verify`）。
    **``pinned`` 的条目照样进候选**：免死金牌管的是**后果**（不被自动降级），不是**检查**（G-8）。

    Args:
        items: 该标的的条目全集（本函数自行过滤 status / origin）。
        limits: 四个 verify 阈值的来源（纯核心从不自己读 `Settings`）。
        now: 墙钟秒。

    Returns:
        按优先级升序、同级按 `event_ts` 升序（最老的先验）的候选元组。
    """
    scored: list[tuple[int, int, IntelItem]] = []
    for item in items or []:
        if item.status != "active" or item.origin != "agent" or not item.item_id:
            continue
        rank = _priority(item, limits=limits, now=now)
        if rank is None:
            continue
        scored.append((rank, int(item.event_ts or item.captured_ts or 0), item))
    scored.sort(key=lambda row: (row[0], row[1]))
    return tuple(item for _rank, _ts, item in scored[:max(1, int(limits.verify_max_items))])


# ---------------- 服务端复核与四种后果（§3.3.4） ----------------
def _clean_refs(raw, doc_refs: frozenset[str], counts: dict[str, int]) -> tuple[str, ...]:
    """过滤 `refs`：不在 D 表的编号**逐个**丢弃并计 `bad_ref`（不丢整条）。"""
    out: list[str] = []
    for entry in (raw or []) if isinstance(raw, (list, tuple)) else []:
        token = str(entry or "").strip().upper()
        if not DOC_REF_RE.match(token) or token not in doc_refs:
            _bump(counts, "bad_ref")
            continue
        if token not in out:
            out.append(token)
    return tuple(out)


def _plan_confirmed(item: IntelItem, reason: str, refs: tuple[str, ...], *,
                    limits: IntelLimits, now: float) -> VerdictPlan:
    """``confirmed``：置信度加成（**封顶仍是 1.0**），tier / status 不动。

    evidence-grounding §3.4.2：一件**已排期且时刻已过**的事被证实 ⇒ 置 `resolve_schedule`。

    Args:
        now: 墙钟秒。**必填 keyword-only、绝不给默认值**——给 `now=None` 再 `time.time()` 兜底，
            漏传的症状就不是 `TypeError`，而是「判据用了一个跟这一轮无关的时刻」；而漏掉
            `event_ts <= now` 这半条的后果是：一条被 P2 通道（中长期从未核查）选中的**未来**
            排期事件被判 `confirmed`，立刻落兑现位，从此**永不失效**——那正是 F-1 换了个位置复发。

    Note:
        **不动 `event_ts`、不动 `tier`、不动 `expires_ts`**。后者容易被「顺手补上」以致违反
        不变式 I-3——但 `expires_for_tier` 的输入是 `max(event_ts, captured_ts)` 与 `tier`，
        两者都没变，重算的结果与原值逐字节相同；写一次多余的 UPDATE 只会让「哪里必须重算
        expires」这条不变式的边界变模糊。
    """
    confidence = min(1.0, round(float(item.confidence) + limits.verify_confirm_bonus, 4))
    resolved = bool(item.is_scheduled and item.event_ts > 0 and item.event_ts <= float(now))
    return VerdictPlan(int(item.item_id), "confirmed", reason, refs, confidence=confidence,
                       verify_count=int(item.verify_count) + 1, resolve_schedule=resolved)


def _plan_refuted(item: IntelItem, reason: str, refs: tuple[str, ...], *,
                  limits: IntelLimits) -> VerdictPlan:
    """``refuted`` 的**两击制**（评审 P1-12）。

    - 受保护条目（`pinned` / `user_edited`）：只写 verdict / 理由 / 次数，**不动 tier、不动
      status、不动 confidence**；但注入照样停（G-8）。
    - 首击（`verify_count == 0`）：写 verdict + 罚置信度，**tier / status / expires_ts 一个字节
      都不动**。注入在 `build_digest` 里立刻停。
    - 第二击（`verify_count >= 1`）：才降一层，且 `expires_ts` **必须在同一条 UPDATE 里**按新
      tier 重算（不变式 I-3）。**已在 ultra 也不归档**——归档买不到任何东西，却把一个可逆动作
      换成了一个要用户去「已归档」里翻的动作。
    """
    next_count = int(item.verify_count) + 1
    if item.protected:
        return VerdictPlan(int(item.item_id), "refuted", reason, refs,
                           verify_count=next_count)
    penalty = round(float(item.confidence) * limits.verify_refute_penalty, 4)
    if int(item.verify_count) < 1:
        return VerdictPlan(int(item.item_id), "refuted", reason, refs, confidence=penalty,
                           verify_count=next_count)
    target = tax.prev_tier(item.tier)
    if target is None:
        return VerdictPlan(int(item.item_id), "refuted", reason, refs, confidence=penalty,
                           verify_count=next_count)
    return VerdictPlan(
        int(item.item_id), "refuted", reason, refs, confidence=penalty, to_tier=target,
        expires_ts=expires_for_tier(tier=target, event_ts=item.event_ts,
                                    captured_ts=item.captured_ts, pinned=item.pinned,
                                    limits=limits),
        verify_count=next_count)


def _plan_one(entry: dict, item: IntelItem, doc_refs: frozenset[str], *,
              limits: IntelLimits, counts: dict[str, int], now: float,
              has_quote: bool) -> VerdictPlan | None:
    """单条判定的复核 → 后果计划；整条被丢弃时回 ``None``。

    Args:
        now: 墙钟秒（逐层下传到 `_plan_confirmed`，**无默认值**）。
        has_quote: 这条判定有没有 ≥1 段**已验证**原文引证（evidence-grounding §3.3.3）。
            纯核心自己判不了这件事——`doc_refs` 只是一个字符串集合、没有任何正文，故由
            `service_verify` 先跑 `quotes.verify_verdict_quotes` 再把结论传进来。
    """
    verdict = str(entry.get("verdict") or "").strip()
    if verdict not in tax.VERDICTS:
        _bump(counts, "bad_verdict")
        return None
    reason = str(entry.get("reason") or "").strip()[:120]
    refs = _clean_refs(entry.get("refs"), doc_refs, counts)
    if not reason:
        _bump(counts, "verdict_no_reason")
        verdict = "unresolved"
    elif verdict != "unresolved" and not refs:
        # G-7：空口断言一律降为待定，**且不执行任何降级**。
        _bump(counts, "verdict_no_ref")
        verdict = "unresolved"
    elif verdict == "refuted" and limits.quote_mode == "strict" and not has_quote:
        # evidence-grounding §3.3.3：`soft` 下**不降级**（只记数），与「soft 只加不减」一致。
        _bump(counts, "refute_without_quote")
        verdict = "unresolved"
    _bump(counts, verdict)
    if verdict == "confirmed":
        return _plan_confirmed(item, reason, refs, limits=limits, now=now)
    if verdict == "refuted":
        return _plan_refuted(item, reason, refs, limits=limits)
    # `unresolved` 只写 verdict_ts / verify_count；**不覆盖已有的 confirmed**。
    return VerdictPlan(int(item.item_id), "unresolved", reason, refs,
                       overwrite_verdict=not item.verdict,
                       verify_count=int(item.verify_count) + 1)


def review_verdicts(verdicts, candidates: dict[str, IntelItem], doc_refs, *,
                    limits: IntelLimits, now: float, quotes_by_ref) -> VerifyReview:
    """模型判定 → 服务端裁决。**模型只提议，服务端裁决，两边的分歧全部写进审计。**

    Args:
        verdicts: 模型 `submit_intel_verdicts` 的 ``verdicts`` 原样。
        candidates: 本轮候选的 ``C 编号 → IntelItem``。
        doc_refs: 本轮语料的 ``D`` 编号集合（`corpus.index` 的键）。
        limits: 置信度加成 / 惩罚系数的来源。
        now: 墙钟秒。**必填 keyword-only、无默认值**（evidence-grounding 评审 P1-1）——
            本模块的 docstring 第一段就写着「无时钟」，而轴 D 的兑现判据需要它。
        quotes_by_ref: ``{C 编号: 已验证引文元组}``（evidence-grounding 评审 P1-3）。
            键**是 `C` 编号不是 `D` 编号**：两张表是分开的命名空间，串号不报错。
            **不给默认值**：给了默认值，漏传的症状就是「strict 下 `refute_without_quote`
            这条硬约束永远不生效」而不是一个 `TypeError`——那正是本仓库反复登记的静默空转。

    Returns:
        `VerifyReview`：后果计划 + 计数直方图（含被驳回的）。
    """
    quotes = quotes_by_ref or {}
    counts: dict[str, int] = {}
    plans: list[VerdictPlan] = []
    seen: set[str] = set()
    refs_set = frozenset(str(ref).upper() for ref in (doc_refs or ()))
    for entry in list(verdicts or []):
        if not isinstance(entry, dict):
            _bump(counts, "bad_item_ref")
            continue
        token = str(entry.get("itemRef") or "").strip().upper()
        item = candidates.get(token)
        if not ITEM_REF_RE.match(token) or item is None:
            _bump(counts, "bad_item_ref")
            continue
        if token in seen:
            _bump(counts, "dup_item_ref")
            continue
        seen.add(token)
        plan = _plan_one(entry, item, refs_set, limits=limits, counts=counts, now=now,
                         has_quote=bool(quotes.get(token)))
        if plan is not None:
            plans.append(plan)
    return VerifyReview(tuple(plans), counts)


def plan_all_unresolved(candidates, *, note: str) -> tuple[VerdictPlan, ...]:
    """语料为空时的确定性后果：**全部候选记 `unresolved` 并 `verify_count+1`**。

    「查不到」是一个真实的核查结论，不是一次失败——把它记成失败会让同一批候选在下一轮被
    原样再查一次，而那正是烧钱且什么都不产出的路径。
    """
    return tuple(
        VerdictPlan(int(item.item_id), "unresolved", note[:120],
                    overwrite_verdict=not item.verdict,
                    verify_count=int(item.verify_count) + 1)
        for item in candidates or () if item.item_id)
