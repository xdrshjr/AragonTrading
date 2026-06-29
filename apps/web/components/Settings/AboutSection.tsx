"use client";

import { Button } from "@/components/ui";
import { getBridge, useIsDesktop } from "@/lib/desktop";

/**
 * 设置中心「关于 / 诊断」小节：承接原 header AppMenu 的「关于 / 打开日志 / 重新加载」三项动作
 * （评审 P1-1）。复用既有 `showAbout` / `openLogsDir` 桥接，零新增 IPC。
 *
 * 其中「关于」对话框承载 Windows WCO 运行期自检（main.js `showAbout`，诊断
 * desktop-packaged-wco-header-right-still-unclickable 依赖），是标题栏右簇回归的真机人工门，
 * 故删 header 汉堡后该入口必须在此保住。仅桌面端可用（`useIsDesktop()` 守卫），Web 端渲染占位。
 */
export default function AboutSection() {
  const isDesktop = useIsDesktop();

  if (!isDesktop) {
    return (
      <div className="card p-4 text-sm text-[var(--muted)]">关于与诊断功能仅在桌面客户端可用。</div>
    );
  }

  const version = getBridge()?.appVersion?.();

  return (
    <div className="flex flex-col gap-5 pb-6">
      <div className="card flex flex-col gap-1 p-4">
        <span className="text-sm font-medium text-[var(--text)]">Aragon Trading</span>
        <span className="text-[11px] text-[var(--muted)]">
          {version ? `版本 ${version}` : "桌面客户端"}
        </span>
      </div>
      <div className="flex flex-col items-start gap-2">
        <Button variant="default" size="sm" onClick={() => getBridge()?.showAbout?.()}>
          关于 Aragon Trading
        </Button>
        <Button variant="ghost" size="sm" onClick={() => void getBridge()?.openLogsDir?.()}>
          打开日志目录
        </Button>
        <Button variant="ghost" size="sm" onClick={() => location.reload()}>
          重新加载
        </Button>
      </div>
    </div>
  );
}
