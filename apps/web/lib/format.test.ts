import { describe, expect, it } from "vitest";
import { fmtPct, fmtPrice, fmtTime, signed } from "./format";

describe("format utils", () => {
  it("fmtPct formats fractions as percent", () => {
    expect(fmtPct(0.1234)).toBe("12.34%");
    expect(fmtPct(undefined)).toBe("—");
  });

  it("signed prefixes positives with +", () => {
    expect(signed(1.5)).toBe("+1.50");
    expect(signed(-2)).toBe("-2.00");
  });

  it("fmtPrice scales decimals by magnitude", () => {
    expect(fmtPrice(63000)).toBe("63,000.0");
    expect(fmtPrice(0.0123)).toBe("0.0123");
  });

  it("fmtTime treats input as UTC seconds", () => {
    // 2021-01-01T00:00:00Z -> 包含日期
    const out = fmtTime(1609459200);
    expect(out).toMatch(/2020|2021/); // 本地时区可能跨日
  });
});
