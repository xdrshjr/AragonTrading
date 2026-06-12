// 与后端 Pydantic schema 对齐的共享类型 (spec §9.3)。
// 约定：本文件所有 ts 字段均为「秒级 UTC 时间戳」(C2，见 spec §4.4)。

export type Bar = "1m" | "5m" | "15m" | "30m" | "1H" | "4H" | "1D";
export type Mode = "backtest" | "paper";

export type Candle = { ts: number; o: number; h: number; l: number; c: number; vol: number; confirm?: boolean };

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
};

export type EquityPoint = { ts: number; equity: number; drawdown: number };

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

export type Health = { status: string; okx: "up" | "down" };

export type ApiError = { error: { code: string; message: string } };
