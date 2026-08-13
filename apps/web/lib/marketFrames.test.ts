import { describe, expect, it, vi } from "vitest";
import { isUsableCandle, routeMarketFrame, type MarketFrameSinks } from "./marketFrames";
import type { MarketWsEvent } from "./types";

function makeSinks(overrides: Partial<MarketFrameSinks> = {}) {
  const sinks: MarketFrameSinks = {
    setError: vi.fn(),
    advanceMarketTs: vi.fn(),
    pushTrade: vi.fn(),
    pushTicker: vi.fn(),
    pushBook: vi.fn(),
    pushPrice: vi.fn(),
    lastMarketTs: () => null,
    applyPublicCandle: true,
    onConfirmedCandle: vi.fn(),
    onFormingCandle: vi.fn(),
    ...overrides,
  };
  return sinks;
}

// 后端 ws_market.py `on_stream` 的真实载荷（每次建流必发，state 依次 connecting → subscribing → live）。
const LINK_FRAME: MarketWsEvent = {
  type: "link",
  data: {
    key: "ws:business:BTC-USDT-SWAP:1H:chart",
    state: "connecting",
    attempt: 0,
    downtimeS: 0,
    detail: "connecting",
  },
};

const CANDLE: MarketWsEvent = {
  type: "candle",
  data: { ts: 1_700_000_000, o: 1, h: 2, l: 0.5, c: 1.5, vol: 10, confirm: false },
};

describe("market frame routing", () => {
  // 复现「打包桌面端开屏白屏」：link 帧曾走 else 兜底进 K 线分支，以 ts=undefined 落进图表数据，
  // 令 lightweight-charts 读 undefined.year 抛错并卸载整棵 React 树。
  it("never lets the OKX link frame reach any candle sink", () => {
    const sinks = makeSinks();
    routeMarketFrame(LINK_FRAME, sinks);

    expect(sinks.onFormingCandle).not.toHaveBeenCalled();
    expect(sinks.onConfirmedCandle).not.toHaveBeenCalled();
    expect(sinks.pushPrice).not.toHaveBeenCalled();
    expect(sinks.advanceMarketTs).not.toHaveBeenCalled();
  });

  it("does not let a link frame clear the market error banner", () => {
    const sinks = makeSinks();
    routeMarketFrame({ type: "error", data: { code: "60018", message: "channel missing" } }, sinks);
    expect(sinks.setError).toHaveBeenCalledWith("channel missing");

    // 链路状态帧到达并不代表行情在流：重连过程中的 connecting/subscribing/live 三帧
    // 都不许把用户唯一能看见的故障提示抹掉。
    for (const state of ["connecting", "subscribing", "live", "down"] as const) {
      routeMarketFrame({ ...LINK_FRAME, data: { ...LINK_FRAME.data, state } }, sinks);
    }
    expect(sinks.setError).toHaveBeenCalledTimes(1);
  });

  it("ignores an event type the backend has not modelled yet", () => {
    const sinks = makeSinks();
    const future = { type: "funding", data: { rate: 0.0001 } } as unknown as MarketWsEvent;
    routeMarketFrame(future, sinks);

    expect(sinks.setError).not.toHaveBeenCalled();
    expect(sinks.onFormingCandle).not.toHaveBeenCalled();
    expect(sinks.onConfirmedCandle).not.toHaveBeenCalled();
  });

  it("routes each data frame to its own sink and clears the error banner", () => {
    const sinks = makeSinks();
    const trade = { ts: 10, price: 1, qty: 2, side: "buy" as const, tradeId: "t1" };
    const ticker = { ts: 11, last: 1, open24h: null, vol24h: null, volCcy24h: null };
    const book = { ts: 12, bids: [], asks: [] };

    routeMarketFrame({ type: "trade", data: trade }, sinks);
    routeMarketFrame({ type: "ticker", data: ticker }, sinks);
    routeMarketFrame({ type: "book", data: book }, sinks);

    expect(sinks.pushTrade).toHaveBeenCalledWith(trade);
    expect(sinks.pushTicker).toHaveBeenCalledWith(ticker);
    expect(sinks.pushBook).toHaveBeenCalledWith(book);
    expect(sinks.advanceMarketTs).toHaveBeenCalledTimes(3);
    expect(sinks.setError).toHaveBeenCalledTimes(3);
    expect(sinks.setError).toHaveBeenLastCalledWith(null);
  });

  it("applies a forming candle, and swaps to live on confirm", () => {
    const sinks = makeSinks();
    routeMarketFrame(CANDLE, sinks);
    expect(sinks.onFormingCandle).toHaveBeenCalledWith(CANDLE.data);
    expect(sinks.onConfirmedCandle).not.toHaveBeenCalled();
    expect(sinks.pushPrice).toHaveBeenCalledWith(1.5, 1_700_000_000);

    const confirmed = { ...CANDLE.data, confirm: true };
    routeMarketFrame({ type: "candle", data: confirmed }, sinks);
    expect(sinks.onConfirmedCandle).toHaveBeenCalledWith(confirmed);
    expect(sinks.onFormingCandle).toHaveBeenLastCalledWith(null);
  });

  it("gates the price push on the market clock but still applies the candle", () => {
    const sinks = makeSinks({ lastMarketTs: () => 1_700_000_001 });
    routeMarketFrame(CANDLE, sinks);
    expect(sinks.pushPrice).not.toHaveBeenCalled();
    expect(sinks.onFormingCandle).toHaveBeenCalledWith(CANDLE.data);
  });

  it("keeps public candles off the chart when the caller opts out", () => {
    const sinks = makeSinks({ applyPublicCandle: false });
    routeMarketFrame(CANDLE, sinks);
    expect(sinks.pushPrice).toHaveBeenCalledTimes(1); // 最新价仍走，图表不动
    expect(sinks.onFormingCandle).not.toHaveBeenCalled();
    expect(sinks.onConfirmedCandle).not.toHaveBeenCalled();
  });

  it("drops a candle-typed frame whose shape cannot be charted", () => {
    const sinks = makeSinks();
    // 即便后端把它标成 candle：ts 不是有限数就会在 setData 里打崩图表库，只能拦在入口。
    for (const bad of [{ ...CANDLE.data, ts: undefined }, { ...CANDLE.data, ts: NaN }, null]) {
      routeMarketFrame({ type: "candle", data: bad } as unknown as MarketWsEvent, sinks);
    }
    expect(sinks.onFormingCandle).not.toHaveBeenCalled();
    expect(sinks.pushPrice).not.toHaveBeenCalled();
  });
});

describe("isUsableCandle", () => {
  it("requires a finite ts and close before a payload may reach the chart", () => {
    expect(isUsableCandle(CANDLE.data)).toBe(true);
    expect(isUsableCandle(LINK_FRAME.data)).toBe(false);
    expect(isUsableCandle({ ...CANDLE.data, ts: "1700000000" })).toBe(false);
    expect(isUsableCandle({ ...CANDLE.data, c: NaN })).toBe(false);
    expect(isUsableCandle(null)).toBe(false);
    expect(isUsableCandle(undefined)).toBe(false);
  });
});
