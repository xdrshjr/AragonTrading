import "@testing-library/jest-dom/vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { useState } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AI_TRADE_BASE_RULE_DEFAULTS, AI_TRADE_PROFIT_GUARD_DEFAULTS } from "@/lib/aiTrade";
import { ApiRequestError } from "@/lib/api";
import type {
  AiTradeEvidenceBackfillOut,
  AiTradeEvidencePolicy,
  AiTradeEvidenceResponse,
} from "@/lib/aiTradeEvidence";
import { aiTradeEvidenceApi } from "@/lib/aiTradeEvidenceApi";
import {
  aiTradeDisciplineApi,
  aiTradeHorizonApi,
  aiTradeTradabilityApi,
} from "@/lib/aiTradeHorizonApi";
import { AI_TRADE_LAUNCH_STEP_TITLE, type AiTradeLaunchStepId } from "@/lib/aiTradeLaunchSteps";
import { autoTradeSettingsApi } from "@/lib/autoTradeSettingsApi";
import { tradingApi } from "@/lib/tradingApi";
import { trajectoryApi } from "@/lib/trajectoryApi";
import AiTradeLaunchModal, { type AiTradeLaunchOpts } from "../AiTradeLaunchModal";
import EvidenceGateCard from "./EvidenceGateCard";
import type { UseAiTradeLaunchState } from "./useAiTradeLaunchState";
import { useEvidenceGateFields } from "./useEvidenceGateFields";

/**
 * 向导「实证准入」卡片（evidence-gated-capital §3.5.3 / §7.5）。
 *
 * 两层：卡片 + hook 的受控用例（档位 → 提交体、回填进行中禁用 / 回执 / 截断 / 失败文案、预览失败
 * 只显示错误行），以及挂进整个向导的用例（默认严格进启动体、回测体不带该键、切档进风险清单与
 * 第 5 步摘要、**预览读取失败不阻断启动**）。
 */

vi.mock("@/lib/aiTradeEvidenceApi", () => ({
  aiTradeEvidenceApi: { evidence: vi.fn(), backfill: vi.fn() },
}));
vi.mock("@/lib/trajectoryApi", () => ({
  trajectoryApi: { config: vi.fn(), validate: vi.fn() },
}));
vi.mock("@/lib/tradingApi", () => ({ tradingApi: { config: vi.fn() } }));
vi.mock("@/lib/aiTradeHorizonApi", () => ({
  aiTradeHorizonApi: { profiles: vi.fn() },
  aiTradeDisciplineApi: { presets: vi.fn() },
  aiTradeTradabilityApi: { probe: vi.fn() },
}));
vi.mock("@/lib/autoTradeSettingsApi", () => ({ autoTradeSettingsApi: { get: vi.fn() } }));
vi.mock("@/lib/experienceApi", () => ({
  experienceApi: {
    overview: vi.fn().mockRejectedValue(new Error("offline")),
    settings: vi.fn().mockResolvedValue({ enabled: true }),
  },
}));
vi.mock("@/lib/api", async (orig) => {
  const actual = (await orig()) as Record<string, unknown>;
  return {
    ...actual,
    api: {
      ...(actual.api as Record<string, unknown>),
      strategies: vi.fn().mockResolvedValue([{ id: 1, name: "均线策略" }]),
    },
  };
});

afterEach(cleanup);

const PREVIEW: AiTradeEvidenceResponse = {
  bar: "30m", refPolicy: "ref-v1:x", asOf: 1790300000,
  rule: { policy: "strict", minSamples: 10, window: 30, z: 1, maxAgeDays: 30 },
  buckets: [
    { instId: "ETH-USDT-SWAP", side: "long", state: "unproven", basis: "bucket", n: 6,
      meanBps: 1.2, seBps: 2, lcbBps: -0.8, ucbBps: 3.2, winRate: 0.5, gating: "shadow",
      backfillN: 4, openN: 1, lastMaturedTs: 1790200000 },
  ],
  recentShadowRatePerDay: { "ETH-USDT-SWAP": { long: 2, short: 1 } },
};

const BACKFILL_OK: AiTradeEvidenceBackfillOut = {
  inserted: 42,
  skipped: { candlesMissing: 3, atrUnavailable: 0, duplicate: 0, liveCovered: 5 },
  truncated: false, remaining: 0, notes: ["opened_wall 取执行周期收盘时刻"],
};

