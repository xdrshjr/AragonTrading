import { describe, expect, it } from "vitest";
import {
  colorForIndex,
  compareKindLabel,
  filterCompareStrategies,
  invertSelection,
  isCompareEligible,
  MAX_COMPARE_SELECTIONS,
  normalizeEquity,
  OVERLAY_COLORS,
  selectAllEligible,
  sortCompareItems,
} from "./compare";
import type { CompareItem, Metrics, Strategy, StrategyKind } from "./types";

function metrics(p: Partial<Metrics>): Metrics {
  return {
    totalReturn: 0,
    winRate: 0,
    profitFactor: 0,
    maxDrawdown: 0,
    sharpe: 0,
    tradeCount: 0,
    avgPnl: 0,
    ...p,
  };
}

function item(strategyId: number, m: Partial<Metrics>): CompareItem {
  return { strategyId, name: `s${strategyId}`, runId: strategyId, metrics: metrics(m), equity: [] };
}

describe("compare utils", () => {
  it("sortCompareItems sorts by metric desc by default", () => {
    const items = [
      item(1, { totalReturn: 0.1 }),
      item(2, { totalReturn: 0.5 }),
      item(3, { totalReturn: -0.2 }),
    ];
    const sorted = sortCompareItems(items, "totalReturn");
    expect(sorted.map((i) => i.strategyId)).toEqual([2, 1, 3]);
    // 不改原数组
    expect(items.map((i) => i.strategyId)).toEqual([1, 2, 3]);
  });

  it("sortCompareItems asc direction reverses order", () => {
    const items = [item(1, { sharpe: 2 }), item(2, { sharpe: -1 }), item(3, { sharpe: 0.5 })];
    const sorted = sortCompareItems(items, "sharpe", "asc");
    expect(sorted.map((i) => i.strategyId)).toEqual([2, 3, 1]);
  });

  it("sortCompareItems on maxDrawdown ranks smaller drawdown first (desc)", () => {
    const items = [
      item(1, { maxDrawdown: -0.3 }),
      item(2, { maxDrawdown: -0.05 }),
      item(3, { maxDrawdown: -0.5 }),
    ];
    const sorted = sortCompareItems(items, "maxDrawdown");
    // -0.05 (回撤最小) 在前
    expect(sorted.map((i) => i.strategyId)).toEqual([2, 1, 3]);
  });

  it("normalizeEquity converts to return-over-initial", () => {
    const norm = normalizeEquity([
      { ts: 1, equity: 10000, drawdown: 0 },
      { ts: 2, equity: 11000, drawdown: 0 },
      { ts: 3, equity: 9000, drawdown: -0.18 },
    ]);
    expect(norm.map((p) => p.ts)).toEqual([1, 2, 3]);
    expect(norm[0].value).toBeCloseTo(0, 10);
    expect(norm[1].value).toBeCloseTo(0.1, 10);
    expect(norm[2].value).toBeCloseTo(-0.1, 10);
  });

  it("normalizeEquity dedupes ts keeping last and sorts ascending", () => {
    const norm = normalizeEquity([
      { ts: 2, equity: 10000, drawdown: 0 },
      { ts: 1, equity: 10000, drawdown: 0 },
      { ts: 2, equity: 12000, drawdown: 0 }, // 同 ts 取最后值
    ]);
    expect(norm.map((p) => p.ts)).toEqual([1, 2]);
    expect(norm[0].value).toBeCloseTo(0, 10);
    expect(norm[1].value).toBeCloseTo(0.2, 10);
  });

  it("normalizeEquity guards against zero initial (no Infinity)", () => {
    const norm = normalizeEquity([
      { ts: 1, equity: 0, drawdown: 0 },
      { ts: 2, equity: 5, drawdown: 0 },
    ]);
    expect(norm.every((p) => Number.isFinite(p.value))).toBe(true);
  });

  it("colorForIndex cycles through palette stably", () => {
    expect(colorForIndex(0)).toBe(OVERLAY_COLORS[0]);
    expect(colorForIndex(OVERLAY_COLORS.length)).toBe(OVERLAY_COLORS[0]);
    expect(colorForIndex(1)).toBe(colorForIndex(1 + OVERLAY_COLORS.length));
  });
});

