// 因子层纯展示 / 几何 helper（IC 格式化、IC 曲线 / 分位条 / 权重条几何、组合校验、过拟合提示）。
// 与 format.ts / combo.ts 同约定：纯函数 + 同名 factor.test.ts 单测，组件只消费此处逻辑。
import type {
  FactorHorizonStat,
  FactorMember,
  FrozenDirection,
  QuantileBucket,
  TrainMetrics,
} from "./factorTypes";

// 类别 key → 中文标题（与后端 FACTOR_LIBRARY.category 对齐）。
export const CATEGORY_LABELS: Record<string, string> = {
  momentum: "动量",
  trend: "趋势",
  reversal: "反转",
  volatility: "波动",
  volume: "成交量",
  liquidity: "流动性",
  mean_revert: "均值回复",
};

export function categoryLabel(category: string): string {
  return CATEGORY_LABELS[category] ?? category;
}

export function directionLabel(direction: string): string {
  return direction === "long" ? "看多" : direction === "short" ? "看空" : "自动";
}

// IC / 数值格式化：null → "—"，否则带符号三位小数。
export function formatIc(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return "—";
  return `${value >= 0 ? "+" : ""}${value.toFixed(3)}`;
}

export function formatPct(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return "—";
  return `${(value * 100).toFixed(2)}%`;
}

// 着色语义：正→long(绿)、负→short(锈)、零/空→neutral。
export function icTone(value: number | null | undefined): "long" | "short" | "neutral" {
  if (value == null || !Number.isFinite(value) || value === 0) return "neutral";
  return value > 0 ? "long" : "short";
}

export interface Pt {
  x: number;
  y: number;
}

// IC 曲线：把逐 horizon 的 rankIc 映射到 [pad, w-pad] × [pad, h-pad]，跳过 null 点（不补零）。
export function icCurvePoints(
  horizons: FactorHorizonStat[],
  width: number,
  height: number,
  pad = 6,
): Pt[] {
  const values = horizons.map((s) => s.rankIc);
  const finite = values.filter((v): v is number => v != null && Number.isFinite(v));
  if (finite.length === 0) return [];
  const domain = Math.max(0.1, ...finite.map((v) => Math.abs(v)));
  const innerW = Math.max(1, width - pad * 2);
  const innerH = Math.max(1, height - pad * 2);
  const n = Math.max(1, values.length - 1);
  const pts: Pt[] = [];
  values.forEach((v, i) => {
    if (v == null || !Number.isFinite(v)) return;
    const x = pad + (innerW * i) / n;
    const y = pad + innerH / 2 - (v / domain) * (innerH / 2);
    pts.push({ x, y });
  });
  return pts;
}

export function pointsToPath(points: Pt[]): string {
  return points.map((p, i) => `${i === 0 ? "M" : "L"}${p.x.toFixed(1)},${p.y.toFixed(1)}`).join(" ");
}

export interface BarGeom {
  q: number;
  meanRet: number;
  heightPct: number;
  positive: boolean;
  count: number;
}

// 分位收益条形几何：按各档均收益绝对值的最大值归一为高度百分比（空集回退空数组）。
export function quantileBars(buckets: QuantileBucket[]): BarGeom[] {
  if (buckets.length === 0) return [];
  const maxAbs = Math.max(1e-9, ...buckets.map((b) => Math.abs(b.meanRet)));
  return buckets.map((b) => ({
    q: b.q,
    meanRet: b.meanRet,
    heightPct: (Math.abs(b.meanRet) / maxAbs) * 100,
    positive: b.meanRet >= 0,
    count: b.count,
  }));
}

export interface WeightGeom {
  name: string;
  weight: number;
  widthPct: number;
  positive: boolean;
}

// 权重条几何：按权重绝对值最大值归一为宽度百分比（保留符号着色）。
export function weightBars(weights: Record<string, number>): WeightGeom[] {
  const entries = Object.entries(weights);
  if (entries.length === 0) return [];
  const maxAbs = Math.max(1e-9, ...entries.map(([, w]) => Math.abs(w)));
  return entries.map(([name, weight]) => ({
    name,
    weight,
    widthPct: (Math.abs(weight) / maxAbs) * 100,
    positive: weight >= 0,
  }));
}

// 过拟合提示：样本外 IC 明显低于样本内（< 50%）→ 返回红字提示，否则 null。
export function overfitWarning(metrics: TrainMetrics | null | undefined): string | null {
  if (!metrics) return null;
  const { icIn, icOut } = metrics;
  if (icIn == null || icOut == null || icIn <= 0.02) return null;
  if (icOut < icIn * 0.5) {
    return `样本外 IC（${formatIc(icOut)}）明显低于样本内（${formatIc(icIn)}），存在过拟合风险。`;
  }
  return null;
}

// 组合成员校验：至少 1 个、方向均冻结、factorKey 非空。合法返回 null，否则中文说明。
export function validateMembers(members: FactorMember[]): string | null {
  if (members.length === 0) return "至少选择 1 个因子成员";
  if (members.length > 12) return "最多 12 个因子成员";
  for (const m of members) {
    if (!m.factorKey) return "成员缺少 factorKey";
    if (m.direction !== "long" && m.direction !== "short") {
      return `成员 ${m.factorKey} 方向须冻结为看多/看空`;
    }
  }
  return null;
}

// 把评估出的方向（含 auto 已被后端定号）冻结为 long/short（保险兜底，绝不留 auto 进组合）。
export function freezeDirection(direction: string): FrozenDirection {
  return direction === "short" ? "short" : "long";
}

// 主 horizon（最小）的 RankIC，用于「我的因子」表排序 / 展示。
export function primaryRankIc(horizons: FactorHorizonStat[]): number | null {
  if (horizons.length === 0) return null;
  const sorted = [...horizons].sort((a, b) => a.h - b.h);
  return sorted[0].rankIc;
}
