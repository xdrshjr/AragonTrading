import { describe, expect, it } from "vitest";
import { DEFAULT_THRESHOLDS, type FactorMember } from "./factorTypes";
import {
  bestRun,
  buildTrainResult,
  buildTrainTarget,
  canBacktestModel,
  codeLabel,
  hasCurveData,
  latestRun,
  metricGeometry,
  modelToTrainTarget,
  runIsBacktestable,
  runToTrained,
  sortModelsByUpdated,
  trainStartBody,
  weightResultToTrained,
} from "./weightModel";
import type {
  TrainCurvePoint,
  TrainResultEvent,
  WeightModel,
  WeightTrainRun,
} from "./weightModelTypes";

const MEMBERS: FactorMember[] = [
  { factorKey: "mom_roc", params: {}, weight: 0, direction: "long" },
  { factorKey: "rev_rsi", params: {}, weight: 0, direction: "short" },
];

function curvePoint(over: Partial<TrainCurvePoint>): TrainCurvePoint {
  return { step: 0, fraction: 0.2, nTrain: 40, trainLoss: 1, validLoss: 1, trainIc: 0.1, validIc: 0.1, ...over };
}

function run(over: Partial<WeightTrainRun>): WeightTrainRun {
  return {
    id: 1, modelId: 1, status: "succeeded", method: "linear", instId: "BTC-USDT-SWAP", bar: "1H",
    startTs: 0, endTs: 1, horizon: 6, config: {}, weights: { mom_roc: 1 }, normStats: { normWindow: 100, clip: 4 },
    metrics: { icIn: 0.3, icOut: 0.2, r2In: null, r2Out: null, nTrain: 100, nValid: 40 },
    provenance: {}, trainerVersion: null, note: "", errorCode: null, errorMessage: null,
    curveCount: 0, startedTs: 0, finishedTs: 1, curve: null, ...over,
  };
}

describe("metricGeometry", () => {
  it("maps train/valid series and shares one domain", () => {
    const curve = [
      curvePoint({ step: 0, fraction: 0.2, validLoss: 0.4, trainLoss: 0.1 }),
      curvePoint({ step: 1, fraction: 1.0, validLoss: 0.2, trainLoss: 0.05 }),
    ];
    const geom = metricGeometry(curve, "trainLoss", "validLoss", 100, 50);
    expect(geom.trainPath).toHaveLength(2);
    expect(geom.validPath).toHaveLength(2);
    expect(geom.min).toBeCloseTo(0.05);
    expect(geom.max).toBeCloseTo(0.4);
    // x 按 fraction 归一填满宽度：首点贴左、末点贴右。
    expect(geom.trainPath[0].x).toBeLessThan(geom.trainPath[1].x);
  });

  it("skips null points (no zero-fill)", () => {
    const curve = [
      curvePoint({ step: 0, fraction: 0.2, validLoss: null }),
      curvePoint({ step: 1, fraction: 1.0, validLoss: 0.3 }),
    ];
    const geom = metricGeometry(curve, "trainLoss", "validLoss", 100, 50);
    expect(geom.validPath).toHaveLength(1); // null 段断线
  });

  // H-T7：单点 / 全 null / 空曲线的几何稳健性（绝不崩、绝不补假点）。
  it("handles a single point (degenerate domain expands)", () => {
    const geom = metricGeometry([curvePoint({ step: 0, fraction: 1.0, validIc: 0.2, trainIc: 0.2 })],
      "trainIc", "validIc", 100, 50);
    expect(geom.trainPath).toHaveLength(1);
    expect(geom.validPath).toHaveLength(1);
    expect(geom.max).toBeGreaterThan(geom.min); // min===max → 人为撑开避免除零
  });

  it("returns empty geometry when all values are null", () => {
    const curve = [
      curvePoint({ step: 0, fraction: 0.2, trainIc: null, validIc: null }),
      curvePoint({ step: 1, fraction: 1.0, trainIc: null, validIc: null }),
    ];
    const geom = metricGeometry(curve, "trainIc", "validIc", 100, 50);
    expect(geom.trainPath).toHaveLength(0);
    expect(geom.validPath).toHaveLength(0);
    expect(geom.min).toBe(0);
    expect(geom.max).toBe(0);
  });

  it("returns empty geometry for an empty curve", () => {
    const geom = metricGeometry([], "trainLoss", "validLoss", 100, 50);
    expect(geom.trainPath).toHaveLength(0);
    expect(geom.validPath).toHaveLength(0);
  });

  // G-T10：NaN / Infinity 与 null 同口径剔除（绝不画到非有限坐标，绝不补假点）。
  it("treats NaN/Infinity values as missing (skipped, not plotted)", () => {
    const curve = [
      curvePoint({ step: 0, fraction: 0.2, validLoss: Number.NaN, trainLoss: 0.3 }),
      curvePoint({ step: 1, fraction: 0.6, validLoss: Number.POSITIVE_INFINITY, trainLoss: 0.2 }),
      curvePoint({ step: 2, fraction: 1.0, validLoss: 0.1, trainLoss: 0.05 }),
    ];
    const geom = metricGeometry(curve, "trainLoss", "validLoss", 100, 50);
    expect(geom.validPath).toHaveLength(1);   // 仅末点有限
    expect(Number.isFinite(geom.min) && Number.isFinite(geom.max)).toBe(true);
    expect(geom.trainPath.every((p) => Number.isFinite(p.x) && Number.isFinite(p.y))).toBe(true);
  });

  it("hasCurveData ignores NaN/Infinity-only series", () => {
    expect(hasCurveData([curvePoint({ trainLoss: Number.NaN, validLoss: Number.POSITIVE_INFINITY,
      trainIc: Number.NaN, validIc: Number.NaN })])).toBe(false);
    expect(hasCurveData([curvePoint({ validIc: 0.1 })])).toBe(true);
  });
});

