import "@testing-library/jest-dom/vitest";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { AiTradeGroupState, AiTradeRoundTrace, AiTradeSessionRow } from "@/lib/aiTradeTypes";
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

// 分组封装：把单会话视图包成 1 成员分组（AiTradeAgentTrace 现按分组 + 聚焦标的渲染）。
function groupWith(session: AiTradeSessionRow, traces: AiTradeRoundTrace[] = []): AiTradeGroupState {
  return {
    groupId: "g1",
    bar: session.bar,
    strategyIds: session.strategyIds,
    strategyNames: session.strategyNames,
    totalBudgetUsd: session.budgetUsd,
    leverage: session.leverage,
    contextRefreshRounds: session.contextRefreshRounds,
    state: session.state === "stopped" ? "stopped" : "running",
    startedTs: session.startedTs,
    members: {
      [session.instId]: {
        instId: session.instId,
        sessionId: session.sessionId,
        allocationPct: 100,
        session,
        trades: [],
        position: null,
        decisions: [],
        traces,
        summary: null,
      },
    },
  };
}

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
    render(<AiTradeAgentTrace group={groupWith(SESSION)} activeInstId="BTC-USDT-SWAP" />);
    expect(screen.getByText(/正在等待首个确认 K 线/)).toBeInTheDocument();
    expect(screen.getByText("运行中")).toBeInTheDocument();
  });

  it("streaming round renders analysis text under a 研判中 chip", () => {
    render(
      <AiTradeAgentTrace
        group={groupWith(SESSION, [round({ analysis: "多数策略维持多头立场", state: "deciding" })])}
        activeInstId="BTC-USDT-SWAP"
      />,
    );
    expect(screen.getByText("多数策略维持多头立场")).toBeInTheDocument();
    expect(screen.getByText("研判中…")).toBeInTheDocument();
  });

  it("executed round shows the decision tool card and long-toned result", () => {
    render(
      <AiTradeAgentTrace
        group={groupWith(SESSION, [
          round({
            state: "done",
            analysis: "开多",
            toolCall: { action: "open_long", sizeFraction: 0.4, confidence: 0.72, reason: "顺势" },
            executed: { ok: true, traded: true, positionAfter: "long", action: "open_long" },
          }),
        ])}
        activeInstId="BTC-USDT-SWAP"
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
        group={groupWith(SESSION, [
          round({
            state: "done",
            toolCall: { action: "hold", reason: "共识不足" },
            executed: { ok: true, traded: false, positionAfter: "flat", action: "hold" },
            degradeReason: "low_consensus(0/1)",
          }),
        ])}
        activeInstId="BTC-USDT-SWAP"
      />,
    );
    expect(screen.getByText(/已降级观望/)).toBeInTheDocument();
  });

  it("summary round renders an expandable context-refresh node", () => {
    render(
      <AiTradeAgentTrace
        group={groupWith(SESSION, [round({ state: "done", summary: "截至第1轮：偏多" })])}
        activeInstId="BTC-USDT-SWAP"
      />,
    );
    expect(screen.getByText(/上下文已刷新/)).toBeInTheDocument();
    expect(screen.getByText("截至第1轮：偏多")).toBeInTheDocument();
  });

  it("stopped session with no traces reads as a stopped empty state", () => {
    render(
      <AiTradeAgentTrace group={groupWith({ ...SESSION, state: "stopped" })} activeInstId="BTC-USDT-SWAP" />,
    );
    expect(screen.getByText(/会话已停止/)).toBeInTheDocument();
    expect(screen.getByText("已停止")).toBeInTheDocument();
  });

  it("shows a symbol switcher for a multi-symbol group and switches focus", () => {
    const eth: AiTradeSessionRow = { ...SESSION, sessionId: "s2", instId: "ETH-USDT-SWAP" };
    const group = groupWith(SESSION, [round({ analysis: "BTC 分析", state: "deciding" })]);
    group.members["ETH-USDT-SWAP"] = {
      instId: "ETH-USDT-SWAP",
      sessionId: "s2",
      allocationPct: 50,
      session: eth,
      trades: [],
      position: null,
      decisions: [],
      traces: [round({ analysis: "ETH 分析", state: "deciding" })],
      summary: null,
    };
    render(<AiTradeAgentTrace group={group} activeInstId="BTC-USDT-SWAP" />);
    expect(screen.getByText("BTC 分析")).toBeInTheDocument();
    // 切到 ETH 标的段控 → 展示该成员轨迹（切换不丢历史）。
    fireEvent.click(screen.getByRole("tab", { name: "ETH-USDT-SWAP" }));
    expect(screen.getByText("ETH 分析")).toBeInTheDocument();
  });

  it("round header shows the trigger reason and time without a round number (R1)", () => {
    render(
      <AiTradeAgentTrace
        group={groupWith(SESSION, [
          round({
            state: "done",
            analysis: "顺势维持多头",
            signalBoard: [
              { strategyId: 1, name: "均线突破", stance: "long", latestSignal: "open_long", strength: 0.8, reason: "" },
            ],
          }),
        ])}
        activeInstId="BTC-USDT-SWAP"
      />,
    );
    expect(screen.getByText(/开多信号/)).toBeInTheDocument();
    expect(screen.getByText(/\d{2}:\d{2}/)).toBeInTheDocument();
    expect(screen.queryByText(/第\s*\d+\s*轮/)).not.toBeInTheDocument();
  });

  it("overview omits the round-number badge and reads as a refresh countdown (R1)", () => {
    render(
      <AiTradeAgentTrace
        group={groupWith(SESSION, [round({ state: "done", analysis: "常规复盘" })])}
        activeInstId="BTC-USDT-SWAP"
      />,
    );
    expect(screen.getByText(/距上下文刷新/)).toBeInTheDocument();
    expect(screen.queryByText(/第\s*\d+\s*轮/)).not.toBeInTheDocument();
  });

  it("shows multi-timeframe evidence long-to-short with stale and missing states", () => {
    const frame = (bar: string, stale: boolean) => ({
      bar, sampleCount: 80, closeTs: 1, ageSeconds: stale ? 999999 : 0, stale,
      regime: { primary: "trend_up", volatility: "normal", event: "none", confidence: 0.8, labels: [] },
      digest: { priceAction: null, volume: null, factors: null },
    });
    render(
      <AiTradeAgentTrace
        group={groupWith(SESSION, [round({
          state: "done",
          multiTimeframeContext: {
            executionBar: "1H", asOfTs: 1,
            frames: [frame("1H", false), frame("1D", true)],
            trendAlignment: { status: "bullish", bullishBars: ["1D", "1H"], bearishBars: [], rangeBars: [] },
            rsiOverboughtBars: [], rsiOversoldBars: [],
            nearestSupport: { price: 62000, distancePct: 1.59, bars: ["1D"] },
            nearestResistance: null, missingBars: ["12H", "4H"], staleBars: ["1D"],
          },
        })])}
        activeInstId="BTC-USDT-SWAP"
      />,
    );
    const frames = screen.getAllByTestId("mtf-frame");
    expect(frames[0]).toHaveTextContent("1D");
    expect(frames[1]).toHaveTextContent("1H");
    expect(frames[0]).toHaveTextContent("数据陈旧");
    expect(screen.getByText(/缺失周期.*12H.*4H/)).toBeInTheDocument();
    expect(screen.getByText(/候选支撑/)).toBeInTheDocument();
  });

  it("auto-resumes bottom-follow after 2 minutes idle and re-scrolls to latest (R2)", () => {
    vi.useFakeTimers();
    try {
      render(
        <AiTradeAgentTrace
          group={groupWith(SESSION, [round({ state: "done", analysis: "复盘" })])}
          activeInstId="BTC-USDT-SWAP"
        />,
      );
      const el = screen.getByTestId("ai-trade-trace-scroll");
      Object.defineProperty(el, "scrollHeight", { value: 1000, configurable: true });
      Object.defineProperty(el, "clientHeight", { value: 200, configurable: true });
      Object.defineProperty(el, "scrollTop", { value: 0, configurable: true });
      fireEvent.scroll(el);
      expect(screen.getByRole("button", { name: "回到最新" })).toBeInTheDocument();
      (Element.prototype.scrollIntoView as unknown as ReturnType<typeof vi.fn>).mockClear();
      act(() => {
        vi.advanceTimersByTime(120_000);
      });
      expect(screen.queryByRole("button", { name: "回到最新" })).not.toBeInTheDocument();
      expect(Element.prototype.scrollIntoView).toHaveBeenCalled();
    } finally {
      vi.useRealTimers();
    }
  });

  it("resets the 2-minute idle timer on each further scroll (R2)", () => {
    vi.useFakeTimers();
    try {
      render(
        <AiTradeAgentTrace
          group={groupWith(SESSION, [round({ state: "done", analysis: "复盘" })])}
          activeInstId="BTC-USDT-SWAP"
        />,
      );
      const el = screen.getByTestId("ai-trade-trace-scroll");
      Object.defineProperty(el, "scrollHeight", { value: 1000, configurable: true });
      Object.defineProperty(el, "clientHeight", { value: 200, configurable: true });
      Object.defineProperty(el, "scrollTop", { value: 0, configurable: true });
      fireEvent.scroll(el);
      expect(screen.getByRole("button", { name: "回到最新" })).toBeInTheDocument();
      act(() => {
        vi.advanceTimersByTime(60_000);
      });
      fireEvent.scroll(el); // 仍离底 → 重置 120s 倒计时
      act(() => {
        vi.advanceTimersByTime(90_000); // 距上次滚动仅 90s < 120s
      });
      expect(screen.getByRole("button", { name: "回到最新" })).toBeInTheDocument();
      act(() => {
        vi.advanceTimersByTime(30_000); // 距上次滚动累计 120s
      });
      expect(screen.queryByRole("button", { name: "回到最新" })).not.toBeInTheDocument();
    } finally {
      vi.useRealTimers();
    }
  });
});