function withClient(node: React.ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={qc}>{node}</QueryClientProvider>;
}

function Harness({ mode = "live" }: { mode?: "live" | "backtest" }) {
  const [evidencePolicy, setEvidencePolicy] = useState<AiTradeEvidencePolicy>("strict");
  const state = {
    mode, bar: "30m", evidencePolicy, setEvidencePolicy,
    members: [{ instId: "ETH-USDT-SWAP", allocationPct: 100 }],
  } as unknown as UseAiTradeLaunchState;
  const view = useEvidenceGateFields(state, true);
  return (
    <>
      <EvidenceGateCard view={view} disabled={false} />
      <pre data-testid="payload">{JSON.stringify(view.payload ?? null)}</pre>
    </>
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(aiTradeEvidenceApi.evidence).mockResolvedValue(PREVIEW);
  vi.mocked(aiTradeEvidenceApi.backfill).mockResolvedValue(BACKFILL_OK);
});

describe("EvidenceGateCard · 受控卡片", () => {
  it("默认选中严格，提交体 {enabled:true, policy:strict}，并按已选标的 × 周期查预览", async () => {
    render(withClient(<Harness />));
    expect(screen.getByTestId("ai-evidence-policy-strict")).toHaveAttribute("aria-checked", "true");
    expect(screen.getByTestId("ai-evidence-policy-strict")).toHaveTextContent("（推荐）");
    expect(JSON.parse(screen.getByTestId("payload").textContent ?? "")).toEqual({
      enabled: true, policy: "strict",
    });
    await screen.findByTestId("ai-evidence-preview");
    expect(aiTradeEvidenceApi.evidence).toHaveBeenCalledWith(["ETH-USDT-SWAP"], "30m", "strict");
    expect(screen.getByTestId("ai-evidence-side-long")).toHaveTextContent("6/10");
    expect(screen.queryByTestId("ai-evidence-side-short")).toBeNull();   // 无桶的方向不出段
    expect(screen.getByText(/其中 4 笔来自历史成交回填/)).toBeInTheDocument();
  });

  it("严格档且无已证实桶 ⇒ 预估文案；天数取最慢方向（多 4/2=2 天，空 10/1=10 天）", async () => {
    render(withClient(<Harness />));
    expect(await screen.findByTestId("ai-evidence-estimate-days")).toHaveTextContent(
      "每个方向攒满 10 笔约需 10 天",
    );
    expect(screen.getByTestId("ai-evidence-estimate")).toHaveTextContent(
      "启动后前几天大概率只记影子、不下真实委托。",
    );
  });

  it("速度拿不到 ⇒ 不显示天数那一句（不猜）", async () => {
    vi.mocked(aiTradeEvidenceApi.evidence).mockResolvedValue({
      ...PREVIEW, recentShadowRatePerDay: {},
    });
    render(withClient(<Harness />));
    await screen.findByTestId("ai-evidence-preview");
    expect(screen.getByTestId("ai-evidence-estimate")).toBeInTheDocument();
    expect(screen.queryByTestId("ai-evidence-estimate-days")).toBeNull();
  });

  it("切换档位改变提交体与说明，预估文案只属于严格档", async () => {
    render(withClient(<Harness />));
    fireEvent.click(screen.getByTestId("ai-evidence-policy-observe"));
    expect(JSON.parse(screen.getByTestId("payload").textContent ?? "")).toEqual({
      enabled: true, policy: "observe",
    });
    expect(screen.getByTestId("ai-evidence-hint")).toHaveTextContent("从不拦截，只记录证据");
    expect(screen.queryByTestId("ai-evidence-estimate")).toBeNull();
    fireEvent.click(screen.getByTestId("ai-evidence-policy-balanced"));
    expect(JSON.parse(screen.getByTestId("payload").textContent ?? "").policy).toBe("balanced");
    await waitFor(() =>
      expect(aiTradeEvidenceApi.evidence).toHaveBeenCalledWith(["ETH-USDT-SWAP"], "30m", "balanced"),
    );
  });

  it("回填：进行中按钮禁用并说明耗时；完成后显示计数与 liveCovered，并重新拉预览", async () => {
    let resolve: (value: AiTradeEvidenceBackfillOut) => void = () => {};
    vi.mocked(aiTradeEvidenceApi.backfill).mockReturnValue(
      new Promise((r) => { resolve = r; }),
    );
    render(withClient(<Harness />));
    await screen.findByTestId("ai-evidence-preview");
    const calls = vi.mocked(aiTradeEvidenceApi.evidence).mock.calls.length;
    fireEvent.click(screen.getByTestId("ai-evidence-backfill"));
    await waitFor(() => expect(screen.getByTestId("ai-evidence-backfill")).toBeDisabled());
    expect(screen.getByText(/可能要一两分钟/)).toBeInTheDocument();
    expect(aiTradeEvidenceApi.backfill).toHaveBeenCalledWith({
      instIds: ["ETH-USDT-SWAP"], bar: "30m",
    });
    resolve(BACKFILL_OK);
    const line = await screen.findByTestId("ai-evidence-backfill-result");
    expect(line).toHaveTextContent(
      "回填 42 笔（跳过 3 笔：K 线缺失 3）；另有 5 笔已由实时影子度量，不重复计入",
    );
    expect(line).toHaveAttribute("title", "opened_wall 取执行周期收盘时刻");
    expect(screen.getByTestId("ai-evidence-backfill")).not.toBeDisabled();
    await waitFor(() =>
      expect(vi.mocked(aiTradeEvidenceApi.evidence).mock.calls.length).toBeGreaterThan(calls),
    );
  });

  it("回填截断 ⇒ 「还有 N 笔没处理完」且按钮改为「继续回填」", async () => {
    vi.mocked(aiTradeEvidenceApi.backfill).mockResolvedValue({
      ...BACKFILL_OK, truncated: true, remaining: 30,
      skipped: { ...BACKFILL_OK.skipped, liveCovered: 0 },
    });
    render(withClient(<Harness />));
    fireEvent.click(screen.getByTestId("ai-evidence-backfill"));
    expect(await screen.findByTestId("ai-evidence-backfill-result")).toHaveTextContent(
      "已回填 42 笔，还有 30 笔没处理完，再点一次继续",
    );
    expect(screen.getByTestId("ai-evidence-backfill")).toHaveTextContent("继续回填");
  });

  it("回填失败 ⇒ 显示后端 message", async () => {
    vi.mocked(aiTradeEvidenceApi.backfill).mockRejectedValue(
      new ApiRequestError("AI_TRADE_EVIDENCE_BACKFILL_FETCH_FAILED", "历史 K 线拉取失败", 502),
    );
    render(withClient(<Harness />));
    fireEvent.click(screen.getByTestId("ai-evidence-backfill"));
    expect(await screen.findByTestId("ai-evidence-backfill-error")).toHaveTextContent(
      "回填失败：历史 K 线拉取失败",
    );
  });

  it("预览读取失败 ⇒ 卡片内一行错误，档位控件照常可用", async () => {
    vi.mocked(aiTradeEvidenceApi.evidence).mockRejectedValue(new Error("boom"));
    render(withClient(<Harness />));
    expect(await screen.findByTestId("ai-evidence-preview-error")).toHaveTextContent(
      "证据预览读取失败：boom（不影响启动）",
    );
    expect(screen.getByTestId("ai-evidence-policy-observe")).not.toBeDisabled();
  });

  it("回测模式：不装配本层（提交体为空、不发查询），只留一行说明", () => {
    render(withClient(<Harness mode="backtest" />));
    expect(screen.getByTestId("ai-evidence-card-backtest")).toBeInTheDocument();
    expect(screen.queryByTestId("ai-evidence-card")).toBeNull();
    expect(screen.getByTestId("payload").textContent).toBe("null");
    expect(aiTradeEvidenceApi.evidence).not.toHaveBeenCalled();
  });
});

