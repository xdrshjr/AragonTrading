// 多模型训练竞技场 TS 契约（与后端 camelCase 对齐，weight-multi-model-training §4.3）。
// 复用权重模型 / 因子层既有契约（WeightTrainRun / TrainStreamEvent / FactorMember / FactorThresholds ...）。
import type { FactorMember, FactorThresholds, TrainMethod, TrainMetrics } from "./factorTypes";
import type {
  TrainCurvePoint,
  TrainPhase,
  TrainResultEvent,
  TrainStreamEvent,
  WeightRunStatus,
  WeightTrainRun,
} from "./weightModelTypes";

export type WeightBatchStatus = "running" | "succeeded" | "failed" | "cancelled";
export type WeightBatchOrigin = "builder" | "model";

// 排行榜条目（后端 batchDone.ranking + 前端实时派生同构）。
export interface WeightBatchRankRow {
  runId: number;
  method: TrainMethod;
  variantLabel: string;
  status: WeightRunStatus;
  icOut: number | null;
  icIn: number | null;
  nTrain: number | null;
  isBest: boolean;
}

// roster 候选（WS batch 事件 + start 响应）。
export interface WeightBatchCandidate {
  runId: number;
  method: TrainMethod;
  variantLabel: string;
  status: WeightRunStatus;
}

// 批次列表概要（GET /weight-models/batches）。
export interface WeightBatchSummary {
  batchId: number;
  label: string;
  instId: string;
  bar: string;
  startTs: number;
  endTs: number;
  horizon: number;
  parentModelId: number;
  factorSetId: number | null;
  origin: WeightBatchOrigin;
  selectedRunId: number | null;
  status: WeightBatchStatus;
  candidateCount: number;
  succeededCount: number;
  best: WeightBatchRankRow | null;
  createdTs: number;
  updatedTs: number;
}

// 批次详情（GET /weight-models/batches/{id}）。
export interface WeightBatchDetail {
  batchId: number;
  label: string;
  instId: string;
  bar: string;
  startTs: number;
  endTs: number;
  horizon: number;
  members: FactorMember[];
  config: { thresholds?: Partial<FactorThresholds> } & Record<string, unknown>;
  parentModelId: number;
  factorSetId: number | null;
  origin: WeightBatchOrigin;
  selectedRunId: number | null;
  status: WeightBatchStatus;
  candidates: WeightTrainRun[];
  ranking: WeightBatchRankRow[];
  createdTs: number;
  updatedTs: number;
}

// 启动响应（POST /weight-models/batches）。
export interface WeightBatchStartResult {
  batchId: number;
  parentModelId: number;
  label: string;
  origin: WeightBatchOrigin;
  candidates: WeightBatchCandidate[];
}

// 跨视图交接载荷（瞬态 store.weightBatchTarget）：FactorSetBuilder 点「多模型训练」写入，
// WeightModelPanel 挂载消费一次性切到竞技场启动器（模式同 weightTrainTarget）。
export interface WeightBatchTarget {
  members: FactorMember[];
  thresholds: FactorThresholds;
  horizon: number;
  instId: string;
  bar: string;
  startTs: number;
  endTs: number;
  name: string;
  methods?: TrainMethod[]; // 预选（launcher 可增删）
  factorSetId?: number;
  origin: WeightBatchOrigin;
}

// WS 批次流事件（/api/ws/weight-batch/{batchId}）：batch|candidate|batchDone|ping 联合类型。
export type BatchStreamEvent =
  | {
      type: "batch";
      data: {
        batchId: number;
        parentModelId: number;
        status: WeightBatchStatus;
        candidates: WeightBatchCandidate[];
      };
    }
  | {
      type: "candidate";
      data: { runId: number; method: TrainMethod; variantLabel: string; event: TrainStreamEvent };
    }
  | { type: "batchDone"; data: { batchId: number; finishedTs: number; ranking: WeightBatchRankRow[] } }
  | { type: "ping" };

// 前端「按候选」reducer 状态（竞技场每张候选卡的实时态）。
export interface CandidateState {
  runId: number;
  method: TrainMethod;
  variantLabel: string;
  status: WeightRunStatus;
  phase: { phase: TrainPhase; pct: number } | null;
  curve: TrainCurvePoint[];
  liveWeights: Record<string, number> | null;
  result: TrainResultEvent | null;
  metrics: TrainMetrics | null;
  error: { code: string; message: string } | null;
}
