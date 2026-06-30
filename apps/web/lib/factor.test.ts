import { describe, expect, it } from "vitest";
import {
  addMembersCapped,
  applyWeights,
  candidateIdentity,
  categoryLabel,
  directionLabel,
  factorRowToMember,
  filterLibraryItems,
  formatIc,
  formatPct,
  freezeDirection,
  freezeMemberDirection,
  icCurvePoints,
  icTone,
  isCandidateSelected,
  libraryItemToMember,
  memberIdentity,
  memberNames,
  overfitWarning,
  primaryRankIc,
  quantileBars,
  rangesOverlap,
  toggleMember,
  validateMembers,
  weightBars,
  weightsCoverMembers,
} from "./factor";
import type { FactorHorizonStat, FactorLibraryItem, FactorMember, TrainMetrics } from "./factorTypes";

function metrics(over: Partial<TrainMetrics>): TrainMetrics {
  return { icIn: 0.2, icOut: 0.18, r2In: null, r2Out: null, nTrain: 100, nValid: 40, ...over };
}

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
    expect(overfitWarning(metrics({ icOut: 0.05 }))).toMatch(/过拟合/);
  });
  it("no warning when out-of-sample holds", () => {
    expect(overfitWarning(metrics({ icOut: 0.18 }))).toBeNull();
    expect(overfitWarning(null)).toBeNull();
  });
  it("warns when validation sample is too small (H7)", () => {
    expect(overfitWarning(metrics({ nValid: 5 }))).toMatch(/样本量过小/);
    expect(overfitWarning(metrics({ nTrain: 10 }))).toMatch(/样本量过小/);
  });
});

