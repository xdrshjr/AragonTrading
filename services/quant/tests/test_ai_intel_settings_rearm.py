"""设置保存 ⇄ 热重装的端到端接线（ai-trade-intel-arm-state §7.1 · G-20…G-23 / G-25 /
G-26 / G-31）。

本轮的验收标准只有一句话：在 AI 自动交易**正在运行**的状态下，进设置中心选好检索源、存好
API Key，**不做任何其它操作**，回到标的卡片点开【市场情报】，弹窗必须立刻显示为「已配置」
并开始正常排期采集。

⚠️ **key 只写进 `app_settings`、env 留空**（放行条件 C-9）：那是打包形态下唯一的存 key 途径，
也是用户报告的那一种处境。把 key 塞进 env 的用例会让这些闸恒绿而什么都没验到。
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete

from app.ai_auto.intel import arming
from app.ai_auto.intel import runtime as intel_runtime
from app.ai_auto.intel import scheduler as intel_scheduler
from app.ai_auto.intel import search as intel_search
from app.ai_auto.intel import service as intel_service
from app.ai_auto.intel import taxonomy as tax
from app.config import Settings
from app.db import repositories as repo
from app.db.models import AppSetting
from app.db.session import get_sessionmaker, init_db

GROUP = "grp-1"
INST = "BTC-USDT-SWAP"
# 端口同步点：18761 是 `security/origins.DEFAULT_ORIGINS` 里的 Next 前端端口。
_ORIGIN = {"Origin": "http://localhost:18761"}


def _client() -> AsyncClient:
    from app.main import app

    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test",
                       headers=_ORIGIN)


@pytest.fixture(autouse=True)
async def _clean_registry():
    yield
    for task in list(intel_scheduler.tasks()):
        task.cancel()
    if intel_scheduler.tasks():
        await asyncio.wait(set(intel_scheduler.tasks()), timeout=0.5)
    intel_scheduler._TASKS.clear()
    intel_scheduler._LOOPS.clear()
    intel_runtime._RUNTIMES.clear()
    intel_runtime._BY_INST.clear()
    arming._ARM_ERRORS.clear()
    # `services/quant/tests` **共用同一个临时 DB**（本仓库成文纪律）：本文件往 `app_settings`
    # 里写了 provider / key 行，留着会让后面按 env 值装配的用例（`test_ai_intel_wiring.py` 的
    # 核查预算回落那条）读到 `provider="none"` 而判红——症状离原因很远。
    try:
        async with get_sessionmaker()() as session:
            await session.execute(delete(AppSetting))
            await session.commit()
    except Exception:  # noqa: BLE001 —— 引擎还没起来时无事可清
        pass


async def _fresh() -> None:
    await init_db()
    async with get_sessionmaker()() as session:
        await session.execute(delete(AppSetting))
        await session.commit()
        await repo.set_workspace_watchlist(session, watchlist=[INST], members=[],
                                           allocation_mode="even", symbol_groups=[])


async def _store_settings(*, provider="tavily", api_key="k-live", enabled=None) -> None:
    """直接写库内行——**不经 PUT**，故不会顺带触发一次重装。

    用来构造「配置是齐的、可流水线就是没装上」这一种处境（`_wire_intel` fail-open 之后的那种）。
    """
    async with get_sessionmaker()() as session:
        await repo.set_app_setting(session, "ai_intel.provider", provider)
        await repo.set_app_setting(session, intel_service.INTEL_API_KEY_APP_KEY, api_key)
        if enabled is not None:
            await repo.set_app_setting(session, "ai_intel.enabled", enabled)


async def _noop_push(summary):
    return None


def _fake_group(group_id=GROUP):
    session = SimpleNamespace(change_24h_pct=lambda: -1.5, push_intel=_noop_push,
                              note_intel_alert=lambda handoff: None)
    member = SimpleNamespace(inst_id=INST, session=session)
    return SimpleNamespace(group_id=group_id, bar="15m", members=[member])


def _running_manager(monkeypatch, *, running=True):
    """一个**真的** `AiTradeManager`（`rearm_intel` 的回落支路必须是真实现），只替换三处外部依赖。"""
    from app.ai_auto.manager import AiTradeManager

    manager = AiTradeManager()
    group = _fake_group()
    manager._group = group if running else None
    monkeypatch.setattr(manager, "_group_settings", lambda g: Settings())
    monkeypatch.setattr(
        manager, "group_status",
        (lambda: {"groupId": GROUP, "members": [{"instId": INST}]}) if running
        else (lambda: None))

    async def provider_cfg(*args, **kwargs):
        return object()

    monkeypatch.setattr("app.ai_auto.manager.resolve_active_provider", provider_cfg)
    monkeypatch.setattr("app.ai_auto.manager.make_llm_client_from_config",
                        lambda *a, **k: object())
    monkeypatch.setattr("app.api.app_settings.get_ai_trade_manager", lambda: manager)
    monkeypatch.setattr("app.api.ai_intel.get_ai_trade_manager", lambda: manager)

    async def forever(_runtime):
        await asyncio.sleep(3600)

    monkeypatch.setattr(intel_scheduler, "_loop", forever)
    return manager, group


# ---------------- G-20：端到端（本轮的原命题） ----------------
@pytest.mark.asyncio
async def test_saving_the_provider_arms_the_running_group_right_away(monkeypatch):
    """G-20：`PUT /api/settings/intel` 之后，同一个进程里流水线**当场**装上。"""
    await _fresh()
    manager, group = _running_manager(monkeypatch)
    await manager._wire_intel(group)                # 开跑时还没配源 ⇒ 造出一个壳
    assert intel_runtime.runtime_for(INST).enabled is False

    async with _client() as client:
        res = await client.put("/api/settings/intel",
                               json={"provider": "tavily", "apiKey": "k-live"})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["arm"]["armed"] is True
    assert body["arm"]["reason"] == ""
    assert body["arm"]["pending"] is False
    assert body["arm"]["groupRunning"] is True
    assert body["arm"]["armedMembers"] == [INST]
    assert INST in body["arm"]["message"]
    assert intel_runtime.runtime_for(INST).enabled is True
    assert intel_runtime.runtime_for(INST).deps.search is not None


@pytest.mark.asyncio
async def test_the_panel_says_provider_configured_even_without_any_runtime(monkeypatch):
    """G-8 / G-9：`providerConfigured` 第一次真的只回答「库里配没配」。

    进程里一个 `IntelRuntime` 都没有时它仍然为真（`armed=false` / `armReason="not_running"`）；
    `provider="none"` 时反向回假 —— 旧行为的那一半必须原样保住。
    """
    await _fresh()
    async with _client() as client:
        await client.put("/api/settings/intel", json={"provider": "tavily", "apiKey": "k"})
        summary = (await client.get(f"/api/intel/{INST}")).json()["summary"]
    assert summary["providerConfigured"] is True, "库里配好了就该说配好了，哪怕还没装上"
    assert summary["intelEnabled"] is True
    assert summary["armed"] is False
    assert summary["armReason"] == "not_running"
    assert summary["degrade"]["code"] == "not_running"

    async with _client() as client:
        await client.put("/api/settings/intel", json={"provider": "none"})
        summary = (await client.get(f"/api/intel/{INST}")).json()["summary"]
    assert summary["providerConfigured"] is False
    assert summary["armReason"] == "provider_unset"


# ---------------- G-21：保存永远不该被情报层拖垮 ----------------
@pytest.mark.asyncio
async def test_a_broken_rearm_never_fails_the_save(monkeypatch):
    """G-21：`rearm_intel` 抛任意异常时 `PUT` **仍然 200**，且回执如实说 `arm_failed`。"""
    await _fresh()
    manager, group = _running_manager(monkeypatch)
    await manager._wire_intel(group)

    async def boom(*args, **kwargs):
        raise RuntimeError("provider down")

    monkeypatch.setattr("app.ai_auto.manager.resolve_active_provider", boom)
    async with _client() as client:
        res = await client.put("/api/settings/intel",
                               json={"provider": "tavily", "apiKey": "k"})
    assert res.status_code == 200, "保存设置绝不因情报层失败而失败"
    arm = res.json()["arm"]
    assert arm["reason"] == "arm_failed"
    assert arm["armed"] is False
    assert "provider down" in arm["detail"]
    assert "provider down" in arm["message"]


# ---------------- G-22：手动【重新装配】幂等 ----------------
@pytest.mark.asyncio
async def test_manual_rearm_is_idempotent(monkeypatch):
    """G-22：连调两次 `POST /api/settings/intel/rearm`，第二次 `pending=False`、`armed` 不变。"""
    await _fresh()
    manager, group = _running_manager(monkeypatch)
    async with _client() as client:
        await client.put("/api/settings/intel", json={"provider": "tavily", "apiKey": "k"})
        await manager._wire_intel(group)
        first = (await client.post("/api/settings/intel/rearm")).json()["arm"]
        second = (await client.post("/api/settings/intel/rearm")).json()["arm"]
    assert first["armed"] is True
    assert second["armed"] is True
    assert second["pending"] is False
    assert first["reason"] == second["reason"] == ""


# ---------------- G-26：自救入口必须救得了它唯一要救的那种处境 ----------------
@pytest.mark.asyncio
async def test_manual_rearm_recovers_a_group_whose_wiring_failed(monkeypatch):
    """G-26（评审 P0-3）：`_wire_intel` 失败 ⇒ `_RUNTIMES` 为空、分组仍在跑。

    此时点【重新装配】**必须真的把流水线装起来**，而不是回一句「AI 自动交易未在运行」——那是
    一句在分组正跑着的时候确定错误的话，而自救入口对自己的立项理由束手无策。
    """
    await _fresh()
    await _store_settings()                     # 配置是齐的——装不上不是用户的错
    manager, group = _running_manager(monkeypatch)

    async def build_boom(**kwargs):
        raise RuntimeError("装配期炸了")

    monkeypatch.setattr(intel_runtime, "build_group_runtime", build_boom)
    await manager._wire_intel(group)
    assert intel_runtime.runtime_for(INST) is None, "fail-open 在 register 之前吞的异常"
    assert arming.arm_error_for(GROUP) == "装配期炸了", "被吞掉的是异常，不是原因"

    async with _client() as client:
        failed = (await client.post("/api/settings/intel/rearm")).json()["arm"]
    assert failed["reason"] == "arm_failed", "还装不上的时候要说装不上，不能说没在跑"
    assert failed["groupRunning"] is True
    assert "装配期炸了" in failed["message"]

    monkeypatch.undo()
    _running_manager(monkeypatch)
    async with _client() as client:
        arm = (await client.post("/api/settings/intel/rearm")).json()["arm"]
    assert arm["armed"] is True, "自救入口必须真的把流水线装起来"
    assert intel_runtime.runtime_for(INST) is not None
    assert intel_runtime.runtime_for(INST).enabled is True


@pytest.mark.asyncio
async def test_manual_rearm_says_not_running_when_the_group_really_is_not(monkeypatch):
    """G-26 的反向：分组确实没在跑时仍回 `not_running`（**不是**一句更好听的话）。"""
    await _fresh()
    _running_manager(monkeypatch, running=False)
    async with _client() as client:
        await client.put("/api/settings/intel", json={"provider": "tavily", "apiKey": "k"})
        arm = (await client.post("/api/settings/intel/rearm")).json()["arm"]
    assert arm["reason"] == "not_running"
    assert arm["groupRunning"] is False
    assert arm["armedMembers"] == []
    assert arm["message"] == tax.DEGRADE_TEXT["not_running"]


# ---------------- G-23：409 的四种分叉 ----------------
@pytest.mark.asyncio
async def test_the_409_names_the_real_reason(monkeypatch):
    """G-23：四种未装配处境各回不同的 `error.code`，文案与 `DEGRADE_TEXT` 逐字一致。"""
    await _fresh()
    _running_manager(monkeypatch, running=False)
    cases = [
        ({"provider": "none"}, "intel_provider_unset", "provider_unset"),
        ({"provider": "tavily", "apiKey": "__clear__"}, "intel_key_missing", "key_missing"),
        ({"provider": "tavily", "apiKey": "k", "enabled": False}, "intel_disabled", "disabled"),
    ]
    async with _client() as client:
        for patch, code, degrade_code in cases:
            await client.put("/api/settings/intel", json=patch)
            res = await client.post(f"/api/intel/{INST}/collect")
            assert res.status_code == 409, patch
            body = res.json()["error"]
            assert body["code"] == code, patch
            assert body["message"] == tax.DEGRADE_TEXT[degrade_code]
    # 配置齐、分组在跑、却没有 runtime ⇒ `arm_failed`（fail-open 吞掉的那种）。
    await _store_settings(enabled=True)
    manager, group = _running_manager(monkeypatch, running=True)

    async def build_boom(**kwargs):
        raise RuntimeError("provider down")

    monkeypatch.setattr(intel_runtime, "build_group_runtime", build_boom)
    await manager._wire_intel(group)
    async with _client() as client:
        res = await client.post(f"/api/intel/{INST}/collect")
    assert res.status_code == 409
    body = res.json()["error"]
    assert body["code"] == "intel_arm_failed"
    assert "provider down" in body["message"]
    # R-11：`detail` 是异常文本，绝不允许把密钥带进这条 409。
    for needle in ("apiKey", "sk-", "k-live"):
        assert needle not in body["message"]


# ---------------- G-31：pending 期间两个键不许说相反的话 ----------------
@pytest.mark.asyncio
async def test_pending_rearm_never_contradicts_itself(monkeypatch):
    """G-31（评审 P1-8）：`summary.armReason` 与 `summary.degrade.code` **同为** `arm_pending`。

    把 pending 做成与 `_arm` 并列的第二个字段，就会让不传 pending 的那条通路说 `armReason=""`
    （一切正常）、而缓存那条说 `degrade.code="arm_pending"`（正在生效中）——同一份 wire 的两个键
    说两句相反的话，且不报错。
    """
    await _fresh()
    manager, group = _running_manager(monkeypatch)
    async with _client() as client:
        await client.put("/api/settings/intel", json={"provider": "tavily", "apiKey": "k"})
    await manager._wire_intel(group)
    runtime = intel_runtime.runtime_for(INST)
    assert runtime is not None
    runtime.mark_arm_pending()
    async with _client() as client:
        summary = (await client.get(f"/api/intel/{INST}")).json()["summary"]
        arm = (await client.get("/api/settings/intel")).json()["arm"]
    assert summary["armReason"] == "arm_pending"
    assert summary["degrade"]["code"] == "arm_pending"
    assert arm["reason"] == "arm_pending"
    assert arm["pending"] is True


@pytest.mark.asyncio
async def test_a_stale_shell_never_contradicts_the_fresh_arm_reason(monkeypatch):
    """实施发现 I-9：缓存里的 `degrade` 是**装配期**打的戳，它会过期。

    一个开跑时没配源的壳，`_arm` 恒是 `provider_unset`；此刻库里可能早已配齐、只是重装失败了。
    无条件让缓存赢，就会出现 `armReason="arm_failed"` 而 `degrade.code="provider_unset"`——
    同一份 wire 的两个键说两句相反的话，正是本轮立项要消灭的东西。
    """
    await _fresh()
    manager, group = _running_manager(monkeypatch)
    await manager._wire_intel(group)                 # 开跑时没配源 ⇒ 壳，`_arm=provider_unset`
    shell = intel_runtime.runtime_for(INST)
    assert shell is not None and shell.arm.reason == "provider_unset"

    await _store_settings()                          # 库里配齐了（不经 PUT ⇒ 不触发重装）
    arming.record_arm_error(GROUP, RuntimeError("LLM provider 未配置"))
    async with _client() as client:
        summary = (await client.get(f"/api/intel/{INST}")).json()["summary"]
    assert summary["providerConfigured"] is True
    assert summary["armReason"] == "arm_failed"
    assert summary["degrade"]["code"] == "arm_failed", "过期的装配期戳绝不许盖住当次算出的结论"
    assert "LLM provider 未配置" in summary["degrade"]["message"]


# ---------------- G-25：两条读 key 通路等价 ----------------
@pytest.mark.asyncio
@pytest.mark.parametrize(("stored_key", "provider"), [
    (None, "tavily"),            # 库内行缺席 ⇒ 回落 env（env 里没有 ⇒ key_missing）
    ("", "tavily"),              # 库内行是空串 ⇒ 同样回落 env
    ("   ", "tavily"),           # 库内行是纯空白 ⇒ key_missing
    ("k-live", "tavily"),
    ("k-live", "none"),
    ("k-live", "bogus"),
])
async def test_the_two_key_paths_never_diverge(stored_key, provider):
    """G-25：`classify_provider(...) == ""` **当且仅当** `resolve_search_provider(...)` 非 None。

    两条通路各读一次 key（一条经 `resolve_intel_settings` 叠进 `Settings` 副本、一条经
    `resolve_api_key` 直读库），分叉了就会重新造出本轮要修的那句假话，而且不报错。
    """
    await _fresh()
    async with get_sessionmaker()() as session:
        await repo.set_app_setting(session, "ai_intel.provider", provider)
        if stored_key is not None:
            await repo.set_app_setting(session, intel_service.INTEL_API_KEY_APP_KEY,
                                       stored_key)
        derived = await intel_service.resolve_intel_settings(session, Settings())
        api_key = await intel_service.resolve_api_key(session, derived)
    verdict = intel_search.classify_provider(
        str(derived.ai_trade_intel_search_provider),
        str(derived.ai_trade_intel_search_api_key or ""))
    runner = intel_search.resolve_search_provider(
        slug=str(derived.ai_trade_intel_search_provider), api_key=api_key)
    assert (verdict == "") is (runner is not None), (
        f"stored_key={stored_key!r} provider={provider!r} verdict={verdict!r}")
