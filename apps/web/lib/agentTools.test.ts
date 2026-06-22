import { describe, expect, it } from "vitest";
import { enabledSummary, groupToolsByCategory } from "./agentTools";
import type { AgentTool, AgentToolCategory } from "./agentTypes";

function tool(over: Partial<AgentTool> = {}): AgentTool {
  return {
    name: "get_candles",
    description: "拉取 K 线",
    category: "market",
    isUiAction: false,
    enabled: true,
    paramsSchema: { type: "object", properties: {} },
    ...over,
  };
}

describe("agentTools — groupToolsByCategory", () => {
  it("orders groups by CATEGORY_ORDER and keeps in-group order", () => {
    const tools = [
      tool({ name: "list_runs", category: "runs" }),
      tool({ name: "get_candles", category: "market" }),
      tool({ name: "show_chart", category: "ui" }),
      tool({ name: "get_market_summary", category: "market" }),
    ];
    const groups = groupToolsByCategory(tools);
    expect(groups.map((g) => g.category)).toEqual(["market", "ui", "runs"]);
    // 组内保持后端返回顺序（get_candles 在 get_market_summary 之前）。
    expect(groups[0].tools.map((t) => t.name)).toEqual(["get_candles", "get_market_summary"]);
  });

  it("drops empty categories", () => {
    const groups = groupToolsByCategory([tool({ category: "market" })]);
    expect(groups).toHaveLength(1);
    expect(groups[0].category).toBe("market");
  });

  it("falls back unknown categories to general", () => {
    const groups = groupToolsByCategory([
      tool({ name: "weird", category: "mystery" as AgentToolCategory }),
    ]);
    expect(groups.map((g) => g.category)).toEqual(["general"]);
  });
});

describe("agentTools — enabledSummary", () => {
  it("counts all enabled", () => {
    expect(enabledSummary([tool(), tool()])).toEqual({ enabled: 2, total: 2 });
  });

  it("counts all disabled", () => {
    expect(enabledSummary([tool({ enabled: false }), tool({ enabled: false })])).toEqual({
      enabled: 0,
      total: 2,
    });
  });

  it("counts a mix", () => {
    expect(enabledSummary([tool(), tool({ enabled: false }), tool()])).toEqual({
      enabled: 2,
      total: 3,
    });
  });
});
