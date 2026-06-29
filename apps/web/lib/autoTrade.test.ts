import { describe, expect, it } from "vitest";
import {
  clampAllocationOnEdit,
  clampGroupBudgetToCap,
  defaultAllocationOnAdd,
  deriveGroupDefaultBudget,
  evenSplit,
  groupBudgetCap,
  isAutoTradable,
  isGroupBudgetClamped,
  memberBudget,
  resolveGroupGate,
  totalAllocation,
} from "./autoTrade";
import type { AutoTradeMember } from "./liveAutoTypes";

const M = (instId: string, allocationPct: number): AutoTradeMember => ({ instId, allocationPct });

describe("isAutoTradable", () => {
  it("accepts USDT-margined swap / futures", () => {
    expect(isAutoTradable("BTC-USDT-SWAP")).toBe(true);
    expect(isAutoTradable("BTC-USDT-250328")).toBe(true);
  });
  it("rejects spot and non-USDT contracts", () => {
    expect(isAutoTradable("BTC-USDT")).toBe(false); // SPOT
    expect(isAutoTradable("BTC-USD-SWAP")).toBe(false); // coin-margined
    expect(isAutoTradable("ETH-USDC-SWAP")).toBe(false);
  });
});

describe("allocation arithmetic", () => {
  it("totalAllocation sums finite pct only", () => {
    expect(totalAllocation([M("A", 40), M("B", 30)])).toBe(70);
  });
  it("defaultAllocationOnAdd (even) gives the remaining headroom", () => {
    expect(defaultAllocationOnAdd([])).toBe(100);
    expect(defaultAllocationOnAdd([M("A", 60)], "even")).toBe(40);
    expect(defaultAllocationOnAdd([M("A", 60), M("B", 50)], "even")).toBe(0); // over → floored at 0
  });
  it("defaultAllocationOnAdd (max_ratio) uses a fixed conservative cap regardless of others", () => {
    expect(defaultAllocationOnAdd([], "max_ratio")).toBe(20);
    expect(defaultAllocationOnAdd([M("A", 90), M("B", 90)], "max_ratio")).toBe(20); // independent
  });
  it("clampAllocationOnEdit (even) clamps to [1, 100 - Σothers]", () => {
    const members = [M("A", 50), M("B", 30)];
    expect(clampAllocationOnEdit(members, "A", 90, "even")).toBe(70); // others=30 → upper 70
    expect(clampAllocationOnEdit(members, "A", 0, "even")).toBe(1); // lower bound 1
    expect(clampAllocationOnEdit(members, "A", Number.NaN, "even")).toBe(1);
  });
  it("clampAllocationOnEdit (max_ratio) clamps to [1, 100] independent of others", () => {
    const members = [M("A", 80), M("B", 70)]; // Σothers irrelevant in max_ratio
    expect(clampAllocationOnEdit(members, "A", 90, "max_ratio")).toBe(90); // not limited by B
    expect(clampAllocationOnEdit(members, "A", 150, "max_ratio")).toBe(100); // capped at 100
    expect(clampAllocationOnEdit(members, "A", 0, "max_ratio")).toBe(1); // lower bound 1
  });
  it("evenSplit floors each and first absorbs remainder to sum 100", () => {
    const out = evenSplit([M("A", 10), M("B", 10), M("C", 10)]);
    expect(out.map((m) => m.allocationPct)).toEqual([34, 33, 33]);
    expect(out.reduce((s, m) => s + m.allocationPct, 0)).toBe(100);
    expect(evenSplit([])).toEqual([]);
  });
});

