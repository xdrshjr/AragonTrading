import { describe, expect, it } from "vitest";
import { computeOhlcStats } from "./ohlc";
import type { Candle } from "./types";

function candle(partial: Partial<Candle>): Candle {
  return { ts: 1_700_000_000, o: 100, h: 110, l: 95, c: 105, vol: 1, ...partial };
}

describe("computeOhlcStats", () => {
  it("returns_nulls_when_no_candle", () => {
    expect(computeOhlcStats(null, 100)).toEqual({ change: null, changePct: null, amplitude: null });
    expect(computeOhlcStats(candle({}), null)).toEqual({
      change: null,
      changePct: null,
      amplitude: null,
    });
  });

  it("computes_change_up_down_flat", () => {
    expect(computeOhlcStats(candle({ c: 105 }), 100).change).toBeCloseTo(5);
    expect(computeOhlcStats(candle({ c: 90 }), 100).change).toBeCloseTo(-10);
    expect(computeOhlcStats(candle({ c: 100 }), 100).change).toBeCloseTo(0);
  });

  it("guards_nonpositive_prevClose", () => {
    const zero = computeOhlcStats(candle({ c: 105 }), 0);
    expect(zero.change).toBeCloseTo(105); // change 仍可计算（c - 0）
    expect(zero.changePct).toBeNull();
    expect(zero.amplitude).toBeNull();

    const neg = computeOhlcStats(candle({ c: 105 }), -5);
    expect(neg.changePct).toBeNull();
    expect(neg.amplitude).toBeNull();
  });

  it("computes_amplitude", () => {
    const stats = computeOhlcStats(candle({ h: 120, l: 100 }), 100);
    expect(stats.amplitude).toBeCloseTo(0.2); // (120-100)/100
    expect(stats.changePct).toBeCloseTo(0.05); // (105-100)/100
  });
});
