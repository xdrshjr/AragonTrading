import "@testing-library/jest-dom/vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { autoTradeSettingsApi } from "@/lib/autoTradeSettingsApi";
import { tradingApi } from "@/lib/tradingApi";
import AiTradeLaunchModal, { type AiTradeLaunchOpts } from "./AiTradeLaunchModal";

vi.mock("@/lib/tradingApi", () => ({ tradingApi: { config: vi.fn() } }));
// 模块级 mock（P2-1）：写在文件顶层与 tradingApi mock 同级，否则其余用例的 settings 查询会触发真实 fetch。
vi.mock("@/lib/autoTradeSettingsApi", () => ({ autoTradeSettingsApi: { get: vi.fn() } }));
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
  // 每次调用都用全新的 [] 字面量播种 default 数组，方便回归测试模拟父树重渲染时传入的新引用。
  const element = (over: Partial<React.ComponentProps<typeof AiTradeLaunchModal>> = {}) => (
    <QueryClientProvider client={qc}>
      <AiTradeLaunchModal
        open
        onClose={vi.fn()}
        onConfirm={onConfirm}
        submitting={false}
        acknowledged
        onAcknowledge={vi.fn()}
        bar="1H"
        defaultMembers={[]}
        defaultAllocationMode="even"
        defaultInstId="BTC-USDT-SWAP"
        defaultStrategyIds={[]}
        defaultContextRounds={10}
        defaultPositionStepPct={0.25}
        {...props}
        {...over}
      />
    </QueryClientProvider>
  );
  const { rerender } = render(element());
  return {
    onConfirm,
    rerender: (over: Partial<React.ComponentProps<typeof AiTradeLaunchModal>> = {}) =>
      rerender(element(over)),
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(tradingApi.config).mockResolvedValue({ availUsdt: 1000 } as never);
  vi.mocked(autoTradeSettingsApi.get).mockResolvedValue({ maxBudgetPct: 100, effectiveMaxBudgetPct: 100 });
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

  it("confirms with the selected strategy ids and bar", async () => {
    const { onConfirm } = renderModal();
    fireEvent.click(await screen.findByText("均线策略"));
    const confirm = screen.getByRole("button", { name: /确认并开始 AI 自动交易/ });
    await waitFor(() => expect(confirm).not.toBeDisabled());
    fireEvent.click(confirm);
    expect(onConfirm).toHaveBeenCalledTimes(1);
    const opts = onConfirm.mock.calls[0][0] as AiTradeLaunchOpts;
    expect(opts.strategyIds).toEqual([1]);
    expect(opts.instId).toBe("BTC-USDT-SWAP");
    expect(opts.bar).toBe("1H");
    expect(opts.contextRefreshRounds).toBe(10);
  });

  it("auto-fills the member list from defaultMembers", async () => {
    renderModal({
      defaultStrategyIds: [1],
      defaultMembers: [
        { instId: "BTC-USDT-SWAP", allocationPct: 60 },
        { instId: "ETH-USDT-SWAP", allocationPct: 40 },
      ],
    });
    await screen.findByText("均线策略");
    expect(screen.getByText("BTC-USDT-SWAP")).toBeInTheDocument();
    expect(screen.getByText("ETH-USDT-SWAP")).toBeInTheDocument();
  });

  it("confirms with members auto-filled from defaultMembers", async () => {
    const { onConfirm } = renderModal({
      defaultStrategyIds: [1],
      defaultMembers: [
        { instId: "BTC-USDT-SWAP", allocationPct: 60 },
        { instId: "ETH-USDT-SWAP", allocationPct: 40 },
      ],
    });
    await screen.findByText("均线策略");
    const confirm = screen.getByRole("button", { name: /确认并开始 AI 自动交易/ });
    await waitFor(() => expect(confirm).not.toBeDisabled());
    fireEvent.click(confirm);
    const opts = onConfirm.mock.calls[0][0] as AiTradeLaunchOpts;
    expect(opts.members).toEqual([
      { instId: "BTC-USDT-SWAP", allocationPct: 60 },
      { instId: "ETH-USDT-SWAP", allocationPct: 40 },
    ]);
  });

  it("falls back to the current chart instId when defaultMembers is empty", async () => {
    const { onConfirm } = renderModal({ defaultStrategyIds: [1], defaultMembers: [] });
    await screen.findByText("均线策略");
    const confirm = screen.getByRole("button", { name: /确认并开始 AI 自动交易/ });
    await waitFor(() => expect(confirm).not.toBeDisabled());
    fireEvent.click(confirm);
    const opts = onConfirm.mock.calls[0][0] as AiTradeLaunchOpts;
    expect(opts.members).toEqual([{ instId: "BTC-USDT-SWAP", allocationPct: 100 }]);
  });

  it("caps auto-filled members at AI_TRADE_MAX_SYMBOLS and disables adding more", async () => {
    renderModal({
      defaultStrategyIds: [1],
      defaultMembers: Array.from({ length: 7 }, (_, i) => ({
        instId: `S${i}-USDT-SWAP`,
        allocationPct: 10,
      })),
    });
    await screen.findByText("均线策略");
    expect(screen.getByText("S0-USDT-SWAP")).toBeInTheDocument();
    expect(screen.getByText("S4-USDT-SWAP")).toBeInTheDocument();
    expect(screen.queryByText("S5-USDT-SWAP")).toBeNull(); // 仅前 5 个入选
    expect(screen.getByText(/已达 AI 自动交易标的上限 5/)).toBeInTheDocument();
    expect(screen.getByPlaceholderText(/已达上限 5 个/)).toBeDisabled();
  });

  it("seeds allocationMode from defaultAllocationMode (max_ratio)", async () => {
    const { onConfirm } = renderModal({
      defaultStrategyIds: [1],
      defaultAllocationMode: "max_ratio",
      defaultMembers: [{ instId: "BTC-USDT-SWAP", allocationPct: 100 }],
    });
    await screen.findByText("均线策略");
    const confirm = screen.getByRole("button", { name: /确认并开始 AI 自动交易/ });
    await waitFor(() => expect(confirm).not.toBeDisabled());
    fireEvent.click(confirm);
    const opts = onConfirm.mock.calls[0][0] as AiTradeLaunchOpts;
    expect(opts.allocationMode).toBe("max_ratio");
  });

  it("even-splits allocations to 100 across members", async () => {
    renderModal({
      defaultStrategyIds: [1],
      defaultMembers: [
        { instId: "BTC-USDT-SWAP", allocationPct: 10 },
        { instId: "ETH-USDT-SWAP", allocationPct: 10 },
      ],
    });
    await screen.findByText("均线策略");
    fireEvent.click(screen.getByRole("button", { name: "均分" }));
    expect(screen.getByLabelText("BTC-USDT-SWAP 百分比")).toHaveValue("50");
    expect(screen.getByLabelText("ETH-USDT-SWAP 百分比")).toHaveValue("50");
  });

  it("changing the editable timeframe flows into onConfirm", async () => {
    const { onConfirm } = renderModal({ defaultStrategyIds: [1] });
    await screen.findByText("均线策略");
    fireEvent.change(screen.getByLabelText("时间级别"), { target: { value: "4H" } });
    const confirm = screen.getByRole("button", { name: /确认并开始 AI 自动交易/ });
    await waitFor(() => expect(confirm).not.toBeDisabled());
    fireEvent.click(confirm);
    const opts = onConfirm.mock.calls[0][0] as AiTradeLaunchOpts;
    expect(opts.bar).toBe("4H");
  });

  it("blocks confirm on an out-of-range context refresh count", async () => {
    renderModal({ defaultContextRounds: 1, defaultStrategyIds: [1] });
    await screen.findByText("均线策略");
    expect(screen.getByText(/上下文刷新轮数需为/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /确认并开始 AI 自动交易/ })).toBeDisabled();
  });

  it("blocks confirm when a member is not auto-tradable (spot)", async () => {
    renderModal({
      defaultStrategyIds: [1],
      defaultMembers: [{ instId: "ADA-USDT", allocationPct: 100 }],
    });
    await screen.findByText("均线策略");
    expect(screen.getByText("不支持")).toBeInTheDocument();
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

  // 第 7 轮预算联动（ai-trade-total-budget-allocation §8.2）：改总预算即按 total×pct/100 重算每标的分配，
  // 天花板由账户级 effectiveMaxBudgetPct 主导（默认 100%），留空默认仍保守 1%。
  it("re-allocates each member budget from the edited total budget (AC-1)", async () => {
    renderModal({
      defaultStrategyIds: [1],
      defaultMembers: [
        { instId: "BTC-USDT-SWAP", allocationPct: 50 },
        { instId: "ETH-USDT-SWAP", allocationPct: 50 },
      ],
    });
    await screen.findByText("均线策略");
    // availUsdt=1000、maxBudgetPct=100 → 总预算改 800 后每标的 = 800×50% = 400（修复前压死在 avail×5% 的小值）。
    fireEvent.change(screen.getAllByPlaceholderText("留空＝默认")[0], { target: { value: "800" } });
    await waitFor(() => expect(screen.getAllByText("400.00 USDT")).toHaveLength(2));
  });

  it("shows the clamp notice and clamps member previews when total exceeds the cap (AC-2)", async () => {
    vi.mocked(autoTradeSettingsApi.get).mockResolvedValue({ maxBudgetPct: 100, effectiveMaxBudgetPct: 5 });
    renderModal({
      defaultStrategyIds: [1],
      defaultMembers: [
        { instId: "BTC-USDT-SWAP", allocationPct: 50 },
        { instId: "ETH-USDT-SWAP", allocationPct: 50 },
      ],
    });
    await screen.findByText("均线策略");
    // 输入 800 > avail(1000)×effectiveMaxBudgetPct(5%)=50 → 夹到 50，每标的 50×50% = 25，并回显「已按 5% 上限夹紧」。
    fireEvent.change(screen.getAllByPlaceholderText("留空＝默认")[0], { target: { value: "800" } });
    await waitFor(() => expect(screen.getAllByText("25.00 USDT")).toHaveLength(2));
    expect(screen.getByText(/已按 5% 上限夹紧/)).toBeInTheDocument();
  });

  it("keeps the blank-total default at 1% of the available balance (AC-3)", async () => {
    renderModal({
      defaultStrategyIds: [1],
      defaultMembers: [
        { instId: "BTC-USDT-SWAP", allocationPct: 50 },
        { instId: "ETH-USDT-SWAP", allocationPct: 50 },
      ],
    });
    await screen.findByText("均线策略");
    // 留空总预算 → 默认 avail(1000)×1% = 10，标签保持「可用余额 × 1%」，每标的 10×50% = 5。
    expect(screen.getByText("默认总预算（可用余额 × 1%）")).toBeInTheDocument();
    await waitFor(() => expect(screen.getAllByText("5.00 USDT")).toHaveLength(2));
  });

  // 仓位操作档位（ai-trade-position-sizing-steps §六·7）：AI 每次开仓规模只能落在用户所选离散档位上。
  it("renders the position-step select seeded from defaultPositionStepPct", async () => {
    renderModal({ defaultStrategyIds: [1] });
    await screen.findByText("均线策略");
    const select = screen.getByLabelText("仓位操作档位") as HTMLSelectElement;
    expect(select.value).toBe("0.25"); // 默认（aiTradeConfig 缺省 → AI_TRADE_DEFAULT_POSITION_STEP）
    expect(screen.getByText(/可用档位：25% \/ 50% \/ 75% \/ 100%/)).toBeInTheDocument();
    expect(screen.getByText(/非分批加\/减仓，平仓为全平/)).toBeInTheDocument(); // [R-10] 提示文案
  });

  it("seeds the position step from the persisted preference prop", async () => {
    renderModal({ defaultStrategyIds: [1], defaultPositionStepPct: 0.5 });
    await screen.findByText("均线策略");
    expect((screen.getByLabelText("仓位操作档位") as HTMLSelectElement).value).toBe("0.5");
    expect(screen.getByText(/可用档位：50% \/ 100%/)).toBeInTheDocument();
  });

  it("flows the chosen position step into onConfirm (live path)", async () => {
    const { onConfirm } = renderModal({ defaultStrategyIds: [1] });
    await screen.findByText("均线策略");
    fireEvent.change(screen.getByLabelText("仓位操作档位"), { target: { value: "0.5" } });
    const confirm = screen.getByRole("button", { name: /确认并开始 AI 自动交易/ });
    await waitFor(() => expect(confirm).not.toBeDisabled());
    fireEvent.click(confirm);
    expect((onConfirm.mock.calls[0][0] as AiTradeLaunchOpts).positionStepPct).toBe(0.5);
  });

  it("carries the position step into onConfirm on the backtest path too", async () => {
    const { onConfirm } = renderModal({ defaultStrategyIds: [1] });
    await screen.findByText("均线策略");
    fireEvent.click(screen.getByRole("button", { name: "回测" }));
    fireEvent.change(screen.getByLabelText("仓位操作档位"), { target: { value: "0.1" } });
    const confirm = screen.getByRole("button", { name: /开始 AI 回测/ });
    await waitFor(() => expect(confirm).not.toBeDisabled());
    fireEvent.click(confirm);
    const opts = onConfirm.mock.calls[0][0] as AiTradeLaunchOpts;
    expect(opts.mode).toBe("backtest");
    expect(opts.positionStepPct).toBe(0.1);
  });

  it("resets the position step to default when the modal reopens (guards inputs-revert)", async () => {
    const { rerender } = renderModal({ defaultStrategyIds: [1] });
    await screen.findByText("均线策略");
    fireEvent.change(screen.getByLabelText("仓位操作档位"), { target: { value: "1" } });
    expect((screen.getByLabelText("仓位操作档位") as HTMLSelectElement).value).toBe("1");
    // 关闭后重开：seed effect 依赖 [open]，false→true 复位回默认档位。
    rerender({ defaultStrategyIds: [1], open: false });
    rerender({ defaultStrategyIds: [1], open: true });
    expect((screen.getByLabelText("仓位操作档位") as HTMLSelectElement).value).toBe("0.25");
  });
});

