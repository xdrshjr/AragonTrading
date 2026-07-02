// 因子组合探索训练纯助手 + 类型（factor-combo-exploration §2.7/§4.3/§5.2）：组合枚举（与后端
// weight_explore_planner.plan_combinations **同口径同去重**，C-7 平价，改一侧必同步另一侧与测试）、候选数
// 预览、矩阵装配、胜出组合回流 target 构造。纯函数 + 同名单测（weightExplore.test.ts）；组件只消费。
import { memberIdentity } from "./factor";
import type { FactorMember, FactorThresholds, TrainMethod } from "./factorTypes";
import type { WeightTrainTarget } from "./weightModelTypes";
import type { CandidateState, WeightBatchOrigin } from "./weightBatchTypes";

export type ExploreStrategy = "sizes" | "topk_prefix" | "leave_one_out" | "manual";

// 前端候选数护栏（与后端默认 max_weight_explore_candidates 对齐，factor-combo-exploration §4.1）：
// 超上限 / 不足下限即禁用启动并显式提示（服务端仍是权威，前端仅提前预览、绝不静默截断）。
export const MAX_EXPLORE_CANDIDATES = 12;
export const MIN_EXPLORE_CANDIDATES = 2;
// 探索因子池上限（与组合成员上限同口径 12）。
export const MAX_EXPLORE_POOL = 12;

// 探索策略参数（按 strategy 取用）：sizes（子集大小 / IC 排序前缀）、rankIc（IC 排序前缀）、combos（手动下标）。
export interface ExploreParams {
  sizes?: number[];
  rankIc?: number[];
  combos?: number[][];
}

// 探索计划快照单条组合的展示元数据（与后端 explore_plan_json.combos 对齐）。
export interface ExploreComboMeta {
  id: string; // 稳定组合 id（C0/C1…，= 后端 variant_label）
  label: string;
  memberKeys: string[];
}

// 探索计划快照（前端本地构造 + 后端 explorePlan 回显同构）。
export interface ExplorePlan {
  strategy: ExploreStrategy;
  params: ExploreParams;
  methods: TrainMethod[];
  poolKeys: string[];
  combos: ExploreComboMeta[];
}

// 本地组合（含完整成员，供矩阵行 + 胜出回流；id 与后端 variant_label 锁步）。
export interface LocalCombo {
  id: string;
  label: string;
  members: FactorMember[];
}

// POST /weight-models/explorations 请求体。
export interface WeightExploreStartBody {
  instId: string;
  bar: string;
  startTs: number;
  endTs: number;
  pool: FactorMember[];
  strategy: ExploreStrategy;
  params: ExploreParams;
  methods: TrainMethod[];
  config?: Record<string, unknown>;
  horizon?: number;
  factorSetId?: number;
  origin?: WeightBatchOrigin;
  maxBars?: number;
}

// 启动响应候选 roster 条目。
export interface WeightExploreCandidate {
  runId: number;
  method: TrainMethod;
  variantLabel: string;
  comboLabel: string;
  status: "queued";
}

// 启动响应（POST /weight-models/explorations）。
export interface WeightExploreStartResult {
  batchId: number;
  parentModelId: number;
  label: string;
  kind: "explore";
  origin: WeightBatchOrigin;
  explorePlan: ExplorePlan;
  candidates: WeightExploreCandidate[];
}

// 组合×方法矩阵：行=组合，列=方法，格=候选态（`${comboId}:${method}` 索引，空格为 null）。
export interface ExploreMatrixRow {
  comboId: string;
  label: string;
  memberCount: number;
}
export interface ExploreMatrix {
  rows: ExploreMatrixRow[];
  cols: TrainMethod[];
  cells: Record<string, CandidateState | null>;
}

// 胜出候选（供回流）：自有组合成员 + 方法 + WS result 产物 + run id。
export interface ExploreWinner {
  runId: number;
  method: TrainMethod;
  members: FactorMember[];
  result: CandidateState["result"];
}

const MAX_COMBO_MEMBERS = 12;

// ---- 组合枚举（镜像后端 weight_explore_planner，C-7） ----

// itertools.combinations(range(n), k) 顺序（字典序）——与后端 sizes 枚举顺序逐一对齐。
function* combinations(n: number, k: number): Generator<number[]> {
  if (k < 1 || k > n) return;
  const idx = Array.from({ length: k }, (_, i) => i);
  while (true) {
    yield idx.slice();
    let i = k - 1;
    while (i >= 0 && idx[i] === i + n - k) i -= 1;
    if (i < 0) return;
    idx[i] += 1;
    for (let j = i + 1; j < k; j += 1) idx[j] = idx[j - 1] + 1;
  }
}

