"""因子路由契约测试：CRUD / evaluate / mine / train / score + 激活取消 + 错误码（monkeypatch rest）。"""
from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from app.okx.types import Candle
from tests.conftest import make_synthetic_candles


class _FakeRest:
    async def health(self) -> bool:
        return True

    async def get_candles(self, inst_id, bar, limit=300, before=None, after=None):
        rows = make_synthetic_candles(min(limit, 300), start_ts=1_700_000_000, step=3600)
        return [Candle(ts=r["ts"], o=r["o"], h=r["h"], l=r["l"], c=r["c"], vol=r["vol"]) for r in rows]


@pytest.fixture
def patch_okx(monkeypatch):
    fake = _FakeRest()
    monkeypatch.setattr("app.api.factors.get_rest_client", lambda: fake)
    return fake


def _load_app():
    from app.main import app
    return app


_RANGE = {"instId": "BTC-USDT-SWAP", "bar": "1H",
          "startTs": 1_700_000_000, "endTs": 1_700_000_000 + 295 * 3600}


async def _client():
    from app.db.session import init_db
    await init_db()
    return AsyncClient(transport=ASGITransport(app=_load_app()), base_url="http://test")


async def test_library_and_factor_crud_roundtrip(patch_okx):
    from app.db.session import dispose_engine
    client = await _client()
    try:
        async with client:
            lib = (await client.get("/api/factors/library")).json()
            assert any(f["key"] == "mom_roc" for f in lib)

            # 内置因子已 seed → 列表非空
            seeded = (await client.get("/api/factors")).json()
            assert len(seeded) >= 12

            created = await client.post("/api/factors", json={
                "factorKey": "mom_roc", "name": "我的动量", "params": {"win": 30},
                "direction": "long"})
            assert created.status_code == 201, created.text
            fid = created.json()["id"]

            # 激活 / 取消（enabled 切换）
            upd = await client.put(f"/api/factors/{fid}", json={"enabled": False})
            assert upd.status_code == 200 and upd.json()["enabled"] is False
            only_enabled = (await client.get("/api/factors?enabled=true")).json()
            assert all(f["enabled"] for f in only_enabled)

            # 自定义可删
            assert (await client.delete(f"/api/factors/{fid}")).status_code == 204
    finally:
        await dispose_engine()


async def test_builtin_factor_delete_409(patch_okx):
    from app.db.session import dispose_engine
    client = await _client()
    try:
        async with client:
            rows = (await client.get("/api/factors")).json()
            builtin = next(f for f in rows if f["builtin"])
            r = await client.delete(f"/api/factors/{builtin['id']}")
            assert r.status_code == 409
            assert r.json()["error"]["code"] == "BUILTIN_READONLY"
    finally:
        await dispose_engine()


async def test_compute_and_evaluate_shapes(patch_okx):
    from app.db.session import dispose_engine
    client = await _client()
    try:
        async with client:
            comp = await client.post("/api/factors/compute",
                                     json={"factorKey": "mom_roc", "params": {"win": 20}, **_RANGE})
            assert comp.status_code == 200, comp.text
            body = comp.json()
            assert body["instId"] == "BTC-USDT-SWAP" and isinstance(body["points"], list)

            ev = await client.post("/api/factors/evaluate",
                                   json={"factorKey": "mom_roc", "direction": "auto", **_RANGE})
            assert ev.status_code == 200, ev.text
            assert ev.json()["direction"] in ("long", "short")
            assert isinstance(ev.json()["horizons"], list)
    finally:
        await dispose_engine()


async def test_mine_and_persist(patch_okx):
    from app.db.session import dispose_engine
    client = await _client()
    try:
        async with client:
            r = await client.post("/api/factors/mine", json={**_RANGE, "topN": 5, "persist": True})
            assert r.status_code == 200, r.text
            data = r.json()
            assert len(data["items"]) <= 5 and isinstance(data["truncated"], bool)
            # persist 落库 source=mined
            mined = (await client.get("/api/factors")).json()
            assert any(f["source"] == "mined" for f in mined)
    finally:
        await dispose_engine()


async def test_empty_range_400(patch_okx):
    from app.db.session import dispose_engine
    client = await _client()
    try:
        async with client:
            far = {"instId": "BTC-USDT-SWAP", "bar": "1H",
                   "startTs": 100, "endTs": 100 + 100 * 3600}
            r = await client.post("/api/factors/compute", json={"factorKey": "mom_roc", **far})
            assert r.status_code == 400
            assert r.json()["error"]["code"] == "EMPTY_RANGE"
    finally:
        await dispose_engine()


