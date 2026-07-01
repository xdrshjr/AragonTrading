import "@testing-library/jest-dom/vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { FactorMember } from "@/lib/factorTypes";
import { weightModelApi } from "@/lib/weightModelApi";
import type { WeightModel, WeightTrainRun } from "@/lib/weightModelTypes";
import WeightModelDetail from "./WeightModelDetail";

vi.mock("./TrainingCurveChart", () => ({ default: () => <div data-testid="curve" /> }));
vi.mock("./WeightBars", () => ({ default: () => <div data-testid="bars" /> }));
vi.mock("@/lib/weightModelApi", () => ({
  weightModelApi: { get: vi.fn(), runs: vi.fn(), run: vi.fn(), removeRun: vi.fn() },
}));

const api = weightModelApi as unknown as Record<string, ReturnType<typeof vi.fn>>;
const MEMBERS: FactorMember[] = [{ factorKey: "mom_roc", params: {}, weight: 0.5, direction: "long" }];

function model(): WeightModel {
  return {
    id: 3, name: "动量组合", instId: "BTC-USDT-SWAP", bar: "1H", members: MEMBERS, method: "linear",
    config: {}, horizon: 6, source: "manual", factorSetId: null, latestRunId: null, bestValidIc: 0.2,
    notes: "", enabled: true, latestRun: null, createdTs: 1, updatedTs: 2,
  };
}

function run(over: Partial<WeightTrainRun>): WeightTrainRun {
  return {
    id: 1, modelId: 3, status: "succeeded", method: "linear", instId: "BTC-USDT-SWAP", bar: "1H",
    startTs: 0, endTs: 1, horizon: 6, config: {}, weights: { mom_roc: 1 }, normStats: { normWindow: 100, clip: 4 },
    metrics: { icIn: 0.3, icOut: 0.2, r2In: null, r2Out: null, nTrain: 100, nValid: 40 },
    provenance: {}, trainerVersion: null, note: "", errorCode: null, errorMessage: null,
    curveCount: 0, startedTs: 0, finishedTs: 1, curve: [], ...over,
  };
}

function wrap(node: ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={client}>{node}</QueryClientProvider>);
}

beforeEach(() => {
  vi.clearAllMocks();
  localStorage.clear();
  api.get.mockResolvedValue(model());
});
afterEach(cleanup);

describe("WeightModelDetail — 记录三态与回放分支（H-F1/H-F7/H-F8）", () => {
  it("renders an error state (not empty) when the runs query fails (H-F1)", async () => {
    api.runs.mockRejectedValue(new Error("记录服务异常"));
    wrap(<WeightModelDetail modelId={3} onBack={() => {}} onTrain={() => {}} />);
    await waitFor(() => expect(screen.getByText(/加载训练记录失败：记录服务异常/)).toBeTruthy());
    expect(screen.queryByText(/还没有训练记录/)).toBeNull();   // 绝不渲染成空状态
    expect(screen.getByRole("button", { name: "重试" })).toBeTruthy();
  });

  it("renders a dedicated cancelled notice in run replay (H-F7)", async () => {
    const cancelled = run({ id: 9, status: "cancelled", weights: {} });
    api.runs.mockResolvedValue([cancelled]);
    api.run.mockResolvedValue(cancelled);
    wrap(<WeightModelDetail modelId={3} onBack={() => {}} onTrain={() => {}} />);
    await waitFor(() => expect(screen.getByText(/该次训练已取消/)).toBeTruthy());
  });

  it("selects a run via keyboard Enter on the row (H-F8)", async () => {
    const r1 = run({ id: 1 });
    const r2 = run({ id: 2 });
    api.runs.mockResolvedValue([r1, r2]);
    api.run.mockResolvedValue(r2);
    wrap(<WeightModelDetail modelId={3} onBack={() => {}} onTrain={() => {}} />);
    await waitFor(() => expect(screen.getAllByRole("button", { name: /训练记录/ }).length).toBe(2));
    const rows = screen.getAllByRole("button", { name: /训练记录/ });
    // 默认选中最新（id=2）；键盘 Enter 选中第一行（id=1）→ 触发其详情加载，证明行可键盘选中。
    fireEvent.keyDown(rows[0], { key: "Enter" });
    await waitFor(() => expect(api.run).toHaveBeenCalledWith(3, 1));
  });
});

