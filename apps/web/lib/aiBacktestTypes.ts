// AI 自动交易 · 回测模式 wire 类型（camelCase，与后端 schemas.py 的 AiBacktest* 同构）。
// 决策/摘要复用 aiTradeTypes 的 AiTradeDecision；aiBacktestJobId 为瞬态（store 不入 partialize，GET 重发现）。
import type { AiTradeDecision } from "./aiTradeTypes";
import type { EquityPoint, Metrics, Trade } from "./types";

export type AiBacktestPhase = "loading" | "priming" | "running" | "finalizing";
export type AiBacktestJobState = "running" | "completed" | "cancelled" | "failed";

// 启动请求（金额/杠杆/OCO 可留空→服务端保守默认，§4.1）。
export interface AiBacktestStartReq {
  instId: string;
  bar: string;
  strategyIds: number[];
  startTs: number;
  endTs: number;
  decisionEveryBars?: number | null;
  initialCapital?: number | null;
  leverage?: number | null;
  stopLossPct?: number | null;
  takeProfitPct?: number | null;
  takeProfitUsd?: number | null;
  stopLossUsd?: number | null;
  contextRefreshRounds: number;
  minConsensus?: number | null;
}

export interface AiBacktestStartResult {
  jobId: string;
  instId: string;
  bar: string;
  strategyIds: number[];
  startTs: number;
  endTs: number;
  decisionEveryBars: number;
  plannedRounds: number;
  initialCapital: number;
  leverage: number;
}

// AI 专属统计（运行器聚合，落 metrics.aiStats）。
export interface AiBacktestAiStats {
  decisionRounds: number;
  actionCounts: Record<string, number>; // {open_long,open_short,close,hold}
  holdRate: number;
  avgConfidence: number;
  degradedRounds: number;
  timeoutRounds: number;
  summaryCount: number;
  finalSummary?: string | null;
  decisionEveryBars: number;
  plannedRounds: number;
  reachedRoundCap: boolean;
}

// 进度事件（驱动进度头 / 滚动权益迷你图）。
export interface AiBacktestProgress {
  barIndex: number;
  totalBars: number;
  ts: number;
  lastPx: number;
  pct: number;
  decisionRound: number;
  plannedRounds: number;
  equity: number;
  positionSide: string;
  sessionPnlUsd: number;
}

export interface AiBacktestSummary {
  round: number;
  ts: number;
  summary: string;
}

// 完成结果（result 事件全量 + 落库摘要版共用形）。
export interface AiBacktestResult {
  runId: number | null;
  metrics: Metrics;
  aiStats: AiBacktestAiStats;
  equity: EquityPoint[];
  trades: Trade[];
  decisionsDigest: AiTradeDecision[];
  summaries: AiBacktestSummary[];
  startTs: number;
  endTs: number;
  barsProcessed: number;
  notes: string[];
}

// 作业只读状态（GET /ai-trade/backtest/{jobId} 与 WS snapshot 的 job 字段）。
export interface AiBacktestStatus {
  jobId: string;
  state: AiBacktestJobState;
  phase: AiBacktestPhase;
  progress: number;
  barIndex: number;
  totalBars: number;
  decisionRound: number;
  plannedRounds: number;
  instId: string;
  bar: string;
  strategyIds: number[];
  startTs: number;
  endTs: number;
  startedTs: number;
  runId: number | null;
  result: AiBacktestResult | null;
}

// WS snapshot 事件负载（连接即回放缓冲近端态）。
export interface AiBacktestSnapshot {
  job: AiBacktestStatus;
  decisions: AiTradeDecision[];
  summaries: AiBacktestSummary[];
  equity: { ts: number; equity: number }[];
}

// 持久化偏好（进 partialize）：记忆上次的决策间隔与刷新轮数。
export interface AiBacktestConfig {
  decisionEveryBars: number;
  contextRefreshRounds: number;
}
