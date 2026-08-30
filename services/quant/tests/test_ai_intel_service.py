"""采集轮与整理轮的端到端编排（§7.1 · test_ai_intel_service）。

真 SQLite（conftest 的临时库）+ 假检索 + 假 LLM，一个真实的出网请求都不发。

放行条件 1 在这里兑现：**P0-2 与 P0-3 必须有跨边界用例，不是同轮断言。**
`test_promoted_item_survives_ultra_ttl` 把时钟推过 `event_ts + 72h` 再断言存活；
`test_archived_duplicate_revives_instead_of_crashing` 先让条目真的被 TTL 归档、再喂同一条新闻。
这两条自毁路径的共同特征是**在同一轮里看不见**。
"""
from __future__ import annotations

import json
import time

import pytest
from sqlalchemy import delete

from app.agent.llm.base import StreamEvent
from app.ai_auto.intel import prompts as intel_prompts
from app.ai_auto.intel import service
from app.ai_auto.intel.search import SearchDoc, SearchRunner
from app.config import Settings
from app.db import repositories as repo
from app.db.models import (
    AiIntelAlert,
    AiIntelCitation,
    AiIntelItem,
    AiIntelRound,
    AiIntelWatch,
)
from app.db.session import get_sessionmaker, init_db

# 假时钟比真实时钟**快一小时**：`ensure_intel_watch` 用真实 `time.time()` 写 `next_due_ts`，
# 与假时钟持平会让每一条用例都判「未到期」而直接早退（返回 None），且症状是空指针而不是断言失败。
NOW = int(time.time()) + 3600
INST = "BTC-USDT-SWAP"


# ---------------- 替身 ----------------
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
        return [SearchDoc(title="US spot ETF outflow reaches 5 weeks",
                          url="https://coindesk.com/a", snippet="Five straight weeks of net "
                          "redemptions from US spot ETFs.", published_ts=NOW - 3600, score=0.9),
                SearchDoc(title="Analysts weigh in on ETF flows",
                          url="https://theblock.co/b", snippet="Redemptions concentrated in two "
                          "large funds over 5 weeks.", published_ts=NOW - 7200, score=0.8)]


class FakeLLM:
    """按 payload 产出一次工具调用的假客户端；`on_stream` 可在流内做断言。"""

    provider = "anthropic"

    def __init__(self, payload, *, tool_name=intel_prompts.COLLECT_TOOL_NAME, on_stream=None):
        self.payload = payload
        self.tool_name = tool_name
        self.on_stream = on_stream
        self.calls = 0

    async def stream(self, *, system, messages, tools):
        self.calls += 1
        if self.on_stream is not None:
            self.on_stream()
        yield StreamEvent(kind="tool_use_start", id="t", name=self.tool_name)
        yield StreamEvent(kind="tool_use_args_delta", id="t",
                          partial_json=json.dumps(self.payload, ensure_ascii=False))
        yield StreamEvent(kind="message_done", stop_reason="tool_use",
                          usage={"input_tokens": 11, "output_tokens": 4})


_COLLECT_PAYLOAD = {"items": [{
    "headline": "美国现货 ETF 连续 5 周净赎回",
    "summary": "连续 5 周净赎回，赎回集中在两只头部产品，短期资金面承压。",
    "category": "macro", "stance": "bearish", "impact": "high",
    "eventTs": NOW - 3600, "sourceRefs": ["D1", "D2"]}]}


async def _fresh() -> None:
    await init_db()
    sm = get_sessionmaker()
    async with sm() as session:
        for table in (AiIntelAlert, AiIntelCitation, AiIntelItem, AiIntelRound,
                      AiIntelWatch):
            await session.execute(delete(table))
        await session.commit()


def _deps(*, provider=None, llm=None, now=NOW, sessionmaker=None, **over) -> service.IntelDeps:
    settings = Settings(**over) if over else Settings()
    return service.IntelDeps(
        settings=settings, sessionmaker=sessionmaker or get_sessionmaker,
        search=SearchRunner(provider, timeout_s=5.0) if provider is not None else None,
        llm=llm, group_id="grp-1", bar="15m", now=(lambda: now))


