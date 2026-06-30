"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import clsx from "clsx";
import { useEffect, useState } from "react";
import { Badge, Button } from "@/components/ui";
import { directionLabel, formatIc } from "@/lib/factor";
import { fmtTime } from "@/lib/format";
import { latestRun, methodLabel, runStatusLabel, runStatusTone, runToTrained } from "@/lib/weightModel";
import { weightModelApi } from "@/lib/weightModelApi";
import type { WeightModel, WeightTrainRun } from "@/lib/weightModelTypes";
import TrainingCurveChart from "./TrainingCurveChart";
import WeightBars from "./WeightBars";

/**
 * 权重模型详情：概要（成员 / 方法 / 最佳验证 IC）+ 历史训练记录表（状态 / 方法 / 区间 / IC / 时间）+
 * 选中某 run 回放其 loss/IC 曲线与权重条。GET 模型 / 记录列表（TanStack Query），选中 run 拉完整 curve。
 */
export default function WeightModelDetail({
  modelId,
  onBack,
  onTrain,
}: {
  modelId: number;
  onBack: () => void;
  onTrain: (model: WeightModel) => void;
}) {
  const qc = useQueryClient();
  const model = useQuery({ queryKey: ["weight-models", modelId], queryFn: () => weightModelApi.get(modelId) });
  const runs = useQuery({ queryKey: ["weight-models", modelId, "runs"], queryFn: () => weightModelApi.runs(modelId) });
  const [selectedRunId, setSelectedRunId] = useState<number | null>(null);

  // 默认选中最近一次记录（含未成功，便于看失败原因）。
  useEffect(() => {
    if (selectedRunId == null && runs.data && runs.data.length > 0) {
      setSelectedRunId(latestRun(runs.data)?.id ?? null);
    }
  }, [runs.data, selectedRunId]);

  if (model.isError) {
    return <ErrorState message={(model.error as Error)?.message} onBack={onBack} />;
  }

  return (
    <div className="flex flex-col gap-4 pb-6">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <Button variant="ghost" size="sm" onClick={onBack}>← 返回列表</Button>
          <span className="text-sm font-semibold text-[var(--text)]">{model.data?.name ?? "模型详情"}</span>
          {model.data && <Badge tone="accent">{methodLabel(model.data.method)}</Badge>}
        </div>
        <Button variant="primary" size="sm" disabled={!model.data} onClick={() => model.data && onTrain(model.data)}>训练新版本</Button>
      </div>

      {model.data && (
        <div className="card flex flex-col gap-2 p-3">
          <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] text-[var(--muted)]">
            <span>{model.data.instId} · {model.data.bar}</span>
            <span>{model.data.members.length} 成员</span>
            <span>前瞻 {model.data.horizon} 根</span>
            <span>最佳验证 IC <span className="text-[var(--text)]">{formatIc(model.data.bestValidIc)}</span></span>
            <span>更新于 {fmtTime(model.data.updatedTs)}</span>
            {model.data.factorSetId != null && <Badge tone="neutral">关联组合 #{model.data.factorSetId}</Badge>}
          </div>
          <div className="flex flex-wrap gap-1">
            {model.data.members.map((m, i) => (
              <Badge key={`${m.factorKey}-${i}`} tone={m.direction === "short" ? "short" : "long"}>
                {m.factorKey}·{directionLabel(m.direction)}
              </Badge>
            ))}
          </div>
          {model.data.notes && <p className="text-[11px] text-[var(--muted)]">{model.data.notes}</p>}
        </div>
      )}

      <RunHistoryTable
        runs={runs.data ?? []}
        loading={runs.isPending}
        selectedId={selectedRunId}
        onSelect={setSelectedRunId}
        onDelete={async (runId) => {
          await weightModelApi.removeRun(modelId, runId);
          if (selectedRunId === runId) setSelectedRunId(null);
          void qc.invalidateQueries({ queryKey: ["weight-models", modelId] });
          void qc.invalidateQueries({ queryKey: ["weight-models", modelId, "runs"] });
        }}
      />

      {selectedRunId != null && <RunReplay modelId={modelId} runId={selectedRunId} />}
    </div>
  );
}

