"use client";

import { useState } from "react";
import { Button } from "@/components/ui";
import { candidateLabel } from "@/lib/weightBatch";
import type { CandidateState, WeightBatchRankRow } from "@/lib/weightBatchTypes";
import type { ExploreMatrix as ExploreMatrixShape } from "@/lib/weightExplore";
import ExploreMatrix from "./ExploreMatrix";
import TrainingCurveChart from "./TrainingCurveChart";
import WeightBatchCompare from "./WeightBatchCompare";
import WeightBars from "./WeightBars";

/**
 * 探索运行态（factor-combo-exploration §2.7）：总进度 + 断线 / 停滞横幅 + 组合×方法矩阵热力图 + 实时排行榜 +
 * 逐候选详情抽屉（学习曲线 + 权重）。全部候选终态前实时可视化训练过程。
 */
export default function ExploreRunningPanel({
  matrix,
  states,
  ranking,
  bestId,
  connected,
  stale,
  onCancelAll,
  cancelling,
}: {
  matrix: ExploreMatrixShape;
  states: CandidateState[];
  ranking: WeightBatchRankRow[];
  bestId: number | null;
  connected: boolean;
  stale: boolean;
  onCancelAll: () => void;
  cancelling: boolean;
}) {
  const [openRunId, setOpenRunId] = useState<number | null>(null);
  const total = states.length;
  const done = states.filter((s) => s.status === "succeeded" || s.status === "failed" || s.status === "cancelled").length;
  const opened = states.find((s) => s.runId === openRunId) ?? null;

  return (
    <div className="flex flex-col gap-3">
      {!connected && (
        <p aria-live="polite" className="rounded-[10px] border border-[var(--accent)]/40 bg-[var(--accent-soft)]/40 px-3 py-2 text-[11px] font-medium text-[var(--accent)]">
          连接中断，正在重连…（训练在后端继续，恢复后自动续显）
        </p>
      )}
      {connected && stale && (
        <p aria-live="polite" className="rounded-[10px] border border-[var(--border)] bg-[var(--surface-2)] px-3 py-2 text-[11px] text-[var(--muted)]">
          等待训练数据…（网络或后端较慢，请稍候）
        </p>
      )}

      <div className="flex items-center gap-3">
        <div className="h-2 flex-1 overflow-hidden rounded-full bg-[var(--surface-2)]">
          <div className="h-full rounded-full bg-[var(--accent)] transition-all" style={{ width: `${total > 0 ? (done / total) * 100 : 0}%` }} />
        </div>
        <span className="text-[11px] tabular text-[var(--muted)]">{done} / {total} 完成</span>
        <Button variant="ghost" size="sm" disabled={cancelling || done >= total} onClick={onCancelAll}>
          {cancelling ? "取消中…" : "全部取消"}
        </Button>
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
    </div>
  );
}

// 逐候选详情抽屉（运行态 / 完成态共用）：组合·方法标题 + 学习曲线 + 权重条。
export function CandidateDetail({ state, onClose }: { state: CandidateState; onClose: () => void }) {
  const weights = state.result?.weights ?? state.liveWeights ?? {};
  return (
    <div className="card flex flex-col gap-3 p-3">
      <div className="flex items-center justify-between gap-2">
        <span className="text-xs font-semibold text-[var(--text)]">
          候选详情 · {candidateLabel(state.method, state.variantLabel)}
        </span>
        <Button variant="ghost" size="sm" onClick={onClose}>收起</Button>
      </div>
      <TrainingCurveChart curve={state.curve} />
      {Object.keys(weights).length > 0 && <WeightBars weights={weights} />}
    </div>
  );
}
