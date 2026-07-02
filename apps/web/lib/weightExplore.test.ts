import { describe, expect, it } from "vitest";
import type { FactorMember, FactorThresholds } from "./factorTypes";
import { DEFAULT_THRESHOLDS } from "./factorTypes";
import type { CandidateState } from "./weightBatchTypes";
import {
  applyBestTarget,
  assembleMatrix,
  buildExploreBody,
  estimateCandidateCount,
  estimateComboCount,
  localPlan,
  planCombos,
  planLocalCombos,
} from "./weightExplore";

const M = (key: string, direction: "long" | "short" = "long"): FactorMember => ({
  factorKey: key,
  params: {},
  weight: 0,
  direction,
});
const POOL = [M("a"), M("b"), M("c")];

function keysOf(pool: FactorMember[], strategy: Parameters<typeof planCombos>[1], params: Parameters<typeof planCombos>[2]) {
  return planCombos(pool, strategy, params).map((c) => c.map((m) => m.factorKey));
}

describe("weightExplore.planCombos (与后端 plan_combinations 同口径, C-7)", () => {
  it("sizes 穷举 C(n,k) 与 itertools 顺序一致", () => {
    expect(keysOf(POOL, "sizes", { sizes: [2, 3] })).toEqual([
      ["a", "b"], ["a", "c"], ["b", "c"], ["a", "b", "c"],
    ]);
    expect(estimateComboCount(POOL, "sizes", { sizes: [2, 3] })).toBe(4);
  });

  it("topk_prefix 按 rankIc 降序取嵌套前缀", () => {
    expect(keysOf(POOL, "topk_prefix", { sizes: [1, 2, 3], rankIc: [0.1, 0.3, 0.2] })).toEqual([
      ["b"], ["b", "c"], ["b", "c", "a"],
    ]);
  });

  it("topk_prefix 缺 rankIc 回落池顺序（确定性、不报错）", () => {
    expect(keysOf(POOL, "topk_prefix", { sizes: [2] })).toEqual([["a", "b"]]);
  });

  it("leave_one_out = 全池 + 逐一去 1；|pool|==1 时仅留全池（不报错）", () => {
    expect(keysOf(POOL, "leave_one_out", {})).toEqual([
      ["a", "b", "c"], ["b", "c"], ["a", "c"], ["a", "b"],
    ]);
    expect(estimateComboCount([M("a")], "leave_one_out", {})).toBe(1);
  });

  it("manual 下标越界 / 空组合抛错；组合内下标去重；跨组合按成员集合去重", () => {
    expect(() => planCombos(POOL, "manual", { combos: [[0, 9]] })).toThrow();
    expect(() => planCombos(POOL, "manual", { combos: [[]] })).toThrow();
    expect(keysOf(POOL, "manual", { combos: [[2, 2]] })).toEqual([["c"]]);
    expect(keysOf(POOL, "manual", { combos: [[0, 1], [1, 0]] })).toEqual([["a", "b"]]);
  });

  it("estimateCandidateCount = 组合数 × 方法数", () => {
    expect(estimateCandidateCount(POOL, "sizes", { sizes: [2] }, ["linear", "xgboost"])).toBe(6);
  });
});

describe("weightExplore.planLocalCombos", () => {
  it("按顺序赋稳定 id C0/C1…（与后端 variant_label 锁步），label 由 labelOf 映射", () => {
    const combos = planLocalCombos(POOL, "sizes", { sizes: [2] }, (m) => m.factorKey.toUpperCase());
    expect(combos.map((c) => c.id)).toEqual(["C0", "C1", "C2"]);
    expect(combos[0].label).toBe("A · B");
    expect(combos[0].members.map((m) => m.factorKey)).toEqual(["a", "b"]);
  });
});

describe("weightExplore.buildExploreBody", () => {
  it("携 pool/strategy/params/methods + config.normWindow + 默认 origin=builder", () => {
    const body = buildExploreBody({
      pool: POOL, strategy: "sizes", params: { sizes: [2] }, methods: ["linear"],
      thresholds: { ...DEFAULT_THRESHOLDS, normWindow: 150 }, horizon: 6,
      instId: "BTC-USDT-SWAP", bar: "1H", startTs: 10, endTs: 20,
    });
    expect(body.pool).toBe(POOL);
    expect(body.methods).toEqual(["linear"]);
    expect(body.origin).toBe("builder");
    expect(body.config).toEqual({ normWindow: 150, thresholds: { ...DEFAULT_THRESHOLDS, normWindow: 150 } });
  });
});

function candidate(runId: number, variantLabel: string, method: "linear" | "xgboost", icOut: number | null): CandidateState {
  return {
    runId, method, variantLabel, status: icOut == null ? "queued" : "succeeded", phase: null, curve: [],
    liveWeights: null, result: null,
    metrics: icOut == null ? null : { icIn: 0, icOut, r2In: 0, r2Out: 0, nTrain: 1, nValid: 1 },
    error: null,
  };
}

describe("weightExplore.assembleMatrix", () => {
  it("按 (comboId, method) 落格；缺失候选为 null", () => {
    const combos = planLocalCombos(POOL, "sizes", { sizes: [2] });
    const plan = localPlan(combos, ["linear", "xgboost"], ["a", "b", "c"], "sizes", { sizes: [2] });
    const states = [candidate(1, "C0", "linear", 0.08), candidate(2, "C0", "xgboost", 0.05)];
    const matrix = assembleMatrix(plan, states);
    expect(matrix.rows.map((r) => r.comboId)).toEqual(["C0", "C1", "C2"]);
    expect(matrix.cols).toEqual(["linear", "xgboost"]);
    expect(matrix.cells["C0:linear"]?.runId).toBe(1);
    expect(matrix.cells["C0:xgboost"]?.runId).toBe(2);
    expect(matrix.cells["C1:linear"]).toBeNull();
  });
});

describe("weightExplore.applyBestTarget", () => {
  it("用胜出子集成员 + origin:builder + config.normWindow=共享值", () => {
    const thresholds: FactorThresholds = { ...DEFAULT_THRESHOLDS, normWindow: 180 };
    const target = applyBestTarget(
      { members: [M("a"), M("b")], method: "xgboost" },
      { thresholds, name: "组合X", instId: "BTC-USDT-SWAP", bar: "1H", startTs: 1, endTs: 2, horizon: 6, sharedNormWindow: 180 },
    );
    expect(target.members.map((m) => m.factorKey)).toEqual(["a", "b"]);
    expect(target.method).toBe("xgboost");
    expect(target.origin).toBe("builder");
    expect(target.config.normWindow).toBe(180);
  });
});
