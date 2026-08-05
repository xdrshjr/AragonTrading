"""发现层 REST / WS 端点测试（strategy-factor-discovery §7.1）。

覆盖：守门序 403 → 400 → 413 → 409、错误体形状、CRUD、候选查询、配置默认与词表、
WS 首发 ``state`` + 环形回放 + 终态不被挤掉 + ``ping``。

OKX 与运行时全程打桩（不触网、不起真循环）。
"""
from __future__ import annotations

import asyncio
import json

from fastapi import WebSocketDisconnect
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete

from app.api.discovery import ws_discovery
from app.config import Settings, get_settings
from app.db.models import DiscoveryCandidate, DiscoveryIteration, DiscoveryRun
from app.db.session import dispose_engine, get_sessionmaker, init_db
from app.discovery import repositories as repo
from app.discovery.manager import get_discovery_manager

_TS = 1_700_000_000


def _load_app():
    from app.main import app
    return app


async def _fresh():
    await init_db()
    manager = get_discovery_manager()
    manager._runs.clear()
    manager._reserved = 0
    sm = get_sessionmaker()
    async with sm() as session:
        await session.execute(delete(DiscoveryCandidate))
        await session.execute(delete(DiscoveryIteration))
        await session.execute(delete(DiscoveryRun))
        await session.commit()
    return sm


def _override(app, **kw) -> None:
    app.dependency_overrides[get_settings] = lambda: Settings(**kw)


def _patch_settings(monkeypatch, **kw) -> None:
    """覆写发现层读到的 settings。

    ⚠️ ``dependency_overrides`` 对本层**无效**：``service`` / ``manager`` 是模块级
    ``get_settings()`` 调用（不走 ``Depends``），只有 monkeypatch 才能改到。
    """
    settings = Settings(**kw)
    for target in ("app.discovery.service.get_settings",
                   "app.discovery.manager.get_settings"):
        monkeypatch.setattr(target, lambda: settings)


def _client(app) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


def _body(**over) -> dict:
    body = {"instId": "BTC-USDT-SWAP", "bar": "1H", "windowBars": 1000,
            "candidatesPerIteration": 10, "iterationIntervalS": 300, "axes": ["factor"]}
    body.update(over)
    return body


class _FakeWS:
    """WebSocket 桩（镜像 ``test_agent_ws.py`` 的做法）。

    刻意**不用** ``TestClient.websocket_connect``：它在已运行的事件循环里会与 anyio portal
    死锁（pytest ``asyncio_mode="auto"`` 下测试函数本身就是协程），表现为整条用例挂住。
    直接调端点函数既快又不触发那条坑。
    """

    def __init__(self, stop_after: int | None = None):
        self.sent: list[dict] = []
        self.closed = False
        self._stop_after = stop_after

    async def accept(self):
        return None

    async def send_text(self, text: str):
        self.sent.append(json.loads(text))
        if self._stop_after is not None and len(self.sent) >= self._stop_after:
            raise WebSocketDisconnect()

    async def close(self):
        self.closed = True

    def types(self) -> list[str]:
        return [event["type"] for event in self.sent]


class _StubRun:
    """运行时桩：只实现 WS 与状态投影需要的接口。"""

    def __init__(self, run_id: int, events: list | None = None,
                 pending: list | None = None):
        self.run_id = run_id
        self.status = "running"
        self.pause_reason = ""
        self.alive = True           # 循环协程是否还在跑（见 DiscoveryRun.is_alive）
        self._events = events or []
        self._pending = pending or []
        self._queues: set[asyncio.Queue] = set()

    @property
    def is_terminal(self) -> bool:
        return self.status in ("stopped", "succeeded", "failed")

    @property
    def is_alive(self) -> bool:
        return self.alive

    def state_snapshot(self) -> dict:
        return {"runId": self.run_id, "status": self.status, "iteration": 3,
                "counters": {"tested": 10, "kept": 1}, "rebuilding": False}

    def snapshot(self) -> list[dict]:
        return [{"type": "state", "data": self.state_snapshot()}] + list(self._events)

    def subscribe(self) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=64)
        for event in self._pending:
            queue.put_nowait(event)
        self._queues.add(queue)
        return queue

    def unsubscribe(self, queue) -> None:
        self._queues.discard(queue)

    def push(self, event: dict) -> None:
        for queue in self._queues:
            queue.put_nowait(event)

    def request_cancel(self):
        self.status = "stopped"

    def request_pause(self, reason="user"):
        self.pause_reason = reason
        self.status = "paused"

    def request_resume(self):
        self.pause_reason = ""
        self.status = "running"

    def start(self):
        return None

    def force_cancel(self):
        return None

    async def join(self):
        return None


