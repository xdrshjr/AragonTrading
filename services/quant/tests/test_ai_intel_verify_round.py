"""核查轮的端到端编排（closed-loop §7.1 · test_ai_intel_verify_round）。

真 SQLite（conftest 的临时库）+ 假检索 + 假 LLM，一个真实的出网请求都不发。

本文件的三条**跨轮**用例是它存在的理由（同轮断言看不见这三种失效）：
- 首击只停注入、不动层级；隔 `verify_recheck_h` 的第二击才降层且重算寿命（G-6）。
- 核查轮跑完之后 `last_curate_ts` 与跑之前逐字节相等（G-16 / P0-3）。
- `verify_every_n_curates=2` 时第一次整理不跑核查、第二次才跑（G-16 的另一半）。
"""
from __future__ import annotations

import json
import time

import pytest
from sqlalchemy import delete

from app.agent.llm.base import StreamEvent
from app.ai_auto.intel import prompts as intel_prompts
from app.ai_auto.intel import service, service_verify
from app.ai_auto.intel.disclosure import build_digest
from app.ai_auto.intel.search import SearchDoc, SearchRunner
from app.config import Settings
from app.db import repositories as repo
from app.db.models import AiIntelCitation, AiIntelItem, AiIntelRound, AiIntelWatch
from app.db.session import get_sessionmaker, init_db

NOW = int(time.time()) + 3600
INST = "BTC-USDT-SWAP"


class FakeProvider:
    slug = "fake"

    def __init__(self, docs=None, *, error=None):
        self._docs = docs
        self._error = error
        self.calls = 0

    async def search(self, query, *, limit, freshness_days):
        self.calls += 1
        if self._error is not None:
            raise self._error
        if self._docs is not None:
            return list(self._docs)
        return [SearchDoc(title="Mainnet upgrade postponed indefinitely",
                          url="https://coindesk.com/v1",
                          snippet="The foundation said the upgrade will not ship in September.",
                          published_ts=NOW - 600, score=0.9)]


class FakeLLM:
    provider = "anthropic"

    def __init__(self, payload, *, tool_name=intel_prompts.VERIFY_TOOL_NAME, capture=None):
        self.payload = payload
        self.tool_name = tool_name
        self.capture = capture if capture is not None else []
        self.calls = 0

    async def stream(self, *, system, messages, tools):
        self.calls += 1
        self.capture.append({"system": system, "user": messages[0].content[0].text,
                             "tools": tools})
        yield StreamEvent(kind="tool_use_start", id="t", name=self.tool_name)
        yield StreamEvent(kind="tool_use_args_delta", id="t",
                          partial_json=json.dumps(self.payload, ensure_ascii=False))
        yield StreamEvent(kind="message_done", stop_reason="tool_use",
                          usage={"input_tokens": 9, "output_tokens": 3})


def _verdicts(verdict: str, *, refs=("D1",), reason="语料明确写到升级已无限期推迟"):
    return {"verdicts": [{"itemRef": "C1", "verdict": verdict, "reason": reason,
                          "refs": list(refs)}]}


async def _fresh() -> None:
    await init_db()
    async with get_sessionmaker()() as session:
        for table in (AiIntelCitation, AiIntelItem, AiIntelRound, AiIntelWatch):
            await session.execute(delete(table))
        await session.commit()


def _deps(*, provider=None, llm=None, now=NOW, **over) -> service.IntelDeps:
    settings = Settings(**over) if over else Settings()
    return service.IntelDeps(
        settings=settings, sessionmaker=get_sessionmaker,
        search=SearchRunner(provider, timeout_s=5.0) if provider is not None else None,
        llm=llm, group_id="grp-1", bar="15m", now=(lambda: now))


