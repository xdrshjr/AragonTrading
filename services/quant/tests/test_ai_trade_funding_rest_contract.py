"""真实 REST 适配的资金费输入保真，网络请求由本地替身隔离。"""
import pytest

from app.ai_auto.funding_ledger import BacktestFundingCurve
from app.ai_auto.funding import FundingCache
from app.okx.rest_client import OKXRestClient


def _client(rows):
    client = object.__new__(OKXRestClient)
    client._funding_cache = {}

    async def request(*_args, **_kwargs):
        return rows

    client._request = request
    return client


@pytest.mark.parametrize("value", [None, "", True, "NaN", "Infinity"])
async def test_history_preserves_invalid_rates_as_unknown(value):
    rows = await _client([{"fundingTime": "1700000000000",
                          "fundingRate": value,
                          "realizedRate": value}]).get_funding_rate_history("X")
    assert rows == [{"fundingTs": 1700000000,
                     "fundingRate": None, "realizedRate": None}]
    assert BacktestFundingCurve(rows).points_in(0, 1800000000) == []


@pytest.mark.parametrize("value", [None, "", True, "NaN", "Infinity"])
async def test_live_preserves_invalid_rates_as_unknown(value):
    row = await _client([{"fundingTime": "1700000000000",
                         "nextFundingTime": "1700028800000",
                         "fundingRate": value}]).get_funding_rate("X")
    assert row["fundingRate"] is None
    cache = FundingCache("X", ttl_s=1)

    async def fetch(_inst_id):
        return row

    assert await cache.refresh(fetch, 100) is False
    assert cache.snapshot(100) is None


async def test_history_keeps_untimed_rows_and_signed_zero():
    rows = await _client([
        {"fundingTime": "", "fundingRate": ".01"},
        {"fundingTime": "1700000000000", "fundingRate": "0",
         "realizedRate": "-0.001"},
    ]).get_funding_rate_history("X")
    assert len(rows) == 2
    assert rows[0] == {"fundingTs": 1700000000,
                       "fundingRate": 0, "realizedRate": -0.001}
    assert rows[1]["fundingTs"] is None


async def test_live_invalid_schedule_never_becomes_known():
    row = await _client([{"fundingTime": "0",
                         "nextFundingTime": "1700028800000",
                         "fundingRate": "0"}]).get_funding_rate("X")
    assert row["intervalKnown"] is False
