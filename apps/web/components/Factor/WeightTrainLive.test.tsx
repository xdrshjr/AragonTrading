import "@testing-library/jest-dom/vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { DEFAULT_THRESHOLDS } from "@/lib/factorTypes";
import { buildTrainTarget } from "@/lib/weightModel";
import { weightModelApi } from "@/lib/weightModelApi";
import { weightTrainWS } from "@/lib/weightTrainWs";
import type { WeightTrainTarget } from "@/lib/weightModelTypes";
import { useAppStore } from "@/lib/store";
import WeightTrainLive from "./WeightTrainLive";

vi.mock("./TrainingCurveChart", () => ({ default: () => <div data-testid="curve" /> }));
vi.mock("./WeightBars", () => ({ default: () => <div data-testid="bars" /> }));
vi.mock("@/lib/weightModelApi", () => ({ weightModelApi: { train: vi.fn(), cancel: vi.fn() } }));
vi.mock("@/lib/api", () => ({ ApiRequestError: class extends Error {} }));
const wsHandle = { connect: vi.fn(), close: vi.fn() };
vi.mock("@/lib/weightTrainWs", () => ({ weightTrainWS: vi.fn(() => wsHandle) }));

const api = weightModelApi as unknown as Record<string, ReturnType<typeof vi.fn>>;
const wsMock = weightTrainWS as unknown as ReturnType<typeof vi.fn>;

function target(origin: "builder" | "model"): WeightTrainTarget {
  return buildTrainTarget({
    members: [{ factorKey: "mom_roc", params: {}, weight: 0, direction: "long" }],
    method: "linear", thresholds: DEFAULT_THRESHOLDS, horizon: 6,
    instId: "BTC-USDT-SWAP", bar: "1H", startTs: 1, endTs: 2, name: "x", modelId: 7, origin,
  });
}

function wrap(node: ReactNode) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(<QueryClientProvider client={client}>{node}</QueryClientProvider>);
}

beforeEach(() => {
  vi.clearAllMocks();
  localStorage.clear();
  useAppStore.setState({ boundModelId: null, weightTrainResult: null });
});
afterEach(cleanup);

describe("WeightTrainLive — origin 记忆 / 连接恢复 / 卸载守卫", () => {
  it("records boundModelId only when origin is builder (H-F2)", async () => {
    api.train.mockResolvedValue({ jobId: "j1", runId: 1, modelId: 7 });
    wrap(<WeightTrainLive target={target("builder")} onBack={() => {}} onViewModel={() => {}} />);
    await waitFor(() => expect(wsMock).toHaveBeenCalled());
    expect(useAppStore.getState().boundModelId).toBe(7);
  });

  it("does NOT record boundModelId when origin is model (H-F2)", async () => {
    api.train.mockResolvedValue({ jobId: "j1", runId: 1, modelId: 7 });
    wrap(<WeightTrainLive target={target("model")} onBack={() => {}} onViewModel={() => {}} />);
    await waitFor(() => expect(wsMock).toHaveBeenCalled());
    expect(useAppStore.getState().boundModelId).toBeNull();
  });

  it("does not open the stream or setState if unmounted before train resolves (H-F4)", async () => {
    let resolveTrain: (v: unknown) => void = () => {};
    api.train.mockReturnValue(new Promise((res) => { resolveTrain = res; }));
    const { unmount } = wrap(<WeightTrainLive target={target("builder")} onBack={() => {}} onViewModel={() => {}} />);
    await waitFor(() => expect(api.train).toHaveBeenCalled());
    unmount();   // 在 train 解析前卸载
    await act(async () => { resolveTrain({ jobId: "j1", runId: 1, modelId: 7 }); });
    expect(wsMock).not.toHaveBeenCalled();                  // 卸载后不开流（不泄漏 socket）
    expect(useAppStore.getState().boundModelId).toBeNull(); // 卸载后不 setState 全局
  });

  it("recovers to streaming and surfaces an error when cancel fails (H-F3)", async () => {
    api.train.mockResolvedValue({ jobId: "j1", runId: 1, modelId: 7 });
    api.cancel.mockRejectedValue(new Error("delete failed"));
    wrap(<WeightTrainLive target={target("builder")} onBack={() => {}} onViewModel={() => {}} />);
    await waitFor(() => expect(screen.getByText("取消训练")).toBeTruthy());
    fireEvent.click(screen.getByText("取消训练"));
    await waitFor(() => expect(screen.getByText(/取消请求失败/)).toBeTruthy());
    expect(screen.getByText("取消训练")).toBeTruthy();   // 解禁可重试（非永久卡「取消中」）
  });
});
