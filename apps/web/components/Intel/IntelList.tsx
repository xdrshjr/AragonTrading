"use client";

import { Skeleton } from "@/components/ui";
import { sortItems } from "@/lib/intel";
import { conflictPartners } from "@/lib/intelActionability";
import type {
  AiTradeIntelSummary,
  IntelConflict,
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
  onRearm,
  evidenceById = {},
  evidenceLoadingId = null,
  evidenceErrors = {},
  blockedDomains = [],
  conflicts = [],
  itemsById = {},
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
  /** 重新装配（`arm_failed` 的空态与 banner 共用）。 */
  onRearm?: () => void;
  /** 弹窗持有的证据缓存（按 `itemId`）；缺键 = 还没取过 ⇒ 展开时才发那一次请求。 */
  evidenceById?: Record<number, IntelEvidence>;
  evidenceLoadingId?: number | null;
  /** 读取失败的原因**按 `itemId` 存**：一个弹窗级的字符串会让 A 的失败落到 B 的抽屉上。 */
  evidenceErrors?: Record<number, string>;
  blockedDomains?: string[];
  /** 当前库存里的矛盾对（轴 C）；空数组 ⇒ 徽标整段不渲染。 */
  conflicts?: IntelConflict[];
  /** 全部条目按 id 索引：矛盾对的另一端可能**不在当前这一层**，故不能只在 `items` 里找。 */
  itemsById?: Record<number, IntelItem>;
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
        onRearm={onRearm}
      />
    );
  }

  const partners = conflictPartners(conflicts);
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
          conflict={conflicts.find((p) => p.aId === item.id || p.bId === item.id)}
          conflictPartner={itemsById[partners[item.id]]}
        />
      ))}
    </div>
  );
}

/**
 * 空态判据（顺序有意义）：未配源 > 总闸关 > 未装配 > 搜索无结果 > 归档页签 > 从未采集 > 本层为空。
 *
 * 前三条必须排在最前，且**必须互相分开**：它们是仅有的三种「用户不动手就永远不会有情报」的
 * 状态，而它们的出路完全不同——第一种要去选源存 key，第二种只要打开一个开关，第三种连设置都
 * 不用碰（点一次「重新装配」，或者根本就是自动交易没在跑）。把三者压成一句「尚未配置情报检索
 * 源」，会让一个**已经配好了**的用户去设置里反复确认自己没配错，然后断定这个功能坏了。
 *
 * 归档页签必须排在「从未采集过」之前：一个空的归档抽屉说「还没采集过、点这里立即采集」是
 * 答非所问。
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
  if (summary && !summary.intelEnabled) return "intel-off";
  if (summary && !summary.armed) return "not-armed";
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
