"use client";

import { Badge, Skeleton } from "@/components/ui";
import { fmtNum, fmtPct, fmtTime } from "@/lib/format";
import { bestWorstWindowIndex } from "@/lib/walkForward";
import type { WalkForwardResult } from "@/lib/types";
import WalkForwardChart from "./WalkForwardChart";

// 高级回测结果展示：汇总卡片（8 项）+ 提示条 + 窗口收益柱状图 + 窗口明细表（标注最好/最差窗口）。
// 替代普通回测的单条净值 + MetricsCards（spec §7.3）。
export default function WalkForwardResults({
  result,
  loading,
  error,
}: {
  result: WalkForwardResult | null;
  loading?: boolean;
  error?: string | null;
}) {
  if (loading) {
    return (
      <div className="grid grid-cols-2 gap-2">
        {Array.from({ length: 8 }).map((_, i) => (
          <Skeleton key={i} className="h-[58px]" />
        ))}
      </div>
    );
  }
  if (error) {
    return (
      <div className="rounded-[8px] border border-[var(--short)] bg-[var(--surface)] px-3 py-2 text-[11px] text-[var(--short-ink)]">
        高级回测失败：{error}
      </div>
    );
  }
  if (!result) {
    return (
      <div className="py-8 text-center text-xs text-[var(--muted)]">
        填写窗口参数后点击「运行高级回测」，查看各窗口收益分布与稳定性评分。
      </div>
    );
  }

  const { summary, windows } = result;
  const { best, worst } = bestWorstWindowIndex(windows);
  const banners = [...summary.notes];
  if (summary.uncoveredBars > 0) {
    banners.push(`末尾 ${summary.uncoveredBars} 根未纳入任何窗口（不足一个完整窗口）`);
  }

  const cards: { label: string; value: string; tone?: "long" | "short" | "neutral" }[] = [
    { label: "窗口数", value: String(summary.windowCount) },
    { label: "盈利窗口占比", value: fmtPct(summary.profitableRate) },
    {
      label: "中位数收益",
      value: fmtPct(summary.medianReturn),
      tone: summary.medianReturn > 0 ? "long" : summary.medianReturn < 0 ? "short" : "neutral",
    },
    {
      label: "平均收益",
      value: fmtPct(summary.avgReturn),
      tone: summary.avgReturn > 0 ? "long" : summary.avgReturn < 0 ? "short" : "neutral",
    },
    { label: "最好窗口", value: fmtPct(summary.bestReturn), tone: "long" },
    { label: "最差窗口", value: fmtPct(summary.worstReturn), tone: "short" },
    {
      label: "平均最大回撤",
      value: fmtPct(summary.avgMaxDrawdown),
      tone: summary.avgMaxDrawdown < 0 ? "short" : "neutral",
    },
    { label: "稳定性评分", value: fmtNum(summary.stabilityScore) },
  ];

  return (
    <div className="flex flex-col gap-3">
      {banners.length > 0 && (
        <div className="flex flex-col gap-1 rounded-[8px] border border-[var(--border)] bg-[var(--surface-2)] px-3 py-2 text-[11px] text-[var(--muted)]">
          {banners.map((note) => (
            <div key={note}>⚠ {note}</div>
          ))}
        </div>
      )}

      <div className="grid grid-cols-2 gap-2">
        {cards.map((card) => (
          <div
            key={card.label}
            className="rounded-[10px] border border-[var(--border)] bg-[var(--surface-2)] px-3 py-2"
            title={card.label === "稳定性评分" ? "v1 启发式，越高越稳健（0~1）" : undefined}
          >
            <div className="text-[11px] text-[var(--muted)]">{card.label}</div>
            <div
              className="tabular text-base font-semibold"
              style={{
                color:
                  card.tone === "long"
                    ? "var(--long)"
                    : card.tone === "short"
                      ? "var(--short)"
                      : "var(--text)",
              }}
            >
              {card.value}
            </div>
          </div>
        ))}
      </div>

      <WalkForwardChart windows={windows} bestIndex={best} worstIndex={worst} />

      <div className="max-h-[320px] overflow-auto">
        <table className="w-full min-w-[560px] text-xs">
          <thead className="sticky top-0 whitespace-nowrap bg-[var(--surface)] text-[var(--muted)]">
            <tr className="text-left">
              <th className="py-1.5 pr-2 font-medium">窗口</th>
              <th className="py-1.5 pr-2 font-medium">时间区间</th>
              <th className="py-1.5 pr-2 text-right font-medium">收益率</th>
              <th className="py-1.5 pr-2 text-right font-medium">最大回撤</th>
              <th className="py-1.5 text-right font-medium">成交</th>
            </tr>
          </thead>
          <tbody>
            {windows.map((w, i) => (
              <tr
                key={`${w.startTs}-${i}`}
                className="whitespace-nowrap border-t border-[var(--border)]"
              >
                <td className="py-1.5 pr-2">
                  <span className="flex items-center gap-1">
                    #{i + 1}
                    {i === best && <Badge tone="long">最好</Badge>}
                    {i === worst && <Badge tone="short">最差</Badge>}
                  </span>
                </td>
                <td className="py-1.5 pr-2 text-[10px] text-[var(--muted)]">
                  {fmtTime(w.startTs)} → {fmtTime(w.endTs)}
                </td>
                <td
                  className="py-1.5 pr-2 text-right tabular font-medium"
                  style={{
                    color:
                      w.metrics.totalReturn > 0
                        ? "var(--long)"
                        : w.metrics.totalReturn < 0
                          ? "var(--short)"
                          : "var(--muted)",
                  }}
                >
                  {fmtPct(w.metrics.totalReturn)}
                </td>
                <td className="py-1.5 pr-2 text-right tabular text-[var(--short-ink)]">
                  {fmtPct(w.metrics.maxDrawdown)}
                </td>
                <td className="py-1.5 text-right tabular text-[var(--muted)]">{w.tradeCount}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
