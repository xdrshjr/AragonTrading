import "@testing-library/jest-dom/vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AI_TRADE_BASE_RULE_DEFAULTS, AI_TRADE_PROFIT_GUARD_DEFAULTS } from "@/lib/aiTrade";
import {
  aiTradeDisciplineApi,
  aiTradeHorizonApi,
  aiTradeTradabilityApi,
} from "@/lib/aiTradeHorizonApi";
import { autoTradeSettingsApi } from "@/lib/autoTradeSettingsApi";
import { tradingApi } from "@/lib/tradingApi";
import {
  AI_TRADE_LAUNCH_STEP_TITLE,
  type AiTradeLaunchStepId,
} from "@/lib/aiTradeLaunchSteps";
import AiTradeLaunchModal, { type AiTradeLaunchOpts } from "./AiTradeLaunchModal";

/**
 * ai-trade-guided-launch 附录 A 的**两个迁移 helper**。
 *
 * 迁移规则（按**步骤**计，不是按用例计）：在一条用例里，**每一个步骤**的首个查询目标之前各插
 * 一次 `gotoStep("<该步骤>")`；点击 CTA 的语句替换为 `submitLaunch()`。**断言本身一律不改**
 * ——一次「顺手改断言」就会把一条守着真实事故的用例悄悄弱化。
 */
async function gotoStep(id: AiTradeLaunchStepId) {
  fireEvent.click(
    await screen.findByRole("tab", { name: new RegExp(AI_TRADE_LAUNCH_STEP_TITLE[id]) }),
  );
  return screen.findByRole("heading", {
    level: 3,
    name: new RegExp(AI_TRADE_LAUNCH_STEP_TITLE[id]),
  });
}

async function submitLaunch(name: RegExp = /确认并开始 AI 自动交易/) {
  await gotoStep("review");
  const cta = await screen.findByRole("button", { name });
  await waitFor(() => expect(cta).not.toBeDisabled());
  fireEvent.click(cta);
  return cta;
}

vi.mock("@/lib/tradingApi", () => ({ tradingApi: { config: vi.fn() } }));
// 交易周期模式预设表：与上面两个同级的模块级 mock——弹窗每次打开都会拉它，不 mock 会触发真实 fetch。
vi.mock("@/lib/aiTradeHorizonApi", () => ({
  aiTradeHorizonApi: { profiles: vi.fn() },
  // 纪律预设的只读阈值表与周期预设表同住一个模块，整模块 mock 时必须一并给出——漏掉它，
  // `queryFn` 会是 undefined，症状是一堆无关用例莫名其妙地红。
  aiTradeDisciplineApi: { presets: vi.fn() },
  // 可交易性预检同住一个模块（tradability-and-open-funnel 轴 A），同理必须一并 mock。
  aiTradeTradabilityApi: { probe: vi.fn() },
}));
// 模块级 mock（P2-1）：写在文件顶层与 tradingApi mock 同级，否则其余用例的 settings 查询会触发真实 fetch。
vi.mock("@/lib/autoTradeSettingsApi", () => ({ autoTradeSettingsApi: { get: vi.fn() } }));
// 经验模式卡片挂载即拉一次库存与阈值（experience-context-graph §7.2）。同一条理由：不 mock 就是
// 每一条弹窗用例都发两次真实 fetch——它们会被卡片自己的 catch 吞掉（那是**产品上正确**的降级），
// 于是症状不是报错而是**整个文件时快时慢、偶尔超时**，排查成本远大于这四行。
vi.mock("@/lib/experienceApi", () => ({
  experienceApi: {
    overview: vi.fn().mockResolvedValue({
      lessons: { iron: 0, normal: 0, candidate: 0, muted: 0, archived: 0 },
      events: 0, sessions: 0, symbols: [], lastUpdatedTs: 0,
      excludedByCostBasis: 0, rejectedNarrative: 0, harvestGapTotal: 0,
      materializeHealth: { lastTs: 0, lastMs: 0, events: 0,
                           slow: false, torn: false, overflow: false },
      costRegimeCuts: {},
    }),
    settings: vi.fn().mockResolvedValue({ enabled: true }),
  },
}));
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
  vi.mocked(aiTradeDisciplineApi.presets).mockResolvedValue(DISCIPLINE_PRESETS_FIXTURE);
  // **默认给 `ok` 而不是 `infeasible`**：预检有 350ms debounce，而 `infeasible` 会把
  // `tradabilityBlocked` 置真、并把启动按钮的名字换成「仍要启动」。于是「等按钮可点 → 点它」
  // 这个贯穿全文件的提交范式变成一场与 debounce 的赛跑——预检先落地就静默不提交，症状是
  // `onConfirm.mock.calls[0]` 为 undefined，**且每次红的是不同的用例**。
  // 需要 `infeasible` 的那几条自己装夹具（就在下面那个 describe 里），其余用例不该被一个与
  // 它们无关的门禁按住。
  vi.mocked(aiTradeTradabilityApi.probe).mockResolvedValue(TRADABILITY_OK_FIXTURE);
});

// 后端 `GET /ai-trade/tradability` 的最小夹具（判据与文案全部由服务端下发；本弹窗**只挂载子组件
// 并透传受控 state**，具体渲染断言在 `AiTradeTradabilityFields.test.tsx`）。
const TRADABILITY_FIXTURE = {
  instId: "BTC-USDT-SWAP",
  bar: "1H",
  preset: "balanced",
  level: "infeasible" as const,
  samples: 200,
  source: "live" as const,
  asOfTs: 1786600000,
  atrBps: { p10: 2.9, p50: 5.6, p90: 11.3 },
  costBps: { market: 13, limit: 8.5 },
  costConfidence: { market: "degraded", limit: "degraded" },
  degradedMultApplied: 1.2,
  requiredBps: { market: 23.4, limit: 15.3 },
  vetoRate: { market: 0.986, limit: 0.926 },
  stopBps: { p50: 11.2 },
  stopInsideCostRate: { market: 0.623, limit: 0.287 },
  alternatives: [{ bar: "15m", vetoRateMarket: 0.203, costBpsMarket: 13, level: "marginal" as const }],
  headline: "在 1H 周期上……约 98.6% 的开仓意图会被否决。",
  advice: "建议改用 15m 或更长的时间级别。",
  disclaimer: "以上是确定性统计，不是收益预测。",
};

// 同一份夹具的 `ok` 档：不触发二次确认，故与提交路径正交（见 `beforeEach` 的理由）。
const TRADABILITY_OK_FIXTURE = {
  ...TRADABILITY_FIXTURE,
  level: "ok" as const,
  vetoRate: { market: 0.12, limit: 0.05 },
  stopInsideCostRate: { market: 0.04, limit: 0.01 },
  headline: "在 1H 周期上……约 12.0% 的开仓意图会被否决。",
  advice: "",
};