describe("AgentPanel segmented view", () => {
  beforeEach(() => {
    localStorage.clear();
    useAppStore.setState({ agentConversationId: "conv-test", aiTradeGroup: null });
  });

  it("hides the 执行轨迹 tab and renders chat when there is no AI trade group", () => {
    render(<AgentPanel />);
    expect(screen.queryByRole("tab", { name: /执行轨迹/ })).not.toBeInTheDocument();
    expect(screen.getByText("AI 交易助手")).toBeInTheDocument(); // 聊天空态逐字不变
  });

  it("auto-switches to the trace view when a group appears", () => {
    render(<AgentPanel />);
    act(() => {
      useAppStore.setState({ aiTradeGroup: groupWith(SESSION) });
    });
    expect(screen.getByRole("tab", { name: /执行轨迹/ })).toBeInTheDocument();
    expect(screen.getByText("运行中")).toBeInTheDocument(); // 轨迹概览条已呈现
  });

  it("shows no round-number badge on the 执行轨迹 tab (R1, P0-1)", () => {
    render(<AgentPanel />);
    act(() => {
      useAppStore.setState({ aiTradeGroup: groupWith(SESSION) });
    });
    expect(screen.getByRole("tab", { name: /执行轨迹/ })).toBeInTheDocument();
    expect(screen.queryByText(/第\s*\d+\s*轮/)).not.toBeInTheDocument();
  });

  it("respects a manual switch back to 对话 and does not auto-steal", () => {
    render(<AgentPanel />);
    act(() => {
      useAppStore.setState({ aiTradeGroup: groupWith(SESSION) });
    });
    fireEvent.click(screen.getByRole("tab", { name: "对话" }));
    expect(screen.queryByText("运行中")).not.toBeInTheDocument();
    expect(screen.getByText("AI 交易助手")).toBeInTheDocument();
  });

  it("falls back to 对话 when the group clears", () => {
    render(<AgentPanel />);
    act(() => {
      useAppStore.setState({ aiTradeGroup: groupWith(SESSION) });
    });
    act(() => {
      useAppStore.setState({ aiTradeGroup: null });
    });
    expect(screen.queryByRole("tab", { name: /执行轨迹/ })).not.toBeInTheDocument();
    expect(screen.getByText("AI 交易助手")).toBeInTheDocument();
  });
});
