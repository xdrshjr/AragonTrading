"use client";

import clsx from "clsx";
import type { ReactNode } from "react";
import { useAppStore, type WorkbenchView } from "@/lib/store";

/**
 * 左侧竖向主功能导航栏（App Shell 外壳）。
 *
 * 纵向排布一级功能项（交易 / 因子实验室），常驻在 header 之下、内容区最左侧；点击即写入
 * store.activeView 切换视图，并关闭设置浮层。选中项以赭色强调（--accent）+ 左侧指示条高亮。
 * 内联 SVG，无三方图标依赖（镜像 FactorButton/SettingsGearButton 的着色与圆角 token）。
 * 普通 <button> + aria-current 即满足 Tab/Enter/Space 键盘可达（因子层 NavRail §2.3）。
 */

interface NavItem {
  key: WorkbenchView;
  label: string;
  icon: ReactNode;
}

function TradeIcon(): ReactNode {
  return (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <line x1="7" y1="3" x2="7" y2="21" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
      <rect x="4.5" y="7" width="5" height="8" rx="1.2" stroke="currentColor" strokeWidth="1.6" />
      <line x1="17" y1="5" x2="17" y2="19" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
      <rect x="14.5" y="9.5" width="5" height="6.5" rx="1.2" stroke="currentColor" strokeWidth="1.6" />
    </svg>
  );
}

function FactorIcon(): ReactNode {
  // 复用 FactorButton 烧瓶 SVG path，保持入口图标视觉一致。
  return (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <path
        d="M9 3h6M9 3v5.2a2 2 0 0 1-.34 1.12l-4.32 6.4A2 2 0 0 0 6 19h12a2 2 0 0 0 1.66-3.12l-4.32-6.4A2 2 0 0 1 15 8.2V3"
        stroke="currentColor"
        strokeWidth="1.6"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      <path d="M7 14h10" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
    </svg>
  );
}

const ITEMS: NavItem[] = [
  { key: "trade", label: "交易", icon: <TradeIcon /> },
  { key: "factor", label: "因子实验室", icon: <FactorIcon /> },
];

export default function NavRail() {
  const activeView = useAppStore((s) => s.activeView);
  const showSettings = useAppStore((s) => s.showSettings);
  const setActiveView = useAppStore((s) => s.setActiveView);
  const setShowSettings = useAppStore((s) => s.setShowSettings);

  // 点导航即关设置浮层再切视图（设置不是导航栏一级项）。
  const select = (key: WorkbenchView) => {
    if (showSettings) setShowSettings(false);
    setActiveView(key);
  };

  return (
    <nav
      aria-label="主功能导航"
      className="flex w-12 shrink-0 flex-col gap-1 border-r border-[var(--border)] bg-[var(--surface)]/60 px-2 py-3"
    >
      {ITEMS.map((item) => {
        // 设置浮层打开时两项均不显选中，避免误导（高亮以 !showSettings && activeView 计算）。
        const active = !showSettings && activeView === item.key;
        return (
          <button
            key={item.key}
            type="button"
            id={`nav-rail-${item.key}`}
            onClick={() => select(item.key)}
            aria-current={active ? "page" : undefined}
            aria-label={item.label}
            className={clsx(
              "group relative flex h-11 items-center justify-center rounded-[12px]",
              "transition-all duration-150 ease-smooth",
              active
                ? "bg-[var(--accent-soft)] text-[var(--accent)]"
                : "text-[var(--muted)] hover:bg-[var(--surface-2)] hover:text-[var(--text)]",
            )}
          >
            {active && (
              <span
                aria-hidden="true"
                className="absolute left-0 top-1/2 h-5 w-[3px] -translate-y-1/2 rounded-full bg-[var(--accent)]"
              />
            )}
            {item.icon}
            {/* 默认仅图标；hover/focus 时右侧弹出文字气泡。aria-hidden：可访问名仍由 aria-label 提供，
                不重复朗读；pointer-events-none + 绝对定位：不撑开 48px 栏宽、不拦截命中。 */}
            <span
              aria-hidden="true"
              className={clsx(
                "pointer-events-none absolute left-full top-1/2 z-50 ml-2 -translate-y-1/2",
                "whitespace-nowrap rounded-md border border-[var(--border)] bg-[var(--surface)]",
                "px-2 py-1 text-xs font-medium text-[var(--text)] shadow-sm",
                "opacity-0 transition-opacity duration-150 ease-smooth",
                "group-hover:opacity-100 group-focus-visible:opacity-100",
              )}
            >
              {item.label}
            </span>
          </button>
        );
      })}
    </nav>
  );
}
