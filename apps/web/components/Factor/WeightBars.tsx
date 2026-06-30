"use client";

import { weightBars } from "@/lib/factor";

/**
 * 共享权重条：按权重绝对值最大值归一为宽度，正负分色、零基线居中（几何取自 lib/factor.weightBars）。
 * 由 FactorSetBuilder（训练结果）与 FactorSetDetail（已存组合）复用，避免重复并控住单文件行数。
 */
export default function WeightBars({ weights }: { weights: Record<string, number> }) {
  const bars = weightBars(weights);
  return (
    <div className="flex flex-col gap-1.5">
      {bars.map((b) => (
        <div key={b.name} className="flex items-center gap-2">
          <span className="w-32 shrink-0 truncate text-[11px] text-[var(--muted)]">{b.name}</span>
          <div className="relative h-3 flex-1 rounded-full bg-[var(--surface-2)]">
            <div
              className="absolute top-0 h-3 rounded-full"
              style={{
                width: `${b.widthPct / 2}%`,
                left: b.positive ? "50%" : undefined,
                right: b.positive ? undefined : "50%",
                background: b.positive ? "var(--long)" : "var(--short)",
              }}
            />
            <div className="absolute left-1/2 top-0 h-3 w-px bg-[var(--border)]" />
          </div>
          <span className="w-14 shrink-0 text-right text-[11px] tabular text-[var(--text)]">
            {b.weight.toFixed(3)}
          </span>
        </div>
      ))}
    </div>
  );
}
