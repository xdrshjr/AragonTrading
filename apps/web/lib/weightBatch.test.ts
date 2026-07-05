import { describe, expect, it } from "vitest";
import { DEFAULT_THRESHOLDS } from "./factorTypes";
import type { TrainMetrics } from "./factorTypes";
import {
  ALL_METHODS,
  DEFAULT_BATCH_METHODS,
  allSettled,
  applyCandidateEvent,
  batchDerivedStatus,
  batchStartBody,
  bestRunId,
  buildBatchTarget,
  emptyCandidate,
  methodLabel,
  rankCandidates,
} from "./weightBatch";
import type { CandidateState } from "./weightBatchTypes";

function metrics(icOut: number | null, icIn = 0.1, nTrain = 100): TrainMetrics {
  return { icIn, icOut, r2In: 0, r2Out: 0, nTrain, nValid: 20 };
}

function candidate(runId: number, patch: Partial<CandidateState> = {}): CandidateState {
  return {
    ...emptyCandidate({ runId, method: "linear", variantLabel: "", status: "queued" }),
    ...patch,
  };
}

describe("ALL_METHODS / DEFAULT_BATCH_METHODS（weight-model-nonlinear-trainers）", () => {
  it("ALL_METHODS 含 11 款且每款有本地化标签（无回落原始 key）", () => {
    expect(ALL_METHODS).toHaveLength(11);
    for (const m of ALL_METHODS) {
      expect(methodLabel(m)).toBeTruthy();
      expect(methodLabel(m)).not.toBe(m);
    }
  });

  it("ALL_METHODS 含全部 6 款顶级 · 含非线性新方法", () => {
    for (const m of ["extra_trees", "mlp", "kernel_ridge", "knn", "mutual_info", "elastic_net"] as const) {
      expect(ALL_METHODS).toContain(m);
    }
  });

  it("DEFAULT_BATCH_METHODS 示例取较快的 extra_trees（不含 O(n³) 的 kernel_ridge）", () => {
    expect(DEFAULT_BATCH_METHODS).toContain("extra_trees");
    expect(DEFAULT_BATCH_METHODS).not.toContain("kernel_ridge");
    for (const m of DEFAULT_BATCH_METHODS) expect(ALL_METHODS).toContain(m);
  });
});

describe("applyCandidateEvent — 单候选内部事件归约", () => {
  it("run → running；phase → 记录 phase", () => {
    let s = candidate(1);
    s = applyCandidateEvent(s, { type: "run", data: { runId: 1, modelId: 9, status: "running" } });
    expect(s.status).toBe("running");
    s = applyCandidateEvent(s, { type: "phase", data: { phase: "fitting", pct: 40 } });
    expect(s.phase).toEqual({ phase: "fitting", pct: 40 });
  });

  it("result → 记录 metrics/liveWeights；done → succeeded", () => {
    let s = candidate(1, { status: "running" });
    s = applyCandidateEvent(s, {
      type: "result",
      data: {
        method: "linear", weights: { mom_roc: 1 }, normStats: { normWindow: 200, clip: 4 },
        metrics: metrics(0.2), trainerVersion: null, note: "",
        provenance: { instId: "BTC-USDT-SWAP", bar: "1H", startTs: 1, endTs: 2, horizon: 6 },
      },
    });
    expect(s.metrics?.icOut).toBe(0.2);
    expect(s.liveWeights).toEqual({ mom_roc: 1 });
    s = applyCandidateEvent(s, { type: "done", data: { runId: 1, finishedTs: 9 } });
    expect(s.status).toBe("succeeded");
  });

  it("error → failed + 错误；cancelled → cancelled", () => {
    let s = candidate(1, { status: "running" });
    s = applyCandidateEvent(s, { type: "error", data: { code: "X", message: "boom", runId: 1 } });
    expect(s.status).toBe("failed");
    expect(s.error).toEqual({ code: "X", message: "boom" });
    let c = candidate(2, { status: "running" });
    c = applyCandidateEvent(c, { type: "cancelled", data: { runId: 2, finishedTs: 9 } });
    expect(c.status).toBe("cancelled");
  });
});

