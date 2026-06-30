import { describe, expect, it } from "vitest";
import type { Mode, Strategy, StrategyListTab } from "./types";
import {
  splitStrategyCatalog,
  visibleStrategiesForStrategyPanel,
  resolveVisibleStrategyId,
} from "./strategyCatalog";

const REGULAR_ONE: Strategy = {
  id: 1,
  templateKey: "alpha",
  name: "Alpha",
  description: "Atomic strategy",
  kind: "atomic",
  params: {},
  paramsSchema: {},
  enabled: true,
  builtin: true,
};

const REGULAR_TWO: Strategy = {
  ...REGULAR_ONE,
  id: 2,
  templateKey: "beta",
  name: "Beta",
};

const BUILTIN_COMBO: Strategy = {
  ...REGULAR_ONE,
  id: 10,
  templateKey: "regime_rotation",
  name: "Regime Rotation",
  description: "Builtin combo template",
  kind: "combo",
  builtin: true,
};

const USER_COMBO: Strategy = {
  ...BUILTIN_COMBO,
  id: 20,
  name: "User Combo",
  builtin: false,
};

const FACTOR_ONE: Strategy = {
  ...REGULAR_ONE,
  id: 30,
  templateKey: "factor_score",
  name: "Factor Score",
  description: "Factor combo score strategy",
  kind: "factor",
  params: { factorSetId: 1 },
  builtin: false,
};

const STRATEGIES = [REGULAR_ONE, REGULAR_TWO, BUILTIN_COMBO, USER_COMBO, FACTOR_ONE];

function visible(mode: Mode, strategyListTab: StrategyListTab) {
  return visibleStrategiesForStrategyPanel({
    strategies: STRATEGIES,
    mode,
    strategyListTab,
  }).map((strategy) => strategy.id);
}

describe("strategyCatalog", () => {
  it("splits atomic strategies into the regular catalog", () => {
    expect(splitStrategyCatalog(STRATEGIES).regular.map((s) => s.id)).toEqual([1, 2]);
  });

  it("shows only user-created combos in the combo catalog", () => {
    expect(splitStrategyCatalog(STRATEGIES).combos.map((s) => s.id)).toEqual([20]);
  });

  it("groups factor strategies into the factors catalog and excludes them from regular", () => {
    const catalog = splitStrategyCatalog(STRATEGIES);
    expect(catalog.factors.map((s) => s.id)).toEqual([30]);
    expect(catalog.regular.map((s) => s.id)).not.toContain(30);
  });

  it("uses factor strategies in backtest mode when factor tab is active", () => {
    expect(visible("backtest", "factor")).toEqual([30]);
  });

  it("uses factor strategies in paper mode when factor tab is active", () => {
    expect(visible("paper", "factor")).toEqual([30]);
  });

  it("never shows factor strategies on the regular tab", () => {
    expect(visible("paper", "regular")).not.toContain(30);
  });

  it("falls back to the first factor strategy when switching to factor tab", () => {
    expect(
      resolveVisibleStrategyId({
        strategies: STRATEGIES,
        currentId: 1,
        mode: "backtest",
        strategyListTab: "factor",
      }),
    ).toBe(30);
  });

  it("returns null when the factor tab has no factor strategies", () => {
    expect(
      resolveVisibleStrategyId({
        strategies: [REGULAR_ONE, USER_COMBO],
        currentId: 1,
        mode: "backtest",
        strategyListTab: "factor",
      }),
    ).toBeNull();
  });

  it("uses regular strategies in paper mode when regular tab is active", () => {
    expect(visible("paper", "regular")).toEqual([1, 2]);
  });

  it("uses user-created combos in paper mode when combo tab is active", () => {
    expect(visible("paper", "combo")).toEqual([20]);
  });

  it("preserves a current id that remains visible", () => {
    expect(
      resolveVisibleStrategyId({
        strategies: STRATEGIES,
        currentId: 2,
        mode: "backtest",
        strategyListTab: "regular",
      }),
    ).toBe(2);
  });

  it("falls back to the first saved combo when switching to combo tab", () => {
    expect(
      resolveVisibleStrategyId({
        strategies: STRATEGIES,
        currentId: 1,
        mode: "backtest",
        strategyListTab: "combo",
      }),
    ).toBe(20);
  });

  it("falls back to the first saved combo in paper mode when switching tabs", () => {
    expect(
      resolveVisibleStrategyId({
        strategies: STRATEGIES,
        currentId: 1,
        mode: "paper",
        strategyListTab: "combo",
      }),
    ).toBe(20);
  });

  it("returns null when the combo tab has no saved combos", () => {
    expect(
      resolveVisibleStrategyId({
        strategies: [REGULAR_ONE, BUILTIN_COMBO],
        currentId: 1,
        mode: "backtest",
        strategyListTab: "combo",
      }),
    ).toBeNull();
  });
});
