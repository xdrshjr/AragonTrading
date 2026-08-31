import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { AiTradeIntelSummary, IntelImpactReport, IntelItem } from "@/lib/intelTypes";
import IntelImpactTab from "./IntelImpactTab";

const INST = "BTC-USDT-SWAP";

let impact: IntelImpactReport;
const impactMock = vi.fn(async () => impact);

vi.mock("@/lib/intelApi", () => ({
  intelApi: { impact: (...a: unknown[]) => impactMock(...(a as [])) },
}));

function item(over: Partial<IntelItem> = {}): IntelItem {
  return {
    id: 1, instId: INST, tier: "mid", category: "macro", headline: "议息会议纪要偏鸽",
    summary: "s", stance: "bullish", impact: "high", confidence: 0.7, eventTs: 0,
    capturedTs: 0, updatedTs: 0, expiresTs: 0, status: "active", pinned: false,
    userEdited: false, undated: false, isScheduled: false, origin: "agent", sources: [],
    sourceCount: 0, domainCount: 1, dedupKey: "k", supersedesId: null, promoteCount: 0,
    demoteCount: 0, lastCuratedTs: 0, curationNote: "", createdTs: 0,
    citeCount: 12, citeEntryCount: 5, settledCount: 4, winCount: 3, netPnlUsd: 18.4,
    lastCitedTs: 0, verdict: "", verdictTs: 0, verdictNote: "", verifyCount: 0,
    quoted: false, quoteCount: 0, quoteState: "", scheduleResolvedTs: 0,
    ...over,
  };
}

function report(over: Partial<IntelImpactReport> = {}): IntelImpactReport {
  return {
    instId: INST,
    totals: { citeCount: 12, citeEntryCount: 5, settledCount: 4, winCount: 3, netPnlUsd: 18.4 },
    top: [item()],
    worst: [],
    neverCited: 7,
    ...over,
  };
}

function summary(over: Partial<AiTradeIntelSummary> = {}): AiTradeIntelSummary {
  return {
    instId: INST, counts: { ultra: 1, mid: 1, long: 0, archived: 0 }, unread: 0,
    lastSeenTs: 0, lastCollectTs: 1, lastCurateTs: null, nextDueTs: null, inFlight: false,
    degrade: null, providerConfigured: true, dailyUsed: 0, dailyCap: 96,
    // ai-trade-intel-arm-state 轴 A：三个新键**必填**（`tsc` 强制，不是可选）。
    intelEnabled: true, armed: true, armReason: "" as const,
    citations: { cited: 0, entryCited: 0, settledTrips: 0, winCount: 0, netPnlUsd: 0,
      neverCited: 0 },
    verify: { confirmed: 0, refuted: 0, unresolved: 0, pending: 0, lastVerifyTs: null },
    quoteHitRate: null,
    alerts24h: 0,
    lastAlertTs: null,
    efficacy: { enabled: false, kMin: 8, sampleN: 0, overall: { settled: 0, wins: 0, netPnlUsd: 0, confirmed: 0, refuted: 0, unresolved: 0, samples: 0, accuracy: null }, byCategory: [] },
    ...over,
  };
}

function tab(props: Partial<Parameters<typeof IntelImpactTab>[0]> = {}) {
  return (
    <IntelImpactTab
      instId={INST}
      summary={summary()}
      settingsCiteEnabled
      settingsAttributionEnabled
      onClearVerdict={vi.fn()}
      onVerifyNow={vi.fn()}
      {...props}
    />
  );
}

function renderTab(props: Partial<Parameters<typeof IntelImpactTab>[0]> = {}) {
  return render(tab(props));
}

beforeEach(() => {
  impact = report();
  impactMock.mockClear();
});
afterEach(cleanup);

