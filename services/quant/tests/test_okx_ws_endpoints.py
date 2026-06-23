"""双端点路由回归测试 (market-depth-realtime-fix spec)。

核心断言（无真实网络，monkeypatch `websockets.connect`）：

- ``market_channels=True`` 时 ``start()`` 开两个连接环（business + public），
  ``False`` 时只开 business 一个；
- candle 频道订阅落在 **business** 端点，trades/tickers/books5 落在 **public** 端点；
- ``stop()`` 取消全部任务、幂等可重入。

这是「先红后绿」的复现用例：修复前（单端点 + 硬编码 market_channels=False）
本测试失败，修复后通过。
"""
from __future__ import annotations

import asyncio
import json

import pytest

from app.config import Settings
from app.okx.ws_client import MarketWSHandlers, OKXMarketWSClient

_BUSINESS_URL = "wss://business.test/ws/v5/business"
_PUBLIC_URL = "wss://public.test/ws/v5/public"


class _FakeConnection:
    """记录订阅 payload，然后挂起直到外层任务被 ``stop()`` 取消。"""

    def __init__(self, url: str, recorder: "_ConnectRecorder"):
        self._url = url
        self._recorder = recorder

    async def __aenter__(self) -> "_FakeConnection":
        return self

    async def __aexit__(self, *_exc) -> bool:
        return False

    async def send(self, payload: str) -> None:
        if payload == "ping":
            return
        self._recorder.subscriptions[self._url] = json.loads(payload)["args"]

    def __aiter__(self) -> "_FakeConnection":
        return self

    async def __anext__(self):
        # 保持「连接存活」，直到任务被取消（模拟没有新消息的稳定连接）。
        await asyncio.Event().wait()
        raise StopAsyncIteration


class _ConnectRecorder:
    """替身 ``websockets.connect``：记录每个被连接的 URL 及其订阅频道。"""

    def __init__(self) -> None:
        self.urls: list[str] = []
        self.subscriptions: dict[str, list] = {}

    def __call__(self, url: str, **_kwargs) -> _FakeConnection:
        self.urls.append(url)
        return _FakeConnection(url, self)


async def _noop(*_args) -> None:
    return None


def _handlers() -> MarketWSHandlers:
    return MarketWSHandlers(
        on_candle=_noop,
        on_trade=_noop,
        on_ticker=_noop,
        on_book=_noop,
    )


def _settings() -> Settings:
    # 显式构造（构造参数优先级高于 .env / 环境变量），与真实端点解耦。
    return Settings(okx_ws_public=_PUBLIC_URL, okx_ws_business=_BUSINESS_URL)


async def _wait_for(predicate, *, tries: int = 200) -> None:
    """让事件循环推进，直到 ``predicate()`` 为真或重试用尽。"""
    for _ in range(tries):
        if predicate():
            return
        await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_start_opens_business_and_public_loops(monkeypatch):
    recorder = _ConnectRecorder()
    monkeypatch.setattr("app.okx.ws_client.websockets.connect", recorder)
    client = OKXMarketWSClient(
        "BTC-USDT-SWAP", "1m", _handlers(), settings=_settings(), market_channels=True
    )
    client.start()
    assert len(client._tasks) == 2
    await _wait_for(lambda: len(recorder.subscriptions) >= 2)
    await client.stop()

    # candle 只订到 business 端点；trades/tickers/books5 只订到 public 端点。
    assert [a["channel"] for a in recorder.subscriptions[_BUSINESS_URL]] == ["candle1m"]
    assert [a["channel"] for a in recorder.subscriptions[_PUBLIC_URL]] == [
        "trades",
        "tickers",
        "books5",
    ]
    assert client._tasks == []


@pytest.mark.asyncio
async def test_start_opens_only_business_loop_when_market_disabled(monkeypatch):
    recorder = _ConnectRecorder()
    monkeypatch.setattr("app.okx.ws_client.websockets.connect", recorder)
    client = OKXMarketWSClient(
        "BTC-USDT-SWAP", "1m", _handlers(), settings=_settings(), market_channels=False
    )
    client.start()
    assert len(client._tasks) == 1
    await _wait_for(lambda: bool(recorder.subscriptions))
    await client.stop()

    # 关闭 market 频道时只连 business（纸面交易的 candle-only 语义不退化）。
    assert list(recorder.subscriptions.keys()) == [_BUSINESS_URL]
    assert [a["channel"] for a in recorder.subscriptions[_BUSINESS_URL]] == ["candle1m"]


@pytest.mark.asyncio
async def test_start_is_idempotent_and_stop_is_reentrant(monkeypatch):
    recorder = _ConnectRecorder()
    monkeypatch.setattr("app.okx.ws_client.websockets.connect", recorder)
    client = OKXMarketWSClient(
        "BTC-USDT-SWAP", "1m", _handlers(), settings=_settings(), market_channels=True
    )
    client.start()
    client.start()  # 任务已在跑，二次 start() 是 no-op，不重复开环。
    assert len(client._tasks) == 2
    await client.stop()
    assert client._tasks == []
    await client.stop()  # 再次 stop() 不得抛异常。
