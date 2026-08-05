import { describe, expect, it } from "vitest";
import {
  CANDIDATES_CAP,
  ITERATIONS_CAP,
  applyDiscoveryEvent,
  countdownSeconds,
  coverageEntries,
  emptyRunState,
  formatDuration,
  funnelOf,
  keptRate,
  keptRateSeries,
  pauseReasonCn,
  rejectReasonCn,
  sourceBreakdown,
  topRejectReasons,
  windowDays,
  workloadOf,
} from "./discovery";
import type {
  DiscoveryCandidateRow,
  DiscoveryIterationRow,
  DiscoveryStateEvent,
} from "./discoveryTypes";

function stateEvent(over: Partial<DiscoveryStateEvent> = {}): DiscoveryStateEvent {
  return {
    runId: 1,
    status: "running",
    pauseReason: "",
    iteration: 7,
    counters: { tested: 120, kept: 4, rejected: { F3: 40, F10: 12 } },
    budget: { mutate: 0.3, crossover: 0.25, perturb: 0.15, random: 0.1, llm: 0.2 },
    poolSizes: { factor: 4 },
    mechanismCoverage: { gap_premium: 3, shadow_pressure: 1 },
    fsaBans: [{ skeleton: "sub(?,?)", share: 0.19, bannedAtIter: 25 }],
    nextIterationTs: 1_700_000_300,
    dataWindow: { startTs: 1, endTs: 2, lastRefreshTs: 3 },
    llmUsage: { callsThisHour: 5, limit: 24, sinceProcessStart: true, enabled: true },
    rebuilding: false,
    keptCount: 4,
    testedCount: 120,
    ...over,
  };
}

function iterationRow(over: Partial<DiscoveryIterationRow> = {}): DiscoveryIterationRow {
  return {
    index: 1,
    startedTs: 1_700_000_000,
    finishedTs: 1_700_000_030,
    budget: { mutate: 0.25, crossover: 0.25, perturb: 0.15, random: 0.15, llm: 0.2 },
    generated: 40,
    ruleRejected: 12,
    llmRejected: 3,
    validated: 25,
    kept: 2,
    rejectedByReason: { F3: 10, F10: 8, duplicate: 5 },
    bySource: { mutate: 10, crossover: 10, perturb: 6, random: 6, llm: 8 },
    truncatedByTimeout: 0,
    overrun: false,
    skipped: "",
    durationMs: 30_000,
    llm: { calls: 1, latencyMs: 900, degraded: false, degradedReason: "", promptChars: 1200 },
    note: "生成 40／审出 25／入库 2",
    ...over,
  };
}

function candidateRow(over: Partial<DiscoveryCandidateRow> = {}): DiscoveryCandidateRow {
  return {
    kind: "factor",
    genomeHash: "abc123",
    exprText: "ts_mean(ret,20)",
    describe: "20 根收益率均值",
    mechanism: "trend_persistence",
    source: "random",
    verdict: "kept",
    rejectReason: "",
    createdTs: 1_700_000_000,
    metrics: {},
    ...over,
  };
}

