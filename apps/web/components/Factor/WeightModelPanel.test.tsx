import "@testing-library/jest-dom/vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { DEFAULT_THRESHOLDS, type FactorMember, type FactorSet } from "@/lib/factorTypes";
import { buildTrainTarget } from "@/lib/weightModel";
import { weightModelApi } from "@/lib/weightModelApi";
import type { WeightModel, WeightTrainResult } from "@/lib/weightModelTypes";
import { useAppStore } from "@/lib/store";
import { factorApi } from "@/lib/factorApi";
import FactorSetBuilder from "./FactorSetBuilder";
import WeightModelPanel from "./WeightModelPanel";

// 固定工作台（panel 取 range；builder 取 instId/bar/范围），避免拉起整个实验室。
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
// 叶子组件自带数据拉取 / 候选交互，本测聚焦路由与交接，桩替代即可。
vi.mock("./MemberPalette", () => ({ default: () => <div data-testid="palette" /> }));
vi.mock("@/lib/weightModelApi", () => ({
  weightModelApi: {
    list: vi.fn(),
    get: vi.fn(),
    create: vi.fn(),
    update: vi.fn(),
    remove: vi.fn(),
    runs: vi.fn(),
    run: vi.fn(),
    removeRun: vi.fn(),
    train: vi.fn(),
    cancel: vi.fn(),
  },
}));
// WS 流不在冒烟范围内：桩为可连/可关句柄，断言落在「是否进实时视图 + 是否发起训练」。
const wsClose = vi.fn();
vi.mock("@/lib/weightTrainWs", () => ({
  weightTrainWS: vi.fn(() => ({ connect: vi.fn(), close: wsClose })),
}));
vi.mock("@/lib/factorApi", () => ({
  factorApi: {
    library: vi.fn(),
    trainers: vi.fn(),
    train: vi.fn(),
    sets: { get: vi.fn(), create: vi.fn(), update: vi.fn() },
  },
}));
vi.mock("@/lib/api", () => ({
  api: { createStrategy: vi.fn(), updateStrategy: vi.fn() },
  ApiRequestError: class extends Error {},
}));

const api = weightModelApi as unknown as Record<string, ReturnType<typeof vi.fn>>;

const MEMBERS: FactorMember[] = [
  { factorKey: "mom_roc", params: {}, weight: 0.6, direction: "long" },
  { factorKey: "ema_slope", params: {}, weight: 0.4, direction: "long" },
];

function model(over: Partial<WeightModel> = {}): WeightModel {
  return {
    id: 3,
    name: "动量组合",
    instId: "BTC-USDT-SWAP",
    bar: "1H",
    members: MEMBERS,
    method: "linear",
    config: {},
    horizon: 6,
    source: "manual",
    factorSetId: null,
    latestRunId: null,
    bestValidIc: 0.18,
    notes: "",
    enabled: true,
    latestRun: null,
    createdTs: 1,
    updatedTs: 2,
    ...over,
  };
}

function trainedSet(): FactorSet {
  return {
    id: 7,
    name: "我的组合",
    members: MEMBERS,
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

function wrap(node: ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={client}>{node}</QueryClientProvider>);
  return client;
}

beforeEach(() => {
  vi.clearAllMocks();
  localStorage.clear();
  useAppStore.setState({
    weightTrainTarget: null,
    weightTrainResult: null,
    boundModelId: null,
    factorLabSection: "builder",
    factorEditTarget: null,
  });
  api.list.mockResolvedValue([]);
  api.train.mockResolvedValue({ jobId: "job-1", runId: 11, modelId: 9 });
  (factorApi.library as ReturnType<typeof vi.fn>).mockResolvedValue([]);
  (factorApi.trainers as ReturnType<typeof vi.fn>).mockResolvedValue([]);
});
afterEach(cleanup);

describe("WeightModelPanel — 路由与交接消费", () => {
  it("renders the model list from weightModelApi.list", async () => {
    api.list.mockResolvedValue([model()]);
    wrap(<WeightModelPanel />);

    await waitFor(() => expect(screen.getByText("动量组合")).toBeTruthy());
    expect(api.list).toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "训练" })).toBeTruthy();
  });

  it("consumes store.weightTrainTarget once: jumps to live view, clears the target, fires training", async () => {
    const target = buildTrainTarget({
      members: MEMBERS,
      method: "linear",
      thresholds: DEFAULT_THRESHOLDS,
      horizon: 6,
      instId: "BTC-USDT-SWAP",
      bar: "1H",
      startTs: 1,
      endTs: 2,
      name: "来自搭建台",
      factorSetId: 7,
    });
    useAppStore.setState({ weightTrainTarget: target });
    wrap(<WeightModelPanel />);

    await waitFor(() => expect(screen.getByText("实时训练")).toBeTruthy());
    // 一次性消费：交接载荷已清空（模式同 factorEditTarget）。
    expect(useAppStore.getState().weightTrainTarget).toBeNull();
    await waitFor(() => expect(api.train).toHaveBeenCalledTimes(1));
    expect(api.train).toHaveBeenCalledWith(
      expect.objectContaining({ members: MEMBERS, method: "linear", factorSetId: 7, name: "来自搭建台" }),
    );
  });

  it("list 训练 button enters live view and starts training with the model spec", async () => {
    api.list.mockResolvedValue([model()]);
    wrap(<WeightModelPanel />);

    await waitFor(() => expect(screen.getByText("动量组合")).toBeTruthy());
    fireEvent.click(screen.getByRole("button", { name: "训练" }));

    await waitFor(() => expect(screen.getByText("实时训练")).toBeTruthy());
    await waitFor(() => expect(api.train).toHaveBeenCalledTimes(1));
    expect(api.train).toHaveBeenCalledWith(
      expect.objectContaining({ members: MEMBERS, modelId: 3, instId: "BTC-USDT-SWAP", bar: "1H" }),
    );
  });
});

