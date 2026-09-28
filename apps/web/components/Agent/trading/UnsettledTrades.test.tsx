import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { agentTradingApi } from "@/lib/agentTradingApi";
import { useAppStore } from "@/lib/store";
import UnsettledTrades from "./UnsettledTrades";

vi.mock("@/lib/agentTradingApi", () => ({ agentTradingApi: {
  unsettled: vi.fn(), intent: vi.fn(),
} }));
afterEach(cleanup);
it("清空后的新会话仍可找回旧会话订单，不依赖能力开关", async () => {
  vi.mocked(agentTradingApi.unsettled).mockResolvedValue({ items: [{ id: "intent",
    conversationId: "old-cid", status: "unknown",
    account: { label: "旧账户", environment: "live" },
    preview: { instId: "BTC-USDT-SWAP" } } as any], nextCursor: null });
  useAppStore.setState({ agentConversationId: "new-cid" });
  render(<UnsettledTrades />);
  fireEvent.click(await screen.findByRole("button", { name: "未结交易 1" }));
  await screen.findByText(/旧账户 · OKX 实盘.*结果待核实/);
  fireEvent.click(screen.getByRole("button", { name: "返回原会话" }));
  expect(useAppStore.getState().agentConversationId).toBe("old-cid");
});
it("查询失败不展示没有未结订单", async () => {
  vi.mocked(agentTradingApi.unsettled).mockRejectedValue(new Error("network"));
  render(<UnsettledTrades />);
  fireEvent.click(screen.getByRole("button", { name: /未结交易/ }));
  await screen.findByText(/未结交易读取失败/);
  expect(screen.queryByText("当前没有未结交易。")).toBeNull();
});