async def _seed_item(**over) -> int:
    payload = {"instId": INST, "tier": "long", "category": "project",
               "headline": "主网 v2 升级已定档 9 月 3 日", "summary": "基金会公告升级窗口。",
               "stance": "bullish", "impact": "high", "confidence": 0.6,
               "eventTs": NOW - 5 * 86400, "capturedTs": NOW - 5 * 86400,
               "expiresTs": NOW + 400 * 86400, "status": "active", "origin": "agent",
               "sources": [{"url": "https://a.com/x", "domain": "a.com", "publishedTs": 0}],
               "domainCount": 1, "dedupKey": "k1"}
    payload.update(over)
    async with get_sessionmaker()() as session:
        ids = await repo.upsert_intel_items(session, [payload])
        await repo.ensure_intel_watch(session, INST)
    return ids[0]


async def _item(item_id: int) -> dict:
    async with get_sessionmaker()() as session:
        return await repo.get_intel_item(session, item_id)


async def _rounds() -> list[dict]:
    async with get_sessionmaker()() as session:
        return await repo.list_intel_rounds(session, INST)


async def _watch() -> dict:
    async with get_sessionmaker()() as session:
        return await repo.load_intel_watch(session, INST) or {}


# ---------------- 两击制（G-6 / P1-12） ----------------
@pytest.mark.asyncio
async def test_first_refuted_stops_injection_without_touching_tier():
    """G-6 前半：首击写 verdict + 罚置信度，**tier / status / expires_ts 一个字节都不动**。"""
    await _fresh()
    item_id = await _seed_item()
    before = await _item(item_id)

    outcome = await service_verify.run_verify_round(
        _deps(provider=FakeProvider(), llm=FakeLLM(_verdicts("refuted"))), INST)

    after = await _item(item_id)
    assert outcome.status == "ok"
    assert after["verdict"] == "refuted" and after["verifyCount"] == 1
    assert after["confidence"] == pytest.approx(before["confidence"] * 0.5, abs=1e-4)
    assert (after["tier"], after["status"], after["expiresTs"]) == (
        before["tier"], before["status"], before["expiresTs"])


@pytest.mark.asyncio
async def test_first_refuted_is_dropped_from_the_next_digest():
    """G-6 的**跨轮**断言：注入集合在下一次定稿时才变——同轮断言看不见这件事。"""
    await _fresh()
    item_id = await _seed_item()
    async with get_sessionmaker()() as session:      # 让 `lastCollectTs` 非空（否则恒不注入）
        await repo.bump_intel_watch(session, INST, kind="collect", status="ok",
                                    next_due_ts=NOW + 60)
    deps = _deps(provider=FakeProvider(), llm=FakeLLM(_verdicts("refuted")))
    snapshot = await service.build_snapshot(deps, INST)
    assert len(snapshot["digest"]["lines"]) == 1

    await service_verify.run_verify_round(deps, INST)

    async with get_sessionmaker()() as session:
        scope = await repo.load_intel_scope(session, INST)
    items = [service.item_from_wire(w) for w in scope]
    wire = build_digest(items, limits=deps.limits, now=NOW, last_collect_ts=NOW - 60, cite=True)
    assert wire["lines"] == []
    assert (await _item(item_id))["verdict"] == "refuted"


@pytest.mark.asyncio
async def test_second_refuted_demotes_and_recomputes_expires():
    """G-6 后半（**跨轮**）：两击之间推过 `verify_recheck_h`，第二击才降层并重算寿命。"""
    await _fresh()
    item_id = await _seed_item()
    await service_verify.run_verify_round(
        _deps(provider=FakeProvider(), llm=FakeLLM(_verdicts("refuted"))), INST)
    first = await _item(item_id)

    later = NOW + 3 * 86400          # 推过 verify_recheck_h（默认 48h）
    await service_verify.run_verify_round(
        _deps(provider=FakeProvider(), llm=FakeLLM(_verdicts("refuted")), now=later), INST)

    second = await _item(item_id)
    assert first["tier"] == "long" and second["tier"] == "mid"
    assert second["verifyCount"] == 2
    assert second["expiresTs"] < first["expiresTs"], "寿命必须按新 tier 在同一条 UPDATE 里重算"
    assert second["status"] == "active", "任何情形都不因 refuted 归档"


