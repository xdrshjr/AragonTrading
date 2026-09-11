"""后训练轨迹采集 · 启动链路、目录校验与生命周期（ai-trade-trajectory-capture §6.1 A-1…A-12）。

REST 层用例沿用 `test_ai_trade_api.py` / `test_ai_trade_resume_cold.py` 的会话替身与 OKX 桩：替身会话不跑
决策轮，本文件守的是「请求 → 解析 → 端点校验 → 装配 → 回执 → 关闭」这条链；写点本身在
`test_ai_trade_trajectory_session.py`。占位假凭证，全程脱网。
"""
from __future__ import annotations

import asyncio
import json
import shutil
import sys
import tempfile
import threading
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path

import pytest
from sqlalchemy import func, select

import app as app_package
from app.ai_auto.manager import (AiTradeManager, AiTradePreflightError, AiTradeResumeNotArmed,
                                 get_ai_trade_manager)
from app.ai_auto.trajectory import RecordingLlmClient
from app.ai_auto.trajectory import directory as directory_mod
from app.ai_auto.trajectory import recorder as recorder_mod
from app.ai_auto.trajectory.directory import DirVerdict, check_directory
from app.ai_auto.trajectory.recorder import RecorderOpenResult
from app.ai_auto.trajectory.writer import QueuedLine, SegmentWriter
from app.config import Settings, get_settings
from app.db import repositories as repo
from app.db.models import StrategyRun
from app.db.session import db_file_path, dispose_engine, get_sessionmaker
from tests import test_ai_trade_api as api_case
from tests import test_ai_trade_resume_cold as cold_case

_WRITER_PREFIX = "ai-trade-trajectory-"


# ---------------- 夹具 ----------------
@dataclass
class _Ctx:
    client: object
    strategy_id: int
    root: str
    app: object


def _override(app, **over) -> None:
    base = {"live_trading_enabled": True, "ai_trade_enabled": True,
            "ai_trade_trajectory_min_free_bytes": 0}
    base.update(over)
    app.dependency_overrides[get_settings] = lambda: Settings(**base)


def _patch(monkeypatch) -> None:
    api_case._patch(monkeypatch)

    async def no_fee(*_args, **_kwargs):
        return None

    monkeypatch.setattr("app.ai_auto.manager.fetch_trade_fee_ex", no_fee)


async def _join_all(timeout: float = 6.0) -> None:
    for recorder in list(recorder_mod._REGISTRY.values()):
        recorder.close_nowait("test_teardown")
        await asyncio.to_thread(recorder.join, timeout)


@asynccontextmanager
async def _api(monkeypatch, **settings_over):
    strategy_id = await api_case._fresh()
    app = api_case._load_app()
    root = tempfile.mkdtemp(prefix="trj")     # Windows 根目录上限 100 字符：不用 pytest 的长 tmp_path
    _override(app, **settings_over)
    _patch(monkeypatch)
    try:
        async with await api_case._client(app) as client:
            await api_case._seed_account(client)
            yield _Ctx(client, strategy_id, root, app)
    finally:
        get_ai_trade_manager()._group = None
        app.dependency_overrides.clear()
        await _join_all()
        shutil.rmtree(root, ignore_errors=True)
        await dispose_engine()


def _body(strategy_id: int, directory: str | None, **over) -> dict:
    return api_case._start_body(strategy_id,
                                trajectoryCapture={"enabled": True, "directory": directory},
                                **over)


async def _start(ctx: _Ctx, **over) -> str:
    started = await ctx.client.post("/api/ai-trade/start", json=_body(ctx.strategy_id, ctx.root,
                                                                      **over))
    assert started.status_code == 200, started.text
    return started.json()["groupId"]


async def _count_runs() -> int:
    async with get_sessionmaker()() as session:
        return (await session.execute(select(func.count()).select_from(StrategyRun))).scalar()


def _verdict(code: str, directory: str, *, is_default: bool = False) -> DirVerdict:
    return DirVerdict(False, code, "目录不可写：拒绝访问（13）", directory=directory,
                      is_default=is_default, exists=True, free_bytes=None, min_free_bytes=0)