describe("WeightModelDetail — 轮询 / 缓存失效 / 错误码（G-F2/G-F4/G-F6/G-T7）", () => {
  it("renders a friendly error-code label for a failed run, raw code as title (G-F6)", async () => {
    const failed = run({ id: 8, status: "failed", weights: {}, errorCode: "EMPTY_RANGE", errorMessage: "no candles" });
    api.runs.mockResolvedValue([failed]);
    api.run.mockResolvedValue(failed);
    wrap(<WeightModelDetail modelId={3} onBack={() => {}} onTrain={() => {}} />);
    await waitFor(() => expect(screen.getByText(/所选区间内没有已确认的行情/)).toBeTruthy());
    expect(screen.queryByText(/EMPTY_RANGE）/)).toBeNull();   // 原始码不再裸串外泄到正文
  });

  it("delete-run flow removes the run and invalidates the list cache (G-F4/G-T7)", async () => {
    const r5 = run({ id: 5 });
    api.runs.mockResolvedValue([r5]);
    api.run.mockResolvedValue(r5);
    api.removeRun.mockResolvedValue(undefined);
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const spy = vi.spyOn(client, "invalidateQueries");
    render(
      <QueryClientProvider client={client}>
        <WeightModelDetail modelId={3} onBack={() => {}} onTrain={() => {}} />
      </QueryClientProvider>,
    );
    await waitFor(() => expect(screen.getByRole("button", { name: "删除" })).toBeTruthy());
    fireEvent.click(screen.getByRole("button", { name: "删除" }));   // 行删除 → ConfirmDialog
    const dialog = await screen.findByRole("dialog");
    fireEvent.click(within(dialog).getByRole("button", { name: "删除" }));   // 确认
    await waitFor(() => expect(api.removeRun).toHaveBeenCalledWith(3, 5));
    expect(spy).toHaveBeenCalledWith({ queryKey: ["weight-models"] });   // G-F4：覆盖列表键
  });

  it("surfaces an in-app notice when delete run fails with 409 running (G-F4)", async () => {
    const r5 = run({ id: 5 });
    api.runs.mockResolvedValue([r5]);
    api.run.mockResolvedValue(r5);
    const err = Object.assign(new Error("running"), { code: "WEIGHT_RUN_RUNNING" });
    api.removeRun.mockRejectedValue(err);
    wrap(<WeightModelDetail modelId={3} onBack={() => {}} onTrain={() => {}} />);
    await waitFor(() => expect(screen.getByRole("button", { name: "删除" })).toBeTruthy());
    fireEvent.click(screen.getByRole("button", { name: "删除" }));
    const dialog = await screen.findByRole("dialog");
    fireEvent.click(within(dialog).getByRole("button", { name: "删除" }));
    await waitFor(() => expect(screen.getByText(/训练进行中，请先取消训练/)).toBeTruthy());
  });

  it("polls the runs query while a run is running (G-F2)", async () => {
    vi.useFakeTimers();
    try {
      const running = run({ id: 7, status: "running", weights: {}, finishedTs: null });
      api.runs.mockResolvedValue([running]);
      api.run.mockResolvedValue(running);
      const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
      render(
        <QueryClientProvider client={client}>
          <WeightModelDetail modelId={3} onBack={() => {}} onTrain={() => {}} />
        </QueryClientProvider>,
      );
      await vi.advanceTimersByTimeAsync(10);
      const first = api.runs.mock.calls.length;
      expect(first).toBeGreaterThanOrEqual(1);
      await vi.advanceTimersByTimeAsync(4100);   // 跨过一次 4s 轮询窗口
      expect(api.runs.mock.calls.length).toBeGreaterThan(first);   // running → 触发 refetch
    } finally {
      vi.useRealTimers();
    }
  });
});
