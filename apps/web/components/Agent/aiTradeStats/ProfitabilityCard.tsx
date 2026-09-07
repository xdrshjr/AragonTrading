"use client";

import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Badge } from "@/components/ui";
import { aiTradeHistoryApi } from "@/lib/aiTradeHistoryApi";
import {
  VERDICT_TONES,
  costBasisText,
  fmtOrDash,
  fmtRate,
  fmtUsd,
  holdDurationText,
  payoffText,
  riskDistributionText,
  travelToCostText,
  type AiTradeProfitabilityResponse,
  type AiTradeSliceRow,
} from "@/lib/aiTradeProfitability";
import { colorForReturn, signed } from "@/lib/format";
import ProfitabilityCoverage from "./ProfitabilityCoverage";

/**
 * 跨会话盈利体检卡（ai-trade-realized-edge-calibration 轴 B）。
 *
 * 它回答的是这套系统至今答不上来的一个问题：**到底赚不赚钱**。全仓所有既有盈利读数的作用域最大
 * 到一个分组，而一个分组的往返数中位只有个位数——于是每一层新纪律上线时都带着 5 笔样本的「证据」。
 * 本卡把逐笔往返在**全部历史**上池化，并在样本不足时显式写「样本不足，不下结论」。
 *
 * 三条呈现纪律：
 * - 每个比例 / 中位数读数都与它自己的分母同屏（`roundTrips` / `rSamples` / `feeTrips`）；
 * - 「样本不足」不等于「藏起来」——数字照常给出，只是徽标写清楚它不构成结论；
 * - 文案不得出现任何收益承诺 / 校准宣称类措辞（词表见 `aiTradeProfitability.test.ts` 的 G-10
 *   扫描；**此处刻意不逐字复述那几个词**——本文件自己就在扫描集里）。
 */
const SLICE_LABELS: { key: string; label: string }[] = [
  { key: "byBar", label: "按周期" },
  { key: "byInstId", label: "按标的" },
  { key: "byExitSource", label: "按退出来源" },
  { key: "byPreset", label: "按纪律预设" },
  { key: "byHorizonProfile", label: "按交易周期模式" },
  { key: "byConfidenceBucket", label: "按置信度区间" },
];

