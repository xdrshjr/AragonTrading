import "@testing-library/jest-dom/vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AI_TRADE_BASE_RULE_DEFAULTS, AI_TRADE_PROFIT_GUARD_DEFAULTS } from "@/lib/aiTrade";
import {
  aiTradeDisciplineApi,
  aiTradeHorizonApi,
  aiTradeTradabilityApi,
} from "@/lib/aiTradeHorizonApi";
import {
  AI_TRADE_LAUNCH_STEP_TITLE,
  type AiTradeLaunchStepId,
} from "@/lib/aiTradeLaunchSteps";
import { autoTradeSettingsApi } from "@/lib/autoTradeSettingsApi";
import { ApiRequestError } from "@/lib/api";
import { tradingApi } from "@/lib/tradingApi";
import { trajectoryApi } from "@/lib/trajectoryApi";
import type {
  AiTradeTrajectoryConfig,
  AiTradeTrajectoryDirVerdict,
} from "@/lib/trajectoryTypes";
import AiTradeLaunchModal, { type AiTradeLaunchOpts } from "../AiTradeLaunchModal";

// 后训练轨迹采集（trajectory-capture F-5 / F-9）的两个只读端点。默认夹具在文件末尾的顶层
// `beforeEach` 里装：`vi.clearAllMocks()` 不清实现，某条用例改过的返回值会漏进下一条。
vi.mock("@/lib/trajectoryApi", () => ({
  trajectoryApi: { config: vi.fn(), validate: vi.fn() },
}));

/**
 * 引导式启动向导的行为用例（ai-trade-guided-launch §8.2 W-1 ~ W-23）。
 *
 * 与 `AiTradeLaunchModal.test.tsx` 的分工：那 79 条守的是**交易语义与提交体**（本轮逐字段冻结，
 * 迁移时只加导航前缀、一律不改断言）；本文件守的是**分步化本身**新引入的失效方式——
 * 藏起来的警示、随卸载失效的探针、没有归属的校验、两棵带 `role` 的树。
 */

