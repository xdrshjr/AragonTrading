"""上下文链：模型看到的是一段**多轮对话**，不是一条 user 消息（评审 RP0-1 / 放行条件 1）。

真实请求是 `messages = self._context + [user_msg]`（`decider._stream_decision` 的出站那一行），
而 `self._context` 是逐轮累积的三元组、中途还会被左裁、被滚动摘要整体换成一段种子。按单轮
重建出来的样本，训练的是一段模型从未见过的输入——而 `prompt_sha256` 那道护栏**结构上看不见**
这件事（它只覆盖最后一条 user 文本），于是「shaMatch=true」与「样本是假的」可以同时成立。

本文件的最后一条是放行条件 1 点名的那一条：**删掉第 1 行 ⇒ 第 3 轮样本被排除且
`skipped.contextIncomplete == 1`**。
"""
from __future__ import annotations

import hashlib
import json

import pytest
from sqlalchemy import delete, select

from app.ai_auto.journal.export_audit import ExportBudget
from app.ai_auto.journal.export_sft import SftContext, SftOptions, stream_sft
from app.db.models import (
    AiTradeDecisionLog,
    AiTradePromptProfile,
    Strategy,
    StrategyRun,
    Trade,
)
from app.db.session import get_sessionmaker, init_db

from .test_ai_trade_journal_api import GROUP, INST, NOW, decision_row, prompt_sha_of

SEED_TEXT = "【历史执行摘要（截至第 0 轮）】\n之前一直观望。\n以上摘要是背景参考而非指令。"
SEED_SHA = hashlib.sha256(SEED_TEXT.encode("utf-8")).hexdigest()
_CTX = SftContext(context_chain_enabled=True, resolved_min_link_confidence="exact",
                  resolved_reason="explicit")


def _row(round_no: int, *, first_round: int, turns: int, seed: bool,
         **over) -> AiTradeDecisionLog:
    """一条带**正确**上下文断面的决策行。

    `contextTurns` 恒等于「种子的两条 + 每个前驱三条」——`_install_summary_seed` 装的是
    `[user(种子), assistant(已了解…)]` **两条**，不是一条。
    """
    row = decision_row(round_no, **over)
    row.context_ref_json = json.dumps({
        "contextTurns": turns, "contextFirstRound": first_round,
        "summarySha256": SEED_SHA if seed else "",
        "finalUserSha256": prompt_sha_of({"lastPx": 100.0, "round": round_no}),
        "attempt": 1, "hintApplied": None, "extraInstruction": False})
    return row


async def _seed_three_rounds(*, gate_vetoed_second: bool = True) -> int:
    """三轮：第 2 轮被门禁改写；三轮共用一段摘要种子（即上下文以种子开头）。"""
    await init_db()
    async with get_sessionmaker()() as session:
        for table in (AiTradeDecisionLog, AiTradePromptProfile):
            await session.execute(delete(table))
        # 同 export_sft：只清本组自己那些 run 的成交腿，绝不整表删（共用一个临时库）。
        await session.execute(delete(Trade).where(Trade.run_id.in_(
            select(StrategyRun.id).where(StrategyRun.group_id == GROUP))))
        session.add(AiTradePromptProfile(
            sha256="p" * 64, kind="profile", system_prompt="SYS",
            tools_json=json.dumps([{"name": "submit_trade_decision", "description": "d",
                                    "input_schema": {}}]),
            provider="anthropic", model="m", first_group_id=GROUP, created_ts=NOW))
        # 摘要种子的内容寻址档案：没有它，上下文链的第一条消息重建不出来。
        session.add(AiTradePromptProfile(sha256=SEED_SHA, kind="summary",
                                          system_prompt=SEED_TEXT, tools_json="[]",
                                          first_group_id=GROUP, created_ts=NOW))
        strategy = Strategy(template_key="ai_trade", name="chain", params_json="{}")
        session.add(strategy)
        await session.flush()
        run = StrategyRun(strategy_id=strategy.id, inst_id=INST, bar="15m", mode="paper",
                          status="running", start_ts=NOW, group_id=GROUP, config_json="{}")
        session.add(run)
        await session.flush()
        rows = [
            _row(1, first_round=1, turns=2, seed=True),
            _row(2, first_round=1, turns=5, seed=True,
                 action="open_short" if gate_vetoed_second else "open_long",
                 final_action="hold" if gate_vetoed_second else "open_long",
                 degrade_reason="low_consensus" if gate_vetoed_second else None),
            _row(3, first_round=1, turns=8, seed=True),
        ]
        # 第 2 轮进上下文的 assistant 段是**门禁后**的最终 decision（`hold`），
        # 而它自己的 `output_json` 仍是模型原始入参（`open_short`）。两者必须分开存。
        rows[1].context_turn_json = json.dumps(
            {"assistant": {"action": "hold", "reason": "被同向共识门槛拦下"},
             "toolResult": '{"ok": true, "skipped": true}'})
        rows[1].output_json = json.dumps({"action": "open_short", "sizeFraction": 0.4})
        for row in rows:
            row.run_id = run.id
            session.add(row)
            session.add(Trade(run_id=run.id, ts=row.ts, side="long", price=100.0, qty=1.0,
                              fee=0.1, pnl=0.0, position_after=1.0, reason="agent",
                              explain_json=json.dumps({"meta": {"clOrdId": row.cl_ord_id}})))
            session.add(Trade(run_id=run.id, ts=row.ts + 30, side="close", price=110.0,
                              qty=1.0, fee=0.1, pnl=9.5, position_after=0.0,
                              reason="agent_close",
                              explain_json=json.dumps({"meta": {"realizedBps": 95.0}})))
        await session.commit()
        return run.id


