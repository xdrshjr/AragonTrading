import { describe, expect, it } from "vitest";
import {
  COST_BASIS_HINT,
  costShareOfGross,
  describeCostBasis,
  describeFeeRates,
  describeIdentityCheck,
  describeReconcile,
  formatCostBps,
  type ReconcileWire,
} from "./aiTradeCost";

function rec(overrides: Partial<ReconcileWire> = {}): ReconcileWire {
  return {
    status: "ok",
    samples: 7,
    localTrips: 7,
    final: false,
    coverageMismatch: false,
    localNetUsd: -8.42,
    diffUsd: -0.12,
    diffRatio: 0.0145,
    exchangeRealizedUsd: -8.55,
    exchangeComparableUsd: -8.3,
    exchangeFeeUsd: 21.4,
    exchangeFundingFeeUsd: 1.2,
    exchangeLiqPenaltyUsd: 0,
    identityHolds: true,
    identityMaxGapUsd: 0.000004,
    windowStartTs: 1754400000,
    ...overrides,
  };
}

describe("describeCostBasis", () => {
  it("separates a KNOWN basis from an ASSUMED one", () => {
    // 「推定」与「已知」是两件事：把它们说成同一句话，用户就无法判断这个数字该不该信（P0-3）。
    expect(describeCostBasis("net")).toBe("净额（已扣手续费）");
    expect(describeCostBasis("net", true)).toBe("净额（推定）");
    expect(describeCostBasis("gross")).toBe("毛额（未扣手续费）");
    expect(describeCostBasis(undefined)).toBe("口径未知");
  });

  it("explains why the number moved down", () => {
    expect(COST_BASIS_HINT).toContain("你本来就已经付掉的钱");
    expect(COST_BASIS_HINT).toContain("不含资金费");
  });
});

describe("formatCostBps / costShareOfGross", () => {
  it("renders bps alongside its percentage", () => {
    expect(formatCostBps(12.5)).toBe("12.5 bps（0.125%）");
    expect(formatCostBps(null)).toBe("—");
    expect(formatCostBps(Number.NaN)).toBe("—");
  });

  it("returns null instead of Infinity when the gross pnl is zero", () => {
    expect(costShareOfGross(3, 12)).toBeCloseTo(0.25);
    expect(costShareOfGross(3, -12)).toBeCloseTo(0.25);
    expect(costShareOfGross(3, 0)).toBeNull();
    expect(costShareOfGross(undefined, 12)).toBeNull();
  });
});

describe("describeReconcile", () => {
  it("returns null when the reading is missing so the card hides entirely", () => {
    // 老会话没有这段读数；显示一张全 0 的对账卡等于宣称系统验证过一件它没验证过的事。
    expect(describeReconcile(null)).toBeNull();
    expect(describeReconcile(undefined)).toBeNull();
  });

  it("never shows a difference when there is no sample", () => {
    const view = describeReconcile(rec({ status: "unavailable", diffUsd: null, samples: 0 }))!;
    expect(view.label).toBe("暂无样本");
    expect(view.detail).not.toContain("差");
  });

  it("labels an ok reading with the signed difference and its ratio", () => {
    const view = describeReconcile(rec())!;
    expect(view.label).toBe("已对上");
    expect(view.tone).toBe("ok");
    expect(view.detail).toContain("-0.12 USDT");
    expect(view.detail).toContain("1.45%");
  });

  it("names the most likely cause when coverage does not match", () => {
    const view = describeReconcile(rec({ coverageMismatch: true }))!;
    expect(view.detail).toContain("非本会话的手工持仓");
  });

  it("explains the partial (final) state instead of calling it a drift", () => {
    const view = describeReconcile(rec({ status: "partial", final: true, coverageMismatch: true }))!;
    expect(view.label).toBe("部分对账");
    expect(view.tone).toBe("muted");
    expect(view.detail).toContain("尚未在交易所侧结算");
  });

  it("marks a real drift as a warning", () => {
    const view = describeReconcile(rec({ status: "drift", diffUsd: 11.58, diffRatio: 1.4 }))!;
    expect(view.label).toBe("存在漂移");
    expect(view.tone).toBe("warn");
    expect(view.detail).toContain("+11.58 USDT");
  });
});

describe("describeIdentityCheck", () => {
  it("stays silent when the check never ran (never claims an unverified pass)", () => {
    expect(describeIdentityCheck(rec({ identityHolds: null }))).toBeNull();
    expect(describeIdentityCheck(null)).toBeNull();
  });

  it("states the failure with its magnitude", () => {
    const text = describeIdentityCheck(rec({ identityHolds: false, identityMaxGapUsd: 3 }))!;
    expect(text).toContain("未通过");
    expect(text).toContain("3");
  });
});

describe("describeFeeRates", () => {
  it("says plainly whether the rates came from the exchange or from local constants", () => {
    const exchange = describeFeeRates({
      makerBps: 1.5, takerBps: 4, level: "Lv2", ruleType: "vip", source: "exchange",
      rebate: false, ageS: 42, stale: false, makerBpsRaw: 1.5, takerBpsRaw: 4,
    })!;
    expect(exchange).toContain("交易所官方档位 Lv2");
    expect(exchange).toContain("maker 1.5 / taker 4 bps");

    const config = describeFeeRates({
      makerBps: 2, takerBps: 5, level: "", ruleType: "", source: "config",
      rebate: false, ageS: 0, stale: true, makerBpsRaw: 2, takerBpsRaw: 5,
    })!;
    expect(config).toContain("本地保守常数");
    expect(config).toContain("读数已陈旧");
  });

  it("returns null when the snapshot is absent", () => {
    expect(describeFeeRates(null)).toBeNull();
  });
});
