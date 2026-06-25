from __future__ import annotations

import json

import pandas as pd
import pytest
from httpx import ASGITransport, AsyncClient

from app.okx.types import Candle
from tests.conftest import make_synthetic_candles


@pytest.fixture(autouse=True)
async def clean_manager():
    from app.paper import manager as manager_mod

    if manager_mod._manager is not None:
        await manager_mod._manager.shutdown()
    manager_mod._manager = None
    yield
    if manager_mod._manager is not None:
        await manager_mod._manager.shutdown()
    manager_mod._manager = None


@pytest.fixture
def patch_paper_session(monkeypatch):
    async def fake_start(self):
        return None

    async def fake_stop(self):
        self.state = "stopped"
        await self._persist_final()

    monkeypatch.setattr("app.paper.session.PaperSession.start", fake_start)
    monkeypatch.setattr("app.paper.session.PaperSession.stop", fake_stop)


async def test_paper_start_accepts_saved_combo(patch_paper_session):
    from app.db import repositories as repo
    from app.db.session import dispose_engine, get_sessionmaker, init_db
    from app.paper.manager import get_paper_manager

    await init_db()
    transport = ASGITransport(app=_load_app())
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            combo = await _create_combo(client)
            response = await client.post(
                "/api/paper/start",
                json={**_single_body(), "strategyId": combo["id"]},
            )

            assert response.status_code == 200, response.text
            body = response.json()
            rows = get_paper_manager().list_sessions()
            assert rows[0]["strategyId"] == combo["id"]
            assert rows[0]["strategyName"] == "Combo Paper"
            assert rows[0]["strategyKind"] == "combo"

            sm = get_sessionmaker()
            async with sm() as session:
                run = await repo.get_run(session, body["runId"])
            assert run is not None
            assert run.mode == "paper"
            config = json.loads(run.config_json)
            assert config["strategyKind"] == "combo"
            assert config["combo"] is True
            assert config["liveComboVersion"] == 1
    finally:
        await dispose_engine()


async def test_paper_batch_start_accepts_atomic_and_combo(patch_paper_session):
    from app.db.session import dispose_engine, init_db

    await init_db()
    transport = ASGITransport(app=_load_app())
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            strategies = (await client.get("/api/strategies")).json()
            atomic_id = next(item["id"] for item in strategies if item.get("kind") != "combo")
            combo = await _create_combo(client)

            response = await client.post(
                "/api/paper/batch/start",
                json={**_batch_body([atomic_id, combo["id"]])},
            )

            assert response.status_code == 200, response.text
            sessions = response.json()["sessions"]
            assert [item["strategyId"] for item in sessions] == [atomic_id, combo["id"]]
            assert [item["strategyKind"] for item in sessions] == ["atomic", "combo"]
    finally:
        await dispose_engine()


async def test_paper_start_rejects_invalid_combo_before_creating_session(
    patch_paper_session,
):
    from app.db.session import dispose_engine, init_db
    from app.paper.manager import get_paper_manager

    await init_db()
    transport = ASGITransport(app=_load_app())
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            combo = await _create_combo(
                client,
                members=[
                    {"templateKey": "trend_pullback_long", "params": {}},
                    {"templateKey": "missing_member", "params": {}},
                    {"templateKey": "range_scalping_strategy", "params": {}},
                ],
            )

            response = await client.post(
                "/api/paper/start",
                json={**_single_body(), "strategyId": combo["id"]},
            )

            assert response.status_code == 400
            assert response.json()["error"]["code"] == "UNAVAILABLE"
            assert get_paper_manager().list_sessions() == []
    finally:
        await dispose_engine()


async def test_paper_combo_window_allows_lookback_1000(patch_paper_session):
    from app.db.session import dispose_engine, init_db
    from app.paper.manager import get_paper_manager
    from app.paper.session import PAPER_WINDOW_MAX
    from app.schemas import PaperBatchStartReq, PaperStartReq

    assert PAPER_WINDOW_MAX == 1200
    assert PaperStartReq(**{**_single_body(), "replayBars": 1000, "primingBars": 1000})
    assert PaperBatchStartReq(**{**_batch_body([1]), "replayBars": 1000, "primingBars": 1000})

    await init_db()
    transport = ASGITransport(app=_load_app())
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            combo = await _create_combo(
                client,
                policy={"lookbackBars": 1000, "rebalanceBars": 24},
            )

            response = await client.post(
                "/api/paper/start",
                json={
                    **_single_body(),
                    "strategyId": combo["id"],
                    "replayBars": 1000,
                    "primingBars": 1000,
                },
            )

            assert response.status_code == 200, response.text
            session = get_paper_manager().get(response.json()["sessionId"])
            assert session is not None
            assert session._candles.maxlen >= 1005
    finally:
        await dispose_engine()


