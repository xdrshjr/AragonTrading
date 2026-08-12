"""私有写的幂等分级与平仓读回核验（okx-link-resilience §2.4.3）——R-1 / R-17。

R-1 是本轮最严重的风险（资金损失）：`_post` 今天对 `TransportError` **一律**重试三次。对
`place_order` / `order-algo` 这是安全的（OKX 按 `clOrdId` / `algoClOrdId` 去重），但
`close-position` **没有这个承诺**——一次「请求已送达、响应在回程丢了」的超时，重试就可能变成
第二次平仓动作。

R-17 是它的影子：新的「不确定」码若没有配套写死「谁都不许据它自动重发平仓」，就是在
`ai-trade-backstop-repair` 刚付过代价的那个坑上重新铺一块木板。
"""
from __future__ import annotations

import httpx
import pytest

from app.config import Settings
from app.okx.trade_client import OKXTradeClient, _has_open_position
from app.okx.private_client import OKXPrivateError
from app.okx.write_safety import IDEMPOTENT_WRITES, NON_IDEMPOTENT_WRITES, may_retry_write


# ---------------- 分级表（纯函数） ----------------


@pytest.mark.parametrize(("path", "payload", "expected"), [
    ("/api/v5/trade/order", {"clOrdId": "aragonx1"}, True),
    ("/api/v5/trade/order", {}, False),               # 声称按 clOrdId 去重却没带它 ⇒ 不许重试
    ("/api/v5/trade/order-algo", {"algoClOrdId": "a1"}, True),
    ("/api/v5/trade/order-algo", {"clOrdId": "a1"}, False),   # 键名不对也是没带
    ("/api/v5/trade/cancel-order", {}, True),         # 天然幂等
    ("/api/v5/account/set-leverage", {}, True),       # 幂等设置
    ("/api/v5/trade/close-position", {"clOrdId": "x"}, False),  # 有键 ≠ 有去重语义
    ("/api/v5/trade/some-future-endpoint", {"clOrdId": "x"}, False),  # 白名单默认答案 = 不重试
])
def test_may_retry_write_is_a_whitelist(path, payload, expected):
    assert may_retry_write(path, payload) is expected


def test_close_position_is_explicitly_registered_as_non_idempotent():
    assert "/api/v5/trade/close-position" in NON_IDEMPOTENT_WRITES
    assert "/api/v5/trade/close-position" not in IDEMPOTENT_WRITES


# ---------------- `_post` 的实际行为 ----------------


class _Resp:
    def __init__(self, body: dict, status_code: int = 200):
        self.status_code = status_code
        self._body = body

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._body


class _FlakyPoster:
    """前 `fail_times` 次抛传输层异常，之后成功；记录**实际发出**的请求次数。"""

    def __init__(self, fail_times: int, body: dict | None = None):
        self._fail_times = fail_times
        self._body = body or {"code": "0", "data": [{"sCode": "0"}]}
        self.posts = 0

    async def post(self, path, content=None, headers=None):
        self.posts += 1
        if self.posts <= self._fail_times:
            raise httpx.ReadTimeout("回程丢了")
        return _Resp(self._body)


def _client(**over) -> OKXTradeClient:
    base = dict(okx_link_private_rate_per_s=0.0, okx_link_verify_attempts=3,
                okx_link_verify_backoff_s=0.0)
    base.update(over)
    return OKXTradeClient("ak", "sk-123456", "pp-123456", settings=Settings(**base))


async def test_an_idempotent_write_is_retried_after_a_transport_error():
    client = _client()
    poster = _FlakyPoster(fail_times=1)
    client._client = poster

    body = await client._post("/api/v5/trade/order", {"clOrdId": "aragonx1"})

    assert body["code"] == "0"
    assert poster.posts == 2


async def test_a_non_idempotent_write_is_never_retried():
    """`close-position` 的传输层异常**只发一次**——重试可能变成第二次平仓动作。"""
    client = _client()
    poster = _FlakyPoster(fail_times=5)
    client._client = poster

    with pytest.raises(OKXPrivateError) as exc:
        await client._post("/api/v5/trade/close-position", {"instId": "BTC-USDT-SWAP"})

    assert exc.value.code == "OKX_UNREACHABLE"
    assert poster.posts == 1


async def test_allow_retry_can_be_overridden_explicitly():
    client = _client()
    poster = _FlakyPoster(fail_times=1)
    client._client = poster
    with pytest.raises(OKXPrivateError):
        await client._post("/api/v5/trade/order", {"clOrdId": "x"}, allow_retry=False)
    assert poster.posts == 1


