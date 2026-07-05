// AI 回测纯件（无副作用、易单测）：计划决策轮数估算、区间校验、阶段文案、动作分布。
// 与后端 §2.5/§2.9 归一估算式同式（tradableBars=(end-start)/barSec+1，plannedRounds=ceil(tradable/every)）。
import type { AiTradeDecision } from "./aiTradeTypes";
import type { AiBacktestPhase } from "./aiBacktestTypes";

// 决策成本护栏默认（与后端 config.ai_backtest_* 对齐；服务端仍权威校验，前端仅作预览/智能默认）。
export const AI_BACKTEST_MAX_ROUNDS = 400;
export const AI_BACKTEST_DEFAULT_DECISION_EVERY = 1;

// 标准 OKX 周期→秒（与后端 okx/types.BAR_SECONDS 对齐；未知周期回落 1H）。
export const BAR_SECONDS: Record<string, number> = {
  "1m": 60,
  "3m": 180,
  "5m": 300,
  "15m": 900,
  "30m": 1800,
  "1H": 3600,
  "2H": 7200,
  "4H": 14400,
  "6H": 21600,
  "12H": 43200,
  "1D": 86400,
};

/** 周期→秒（未知回落 1H=3600）。 */
export function barSeconds(bar: string): number {
  return BAR_SECONDS[bar] ?? 3600;
}

/** 区间可交易根数（含端点）：`floor((end-start)/barSec)+1`；非正区间回 0。 */
export function tradableBars(startTs: number, endTs: number, bar: string): number {
  if (endTs <= startTs) return 0;
  return Math.floor((endTs - startTs) / barSeconds(bar)) + 1;
}

/** 计划决策轮数估算：`ceil(tradableBars / decisionEveryBars)`（与后端同式；every≤0 回落 1）。 */
export function planRounds(startTs: number, endTs: number, bar: string, every: number): number {
  const bars = tradableBars(startTs, endTs, bar);
  if (bars <= 0) return 0;
  const step = every > 0 ? Math.floor(every) : 1;
  return Math.ceil(bars / step);
}

/** UI 默认决策间隔：`max(configDefault, ceil(tradableBars/maxRounds))`，使默认配置永不因越界自拒（R5）。 */
export function defaultDecisionEvery(
  startTs: number,
  endTs: number,
  bar: string,
  configDefault: number,
  maxRounds: number,
): number {
  const bars = tradableBars(startTs, endTs, bar);
  if (bars <= 0 || maxRounds <= 0) return Math.max(1, configDefault);
  return Math.max(configDefault, Math.ceil(bars / maxRounds));
}

/** 区间校验：起止顺序 / 未来 / 太短；返回错误文案或 null。 */
export function validateBacktestRange(
  startTs: number,
  endTs: number,
  nowTs: number = Math.floor(Date.now() / 1000),
): string | null {
  if (!Number.isFinite(startTs) || !Number.isFinite(endTs)) return "请选择回测区间起止时间";
  if (startTs >= endTs) return "回测起始时间需早于结束时间";
  if (startTs > nowTs) return "回测起始时间不能在未来";
  return null;
}

const PHASE_LABELS: Record<AiBacktestPhase, string> = {
  loading: "加载历史行情",
  priming: "策略暖机",
  running: "AI 逐根决策",
  finalizing: "汇总指标",
};

/** 回测阶段中文化（缺省回原值）。 */
export function describeBacktestPhase(phase: string): string {
  return PHASE_LABELS[phase as AiBacktestPhase] ?? phase;
}

const STATE_LABELS: Record<string, string> = {
  running: "运行中",
  completed: "已完成",
  cancelled: "已取消",
  failed: "失败",
};

/** 作业状态中文化。 */
export function describeBacktestState(state: string): string {
  return STATE_LABELS[state] ?? state;
}

export interface ActionSlice {
  action: string;
  label: string;
  count: number;
  pct: number;
}

const ACTION_LABELS: Record<string, string> = {
  open_long: "开多",
  open_short: "开空",
  close: "平仓",
  hold: "观望",
};
const ACTION_ORDER = ["open_long", "open_short", "close", "hold"];

/** 动作分布：把 actionCounts 归一为有序切片（含占比%），供决策动作分布条。 */
export function actionDistribution(counts: Record<string, number> | null | undefined): ActionSlice[] {
  const safe = counts ?? {};
  const total = ACTION_ORDER.reduce((sum, a) => sum + (safe[a] ?? 0), 0);
  return ACTION_ORDER.map((action) => {
    const count = safe[action] ?? 0;
    return {
      action,
      label: ACTION_LABELS[action] ?? action,
      count,
      pct: total > 0 ? count / total : 0,
    };
  });
}

/** 从决策序列聚合动作计数（无 aiStats 时的兜底，如运行态实时列表）。 */
export function countActions(decisions: AiTradeDecision[]): Record<string, number> {
  const counts: Record<string, number> = { open_long: 0, open_short: 0, close: 0, hold: 0 };
  for (const d of decisions) counts[d.action] = (counts[d.action] ?? 0) + 1;
  return counts;
}

/** 进度百分比（0~100 整数，越界夹紧）。 */
export function progressPercent(pct: number | null | undefined): number {
  const v = (pct ?? 0) * 100;
  return Math.max(0, Math.min(100, Math.round(v)));
}
