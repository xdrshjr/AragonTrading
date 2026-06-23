import { describe, expect, it } from "vitest";
import {
  computeCoinQty,
  computeMargin,
  computeNotional,
  maxSizeForBudget,
  sizeToSliderPct,
  sliderPctToSize,
  snapPrice,
  snapSize,
  stepDecimals,
  tpSlTriggerFromAmount,
  validateOrder,
  type OrderDraftCheck,
} from "./liveOrder";

const META = { ctVal: 0.01, lotSz: 1, minSz: 1, tickSz: 0.1, maxLever: 125 };

function check(over: Partial<OrderDraftCheck> = {}): OrderDraftCheck {
  return {
    ordType: "limit",
    px: 64000,
    sz: 2,
    lever: 5,
    minSz: META.minSz,
    maxLever: META.maxLever,
    instrumentSupported: true,
    ...over,
  };
}

describe("liveOrder — notional / margin / coin qty", () => {
  it("notional = sz * ctVal * px", () => {
    expect(computeNotional(2, 0.01, 64000)).toBeCloseTo(1280);
  });
  it("margin = notional / lever, lever<=0 falls back to notional", () => {
    expect(computeMargin(1280, 5)).toBeCloseTo(256);
    expect(computeMargin(1280, 0)).toBeCloseTo(1280);
  });
  it("coinQty = sz * ctVal", () => {
    expect(computeCoinQty(2, 0.01)).toBeCloseTo(0.02);
  });
});

describe("liveOrder — step rounding", () => {
  it("stepDecimals derives precision from step", () => {
    expect(stepDecimals(1)).toBe(0);
    expect(stepDecimals(0.1)).toBe(1);
    expect(stepDecimals(0.0001)).toBe(4);
  });
  it("snapSize floors to lotSz multiples", () => {
    expect(snapSize(2.7, 1)).toBe(2);
    expect(snapSize(0.45, 0.1)).toBeCloseTo(0.4);
  });
  it("snapPrice rounds to nearest tickSz", () => {
    expect(snapPrice(64000.06, 0.1)).toBeCloseTo(64000.1);
    expect(snapPrice(64000.04, 0.1)).toBeCloseTo(64000.0);
  });
});

describe("liveOrder — slider <-> size are inverse", () => {
  it("maxSizeForBudget caps by availUsdt * lever / (ctVal*px)", () => {
    // 1000 USDT * 5x / (0.01 * 50000) = 10 张
    expect(maxSizeForBudget(1000, 5, 0.01, 50000, 1)).toBe(10);
  });
  it("returns 0 when price / ctVal / lever invalid", () => {
    expect(maxSizeForBudget(1000, 5, 0.01, null, 1)).toBe(0);
    expect(maxSizeForBudget(1000, 0, 0.01, 50000, 1)).toBe(0);
  });
  it("pct -> size -> pct round trips on lot grid", () => {
    const maxSz = 10;
    const sz = sliderPctToSize(50, maxSz, 1);
    expect(sz).toBe(5);
    expect(sizeToSliderPct(sz, maxSz)).toBeCloseTo(50);
  });
  it("clamps slider pct to [0,100]", () => {
    expect(sliderPctToSize(150, 10, 1)).toBe(10);
    expect(sliderPctToSize(-20, 10, 1)).toBe(0);
    expect(sizeToSliderPct(5, 0)).toBe(0);
  });
});

describe("liveOrder — tp/sl trigger from amount (long/short symmetric)", () => {
  const entry = 64000;
  const sz = 2;
  const ctVal = 0.01; // coinQty = 0.02 -> delta = amount / 0.02
  it("long: tp above entry, sl below entry", () => {
    expect(tpSlTriggerFromAmount("long", "tp", entry, 20, sz, ctVal)).toBeCloseTo(64000 + 1000);
    expect(tpSlTriggerFromAmount("long", "sl", entry, 20, sz, ctVal)).toBeCloseTo(64000 - 1000);
  });
  it("short: tp below entry, sl above entry (mirror of long)", () => {
    expect(tpSlTriggerFromAmount("short", "tp", entry, 20, sz, ctVal)).toBeCloseTo(64000 - 1000);
    expect(tpSlTriggerFromAmount("short", "sl", entry, 20, sz, ctVal)).toBeCloseTo(64000 + 1000);
  });
  it("returns null on invalid inputs", () => {
    expect(tpSlTriggerFromAmount("long", "tp", 0, 20, sz, ctVal)).toBeNull();
    expect(tpSlTriggerFromAmount("long", "tp", entry, 0, sz, ctVal)).toBeNull();
    expect(tpSlTriggerFromAmount("long", "tp", entry, 20, 0, ctVal)).toBeNull();
  });
});

describe("liveOrder — validateOrder", () => {
  it("accepts a well-formed limit order", () => {
    expect(validateOrder(check()).valid).toBe(true);
  });
  it("rejects limit order without a positive price", () => {
    const r = validateOrder(check({ px: null }));
    expect(r.valid).toBe(false);
    expect(r.errors.px).toBeTruthy();
  });
  it("market order does not require px", () => {
    expect(validateOrder(check({ ordType: "market", px: null })).valid).toBe(true);
  });
  it("rejects size below minSz", () => {
    const r = validateOrder(check({ sz: 0, minSz: 1 }));
    expect(r.valid).toBe(false);
    expect(r.errors.sz).toBeTruthy();
  });
  it("rejects leverage above maxLever or below 1", () => {
    expect(validateOrder(check({ lever: 200 })).errors.lever).toBeTruthy();
    expect(validateOrder(check({ lever: 0 })).errors.lever).toBeTruthy();
  });
  it("is invalid when instrument unsupported even if fields ok", () => {
    expect(validateOrder(check({ instrumentSupported: false })).valid).toBe(false);
  });
});
