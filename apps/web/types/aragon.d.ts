import type { AragonBridge } from "@/lib/desktop";

/**
 * 全局 `window.aragon` 类型声明（spec §4.6）。
 * 桌面端 Electron preload 注入；浏览器环境为 `undefined`，故为可选。
 */
declare global {
  interface Window {
    aragon?: AragonBridge;
  }
}

export {};
