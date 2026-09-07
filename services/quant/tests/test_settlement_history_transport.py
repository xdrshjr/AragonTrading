"""实际 GET 参数替身与原始值边界；不访问交易所。"""
import pytest

from app.okx.accounts import _normalize_position
from app.okx.trade_client import OKXTradeClient
from app.okx.trading import _normalize_position_history


@pytest.mark.asyncio
async def test_after_semantics_send_before_and_keep_one_page(monkeypatch):
    client = OKXTradeClient("test", "test", "test")
    calls = []
    async def get(path, params):
        calls.append((path, params))
        return [{"uTime": "1234001"}]
    monkeypatch.setattr(client, "_get", get)
    try:
        assert await client.get_position_history(
            "SWAP", "SOL-USDT-SWAP", after_ms=1234000)
        assert len(calls) == 1
        assert calls[0][1]["before"] == "1234000"
        assert "after" not in calls[0][1]
    finally:
        await client.aclose()


@pytest.mark.parametrize("value", [True, False, float("inf"), {}, "oops"])
def test_invalid_raw_numbers_are_not_coerced_to_money(value):
    normalized = _normalize_position_history(
        {"instType": "SWAP", "fee": value, "settledPnl": value})
    assert normalized["fee"] is None
    assert "fee" in normalized["invalidFields"]
    assert "settledPnl" in normalized["invalidFields"]


def test_swap_missing_settled_pnl_is_explicit_not_applicable():
    normalized = _normalize_position_history(
        {"instType": "SWAP", "settledPnl": "", "fee": "0",
         "cTime": "1000001", "uTime": "1000002", "type": "2",
         "closeTotalPos": "10", "mgnMode": "cross", "settleCcy": "BTC"})
    assert normalized["settledPnl"] == 0
    assert normalized["amountSources"]["settledPnl"] == "not_applicable"
    assert normalized["exchangeCTimeMs"] == "1000001"
    assert normalized["exchangeUTimeMs"] == "1000002"
    assert normalized["marginMode"] == "cross"
    assert normalized["currency"] == "BTC"
    assert normalized["closeTotalPos"] == 10
    assert _normalize_position_history({"instType": "FUTURES"})[
        "settledPnl"] is None


def test_positions_preserve_original_milliseconds_without_fabrication():
    normalized = _normalize_position({"cTime": "1000001",
                                      "uTime": "1000002"})
    assert normalized["cTimeS"] == normalized["uTimeS"] == 1000
    assert normalized["exchangeCTimeMs"] == "1000001"
    assert normalized["exchangeUTimeMs"] == "1000002"
    assert _normalize_position({"cTime": True})["exchangeCTimeMs"] is None