const HOUR = 3600;
const START = 1_700_000_040; // 分钟对齐：datetime-local 往返丢秒，避免断言偏移

describe("AiTradeLaunchModal · backtest mode", () => {
  it("switching to backtest hides the real-money ack and shows range/interval fields", async () => {
    renderModal({ defaultStrategyIds: [1] });
    await screen.findByText("均线策略");
    fireEvent.click(screen.getByRole("button", { name: "回测" }));
    expect(screen.queryByLabelText(/我已知晓这是真实资金/)).toBeNull();
    expect(screen.getByText("回测起始")).toBeInTheDocument();
    expect(screen.getByText("回测结束")).toBeInTheDocument();
    expect(screen.getByText("决策间隔（根/次）")).toBeInTheDocument();
    expect(screen.getByText("模拟回测")).toBeInTheDocument();
  });

  it("defaults the backtest instId to the first auto-trade member", async () => {
    renderModal({
      defaultStrategyIds: [1],
      defaultMembers: [{ instId: "ETH-USDT-SWAP", allocationPct: 100 }],
    });
    await screen.findByText("均线策略");
    fireEvent.click(screen.getByRole("button", { name: "回测" }));
    expect(screen.getByDisplayValue("ETH-USDT-SWAP")).toBeInTheDocument();
  });

  it("backtest confirm does NOT require acknowledged and passes mode+range+bar", async () => {
    const { onConfirm } = renderModal({
      acknowledged: false,
      defaultStrategyIds: [1],
      defaultStartTs: START,
      defaultEndTs: START + 100 * HOUR,
    });
    await screen.findByText("均线策略");
    fireEvent.click(screen.getByRole("button", { name: "回测" }));
    const confirm = screen.getByRole("button", { name: /开始 AI 回测/ });
    await waitFor(() => expect(confirm).not.toBeDisabled()); // 无 acknowledged 也可
    fireEvent.click(confirm);
    expect(onConfirm).toHaveBeenCalledTimes(1);
    const opts = onConfirm.mock.calls[0][0] as AiTradeLaunchOpts;
    expect(opts.mode).toBe("backtest");
    expect(opts.strategyIds).toEqual([1]);
    expect(opts.bar).toBe("1H");
    expect(opts.startTs).toBe(START);
    expect(opts.endTs).toBe(START + 100 * HOUR);
    expect(opts.decisionEveryBars).toBeGreaterThanOrEqual(1);
  });

  it("disables the backtest CTA when planned rounds exceed the cap (R5)", async () => {
    renderModal({
      defaultStrategyIds: [1],
      defaultStartTs: START,
      defaultEndTs: START + 500 * HOUR, // 501 根，间隔 1 → 501 轮 > 400 上限
      defaultDecisionEveryBars: 1,
    });
    await screen.findByText("均线策略");
    fireEvent.click(screen.getByRole("button", { name: "回测" }));
    expect(screen.getByText(/计划决策轮数 501 超上限 400/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /开始 AI 回测/ })).toBeDisabled();
  });

  it("keeps the live segment gating intact when live is available", async () => {
    renderModal({ defaultStrategyIds: [1], acknowledged: false });
    await screen.findByText("均线策略");
    // 默认落实盘段，未勾选 acknowledged → CTA 禁用
    expect(screen.getByRole("button", { name: /确认并开始 AI 自动交易/ })).toBeDisabled();
  });
});
