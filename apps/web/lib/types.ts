export type Bar = "1m" | "5m" | "15m" | "30m" | "1H" | "4H" | "1D";
export type Mode = "backtest" | "compare" | "paper";
export type StrategyListTab = "regular" | "combo" | "factor";
// 原子 / 组合 / 因子组合得分（后端 merge_strategy 透传 kind；评审 C-P1-5）。
export type StrategyKind = "atomic" | "combo" | "factor";
export type AccountMode = "sim" | "live";
export type ChartView = "run-range" | "compare-range" | "live-follow";
export type ChartPane = "price" | "equity";
export type BottomTab = "trades" | "positions";
export type DataSource = "live" | "cache";

export interface Candle {
  ts: number;
  o: number;
  h: number;
  l: number;
  c: number;
  vol: number;
  confirm?: boolean;
}

export interface MarketTrade {
  ts: number;
  price: number;
  qty: number;
  side: "buy" | "sell";
  tradeId?: string | null;
}

export interface OrderBookLevel {
  price: number;
  qty: number;
  orders?: number | null;
}

export interface OrderBookSnapshot {
  ts: number;
  bids: OrderBookLevel[];
  asks: OrderBookLevel[];
}

export interface MarketTicker {
  ts: number;
  last: number;
  open24h: number | null;
  vol24h: number | null;
  volCcy24h: number | null;
}

export type MarketWsEvent =
  | { type: "candle"; data: Candle }
  | { type: "trade"; data: MarketTrade }
  | { type: "ticker"; data: MarketTicker }
  | { type: "book"; data: OrderBookSnapshot }
  | { type: "error"; data: { code: string; message: string } };

export interface LiveMarketState {
  instId: string;
  lastPrice: number | null;
  ticker: MarketTicker | null;
  book: OrderBookSnapshot | null;
  trades: MarketTrade[];
  connected: boolean;
  error: string | null;
  updatedTs: number | null;
}

export interface Instrument {
  instId: string;
  instType: "SPOT" | "SWAP" | "FUTURES";
  base: string;
  quote: string;
  last?: number | null;
  open24h?: number | null;
  vol24h?: number | null;
  volCcy24h?: number | null;
  changePct?: number | null;
}

export type ParamSchema = Record<
  string,
  {
    type: "number" | "int" | "bool" | "enum";
    default: unknown;
    min?: number;
    max?: number;
    step?: number;
    options?: string[];
    label: string;
  }
>;

export interface Strategy {
  id: number;
  templateKey: string;
  name: string;
  description: string;
  // 原子策略 "atomic" | 组合策略 "combo"（后端 merge_strategy 输出；旧前端忽略不影响）。
  kind?: StrategyKind;
  params: Record<string, unknown>;
  paramsSchema: ParamSchema;
  enabled: boolean;
  builtin: boolean;
  unavailable?: boolean;
}

// ---- 策略组合（regime rotation）----
export interface ComboMemberSpec {
  templateKey: string;
  params: Record<string, unknown>;
  name?: string;
}

export interface ComboPolicy {
  rebalanceBars: number;
  lookbackBars: number;
  selectionMetric: "return" | "sharpe" | "calmar";
  minDwellBars: number;
  switchMarginPct: number;
  regimeGating: boolean;
  turnoverBps: number;
}

export interface ComboTimelineSeg {
  startTs: number;
  endTs: number;
  memberKey: string;
  memberName: string;
  regime: string;
  bars: number;
  contribution: number;
}

export interface ComboBreakdownRow {
  regime: string;
  memberKey: string;
  memberName: string;
  bars: number;
  contribution: number;
}

export interface ComboPreviewResult {
  runId: number;
  metrics: Metrics;
  combo: {
    annualizedReturn: number;
    timeline: ComboTimelineSeg[];
    breakdown: ComboBreakdownRow[];
    notes?: string[];
  };
}

// 单策略回测响应（POST /strategies/{id}/backtest）。组合行后端追加 `combo`（_run_saved_combo），
// 原子行 `combo` 为 undefined。详情弹窗绩效区据此渲染年化 / 时间线（R1）。
// （v2·P1-1）全区间覆盖透明度：旧字段不变，新增三项可选，原子回测路径回传（combo 路径为 undefined）。
export interface BacktestResult {
  runId: number;
  metrics: Metrics;
  effectiveCandleCount?: number; // 实际参与回测的已确认 K 线根数
  requestedBars?: number; // (endTs-startTs)/barSec + 1，请求区间应有根数
  truncated?: boolean; // 未覆盖到 startTs（撞 max_bars 上限或 OKX 历史不足）
  combo?: ComboPreviewResult["combo"];
}

