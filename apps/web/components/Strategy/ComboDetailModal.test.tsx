import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "@/lib/api";
import { useAppStore } from "@/lib/store";
import type { Strategy } from "@/lib/types";
import ComboDetailModal from "./ComboDetailModal";

const FULL_SCHEMA = {
  rebalanceBars: { type: "int", default: 24, min: 4, max: 240, step: 1, label: "再平衡间隔(根)" },
  lookbackBars: { type: "int", default: 168, min: 24, max: 1000, step: 1, label: "表现回看(根)" },
  selectionMetric: { type: "enum", default: "sharpe", options: ["return", "sharpe", "calmar"], label: "选优指标" },
  minDwellBars: { type: "int", default: 24, min: 1, max: 240, step: 1, label: "最短持有(根)" },
  switchMarginPct: { type: "number", default: 0.1, min: 0, max: 1, step: 0.01, label: "切换迟滞%" },
  regimeGating: { type: "bool", default: true, label: "按体制限定候选" },
  turnoverBps: { type: "number", default: 5, min: 0, max: 50, step: 0.5, label: "换手成本(bps)" },
} as const;

const STRATEGIES: Strategy[] = [
  { id: 1, templateKey: "trend_pullback_long", name: "顺势回踩做多", description: "", kind: "atomic",
    params: {}, paramsSchema: {}, enabled: true, builtin: true },
  { id: 2, templateKey: "range_scalping_strategy", name: "区间剥头皮", description: "", kind: "atomic",
    params: {}, paramsSchema: {}, enabled: true, builtin: true },
];

const COMBO: Strategy = {
  id: 20, templateKey: "regime_rotation", name: "我的组合A", description: "", kind: "combo",
  enabled: true, builtin: false,
  params: {
    members: [
      { templateKey: "trend_pullback_long", params: {}, name: "顺势回踩做多" },
      { templateKey: "range_scalping_strategy", params: {}, name: "区间剥头皮" },
    ],
    rebalanceBars: 24, lookbackBars: 168, selectionMetric: "sharpe",
    minDwellBars: 24, switchMarginPct: 0.1, regimeGating: true, turnoverBps: 5,
  },
  paramsSchema: FULL_SCHEMA as unknown as Strategy["paramsSchema"],
};

const METRICS = {
  totalReturn: 0.12, winRate: 0.5, profitFactor: 1.4, maxDrawdown: -0.08,
  sharpe: 1.2, tradeCount: 18, avgPnl: 3.2,
};

vi.mock("@/lib/api", () => ({
  api: { backtest: vi.fn() },
}));

function renderModal(onClose = vi.fn(), onEdit = vi.fn(), onDelete = vi.fn()) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <ComboDetailModal
        combo={COMBO}
        strategies={STRATEGIES}
        onClose={onClose}
        onEdit={onEdit}
        onDelete={onDelete}
      />
    </QueryClientProvider>,
  );
  return { onClose, onEdit, onDelete };
}

const VALID_RANGE = { backtestStartTs: 1_700_000_000, backtestEndTs: 1_700_000_000 + 100 * 3600 };

describe("ComboDetailModal", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    useAppStore.setState(VALID_RANGE);
  });
  afterEach(cleanup);

  it("renders overview, members and rotation params from the combo row", () => {
    renderModal();
    expect(screen.getByText("概览")).toBeTruthy();
    expect(screen.getByText("成员策略 · 2")).toBeTruthy();
    expect(screen.getByText("轮动参数")).toBeTruthy();
    expect(screen.getByText("顺势回踩做多")).toBeTruthy();
    expect(screen.getByText("区间剥头皮")).toBeTruthy();
    expect(screen.getByText("再平衡间隔(根)")).toBeTruthy();
  });

  it("closes on Escape via the Modal a11y contract", () => {
    const { onClose } = renderModal();
    fireEvent.keyDown(screen.getByRole("dialog"), { key: "Escape" });
    expect(onClose).toHaveBeenCalled();
  });

  it("disables the backtest button when the range is not ready", () => {
    useAppStore.setState({ backtestStartTs: 0, backtestEndTs: 0 });
    renderModal();
    expect((screen.getByText("运行回测") as HTMLButtonElement).disabled).toBe(true);
  });

  it("renders annualized return and metrics from res.combo after a lazy backtest", async () => {
    (api.backtest as ReturnType<typeof vi.fn>).mockResolvedValue({
      runId: 7,
      metrics: METRICS,
      combo: {
        annualizedReturn: 0.25,
        timeline: [
          { startTs: 1, endTs: 2, memberKey: "a", memberName: "顺势回踩做多", regime: "trend_up", bars: 10, contribution: 0.05 },
        ],
        breakdown: [],
        notes: [],
      },
    });
    renderModal();
    fireEvent.click(screen.getByText("运行回测"));
    expect(await screen.findByText("期望年化收益")).toBeTruthy();
    expect(screen.getByText("25.00%")).toBeTruthy();
    await waitFor(() => expect(screen.getByText("总收益率")).toBeTruthy());
  });
});
