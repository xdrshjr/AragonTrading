// 与后端 Pydantic schema 对齐的共享类型 (spec §9.3)。
// 约定：本文件所有 ts 字段均为「秒级 UTC 时间戳」(C2，见 spec §4.4)。

export type Bar = "1m" | "5m" | "15m" | "30m" | "1H" | "4H" | "1D";
export type Mode = "backtest" | "compare" | "paper";
// 顶部账户模式（live-paper-account-mode 支柱 A）：sim=模拟盘；live=实盘（Phase 2，本期禁用）。
export type AccountMode = "sim" | "live";
export type ChartView = "run-range" | "compare-range" | "live-follow";

// 左侧图表区上方「K线价格走势 / 净值」视图页签（chart-workspace-layout 需求 1）。
// 注意：与既有 ChartView（历史区间选择器）正交，故另起字段名 chartPane（见 spec §实施过程发现的方案缺陷）。
export type ChartPane = "price" | "equity";
// 左侧下区「成交记录 / 当前持仓」数据页签（需求 3）。
export type BottomTab = "trades" | "positions";

// /candles 响应头 X-Aragon-Data-Source 的取值（F2）：live=真实实时，cache=缓存样例·非实时。
export type DataSource = "live" | "cache";

export type Candle = { ts: number; o: number; h: number; l: number; c: number; vol: number; confirm?: boolean };

export type MarketTrade = {
  ts: number;
  price: number;
  qty: number;
  side: "buy" | "sell";
  tradeId?: string | null;
};

export type OrderBookLevel = { price: number; qty: number; orders?: number | null };
export type OrderBookSnapshot = {
  ts: number;
  bids: OrderBookLevel[];
  asks: OrderBookLevel[];
};

export type MarketTicker = {
  ts: number;
  last: number;
  open24h: number | null;
  vol24h: number | null;
  volCcy24h: number | null;
};

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

export type Instrument = { instId: string; instType: "SWAP" | "SPOT"; base: string; quote: string };

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

export type Strategy = {
  id: number;
  templateKey: string;
  name: string;
  description: string;
  params: Record<string, unknown>;
  paramsSchema: ParamSchema;
  enabled: boolean;
  builtin: boolean;
  unavailable?: boolean;
};

export type Trade = {
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
  seeded?: boolean; // 预热回放（warm-start priming）回填的历史成交标记（live-paper-account-mode B1）
};

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

// transient：逐笔瞬时盯市点（不入库/不计指标，前端按 ts upsert 仅用于绘动，B2）。
export type EquityPoint = { ts: number; equity: number; drawdown: number; transient?: boolean };

// 实时模拟当前持仓便捷读数（可选，B2/§7.1）；权威视图仍由 deriveOpenPosition 推导。
export interface PaperPositionEvent {
  side: "long" | "short" | "flat";
  size: number;
  avgEntry: number;
  markPrice: number;
  unrealizedPnl: number;
}

// 实时模拟会话状态（订阅即发，显式标注预热边界，B3/§7.1）。
export interface PaperStatus {
  state: string;
  warmedBars: number;
  seededTrades: number;
  initialCapital: number;
}

// 订阅即重放快照（晚订阅 / 断线重连恢复，B3/§7.1）。
export interface PaperSnapshot {
  trades: Trade[];
  equity: EquityPoint[];
  position: PaperPositionEvent | null;
  status?: PaperStatus;
}

// 一条对比净值叠加曲线（compare 模式，F3）。由 CandleChart 上移至此，供 EquityChart / ChartWorkspace 共享。
export type Overlay = { id: number; name: string; color: string; equity: EquityPoint[] };

// 合约行情快照（chart-workspace-layout P2，/tickers）。缺省时 InstrumentList 仅当前标的有价。
export type Ticker = { instId: string; last: number; open24h: number; changePct: number };

export type Metrics = {
  totalReturn: number;
  winRate: number;
  profitFactor: number;
  maxDrawdown: number;
  sharpe: number;
  tradeCount: number;
  avgPnl: number;
};

export type Run = {
  id: number;
  strategyId: number;
  instId: string;
  bar: string;
  mode: Mode;
  status: string;
  startTs: number;
  endTs?: number | null;
  metrics: Metrics;
};

export type PaperSessionInfo = {
  sessionId: string;
  runId: number;
  instId: string;
  bar: string;
  state: string;
  equity: number;
  positionSide: "long" | "short" | "flat";
};

// 多策略对比结果（F3）。equity 为归一化前的真实净值，前端按 equity/initial-1 归一化叠加。
export type CompareItem = {
  strategyId: number;
  name: string;
  runId: number;
  metrics: Metrics;
  equity: EquityPoint[];
};
export type CompareResult = { instId: string; bar: string; items: CompareItem[] };

export type Health = {
  status: string;
  okx: "up" | "down";
  okxLatencyMs: number | null;
  lastOkTs: number | null;
  wsConfigured: boolean;
  cachedFallbackEnabled?: boolean; // F2 新增（可选，向后兼容）
};

export type ApiError = { error: { code: string; message: string } };
