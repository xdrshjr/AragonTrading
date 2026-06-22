"""AI 模型服务商 REST 单测：列表脱敏 / 建自定义 / PUT 空 key 保留 / 删官方拒绝 / 设默认 / test。"""
from __future__ import annotations

from httpx import ASGITransport, AsyncClient

from app.agent.llm.base import StreamEvent
from app.db import repositories as repo
from app.db.session import dispose_engine, get_sessionmaker, init_db


def _load_app():
    from app.main import app
    return app


async def _reseed() -> None:
    """共享临时 DB：清空 providers 后重新种官方+KIMI（确定起步，避免跨用例污染）。"""
    await init_db()
    sm = get_sessionmaker()
    async with sm() as session:
        for row in await repo.list_agent_providers(session):
            await session.delete(row)
        await session.commit()
    await init_db()


async def _client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=_load_app()), base_url="http://test")


async def test_list_is_masked_and_has_seeded_providers():
    await _reseed()
    try:
        async with await _client() as client:
            r = await client.get("/api/agent/providers")
            assert r.status_code == 200, r.text
            body = r.json()
            slugs = {p["slug"] for p in body["providers"]}
            assert {"openai", "anthropic", "gemini", "kimi"} <= slugs
            for p in body["providers"]:
                assert "apiKey" not in p          # 绝不返回原始 key
                assert "hasKey" in p and "keyPreview" in p
            assert body["active"] is not None
    finally:
        await dispose_engine()


async def test_create_custom_then_put_empty_key_preserves():
    await _reseed()
    try:
        async with await _client() as client:
            r = await client.post("/api/agent/providers", json={
                "label": "我的网关", "apiStandard": "openai",
                "baseUrl": "https://gw.test/v1/chat/completions",
                "apiKey": "sk-abcd1234", "model": "gpt-x"})
            assert r.status_code == 201, r.text
            created = r.json()
            assert "apiKey" not in created and created["hasKey"] is True
            assert created["keyPreview"].endswith("1234")
            pid = created["id"]

            # PUT 只改 label、不带 apiKey → key 保留（hasKey 仍 True，preview 不变）
            r = await client.put(f"/api/agent/providers/{pid}", json={"label": "改名后"})
            assert r.status_code == 200, r.text
            updated = r.json()
            assert updated["label"] == "改名后" and updated["hasKey"] is True
            assert updated["keyPreview"].endswith("1234")
    finally:
        await dispose_engine()


async def test_create_rejects_invalid_base_url():
    await _reseed()
    try:
        async with await _client() as client:
            r = await client.post("/api/agent/providers", json={
                "label": "坏url", "apiStandard": "openai", "baseUrl": "ftp://nope",
                "apiKey": "k", "model": "m"})
            assert r.status_code == 400
            assert r.json()["error"]["code"] == "INVALID_BASE_URL"
    finally:
        await dispose_engine()


async def test_delete_official_returns_400():
    await _reseed()
    try:
        async with await _client() as client:
            providers = (await client.get("/api/agent/providers")).json()["providers"]
            kimi = next(p for p in providers if p["slug"] == "kimi")
            r = await client.delete(f"/api/agent/providers/{kimi['id']}")
            assert r.status_code == 400
            assert r.json()["error"]["code"] == "PROVIDER_NOT_DELETABLE"
    finally:
        await dispose_engine()


async def test_delete_missing_returns_404():
    await _reseed()
    try:
        async with await _client() as client:
            r = await client.delete("/api/agent/providers/999999")
            assert r.status_code == 404
            assert r.json()["error"]["code"] == "PROVIDER_NOT_FOUND"
    finally:
        await dispose_engine()


async def test_set_default_updates_active_summary():
    await _reseed()
    try:
        async with await _client() as client:
            created = (await client.post("/api/agent/providers", json={
                "label": "默认目标", "apiStandard": "openai",
                "baseUrl": "https://d.test", "apiKey": "sk-zzzz9999", "model": "dm"})).json()
            pid = created["id"]
            r = await client.post(f"/api/agent/providers/{pid}/default")
            assert r.status_code == 200, r.text
            body = r.json()
            assert body["defaultId"] == pid
            assert body["active"]["providerId"] == pid
            assert body["active"]["configured"] is True and body["active"]["model"] == "dm"
    finally:
        await dispose_engine()