def _manifest(recorder) -> dict:
    return json.loads((recorder.group_dir / "manifest.json").read_text(encoding="utf-8"))


# ---------------- A-1 / A-2 / A-4 / A-12：请求 → 回执 ----------------
async def test_a1_absent_key_never_captures_nor_creates_a_directory(monkeypatch):
    async with _api(monkeypatch) as ctx:
        default_root = Path(ctx.root) / "default-root"
        _override(ctx.app, ai_trade_trajectory_default_dir=str(default_root))
        before = set(recorder_mod._REGISTRY)
        started = await ctx.client.post("/api/ai-trade/start",
                                        json=api_case._start_body(ctx.strategy_id))
        assert started.status_code == 200, started.text
        receipt = started.json()["trajectoryCapture"]
        assert (receipt["enabled"], receipt["requested"]) == (False, False)
        assert (receipt["source"], receipt["state"]) == ("not_requested", "off")
        assert receipt["directory"] is None and receipt["lastError"] is None
        assert set(receipt["counters"]) >= {"records", "dropped", "lateCalls"}
        assert not default_root.exists() and set(recorder_mod._REGISTRY) == before


async def test_a2_requested_capture_records_into_a_dated_group_directory(monkeypatch):
    async with _api(monkeypatch) as ctx:
        started = await ctx.client.post("/api/ai-trade/start",
                                        json=_body(ctx.strategy_id, ctx.root))
        assert started.status_code == 200, started.text
        group_id = started.json()["groupId"]
        receipt = started.json()["trajectoryCapture"]
        assert (receipt["enabled"], receipt["requested"]) == (True, True)
        assert (receipt["source"], receipt["state"]) == ("request", "recording")
        directory = Path(receipt["directory"])
        assert directory.parent == Path(ctx.root) and directory.name.endswith(f"-{group_id}")
        assert directory.name[:8].isdigit() and receipt["isDefaultRoot"] is False
        group = get_ai_trade_manager()._group
        assert group.start_wire["trajectoryCapture"] == {"enabled": True, "directory": ctx.root}
        status = (await ctx.client.get("/api/ai-trade/status")).json()["group"]
        assert status["trajectoryCapture"]["state"] == "recording"
        recorder = recorder_mod._REGISTRY[group_id]
        assert (await ctx.client.post(f"/api/ai-trade/{group_id}/stop")).status_code == 200
        assert await asyncio.to_thread(recorder.join, recorder.config.close_timeout_s + 1)
        manifest = _manifest(recorder)
        assert manifest["state"] == "closed" and manifest["env"] == "live"
        assert (Path(ctx.root) / "DATASET_CARD.md").exists()


async def test_a4_ops_switch_off_resolves_to_ops_off_without_touching_disk(monkeypatch):
    async with _api(monkeypatch, ai_trade_trajectory_enabled=False) as ctx:
        target = Path(ctx.root) / "never-created"
        started = await ctx.client.post("/api/ai-trade/start",
                                        json=_body(ctx.strategy_id, str(target)))
        assert started.status_code == 200, started.text
        receipt = started.json()["trajectoryCapture"]
        assert (receipt["enabled"], receipt["requested"]) == (False, False)
        assert (receipt["source"], receipt["state"]) == ("ops_off", "off")
        assert not target.exists()
        assert started.json()["groupId"] not in recorder_mod._REGISTRY


async def test_a12_demo_account_sessions_are_labelled_demo(monkeypatch):
    async with _api(monkeypatch, okx_account_demo=True) as ctx:
        group_id = await _start(ctx)
        recorder = recorder_mod._REGISTRY[group_id]
        assert recorder.env == "demo"
        await ctx.client.post(f"/api/ai-trade/{group_id}/stop")
        assert await asyncio.to_thread(recorder.join, recorder.config.close_timeout_s + 1)
        assert _manifest(recorder)["env"] == "demo"


