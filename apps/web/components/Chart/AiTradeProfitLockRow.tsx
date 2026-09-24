"use client";

import { describeProfitLock, type ProfitLockTone } from "@/lib/aiTradeProfitLock";
import type { AiTradeGroupMemberState } from "@/lib/aiTradeTypes";

// 持仓行的「收益锁」一行（ai-trade-profit-lock-and-entry-unblock 轴 C-1）。
//
// 门控与利润保护行相同：**确实还持仓**且快照存在二者同时成立——`member.position` 在平仓后不会被
// 清空，只判快照会在空仓后继续显示一条已不存在的锁线。单行不折行（工作台零折行契约），完整读数
// 放在悬停说明里。

const TONE_CLASS: Record<ProfitLockTone, string> = {
  muted: "text-[var(--muted)]",
  accent: "text-[var(--accent-ink)]",
  success: "text-[var(--long-ink)]",
  warn: "text-[var(--warn)]",
};

export default function AiTradeProfitLockRow({ member }: { member: AiTradeGroupMemberState }) {
  const positionSide = member.session?.positionSide ?? "flat";
  const snapshot = positionSide !== "flat" ? (member.position?.profitLock ?? null) : null;
  if (!snapshot) return null;
  const view = describeProfitLock(snapshot, member.session?.profitLock?.exits ?? null);
  return (
    <span
      data-testid="ai-trade-profit-lock"
      data-tone={view.tone}
      className={`tabular truncate whitespace-nowrap text-[10px] ${TONE_CLASS[view.tone]}`}
      title={view.title}
    >
      {view.text}
    </span>
  );
}
