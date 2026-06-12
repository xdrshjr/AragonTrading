import { describe, expect, it } from "vitest";
import { colorForIndex, normalizeEquity, OVERLAY_COLORS, sortCompareItems } from "./compare";
import type { CompareItem, Metrics } from "./types";

function metrics(p: Partial<Metrics>): Metrics {
  return {
    totalReturn: 0,
    winRate: 0,
    profitFactor: 0,
    maxDrawdown: 0,
    sharpe: 0,
    tradeCount: 0,
    avgPnl: 0,
    ...p,
  };
}

function item(strategyId: number, m: Partial<Metrics>): CompareItem {
  return { strategyId, name: `s${strategyId}`, runId: strategyId, metrics: metrics(m), equity: [] };
}

describe("compare utils", () => {
  it("sortCompareItems sorts by metric desc by default", () => {
    const items = [
      item(1, { totalReturn: 0.1 }),
      item(2, { totalReturn: 0.5 }),
      item(3, { totalReturn: -0.2 }),
    ];
    const sorted = sortCompareItems(items, "totalReturn");
    expect(sorted.map((i) => i.strategyId)).toEqual([2, 1, 3]);
    // 不改原数组
    expect(items.map((i) => i.strategyId)).toEqual([1, 2, 3]);
  });

  it("sortCompareItems asc direction reverses order", () => {
    const items = [item(1, { sharpe: 2 }), item(2, { sharpe: -1 }), item(3, { sharpe: 0.5 })];
    const sorted = sortCompareItems(items, "sharpe", "asc");
    expect(sorted.map((i) => i.strategyId)).toEqual([2, 3, 1]);
  });

  it("sortCompareItems on maxDrawdown ranks smaller drawdown first (desc)", () => {
    const items = [
      item(1, { maxDrawdown: -0.3 }),
      item(2, { maxDrawdown: -0.05 }),
      item(3, { maxDrawdown: -0.5 }),
    ];
    const sorted = sortCompareItems(items, "maxDrawdown");
    // -0.05 (回撤最小) 在前
    expect(sorted.map((i) => i.strategyId)).toEqual([2, 1, 3]);
  });

  it("normalizeEquity converts to return-over-initial", () => {
    const norm = normalizeEquity([
      { ts: 1, equity: 10000, drawdown: 0 },
      { ts: 2, equity: 11000, drawdown: 0 },
      { ts: 3, equity: 9000, drawdown: -0.18 },
    ]);
    expect(norm.map((p) => p.ts)).toEqual([1, 2, 3]);
    expect(norm[0].value).toBeCloseTo(0, 10);
    expect(norm[1].value).toBeCloseTo(0.1, 10);
    expect(norm[2].value).toBeCloseTo(-0.1, 10);
  });

  it("normalizeEquity dedupes ts keeping last and sorts ascending", () => {
    const norm = normalizeEquity([
      { ts: 2, equity: 10000, drawdown: 0 },
      { ts: 1, equity: 10000, drawdown: 0 },
      { ts: 2, equity: 12000, drawdown: 0 }, // 同 ts 取最后值
    ]);
    expect(norm.map((p) => p.ts)).toEqual([1, 2]);
    expect(norm[0].value).toBeCloseTo(0, 10);
    expect(norm[1].value).toBeCloseTo(0.2, 10);
  });

  it("normalizeEquity guards against zero initial (no Infinity)", () => {
    const norm = normalizeEquity([
      { ts: 1, equity: 0, drawdown: 0 },
      { ts: 2, equity: 5, drawdown: 0 },
    ]);
    expect(norm.every((p) => Number.isFinite(p.value))).toBe(true);
  });

  it("colorForIndex cycles through palette stably", () => {
    expect(colorForIndex(0)).toBe(OVERLAY_COLORS[0]);
    expect(colorForIndex(OVERLAY_COLORS.length)).toBe(OVERLAY_COLORS[0]);
    expect(colorForIndex(1)).toBe(colorForIndex(1 + OVERLAY_COLORS.length));
  });
});
