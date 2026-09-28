import "@testing-library/jest-dom/vitest";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { agentApi } from "@/lib/agentApi";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { AiTradeGroupState } from "@/lib/aiTradeTypes";
import { agentWS } from "@/lib/agentWs";
import { useAppStore } from "@/lib/store";
import AgentPanel from "./AgentPanel";

// 工具栏两横栏（agent-panel-toolbar-two-rows §9.1 A）：守的是**排版契约与位置**，不是像素——
// jsdom 没有布局引擎，「不折行」只能靠冻结类名与结构证明，真实换行由人工验收（R1–R9）负责。
//
// 桩化沿用 AiTradeAgentTrace.test.tsx：WS 与 REST 全桩，AiTradeToggle 桩为占位。桩掉它之后整棵
// 子树里不再有任何 useQuery（AiTradeStatsButton 只有本地 state），因此**不需要** QueryClientProvider。
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
vi.mock("./AiTradeToggle", () => ({ default: () => <div data-testid="ai-trade-toggle" /> }));

// jsdom 未实现 scrollIntoView（消息流贴底跟随会调用），补一个无副作用桩。
Element.prototype.scrollIntoView = vi.fn();

// 最小分组夹具：只为把面板切进「执行轨迹」视图（AiTradeAgentTrace.test.tsx 的 groupWith 未导出，
// 不为此去改那个文件）。
function traceGroup(): AiTradeGroupState {
  return {
    groupId: "g1",
    bar: "1H",
    strategyIds: [1],
    strategyNames: ["S"],
    totalBudgetUsd: 100,
    leverage: 3,
    contextRefreshRounds: 10,
    state: "running",
    startedTs: 0,
    members: {
      "BTC-USDT-SWAP": {
        instId: "BTC-USDT-SWAP",
        sessionId: "s1",
        allocationPct: 100,
        session: {
          sessionId: "s1", runId: 1, state: "running", instId: "BTC-USDT-SWAP", bar: "1H",
          strategyIds: [1], strategyNames: ["S"], budgetUsd: 100, leverage: 3,
          contextRefreshRounds: 10, roundsCompleted: 0, roundsSinceSummary: 0,
          positionSide: "flat", sessionPnlUsd: 0, startedTs: 0,
        },
        trades: [],
        position: null,
        decisions: [],
        traces: [],
        summary: null,
      },
    },
  };
}

// `ready` 是 agentWS 的**第 2 个位置参数** onEvent 投喂进来的（AgentPanel.tsx 的
// `agentWS(conversationId, handleEvent, onConnState)`）。
function emitReady(model: string) {
  const onEvent = vi.mocked(agentWS).mock.calls[0][1];
  act(() => {
    onEvent({ type: "ready", data: { configured: true, provider: "p", model } } as never);
  });
}

beforeEach(() => {
  vi.clearAllMocks();
  localStorage.clear();
  useAppStore.setState({ agentConversationId: "conv-toolbar", aiTradeGroup: null });
});

afterEach(() => cleanup());

