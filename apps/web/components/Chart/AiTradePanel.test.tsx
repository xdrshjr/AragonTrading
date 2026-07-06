import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { AiTradeSessionRow } from "@/lib/aiTradeTypes";
import { useAppStore } from "@/lib/store";
import type { Instrument } from "@/lib/types";
import AiTradePanel from "./AiTradePanel";

// AiTradePanel 内部订阅 store.aiTradeSession（非 props 注入），故用例须 seed 会话并逐例复位（P2-4）。
const SESSION: AiTradeSessionRow = {
  sessionId: "s1",
  runId: 1,
  state: "running",
  instId: "BTC-USDT-SWAP",
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
};

const ROWS: Instrument[] = [
  {
    instId: "BTC-USDT-SWAP",
    instType: "SWAP",
    base: "BTC",
    quote: "USDT",
    last: 68420,
    changePct: 0.012,
  },
];

const noop = () => {};

afterEach(() => {
  cleanup();
  useAppStore.setState({ aiTradeSession: null });
});

describe("AiTradePanel", () => {
  it("renders nothing when there is no AI trade session", () => {
    useAppStore.setState({ aiTradeSession: null });
    const { container } = render(
      <AiTradePanel rows={ROWS} active="" livePrice={null} changePct={null} onSelect={noop} />,
    );
    expect(container).toBeEmptyDOMElement();
  });

  it("renders the session header and auto-trade symbol row, without execution details", () => {
    useAppStore.setState({ aiTradeSession: SESSION });
    render(
      <AiTradePanel
        rows={ROWS}
        active="BTC-USDT-SWAP"
        livePrice={null}
        changePct={null}
        onSelect={noop}
      />,
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

  it("calls onSelect with the session instId when the symbol row is clicked", () => {
    useAppStore.setState({ aiTradeSession: SESSION });
    const onSelect = vi.fn();
    render(
      <AiTradePanel
        rows={ROWS}
        active="BTC-USDT-SWAP"
        livePrice={null}
        changePct={null}
        onSelect={onSelect}
      />,
    );
    fireEvent.click(screen.getByText("BTC-USDT-SWAP"));
    expect(onSelect).toHaveBeenCalledWith("BTC-USDT-SWAP");
  });
});