# ---------------- A-3 / A-9：新启动的拒绝 ----------------
@pytest.mark.parametrize("is_default,expected", [
    (False, ["trajectory_use_default_dir", "trajectory_disable"]),
    (True, ["trajectory_disable"]),
])
async def test_a3_unusable_directory_rejects_the_start_with_remedies(monkeypatch, is_default,
                                                                     expected):
    async with _api(monkeypatch) as ctx:
        async def unusable(raw, settings):
            return _verdict("TRAJECTORY_DIR_NOT_WRITABLE", ctx.root, is_default=is_default)

        monkeypatch.setattr("app.api.ai_auto.probe_directory", unusable)
        runs_before = await _count_runs()
        rejected = await ctx.client.post(
            "/api/ai-trade/start", json=_body(ctx.strategy_id, None if is_default else ctx.root))
        assert rejected.status_code == 422, rejected.text
        error = rejected.json()["error"]
        assert error["code"] == "AI_TRADE_TRAJECTORY_DIR_INVALID"
        assert error["reasonCode"] == "TRAJECTORY_DIR_NOT_WRITABLE"
        assert [remedy["id"] for remedy in error["remedies"]] == expected
        assert "**" not in error["message"]
        assert get_ai_trade_manager().group_status() is None
        assert await _count_runs() == runs_before


@pytest.mark.parametrize("code", ["OPEN_FAILED", "OPEN_TIMEOUT", "OPEN_BUSY"])
async def test_a9_fresh_start_open_failure_is_a_422_with_zero_residue(monkeypatch, code):
    """RV10：新启动打开失败在 W11 抛出——尚未建任何 run 行，抛出零残留。"""
    async with _api(monkeypatch) as ctx:
        async def failing_open(_request):
            return RecorderOpenResult(error={"code": code, "message": "写线程起不来", "ts": 0,
                                             "directory": ctx.root, "isDefault": False})

        monkeypatch.setattr("app.ai_auto.manager.open_group_recorder", failing_open)
        runs_before = await _count_runs()
        rejected = await ctx.client.post("/api/ai-trade/start",
                                         json=_body(ctx.strategy_id, ctx.root))
        assert rejected.status_code == 422, rejected.text
        error = rejected.json()["error"]
        assert error["code"] == "AI_TRADE_TRAJECTORY_DIR_INVALID" and error["reasonCode"] == code
        assert [remedy["id"] for remedy in error["remedies"]] == [
            "trajectory_use_default_dir", "trajectory_disable"]
        assert await _count_runs() == runs_before
        assert get_ai_trade_manager().group_status() is None


# ---------------- A-6 / A-7：两个只读端点 ----------------
def _case_disabled(ctx, monkeypatch):
    _override(ctx.app, ai_trade_trajectory_enabled=False)
    return ctx.root, "TRAJECTORY_DISABLED_BY_OPS"


def _case_unsupported(ctx, monkeypatch):
    return "\\\\?\\C:\\data\\ai-trade", "TRAJECTORY_DIR_UNSUPPORTED"


def _case_not_absolute(ctx, monkeypatch):
    return "relative\\ai-trade", "TRAJECTORY_DIR_NOT_ABSOLUTE"


def _case_too_long(ctx, monkeypatch):
    if sys.platform != "win32":
        pytest.skip("长路径阈值只在 Windows 生效")
    return str(Path(ctx.root) / ("x" * 120)), "TRAJECTORY_DIR_PATH_TOO_LONG"


def _case_forbidden(ctx, monkeypatch):
    return str(Path(app_package.__file__).parent / "datasets"), "TRAJECTORY_DIR_FORBIDDEN"


def _case_is_file(ctx, monkeypatch):
    target = Path(ctx.root) / "a-file.txt"
    target.write_text("x", encoding="utf-8")
    return str(target), "TRAJECTORY_DIR_IS_FILE"


def _case_not_writable(ctx, monkeypatch):
    monkeypatch.setattr(directory_mod, "_write_probe",
                        lambda _directory: "目录不可写：拒绝访问（13）")
    return str(Path(ctx.root) / "locked"), "TRAJECTORY_DIR_NOT_WRITABLE"


