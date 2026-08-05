import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import ExpressionTree, { parseExprTree } from "./ExpressionTree";

afterEach(cleanup);

describe("parseExprTree", () => {
  it("解析嵌套表达式并把窗口整数归到父节点", () => {
    const tree = parseExprTree("sub(ts_mean(overnight,20),delta(amplitude,5))")!;
    expect(tree.label).toBe("sub");
    expect(tree.children).toHaveLength(2);
    expect(tree.children[0].label).toBe("ts_mean");
    expect(tree.children[0].window).toBe(20);
    expect(tree.children[0].children[0].label).toBe("overnight");
    expect(tree.children[1].window).toBe(5);
  });

  it("解析裸字段", () => {
    expect(parseExprTree("close")).toEqual({ label: "close", window: null, children: [] });
  });

  it("深度 5 的表达式可解析", () => {
    const deep = "ts_rank(div(sub(close,ts_delay(typical,180)),ts_std(body,240)),90)";
    const tree = parseExprTree(deep)!;
    let depth = 0;
    let node = tree;
    while (node.children.length) {
      depth += 1;
      node = node.children[0];
    }
    expect(depth).toBeGreaterThanOrEqual(3);
  });

  it("非法输入返回 null（由调用方回落等宽文本，绝不崩）", () => {
    expect(parseExprTree("")).toBeNull();
    expect(parseExprTree("sub(ret")).toBeNull();
    expect(parseExprTree("sub(ret,ret) trailing")).toBeNull();
  });
});

describe("ExpressionTree — 自绘 SVG", () => {
  it("渲染出带无障碍名的 SVG，节点不重叠", () => {
    const expr = "sub(ts_mean(overnight,20),delta(amplitude,5))";
    const { container } = render(<ExpressionTree expr={expr} />);
    expect(screen.getByRole("img", { name: `表达式结构：${expr}` })).toBeTruthy();
    const rects = Array.from(container.querySelectorAll("rect"));
    expect(rects.length).toBeGreaterThanOrEqual(5);
    // 同层节点横向不重叠：叶子按顺序铺开，父节点居中于子区间。
    const leaves = rects
      .map((rect) => ({
        x: Number(rect.getAttribute("x")),
        y: Number(rect.getAttribute("y")),
      }))
      .filter((item) => item.y === Math.max(...rects.map((r) => Number(r.getAttribute("y")))));
    const xs = leaves.map((item) => item.x).sort((a, b) => a - b);
    for (let i = 1; i < xs.length; i += 1) {
      expect(xs[i] - xs[i - 1]).toBeGreaterThan(0);
    }
  });

  it("标注窗口参数", () => {
    render(<ExpressionTree expr="ts_mean(overnight,20)" />);
    expect(screen.getByText("窗口 20")).toBeTruthy();
  });

  it("解析失败时优雅回落为等宽表达式文本（不抛、不空白）", () => {
    render(<ExpressionTree expr="not a valid expr(" />);
    expect(screen.getByText("not a valid expr(")).toBeTruthy();
  });

  it("空表达式显示占位符", () => {
    render(<ExpressionTree expr="" />);
    expect(screen.getByText("—")).toBeTruthy();
  });
});
