import { describe, expect, it } from "vitest";
import { regimeAffinityLabels, regimeLabel } from "./regimeAffinity";

describe("regimeLabel", () => {
  it("maps known regime keys to Chinese labels", () => {
    expect(regimeLabel("trend_up")).toBe("趋势上行");
    expect(regimeLabel("range")).toBe("区间震荡");
  });

  it("degrades safely for an unknown key (returns the key)", () => {
    expect(regimeLabel("mystery")).toBe("mystery");
  });
});

describe("regimeAffinityLabels", () => {
  it("returns the regimes a template is hinted for, in display order", () => {
    expect(regimeAffinityLabels("trend_pullback_long")).toEqual(["趋势上行"]);
    // ema_cross_scalper 同时命中 trend_up 与 trend_down。
    expect(regimeAffinityLabels("ema_cross_scalper")).toEqual(["趋势上行", "趋势下行"]);
    expect(regimeAffinityLabels("range_scalping_strategy")).toEqual(["区间震荡", "低波动"]);
  });

  it("returns an empty list for a template with no regime affinity", () => {
    expect(regimeAffinityLabels("unknown_template")).toEqual([]);
  });
});
