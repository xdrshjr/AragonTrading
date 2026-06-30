import "@testing-library/jest-dom/vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { factorApi } from "@/lib/factorApi";
import type { FactorSet } from "@/lib/factorTypes";
import { useAppStore } from "@/lib/store";
import FactorSetBuilder from "./FactorSetBuilder";

// builder 依赖 FactorLab 的 WorkbenchContext；用 mock 提供固定工作台，避免拉起整个实验室。
vi.mock("./FactorLab", () => ({
  useWorkbench: () => ({
    instId: "BTC-USDT-SWAP",
    bar: "1H",
    startTs: 1,
    endTs: 2,
    setInstId: () => {},
    setBar: () => {},
    setRange: () => {},
    evalTarget: null,
    setEvalTarget: () => {},
    pendingMembers: [],
    addPendingMember: () => {},
    clearPendingMembers: () => {},
  }),
}));
// MemberPalette 自带数据拉取与候选交互，本测聚焦水合 / 编辑态，桩替代即可。
vi.mock("./MemberPalette", () => ({ default: () => <div data-testid="palette" /> }));
vi.mock("@/lib/factorApi", () => ({
  factorApi: {
    library: vi.fn(),
    trainers: vi.fn(),
    train: vi.fn(),
    sets: { get: vi.fn(), create: vi.fn(), update: vi.fn() },
  },
}));
vi.mock("@/lib/api", () => ({ api: { createStrategy: vi.fn(), updateStrategy: vi.fn() } }));

const setsApi = factorApi.sets as unknown as { get: ReturnType<typeof vi.fn> };

function trainedSet(): FactorSet {
  return {
    id: 7,
    name: "我的组合",
    members: [
      { factorKey: "mom_roc", params: {}, weight: 0.6, direction: "long" },
      { factorKey: "ema_slope", params: {}, weight: 0.4, direction: "long" },
    ],
    method: "linear",
    config: { thresholds: { enterLong: 0.3, enterShort: 0.3, exitBand: 0.1, normWindow: 200, squash: true } },
    model: {
      weights: { mom_roc: 0.6, ema_slope: 0.4 },
      normStats: { normWindow: 200, clip: 4 },
      provenance: { instId: "BTC-USDT-SWAP", bar: "1H", startTs: 1, endTs: 2, horizon: 6, trainedAt: 9 },
      trainedAt: 9,
    },
    metrics: { icIn: 0.2, icOut: 0.18, r2In: null, r2Out: null, nTrain: 100, nValid: 40 },
    enabled: true,
    createdTs: 1,
    updatedTs: 2,
  };
}

function renderBuilder() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <FactorSetBuilder />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  localStorage.clear();
  (factorApi.library as ReturnType<typeof vi.fn>).mockResolvedValue([]);
  (factorApi.trainers as ReturnType<typeof vi.fn>).mockResolvedValue([]);
  useAppStore.setState({ factorEditTarget: null });
});
afterEach(cleanup);

describe("FactorSetBuilder — 编辑态水合", () => {
  it("hydrates a set edit target, keeps training fresh (H3), enables 更新组合, consumes the target", async () => {
    setsApi.get.mockResolvedValue(trainedSet());
    useAppStore.setState({ factorEditTarget: { source: "set", setId: 7 } });
    renderBuilder();

    await waitFor(() => expect(screen.getByText(/正在编辑组合/)).toBeTruthy());
    expect(setsApi.get).toHaveBeenCalledWith(7);
    // H3：水合的训练结果未被 [members] effect 清掉 → trainedFresh 为真 → 「更新组合」可点。
    const updateBtn = screen.getByRole("button", { name: "更新组合" }) as HTMLButtonElement;
    expect(updateBtn.disabled).toBe(false);
    // 一次性消费：交接载荷已清空（P2-2）。
    expect(useAppStore.getState().factorEditTarget).toBeNull();
  });

  it("hydrates a snapshot edit target and offers 更新该策略 + 另存为组合 (DoD #4)", async () => {
    useAppStore.setState({
      factorEditTarget: {
        source: "snapshot",
        strategyId: 5,
        name: "旧因子策略",
        snapshot: {
          members: [
            { factorKey: "mom_roc", params: {}, weight: 0.6, direction: "long" },
            { factorKey: "rev_rsi", params: {}, weight: 0.4, direction: "short" },
          ],
          thresholds: { enterLong: 0.3, enterShort: 0.3, exitBand: 0.1, normWindow: 200, squash: true },
        },
      },
    });
    renderBuilder();

    await waitFor(() => expect(screen.getByRole("button", { name: "更新该策略" })).toBeTruthy());
    expect(screen.getByRole("button", { name: "另存为组合" })).toBeTruthy();
    expect(setsApi.get).not.toHaveBeenCalled();
  });
});
