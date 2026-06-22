"use client";

import clsx from "clsx";
import { useAppStore } from "@/lib/store";

// 设置项注册表（结构可扩展：未来追加条目即在此登记）。
const SECTIONS = [
  { key: "ai-models", label: "AI 模型服务", desc: "API 与默认服务" },
  { key: "agent-tools", label: "Agent 工具管理", desc: "查看与开关可用工具" },
];

export default function SettingsNav() {
  const section = useAppStore((s) => s.settingsSection);
  const setSection = useAppStore((s) => s.setSettingsSection);

  return (
    <nav className="flex flex-col gap-1" aria-label="设置项">
      {SECTIONS.map((s) => (
        <button
          key={s.key}
          type="button"
          onClick={() => setSection(s.key)}
          aria-current={section === s.key ? "page" : undefined}
          className={clsx(
            "flex flex-col gap-0.5 rounded-[10px] px-3 py-2 text-left",
            "transition-colors duration-150 ease-smooth",
            section === s.key
              ? "bg-[var(--accent-soft)] text-[var(--accent)]"
              : "text-[var(--muted)] hover:bg-[var(--surface-2)] hover:text-[var(--text)]",
          )}
        >
          <span className="text-sm font-medium">{s.label}</span>
          <span className="text-[11px] opacity-80">{s.desc}</span>
        </button>
      ))}
    </nav>
  );
}
