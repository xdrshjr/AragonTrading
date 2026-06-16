import { describe, expect, it } from "vitest";
import type { IndicatorDef, IndicatorKey } from "./indicators";
import {
  buildIndicatorLegendItems,
  type LegendItem,
} from "./indicatorLegend";

function indicatorState(
  enabled: Partial<Record<IndicatorKey, boolean>>,
): Record<IndicatorKey, boolean> {
  return {
    ma: false,
    ema: false,
    boll: false,
    macd: false,
    rsi: false,
    kdj: false,
    vol: false,
    ...enabled,
  };
}

const registry: IndicatorDef[] = [
  makeIndicator("ma", "MA Avg", "overlay", true, "(5,10)"),
  makeIndicator("ema", "EMA Avg", "overlay", false, "(12,26)"),
  makeIndicator("boll", "BOLL Bands", "overlay", true, "(20,2)"),
  makeIndicator("macd", "MACD", "pane", true, "(12,26,9)"),
  makeIndicator("rsi", "RSI Strength", "pane", false, "(6,12,24)"),
];

describe("buildIndicatorLegendItems", () => {
  it("returns_enabled_overlay_indicators_in_registry_order", () => {
    const items = buildIndicatorLegendItems({
      indicators: indicatorState({ ma: true, boll: true, ema: false }),
      activePaneKey: null,
      registry,
    });

    expect(items.map((item) => item.key)).toEqual([
      "overlay:ma",
      "overlay:boll",
    ]);
  });

  it("includes_only_active_pane_indicator", () => {
    const items = buildIndicatorLegendItems({
      indicators: indicatorState({ macd: true, rsi: true }),
      activePaneKey: "rsi",
      registry,
    });

    expect(items.map((item) => item.key)).toEqual(["pane:rsi"]);
  });

  it("omits_pane_when_active_key_null", () => {
    const items = buildIndicatorLegendItems({
      indicators: indicatorState({ macd: true, ma: true }),
      activePaneKey: null,
      registry,
    });

    expect(items.map((item) => item.key)).toEqual(["overlay:ma"]);
  });

  it("returns_empty_when_all_indicators_disabled", () => {
    expect(
      buildIndicatorLegendItems({
        indicators: indicatorState({}),
        activePaneKey: null,
        registry,
      }),
    ).toEqual([]);
  });

  it("keeps_labels_human_readable", () => {
    const [item] = buildIndicatorLegendItems({
      indicators: indicatorState({ ma: true }),
      activePaneKey: null,
      registry,
    });

    expect(item.label).toContain("MA Avg");
    expect(item.label).toContain("(5,10)");
    expect(item.label).not.toBe("ma");
  });

  it("provides_compact_labels_and_priorities", () => {
    const items = buildIndicatorLegendItems({
      indicators: indicatorState({ ma: true, rsi: true }),
      activePaneKey: "rsi",
      registry,
    });
    const byKey = indexByKey(items);

    expect(byKey["overlay:ma"].compactLabel).toBe("MA");
    expect(byKey["pane:rsi"].compactLabel).toBe("RSI");
    expect(byKey["pane:rsi"].priority).toBeGreaterThan(
      byKey["overlay:ma"].priority,
    );
  });

  it("supports_custom_registry_for_unit_tests", () => {
    const items = buildIndicatorLegendItems({
      indicators: indicatorState({ boll: true, macd: true }),
      activePaneKey: "macd",
      registry: registry.slice(2, 4),
    });

    expect(items.map((item) => item.key)).toEqual([
      "overlay:boll",
      "pane:macd",
    ]);
  });
});

function makeIndicator(
  key: IndicatorKey,
  label: string,
  category: "overlay" | "pane",
  defaultOn: boolean,
  paramsHint: string,
): IndicatorDef {
  return {
    key,
    label,
    description: label,
    category,
    defaultOn,
    paramsHint,
    compute: () => [],
  };
}

function indexByKey(items: LegendItem[]): Record<string, LegendItem> {
  return Object.fromEntries(items.map((item) => [item.key, item]));
}
