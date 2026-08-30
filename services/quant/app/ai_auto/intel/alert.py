"""情报告警的确定性判定（ai-trade-intel-actionability 轴 A，§3.1.1）。

**纯核心**：无 IO、无网络、无 DB、无 LLM、无时钟（`now` 一律由调用方传入）。

这条纪律不是洁癖——**唤醒一次决策是会花真钱的动作**：它的判据必须能在一个离线用例里被逐条
冻结、被回放、被反证。本轮因此不新增任何 LLM 调用车道，告警判定与 `wake_reason.classify_wake`
同款：一张 `(码, 谓词)` 的元组表，首个命中即抑制。

三条贯穿本模块的判断：

1. **恒有产物**。`plan_alert` 绝不返回 ``None``：要么 `fired`，要么带一个抑制码。空轮也写台账
   ⇒「今天为什么一次都没告警」永远有答案。
2. **一轮至多一条**。两条同时唤醒只会撞在同一个 `_deciding` 槽位上，第二条必然被丢弃——那不如
   根本不发。全部通过后按 `severity` 降序取第一条。
3. **本模块只 import `taxonomy`**（评审 P1-2 / P1-3）：阈值住在 `taxonomy.IntelLimits`，
   `taxonomy` 反过来不 import 本模块，故不成环。
"""
from __future__ import annotations

from dataclasses import dataclass

from . import taxonomy as tax
from .taxonomy import IntelLimits


@dataclass(frozen=True)
class AlertCandidate:
    """本轮 accepted / merges 条目的只读投影（**不含 URL、不含域名、不含正文**）。

    ★ 评审 P1-4：**两个分支的投影方式不同，`gate.MergeOp` 一个内容字段都没有。**

    - `accepted`：直接投影 `gate.IntelItem`（`quoted` 已由 `_build_item` 按本轮已验证引文写好）。
    - `merges`：`MergeOp` 只有 `item_id / dedup_key / sources / domain_count / expires_ts /
      revive / updated_ts / prior_quote*`，故内容字段必须由 `run_collect_round` 手上的
      ``existing[merge.dedup_key]`` 出，`domain_count` 取合并**之后**的新值。
      **不能砍掉 merges 分支**：「同一条风险情报被第二、第三个域名再次报道」只走这条分支，
      而它恰恰是 `min_domains >= 2` 最该放行的那一类（G-31 冻结）。

    Note:
        `summary` 是实施期追加的一个带默认值的字段（见 spec《实施过程发现的方案缺陷》I-1）：
        §4.3 的唤醒句在 ``in_digest=False`` 时要把摘要**就地内联**进 prompt，而 v2 列出的字段表
        里没有任何一个装得下它。它**不参与任何判据**，也不进台账（台账只存 `headline`）。
    """

    item_id: int
    headline: str
    category: str
    tier: str
    stance: str
    impact: str
    confidence: float
    event_ts: int
    captured_ts: int
    domain_count: int
    quoted: bool
    is_scheduled: bool
    verdict: str
    summary: str = ""


@dataclass(frozen=True)
class AlertDecision:
    """**恒有产物**：要么 fired，要么带一个抑制码。绝不返回 None。"""

    fired: bool
    # 评审 P2-11：**轮级抑制（daily_cap / cooldown）时也要带上最高 severity 的候选**——否则台账
    # 行是 `item_id=0` / `headline=""`，动态页签只能说「未告警 · 日配额已满」而说不出被压下去的
    # 是哪一条。只有「本轮一条候选都没有」（`no_candidate`）时才为 None。
    candidate: AlertCandidate | None
    severity: float                            # 0..1
    suppressed_code: str                       # "" 当且仅当 fired=True
    reasons: dict[str, float | int | bool]     # 判据明细，逐条落台账