export default function ProfitabilityCard() {
  const [openSlices, setOpenSlices] = useState(false);
  const query = useQuery({
    queryKey: ["aiTrade", "profitability"],
    queryFn: () => aiTradeHistoryApi.profitability(),
  });
  const data = query.data;
  if (query.isError) return <div role="alert"
    className="flex min-w-0 flex-wrap gap-2 text-xs text-[var(--muted)]">
    盈利体检取数失败
    <button type="button" onClick={() => void query.refetch()}
      className="rounded focus-visible:outline focus-visible:outline-2 focus-visible:outline-[var(--accent)]">
      重试
    </button>
  </div>;
  if (query.isPending || !data) return null;
  if (data.schemaVersion != null && data.schemaVersion !== 2) {
    return <p className="text-xs text-[var(--muted)]">此版本盈利详情暂不可读</p>;
  }
  if ((data.coverage?.totalTrips ?? data.sampleTrips) === 0
    && !data.coverage?.structuralIssueCount) return null;

  const { pooled, risk, verdict } = data;
  return (
    <div
      className="flex min-w-0 max-w-full flex-col gap-2 break-words rounded-md border border-[var(--border)] bg-[var(--surface)] px-3 py-2"
      data-testid="ai-profitability-card"
    >
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <span className="text-xs text-[var(--muted)]">跨会话盈利体检 · 池化口径</span>
        <Badge tone={VERDICT_TONES[verdict.code] ?? "neutral"}>{verdict.title}</Badge>
      </div>
      <p className="text-[10px] leading-relaxed text-[var(--muted)]" data-testid="ai-profitability-verdict">
        {verdict.detail}
      </p>
      <ProfitabilityCoverage coverage={data.coverage}
        schemaVersion={data.schemaVersion} />
      <div className="grid gap-2"
        style={{ gridTemplateColumns: "repeat(auto-fit, minmax(min(100%, 140px), 1fr))" }}>
        <div data-testid="ai-profitability-net">
          <Cell label={`交易净额（${pooled.roundTrips} 笔，不含资金费）`}
            value={pooled.netPnlUsd == null ? "—" : signed(pooled.netPnlUsd)}
            tone={pooled.netPnlUsd} />
        </div>
        <Cell label={`胜率（${pooled.roundTrips} 笔）`} value={fmtRate(pooled.winRate)} />
        <Cell label="赔率 / 盈亏平衡" value={payoffText(pooled)} />
        <Cell label={`每笔期望（${pooled.roundTrips} 笔）`} value={fmtUsd(pooled.expectancyUsd)} tone={pooled.expectancyUsd} />
      </div>
      <div className="grid gap-2"
        style={{ gridTemplateColumns: "repeat(auto-fit, minmax(min(100%, 140px), 1fr))" }}>
        <Cell label={`t 统计量（${pooled.roundTrips} 笔）`} value={fmtOrDash(pooled.tStat, 3)} />
        <Cell label={`R 中位（${risk.rSamples} 笔可算）`} value={fmtOrDash(risk.rP50, 3)} />
        <Cell label={`触及 ±1R 占比（${risk.rSamples} 笔）`} value={fmtRate(risk.reachedOneRShare)} />
        <Cell label={`止损利用率中位（${risk.stopUtilizationSamples ?? "—"} 笔）`} value={fmtRate(risk.stopUtilizationP50)} />
      </div>
      {/* [P0-4] 这一行是本轮最该上屏的那个数，且**必须**与 feeCoverage 同行（G-9）。 */}
      <p className="text-[10px] leading-relaxed text-[var(--text)]" data-testid="ai-profitability-travel">
        {travelToCostText(pooled)}
      </p>
      {/* 中位持仓时长：后端一直算着却从没上过屏；它是「交易周期模式」那套纪律的事后验证读数。 */}
      <p className="text-[10px] leading-relaxed text-[var(--muted)]" data-testid="ai-profitability-hold">
        {holdDurationText(pooled)}
      </p>
      {/* R 分布：整套止损 / 止盈 / 保护武装线到底有没有被触碰过，答案全在这一行（D-1）。 */}
      <p className="text-[10px] leading-relaxed text-[var(--muted)]" data-testid="ai-profitability-risk">
        {riskDistributionText(risk)}
      </p>
      {costBasisText(data.costBasisAssumedTrips, pooled.roundTrips) && (
        <p className="text-[10px] leading-relaxed text-[var(--muted)]" data-testid="ai-profitability-basis">
          {costBasisText(data.costBasisAssumedTrips, pooled.roundTrips)}
        </p>
      )}
      <button
        type="button"
        onClick={() => setOpenSlices((v) => !v)}
        className="self-start rounded text-[10px] text-[var(--muted)] hover:text-[var(--text)] focus-visible:outline focus-visible:outline-2 focus-visible:outline-[var(--accent)]"
        aria-expanded={openSlices}
      >
        {openSlices ? "▾" : "▸"} 分层切片（每行自带分母）
      </button>
      {openSlices && (
        <div className="flex flex-col gap-1.5">
          {SLICE_LABELS.map(({ key, label }) => (
            <SliceGroup key={key} label={label} rows={data.slices?.[key] ?? []} />
          ))}
        </div>
      )}
      <p className="text-[10px] leading-relaxed text-[var(--muted)]">
        全部比例按各自样本池化；总体证据完整且满 {data.minTrips} 笔往返才下结论。
        这是一张历史统计，不构成对未来的任何承诺。
      </p>
    </div>
  );
}

function SliceGroup({ label, rows }: { label: string; rows: AiTradeSliceRow[] }) {
  if (rows.length === 0) return null;
  return (
    <div className="flex flex-col gap-0.5" data-testid={`ai-profitability-slice-${label}`}>
      <span className="text-[10px] text-[var(--muted)]">{label}</span>
      {rows.map((row) => (
        <span key={row.key} className="tabular text-[10px] text-[var(--text)]">
          {row.key} · {row.totalTrips == null ? `${row.trips} 笔`
            : `可核算 ${row.netEligibleTrips ?? row.trips} / 总往返 ${row.totalTrips} 笔 · 未纳入 ${row.excludedTrips ?? "—"} 笔`}
          {Boolean(row.coverage?.structuralIssueCount) &&
            ` · 结构异常 ${row.coverage?.structuralIssueCount} 条`}
          {" · 净额 "}
          <span style={{ color: row.netPnlUsd == null
            ? "var(--muted)" : colorForReturn(row.netPnlUsd) }}>
            {row.netPnlUsd == null ? "—" : signed(row.netPnlUsd)}
          </span> · 胜率（{row.netSamples ?? row.trips} 笔）{" "}
          {fmtRate(row.winRate)} · R 中位（{row.rSamples ?? "—"} 笔）{" "}
          {fmtOrDash(row.medianRMultiple, 3)}
        </span>
      ))}
    </div>
  );
}

function Cell({ label, value, tone }: { label: string; value: string; tone?: number | null }) {
  return (
    <div className="flex min-w-0 flex-col gap-0.5 rounded-md bg-[var(--surface-2)] px-2.5 py-1.5">
      <span className="text-[10px] text-[var(--muted)]">{label}</span>
      <span
        className="overflow-x-auto whitespace-nowrap tabular text-sm font-medium"
        style={{ color: tone == null ? "var(--text)" : colorForReturn(tone) }}
      >
        {value}
      </span>
    </div>
  );
}

export type { AiTradeProfitabilityResponse };
