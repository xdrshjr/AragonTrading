"""整理轮的完整编排（ai-trade-market-intel §3.4 + intel-actionability 轴 B / 轴 C）。

**本模块是从 `service.py` 整段搬过来的，零语义变化**：actionability 轮给采集轮加了告警写段、
给整理轮加了战绩举证责任与矛盾裁决之后，`service.py` 实测 919 行，越过 CLAUDE.md 的 800 行
阈值，而 CLAUDE.md 对越线的处置是「超了就再下沉一层」而不是放宽阈值（spec §十二 / `EG-I2`
的成文修法）。文件名逐字沿用同目录里 `service_verify.py` 的既有范式——三种轮各一个编排文件。

搬家的方向是**单向**的：本模块 import `service` 的公开面（`IntelDeps` / `RoundOutcome` /
`item_from_wire` / `solve_efficacy` / `load_conflicts` / `next_due_for`），`service` 侧只留一个
**延迟导入**的薄转发（逐行照抄 `IntelRuntime.run_verify` 对 `service_verify` 的既有做法），故
`service.run_curate_round` 这个既有调用点一个字节不改。

整理轮的两条既有纪律仍然成立：

- **确定性清理不依赖模型**（TTL / 溢出 / 已排期失效 / 存量扫除）：模型哑火时噪声照样被清；
- **写段一次提交**（不变式 I-4）：复核通过的动作 → 四步清理 → 写轮 → 推节律，同一个事务。
"""
from __future__ import annotations

import asyncio
import logging

from ...db import repositories as repo
from . import cleanup as cleanup_mod
from . import conflict as conflict_mod
from . import gate, prompts
from . import taxonomy as tax
from .agent import run_round
from .gate import IntelItem
from .service import (
    IntelDeps,
    RoundOutcome,
    item_from_wire,
    load_conflicts,
    next_due_for,
    solve_efficacy,
)

logger = logging.getLogger(__name__)


