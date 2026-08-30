"use client";

import { useId, useRef } from "react";
import { Button, Modal } from "@/components/ui";

/**
 * 应用内确认弹窗（H-F8 / R-07）：在 `Modal` 外壳（focus-trap / Esc / portal）上加一层「是否」契约，
 * 替换破坏性操作的阻塞式原生 `window.confirm`，焦点受困于应用、风格与设计 token 一致。
 *
 * 受控：调用方持 `open` 状态触发；`onConfirm` 执行操作，`onCancel`（含 Esc / 点击遮罩）关闭。
 *
 * **初始焦点显式钉在「取消」上**（spec D6）。此前确认键写着 `autoFocus`、而外层 `Modal` 又会 focus
 * DOM 里第一个可聚焦元素，两者相互覆盖——代码表达的意图（焦点在危险按钮）与实际行为（焦点在取消）
 * 相反，且没有任何用例钉住其中任何一个。今天恰好是安全的，但任何一次调用方重排都会让**回车键的
 * 落点从「取消」变成「永久删除」**，而没有一条用例会红。这里的规则与 HIG / WAI-ARIA 一致：
 * 默认动作不应是不可逆动作。`danger=false` 时同样钉取消——一个「是否」弹窗的默认答案就该是「否」。
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
  children,
}: {
  open: boolean;
  title: string;
  message: string;
  confirmText?: string;
  cancelText?: string;
  danger?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
  /**
   * 可选的补充控件（作用域勾选框之类），渲染在说明与按钮之间。
   *
   * 加这个插槽而不是另造一个确认弹窗：焦点陷阱、Esc、遮罩、**以及「初始焦点钉在取消上」**这条
   * 不可逆动作的默认极性，都只有一份实现才守得住（ai-trade-intel-closed-loop §3.7.2 的需要）。
   * 缺省 `undefined` ⇒ 既有全部调用点逐字节不变。
   */
  children?: React.ReactNode;
}) {
  const titleId = useId();
  const cancelRef = useRef<HTMLButtonElement>(null);
  return (
    <Modal
      open={open}
      onClose={onCancel}
      labelledBy={titleId}
      className="max-w-md"
      initialFocusRef={cancelRef}
    >
      <div className="flex flex-col gap-3 p-5">
        <h2 id={titleId} className="text-sm font-semibold text-[var(--text)]">{title}</h2>
        <p className="text-[13px] leading-relaxed text-[var(--muted)]">{message}</p>
        {children}
        <div className="mt-1 flex items-center justify-end gap-2">
          <Button ref={cancelRef} variant="ghost" size="sm" onClick={onCancel}>{cancelText}</Button>
          <Button
            variant={danger ? "danger" : "primary"}
            size="sm"
            onClick={onConfirm}
          >
            {confirmText}
          </Button>
        </div>
      </div>
    </Modal>
  );
}