# ---------------- 平仓读回核验（[评审 P1-6]） ----------------


class _ClosePositionClient:
    """平仓 POST 恒超时；`get_positions` 按脚本逐次返回（模拟交易所侧归零的延迟）。"""

    def __init__(self, position_script: list[list]):
        self._script = list(position_script)
        self.posts = 0
        self.gets = 0

    async def post(self, path, content=None, headers=None):
        self.posts += 1
        raise httpx.ReadTimeout("平仓请求送达了，响应在回程丢了")

    async def get(self, path, params=None, headers=None):
        self.gets += 1
        item = self._script[min(self.gets - 1, len(self._script) - 1)]
        if isinstance(item, Exception):
            raise item
        return _Resp({"code": "0", "data": item})


_POS = [{"instId": "BTC-USDT-SWAP", "posSide": "long", "pos": "3"}]


async def test_a_bounded_reread_recognises_a_close_that_actually_succeeded():
    """第 1 次读仍见持仓、第 2 次读为 0 ⇒ **判成功**。

    只读一次会把「其实成功了」稳定地误报成「不确定」——而「不确定」在自愈层里是最贵的一种回答。
    """
    client = _client()
    fake = _ClosePositionClient([_POS, []])
    client._client = fake

    row = await client.close_position("BTC-USDT-SWAP", "long", "cross")

    assert row["verified"] is True
    assert row["instId"] == "BTC-USDT-SWAP" and row["posSide"] == "long"
    assert fake.posts == 1, "核验期间绝不许再发一次平仓"


async def test_a_position_that_never_clears_yields_a_terminal_uncertain_code():
    client = _client()
    fake = _ClosePositionClient([_POS, _POS, _POS])
    client._client = fake

    with pytest.raises(OKXPrivateError) as exc:
        await client.close_position("BTC-USDT-SWAP", "long", "cross")

    assert exc.value.code == "OKX_SEND_UNCERTAIN"
    assert "人工核对" in exc.value.message
    assert fake.posts == 1, "`close_position` 只被发出过一次"
    assert fake.gets == 3, "读回核验按 okx_link_verify_attempts 有界重读"


async def test_a_failing_verification_never_fakes_success():
    """核验自身也失败 ⇒ 仍是 `OKX_SEND_UNCERTAIN`，**绝不**假成功、**绝不**盲目再发一次。"""
    client = _client()
    fake = _ClosePositionClient([httpx.ConnectError("读不到")])
    client._client = fake

    with pytest.raises(OKXPrivateError) as exc:
        await client.close_position("BTC-USDT-SWAP", "long", "cross")

    assert exc.value.code == "OKX_SEND_UNCERTAIN"
    assert fake.posts == 1


async def test_a_business_rejection_is_not_turned_into_uncertain():
    """业务拒单是**确定**的失败，不该被核验路径改判成「不确定」。"""
    class _Rejecting:
        def __init__(self):
            self.posts = 0

        async def post(self, path, content=None, headers=None):
            self.posts += 1
            return _Resp({"code": "1", "data": [{"sCode": "51023", "sMsg": "无持仓"}]})

    client = _client()
    fake = _Rejecting()
    client._client = fake

    with pytest.raises(OKXPrivateError) as exc:
        await client.close_position("BTC-USDT-SWAP", "long", "cross")

    assert exc.value.code == "51023"


# ---------------- 持仓判据 ----------------


@pytest.mark.parametrize(("positions", "expected"), [
    ([], False),
    ([{"instId": "BTC-USDT-SWAP", "posSide": "long", "pos": "0"}], False),
    ([{"instId": "BTC-USDT-SWAP", "posSide": "long", "pos": ""}], False),
    ([{"instId": "BTC-USDT-SWAP", "posSide": "long", "pos": "2"}], True),
    ([{"instId": "ETH-USDT-SWAP", "posSide": "long", "pos": "2"}], False),   # 别的标的
    ([{"instId": "BTC-USDT-SWAP", "posSide": "short", "pos": "2"}], False),  # 别的方向
    ([{"instId": "BTC-USDT-SWAP", "posSide": "long", "pos": "看不懂"}], True),  # 读不懂 ⇒ 保守
])
def test_has_open_position(positions, expected):
    assert _has_open_position(positions, "BTC-USDT-SWAP", "long") is expected


def test_net_mode_matches_any_side():
    rows = [{"instId": "BTC-USDT-SWAP", "posSide": "net", "pos": "-2"}]
    assert _has_open_position(rows, "BTC-USDT-SWAP", "net") is True
    assert _has_open_position(rows, "BTC-USDT-SWAP", "long") is True
