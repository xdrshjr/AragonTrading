"""提示词的按需重建与 sha 判据（ai-trade-audit-journal §2.2.4 / §4.4）。

`shaMatch=false` **不是错误**（仍返回 200）：重建出来的文本仍有参考价值。但它必须被显著告知
——这句话直接决定用户会不会拿一批假样本去训练。`minimal` 档没有上下文可供重建 ⇒ 409。
"""
from __future__ import annotations

import json

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete

from app.db.models import AiTradeDecisionLog, AiTradeEventLog, AiTradePromptProfile
from app.db.session import get_sessionmaker, init_db

from .test_ai_trade_journal_api import BASE, _ORIGIN, decision_row


def _client() -> AsyncClient:
    from app.main import app

    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test",
                       headers=_ORIGIN)


async def _seed(row: AiTradeDecisionLog) -> int:
    await init_db()
    async with get_sessionmaker()() as session:
        for table in (AiTradeDecisionLog, AiTradeEventLog, AiTradePromptProfile):
            await session.execute(delete(table))
        session.add(row)
        await session.flush()
        row_id = row.id
        await session.commit()
    return row_id


@pytest.mark.asyncio
async def test_matching_sha_reports_sha_match_true():
    row_id = await _seed(decision_row(1))
    async with _client() as client:
        body = (await client.get(f"{BASE}/decisions/{row_id}/prompt")).json()
    assert body["shaMatch"] is True
    assert body["storedSha"] == body["renderedSha"]
    assert body["source"] == "rendered" and body["text"]


@pytest.mark.asyncio
async def test_drifted_sha_still_returns_200_but_says_it_drifted():
    """200 而不是 4xx：重建出来的文本仍有参考价值，但界面必须显著告知它不完全相同。"""
    row_id = await _seed(decision_row(1, prompt_sha256="deadbeef" * 8))
    async with _client() as client:
        res = await client.get(f"{BASE}/decisions/{row_id}/prompt")
    assert res.status_code == 200
    body = res.json()
    assert body["shaMatch"] is False
    assert body["storedSha"] != body["renderedSha"]


@pytest.mark.asyncio
async def test_stored_prompt_is_returned_verbatim_and_never_rerendered():
    """`full` 档存了含后缀的原文 ⇒ 直接用，不受渲染代码演进影响。"""
    row_id = await _seed(decision_row(1, capture_tier="full",
                                       prompt_text="ORIGINAL TEXT WITH RETRY HINT"))
    async with _client() as client:
        body = (await client.get(f"{BASE}/decisions/{row_id}/prompt")).json()
    assert body["source"] == "stored"
    assert body["text"] == "ORIGINAL TEXT WITH RETRY HINT"
    assert body["shaMatch"] is True


@pytest.mark.asyncio
async def test_minimal_tier_returns_409_with_a_typed_code():
    row_id = await _seed(decision_row(1, capture_tier="minimal", payload_json=None))
    async with _client() as client:
        res = await client.get(f"{BASE}/decisions/{row_id}/prompt")
    assert res.status_code == 409
    assert res.json()["error"]["code"] == "JOURNAL_PAYLOAD_ABSENT"


@pytest.mark.asyncio
async def test_missing_row_is_404():
    await _seed(decision_row(1))
    async with _client() as client:
        res = await client.get(f"{BASE}/decisions/88888888/prompt")
    assert res.status_code == 404


@pytest.mark.asyncio
async def test_detail_prompt_block_reports_its_source():
    """详情页第 4 段据 `source` 决定显示原文还是「按上下文即时重建」按钮。"""
    row_id = await _seed(decision_row(1))
    async with _client() as client:
        detail = (await client.get(f"{BASE}/decisions/{row_id}")).json()
    assert detail["prompt"]["source"] == "rebuildable"
    assert detail["prompt"]["storedSha"]

    row_id = await _seed(decision_row(2, capture_tier="minimal", payload_json=None))
    async with _client() as client:
        detail = (await client.get(f"{BASE}/decisions/{row_id}")).json()
    assert detail["prompt"] == {"source": "none"}


