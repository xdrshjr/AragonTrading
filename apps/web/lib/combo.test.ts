import { describe, expect, it } from "vitest";
import {
  buildMemberColorMap,
  clampComboPolicy,
  DEFAULT_COMBO_POLICY,
  groupBreakdownByRegime,
  MAX_MEMBERS,
  MIN_MEMBERS,
  totalTimelineBars,
  validateMemberCount,
} from "./combo";
import { OVERLAY_COLORS } from "./compare";
import type { ComboBreakdownRow, ComboPolicy } from "./types";

describe("combo draft validation", () => {
  it("rejects fewer than MIN_MEMBERS", () => {
    expect(validateMemberCount(0)).not.toBeNull();
    expect(validateMemberCount(MIN_MEMBERS - 1)).not.toBeNull();
  });

  it("rejects more than MAX_MEMBERS", () => {
    expect(validateMemberCount(MAX_MEMBERS + 1)).not.toBeNull();
  });

  it("accepts a count within [MIN, MAX]", () => {
    expect(validateMemberCount(MIN_MEMBERS)).toBeNull();
    expect(validateMemberCount(MAX_MEMBERS)).toBeNull();
  });
});

describe("clampComboPolicy", () => {
  it("clamps out-of-range numeric params to schema bounds", () => {
    const wild: ComboPolicy = {
      rebalanceBars: 9999,
      lookbackBars: 1,
      selectionMetric: "sharpe",
      minDwellBars: 0,
      switchMarginPct: 5,
      regimeGating: true,
      turnoverBps: 999,
    };
    const clamped = clampComboPolicy(wild);
    expect(clamped.rebalanceBars).toBe(240);
    expect(clamped.lookbackBars).toBe(24);
    expect(clamped.minDwellBars).toBe(1);
    expect(clamped.switchMarginPct).toBe(1);
    expect(clamped.turnoverBps).toBe(50);
  });

  it("falls back to sharpe for an invalid selection metric", () => {
    const bad = { ...DEFAULT_COMBO_POLICY, selectionMetric: "bogus" as ComboPolicy["selectionMetric"] };
    expect(clampComboPolicy(bad).selectionMetric).toBe("sharpe");
  });

  it("rounds integer params and guards non-finite input", () => {
    const policy = { ...DEFAULT_COMBO_POLICY, rebalanceBars: 12.7, turnoverBps: Number.NaN };
    const clamped = clampComboPolicy(policy);
    expect(clamped.rebalanceBars).toBe(13);
    expect(clamped.turnoverBps).toBe(5); // NaN → fallback 5
  });
});

describe("buildMemberColorMap", () => {
  it("assigns stable colors by first appearance and dedupes", () => {
    const map = buildMemberColorMap(["a", "b", "a", "c"]);
    expect(map.get("a")).toBe(OVERLAY_COLORS[0]);
    expect(map.get("b")).toBe(OVERLAY_COLORS[1]);
    expect(map.get("c")).toBe(OVERLAY_COLORS[2]);
    expect(map.size).toBe(3);
  });
});

describe("totalTimelineBars", () => {
  it("sums bars and guards against zero (no divide-by-zero)", () => {
    expect(totalTimelineBars([{ bars: 10 }, { bars: 5 }])).toBe(15);
    expect(totalTimelineBars([])).toBe(1);
    expect(totalTimelineBars([{ bars: 0 }])).toBe(1);
  });
});

describe("groupBreakdownByRegime", () => {
  it("groups rows by regime preserving first-seen order and summing bars", () => {
    const rows: ComboBreakdownRow[] = [
      { regime: "trend_up", memberKey: "a", memberName: "A", bars: 10, contribution: 0.05 },
      { regime: "range", memberKey: "b", memberName: "B", bars: 8, contribution: 0.01 },
      { regime: "trend_up", memberKey: "c", memberName: "C", bars: 4, contribution: 0.02 },
    ];
    const groups = groupBreakdownByRegime(rows);
    expect(groups.map((g) => g.regime)).toEqual(["trend_up", "range"]);
    expect(groups[0].bars).toBe(14);
    expect(groups[0].rows).toHaveLength(2);
    expect(groups[1].bars).toBe(8);
  });
});
