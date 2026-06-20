from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient


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


async def test_batch_start_respects_session_limit(monkeypatch, patch_paper_session):
    from app.config import get_settings
    from app.db.session import dispose_engine, init_db

    get_settings.cache_clear()
    monkeypatch.setenv("MAX_PAPER_SESSIONS", "2")
    await init_db()
    transport = ASGITransport(app=_load_app())
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            strategies = (await client.get("/api/strategies")).json()
            ids = [item["id"] for item in strategies[:3]]
            response = await client.post("/api/paper/batch/start", json=_body(ids))

            assert response.status_code == 429
            assert response.json()["error"]["code"] == "SESSION_LIMIT"
    finally:
        get_settings.cache_clear()
        await dispose_engine()


async def test_batch_start_respects_existing_running_sessions(monkeypatch, patch_paper_session):
    from app.config import get_settings
    from app.db.session import dispose_engine, init_db
    from app.paper.manager import get_paper_manager

    get_settings.cache_clear()
    monkeypatch.setenv("MAX_PAPER_SESSIONS", "2")
    await init_db()
    transport = ASGITransport(app=_load_app())
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            strategies = (await client.get("/api/strategies")).json()
            first_id = strategies[0]["id"]
            next_ids = [item["id"] for item in strategies[1:3]]

            existing = await client.post(
                "/api/paper/start",
                json={**_body([first_id]), "strategyId": first_id},
            )
            assert existing.status_code == 200, existing.text

            response = await client.post("/api/paper/batch/start", json=_body(next_ids))

            assert response.status_code == 429
            running = [
                item for item in get_paper_manager().list_sessions()
                if item["state"] == "running"
            ]
            assert len(running) == 1
    finally:
        get_settings.cache_clear()
        await dispose_engine()


async def test_batch_start_rejects_unknown_strategy_before_starting_any_session(
    patch_paper_session,
):
    from app.db.session import dispose_engine, init_db
    from app.paper.manager import get_paper_manager

    await init_db()
    transport = ASGITransport(app=_load_app())
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            sid = (await client.get("/api/strategies")).json()[0]["id"]
            response = await client.post("/api/paper/batch/start", json=_body([sid, 999999]))

            assert response.status_code == 404
            assert get_paper_manager().list_sessions() == []
    finally:
        await dispose_engine()


async def test_batch_start_creates_one_run_per_strategy(patch_paper_session):
    from app.db import repositories as repo
    from app.db.session import dispose_engine, get_sessionmaker, init_db

    await init_db()
    transport = ASGITransport(app=_load_app())
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            ids = [item["id"] for item in (await client.get("/api/strategies")).json()[:2]]
            response = await client.post("/api/paper/batch/start", json=_body(ids))

            assert response.status_code == 200, response.text
            body = response.json()
            assert body["groupId"]
            assert [item["strategyId"] for item in body["sessions"]] == ids
            assert all(item["state"] == "running" for item in body["sessions"])

            sm = get_sessionmaker()
            async with sm() as session:
                runs = [await repo.get_run(session, item["runId"]) for item in body["sessions"]]
            assert [run.strategy_id for run in runs if run is not None] == ids
            assert all(run and run.mode == "paper" for run in runs)
    finally:
        await dispose_engine()


