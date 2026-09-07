"use client";

import { useQuery } from "@tanstack/react-query";
import { aiTradeHistoryApi } from "@/lib/aiTradeHistoryApi";
import {
  coverageText,
  fmtBps,
  fmtOrDash,
  fmtRate,
  fmtUsd,
  type AiTradeCalibrationBlock,
} from "@/lib/aiTradeProfitability";

/**
 * 校准三问卡（ai-trade-realized-edge-calibration 轴 C）。
 *
 * 三问共用一个形状：`{n, coverage, 读数, conclusive}`。**`coverage < 1` 时必须把覆盖率显示出来**
 * ——一个在 11% 子样本上算出来的成本读数与一个在 100% 样本上算出来的胜率并排放着而不说明，
 * 本身就是误导（G-8）。
 *
 * 上线当天问一与问三的 `coverage` 恒为 0：它们需要开仓那一刻冻结的门禁估计，而历史往返一笔都没有。
 * **这不是缺陷、是事实**，故卡片显式写「需要新会话积累」而不是把整块藏起来。
 */
export default function CalibrationCard() {
  const query = useQuery({
    queryKey: ["aiTrade", "profitability"],
    queryFn: () => aiTradeHistoryApi.profitability(),
  });
  const data = query.data;
  if (query.isError) return <div role="alert"
    className="flex min-w-0 flex-wrap gap-2 text-xs text-[var(--muted)]">
    校准取数失败
    <button type="button" onClick={() => void query.refetch()}
      className="rounded focus-visible:outline focus-visible:outline-2 focus-visible:outline-[var(--accent)]">
      重试
    </button>
  </div>;
  if (query.isPending || !data) return null;
  if (data.schemaVersion != null && data.schemaVersion !== 2) {
    return <p className="text-xs text-[var(--muted)]">此版本校准详情暂不可读</p>;
  }
  if ((data.coverage?.totalTrips ?? data.sampleTrips) === 0
    && !data.coverage?.structuralIssueCount) return null;
  const { edgeNumerator, costModel, confidenceReliability } = data.calibration;
  const total = data.coverage?.totalTrips ?? data.sampleTrips;
  const isFeeComparison = data.schemaVersion === 2
    && costModel.comparisonBasis === "fees";

  return (
    <div
      className="flex min-w-0 max-w-full flex-col gap-2 break-words rounded-md border border-[var(--border)] bg-[var(--surface)] px-3 py-2"
      data-testid="ai-calibration-card"
    >
      <span className="text-xs text-[var(--muted)]">校准三问 · 估计 vs 已实现</span>
      {!data.coverage && <p className="text-[10px] text-[var(--muted)]">
        旧版未提供总体覆盖，以下分母沿用旧版样本口径
      </p>}

      <Block
        title="门禁分子准不准"
        block={edgeNumerator}
        total={total}
        rows={[
          { label: "估计毛 edge 中位", value: fmtBps(edgeNumerator.medianEstEdgeBps) },
          { label: "已实现毛额中位", value: fmtBps(edgeNumerator.medianRealizedGrossBps) },
          { label: "误差中位", value: fmtBps(edgeNumerator.medianErrorBps) },
          { label: `方向命中率（${edgeNumerator.signSamples ?? "—"} 笔正估计）`, value: fmtRate(edgeNumerator.signAgreementRate) },
        ]}
        hint="门禁的分子是「若走对能吃到多少」的幅度而非期望，理论上应系统性高于已实现毛额中位（后者含走错的那一半）；「方向命中率」度量的是胜率，不是门禁预测准确率。"
      />

      <Block
        title="成本模型准不准"
        block={costModel}
        total={total}
        rows={[
          { label: "手续费估计中位", value: fmtBps(isFeeComparison ? costModel.medianEstFeeBps : null) },
          { label: "实测手续费中位", value: fmtBps(isFeeComparison ? costModel.medianRealizedFeeBps : null) },
          { label: "手续费误差中位", value: fmtBps(isFeeComparison ? costModel.medianFeeErrorBps : null) },
          { label: "历史总成本估计，仅作描述", value: fmtBps(costModel.medianEstTotalCostBps) },
          { label: "当前配置总成本预览，非历史预测", value: fmtBps(costModel.modelConstantBps) },
        ]}
        hint={isFeeComparison
          ? `仅比较同笔往返手续费，不含资金费或其他成本；可用估计 ${costModel.availableEstimateTrips ?? "—"} 笔，可用实测 ${costModel.availableRealizedTrips ?? "—"} 笔，同笔配对 ${costModel.n} 笔。${costModel.note ?? ""}`
          : `旧版未提供同笔手续费比较，费用误差暂不可得。${costModel.note ?? ""}`}
      />

      <Block
        title="置信度有没有区分度"
        block={confidenceReliability}
        total={total}
        rows={(confidenceReliability.buckets ?? []).map((bucket) => ({
          label: `${bucket.key}（净额 ${bucket.netSamples ?? bucket.trips} 笔，R ${bucket.rSamples ?? "—"} 笔）`,
          value: `胜率 ${fmtRate(bucket.winRate)} · 每笔期望 ${fmtUsd(bucket.expectancyUsd)} · R 中位 ${fmtOrDash(bucket.medianRMultiple, 3)}`,
        }))}
        hint={
          confidenceReliability.monotonic == null
            ? "并非每个区间都有足够样本，故不判断胜率是否随置信度单调上升。本块只出读数，本轮不改任何门槛。"
            : confidenceReliability.monotonic
              ? "胜率随置信度区间单调不减。本块只出读数，本轮不改任何门槛。"
              : "胜率并未随置信度区间单调上升。本块只出读数，本轮不改任何门槛。"
        }
      />
    </div>
  );
}

function Block({
  title,
  block,
  total,
  rows,
  hint,
}: {
  title: string;
  block: AiTradeCalibrationBlock;
  total: number;
  rows: { label: string; value: string }[];
  hint: string;
}) {
  const coverage = coverageText(block.n, block.coverage, total)
    ?? `覆盖 ${block.n} / ${total} 笔（${fmtRate(block.coverage)}）`;
  return (
    <div className="flex min-w-0 flex-col gap-1 border-t border-[var(--border)] pt-1.5 first:border-t-0 first:pt-0">
      <div className="flex flex-wrap items-center justify-between gap-2 text-[11px]">
        <span className="text-[var(--text)]">{title}</span>
        {!block.conclusive && (
          <span
            className="shrink-0 rounded-[3px] border border-[var(--border)] px-1 py-px text-[9px] text-[var(--muted)]"
            data-testid={`ai-calibration-inconclusive-${title}`}
          >
            {block.n === 0 ? "需要新会话积累" : "样本或证据不足，不下结论"}
          </span>
        )}
      </div>
      {/* G-8：coverage < 1 必须显示覆盖率。分母不上屏的比例读数就是误导。 */}
      {coverage && (
        <span className="tabular text-[10px] text-[var(--muted)]" data-testid={`ai-calibration-coverage-${title}`}>
          {coverage}
        </span>
      )}
      <div className="flex flex-col gap-0.5">
        {rows.map((row) => (
          <span key={row.label} className="tabular text-[10px] text-[var(--text)]">
            {row.label}：{row.value}
          </span>
        ))}
      </div>
      <p className="text-[10px] leading-relaxed text-[var(--muted)]">{hint}</p>
    </div>
  );
}
