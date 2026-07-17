// 前端观测口径镜像用例（ai-trade-hold-inertia-observability §六-6）：与后端
// session._compute_ai_stats / _REFLECTION_FAIL_CLOSED_REASONS 逐字镜像的口径钉——四码集合字面量
// 断言 + R2-02 分支位置双钉（fail-closed 轮计入 runs+failClosed；verdict=null 合成样本仍计
// failClosed 不计 runs），防双侧静默漂移。
import { describe, expect, it } from "vitest";
import type { AiTradeHistoryMember } from "./aiTradeHistoryTypes";
import {
  REFLECTION_FAIL_CLOSED_REASONS,
  aggregateAiObservability,
  computeReflectionCounts,
  latestHoldStreak,
} from "./aiTradeObservability";
import type { AiTradeDecision } from "./aiTradeTypes";

function decision(overrides: Partial<AiTradeDecision> = {}): AiTradeDecision {
  return {
    ts: 1,
    round: 1,
    action: "hold",
    reason: "r",
    signalBoard: [],
    ...overrides,
  };
}

function member(
  aiStats?: NonNullable<AiTradeHistoryMember["metrics"]>["aiStats"],
): AiTradeHistoryMember {
  return {
    runId: 1,
    instId: "BTC-USDT-SWAP",
    leverage: 3,
    budgetUsd: 100,
    allocationPct: 100,
    strategyNames: ["动量"],
    status: "stopped",
    metrics: aiStats ? { aiStats } : {},
    trades: [],
  };
}

describe("REFLECTION_FAIL_CLOSED_REASONS", () => {
  it("mirrors the backend four-code frozenset literally", () => {
    // 与后端 session._REFLECTION_FAIL_CLOSED_REASONS **逐字**相等（字面量断言，防双侧漂移）。
    expect([...REFLECTION_FAIL_CLOSED_REASONS].sort()).toEqual([
      "reflection_invalid",
      "reflection_llm_error",
      "reflection_no_tool",
      "reflection_timeout",
    ]);
  });
});

describe("computeReflectionCounts", () => {
  it("splits genuine aborts from fail-closed and counts confirms (R-04)", () => {
    const counts = computeReflectionCounts([
      decision({ reflectionVerdict: "confirm", degradeReason: null }),
      decision({ reflectionVerdict: "abort", degradeReason: "reflection_abort" }),
      decision({ reflectionVerdict: "abort", degradeReason: "reflection_llm_error" }),
      decision({ reflectionVerdict: null, degradeReason: null }),
      decision({ degradeReason: "low_consensus(0/1)" }), // 无 verdict 键：不计 runs、非 fail-closed 码
    ]);
    expect(counts).toEqual({ runs: 3, confirms: 1, aborts: 1, failClosed: 1 });
  });

  it("counts a real fail-closed round into runs AND failClosed but never aborts (R2-02 pin 1)", () => {
    // 今日真实 fail-closed 轮形状：decider._fail_closed 构造 verdict="abort" + 基础设施码。
    const counts = computeReflectionCounts([
      decision({ reflectionVerdict: "abort", degradeReason: "reflection_timeout" }),
    ]);
    expect(counts).toEqual({ runs: 1, confirms: 0, aborts: 0, failClosed: 1 });
  });

  it("counts failClosed independently of verdict presence (R2-02 pin 2)", () => {
    // 合成样本：verdict=null 但 degradeReason 为 fail-closed 码 → failClosed+1 且 runs 不计
    // （镜像后端 failClosed 判定位于 `if verdict is not None` 块之外）。
    const counts = computeReflectionCounts([
      decision({ reflectionVerdict: null, degradeReason: "reflection_no_tool" }),
    ]);
    expect(counts).toEqual({ runs: 0, confirms: 0, aborts: 0, failClosed: 1 });
  });

  it("returns zeros on empty input and does not mutate its argument", () => {
    expect(computeReflectionCounts([])).toEqual({ runs: 0, confirms: 0, aborts: 0, failClosed: 0 });
    const input = [decision({ reflectionVerdict: "confirm" })];
    const frozen = JSON.stringify(input);
    computeReflectionCounts(input);
    expect(JSON.stringify(input)).toBe(frozen);
  });
});

describe("aggregateAiObservability", () => {
  it("sums the five counters across members and takes the max hold streak", () => {
    const agg = aggregateAiObservability([
      member({
        decisionRounds: 10, actionCounts: {}, holdRate: 0.5, avgConfidence: 0.6,
        degradedRounds: 0, reflectionRuns: 3, reflectionConfirms: 2, reflectionAborts: 1,
        reflectionFailClosed: 0, wakeSkips: 5, maxHoldStreak: 12,
      }),
      member({
        decisionRounds: 8, actionCounts: {}, holdRate: 0.4, avgConfidence: 0.7,
        degradedRounds: 1, reflectionRuns: 1, reflectionConfirms: 0, reflectionAborts: 0,
        reflectionFailClosed: 1, wakeSkips: 2, maxHoldStreak: 4,
      }),
    ]);
    expect(agg).toEqual({
      reflectionRuns: 4, reflectionConfirms: 2, reflectionAborts: 1,
      reflectionFailClosed: 1, wakeSkips: 7, maxHoldStreak: 12,
    });
  });

  it("treats missing keys as 0 while any present key keeps the row visible", () => {
    const agg = aggregateAiObservability([
      member({
        decisionRounds: 10, actionCounts: {}, holdRate: 0.5, avgConfidence: 0.6,
        degradedRounds: 0, wakeSkips: 3, // 仅带一个新键（部分老后端形状）
      }),
      member(), // 无 aiStats 成员
    ]);
    expect(agg).toEqual({
      reflectionRuns: 0, reflectionConfirms: 0, reflectionAborts: 0,
      reflectionFailClosed: 0, wakeSkips: 3, maxHoldStreak: 0,
    });
  });

  it("returns null when no member carries any new key (legacy records hide the row)", () => {
    expect(aggregateAiObservability([])).toBeNull();
    expect(
      aggregateAiObservability([
        member({
          decisionRounds: 10, actionCounts: { hold: 8 }, holdRate: 0.8,
          avgConfidence: 0.6, degradedRounds: 0, // 旧五键齐全但无任何新键
        }),
        member(),
      ]),
    ).toBeNull();
  });
});

describe("latestHoldStreak", () => {
  it("takes the tail-most decision carrying holdStreak (skipped rounds lack the key)", () => {
    const decisions = [
      decision({ round: 1, holdStreak: 3 }),
      decision({ round: 2, holdStreak: 4 }),
      decision({ round: 2 }), // in-flight 跳过轮事件：无 holdStreak 键 → 向前回溯
    ];
    expect(latestHoldStreak(decisions, { holdStreak: 99 })).toBe(4);
  });

  it("falls back to session.holdStreak when no decision carries the key", () => {
    expect(latestHoldStreak([decision()], { holdStreak: 7 })).toBe(7);
    expect(latestHoldStreak([], { holdStreak: 0 })).toBe(0);
  });

  it("returns null when both sources are missing (legacy backend shows —)", () => {
    expect(latestHoldStreak([decision()], null)).toBeNull();
    expect(latestHoldStreak([], undefined)).toBeNull();
    expect(latestHoldStreak([], {})).toBeNull();
  });
});
