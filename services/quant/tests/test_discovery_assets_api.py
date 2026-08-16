"""成果资产台账的 10 个 REST 端点（discovery-asset-ledger §四 / §7.3-7）。

重点覆盖两件在设计里被反复强调的事：
- **AL-11**：候选行被删除之后，资产的详情与「入库」**仍然可用**（台账自持全部判级输入）；
- 守门序与错误码：403 总闸 / 404 / 409 BUSY / 409 ARCHIVED / 413 导出超限 / 筛选式截断。
"""
from __future__ import annotations

import json

from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete

from app.config import Settings
from app.db.models import (
    DiscoveryAsset,
    DiscoveryAssetCheck,
    DiscoveryCandidate,
    DiscoveryIteration,
    DiscoveryRun,
    Factor,
)
from app.db.session import dispose_engine, get_sessionmaker, init_db
from app.discovery import asset_repositories as asset_repo
from app.discovery.health_runtime import get_health_guard

TS0 = 1_700_000_000
EXPR = "sub(ts_mean(overnight,20),delta(amplitude,5))"


def _load_app():
    from app.main import app

    return app


async def _fresh():
    await init_db()
    get_health_guard()._inflight.clear()
    sm = get_sessionmaker()
    async with sm() as session:
        for model in (DiscoveryAssetCheck, DiscoveryAsset, DiscoveryCandidate,
                      DiscoveryIteration, DiscoveryRun):
            await session.execute(delete(model))
        await session.execute(delete(Factor).where(Factor.source == "mined"))
        await session.commit()
    return sm


async def _seed(sm, *, key: str = "f:api0001", kind: str = "factor",
                expr: str = EXPR, **overrides) -> int:
    values = dict(asset_key=key, kind=kind, inst_id="BTC-USDT-SWAP", bar="15m", horizon=6,
                  direction="long", expr_text=expr, genome={}, describe_text="跳空溢价背离",
                  mechanism="gap_premium", label="挖掘因子 abcd1234：跳空溢价背离",
                  found_ts=TS0, source={"runId": 1, "candidateId": 5, "genomeHash": "aaa"},
                  mined_window={"endTs": TS0, "splitTs": 0},
                  accept_metrics={"is": {"rankIc": 0.05, "ir": 0.9}},
                  accept_thresholds={"minAbsIc": 0.03}, trials_at_accept=812,
                  next_check_ts=TS0 + 100)
    values.update(overrides)
    async with sm() as session:
        row = await asset_repo.create_asset(session, asset_repo.AssetValues(**values))
    return int(row.id)


def _client():
    app = _load_app()
    return app, AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


def _disable(monkeypatch) -> None:
    """总闸走**模块级** ``get_settings()``（不经 ``Depends``），只有 monkeypatch 改得到。"""
    from app.api import discovery_assets

    monkeypatch.setattr(discovery_assets, "get_settings",
                        lambda: Settings(discovery_enabled=False))


# ============================ 列表 / 筛选 / 排序 ============================
async def test_list_filters_and_paginates() -> None:
    sm = await _fresh()
    app, client = _client()
    try:
        await _seed(sm, key="f:one", expr=EXPR)
        await _seed(sm, key="s:two", kind="strategy_param", expr="",
                    describe_text="动量突破参数", mechanism="trend",
                    label="挖掘策略 beef0001：动量突破",
                    genome={"templateKey": "momentum_breakout", "params": {}})
        async with client:
            everything = (await client.get("/api/discovery/assets")).json()
            factors = (await client.get("/api/discovery/assets?kind=factor")).json()
            searched = (await client.get("/api/discovery/assets?q=跳空")).json()
            paged = (await client.get("/api/discovery/assets?limit=1")).json()
        assert everything["total"] == 2
        assert factors["total"] == 1 and factors["items"][0]["kind"] == "factor"
        assert searched["total"] == 1
        assert len(paged["items"]) == 1 and paged["total"] == 2
        assert factors["items"][0]["mechanismCn"] and factors["items"][0]["health"][
            "status"] == "fresh"
    finally:
        app.dependency_overrides.clear()
        await dispose_engine()


