"""交易日志的 10 个端点（ai-trade-audit-journal §四）。

判据落在 **HTTP 响应体**上而不是仓储函数的返回值：本仓库为「后端算了、前端永远读不到」这条
栽过（FastAPI 会静默丢弃未在 `response_model` 里声明的键）。只断言仓库函数的用例对那类缺陷
完全不敏感。
"""
from __future__ import annotations

import hashlib
import json
import time

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete

from app.db.models import (
    AiTradeDecisionLog,
    AiTradeEventLog,
    AiTradePromptProfile,
    Strategy,
    StrategyRun,
)
from app.db.session import get_sessionmaker, init_db

NOW = int(time.time())
INST = "BTC-USDT-SWAP"
GROUP = "grp-journal-api"
_ORIGIN = {"Origin": "http://localhost:18761"}
BASE = "/api/ai-trade/journal"


def _client() -> AsyncClient:
    from app.main import app

    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test",
                       headers=_ORIGIN)


async def _fresh() -> None:
    await init_db()
    async with get_sessionmaker()() as session:
        for table in (AiTradeDecisionLog, AiTradeEventLog, AiTradePromptProfile):
            await session.execute(delete(table))
        await session.commit()


def prompt_sha_of(payload: dict) -> str:
    """按当前渲染代码算出该 payload 的提示词 sha（夹具里的 `prompt_sha256` 必须是**真的**）。

    写一个假 sha 会让每一条样本都掉进 `promptDrift` 桶——那是导出器的正确行为，
    但会让本文件的用例在一条与它无关的判据上恒红。
    """
    from app.ai_auto.prompts import build_decision_prompt
    return hashlib.sha256(build_decision_prompt(payload).encode("utf-8")).hexdigest()


def decision_row(round_no: int, **over) -> AiTradeDecisionLog:
    """一条可直接入库的决策行（默认是「模型开多、成交、standard 档」）。"""
    payload = {"lastPx": 100.0, "round": round_no}
    base = dict(
        schema_version=1, group_id=GROUP, run_id=1, inst_id=INST, bar="15m",
        round_no=round_no, ts=NOW - (100 - round_no) * 60, wall_ts=NOW, trigger="interval",
        action="open_long", final_action="open_long", size_fraction=0.5, confidence=0.72,
        ord_type="market", limit_px=None, degrade_reason=None, reflection_verdict="confirm",
        reason=f"round {round_no}", executed=True, cl_ord_id=f"cl-{round_no}", ord_id="",
        fill_ref_json=json.dumps({"traded": True, "legs": [
            {"side": "long", "px": 100.0, "sz": 1.0, "fee": 0.1, "pnl": 0.0,
             "clOrdId": f"cl-{round_no}", "source": "round"}]}),
        profile_sha256="p" * 64, prompt_sha256=prompt_sha_of(payload),
        payload_json=json.dumps(payload),
        payload_truncated=False, prompt_text=None,
        output_json=json.dumps({"action": "open_long", "sizeFraction": 0.5}),
        context_ref_json=json.dumps({"contextTurns": 0,
                                     "contextFirstRound": round_no,
                                     "summarySha256": "",
                                     "finalUserSha256": prompt_sha_of(payload)}),
        context_turn_json=json.dumps({"assistant": {"action": "open_long"},
                                      "toolResult": "{}"}),
        analysis_text="analysis", reflection_text=None, thinking_text=None,
        gates_json=json.dumps({"schema": 1, "vetoedBy": None, "layers": []}),
        telemetry_json=json.dumps({"provider": "anthropic", "model": "claude-opus-5"}),
        capture_tier="standard", created_ts=NOW)
    base.update(over)
    return AiTradeDecisionLog(**base)


