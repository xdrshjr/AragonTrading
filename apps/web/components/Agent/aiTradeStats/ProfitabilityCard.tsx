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
  // 没有任何历史往返 ⇒ 整卡不渲染（绝不在一个没有数据的账户上挂一张空表）。
  if (query.isPending || !data || data.sampleTrips === 0) return null;

  const { pooled, risk, verdict } = data;
  return (
    <div
      className="flex flex-col gap-2 rounded-md border border-[var(--border)] bg-[var(--surface)] px-3 py-2"
      data-testid="ai-profitability-card"
    >
      <div className="flex items-baseline justify-between gap-2">
        <span className="text-xs text-[var(--muted)]">跨会话盈利体检 · 池化口径</span>
        <Badge tone={VERDICT_TONES[verdict.code] ?? "neutral"}>{verdict.title}</Badge>
      </div>
      <p className="text-[10px] leading-relaxed text-[var(--muted)]" data-testid="ai-profitability-verdict">
        {verdict.detail}
      </p>
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
        <Cell label={`已实现净额（${pooled.roundTrips} 笔）`} value={signed(pooled.netPnlUsd ?? 0)} tone={pooled.netPnlUsd} />
        <Cell label="胜率" value={fmtRate(pooled.winRate)} />
        <Cell label="赔率 / 盈亏平衡" value={payoffText(pooled)} />
        <Cell label="每笔期望" value={fmtUsd(pooled.expectancyUsd)} tone={pooled.expectancyUsd} />
      </div>
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
        <Cell label={`t 统计量（${pooled.roundTrips} 笔）`} value={fmtOrDash(pooled.tStat, 3)} />
        <Cell label={`R 中位（${risk.rSamples} 笔可算）`} value={fmtOrDash(risk.rP50, 3)} />
        <Cell label="触及 ±1R 占比" value={fmtRate(risk.reachedOneRShare)} />
        <Cell label="止损利用率中位" value={fmtRate(risk.stopUtilizationP50)} />
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
        className="self-start text-[10px] text-[var(--muted)] hover:text-[var(--text)]"
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
        全部比例一律池化（Σ分子 / Σ分母），绝不对各局的比值求平均；满 {data.minTrips} 笔往返才下结论。
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
          {row.key} · {row.trips} 笔 · 净额{" "}
          <span style={{ color: colorForReturn(row.netPnlUsd ?? 0) }}>{signed(row.netPnlUsd ?? 0)}</span> · 胜率{" "}
          {fmtRate(row.winRate)} · R 中位 {fmtOrDash(row.medianRMultiple, 3)}
        </span>
      ))}
    </div>
  );
}

function Cell({ label, value, tone }: { label: string; value: string; tone?: number | null }) {
  return (
    <div className="flex flex-col gap-0.5 rounded-md bg-[var(--surface-2)] px-2.5 py-1.5">
      <span className="text-[10px] text-[var(--muted)]">{label}</span>
      <span
        className="tabular text-sm font-medium"
        style={{ color: tone == null ? "var(--text)" : colorForReturn(tone) }}
      >
        {value}
      </span>
    </div>
  );
}

export type { AiTradeProfitabilityResponse };
