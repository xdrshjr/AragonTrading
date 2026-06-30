// 自动交易收益聚合纯函数库（auto-trade-pnl-records §3.3）。
//
// 全部无副作用、不依赖 React/store/时钟：把分组各成员的成交流配对为「完整交易」往返
// （复用 tradeTrails 的因果配对），按时间窗口筛选并汇总总收益 / 总收益率 / 胜率 / 最佳最差。
// `nowSec` 由调用方注入，便于单测与时区一致性。

import { buildTradeTrailSegments } from "./tradeTrails";
import type { Trade } from "./types";

export type AutoTradeRange = "today" | "7d" | "30d" | "all" | "custom";

export interface RoundTrip {
  instId: string;
  direction: "long" | "short";
  entry: Trade;
  exit: Trade;
  pnlUsd: number; // = exit.pnl（后端真实已实现）
  pnlPct: number | null; // = exit.pnlPct ?? 回退派生（见 derivePct）
  openedTs: number; // entry.ts
  closedTs: number; // exit.ts（用于时间窗口筛选 + 排序）
  isWin: boolean; // pnlUsd > 0
}

// 秒级 UTC 闭区间 [startTs, endTs]。
export interface RangeBounds {
  startTs: number;
  endTs: number;
}

export interface PnlSummary {
  totalPnlUsd: number; // Σ window 内 round-trip pnlUsd
  totalReturnPct: number; // totalPnlUsd / capitalBaseUsd（capitalBaseUsd<=0 → 0）
  count: number; // window 内完整交易笔数
  wins: number;
  losses: number;
  winRate: number; // [0,1]，count===0 → 0
  bestUsd: number; // 单笔最大盈（无样本 → 0）
  worstUsd: number; // 单笔最大亏（无样本 → 0）
}

const SECONDS_PER_DAY = 86_400;

export interface LedgerMember {
  instId: string;
  trades: Trade[];
  leverage: number;
}

/**
 * 单个标的成交流 → 完整交易往返。复用 buildTradeTrailSegments 的因果配对（按 ts+id 排序、单持仓配对）；
 * seeded（历史预热）成交在配对前过滤——非真实成交不计入收益（当前 live-auto 无 seeded，前瞻保护）。
 */
export function buildRoundTrips(
  instId: string,
  trades: Trade[],
  leverage: number,
): RoundTrip[] {
  const real = trades.filter((t) => !t.seeded);
  return buildTradeTrailSegments(real).map((seg) => {
    const pnlUsd = seg.exit.pnl;
    const fallback = derivePct(seg.direction, seg.entry.price, seg.exit.price, leverage);
    return {
      instId,
      direction: seg.direction,
      entry: seg.entry,
      exit: seg.exit,
      pnlUsd,
      pnlPct: seg.exit.pnlPct ?? fallback,
      openedTs: seg.entry.ts,
      closedTs: seg.exit.ts,
      isWin: pnlUsd > 0,
    };
  });
}

/** 跨成员合并为扁平 RoundTrip[]，逐成员配对、绝不跨 instId（R5）。 */
export function buildLedger(members: LedgerMember[]): RoundTrip[] {
  return members.flatMap((m) => buildRoundTrips(m.instId, m.trades, m.leverage));
}

/** 时间窗口 → 秒级闭区间边界。`nowSec` 由调用方注入（不读时钟，可测）。 */
export function resolveRangeBounds(
  range: AutoTradeRange,
  nowSec: number,
  custom?: { startTs?: number; endTs?: number },
): RangeBounds {
  switch (range) {
    case "today":
      return { startTs: localMidnightSec(nowSec), endTs: nowSec };
    case "7d":
      return { startTs: nowSec - 7 * SECONDS_PER_DAY, endTs: nowSec };
    case "30d":
      return { startTs: nowSec - 30 * SECONDS_PER_DAY, endTs: nowSec };
    case "custom":
      return { startTs: custom?.startTs ?? 0, endTs: custom?.endTs ?? nowSec };
    case "all":
    default:
      return { startTs: 0, endTs: nowSec };
  }
}

/** 按平仓时刻 closedTs 落入闭区间 [startTs, endTs] 的往返。 */
export function filterRoundTrips(rts: RoundTrip[], bounds: RangeBounds): RoundTrip[] {
  return rts.filter((rt) => rt.closedTs >= bounds.startTs && rt.closedTs <= bounds.endTs);
}

/** 单遍归约出窗口汇总；空输入返回全零（bestUsd/worstUsd 无样本取 0）。 */
export function summarizeRoundTrips(rts: RoundTrip[], capitalBaseUsd: number): PnlSummary {
  let totalPnlUsd = 0;
  let wins = 0;
  let losses = 0;
  let bestUsd = 0;
  let worstUsd = 0;
  for (const rt of rts) {
    totalPnlUsd += rt.pnlUsd;
    if (rt.pnlUsd > 0) wins += 1;
    else if (rt.pnlUsd < 0) losses += 1;
    if (rt.pnlUsd > bestUsd) bestUsd = rt.pnlUsd;
    if (rt.pnlUsd < worstUsd) worstUsd = rt.pnlUsd;
  }
  const count = rts.length;
  return {
    totalPnlUsd,
    totalReturnPct: capitalBaseUsd > 0 ? totalPnlUsd / capitalBaseUsd : 0,
    count,
    wins,
    losses,
    winRate: count > 0 ? wins / count : 0, // v2（P2-2）守卫 0/0，避免 NaN 外泄
    bestUsd,
    worstUsd,
  };
}

// 后端未给 pnlPct 时的回退派生（极少数旧 snapshot / 边界）：价格变动% × 杠杆，方向化；
// 与后端口径一致（忽略手续费，误差有界）。entry.price<=0 无法计算 → null（由 fmtPct 显示「—」）。
function derivePct(
  direction: "long" | "short",
  entryPrice: number,
  exitPrice: number,
  leverage: number,
): number | null {
  if (entryPrice <= 0) return null;
  const sign = direction === "long" ? 1 : -1;
  return sign * (exitPrice / entryPrice - 1) * leverage;
}

// 本地零点（清零时分秒毫秒）的秒级时间戳——「今日」窗口起点，按本地时区。
function localMidnightSec(nowSec: number): number {
  const d = new Date(nowSec * 1000);
  d.setHours(0, 0, 0, 0);
  return Math.floor(d.getTime() / 1000);
}
