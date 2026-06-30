"use client";

import { useMemo, useState } from "react";
import { formatIc, formatLoss, hasCurveData, metricGeometry, plotPath } from "@/lib/weightModel";
import type { TrainCurvePoint } from "@/lib/weightModelTypes";

const WIDTH = 560;
const HEIGHT = 110;
const PAD = 6;

/**
 * 扩样本学习曲线（自绘 SVG）：上「拟合损失（越低越好）」+ 下「RankIC（越高越好）」两面板，
 * 各含 train（虚线·弱色）与 valid（实线·强色）两序列。null 点断线（缺测显式，绝不补零）；
 * 等比缩放（去 preserveAspectRatio="none" 拉伸变形，H-F9）、悬停 tooltip、y 轴数值刻度、轴标签本地化；
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
  const [hovered, setHovered] = useState<number | null>(null);
  const geom = useMemo(() => metricGeometry(curve, trainKey, validKey, WIDTH, HEIGHT, PAD), [curve, trainKey, validKey]);
  const trainPath = plotPath(geom.trainPath);
  const validPath = plotPath(geom.validPath);
  const fracs = curve.map((p) => p.fraction);
  const fmin = Math.min(...fracs);
  const fspan = Math.max(...fracs) - fmin || 1;
  const innerW = Math.max(1, WIDTH - PAD * 2);
  const xAt = (i: number) => PAD + ((curve[i].fraction - fmin) / fspan) * innerW;

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
      <div className="relative overflow-hidden rounded-[10px] border border-[var(--border)] bg-[var(--surface)]">
        <svg viewBox={`0 0 ${WIDTH} ${HEIGHT}`} className="h-auto w-full" role="img" aria-label={`${title}学习曲线`}>
          <text x={PAD} y={PAD + 8} className="fill-[var(--muted)]" fontSize={9}>{geom.max.toFixed(3)}</text>
          <text x={PAD} y={HEIGHT - PAD} className="fill-[var(--muted)]" fontSize={9}>{geom.min.toFixed(3)}</text>
          {trainPath && (
            <path d={trainPath} fill="none" stroke="var(--muted)" strokeWidth={1.4} strokeDasharray="4 3" strokeLinejoin="round" />
          )}
          {validPath && <path d={validPath} fill="none" stroke="var(--accent)" strokeWidth={1.8} strokeLinejoin="round" />}
          {geom.validPath.map((p, i) => (
            <circle key={i} cx={p.x} cy={p.y} r={1.8} fill="var(--accent)" />
          ))}
          {hovered != null && (
            <line x1={xAt(hovered)} x2={xAt(hovered)} y1={PAD} y2={HEIGHT - PAD} stroke="var(--accent)" strokeWidth={0.8} strokeDasharray="2 2" opacity={0.6} />
          )}
          {curve.map((_, i) => (
            <rect
              key={i}
              x={Math.max(0, xAt(i) - innerW / (2 * curve.length))}
              y={0}
              width={innerW / curve.length}
              height={HEIGHT}
              fill="transparent"
              onMouseEnter={() => setHovered(i)}
              onMouseLeave={() => setHovered(null)}
            />
          ))}
        </svg>
        {hovered != null && <CurveTooltip point={curve[hovered]} leftPct={(xAt(hovered) / WIDTH) * 100} />}
      </div>
      <div className="flex justify-between text-[10px] tabular text-[var(--muted)]">
        <span>最小 {geom.min.toFixed(3)}</span>
        <span>样本占比 → 增大</span>
        <span>最大 {geom.max.toFixed(3)}</span>
      </div>
    </div>
  );
}

// 悬停 tooltip：呈现该步 fraction / 训练样本 / 训练·验证损失 / 训练·验证 IC（缺测显式为 —）。
function CurveTooltip({ point, leftPct }: { point: TrainCurvePoint; leftPct: number }) {
  const side = leftPct > 60 ? { right: `${100 - leftPct}%` } : { left: `${leftPct}%` };
  return (
    <div
      className="pointer-events-none absolute top-1 z-10 flex flex-col gap-0.5 rounded-[8px] border border-[var(--border)] bg-[var(--bg)] px-2 py-1 text-[10px] tabular text-[var(--text)] shadow-soft"
      style={side}
    >
      <span className="text-[var(--muted)]">样本占比 {(point.fraction * 100).toFixed(0)}% · 训练样本 {point.nTrain}</span>
      <span>训练损失 {formatLoss(point.trainLoss)} · 验证损失 {formatLoss(point.validLoss)}</span>
      <span>训练 IC {formatIc(point.trainIc)} · 验证 IC {formatIc(point.validIc)}</span>
    </div>
  );
}
