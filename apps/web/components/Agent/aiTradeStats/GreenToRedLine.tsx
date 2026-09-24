"use client";

import type { GreenToRedSummary } from "@/lib/aiTradeCapture";

// 统计页「盈转亏」读数行（ai-trade-profit-lock-and-entry-unblock 轴 C-3）。
//
// 生产取证里，把账打成负数的主要不是方向判断，而是「先赚后亏」：131 笔往返中 46 笔曾经浮盈、
// 最后以亏损离场。这一行把那件事变成一个每次都看得见的数字，也把收益锁本身的代价（锁平后立刻
// 同向重开，多付一份往返成本）一并摆出来，而不是只报好消息。

const HINT =
  "盈转亏：持仓期间曾有浮盈、最终却以亏损离场的往返。回吐 = 峰值浮盈 − 实际落袋。"
  + "收益锁装配后峰值追踪覆盖全部持仓，因此样本口径比未装配时更宽，这是口径扩大，不是回归。";

export default function GreenToRedLine({ summary }: { summary: Partial<GreenToRedSummary> }) {
  const count = summary.greenToRedCount ?? 0;
  const giveback = summary.greenToRedGivebackUsd ?? 0;
  const locks = summary.lockExitCount ?? 0;
  const reentries = summary.lockReentryCount ?? 0;
  const head =
    count === 0
      ? "暂无盈转亏"
      : `盈转亏 ${count} 笔 · 回吐 ${giveback.toFixed(2)} USDT`;
  const lockPart =
    count > 0 ? ` · 其中收益锁离场 ${locks} 笔` : locks > 0 ? ` · 收益锁离场 ${locks} 笔` : "";
  const text = head + lockPart + (locks > 0 ? ` · 锁平后立即同向重开 ${reentries} 笔` : "");
  return (
    <div
      className="flex min-w-0 items-center justify-between gap-2 text-xs"
      data-testid="ai-green-to-red"
      title={HINT}
    >
      <span className="shrink-0 text-[var(--muted)]">盈转亏</span>
      <span className={`tabular truncate whitespace-nowrap ${count > 0 ? "text-[var(--warn)]" : "text-[var(--muted)]"}`}>
        {text}
      </span>
    </div>
  );
}
