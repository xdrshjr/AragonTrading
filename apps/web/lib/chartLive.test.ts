import { describe, expect, it } from "vitest";
import {
  isCandleFrame,
  LATEST_CANDLE_LIMIT,
  liveCandleRefreshIntervalMs,
  resolveChartRange,
  shouldApplyPublicCandle,
  shouldStreamMarket,
} from "./chartLive";
import type { MarketWsEvent } from "./types";

const runRange = {
  instId: "BTC-USDT-SWAP",
  bar: "1H",
  startTs: 1_700_000_000,
  endTs: 1_700_360_000,
};

describe("chart live-follow policy", () => {
  it("keeps a backtest run range frozen unless live-follow is selected", () => {
    expect(
      resolveChartRange({
        chartView: "run-range",
        mode: "backtest",
        runRange,
        instId: "ETH-USDT-SWAP",
        bar: "1m",
        backtestStartTs: 1,
        backtestEndTs: 2,
      }),
    ).toEqual(runRange);
    expect(shouldStreamMarket("backtest", true)).toBe(true);
    expect(shouldApplyPublicCandle("run-range", "backtest", null)).toBe(false);

    expect(
      resolveChartRange({
        chartView: "live-follow",
        mode: "backtest",
        runRange,
        instId: "ETH-USDT-SWAP",
        bar: "1m",
        backtestStartTs: 1,
        backtestEndTs: 2,
      }),
    ).toBeNull();
    expect(shouldApplyPublicCandle("live-follow", "backtest", null)).toBe(true);
  });

  it("keeps compare range historical while market stream stays available", () => {
    expect(
      resolveChartRange({
        chartView: "compare-range",
        mode: "compare",
        runRange: null,
        instId: "SOL-USDT-SWAP",
        bar: "5m",
        backtestStartTs: 1_700_000_000,
        backtestEndTs: 1_700_360_000,
      }),
    ).toEqual({
      instId: "SOL-USDT-SWAP",
      bar: "5m",
      startTs: 1_700_000_000,
      endTs: 1_700_360_000,
    });
    expect(shouldStreamMarket("compare", true)).toBe(true);
    expect(shouldApplyPublicCandle("compare-range", "compare", null)).toBe(false);
  });

  it("streams market data in paper mode without applying public candles", () => {
    expect(shouldStreamMarket("paper", true)).toBe(true);
    expect(shouldApplyPublicCandle("live-follow", "paper", "sid-1")).toBe(false);
    expect(shouldStreamMarket("paper", false)).toBe(false);
  });

  it("uses a tiny latest-candle poll window with bar-aware intervals", () => {
    expect(LATEST_CANDLE_LIMIT).toBe(3);
    expect(liveCandleRefreshIntervalMs("1m")).toBe(15_000);
    expect(liveCandleRefreshIntervalMs("5m")).toBe(30_000);
    expect(liveCandleRefreshIntervalMs("1H")).toBe(60_000);
    expect(liveCandleRefreshIntervalMs("1D")).toBe(120_000);
  });
});

describe("market frame routing", () => {
  // 复现「打包桌面端开屏白屏」：后端每次建流都先发一帧 link（state=connecting）。消费端若把
  // 非 candle 帧当 K 线，它会以 ts=undefined 进入图表数据，令 lightweight-charts 抛
  // `Cannot read properties of undefined (reading 'year')`，整棵 React 树卸载成白屏。
  it("rejects the OKX link status frame that shares the market stream", () => {
    const link: MarketWsEvent = {
      type: "link",
      data: {
        key: "ws:business:BTC-USDT-SWAP:1H:chart",
        state: "connecting",
        attempt: 0,
        downtimeS: 0,
        detail: "connecting",
      },
    };
    expect(isCandleFrame(link)).toBe(false);
    // 该帧确实**没有** ts —— 这正是它一旦被当作 K 线就会打崩图表库的原因。
    expect((link.data as unknown as { ts?: number }).ts).toBeUndefined();
  });

  it("ignores any event type the backend adds later, and only accepts candles", () => {
    // 后端把新事件类型作为「加法」加进同一条流的前提就是前端未知即忽略。
    const future = { type: "funding", data: { rate: 0.0001 } } as unknown as MarketWsEvent;
    expect(isCandleFrame(future)).toBe(false);

    for (const type of ["trade", "ticker", "book", "error"] as const) {
      expect(isCandleFrame({ type, data: {} } as unknown as MarketWsEvent)).toBe(false);
    }

    const candle: MarketWsEvent = {
      type: "candle",
      data: { ts: 1_700_000_000, o: 1, h: 2, l: 0.5, c: 1.5, vol: 10, confirm: false },
    };
    expect(isCandleFrame(candle)).toBe(true);
  });
});