// 后端 `GET /ai-trade/discipline-presets` 的最小夹具（中文标签与已格式化的值全由服务端下发，
// 前端不复制任何一个——这条纪律与周期预设表同源，见 R-3 / G-10）。
const DISCIPLINE_PRESETS_FIXTURE = {
  enabled: true,
  defaultPreset: "balanced" as const,
  presets: [
    { id: "steady" as const, label: "稳健",
      rows: [{ key: "ai_trade_edge_gate_k_min", label: "最小 edge 门禁", value: "2 ×成本",
               off: false }] },
    { id: "balanced" as const, label: "均衡",
      rows: [{ key: "ai_trade_edge_gate_k_min", label: "最小 edge 门禁", value: "1.5 ×成本",
               off: false },
             // ai-trade-loss-and-liveness-hardening A-3 / A-4：这一行正是生产里那 45 条 run 的
             // 症结——预设承诺了硬止盈，而它的唯一执行者（利润保护层）被另一个复选框关掉了。
             { key: "ai_trade_profit_guard_hard_tp_return_pct", label: "利润保护·硬止盈",
               value: "50%", off: false }] },
    { id: "aggressive" as const, label: "激进",
      rows: [{ key: "ai_trade_profit_guard_hard_tp_return_pct", label: "利润保护·硬止盈",
               value: "0%", off: true }] },
    { id: "off" as const, label: "关闭", rows: [] },
  ],
};