vi.mock("@/lib/tradingApi", () => ({ tradingApi: { config: vi.fn() } }));
vi.mock("@/lib/aiTradeHorizonApi", () => ({
  aiTradeHorizonApi: { profiles: vi.fn() },
  aiTradeDisciplineApi: { presets: vi.fn() },
  aiTradeTradabilityApi: { probe: vi.fn() },
}));
vi.mock("@/lib/autoTradeSettingsApi", () => ({ autoTradeSettingsApi: { get: vi.fn() } }));
vi.mock("@/lib/experienceApi", () => ({
  experienceApi: {
    overview: vi.fn().mockResolvedValue({
      lessons: { iron: 0, normal: 0, candidate: 0, muted: 0, archived: 0 },
      events: 0, sessions: 0, symbols: [], lastUpdatedTs: 0,
      excludedByCostBasis: 0, rejectedNarrative: 0, harvestGapTotal: 0,
      materializeHealth: { lastTs: 0, lastMs: 0, events: 0, slow: false, torn: false, overflow: false },
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

const TRADABILITY_OK = {
  instId: "BTC-USDT-SWAP", bar: "1H", preset: "balanced", level: "ok" as const,
  samples: 200, source: "live" as const, asOfTs: 1786600000,
  atrBps: { p10: 2.9, p50: 5.6, p90: 11.3 },
  costBps: { market: 13, limit: 8.5 },
  costConfidence: { market: "degraded", limit: "degraded" },
  degradedMultApplied: 1.2,
  requiredBps: { market: 23.4, limit: 15.3 },
  vetoRate: { market: 0.12, limit: 0.05 },
  stopBps: { p50: 11.2 },
  stopInsideCostRate: { market: 0.04, limit: 0.01 },
  alternatives: [{ bar: "15m", vetoRateMarket: 0.203, costBpsMarket: 13, level: "marginal" as const }],
  headline: "在 1H 周期上……约 12.0% 的开仓意图会被否决。",
  advice: "",
  disclaimer: "以上是确定性统计，不是收益预测。",
};

const TRADABILITY_INFEASIBLE = {
  ...TRADABILITY_OK,
  level: "infeasible" as const,
  vetoRate: { market: 0.986, limit: 0.926 },
  headline: "在 1H 周期上……约 98.6% 的开仓意图会被否决。",
  advice: "建议改用 15m 或更长的时间级别。",
};

const DISCIPLINE_PRESETS_FIXTURE = {
  enabled: true,
  defaultPreset: "balanced" as const,
  presets: [
    { id: "steady" as const, label: "稳健", rows: [] },
    {
      id: "balanced" as const, label: "均衡",
      rows: [{ key: "ai_trade_profit_guard_hard_tp_return_pct", label: "利润保护·硬止盈",
               value: "50%", off: false }],
    },
    { id: "aggressive" as const, label: "激进", rows: [] },
    { id: "off" as const, label: "关闭", rows: [] },
  ],
};

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
      id: "custom" as const, title: "自定义", description: "自行设定。", targetLabel: null,
      bar: null, wakeIntervalMinutes: null, minHoldSeconds: null, exitConfirmRounds: null,
      turnoverHysteresisK: null, targetHoldMinSeconds: null, targetHoldMaxSeconds: null,
      expectedHoldBars: null, confirmWindowSeconds: null, leverageMaxHint: null,
    },
  ],
};

function renderModal(props: Partial<React.ComponentProps<typeof AiTradeLaunchModal>> = {}) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const onConfirm = vi.fn();
  const onAcknowledge = vi.fn();
  const element = (over: Partial<React.ComponentProps<typeof AiTradeLaunchModal>> = {}) => (
    <QueryClientProvider client={qc}>
      <AiTradeLaunchModal
        open
        onClose={vi.fn()}
        onConfirm={onConfirm}
        submitting={false}
        acknowledged
        onAcknowledge={onAcknowledge}
        bar="1H"
        defaultBaseRule={AI_TRADE_BASE_RULE_DEFAULTS}
        defaultProfitGuard={AI_TRADE_PROFIT_GUARD_DEFAULTS}
        defaultMembers={[]}
        defaultAllocationMode="even"
        defaultInstId="BTC-USDT-SWAP"
        defaultStrategyIds={[1]}
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
    onAcknowledge,
    rerender: (over: Partial<React.ComponentProps<typeof AiTradeLaunchModal>> = {}) =>
      rerender(element(over)),
  };
}

async function gotoStep(id: AiTradeLaunchStepId) {
  fireEvent.click(
    await screen.findByRole("tab", { name: new RegExp(AI_TRADE_LAUNCH_STEP_TITLE[id]) }),
  );
  return screen.findByRole("heading", {
    level: 3,
    name: new RegExp(AI_TRADE_LAUNCH_STEP_TITLE[id]),
  });
}

function riskIds(): string[] {
  return Array.from(document.querySelectorAll("[data-risk-id]")).map(
    (el) => el.getAttribute("data-risk-id") ?? "",
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(tradingApi.config).mockResolvedValue({ availUsdt: 1000 } as never);
  vi.mocked(autoTradeSettingsApi.get).mockResolvedValue({
    maxBudgetPct: 100, effectiveMaxBudgetPct: 100,
  });
  vi.mocked(aiTradeHorizonApi.profiles).mockResolvedValue(HORIZON_PROFILES as never);
  vi.mocked(aiTradeDisciplineApi.presets).mockResolvedValue(DISCIPLINE_PRESETS_FIXTURE);
  vi.mocked(aiTradeTradabilityApi.probe).mockResolvedValue(TRADABILITY_OK);
});

afterEach(() => cleanup());

describe("轴 B · 导航契约", () => {
  it("W-1 打开即停在第 1 步，且第 3 步的字段**不在文档中**（真分步，不是 hidden 保留 DOM）", async () => {
    renderModal();
    await screen.findByText("均线策略");
    expect(screen.queryByLabelText("杠杆（×）")).toBeNull();
    expect(screen.getByRole("tab", { name: /标的与策略/ })).toHaveAttribute("aria-selected", "true");
  });

  it("W-2 步骤轨渲染 6 项、role=tablist、当前项 aria-selected=true", async () => {
    renderModal();
    await screen.findByText("均线策略");
    expect(screen.getByRole("tablist")).toBeInTheDocument();
    const tabs = screen.getAllByRole("tab");
    expect(tabs).toHaveLength(6);
    expect(tabs.filter((t) => t.getAttribute("aria-selected") === "true")).toHaveLength(1);
  });

  it("W-3 自由跳转：直接点第 5 步即可达（不要求逐步走过）", async () => {
    renderModal();
    await screen.findByText("均线策略");
    await gotoStep("guards");
    expect(screen.getByRole("checkbox", { name: "利润保护（推荐开启）" })).toBeInTheDocument();
  });

  it("W-4 本步错误挡住「下一步」，且同屏出现该错误原文（不是笼统的「请检查输入」）", async () => {
    renderModal({ defaultStrategyIds: [] });
    await screen.findByText("均线策略");
    const next = screen.getByRole("button", { name: "下一步" });
    expect(next).toBeDisabled();
    expect(screen.getAllByText("请至少选择一个交易策略").length).toBeGreaterThan(0);
  });

  it("W-5 跨步错误**不**挡本步：第 3 步填非法杠杆后，回第 1 步的「下一步」仍可点", async () => {
    renderModal();
    await screen.findByText("均线策略");
    await gotoStep("capital");
    fireEvent.change(screen.getByLabelText("杠杆（×）"), { target: { value: "-3" } });
    await gotoStep("scope");
    expect(screen.getByRole("button", { name: "下一步" })).not.toBeDisabled();
  });

  it("W-6 状态点：有阻断错误的步 data-state=error，有非阻断警示的步 data-state=warn", async () => {
    renderModal({ defaultStrategyIds: [] });
    await screen.findByText("均线策略");
    expect(screen.getByRole("tab", { name: /标的与策略/ })).toHaveAttribute("data-state", "error");
    // 档位 0.5 ⇒ 第 3 步出现分辨率警示 ⇒ 该步状态点为 warn（断言属性，不断言颜色）。
    await gotoStep("capital");
    fireEvent.change(screen.getByLabelText("仓位操作档位"), { target: { value: "0.5" } });
    await waitFor(() =>
      expect(screen.getByRole("tab", { name: /资金与仓位/ })).toHaveAttribute("data-state", "warn"),
    );
  });

  it("W-6b 未访问 / 已访问是两态：翻过第 3 步之后它的 data-state 由 default 变 edited", async () => {
    renderModal();
    await screen.findByText("均线策略");
    expect(screen.getByRole("tab", { name: /资金与仓位/ })).toHaveAttribute("data-state", "default");
    await gotoStep("capital");
    await gotoStep("scope");
    expect(screen.getByRole("tab", { name: /资金与仓位/ })).toHaveAttribute("data-state", "edited");
    // 「已访问」不是判据：它不参与放行，只让用户一眼看出自己还没翻过哪几格。
    expect(screen.getByRole("button", { name: "下一步" })).not.toBeDisabled();
  });

  it("W-7 摘要：成员改为 3 个后，轨上第 1 步摘要含「3 个标的」", async () => {
    renderModal({
      defaultMembers: [
        { instId: "BTC-USDT-SWAP", allocationPct: 30 },
        { instId: "ETH-USDT-SWAP", allocationPct: 30 },
        { instId: "SOL-USDT-SWAP", allocationPct: 30 },
      ],
    });
    await screen.findByText("均线策略");
    expect(screen.getByRole("tab", { name: /标的与策略/ })).toHaveAttribute(
      "title", expect.stringContaining("3 个标的"),
    );
  });

  it("W-8 往返不丢输入：第 3 步改杠杆 → 去第 1 步 → 回第 3 步，值仍在", async () => {
    renderModal();
    await screen.findByText("均线策略");
    await gotoStep("capital");
    fireEvent.change(screen.getByLabelText("杠杆（×）"), { target: { value: "7" } });
    await gotoStep("scope");
    await gotoStep("capital");
    expect((screen.getByLabelText("杠杆（×）") as HTMLInputElement).value).toBe("7");
  });

  it("W-9 重开弹窗回到第 1 步（stepId 确实进了那个 [open] effect）", async () => {
    const { rerender } = renderModal();
    await screen.findByText("均线策略");
    await gotoStep("guards");
    rerender({ open: false });
    rerender({ open: true });
    await waitFor(() =>
      expect(screen.getByRole("tab", { name: /标的与策略/ })).toHaveAttribute(
        "aria-selected", "true",
      ),
    );
  });

  it("W-10 复发守门：切步后推进 2 秒假时钟，第 2 步的自定义周期输入**不被冲回默认**", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      const { rerender } = renderModal();
      await gotoStep("rhythm");
      fireEvent.click(await screen.findByRole("radio", { name: /自定义/ }));
      const input = screen.getByLabelText("最短主动平仓窗口（秒，0=不约束）") as HTMLInputElement;
      fireEvent.change(input, { target: { value: "1234" } });
      await gotoStep("capital");
      await gotoStep("rhythm");
      rerender();
      await act(async () => {
        await vi.advanceTimersByTimeAsync(2000);
      });
      expect(
        (screen.getByLabelText("最短主动平仓窗口（秒，0=不约束）") as HTMLInputElement).value,
      ).toBe("1234");
    } finally {
      vi.useRealTimers();
    }
  });

  it("W-11 切到回测段：第 1 步出现区间字段、第 4 步不再渲染情报卡、第 6 步无真资金知情勾选", async () => {
    renderModal();
    await screen.findByText("均线策略");
    // 先在实盘段证明这张卡确实渲染得出来——否则下面那条 null 断言恒真、守不住任何东西。
    await gotoStep("cognition");
    expect(await screen.findByTestId("ai-trade-intel-fields")).toBeInTheDocument();
    await gotoStep("scope");
    fireEvent.click(screen.getByRole("button", { name: "回测" }));
    expect(screen.getByLabelText("回测起始")).toBeInTheDocument();
    await gotoStep("cognition");
    expect(screen.queryByTestId("ai-trade-intel-fields")).toBeNull();
    await gotoStep("review");
    expect(screen.queryByLabelText(/我已知晓这是真实资金/)).toBeNull();
  });

  it("W-12 第 6 步逐组摘要各带一个「修改」按钮，点击跳回对应步", async () => {
    renderModal();
    await screen.findByText("均线策略");
    await gotoStep("review");
    for (const id of ["scope", "rhythm", "capital", "cognition", "guards"] as const) {
      expect(screen.getByTestId(`ai-launch-summary-${id}`)).toBeInTheDocument();
    }
    fireEvent.click(screen.getByRole("button", { name: "修改资金与仓位" }));
    expect(await screen.findByLabelText("杠杆（×）")).toBeInTheDocument();
  });

  it("W-18 键盘：Alt+ArrowRight 前进一步，且焦点落在内容区标题上", async () => {
    renderModal();
    await screen.findByText("均线策略");
    fireEvent.keyDown(window, { key: "ArrowRight", altKey: true });
    const heading = await screen.findByRole("heading", { level: 3, name: /周期与节奏/ });
    await waitFor(() => expect(document.activeElement).toBe(heading));
  });

  it("W-19 aria-live 播报「第 N 步，共 6 步」；且刷新后的首启（未勾知情）仍有「跳到确认」", async () => {
    // 【v2 · P1-1】夹具刻意**不预置** acknowledged —— 预置了这条就恒绿且守不住任何东西：
    // `ready` 与它合取，用 `ready` 当判据时这个出口恰好对每一次刷新后的首次启动都不存在。
    renderModal({ acknowledged: false });
    await screen.findByText("均线策略");
    fireEvent.keyDown(window, { key: "ArrowRight", altKey: true });
    await waitFor(() =>
      expect(document.body.textContent).toContain("第 2 步，共 6 步：周期与节奏"),
    );
    const jump = screen.getByRole("button", { name: "跳到确认" });
    fireEvent.click(jump);
    expect(await screen.findByRole("heading", { level: 3, name: /确认并启动/ })).toBeInTheDocument();
  });

  it("W-19b 第 1–5 步有输入错误时「跳到确认」**不渲染**（点了没用的按钮更坏）", async () => {
    renderModal({ defaultStrategyIds: [], acknowledged: false });
    await screen.findByText("均线策略");
    expect(screen.queryByRole("button", { name: "跳到确认" })).toBeNull();
  });

  it("六步全部渲染一遍，没有一步把源码注释漏成正文", async () => {
    // JSX 的 children 位置里 `/** … */` **不是注释**，是一段会原样渲染给用户的文本，而 tsc 与
    // 其余渲染用例都不会红（没有任何断言说"这段字不该出现"）。本轮实测踩到过一次：五个
    // `Step*.tsx` 里那条「已就地说出口的风险不再重复」的说明一度是块注释，直接印在了面板上。
    renderModal();
    await screen.findByText("均线策略");
    for (const id of ["scope", "rhythm", "capital", "cognition", "guards", "review"] as const) {
      await gotoStep(id);
      const text = document.body.textContent ?? "";
      expect(text, `第 ${id} 步把块注释漏成了正文`).not.toContain("/*");
      expect(text, `第 ${id} 步把块注释漏成了正文`).not.toContain("*/");
      // Markdown 强调符同理：JSX 里的 `**粗体**` 会原样显示星号（本仓已为此复发四次）。
      expect(text, `第 ${id} 步出现了字面量 Markdown 强调符`).not.toContain("**");
    }
  });

  it("W-22 轨只有一棵：getAllByRole('tab') 恰好 6 个（守住「窄档另渲染一份进度条」）", async () => {
    renderModal();
    await screen.findByText("均线策略");
    expect(screen.getAllByRole("tab")).toHaveLength(6);
    expect(screen.getAllByRole("tablist")).toHaveLength(1);
    expect(screen.getAllByRole("progressbar")).toHaveLength(1);
  });
});