describe("WeightModelPanel — 列表三态与训练拒绝回灌（H-T3）", () => {
  it("renders loading state while list query is pending", () => {
    api.list.mockReturnValue(new Promise(() => {}));   // 永不 resolve → pending
    wrap(<WeightModelPanel />);
    expect(screen.getByText("加载权重模型...")).toBeTruthy();
  });

  it("renders empty state when list is empty", async () => {
    api.list.mockResolvedValue([]);
    wrap(<WeightModelPanel />);
    await waitFor(() => expect(screen.getByText("还没有权重模型")).toBeTruthy());
  });

  it("renders error state (not empty) when list query fails", async () => {
    api.list.mockRejectedValue(new Error("后端不可达"));
    wrap(<WeightModelPanel />);
    await waitFor(() => expect(screen.getByText(/加载权重模型失败：后端不可达/)).toBeTruthy());
    expect(screen.getByRole("button", { name: "重试" })).toBeTruthy();
  });

  it("surfaces a 429/413 train rejection in the live view (回灌可返回列表)", async () => {
    api.train.mockRejectedValue(new Error("并发训练作业已达上限 4，请稍后重试。"));
    const target = buildTrainTarget({
      members: MEMBERS, method: "linear", thresholds: DEFAULT_THRESHOLDS, horizon: 6,
      instId: "BTC-USDT-SWAP", bar: "1H", startTs: 1, endTs: 2, name: "繁忙组合",
    });
    useAppStore.setState({ weightTrainTarget: target });
    wrap(<WeightModelPanel />);

    await waitFor(() => expect(screen.getByText(/并发训练作业已达上限/)).toBeTruthy());
    expect(screen.getByRole("button", { name: "← 返回列表" })).toBeTruthy();   // 可回灌列表
  });
});

describe("FactorSetBuilder ⇄ 权重模型工作区 交接", () => {
  it("训练权重 writes the handoff target and switches to the models section", async () => {
    (factorApi.sets.get as ReturnType<typeof vi.fn>).mockResolvedValue(trainedSet());
    useAppStore.setState({ factorEditTarget: { source: "set", setId: 7 } });
    wrap(<FactorSetBuilder />);

    await waitFor(() => expect(screen.getByRole("button", { name: "训练权重" })).toBeTruthy());
    fireEvent.click(screen.getByRole("button", { name: "训练权重" }));

    const written = useAppStore.getState().weightTrainTarget;
    expect(written).not.toBeNull();
    expect(written?.members).toHaveLength(2);
    expect(written?.name).toBe("我的组合");
    expect(written?.factorSetId).toBe(7); // 携 editingSetId 复用同组合（R-02）
    expect(useAppStore.getState().factorLabSection).toBe("models");
  });

  it("consumes store.weightTrainResult: hydrates builder, enables 保存组合, clears + remembers model", async () => {
    const result: WeightTrainResult = {
      members: MEMBERS,
      method: "linear",
      name: "回流组合",
      thresholds: DEFAULT_THRESHOLDS,
      weights: { mom_roc: 0.6, ema_slope: 0.4 },
      normStats: { normWindow: 200, clip: 4 },
      metrics: { icIn: 0.2, icOut: 0.18, r2In: null, r2Out: null, nTrain: 100, nValid: 40 },
      provenance: { instId: "BTC-USDT-SWAP", bar: "1H", startTs: 1, endTs: 2, horizon: 6, trainedAt: 9 },
      trainerVersion: "sklearn",
      note: "",
      modelId: 5,
      runId: 11,
    };
    useAppStore.setState({ weightTrainResult: result });
    wrap(<FactorSetBuilder />);

    // 完整水合 → trainedFresh 真 → 新建态主按钮「保存组合」可点。
    await waitFor(() => {
      const save = screen.getByRole("button", { name: "保存组合" }) as HTMLButtonElement;
      expect(save.disabled).toBe(false);
    });
    // 一次性消费 + 复用记忆：载荷清空、boundModelId 记住模型（R-02）。
    expect(useAppStore.getState().weightTrainResult).toBeNull();
    expect(useAppStore.getState().boundModelId).toBe(5);
  });
});