async def test_batch_start_rolls_back_partial_failure(monkeypatch, patch_paper_session):
    from app.db.session import dispose_engine, init_db
    from app.paper.manager import PaperManager, get_paper_manager

    original_start = PaperManager.start
    calls = 0

    async def flaky_start(self, *args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("forced batch failure")
        return await original_start(self, *args, **kwargs)

    monkeypatch.setattr(PaperManager, "start", flaky_start)
    await init_db()
    transport = ASGITransport(app=_load_app())
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            ids = [item["id"] for item in (await client.get("/api/strategies")).json()[:2]]
            response = await client.post("/api/paper/batch/start", json=_body(ids))

            assert response.status_code == 500
            running = [
                item for item in get_paper_manager().list_sessions()
                if item["state"] == "running"
            ]
            assert running == []
    finally:
        await dispose_engine()


async def test_batch_stop_finalizes_all_runs(patch_paper_session):
    from app.db.session import dispose_engine, init_db

    await init_db()
    transport = ASGITransport(app=_load_app())
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            ids = [item["id"] for item in (await client.get("/api/strategies")).json()[:2]]
            start = await client.post("/api/paper/batch/start", json=_body(ids))
            group_id = start.json()["groupId"]

            response = await client.post(f"/api/paper/batch/{group_id}/stop")

            assert response.status_code == 200, response.text
            runs = response.json()["runs"]
            assert [item["strategyId"] for item in runs] == ids
            assert all(item["run"]["status"] == "stopped" for item in runs)
    finally:
        await dispose_engine()


async def test_batch_stop_is_idempotent_for_known_group(patch_paper_session):
    from app.db.session import dispose_engine, init_db

    await init_db()
    transport = ASGITransport(app=_load_app())
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            ids = [item["id"] for item in (await client.get("/api/strategies")).json()[:2]]
            start = await client.post("/api/paper/batch/start", json=_body(ids))
            group_id = start.json()["groupId"]

            first = await client.post(f"/api/paper/batch/{group_id}/stop")
            second = await client.post(f"/api/paper/batch/{group_id}/stop")

            assert first.status_code == 200, first.text
            assert second.status_code == 200, second.text
            assert all(item["run"]["status"] == "stopped" for item in second.json()["runs"])
    finally:
        await dispose_engine()


async def test_batch_stop_unknown_group_returns_404(patch_paper_session):
    from app.db.session import dispose_engine, init_db

    await init_db()
    transport = ASGITransport(app=_load_app())
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post("/api/paper/batch/missing/stop")

            assert response.status_code == 404
            assert response.json()["error"]["code"] == "NOT_FOUND"
    finally:
        await dispose_engine()


async def test_stop_all_frees_capacity_for_subsequent_start(monkeypatch, patch_paper_session):
    from app.config import get_settings
    from app.db.session import dispose_engine, init_db
    from app.paper.manager import get_paper_manager

    get_settings.cache_clear()
    monkeypatch.setenv("MAX_PAPER_SESSIONS", "2")
    await init_db()
    transport = ASGITransport(app=_load_app())
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            ids = [item["id"] for item in (await client.get("/api/strategies")).json()[:2]]
            filled = await client.post("/api/paper/batch/start", json=_body(ids))
            assert filled.status_code == 200, filled.text

            blocked = await client.post(
                "/api/paper/start", json={**_body([ids[0]]), "strategyId": ids[0]})
            assert blocked.status_code == 429

            stop_all = await client.post("/api/paper/stop-all")
            assert stop_all.status_code == 200, stop_all.text
            assert stop_all.json()["stopped"] == 2
            running = [s for s in get_paper_manager().list_sessions() if s["state"] == "running"]
            assert running == []

            retry = await client.post("/api/paper/batch/start", json=_body(ids))
            assert retry.status_code == 200, retry.text
    finally:
        get_settings.cache_clear()
        await dispose_engine()


async def test_stop_all_is_noop_safe_and_keeps_stopped_rows(patch_paper_session):
    from app.db.session import dispose_engine, init_db
    from app.paper.manager import get_paper_manager

    await init_db()
    transport = ASGITransport(app=_load_app())
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            empty = await client.post("/api/paper/stop-all")
            assert empty.status_code == 200, empty.text
            assert empty.json()["stopped"] == 0
            assert empty.json()["runs"] == []

            sid = (await client.get("/api/strategies")).json()[0]["id"]
            started = await client.post(
                "/api/paper/start", json={**_body([sid]), "strategyId": sid})
            assert started.status_code == 200, started.text
            session_id = started.json()["sessionId"]
            await client.post(f"/api/paper/{session_id}/stop")

            again = await client.post("/api/paper/stop-all")
            assert again.json()["stopped"] == 0
            rows = get_paper_manager().list_sessions()
            assert len(rows) == 1
            assert all(row["state"] == "stopped" for row in rows)
    finally:
        await dispose_engine()


def _body(strategy_ids: list[int]) -> dict:
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
