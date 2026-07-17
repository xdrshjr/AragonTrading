// AI 自动交易统计历史 wire 类型（ai-trade-stats-dashboard §3.8）：与后端 schemas.AiTradeHistory* 逐字对齐。
// 皆为组件 / lib 局部类型，不入 store（历史走 TanStack Query，不持久化）。时间戳全为秒（§6.2 / P2-8）。
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
  members: AiTradeHistoryMember[];
  summary: AiTradeHistorySummary;
}
