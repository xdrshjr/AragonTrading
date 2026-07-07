import "@testing-library/jest-dom/vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { BacktestConfig, BacktestResult } from "@/lib/types";
import BacktestConfigModal from "./BacktestConfigModal";

vi.mock("@/lib/api", () => ({
  api: { equity: vi.fn().mockResolvedValue([]) },
}));

const CONFIG: BacktestConfig = {
  bar: "1H",
  rangePreset: "custom",
  startTs: 1_700_000_000,
  endTs: 1_700_086_400,
  initialCapital: 10000,
  feesBps: 5,
  slippageBps: 2,
  leverage: 1,
};

const RESULT: BacktestResult = {
  runId: 101,
  metrics: {
    totalReturn: 0.12,
    winRate: 0.55,
    profitFactor: 1.6,
    maxDrawdown: -0.04,
    sharpe: 1.25,
    tradeCount: 11,
    avgPnl: 8,
  },
  extendedMetrics: {
    annualizedReturn: 0.31,
    annualizedVolatility: 0.42,
    sortino: 1.9,
    calmar: 3.8,
    maxDrawdownBars: 57,
    maxDrawdownSecs: 205200,
    timeInMarketPct: 0.63,
    avgWin: 6.2,
    avgLoss: 3.1,
    payoffRatio: 2.0,
    largestWin: 40.1,
    largestLoss: -22.3,
    maxConsecutiveWins: 6,
    maxConsecutiveLosses: 3,
    avgHoldBars: 8.4,
    tradesPerWeek: 5.2,
  },
  effectiveCandleCount: 200,
  requestedBars: 720,
  truncated: true,
};

afterEach(cleanup);

function renderModal(props: Partial<React.ComponentProps<typeof BacktestConfigModal>> = {}) {
  const onClose = vi.fn();
  const onRun = vi.fn();
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={queryClient}>
      <BacktestConfigModal
        open
        onClose={onClose}
        strategyName="Alpha Trend"
        instId="BTC-USDT-SWAP"
        initialConfig={CONFIG}
        running={false}
        onRun={onRun}
        {...props}
      />
    </QueryClientProvider>,
  );
  return { onClose, onRun };
}

describe("BacktestConfigModal", () => {
  it("renders the title, instrument and preset / bar chips", () => {
    renderModal();
    expect(screen.getByRole("heading", { name: "运行回测" })).toBeInTheDocument();
    expect(screen.getByText(/BTC-USDT-SWAP/)).toBeInTheDocument();
    // 区间预设 + 自定义 chips。
    expect(screen.getByRole("button", { name: "近 30 天" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "自定义" })).toBeInTheDocument();
    // K 线周期 chips。
    expect(screen.getByRole("button", { name: "1 小时" })).toBeInTheDocument();
    // 运行按钮存在且可用（区间合法）。
    expect(screen.getByRole("button", { name: "开始回测" })).toBeEnabled();
  });

  it("disables run and shows the reason when the range is invalid", () => {
    renderModal({ initialConfig: { ...CONFIG, startTs: CONFIG.endTs, endTs: CONFIG.startTs } });
    expect(screen.getByRole("button", { name: "开始回测" })).toBeDisabled();
    expect(screen.getByText(/起始时间必须早于结束时间/)).toBeInTheDocument();
  });

  it("calls onRun with the current config when run is clicked", () => {
    const { onRun } = renderModal();
    fireEvent.click(screen.getByRole("button", { name: "开始回测" }));
    expect(onRun).toHaveBeenCalledTimes(1);
    expect(onRun).toHaveBeenCalledWith(
      expect.objectContaining({
        bar: "1H",
        rangePreset: "custom",
        startTs: 1_700_000_000,
        endTs: 1_700_086_400,
        initialCapital: 10000,
        feesBps: 5,
        slippageBps: 2,
        leverage: 1,
      }),
    );
  });

  it("shows a running label while the backtest is pending", () => {
    renderModal({ running: true });
    expect(screen.getByRole("button", { name: "回测中…" })).toBeInTheDocument();
    // 运行中结果区不显示空态占位（页脚提示常驻，故按空态独有文案精确匹配）。
    expect(screen.queryByText(/指标将在此呈现/)).not.toBeInTheDocument();
  });

  it("renders the metrics grid and coverage banner after a run completes", () => {
    renderModal({ result: RESULT });
    // 结果区在弹窗内触发运行后才展示（避免跨策略陈旧结果误显）。
    fireEvent.click(screen.getByRole("button", { name: "开始回测" }));
    expect(screen.getByText("12.00%")).toBeInTheDocument(); // 总收益
    expect(screen.getByText("年化收益")).toBeInTheDocument(); // 扩展指标
    expect(screen.getByText(/仅覆盖最近 200 \/ 720 根/)).toBeInTheDocument(); // 覆盖横幅
  });

  it("renders the error state when a backtest fails", () => {
    renderModal({ errorMessage: "boom" });
    fireEvent.click(screen.getByRole("button", { name: "开始回测" }));
    expect(screen.getByText(/回测失败：boom/)).toBeInTheDocument();
  });

  it("calls onClose on Escape and on overlay click", () => {
    const { onClose } = renderModal();
    fireEvent.keyDown(window, { key: "Escape" });
    expect(onClose).toHaveBeenCalledTimes(1);

    const overlay = screen.getByRole("dialog").parentElement as HTMLElement;
    fireEvent.click(overlay);
    expect(onClose).toHaveBeenCalledTimes(2);
  });
});