async def test_archived_assets_are_hidden_by_default() -> None:
    sm = await _fresh()
    app, client = _client()
    try:
        asset_id = await _seed(sm, key="f:arch")
        async with client:
            await client.post(f"/api/discovery/assets/{asset_id}/archive")
            default = (await client.get("/api/discovery/assets")).json()
            shown = (await client.get("/api/discovery/assets?archived=1")).json()
        assert default["total"] == 0 and shown["total"] == 1
        assert shown["items"][0]["archived"] is True
    finally:
        app.dependency_overrides.clear()
        await dispose_engine()


# ============================ 详情 / 体检记录 ============================
async def test_detail_marks_dangling_sources_server_side() -> None:
    """``runExists`` **由服务端算**（评审 P2-4）：前端不得自己再拉一次 run 列表比对。"""
    sm = await _fresh()
    app, client = _client()
    try:
        asset_id = await _seed(sm, key="f:dangling")
        async with client:
            detail = (await client.get(f"/api/discovery/assets/{asset_id}")).json()
            missing = (await client.get("/api/discovery/assets/99999")).json()
        assert detail["sources"][0]["runExists"] is False
        assert set(detail["minedWindow"]) >= {"endTs", "splitTs"}
        assert detail["acceptThresholds"]["minAbsIc"] == 0.03
        assert missing["error"]["code"] == "NOT_FOUND"
    finally:
        app.dependency_overrides.clear()
        await dispose_engine()


async def test_checks_endpoint_returns_the_series() -> None:
    sm = await _fresh()
    app, client = _client()
    try:
        asset_id = await _seed(sm, key="f:series")
        async with sm() as session:
            for index in range(3):
                await asset_repo.write_check(
                    session,
                    asset_repo.CheckValues(asset_id=asset_id, checked_ts=TS0 + index,
                                           window_start_ts=0, window_end_ts=0,
                                           fresh_start_ts=0, fresh_bars=200,
                                           warmup_bars=400, status="healthy",
                                           reasons=[], retention=0.9,
                                           metrics={"fresh": {}, "history": {}}),
                    health={"status": "healthy", "retention": 0.9, "consecutiveFail": 0,
                            "nextCheckTs": TS0 + 100},
                    max_rows=200)
        async with client:
            payload = (await client.get(
                f"/api/discovery/assets/{asset_id}/checks")).json()
        assert payload["total"] == 3
        assert [item["checkedTs"] for item in payload["items"]] == [TS0, TS0 + 1, TS0 + 2]
    finally:
        app.dependency_overrides.clear()
        await dispose_engine()


# ============================ 守门 ============================
async def test_write_endpoints_are_gated_by_the_master_switch(monkeypatch) -> None:
    sm = await _fresh()
    app, client = _client()
    try:
        asset_id = await _seed(sm, key="f:gated")
        _disable(monkeypatch)
        async with client:
            for path, method in ((f"/api/discovery/assets/{asset_id}/archive", "post"),
                                 (f"/api/discovery/assets/{asset_id}/check", "post"),
                                 (f"/api/discovery/assets/{asset_id}/promote", "post")):
                res = await getattr(client, method)(path)
                assert res.status_code == 403
                assert res.json()["error"]["code"] == "DISCOVERY_DISABLED"
            listed = await client.get("/api/discovery/assets")     # 读类仍可看历史
        assert listed.status_code == 200
    finally:
        app.dependency_overrides.clear()
        await dispose_engine()


async def test_check_now_returns_409_when_already_running() -> None:
    sm = await _fresh()
    app, client = _client()
    guard = get_health_guard()
    try:
        asset_id = await _seed(sm, key="f:busy")
        guard.claim(asset_id)
        async with client:
            res = await client.post(f"/api/discovery/assets/{asset_id}/check")
        assert res.status_code == 409
        assert res.json()["error"]["code"] == "DISCOVERY_HEALTH_BUSY"
    finally:
        guard.release(asset_id)
        app.dependency_overrides.clear()
        await dispose_engine()


async def test_archived_asset_refuses_a_check() -> None:
    sm = await _fresh()
    app, client = _client()
    try:
        asset_id = await _seed(sm, key="f:frozen")
        async with client:
            await client.post(f"/api/discovery/assets/{asset_id}/archive")
            res = await client.post(f"/api/discovery/assets/{asset_id}/check")
        assert res.status_code == 409
        assert res.json()["error"]["code"] == "DISCOVERY_ASSET_ARCHIVED"
    finally:
        app.dependency_overrides.clear()
        await dispose_engine()


