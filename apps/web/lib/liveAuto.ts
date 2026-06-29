// 实时自动交易纯件（live-auto-trading）：守门判定、预算派生 / 夹紧、金额输入校验、停止原因文案。
// 全部为无副作用纯函数，供 AutoTradeToggle / AutoTradeConfirmModal / AutoTradeStatusBar 复用并单测。
import type { AutoStopReason } from "./liveAutoTypes";
import type { AccountMode } from "./types";

// 预算默认 / 硬上限占可用余额比例（与后端 settings.live_auto_*_capital_frac 一致）。
export const LIVE_AUTO_DEFAULT_CAPITAL_FRAC = 0.01;
export const LIVE_AUTO_MAX_CAPITAL_FRAC = 0.05;
// 生效杠杆默认值（与后端 settings.live_default_leverage 一致，弹窗摘要展示，H8）。
export const LIVE_AUTO_DEFAULT_LEVERAGE = 3;
// 单笔名义价值上限（与后端 settings.live_max_order_notional_usd 默认一致，弹窗摘要展示，H8）。
export const LIVE_MAX_ORDER_NOTIONAL_USD = 1000;

export interface AutoTradeGateInput {
  accountMode: AccountMode;
  hasReadyDefault: boolean;
  liveAutoEnabled: boolean;
  // 用户在策略面板里勾选（复选框，写 paperSelectionIds）的策略数量。自动交易为单会话独占
  // （后端 cap=1），故仅「恰选 1 个」放行；0 / >1 各给明确守门文案，杜绝默默回落默认策略。
  selectedCount: number;
}

/** 自动交易开关守门：返回禁用原因（中文），全部满足返回 null（可启用）。 */
export function resolveAutoTradeGate(input: AutoTradeGateInput): string | null {
  if (input.accountMode !== "live") return "请先切换到实盘账户模式";
  if (!input.hasReadyDefault) return "请先在设置中配置默认实盘账户";
  if (!input.liveAutoEnabled) return "实时自动交易未启用（需后端开启 LIVE_AUTO_TRADING_ENABLED）";
  if (input.selectedCount === 0) return "请先选择一个交易策略";
  if (input.selectedCount > 1) return "自动交易仅支持单个策略，请只勾选一个";
  return null;
}

/** 留空预算时的保守默认：可用 USDT × 默认比例（1%）。 */
export function deriveDefaultBudget(availUsdt: number): number {
  return Math.max(0, availUsdt) * LIVE_AUTO_DEFAULT_CAPITAL_FRAC;
}

/** 预算硬上限夹紧（R4 的前端预览口径，最终仍以服务端回执 budgetUsd 为准）。 */
export function clampBudgetToCap(budget: number, availUsdt: number): number {
  const cap = Math.max(0, availUsdt) * LIVE_AUTO_MAX_CAPITAL_FRAC;
  return cap > 0 ? Math.min(budget, cap) : budget;
}

/** 解析可选金额输入：空串→null（用默认）；非正 / 非数→错误提示。 */
export function parseOptionalUsd(raw: string): { value: number | null; error: string | null } {
  const text = raw.trim();
  if (text === "") return { value: null, error: null };
  const n = Number(text);
  if (!Number.isFinite(n) || n <= 0) {
    return { value: null, error: "请输入大于 0 的金额，或留空使用默认" };
  }
  return { value: n, error: null };
}

/** 解析可选杠杆输入：空串→null（用默认）；区间 (0,125]。 */
export function parseOptionalLeverage(raw: string): { value: number | null; error: string | null } {
  const text = raw.trim();
  if (text === "") return { value: null, error: null };
  const n = Number(text);
  if (!Number.isFinite(n) || n <= 0 || n > 125) {
    return { value: null, error: "杠杆需在 1~125 之间，或留空使用默认" };
  }
  return { value: n, error: null };
}

// 自动会话停止原因 → 中文文案（hardening H7/R-6）；未知值回落「已停止」，杜绝枚举漂移导致空白。
const STOP_REASON_LABELS: Record<AutoStopReason, string> = {
  user_stop: "手动停止",
  session_stop_loss: "触发会话止损",
  session_take_profit: "触发会话止盈",
  shutdown: "服务重启",
  stale_feed: "行情中断保护",
  start_failed: "启动失败",
};

/** 终止横幅文案：停止原因 → 中文（H7）；空 / 未知一律回落「已停止」。 */
export function describeStopReason(reason: string | null | undefined): string {
  if (!reason) return "已停止";
  return STOP_REASON_LABELS[reason as AutoStopReason] ?? "已停止";
}
