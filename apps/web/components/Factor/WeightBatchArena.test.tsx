import "@testing-library/jest-dom/vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { DEFAULT_THRESHOLDS } from "@/lib/factorTypes";
import type { TrainMethod, TrainMetrics } from "@/lib/factorTypes";
import { buildBatchTarget } from "@/lib/weightBatch";
import { weightBatchApi } from "@/lib/weightBatchApi";
import { weightBatchWS } from "@/lib/weightBatchWs";
import type { BatchStreamEvent, WeightBatchTarget } from "@/lib/weightBatchTypes";
import { useAppStore } from "@/lib/store";
import WeightBatchArena from "./WeightBatchArena";

vi.mock("./TrainingCurveChart", () => ({ default: () => <div data-testid="curve" /> }));
vi.mock("./WeightBars", () => ({ default: () => <div data-testid="bars" /> }));
vi.mock("@/lib/weightBatchApi", () => ({
  weightBatchApi: { get: vi.fn(), select: vi.fn(), cancelCandidate: vi.fn() },
}));
vi.mock("@/lib/weightModelApi", () => ({ weightModelApi: { removeRun: vi.fn() } }));
vi.mock("@/lib/api", () => ({ ApiRequestError: class extends Error {} }));
const wsHandle = { connect: vi.fn(), close: vi.fn() };
vi.mock("@/lib/weightBatchWs", () => ({ weightBatchWS: vi.fn(() => wsHandle) }));

const api = weightBatchApi as unknown as Record<string, ReturnType<typeof vi.fn>>;
const wsMock = weightBatchWS as unknown as ReturnType<typeof vi.fn>;

function metrics(icOut: number): TrainMetrics {
  return { icIn: 0.1, icOut, r2In: 0, r2Out: 0, nTrain: 100, nValid: 20 };
}

function applyTarget(): WeightBatchTarget {
  return buildBatchTarget({
    members: [{ factorKey: "mom_roc", params: {}, weight: 0, direction: "long" }],
    thresholds: { ...DEFAULT_THRESHOLDS }, horizon: 6, instId: "BTC-USDT-SWAP", bar: "1H",
    startTs: 1, endTs: 2, name: "对比", origin: "builder",
  });
}

function wrap(node: ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(<QueryClientProvider client={client}>{node}</QueryClientProvider>);
}

function emit(ev: BatchStreamEvent) {
  const onEvent = wsMock.mock.calls[0][1] as (e: BatchStreamEvent) => void;
  act(() => onEvent(ev));
}

// 驱动一个 2 候选批次跑到全部 succeeded（xgboost icOut 更高 → 最优）。
function driveTwoSucceeded() {
  emit({
    type: "batch",
    data: {
      batchId: 1, parentModelId: 9, status: "running",
      candidates: [
        { runId: 11, method: "linear", variantLabel: "", status: "queued" },
        { runId: 12, method: "xgboost", variantLabel: "", status: "queued" },
      ],
    },
  });
  const result = (method: TrainMethod, icOut: number) => ({
    method, weights: { mom_roc: 1 }, normStats: { normWindow: 200, clip: 4 }, metrics: metrics(icOut),
    trainerVersion: null, note: "", provenance: { instId: "BTC-USDT-SWAP", bar: "1H", startTs: 1, endTs: 2, horizon: 6 },
  });
  for (const [runId, method, ic] of [[11, "linear", 0.1], [12, "xgboost", 0.3]] as const) {
    emit({ type: "candidate", data: { runId, method, variantLabel: "", event: { type: "run", data: { runId, modelId: 9, status: "running" } } } });
    emit({ type: "candidate", data: { runId, method, variantLabel: "", event: { type: "result", data: result(method, ic) } } });
    emit({ type: "candidate", data: { runId, method, variantLabel: "", event: { type: "done", data: { runId, finishedTs: 9 } } } });
  }
}

beforeEach(() => {
  vi.clearAllMocks();
  localStorage.clear();
  useAppStore.setState({ weightTrainResult: null, boundModelId: null });
});
afterEach(cleanup);

describe("WeightBatchArena — 候选卡 / 排行榜高亮 / 择优仅 builder", () => {
  it("渲染候选卡并高亮最优（icOut 最高的 xgboost 得 🏆）", async () => {
    api.get.mockResolvedValue({ batchId: 1, parentModelId: 9, origin: "builder", selectedRunId: null, status: "running", instId: "BTC-USDT-SWAP", bar: "1H", startTs: 1, endTs: 2, horizon: 6, members: [], config: {}, candidates: [], ranking: [], factorSetId: null, createdTs: 0, updatedTs: 0 });
    wrap(<WeightBatchArena batchId={1} applyTarget={applyTarget()} onBack={vi.fn()} onViewModel={vi.fn()} />);
    await waitFor(() => expect(wsMock).toHaveBeenCalled());
    driveTwoSucceeded();
    // 候选标签渲染（线性岭回归 / XGBoost）。
    await waitFor(() => expect(screen.getAllByText(/线性岭回归/).length).toBeGreaterThan(0));
    expect(screen.getAllByText(/XGBoost/).length).toBeGreaterThan(0);
    // 最优候选 🏆 高亮（候选卡 + 排行榜均出现奖杯）。
    expect(screen.getAllByText("🏆").length).toBeGreaterThan(0);
  });

  it("builder-origin 全部完成后渲染「应用最佳到组合并返回」", async () => {
    api.get.mockResolvedValue({ batchId: 1, parentModelId: 9, origin: "builder", selectedRunId: null, status: "succeeded", instId: "BTC-USDT-SWAP", bar: "1H", startTs: 1, endTs: 2, horizon: 6, members: [], config: {}, candidates: [], ranking: [], factorSetId: null, createdTs: 0, updatedTs: 0 });
    wrap(<WeightBatchArena batchId={1} applyTarget={applyTarget()} onBack={vi.fn()} onViewModel={vi.fn()} />);
    await waitFor(() => expect(wsMock).toHaveBeenCalled());
    driveTwoSucceeded();
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "应用最佳到组合并返回" })).toBeInTheDocument(),
    );
  });

  it("model-origin（applyTarget=null）绝不渲染「应用最佳」（守 G-F1）", async () => {
    api.get.mockResolvedValue({ batchId: 1, parentModelId: 9, origin: "model", selectedRunId: null, status: "succeeded", instId: "BTC-USDT-SWAP", bar: "1H", startTs: 1, endTs: 2, horizon: 6, members: [], config: {}, candidates: [], ranking: [], factorSetId: null, createdTs: 0, updatedTs: 0 });
    wrap(<WeightBatchArena batchId={1} applyTarget={null} onBack={vi.fn()} onViewModel={vi.fn()} />);
    await waitFor(() => expect(wsMock).toHaveBeenCalled());
    driveTwoSucceeded();
    await waitFor(() => expect(screen.getAllByText(/XGBoost/).length).toBeGreaterThan(0));
    expect(screen.queryByRole("button", { name: "应用最佳到组合并返回" })).toBeNull();
  });
});
