// 实盘下单纯计算（无 IO）：仓位↔名义↔保证金、滑块%↔张数、精度取整、止盈止损价换算、下单校验。
// 与后端 order_builder 口径一致（张为单位、ctVal 换算）；便于单测与组件复用（spec §2.2/§2.4）。
import type { OrderDirection, OrderType } from "./tradingTypes";

// 合约 sz 单位是「张」(contracts)；名义价值 / 币量经 ctVal（合约面值）换算（§2.3）。
export function computeNotional(sz: number, ctVal: number, px: number): number {
  return sz * ctVal * px;
}

export function computeMargin(notional: number, lever: number): number {
  return lever > 0 ? notional / lever : notional;
}

export function computeCoinQty(sz: number, ctVal: number): number {
  return sz * ctVal;
}

// 由步进值推断小数位数（展示 / 取整精度，与 OKX px/sz 字符串口径一致）。
export function stepDecimals(step: number): number {
  if (!(step > 0)) return 0;
  const text = step.toFixed(12).replace(/0+$/, "");
  const dot = text.indexOf(".");
  return dot < 0 ? 0 : text.length - dot - 1;
}

// 张数向下对齐到 lotSz 整数倍（宁可少下，杜绝超额）。
export function snapSize(sz: number, lotSz: number): number {
  if (!(lotSz > 0) || !Number.isFinite(sz)) return Math.max(0, sz);
  const snapped = Math.floor(sz / lotSz + 1e-9) * lotSz;
  return Number(snapped.toFixed(stepDecimals(lotSz)));
}

// 价格对齐到 tickSz 最近整数倍。
export function snapPrice(px: number, tickSz: number): number {
  if (!(tickSz > 0) || !Number.isFinite(px)) return px;
  const snapped = Math.round(px / tickSz) * tickSz;
  return Number(snapped.toFixed(stepDecimals(tickSz)));
}

// 给定可用 USDT / 杠杆 / 价格，可开的最大张数（对齐 lotSz）。px/ctVal/lotSz 非正时回 0。
export function maxSizeForBudget(
  availUsdt: number,
  lever: number,
  ctVal: number,
  px: number | null,
  lotSz: number,
): number {
  const price = px ?? 0;
  if (!(price > 0) || !(ctVal > 0) || !(lever > 0)) return 0;
  const maxNotional = availUsdt * lever;
  return snapSize(maxNotional / (ctVal * price), lotSz);
}

// 滑块百分比 (0..100) → 张数（按 maxSz 线性映射后对齐 lotSz）。
export function sliderPctToSize(pct: number, maxSz: number, lotSz: number): number {
  const clamped = Math.min(Math.max(pct, 0), 100);
  return snapSize((clamped / 100) * maxSz, lotSz);
}

// 张数 → 滑块百分比 (0..100)；maxSz≤0 回 0。
export function sizeToSliderPct(sz: number, maxSz: number): number {
  if (!(maxSz > 0)) return 0;
  return Math.min(Math.max((sz / maxSz) * 100, 0), 100);
}

// 按金额（USDT 预计盈亏）换算止盈 / 止损触发价（多空对称，§2.4）。无效输入回 null（不下发该字段）。
export function tpSlTriggerFromAmount(
  direction: OrderDirection,
  kind: "tp" | "sl",
  entryPx: number,
  amount: number,
  sz: number,
  ctVal: number,
): number | null {
  const coinQty = sz * ctVal;
  if (!(coinQty > 0) || !(entryPx > 0) || !(amount > 0)) return null;
  const delta = amount / coinQty;
  // 多：止盈在上、止损在下；空：止盈在下、止损在上。
  const up = direction === "long" ? kind === "tp" : kind === "sl";
  return up ? entryPx + delta : entryPx - delta;
}

export interface OrderDraftCheck {
  ordType: OrderType;
  px: number | null;
  sz: number;
  lever: number;
  minSz: number;
  maxLever: number;
  instrumentSupported: boolean;
}

export interface OrderValidation {
  valid: boolean;
  errors: { px?: string; sz?: string; lever?: string };
}

// 下单前校验：限价须有正价、sz≥minSz、杠杆在 [1, maxLever]；标的不支持直接判不可下单。
export function validateOrder(input: OrderDraftCheck): OrderValidation {
  const errors: OrderValidation["errors"] = {};
  if (input.ordType === "limit" && !(input.px !== null && input.px > 0)) {
    errors.px = "请输入大于 0 的限价";
  }
  if (!(input.sz > 0) || input.sz < input.minSz) {
    errors.sz = `数量需 ≥ 最小下单量 ${input.minSz} 张`;
  }
  if (!(input.lever >= 1) || input.lever > input.maxLever) {
    errors.lever = `杠杆需在 1 ~ ${input.maxLever}× 之间`;
  }
  return { valid: input.instrumentSupported && Object.keys(errors).length === 0, errors };
}
