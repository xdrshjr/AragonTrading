"use client";

import RailIconButton from "./RailIconButton";
import NavRailControls from "./NavRailControls";
import type { ReactNode } from "react";
import { useAppStore, type WorkbenchView } from "@/lib/store";

/**
 * 左侧竖向主功能导航栏（App Shell 外壳）。
 *
 * 纵向排布一级功能项（交易 / 因子实验室 / 策略与因子挖掘），常驻在 header 之下、
 * 内容区最左侧；点击即写入
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

function RadarIcon(): ReactNode {
  // 雷达：同心扫描弧 + 指向臂，呼应「持续扫描 / 自动发现」。规格与前两枚一致
  // （20×20 / 1.6 stroke / currentColor / 内联 SVG，不引第三方图标库）。
  return (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <circle cx="12" cy="12" r="8.5" stroke="currentColor" strokeWidth="1.6" />
      <circle cx="12" cy="12" r="4.5" stroke="currentColor" strokeWidth="1.6" />
      <circle cx="12" cy="12" r="1.1" fill="currentColor" />
      <path d="M12 12 19.2 7.6" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
    </svg>
  );
}

const ITEMS: NavItem[] = [
  { key: "trade", label: "交易", icon: <TradeIcon /> },
  { key: "factor", label: "因子实验室", icon: <FactorIcon /> },
  { key: "discovery", label: "策略与因子挖掘", icon: <RadarIcon /> },
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
    <nav aria-label="主功能导航"
      className="flex min-h-0 w-14 shrink-0 flex-col border-r border-[var(--border)] bg-[var(--surface)]/60 py-3">
      <div className="min-h-0 flex-1 overflow-y-auto overflow-x-hidden">
        <div className="flex flex-col items-center gap-1">
          {ITEMS.map((item) => {
            const active = !showSettings && activeView === item.key;
            return <RailIconButton key={item.key}
              id={`nav-rail-${item.key}`} label={item.label}
              active={active} indicator aria-current={active ? "page" : undefined}
              onClick={() => select(item.key)}>
              {item.icon}
            </RailIconButton>;
          })}
        </div>
      </div>
      <NavRailControls />
    </nav>
  );
}
