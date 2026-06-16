// 实时模拟净值序列合并（live-paper-account-mode B2，修复 P1-3）。
// 与 format.ts / position.ts 同约定：纯函数 + 同名 liveSeries.test.ts；不依赖 React / DOM。
//
// 为何按 ts upsert 而非追加：逐笔瞬时盯市点（transient）与同根确认净值点**共享成形根 ts**，
// 纯追加会产生重复 ts，违反 lightweight-charts「time 严格递增」不变量。upsert 使同 ts 收敛为单点，
// 确认点（后到、非 transient）覆盖瞬时点，结算后的权威值留存。
import type { EquityPoint } from "./types";

/**
 * 把一个净值点并入序列：同 ts 覆盖、整体保持 ts 升序。
 *
 * @param prev 现有净值序列（升序）。
 * @param point 新到的净值点（transient 或确认）。
 * @returns 新序列；ts 唯一且严格升序。
 */
export function mergeEquityByTs(prev: EquityPoint[], point: EquityPoint): EquityPoint[] {
  const map = new Map<number, EquityPoint>();
  for (const p of prev) map.set(p.ts, p);
  map.set(point.ts, point); // 同 ts 覆盖（确认点覆盖 transient 点）
  return Array.from(map.values()).sort((a, b) => a.ts - b.ts);
}
