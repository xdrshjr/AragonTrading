"use client";

import { summarizeComboMembers } from "@/lib/combo";
import type { Strategy } from "@/lib/types";
import { Badge, Button } from "@/components/ui";

/**
 * 列表选中「组合」行时的右栏紧凑详情：组合描述 + 成员/轮动摘要 + 运行回测 / 查看 / 编辑 / 删除。
 * 不含原子参数网格、不含「另存为」、不含 paper 启停（组合只走回测 / 对比，不接 paper）。
 */
export default function ComboStrategyDetail({
  combo,
  busy,
  onRunBacktest,
  onView,
  onEdit,
  onDelete,
}: {
  combo: Strategy;
  busy: boolean;
  onRunBacktest: (combo: Strategy) => void;
  onView: (combo: Strategy) => void;
  onEdit: (combo: Strategy) => void;
  onDelete: (combo: Strategy) => void;
}) {
  return (
    <div className="flex flex-col gap-3">
      <div className="flex min-w-0 items-center gap-2">
        <span className="truncate text-sm font-semibold text-[var(--text)]">{combo.name}</span>
        <Badge tone="accent">组合</Badge>
      </div>

      {combo.description && (
        <p className="text-xs leading-relaxed text-[var(--muted)]">{combo.description}</p>
      )}

      <p className="text-[11px] text-[var(--muted)]">{summarizeComboMembers(combo)}</p>

      <div className="flex flex-wrap gap-2">
        <Button variant="primary" disabled={busy} onClick={() => onRunBacktest(combo)}>
          {busy ? "运行中..." : "运行回测"}
        </Button>
        <Button variant="default" disabled={busy} onClick={() => onView(combo)}>
          查看详情
        </Button>
        <Button variant="default" disabled={busy} onClick={() => onEdit(combo)}>
          编辑
        </Button>
        <Button variant="danger" disabled={busy} onClick={() => onDelete(combo)}>
          删除
        </Button>
      </div>
    </div>
  );
}