def _patch_launch(monkeypatch) -> list:
    """把真正的 run 装配换成桩（不取数、不起协程），返回被启动的 spec 列表。"""
    launched: list = []

    def _start(self, spec):
        launched.append(spec)
        run = _StubRun(spec.run_id)
        self._runs[spec.run_id] = run
        self.release()
        return run

    monkeypatch.setattr("app.discovery.manager.DiscoveryManager.start", _start)

    async def _no_client(_config):
        return None

    monkeypatch.setattr("app.discovery.service._resolve_client", _no_client)
    return launched


# ============================ 启动守门序 403 → 400 → 413 → 409 ============================
async def test_master_switch_returns_403(monkeypatch):
    await _fresh()
    app = _load_app()
    _patch_settings(monkeypatch, discovery_enabled=False)
    _patch_launch(monkeypatch)
    try:
        async with _client(app) as client:
            res = await client.post("/api/discovery/runs", json=_body())
        assert res.status_code == 403
        assert res.json()["error"]["code"] == "DISCOVERY_DISABLED"
    finally:
        app.dependency_overrides.clear()
        await dispose_engine()


async def test_unimplemented_axis_returns_400(monkeypatch):
    """D-6 强制推论：未实现的轴在 API 上一律 400，绝不「能选但不产候选」。"""
    await _fresh()
    app = _load_app()
    _override(app)
    _patch_launch(monkeypatch)
    try:
        async with _client(app) as client:
            res = await client.post("/api/discovery/runs",
                                    json=_body(axes=["strategy_param"]))
        assert res.status_code == 400
        assert res.json()["error"]["code"] == "DISCOVERY_AXIS_UNSUPPORTED"
    finally:
        app.dependency_overrides.clear()
        await dispose_engine()


async def test_unknown_bar_returns_400(monkeypatch):
    await _fresh()
    app = _load_app()
    _override(app)
    _patch_launch(monkeypatch)
    try:
        async with _client(app) as client:
            res = await client.post("/api/discovery/runs", json=_body(bar="7H"))
        assert res.status_code == 400
        assert res.json()["error"]["code"] == "DISCOVERY_BAR_UNSUPPORTED"
    finally:
        app.dependency_overrides.clear()
        await dispose_engine()


async def test_workload_over_limit_returns_413(monkeypatch):
    await _fresh()
    app = _load_app()
    _patch_settings(monkeypatch, discovery_work_unit_limit=1000)
    _patch_launch(monkeypatch)
    try:
        async with _client(app) as client:
            res = await client.post("/api/discovery/runs",
                                    json=_body(windowBars=8000, candidatesPerIteration=200))
        assert res.status_code == 413
        assert res.json()["error"]["code"] == "DISCOVERY_TOO_LARGE"
    finally:
        app.dependency_overrides.clear()
        await dispose_engine()


async def test_second_concurrent_start_returns_409(monkeypatch):
    """并发闸：默认 ``discovery_max_runs=1``，第二个启动请求恒 409（与时序无关）。"""
    await _fresh()
    app = _load_app()
    _override(app)
    _patch_launch(monkeypatch)
    try:
        async with _client(app) as client:
            first = await client.post("/api/discovery/runs", json=_body())
            second = await client.post("/api/discovery/runs", json=_body())
        assert first.status_code == 201
        assert second.status_code == 409
        assert second.json()["error"]["code"] == "DISCOVERY_BUSY"
    finally:
        app.dependency_overrides.clear()
        await dispose_engine()


