import { describe, expect, it } from "vitest";
import {
  MIN_ELAPSED_SEC,
  MIN_TRADES,
  annualizedConfidence,
  projectAnnualizedReturn,
} from "./annualized";

const HOUR = 3600;
const DAY = 86_400;
const YEAR_SECONDS = 365 * DAY;

describe("projectAnnualizedReturn", () => {
  it("degrades to no-elapsed when elapsedSec is non-positive (never divides by zero)", () => {
    const zero = projectAnnualizedReturn({ returnPct: 0.1, elapsedSec: 0, tradeCount: 10 });
    expect(zero.ok).toBe(false);
    expect(zero.annualizedPct).toBeNull();
    expect(zero.reason).toBe("no-elapsed");
    const negative = projectAnnualizedReturn({ returnPct: 0.1, elapsedSec: -5, tradeCount: 10 });
    expect(negative.reason).toBe("no-elapsed");
  });

  it("degrades to too-short below the minimum elapsed gate, emitting no number", () => {
    const p = projectAnnualizedReturn({
      returnPct: 0.5,
      elapsedSec: MIN_ELAPSED_SEC - 1,
      tradeCount: 99,
    });
    expect(p.ok).toBe(false);
    expect(p.annualizedPct).toBeNull();
    expect(p.reason).toBe("too-short");
  });

  it("degrades to too-few-trades below the minimum trade gate, emitting no number", () => {
    const p = projectAnnualizedReturn({
      returnPct: 0.5,
      elapsedSec: 30 * 24 * HOUR,
      tradeCount: MIN_TRADES - 1,
    });
    expect(p.ok).toBe(false);
    expect(p.annualizedPct).toBeNull();
    expect(p.reason).toBe("too-few-trades");
  });

  it("degrades to wipeout when the principal is fully lost (1+returnPct<=0)", () => {
    const p = projectAnnualizedReturn({
      returnPct: -1,
      elapsedSec: 30 * 24 * HOUR,
      tradeCount: 10,
    });
    expect(p.ok).toBe(false);
    expect(p.annualizedPct).toBeNull();
    expect(p.reason).toBe("wipeout");
  });

  it("extrapolates linearly (returnPct * YEAR / elapsed) once every gate passes", () => {
    // 10 天内 +5% → 年化 = 0.05 * (365 / 10)
    const elapsedSec = 10 * 24 * HOUR;
    const p = projectAnnualizedReturn({ returnPct: 0.05, elapsedSec, tradeCount: 8 });
    expect(p.ok).toBe(true);
    expect(p.reason).toBeUndefined();
    expect(p.annualizedPct).toBeCloseTo(0.05 * (YEAR_SECONDS / elapsedSec), 10);
    // 样本基数透传，供卡片展示
    expect(p.elapsedSec).toBe(elapsedSec);
    expect(p.tradeCount).toBe(8);
  });

  it("is linear (not compounding): a full year of elapsed returns the raw returnPct", () => {
    const p = projectAnnualizedReturn({ returnPct: 0.2, elapsedSec: YEAR_SECONDS, tradeCount: 100 });
    expect(p.ok).toBe(true);
    expect(p.annualizedPct).toBeCloseTo(0.2, 10);
  });

  it("extrapolates negative realized returns without exploding into absurd magnitudes", () => {
    const elapsedSec = 20 * 24 * HOUR;
    const p = projectAnnualizedReturn({ returnPct: -0.03, elapsedSec, tradeCount: 5 });
    expect(p.ok).toBe(true);
    expect(p.annualizedPct).toBeCloseTo(-0.03 * (YEAR_SECONDS / elapsedSec), 10);
  });

  it("honours caller-supplied gate overrides", () => {
    const p = projectAnnualizedReturn({
      returnPct: 0.01,
      elapsedSec: HOUR,
      tradeCount: 1,
      minElapsedSec: HOUR,
      minTrades: 1,
    });
    expect(p.ok).toBe(true);
    expect(p.annualizedPct).toBeCloseTo(0.01 * (YEAR_SECONDS / HOUR), 10);
  });
});

describe("annualizedConfidence (E4 样本可信度分档)", () => {
  it("rates high only when both time and trade tiers are high", () => {
    expect(annualizedConfidence(45 * DAY, 40)).toBe("high");
  });

  it("rates medium when both tiers reach at least medium", () => {
    expect(annualizedConfidence(10 * DAY, 12)).toBe("medium");
    expect(annualizedConfidence(8 * DAY, 15)).toBe("medium");
  });

  it("takes the lower tier so a short elapsed span pulls the rating down", () => {
    expect(annualizedConfidence(2 * DAY, 40)).toBe("low"); // 时长档 low 压低
  });

  it("takes the lower tier so too few trades pull the rating down", () => {
    expect(annualizedConfidence(45 * DAY, 5)).toBe("low"); // 笔数档 low 压低
  });

  it("treats the tier thresholds as inclusive boundaries", () => {
    expect(annualizedConfidence(30 * DAY, 30)).toBe("high"); // 边界 ≥30 天 且 ≥30 笔
    expect(annualizedConfidence(7 * DAY, 10)).toBe("medium"); // 边界 ≥7 天 且 ≥10 笔
  });

  it("never throws on degenerate inputs (zero / negative)", () => {
    expect(annualizedConfidence(0, 0)).toBe("low");
    expect(annualizedConfidence(-1, -1)).toBe("low");
  });
});
