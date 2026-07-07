import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { DEFAULT_BACKTEST_CONFIG } from "@/lib/backtestConfig";
import type { BacktestConfig, Strategy } from "@/lib/types";
import ComboStrategyDetail from "./ComboStrategyDetail";

vi.mock("@/lib/api", () => ({
  api: { equity: vi.fn().mockResolvedValue([]) },
}));

const COMBO: Strategy = {
  id: 20, templateKey: "regime_rotation", name: "我的组合A", description: "趋势·震荡自适应",
  kind: "combo", enabled: true, builtin: false,
  params: {
    members: [
      { templateKey: "trend_pullback_long", params: {}, name: "顺势回踩做多" },
      { templateKey: "range_scalping_strategy", params: {}, name: "区间剥头皮" },
    ],
    selectionMetric: "sharpe", regimeGating: true,
  },
  paramsSchema: {},
};

const BACKTEST_CONFIG: BacktestConfig = {
  ...DEFAULT_BACKTEST_CONFIG,
  rangePreset: "custom",
  startTs: 1_700_000_000,
  endTs: 1_700_086_400,
};

function renderDetail({
  busy = false,
  backtestKind = "simple",
  mode = "backtest",
  paperActive = false,
}: {
  busy?: boolean;
  backtestKind?: "simple" | "walkforward";
  mode?: "backtest" | "paper";
  paperActive?: boolean;
} = {}) {
  const onRunBacktest = vi.fn();
  const onRunWalkForward = vi.fn();
  const onStartPaper = vi.fn();
  const onStopPaper = vi.fn();
  const onView = vi.fn();
  const onEdit = vi.fn();
  const onDelete = vi.fn();
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={queryClient}>
      <ComboStrategyDetail
        combo={COMBO}
        mode={mode}
        backtestKind={backtestKind}
        busy={busy}
        paperActive={paperActive}
        instId="BTC-USDT-SWAP"
        backtestConfig={BACKTEST_CONFIG}
        onRunBacktest={onRunBacktest}
        onRunWalkForward={onRunWalkForward}
        onStartPaper={onStartPaper}
        onStopPaper={onStopPaper}
        onView={onView}
        onEdit={onEdit}
        onDelete={onDelete}
      />
    </QueryClientProvider>,
  );
  return {
    onRunBacktest,
    onRunWalkForward,
    onStartPaper,
    onStopPaper,
    onView,
    onEdit,
    onDelete,
  };
}

describe("ComboStrategyDetail", () => {
  afterEach(cleanup);

  it("renders the combo name, summary and no atomic param grid / save-as", () => {
    renderDetail();
    expect(screen.getByText("我的组合A")).toBeTruthy();
    expect(screen.getByText("趋势·震荡自适应")).toBeTruthy();
    // 成员/轮动摘要（复用 summarizeComboMembers）。
    expect(screen.getByText(/2 个成员/)).toBeTruthy();
    // 组合详情不渲染原子参数编辑器的「另存为」。
    expect(screen.queryByText("另存为")).toBeNull();
  });

  it("opens the config modal and runs the backtest with the config from it", () => {
    const { onRunBacktest, onView, onEdit, onDelete } = renderDetail();
    // 「运行回测」现打开配置弹窗；弹窗内「开始回测」才触发回调（携带回测配置）。
    fireEvent.click(screen.getByText("运行回测"));
    fireEvent.click(screen.getByRole("button", { name: "开始回测" }));
    expect(onRunBacktest).toHaveBeenCalledWith(
      expect.objectContaining({
        bar: "1H",
        startTs: 1_700_000_000,
        endTs: 1_700_086_400,
      }),
    );
    fireEvent.click(screen.getByText("查看详情"));
    expect(onView).toHaveBeenCalledWith(COMBO);
    fireEvent.click(screen.getByText("编辑"));
    expect(onEdit).toHaveBeenCalledWith(COMBO);
    fireEvent.click(screen.getByText("删除"));
    expect(onDelete).toHaveBeenCalledWith(COMBO);
  });

  it("disables the action buttons while busy", () => {
    renderDetail({ busy: true });
    expect((screen.getByText("运行中...") as HTMLButtonElement).disabled).toBe(true);
  });

  it("runs walk-forward in advanced mode while keeping management actions", () => {
    const { onRunBacktest, onRunWalkForward, onView, onEdit, onDelete } = renderDetail({
      backtestKind: "walkforward",
    });

    expect(screen.queryByText("运行回测")).toBeNull();
    fireEvent.click(screen.getByText("运行高级回测"));
    expect(onRunWalkForward).toHaveBeenCalledWith(COMBO);
    expect(onRunBacktest).not.toHaveBeenCalled();

    fireEvent.click(screen.getByText("查看详情"));
    expect(onView).toHaveBeenCalledWith(COMBO);
    fireEvent.click(screen.getByText("编辑"));
    expect(onEdit).toHaveBeenCalledWith(COMBO);
    fireEvent.click(screen.getByText("删除"));
    expect(onDelete).toHaveBeenCalledWith(COMBO);
  });

  it("shows paper start controls without backtest controls in paper mode", () => {
    const { onRunBacktest, onStartPaper, onView, onEdit, onDelete } = renderDetail({
      mode: "paper",
    });

    expect(screen.queryByText("运行回测")).toBeNull();
    fireEvent.click(screen.getByText("启动实时模拟"));
    expect(onStartPaper).toHaveBeenCalledWith(COMBO, {
      source: "live",
      speed: 16,
      replayBars: 300,
    });
    expect(onRunBacktest).not.toHaveBeenCalled();

    fireEvent.click(screen.getByText("查看详情"));
    expect(onView).toHaveBeenCalledWith(COMBO);
    fireEvent.click(screen.getByText("编辑"));
    expect(onEdit).toHaveBeenCalledWith(COMBO);
    fireEvent.click(screen.getByText("删除"));
    expect(onDelete).toHaveBeenCalledWith(COMBO);
  });

  it("shows paper stop control for an active combo paper session", () => {
    const { onStopPaper } = renderDetail({ mode: "paper", paperActive: true });

    fireEvent.click(screen.getByText("停止模拟"));
    expect(onStopPaper).toHaveBeenCalledOnce();
  });
});
