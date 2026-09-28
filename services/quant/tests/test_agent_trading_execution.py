"""聊天交易发送边界；所有网络均使用 MockTransport。"""
import httpx
import pytest
from app.config import Settings
from app.okx.trade_client import OKXTradeClient, SendOptions
from app.okx.order_builder import ClosePositionInput, InstrumentMeta, build_close_order_payload, validate_close

@pytest.mark.asyncio
async def test_cancel_timeout_sends_once():
    calls=[]
    def transport(request):
        calls.append(request)
        raise httpx.ReadTimeout("响应丢失")
    client=OKXTradeClient("a","b","c", Settings())
    client._client=httpx.AsyncClient(base_url="https://example.test",transport=httpx.MockTransport(transport))
    with pytest.raises(Exception):
        await client.cancel_order("BTC-USDT-SWAP",ord_id="123",options=SendOptions(allow_retry=False))
    assert len(calls)==1
    await client.aclose()

@pytest.mark.asyncio
async def test_cancel_guard_runs_after_slot_before_post():
    calls=[]
    client=OKXTradeClient("a","b","c", Settings())
    client._client=httpx.AsyncClient(base_url="https://example.test",transport=httpx.MockTransport(lambda r: calls.append(r)))
    def deny():
        raise ValueError("已过期")
    with pytest.raises(ValueError,match="已过期"):
        await client.cancel_order("BTC-USDT-SWAP",ord_id="123",options=SendOptions(False,deny))
    assert calls==[]
    await client.aclose()

def test_market_close_is_sized_reduce_only_without_price():
    meta=InstrumentMeta(0.01,"BTC",1,1,0.1,100,"linear","USDT")
    close=ClosePositionInput("BTC-USDT-SWAP","net",10,"cross","market",None,2)
    assert validate_close(close,meta,10,order_type="market")=={"sz":2}
    body=build_close_order_payload(close,meta,"net_mode",2,"agt1",order_type="market")
    assert body["reduceOnly"] is True
    assert body["sz"]=="2"
    assert body["ordType"]=="market"
    assert "px" not in body

@pytest.mark.asyncio
async def test_client_environment_is_frozen():
    settings=Settings(okx_account_demo=True)
    client=OKXTradeClient("a","b","c",settings)
    settings.okx_account_demo=False
    assert client._headers("now","GET","/test")["x-simulated-trading"]=="1"


@pytest.mark.asyncio
async def test_dispatch_metadata_bypasses_public_cache(monkeypatch):
    from app.okx import trading
    class Public:
        async def _request(self, path, params):
            return [{"instId": params["instId"], "ctVal": "0.02", "ctValCcy": "BTC",
                     "lotSz": "1", "minSz": "1", "tickSz": "0.1",
                     "ctType": "linear", "settleCcy": "USDT"}]
        async def get_instrument_meta(self, *args):
            raise AssertionError("不能用缓存元数据标记为刚观测")
    monkeypatch.setattr(trading, "get_rest_client", lambda: Public())
    assert (await trading._load_meta("BTC-USDT-SWAP", fresh=True)).ct_val == 0.02
