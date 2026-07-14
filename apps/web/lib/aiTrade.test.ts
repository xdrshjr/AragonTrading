import { describe, expect, it } from "vitest";

import {
  AI_TRADE_DEFAULT_POSITION_STEP,
  AI_TRADE_POSITION_STEP_OPTIONS,
  clampBudgetToCap,
  deriveDefaultBudget,
  describeAiAction,
  describeCandidateLevel,
  describeConsensus,
  describeExecuted,
  describeFactors,
  describePriceAction,
  describeReflection,
  describeRegime,
  describeMultiTimeframeAlignment,
  describeMultiTimeframeFrame,
  describeStance,
  describeStopReason,
  describeTrigger,
  describeVolume,
  positionLadder,
  resolveAiTradeGate,
  validateContextRounds,
  validateStrategySelection,
  validateWakeInterval,
} from "./aiTrade";
import type {
  Consensus,
  FactorReadings,
  MarketContext,
  MultiTimeframeFrame,
  PriceAction,
  StrategySignalView,
  VolumeContext,
} from "./aiTradeTypes";

const MTF_FRAME: MultiTimeframeFrame = {
  bar: "1D",
  sampleCount: 80,
  closeTs: 1,
  ageSeconds: 100,
  stale: true,
  regime: { primary: "trend_up", volatility: "normal", event: "none", confidence: 0.8, labels: [] },
  digest: { priceAction: null, volume: null, factors: { rsi14: { value: 72, label: "overbought" },
    macdHist: { value: 1, label: "bullish" }, bollPctB: { value: 1, label: "upper_half" },
    atrPct: { value: 1, label: "normal" }, rocPct: { value: 1, label: "positive" } } },
};

describe("multi-timeframe context formatting", () => {
  it("formats alignment, candidate levels, and stale frame evidence", () => {
    expect(describeMultiTimeframeAlignment("mixed")).toBe("多空混合");
    expect(describeMultiTimeframeAlignment("future_state")).toBe("future_state");
    expect(describeCandidateLevel({ price: 100, distancePct: 2.5, bars: ["1D", "4H"] }))
      .toContain("100");
    expect(describeCandidateLevel(null)).toBe("暂无");
    const text = describeMultiTimeframeFrame(MTF_FRAME);
    expect(text).toContain("1D");
    expect(text).toContain("上升趋势");
    expect(text).toContain("超买");
    expect(text).toContain("80 根");
    expect(text).toContain("数据陈旧");
  });
});

const ready = {
  accountMode: "live" as const,
  hasReadyDefault: true,
  aiTradeEnabled: true,
  agentConfigured: true,
  liveAutoActive: false,
  hasActiveSession: false,
};

describe("resolveAiTradeGate", () => {
  it("returns null when all gates pass", () => {
    expect(resolveAiTradeGate(ready)).toBeNull();
  });

  it("returns null while a session is active (toggle used to stop)", () => {
    expect(resolveAiTradeGate({ ...ready, accountMode: "sim", hasActiveSession: true })).toBeNull();
  });

  it("blocks when not in live account mode", () => {
    expect(resolveAiTradeGate({ ...ready, accountMode: "sim" })).toMatch(/实盘/);
  });

  it("blocks when no ready default account", () => {
    expect(resolveAiTradeGate({ ...ready, hasReadyDefault: false })).toMatch(/默认实盘账户/);
  });

  it("blocks when AI trade disabled", () => {
    expect(resolveAiTradeGate({ ...ready, aiTradeEnabled: false })).toMatch(/未启用/);
  });

  it("blocks when agent model not configured", () => {
    expect(resolveAiTradeGate({ ...ready, agentConfigured: false })).toMatch(/AI 模型/);
  });

  it("blocks when a live-auto group is active (mutual exclusivity)", () => {
    expect(resolveAiTradeGate({ ...ready, liveAutoActive: true })).toMatch(/互斥/);
  });
});

describe("validateStrategySelection", () => {
  it("rejects empty selection", () => {
    expect(validateStrategySelection([])).toMatch(/至少/);
  });
  it("rejects over the max", () => {
    expect(validateStrategySelection([1, 2, 3], 2)).toMatch(/最多/);
  });
  it("accepts a valid selection", () => {
    expect(validateStrategySelection([1, 2])).toBeNull();
  });
});

describe("validateContextRounds", () => {
  it("rejects below min", () => {
    expect(validateContextRounds(1)).toMatch(/整数/);
  });
  it("rejects above max", () => {
    expect(validateContextRounds(9999)).toMatch(/整数/);
  });
  it("rejects non-integer", () => {
    expect(validateContextRounds(3.5)).toMatch(/整数/);
  });
  it("accepts a valid value", () => {
    expect(validateContextRounds(10)).toBeNull();
  });
});

