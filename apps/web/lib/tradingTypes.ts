// 实盘交易类型（与后端 schemas PlaceOrder*/SetLeverage*/TradingConfig*/LiveFill 对齐，camelCase）。
// 订单真源在 OKX；前端绝不缓存 / 伪造成交（C1）。
import type { OkxAccountPosition } from "./accountTypes";
import type { TriggerExplain } from "./types";

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
  tradeId?: string; // OKX 每笔成交唯一 id（行 key / 展开态身份，RV2）
  clOrdId?: string; // OKX 回带（自动交易为 aragon/aragonc 前缀）
  explain?: TriggerExplain; // 命中自动交易触发解释时存在（live-fills-explainability）
}

export type LivePosition = OkxAccountPosition; // 复用 accountTypes（spec §5.2）

export interface LiveFillsResponse {
  fills: LiveFill[];
}

// GET /trading/open-orders：未结委托归一形态（普通挂单 + 策略委托）。
// kind 由后端派生：limit_close 限价平仓 / tp 止盈 / sl 止损 / limit_open 限价开仓（前端按 kind 过滤展示）。
export type OpenOrderKind = "limit_close" | "tp" | "sl" | "limit_open";

export interface LiveOpenOrder {
  kind: OpenOrderKind;
  instId: string;
  posSide: "long" | "short" | "net";
  side: "buy" | "sell";
  px: number | null; // 限价委托价（tp/sl 触发后市价时为 null）
  triggerPx: number | null; // 策略委托触发价（普通挂单为 null）
  sz: number | null;
  state: string; // live | partially_filled ...
  reduceOnly: boolean;
  ordId: string | null;
  algoId: string | null;
  createdTs: number; // 秒级 UTC
}

export interface LiveOpenOrdersResponse {
  orders: LiveOpenOrder[];
}

export interface LivePositionsResponse {
  positions: LivePosition[];
}

// POST /trading/close 请求（单仓平仓；clOrdId 由后端生成，前端不传）。
// limit=挂单平仓（须带 px、可选 sz）；market=整仓市价平（px/sz 忽略，OKX 自行整仓平）。
export interface ClosePositionReq {
  accountId?: number;
  instId: string;
  posSide: "long" | "short" | "net";
  pos: number; // 带符号张数（net 判向参考；sz 上限以后端实时持仓为准）
  mgnMode: TdMode;
  ordType: OrderType;
  px?: number;
  sz?: number;
}

export interface ClosePositionResult {
  ok: boolean;
  mode: OrderType;
  ordId?: string | null;
  clOrdId?: string | null;
  closedSz?: number | null; // 市价单回填本地 |pos|；限价单为「委托」平仓量（未必已成交）
  code?: string | null;
  message?: string | null;
}

// POST /trading/close-all 逐仓结果行（单仓失败不致整体失败，R10）。
export interface CloseAllResultRow {
  instId: string;
  posSide: string;
  ok: boolean;
  clOrdId?: string | null;
  code?: string | null;
  message?: string | null;
}

export interface CloseAllResult {
  ok: boolean; // 仅在全部成功为真（诚实汇总）
  closedCount: number;
  failedCount: number;
  results: CloseAllResultRow[];
}