async def _items(status: str | None = None) -> list[dict]:
    sm = get_sessionmaker()
    async with sm() as session:
        return await repo.load_intel_scope(session, INST, (status,) if status else None)


# ---------------- 采集轮 ----------------
@pytest.mark.asyncio
async def test_collect_round_end_to_end():
    await _fresh()
    deps = _deps(provider=FakeProvider(), llm=FakeLLM(_COLLECT_PAYLOAD))
    outcome = await service.run_collect_round(deps, INST)
    assert outcome.status == "ok" and outcome.accepted == 1
    rows = await _items("active")
    assert len(rows) == 1
    item = rows[0]
    assert item["tier"] == "ultra", "一切新采集条目的唯一落点是超短"
    assert item["domainCount"] == 2
    # G-1：每一条情报都带真实存在于**本轮语料**中的来源链接。
    assert {s["url"] for s in item["sources"]} == {"https://coindesk.com/a",
                                                  "https://theblock.co/b"}
    async with get_sessionmaker()() as session:
        rounds = await repo.list_intel_rounds(session, INST)
    assert rounds[0]["status"] == "ok" and rounds[0]["inputTokens"] == 11


@pytest.mark.asyncio
async def test_no_provider_produces_zero_items():
    """G-7：未配置检索源 ⇒ 一条情报都不产出，且降级码如实落轮。"""
    await _fresh()
    deps = _deps(provider=None, llm=None)
    outcome = await service.run_collect_round(deps, INST)
    assert outcome.status == "degraded" and outcome.degrade_code == "no_provider"
    assert await _items() == []


@pytest.mark.asyncio
async def test_search_empty_does_not_call_the_model():
    await _fresh()
    llm = FakeLLM(_COLLECT_PAYLOAD)
    deps = _deps(provider=FakeProvider(docs=[]), llm=llm)
    outcome = await service.run_collect_round(deps, INST)
    assert outcome.degrade_code == "search_empty"
    assert llm.calls == 0, "0 文档时调模型只会烧钱并诱发幻觉"


@pytest.mark.asyncio
async def test_search_failure_marks_round_failed_and_backs_off():
    await _fresh()
    from app.ai_auto.intel.search import IntelSearchError

    deps = _deps(provider=FakeProvider(error=IntelSearchError("network_error", "down")),
                 llm=FakeLLM(_COLLECT_PAYLOAD))
    outcome = await service.run_collect_round(deps, INST)
    assert outcome.status == "failed" and outcome.degrade_code == "search_failed"
    async with get_sessionmaker()() as session:
        watch = await repo.load_intel_watch(session, INST)
    assert watch["failStreak"] == 1
    # 下界 = 周期×(1−jitter) + 退避基数 = 30min×0.85 + 120s。写死 30min 会在 jitter 抽到
    # 负向时偶发红——一条会随机变红的门禁比没有门禁更坏。
    assert watch["nextDueTs"] - NOW >= int(30 * 60 * 0.85) + 120, "失败必须叠加退避"


@pytest.mark.asyncio
async def test_all_rejected_is_reported_not_silently_empty():
    await _fresh()
    bad = {"items": [{**_COLLECT_PAYLOAD["items"][0], "sourceRefs": ["D9"]}]}
    deps = _deps(provider=FakeProvider(), llm=FakeLLM(bad))
    outcome = await service.run_collect_round(deps, INST)
    assert outcome.status == "degraded" and outcome.degrade_code == "all_rejected"
    assert outcome.rejected == 1


@pytest.mark.asyncio
async def test_budget_exhausted_blocks_even_forced_rounds():
    await _fresh()
    sm = get_sessionmaker()
    async with sm() as session:
        await repo.ensure_intel_watch(session, INST)
        row = await session.get(AiIntelWatch, INST)
        row.day_key = repo.intel_utc_day_key()
        row.day_rounds = 96
        await session.commit()
    deps = _deps(provider=FakeProvider(), llm=FakeLLM(_COLLECT_PAYLOAD))
    outcome = await service.run_collect_round(deps, INST, force=True)
    assert outcome.degrade_code == "budget_exhausted", "额度是成本闸，手动触发不该能绕过它"


