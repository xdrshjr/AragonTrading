"use client";

import { useMemo } from "react";

/**
 * 表达式树自绘 SVG（strategy-factor-discovery §2.13）。
 *
 * 解析后端的前缀式规范式（如 `sub(ts_mean(overnight,20),delta(amplitude,5))`）并按层布局，
 * 节点上标注算子 / 字段与窗口。纯前端解析：只为**可视化**，不参与任何计算——真正的语法与量纲
 * 真源在后端 `discovery/expression.py`，前端解析失败一律优雅回落为等宽表达式文本。
 */
interface TreeNode {
  label: string;
  window: number | null;
  children: TreeNode[];
}

const NODE_W = 116;
const NODE_H = 30;
const GAP_X = 12;
const GAP_Y = 44;

/** 极简前缀式解析（只认 `name(...)` 与逗号分隔；失败返回 null 由调用方回落）。 */
export function parseExprTree(text: string): TreeNode | null {
  const source = (text || "").trim();
  if (!source) return null;
  let pos = 0;

  function parseNode(): TreeNode | null {
    const start = pos;
    while (pos < source.length && /[A-Za-z0-9_.\-]/.test(source[pos])) pos += 1;
    const name = source.slice(start, pos);
    if (!name) return null;
    if (source[pos] !== "(") return { label: name, window: null, children: [] };
    pos += 1;
    const args: TreeNode[] = [];
    const literals: number[] = [];
    while (pos < source.length && source[pos] !== ")") {
      if (source[pos] === ",") {
        pos += 1;
        continue;
      }
      const child = parseNode();
      if (!child) return null;
      if (child.children.length === 0 && /^-?\d+$/.test(child.label)) {
        literals.push(Number(child.label));
      } else {
        args.push(child);
      }
    }
    if (source[pos] !== ")") return null;
    pos += 1;
    return { label: name, window: literals.length ? literals[literals.length - 1] : null, children: args };
  }

  const root = parseNode();
  return root && pos === source.length ? root : null;
}

interface Placed {
  node: TreeNode;
  x: number;
  y: number;
}

/** 自底向上布局：叶子按顺序铺开，父节点居中于其子节点区间（保证同层不重叠）。 */
function layout(root: TreeNode): { placed: Placed[]; edges: [Placed, Placed][]; width: number; height: number } {
  const placed: Placed[] = [];
  const edges: [Placed, Placed][] = [];
  let nextLeafX = 0;

  function walk(node: TreeNode, depth: number): Placed {
    if (node.children.length === 0) {
      const item = { node, x: nextLeafX * (NODE_W + GAP_X), y: depth * GAP_Y };
      nextLeafX += 1;
      placed.push(item);
      return item;
    }
    const kids = node.children.map((child) => walk(child, depth + 1));
    const x = (kids[0].x + kids[kids.length - 1].x) / 2;
    const item = { node, x, y: depth * GAP_Y };
    placed.push(item);
    kids.forEach((kid) => edges.push([item, kid]));
    return item;
  }

  walk(root, 0);
  const width = Math.max(NODE_W, nextLeafX * (NODE_W + GAP_X) - GAP_X);
  const height = Math.max(...placed.map((item) => item.y)) + NODE_H;
  return { placed, edges, width, height };
}

export default function ExpressionTree({ expr }: { expr: string }) {
  const tree = useMemo(() => parseExprTree(expr), [expr]);
  const model = useMemo(() => (tree ? layout(tree) : null), [tree]);

  if (!tree || !model) {
    return (
      <pre className="overflow-x-auto rounded-[10px] bg-[var(--surface-2)] px-3 py-2 font-mono text-[11px] text-[var(--text)]">
        {expr || "—"}
      </pre>
    );
  }

  return (
    <div className="overflow-x-auto">
      <svg
        width={model.width + 4}
        height={model.height + 4}
        viewBox={`-2 -2 ${model.width + 4} ${model.height + 4}`}
        role="img"
        aria-label={`表达式结构：${expr}`}
      >
        {model.edges.map(([parent, child], index) => (
          <path
            key={index}
            d={`M ${parent.x + NODE_W / 2} ${parent.y + NODE_H} V ${(parent.y + NODE_H + child.y) / 2} H ${
              child.x + NODE_W / 2
            } V ${child.y}`}
            fill="none"
            stroke="var(--border)"
            strokeWidth="1.2"
          />
        ))}
        {model.placed.map((item, index) => {
          const isLeaf = item.node.children.length === 0;
          return (
            <g key={index}>
              <rect
                x={item.x}
                y={item.y}
                width={NODE_W}
                height={NODE_H}
                rx="7"
                fill={isLeaf ? "var(--surface-2)" : "var(--accent-soft)"}
                stroke="var(--border)"
                strokeWidth="1"
              />
              <text
                x={item.x + NODE_W / 2}
                y={item.y + (item.node.window === null ? 19 : 14)}
                textAnchor="middle"
                fontSize="11"
                fill={isLeaf ? "var(--text)" : "var(--accent)"}
              >
                {item.node.label}
              </text>
              {item.node.window !== null && (
                <text
                  x={item.x + NODE_W / 2}
                  y={item.y + 25}
                  textAnchor="middle"
                  fontSize="10"
                  fill="var(--muted)"
                >
                  窗口 {item.node.window}
                </text>
              )}
            </g>
          );
        })}
      </svg>
    </div>
  );
}
