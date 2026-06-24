import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { DEFAULT_COMBO_POLICY } from "@/lib/combo";
import { api } from "@/lib/api";
import { useAppStore } from "@/lib/store";
import ComboView from "./ComboView";

const COMBO_SCHEMA = {
  rebalanceBars: { type: "int", default: 24, min: 4, max: 240, step: 1, label: "再平衡间隔(根)" },
};

const USER_COMBO_MEMBERS = [
  { templateKey: "trend_pullback_long", params: {}, name: "顺势回踩做多" },
  { templateKey: "range_scalping_strategy", params: {}, name: "区间剥头皮" },
];

const STRATEGIES = vi.hoisted(() => [
  { id: 1, templateKey: "trend_pullback_long", name: "顺势回踩做多", description: "", kind: "atomic",
    params: {}, paramsSchema: {}, enabled: true, builtin: true },
  { id: 2, templateKey: "range_scalping_strategy", name: "区间剥头皮", description: "", kind: "atomic",
    params: {}, paramsSchema: {}, enabled: true, builtin: true },
  { id: 9, templateKey: "regime_rotation", name: "趋势自适应策略组合", description: "", kind: "combo",
    params: {}, enabled: true, builtin: true,
    paramsSchema: {
      rebalanceBars: { type: "int", default: 24, min: 4, max: 240, step: 1, label: "再平衡间隔(根)" },
    } },
  { id: 20, templateKey: "regime_rotation", name: "我的组合A", description: "", kind: "combo",
    enabled: true, builtin: false,
    params: {
      members: [
        { templateKey: "trend_pullback_long", params: {}, name: "顺势回踩做多" },
        { templateKey: "range_scalping_strategy", params: {}, name: "区间剥头皮" },
      ],
      selectionMetric: "sharpe", regimeGating: true,
    },
    paramsSchema: {
      rebalanceBars: { type: "int", default: 24, min: 4, max: 240, step: 1, label: "再平衡间隔(根)" },
    } },
]);

vi.mock("@/lib/api", () => ({
  api: {
    strategies: vi.fn().mockResolvedValue(STRATEGIES),
    createStrategy: vi.fn().mockResolvedValue({ id: 50, templateKey: "regime_rotation" }),
    updateStrategy: vi.fn().mockResolvedValue({ id: 20, templateKey: "regime_rotation" }),
    deleteStrategy: vi.fn().mockResolvedValue(undefined),
    comboPreview: vi.fn().mockResolvedValue({ runId: 1, metrics: {}, combo: {} }),
    backtest: vi.fn().mockResolvedValue({ runId: 2, metrics: {}, combo: {} }),
  },
}));

function renderView() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <ComboView />
    </QueryClientProvider>,
  );
}

// 勾选前 N 个原子成员候选（普通策略页签默认激活）。
function selectMembers(count: number) {
  const boxes = screen.getAllByRole("checkbox");
  for (let i = 0; i < count; i += 1) fireEvent.click(boxes[i]);
}

describe("ComboView", () => {
  beforeEach(() => {
    useAppStore.setState({
      comboDraft: { memberIds: [], policy: { ...DEFAULT_COMBO_POLICY } },
      comboEditingId: null,
    });
    vi.clearAllMocks();
  });
  afterEach(cleanup);

  it("lists atomic member candidates and excludes combos from the picker", async () => {
    renderView();
    expect(await screen.findByText("顺势回踩做多")).toBeTruthy();
    expect(screen.getByText("区间剥头皮")).toBeTruthy();
    // 组合自身不应出现在「普通策略」成员候选中（不能把组合当成员）。
    expect(screen.queryByText("趋势自适应策略组合")).toBeNull();
  });

  it("disables the run button until at least 2 members are selected", async () => {
    renderView();
    const button = (await screen.findByText("运行组合回测")) as HTMLButtonElement;
    expect(button.disabled).toBe(true);
  });

  it("saves a new combo with the correct POST payload", async () => {
    renderView();
    await screen.findByText("顺势回踩做多");
    selectMembers(2);
    fireEvent.change(screen.getByLabelText("组合名称"), { target: { value: "我的新组合" } });
    fireEvent.click(screen.getByText("保存组合"));
    await waitFor(() => expect(api.createStrategy).toHaveBeenCalledTimes(1));
    const payload = (api.createStrategy as ReturnType<typeof vi.fn>).mock.calls[0][0];
    expect(payload.templateKey).toBe("regime_rotation");
    expect(payload.name).toBe("我的新组合");
    expect(payload.params.members).toHaveLength(2);
  });

  it("disables save when the name is empty even with enough members", async () => {
    renderView();
    await screen.findByText("顺势回踩做多");
    selectMembers(2);
    expect((screen.getByText("保存组合") as HTMLButtonElement).disabled).toBe(true);
  });

  it("loads a combo into the editor and saves edits via PUT", async () => {
    renderView();
    await screen.findByText("我的组合A");
    const row = screen.getByText("我的组合A").closest("div")!.parentElement!.parentElement!;
    fireEvent.click(within(row).getByText("编辑"));
    expect((screen.getByLabelText("组合名称") as HTMLInputElement).value).toBe("我的组合A");
    fireEvent.click(screen.getByText("保存修改"));
    await waitFor(() => expect(api.updateStrategy).toHaveBeenCalledTimes(1));
    expect((api.updateStrategy as ReturnType<typeof vi.fn>).mock.calls[0][0]).toBe(20);
  });

  it("deletes a combo after the confirm dialog", async () => {
    renderView();
    await screen.findByText("我的组合A");
    const row = screen.getByText("我的组合A").closest("div")!.parentElement!.parentElement!;
    fireEvent.click(within(row).getByText("删除"));
    const dialog = await screen.findByRole("dialog");
    fireEvent.click(within(dialog).getByText("删除"));
    await waitFor(() => expect(api.deleteStrategy).toHaveBeenCalledWith(20));
  });

  it("imports members from a saved combo via the combo tab", async () => {
    renderView();
    await screen.findByText("顺势回踩做多");
    fireEvent.click(screen.getByText("组合策略"));
    fireEvent.click(await screen.findByText("导入成员"));
    expect(await screen.findByText(/已展开导入 2 个原子成员/)).toBeTruthy();
  });
});
