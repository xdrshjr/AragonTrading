"""账户读取白名单、绑定冻结与分页诚实性。"""

import json

import pytest

from tests.test_agent_trading_intents import store as store


def test_snapshot_units_preserve_spot_quote_and_margin_currency():
    from app.agent_trading.read_service import project_fill, project_position

    fill = project_fill({
        "instId": "ETH-BTC", "instType": "SPOT", "fillSz": "2",
        "fillPx": "0.04", "fee": "0.001", "feeCcy": "ETH",
    })
    assert fill["fieldUnits"]["fillSz"] == "ETH"
    assert fill["fieldUnits"]["fillPx"] == "BTC"
    assert fill["fieldUnits"]["fee"] == "ETH"
    position = project_position({
        "instId": "BTC-USD-SWAP", "instType": "SWAP", "pos": "3",
        "ccy": "BTC", "margin": "0.01", "upl": "0.002",
    })
    assert position["fieldUnits"]["pos"] == "contracts"
    assert position["fieldUnits"]["markPx"] == "USD"
    assert position["fieldUnits"]["margin"] == "BTC"
    assert position["fieldUnits"]["upl"] == "BTC"


def test_spot_market_order_size_uses_target_currency_not_fill_unit():
    from app.agent_trading.read_service import project_order

    order = project_order({
        "instId": "ETH-USDT", "instType": "SPOT", "ordType": "market",
        "side": "buy", "sz": "100", "accFillSz": "0.02",
        "tgtCcy": "quote_ccy",
    })
    assert order["fieldUnits"]["sz"] == "USDT"
    assert order["fieldUnits"]["accFillSz"] == "ETH"


def test_snapshot_does_not_claim_uniform_contract_or_usdt_units():
    from app.agent_trading.read_service import _envelope
    from app.agent_trading.schemas import EmptyRequest

    result = _envelope(None, 1, ([], True, []), EmptyRequest())
    assert result.get("quantityUnit") != "contracts"
    assert result.get("amountCurrency") != "USDT"


def test_balance_detail_units_follow_each_currency():
    from app.agent_trading.read_service import project_balance

    result = project_balance({"details": [
        {"ccy": "ETH", "eq": "1", "availEq": "0.5", "eqUsd": "1000"}
    ]}, {"acctLv": "2"})
    units = result["details"][0]["fieldUnits"]
    assert units["availEq"] == "ETH"
    assert units["eqUsd"] == "USD"


@pytest.mark.asyncio
async def test_binding_version_and_mode_are_frozen(store, monkeypatch):
    from app.agent_trading.binding import (
        BindingService,
        freeze_call,
        validate_call,
    )
    from app.agent_trading.errors import TradingError
    from app.db.models import OkxAccount

    async with store() as session:
        session.add(
            OkxAccount(
                id=1,
                label="账户",
                api_key="key",
                secret_key="secret",
                passphrase="pass",
            )
        )
        await session.commit()
    service = BindingService(store)
    row = await service.put(
        "chat", {"accountId": 1, "accountMode": "live", "expectedVersion": 0}
    )
    call = await freeze_call(store, "chat", "msg", "live")
    assert (await validate_call(call, store)).account_id == 1
    await service.put(
        "chat",
        {"accountId": 1, "accountMode": "sim", "expectedVersion": row.version},
    )
    with pytest.raises(TradingError, match="BINDING_CHANGED"):
        await validate_call(call, store)
    with pytest.raises(TradingError):
        await service.put(
            "chat",
            {"accountId": 1, "accountMode": "live", "expectedVersion": 1},
        )


def test_projection_preserves_unknown_and_excludes_secrets():
    from app.agent_trading.read_service import project_balance

    raw = {
        "totalEq": "12.30",
        "uid": "SECRET",
        "details": [
            {"ccy": "USDT", "availBal": "", "availEq": "", "apiKey": "SECRET"}
        ],
    }
    result = project_balance(raw, {"acctLv": "2"})
    assert result["availableMarginUsdt"] is None
    assert "SECRET" not in json.dumps(result)


@pytest.mark.asyncio
async def test_oco_legs_have_stable_distinct_keys_and_empty_partial_is_honest():
    from app.agent_trading.read_service import fetch_open_orders

    class Client:
        async def _get(self, path, params):
            if params.get("ordType") == "oco":
                return [
                    {
                        "algoId": "42",
                        "tpTriggerPx": "120",
                        "slTriggerPx": "90",
                        "instId": "BTC-USDT-SWAP",
                    }
                ]
            return []

    result = await fetch_open_orders(Client())
    assert {row["rowKey"] for row in result["rows"]} == {
        "oco:42:tp",
        "oco:42:sl",
    }

    class PartialClient:
        async def _get(self, path, params):
            if params.get("ordType") == "oco":
                raise RuntimeError("offline")
            return []

    result = await fetch_open_orders(PartialClient())
    assert result["rows"] == []
    assert result["complete"] is False


