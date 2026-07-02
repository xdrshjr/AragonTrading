"use client";

import { useState } from "react";
import { Badge, Button } from "@/components/ui";
import ConfirmDialog from "@/components/ui/ConfirmDialog";
import { formatIc } from "@/lib/factor";
import { candidateIcOut, candidateLabel } from "@/lib/weightBatch";
import type { CandidateState, WeightBatchRankRow } from "@/lib/weightBatchTypes";
import type { ExploreMatrix as ExploreMatrixShape } from "@/lib/weightExplore";
import ExploreMatrix from "./ExploreMatrix";
import { CandidateDetail } from "./ExploreRunningPanel";
import WeightBatchCompare from "./WeightBatchCompare";

/**
 * 探索完成态（factor-combo-exploration §2.7）：最佳组合摘要卡 + 满格矩阵热力图 + 最终排行榜 + 逐候选详情抽屉。
 * 「应用最佳组合到组合成员」把胜出子集/方法/权重回流为全新 builder 草稿（不绑定探索父模型，P0-1）并关闭弹窗。
 */
export default function ExploreResultPanel({
  matrix,
  states,
  ranking,
  bestId,
  onApplyBest,
  onDelete,
  deleting,
}: {
  matrix: ExploreMatrixShape;
  states: CandidateState[];
  ranking: WeightBatchRankRow[];
  bestId: number | null;
  onApplyBest: (runId: number) => void;
  onDelete: () => void;
  deleting: boolean;
}) {
  const [openRunId, setOpenRunId] = useState<number | null>(null);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const succeeded = states.filter((s) => s.status === "succeeded").length;
  const best = states.find((s) => s.runId === bestId) ?? null;
  const bestLabel = best ? matrix.rows.find((r) => r.comboId === best.variantLabel)?.label ?? best.variantLabel : null;
  const opened = states.find((s) => s.runId === openRunId) ?? null;

  return (
    <div className="flex flex-col gap-3">
      {best ? (
        <div className="card flex flex-wrap items-center gap-2 border-[var(--long)]/40 bg-[var(--long)]/5 p-3">
          <span className="text-[13px]">🏆</span>
          <span className="text-xs font-semibold text-[var(--text)]">最佳组合</span>
          <Badge tone="long">{bestLabel}</Badge>
          <Badge tone="accent">{candidateLabel(best.method, "")}</Badge>
          <span className="text-[11px] text-[var(--muted)]">样本外 IC <span className="tabular font-semibold text-[var(--text)]">{formatIc(candidateIcOut(best))}</span></span>
        </div>
      ) : (
        <p role="alert" className="rounded-[10px] border border-[var(--short)]/40 bg-[var(--short)]/10 px-3 py-2 text-[11px] font-medium text-[var(--short)]">
          没有任何候选成功训练，无法择优；请调整因子池 / 组合策略 / 区间后重新探索。
        </p>
      )}

      <div className="flex flex-wrap items-center gap-2">
        {best && (
          <Button variant="primary" size="sm" onClick={() => onApplyBest(best.runId)}>
            应用最佳组合到组合成员
          </Button>
        )}
        <Button variant="ghost" size="sm" disabled={deleting} onClick={() => setConfirmDelete(true)}>
          {deleting ? "删除中…" : "删除本次探索"}
        </Button>
        <span className="text-[11px] text-[var(--muted)]">{succeeded} / {states.length} 成功</span>
      </div>

      <ExploreMatrix matrix={matrix} bestRunId={bestId} onCell={(s) => setOpenRunId(s.runId)} />

      {states.length > 0 && (
        <WeightBatchCompare
          ranking={ranking}
          states={states}
          selectedRunId={null}
          canApply={false}
          onView={(runId) => setOpenRunId(runId)}
          onSelect={() => undefined}
          onApply={() => undefined}
        />
      )}

      {opened && <CandidateDetail state={opened} onClose={() => setOpenRunId(null)} />}

      <ConfirmDialog
        open={confirmDelete}
        title="删除本次探索"
        message="将删除本次探索批次（父模型 + 全部候选记录），且无法恢复。已应用的最佳组合草稿不受影响，确定继续？"
        confirmText="删除探索"
        onConfirm={() => {
          setConfirmDelete(false);
          onDelete();
        }}
        onCancel={() => setConfirmDelete(false)}
      />
    </div>
  );
}