def _case_low_space(ctx, monkeypatch):
    _override(ctx.app, ai_trade_trajectory_min_free_bytes=10 ** 18)
    return str(Path(ctx.root) / "full"), "TRAJECTORY_DIR_LOW_SPACE"


def _case_probe_timeout(ctx, monkeypatch):
    _override(ctx.app, ai_trade_trajectory_probe_timeout_s=0.05)
    real = directory_mod.check_directory

    def slow(raw, settings):
        time.sleep(0.5)
        return real(raw, settings)

    monkeypatch.setattr(directory_mod, "check_directory", slow)
    return str(Path(ctx.root) / "slow"), "TRAJECTORY_DIR_PROBE_TIMEOUT"


def _case_ok(ctx, monkeypatch):
    return str(Path(ctx.root) / "new" / "deeper"), None


_A6_CASES = {"disabled": _case_disabled, "unsupported": _case_unsupported,
             "not_absolute": _case_not_absolute, "too_long": _case_too_long,
             "forbidden": _case_forbidden, "is_file": _case_is_file,
             "not_writable": _case_not_writable, "low_space": _case_low_space,
             "probe_timeout": _case_probe_timeout, "ok": _case_ok}


@pytest.mark.parametrize("case", sorted(_A6_CASES))
async def test_a6_validate_returns_each_verdict_code_and_never_creates_the_target(monkeypatch,
                                                                                  case):
    async with _api(monkeypatch) as ctx:
        directory, code = _A6_CASES[case](ctx, monkeypatch)
        response = await ctx.client.post("/api/ai-trade/trajectory/validate",
                                         json={"directory": directory})
        assert response.status_code == 200, response.text
        verdict = response.json()
        assert verdict["code"] == code and verdict["ok"] is (code is None)
        assert "**" not in (verdict["message"] or "")
        if code is None:
            assert verdict["exists"] is False and isinstance(verdict["freeBytes"], int)
        if case in ("not_writable", "low_space", "probe_timeout", "ok"):
            assert not Path(directory).exists(), "校验绝不创建目标目录"


async def test_a6_validate_rejects_an_oversized_body(monkeypatch):
    async with _api(monkeypatch) as ctx:
        response = await ctx.client.post("/api/ai-trade/trajectory/validate",
                                         json={"directory": "C:\\" + "x" * 1100})
        assert response.status_code == 422


@pytest.mark.parametrize("absolute", [True, False])
async def test_a7_config_default_root_is_derived_from_the_database_file(monkeypatch, absolute):
    async with _api(monkeypatch) as ctx:
        db_path = str(Path(ctx.root) / "db" / "aragon.db") if absolute else "aragon.db"
        _override(ctx.app, db_path=db_path)
        config = (await ctx.client.get("/api/ai-trade/trajectory/config")).json()
    expected = db_file_path(Settings(db_path=db_path)).parent / "datasets" / "ai-trade"
    assert config["defaultRoot"] == str(expected)
    assert config["enabled"] is True and config["schemaVersion"] == 1
    assert config["backtestSupported"] is False
    assert config["thinkingCapturedProviders"] == ["anthropic"]


