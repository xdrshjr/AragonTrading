import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import CompareSelectionBar from "./CompareSelectionBar";

function setup(overrides: Partial<Parameters<typeof CompareSelectionBar>[0]> = {}) {
  const props = {
    filter: "all" as const,
    onFilter: vi.fn(),
    selectedCount: 3,
    maxSelections: 12,
    minSelections: 2,
    hasResult: false,
    running: false,
    onSelectAll: vi.fn(),
    onInvert: vi.fn(),
    onClear: vi.fn(),
    onRun: vi.fn(),
    onClearResult: vi.fn(),
    ...overrides,
  };
  render(<CompareSelectionBar {...props} />);
  return props;
}

describe("CompareSelectionBar", () => {
  afterEach(cleanup);

  it("renders the selected count", () => {
    setup({ selectedCount: 3 });
    expect(screen.getByText("已选 3/12")).toBeTruthy();
  });

  it("shows a below-minimum hint when fewer than the minimum are selected", () => {
    setup({ selectedCount: 1 });
    expect(screen.getByText(/至少 2 个/)).toBeTruthy();
  });

  it("shows an at-limit hint when the maximum is reached", () => {
    setup({ selectedCount: 12 });
    expect(screen.getByText(/已达上限/)).toBeTruthy();
  });

  it("disables run below the minimum and enables / triggers it above the minimum", () => {
    setup({ selectedCount: 1 });
    expect((screen.getByRole("button", { name: /运行对比/ }) as HTMLButtonElement).disabled).toBe(true);
    cleanup();
    const p = setup({ selectedCount: 3 });
    const run = screen.getByRole("button", { name: /运行对比/ }) as HTMLButtonElement;
    expect(run.disabled).toBe(false);
    fireEvent.click(run);
    expect(p.onRun).toHaveBeenCalled();
  });

  it("disables run and shows progress label while running", () => {
    setup({ selectedCount: 3, running: true });
    expect((screen.getByRole("button", { name: "对比中…" }) as HTMLButtonElement).disabled).toBe(true);
  });

  it("disables run above the maximum", () => {
    setup({ selectedCount: 13 });
    expect((screen.getByRole("button", { name: /运行对比/ }) as HTMLButtonElement).disabled).toBe(true);
  });

  it("fires the selection callbacks", () => {
    const p = setup({ selectedCount: 3 });
    fireEvent.click(screen.getByText("全选"));
    fireEvent.click(screen.getByText("反选"));
    fireEvent.click(screen.getByText("清空"));
    expect(p.onSelectAll).toHaveBeenCalled();
    expect(p.onInvert).toHaveBeenCalled();
    expect(p.onClear).toHaveBeenCalled();
  });

  it("only renders clear-result when there is a result", () => {
    setup({ hasResult: false });
    expect(screen.queryByText("清空结果")).toBeNull();
    cleanup();
    const p = setup({ hasResult: true });
    fireEvent.click(screen.getByText("清空结果"));
    expect(p.onClearResult).toHaveBeenCalled();
  });

  it("fires the filter callback", () => {
    const p = setup();
    fireEvent.click(screen.getByText("组合"));
    expect(p.onFilter).toHaveBeenCalledWith("combo");
  });
});
