import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { agentTradingApi } from "@/lib/agentTradingApi";
import type { TradeIntentView } from "@/lib/agentTradingTypes";
import { useAppStore } from "@/lib/store";
import { mergeIntent, TradingProvider } from "./TradingContext";
import TradeIntentCard from "./TradeIntentCard";

vi.mock("@/lib/agentTradingApi", () => ({ agentTradingApi: {
  capabilities: vi.fn(), binding: vi.fn(), intent: vi.fn(),
  execute: vi.fn(), dismiss: vi.fn(), bind: vi.fn(),
} }));

function intent(overrides: Partial<TradeIntentView> = {}): TradeIntentView {
  return { schemaVersion: 1, id: crypto.randomUUID(), conversationId: "cid",
    version: 1, action: "open", status: "prepared", bindingVersion: 1,
    createdTs: 1, expiresTs: Date.now() / 1000 + 60, previewHash: "frozen",
    account: { id: 1, label: "演示账户", environment: "demo" },
    preview: { instId: "BTC-USDT-SWAP", direction: "long", ordType: "market",
      sizeContracts: "2", warnings: [], observedTs: 1 },
    result: null, error: null, canExecute: true, ...overrides,
  } as TradeIntentView;
}

beforeEach(() => {
  vi.resetAllMocks();
  sessionStorage.clear();
  useAppStore.setState({ agentConversationId: "cid", accountMode: "live",
    agentTradingBindingPending: false });
  vi.mocked(agentTradingApi.capabilities).mockResolvedValue({ enabled: true,
    executionEnabled: true, recoveryReady: true, environment: "demo",
    supportedActions: ["open"], accounts: [] });
  vi.mocked(agentTradingApi.binding).mockResolvedValue({ conversationId: "cid",
    accountId: 1, accountMode: "live", environment: "demo", version: 1,
    updatedTs: 1 });
});
afterEach(cleanup);

it("历史卡先查询真值，同tick双击只执行一次且不把受理当成交", async () => {
  const preview = intent();
  let resolve!: (value: TradeIntentView) => void;
  vi.mocked(agentTradingApi.intent).mockReturnValue(new Promise((r) => { resolve = r; }));
  vi.mocked(agentTradingApi.execute).mockResolvedValue({ ...preview,
    version: 2, status: "accepted", canExecute: false });
  render(<TradingProvider><TradeIntentCard intent={preview} /></TradingProvider>);
  const button = screen.getByRole("button", { name: "提交买多" }) as HTMLButtonElement;
  expect(button.disabled).toBe(true);
  await act(async () => resolve(preview));
  await waitFor(() => expect(button.disabled).toBe(false));
  act(() => { fireEvent.click(button); fireEvent.click(button); });
  await screen.findByText("委托已受理");
  expect(agentTradingApi.execute).toHaveBeenCalledTimes(1);
  expect(screen.queryByText("已成交")).toBeNull();
});

it("提交超时后只查询，重新挂载也不恢复提交按钮", async () => {
  const preview = intent();
  vi.mocked(agentTradingApi.intent).mockResolvedValue(preview);
  vi.mocked(agentTradingApi.execute).mockRejectedValue(new Error("timeout"));
  const view = render(<TradingProvider><TradeIntentCard intent={preview} /></TradingProvider>);
  const button = screen.getByRole("button", { name: "提交买多" }) as HTMLButtonElement;
  await waitFor(() => expect(button.disabled).toBe(false));
  fireEvent.click(button);
  await screen.findByText("结果待核实");
  expect(screen.queryByRole("button", { name: "提交买多" })).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "查询状态" }));
  expect(agentTradingApi.execute).toHaveBeenCalledTimes(1);
  view.unmount();
  render(<TradingProvider><TradeIntentCard intent={preview} /></TradingProvider>);
  await screen.findByText("结果待核实");
  expect(screen.queryByRole("button", { name: "提交买多" })).toBeNull();
});

it("旧版本与跨会话响应不覆写当前真值", () => {
  const current = intent({ version: 5, status: "filled" });
  const cache = { [current.id]: current };
  expect(mergeIntent(cache, { ...current, version: 4 }, "cid")).toBe(cache);
  expect(mergeIntent(cache, { ...current, conversationId: "other" }, "cid")).toBe(cache);
});

