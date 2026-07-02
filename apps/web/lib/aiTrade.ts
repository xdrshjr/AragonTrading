// AI 自动交易纯件（无副作用、易单测）：开关守门文案矩阵、策略/轮数/预算校验、动作与停止原因中文化。
// 预算派生/夹紧复用 live-auto 同款（0.01 默认 / 0.05 上限，与后端 ai_trade_default/max_capital_frac 对齐）。
import { clampBudgetToCap, deriveDefaultBudget } from "./liveAuto";
import type { AccountMode } from "./types";
import type { AiTradeAction, AiTradeStopReason } from "./aiTradeTypes";

export { clampBudgetToCap, deriveDefaultBudget };

// 上下文刷新轮数区间（与后端 ai_trade_min/max_context_rounds 对齐；默认 10）。
export const AI_TRADE_MIN_CONTEXT_ROUNDS = 3;
export const AI_TRADE_MAX_CONTEXT_ROUNDS = 200;
export const AI_TRADE_DEFAULT_CONTEXT_ROUNDS = 10;
export const AI_TRADE_MAX_STRATEGIES = 8;

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
