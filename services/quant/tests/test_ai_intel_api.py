"""情报层的十二个端点（§7.1 · test_ai_intel_api）。

全程离线（不出网、不起会话）。四条门禁在这里：写类端点的双闸、`instId` 归属校验、密钥绝不出接口、
以及 G-16「`POST /seen` 之后 `unread` 归零且进程重启后仍为零」。
"""
from __future__ import annotations

import time

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete

from app.ai_auto.intel import runtime as intel_runtime
from app.ai_auto.intel import service as intel_service
from app.config import get_settings
from app.db import repositories as repo
from app.db.models import AiIntelItem, AiIntelRound, AiIntelWatch, AppSetting
from app.db.session import dispose_engine, get_sessionmaker, init_db

INST = "BTC-USDT-SWAP"
NOW = int(time.time())
# 端口同步点：18761 是 `security/origins.DEFAULT_ORIGINS` 里的 Next 前端端口。写别的会让每一条
# 写类断言都变成 403，而 403 与「端点判据坏了」在断言里长得一模一样。
_ORIGIN = {"Origin": "http://localhost:18761"}


def _app():
    from app.main import app
    return app


def _client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=_app()), base_url="http://test",
                       headers=_ORIGIN)


async def _fresh(*, watchlist=(INST,)) -> None:
    await init_db()
    sm = get_sessionmaker()
    async with sm() as session:
        for table in (AiIntelItem, AiIntelRound, AiIntelWatch):
            await session.execute(delete(table))
        await session.execute(delete(AppSetting))
        await session.commit()
        if watchlist:
            await repo.set_workspace_watchlist(session, watchlist=list(watchlist), members=[],
                                               allocation_mode="even", symbol_groups=[])
    intel_runtime._RUNTIMES.clear()
    intel_runtime._BY_INST.clear()


def _item_body(**over) -> dict:
    body = {"tier": "ultra", "category": "macro", "headline": "手工记一条",
            "summary": "美联储议息会议将于下周举行。", "stance": "bearish", "impact": "medium",
            "eventTs": NOW + 3 * 86400,
            "sources": [{"url": "https://coindesk.com/a", "title": "T"}], "pinned": False}
    body.update(over)
    return body


# ---------------- 读端点 ----------------
@pytest.mark.asyncio
async def test_read_empty_library_is_a_valid_answer():
    await _fresh()
    async with _client() as client:
        res = await client.get(f"/api/intel/{INST}")
    assert res.status_code == 200
    body = res.json()
    assert set(body["tiers"]) == {"ultra", "mid", "long"}
    assert body["summary"]["counts"] == {"ultra": 0, "mid": 0, "long": 0, "archived": 0}
    assert body["summary"]["providerConfigured"] is False


@pytest.mark.asyncio
async def test_read_is_not_gated_by_the_ai_switch():
    """G-4 要保住的正是「停掉自动交易之后仍能查看」——读端点不设总闸。"""
    await _fresh()
    settings = get_settings()
    original = settings.ai_trade_enabled
    object.__setattr__(settings, "ai_trade_enabled", False)
    try:
        async with _client() as client:
            assert (await client.get(f"/api/intel/{INST}")).status_code == 200
    finally:
        object.__setattr__(settings, "ai_trade_enabled", original)


# ---------------- CRUD ----------------
@pytest.mark.asyncio
async def test_create_patch_move_delete_roundtrip():
    await _fresh()
    async with _client() as client:
        created = await client.post(f"/api/intel/{INST}/items", json=_item_body())
        assert created.status_code == 201, created.text
        item = created.json()["item"]
        assert item["origin"] == "user"
        assert item["userEdited"] is True, "手工新增即免死金牌"
        assert item["isScheduled"] is True, "事件在未来 ⇒ 前向窗准入"
        assert item["expiresTs"] > item["eventTs"], "寿命取 max(event, capture) + TTL"

        patched = await client.patch(f"/api/intel/items/{item['id']}",
                                     json={"headline": "改过的标题"})
        assert patched.status_code == 200
        assert patched.json()["item"]["headline"] == "改过的标题"

        moved = await client.post(f"/api/intel/items/{item['id']}/move", json={"tier": "long"})
        assert moved.status_code == 200
        moved_item = moved.json()["item"]
        assert moved_item["tier"] == "long"
        # 不变式 I-3：移层必须在同一事务内重算寿命，否则整座库会在超短 TTL 后被扫空。
        assert moved_item["expiresTs"] > item["expiresTs"] + 300 * 86400

        assert (await client.delete(f"/api/intel/items/{item['id']}")).status_code == 204
        assert (await client.get(f"/api/intel/{INST}")).json()["tiers"]["long"]["total"] == 0


