import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import PaneSplitHandle from "./PaneSplitHandle";

// jsdom (25.x) 未实现 Pointer 捕获 API，也无 PointerEvent 构造器：以空桩补齐捕获方法，
// 并用同名 MouseEvent 派发 pointer 事件（React 把原生 pointermove 包成 SyntheticPointerEvent，
// 直接读取 clientY），避免依赖运行环境缺失的 PointerEvent。
beforeAll(() => {
  if (!HTMLElement.prototype.setPointerCapture) {
    HTMLElement.prototype.setPointerCapture = vi.fn();
    HTMLElement.prototype.releasePointerCapture = vi.fn();
    HTMLElement.prototype.hasPointerCapture = vi.fn(() => false);
  }
});

afterEach(cleanup);

function firePointer(el: Element, type: string, init: MouseEventInit = {}) {
  el.dispatchEvent(new MouseEvent(type, { bubbles: true, cancelable: true, ...init }));
}

describe("ui — PaneSplitHandle", () => {
  it("renders a horizontal separator carrying the aria label", () => {
    render(<PaneSplitHandle onResize={vi.fn()} ariaLabel="拖动调整主图与副图高度" />);
    const handle = screen.getByRole("separator");
    expect(handle.getAttribute("aria-orientation")).toBe("horizontal");
    expect(handle.getAttribute("aria-label")).toBe("拖动调整主图与副图高度");
  });

  it("reports clientY through onResize while dragging", () => {
    const onResize = vi.fn();
    render(<PaneSplitHandle onResize={onResize} ariaLabel="x" />);
    const handle = screen.getByRole("separator");
    firePointer(handle, "pointerdown");
    firePointer(handle, "pointermove", { clientY: 240 });
    expect(onResize).toHaveBeenCalledWith(240);
  });

  it("ignores moves that happen before pointerdown", () => {
    const onResize = vi.fn();
    render(<PaneSplitHandle onResize={onResize} ariaLabel="x" />);
    firePointer(screen.getByRole("separator"), "pointermove", { clientY: 100 });
    expect(onResize).not.toHaveBeenCalled();
  });

  it("stops reporting after pointerup releases the drag", () => {
    const onResize = vi.fn();
    render(<PaneSplitHandle onResize={onResize} ariaLabel="x" />);
    const handle = screen.getByRole("separator");
    firePointer(handle, "pointerdown");
    firePointer(handle, "pointerup");
    firePointer(handle, "pointermove", { clientY: 300 });
    expect(onResize).not.toHaveBeenCalled();
  });

  it("calls onReset on double click", () => {
    const onReset = vi.fn();
    render(<PaneSplitHandle onResize={vi.fn()} onReset={onReset} ariaLabel="x" />);
    firePointer(screen.getByRole("separator"), "dblclick");
    expect(onReset).toHaveBeenCalledTimes(1);
  });
});
