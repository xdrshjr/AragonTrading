"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import clsx from "clsx";
import { useEffect, useState } from "react";
import { Badge, Button } from "@/components/ui";
import ConfirmDialog from "@/components/ui/ConfirmDialog";
import { ApiRequestError } from "@/lib/api";
import { directionLabel, formatIc } from "@/lib/factor";
import { fmtTime } from "@/lib/format";
import { codeLabel, latestRun, methodLabel, runIsBacktestable, runStatusLabel, runStatusTone, runToTrained, symbolsSummary } from "@/lib/weightModel";
import { weightModelApi } from "@/lib/weightModelApi";
import type { WeightModel, WeightTrainRun } from "@/lib/weightModelTypes";
import TrainingCurveChart from "./TrainingCurveChart";
import WeightBacktestPanel from "./WeightBacktestPanel";
import WeightBars from "./WeightBars";

const RUNS_PAGE = 50;   // G-F3：历史记录分页步长（避免重度训练模型一次性渲染数百行）

/**
 * 权重模型详情：概要（成员 / 方法 / 最佳验证 IC）+ 历史训练记录表（状态 / 方法 / 区间 / IC / 时间）+
 * 选中某 run 回放其 loss/IC 曲线与权重条。GET 模型 / 记录列表（TanStack Query），选中 run 拉完整 curve。
 */
export default function WeightModelDetail({
  modelId,
  autoBacktestRunId,
  onBack,
  onTrain,
  trainRange,
}: {
  modelId: number;
  autoBacktestRunId?: number;   // 卡片「回测」进入时自动选中并回测该记录（P1-2）；查看 / 训练入口缺省不传
  onBack: () => void;
  onTrain: (model: WeightModel) => void;
  trainRange?: { startTs: number; endTs: number };   // G-F7：训练新版本将用的工作台时间窗（panel 注入；缺省不显）
}) {
  const qc = useQueryClient();
  const [limit, setLimit] = useState(RUNS_PAGE);   // G-F3：分页 limit
  const model = useQuery({ queryKey: ["weight-models", modelId], queryFn: () => weightModelApi.get(modelId) });
  const runs = useQuery({
    queryKey: ["weight-models", modelId, "runs", { limit }],
    queryFn: () => weightModelApi.runs(modelId, { limit }),
    // G-F2：任一记录 running 时轮询刷新（4s）——打开他处发起训练的模型也能实时更新，否则永久停在「训练中」。
    refetchInterval: (q) => (q.state.data?.some((r) => r.status === "running") ? 4000 : false),
  });
  const [selectedRunId, setSelectedRunId] = useState<number | null>(null);
  const [pendingDeleteRunId, setPendingDeleteRunId] = useState<number | null>(null);   // H-F8：应用内确认
  const [deleteNotice, setDeleteNotice] = useState<string | null>(null);   // G-F4：删除失败应用内提示

  const deleteRun = async (runId: number) => {
    try {
      await weightModelApi.removeRun(modelId, runId);
      if (selectedRunId === runId) setSelectedRunId(null);
      void qc.invalidateQueries({ queryKey: ["weight-models", modelId] });
      void qc.invalidateQueries({ queryKey: ["weight-models", modelId, "runs"] });
      void qc.invalidateQueries({ queryKey: ["weight-models"] });   // G-F4：覆盖列表键，删最佳 / 最近 run 后卡片即时更新
    } catch (err) {
      // G-F4：删除失败（如 409 训练中）给应用内提示，绝不静默吞错。
      const code = (err as ApiRequestError)?.code;
      setDeleteNotice(code === "WEIGHT_RUN_RUNNING" ? "训练进行中，请先取消训练" : codeLabel(code));
    }
  };

  // 卡片「回测」进入：以 autoBacktestRunId 播种选中记录（先于默认最近记录，P1-2）。
  useEffect(() => {
    if (autoBacktestRunId != null) setSelectedRunId(autoBacktestRunId);
  }, [autoBacktestRunId]);

  // 默认选中最近一次记录（含未成功，便于看失败原因）；autoBacktestRunId 已播种时 selectedRunId 非空即跳过。
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
        <div className="flex items-center gap-2">
          {/* G-F7：呈现「训练新版本」将采用的工作台时间窗，便于用户核对日期段。 */}
          {trainRange && (
            <span className="text-[11px] text-[var(--muted)]">区间 {fmtTime(trainRange.startTs)} – {fmtTime(trainRange.endTs)}</span>
          )}
          <Button variant="primary" size="sm" disabled={!model.data} onClick={() => model.data && onTrain(model.data)}>训练新版本</Button>
        </div>
      </div>

      {model.data && (
        <div className="card flex flex-col gap-2 p-3">
          <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] text-[var(--muted)]">
            <span title={symbolsSummary(model.data.instId, model.data.symbols).title}>
              {symbolsSummary(model.data.instId, model.data.symbols).label} · {model.data.bar}
            </span>
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

      {deleteNotice && (
        <p role="alert" className="rounded-[10px] border border-[var(--short)]/40 bg-[var(--short)]/10 px-3 py-2 text-[11px] font-medium text-[var(--short)]">
          删除失败：{deleteNotice}
        </p>
      )}

      <RunHistoryTable
        runs={runs.data ?? []}
        loading={runs.isPending}
        error={runs.isError}
        errorMessage={(runs.error as Error)?.message}
        onRetry={() => void runs.refetch()}
        selectedId={selectedRunId}
        onSelect={setSelectedRunId}
        onRequestDelete={setPendingDeleteRunId}
        hasMore={(runs.data?.length ?? 0) >= limit}
        onLoadMore={() => setLimit((l) => l + RUNS_PAGE)}
      />

      {selectedRunId != null && (
        <RunReplay modelId={modelId} runId={selectedRunId} autoBacktestRunId={autoBacktestRunId} />
      )}

      <ConfirmDialog
        open={pendingDeleteRunId != null}
        title="删除训练记录"
        message="删除这条训练记录后将无法恢复，确定继续？"
        confirmText="删除"
        onConfirm={() => {
          if (pendingDeleteRunId != null) void deleteRun(pendingDeleteRunId);
          setPendingDeleteRunId(null);
        }}
        onCancel={() => setPendingDeleteRunId(null)}
      />
    </div>
  );
}