describe("AgentPanel 工具栏两横栏", () => {
  it("未配置或断线仍可清空，标题只有单一入口", () => {
    render(<AgentPanel />);
    const emit = vi.mocked(agentWS).mock.calls[0][1];
    act(() => emit({ type: "ready", data: {
      configured: false, model: "", provider: "test",
    } }));
    expect(screen.getAllByRole("button", { name: "清空上下文" })).toHaveLength(1);
    expect(screen.queryByRole("button", { name: "新对话" })).toBeNull();
    expect(screen.queryByRole("button", { name: "清除上下文" })).toBeNull();
    expect(screen.getByRole("button", { name: "清空上下文" })).toBeEnabled();
    fireEvent.click(screen.getByRole("button", { name: "清空上下文" }));
    expect(screen.getByText(/历史记录保留，自动交易状态不受影响/)).toBeTruthy();
  });

  it("聊天生成中切到轨迹且断线仍可停止，清空回聊天不改变交易分组", () => {
    render(<AgentPanel />);
    const emit = vi.mocked(agentWS).mock.calls[0][1];
    const state = vi.mocked(agentWS).mock.calls[0][2]!;
    const group = traceGroup();
    act(() => {
      emit({ type: "message_start", data: { id: "stream", role: "assistant" } });
      useAppStore.setState({ aiTradeGroup: group });
      state(false);
    });
    expect(screen.getByRole("button", { name: "清空上下文" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "停止当前回答" }));
    fireEvent.click(screen.getByRole("button", { name: "清空上下文" }));
    expect(screen.getByRole("tab", { name: "对话" })).toHaveAttribute("aria-selected", "true");
    expect(useAppStore.getState().aiTradeGroup).toBe(group);
    expect(screen.getByTestId("agent-composer")).toBeTruthy();
  });

  it("清空后丢弃旧连接状态，保留新连接", () => {
    render(<AgentPanel />);
    const oldState = vi.mocked(agentWS).mock.calls[0][2]!;
    act(() => oldState(true));
    fireEvent.click(screen.getByRole("button", { name: /清[空除]上下文/ }));
    const newState = vi.mocked(agentWS).mock.calls.at(-1)![2]!;
    act(() => newState(true));
    act(() => oldState(false));
    expect(screen.getByTitle("已连接")).toBeInTheDocument();
    expect(screen.queryByText(/连接中断/)).toBeNull();
  });

  it("旧会话 gap 对账迟到不能恢复已清空历史", async () => {
    let resolve!: (value: never[]) => void;
    vi.mocked(agentApi.messages).mockReturnValueOnce(
      new Promise((done) => { resolve = done; }),
    );
    render(<AgentPanel />);
    const emit = vi.mocked(agentWS).mock.calls[0][1];
    act(() => {
      emit({ type: "resumed", data: { ok: false, reason: "gap" } });
      emit({ type: "done", data: {} });
    });
    fireEvent.click(screen.getByRole("button", { name: /清[空除]上下文/ }));
    await act(async () => resolve([
      { id: "old", role: "user", ts: 0,
        blocks: [{ type: "text", text: "旧会话独特口令" }] } as never,
    ]));
    expect(screen.queryByText("旧会话独特口令")).toBeNull();
    expect(agentApi.clear).not.toHaveBeenCalled();
  });

  it("renders the control bar directly above the composer", () => {
    render(<AgentPanel />);
    expect(screen.getByTestId("ai-trade-control-bar").nextElementSibling).toBe(
      screen.getByTestId("agent-composer"),
    );
  });

  it("keeps the control bar mounted in the trace view where the composer is hidden", () => {
    render(<AgentPanel />);
    act(() => {
      useAppStore.setState({ aiTradeGroup: traceGroup() });
    });
    expect(screen.queryByTestId("agent-composer")).not.toBeInTheDocument();
    const bar = screen.getByTestId("ai-trade-control-bar");
    expect(bar).toBeInTheDocument();
    // 输入框不渲染时控制条自然落到面板底缘 —— 两个视图里位置一致，用户不必重新找同一个开关。
    expect(bar.parentElement!.lastElementChild).toBe(bar);
  });

  it("no longer renders the auto-trade toggle inside the header", () => {
    const { container } = render(<AgentPanel />);
    const header = container.querySelector("header")!;
    expect(header.querySelector('[data-testid="ai-trade-toggle"]')).toBeNull();
    expect(screen.getByTestId("ai-trade-toggle")).toBeInTheDocument();
  });

  it("freezes the no-wrap contract on both toolbar rows", () => {
    const { container } = render(<AgentPanel />);
    expect(container.querySelector("header")).toHaveClass("whitespace-nowrap");
    expect(screen.getByTestId("ai-trade-control-bar")).toHaveClass("whitespace-nowrap");
  });

  it("truncates the model name instead of squeezing the buttons", () => {
    render(<AgentPanel />);
    const model = "claude-sonnet-4-5-20250929-extra-long-model-identifier";
    emitReady(model);
    const span = screen.getByTitle(model);
    expect(span).toHaveClass("truncate");
    expect(span).toHaveTextContent(model);
  });

  it("announces the control bar as a labelled group", () => {
    render(<AgentPanel />);
    expect(screen.getByRole("group", { name: "AI 自动交易控制" })).toBeInTheDocument();
  });
});
