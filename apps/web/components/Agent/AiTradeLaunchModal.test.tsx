import "@testing-library/jest-dom/vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AI_TRADE_BASE_RULE_DEFAULTS, AI_TRADE_PROFIT_GUARD_DEFAULTS } from "@/lib/aiTrade";
import { aiTradeHorizonApi } from "@/lib/aiTradeHorizonApi";
import { autoTradeSettingsApi } from "@/lib/autoTradeSettingsApi";
import { tradingApi } from "@/lib/tradingApi";
import AiTradeLaunchModal, { type AiTradeLaunchOpts } from "./AiTradeLaunchModal";

vi.mock("@/lib/tradingApi", () => ({ tradingApi: { config: vi.fn() } }));
// 交易周期模式预设表：与上面两个同级的模块级 mock——弹窗每次打开都会拉它，不 mock 会触发真实 fetch。
vi.mock("@/lib/aiTradeHorizonApi", () => ({ aiTradeHorizonApi: { profiles: vi.fn() } }));
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
        defaultBaseRule={AI_TRADE_BASE_RULE_DEFAULTS}
        defaultProfitGuard={AI_TRADE_PROFIT_GUARD_DEFAULTS}
        defaultMembers={[]}
        defaultAllocationMode="even"
        defaultInstId="BTC-USDT-SWAP"
        defaultStrategyIds={[]}
        defaultContextRounds={10}
        defaultPositionStepPct={0.25}
        defaultWakeIntervalMinutes={2}
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
  vi.mocked(aiTradeHorizonApi.profiles).mockResolvedValue(HORIZON_PROFILES);
});