# ---------------- 整理轮 ----------------
async def run_curate_round(deps: IntelDeps, inst_id: str) -> RoundOutcome:
    """跑一轮整理：模型提议 → 服务端复核 → 执行 → **确定性清理** → 写轮。

    确定性清理（TTL / 溢出 / 已排期失效 / 孤儿 superseded）**不依赖模型**：模型哑火时噪声照样
    被清。这正是「噪声或无效的短线情报可以进行整理或清理」这条需求的兜底实现。
    """
    now = deps.now()
    started = int(now)
    async with deps.sessionmaker()() as db:
        scope = await repo.load_intel_scope(db, inst_id)
        # closed-loop 评审 P1-8：整理轮的只读会话此前**不查**黑名单（只有采集轮查）。不补这一行，
        # `plan_cleanup` 的存量扫除就永远算出空集——而 `blocked_domains` 是必填形参，漏传会是
        # `TypeError` 而不是静默空转，两条一起才根治。
        blocked = frozenset(await repo.list_intel_blocked_domains(db, inst_id))
        # * actionability 评审 P0-1：整理轮**自解一次** `Efficacy`。轴 B 的两个消费点
        # （`review_actions` 的 `promote_weak_record` 与 `plan_cleanup` 的溢出淘汰打分）都住在
        # 这一轮里，而它拿不到 `IntelRuntime` 的缓存——照 v1 实施的结果是这两处在默认路径上
        # 恒 `eff=None`，四个消费点里只有两个真的活着，且完全静默。
        eff = await solve_efficacy(db, deps, inst_id, scope)
        items = [item_from_wire(w) for w in scope]
        conflicts, conflict_rows = await load_conflicts(db, inst_id, limits=deps.limits,
                                                        now=now, items=items)
    live = [it for it in items if it.status == "active" and it.item_id]

    review = None
    usage_in = usage_out = 0
    degrade_code = ""
    if deps.llm is not None and live:
        result = await run_round(
            deps.llm,
            system=prompts.build_curate_system_prompt(inst_id, deps.limits),
            user=prompts.build_curate_user_prompt(
                render_listing(live, now=now),
                conflicts=render_conflicts(conflict_rows, live)),
            tool_name=prompts.CURATE_TOOL_NAME,
            tool_description=prompts.CURATE_TOOL_DESCRIPTION,
            tool_schema=prompts.build_curate_tool_schema(deps.limits),
            timeout_s=float(deps.settings.ai_trade_intel_timeout_s))
        usage_in, usage_out = result.input_tokens, result.output_tokens
        if result.degrade:
            degrade_code = result.degrade
        else:
            review = gate.review_actions(list(result.raw.get("actions") or []),
                                         {int(it.item_id): it for it in live},
                                         limits=deps.limits, now=now,
                                         context=gate.ReviewContext(
                                             eff=eff, conflicts=conflicts,
                                             max_weight=deps.limits.efficacy_max_weight))
    elif deps.llm is None:
        degrade_code = "no_provider"

    actions = list(review.approved) if review else []
    # 存量扫除单键（closed-loop §11）：关掉时**传空集**而不是跳过整步——`blocked_domains` 是必填
    # 形参，空集是它的诚实取值，而「跳过整步」会让 `counts["blocked_sweep"]` 这一键整键缺席。
    sweep_on = bool(getattr(deps.settings, "ai_trade_intel_blocked_sweep_enabled", True))
    # §3.4.4：确定性清理跑在**模型动作执行之后**。用动作前的快照算清理计划会让一条刚被升进中期
    # 的情报仍然按超短层参与溢出淘汰——那正是「算了却算错了对象」的形状。
    projected = _after_actions(live, actions)
    plan = cleanup_mod.plan_cleanup(projected, limits=deps.limits, now=now,
                                    blocked_domains=(blocked if sweep_on else frozenset()),
                                    eff=eff)
    counts: dict[str, int] = dict(review.counts) if review else {}
    rejects = dict(review.rejects) if review else {}
    counts.update(_conflict_counts(deps, conflicts, projected, now=now))
    return await _persist_curate(deps, inst_id, started=started, actions=actions, plan=plan,
                                 counts=counts, rejects=rejects, degrade_code=degrade_code,
                                 tokens=(usage_in, usage_out))


def _conflict_counts(deps: IntelDeps, conflicts, projected: list[IntelItem], *,
                     now: float) -> dict[str, int]:
    """`conflictsOpen` / `conflictsResolved`（**服务端重算求差，不采信模型自报**，G-22）。

    「解决了」的定义是**这一对在动作落库之后不再成对**——降级、归档、合并、或其中一条被判
    `refuted` 都算。模型说「我已解决 3 对」而实际只解决 1 对时，轮存根记的是 1。
    """
    if not conflicts:
        return {}
    after = {(p.a_id, p.b_id) for p in conflict_mod.find_conflicts(
        projected, min_overlap=deps.limits.conflict_min_overlap,
        max_pairs=conflict_mod.MAX_CONFLICT_PAIRS, now=now)}
    before = {(p.a_id, p.b_id) for p in conflicts}
    return {"conflictsOpen": len(before), "conflictsResolved": len(before - after)}


