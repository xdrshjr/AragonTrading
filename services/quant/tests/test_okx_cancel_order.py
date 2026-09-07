"""撤单 / 查单的客户端与服务层单测（ai-trade-limit-orders §4.3 / §4.4）。

分两层：客户端层（`OKXTradeClient.cancel_order` / `get_order`）只验「本地必填拦截 + 双层
code/sCode 校验 + 忠实上抛原始 sCode」；服务层只验**纯函数** `_map_trade_code` / `_cancel_outcome` /
`_normalize_order`——三态归因的全部风险都收敛在这三个查表里，故不必构造 DB session 即可锁死。

本文件的核心是 `test_cancel_live_order_maps_three_outcomes`（P0-2）：「已撤销」（无持仓）与
「已成交」（有真实持仓）的持仓后果**完全相反**，一旦被合并成同一个「订单不在了」的归一码，撤旧挂新
就会在一笔已成交的仓位之上再开一单，敞口翻倍。全部用例纯离线（无网络、无 DB、假凭证）。
"""
from __future__ import annotations

import pytest

from app.okx.private_client import OKXPrivateError
from app.okx.trade_client import OKXTradeClient
from app.okx.trading import _cancel_outcome, _map_trade_code, _normalize_order

# 假凭证：仅供构造客户端，全部用例均已桩掉 _post/_get，绝不出网（R-Secret）。
_FAKE_KEY = "ak-fake"
_FAKE_SECRET = "sk-fake-000000"
_FAKE_PASSPHRASE = "pp-fake-000000"


def _client() -> OKXTradeClient:
    return OKXTradeClient(_FAKE_KEY, _FAKE_SECRET, _FAKE_PASSPHRASE)


class _StubPost:
    """桩 `_post`：记录 path/payload，返回预置 OKX 响应体。"""

    def __init__(self, body: dict):
        self._body = body
        self.path: str | None = None
        self.payload: dict | None = None

    async def __call__(self, path: str, payload: dict) -> dict:
        self.path = path
        self.payload = payload
        return self._body


# ---------------- 客户端层：cancel_order ----------------


async def test_cancel_order_rejects_request_without_ord_id_or_cl_ord_id():
    """本地拦截：`ordId` / `clOrdId` 二者皆缺时不发请求（OKX 侧会撤掉什么无法预期）。"""
    with pytest.raises(OKXPrivateError) as exc:
        await _client().cancel_order("BTC-USDT-SWAP")
    assert exc.value.code == "ORDER_PARAM_INVALID"


async def test_cancel_order_raises_with_specific_scode_not_top_level_code():
    """双层码校验：以**具体 sCode** 抛错，而非顶层 code。

    顶层 code 恒为笼统的 `"1"`，三态归因全靠 sCode——上抛顶层码等于把 51402（已成交）与
    51401（已撤销）压成同一个错误，P0-2 的二义陷阱当场复发。
    """
    client = _client()
    client._post = _StubPost({"code": "1", "data": [{"sCode": "51402", "sMsg": "已成交"}]})
    with pytest.raises(OKXPrivateError) as exc:
        await client.cancel_order("BTC-USDT-SWAP", cl_ord_id="aragon-1")
    assert exc.value.code == "51402"


async def test_cancel_order_returns_row_and_posts_cl_ord_id_on_success():
    client = _client()
    stub = _StubPost({"code": "0", "data": [{"sCode": "0", "ordId": "9", "clOrdId": "aragon-1"}]})
    client._post = stub
    row = await client.cancel_order("BTC-USDT-SWAP", cl_ord_id="aragon-1")
    assert row["ordId"] == "9"
    assert stub.path == "/api/v5/trade/cancel-order"
    assert stub.payload == {"instId": "BTC-USDT-SWAP", "clOrdId": "aragon-1"}


async def test_cancel_order_raises_on_non_zero_top_code_without_scode():
    """顶层 code 非 0 而逐项位无 sCode（连通/鉴权类）时，回落顶层码抛错。"""
    client = _client()
    client._post = _StubPost({"code": "50011", "msg": "rate limit", "data": []})
    with pytest.raises(OKXPrivateError) as exc:
        await client.cancel_order("BTC-USDT-SWAP", ord_id="9")
    assert exc.value.code == "50011"


# ---------------- 客户端层：get_order ----------------


class _StubGet:
    """桩 `_get`：返回预置 OKX `data` 数组。"""

    def __init__(self, rows: list):
        self._rows = rows
        self.path: str | None = None
        self.params: dict | None = None

    async def __call__(self, path: str, params: dict | None = None) -> list:
        self.path = path
        self.params = params
        return self._rows


async def test_get_order_rejects_request_without_ord_id_or_cl_ord_id():
    with pytest.raises(OKXPrivateError) as exc:
        await _client().get_order("BTC-USDT-SWAP")
    assert exc.value.code == "ORDER_PARAM_INVALID"


async def test_get_order_returns_empty_dict_when_okx_returns_no_rows():
    """订单不存在 ⇒ `{}`（归一后 `state==""` ⇒ classify_state → unknown ⇒ 保留挂单、下轮再判）。"""
    client = _client()
    client._get = _StubGet([])
    assert await client.get_order("BTC-USDT-SWAP", cl_ord_id="aragon-1") == {}


