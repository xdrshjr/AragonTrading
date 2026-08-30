"use client";

import { attributionText, attributionTitle, netPnlTone } from "@/lib/intelClosedLoop";
import type { IntelItem } from "@/lib/intelTypes";

/**
 * 情报卡上的归因轨（ai-trade-intel-closed-loop §3.7.1 左半）。
 *
 * 两条纪律：
 * - **零引用 ⇒ 整行不渲染**。一条「引用 0 次」的灰字是纯噪音，而卡片上的每一行都在跟摘要抢
 *   用户的注意力。
 * - 净额只用**盈亏配色变量**（`--up` / `--down`），**零值用 `--muted`**——一个「打平」被画成
 *   绿色，会让一排零值看起来像一排小胜。
 *
 * 文案里坚持写「**关联**净额」而不是「贡献」「带来」：这些数字是**共现**统计，一笔交易通常
 * 同时采信了多条证据（R-1）。完整口径挂在 `title` 上。
 */
export default function IntelAttributionRail({ item }: { item: IntelItem }) {
  const text = attributionText(item);
  if (!text) return null;
  return (
    <span
      data-testid="intel-attribution-rail"
      title={attributionTitle(item)}
      className="flex min-w-0 items-center gap-1 text-[10px] tabular text-[var(--muted)]"
    >
      <span aria-hidden="true">🔗</span>
      <span
        className="truncate"
        style={item.settledCount > 0 ? { color: netPnlTone(item.netPnlUsd) } : undefined}
      >
        {text}
      </span>
    </span>
  );
}