describe("rankCandidates — 排行榜派生（与后端同口径）", () => {
  it("succeeded 按 icOut 降序，仅首个置 isBest；非成功殿后", () => {
    const states = [
      candidate(1, { status: "succeeded", metrics: metrics(0.05) }),
      candidate(2, { status: "succeeded", metrics: metrics(0.2) }),
      candidate(3, { status: "failed" }),
      candidate(4, { status: "succeeded", metrics: metrics(0.1) }),
    ];
    const ranking = rankCandidates(states);
    expect(ranking.map((r) => r.runId)).toEqual([2, 4, 1, 3]);   // 0.2 > 0.1 > 0.05 > failed
    expect(ranking[0].isBest).toBe(true);
    expect(ranking.slice(1).every((r) => !r.isBest)).toBe(true);
  });

  it("tie-break：icOut 相同看 icIn 降序", () => {
    const states = [
      candidate(1, { status: "succeeded", metrics: metrics(0.1, 0.3) }),
      candidate(2, { status: "succeeded", metrics: metrics(0.1, 0.5) }),
    ];
    expect(rankCandidates(states).map((r) => r.runId)).toEqual([2, 1]);
  });

  it("无成功候选 → 全无 isBest", () => {
    const states = [candidate(1, { status: "failed" }), candidate(2, { status: "cancelled" })];
    expect(rankCandidates(states).some((r) => r.isBest)).toBe(false);
    expect(bestRunId(states)).toBeNull();
  });
});

describe("batchDerivedStatus / allSettled", () => {
  it("有 queued/running → running", () => {
    expect(batchDerivedStatus([candidate(1, { status: "running" }), candidate(2, { status: "succeeded", metrics: metrics(0.1) })])).toBe("running");
    expect(batchDerivedStatus([candidate(1, { status: "queued" })])).toBe("running");
  });
  it("全终态且有 succeeded → succeeded", () => {
    expect(batchDerivedStatus([candidate(1, { status: "succeeded", metrics: metrics(0.1) }), candidate(2, { status: "failed" })])).toBe("succeeded");
  });
  it("无 succeeded 有 cancelled → cancelled；全 failed → failed", () => {
    expect(batchDerivedStatus([candidate(1, { status: "failed" }), candidate(2, { status: "cancelled" })])).toBe("cancelled");
    expect(batchDerivedStatus([candidate(1, { status: "failed" })])).toBe("failed");
  });
  it("allSettled：全终态才为真", () => {
    expect(allSettled([candidate(1, { status: "succeeded", metrics: metrics(0.1) }), candidate(2, { status: "failed" })])).toBe(true);
    expect(allSettled([candidate(1, { status: "running" })])).toBe(false);
    expect(allSettled([])).toBe(false);
  });
});

describe("target / body 构造", () => {
  const base = {
    members: [{ factorKey: "mom_roc", params: {}, weight: 0, direction: "long" as const }],
    thresholds: { ...DEFAULT_THRESHOLDS, normWindow: 150 },
    horizon: 6, instId: "BTC-USDT-SWAP", bar: "1H", startTs: 1, endTs: 2, name: "对比",
  };

  it("buildBatchTarget：origin 缺省 builder；methods/factorSetId 条件写入", () => {
    const t = buildBatchTarget({ ...base, methods: ["linear", "xgboost"], factorSetId: 12 });
    expect(t.origin).toBe("builder");
    expect(t.methods).toEqual(["linear", "xgboost"]);
    expect(t.factorSetId).toBe(12);
    const t2 = buildBatchTarget({ ...base, origin: "model" });
    expect(t2.origin).toBe("model");
    expect(t2.methods).toBeUndefined();
    expect(t2.factorSetId).toBeUndefined();
  });

  it("batchStartBody：config 携 normWindow + thresholds；候选透传；origin/name/factorSetId 写入", () => {
    const t = buildBatchTarget({ ...base, factorSetId: 12, origin: "builder" });
    const body = batchStartBody(t, [{ method: "linear" }, { method: "xgboost", variantLabel: "深" }]);
    expect(body.instId).toBe("BTC-USDT-SWAP");
    expect(body.config).toEqual({ normWindow: 150, thresholds: t.thresholds });
    expect(body.candidates).toEqual([{ method: "linear" }, { method: "xgboost", variantLabel: "深" }]);
    expect(body.origin).toBe("builder");
    expect(body.name).toBe("对比");
    expect(body.factorSetId).toBe(12);
  });
});
