"use client";

import { summarizeComboMembers } from "@/lib/combo";
import type { Strategy } from "@/lib/types";
import { Badge, Button, Skeleton } from "@/components/ui";

/**
 * 「我的组合策略」列表：行渲染 + 行内动作（查看 / 编辑 / 删除 / 回测）+「新建组合」。
 * 列表只列用户组合（内置 regime_rotation 行已由 isUserCombo 排除，故无删除内置之虞，R5a）。
 * 编辑态下对「编辑他项」禁用并提示先保存 / 放弃，避免静默覆盖未保存脏草稿（R5b）。
 */
export default function SavedCombosList({
  combos,
  isLoading,
  editingId,
  backtestingId,
  onNew,
  onView,
  onEdit,
  onDelete,
  onBacktest,
}: {
  combos: Strategy[];
  isLoading: boolean;
  editingId: number | null;
  backtestingId: number | null;
  onNew: () => void;
  onView: (combo: Strategy) => void;
  onEdit: (combo: Strategy) => void;
  onDelete: (combo: Strategy) => void;
  onBacktest: (combo: Strategy) => void;
}) {
  return (
    <section className="flex flex-col gap-2">
      <div className="flex items-center justify-between">
        <span className="text-[11px] font-medium text-[var(--text)]">我的组合策略</span>
        <Button variant="default" size="sm" onClick={onNew}>
          + 新建组合
        </Button>
      </div>

      {isLoading ? (
        <div className="flex flex-col gap-1.5">
          <Skeleton className="h-12" />
          <Skeleton className="h-12" />
        </div>
      ) : combos.length === 0 ? (
        <p className="rounded-[8px] border border-dashed border-[var(--border)] px-3 py-4 text-center text-[11px] text-[var(--muted)]">
          还没有保存的组合，点「+ 新建组合」开始
        </p>
      ) : (
        <div className="flex flex-col gap-1.5">
          {combos.map((combo) => (
            <ComboRow
              key={combo.id}
              combo={combo}
              isEditing={editingId === combo.id}
              editDisabled={editingId !== null && editingId !== combo.id}
              isBacktesting={backtestingId === combo.id}
              onView={onView}
              onEdit={onEdit}
              onDelete={onDelete}
              onBacktest={onBacktest}
            />
          ))}
        </div>
      )}
    </section>
  );
}

function ComboRow({
  combo,
  isEditing,
  editDisabled,
  isBacktesting,
  onView,
  onEdit,
  onDelete,
  onBacktest,
}: {
  combo: Strategy;
  isEditing: boolean;
  editDisabled: boolean;
  isBacktesting: boolean;
  onView: (combo: Strategy) => void;
  onEdit: (combo: Strategy) => void;
  onDelete: (combo: Strategy) => void;
  onBacktest: (combo: Strategy) => void;
}) {
  return (
    <div
      className={`flex items-center gap-2 rounded-[8px] border bg-[var(--surface)] px-2.5 py-1.5 text-xs ${
        isEditing ? "border-[var(--accent)]" : "border-[var(--border)]"
      }`}
    >
      <div className="flex min-w-0 flex-1 flex-col">
        <div className="flex items-center gap-1.5">
          <span className="truncate text-[var(--text)]">{combo.name}</span>
          <Badge tone="accent">组合</Badge>
          {isEditing && <span className="text-[10px] text-[var(--accent)]">编辑中</span>}
        </div>
        <span className="truncate text-[11px] text-[var(--muted)]">
          {summarizeComboMembers(combo)}
        </span>
      </div>
      <div className="flex shrink-0 items-center gap-0.5">
        <Button variant="ghost" size="sm" onClick={() => onView(combo)}>
          查看
        </Button>
        <Button
          variant="ghost"
          size="sm"
          disabled={editDisabled}
          title={editDisabled ? "请先保存或放弃当前编辑" : undefined}
          onClick={() => onEdit(combo)}
        >
          编辑
        </Button>
        <Button
          variant="ghost"
          size="sm"
          disabled={isBacktesting}
          onClick={() => onBacktest(combo)}
        >
          {isBacktesting ? "回测中…" : "回测"}
        </Button>
        <Button variant="danger" size="sm" onClick={() => onDelete(combo)}>
          删除
        </Button>
      </div>
    </div>
  );
}