describe("轴 B · 不变式 I-1（风险零丢失）", () => {
  it("W-13 就地 + 清单：档位 0.5 ⇒ 第 3 步出现分辨率警示，第 6 步同一条以 data-risk-id 出现", async () => {
    renderModal();
    await screen.findByText("均线策略");
    await gotoStep("capital");
    fireEvent.change(screen.getByLabelText("仓位操作档位"), { target: { value: "0.5" } });
    expect(screen.getByTestId("gear-resolution-warning")).toBeInTheDocument();
    await gotoStep("review");
    expect(riskIds()).toContain("gearResolution");
  });

  it("W-14 从未访问第 5 步：探针夹具 infeasible ⇒ 清单含 tradabilityInfeasible 且 CTA 改文案", async () => {
    vi.mocked(aiTradeTradabilityApi.probe).mockResolvedValue(TRADABILITY_INFEASIBLE);
    renderModal();
    await screen.findByText("均线策略");
    await gotoStep("review");   // 全程不访问第 5 步
    await waitFor(() => expect(riskIds()).toContain("tradabilityInfeasible"));
    expect(screen.getByRole("button", { name: /仍要启动 AI 自动交易/ })).toBeInTheDocument();
  });

  it("W-20 探针不随步骤卸载而失效：在探针入参变化**之后**取值仍拦得住", async () => {
    // 与 W-14 互补：本条守的是「`tradabilityNeedsConfirm` 被写成了 state」这一种实现——
    // 那样它只会停在最后一次挂载时的样子，而第 5 步在这条路径上从未挂载过。
    vi.mocked(aiTradeTradabilityApi.probe).mockResolvedValue(TRADABILITY_INFEASIBLE);
    const { onConfirm } = renderModal();
    await screen.findByText("均线策略");
    await gotoStep("rhythm");
    fireEvent.change(screen.getByLabelText("时间级别"), { target: { value: "1m" } });
    await gotoStep("review");   // 仍然全程不访问第 5 步
    const cta = await screen.findByRole("button", { name: /仍要启动 AI 自动交易/ });
    fireEvent.click(cta);
    expect(onConfirm).not.toHaveBeenCalled();
    // 勾一次即放行（确认是一次显式动作，不是永久豁免）。
    fireEvent.click(screen.getByLabelText("仍要在该周期上启动"));
    fireEvent.click(screen.getByRole("button", { name: /仍要启动 AI 自动交易/ }));
    await waitFor(() => expect(onConfirm).toHaveBeenCalledTimes(1));
  });

  it("W-21 ready 假而横幅为空时不留死路：给出「第 N 步（…）有未解决的输入错误」+ 去处理", async () => {
    renderModal();
    await screen.findByText("均线策略");
    await gotoStep("guards");
    // 逆势置信度越界 ⇒ 只产生 `directionError`，它**不进** `firstError`。
    fireEvent.change(screen.getByLabelText("置信度下界"), { target: { value: "9" } });
    await gotoStep("review");
    expect(screen.getByRole("button", { name: /确认并开始 AI 自动交易/ })).toBeDisabled();
    const row = screen.getByTestId("ai-launch-blocking-step");
    expect(row).toHaveTextContent("第 5 步（纪律与护栏）有未解决的输入错误");
    fireEvent.click(screen.getByRole("button", { name: "去处理" }));
    expect(await screen.findByRole("heading", { level: 3, name: /纪律与护栏/ })).toBeInTheDocument();
  });

  it("W-23a experienceOff：取消经验勾选 ⇒ 第 4 步出现原提示；不访问第 4 步也能在第 6 步看到", async () => {
    renderModal();
    await screen.findByText("均线策略");
    await gotoStep("cognition");
    fireEvent.click(screen.getByRole("checkbox", { name: "启用经验模式" }));
    expect(screen.getByTestId("ai-trade-experience-off-note")).toBeInTheDocument();
    await gotoStep("review");
    expect(riskIds()).toContain("experienceOff");
  });

  it("W-23b wakeFloorRaised：divisor=24 且唤醒低于下限 ⇒ 第 6 步清单含该条", async () => {
    vi.mocked(aiTradeHorizonApi.profiles).mockResolvedValue({
      ...HORIZON_PROFILES, wakeHorizonDivisor: 24,
    } as never);
    renderModal({ defaultWakeIntervalMinutes: 1 });   // 14400 / 24 = 600s = 10 分钟 > 1 分钟
    await screen.findByText("均线策略");
    await gotoStep("review");
    await waitFor(() => expect(riskIds()).toContain("wakeFloorRaised"));
  });

  it("I-1 等值判据：第 6 步渲染出的 data-risk-id 集合 === collectRisks() 的 id 集合", async () => {
    vi.mocked(aiTradeTradabilityApi.probe).mockResolvedValue(TRADABILITY_INFEASIBLE);
    renderModal({ defaultMembers: [{ instId: "BTC-USDT-SWAP", allocationPct: 100 }] });
    await screen.findByText("均线策略");
    await gotoStep("capital");
    fireEvent.change(screen.getByLabelText("仓位操作档位"), { target: { value: "0.5" } });
    await gotoStep("guards");
    fireEvent.click(screen.getByRole("checkbox", { name: "利润保护（推荐开启）" }));
    await gotoStep("review");
    await waitFor(() => expect(riskIds().length).toBeGreaterThanOrEqual(4));
    const rendered = riskIds();
    expect(new Set(rendered).size).toBe(rendered.length);   // 每条只渲染一次
    for (const id of ["gearResolution", "tradabilityInfeasible", "profitGuardOff", "inertThresholds"]) {
      expect(rendered).toContain(id);
    }
  });
});

