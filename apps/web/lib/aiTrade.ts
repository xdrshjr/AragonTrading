// AI 自动交易纯件（无副作用、易单测）：开关守门文案矩阵、策略/轮数/预算校验、动作与停止原因中文化。
// 预算派生/夹紧复用 live-auto 同款（0.01 默认 / 0.05 上限，与后端 ai_trade_default/max_capital_frac 对齐）。
import { clampBudgetToCap, deriveDefaultBudget } from "./liveAuto";
import type { AccountMode } from "./types";
import type {
  AiTradeAction,
  AiTradeDecision,
  AiTradeStopReason,
  Consensus,
  MarketContext,
} from "./aiTradeTypes";

export { clampBudgetToCap, deriveDefaultBudget };

// 上下文刷新轮数区间（与后端 ai_trade_min/max_context_rounds 对齐；默认 10）。
export const AI_TRADE_MIN_CONTEXT_ROUNDS = 3;
export const AI_TRADE_MAX_CONTEXT_ROUNDS = 200;
export const AI_TRADE_DEFAULT_CONTEXT_ROUNDS = 10;
export const AI_TRADE_MAX_STRATEGIES = 8;
// AI 自动交易「分组」标的数上限（与后端 ai_trade_max_symbols=5 对齐）：弹窗回填 autoTradeMembers 时至多取
// 前 N 个（超出提示并禁增，P1-2）。刻意 < live_auto_max_group_symbols(8)：N 个 decider 每轮各一次 LLM 调用。
export const AI_TRADE_MAX_SYMBOLS = 5;
// 单仓 OCO 比例夹紧上限（fraction，与后端 ai_trade_max_sl_pct/tp_pct 对齐，E4 弹窗预览用）。
export const AI_TRADE_MAX_SL_PCT = 0.2;
export const AI_TRADE_MAX_TP_PCT = 0.5;

// 开关守门输入（不含策略数——策略在启动弹窗内多选；运行态开关恒可点用于停止）。
export interface AiTradeGateInput {
  accountMode: AccountMode;
  hasReadyDefault: boolean;
  aiTradeEnabled: boolean;
  agentConfigured: boolean;
  liveAutoActive: boolean;
  hasActiveSession: boolean;
}

/** 守门文案矩阵：返回禁用原因（悬浮提示），全部通过返回 null。运行态恒返回 null（开关用于停止）。 */
export function resolveAiTradeGate(input: AiTradeGateInput): string | null {
  if (input.hasActiveSession) return null;
  if (input.accountMode !== "live") return "请先切换到实盘账户模式";
  if (!input.hasReadyDefault) return "请先在设置中配置默认实盘账户";
  if (!input.aiTradeEnabled) return "AI 自动交易未启用（需后端开启 AI_TRADE_ENABLED）";
  if (!input.agentConfigured) return "请先在设置中心配置 AI 模型";
  if (input.liveAutoActive) return "已有活跃策略自动交易分组，请先停止（与 AI 互斥）";
  return null;
}

/** 校验多选策略：至少 1 个、至多上限；返回错误文案或 null。 */
export function validateStrategySelection(
  ids: number[],
  max: number = AI_TRADE_MAX_STRATEGIES,
): string | null {
  if (ids.length === 0) return "请至少选择一个交易策略";
  if (ids.length > max) return `最多选择 ${max} 个策略，请减少勾选`;
  return null;
}

/** 校验上下文刷新轮数：须为区间内整数；返回错误文案或 null。 */
export function validateContextRounds(
  rounds: number,
  min: number = AI_TRADE_MIN_CONTEXT_ROUNDS,
  max: number = AI_TRADE_MAX_CONTEXT_ROUNDS,
): string | null {
  if (!Number.isInteger(rounds) || rounds < min || rounds > max) {
    return `上下文刷新轮数需为 ${min}~${max} 的整数`;
  }
  return null;
}

const ACTION_LABELS: Record<AiTradeAction, string> = {
  open_long: "开多",
  open_short: "开空",
  close: "平仓",
  hold: "观望",
};

/** AI 决策动作中文化（open_long→「开多」…）。 */
export function describeAiAction(action: string): string {
  return ACTION_LABELS[action as AiTradeAction] ?? action;
}

const STANCE_LABELS: Record<string, string> = { long: "多", short: "空", flat: "空仓" };

/** 影子立场中文化（long→「多」，short→「空」，flat→「空仓」）。 */
export function describeStance(stance: string): string {
  return STANCE_LABELS[stance] ?? stance;
}

const STOP_REASON_LABELS: Record<AiTradeStopReason, string> = {
  user_stop: "手动停止",
  session_stop_loss: "触发会话止损",
  session_take_profit: "触发会话止盈",
  shutdown: "服务重启",
  stale_feed: "行情中断保护",
  start_failed: "启动失败",
};