@pytest.mark.asyncio
async def test_not_due_writes_no_round_row():
    """未到期 ⇒ 直接返回 None，**不写 round 行**——那会让「整理记录」被无信息量的行刷屏。"""
    await _fresh()
    sm = get_sessionmaker()
    async with sm() as session:
        await repo.ensure_intel_watch(session, INST)
        row = await session.get(AiIntelWatch, INST)
        row.next_due_ts = NOW + 9999
        await session.commit()
    deps = _deps(provider=FakeProvider(), llm=FakeLLM(_COLLECT_PAYLOAD))
    assert await service.run_collect_round(deps, INST) is None
    async with sm() as session:
        assert await repo.list_intel_rounds(session, INST) == []


# ---------------- 不变式 I-4：会话不跨越 LLM ----------------
@pytest.mark.asyncio
async def test_no_db_session_open_across_llm_call():
    """G-14：SQLite 单写者，一个横跨 LLM 调用的写事务会把**全库**其它写入阻塞数十秒。"""
    await _fresh()
    real = get_sessionmaker()
    live = {"n": 0, "peak_during_llm": 0}

    class Counting:
        def __init__(self):
            self._inner = real()

        async def __aenter__(self):
            live["n"] += 1
            return await self._inner.__aenter__()

        async def __aexit__(self, *exc):
            live["n"] -= 1
            return await self._inner.__aexit__(*exc)

    def factory():
        return Counting

    def on_stream():
        live["peak_during_llm"] = max(live["peak_during_llm"], live["n"])

    deps = _deps(provider=FakeProvider(), llm=FakeLLM(_COLLECT_PAYLOAD, on_stream=on_stream),
                 sessionmaker=factory)
    outcome = await service.run_collect_round(deps, INST)
    assert outcome.status == "ok"
    assert live["peak_during_llm"] == 0, "LLM 调用期间必须没有任何在飞 DB 会话"
    assert live["n"] == 0, "会话必须全部关闭"


# ---------------- 整理轮 ----------------
async def _seed(**over) -> int:
    """种一条 active 条目并回它的 id。"""
    payload = {"instId": INST, "tier": "ultra", "category": "macro", "headline": "H",
               "summary": "s" * 30, "stance": "bearish", "impact": "high",
               "confidence": 0.7, "eventTs": NOW - 7200, "capturedTs": NOW - 7200,
               "expiresTs": NOW + 3600, "status": "active", "pinned": False,
               "userEdited": False, "undated": False, "isScheduled": False,
               "origin": "agent", "sources": [], "domainCount": 2,
               "dedupKey": f"k-{over.get('headline', 'H')}"}
    payload.update(over)
    async with get_sessionmaker()() as session:
        ids = await repo.upsert_intel_items(session, [payload])
    return ids[0]


def _curate_llm(actions):
    return FakeLLM({"actions": actions}, tool_name=intel_prompts.CURATE_TOOL_NAME)


@pytest.mark.asyncio
async def test_curate_rejects_promote_without_two_domains():
    await _fresh()
    item_id = await _seed(domainCount=1)
    deps = _deps(provider=FakeProvider(),
                 llm=_curate_llm([{"id": item_id, "action": "promote", "toTier": "mid",
                                   "reason": "看起来重要"}]))
    outcome = await service.run_curate_round(deps, INST)
    assert outcome.actions.get("rejected_promote_insufficient_evidence") == 1
    rows = await _items("active")
    assert rows[0]["tier"] == "ultra", "服务端裁决：不满足门槛的提议不执行"


