// 因子层纯展示 / 几何 helper（IC 格式化、IC 曲线 / 分位条 / 权重条几何、组合校验、过拟合提示）。
// 与 format.ts / combo.ts 同约定：纯函数 + 同名 factor.test.ts 单测，组件只消费此处逻辑。
import { DEFAULT_THRESHOLDS } from "./factorTypes";
import type {
  Factor,
  FactorEditTarget,
  FactorHorizonStat,
  FactorLibraryItem,
  FactorMember,
  FactorScoreSnapshotParams,
  FactorSet,
  FactorThresholds,
  FrozenDirection,
  QuantileBucket,
  TrainMetrics,
  TrainMethod,
  TrainProvenance,
  TrainResult,
} from "./factorTypes";

// 复合得分 / 训练默认 clip（与后端 engine.factors.scoring.DEFAULT_CLIP 同口径）：组合 model.normStats
// 缺 clip 时回落，保证从组合重建 TrainResult.normStats 始终带完整 {normWindow, clip}。
const DEFAULT_CLIP = 4;

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

// 候选身份键＝`factorKey | 规范化(params)`（**不含 direction**，评审 R1）。调色板用它做「已选回显 /
// 单击切换 / 批量去重」：同一因子在调色板里只对应一个已选态，direction 仅是加入时默认值，加入篮子后
// 可翻转而不改候选已选态。与含 direction 的后端对齐键 memberIdentity 职责不同、并存不冲突。
export function candidateIdentity(
  member: Pick<FactorMember, "factorKey"> & { params?: Record<string, unknown> },
): string {
  return `${member.factorKey}|${stableStringify(member.params ?? {})}`;
}

// 候选是否已在 members 中（按 candidateIdentity 判定，忽略 direction）。
export function isCandidateSelected(members: FactorMember[], candidate: FactorMember): boolean {
  const id = candidateIdentity(candidate);
  return members.some((m) => candidateIdentity(m) === id);
}

// 切换候选：命中 candidateIdentity 则移除所有匹配该因子的成员；否则在未满时以候选默认方向追加。
// added=发生追加；capped=因已满而拒绝追加（供调用方提示上限，绝不静默吞）。
export function toggleMember(
  members: FactorMember[],
  candidate: FactorMember,
  max: number,
): { next: FactorMember[]; added: boolean; capped: boolean } {
  const id = candidateIdentity(candidate);
  if (members.some((m) => candidateIdentity(m) === id)) {
    return { next: members.filter((m) => candidateIdentity(m) !== id), added: false, capped: false };
  }
  if (members.length >= max) return { next: members, added: false, capped: true };
  return { next: [...members, candidate], added: true, capped: false };
}

// 批量并入：按 candidateIdentity 对「候选 vs 既有」与「候选之间」双重去重并截断到 max。
// skipped=被去重或越上限丢弃的条数（绝不静默吞，供调用方提示）。
export function addMembersCapped(
  members: FactorMember[],
  candidates: FactorMember[],
  max: number,
): { next: FactorMember[]; skipped: number } {
  const next = [...members];
  const seen = new Set(next.map((m) => candidateIdentity(m)));
  let skipped = 0;
  for (const candidate of candidates) {
    const id = candidateIdentity(candidate);
    if (seen.has(id) || next.length >= max) {
      skipped += 1;
      continue;
    }
    next.push(candidate);
    seen.add(id);
  }
  return { next, skipped };
}

// 客户端过滤库目录：按中文名 / key 命中 q（大小写不敏感）+ 类别匹配（""=全部）。
export function filterLibraryItems(
  items: FactorLibraryItem[],
  q: string,
  category: string,
): FactorLibraryItem[] {
  const needle = q.trim().toLowerCase();
  return items.filter((item) => {
    if (category && item.category !== category) return false;
    if (!needle) return true;
    return item.cn.toLowerCase().includes(needle) || item.key.toLowerCase().includes(needle);
  });
}

// 库目录条目 → 组合成员（默认参数 + directionHint，weight=0）。
export function libraryItemToMember(item: FactorLibraryItem): FactorMember {
  return { factorKey: item.key, params: {}, weight: 0, direction: item.directionHint };
}

// 已存因子行 → 组合成员（自带 params + 冻结方向，weight=0；方向冻结复用 freezeMemberDirection）。
export function factorRowToMember(
  factor: Pick<Factor, "factorKey" | "params" | "direction">,
  library: FactorLibraryItem[],
): FactorMember {
  return {
    factorKey: factor.factorKey,
    params: factor.params,
    weight: 0,
    direction: freezeMemberDirection(factor, library),
  };
}

// ---- 因子组合策略：列表/详情/编辑回链的纯逻辑（与 factor.test.ts 同约定，组件只消费此处） ----

// 阈值回落：缺字段一律回落 DEFAULT_THRESHOLDS（单一口径，与后端 normalize_thresholds / 策略
// resolve_thresholds 一致），供 builder 水合与详情摘要共用。
export function resolveThresholds(raw: Partial<FactorThresholds> | null | undefined): FactorThresholds {
  return { ...DEFAULT_THRESHOLDS, ...(raw ?? {}) };
}

// 缺失指标回落零值（P2-1）：set.metrics 为 null 时仍给出完整 TrainMetrics，避免重建 TrainResult 缺字段。
function zeroTrainMetrics(): TrainMetrics {
  return { icIn: null, icOut: null, r2In: null, r2Out: null, nTrain: 0, nValid: 0 };
}

