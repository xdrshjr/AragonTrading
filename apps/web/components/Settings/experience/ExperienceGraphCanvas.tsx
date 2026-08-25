"use client";

import { useMemo, useRef, useState } from "react";
import { Button } from "@/components/ui";
import { axisLabel, tierLabel, valueLabel } from "@/lib/experience";
import { clampScale, layoutGraph, neighboursOf, viewBoxOf } from "@/lib/experienceGraph";
import type { ExperienceEdge, ExperienceNode } from "@/lib/experienceTypes";

/** 关系 → 视觉。`contradicts` 用 `--warn`：一对互相矛盾的经验是要被看见的，不是要被藏起来的。 */
const REL_STROKE: Record<ExperienceEdge["rel"], string> = {
  applies_in: "var(--border)",
  co_occurs: "var(--muted)",
  contradicts: "var(--warn)",
  refines: "var(--accent)",
};

/** 档位 → 环色（铁律用强调色，已沉降半透明——它仍然在图上，只是不再进 prompt）。 */
function ringColor(node: ExperienceNode): string {
  if (node.muted) return "var(--muted)";
  if (node.pinned || node.tier === "iron") return "var(--accent)";
  return node.tier === "normal" ? "var(--muted)" : "var(--border)";
}

/**
 * 上下文图的 SVG 画布。
 *
 * 布局是**确定性**的且跑在 `useMemo` 里，绝不在 render 路径上算——120 节点的布局落在 render 上
 * 会让每一次筛选切换掉帧。同一份数据（无论后端回执顺序）恒得同一份坐标，故筛选切换时节点
 * **不会乱跳**（这是图可视化最常见的体验缺陷）。
 *
 * 截断**如实呈现**：后端回 `truncated:true` 时在图上明示「已按权重展示前 N / 共 M」并给一个
 * 「提高上限」按钮——静默截断读起来像「全都在这儿了」。
 */
