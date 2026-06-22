"""OKX V5 私有签名单测：确定性签名向量 / 时间戳格式 / 鉴权头组装（不依赖网络）。"""
from __future__ import annotations

import base64
import hashlib
import hmac
import re

from app.config import Settings
from app.okx.private_client import OKXPrivateClient, _iso_timestamp, _sign


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
