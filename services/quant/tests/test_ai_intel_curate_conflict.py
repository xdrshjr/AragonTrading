"""整理轮的战绩举证责任与矛盾裁决（G-21 / G-22 / G-27 / G-28② / G-30）。

**G-28② 必须从 `run_curate_round` 这条真实路径上跑**：直接调 `review_actions(context=…)` 的
用例会掩盖「整理轮根本拿不到 `Efficacy`」这个根因（评审 P0-1 的交付条件 C-1）。
"""
from __future__ import annotations

import hashlib
import json
import time

import pytest
from sqlalchemy import delete, select

from app.agent.llm.base import StreamEvent
from app.ai_auto.intel import conflict as conflict_mod
from app.ai_auto.intel import prompts as intel_prompts
from app.ai_auto.intel import service, service_curate
from app.ai_auto.intel import taxonomy as tax
from app.config import Settings
from app.db import repositories as repo
from app.db.models import AiIntelAlert, AiIntelCitation, AiIntelItem, AiIntelRound, AiIntelWatch
from app.db.session import get_sessionmaker, init_db

NOW = int(time.time()) + 3600
INST = "BTC-USDT-SWAP"
_TOPIC = "ETF 资金流向出现明显变化机构仓位随之调整"


class _CurateLLM:
    provider = "anthropic"

    def __init__(self, payload):
        self.payload = payload
        self.calls = 0
        self.last_system = ""
        self.last_user = ""

    async def stream(self, *, system, messages, tools):
        self.calls += 1
        self.last_system = system
        last = messages[-1] if messages else None
        # `messages` 的元素是 `LlmMessage` 对象而不是 dict：下标访问会抛，而抛出来的
        # 异常会被编排层收敛成一个降级码 —— 用例于是「绿着但什么都没测到」。
        self.last_user = str(getattr(last, "content", "") or "")
        yield StreamEvent(kind="tool_use_start", id="t",
                          name=intel_prompts.CURATE_TOOL_NAME)
        yield StreamEvent(kind="tool_use_args_delta", id="t",
                          partial_json=json.dumps(self.payload, ensure_ascii=False))
        yield StreamEvent(kind="message_done", stop_reason="tool_use",
                          usage={"input_tokens": 3, "output_tokens": 2})


async def _fresh() -> None:
    await init_db()
    async with get_sessionmaker()() as session:
        for table in (AiIntelAlert, AiIntelCitation, AiIntelItem, AiIntelRound, AiIntelWatch):
            await session.execute(delete(table))
        await session.commit()


def _deps(llm=None, **over) -> service.IntelDeps:
    return service.IntelDeps(settings=Settings(**over), sessionmaker=get_sessionmaker,
                             search=None, llm=llm, group_id="grp-1", bar="15m",
                             now=(lambda: float(NOW)))


async def _seed(rows: list[dict]) -> list[int]:
    async with get_sessionmaker()() as session:
        ids = await repo.upsert_intel_items(session, rows)
        await session.commit()
    return ids


def _row(headline: str, *, category="market", stance="bullish", tier="ultra",
         summary="", **over) -> dict:
    base = {"instId": INST, "tier": tier, "category": category, "headline": headline,
            "summary": summary or f"{headline}。这条摘要写得足够长以通过长度判据。",
            "stance": stance, "impact": "high", "confidence": 0.8,
            "eventTs": NOW - 7200, "capturedTs": NOW - 7200, "expiresTs": NOW + 86400,
            "status": "active", "pinned": False, "userEdited": False, "undated": False,
            "isScheduled": False, "origin": "agent", "originGroupId": "grp-1",
            "originRoundId": 0, "sources": [{"url": "https://a.com/x", "domain": "a.com"},
                                            {"url": "https://b.com/y", "domain": "b.com"}],
            "domainCount": 2, "dedupKey": f"k-{abs(hash(headline)) % 10**8}"}
    return {**base, **over}


# ---------- G-30 / G-27：轴 C 总闸关掉时整理链路不多一个字节 ----------
def _schema_hash(limits) -> str:
    return hashlib.sha256(json.dumps(intel_prompts.build_curate_tool_schema(limits),
                                     sort_keys=True,
                                     ensure_ascii=False).encode("utf-8")).hexdigest()


def test_the_tool_schema_is_bit_identical_when_conflicts_are_off():
    off = tax.IntelLimits(conflict_enabled=False)
    schema = intel_prompts.build_curate_tool_schema(off)
    action = schema["properties"]["actions"]["items"]["properties"]["action"]
    assert action["enum"] == ["promote", "demote", "archive", "merge", "keep"]
    assert "pairId" not in schema["properties"]["actions"]["items"]["properties"]
    # 单一真源的顺序即字节：`CURATE_ACTIONS` 的前五项必须逐字等于 `CURATE_ACTIONS_BASE`。
    assert tax.CURATE_ACTIONS[:5] == tax.CURATE_ACTIONS_BASE
    assert _schema_hash(off) != _schema_hash(tax.IntelLimits(conflict_enabled=True))