@pytest.mark.asyncio
async def test_refuted_never_archives_even_at_ultra():
    """P1-12：已在最低层也只是不再降层，**绝不归档**。"""
    await _fresh()
    item_id = await _seed_item(tier="ultra", expiresTs=NOW + 72 * 3600)
    for offset in (0, 3 * 86400):
        await service_verify.run_verify_round(
            _deps(provider=FakeProvider(), llm=FakeLLM(_verdicts("refuted")),
                  now=NOW + offset), INST)

    after = await _item(item_id)
    assert after["tier"] == "ultra" and after["status"] == "active"


@pytest.mark.asyncio
async def test_protected_refuted_item_keeps_tier_but_stops_injecting():
    """G-8：置顶条目照样进候选、照样被判、照样停注入，但层级与置信度一个字节不动。"""
    await _fresh()
    item_id = await _seed_item(pinned=True, expiresTs=0)
    before = await _item(item_id)
    await service_verify.run_verify_round(
        _deps(provider=FakeProvider(), llm=FakeLLM(_verdicts("refuted"))), INST)

    after = await _item(item_id)
    assert after["verdict"] == "refuted"
    assert (after["tier"], after["confidence"]) == (before["tier"], before["confidence"])


@pytest.mark.asyncio
async def test_refuted_without_refs_is_downgraded_and_counted():
    """G-7：空口断言一律降为待定，**不执行任何降级后果**，且直方图里看得见。"""
    await _fresh()
    item_id = await _seed_item()
    await service_verify.run_verify_round(
        _deps(provider=FakeProvider(), llm=FakeLLM(_verdicts("refuted", refs=()))), INST)

    after = await _item(item_id)
    assert after["verdict"] == "unresolved" and after["tier"] == "long"
    assert (await _rounds())[0]["actions"].get("verdict_no_ref") == 1


# ---------------- 降级路径 ----------------
@pytest.mark.asyncio
async def test_empty_corpus_marks_unresolved_not_failed():
    """「查不到」是一个真实的核查结论：全部候选记 `unresolved` 并 `verify_count+1`。"""
    await _fresh()
    item_id = await _seed_item()
    llm = FakeLLM(_verdicts("confirmed"))
    outcome = await service_verify.run_verify_round(
        _deps(provider=FakeProvider(docs=[]), llm=llm), INST)

    assert outcome.degrade_code == "verify_no_corpus"
    assert llm.calls == 0, "零文档时调模型只会烧钱并诱发幻觉"
    after = await _item(item_id)
    assert after["verdict"] == "unresolved" and after["verifyCount"] == 1


@pytest.mark.asyncio
async def test_no_candidates_writes_no_round_row():
    """一张全是空轮的审计表等于没有审计表。"""
    await _fresh()
    await _seed_item(origin="user")          # 用户手写条目不进自动候选池
    outcome = await service_verify.run_verify_round(
        _deps(provider=FakeProvider(), llm=FakeLLM(_verdicts("confirmed"))), INST)

    assert outcome.actions == {"verify_skipped": 1}
    assert await _rounds() == []


@pytest.mark.asyncio
async def test_verify_round_self_checks_daily_cap():
    """P1-5：不自判的话核查轮会吃掉采集的额度、自己却不受额度约束。"""
    await _fresh()
    await _seed_item()
    async with get_sessionmaker()() as session:
        await repo.bump_intel_watch(session, INST, kind="collect", status="ok",
                                    next_due_ts=NOW + 60)
    llm = FakeLLM(_verdicts("confirmed"))
    outcome = await service_verify.run_verify_round(
        _deps(provider=FakeProvider(), llm=llm, ai_trade_intel_daily_round_cap=1), INST)

    assert outcome.degrade_code == "budget_exhausted"
    assert llm.calls == 0
    assert (await _rounds())[0]["kind"] == "verify"


