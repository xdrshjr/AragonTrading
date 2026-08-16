import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { Tabs } from "./index";

afterEach(cleanup);

/**
 * 共享 `Tabs` 原语的页签语义与键盘漫游（spec D8）。
 *
 * 它被 11 处调用——RightPanel 主页签、ChartHeaderBar 视图段控、LiveBottomPanel、InstrumentSidebar……
 * 几乎是整个应用的主导航——却渲染的是一排裸 `<button>`：读屏用户听不到「第 2 项，共 4 项，已选中」，
 * 键盘用户在主页签之间移动要按 Tab 逐个穿过。反讽的是同仓库三处**手写**页签都做对了。
 */
const TABS = [
  { key: "a", label: "甲" },
  { key: "b", label: "乙" },
  { key: "c", label: "丙" },
];

function renderTabs(variant: "segmented" | "underline" = "segmented", active = "a") {
  const onChange = vi.fn();
  render(<Tabs tabs={TABS} active={active} onChange={onChange} variant={variant} label="测试页签" />);
  return { onChange };
}

describe.each(["segmented", "underline"] as const)("ui — Tabs（%s 变体）", (variant) => {
  it("外层是 tablist、每项是 tab，且 aria-selected 只在激活项为真", () => {
    renderTabs(variant, "b");
    const list = screen.getByRole("tablist", { name: "测试页签" });
    expect(list).toBeTruthy();
    const tabs = screen.getAllByRole("tab");
    expect(tabs).toHaveLength(3);
    expect(tabs.map((t) => t.getAttribute("aria-selected"))).toEqual(["false", "true", "false"]);
  });

  it("roving tabindex：只有激活项进 Tab 序列", () => {
    renderTabs(variant, "b");
    expect(screen.getAllByRole("tab").map((t) => t.tabIndex)).toEqual([-1, 0, -1]);
  });

  it("每个 tab 都是 type=button（避免落在表单里时误提交）", () => {
    renderTabs(variant);
    for (const tab of screen.getAllByRole("tab")) {
      expect(tab.getAttribute("type")).toBe("button");
    }
  });

  it("ArrowRight 前进一项并把焦点移过去", () => {
    const { onChange } = renderTabs(variant, "a");
    fireEvent.keyDown(screen.getByRole("tablist"), { key: "ArrowRight" });
    expect(onChange).toHaveBeenCalledWith("b");
    expect(document.activeElement).toBe(screen.getByRole("tab", { name: "乙" }));
  });

  it("ArrowRight 在末项环回首项、ArrowLeft 在首项环到末项", () => {
    const last = renderTabs(variant, "c");
    fireEvent.keyDown(screen.getByRole("tablist"), { key: "ArrowRight" });
    expect(last.onChange).toHaveBeenCalledWith("a");
    cleanup();

    const first = renderTabs(variant, "a");
    fireEvent.keyDown(screen.getByRole("tablist"), { key: "ArrowLeft" });
    expect(first.onChange).toHaveBeenCalledWith("c");
  });

  it("Home / End 跳首尾", () => {
    const end = renderTabs(variant, "a");
    fireEvent.keyDown(screen.getByRole("tablist"), { key: "End" });
    expect(end.onChange).toHaveBeenCalledWith("c");
    cleanup();

    const home = renderTabs(variant, "c");
    fireEvent.keyDown(screen.getByRole("tablist"), { key: "Home" });
    expect(home.onChange).toHaveBeenCalledWith("a");
  });

  it("非导航键不触发切换（别把 Enter/空格从按钮手里抢走）", () => {
    const { onChange } = renderTabs(variant, "a");
    fireEvent.keyDown(screen.getByRole("tablist"), { key: "ArrowDown" });
    fireEvent.keyDown(screen.getByRole("tablist"), { key: "Enter" });
    expect(onChange).not.toHaveBeenCalled();
  });

  it("点击照旧切换（键盘支持是加法，不改鼠标语义）", () => {
    const { onChange } = renderTabs(variant, "a");
    fireEvent.click(screen.getByRole("tab", { name: "丙" }));
    expect(onChange).toHaveBeenCalledWith("c");
  });
});

describe("ui — Tabs 不传 label 时不渲染 aria-label", () => {
  it("调用方不传即无该属性（11 个既有调用方零改动）", () => {
    render(<Tabs tabs={TABS} active="a" onChange={vi.fn()} />);
    expect(screen.getByRole("tablist").hasAttribute("aria-label")).toBe(false);
  });
});
