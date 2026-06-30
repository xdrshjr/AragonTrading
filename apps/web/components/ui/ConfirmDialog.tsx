"use client";

import { useId } from "react";
import { Button, Modal } from "@/components/ui";

/**
 * 应用内确认弹窗（H-F8 / R-07）：在 `Modal` 外壳（focus-trap / Esc / portal）上加一层「是否」契约，
 * 替换破坏性操作的阻塞式原生 `window.confirm`，焦点受困于应用、风格与设计 token 一致。
 *
 * 受控：调用方持 `open` 状态触发；`onConfirm` 执行操作，`onCancel`（含 Esc / 点击遮罩）关闭。
 */
export default function ConfirmDialog({
  open,
  title,
  message,
  confirmText = "确定",
  cancelText = "取消",
  danger = true,
  onConfirm,
  onCancel,
}: {
  open: boolean;
  title: string;
  message: string;
  confirmText?: string;
  cancelText?: string;
  danger?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  const titleId = useId();
  return (
    <Modal open={open} onClose={onCancel} labelledBy={titleId} className="max-w-md">
      <div className="flex flex-col gap-3 p-5">
        <h2 id={titleId} className="text-sm font-semibold text-[var(--text)]">{title}</h2>
        <p className="text-[13px] leading-relaxed text-[var(--muted)]">{message}</p>
        <div className="mt-1 flex items-center justify-end gap-2">
          <Button variant="ghost" size="sm" onClick={onCancel}>{cancelText}</Button>
          <Button
            variant={danger ? "danger" : "primary"}
            size="sm"
            onClick={onConfirm}
            autoFocus
          >
            {confirmText}
          </Button>
        </div>
      </div>
    </Modal>
  );
}
