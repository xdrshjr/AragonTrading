import { describe, expect, it } from "vitest";
import {
  categoryLabel,
  directionLabel,
  formatIc,
  formatPct,
  freezeDirection,
  icCurvePoints,
  icTone,
  overfitWarning,
  primaryRankIc,
  quantileBars,
  validateMembers,
  weightBars,
} from "./factor";
import type { FactorHorizonStat, FactorMember } from "./factorTypes";

function horizon(h: number, rankIc: number | null): FactorHorizonStat {
  return {
    h,
    ic: rankIc,
    rankIc,
    icMean: null,
    icStd: null,
    ir: rankIc,
    tStat: null,
    monotonicity: null,
    topBottomSpread: null,
    quantiles: [],
  };
}

describe("formatIc / icTone", () => {
  it("formats with sign and 3 decimals", () => {
    expect(formatIc(0.1234)).toBe("+0.123");
    expect(formatIc(-0.2)).toBe("-0.200");
  });
  it("returns dash for null / non-finite", () => {
    expect(formatIc(null)).toBe("—");
    expect(formatIc(Number.NaN)).toBe("—");
  });
  it("tones by sign", () => {
    expect(icTone(0.1)).toBe("long");
    expect(icTone(-0.1)).toBe("short");
    expect(icTone(0)).toBe("neutral");
    expect(icTone(null)).toBe("neutral");
  });
});

describe("labels", () => {
  it("maps known category, falls back to raw", () => {
    expect(categoryLabel("momentum")).toBe("动量");
    expect(categoryLabel("unknown")).toBe("unknown");
  });
  it("direction labels", () => {
    expect(directionLabel("long")).toBe("看多");
    expect(directionLabel("short")).toBe("看空");
    expect(directionLabel("auto")).toBe("自动");
  });
  it("formatPct", () => {
    expect(formatPct(0.01)).toBe("1.00%");
    expect(formatPct(null)).toBe("—");
  });
});

describe("icCurvePoints", () => {
  it("skips null points and stays within bounds", () => {
    const pts = icCurvePoints([horizon(1, 0.2), horizon(3, null), horizon(6, -0.1)], 100, 40);
    expect(pts.length).toBe(2);
    pts.forEach((p) => {
      expect(p.x).toBeGreaterThanOrEqual(0);
      expect(p.x).toBeLessThanOrEqual(100);
      expect(p.y).toBeGreaterThanOrEqual(0);
      expect(p.y).toBeLessThanOrEqual(40);
    });
  });
  it("returns empty when all null", () => {
    expect(icCurvePoints([horizon(1, null)], 100, 40)).toEqual([]);
  });
});

describe("quantileBars / weightBars", () => {
  it("normalizes quantile heights to max abs", () => {
    const bars = quantileBars([
      { q: 0, meanRet: -0.02, count: 10 },
      { q: 1, meanRet: 0.04, count: 10 },
    ]);
    expect(bars[1].heightPct).toBe(100);
    expect(bars[0].positive).toBe(false);
    expect(bars[1].positive).toBe(true);
  });
  it("empty buckets → empty", () => {
    expect(quantileBars([])).toEqual([]);
  });
  it("normalizes weight widths and keeps sign", () => {
    const bars = weightBars({ a: 0.6, b: -0.3 });
    expect(bars[0].widthPct).toBe(100);
    expect(bars[1].widthPct).toBe(50);
    expect(bars[1].positive).toBe(false);
  });
});

describe("overfitWarning", () => {
  it("warns when out-of-sample IC collapses", () => {
    expect(
      overfitWarning({ icIn: 0.2, icOut: 0.05, r2In: null, r2Out: null, nTrain: 100, nValid: 40 }),
    ).toMatch(/过拟合/);
  });
  it("no warning when out-of-sample holds", () => {
    expect(
      overfitWarning({ icIn: 0.2, icOut: 0.18, r2In: null, r2Out: null, nTrain: 100, nValid: 40 }),
    ).toBeNull();
    expect(overfitWarning(null)).toBeNull();
  });
});

describe("validateMembers / freezeDirection / primaryRankIc", () => {
  const ok: FactorMember = { factorKey: "mom_roc", params: {}, weight: 0.5, direction: "long" };
  it("accepts frozen members", () => {
    expect(validateMembers([ok])).toBeNull();
  });
  it("rejects empty and auto direction", () => {
    expect(validateMembers([])).toMatch(/至少/);
    expect(
      validateMembers([{ ...ok, direction: "auto" as unknown as "long" }]),
    ).toMatch(/方向/);
  });
  it("freezes auto to long, short stays short", () => {
    expect(freezeDirection("auto")).toBe("long");
    expect(freezeDirection("short")).toBe("short");
  });
  it("primaryRankIc picks smallest horizon", () => {
    expect(primaryRankIc([horizon(6, 0.1), horizon(1, 0.3)])).toBe(0.3);
    expect(primaryRankIc([])).toBeNull();
  });
});
