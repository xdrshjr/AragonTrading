"use client";

import { useEffect, useRef, useState } from "react";
import { getBridge, useIsDesktop } from "@/lib/desktop";

interface MenuItem {
  label: string;
  action: () => void;
}

/**
 * 左侧应用内轻量菜单（spec §4.8）：补偿被移除原生菜单的「关于 / 打开日志 / 重载」三项。
 *
 * 仅桌面端渲染（`useIsDesktop()` 守卫）；点击外部 / Esc 关闭；方向键在菜单项间移动焦点。
 * 保持最小（仅三项），不扩展为通用菜单框架（spec 放行条件 C-3）。
 */
export default function AppMenu() {
  const isDesktop = useIsDesktop();
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);
  const itemRefs = useRef<Array<HTMLButtonElement | null>>([]);

  const items: MenuItem[] = [
    { label: "关于 Aragon Trading", action: () => getBridge()?.showAbout?.() },
    { label: "打开日志目录", action: () => void getBridge()?.openLogsDir?.() },
    { label: "重新加载", action: () => location.reload() },
  ];

  useEffect(() => {
    if (!open) return;
    const onPointerDown = (e: MouseEvent) => {
      if (!rootRef.current?.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onPointerDown);
    return () => document.removeEventListener("mousedown", onPointerDown);
  }, [open]);

  useEffect(() => {
    if (open) itemRefs.current[0]?.focus();
  }, [open]);

  if (!isDesktop) return null;

  const runItem = (item: MenuItem) => {
    item.action();
    setOpen(false);
  };

  const moveFocus = (delta: number, from: number) => {
    const next = (from + delta + items.length) % items.length;
    itemRefs.current[next]?.focus();
  };

  const onItemKeyDown = (e: React.KeyboardEvent, index: number) => {
    if (e.key === "Escape") return setOpen(false);
    if (e.key === "ArrowDown") return e.preventDefault(), moveFocus(1, index);
    if (e.key === "ArrowUp") return e.preventDefault(), moveFocus(-1, index);
  };

  return (
    <div ref={rootRef} className="no-drag relative">
      <button
        type="button"
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label="应用菜单"
        onClick={() => setOpen((v) => !v)}
        className="flex h-7 w-7 items-center justify-center rounded-md text-[var(--muted)] transition-colors hover:bg-[var(--surface-2)] hover:text-[var(--text)]"
      >
        <svg width="16" height="16" viewBox="0 0 16 16" aria-hidden="true">
          <path d="M2 4h12M2 8h12M2 12h12" stroke="currentColor" strokeWidth="1.4" fill="none" strokeLinecap="round" />
        </svg>
      </button>
      {open && (
        <div role="menu" aria-label="应用菜单" className="card absolute left-0 top-9 z-50 min-w-[180px] p-1">
          {items.map((item, index) => (
            <button
              key={item.label}
              ref={(el) => {
                itemRefs.current[index] = el;
              }}
              role="menuitem"
              type="button"
              onClick={() => runItem(item)}
              onKeyDown={(e) => onItemKeyDown(e, index)}
              className="block w-full rounded-md px-3 py-1.5 text-left text-sm text-[var(--text)] transition-colors hover:bg-[var(--surface-2)] focus:bg-[var(--surface-2)] focus:outline-none"
            >
              {item.label}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