async def test_parameter_error_does_not_consume_a_slot(monkeypatch):
    """守门序把 ``reserve()`` 放最后：参数错**不**白占名额（否则一次手滑就锁死并发位）。"""
    await _fresh()
    app = _load_app()
    _override(app)
    _patch_launch(monkeypatch)
    try:
        async with _client(app) as client:
            bad = await client.post("/api/discovery/runs", json=_body(bar="7H"))
            good = await client.post("/api/discovery/runs", json=_body())
        assert bad.status_code == 400 and good.status_code == 201
        assert get_discovery_manager()._reserved == 0
    finally:
        app.dependency_overrides.clear()
        await dispose_engine()


async def test_launch_failure_releases_the_slot(monkeypatch):
    """启动路径任一步抛错都要 ``release()`` 归还名额，绝不泄漏。

    直接调 service 层而非走 HTTP：``ASGITransport`` 会把未处理异常原样抛给调用方，
    在这里断言「异常发生了 **且** 名额已归还」比断言状态码更直接。
    """
    import pytest

    from app.discovery import service as discovery_service

    sm = await _fresh()
    _patch_settings(monkeypatch)

    def _boom(self, spec):
        raise RuntimeError("boom")

    monkeypatch.setattr("app.discovery.manager.DiscoveryManager.start", _boom)

    async def _no_client(_config):
        return None

    monkeypatch.setattr("app.discovery.service._resolve_client", _no_client)
    try:
        async with sm() as session:
            with pytest.raises(RuntimeError):
                await discovery_service.start_run(session, object(), _body())
        assert get_discovery_manager()._reserved == 0
    finally:
        await dispose_engine()


# ============================ 生命周期与查询 ============================
async def test_full_lifecycle(monkeypatch):
    sm = await _fresh()
    app = _load_app()
    _override(app)
    _patch_launch(monkeypatch)
    try:
        async with _client(app) as client:
            created = (await client.post("/api/discovery/runs", json=_body())).json()
            run_id = created["runId"]
            assert created["config"]["axes"] == ["factor"]
            listed = (await client.get("/api/discovery/runs")).json()
            assert listed["total"] == 1 and listed["items"][0]["runId"] == run_id
            detail = (await client.get(f"/api/discovery/runs/{run_id}")).json()
            assert detail["config"]["candidatesPerIteration"] == 10
            assert detail["state"]["status"] == "running"
            paused = await client.post(f"/api/discovery/runs/{run_id}/pause")
            assert paused.status_code == 202 and paused.json()["status"] == "paused"
            stopped = await client.post(f"/api/discovery/runs/{run_id}/stop")
            assert stopped.status_code == 202
            assert (await client.get("/api/discovery/runs/9999")).status_code == 404
            deleted = await client.delete(f"/api/discovery/runs/{run_id}")
            assert deleted.status_code == 204
        async with sm() as session:
            assert await repo.get_run(session, run_id) is None
    finally:
        app.dependency_overrides.clear()
        await dispose_engine()


async def test_delete_running_run_returns_409(monkeypatch):
    await _fresh()
    app = _load_app()
    _override(app)
    _patch_launch(monkeypatch)
    try:
        async with _client(app) as client:
            run_id = (await client.post("/api/discovery/runs", json=_body())).json()["runId"]
            res = await client.delete(f"/api/discovery/runs/{run_id}")
        assert res.status_code == 409
        assert res.json()["error"]["code"] == "DISCOVERY_RUNNING"
    finally:
        app.dependency_overrides.clear()
        await dispose_engine()


async def test_resume_on_terminal_run_returns_409(monkeypatch):
    sm = await _fresh()
    app = _load_app()
    _override(app)
    _patch_launch(monkeypatch)
    try:
        async with sm() as session:
            row = await repo.create_run(session, values=repo.RunValues(
                label="x", inst_id="BTC-USDT-SWAP", bar="1H", symbols=["BTC-USDT-SWAP"],
                config={"axes": ["factor"]}, checkpoint={}, started_ts=_TS))
            await repo.set_run_status(session, row.id, status="stopped")
        async with _client(app) as client:
            res = await client.post(f"/api/discovery/runs/{row.id}/resume")
        assert res.status_code == 409
        assert res.json()["error"]["code"] == "DISCOVERY_NOT_RESUMABLE"
    finally:
        app.dependency_overrides.clear()
        await dispose_engine()


