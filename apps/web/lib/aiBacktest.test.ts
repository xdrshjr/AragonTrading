import { describe, expect, it } from "vitest";

import type { AiTradeDecision } from "./aiTradeTypes";
import {
  actionDistribution,
  countActions,
  defaultDecisionEvery,
  describeBacktestPhase,
  planRounds,
  progressPercent,
  tradableBars,
  validateBacktestRange,
} from "./aiBacktest";

const HOUR = 3600;
const START = 1_700_000_000;

describe("tradableBars & planRounds", () => {
  it("counts inclusive bars over a range", () => {
    expect(tradableBars(START, START + 9 * HOUR, "1H")).toBe(10);
    expect(tradableBars(START, START, "1H")).toBe(0);
    expect(tradableBars(START, START - HOUR, "1H")).toBe(0);
  });
  it("plans rounds as ceil(tradableBars/every)", () => {
    expect(planRounds(START, START + 9 * HOUR, "1H", 1)).toBe(10);
    expect(planRounds(START, START + 9 * HOUR, "1H", 4)).toBe(3); // ceil(10/4)
    expect(planRounds(START, START + 9 * HOUR, "1H", 0)).toBe(10); // every<=0 → 1
  });
  it("uses the bar period to size the range", () => {
    expect(planRounds(START, START + 9 * HOUR, "15m", 1)).toBe(37); // 9h/15m=36 → 37 bars
  });
});

describe("defaultDecisionEvery (R5: default never self-rejects)", () => {
  it("bumps interval so plannedRounds never exceeds the cap", () => {
    // 720 bars, cap 400 → default = max(1, ceil(720/400)) = 2 → plannedRounds = 360 ≤ 400
    const every = defaultDecisionEvery(START, START + 719 * HOUR, "1H", 1, 400);
    expect(every).toBe(2);
    expect(planRounds(START, START + 719 * HOUR, "1H", every)).toBeLessThanOrEqual(400);
  });
  it("keeps the config default when the range is small", () => {
    expect(defaultDecisionEvery(START, START + 50 * HOUR, "1H", 1, 400)).toBe(1);
  });
});

describe("validateBacktestRange", () => {
  const now = START + 1000 * HOUR;
  it("accepts a valid past range", () => {
    expect(validateBacktestRange(START, START + 10 * HOUR, now)).toBeNull();
  });
  it("rejects reversed order", () => {
    expect(validateBacktestRange(START + 10 * HOUR, START, now)).toMatch(/早于/);
  });
  it("rejects a future start", () => {
    expect(validateBacktestRange(now + HOUR, now + 10 * HOUR, now)).toMatch(/未来/);
  });
});

describe("describeBacktestPhase", () => {
  it("localizes known phases and falls back for unknown", () => {
    expect(describeBacktestPhase("running")).toBe("AI 逐根决策");
    expect(describeBacktestPhase("loading")).toBe("加载历史行情");
    expect(describeBacktestPhase("weird")).toBe("weird");
  });
});

describe("actionDistribution & countActions", () => {
  it("normalizes counts into ordered slices with pct", () => {
    const slices = actionDistribution({ open_long: 3, open_short: 1, close: 2, hold: 4 });
    expect(slices.map((s) => s.action)).toEqual(["open_long", "open_short", "close", "hold"]);
    expect(slices[0].pct).toBeCloseTo(0.3);
    expect(slices[3].label).toBe("观望");
  });
  it("handles empty counts without div-by-zero", () => {
    expect(actionDistribution(null).every((s) => s.pct === 0 && s.count === 0)).toBe(true);
  });
  it("aggregates action counts from a decision list", () => {
    const decisions = [
      { action: "open_long" },
      { action: "hold" },
      { action: "open_long" },
    ] as AiTradeDecision[];
    expect(countActions(decisions)).toEqual({ open_long: 2, open_short: 0, close: 0, hold: 1 });
  });
});

describe("progressPercent", () => {
  it("clamps to 0..100 integers", () => {
    expect(progressPercent(0.423)).toBe(42);
    expect(progressPercent(1.5)).toBe(100);
    expect(progressPercent(-1)).toBe(0);
    expect(progressPercent(null)).toBe(0);
  });
});