async def test_train_and_factor_set_lifecycle(patch_okx):
    from app.db.session import dispose_engine
    client = await _client()
    members = [
        {"factorKey": "mom_roc", "params": {"win": 20}, "direction": "long"},
        {"factorKey": "rev_rsi", "params": {}, "direction": "short"},
    ]
    try:
        async with client:
            trained = await client.post("/api/factor-sets/train",
                                        json={"members": members, "method": "linear",
                                              "horizon": 6, **_RANGE})
            assert trained.status_code == 200, trained.text
            tr = trained.json()
            assert tr["available"] is True and "icIn" in tr["metrics"]

            saved = await client.post("/api/factor-sets", json={
                "name": "我的组合", "members": members, "method": "linear",
                "modelJson": {"weights": tr["weights"]}, "metricsJson": tr["metrics"],
                "config": {"thresholds": {"normWindow": 80}}})
            assert saved.status_code == 201, saved.text
            sid = saved.json()["id"]

            score = await client.post(f"/api/factor-sets/{sid}/score", json=_RANGE)
            assert score.status_code == 200, score.text
            assert "latest" in score.json() and isinstance(score.json()["points"], list)

            assert (await client.delete(f"/api/factor-sets/{sid}")).status_code == 204
    finally:
        await dispose_engine()


async def test_train_unavailable_returns_400(patch_okx, monkeypatch):
    """缺 xgboost 库 → 400 FACTOR_TRAINER_UNAVAILABLE（绝不回假权重）。"""
    from app.db.session import dispose_engine
    from app.engine.factors import training as training_mod
    monkeypatch.setattr(training_mod.importlib.util, "find_spec", lambda name: None)
    client = await _client()
    members = [{"factorKey": "mom_roc", "direction": "long"},
               {"factorKey": "rev_rsi", "direction": "short"}]
    try:
        async with client:
            r = await client.post("/api/factor-sets/train",
                                  json={"members": members, "method": "xgboost", **_RANGE})
            assert r.status_code == 400
            assert r.json()["error"]["code"] == "FACTOR_TRAINER_UNAVAILABLE"
    finally:
        await dispose_engine()


async def test_invalid_member_direction_rejected(patch_okx):
    """组合成员方向必须冻结 long/short：auto 在边界被拒（422 校验，评审 C-P1-1）。"""
    from app.db.session import dispose_engine
    client = await _client()
    try:
        async with client:
            r = await client.post("/api/factor-sets", json={
                "name": "bad", "members": [{"factorKey": "mom_roc", "direction": "auto"}]})
            assert r.status_code == 422
    finally:
        await dispose_engine()


_TWO_MEMBERS = [{"factorKey": "mom_roc", "params": {"win": 20}, "direction": "long"},
                {"factorKey": "rev_rsi", "params": {}, "direction": "short"}]


async def test_train_returns_provenance(patch_okx):
    """训练响应带溯源元数据（标的/周期/区间/horizon），供判断权重是否陈旧（H7）。"""
    from app.db.session import dispose_engine
    client = await _client()
    try:
        async with client:
            tr = (await client.post("/api/factor-sets/train",
                                    json={"members": _TWO_MEMBERS, "method": "linear",
                                          "horizon": 6, **_RANGE})).json()
            prov = tr["provenance"]
            assert prov["instId"] == "BTC-USDT-SWAP" and prov["bar"] == "1H"
            assert prov["horizon"] == 6 and prov["startTs"] == _RANGE["startTs"]
    finally:
        await dispose_engine()


async def test_norm_window_mismatch_400(patch_okx):
    """已训练组合保存时 thresholds.normWindow != 训练 normStats.normWindow → 400（H2）。"""
    from app.db.session import dispose_engine
    client = await _client()
    try:
        async with client:
            tr = (await client.post("/api/factor-sets/train",
                                    json={"members": _TWO_MEMBERS, "method": "linear",
                                          "horizon": 6, **_RANGE})).json()
            r = await client.post("/api/factor-sets", json={
                "name": "mismatch", "members": _TWO_MEMBERS, "method": "linear",
                "modelJson": {"weights": tr["weights"], "normStats": tr["normStats"]},
                "metricsJson": tr["metrics"],
                "config": {"thresholds": {"normWindow": tr["normStats"]["normWindow"] + 300}}})
            assert r.status_code == 400
            assert r.json()["error"]["code"] == "FACTOR_NORM_WINDOW_MISMATCH"
    finally:
        await dispose_engine()


