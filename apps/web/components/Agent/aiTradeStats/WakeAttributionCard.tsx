"use client";

import { useMemo } from "react";
import {
  attributeByWake,
  formatWakeWinRate,
  hasWakeAttribution,
  WAKE_ATTRIBUTION_MIN_SAMPLES,
} from "@/lib/aiTradeWakeAttribution";
import type { RoundTrip } from "@/lib/autoTradePnl";
import { colorForReturn, signed } from "@/lib/format";

/**
 * 「按唤醒来源」胜率卡（ai-trade-wake-scheduler-closed-loop 轴 D）。
 *
 * 诊断里那句「用数据说话，不靠猜」的落点：哪种唤醒值得留、哪种纯属浪费，事后可用真实成交回答。
 * 同一个组件同时挂进「当前」与「历史·详情」两个页签，props 只吃 `RoundTrip[]`，两处零分叉。
 *
 * 两条诚实性纪律：
 * - 样本 < 5 一律不给胜率，显示「样本不足（n=x）」——绝不用 2 笔样本冒充统计结论；
 * - 无任何带来源的样本 ⇒ **整卡不渲染**（老会话不该看到一张全是「未记录来源」的表）。
 */
export default function WakeAttributionCard({ trips }: { trips: RoundTrip[] }) {
  const rows = useMemo(() => attributeByWake(trips), [trips]);
  if (!hasWakeAttribution(rows)) return null;
  return (
    <div
      className="flex flex-col gap-1.5 rounded-md border border-[var(--border)] bg-[var(--surface)] px-3 py-2"
      data-testid="wake-attribution-card"
    >
      <div className="flex items-baseline justify-between gap-2">
        <span className="text-xs text-[var(--muted)]">按唤醒来源</span>
        <span className="text-[10px] text-[var(--muted)]">
          样本 ≥ {WAKE_ATTRIBUTION_MIN_SAMPLES} 才给胜率
        </span>
      </div>
      <div className="flex flex-col gap-0.5">
        {rows.map((row) => (
          <div key={row.kind} className="flex items-center justify-between gap-2 text-[11px]">
            <span className="min-w-0 flex-1 truncate text-[var(--text)]" title={row.label}>
              {row.label}
            </span>
            <span className="tabular shrink-0 text-[var(--muted)]">
              {row.count} 笔 · 胜率 {formatWakeWinRate(row)}
            </span>
            <span
              className="tabular w-[72px] shrink-0 text-right font-medium"
              style={{ color: colorForReturn(row.netUsd) }}
            >
              {signed(row.netUsd)}
            </span>
          </div>
        ))}
      </div>
      <p className="text-[10px] leading-relaxed text-[var(--muted)]">
        按<span className="text-[var(--text)]">开仓</span>成交的唤醒来源归集（保护层平仓等确定性
        退出与唤醒无关，不参与归因）。唤醒原因总闸关闭的会话、以及该功能上线前的历史成交，落在
        「未记录来源」。
      </p>
    </div>
  );
}