describe("轴 A · 互锁默认关（本轮需求本体）", () => {
  it("W-15 默认夹具（键缺席）+ 取消利润保护 ⇒ 第 6 步不要求任何额外勾选，一次点击即启动", async () => {
    const { onConfirm } = renderModal();
    await screen.findByText("均线策略");
    await gotoStep("guards");
    fireEvent.click(screen.getByRole("checkbox", { name: "利润保护（推荐开启）" }));
    await gotoStep("review");
    expect(screen.queryByTestId("ai-profit-guard-unguarded-confirm")).toBeNull();
    const cta = screen.getByRole("button", { name: /确认并开始 AI 自动交易/ });
    await waitFor(() => expect(cta).not.toBeDisabled());
    fireEvent.click(cta);
    await waitFor(() => expect(onConfirm).toHaveBeenCalledTimes(1));
    expect((onConfirm.mock.calls[0][0] as AiTradeLaunchOpts).profitGuard).toEqual({ enabled: false });
    // 不阻断 ≠ 不披露：那条 danger 必须在清单里。
    expect(riskIds()).toContain("profitGuardOff");
  });

  it("W-16 回退路径：夹具置 true ⇒ 恢复二次确认，未勾选点击不提交并同屏给出理由", async () => {
    vi.mocked(aiTradeHorizonApi.profiles).mockResolvedValue({
      ...HORIZON_PROFILES, requireProfitExitWithHorizon: true,
    } as never);
    const { onConfirm } = renderModal();
    await screen.findByText("均线策略");
    await gotoStep("guards");
    fireEvent.click(screen.getByRole("checkbox", { name: "利润保护（推荐开启）" }));
    await gotoStep("review");
    const box = await screen.findByTestId("ai-profit-guard-unguarded-confirm");
    const cta = screen.getByRole("button", { name: /确认并开始 AI 自动交易/ });
    fireEvent.click(cta);
    expect(onConfirm).not.toHaveBeenCalled();
    expect(screen.getByRole("alert").textContent ?? "").toContain("已关闭利润保护");
    // 【v2 · P1-7】必须说出「是谁在拦你」——否则用户看到的是一个与发行说明矛盾的确认框。
    expect(document.body.textContent).toContain("AI_TRADE_REQUIRE_PROFIT_EXIT_WITH_HORIZON");
    fireEvent.click(box);
    fireEvent.click(cta);
    await waitFor(() => expect(onConfirm).toHaveBeenCalledTimes(1));
  });

  it("W-17 老后端不发该键 ⇒ 按 false 处理，不阻断（安全的降级方向）", async () => {
    const { profiles } = HORIZON_PROFILES as Record<string, unknown> & { profiles: unknown };
    vi.mocked(aiTradeHorizonApi.profiles).mockResolvedValue({
      enabled: true, defaultProfile: "intraday_swing", lossReleaseFrac: 0.5,
      lossReleaseReturnPctFallback: 0.1, profiles,
    } as never);
    const { onConfirm } = renderModal();
    await screen.findByText("均线策略");
    await gotoStep("guards");
    fireEvent.click(screen.getByRole("checkbox", { name: "利润保护（推荐开启）" }));
    await gotoStep("review");
    expect(screen.queryByTestId("ai-profit-guard-unguarded-confirm")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: /确认并开始 AI 自动交易/ }));
    await waitFor(() => expect(onConfirm).toHaveBeenCalledTimes(1));
  });

  it("W-16b 互锁武装时，第 5 步的后果清单末句如实说出「服务端会拒绝启动」", async () => {
    vi.mocked(aiTradeHorizonApi.profiles).mockResolvedValue({
      ...HORIZON_PROFILES, requireProfitExitWithHorizon: true,
    } as never);
    renderModal();
    await screen.findByText("均线策略");
    await gotoStep("guards");
    fireEvent.click(screen.getByRole("checkbox", { name: "利润保护（推荐开启）" }));
    await waitFor(() =>
      expect(screen.getByTestId("ai-profit-guard-interlock-note")).toHaveTextContent(
        "服务端会拒绝启动",
      ),
    );
  });

  it("W-16c 互锁未武装时，同一句改口为「不会拦住你，但代价会列在风险清单里」", async () => {
    renderModal();
    await screen.findByText("均线策略");
    await gotoStep("guards");
    fireEvent.click(screen.getByRole("checkbox", { name: "利润保护（推荐开启）" }));
    const note = screen.getByTestId("ai-profit-guard-interlock-note");
    expect(note).toHaveTextContent("不会拦住你");
    expect(note.textContent ?? "").not.toContain("服务端会拒绝启动");
  });

  it("W-16d remedies 下方列出运维总闸的三个落点（这一行的所有者是前端，不是 remedies）", async () => {
    renderModal({
      error: "已选择「日内波段」持仓周期，但利润保护层被关闭",
      errorRemedies: [
        { id: "enable_profit_guard", label: "开启利润保护（推荐）" },
        { id: "disable_horizon", label: "关闭持仓周期约束" },
        { id: "ops_override", label: "运维总闸 AI_TRADE_REQUIRE_PROFIT_EXIT_WITH_HORIZON=false" },
      ],
    });
    await screen.findByText("均线策略");
    await gotoStep("review");
    const hint = screen.getByTestId("ai-start-remedy-ops-hint");
    expect(hint).toHaveTextContent("进程环境变量");
    expect(hint).toHaveTextContent("services/quant/.env");
    expect(hint).toHaveTextContent("agent.env");
    // 点「开启利润保护」⇒ 真的把第 5 步那个开关拨过去，**并当场说出拨的是哪一格**（§4.2.6）：
    // 按钮改的是一个用户此刻看不见的步骤里的开关，不给回执就是分步化带来的新暗箱。
    fireEvent.click(screen.getByTestId("ai-start-remedy-enable_profit_guard"));
    expect(screen.getByTestId("ai-start-remedy-receipt")).toHaveTextContent(
      "已为你开启利润保护（第 5 步 · 纪律与护栏）",
    );
    fireEvent.click(screen.getByRole("button", { name: "去看看" }));
    expect(await screen.findByRole("heading", { level: 3, name: /纪律与护栏/ })).toBeInTheDocument();
    expect(screen.getByRole("checkbox", { name: "利润保护（推荐开启）" })).toBeChecked();
  });
});

