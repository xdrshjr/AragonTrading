"""OKX V5 私有签名单测：确定性签名向量 / 时间戳格式 / 鉴权头组装（不依赖网络）。"""
from __future__ import annotations

import base64
import hashlib
import hmac
import re

import anyio
import httpx
import pytest

from app.config import Settings
from app.okx.client_pool import get_client_pool
from app.okx.private_client import OKXPrivateClient, OKXPrivateError, _iso_timestamp, _sign


def test_sign_matches_known_vector():
    """给定固定 secret/ts/method/path/body，`_sign` 产出与离线手算一致的 base64。"""
    secret = "unit-test-hmac-secret-vector"  # 测试向量，非真实凭证（R-Secret）
    ts = "2026-06-22T08:15:42.123Z"
    method = "GET"
    request_path = "/api/v5/account/config"
    expected = base64.b64encode(
        hmac.new(secret.encode("utf-8"),
                 f"{ts}{method}{request_path}".encode("utf-8"),
                 hashlib.sha256).digest()).decode("utf-8")
    assert _sign(secret, ts, method, request_path, "") == expected


def test_sign_is_deterministic_and_query_sensitive():
    """同输入确定可复现；requestPath 含 query 时签名随之变化（与协议一致）。"""
    secret = "topsecret-123456"
    ts = "2026-01-01T00:00:00.000Z"
    a = _sign(secret, ts, "GET", "/api/v5/account/positions")
    b = _sign(secret, ts, "GET", "/api/v5/account/positions?instType=SWAP")
    assert _sign(secret, ts, "GET", "/api/v5/account/positions") == a
    assert a != b


def test_iso_timestamp_has_millis_and_trailing_z():
    """时间戳形如 ``....\\d{3}Z``（ISO8601 毫秒 UTC 带尾 Z）。"""
    ts = _iso_timestamp()
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z", ts)


def test_headers_contain_all_okx_fields_and_no_simulated_by_default():
    """实盘默认：头含全部 5 个 OK-ACCESS-* / Content-Type，且不含 x-simulated-trading。"""
    client = OKXPrivateClient("ak", "sk-123456", "pass-123456", Settings(okx_account_demo=False))
    ts = _iso_timestamp()
    headers = client._headers(ts, "GET", "/api/v5/account/config")
    assert headers["OK-ACCESS-KEY"] == "ak"
    assert headers["OK-ACCESS-TIMESTAMP"] == ts
    assert headers["OK-ACCESS-PASSPHRASE"] == "pass-123456"
    assert headers["Content-Type"] == "application/json"
    assert headers["OK-ACCESS-SIGN"] == _sign("sk-123456", ts, "GET", "/api/v5/account/config")
    assert "x-simulated-trading" not in headers


def test_headers_add_simulated_trading_when_demo_enabled():
    """demo 开关开启时附 x-simulated-trading:1（模拟盘）。"""
    client = OKXPrivateClient("ak", "sk-123456", "pass-123456", Settings(okx_account_demo=True))
    headers = client._headers(_iso_timestamp(), "GET", "/api/v5/account/config")
    assert headers["x-simulated-trading"] == "1"


def _inject_transport(client: OKXPrivateClient, handler) -> None:
    """注入 MockTransport：`_get_client` 见 `self._client` 已置则直接复用（测试用）。"""
    client._client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="https://www.okx.com")


async def test_get_retries_on_429_then_succeeds(monkeypatch):
    """限流回归：首发 429、次发 200 时，签名 GET 退避后**重试到成功**，不立即抛 OKX_HTTP_ERROR。

    护栏修复前（无退避）会在首发 429 立即抛错，整组启动失败成 502。
    """
    calls = {"n": 0}

    def handler(_request):
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, json={"msg": "Too Many Requests"})
        return httpx.Response(200, json={"code": "0", "data": [{"uid": "u-123"}]})

    monkeypatch.setattr("app.okx.private_client.asyncio.sleep", _no_sleep)
    client = OKXPrivateClient("ak", "sk-123456", "pass-123456", Settings(okx_account_demo=False))
    _inject_transport(client, handler)
    try:
        cfg = await client.get_account_config()
    finally:
        await client.aclose()
    assert cfg == {"uid": "u-123"}
    assert calls["n"] == 2   # 首发 429 被退避吞掉，次发 200 成功


async def test_get_exhausts_429_raises_http_error(monkeypatch):
    """限流回归：持续 429 至退避耗尽，须显式抛 `OKX_HTTP_ERROR / OKX HTTP 429`（非 last_exc=None 误导串）。"""
    def handler(_request):
        return httpx.Response(429, json={"msg": "rate"})

    monkeypatch.setattr("app.okx.private_client.asyncio.sleep", _no_sleep)
    client = OKXPrivateClient("ak", "sk-123456", "pass-123456", Settings(okx_account_demo=False))
    _inject_transport(client, handler)
    try:
        with pytest.raises(OKXPrivateError) as excinfo:
            await client.get_account_config()
    finally:
        await client.aclose()
    assert excinfo.value.code == "OKX_HTTP_ERROR"
    assert "429" in excinfo.value.message


