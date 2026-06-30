"use client";

import clsx from "clsx";
import { type PointerEvent, useRef } from "react";

interface PaneSplitHandleProps {
  /** 拖拽中每帧回吐指针的视口 Y 坐标（clientY）；父组件据自身容器 rect 换算成比例后写 store。 */
  onResize: (clientY: number) => void;
  /** 双击复位回调（可选）。 */
  onReset?: () => void;
  /** 无障碍标签与 title 提示文案。 */
  ariaLabel?: string;
  /** 透传到根 div 的附加类名（可选）。 */
  className?: string;
}

/**
 * 可复用行向拖拽手柄（调高）：Pointer 捕获 + 每帧回吐 clientY + 双击复位。
 *
 * 与 PanelResizeHandle 既有竖向手柄（调宽）同构，但朝向 / 光标 / 边框不同，故单独成文；
 * `setPointerCapture` 把拖拽期事件锁定到手柄，卸载即自动释放，无需手动清理 window 监听。
 * `pointercancel` / `lostpointercapture`（系统取消拖拽）一并复位 `draggingRef`，避免残留 true。
 */
export default function PaneSplitHandle({
  onResize,
  onReset,
  ariaLabel,
  className,
}: PaneSplitHandleProps) {
  const draggingRef = useRef(false);
  const onPointerDown = (e: PointerEvent<HTMLDivElement>) => {
    e.preventDefault();
    e.currentTarget.setPointerCapture(e.pointerId);
    draggingRef.current = true;
  };
  const onPointerMove = (e: PointerEvent<HTMLDivElement>) => {
    if (!draggingRef.current) return;
    onResize(e.clientY);
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
      aria-orientation="horizontal"
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
        "group flex h-1.5 w-full shrink-0 cursor-row-resize items-center justify-center",
        "border-t border-[var(--border)] bg-[var(--surface-2)] hover:bg-[var(--accent-soft)]",
        className,
      )}
    >
      <div className="h-0.5 w-8 rounded-full bg-[var(--border)] group-hover:bg-[var(--accent)]" />
    </div>
  );
}