// ---------- 挂进整个向导 ----------

const HORIZON_PROFILES = {
  enabled: true, defaultProfile: "custom" as const, estimatedCostBps: 13,
  lossReleaseFrac: 0.5, lossReleaseReturnPctFallback: 0.1,
  profiles: [{
    id: "custom" as const, title: "自定义", description: "自行设定。", targetLabel: null,
    bar: null, wakeIntervalMinutes: null, minHoldSeconds: null, exitConfirmRounds: null,
    turnoverHysteresisK: null, targetHoldMinSeconds: null, targetHoldMaxSeconds: null,
    expectedHoldBars: null, confirmWindowSeconds: null, leverageMaxHint: null,
  }],
};

function renderModal() {
  const onConfirm = vi.fn();
  render(withClient(
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
      defaultStrategyIds={[1]}
      defaultContextRounds={10}
      defaultPositionStepPct={0.25}
      defaultWakeIntervalMinutes={2}
    />,
  ));
  return { onConfirm };
}

async function gotoStep(id: AiTradeLaunchStepId) {
  fireEvent.click(
    await screen.findByRole("tab", { name: new RegExp(AI_TRADE_LAUNCH_STEP_TITLE[id]) }),
  );
}

/** 走到第 6 步、检查摘要后点启动。`summary` 断言在点击前做（第 5 步摘要只在第 6 步渲染）。 */
async function launch(
  onConfirm: ReturnType<typeof vi.fn>,
  checkSummary: (guards: HTMLElement) => void,
  cta: RegExp = /确认并开始 AI 自动交易/,
): Promise<AiTradeLaunchOpts> {
  await gotoStep("review");
  checkSummary(await screen.findByTestId("ai-launch-summary-guards"));
  const button = await screen.findByRole("button", { name: cta });
  await waitFor(() => expect(button).not.toBeDisabled());
  fireEvent.click(button);
  await waitFor(() => expect(onConfirm).toHaveBeenCalledTimes(1));
  return onConfirm.mock.calls[0][0] as AiTradeLaunchOpts;
}

