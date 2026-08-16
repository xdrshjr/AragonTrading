"use client";

import type { TemplateSpec } from "@/lib/discoveryTypes";

/**
 * 可挖模板勾选表。
 *
 * 不可挖的模板**灰显并直接写出原因**，绝不静默隐藏——「界面上没有」和「不能挖」是两件事，
 * 混起来用户只会以为是自己没找到（诚实优先于漂亮，§2.12）。
 */
export default function DiscoveryTemplatePicker({
  templates,
  selected,
  onChange,
}: {
  templates: TemplateSpec[];
  /** 空数组 = 全部可挖模板（与后端 `templates: []` 的语义一致）。 */
  selected: string[];
  onChange: (next: string[]) => void;
}) {
  const minable = templates.filter((item) => item.minable);
  const blocked = templates.filter((item) => !item.minable);
  const checked = (key: string) => selected.length === 0 || selected.includes(key);

  const toggle = (key: string) => {
    const base = selected.length === 0 ? minable.map((item) => item.key) : selected;
    const next = base.includes(key) ? base.filter((item) => item !== key) : [...base, key];
    onChange(next.length === minable.length ? [] : next);
  };

  return (
    <div className="flex flex-col gap-2">
      <div className="flex items-center gap-2 text-[11px] text-[var(--muted)]">
        <span>
          可挖模板 {minable.length} 个{selected.length === 0 ? "（默认全选）" : `（已选 ${selected.length}）`}
        </span>
        <button
          type="button"
          className="text-[var(--accent-ink)] underline"
          onClick={() => onChange([])}
        >
          全选
        </button>
      </div>

      <ul className="flex max-h-[168px] flex-col gap-1 overflow-y-auto pr-1">
        {minable.map((item) => (
          <li key={item.key}>
            <label className="flex cursor-pointer items-center gap-2 text-[11px] text-[var(--text)]">
              <input
                type="checkbox"
                aria-label={`挖掘模板 ${item.name}`}
                checked={checked(item.key)}
                onChange={() => toggle(item.key)}
              />
              <span className="truncate">{item.name}</span>
              <span className="shrink-0 text-[10px] text-[var(--muted)]">
                {item.familyCn} · {item.paramCount} 个可搜索参数
                {item.htfBars.length ? ` · 高周期 ${item.htfBars.join("/")}` : ""}
              </span>
            </label>
          </li>
        ))}
      </ul>

      {blocked.length > 0 && (
        <div>
          <p className="mb-1 text-[10px] text-[var(--muted)]">不可挖的模板（原因如实列出）</p>
          <ul className="flex flex-col gap-0.5">
            {blocked.map((item) => (
              <li
                key={item.key}
                className="flex items-center gap-2 text-[10px] text-[var(--muted)]"
              >
                <span aria-hidden>—</span>
                <span className="truncate">{item.name}</span>
                <span className="truncate italic">{item.reason}</span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
