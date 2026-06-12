"""OKX 归一化单测 (spec R7 / C2)：毫秒->秒、升序、number 类型。"""
from __future__ import annotations

from app.okx.types import parse_okx_candle


def test_ms_to_seconds():
    # OKX 行：[ts(ms), o, h, l, c, vol, volCcy, volCcyQuote, confirm]
    row = ["1781287200000", "63836.3", "63907.2", "63681.5", "63799.9",
           "69400.64", "694.0", "44281773.95", "1"]
    c = parse_okx_candle(row)
    assert c.ts == 1781287200          # 毫秒 // 1000 = 秒
    assert isinstance(c.ts, int)
    assert c.o == 63836.3 and c.c == 63799.9
    assert c.confirm is True


def test_unconfirmed_flag():
    row = ["1781283600000", "1", "2", "0.5", "1.5", "10", "1", "1", "0"]
    c = parse_okx_candle(row)
    assert c.confirm is False
