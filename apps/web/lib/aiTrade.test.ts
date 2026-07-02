import { describe, expect, it } from "vitest";

import {
  clampBudgetToCap,
  deriveDefaultBudget,
  describeAiAction,
  describeStance,
  describeStopReason,
  resolveAiTradeGate,
  validateContextRounds,
  validateStrategySelection,
} from "./aiTrade";

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
