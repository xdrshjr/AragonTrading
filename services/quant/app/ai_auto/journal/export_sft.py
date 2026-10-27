"""后训练样本（`sft-jsonl`）的装配（ai-trade-audit-journal §2.5.2 + §2.2.4bis）。

**本模块最重要的一条纪律**：宁可少导出一条样本，也绝不导出一条「我们以为模型看到了这个」的
假样本。九个 `skipped` 桶存在的全部理由就是把「为什么没导出」说出来——一个「导出了 37 条」
却不说「另外 2810 条为什么没导出」的导出器，会让人误以为系统只做了 37 次决策。

`messages` 是**按上下文链重建的整段多轮对话**，不是单轮三段（评审 RP0-1）。真实请求是
`messages = self._context + [user_msg]`（`decider._stream_decision` 的出站那一行），而
`self._context` 是逐轮累积的三元组、中途还会被左裁、被滚动摘要整体换成一段种子。按单轮重建
出来的样本，训练的是一段模型从未见过的输入——而 `prompt_sha256` 那道护栏**结构上看不见**这件
事（它只覆盖最后一条 user 文本），于是「shaMatch=true」与「样本是假的」可以同时成立。
"""
from __future__ import annotations

import hashlib
import json
import math
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass

from ...db import ai_journal_repositories as journal_repo
from ..prompts import DECISION_TOOL_NAME, build_decision_prompt
from . import linking
from .export_audit import ExportBudget, _ndjson

# 九个桶（评审 RP0-1 把 `contextIncomplete` 加成第 9 个）。它必须与别的桶分开计——
# 「模型说错了话」和「我们没留住模型看过的话」是两件性质完全不同的事，混进一个数字里
# 等于把后者藏起来。
SKIPPED_BUCKETS = ("gateVetoed", "degraded", "unsettled", "hold", "lowConfidence",
                   "promptDrift", "payloadTruncated", "minimalTier", "contextIncomplete")

CONTEXT_MODES = ("full", "lastTurnOnly")


@dataclass(frozen=True, slots=True)
class SftOptions:
    """八条筛选纪律（默认值即「能直接拿去训练」的口径）。"""

    exclude_gate_vetoed: bool = True
    exclude_degraded: bool = True
    only_settled: bool = True
    include_hold: bool = False
    min_link_confidence: str = "auto"
    context_mode: str = "full"


@dataclass(frozen=True, slots=True)
class SftContext:
    """一次导出的会话级上下文（总闸 + 解出的关联档位）。"""

    context_chain_enabled: bool = True
    resolved_min_link_confidence: str = "exact"
    resolved_reason: str = "explicit"