async def test_resume_relaunches_a_run_whose_loop_already_exited(monkeypatch):
    """``_prepare`` 阶段取数失败转的 ``paused``：循环协程已返回，只翻标志等于什么都没做。

    此时对象仍在注册表里且非终态，若照旧走「内存里还在 ⇒ 放行标志」分支，接口会回
    ``running`` 而实际一轮都不会再跑——而且它还占着并发名额。
    """
    sm = await _fresh()
    app = _load_app()
    _override(app)
    launched = _patch_launch(monkeypatch)
    try:
        async with _client(app) as client:
            run_id = (await client.post("/api/discovery/runs", json=_body())).json()["runId"]
            dead = get_discovery_manager().get(run_id)
            dead.status, dead.pause_reason, dead.alive = "paused", "data_unavailable", False
            async with sm() as session:
                await repo.set_run_status(session, run_id, status="paused",
                                          pause_reason="data_unavailable")
            res = await client.post(f"/api/discovery/runs/{run_id}/resume")
        assert res.status_code == 202 and res.json()["status"] == "running"
        assert [spec.run_id for spec in launched] == [run_id, run_id], "没有重新装配 run"
        assert launched[-1].resume is True, "重装配必须走续跑路径（先重建 kept 池）"
    finally:
        app.dependency_overrides.clear()
        await dispose_engine()


async def test_candidate_endpoints(monkeypatch):
    sm = await _fresh()
    app = _load_app()
    _override(app)
    try:
        async with sm() as session:
            row = await repo.create_run(session, values=repo.RunValues(
                label="x", inst_id="BTC-USDT-SWAP", bar="1H", symbols=["BTC-USDT-SWAP"],
                config={}, checkpoint={}, started_ts=_TS))
            await repo.commit_iteration(
                session, row.id,
                candidates=[repo.CandidateRow(
                    kind="factor", genome_hash="h1", genome={"expr": "e"},
                    expr_text="ts_mean(ret,20)", describe_text="20 根收益率均值",
                    skeleton="ts_mean(?,_)", mechanism="gap_premium", source="random",
                    verdict="kept", metrics={"trials": 5}, series={"isIc": [0.1]},
                    trials_at_accept=5, created_ts=_TS)],
                iteration=repo.IterationRow(idx=1, started_ts=_TS, finished_ts=_TS,
                                            budget={}, stats={}, llm={}),
                checkpoint_json="{}", counters={"tested": 1, "kept": 1},
                candidate_row_cap=100, iteration_row_cap=100)
        async with _client(app) as client:
            listed = (await client.get(
                f"/api/discovery/runs/{row.id}/candidates")).json()
            assert listed["total"] == 1
            candidate_id = listed["items"][0]["id"]
            assert listed["items"][0]["trialsAtAccept"] == 5
            detail = (await client.get(
                f"/api/discovery/candidates/{candidate_id}")).json()
            assert detail["series"]["isIc"] == [0.1]
            assert (await client.get("/api/discovery/candidates/9999")).status_code == 404
            assert (await client.get(
                "/api/discovery/runs/9999/candidates")).status_code == 404
    finally:
        app.dependency_overrides.clear()
        await dispose_engine()


# ============================ 配置与词表 ============================
async def test_defaults_expose_only_implemented_axes():
    await _fresh()
    app = _load_app()
    _override(app)
    try:
        async with _client(app) as client:
            payload = (await client.get("/api/discovery/config/defaults")).json()
        assert payload["implementedAxes"] == ["factor"]
        assert set(payload["allAxes"]) > {"factor"}
        assert payload["thresholds"]["minAbsIc"] == 0.03
        assert payload["evolution"]["budget"]["mutate"] == 0.25
        assert payload["workUnitLimit"] > 0
    finally:
        app.dependency_overrides.clear()
        await dispose_engine()


