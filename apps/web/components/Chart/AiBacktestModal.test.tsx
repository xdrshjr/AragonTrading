import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { AiBacktestJobView } from "@/lib/useAiBacktestJob";
import AiBacktestModal from "./AiBacktestModal";

// 受控注入回测作业视图（隔离 WS / GET），并桩掉 EquityChart（lightweight-charts 不进 jsdom）与取消 API。
let mockView: AiBacktestJobView | null = null;
vi.mock("@/lib/useAiBacktestJob", () => ({ useAiBacktestJob: () => mockView }));
vi.mock("./EquityChart", () => ({ default: () => <div data-testid="equity-chart" /> }));
const cancelMock = vi.fn().mockResolvedValue({});
vi.mock("@/lib/aiBacktestApi", () => ({
  aiBacktestApi: { cancel: (id: string) => cancelMock(id) },
  aiBacktestWS: vi.fn(),
}));

const START = 1_700_000_000;

function runningView(): AiBacktestJobView {
  return {
    jobId: "aibt_run",
    status: {
      jobId: "aibt_run",
      state: "running",
      phase: "running",
      progress: 0.5,
      barIndex: 5,
      totalBars: 10,
      decisionRound: 3,
      plannedRounds: 6,
      instId: "BTC-USDT-SWAP",
      bar: "1H",
      strategyIds: [1],
      startTs: START,
      endTs: START + 10 * 3600,
      startedTs: START,
      runId: null,
      result: null,
    },
    phase: "running",
    progress: {
      barIndex: 5,
      totalBars: 10,
      ts: START,
      lastPx: 100,
      pct: 0.5,
      decisionRound: 3,
      plannedRounds: 6,
      equity: 10100,
      positionSide: "long",
      sessionPnlUsd: 100,
    },
    decisions: [
      {
        ts: START,
        round: 1,
        action: "open_long",
        confidence: 0.8,
        reason: "趋势向上，开多",
        signalBoard: [],
        executed: { submitted: true } as never,
        marketContext: null,
        consensus: null,
        degradeReason: null,
      },
    ],
    summaries: [],
    trades: [],
    equity: [
      { ts: START, equity: 10000 },
      { ts: START + 3600, equity: 10100 },
    ],
    result: null,
    state: "running",
    connected: true,
    error: null,
  };
}

function completeView(): AiBacktestJobView {
  const base = runningView();
  return {
    ...base,
    jobId: "aibt_done",
    state: "completed",
    result: {
      runId: 42,
      metrics: {
        totalReturn: 0.1,
        winRate: 0.5,
        profitFactor: 1.2,
        maxDrawdown: -0.05,
        sharpe: 1.0,
        tradeCount: 4,
        avgPnl: 5,
      },
      aiStats: {
        decisionRounds: 6,
        actionCounts: { open_long: 3, open_short: 0, close: 3, hold: 0 },
        holdRate: 0,
        avgConfidence: 0.8,
        degradedRounds: 0,
        timeoutRounds: 0,
        summaryCount: 2,
        finalSummary: "偏多",
        decisionEveryBars: 1,
        plannedRounds: 6,
        reachedRoundCap: false,
      },
      equity: [{ ts: START, equity: 10000, drawdown: 0 }],
      trades: [
        { ts: START, side: "long", price: 100, qty: 1, fee: 0.1, pnl: 0, positionAfter: 1, reason: "r" },
      ],
      decisionsDigest: base.decisions,
      summaries: [{ round: 3, ts: START, summary: "中途摘要" }],
      startTs: START,
      endTs: START + 10 * 3600,
      barsProcessed: 6,
      notes: ["已触发会话级止损，末段仅盯市"],
    },
  };
}

beforeEach(() => vi.clearAllMocks());
afterEach(() => cleanup());

describe("AiBacktestModal", () => {
  it("renders the running view with progress head, decision log and cancel", () => {
    mockView = runningView();
    render(<AiBacktestModal jobId="aibt_run" onClose={vi.fn()} />);
    expect(screen.getByText("AI 逐根决策")).toBeInTheDocument(); // phase label
    expect(screen.getByText(/第 5\/10 根/)).toBeInTheDocument();
    expect(screen.getByText("趋势向上，开多")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "取消回测" })).toBeInTheDocument();
  });

  it("cancel button calls aiBacktestApi.cancel", () => {
    mockView = runningView();
    render(<AiBacktestModal jobId="aibt_run" onClose={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: "取消回测" }));
    expect(cancelMock).toHaveBeenCalledWith("aibt_run");
  });

  it("renders the complete view with metrics, AI stats, trades and run link", () => {
    mockView = completeView();
    render(<AiBacktestModal jobId="aibt_done" onClose={vi.fn()} />);
    expect(screen.getAllByText("已完成").length).toBeGreaterThan(0); // 状态徽标 + 进度头
    expect(screen.getByText("决策轮数")).toBeInTheDocument(); // AI 统计卡
    expect(screen.getByText("动作分布")).toBeInTheDocument();
    expect(screen.getByText(/运行历史 ID：42/)).toBeInTheDocument();
    expect(screen.getByText(/已触发会话级止损/)).toBeInTheDocument(); // notes
    // 完成态不显示取消按钮
    expect(screen.queryByRole("button", { name: "取消回测" })).toBeNull();
  });
});
