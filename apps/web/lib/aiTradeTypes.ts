// AI 自动交易 wire 类型（camelCase，与后端 schemas.py 的 AiTrade* 同构）。
// 运行期句柄（sessionId 等）为瞬态（store 不入 partialize）；aiTradeConfig 为持久化偏好。
import type { PaperPositionEvent, Trade } from "./types";

export type AiTradeAction = "open_long" | "open_short" | "close" | "hold";
export type AiStance = "long" | "short" | "flat";
export type AiLatestSignal = "open_long" | "open_short" | "close" | "none";

// 单策略此刻喂给 AI 的立场视图（P0-1：stance=影子持久立场，latestSignal=本根边沿）。
export interface StrategySignalView {
  strategyId: number;
  name: string;
  stance: AiStance;
  latestSignal: AiLatestSignal;
  strength: number;
  reason: string;
}

// 市场体制上下文（E1）：engine/regime.classify_regime 的紧凑视图（取值∈真实 regime 枚举）。
export interface MarketContext {
  primary: string;      // trend_up | trend_down | range
  volatility: string;   // high_volatility | low_volatility | normal
  event: string;        // crash | breakout | none
  confidence: number;
  labels: string[];
}

// 策略共识度（E2）：信号板逐策略持久立场聚合出的多/空/空仓票 + 净立场 + 一致度。
export interface Consensus {
  longVotes: number;
  shortVotes: number;
  flatVotes: number;
  total: number;
  netStance: AiStance;
  agreement: number;    // max(long,short)/total ∈ [0,1]
}

// 市场证据摘要（R1，ai-trade-reasoning-chain）：与后端 market_digest.build_market_digest 产物 wire 镜像。
// 单技术因子读数：value 可为 null（数据不足/除零），label 为定性枚举（前端另做中文化）。
export interface FactorReading {
  value: number | null;
  label: string;
}

// 价格走势证据：趋势 / 动量 / 均线结构 / 摆动结构 / 区间位置（分母 0 时对应字段为 null）。
export interface PriceAction {
  changePct: number;
  trend: string;               // up | down | flat
  emaFast: number;
  emaSlow: number;
  emaRelation: string;         // fast_above | fast_below
  emaSlopePct: number | null;
  rangePos: number | null;     // ∈[0,1]
  swingHigh: number;
  swingLow: number;
  consecUp: number;
  consecDown: number;
  lastBar: { o: number; h: number; l: number; c: number; bodyPct: number | null };
}

// 成交量证据：量能水平 / 量比 / zscore / 放缩趋势 / 异常放量。
export interface VolumeContext {
  last: number;
  sma: number;
  ratio: number | null;
  zscore: number | null;
  trend: string;               // rising | falling | flat
  spike: boolean;
}

// 一组固定经典技术因子读数（RSI/MACD 柱/布林%B/ATR%/动量 ROC）。
export interface FactorReadings {
  rsi14: FactorReading;
  macdHist: FactorReading;
  bollPctB: FactorReading;
  atrPct: FactorReading;
  rocPct: FactorReading;
}

// 市场证据摘要（R1）：任一子段可为 null（数据不足），整体在禁用/窗口不足时为 null（prompt/前端省略该段）。
export interface MarketDigest {
  priceAction: PriceAction | null;
  volume: VolumeContext | null;
  factors: FactorReadings | null;
}

// 本轮 AI 决策（受约束）：action + 可选仓位占比/置信度 + 理由 + 信号板 + 执行结果 + 体制/共识/降级（E1/E2/E3）。
export interface AiTradeDecision {
  ts: number;
  round: number;
  action: AiTradeAction;
  sizeFraction?: number | null;
  confidence?: number | null;
  reason: string;
  signalBoard: StrategySignalView[];
  executed?: {
    ok?: boolean;
    action?: string;                        // 门禁后最终动作（后端 _execute_decision 回带；与 wire §5.1 一致）
    skipped?: boolean;
    traded?: boolean;
    reason?: string;
    side?: string;
    qty?: number;
    positionAfter?: string;
  } | null;
  marketContext?: MarketContext | null;   // E1（可为 null）
  consensus?: Consensus | null;           // E2
  degradeReason?: string | null;          // E3（low_consensus(k/n) / decision_timeout / … 或 null）
  analysis?: string | null;               // R1：研判思维链有界摘录（≤400 字；完整全文见 agentTraces）
  marketDigest?: MarketDigest | null;     // R1：价格走势/成交量/因子证据摘要（可为 null）
}

// 决策认知轨迹阶段（ai-auto-trading-agent-trace §5.3；与后端 trace.PHASES 镜像）。
export type AiTradeTracePhase =
  | "round_start"
  | "thinking"
  | "analysis"
  | "tool_call_start"
  | "tool_call"
  | "result"
  | "summary"
  | "error"
  | "retry";