def test_all_three_products_appear_and_disappear_together():
    """schema 的 `keep_both` / `pairId`、system prompt 的裁决纪律、user prompt 的矛盾段
    **三者同条件缺席**（评审 P0-4）。"""
    off = tax.IntelLimits(conflict_enabled=False)
    on = tax.IntelLimits(conflict_enabled=True)
    assert "keep_both" not in intel_prompts.build_curate_system_prompt(INST, off)
    assert "keep_both" in intel_prompts.build_curate_system_prompt(INST, on)
    assert intel_prompts.build_curate_user_prompt("清单") == intel_prompts.build_curate_user_prompt(
        "清单", conflicts="")
    assert "R1–R6" in intel_prompts.build_curate_user_prompt("清单")
    assert "R1–R7" in intel_prompts.build_curate_user_prompt("清单", conflicts="【矛盾】")


def test_the_system_prompt_never_names_an_action_that_does_not_exist():
    """`supersede` 不是一个动作，只是 `merge` 的落库效果。写进 prompt，模型会照着发然后被
    `bad_action` 驳回，而界面上没有任何线索指向那句 prompt（评审建议 3）。"""
    text = intel_prompts.build_curate_system_prompt(INST, tax.IntelLimits(conflict_enabled=True))
    assert "supersede" not in text
    for action in tax.CURATE_ACTIONS:
        assert action in text or action in ("promote", "demote", "archive", "merge", "keep")


# ---------- G-21：`keep_both` 的三条服务端判据 ----------
@pytest.mark.asyncio
async def test_keep_both_requires_a_real_pair_and_a_thick_reason():
    await _fresh()
    ids = await _seed([_row(f"{_TOPIC}，连续第 5 日净流入", stance="bullish"),
                       _row(f"{_TOPIC}，单日净流出创年内新高", stance="bearish"),
                       _row("一件完全无关的事情发生了", stance="bullish")])
    long_reason = "两条讲的是不同时间尺度：一条是周度累计，一条是单日读数，可以同时成立。"
    llm = _CurateLLM({"actions": [
        {"id": ids[0], "action": "keep_both", "pairId": ids[1], "reason": long_reason},
        {"id": ids[0], "action": "keep_both", "pairId": ids[1], "reason": "都留着"},
        {"id": ids[0], "action": "keep_both", "pairId": ids[2], "reason": long_reason},
    ]})
    outcome = await service_curate.run_curate_round(_deps(llm), INST)
    assert outcome.actions["rejected_keep_both_thin_note"] == 1
    assert outcome.actions["rejected_keep_both_bad_pair"] == 1
    assert outcome.actions["applied_keep_both"] == 1
    # **tier 与 status 一个字节不变**：它是一次有记录的不作为。
    async with get_sessionmaker()() as session:
        row = await session.get(AiIntelItem, ids[0])
        assert (row.tier, row.status) == ("ultra", "active")
        assert row.curation_note.startswith("两条讲的是不同时间尺度")
        assert row.last_curated_ts == outcome_started(outcome)


def outcome_started(outcome) -> int:
    """轮存根里那一刻的墙钟（`run_curate_round` 用的是 `deps.now()`）。"""
    return NOW


# ---------- G-22：`conflictsResolved` 由服务端重算 ----------
@pytest.mark.asyncio
async def test_conflicts_resolved_is_recomputed_not_taken_from_the_model():
    await _fresh()
    ids = await _seed([_row(f"{_TOPIC}，连续第 5 日净流入", stance="bullish"),
                       _row(f"{_TOPIC}，单日净流出创年内新高", stance="bearish")])
    # 模型只归档了一条（矛盾因此真的解除），但它在 note 里自报「已解决 3 对」——不采信。
    llm = _CurateLLM({"actions": [{"id": ids[1], "action": "archive",
                                   "reason": "该口径已被更权威的数据源覆盖"}],
                      "note": "已解决 3 对矛盾"})
    outcome = await service_curate.run_curate_round(_deps(llm), INST)
    assert outcome.actions["conflictsOpen"] == 1
    assert outcome.actions["conflictsResolved"] == 1


@pytest.mark.asyncio
async def test_an_untouched_conflict_stays_open_in_the_round_stub():
    await _fresh()
    await _seed([_row(f"{_TOPIC}，连续第 5 日净流入", stance="bullish"),
                 _row(f"{_TOPIC}，单日净流出创年内新高", stance="bearish")])
    outcome = await service_curate.run_curate_round(
        _CurateLLM({"actions": []}) and _deps(_CurateLLM({"actions": []})), INST)
    assert outcome.actions["conflictsOpen"] == 1
    assert outcome.actions["conflictsResolved"] == 0


@pytest.mark.asyncio
async def test_the_conflict_segment_reaches_the_user_prompt():
    await _fresh()
    ids = await _seed([_row(f"{_TOPIC}，连续第 5 日净流入", stance="bullish"),
                       _row(f"{_TOPIC}，单日净流出创年内新高", stance="bearish")])
    llm = _CurateLLM({"actions": []})
    await service_curate.run_curate_round(_deps(llm), INST)
    assert "未裁决的矛盾对" in llm.last_user
    assert f"#{ids[0]}" in llm.last_user and f"#{ids[1]}" in llm.last_user
    assert "R1–R7" in llm.last_user