function RunHistoryTable({
  runs,
  loading,
  selectedId,
  onSelect,
  onDelete,
}: {
  runs: WeightTrainRun[];
  loading: boolean;
  selectedId: number | null;
  onSelect: (id: number) => void;
  onDelete: (id: number) => void;
}) {
  if (loading) return <p className="py-6 text-center text-[11px] text-[var(--muted)]">加载训练记录...</p>;
  if (runs.length === 0) {
    return (
      <div className="card flex flex-col items-center gap-2 p-6 text-center">
        <span className="text-[11px] text-[var(--muted)]">该模型还没有训练记录，点「训练新版本」开始一次训练。</span>
      </div>
    );
  }
  return (
    <div className="card overflow-x-auto p-0">
      <table className="w-full text-left text-[11px]">
        <thead className="text-[var(--muted)]">
          <tr className="border-b border-[var(--border)]">
            <th className="px-3 py-2 font-medium">状态</th>
            <th className="px-3 py-2 font-medium">方法</th>
            <th className="px-3 py-2 font-medium">区间</th>
            <th className="px-3 py-2 font-medium tabular">样本外 IC</th>
            <th className="px-3 py-2 font-medium">时间</th>
            <th className="px-3 py-2" />
          </tr>
        </thead>
        <tbody>
          {runs.map((run) => (
            <tr
              key={run.id}
              onClick={() => onSelect(run.id)}
              className={clsx(
                "cursor-pointer border-b border-[var(--border)] transition-colors",
                selectedId === run.id ? "bg-[var(--accent-soft)]/40" : "hover:bg-[var(--surface-2)]",
              )}
            >
              <td className="px-3 py-2"><Badge tone={runStatusTone(run.status)}>{runStatusLabel(run.status)}</Badge></td>
              <td className="px-3 py-2 text-[var(--muted)]">{methodLabel(run.method)}</td>
              <td className="px-3 py-2 text-[var(--muted)]">{fmtTime(run.startTs)} → {fmtTime(run.endTs)}</td>
              <td className="px-3 py-2 tabular text-[var(--text)]">{formatIc(run.metrics?.icOut ?? null)}</td>
              <td className="px-3 py-2 text-[var(--muted)]">{run.finishedTs ? fmtTime(run.finishedTs) : "—"}</td>
              <td className="px-3 py-2 text-right">
                {run.status !== "running" && (
                  <button
                    type="button"
                    onClick={(e) => { e.stopPropagation(); if (window.confirm("删除这条训练记录？")) onDelete(run.id); }}
                    className="text-[var(--muted)] underline hover:text-[var(--short)]"
                  >
                    删除
                  </button>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function RunReplay({ modelId, runId }: { modelId: number; runId: number }) {
  const run = useQuery({
    queryKey: ["weight-models", modelId, "runs", runId],
    queryFn: () => weightModelApi.run(modelId, runId),
  });
  if (run.isPending) return <p className="py-4 text-center text-[11px] text-[var(--muted)]">加载曲线...</p>;
  if (run.isError) {
    return <p className="text-[11px] text-[var(--short)]">加载训练记录失败：{(run.error as Error)?.message}</p>;
  }
  const data = run.data;
  if (data.status === "failed") {
    return (
      <p className="rounded-[10px] border border-[var(--short)]/40 bg-[var(--short)]/10 px-3 py-2 text-[11px] text-[var(--short)]">
        该次训练失败（{data.errorCode ?? "ERROR"}）：{data.errorMessage ?? "无详情"}
      </p>
    );
  }
  const trained = runToTrained(data);
  return (
    <div className="flex flex-col gap-3">
      <div className="card flex flex-col gap-2 p-3">
        <span className="text-[11px] font-medium text-[var(--muted)]">学习曲线回放</span>
        <TrainingCurveChart curve={data.curve ?? []} />
      </div>
      {trained && (
        <div className="card flex flex-col gap-2 p-3">
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-[11px] font-medium text-[var(--muted)]">训练权重</span>
            <Badge tone="neutral">样本内 IC {formatIc(data.metrics?.icIn ?? null)}</Badge>
            <Badge tone="neutral">样本外 IC {formatIc(data.metrics?.icOut ?? null)}</Badge>
            {data.trainerVersion && <Badge tone="neutral">{data.trainerVersion}</Badge>}
          </div>
          <WeightBars weights={data.weights} />
        </div>
      )}
    </div>
  );
}

function ErrorState({ message, onBack }: { message?: string; onBack: () => void }) {
  return (
    <div className="flex flex-col items-center gap-2 py-8">
      <p className="text-[11px] text-[var(--short)]">加载模型失败：{message}</p>
      <Button variant="default" size="sm" onClick={onBack}>返回列表</Button>
    </div>
  );
}
