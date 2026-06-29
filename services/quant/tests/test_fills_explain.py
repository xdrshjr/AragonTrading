"""成交 ↔ 触发解释关联单测（live-fills-explainability §六）。

纯函数（index_trade_explains / attach_explains / _normalize_fill）不依赖 DB / 网络；端点富化测试在
``app.api.trading.fetch_live_fills`` 边界打桩注入 fills，并在共享临时 DB 写真实解释 trade 行验证关联。
**铁律**：一律用占位假凭证，严禁写入真实开发凭证（沿用 test_trading_api 规约）。
"""
from __future__ import annotations

import json
from types import SimpleNamespace

from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete

from app.config import Settings, get_settings
from app.db import repositories as repo
from app.db.models import Trade
from app.db.session import dispose_engine, get_sessionmaker, init_db
from app.live_auto.fills_explain import attach_explains, index_trade_explains
from app.okx.trading import _normalize_fill


# ============================ 纯函数：index_trade_explains ============================
def _explain(*, cl_ord_id: str = "aragonX", ord_id: str = "ord-1",
             headline: str = "复合得分多头", with_meta: bool = True) -> dict:
    explain: dict = {
        "schema": 1, "kind": "factor_score", "side": "long", "headline": headline,
        "metrics": [{"label": "复合得分", "value": "0.42"}],
    }
    if with_meta:
        explain["meta"] = {"clOrdId": cl_ord_id, "ordId": ord_id, "strategyName": "demo"}
    return explain


def _trade(explain: dict | None) -> SimpleNamespace:
    """轻量 trade 桩：index_trade_explains 仅经 getattr 读 explain_json。"""
    raw = json.dumps(explain, ensure_ascii=False) if explain is not None else None
    return SimpleNamespace(explain_json=raw)


def test_index_maps_both_clordid_and_ordid_to_same_explain():
    explain = _explain(cl_ord_id="aragonA", ord_id="ord-A")
    index = index_trade_explains([_trade(explain)])
    assert index["aragonA"]["headline"] == "复合得分多头"
    assert index["ord-A"] is index["aragonA"]


def test_index_keeps_newest_on_duplicate_key():
    """输入按 ts 倒序：同 clOrdId 第一条（最新）保留，后续跳过。"""
    newest = _trade(_explain(cl_ord_id="dup", ord_id="o-new", headline="NEW"))
    older = _trade(_explain(cl_ord_id="dup", ord_id="o-old", headline="OLD"))
    index = index_trade_explains([newest, older])
    assert index["dup"]["headline"] == "NEW"


def test_index_skips_bad_json_empty_and_missing_meta():
    bad = SimpleNamespace(explain_json="{not valid json")
    empty = SimpleNamespace(explain_json=None)
    no_meta = _trade(_explain(with_meta=False))
    empty_keys = _trade(_explain(cl_ord_id="", ord_id=""))
    index = index_trade_explains([bad, empty, no_meta, empty_keys])
    assert index == {}


# ============================ 纯函数：attach_explains ============================
def _fill(**over) -> dict:
    base = {"ts": 1_751_200_000, "instId": "BTC-USDT-SWAP", "side": "buy",
            "fillPx": 64210.5, "fillSz": 0.03, "fee": -0.01, "ordId": "ord-1",
            "clOrdId": "aragonA", "tradeId": "t-1"}
    base.update(over)
    return base


def test_attach_hits_by_clordid_primary():
    index = {"aragonA": _explain(cl_ord_id="aragonA")}
    out = attach_explains([_fill()], index)
    assert out[0]["explain"]["kind"] == "factor_score"


def test_attach_falls_back_to_ordid_secondary():
    """clOrdId 不中、ordId 中 → 走副键命中（覆盖市价平仓 clOrdId 为空场景的反向亦然）。"""
    index = {"ord-1": _explain(ord_id="ord-1")}
    out = attach_explains([_fill(clOrdId="")], index)
    assert out[0]["explain"] is not None


def test_attach_leaves_none_when_no_match():
    out = attach_explains([_fill(clOrdId="nope", ordId="nope")], {"other": _explain()})
    assert out[0]["explain"] is None


def test_attach_shares_explain_across_partial_fills():
    """同 clOrdId 的两条 partial fill → 都挂同一 explain（语义：同单多成交同因）。"""
    index = {"aragonA": _explain(cl_ord_id="aragonA")}
    out = attach_explains([_fill(tradeId="t-1"), _fill(tradeId="t-2", fillSz=0.02)], index)
    assert out[0]["explain"] is not None and out[1]["explain"] is not None


def test_attach_does_not_mutate_input_fills():
    original = _fill()
    attach_explains([original], {})
    assert "explain" not in original


# ============================ _normalize_fill 连接键回带 ============================
def test_normalize_fill_carries_clordid_and_tradeid():
    out = _normalize_fill({
        "fillTime": "1751200000000", "instId": "BTC-USDT-SWAP", "side": "buy",
        "fillPx": "64210.5", "fillSz": "0.03", "fee": "-0.01",
        "ordId": "o1", "clOrdId": "aragonx", "tradeId": "t1"})
    assert out["clOrdId"] == "aragonx"
    assert out["tradeId"] == "t1"
    assert out["ts"] == 1_751_200_000


