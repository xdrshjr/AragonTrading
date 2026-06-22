import { cleanup, fireEvent, render, screen } from "@testing-library/react";
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