describe("IntelImpactTab", () => {
  it("顶部常驻口径说明（唯一挡在「这条情报值 18 USDT」误读前面的东西）", async () => {
    renderTab();
    expect(await screen.findByText(/共现统计，不是因果归因/)).toBeInTheDocument();
  });

  it("三张读数卡说的是「关联净额」而不是「贡献」", async () => {
    renderTab();
    await waitFor(() => expect(impactMock).toHaveBeenCalled());
    expect(screen.getByText("本标的引用次数")).toBeInTheDocument();
    expect(screen.getByText("已结算往返")).toBeInTheDocument();
    expect(screen.getByText("关联净额")).toBeInTheDocument();
    expect(screen.queryByText("贡献净额")).toBeNull();
  });

  it("贡献榜按关联净额降序列出条目", async () => {
    renderTab();
    expect(await screen.findByText("贡献榜")).toBeInTheDocument();
    expect(screen.getByText("议息会议纪要偏鸽")).toBeInTheDocument();
  });

  it("拖累榜为空时整块不渲染（一个空标题只是噪音）", async () => {
    renderTab();
    await waitFor(() => expect(impactMock).toHaveBeenCalled());
    expect(screen.queryByText("拖累榜")).toBeNull();
  });

  it("「从未被引用」给的是一句诚实的解释而不是一个孤零零的数字", async () => {
    renderTab();
    expect(await screen.findByText(/从未被引用：7 条/)).toBeInTheDocument();
    expect(screen.getByText(/仍在情报库中并可能被注入/)).toBeInTheDocument();
  });

  it("四种空态各自文案不同（逐条断言不相等，防被复制成同一段）", async () => {
    const texts: string[] = [];

    const cases: Array<Partial<Parameters<typeof IntelImpactTab>[0]>> = [
      { settingsCiteEnabled: false },
      { settingsAttributionEnabled: false },
      // ai-trade-intel-arm-state 评审 P2-7：判据从 `providerConfigured` 改读 `armed` 之后，
      // 一个「配好了但没装上」的 summary 才落在 `not-armed` 上；仍传 `providerConfigured:false`
      // 会让它掉到 `no-citations`，与第四个 case 撞成同一段文案而把这条断言判红。
      { summary: summary({ armed: false }) },
      {},
    ];
    impact = report({ totals: { citeCount: 0, citeEntryCount: 0, settledCount: 0,
      winCount: 0, netPnlUsd: 0 }, top: [], worst: [], neverCited: 0 });

    for (const props of cases) {
      const view = renderTab(props);
      texts.push((await screen.findByTestId("intel-impact-empty")).textContent ?? "");
      view.unmount();
    }

    expect(new Set(texts).size).toBe(4);
    texts.forEach((t) => expect(t.length).toBeGreaterThan(10));
  });

  it("写动作之后按 `reloadToken` 重取（本页签不在 5 秒轮询里）", async () => {
    const view = render(tab({ reloadToken: 0 }));
    await waitFor(() => expect(impactMock).toHaveBeenCalledTimes(1));

    // 用户在榜单上点掉一个判定 ⇒ 弹窗把 token +1。不重取的话那张卡片纹丝不动，
    // 一次成功的写动作在用户眼里就是一次「点了没反应」。
    view.rerender(tab({ reloadToken: 1 }));
    await waitFor(() => expect(impactMock).toHaveBeenCalledTimes(2));
  });
});

describe("战绩与信誉块（ai-trade-intel-actionability 轴 B）", () => {
  it("`efficacy` 在位 ⇒ 渲染战绩块并如实写出先验是否已生效", async () => {
    impact = report({
      efficacy: {
        enabled: true, kMin: 8, sampleN: 12,
        overall: { settled: 4, wins: 3, netPnlUsd: 41.2, confirmed: 6, refuted: 2,
          unresolved: 0, samples: 12, accuracy: 0.75 },
        byCategory: [{ category: "risk", settled: 3, wins: 3, netPnlUsd: 51.2,
          confirmed: 5, refuted: 0, unresolved: 0, samples: 8, accuracy: 1, weight: 0.18 }],
      },
    });
    render(<IntelImpactTab instId={INST} summary={summary()} settingsCiteEnabled
      settingsAttributionEnabled onClearVerdict={() => undefined}
      onVerifyNow={() => undefined} />);
    expect(await screen.findByTestId("intel-efficacy-block")).toBeInTheDocument();
    expect(screen.getByTestId("intel-efficacy-ready")).toHaveTextContent("已生效");
  });

  it("老后端不带 `efficacy` ⇒ 整块不渲染（绝不显示一个凭空的 0%）", async () => {
    impact = report();
    render(<IntelImpactTab instId={INST} summary={summary()} settingsCiteEnabled
      settingsAttributionEnabled onClearVerdict={() => undefined}
      onVerifyNow={() => undefined} />);
    await screen.findByTestId("intel-impact-tab");
    expect(screen.queryByTestId("intel-efficacy-block")).toBeNull();
  });
});