describe("validateWakeInterval (wake-interval, blank/0 = disabled, out-of-range errors)", () => {
  it("treats blank (null) and 0 as a legal disable", () => {
    expect(validateWakeInterval(null)).toBeNull();
    expect(validateWakeInterval(0)).toBeNull();
  });
  it("accepts values within [0.5, 240] minutes", () => {
    expect(validateWakeInterval(2)).toBeNull();
    expect(validateWakeInterval(0.5)).toBeNull();
    expect(validateWakeInterval(240)).toBeNull();
  });
  it("rejects below the floor and above the ceiling", () => {
    expect(validateWakeInterval(0.1)).toMatch(/唤醒周期/);
    expect(validateWakeInterval(9999)).toMatch(/唤醒周期/);
  });
  it("rejects a non-finite value", () => {
    expect(validateWakeInterval(Number.NaN)).toMatch(/唤醒周期/);
  });
});

describe("budget helpers", () => {
  it("derives 1% default budget", () => {
    expect(deriveDefaultBudget(1000)).toBeCloseTo(10);
  });
  it("clamps to the 5% hard cap", () => {
    expect(clampBudgetToCap(500, 1000)).toBeCloseTo(50);
  });
});

describe("describe helpers", () => {
  it("localizes actions", () => {
    expect(describeAiAction("open_long")).toBe("开多");
    expect(describeAiAction("open_short")).toBe("开空");
    expect(describeAiAction("close")).toBe("平仓");
    expect(describeAiAction("hold")).toBe("观望");
  });
  it("localizes stances", () => {
    expect(describeStance("long")).toBe("多");
    expect(describeStance("flat")).toBe("空仓");
  });
  it("localizes stop reasons", () => {
    expect(describeStopReason("session_stop_loss")).toMatch(/止损/);
    expect(describeStopReason(null)).toBe("已停止");
    expect(describeStopReason("unknown_reason")).toBe("已停止");
  });
});

const mc = (over: Partial<MarketContext> = {}): MarketContext => ({
  primary: "trend_up",
  volatility: "high_volatility",
  event: "breakout",
  confidence: 0.6,
  labels: [],
  ...over,
});

describe("describeRegime (E1, real regime enum, R-P1-2)", () => {
  it("localizes the real regime enum", () => {
    expect(describeRegime(mc())).toBe("上升趋势 · 高波动 · 突破");
    expect(describeRegime(mc({ primary: "trend_down", volatility: "low_volatility", event: "crash" }))).toBe(
      "下降趋势 · 低波动 · 急跌",
    );
  });
  it("omits the event segment when none", () => {
    expect(describeRegime(mc({ primary: "range", volatility: "normal", event: "none" }))).toBe("震荡 · 常波动");
  });
  it("falls back to the raw value for unknown enums (no fabrication)", () => {
    expect(describeRegime(mc({ primary: "weird_regime", event: "none" }))).toBe("weird_regime · 高波动");
  });
  it("returns empty string for null/undefined", () => {
    expect(describeRegime(null)).toBe("");
    expect(describeRegime(undefined)).toBe("");
  });
});

const consensus = (over: Partial<Consensus> = {}): Consensus => ({
  longVotes: 3,
  shortVotes: 1,
  flatVotes: 1,
  total: 5,
  netStance: "long",
  agreement: 0.6,
  ...over,
});

describe("describeConsensus (E2)", () => {
  it("renders votes and agreement percent", () => {
    expect(describeConsensus(consensus())).toBe("3多/1空/1空仓 · 一致度 60%");
  });
  it("returns empty string when absent", () => {
    expect(describeConsensus(null)).toBe("");
  });
});

const priceAction = (over: Partial<PriceAction> = {}): PriceAction => ({
  changePct: 1.2,
  trend: "up",
  emaFast: 101,
  emaSlow: 100,
  emaRelation: "fast_above",
  emaSlopePct: 0.4,
  rangePos: 0.78,
  swingHigh: 110,
  swingLow: 90,
  consecUp: 3,
  consecDown: 0,
  lastBar: { o: 100, h: 102, l: 99, c: 101, bodyPct: 1 },
  ...over,
});

