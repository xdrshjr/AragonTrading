import { describe, expect, it } from "vitest";
import {
  LATEST_CANDLE_LIMIT,
  liveCandleRefreshIntervalMs,
  resolveChartRange,
  shouldFollowLive,
} from "./chartLive";

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
    expect(shouldFollowLive("run-range", "backtest", true)).toBe(false);

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
    expect(shouldFollowLive("live-follow", "backtest", true)).toBe(true);
  });

  it("keeps compare range historical and never opens a live feed for it", () => {
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
    expect(shouldFollowLive("compare-range", "compare", true)).toBe(false);
  });

  it("uses a tiny latest-candle poll window with bar-aware intervals", () => {
    expect(LATEST_CANDLE_LIMIT).toBe(3);
    expect(liveCandleRefreshIntervalMs("1m")).toBe(15_000);
    expect(liveCandleRefreshIntervalMs("5m")).toBe(30_000);
    expect(liveCandleRefreshIntervalMs("1H")).toBe(60_000);
    expect(liveCandleRefreshIntervalMs("1D")).toBe(120_000);
  });
});