/** 停止原因中文化（缺省回「已停止」）。 */
export function describeStopReason(reason: string | null | undefined): string {
  if (!reason) return "已停止";
  return STOP_REASON_LABELS[reason as AiTradeStopReason] ?? "已停止";
}

// 市场体制真实枚举 → 中文（R-P1-2：映射 engine/regime 真值，缺省回原值不臆造）。
const REGIME_PRIMARY_LABELS: Record<string, string> = {
  trend_up: "上升趋势",
  trend_down: "下降趋势",
  range: "震荡",
};
const REGIME_VOLATILITY_LABELS: Record<string, string> = {
  high_volatility: "高波动",
  low_volatility: "低波动",
  normal: "常波动",
};
const REGIME_EVENT_LABELS: Record<string, string> = {
  breakout: "突破",
  crash: "急跌",
};

/**
 * 市场体制上下文中文化（E1）：`趋势结构 · 波动 · 事件`（如「上升趋势 · 高波动 · 突破」）。
 * 事件为 none 时省略；任一未知枚举缺省回原值（不臆造，R-P1-2）；空上下文返回空串。
 */
export function describeRegime(mc: MarketContext | null | undefined): string {
  if (!mc) return "";
  const parts = [
    REGIME_PRIMARY_LABELS[mc.primary] ?? mc.primary,
    REGIME_VOLATILITY_LABELS[mc.volatility] ?? mc.volatility,
  ];
  if (mc.event && mc.event !== "none") {
    parts.push(REGIME_EVENT_LABELS[mc.event] ?? mc.event);
  }
  return parts.filter(Boolean).join(" · ");
}

/**
 * 策略共识度中文化（E2）：`<N>多/<M>空/<K>空仓 · 一致度 <p>%`（如「3多/1空/1空仓 · 一致度 60%」）。
 * 空共识返回空串。
 */
export function describeConsensus(consensus: Consensus | null | undefined): string {
  if (!consensus) return "";
  const pct = Math.round((consensus.agreement ?? 0) * 100);
  return `${consensus.longVotes}多/${consensus.shortVotes}空/${consensus.flatVotes}空仓 · 一致度 ${pct}%`;
}

export type AiExecTone = "long" | "short" | "muted";
export type AiTraceTone = AiExecTone | "accent";

export interface AiTraceStateChip {
  label: string; // 研判中… / 已执行 / 已决策 / 降级观望 / 已跳过 / 异常
  tone: AiTraceTone;
}

/**
 * 轮次状态 chip（轨迹轮头右上角）：把已装配轮次的 state + 执行/降级归一为一枚状态徽标。
 * 优先级：异常 > 已跳过 > 研判中（deciding）> 降级观望（degradeReason）> 已执行（traded，按方向着色）> 已决策。
 */
export function describeTraceState(trace: {
  state: string;
  toolCall?: { action?: string } | null;
  executed?: AiTradeDecision["executed"];
  degradeReason?: string | null;
}): AiTraceStateChip {
  if (trace.state === "error") return { label: "异常", tone: "short" };
  if (trace.state === "skipped") return { label: "已跳过", tone: "muted" };
  if (trace.state === "deciding") return { label: "研判中…", tone: "accent" };
  if (trace.degradeReason) return { label: "降级观望", tone: "short" };
  if (trace.executed?.traded) {
    const action = trace.toolCall?.action;
    const tone: AiTraceTone =
      action === "open_long" ? "long" : action === "open_short" ? "short" : "muted";
    return { label: "已执行", tone };
  }
  return { label: "已决策", tone: "muted" };
}

export interface AiExecutedView {
  label: string;    // 已成交 / 已跳过 / 已降级观望（reason） / 观望
  tone: AiExecTone; // 净立场着色（long/short/muted）
}

/**
 * 执行结果态中文化（E3）：把本轮 `executed`/`degradeReason` 归一为四态状态点（+着色）。
 * 优先级：已跳过（decision_in_flight）> 已降级观望（含 reason）> 已成交（按方向着色）> 观望。
 */
export function describeExecuted(
  action: string,
  executed: AiTradeDecision["executed"],
  degradeReason?: string | null,
): AiExecutedView {
  if (executed?.skipped) return { label: "已跳过", tone: "muted" };
  if (degradeReason) return { label: `已降级观望（${degradeReason}）`, tone: "short" };
  if (executed?.traded) {
    const tone: AiExecTone =
      action === "open_long" ? "long" : action === "open_short" ? "short" : "muted";
    return { label: "已成交", tone };
  }
  return { label: "观望", tone: "muted" };
}