@pytest.mark.asyncio
async def test_rebuild_is_deterministic_for_the_same_payload():
    payload = {"lastPx": 100.0, "round": 1}
    row_id = await _seed(decision_row(1, payload_json=json.dumps(payload)))
    async with _client() as client:
        first = (await client.get(f"{BASE}/decisions/{row_id}/prompt")).json()
        second = (await client.get(f"{BASE}/decisions/{row_id}/prompt")).json()
    assert first["text"] == second["text"]


@pytest.mark.asyncio
@pytest.mark.parametrize("retry", [False, True])
async def test_frozen_advisory_rebuild_matches_actual_request_after_commit(retry):
    from copy import deepcopy
    from app.agent.llm.base import StreamEvent
    from app.config import Settings
    from .test_ai_trade_advisory_snapshot import (
        _commit_record, _snapshot_payload,
    )
    from .test_ai_trade_decider import _decider, _tool_events

    scripts = ([[StreamEvent("message_done")]] if retry else [])
    scripts.append(_tool_events({"action": "hold", "reason": "等待证据"}))
    decider = _decider(scripts, settings=Settings(
        ai_trade_decision_max_retries=int(retry),
        ai_trade_decision_retry_backoff_base_s=0))
    payload = _snapshot_payload()
    stored = deepcopy(payload)
    decision, _ = await decider.decide(payload)
    shape = decider.last_request_shape
    payload["intel"]["lines"][0] = "调用方事后改变"
    await decider.commit_round(payload, decision, {"ok": True},
                               record=_commit_record(stored))
    row_id = await _seed(decision_row(1,
        capture_tier="full" if retry else "standard",
        payload_json=json.dumps(stored, ensure_ascii=False),
        prompt_text=shape["finalUserText"] if retry else None,
        prompt_sha256=shape["finalUserSha256"]))
    async with _client() as client:
        rebuilt = (await client.get(
            f"{BASE}/decisions/{row_id}/prompt")).json()
    assert rebuilt["shaMatch"] is True
    assert rebuilt["text"] == shape["finalUserText"]
    assert "调用方事后改变" not in rebuilt["text"]
    assert decider._llm.calls == 1 + int(retry)
    assert "上一次未产出" not in decider._context[-3].content[0].text


@pytest.mark.asyncio
async def test_summary_seed_then_retry_preserves_journal_request_identity():
    from app.ai_auto.decision_memory import EVIDENCE_VERSION
    from app.agent.llm.base import StreamEvent
    from app.config import Settings
    from .test_ai_trade_advisory_snapshot import (
        _commit_record, _snapshot_payload,
    )
    from .test_ai_trade_decider import _decider, _tool_events
    decider = _decider([
        _tool_events({"action": "hold"}),
        [StreamEvent("text_delta", text="没有事后结算，判断待验证"),
         StreamEvent("message_done")],
        [StreamEvent("message_done")],
        _tool_events({"action": "hold"}),
    ], rounds=1, settings=Settings(ai_trade_decision_max_retries=1,
        ai_trade_decision_retry_backoff_base_s=0))
    first = _snapshot_payload()
    decision, _ = await decider.decide(first)
    await decider.commit_round(first, decision, {"ok": True},
                               record=_commit_record(first))
    assert decider.memory_wire()["summaryEvidenceVersion"] == EVIDENCE_VERSION
    second = _snapshot_payload(2)
    second["summary"] = decider.summary
    decision, _ = await decider.decide(second)
    shape = decider.last_request_shape
    await decider.commit_round(second, decision, {"ok": True},
                               record=_commit_record(second), defer_summary=True)
    row_id = await _seed(decision_row(2, capture_tier="full",
        payload_json=json.dumps(decider.frozen_round_payload(second)),
        prompt_text=shape["finalUserText"],
        prompt_sha256=shape["finalUserSha256"],
        context_ref_json=json.dumps(shape)))
    async with _client() as client:
        rebuilt = (await client.get(
            f"{BASE}/decisions/{row_id}/prompt")).json()
    assert rebuilt["shaMatch"] is True
    assert rebuilt["text"] == shape["finalUserText"]
    assert shape["summarySha256"]
    assert "没有事后结算" in shape["summaryText"]
    assert decider._llm.calls == 4
