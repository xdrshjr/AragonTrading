import "@testing-library/jest-dom/vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { DEFAULT_THRESHOLDS } from "@/lib/factorTypes";
import type { FactorMember, TrainMethod } from "@/lib/factorTypes";
import { methodLabel } from "@/lib/weightBatch";
import { weightBatchApi } from "@/lib/weightBatchApi";
import { weightExploreApi } from "@/lib/weightExploreApi";
import { weightBatchWS } from "@/lib/weightBatchWs";
import type { BatchStreamEvent } from "@/lib/weightBatchTypes";
import type { TrainResultEvent } from "@/lib/weightModelTypes";
import { useAppStore } from "@/lib/store";
import WeightExploreModal from "./WeightExploreModal";

vi.mock("./TrainingCurveChart", () => ({ default: () => <div data-testid="curve" /> }));
vi.mock("./WeightBars", () => ({ default: () => <div data-testid="bars" /> }));
vi.mock("./WeightBatchCompare", () => ({
  default: ({ ranking }: { ranking: unknown[] }) => <div data-testid="ranking">{ranking.length}</div>,
}));
vi.mock("@/lib/api", () => ({ ApiRequestError: class extends Error {} }));
vi.mock("@/lib/weightExploreApi", () => ({ weightExploreApi: { start: vi.fn() } }));
vi.mock("@/lib/weightBatchApi", () => ({
  weightBatchApi: { cancel: vi.fn().mockResolvedValue({}), remove: vi.fn().mockResolvedValue(undefined) },
}));
vi.mock("@/lib/factorApi", () => ({ factorApi: { trainers: vi.fn(), list: vi.fn() } }));
const wsHandle = { connect: vi.fn(), close: vi.fn() };
vi.mock("@/lib/weightBatchWs", () => ({ weightBatchWS: vi.fn(() => wsHandle) }));

import { factorApi } from "@/lib/factorApi";

const startMock = weightExploreApi.start as unknown as ReturnType<typeof vi.fn>;
const cancelMock = weightBatchApi.cancel as unknown as ReturnType<typeof vi.fn>;
const wsMock = weightBatchWS as unknown as ReturnType<typeof vi.fn>;
const trainersMock = factorApi.trainers as unknown as ReturnType<typeof vi.fn>;

const M = (key: string): FactorMember => ({ factorKey: key, params: {}, weight: 0, direction: "long" });

function ctx(members: FactorMember[]) {
  return {
    library: [] as never[],
    members,
    thresholds: { ...DEFAULT_THRESHOLDS, normWindow: 150 },
    name: "组合X",
    instId: "BTC-USDT-SWAP",
    bar: "1H",
    startTs: 1,
    endTs: 2,
  };
}

function wrap(node: ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(<QueryClientProvider client={client}>{node}</QueryClientProvider>);
}

function emit(ev: BatchStreamEvent) {
  const onEvent = wsMock.mock.calls[0][1] as (e: BatchStreamEvent) => void;
  act(() => onEvent(ev));
}

function resultEvent(method: TrainMethod, icOut: number): TrainResultEvent {
  return {
    method,
    weights: { a: 0.6, b: 0.4 },
    normStats: { normWindow: 150, clip: 4 },
    metrics: { icIn: 0.1, icOut, r2In: 0, r2Out: 0, nTrain: 100, nValid: 20 },
    trainerVersion: null,
    note: "",
    provenance: { instId: "BTC-USDT-SWAP", bar: "1H", startTs: 1, endTs: 2, horizon: 6 },
  };
}

// 驱动 3 候选（C0 × linear/rf/xgb）全部 succeeded（xgb icOut 最高 → 最优）。
function drive3Succeeded() {
  const roster = [
    { runId: 101, method: "linear" as TrainMethod, variantLabel: "C0", status: "queued" as const },
    { runId: 102, method: "random_forest" as TrainMethod, variantLabel: "C0", status: "queued" as const },
    { runId: 103, method: "xgboost" as TrainMethod, variantLabel: "C0", status: "queued" as const },
  ];
  emit({ type: "batch", data: { batchId: 5, parentModelId: 9, status: "running", candidates: roster } });
  for (const [runId, method, ic] of [
    [101, "linear", 0.1],
    [102, "random_forest", 0.2],
    [103, "xgboost", 0.3],
  ] as const) {
    emit({ type: "candidate", data: { runId, method, variantLabel: "C0", event: { type: "result", data: resultEvent(method, ic) } } });
    emit({ type: "candidate", data: { runId, method, variantLabel: "C0", event: { type: "done", data: { runId, finishedTs: 9 } } } });
  }
  emit({ type: "batchDone", data: { batchId: 5, finishedTs: 9, ranking: [] } });
}

