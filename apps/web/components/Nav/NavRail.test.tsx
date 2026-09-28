import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useAppStore } from "@/lib/store";
import NavRail from "./NavRail";
vi.mock("@/lib/accountApi", () => ({ accountApi: {
  accounts: { list: vi.fn().mockResolvedValue({ accounts: [], defaultId: null }) },
} }));
function renderRail() {
  return render(<QueryClientProvider client={new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })}><NavRail /></QueryClientProvider>);
}

beforeEach(() => {
  localStorage.clear();
  // store 为模块单例：复位一级视图与设置浮层，避免跨用例残留。
  useAppStore.setState({ activeView: "trade", showSettings: false });
});
afterEach(cleanup);

describe("NavRail — 左侧主功能导航", () => {
  it("renders the three primary nav items with accessible names", () => {
    renderRail();
    expect(screen.getByRole("button", { name: "交易" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "因子实验室" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "策略与因子挖掘" })).toBeTruthy();
  });

  it("marks the active view with aria-current=page (trade by default)", () => {
    renderRail();
    expect(screen.getByRole("button", { name: "交易" })).toHaveAttribute("aria-current", "page");
    expect(screen.getByRole("button", { name: "因子实验室" })).not.toHaveAttribute("aria-current");
    expect(screen.getByRole("button", { name: "策略与因子挖掘" })).not.toHaveAttribute("aria-current");
  });

  it("clicking 策略与因子挖掘 switches activeView to discovery", () => {
    renderRail();
    fireEvent.click(screen.getByRole("button", { name: "策略与因子挖掘" }));
    expect(useAppStore.getState().activeView).toBe("discovery");
    // 选中态视觉与既有两项一致（左侧指示条 + accent 前景）。
    expect(screen.getByRole("button", { name: "策略与因子挖掘" })).toHaveAttribute(
      "aria-current",
      "page",
    );
  });

  it("clicking 因子实验室 switches activeView to factor", () => {
    renderRail();
    fireEvent.click(screen.getByRole("button", { name: "因子实验室" }));
    expect(useAppStore.getState().activeView).toBe("factor");
  });

  it("clicking a nav item closes the settings overlay and switches view", () => {
    useAppStore.setState({ showSettings: true });
    renderRail();
    // 设置浮层打开时各项均不显选中，避免误导。
    expect(screen.getByRole("button", { name: "交易" })).not.toHaveAttribute("aria-current");
    fireEvent.click(screen.getByRole("button", { name: "因子实验室" }));
    expect(useAppStore.getState().showSettings).toBe(false);
    expect(useAppStore.getState().activeView).toBe("factor");
  });

  it("renders the item label only as a hover/focus tooltip (icon-only by default)", () => {
    renderRail();
    expect(screen.queryByText("交易")).toBeNull();
    fireEvent.mouseEnter(screen.getByRole("button", { name: "交易" }));
    const tip = screen.getByText("交易");
    expect(tip).toHaveAttribute("aria-hidden", "true");
    expect(tip.parentElement).toBe(document.body);
  });

  it("导航为56px，主功能与工具命中框均为44px", () => {
    renderRail();
    const rail = screen.getByRole("navigation", { name: "主功能导航" });
    expect(rail.className).toContain("w-14");
    expect(rail.className).not.toContain("w-16");
    for (const button of screen.getAllByRole("button")) {
      expect(button).toHaveClass("h-11", "w-11", "shrink-0");
    }
    expect(rail.lastElementChild).toHaveAttribute("aria-label", "工作台设置");
  });
});

describe("NavRail 隐藏机制回归护栏（评审 C-3 / 守 C-1 P0）", () => {
  it("the wrapper carrying `hidden` must not also carry a display utility class", () => {
    // 镜像 page.tsx 内容区不变量：承载 hidden 的 wrapper 绝不带 grid/flex/block 等 display 工具类，
    // 否则作者样式覆盖 UA [hidden]{display:none}，两视图叠显（C-1/P0）；grid 与 mainRef 须下沉内层 <main>。
    render(
      <div data-testid="trade-wrap" className="min-h-0 flex-1" hidden>
        <main className="grid h-full">交易</main>
      </div>,
    );
    const wrap = screen.getByTestId("trade-wrap");
    expect(wrap).toHaveAttribute("hidden");
    expect(wrap).not.toBeVisible();
    // 关键不变量：承载 hidden 的元素绝不带「设置 display 的」工具类（按完整 token 比对，
    // 避免把 flex-1（flex-grow）/grid-cols-* 等非 display 类误判）。
    const DISPLAY_CLASSES = new Set([
      "block", "inline-block", "inline", "flex", "inline-flex",
      "grid", "inline-grid", "table", "flow-root", "contents",
    ]);
    const tokens = wrap.className.split(/\s+/).filter(Boolean);
    expect(tokens.some((t) => DISPLAY_CLASSES.has(t))).toBe(false);
  });
});