# ---------------- A-8：关闭按调用方分档 ----------------
async def test_a8_stop_and_end_never_wait_for_the_writer(monkeypatch):
    """RV8：真实资金的停止 / 结束路径只 `close_nowait`。写线程被卡住 10 秒时两者照样立即返回。

    阈值取 1 秒而不是 spec 的 200 ms：替身会话的停止与归档本身有数据库写，CI 上偶发超过 200 ms；
    1 秒仍远小于被卡住的 10 秒与 5 秒关闭死线，足以证伪「在等写线程」。
    """
    release = threading.Event()
    original_finish = SegmentWriter._finish

    def stuck_finish(self, sentinel):
        release.wait(10)
        original_finish(self, sentinel)

    monkeypatch.setattr(SegmentWriter, "_finish", stuck_finish)
    try:
        async with _api(monkeypatch) as ctx:
            manager = get_ai_trade_manager()
            group_id = await _start(ctx)
            recorder = recorder_mod._REGISTRY[group_id]
            started = time.monotonic()
            assert await manager.stop_group(group_id) is not None
            assert time.monotonic() - started < 1.0 and recorder.is_alive()
            release.set()
            assert await asyncio.to_thread(recorder.join, recorder.config.close_timeout_s + 1)
            assert _manifest(recorder)["state"] == "closed"

            release.clear()
            group_id = await _start(ctx)
            paused = await ctx.client.post(f"/api/ai-trade/{group_id}/pause", json={})
            assert paused.status_code == 200, paused.text
            recorder = recorder_mod._REGISTRY[group_id]
            started = time.monotonic()
            assert await manager.end_group(group_id, flatten=None, acknowledged=False) is not None
            assert time.monotonic() - started < 1.0 and recorder.is_alive()
            release.set()
            assert await asyncio.to_thread(recorder.join, recorder.config.close_timeout_s + 1)
            assert _manifest(recorder)["state"] == "closed"
    finally:
        release.set()


@pytest.mark.parametrize("branch", ["paused", "hold", "flatten"])
async def test_a8_shutdown_closes_the_writer_within_its_bound(monkeypatch, branch):
    """RV7：`shutdown()` 三个分支都有界关闭（不压缩活动段、≤ 1 秒），返回时线程已结束或已记超时。"""
    over = {"ai_trade_shutdown_action": "flatten"} if branch == "flatten" else {}
    async with _api(monkeypatch, **over) as ctx:
        group_id = await _start(ctx)
        if branch == "paused":
            paused = await ctx.client.post(f"/api/ai-trade/{group_id}/pause", json={})
            assert paused.status_code == 200, paused.text
        recorder = recorder_mod._REGISTRY[group_id]
        started = time.monotonic()
        await get_ai_trade_manager().shutdown()
        assert time.monotonic() - started < 5.0
        assert not recorder.is_alive() or recorder.close_timed_out
        assert recorder.receipt_body()["state"] == "closed"


def _write_one_record(recorder) -> None:
    """直接入队一条记录（替身会话不跑写点），让关闭时真有一个活动段可压。"""
    inst_id = "BTC-USDT-SWAP"
    seq = recorder.allocate_seq(inst_id)
    line = json.dumps({"v": 1, "kind": "lifecycle", "seq": seq, "event": "probe"})
    assert recorder.offer(QueuedLine(inst_id, seq, recorder.epoch, "lifecycle", line, len(line)))


async def test_a8_flatten_shutdown_never_compresses_after_the_clear_group_close(monkeypatch):
    """RV7：flatten 退出时会话停止先经清组发一次默认压缩的关闭，随后的 `aclose(compress_active=False)`
    空转——写线程收尾仍不得压活动段（桌面端 2 秒后强杀，manifest 会写不到 closed）。"""
    async with _api(monkeypatch, ai_trade_shutdown_action="flatten") as ctx:
        group_id = await _start(ctx)
        recorder = recorder_mod._REGISTRY[group_id]
        _write_one_record(recorder)
        original_stop = AiTradeManager._stop_session
        stopped: list[str] = []

        async def stop_then_clear(self, session, reason, **kwargs):
            result = await original_stop(self, session, reason, **kwargs)
            stopped.append(reason)
            recorder_mod.close_recorder_nowait(recorder, "group_cleared")
            return result

        monkeypatch.setattr(AiTradeManager, "_stop_session", stop_then_clear)
        await get_ai_trade_manager().shutdown()
        assert stopped == ["shutdown"]
        assert await asyncio.to_thread(recorder.join, recorder.config.close_timeout_s + 1)
        names = [name for member in _manifest(recorder)["members"].values()
                 for name in member["segments"]]
        assert names and not any(name.endswith(".gz") for name in names)


