import { describe, expect, it } from "vitest";

import {
  AI_TRADE_DEFAULT_POSITION_STEP,
  AI_TRADE_PROFIT_GUARD_DEFAULTS,
  AI_TRADE_POSITION_STEP_OPTIONS,
  clampBudgetToCap,
  deriveDefaultBudget,
  describeAiAction,
  describeBaseRule,
  describeCandidateLevel,
  describeCommittedExit,
  describeConsensus,
  describeDegradeReason,
  describeExecuted,
  describeFactors,
  describeOrderType,
  describePriceAction,
  describeProfitGuardCooldown,
  describeProfitGuardReason,
  describeProfitGuardFallback,
  describeProfitGuardStatus,
  describeProfitGuardTrail,
  describeProfitGuardUnavailable,
  isEffectiveExitCommitted,
  describeReflection,
  describeRegime,
  describeMultiTimeframeAlignment,
  describeMultiTimeframeFrame,
  describeStance,
  describeStopReason,
  describeTacticalAlignment,
  describeTacticalFrame,
  describeTrigger,
  describeWakeReason,
  describeVolume,
  marginReturnFromPricePct,
  positionLadder,
  priceMoveFromMarginPct,
  resolveAiTradeGate,
  validateContextRounds,
  validateProfitGuard,
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
  WakeKind,
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
    expect(describeAiAction("cancel_order")).toBe("撤单");
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

describe("describeOrderType (ai-trade-limit-orders, 委托方式文案)", () => {
  it("localizes the order type enum", () => {
    expect(describeOrderType("market")).toBe("市价");
    expect(describeOrderType("limit")).toBe("限价");
  });
  it("treats an absent order type as market (old snapshots / limit orders disabled)", () => {
    expect(describeOrderType(null)).toBe("市价");
    expect(describeOrderType(undefined)).toBe("市价");
  });
  it("falls back to the raw value for unknown enums (no fabrication)", () => {
    expect(describeOrderType("post_only")).toBe("post_only");
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


// ---------- 【基础策略】铁律中文化（ai-trade-base-strategy-ma-band §6.2 用例 26/27）----------
describe("describeBaseRule", () => {
  it("放行轮给出锚区名 + muted 常态着色", () => {
    expect(describeBaseRule({ baseRuleStatus: "pass", baseRuleZone: "mid" })).toEqual({
      label: "基础策略 · 中轨锚区 · 放行",
      tone: "muted",
    });
    expect(describeBaseRule({ baseRuleStatus: "pass", baseRuleZone: "lower" })?.label).toBe(
      "基础策略 · 下轨锚区 · 放行",
    );
  });

  it("否决 / 数据不足两态均为红（静默的保守会被用户当成故障）", () => {
    expect(describeBaseRule({ baseRuleStatus: "veto", baseRuleZone: null })).toEqual({
      label: "基础策略否决 · 观望",
      tone: "short",
    });
    expect(describeBaseRule({ baseRuleStatus: "unavailable", baseRuleZone: null })).toEqual({
      label: "基础策略 · 数据不足 · 观望",
      tone: "short",
    });
  });

  it("规则未装配（无 baseRuleStatus）→ null，组件整段不渲染", () => {
    expect(describeBaseRule({})).toBeNull();
    expect(describeBaseRule({ baseRuleStatus: null, baseRuleZone: null })).toBeNull();
  });
});

describe("describeDegradeReason", () => {
  it("命中三个基础策略码 → 中文说明（保留括号内参数供排查）", () => {
    expect(describeDegradeReason("base_rule_ma_band(pctB=0.42)")).toBe(
      "基础策略否决：开单价不在均线通道上/中/下轨附近(pctB=0.42)",
    );
    expect(describeDegradeReason("base_rule_ma_band_direction(zone=upper)")).toBe(
      "基础策略否决：方向与所处均线锚区不符(zone=upper)",
    );
    expect(describeDegradeReason("base_rule_band_unavailable")).toBe(
      "基础策略否决：均线通道数据不足，无法验证开单位置",
    );
  });

  it("未命中一律原样回退（既有 low_consensus / decision_timeout 展示逐字不变，P1-9）", () => {
    expect(describeDegradeReason("low_consensus(1/2)")).toBe("low_consensus(1/2)");
    expect(describeDegradeReason("decision_timeout")).toBe("decision_timeout");
    expect(describeDegradeReason(null)).toBe("");
    expect(describeDegradeReason(undefined)).toBe("");
  });

  it("describeExecuted 经其渲染：既有码逐字不变，新码中文化", () => {
    expect(describeExecuted("hold", null, "low_consensus(1/2)").label).toBe(
      "已降级观望（low_consensus(1/2)）",
    );
    expect(describeExecuted("hold", null, "base_rule_band_unavailable").label).toContain(
      "基础策略否决",
    );
  });
});


// ---------------- 利润保护层纯件（ai-trade-profit-protection §3.3）----------------
describe("validateProfitGuard", () => {
  it("默认参数合法", () => {
    expect(validateProfitGuard({ ...AI_TRADE_PROFIT_GUARD_DEFAULTS })).toBeNull();
  });

  it("关闭时忽略其余输入（残留值不该阻塞提交）", () => {
    expect(validateProfitGuard({ enabled: false, armReturnPct: Number.NaN })).toBeNull();
  });

  it("越界逐项报错（与后端 schema Field 边界一致）", () => {
    expect(validateProfitGuard({ enabled: true, armReturnPct: 0 })).toContain("武装阈值");
    expect(validateProfitGuard({ enabled: true, givebackPct: 1.5 })).toContain("回吐");
    expect(validateProfitGuard({ enabled: true, breakevenArmReturnPct: -1 })).toContain("保本");
    expect(validateProfitGuard({ enabled: true, minHoldSeconds: 99999 })).toContain("最短持有");
    expect(validateProfitGuard({ enabled: true, armReturnPct: Number.NaN })).toContain("武装阈值");
  });

  it("保本阈值 0 = 关闭保本，合法", () => {
    expect(validateProfitGuard({ enabled: true, breakevenArmReturnPct: 0 })).toBeNull();
  });
});

describe("describeProfitGuardStatus", () => {
  it("三态武装标签 + 色调（unavailable 用 short：保护此刻不生效必须显眼）", () => {
    expect(describeProfitGuardStatus({ ruleId: "profit_trailing", status: "armed" })).toEqual({
      label: "利润保护 · 移动止盈已武装",
      tone: "long",
    });
    expect(
      describeProfitGuardStatus({ ruleId: "profit_trailing", status: "breakeven_armed" }),
    ).toEqual({ label: "利润保护 · 保本线已武装", tone: "accent" });
    expect(describeProfitGuardStatus({ ruleId: "profit_trailing", status: "idle" })).toEqual({
      label: "利润保护 · 未武装",
      tone: "muted",
    });
    expect(
      describeProfitGuardStatus({
        ruleId: "profit_trailing",
        status: "unavailable",
        unavailableReason: "stale_feed",
      }),
    ).toEqual({ label: "利润保护暂不可用（行情数据陈旧）", tone: "short" });
  });

  it("未装配（快照缺失）与 flat 一律 null，组件整段不渲染", () => {
    expect(describeProfitGuardStatus(null)).toBeNull();
    expect(describeProfitGuardStatus(undefined)).toBeNull();
    expect(describeProfitGuardStatus({ ruleId: "profit_trailing", status: "flat" })).toBeNull();
  });
});

describe("describeProfitGuardFallback", () => {
  it("交易所侧确实挂了 OCO 才写「仍生效」，没挂就必须如实说没有兜底", () => {
    expect(
      describeProfitGuardFallback({
        ruleId: "r", status: "unavailable", exchangeStopLossPx: 0.0688,
      }),
    ).toContain("仅交易所侧止盈止损仍生效");
    // 运维把 AI_TRADE_DEFAULT_SL_PCT/_TP_PCT 置 0 ⇒ 后端根本不挂 OCO：此刻宣称有兜底是最坏的失灵
    // （既没有保护层、又以为有防线）。
    const bare = describeProfitGuardFallback({ ruleId: "r", status: "unavailable" });
    expect(bare).toContain("没有任何自动兜底");
    expect(bare).not.toContain("仍生效");
  });
});

describe("describeProfitGuardUnavailable", () => {
  it("三个原因码中文化；未知码回落「数据异常」（绝不显示裸英文）", () => {
    expect(
      describeProfitGuardUnavailable({ ruleId: "r", status: "unavailable", unavailableReason: "no_price" }),
    ).toBe("实时价不可用");
    expect(
      describeProfitGuardUnavailable({ ruleId: "r", status: "unavailable", unavailableReason: "bad_position" }),
    ).toBe("持仓读数异常");
    expect(describeProfitGuardUnavailable({ ruleId: "r", status: "unavailable" })).toBe("数据异常");
    expect(describeProfitGuardUnavailable(null)).toBe("数据异常");
  });
});

describe("describeProfitGuardReason", () => {
  it("三个触发码中文化；未知码原样回退", () => {
    expect(describeProfitGuardReason("profit_guard_trailing")).toBe("移动止盈");
    expect(describeProfitGuardReason("profit_guard_breakeven")).toBe("保本线");
    expect(describeProfitGuardReason("profit_guard_hard_take_profit")).toBe("硬止盈");
    expect(describeProfitGuardReason("something_else")).toBe("something_else");
    expect(describeProfitGuardReason(null)).toBe("");
  });
});

describe("marginReturnFromPricePct / priceMoveFromMarginPct", () => {
  it("价格口径 × 杠杆 = 保证金口径（5× 下标的 10% ⇒ 保证金 50%，诊断根因 ②）", () => {
    expect(marginReturnFromPricePct(0.1, 5)).toBeCloseTo(0.5, 10);
    expect(priceMoveFromMarginPct(0.05, 5)).toBeCloseTo(0.01, 10);
  });

  it("杠杆留空（null）必须返回 null——绝不硬编码后端 live_default_leverage（[P1-9]）", () => {
    expect(marginReturnFromPricePct(0.1, null)).toBeNull();
    expect(marginReturnFromPricePct(0.1, undefined)).toBeNull();
    expect(priceMoveFromMarginPct(0.05, null)).toBeNull();
  });

  it("非法输入（NaN / 非正杠杆 / 空值）一律 null，绝不假数据", () => {
    expect(marginReturnFromPricePct(null, 5)).toBeNull();
    expect(marginReturnFromPricePct(Number.NaN, 5)).toBeNull();
    expect(marginReturnFromPricePct(0.1, 0)).toBeNull();
    expect(marginReturnFromPricePct(0.1, -3)).toBeNull();
    expect(priceMoveFromMarginPct(0.05, 0)).toBeNull();
  });
});

describe("describeDegradeReason · 利润保护码", () => {
  it("三个保护码中文化（成交列表的原因位不再是裸英文）", () => {
    expect(describeDegradeReason("profit_guard_trailing")).toContain("浮盈自峰值回吐超阈");
    expect(describeDegradeReason("profit_guard_breakeven")).toContain("跌回保本线");
    expect(describeDegradeReason("profit_guard_hard_take_profit")).toContain("硬止盈");
  });
});


// ============ 自适应保护线 + 冷却期（ai-trade-profit-protection-adaptive §六）============
describe("describeProfitGuardTrail", () => {
  const base = {
    ruleId: "profit_trailing",
    status: "armed" as const,
    side: "long" as const,
    trailStopPx: 0.0741075,
    givebackPctEffective: 0.35,
    trailDistancePx: 0.00089,
  };

  it("必须交代这条线被谁定住（回吐阈 + 约束来源）", () => {
    expect(describeProfitGuardTrail({ ...base, trailBasis: "giveback" })).toBe(
      "移动止盈线 0.0741075｜回吐阈 35%",
    );
    expect(describeProfitGuardTrail({ ...base, trailBasis: "atr_floor" })).toContain(
      "已按波动率放宽至下界",
    );
    expect(describeProfitGuardTrail({ ...base, trailBasis: "atr_cap" })).toContain(
      "已按波动率收紧至上界",
    );
    expect(describeProfitGuardTrail({ ...base, trailBasis: "clamped_breakeven" })).toContain(
      "已夹至保本线",
    );
  });

  it("抑制态如实说「本轮作废」，绝不假装那条线还在（P0-1）", () => {
    const text = describeProfitGuardTrail({
      ...base,
      trailStopPx: null,
      trailDistancePx: null,
      trailBasis: "suppressed_at_entry",
      trailClampedAtEntry: true,
    });
    expect(text).toContain("本轮作废");
    expect(text).toContain("本轮不会据它平仓");
  });

  it("未武装 / 不可用 / 无快照 → null（整行不渲染，不显示算得出来却不作数的数字）", () => {
    expect(describeProfitGuardTrail(null)).toBeNull();
    expect(describeProfitGuardTrail({ ...base, status: "unavailable", trailStopPx: null })).toBeNull();
    expect(describeProfitGuardTrail({ ...base, status: "idle", trailStopPx: null })).toBeNull();
  });
});

describe("describeProfitGuardCooldown", () => {
  const cooldown = {
    activeUntilTs: 1_700_000_180,
    remainingSeconds: 120,
    scope: "same_side" as const,
    blockedSide: "long" as const,
    reasonCode: "profit_guard_trailing",
    label: "移动止盈",
  };

  it("同时给出剩余时间与豁免范围（只说「被拦截」会让人以为交易能力被剥夺）", () => {
    const text = describeProfitGuardCooldown(cooldown);
    expect(text).toContain("120s");
    expect(text).toContain("暂停做多开仓");
    expect(text).toContain("反向不受影响");
    expect(text).toContain("平仓不受限");
  });

  it("both 作用域写明多空均拦", () => {
    expect(describeProfitGuardCooldown({ ...cooldown, scope: "both" })).toContain("多空均拦");
  });

  it("未激活 / 已到期 → null（chip 整块不渲染）", () => {
    expect(describeProfitGuardCooldown(null)).toBeNull();
    expect(describeProfitGuardCooldown({ ...cooldown, remainingSeconds: 0 })).toBeNull();
  });
});

describe("validateProfitGuard · 自适应参数", () => {
  const enabled = { enabled: true };

  it("阶梯回吐比例必须单调不增（赚得越多不可允许回吐更多）", () => {
    expect(
      validateProfitGuard({
        ...enabled,
        givebackTiers: [
          { peakReturnPct: 0.1, givebackPct: 0.3 },
          { peakReturnPct: 0.25, givebackPct: 0.4 },
        ],
      }),
    ).toContain("单调不增");
  });

  it("阶梯阈值必须严格递增且首档 > 0", () => {
    expect(
      validateProfitGuard({
        ...enabled,
        givebackTiers: [
          { peakReturnPct: 0.25, givebackPct: 0.3 },
          { peakReturnPct: 0.1, givebackPct: 0.2 },
        ],
      }),
    ).toContain("严格递增");
    expect(
      validateProfitGuard({ ...enabled, givebackTiers: [{ peakReturnPct: 0, givebackPct: 0.3 }] }),
    ).toContain("严格递增");
  });

  it("ATR 下界不可大于上界；冷却秒需在 [0, 86400]", () => {
    expect(validateProfitGuard({ ...enabled, atrFloorMult: 5, atrCapMult: 1 })).toContain(
      "不可大于上界",
    );
    expect(validateProfitGuard({ ...enabled, cooldownSeconds: -1 })).toContain("冷却秒数");
    expect(validateProfitGuard({ ...enabled, cooldownSeconds: 0 })).toBeNull(); // 0 = 关闭，合法
  });

  it("空阶梯（显式关闭）与默认参数均合法", () => {
    expect(validateProfitGuard({ ...enabled, givebackTiers: [] })).toBeNull();
    expect(
      validateProfitGuard({
        ...enabled,
        givebackTiers: AI_TRADE_PROFIT_GUARD_DEFAULTS.givebackTiers,
        atrFloorMult: AI_TRADE_PROFIT_GUARD_DEFAULTS.atrFloorMult,
        atrCapMult: AI_TRADE_PROFIT_GUARD_DEFAULTS.atrCapMult,
        cooldownSeconds: AI_TRADE_PROFIT_GUARD_DEFAULTS.cooldownSeconds,
      }),
    ).toBeNull();
  });

  it("关闭保护时其余输入一律不校验（关闭态的残留值不该阻塞提交）", () => {
    expect(validateProfitGuard({ enabled: false, atrFloorMult: 99, cooldownSeconds: -5 })).toBeNull();
  });
});

describe("describeDegradeReason · 冷却降级码", () => {
  it("带参数后缀的冷却码按前缀中文化，参数原样保留", () => {
    expect(describeDegradeReason("profit_cooldown(120s,long)")).toBe(
      "利润保护冷却：刚锁住利润，暂停同向开仓(120s,long)",
    );
  });
});

// 承诺退出线（ai-trade-profit-protection-commitment C4）
describe("describeCommittedExit", () => {
  const base = {
    ruleId: "profit_trailing",
    status: "armed" as const,
    side: "long" as const,
    committedExitPx: 0.07436,
  };

  it("按 basis 各配后缀（adopted 无后缀 / atr_floor / clamped_breakeven）", () => {
    expect(describeCommittedExit({ ...base, committedExitBasis: "adopted" })).toBe(
      "承诺退出线 0.07436",
    );
    expect(describeCommittedExit({ ...base, committedExitBasis: "atr_floor" })).toContain(
      "已按波动率放宽至下界",
    );
    expect(describeCommittedExit({ ...base, committedExitBasis: "clamped_breakeven" })).toContain(
      "已夹至保本线",
    );
  });

  it("无承诺 / unavailable / flat / 无快照 → null（整行不渲染）", () => {
    expect(describeCommittedExit({ ...base, committedExitPx: null })).toBeNull();
    expect(describeCommittedExit({ ...base, status: "unavailable" })).toBeNull();
    expect(describeCommittedExit({ ...base, status: "flat" })).toBeNull();
    expect(describeCommittedExit(null)).toBeNull();
    expect(describeCommittedExit(undefined)).toBeNull();
  });
});

describe("isEffectiveExitCommitted", () => {
  it("只读后端 effectiveExitSource 归因，不比价推断（[P1-3]）", () => {
    expect(
      isEffectiveExitCommitted({
        ruleId: "profit_trailing",
        status: "armed",
        effectiveExitSource: "profit_guard_committed_exit",
      }),
    ).toBe(true);
    expect(
      isEffectiveExitCommitted({
        ruleId: "profit_trailing",
        status: "armed",
        effectiveExitSource: "profit_guard_trailing",
      }),
    ).toBe(false);
    expect(isEffectiveExitCommitted(null)).toBe(false);
    expect(isEffectiveExitCommitted({ ruleId: "profit_trailing", status: "armed" })).toBe(false);
  });
});

describe("describeProfitGuardReason: 承诺退出线码", () => {
  it("profit_guard_committed_exit → 承诺退出线", () => {
    expect(describeProfitGuardReason("profit_guard_committed_exit")).toBe("承诺退出线");
  });
});


// ---------- 唤醒感知上下文（ai-trade-wake-aware-context §6.2 G-8 / §3.3）----------
describe("describeTrigger 三级优先：信号板边沿 > wake.kind > trigger", () => {
  it("无边沿时把单调的两个兜底文案细化成七态", () => {
    const cases: Array<[WakeKind, string]> = [
      ["candle_close", "K 线收盘 · 信号板已重算"],
      ["bar_advanced", "补看新 K 线 · 首次研判"],
      ["resting_order", "挂单盯守 · 未成交委托"],
      ["profit_tier", "浮盈跨档 · 保护线已激活"],
      ["price_breakout", "价格异动 · 越过唤醒阈值"],
      ["position_watch", "持仓盯市 · 常规复盘"],
      ["heartbeat", "心跳复盘 · 数据未见异动"],
    ];
    for (const [kind, label] of cases) {
      expect(
        describeTrigger({ signalBoard: [], trigger: "interval", wake: { kind, trigger: "interval" } }).label,
      ).toBe(label);
    }
  });

  it("wake 绝不抢先：有边沿信号时仍按信号板派生（信息量更高的那个赢）", () => {
    const view = describeTrigger({
      signalBoard: [signal({ name: "均线突破", latestSignal: "open_long" })],
      trigger: "interval",
      wake: { kind: "price_breakout", trigger: "interval" },
    });
    expect(view).toEqual({ label: "开多信号 · 均线突破", tone: "long" });
  });

  it("wake 缺席时逐字回退到既有二值兜底", () => {
    expect(describeTrigger({ signalBoard: [], trigger: "interval" }).label).toBe("周期唤醒 · 常规复盘");
    expect(describeTrigger({ signalBoard: [] }).label).toBe("K 线收盘 · 常规复盘");
  });

  it("只有真正值得注意的四态用 accent（满屏 accent 等于没有 accent）", () => {
    const tone = (kind: WakeKind) =>
      describeTrigger({ signalBoard: [], wake: { kind, trigger: "interval" } }).tone;
    expect([tone("bar_advanced"), tone("price_breakout"), tone("profit_tier"), tone("resting_order")]).toEqual(
      ["accent", "accent", "accent", "accent"],
    );
    expect([tone("candle_close"), tone("position_watch"), tone("heartbeat")]).toEqual([
      "muted",
      "muted",
      "muted",
    ]);
  });
});

describe("describeTacticalFrame / describeTacticalAlignment / describeWakeReason", () => {
  const frame = {
    bar: "1m",
    sampleCount: 120,
    closeTs: 1,
    ageSeconds: 42,
    stale: false,
    closedBars: 3,
    regime: { primary: "range", volatility: "normal", event: "none", confidence: 0.6, labels: [] },
    digest: {
      priceAction: null,
      volume: null,
      factors: {
        rsi14: { value: 49, label: "neutral" },
        macdHist: { value: 0, label: "flat" },
        bollPctB: { value: 0.5, label: "mid" },
        atrPct: { value: 0.3, label: "low" },
        rocPct: { value: 0, label: "flat" },
      },
    },
  };

  it("把「自上轮收 N 根」放在中线背景放区间位的位置（战术层真正逐轮变化的量）", () => {
    const text = describeTacticalFrame(frame);
    expect(text).toContain("1m");
    expect(text).toContain("自上轮收 3 根");
    expect(text).toContain("120 根");
    expect(text).not.toContain("数据陈旧");
    expect(describeTacticalFrame({ ...frame, stale: true })).toContain("数据陈旧");
  });

  it("首轮无参照时不渲染「自上轮收」（绝不显示 null 根）", () => {
    expect(describeTacticalFrame({ ...frame, closedBars: null })).not.toContain("自上轮收");
  });

  it("体制关系中文化；未知枚举原样回显", () => {
    expect(describeTacticalAlignment("aligned")).toBe("与执行周期同向");
    expect(describeTacticalAlignment("diverged")).toBe("与执行周期背离");
    expect(describeTacticalAlignment("insufficient")).toBe("体制证据不足");
    expect(describeTacticalAlignment("unknown_enum")).toBe("unknown_enum");
  });

  it("唤醒原因说明取**折算后**的实际门槛，且 ATR 倍数必带周期名", () => {
    const text = describeWakeReason({
      kind: "price_breakout",
      trigger: "interval",
      executionBar: "30m",
      sinceLastRoundS: 182,
      priceMovePct: 0.42,
      priceMoveAtr: 0.91,
      moveThresholdAtr: 0.5,
      horizonScale: 0.316,
      moveThresholdAtrEff: 0.158,
    });
    expect(text).toContain("价格异动");
    expect(text).toContain("距上轮 182s");
    expect(text).toContain("+0.42%");
    expect(text).toContain("0.91×30m ATR");
    expect(text).toContain("阈值 0.16×30m ATR");
    expect(describeWakeReason(null)).toBe("");
  });

  it("无数值可讲时返回空串（轮头 chip 已给标签，再渲染一行同样的文字只是噪音）", () => {
    expect(describeWakeReason({ kind: "heartbeat", trigger: "interval" })).toBe("");
    expect(describeWakeReason({ kind: "resting_order", trigger: "interval", restingAgeS: 240 })).toBe(
      "挂单盯守 · 未成交委托 · 挂单已 240s",
    );
  });
});
