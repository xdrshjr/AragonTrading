// 当前事实的 v2 只读契约，缺失与零值有不同语义。
import type { AiTradeOpenFunnel } from "./aiTradeTypes";

export type IntegrityCoverage =
  | "complete" | "partial" | "legacy_partial" | "unavailable";

export interface OpenAttemptReceipt {
  attemptId: string | null;
  status: "not_submitted" | "accepted" | "rejected" | "unknown";
  reason: string;
  clOrdId: string | null;
  ordId: string | null;
  instId: string;
  submittedNew: boolean;
  submissionPhase: "not_sent" | "sent";
  observedAtTs: number;
}

export interface MetricCoverage {
  scope: string;
  basis: string;
  sampleCount: number;
  excludedCount: number;
  reason?: string | null;
}

export interface EconomicsSnapshot {
  scope: "run" | "group";
  basis: "local_estimate" | "exchange_settled" | "mixed" | "unknown";
  accountingVersion?: number;
  asOfTs?: number | null;
  roundTripCount?: number;
  completeFeeTripCount?: number;
  unknownTripCount?: number;
  winRate?: number | null;
  profitFactor?: number | null;
  avgPnlUsd?: number | null;
  grossPnlUsd?: number | null;
  feeUsd?: number | null;
  sessionFeeUsd?: number | null;
  tradingNetUsd?: number | null;
  fundingCostUsd?: number | null;
  fundingBasis?: "accrued" | "exchange_settled" | "unknown";
  netAfterFundingUsd?: number | null;
  openFundingAccruedUsd?: number | null;
  exchangeTradingNetUsd?: number | null;
  exchangeFundingCostUsd?: number | null;
  exchangeNetAfterFundingUsd?: number | null;
  exchangeRealizedPnlUsd?: number | null;
  liquidationCostUsd?: number | null;
  settlementAdjustmentUsd?: number | null;
  coverage?: IntegrityCoverage;
  metricCoverage?: Record<string, MetricCoverage>;
  comparisonCoverage?: Record<string, unknown> | null;
}

export interface EntryQuotaSnapshot {
  schemaVersion?: number;
  used?: number;
  reserved?: number;
  remaining?: number;
  maxPerSession?: number;
  legacyAssumedUsed?: number;
  coverage?: IntegrityCoverage | "legacy_unknown";
}

export interface IntegritySnapshot {
  schemaVersion: number;
  asOfTs?: number | null;
  runStartedTs?: number | null;
  runtimeEpochId?: string | null;
  runtimeEpochStartedTs?: number | null;
  sampleThroughRound?: number;
  diagnosis?: { code: string; message: string; details?: unknown };
  funnel?: AiTradeOpenFunnel | null;
  entryQuota?: EntryQuotaSnapshot | null;
  pendingOpen?: {
    attemptId?: string;
    status?: string;
    phase?: string;
    ack?: string;
    clOrdId?: string;
    ordId?: string | null;
    createdTs?: number;
    updatedTs?: number;
  } | null;
  economics?: EconomicsSnapshot | null;
  memory?: {
    factsVersion?: number;
    legacyNarrative?: boolean;
    summaryAsOfTs?: number | null;
  } | null;
}

/** 未知服务端版本不被解释为 v2，更不能补成完整零值。 */
export function readIntegrity(value: unknown): IntegritySnapshot | null {
  if (!value || typeof value !== "object") return null;
  const wire = value as IntegritySnapshot;
  return wire.schemaVersion === 2 ? wire : null;
}

/** 唯一主原因由服务端排序，客户端不解析中文原因或猜测偏好。 */
export function describeIntegrity(value: unknown): string {
  if (value && typeof value === "object"
    && typeof (value as IntegritySnapshot).schemaVersion === "number"
    && (value as IntegritySnapshot).schemaVersion > 2) {
    return "此版本详情暂不可读";
  }
  return readIntegrity(value)?.diagnosis?.message
    || "历史分类不完整，当前诊断暂不可得";
}

/** 读取服务端提供的说明，不从中文文案推断状态或重算门禁。 */
export function integrityDetailText(value: unknown): string | null {
  if (typeof value === "string") return value || null;
  if (!value || typeof value !== "object") return null;
  const reason = (value as { reason?: unknown }).reason;
  return typeof reason === "string" && reason ? reason : null;
}

export function coverageLabel(value?: string): string {
  const labels: Record<string, string> = {
    complete: "完整覆盖", partial: "部分覆盖",
    legacy_partial: "旧数据部分覆盖", unavailable: "暂不可得",
    legacy_unknown: "旧额度不可确认",
  };
  return labels[value ?? ""] ?? "覆盖范围暂不可得";
}

export function economicsSourceLabel(value?: string): string {
  const labels: Record<string, string> = {
    local_estimate: "本地估算", exchange_settled: "官方结算",
    mixed: "本地估算，附官方对账", unknown: "来源暂不可得",
  };
  return labels[value ?? ""] ?? "来源暂不可得";
}

export function executionReasonLabel(code: string): string {
  const labels: Record<string, string> = {
    pre_submit_error: "提交前检查失败", canceled_before_send: "发送前已取消",
    below_min_size: "低于最小张数", existing_order_kept: "保留既有挂单",
    position_not_flat: "旧仓尚未平清", same_side_position: "已有同向持仓",
    exchange_rejected: "交易所拒绝", insufficient_balance: "可用保证金不足",
    attempt_persist_failed: "开仓状态保存失败", acceptance_unknown: "接收待核实",
    pre_submit_context_changed: "执行前事实已变化", facts_unavailable: "事实不完整",
    quota_exhausted: "额度已用尽", session_not_running: "会话已暂停",
    other: "其他执行限制",
  };
  return labels[code] ?? "执行原因暂不可得";
}