// 驱动一批（组合×方法）候选全部 succeeded：ics = [runId, method, variantLabel, icOut][]。
function driveExplore(roster: { runId: number; method: TrainMethod; variantLabel: string; status: "queued" }[], ics: [number, TrainMethod, string, number][]) {
  emit({ type: "batch", data: { batchId: 5, parentModelId: 9, status: "running", candidates: roster } });
  for (const [runId, method, variantLabel, ic] of ics) {
    emit({ type: "candidate", data: { runId, method, variantLabel, event: { type: "result", data: resultEvent(method, ic) } } });
    emit({ type: "candidate", data: { runId, method, variantLabel, event: { type: "done", data: { runId, finishedTs: 9 } } } });
  }
  emit({ type: "batchDone", data: { batchId: 5, finishedTs: 9, ranking: [] } });
}

beforeEach(() => {
  vi.clearAllMocks();
  localStorage.clear();
  useAppStore.setState({ weightTrainResult: null, boundModelId: null });
  trainersMock.mockResolvedValue([]); // 缺省全部可用（available 回落 true）
});
afterEach(cleanup);

describe("WeightExploreModal", () => {
  it("blocks launch and warns when candidate count exceeds the cap", async () => {
    wrap(<WeightExploreModal onClose={vi.fn()} ctx={ctx([M("a"), M("b"), M("c"), M("d"), M("e"), M("f")])} />);
    const start = await screen.findByRole("button", { name: /开始探索训练/ });
    expect((start as HTMLButtonElement).disabled).toBe(true); // 6 因子默认 [5,6] → 7 组合 × 3 方法 = 21 > 12
    expect(screen.getByText(/候选数超过上限/)).toBeTruthy();
  });

  it("blocks launch when no training method is selected", async () => {
    wrap(<WeightExploreModal onClose={vi.fn()} ctx={ctx([M("a"), M("b"), M("c")])} />);
    await screen.findByRole("button", { name: /开始探索训练/ });
    const checked = screen.getAllByRole("checkbox").filter((c) => (c as HTMLInputElement).checked);
    checked.forEach((c) => fireEvent.click(c)); // 取消全部默认方法
    expect(screen.getByText(/请至少选择 1 个可用训练方法/)).toBeTruthy();
    expect((screen.getByRole("button", { name: /开始探索训练/ }) as HTMLButtonElement).disabled).toBe(true);
  });

  it("drives the matrix via WS and applies the best combo as a fresh draft (modelId null, subset members)", async () => {
    startMock.mockResolvedValue({
      batchId: 5, parentModelId: 9, label: "x", kind: "explore", origin: "builder",
      explorePlan: { strategy: "sizes", params: { sizes: [2] }, methods: ["linear", "random_forest", "xgboost"], poolKeys: ["a", "b"], combos: [] },
      candidates: [],
    });
    const onClose = vi.fn();
    wrap(<WeightExploreModal onClose={onClose} ctx={ctx([M("a"), M("b")])} />);

    fireEvent.click(await screen.findByRole("button", { name: /开始探索训练/ }));
    await waitFor(() => expect(startMock).toHaveBeenCalled());
    await waitFor(() => expect(wsMock).toHaveBeenCalled());

    drive3Succeeded();

    // 完成态：等待「应用最佳组合」按钮出现（全部终态 → result 态）。
    const applyBtn = await screen.findByRole("button", { name: "应用最佳组合到组合成员" });
    // 矩阵驱动：最优候选（xgb, IC 0.300）在热力图标注 🏆 + IC（矩阵格 + 摘要卡各一处）。
    expect(screen.getAllByText("+0.300").length).toBeGreaterThan(0);
    expect(screen.getAllByText("🏆").length).toBeGreaterThan(0);

    // 应用最佳组合 → 回流：胜出子集成员 [a,b] + method xgboost + modelId=null（全新草稿，不绑父模型）。
    fireEvent.click(applyBtn);
    const result = useAppStore.getState().weightTrainResult;
    expect(result?.modelId).toBeNull();
    expect(result?.members.map((m) => m.factorKey)).toEqual(["a", "b"]);
    expect(result?.method).toBe("xgboost");
    expect(onClose).toHaveBeenCalled();
  });

  it("cancels all running candidates via weightBatchApi.cancel", async () => {
    startMock.mockResolvedValue({
      batchId: 5, parentModelId: 9, label: "x", kind: "explore", origin: "builder",
      explorePlan: { strategy: "sizes", params: { sizes: [2] }, methods: ["linear", "random_forest", "xgboost"], poolKeys: ["a", "b"], combos: [] },
      candidates: [],
    });
    wrap(<WeightExploreModal onClose={vi.fn()} ctx={ctx([M("a"), M("b")])} />);
    fireEvent.click(await screen.findByRole("button", { name: /开始探索训练/ }));
    await waitFor(() => expect(wsMock).toHaveBeenCalled());
    emit({
      type: "batch",
      data: {
        batchId: 5, parentModelId: 9, status: "running",
        candidates: [
          { runId: 101, method: "linear", variantLabel: "C0", status: "queued" },
          { runId: 102, method: "xgboost", variantLabel: "C0", status: "queued" },
        ],
      },
    });
    fireEvent.click(await screen.findByRole("button", { name: "全部取消" }));
    await waitFor(() => expect(cancelMock).toHaveBeenCalledWith(5));
  });

  it("renders the factor-insights panel and disables refine when samples are insufficient", async () => {
    startMock.mockResolvedValue({
      batchId: 5, parentModelId: 9, label: "x", kind: "explore", origin: "builder",
      explorePlan: { strategy: "sizes", params: { sizes: [2] }, methods: ["linear", "random_forest", "xgboost"], poolKeys: ["a", "b"], combos: [] },
      candidates: [],
    });
    wrap(<WeightExploreModal onClose={vi.fn()} ctx={ctx([M("a"), M("b")])} />);
    fireEvent.click(await screen.findByRole("button", { name: /开始探索训练/ }));
    await waitFor(() => expect(wsMock).toHaveBeenCalled());
    drive3Succeeded();

    // 单组合 C0 → 因子恒在 → hasEnoughSamples=false → 洞察面板渲染弱样本提示（外壳把 insights 下传，P0-1）。
    await screen.findByRole("button", { name: "应用最佳组合到组合成员" });
    expect(screen.getByTestId("insights-weak")).toBeTruthy();
    const refineBtn = screen.getByRole("button", { name: "据洞察精修探索（下一轮）" });
    expect((refineBtn as HTMLButtonElement).disabled).toBe(true); // 样本不足 → 精修禁用（P2-3）
    expect(refineBtn.getAttribute("title")).toBeTruthy();
  });

  it("refines into a second round: manual combos on the last pool + breadcrumb, no config flash", async () => {
    startMock.mockResolvedValue({
      batchId: 5, parentModelId: 9, label: "x", kind: "explore", origin: "builder",
      explorePlan: { strategy: "sizes", params: { sizes: [2, 3] }, methods: ["linear"], poolKeys: ["a", "b", "c"], combos: [] },
      candidates: [],
    });
    wrap(<WeightExploreModal onClose={vi.fn()} ctx={ctx([M("a"), M("b"), M("c")])} />);
    await screen.findByRole("button", { name: /开始探索训练/ });
    // 仅留 linear（默认 3 方法），4 组合 × 1 方法 = 4 候选。
    const checked = screen.getAllByRole("checkbox").filter((c) => (c as HTMLInputElement).checked);
    fireEvent.click(checked[1]);
    fireEvent.click(checked[2]);
    fireEvent.click(screen.getByRole("button", { name: /开始探索训练/ }));
    await waitFor(() => expect(startMock).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(wsMock).toHaveBeenCalled());

    driveExplore(
      [
        { runId: 101, method: "linear", variantLabel: "C0", status: "queued" },
        { runId: 102, method: "linear", variantLabel: "C1", status: "queued" },
        { runId: 103, method: "linear", variantLabel: "C2", status: "queued" },
        { runId: 104, method: "linear", variantLabel: "C3", status: "queued" },
      ],
      [
        [101, "linear", "C0", 0.1],
        [102, "linear", "C1", 0.12],
        [103, "linear", "C2", 0.08],
        [104, "linear", "C3", 0.2], // 最佳 = C3 = [a,b,c]
      ],
    );

    const refineBtn = await screen.findByRole("button", { name: "据洞察精修探索（下一轮）" });
    expect((refineBtn as HTMLButtonElement).disabled).toBe(false); // 样本充足 → 精修可用
    fireEvent.click(refineBtn);

    // 第 2 轮：再发一次 POST，body 为 manual 组合，下标口径落在上一轮 lastConfig.pool（P1-1）。
    await waitFor(() => expect(startMock).toHaveBeenCalledTimes(2));
    const body = startMock.mock.calls[1][0];
    expect(body.strategy).toBe("manual");
    expect(body.params.combos).toEqual([[0, 1, 2], [0, 2]]); // B 本身 + B−最弱因子
    expect(body.methods).toEqual(["linear"]);
    expect(body.pool.map((m: FactorMember) => m.factorKey)).toEqual(["a", "b", "c"]); // 精修池=上一轮池
    expect(screen.getByText("第 2 轮")).toBeTruthy(); // 面包屑记录轮次
    expect(screen.queryByText(/探索因子池/)).toBeNull(); // P2-2：过渡期不闪回配置态
  });
});
