import { describe, expect, it } from "vitest";
import {
  clampBudgetToCap,
  clampPct,
  deriveDefaultBudget,
  describeStopReason,
  parseOptionalLeverage,
  parseOptionalPct,
  parseOptionalUsd,
  resolveAutoTradeGate,
} from "./liveAuto";

describe("resolveAutoTradeGate", () => {
  const ready = { accountMode: "live" as const, hasReadyDefault: true, liveAutoEnabled: true, selectedCount: 1 };

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

  it("blocks when no strategy selected (selectedCount=0)", () => {
    expect(resolveAutoTradeGate({ ...ready, selectedCount: 0 })).toMatch(/选择/);
  });

  it("blocks when more than one strategy selected (selectedCount>1)", () => {
    expect(resolveAutoTradeGate({ ...ready, selectedCount: 2 })).toMatch(/单个/);
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

describe("parseOptionalPct (E4)", () => {
  it("empty string → null (use default), no error", () => {
    expect(parseOptionalPct("")).toEqual({ value: null, error: null });
    expect(parseOptionalPct("   ")).toEqual({ value: null, error: null });
  });

  it("valid positive fraction", () => {
    expect(parseOptionalPct("0.05")).toEqual({ value: 0.05, error: null });
  });

  it("rejects non-positive / non-numeric (越界不在此拦，交由 clampPct)", () => {
    expect(parseOptionalPct("0").error).toBeTruthy();
    expect(parseOptionalPct("-0.1").error).toBeTruthy();
    expect(parseOptionalPct("abc").error).toBeTruthy();
    expect(parseOptionalPct("0.9").error).toBeNull(); // 越界仍解析成功，夹紧交给 clampPct
  });
});

describe("clampPct (E4)", () => {
  it("clamps to [0, max]", () => {
    expect(clampPct(0.9, 0.2)).toBe(0.2);
    expect(clampPct(0.03, 0.2)).toBe(0.03);
    expect(clampPct(-1, 0.2)).toBe(0);
  });
});

describe("describeStopReason (hardening H7/R-6)", () => {
  it("maps every known stop reason to Chinese", () => {
    expect(describeStopReason("user_stop")).toBe("手动停止");
    expect(describeStopReason("session_stop_loss")).toBe("触发会话止损");
    expect(describeStopReason("session_take_profit")).toBe("触发会话止盈");
    expect(describeStopReason("shutdown")).toBe("服务重启");
    expect(describeStopReason("stale_feed")).toBe("行情中断保护");
    expect(describeStopReason("start_failed")).toBe("启动失败");
  });

  it("falls back to 已停止 for null / unknown values", () => {
    expect(describeStopReason(null)).toBe("已停止");
    expect(describeStopReason(undefined)).toBe("已停止");
    expect(describeStopReason("some_future_reason")).toBe("已停止");
  });
});
