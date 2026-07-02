import "@testing-library/jest-dom/vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { accountApi } from "@/lib/accountApi";
import { agentApi } from "@/lib/agentApi";
import { aiTradeApi } from "@/lib/aiTradeApi";
import { useAppStore } from "@/lib/store";
import { tradingApi } from "@/lib/tradingApi";
import AiTradeToggle from "./AiTradeToggle";

vi.mock("@/lib/aiTradeApi", () => ({
  aiTradeApi: { start: vi.fn(), stop: vi.fn(), status: vi.fn() },
  aiTradeWS: vi.fn(() => ({ connect: vi.fn(), close: vi.fn() })),
}));
vi.mock("@/lib/accountApi", () => ({ accountApi: { accounts: { list: vi.fn() } } }));
vi.mock("@/lib/agentApi", () => ({ agentApi: { config: vi.fn() } }));
vi.mock("@/lib/tradingApi", () => ({ tradingApi: { config: vi.fn() } }));
vi.mock("@/lib/api", async (orig) => {
  const actual = (await orig()) as Record<string, unknown>;
  return {
    ...actual,
    api: { ...(actual.api as Record<string, unknown>), strategies: vi.fn().mockResolvedValue([]) },
  };
});

const READY_ACCOUNTS = { accounts: [{ id: 1, hasSecret: true, hasPassphrase: true }], defaultId: 1 };

function renderToggle() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <AiTradeToggle />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  localStorage.clear();
  useAppStore.setState({
    accountMode: "live",
    instId: "BTC-USDT-SWAP",
    bar: "1H",
    strategyId: 1,
    aiTradeSessionId: null,
    aiTradeSession: null,
    liveAutoGroup: null,
    aiTradeAcknowledged: false,
    aiTradeConfig: null,
  });
  vi.mocked(accountApi.accounts.list).mockResolvedValue(READY_ACCOUNTS as never);
  vi.mocked(agentApi.config).mockResolvedValue({ configured: true, provider: "x", model: "y" } as never);
  vi.mocked(aiTradeApi.status).mockResolvedValue({ session: null, aiTradeEnabled: true } as never);
  vi.mocked(tradingApi.config).mockResolvedValue({ availUsdt: 1000 } as never);
});

afterEach(() => cleanup());

describe("AiTradeToggle", () => {
  it("renders an off switch when no session is active", () => {
    renderToggle();
    expect(screen.getByRole("switch")).toHaveAttribute("aria-checked", "false");
  });

  it("opens the launch modal when all gates pass", async () => {
    renderToggle();
    // 守门通过后包裹容器 title 变为「开启…」，据此等三条查询（账户/模型/AI 开关）都已水合再点击。
    await screen.findByTitle("开启 AI 自动交易");
    fireEvent.click(screen.getByRole("switch"));
    expect(await screen.findByText("启动 AI 自动交易")).toBeInTheDocument();
  });

  it("does not open the modal when AI trading is disabled backend-side", async () => {
    vi.mocked(aiTradeApi.status).mockResolvedValue({ session: null, aiTradeEnabled: false } as never);
    renderToggle();
    await waitFor(() => expect(aiTradeApi.status).toHaveBeenCalled());
    fireEvent.click(screen.getByRole("switch"));
    expect(screen.queryByText("启动 AI 自动交易")).not.toBeInTheDocument();
  });

  it("stops the session when toggled off while running", async () => {
    useAppStore.setState({
      aiTradeSessionId: "sess-1",
      aiTradeSession: {
        sessionId: "sess-1", runId: 1, state: "running", instId: "BTC-USDT-SWAP", bar: "1H",
        strategyIds: [1], strategyNames: ["S1"], budgetUsd: 100, leverage: 3,
        contextRefreshRounds: 10, roundsCompleted: 0, roundsSinceSummary: 0,
        positionSide: "flat", sessionPnlUsd: 0, startedTs: 0,
      },
    });
    vi.mocked(aiTradeApi.stop).mockResolvedValue({ sessionId: "sess-1", stopReason: "user_stop" } as never);
    renderToggle();
    const sw = screen.getByRole("switch");
    expect(sw).toHaveAttribute("aria-checked", "true");
    fireEvent.click(sw);
    await waitFor(() => expect(aiTradeApi.stop).toHaveBeenCalledWith("sess-1"));
  });
});