@pytest.mark.asyncio
async def test_promote_recomputes_expiry():
    """不变式 I-3：写 `tier` 与重算 `expires_ts` 在同一条 UPDATE 里完成。"""
    await _fresh()
    item_id = await _seed()
    deps = _deps(provider=FakeProvider(),
                 llm=_curate_llm([{"id": item_id, "action": "promote", "toTier": "mid",
                                   "reason": "两家独立媒体佐证"}]))
    await service.run_curate_round(deps, INST)
    rows = await _items("active")
    assert rows[0]["tier"] == "mid"
    assert rows[0]["expiresTs"] > NOW + 40 * 86400, "寿命必须跟着层级走"


@pytest.mark.asyncio
async def test_promoted_item_survives_ultra_ttl():
    """G-11 / 评审 P0-2 的**跨边界**断言：把时钟推过 `event_ts + 72h` 再看它还在不在。

    只在同一轮内断言「升级成功」的用例永远看不到这条自毁路径：`expires_ts` 若没随升级重算，
    整座多级库会在事件发生 72 小时后被确定性清理整层扫空，而 G-4「下次重开原样还在」对任何超过
    三天的条目恒假。
    """
    await _fresh()
    item_id = await _seed()
    up = _deps(provider=FakeProvider(),
               llm=_curate_llm([{"id": item_id, "action": "promote", "toTier": "mid",
                                 "reason": "两家独立媒体佐证"}]))
    await service.run_curate_round(up, INST)
    # —— 时钟推过超短 TTL（72h）之后再跑一次整理（模型这次什么都不提议）——
    later = NOW + 80 * 3600
    quiet = _deps(provider=FakeProvider(), llm=_curate_llm([]), now=later)
    await service.run_curate_round(quiet, INST)
    rows = await _items("active")
    assert [r["id"] for r in rows] == [item_id], "升进中期的条目不该被超短 TTL 扫掉"


@pytest.mark.asyncio
async def test_ttl_cleanup_runs_even_when_model_returns_no_actions():
    """确定性清理**不依赖模型**：模型哑火时噪声照样被清。"""
    await _fresh()
    stale = await _seed(headline="过期", expiresTs=NOW - 1)
    deps = _deps(provider=FakeProvider(), llm=_curate_llm([]))
    outcome = await service.run_curate_round(deps, INST)
    assert outcome.actions.get("ttl") == 1
    rows = await _items()
    assert [r["status"] for r in rows if r["id"] == stale] == ["archived"]


@pytest.mark.asyncio
async def test_pinned_item_survives_overflow_eviction():
    await _fresh()
    pinned = await _seed(headline="置顶", pinned=True, confidence=0.05,
                         expiresTs=0, dedupKey="k-pin")
    for i in range(3):
        await _seed(headline=f"普通{i}", confidence=0.9, expiresTs=NOW + 99999,
                    dedupKey=f"k-n{i}")
    deps = _deps(provider=FakeProvider(), llm=_curate_llm([]),
                 ai_trade_intel_max_active_ultra=5)
    # 硬上限收到 2：四条 active 淘汰到 2 条，**且置顶那条不在被淘汰之列**——即便它的 score 最低。
    # 「淘汰到上限」按**全部 active** 计（含受保护的），故最终恰好 2 条。
    deps.limits.max_active["ultra"] = 2
    await service.run_curate_round(deps, INST)
    rows = await _items("active")
    assert pinned in [r["id"] for r in rows], "score 最低却受保护的条目绝不能被溢出淘汰带走"
    assert len(rows) == 2


@pytest.mark.asyncio
async def test_lapsed_scheduled_event_is_demoted():
    """G-12：事件时刻已过且未被后续语料证实 ⇒ 降级；防「永远的明天」堆积。"""
    await _fresh()
    item_id = await _seed(headline="下周解锁", tier="mid", isScheduled=True,
                         eventTs=NOW - 3 * 86400, expiresTs=NOW + 99999,
                         dedupKey="k-sched")
    async with get_sessionmaker()() as session:
        row = await session.get(AiIntelItem, item_id)
        row.updated_ts = NOW - 5 * 86400          # 自事件之后没有新增来源
        await session.commit()
    deps = _deps(provider=FakeProvider(), llm=_curate_llm([]))
    outcome = await service.run_curate_round(deps, INST)
    assert outcome.actions.get("lapsed_schedule") == 1
    rows = await _items("active")
    assert rows[0]["tier"] == "ultra"


