// 高级回测（滑动窗口 / Walk-forward）纯展示助手：窗口收益柱状图几何 + 最好/最差窗口定位。
// 与 format.ts / combo.ts 同约定：纯函数 + 同名 walkForward.test.ts 单测，组件仅消费此处逻辑。
import type { WalkForwardWindow } from "./types";

export interface WindowBarGeometry {
  index: number; // 窗口序号（0 起）
  value: number; // 该窗口收益率（metrics.totalReturn）
  heightPct: number; // |value| / 最大绝对值 * 100，夹取 [0,100]（柱高百分比）
  positive: boolean; // value >= 0（正绿负红配色）
}

// 把各窗口收益率映射为柱状图几何：以最大绝对值归一化柱高（空集回落空数组；全 0 时柱高为 0）。
export function windowBarGeometry(returns: number[]): WindowBarGeometry[] {
  const maxAbs = returns.reduce((m, r) => Math.max(m, Math.abs(r)), 0);
  const denom = maxAbs > 0 ? maxAbs : 1; // 防除零：全 0 时柱高恒为 0
  return returns.map((value, index) => ({
    index,
    value,
    positive: value >= 0,
    heightPct: Math.min(100, (Math.abs(value) / denom) * 100),
  }));
}

// 定位最好 / 最差窗口下标（按 metrics.totalReturn）；空集返回 {best:-1, worst:-1}。
// 并列时取首个命中（稳定，便于高亮一致）。
export function bestWorstWindowIndex(windows: WalkForwardWindow[]): { best: number; worst: number } {
  if (windows.length === 0) return { best: -1, worst: -1 };
  let best = 0;
  let worst = 0;
  windows.forEach((w, i) => {
    if (w.metrics.totalReturn > windows[best].metrics.totalReturn) best = i;
    if (w.metrics.totalReturn < windows[worst].metrics.totalReturn) worst = i;
  });
  return { best, worst };
}