async def test_get_non_429_status_raises_immediately(monkeypatch):
    """非 429 的 HTTP 错误（如 401）语义不变：立即抛 OKX_HTTP_ERROR，不退避重试。"""
    calls = {"n": 0}

    def handler(_request):
        calls["n"] += 1
        return httpx.Response(401, json={"msg": "unauthorized"})

    monkeypatch.setattr("app.okx.private_client.asyncio.sleep", _no_sleep)
    client = OKXPrivateClient("ak", "sk-123456", "pass-123456", Settings(okx_account_demo=False))
    _inject_transport(client, handler)
    try:
        with pytest.raises(OKXPrivateError) as excinfo:
            await client.get_account_config()
    finally:
        await client.aclose()
    assert excinfo.value.code == "OKX_HTTP_ERROR"
    assert "401" in excinfo.value.message
    assert calls["n"] == 1   # 非 429 不重试


async def _no_sleep(_seconds) -> None:
    """退避桩：跳过真实 sleep，避免单测被指数退避拖慢。"""
    return None


# ---------------- F2：传输失败的谓词是「结构」而不是「类型」 ----------------
# （ai-trade-interval-wake-dead-monitor-loop §3.3 / §8.2）


def _pooled(handler, **over) -> OKXPrivateClient:
    """走**连接池**路径的客户端（不注入 `self._client`，否则 `_invalidate_pooled` 会空转）。"""
    base = dict(okx_account_demo=False, okx_link_private_rate_per_s=0.0,
                okx_link_pool_idle_ttl_s=120.0)
    base.update(over)
    client = OKXPrivateClient("ak", "sk-123456", "pass-123456", Settings(**base))
    client._new_client = lambda: httpx.AsyncClient(  # noqa: E731 —— 测试替身工厂
        transport=httpx.MockTransport(handler), base_url="https://www.okx.com")
    return client


async def test_a_non_httpx_transport_failure_is_normalised_and_swaps_the_client(monkeypatch):
    """镜像公共侧 `test_okx_rest_resilience.py` 的同名护栏——**私有侧此前正栽在这上面**。

    `anyio.ClosedResourceError`（代理 CONNECT 隧道在 TLS 握手中途被拆）不是 httpx 异常：用
    `except httpx.TransportError` 当谓词的写法，恰好会在这个**已知会发生**的类上静默失效，让裸
    异常穿透 `_get` 承诺的「一律抛 OKXPrivateError」，于是下游 `executor.reconcile` 的
    `except OKXError` 降级链路整条空转，盯市循环被直接打死（本轮缺陷的第一层漏斗）。
    """
    calls = {"n": 0}
    made: list = []

    def handler(_request):
        calls["n"] += 1
        raise anyio.ClosedResourceError()

    monkeypatch.setattr("app.okx.private_client.asyncio.sleep", _no_sleep)
    client = _pooled(handler)
    factory = client._new_client

    def counting_factory():
        made.append(factory())
        return made[-1]

    client._new_client = counting_factory
    try:
        with pytest.raises(OKXPrivateError) as excinfo:
            await client.get_positions()
    finally:
        await client.aclose()
        await get_client_pool().aclose_all()

    assert excinfo.value.code == "OKX_UNREACHABLE"
    assert calls["n"] == 3, "传输失败照旧重试三跳（语义不变）"
    assert len(made) == 3, "每一次传输失败都必须换代，下一跳才拿得到新连接"


async def test_a_business_error_code_is_raised_at_once_and_never_retried(monkeypatch):
    """守住 §8.2 判定为**阻断级**的那条回归面。

    F2 若只把关键字换成 `except Exception` 而**不收窄 try**，`code != "0"` 的鉴权 / 业务错误
    （`OKXPrivateError` 也是 `Exception`）会被当成传输失败重试三次并坍缩成 `OKX_UNREACHABLE`，
    直接推翻 `_get` docstring 的承诺，也让 `_maybe_heal_clock` 的时钟自愈与服务层的语义码全失效。
    """
    calls = {"n": 0}

    def handler(_request):
        calls["n"] += 1
        return httpx.Response(200, json={"code": "50113", "msg": "Invalid Sign"})

    monkeypatch.setattr("app.okx.private_client.asyncio.sleep", _no_sleep)
    client = _pooled(handler)
    try:
        with pytest.raises(OKXPrivateError) as excinfo:
            await client.get_positions()
    finally:
        await client.aclose()
        await get_client_pool().aclose_all()

    assert excinfo.value.code == "50113", "业务码必须原样上抛，不得坍缩成 OKX_UNREACHABLE"
    assert excinfo.value.message == "Invalid Sign"
    assert calls["n"] == 1, "鉴权 / 业务错误立即上抛，绝不重试"