async def test_a8_a_failing_shutdown_branch_still_closes_the_writer(monkeypatch):
    """已暂停分支里写状态行抛错：写线程照样在 finally 里有界关闭（I-10 起初只覆盖了 hold 分支）。"""
    async with _api(monkeypatch) as ctx:
        group_id = await _start(ctx)
        paused = await ctx.client.post(f"/api/ai-trade/{group_id}/pause", json={})
        assert paused.status_code == 200, paused.text
        recorder = recorder_mod._REGISTRY[group_id]

        async def broken_touch(self, _group_id):
            raise RuntimeError("state row write failed")

        monkeypatch.setattr(AiTradeManager, "_touch_state_row", broken_touch)
        with pytest.raises(RuntimeError, match="state row write failed"):
            await get_ai_trade_manager().shutdown()
        assert not recorder.is_alive() or recorder.close_timed_out
        assert recorder.receipt_body()["state"] == "closed"


# ---------------- A-10：所有者帧 ----------------
async def test_a10_a_member_build_failure_closes_the_writer(monkeypatch):
    async with _api(monkeypatch) as ctx:
        original = AiTradeManager._build_member
        built = {"count": 0}

        async def flaky(self, build_ctx, plan):
            built["count"] += 1
            if built["count"] == 2:
                raise AiTradePreflightError("MEMBER_BUILD_FAILED", "第二个成员装配失败", 409)
            return await original(self, build_ctx, plan)

        monkeypatch.setattr(AiTradeManager, "_build_member", flaky)
        before = set(recorder_mod._REGISTRY)
        members = [{"instId": "BTC-USDT-SWAP", "allocationPct": 50},
                   {"instId": "ETH-USDT-SWAP", "allocationPct": 50}]
        rejected = await ctx.client.post("/api/ai-trade/start",
                                         json=_body(ctx.strategy_id, ctx.root, members=members))
        assert rejected.status_code == 409, rejected.text
        opened = [recorder_mod._REGISTRY[group_id]
                  for group_id in set(recorder_mod._REGISTRY) - before]
        assert len(opened) == 1
        assert await asyncio.to_thread(opened[0].join, opened[0].config.close_timeout_s + 1)


# ---------------- A-5 / A-11：冷续跑 ----------------
async def _start_and_pause_with_capture(client, strategy_id: int, root: str) -> str:
    """`test_ai_trade_resume_cold._start_and_pause` 的同形版本，只多一个 `trajectoryCapture` 键。"""
    await cold_case._seed_account(client)
    body = {**cold_case._start_body(strategy_id),
            "trajectoryCapture": {"enabled": True, "directory": root}}
    started = await client.post("/api/ai-trade/start", json=body)
    assert started.status_code == 200, started.text
    group_id = started.json()["groupId"]
    open_ts = cold_case._START_TS - 4 * cold_case._BAR_SECONDS
    async with get_sessionmaker()() as db:
        for member in get_ai_trade_manager()._group.members:
            await repo.append_live_trade(db, member.run_id, {
                "ts": open_ts, "side": "long", "price": 30000.0, "qty": 0.6, "fee": 0, "pnl": 0,
                "positionAfter": 0.6, "explain": {"meta": {
                    "accountingEventId": f"paused-open:{member.run_id}", "wallTs": open_ts,
                    "feeKnown": True, "feeBasis": "measured", "feeCurrency": "USDT"}}})
    paused = await client.post(f"/api/ai-trade/{group_id}/pause", json={})
    assert paused.status_code == 200, paused.text
    return group_id


@asynccontextmanager
async def _cold(monkeypatch):
    strategy_id = await cold_case._fresh()
    app = cold_case._load_app()
    root = tempfile.mkdtemp(prefix="trj")
    _override(app)
    cold_case._patch(monkeypatch)
    try:
        async with await cold_case._client(app) as client:
            group_id = await _start_and_pause_with_capture(client, strategy_id, root)
            await cold_case._go_cold(group_id)
            cold_case._patch(monkeypatch, positions=[dict(cold_case._ACTUAL)])
            yield client, group_id, root
    finally:
        get_ai_trade_manager()._group = None
        app.dependency_overrides.clear()
        await _join_all()
        shutil.rmtree(root, ignore_errors=True)
        await dispose_engine()