function strat(id: number, kind: StrategyKind, unavailable = false): Strategy {
  return {
    id,
    templateKey: `t${id}`,
    name: `策略${id}`,
    description: "",
    kind,
    params: {},
    paramsSchema: {},
    enabled: true,
    builtin: true,
    unavailable,
  };
}

describe("compare selection helpers", () => {
  const list: Strategy[] = [
    strat(1, "atomic"),
    strat(2, "combo"),
    strat(3, "factor"),
    strat(4, "atomic", true), // unavailable
  ];

  it("isCompareEligible excludes unavailable strategies", () => {
    expect(isCompareEligible(strat(1, "atomic"))).toBe(true);
    expect(isCompareEligible(strat(4, "atomic", true))).toBe(false);
  });

  it("filterCompareStrategies: all is full set; combo is kind==combo; atomic includes factor", () => {
    expect(filterCompareStrategies(list, "all").map((s) => s.id)).toEqual([1, 2, 3, 4]);
    expect(filterCompareStrategies(list, "combo").map((s) => s.id)).toEqual([2]);
    // atomic = kind !== "combo" → 原子 1/4 + 因子 3，因子可在「原子」标签下被发现
    expect(filterCompareStrategies(list, "atomic").map((s) => s.id)).toEqual([1, 3, 4]);
  });

  it("selectAllEligible unions visible eligible into current and excludes unavailable", () => {
    const result = selectAllEligible(new Set([2]), list);
    // 并入可见可用项 1/3，排除 unavailable 4，保留已选的 2
    expect(new Set(result)).toEqual(new Set([1, 2, 3]));
    expect(result).not.toContain(4);
  });

  it("selectAllEligible clamps to cap by stable order keeping current first", () => {
    const visible = Array.from({ length: 20 }, (_, i) => strat(i + 1, "atomic"));
    const result = selectAllEligible(new Set([100]), visible, MAX_COMPARE_SELECTIONS);
    expect(result.length).toBe(MAX_COMPARE_SELECTIONS);
    // current 中不在 visible 的已选项（100）不被丢弃
    expect(result).toContain(100);
    expect(result[0]).toBe(100);
  });

  it("selectAllEligible does not drop current selections outside the visible filter", () => {
    // 当前已选一个组合（在「原子」筛选下不可见），点「原子」标签的全选不应丢弃它
    const atomicVisible = filterCompareStrategies(list, "atomic");
    const result = selectAllEligible(new Set([2]), atomicVisible);
    expect(result).toContain(2);
  });

  it("invertSelection inverts within visible eligible and keeps out-of-visible selections", () => {
    const visible = [strat(1, "atomic"), strat(2, "atomic"), strat(3, "atomic")];
    // current: 1（可见，反选移除）+ 9（可见外，保留）
    const result = invertSelection(new Set([1, 9]), visible);
    expect(new Set(result)).toEqual(new Set([9, 2, 3]));
    expect(result).not.toContain(1);
  });

  it("invertSelection clamps to cap", () => {
    const visible = Array.from({ length: 20 }, (_, i) => strat(i + 1, "atomic"));
    const result = invertSelection(new Set(), visible, MAX_COMPARE_SELECTIONS);
    expect(result.length).toBe(MAX_COMPARE_SELECTIONS);
  });

  it("selectAllEligible / invertSelection degrade to [] for empty or all-unavailable", () => {
    expect(selectAllEligible(new Set(), [])).toEqual([]);
    expect(selectAllEligible(new Set(), [strat(4, "atomic", true)])).toEqual([]);
    expect(invertSelection(new Set(), [])).toEqual([]);
    expect(invertSelection(new Set(), [strat(4, "atomic", true)])).toEqual([]);
  });

  it("compareKindLabel maps the three kinds", () => {
    expect(compareKindLabel("atomic")).toBe("原子");
    expect(compareKindLabel("combo")).toBe("组合");
    expect(compareKindLabel("factor")).toBe("因子");
    expect(compareKindLabel(undefined)).toBe("原子");
  });
});
