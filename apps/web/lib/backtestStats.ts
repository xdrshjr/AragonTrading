// AI 回测前端派生统计（ai-auto-trading-backtest-analytics §2.4，纯件、无副作用、易单测）。
// 全部从既有 result.equity（EquityPoint{ts,equity,drawdown}）派生，无后端改动、无网络。
import type { EquityPoint } from "./types";

export type ReturnPeriod = "day" | "week" | "month";

export interface UnderwaterPoint {
  ts: number;
  drawdown: number; // ≤0
}

export interface PeriodReturn {
  label: string;
  ret: number;
  ts: number;
}

export interface DrawdownWindow {
  startTs: number;
  endTs: number;
  depth: number; // ≤0
}

const DAY = 86400;
const WEEK = 604800;
const BUCKET_MAX = 60; // 桶数 > 60 触发降密（day→week→month，§2.4）

// UTC ISO 日期（YYYY-MM-DD）：秒级 ts → 本系统统一 UTC 展示口径。
function isoDate(tsSeconds: number): string {
  return new Date(tsSeconds * 1000).toISOString().slice(0, 10);
}

/** 水下回撤序列：优先取 equity[].drawdown，缺失/非有限则用运行峰值现算兜底（§2.4）。 */
export function underwaterSeries(equity: EquityPoint[]): UnderwaterPoint[] {
  let peak = -Infinity;
  return equity.map((p) => {
    peak = Math.max(peak, p.equity);
    const has = typeof p.drawdown === "number" && Number.isFinite(p.drawdown);
    const fallback = peak > 0 ? Math.min(p.equity / peak - 1, 0) : 0;
    return { ts: p.ts, drawdown: has ? p.drawdown : fallback };
  });
}

// 分桶键：epoch 对齐的 day/week 桶号，或 UTC 年-月。
function bucketKey(tsSeconds: number, period: ReturnPeriod): string {
  if (period === "day") return String(Math.floor(tsSeconds / DAY));
  if (period === "week") return String(Math.floor(tsSeconds / WEEK));
  const d = new Date(tsSeconds * 1000);
  return `${d.getUTCFullYear()}-${d.getUTCMonth()}`;
}

function bucketLabel(tsSeconds: number, period: ReturnPeriod): string {
  if (period === "month") return isoDate(tsSeconds).slice(0, 7); // YYYY-MM
  return isoDate(tsSeconds);
}

// 按周期分桶，桶收益 = 桶末权益/桶初权益 − 1（保持 ts 升序）。
function bucketReturns(equity: EquityPoint[], period: ReturnPeriod): PeriodReturn[] {
  const order: string[] = [];
  const acc = new Map<string, { first: number; last: number; ts: number; label: string }>();
  for (const p of equity) {
    const key = bucketKey(p.ts, period);
    let b = acc.get(key);
    if (!b) {
      b = { first: p.equity, last: p.equity, ts: p.ts, label: bucketLabel(p.ts, period) };
      acc.set(key, b);
      order.push(key);
    }
    b.last = p.equity;
  }
  return order.map((k) => {
    const b = acc.get(k)!;
    return { label: b.label, ts: b.ts, ret: b.first !== 0 ? b.last / b.first - 1 : 0 };
  });
}

/**
 * 周期收益分布：显式 period 则按之分桶，否则从 day 起，桶数 > 60 时逐级降密到 week→month（§2.4）。
 * month 为最粗粒度（多年回测封顶到月），少于 2 点无法计收益 → 空数组。
 */
export function periodReturns(equity: EquityPoint[], period?: ReturnPeriod): PeriodReturn[] {
  if (equity.length < 2) return [];
  if (period) return bucketReturns(equity, period);
  const day = bucketReturns(equity, "day");
  if (day.length <= BUCKET_MAX) return day;
  const week = bucketReturns(equity, "week");
  if (week.length <= BUCKET_MAX) return week;
  return bucketReturns(equity, "month");
}

/** 最深回撤区间：从触发峰值到最深谷底的 {startTs,endTs,depth}；无回撤时 null。 */
export function maxDrawdownWindow(equity: EquityPoint[]): DrawdownWindow | null {
  const dd = underwaterSeries(equity);
  let troughIdx = -1;
  let depth = 0;
  for (let i = 0; i < dd.length; i++) {
    if (dd[i].drawdown < depth) {
      depth = dd[i].drawdown;
      troughIdx = i;
    }
  }
  if (troughIdx < 0) return null;
  let startIdx = troughIdx;
  for (let i = troughIdx - 1; i >= 0; i--) {
    startIdx = i;
    if (dd[i].drawdown >= 0) break; // 回到水面 = 该段回撤的起点峰值
  }
  return { startTs: dd[startIdx].ts, endTs: dd[troughIdx].ts, depth };
}

/** 时长（秒）→ 紧凑中文（天/小时/分），供最长水下时长展示。 */
export function fmtDuration(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds <= 0) return "—";
  if (seconds >= DAY) return `${(seconds / DAY).toFixed(1)} 天`;
  if (seconds >= 3600) return `${Math.round(seconds / 3600)} 小时`;
  return `${Math.round(seconds / 60)} 分`;
}
