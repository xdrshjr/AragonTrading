"""后训练样本的形状与八条筛选纪律（ai-trade-audit-journal §2.5.2）。

`skipped` 的**九个**桶加总必须等于「总候选 − 导出条数」——一个「导出了 37 条」却不说
「另外 2810 条为什么没导出」的导出器，会让人误以为系统只做了 37 次决策。
"""
from __future__ import annotations

import json

import pytest
from sqlalchemy import delete, select

from app.ai_auto.journal.export_audit import ExportBudget
from app.ai_auto.journal.export_sft import (
    SKIPPED_BUCKETS,
    SftContext,
    SftOptions,
    preview,
    scope_has_client_order_id,
    stream_sft,
)
from app.db.models import (
    AiTradeDecisionLog,
    AiTradeEventLog,
    AiTradePromptProfile,
    Strategy,
    StrategyRun,
    Trade,
)
from app.db.session import get_sessionmaker, init_db

from .test_ai_trade_journal_api import GROUP, INST, NOW, decision_row

_CTX = SftContext(context_chain_enabled=True, resolved_min_link_confidence="exact",
                  resolved_reason="explicit")


async def _reset() -> None:
    await init_db()
    async with get_sessionmaker()() as session:
        for table in (AiTradeDecisionLog, AiTradeEventLog, AiTradePromptProfile):
            await session.execute(delete(table))
        # 只清**本组自己那些 run** 的成交腿：整表删会拿掉别的用例的数据（共用一个临时库）。
        await session.execute(delete(Trade).where(Trade.run_id.in_(
            select(StrategyRun.id).where(StrategyRun.group_id == GROUP))))
        session.add(AiTradePromptProfile(sha256="p" * 64, kind="profile",
                                         system_prompt="SYS", tools_json=json.dumps(
                                             [{"name": "submit_trade_decision",
                                               "description": "d", "input_schema": {}}]),
                                         provider="anthropic", model="m",
                                         first_group_id=GROUP, created_ts=NOW))
        await session.commit()


async def _add(rows: list[AiTradeDecisionLog], *, settle: bool = True,
               open_only: bool = False) -> None:
    """写入决策行并按需补成交腿。

    `settle=True` ⇒ 补一对开→平（`onlySettled` 能过）；`open_only=True` ⇒ 只补开仓腿
    （关联得上但**尚未结算**，这才是 `unsettled` 桶要测的那个断面）；`settle=False` ⇒
    一条腿都不补（那会先掉进 `lowConfidence`，是另一件事）。
    """
    async with get_sessionmaker()() as session:
        strategy = Strategy(template_key="ai_trade", name="sft", params_json="{}")
        session.add(strategy)
        await session.flush()
        run = StrategyRun(strategy_id=strategy.id, inst_id=INST, bar="15m", mode="paper",
                          status="running", start_ts=NOW, group_id=GROUP, config_json="{}")
        session.add(run)
        await session.flush()
        for row in rows:
            row.run_id = run.id
            session.add(row)
            if not settle and not open_only:
                continue
            session.add(Trade(run_id=run.id, ts=row.ts, side="long", price=100.0, qty=1.0,
                              fee=0.1, pnl=0.0, position_after=1.0, reason="agent",
                              explain_json=json.dumps({"meta": {"clOrdId": row.cl_ord_id}})))
            if open_only:
                continue
            session.add(Trade(run_id=run.id, ts=row.ts + 60, side="close", price=110.0,
                              qty=1.0, fee=0.1, pnl=9.5, position_after=0.0,
                              reason="agent_close",
                              explain_json=json.dumps({"meta": {
                                  "realizedBps": 95.0, "rMultiple": 1.2,
                                  "exitSource": "agent", "holdSeconds": 60}})))
        await session.commit()


async def _collect(**opts) -> tuple[list[dict], dict]:
    lines = []
    stream = stream_sft({"groupId": GROUP}, ExportBudget(page_size=50),
                        SftOptions(**opts), _CTX, {})
    async for chunk in stream:
        lines.append(json.loads(chunk))
    return [row for row in lines if row["kind"] == "sample"], lines[-1]


@pytest.mark.asyncio
async def test_sample_shape_is_messages_tools_meta_label_quality():
    await _reset()
    await _add([decision_row(1)])
    samples, eof = await _collect()
    assert len(samples) == 1
    sample = samples[0]
    assert set(sample) == {"kind", "id", "messages", "tools", "meta", "label", "quality"}
    assert sample["messages"][0]["role"] == "system"
    last = sample["messages"][-1]
    assert last["role"] == "assistant" and last["content"][0]["type"] == "tool_use"
    # 训练目标用的是 `output_json`（模型原始入参），不是门禁后的最终 decision。
    assert last["content"][0]["input"] == {"action": "open_long", "sizeFraction": 0.5}
    assert sample["tools"][0]["name"] == "submit_trade_decision"
    assert sample["label"]["settled"] is True and sample["label"]["realizedBps"] == 95.0
    assert sample["quality"]["contextTruncated"] is False
    assert eof["kind"] == "eof" and set(eof["skipped"]) == set(SKIPPED_BUCKETS)


@pytest.mark.asyncio
async def test_gate_vetoed_rounds_are_excluded_by_default():
    """把被否决的输出当正样本，训出来的就是会被否决的行为。"""
    await _reset()
    await _add([decision_row(1, action="open_long", final_action="hold",
                             degrade_reason="low_consensus")])
    samples, eof = await _collect()
    assert samples == [] and eof["skipped"]["gateVetoed"] == 1
    samples, _eof = await _collect(exclude_gate_vetoed=False)
    assert len(samples) == 1 and samples[0]["quality"]["gateVetoed"] is True


