import { describe, expect, it } from "vitest";
import {
  DEFAULT_BACKTEST_CONFIG,
  barToSeconds,
  estimateBars,
  presetToRange,
  refreshPresetRange,
  seedBacktestConfig,
  validateConfig,
} from "./backtestConfig";
import type { BacktestConfig } from "./types";

const NOW = 1_700_000_000;

function cfg(overrides: Partial<BacktestConfig> = {}): BacktestConfig {
  return {
    ...DEFAULT_BACKTEST_CONFIG,
    startTs: NOW - 30 * 86400,
    endTs: NOW,
    ...overrides,
  };
}

describe("presetToRange", () => {
  it("derives 7d / 30d / 1y spans ending at now", () => {
    expect(presetToRange("7d", NOW)).toEqual({ startTs: NOW - 7 * 86400, endTs: NOW });
    expect(presetToRange("30d", NOW)).toEqual({ startTs: NOW - 30 * 86400, endTs: NOW });
    expect(presetToRange("1y", NOW)).toEqual({ startTs: NOW - 365 * 86400, endTs: NOW });
  });
});

describe("estimateBars", () => {
  it("counts inclusive bars for a range", () => {
    expect(estimateBars("1H", NOW, NOW)).toBe(0); // start==end 视为非法区间，回 0
    expect(estimateBars("1H", NOW, NOW + 3600)).toBe(2);
    expect(estimateBars("1H", NOW, NOW + 3600 * 10)).toBe(11);
  });

  it("scales with the bar seconds", () => {
    expect(estimateBars("1D", NOW, NOW + 86400 * 5)).toBe(6);
    expect(estimateBars("5m", NOW, NOW + 300 * 4)).toBe(5);
  });

  it("returns 0 for start >= end", () => {
    expect(estimateBars("1H", NOW, NOW - 1)).toBe(0);
  });
});

describe("barToSeconds", () => {
  it("maps standard OKX bars", () => {
    expect(barToSeconds("1m")).toBe(60);
    expect(barToSeconds("4H")).toBe(14400);
    expect(barToSeconds("1D")).toBe(86400);
  });
});

describe("validateConfig", () => {
  it("accepts a valid config with no errors", () => {
    const v = validateConfig(cfg());
    expect(v.ok).toBe(true);
    expect(v.errors).toHaveLength(0);
  });

  it("rejects start >= end", () => {
    const v = validateConfig(cfg({ startTs: NOW, endTs: NOW }));
    expect(v.ok).toBe(false);
    expect(v.errors.join()).toMatch(/起始时间/);
  });

  it("rejects non-positive capital, negative costs and leverage < 1", () => {
    const v = validateConfig(cfg({ initialCapital: 0, feesBps: -1, slippageBps: -2, leverage: 0.5 }));
    expect(v.ok).toBe(false);
    expect(v.errors.length).toBeGreaterThanOrEqual(4);
  });

  it("warns (but stays ok) when the range is shorter than the warmup", () => {
    // 1H × 1 天区间 ≈ 25 根，暖机 260 根 → 告警但 ok。
    const v = validateConfig(cfg({ startTs: NOW - 86400, endTs: NOW }), 260);
    expect(v.ok).toBe(true);
    expect(v.warnings).toHaveLength(1);
  });
});

describe("seedBacktestConfig", () => {
  it("reuses the stored config verbatim when present", () => {
    const stored = cfg({ bar: "4H", rangePreset: "90d" });
    expect(seedBacktestConfig({ stored, bar: "1H", startTs: 1, endTs: 2 })).toBe(stored);
  });

  it("falls back to a custom range seeded from the chart bar and current range", () => {
    const seeded = seedBacktestConfig({ stored: null, bar: "15m", startTs: 100, endTs: 200 });
    expect(seeded.rangePreset).toBe("custom");
    expect(seeded.bar).toBe("15m");
    expect(seeded.startTs).toBe(100);
    expect(seeded.endTs).toBe(200);
    expect(seeded.initialCapital).toBe(DEFAULT_BACKTEST_CONFIG.initialCapital);
  });
});

describe("refreshPresetRange", () => {
  it("re-derives a preset range from now", () => {
    const refreshed = refreshPresetRange(cfg({ rangePreset: "7d", startTs: 1, endTs: 2 }), NOW);
    expect(refreshed.startTs).toBe(NOW - 7 * 86400);
    expect(refreshed.endTs).toBe(NOW);
  });

  it("leaves a custom range untouched", () => {
    const custom = cfg({ rangePreset: "custom", startTs: 111, endTs: 222 });
    expect(refreshPresetRange(custom, NOW)).toBe(custom);
  });
});
