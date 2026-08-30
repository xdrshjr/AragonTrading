"use client";

import { useState } from "react";
import clsx from "clsx";
import IntelModal from "@/components/Intel/IntelModal";
import {
  intelButtonLabel,
  intelButtonTitle,
  isSevereDegrade,
  unreadBadge,
} from "@/lib/intel";
import { useAppStore } from "@/lib/store";

/**
 * 标的卡片右侧的「市场情报」hover 按钮（ai-trade-market-intel §3.8.2）。
 *
 * **它必须是 `AiTradeMemberRow` 的兄弟节点而不是子节点**：行组件本体是一个 `<button>`，而按钮
 * 不能嵌套按钮（`aiTradePanelNoNestedButton.source.test.ts` 冻结这条约束）。布局由外层
 * `group/member` 容器承担，行组件保持零签名变更。
 *
 * 可见性规则（HCI 三条，缺一不可）：
 * - 默认占位但不可见、不可点，鼠标 hover 行内任意处淡入；
 * - **键盘 Tab 聚焦到它即可见**——只在 hover 时出现的功能对键盘用户等于不存在；
 * - **有未读情报时常驻可见**并带计数徽标，触摸设备（无 hover）同样常驻。
 *
 * 降级中（`degrade != null` 且不是弱提示）图标染 `--warn` 色并在 `title` 里追加降级文案：
 * 用户不必打开弹窗就该知道情报流水线出问题了。
 */
export function AiTradeIntelButton({ instId }: { instId: string }) {
  const [open, setOpen] = useState(false);
  const summary = useAppStore((s) => s.aiTradeIntel[instId] ?? null);
  const unread = summary?.unread ?? 0;
  const badge = unreadBadge(unread);
  const severe = isSevereDegrade(summary?.degrade ?? null);

  return (
    <>
      <button
        type="button"
        data-testid="ai-trade-intel-button"
        aria-label={intelButtonLabel(instId, summary)}
        title={intelButtonTitle(summary)}
        onClick={() => setOpen(true)}
        className={clsx(
          "relative flex h-7 w-7 shrink-0 items-center justify-center self-center rounded-md",
          "border border-[var(--border)] bg-[var(--bg)] transition-opacity duration-150 ease-smooth",
          "hover:bg-[var(--surface-2)] motion-reduce:transition-none",
          "outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]",
          // 键盘可达与触摸设备常驻：两者都不是可选项。
          "focus-visible:opacity-100 focus-visible:pointer-events-auto",
          "[@media(hover:none)]:opacity-100 [@media(hover:none)]:pointer-events-auto",
          unread > 0
            ? "opacity-100 pointer-events-auto"
            : "opacity-0 pointer-events-none group-hover/member:opacity-100 group-hover/member:pointer-events-auto",
          severe ? "text-[var(--warn)]" : "text-[var(--muted)]",
        )}
      >
        {/* 雷达 / 信号图标：三段同心弧 + 圆心，`currentColor` 随降级染色。 */}
        <svg
          viewBox="0 0 24 24"
          aria-hidden="true"
          className="h-3.5 w-3.5"
          fill="none"
          stroke="currentColor"
          strokeWidth={1.8}
          strokeLinecap="round"
        >
          <circle cx="12" cy="18" r="1.4" fill="currentColor" stroke="none" />
          <path d="M8.5 15.5a5 5 0 0 1 7 0" />
          <path d="M5.5 12.2a9.5 9.5 0 0 1 13 0" />
          <path d="M2.8 8.9a14 14 0 0 1 18.4 0" />
        </svg>
        {badge && (
          <span
            data-testid="ai-trade-intel-unread"
            aria-hidden="true"
            className="absolute -right-1 -top-1 flex h-3.5 min-w-3.5 items-center justify-center rounded-full bg-[var(--accent)] px-1 text-[9px] font-semibold leading-none text-[var(--on-accent)]"
          >
            {badge}
          </span>
        )}
      </button>
      {/* 弹窗只在打开时挂载：一个分组可能有十个标的，十份常驻的弹窗状态没有任何价值。 */}
      {open && <IntelModal instId={instId} open onClose={() => setOpen(false)} />}
    </>
  );
}

export default AiTradeIntelButton;
