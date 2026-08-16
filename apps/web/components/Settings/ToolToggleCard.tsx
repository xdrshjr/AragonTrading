"use client";

import { useState } from "react";
import clsx from "clsx";
import { Badge, Switch } from "@/components/ui";
import { labelFor } from "@/lib/agentToolMeta";
import type { AgentTool } from "@/lib/agentTypes";

/**
 * 单工具卡片：图标 + 友好中文名 + 等宽工具名 + 描述 + 启用开关 + 可折叠「查看参数」。
 *
 * 无障碍（spec §2.9 / P1-1）：开关用 `Switch` 的 `label` 形参承载读屏文案（组件内部映射为
 * `aria-label`），**不要写 `aria-label`**（会被静默丢弃）。禁用态整卡降低不透明度但开关与展开仍可操作。
 */
export default function ToolToggleCard({
  tool,
  onToggle,
}: {
  tool: AgentTool;
  onToggle: (name: string, next: boolean) => void;
}) {
  const [open, setOpen] = useState(false);
  const { label, icon } = labelFor(tool.name);

  return (
    <div className="overflow-hidden rounded-[12px] border border-[var(--border)] bg-[var(--surface)]">
      <div
        className={clsx(
          "flex items-start gap-3 p-3.5 transition-opacity duration-150",
          !tool.enabled && "opacity-60",
        )}
      >
        <span className="mt-0.5 text-lg leading-none">{icon}</span>
        <div className="flex min-w-0 flex-1 flex-col gap-1">
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-sm font-medium text-[var(--text)]">{label}</span>
            <code className="rounded bg-[var(--surface-2)] px-1.5 py-0.5 text-[11px] text-[var(--muted)]">
              {tool.name}
            </code>
            {tool.isUiAction && <Badge tone="accent">界面动作</Badge>}
          </div>
          <p className="text-xs leading-relaxed text-[var(--muted)]">{tool.description}</p>
          <button
            type="button"
            onClick={() => setOpen((v) => !v)}
            aria-expanded={open}
            className="mt-0.5 inline-flex w-fit items-center gap-1 text-[11px] font-medium text-[var(--accent-ink)] hover:underline"
          >
            <span>{open ? "▾" : "▸"}</span>
            查看参数
          </button>
        </div>
        <Switch
          checked={tool.enabled}
          onChange={(next) => onToggle(tool.name, next)}
          label={`${tool.enabled ? "关闭" : "开启"}${label}`}
        />
      </div>
      {open && (
        <div className="border-t border-[var(--border)] bg-[var(--surface-2)]/40 px-3.5 py-3">
          <ParamsTable schema={tool.paramsSchema} />
        </div>
      )}
    </div>
  );
}

/** 只读入参表：字段 / 类型 / 必填 / 说明；无入参时给出提示。 */
function ParamsTable({ schema }: { schema: AgentTool["paramsSchema"] }) {
  const properties = schema.properties ?? {};
  const required = new Set(schema.required ?? []);
  const fields = Object.entries(properties);

  if (fields.length === 0) {
    return <p className="text-[11px] text-[var(--muted)]">无入参。</p>;
  }

  return (
    <table className="w-full text-left text-[11px]">
      <thead>
        <tr className="text-[var(--muted)]">
          <th className="pb-1.5 pr-3 font-medium">字段</th>
          <th className="pb-1.5 pr-3 font-medium">类型</th>
          <th className="pb-1.5 pr-3 font-medium">必填</th>
          <th className="pb-1.5 font-medium">说明</th>
        </tr>
      </thead>
      <tbody className="align-top">
        {fields.map(([key, spec]) => (
          <tr key={key} className="border-t border-[var(--border)]">
            <td className="py-1.5 pr-3">
              <code className="text-[var(--text)]">{key}</code>
            </td>
            <td className="py-1.5 pr-3 text-[var(--muted)]">{typeOf(spec)}</td>
            <td className="py-1.5 pr-3 text-[var(--muted)]">{required.has(key) ? "必填" : "可选"}</td>
            <td className="py-1.5 text-[var(--muted)]">{descOf(spec)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

/** 入参类型展示：基础类型，带 enum 时追加候选值。 */
function typeOf(spec: unknown): string {
  if (!spec || typeof spec !== "object") return "—";
  const s = spec as { type?: string; enum?: unknown[] };
  const base = s.type ?? "any";
  return Array.isArray(s.enum) ? `${base}（${s.enum.join(" / ")}）` : base;
}

function descOf(spec: unknown): string {
  if (!spec || typeof spec !== "object") return "—";
  return (spec as { description?: string }).description ?? "—";
}