@pytest.mark.asyncio
async def test_pinning_makes_the_item_immortal():
    await _fresh()
    async with _client() as client:
        item = (await client.post(f"/api/intel/{INST}/items",
                                  json=_item_body())).json()["item"]
        pinned = await client.patch(f"/api/intel/items/{item['id']}", json={"pinned": True})
    assert pinned.json()["item"]["expiresTs"] == 0, "置顶恒不过期（寿命必须随 pinned 重算）"


@pytest.mark.asyncio
async def test_patch_and_delete_404_for_unknown_item():
    await _fresh()
    async with _client() as client:
        assert (await client.patch("/api/intel/items/9999",
                                   json={"pinned": True})).status_code == 404
        assert (await client.delete("/api/intel/items/9999")).status_code == 404


@pytest.mark.asyncio
async def test_empty_patch_is_422():
    """静默地「保存成功但什么都没改」是本仓库明令禁止的形状。"""
    await _fresh()
    async with _client() as client:
        item = (await client.post(f"/api/intel/{INST}/items",
                                  json=_item_body())).json()["item"]
        res = await client.patch(f"/api/intel/items/{item['id']}", json={})
    assert res.status_code == 422


@pytest.mark.asyncio
async def test_non_http_source_url_is_422():
    await _fresh()
    async with _client() as client:
        res = await client.post(f"/api/intel/{INST}/items",
                                json=_item_body(sources=[{"url": "javascript:alert(1)"}]))
    assert res.status_code == 422


# ---------------- 双闸与归属校验 ----------------
@pytest.mark.asyncio
async def test_write_endpoints_403_when_ai_trade_disabled():
    await _fresh()
    settings = get_settings()
    original = settings.ai_trade_enabled
    object.__setattr__(settings, "ai_trade_enabled", False)
    try:
        async with _client() as client:
            for res in (await client.post(f"/api/intel/{INST}/items", json=_item_body()),
                        await client.post(f"/api/intel/{INST}/collect"),
                        await client.post(f"/api/intel/{INST}/seen", json={})):
                assert res.status_code == 403, res.text
    finally:
        object.__setattr__(settings, "ai_trade_enabled", original)


@pytest.mark.asyncio
async def test_unknown_inst_rejected_on_write_only():
    """评审 P2-11：没有这道校验，任意字符串都能建出永远不会被采集也不会被清理的孤儿标的。"""
    await _fresh(watchlist=(INST,))
    async with _client() as client:
        assert (await client.get("/api/intel/FAKE-USDT-SWAP")).status_code == 200
        res = await client.post("/api/intel/FAKE-USDT-SWAP/items", json=_item_body())
    assert res.status_code == 422
    assert res.json()["error"]["code"] == "intel_unknown_inst"


@pytest.mark.asyncio
async def test_collect_409_when_no_provider():
    """诚实降级：未配置检索源时**绝不假装在采**。"""
    await _fresh()
    async with _client() as client:
        res = await client.post(f"/api/intel/{INST}/collect")
    assert res.status_code == 409
    assert res.json()["error"]["code"] == "intel_no_provider"


# ---------------- 未读徽标（G-16 / 评审 P1-2） ----------------
@pytest.mark.asyncio
async def test_seen_resets_unread_across_restart():
    await _fresh()
    async with _client() as client:
        await client.post(f"/api/intel/{INST}/items", json=_item_body())
        before = (await client.get(f"/api/intel/{INST}")).json()["summary"]
        assert before["unread"] == 1
        seen = await client.post(f"/api/intel/{INST}/seen", json={})
        assert seen.status_code == 200
        assert seen.json()["summary"]["unread"] == 0
    # —— 模拟进程重启：弃掉引擎与进程内注册表，只留磁盘 ——
    await dispose_engine()
    intel_runtime._RUNTIMES.clear()
    intel_runtime._BY_INST.clear()
    await init_db()
    async with _client() as client:
        after = (await client.get(f"/api/intel/{INST}")).json()
    assert after["summary"]["unread"] == 0, "未读的真值必须在服务端，而不是前端 localStorage"
    assert after["tiers"]["ultra"]["total"] == 1, "G-4：情报库跨进程原样还在"


