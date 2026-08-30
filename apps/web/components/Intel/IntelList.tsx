"use client";

import { Skeleton } from "@/components/ui";
import { sortItems } from "@/lib/intel";
import type {
  AiTradeIntelSummary,
  IntelEvidence,
  IntelItem,
  IntelTier,
} from "@/lib/intelTypes";
import IntelCard, { type IntelCardActions } from "./IntelCard";
import { IntelEmptyState, type IntelEmptyKind } from "./IntelEmptyStates";

/**
 * 单层情报列表（ai-trade-market-intel §6.2 #28）：排序、空态分派、骨架屏。
 *
 * **加载态只在首次打开时用骨架屏**；轮询刷新走顶部进度条，绝不把已渲染的列表清空重画——那会让
 * 用户每隔几秒就丢一次阅读位置。
 */
export default function IntelList({
  items,
  tier,
  summary,
  loading,
  filtered,
  archivedView,
  actions,
  nowMs,
  onOpenSettings,
  onCollect,
  onClearFilters,
  evidenceById = {},
  evidenceLoadingId = null,
  evidenceErrors = {},
  blockedDomains = [],
}: {
  items: IntelItem[];
  tier: IntelTier;
  summary: AiTradeIntelSummary | null;
  /** 首次加载（尚无任何数据）时为真。 */
  loading: boolean;
  /** 当前是否存在生效的搜索词或类别筛选（决定空态说哪一句话）。 */
  filtered: boolean;
  /** 当前渲染的是「已归档 / 已静音」页签（它的空态与三层的空态说的不是一回事）。 */
  archivedView?: boolean;
  actions: IntelCardActions;
  nowMs: number;
  onOpenSettings: () => void;
  onCollect: () => void;
  onClearFilters: () => void;
  /** 弹窗持有的证据缓存（按 `itemId`）；缺键 = 还没取过 ⇒ 展开时才发那一次请求。 */
  evidenceById?: Record<number, IntelEvidence>;
  evidenceLoadingId?: number | null;
  /** 读取失败的原因**按 `itemId` 存**：一个弹窗级的字符串会让 A 的失败落到 B 的抽屉上。 */
  evidenceErrors?: Record<number, string>;
  blockedDomains?: string[];
}) {
  if (loading) {
    return (
      <div className="flex flex-col gap-2 p-3" data-testid="intel-list-skeleton">
        <Skeleton className="h-14 w-full rounded-[10px]" />
        <Skeleton className="h-14 w-full rounded-[10px]" />
        <Skeleton className="h-14 w-full rounded-[10px]" />
      </div>
    );
  }

  if (!items.length) {
    return (
      <IntelEmptyState
        kind={emptyKind({ summary, filtered, tier, archivedView })}
        summary={summary}
        nowMs={nowMs}
        onOpenSettings={onOpenSettings}
        onCollect={onCollect}
        onClearFilters={onClearFilters}
      />
    );
  }

  return (
    <div className="flex flex-col gap-1.5 p-3" data-testid="intel-list">
      {sortItems(items, nowMs).map((item) => (
        <IntelCard
          key={item.id}
          item={item}
          summary={summary}
          actions={actions}
          nowMs={nowMs}
          evidence={evidenceById[item.id]}
          evidenceLoading={evidenceLoadingId === item.id}
          evidenceError={evidenceErrors[item.id] ?? ""}
          blockedDomains={blockedDomains}
        />
      ))}
    </div>
  );
}

/**
 * 空态判据（顺序有意义）：未配置源 > 搜索无结果 > 归档页签 > 从未采集过 > 本层为空。
 *
 * 「未配置源」必须排第一：它是唯一一种**用户不动手就永远不会有情报**的状态，把它排在后面会让
 * 用户以为只是「还没采到」而一直等下去。归档页签必须排在「从未采集过」之前：一个空的归档抽屉
 * 说「还没采集过、点这里立即采集」是答非所问。
 */
function emptyKind({
  summary,
  filtered,
  tier,
  archivedView,
}: {
  summary: AiTradeIntelSummary | null;
  filtered: boolean;
  tier: IntelTier;
  archivedView?: boolean;
}): IntelEmptyKind {
  if (summary && !summary.providerConfigured) return "no-provider";
  if (filtered) return "no-match";
  if (archivedView) return "none-archived";
  if (summary && summary.lastCollectTs == null) return "not-collected";
  const others = summary
    ? Object.entries(summary.counts).some(
        ([key, value]) => key !== tier && key !== "archived" && value > 0,
      )
    : false;
  return others ? "other-tier" : "not-collected";
}
