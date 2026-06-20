import { afterEach, describe, expect, it, vi } from "vitest";
import { getBridge, getDesktopPlatform, getWindowControls } from "./desktop";
import type { WindowControls } from "./desktop";

function fakeControls(): WindowControls {
  return {
    minimize: () => {},
    toggleMaximize: () => {},
    close: () => {},
    isMaximized: () => Promise.resolve(false),
    onMaximizeChange: () => () => {},
  };
}

describe("lib/desktop — 桌面桥探测", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    delete (window as { aragon?: unknown }).aragon;
  });

  it("浏览器（无 window.aragon）时 getWindowControls / getDesktopPlatform 返回 null", () => {
    expect(getWindowControls()).toBeNull();
    expect(getDesktopPlatform()).toBeNull();
  });

  it("注入 window.aragon 后返回该窗口控制句柄与平台", () => {
    const controls = fakeControls();
    (window as { aragon?: unknown }).aragon = { platform: "win32", windowControls: controls };
    expect(getWindowControls()).toBe(controls);
    expect(getDesktopPlatform()).toBe("win32");
  });

  it("SSR（window 未定义）时 getBridge 返回 null 且不抛错", () => {
    vi.stubGlobal("window", undefined);
    expect(getBridge()).toBeNull();
  });
});
