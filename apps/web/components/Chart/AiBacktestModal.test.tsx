import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type {
  AiBacktestBenchmark,
  AiBacktestExtendedMetrics,
} from "@/lib/aiBacktestTypes";
import type { AiBacktestJobView } from "@/lib/useAiBacktestJob";
import AiBacktestModal from "./AiBacktestModal";

// 受控注入回测作业视图（隔离 WS / GET），并桩掉 EquityChart（lightweight-charts 不进 jsdom）、取消 API、导出。
let mockView: AiBacktestJobView | null = null;
vi.mock("@/lib/useAiBacktestJob", () => ({ useAiBacktestJob: () => mockView }));
vi.mock("./EquityChart", () => ({ default: () => <div data-testid="equity-chart" /> }));
const cancelMock = vi.fn().mockResolvedValue({});
vi.mock("@/lib/aiBacktestApi", () => ({
  aiBacktestApi: { cancel: (id: string) => cancelMock(id) },
  aiBacktestWS: vi.fn(),
}));
const downloadCsvMock = vi.fn();
const downloadJsonMock = vi.fn();
vi.mock("@/lib/export", () => ({
  downloadCsv: (...a: unknown[]) => downloadCsvMock(...a),
  downloadJson: (...a: unknown[]) => downloadJsonMock(...a),
  tradesToCsv: () => "csv",
  equityToCsv: () => "csv",
  resultToJson: () => "{}",
}));

const START = 1_700_000_000;

const EXT: AiBacktestExtendedMetrics = {
  annualizedReturn: 0.42, annualizedVolatility: 0.3, sortino: 1.9, calmar: 1.3,
  maxDrawdownBars: 34, maxDrawdownSecs: 122400, timeInMarketPct: 0.61,
  avgWin: 128.4, avgLoss: 71.2, payoffRatio: 1.8, largestWin: 540, largestLoss: -260,
  maxConsecutiveWins: 5, maxConsecutiveLosses: 3, avgHoldBars: 7.4, tradesPerWeek: 6.2,
};
const BENCH: AiBacktestBenchmark = {
  equity: [{ ts: START, equity: 10000, drawdown: 0 }],
  totalReturn: 0.05, maxDrawdown: -0.08, sharpe: 0.7, annualizedReturn: 0.2, alpha: 0.05,
};

function runningView(): AiBacktestJobView {
  return {
    jobId: "aibt_run",
    status: {
      jobId: "aibt_run", state: "running", phase: "running", progress: 0.5, barIndex: 5,
      totalBars: 10, decisionRound: 3, plannedRounds: 6, instId: "BTC-USDT-SWAP", bar: "1H",
      strategyIds: [1], startTs: START, endTs: START + 10 * 3600, startedTs: START,
      runId: null, result: null,
    },
    phase: "running",
    progress: {
      barIndex: 5, totalBars: 10, ts: START, lastPx: 100, pct: 0.5, decisionRound: 3,
      plannedRounds: 6, equity: 10100, positionSide: "long", sessionPnlUsd: 100,
    },
    decisions: [
      {
        ts: START, round: 1, action: "open_long", confidence: 0.8, reason: "趋势向上，开多",
        signalBoard: [], executed: { submitted: true } as never, marketContext: null,
        consensus: null, degradeReason: null,
      },
    ],
    summaries: [],
    trades: [],
    equity: [{ ts: START, equity: 10000 }, { ts: START + 3600, equity: 10100 }],
    result: null,
    state: "running",
    connected: true,
    error: null,
  };
}

function completeView(
  opts: { ext?: boolean; bench?: boolean; guard?: { enabled: boolean; exits: number } }
    = { ext: true, bench: true },
): AiBacktestJobView {
  const base = runningView();
  return {
    ...base,
    jobId: "aibt_done",
    state: "completed",
    result: {
      runId: 42,
      metrics: {
        totalReturn: 0.1, winRate: 0.5, profitFactor: 1.2, maxDrawdown: -0.05, sharpe: 1.0,
        tradeCount: 4, avgPnl: 5,
      },
      aiStats: {
        decisionRounds: 6, actionCounts: { open_long: 3, open_short: 0, close: 3, hold: 0 },
        holdRate: 0, avgConfidence: 0.8, degradedRounds: 0, timeoutRounds: 0, summaryCount: 2,
        finalSummary: "偏多", decisionEveryBars: 1, plannedRounds: 6, reachedRoundCap: false,
        profitGuardEnabled: opts.guard?.enabled,
        profitGuardExits: opts.guard?.exits,
      },
      equity: [
        { ts: START, equity: 10000, drawdown: 0 },
        { ts: START + 3600, equity: 9900, drawdown: -0.01 },
      ],
      trades: [
        { ts: START, side: "long", price: 100, qty: 1, fee: 0.1, pnl: 0, positionAfter: 1, reason: "r" },
      ],
      decisionsDigest: base.decisions,
      summaries: [{ round: 3, ts: START, summary: "中途摘要" }],
      startTs: START,
      endTs: START + 10 * 3600,
      barsProcessed: 6,
      notes: ["已触发会话级止损，末段仅盯市"],
      extendedMetrics: opts.ext ? EXT : undefined,
      benchmark: opts.bench ? BENCH : undefined,
    },
  };
}