// 后端 `GET /ai-trade/horizon-profiles` 的最小夹具（数值与中文标签一律来自服务端，前端不复制，R-3）。
const HORIZON_PROFILES = {
  enabled: true,
  defaultProfile: "intraday_swing" as const,
  estimatedCostBps: 13,
  lossReleaseFrac: 0.5,
  lossReleaseReturnPctFallback: 0.1,
  profiles: [
    {
      id: "intraday_swing" as const, title: "日内波段", description: "小时级别一段趋势。",
      targetLabel: "4 – 12 小时", bar: "1H", wakeIntervalMinutes: 30, minHoldSeconds: 7200,
      exitConfirmRounds: 2, turnoverHysteresisK: 1.5, targetHoldMinSeconds: 14400,
      targetHoldMaxSeconds: 43200, expectedHoldBars: 12, confirmWindowSeconds: 3600,
      leverageMaxHint: 3,
    },
    {
      id: "trend_hold" as const, title: "趋势持仓", description: "跟随一段中线趋势。",
      targetLabel: "1 – 3 天", bar: "4H", wakeIntervalMinutes: 0, minHoldSeconds: 43200,
      exitConfirmRounds: 3, turnoverHysteresisK: 2, targetHoldMinSeconds: 86400,
      targetHoldMaxSeconds: 259200, expectedHoldBars: 18, confirmWindowSeconds: 21600,
      leverageMaxHint: 2,
    },
    {
      id: "custom" as const, title: "自定义", description: "自行设定。", targetLabel: null,
      bar: null, wakeIntervalMinutes: null, minHoldSeconds: null, exitConfirmRounds: null,
      turnoverHysteresisK: null, targetHoldMinSeconds: null, targetHoldMaxSeconds: null,
      expectedHoldBars: null, confirmWindowSeconds: null, leverageMaxHint: null,
    },
  ],
};

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

  // ---------- 【基础策略】铁律区块（ai-trade-base-strategy-ma-band §6.2 用例 29）----------
  it("默认勾选基础策略，提交体带完整规则参数", async () => {
    const { onConfirm } = renderModal({ defaultStrategyIds: [1] });
    await screen.findByText("均线策略");
    const box = screen.getByRole("checkbox", { name: "基础策略（铁律 · 默认开启）" });
    expect(box).toBeChecked();
    const confirm = screen.getByRole("button", { name: /确认并开始 AI 自动交易/ });
    await waitFor(() => expect(confirm).not.toBeDisabled());
    fireEvent.click(confirm);
    const opts = onConfirm.mock.calls[0][0] as AiTradeLaunchOpts;
    expect(opts.baseRules).toEqual({
      maBandEntry: { enabled: true, period: 20, stdMult: 2, tolerance: 0.15, directional: false },
    });
  });

  it("取消勾选后提交体为显式 enabled:false（与「字段缺省=默认开启」严格区分）", async () => {
    const { onConfirm } = renderModal({ defaultStrategyIds: [1] });
    await screen.findByText("均线策略");
    fireEvent.click(screen.getByRole("checkbox", { name: "基础策略（铁律 · 默认开启）" }));
    const confirm = screen.getByRole("button", { name: /确认并开始 AI 自动交易/ });
    await waitFor(() => expect(confirm).not.toBeDisabled());
    fireEvent.click(confirm);
    const opts = onConfirm.mock.calls[0][0] as AiTradeLaunchOpts;
    expect(opts.baseRules).toEqual({ maBandEntry: { enabled: false } });
  });

  it("高级参数回填上次所选并可编辑，越界周期即时报错并禁用确认", async () => {
    const { onConfirm } = renderModal({
      defaultStrategyIds: [1],
      defaultBaseRule: { enabled: true, period: 30, stdMult: 2.5, tolerance: 0.05, directional: true },
    });
    await screen.findByText("均线策略");
    const period = screen.getByLabelText("基础策略均线周期") as HTMLInputElement;
    expect(period.value).toBe("30");
    expect(screen.getByLabelText("基础策略方向性模式")).toBeChecked();

    fireEvent.change(period, { target: { value: "1" } });
    expect(screen.getByText("基础策略均线周期需为 2~200 的整数")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /确认并开始 AI 自动交易/ })).toBeDisabled();

    fireEvent.change(period, { target: { value: "40" } });
    const confirm = screen.getByRole("button", { name: /确认并开始 AI 自动交易/ });
    await waitFor(() => expect(confirm).not.toBeDisabled());
    fireEvent.click(confirm);
    const opts = onConfirm.mock.calls[0][0] as AiTradeLaunchOpts;
    expect(opts.baseRules.maBandEntry).toEqual({
      enabled: true, period: 40, stdMult: 2.5, tolerance: 0.05, directional: true,
    });
  });

  it("[R-7] 父树重渲染不冲掉正在编辑的基础策略参数（seed effect 依赖仍为 [open]）", async () => {
    const { rerender } = renderModal({ defaultStrategyIds: [1] });
    await screen.findByText("均线策略");
    const period = screen.getByLabelText("基础策略均线周期") as HTMLInputElement;
    fireEvent.change(period, { target: { value: "55" } });
    rerender({ defaultStrategyIds: [1] });          // 新引用的 default 数组，模拟父树每秒重渲染
    await waitFor(() =>
      expect((screen.getByLabelText("基础策略均线周期") as HTMLInputElement).value).toBe("55"),
    );
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
    // 单仓 OCO 的占位文案已被利润保护特性改写为具体默认值，故按标签取元素（比占位序号稳）。
    fireEvent.change(screen.getByLabelText("单仓止损%（如 0.05＝5%）"), { target: { value: "-1" } });
    expect(screen.getByRole("button", { name: /确认并开始 AI 自动交易/ })).toBeDisabled();
  });

  it("clamps an out-of-range per-position pct in the preview, without blocking (E4)", async () => {
    renderModal({ defaultStrategyIds: [1] });
    await screen.findByText("均线策略");
    fireEvent.change(screen.getByLabelText("单仓止损%（如 0.05＝5%）"), { target: { value: "0.9" } });
    // 双口径显性化后：填值夹到 max 0.2 → 20%；留空侧如实写出生效默认 10%（不再只写「默认」）。
    expect(screen.getByText(/SL 20% \/ TP 10%（默认）/)).toBeInTheDocument();
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

  // Agent 墙钟定时唤醒周期（ai-auto-trading-wake-interval §6.2）：仅实盘段露出，默认 2 分钟；
  // 空/0 = 关闭（合法、送 0）；越界即时报错阻止提交；改值随 onConfirm 的 live opts 透传（回测段不渲染）。
  it("renders the wake-interval input in the live segment seeded from the default (2 min)", async () => {
    renderModal({ defaultStrategyIds: [1] });
    await screen.findByText("均线策略");
    const input = screen.getByLabelText("Agent 唤醒周期（分钟）") as HTMLInputElement;
    expect(input.value).toBe("2");
    expect(screen.getByText(/仅按 K 线与策略触发/)).toBeInTheDocument();
  });

  it("seeds the wake interval from the persisted preference prop", async () => {
    renderModal({ defaultStrategyIds: [1], defaultWakeIntervalMinutes: 5 });
    await screen.findByText("均线策略");
    expect((screen.getByLabelText("Agent 唤醒周期（分钟）") as HTMLInputElement).value).toBe("5");
  });

  it("does NOT render the wake-interval input in the backtest segment", async () => {
    renderModal({ defaultStrategyIds: [1] });
    await screen.findByText("均线策略");
    fireEvent.click(screen.getByRole("button", { name: "回测" }));
    expect(screen.queryByLabelText("Agent 唤醒周期（分钟）")).toBeNull();
  });

  it("flows the chosen wake interval into onConfirm (live path)", async () => {
    const { onConfirm } = renderModal({ defaultStrategyIds: [1] });
    await screen.findByText("均线策略");
    fireEvent.change(screen.getByLabelText("Agent 唤醒周期（分钟）"), { target: { value: "3" } });
    const confirm = screen.getByRole("button", { name: /确认并开始 AI 自动交易/ });
    await waitFor(() => expect(confirm).not.toBeDisabled());
    fireEvent.click(confirm);
    expect((onConfirm.mock.calls[0][0] as AiTradeLaunchOpts).wakeIntervalMinutes).toBe(3);
  });

  it("treats a blank wake interval as disabled (sends 0) and stays submittable", async () => {
    const { onConfirm } = renderModal({ defaultStrategyIds: [1] });
    await screen.findByText("均线策略");
    fireEvent.change(screen.getByLabelText("Agent 唤醒周期（分钟）"), { target: { value: "" } });
    const confirm = screen.getByRole("button", { name: /确认并开始 AI 自动交易/ });
    await waitFor(() => expect(confirm).not.toBeDisabled());
    fireEvent.click(confirm);
    expect((onConfirm.mock.calls[0][0] as AiTradeLaunchOpts).wakeIntervalMinutes).toBe(0);
  });

  it("blocks confirm on an out-of-range wake interval", async () => {
    renderModal({ defaultStrategyIds: [1] });
    await screen.findByText("均线策略");
    fireEvent.change(screen.getByLabelText("Agent 唤醒周期（分钟）"), { target: { value: "9999" } });
    expect(screen.getByText(/Agent 唤醒周期需为/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /确认并开始 AI 自动交易/ })).toBeDisabled();
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

  it("resets the wake interval to default when the modal reopens (RN-2 guards inputs-revert)", async () => {
    const { rerender } = renderModal({ defaultStrategyIds: [1] });
    await screen.findByText("均线策略");
    fireEvent.change(screen.getByLabelText("Agent 唤醒周期（分钟）"), { target: { value: "9" } });
    expect((screen.getByLabelText("Agent 唤醒周期（分钟）") as HTMLInputElement).value).toBe("9");
    // 关闭后重开：seed 只在 open false→true 那一次转换执行（单行 setWakeIntervalRaw 在既有 [open] effect 内）。
    rerender({ defaultStrategyIds: [1], open: false });
    rerender({ defaultStrategyIds: [1], open: true });
    expect((screen.getByLabelText("Agent 唤醒周期（分钟）") as HTMLInputElement).value).toBe("2");
  });

  // 复发根因回归护栏（ai-trade-launch-modal-inputs-revert-recurrence，方案 A）：
  // seed effect 依赖收敛为 [open] 后，弹窗 open 恒为真、父树以 ~1Hz 重渲染并传入全新的
  // defaultStrategyIds/defaultMembers 引用时，effect 不再重跑，用户正在编辑的字段不被冲回默认。
  // 缺陷版（依赖含 seededMembers/defaultStrategyIds）下本用例会失败：编辑约 1 帧后即被重置。
  it("keeps user edits while open when the parent re-renders with new default refs (guards inputs-revert recurrence)", async () => {
    const { onConfirm, rerender } = renderModal({ defaultStrategyIds: [1] });
    await screen.findByText("均线策略");

    // 用户编辑多个字段：追加勾选策略、改仓位档位、改唤醒周期、改时间级别、填总预算。
    fireEvent.click(screen.getByText("动量策略")); // selectedIds: [1] → [1, 2]
    fireEvent.change(screen.getByLabelText("仓位操作档位"), { target: { value: "0.5" } });
    fireEvent.change(screen.getByLabelText("Agent 唤醒周期（分钟）"), { target: { value: "7" } });
    fireEvent.change(screen.getByLabelText("时间级别"), { target: { value: "4H" } });
    fireEvent.change(screen.getAllByPlaceholderText("留空＝默认")[0], { target: { value: "500" } });
    fireEvent.change(screen.getByLabelText("允许回吐（%）"), { target: { value: "55" } });
    fireEvent.change(screen.getByLabelText("ATR 下界倍数（0=关）"), { target: { value: "0.4" } });
    fireEvent.change(screen.getByLabelText("平仓后冷却（秒，0=关）"), { target: { value: "90" } });
    fireEvent.change(screen.getByLabelText("回吐阶梯"), { target: { value: "tight" } });

    // 模拟行情流节流 flush 引发的父树重渲染：open 恒为真，但每帧传入全新的默认数组引用。
    rerender({ defaultStrategyIds: [1], defaultMembers: [{ instId: "ETH-USDT-SWAP", allocationPct: 100 }] });
    rerender({ defaultStrategyIds: [1], defaultMembers: [{ instId: "ETH-USDT-SWAP", allocationPct: 100 }] });

    // 未发生 open false→true 转换 → seed effect 不重跑，全部编辑保持不变（RN-2：新增唤醒周期字段同守护）。
    expect((screen.getByLabelText("仓位操作档位") as HTMLSelectElement).value).toBe("0.5");
    expect((screen.getByLabelText("Agent 唤醒周期（分钟）") as HTMLInputElement).value).toBe("7");
    expect((screen.getByLabelText("时间级别") as HTMLSelectElement).value).toBe("4H");
    // 本轮新增的利润保护输入同守（复发性缺陷的教训：每加一个新字段就要在此加一条断言）。
    expect((screen.getByLabelText("允许回吐（%）") as HTMLInputElement).value).toBe("55");
    expect((screen.getByLabelText("ATR 下界倍数（0=关）") as HTMLInputElement).value).toBe("0.4");
    expect((screen.getByLabelText("平仓后冷却（秒，0=关）") as HTMLInputElement).value).toBe("90");
    expect((screen.getByLabelText("回吐阶梯") as HTMLSelectElement).value).toBe("tight");
    const confirm = screen.getByRole("button", { name: /确认并开始 AI 自动交易/ });
    await waitFor(() => expect(confirm).not.toBeDisabled());
    fireEvent.click(confirm);
    const opts = onConfirm.mock.calls[0][0] as AiTradeLaunchOpts;
    expect(opts.strategyIds).toEqual([1, 2]); // 未被冲回默认 [1]
    expect(opts.bar).toBe("4H");
    expect(opts.positionStepPct).toBe(0.5);
    expect(opts.wakeIntervalMinutes).toBe(7);
    expect(opts.capitalUsd).toBe(500);
    expect(opts.profitGuard?.givebackPct).toBeCloseTo(0.55, 10);
  });

  // ---------- 利润保护区块（ai-trade-profit-protection §3.3 / §六）----------
  it("默认勾选利润保护，提交体带完整参数（UI 百分数 → wire fraction）", async () => {
    const { onConfirm } = renderModal({ defaultStrategyIds: [1] });
    await screen.findByText("均线策略");
    expect(screen.getByRole("checkbox", { name: "利润保护（推荐开启）" })).toBeChecked();
    const confirm = screen.getByRole("button", { name: /确认并开始 AI 自动交易/ });
    await waitFor(() => expect(confirm).not.toBeDisabled());
    fireEvent.click(confirm);
    const opts = onConfirm.mock.calls[0][0] as AiTradeLaunchOpts;
    // 本轮 F1/F2 的四个自适应字段是**纯加法**，故这条整体 toEqual 须同步扩展（清单外的必要更新，
    // 已回填 spec §十一）：断言仍是「提交体逐键等于默认参数」，语义一字未变。
    expect(opts.profitGuard).toEqual({
      enabled: true,
      armReturnPct: 0.05,
      givebackPct: 0.4,
      breakevenArmReturnPct: 0.03,
      minHoldSeconds: 30,
      givebackTiers: AI_TRADE_PROFIT_GUARD_DEFAULTS.givebackTiers,
      atrFloorMult: AI_TRADE_PROFIT_GUARD_DEFAULTS.atrFloorMult,
      atrCapMult: AI_TRADE_PROFIT_GUARD_DEFAULTS.atrCapMult,
      cooldownSeconds: AI_TRADE_PROFIT_GUARD_DEFAULTS.cooldownSeconds,
    });
  });

  it("取消勾选后提交体为显式 enabled:false（与「字段缺省=默认开启」严格区分）", async () => {
    const { onConfirm } = renderModal({ defaultStrategyIds: [1] });
    await screen.findByText("均线策略");
    fireEvent.click(screen.getByRole("checkbox", { name: "利润保护（推荐开启）" }));
    const confirm = screen.getByRole("button", { name: /确认并开始 AI 自动交易/ });
    await waitFor(() => expect(confirm).not.toBeDisabled());
    fireEvent.click(confirm);
    const opts = onConfirm.mock.calls[0][0] as AiTradeLaunchOpts;
    expect(opts.profitGuard).toEqual({ enabled: false });
  });

  it("越界回吐比例即时报错并禁用确认（前端即时反馈，后端另有 422 兜边界）", async () => {
    renderModal({ defaultStrategyIds: [1] });
    await screen.findByText("均线策略");
    fireEvent.change(screen.getByLabelText("允许回吐（%）"), { target: { value: "150" } });
    expect(screen.getByText("允许回吐比例需为 0~100%")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /确认并开始 AI 自动交易/ })).toBeDisabled();
  });

  it("回填上次所选利润保护参数（诊断根因 ③ 的记忆闭环）", async () => {
    renderModal({
      defaultStrategyIds: [1],
      defaultProfitGuard: { enabled: true, armReturnPct: 0.08, givebackPct: 0.3,
        breakevenArmReturnPct: 0.02, minHoldSeconds: 45 },
    });
    await screen.findByText("均线策略");
    expect((screen.getByLabelText("武装阈值（保证金收益 %）") as HTMLInputElement).value).toBe("8");
    expect((screen.getByLabelText("允许回吐（%）") as HTMLInputElement).value).toBe("30");
    expect((screen.getByLabelText("最短持有（秒）") as HTMLInputElement).value).toBe("45");
  });

  it("单仓 TP/SL 回填上次值，且 placeholder 把「留空＝默认」写成具体数值（诊断根因 ⑤）", async () => {
    renderModal({ defaultStrategyIds: [1], defaultStopLossPct: 0.03, defaultTakeProfitPct: 0.06 });
    await screen.findByText("均线策略");
    expect((screen.getByLabelText("单仓止损%（如 0.05＝5%）") as HTMLInputElement).value).toBe("0.03");
    expect((screen.getByLabelText("单仓止盈%（如 0.10＝10%）") as HTMLInputElement).value).toBe("0.06");
  });

  it("杠杆留空时只显示价格口径 + 一句诚实提示；填写杠杆后才出保证金口径（[P1-9]）", async () => {
    renderModal({ defaultStrategyIds: [1] });
    await screen.findByText("均线策略");
    const sl = screen.getByLabelText("单仓止损%（如 0.05＝5%）") as HTMLInputElement;
    expect(sl.placeholder).toBe("留空＝默认 5%");
    expect(screen.getByText("保证金口径按下单杠杆换算，填写杠杆后显示")).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText("杠杆（×）"), { target: { value: "5" } });
    expect((screen.getByLabelText("单仓止损%（如 0.05＝5%）") as HTMLInputElement).placeholder).toBe(
      "留空＝默认 5%（≈保证金 -25%）",
    );
    expect(screen.queryByText("保证金口径按下单杠杆换算，填写杠杆后显示")).not.toBeInTheDocument();
  });

  // [adaptive §7.2a I-2] 本轮**有意反转**上一轮的「回测段整块隐藏利润保护」用例：F3 用 bar 内 OHLC
  // 极值 + 保守成交序取代了收盘价近似，故回测段照常露出并随体提交——但文案必须写明是**保守近似**，
  // 不得让用户以为回测 = 实盘（R-4：曲线变好看 ⇒ 高估实盘，是本特性最大的风险）。
  it("回测段渲染利润保护 UI 且随体提交，文案与实盘段不同（保守近似免责）", async () => {
    const { onConfirm } = renderModal({ defaultStrategyIds: [1] });
    await screen.findByText("均线策略");
    expect(screen.getByText(/判定只用/)).toBeInTheDocument();          // 实盘段文案

    fireEvent.click(screen.getByRole("button", { name: "回测" }));
    expect(screen.getByRole("checkbox", { name: "利润保护（推荐开启）" })).toBeInTheDocument();
    expect(screen.getByText(/bar 内极值保守近似/)).toBeInTheDocument();
    expect(screen.getByText(/相对实盘偏保守/)).toBeInTheDocument();
    expect(screen.queryByText(/判定只用/)).not.toBeInTheDocument();    // 回测不得照抄实盘文案

    const confirm = screen.getByRole("button", { name: /开始 AI 回测/ });
    await waitFor(() => expect(confirm).not.toBeDisabled());
    fireEvent.click(confirm);
    const opts = onConfirm.mock.calls[0][0] as AiTradeLaunchOpts;
    expect(opts.mode).toBe("backtest");
    expect(opts.profitGuard?.enabled).toBe(true);
    expect(opts.profitGuard?.givebackTiers?.length).toBeGreaterThan(0);
  });

  // ---------- 自适应四项（adaptive §3.3）----------
  it("四个新输入随体提交，且阶梯预设切换生效", async () => {
    const { onConfirm } = renderModal({ defaultStrategyIds: [1] });
    await screen.findByText("均线策略");

    fireEvent.change(screen.getByLabelText("ATR 下界倍数（0=关）"), { target: { value: "0.5" } });
    fireEvent.change(screen.getByLabelText("ATR 上界倍数（0=关）"), { target: { value: "2" } });
    fireEvent.change(screen.getByLabelText("平仓后冷却（秒，0=关）"), { target: { value: "240" } });
    fireEvent.change(screen.getByLabelText("回吐阶梯"), { target: { value: "off" } });

    const confirm = screen.getByRole("button", { name: /确认并开始 AI 自动交易/ });
    await waitFor(() => expect(confirm).not.toBeDisabled());
    fireEvent.click(confirm);
    const opts = onConfirm.mock.calls[0][0] as AiTradeLaunchOpts;
    expect(opts.profitGuard?.atrFloorMult).toBe(0.5);
    expect(opts.profitGuard?.atrCapMult).toBe(2);
    expect(opts.profitGuard?.cooldownSeconds).toBe(240);
    expect(opts.profitGuard?.givebackTiers).toEqual([]);      // 「关闭阶梯」预设
  });

  it("ATR 下界大于上界时两模式都拦住提交（P2-8：校验必须真的挡住按钮）", async () => {
    renderModal({ defaultStrategyIds: [1] });
    await screen.findByText("均线策略");
    fireEvent.change(screen.getByLabelText("ATR 下界倍数（0=关）"), { target: { value: "5" } });
    fireEvent.change(screen.getByLabelText("ATR 上界倍数（0=关）"), { target: { value: "1" } });

    expect(screen.getByRole("alert").textContent).toContain("ATR 下界倍数不可大于上界倍数");
    expect(screen.getByRole("button", { name: /确认并开始 AI 自动交易/ })).toBeDisabled();

    fireEvent.click(screen.getByRole("button", { name: "回测" }));
    expect(screen.getByRole("alert").textContent).toContain("ATR 下界倍数不可大于上界倍数");
    expect(screen.getByRole("button", { name: /开始 AI 回测/ })).toBeDisabled();
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

// ---------- 交易周期模式（ai-trade-holding-horizon-modes §6.2）----------
describe("AiTradeLaunchModal · 交易周期模式", () => {
  it("默认勾选并采用接口下发的 defaultProfile，提交体带 tradingHorizon", async () => {
    const { onConfirm } = renderModal({ defaultStrategyIds: [1] });
    await screen.findByRole("radio", { name: /日内波段/ });
    expect(screen.getByRole("checkbox", { name: "交易周期模式（推荐开启）" })).toBeChecked();
    const confirm = screen.getByRole("button", { name: /确认并开始 AI 自动交易/ });
    await waitFor(() => expect(confirm).not.toBeDisabled());
    fireEvent.click(confirm);
    const opts = onConfirm.mock.calls[0][0] as AiTradeLaunchOpts;
    expect(opts.tradingHorizon).toEqual({ enabled: true, profile: "intraday_swing" });
  });

  it("预设表读取失败 ⇒ 提交体**省略 profile 键**，由服务端按默认模式装配（不拿 custom 顶上）", async () => {
    vi.mocked(aiTradeHorizonApi.profiles).mockRejectedValue(new Error("boom"));
    const { onConfirm } = renderModal({ defaultStrategyIds: [1] });
    await screen.findByTestId("ai-trade-horizon-loading");
    const confirm = screen.getByRole("button", { name: /确认并开始 AI 自动交易/ });
    await waitFor(() => expect(confirm).not.toBeDisabled());
    fireEvent.click(confirm);
    const opts = onConfirm.mock.calls[0][0] as AiTradeLaunchOpts;
    // `custom` 全默认逐字等价于本层未装配——静默顶上等于把一个本应生效的周期契约悄悄关掉。
    expect(opts.tradingHorizon).toEqual({ enabled: true });
  });

  it("取消勾选 ⇒ 提交体为显式关闭（与「字段缺省=默认开启」严格区分）", async () => {
    const { onConfirm } = renderModal({ defaultStrategyIds: [1] });
    await screen.findByRole("radio", { name: /日内波段/ });
    fireEvent.click(screen.getByRole("checkbox", { name: "交易周期模式（推荐开启）" }));
    const confirm = screen.getByRole("button", { name: /确认并开始 AI 自动交易/ });
    await waitFor(() => expect(confirm).not.toBeDisabled());
    fireEvent.click(confirm);
    const opts = onConfirm.mock.calls[0][0] as AiTradeLaunchOpts;
    expect(opts.tradingHorizon).toEqual({ enabled: false });
  });

  it("切换 profile 联动时间级别与唤醒周期（这正是「模式」的意义）", async () => {
    const { onConfirm } = renderModal({ defaultStrategyIds: [1] });
    fireEvent.click(await screen.findByRole("radio", { name: /趋势持仓/ }));
    const confirm = screen.getByRole("button", { name: /确认并开始 AI 自动交易/ });
    await waitFor(() => expect(confirm).not.toBeDisabled());
    fireEvent.click(confirm);
    const opts = onConfirm.mock.calls[0][0] as AiTradeLaunchOpts;
    expect(opts.bar).toBe("4H");
    expect(opts.wakeIntervalMinutes).toBe(0);
    expect(opts.tradingHorizon).toEqual({ enabled: true, profile: "trend_hold" });
  });

  it("[P0-1] 切换 profile **不得**触碰利润保护层的最短持有——那会造出「G1 拦 AI + 保护层停摆」双关窗口", async () => {
    const { onConfirm } = renderModal({ defaultStrategyIds: [1] });
    await screen.findByRole("radio", { name: /日内波段/ });
    const minHold = screen.getByLabelText("最短持有（秒）") as HTMLInputElement;
    const before = minHold.value;
    fireEvent.click(screen.getByRole("radio", { name: /趋势持仓/ }));
    expect((screen.getByLabelText("最短持有（秒）") as HTMLInputElement).value).toBe(before);
    const confirm = screen.getByRole("button", { name: /确认并开始 AI 自动交易/ });
    await waitFor(() => expect(confirm).not.toBeDisabled());
    fireEvent.click(confirm);
    const opts = onConfirm.mock.calls[0][0] as AiTradeLaunchOpts;
    expect(opts.profitGuard?.minHoldSeconds).toBe(Number(before));
  });

  it("切到 custom 露出高级参数且**不联动**任何字段（[P1-8] bar === null）", async () => {
    const { onConfirm } = renderModal({ defaultStrategyIds: [1] });
    fireEvent.click(await screen.findByRole("radio", { name: /自定义/ }));
    fireEvent.change(screen.getByLabelText("最短主动平仓窗口（秒，0=不约束）"), {
      target: { value: "300" },
    });
    const confirm = screen.getByRole("button", { name: /确认并开始 AI 自动交易/ });
    await waitFor(() => expect(confirm).not.toBeDisabled());
    fireEvent.click(confirm);
    const opts = onConfirm.mock.calls[0][0] as AiTradeLaunchOpts;
    expect(opts.bar).toBe("1H");                 // 未被联动改写
    expect(opts.tradingHorizon).toEqual({
      enabled: true, profile: "custom", minHoldSeconds: 300,
    });
  });

  it("[G-7] 复发回归：改过的周期输入在 2 秒假时钟后**不被冲回默认**", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      const { rerender } = renderModal({ defaultStrategyIds: [1] });
      await screen.findByRole("radio", { name: /日内波段/ });
      fireEvent.click(screen.getByRole("radio", { name: /自定义/ }));
      const input = screen.getByLabelText("最短主动平仓窗口（秒，0=不约束）") as HTMLInputElement;
      fireEvent.change(input, { target: { value: "1234" } });
      rerender();                                 // 模拟父树重渲染（新引用的默认值）
      await vi.advanceTimersByTimeAsync(2000);
      expect(
        (screen.getByLabelText("最短主动平仓窗口（秒，0=不约束）") as HTMLInputElement).value,
      ).toBe("1234");
    } finally {
      vi.useRealTimers();
    }
  });

  it("契约摘要按当前表单值现算：改杠杆 ⇒ 解除阈随之变化（[P1-10]，无静态算例）", async () => {
    renderModal({ defaultStrategyIds: [1] });
    await screen.findByRole("radio", { name: /日内波段/ });
    const summary = () => screen.getByTestId("ai-trade-horizon-contract").textContent ?? "";
    // 杠杆留空 ⇒ 解除阈**诚实降级**为兜底口径（绝不猜一个杠杆去算出一个看起来很确定的数）。
    expect(summary()).toContain("兜底口径");
    fireEvent.change(screen.getByLabelText("杠杆（×）"), { target: { value: "3" } });
    await waitFor(() => expect(summary()).toContain("−7.50%"));   // 默认止损 5% × 3× × 0.5
    fireEvent.change(screen.getByLabelText("杠杆（×）"), { target: { value: "5" } });
    await waitFor(() => expect(summary()).toContain("−12.50%"));
  });
});
