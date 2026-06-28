"use client";

import clsx from "clsx";
import { Input } from "@/components/ui";

// 杠杆控件：快捷档（1/3/5/10/20×）+ 手工输入，按 maxLever 夹取（§2.3 / C1）。
const PRESETS = [1, 3, 5, 10, 20];

export default function LeverageControl({
  value,
  maxLever,
  onChange,
  disabled,
}: {
  value: number;
  maxLever: number;
  onChange: (next: number) => void;
  disabled?: boolean;
}) {
  // 夹取到 [1, maxLever]；非有限数回落 1（杜绝 NaN 杠杆，C1 边界校验）。
  const clamp = (n: number): number => {
    if (!Number.isFinite(n)) return 1;
    return Math.min(Math.max(Math.round(n), 1), Math.max(1, Math.floor(maxLever)));
  };

  return (
    <div className="flex flex-col gap-1.5">
      <div className="flex items-center justify-between">
        <span className="text-xs font-medium text-[var(--muted)]">杠杆倍率</span>
        <span className="text-[10px] text-[var(--muted)]">上限 {Math.floor(maxLever)}×</span>
      </div>
      <div className="flex items-center gap-1.5">
        <div className="grid min-w-0 flex-1 grid-cols-5 gap-1">
          {PRESETS.filter((p) => p <= Math.floor(maxLever)).map((preset) => (
            <button
              key={preset}
              type="button"
              disabled={disabled}
              onClick={() => onChange(clamp(preset))}
              className={clsx(
                "rounded-[7px] border px-1 py-1 text-[11px] font-medium transition-colors duration-150",
                "disabled:cursor-not-allowed disabled:opacity-40",
                value === preset
                  ? "border-[var(--accent)] bg-[var(--accent-soft)] text-[var(--accent)]"
                  : "border-[var(--border)] text-[var(--muted)] hover:text-[var(--text)]",
              )}
            >
              {preset}×
            </button>
          ))}
        </div>
        <div className="w-16 shrink-0">
          <Input
            type="number"
            min={1}
            max={Math.floor(maxLever)}
            step={1}
            value={value}
            disabled={disabled}
            aria-label="杠杆倍率"
            onChange={(e) => onChange(clamp(Number(e.target.value)))}
            className="text-right tabular"
          />
        </div>
      </div>
    </div>
  );
}
