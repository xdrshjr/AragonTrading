"use client";

import type { Strategy } from "@/lib/types";
import { Button, Modal } from "@/components/ui";

/**
 * 删除组合二次确认弹窗：复用 `Modal` 原语（焦点陷阱 / Esc / 遮罩关闭）。
 * 删除不可撤销，故确认后才打 `api.deleteStrategy`；失败显式错误态（不假成功，C1）。
 */
export default function DeleteComboConfirm({
  combo,
  pending,
  error,
  onCancel,
  onConfirm,
}: {
  combo: Strategy;
  pending: boolean;
  error: string | null;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  const titleId = `combo-delete-${combo.id}`;
  return (
    <Modal open onClose={onCancel} labelledBy={titleId} className="max-w-sm">
      <div className="flex flex-col gap-3 px-5 py-4">
        <h2 id={titleId} className="text-sm font-semibold text-[var(--text)]">
          删除组合
        </h2>
        <p className="text-xs leading-relaxed text-[var(--muted)]">
          确认删除组合「<span className="text-[var(--text)]">{combo.name}</span>」？此操作不可撤销。
        </p>
        {error && (
          <div className="rounded-[8px] border border-[var(--short)] bg-[var(--surface)] px-3 py-2 text-[11px] text-[var(--short)]">
            删除失败：{error}
          </div>
        )}
        <div className="flex items-center justify-end gap-2">
          <Button variant="ghost" size="sm" disabled={pending} onClick={onCancel}>
            取消
          </Button>
          <Button variant="danger" size="sm" disabled={pending} onClick={onConfirm}>
            {pending ? "删除中…" : "删除"}
          </Button>
        </div>
      </div>
    </Modal>
  );
}
