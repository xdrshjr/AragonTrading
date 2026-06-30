import { isUserCombo } from "./combo";
import type { Mode, Strategy, StrategyListTab } from "./types";

export const STRATEGY_LIST_TABS: { key: StrategyListTab; label: string }[] = [
  { key: "regular", label: "常规策略" },
  { key: "combo", label: "组合策略" },
  { key: "factor", label: "因子策略" },
];

// 因子组合得分策略（kind==="factor"，后端 factor_score 模板透传）：归入「因子策略」页签，
// 从「常规策略」排除，与因子实验室的「配方 ↔ 按配方做的菜」一一对应。
export function isFactorStrategy(s: Strategy): boolean {
  return s.kind === "factor";
}

export function splitStrategyCatalog(strategies: Strategy[]): {
  regular: Strategy[];
  combos: Strategy[];
  factors: Strategy[];
} {
  return {
    regular: strategies.filter((strategy) => strategy.kind !== "combo" && !isFactorStrategy(strategy)),
    combos: strategies.filter(isUserCombo),
    factors: strategies.filter(isFactorStrategy),
  };
}

export function visibleStrategiesForStrategyPanel(args: {
  strategies: Strategy[];
  mode: Mode;
  strategyListTab: StrategyListTab;
}): Strategy[] {
  const { regular, combos, factors } = splitStrategyCatalog(args.strategies);
  if (args.mode !== "backtest" && args.mode !== "paper") return regular;
  if (args.strategyListTab === "combo") return combos;
  if (args.strategyListTab === "factor") return factors;
  return regular;
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
