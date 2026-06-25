import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { DEFAULT_COMBO_POLICY } from "@/lib/combo";
import { api } from "@/lib/api";
import { useAppStore } from "@/lib/store";
import type { BacktestResult, Health, Strategy } from "@/lib/types";
import StrategyPanel from "./StrategyPanel";

const RUN_BACKTEST = "运行回测";
const SAVE_AS = "另存为";
const NEW_COMBO = "新建组合";
const SAVE_COMBO = "保存组合";
const DELETE = "删除";
const REGULAR_TAB = "常规策略";
const COMBO_TAB = "组合策略";

const METRICS = vi.hoisted(() => ({
  totalReturn: 0.12,
  winRate: 0.55,
  profitFactor: 1.6,
  maxDrawdown: -0.04,
  sharpe: 1.25,
  tradeCount: 11,
  avgPnl: 8,
}));

const BACKTEST_RESULT = vi.hoisted(
  (): BacktestResult => ({
    runId: 101,
    metrics: METRICS,
    effectiveCandleCount: 300,
    requestedBars: 300,
    truncated: false,
  }),
);

const HEALTH = vi.hoisted(
  (): Health => ({
    status: "ok",
    okx: "up",
    okxLatencyMs: 12,
    lastOkTs: 1_700_000_000,
    wsConfigured: true,
    maxPaperSessions: 3,
  }),
);

const STRATEGIES = vi.hoisted(
  (): Strategy[] => [
    {
      id: 1,
      templateKey: "alpha_trend",
      name: "Alpha Trend",
      description: "Alpha strategy",
      kind: "atomic",
      params: { threshold: 10, enabled: true },
      paramsSchema: {
        threshold: {
          type: "number",
          default: 10,
          min: 0,
          max: 100,
          step: 1,
          label: "Threshold",
        },
        enabled: {
          type: "bool",
          default: true,
          label: "Enabled filter",
        },
      },
      enabled: true,
      builtin: true,
    },
    {
      id: 2,
      templateKey: "beta_range",
      name: "Beta Range",
      description: "Beta strategy",
      kind: "atomic",
      params: { threshold: 20, enabled: false },
      paramsSchema: {
        threshold: {
          type: "number",
          default: 20,
          min: 0,
          max: 100,
          step: 1,
          label: "Threshold",
        },
      },
      enabled: true,
      builtin: true,
    },
    {
      id: 20,
      templateKey: "regime_rotation",
      name: "Combo One",
      description: "Saved combo",
      kind: "combo",
      params: {
        members: [
          { templateKey: "alpha_trend", params: {}, name: "Alpha Trend" },
          { templateKey: "beta_range", params: {}, name: "Beta Range" },
        ],
        rebalanceBars: 24,
        lookbackBars: 168,
        selectionMetric: "sharpe",
        minDwellBars: 24,
        switchMarginPct: 0.1,
        regimeGating: true,
        turnoverBps: 5,
      },
      paramsSchema: {
        rebalanceBars: {
          type: "int",
          default: 24,
          min: 4,
          max: 240,
          step: 1,
          label: "Rebalance bars",
        },
      },
      enabled: true,
      builtin: false,
    },
  ],
);

vi.mock("@/lib/api", () => {
  class ApiRequestError extends Error {
    code: string;
    status: number;

    constructor(code: string, message: string, status: number) {
      super(message);
      this.code = code;
      this.status = status;
    }
  }

  return {
    ApiRequestError,
    api: {
      health: vi.fn(),
      strategies: vi.fn(),
      createStrategy: vi.fn(),
      updateStrategy: vi.fn(),
      deleteStrategy: vi.fn(),
      backtest: vi.fn(),
      walkForward: vi.fn(),
      trades: vi.fn(),
      equity: vi.fn(),
      runsList: vi.fn(),
      paperStart: vi.fn(),
      paperBatchStart: vi.fn(),
      paperStop: vi.fn(),
      paperBatchStop: vi.fn(),
      paperStopAll: vi.fn(),
      paperSessions: vi.fn(),
    },
  };
});

