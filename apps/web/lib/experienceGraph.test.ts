import { describe, expect, it } from "vitest";
import { LAYOUT_SIZE, clampScale, layoutGraph, neighboursOf, viewBoxOf } from "./experienceGraph";
import type { ExperienceEdge, ExperienceNode } from "./experienceTypes";

function node(id: number, over: Partial<ExperienceNode> = {}): ExperienceNode {
  return {
    id,
    kind: "lesson",
    key: `lesson:${id}`,
    axis: null,
    value: null,
    label: `教训 ${id}`,
    detail: "",
    source: "stat",
    scope: "symbol",
    tier: "normal",
    pinned: false,
    muted: false,
    muteReason: null,
    support: 20,
    weight: 0.8,
    stats: {},
    signature: {},
    firstTs: 0,
    lastSeenTs: 0,
    signStreak: 0,
    createdTs: 0,
    updatedTs: 0,
    ...over,
  };
}

function context(id: number, axis: string, value: string): ExperienceNode {
  return node(id, { kind: "context", key: `${axis}:${value}`, axis, value, source: "context" });
}

function edge(id: number, srcId: number, dstId: number,
              rel: ExperienceEdge["rel"] = "applies_in"): ExperienceEdge {
  return { id, srcId, dstId, rel, weight: 1, support: 5, updatedTs: 0 };
}

const NODES: ExperienceNode[] = [
  context(1, "symbol", "BTC-USDT-SWAP"),
  context(2, "volatility", "high_volatility"),
  context(3, "regime", "range"),
  node(4, { key: "lesson:a", tier: "iron", signature: { symbol: "BTC-USDT-SWAP" } }),
  node(5, { key: "lesson:b", tier: "candidate", support: 3 }),
  node(6, { key: "lesson:c", tier: "normal", muted: true }),
];

const EDGES: ExperienceEdge[] = [
  edge(1, 4, 1), edge(2, 4, 2), edge(3, 5, 2), edge(4, 6, 3),
  edge(5, 2, 3, "co_occurs"), edge(6, 4, 6, "contradicts"),
];

/** 打乱顺序但**不改内容**（A-20 的核心：后端回执的顺序不是契约）。 */
function shuffled<T>(rows: T[]): T[] {
  return [...rows].reverse();
}

describe("上下文图的确定性布局（A-20）", () => {
  it("同一份数据两次布局坐标逐值相等", () => {
    const first = layoutGraph(NODES, EDGES);
    const second = layoutGraph(NODES, EDGES);
    expect(second.nodes.map((n) => [n.node.key, n.x, n.y, n.r]))
      .toEqual(first.nodes.map((n) => [n.node.key, n.x, n.y, n.r]));
  });

  it("入参顺序打乱后坐标仍然逐值相等", () => {
    // 这条才是 A-20 真正要守的：后端回执的顺序不是契约，一次无关的排序改动就不该让整张图
    // 重新排布。力导向布局对入参顺序敏感，正是本轮改用确定性放射布局的理由（评审 P2-8）。
    const base = layoutGraph(NODES, EDGES);
    const jumbled = layoutGraph(shuffled(NODES), shuffled(EDGES));
    const key = (row: { node: ExperienceNode; x: number; y: number }) =>
      [row.node.key, row.x, row.y] as const;
    expect([...jumbled.nodes].map(key).sort()).toEqual([...base.nodes].map(key).sort());
  });

  it("经验节点挂在它 applies_in 到的语境的方向上，绝不堆在圆心", () => {
    const layout = layoutGraph(NODES, EDGES);
    const centre = LAYOUT_SIZE / 2;
    for (const row of layout.nodes) {
      const distance = Math.hypot(row.x - centre, row.y - centre);
      expect(distance).toBeGreaterThan(0);
    }
  });

  it("半径 ∝ √support 且被夹在可读区间内（一条 3000 样本的经验不该吃掉半屏）", () => {
    const big = layoutGraph([...NODES, node(7, { key: "lesson:big", support: 100000 })],
                            [...EDGES, edge(7, 7, 1)]);
    const radii = big.nodes.filter((n) => n.node.kind === "lesson").map((n) => n.r);
    expect(Math.max(...radii)).toBeLessThanOrEqual(16);
    expect(Math.min(...radii)).toBeGreaterThanOrEqual(4);
  });

  it("悬空边被丢弃（绝不画一条指向虚空的线）", () => {
    const layout = layoutGraph(NODES, [...EDGES, edge(99, 4, 4242)]);
    expect(layout.edges.every((e) => e.edge.dstId !== 4242)).toBe(true);
  });

  it("一跳邻域含自身，且无向可达", () => {
    const layout = layoutGraph(NODES, EDGES);
    const around = neighboursOf(layout, 4);
    expect(around.has(4)).toBe(true);
    expect(around.has(1)).toBe(true);      // applies_in 的另一端
    expect(around.has(6)).toBe(true);      // contradicts 的另一端
    expect(neighboursOf(layout, null).size).toBe(0);
  });

  it("空图不抛（冷启动时用户看到的是引导态，不是一个崩掉的面板）", () => {
    const layout = layoutGraph([], []);
    expect(layout.nodes).toEqual([]);
    expect(layout.edges).toEqual([]);
  });
});

describe("视口变换", () => {
  it("缩放夹在 [0.4, 3]（与 §7.1 写死的交互契约一致）", () => {
    expect(clampScale(0.01)).toBe(0.4);
    expect(clampScale(99)).toBe(3);
    expect(clampScale(1.5)).toBe(1.5);
  });

  it("viewBox 随缩放收缩、随平移移动", () => {
    const [, , w1] = viewBoxOf(1, 0, 0).split(" ").map(Number);
    const [, , w2] = viewBoxOf(2, 0, 0).split(" ").map(Number);
    expect(w2).toBeLessThan(w1);
    const [x0] = viewBoxOf(1, 0, 0).split(" ").map(Number);
    const [x1] = viewBoxOf(1, 120, 0).split(" ").map(Number);
    expect(x1 - x0).toBe(120);
  });
});