it("过期不可提交，部分成交和撤销仍保留真实成交数量", async () => {
  const preview = intent({ expiresTs: 1 });
  vi.mocked(agentTradingApi.intent).mockResolvedValue(preview);
  const view = render(<TradingProvider><TradeIntentCard intent={preview} /></TradingProvider>);
  await waitFor(() => expect(agentTradingApi.intent).toHaveBeenCalled());
  expect((screen.getByRole("button", { name: "提交买多" }) as HTMLButtonElement).disabled).toBe(true);
  view.unmount();
  const canceled = intent({ status: "canceled", result: { ordId: "o1",
    clOrdId: "c1", exchangeState: "canceled", accFillSz: "1", avgPx: "60000",
    lastCheckedTs: 1 } });
  vi.mocked(agentTradingApi.intent).mockResolvedValue(canceled);
  render(<TradeIntentCard intent={canceled} readOnly />);
  await waitFor(() => expect(screen.queryByText(/正在核对当前状态/)).toBeNull());
  expect(screen.getByText(/累计成交 1 张/)).toBeTruthy();
  expect(screen.getByText("已撤销")).toBeTruthy();
});

it("会话切换后迟到响应不能更新原卡", async () => {
  const preview = intent();
  let resolve!: (value: TradeIntentView) => void;
  vi.mocked(agentTradingApi.intent).mockReturnValue(new Promise((r) => { resolve = r; }));
  const view = render(<TradingProvider><TradeIntentCard intent={preview} /></TradingProvider>);
  act(() => useAppStore.getState().setAgentConversationId("new-cid"));
  view.unmount();
  await act(async () => resolve({ ...preview, status: "filled", version: 10 }));
  expect(agentTradingApi.execute).not.toHaveBeenCalled();
});

it("本地模拟下返回旧实盘会话不能提交历史预览", async () => {
  const preview = intent();
  useAppStore.setState({ accountMode: "sim" });
  vi.mocked(agentTradingApi.intent).mockResolvedValue(preview);
  render(<TradingProvider><TradeIntentCard intent={preview} /></TradingProvider>);
  await waitFor(() => expect(screen.queryByText(/正在核对当前状态/)).toBeNull());
  const button = screen.getByRole("button", { name: "提交买多" });
  expect((button as HTMLButtonElement).disabled).toBe(true);
  fireEvent.click(button);
  expect(agentTradingApi.execute).not.toHaveBeenCalled();
});

it("提交途中重连后恢复状态查询按钮且不重复提交", async () => {
  const preview = intent();
  let finish!: (value: TradeIntentView) => void;
  vi.mocked(agentTradingApi.intent).mockResolvedValue(preview);
  vi.mocked(agentTradingApi.execute).mockReturnValue(
    new Promise((resolve) => { finish = resolve; }),
  );
  render(<TradingProvider><TradeIntentCard intent={preview} /></TradingProvider>);
  const submit = screen.getByRole("button", { name: "提交买多" });
  await waitFor(() => expect((submit as HTMLButtonElement).disabled).toBe(false));
  fireEvent.click(submit);
  const accepted = { ...preview, version: 2, status: "accepted" as const,
    canExecute: false };
  vi.mocked(agentTradingApi.intent).mockResolvedValue(accepted);
  act(() => useAppStore.setState((state) => ({
    agentTradingBindingRevision: state.agentTradingBindingRevision + 1,
  })));
  await screen.findByText("委托已受理");
  await act(async () => finish(accepted));
  expect((screen.getByRole("button", {
    name: "查询状态",
  }) as HTMLButtonElement).disabled).toBe(false);
  expect(agentTradingApi.execute).toHaveBeenCalledTimes(1);
});

it("平仓预览明确展示仓位方向与实际委托方向", async () => {
  const preview = intent({ action: "close" });
  preview.preview = { ...preview.preview,
    direction: "short", posSide: "short", side: "buy" };
  vi.mocked(agentTradingApi.intent).mockResolvedValue(preview);
  render(<TradingProvider><TradeIntentCard intent={preview} /></TradingProvider>);
  await waitFor(() => expect(screen.queryByText(/正在核对当前状态/)).toBeNull());
  expect(screen.getByText("空头")).toBeTruthy();
  expect(screen.getByText("买入")).toBeTruthy();
});