@dataclass(frozen=True)
class AlertHandoff:
    """从采集轮交到交易会话手上的那一条待处理告警（§3.1.3 的三跳载体）。

    `in_digest` **不是**「有没有编号」的同义词（评审 P0-3）：`_TIER_RANK` 让注入排序 long 在最前、
    ultra 在最后，而告警条目恒是刚采到的 ultra；`prompt_max_chars` 默认只装得下 ~5–6 行。长期层
    一旦攒到 6 条 active，告警条目在**稳态上**就进不了 prompt 了。它由 `IntelRuntime._alert` 从
    **刚刷新的** digest 反查得出，决定 §4.3 的唤醒句走哪一态——绝不允许用「有 ref」冒充它。
    """

    alert_id: int
    candidate: AlertCandidate
    severity: float
    created_wall: float
    ref: str = ""              # "I02"；`cite_enabled=False` 时为 ""
    in_digest: bool = False

    def wire(self, *, now: float) -> dict:
        """`WakeFacts.intel` 的 wire（camelCase）。**不含 URL、不含域名、不含正文**。

        Args:
            now: 渲染时刻（`ageMin` 在这一刻冻结）。

        Returns:
            进 `payload["wake"]["intel"]` 的只读 dict；`wake_text` 的两态渲染器只读它。
        """
        candidate = self.candidate
        return {
            "alertId": int(self.alert_id),
            "itemId": int(candidate.item_id),
            "ref": self.ref,
            "inDigest": bool(self.in_digest),
            "headline": candidate.headline,
            "summary": candidate.summary,
            "category": candidate.category,
            # 已本地化的一句「风险事件·偏空·高」。**在这里编而不是在 `wake_text` 里**：
            # 后者若为了三个中文标签去 import `intel.taxonomy`，就等于让唤醒文案模块拖进整个
            # 情报包（它的 `__init__` 会一路拉到 `db`）。跟随 `degrade_wire` 的既有裁决——
            # 进 wire 的文案由后端管。
            "label": _candidate_label(candidate),
            "tier": candidate.tier,
            "stance": candidate.stance,
            "impact": candidate.impact,
            "domainCount": int(candidate.domain_count),
            "quoted": bool(candidate.quoted),
            "confidence": round(float(candidate.confidence), 4),
            "severity": round(float(self.severity), 4),
            "ageMin": round(age_minutes(candidate, now=now), 2),
        }


def candidates_from_round(admitted, existing: dict, *,
                          item_ids: list[int]) -> list[AlertCandidate]:
    """把一次采集轮的闸结果投影成候选（**两个分支的投影方式不同**，评审 P1-4）。

    Args:
        admitted: `gate.AdmitResult`（鸭子类型：`.accepted` / `.merges` / `.quotes`）。
        existing: ``dedup_key → gate.IntelItem``，`run_collect_round` 手上的那张表。
            **merges 分支的全部内容字段只能从这里出**——`MergeOp` 一个内容字段都没有。
        item_ids: `upsert_intel_items` 的返回值，与 ``admitted.accepted`` **按入参顺序对齐**。
            短于 accepted 时多出来的那几条被跳过（没有 `item_id` 的候选无法落台账、也无法回写）。

    Returns:
        候选列表（顺序无意义，`plan_alert` 自己按 `severity` 排）。
    """
    out: list[AlertCandidate] = []
    for index, item in enumerate(getattr(admitted, "accepted", ()) or ()):
        if index >= len(item_ids or ()):
            break
        out.append(AlertCandidate(
            item_id=int(item_ids[index]), headline=item.headline, category=item.category,
            tier=item.tier, stance=item.stance, impact=item.impact,
            confidence=float(item.confidence), event_ts=int(item.event_ts),
            captured_ts=int(item.captured_ts), domain_count=int(item.domain_count),
            quoted=bool(item.quoted), is_scheduled=bool(item.is_scheduled),
            verdict=str(item.verdict or ""), summary=item.summary))
    quotes = getattr(admitted, "quotes", {}) or {}
    for merge in getattr(admitted, "merges", ()) or ():
        prior = (existing or {}).get(merge.dedup_key)
        if prior is None or not merge.item_id:
            continue
        out.append(AlertCandidate(
            item_id=int(merge.item_id), headline=prior.headline, category=prior.category,
            tier=prior.tier, stance=prior.stance, impact=prior.impact,
            confidence=float(prior.confidence), event_ts=int(prior.event_ts),
            # 合并轮的「被采到」时刻是**这一次再次报道**的时刻，不是当初那一次。
            captured_ts=int(merge.updated_ts or prior.captured_ts),
            # ★ 合并**之后**的新值：「第二个域名再次报道」正是 `min_domains >= 2` 最该放行的
            # 那一类，取旧值等于让这条分支永远达不到门槛（G-31 冻结）。
            domain_count=int(merge.domain_count),
            quoted=bool(getattr(merge, "prior_quoted", False)
                        or bool(quotes.get(merge.dedup_key))),
            is_scheduled=bool(prior.is_scheduled), verdict=str(prior.verdict or ""),
            summary=prior.summary))
    return out


