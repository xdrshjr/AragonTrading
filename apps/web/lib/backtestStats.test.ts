import { describe, expect, it } from "vitest";
import {
  fmtDuration,
  maxDrawdownWindow,
  periodReturns,
  underwaterSeries,
} from "./backtestStats";
import type { EquityPoint } from "./types";

// 由 (ts, equity) 构造带峰值回撤的权益点（drawdown=equity/peak-1 ≤0）。
function eq(points: [number, number][]): EquityPoint[] {
  let peak = -Infinity;
  return points.map(([ts, equity]) => {
    peak = Math.max(peak, equity);
    return { ts, equity, drawdown: Math.min(equity / peak - 1, 0) };
  });
}

describe("underwaterSeries", () => {
  it("returns the stored drawdown when present", () => {
    const series = underwaterSeries(eq([[0, 100], [1, 110], [2, 99]]));
    expect(series.map((p) => p.drawdown)).toEqual([0, 0, 99 / 110 - 1]);
  });

  it("falls back to running-peak drawdown when a point lacks a finite drawdown", () => {
    const raw = [
      { ts: 0, equity: 100, drawdown: 0 },
      { ts: 1, equity: 120, drawdown: 0 },
      { ts: 2, equity: 90, drawdown: Number.NaN }, // 缺失 → 兜底 90/120-1
    ] as unknown as EquityPoint[];
    const series = underwaterSeries(raw);
    expect(series[2].drawdown).toBeCloseTo(90 / 120 - 1, 10);
  });
});

describe("periodReturns", () => {
  it("buckets by UTC day with bucket-end / bucket-start return", () => {
    const out = periodReturns(eq([[0, 100], [3600, 110], [86400, 120], [90000, 90]]), "day");
    expect(out).toHaveLength(2);
    expect(out[0].label).toBe("1970-01-01");
    expect(out[0].ret).toBeCloseTo(0.1, 10);
    expect(out[1].label).toBe("1970-01-02");
    expect(out[1].ret).toBeCloseTo(90 / 120 - 1, 10);
  });

  it("auto-densifies day→week once day buckets exceed 60", () => {
    const points = Array.from({ length: 61 }, (_, i) => [i * 86400, 100 + i] as [number, number]);
    const daily = periodReturns(eq(points), "day");
    const auto = periodReturns(eq(points));
    expect(daily).toHaveLength(61); // 显式 day：每天一桶
    expect(auto.length).toBeLessThanOrEqual(60); // 自动降密到 week（9 桶）
    expect(auto.length).toBeGreaterThan(0);
  });

  it("returns an empty array for fewer than two points", () => {
    expect(periodReturns(eq([[0, 100]]))).toEqual([]);
    expect(periodReturns([])).toEqual([]);
  });
});

describe("maxDrawdownWindow", () => {
  it("locates the peak→trough window of the deepest drawdown", () => {
    const win = maxDrawdownWindow(eq([[0, 100], [1, 110], [2, 105], [3, 120], [4, 90], [5, 130]]));
    expect(win).not.toBeNull();
    expect(win!.depth).toBeCloseTo(90 / 120 - 1, 10); // -0.25，峰值 120
    expect(win!.startTs).toBe(3); // 触发峰值
    expect(win!.endTs).toBe(4); // 最深谷底
  });

  it("returns null when the curve never draws down", () => {
    expect(maxDrawdownWindow(eq([[0, 100], [1, 110], [2, 120]]))).toBeNull();
  });
});

describe("fmtDuration", () => {
  it("renders days, hours and minutes compactly", () => {
    expect(fmtDuration(2 * 86400)).toBe("2.0 天");
    expect(fmtDuration(3 * 3600)).toBe("3 小时");
    expect(fmtDuration(120)).toBe("2 分");
    expect(fmtDuration(0)).toBe("—");
  });
});
