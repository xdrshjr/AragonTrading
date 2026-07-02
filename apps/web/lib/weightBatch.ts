// 多模型训练竞技场纯助手：候选事件 reducer、排行榜派生、批次状态派生、target·body 构造
// （与 weightBatch.test.ts 同约定，组件只消费）。与 weightModel.ts 同范式：纯函数 + 同名单测。
import { formatIc } from "./factor";
import type { FactorMember, FactorThresholds, TrainMethod } from "./factorTypes";
import { methodLabel } from "./weightModel";
import type { WeightBatchCandidateBody, WeightBatchStartBody } from "./weightBatchApi";
import type {
  CandidateState,
  WeightBatchOrigin,
  WeightBatchRankRow,
  WeightBatchStatus,
  WeightBatchTarget,
} from "./weightBatchTypes";
import type { TrainCurvePoint, TrainStreamEvent } from "./weightModelTypes";
import type { WeightBatchCandidate as RosterCandidate } from "./weightBatchTypes";

// 全部可选训练方法（共享导出，factor-combo-exploration P2-6/C-5）：launcher 与探索弹窗方法多选复用同一真源，
// 杜绝两份漂移。可用性仍由 factorApi.trainers 逐项判定（未安装置灰）。
export const ALL_METHODS: TrainMethod[] = ["linear", "equal", "ic_weight", "random_forest", "xgboost"];

// 默认多模型候选方法（launcher 初始勾选）：覆盖线性 / 树集成主流，用户可增删。
export const DEFAULT_BATCH_METHODS: TrainMethod[] = ["linear", "random_forest", "xgboost"];

// roster 候选 → 初始候选态（queued，未有任何流事件）。
export function emptyCandidate(entry: RosterCandidate): CandidateState {
  return {
    runId: entry.runId,
    method: entry.method,
    variantLabel: entry.variantLabel,
    status: entry.status,
    phase: null,
    curve: [],
    liveWeights: null,
    result: null,
    metrics: null,
    error: null,
  };
}

// 曲线点按 step upsert（重连回放同 step 覆盖，避免重复堆叠；镜像 WeightTrainLive）。
function upsertCurvePoint(prev: TrainCurvePoint[], point: TrainCurvePoint): TrainCurvePoint[] {
  const idx = prev.findIndex((p) => p.step === point.step);
  if (idx === -1) return [...prev, point].sort((a, b) => a.step - b.step);
  const next = [...prev];
  next[idx] = point;
  return next;
}

// 单候选内部 TrainStreamEvent → 新候选态（镜像 WeightTrainLive.onEvent 的单候选归约，纯函数便于测试）。
export function applyCandidateEvent(state: CandidateState, event: TrainStreamEvent): CandidateState {
  switch (event.type) {
    case "run":
      return { ...state, status: "running" };
    case "phase":
      return { ...state, status: "running", phase: event.data };
    case "curve": {
      const { weights, ...point } = event.data;
      return {
        ...state,
        status: "running",
        curve: upsertCurvePoint(state.curve, point),
        liveWeights: weights ?? state.liveWeights,
      };
    }
    case "result":
      return {
        ...state,
        result: event.data,
        metrics: event.data.metrics,
        liveWeights: event.data.weights,
      };
    case "done":
      return { ...state, status: "succeeded" };
    case "cancelled":
      return { ...state, status: "cancelled" };
    case "error":
      return { ...state, status: "failed", error: { code: event.data.code, message: event.data.message } };
    default:
      return state;
  }
}

