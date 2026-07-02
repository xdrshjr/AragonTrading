import "@testing-library/jest-dom/vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { DEFAULT_THRESHOLDS } from "@/lib/factorTypes";
import { buildBatchTarget } from "@/lib/weightBatch";
import { factorApi } from "@/lib/factorApi";
import { weightBatchApi } from "@/lib/weightBatchApi";
import type { WeightBatchTarget } from "@/lib/weightBatchTypes";
import WeightBatchLauncher from "./WeightBatchLauncher";

vi.mock("@/lib/factorApi", () => ({ factorApi: { trainers: vi.fn() } }));
vi.mock("@/lib/weightBatchApi", () => ({ weightBatchApi: { start: vi.fn() } }));
vi.mock("@/lib/api", () => ({ ApiRequestError: class extends Error {} }));

const trainers = factorApi as unknown as { trainers: ReturnType<typeof vi.fn> };
const api = weightBatchApi as unknown as { start: ReturnType<typeof vi.fn> };

function target(methods?: WeightBatchTarget["methods"]): WeightBatchTarget {
  return buildBatchTarget({
    members: [{ factorKey: "mom_roc", params: {}, weight: 0, direction: "long" }],
    thresholds: { ...DEFAULT_THRESHOLDS, normWindow: 150 },
    horizon: 6, instId: "BTC-USDT-SWAP", bar: "1H", startTs: 1, endTs: 2, name: "对比",
    methods, origin: "builder",
  });
}

function wrap(node: ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(<QueryClientProvider client={client}>{node}</QueryClientProvider>);
}

beforeEach(() => {
  vi.clearAllMocks();
  trainers.trainers.mockResolvedValue([
    { method: "linear", available: true },
    { method: "random_forest", available: true },
    { method: "xgboost", available: false },
  ]);
});
afterEach(cleanup);

describe("WeightBatchLauncher — 方法多选 / 不可用禁用 / 候选数下限 / 提交体", () => {
  it("target 为空 → 引导态，点「去权重训练与组合」触发 onGoBuilder", () => {
    const onGoBuilder = vi.fn();
    wrap(<WeightBatchLauncher target={null} onLaunched={vi.fn()} onBack={vi.fn()} onGoBuilder={onGoBuilder} />);
    fireEvent.click(screen.getByRole("button", { name: "去权重训练与组合" }));
    expect(onGoBuilder).toHaveBeenCalled();
  });

  it("不可用方法（xgboost）复选框禁用", async () => {
    wrap(<WeightBatchLauncher target={target(["linear", "random_forest"])} onLaunched={vi.fn()} onBack={vi.fn()} onGoBuilder={vi.fn()} />);
    await waitFor(() =>
      expect((screen.getByRole("checkbox", { name: /XGBoost/ }) as HTMLInputElement).disabled).toBe(true),
    );
  });

  it("候选降到 <2 → 开始按钮禁用", async () => {
    wrap(<WeightBatchLauncher target={target(["linear", "random_forest"])} onLaunched={vi.fn()} onBack={vi.fn()} onGoBuilder={vi.fn()} />);
    await waitFor(() => expect(screen.getByRole("checkbox", { name: /随机森林/ })).toBeInTheDocument());
    fireEvent.click(screen.getByRole("checkbox", { name: /随机森林/ }));   // 取消一个 → 仅剩 1 个
    const start = screen.getByRole("button", { name: /开始批量训练/ }) as HTMLButtonElement;
    expect(start.disabled).toBe(true);
  });

  it("点开始 → 以所选方法构造提交体调用 weightBatchApi.start", async () => {
    const onLaunched = vi.fn();
    api.start.mockResolvedValue({ batchId: 1, parentModelId: 9, label: "对比", origin: "builder", candidates: [] });
    wrap(<WeightBatchLauncher target={target(["linear", "random_forest"])} onLaunched={onLaunched} onBack={vi.fn()} onGoBuilder={vi.fn()} />);
    await waitFor(() => expect(screen.getByRole("button", { name: /开始批量训练/ })).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: /开始批量训练/ }));
    await waitFor(() => expect(api.start).toHaveBeenCalled());
    const body = api.start.mock.calls[0][0];
    expect(body.candidates).toEqual([{ method: "linear" }, { method: "random_forest" }]);
    expect(body.config).toEqual({ normWindow: 150, thresholds: expect.any(Object) });
    expect(body.origin).toBe("builder");
    await waitFor(() => expect(onLaunched).toHaveBeenCalled());
  });
});
