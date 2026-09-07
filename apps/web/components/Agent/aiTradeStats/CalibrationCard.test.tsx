import "@testing-library/jest-dom/vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { aiTradeHistoryApi } from "@/lib/aiTradeHistoryApi";
import CalibrationCard from "./CalibrationCard";

const block = { n: 1, coverage: .2, conclusive: false };
const response = {
  schemaVersion: 2, sampleTrips: 2, minTrips: 30, conclusive: false,
  coverage: { totalTrips: 5, structuralIssueCount: 0 },
  calibration: {
    edgeNumerator: { ...block, signSamples: 1 },
    costModel: { ...block, comparisonBasis: "fees",
      availableEstimateTrips: 1, availableRealizedTrips: 2,
      medianEstFeeBps: 10, medianRealizedFeeBps: 10,
      medianFeeErrorBps: 0, medianEstTotalCostBps: 16,
      medianEstCostBps: null, medianRealizedCostBps: null,
      medianErrorBps: null, modelConstantBps: 18,
    },
    confidenceReliability: { ...block, monotonic: null, buckets: [{
      key: "0.8–1.0", trips: 2, netSamples: 2, rSamples: 1,
      winRate: .5, expectancyUsd: -2, medianRMultiple: -.1,
    }] },
  },
};
const fetchMock = vi.fn();

beforeEach(() => {
  fetchMock.mockReset().mockResolvedValue(response);
  vi.spyOn(aiTradeHistoryApi, "profitability").mockImplementation(fetchMock);
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

function mount() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(<QueryClientProvider client={client}>
    <CalibrationCard />
  </QueryClientProvider>);
}

it("手续费比较只展示同笔费用误差与总体分母", async () => {
  mount();
  expect(await screen.findByText("手续费估计中位：10.0 bps"))
    .toBeInTheDocument();
  expect(screen.getByText("实测手续费中位：10.0 bps")).toBeInTheDocument();
  expect(screen.getByText("手续费误差中位：0.0 bps")).toBeInTheDocument();
  expect(screen.getByText("历史总成本估计，仅作描述：16.0 bps"))
    .toBeInTheDocument();
  expect(screen.getByTestId("ai-calibration-coverage-成本模型准不准"))
    .toHaveTextContent("覆盖 1 / 5 笔（20.0%）");
  expect(screen.getByText(/可用估计 1 笔.*可用实测 2 笔.*同笔配对 1 笔/))
    .toBeInTheDocument();
  expect(screen.getByText(/净额 2 笔.*R 1 笔/)).toBeInTheDocument();
});

it("旧费用口径不被默认为新版手续费比较", async () => {
  fetchMock.mockResolvedValue({ ...response, schemaVersion: undefined,
    calibration: { ...response.calibration,
      costModel: { ...block, medianEstCostBps: 16,
        medianRealizedCostBps: 10, medianErrorBps: -6 },
    } });
  mount();
  expect(await screen.findByText(/旧版未提供同笔手续费比较/))
    .toBeInTheDocument();
  expect(screen.queryByText(/-6.0 bps/)).not.toBeInTheDocument();
  expect(screen.getByText("手续费误差中位：—")).toBeInTheDocument();
});

it("配对为零仍保留缺口与金额未知", async () => {
  fetchMock.mockResolvedValue({ ...response, sampleTrips: 0,
    calibration: { ...response.calibration, costModel: {
      ...response.calibration.costModel, n: 0, coverage: 0,
      medianEstFeeBps: null, medianRealizedFeeBps: null,
      medianFeeErrorBps: null,
    } } });
  mount();
  expect(await screen.findByTestId("ai-calibration-card")).toBeInTheDocument();
  expect(screen.getByText("手续费误差中位：—")).toBeInTheDocument();
});

it("失败明确提示并支持重试", async () => {
  fetchMock.mockRejectedValueOnce(new Error("offline"));
  mount();
  expect(await screen.findByRole("alert")).toHaveTextContent("取数失败");
  fireEvent.click(screen.getByRole("button", { name: "重试" }));
  expect(await screen.findByTestId("ai-calibration-card")).toBeInTheDocument();
  expect(fetchMock).toHaveBeenCalledTimes(2);
});
