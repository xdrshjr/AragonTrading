"use client";

import { useQuery } from "@tanstack/react-query";
import { Badge } from "@/components/ui";
import { directionLabel, formatIc, overfitWarning, resolveThresholds, thresholdsSummary } from "@/lib/factor";
import { factorApi } from "@/lib/factorApi";
import { scorePointsToLine } from "@/lib/factorSeries";
import type { FactorSet } from "@/lib/factorTypes";
import { fmtTime } from "@/lib/format";
import FactorScoreChart from "./FactorScoreChart";
import { useWorkbench } from "./FactorLab";
import WeightBars from "./WeightBars";

// 复合得分提示文案：区间过短 / 成员对当前周期不兼容时显式解释，不静默空白（H6/L5，P2-3 不静默）。
function scoreNote(note: string | undefined, minBars: number | undefined): string | undefined {
  if (note === "window_too_short") {
    return `区间过短，至少需约 ${minBars ?? "更多"} 根数据才能产出复合得分曲线，请扩大区间。`;
  }
  if (note === "bar_incompatible") {
    return "组合内含仅适用于日内周期的因子，请把工作台周期切到 1m/5m/15m/1H 等日内周期。";
  }
  return undefined;
}

/**
 * 因子组合详情：成员徽标 + 权重条 + 复合 IC + 阈值摘要 + 训练溯源 + 复合得分 sparkline。
 * 复合得分以工作台 {标的/周期/区间} 调 sets.score 后把 ScorePoint 适配为 LinePoint 喂 FactorScoreChart（P2-3）。
 */
export default function FactorSetDetail({ set }: { set: FactorSet }) {
  const wb = useWorkbench();
  const rangeValid = wb.startTs < wb.endTs;
  const range = { instId: wb.instId, bar: wb.bar, startTs: wb.startTs, endTs: wb.endTs };
  const score = useQuery({
    queryKey: ["factor", "sets", "score", set.id, range.instId, range.bar, range.startTs, range.endTs],
    queryFn: () => factorApi.sets.score(set.id, range),
    enabled: rangeValid,
  });

  const thresholds = resolveThresholds(set.config?.thresholds);
  const weights = set.model?.weights;
  const provenance = set.model?.provenance;
  const overfit = overfitWarning(set.metrics);
  const note = scoreNote(score.data?.note, score.data?.minBarsRequired);
  const points = score.data ? scorePointsToLine(score.data.points) : [];

  return (
    <div className="flex flex-col gap-3">
      <div className="card flex flex-col gap-2 p-3">
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-sm font-semibold text-[var(--text)]">{set.name}</span>
          <Badge tone="accent">{set.method}</Badge>
          <Badge tone="neutral">样本内 IC {formatIc(set.metrics?.icIn ?? null)}</Badge>
          <Badge tone="neutral">样本外 IC {formatIc(set.metrics?.icOut ?? null)}</Badge>
          <span className="text-[11px] text-[var(--muted)]">{set.members.length} 个成员</span>
        </div>
        {overfit && <p className="text-[11px] font-medium text-[var(--short)]">{overfit}</p>}
        <div className="flex flex-wrap gap-1">
          {set.members.map((m, i) => (
            <Badge key={`${m.factorKey}-${i}`} tone={m.direction === "short" ? "short" : "long"}>
              {m.factorKey}·{directionLabel(m.direction)}
            </Badge>
          ))}
        </div>
      </div>

      <div className="card flex flex-col gap-2 p-3">
        <span className="text-[11px] font-medium text-[var(--muted)]">权重</span>
        {weights && Object.keys(weights).length > 0 ? (
          <WeightBars weights={weights} />
        ) : (
          <p className="text-[11px] text-[var(--muted)]">该组合尚未训练权重（编辑后重新训练即可生成）。</p>
        )}
      </div>

      <div className="card flex flex-col gap-1 p-3">
        <span className="text-[11px] font-medium text-[var(--muted)]">交易阈值</span>
        <p className="text-[11px] tabular text-[var(--text)]">{thresholdsSummary(thresholds)}</p>
        {provenance && (
          <p className="text-[11px] text-[var(--muted)]">
            训练于 {provenance.instId} · {provenance.bar} · [{fmtTime(provenance.startTs)},{" "}
            {fmtTime(provenance.endTs)}]
            {provenance.horizon != null ? ` · 前瞻 ${provenance.horizon} 根` : ""}
          </p>
        )}
      </div>

      <div className="card flex flex-col gap-2 p-3">
        <span className="text-[11px] font-medium text-[var(--muted)]">复合得分曲线</span>
        {!rangeValid ? (
          <FactorScoreChart points={[]} emptyNote="工作台起始须早于结束" />
        ) : score.isPending ? (
          <p className="py-6 text-center text-[11px] text-[var(--muted)]">计算中...</p>
        ) : score.isError ? (
          <p className="text-[11px] text-[var(--short)]">复合得分计算失败：{(score.error as Error)?.message}</p>
        ) : (
          <FactorScoreChart points={points} title={`${set.name} 复合得分`} emptyNote={note} />
        )}
      </div>
    </div>
  );
}
