import { describe, expect, it } from "vitest";
import type { FactorMember } from "./factorTypes";
import type { CandidateState } from "./weightBatchTypes";
import type { LocalCombo } from "./weightExplore";
import { computeInsights, suggestRefinement } from "./exploreInsights";

const M = (key: string): FactorMember => ({ factorKey: key, params: {}, weight: 0, direction: "long" });

function combo(id: string, keys: string[]): LocalCombo {
  return { id, label: keys.join(" · "), members: keys.map(M) };
}

// 成功候选态：icOut 驱动归因（candidateIcOut 读 metrics.icOut），weights 供平均权重列。
function cand(
  runId: number,
  variantLabel: string,
  icOut: number | null,
  weights: Record<string, number> | null = null,
  status: CandidateState["status"] = "succeeded",
): CandidateState {
  const metrics = icOut == null ? null : { icIn: 0, icOut, r2In: 0, r2Out: 0, nTrain: 1, nValid: 1 };
  return {
    runId,
    method: "linear",
    variantLabel,
    status,
    phase: null,
    curve: [],
    liveWeights: null,
    result:
      icOut == null
        ? null
        : {
            method: "linear",
            weights: weights ?? {},
            normStats: { normWindow: 200, clip: 4 },
            metrics: metrics!,
            trainerVersion: null,
            note: "",
            provenance: { instId: "X", bar: "1H", startTs: 0, endTs: 1, horizon: 6 },
          },
    metrics,
    error: null,
  };
}

describe("computeInsights · 因子边际贡献", () => {
  const combos = [combo("C0", ["a", "b"]), combo("C1", ["a", "c"]), combo("C2", ["b", "c"])];
  const states = [cand(1, "C0", 0.1), cand(2, "C1", 0.2), cand(3, "C2", 0.06)];

  it("marginalLift = meanIcWith − meanIcWithout，并按边际提升降序", () => {
    const insights = computeInsights(states, combos);
    const byKey = Object.fromEntries(insights.factors.map((f) => [f.factorKey, f]));
    // a: with (0.1,0.2)=0.15, without (0.06) → +0.09；c: with (0.2,0.06)=0.13, without (0.1) → +0.03；b: -0.12
    expect(byKey.a.marginalLift).toBeCloseTo(0.09, 6);
    expect(byKey.c.marginalLift).toBeCloseTo(0.03, 6);
    expect(byKey.b.marginalLift).toBeCloseTo(-0.12, 6);
    expect(insights.factors.map((f) => f.factorKey)).toEqual(["a", "c", "b"]); // 降序
    expect(insights.hasEnoughSamples).toBe(true);
    expect(insights.succeededCount).toBe(3);
  });

  it("恒在因子（出现在所有成功候选）marginalLift=null 而非 0", () => {
    const alwaysA = [combo("C0", ["a", "b"]), combo("C1", ["a", "c"])];
    const insights = computeInsights([cand(1, "C0", 0.1), cand(2, "C1", 0.2)], alwaysA);
    const a = insights.factors.find((f) => f.factorKey === "a")!;
    expect(a.absentCount).toBe(0);
    expect(a.marginalLift).toBeNull();
    expect(a.meanIcWithout).toBeNull();
  });

  it("无成功候选 → hasEnoughSamples=false 且各因子 meanIcWith=null", () => {
    const insights = computeInsights([cand(1, "C0", null, null, "failed"), cand(2, "C1", null, null, "failed")], combos);
    expect(insights.succeededCount).toBe(0);
    expect(insights.hasEnoughSamples).toBe(false);
    expect(insights.factors.length).toBeGreaterThan(0);
    expect(insights.factors.every((f) => f.meanIcWith == null)).toBe(true);
  });
});

