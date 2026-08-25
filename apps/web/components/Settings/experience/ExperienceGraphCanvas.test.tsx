import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import ExperienceGraphCanvas from "./ExperienceGraphCanvas";
import type { ExperienceEdge, ExperienceNode } from "@/lib/experienceTypes";

// 图谱画布（A-21）。最重要的一条：**截断必须如实呈现**。
// 静默截断读起来像「全都在这儿了」——用户会据一张不完整的图判断「Agent 学到了什么」，
// 而这个界面存在的全部理由就是让那件事可核对。

function node(id: number, over: Partial<ExperienceNode> = {}): ExperienceNode {
  return {
    id, kind: "lesson", key: `lesson:${id}`, axis: null, value: null,
    label: `教训 ${id}`, detail: "", source: "stat", scope: "symbol", tier: "normal",
    pinned: false, muted: false, muteReason: null, support: 20, weight: 0.8,
    stats: {}, signature: {}, firstTs: 0, lastSeenTs: 0, signStreak: 0,
    createdTs: 0, updatedTs: 0, ...over,
  };
}

const CONTEXT = node(1, {
  kind: "context", key: "symbol:BTC-USDT-SWAP", axis: "symbol", value: "BTC-USDT-SWAP",
  source: "context",
});
const LESSON = node(2, { key: "lesson:a", tier: "iron" });
const EDGES: ExperienceEdge[] = [
  { id: 1, srcId: 2, dstId: 1, rel: "applies_in", weight: 1, support: 5, updatedTs: 0 },
];

afterEach(cleanup);

function renderCanvas(over: Partial<React.ComponentProps<typeof ExperienceGraphCanvas>> = {}) {
  const props = {
    nodes: [CONTEXT, LESSON], edges: EDGES, total: 2, truncated: false, limit: 120,
    onRaiseLimit: vi.fn(), onOpen: vi.fn(), onFilterContext: vi.fn(), ...over,
  };
  render(<ExperienceGraphCanvas {...props} />);
  return props;
}

describe("ExperienceGraphCanvas", () => {
  it("空图 ⇒ 引导态而不是一张空白画布（用户会以为坏了）", () => {
    renderCanvas({ nodes: [], edges: [], total: 0 });
    expect(screen.getByTestId("experience-graph-empty"))
      .toHaveTextContent("完成第一次自动交易");
  });

  it("A-21 · truncated ⇒ 必须渲染「已展示前 N / 共 M」并给出提高上限的出口", () => {
    const props = renderCanvas({ truncated: true, total: 940, limit: 120 });
    const banner = screen.getByTestId("experience-graph-truncated");
    expect(banner).toHaveTextContent("前 120");
    expect(banner).toHaveTextContent("940");
    expect(banner).toHaveTextContent("这不是全部");
    fireEvent.click(screen.getByRole("button", { name: "提高上限" }));
    expect(props.onRaiseLimit).toHaveBeenCalled();
  });

  it("未截断 ⇒ 不渲染那条横幅（不制造一句无信息量的噪音）", () => {
    renderCanvas();
    expect(screen.queryByTestId("experience-graph-truncated")).toBeNull();
  });

  it("每个节点是可聚焦的 role=button，且带可读的无障碍名", () => {
    renderCanvas();
    expect(screen.getByRole("button", { name: /标的·BTC-USDT-SWAP（语境）/ })).toBeVisible();
    expect(screen.getByRole("button", { name: /教训 2（铁律）/ })).toBeVisible();
  });

  it("点击经验节点打开详情；双击语境节点加入筛选", () => {
    const props = renderCanvas();
    fireEvent.click(screen.getByRole("button", { name: /教训 2（铁律）/ }));
    expect(props.onOpen).toHaveBeenCalledWith(LESSON);
    fireEvent.doubleClick(screen.getByRole("button", { name: /标的·BTC-USDT-SWAP（语境）/ }));
    expect(props.onFilterContext).toHaveBeenCalledWith(CONTEXT);
  });

  it("键盘可达：Enter 与空格与点击等价（只有图的界面对读屏用户不可用）", () => {
    const props = renderCanvas();
    fireEvent.keyDown(screen.getByRole("button", { name: /教训 2（铁律）/ }), { key: "Enter" });
    expect(props.onOpen).toHaveBeenCalledWith(LESSON);
    fireEvent.keyDown(screen.getByRole("button", { name: /标的·BTC-USDT-SWAP（语境）/ }),
                      { key: " " });
    expect(props.onFilterContext).toHaveBeenCalledWith(CONTEXT);
  });
});
