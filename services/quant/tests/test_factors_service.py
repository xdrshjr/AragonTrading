"""因子层取数复用缓存测试（L6）：命中/隔离/TTL/清缓存 + 命中返回 .copy() 防污染（评审 P1-3）。

缓存收敛在唯一 choke point ``_load_df``，缓存的是不可变历史 K 线；未命中仍真实取数、OKX 不可达仍
显式报错（绝不以缓存假装实时）。测试共享进程，每例前后清缓存防串扰（呼应 aragon-quant-test-isolation）。
"""
from __future__ import annotations

import types

import pytest
from fastapi import HTTPException

from app.factors import service
from app.factors.service import FactorWindow, clear_candle_cache
from app.okx.types import Candle
from tests.conftest import make_synthetic_candles


@pytest.fixture(autouse=True)
def _isolate_cache():
    clear_candle_cache()
    yield
    clear_candle_cache()


def _candles(n: int = 60) -> list[Candle]:
    return [Candle(ts=r["ts"], o=r["o"], h=r["h"], l=r["l"], c=r["c"], vol=r["vol"])
            for r in make_synthetic_candles(n, start_ts=1_700_000_000, step=3600)]


def _counting_loader(calls: dict):
    async def _load(rest, *, inst_id, bar, start_ts, end_ts, max_bars=None):
        calls["n"] += 1
        return _candles()
    return _load


_W = FactorWindow(inst_id="BTC-USDT-SWAP", bar="1H", start_ts=1_700_000_000,
                  end_ts=1_700_000_000 + 59 * 3600)
_W_OTHER = FactorWindow(inst_id="ETH-USDT-SWAP", bar="1H", start_ts=1_700_000_000,
                        end_ts=1_700_000_000 + 59 * 3600)


async def test_same_window_fetches_once(monkeypatch):
    """同窗连续两次取数只真实取一次（命中缓存复用，L6）。"""
    calls = {"n": 0}
    monkeypatch.setattr(service, "load_range_candles", _counting_loader(calls))
    await service._load_df(None, _W)
    await service._load_df(None, _W)
    assert calls["n"] == 1


async def test_different_windows_fetch_separately(monkeypatch):
    """不同窗各自取数（严格隔离不串味，L6）。"""
    calls = {"n": 0}
    monkeypatch.setattr(service, "load_range_candles", _counting_loader(calls))
    await service._load_df(None, _W)
    await service._load_df(None, _W_OTHER)
    assert calls["n"] == 2


async def test_ttl_expiry_refetches(monkeypatch):
    """TTL 过期后重新取数（缓存短寿，L6）。"""
    calls = {"n": 0}
    clock = {"t": 1000.0}
    monkeypatch.setattr(service, "time", types.SimpleNamespace(monotonic=lambda: clock["t"]))
    monkeypatch.setattr(service, "load_range_candles", _counting_loader(calls))
    await service._load_df(None, _W)
    clock["t"] += service._CANDLE_CACHE_TTL_SECONDS + 1.0
    await service._load_df(None, _W)
    assert calls["n"] == 2


async def test_clear_cache_refetches(monkeypatch):
    """clear_candle_cache() 后重新取数（测试隔离用，L6）。"""
    calls = {"n": 0}
    monkeypatch.setattr(service, "load_range_candles", _counting_loader(calls))
    await service._load_df(None, _W)
    clear_candle_cache()
    await service._load_df(None, _W)
    assert calls["n"] == 2


async def test_hit_returns_defensive_copy(monkeypatch):
    """命中返回 .copy()：就地改列不污染缓存，下次命中仍干净且不重取（P1-3）。"""
    calls = {"n": 0}
    monkeypatch.setattr(service, "load_range_candles", _counting_loader(calls))
    df1 = await service._load_df(None, _W)
    df1["o"] = -999.0                       # 下游就地改列（模拟消费方污染）
    df2 = await service._load_df(None, _W)  # 命中缓存
    assert calls["n"] == 1                  # 仍是命中（未重取）
    assert (df2["o"] != -999.0).all()       # 缓存未被 df1 的就地修改污染


async def test_unreachable_still_errors_not_masked(monkeypatch):
    """未命中且 OKX 不可达仍按既有语义显式报错（绝不被缓存掩盖，L6 铁律）。"""
    async def _boom(rest, **kwargs):
        raise HTTPException(400, detail={"code": "EMPTY_RANGE", "message": "no data"})
    monkeypatch.setattr(service, "load_range_candles", _boom)
    with pytest.raises(HTTPException):
        await service._load_df(None, _W)
