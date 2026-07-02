import "@testing-library/jest-dom/vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { FactorMember } from "@/lib/factorTypes";
import { weightModelApi } from "@/lib/weightModelApi";
import type { WeightModel, WeightTrainRun } from "@/lib/weightModelTypes";
import WeightModelList from "./WeightModelList";

vi.mock("@/lib/weightModelApi", () => ({
  weightModelApi: { list: vi.fn(), remove: vi.fn() },
}));

const api = weightModelApi as unknown as Record<string, ReturnType<typeof vi.fn>>;
const MEMBERS: FactorMember[] = [{ factorKey: "mom_roc", params: {}, weight: 0.5, direction: "long" }];

function model(over: Partial<WeightModel> = {}): WeightModel {
  return {
    id: 3, name: "动量组合", instId: "BTC-USDT-SWAP", bar: "1H", members: MEMBERS, method: "linear",
    config: {}, horizon: 6, source: "manual", factorSetId: null, latestRunId: null, bestValidIc: 0.2,
    notes: "", enabled: true, latestRun: null, createdTs: 1, updatedTs: 2, ...over,
  };
}

function runningRun(): WeightTrainRun {
  return {
    id: 9, modelId: 3, status: "running", method: "linear", instId: "BTC-USDT-SWAP", bar: "1H",
    startTs: 0, endTs: 1, horizon: 6, config: {}, weights: {}, normStats: {}, metrics: null,
    provenance: {}, trainerVersion: null, note: "", errorCode: null, errorMessage: null,
    curveCount: 0, startedTs: 0, finishedTs: null, curve: [],
  };
}

function wrap(node: ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={client}>{node}</QueryClientProvider>);
  return client;
}

const noop = () => {};

beforeEach(() => {
  vi.clearAllMocks();
  localStorage.clear();
  api.list.mockResolvedValue([model()]);
  api.remove.mockResolvedValue(undefined);
});
afterEach(cleanup);

describe("WeightModelList — H-F10 守护 + 缓存失效（G-T1）", () => {
  it("toggling 显示已停用 re-queries the list with enabled=undefined", async () => {
    wrap(<WeightModelList onTrain={noop} onView={noop} onBacktest={noop} onEdit={noop} onNew={noop} onNewBatch={noop} onOpenBatch={noop} />);
    await waitFor(() => expect(screen.getByText("动量组合")).toBeTruthy());   // 列表已渲染
    expect(api.list).toHaveBeenCalledWith(true);                              // 默认仅启用
    fireEvent.click(screen.getByRole("switch"));                             // 切到「显示已停用」
    await waitFor(() => expect(api.list).toHaveBeenCalledWith(undefined));    // 改 list 入参
  });

  it("disables the 训练 button for a model whose latest run is running (G-F10)", async () => {
    api.list.mockResolvedValue([model({ latestRun: runningRun() })]);
    wrap(<WeightModelList onTrain={noop} onView={noop} onBacktest={noop} onEdit={noop} onNew={noop} onNewBatch={noop} onOpenBatch={noop} />);
    await waitFor(() => expect(screen.getByText("动量组合")).toBeTruthy());
    const train = screen.getByRole("button", { name: "训练" }) as HTMLButtonElement;
    expect(train.disabled).toBe(true);   // 在跑模型禁「训练」，杜绝重叠 run
  });

  it("switch has no redundant aria-label and is associated via <label htmlFor> (G-F11)", async () => {
    wrap(<WeightModelList onTrain={noop} onView={noop} onBacktest={noop} onEdit={noop} onNew={noop} onNewBatch={noop} onOpenBatch={noop} />);
    await waitFor(() => expect(screen.getByText("动量组合")).toBeTruthy());
    const sw = screen.getByRole("switch");
    expect(sw).toHaveAttribute("id", "wm-show-disabled");
    expect(sw).not.toHaveAttribute("aria-label");   // 不再双重播报
  });

  it("delete flow: ConfirmDialog → confirm → remove + invalidate ['weight-models']", async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const spy = vi.spyOn(client, "invalidateQueries");
    render(
      <QueryClientProvider client={client}>
        <WeightModelList onTrain={noop} onView={noop} onBacktest={noop} onEdit={noop} onNew={noop} onNewBatch={noop} onOpenBatch={noop} />
      </QueryClientProvider>,
    );
    await waitFor(() => expect(screen.getByText("动量组合")).toBeTruthy());
    fireEvent.click(screen.getByRole("button", { name: "删除" }));   // 卡片删除 → 确认弹窗
    const dialog = await screen.findByRole("dialog");
    fireEvent.click(within(dialog).getByRole("button", { name: "删除" }));
    await waitFor(() => expect(api.remove).toHaveBeenCalledWith(3));
    await waitFor(() => expect(spy).toHaveBeenCalledWith({ queryKey: ["weight-models"] }));
  });

  it("cancelling the ConfirmDialog does not delete", async () => {
    wrap(<WeightModelList onTrain={noop} onView={noop} onBacktest={noop} onEdit={noop} onNew={noop} onNewBatch={noop} onOpenBatch={noop} />);
    await waitFor(() => expect(screen.getByText("动量组合")).toBeTruthy());
    fireEvent.click(screen.getByRole("button", { name: "删除" }));
    const dialog = await screen.findByRole("dialog");
    fireEvent.click(within(dialog).getByRole("button", { name: "取消" }));
    expect(api.remove).not.toHaveBeenCalled();
  });
});

describe("WeightModelList — 卡片一键回测入口（weight-model-backtest-replay §六）", () => {
  it("disables the 回测 button for a model with no successful run (latestRunId=null)", async () => {
    api.list.mockResolvedValue([model({ latestRunId: null })]);
    wrap(<WeightModelList onTrain={noop} onView={noop} onBacktest={noop} onEdit={noop} onNew={noop} onNewBatch={noop} onOpenBatch={noop} />);
    await waitFor(() => expect(screen.getByText("动量组合")).toBeTruthy());
    const backtest = screen.getByRole("button", { name: "回测" }) as HTMLButtonElement;
    expect(backtest.disabled).toBe(true);
    expect(backtest).toHaveAttribute("title", "请先完成一次成功训练");
  });

  it("enables 回测 and fires onBacktest with the model when latestRunId is present", async () => {
    const onBacktest = vi.fn();
    api.list.mockResolvedValue([model({ latestRunId: 42 })]);
    wrap(<WeightModelList onTrain={noop} onView={noop} onBacktest={onBacktest} onEdit={noop} onNew={noop} onNewBatch={noop} onOpenBatch={noop} />);
    await waitFor(() => expect(screen.getByText("动量组合")).toBeTruthy());
    const backtest = screen.getByRole("button", { name: "回测" }) as HTMLButtonElement;
    expect(backtest.disabled).toBe(false);
    fireEvent.click(backtest);
    expect(onBacktest).toHaveBeenCalledWith(expect.objectContaining({ id: 3, latestRunId: 42 }));
  });
});
