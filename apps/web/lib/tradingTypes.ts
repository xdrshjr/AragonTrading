// 实盘交易类型（与后端 schemas PlaceOrder*/SetLeverage*/TradingConfig*/LiveFill 对齐，camelCase）。
// 订单真源在 OKX；前端绝不缓存 / 伪造成交（C1）。
import type { OkxAccountPosition } from "./accountTypes";

export type OrderDirection = "long" | "short"; // long=买多 | short=买空
export type OrderType = "limit" | "market";
export type TdMode = "cross" | "isolated";
export type PosMode = "long_short_mode" | "net_mode";

// 合约元数据（来源 GET /api/v5/public/instruments，C1-1）；下单数学全依赖之。
export interface InstMeta {
  ctVal: number;
  ctValCcy: string;
  lotSz: number;
  minSz: number;
  tickSz: number;
  maxLever: number;
}

// GET /trading/config：下单面板上下文。
export interface TradingConfig {
  accountId: number;
  instId: string;
  posMode: PosMode;
  tdMode: TdMode;
  availUsdt: number;
  instMeta: InstMeta;
  lastPx: number | null;
  instrumentSupported: boolean; // settleCcy=USDT 且 SWAP/FUTURES 才可下单（C1-4）
  liveTradingEnabled: boolean;
}

// POST /trading/order 请求（px 仅 limit 必填；clOrdId 由后端生成，前端不传，C1-2）。
export interface PlaceOrderReq {
  accountId?: number;
  instId: string;
  direction: OrderDirection;
  ordType: OrderType;
  tdMode: TdMode;
  px?: number;
  sz: number;
  lever?: number;
  tpTriggerPx?: number;
  slTriggerPx?: number;
}

export interface PlaceOrderResult {
  ok: boolean;
  ordId?: string;
  clOrdId?: string;
  code?: string | null;
  message?: string | null;
}

// POST /trading/leverage（posSide 仅 isolated + long_short_mode 时传，C2-1）。
export interface SetLeverageReq {
  accountId?: number;
  instId: string;
  lever: number;
  mgnMode: TdMode;
  posSide?: OrderDirection | null;
}

export interface SetLeverageResult {
  ok: boolean;
  instId: string;
  lever: number;
  mgnMode: string;
  posSide?: string | null;
}

// GET /trading/fills 单行。
export interface LiveFill {
  ts: number;
  instId: string;
  side: "buy" | "sell";
  fillPx: number;
  fillSz: number;
  fee: number;
  ordId: string;
}

export type LivePosition = OkxAccountPosition; // 复用 accountTypes（spec §5.2）

export interface LiveFillsResponse {
  fills: LiveFill[];
}

export interface LivePositionsResponse {
  positions: LivePosition[];
}