async def test_vocabulary_is_sourced_from_code_tables():
    await _fresh()
    app = _load_app()
    _override(app)
    try:
        async with _client(app) as client:
            payload = (await client.get("/api/discovery/vocabulary")).json()
        assert len(payload["mechanisms"]) == 13
        assert any(item["key"] == "overnight" for item in payload["fields"])
        assert any(item["key"] == "ts_zscore" for item in payload["operators"])
        assert "不构成本功能的预期收益" in payload["note"]
    finally:
        app.dependency_overrides.clear()
        await dispose_engine()


# ============================ WebSocket ============================
async def test_ws_sends_state_snapshot_first_then_increments():
    """订阅即首发 ``state`` 全量快照，随后回放环形缓冲，再转发增量。"""
    await _fresh()
    manager = get_discovery_manager()
    run = _StubRun(1, events=[{"type": "iteration", "data": {"index": 3}}],
                   pending=[{"type": "candidate", "data": {"genomeHash": "h"}}])
    manager._runs[1] = run
    ws = _FakeWS(stop_after=3)
    try:
        await ws_discovery(ws, 1)
        assert ws.types() == ["state", "iteration", "candidate"]
        assert ws.sent[0]["data"]["status"] == "running"
        assert ws.sent[1]["data"]["index"] == 3
    finally:
        manager._runs.clear()
        await dispose_engine()


async def test_ws_terminal_event_is_never_pushed_out_of_the_ring():
    """终态事件单独持槽：晚订阅的客户端绝不会看到一个永远「进行中」的死流。"""
    await _fresh()
    manager = get_discovery_manager()
    run = _StubRun(2, events=[{"type": "iteration", "data": {"index": i}} for i in range(3)]
                   + [{"type": "done", "data": {"status": "stopped"}}])
    run.status = "stopped"
    manager._runs[2] = run
    ws = _FakeWS()
    try:
        await ws_discovery(ws, 2)
        assert ws.types() == ["state", "iteration", "iteration", "iteration", "done"]
        assert ws.closed is True
    finally:
        manager._runs.clear()
        await dispose_engine()


async def test_ws_unknown_run_gets_error_then_close():
    await _fresh()
    ws = _FakeWS()
    try:
        await ws_discovery(ws, 404)
        assert ws.types() == ["error"]
        assert ws.sent[0]["data"]["code"] == "NOT_FOUND"
        assert ws.closed is True
    finally:
        await dispose_engine()


async def test_ws_pings_on_silence_and_closes_on_terminal(monkeypatch):
    """静默即 ping 半开探测；run 已终态则补发 ``done`` 后关闭（满队列丢终态的兜底）。"""
    import app.api.discovery as api_discovery

    await _fresh()
    monkeypatch.setattr(api_discovery, "_WS_HEARTBEAT_SECONDS", 0.01)
    manager = get_discovery_manager()
    run = _StubRun(3, events=[{"type": "done", "data": {"status": "stopped"}}])
    manager._runs[3] = run
    ws = _FakeWS()
    try:
        queue: asyncio.Queue = asyncio.Queue()
        run.status = "running"

        async def _delayed_terminal():
            await asyncio.sleep(0.03)
            run.status = "stopped"

        task = asyncio.create_task(_delayed_terminal())
        await api_discovery._pump(ws, run, queue)
        await task
        assert "ping" in ws.types()
        assert ws.types()[-1] == "done"
        assert ws.closed is True
    finally:
        manager._runs.clear()
        await dispose_engine()


async def test_error_body_shape_is_uniform(monkeypatch):
    await _fresh()
    app = _load_app()
    _patch_settings(monkeypatch, discovery_enabled=False)
    try:
        async with _client(app) as client:
            body = (await client.post("/api/discovery/runs", json=_body())).json()
        assert set(body) == {"error"}
        assert set(body["error"]) == {"code", "message"}
        assert isinstance(body["error"]["message"], str)
        json.dumps(body, ensure_ascii=False)     # 中文消息可序列化
    finally:
        app.dependency_overrides.clear()
        await dispose_engine()
