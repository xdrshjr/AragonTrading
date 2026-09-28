import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import RailIconButton from "./RailIconButton";

afterEach(cleanup);

it("提示通过portal显示，焦点与hover合并，点击和Esc保持关闭", () => {
  const click = vi.fn();
  const focus = vi.fn();
  const { container } = render(
    <RailIconButton label="交易" onClick={click} onFocus={focus}>图</RailIconButton>,
  );
  const button = screen.getByRole("button", { name: "交易" });
  expect(button).toHaveClass("h-11", "w-11", "shrink-0");
  expect(screen.queryByText("交易")).toBeNull();
  fireEvent.mouseEnter(button);
  expect(container.contains(screen.getByText("交易"))).toBe(false);
  expect(screen.getByText("交易")).toHaveAttribute("aria-hidden", "true");
  fireEvent.focus(button);
  fireEvent.mouseLeave(button);
  expect(screen.getByText("交易")).toBeInTheDocument();
  fireEvent.click(button);
  expect(click).toHaveBeenCalledOnce();
  expect(focus).toHaveBeenCalledOnce();
  expect(screen.queryByText("交易")).toBeNull();
  fireEvent.mouseEnter(button);
  fireEvent.keyDown(window, { key: "Escape" });
  expect(screen.queryByText("交易")).toBeNull();
});

it("滚动、resize、弹窗展开和卸载清除提示", () => {
  const { rerender, unmount } = render(<RailIconButton label="账户" />);
  const button = screen.getByRole("button");
  fireEvent.focus(button);
  fireEvent.scroll(window);
  expect(screen.queryByText("账户")).toBeNull();
  fireEvent.mouseEnter(button);
  fireEvent.resize(window);
  expect(screen.queryByText("账户")).toBeNull();
  fireEvent.mouseEnter(button);
  rerender(<RailIconButton label="账户" aria-expanded />);
  expect(screen.queryByText("账户")).toBeNull();
  rerender(<RailIconButton label="账户" />);
  fireEvent.focus(button);
  unmount();
  expect(screen.queryByText("账户")).toBeNull();
});
