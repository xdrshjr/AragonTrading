"use client";

import clsx from "clsx";
import { useAppStore } from "@/lib/store";

/**
 * 顶栏「因子实验室」入口按钮（镜像 SettingsGearButton；内联 SVG，无三方图标依赖）。
 * 处于因子实验室时高亮（accent-soft），并以 aria-pressed 表达切换态（评审 C-P1-7）。
 */
export default function FactorButton() {
  const showFactorLab = useAppStore((s) => s.showFactorLab);
  const setShowFactorLab = useAppStore((s) => s.setShowFactorLab);
  const setShowSettings = useAppStore((s) => s.setShowSettings);

  return (
    <button
      type="button"
      id="factor-lab-button"
      onClick={() => {
        if (!showFactorLab) setShowSettings(false); // 与设置面板互斥，避免双面板叠加
        setShowFactorLab(!showFactorLab);
      }}
      aria-label="因子实验室"
      aria-pressed={showFactorLab}
      title="因子实验室"
      className={clsx(
        "no-drag inline-flex h-8 items-center gap-1.5 rounded-[10px] px-2.5",
        "text-xs font-medium transition-all duration-150 ease-smooth",
        showFactorLab
          ? "bg-[var(--accent-soft)] text-[var(--accent)]"
          : "text-[var(--muted)] hover:bg-[var(--surface-2)] hover:text-[var(--text)]",
      )}
    >
      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" aria-hidden="true">
        <path d="M9 3h6M9 3v5.2a2 2 0 0 1-.34 1.12l-4.32 6.4A2 2 0 0 0 6 19h12a2 2 0 0 0 1.66-3.12l-4.32-6.4A2 2 0 0 1 15 8.2V3"
          stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
        <path d="M7 14h10" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
      </svg>
      <span className="hidden md:inline">因子实验室</span>
    </button>
  );
}
