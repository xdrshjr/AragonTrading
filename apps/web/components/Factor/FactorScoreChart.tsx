"use client";

import { useMemo } from "react";
import { type LinePoint, sparkline } from "@/lib/factorSeries";

/**
 * 复合得分 / 单因子值的振荡 pane（自绘 SVG sparkline + 零基线 + 端点最新值）。
 * 复用 factorSeries 几何，配色用设计 token，明暗主题零适配（因子层 §2.8 上图）。
 */
export default function FactorScoreChart({
  points,
  title,
  height = 120,
  zeroBaseline = true,
}: {
  points: LinePoint[];
  title?: string;
  height?: number;
  zeroBaseline?: boolean;
}) {
  const width = 640;
  const geom = useMemo(() => sparkline(points, width, height), [points, height]);

  if (points.length === 0) {
    return (
      <div
        className="flex items-center justify-center rounded-[10px] border border-dashed border-[var(--border)] text-[11px] text-[var(--muted)]"
        style={{ height }}
      >
        暂无可上图的数据点
      </div>
    );
  }

  const path = geom.coords.map((c, i) => `${i === 0 ? "M" : "L"}${c.x.toFixed(1)},${c.y.toFixed(1)}`).join(" ");
  const span = geom.maxV - geom.minV || 1;
  const zeroY = geom.minV <= 0 && geom.maxV >= 0 ? 2 + (height - 4) - ((0 - geom.minV) / span) * (height - 4) : null;
  const latest = points[points.length - 1].value;

  return (
    <div className="flex flex-col gap-1.5">
      {title && (
        <div className="flex items-center justify-between">
          <span className="text-[11px] font-medium text-[var(--muted)]">{title}</span>
          <span className="text-[11px] tabular text-[var(--text)]">
            最新 <span className={latest >= 0 ? "text-[var(--long)]" : "text-[var(--short)]"}>{latest.toFixed(4)}</span>
          </span>
        </div>
      )}
      <div className="overflow-hidden rounded-[10px] border border-[var(--border)] bg-[var(--surface)]">
        <svg viewBox={`0 0 ${width} ${height}`} className="h-auto w-full" preserveAspectRatio="none" role="img" aria-label={title || "因子得分曲线"}>
          {zeroBaseline && zeroY != null && (
            <line x1={0} x2={width} y1={zeroY} y2={zeroY} stroke="var(--border)" strokeWidth={1} strokeDasharray="3 3" />
          )}
          <path d={path} fill="none" stroke="var(--accent)" strokeWidth={1.6} strokeLinejoin="round" />
        </svg>
      </div>
      <div className="flex justify-between text-[10px] tabular text-[var(--muted)]">
        <span>min {geom.minV.toFixed(4)}</span>
        <span>{points.length} 点</span>
        <span>max {geom.maxV.toFixed(4)}</span>
      </div>
    </div>
  );
}