function RunHistoryTable({
  runs,
  loading,
  error,
  errorMessage,
  onRetry,
  selectedId,
  onSelect,
  onRequestDelete,
  hasMore,
  onLoadMore,
}: {
  runs: WeightTrainRun[];
  loading: boolean;
  error?: boolean;
  errorMessage?: string;
  onRetry?: () => void;
  selectedId: number | null;
  onSelect: (id: number) => void;
  onRequestDelete: (id: number) => void;
  hasMore?: boolean;
  onLoadMore?: () => void;
}) {
  if (loading) return <p className="py-6 text-center text-[11px] text-[var(--muted)]">加载训练记录...</p>;
  // H-F1：加载失败显式为错误态（含重试），绝不渲染成「没有记录」空状态误导用户。
  if (error) {
    return (
      <div className="card flex flex-col items-center gap-2 p-6 text-center">
        <span className="text-[11px] text-[var(--short)]">加载训练记录失败：{errorMessage ?? "未知错误"}</span>
        {onRetry && <Button variant="default" size="sm" onClick={onRetry}>重试</Button>}
      </div>
    );
  }
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
              role="button"
              tabIndex={0}
              aria-label={`训练记录 ${runStatusLabel(run.status)} ${methodLabel(run.method)}`}
              onClick={() => onSelect(run.id)}
              onKeyDown={(e) => {
                if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onSelect(run.id); }
              }}
              className={clsx(
                "cursor-pointer border-b border-[var(--border)] outline-none transition-colors focus-visible:bg-[var(--accent-soft)]/30",
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
                    onClick={(e) => { e.stopPropagation(); onRequestDelete(run.id); }}
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
      {hasMore && onLoadMore && (
        <div className="flex justify-center border-t border-[var(--border)] p-2">
          <Button variant="ghost" size="sm" onClick={onLoadMore}>加载更多</Button>
        </div>
      )}
    </div>
  );
}

function RunReplay({
  modelId,
  runId,
  autoBacktestRunId,
}: {
  modelId: number;
  runId: number;
  autoBacktestRunId?: number;
}) {
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
      // G-F6：错误码经 codeLabel 友好呈现，原始码作 title 保留排障线索。
      <p role="alert" title={data.errorCode ?? undefined} className="rounded-[10px] border border-[var(--short)]/40 bg-[var(--short)]/10 px-3 py-2 text-[11px] text-[var(--short)]">
        该次训练失败（{codeLabel(data.errorCode)}）：{data.errorMessage ?? "无详情"}
      </p>
    );
  }
  // H-F7：取消态专门呈现中性提示，不再含糊地展示「空权重回放」，与「成功但空权重」区分。
  if (data.status === "cancelled") {
    return (
      <p className="rounded-[10px] border border-[var(--border)] bg-[var(--surface-2)] px-3 py-2 text-[11px] text-[var(--muted)]">
        该次训练已取消（用户主动中止），未产出最终权重。
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
      {/* 「学习曲线回放」下方的 K 线回测区块：仅可回测记录渲染（失败 / 取消 / 空权重经门禁上方已早退，绝不出空图）。 */}
      {runIsBacktestable(data) && (
        <WeightBacktestPanel modelId={modelId} runId={runId} autoRun={runId === autoBacktestRunId} />
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