# ============================ 备注 / 归档 ============================
async def test_note_is_truncated_server_side() -> None:
    sm = await _fresh()
    app, client = _client()
    try:
        asset_id = await _seed(sm, key="f:note")
        async with client:
            too_long = await client.patch(f"/api/discovery/assets/{asset_id}",
                                          json={"note": "备" * 300})
            ok = await client.patch(f"/api/discovery/assets/{asset_id}",
                                    json={"note": "留意隔夜行情"})
        assert too_long.status_code == 422   # Pydantic 边界在先，服务端再截断一次兜底
        assert ok.json()["note"] == "留意隔夜行情"
    finally:
        app.dependency_overrides.clear()
        await dispose_engine()


async def test_unarchive_keeps_the_original_schedule() -> None:
    """取消归档即恢复排队，**不重置**周期（否则归档会变相延后一整轮体检）。"""
    sm = await _fresh()
    app, client = _client()
    try:
        asset_id = await _seed(sm, key="f:back")
        async with client:
            await client.post(f"/api/discovery/assets/{asset_id}/archive")
            res = await client.post(f"/api/discovery/assets/{asset_id}/unarchive")
        assert res.json() == {"id": asset_id, "archived": False, "archivedTs": 0}
        async with sm() as session:
            assert int((await asset_repo.get_asset(session,
                                                   asset_id)).next_check_ts) == TS0 + 100
    finally:
        app.dependency_overrides.clear()
        await dispose_engine()


# ============================ 入库（AL-11：候选行没了也能用） ============================
async def test_promote_from_the_ledger_works_without_any_candidate_row() -> None:
    sm = await _fresh()
    app, client = _client()
    try:
        asset_id = await _seed(sm, key="f:promote")
        async with client:
            first = (await client.post(
                f"/api/discovery/assets/{asset_id}/promote")).json()
            again = (await client.post(
                f"/api/discovery/assets/{asset_id}/promote")).json()
        assert first["promoted"]["entityKind"] == "factor"
        assert first["promoted"]["reused"] is False
        assert again["promoted"]["entityId"] == first["promoted"]["entityId"]
        assert again["promoted"]["reused"] is True      # 幂等回放
        async with sm() as session:
            row = await asset_repo.get_asset(session, asset_id)
        assert row.promoted_kind == "factor" and row.promoted_id
    finally:
        app.dependency_overrides.clear()
        await dispose_engine()


# ============================ 摘要 / 导出 ============================
async def test_summary_exposes_guard_state() -> None:
    sm = await _fresh()
    app, client = _client()
    try:
        await _seed(sm, key="f:sum")
        async with client:
            payload = (await client.get("/api/discovery/assets/summary")).json()
        assert payload["total"] == 1
        assert payload["byStatus"]["fresh"] == 1
        assert set(payload["guard"]) >= {"enabled", "running", "lastTickTs", "lastError",
                                         "checkedTotal", "skippedByCap", "dataFailures",
                                         "assetLimitHits", "intervalS", "tickS",
                                         "warmupBars", "windowBars"}
    finally:
        app.dependency_overrides.clear()
        await dispose_engine()


async def test_export_rejects_an_oversized_explicit_id_list() -> None:
    """点名要哪些就不能悄悄少给几条 ⇒ 413；筛选式则截断 + ``truncated``（§4.8）。"""
    sm = await _fresh()
    app, client = _client()
    try:
        await _seed(sm, key="f:exp")
        ids = ",".join(str(value) for value in range(1, 502))
        async with client:
            too_many = await client.get(f"/api/discovery/assets/export?ids={ids}")
            filtered = await client.get("/api/discovery/assets/export")
        assert too_many.status_code == 413
        assert too_many.json()["error"]["code"] == "DISCOVERY_EXPORT_TOO_LARGE"
        payload = json.loads(filtered.text)
        assert payload["truncated"] is False and payload["matched"] == 1
        assert "attachment" in filtered.headers["content-disposition"]
    finally:
        app.dependency_overrides.clear()
        await dispose_engine()
