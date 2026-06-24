import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { DEFAULT_COMBO_POLICY } from "@/lib/combo";
import { api } from "@/lib/api";
import { useAppStore } from "@/lib/store";
import ComboEditorModal from "./ComboEditorModal";

const STRATEGIES = vi.hoisted(() => [
  { id: 1, templateKey: "trend_pullback_long", name: "顺势回踩做多", description: "", kind: "atomic",
    params: {}, paramsSchema: {}, enabled: true, builtin: true },
  { id: 2, templateKey: "range_scalping_strategy", name: "区间剥头皮", description: "", kind: "atomic",
    params: {}, paramsSchema: {}, enabled: true, builtin: true },
  { id: 9, templateKey: "regime_rotation", name: "内置组合", description: "", kind: "combo",
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

const PREVIEW = vi.hoisted(() => ({
  runId: 7,
  metrics: {
    totalReturn: 0.12, winRate: 0.5, profitFactor: 1.4, maxDrawdown: -0.08,
    sharpe: 1.2, tradeCount: 18, avgPnl: 3.2,
  },
  combo: {
    annualizedReturn: 0.25,
    timeline: [
      { startTs: 1, endTs: 2, memberKey: "a", memberName: "顺势回踩做多", regime: "trend_up", bars: 10, contribution: 0.05 },
    ],
    breakdown: [],
    notes: [],
  },
}));

vi.mock("@/lib/api", () => ({
  api: {
    strategies: vi.fn().mockResolvedValue(STRATEGIES),
    createStrategy: vi.fn().mockResolvedValue({ id: 50, templateKey: "regime_rotation", name: "我的新组合" }),
    updateStrategy: vi.fn().mockResolvedValue({ id: 20, templateKey: "regime_rotation" }),
    comboPreview: vi.fn().mockResolvedValue(PREVIEW),
  },
}));

function renderModal(props: Partial<Parameters<typeof ComboEditorModal>[0]> = {}) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const onClose = vi.fn();
  const onSaved = vi.fn();
  render(
    <QueryClientProvider client={qc}>
      <ComboEditorModal open editingId={null} onClose={onClose} onSaved={onSaved} {...props} />
    </QueryClientProvider>,
  );
  return { onClose, onSaved };
}

// 勾选前 N 个原子成员候选（「普通策略」页签默认激活）。
function selectMembers(count: number) {
  const boxes = screen.getAllByRole("checkbox");
  for (let i = 0; i < count; i += 1) fireEvent.click(boxes[i]);
}

describe("ComboEditorModal", () => {
  beforeEach(() => {
    useAppStore.setState({
      comboDraft: { memberIds: [], policy: { ...DEFAULT_COMBO_POLICY } },
      comboEditingId: null,
    });
    vi.clearAllMocks();
  });
  afterEach(cleanup);

  it("renders the new-combo title and disables save while the name is empty", async () => {
    renderModal();
    expect(screen.getByText("新建组合 · 体制自适应轮动")).toBeTruthy();
    // 先等 strategies 加载出成员候选，再勾选（标题先于 strategies 渲染，不能据它判定候选就绪）。
    await screen.findByText("顺势回踩做多");
    selectMembers(2);
    // 有足够成员但名称为空 → 「保存组合」仍禁用。
    expect((screen.getByText("保存组合") as HTMLButtonElement).disabled).toBe(true);
  });

  it("saves a new combo with the correct POST payload and reports it via onSaved", async () => {
    const { onSaved } = renderModal();
    await screen.findByText("顺势回踩做多");
    selectMembers(2);
    fireEvent.change(screen.getByLabelText("组合名称"), { target: { value: "我的新组合" } });
    fireEvent.click(screen.getByText("保存组合"));
    await waitFor(() => expect(api.createStrategy).toHaveBeenCalledTimes(1));
    const payload = (api.createStrategy as ReturnType<typeof vi.fn>).mock.calls[0][0];
    expect(payload.templateKey).toBe("regime_rotation");
    expect(payload.name).toBe("我的新组合");
    expect(payload.params.members).toHaveLength(2);
    await waitFor(() => expect(onSaved).toHaveBeenCalledWith({ id: 50, templateKey: "regime_rotation", name: "我的新组合" }));
  });

  it("backfills members / params / name from editingId and saves edits via PUT", async () => {
    renderModal({ editingId: 20 });
    await waitFor(() =>
      expect((screen.getByLabelText("组合名称") as HTMLInputElement).value).toBe("我的组合A"),
    );
    expect(screen.getByText("保存修改")).toBeTruthy();
    fireEvent.click(screen.getByText("保存修改"));
    await waitFor(() => expect(api.updateStrategy).toHaveBeenCalledTimes(1));
    expect((api.updateStrategy as ReturnType<typeof vi.fn>).mock.calls[0][0]).toBe(20);
  });

  it("runs a combo preview and renders ComboResult (annualized + timeline)", async () => {
    renderModal();
    await screen.findByText("顺势回踩做多");
    selectMembers(2);
    fireEvent.click(screen.getByText("运行组合回测"));
    expect(await screen.findByText("期望年化收益")).toBeTruthy();
    expect(screen.getByText("25.00%")).toBeTruthy();
    expect(screen.getByText("轮动时间线")).toBeTruthy();
  });

  it("shows an inline error state when the preview fails", async () => {
    (api.comboPreview as ReturnType<typeof vi.fn>).mockRejectedValueOnce(new Error("OKX 不可达"));
    renderModal();
    await screen.findByText("顺势回踩做多");
    selectMembers(2);
    fireEvent.click(screen.getByText("运行组合回测"));
    expect(await screen.findByText(/回测失败：OKX 不可达/)).toBeTruthy();
  });

  it("closes on Escape and on overlay click via the Modal a11y contract", async () => {
    const { onClose } = renderModal();
    await screen.findByText("顺势回踩做多");
    fireEvent.keyDown(screen.getByRole("dialog"), { key: "Escape" });
    expect(onClose).toHaveBeenCalled();
    const overlay = screen.getByRole("dialog").parentElement as HTMLElement;
    fireEvent.click(overlay);
    expect(onClose).toHaveBeenCalledTimes(2);
  });
});
