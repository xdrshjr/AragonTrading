import "@testing-library/jest-dom/vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import CalibrationCard from "./CalibrationCard";
import ProfitabilityCard from "./ProfitabilityCard";
import { aiTradeHistoryApi } from "@/lib/aiTradeHistoryApi";
import type { AiTradeProfitabilityResponse } from "@/lib/aiTradeProfitability";

// 跨会话盈利体检卡 + 校准三问卡（realized-edge-calibration 轴 B/C）。
// 三条钉子：`travelToCostRatio` 与 `feeCoverage` 同屏（G-9）、任一 coverage<1 必显覆盖率（G-8）、
// 「样本不足」时数字**照常给出**（G-11：不足不等于藏起来）。

const RESPONSE: AiTradeProfitabilityResponse = {
  sampleTrips: 193,
  minTrips: 30,
  conclusive: true,
  costBasisAssumedTrips: 172,
  pooled: {
    roundTrips: 193, netPnlUsd: -7.4185, grossPnlUsd: -3.5437, feeUsd: 3.8748,
    winRate: 0.4041, avgWinUsd: 0.2092, avgLossUsd: -0.2064, payoff: 1.014,
    breakevenPayoff: 1.474, expectancyUsd: -0.0384, expectancyR: -0.011, rSamples: 191,
    tStat: -1.446, medianHoldSeconds: 1271, medianMoveBps: 13.9, medianCostBps: 7.01,
    travelToCostRatio: 1.98, feeCoverage: 0.1088, feeTrips: 21, costBasisAssumedTrips: 172,
  },
  risk: {
    rSamples: 191, rP10: -0.053, rP50: -0.011, rP90: 0.063, reachedOneRShare: 0,
    withinFifthRShare: 1, plannedRrMedian: 2, stopUtilizationP50: 0.0283,
    medianStopAtrMult: null, stopAtrCoverage: 0,
  },
  slices: {
    byBar: [{ key: "15m", trips: 98, netPnlUsd: 2.759, winRate: 0.44, expectancyUsd: 0.028, medianRMultiple: 0.004 }],
    byInstId: [], byExitSource: [], byPreset: [], byHorizonProfile: [], byConfidenceBucket: [],
  },
  calibration: {
    edgeNumerator: {
      n: 0, coverage: 0, conclusive: false, medianEstEdgeBps: null,
      medianRealizedGrossBps: -0.6, medianErrorBps: null, signAgreementRate: null,
    },
    costModel: {
      n: 21, coverage: 0.1088, conclusive: false, medianEstCostBps: null,
      medianRealizedCostBps: 7.01, medianErrorBps: null, modelConstantBps: 13,
      note: "193 笔往返里只有 21 笔带费率记录，其余为「未记录」而非「零费率」。",
    },
    confidenceReliability: { n: 0, coverage: 0, conclusive: false, buckets: [], monotonic: null },
  },
  verdict: {
    code: "no_edge_yet",
    title: "尚无正期望证据",
    detail: "193 笔往返的净额期望为 -0.0384 USDT/笔（t=-1.446）；其中 172 笔的盈亏口径为推定、21 笔带费率记录。",
  },
};

const fetchMock = vi.fn();

beforeEach(() => {
  fetchMock.mockReset().mockResolvedValue(RESPONSE);
  vi.spyOn(aiTradeHistoryApi, "profitability").mockImplementation(fetchMock);
});
afterEach(() => {
  vi.restoreAllMocks();
  cleanup();
});

function renderCard(node: React.ReactElement) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}>{node}</QueryClientProvider>);
}