function mockApi(strategies: Strategy[] = STRATEGIES) {
  vi.mocked(api.health).mockResolvedValue(HEALTH);
  vi.mocked(api.strategies).mockResolvedValue(strategies);
  vi.mocked(api.createStrategy).mockResolvedValue({
    ...STRATEGIES[0],
    id: 3,
    name: "Alpha Copy",
    builtin: false,
  });
  vi.mocked(api.updateStrategy).mockResolvedValue(STRATEGIES[0]);
  vi.mocked(api.deleteStrategy).mockResolvedValue(undefined);
  vi.mocked(api.backtest).mockResolvedValue(BACKTEST_RESULT);
  vi.mocked(api.walkForward).mockResolvedValue({
    runId: 202,
    summary: {
      windowCount: 1,
      profitableWindows: 1,
      profitableRate: 1,
      medianReturn: 0.1,
      avgReturn: 0.1,
      worstReturn: 0.1,
      bestReturn: 0.1,
      avgMaxDrawdown: -0.02,
      stabilityScore: 0.8,
      uncoveredBars: 0,
      notes: [],
    },
    windows: [],
  });
  vi.mocked(api.trades).mockResolvedValue([]);
  vi.mocked(api.equity).mockResolvedValue([]);
  vi.mocked(api.runsList).mockResolvedValue([]);
  vi.mocked(api.paperSessions).mockResolvedValue([]);
  vi.mocked(api.paperStart).mockResolvedValue({ sessionId: "paper-1", runId: 301 });
  vi.mocked(api.paperBatchStart).mockResolvedValue({
    groupId: "batch-1",
    instId: "BTC-USDT-SWAP",
    bar: "1H",
    sessions: [],
  });
  vi.mocked(api.paperStop).mockResolvedValue({
    id: 301,
    strategyId: 1,
    instId: "BTC-USDT-SWAP",
    bar: "1H",
    mode: "paper",
    status: "stopped",
    startTs: 1_700_000_000,
    metrics: METRICS,
  });
  vi.mocked(api.paperBatchStop).mockResolvedValue({ groupId: "batch-1", runs: [] });
  vi.mocked(api.paperStopAll).mockResolvedValue({ stopped: 0, runs: [] });
}

function resetStore(state: Partial<ReturnType<typeof useAppStore.getState>> = {}) {
  window.localStorage.clear();
  useAppStore.setState({
    instId: "BTC-USDT-SWAP",
    bar: "1H",
    strategyId: null,
    runId: null,
    chartView: "live-follow",
    mode: "backtest",
    strategyListTab: "regular",
    backtestKind: "simple",
    wfParams: { windowBars: 1000, stepBars: 200, resetCapital: true },
    walkForwardResult: null,
    simBalance: 10_000,
    paperSessionId: null,
    paperSelectionIds: [],
    paperVisibleStrategyIds: [],
    paperActiveStrategyId: null,
    paperBatchGroupId: null,
    paperSessionsByStrategyId: {},
    paperStreamsEnabled: false,
    backtestStartTs: 1_700_000_000,
    backtestEndTs: 1_700_086_400,
    comboDraft: { memberIds: [], policy: { ...DEFAULT_COMBO_POLICY } },
    comboEditingId: null,
    ...state,
  });
}

function renderPanel() {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  });
  render(
    <QueryClientProvider client={queryClient}>
      <StrategyPanel />
    </QueryClientProvider>,
  );
  return queryClient;
}

async function settleEffects() {
  await act(async () => {
    await Promise.resolve();
  });
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0));
  });
}

async function renderLoadedPanel(state: Partial<ReturnType<typeof useAppStore.getState>> = {}) {
  resetStore(state);
  const queryClient = renderPanel();
  await screen.findByText("Alpha Trend");
  await waitFor(() => expect(useAppStore.getState().strategyId).toBe(1));
  await settleEffects();
  return queryClient;
}

function getButton(text: string) {
  const button = screen
    .getAllByRole("button")
    .find((item) => item.textContent?.includes(text));
  if (!button) throw new Error(`Button not found: ${text}`);
  return button;
}

function selectStrategy(name: string) {
  fireEvent.click(screen.getByRole("button", { name: new RegExp(name) }));
}

async function selectComboTab() {
  fireEvent.click(getButton(COMBO_TAB));
  await waitFor(() => expect(screen.getAllByText("Combo One").length).toBeGreaterThan(0));
}

async function runAtomicBacktest() {
  fireEvent.click(getButton(RUN_BACKTEST));
  await waitFor(() => expect(api.backtest).toHaveBeenCalledTimes(1));
  await screen.findByText("12.00%");
}

