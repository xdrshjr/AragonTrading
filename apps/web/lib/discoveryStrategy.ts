// 策略轴的纯函数层（discovery-strategy-axes §3.3）：指标格式化、S 闸标签、参数差异、
// 按轴列定义、让渡文案。零副作用、零依赖，可单测。
//
// 与 `discovery.ts` 分文件的理由：因子轴的口径是统计式（IC / IR / 单调性），策略轴是路径依赖
// 的回测（夏普 / 回撤 / 笔数），两套口径**不可比**——混在一个文件里迟早被读错。

import { rejectReasonCn } from "./discovery";
import type {
  AxisPlanEntry,
  DiscoveryAxis,
  DiscoveryCandidateRow,
  DiscoveryGateRow,
  DiscoveryParamDiff,
  DiscoveryStrategyMetrics,
  DiscoveryStrategySide,
} from "./discoveryTypes";

export const AXIS_LABEL_CN: Record<string, string> = {
  factor: "因子",
  factor_combo: "因子组合策略",
  strategy_param: "常规策略",
  strategy_combo: "策略轮动组合",
};

export const AXIS_SHORT_CN: Record<string, string> = {
  factor: "因子",
  factor_combo: "因子组合",
  strategy_param: "常规策略",
  strategy_combo: "策略轮动",
};

export const READINESS_REASON_CN: Record<string, string> = {
  no_minable_template: "没有可挖的策略模板",
  member_catalog_too_small: "因子成员目录不足 2 条",
  upstream_pool_too_small: "常规策略池不足，等待上游产出",
  not_selected: "本任务未启用该轴",
};

/** 两条口径的中文说明（BL-3）：**不得**用一套话术盖住两种切法。 */
export const CLIP_MODE_CN: Record<string, string> = {
  gated:
    "样本外为「前伸暖机 + 只暖机不交易」的纯切片：暖机区零成交，起点权益恒等于初始资金。",
  rebased:
    "轮动组合的净值由成员曲线合成、无法逐根 gate，样本外改用「权益重基」切片：" +
    "回撤按切片后的运行峰值重算，跨界那一笔的盈亏会留在切片内、笔数按切点之后统计。" +
    "这与另两轴的口径不同，两者的样本外指标不可直接比较。",
};

export const STRATEGY_AXES: DiscoveryAxis[] = [
  "factor_combo",
  "strategy_param",
  "strategy_combo",
];

export function isStrategyAxis(kind: string): boolean {
  return STRATEGY_AXES.includes(kind as DiscoveryAxis);
}

export function axisLabel(axis: string): string {
  return AXIS_LABEL_CN[axis] ?? axis;
}

// ============================ 数值格式化 ============================
export function fmtNumber(value: unknown, digits = 2): string {
  const num = Number(value);
  return Number.isFinite(num) ? num.toFixed(digits) : "—";
}

export function fmtPercent(value: unknown, digits = 2): string {
  const num = Number(value);
  return Number.isFinite(num) ? `${(num * 100).toFixed(digits)}%` : "—";
}

export function fmtCount(value: unknown): string {
  const num = Number(value);
  return Number.isFinite(num) ? String(Math.round(num)) : "—";
}

/** 单段指标 → 展示行（IS / OOS 双栏共用一份定义，避免两处各写一遍标签）。 */
export function sideRows(side: DiscoveryStrategySide | undefined): {
  label: string;
  value: string;
}[] {
  return [
    { label: "夏普", value: fmtNumber(side?.sharpe) },
    { label: "总收益", value: fmtPercent(side?.totalReturn) },
    { label: "最大回撤", value: fmtPercent(side?.maxDrawdown) },
    { label: "Calmar", value: fmtNumber(side?.calmar) },
    { label: "盈亏比", value: fmtNumber(side?.profitFactor) },
    { label: "胜率", value: fmtPercent(side?.winRate) },
    { label: "平仓笔数", value: fmtCount(side?.tradeCount) },
    { label: "交易密度", value: fmtNumber(side?.tradeRate, 4) },
  ];
}

// ============================ S 闸阶梯 ============================
/** 逐条闸 → 展示行；卡住的那条带中文原因（详情抽屉显示「卡在哪、差多少」）。 */
export function gateRows(gates: DiscoveryGateRow[] | undefined): (DiscoveryGateRow & {
  reasonCn: string;
})[] {
  return (gates ?? []).map((gate) => ({
    ...gate,
    reasonCn: gate.passed ? "" : rejectReasonCn(gate.id),
  }));
}

/** 第一条未通过的闸（UI 高亮它；全通过返回 null）。 */
export function blockingGate(gates: DiscoveryGateRow[] | undefined): DiscoveryGateRow | null {
  return (gates ?? []).find((gate) => !gate.passed) ?? null;
}

// ============================ 参数差异 ============================
export function paramDiffRows(metrics: Partial<DiscoveryStrategyMetrics>): DiscoveryParamDiff[] {
  return metrics.paramDiff ?? [];
}

