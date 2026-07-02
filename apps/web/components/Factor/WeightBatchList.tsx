"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Badge, Button } from "@/components/ui";
import ConfirmDialog from "@/components/ui/ConfirmDialog";
import { ApiRequestError } from "@/lib/api";
import { formatIc } from "@/lib/factor";
import { fmtTime } from "@/lib/format";
import { candidateLabel } from "@/lib/weightBatch";
import { codeLabel } from "@/lib/weightModel";
import { weightBatchApi } from "@/lib/weightBatchApi";
import type { WeightBatchStatus, WeightBatchSummary } from "@/lib/weightBatchTypes";

const STATUS_LABEL: Record<WeightBatchStatus, string> = {
  running: "训练中",
  succeeded: "已完成",
  failed: "全部失败",
  cancelled: "已取消",
};

const STATUS_TONE: Record<WeightBatchStatus, "long" | "short" | "accent" | "neutral"> = {
  running: "accent",
  succeeded: "long",
  failed: "short",
  cancelled: "neutral",
};

/**
 * 历史批次列表（「权重模型」区「训练批次」tab）：卡片呈现名称 / 标的·周期 / 状态 / 候选数 / 最优 IC / 时间；
 * 查看（进竞技场回放）/ 删除（级联删父模型+候选+批次行，应用内二次确认）。running 批次轮询刷新。
 */
export default function WeightBatchList({ onOpen }: { onOpen: (batchId: number) => void }) {
  const qc = useQueryClient();
  const [pendingDelete, setPendingDelete] = useState<WeightBatchSummary | null>(null);
  const [deleteNotice, setDeleteNotice] = useState<string | null>(null);
  const batches = useQuery({
    queryKey: ["weight-batches"],
    queryFn: () => weightBatchApi.list(),
    refetchInterval: (q) => ((q.state.data ?? []).some((b) => b.status === "running") ? 4000 : false),
  });
  const remove = useMutation({
    mutationFn: (id: number) => weightBatchApi.remove(id),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["weight-batches"] }),
    onError: (err) => {
      const code = (err as ApiRequestError)?.code;
      setDeleteNotice(code === "WEIGHT_BATCH_RUNNING" ? "批次仍在训练 / 排队，请先取消批次" : codeLabel(code));
    },
  });

  if (batches.isPending) {
    return <p className="py-8 text-center text-[11px] text-[var(--muted)]">加载训练批次...</p>;
  }
  if (batches.isError) {
    return (
      <div className="flex flex-col items-center gap-2 py-8">
        <p className="text-[11px] text-[var(--short)]">加载训练批次失败：{(batches.error as Error)?.message}</p>
        <Button variant="default" size="sm" onClick={() => batches.refetch()}>重试</Button>
      </div>
    );
  }

  const items = batches.data ?? [];
  if (items.length === 0) {
    return (
      <div className="card flex flex-col items-center gap-2 p-8 text-center">
        <span className="text-sm font-medium text-[var(--text)]">还没有训练批次</span>
        <span className="text-[11px] text-[var(--muted)]">
          从「权重训练与组合」选好成员后点「多模型训练」，即可一次并行训练多个方法并择优。
        </span>
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-3">
      {deleteNotice && (
        <p role="alert" className="rounded-[10px] border border-[var(--short)]/40 bg-[var(--short)]/10 px-3 py-2 text-[11px] font-medium text-[var(--short)]">
          删除失败：{deleteNotice}
        </p>
      )}
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-3">
        {items.map((batch) => (
          <div key={batch.batchId} className="card flex flex-col gap-2 p-3 transition-colors hover:border-[var(--accent-soft)]">
            <button type="button" onClick={() => onOpen(batch.batchId)} className="flex flex-col gap-1 text-left">
              <div className="flex items-center gap-2">
                <span className="truncate text-sm font-medium text-[var(--text)]">{batch.label}</span>
                {batch.kind === "explore" && <Badge tone="accent">探索</Badge>}
                <Badge tone={STATUS_TONE[batch.status]}>{STATUS_LABEL[batch.status]}</Badge>
              </div>
              <div className="flex flex-wrap items-center gap-x-2 gap-y-0.5 text-[11px] text-[var(--muted)]">
                <span>{batch.instId} · {batch.bar}</span>
                {batch.kind === "explore" && batch.comboCount != null && batch.methodCount != null && (
                  <span>{batch.comboCount} 组合 × {batch.methodCount} 方法</span>
                )}
                <span>{batch.succeededCount}/{batch.candidateCount} 成功</span>
                {batch.best && (
                  <span>最优 {candidateLabel(batch.best.method, batch.best.variantLabel)} · IC <span className="text-[var(--text)]">{formatIc(batch.best.icOut)}</span></span>
                )}
              </div>
              <span className="text-[11px] text-[var(--muted)]">更新于 {fmtTime(batch.updatedTs)}</span>
            </button>
            <div className="flex flex-wrap gap-1.5">
              <Button variant="primary" size="sm" onClick={() => onOpen(batch.batchId)}>查看</Button>
              <Button
                variant="ghost"
                size="sm"
                disabled={remove.isPending && remove.variables === batch.batchId}
                onClick={() => setPendingDelete(batch)}
              >
                {remove.isPending && remove.variables === batch.batchId ? "删除中..." : "删除"}
              </Button>
            </div>
          </div>
        ))}
      </div>

      <ConfirmDialog
        open={pendingDelete != null}
        title="删除训练批次"
        message={pendingDelete ? `确定删除批次「${pendingDelete.label}」？将一并删除其父模型与全部候选记录，且无法恢复。` : ""}
        confirmText="删除"
        onConfirm={() => {
          if (pendingDelete) remove.mutate(pendingDelete.batchId);
          setPendingDelete(null);
        }}
        onCancel={() => setPendingDelete(null)}
      />
    </div>
  );
}
