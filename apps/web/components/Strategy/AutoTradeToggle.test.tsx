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
      strategies: vi
        .fn()
        .mockResolvedValue([
          { id: 7, name: "EMA 交叉剥头皮" },
          { id: 9, name: "动量突破" },
        ]),
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
  // 分叉场景（回归钉，诊断 live-auto-trade-ignores-selected-strategy §二代码级最小复现）：
  // 「激活态」strategyId 停在默认首个 7，而用户用复选框勾选的是 9 → 自动交易必须用 9。
  useAppStore.setState({
    accountMode: "live", strategyId: 7, paperSelectionIds: [9],
    instId: "BTC-USDT-SWAP", bar: "1H",
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
    // 禁用原因渲染在就地可见 span（文本）与 wrapper 的 title 两处；按可见文本断言只命中 span，
    // 避免 getByTitle 同时匹配 div+span 而报「multiple elements」。
    await waitFor(() => expect(screen.getByText(/请先切换到实盘账户模式/)).toBeTruthy());
    fireEvent.click(screen.getByRole("switch"));
    expect(screen.queryByRole("button", { name: "确认并开始自动交易" })).toBeNull();
  });
});

describe("AutoTradeToggle — start flow", () => {
  it("uses the checkbox-selected strategy (paperSelectionIds), not the active strategyId", async () => {
    renderToggle();
    await waitFor(() => expect(screen.getByTitle("启动策略驱动的实时自动交易")).toBeTruthy());
    fireEvent.click(screen.getByRole("switch"));

    const confirm = (await screen.findByRole("button", {
      name: "确认并开始自动交易",
    })) as HTMLButtonElement;
    expect(confirm.disabled).toBe(true); // 未勾选风险前不可确认
    expect(liveAutoApi.start).not.toHaveBeenCalled();
    // 弹窗「策略」一行显示的是复选框勾选的 9（动量突破），而非激活态 7。
    expect(screen.getByText("动量突破")).toBeTruthy();

    fireEvent.click(screen.getByLabelText("我已知晓这是真实资金自动交易"));
    await waitFor(() => expect(confirm.disabled).toBe(false));
    fireEvent.click(confirm);

    await waitFor(() => expect(liveAutoApi.start).toHaveBeenCalledTimes(1));
    // strategyId 必须是复选框勾选的 9，而非默认激活的 7（本缺陷的回归钉）。
    expect(vi.mocked(liveAutoApi.start).mock.calls[0][0]).toEqual({
      strategyId: 9, instId: "BTC-USDT-SWAP", bar: "1H",
      capitalUsd: null, leverage: null, takeProfitUsd: null, stopLossUsd: null,
    });
  });

  it("stays blocked (no modal) when no strategy is checked", async () => {
    useAppStore.setState({ paperSelectionIds: [] });
    renderToggle();
    await waitFor(() => expect(screen.getByText(/请先选择一个交易策略/)).toBeTruthy());
    fireEvent.click(screen.getByRole("switch"));
    expect(screen.queryByRole("button", { name: "确认并开始自动交易" })).toBeNull();
    expect(liveAutoApi.start).not.toHaveBeenCalled();
  });

  it("stays blocked (no modal) when more than one strategy is checked", async () => {
    useAppStore.setState({ paperSelectionIds: [7, 9] });
    renderToggle();
    await waitFor(() => expect(screen.getByText(/仅支持单个策略/)).toBeTruthy());
    fireEvent.click(screen.getByRole("switch"));
    expect(screen.queryByRole("button", { name: "确认并开始自动交易" })).toBeNull();
    expect(liveAutoApi.start).not.toHaveBeenCalled();
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
