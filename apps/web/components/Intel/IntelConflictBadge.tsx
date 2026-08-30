"use client";

import { conflictTitle } from "@/lib/intelActionability";
import type { IntelConflict, IntelItem } from "@/lib/intelTypes";

/**
 * 卡片上的矛盾徽标（ai-trade-intel-actionability 轴 C）。
 *
 * 矛盾是**条目的属性**，不是一个独立的视图——所以它是卡片上的一枚徽标，而不是第八个页签。
 *
 * 徽标只说「这一条与另一条同题反向的情报冲突」，**绝不暗示哪一条是错的**：确定性规则没有能力
 * 判断两条相反的报道里哪一条为真，它唯一诚实的动作是让分歧对模型和用户都可见。误判的后果上限
 * 因此只是「多一枚徽标」——不删除、不降级、不停注入（R-7）。
 */
export default function IntelConflictBadge({
  item,
  pair,
  partner,
}: {
  item: IntelItem;
  /** 这一条所属的矛盾对；`undefined` ⇒ **整枚徽标不渲染**（不是渲染一个「无冲突」）。 */
  pair?: IntelConflict;
  /** 对方条目（用于悬浮说明里那句「与「…」冲突」）；拿不到 ⇒ 只说前半句。 */
  partner?: IntelItem;
}) {
  if (!pair) return null;
  const other = pair.aId === item.id ? pair.bId : pair.aId;
  return (
    <span
      data-testid="intel-conflict-badge"
      title={conflictTitle(pair, partner)}
      className="inline-flex shrink-0 items-center gap-1 rounded-full border border-[var(--warn)] px-1.5 py-px text-[10px] text-[var(--warn)]"
    >
      <span aria-hidden="true">⇄</span>
      与 #{other} 矛盾
      {pair.openRounds > 0 && (
        <span className="text-[var(--muted)]">· 悬置 {pair.openRounds} 轮</span>
      )}
    </span>
  );
}
