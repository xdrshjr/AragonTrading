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
  /** 同步 Windows 系统标题栏控件叠加(titleBarOverlay)配色到明/暗主题（非 win 平台为 no-op）。 */
  setOverlayTheme?(theme: "light" | "dark"): void;
  /** 把渲染层观测到的 Window Controls Overlay 运行期状态回传主进程（G1，供「关于」展示）。 */
  reportOverlay?(state: WindowControlsOverlayState): void;
}

/**
 * Window Controls Overlay 运行期状态（G1，诊断 desktop-packaged-wco-header-right-still-unclickable）。
 *
 * `supported` 为 `false` 表示当前运行体不提供 `navigator.windowControlsOverlay`
 * （浏览器 / mac / linux / 旧 Chromium），此时 `visible`/`rect` 无意义。
 */
export interface WindowControlsOverlayState {
  supported: boolean;
  visible: boolean;
  rect: { x: number; y: number; width: number; height: number } | null;
}

/** `navigator.windowControlsOverlay` 的最小形状（DOM lib 未必含该类型，故本地声明只读取所需字段）。 */
interface WindowControlsOverlayLike {
  visible: boolean;
  getTitlebarAreaRect(): DOMRect;
  addEventListener(type: "geometrychange", cb: () => void): void;
  removeEventListener(type: "geometrychange", cb: () => void): void;
}

function getOverlayApi(): WindowControlsOverlayLike | null {
  if (typeof navigator === "undefined") return null;
  return (
    (navigator as unknown as { windowControlsOverlay?: WindowControlsOverlayLike })
      .windowControlsOverlay ?? null
  );
}

/**
 * 读取 Window Controls Overlay 运行期状态（G1）。
 *
 * 必须 feature-detect：非 win / 旧 Chromium / 浏览器下 `navigator.windowControlsOverlay`
 * 为 `undefined`，直接读 `.visible` 会抛错。无该 API 时安全返回 `supported:false`。
 */
export function readWindowControlsOverlay(): WindowControlsOverlayState {
  const wco = getOverlayApi();
  if (!wco) return { supported: false, visible: false, rect: null };
  let rect: WindowControlsOverlayState["rect"] = null;
  try {
    const r = wco.getTitlebarAreaRect();
    if (r) {
      rect = {
        x: Math.round(r.x),
        y: Math.round(r.y),
        width: Math.round(r.width),
        height: Math.round(r.height),
      };
    }
  } catch {
    rect = null;
  }
  return { supported: true, visible: !!wco.visible, rect };
}

/**
 * 订阅 Window Controls Overlay 状态（挂载即回调一次，几何变化时再回调），返回取消订阅函数（G1）。
 * 非 WCO 平台上回调一次 `supported:false` 后不再触发，且不注册任何监听（零回归）。
 */
export function subscribeWindowControlsOverlay(
  cb: (state: WindowControlsOverlayState) => void,
): () => void {
  cb(readWindowControlsOverlay());
  const wco = getOverlayApi();
  if (!wco?.addEventListener) return () => {};
  const handler = () => cb(readWindowControlsOverlay());
  wco.addEventListener("geometrychange", handler);
  return () => wco.removeEventListener("geometrychange", handler);
}

export interface AragonBridge {
  isDesktop?: boolean;
  platform?: NodeJS.Platform;
  appVersion?(): string;
  openLogsDir?(): Promise<void>;
  /** 系统「选择文件夹」对话框（后训练数据保存目录）；取消 ⇒ `null`。 */
  pickDirectory?(opts?: { defaultPath?: string }): Promise<string | null>;
  /**
   * 在资源管理器中打开一个**已存在的绝对目录**。成功返回空串；失败返回原因
   * （`invalid_path` / `not_found` / `not_directory` / 系统错误文本）。
   */
  openPath?(target: string): Promise<string>;
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
