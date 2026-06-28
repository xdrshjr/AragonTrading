import { describe, expect, it } from "vitest";
import {
  clampBudgetToCap,
  deriveDefaultBudget,
  parseOptionalLeverage,
  parseOptionalUsd,
  resolveAutoTradeGate,
} from "./liveAuto";

describe("resolveAutoTradeGate", () => {
  const ready = { accountMode: "live" as const, hasReadyDefault: true, liveAutoEnabled: true, strategyId: 7 };

  it("returns null when all gates pass", () => {
    expect(resolveAutoTradeGate(ready)).toBeNull();
  });

  it("blocks when not in live account mode", () => {
    expect(resolveAutoTradeGate({ ...ready, accountMode: "sim" })).toMatch(/实盘/);
  });

  it("blocks when no ready default account", () => {
    expect(resolveAutoTradeGate({ ...ready, hasReadyDefault: false })).toMatch(/默认实盘账户/);
  });

  it("blocks when live-auto trading not enabled", () => {
    expect(resolveAutoTradeGate({ ...ready, liveAutoEnabled: false })).toMatch(/未启用/);
  });

  it("blocks when no strategy selected", () => {
    expect(resolveAutoTradeGate({ ...ready, strategyId: null })).toMatch(/选择/);
  });
});

describe("budget derivation", () => {
  it("derives 1% default budget from available USDT", () => {
    expect(deriveDefaultBudget(1000)).toBe(10);
  });

  it("treats negative avail as zero", () => {
    expect(deriveDefaultBudget(-50)).toBe(0);
  });

  it("clamps budget to 5% hard cap", () => {
    expect(clampBudgetToCap(500, 1000)).toBe(50);
    expect(clampBudgetToCap(30, 1000)).toBe(30); // 未超上限不变
  });
});

describe("parseOptionalUsd", () => {
  it("empty string → null (use default), no error", () => {
    expect(parseOptionalUsd("")).toEqual({ value: null, error: null });
    expect(parseOptionalUsd("   ")).toEqual({ value: null, error: null });
  });

  it("valid positive number", () => {
    expect(parseOptionalUsd("12.5")).toEqual({ value: 12.5, error: null });
  });

  it("rejects non-positive / non-numeric", () => {
    expect(parseOptionalUsd("0").error).toBeTruthy();
    expect(parseOptionalUsd("-3").error).toBeTruthy();
    expect(parseOptionalUsd("abc").error).toBeTruthy();
  });
});

describe("parseOptionalLeverage", () => {
  it("empty → null", () => {
    expect(parseOptionalLeverage("")).toEqual({ value: null, error: null });
  });

  it("accepts 1..125", () => {
    expect(parseOptionalLeverage("3")).toEqual({ value: 3, error: null });
  });

  it("rejects out of range", () => {
    expect(parseOptionalLeverage("200").error).toBeTruthy();
    expect(parseOptionalLeverage("0").error).toBeTruthy();
  });
});
