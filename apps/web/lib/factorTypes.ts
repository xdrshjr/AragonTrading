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

export interface TrainResult {
  method: TrainMethod;
  weights: Record<string, number>;
  normStats: { normWindow: number; clip: number };
  metrics: TrainMetrics;
  available: boolean;
  note: string;
  trainerVersion: string | null;
}

export interface TrainerInfo {
  method: TrainMethod;
  available: boolean;
}

// 因子组合（GET /factor-sets）。
export interface FactorSet {
  id: number;
  name: string;
  members: FactorMember[];
  method: TrainMethod;
  config: { thresholds?: FactorThresholds } & Record<string, unknown>;
  model: { weights?: Record<string, number>; normStats?: Record<string, number> } & Record<string, unknown>;
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
}

export const DEFAULT_THRESHOLDS: FactorThresholds = {
  enterLong: 0.3,
  enterShort: 0.3,
  exitBand: 0.1,
  normWindow: 200,
  squash: true,
};
