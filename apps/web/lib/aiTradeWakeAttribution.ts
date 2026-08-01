// AI 自动交易「按唤醒来源切胜率」纯函数（ai-trade-wake-scheduler-closed-loop 轴 D）。
//
// 诊断问的是：「我赚钱的那些交易，是哪种唤醒**触发**的？」第 1 轮已经把开仓成交的
// `explain.meta.wakeKind` 写进了 `trades.explain_json`，而 `autoTradePnl.RoundTrip` 本来就持有
// `entry: Trade`。因此这一轴是既有数据的**纯投影**——刻意不在后端再造一份统计口径（本仓库已经为
// 「同一个量两处各算一次」付过学费，见 `stats_ext.py` 的 `build_cost_stats` 唯一真源纪律）。
//
// **归因按开仓腿而非平仓腿**是刻意的：保护层平仓腿的 `wakeKind` 按设计缺席（它与唤醒无关），
// 而 `reverse`（反手的平仓腿）虽然带 `wakeKind`，但那回答的是「离场时是什么叫醒的」——答非所问。
// 用平仓腿归因会把「保护层救回来的单」整类打进 unknown 桶，恰好抹掉最值得复盘的那一类。

import type { RoundTrip } from "./autoTradePnl";
import { WAKE_LABELS } from "./aiTrade";
import type { WakeKind } from "./aiTradeTypes";

/** 低于此样本量一律不给胜率 / 期望值——**绝不用 2 笔样本冒充统计结论**。 */
export const WAKE_ATTRIBUTION_MIN_SAMPLES = 5;

/** 无 `wakeKind` 的往返（老成交 / 唤醒原因总闸关闭的会话）落这个桶，且恒排末位。 */
export const UNKNOWN_WAKE_KIND = "unknown" as const;

export const UNKNOWN_WAKE_LABEL = "未记录来源";

export interface WakeAttributionRow {
  kind: WakeKind | typeof UNKNOWN_WAKE_KIND;
  label: string;
  count: number;
  wins: number;
  /** 样本 < WAKE_ATTRIBUTION_MIN_SAMPLES ⇒ null（不是 0——0 会被读成「一笔没赢」）。 */
  winRate: number | null;
  netUsd: number;
  /** 单笔期望值；同上，样本不足即 null。 */
  expectancyUsd: number | null;
}

/**
 * 按**开仓腿**的唤醒来源归集往返，产出可直接渲染的只读行。
 *
 * 排序：`count` 降序（同数按标签稳定），`unknown` 恒排末位。
 */
export function attributeByWake(trips: RoundTrip[]): WakeAttributionRow[] {
  const buckets = new Map<string, { count: number; wins: number; netUsd: number }>();
  for (const trip of trips) {
    const kind = readEntryWakeKind(trip);
    const bucket = buckets.get(kind) ?? { count: 0, wins: 0, netUsd: 0 };
    bucket.count += 1;
    bucket.wins += trip.isWin ? 1 : 0;
    bucket.netUsd += trip.pnlUsd;
    buckets.set(kind, bucket);
  }
  const rows = [...buckets.entries()].map(([kind, bucket]) => toRow(kind, bucket));
  return rows.sort(compareRows);
}

/** 是否存在**非 unknown** 的样本；false ⇒ 组件整卡不渲染（老会话不该看到一张全「未记录来源」的表）。 */
export function hasWakeAttribution(rows: WakeAttributionRow[]): boolean {
  return rows.some((row) => row.kind !== UNKNOWN_WAKE_KIND && row.count > 0);
}

/** 样本不足时的诚实降级文案（与 `_t_stat` 在 n<2 时返回 None 同款纪律）。 */
export function formatWakeWinRate(row: WakeAttributionRow): string {
  if (row.winRate === null) return `样本不足（n=${row.count}）`;
  return `${(row.winRate * 100).toFixed(0)}%`;
}

// 只读开仓腿：`entry.explain.meta.wakeKind`。缺任何一层都落 unknown，绝不猜。
function readEntryWakeKind(trip: RoundTrip): string {
  const kind = trip.entry.explain?.meta?.wakeKind;
  return typeof kind === "string" && kind ? kind : UNKNOWN_WAKE_KIND;
}

function toRow(
  kind: string,
  bucket: { count: number; wins: number; netUsd: number },
): WakeAttributionRow {
  const enough = bucket.count >= WAKE_ATTRIBUTION_MIN_SAMPLES;
  return {
    kind: kind as WakeAttributionRow["kind"],
    label: labelFor(kind),
    count: bucket.count,
    wins: bucket.wins,
    winRate: enough ? bucket.wins / bucket.count : null,
    netUsd: bucket.netUsd,
    expectancyUsd: enough ? bucket.netUsd / bucket.count : null,
  };
}

// 标签复用 WAKE_LABELS（新增 kind 由 tsc 强制补齐）；后端未来新增而前端尚未镜像的 kind 原样显示，
// 绝不静默吞成「未记录来源」——那会让一整类唤醒凭空消失。
function labelFor(kind: string): string {
  if (kind === UNKNOWN_WAKE_KIND) return UNKNOWN_WAKE_LABEL;
  return WAKE_LABELS[kind as WakeKind]?.label ?? kind;
}

function compareRows(a: WakeAttributionRow, b: WakeAttributionRow): number {
  if (a.kind === UNKNOWN_WAKE_KIND) return 1;
  if (b.kind === UNKNOWN_WAKE_KIND) return -1;
  if (b.count !== a.count) return b.count - a.count;
  return a.label.localeCompare(b.label);
}
