// 权重模型工作区纯助手：曲线几何、最佳/最近 run 选取、交接载荷构造（与 weightModel.test.ts 同约定，组件只消费）。
// 与 factor.ts 同约定：纯函数 + 同名单测，组件不内联此处逻辑。
import { formatIc } from "./factor";
import { DEFAULT_THRESHOLDS } from "./factorTypes";
import type { FactorMember, FactorThresholds, TrainMethod, TrainResult } from "./factorTypes";
import type { WeightModelCreateBody, WeightTrainStartBody } from "./weightModelApi";
import type {
  TrainCurvePoint,
  TrainPhase,
  TrainResultEvent,
  WeightModel,
  WeightRunStatus,
  WeightTrainResult,
  WeightTrainRun,
  WeightTrainTarget,
} from "./weightModelTypes";

export type CurveKey = "trainLoss" | "validLoss" | "trainIc" | "validIc";

export interface PlotPoint {
  x: number;
  y: number;
}

export interface MetricGeom {
  trainPath: PlotPoint[];
  validPath: PlotPoint[];
  min: number;
  max: number;
}

// 一对（train/valid）曲线序列 → SVG 坐标：x 按 fraction 归一填满宽度，y 按两序列共享 [min,max] 反转；
// null 点跳过（绝不补零，缺测段断线）。共享 domain 使 train/valid 同尺可比。
export function metricGeometry(
  curve: TrainCurvePoint[],
  trainKey: CurveKey,
  validKey: CurveKey,
  width: number,
  height: number,
  pad = 6,
): MetricGeom {
  const trainVals = curve.map((p) => p[trainKey]);
  const validVals = curve.map((p) => p[validKey]);
  const finite = [...trainVals, ...validVals].filter(
    (v): v is number => v != null && Number.isFinite(v),
  );
  if (finite.length === 0) return { trainPath: [], validPath: [], min: 0, max: 0 };
  let min = Math.min(...finite);
  let max = Math.max(...finite);
  if (min === max) {
    min -= 0.5;
    max += 0.5;
  }
  const fracs = curve.map((p) => p.fraction);
  const fmin = Math.min(...fracs);
  const fspan = Math.max(...fracs) - fmin || 1;
  const innerW = Math.max(1, width - pad * 2);
  const innerH = Math.max(1, height - pad * 2);
  const span = max - min;
  const toPath = (vals: (number | null)[]): PlotPoint[] => {
    const pts: PlotPoint[] = [];
    vals.forEach((v, i) => {
      if (v == null || !Number.isFinite(v)) return;
      const fx = (curve[i].fraction - fmin) / fspan;
      pts.push({ x: pad + fx * innerW, y: pad + innerH - ((v - min) / span) * innerH });
    });
    return pts;
  };
  return { trainPath: toPath(trainVals), validPath: toPath(validVals), min, max };
}

export function plotPath(points: PlotPoint[]): string {
  return points.map((p, i) => `${i === 0 ? "M" : "L"}${p.x.toFixed(1)},${p.y.toFixed(1)}`).join(" ");
}

// 是否曲线含任一可画点（任一序列任一非 null）。
export function hasCurveData(curve: TrainCurvePoint[]): boolean {
  return curve.some(
    (p) => [p.trainLoss, p.validLoss, p.trainIc, p.validIc].some((v) => v != null && Number.isFinite(v)),
  );
}

export function succeededRuns(runs: WeightTrainRun[]): WeightTrainRun[] {
  return runs.filter((r) => r.status === "succeeded");
}

// 历史最佳 run：succeeded 中样本外 IC（icOut）最大者；无则 null。
export function bestRun(runs: WeightTrainRun[]): WeightTrainRun | null {
  const scored = succeededRuns(runs).filter((r) => r.metrics?.icOut != null);
  if (scored.length === 0) return null;
  return scored.reduce((best, r) => ((r.metrics!.icOut as number) > (best.metrics!.icOut as number) ? r : best));
}

// 最近 run：id 最大者（含未成功，供详情默认选中最新一次）；无则 null。
export function latestRun(runs: WeightTrainRun[]): WeightTrainRun | null {
  if (runs.length === 0) return null;
  return runs.reduce((latest, r) => (r.id > latest.id ? r : latest));
}

// 列表排序：updatedTs 降序（最近更新置顶，与 FactorSetList 同约定）。
export function sortModelsByUpdated(models: WeightModel[]): WeightModel[] {
  return [...models].sort((a, b) => b.updatedTs - a.updatedTs);
}

const METHOD_LABELS: Record<TrainMethod, string> = {
  linear: "线性岭回归",
  equal: "等权",
  ic_weight: "IC 加权",
  random_forest: "随机森林",
  xgboost: "XGBoost",
};

export function methodLabel(method: TrainMethod): string {
  return METHOD_LABELS[method] ?? method;
}

const PHASE_LABELS: Record<TrainPhase, string> = {
  loading: "加载真实行情",
  building: "构建因子矩阵",
  fitting: "扩样本拟合权重",
  evaluating: "评估留出验证段",
};

export function phaseLabel(phase: TrainPhase): string {
  return PHASE_LABELS[phase] ?? phase;
}

const STATUS_LABELS: Record<WeightRunStatus, string> = {
  running: "训练中",
  succeeded: "成功",
  failed: "失败",
  cancelled: "已取消",
};

export function runStatusLabel(status: WeightRunStatus): string {
  return STATUS_LABELS[status] ?? status;
}

