export type Bar = "1m" | "5m" | "15m" | "30m" | "1H" | "4H" | "1D";
export type Mode = "backtest" | "compare" | "paper";
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
  params: Record<string, unknown>;
  paramsSchema: ParamSchema;
  enabled: boolean;
  builtin: boolean;
  unavailable?: boolean;
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
  positionAfter: number;
  reason: string;
  seeded?: boolean;
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
