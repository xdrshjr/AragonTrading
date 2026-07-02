import "@testing-library/jest-dom/vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { tradingApi } from "@/lib/tradingApi";
import AiTradeLaunchModal, { type AiTradeLaunchOpts } from "./AiTradeLaunchModal";

vi.mock("@/lib/tradingApi", () => ({ tradingApi: { config: vi.fn() } }));
vi.mock("@/lib/api", async (orig) => {
  const actual = (await orig()) as Record<string, unknown>;
  return {
    ...actual,
    api: {
      ...(actual.api as Record<string, unknown>),
      strategies: vi.fn().mockResolvedValue([
        { id: 1, name: "均线策略" },
        { id: 2, name: "动量策略" },
      ]),
    },
  };
});

function renderModal(props: Partial<React.ComponentProps<typeof AiTradeLaunchModal>> = {}) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const onConfirm = vi.fn();
  render(
    <QueryClientProvider client={qc}>
      <AiTradeLaunchModal
        open
        onClose={vi.fn()}
        onConfirm={onConfirm}
        submitting={false}
        acknowledged
        onAcknowledge={vi.fn()}
        bar="1H"
        defaultInstId="BTC-USDT-SWAP"
        defaultStrategyIds={[]}
        defaultContextRounds={10}
        {...props}
      />
    </QueryClientProvider>,
  );
  return { onConfirm };
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(tradingApi.config).mockResolvedValue({ availUsdt: 1000 } as never);
});

afterEach(() => cleanup());

describe("AiTradeLaunchModal", () => {
  it("renders the strategy multi-select from the strategies query", async () => {
    renderModal();
    expect(await screen.findByText("均线策略")).toBeInTheDocument();
    expect(screen.getByText("动量策略")).toBeInTheDocument();
  });

  it("keeps confirm disabled until at least one strategy is selected", async () => {
    renderModal();
    await screen.findByText("均线策略");
    const confirm = screen.getByRole("button", { name: /确认并开始 AI 自动交易/ });
    expect(confirm).toBeDisabled();
    expect(screen.getByText("请至少选择一个交易策略")).toBeInTheDocument();
  });

  it("confirms with the selected strategy ids", async () => {
    const { onConfirm } = renderModal();
    fireEvent.click(await screen.findByText("均线策略"));
    const confirm = screen.getByRole("button", { name: /确认并开始 AI 自动交易/ });
    await waitFor(() => expect(confirm).not.toBeDisabled());
    fireEvent.click(confirm);
    expect(onConfirm).toHaveBeenCalledTimes(1);
    const opts = onConfirm.mock.calls[0][0] as AiTradeLaunchOpts;
    expect(opts.strategyIds).toEqual([1]);
    expect(opts.instId).toBe("BTC-USDT-SWAP");
    expect(opts.contextRefreshRounds).toBe(10);
  });

  it("blocks confirm on an out-of-range context refresh count", async () => {
    renderModal({ defaultContextRounds: 1, defaultStrategyIds: [1] });
    await screen.findByText("均线策略");
    expect(screen.getByText(/上下文刷新轮数需为/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /确认并开始 AI 自动交易/ })).toBeDisabled();
  });

  it("blocks confirm when the instrument is not auto-tradable (spot)", async () => {
    renderModal({ defaultInstId: "ADA-USDT", defaultStrategyIds: [1] });
    await screen.findByText("均线策略");
    expect(screen.getByText(/标的不支持自动交易/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /确认并开始 AI 自动交易/ })).toBeDisabled();
  });

  it("blocks confirm on an invalid budget input", async () => {
    renderModal({ defaultStrategyIds: [1] });
    await screen.findByText("均线策略");
    // 总预算与杠杆共用「留空＝默认」占位，取首个（总预算）填入非法负值。
    fireEvent.change(screen.getAllByPlaceholderText("留空＝默认")[0], { target: { value: "-5" } });
    expect(screen.getByRole("button", { name: /确认并开始 AI 自动交易/ })).toBeDisabled();
  });

  it("confirms with per-position pct as null when blank (E4)", async () => {
    const { onConfirm } = renderModal({ defaultStrategyIds: [1] });
    await screen.findByText("均线策略");
    const confirm = screen.getByRole("button", { name: /确认并开始 AI 自动交易/ });
    await waitFor(() => expect(confirm).not.toBeDisabled());
    fireEvent.click(confirm);
    const opts = onConfirm.mock.calls[0][0] as AiTradeLaunchOpts;
    expect(opts.stopLossPct).toBeNull();
    expect(opts.takeProfitPct).toBeNull();
  });

  it("blocks confirm on an invalid per-position stop-loss pct (E4)", async () => {
    renderModal({ defaultStrategyIds: [1] });
    await screen.findByText("均线策略");
    // 「留空＝默认」占位顺序：[0]总预算 [1]杠杆 [2]单仓止损% [3]单仓止盈%
    fireEvent.change(screen.getAllByPlaceholderText("留空＝默认")[2], { target: { value: "-1" } });
    expect(screen.getByRole("button", { name: /确认并开始 AI 自动交易/ })).toBeDisabled();
  });

  it("clamps an out-of-range per-position pct in the preview, without blocking (E4)", async () => {
    renderModal({ defaultStrategyIds: [1] });
    await screen.findByText("均线策略");
    fireEvent.change(screen.getAllByPlaceholderText("留空＝默认")[2], { target: { value: "0.9" } });
    expect(screen.getByText(/SL 20% \/ TP 默认/)).toBeInTheDocument(); // 0.9 夹到 max 0.2 → 20%
    const confirm = screen.getByRole("button", { name: /确认并开始 AI 自动交易/ });
    await waitFor(() => expect(confirm).not.toBeDisabled());
  });
});