describe("StrategyPanel manual backtest trigger", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockApi();
  });

  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it("loads strategies on mount without running or persisting a backtest", async () => {
    await renderLoadedPanel();

    expect(api.strategies).toHaveBeenCalled();
    expect(api.backtest).not.toHaveBeenCalled();
    expect(api.updateStrategy).not.toHaveBeenCalled();
  });

  it("selects another atomic strategy without running or persisting a backtest", async () => {
    await renderLoadedPanel();
    vi.clearAllMocks();

    selectStrategy("Beta Range");
    await waitFor(() => expect(useAppStore.getState().strategyId).toBe(2));
    await settleEffects();

    expect(api.backtest).not.toHaveBeenCalled();
    expect(api.updateStrategy).not.toHaveBeenCalled();
  });

  it("renders strategy category tabs and filters the strategy list", async () => {
    await renderLoadedPanel();

    expect(getButton(REGULAR_TAB)).toBeTruthy();
    expect(getButton(COMBO_TAB)).toBeTruthy();
    expect(screen.getByText("Alpha Trend")).toBeTruthy();
    expect(screen.queryByText("Combo One")).toBeNull();

    await selectComboTab();

    expect(screen.queryByText("Alpha Trend")).toBeNull();
    expect(screen.getAllByText("Combo One").length).toBeGreaterThan(0);
    expect(useAppStore.getState().strategyId).toBe(20);
  });

  it("shows an empty combo state without stale detail or results", async () => {
    mockApi(STRATEGIES.filter((strategy) => strategy.kind !== "combo"));
    await renderLoadedPanel();
    vi.clearAllMocks();
    await runAtomicBacktest();
    vi.clearAllMocks();

    fireEvent.click(getButton(COMBO_TAB));
    await screen.findByText("暂无组合策略");
    await waitFor(() => expect(useAppStore.getState().strategyId).toBeNull());

    expect(api.backtest).not.toHaveBeenCalled();
    expect(screen.queryByText("Alpha Trend")).toBeNull();
    expect(screen.queryByText("12.00%")).toBeNull();
    expect(getButton(NEW_COMBO)).toBeTruthy();
  });

  it("keeps atomic parameter edits local until the run button is clicked", async () => {
    await renderLoadedPanel();
    vi.clearAllMocks();

    fireEvent.change(screen.getByLabelText("Threshold"), { target: { value: "33" } });
    await settleEffects();
    expect(api.updateStrategy).not.toHaveBeenCalled();
    expect(api.backtest).not.toHaveBeenCalled();

    fireEvent.click(getButton(RUN_BACKTEST));

    await waitFor(() => expect(api.updateStrategy).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(api.backtest).toHaveBeenCalledTimes(1));
    expect(api.updateStrategy).toHaveBeenCalledWith(1, {
      params: { threshold: 33, enabled: true },
    });
    expect(api.backtest).toHaveBeenCalledWith(1, {
      instId: "BTC-USDT-SWAP",
      bar: "1H",
      startTs: 1_700_000_000,
      endTs: 1_700_086_400,
    });
    const updateOrder = vi.mocked(api.updateStrategy).mock.invocationCallOrder[0];
    const backtestOrder = vi.mocked(api.backtest).mock.invocationCallOrder[0];
    expect(updateOrder).toBeLessThan(backtestOrder);
  });

  it("runs saved combos only from the combo run button and never updates params", async () => {
    await renderLoadedPanel();
    vi.clearAllMocks();

    await selectComboTab();
    vi.clearAllMocks();
    selectStrategy("Combo One");
    await waitFor(() => expect(useAppStore.getState().strategyId).toBe(20));
    await settleEffects();

    expect(api.backtest).not.toHaveBeenCalled();
    expect(api.updateStrategy).not.toHaveBeenCalled();

    fireEvent.click(getButton(RUN_BACKTEST));

    await waitFor(() => expect(api.backtest).toHaveBeenCalledTimes(1));
    expect(api.backtest).toHaveBeenCalledWith(20, {
      instId: "BTC-USDT-SWAP",
      bar: "1H",
      startTs: 1_700_000_000,
      endTs: 1_700_086_400,
    });
    expect(api.updateStrategy).not.toHaveBeenCalled();
  });

  it("clears displayed run results when a passive strategy selection changes", async () => {
    await renderLoadedPanel();
    vi.clearAllMocks();
    await runAtomicBacktest();
    vi.clearAllMocks();

    selectStrategy("Beta Range");
    await waitFor(() => expect(useAppStore.getState().strategyId).toBe(2));
    await settleEffects();

    expect(api.backtest).not.toHaveBeenCalled();
    expect(useAppStore.getState().runId).toBeNull();
    expect(screen.queryByText("12.00%")).toBeNull();
  });

  it("keeps save-as and saved-combo creation selection passive", async () => {
    const createdAtomic = { ...STRATEGIES[0], id: 3, name: "Alpha Copy", builtin: false };
    const createdCombo = { ...STRATEGIES[2], id: 30, name: "Combo Saved" };
    vi.mocked(api.strategies).mockResolvedValue([...STRATEGIES, createdAtomic, createdCombo]);

    await renderLoadedPanel();
    vi.clearAllMocks();
    await runAtomicBacktest();
    vi.clearAllMocks();

    vi.mocked(api.createStrategy).mockResolvedValueOnce(createdAtomic);
    const promptSpy = vi.spyOn(window, "prompt").mockReturnValue("Alpha Copy");
    fireEvent.click(getButton(SAVE_AS));

    await waitFor(() => expect(api.createStrategy).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(useAppStore.getState().strategyId).toBe(3));
    await settleEffects();
    expect(promptSpy).toHaveBeenCalled();
    expect(api.backtest).not.toHaveBeenCalled();
    expect(useAppStore.getState().runId).toBeNull();
    expect(screen.queryByText("12.00%")).toBeNull();

    vi.clearAllMocks();
    vi.mocked(api.createStrategy).mockResolvedValueOnce(createdCombo);
    await selectComboTab();
    vi.clearAllMocks();
    fireEvent.click(getButton(NEW_COMBO));
    await screen.findByRole("dialog");
    fireEvent.click(screen.getByLabelText(/Alpha Trend/));
    fireEvent.click(screen.getByLabelText(/Beta Range/));
    fireEvent.change(document.querySelector("#combo-name") as HTMLInputElement, {
      target: { value: "Combo Saved" },
    });
    fireEvent.click(getButton(SAVE_COMBO));

    await waitFor(() => expect(api.createStrategy).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(useAppStore.getState().strategyId).toBe(30));
    await settleEffects();
    expect(api.backtest).not.toHaveBeenCalled();
    expect(useAppStore.getState().runId).toBeNull();
    expect(screen.queryByText("12.00%")).toBeNull();
  });

  it("keeps deleting the selected combo passive and clears the current result", async () => {
    await renderLoadedPanel();
    await selectComboTab();
    selectStrategy("Combo One");
    await waitFor(() => expect(useAppStore.getState().strategyId).toBe(20));
    vi.clearAllMocks();
    await runAtomicBacktest();
    vi.clearAllMocks();
    vi.mocked(api.strategies).mockResolvedValue(STRATEGIES.filter((item) => item.id !== 20));

    fireEvent.click(getButton(DELETE));
    const dialog = await screen.findByRole("dialog");
    fireEvent.click(within(dialog).getByRole("button", { name: DELETE }));

    await waitFor(() => expect(api.deleteStrategy).toHaveBeenCalledWith(20));
    await waitFor(() => expect(useAppStore.getState().strategyId).toBeNull());
    await settleEffects();
    expect(api.backtest).not.toHaveBeenCalled();
    expect(useAppStore.getState().runId).toBeNull();
    expect(screen.queryByText("12.00%")).toBeNull();
    expect(screen.getByText("暂无组合策略")).toBeTruthy();
  });

  it("keeps instrument, bar, and range changes passive while clearing stale results", async () => {
    await renderLoadedPanel();
    vi.clearAllMocks();
    await runAtomicBacktest();
    vi.clearAllMocks();

    act(() => {
      useAppStore.getState().setBacktestRange(1_700_100_000, 1_700_200_000);
    });
    await settleEffects();

    expect(api.backtest).not.toHaveBeenCalled();
    expect(useAppStore.getState().runId).toBeNull();
    expect(screen.queryByText("12.00%")).toBeNull();

    vi.clearAllMocks();
    await runAtomicBacktest();
    vi.clearAllMocks();
    act(() => {
      useAppStore.getState().setInst("ETH-USDT-SWAP");
      useAppStore.getState().setBar("4H");
    });
    await settleEffects();

    expect(api.backtest).not.toHaveBeenCalled();
    expect(useAppStore.getState().runId).toBeNull();
    expect(screen.queryByText("12.00%")).toBeNull();
  });
});