@pytest.mark.asyncio
async def test_archived_duplicate_revives_instead_of_crashing():
    """G-13 / 评审 P0-3：先让条目真的被 TTL 归档，再喂同一条新闻。

    去重闸的作用域若比 `uq_ai_intel_dedup` 窄，这一步会撞 `IntegrityError` 让**整轮事务回滚**，
    round 记 `failed` 并进退避——而热门标的上这条路径几天内必然走到。
    """
    await _fresh()
    # ① 正常采一轮
    deps = _deps(provider=FakeProvider(), llm=FakeLLM(_COLLECT_PAYLOAD))
    assert (await service.run_collect_round(deps, INST)).status == "ok"
    first = (await _items("active"))[0]
    # ② 让它真的被 TTL 归档
    async with get_sessionmaker()() as session:
        row = await session.get(AiIntelItem, first["id"])
        row.expires_ts = NOW - 1
        await session.commit()
    await service.run_curate_round(_deps(provider=FakeProvider(), llm=_curate_llm([])), INST)
    assert (await _items("active")) == []
    # ③ 媒体隔天再报同一件事
    again = _deps(provider=FakeProvider(), llm=FakeLLM(_COLLECT_PAYLOAD), now=NOW + 3600)
    async with get_sessionmaker()() as session:
        watch = await session.get(AiIntelWatch, INST)
        watch.next_due_ts = 0
        await session.commit()
    outcome = await service.run_collect_round(again, INST)
    assert outcome.status == "ok", "整轮绝不因为 IntegrityError 变成 failed"
    assert outcome.merged == 1
    rows = await _items("active")
    assert [r["id"] for r in rows] == [first["id"]], "复活的是原来那一条，不是新造一条"


# ---------------- 快照 ----------------
@pytest.mark.asyncio
async def test_snapshot_reports_provider_state_and_unread():
    await _fresh()
    deps = _deps(provider=FakeProvider(), llm=FakeLLM(_COLLECT_PAYLOAD))
    await service.run_collect_round(deps, INST)
    snapshot = await service.build_snapshot(deps, INST)
    assert snapshot["summary"]["providerConfigured"] is True
    assert snapshot["summary"]["counts"]["ultra"] == 1
    assert snapshot["summary"]["unread"] == 1
    assert snapshot["digest"]["lines"], "成功采集过 ⇒ 注入段非空"
    async with get_sessionmaker()() as session:
        await repo.mark_intel_seen(session, INST, ts=NOW + 10)
    assert (await service.build_snapshot(deps, INST))["summary"]["unread"] == 0


@pytest.mark.asyncio
async def test_snapshot_respects_inject_switch():
    """`AI_TRADE_INTEL_INJECT=false` ⇒ 照常采集与展示，但一个字都不喂给交易 Agent。"""
    await _fresh()
    deps = _deps(provider=FakeProvider(), llm=FakeLLM(_COLLECT_PAYLOAD),
                 ai_trade_intel_inject=False)
    await service.run_collect_round(deps, INST)
    snapshot = await service.build_snapshot(deps, INST)
    assert snapshot["summary"]["counts"]["ultra"] == 1
    assert snapshot["digest"] == {"lines": [], "hidden": 0}


