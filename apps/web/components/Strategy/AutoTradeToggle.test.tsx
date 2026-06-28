import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { accountApi } from "@/lib/accountApi";
import { liveAutoApi } from "@/lib/liveAutoApi";
import { useAppStore } from "@/lib/store";
import { tradingApi } from "@/lib/tradingApi";
import LiveTradePanel from "@/components/Live/LiveTradePanel";
import AutoTradeToggle from "./AutoTradeToggle";

vi.mock("@/lib/liveAutoApi", () => ({
  liveAutoApi: { status: vi.fn(), start: vi.fn(), stop: vi.fn() },
  liveAutoWS: vi.fn(() => ({ connect: vi.fn(), close: vi.fn() })),
}));
vi.mock("@/lib/accountApi", () => ({ accountApi: { accounts: { list: vi.fn() } } }));
vi.mock("@/lib/tradingApi", () => ({ tradingApi: { config: vi.fn() } }));
vi.mock("@/lib/api", async (orig) => {
  const actual = (await orig()) as Record<string, unknown>;
  return {
    ...actual,
    api: {
      ...(actual.api as Record<string, unknown>),
      strategies: vi.fn().mockResolvedValue([{ id: 7, name: "EMA 交叉剥头皮" }]),
    },
  };
});

const READY_ACCOUNTS = { accounts: [{ id: 1, hasSecret: true, hasPassphrase: true }], defaultId: 1 };

function mockConfig(over: Record<string, unknown> = {}) {
  return {
    accountId: 1, instId: "BTC-USDT-SWAP", posMode: "long_short_mode", tdMode: "cross",
    availUsdt: 1000,
    instMeta: { ctVal: 0.01, ctValCcy: "BTC", lotSz: 1, minSz: 1, tickSz: 0.1, maxLever: 125 },
    lastPx: 50000, instrumentSupported: true, liveTradingEnabled: true, ...over,
  };
}

function renderToggle() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <AutoTradeToggle />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  useAppStore.setState({
    accountMode: "live", strategyId: 7, instId: "BTC-USDT-SWAP", bar: "1H",
    liveAutoSessionId: null, liveAutoAcknowledged: false,
  });
  vi.mocked(accountApi.accounts.list).mockResolvedValue(READY_ACCOUNTS as never);
  vi.mocked(liveAutoApi.status).mockResolvedValue({ session: null, liveAutoTradingEnabled: true });
  vi.mocked(tradingApi.config).mockResolvedValue(mockConfig() as never);
  vi.mocked(liveAutoApi.start).mockResolvedValue({
    sessionId: "liveauto_x", runId: 1, strategyKind: "atomic", instId: "BTC-USDT-SWAP",
    bar: "1H", budgetUsd: 10, leverage: 3,
  });
});
afterEach(cleanup);

describe("AutoTradeToggle — gating", () => {
  it("stays blocked (no modal) when not in live account mode", async () => {
    useAppStore.setState({ accountMode: "sim" });
    renderToggle();
    await waitFor(() => expect(screen.getByTitle(/请先切换到实盘账户模式/)).toBeTruthy());
    fireEvent.click(screen.getByRole("switch"));
    expect(screen.queryByRole("button", { name: "确认并开始自动交易" })).toBeNull();
  });
});

describe("AutoTradeToggle — start flow", () => {
  it("opens confirm modal when enabled, then calls start with mapped args (defaults left blank)", async () => {
    renderToggle();
    await waitFor(() => expect(screen.getByTitle("启动策略驱动的实时自动交易")).toBeTruthy());
    fireEvent.click(screen.getByRole("switch"));

    const confirm = (await screen.findByRole("button", {
      name: "确认并开始自动交易",
    })) as HTMLButtonElement;
    expect(confirm.disabled).toBe(true); // 未勾选风险前不可确认
    expect(liveAutoApi.start).not.toHaveBeenCalled();

    fireEvent.click(screen.getByLabelText("我已知晓这是真实资金自动交易"));
    await waitFor(() => expect(confirm.disabled).toBe(false));
    fireEvent.click(confirm);

    await waitFor(() => expect(liveAutoApi.start).toHaveBeenCalledTimes(1));
    expect(vi.mocked(liveAutoApi.start).mock.calls[0][0]).toEqual({
      strategyId: 7, instId: "BTC-USDT-SWAP", bar: "1H",
      capitalUsd: null, leverage: null, takeProfitUsd: null, stopLossUsd: null,
    });
  });
});

describe("LiveTradePanel — disabled while auto-trading active", () => {
  it("disables manual ordering and shows banner when a live-auto session is active", async () => {
    useAppStore.setState({ liveAutoSessionId: "liveauto_x" });
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={qc}>
        <LiveTradePanel instId="BTC-USDT-SWAP" markPrice={null} />
      </QueryClientProvider>,
    );
    const submit = (await screen.findByRole("button", { name: "买入开多" })) as HTMLButtonElement;
    expect(submit.disabled).toBe(true);
    expect(screen.getByText(/自动交易进行中/)).toBeTruthy();
  });
});
