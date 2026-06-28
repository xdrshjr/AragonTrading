// 因子层纯展示 / 几何 helper（IC 格式化、IC 曲线 / 分位条 / 权重条几何、组合校验、过拟合提示）。
// 与 format.ts / combo.ts 同约定：纯函数 + 同名 factor.test.ts 单测，组件只消费此处逻辑。
import type {
  Factor,
  FactorHorizonStat,
  FactorLibraryItem,
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

// 训练样本量下限（与后端 MIN_TRAIN_SAMPLES 同口径；低于即提示稳健性存疑，H7）。
export const MIN_TRAIN_SAMPLES = 30;
export const MIN_VALID_SAMPLES = 20;

// 过拟合 / 样本不足提示：样本量过小 或 样本外 IC 明显低于样本内（< 50%）→ 返回红字提示，否则 null（H7）。
export function overfitWarning(metrics: TrainMetrics | null | undefined): string | null {
  if (!metrics) return null;
  const { icIn, icOut, nTrain, nValid } = metrics;
  if ((nTrain != null && nTrain < MIN_TRAIN_SAMPLES) || (nValid != null && nValid < MIN_VALID_SAMPLES)) {
    return `训练/验证样本量过小（n_train ${nTrain ?? "—"} · n_valid ${nValid ?? "—"}），权重稳健性存疑，建议扩大区间。`;
  }
  if (icIn == null || icOut == null || icIn <= 0.02) return null;
  if (icOut < icIn * 0.5) {
    return `样本外 IC（${formatIc(icOut)}）明显低于样本内（${formatIc(icIn)}），存在过拟合风险。`;
  }
  return null;
}

// 成员稳定列名（与后端 member_names 同口径：重复 factorKey 追加 #k，训练/打分对齐权重）。
export function memberNames(members: FactorMember[]): string[] {
  const names: string[] = [];
  const seen: Record<string, number> = {};
  for (const m of members) {
    const base = m.factorKey || "factor";
    const count = seen[base] ?? 0;
    seen[base] = count + 1;
    names.push(count === 0 ? base : `${base}#${count + 1}`);
  }
  return names;
}

// 把训练权重按成员名烘焙进各成员 weight（自包含快照；无权重时原样返回）。与后端 apply_weights 同口径（H3）。
export function applyWeights(
  members: FactorMember[],
  weights: Record<string, number> | null | undefined,
): FactorMember[] {
  if (!weights) return members.map((m) => ({ ...m }));
  const names = memberNames(members);
  return members.map((m, i) => ({ ...m, weight: weights[names[i]] ?? m.weight ?? 0 }));
}

// 训练权重键集是否恰好覆盖当前成员（成员变化后权重陈旧即 false；与后端 assert_weights_cover 同口径，H3）。
export function weightsCoverMembers(
  members: FactorMember[],
  weights: Record<string, number> | null | undefined,
): boolean {
  if (!weights) return false;
  const names = memberNames(members);
  const keys = Object.keys(weights);
  if (keys.length !== names.length) return false;
  const nameSet = new Set(names);
  return keys.every((k) => nameSet.has(k));
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

// 把已存因子的方向冻结为 long/short 以选入组合：long/short 复用 freezeDirection 保持口径一致；
// auto（尚未评估定号）回落库 directionHint（合理默认），缺失再回落 "long"（绝不把 auto 带进组合，C-P1-1）。
export function freezeMemberDirection(
  factor: Pick<Factor, "factorKey" | "direction">,
  library: FactorLibraryItem[],
): FrozenDirection {
  if (factor.direction === "long" || factor.direction === "short") return freezeDirection(factor.direction);
  return library.find((f) => f.key === factor.factorKey)?.directionHint ?? "long";
}

// 稳定序列化（递归按键排序），让 memberIdentity 对同 params 不同书写顺序产出同一身份键。
function stableStringify(value: unknown): string {
  if (value === null || typeof value !== "object") return JSON.stringify(value) ?? "null";
  if (Array.isArray(value)) return `[${value.map(stableStringify).join(",")}]`;
  const obj = value as Record<string, unknown>;
  return `{${Object.keys(obj)
    .sort()
    .map((k) => `${JSON.stringify(k)}:${stableStringify(obj[k])}`)
    .join(",")}}`;
}

// 成员去重唯一身份键＝`factorKey | 规范化(params) | direction`，镜像后端 _factor_dedupe_key：含规范化 params，
// 避免按 (key,direction) 去重时静默丢弃同 key 不同参的挖掘变体（评审 P1-1）。
export function memberIdentity(
  member: Pick<FactorMember, "factorKey" | "direction"> & { params?: Record<string, unknown> },
): string {
  return `${member.factorKey}|${stableStringify(member.params ?? {})}|${member.direction}`;
}

// 两个 [start,end] 区间是否重叠（相邻端点不算重叠）；用于判定回测区间与训练区间是否重叠（样本内提示，L4）。
export function rangesOverlap(aStart: number, aEnd: number, bStart: number, bEnd: number): boolean {
  return aStart < bEnd && bStart < aEnd;
}