def test_position_projection_preserves_decimal_without_private_fields():
    from app.agent_trading.read_service import project_position

    position = project_position(
        {
            "instId": "BTC-USDT-SWAP",
            "pos": "1.100",
            "mgnMode": "cross",
            "posSide": "net",
            "secret": "SECRET",
        }
    )
    assert position["pos"] == "1.1"
    assert "SECRET" not in json.dumps(position)


@pytest.mark.asyncio
async def test_pending_partial_pagination_is_not_complete():
    from app.agent_trading.read_service import fetch_open_orders

    class Client:
        async def _get(self, path, params):
            if params.get("after"):
                raise RuntimeError("SECRET network failure")
            if "algo" in path:
                return []
            return [
                {"ordId": str(i), "instId": "BTC-USDT-SWAP"} for i in range(100)
            ]

    result = await fetch_open_orders(Client())
    assert result["complete"] is False
    assert len(result["rows"]) == 100
    assert "SECRET" not in json.dumps(result)


@pytest.mark.asyncio
async def test_pending_missing_identity_never_claims_complete():
    from app.agent_trading.read_service import fetch_open_orders

    class Client:
        async def _get(self, path, params):
            if "algo" in path:
                return []
            return [{"instId": "BTC-USDT-SWAP"}]

    result = await fetch_open_orders(Client())
    assert result["complete"] is False
    assert result["rows"] == []


@pytest.mark.asyncio
async def test_complete_two_page_orders_keep_all_unique_orders_and_total():
    from app.agent_trading.read_service import fetch_open_orders, _envelope
    from app.agent_trading.schemas import PositionQuery

    calls = []

    class Client:
        async def _get(self, path, params):
            if "algo" in path:
                return []
            calls.append(dict(params))
            start, end = (0, 100) if not params.get("after") else (99, 103)
            return [
                {"ordId": str(index), "instId": "BTC-USDT-SWAP"}
                for index in range(start, end)
            ]

    result = await fetch_open_orders(Client())
    assert len(calls) == 2
    assert calls[1]["after"] == "99"
    assert result["complete"] is True
    assert len(result["rows"]) == 103
    assert len({row["rowKey"] for row in result["rows"]}) == 103
    snapshot = _envelope(
        None, 1, (result["rows"], True, []), PositionQuery(limit=50)
    )
    assert snapshot["total"] == 103
    assert 0 < snapshot["returnedCount"] <= 50
    assert snapshot["returnedCount"] == len(snapshot["data"])
    assert len(json.dumps(snapshot, ensure_ascii=False).encode()) <= 24 * 1024
    assert snapshot["hasMore"] is True


@pytest.mark.asyncio
async def test_five_page_limit_is_partial_with_unknown_total():
    from app.agent_trading.read_service import fetch_open_orders, _envelope
    from app.agent_trading.schemas import PositionQuery

    calls = []

    class Client:
        async def _get(self, path, params):
            if "algo" in path:
                return []
            start = len(calls) * 100
            calls.append(dict(params))
            return [
                {"ordId": str(index), "instId": "BTC-USDT-SWAP"}
                for index in range(start, start + 100)
            ]

    result = await fetch_open_orders(Client())
    assert len(calls) == 5
    assert len(result["rows"]) == 500
    assert result["complete"] is False
    snapshot = _envelope(
        None, 1, (result["rows"], False, result["warnings"]), PositionQuery()
    )
    assert snapshot["total"] is None
    assert snapshot["observedCount"] == 500
    assert snapshot["coverage"]["status"] == "partial"
    assert snapshot["hasMore"] is True
    assert snapshot["warnings"]


async def test_read_deadline_preserves_orders_from_completed_pages(monkeypatch):
    import asyncio
    from types import SimpleNamespace
    from app.agent_trading import read_service

    identity = SimpleNamespace(
        account_id=1, label="账户", environment="demo",
        credentials=SimpleNamespace(
            api_key="key", secret_key="secret", passphrase="pass"
        ),
    )

    async def validate(*args):
        return identity

    class Client:
        def __init__(self, *args):
            pass

        async def _get(self, path, params):
            if params.get("after"):
                await asyncio.sleep(10)
            return [{"ordId": str(i), "instId": "BTC-USDT-SWAP"}
                    for i in range(100)]

        async def aclose(self):
            pass

    monkeypatch.setattr(read_service, "READ_TIMEOUT_S", 0.05)
    monkeypatch.setattr(read_service, "validate_call", validate)
    monkeypatch.setattr(read_service, "OKXTradeClient", Client)
    result = await read_service.read_account("orders", {}, object())
    assert result["coverage"]["status"] == "partial"
    assert result["total"] is None
    assert result["observedCount"] == 100
    assert result["data"]