describe("EvidenceGateCard · 向导接线", () => {
  beforeEach(() => {
    vi.mocked(tradingApi.config).mockResolvedValue({ availUsdt: 1000 } as never);
    vi.mocked(autoTradeSettingsApi.get).mockResolvedValue({
      maxBudgetPct: 100, effectiveMaxBudgetPct: 100,
    });
    vi.mocked(aiTradeHorizonApi.profiles).mockResolvedValue(HORIZON_PROFILES as never);
    vi.mocked(aiTradeDisciplineApi.presets).mockRejectedValue(new Error("offline"));
    vi.mocked(aiTradeTradabilityApi.probe).mockRejectedValue(new Error("offline"));
    vi.mocked(trajectoryApi.config).mockRejectedValue(
      new ApiRequestError("404", "Not Found", 404),
    );
  });

  it("默认严格进实盘启动体；第 5 步摘要说「实证准入：严格」；严格档不进风险清单", async () => {
    const { onConfirm } = renderModal();
    await screen.findByText("均线策略");
    await gotoStep("guards");
    expect(await screen.findByTestId("ai-evidence-card")).toBeInTheDocument();
    const opts = await launch(onConfirm, (guards) =>
      expect(guards).toHaveTextContent("实证准入：严格"),
    );
    expect(opts.evidenceGate).toEqual({ enabled: true, policy: "strict" });
    expect(document.querySelector('[data-risk-id^="evidence"]')).toBeNull();
  });

  it("切到「仅观察」⇒ 提交体跟随，第 6 步清单出现 evidenceObserveOnly", async () => {
    const { onConfirm } = renderModal();
    await screen.findByText("均线策略");
    await gotoStep("guards");
    fireEvent.click(await screen.findByTestId("ai-evidence-policy-observe"));
    const opts = await launch(onConfirm, (guards) =>
      expect(guards).toHaveTextContent("实证准入：仅观察"),
    );
    expect(document.querySelector('[data-risk-id="evidenceObserveOnly"]')).not.toBeNull();
    expect(opts.evidenceGate).toEqual({ enabled: true, policy: "observe" });
  });

  it("预览接口失败不阻断启动：卡片内显示错误行，启动照常", async () => {
    vi.mocked(aiTradeEvidenceApi.evidence).mockRejectedValue(new Error("boom"));
    const { onConfirm } = renderModal();
    await screen.findByText("均线策略");
    await gotoStep("guards");
    expect(await screen.findByTestId("ai-evidence-preview-error")).toBeInTheDocument();
    const opts = await launch(onConfirm, () => {});
    expect(opts.evidenceGate?.policy).toBe("strict");
  });

  it("回测启动体不带 evidenceGate（AI 回测不装配本层）", async () => {
    const { onConfirm } = renderModal();
    await screen.findByText("均线策略");
    fireEvent.click(screen.getByRole("button", { name: "回测" }));
    const opts = await launch(
      onConfirm,
      (guards) => expect(guards).not.toHaveTextContent("实证准入"),
      /开始 AI 回测/,
    );
    expect(opts.mode).toBe("backtest");
    expect("evidenceGate" in opts).toBe(false);
  });
});
