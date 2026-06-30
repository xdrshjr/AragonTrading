"use client";

import { useMemo } from "react";
import { hasCurveData, metricGeometry, plotPath } from "@/lib/weightModel";
import type { TrainCurvePoint } from "@/lib/weightModelTypes";

/**
 * 扩样本学习曲线（自绘 SVG）：上「拟合损失（越低越好）」+ 下「RankIC（越高越好）」两面板，
 * 各含 train（虚线·弱色）与 valid（实线·强色）两序列。null 点断线（缺测显式，绝不补零）；
 * 配色全用设计 token，明暗主题零适配（weight-model-workspace §3.3）。
 */
export default function TrainingCurveChart({ curve }: { curve: TrainCurvePoint[] }) {
  if (!hasCurveData(curve)) {
    return (
      <div className="flex h-24 items-center justify-center rounded-[10px] border border-dashed border-[var(--border)] text-[11px] text-[var(--muted)]">
        暂无曲线数据点（样本不足时该指标记为缺测，不补零）
      </div>
    );
  }
  return (
    <div className="flex flex-col gap-3">
      <MetricPanel curve={curve} title="拟合损失" hint="越低越好" trainKey="trainLoss" validKey="validLoss" />
      <MetricPanel curve={curve} title="RankIC（复合得分↔前瞻收益）" hint="越高越好" trainKey="trainIc" validKey="validIc" />
    </div>
  );
}

function MetricPanel({
  curve,
  title,
  hint,
  trainKey,
  validKey,
}: {
  curve: TrainCurvePoint[];
  title: string;
  hint: string;
  trainKey: "trainLoss" | "trainIc";
  validKey: "validLoss" | "validIc";
}) {
  const width = 560;
  const height = 110;
  const geom = useMemo(
    () => metricGeometry(curve, trainKey, validKey, width, height),
    [curve, trainKey, validKey],
  );
  const trainPath = plotPath(geom.trainPath);
  const validPath = plotPath(geom.validPath);

  return (
    <div className="flex flex-col gap-1">
      <div className="flex items-center justify-between">
        <span className="text-[11px] font-medium text-[var(--muted)]">
          {title} <span className="opacity-70">· {hint}</span>
        </span>
        <span className="flex items-center gap-3 text-[10px] text-[var(--muted)]">
          <span className="flex items-center gap-1">
            <span className="inline-block h-0 w-4 border-t border-dashed border-[var(--muted)]" />训练段
          </span>
          <span className="flex items-center gap-1">
            <span className="inline-block h-[2px] w-4 rounded bg-[var(--accent)]" />验证段
          </span>
        </span>
      </div>
      <div className="overflow-hidden rounded-[10px] border border-[var(--border)] bg-[var(--surface)]">
        <svg
          viewBox={`0 0 ${width} ${height}`}
          className="h-auto w-full"
          preserveAspectRatio="none"
          role="img"
          aria-label={`${title}学习曲线`}
        >
          {trainPath && (
            <path
              d={trainPath}
              fill="none"
              stroke="var(--muted)"
              strokeWidth={1.4}
              strokeDasharray="4 3"
              strokeLinejoin="round"
            />
          )}
          {validPath && (
            <path d={validPath} fill="none" stroke="var(--accent)" strokeWidth={1.8} strokeLinejoin="round" />
          )}
          {geom.validPath.map((p, i) => (
            <circle key={i} cx={p.x} cy={p.y} r={1.8} fill="var(--accent)" />
          ))}
        </svg>
      </div>
      <div className="flex justify-between text-[10px] tabular text-[var(--muted)]">
        <span>min {geom.min.toFixed(3)}</span>
        <span>样本占比 → 增大</span>
        <span>max {geom.max.toFixed(3)}</span>
      </div>
    </div>
  );
}
