"use client";

import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import type { EquityPoint } from "@/lib/types";

// SVG viewBox 单位（preserveAspectRatio="none" 令其横向拉伸铺满容器，纵向由 h-* 决定）。
const VW = 100;
const VH = 32;

// 纯几何映射：净值序列 → viewBox 归一坐标（min→底、max→顶）。少于 2 点无法成线。
function toPath(points: EquityPoint[]): { line: string; area: string; up: boolean } | null {
  if (points.length < 2) return null;
  const values = points.map((p) => p.equity);
  const min = Math.min(...values);
  const max = Math.max(...values);
  const span = max - min || 1;
  const last = points.length - 1;
  const coords = points.map((p, i) => {
    const x = (i / last) * VW;
    const y = VH - ((p.equity - min) / span) * VH;
    return `${x.toFixed(2)},${y.toFixed(2)}`;
  });
  const line = coords.map((c, i) => `${i === 0 ? "M" : "L"}${c}`).join(" ");
  const area = `${line} L${VW},${VH} L0,${VH} Z`;
  return { line, area, up: values[last] >= values[0] };
}

/**
 * 净值曲线缩略图（strategy-backtest-config-modal §2.4）：`api.equity(runId)` → 纯几何映射自绘 SVG。
 *
 * queryKey `["equity", runId]` 与 StrategyPanel 的净值查询同键，共享 react-query 缓存（不重复取数）。
 * 上涨着 `var(--long)`、下跌着 `var(--short)`，明暗主题自适应；数据不足 2 点渲染占位空条。
 */
export default function EquitySparkline({ runId }: { runId: number }) {
  const { data } = useQuery({
    queryKey: ["equity", runId],
    queryFn: () => api.equity(runId),
    enabled: !!runId,
  });
  const path = toPath(data ?? []);
  if (!path) {
    return (
      <div className="flex h-14 items-center justify-center rounded-[10px] border border-dashed border-[var(--border)] text-[11px] text-[var(--muted)]">
        净值曲线加载中…
      </div>
    );
  }
  const color = path.up ? "var(--long)" : "var(--short)";
  return (
    <svg
      viewBox={`0 0 ${VW} ${VH}`}
      preserveAspectRatio="none"
      className="h-14 w-full rounded-[10px] border border-[var(--border)] bg-[var(--surface-2)]"
      role="img"
      aria-label="回测净值曲线缩略图"
    >
      <path d={path.area} fill={color} fillOpacity={0.12} />
      <path
        d={path.line}
        fill="none"
        stroke={color}
        strokeWidth={1.5}
        strokeLinejoin="round"
        strokeLinecap="round"
        vectorEffect="non-scaling-stroke"
      />
    </svg>
  );
}