async def _persist_curate(deps: IntelDeps, inst_id: str, *, started: int, actions, plan,
                          counts: dict, rejects: dict, degrade_code: str,
                          tokens: tuple[int, int]) -> RoundOutcome:
    """整理轮的写段：复核通过的动作 → 三步确定性清理 → 写轮 → 推节律，**一次提交**。"""
    usage_in, usage_out = tokens
    async with deps.sessionmaker()() as db:
        applied = await repo.apply_intel_curation(db, [
            {"itemId": a.item_id, "action": a.action, "toTier": a.to_tier,
             "mergeIntoId": a.merge_into_id, "reason": a.reason, "expiresTs": a.expires_ts}
            for a in actions], now=started, commit=False)
        lapsed = await repo.apply_intel_curation(db, [
            {"itemId": a.item_id, "action": a.action, "toTier": a.to_tier,
             "mergeIntoId": None, "reason": a.reason, "expiresTs": a.expires_ts}
            for a in plan.lapsed], now=started, commit=False)
        await repo.archive_intel_items(db, list(plan.expire_ids), reason="ttl", now=started,
                                       commit=False)
        await repo.archive_intel_items(db, list(plan.overflow_ids), reason="overflow",
                                       now=started, commit=False)
        # closed-loop §3.4.2 第 5 步：全部来源都被拉黑的存量条目（G-9②）。
        await repo.archive_intel_items(db, list(plan.blocked_ids), reason="source_blocked",
                                       now=started, commit=False)
        await repo.purge_orphan_superseded(db, inst_id, commit=False)
        # closed-loop G-13：引用明细的保留期裁剪顺手挂在整理轮末尾。**只删明细、不回滚统计列**
        # ——统计列是已发布的读数，为了省几行明细去追溯改写它们比多留几行明细坏得多。
        pruned = await repo.prune_intel_citations(
            db, before_ts=int(started - float(getattr(
                deps.settings, "ai_trade_intel_cite_retention_d", 180.0)) * 86400),
            commit=False)
        histogram = {**counts, **{f"applied_{k}": v for k, v in applied.items()},
                     **plan.counts, **{f"rejected_{k}": v for k, v in rejects.items()}}
        if lapsed:
            histogram["applied_lapsed"] = sum(lapsed.values())
        if pruned:
            histogram["citations_pruned"] = pruned
        # actionability R-9：告警台账的保留期裁剪顺手挂在同一处（**不新增任何清理点**）。
        alerts_pruned = await repo.prune_intel_alerts(
            db, before_ts=int(started - float(getattr(
                deps.settings, "ai_trade_intel_alert_retention_d", 30.0)) * 86400),
            commit=False)
        if alerts_pruned:
            histogram["alerts_pruned"] = alerts_pruned
        # evidence-grounding §3.1.4 第 6 步：正文缓存的三道闸（TTL / 行数 / 字节）。
        # **只删缓存，绝不动引文**——引文自带原文副本，它必须比缓存活得久。
        docs_pruned = await _prune_documents(deps, db, now=started)
        if docs_pruned:
            histogram["documents_pruned"] = docs_pruned
        status = "degraded" if degrade_code else "ok"
        await repo.write_intel_round(db, {
            "instId": inst_id, "kind": "curate", "groupId": deps.group_id,
            "startedTs": started, "finishedTs": int(deps.now()), "status": status,
            "actions": histogram, "inputTokens": usage_in, "outputTokens": usage_out,
            "degradeCode": degrade_code}, commit=False)
        await repo.bump_intel_watch(db, inst_id, kind="curate", status=status,
                                    next_due_ts=next_due_for(deps, fail_streak=0),
                                    curate_reset=True, commit=False)
        await db.commit()
    return RoundOutcome(inst_id, "curate", status, degrade_code, actions=histogram)


async def _prune_documents(deps: IntelDeps, db, *, now: int) -> int:
    """跑一遍正文缓存的三道闸；返回删掉的总行数。**绝不阻断整理轮**（不变式 I-2）。"""
    ttl_h = float(getattr(deps.settings, "ai_trade_intel_reader_cache_ttl_h", 72.0))
    max_rows = int(getattr(deps.settings, "ai_trade_intel_reader_cache_max_rows", 4000))
    body_max = int(getattr(deps.settings, "ai_trade_intel_reader_body_max_chars", 20_000))
    try:
        counts = await repo.prune_documents(db, now=now, ttl_s=ttl_h * 3600.0,
                                            max_rows=max_rows,
                                            max_bytes=max_rows * body_max, commit=False)
    except asyncio.CancelledError:
        raise
    except Exception as exc:      # noqa: BLE001 —— 缓存清理是加法，失败只放弃这一次
        logger.warning("ai-trade intel document prune failed: %s", exc)
        return 0
    return sum(counts.values())


