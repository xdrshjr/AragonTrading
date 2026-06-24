import { describe, expect, it } from "vitest";
import { bestWorstWindowIndex, windowBarGeometry } from "./walkForward";
import type { Metrics, WalkForwardWindow } from "./types";

function win(totalReturn: number): WalkForwardWindow {
  const metrics = { totalReturn, winRate: 0.5, profitFactor: 1, maxDrawdown: -0.05, sharpe: 1,
    tradeCount: 3, avgPnl: 1 } satisfies Metrics;
  return { startTs: 0, endTs: 1, metrics, tradeCount: 3 };
}

describe("walkForward — windowBarGeometry", () => {
  it("normalizes bar heights by max absolute value", () => {
    const bars = windowBarGeometry([0.1, -0.05, 0.05]);
    expect(bars.map((b) => b.heightPct)).toEqual([100, 50, 50]); // 归一化到最大绝对值 0.1
    expect(bars.map((b) => b.positive)).toEqual([true, false, true]);
    expect(bars.map((b) => b.index)).toEqual([0, 1, 2]);
  });

  it("returns empty array for empty input", () => {
    expect(windowBarGeometry([])).toEqual([]);
  });

  it("yields zero heights when all returns are zero (no divide-by-zero)", () => {
    const bars = windowBarGeometry([0, 0]);
    expect(bars.map((b) => b.heightPct)).toEqual([0, 0]);
    expect(bars.every((b) => b.positive)).toBe(true); // 0 >= 0 视为正
  });

  it("clamps height to 100 for the largest absolute value", () => {
    const bars = windowBarGeometry([-0.2, 0.1]);
    expect(bars[0].heightPct).toBe(100);
    expect(bars[1].heightPct).toBe(50);
  });
});

describe("walkForward — bestWorstWindowIndex", () => {
  it("locates best and worst windows by totalReturn", () => {
    const windows = [win(0.03), win(-0.08), win(0.15), win(0.01)];
    expect(bestWorstWindowIndex(windows)).toEqual({ best: 2, worst: 1 });
  });

  it("returns -1 indices for empty input", () => {
    expect(bestWorstWindowIndex([])).toEqual({ best: -1, worst: -1 });
  });

  it("picks the first window on ties", () => {
    const windows = [win(0.05), win(0.05), win(0.05)];
    expect(bestWorstWindowIndex(windows)).toEqual({ best: 0, worst: 0 });
  });
});