describe("memberBudget / default budget", () => {
  it("memberBudget = total × pct / 100", () => {
    expect(memberBudget(1000, 25)).toBe(250);
  });
  it("deriveGroupDefaultBudget = avail × maxBudgetPct%, no member-count scaling", () => {
    expect(deriveGroupDefaultBudget(1000, 100)).toBe(1000); // default 100% → full avail
    expect(deriveGroupDefaultBudget(1000, 30)).toBe(300); // 30% of avail
    expect(deriveGroupDefaultBudget(1000, 5)).toBe(50); // arbitrary configured pct
  });
  it("clampGroupBudgetToCap clamps to maxBudgetPct% of avail", () => {
    expect(clampGroupBudgetToCap(5000, 1000, 100)).toBe(1000); // capped at 100% of 1000
    expect(clampGroupBudgetToCap(20, 1000, 100)).toBe(20); // under cap → unchanged
    expect(clampGroupBudgetToCap(500, 1000, 30)).toBe(300); // capped at 30%
  });
});

describe("budget cap / clamp detection", () => {
  it("groupBudgetCap = avail × maxBudgetPct%, and 0 when avail is null / ≤0", () => {
    expect(groupBudgetCap(1000, 100)).toBe(1000);
    expect(groupBudgetCap(1000, 30)).toBe(300);
    expect(groupBudgetCap(null, 100)).toBe(0);
    expect(groupBudgetCap(0, 100)).toBe(0);
    expect(groupBudgetCap(-100, 100)).toBe(0); // negative avail → no valid cap
  });
  it("isGroupBudgetClamped is true only for a value strictly above a positive cap", () => {
    expect(isGroupBudgetClamped(200, 1000, 100)).toBe(false); // 200 < cap 1000 → within cap
    expect(isGroupBudgetClamped(1000, 1000, 100)).toBe(false); // == cap → passes through
    expect(isGroupBudgetClamped(5000, 1000, 100)).toBe(true); // > cap 1000 → clamped, hint shown
    expect(isGroupBudgetClamped(500, 1000, 30)).toBe(true); // > cap 300 (30%) → clamped
    expect(isGroupBudgetClamped(null, 1000, 100)).toBe(false); // empty input → uses default
    expect(isGroupBudgetClamped(100, null, 100)).toBe(false); // balance loading → no cap
    expect(isGroupBudgetClamped(100, 0, 100)).toBe(false); // avail 0 → cap 0, never "clamped to 0"
  });
});

describe("resolveGroupGate", () => {
  const base = {
    accountMode: "live" as const,
    hasReadyDefault: true,
    liveAutoEnabled: true,
    members: [M("BTC-USDT-SWAP", 60), M("ETH-USDT-SWAP", 40)],
    running: false,
    mode: "even" as const,
  };
  it("passes when everything is satisfied", () => {
    expect(resolveGroupGate(base)).toBeNull();
  });
  it("is always enabled while running (acts as stop)", () => {
    expect(resolveGroupGate({ ...base, accountMode: "sim", running: true })).toBeNull();
  });
  it("blocks non-live / no account / disabled / empty", () => {
    expect(resolveGroupGate({ ...base, accountMode: "sim" })).toContain("实盘");
    expect(resolveGroupGate({ ...base, hasReadyDefault: false })).toContain("默认实盘账户");
    expect(resolveGroupGate({ ...base, liveAutoEnabled: false })).toContain("未启用");
    expect(resolveGroupGate({ ...base, members: [] })).toContain("拖入");
  });
  it("rejects SPOT members and zero pct in both modes", () => {
    expect(resolveGroupGate({ ...base, members: [M("BTC-USDT", 100)] })).toContain("不支持");
    expect(resolveGroupGate({ ...base, members: [M("BTC-USDT-SWAP", 0)] })).toContain("0");
  });
  it("rejects sum over 100 in even mode but allows it in max_ratio mode", () => {
    const over = [M("BTC-USDT-SWAP", 60), M("ETH-USDT-SWAP", 60)]; // Σ=120
    expect(resolveGroupGate({ ...base, mode: "even", members: over })).toContain("100%");
    expect(resolveGroupGate({ ...base, mode: "max_ratio", members: over })).toBeNull();
  });
});
