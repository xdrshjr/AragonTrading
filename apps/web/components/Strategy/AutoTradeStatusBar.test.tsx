import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { liveAutoApi } from "@/lib/liveAutoApi";
import type { LiveAutoStatus } from "@/lib/liveAutoTypes";
import { useAppStore } from "@/lib/store";
import AutoTradeStatusBar from "./AutoTradeStatusBar";

vi.mock("@/lib/liveAutoApi", () => ({
  liveAutoApi: { status: vi.fn(), start: vi.fn(), stop: vi.fn() },
  liveAutoWS: vi.fn(() => ({ connect: vi.fn(), close: vi.fn() })),
}));

function renderBar() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <AutoTradeStatusBar />
    </QueryClientProvider>,
  );
}

function setStatus(status: LiveAutoStatus | null) {
  useAppStore.setState({ liveAutoSessionId: "liveauto_x", liveAutoStatus: status });
}

beforeEach(() => {
  vi.clearAllMocks();
  useAppStore.setState({
    liveAutoSessionId: null,
    liveAutoStatus: null,
    liveAutoPosition: null,
    liveAutoTrades: [],
    liveAutoEvents: [],
  });
  // 停止后 status 接口已无活跃会话。
  vi.mocked(liveAutoApi.status).mockResolvedValue({ session: null, liveAutoTradingEnabled: true });
});
afterEach(cleanup);

describe("AutoTradeStatusBar — terminal banner (H7)", () => {
  it("renders stopped banner with Chinese stop reason and session pnl", async () => {
    setStatus({ state: "stopped", stopReason: "session_stop_loss", sessionPnlUsd: -12.5 });
    renderBar();
    expect(await screen.findByText("自动交易已停止")).toBeTruthy();
    expect(screen.getByText("触发会话止损")).toBeTruthy();
    expect(screen.getByText(/会话盈亏/)).toBeTruthy();
  });

  it("escalates to manual-flatten alert when flattenFailed", async () => {
    setStatus({
      state: "stopped",
      stopReason: "user_stop",
      sessionPnlUsd: 0,
      flattenFailed: true,
    });
    renderBar();
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toMatch(/自动平仓失败/);
    expect(alert.textContent).toMatch(/手动平仓/);
  });

  it("close button clears the live-auto runtime", async () => {
    setStatus({ state: "stopped", stopReason: "user_stop", sessionPnlUsd: 0 });
    renderBar();
    fireEvent.click(await screen.findByRole("button", { name: "关闭" }));
    await waitFor(() => expect(useAppStore.getState().liveAutoSessionId).toBeNull());
  });
});

describe("AutoTradeStatusBar — running degraded badge (H7)", () => {
  it("shows a 连接异常 badge while running and degraded", async () => {
    setStatus({ state: "running", sessionPnlUsd: 1.0, degraded: true });
    renderBar();
    expect(await screen.findByText("自动交易运行中")).toBeTruthy();
    expect(screen.getByText("连接异常")).toBeTruthy();
  });

  it("hides the badge when not degraded", async () => {
    setStatus({ state: "running", sessionPnlUsd: 1.0 });
    renderBar();
    expect(await screen.findByText("自动交易运行中")).toBeTruthy();
    expect(screen.queryByText("连接异常")).toBeNull();
  });
});