describe("ProfitabilityCard", () => {
  it("零历史 ⇒ 整卡不渲染（绝不在没有数据的账户上挂一张空表）", async () => {
    fetchMock.mockResolvedValue({ ...RESPONSE, sampleTrips: 0 });
    const { container } = renderCard(<ProfitabilityCard />);
    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    expect(container.querySelector('[data-testid="ai-profitability-card"]')).toBeNull();
  });

  it("结论徽标与后端下发的 detail 逐字上屏（前端不拼数字）", async () => {
    renderCard(<ProfitabilityCard />);
    expect(await screen.findByText("尚无正期望证据")).toBeInTheDocument();
    expect(screen.getByTestId("ai-profitability-verdict")).toHaveTextContent(RESPONSE.verdict.detail);
  });

  it("G-9：travelToCostRatio 必须与它的 feeCoverage 同行上屏", async () => {
    renderCard(<ProfitabilityCard />);
    const row = await screen.findByTestId("ai-profitability-travel");
    expect(row).toHaveTextContent("1.98×");
    expect(row).toHaveTextContent("21 笔带费率记录");
    expect(row).toHaveTextContent("10.9%");
  });

  it("每个 KPI 都带着它自己的分母", async () => {
    renderCard(<ProfitabilityCard />);
    expect(await screen.findByText("已实现净额（193 笔）")).toBeInTheDocument();
    expect(screen.getByText("R 中位（191 笔可算）")).toBeInTheDocument();
    expect(screen.getByText("赔率 / 盈亏平衡")).toBeInTheDocument();
    expect(screen.getByText("1.014 / 需 1.474")).toBeInTheDocument();
  });

  it("R 分布上屏——「无一笔触及 ±1R」这条头条结论必须看得见", async () => {
    renderCard(<ProfitabilityCard />);
    const row = await screen.findByTestId("ai-profitability-risk");
    expect(row).toHaveTextContent("R 分布 p10 / p50 / p90 = -0.053 / -0.011 / 0.063（191 笔可算）");
    expect(row).toHaveTextContent("|R| < 0.2 占比 100.0%");
    expect(row).toHaveTextContent("计划盈亏比中位 2.00");
    expect(row).not.toHaveTextContent("×ATR");     // 该覆盖率为 0 ⇒ 整项省略，绝不显示一个 0 倍
  });

  it("口径推定的计数如实上屏（[P1-12]）", async () => {
    renderCard(<ProfitabilityCard />);
    expect(await screen.findByTestId("ai-profitability-basis")).toHaveTextContent("172 / 193");
  });

  it("G-11：样本不足时数字照常给出，只是徽标改口", async () => {
    fetchMock.mockResolvedValue({
      ...RESPONSE,
      conclusive: false,
      verdict: { code: "insufficient_samples", title: "样本不足，不下结论", detail: "目前只有 12 笔已完成往返" },
    });
    renderCard(<ProfitabilityCard />);
    expect(await screen.findByText("样本不足，不下结论")).toBeInTheDocument();
    expect(screen.getByTestId("ai-profitability-travel")).toHaveTextContent("1.98×");
  });
});

describe("CalibrationCard", () => {
  it("G-8：coverage < 1 的每一块都必须把覆盖率显示出来", async () => {
    renderCard(<CalibrationCard />);
    expect(await screen.findByTestId("ai-calibration-coverage-成本模型准不准")).toHaveTextContent(
      "覆盖 21 / 193 笔（10.9%）",
    );
    expect(screen.getByTestId("ai-calibration-coverage-门禁分子准不准")).toHaveTextContent(
      "覆盖 0 / 193 笔",
    );
  });

  it("估计侧零覆盖 ⇒ 明说「需要新会话积累」，而不是把整块藏起来（R-7）", async () => {
    renderCard(<CalibrationCard />);
    expect(await screen.findByTestId("ai-calibration-inconclusive-门禁分子准不准")).toHaveTextContent(
      "需要新会话积累",
    );
    expect(screen.getByText("已实现毛额中位：-0.6 bps")).toBeInTheDocument();
  });

  it("成本块的分母注解上屏（「未记录」不等于「零费率」）", async () => {
    renderCard(<CalibrationCard />);
    expect(await screen.findByText(/只有 21 笔带费率记录/)).toBeInTheDocument();
    expect(screen.getByText("模型常数：13.0 bps")).toBeInTheDocument();
  });

  it("桶全空时不宣称单调性", async () => {
    renderCard(<CalibrationCard />);
    expect(await screen.findByText(/并非每个区间都有足够样本/)).toBeInTheDocument();
  });
});