export function runStatusTone(status: WeightRunStatus): "long" | "short" | "neutral" | "accent" {
  if (status === "succeeded") return "long";
  if (status === "failed") return "short";
  if (status === "running") return "accent";
  return "neutral";
}

// 损失数值格式化：null → "—"，否则三位小数（缺测显式，绝不假装 0）。
export function formatLoss(value: number | null | undefined): string {
  return value == null || !Number.isFinite(value) ? "—" : value.toFixed(3);
}

export { formatIc };

// builder「训练权重」→ 交接载荷（瞬态 store.weightTrainTarget）。携带完整 builder 状态供回流无损水合。
export function buildTrainTarget(params: {
  members: FactorMember[];
  method: TrainMethod;
  thresholds: FactorThresholds;
  horizon: number;
  instId: string;
  bar: string;
  startTs: number;
  endTs: number;
  name: string;
  modelId?: number;
  factorSetId?: number;
}): WeightTrainTarget {
  return {
    members: params.members,
    method: params.method,
    config: { normWindow: params.thresholds.normWindow },
    thresholds: params.thresholds,
    horizon: params.horizon,
    instId: params.instId,
    bar: params.bar,
    startTs: params.startTs,
    endTs: params.endTs,
    name: params.name,
    ...(params.modelId != null ? { modelId: params.modelId } : {}),
    ...(params.factorSetId != null ? { factorSetId: params.factorSetId } : {}),
  };
}

// 交接载荷 → POST /weight-models/train 请求体。
export function trainStartBody(target: WeightTrainTarget): WeightTrainStartBody {
  return {
    instId: target.instId,
    bar: target.bar,
    startTs: target.startTs,
    endTs: target.endTs,
    members: target.members,
    method: target.method,
    config: target.config,
    horizon: target.horizon,
    ...(target.modelId != null ? { modelId: target.modelId } : {}),
    ...(target.factorSetId != null ? { factorSetId: target.factorSetId } : {}),
    ...(target.name ? { name: target.name } : {}),
  };
}

// 模型 → 新建训练交接载荷（在列表「训练」直接进实时视图，复用模型当前 spec）。
export function modelToTrainTarget(model: WeightModel, range: {
  instId: string;
  bar: string;
  startTs: number;
  endTs: number;
}): WeightTrainTarget {
  const thresholds: FactorThresholds = { ...DEFAULT_THRESHOLDS, ...(model.config?.thresholds ?? {}) };
  return buildTrainTarget({
    members: model.members,
    method: model.method,
    thresholds,
    horizon: model.horizon,
    instId: range.instId,
    bar: range.bar,
    startTs: range.startTs,
    endTs: range.endTs,
    name: model.name,
    modelId: model.id,
    factorSetId: model.factorSetId ?? undefined,
  });
}

// 模型编辑器 → 新建模型请求体。
export function editorToCreateBody(form: {
  name: string;
  instId: string;
  bar: string;
  members: FactorMember[];
  method: TrainMethod;
  horizon: number;
  notes: string;
}): WeightModelCreateBody {
  return {
    name: form.name,
    instId: form.instId,
    bar: form.bar,
    members: form.members,
    method: form.method,
    horizon: form.horizon,
    notes: form.notes,
  };
}

// WS result 事件 + 交接 target + 句柄 → 完整回流载荷（瞬态 store.weightTrainResult）。
// members/method/name/thresholds 取自 target（重挂载水合所需），weights/normStats/metrics/provenance 取自 WS 产物。
export function buildTrainResult(
  event: TrainResultEvent,
  target: WeightTrainTarget,
  ids: { modelId: number; runId: number },
): WeightTrainResult {
  return {
    members: target.members,
    method: event.method,
    name: target.name,
    thresholds: target.thresholds,
    weights: event.weights,
    normStats: event.normStats,
    metrics: event.metrics,
    provenance: event.provenance,
    trainerVersion: event.trainerVersion,
    note: event.note,
    modelId: ids.modelId,
    runId: ids.runId,
    ...(target.factorSetId != null ? { factorSetId: target.factorSetId } : {}),
  };
}

// 回流载荷 → factorTypes.TrainResult（供 FactorSetBuilder 水合 trained，= 旧 train.onSuccess 效果）。
export function weightResultToTrained(result: WeightTrainResult): TrainResult {
  return {
    method: result.method,
    weights: result.weights,
    normStats: result.normStats,
    metrics: result.metrics,
    available: true,
    note: result.note ?? "",
    trainerVersion: result.trainerVersion ?? null,
    provenance: result.provenance,
  };
}

// 历史 run → factorTypes.TrainResult（详情页权重条 / 指标复用既有渲染）。
export function runToTrained(run: WeightTrainRun): TrainResult | null {
  if (run.status !== "succeeded" || !run.weights || Object.keys(run.weights).length === 0) return null;
  const normWindow = typeof run.normStats?.normWindow === "number" ? run.normStats.normWindow : 200;
  const clip = typeof run.normStats?.clip === "number" ? run.normStats.clip : 4;
  return {
    method: run.method,
    weights: run.weights,
    normStats: { normWindow, clip },
    metrics: run.metrics ?? { icIn: null, icOut: null, r2In: null, r2Out: null, nTrain: 0, nValid: 0 },
    available: true,
    note: run.note ?? "",
    trainerVersion: run.trainerVersion ?? null,
    provenance: run.provenance as TrainResult["provenance"],
  };
}