export function formatParamValue(value: unknown): string {
  if (value === null || value === undefined) return "—";
  if (typeof value === "boolean") return value ? "是" : "否";
  if (typeof value === "number") {
    return Number.isInteger(value) ? String(value) : String(Number(value.toFixed(4)));
  }
  return String(value);
}

// ============================ 按轴列定义 ============================
export interface ColumnDef {
  key: string;
  label: string;
  align?: "left" | "right";
  value: (row: DiscoveryCandidateRow) => string;
}

const FACTOR_COLUMNS: ColumnDef[] = [
  { key: "isIc", label: "IS RankIC", align: "right",
    value: (row) => fmtNumber(row.metrics.is?.rankIc, 4) },
  { key: "oosIc", label: "OOS RankIC", align: "right",
    value: (row) => fmtNumber(row.metrics.oos?.rankIc, 4) },
  { key: "ir", label: "IR", align: "right",
    value: (row) => fmtNumber(row.metrics.is?.ir, 3) },
  { key: "turnover", label: "换手", align: "right",
    value: (row) => fmtNumber(row.metrics.turnoverProxy, 3) },
];

const STRATEGY_COLUMNS: ColumnDef[] = [
  { key: "isSharpe", label: "IS 夏普", align: "right",
    value: (row) => fmtNumber(row.metrics.is?.sharpe) },
  { key: "isReturn", label: "IS 收益", align: "right",
    value: (row) => fmtPercent(row.metrics.is?.totalReturn) },
  { key: "oosSharpe", label: "OOS 夏普", align: "right",
    value: (row) => fmtNumber(row.metrics.oos?.sharpe) },
  { key: "drawdown", label: "回撤", align: "right",
    value: (row) => fmtPercent(row.metrics.is?.maxDrawdown) },
  { key: "trades", label: "笔数", align: "right",
    value: (row) => fmtCount(row.metrics.is?.tradeCount) },
];

/**
 * 候选表的指标列定义。
 *
 * `kind` 为空（混排「全部轴」）时取两套列的**交集口径**——因子的 IC 与策略的夏普不可比，
 * 混排时只给一列徽标区分轴，指标列退化为各轴自己的首要指标。
 */
export function columnsForKind(kind: string): ColumnDef[] {
  if (kind === "factor") return FACTOR_COLUMNS;
  if (isStrategyAxis(kind)) return STRATEGY_COLUMNS;
  return [
    { key: "primary", label: "首要指标", align: "right",
      value: (row) =>
        isStrategyAxis(row.kind)
          ? `夏普 ${fmtNumber(row.metrics.is?.sharpe)}`
          : `IC ${fmtNumber(row.metrics.is?.rankIc, 4)}` },
    { key: "oos", label: "样本外", align: "right",
      value: (row) =>
        isStrategyAxis(row.kind)
          ? `夏普 ${fmtNumber(row.metrics.oos?.sharpe)}`
          : `IC ${fmtNumber(row.metrics.oos?.rankIc, 4)}` },
  ];
}

// ============================ 让渡文案（BL-9 的可见性落点） ============================
/** `yieldedByPool` 的一条 → 中文一句话（含 `toAxis`：让渡去了哪条轴必须说清）。 */
export function yieldSentence(axis: string, entry: AxisPlanEntry): string {
  const target = entry.toAxis ? axisLabel(entry.toAxis) : "";
  const reason = READINESS_REASON_CN[entry.reason] ?? entry.reason;
  const tail = target ? ` → ${target}` : "（本轮无可接收的轴）";
  return `${axisLabel(axis)} ${entry.count}${tail}（原因：${reason}）`;
}

export function yieldSentences(
  yielded: Record<string, AxisPlanEntry> | undefined,
): { axis: string; text: string }[] {
  return Object.entries(yielded ?? {}).map(([axis, entry]) => ({
    axis,
    text: yieldSentence(axis, entry),
  }));
}

/** 覆盖图是否该显示：两条组合轴的机制恒为单值，画出来就是一根 100% 的柱子（P2-11）。 */
export function shouldShowCoverage(axes: string[] | undefined): boolean {
  const list = axes ?? [];
  if (!list.length) return true;
  return list.some((axis) => axis === "factor" || axis === "strategy_param");
}

/** 单候选每轮的底层回测次数预估（启动器的「预计每轮回测次数」用，与 §六 同口径）。 */
export function backtestsPerIteration(
  axes: string[],
  candidates: number,
  info: Record<string, { backtestsPerCandidate: number }> | undefined,
): number {
  if (!axes.length || candidates <= 0) return 0;
  const share = Math.floor(candidates / axes.length);
  return axes.reduce(
    (sum, axis) => sum + share * (info?.[axis]?.backtestsPerCandidate ?? 0),
    0,
  );
}