// ============ 后训练轨迹采集（ai-trade-trajectory-capture §6.2 F-5 / F-9）============

const TRAJECTORY_CONFIG: AiTradeTrajectoryConfig = {
  enabled: true,
  defaultRoot: "C:\\Users\\me\\AppData\\Roaming\\aragon-desktop\\datasets\\ai-trade",
  minFreeBytes: 1_073_741_824,
  segmentMaxBytes: 67_108_864,
  compressSegments: true,
  schemaVersion: 1,
  backtestSupported: false,
  thinkingCapturedProviders: ["anthropic"],
};

/** 夹具里唯一一个不可写的目录；其余目录（含默认根）一律可写。 */
const LOCKED_DIR = "D:\\locked";

function verdictFor(directory: string | null): AiTradeTrajectoryDirVerdict {
  const verdict: AiTradeTrajectoryDirVerdict = {
    ok: true,
    code: null,
    message: null,
    directory: directory ?? TRAJECTORY_CONFIG.defaultRoot,
    isDefault: directory == null,
    exists: true,
    freeBytes: 137_870_000_000,
    minFreeBytes: 1_073_741_824,
  };
  if (directory !== LOCKED_DIR) return verdict;
  return {
    ...verdict,
    ok: false,
    code: "TRAJECTORY_DIR_NOT_WRITABLE",
    message: "目录不可写：拒绝访问（13）",
  };
}

