import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { accountApi } from "@/lib/accountApi";
import { liveAutoGroupApi } from "@/lib/liveAutoGroupApi";
import { type LiveAutoGroupState, useAppStore } from "@/lib/store";
import type { Instrument } from "@/lib/types";
import WatchlistTab from "./WatchlistTab";

vi.mock("@/lib/liveAutoGroupApi", () => ({
  liveAutoGroupApi: { status: vi.fn(), start: vi.fn(), stop: vi.fn(), stopMember: vi.fn() },
  liveAutoWS: vi.fn(() => ({ connect: vi.fn(), close: vi.fn() })),
}));
vi.mock("@/lib/accountApi", () => ({ accountApi: { accounts: { list: vi.fn() } } }));
vi.mock("@/lib/tradingApi", () => ({ tradingApi: { config: vi.fn() } }));
vi.mock("@/lib/api", async (orig) => {
  const actual = (await orig()) as Record<string, unknown>;
  return {
    ...actual,
    api: { ...(actual.api as Record<string, unknown>), strategies: vi.fn().mockResolvedValue([]) },
  };
});

const READY_ACCOUNTS = { accounts: [{ id: 1, hasSecret: true, hasPassphrase: true }], defaultId: 1 };

function inst(instId: string, instType: Instrument["instType"], base: string, quote: string): Instrument {
  return { instId, instType, base, quote, last: 100, open24h: 99, vol24h: 1, volCcy24h: 1, changePct: 0.01 };
}

const ROWS: Instrument[] = [
  inst("BTC-USDT-SWAP", "SWAP", "BTC", "USDT"),
  inst("ETH-USDT-SWAP", "SWAP", "ETH", "USDT"),
  inst("ADA-USDT", "SPOT", "ADA", "USDT"),
];

function renderTab() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <WatchlistTab rows={ROWS} active="BTC-USDT-SWAP" onSelect={vi.fn()} />
    </QueryClientProvider>,
  );
}

function runningGroup(): LiveAutoGroupState {
  return {
    groupId: "g1", strategyId: 1, strategyName: "s", bar: "1H", totalBudgetUsd: 100, leverage: 3,
    state: "running", startedTs: 0,
    members: {
      "BTC-USDT-SWAP": {
        instId: "BTC-USDT-SWAP", sessionId: "a", runId: 1, allocationPct: 100, budgetUsd: 100,
        status: { state: "running", sessionPnlUsd: 5, positionSide: "long" }, position: null, trades: [],
      },
    },
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  localStorage.clear();
  useAppStore.setState({
    accountMode: "live", bar: "1H", strategyId: 1,
    watchlist: ["BTC-USDT-SWAP", "ETH-USDT-SWAP", "ADA-USDT"],
    autoTradeMembers: [{ instId: "BTC-USDT-SWAP", allocationPct: 100 }],
    liveAutoGroup: null, liveAutoAcknowledged: false,
  });
  vi.mocked(accountApi.accounts.list).mockResolvedValue(READY_ACCOUNTS as never);
  vi.mocked(liveAutoGroupApi.status).mockResolvedValue({ group: null, liveAutoTradingEnabled: true });
});
afterEach(cleanup);

describe("WatchlistTab — partition & membership", () => {
  it("renders the auto-trade member on top and other watchlist below", () => {
    renderTab();
    const auto = screen.getByLabelText("自动交易");
    const other = screen.getByLabelText("其他自选");
    expect(within(auto).getByText("BTC-USDT-SWAP")).toBeTruthy();
    expect(within(other).getByText("ETH-USDT-SWAP")).toBeTruthy();
    expect(within(other).getByText("ADA-USDT")).toBeTruthy();
  });

  it("adds a watchlist item to auto-trade via the + button (keyboard equivalent of drag)", () => {
    renderTab();
    fireEvent.click(screen.getByRole("button", { name: "加入自动交易 ETH-USDT-SWAP" }));
    const members = useAppStore.getState().autoTradeMembers.map((m) => m.instId);
    expect(members).toContain("ETH-USDT-SWAP");
    const auto = screen.getByLabelText("自动交易");
    expect(within(auto).getByText("ETH-USDT-SWAP")).toBeTruthy();
  });

  it("disables the + button for SPOT (not auto-tradable)", () => {
    renderTab();
    const btn = screen.getByRole("button", { name: "加入自动交易 ADA-USDT" }) as HTMLButtonElement;
    expect(btn.disabled).toBe(true);
  });

  it("removes a member back to other via its × button", () => {
    renderTab();
    fireEvent.click(screen.getByRole("button", { name: "从自动交易移出 BTC-USDT-SWAP" }));
    expect(useAppStore.getState().autoTradeMembers).toEqual([]);
  });
});

describe("WatchlistTab — gating", () => {
  it("blocks the switch and shows a reason when allocation sum exceeds 100%", async () => {
    useAppStore.setState({
      autoTradeMembers: [
        { instId: "BTC-USDT-SWAP", allocationPct: 60 },
        { instId: "ETH-USDT-SWAP", allocationPct: 60 },
      ],
    });
    renderTab();
    await waitFor(() => expect(screen.getByText(/之和超过 100%/)).toBeTruthy());
  });

  it("blocks the switch when not in live account mode", async () => {
    useAppStore.setState({ accountMode: "sim" });
    renderTab();
    await waitFor(() => expect(screen.getByText(/请先切换到实盘账户模式/)).toBeTruthy());
  });
});

describe("WatchlistTab — running lock", () => {
  it("shows running state, a per-member stop, and stops the group on toggle off", async () => {
    useAppStore.setState({ liveAutoGroup: runningGroup() });
    vi.mocked(liveAutoGroupApi.stop).mockResolvedValue({
      groupId: "g1", stopReason: "user_stop", members: [],
    });
    renderTab();
    const sw = screen.getByRole("switch", { name: "自动交易分组开关" });
    expect(sw.getAttribute("aria-checked")).toBe("true");
    // 运行态成员行展示「停止」而非百分比输入（锁定编辑）
    expect(screen.getByTitle("停止该标的自动交易并平仓")).toBeTruthy();
    fireEvent.click(sw);
    await waitFor(() => expect(liveAutoGroupApi.stop).toHaveBeenCalledWith("g1"));
  });
});
