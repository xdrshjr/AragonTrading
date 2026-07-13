import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { LiveAutoGroupState } from "@/lib/store";
import { useAppStore } from "@/lib/store";
import { tradingApi } from "@/lib/tradingApi";
import type { LivePosition } from "@/lib/tradingTypes";
import LivePositionsTable from "./LivePositionsTable";

vi.mock("@/lib/tradingApi", () => ({
  tradingApi: {
    positions: vi.fn(),
    config: vi.fn(),
    openOrders: vi.fn(),
  },
}));

const position: LivePosition = {
  instId: "BTC-USDT-SWAP",
  instType: "SWAP",
  posSide: "long",
  pos: 2,
  avgPx: 30000,
  last: 30100,
  upl: 200,
  uplRatio: 0.01,
  lever: 2,
  marginMode: "cross",
};

function group(size = 2): LiveAutoGroupState {
  return {
    groupId: "group-1",
    strategyId: 1,
    strategyName: "S",
    bar: "1H",
    totalBudgetUsd: 1000,
    leverage: 2,
    state: "running",
    startedTs: 1,
    members: {
      [position.instId]: {
        instId: position.instId,
        sessionId: "session-1",
        runId: 1,
        allocationPct: 100,
        budgetUsd: 1000,
        status: null,
        trades: [],
        position: {
          side: "long",
          size,
          avgEntry: 30000,
          markPrice: 30100,
          unrealizedPnl: 200,
          positionPct: 0.4,
        },
      },
    },
  };
}

function renderTable() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <LivePositionsTable instId={position.instId} />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(tradingApi.positions).mockResolvedValue({ positions: [position] });
  vi.mocked(tradingApi.config).mockResolvedValue({
    accountId: 1,
    instId: position.instId,
    posMode: "long_short_mode",
    tdMode: "cross",
    availUsdt: 1000,
    instMeta: { ctVal: 1, ctValCcy: "BTC", lotSz: 1, minSz: 1, tickSz: 0.1, maxLever: 10 },
    lastPx: 30100,
    instrumentSupported: true,
    liveTradingEnabled: true,
  });
  vi.mocked(tradingApi.openOrders).mockResolvedValue({ orders: [] });
  useAppStore.setState({
    liveAutoGroup: group(),
    aiTradeGroup: null,
    liveAutoPosition: null,
  });
});

afterEach(cleanup);

describe("LivePositionsTable position percentage", () => {
  it("shows the matched automatic position percentage and updates child colspan", async () => {
    renderTable();
    expect(await screen.findByText("仓位占比")).toBeTruthy();
    expect(screen.getByText("40.00%")).toBeTruthy();
    const detail = screen.getByText(/该持仓暂无止盈/).closest("td");
    expect(detail?.getAttribute("colspan")).toBe("8");
  });

  it("shows a dash when the OKX quantity no longer matches runtime state", async () => {
    useAppStore.setState({ liveAutoGroup: group(1) });
    renderTable();
    expect(await screen.findByText("仓位占比")).toBeTruthy();
    expect(screen.queryByText("40.00%")).toBeNull();
    expect(screen.getByText("—")).toBeTruthy();
  });
});