describe("applyDiscoveryEvent — WS reducer", () => {
  it("returns null when there is no run state yet", () => {
    expect(applyDiscoveryEvent(null, { type: "state", data: stateEvent() })).toBeNull();
  });

  it("字段往返：DiscoveryRunState 的每个后端键都能从 WS state 事件穿透到 store", () => {
    // 这是防「逐键手抄漏字段」的门禁（R-11）：后端 state 事件的每一个键都必须落到内存态上，
    // 忘了接线就在这里红，而不是等用户发现「后端算了、界面没有」。
    const next = applyDiscoveryEvent(emptyRunState(1), { type: "state", data: stateEvent() })!;
    const event = stateEvent();
    for (const key of Object.keys(event) as (keyof DiscoveryStateEvent)[]) {
      expect(next[key as keyof typeof next]).toEqual(event[key]);
    }
    expect(next.connected).toBe(true);
  });

  it("整对象合并：后端新增的未知字段也会穿透（不逐键手抄）", () => {
    const withNewKey = { ...stateEvent(), brandNewMetric: 42 } as unknown as DiscoveryStateEvent;
    const next = applyDiscoveryEvent(emptyRunState(1), { type: "state", data: withNewKey })!;
    expect((next as unknown as Record<string, unknown>).brandNewMetric).toBe(42);
  });

  it("appends iterations and candidates", () => {
    let state = emptyRunState(1);
    state = applyDiscoveryEvent(state, { type: "iteration", data: iterationRow() })!;
    state = applyDiscoveryEvent(state, { type: "candidate", data: candidateRow() })!;
    expect(state.iterations).toHaveLength(1);
    expect(state.candidates).toHaveLength(1);
  });

  it("重复 / 乱序事件幂等：同 index 的迭代与同 hash 的候选只保留一条", () => {
    let state = emptyRunState(1);
    state = applyDiscoveryEvent(state, { type: "iteration", data: iterationRow({ kept: 1 }) })!;
    state = applyDiscoveryEvent(state, { type: "iteration", data: iterationRow({ kept: 5 }) })!;
    expect(state.iterations).toHaveLength(1);
    expect(state.iterations[0].kept).toBe(5);

    state = applyDiscoveryEvent(state, { type: "candidate", data: candidateRow() })!;
    state = applyDiscoveryEvent(state, {
      type: "candidate",
      data: candidateRow({ verdict: "rejected", rejectReason: "F3" }),
    })!;
    expect(state.candidates).toHaveLength(1);
    expect(state.candidates[0].verdict).toBe("rejected");
  });

  it("环形上限：超出即丢最旧（长跑内存不涨，DoD #7）", () => {
    let state = emptyRunState(1);
    for (let i = 0; i < ITERATIONS_CAP + 50; i += 1) {
      state = applyDiscoveryEvent(state, { type: "iteration", data: iterationRow({ index: i }) })!;
    }
    expect(state.iterations).toHaveLength(ITERATIONS_CAP);
    expect(state.iterations[0].index).toBe(50);

    for (let i = 0; i < CANDIDATES_CAP + 30; i += 1) {
      state = applyDiscoveryEvent(state, {
        type: "candidate",
        data: candidateRow({ genomeHash: `h${i}` }),
      })!;
    }
    expect(state.candidates).toHaveLength(CANDIDATES_CAP);
  });

  it("marks a candidate as demoted", () => {
    let state = emptyRunState(1);
    state = applyDiscoveryEvent(state, { type: "candidate", data: candidateRow({ id: 9 }) })!;
    state = applyDiscoveryEvent(state, {
      type: "demoted",
      data: { candidateId: 9, reason: "F6" },
    })!;
    expect(state.candidates[0].demoted).toBe(true);
  });

  it("error 事件不终止流，只记录最近一次错误", () => {
    const state = applyDiscoveryEvent(emptyRunState(1), {
      type: "error",
      data: { code: "DISCOVERY_DATA_UNAVAILABLE", message: "行情取数失败" },
    })!;
    expect(state.lastError?.code).toBe("DISCOVERY_DATA_UNAVAILABLE");
    expect(state.status).toBe("running");
  });

  it("done 事件落终态并清掉倒计时（绝不留一个永远在跳的下一轮）", () => {
    let state = applyDiscoveryEvent(emptyRunState(1), { type: "state", data: stateEvent() })!;
    expect(state.nextIterationTs).not.toBeNull();
    state = applyDiscoveryEvent(state, { type: "done", data: { status: "stopped" } })!;
    expect(state.status).toBe("stopped");
    expect(state.nextIterationTs).toBeNull();
  });

  it("ping 与未知事件不改变状态", () => {
    const base = applyDiscoveryEvent(emptyRunState(1), { type: "state", data: stateEvent() })!;
    expect(applyDiscoveryEvent(base, { type: "ping" })).toBe(base);
  });
});