@pytest.mark.asyncio
async def test_no_provider_degrades_without_calling_the_model():
    await _fresh()
    await _seed_item()
    outcome = await service_verify.run_verify_round(_deps(provider=None, llm=None), INST)

    assert outcome.degrade_code == "verify_no_provider"


@pytest.mark.asyncio
async def test_verify_llm_falls_back_to_the_collect_client():
    """P1-3：`verify_llm is None` ⇒ 回落 `deps.llm`，**不报错**。"""
    await _fresh()
    item_id = await _seed_item()
    llm = FakeLLM(_verdicts("confirmed"))
    deps = _deps(provider=FakeProvider(), llm=llm)
    assert deps.verify_llm is None
    await service_verify.run_verify_round(deps, INST)

    assert llm.calls == 1
    assert (await _item(item_id))["verdict"] == "confirmed"


# ---------------- 节律（G-16 / P0-3） ----------------
@pytest.mark.asyncio
async def test_verify_round_does_not_touch_last_curate_ts():
    """G-16 / P0-3：核查轮跑完之后「上次整理」与跑之前**逐字节相等**。"""
    await _fresh()
    await _seed_item()
    async with get_sessionmaker()() as session:
        await repo.bump_intel_watch(session, INST, kind="curate", status="ok",
                                    next_due_ts=NOW + 60, curate_reset=True)
    before = await _watch()
    assert before["lastCurateTs"] > 0
    assert before["curatesSinceVerify"] == 1, "curate 分支是 curates_since_verify 的唯一自增点"

    await service_verify.run_verify_round(
        _deps(provider=FakeProvider(), llm=FakeLLM(_verdicts("confirmed"))), INST)

    after = await _watch()
    assert after["lastCurateTs"] == before["lastCurateTs"]
    assert after["lastVerifyTs"] > 0
    assert after["curatesSinceVerify"] == 0, "verify_reset 必须清零，否则下一轮整理必再核查"


def test_verify_cadence_steady_state_period_is_exactly_n_curates():
    """「每 N 次整理跑一次核查」必须**逐周期**验，不能只问计数器的一个点。

    `verify_reset` 清零之后，**同一个 tick 的整理轮立刻把它加回 1**，所以稳态下这个计数器永远
    不会被观察到 0。判据里若带 `+1`（整理轮那条判据的形状，时序却相反），稳态周期会塌成
    `N-1`：把 `verify_every_n_curates` 调到 2 的用户会得到「从第二次整理起每次都核查」，检索与
    LLM 账单翻倍，而界面上没有任何异样——P0-3 的镜像。
    """
    for every in (1, 2, 3, 4):
        settings = Settings(ai_trade_intel_verify_every_n_curates=every)
        counter, fired = 0, []
        for cycle in range(1, 21):      # 20 个「采集到期 ⇒ 整理轮」周期，逐个走 `_tick` 的时序
            if service.verify_due(settings, {"curatesSinceVerify": counter}):
                fired.append(cycle)
                counter = 0             # run_verify ⇒ bump_intel_watch(verify_reset=True)
            counter += 1                # run_curate ⇒ curates_since_verify 的唯一自增点
        gaps = {later - prior for prior, later in zip(fired, fired[1:])}
        assert gaps == {every}, f"every={every} 的稳态周期应为 {every}，实为 {gaps}"


def test_verify_switch_off_beats_any_counter():
    """轴 C 单键关掉 ⇒ 计数器再大也不跑（三把单键逐层收窄的最内层）。"""
    off = Settings(ai_trade_intel_verify_enabled=False)
    assert service.verify_due(off, {"curatesSinceVerify": 9}) is False


