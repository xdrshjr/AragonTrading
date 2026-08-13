import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import WorkspaceError from "./error";

afterEach(cleanup);

beforeEach(() => {
  vi.spyOn(console, "error").mockImplementation(() => {});
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("workspace error boundary", () => {
  // 事故回归：图表库异常曾把整棵树换成一片空白。边界的最低要求是「页面上还有字、还有出路」。
  it("keeps a readable screen with a way out instead of a blank page", () => {
    const reset = vi.fn();
    const error = Object.assign(new Error("Cannot read properties of undefined (reading 'year')"), {
      digest: "abc123",
    });
    render(<WorkspaceError error={error} reset={reset} />);

    expect(screen.getByText("工作台渲染失败")).toBeTruthy();
    expect(screen.getByText(/reading 'year'/)).toBeTruthy();
    expect(screen.getByText(/abc123/)).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "重试" }));
    expect(reset).toHaveBeenCalledTimes(1);
  });

  it("still renders when the error carries neither message nor digest", () => {
    render(<WorkspaceError error={new Error("")} reset={vi.fn()} />);
    expect(screen.getByText("未知错误")).toBeTruthy();
    expect(screen.getByRole("button", { name: "重试" })).toBeTruthy();
  });
});