beforeEach(() => vi.clearAllMocks());
afterEach(() => cleanup());

describe("AiBacktestModal · running", () => {
  it("renders progress head, decision log and cancel", () => {
    mockView = runningView();
    render(<AiBacktestModal jobId="aibt_run" onClose={vi.fn()} />);
    expect(screen.getByText("AI 逐根决策")).toBeInTheDocument();
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
});

describe("AiBacktestModal · complete dashboard", () => {
  it("defaults to the overview tab with KPI hero, alpha, notes and run link", () => {
    mockView = completeView();
    render(<AiBacktestModal jobId="aibt_done" onClose={vi.fn()} />);
    expect(screen.getByText("总收益")).toBeInTheDocument(); // KPIHero
    expect(screen.getByText("超额 α")).toBeInTheDocument(); // 有基准 → 显示 alpha
    expect(screen.getByText(/已触发会话级止损/)).toBeInTheDocument(); // notes
    expect(screen.getByText(/运行历史 ID：42/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "取消回测" })).toBeNull();
  });

  it("advanced tab shows extended metrics with a non-colliding payoff label", () => {
    mockView = completeView();
    render(<AiBacktestModal jobId="aibt_done" onClose={vi.fn()} />);
    fireEvent.click(screen.getByRole("tab", { name: "进阶指标" }));
    expect(screen.getByText("索提诺")).toBeInTheDocument();
    // profitFactor 卡（MetricsCards「盈亏比」）与 payoffRatio 卡（「盈亏比·赔率」）标签不重名（P1-1）
    const payoff = screen.getByText("盈亏比·赔率");
    const profitFactor = screen.getByText("盈亏比");
    expect(payoff).toBeInTheDocument();
    expect(profitFactor).not.toBe(payoff);
    expect(screen.getByText("策略 vs 买入持有")).toBeInTheDocument(); // BenchmarkCompareCard
  });

  it("trades tab export buttons call downloadCsv / downloadJson", () => {
    mockView = completeView();
    render(<AiBacktestModal jobId="aibt_done" onClose={vi.fn()} />);
    fireEvent.click(screen.getByRole("tab", { name: "成交" }));
    fireEvent.click(screen.getByRole("button", { name: "导出成交 CSV" }));
    expect(downloadCsvMock).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByRole("button", { name: "导出完整 JSON" }));
    expect(downloadJsonMock).toHaveBeenCalledTimes(1);
  });

  it("ai-decision tab shows decision stats and action distribution", () => {
    mockView = completeView();
    render(<AiBacktestModal jobId="aibt_done" onClose={vi.fn()} />);
    fireEvent.click(screen.getByRole("tab", { name: "AI 决策" }));
    expect(screen.getByText("决策轮数")).toBeInTheDocument();
    expect(screen.getByText("动作分布")).toBeInTheDocument();
  });

  // 保护层免责卡片的门控是「本次回测是否真的装配了保护层」，不是「触发了几次」：未装配的回测同样
  // 下发 profitGuardExits: 0，照它渲染就等于在一次没有防线的回测上宣称有防线（RV-1）。
  it("profit-guard caveat card only shows when the backtest actually assembled the guard", () => {
    mockView = completeView({ ext: true, bench: true, guard: { enabled: true, exits: 2 } });
    const { unmount } = render(<AiBacktestModal jobId="aibt_done" onClose={vi.fn()} />);
    fireEvent.click(screen.getByRole("tab", { name: "AI 决策" }));
    expect(screen.getByTestId("bt-profit-guard-exits").textContent).toContain("2");
    expect(screen.getByText(/跳空穿越按开盘价成交/)).toBeInTheDocument();
    unmount();

    mockView = completeView({ ext: true, bench: true, guard: { enabled: false, exits: 0 } });
    render(<AiBacktestModal jobId="aibt_done" onClose={vi.fn()} />);
    fireEvent.click(screen.getByRole("tab", { name: "AI 决策" }));
    expect(screen.queryByTestId("bt-profit-guard-exits")).toBeNull();
    expect(screen.queryByText(/保守近似/)).toBeNull();
  });

  it("gracefully degrades for old results lacking extendedMetrics / benchmark", () => {
    mockView = completeView({ ext: false, bench: false });
    render(<AiBacktestModal jobId="aibt_done" onClose={vi.fn()} />);
    expect(screen.getByText("总收益")).toBeInTheDocument(); // 概览仍渲染
    expect(screen.queryByText("超额 α")).toBeNull(); // 无基准 → 隐藏 alpha
    fireEvent.click(screen.getByRole("tab", { name: "进阶指标" }));
    expect(screen.getByText("核心指标")).toBeInTheDocument(); // 七项仍在
    expect(screen.queryByText("盈亏比·赔率")).toBeNull(); // 扩展网格优雅隐藏
    expect(screen.queryByText("策略 vs 买入持有")).toBeNull(); // 基准对比隐藏
  });
});
