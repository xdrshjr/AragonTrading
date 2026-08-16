import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import CrashFallback from "./CrashFallback";

// 两级错误边界共用的那张崩溃页。它的验收与 `app/error.test.tsx` 同源：
// 「页面上还有字、还有出路」——外加错误编号必须亮出来供对日志。

afterEach(cleanup);

describe("CrashFallback", () => {
  it("标题 / 正文 / 错误原文 / 错误编号四样都在页面上", () => {
    render(
      <CrashFallback
        title="应用启动失败"
        description="后端服务与已落库的数据不受影响。"
        error={{ message: "Cannot read properties of undefined (reading 'year')", digest: "abc123" }}
      />,
    );
    expect(screen.getByText("应用启动失败")).toBeTruthy();
    expect(screen.getByText("后端服务与已落库的数据不受影响。")).toBeTruthy();
    expect(screen.getByText(/reading 'year'/)).toBeTruthy();
    expect(screen.getByText(/错误编号：abc123/)).toBeTruthy();
  });

  it("既无 message 也无 digest 时仍渲染「未知错误」，绝不给一片空白", () => {
    render(<CrashFallback title="t" description="d" />);
    expect(screen.getByText("未知错误")).toBeTruthy();
  });

  it("点重试按钮触发 onRetry 恰一次，按钮文案可覆写", () => {
    const onRetry = vi.fn();
    render(<CrashFallback title="t" description="d" onRetry={onRetry} retryText="重新载入" />);
    fireEvent.click(screen.getByRole("button", { name: "重新载入" }));
    expect(onRetry).toHaveBeenCalledTimes(1);
  });

  it("没有 onRetry 就不渲染一个点了没反应的按钮", () => {
    render(<CrashFallback title="t" description="d" />);
    expect(screen.queryByRole("button")).toBeNull();
  });
});