def _candidate_label(candidate: AlertCandidate) -> str:
    """``"风险事件·偏空·高"``：唤醒句与台账行共用的一句短标签。"""
    return "·".join((
        tax.CATEGORY_LABELS.get(candidate.category, candidate.category),
        tax.STANCE_LABELS.get(candidate.stance, candidate.stance),
        tax.IMPACT_LABELS.get(candidate.impact, candidate.impact),
    ))


def _clamp01(value: float) -> float:
    return 0.0 if value < 0.0 else (1.0 if value > 1.0 else value)


def age_minutes(candidate: AlertCandidate, *, now: float) -> float:
    """候选的事件年龄（分钟）。未来事件为负——`freshness` 因此对它恒为 1.0。

    参照点取 ``event_ts if event_ts > 0 else captured_ts``，与 `gate.score_item` 同一条口径
    （无事件时刻的条目不该因为「时间未知」就被判成无限旧）。
    """
    ref = candidate.event_ts if candidate.event_ts > 0 else candidate.captured_ts
    return (float(now) - float(ref or now)) / 60.0


def severity(candidate: AlertCandidate, *, now: float, limits: IntelLimits) -> float:
    """告警的严重度（0..1）：四项加权 × 新鲜度衰减。

    `impact_w` 复用 `taxonomy.IMPACT_W`（**不是** `gate.SCORE_IMPACT_W`）：前者的 ``low = 0.0``
    让「低影响力恒不告警」成为一条算术性质而不是一条要单独记住的规则；后者服务注入排序，那里
    `low` 必须仍有正分。**两张表不合并**（评审 P2-6）。

    Args:
        candidate: 目标候选。
        now: 墙钟秒。
        limits: 提供 `alert_max_event_age_min`（新鲜度的分母）。

    Returns:
        0..1 的严重度；同分时由调用方按候选顺序取第一条（稳定排序）。
    """
    impact_w = tax.IMPACT_W.get(candidate.impact, 0.0)
    domains = min(1.0, max(0, int(candidate.domain_count)) / 3.0)
    quoted = 1.0 if candidate.quoted else 0.0
    base = _clamp01(0.45 * impact_w + 0.20 * domains + 0.15 * quoted
                    + 0.20 * float(candidate.confidence))
    window = max(1.0, float(limits.alert_max_event_age_min))
    freshness = _clamp01(1.0 - age_minutes(candidate, now=now) / window)
    return base * freshness


def _reasons(candidate: AlertCandidate | None, *, now: float,
             limits: IntelLimits) -> dict[str, float | int | bool]:
    """判据明细（逐条落台账的 `reasons_json`）。候选为 None ⇒ 空表。"""
    if candidate is None:
        return {}
    return {
        "impactW": round(tax.IMPACT_W.get(candidate.impact, 0.0), 4),
        "freshness": round(_clamp01(
            1.0 - age_minutes(candidate, now=now)
            / max(1.0, float(limits.alert_max_event_age_min))), 4),
        "ageMin": round(age_minutes(candidate, now=now), 2),
        "domainCount": int(candidate.domain_count),
        "confidence": round(float(candidate.confidence), 4),
        "quoted": bool(candidate.quoted),
    }


