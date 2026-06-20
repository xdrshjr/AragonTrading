import { useEffect, useState } from "react";

/**
 * 桌面外壳（Electron preload）经 contextBridge 暴露的 `window.aragon` 契约（spec §4.4 / §4.6）。
 *
 * 浏览器环境下 `window.aragon` 为 `undefined`，本模块全部安全降级为 `null`，
 * 故引用方（拖拽区、窗口控制键、应用菜单）在 Web 端零回归。
 */
export interface WindowControls {
  minimize(): void;
  toggleMaximize(): void;
  close(): void;
  isMaximized(): Promise<boolean>;
  /** 订阅最大化态变化，返回取消订阅函数。 */
  onMaximizeChange(cb: (max: boolean) => void): () => void;
}

export interface AragonBridge {
  isDesktop?: boolean;
  platform?: NodeJS.Platform;
  appVersion?(): string;
  openLogsDir?(): Promise<void>;
  showAbout?(): void;
  windowControls?: WindowControls;
}

/** 取桌面桥；SSR（无 `window`）与浏览器（无 `window.aragon`）均返回 `null`，不抛错。 */
export function getBridge(): AragonBridge | null {
  if (typeof window === "undefined") return null;
  return (window as unknown as { aragon?: AragonBridge }).aragon ?? null;
}

export function getWindowControls(): WindowControls | null {
  return getBridge()?.windowControls ?? null;
}

export function getDesktopPlatform(): NodeJS.Platform | null {
  return getBridge()?.platform ?? null;
}

/** SSR/浏览器安全：首渲染恒为 false，挂载后才置真，避免水合不一致（spec D4 / RV-3）。 */
export function useIsDesktop(): boolean {
  const [isDesktop, setIsDesktop] = useState(false);
  useEffect(() => {
    setIsDesktop(!!getWindowControls());
  }, []);
  return isDesktop;
}
