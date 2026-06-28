import { describe, expect, it } from "vitest";
import { defaultCloseSize, estimateRealizedPnl, validateCloseSize } from "./closePosition";

describe("defaultCloseSize", () => {
  it("returns the full |pos| aligned down to lotSz", () => {
    expect(defaultCloseSize(5, 1)).toBe(5);
    expect(defaultCloseSize(-5, 1)).toBe(5); // net 负 pos 取绝对值
    expect(defaultCloseSize(3.7, 1)).toBe(3); // 向下对齐 lotSz
    expect(defaultCloseSize(0.45, 0.1)).toBeCloseTo(0.4);
  });
});

describe("validateCloseSize", () => {
  it("accepts a partial size within the position", () => {
    expect(validateCloseSize(3, 5, 1, 1).valid).toBe(true);
    expect(validateCloseSize(5, 5, 1, 1).valid).toBe(true); // 全仓
  });

  it("rejects a size exceeding the position", () => {
    const r = validateCloseSize(8, 5, 1, 1);
    expect(r.valid).toBe(false);
    expect(r.error).toContain("超过");
  });

  it("rejects a size below minSz", () => {
    const r = validateCloseSize(2, 10, 5, 1);
    expect(r.valid).toBe(false);
    expect(r.error).toContain("最小下单量");
  });

  it("rejects a size that snaps to zero", () => {
    expect(validateCloseSize(0.4, 5, 1, 1).valid).toBe(false); // lotSz=1 → 取整为 0
    expect(validateCloseSize(0, 5, 1, 1).valid).toBe(false);
  });

  it("handles net negative pos via absolute value", () => {
    expect(validateCloseSize(3, -5, 1, 1).valid).toBe(true);
    expect(validateCloseSize(6, -5, 1, 1).valid).toBe(false);
  });
});

describe("estimateRealizedPnl", () => {
  it("computes long pnl as (closePx-avgPx)*coinQty", () => {
    // sz=5, ctVal=0.01 → coinQty=0.05；(65000-64000)*0.05 = 50
    expect(estimateRealizedPnl(true, 64000, 65000, 5, 0.01)).toBeCloseTo(50);
  });

  it("computes short pnl as (avgPx-closePx)*coinQty (symmetric)", () => {
    // 空：价格下跌盈利
    expect(estimateRealizedPnl(false, 64000, 63000, 5, 0.01)).toBeCloseTo(50);
    // 空：价格上涨亏损
    expect(estimateRealizedPnl(false, 64000, 65000, 5, 0.01)).toBeCloseTo(-50);
  });

  it("returns 0 on degenerate inputs", () => {
    expect(estimateRealizedPnl(true, 0, 65000, 5, 0.01)).toBe(0);
    expect(estimateRealizedPnl(true, 64000, 0, 5, 0.01)).toBe(0);
    expect(estimateRealizedPnl(true, 64000, 65000, 0, 0.01)).toBe(0);
  });
});