describe("codeLabel (G-F6)", () => {
  it("maps known error codes to friendly Chinese labels", () => {
    expect(codeLabel("EMPTY_RANGE")).toMatch(/已确认的行情/);
    expect(codeLabel("WEIGHT_TRAIN_INSUFFICIENT_SAMPLES")).toMatch(/样本不足/);
    expect(codeLabel("CANCEL_TIMEOUT")).toMatch(/取消未确认/);
  });

  it("falls back to the raw code for unknown codes and to a default for empty", () => {
    expect(codeLabel("SOME_NEW_CODE")).toBe("SOME_NEW_CODE");   // 未知码回落原串
    expect(codeLabel(null)).toBe("未知错误");
    expect(codeLabel(undefined)).toBe("未知错误");
  });
});

describe("run selectors", () => {
  it("bestRun picks max icOut among succeeded", () => {
    const runs = [run({ id: 1, metrics: { ...run({}).metrics!, icOut: 0.1 } }),
                  run({ id: 2, metrics: { ...run({}).metrics!, icOut: 0.4 } }),
                  run({ id: 3, status: "failed", metrics: null })];
    expect(bestRun(runs)?.id).toBe(2);
  });

  it("latestRun picks max id (incl non-succeeded)", () => {
    const runs = [run({ id: 1 }), run({ id: 5, status: "failed" })];
    expect(latestRun(runs)?.id).toBe(5);
  });

  it("bestRun returns null when none succeeded with icOut", () => {
    expect(bestRun([run({ status: "failed", metrics: null })])).toBeNull();
  });
});