// 规整 sizes：取 1..poolSize 的整数、去重、保输入顺序；越界 / 非整数一律跳过（镜像后端 _valid_sizes）。
function validSizes(sizes: number[] | undefined, poolSize: number): number[] {
  if (!Array.isArray(sizes)) return [];
  const out: number[] = [];
  const seen = new Set<number>();
  for (const v of sizes) {
    if (!Number.isInteger(v) || v < 1 || v > poolSize || seen.has(v)) continue;
    seen.add(v);
    out.push(v);
  }
  return out;
}

// 按 rankIc 降序排 pool 下标（tie 用 pool 序）；缺失 / 长度不匹配回落 pool 顺序（镜像后端 _rank_order）。
function rankOrder(poolSize: number, rankIc: number[] | undefined): number[] {
  const base = Array.from({ length: poolSize }, (_, i) => i);
  if (!Array.isArray(rankIc) || rankIc.length !== poolSize) return base;
  const scores = rankIc.map((v) => (typeof v === "number" && Number.isFinite(v) ? v : -Infinity));
  return base.sort((a, b) => scores[b] - scores[a] || a - b);
}

// manual：显式下标数组；下标越界 / 空组合 → 抛错；组合内下标去重保序（镜像后端 _manual_index_combos）。
function manualIndexCombos(poolSize: number, raw: number[][] | undefined): number[][] {
  if (!Array.isArray(raw) || raw.length === 0) throw new Error("手动策略需要非空的 combos 下标数组");
  return raw.map((entry) => {
    if (!Array.isArray(entry) || entry.length === 0) throw new Error("手动组合不可为空");
    const idxs: number[] = [];
    const seen = new Set<number>();
    for (const i of entry) {
      if (!Number.isInteger(i) || i < 0 || i >= poolSize) throw new Error(`组合下标越界：${i}`);
      if (!seen.has(i)) {
        seen.add(i);
        idxs.push(i);
      }
    }
    return idxs;
  });
}

// 据策略生成组合的 pool 下标列表（未去重、未过滤成员数）。
function indexCombos(poolSize: number, strategy: ExploreStrategy, params: ExploreParams): number[][] {
  if (strategy === "sizes") {
    const out: number[][] = [];
    for (const k of validSizes(params.sizes, poolSize)) for (const c of combinations(poolSize, k)) out.push(c);
    return out;
  }
  if (strategy === "topk_prefix") {
    const order = rankOrder(poolSize, params.rankIc);
    return validSizes(params.sizes, poolSize).map((k) => order.slice(0, k));
  }
  if (strategy === "leave_one_out") {
    const full = Array.from({ length: poolSize }, (_, i) => i);
    const out: number[][] = [full];
    if (poolSize >= 2) for (let i = 0; i < poolSize; i += 1) out.push(full.filter((j) => j !== i));
    return out;
  }
  return manualIndexCombos(poolSize, params.combos);
}

// 据策略从因子池生成去重后的组合列表（成员快照子集；与后端 plan_combinations 同口径同去重，C-7）。
export function planCombos(pool: FactorMember[], strategy: ExploreStrategy, params: ExploreParams): FactorMember[][] {
  const seen = new Set<string>();
  const combos: FactorMember[][] = [];
  for (const indices of indexCombos(pool.length, strategy, params)) {
    if (indices.length === 0) continue; // 自动策略空组合跳过（manual 空组合已在上游抛错）
    if (indices.length > MAX_COMBO_MEMBERS) throw new Error(`组合成员数须在 1..${MAX_COMBO_MEMBERS} 之间`);
    const key = indices.map((i) => memberIdentity(pool[i])).sort().join("||");
    if (seen.has(key)) continue;
    seen.add(key);
    combos.push(indices.map((i) => pool[i]));
  }
  return combos;
}

// 本地组合（含 id/展示名/成员），供矩阵行与胜出回流；label 由 labelOf 映射成员名（缺省用 factorKey）。
export function planLocalCombos(
  pool: FactorMember[],
  strategy: ExploreStrategy,
  params: ExploreParams,
  labelOf: (member: FactorMember) => string = (m) => m.factorKey,
): LocalCombo[] {
  return planCombos(pool, strategy, params).map((members, i) => ({
    id: `C${i}`,
    label: members.map(labelOf).join(" · "),
    members,
  }));
}

