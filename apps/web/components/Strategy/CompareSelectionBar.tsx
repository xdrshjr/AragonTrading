"use client";

import clsx from "clsx";
import type { CompareFilter } from "@/lib/compare";
import { Button } from "@/components/ui";

const FILTERS: { key: CompareFilter; label: string }[] = [
  { key: "all", label: "全部" },
  { key: "atomic", label: "原子" },
  { key: "combo", label: "组合" },
];

/**
 * 对比清单顶部常驻工具条（批量选择 + 批量操作 · HCI 最佳实践）：
 * 第一行 [分类筛选] … [已选计数 + 上下文提示]；第二行 [全选/反选/清空]（选择）… [清空结果/运行对比]（操作）。
 * 主操作（运行对比）右下角常驻、视觉最重；破坏性 / 次要操作用 ghost 弱化。计数 `aria-live` 供读屏感知。
 */
export default function CompareSelectionBar({
  filter,
  onFilter,
  selectedCount,
  maxSelections,
  minSelections,
  hasResult,
  running,
  onSelectAll,
  onInvert,
  onClear,
  onRun,
  onClearResult,
}: {
  filter: CompareFilter;
  onFilter: (f: CompareFilter) => void;
  selectedCount: number;
  maxSelections: number;
  minSelections: number;
  hasResult: boolean;
  running: boolean;
  onSelectAll: () => void;
  onInvert: () => void;
  onClear: () => void;
  onRun: () => void;
  onClearResult: () => void;
}) {
  const belowMin = selectedCount < minSelections;
  const atMax = selectedCount >= maxSelections;
  const runDisabled = belowMin || selectedCount > maxSelections || running;
  const hint = belowMin ? `至少 ${minSelections} 个` : atMax ? "已达上限" : null;

  return (
    <div className="sticky top-0 z-10 flex flex-col gap-2 rounded-[10px] border border-[var(--border)] bg-[var(--surface-2)] p-2.5">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex gap-0.5 rounded-[8px] bg-[var(--surface)] p-0.5">
          {FILTERS.map((f) => (
            <button
              key={f.key}
              type="button"
              onClick={() => onFilter(f.key)}
              className={clsx(
                "rounded-[6px] px-2.5 py-0.5 text-[11px] font-medium transition-all duration-150",
                filter === f.key
                  ? "bg-[var(--accent-soft)] text-[var(--accent)]"
                  : "text-[var(--muted)] hover:text-[var(--text)]",
              )}
            >
              {f.label}
            </button>
          ))}
        </div>
        <span className="flex items-center gap-1.5 text-[11px]" aria-live="polite">
          <span className="tabular text-[var(--text)]">
            已选 {selectedCount}/{maxSelections}
          </span>
          {hint && <span className="text-[var(--muted)]">· {hint}</span>}
        </span>
      </div>

      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex gap-1">
          <Button variant="ghost" size="sm" onClick={onSelectAll}>
            全选
          </Button>
          <Button variant="ghost" size="sm" onClick={onInvert}>
            反选
          </Button>
          <Button variant="ghost" size="sm" disabled={selectedCount === 0} onClick={onClear}>
            清空
          </Button>
        </div>
        <div className="flex gap-1">
          {hasResult && (
            <Button variant="ghost" size="sm" onClick={onClearResult}>
              清空结果
            </Button>
          )}
          <Button variant="primary" size="sm" disabled={runDisabled} onClick={onRun}>
            {running ? "对比中…" : `运行对比 (${selectedCount})`}
          </Button>
        </div>
      </div>
    </div>
  );
}
