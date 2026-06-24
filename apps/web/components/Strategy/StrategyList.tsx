"use client";

import clsx from "clsx";
import type { Strategy } from "@/lib/types";
import { Badge } from "@/components/ui";

export default function StrategyList({
  strategies,
  activeId,
  returnsById,
  onSelect,
  multiSelectIds = [],
  multiSelectDisabledIds = [],
  onToggleMultiSelect,
}: {
  strategies: Strategy[];
  activeId: number | null;
  returnsById: Record<number, number | undefined>;
  onSelect: (id: number) => void;
  multiSelectIds?: number[];
  multiSelectDisabledIds?: number[];
  onToggleMultiSelect?: (id: number) => void;
}) {
  return (
    <div className="flex flex-col gap-1.5">
      {strategies.map((s) => {
        const ret = returnsById[s.id];
        const active = s.id === activeId;
        return (
          <button
            key={s.id}
            onClick={() => onSelect(s.id)}
            disabled={s.unavailable}
            className={clsx(
              "group rounded-[10px] border px-3 py-2.5 text-left transition-all duration-150",
              active
                ? "border-[var(--accent)] bg-[var(--accent-soft)]"
                : "border-[var(--border)] bg-[var(--surface)] hover:border-[var(--accent)] hover:bg-[var(--surface-2)]",
              s.unavailable && "opacity-40",
            )}
          >
            <div className="flex items-center justify-between gap-2">
              <span className="flex min-w-0 items-center gap-2">
                {onToggleMultiSelect && (
                  <input
                    type="checkbox"
                    checked={multiSelectIds.includes(s.id)}
                    disabled={s.unavailable || multiSelectDisabledIds.includes(s.id)}
                    onChange={() => onToggleMultiSelect(s.id)}
                    onClick={(event) => event.stopPropagation()}
                    className="h-4 w-4 shrink-0 accent-[var(--accent)]"
                    aria-label={`选择 ${s.name}`}
                  />
                )}
                <span className="truncate text-sm font-semibold text-[var(--text)]">
                  {s.name}
                </span>
              </span>
              <div className="flex items-center gap-1">
                {s.kind === "combo" && <Badge tone="accent">组合</Badge>}
                {s.builtin && <Badge tone="accent">内置</Badge>}
                {ret !== undefined && (
                  <Badge tone={ret > 0 ? "long" : ret < 0 ? "short" : "neutral"}>
                    {ret > 0 ? "+" : ""}
                    {(ret * 100).toFixed(1)}%
                  </Badge>
                )}
              </div>
            </div>
            <p className="mt-0.5 line-clamp-2 text-[11px] leading-snug text-[var(--muted)]">
              {s.description}
            </p>
          </button>
        );
      })}
    </div>
  );
}
