"use client";

import { Button } from "@/components/ui";
import { useAppStore } from "@/lib/store";
import AiModelsSection from "./AiModelsSection";
import SettingsNav from "./SettingsNav";

/**
 * 设置中心外壳：顶部标题「设置」+ 返回工作台；body 左右布局（左 15% 导航 / 右 75% 内容 + 对称留白）。
 */
export default function SettingsPanel() {
  const setShowSettings = useAppStore((s) => s.setShowSettings);
  const section = useAppStore((s) => s.settingsSection);

  return (
    <div className="flex h-full min-h-0 flex-col bg-[var(--bg)]">
      <header className="flex h-12 shrink-0 items-center justify-between border-b border-[var(--border)] px-6">
        <h1 className="text-sm font-semibold tracking-tight text-[var(--text)]">设置</h1>
        <Button variant="ghost" size="sm" onClick={() => setShowSettings(false)} aria-label="返回工作台">
          返回工作台
        </Button>
      </header>

      <div className="flex min-h-0 flex-1 justify-center gap-6 px-6 py-5">
        <nav className="w-[15%] min-w-[160px] shrink-0">
          <SettingsNav />
        </nav>
        <section className="w-[75%] min-w-0 grow-0 overflow-y-auto">
          {section === "ai-models" ? <AiModelsSection /> : null}
        </section>
      </div>
    </div>
  );
}
