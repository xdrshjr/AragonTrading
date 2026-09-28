/** 对话交易公开 DTO；数值沿用服务端十进制字符串与 UTC 秒。 */
export type TradingEnvironment = "demo" | "live";
export interface TradingAccount {
  id: number;
  label: string;
  isDefault: boolean;
  configured: boolean;
}
export interface TradingCapabilities {
  enabled: boolean;
  executionEnabled: boolean;
  recoveryReady: boolean;
  environment: TradingEnvironment;
  supportedActions: string[];
  accounts: TradingAccount[];
}
export interface TradingBinding {
  conversationId: string;
  accountId: number | null;
  environment: TradingEnvironment;
  accountMode: "sim" | "live";
  version: number;
  updatedTs: number;
}
export interface BindingInput {
  accountId: number | null;
  accountMode: "sim" | "live";
  expectedVersion: number;
}
export type IntentStatus = "prepared" | "submitting" | "unknown"
  | "accepted" | "partially_filled" | "filled" | "canceled"
  | "rejected" | "expired" | "dismissed";
export interface TradePreview {
  instId: string;
  direction: string | null;
  posSide: string | null;
  side: string | null;
  tdMode: string | null;
  ordType: string | null;
  sizeContracts: string | null;
  price: string | null;
  referencePrice: string | null;
  notionalEstimateUsdt: string | null;
  marginEstimateUsdt: string | null;
  leverageObserved: string | null;
  tpTriggerPx: string | null;
  slTriggerPx: string | null;
  warnings: string[];
  observedTs: number;
  reduceOnly: boolean | null;
  positionObservedSz: string | null;
  positionIdentity: Record<string, unknown> | null;
  accountMode: string | null;
  acctLv: string | null;
  posMode: string | null;
  instrumentConstraints: Record<string, string | null> | null;
  maxOpenContracts: string | null;
  protectionStatus: string | null;
  targetOrdId?: string | null;
  kind?: string | null;
}
export interface TradeResult {
  ordId: string | null;
  clOrdId: string | null;
  exchangeState: string | null;
  accFillSz: string | null;
  avgPx: string | null;
  lastCheckedTs: number | null;
  protectionStatus?: string | null;
}
export interface TradeIntentView {
  schemaVersion: 1;
  id: string;
  conversationId: string;
  version: number;
  action: "open" | "close" | "cancel";
  status: IntentStatus;
  account: { id: number; label: string; environment: TradingEnvironment };
  bindingVersion: number;
  createdTs: number;
  expiresTs: number;
  previewHash: string;
  preview: TradePreview;
  result: TradeResult | null;
  error: { code: string; message: string } | null;
  canExecute: boolean;
}
export interface IntentPage {
  items: TradeIntentView[];
  nextCursor: string | null;
}
export interface AccountSnapshot {
  schemaVersion: 1;
  source: string;
  accountId: number | null;
  accountLabel: string;
  environment: TradingEnvironment;
  observedStartTs?: number;
  asOfTs: number;
  coverage: {
    status: "complete" | "partial";
    includedKinds?: string[];
    excludedKinds?: string[];
  };
  warnings: string[];
  data: Record<string, unknown> | Record<string, unknown>[];
  total?: number | null;
  hasMore?: boolean;
  observedCount?: number;
  returnedCount?: number;
  truncated?: boolean;
}
export interface AutoTradeContext extends AccountSnapshot {
  contextUnavailableReason?: string | null;
}
export type TradingPayload =
  | { type: "account_snapshot"; snapshot: AccountSnapshot }
  | { type: "trade_intent"; intent: TradeIntentView }
  | { type: "auto_trade_context"; context: AutoTradeContext };

export const INTENT_LABELS: Record<IntentStatus, string> = {
  prepared: "待提交", submitting: "提交中", unknown: "结果待核实",
  accepted: "委托已受理", partially_filled: "部分成交", filled: "已成交",
  canceled: "已撤销", rejected: "未执行/已拒绝", expired: "预览已过期",
  dismissed: "已放弃",
};

/** 已提交未结状态独立于预览，不允许据此再次发送。 */
export function isUnsettled(status: IntentStatus): boolean {
  return ["submitting", "unknown", "accepted", "partially_filled"]
    .includes(status);
}

/** 交易所环境与本地模拟是两个不同概念。 */
export function environmentLabel(environment: TradingEnvironment): string {
  return environment === "demo" ? "OKX 模拟盘" : "OKX 实盘";
}

/** 服务端时间为秒，展示时转换，不把未知值当作当前时间。 */
export function tradingTime(timestamp: number | null | undefined): string {
  return timestamp == null ? "尚未核对"
    : new Date(timestamp * 1000).toLocaleString("zh-CN");
}