def test_normalize_fill_defaults_missing_join_keys_to_empty():
    out = _normalize_fill({"instId": "BTC-USDT-SWAP", "side": "sell"})
    assert out["clOrdId"] == ""
    assert out["tradeId"] == ""


# ============================ 端点富化（best-effort，DB） ============================
_ACCOUNT_BODY = {"label": "Dev-JR-US", "apiKey": "test-ak-demo-only-c848",
                 "secretKey": "test-sk-demo-secret-0001", "passphrase": "test-pp-123456",
                 "ipWhitelist": "203.0.113.10", "permissions": "读取/交易"}


def _load_app():
    from app.main import app
    return app


async def _fresh() -> None:
    """建表并清空 trades + okx_accounts（确定空表起步；共享临时 DB）。"""
    await init_db()
    sm = get_sessionmaker()
    async with sm() as session:
        await session.execute(delete(Trade))
        for row in await repo.list_okx_accounts(session):
            await session.delete(row)
        await session.commit()


def _override_settings(app) -> None:
    app.dependency_overrides[get_settings] = lambda: Settings(live_trading_enabled=True)


async def _client(app) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def _seed_account(client) -> int:
    created = await client.post("/api/accounts", json=_ACCOUNT_BODY)
    assert created.status_code == 201, created.text
    return created.json()["id"]


def _fake_fetch(fills: list[dict]):
    async def _inner(creds, inst_id=None, limit=100):
        return fills
    return _inner


async def _seed_explained_trade(*, cl_ord_id: str, ts: int, ord_id: str = "ord-1") -> None:
    sm = get_sessionmaker()
    explain = _explain(cl_ord_id=cl_ord_id, ord_id=ord_id)
    async with sm() as session:
        session.add(Trade(run_id=1, ts=ts, side="long", price=64000.0, qty=0.03, fee=0.0,
                          pnl=0.0, position_after=0.03, reason="factor_score",
                          explain_json=json.dumps(explain, ensure_ascii=False)))
        await session.commit()


def _fill_row(**over) -> dict:
    base = {"ts": 1_751_200_000, "instId": "BTC-USDT-SWAP", "side": "buy", "fillPx": 64210.5,
            "fillSz": 0.03, "fee": -0.01, "ordId": "ord-1", "clOrdId": "aragonjoin", "tradeId": "t-1"}
    base.update(over)
    return base


async def test_endpoint_joins_explain_across_one_bar_period_gap(monkeypatch):
    """RV1/P0 回归铁律：解释 trade 的确认根开盘 ts 比成交 ts 早一个 bar 周期（900s）仍命中。"""
    await _fresh()
    app = _load_app()
    _override_settings(app)
    fill_ts = 1_751_200_000
    monkeypatch.setattr("app.api.trading.fetch_live_fills",
                        _fake_fetch([_fill_row(ts=fill_ts, clOrdId="aragonjoin")]))
    await _seed_explained_trade(cl_ord_id="aragonjoin", ts=fill_ts - 900)
    try:
        async with await _client(app) as client:
            await _seed_account(client)
            r = await client.get("/api/trading/fills", params={"instId": "BTC-USDT-SWAP"})
            assert r.status_code == 200, r.text
            fills = r.json()["fills"]
            assert len(fills) == 1
            assert fills[0]["explain"] is not None
            assert fills[0]["explain"]["kind"] == "factor_score"
            # 解释富化不得回显凭证（沿用 explain 不含凭证不变量）。
            assert "test-sk-demo-secret-0001" not in r.text
            assert "test-pp-123456" not in r.text
    finally:
        app.dependency_overrides.clear()
        await dispose_engine()


async def test_endpoint_returns_unenriched_fills_when_join_query_raises(monkeypatch):
    """解释行查询抛异常 → 端点仍 200 返回未富化 fills（best-effort，R1），不 500。"""
    await _fresh()
    app = _load_app()
    _override_settings(app)
    monkeypatch.setattr("app.api.trading.fetch_live_fills", _fake_fetch([_fill_row()]))

    async def _boom(*args, **kwargs):
        raise RuntimeError("db down")

    monkeypatch.setattr("app.db.repositories.list_explained_trades", _boom)
    try:
        async with await _client(app) as client:
            await _seed_account(client)
            r = await client.get("/api/trading/fills", params={"instId": "BTC-USDT-SWAP"})
            assert r.status_code == 200, r.text
            fills = r.json()["fills"]
            assert len(fills) == 1 and fills[0]["explain"] is None
    finally:
        app.dependency_overrides.clear()
        await dispose_engine()


async def test_endpoint_skips_join_query_when_no_fills(monkeypatch):
    """空 fills 提前返回，不触发解释行查询（RV3）。"""
    await _fresh()
    app = _load_app()
    _override_settings(app)
    monkeypatch.setattr("app.api.trading.fetch_live_fills", _fake_fetch([]))
    calls = {"n": 0}

    async def _counting(*args, **kwargs):
        calls["n"] += 1
        return []

    monkeypatch.setattr("app.db.repositories.list_explained_trades", _counting)
    try:
        async with await _client(app) as client:
            await _seed_account(client)
            r = await client.get("/api/trading/fills", params={"instId": "BTC-USDT-SWAP"})
            assert r.status_code == 200, r.text
            assert r.json()["fills"] == []
            assert calls["n"] == 0
    finally:
        app.dependency_overrides.clear()
        await dispose_engine()
