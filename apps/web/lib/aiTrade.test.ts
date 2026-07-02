import { describe, expect, it } from "vitest";

import {
  clampBudgetToCap,
  deriveDefaultBudget,
  describeAiAction,
  describeConsensus,
  describeExecuted,
  describeRegime,
  describeStance,
  describeStopReason,
  resolveAiTradeGate,
  validateContextRounds,
  validateStrategySelection,
} from "./aiTrade";
import type { Consensus, MarketContext } from "./aiTradeTypes";

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