// ---- 高级回测：滑动窗口 / Walk-forward（spec advanced-backtest-walk-forward §6.2）----
export type BacktestKind = "simple" | "walkforward";

export interface WalkForwardWindow {
  startTs: number;
  endTs: number;
  metrics: Metrics; // 复用现有 Metrics（totalReturn/winRate/maxDrawdown/sharpe/...）
  tradeCount: number; // == metrics.tradeCount，冗余便于表格直读
}

export interface WalkForwardSummary {
  windowCount: number;
  profitableWindows: number;
  profitableRate: number;
  medianReturn: number;
  avgReturn: number;
  worstReturn: number;
  bestReturn: number;
  avgMaxDrawdown: number; // ≤ 0
  stabilityScore: number; // [0,1]
  uncoveredBars: number; // 末尾被丢弃的尾段根数（v2·P1-2）
  notes: string[]; // 用户可读提示（窗口偏短 / 数据不足一个窗口 / 截断 等，v2·P1-2）
}

export interface WalkForwardResult {
  runId: number;
  summary: WalkForwardSummary;
  windows: WalkForwardWindow[];
}

// 自动交易触发解释（auto-trade-explainability §5.2）：与后端 TriggerExplain wire/DB JSON 同构。
export type TriggerExplainKind =
  | "factor_score"
  | "atomic"
  | "risk_exit"
  | "reverse"
  | "session_stop"
  | "external_close";

export interface TriggerMetric {
  label: string;
  value: string;
  tone?: "long" | "short" | "neutral" | "warn";
  hint?: string;
}

export interface FactorContribution {
  factorKey: string;
  name: string;
  direction: "long" | "short";
  value: number; // 方向化原始值 dir·raw
  zscore: number; // 因果滚动 z（与训练 / 打分同窗口、同 clip）
  weight: number;
  contribution: number; // weight * zscore（pre-squash 贡献）
  share: number; // |contribution| / Σ|contribution| ∈ [0,1]
  isTopDriver: boolean; // 主要驱动因子（前 3 且 share≥0.15）
}

export interface TriggerExplain {
  schema: number;
  kind: TriggerExplainKind;
  side: "long" | "short" | "close";
  headline: string;
  summary?: string;
  metrics: TriggerMetric[];
  composite?: {
    score: number;
    preSquash: number;
    squash: boolean;
    enterLong?: number;
    enterShort?: number;
    exitBand?: number;
  };
  factors?: FactorContribution[];
  risk?: {
    stopLossPrice?: number | null;
    takeProfitPrice?: number | null;
    stopLossPct?: number | null;
    takeProfitPct?: number | null;
    maxHoldBars?: number | null;
  };
  meta?: {
    strategyName?: string;
    strategyKind?: string;
    instId?: string;
    bar?: string;
    barTs?: number;
    clOrdId?: string;
    ordId?: string;
  };
}

export interface Trade {
  id?: number;
  runId?: number;
  ts: number;
  side: "long" | "short" | "close";
  price: number;
  qty: number;
  fee: number;
  pnl: number;
  // 平仓收益率（已实现盈亏 / 开仓占用保证金）。仅实盘自动交易平仓携带；开仓 / 回测 / 模拟成交无此字段。
  pnlPct?: number | null;
  stopLossPct?: number | null;
  takeProfitPct?: number | null;
  maxHoldBars?: number | null;
  stopLossPrice?: number | null;
  takeProfitPrice?: number | null;
  positionAfter: number;
  reason: string;
  seeded?: boolean;
  // 自动交易可解释性（auto-trade-explainability）：可空，历史 / 非自动交易成交无该字段。
  explain?: TriggerExplain | null;
  clOrdId?: string;
  ordId?: string;
}

export type TradeDirection = "long" | "short";

export interface TradeTrailSegment {
  id: string;
  direction: TradeDirection;
  entry: Trade;
  exit: Trade;
  pnl: number;
  isWin: boolean;
  canRenderLine: boolean;
  lineColor: string;
  lineStyle?: number;
}

