import "@testing-library/jest-dom/vitest";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { AiTradeRoundTrace, AiTradeSessionRow } from "@/lib/aiTradeTypes";
import { useAppStore } from "@/lib/store";
import AgentPanel from "./AgentPanel";
import AiTradeAgentTrace from "./AiTradeAgentTrace";

// AgentPanel 依赖：WS 与 REST 全桩化，避免真实连接 / 网络。
vi.mock("@/lib/agentWs", () => ({
  agentWS: vi.fn(() => ({ connect: vi.fn(), close: vi.fn(), send: vi.fn() })),
}));
vi.mock("@/lib/agentApi", () => ({
  agentApi: {
    messages: vi.fn().mockResolvedValue([]),
    clear: vi.fn().mockResolvedValue({}),
    config: vi.fn(),
  },
}));
// AiTradeToggle 头部控件（含自身守门查询）：桩为占位，聚焦分段视图行为。
vi.mock("./AiTradeToggle", () => ({ default: () => <div data-testid="ai-trade-toggle" /> }));

// jsdom 未实现 scrollIntoView（时间线贴底跟随会调用），补一个无副作用桩。
Element.prototype.scrollIntoView = vi.fn();

const SESSION: AiTradeSessionRow = {
  sessionId: "s1",
  runId: 1,
  state: "running",
  instId: "BTC-USDT-SWAP",
  bar: "1H",
  strategyIds: [1],
  strategyNames: ["S"],
  budgetUsd: 100,
  leverage: 3,
  contextRefreshRounds: 10,
  roundsCompleted: 2,
  roundsSinceSummary: 1,
  positionSide: "flat",
  sessionPnlUsd: 12.5,
  startedTs: 0,
};

function round(overrides: Partial<AiTradeRoundTrace>): AiTradeRoundTrace {
  return {
    round: 1,
    ts: 1_700_000_000,
    lastPx: 63000,
    signalBoard: [],
    thinking: "",
    analysis: "",
    toolCallPending: false,
    toolCall: null,
    executed: null,
    degradeReason: null,
    summary: null,
    errorMessage: null,
    state: "deciding",
    ...overrides,
  };
}

afterEach(() => cleanup());

describe("AiTradeAgentTrace", () => {
  it("running session with no traces shows the waiting empty state", () => {
    render(<AiTradeAgentTrace session={SESSION} traces={[]} />);
    expect(screen.getByText(/正在等待首个确认 K 线/)).toBeInTheDocument();
    expect(screen.getByText("运行中")).toBeInTheDocument();
  });

  it("streaming round renders analysis text under a 研判中 chip", () => {
    render(
      <AiTradeAgentTrace
        session={SESSION}
        traces={[round({ analysis: "多数策略维持多头立场", state: "deciding" })]}
      />,
    );
    expect(screen.getByText("多数策略维持多头立场")).toBeInTheDocument();
    expect(screen.getByText("研判中…")).toBeInTheDocument();
  });

  it("executed round shows the decision tool card and long-toned result", () => {
    render(
      <AiTradeAgentTrace
        session={SESSION}
        traces={[
          round({
            state: "done",
            analysis: "开多",
            toolCall: { action: "open_long", sizeFraction: 0.4, confidence: 0.72, reason: "顺势" },
            executed: { ok: true, traded: true, positionAfter: "long", action: "open_long" },
          }),
        ]}
      />,
    );
    expect(screen.getByText("决策工具 · 开多")).toBeInTheDocument();
    const result = screen.getByText("已成交");
    expect(result).toBeInTheDocument();
    expect(result.parentElement?.getAttribute("style") ?? "").toContain("--long");
  });

  it("degraded round shows the downgrade result", () => {
    render(
      <AiTradeAgentTrace
        session={SESSION}
        traces={[
          round({
            state: "done",
            toolCall: { action: "hold", reason: "共识不足" },
            executed: { ok: true, traded: false, positionAfter: "flat", action: "hold" },
            degradeReason: "low_consensus(0/1)",
          }),
        ]}
      />,
    );
    expect(screen.getByText(/已降级观望/)).toBeInTheDocument();
  });

  it("summary round renders an expandable context-refresh node", () => {
    render(
      <AiTradeAgentTrace
        session={SESSION}
        traces={[round({ state: "done", summary: "截至第1轮：偏多" })]}
      />,
    );
    expect(screen.getByText(/上下文已刷新（第 1 轮摘要）/)).toBeInTheDocument();
    expect(screen.getByText("截至第1轮：偏多")).toBeInTheDocument();
  });

  it("stopped session with no traces reads as a stopped empty state", () => {
    render(<AiTradeAgentTrace session={{ ...SESSION, state: "stopped" }} traces={[]} />);
    expect(screen.getByText(/会话已停止/)).toBeInTheDocument();
    expect(screen.getByText("已停止")).toBeInTheDocument();
  });
});

describe("AgentPanel segmented view", () => {
  beforeEach(() => {
    localStorage.clear();
    useAppStore.setState({
      agentConversationId: "conv-test",
      aiTradeSession: null,
      aiTradeTraces: [],
    });
  });

  it("hides the 执行轨迹 tab and renders chat when there is no AI trade session", () => {
    render(<AgentPanel />);
    expect(screen.queryByRole("tab", { name: /执行轨迹/ })).not.toBeInTheDocument();
    expect(screen.getByText("AI 交易助手")).toBeInTheDocument(); // 聊天空态逐字不变
  });

  it("auto-switches to the trace view when a session appears", () => {
    render(<AgentPanel />);
    act(() => {
      useAppStore.setState({ aiTradeSession: SESSION });
    });
    expect(screen.getByRole("tab", { name: /执行轨迹/ })).toBeInTheDocument();
    expect(screen.getByText("运行中")).toBeInTheDocument(); // 轨迹概览条已呈现
  });

  it("respects a manual switch back to 对话 and does not auto-steal", () => {
    render(<AgentPanel />);
    act(() => {
      useAppStore.setState({ aiTradeSession: SESSION });
    });
    fireEvent.click(screen.getByRole("tab", { name: "对话" }));
    expect(screen.queryByText("运行中")).not.toBeInTheDocument();
    expect(screen.getByText("AI 交易助手")).toBeInTheDocument();
  });

  it("falls back to 对话 when the session clears", () => {
    render(<AgentPanel />);
    act(() => {
      useAppStore.setState({ aiTradeSession: SESSION });
    });
    act(() => {
      useAppStore.setState({ aiTradeSession: null });
    });
    expect(screen.queryByRole("tab", { name: /执行轨迹/ })).not.toBeInTheDocument();
    expect(screen.getByText("AI 交易助手")).toBeInTheDocument();
  });
});