describe("computeInsights · 前四分位命中 / 平均权重 / 大小分桶", () => {
  it("topHitRate 用线性插值 75 分位（≥4 样本）", () => {
    const combos = [combo("C0", ["x", "a"]), combo("C1", ["x", "b"]), combo("C2", ["x", "c"]), combo("C3", ["x", "d"])];
    const states = [cand(1, "C0", 0.1), cand(2, "C1", 0.2), cand(3, "C2", 0.3), cand(4, "C3", 0.4)];
    const insights = computeInsights(states, combos);
    const x = insights.factors.find((f) => f.factorKey === "x")!;
    // q75 = 0.325，仅 0.4 命中 → 1/4
    expect(x.topHitRate).toBeCloseTo(0.25, 6);
  });

  it("样本 <4 时前四分位退化为「≥中位数」", () => {
    const combos = [combo("C0", ["x", "a"]), combo("C1", ["x", "b"]), combo("C2", ["x", "c"])];
    const states = [cand(1, "C0", 0.1), cand(2, "C1", 0.2), cand(3, "C2", 0.3)];
    const x = computeInsights(states, combos).factors.find((f) => f.factorKey === "x")!;
    // 中位数=0.2，命中 {0.2,0.3} → 2/3
    expect(x.topHitRate).toBeCloseTo(2 / 3, 6);
  });

  it("meanAbsWeight 缺权重键跳过样本、全缺 → null", () => {
    const combos = [combo("C0", ["x", "a"]), combo("C1", ["x", "b"]), combo("C2", ["x", "c"])];
    const states = [
      cand(1, "C0", 0.1, { x: 0.6, a: 0.4 }),
      cand(2, "C1", 0.2, { x: -0.2, b: 0.8 }),
      cand(3, "C2", 0.3, {}), // 等权/IC 加权：无逐因子权重
    ];
    const insights = computeInsights(states, combos);
    const x = insights.factors.find((f) => f.factorKey === "x")!;
    const c = insights.factors.find((f) => f.factorKey === "c")!;
    expect(x.meanAbsWeight).toBeCloseTo((0.6 + 0.2) / 2, 6); // C2 缺 x 键 → 跳过
    expect(c.meanAbsWeight).toBeNull(); // c 仅在 C2 且权重表空 → 全缺
  });

  it("sizes 分桶按成员数聚合均值 / 最优", () => {
    const combos = [combo("C0", ["a", "b"]), combo("C1", ["a", "b", "c"]), combo("C2", ["a", "c"])];
    const states = [cand(1, "C0", 0.1), cand(2, "C1", 0.3), cand(3, "C2", 0.2)];
    const sizes = computeInsights(states, combos).sizes;
    expect(sizes).toEqual([
      { size: 2, count: 2, meanIc: 0.15000000000000002, bestIc: 0.2 },
      { size: 3, count: 1, meanIc: 0.3, bestIc: 0.3 },
    ]);
  });
});

describe("suggestRefinement · 引导精修", () => {
  const combos = [combo("C0", ["a", "b"]), combo("C1", ["a", "c"]), combo("C2", ["b", "c"])];
  const states = [cand(1, "C0", 0.1), cand(2, "C1", 0.2), cand(3, "C2", 0.06)];
  const pool = [M("a"), M("b"), M("c")];
  const insights = computeInsights(states, combos);

  it("以最佳组合为锚生成去重、界内的 manual 组合与等长理由", () => {
    // 最佳=C1=[a,c]（icOut 0.2）→ B=[0,2]。
    const sug = suggestRefinement(insights, pool, [M("a"), M("c")], "linear");
    expect(sug.strategy).toBe("manual");
    expect(sug.methods).toEqual(["linear"]);
    expect(sug.pool).toBe(pool);
    expect(sug.rationale.length).toBe(sug.combos.length);
    for (const c of sug.combos) {
      expect(c.length).toBeGreaterThanOrEqual(2);
      expect(c.length).toBeLessThanOrEqual(3);
      for (const i of c) expect(i).toBeGreaterThanOrEqual(0), expect(i).toBeLessThan(pool.length);
    }
    // 去重：无两个组合是同一成员集合。
    const keys = sug.combos.map((c) => [...c].sort().join(","));
    expect(new Set(keys).size).toBe(keys.length);
    expect(keys).toContain("0,2"); // B 本身
    expect(sug.truncated).toBe(false);
  });

  it("combos×methods 超 cap 时截断并置 truncated=true", () => {
    const sug = suggestRefinement(insights, pool, [M("a"), M("c")], "linear", 1);
    expect(sug.combos.length).toBe(1);
    expect(sug.truncated).toBe(true);
  });

  it("样本不足时只回「B 本身」的安全建议", () => {
    const weak = computeInsights([cand(1, "C0", 0.1)], [combo("C0", ["a", "b"])]);
    expect(weak.hasEnoughSamples).toBe(false);
    const sug = suggestRefinement(weak, [M("a"), M("b")], [M("a"), M("b")], "linear");
    expect(sug.combos).toEqual([[0, 1]]);
    expect(sug.rationale.length).toBe(1);
    expect(sug.truncated).toBe(false);
  });
});