def _sha256(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def _tool_use_id(round_no: int) -> str:
    return f"toolu_journal_{int(round_no or 0)}"


def resolve_prompt(full: dict) -> tuple[str | None, str]:
    """一条决策行的提示词原文与来源。

    Returns:
        `(text, source)`——`source ∈ {"stored", "rendered", "drift", "absent"}`。
        `drift` 表示重建出的 sha 与存档不一致（该轮带过重试后缀 / 渲染代码已变更），
        **该样本一律排除**；`absent` 表示这一档根本没留上下文。
    """
    if full.get("promptText"):
        return full["promptText"], "stored"
    payload = full.get("payload")
    if payload is None:
        return None, "absent"
    text = build_decision_prompt(payload)
    stored = full.get("promptSha256") or ""
    if stored and _sha256(text) != stored:
        return None, "drift"
    return text, "rendered"


def _assistant_tool_use(round_no: int, tool_input: dict) -> dict:
    return {"role": "assistant",
            "content": [{"type": "tool_use", "id": _tool_use_id(round_no),
                         "name": DECISION_TOOL_NAME, "input": tool_input}]}


def _tool_result(round_no: int, content: str) -> dict:
    return {"role": "tool",
            "content": [{"type": "tool_result", "tool_use_id": _tool_use_id(round_no),
                         "content": content}]}


def build_context_messages(full: dict, predecessors: list[dict],
                           seed_text: str) -> tuple[list[dict] | None, int]:
    """按 §2.2.4bis ④ 重建「本轮之前」的那一段对话。

    Args:
        full: 本轮的决策行。
        predecessors: 同 `run_id`、`round_no ∈ [contextFirstRound, roundNo)` 的行（升序）。
        seed_text: 滚动摘要种子的原文；空串表示这一段上下文没有种子。

    Returns:
        `(messages, turns)`——`messages` 为 `None` 表示**重建不出来**（任一前驱缺席 / 是
        `minimal` 档 / `payload_truncated` / 提示词漂移），调用方据此计入 `contextIncomplete`；
        `turns` 是重建出的消息条数，供与 `contextRef.contextTurns` 逐数比对。

    ⚠️ 种子是**两条**消息而不是一条：`decider._install_summary_seed` 装的是
    `[user(种子), assistant("已了解此前执行情况…")]`。只重建一条会让 `contextTurns` 与实际
    重建条数恒差 1，于是**每一条**摘要重置之后的样本都会被判成 `contextIncomplete`。
    """
    messages: list[dict] = []
    if seed_text:
        messages.append({"role": "user", "content": seed_text})
        messages.append({"role": "assistant",
                         "content": "已了解此前执行情况，将据此继续决策。"})
    for row in predecessors:
        if row.get("payloadTruncated"):
            return None, 0
        text, source = resolve_prompt(row)
        if text is None or source in ("drift", "absent"):
            return None, 0
        turn = row.get("contextTurn") or {}
        if not turn.get("assistant"):
            return None, 0
        round_no = int(row.get("roundNo") or 0)
        messages.append({"role": "user", "content": text})
        messages.append(_assistant_tool_use(round_no, turn["assistant"]))
        messages.append(_tool_result(round_no, str(turn.get("toolResult") or "")))
    return messages, len(messages)


def build_sample(full: dict, link: dict, *, prompt_text: str, prompt_source: str,
                 profile: dict, context: list[dict] | None, opts: SftOptions,
                 ctx: SftContext) -> dict:
    """把一条已通过全部筛选的决策行装配成一条 Messages 形态的后训练样本。"""
    telemetry = full.get("telemetry") or {}
    trip = link.get("trip") or {}
    context_ref = full.get("contextRef") or {}
    truncated = context is None
    messages: list[dict] = [{"role": "system", "content": profile.get("systemPrompt") or ""}]
    if context:
        messages.extend(context)
    messages.append({"role": "user", "content": prompt_text})
    messages.append(_assistant_tool_use(int(full.get("roundNo") or 0), full.get("output") or {}))
    sample = {
        "kind": "sample",
        "id": f"{full.get('groupId')}:{full.get('instId')}:{full.get('roundNo')}",
        "messages": messages,
        "tools": _tools_of(profile),
        "meta": {
            "groupId": full.get("groupId"), "runId": full.get("runId"),
            "instId": full.get("instId"), "bar": full.get("bar"),
            "roundNo": full.get("roundNo"), "ts": full.get("ts"),
            "wallTs": full.get("wallTs"), "trigger": full.get("trigger"),
            "captureTier": full.get("captureTier"), "promptSource": prompt_source,
            "model": {"provider": telemetry.get("provider"), "name": telemetry.get("model")},
            "finalAction": full.get("finalAction"), "modelAction": full.get("action"),
            "contextMode": "lastTurnOnly" if truncated else opts.context_mode,
            "contextTurns": context_ref.get("contextTurns"),
            "contextFirstRound": context_ref.get("contextFirstRound"),
            "contextSummarySeed": bool(context_ref.get("summarySha256")),
            "resolvedMinLinkConfidence": ctx.resolved_min_link_confidence,
        },
        "label": _label(link, trip),
        "quality": {
            "degraded": bool(full.get("degradeReason")),
            "degradeReason": full.get("degradeReason"),
            "gateVetoed": full.get("finalAction") != full.get("action"),
            "reflectionVerdict": full.get("reflectionVerdict"),
            "payloadTruncated": bool(full.get("payloadTruncated")),
            # `true` 只出现在用户**显式**选了 `lastTurnOnly`、或总闸 CONTEXT_CHAIN_ENABLED
            # 关闭时。默认路径上它恒为 false——默认路径绝不产出一条「看起来像整段对话、
            # 其实只有最后一轮」的样本。
            "contextTruncated": truncated,
        },
    }
    return sample


def _tools_of(profile: dict) -> list:
    try:
        return json.loads(profile.get("toolsJson") or "[]")
    except (ValueError, TypeError):
        return []


def _label(link: dict, trip: dict) -> dict:
    """结果标签。**缺键即缺席，绝不补 0**（`trip_meta_wire` 的约束 6 原文即如此）。"""
    settled = bool(trip)
    label: dict = {"executed": link.get("fill") is not None,
                   "linkConfidence": link.get("linkConfidence"), "settled": settled}
    if not settled:
        return label
    pnl = trip.get("pnl")
    if pnl is not None:
        label["realizedPnlUsd"] = pnl
        label["outcome"] = "win" if float(pnl) > 0 else ("loss" if float(pnl) < 0 else "flat")
    for key in ("realizedBps", "rMultiple", "roundTripFeeUsd", "exitSource", "holdSeconds"):
        if trip.get(key) is not None:
            label[key] = trip[key]
    return label


def reject_bucket(full: dict, link: dict, opts: SftOptions, ctx: SftContext) -> str | None:
    """走完八条筛选纪律，返回被拒的桶名；通过 ⇒ `None`。**顺序即优先级**。

    **每一行至多进一个桶**——一行被两个理由排除时只记第一个，否则 `skipped` 的加总不再等于
    「总候选 − 导出条数」，而那条等式是这张表唯一的价值。

    纯函数：预检与流式导出**共用它**。两处各写一遍筛选，预检说的条数与真正导出的条数就会分叉，
    而那种分叉不报任何错。
    """
    if full.get("captureTier") == "minimal" or full.get("payload") is None:
        if not full.get("promptText"):
            return "minimalTier"
    if full.get("payloadTruncated"):
        return "payloadTruncated"
    gate_vetoed = full.get("finalAction") != full.get("action")
    if opts.exclude_gate_vetoed and gate_vetoed:
        return "gateVetoed"
    if opts.exclude_degraded and full.get("degradeReason") and not gate_vetoed:
        return "degraded"
    if not opts.include_hold and full.get("action") == "hold":
        return "hold"
    if not linking.meets_confidence(link.get("linkConfidence") or "none",
                                    ctx.resolved_min_link_confidence):
        return "lowConfidence"
    if opts.only_settled and not link.get("trip"):
        return "unsettled"
    return None


async def _load_predecessors(session, full: dict) -> list[dict]:
    """取上下文链的前驱行（走 `ix_ai_journal_dec_run_round`）。"""
    ref = full.get("contextRef") or {}
    first = int(ref.get("contextFirstRound") or 0)
    round_no = int(full.get("roundNo") or 0)
    if first <= 0 or first >= round_no:
        return []
    rows = await journal_repo.list_context_rows(session, run_id=int(full.get("runId") or 0),
                                                first_round=first, until_round=round_no)
    return [journal_repo.decision_full(row) for row in rows]


async def _resolve_context(session, full: dict, opts: SftOptions,
                           ctx: SftContext) -> tuple[list[dict] | None, bool]:
    """本轮样本的历史段。

    Returns:
        `(messages, ok)`——`ok=False` 表示重建不出来（计入 `contextIncomplete`）；
        `messages` 为 `None` 且 `ok=True` 表示显式的 `lastTurnOnly`（单轮样本）。
    """
    if opts.context_mode == "lastTurnOnly" or not ctx.context_chain_enabled:
        return None, True
    ref = full.get("contextRef")
    if not ref:
        # 总闸开着却没有断面 ⇒ 这一行是总闸打开**之前**落的。重建不出来就是重建不出来。
        return None, False
    predecessors = await _load_predecessors(session, full)
    expected_first = int(ref.get("contextFirstRound") or 0)
    round_no = int(full.get("roundNo") or 0)
    if expected_first > 0 and len(predecessors) != max(0, round_no - expected_first):
        return None, False        # 前驱被剪枝掉了：数得出来的不一致必须当作不一致
    seed_sha = ref.get("summarySha256") or ""
    seed_text = ""
    if seed_sha:
        profile = await journal_repo.get_prompt_profile(session, seed_sha)
        if profile is None or profile.kind != "summary":
            return None, False    # 种子档案行找不到 ⇒ 第一条消息重建不出来
        seed_text = profile.system_prompt
    messages, turns = build_context_messages(full, predecessors, seed_text)
    if messages is None:
        return None, False
    declared = ref.get("contextTurns")
    if declared is not None and int(declared) != turns:
        return None, False        # 与出站那一刻记下的条数对不上 ⇒ 一律排除
    return messages, True


async def iter_samples(filters: dict, budget: ExportBudget, opts: SftOptions,
                       ctx: SftContext):
    """逐页装配样本；yield `(sample | None, bucket | None, last_row_id)`。

    调用方（流式导出与预检）共用它——两处各写一遍筛选，预检说的条数与真正导出的条数就会分叉，
    而那种分叉**不报任何错**。
    """
    from ...db.session import get_sessionmaker
    sessionmaker = get_sessionmaker()
    cursor = filters.get("beforeId")
    while True:
        page_filters = dict(filters)
        if cursor:
            page_filters["beforeId"] = cursor
        async with sessionmaker() as session:
            rows = await journal_repo.list_decision_rows(session, page_filters,
                                                         limit=budget.page_size)
            if not rows:
                return
            fulls = [journal_repo.decision_full(row) for row in rows]
            trades = await journal_repo.list_run_trades(
                session, sorted({row.run_id for row in rows}))
            profiles = await journal_repo.load_profiles(
                session, [row.profile_sha256 for row in rows])
            links = linking.link_decisions_to_fills(fulls, trades)
            sifter_results = []
            for row, full, link in zip(rows, fulls, links):
                sifter_results.append(
                    await _assemble_one(session, full, link, profiles, opts, ctx, row.id))
        for item in sifter_results:
            yield item
        cursor = rows[-1].id
        if len(rows) < budget.page_size:
            return


async def _assemble_one(session, full: dict, link: dict, profiles: dict,
                        opts: SftOptions, ctx: SftContext, row_id: int):
    """单行的完整判定 + 装配（筛选顺序即 `_Sifter.check`）。"""
    bucket = reject_bucket(full, link, opts, ctx)
    if bucket:
        return None, bucket, row_id
    prompt_text, source = resolve_prompt(full)
    if source == "drift":
        return None, "promptDrift", row_id
    if prompt_text is None:
        return None, "minimalTier", row_id
    context, ok = await _resolve_context(session, full, opts, ctx)
    if not ok:
        return None, "contextIncomplete", row_id
    profile = profiles.get(full.get("profileSha256") or "") or {}
    sample = build_sample(full, link, prompt_text=prompt_text, prompt_source=source,
                          profile=profile, context=context, opts=opts, ctx=ctx)
    return sample, None, row_id


async def stream_sft(filters: dict, budget: ExportBudget, opts: SftOptions,
                     ctx: SftContext, meta: dict) -> AsyncIterator[str]:
    """`sft-jsonl`：manifest → sample* → eof（首尾各一行的完整性信封）。"""
    started = time.monotonic()
    skipped = dict.fromkeys(SKIPPED_BUCKETS, 0)
    yield _ndjson({"kind": "manifest", "schema": 1, "format": "sft-jsonl",
                   "generatedTs": int(time.time()),
                   "resolvedMinLinkConfidence": ctx.resolved_min_link_confidence,
                   "resolvedReason": ctx.resolved_reason,
                   "contextMode": opts.context_mode,
                   "contextChainEnabled": ctx.context_chain_enabled,
                   "redaction": "write-time key+value denylist", **meta})
    emitted = 0
    reason: str | None = None
    next_before_id = None
    async for sample, bucket, row_id in iter_samples(filters, budget, opts, ctx):
        next_before_id = row_id
        if bucket:
            skipped[bucket] += 1
            continue
        yield _ndjson(sample)
        emitted += 1
        reason = budget.exhausted(started, emitted)
        if reason:
            break
    yield _ndjson({"kind": "eof", "emitted": emitted, "complete": reason is None,
                   "truncatedReason": reason or "none",
                   "nextBeforeId": next_before_id if reason else None,
                   "skipped": skipped})


async def preview(filters: dict, budget: ExportBudget, opts: SftOptions,
                  ctx: SftContext) -> dict:
    """导出预检：精确条数 + 九个 `skipped` 桶 + 预估体积 / 耗时 / 片数。

    `estimatedSeconds` 由**这一次预检自己的**单行装配耗时外推——写死一个常数会在下一次装配
    变重时静默失准，而用户看到的片数正是照它算的。
    """
    started = time.monotonic()
    skipped = dict.fromkeys(SKIPPED_BUCKETS, 0)
    rows_out = 0
    approx_bytes = 0
    scanned = 0
    scanned_ids: list[int] = []
    async for sample, bucket, row_id in iter_samples(filters, budget, opts, ctx):
        scanned += 1
        scanned_ids.append(row_id)
        if bucket:
            skipped[bucket] += 1
            continue
        rows_out += 1
        approx_bytes += len(_ndjson(sample).encode("utf-8"))
        if rows_out >= budget.max_rows:
            break
    elapsed = max(1e-6, time.monotonic() - started)
    estimated = elapsed
    slices = 1
    if budget.time_budget_s > 0:
        slices = max(1, math.ceil(estimated / budget.time_budget_s))
    return {"rows": rows_out, "approxBytes": approx_bytes,
            "overLimit": rows_out >= budget.max_rows,
            "estimatedSeconds": round(estimated, 1), "slices": slices,
            # ★ 分片游标（RP0-2 的另一半）：`slices > 1` 时，第 k+1 片的 `beforeId`。
            # **没有它，分片就是假的**——浏览器导航式的下载读不回 `eof.nextBeforeId`，
            # 前端只能一片片从头拉，最后拿到 N 个一模一样的文件。
            "sliceCursors": slice_cursors(scanned_ids, slices),
            "timeBudgetS": budget.time_budget_s,
            "resolvedMinLinkConfidence": ctx.resolved_min_link_confidence,
            "resolvedReason": ctx.resolved_reason,
            "skipped": skipped}


def slice_cursors(scanned_ids: list[int], slices: int) -> list[int]:
    """把一次预检**实际走过的行 id 序列**（降序）切成 N 片，返回第 2..N 片的 `beforeId`。

    判据只有一条：第 k 片的边界就是第 k 段最后一行的 id。导出与预检用**同一条过滤 + 同一个
    排序**（`id DESC`），故这些游标是精确的——不是估的。

    Args:
        scanned_ids: 预检遍历到的行 id，按遍历顺序（即 `id DESC`）。
        slices: 片数（≤ 1 即不分片）。

    Returns:
        长度为 `slices - 1` 的游标列表；行数不够切时返回能切出来的那几个。
    """
    total = len(scanned_ids)
    if slices <= 1 or total == 0:
        return []
    per_slice = max(1, math.ceil(total / slices))
    cursors: list[int] = []
    for index in range(1, slices):
        boundary = index * per_slice - 1
        # `>= total - 1` 而不是 `>= total`：边界落在**最后一行**上时，它后面已经没有行了，
        # 那一片会是个空文件。少发一片，好过发一个内容为空、界面上却写着「第 3 / 3 片」的请求。
        if boundary >= total - 1:
            break
        cursors.append(int(scanned_ids[boundary]))
    return cursors


async def scope_has_client_order_id(filters: dict) -> bool:
    """该 scope 内是否**存在**任一 `cl_ord_id` 非空的决策行（`minLinkConfidence=auto` 的判据）。"""
    from sqlalchemy import select

    from ...db.models import AiTradeDecisionLog
    from ...db.session import get_sessionmaker
    stmt = select(AiTradeDecisionLog.id).where(AiTradeDecisionLog.cl_ord_id != "").limit(1)
    for key, column in (("groupId", AiTradeDecisionLog.group_id),
                        ("instId", AiTradeDecisionLog.inst_id),
                        ("bar", AiTradeDecisionLog.bar)):
        if filters.get(key):
            stmt = stmt.where(column == filters[key])
    async with get_sessionmaker()() as session:
        return (await session.execute(stmt)).scalars().first() is not None
