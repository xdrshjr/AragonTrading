import { describe, expect, it } from "vitest";
import type { Candle, MarketTicker, MarketTrade, OrderBookSnapshot } from "./types";
import { bookSideTotals, bookSpread, mergeTradeTape, resolveLivePrice } from "./marketDepth";

describe("market depth helpers", () => {
  it("dedupes trade ids, sorts newest first, and caps rows", () => {
    const older: MarketTrade = { ts: 10, price: 1, qty: 1, side: "buy", tradeId: "a" };
    const newer: MarketTrade = { ts: 12, price: 2, qty: 1, side: "sell", tradeId: "b" };
    const duplicate: MarketTrade = { ts: 11, price: 3, qty: 1, side: "buy", tradeId: "a" };

    const rows = mergeTradeTape([older], newer, 2);
    expect(mergeTradeTape(rows, duplicate, 2)).toEqual([newer, older]);
  });

  it("returns spread and percentage from top book levels", () => {
    const book: OrderBookSnapshot = {
      ts: 1,
      asks: [{ price: 105, qty: 2 }],
      bids: [{ price: 100, qty: 3 }],
    };
    expect(bookSpread(book)).toEqual({ value: 5, pct: 5 / 100 });
    expect(bookSpread({ ts: 1, asks: [], bids: book.bids })).toBeNull();
  });

  it("prefers newer trade or ticker prices over older candles", () => {
    const candle: Candle = { ts: 10, o: 1, h: 2, l: 0.5, c: 1.5, vol: 10 };
    const olderTicker: MarketTicker = { ts: 9, last: 1.6, open24h: null, vol24h: null, volCcy24h: null };
    const trade: MarketTrade = { ts: 11, price: 1.7, qty: 1, side: "buy", tradeId: "t" };
    expect(resolveLivePrice(candle, trade, olderTicker)).toBe(1.7);
    expect(resolveLivePrice(candle, null, olderTicker)).toBe(1.5);
  });

  it("calculates cumulative quantities for depth bars", () => {
    expect(bookSideTotals([
      { price: 101, qty: 2 },
      { price: 102, qty: 3 },
    ])).toEqual([2, 5]);
  });
});