# ---------------- closed-loop：设置桥 / 降级文案 / 估算 / 整理轮末尾的两件事 ----------------
def test_every_new_intel_setting_key_round_trips_through_the_bridge():
    """G-18 / P0-5：**逐键**断言 8 个新键都在 `INTEL_SETTING_BRIDGE` 里，且 wire 键与字段对得上。

    不扩那张表 ⇒ 请求 200、面板上开关照动、`Settings` 上的值**永远不变**。断言「请求不报错」
    抓不到这件事，故这里断言的是键映射本身。
    """
    bridge = {wire: (app_key, field, caster)
              for wire, app_key, field, caster in service.INTEL_SETTING_BRIDGE}
    expected = {
        "citeEnabled": ("ai_trade_intel_cite_enabled", bool),
        "attributionEnabled": ("ai_trade_intel_attribution_enabled", bool),
        "verifyEnabled": ("ai_trade_intel_verify_enabled", bool),
        "verifyEveryNCurates": ("ai_trade_intel_verify_every_n_curates", int),
        "verifyMaxItems": ("ai_trade_intel_verify_max_items", int),
        "verifyDocsPerItem": ("ai_trade_intel_verify_docs_per_item", int),
        "blockedSweepEnabled": ("ai_trade_intel_blocked_sweep_enabled", bool),
        "citeMaxRefs": ("ai_trade_intel_cite_max_refs", int),
    }
    settings = Settings()
    for wire, (field, caster) in expected.items():
        assert wire in bridge, f"{wire} 没进 INTEL_SETTING_BRIDGE ⇒ 它是一个假开关"
        app_key, mapped_field, mapped_caster = bridge[wire]
        assert (mapped_field, mapped_caster) == (field, caster), wire
        assert app_key.startswith("ai_intel."), wire
        assert hasattr(settings, field), f"{field} 在 Settings 上不存在"


@pytest.mark.asyncio
async def test_new_setting_keys_are_readable_after_being_written():
    """G-18 的另一半：写进去 → `resolve_intel_settings` 读出来 → **逐键**比对生效值。"""
    await _fresh()
    writes = {"ai_intel.cite_enabled": False, "ai_intel.attribution_enabled": False,
              "ai_intel.verify_enabled": False, "ai_intel.verify_every_n": 3,
              "ai_intel.verify_max_items": 2, "ai_intel.verify_docs_per_item": 5,
              "ai_intel.blocked_sweep": False, "ai_intel.cite_max_refs": 4}
    async with get_sessionmaker()() as session:
        for key, value in writes.items():
            await repo.set_app_setting(session, key, value)
        derived = await service.resolve_intel_settings(session, Settings())

    assert derived.ai_trade_intel_cite_enabled is False
    assert derived.ai_trade_intel_attribution_enabled is False
    assert derived.ai_trade_intel_verify_enabled is False
    assert derived.ai_trade_intel_verify_every_n_curates == 3
    assert derived.ai_trade_intel_verify_max_items == 2
    assert derived.ai_trade_intel_verify_docs_per_item == 5
    assert derived.ai_trade_intel_blocked_sweep_enabled is False
    assert derived.ai_trade_intel_cite_max_refs == 4


def test_every_new_degrade_code_has_copy():
    """G-19 / P1-4：四个新码**逐个**断言不等于兜底串。

    漏登记不报错——`degrade_message` 会回落成 `f"情报流水线降级（{code}）"`，用户看到的就是裸码。
    """
    from app.ai_auto.intel import taxonomy as tax

    for code in ("verify_no_provider", "verify_search_failed", "verify_no_corpus",
                 "verify_llm_failed"):
        message = tax.degrade_message(code, reason="x")
        assert message != f"情报流水线降级（{code}）", f"{code} 没进 DEGRADE_TEXT"
        assert code not in message, f"{code} 的文案里露出了裸码"
    assert len(tax.degrade_message("verify_no_corpus")) > 4


def test_estimate_daily_cost_counts_the_verify_round():
    """§3.5：核查轮**必须**被算进估算——把日配额悄悄扩大一倍不是实现能自行决定的事。"""
    base = service.estimate_daily_cost(symbols=2, interval_min=30, corpus_docs=16,
                                       snippet_chars=500)
    with_verify = service.estimate_daily_cost(symbols=2, interval_min=30, corpus_docs=16,
                                              snippet_chars=500, verify_enabled=True,
                                              verify_docs=12)

    assert with_verify["roundsPerDay"] > base["roundsPerDay"]
    assert with_verify["inputTokensPerDay"] > base["inputTokensPerDay"]
    # 默认形参 ⇒ 既有调用点逐字节回退。
    assert service.estimate_daily_cost(symbols=2, interval_min=30, corpus_docs=16,
                                       snippet_chars=500, verify_enabled=True,
                                       verify_docs=0) == base


