import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Strategy } from "@/lib/types";
import ComboStrategyDetail from "./ComboStrategyDetail";

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

function renderDetail(busy = false) {
  const onRunBacktest = vi.fn();
  const onView = vi.fn();
  const onEdit = vi.fn();
  const onDelete = vi.fn();
  render(
    <ComboStrategyDetail
      combo={COMBO}
      busy={busy}
      onRunBacktest={onRunBacktest}
      onView={onView}
      onEdit={onEdit}
      onDelete={onDelete}
    />,
  );
  return { onRunBacktest, onView, onEdit, onDelete };
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

  it("fires the right callback for each action button", () => {
    const { onRunBacktest, onView, onEdit, onDelete } = renderDetail();
    fireEvent.click(screen.getByText("运行回测"));
    expect(onRunBacktest).toHaveBeenCalledWith(COMBO);
    fireEvent.click(screen.getByText("查看详情"));
    expect(onView).toHaveBeenCalledWith(COMBO);
    fireEvent.click(screen.getByText("编辑"));
    expect(onEdit).toHaveBeenCalledWith(COMBO);
    fireEvent.click(screen.getByText("删除"));
    expect(onDelete).toHaveBeenCalledWith(COMBO);
  });

  it("disables the action buttons while busy", () => {
    renderDetail(true);
    expect((screen.getByText("运行中...") as HTMLButtonElement).disabled).toBe(true);
  });
});
