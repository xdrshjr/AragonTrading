"""实盘账户 REST 单测：CRUD / 脱敏铁律 / 设默认唯一性 / 删默认回落 / test 错误码映射 / detail 聚合。

私有客户端全程打桩，不依赖网络。共享临时 DB（conftest 模块级），每用例先清空 okx_accounts。
"""
from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from app.db import repositories as repo
from app.db.session import dispose_engine, get_sessionmaker, init_db
from app.okx.private_client import OKXPrivateError


def _load_app():
    from app.main import app
    return app


async def _fresh() -> None:
    """建表并清空 okx_accounts（确定空表起步，避免跨用例污染）。"""
    await init_db()
    sm = get_sessionmaker()
    async with sm() as session:
        for row in await repo.list_okx_accounts(session):
            await session.delete(row)
        await session.commit()


async def _client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=_load_app()), base_url="http://test")


# 全部为占位假凭证，绝不使用任务下发的真实开发凭证（R-Secret / P2-GitHygiene）。
def _new_body(label="Dev-JR-US", **over) -> dict:
    body = {"label": label, "apiKey": "test-ak-demo-only-c848",
            "secretKey": "test-sk-demo-secret-0001", "passphrase": "test-pp-123456",
            "ipWhitelist": "203.0.113.10", "permissions": "读取/交易"}
    body.update(over)
    return body


class _FakeClient:
    """OKXPrivateClient 桩：按用例注入各方法的返回值或抛出的 OKXPrivateError。"""

    config: dict = {}
    balance: dict = {}
    positions: list = []
    raise_on_config: OKXPrivateError | None = None
    raise_on_any: OKXPrivateError | None = None

    def __init__(self, *args, **kwargs):
        pass

    async def get_account_config(self):
        if self.raise_on_config:
            raise self.raise_on_config
        if self.raise_on_any:
            raise self.raise_on_any
        return self.config

    async def get_balance(self):
        if self.raise_on_any:
            raise self.raise_on_any
        return self.balance

    async def get_positions(self):
        if self.raise_on_any:
            raise self.raise_on_any
        return self.positions

    async def aclose(self):
        pass


def _patch_client(monkeypatch, **attrs) -> None:
    cls = type("_Fake", (_FakeClient,), attrs)
    monkeypatch.setattr("app.okx.accounts.OKXPrivateClient", cls)


async def test_crud_round_trip_and_masking():
    await _fresh()
    try:
        async with await _client() as client:
            created = await client.post("/api/accounts", json=_new_body())
            assert created.status_code == 201, created.text
            row = created.json()
            # 脱敏铁律：响应不含明文 secret/passphrase、不含完整 api_key（仅末 4 位）
            raw = created.text
            assert "test-sk-demo-secret-0001" not in raw
            assert "test-pp-123456" not in raw
            assert "test-ak-demo-only-c848" not in raw
            assert "secretKey" not in row and "passphrase" not in row
            assert row["apiKeyPreview"].endswith("c848")
            assert row["hasSecret"] is True and row["hasPassphrase"] is True
            assert row["isDefault"] is True              # 首账户自动设默认
            aid = row["id"]

            listed = (await client.get("/api/accounts")).json()
            assert [a["id"] for a in listed["accounts"]] == [aid]
            assert listed["defaultId"] == aid

            # PUT 改 label，secret/passphrase 留空保留
            updated = await client.put(f"/api/accounts/{aid}",
                                       json={"label": "改名后", "ipWhitelist": "1.2.3.4"})
            assert updated.status_code == 200, updated.text
            assert updated.json()["label"] == "改名后"
            assert updated.json()["hasSecret"] is True

            deleted = await client.delete(f"/api/accounts/{aid}")
            assert deleted.status_code == 204
            assert (await client.get("/api/accounts")).json()["accounts"] == []
    finally:
        await dispose_engine()


async def test_set_default_keeps_single_and_delete_falls_back():
    await _fresh()
    try:
        async with await _client() as client:
            a = (await client.post("/api/accounts", json=_new_body(label="A"))).json()
            b = (await client.post("/api/accounts", json=_new_body(label="B"))).json()
            # 设 B 为默认 → 全表仅 B 默认
            body = (await client.post(f"/api/accounts/{b['id']}/default")).json()
            assert body["defaultId"] == b["id"]
            defaults = [acc["id"] for acc in body["accounts"] if acc["isDefault"]]
            assert defaults == [b["id"]]
            # 删默认 B → 回落 id 最小者 A
            await client.delete(f"/api/accounts/{b['id']}")
            after = (await client.get("/api/accounts")).json()
            assert after["defaultId"] == a["id"]
    finally:
        await dispose_engine()


