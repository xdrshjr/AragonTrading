// 因子层 TS 类型（与后端 camelCase 对齐，spec §3.2）。
// 独立 Factor Layer：行情 → 因子计算 → 评估/挖掘 → 权重训练 → 组合得分 → 策略决策。

export type FactorDirection = "long" | "short" | "auto";
export type FrozenDirection = "long" | "short";
export type FactorSource = "builtin" | "mined" | "custom";
export type TrainMethod = "equal" | "ic_weight" | "linear" | "random_forest" | "xgboost";

// 因子库目录条目（GET /factors/library）。
export interface FactorLibraryItem {
  key: string;
  cn: string;
  category: string;
  directionHint: FrozenDirection;
  paramsSchema: Record<string, FactorParamDef>;
  intradayOnly: boolean;
}

export interface FactorParamDef {
  type: "int" | "number" | "bool" | "enum";
  default: number | boolean | string;
  min?: number;
  max?: number;
  step?: number;
  options?: string[];
  label: string;
}

// 已存因子（GET /factors）。
export interface Factor {
  id: number;
  factorKey: string;
  name: string;
  params: Record<string, unknown>;
  direction: FactorDirection;
  category: string;
  source: FactorSource;
  enabled: boolean;
  builtin: boolean;
  notes: string;
  eval: FactorEvalResult | null;
  createdTs: number;
  updatedTs: number;
}

// 评估结果（POST /factors/evaluate）。
export interface QuantileBucket {
  q: number;
  meanRet: number;
  count: number;
}

export interface FactorHorizonStat {
  h: number;
  ic: number | null;
  rankIc: number | null;
  icMean: number | null;
  icStd: number | null;
  ir: number | null;
  tStat: number | null;
  monotonicity: number | null;
  topBottomSpread: number | null;
  quantiles: QuantileBucket[];
}

export interface FactorEvalResult {
  factorKey: string;
  direction: FrozenDirection;
  bars: number;
  windowStartTs: number;
  windowEndTs: number;
  coverage: number;
  occurrences: number;
  horizons: FactorHorizonStat[];
  instId: string | null;
  bar: string | null;
  note: string;
}

// 计算结果（POST /factors/compute）。
export interface FactorPoint {
  ts: number;
  value: number;
}

export interface FactorComputeResult {
  instId: string;
  bar: string;
  points: FactorPoint[];
  coverage: number;
  // 因子对当前 bar 不兼容（日内因子用于非日内周期）时后端附 note:"bar_incompatible"（L5）；旧响应无此字段，缺省安全。
  note?: string;
}

// 挖掘结果（POST /factors/mine）。
export interface RankedFactor {
  factorKey: string;
  params: Record<string, unknown>;
  direction: FrozenDirection;
  category: string;
  ic: number | null;
  rankIc: number | null;
  ir: number | null;
  monotonicity: number | null;
  coverage: number;
  occurrences: number;
  note: string;
}

export interface FactorMineResult {
  items: RankedFactor[];
  truncated: boolean;
  // 落库计数（H5，绝不静默丢弃）：evaluated=考虑落库的候选数，persisted=新写入，skipped=去重/无效。
  evaluated?: number;
  persisted?: number;
  skipped?: number;
}

// 组合成员（自包含快照，方向冻结）。
export interface FactorMember {
  factorKey: string;
  params: Record<string, unknown>;
  weight: number;
  direction: FrozenDirection;
}

// 训练结果（POST /factor-sets/train）。
export interface TrainMetrics {
  icIn: number | null;
  icOut: number | null;
  r2In: number | null;
  r2Out: number | null;
  nTrain: number;
  nValid: number;
}

// 训练溯源元数据（H7）：标的/周期/区间/horizon + 训练时刻（trainedAt 由前端注入秒级 UTC）。
export interface TrainProvenance {
  instId: string;
  bar: string;
  startTs: number;
  endTs: number;
  horizon: number;
  trainedAt?: number;
}

export interface TrainResult {
  method: TrainMethod;
  weights: Record<string, number>;
  normStats: { normWindow: number; clip: number };
  metrics: TrainMetrics;
  available: boolean;
  note: string;
  trainerVersion: string | null;
  provenance?: TrainProvenance;
}

// 生效后端（评审 P2-3）：native=核心 numpy 训练器，library=sklearn/xgboost 在场，builtin=内置树集成兜底。
// 旧响应无此字段，缺省安全（UI 仅在 available=false 时禁用选项）。
export interface TrainerInfo {
  method: TrainMethod;
  available: boolean;
  backend?: "native" | "library" | "builtin";
}

// 因子组合（GET /factor-sets）。
export interface FactorSet {
  id: number;
  name: string;
  members: FactorMember[];
  method: TrainMethod;
  config: { thresholds?: FactorThresholds } & Record<string, unknown>;
  model: {
    weights?: Record<string, number>;
    normStats?: Record<string, number>;
    provenance?: TrainProvenance;
    trainedAt?: number;
  } & Record<string, unknown>;
  metrics: TrainMetrics | null;
  enabled: boolean;
  createdTs: number;
  updatedTs: number;
}

export interface FactorThresholds {
  enterLong: number;
  enterShort: number;
  exitBand: number;
  normWindow: number;
  squash: boolean;
}

// 复合得分（POST /factor-sets/{id}/score、/score-preview）。
export interface ScorePoint {
  ts: number;
  score: number;
}

export interface FactorScoreResult {
  points: ScorePoint[];
  latest: number | null;
  coverage: number;
  // 区间过短（window_too_short，H6）或成员对当前 bar 不兼容（bar_incompatible，L5）时后端附此提示；旧响应无此字段，缺省安全。
  note?: string;
  minBarsRequired?: number;
}

// factor_score 策略内联快照 params（自包含；provenance 仅供 UI 展示训练区间，绝不参与打分，L4）。
export interface FactorScoreSnapshotParams {
  members: FactorMember[];
  thresholds: FactorThresholds;
  normStats?: { normWindow: number; clip: number };
  provenance?: TrainProvenance;
  // 弱关联来源组合（factor_set）id：仅供「在因子实验室编辑」回链定位，不参与打分（prepare/on_candle
  // 不读它，被后端透传忽略）；旧快照缺省安全 → buildEditTargetFromStrategy 回落 snapshot 分支。
  factorSetId?: number;
}

// 跨视图编辑交接载荷（瞬态 store.factorEditTarget）：从「因子组合策略」列表或交易面板「在因子实验室
// 编辑」发起，由 FactorSetBuilder 挂载时消费一次性水合。set 分支额外携带 snapshot 兜底（P1-1）：
// setId 指向的组合若已删（sets.get 404），builder 回落用 snapshot 水合并提示。从 builder「编辑」入口
// 发起时 strategyId/name/snapshot 可缺省（直接有 set）；从交易面板发起时三者必带（兜底所需）。
export type FactorEditTarget =
  | { source: "set"; setId: number; strategyId?: number; name?: string; snapshot?: FactorScoreSnapshotParams }
  | { source: "snapshot"; strategyId: number; name: string; snapshot: FactorScoreSnapshotParams };

export const DEFAULT_THRESHOLDS: FactorThresholds = {
  enterLong: 0.3,
  enterShort: 0.3,
  exitBand: 0.1,
  normWindow: 200,
  squash: true,
};
