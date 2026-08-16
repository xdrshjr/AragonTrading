import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { useRef } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { Modal } from "./index";
import ConfirmDialog from "./ConfirmDialog";

afterEach(cleanup);

function renderModal(onClose = vi.fn(), open = true) {
  const utils = render(
    <Modal open={open} onClose={onClose} labelledBy="m-title">
      <h2 id="m-title">详情</h2>
      <button type="button">关闭</button>
    </Modal>,
  );
  return { onClose, ...utils };
}

describe("ui — Modal primitive", () => {
  it("renders a labelled dialog with aria-modal when open", () => {
    renderModal();
    const dialog = screen.getByRole("dialog");
    expect(dialog.getAttribute("aria-modal")).toBe("true");
    expect(dialog.getAttribute("aria-labelledby")).toBe("m-title");
  });

  it("renders nothing when closed", () => {
    renderModal(vi.fn(), false);
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("moves initial focus into the dialog on mount", () => {
    renderModal();
    const dialog = screen.getByRole("dialog");
    expect(dialog.contains(document.activeElement)).toBe(true);
  });

  it("calls onClose on Escape", () => {
    const { onClose } = renderModal();
    fireEvent.keyDown(window, { key: "Escape" });
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("calls onClose when clicking the overlay (not the dialog body)", () => {
    const { onClose } = renderModal();
    const dialog = screen.getByRole("dialog");
    const overlay = dialog.parentElement as HTMLElement;
    fireEvent.click(overlay);
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("does not call onClose when clicking inside the dialog", () => {
    const { onClose } = renderModal();
    fireEvent.click(screen.getByRole("dialog"));
    expect(onClose).not.toHaveBeenCalled();
  });

  it("locks body scroll while open and restores it on unmount", () => {
    const { unmount } = renderModal();
    expect(document.body.style.overflow).toBe("hidden");
    unmount();
    expect(document.body.style.overflow).not.toBe("hidden");
  });
});

// `initialFocusRef`（spec D6）：显式指定初始焦点落点。这是 `ConfirmDialog` 把回车键钉在
// 「取消」上的机制；默认行为必须逐字不变，否则 11 个既有调用方会被一次性改掉焦点语义。
describe("ui — Modal initialFocusRef", () => {
  function TwoButtonModal({ useRefTarget }: { useRefTarget: boolean }) {
    const second = useRef<HTMLButtonElement>(null);
    return (
      <Modal
        open
        onClose={() => {}}
        labelledBy="m-title"
        initialFocusRef={useRefTarget ? second : undefined}
      >
        <h2 id="m-title">详情</h2>
        <button type="button">第一个</button>
        <button ref={second} type="button">
          第二个
        </button>
      </Modal>
    );
  }

  it("focuses the referenced element instead of the first focusable one", () => {
    render(<TwoButtonModal useRefTarget />);
    expect(document.activeElement).toBe(screen.getByRole("button", { name: "第二个" }));
  });

  it("keeps the legacy behaviour (first focusable) when the prop is omitted", () => {
    render(<TwoButtonModal useRefTarget={false} />);
    expect(document.activeElement).toBe(screen.getByRole("button", { name: "第一个" }));
  });
});

// 嵌套弹窗的 Esc 归属（spec D4）。
//
// 每个 `Modal` 都在 `window` 上挂一份**捕获期** keydown，而 `e.stopPropagation()` 按 DOM 规范
// 只阻止事件传到**其它节点**，不阻止**同一节点上的其它监听器**。两个 Modal 同开时，先注册的那个
// 先执行，于是 Esc 先关掉它：用户想「退出这个删除确认」，结果整个标的组管理器一起消失。
// 真实调用点是 `Factor/SymbolGroupModal.tsx:177`（`<ConfirmDialog>` 渲染在 `<Modal>` 内部）。
//
// ⚠️ 两条挂载次序必须**分别**验，它们的失败方向相反，只测一条会得到一条永远绿的用例：
//   · **先后打开**（真实路径：先开管理器，再点删除）——外层的监听器**先**注册，
//     所以「谁先注册谁赢」的旧行为会关掉外层。这是本缺陷真正的形状。
//   · **同时挂载**——React 的 effect 自底向上跑，内层**先**注册，旧行为反而恰好是对的。
// 判定必须是 React 树上的嵌套深度，而不是注册顺序。
describe("ui — 嵌套弹窗只有栈顶响应 Esc（D4）", () => {
  function Nested({
    onClose,
    onCancel,
    confirmOpen,
  }: {
    onClose: () => void;
    onCancel: () => void;
    confirmOpen: boolean;
  }) {
    return (
      <Modal open onClose={onClose} labelledBy="outer-title">
        <h2 id="outer-title">标的组管理</h2>
        <button type="button">外层按钮</button>
        <ConfirmDialog
          open={confirmOpen}
          title="删除标的组"
          message="此操作不可撤销。"
          onConfirm={() => {}}
          onCancel={onCancel}
        />
      </Modal>
    );
  }

  it("先开外层、再开内层：Esc 只关内层（这条是缺陷的真实形状）", () => {
    const onClose = vi.fn();
    const onCancel = vi.fn();
    const { rerender } = render(
      <Nested onClose={onClose} onCancel={onCancel} confirmOpen={false} />,
    );
    rerender(<Nested onClose={onClose} onCancel={onCancel} confirmOpen />);

    fireEvent.keyDown(window, { key: "Escape" });

    expect(onCancel).toHaveBeenCalledTimes(1);
    expect(onClose).toHaveBeenCalledTimes(0);
  });

  it("两层同时挂载：Esc 同样只关内层", () => {
    const onClose = vi.fn();
    const onCancel = vi.fn();
    render(<Nested onClose={onClose} onCancel={onCancel} confirmOpen />);

    fireEvent.keyDown(window, { key: "Escape" });

    expect(onCancel).toHaveBeenCalledTimes(1);
    expect(onClose).toHaveBeenCalledTimes(0);
  });

  // P1-4 的场景守卫：调用方普遍传内联箭头（`FactorLab.tsx:301`），父组件每重渲染一次
  // 外层 Modal 的 `onClose` 就换一次身份。入栈 effect 的依赖数组因此只写 `[open]`——写成
  // `[open, onClose]` 会让外层每次重渲染都「splice 掉自己 → 重新入栈」。
  //
  // 诚实标注射程：**采用「深度优先」判定之后，这条用例挡不住依赖数组被改坏**——重新入栈也改变
  // 不了「内层深度更大」这个事实。它守的是行为（父组件反复重渲染后 Esc 归属不漂移），
  // 而依赖数组本身的稳定性是由深度判定在结构上保证的，不再依赖任何一条用例。
  it("外层 onClose 反复换新身份后，Esc 仍然只关内层", () => {
    const onClose = vi.fn();
    const onCancel = vi.fn();
    const { rerender } = render(
      <Nested onClose={() => onClose()} onCancel={onCancel} confirmOpen={false} />,
    );
    rerender(<Nested onClose={() => onClose()} onCancel={onCancel} confirmOpen />);
    rerender(<Nested onClose={() => onClose()} onCancel={onCancel} confirmOpen />);
    rerender(<Nested onClose={() => onClose()} onCancel={onCancel} confirmOpen />);

    fireEvent.keyDown(window, { key: "Escape" });

    expect(onCancel).toHaveBeenCalledTimes(1);
    expect(onClose).toHaveBeenCalledTimes(0);
  });

  it("内层关闭后 Esc 回到外层（栈顶守卫不得把外层永久锁死）", () => {
    const onClose = vi.fn();
    const onCancel = vi.fn();
    const { rerender } = render(
      <Nested onClose={onClose} onCancel={onCancel} confirmOpen />,
    );
    rerender(<Nested onClose={onClose} onCancel={onCancel} confirmOpen={false} />);

    fireEvent.keyDown(window, { key: "Escape" });

    expect(onClose).toHaveBeenCalledTimes(1);
    expect(onCancel).toHaveBeenCalledTimes(0);
  });
});