@pytest.mark.asyncio
async def test_degraded_rounds_are_excluded_by_default():
    """降级轮里 assistant 那一段是**系统合成的 hold**，不是模型说的话。"""
    await _reset()
    await _add([decision_row(1, action="hold", final_action="hold",
                             degrade_reason="llm_error")])
    samples, eof = await _collect(include_hold=True)
    assert samples == [] and eof["skipped"]["degraded"] == 1


@pytest.mark.asyncio
async def test_hold_rounds_are_excluded_by_default():
    """观望轮占比极高（生产实测某 run 484 轮 100% 观望），全量导出会把样本严重压偏。"""
    await _reset()
    await _add([decision_row(1, action="hold", final_action="hold")])
    samples, eof = await _collect()
    assert samples == [] and eof["skipped"]["hold"] == 1
    samples, _eof = await _collect(include_hold=True)
    assert len(samples) == 1


@pytest.mark.asyncio
async def test_unsettled_rounds_are_excluded_by_default_and_never_zero_filled():
    await _reset()
    await _add([decision_row(1)], open_only=True)
    samples, eof = await _collect()
    assert samples == [] and eof["skipped"]["unsettled"] == 1
    samples, _eof = await _collect(only_settled=False)
    assert len(samples) == 1
    label = samples[0]["label"]
    assert label["settled"] is False
    assert "realizedPnlUsd" not in label, "没有标签时盈亏字段**整键缺席**，绝不填 0"


@pytest.mark.asyncio
async def test_minimal_tier_rows_are_excluded_and_counted():
    await _reset()
    await _add([decision_row(1, capture_tier="minimal", payload_json=None,
                             output_json=None)])
    samples, eof = await _collect()
    assert samples == [] and eof["skipped"]["minimalTier"] == 1


@pytest.mark.asyncio
async def test_truncated_payload_rows_are_excluded_and_counted():
    """截断的上下文重建不出原提示词——宁可少导出一条，也不导出一条假样本。"""
    await _reset()
    await _add([decision_row(1, payload_truncated=True)])
    samples, eof = await _collect()
    assert samples == [] and eof["skipped"]["payloadTruncated"] == 1


@pytest.mark.asyncio
async def test_prompt_drift_rows_are_excluded_and_counted():
    """存档 sha 与按当前代码重建出的 sha 不一致 ⇒ 该样本被排除并如实报数。"""
    await _reset()
    await _add([decision_row(1, prompt_sha256="deadbeef" * 8)])
    samples, eof = await _collect()
    assert samples == [] and eof["skipped"]["promptDrift"] == 1


@pytest.mark.asyncio
async def test_low_confidence_rows_are_excluded_under_exact():
    await _reset()
    await _add([decision_row(1, cl_ord_id="", fill_ref_json=json.dumps(
        {"traded": True, "legs": [{"side": "long", "px": 100.0, "sz": 7.0,
                                   "source": "round"}]}))])
    samples, eof = await _collect()
    assert samples == [] and eof["skipped"]["lowConfidence"] == 1


@pytest.mark.asyncio
async def test_skipped_buckets_sum_to_total_minus_emitted():
    """这张表唯一的价值就是它闭合：每一行至多进一个桶。"""
    await _reset()
    await _add([decision_row(1),
                decision_row(2, action="hold", final_action="hold"),
                decision_row(3, action="open_long", final_action="hold",
                             degrade_reason="low_consensus"),
                decision_row(4, capture_tier="minimal", payload_json=None)])
    samples, eof = await _collect()
    assert len(samples) + sum(eof["skipped"].values()) == 4


@pytest.mark.asyncio
async def test_paperless_scope_resolves_to_heuristic_and_still_exports():
    """★ 纸面会话恒 0 条的回归护栏（评审 RP1-6 / R15）。"""
    await _reset()
    await _add([decision_row(1, cl_ord_id="", fill_ref_json=json.dumps(
        {"traded": True, "legs": [{"side": "long", "px": 100.0, "sz": 1.0,
                                   "source": "round"}]}))])
    async with get_sessionmaker()() as session:
        rows = (await session.execute(
            __import__("sqlalchemy").select(Trade))).scalars().all()
        for trade in rows:                    # 抹掉交易所单号：纸面会话在结构上没有它
            trade.explain_json = json.dumps({"meta": {}})
        await session.commit()
    assert await scope_has_client_order_id({"groupId": GROUP}) is False
    ctx = SftContext(context_chain_enabled=True, resolved_min_link_confidence="heuristic",
                     resolved_reason="no_client_order_id_in_scope")
    lines = []
    async for chunk in stream_sft({"groupId": GROUP}, ExportBudget(),
                                  SftOptions(min_link_confidence="auto"), ctx, {}):
        lines.append(json.loads(chunk))
    samples = [row for row in lines if row["kind"] == "sample"]
    assert len(samples) > 0, "纸面会话必须导得出样本，否则用户拿到一个没有解释的空文件"
    assert lines[0]["resolvedReason"] == "no_client_order_id_in_scope"
    assert samples[0]["label"]["linkConfidence"] == "heuristic"


@pytest.mark.asyncio
async def test_preview_and_stream_agree_on_the_counts():
    """两处各写一遍筛选，预检说的条数与真正导出的条数就会分叉——而那种分叉不报任何错。"""
    await _reset()
    await _add([decision_row(1), decision_row(2, action="hold", final_action="hold")])
    result = await preview({"groupId": GROUP}, ExportBudget(), SftOptions(), _CTX)
    samples, eof = await _collect()
    assert result["rows"] == len(samples)
    assert result["skipped"] == eof["skipped"]
    assert result["slices"] >= 1 and result["estimatedSeconds"] >= 0
