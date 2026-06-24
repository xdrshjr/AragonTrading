"""注册表断言：regime_rotation 组合模板已注册 / kind=combo / 被 seed 为 builtin / on_candle 抛错。"""
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
    monkeypatch.setattr("app.api.strategies.get_rest_client", lambda: fake)
    return fake


def test_regime_rotation_registered():
    from app.strategies import BUILTIN_ORDER, STRATEGY_REGISTRY
    assert "regime_rotation" in STRATEGY_REGISTRY
    assert STRATEGY_REGISTRY["regime_rotation"].meta.kind == "combo"
    assert "regime_rotation" in BUILTIN_ORDER


def test_combination_on_candle_raises():
    """误把组合喂进 run_backtest 必须立即响（C1 不静默）。"""
    from app.strategies import STRATEGY_REGISTRY
    combo = STRATEGY_REGISTRY["regime_rotation"]()
    with pytest.raises(RuntimeError):
        combo.on_candle(None, 0, None)


async def test_regime_rotation_seeded_as_builtin_and_runnable(patch_okx):
    """regime_rotation 被 seed 为 builtin（kind=combo），默认成员可跑通一次 preview。"""
    from app.db.session import dispose_engine, init_db
    await init_db()
    transport = ASGITransport(app=_load_app())
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            strategies = (await client.get("/api/strategies")).json()
            combo = next((s for s in strategies if s["templateKey"] == "regime_rotation"), None)
            assert combo is not None
            assert combo["builtin"] is True
            assert combo["kind"] == "combo"

            r = await client.post(f"/api/strategies/{combo['id']}/backtest", json={
                "instId": "BTC-USDT-SWAP", "bar": "1H", "limit": 300,
                "startTs": 1_700_000_000, "endTs": 1_700_000_000 + 295 * 3600})
            assert r.status_code == 200, r.text
            data = r.json()
            assert "combo" in data   # 保存组合走 backtest 路由也带 combo 字段
            assert "totalReturn" in data["metrics"]
    finally:
        await dispose_engine()


def _load_app():
    from app.main import app
    return app