@pytest.mark.asyncio
async def test_read_summary_counts_are_fresh_while_runtime_flags_come_from_the_runtime():
    """库存计数取**当次查库**，轮级瞬态取运行时缓存。

    整份返回缓存的后果：用户新增一条之后，`invalidate` 只是**排了一个**异步刷新任务，而弹窗紧接着
    的这次 `GET` 会拿到尚未刷新的那一份——列表里已经有新条目、页签计数却还是旧的。反过来整份查库
    也不对：`degrade` 与 `providerConfigured` 在库里根本没有落点。

    用**未配置检索源的 runtime 壳**来验：它的 `refresh` 不查库、不会把静态降级态刷掉，故这条断言
    是确定性的（换成 enabled 的 runtime 会与它自己触发的异步刷新赛跑）。
    """
    await _fresh()
    deps = intel_service.IntelDeps(settings=get_settings(), sessionmaker=get_sessionmaker,
                                   group_id="grp-1")
    shell = intel_runtime.IntelRuntime("grp-1", [INST], deps, enabled=False)
    intel_runtime.register(shell)
    try:
        async with _client() as client:
            await client.post(f"/api/intel/{INST}/items", json=_item_body())
            summary = (await client.get(f"/api/intel/{INST}")).json()["summary"]
        assert summary["counts"]["ultra"] == 1, "刚加的那一条必须立刻计进页签，不能等下一次刷新"
        assert summary["unread"] == 1, "未读同样是库存事实，必须查库"
        assert summary["degrade"]["code"] == "no_provider", "降级在库里没有落点，只能取运行时"
        assert summary["providerConfigured"] is False
    finally:
        intel_runtime.unregister("grp-1")


# ---------------- 轮次 ----------------
@pytest.mark.asyncio
async def test_rounds_endpoint_returns_audit_stubs():
    await _fresh()
    async with get_sessionmaker()() as session:
        await repo.write_intel_round(session, {
            "instId": INST, "kind": "collect", "startedTs": NOW, "finishedTs": NOW,
            "status": "degraded", "degradeCode": "all_rejected",
            "rejects": {"bad_ref": 2}, "docCount": 6})
    async with _client() as client:
        body = (await client.get(f"/api/intel/{INST}/rounds")).json()
    assert body["rounds"][0]["degradeCode"] == "all_rejected"
    assert body["rounds"][0]["rejects"] == {"bad_ref": 2}


# ---------------- 设置端点 ----------------
@pytest.mark.asyncio
async def test_get_settings_never_returns_key():
    await _fresh()
    async with _client() as client:
        saved = await client.put("/api/settings/intel",
                                 json={"provider": "tavily", "apiKey": "tvly-super-secret-1234"})
        assert saved.status_code == 200
        body = saved.json()
        read = (await client.get("/api/settings/intel")).json()
    for payload in (body, read):
        assert "tvly-super-secret-1234" not in str(payload)
        assert payload["hasKey"] is True
        assert payload["keyPreview"].endswith("1234")
        assert payload["provider"] == "tavily"
    assert read["estimate"]["roundsPerDay"] > 0, "成本估算必须在按下开关之前就看得见"


@pytest.mark.asyncio
async def test_settings_api_key_three_states():
    """`""`/缺席 = 保持不变；`"__clear__"` = 清除；其余 = 写入。"""
    await _fresh()
    async with _client() as client:
        await client.put("/api/settings/intel", json={"apiKey": "tvly-abcdefgh"})
        kept = (await client.put("/api/settings/intel",
                                 json={"collectIntervalMin": 60})).json()
        assert kept["hasKey"] is True and kept["collectIntervalMin"] == 60.0
        cleared = (await client.put("/api/settings/intel", json={"apiKey": "__clear__"})).json()
        assert cleared["hasKey"] is False


@pytest.mark.asyncio
async def test_settings_reject_out_of_range_values():
    await _fresh()
    async with _client() as client:
        assert (await client.put("/api/settings/intel",
                                 json={"collectIntervalMin": 1})).status_code == 422
        assert (await client.put("/api/settings/intel",
                                 json={"dailyCap": 0})).status_code == 422


@pytest.mark.asyncio
async def test_probe_without_config_is_honest():
    await _fresh()
    async with _client() as client:
        res = await client.post("/api/settings/intel/probe", json={})
    assert res.status_code == 200
    assert res.json()["ok"] is False
