import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { useRef } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { Modal } from "./index";

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
