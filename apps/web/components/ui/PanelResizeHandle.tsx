"use client";

import clsx from "clsx";
import { type PointerEvent, useRef } from "react";

interface PanelResizeHandleProps {
  /** 拖拽中每帧回吐指针的视口 X 坐标（clientX）；父组件据自身容器 rect 换算成宽度 / 比例后写 store。 */
  onResize: (clientX: number) => void;
  /** 双击复位回调（可选）。 */
  onReset?: () => void;
  /** 无障碍标签与 title 提示文案。 */
  ariaLabel?: string;
  /** 透传到根 div 的附加类名（可选）。 */
  className?: string;
}

/**
 * 可复用竖向拖拽手柄（调宽）：Pointer 捕获 + 每帧回吐 clientX + 双击复位。
 *
 * 与 ChartWorkspace 既有横向手柄（调高）同构，但朝向 / 光标 / 边框不同，故单独成文；
 * `setPointerCapture` 把拖拽期事件锁定到手柄，卸载即自动释放，无需手动清理 window 监听。
 * `pointercancel` / `lostpointercapture`（系统取消拖拽）一并复位 `draggingRef`，避免残留 true。
 */
export default function PanelResizeHandle({
  onResize,
  onReset,
  ariaLabel,
  className,
}: PanelResizeHandleProps) {
  const draggingRef = useRef(false);
  const onPointerDown = (e: PointerEvent<HTMLDivElement>) => {
    e.preventDefault();
    e.currentTarget.setPointerCapture(e.pointerId);
    draggingRef.current = true;
  };
  const onPointerMove = (e: PointerEvent<HTMLDivElement>) => {
    if (!draggingRef.current) return;
    onResize(e.clientX);
  };
  const onPointerUp = (e: PointerEvent<HTMLDivElement>) => {
    draggingRef.current = false;
    if (e.currentTarget.hasPointerCapture(e.pointerId)) {
      e.currentTarget.releasePointerCapture(e.pointerId);
    }
  };
  const onPointerCancel = () => {
    draggingRef.current = false;
  };
  return (
    <div
      role="separator"
      aria-orientation="vertical"
      aria-label={ariaLabel}
      title={ariaLabel}
      onPointerDown={onPointerDown}
      onPointerMove={onPointerMove}
      onPointerUp={onPointerUp}
      onPointerCancel={onPointerCancel}
      onLostPointerCapture={onPointerCancel}
      onDoubleClick={onReset}
      style={{ touchAction: "none" }}
      className={clsx(
        "group flex w-1.5 shrink-0 cursor-col-resize items-center justify-center",
        "border-l border-[var(--border)] bg-[var(--surface-2)] hover:bg-[var(--accent-soft)]",
        className,
      )}
    >
      <div className="h-8 w-0.5 rounded-full bg-[var(--border)] group-hover:bg-[var(--accent)]" />
    </div>
  );
}