async def test_paper_combo_window_rejects_oversized_warmup(patch_paper_session):
    from app.db.session import dispose_engine, init_db
    from app.paper.manager import get_paper_manager

    await init_db()
    transport = ASGITransport(app=_load_app())
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            combo = await _create_combo(
                client,
                policy={"lookbackBars": 1196, "rebalanceBars": 24},
            )

            response = await client.post(
                "/api/paper/start",
                json={**_single_body(), "strategyId": combo["id"]},
            )

            assert response.status_code == 400
            assert response.json()["error"]["code"] == "COMBO_WINDOW"
            assert get_paper_manager().list_sessions() == []
    finally:
        await dispose_engine()


async def test_combo_paper_stop_finalizes_run(patch_paper_session):
    from app.db import repositories as repo
    from app.db.session import dispose_engine, get_sessionmaker, init_db

    await init_db()
    transport = ASGITransport(app=_load_app())
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            combo = await _create_combo(client)
            start = await client.post(
                "/api/paper/start",
                json={**_single_body(), "strategyId": combo["id"]},
            )
            assert start.status_code == 200, start.text

            response = await client.post(
                f"/api/paper/{start.json()['sessionId']}/stop",
            )

            assert response.status_code == 200, response.text
            body = response.json()
            assert body["strategyId"] == combo["id"]
            assert body["status"] == "stopped"
            assert "totalReturn" in body["metrics"]

            sm = get_sessionmaker()
            async with sm() as session:
                run = await repo.get_run(session, start.json()["runId"])
            assert run is not None
            assert run.status == "stopped"
    finally:
        await dispose_engine()


def test_live_combo_strategy_does_not_call_combination_on_candle():
    from app.engine.backtester import candles_to_df
    from app.paper.live_combo import LiveComboStrategy
    from app.strategies.combo_specs import build_members, policy_from_spec

    rows = make_synthetic_candles(260)
    df = candles_to_df([Candle(**row) for row in rows])
    members = build_members(_members(), [], strict=True)
    strategy = LiveComboStrategy(
        members=members,
        policy=policy_from_spec({"lookbackBars": 48, "rebalanceBars": 12}),
        policy_spec={"lookbackBars": 48, "rebalanceBars": 12},
        members_spec=_members(),
    )

    prepared = strategy.prepare(df, "1H")
    signals = strategy.on_candle(prepared, len(prepared) - 2, _FlatContext())

    assert isinstance(signals, list)


def _members() -> list[dict]:
    return [
        {"templateKey": "trend_pullback_long", "params": {}},
        {"templateKey": "range_scalping_strategy", "params": {}},
    ]


async def _create_combo(
    client: AsyncClient,
    *,
    members: list[dict] | None = None,
    policy: dict | None = None,
) -> dict:
    response = await client.post(
        "/api/strategies",
        json={
            "templateKey": "regime_rotation",
            "name": "Combo Paper",
            "params": {
                "members": members or _members(),
                "selectionMetric": "sharpe",
                "rebalanceBars": 24,
                "lookbackBars": 168,
                "minDwellBars": 24,
                "switchMarginPct": 0.1,
                "regimeGating": True,
                "turnoverBps": 5,
                **(policy or {}),
            },
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def _single_body() -> dict:
    return {
        "strategyId": 1,
        "instId": "BTC-USDT-SWAP",
        "bar": "1H",
        "initialCapital": 10000,
        "source": "live",
        "speed": 1,
        "replayBars": 10,
        "priming": False,
        "primingBars": 10,
    }


def _batch_body(strategy_ids: list[int]) -> dict:
    return {
        "strategyIds": strategy_ids,
        "instId": "BTC-USDT-SWAP",
        "bar": "1H",
        "initialCapital": 10000,
        "source": "live",
        "speed": 1,
        "replayBars": 10,
        "priming": False,
        "primingBars": 10,
    }


def _load_app():
    from app.main import app

    return app


class _FlatContext:
    position_side = "flat"
    bars_in_position = 0
    entry_price = 0.0
    equity = 10000.0