// agent_trace 事件 data（各字段按 phase 可选；前端 reducer 按 phase 归约）。
export interface AiTradeTraceEvent {
  phase: AiTradeTracePhase;
  round: number;
  ts: number;
  delta?: string; // thinking / analysis 合并增量
  name?: string; // tool_call_start / tool_call 工具名
  input?: {
    action: AiTradeAction;
    sizeFraction?: number | null;
    confidence?: number | null;
    reason: string;
  }; // tool_call：门禁后最终决策工具入参
  executed?: AiTradeDecision["executed"]; // result（复用既有 executed 形状）
  degradeReason?: string | null; // result
  summary?: string; // summary
  message?: string; // error
  lastPx?: number; // round_start
  position?: unknown | null; // round_start
  signalBoard?: StrategySignalView[]; // round_start
  marketContext?: MarketContext | null; // round_start
  consensus?: Consensus | null; // round_start
  marketDigest?: MarketDigest | null; // round_start（R1：价格走势/成交量/因子证据摘要）
  attempt?: number; // retry：当前重试序号（1-based）
  maxAttempts?: number; // retry：本轮最大重试次数
  reason?: string; // retry：触发本次重试的上一次 degrade 原因
}

// store / snapshot 的已装配单轮轨迹（thinking/analysis 累积；env 环形 ≤20）。
export interface AiTradeRoundTrace {
  round: number;
  ts: number;
  lastPx?: number;
  position?: unknown | null;
  signalBoard: StrategySignalView[];
  marketContext?: MarketContext | null;
  consensus?: Consensus | null;
  marketDigest?: MarketDigest | null; // R1：价格走势/成交量/因子证据摘要（round_start 承载）
  thinking: string; // 累积深度思考（未开启扩展思考时通常为空）
  analysis: string; // 累积自然语言研判分析（流式主可见内容）
  toolCallPending?: boolean;
  toolCall?: AiTradeTraceEvent["input"] | null;
  executed?: AiTradeDecision["executed"];
  degradeReason?: string | null;
  summary?: string | null;
  errorMessage?: string | null;
  retries?: number; // §4.4：本轮已重试次数（retry 阶段累加）
  lastRetryReason?: string | null; // 最近一次触发重试的 degrade 原因
  state: "deciding" | "done" | "skipped" | "error";
}

// GET /ai-trade/status 与 WS snapshot 的会话只读视图（info()）。
export interface AiTradeSessionRow {
  sessionId: string;
  runId: number;
  state: string;
  instId: string;
  bar: string;
  strategyIds: number[];
  strategyNames: string[];
  budgetUsd: number;
  leverage: number;
  takeProfitUsd?: number | null;
  stopLossUsd?: number | null;
  contextRefreshRounds: number;
  roundsCompleted: number;
  roundsSinceSummary: number;
  positionSide: string;
  sessionPnlUsd: number;
  startedTs: number;
  lastDecision?: AiTradeDecision | null;
  contextSummary?: string | null;
  degraded?: boolean | null;
}

// WS status 事件负载（_status_payload / 轻量 status，会话视图的可增量合并子集）。
export interface AiTradeStatusEvent {
  state: string;
  positionSide?: string;
  sessionPnlUsd?: number;
  budgetUsd?: number;
  leverage?: number;
  roundsCompleted?: number;
  roundsSinceSummary?: number;
  contextRefreshRounds?: number;
  degraded?: boolean;
  stopReason?: string | null;
  flattenFailed?: boolean;
  message?: string;
}

export interface AiTradeStartReq {
  instId: string;
  bar: string;
  strategyIds: number[];
  accountId?: number | null;
  capitalUsd?: number | null;
  leverage?: number | null;
  takeProfitUsd?: number | null;
  stopLossUsd?: number | null;
  stopLossPct?: number | null;      // E4：单仓 OCO 止损%（留空→config 默认；越界服务端夹紧）
  takeProfitPct?: number | null;    // E4：单仓 OCO 止盈%
  contextRefreshRounds: number;
  priming?: boolean;
  primingBars?: number;
}

export interface AiTradeStartResult {
  sessionId: string;
  runId: number;
  instId: string;
  bar: string;
  strategyIds: number[];
  strategyNames: string[];
  budgetUsd: number;
  leverage: number;
  contextRefreshRounds: number;
  stopLossPct?: number | null;      // E4：executor 生效止损%（夹紧后回执）
  takeProfitPct?: number | null;    // E4：executor 生效止盈%
}

export interface AiTradeStatusResponse {
  session: AiTradeSessionRow | null;
  aiTradeEnabled: boolean;
}

export interface AiTradeStopResult {
  sessionId: string;
  stopReason: string | null;
  run?: unknown;
  flattened?: { ok?: boolean; closedSz?: number; residual?: { side: string; size: number } | null } | null;
}

