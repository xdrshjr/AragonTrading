"use client";

import clsx from "clsx";
import { formatIc } from "@/lib/factor";
import { candidateIcOut, candidateProgressPct, methodLabel } from "@/lib/weightBatch";
import type { CandidateState } from "@/lib/weightBatchTypes";
import type { ExploreMatrix as ExploreMatrixShape } from "@/lib/weightExplore";

// 样本外 IC 归一化基准（IC 量级通常 ≤ 0.1）：|icOut|/BASE 夹到 [0,1] 决定单元格上色强度。
const IC_INTENSITY_BASE = 0.1;

/**
 * 组合×方法矩阵热力图（本特性签名可视化，factor-combo-exploration §2.7）：行=组合、列=方法，单元格 = 该
 * (组合,方法) 候选态。succeeded 按 icOut 发散上色（负→--short、正→--long，强度按 |icOut| 归一）并标注 IC；
 * queued 灰底、running 脉冲 + 进度、failed ✕、cancelled 中性；最佳单元格 🏆 + ring。点击单元格开候选详情抽屉。
 */
export default function ExploreMatrix({
  matrix,
  bestRunId,
  onCell,
}: {
  matrix: ExploreMatrixShape;
  bestRunId: number | null;
  onCell?: (state: CandidateState) => void;
}) {
  return (
    <div className="card overflow-x-auto p-0">
      <table className="w-full border-collapse text-left text-[11px]">
        <thead className="text-[var(--muted)]">
          <tr className="border-b border-[var(--border)]">
            <th className="sticky left-0 z-10 bg-[var(--surface)] px-3 py-2 font-medium">组合 \ 方法</th>
            {matrix.cols.map((m) => (
              <th key={m} className="px-2 py-2 text-center font-medium">
                {methodLabel(m)}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {matrix.rows.map((row) => (
            <tr key={row.comboId} className="border-b border-[var(--border)] last:border-0">
              <th
                scope="row"
                className="sticky left-0 z-10 max-w-[200px] bg-[var(--surface)] px-3 py-2 text-left font-normal"
              >
                <span className="block truncate text-[var(--text)]" title={row.label}>
                  {row.label}
                </span>
                <span className="text-[10px] text-[var(--muted)]">{row.memberCount} 因子</span>
              </th>
              {matrix.cols.map((method) => {
                const cell = matrix.cells[`${row.comboId}:${method}`];
                return (
                  <td key={method} className="p-1">
                    <MatrixCell state={cell} isBest={!!cell && cell.runId === bestRunId} onClick={onCell} />
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// 发散上色：icOut 正→--long、负→--short，强度按 |icOut| 归一混入 --surface（color-mix，现代浏览器 / Electron 支持）。
function icBackground(ic: number): string {
  const intensity = Math.round(Math.min(1, Math.abs(ic) / IC_INTENSITY_BASE) * 65) + 12; // 12%..77%
  const token = ic >= 0 ? "--long" : "--short";
  return `color-mix(in srgb, var(${token}) ${intensity}%, var(--surface))`;
}

function MatrixCell({
  state,
  isBest,
  onClick,
}: {
  state: CandidateState | null;
  isBest: boolean;
  onClick?: (state: CandidateState) => void;
}) {
  if (!state) {
    return <div aria-hidden className="h-14 rounded-[8px] border border-dashed border-[var(--border)]/60" />;
  }
  const ic = candidateIcOut(state);
  const succeeded = state.status === "succeeded";
  const style = succeeded && ic != null ? { backgroundColor: icBackground(ic) } : undefined;
  return (
    <button
      type="button"
      onClick={() => onClick?.(state)}
      title={`${state.variantLabel} · ${state.method}`}
      style={style}
      className={clsx(
        "flex h-14 w-full flex-col items-center justify-center gap-0.5 rounded-[8px] border px-1 text-center transition-all",
        state.status === "queued" && "border-[var(--border)] bg-[var(--surface-2)] text-[var(--muted)]",
        state.status === "running" && "animate-pulse border-[var(--accent)]/50 bg-[var(--accent-soft)]/40 text-[var(--accent-ink)]",
        state.status === "failed" && "border-[var(--short)]/40 bg-[var(--short)]/10 text-[var(--short-ink)]",
        state.status === "cancelled" && "border-[var(--border)] bg-[var(--surface-2)] text-[var(--muted)]",
        succeeded && "border-transparent text-[var(--text)]",
        isBest && "ring-2 ring-[var(--long)]/50",
      )}
    >
      <MatrixCellBody state={state} ic={ic} isBest={isBest} />
    </button>
  );
}

function MatrixCellBody({ state, ic, isBest }: { state: CandidateState; ic: number | null; isBest: boolean }) {
  if (state.status === "succeeded") {
    return (
      <>
        <span className="text-[13px]">{isBest ? "🏆" : ""}</span>
        <span className="tabular text-[11px] font-semibold">{formatIc(ic)}</span>
      </>
    );
  }
  if (state.status === "running") {
    return (
      <>
        <span className="text-[10px]">训练中</span>
        <span className="tabular text-[10px]">{Math.round(candidateProgressPct(state))}%</span>
      </>
    );
  }
  if (state.status === "queued") return <span className="text-[10px]">排队</span>;
  if (state.status === "failed") return <span className="text-[13px]">✕</span>;
  return <span className="text-[13px]">—</span>;
}
