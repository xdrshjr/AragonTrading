import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import ConfirmDialog from "./ConfirmDialog";

afterEach(cleanup);

/**
 * 破坏性确认框的**初始焦点契约**（spec D6）。
 *
 * 这个契约此前一条用例都没有：源码给确认（危险）按钮写了 `autoFocus`，而外层 `Modal` 的挂载
 * effect 又会 focus DOM 里第一个可聚焦元素——最终落点取决于「autoFocus 在 layout 阶段执行、
 * 父组件 passive effect 在其后执行」这个实现细节。今天恰好是安全的（取消键在 DOM 里排在前面），
 * 但任何一次调用方重排（把确认键写在前面、在 message 里插一个链接、给标题加一个按钮）都会让
 * **回车键的落点从「取消」变成「永久删除」**，而没有任何东西会变红。ConfirmDialog 当前守着
 * 6+ 处不可撤销的删除。
 */
function renderDialog(over: Partial<React.ComponentProps<typeof ConfirmDialog>> = {}) {
  const onConfirm = vi.fn();
  const onCancel = vi.fn();
  render(
    <ConfirmDialog
      open
      title="删除因子组合"
      message="确定删除组合「组合A」？"
      onConfirm={onConfirm}
      onCancel={onCancel}
      {...over}
    />,
  );
  return { onConfirm, onCancel };
}

describe("ui — ConfirmDialog 初始焦点与出口", () => {
  it("初始焦点落在「取消」上（默认动作绝不能是不可逆动作）", () => {
    renderDialog();
    expect(document.activeElement).toBe(screen.getByRole("button", { name: "取消" }));
  });

  it("danger=false 时初始焦点仍在「取消」（「是否」弹窗的默认答案就该是「否」）", () => {
    renderDialog({ danger: false });
    expect(document.activeElement).toBe(screen.getByRole("button", { name: "取消" }));
  });

  it("自定义按钮文案时焦点仍钉在取消键上", () => {
    renderDialog({ cancelText: "再想想", confirmText: "永久删除" });
    expect(document.activeElement).toBe(screen.getByRole("button", { name: "再想想" }));
  });

  it("Esc 走 onCancel，不走 onConfirm", () => {
    const { onConfirm, onCancel } = renderDialog();
    fireEvent.keyDown(window, { key: "Escape" });
    expect(onCancel).toHaveBeenCalledTimes(1);
    expect(onConfirm).not.toHaveBeenCalled();
  });

  it("点确定走 onConfirm，点取消走 onCancel", () => {
    const { onConfirm, onCancel } = renderDialog();
    fireEvent.click(screen.getByRole("button", { name: "确定" }));
    expect(onConfirm).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByRole("button", { name: "取消" }));
    expect(onCancel).toHaveBeenCalledTimes(1);
  });

  it("open=false 时什么都不渲染", () => {
    renderDialog({ open: false });
    expect(screen.queryByRole("dialog")).toBeNull();
  });
});
