"use client";

import { useEffect, useRef } from "react";
import { Button } from "@/components/ui";
import { useAppStore } from "@/lib/store";
import AboutSection from "./AboutSection";
import AgentToolsSection from "./AgentToolsSection";
import AiModelsSection from "./AiModelsSection";
import OkxAccountsSection from "./OkxAccountsSection";
import SettingsNav from "./SettingsNav";

/**
 * 设置中心外壳：顶部标题「设置」+ 返回工作台；body 左右布局（左 15% 导航 / 右 75% 内容 + 对称留白）。
 * 无障碍（H7）：root role="region"、挂载时焦点入标题、Esc 关闭、卸载时把焦点交还齿轮按钮。
 */
export default function SettingsPanel() {
  const setShowSettings = useAppStore((s) => s.setShowSettings);
  const section = useAppStore((s) => s.settingsSection);
  const headingRef = useRef<HTMLHeadingElement>(null);

  // 开合焦点流转：挂载焦点入标题，卸载（任意方式关闭）把焦点还给齿轮（找不到则静默降级）。
  useEffect(() => {
    headingRef.current?.focus();
    return () => {
      document.getElementById("settings-gear")?.focus();
    };
  }, []);

  // Esc 关闭：仅在面板挂载期生效，卸载即解绑，避免与既有键盘流冲突（R-Focus）。
  useEffect(() => {
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") setShowSettings(false);
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [setShowSettings]);

  return (
    <div role="region" aria-label="设置" className="flex h-full min-h-0 flex-col bg-[var(--bg)]">
      <header className="flex h-12 shrink-0 items-center justify-between border-b border-[var(--border)] px-6">
        <h1
          ref={headingRef}
          tabIndex={-1}
          className="text-sm font-semibold tracking-tight text-[var(--text)] outline-none"
        >
          设置
        </h1>
        <Button variant="ghost" size="sm" onClick={() => setShowSettings(false)} aria-label="返回工作台">
          返回工作台
        </Button>
      </header>

      <div className="flex min-h-0 flex-1 justify-center gap-6 px-6 py-5">
        <nav className="w-[15%] min-w-[160px] shrink-0">
          <SettingsNav />
        </nav>
        <section className="w-[75%] min-w-0 grow-0 overflow-y-auto">
          {section === "okx-accounts" ? (
            <OkxAccountsSection />
          ) : section === "agent-tools" ? (
            <AgentToolsSection />
          ) : section === "about" ? (
            <AboutSection />
          ) : (
            <AiModelsSection />
          )}
        </section>
      </div>
    </div>
  );
}