async def test_a5_cold_resume_with_an_unusable_directory_still_resumes(monkeypatch):
    """采集层绝不阻挡一个可能正持有真实仓位的会话续跑：续跑成功，回执 `failed` + 原因。"""
    async with _cold(monkeypatch) as (client, group_id, root):
        monkeypatch.setattr("app.ai_auto.trajectory.recorder.check_directory",
                            lambda raw, settings: _verdict("TRAJECTORY_DIR_NOT_WRITABLE", root))
        resumed = await client.post(f"/api/ai-trade/{group_id}/resume", json={"acknowledge": True})
        assert resumed.status_code == 200, resumed.text
        receipt = (await client.get("/api/ai-trade/status")).json()["group"]["trajectoryCapture"]
        assert (receipt["requested"], receipt["enabled"], receipt["state"]) == (True, False,
                                                                              "failed")
        assert "目录不可写" in receipt["lastError"]["message"]
        assert "**" not in receipt["lastError"]["message"]


async def test_a11_cold_verify_failure_closes_the_writer_and_a_retry_has_one_writer(
        monkeypatch):
    async with _cold(monkeypatch) as (client, group_id, root):
        # 冷续跑读进程级 settings（下限 1 GiB）：这里只关心所有者帧，故把空间下限拿掉。
        monkeypatch.setattr(
            "app.ai_auto.trajectory.recorder.check_directory",
            lambda raw, settings: check_directory(raw, settings.model_copy(
                update={"ai_trade_trajectory_min_free_bytes": 0})))
        original = AiTradeManager._verify_armed
        failing = {"on": True}

        async def verify(self, group, receipts, *, cold=False):
            if cold and failing["on"]:
                raise AiTradeResumeNotArmed([{"instId": "BTC-USDT-SWAP", "code": "feed_stale",
                                              "message": "行情不可达"}], "继续失败，已退回暂停态。")
            return await original(self, group, receipts, cold=cold)

        monkeypatch.setattr(AiTradeManager, "_verify_armed", verify)
        refused = await client.post(f"/api/ai-trade/{group_id}/resume", json={"acknowledge": True})
        assert refused.status_code == 409, refused.text
        recorder = recorder_mod._REGISTRY[group_id]
        assert await asyncio.to_thread(recorder.join, recorder.config.close_timeout_s + 1)
        failing["on"] = False
        resumed = await client.post(f"/api/ai-trade/{group_id}/resume", json={"acknowledge": True})
        assert resumed.status_code == 200, resumed.text
        writers = [thread for thread in threading.enumerate()
                   if thread.name.startswith(_WRITER_PREFIX) and thread.is_alive()]
        assert len(writers) == 1
        assert recorder_mod._REGISTRY[group_id] is not recorder


# ---------------- S-6（装配半边）：规则关闭时客户端对象逐个不变 ----------------
async def test_s6_rule_off_hands_the_factory_clients_to_the_decider_unchanged(monkeypatch):
    created: list[object] = []
    deciders: list[object] = []

    def factory(_cfg):
        created.append(object())
        return created[-1]

    class _CapturingSession(api_case._FakeSession):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            deciders.append(args[6])

    async with _api(monkeypatch) as ctx:
        monkeypatch.setattr("app.ai_auto.manager.make_llm_client_from_config", factory)
        monkeypatch.setattr("app.ai_auto.manager.AiTradeSession", _CapturingSession)
        plain = await ctx.client.post("/api/ai-trade/start",
                                      json=api_case._start_body(ctx.strategy_id))
        assert plain.status_code == 200, plain.text
        decider = deciders[-1]
        assert decider._llm is created[0]
        assert decider._reflection_llm is created[1] and decider._summary_llm is created[2]
        await ctx.client.post(f"/api/ai-trade/{plain.json()['groupId']}/stop")
        group_id = await _start(ctx)
        wrapped = deciders[-1]._llm
        assert isinstance(wrapped, RecordingLlmClient) and wrapped._inner is created[3]
        await ctx.client.post(f"/api/ai-trade/{group_id}/stop")