async def _seed(rounds: int = 3) -> None:
    async with get_sessionmaker()() as session:
        session.add(AiTradePromptProfile(sha256="p" * 64, kind="profile",
                                         system_prompt="SYS", tools_json="[]",
                                         provider="anthropic", model="claude-opus-5",
                                         first_group_id=GROUP, created_ts=NOW))
        for index in range(1, rounds + 1):
            session.add(decision_row(index))
        session.add(AiTradeEventLog(group_id=GROUP, run_id=1, inst_id=INST, ts=NOW,
                                    wall_ts=NOW, level="info", category="lifecycle",
                                    code="GROUP_STARTED", message="已启动", created_ts=NOW))
        await session.commit()


@pytest.mark.asyncio
async def test_decisions_list_is_keyset_paged_and_never_counts():
    await _fresh()
    await _seed(5)
    async with _client() as client:
        first = await client.get(f"{BASE}/decisions", params={"groupId": GROUP, "limit": 2})
        assert first.status_code == 200
        body = first.json()
        assert len(body["items"]) == 2
        assert body["total"] is None, "列表页恒不做 COUNT（十万行上那是一次全扫）"
        assert body["nextBeforeId"] is not None
        second = await client.get(f"{BASE}/decisions", params={
            "groupId": GROUP, "limit": 2, "beforeId": body["nextBeforeId"]})
        ids_first = {row["id"] for row in body["items"]}
        ids_second = {row["id"] for row in second.json()["items"]}
        assert ids_first & ids_second == set(), "两页之间无重叠"


@pytest.mark.asyncio
async def test_decision_detail_has_all_seven_sections():
    await _fresh()
    await _seed(1)
    async with _client() as client:
        listed = (await client.get(f"{BASE}/decisions", params={"groupId": GROUP})).json()
        row_id = listed["items"][0]["id"]
        detail = (await client.get(f"{BASE}/decisions/{row_id}")).json()
    for key in ("brief", "gates", "payload", "output", "analysis", "prompt",
                "telemetry", "outcome"):
        assert key in detail, f"详情缺少第 {key} 段"
    assert detail["outcome"]["linkConfidence"] in ("exact", "heuristic", "none")


@pytest.mark.asyncio
async def test_missing_decision_returns_a_typed_404():
    await _fresh()
    async with _client() as client:
        res = await client.get(f"{BASE}/decisions/99999999")
    assert res.status_code == 404
    assert res.json()["error"]["code"] == "JOURNAL_DECISION_NOT_FOUND"


@pytest.mark.asyncio
async def test_overlong_q_is_rejected_at_the_framework_boundary():
    """`q` 进的是 SQL LIKE；无界关键字会把十万行上的扫描拖成分钟级。"""
    await _fresh()
    async with _client() as client:
        res = await client.get(f"{BASE}/decisions", params={"q": "x" * 65})
    assert res.status_code == 422


@pytest.mark.asyncio
async def test_events_and_fills_and_stats_are_reachable():
    await _fresh()
    await _seed(2)
    async with _client() as client:
        events = (await client.get(f"{BASE}/events", params={"groupId": GROUP})).json()
        fills = (await client.get(f"{BASE}/fills", params={"groupId": GROUP})).json()
        stats = (await client.get(f"{BASE}/stats")).json()
    assert events["items"][0]["code"] == "GROUP_STARTED"
    assert fills["items"], "已成交轮必须出现在成交与订单页"
    assert fills["items"][0]["decisionRef"]["roundNo"] in (1, 2), "成交找得回决策"
    for key in ("enabled", "captureTier", "queue", "counters", "tables", "retention",
                "limits"):
        assert key in stats, f"stats 缺少 {key}——前端的口径数字全部来自它"


