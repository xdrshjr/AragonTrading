// 平仓纯计算（无 IO）：默认平仓张数、平仓数量校验、实现盈亏估算。
// 与后端 order_builder/validate_close 口径一致（张为单位、ctVal 换算、lotSz 取整），便于单测与组件复用（spec §4.3）。
import { snapSize } from "./liveOrder";

// 默认平仓张数 = 全仓 |pos|（对齐 lotSz、向下取整杜绝超额）。
export function defaultCloseSize(pos: number, lotSz: number): number {
  return snapSize(Math.abs(pos), lotSz);
}

export interface CloseSizeValidation {
  valid: boolean;
  error?: string;
}

// 限价平仓数量校验：取整后 >0、≥minSz、≤|pos|（与后端 validate_close 一致，P1-2）。
export function validateCloseSize(
  sz: number,
  pos: number,
  minSz: number,
  lotSz: number,
): CloseSizeValidation {
  const absPos = Math.abs(pos);
  const snapped = snapSize(sz, lotSz);
  if (!(snapped > 0)) return { valid: false, error: "平仓数量需大于 0" };
  if (snapped < minSz) return { valid: false, error: `平仓数量需 ≥ 最小下单量 ${minSz} 张` };
  if (snapped > absPos) return { valid: false, error: `平仓数量不可超过当前持仓 ${absPos} 张` };
  return { valid: true };
}

// 估算实现盈亏（USDT，仅按 closePx 的近似）：多 (closePx-avgPx)、空 (avgPx-closePx)，乘币量 sz*ctVal。
export function estimateRealizedPnl(
  isLong: boolean,
  avgPx: number,
  closePx: number,
  sz: number,
  ctVal: number,
): number {
  const coinQty = sz * ctVal;
  if (!(coinQty > 0) || !(avgPx > 0) || !(closePx > 0)) return 0;
  const diff = isLong ? closePx - avgPx : avgPx - closePx;
  return diff * coinQty;
}
