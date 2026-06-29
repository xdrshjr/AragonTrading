"""OKX V5 交易客户端 POST 签名单测（实盘交易模式 §7.1）。

固定向量校验 ``_sign`` 的 POST 预串；并注入假 httpx client 验证 ``_post`` 用**紧凑 JSON**（无空格）
且 ``OK-ACCESS-SIGN`` 的签名串与实际发送的 body 字节**完全一致**（守 50113 签名错误）。不依赖网络。
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json

import pytest

from app.okx.private_client import OKXPrivateError, _sign
from app.okx.trade_client import OKXTradeClient


def test_post_sign_matches_known_vector():
    """POST 预串 ``ts+POST+path+body``，``_sign`` 与离线手算一致。"""
    secret = "unit-test-hmac-secret-vector"  # 测试向量，非真实凭证（R-Secret）
    ts = "2026-06-22T08:15:42.123Z"
    path = "/api/v5/trade/order"
    body = '{"instId":"BTC-USDT-SWAP","side":"buy"}'
    expected = base64.b64encode(
        hmac.new(secret.encode("utf-8"),
                 f"{ts}POST{path}{body}".encode("utf-8"),
                 hashlib.sha256).digest()).decode("utf-8")
    assert _sign(secret, ts, "POST", path, body) == expected


class _FakeResponse:
    def __init__(self, payload: dict, status_code: int = 200):
        self._payload = payload
        self.status_code = status_code   # _post 现先看 status_code（429 退避预检），桩须提供

    def raise_for_status(self) -> None:
        pass

    def json(self) -> dict:
        return self._payload


class _CapturingClient:
    """假 httpx client：捕获 POST 的 content/headers，返回预置成功响应。"""

    def __init__(self, payload: dict):
        self.payload = payload
        self.sent_content: str | None = None
        self.sent_headers: dict | None = None

    async def post(self, path, content=None, headers=None):  # noqa: D401 —— 仿 httpx 接口
        self.sent_content = content
        self.sent_headers = headers
        return _FakeResponse(self.payload)


async def _client_with_fake(payload: dict) -> tuple[OKXTradeClient, _CapturingClient]:
    client = OKXTradeClient("ak", "sk-post-vector-123456", "pp-123456")
    fake = _CapturingClient(payload)
    client._client = fake  # 注入：绕过 _get_client 的真实 httpx
    return client, fake


async def test_post_uses_compact_json_and_signs_exact_body():
    client, fake = await _client_with_fake({"code": "0", "data": [{"ordId": "1"}]})
    payload = {"instId": "BTC-USDT-SWAP", "tdMode": "cross", "side": "buy", "sz": "1"}
    body = await client._post("/api/v5/trade/order", payload)
    assert body == {"code": "0", "data": [{"ordId": "1"}]}
    # 紧凑 JSON：无空格分隔符，且与 python 紧凑序列化一致
    assert fake.sent_content == json.dumps(payload, separators=(",", ":"))
    assert " " not in fake.sent_content
    # 签名串以**实际发送的 body 字节**计算（一致性是 50113 防护核心）
    ts = fake.sent_headers["OK-ACCESS-TIMESTAMP"]
    expected_sign = _sign("sk-post-vector-123456", ts, "POST", "/api/v5/trade/order",
                          fake.sent_content)
    assert fake.sent_headers["OK-ACCESS-SIGN"] == expected_sign


async def test_place_order_raises_on_data_scode_non_zero():
    """顶层 code=1 且 data[0].sCode 非 0 → 以具体 sCode 抛 OKXPrivateError（§2.2f）。"""
    client, _ = await _client_with_fake(
        {"code": "1", "data": [{"sCode": "51008", "sMsg": "insufficient"}]})
    with pytest.raises(OKXPrivateError) as exc:
        await client.place_order({"instId": "BTC-USDT-SWAP"})
    assert exc.value.code == "51008"


async def test_place_order_returns_row_on_success():
    client, _ = await _client_with_fake(
        {"code": "0", "data": [{"sCode": "0", "ordId": "987", "clOrdId": "aragonx"}]})
    row = await client.place_order({"instId": "BTC-USDT-SWAP"})
    assert row["ordId"] == "987" and row["clOrdId"] == "aragonx"


# ---------------- 回归：签名 POST 的 429 退避（live-auto-multi-symbol-rate-limit-429）----------------


class _SequencedClient:
    """假 httpx client：按预置状态码序列逐次返回，并记录每次发送的 content（验证重试复用同一 body）。"""

    def __init__(self, statuses: list[int], payload: dict):
        self._statuses = list(statuses)
        self._payload = payload
        self.sent_contents: list[str] = []

    async def post(self, path, content=None, headers=None):  # noqa: D401 —— 仿 httpx 接口
        self.sent_contents.append(content)
        return _FakeResponse(self._payload, status_code=self._statuses.pop(0))


async def test_post_retries_on_429_then_succeeds(monkeypatch):
    """限流回归：首发 429、次发 200 时，签名 POST 退避后**重试到成功**，且重试复用同一 body。

    复用同一 body 即同一 ``clOrdId``，OKX 按 ``clOrdId`` 去重，故重试幂等安全、不会重复下单。
    护栏修复前（无退避）会在首发 429 立即抛 OKX_HTTP_ERROR。
    """
    monkeypatch.setattr("app.okx.trade_client.asyncio.sleep", _no_sleep)
    client = OKXTradeClient("ak", "sk-post-vector-123456", "pp-123456")
    fake = _SequencedClient([429, 200], {"code": "0", "data": [{"ordId": "1"}]})
    client._client = fake   # 注入：绕过 _get_client 的真实 httpx
    body = await client._post(
        "/api/v5/trade/order", {"instId": "BTC-USDT-SWAP", "clOrdId": "aragonx1", "side": "buy"})
    assert body == {"code": "0", "data": [{"ordId": "1"}]}
    assert len(fake.sent_contents) == 2                       # 首发 429 退避吞掉，次发 200 成功
    assert fake.sent_contents[0] == fake.sent_contents[1]     # 同一 body / 同一 clOrdId（幂等安全）


async def test_post_exhausts_429_raises_http_error(monkeypatch):
    """限流回归：持续 429 至退避耗尽，须显式抛 ``OKX_HTTP_ERROR / OKX HTTP 429``（非 last_exc=None 误导串）。"""
    monkeypatch.setattr("app.okx.trade_client.asyncio.sleep", _no_sleep)
    client = OKXTradeClient("ak", "sk-post-vector-123456", "pp-123456")
    fake = _SequencedClient([429, 429, 429], {"code": "0", "data": []})
    client._client = fake
    with pytest.raises(OKXPrivateError) as exc:
        await client._post("/api/v5/trade/order", {"instId": "BTC-USDT-SWAP"})
    assert exc.value.code == "OKX_HTTP_ERROR"
    assert "429" in exc.value.message


async def _no_sleep(_seconds) -> None:
    """退避桩：跳过真实 sleep，避免单测被指数退避拖慢。"""
    return None