describe("派生函数", () => {
  it("漏斗逐段数字守恒", () => {
    const stages = funnelOf(iterationRow());
    expect(stages.map((s) => s.value)).toEqual([40, 25, 25, 2]);
    expect(funnelOf(null).every((s) => s.value === 0)).toBe(true);
  });

  it("拒绝原因 Top N 降序且中文化", () => {
    const top = topRejectReasons(iterationRow().rejectedByReason, 2);
    expect(top.map((item) => item.code)).toEqual(["F3", "F10"]);
    expect(top[0].label).toContain("IR");
    expect(topRejectReasons(undefined)).toEqual([]);
  });

  it("来源分布补齐缺席来源为 0（柱宽稳定）", () => {
    const parts = sourceBreakdown(iterationRow({ bySource: { mutate: 3 } }));
    expect(parts.find((p) => p.key === "llm")?.value).toBe(0);
    expect(parts.find((p) => p.key === "mutate")?.value).toBe(3);
  });

  it("入库率在样本不足时返回 null（诚实优先于漂亮，绝不外推）", () => {
    expect(keptRate(3, 1)).toBeNull();
    expect(keptRate(100, 5)).toBeCloseTo(0.05);
    expect(keptRate(Number.NaN, 1)).toBeNull();
  });

  it("入库率序列对零验证轮给 null 断点而不是 0", () => {
    const series = keptRateSeries([iterationRow(), iterationRow({ index: 2, validated: 0, kept: 0 })]);
    expect(series[0]).toBeCloseTo(2 / 25);
    expect(series[1]).toBeNull();
  });

  it("机制覆盖按覆盖数降序，0 覆盖沉底", () => {
    const entries = coverageEntries({ a: 0, b: 5, c: 2 });
    expect(entries.map((e) => e.key)).toEqual(["b", "c", "a"]);
  });

  it("倒计时：已过点或缺失返回 null（UI 显示「即将开始」而不是负数）", () => {
    expect(countdownSeconds(null)).toBeNull();
    expect(countdownSeconds(1_700_000_000, 1_700_000_500_000)).toBeNull();
    expect(countdownSeconds(1_700_000_300, 1_700_000_000_000)).toBe(300);
  });

  it("工作量与窗口天数与后端同口径", () => {
    expect(workloadOf(40, 4000)).toBe(160_000);
    expect(workloadOf(-1, 100)).toBe(0);
    expect(windowDays("1H", 24)).toBeCloseTo(1);
    expect(windowDays("1D", 30)).toBeCloseTo(30);
  });

  it("时长格式化", () => {
    expect(formatDuration(45)).toBe("45 秒");
    expect(formatDuration(3 * 3600 + 120)).toBe("3 小时 2 分");
    expect(formatDuration(2 * 86400 + 3600)).toBe("2 天 1 小时");
    expect(formatDuration(-1)).toBe("—");
  });
});

describe("中文映射", () => {
  it("11 条闸与审查规则都有中文说明", () => {
    for (const code of ["F1", "F6", "F8", "F9", "F10", "F11", "duplicate", "fsa_banned"]) {
      expect(rejectReasonCn(code)).not.toBe(code);
    }
  });

  it("未登记码原样返回，绝不吞成空白", () => {
    expect(rejectReasonCn("SOMETHING_NEW")).toBe("SOMETHING_NEW");
    expect(rejectReasonCn("")).toBe("");
  });

  it("带参数的复合码被正确拆解", () => {
    expect(rejectReasonCn("llm_review:量纲不可比")).toContain("量纲不可比");
    expect(rejectReasonCn("eval_error:ValueError")).toContain("ValueError");
  });

  it("暂停原因中文化（UI 据此给不同的下一步引导）", () => {
    expect(pauseReasonCn("disabled_by_config")).toContain("总闸");
    expect(pauseReasonCn("data_unavailable")).toContain("行情");
    expect(pauseReasonCn("")).toBe("");
  });
});