@pytest.mark.asyncio
async def test_sessions_endpoint_lists_the_running_group_too():
    """与 `/ai-trade/history` 的区别是硬性的：日志台必须能看正在跑的那一个。"""
    await _fresh()
    await _seed(1)
    async with get_sessionmaker()() as session:
        # 本文件与别的用例共用一个临时库，故两条纪律：**绝不指定自增主键**（写死 `id=1` 会在
        # 别的用例先插过一行时撞 UNIQUE），**也绝不删 `strategy_runs` 的任何行**——
        # `INTEGER PRIMARY KEY` 在删掉最大 id 之后会复用它，而复用会让后面某个用例的新 run
        # 读到一段属于已删 run 的净值序列（症状离现场极远、单独跑又是绿的）。
        strategy = Strategy(template_key="ai_trade", name="j", params_json="{}")
        session.add(strategy)
        await session.flush()
        session.add(StrategyRun(strategy_id=strategy.id, inst_id=INST, bar="15m",
                                mode="paper", status="running", start_ts=NOW - 3600,
                                group_id=GROUP, config_json="{}"))
        await session.commit()
    async with _client() as client:
        body = (await client.get(f"{BASE}/sessions")).json()
    row = next((item for item in body["items"] if item["groupId"] == GROUP), None)
    assert row is not None and row["status"] == "running"
    assert row["decisionRows"] == 1


@pytest.mark.asyncio
async def test_export_preview_returns_the_nine_skipped_buckets():
    await _fresh()
    await _seed(3)
    async with _client() as client:
        res = await client.post(f"{BASE}/export/preview", json={
            "format": "sft-jsonl", "scope": {"groupId": GROUP}, "sft": {}})
    body = res.json()
    assert res.status_code == 200
    assert set(body["skipped"]) == {
        "gateVetoed", "degraded", "unsettled", "hold", "lowConfidence", "promptDrift",
        "payloadTruncated", "minimalTier", "contextIncomplete"}
    assert body["resolvedMinLinkConfidence"] in ("exact", "heuristic", "none")
    assert body["slices"] >= 1 and body["timeBudgetS"] >= 0


@pytest.mark.asyncio
async def test_unknown_export_format_is_422():
    await _fresh()
    async with _client() as client:
        res = await client.post(f"{BASE}/export/preview", json={"format": "parquet"})
    assert res.status_code == 422
    assert res.json()["error"]["code"] == "JOURNAL_FORMAT_INVALID"


@pytest.mark.asyncio
async def test_prune_defaults_to_dry_run():
    """一个默认就删的删除端点是不可接受的。"""
    await _fresh()
    await _seed(2)
    async with _client() as client:
        res = await client.post(f"{BASE}/prune", json={})
    body = res.json()
    assert res.status_code == 200
    assert body["deleted"] is None and body["wouldDelete"] is not None


@pytest.mark.asyncio
async def test_export_streams_csv_with_bom_and_headers():
    await _fresh()
    await _seed(2)
    async with _client() as client:
        res = await client.get(f"{BASE}/export", params={"format": "audit-csv",
                                                         "groupId": GROUP})
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("text/csv")
    assert "attachment; filename=" in res.headers["content-disposition"]
    assert res.text.startswith("﻿")


@pytest.mark.asyncio
async def test_routes_are_absent_when_the_ui_gate_is_off(monkeypatch):
    """总闸关 ⇒ **整个路由不注册** ⇒ 404，而不是注册一个恒回空的端点。

    路由表在 import 期按 `get_settings()` 装配，故这里断言的是**装配判据**本身：
    `main.py` 里那个 `if` 读的就是这个键，且它今天为真（默认档）。
    """
    import pathlib
    main_src = (pathlib.Path(__file__).resolve().parents[1] / "app" / "main.py").read_text(
        encoding="utf-8")
    assert "if get_settings().ai_trade_journal_ui_enabled:" in main_src
    assert "app.include_router(ai_trade_journal.router" in main_src
    from app.main import app
    paths = {route.path for route in app.routes if "journal" in getattr(route, "path", "")}
    assert len(paths) == 10, f"应为 9 个只读 + 1 个剪枝，实得 {sorted(paths)}"


@pytest.mark.asyncio
async def test_health_declares_the_journal_gate():
    """`/health` 未声明 `response_model`，故加法键不会被静默剔除。"""
    async with _client() as client:
        body = (await client.get("/api/health")).json()
    assert "journalEnabled" in body