def _suppress_round_level(candidates: list[AlertCandidate], *, limits: IntelLimits,
                          now: float, last_fired_ts: int, day_fired: int) -> str:
    """判据 0–3（**轮级**）：总闸 / 空轮 / 日配额 / 冷却。返回抑制码或 ``""``。

    轮级判据**优先于**逐条判据：额度满时不必逐条算。但按 P2-11，调用方仍要把最高分候选投影进
    `AlertDecision.candidate`——否则台账说不出「被压下去的是哪一条」。
    """
    if not limits.alert_enabled:
        return "disabled"
    if not candidates:
        return "no_candidate"
    if day_fired >= max(1, int(limits.alert_daily_cap)):
        return "daily_cap"
    if float(now) - float(last_fired_ts or 0) < float(limits.alert_cooldown_min) * 60.0:
        return "cooldown"
    return ""


def _suppress_item_level(candidate: AlertCandidate, *, limits: IntelLimits,
                         now: float) -> str:
    """判据 4–11（**逐条**）。返回抑制码或 ``""``（该条够格）。

    写成一张 `(码, 谓词)` 元组表而不是八个 `if`：`plan_alert` 的判定链有 12 个分支，圈复杂度会
    越过 CLAUDE.md 的 10，而查找表正是 §六「循环里出现复杂条件分支」指向的形状
    （与 `wake_reason.classify_wake` 的 `conditions` 同款）。
    """
    min_impact_w = tax.IMPACT_W.get(limits.alert_min_impact, 1.0)
    checks: tuple[tuple[str, bool], ...] = (
        ("impact_below_bar", tax.IMPACT_W.get(candidate.impact, 0.0) >= min_impact_w),
        ("category_out", candidate.category in limits.alert_categories),
        ("refuted", candidate.verdict != "refuted"),
        ("scheduled_future", limits.alert_allow_scheduled or not candidate.is_scheduled),
        ("stale_event",
         age_minutes(candidate, now=now) <= float(limits.alert_max_event_age_min)),
        ("few_domains", int(candidate.domain_count) >= int(limits.alert_min_domains)),
        ("low_confidence",
         float(candidate.confidence) >= float(limits.alert_min_confidence)),
        ("no_quote", (not limits.alert_require_quote) or candidate.quoted),
    )
    for code, ok in checks:
        if not ok:
            return code
    return ""


def plan_alert(candidates: list[AlertCandidate], *, limits: IntelLimits, now: float,
               last_fired_ts: int, day_fired: int) -> AlertDecision:
    """一次采集轮的告警判定（**恒有产物**）。

    Args:
        candidates: 本轮 accepted + merges 的只读投影（顺序不影响结果，排序在本函数内做）。
        limits: 阈值聚合（十项 `alert_*` 全部住在 `IntelLimits`，见 §3.1.1 的方框）。
        now: 墙钟秒。
        last_fired_ts: 该标的最近一次 `fired=True` 的台账时刻（0 = 从未告警过）。
        day_fired: 当前 UTC 日已 fired 的条数。

    Returns:
        `AlertDecision`。`fired=True` 时 `suppressed_code` 恒为 ``""``，反之亦然。
        **一轮至多一条**：全部通过后按 `severity` 降序取第一条。
    """
    ranked = sorted(candidates or (), key=lambda c: severity(c, now=now, limits=limits),
                    reverse=True)
    top = ranked[0] if ranked else None
    round_code = _suppress_round_level(ranked, limits=limits, now=now,
                                       last_fired_ts=last_fired_ts, day_fired=day_fired)
    if round_code:
        return AlertDecision(False, top, severity(top, now=now, limits=limits) if top else 0.0,
                             round_code, _reasons(top, now=now, limits=limits))
    eligible: list[AlertCandidate] = []
    first_code = ""
    for candidate in ranked:
        code = _suppress_item_level(candidate, limits=limits, now=now)
        if code:
            first_code = first_code or code
            continue
        eligible.append(candidate)
    if not eligible:
        # 逐条全被抑制：报**最高分那一条**的码。G-4 的每条用例只造一个「只差那一个判据」的
        # 候选，故这一步在用例里是无歧义的；多候选时取最高分是为了让台账说的是最接近够格的那条。
        return AlertDecision(False, top, severity(top, now=now, limits=limits) if top else 0.0,
                             first_code or "no_candidate",
                             _reasons(top, now=now, limits=limits))
    winner = eligible[0]
    return AlertDecision(True, winner, severity(winner, now=now, limits=limits), "",
                         _reasons(winner, now=now, limits=limits))