// WS snapshot 事件负载（连接即重放当前会话全量态）。
export interface AiTradeSnapshot {
  trades: Trade[];
  equity: unknown[];
  position: PaperPositionEvent | null;
  status: AiTradeStatusEvent;
  decisions: AiTradeDecision[];
  contextSummary: string | null;
  agentTraces?: AiTradeRoundTrace[]; // 最近 ≤20 轮已装配轨迹（重连回放，已防御性冻结）
}

// ---------------- AI 自动交易「分组」（ai-trade-multi-symbol-and-timeframe §6.3，镜像 live-auto 分组）----------------
// 分组成员入参：标的 + 该标的可用仓位百分比（(0,100]，even 模式整组 Σ ≤ 100）。
export interface AiTradeGroupMemberInput {
  instId: string;
  allocationPct: number;
}

// 分组启动请求（camelCase，与后端 AiTradeGroupStartIn 同构；capitalUsd 为分组总预算，按 pct 切分）。
export interface AiTradeGroupStartReq {
  members: AiTradeGroupMemberInput[];
  bar: string;
  strategyIds: number[];
  accountId?: number | null;
  allocationMode: "even" | "max_ratio";
  capitalUsd?: number | null;
  leverage?: number | null;
  takeProfitUsd?: number | null;
  stopLossUsd?: number | null;
  stopLossPct?: number | null;      // E4：单仓 OCO 止损%（各成员同值，服务端夹紧）
  takeProfitPct?: number | null;    // E4：单仓 OCO 止盈%（各成员同值）
  contextRefreshRounds: number;
  priming?: boolean;
  primingBars?: number;
  positionStepPct?: number | null;  // 仓位操作档位步长（fraction；留空→服务端默认；0→连续）
}

// 分组成员行（start 回执 / status 视图共用）：完整会话行 + 分配占比。
export interface AiTradeGroupMemberRow extends AiTradeSessionRow {
  allocationPct: number;
}

// 分组只读视图（GET /ai-trade/status 与 start 回执同构）。
export interface AiTradeGroupRow {
  groupId: string;
  bar: string;
  strategyIds: number[];
  strategyNames: string[];
  totalBudgetUsd: number;       // 夹紧后最终生效的分组总预算（可能 < 用户填值）
  leverage: number;
  contextRefreshRounds: number;
  createdTs: number;
  members: AiTradeGroupMemberRow[];
}

export type AiTradeGroupStartResult = AiTradeGroupRow;

export interface AiTradeGroupStatusResponse {
  group: AiTradeGroupRow | null;
  aiTradeEnabled: boolean;
}

export interface AiTradeGroupStopResult {
  groupId: string;
  stopReason: string | null;
  members: { instId: string; sessionId: string; runId: number; flattened?: unknown }[];
}

export interface AiTradeGroupMemberStopResult {
  groupId: string;
  instId: string;
  flattened?: unknown;
}

// 运行期成员槽（瞬态；镜像 LiveAutoGroupMemberState + AI 专属轨迹 / 决策 / 摘要）。
export interface AiTradeGroupMemberState {
  instId: string;
  sessionId: string;
  allocationPct: number;
  session: AiTradeSessionRow | null;   // 会话只读视图（info + WS status/decision/summary 增量合并）
  trades: Trade[];
  position: PaperPositionEvent | null;
  decisions: AiTradeDecision[];
  traces: AiTradeRoundTrace[];
  summary: string | null;
}

// 分组运行期态（瞬态，不入 partialize，刷新由 GET /ai-trade/status 重发现）。
export interface AiTradeGroupState {
  groupId: string;
  bar: string;
  strategyIds: number[];
  strategyNames: string[];
  totalBudgetUsd: number;
  leverage: number;
  contextRefreshRounds: number;
  state: "running" | "stopped";
  startedTs: number;
  members: Record<string, AiTradeGroupMemberState>;
}

// 持久化偏好（进 partialize）：记忆上次选的策略集合与刷新轮数与仓位操作档位。
export interface AiTradeConfig {
  strategyIds: number[];
  contextRefreshRounds: number;
  // 仓位操作档位步长（fraction）；[R-7] 声明为**可选**——与「老配置无此键 / aiTradeConfig 可为 null」一致，
  // 读点一律 `aiTradeConfig?.positionStepPct ?? AI_TRADE_DEFAULT_POSITION_STEP` 兜底。
  positionStepPct?: number;
}

// 别名（spec §3 类型清单）：会话运行期视图 / 轻量状态负载，供 store 与流式路由语义化引用。
export type AiTradeSessionState = AiTradeSessionRow;
export type AiTradeStatus = AiTradeStatusEvent;

export type AiTradeStopReason =
  | "user_stop"
  | "session_stop_loss"
  | "session_take_profit"
  | "shutdown"
  | "stale_feed"
  | "start_failed";