describe("handoff construction", () => {
  it("buildTrainTarget carries full builder state + reuse ids", () => {
    const target = buildTrainTarget({
      members: MEMBERS, method: "linear", thresholds: DEFAULT_THRESHOLDS, horizon: 6,
      instId: "BTC-USDT-SWAP", bar: "1H", startTs: 10, endTs: 20, name: "组合A",
      modelId: 3, factorSetId: 7,
    });
    expect(target.config.normWindow).toBe(DEFAULT_THRESHOLDS.normWindow);
    expect(target.thresholds).toEqual(DEFAULT_THRESHOLDS);
    expect(target.modelId).toBe(3);
    expect(target.factorSetId).toBe(7);

    const body = trainStartBody(target);
    expect(body.instId).toBe("BTC-USDT-SWAP");
    expect(body.members).toBe(MEMBERS);
    expect(body.modelId).toBe(3);
  });

  it("buildTrainResult merges WS produce + target context for lossless round-trip", () => {
    const target = buildTrainTarget({
      members: MEMBERS, method: "linear", thresholds: DEFAULT_THRESHOLDS, horizon: 6,
      instId: "BTC-USDT-SWAP", bar: "1H", startTs: 10, endTs: 20, name: "组合A", factorSetId: 7,
    });
    const event: TrainResultEvent = {
      method: "linear", weights: { mom_roc: 0.6, rev_rsi: -0.4 }, normStats: { normWindow: 150, clip: 4 },
      metrics: { icIn: 0.3, icOut: 0.2, r2In: null, r2Out: null, nTrain: 100, nValid: 40 },
      trainerVersion: "sklearn 1.6", note: "", provenance: { instId: "BTC-USDT-SWAP", bar: "1H", startTs: 10, endTs: 20, horizon: 6 },
    };
    const result = buildTrainResult(event, target, { modelId: 9, runId: 4 });
    expect(result.members).toBe(MEMBERS);
    expect(result.name).toBe("组合A");
    expect(result.factorSetId).toBe(7);
    expect(result.weights).toEqual({ mom_roc: 0.6, rev_rsi: -0.4 });
    expect(result.modelId).toBe(9);

    // 回流 → factorTypes.TrainResult（builder 水合用）：normStats.normWindow 锁定。
    const trained = weightResultToTrained(result);
    expect(trained.available).toBe(true);
    expect(trained.normStats.normWindow).toBe(150);
    expect(trained.weights).toEqual(result.weights);
  });

  it("runToTrained returns null for non-succeeded run", () => {
    expect(runToTrained(run({ status: "failed", weights: {} }))).toBeNull();
    expect(runToTrained(run({}))?.weights).toEqual({ mom_roc: 1 });
  });

  // H-F5/H-F2：列表/详情发起训练用模型自身标的/周期（非工作台），时间窗仍取工作台区间，origin=model。
  it("modelToTrainTarget uses the model's own instId/bar and tags origin=model", () => {
    const m: WeightModel = {
      id: 5, name: "动量", instId: "SOL-USDT-SWAP", bar: "15m", members: MEMBERS, method: "linear",
      config: {}, horizon: 6, source: "manual", factorSetId: 7, latestRunId: null, bestValidIc: null,
      notes: "", enabled: true, latestRun: null, createdTs: 0, updatedTs: 0,
    };
    const target = modelToTrainTarget(m, { instId: "BTC-USDT-SWAP", bar: "1H", startTs: 100, endTs: 200 });
    expect(target.instId).toBe("SOL-USDT-SWAP");   // 模型自身标的（非工作台 BTC）
    expect(target.bar).toBe("15m");                // 模型自身周期（非工作台 1H）
    expect(target.startTs).toBe(100);              // 时间窗仍取工作台区间
    expect(target.endTs).toBe(200);
    expect(target.origin).toBe("model");
    expect(target.modelId).toBe(5);
    expect(target.factorSetId).toBe(7);
  });

  it("buildTrainTarget defaults origin to builder", () => {
    const target = buildTrainTarget({
      members: MEMBERS, method: "linear", thresholds: DEFAULT_THRESHOLDS, horizon: 6,
      instId: "BTC-USDT-SWAP", bar: "1H", startTs: 1, endTs: 2, name: "x",
    });
    expect(target.origin).toBe("builder");
  });
});

describe("backtest gating (weight-model-backtest-replay)", () => {
  const mk = (over: Partial<WeightModel> = {}): WeightModel => ({
    id: 1, name: "m", instId: "BTC-USDT-SWAP", bar: "1H", members: [], method: "linear",
    config: {}, horizon: 6, source: "manual", factorSetId: null, latestRunId: null,
    bestValidIc: null, notes: "", enabled: true, latestRun: null, createdTs: 0, updatedTs: 0, ...over,
  });

  it("canBacktestModel is true only when latestRunId is present", () => {
    expect(canBacktestModel(mk({ latestRunId: 7 }))).toBe(true);
    expect(canBacktestModel(mk({ latestRunId: null }))).toBe(false);
  });

  it("runIsBacktestable requires succeeded status and non-empty weights", () => {
    expect(runIsBacktestable(run({ status: "succeeded", weights: { mom_roc: 1 } }))).toBe(true);
    expect(runIsBacktestable(run({ status: "succeeded", weights: {} }))).toBe(false);
    expect(runIsBacktestable(run({ status: "failed", weights: { mom_roc: 1 } }))).toBe(false);
    expect(runIsBacktestable(run({ status: "cancelled", weights: { mom_roc: 1 } }))).toBe(false);
    expect(runIsBacktestable(run({ status: "running", weights: {} }))).toBe(false);
  });
});

describe("sortModelsByUpdated", () => {
  it("sorts by updatedTs desc", () => {
    const mk = (id: number, ts: number): WeightModel => ({
      id, name: `m${id}`, instId: "BTC-USDT-SWAP", bar: "1H", members: [], method: "linear",
      config: {}, horizon: 6, source: "manual", factorSetId: null, latestRunId: null,
      bestValidIc: null, notes: "", enabled: true, latestRun: null, createdTs: 0, updatedTs: ts,
    });
    const sorted = sortModelsByUpdated([mk(1, 10), mk(2, 30), mk(3, 20)]);
    expect(sorted.map((m) => m.id)).toEqual([2, 3, 1]);
  });
});
