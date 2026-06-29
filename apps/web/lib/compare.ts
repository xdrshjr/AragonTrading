// 多策略对比的纯函数：榜单排序键 + 净值归一化 + 稳定配色（F3, C6），
// 以及对比清单的批量选择 / 筛选纯逻辑（默认空选 + 顶部批量工具条）。
// 与 format.ts 同约定：纯函数 + 同名 compare.test.ts 单测，组件仅消费此处逻辑。
import type { CompareItem, EquityPoint, Metrics, Strategy, StrategyKind } from "./types";

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

// ============================ 对比清单批量选择 / 筛选 ============================
// 单一真源：选择上下限常量与筛选 / 全选 / 反选纯逻辑，CompareView 与 CompareSelectionBar 共享，
// 避免组件层各自定义阈值或在 JSX 里手写分支算法（CLAUDE §六：复杂分支下沉纯函数 + 同名单测）。

// 对比清单分类筛选：全部 / 原子（含因子）/ 组合。
export type CompareFilter = "all" | "atomic" | "combo";

// 多策略对比的选择上下限（替代 CompareView 私有常量，杜绝双份定义漂移）。
export const MAX_COMPARE_SELECTIONS = 12;
export const MIN_COMPARE_SELECTIONS = 2;

// 可入选 = 非 unavailable（与对比清单 checkbox 的 disabled 口径一致）。
export function isCompareEligible(s: Strategy): boolean {
  return !s.unavailable;
}

// 当前筛选下的可见清单（保持原顺序）。语义统一为：
//   all=全集；combo=kind==="combo"；atomic=kind!=="combo"（原子 + 因子，均归「原子」标签，
//   确保因子策略不落入「无标签兜底」盲区）。
export function filterCompareStrategies(list: Strategy[], filter: CompareFilter): Strategy[] {
  if (filter === "combo") return list.filter((s) => s.kind === "combo");
  if (filter === "atomic") return list.filter((s) => s.kind !== "combo");
  return list;
}

// 全选（并集语义）：把当前筛选下可用项并入 current，不丢弃 current 中不在 visible 的已选项；
// 按稳定顺序（current 在前、visible 列表序在后）夹取至 cap，返回 id 数组。
export function selectAllEligible(
  current: Set<number>,
  visible: Strategy[],
  cap: number = MAX_COMPARE_SELECTIONS,
): number[] {
  const result: number[] = [...current];
  for (const s of visible) {
    if (result.length >= cap) break;
    if (isCompareEligible(s) && !current.has(s.id)) result.push(s.id);
  }
  return result.slice(0, cap);
}

// 反选：在当前筛选下的可用集合内反转选择（原选→去、原未选→入），可见集合外的已选项原样保留；
// 按稳定顺序（可见外已选在前、反选入的可见项在后）夹取至 cap，返回 id 数组。
export function invertSelection(
  current: Set<number>,
  visible: Strategy[],
  cap: number = MAX_COMPARE_SELECTIONS,
): number[] {
  const visibleIds = new Set(visible.map((s) => s.id));
  const kept = [...current].filter((id) => !visibleIds.has(id));
  const toggledIn = visible
    .filter((s) => isCompareEligible(s) && !current.has(s.id))
    .map((s) => s.id);
  return [...kept, ...toggledIn].slice(0, cap);
}

// 策略类型中文标签：atomic→原子 / combo→组合 / factor→因子（缺省按原子）。
export function compareKindLabel(kind?: StrategyKind): string {
  if (kind === "combo") return "组合";
  if (kind === "factor") return "因子";
  return "原子";
}
