"""F2 缓存回退诚实标注 (spec §8.1)：

- ``/candles`` 成功路径响应头 ``X-Aragon-Data-Source: live``；
- 构造 OKX 抛错 + ``allow_cached_fallback=True`` + 预置缓存 -> 头 ``cache`` + ``X-Aragon-Stale: 1``，body 仍为 candle 数组；
- OKX 抛错 + ``allow_cached_fallback=False`` -> 仍 502（无回退、无假数据）；
- ``/health`` 含 ``cachedFallbackEnabled`` 字段。

装配要点（评审 C4）：``get_settings`` 为 @lru_cache 且 conftest 在导入期固定
``ALLOW_CACHED_FALLBACK=false``，故翻动开关用 monkeypatch ``app.api.market.get_settings``；
预置缓存先以可用假客户端成功跑一次 ``/candles``（触发 upsert_candles 落 candles_cache），
再令假客户端的 get_candles 抛 OKXError，方能命中回退分支。
"""
from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from app.okx.rest_client import OKXError
from app.okx.types import Candle, Instrument
from tests.conftest import make_synthetic_candles


class _FlakyRest:
    """可切换的假 OKX 客户端：先成功取数（落缓存），翻 raise_okx 后抛 OKXError。"""

    def __init__(self) -> None:
        self.raise_okx = False

    async def health(self) -> bool:
        return not self.raise_okx

    async def get_candles(self, inst_id, bar, limit=300, before=None, after=None):
        if self.raise_okx:
            raise OKXError("OKX_UNREACHABLE", "fake offline")
        rows = make_synthetic_candles(min(limit, 300))
        return [Candle(ts=r["ts"], o=r["o"], h=r["h"], l=r["l"], c=r["c"], vol=r["vol"]) for r in rows]

    async def get_instruments(self, inst_type="SWAP"):
        return [Instrument(instId="BTC-USDT-SWAP", instType="SWAP", base="BTC", quote="USDT")]

    async def aclose(self):
        pass


class _FakeSettings:
    """仅承载 market.py / health 用到的字段，避开 @lru_cache 真单例。"""

    def __init__(self, allow_cached_fallback: bool) -> None:
        self.allow_cached_fallback = allow_cached_fallback
        self.okx_ws_public = "wss://example.test/ws"


def _load_app():
    from app.main import app

    return app


async def test_candles_live_header(monkeypatch):
    """真实实时取数 -> X-Aragon-Data-Source: live，无 Stale 头。"""
    from app.db.session import dispose_engine, init_db

    fake = _FlakyRest()
    monkeypatch.setattr("app.api.market.get_rest_client", lambda: fake)
    await init_db()
    transport = ASGITransport(app=_load_app())
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.get(
                "/api/candles", params={"instId": "BTC-USDT-SWAP", "bar": "1H", "limit": 100}
            )
            assert r.status_code == 200, r.text
            assert r.headers.get("X-Aragon-Data-Source") == "live"
            assert "X-Aragon-Stale" not in r.headers
            assert len(r.json()) > 0
    finally:
        await dispose_engine()


async def test_candles_cache_fallback_header(monkeypatch):
    """OKX 抛错 + 开回退 + 预置缓存 -> cache 头 + Stale，body 仍是 candle 数组。"""
    from app.db.session import dispose_engine, init_db

    fake = _FlakyRest()
    monkeypatch.setattr("app.api.market.get_rest_client", lambda: fake)
    monkeypatch.setattr(
        "app.api.market.get_settings", lambda: _FakeSettings(allow_cached_fallback=True)
    )
    await init_db()
    transport = ASGITransport(app=_load_app())
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            # 预置缓存：先成功取一次真实数据（触发 repo.upsert_candles 落 candles_cache）
            r = await client.get(
                "/api/candles", params={"instId": "ETH-USDT-SWAP", "bar": "1H", "limit": 120}
            )
            assert r.status_code == 200 and r.headers.get("X-Aragon-Data-Source") == "live"
            # 令 OKX 不可达 -> 命中缓存回退分支
            fake.raise_okx = True
            r = await client.get(
                "/api/candles", params={"instId": "ETH-USDT-SWAP", "bar": "1H", "limit": 120}
            )
            assert r.status_code == 200, r.text
            assert r.headers.get("X-Aragon-Data-Source") == "cache"
            assert r.headers.get("X-Aragon-Stale") == "1"
            assert len(r.json()) > 0
    finally:
        await dispose_engine()


async def test_candles_no_fallback_still_502(monkeypatch):
    """默认配置（不回退）下 OKX 抛错 -> 显式 502，无假数据（C1 不回退）。"""
    from app.db.session import dispose_engine, init_db

    fake = _FlakyRest()
    fake.raise_okx = True
    monkeypatch.setattr("app.api.market.get_rest_client", lambda: fake)
    monkeypatch.setattr(
        "app.api.market.get_settings", lambda: _FakeSettings(allow_cached_fallback=False)
    )
    await init_db()
    transport = ASGITransport(app=_load_app())
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.get(
                "/api/candles", params={"instId": "XRP-USDT-SWAP", "bar": "1H", "limit": 50}
            )
            assert r.status_code == 502
            assert r.json()["error"]["code"]
    finally:
        await dispose_engine()


async def test_health_has_cached_fallback_flag(monkeypatch):
    """/health 追加 cachedFallbackEnabled 字段（默认 false，向后兼容）。"""
    from app.db.session import dispose_engine, init_db

    fake = _FlakyRest()
    monkeypatch.setattr("app.api.market.get_rest_client", lambda: fake)
    await init_db()
    transport = ASGITransport(app=_load_app())
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.get("/api/health")
            assert r.status_code == 200
            body = r.json()
            assert "cachedFallbackEnabled" in body
            assert body["cachedFallbackEnabled"] is False
    finally:
        await dispose_engine()
