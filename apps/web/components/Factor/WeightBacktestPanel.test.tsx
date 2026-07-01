import "@testing-library/jest-dom/vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { weightModelApi } from "@/lib/weightModelApi";
import type { WeightBacktestResult } from "@/lib/weightModelTypes";
import WeightBacktestPanel from "./WeightBacktestPanel";

// slim 蜡烛图经 next/dynamic 引入；测试内以 stub 替代（规避 lightweight-charts 在 jsdom 的异步加载不确定性）。
vi.mock("next/dynamic", () => ({
  default: () => () => <div data-testid="bt-chart" />,
}));
vi.mock("@/lib/weightModelApi", () => ({ weightModelApi: { backtest: vi.fn() } }));

const api = weightModelApi as unknown as Record<string, ReturnType<typeof vi.fn>>;

function result(): WeightBacktestResult {
  return {
    candles: [{ ts: 1_700_000_000, o: 1, h: 1.1, l: 0.9, c: 1.05, vol: 100 }],
    trades: [
      { ts: 1_700_003_600, side: "long", price: 1.05, qty: 10, fee: 0.01, pnl: 0, positionAfter: 10, reason: "复合得分多头" },
      { ts: 1_700_007_200, side: "close", price: 1.2, qty: 10, fee: 0.01, pnl: 1.5, positionAfter: 0, reason: "得分落入退出带" },
    ],
    equity: [{ ts: 1_700_003_600, equity: 10000, drawdown: 0 }],
    metrics: { totalReturn: 0.15, winRate: 1, profitFactor: 3, maxDrawdown: -0.02, sharpe: 1.5, tradeCount: 1, avgPnl: 1.5 },
    meta: { instId: "BTC-USDT-SWAP", bar: "1H", startTs: 1_700_000_000, endTs: 1_700_100_000, horizon: 6, effectiveCandleCount: 1, initialCapital: 10000 },
  };
}

function wrap(node: ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={client}>{node}</QueryClientProvider>);
}

beforeEach(() => {
  vi.clearAllMocks();
});
afterEach(cleanup);

describe("WeightBacktestPanel (weight-model-backtest-replay §六)", () => {
  it("autoRun fires the backtest exactly once on mount and renders metrics + chart + trades", async () => {
    api.backtest.mockResolvedValue(result());
    wrap(<WeightBacktestPanel modelId={3} runId={5} autoRun />);

    await waitFor(() => expect(api.backtest).toHaveBeenCalledWith(3, 5));
    expect(api.backtest).toHaveBeenCalledTimes(1); // 仅消费一次
    await waitFor(() => expect(screen.getByTestId("bt-chart")).toBeTruthy());
    expect(screen.getByText("总收益率")).toBeTruthy(); // MetricsCards 渲染
    expect(screen.getByText("开多")).toBeTruthy(); // TradesTable 渲染逐笔
    expect(screen.getByText("平仓")).toBeTruthy();
    // 区间提示由 meta 回填（样本内）。
    expect(screen.getByText(/回测区间 = 训练区间/)).toBeTruthy();
  });

  it("does not fire until 回测 is clicked when autoRun is false", async () => {
    api.backtest.mockResolvedValue(result());
    wrap(<WeightBacktestPanel modelId={3} runId={5} />);

    expect(api.backtest).not.toHaveBeenCalled();
    expect(screen.getByText(/点「回测」在该次训练权重上/)).toBeTruthy(); // idle 引导态
    fireEvent.click(screen.getByRole("button", { name: "回测" }));
    await waitFor(() => expect(api.backtest).toHaveBeenCalledWith(3, 5));
    await waitFor(() => expect(screen.getByTestId("bt-chart")).toBeTruthy());
  });

  it("renders a friendly error alert when the backtest fails", async () => {
    api.backtest.mockRejectedValue(Object.assign(new Error("stale"), { code: "FACTOR_WEIGHTS_STALE" }));
    wrap(<WeightBacktestPanel modelId={3} runId={5} autoRun />);

    await waitFor(() => expect(screen.getByRole("alert")).toBeTruthy());
    expect(screen.getByText(/成员已变化/)).toBeTruthy(); // codeLabel 友好映射
    expect(screen.getByRole("button", { name: "重新回测" })).toBeTruthy(); // 可重试
  });
});
