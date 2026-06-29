import { describe, expect, it } from "vitest";
import {
  clampAllocationOnEdit,
  clampGroupBudgetToCap,
  defaultAllocationOnAdd,
  deriveGroupDefaultBudget,
  evenSplit,
  isAutoTradable,
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
  it("defaultAllocationOnAdd gives the remaining headroom", () => {
    expect(defaultAllocationOnAdd([])).toBe(100);
    expect(defaultAllocationOnAdd([M("A", 60)])).toBe(40);
    expect(defaultAllocationOnAdd([M("A", 60), M("B", 50)])).toBe(0); // already over → floored at 0
  });
  it("clampAllocationOnEdit clamps to [1, 100 - Σothers]", () => {
    const members = [M("A", 50), M("B", 30)];
    expect(clampAllocationOnEdit(members, "A", 90)).toBe(70); // others=30 → upper 70
    expect(clampAllocationOnEdit(members, "A", 0)).toBe(1); // lower bound 1
    expect(clampAllocationOnEdit(members, "A", Number.NaN)).toBe(1);
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
  it("deriveGroupDefaultBudget scales with member count under the 5% cap", () => {
    expect(deriveGroupDefaultBudget(1000, 3)).toBe(30); // min(1000×1%×3, 1000×5%)
    expect(deriveGroupDefaultBudget(1000, 8)).toBe(50); // capped at 5%
    expect(deriveGroupDefaultBudget(1000, 1)).toBe(10); // single == old default
  });
  it("clampGroupBudgetToCap clamps to 5% of avail", () => {
    expect(clampGroupBudgetToCap(500, 1000)).toBe(50);
    expect(clampGroupBudgetToCap(20, 1000)).toBe(20);
  });
});

describe("resolveGroupGate", () => {
  const base = {
    accountMode: "live" as const,
    hasReadyDefault: true,
    liveAutoEnabled: true,
    members: [M("BTC-USDT-SWAP", 60), M("ETH-USDT-SWAP", 40)],
    running: false,
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
  it("rejects SPOT members, zero pct, and sum over 100", () => {
    expect(resolveGroupGate({ ...base, members: [M("BTC-USDT", 100)] })).toContain("不支持");
    expect(resolveGroupGate({ ...base, members: [M("BTC-USDT-SWAP", 0)] })).toContain("0");
    expect(
      resolveGroupGate({ ...base, members: [M("BTC-USDT-SWAP", 60), M("ETH-USDT-SWAP", 60)] }),
    ).toContain("100%");
  });
});
