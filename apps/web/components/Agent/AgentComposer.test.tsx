import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import AgentComposer from "./AgentComposer";

afterEach(cleanup);
it("只有发送/停止入口，Enter发送、Shift+Enter保留换行", () => {
  const send = vi.fn();
  const stop = vi.fn();
  const props = { value: "问题", onChange: vi.fn(), onSend: send,
    onStop: stop, running: false };
  const { rerender } = render(<AgentComposer {...props} />);
  expect(screen.queryByRole("button", { name: /清/ })).toBeNull();
  fireEvent.keyDown(screen.getByRole("textbox"), { key: "Enter", shiftKey: true });
  expect(send).not.toHaveBeenCalled();
  fireEvent.keyDown(screen.getByRole("textbox"), { key: "Enter" });
  expect(send).toHaveBeenCalledOnce();
  fireEvent.click(screen.getByRole("button", { name: "发送" }));
  expect(send).toHaveBeenCalledTimes(2);
  rerender(<AgentComposer {...props} running disabled />);
  expect(screen.getByRole("button", { name: "停止" })).toBeEnabled();
  fireEvent.click(screen.getByRole("button", { name: "停止" }));
  expect(stop).toHaveBeenCalledOnce();
});