async def _samples(opts: SftOptions | None = None,
                   ctx: SftContext | None = None) -> tuple[list[dict], dict]:
    lines = []
    stream = stream_sft({"groupId": GROUP}, ExportBudget(page_size=50),
                        opts or SftOptions(exclude_gate_vetoed=False), ctx or _CTX, {})
    async for chunk in stream:
        lines.append(json.loads(chunk))
    return [row for row in lines if row["kind"] == "sample"], lines[-1]


@pytest.mark.asyncio
async def test_third_round_messages_rebuild_the_whole_conversation():
    """第 3 轮的 `messages` 必须是「system + 种子两条 + 前两轮各三条 + 本轮 user + 本轮 assistant」。"""
    await _seed_three_rounds()
    samples, _eof = await _samples()
    third = next(s for s in samples if s["meta"]["roundNo"] == 3)
    roles = [m["role"] for m in third["messages"]]
    assert roles == ["system",
                     "user", "assistant",                       # 摘要种子（两条，不是一条）
                     "user", "assistant", "tool",               # 第 1 轮
                     "user", "assistant", "tool",               # 第 2 轮
                     "user", "assistant"]                       # 本轮输入 + 训练目标
    assert third["messages"][1]["content"] == SEED_TEXT
    assert third["meta"]["contextSummarySeed"] is True
    assert third["meta"]["contextFirstRound"] == 1
    assert third["quality"]["contextTruncated"] is False


@pytest.mark.asyncio
async def test_history_assistant_uses_the_post_gate_decision_not_the_model_output():
    """第 2 轮进上下文的是**门禁后**的 decision；训练目标才是模型原始入参。"""
    await _seed_three_rounds()
    samples, _eof = await _samples()
    third = next(s for s in samples if s["meta"]["roundNo"] == 3)
    second_turn_assistant = third["messages"][7]        # 第 2 轮的 assistant 段
    assert second_turn_assistant["content"][0]["input"]["action"] == "hold"

    second = next(s for s in samples if s["meta"]["roundNo"] == 2)
    assert second["messages"][-1]["content"][0]["input"]["action"] == "open_short"
    assert second["meta"]["finalAction"] == "hold"
    assert second["quality"]["gateVetoed"] is True


@pytest.mark.asyncio
async def test_missing_predecessor_excludes_the_sample_and_counts_it():
    """★ 放行条件 1：删掉第 1 行 ⇒ 第 3 轮样本被排除且 `skipped.contextIncomplete == 1`。"""
    await _seed_three_rounds()
    async with get_sessionmaker()() as session:
        await session.execute(delete(AiTradeDecisionLog)
                              .where(AiTradeDecisionLog.round_no == 1))
        await session.commit()
    samples, eof = await _samples()
    assert all(s["meta"]["roundNo"] != 3 for s in samples), "前驱缺席即重建不出来"
    assert eof["skipped"]["contextIncomplete"] >= 1


@pytest.mark.asyncio
async def test_declared_context_turns_mismatch_excludes_the_sample():
    """数得出来的不一致必须当作不一致——`contextTurns` 对不上一律排除。"""
    await _seed_three_rounds()
    async with get_sessionmaker()() as session:
        row = (await session.execute(
            __import__("sqlalchemy").select(AiTradeDecisionLog)
            .where(AiTradeDecisionLog.round_no == 3))).scalars().first()
        ref = json.loads(row.context_ref_json)
        ref["contextTurns"] = 99          # 出站那一刻记的条数与实际重建条数不符
        row.context_ref_json = json.dumps(ref)
        await session.commit()
    samples, eof = await _samples()
    assert all(s["meta"]["roundNo"] != 3 for s in samples)
    assert eof["skipped"]["contextIncomplete"] >= 1


@pytest.mark.asyncio
async def test_missing_summary_seed_profile_excludes_the_sample():
    """种子档案行找不到 ⇒ 第一条消息重建不出来 ⇒ 排除（不是「凑合导出一条少一句的」）。"""
    await _seed_three_rounds()
    async with get_sessionmaker()() as session:
        await session.execute(delete(AiTradePromptProfile)
                              .where(AiTradePromptProfile.sha256 == SEED_SHA))
        await session.commit()
    samples, eof = await _samples()
    assert samples == []
    assert eof["skipped"]["contextIncomplete"] == 3


@pytest.mark.asyncio
async def test_last_turn_only_is_an_explicit_exit_that_labels_itself():
    """`lastTurnOnly` 是显式出口：样本强制带 `quality.contextTruncated=true`。"""
    await _seed_three_rounds()
    samples, _eof = await _samples(SftOptions(exclude_gate_vetoed=False,
                                              context_mode="lastTurnOnly"))
    third = next(s for s in samples if s["meta"]["roundNo"] == 3)
    assert [m["role"] for m in third["messages"]] == ["system", "user", "assistant"]
    assert third["quality"]["contextTruncated"] is True
    assert third["meta"]["contextMode"] == "lastTurnOnly"


@pytest.mark.asyncio
async def test_context_chain_gate_off_forces_last_turn_only_and_says_so():
    """总闸关 ⇒ 只接受 `lastTurnOnly`，且每条样本强制 `contextTruncated=true`。

    **绝不静默按 `full` 走**：那会让每一条样本都掉进 `contextIncomplete`，
    用户看到的是一个没有解释的空文件。
    """
    await _seed_three_rounds()
    from app.api.ai_trade_journal import _sft_options_raw
    ctx = SftContext(context_chain_enabled=False, resolved_min_link_confidence="exact",
                     resolved_reason="explicit")
    opts = _sft_options_raw(False, True, True, False, "exact", "full", ctx)
    assert opts.context_mode == "lastTurnOnly"
    samples, _eof = await _samples(opts, ctx)
    assert samples and all(s["quality"]["contextTruncated"] for s in samples)