// 候选数预览（组合数，未乘方法数；与后端 planner 组合数同口径，C-7）。非法计划（manual 越界）向上抛错，由 UI 处置。
export function estimateComboCount(pool: FactorMember[], strategy: ExploreStrategy, params: ExploreParams): number {
  return planCombos(pool, strategy, params).length;
}

// 候选总数 = 组合数 × 方法数（预览「M 组合 × N 方法 = K 候选」）。
export function estimateCandidateCount(
  pool: FactorMember[],
  strategy: ExploreStrategy,
  params: ExploreParams,
  methods: TrainMethod[],
): number {
  return estimateComboCount(pool, strategy, params) * methods.length;
}

// 探索请求体构造：config 携 normWindow（训练用）+ thresholds（父模型回测用，与批次同口径）。
export function buildExploreBody(input: {
  pool: FactorMember[];
  strategy: ExploreStrategy;
  params: ExploreParams;
  methods: TrainMethod[];
  thresholds: FactorThresholds;
  horizon: number;
  instId: string;
  bar: string;
  startTs: number;
  endTs: number;
  factorSetId?: number;
  origin?: WeightBatchOrigin;
}): WeightExploreStartBody {
  return {
    instId: input.instId,
    bar: input.bar,
    startTs: input.startTs,
    endTs: input.endTs,
    pool: input.pool,
    strategy: input.strategy,
    params: input.params,
    methods: input.methods,
    config: { normWindow: input.thresholds.normWindow, thresholds: input.thresholds },
    horizon: input.horizon,
    origin: input.origin ?? "builder",
    ...(input.factorSetId != null ? { factorSetId: input.factorSetId } : {}),
  };
}

// 组合×方法矩阵装配：按 (comboId, method) 二维索引候选态；空格为 null。plan 可为本地计划（cn 展示名）或后端回显。
export function assembleMatrix(plan: ExplorePlan, states: CandidateState[]): ExploreMatrix {
  const byKey = new Map<string, CandidateState>();
  for (const s of states) byKey.set(`${s.variantLabel}:${s.method}`, s);
  const rows: ExploreMatrixRow[] = plan.combos.map((c) => ({
    comboId: c.id,
    label: c.label,
    memberCount: c.memberKeys.length,
  }));
  const cells: Record<string, CandidateState | null> = {};
  for (const c of plan.combos) {
    for (const m of plan.methods) {
      const key = `${c.id}:${m}`;
      cells[key] = byKey.get(key) ?? null;
    }
  }
  return { rows, cols: plan.methods, cells };
}

// 本地组合列表 → ExplorePlan（cn 展示名），供矩阵装配（回流成员用 LocalCombo.members 全保真，非 memberKeys）。
export function localPlan(
  combos: LocalCombo[],
  methods: TrainMethod[],
  poolKeys: string[],
  strategy: ExploreStrategy,
  params: ExploreParams,
): ExplorePlan {
  return {
    strategy,
    params,
    methods,
    poolKeys,
    combos: combos.map((c) => ({ id: c.id, label: c.label, memberKeys: c.members.map((m) => m.factorKey) })),
  };
}

// 胜出组合 → 回流 target（用胜出子集成员 + origin:"builder" + config.normWindow=共享 normWindow，避
// FACTOR_NORM_WINDOW_MISMATCH，factor-combo-exploration §2.7/C-4）。回流经共享 buildTrainResult 单写入器 +
// modelId=null（全新草稿），绝不绑定探索父模型（P0-1）。
export function applyBestTarget(
  winner: Pick<ExploreWinner, "members" | "method">,
  ctx: {
    thresholds: FactorThresholds;
    name: string;
    instId: string;
    bar: string;
    startTs: number;
    endTs: number;
    horizon: number;
    sharedNormWindow: number;
    factorSetId?: number;
  },
): WeightTrainTarget {
  return {
    members: winner.members,
    method: winner.method,
    thresholds: ctx.thresholds,
    horizon: ctx.horizon,
    instId: ctx.instId,
    bar: ctx.bar,
    startTs: ctx.startTs,
    endTs: ctx.endTs,
    name: ctx.name,
    config: { normWindow: ctx.sharedNormWindow },
    origin: "builder",
    ...(ctx.factorSetId != null ? { factorSetId: ctx.factorSetId } : {}),
  };
}