// 后端 `GET /ai-trade/horizon-profiles` 的最小夹具（数值与中文标签一律来自服务端，前端不复制，R-3）。
const HORIZON_PROFILES = {
  enabled: true,
  defaultProfile: "intraday_swing" as const,
  estimatedCostBps: 13,
  lossReleaseFrac: 0.5,
  lossReleaseReturnPctFallback: 0.1,
  backstopRepair: {
    defaultMode: "plan" as const,
    modes: [
      { id: "plan" as const, label: "仅提醒", detail: "系统只给出补挂方案，由你一键执行" },
      { id: "arm" as const, label: "自动补挂", detail: "核实到止损缺失时由系统自动补挂" },
    ],
    note: "补挂只补止损腿、只补差额张数、永不平仓、永不加仓。",
    maxPerEpoch: 2,
    maxPerSession: 8,
  },
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
    await gotoStep("review");
    const confirm = screen.getByRole("button", { name: /确认并开始 AI 自动交易/ });
    expect(confirm).toBeDisabled();
    await gotoStep("scope");
    expect(screen.getByText("请至少选择一个交易策略")).toBeInTheDocument();
  });

  it("confirms with the selected strategy ids and bar", async () => {
    const { onConfirm } = renderModal();
    fireEvent.click(await screen.findByText("均线策略"));
    await gotoStep("review");
    const confirm = screen.getByRole("button", { name: /确认并开始 AI 自动交易/ });
    await waitFor(() => expect(confirm).not.toBeDisabled());
    fireEvent.click(confirm);
    expect(onConfirm).toHaveBeenCalledTimes(1);
    const opts = onConfirm.mock.calls[0][0] as AiTradeLaunchOpts;
    expect(opts.strategyIds).toEqual([1]);
    await gotoStep("scope");
    expect(opts.instId).toBe("BTC-USDT-SWAP");
    expect(opts.bar).toBe("1H");
    expect(opts.contextRefreshRounds).toBe(10);
  });

  // ---------- 交易纪律预设（ai-trade-discipline-preset-and-integrity 轴 A）----------
  it("默认下发服务端默认预设，并在卡片上明示这是默认值", async () => {
    const { onConfirm } = renderModal({ defaultStrategyIds: [1] });
    await screen.findByText("均线策略");
    // 本轮唯一改变默认行为的地方，必须**明示**而不是静默生效。
    await gotoStep("guards");
    expect(screen.getByRole("button", { name: /均衡/ })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByText("服务端默认")).toBeInTheDocument();
    await gotoStep("review");
    const confirm = screen.getByRole("button", { name: /确认并开始 AI 自动交易/ });
    await waitFor(() => expect(confirm).not.toBeDisabled());
    fireEvent.click(confirm);
    const opts = onConfirm.mock.calls[0][0] as AiTradeLaunchOpts;
    expect(opts.discipline).toEqual({ preset: "balanced" });
  });

  it("选中另一个预设后提交体带该 id，且**不带任何阈值**（阈值真源在服务端冻结表）", async () => {
    const { onConfirm } = renderModal({ defaultStrategyIds: [1] });
    await screen.findByText("均线策略");
    await gotoStep("guards");
    fireEvent.click(screen.getByRole("button", { name: /稳健/ }));
    await gotoStep("review");
    const confirm = screen.getByRole("button", { name: /确认并开始 AI 自动交易/ });
    await waitFor(() => expect(confirm).not.toBeDisabled());
    fireEvent.click(confirm);
    const opts = onConfirm.mock.calls[0][0] as AiTradeLaunchOpts;
    expect(opts.discipline).toEqual({ preset: "steady" });
    expect(Object.keys(opts.discipline ?? {})).toEqual(["preset"]);
  });

  it("「关闭」是显式退出口，且其后果说明必须写清楚失去了什么", async () => {
    const { onConfirm } = renderModal({ defaultStrategyIds: [1] });
    await screen.findByText("均线策略");
    await gotoStep("guards");
    fireEvent.click(screen.getByRole("button", { name: /关闭/ }));
    expect(screen.getByText(/没有波动率定标、没有回撤刹车/)).toBeInTheDocument();
    await gotoStep("review");
    const confirm = screen.getByRole("button", { name: /确认并开始 AI 自动交易/ });
    await waitFor(() => expect(confirm).not.toBeDisabled());
    fireEvent.click(confirm);
    expect((onConfirm.mock.calls[0][0] as AiTradeLaunchOpts).discipline)
      .toEqual({ preset: "off" });
  });

  it("展开后的只读阈值表**只渲染后端下发的值**（前端不复制任何一个数）", async () => {
    renderModal({ defaultStrategyIds: [1] });
    await screen.findByText("均线策略");
    await gotoStep("guards");
    expect(screen.queryByTestId("ai-discipline-threshold-table")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "查看这一档的阈值" }));
    const table = screen.getByTestId("ai-discipline-threshold-table");
    expect(table).toHaveTextContent("最小 edge 门禁");
    expect(table).toHaveTextContent("1.5 ×成本");        // balanced 那一档，来自服务端夹具
    // 切到另一档 ⇒ 表随之换（若前端自存一份，这里会纹丝不动）。
    fireEvent.click(screen.getByRole("button", { name: /稳健/ }));
    expect(screen.getByTestId("ai-discipline-threshold-table")).toHaveTextContent("2 ×成本");
  });

  it("取值为 0 的阈值显示「关闭」而不是「0」（显式关闭 ≠ 一个很小的数）", async () => {
    renderModal({ defaultStrategyIds: [1] });
    await screen.findByText("均线策略");
    await gotoStep("guards");
    fireEvent.click(screen.getByRole("button", { name: /激进/ }));
    fireEvent.click(screen.getByRole("button", { name: "查看这一档的阈值" }));
    const table = screen.getByTestId("ai-discipline-threshold-table");
    expect(table).toHaveTextContent("利润保护·硬止盈");
    expect(table).toHaveTextContent("关闭");
  });

  it("运维总闸关闭时如实说明，而不是把整个分区藏起来", async () => {
    vi.mocked(aiTradeDisciplineApi.presets).mockResolvedValue({
      ...DISCIPLINE_PRESETS_FIXTURE, enabled: false,
    });
    renderModal({ defaultStrategyIds: [1] });
    await screen.findByText("均线策略");
    await gotoStep("guards");
    expect(await screen.findByText(/本机已由运维总闸整轴关闭/)).toBeInTheDocument();
  });

  it("阈值表取不到时优雅降级：四张卡与提交路径照常可用", async () => {
    vi.mocked(aiTradeDisciplineApi.presets).mockRejectedValue(new Error("boom"));
    const { onConfirm } = renderModal({ defaultStrategyIds: [1] });
    await screen.findByText("均线策略");
    await gotoStep("guards");
    expect(screen.queryByRole("button", { name: "查看这一档的阈值" })).toBeNull();
    await gotoStep("review");
    const confirm = screen.getByRole("button", { name: /确认并开始 AI 自动交易/ });
    await waitFor(() => expect(confirm).not.toBeDisabled());
    fireEvent.click(confirm);
    expect((onConfirm.mock.calls[0][0] as AiTradeLaunchOpts).discipline)
      .toEqual({ preset: "balanced" });
  });

  it("不得宣称阈值「已校准」（[P1-10]：不得宣称不存在的东西）", async () => {
    renderModal({ defaultStrategyIds: [1] });
    await screen.findByText("均线策略");
    await gotoStep("guards");
    expect(screen.getByText(/尚未经回测校准/)).toBeInTheDocument();
    expect(screen.queryByText(/已校准|经回测验证/)).toBeNull();
  });

  // ---------- 【基础策略】铁律区块（ai-trade-base-strategy-ma-band §6.2 用例 29）----------
  it("默认勾选基础策略，提交体带完整规则参数", async () => {
    const { onConfirm } = renderModal({ defaultStrategyIds: [1] });
    await screen.findByText("均线策略");
    await gotoStep("guards");
    const box = screen.getByRole("checkbox", { name: "基础策略（铁律 · 默认开启）" });
    expect(box).toBeChecked();
    await gotoStep("review");
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
    await gotoStep("guards");
    fireEvent.click(screen.getByRole("checkbox", { name: "基础策略（铁律 · 默认开启）" }));
    await gotoStep("review");
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
    await gotoStep("guards");
    const period = screen.getByLabelText("基础策略均线周期") as HTMLInputElement;
    expect(period.value).toBe("30");
    expect(screen.getByLabelText("基础策略方向性模式")).toBeChecked();

    fireEvent.change(period, { target: { value: "1" } });
    expect(screen.getByText("基础策略均线周期需为 2~200 的整数")).toBeInTheDocument();
    await gotoStep("review");
    expect(screen.getByRole("button", { name: /确认并开始 AI 自动交易/ })).toBeDisabled();

    // 回到产地那一步再改：分步之后第 5 步的 DOM 在第 6 步是真卸载的，`period` 已经不在文档里。
    await gotoStep("guards");
    fireEvent.change(screen.getByLabelText("基础策略均线周期"), { target: { value: "40" } });
    await gotoStep("review");
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
    await gotoStep("guards");
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
    await gotoStep("review");
    const confirm = screen.getByRole("button", { name: /确认并开始 AI 自动交易/ });
    await waitFor(() => expect(confirm).not.toBeDisabled());
    fireEvent.click(confirm);
    const opts = onConfirm.mock.calls[0][0] as AiTradeLaunchOpts;
    await gotoStep("scope");
    expect(opts.members).toEqual([
      { instId: "BTC-USDT-SWAP", allocationPct: 60 },
      { instId: "ETH-USDT-SWAP", allocationPct: 40 },
    ]);
  });

  it("falls back to the current chart instId when defaultMembers is empty", async () => {
    const { onConfirm } = renderModal({ defaultStrategyIds: [1], defaultMembers: [] });
    await screen.findByText("均线策略");
    await gotoStep("review");
    const confirm = screen.getByRole("button", { name: /确认并开始 AI 自动交易/ });
    await waitFor(() => expect(confirm).not.toBeDisabled());
    fireEvent.click(confirm);
    const opts = onConfirm.mock.calls[0][0] as AiTradeLaunchOpts;
    await gotoStep("scope");
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
    await gotoStep("review");
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
    await gotoStep("rhythm");
    fireEvent.change(screen.getByLabelText("时间级别"), { target: { value: "4H" } });
    await gotoStep("review");
    const confirm = screen.getByRole("button", { name: /确认并开始 AI 自动交易/ });
    await waitFor(() => expect(confirm).not.toBeDisabled());
    fireEvent.click(confirm);
    const opts = onConfirm.mock.calls[0][0] as AiTradeLaunchOpts;
    expect(opts.bar).toBe("4H");
  });

  it("blocks confirm on an out-of-range context refresh count", async () => {
    renderModal({ defaultContextRounds: 1, defaultStrategyIds: [1] });
    await screen.findByText("均线策略");
    await gotoStep("rhythm");
    expect(screen.getByText(/上下文刷新轮数需为/)).toBeInTheDocument();
    await gotoStep("review");
    expect(screen.getByRole("button", { name: /确认并开始 AI 自动交易/ })).toBeDisabled();
  });

  it("blocks confirm when a member is not auto-tradable (spot)", async () => {
    renderModal({
      defaultStrategyIds: [1],
      defaultMembers: [{ instId: "ADA-USDT", allocationPct: 100 }],
    });
    await screen.findByText("均线策略");
    expect(screen.getByText("不支持")).toBeInTheDocument();
    await gotoStep("review");
    expect(screen.getByRole("button", { name: /确认并开始 AI 自动交易/ })).toBeDisabled();
  });

  it("blocks confirm on an invalid budget input", async () => {
    renderModal({ defaultStrategyIds: [1] });
    await screen.findByText("均线策略");
    // 总预算与杠杆共用「留空＝默认」占位，取首个（总预算）填入非法负值。
    await gotoStep("capital");
    fireEvent.change(screen.getAllByPlaceholderText("留空＝默认")[0], { target: { value: "-5" } });
    await gotoStep("review");
    expect(screen.getByRole("button", { name: /确认并开始 AI 自动交易/ })).toBeDisabled();
  });

  it("confirms with per-position pct as null when blank (E4)", async () => {
    const { onConfirm } = renderModal({ defaultStrategyIds: [1] });
    await screen.findByText("均线策略");
    await gotoStep("review");
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
    await gotoStep("capital");
    fireEvent.change(screen.getByLabelText("单仓止损%（如 0.05＝5%）"), { target: { value: "-1" } });
    await gotoStep("review");
    expect(screen.getByRole("button", { name: /确认并开始 AI 自动交易/ })).toBeDisabled();
  });

  it("clamps an out-of-range per-position pct in the preview, without blocking (E4)", async () => {
    renderModal({ defaultStrategyIds: [1] });
    await screen.findByText("均线策略");
    await gotoStep("capital");
    fireEvent.change(screen.getByLabelText("单仓止损%（如 0.05＝5%）"), { target: { value: "0.9" } });
    // 双口径显性化后：填值夹到 max 0.2 → 20%；留空侧如实写出生效默认 10%（不再只写「默认」）。
    expect(screen.getByText(/SL 20% \/ TP 10%（默认）/)).toBeInTheDocument();
    await gotoStep("review");
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
    await gotoStep("capital");
    fireEvent.change(screen.getAllByPlaceholderText("留空＝默认")[0], { target: { value: "800" } });
    // 每标的预算预览住在第 1 步的分配器里（金额输入在第 3 步），故这里跨回一步再断言。
    await gotoStep("scope");
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
    await gotoStep("capital");
    fireEvent.change(screen.getAllByPlaceholderText("留空＝默认")[0], { target: { value: "800" } });
    expect(screen.getByText(/已按 5% 上限夹紧/)).toBeInTheDocument();
    await gotoStep("scope");
    await waitFor(() => expect(screen.getAllByText("25.00 USDT")).toHaveLength(2));
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
    await gotoStep("capital");
    expect(screen.getByText("默认总预算（可用余额 × 1%）")).toBeInTheDocument();
    await gotoStep("scope");
    await waitFor(() => expect(screen.getAllByText("5.00 USDT")).toHaveLength(2));
  });

  // 仓位操作档位（ai-trade-position-sizing-steps §六·7）：AI 每次开仓规模只能落在用户所选离散档位上。
  it("renders the position-step select seeded from defaultPositionStepPct", async () => {
    renderModal({ defaultStrategyIds: [1] });
    await screen.findByText("均线策略");
    await gotoStep("capital");
    const select = screen.getByLabelText("仓位操作档位") as HTMLSelectElement;
    expect(select.value).toBe("0.25"); // 默认（aiTradeConfig 缺省 → AI_TRADE_DEFAULT_POSITION_STEP）
    expect(screen.getByText(/可用档位：25% \/ 50% \/ 75% \/ 100%/)).toBeInTheDocument();
    expect(screen.getByText(/非分批加\/减仓，平仓为全平/)).toBeInTheDocument(); // [R-10] 提示文案
  });

  it("seeds the position step from the persisted preference prop", async () => {
    renderModal({ defaultStrategyIds: [1], defaultPositionStepPct: 0.5 });
    await screen.findByText("均线策略");
    await gotoStep("capital");
    expect((screen.getByLabelText("仓位操作档位") as HTMLSelectElement).value).toBe("0.5");
    expect(screen.getByText(/可用档位：50% \/ 100%/)).toBeInTheDocument();
  });

  it("flows the chosen position step into onConfirm (live path)", async () => {
    const { onConfirm } = renderModal({ defaultStrategyIds: [1] });
    await screen.findByText("均线策略");
    await gotoStep("capital");
    fireEvent.change(screen.getByLabelText("仓位操作档位"), { target: { value: "0.5" } });
    await gotoStep("review");
    const confirm = screen.getByRole("button", { name: /确认并开始 AI 自动交易/ });
    await waitFor(() => expect(confirm).not.toBeDisabled());
    fireEvent.click(confirm);
    expect((onConfirm.mock.calls[0][0] as AiTradeLaunchOpts).positionStepPct).toBe(0.5);
  });

  it("carries the position step into onConfirm on the backtest path too", async () => {
    const { onConfirm } = renderModal({ defaultStrategyIds: [1] });
    await screen.findByText("均线策略");
    fireEvent.click(screen.getByRole("button", { name: "回测" }));
    await gotoStep("capital");
    fireEvent.change(screen.getByLabelText("仓位操作档位"), { target: { value: "0.1" } });
    await gotoStep("review");
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
    await gotoStep("rhythm");
    const input = screen.getByLabelText("Agent 唤醒周期（分钟）") as HTMLInputElement;
    expect(input.value).toBe("2");
    expect(screen.getByText(/仅按 K 线与策略触发/)).toBeInTheDocument();
  });

  it("seeds the wake interval from the persisted preference prop", async () => {
    renderModal({ defaultStrategyIds: [1], defaultWakeIntervalMinutes: 5 });
    await screen.findByText("均线策略");
    await gotoStep("rhythm");
    expect((screen.getByLabelText("Agent 唤醒周期（分钟）") as HTMLInputElement).value).toBe("5");
  });

  it("does NOT render the wake-interval input in the backtest segment", async () => {
    renderModal({ defaultStrategyIds: [1] });
    await screen.findByText("均线策略");
    fireEvent.click(screen.getByRole("button", { name: "回测" }));
    await gotoStep("rhythm");
    expect(screen.queryByLabelText("Agent 唤醒周期（分钟）")).toBeNull();
  });

  it("flows the chosen wake interval into onConfirm (live path)", async () => {
    const { onConfirm } = renderModal({ defaultStrategyIds: [1] });
    await screen.findByText("均线策略");
    await gotoStep("rhythm");
    fireEvent.change(screen.getByLabelText("Agent 唤醒周期（分钟）"), { target: { value: "3" } });
    await gotoStep("review");
    const confirm = screen.getByRole("button", { name: /确认并开始 AI 自动交易/ });
    await waitFor(() => expect(confirm).not.toBeDisabled());
    fireEvent.click(confirm);
    expect((onConfirm.mock.calls[0][0] as AiTradeLaunchOpts).wakeIntervalMinutes).toBe(3);
  });

  it("treats a blank wake interval as disabled (sends 0) and stays submittable", async () => {
    const { onConfirm } = renderModal({ defaultStrategyIds: [1] });
    await screen.findByText("均线策略");
    await gotoStep("rhythm");
    fireEvent.change(screen.getByLabelText("Agent 唤醒周期（分钟）"), { target: { value: "" } });
    await gotoStep("review");
    const confirm = screen.getByRole("button", { name: /确认并开始 AI 自动交易/ });
    await waitFor(() => expect(confirm).not.toBeDisabled());
    fireEvent.click(confirm);
    expect((onConfirm.mock.calls[0][0] as AiTradeLaunchOpts).wakeIntervalMinutes).toBe(0);
  });

  it("blocks confirm on an out-of-range wake interval", async () => {
    renderModal({ defaultStrategyIds: [1] });
    await screen.findByText("均线策略");
    await gotoStep("rhythm");
    fireEvent.change(screen.getByLabelText("Agent 唤醒周期（分钟）"), { target: { value: "9999" } });
    expect(screen.getByText(/Agent 唤醒周期需为/)).toBeInTheDocument();
    await gotoStep("review");
    expect(screen.getByRole("button", { name: /确认并开始 AI 自动交易/ })).toBeDisabled();
  });

  it("resets the position step to default when the modal reopens (guards inputs-revert)", async () => {
    const { rerender } = renderModal({ defaultStrategyIds: [1] });
    await screen.findByText("均线策略");
    await gotoStep("capital");
    fireEvent.change(screen.getByLabelText("仓位操作档位"), { target: { value: "1" } });
    expect((screen.getByLabelText("仓位操作档位") as HTMLSelectElement).value).toBe("1");
    // 关闭后重开：seed effect 依赖 [open]，false→true 复位回默认档位。
    rerender({ defaultStrategyIds: [1], open: false });
    rerender({ defaultStrategyIds: [1], open: true });
    // 重开即回到第 1 步（向导位置刻意不持久化，进的是那个既有的 [open] effect）。
    await gotoStep("capital");
    expect((screen.getByLabelText("仓位操作档位") as HTMLSelectElement).value).toBe("0.25");
  });

  it("resets the wake interval to default when the modal reopens (RN-2 guards inputs-revert)", async () => {
    const { rerender } = renderModal({ defaultStrategyIds: [1] });
    await screen.findByText("均线策略");
    await gotoStep("rhythm");
    fireEvent.change(screen.getByLabelText("Agent 唤醒周期（分钟）"), { target: { value: "9" } });
    expect((screen.getByLabelText("Agent 唤醒周期（分钟）") as HTMLInputElement).value).toBe("9");
    // 关闭后重开：seed 只在 open false→true 那一次转换执行（单行 setWakeIntervalRaw 在既有 [open] effect 内）。
    rerender({ defaultStrategyIds: [1], open: false });
    rerender({ defaultStrategyIds: [1], open: true });
    await gotoStep("rhythm");
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
    await gotoStep("capital");
    fireEvent.change(screen.getByLabelText("仓位操作档位"), { target: { value: "0.5" } });
    await gotoStep("rhythm");
    fireEvent.change(screen.getByLabelText("Agent 唤醒周期（分钟）"), { target: { value: "7" } });
    fireEvent.change(screen.getByLabelText("时间级别"), { target: { value: "4H" } });
    await gotoStep("capital");
    fireEvent.change(screen.getAllByPlaceholderText("留空＝默认")[0], { target: { value: "500" } });
    await gotoStep("guards");
    fireEvent.change(screen.getByLabelText("允许回吐（%）"), { target: { value: "55" } });
    fireEvent.change(screen.getByLabelText("ATR 下界倍数（0=关）"), { target: { value: "0.4" } });
    fireEvent.change(screen.getByLabelText("平仓后冷却（秒，0=关）"), { target: { value: "90" } });
    fireEvent.change(screen.getByLabelText("回吐阶梯"), { target: { value: "tight" } });

    // 模拟行情流节流 flush 引发的父树重渲染：open 恒为真，但每帧传入全新的默认数组引用。
    await gotoStep("scope");
    rerender({ defaultStrategyIds: [1], defaultMembers: [{ instId: "ETH-USDT-SWAP", allocationPct: 100 }] });
    rerender({ defaultStrategyIds: [1], defaultMembers: [{ instId: "ETH-USDT-SWAP", allocationPct: 100 }] });

    // 未发生 open false→true 转换 → seed effect 不重跑，全部编辑保持不变（RN-2：新增唤醒周期字段同守护）。
    await gotoStep("capital");
    expect((screen.getByLabelText("仓位操作档位") as HTMLSelectElement).value).toBe("0.5");
    await gotoStep("rhythm");
    expect((screen.getByLabelText("Agent 唤醒周期（分钟）") as HTMLInputElement).value).toBe("7");
    expect((screen.getByLabelText("时间级别") as HTMLSelectElement).value).toBe("4H");
    // 本轮新增的利润保护输入同守（复发性缺陷的教训：每加一个新字段就要在此加一条断言）。
    await gotoStep("guards");
    expect((screen.getByLabelText("允许回吐（%）") as HTMLInputElement).value).toBe("55");
    expect((screen.getByLabelText("ATR 下界倍数（0=关）") as HTMLInputElement).value).toBe("0.4");
    expect((screen.getByLabelText("平仓后冷却（秒，0=关）") as HTMLInputElement).value).toBe("90");
    expect((screen.getByLabelText("回吐阶梯") as HTMLSelectElement).value).toBe("tight");
    await gotoStep("review");
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
  it("A-23 · 经验模式默认勾选，提交体带完整规则", async () => {
    const { onConfirm } = renderModal({ defaultStrategyIds: [1] });
    await screen.findByText("均线策略");
    await gotoStep("cognition");
    expect(screen.getByRole("checkbox", { name: "启用经验模式" })).toBeChecked();
    await gotoStep("review");
    const confirm = screen.getByRole("button", { name: /确认并开始 AI 自动交易/ });
    await waitFor(() => expect(confirm).not.toBeDisabled());
    fireEvent.click(confirm);
    const opts = onConfirm.mock.calls[0][0] as AiTradeLaunchOpts;
    expect(opts.experience).toEqual({ enabled: true, scope: "symbol", write: true });
  });

  it("A-23 · 取消勾选 ⇒ 提交 `{enabled:false}` 而不是省略整键", async () => {
    // 「字段整键缺省」= 老客户端没发这个字段 ⇒ 服务端按默认装配；
    // 「显式 false」= 用户亲手取消了勾选 ⇒ 该会话不装配。
    // 把两者合成一个判据，用户就再也关不掉这个功能，而界面上那个复选框看起来是生效的
    // ——本仓库已在 baseRules / profitGuard / tradingHorizon 上踩过三次。
    const { onConfirm } = renderModal({ defaultStrategyIds: [1] });
    await screen.findByText("均线策略");
    await gotoStep("cognition");
    fireEvent.click(screen.getByRole("checkbox", { name: "启用经验模式" }));
    await gotoStep("review");
    const confirm = screen.getByRole("button", { name: /确认并开始 AI 自动交易/ });
    await waitFor(() => expect(confirm).not.toBeDisabled());
    fireEvent.click(confirm);
    const opts = onConfirm.mock.calls[0][0] as AiTradeLaunchOpts;
    expect(opts.experience).toEqual({ enabled: false, scope: "symbol", write: true });
  });

  it("默认勾选利润保护，提交体带完整参数（UI 百分数 → wire fraction）", async () => {
    const { onConfirm } = renderModal({ defaultStrategyIds: [1] });
    await screen.findByText("均线策略");
    await gotoStep("guards");
    expect(screen.getByRole("checkbox", { name: "利润保护（推荐开启）" })).toBeChecked();
    await gotoStep("review");
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

  // 【W-15 · ai-trade-guided-launch 轴 A 主判据】运维互锁默认已关（wire 上
  // `requireProfitExitWithHorizon` 缺席 ⇒ 按 false 处理）⇒ 关掉利润保护**不再要求任何额外勾选**，
  // 一次点击即启动。代价（浮盈没有自动兑现通道）由第 6 步的风险清单如实披露，而不是把用户挡在
  // 门外。武装态下恢复二次确认的那条路径由 W-16 守。
  it("取消勾选后提交体为显式 enabled:false（与「字段缺省=默认开启」严格区分）", async () => {
    const { onConfirm } = renderModal({ defaultStrategyIds: [1] });
    await screen.findByText("均线策略");
    await gotoStep("guards");
    fireEvent.click(screen.getByRole("checkbox", { name: "利润保护（推荐开启）" }));
    await gotoStep("review");
    // 风险披露恒在：即便不阻断，最后一屏也必须说出这一条。
    expect(
      screen.getByTestId("ai-launch-risk-list").querySelector("[data-risk-id=\"profitGuardOff\"]"),
    ).toBeInTheDocument();
    expect(screen.queryByTestId("ai-profit-guard-unguarded-confirm")).toBeNull();
    const confirm = screen.getByRole("button", { name: /确认并开始 AI 自动交易/ });
    await waitFor(() => expect(confirm).not.toBeDisabled());
    fireEvent.click(confirm);
    const opts = onConfirm.mock.calls[0][0] as AiTradeLaunchOpts;
    expect(opts.profitGuard).toEqual({ enabled: false });
  });

  it("A-3：关闭利润保护 ⇒ 逐条列出将失效的机制，且数值全部来自受控值 / 后端下发", async () => {
    // 生产实证：193 / 211 条 run 的利润保护层未装配，其中 45 条同时选了承诺硬止盈的纪律预设
    // ——用户看得见那个数，它却逐字节不作数。关闭是用户的权利，**后果必须被说出来**。
    renderModal({ defaultStrategyIds: [1] });
    await screen.findByText("均线策略");
    await gotoStep("guards");
    expect(screen.queryByTestId("ai-profit-guard-unguarded-notice")).toBeNull();
    fireEvent.click(screen.getByRole("checkbox", { name: "利润保护（推荐开启）" }));
    const notice = screen.getByTestId("ai-profit-guard-unguarded-notice");
    expect(notice).toHaveAttribute("role", "alert");
    expect(notice).toHaveTextContent("回吐棘轮");
    expect(notice).toHaveTextContent("保本线");
    expect(notice).toHaveTextContent("峰值浮盈与利润捕获率读数");
    expect(notice).toHaveTextContent("同向冷却");
    // 硬止盈那一条：**整句取自后端下发的只读阈值表**（标签 + 已格式化的值），前端不复制数字。
    const hardTp = await screen.findByTestId("ai-profit-guard-unguarded-hardtp");
    expect(hardTp).toHaveTextContent("利润保护·硬止盈");
    expect(hardTp).toHaveTextContent("该项由利润保护层执行，关闭后不生效");
  });

  it("A-4：关闭利润保护 ⇒ 只读阈值表里那一行加删除线 + 「未装配，不生效」尾注", async () => {
    renderModal({ defaultStrategyIds: [1] });
    await screen.findByText("均线策略");
    await gotoStep("guards");
    fireEvent.click(screen.getByRole("checkbox", { name: "利润保护（推荐开启）" }));
    fireEvent.click(await screen.findByRole("button", { name: "查看这一档的阈值" }));
    const row = await screen.findByTestId(
      "ai-discipline-threshold-inert-ai_trade_profit_guard_hard_tp_return_pct");
    expect(row).toHaveTextContent("未装配，不生效");
  });

  it("A-1：422 的三条补救渲染成按钮，且点击真的把对应开关拨过去（文案由后端下发）", async () => {
    const { rerender } = renderModal({ defaultStrategyIds: [1] });
    await screen.findByText("均线策略");
    await gotoStep("rhythm");
    rerender({
      error: "已选择「日内波段」持仓周期，但利润保护层被关闭",
      errorRemedies: [
        { id: "enable_profit_guard", label: "开启利润保护（推荐）" },
        { id: "disable_horizon", label: "关闭持仓周期约束" },
        { id: "ops_override", label: "运维总闸 AI_TRADE_REQUIRE_PROFIT_EXIT_WITH_HORIZON=false" },
      ],
    });
    await gotoStep("review");
    const box = await screen.findByTestId("ai-start-remedies");
    expect(box).toHaveTextContent("运维总闸 AI_TRADE_REQUIRE_PROFIT_EXIT_WITH_HORIZON=false");
    // 运维那一条**不是按钮**（前端拨不动它，做成按钮就是一个点了没反应的控件）
    expect(screen.queryByTestId("ai-start-remedy-ops_override")).toBeNull();
    // 先取消勾选，再点「开启利润保护」⇒ 勾选态真的被拨回来
    await gotoStep("guards");
    fireEvent.click(screen.getByRole("checkbox", { name: "利润保护（推荐开启）" }));
    expect(screen.getByRole("checkbox", { name: "利润保护（推荐开启）" })).not.toBeChecked();
    await gotoStep("review");
    fireEvent.click(screen.getByTestId("ai-start-remedy-enable_profit_guard"));
    await gotoStep("guards");
    expect(screen.getByRole("checkbox", { name: "利润保护（推荐开启）" })).toBeChecked();
  });

  it("越界回吐比例即时报错并禁用确认（前端即时反馈，后端另有 422 兜边界）", async () => {
    renderModal({ defaultStrategyIds: [1] });
    await screen.findByText("均线策略");
    await gotoStep("guards");
    fireEvent.change(screen.getByLabelText("允许回吐（%）"), { target: { value: "150" } });
    expect(screen.getByText("允许回吐比例需为 0~100%")).toBeInTheDocument();
    await gotoStep("review");
    expect(screen.getByRole("button", { name: /确认并开始 AI 自动交易/ })).toBeDisabled();
  });

  it("回填上次所选利润保护参数（诊断根因 ③ 的记忆闭环）", async () => {
    renderModal({
      defaultStrategyIds: [1],
      defaultProfitGuard: { enabled: true, armReturnPct: 0.08, givebackPct: 0.3,
        breakevenArmReturnPct: 0.02, minHoldSeconds: 45 },
    });
    await screen.findByText("均线策略");
    await gotoStep("guards");
    expect((screen.getByLabelText("武装阈值（保证金收益 %）") as HTMLInputElement).value).toBe("8");
    expect((screen.getByLabelText("允许回吐（%）") as HTMLInputElement).value).toBe("30");
    expect((screen.getByLabelText("最短持有（秒）") as HTMLInputElement).value).toBe("45");
  });

  it("单仓 TP/SL 回填上次值，且 placeholder 把「留空＝默认」写成具体数值（诊断根因 ⑤）", async () => {
    renderModal({ defaultStrategyIds: [1], defaultStopLossPct: 0.03, defaultTakeProfitPct: 0.06 });
    await screen.findByText("均线策略");
    await gotoStep("capital");
    expect((screen.getByLabelText("单仓止损%（如 0.05＝5%）") as HTMLInputElement).value).toBe("0.03");
    expect((screen.getByLabelText("单仓止盈%（如 0.10＝10%）") as HTMLInputElement).value).toBe("0.06");
  });

  it("杠杆留空时只显示价格口径 + 一句诚实提示；填写杠杆后才出保证金口径（[P1-9]）", async () => {
    renderModal({ defaultStrategyIds: [1] });
    await screen.findByText("均线策略");
    await gotoStep("capital");
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
    await gotoStep("guards");
    expect(screen.getByText(/判定只用/)).toBeInTheDocument();          // 实盘段文案

    fireEvent.click(screen.getByRole("button", { name: "回测" }));
    expect(screen.getByRole("checkbox", { name: "利润保护（推荐开启）" })).toBeInTheDocument();
    expect(screen.getByText(/bar 内极值保守近似/)).toBeInTheDocument();
    expect(screen.getByText(/相对实盘偏保守/)).toBeInTheDocument();
    expect(screen.queryByText(/判定只用/)).not.toBeInTheDocument();    // 回测不得照抄实盘文案

    await gotoStep("review");
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

    await gotoStep("guards");
    fireEvent.change(screen.getByLabelText("ATR 下界倍数（0=关）"), { target: { value: "0.5" } });
    fireEvent.change(screen.getByLabelText("ATR 上界倍数（0=关）"), { target: { value: "2" } });
    fireEvent.change(screen.getByLabelText("平仓后冷却（秒，0=关）"), { target: { value: "240" } });
    fireEvent.change(screen.getByLabelText("回吐阶梯"), { target: { value: "off" } });

    await gotoStep("review");
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
    await gotoStep("guards");
    fireEvent.change(screen.getByLabelText("ATR 下界倍数（0=关）"), { target: { value: "5" } });
    fireEvent.change(screen.getByLabelText("ATR 上界倍数（0=关）"), { target: { value: "1" } });

    await gotoStep("review");
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
    await gotoStep("review");
    expect(screen.queryByLabelText(/我已知晓这是真实资金/)).toBeNull();
    await gotoStep("scope");
    expect(screen.getByText("回测起始")).toBeInTheDocument();
    expect(screen.getByText("回测结束")).toBeInTheDocument();
    await gotoStep("rhythm");
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
    await gotoStep("review");
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
    await gotoStep("rhythm");
    expect(screen.getByText(/计划决策轮数 501 超上限 400/)).toBeInTheDocument();
    await gotoStep("review");
    expect(screen.getByRole("button", { name: /开始 AI 回测/ })).toBeDisabled();
  });

  it("keeps the live segment gating intact when live is available", async () => {
    renderModal({ defaultStrategyIds: [1], acknowledged: false });
    await screen.findByText("均线策略");
    // 默认落实盘段，未勾选 acknowledged → CTA 禁用
    await gotoStep("review");
    expect(screen.getByRole("button", { name: /确认并开始 AI 自动交易/ })).toBeDisabled();
  });
});

// ---------- 交易周期模式（ai-trade-holding-horizon-modes §6.2）----------
describe("AiTradeLaunchModal · 交易周期模式", () => {
  it("默认勾选并采用接口下发的 defaultProfile，提交体带 tradingHorizon", async () => {
    const { onConfirm } = renderModal({ defaultStrategyIds: [1] });
    await gotoStep("rhythm");
    await screen.findByRole("radio", { name: /日内波段/ });
    expect(screen.getByRole("checkbox", { name: "交易周期模式（推荐开启）" })).toBeChecked();
    await gotoStep("review");
    const confirm = screen.getByRole("button", { name: /确认并开始 AI 自动交易/ });
    await waitFor(() => expect(confirm).not.toBeDisabled());
    fireEvent.click(confirm);
    const opts = onConfirm.mock.calls[0][0] as AiTradeLaunchOpts;
    expect(opts.tradingHorizon).toEqual({ enabled: true, profile: "intraday_swing" });
  });

  it("预设表读取失败 ⇒ 提交体**省略 profile 键**，由服务端按默认模式装配（不拿 custom 顶上）", async () => {
    vi.mocked(aiTradeHorizonApi.profiles).mockRejectedValue(new Error("boom"));
    const { onConfirm } = renderModal({ defaultStrategyIds: [1] });
    await gotoStep("rhythm");
    await screen.findByTestId("ai-trade-horizon-loading");
    await gotoStep("review");
    const confirm = screen.getByRole("button", { name: /确认并开始 AI 自动交易/ });
    await waitFor(() => expect(confirm).not.toBeDisabled());
    fireEvent.click(confirm);
    const opts = onConfirm.mock.calls[0][0] as AiTradeLaunchOpts;
    // `custom` 全默认逐字等价于本层未装配——静默顶上等于把一个本应生效的周期契约悄悄关掉。
    expect(opts.tradingHorizon).toEqual({ enabled: true });
  });

  it("取消勾选 ⇒ 提交体为显式关闭（与「字段缺省=默认开启」严格区分）", async () => {
    const { onConfirm } = renderModal({ defaultStrategyIds: [1] });
    await gotoStep("rhythm");
    await screen.findByRole("radio", { name: /日内波段/ });
    fireEvent.click(screen.getByRole("checkbox", { name: "交易周期模式（推荐开启）" }));
    await gotoStep("review");
    const confirm = screen.getByRole("button", { name: /确认并开始 AI 自动交易/ });
    await waitFor(() => expect(confirm).not.toBeDisabled());
    fireEvent.click(confirm);
    const opts = onConfirm.mock.calls[0][0] as AiTradeLaunchOpts;
    expect(opts.tradingHorizon).toEqual({ enabled: false });
  });

  it("切换 profile 联动时间级别与唤醒周期（这正是「模式」的意义）", async () => {
    const { onConfirm } = renderModal({ defaultStrategyIds: [1] });
    await gotoStep("rhythm");
    fireEvent.click(await screen.findByRole("radio", { name: /趋势持仓/ }));
    await gotoStep("review");
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
    await gotoStep("rhythm");
    await screen.findByRole("radio", { name: /日内波段/ });
    await gotoStep("guards");
    const minHold = screen.getByLabelText("最短持有（秒）") as HTMLInputElement;
    const before = minHold.value;
    await gotoStep("rhythm");
    fireEvent.click(screen.getByRole("radio", { name: /趋势持仓/ }));
    await gotoStep("guards");
    expect((screen.getByLabelText("最短持有（秒）") as HTMLInputElement).value).toBe(before);
    await gotoStep("review");
    const confirm = screen.getByRole("button", { name: /确认并开始 AI 自动交易/ });
    await waitFor(() => expect(confirm).not.toBeDisabled());
    fireEvent.click(confirm);
    const opts = onConfirm.mock.calls[0][0] as AiTradeLaunchOpts;
    expect(opts.profitGuard?.minHoldSeconds).toBe(Number(before));
  });

  it("切到 custom 露出高级参数且**不联动**任何字段（[P1-8] bar === null）", async () => {
    const { onConfirm } = renderModal({ defaultStrategyIds: [1] });
    await gotoStep("rhythm");
    fireEvent.click(await screen.findByRole("radio", { name: /自定义/ }));
    fireEvent.change(screen.getByLabelText("最短主动平仓窗口（秒，0=不约束）"), {
      target: { value: "300" },
    });
    await gotoStep("review");
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
      await gotoStep("rhythm");
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

  it("U-21 [P1-8] 复发守门：切到「自动补挂」后等一个渲染周期与 2 秒假时钟，值**不得回退**", async () => {
    // 该缺陷的形态是「新字段写在了另一个非稳定依赖的 effect 里，约 1 秒后被冲回默认」——
    // 每加一个新受控输入就复发一次。新 state 必须塞进那个依赖收敛为 [open] 的既有 effect。
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      const { rerender } = renderModal({ defaultStrategyIds: [1] });
      await gotoStep("rhythm");
      await screen.findByTestId("ai-trade-backstop-repair");
      fireEvent.click(screen.getByTestId("ai-trade-backstop-repair-arm"));
      expect(screen.getByTestId("ai-trade-backstop-repair-arm")).toHaveAttribute(
        "aria-checked", "true");
      rerender();                                 // 模拟父树重渲染（新引用的默认值）
      await vi.advanceTimersByTimeAsync(2000);
      expect(screen.getByTestId("ai-trade-backstop-repair-arm")).toHaveAttribute(
        "aria-checked", "true");
      expect(screen.getByTestId("ai-trade-backstop-repair-consent")).toBeInTheDocument();
    } finally {
      vi.useRealTimers();
    }
  });

  it("自愈模式进 `tradingHorizon` payload；没主动选过 ⇒ **不带该键**（交服务端按运维缺省回填）", async () => {
    const { onConfirm } = renderModal({ defaultStrategyIds: [1] });
    await gotoStep("rhythm");
    await screen.findByTestId("ai-trade-backstop-repair");
    await gotoStep("review");
    const confirm = screen.getByRole("button", { name: /确认并开始 AI 自动交易/ });
    await waitFor(() => expect(confirm).not.toBeDisabled());
    fireEvent.click(confirm);
    expect(
      (onConfirm.mock.calls[0][0] as AiTradeLaunchOpts).tradingHorizon,
    ).not.toHaveProperty("backstopRepair");
    await gotoStep("rhythm");
    fireEvent.click(screen.getByTestId("ai-trade-backstop-repair-arm"));
    await gotoStep("review");
    fireEvent.click(screen.getByRole("button", { name: /确认并开始 AI 自动交易/ }));
    expect((onConfirm.mock.calls[1][0] as AiTradeLaunchOpts).tradingHorizon).toMatchObject({
      backstopRepair: "arm",
    });
  });

  it("[P2-6] 重开弹窗回到「仅提醒」——`arm` 是一次性授权，刻意**不进偏好回填链**", async () => {
    const { rerender } = renderModal({ defaultStrategyIds: [1] });
    await gotoStep("rhythm");
    await screen.findByTestId("ai-trade-backstop-repair");
    fireEvent.click(screen.getByTestId("ai-trade-backstop-repair-arm"));
    rerender({ defaultStrategyIds: [1], open: false });
    rerender({ defaultStrategyIds: [1], open: true });
    await gotoStep("rhythm");
    await waitFor(() =>
      expect(screen.getByTestId("ai-trade-backstop-repair-plan")).toHaveAttribute(
        "aria-checked", "true"),
    );
  });

  // ---- 可交易性预检（tradability-and-open-funnel 轴 A）：本弹窗**只断言子组件被挂载且受控
  // state 正确透传**，具体的四档渲染 / 二次确认 / 确认态重置断言全在 AiTradeTradabilityFields.test.tsx。
  it("挂载可交易性预检子组件，并按当前 instId / bar / preset 透传", async () => {
    renderModal({ defaultStrategyIds: [1] });
    await gotoStep("guards");
    await screen.findByTestId("ai-trade-tradability");
    await gotoStep("scope");
    await waitFor(() =>
      // 第 4 个入参是单仓止损那一格的现值（realized-edge 轴 E-0）：留空 ⇒ null ⇒ 后端用默认值。
      expect(aiTradeTradabilityApi.probe).toHaveBeenCalledWith(
        "BTC-USDT-SWAP", "1H", "balanced", null),
    );
  });

  it("`infeasible` 也**不禁用**启动按钮，但文案改为「仍要启动」——预检只提示、不阻断", async () => {
    vi.mocked(aiTradeTradabilityApi.probe).mockResolvedValue(TRADABILITY_FIXTURE);
    renderModal({ defaultStrategyIds: [1] });
    await gotoStep("guards");
    await screen.findByTestId("ai-tradability-confirm");
    await gotoStep("review");
    const confirm = await screen.findByRole("button", { name: /仍要启动 AI 自动交易/ });
    expect(confirm).not.toBeDisabled();
  });

  // 二次确认必须**真的拦得住**：若勾选态只被写进 state 而没有任何读点，那一行就纯粹是装饰，
  // 一次点击照样能在一个几乎开不出单的周期上起一个真实资金会话（有写点、没读点）。
  it("`infeasible` 未勾选确认时点击启动**不提交**，并同屏说明该做什么", async () => {
    vi.mocked(aiTradeTradabilityApi.probe).mockResolvedValue(TRADABILITY_FIXTURE);
    const { onConfirm } = renderModal({ defaultStrategyIds: [1] });
    await gotoStep("guards");
    await screen.findByTestId("ai-tradability-confirm");
    await gotoStep("review");
    fireEvent.click(await screen.findByRole("button", { name: /仍要启动 AI 自动交易/ }));
    expect(onConfirm).not.toHaveBeenCalled();
    expect(screen.getByRole("alert").textContent ?? "").toContain("仍要启动");
  });

  it("勾选二次确认后即可启动（确认是一次显式动作，不是永久豁免）", async () => {
    vi.mocked(aiTradeTradabilityApi.probe).mockResolvedValue(TRADABILITY_FIXTURE);
    const { onConfirm } = renderModal({ defaultStrategyIds: [1] });
    await gotoStep("guards");
    const box = await screen.findByLabelText("仍要在该周期上启动");
    fireEvent.click(box);
    await waitFor(() => expect(box).toBeChecked());
    await gotoStep("review");
    fireEvent.click(screen.getByRole("button", { name: /仍要启动 AI 自动交易/ }));
    await waitFor(() => expect(onConfirm).toHaveBeenCalledTimes(1));
  });

  it("「一键换周期」改的是本弹窗的时间级别下拉（不自动改预设，不发额外请求）", async () => {
    vi.mocked(aiTradeTradabilityApi.probe).mockResolvedValue(TRADABILITY_FIXTURE);
    renderModal({ defaultStrategyIds: [1] });
    await gotoStep("guards");
    await screen.findByTestId("ai-tradability-pick-bar");
    fireEvent.click(screen.getByTestId("ai-tradability-pick-bar"));
    await gotoStep("rhythm");
    await waitFor(() =>
      expect((screen.getByLabelText("时间级别") as HTMLSelectElement).value).toBe("15m"),
    );
  });

  it("契约摘要按当前表单值现算：改杠杆 ⇒ 解除阈随之变化（[P1-10]，无静态算例）", async () => {
    renderModal({ defaultStrategyIds: [1] });
    await gotoStep("rhythm");
    await screen.findByRole("radio", { name: /日内波段/ });
    const summary = () => screen.getByTestId("ai-trade-horizon-contract").textContent ?? "";
    // 杠杆留空 ⇒ 解除阈**诚实降级**为兜底口径（绝不猜一个杠杆去算出一个看起来很确定的数）。
    expect(summary()).toContain("兜底口径");
    await gotoStep("capital");
    fireEvent.change(screen.getByLabelText("杠杆（×）"), { target: { value: "3" } });
    await gotoStep("rhythm");
    await waitFor(() => expect(summary()).toContain("−7.50%"));   // 默认止损 5% × 3× × 0.5
    await gotoStep("capital");
    fireEvent.change(screen.getByLabelText("杠杆（×）"), { target: { value: "5" } });
    await gotoStep("rhythm");
    await waitFor(() => expect(summary()).toContain("−12.50%"));
  });
});