// 某候选样本外 IC（succeeded 且有 result 才有值；供排行榜排序 / 高亮）。
export function candidateIcOut(state: CandidateState): number | null {
  const value = state.metrics?.icOut;
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

// 排行榜派生（与后端 weight_batch_ranking 同口径）：succeeded 按 icOut 降序（tie: icIn→nTrain→runId 升序），
// 非成功候选殿后；仅首个 succeeded 置 isBest。无成功候选时全无 isBest（择优 / 应用最佳按此禁用）。
export function rankCandidates(states: CandidateState[]): WeightBatchRankRow[] {
  const rows: WeightBatchRankRow[] = states.map((s) => ({
    runId: s.runId,
    method: s.method,
    variantLabel: s.variantLabel,
    status: s.status,
    icOut: candidateIcOut(s),
    icIn: typeof s.metrics?.icIn === "number" ? s.metrics.icIn : null,
    nTrain: typeof s.metrics?.nTrain === "number" ? s.metrics.nTrain : null,
    isBest: false,
  }));
  const scored = rows.filter((r) => r.status === "succeeded" && r.icOut != null);
  scored.sort(
    (a, b) =>
      (b.icOut as number) - (a.icOut as number) ||
      (b.icIn ?? -Infinity) - (a.icIn ?? -Infinity) ||
      (b.nTrain ?? 0) - (a.nTrain ?? 0) ||
      a.runId - b.runId,
  );
  const scoredIds = new Set(scored.map((r) => r.runId));
  const rest = rows.filter((r) => !scoredIds.has(r.runId));
  const ranking = [...scored, ...rest];
  if (scored.length > 0) ranking[0].isBest = true;
  return ranking;
}

// 最优候选 runId（首个 succeeded；无则 null）。
export function bestRunId(states: CandidateState[]): number | null {
  const ranking = rankCandidates(states);
  return ranking.length > 0 && ranking[0].isBest ? ranking[0].runId : null;
}

// 批次派生状态（与后端 weight_batch_status 同口径）。
export function batchDerivedStatus(states: CandidateState[]): WeightBatchStatus {
  const statuses = states.map((s) => s.status);
  if (statuses.some((s) => s === "queued" || s === "running")) return "running";
  if (statuses.some((s) => s === "succeeded")) return "succeeded";
  if (statuses.some((s) => s === "cancelled")) return "cancelled";
  return "failed";
}

// 候选进度百分比（succeeded=100；running 取 phase.pct；失败 / 取消保留最后 pct；queued=0）。
export function candidateProgressPct(state: CandidateState): number {
  if (state.status === "succeeded") return 100;
  if (state.status === "queued") return 0;
  return state.phase?.pct ?? 0;
}

// 是否全部候选已达终态（无 queued/running）——驱动竞技场「择优 / 应用最佳」动作解禁。
export function allSettled(states: CandidateState[]): boolean {
  return states.length > 0 && states.every(
    (s) => s.status === "succeeded" || s.status === "failed" || s.status === "cancelled",
  );
}

// builder / 列表「多模型训练」→ 交接载荷（瞬态 store.weightBatchTarget）。携带完整共享规格供 launcher 消费。
export function buildBatchTarget(params: {
  members: FactorMember[];
  thresholds: FactorThresholds;
  horizon: number;
  instId: string;
  bar: string;
  startTs: number;
  endTs: number;
  name: string;
  methods?: TrainMethod[];
  factorSetId?: number;
  origin?: WeightBatchOrigin;
}): WeightBatchTarget {
  return {
    members: params.members,
    thresholds: params.thresholds,
    horizon: params.horizon,
    instId: params.instId,
    bar: params.bar,
    startTs: params.startTs,
    endTs: params.endTs,
    name: params.name,
    origin: params.origin ?? "builder",
    ...(params.methods ? { methods: params.methods } : {}),
    ...(params.factorSetId != null ? { factorSetId: params.factorSetId } : {}),
  };
}

// 交接载荷 + 勾选候选 → POST /weight-models/batches 请求体。config 携 normWindow（训练用）+ thresholds
// （父模型回测用，与单次训练同口径）。
export function batchStartBody(
  target: WeightBatchTarget,
  candidates: WeightBatchCandidateBody[],
): WeightBatchStartBody {
  return {
    instId: target.instId,
    bar: target.bar,
    startTs: target.startTs,
    endTs: target.endTs,
    members: target.members,
    config: { normWindow: target.thresholds.normWindow, thresholds: target.thresholds },
    horizon: target.horizon,
    candidates,
    origin: target.origin,
    ...(target.name ? { name: target.name } : {}),
    ...(target.factorSetId != null ? { factorSetId: target.factorSetId } : {}),
  };
}

// 候选显示名：方法本地化 + 变体标签（如「线性岭回归 · L2=0.1」）。
export function candidateLabel(method: TrainMethod, variantLabel: string): string {
  return variantLabel ? `${methodLabel(method)} · ${variantLabel}` : methodLabel(method);
}

export { formatIc, methodLabel };