async def test_update_and_delete_missing_returns_404():
    await _fresh()
    try:
        async with await _client() as client:
            r = await client.put("/api/accounts/999999", json={"label": "x"})
            assert r.status_code == 404 and r.json()["error"]["code"] == "ACCOUNT_NOT_FOUND"
            r = await client.delete("/api/accounts/999999")
            assert r.status_code == 404 and r.json()["error"]["code"] == "ACCOUNT_NOT_FOUND"
    finally:
        await dispose_engine()


async def test_create_rejects_missing_required_fields():
    await _fresh()
    try:
        async with await _client() as client:
            r = await client.post("/api/accounts", json={"label": "缺密钥"})
            assert r.status_code == 422, r.text
    finally:
        await dispose_engine()


@pytest.mark.parametrize("okx_code,semantic", [
    ("50110", "OKX_IP_DENIED"),
    ("50105", "OKX_AUTH_PASSPHRASE"),
    ("50113", "OKX_AUTH_SIGN"),
    ("50111", "OKX_AUTH_KEY"),
])
async def test_test_route_maps_okx_error_codes(monkeypatch, okx_code, semantic):
    await _fresh()
    _patch_client(monkeypatch,
                  raise_on_config=OKXPrivateError(okx_code, f"okx says {okx_code}"))
    try:
        async with await _client() as client:
            created = (await client.post("/api/accounts", json=_new_body())).json()
            r = await client.post(f"/api/accounts/{created['id']}/test")
            assert r.status_code == 200, r.text
            body = r.json()
            assert body["ok"] is False and body["code"] == semantic
    finally:
        await dispose_engine()


async def test_test_route_ok_returns_latency(monkeypatch):
    await _fresh()
    _patch_client(monkeypatch, config={"uid": "123"})
    try:
        async with await _client() as client:
            created = (await client.post("/api/accounts", json=_new_body())).json()
            r = await client.post(f"/api/accounts/{created['id']}/test")
            assert r.status_code == 200, r.text
            body = r.json()
            assert body["ok"] is True and isinstance(body["latencyMs"], float)
    finally:
        await dispose_engine()


async def test_test_route_redacts_secret_in_message(monkeypatch):
    """对外 message 不得回显 secret / passphrase 明文（C7）。"""
    await _fresh()
    leak = "test-sk-demo-secret-0001 leaked"
    _patch_client(monkeypatch, raise_on_config=OKXPrivateError("50113", leak))
    try:
        async with await _client() as client:
            created = (await client.post("/api/accounts", json=_new_body())).json()
            r = await client.post(f"/api/accounts/{created['id']}/test")
            assert "test-sk-demo-secret-0001" not in r.text
    finally:
        await dispose_engine()


async def test_detail_route_aggregates_normalized_shape(monkeypatch):
    await _fresh()
    _patch_client(
        monkeypatch,
        config={"uid": "98765", "acctLv": "2", "perm": "read_only,trade"},
        balance={"totalEq": "1234.5",
                 "details": [{"ccy": "USDT", "eq": "1000", "availBal": "900",
                              "frozenBal": "100", "eqUsd": "1000"}]},
        positions=[{"instId": "BTC-USDT-SWAP", "instType": "SWAP", "posSide": "long",
                    "pos": "2", "avgPx": "60000", "last": "61000", "upl": "2000",
                    "uplRatio": "0.033", "lever": "10", "mgnMode": "cross"}])
    try:
        async with await _client() as client:
            created = (await client.post("/api/accounts", json=_new_body())).json()
            r = await client.get(f"/api/accounts/{created['id']}/detail")
            assert r.status_code == 200, r.text
            body = r.json()
            assert body["info"]["uid"] == "98765"
            assert body["info"]["acctLevelLabel"] == "单币种保证金模式"
            assert body["info"]["permissions"] == ["read_only", "trade"]
            assert body["balance"]["totalEqUsd"] == 1234.5
            assert body["balance"]["details"][0]["ccy"] == "USDT"
            pos = body["positions"][0]
            assert pos["instId"] == "BTC-USDT-SWAP" and pos["marginMode"] == "cross"
            assert pos["pos"] == 2.0 and pos["upl"] == 2000.0
    finally:
        await dispose_engine()


async def test_detail_route_okx_failure_returns_502(monkeypatch):
    await _fresh()
    _patch_client(monkeypatch, raise_on_any=OKXPrivateError("50110", "ip denied"))
    try:
        async with await _client() as client:
            created = (await client.post("/api/accounts", json=_new_body())).json()
            r = await client.get(f"/api/accounts/{created['id']}/detail")
            assert r.status_code == 502, r.text
            assert r.json()["error"]["code"] == "OKX_IP_DENIED"
    finally:
        await dispose_engine()
