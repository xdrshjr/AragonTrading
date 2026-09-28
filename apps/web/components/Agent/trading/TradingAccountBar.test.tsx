import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { agentTradingApi } from "@/lib/agentTradingApi";
import { useAppStore } from "@/lib/store";
import { TradingProvider } from "./TradingContext";
import TradingAccountBar from "./TradingAccountBar";

vi.mock("@/lib/agentTradingApi", () => ({ agentTradingApi: {
  capabilities: vi.fn(), binding: vi.fn(), bind: vi.fn(), unsettled: vi.fn(),
} }));
beforeEach(() => {
  vi.resetAllMocks();
  useAppStore.setState({ agentConversationId: "cid", accountMode: "live",
    agentTradingBindingPending: false, agentTradingBindingError: null });
  vi.mocked(agentTradingApi.binding).mockResolvedValue({ conversationId: "cid",
    accountId: 1, version: 3, environment: "demo", accountMode: "live", updatedTs: 1 });
  vi.mocked(agentTradingApi.capabilities).mockResolvedValue({ enabled: true,
    executionEnabled: true, recoveryReady: true, environment: "demo",
    supportedActions: [], accounts: [{ id: 1, label: "账户一", isDefault: true,
      configured: true }, { id: 2, label: "账户二", isDefault: false, configured: true }] });
  vi.mocked(agentTradingApi.unsettled).mockResolvedValue({ items: [], nextCursor: null });
});
afterEach(cleanup);

it("账户切换携带当前模式与CAS版本，失败保留原账户", async () => {
  vi.mocked(agentTradingApi.bind).mockRejectedValue(new Error("提交处理中，暂不能切换"));
  render(<TradingProvider><TradingAccountBar /></TradingProvider>);
  const selector = screen.getByRole("combobox") as HTMLSelectElement;
  await waitFor(() => expect(selector.disabled).toBe(false));
  fireEvent.change(selector, { target: { value: "2" } });
  await screen.findByText("提交处理中，暂不能切换");
  expect(selector.value).toBe("1");
  expect(agentTradingApi.bind).toHaveBeenCalledWith("cid", {
    accountId: 2, accountMode: "live", expectedVersion: 3,
  });
});

it("模式PUT失败仍显示原模式，等待时所有交易操作阻塞", async () => {
  let reject!: (error: Error) => void;
  vi.mocked(agentTradingApi.bind).mockReturnValue(new Promise((_, r) => { reject = r; }));
  act(() => useAppStore.getState().setAccountMode("sim"));
  expect(useAppStore.getState().accountMode).toBe("live");
  expect(useAppStore.getState().agentTradingBindingPending).toBe(true);
  await waitFor(() => expect(agentTradingApi.bind).toHaveBeenCalled());
  await act(async () => reject(new Error("提交处理中，暂不能切换")));
  expect(useAppStore.getState().accountMode).toBe("live");
  expect(useAppStore.getState().agentTradingBindingPending).toBe(false);
  expect(useAppStore.getState().agentTradingBindingError).toContain("暂不能切换");
});

it("本地模拟恢复旧实盘会话先持久同步模式版本", async () => {
  useAppStore.setState({ accountMode: "sim" });
  vi.mocked(agentTradingApi.bind).mockImplementation(async (cid, input) => {
    const binding = { conversationId: cid, ...input, version: 4,
      environment: "demo" as const, updatedTs: 2 };
    vi.mocked(agentTradingApi.binding).mockResolvedValue(binding);
    return binding;
  });
  render(<TradingProvider><TradingAccountBar /></TradingProvider>);
  await waitFor(() => expect(agentTradingApi.bind).toHaveBeenCalledWith("cid", {
    accountId: 1, accountMode: "sim", expectedVersion: 3,
  }));
  await waitFor(() => expect(
    (screen.getByRole("combobox") as HTMLSelectElement).disabled,
  ).toBe(false));
  expect(useAppStore.getState().accountMode).toBe("sim");
});

it("恢复会话的模式同步失败保持只读并显示错误", async () => {
  useAppStore.setState({ accountMode: "sim" });
  vi.mocked(agentTradingApi.bind).mockRejectedValue(new Error("绑定版本已变化"));
  render(<TradingProvider><TradingAccountBar /></TradingProvider>);
  await screen.findByText("绑定版本已变化");
  expect((screen.getByRole("combobox") as HTMLSelectElement).disabled).toBe(true);
  expect(useAppStore.getState().accountMode).toBe("sim");
});
