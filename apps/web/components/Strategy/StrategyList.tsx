"use client";

import clsx from "clsx";
import type { Strategy } from "@/lib/types";
import { Badge, Button } from "@/components/ui";

export default function StrategyList({
  strategies,
  activeId,
  returnsById,
  onSelect,
  multiSelectIds = [],
  multiSelectDisabledIds = [],
  onToggleMultiSelect,
  onEditStrategy,
  onDeleteStrategy,
}: {
  strategies: Strategy[];
  activeId: number | null;
  returnsById: Record<number, number | undefined>;
  onSelect: (id: number) => void;
  multiSelectIds?: number[];
  multiSelectDisabledIds?: number[];
  onToggleMultiSelect?: (id: number) => void;
  onEditStrategy?: (s: Strategy) => void;
  onDeleteStrategy?: (s: Strategy) => void;
}) {
  const showActions = Boolean(onEditStrategy || onDeleteStrategy);
  return (
    <div className="flex flex-col gap-1.5">
      {strategies.map((s) => {
        const ret = returnsById[s.id];
        const active = s.id === activeId;
        // 编辑入口语义按类型不同：原子在下方详情区内联改参；组合弹编辑器；因子交接因子实验室。
        const editTitle =
          s.kind === "combo"
            ? "编辑组合"
            : s.kind === "factor"
              ? "在因子实验室编辑"
              : "选中并在下方编辑参数";
        return (
          // 卡片改为非交互容器（原为 <button>）：选择区是独立 button、动作按钮是其兄弟节点，
          // 避免交互元素非法嵌套（原 checkbox 嵌在 button 内的既有问题一并修复）。
          <div
            key={s.id}
            className={clsx(
              "group flex items-start gap-2 rounded-[10px] border px-3 py-2.5 transition-all duration-150",
              active
                ? "border-[var(--accent)] bg-[var(--accent-soft)]"
                : "border-[var(--border)] bg-[var(--surface)] hover:border-[var(--accent)] hover:bg-[var(--surface-2)]",
              s.unavailable && "opacity-40",
            )}
          >
            {onToggleMultiSelect && (
              <input
                type="checkbox"
                checked={multiSelectIds.includes(s.id)}
                disabled={s.unavailable || multiSelectDisabledIds.includes(s.id)}
                onChange={() => onToggleMultiSelect(s.id)}
                className="mt-1 h-4 w-4 shrink-0 accent-[var(--accent)]"
                aria-label={`选择 ${s.name}`}
              />
            )}
            <button
              type="button"
              onClick={() => onSelect(s.id)}
              disabled={s.unavailable}
              aria-pressed={active}
              className="flex min-w-0 flex-1 flex-col text-left"
            >
              <span className="flex items-center justify-between gap-2">
                <span className="truncate text-sm font-semibold text-[var(--text)]">{s.name}</span>
                <span className="flex shrink-0 items-center gap-1">
                  {s.kind === "combo" && <Badge tone="accent">组合</Badge>}
                  {s.builtin && <Badge tone="accent">内置</Badge>}
                  {ret !== undefined && (
                    <Badge tone={ret > 0 ? "long" : ret < 0 ? "short" : "neutral"}>
                      {ret > 0 ? "+" : ""}
                      {(ret * 100).toFixed(1)}%
                    </Badge>
                  )}
                </span>
              </span>
              <span className="mt-0.5 line-clamp-2 text-[11px] leading-snug text-[var(--muted)]">
                {s.description}
              </span>
            </button>
            {showActions && (
              <div className="flex shrink-0 items-center gap-1">
                {onEditStrategy && (
                  <Button
                    type="button"
                    variant="ghost"
                    size="sm"
                    disabled={s.unavailable}
                    title={editTitle}
                    aria-label={`编辑 ${s.name}`}
                    onClick={(event) => {
                      event.stopPropagation();
                      onEditStrategy(s);
                    }}
                  >
                    ✎
                  </Button>
                )}
                {onDeleteStrategy && (
                  <Button
                    type="button"
                    variant="ghost"
                    size="sm"
                    disabled={s.builtin}
                    title={s.builtin ? "内置策略不可删除" : `删除 ${s.name}`}
                    aria-label={`删除 ${s.name}`}
                    onClick={(event) => {
                      event.stopPropagation();
                      onDeleteStrategy(s);
                    }}
                  >
                    🗑
                  </Button>
                )}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}
