"use client";

import { useEffect, useId, useRef, useState } from "react";
import { Button, Switch } from "@/components/ui";
import { INDICATORS, type IndicatorCategory } from "@/lib/indicators";
import { useAppStore } from "@/lib/store";

const GROUPS: { category: IndicatorCategory; title: string }[] = [
  { category: "overlay", title: "主图叠加" },
  { category: "pane", title: "副图指标" },
];

// 工具栏「指标」按钮 + 下拉弹窗：按类别分组、逐项 Switch、恢复默认 / 全部关闭；
// 键盘可达（Esc 关闭并还焦点、Space/Enter 切换）、点击外部关闭、ARIA（spec §4.6）。
export default function IndicatorMenu() {
  const indicators = useAppStore((s) => s.indicators);
  const toggleIndicator = useAppStore((s) => s.toggleIndicator);
  const resetIndicators = useAppStore((s) => s.resetIndicators);
  const setIndicators = useAppStore((s) => s.setIndicators);

  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);
  const menuId = useId();

  const enabledCount = INDICATORS.reduce((n, d) => n + (indicators[d.key] ? 1 : 0), 0);

  // 点击外部关闭（mousedown，先于 click，避免误触发内部交互）
  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, [open]);

  // Esc 关闭并把焦点还给触发按钮（按钮带 aria-haspopup，唯一可定位）
  const onKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Escape") {
      e.stopPropagation();
      setOpen(false);
      (rootRef.current?.querySelector('[aria-haspopup="true"]') as HTMLElement | null)?.focus();
    }
  };

  const allOff = () =>
    setIndicators(
      INDICATORS.reduce(
        (acc, d) => {
          acc[d.key] = false;
          return acc;
        },
        {} as Record<(typeof INDICATORS)[number]["key"], boolean>,
      ),
    );

  return (
    <div ref={rootRef} className="relative" onKeyDown={onKeyDown}>
      <Button
        variant="default"
        size="sm"
        aria-haspopup="true"
        aria-expanded={open}
        aria-controls={menuId}
        onClick={() => setOpen((v) => !v)}
        title="技术指标显示开关"
      >
        <span aria-hidden>📊</span>
        <span>指标</span>
        {enabledCount > 0 && (
          <span className="ml-0.5 inline-flex h-4 min-w-4 items-center justify-center rounded-full bg-[var(--accent)] px-1 text-[10px] font-semibold text-[var(--on-accent)] tabular">
            {enabledCount}
          </span>
        )}
      </Button>

      {open && (
        <div
          id={menuId}
          role="menu"
          aria-label="技术指标"
          className="card absolute left-0 top-[calc(100%+6px)] z-50 w-[280px] overflow-hidden p-0 shadow-soft"
        >
          <div className="max-h-[60vh] overflow-y-auto p-1.5">
            {GROUPS.map((g) => {
              const items = INDICATORS.filter((d) => d.category === g.category);
              if (items.length === 0) return null;
              return (
                <div key={g.category} className="mb-1">
                  <div className="px-2 py-1 text-[10px] font-semibold uppercase tracking-wide text-[var(--muted)]">
                    {g.title}
                  </div>
                  {items.map((d) => {
                    const checked = !!indicators[d.key];
                    return (
                      <div
                        key={d.key}
                        role="menuitemcheckbox"
                        aria-checked={checked}
                        tabIndex={0}
                        onClick={() => toggleIndicator(d.key)}
                        onKeyDown={(e) => {
                          if (e.key === " " || e.key === "Enter") {
                            e.preventDefault();
                            toggleIndicator(d.key);
                          }
                        }}
                        className="flex cursor-pointer items-center justify-between gap-2 rounded-[8px] px-2 py-1.5 hover:bg-[var(--surface-2)] focus:bg-[var(--surface-2)] focus:outline-none"
                      >
                        <div className="min-w-0">
                          <div className="flex items-center gap-1.5 text-xs font-medium text-[var(--text)]">
                            <span className="truncate">{d.label}</span>
                            {d.paramsHint && (
                              <span className="shrink-0 text-[10px] text-[var(--muted)]">
                                {d.paramsHint}
                              </span>
                            )}
                          </div>
                          <div className="truncate text-[10px] text-[var(--muted)]">
                            {d.description}
                          </div>
                        </div>
                        {/* Switch 为行内视觉指示：行本身（role=menuitemcheckbox）已承载全部交互与
                            aria-checked，故此处吞掉点击避免与行 onClick 双触发（净零切换 bug）、
                            tabIndex=-1 不重复进 Tab 序、aria-hidden 不重复朗读。 */}
                        <span aria-hidden onClick={(e) => e.stopPropagation()}>
                          <Switch
                            checked={checked}
                            onChange={() => toggleIndicator(d.key)}
                            label={`${d.label} 显示开关`}
                            tabIndex={-1}
                          />
                        </span>
                      </div>
                    );
                  })}
                </div>
              );
            })}
          </div>

          <div className="flex items-center justify-between gap-2 border-t border-[var(--border)] px-2 py-1.5">
            <button
              onClick={resetIndicators}
              className="rounded-[7px] px-2 py-1 text-[11px] font-medium text-[var(--accent-ink)] hover:bg-[var(--accent-soft)]"
            >
              恢复默认
            </button>
            <button
              onClick={allOff}
              className="rounded-[7px] px-2 py-1 text-[11px] font-medium text-[var(--muted)] hover:bg-[var(--surface-2)] hover:text-[var(--text)]"
            >
              全部关闭
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
