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

export interface FeeAdjustment {
  kind: string;
  exchangeTripFeeUsd?: number | null;
  bookedLocalFeeUsd?: number | null;
  residualFeeUsd?: number | null;
  feeBasis?: string | null;
}

export interface SettlementValidationSnapshot {
  schemaVersion: number;
  status?: string | null;
  reason?: string | null;
  detailReason?: string | null;
  message?: string | null;
  asOfTs?: number | null;
  epochKey?: string | null;
  settlementKey?: string | null;
  identityBasis?: string | null;
  amountCoverage?: string | null;
  queryCoverage?: string | null;
  priceBasis?: string | null;
  feeAdjustment?: FeeAdjustment | null;
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
  settlementValidation?: SettlementValidationSnapshot | null;
  memory?: {
    factsVersion?: number;
    evidenceVersion?: number | null;
    summaryEvidenceVersion?: number | null;
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
    pre_submit_context_changed: "执行前成本明显上升或持仓已变化",
    pre_submit_price_drift: "价格已不利移动", facts_unavailable: "事实不完整",
    quota_exhausted: "额度已用尽", session_not_running: "会话已暂停",
    other: "其他执行限制",
  };
  return labels[code] ?? "执行原因暂不可得";
}

/** 原因枚举由服务端裁决；未知枚举仅使用安全说明或未知提示。 */
export function settlementReasonLabel(
  code?: string | null, message?: string | null,
): string {
  if (message) return message;
  const labels: Record<string, string> = {
    identity_unavailable: "持仓身份暂不可得",
    ownership_conflict: "持仓归属冲突", unsupported_product: "产品暂不支持",
    currency_unknown: "结算币种未知", not_closed: "持仓尚未全平",
    identity_mismatch: "持仓身份不符", time_mismatch: "结算时间不符",
    quantity_mismatch: "结算数量不符", invalid_price: "结算价格无效",
    missing_amount: "结算金额缺失", identity_failed: "结算金额恒等式不成立",
    conflicting_duplicate: "重复结算记录冲突", ambiguous: "结算归属存在歧义",
    not_found: "未找到结算记录", already_claimed: "结算已记账",
    fetch_failed: "结算查询失败", deadline_exceeded: "结算等待已截止",
    invalid_amount: "结算金额无效", unsupported_currency: "结算币种暂不支持",
    increment_invalid: "结算增量无效",
  };
  return labels[code ?? ""] ?? "原因暂不可得";
}

/** 结算协议独立版本化，不能由外层 v2 推断内层可以解释。 */
export function readSettlementValidation(
  value?: SettlementValidationSnapshot | null,
): SettlementValidationSnapshot | null {
  return value?.schemaVersion === 1 ? value : null;
}

/** 保留本地估算与已验证结算的边界，不将缺失结果认证为匹配。 */
export function describeSettlement(
  value?: SettlementValidationSnapshot | null,
): string {
  if (!value) return "旧记录未提供校验结果";
  const settlement = readSettlementValidation(value);
  if (!settlement) return "此版本结算详情暂不可读";
  if (settlement.status === "matched") return "本次持仓匹配通过";
  if (settlement.status === "already_claimed") return "结算已记账";
  const reason = settlementReasonLabel(
    settlement.detailReason ?? settlement.reason, settlement.message,
  );
  const labels: Record<string, string> = {
    local_estimate: "本地估算", pending: "结算待确认",
    unavailable: "结算暂不可得",
  };
  return `${labels[settlement.status ?? ""] ?? "结算状态暂不可得"}：${reason}`;
}

/** 只解释来源枚举，不从成交金额推断实测来源。 */
export function settlementBasisLabel(value?: string | null): string {
  const labels: Record<string, string> = {
    observed_position: "交易所持仓观察", paused_position: "暂停持仓快照",
    exchange_position_average: "交易所持仓均价",
    exchange_aggregate_residual: "累计成交差额", local_mark: "本地标记价",
    single_page: "单页查询", measured: "实测", estimated: "含本地估算",
  };
  return labels[value ?? ""] ?? "来源暂不可得";
}

/** 运行能力与摘要来源分别显示，旧摘要不因当前运行版本而升级。 */
export function evidenceVersionLabel(value?: number | null): string {
  if (value == null) return "旧记录未提供证据版本";
  if (value === 2 || value === 3) return `历史证据 v${value}`;
  return value === 4 ? "v4" : "此版本证据暂不可读";
}