export default function ExperienceGraphCanvas({
  nodes,
  edges,
  total,
  truncated,
  limit,
  onRaiseLimit,
  onOpen,
  onFilterContext,
}: {
  nodes: ExperienceNode[];
  edges: ExperienceEdge[];
  total: number;
  truncated: boolean;
  limit: number;
  onRaiseLimit: () => void;
  onOpen: (node: ExperienceNode) => void;
  onFilterContext: (node: ExperienceNode) => void;
}) {
  const [scale, setScale] = useState(1);
  const [pan, setPan] = useState({ x: 0, y: 0 });
  const [dragFrom, setDragFrom] = useState<{ x: number; y: number } | null>(null);
  const [activeId, setActiveId] = useState<number | null>(null);

  const layout = useMemo(() => layoutGraph(nodes, edges), [nodes, edges]);
  const highlighted = useMemo(() => neighboursOf(layout, activeId), [layout, activeId]);
  const dim = (id: number) => activeId !== null && !highlighted.has(id);

  // 键盘走查用 **roving tabindex**：整张图只占一个 Tab 停靠点，进图后用方向键在节点间移动。
  // 顺序按 `weight` 降序（而不是 SVG 里恰好的绘制顺序），使键盘与读屏用户先听到最重要的那几条。
  // 只给一个 tabindex=0 而不实现方向键，等于「除最高权重的那一个之外，其余节点键盘永远够不到」
  // ——那不是 roving tabindex，那是一个漏了一半的实现。
  const tabOrder = useMemo(
    () => [...layout.nodes].sort((a, b) => b.node.weight - a.node.weight),
    [layout.nodes],
  );
  const orderIndexOf = useMemo(() => {
    const map = new Map<number, number>();
    tabOrder.forEach((row, index) => map.set(row.node.id, index));
    return map;
  }, [tabOrder]);
  const [rovingId, setRovingId] = useState<number | null>(null);
  const nodeRefs = useRef(new Map<number, SVGGElement>());
  // 数据换了以后旧的 `rovingId` 可能已经不在图上；回落到权重最高的那一个（恒有一个可聚焦点）。
  const focusStop = rovingId !== null && orderIndexOf.has(rovingId)
    ? rovingId
    : (tabOrder[0]?.node.id ?? null);

  const moveFocus = (fromId: number, step: number) => {
    const index = orderIndexOf.get(fromId);
    if (index === undefined || tabOrder.length === 0) return;
    const next = tabOrder[(index + step + tabOrder.length) % tabOrder.length].node.id;
    setRovingId(next);
    nodeRefs.current.get(next)?.focus();
  };

  if (!layout.nodes.length) {
    return (
      <p
        className="rounded-md border border-dashed border-[var(--border)] px-4 py-10 text-center text-xs text-[var(--muted)]"
        data-testid="experience-graph-empty"
      >
        还没有经验——完成第一次自动交易并平掉一笔后，这里会出现你的第一条。
      </p>
    );
  }

  return (
    <div className="flex flex-col gap-2" data-testid="experience-graph">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-[11px] text-[var(--muted)]">
          滚轮缩放 · 拖拽平移 · 点击节点看详情 · 双击语境节点加入筛选 ·
          键盘：Tab 进入后按方向键在节点间移动（按权重降序），Enter / 空格打开
        </p>
        <div className="flex items-center gap-2">
          <span className="tabular text-[11px] text-[var(--muted)]">
            {(scale * 100).toFixed(0)}%
          </span>
          <Button variant="ghost" size="sm" onClick={() => { setScale(1); setPan({ x: 0, y: 0 }); }}>
            重置视图
          </Button>
        </div>
      </div>

      {truncated && (
        <p
          className="rounded-md border border-[var(--warn)] px-3 py-1.5 text-[11px] text-[var(--warn)]"
          data-testid="experience-graph-truncated"
        >
          已按权重展示前 {limit} 个 / 共 {total} 条经验——这不是全部。
          <Button variant="ghost" size="sm" onClick={onRaiseLimit} className="ml-2">
            提高上限
          </Button>
        </p>
      )}

      <div className="overflow-hidden rounded-md border border-[var(--border)] bg-[var(--surface)]">
        <svg
          role="group"
          aria-label="交易经验上下文图"
          viewBox={viewBoxOf(scale, pan.x, pan.y)}
          className="h-[440px] w-full touch-none"
          onWheel={(e) => setScale((prev) => clampScale(prev * (e.deltaY < 0 ? 1.1 : 0.9)))}
          onPointerDown={(e) => setDragFrom({ x: e.clientX, y: e.clientY })}
          onPointerUp={() => setDragFrom(null)}
          onPointerLeave={() => setDragFrom(null)}
          onPointerMove={(e) => {
            if (!dragFrom) return;
            const k = 1 / clampScale(scale);
            setPan((prev) => ({
              x: prev.x - (e.clientX - dragFrom.x) * k,
              y: prev.y - (e.clientY - dragFrom.y) * k,
            }));
            setDragFrom({ x: e.clientX, y: e.clientY });
          }}
        >
          <g>
            {layout.edges.map(({ edge, x1, y1, x2, y2 }) => (
              <line
                key={`${edge.srcId}-${edge.dstId}-${edge.rel}`}
                x1={x1}
                y1={y1}
                x2={x2}
                y2={y2}
                stroke={REL_STROKE[edge.rel]}
                strokeWidth={edge.rel === "contradicts" ? 1.6 : 1}
                strokeDasharray={edge.rel === "co_occurs" ? "3 4" : undefined}
                opacity={dim(edge.srcId) && dim(edge.dstId) ? 0.12 : 0.55}
              />
            ))}
          </g>
          <g>
            {layout.nodes.map(({ node, x, y, r }) => {
              const isContext = node.kind === "context";
              const label = isContext
                ? `${axisLabel(node.axis ?? "")}·${valueLabel(node.value ?? "")}`
                : node.label;
              return (
                <g
                  key={node.id}
                  ref={(el) => {
                    if (el) nodeRefs.current.set(node.id, el);
                    else nodeRefs.current.delete(node.id);
                  }}
                  role="button"
                  tabIndex={node.id === focusStop ? 0 : -1}
                  aria-label={`${label}（${isContext ? "语境" : tierLabel(node)}）`}
                  opacity={dim(node.id) ? 0.2 : 1}
                  onFocus={() => { setActiveId(node.id); setRovingId(node.id); }}
                  onBlur={() => setActiveId(null)}
                  onMouseEnter={() => setActiveId(node.id)}
                  onMouseLeave={() => setActiveId(null)}
                  onClick={() => (isContext ? undefined : onOpen(node))}
                  onDoubleClick={() => (isContext ? onFilterContext(node) : undefined)}
                  onKeyDown={(e) => {
                    if (e.key === "ArrowRight" || e.key === "ArrowDown") {
                      e.preventDefault();
                      moveFocus(node.id, 1);
                      return;
                    }
                    if (e.key === "ArrowLeft" || e.key === "ArrowUp") {
                      e.preventDefault();
                      moveFocus(node.id, -1);
                      return;
                    }
                    if (e.key !== "Enter" && e.key !== " ") return;
                    e.preventDefault();
                    if (isContext) onFilterContext(node);
                    else onOpen(node);
                  }}
                  className="cursor-pointer outline-none focus-visible:opacity-100"
                >
                  {isContext ? (
                    <rect
                      x={x - 5}
                      y={y - 5}
                      width={10}
                      height={10}
                      rx={3}
                      fill="var(--surface-2)"
                      stroke="var(--border)"
                    />
                  ) : (
                    <circle
                      cx={x}
                      cy={y}
                      r={r}
                      fill="var(--surface-2)"
                      stroke={ringColor(node)}
                      strokeWidth={node.pinned ? 2.4 : 1.4}
                      strokeDasharray={node.tier === "candidate" ? "2 3" : undefined}
                      opacity={node.muted ? 0.3 : 1}
                    />
                  )}
                  <title>{label}</title>
                  {(isContext || activeId === node.id) && (
                    <text
                      x={x + (isContext ? 8 : r + 4)}
                      y={y + 3}
                      fontSize={11}
                      fill="var(--muted)"
                    >
                      {label.length > 22 ? `${label.slice(0, 22)}…` : label}
                    </text>
                  )}
                </g>
              );
            })}
          </g>
        </svg>
      </div>
    </div>
  );
}
