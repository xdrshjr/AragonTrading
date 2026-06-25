import { isUserCombo } from "./combo";
import type { Mode, Strategy, StrategyListTab } from "./types";

export const STRATEGY_LIST_TABS: { key: StrategyListTab; label: string }[] = [
  { key: "regular", label: "常规策略" },
  { key: "combo", label: "组合策略" },
];

export function splitStrategyCatalog(strategies: Strategy[]): {
  regular: Strategy[];
  combos: Strategy[];
} {
  return {
    regular: strategies.filter((strategy) => strategy.kind !== "combo"),
    combos: strategies.filter(isUserCombo),
  };
}

export function visibleStrategiesForStrategyPanel(args: {
  strategies: Strategy[];
  mode: Mode;
  strategyListTab: StrategyListTab;
}): Strategy[] {
  const { regular, combos } = splitStrategyCatalog(args.strategies);
  if (args.mode !== "backtest" && args.mode !== "paper") return regular;
  return args.strategyListTab === "combo" ? combos : regular;
}

export function resolveVisibleStrategyId(args: {
  strategies: Strategy[];
  currentId: number | null;
  mode: Mode;
  strategyListTab: StrategyListTab;
}): number | null {
  const visible = visibleStrategiesForStrategyPanel(args);
  if (args.currentId !== null && visible.some((strategy) => strategy.id === args.currentId)) {
    return args.currentId;
  }
  return visible[0]?.id ?? null;
}
