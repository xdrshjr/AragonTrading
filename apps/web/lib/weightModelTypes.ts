// 权重模型工作区 TS 类型（与后端 camelCase 对齐，weight-model-workspace §6.3）。
// 复用因子层既有契约（TrainMethod / FactorMember / TrainMetrics / TrainProvenance / FactorThresholds）。
import type {
  FactorMember,
  FactorThresholds,
  TrainMethod,
  TrainMetrics,
  TrainProvenance,
} from "./factorTypes";

export type WeightModelSource = "builder" | "manual";
export type WeightRunStatus = "running" | "succeeded" | "failed" | "cancelled";
export type NormStats = { normWindow: number; clip: number };

// 扩样本学习曲线点（GET runs/{id}.curve + WS curve 事件）。损失 / IC 任一段样本不足记 null（绝不假装 0）。
export interface TrainCurvePoint {
  step: number;
  fraction: number;
  nTrain: number;
  trainLoss: number | null;
  validLoss: number | null;
  trainIc: number | null;
  validIc: number | null;
}

// 一次训练记录（GET /weight-models/{id}/runs[/{runId}]）。列表态 curve=null（仅 curveCount），详情含完整 curve。
export interface WeightTrainRun {
  id: number;
  modelId: number;
  status: WeightRunStatus;
  method: TrainMethod;
  instId: string;
  bar: string;
  startTs: number;
  endTs: number;
  horizon: number;
  config: Record<string, unknown>;
  weights: Record<string, number>;
  normStats: Partial<NormStats>;
  metrics: TrainMetrics | null;
  provenance: TrainProvenance | Record<string, unknown>;
  trainerVersion: string | null;
  note: string;
  errorCode: string | null;
  errorMessage: string | null;
  curveCount: number;
  startedTs: number;
  finishedTs: number | null;
  curve: TrainCurvePoint[] | null;
}

// 权重模型（GET /weight-models[...]）。
export interface WeightModel {
  id: number;
  name: string;
  instId: string;
  bar: string;
  members: FactorMember[];
  method: TrainMethod;
  config: { thresholds?: Partial<FactorThresholds> } & Record<string, unknown>;
  horizon: number;
  source: WeightModelSource;
  factorSetId: number | null;
  latestRunId: number | null;
  bestValidIc: number | null;
  notes: string;
  enabled: boolean;
  latestRun: WeightTrainRun | null;
  createdTs: number;
  updatedTs: number;
}

// 跨视图交接载荷（瞬态 store.weightTrainTarget）：FactorSetBuilder 点「训练权重」写入，
// WeightModelPanel 挂载消费一次性切到实时训练视图（模式同 factorEditTarget）。
export interface WeightTrainTarget {
  members: FactorMember[];
  method: TrainMethod;
  config: { normWindow: number } & Record<string, unknown>;
  // 完整交易阈值（随载荷往返）：FactorLab 条件渲染会使 builder 在切到「权重模型」时卸载，回流时 builder
  // 重挂载已丢失本地态，故交接载荷须携带完整 builder 状态以便无损水合（与 factorEditTarget 同范式）。
  thresholds: FactorThresholds;
  horizon: number;
  instId: string;
  bar: string;
  startTs: number;
  endTs: number;
  name: string;
  modelId?: number;
  factorSetId?: number;
  // 发起来源（瞬态，不持久化）：builder = 搭建台「训练权重」（同会话复用记忆 boundModelId）；
  // model = 列表/详情「训练」已存模型（不写 boundModelId，杜绝跨上下文污染，H-F2）。
  origin: "builder" | "model";
}

// 训练产物回流载荷（瞬态 store.weightTrainResult）：WeightTrainLive「应用到组合并返回」写入，
// FactorSetBuilder 消费**完整水合**（members/method/name/thresholds/trained + 编辑指针），因 builder 卸载重挂载
// 已丢失本地态，故载荷须自包含（绝不只回 trained，否则重挂载后 members 空、trainedFresh=false、无法保存）。
export interface WeightTrainResult {
  members: FactorMember[];
  method: TrainMethod;
  name: string;
  thresholds: FactorThresholds;
  weights: Record<string, number>;
  normStats: NormStats;
  metrics: TrainMetrics;
  provenance?: TrainProvenance;
  trainerVersion?: string | null;
  note?: string;
  modelId: number;
  runId: number;
  factorSetId?: number;
}

// WS 流式事件（/api/ws/weight-train/{jobId}）：run|phase|curve|result|done|cancelled|error 联合类型。
export type TrainPhase = "loading" | "building" | "fitting" | "evaluating";

export interface TrainResultEvent {
  method: TrainMethod;
  weights: Record<string, number>;
  normStats: NormStats;
  metrics: TrainMetrics;
  trainerVersion: string | null;
  note: string;
  provenance: TrainProvenance;
}

export type TrainStreamEvent =
  | { type: "run"; data: { runId: number; modelId: number; status: "running" } }
  | { type: "phase"; data: { phase: TrainPhase; pct: number } }
  | { type: "curve"; data: TrainCurvePoint & { weights?: Record<string, number> } }
  | { type: "result"; data: TrainResultEvent }
  | { type: "done"; data: { runId: number; finishedTs: number } }
  | { type: "cancelled"; data: { runId: number; finishedTs: number } }
  | { type: "error"; data: { code: string; message: string; runId?: number } };
