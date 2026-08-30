"""检索层：tavily 适配器 / 重试 / 熔断 / 脱敏 / probe 硬封顶（§7.1 · test_ai_intel_search）。

全程离线：`httpx` 的传输层用 `MockTransport` 替身，一个真实的出网请求都不发。
"""
from __future__ import annotations

import asyncio
import time

import httpx
import pytest

from app.ai_auto.intel import search as search_mod
from app.ai_auto.intel.search import (
    IntelSearchError,
    SearchRunner,
    TavilyProvider,
    probe_provider,
    resolve_search_provider,
)

KEY = "tvly-secret-key-1234567890"


# **必须在打补丁之前捕获原始类**：同一个用例里装两次替身时，第二次的 `real` 会指向第一次的
# 工厂，构造参数于是撞车（`got multiple values for keyword argument 'transport'`）。
_REAL_ASYNC_CLIENT = httpx.AsyncClient


def _install(monkeypatch, handler):
    """把 `httpx.AsyncClient` 换成带 MockTransport 的替身（适配器内部自建 client，故拦构造）。"""
    real = _REAL_ASYNC_CLIENT

    def factory(*args, **kwargs):
        kwargs.pop("proxy", None)
        return real(*args, transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(search_mod.httpx, "AsyncClient", factory)


def test_resolve_returns_none_for_unconfigured_source():
    """未配置 ⇒ `None` ⇒ 全链路诚实降级；**绝不**悄悄回落到「让模型自己写一条」。"""
    assert resolve_search_provider(slug="none", api_key=KEY) is None
    assert resolve_search_provider(slug="tavily", api_key="") is None
    # `brave` / `serper` 自 ai-trade-intel-closed-loop 轴 D 起**已实现**（本文件原先断言它们
    # 「按未配置处理」，那是 v1 的事实）。仍然按未配置处理的只剩下**拼错的 slug**——这条路
    # 必须留着：删掉它，一个手写错的 provider 名会变成一次静默的「未配置」。
    assert resolve_search_provider(slug="typo-search", api_key=KEY) is None
    assert isinstance(resolve_search_provider(slug="tavily", api_key=KEY), TavilyProvider)
    assert resolve_search_provider(slug="brave", api_key=KEY) is not None
    assert resolve_search_provider(slug="serper", api_key=KEY) is not None
    assert resolve_search_provider(slug="brave", api_key="") is None


@pytest.mark.asyncio
async def test_tavily_request_body_and_parsing(monkeypatch):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["body"] = request.read().decode()
        return httpx.Response(200, json={"results": [
            {"title": "T1", "url": "https://a.com/1", "content": "body",
             "published_date": "2027-01-15T08:00:00Z", "score": 0.9},
            {"title": "no url", "content": "x"},
        ]})

    _install(monkeypatch, handler)
    docs = await TavilyProvider(api_key=KEY).search("btc news", limit=6, freshness_days=3)
    assert seen["url"] == search_mod.TAVILY_ENDPOINT
    assert '"api_key"' in seen["body"] and '"days":3' in seen["body"].replace(" ", "")
    assert len(docs) == 1, "缺 url 的行必须被丢弃（它无法被引用，也无法被核对）"
    assert docs[0].published_ts == 1_800_000_000


@pytest.mark.asyncio
async def test_retries_429_but_not_400(monkeypatch):
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(429 if calls["n"] == 1 else 200,
                              json={"results": [{"title": "T", "url": "https://a.com/1"}]})

    _install(monkeypatch, handler)
    monkeypatch.setattr(search_mod, "_sleep_backoff", lambda attempt: asyncio.sleep(0))
    docs = await TavilyProvider(api_key=KEY, max_retries=2).search("q", limit=3,
                                                                  freshness_days=3)
    assert calls["n"] == 2 and len(docs) == 1

    calls["n"] = 0
    _install(monkeypatch, lambda req: httpx.Response(400, text="bad request"))
    with pytest.raises(IntelSearchError) as err:
        await TavilyProvider(api_key=KEY, max_retries=3).search("q", limit=3, freshness_days=3)
    assert err.value.code == "http_error"


@pytest.mark.asyncio
async def test_error_message_never_contains_key(monkeypatch):
    """§3.2.3：key 绝不进日志。`IntelSearchError` 在**构造时**就完成脱敏。"""
    _install(monkeypatch, lambda req: httpx.Response(401, text=f"invalid key {KEY}"))
    with pytest.raises(IntelSearchError) as err:
        await TavilyProvider(api_key=KEY, max_retries=0).search("q", limit=3, freshness_days=3)
    assert KEY not in err.value.message
    assert KEY not in str(err.value)
    assert "***" in err.value.message


def test_breaker_opens_after_threshold_and_reports_remaining():
    breaker = search_mod._Breaker(threshold=4, cooldown_s=300.0)
    for _ in range(3):
        breaker.note_failure()
    breaker.ensure_closed_or_raise()          # 未到阈值：放行
    breaker.note_failure()
    with pytest.raises(IntelSearchError) as err:
        breaker.ensure_closed_or_raise()
    assert err.value.code == "circuit_open"
    assert breaker.remaining_s() > 0
    breaker.note_ok()
    breaker.ensure_closed_or_raise()          # 任一次成功即完全复位
    assert breaker.remaining_s() == 0


@pytest.mark.asyncio
async def test_search_many_survives_partial_failure_but_raises_on_total_failure():
    class Flaky:
        slug = "fake"

        def __init__(self):
            self.n = 0

        async def search(self, query, *, limit, freshness_days):
            self.n += 1
            if self.n == 1:
                raise IntelSearchError("network_error", "boom")
            return [search_mod.SearchDoc(title="T", url="https://a.com/1", snippet="s")]

    runner = SearchRunner(Flaky(), timeout_s=5.0)
    docs = await runner.search_many(["q1", "q2"], limit=3, freshness_days=3)
    assert len(docs) == 1, "单 query 失败不拖垮整轮"

    class Dead:
        slug = "fake"

        async def search(self, query, *, limit, freshness_days):
            raise IntelSearchError("network_error", "down")

    dead = SearchRunner(Dead(), timeout_s=5.0)
    with pytest.raises(IntelSearchError):
        await dead.search_many(["q1"], limit=3, freshness_days=3)
    assert dead.breaker.failures == 1, "全失败必须喂熔断器，否则它永远不会开路"


@pytest.mark.asyncio
async def test_probe_caps_total_wall_clock(monkeypatch):
    """评审 P1-6：probe 是配置校验、不是生产取数，故 `max_retries=0` 且总耗时硬封顶。"""
    async def never_return(*args, **kwargs):
        await asyncio.sleep(60)

    monkeypatch.setattr(search_mod, "PROBE_MAX_WALL_S", 0.05)
    monkeypatch.setattr(TavilyProvider, "search", never_return)
    started = time.monotonic()
    result = await probe_provider(slug="tavily", api_key=KEY, timeout_s=60.0)
    assert time.monotonic() - started < 5.0
    assert result["ok"] is False
    assert "没有响应" in result["message"]


@pytest.mark.asyncio
async def test_probe_reports_unconfigured_source_honestly():
    result = await probe_provider(slug="none", api_key="")
    assert result["ok"] is False and "未" in result["message"]
