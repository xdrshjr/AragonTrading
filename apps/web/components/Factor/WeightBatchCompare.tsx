"use client";

import clsx from "clsx";
import { Badge, Button } from "@/components/ui";
import { formatIc } from "@/lib/factor";
import { hasCurveData, metricGeometry, plotPath } from "@/lib/weightModel";
import { candidateLabel } from "@/lib/weightBatch";
import type { CandidateState, WeightBatchRankRow } from "@/lib/weightBatchTypes";
import { runStatusLabel, runStatusTone } from "@/lib/weightModel";

// 叠加曲线用色（明暗主题皆可辨；超出 6 序列循环取色，legend 已含方法名消歧）。
const SERIES_COLORS = [
  "var(--long)",
  "var(--accent)",
  "var(--short)",
  "#8b5cf6",
  "#f59e0b",
  "#06b6d4",
];

/**
 * 排行榜（按样本外 IC 排名、最优高亮奖杯、Δvs#1、行内 查看/择优/应用）+ 多候选验证 IC 叠加曲线。
 * 行键盘可达（Enter/Space 选中，复刻 WeightModelDetail 内私有 RunHistoryTable 的键盘可达模式，R10）。
 */
export default function WeightBatchCompare({
  ranking,
  states,
  selectedRunId,
  canApply,
  onView,
  onSelect,
  onApply,
}: {
  ranking: WeightBatchRankRow[];
  states: CandidateState[];
  selectedRunId: number | null;
  canApply: boolean;
  onView: (runId: number) => void;
  onSelect: (runId: number) => void;
  onApply: (runId: number) => void;
}) {
  const best = ranking.find((r) => r.isBest) ?? null;
  return (
    <div className="flex flex-col gap-3">
      <ValidIcOverlay states={states} />
      <div className="card overflow-x-auto p-0">
        <table className="w-full text-left text-[11px]">
          <thead className="text-[var(--muted)]">
            <tr className="border-b border-[var(--border)]">
              <th className="px-3 py-2 font-medium">排名</th>
              <th className="px-3 py-2 font-medium">候选</th>
              <th className="px-3 py-2 font-medium">状态</th>
              <th className="px-3 py-2 font-medium tabular">样本外 IC</th>
              <th className="px-3 py-2 font-medium tabular">Δ vs #1</th>
              <th className="px-3 py-2" />
            </tr>
          </thead>
          <tbody>
            {ranking.map((row, idx) => {
              const scored = row.status === "succeeded" && row.icOut != null;
              const delta =
                scored && best?.icOut != null ? (row.icOut as number) - best.icOut : null;
              return (
                <tr
                  key={row.runId}
                  role="button"
                  tabIndex={0}
                  aria-label={`候选 ${candidateLabel(row.method, row.variantLabel)} ${runStatusLabel(row.status)}`}
                  onClick={() => onView(row.runId)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" || e.key === " ") {
                      e.preventDefault();
                      onView(row.runId);
                    }
                  }}
                  className={clsx(
                    "cursor-pointer border-b border-[var(--border)] outline-none transition-colors focus-visible:bg-[var(--accent-soft)]/30",
                    row.isBest ? "bg-[var(--long)]/10" : "hover:bg-[var(--surface-2)]",
                  )}
                >
                  <td className="px-3 py-2 tabular">
                    {row.isBest ? "🏆 1" : scored ? idx + 1 : "—"}
                  </td>
                  <td className="px-3 py-2 text-[var(--text)]">
                    {candidateLabel(row.method, row.variantLabel)}
                    {selectedRunId === row.runId && (
                      <Badge tone="accent" >已选</Badge>
                    )}
                  </td>
                  <td className="px-3 py-2">
                    <Badge tone={runStatusTone(row.status)}>{runStatusLabel(row.status)}</Badge>
                  </td>
                  <td className="px-3 py-2 tabular text-[var(--text)]">{formatIc(row.icOut)}</td>
                  <td className="px-3 py-2 tabular text-[var(--muted)]">
                    {delta == null ? "—" : delta === 0 ? "基准" : delta.toFixed(4)}
                  </td>
                  <td className="px-3 py-2">
                    <div className="flex flex-wrap justify-end gap-1.5">
                      <button
                        type="button"
                        onClick={(e) => {
                          e.stopPropagation();
                          onView(row.runId);
                        }}
                        className="text-[var(--muted)] underline hover:text-[var(--accent)]"
                      >
                        查看/回测
                      </button>
                      <button
                        type="button"
                        disabled={!scored}
                        onClick={(e) => {
                          e.stopPropagation();
                          onSelect(row.runId);
                        }}
                        className={clsx(
                          "underline",
                          scored
                            ? "text-[var(--muted)] hover:text-[var(--accent)]"
                            : "cursor-not-allowed text-[var(--border)]",
                        )}
                      >
                        设为最佳
                      </button>
                      {canApply && (
                        <button
                          type="button"
                          disabled={!scored}
                          onClick={(e) => {
                            e.stopPropagation();
                            onApply(row.runId);
                          }}
                          className={clsx(
                            "underline",
                            scored
                              ? "text-[var(--muted)] hover:text-[var(--long)]"
                              : "cursor-not-allowed text-[var(--border)]",
                          )}
                        >
                          应用到组合
                        </button>
                      )}
                    </div>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}

// 多候选「验证段 RankIC」叠加曲线：各候选一条实线（色循环），共享 [min,max] 纵轴，legend=方法·变体。
function ValidIcOverlay({ states }: { states: CandidateState[] }) {
  const series = states
    .map((s, i) => ({ state: s, color: SERIES_COLORS[i % SERIES_COLORS.length] }))
    .filter((x) => hasCurveData(x.state.curve));
  if (series.length === 0) return null;
  const width = 560;
  const height = 120;
  return (
    <div className="card flex flex-col gap-2 p-3">
      <span className="text-[11px] font-medium text-[var(--muted)]">
        各候选验证段 RankIC 叠加（越高越好，实时对比收敛）
      </span>
      <div className="overflow-x-auto">
        <svg viewBox={`0 0 ${width} ${height}`} className="w-full" style={{ maxWidth: width }}>
          {series.map(({ state, color }) => {
            // 复用 metricGeometry 取 validIc 序列（trainKey 传同 validKey，只画 valid 一条）。
            const geom = metricGeometry(state.curve, "validIc", "validIc", width, height);
            return (
              <path
                key={state.runId}
                d={plotPath(geom.validPath)}
                fill="none"
                stroke={color}
                strokeWidth={1.5}
              />
            );
          })}
        </svg>
      </div>
      <div className="flex flex-wrap gap-x-3 gap-y-1">
        {series.map(({ state, color }) => (
          <span key={state.runId} className="flex items-center gap-1 text-[10px] text-[var(--muted)]">
            <span className="inline-block h-2 w-3 rounded-sm" style={{ background: color }} />
            {candidateLabel(state.method, state.variantLabel)}
          </span>
        ))}
      </div>
    </div>
  );
}