async def test_get_order_returns_first_row_with_cl_ord_id_query():
    client = _client()
    stub = _StubGet([{"state": "live", "ordId": "9"}])
    client._get = stub
    row = await client.get_order("BTC-USDT-SWAP", cl_ord_id="aragon-1")
    assert row == {"state": "live", "ordId": "9"}
    assert stub.path == "/api/v5/trade/order"
    assert stub.params == {"instId": "BTC-USDT-SWAP", "clOrdId": "aragon-1"}


# ---------------- 服务层：错误码归一 ----------------


@pytest.mark.parametrize("code, expected", [
    ("51400", "ORDER_NOT_FOUND"),          # 撤单失败：订单不存在（状态未知）
    ("51401", "ORDER_ALREADY_CANCELED"),   # 撤单失败：订单已撤销（无持仓）
    ("51402", "ORDER_ALREADY_FILLED"),     # 撤单失败：订单已成交（**有真实持仓**）
    ("51006", "LIMIT_PRICE_OUT_OF_RANGE"),
    ("51121", "ORDER_SIZE_NOT_MULTIPLE"),
])
def test_map_trade_code_maps_limit_order_codes(code, expected):
    assert _map_trade_code(code) == expected


def test_map_trade_code_keeps_cancel_codes_distinct():
    """三个撤单码必须映射到三个互不相同的归一码——合并即丢失持仓后果的差异。"""
    assert len({_map_trade_code(c) for c in ("51400", "51401", "51402")}) == 3


# ---------------- 服务层：三态归因（P0-2 核心）----------------


def test_cancel_live_order_maps_three_outcomes():
    """【P0-2 守门】撤单是**三态**不是两态：成功/已撤销、已成交、状态未知。

    「撤单请求发出的同时那笔挂单成交了」（51402）是真实存在的第三态。若把它当「良性幂等」
    与 51401 一并丢弃 resting 后照常下新单，就会在一笔**已经成交的真实仓位**之上再开一笔——
    敞口翻倍且自管 `projected` 还以为自己是 flat。这是本特性唯一能凭一个错误码把风险放大一倍
    的路径，故三态必须互不相等。
    """
    assert _cancel_outcome("ORDER_ALREADY_CANCELED") == "canceled"
    assert _cancel_outcome("ORDER_ALREADY_FILLED") == "filled"
    assert _cancel_outcome("ORDER_NOT_FOUND") == "unknown"
    outcomes = {_cancel_outcome("ORDER_ALREADY_CANCELED"),
                _cancel_outcome("ORDER_ALREADY_FILLED"),
                _cancel_outcome("ORDER_NOT_FOUND")}
    assert len(outcomes) == 3      # 「已撤销」(无持仓) 与「已成交」(有持仓) 绝不可合并


@pytest.mark.parametrize("mapped", ["OKX_ERROR", "OKX_UNREACHABLE", "INSUFFICIENT_BALANCE", ""])
def test_cancel_outcome_falls_back_to_unknown_for_unmapped_codes(mapped):
    """未归因的码一律 `"unknown"` ⇒ 保留 resting、下轮再判（兜底方向必须是「不动」）。"""
    assert _cancel_outcome(mapped) == "unknown"


def test_cancel_outcome_chains_from_okx_scode_through_map_trade_code():
    """端到端串联：原始 sCode → `_map_trade_code` → 三态，锁住两张表之间的键对齐。"""
    assert _cancel_outcome(_map_trade_code("51401")) == "canceled"
    assert _cancel_outcome(_map_trade_code("51402")) == "filled"
    assert _cancel_outcome(_map_trade_code("51400")) == "unknown"


# ---------------- 服务层：订单归一 ----------------


def test_normalize_order_maps_fields_and_converts_utime_to_seconds():
    assert _normalize_order({
        "instId": "BTC-USDT-SWAP", "side": "buy", "posSide": "net",
        "state": "partially_filled", "px": "61234.5", "sz": "0.03", "avgPx": "61230.1",
        "accFillSz": "0.01", "ordId": "9", "clOrdId": "aragon-1", "uTime": "1710000001123",
    }) == {
        "instId": "BTC-USDT-SWAP", "side": "buy", "posSide": "net",
        "state": "partially_filled", "px": 61234.5, "sz": 0.03, "avgPx": 61230.1,
        "accFillSz": 0.01, "ordId": "9", "clOrdId": "aragon-1", "updatedTs": 1710000001,
    }


def test_normalize_order_tolerates_empty_row_from_missing_order():
    """订单不存在（`get_order` 回 `{}`）时归一为全空态，绝不炸——`state==""` 走 unknown 分支。"""
    assert _normalize_order({}) == {
        "state": "", "px": None, "sz": None, "avgPx": None, "accFillSz": None,
        "instId": "", "side": "", "posSide": "",
        "ordId": None, "clOrdId": None, "updatedTs": 0,
    }


def test_normalize_order_maps_unfilled_live_order_with_blank_avg_px():
    """价格与累计量空串均保留未知；不能把未提供的成交量当作明确零成交。"""
    row = _normalize_order({"state": "live", "px": "100", "sz": "0.03", "avgPx": "",
                            "accFillSz": "", "ordId": "9", "clOrdId": "aragon-1",
                            "uTime": "1710000001999"})
    assert row["state"] == "live"
    assert row["avgPx"] is None
    assert row["accFillSz"] is None
    assert row["updatedTs"] == 1710000001