// 从已存组合的 model 重建一个「等价于刚训练完」的 TrainResult：仅当权重键集恰好覆盖成员（H3 单源真相）
// 才返回，否则 null（用户需重训才能再保存/更新）。normStats 缺字段回落 {DEFAULT_NORM_WINDOW, DEFAULT_CLIP}，
// metrics 缺失回落零值（P2-1），provenance/trainerVersion 原样回传（无重训纯改名时不重打戳，P2-1）。
export function trainedFromSet(set: FactorSet, thresholds?: FactorThresholds): TrainResult | null {
  const members = set.members ?? [];
  const weights = set.model?.weights;
  if (!weightsCoverMembers(members, weights)) return null;
  const ns = set.model?.normStats ?? {};
  const normWindow =
    typeof ns.normWindow === "number" ? ns.normWindow : thresholds?.normWindow ?? DEFAULT_THRESHOLDS.normWindow;
  const clip = typeof ns.clip === "number" ? ns.clip : DEFAULT_CLIP;
  const trainerVersion = set.model?.trainerVersion;
  return {
    method: set.method,
    weights: weights as Record<string, number>,
    normStats: { normWindow, clip },
    metrics: set.metrics ?? zeroTrainMetrics(),
    available: true,
    note: "",
    trainerVersion: typeof trainerVersion === "string" ? trainerVersion : null,
    provenance: set.model?.provenance,
  };
}

// builder 编辑态初值：把一条组合映射为 {成员, 方法, 名称, 阈值, 训练结果}（trained 为 null 时须重训）。
export interface BuilderStateFromSet {
  members: FactorMember[];
  method: TrainMethod;
  name: string;
  thresholds: FactorThresholds;
  trained: TrainResult | null;
}

export function factorSetToBuilderState(set: FactorSet): BuilderStateFromSet {
  const thresholds = resolveThresholds(set.config?.thresholds);
  return {
    members: set.members ?? [],
    method: set.method,
    name: set.name,
    thresholds,
    trained: trainedFromSet(set, thresholds),
  };
}

// 策略内联快照 params → 规范化 FactorScoreSnapshotParams（成员/阈值/归一化/溯源/来源组合 id）。
function snapshotFromParams(params: Record<string, unknown>): FactorScoreSnapshotParams {
  const members = Array.isArray(params.members) ? (params.members as FactorMember[]) : [];
  const normStats = params.normStats as { normWindow: number; clip: number } | undefined;
  const provenance = params.provenance as TrainProvenance | undefined;
  const factorSetId = typeof params.factorSetId === "number" ? params.factorSetId : undefined;
  return {
    members,
    thresholds: resolveThresholds(params.thresholds as Partial<FactorThresholds> | undefined),
    ...(normStats ? { normStats } : {}),
    ...(provenance ? { provenance } : {}),
    ...(factorSetId != null ? { factorSetId } : {}),
  };
}

// 交易面板「在因子实验室编辑」：把一条 factor_score 策略映射为编辑交接目标。
// 含 factorSetId → set 分支（**仍带 snapshot 兜底**，组合被删时 builder 回落快照，P1-1）；
// 缺省（旧快照）→ snapshot 分支，用内联快照重建 builder。
export function buildEditTargetFromStrategy(
  strategy: { id: number; name: string; params: Record<string, unknown> },
): FactorEditTarget {
  const snapshot = snapshotFromParams(strategy.params ?? {});
  if (typeof snapshot.factorSetId === "number") {
    return {
      source: "set",
      setId: snapshot.factorSetId,
      strategyId: strategy.id,
      name: strategy.name,
      snapshot,
    };
  }
  return { source: "snapshot", strategyId: strategy.id, name: strategy.name, snapshot };
}

// 从 factor_score 策略内联快照重建 TrainResult：快照成员已烘焙权重、无顶层 weights 表，故按 memberNames
// 反推权重表，使旧策略（无 factorSetId）「在因子实验室编辑」走快照分支时无需强制重训即可「另存为组合 /
// 更新该策略」（DoD #4）。method 由调用方传入（快照不含训练方法）；metrics 缺失回落零值（P2-1）。
export function trainedFromSnapshot(
  snapshot: FactorScoreSnapshotParams,
  method: TrainMethod,
): TrainResult | null {
  const members = snapshot.members ?? [];
  if (members.length === 0) return null;
  const names = memberNames(members);
  const weights: Record<string, number> = {};
  members.forEach((m, i) => {
    weights[names[i]] = typeof m.weight === "number" ? m.weight : 0;
  });
  const ns = snapshot.normStats;
  return {
    method,
    weights,
    normStats: {
      normWindow: ns?.normWindow ?? snapshot.thresholds?.normWindow ?? DEFAULT_THRESHOLDS.normWindow,
      clip: ns?.clip ?? DEFAULT_CLIP,
    },
    metrics: zeroTrainMetrics(),
    available: true,
    note: "",
    trainerVersion: null,
    provenance: snapshot.provenance,
  };
}

/**
 * 把「当前选中标的」协调到「自选标的下拉」可呈现的合法值：
 * - current 在 watchlist 内 → 原样返回（尊重用户已选）；
 * - 否则回落到 watchlist[0]（下拉首项），保证原生 select 的 value 恒命中一个 option；
 * - watchlist 为空 → 返回 ""（由 UI 渲染禁用态 + 空态提示）。
 */
export function resolveWatchlistInstId(current: string, watchlist: string[]): string {
  if (watchlist.includes(current)) return current;
  return watchlist[0] ?? "";
}

// 阈值摘要文案（详情卡片用，稳定可单测）：进多/进空/退出带/归一化窗口/压缩开关。
export function thresholdsSummary(thresholds: FactorThresholds): string {
  const squash = thresholds.squash ? " · tanh 压缩" : "";
  return (
    `进多 ${thresholds.enterLong} · 进空 ${thresholds.enterShort} · ` +
    `退出带 ${thresholds.exitBand} · 归一化窗口 ${thresholds.normWindow}${squash}`
  );
}