@pytest.mark.asyncio
async def test_curate_round_prunes_citations_and_sweeps_blocked_items():
    """整理轮末尾的两件事：引用明细到期裁剪 + 全部来源被拉黑的存量归档。"""
    await _fresh()
    async with get_sessionmaker()() as session:
        ids = await repo.upsert_intel_items(session, [{
            "instId": INST, "tier": "ultra", "category": "market", "headline": "脏源独家",
            "summary": "s", "stance": "neutral", "impact": "medium", "confidence": 0.5,
            "eventTs": NOW - 3600, "capturedTs": NOW - 3600, "expiresTs": NOW + 86400,
            "status": "active", "origin": "agent", "dedupKey": "dirty",
            "sources": [{"url": "https://bad.example/x", "domain": "bad.example",
                         "publishedTs": 0}], "domainCount": 1}])
        await repo.set_intel_feedback(session, key="bad.example", action="block", inst_id=INST)
        await repo.append_intel_citations(session, [{
            "itemId": ids[0], "instId": INST, "runId": 1, "roundNo": 1,
            "citedTs": NOW - 400 * 86400, "refToken": "I01", "role": "hold"}])

    outcome = await service.run_curate_round(_deps(provider=None, llm=None), INST)

    assert outcome.actions.get("blocked_sweep") == 1
    assert outcome.actions.get("citations_pruned") == 1
    async with get_sessionmaker()() as session:
        assert (await repo.get_intel_item(session, ids[0]))["status"] == "archived"
        rows, _more = await repo.list_intel_citations(session, inst_id=INST, limit=10)
    assert rows == [], "只删明细"
    assert (await _items())[0]["citeCount"] == 1, "**不回滚统计列**"


# ---------------- evidence-grounding：抓取接线（§3.1.1） ----------------
@pytest.mark.asyncio
async def test_round_row_carries_four_new_counters():
    """G-14：`write_intel_round` 是**逐列手抄**的 payload 映射；新列不加一行即恒为 0 且不报错。"""
    await _fresh()
    deps = _deps(provider=FakeProvider(), llm=FakeLLM(_COLLECT_PAYLOAD))
    await service.run_collect_round(deps, INST)
    async with get_sessionmaker()() as session:
        row = (await repo.list_intel_rounds(session, INST))[0]
    for key in ("readerAttempt", "readerOk", "quotesProposed", "quotesVerified"):
        assert key in row, f"轮次 wire 少了 {key}（写→读两端都要通）"


@pytest.mark.asyncio
async def test_reader_none_means_no_fetch_and_no_error():
    """`IntelDeps.reader is None` ⇒ **不抓、不报错**，语料块逐字节回到本轮合入前。"""
    await _fresh()
    deps = _deps(provider=FakeProvider(), llm=FakeLLM(_COLLECT_PAYLOAD))
    assert deps.reader is None
    outcome = await service.run_collect_round(deps, INST)
    assert outcome.status == "ok"
    async with get_sessionmaker()() as session:
        health = await repo.document_health(session)
    assert health["cachedDocs"] == 0


@pytest.mark.asyncio
async def test_zero_quote_item_gets_a_reason_not_legacy():
    """G-25：今天入库的零引文条目**不叫** `legacy`——那是四句话里唯一一句确定错误的。"""
    await _fresh()
    deps = _deps(provider=FakeProvider(), llm=FakeLLM(_COLLECT_PAYLOAD))
    await service.run_collect_round(deps, INST)
    rows = await _items("active")
    assert rows[0]["quoteState"] == "noBody"
    assert rows[0]["quoteState"] != "legacy"


