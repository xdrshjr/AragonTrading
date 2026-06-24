// 前端只读镜像后端 engine/regime.REGIME_STRATEGY_HINTS：templateKey → 擅长体制中文标签。
// 仅用于成员选择时的「体制亲和」徽标，不参与任何决策（决策真源恒在后端，C6）。
// 与 combo.ts 同约定：纯函数 + 同名 regimeAffinity.test.ts 单测。

// 体制 key → 中文短标签（镜像后端 REGIME_LABELS 的 primary 子集）。
export const REGIME_LABELS_CN: Record<string, string> = {
  trend_up: "趋势上行",
  trend_down: "趋势下行",
  range: "区间震荡",
  high_volatility: "高波动",
  low_volatility: "低波动",
  breakout: "突破",
  crash: "急跌",
};

// 体制 → 候选策略 templateKey（镜像后端 REGIME_STRATEGY_HINTS）。
const REGIME_STRATEGY_HINTS: Record<string, string[]> = {
  trend_up: ["trend_pullback_long", "ema_cross_scalper", "momentum_breakout"],
  trend_down: ["trend_pullback_short", "ema_cross_scalper"],
  range: ["range_scalping_strategy", "bollinger_reversion"],
  high_volatility: ["momentum_breakout", "vwap_rsi_momentum"],
  low_volatility: ["range_scalping_strategy", "bollinger_reversion"],
  breakout: ["momentum_breakout", "vwap_rsi_momentum"],
  crash: ["trend_pullback_short", "long_shadow_reversal"],
};

// 体制展示顺序（与后端三结构轴 + 波动/事件轴对齐）。
const REGIME_ORDER = [
  "trend_up",
  "trend_down",
  "range",
  "high_volatility",
  "low_volatility",
  "breakout",
  "crash",
];

// 单个体制 key → 中文标签；未知 key 安全降级为原 key（不抛错）。
export function regimeLabel(regime: string): string {
  return REGIME_LABELS_CN[regime] ?? regime;
}

// 给定成员 templateKey，返回其擅长的体制中文标签列表（去重保序）。未命中任何体制返回 []。
export function regimeAffinityLabels(templateKey: string): string[] {
  const labels: string[] = [];
  for (const regime of REGIME_ORDER) {
    if (REGIME_STRATEGY_HINTS[regime]?.includes(templateKey)) {
      labels.push(regimeLabel(regime));
    }
  }
  return labels;
}
