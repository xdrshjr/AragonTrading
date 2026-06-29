import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "@/lib/api";
import { useAppStore } from "@/lib/store";
import type { CompareResult, Metrics, Strategy } from "@/lib/types";
import CompareView from "./CompareView";

const METRICS: Metrics = {
  totalReturn: 0.12,
  winRate: 0.5,
  profitFactor: 1.4,
  maxDrawdown: -0.08,
  sharpe: 1.2,
  tradeCount: 18,
  avgPnl: 3.2,
};

const STRATEGIES: Strategy[] = [
  { id: 1, templateKey: "a1", name: "策略一", description: "d1", kind: "atomic",
    params: {}, paramsSchema: {}, enabled: true, builtin: true },
  { id: 2, templateKey: "a2", name: "策略二", description: "d2", kind: "atomic",
    params: {}, paramsSchema: {}, enabled: true, builtin: true },
  { id: 3, templateKey: "c1", name: "组合一", description: "d3", kind: "combo",
    params: { members: [] }, paramsSchema: {}, enabled: true, builtin: false },
];

const RANGE = {
  instId: "BTC-USDT-SWAP",
  bar: "1H" as const,
  backtestStartTs: 1_700_000_000,
  backtestEndTs: 1_700_360_000,
};

const COMPARE_KEY = ["compare", RANGE.instId, RANGE.bar, RANGE.backtestStartTs, RANGE.backtestEndTs];

const COMPARE_RESULT: CompareResult = {
  instId: RANGE.instId,
  bar: RANGE.bar,
  items: [
    { strategyId: 1, name: "策略一", runId: 101, metrics: METRICS, equity: [] },
    { strategyId: 2, name: "策略二", runId: 102, metrics: METRICS, equity: [] },
  ],
};

vi.mock("@/lib/api", () => ({
  api: { strategies: vi.fn(), compare: vi.fn() },
}));

function renderView() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <CompareView />
    </QueryClientProvider>,
  );
  return qc;
}

describe("CompareView", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    useAppStore.setState(RANGE);
    (api.strategies as ReturnType<typeof vi.fn>).mockResolvedValue(STRATEGIES);
  });
  afterEach(cleanup);

  it("enters with nothing selected: run disabled and empty-state prompt shown", async () => {
    renderView();
    expect(await screen.findByText("策略一")).toBeTruthy();
    expect(screen.getByText("已选 0/12")).toBeTruthy();
    expect(screen.getByText(/请从下方勾选/)).toBeTruthy();
    expect((screen.getByRole("button", { name: /运行对比/ }) as HTMLButtonElement).disabled).toBe(true);
  });

  it("writes the shared compare cache key after running with >= 2 selections", async () => {
    (api.compare as ReturnType<typeof vi.fn>).mockResolvedValue(COMPARE_RESULT);
    const qc = renderView();
    await screen.findByText("策略一");
    fireEvent.click(screen.getByLabelText("选择 策略一"));
    fireEvent.click(screen.getByLabelText("选择 策略二"));
    expect(screen.getByText("已选 2/12")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /运行对比/ }));
    await waitFor(() => expect(api.compare).toHaveBeenCalled());
    await waitFor(() => expect(qc.getQueryData(COMPARE_KEY)).toBeTruthy());
    expect(await screen.findByText(/收益率/)).toBeTruthy();
  });

  it("opening a row's detail does not change the selection", async () => {
    renderView();
    await screen.findByText("策略一");
    fireEvent.click(screen.getAllByText("详情")[0]);
    expect(await screen.findByRole("dialog")).toBeTruthy();
    expect(screen.getByText("已选 0/12")).toBeTruthy();
  });

  it("clears results, removing the leaderboard (responsive cache read)", async () => {
    (api.compare as ReturnType<typeof vi.fn>).mockResolvedValue(COMPARE_RESULT);
    renderView();
    await screen.findByText("策略一");
    fireEvent.click(screen.getByLabelText("选择 策略一"));
    fireEvent.click(screen.getByLabelText("选择 策略二"));
    fireEvent.click(screen.getByRole("button", { name: /运行对比/ }));
    expect(await screen.findByText(/收益率/)).toBeTruthy();
    fireEvent.click(screen.getByText("清空结果"));
    await waitFor(() => expect(screen.queryByText(/收益率/)).toBeNull());
  });

  it("filters the list to combos only", async () => {
    renderView();
    await screen.findByText("策略一");
    fireEvent.click(screen.getByRole("button", { name: "组合" }));
    expect(screen.queryByText("策略一")).toBeNull();
    expect(screen.getByText("组合一")).toBeTruthy();
  });
});
