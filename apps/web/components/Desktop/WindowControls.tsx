"use client";

import clsx from "clsx";
import { useEffect, useState } from "react";
import { getDesktopPlatform, getWindowControls, useIsDesktop } from "@/lib/desktop";

/**
 * 自定义标题栏右侧窗口控制带（spec §4.7）：最小化 / 最大化↔还原 / 关闭。
 *
 * - 仅桌面端（`window.aragon`）的 Linux 渲染自绘三键；浏览器、mac、Windows 均降级为 `null`
 *   （mac 红绿灯由系统在左侧绘制；Windows 改用系统 titleBarOverlay 在右上角绘制三键，
 *   见诊断 desktop-header-right-and-live-toggle-unclickable——自绘三键会与系统叠加重影）。
 * - 图标随窗口最大化态切换：初始读 `isMaximized()`，并订阅 `onMaximizeChange`。
 */
export default function WindowControls() {
  const isDesktop = useIsDesktop();
  const [maximized, setMaximized] = useState(false);

  useEffect(() => {
    const controls = getWindowControls();
    if (!controls) return;
    controls.isMaximized().then(setMaximized);
    return controls.onMaximizeChange(setMaximized);
  }, []);

  const platform = getDesktopPlatform();
  if (!isDesktop || platform === "darwin" || platform === "win32") return null;
  const controls = getWindowControls();
  if (!controls) return null;

  const base =
    "no-drag flex h-12 w-[46px] items-center justify-center text-[var(--muted)] " +
    "transition-colors duration-150 hover:bg-[var(--surface-2)] hover:text-[var(--text)] " +
    "focus-visible:outline focus-visible:outline-2 focus-visible:outline-[var(--accent)] " +
    "focus-visible:-outline-offset-2";

  return (
    <div className="no-drag -mr-4 flex h-12 items-stretch">
      <button type="button" className={base} aria-label="最小化" onClick={() => controls.minimize()}>
        <svg width="11" height="11" viewBox="0 0 11 11" aria-hidden="true">
          <path d="M1.5 6h8" stroke="currentColor" strokeWidth="1.1" fill="none" />
        </svg>
      </button>
      <button
        type="button"
        className={base}
        aria-label={maximized ? "向下还原" : "最大化"}
        onClick={() => controls.toggleMaximize()}
      >
        {maximized ? (
          <svg width="11" height="11" viewBox="0 0 11 11" aria-hidden="true">
            <rect x="1" y="3" width="6" height="6" rx="0.5" stroke="currentColor" strokeWidth="1.1" fill="none" />
            <path d="M3.5 3V1.5h6v6H8" stroke="currentColor" strokeWidth="1.1" fill="none" />
          </svg>
        ) : (
          <svg width="11" height="11" viewBox="0 0 11 11" aria-hidden="true">
            <rect x="1.5" y="1.5" width="8" height="8" rx="0.5" stroke="currentColor" strokeWidth="1.1" fill="none" />
          </svg>
        )}
      </button>
      <button
        type="button"
        className={clsx(base, "hover:bg-[#e81123] hover:text-white")}
        aria-label="关闭"
        onClick={() => controls.close()}
      >
        <svg width="11" height="11" viewBox="0 0 11 11" aria-hidden="true">
          <path d="M1.5 1.5l8 8M9.5 1.5l-8 8" stroke="currentColor" strokeWidth="1.1" fill="none" />
        </svg>
      </button>
    </div>
  );
}
