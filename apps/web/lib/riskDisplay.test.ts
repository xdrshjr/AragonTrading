import { describe, expect, it } from "vitest";
import {
  formatMaxHoldBars,
  formatRiskLine,
  formatRiskPct,
  formatRiskValue,
  riskColumnLabel,
  riskInlineLabel,
} from "./riskDisplay";

describe("risk display labels", () => {
  it("returns Chinese stop-loss and take-profit labels", () => {
    expect(riskColumnLabel("stopLoss")).toBe("止损(SL)");
    expect(riskColumnLabel("takeProfit")).toBe("止盈(TP)");
    expect(riskInlineLabel("stopLoss")).toBe("止损(SL)");
    expect(riskInlineLabel("takeProfit")).toBe("止盈(TP)");
  });
});

describe("formatRiskPct", () => {
  it("formats finite decimal percentages and ignores unusable values", () => {
    expect(formatRiskPct(0.02)).toBe("2.00%");
    expect(formatRiskPct(null)).toBeNull();
    expect(formatRiskPct(undefined)).toBeNull();
    expect(formatRiskPct(Number.NaN)).toBeNull();
    expect(formatRiskPct(Number.POSITIVE_INFINITY)).toBeNull();
  });
});

describe("formatRiskValue", () => {
  it("formats available percentage and price fields without inference", () => {
    expect(formatRiskValue(0.02, 98)).toBe("2.00% @ 98.00");
    expect(formatRiskValue(null, 98)).toBe("98.00");
    expect(formatRiskValue(0.02, null)).toBe("2.00%");
    expect(formatRiskValue(null, null)).toBe("-");
    expect(formatRiskValue(Number.NaN, Number.POSITIVE_INFINITY)).toBe("-");
  });
});

describe("formatRiskLine", () => {
  it("prefixes localized labels to concrete risk values", () => {
    expect(formatRiskLine("stopLoss", 0.02, 98)).toBe("止损(SL) 2.00% @ 98.00");
    expect(formatRiskLine("takeProfit", 0.04, 104)).toBe("止盈(TP) 4.00% @ 104.00");
  });
});

describe("formatMaxHoldBars", () => {
  it("formats only finite positive max-hold values", () => {
    expect(formatMaxHoldBars(12)).toBe("最长持有 12 根K线");
    expect(formatMaxHoldBars(null)).toBeNull();
    expect(formatMaxHoldBars(undefined)).toBeNull();
    expect(formatMaxHoldBars(0)).toBeNull();
    expect(formatMaxHoldBars(-1)).toBeNull();
    expect(formatMaxHoldBars(Number.POSITIVE_INFINITY)).toBeNull();
  });
});