// 顶层钩子（按注册顺序排在文件开头那个 `vi.clearAllMocks()` 之后执行，对全部用例生效）。
beforeEach(() => {
  vi.mocked(trajectoryApi.config).mockResolvedValue(TRAJECTORY_CONFIG);
  vi.mocked(trajectoryApi.validate).mockImplementation(async (directory) =>
    verdictFor(directory),
  );
});

const trajectoryBox = () => screen.getByRole("checkbox", { name: /采集后训练数据/ });

async function reviewCta() {
  await gotoStep("review");
  return screen.findByRole("button", { name: /确认并开始 AI 自动交易/ });
}

describe("后训练轨迹采集 · 向导（trajectory-capture F-5 / F-9）", () => {
  it("AC-1 首次打开第 4 步采集卡默认不勾；提交体没有该键，偏好是原始的「关」", async () => {
    const { onConfirm } = renderModal();
    await screen.findByText("均线策略");
    await gotoStep("cognition");
    await waitFor(() => expect(trajectoryBox()).not.toBeDisabled());
    expect(trajectoryBox()).not.toBeChecked();
    const cta = await reviewCta();
    expect(screen.queryByTestId("ai-launch-trajectory-destination")).toBeNull();
    expect(screen.queryByTestId("ai-launch-trajectory-notice")).toBeNull();
    await waitFor(() => expect(cta).not.toBeDisabled());
    fireEvent.click(cta);
    await waitFor(() => expect(onConfirm).toHaveBeenCalledTimes(1));
    const opts = onConfirm.mock.calls[0][0] as AiTradeLaunchOpts;
    expect(opts.trajectoryCapture).toBeUndefined();
    expect(opts.trajectoryPreference).toEqual({ enabled: false, directory: null });
  });

  it("W-T1 勾选采集 + 校验回执 ok=false ⇒ 第 6 步启动按钮禁用，兜底行指向第 4 步", async () => {
    renderModal();
    await screen.findByText("均线策略");
    await gotoStep("cognition");
    await waitFor(() => expect(trajectoryBox()).not.toBeDisabled());   // 配置落地之前框是禁用的
    fireEvent.click(trajectoryBox());
    const input = await screen.findByLabelText("后训练数据保存目录");
    fireEvent.change(input, { target: { value: LOCKED_DIR } });
    fireEvent.blur(input);
    expect(await screen.findByText("⚠ 目录不可写：拒绝访问（13）")).toBeInTheDocument();
    // 本步自有错误挡住「下一步」（N-2），「跳到确认」也不再渲染（点了没用的按钮更坏）。
    expect(screen.getByRole("button", { name: "下一步" })).toBeDisabled();
    expect(screen.queryByRole("button", { name: "跳到确认" })).toBeNull();
    const cta = await reviewCta();
    expect(cta).toBeDisabled();
    expect(screen.getByTestId("ai-launch-blocking-step")).toHaveTextContent(
      "第 4 步（Agent 认知）有未解决的输入错误",
    );
    // 兜底行正说这个目录用不了，同屏就不许再说「将写入」它。
    expect(screen.queryByTestId("ai-launch-trajectory-destination")).toBeNull();
  });

  it("W-T2 兜底行「去处理」回到第 4 步，点「改用默认目录」后启动按钮恢复可点", async () => {
    renderModal({ defaultTrajectoryCapture: { enabled: true, directory: LOCKED_DIR } });
    await screen.findByText("均线策略");
    await gotoStep("review");
    const row = await screen.findByTestId("ai-launch-blocking-step");
    expect(screen.getByRole("button", { name: /确认并开始 AI 自动交易/ })).toBeDisabled();
    fireEvent.click(row.querySelector("button") as HTMLButtonElement);
    expect(
      await screen.findByRole("heading", { level: 3, name: /Agent 认知/ }),
    ).toBeInTheDocument();
    fireEvent.click(await screen.findByRole("button", { name: "改用默认目录" }));
    const cta = await reviewCta();
    await waitFor(() => expect(cta).not.toBeDisabled());
    expect(screen.queryByTestId("ai-launch-blocking-step")).toBeNull();
    expect(screen.getByTestId("ai-launch-trajectory-destination")).toHaveTextContent(
      `后训练数据将写入：${TRAJECTORY_CONFIG.defaultRoot}（本机，不上传）`,
    );
  });

  it("W-T3 422 的两条 remedies 各自拨动采集状态并给出就地回执（且不渲染运维总闸落点）", async () => {
    renderModal({
      defaultTrajectoryCapture: { enabled: true, directory: "E:\\gone" },
      error: "后训练数据目录不可用：目录不可写：拒绝访问（13）",
      errorRemedies: [
        { id: "trajectory_use_default_dir", label: "改用默认目录" },
        { id: "trajectory_disable", label: "本次不采集数据" },
      ],
    });
    await screen.findByText("均线策略");
    await gotoStep("review");
    const summary = screen.getByTestId("ai-launch-summary-cognition");
    await waitFor(() => expect(summary).toHaveTextContent("采集开"));
    // RV-P2-11：目录 422 与运维总闸无关，不在它下面教用户去改 env。
    expect(screen.getByTestId("ai-start-remedies")).toBeInTheDocument();
    expect(screen.queryByTestId("ai-start-remedy-ops-hint")).toBeNull();

    fireEvent.click(screen.getByTestId("ai-start-remedy-trajectory_use_default_dir"));
    expect(screen.getByTestId("ai-start-remedy-receipt")).toHaveTextContent(
      "已改用默认目录（第 4 步 · Agent 认知）",
    );
    await waitFor(() => expect(trajectoryApi.validate).toHaveBeenCalledWith(null));
    expect(screen.getByTestId("ai-launch-trajectory-destination")).toHaveTextContent(
      TRAJECTORY_CONFIG.defaultRoot,
    );

    fireEvent.click(screen.getByTestId("ai-start-remedy-trajectory_disable"));
    expect(screen.getByTestId("ai-start-remedy-receipt")).toHaveTextContent(
      "已关闭本次数据采集（第 4 步 · Agent 认知）",
    );
    await waitFor(() => expect(summary).not.toHaveTextContent("采集开"));
    fireEvent.click(screen.getByRole("button", { name: "去看看" }));
    await screen.findByRole("heading", { level: 3, name: /Agent 认知/ });
    expect(trajectoryBox()).not.toBeChecked();
  });

  it("W-T3b「本次不采集数据」只作用于这一次：提交体不带采集、记住的偏好原样；在第 4 步重新勾选即撤销", async () => {
    const preference = { enabled: true, directory: LOCKED_DIR };
    const { onConfirm } = renderModal({
      defaultTrajectoryCapture: preference,
      error: "后训练数据目录不可用：目录不可写：拒绝访问（13）",
      errorRemedies: [{ id: "trajectory_disable", label: "本次不采集数据" }],
    });
    await screen.findByText("均线策略");
    const cta = await reviewCta();
    await waitFor(() => expect(cta).toBeDisabled());
    fireEvent.click(screen.getByTestId("ai-start-remedy-trajectory_disable"));
    await waitFor(() => expect(cta).not.toBeDisabled());
    fireEvent.click(cta);
    await waitFor(() => expect(onConfirm).toHaveBeenCalledTimes(1));
    const opts = onConfirm.mock.calls[0][0] as AiTradeLaunchOpts;
    expect(opts.trajectoryCapture).toBeUndefined();
    // 拨成「关」会被当成用户的选择记住：正在积累的数据集就从下一次启动起静默断档。
    expect(opts.trajectoryPreference).toEqual(preference);

    await gotoStep("cognition");
    expect(trajectoryBox()).not.toBeChecked();
    fireEvent.click(trajectoryBox());
    await waitFor(() => expect(trajectoryBox()).toBeChecked());
    expect(await screen.findByText("⚠ 目录不可写：拒绝访问（13）")).toBeInTheDocument();
  });

  it("W-T4 open 恒真 + 父组件以新引用重渲染 ⇒ 已勾选的采集与已提交的目录不被重置", async () => {
    // 「输入约 1 秒回退」修过三次；这是它在采集卡上的第四道护栏（R13）。
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      const { rerender } = renderModal();
      await screen.findByText("均线策略");
      await gotoStep("cognition");
      await waitFor(() => expect(trajectoryBox()).not.toBeDisabled());
      fireEvent.click(trajectoryBox());
      const input = (await screen.findByLabelText("后训练数据保存目录")) as HTMLInputElement;
      fireEvent.change(input, { target: { value: "D:\\datasets\\mine" } });
      fireEvent.blur(input);
      // 再敲一段**未提交**的草稿：它同样不许被父树重渲染冲掉。
      fireEvent.change(input, { target: { value: "D:\\datasets\\draft" } });
      for (let i = 0; i < 3; i += 1) {
        rerender({
          defaultStrategyIds: [1],
          defaultTrajectoryCapture: { enabled: false, directory: null },
        });
      }
      await act(async () => {
        await vi.advanceTimersByTimeAsync(2000);
      });
      expect(trajectoryBox()).toBeChecked();
      expect((screen.getByLabelText("后训练数据保存目录") as HTMLInputElement).value).toBe(
        "D:\\datasets\\draft",
      );
      // 切走再回来：未提交的草稿随卸载作废，已提交的目录还在。
      await gotoStep("guards");
      await gotoStep("cognition");
      const again = (await screen.findByLabelText("后训练数据保存目录")) as HTMLInputElement;
      expect(again.value).toBe("D:\\datasets\\mine");
      expect(trajectoryBox()).toBeChecked();
    } finally {
      vi.useRealTimers();
    }
  });

  it("F-9 偏好 enabled:true + 配置接口 404 ⇒ 框未勾且禁用、摘要无「采集开」、第 6 步有说明、提交体不带该键而偏好保留", async () => {
    vi.mocked(trajectoryApi.config).mockRejectedValue(new ApiRequestError("404", "Not Found", 404));
    const preference = { enabled: true, directory: "D:\\datasets" };
    const { onConfirm } = renderModal({ defaultTrajectoryCapture: preference });
    await screen.findByText("均线策略");
    await gotoStep("cognition");
    await waitFor(() =>
      expect(screen.getByTestId("ai-trade-trajectory-lock-reason")).toHaveTextContent(
        "无法读取采集配置",
      ),
    );
    expect(trajectoryBox()).not.toBeChecked();
    expect(trajectoryBox()).toBeDisabled();
    expect(screen.getByRole("tab", { name: /Agent 认知/ }).getAttribute("title") ?? "")
      .not.toContain("采集开");

    const cta = await reviewCta();
    expect(screen.getByTestId("ai-launch-trajectory-notice")).toHaveTextContent(
      "△ 无法读取采集配置（后端尚未就绪），本次不会采集",
    );
    expect(screen.queryByTestId("ai-launch-trajectory-destination")).toBeNull();
    expect(screen.getByTestId("ai-launch-summary-cognition")).not.toHaveTextContent("采集开");
    await waitFor(() => expect(cta).not.toBeDisabled());   // 说明不是阻断
    fireEvent.click(cta);
    await waitFor(() => expect(onConfirm).toHaveBeenCalledTimes(1));
    const opts = onConfirm.mock.calls[0][0] as AiTradeLaunchOpts;
    expect(opts.trajectoryCapture).toBeUndefined();
    expect(opts.trajectoryPreference).toEqual(preference);
    expect(trajectoryApi.validate).not.toHaveBeenCalled();
  });

  it("F-9 运维关（config.enabled=false）同形：框禁用、第 6 步说明是运维关、提交体不带该键", async () => {
    vi.mocked(trajectoryApi.config).mockResolvedValue({ ...TRAJECTORY_CONFIG, enabled: false });
    const preference = { enabled: true, directory: null };
    const { onConfirm } = renderModal({ defaultTrajectoryCapture: preference });
    await screen.findByText("均线策略");
    await gotoStep("cognition");
    expect(await screen.findByTestId("ai-trade-trajectory-ops-off")).toBeInTheDocument();
    expect(trajectoryBox()).toBeDisabled();
    expect(trajectoryBox()).not.toBeChecked();
    const cta = await reviewCta();
    expect(screen.getByTestId("ai-launch-trajectory-notice")).toHaveTextContent(
      "△ 后训练数据采集已被运维关闭，本次不会采集",
    );
    await waitFor(() => expect(cta).not.toBeDisabled());
    fireEvent.click(cta);
    await waitFor(() => expect(onConfirm).toHaveBeenCalledTimes(1));
    const opts = onConfirm.mock.calls[0][0] as AiTradeLaunchOpts;
    expect(opts.trajectoryCapture).toBeUndefined();
    expect(opts.trajectoryPreference).toEqual(preference);
  });

  it("F-9 配置恢复后重开弹窗 ⇒ 用户上次的选择自动恢复为勾选，提交体带上它", async () => {
    vi.mocked(trajectoryApi.config).mockRejectedValue(new ApiRequestError("404", "Not Found", 404));
    const preference = { enabled: true, directory: "D:\\datasets" };
    const { onConfirm, rerender } = renderModal({ defaultTrajectoryCapture: preference });
    await screen.findByText("均线策略");
    await gotoStep("cognition");
    await waitFor(() =>
      expect(screen.getByTestId("ai-trade-trajectory-lock-reason")).toHaveTextContent(
        "无法读取采集配置",
      ),
    );
    expect(trajectoryBox()).not.toBeChecked();

    vi.mocked(trajectoryApi.config).mockResolvedValue(TRAJECTORY_CONFIG);
    rerender({ open: false });
    rerender({ open: true });
    await screen.findByText("均线策略");
    await gotoStep("cognition");
    await waitFor(() => expect(trajectoryBox()).toBeChecked());
    const cta = await reviewCta();
    expect(screen.getByTestId("ai-launch-summary-cognition")).toHaveTextContent("采集开");
    expect(screen.getByTestId("ai-launch-trajectory-destination")).toHaveTextContent(
      "D:\\datasets",
    );
    await waitFor(() => expect(cta).not.toBeDisabled());
    fireEvent.click(cta);
    await waitFor(() => expect(onConfirm).toHaveBeenCalledTimes(1));
    expect((onConfirm.mock.calls[0][0] as AiTradeLaunchOpts).trajectoryCapture).toEqual(
      preference,
    );
  });
});
