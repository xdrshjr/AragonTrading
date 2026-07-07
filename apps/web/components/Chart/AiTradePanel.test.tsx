import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { AiTradeGroupState, AiTradeSessionRow } from "@/lib/aiTradeTypes";
import { useAppStore } from "@/lib/store";
import type { Instrument } from "@/lib/types";
import AiTradePanel from "./AiTradePanel";

// AiTradePanel 内部订阅 store.aiTradeGroup（非 props 注入），故用例须 seed 分组并逐例复位。
function makeSession(instId: string, over: Partial<AiTradeSessionRow> = {}): AiTradeSessionRow {
  return {
    sessionId: `sess-${instId}`,
    runId: 1,
    state: "running",
    instId,
    bar: "1H",
    strategyIds: [1],
    strategyNames: ["S"],
    budgetUsd: 500,
    leverage: 3,
    contextRefreshRounds: 10,
    roundsCompleted: 2,
    roundsSinceSummary: 1,
    positionSide: "long",
    sessionPnlUsd: 12.3,
    startedTs: 0,
    ...over,
  };
}

function makeGroup(sessions: AiTradeSessionRow[]): AiTradeGroupState {
  const members: AiTradeGroupState["members"] = {};
  for (const s of sessions) {
    members[s.instId] = {
      instId: s.instId,
      sessionId: s.sessionId,
      allocationPct: Math.round(100 / sessions.length),
      session: s,
      trades: [],
      position: null,
      decisions: [],
      traces: [],
      summary: null,
    };
  }
  return {
    groupId: "g1",
    bar: "1H",
    strategyIds: [1],
    strategyNames: ["S"],
    totalBudgetUsd: 1000,
    leverage: 3,
    contextRefreshRounds: 10,
    state: "running",
    startedTs: 0,
    members,
  };
}

const ROWS: Instrument[] = [
  { instId: "BTC-USDT-SWAP", instType: "SWAP", base: "BTC", quote: "USDT", last: 68420, changePct: 0.012 },
  { instId: "ETH-USDT-SWAP", instType: "SWAP", base: "ETH", quote: "USDT", last: 3820, changePct: -0.004 },
];

const noop = () => {};

afterEach(() => {
  cleanup();
  useAppStore.setState({ aiTradeGroup: null });
});

describe("AiTradePanel", () => {
  it("renders nothing when there is no AI trade group", () => {
    useAppStore.setState({ aiTradeGroup: null });
    const { container } = render(
      <AiTradePanel rows={ROWS} active="" livePrice={null} changePct={null} onSelect={noop} />,
    );
    expect(container).toBeEmptyDOMElement();
  });

  it("renders the group header and a single auto-trade symbol row, without execution details", () => {
    useAppStore.setState({ aiTradeGroup: makeGroup([makeSession("BTC-USDT-SWAP")]) });
    render(
      <AiTradePanel rows={ROWS} active="BTC-USDT-SWAP" livePrice={null} changePct={null} onSelect={noop} />,
    );
    expect(screen.getByText("AI 自动交易")).toBeInTheDocument();
    expect(screen.getByText("运行中")).toBeInTheDocument();
    expect(screen.getByText("自动交易标的")).toBeInTheDocument();
    expect(screen.getByText("BTC-USDT-SWAP")).toBeInTheDocument();
    expect(screen.getByText("1H")).toBeInTheDocument();
    // 执行情况（决策日志 / 策略信号板 / 上下文摘要）已迁到右侧「AI 助手」，左侧不再呈现。
    expect(screen.queryByText(/决策日志/)).not.toBeInTheDocument();
    expect(screen.queryByText("策略信号板")).not.toBeInTheDocument();
    expect(screen.queryByText("上下文摘要")).not.toBeInTheDocument();
  });

  it("renders one row per member for a multi-symbol group", () => {
    useAppStore.setState({
      aiTradeGroup: makeGroup([makeSession("BTC-USDT-SWAP"), makeSession("ETH-USDT-SWAP")]),
    });
    render(
      <AiTradePanel rows={ROWS} active="BTC-USDT-SWAP" livePrice={null} changePct={null} onSelect={noop} />,
    );
    expect(screen.getByText("BTC-USDT-SWAP")).toBeInTheDocument();
    expect(screen.getByText("ETH-USDT-SWAP")).toBeInTheDocument();
    expect(screen.getByText("2 个标的")).toBeInTheDocument();
  });

  it("calls onSelect with the member instId when its symbol row is clicked", () => {
    useAppStore.setState({
      aiTradeGroup: makeGroup([makeSession("BTC-USDT-SWAP"), makeSession("ETH-USDT-SWAP")]),
    });
    const onSelect = vi.fn();
    render(
      <AiTradePanel rows={ROWS} active="BTC-USDT-SWAP" livePrice={null} changePct={null} onSelect={onSelect} />,
    );
    fireEvent.click(screen.getByText("ETH-USDT-SWAP"));
    expect(onSelect).toHaveBeenCalledWith("ETH-USDT-SWAP");
  });
});
