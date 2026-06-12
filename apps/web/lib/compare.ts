// 多策略对比的纯函数：榜单排序键 + 净值归一化 + 稳定配色（F3, C6）。
// 与 format.ts 同约定：纯函数 + 同名 compare.test.ts 单测，组件仅消费此处逻辑。
import type { CompareItem, EquityPoint, Metrics } from "./types";

// 可作为榜单排序依据的指标键（均为 Metrics 的数值字段）
export type SortKey = "totalReturn" | "sharpe" | "winRate" | "maxDrawdown" | "tradeCount";
export type SortDir = "asc" | "desc";

function metricVal(m: Metrics, key: SortKey): number {
  const v = m[key];
  return typeof v === "number" && !Number.isNaN(v) ? v : 0;
}

// 按指定指标排序，返回新数组（不改原数组）。默认降序——所有指标"越大越好"，
// maxDrawdown 为负值时越大（越接近 0）回撤越小，降序同样把更优的排在前。
export function sortCompareItems(
  items: CompareItem[],
  key: SortKey,
  dir: SortDir = "desc",
): CompareItem[] {
  const mult = dir === "desc" ? -1 : 1;
  return items
    .slice()
    .sort((a, b) => mult * (metricVal(a.metrics, key) - metricVal(b.metrics, key)));
}

export type NormPoint = { ts: number; value: number };

// 把净值曲线归一化为"相对初始资金的收益率" equity/initial-1，使跨策略可叠加对比。
// 以首个净值点（≈初始资金）为基准；ts 去重升序（lightweight-charts 要求 time 严格递增）。
export function normalizeEquity(equity: EquityPoint[]): NormPoint[] {
  if (!equity.length) return [];
  const initial = equity[0]?.equity ?? 0;
  const points: NormPoint[] = equity.map((p) => ({
    ts: p.ts,
    // 退化保护：初始为 0 时退回原值，避免除零产生 Infinity
    value: initial ? p.equity / initial - 1 : p.equity,
  }));
  const byTs = new Map<number, number>();
  for (const p of points) byTs.set(p.ts, p.value);
  return Array.from(byTs, ([ts, value]) => ({ ts, value })).sort((a, b) => a.ts - b.ts);
}

// 对比曲线配色板（最多 12 色循环，与榜单色块一致）。
export const OVERLAY_COLORS = [
  "#6366f1", "#10b981", "#f59e0b", "#ef4444", "#06b6d4", "#8b5cf6",
  "#ec4899", "#84cc16", "#f97316", "#14b8a6", "#a855f7", "#eab308",
];

export function colorForIndex(i: number): string {
  return OVERLAY_COLORS[((i % OVERLAY_COLORS.length) + OVERLAY_COLORS.length) % OVERLAY_COLORS.length];
}
