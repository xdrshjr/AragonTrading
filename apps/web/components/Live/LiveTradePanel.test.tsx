import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useAppStore } from "@/lib/store";
import { tradingApi } from "@/lib/tradingApi";
import type { TradingConfig } from "@/lib/tradingTypes";
import LiveTradePanel from "./LiveTradePanel";

vi.mock("@/lib/tradingApi", () => ({
  tradingApi: {
    config: vi.fn(),
    placeOrder: vi.fn(),
    positions: vi.fn(),
    fills: vi.fn(),
    setLeverage: vi.fn(),
  },
}));

function mockConfig(over: Partial<TradingConfig> = {}): TradingConfig {
  return {
    accountId: 1,
    instId: "BTC-USDT-SWAP",
    posMode: "long_short_mode",
    tdMode: "cross",
    availUsdt: 10000,
    instMeta: { ctVal: 0.01, ctValCcy: "BTC", lotSz: 1, minSz: 1, tickSz: 0.1, maxLever: 125 },
    lastPx: 50000,
    instrumentSupported: true,
    liveTradingEnabled: true,
    ...over,
  };
}

function renderPanel() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <LiveTradePanel instId="BTC-USDT-SWAP" markPrice={null} />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  useAppStore.setState({ liveAcknowledged: false });
});
afterEach(cleanup);

describe("LiveTradePanel — guards", () => {
  it("disables ordering and explains when live trading is not enabled", async () => {
    vi.mocked(tradingApi.config).mockResolvedValue(mockConfig({ liveTradingEnabled: false }));
    renderPanel();
    const submit = (await screen.findByRole("button", { name: "买入开多" })) as HTMLButtonElement;
    expect(submit.disabled).toBe(true);
    expect(screen.getByText(/实盘下单未启用/)).toBeTruthy();
  });

  it("disables ordering for unsupported (non-USDT-margined) instruments", async () => {
    vi.mocked(tradingApi.config).mockResolvedValue(mockConfig({ instrumentSupported: false }));
    renderPanel();
    const submit = (await screen.findByRole("button", { name: "买入开多" })) as HTMLButtonElement;
    expect(submit.disabled).toBe(true);
    expect(screen.getByText(/暂不支持实盘下单/)).toBeTruthy();
  });

  it("keeps submit disabled until a valid quantity is entered", async () => {
    vi.mocked(tradingApi.config).mockResolvedValue(mockConfig());
    renderPanel();
    const submit = (await screen.findByRole("button", { name: "买入开多" })) as HTMLButtonElement;
    expect(submit.disabled).toBe(true); // sz defaults to 0 → below minSz
  });
});

describe("LiveTradePanel — confirm + submit flow", () => {
  it("requires the per-order confirm modal + risk acknowledgement, then calls placeOrder with mapped args", async () => {
    vi.mocked(tradingApi.config).mockResolvedValue(mockConfig());
    vi.mocked(tradingApi.placeOrder).mockResolvedValue({
      ok: true,
      ordId: "987654321",
      clOrdId: "aragon7f3a",
    });
    renderPanel();

    const qty = (await screen.findByLabelText("仓位数量（张）")) as HTMLInputElement;
    fireEvent.change(qty, { target: { value: "2" } });

    const submit = screen.getByRole("button", { name: "买入开多" }) as HTMLButtonElement;
    await waitFor(() => expect(submit.disabled).toBe(false));
    fireEvent.click(submit);

    // 每单都弹确认弹窗（C1-3）：未勾选风险前不可确认。
    const confirm = (await screen.findByRole("button", { name: "确认下单" })) as HTMLButtonElement;
    expect(confirm.disabled).toBe(true);
    expect(tradingApi.placeOrder).not.toHaveBeenCalled();

    fireEvent.click(screen.getByLabelText("我已知晓这是真实下单"));
    await waitFor(() => expect(confirm.disabled).toBe(false));
    fireEvent.click(confirm);

    await waitFor(() => expect(tradingApi.placeOrder).toHaveBeenCalledTimes(1));
    const req = vi.mocked(tradingApi.placeOrder).mock.calls[0][0];
    expect(req.instId).toBe("BTC-USDT-SWAP");
    expect(req.direction).toBe("long");
    expect(req.ordType).toBe("limit");
    expect(req.tdMode).toBe("cross");
    expect(req.sz).toBe(2);
    expect(req.lever).toBe(3);
    expect(req.px).toBe(50000); // 自最新价预填并按 tickSz 对齐
  });
});
