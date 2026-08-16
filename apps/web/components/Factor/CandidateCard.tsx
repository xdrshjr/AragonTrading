"use client";

import clsx from "clsx";
import { Badge, Button } from "@/components/ui";
import { codeLabel, formatIc, phaseLabel, runStatusLabel, runStatusTone } from "@/lib/weightModel";
import { candidateLabel, candidateProgressPct } from "@/lib/weightBatch";
import type { CandidateState } from "@/lib/weightBatchTypes";
import TrainingCurveChart from "./TrainingCurveChart";
import WeightBars from "./WeightBars";

/**
 * 竞技场单候选卡：方法徽标 / 状态 / 进度+phase / 迷你 loss·IC 曲线 / 收敛中的权重条 / 单候选取消。
 * 最优候选（isBest）加奖杯高亮。状态迁移经 aria-live 播报（无障碍，§六.11）。
 */
export default function CandidateCard({
  state,
  isBest,
  onCancel,
}: {
  state: CandidateState;
  isBest: boolean;
  onCancel?: () => void;
}) {
  const pct = candidateProgressPct(state);
  const cancellable = state.status === "running" || state.status === "queued";
  const tone = runStatusTone(state.status);
  return (
    <div
      className={clsx(
        "card flex flex-col gap-2 p-3 transition-colors",
        isBest ? "border-[var(--long)] ring-1 ring-[var(--long)]/40" : "hover:border-[var(--accent-soft)]",
      )}
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          {isBest && <span title="当前最优候选" aria-label="最优候选">🏆</span>}
          <span className="truncate text-sm font-medium text-[var(--text)]">
            {candidateLabel(state.method, state.variantLabel)}
          </span>
        </div>
        <Badge tone={tone}>{runStatusLabel(state.status)}</Badge>
      </div>

      <div className="flex flex-col gap-1" role="status" aria-live="polite">
        <div className="flex items-center justify-between text-[11px] text-[var(--muted)]">
          <span>
            {state.status === "succeeded"
              ? "训练完成"
              : state.status === "failed"
                ? "训练失败"
                : state.status === "cancelled"
                  ? "已取消"
                  : state.status === "queued"
                    ? "排队等待名额…"
                    : state.phase
                      ? phaseLabel(state.phase.phase)
                      : "启动中…"}
          </span>
          <span className="tabular">{Math.round(pct)}%</span>
        </div>
        <div className="h-1.5 overflow-hidden rounded-full bg-[var(--surface-2)]">
          <div
            className="h-full rounded-full transition-all duration-300 ease-smooth"
            style={{
              width: `${pct}%`,
              background:
                state.status === "failed"
                  ? "var(--short)"
                  : state.status === "succeeded"
                    ? "var(--long)"
                    : "var(--accent)",
            }}
          />
        </div>
      </div>

      {state.metrics && (state.status === "succeeded" || state.result) && (
        <div className="flex flex-wrap items-center gap-1.5">
          <Badge tone="neutral">样本外 IC {formatIc(state.metrics.icOut)}</Badge>
          <Badge tone="neutral">样本内 IC {formatIc(state.metrics.icIn)}</Badge>
          <span className="text-[11px] text-[var(--muted)]">
            训练 {state.metrics.nTrain} · 验证 {state.metrics.nValid}
          </span>
        </div>
      )}

      {state.error && (
        <p
          role="alert"
          title={state.error.code}
          className="rounded-[8px] border border-[var(--short)]/40 bg-[var(--short)]/10 px-2 py-1 text-[11px] font-medium text-[var(--short-ink)]"
        >
          {codeLabel(state.error.code)}：{state.error.message}
        </p>
      )}

      {state.curve.length > 0 && (
        <div className="overflow-x-auto">
          <TrainingCurveChart curve={state.curve} />
        </div>
      )}

      {state.liveWeights && Object.keys(state.liveWeights).length > 0 && (
        <div className="flex flex-col gap-1">
          <span className="text-[11px] font-medium text-[var(--muted)]">
            {state.status === "succeeded" ? "最终权重" : "收敛中的权重"}
          </span>
          <WeightBars weights={state.liveWeights} />
        </div>
      )}

      {cancellable && onCancel && (
        <div className="flex justify-end">
          <Button variant="ghost" size="sm" onClick={onCancel}>
            取消该候选
          </Button>
        </div>
      )}
    </div>
  );
}
