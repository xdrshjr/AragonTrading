// OHLC 数值统计纯函数（chart-workspace-layout 需求 2，R2）。
// 与 format.ts / compare.ts / indicators.ts 同约定：纯函数 + 同名 ohlc.test.ts 单测；
// 抽出组件外是为了空安全 + 除零守卫，并规避 signed/colorForReturn 形参不接受 null 的类型错误。
import type { Candle } from "./types";

export interface OhlcStats {
  change: number | null; // c - prevClose
  changePct: number | null; // change / prevClose（prevClose>0 时）
  amplitude: number | null; // (h - l) / prevClose（prevClose>0 时；A 股口径：最高−最低相对昨收）
}

// 由当前 / 被选中 K 线与其昨收基准计算涨跌额、涨跌幅与振幅。
// candle 或 prevClose 为空 → 三字段全 null；prevClose<=0 → 幅度类字段 null（不产生 Infinity）。
export function computeOhlcStats(candle: Candle | null, prevClose: number | null): OhlcStats {
  if (!candle || prevClose == null) return { change: null, changePct: null, amplitude: null };
  const change = candle.c - prevClose;
  const usable = prevClose > 0;
  return {
    change,
    changePct: usable ? change / prevClose : null,
    amplitude: usable ? (candle.h - candle.l) / prevClose : null,
  };
}