describe("describePriceAction (R1)", () => {
  it("localizes trend / ema structure / range position", () => {
    const text = describePriceAction(priceAction());
    expect(text).toContain("走势 上行(+1.2%)");
    expect(text).toContain("多头排列");
    expect(text).toContain("区间位 78%");
  });
  it("omits range position when null and falls back on unknown trend", () => {
    const text = describePriceAction(priceAction({ trend: "weird", rangePos: null }));
    expect(text).toContain("weird");
    expect(text).not.toContain("区间位");
  });
  it("returns empty string for null/undefined", () => {
    expect(describePriceAction(null)).toBe("");
    expect(describePriceAction(undefined)).toBe("");
  });
});

const volumeCtx = (over: Partial<VolumeContext> = {}): VolumeContext => ({
  last: 180,
  sma: 100,
  ratio: 1.8,
  zscore: 1.2,
  trend: "rising",
  spike: false,
  ...over,
});

describe("describeVolume (R1)", () => {
  it("localizes volume trend and ratio", () => {
    const text = describeVolume(volumeCtx());
    expect(text).toContain("量能 放量");
    expect(text).toContain("量比 1.8");
    expect(text).not.toContain("异常放量");
  });
  it("appends spike marker and omits ratio when null", () => {
    const text = describeVolume(volumeCtx({ ratio: null, spike: true }));
    expect(text).toContain("异常放量");
    expect(text).not.toContain("量比");
  });
  it("returns empty string when absent", () => {
    expect(describeVolume(null)).toBe("");
  });
});

const factorReadings = (over: Partial<FactorReadings> = {}): FactorReadings => ({
  rsi14: { value: 63.2, label: "neutral" },
  macdHist: { value: 12.4, label: "bullish" },
  bollPctB: { value: 0.82, label: "upper_half" },
  atrPct: { value: 1.1, label: "normal" },
  rocPct: { value: 2.3, label: "positive" },
  ...over,
});

describe("describeFactors (R1)", () => {
  it("localizes the fixed factor readings", () => {
    const text = describeFactors(factorReadings());
    expect(text).toContain("RSI 中性");
    expect(text).toContain("MACD 偏多");
    expect(text).toContain("布林 上半区");
    expect(text).toContain("动量 正向");
  });
  it("falls back to the raw label for unknown enums (no fabrication)", () => {
    const text = describeFactors(factorReadings({ rsi14: { value: null, label: "mystery" } }));
    expect(text).toContain("RSI mystery");
  });
  it("returns empty string when absent", () => {
    expect(describeFactors(null)).toBe("");
  });
});

describe("describeExecuted (E3, four states + tone)", () => {
  it("skipped in-flight round", () => {
    expect(describeExecuted("hold", { skipped: true, reason: "decision_in_flight" }, null)).toEqual({
      label: "已跳过",
      tone: "muted",
    });
  });
  it("consensus-gate downgrade shows reason with short tone", () => {
    const view = describeExecuted("open_long", { traded: false }, "low_consensus(0/1)");
    expect(view.tone).toBe("short");
    expect(view.label).toMatch(/已降级观望/);
    expect(view.label).toMatch(/low_consensus\(0\/1\)/);
  });
  it("filled open colored by side", () => {
    expect(describeExecuted("open_long", { traded: true }, null)).toEqual({ label: "已成交", tone: "long" });
    expect(describeExecuted("open_short", { traded: true }, null)).toEqual({ label: "已成交", tone: "short" });
  });
  it("plain hold is muted 观望", () => {
    expect(describeExecuted("hold", { traded: false }, null)).toEqual({ label: "观望", tone: "muted" });
  });
});

describe("describeReflection (下单前二次反思裁决态)", () => {
  it("pending reflection reads as accent 复核中", () => {
    expect(describeReflection({ reflectionPending: true, reflectionVerdict: null })).toEqual({
      label: "二次反思复核中…",
      tone: "accent",
    });
  });
  it("confirm verdict reads as long-toned 确认执行", () => {
    expect(describeReflection({ reflectionPending: false, reflectionVerdict: "confirm" })).toEqual({
      label: "二次反思确认执行",
      tone: "long",
    });
  });
  it("abort verdict reads as short-toned 否决观望", () => {
    expect(describeReflection({ reflectionPending: false, reflectionVerdict: "abort" })).toEqual({
      label: "二次反思否决 · 观望",
      tone: "short",
    });
  });
  it("returns null for a round without reflection (hold / disabled)", () => {
    expect(describeReflection({ reflectionPending: false, reflectionVerdict: null })).toBeNull();
    expect(describeReflection({})).toBeNull();
  });
});

const signal = (over: Partial<StrategySignalView> = {}): StrategySignalView => ({
  strategyId: 1,
  name: "均线突破",
  stance: "long",
  latestSignal: "open_long",
  strength: 0.8,
  reason: "",
  ...over,
});

