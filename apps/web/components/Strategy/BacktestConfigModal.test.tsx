import "@testing-library/jest-dom/vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
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
  const base: React.ComponentProps<typeof BacktestConfigModal> = {
    open: true,
    onClose,
    strategyName: "Alpha Trend",
    instId: "BTC-USDT-SWAP",
    initialConfig: CONFIG,
    running: false,
    onRun,
    ...props,
  };
  const view = render(
    <QueryClientProvider client={queryClient}>
      <BacktestConfigModal {...base} />
    </QueryClientProvider>,
  );
  // rerender 保留组件内部 step 状态，用于模拟父层 running/result 的异步到达。
  const rerender = (next: Partial<React.ComponentProps<typeof BacktestConfigModal>>) =>
    view.rerender(
      <QueryClientProvider client={queryClient}>
        <BacktestConfigModal {...base} {...next} />
      </QueryClientProvider>,
    );
  return { onClose, onRun, rerender };
}

describe("BacktestConfigModal", () => {
  it("renders the title, instrument, chips and the config step as current", () => {
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
    // 步骤指示器：① 配置 为当前步。
    expect(screen.getByRole("button", { name: /配置/ })).toHaveAttribute("aria-current", "step");
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

  it("advances to a dedicated result step with metrics and coverage banner", () => {
    renderModal({ result: RESULT });
    // 结果独立成第二步，仅在弹窗内触发运行后才进入（避免跨策略陈旧结果误显）。
    fireEvent.click(screen.getByRole("button", { name: "开始回测" }));
    expect(screen.getByText("回测结果")).toBeInTheDocument(); // 第二步小标题
    expect(screen.getByText("12.00%")).toBeInTheDocument(); // 总收益
    expect(screen.getByText("年化收益")).toBeInTheDocument(); // 扩展指标
    expect(screen.getByText(/仅覆盖最近 200 \/ 720 根/)).toBeInTheDocument(); // 覆盖横幅（子串匹配）
    // 第二步页脚出现「返回上一步」；步骤指示器「② 结果」为当前步。
    expect(screen.getByRole("button", { name: /返回上一步/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /结果/ })).toHaveAttribute("aria-current", "step");
  });

  it("returns to the config step without re-running when back is clicked", () => {
    const { onRun } = renderModal({ result: RESULT });
    fireEvent.click(screen.getByRole("button", { name: "开始回测" }));
    fireEvent.click(screen.getByRole("button", { name: /返回上一步/ }));
    // 回到第一步：区间 chips 再次可见，指标网格从文档移除。
    expect(screen.getByRole("button", { name: "近 30 天" })).toBeInTheDocument();
    expect(screen.queryByText("12.00%")).not.toBeInTheDocument();
    expect(onRun).toHaveBeenCalledTimes(1); // 返回不触发重跑
  });

  it("does not render results until the user runs from the modal", () => {
    renderModal({ result: RESULT });
    // 未点运行：仍在第一步，指标 / 覆盖横幅不在文档（守防陈旧）。
    expect(screen.queryByText("12.00%")).not.toBeInTheDocument();
    expect(screen.queryByText(/仅覆盖最近 200 \/ 720 根/)).not.toBeInTheDocument();
    expect(screen.queryByText("回测结果")).not.toBeInTheDocument();
  });

  it("shows the running skeleton on the result step while pending", () => {
    const { rerender } = renderModal();
    fireEvent.click(screen.getByRole("button", { name: "开始回测" })); // 推进到第二步
    rerender({ running: true }); // 模拟父层 mutation 置真
    // 正向断言：运行骨架出现；已删除的空态占位文案不再出现。
    expect(document.querySelectorAll(".skeleton").length).toBeGreaterThan(0);
    expect(screen.queryByText(/指标将在此呈现/)).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /返回上一步/ })).toBeEnabled();
  });

  it("keeps the run button labeled 回测中… on the config step when running externally", () => {
    // R-4：父层外部把 running 置真而用户仍在第一步（未点运行）→ 不自动跳步。
    renderModal({ running: true });
    expect(screen.getByRole("button", { name: "回测中…" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "近 30 天" })).toBeInTheDocument(); // 仍在配置屏
    expect(screen.queryByText("回测结果")).not.toBeInTheDocument();
  });

  it("renders the error state and a retry action on the result step", () => {
    renderModal({ errorMessage: "boom" });
    fireEvent.click(screen.getByRole("button", { name: "开始回测" }));
    expect(screen.getByText(/回测失败：boom/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "重新回测" })).toBeInTheDocument();
  });

  it("calls onClose on Escape and on overlay click", () => {
    const { onClose } = renderModal();
    fireEvent.keyDown(window, { key: "Escape" });
    expect(onClose).toHaveBeenCalledTimes(1);

    const overlay = screen.getByRole("dialog").parentElement as HTMLElement;
    fireEvent.click(overlay);
    expect(onClose).toHaveBeenCalledTimes(2);
  });

  it("moves focus to the result heading when advancing to the result step", () => {
    renderModal({ result: RESULT });
    fireEvent.click(screen.getByRole("button", { name: "开始回测" }));
    expect(screen.getByText("回测结果")).toHaveFocus();
  });

  it("lets the user revisit existing results via the step indicator without re-running", () => {
    const { onRun } = renderModal({ result: RESULT });
    fireEvent.click(screen.getByRole("button", { name: "开始回测" })); // → 第二步
    expect(screen.getByText("12.00%")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /返回上一步/ })); // → 第一步
    expect(screen.queryByText("12.00%")).not.toBeInTheDocument();
    // 已运行后「② 结果」可点前进，直接回看现有结果、不重跑。
    fireEvent.click(screen.getByRole("button", { name: /结果/ }));
    expect(screen.getByText("12.00%")).toBeInTheDocument();
    expect(onRun).toHaveBeenCalledTimes(1);
  });

  it("renders the risk override section and carries typed stop-loss / take-profit in onRun", () => {
    // riskOverrideEnabled 默认 true（原子/因子）：渲染「止盈止损（可选）」分区，填入后 onRun 携带两字段。
    const { onRun } = renderModal();
    const section = screen.getByText("止盈止损（可选）").closest("section") as HTMLElement;
    const inputs = within(section).getAllByRole("spinbutton"); // [止盈%, 止损%]（DOM 顺序）
    fireEvent.change(inputs[0], { target: { value: "5" } });
    fireEvent.change(inputs[1], { target: { value: "3" } });
    fireEvent.click(screen.getByRole("button", { name: "开始回测" }));
    expect(onRun).toHaveBeenCalledWith(
      expect.objectContaining({ takeProfitPct: 5, stopLossPct: 3 }),
    );
  });

  it("hides the risk override section when riskOverrideEnabled is false (combo)", () => {
    renderModal({ riskOverrideEnabled: false });
    expect(screen.queryByText("止盈止损（可选）")).not.toBeInTheDocument();
  });
});
