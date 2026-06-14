// 当前持仓推导纯函数（chart-workspace-layout 需求 3，R6/R7）。
// 与 format.ts / compare.ts / ohlc.ts 同约定：纯函数 + 同名 position.test.ts 单测；不依赖 React / DOM。
// 后端真实模型（已核验 engine/simulator.py:227/244）：positionAfter = +qty 多 / −qty 空 / 0 平，
// 且当前引擎「整开整平、无加/减仓」。加权均价 / 部分平 / 反手分支为前瞻护栏（未来多笔成交可直接复用）。
import type { Trade } from "./types";

export interface OpenPosition {
  side: "long" | "short";
  size: number; // 持仓量（正数量级）
  avgEntry: number; // 开仓加权均价
  markPrice: number | null; // 标记价
  unrealizedPnl: number | null; // (mark-avg)*signedSize
  unrealizedPct: number | null; // unrealizedPnl / (avgEntry*size)
}

/**
 * 由成交序列推导「当前未平仓持仓」。纯函数、确定性、可单测。
 * 约定：side 'long'=开/加多，'short'=开/加空，'close'=平仓（向 0 收敛）。
 *
 * @param trades 成交序列（任意顺序，内部按 ts 升序回放）。
 * @param markPrice 标记价（给出浮动盈亏；为 null 时盈亏字段为 null）。
 * @param asOfTs 给定时只考虑 ts<=asOfTs 的成交（用于按选中时点回放持仓，P2）。
 * @returns 当前持仓；空仓返回 null。
 */
export function deriveOpenPosition(
  trades: Trade[],
  markPrice: number | null,
  asOfTs?: number,
): OpenPosition | null {
  const rows = trades
    .filter((t) => asOfTs == null || t.ts <= asOfTs)
    .slice()
    .sort((a, b) => a.ts - b.ts);
  let size = 0; // 有符号：+多 / -空
  let avgEntry = 0;
  const EPS = 1e-9;
  for (const t of rows) {
    if (t.side === "close") {
      const dir = Math.sign(size);
      const remaining = Math.abs(size) - t.qty;
      if (remaining <= EPS) {
        size = 0;
        avgEntry = 0;
      } else {
        size = dir * remaining; // 平仓不改均价
      }
      continue;
    }
    const dir = t.side === "long" ? 1 : -1;
    if (size === 0 || Math.sign(size) === dir) {
      // 开 / 同向加仓 → 加权均价
      const absOld = Math.abs(size);
      avgEntry = (avgEntry * absOld + t.price * t.qty) / (absOld + t.qty);
      size += dir * t.qty;
    } else {
      // 反向以 open 入场（少见）→ 视作减仓 / 反手
      const remaining = Math.abs(size) - t.qty;
      if (remaining > EPS) {
        size = Math.sign(size) * remaining;
      } else if (remaining < -EPS) {
        size = dir * -remaining;
        avgEntry = t.price;
      } else {
        size = 0;
        avgEntry = 0;
      }
    }
  }
  if (Math.abs(size) <= EPS) return null; // 空仓
  const mag = Math.abs(size);
  const pnl = markPrice != null ? (markPrice - avgEntry) * size : null;
  const pct = markPrice != null && avgEntry > 0 ? (pnl as number) / (avgEntry * mag) : null;
  return {
    side: size > 0 ? "long" : "short",
    size: mag,
    avgEntry,
    markPrice,
    unrealizedPnl: pnl,
    unrealizedPct: pct,
  };
}