# ---------------- prompt 的两条硬约束（P1-11 / §3.8） ----------------
@pytest.mark.asyncio
async def test_verify_prompt_omits_stance_and_confidence():
    """P1-11：把「这条是利多」摆在核查员眼前，再要求他别看方向，是用输入反驳自己的指令。"""
    await _fresh()
    await _seed_item()
    capture: list[dict] = []
    await service_verify.run_verify_round(
        _deps(provider=FakeProvider(), llm=FakeLLM(_verdicts("confirmed"), capture=capture)),
        INST)

    user = capture[0]["user"]
    assert "C1" in user and "主网 v2 升级已定档 9 月 3 日" in user
    for leaked in ("偏多", "偏空", "置信", "长期", "中期", "bullish"):
        assert leaked not in user, f"候选渲染泄漏了 {leaked}"


@pytest.mark.asyncio
async def test_verify_prompt_carries_no_position_facts():
    """§3.8：一个知道「我们现在是多头」的核查员，判 refuted 的门槛会不一样。"""
    await _fresh()
    await _seed_item()
    capture: list[dict] = []
    await service_verify.run_verify_round(
        _deps(provider=FakeProvider(), llm=FakeLLM(_verdicts("confirmed"), capture=capture)),
        INST)

    # **只扫 user 消息**：system prompt 里那句「你不知道任何持仓、盈亏或方向」是硬约束本身，
    # 把它一起扫会让这条用例在「约束写得越清楚越红」的方向上失效。
    for leaked in ("持仓", "浮盈", "预算", "杠杆", "开仓", "平仓", "多头", "空头"):
        assert leaked not in capture[0]["user"], f"核查证据段泄漏了 {leaked}"
    assert "不知道**任何持仓、盈亏或方向" in capture[0]["system"]
    assert [t.name for t in capture[0]["tools"]] == [intel_prompts.VERIFY_TOOL_NAME], \
        "核查 Agent 的 tools 恒为单元素，结构上无法交易"


# ---------------- evidence-grounding §3.3.3：证伪的举证责任 ----------------
@pytest.mark.asyncio
async def test_strict_refuted_without_a_verified_quote_is_downgraded():
    """`refuted` 是本层**唯一**会立刻停止注入的判定，它的举证责任应当高于 `confirmed`。

    `soft` 下**不降级**（只记数），与「soft 只加不减」一致；`strict` 下降为 `unresolved`
    并计 `refute_without_quote`。
    """
    await _fresh()
    item_id = await _seed_item(dedupKey="k-strict")
    deps = _deps(provider=FakeProvider(), llm=FakeLLM(_verdicts("refuted")),
                 ai_trade_intel_quote_mode="strict")
    await service_verify.run_verify_round(deps, INST, only_item_id=item_id)
    row = await _item(item_id)
    assert row["verdict"] == "unresolved", "指不出原文的证伪不该能停掉一条情报的注入"
    rounds = await _rounds()
    assert rounds[0]["actions"].get("refute_without_quote") == 1


@pytest.mark.asyncio
async def test_soft_refuted_without_a_quote_still_stands():
    await _fresh()
    item_id = await _seed_item(dedupKey="k-soft")
    deps = _deps(provider=FakeProvider(), llm=FakeLLM(_verdicts("refuted")))
    await service_verify.run_verify_round(deps, INST, only_item_id=item_id)
    assert (await _item(item_id))["verdict"] == "refuted"


@pytest.mark.asyncio
async def test_verify_round_carries_the_four_new_counters_even_without_a_reader():
    """核查轮的 round 存根同样带四个计数：不带 ⇒ 命中率的分母系统性偏小。"""
    await _fresh()
    item_id = await _seed_item(dedupKey="k-counters")
    deps = _deps(provider=FakeProvider(), llm=FakeLLM(_verdicts("unresolved")))
    await service_verify.run_verify_round(deps, INST, only_item_id=item_id)
    row = (await _rounds())[0]
    for key in ("readerAttempt", "readerOk", "quotesProposed", "quotesVerified"):
        assert key in row, f"轮次 wire 少了 {key}"
    assert row["readerAttempt"] == 0, "未装配 reader ⇒ 一次抓取都不该发生"
