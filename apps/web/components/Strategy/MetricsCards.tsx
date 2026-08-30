"use client";

import type { Metrics } from "@/lib/types";
import { fmtNum, fmtPct } from "@/lib/format";
import { Skeleton } from "@/components/ui";

export default function MetricsCards({ metrics, loading }: { metrics?: Metrics; loading?: boolean }) {
  if (loading || !metrics) {
    return (
      <div className="grid grid-cols-2 gap-2">
        {Array.from({ length: 6 }).map((_, i) => (
          <Skeleton key={i} className="h-[58px]" />
        ))}
      </div>
    );
  }

  const items: { label: string; value: string; tone?: "long" | "short" | "neutral" }[] = [
    {
      label: "总收益率",
      value: fmtPct(metrics.totalReturn),
      tone: metrics.totalReturn > 0 ? "long" : metrics.totalReturn < 0 ? "short" : "neutral",
    },
    { label: "胜率", value: fmtPct(metrics.winRate) },
    {
      label: "最大回撤",
      value: fmtPct(metrics.maxDrawdown),
      tone: metrics.maxDrawdown < 0 ? "short" : "neutral",
    },
    { label: "夏普比率", value: fmtNum(metrics.sharpe) },
    { label: "盈亏比", value: fmtNum(metrics.profitFactor) },
    { label: "成交笔数", value: String(metrics.tradeCount) },
  ];

  return (
    <div
      data-nowrap="scope"
      className="grid grid-cols-2 gap-2 whitespace-nowrap"
    >
      {items.map((it) => (
        <div
          key={it.label}
          className="min-w-0 rounded-[10px] border border-[var(--border)] bg-[var(--surface-2)] px-3 py-2"
        >
          <div className="truncate text-[11px] text-[var(--muted)]" title={it.label}>
            {it.label}
          </div>
          <div
            className="truncate tabular text-base font-semibold"
            title={it.value}
            style={{
              color:
                it.tone === "long"
                  ? "var(--long)"
                  : it.tone === "short"
                    ? "var(--short)"
                    : "var(--text)",
            }}
          >
            {it.value}
          </div>
        </div>
      ))}
    </div>
  );
}