describe("memberNames / applyWeights / weightsCoverMembers", () => {
  const A: FactorMember = { factorKey: "mom_roc", params: {}, weight: 0, direction: "long" };
  const B: FactorMember = { factorKey: "rev_rsi", params: {}, weight: 0, direction: "short" };

  it("names repeated factorKey with #k suffix (mirrors backend member_names)", () => {
    expect(memberNames([A, { ...A }, B])).toEqual(["mom_roc", "mom_roc#2", "rev_rsi"]);
  });
  it("bakes weights into members by name", () => {
    const baked = applyWeights([A, B], { mom_roc: 0.6, rev_rsi: 0.4 });
    expect(baked[0].weight).toBe(0.6);
    expect(baked[1].weight).toBe(0.4);
  });
  it("keeps member weight when weights null/undefined", () => {
    expect(applyWeights([{ ...A, weight: 0.9 }], null)[0].weight).toBe(0.9);
  });
  it("covers when keys exactly match member names", () => {
    expect(weightsCoverMembers([A, B], { mom_roc: 0.6, rev_rsi: 0.4 })).toBe(true);
  });
  it("not covered when a member is missing / extra / weights absent", () => {
    expect(weightsCoverMembers([A, B], { mom_roc: 1 })).toBe(false);
    expect(weightsCoverMembers([A], { mom_roc: 0.5, rev_rsi: 0.5 })).toBe(false);
    expect(weightsCoverMembers([A], null)).toBe(false);
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

const _LIBRARY: FactorLibraryItem[] = [
  { key: "mom_roc", cn: "动量", category: "momentum", directionHint: "long", paramsSchema: {}, intradayOnly: false },
  { key: "rev_rsi", cn: "RSI反转", category: "reversal", directionHint: "short", paramsSchema: {}, intradayOnly: false },
];

describe("freezeMemberDirection", () => {
  it("keeps explicit long/short", () => {
    expect(freezeMemberDirection({ factorKey: "mom_roc", direction: "short" }, _LIBRARY)).toBe("short");
    expect(freezeMemberDirection({ factorKey: "mom_roc", direction: "long" }, _LIBRARY)).toBe("long");
  });
  it("auto falls back to library directionHint", () => {
    expect(freezeMemberDirection({ factorKey: "rev_rsi", direction: "auto" }, _LIBRARY)).toBe("short");
  });
  it("auto with missing library entry falls back to long", () => {
    expect(freezeMemberDirection({ factorKey: "unknown", direction: "auto" }, _LIBRARY)).toBe("long");
  });
});

describe("memberIdentity", () => {
  it("same key+params+direction → identical key, regardless of param order", () => {
    expect(memberIdentity({ factorKey: "mom_roc", params: { a: 1, b: 2 }, direction: "long" })).toBe(
      memberIdentity({ factorKey: "mom_roc", params: { b: 2, a: 1 }, direction: "long" }),
    );
  });
  it("same key DIFFERENT params → different key (mining variants not silently dropped, P1-1)", () => {
    expect(memberIdentity({ factorKey: "mom_roc", params: { win: 10 }, direction: "long" })).not.toBe(
      memberIdentity({ factorKey: "mom_roc", params: { win: 30 }, direction: "long" }),
    );
  });
  it("different direction → different key", () => {
    expect(memberIdentity({ factorKey: "mom_roc", params: {}, direction: "long" })).not.toBe(
      memberIdentity({ factorKey: "mom_roc", params: {}, direction: "short" }),
    );
  });
});

describe("rangesOverlap", () => {
  it("adjacent ranges do not overlap (shared endpoint)", () => {
    expect(rangesOverlap(0, 10, 10, 20)).toBe(false);
    expect(rangesOverlap(10, 20, 0, 10)).toBe(false);
  });
  it("containment overlaps", () => {
    expect(rangesOverlap(0, 100, 30, 40)).toBe(true);
  });
  it("partial overlap", () => {
    expect(rangesOverlap(0, 15, 10, 20)).toBe(true);
  });
  it("disjoint ranges do not overlap", () => {
    expect(rangesOverlap(0, 5, 10, 20)).toBe(false);
  });
});

const _MROC: FactorMember = { factorKey: "mom_roc", params: { win: 10 }, weight: 0, direction: "long" };

describe("candidateIdentity / isCandidateSelected", () => {
  it("ignores direction: same key+params, different direction → same identity (R1)", () => {
    const flipped: FactorMember = { ..._MROC, direction: "short" };
    expect(candidateIdentity(_MROC)).toBe(candidateIdentity(flipped));
  });
  it("same key DIFFERENT params → different identity (mining variants distinct)", () => {
    expect(candidateIdentity(_MROC)).not.toBe(candidateIdentity({ ..._MROC, params: { win: 30 } }));
  });
  it("selected even after basket direction flip (R1)", () => {
    expect(isCandidateSelected([{ ..._MROC, direction: "short" }], _MROC)).toBe(true);
  });
  it("not selected for a different-params variant, and empty members → false", () => {
    expect(isCandidateSelected([_MROC], { ..._MROC, params: { win: 30 } })).toBe(false);
    expect(isCandidateSelected([], _MROC)).toBe(false);
  });
});

describe("toggleMember", () => {
  const a: FactorMember = { factorKey: "mom_roc", params: {}, weight: 0, direction: "long" };
  const b: FactorMember = { factorKey: "rev_rsi", params: {}, weight: 0, direction: "short" };
  it("adds when not selected and under cap", () => {
    const r = toggleMember([a], b, 12);
    expect(r.added).toBe(true);
    expect(r.capped).toBe(false);
    expect(r.next).toHaveLength(2);
  });
  it("removes when already selected, even if basket direction was flipped (R1)", () => {
    const r = toggleMember([{ ...a, direction: "short" }, b], a, 12);
    expect(r.added).toBe(false);
    expect(r.capped).toBe(false);
    expect(r.next).toEqual([b]);
  });
  it("refuses to add when full (capped), returns members unchanged", () => {
    const full = [a, b];
    const r = toggleMember(full, { factorKey: "x", params: {}, weight: 0, direction: "long" }, 2);
    expect(r.added).toBe(false);
    expect(r.capped).toBe(true);
    expect(r.next).toBe(full);
  });
});

describe("addMembersCapped", () => {
  const a: FactorMember = { factorKey: "a", params: {}, weight: 0, direction: "long" };
  const b: FactorMember = { factorKey: "b", params: {}, weight: 0, direction: "long" };
  const c: FactorMember = { factorKey: "c", params: {}, weight: 0, direction: "long" };
  it("merges new, dedupes against existing and within the batch", () => {
    const r = addMembersCapped([a], [a, b, b, c], 12);
    expect(r.next.map((m) => m.factorKey)).toEqual(["a", "b", "c"]);
    expect(r.skipped).toBe(2); // duplicate of existing a + duplicate b within batch
  });
  it("treats direction-only difference as a duplicate, not a new member (R1)", () => {
    const r = addMembersCapped([a], [{ ...a, direction: "short" }], 12);
    expect(r.next).toHaveLength(1);
    expect(r.skipped).toBe(1);
  });
  it("truncates to cap and counts the skipped overflow", () => {
    const r = addMembersCapped([], [a, b, c], 2);
    expect(r.next).toHaveLength(2);
    expect(r.skipped).toBe(1);
  });
  it("empty candidates → original members, skipped 0", () => {
    const r = addMembersCapped([a], [], 12);
    expect(r.next).toEqual([a]);
    expect(r.skipped).toBe(0);
  });
});

describe("filterLibraryItems", () => {
  it("matches cn / key case-insensitively", () => {
    expect(filterLibraryItems(_LIBRARY, "RSI", "").map((i) => i.key)).toEqual(["rev_rsi"]);
    expect(filterLibraryItems(_LIBRARY, "动量", "").map((i) => i.key)).toEqual(["mom_roc"]);
  });
  it("filters by category", () => {
    expect(filterLibraryItems(_LIBRARY, "", "reversal").map((i) => i.key)).toEqual(["rev_rsi"]);
  });
  it("empty q + empty category → original set", () => {
    expect(filterLibraryItems(_LIBRARY, "", "")).toHaveLength(_LIBRARY.length);
  });
});

describe("libraryItemToMember / factorRowToMember", () => {
  it("library item → member (default params + directionHint, weight 0)", () => {
    expect(libraryItemToMember(_LIBRARY[1])).toEqual({
      factorKey: "rev_rsi",
      params: {},
      weight: 0,
      direction: "short",
    });
  });
  it("factor row passes params, freezes auto via library hint, weight 0", () => {
    expect(
      factorRowToMember({ factorKey: "mom_roc", params: { win: 14 }, direction: "auto" }, _LIBRARY),
    ).toEqual({ factorKey: "mom_roc", params: { win: 14 }, weight: 0, direction: "long" });
  });
});
