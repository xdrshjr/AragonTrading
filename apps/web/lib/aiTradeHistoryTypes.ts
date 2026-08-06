// AI 自动交易统计历史 wire 类型（ai-trade-stats-dashboard §3.8）：与后端 schemas.AiTradeHistory* 逐字对齐。
// 皆为组件 / lib 局部类型，不入 store（历史走 TanStack Query，不持久化）。时间戳全为秒（§6.2 / P2-8）。
import type { FeeRatesWire, ReconcileWire } from "./aiTradeCost";
import type { Trade } from "./types";

// 历史会话列表单行 KPI（后端逐成员配对聚合；口径同 autoTradePnl.summarizeRoundTrips）。
export interface AiTradeHistoryEntry {
  groupId: string;
  strategyName: string;
  bar: string;
  instIds: string[];
  startedTs: number;
  endedTs: number;
  status: string;
  totalBudgetUsd: number;
  realizedPnlUsd: number;
  returnPct: number;
  tradeCount: number;
  winRate: number;
  bestUsd: number;
  worstUsd: number;
  // 成本三键（cost-truth §5.6）：realizedPnlUsd 已是**净额**，与 feeUsd / grossPnlUsd 并列才能
  // 逐笔解释「数字为什么下移」。老后端不下发 ⇒ 读点 `?? 0` 兜底，故全部可选。
  feeUsd?: number;
  grossPnlUsd?: number;
  costBasisMixed?: boolean;
}

export interface AiTradeHistoryListOut {
  items: AiTradeHistoryEntry[];
  total: number;
  limit: number;
  offset: number;
}

export interface AiTradeHistorySummary {
  realizedPnlUsd: number;
  returnPct: number;
  tradeCount: number;
  winRate: number;
  bestUsd: number;
  worstUsd: number;
  // 利润捕获率五键（ai-trade-profit-protection-adaptive §2.4.3）：与前端 summarizeProfitCapture
  // 公式逐字对应。**历史页一律取后端这份，不在前端重算**（老库缺列时后端已按 getattr 降级为 0）。
  // 老后端不下发这五键 ⇒ 读点一律 `?? 0` 兜底，故全部可选。
  peakPnlUsd?: number;
  capturedPnlUsd?: number;
  captureRatio?: number;
  capturePairs?: number;
  noPeakCount?: number;
  // cost-truth：捕获率五键**分子分母同为毛额**（不随净额总闸变化）；手续费口径看这三键。
  feeUsd?: number;
  grossPnlUsd?: number;
  costBasisMixed?: boolean;
}

// metrics_json 解析（全可选，缺失即优雅降级——崩溃 / 对账会话没有富化字段）。
export interface AiTradeRunMetrics {
  totalReturn?: number;
  tradeCount?: number;
  avgPnl?: number;
  roundsCompleted?: number;
  finalSummary?: string | null;
  reconciled?: boolean;
  equityDigest?: { ts: number; equity: number; drawdown: number }[];
  decisionsDigest?: {
    ts: number;
    round: number;
    action: string;
    executed?: boolean;
    confidence?: number | null;
    reason?: string | null;
    reflectionVerdict?: string | null; // R2-07：后端 _digest_decisions 已下发（缺省 null）
  }[];
  aiStats?: {
    decisionRounds: number;
    actionCounts: Record<string, number>;
    holdRate: number;
    avgConfidence: number;
    degradedRounds: number;
    // hold-inertia-observability O2（全可选，老记录缺键即优雅降级——aggregate 全缺返 null 隐藏整行）：
    // 反思四计数为近 50 轮保留样本口径；wakeSkips / maxHoldStreak 为会话级全程直取。
    reflectionRuns?: number;
    reflectionConfirms?: number;
    reflectionAborts?: number;
    reflectionFailClosed?: number;
    wakeSkips?: number;
    maxHoldStreak?: number;
    // 唤醒调度闭环观测（wake-scheduler-closed-loop §4.4）：加法写既有 JSON 列，老记录缺键 ⇒
    // 整段隐藏（绝不显示一排误导性的 0）。`drops` 与 status.wakeDrops **同读**一个后端计数。
    wakeScheduler?: {
      drops?: number;
      catchups?: number;
      catchupCeilingHits?: number;
      positionWatchSkips?: number;
      signalFlipWakes?: number;
      previewTimeouts?: number;
      previewErrors?: number;
      previewDisabled?: boolean;
    };
    // 成本真值化（cost-truth §5.4，**顶层**加法键）：老记录缺键 ⇒ 整段隐藏（绝不显示误导性的 0）。
    feeRates?: FeeRatesWire;
    reconcile?: ReconcileWire;
  };
}

export interface AiTradeHistoryMember {
  runId: number;
  instId: string;
  leverage: number;
  budgetUsd: number;
  allocationPct: number;
  strategyNames: string[];
  status: string;
  costBasis?: "net" | "gross";   // 该成员 run 的盈亏口径（老后端不下发 ⇒ undefined）
  metrics: AiTradeRunMetrics;
  trades: Trade[];
}

export interface AiTradeHistoryDetailOut {
  groupId: string;
  strategyName: string;
  bar: string;
  startedTs: number;
  endedTs: number;
  status: string;
  totalBudgetUsd: number;
  // 口径是否为**推定**（cost-truth P0-3）：老 run 缺 costBasis 时按运维配置推定，UI 必须把
  // 「推定」与「已知」分开显示——两件事不可合并成一个徽标。
  costBasisAssumed?: boolean;
  members: AiTradeHistoryMember[];
  summary: AiTradeHistorySummary;
}