export interface TradeMarkerEvent {
  id: string;
  trade: Trade;
  kind: "entry-long" | "entry-short" | "close" | "orphan-close";
  direction?: TradeDirection;
  pairedSegmentId?: string;
}

export interface EquityPoint {
  ts: number;
  equity: number;
  drawdown: number;
  transient?: boolean;
}

export interface PaperPositionEvent {
  side: "long" | "short" | "flat";
  size: number;
  avgEntry: number;
  markPrice: number;
  unrealizedPnl: number;
}

export interface PaperStatus {
  state: string;
  warmedBars: number;
  seededTrades: number;
  initialCapital: number;
}

export interface PaperSnapshot {
  trades: Trade[];
  equity: EquityPoint[];
  position: PaperPositionEvent | null;
  status?: PaperStatus;
}

export interface PaperStrategySession {
  strategyId: number;
  strategyName: string;
  strategyKind?: StrategyKind;
  sessionId: string;
  runId: number;
  color: string;
  state: "starting" | "running" | "stopped" | "error";
}

export interface StrategyTrade extends Trade {
  strategyId: number;
  strategyName: string;
  color: string;
}

export interface StrategyEquityPoint extends EquityPoint {
  strategyId: number;
}

export interface PaperBatchSessionResult {
  strategyId: number;
  name: string;
  strategyKind?: StrategyKind;
  sessionId: string;
  runId: number;
  state: PaperStrategySession["state"];
}

export interface PaperBatchStartResult {
  groupId: string;
  instId: string;
  bar: string;
  sessions: PaperBatchSessionResult[];
}

export interface PaperBatchStopResult {
  groupId: string;
  runs: { strategyId: number; sessionId: string; run: Run }[];
}

// POST /api/paper/stop-all：停止注册表中所有 running 会话，回收配额（停止全部并重试）。
export interface PaperStopAllResult {
  stopped: number;
  runs: { strategyId: number; sessionId: string; run: Run }[];
}

export interface PaperChartFilter {
  strategyIds: number[];
}

export interface Overlay {
  id: number;
  name: string;
  color: string;
  equity: EquityPoint[];
}

export interface Metrics {
  totalReturn: number;
  winRate: number;
  profitFactor: number;
  maxDrawdown: number;
  sharpe: number;
  tradeCount: number;
  avgPnl: number;
}

export interface Run {
  id: number;
  strategyId: number;
  instId: string;
  bar: string;
  mode: Mode;
  status: string;
  startTs: number;
  endTs?: number | null;
  metrics: Metrics;
}

export interface PaperSessionInfo {
  sessionId: string;
  runId: number;
  instId: string;
  bar: string;
  state: string;
  equity: number;
  positionSide: "long" | "short" | "flat";
}

// GET /api/paper/sessions 行：session.info() 合并 _session_meta 的形态。
// strategyName 仅批量会话携带（单条 start 不写），groupId 为 null 时表示单条会话。
export interface PaperSessionRow extends PaperSessionInfo {
  strategyId: number;
  strategyName?: string;
  strategyKind?: StrategyKind;
  groupId: string | null;
}

export interface CompareItem {
  strategyId: number;
  name: string;
  runId: number;
  metrics: Metrics;
  equity: EquityPoint[];
}

export interface CompareResult {
  instId: string;
  bar: string;
  items: CompareItem[];
}

// 多样本回测（multi-sample-backtest）：单策略 × 多标的，逐标的各落一条 run；items 顺序与请求 instIds 去重后一致。
export interface MultiSampleItem {
  instId: string;
  status: "ok" | "error";
  runId?: number; // status==="ok"
  metrics?: Metrics; // status==="ok"，复用既有 Metrics
  effectiveCandleCount?: number;
  requestedBars?: number;
  truncated?: boolean;
  combo?: ComboPreviewResult["combo"]; // 组合策略多样本时透传（可选）
  error?: string; // status==="error"，非空错误消息
}

export interface MultiSampleResult {
  strategyId: number;
  bar: string;
  startTs: number;
  endTs: number;
  items: MultiSampleItem[];
}

export interface Health {
  status: string;
  okx: "up" | "down";
  okxLatencyMs: number | null;
  lastOkTs: number | null;
  wsConfigured: boolean;
  cachedFallbackEnabled?: boolean;
  maxPaperSessions?: number;
}

export interface ApiError {
  error: { code: string; message: string };
}