async def test_test_endpoint_uses_probe(monkeypatch):
    await _reseed()

    class _OkLLM:
        async def stream(self, *, system, messages, tools):
            yield StreamEvent(kind="message_done", stop_reason="end_turn")

    monkeypatch.setattr("app.agent.providers.make_llm_client_from_config", lambda cfg: _OkLLM())
    try:
        async with await _client() as client:
            created = (await client.post("/api/agent/providers", json={
                "label": "测连通", "apiStandard": "openai", "baseUrl": "https://t.test",
                "apiKey": "sk-test1234", "model": "tm"})).json()
            r = await client.post(f"/api/agent/providers/{created['id']}/test")
            assert r.status_code == 200, r.text
            assert r.json()["ok"] is True

            # 草稿 test：直接带表单字段
            r = await client.post("/api/agent/providers/test", json={
                "apiStandard": "openai", "baseUrl": "https://t.test",
                "apiKey": "sk-draft999", "model": "tm"})
            assert r.status_code == 200 and r.json()["ok"] is True
    finally:
        await dispose_engine()


async def test_create_rejects_overlong_label():
    """H3：超长 label（>128）→ 422（FastAPI 校验体）。"""
    await _reseed()
    try:
        async with await _client() as client:
            r = await client.post("/api/agent/providers", json={
                "label": "x" * 200, "apiStandard": "openai",
                "baseUrl": "https://ok.test", "apiKey": "k", "model": "m"})
            assert r.status_code == 422, r.text
    finally:
        await dispose_engine()


async def test_create_rejects_overlong_base_url():
    """H3：超长 baseUrl（>512）→ 422。"""
    await _reseed()
    try:
        async with await _client() as client:
            r = await client.post("/api/agent/providers", json={
                "label": "ok", "apiStandard": "openai",
                "baseUrl": "https://x.test/" + "a" * 600, "apiKey": "k", "model": "m"})
            assert r.status_code == 422, r.text
    finally:
        await dispose_engine()


async def test_put_rejects_explicit_empty_label():
    """H3：PUT 显式给空 label → 422（缺省=不改语义不变，仅拒显式空串）。"""
    await _reseed()
    try:
        async with await _client() as client:
            created = (await client.post("/api/agent/providers", json={
                "label": "原名", "apiStandard": "openai", "baseUrl": "https://ok.test",
                "apiKey": "sk-abcd1234", "model": "m"})).json()
            r = await client.put(f"/api/agent/providers/{created['id']}", json={"label": ""})
            assert r.status_code == 422, r.text
    finally:
        await dispose_engine()


async def test_put_rejects_explicit_empty_model():
    """H3：PUT 显式给空 model → 422。"""
    await _reseed()
    try:
        async with await _client() as client:
            created = (await client.post("/api/agent/providers", json={
                "label": "原名", "apiStandard": "openai", "baseUrl": "https://ok.test",
                "apiKey": "sk-abcd1234", "model": "m"})).json()
            r = await client.put(f"/api/agent/providers/{created['id']}", json={"model": ""})
            assert r.status_code == 422, r.text
    finally:
        await dispose_engine()


async def test_set_default_to_keyless_marks_unconfigured():
    """H1 闭环：把缺 key 服务设为默认 → active.configured=false、providerId=null（不静默用 env）。"""
    await _reseed()
    try:
        async with await _client() as client:
            created = (await client.post("/api/agent/providers", json={
                "label": "无key目标", "apiStandard": "openai",
                "baseUrl": "https://nokey.test", "apiKey": "", "model": "nm"})).json()
            pid = created["id"]
            r = await client.post(f"/api/agent/providers/{pid}/default")
            assert r.status_code == 200, r.text
            body = r.json()
            assert body["defaultId"] == pid               # 默认指向该缺 key 行
            assert body["active"]["configured"] is False  # 但解析为「未配置」
            assert body["active"]["providerId"] is None
    finally:
        await dispose_engine()


async def test_draft_test_empty_key_reports_not_configured():
    await _reseed()
    try:
        async with await _client() as client:
            r = await client.post("/api/agent/providers/test", json={
                "apiStandard": "openai", "baseUrl": "https://t.test", "apiKey": "", "model": "tm"})
            assert r.status_code == 200
            assert r.json() == {"ok": False, "latencyMs": None, "model": None,
                                "code": "AGENT_NOT_CONFIGURED", "message": "未填写密钥"}
    finally:
        await dispose_engine()