@pytest.mark.asyncio
async def test_curate_round_prunes_the_document_cache():
    """整理轮的第 6 步：正文缓存的三道闸。**只删缓存，绝不动引文**。"""
    await _fresh()
    from app.ai_auto.intel.corpus import document_key

    async with get_sessionmaker()() as session:
        await repo.upsert_documents(session, [{
            "urlHash": document_key("https://reuters.com/old"),
            "url": "https://reuters.com/old", "domain": "reuters.com", "body": "旧正文",
            "bodyChars": 3, "status": "ok", "fetchedTs": NOW - 400 * 3600}])
    deps = _deps(provider=FakeProvider(), llm=None)
    outcome = await service.run_curate_round(deps, INST)
    assert outcome.actions.get("documents_pruned") == 1
    async with get_sessionmaker()() as session:
        assert (await repo.document_health(session))["cachedDocs"] == 0


# ---------- ai-trade-intel-actionability：G-25 + 采集轮的告警写段 ----------
@pytest.mark.asyncio
async def test_the_summary_always_carries_the_actionability_keys():
    """G-25：`alerts24h` / `lastAlertTs` / `efficacy` **在位**（形状恒一致）。

    未装配的分组由 `runtime._static_summary` 给出同形状的零值——前端读到 `undefined` 会把
    「情报层没装上」画成「零告警」，本仓库已在 `citations` / `verify` / `quoteHitRate` 上
    连踩三次。
    """
    await _fresh()
    deps = _deps(provider=FakeProvider(), llm=FakeLLM(_COLLECT_PAYLOAD))
    snapshot = await service.build_snapshot(deps, INST)
    summary = snapshot["summary"]
    assert summary["alerts24h"] == 0
    assert summary["lastAlertTs"] is None, "从未告警过 ≠ 1970 年告警过"
    assert summary["efficacy"]["enabled"] is True
    assert summary["efficacy"]["overall"]["accuracy"] is None
    assert snapshot["conflicts"] == []
    assert isinstance(snapshot["digestItemIds"], frozenset)
    # 两条通路（WS 推送 / REST 读取）共用**同一个**装配点，故形状逐键一致——写在
    # `build_snapshot` 里会让 REST 那一份少一个键，而少键**不报错**。
    async with get_sessionmaker()() as session:
        fresh = await service.read_summary(session, INST, settings=deps.settings,
                                           provider_configured=True, now=NOW)
    assert set(fresh) == set(summary)


@pytest.mark.asyncio
async def test_every_collect_round_writes_exactly_one_ledger_row():
    """**无论 fired 与否都写**：「今天为什么一次都没告警」的答案就住在这些行里。

    同一次 `commit`（评审 P1-5）：条目回滚而告警行留下，等于凭空多出一条指向不存在条目的告警。
    """
    await _fresh()
    deps = _deps(provider=FakeProvider(), llm=FakeLLM(_COLLECT_PAYLOAD))
    outcome = await service.run_collect_round(deps, INST)
    assert outcome.status == "ok"
    async with get_sessionmaker()() as session:
        rows, _ = await repo.list_intel_alerts(session, inst_id=INST)
        items = await repo.load_intel_scope(session, INST)
    assert len(rows) == 1
    row = rows[0]
    # 默认阈值下这条 macro 情报只有 2 个域名、没有引文 ⇒ 被 `no_quote` 压下去，
    # 但台账**照样有一行**，且它说得出被压下去的是哪一条。
    assert row["fired"] is False and row["suppressedCode"]
    assert row["itemId"] == items[0]["id"]
    assert row["headline"] == items[0]["headline"]
    assert row["roundId"] > 0


@pytest.mark.asyncio
async def test_a_degraded_round_writes_no_ledger_row_at_all():
    """降级路径（`finish_degraded`）不走写段 ⇒ 一条台账都不该产生。

    它们的解释落在轮存根的 `degradeCode` 上；在告警台账里再写一行「因为检索失败所以没告警」
    是同一个事实的第二份账。
    """
    await _fresh()
    deps = _deps(provider=None, llm=None)
    await service.run_collect_round(deps, INST)
    async with get_sessionmaker()() as session:
        rows, _ = await repo.list_intel_alerts(session, inst_id=INST)
    assert rows == []