def _after_actions(items: list[IntelItem], actions) -> list[IntelItem]:
    """把已过复核的动作投影到内存中的库存快照上（**不落库**，只为算清理计划）。

    与 `repo.apply_intel_curation` 的语义逐条对齐：`promote`/`demote` 改 tier 与寿命、`archive`
    置 `archived`、`merge` 置 `superseded`。两处一旦漂移，清理就会按一份不存在的库存来算。
    """
    from dataclasses import replace as _replace

    projected = {int(it.item_id): it for it in items if it.item_id}
    for action in actions:
        item = projected.get(int(action.item_id))
        if item is None:
            continue
        if action.action in ("promote", "demote"):
            projected[item.item_id] = _replace(item, tier=str(action.to_tier),
                                               expires_ts=int(action.expires_ts))
        elif action.action == "archive":
            projected[item.item_id] = _replace(item, status="archived")
        elif action.action == "merge":
            projected[item.item_id] = _replace(item, status="superseded")
        # `keep` 与 actionability 轴 C 的 `keep_both` 都**不改任何字段**：前者是「我看过并决定
        # 不动」，后者是「我看过这一对并决定两条都留着」。两者在投影上等价于不存在——
        # `conflictsResolved` 因此不会把一次 `keep_both` 误记成一次「已解决」。
    return list(projected.values())


def render_listing(items: list[IntelItem], *, now: float, max_rows: int = 80) -> str:
    """整理轮的紧凑清单（按层分组、层内按 score 降序、总数封顶）。"""
    from .disclosure import relative_time     # 同包复用，避免第二份相对时间口径

    rows: list[str] = []
    remaining = max_rows
    for tier in ("long", "mid", "ultra"):
        group = sorted([it for it in items if it.tier == tier],
                       key=lambda it: gate.score_item(it, now=now), reverse=True)
        if not group:
            continue
        rows.append(f"【{tax.TIER_LABELS[tier]} {len(group)} 条】")
        for item in group[:max(0, remaining)]:
            flags = "".join(["【置顶】" if item.pinned else "",
                             "【用户编辑】" if item.user_edited else "",
                             "【已排期】" if item.is_scheduled else ""])
            rows.append(
                f"#{item.item_id} [{tax.CATEGORY_LABELS[item.category]}·"
                f"{tax.STANCE_LABELS[item.stance]}·{tax.IMPACT_LABELS[item.impact]}] "
                f"置信{item.confidence:.2f} · 事件{relative_time(item.event_ts, now)} · "
                f"{item.domain_count} 源{flags} · 「{item.headline}」")
            rows.append(f"     摘要：{item.summary[:80]}")
        remaining -= len(group)
        if remaining <= 0:
            break
    return "\n".join(rows) if rows else "（本标的当前没有任何在库情报。）"


def render_conflicts(rows: list[dict], items: list[IntelItem]) -> str:
    """整理轮 user prompt 的「未裁决的矛盾对」段（`conflict_enabled=False` ⇒ **空串**）。

    空串时 `build_curate_user_prompt` 的产物与本轮合入前**逐字节相同**（G-3 的第四件产物）。
    每对两行，带双方的 id / 标题 / 立场 / 置信度 / 判定 / 已悬置轮数；悬置最久的排在最前
    ——一个被反复跳过的分歧，正是最需要模型这一轮给个说法的那一个。
    """
    if not rows:
        return ""
    by_id = {int(it.item_id): it for it in items if it.item_id}
    lines: list[str] = ["【未裁决的矛盾对】（同类别、方向相反、主题高度重叠）"]
    for row in rows:
        pair_ids = (int(row["aId"]), int(row["bId"]))
        if not all(key in by_id for key in pair_ids):
            continue
        lines.append(f"· 重叠度 {row['overlap']:.2f} · 已悬置 {row['openRounds']} 轮：")
        for key in pair_ids:
            item = by_id[key]
            verdict = tax.VERDICT_LABELS.get(item.verdict, "未核查")
            lines.append(
                f"    #{item.item_id} [{tax.STANCE_LABELS[item.stance]}] "
                f"置信{item.confidence:.2f} · {verdict} · 「{item.headline}」")
    return "\n".join(lines) if len(lines) > 1 else ""
