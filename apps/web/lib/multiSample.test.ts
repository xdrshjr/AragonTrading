import { describe, expect, it } from "vitest";
import {
  MULTI_SAMPLE_MAX_SYMBOLS,
  dedupeInstIds,
  multiSampleTabTone,
  splitInstId,
  summarizeMultiSample,
} from "./multiSample";
import type { Metrics, MultiSampleItem, MultiSampleResult } from "./types";

function metrics(totalReturn: number): Metrics {
  return {
    totalReturn,
    winRate: 0.5,
    profitFactor: 1.2,
    maxDrawdown: -0.05,
    sharpe: 0.9,
    tradeCount: 7,
    avgPnl: 3,
  };
}

function okItem(instId: string, totalReturn: number): MultiSampleItem {
  return { instId, status: "ok", runId: 1, metrics: metrics(totalReturn) };
}

function errItem(instId: string): MultiSampleItem {
  return { instId, status: "error", error: "No confirmed candles in requested range" };
}

function result(items: MultiSampleItem[]): MultiSampleResult {
  return { strategyId: 1, bar: "1H", startTs: 1, endTs: 2, items };
}

describe("dedupeInstIds", () => {
  it("removes duplicates preserving first-seen order", () => {
    expect(dedupeInstIds(["BTC", "ETH", "BTC", "SOL", "ETH"])).toEqual(["BTC", "ETH", "SOL"]);
  });

  it("returns an empty array for empty input", () => {
    expect(dedupeInstIds([])).toEqual([]);
  });
});

describe("splitInstId", () => {
  it("splits a swap instId into base and quote", () => {
    expect(splitInstId("BTC-USDT-SWAP")).toEqual({ base: "BTC", quote: "USDT" });
  });

  it("falls back to the whole id when there is no separator", () => {
    expect(splitInstId("BTC")).toEqual({ base: "BTC", quote: "" });
  });
});

describe("summarizeMultiSample", () => {
  it("counts ok/error and picks best/worst by totalReturn", () => {
    const summary = summarizeMultiSample(
      result([okItem("BTC-USDT-SWAP", 0.2), errItem("DOGE-USDT-SWAP"), okItem("ETH-USDT-SWAP", -0.1)]),
    );
    expect(summary.okCount).toBe(2);
    expect(summary.errorCount).toBe(1);
    expect(summary.best?.instId).toBe("BTC-USDT-SWAP");
    expect(summary.worst?.instId).toBe("ETH-USDT-SWAP");
  });

  it("returns null best/worst when all symbols failed", () => {
    const summary = summarizeMultiSample(result([errItem("BTC-USDT-SWAP"), errItem("ETH-USDT-SWAP")]));
    expect(summary.okCount).toBe(0);
    expect(summary.errorCount).toBe(2);
    expect(summary.best).toBeNull();
    expect(summary.worst).toBeNull();
  });

  it("is safe for null result and empty items", () => {
    expect(summarizeMultiSample(null)).toEqual({ okCount: 0, errorCount: 0, best: null, worst: null });
    expect(summarizeMultiSample(result([]))).toEqual({
      okCount: 0,
      errorCount: 0,
      best: null,
      worst: null,
    });
  });
});

describe("multiSampleTabTone", () => {
  it("tones error items as short", () => {
    expect(multiSampleTabTone(errItem("BTC-USDT-SWAP"))).toBe("short");
  });

  it("tones ok items by the sign of totalReturn", () => {
    expect(multiSampleTabTone(okItem("BTC-USDT-SWAP", 0.2))).toBe("long");
    expect(multiSampleTabTone(okItem("ETH-USDT-SWAP", -0.2))).toBe("short");
    expect(multiSampleTabTone(okItem("SOL-USDT-SWAP", 0))).toBe("neutral");
  });
});

describe("MULTI_SAMPLE_MAX_SYMBOLS", () => {
  it("matches the backend hard cap of 20", () => {
    expect(MULTI_SAMPLE_MAX_SYMBOLS).toBe(20);
  });
});