describe("positionLadder (ai-trade-position-sizing-steps, backend position_ladder mirror [R-5])", () => {
  it("mirrors the backend ladder table exactly", () => {
    expect(positionLadder(0.1)).toEqual([0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0]);
    expect(positionLadder(0.2)).toEqual([0.2, 0.4, 0.6, 0.8, 1.0]);
    expect(positionLadder(0.25)).toEqual([0.25, 0.5, 0.75, 1.0]);
    expect(positionLadder(0.5)).toEqual([0.5, 1.0]);
    expect(positionLadder(1)).toEqual([1.0]);
  });
  it("returns an empty ladder for the continuous / non-positive sentinel", () => {
    expect(positionLadder(0)).toEqual([]);
    expect(positionLadder(-0.5)).toEqual([]);
  });
  it("caps the top gear at 100% for non-divisor steps (no 99%-style top)", () => {
    expect(positionLadder(0.3)).toEqual([0.3, 0.6, 0.9]); // 顶格 0.9 < 1.0
  });
});

describe("AI_TRADE_POSITION_STEP_OPTIONS ([R-6] discrete only, no continuous)", () => {
  it("exposes exactly 10/20/25/50/100% and excludes 0 (continuous / AI-free)", () => {
    const values = AI_TRADE_POSITION_STEP_OPTIONS.map((o) => o.value);
    expect(new Set(values)).toEqual(new Set([0.1, 0.2, 0.25, 0.5, 1]));
    expect(values).not.toContain(0); // 需求明确排斥「AI 自由决定金额」，不作一等选项暴露
  });
  it("defaults the step to 0.25 (aligned with backend ai_trade_default_position_step_pct)", () => {
    expect(AI_TRADE_DEFAULT_POSITION_STEP).toBe(0.25);
    expect(AI_TRADE_POSITION_STEP_OPTIONS.some((o) => o.value === AI_TRADE_DEFAULT_POSITION_STEP)).toBe(true);
  });
});

describe("describeTrigger (R1, trigger reason derived from signalBoard)", () => {
  it("falls back to a routine review when no strategy fired (empty / all none)", () => {
    expect(describeTrigger({ signalBoard: [] })).toEqual({
      label: "K 线收盘 · 常规复盘",
      tone: "muted",
    });
    expect(
      describeTrigger({ signalBoard: [signal({ latestSignal: "none" }), signal({ latestSignal: "none" })] }),
    ).toEqual({ label: "K 线收盘 · 常规复盘", tone: "muted" });
  });

  it("labels an interval wake with no edge signal as a periodic review (accent)", () => {
    expect(describeTrigger({ signalBoard: [], trigger: "interval" })).toEqual({
      label: "周期唤醒 · 常规复盘",
      tone: "accent",
    });
    // 有边沿信号时，即便 trigger=interval 仍按信号板派生（周期唤醒只改「无边沿」分支）。
    expect(
      describeTrigger({ signalBoard: [signal({ latestSignal: "open_long" })], trigger: "interval" }).label,
    ).toContain("开多信号");
  });

  it("labels a single open_long trigger with the strategy name and long tone", () => {
    expect(
      describeTrigger({ signalBoard: [signal({ name: "均线突破", latestSignal: "open_long" })] }),
    ).toEqual({ label: "开多信号 · 均线突破", tone: "long" });
  });

  it("colors a single open_short trigger short and close accent", () => {
    expect(describeTrigger({ signalBoard: [signal({ latestSignal: "open_short" })] }).tone).toBe("short");
    const close = describeTrigger({ signalBoard: [signal({ name: "反转", latestSignal: "close" })] });
    expect(close).toEqual({ label: "平仓信号 · 反转", tone: "accent" });
  });

  it("aggregates multiple same-direction triggers into a strategy count", () => {
    expect(
      describeTrigger({
        signalBoard: [
          signal({ strategyId: 1, latestSignal: "open_long" }),
          signal({ strategyId: 2, latestSignal: "open_long" }),
          signal({ strategyId: 3, latestSignal: "open_long" }),
        ],
      }),
    ).toEqual({ label: "3 策略开多信号", tone: "long" });
  });

  it("reports a divergence tally for conflicting triggers with accent tone", () => {
    expect(
      describeTrigger({
        signalBoard: [
          signal({ strategyId: 1, latestSignal: "open_long" }),
          signal({ strategyId: 2, latestSignal: "open_long" }),
          signal({ strategyId: 3, latestSignal: "open_short" }),
        ],
      }),
    ).toEqual({ label: "信号分歧 · 2开多/1开空", tone: "accent" });
  });
});
