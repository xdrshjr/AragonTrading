"use client";

import { useQuery } from "@tanstack/react-query";
import { aiTradeHistoryApi } from "@/lib/aiTradeHistoryApi";
import {
  coverageText,
  fmtBps,
  fmtOrDash,
  fmtRate,
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
  if (query.isPending || !data || data.sampleTrips === 0) return null;
  const { edgeNumerator, costModel, confidenceReliability } = data.calibration;
  const total = data.sampleTrips;

  return (
    <div
      className="flex flex-col gap-2 rounded-md border border-[var(--border)] bg-[var(--surface)] px-3 py-2"
      data-testid="ai-calibration-card"
    >
      <span className="text-xs text-[var(--muted)]">校准三问 · 估计 vs 已实现</span>

      <Block
        title="门禁分子准不准"
        block={edgeNumerator}
        total={total}
        rows={[
          { label: "估计毛 edge 中位", value: fmtBps(edgeNumerator.medianEstEdgeBps) },
          { label: "已实现毛额中位", value: fmtBps(edgeNumerator.medianRealizedGrossBps) },
          { label: "误差中位", value: fmtBps(edgeNumerator.medianErrorBps) },
          { label: "方向命中率", value: fmtRate(edgeNumerator.signAgreementRate) },
        ]}
        hint="门禁的分子是「若走对能吃到多少」的幅度而非期望，理论上应系统性高于已实现毛额中位（后者含走错的那一半）；「方向命中率」度量的是胜率，不是门禁预测准确率。"
      />

      <Block
        title="成本模型准不准"
        block={costModel}
        total={total}
        rows={[
          { label: "模型常数", value: fmtBps(costModel.modelConstantBps) },
          { label: "实测往返成本中位", value: fmtBps(costModel.medianRealizedCostBps) },
          { label: "估计成本中位", value: fmtBps(costModel.medianEstCostBps) },
          { label: "误差中位", value: fmtBps(costModel.medianErrorBps) },
        ]}
        hint={costModel.note ?? "腿费恒 0 的往返一律不进本块分母——那是「未记录」而不是「零费率」。"}
      />

      <Block
        title="置信度有没有区分度"
        block={confidenceReliability}
        total={total}
        rows={(confidenceReliability.buckets ?? []).map((bucket) => ({
          label: `${bucket.key}（${bucket.trips} 笔）`,
          value: `胜率 ${fmtRate(bucket.winRate)} · R 中位 ${fmtOrDash(bucket.medianRMultiple, 3)}`,
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
  const coverage = coverageText(block.n, block.coverage, total);
  return (
    <div className="flex flex-col gap-1 border-t border-[var(--border)] pt-1.5 first:border-t-0 first:pt-0">
      <div className="flex items-center justify-between gap-2 text-[11px]">
        <span className="text-[var(--text)]">{title}</span>
        {!block.conclusive && (
          <span
            className="shrink-0 rounded-[3px] border border-[var(--border)] px-1 py-px text-[9px] text-[var(--muted)]"
            data-testid={`ai-calibration-inconclusive-${title}`}
          >
            {block.n === 0 ? "需要新会话积累" : "样本不足，不下结论"}
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
