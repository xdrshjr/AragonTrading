"use client";

import clsx from "clsx";
import {
  MIN_ELAPSED_SEC,
  MIN_TRADES,
  type AnnualizedConfidence,
  type AnnualizedReason,
  annualizedConfidence,
  projectAnnualizedReturn,
} from "@/lib/annualized";
import { colorForReturn, fmtPct } from "@/lib/format";

// 降级原因 → 诚实文案（不满足门禁时不出数字，明确告知还差什么，绝不伪造天文年化）。
const REASON_HINTS: Record<AnnualizedReason, string> = {
  "no-elapsed": "运行时长不足，暂无法估算。",
  "too-short": `运行需满 ${Math.round(MIN_ELAPSED_SEC / 3600)} 小时后方可估算（样本过短的外推会严重失真）。`,
  "too-few-trades": `完整交易需满 ${MIN_TRADES} 笔后方可估算（样本过少的外推不可信）。`,
  wipeout: "本金已接近归零，年化外推无意义。",
};

// E4 样本可信度徽标（低 / 中 / 高）：仅在年化出数字时并排展示，一眼提示这次外推有多可信。
const CONFIDENCE_BADGE: Record<AnnualizedConfidence, { label: string; cls: string }> = {
  low: { label: "可信度 低", cls: "text-[var(--short-ink)] border-[var(--short)]" },
  medium: { label: "可信度 中", cls: "text-[var(--muted)] border-[var(--border)]" },
  high: { label: "可信度 高", cls: "text-[var(--long-ink)] border-[var(--long)]" },
};

/**
 * 预计年化收益卡（ai-trade-stats-two-column-and-kline §3.3）：据「全会话已实现收益率 + 真实经历时长 +
 * 完整交易笔数」线性外推年化。P1-2 诚实护栏——满足最小样本（≥6h 且 ≥3 笔）才出数字并常驻免责声明 + 样本基数；
 * 否则显式降级「样本不足以估算年化」，绝不对极短样本给天文数字。口径 = 仅已实现收益、与时间筛选解耦（调用方保证）。
 */
export default function AnnualizedProjectionCard({
  returnPct,
  elapsedSec,
  tradeCount,
}: {
  returnPct: number;
  elapsedSec: number;
  tradeCount: number;
}) {
  const projection = projectAnnualizedReturn({ returnPct, elapsedSec, tradeCount });
  const confidence = CONFIDENCE_BADGE[annualizedConfidence(elapsedSec, tradeCount)];

  return (
    <div className="flex flex-col gap-1 rounded-lg border border-[var(--border)] bg-[var(--surface)] px-3 py-2.5">
      <span className="text-[10px] uppercase tracking-wide text-[var(--muted)]">
        预计年化收益（线性外推 · 估算）
      </span>
      {projection.ok && projection.annualizedPct !== null ? (
        <>
          <div className="flex items-center gap-2">
            <span
              className="tabular text-xl font-semibold"
              style={{ color: colorForReturn(projection.annualizedPct) }}
            >
              {fmtPct(projection.annualizedPct)}
            </span>
            <span
              className={clsx(
                "rounded-full border px-1.5 py-0.5 text-[9px] font-medium",
                confidence.cls,
              )}
            >
              {confidence.label}
            </span>
          </div>
          <span className="text-[10px] leading-relaxed text-[var(--muted)]">
            按当前已实现收益线性外推一年（估算，非未来承诺）· 基于{" "}
            <span className="tabular text-[var(--text)]">{fmtElapsed(elapsedSec)}</span> /{" "}
            <span className="tabular text-[var(--text)]">{tradeCount}</span> 笔样本；仅计已实现收益，样本越短越不可靠。
          </span>
        </>
      ) : (
        <>
          <span className="text-sm font-medium text-[var(--muted)]">样本不足以估算年化</span>
          <span className="text-[10px] leading-relaxed text-[var(--muted)]">
            {projection.reason ? REASON_HINTS[projection.reason] : "样本不足，暂无法估算。"}
          </span>
        </>
      )}
    </div>
  );
}

// 经历时长（秒）→ 紧凑可读串（天 / 时 / 分），至多两段，作年化样本基数展示。
function fmtElapsed(seconds: number): string {
  const s = Math.max(0, Math.floor(seconds));
  const days = Math.floor(s / 86_400);
  const hours = Math.floor((s % 86_400) / 3600);
  if (days > 0) return hours > 0 ? `${days} 天 ${hours} 时` : `${days} 天`;
  const minutes = Math.floor((s % 3600) / 60);
  if (hours > 0) return minutes > 0 ? `${hours} 时 ${minutes} 分` : `${hours} 时`;
  return `${minutes} 分`;
}