@pytest.mark.asyncio
async def test_switching_the_conflict_gate_off_removes_the_segment_entirely():
    await _fresh()
    await _seed([_row(f"{_TOPIC}，连续第 5 日净流入", stance="bullish"),
                 _row(f"{_TOPIC}，单日净流出创年内新高", stance="bearish")])
    llm = _CurateLLM({"actions": []})
    outcome = await service_curate.run_curate_round(
        _deps(llm, ai_trade_intel_conflict_enabled=False), INST)
    assert "未裁决的矛盾对" not in llm.last_user
    assert "R1–R6" in llm.last_user
    assert "conflictsOpen" not in outcome.actions


# ---------- G-28②③：`promote_weak_record` 必须能在真实整理轮上被触发 ----------
async def _seed_negative_record(category: str, item_ids: list[int]) -> None:
    """给一个类别造出「历史战绩为负」：多条已证伪的判定 + 几笔亏损的入场引用。"""
    async with get_sessionmaker()() as session:
        for item_id in item_ids:
            row = await session.get(AiIntelItem, item_id)
            row.verdict = "refuted"
            row.verdict_ts = NOW - 86400
        await session.commit()
        await repo.append_intel_citations(session, [
            {"itemId": item_ids[0], "instId": INST, "runId": 1, "roundNo": i,
             "citedTs": NOW - 7200, "refToken": "I01", "role": "entry",
             "proposedAction": "open_long", "finalAction": "open_long", "traded": True,
             "positionSeq": i} for i in range(1, 4)])
        rows = (await session.execute(select(AiIntelCitation))).scalars().all()
        for row in rows:
            row.settled = True
            row.outcome = "loss"
            row.net_pnl_usd = -30.0
        await session.commit()


@pytest.mark.asyncio
async def test_promote_to_long_on_a_weak_category_needs_a_thicker_reason():
    """★ 评审 P0-1 的交付条件：**这条用例必须从 `run_curate_round` 这条真实路径上跑**。

    直接调 `review_actions(context=…)` 会掩盖「整理轮根本拿不到 `Efficacy`」这个根因——那正是
    v1 会在默认路径上静默空转的地方。
    """
    await _fresh()
    old = NOW - 10 * 86400
    ids = await _seed([_row(f"宏观议题 {i} 的政策路径出现变化", category="macro",
                            tier="mid", eventTs=old, capturedTs=old,
                            summary=f"宏观议题 {i} 的政策路径出现变化，影响中期资金成本。")
                       for i in range(6)])
    await _seed_negative_record("macro", ids)
    deps = _deps(_CurateLLM({"actions": [
        {"id": ids[0], "action": "promote", "toTier": "long", "reason": "很重要"}]}),
        ai_trade_intel_efficacy_k_min=4)
    outcome = await service_curate.run_curate_round(deps, INST)
    assert outcome.actions["rejected_promote_weak_record"] == 1
    assert "applied_promote" not in outcome.actions

    # ③ 同一个动作把理由写足 ⇒ 通过（它是举证责任，不是免死金牌）。
    thick = "该类别过去 6 条被证伪且三次入场累计亏损，但本条有两个一级来源与链上数据互证。"
    deps = _deps(_CurateLLM({"actions": [
        {"id": ids[0], "action": "promote", "toTier": "long", "reason": thick}]}),
        ai_trade_intel_efficacy_k_min=4)
    outcome = await service_curate.run_curate_round(deps, INST)
    assert "rejected_promote_weak_record" not in outcome.actions
    assert outcome.actions["applied_promote"] == 1


@pytest.mark.asyncio
async def test_the_curate_round_and_the_snapshot_solve_the_same_efficacy():
    """G-28①（端到端一侧）：两处求解**逐字段相等**——这是「不构成两个真源」的全部依据。"""
    await _fresh()
    ids = await _seed([_row(f"宏观议题 {i} 的政策路径出现变化", category="macro")
                       for i in range(6)])
    await _seed_negative_record("macro", ids)
    deps = _deps(None, ai_trade_intel_efficacy_k_min=4)
    async with get_sessionmaker()() as session:
        scope = await repo.load_intel_scope(session, INST)
        from_curate = await service.solve_efficacy(session, deps, INST, scope)
        from_rest = await service.read_efficacy(session, INST, limits=deps.limits,
                                                now=deps.now())
    snapshot_eff = None
    async with get_sessionmaker()() as session:
        snapshot_eff = await service.solve_efficacy(session, deps, INST,
                                                    await repo.load_intel_scope(session, INST))
    for other in (from_rest, snapshot_eff):
        assert other.overall == from_curate.overall
        assert other.by_category == from_curate.by_category
        assert other.weights == from_curate.weights


def test_conflict_pairs_are_normalised_and_indexable():
    pair = conflict_mod.ConflictPair(9, 4, 0.6, 24.0, "market")
    assert conflict_mod.pair_index([pair]) == {9: 4, 4: 9}