async def test_weights_stale_400(patch_okx):
    """训练后增删成员、权重键集与成员不一致 → 保存触发 FACTOR_WEIGHTS_STALE 400（H3）。"""
    from app.db.session import dispose_engine
    client = await _client()
    try:
        async with client:
            tr = (await client.post("/api/factor-sets/train",
                                    json={"members": _TWO_MEMBERS, "method": "linear",
                                          "horizon": 6, **_RANGE})).json()
            members3 = _TWO_MEMBERS + [{"factorKey": "trend_ema_dist", "direction": "long"}]
            r = await client.post("/api/factor-sets", json={
                "name": "stale", "members": members3, "method": "linear",
                "modelJson": {"weights": tr["weights"], "normStats": tr["normStats"]},
                "config": {"thresholds": {"normWindow": tr["normStats"]["normWindow"]}}})
            assert r.status_code == 400
            assert r.json()["error"]["code"] == "FACTOR_WEIGHTS_STALE"
    finally:
        await dispose_engine()


async def test_save_trained_combo_consistent_ok(patch_okx):
    """窗口一致 + 权重覆盖成员 → 正常 201（校验只拦漂移/陈旧，绝不误伤合法保存）。"""
    from app.db.session import dispose_engine
    client = await _client()
    try:
        async with client:
            tr = (await client.post("/api/factor-sets/train",
                                    json={"members": _TWO_MEMBERS, "method": "linear",
                                          "horizon": 6, **_RANGE})).json()
            r = await client.post("/api/factor-sets", json={
                "name": "ok", "members": _TWO_MEMBERS, "method": "linear",
                "modelJson": {"weights": tr["weights"], "normStats": tr["normStats"]},
                "metricsJson": tr["metrics"],
                "config": {"thresholds": {"normWindow": tr["normStats"]["normWindow"]}}})
            assert r.status_code == 201, r.text
    finally:
        await dispose_engine()


async def test_mine_counts_and_dedupe(patch_okx):
    """挖掘落库计数自洽 + 二次同区间去重 persisted=0（H5）。"""
    from app.db.session import dispose_engine
    client = await _client()
    try:
        async with client:
            d1 = (await client.post("/api/factors/mine",
                                    json={**_RANGE, "topN": 6, "persist": True})).json()
            assert d1["evaluated"] == d1["persisted"] + d1["skipped"]
            # 二次同区间挖掘必全去重（与先前 DB 状态无关，落库幂等）
            d2 = (await client.post("/api/factors/mine",
                                    json={**_RANGE, "topN": 6, "persist": True})).json()
            assert d2["evaluated"] == d2["persisted"] + d2["skipped"]
            assert d2["persisted"] == 0 and d2["skipped"] == d2["evaluated"]
    finally:
        await dispose_engine()


async def test_disabled_factor_excluded_from_enabled_filter(patch_okx):
    """禁用某因子后 GET /factors?enabled=true 不含它（H4 enabled 真实下游：Agent 池 / 我的因子默认视图）。"""
    from app.db.session import dispose_engine
    client = await _client()
    try:
        async with client:
            created = await client.post("/api/factors", json={
                "factorKey": "mom_roc", "name": "待禁用", "params": {"win": 9}, "direction": "long"})
            fid = created.json()["id"]
            await client.put(f"/api/factors/{fid}", json={"enabled": False})
            enabled_ids = {f["id"] for f in (await client.get("/api/factors?enabled=true")).json()}
            all_ids = {f["id"] for f in (await client.get("/api/factors")).json()}
            assert fid not in enabled_ids and fid in all_ids
    finally:
        await dispose_engine()


async def test_score_short_window_note(patch_okx):
    """短区间打分无有效点 → 响应带 window_too_short + minBarsRequired（取契约窗口，区间过短可观测，H6）。"""
    from app.db.session import dispose_engine
    client = await _client()
    try:
        async with client:
            tr = (await client.post("/api/factor-sets/train",
                                    json={"members": _TWO_MEMBERS, "method": "linear",
                                          "horizon": 6, "config": {"normWindow": 150}, **_RANGE})).json()
            saved = await client.post("/api/factor-sets", json={
                "name": "win", "members": _TWO_MEMBERS, "method": "linear",
                "modelJson": {"weights": tr["weights"], "normStats": tr["normStats"]},
                "config": {"thresholds": {"normWindow": tr["normStats"]["normWindow"]}}})
            sid = saved.json()["id"]
            # 区间根数 (≈100) < 训练契约窗口 150 → 全 NaN → 无有效点
            short = {"instId": "BTC-USDT-SWAP", "bar": "1H",
                     "startTs": 1_700_000_000, "endTs": 1_700_000_000 + 100 * 3600}
            body = (await client.post(f"/api/factor-sets/{sid}/score", json=short)).json()
            assert body["points"] == []
            assert body["note"] == "window_too_short" and body["minBarsRequired"] == 150
    finally:
        await dispose_engine()
