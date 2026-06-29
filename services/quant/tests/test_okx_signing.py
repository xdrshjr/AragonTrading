"""OKX V5 私有签名单测：确定性签名向量 / 时间戳格式 / 鉴权头组装（不依赖网络）。"""
from __future__ import annotations

import base64
import hashlib
import hmac
import re

import httpx
import pytest

from app.config import Settings
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
