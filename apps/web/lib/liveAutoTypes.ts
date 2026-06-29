// 实时自动交易类型（live-auto-trading §5.1，camelCase 与后端 wire 对齐）。
import type { EquityPoint, PaperPositionEvent, Trade } from "./types";

// 自动会话停止原因（用户手动 / 会话级止盈止损 / 进程关停 / 启动失败回滚 / 行情中断保护）。
// hardening R-6：扩充 stale_feed（看门狗 flatten_on_stale 触发的保护性停止）。
export type AutoStopReason =
  | "user_stop"
  | "session_take_profit"
  | "session_stop_loss"
  | "shutdown"
  | "start_failed"
  | "stale_feed";

// 启动请求（全部金额字段可选；留空→服务端保守默认）。
export interface LiveAutoStartReq {
  strategyId: number;
  instId: string;
  bar: string;
  accountId?: number | null;
  capitalUsd?: number | null;
  leverage?: number | null;
  takeProfitUsd?: number | null;
  stopLossUsd?: number | null;
  priming?: boolean;
  primingBars?: number;
}

// 启动回执；budgetUsd 为夹紧后的最终生效预算（可能 < 用户填值，R4）。
export interface LiveAutoStartResult {
  sessionId: string;
  runId: number;
  strategyKind: string;
  instId: string;
  bar: string;
  budgetUsd: number;
  leverage: number;
}

// 活跃会话行（GET /live-auto/status 的 session 字段；前端重载后据此重连 WS）。
export interface LiveAutoSessionRow {
  sessionId: string;
  runId: number;
  state: string;
  strategyId: number | null;
  strategyName: string;
  instId: string;
  bar: string;
  budgetUsd: number;
  leverage: number;
  takeProfitUsd: number | null;
  stopLossUsd: number | null;
  positionSide: string;
  sessionPnlUsd: number;
  startedTs: number;
}

export interface LiveAutoStatusResponse {
  session: LiveAutoSessionRow | null;
  liveAutoTradingEnabled: boolean;
}

// WS status 事件载荷（与 paper status 同构 + 自动交易扩展字段）。
// hardening R-6：message?/stopReason? 已存在；新增 degraded?/flattenFailed?/residual?（向后兼容）。
export interface LiveAutoStatus {
  state: string;
  warmedBars?: number;
  sessionPnlUsd: number;
  positionSide?: string;
  budgetUsd?: number;
  leverage?: number;
  stopReason?: string | null;
  message?: string;
  degraded?: boolean;
  flattenFailed?: boolean;
  residual?: { side?: string; size?: number } | null;
}

// 自动交易事件流条目（hardening H6/H7）：error / status.message / 降级 等关键信息的轻量活动流载体。
export interface LiveAutoEvent {
  ts: number;
  level: "info" | "warn" | "error";
  code?: string;
  message: string;
}

export interface LiveAutoFlattenResult {
  ok: boolean;
  closedSz?: number;
  clOrdId?: string | null;
  code?: string;
  message?: string;
  note?: string;
}

export interface LiveAutoStopResult {
  sessionId: string;
  stopReason: string | null;
  run: unknown | null;
  flattened: LiveAutoFlattenResult;
}

// WS 订阅即重放快照（事件协议与 /ws/paper 同构）。
export interface LiveAutoSnapshot {
  trades: Trade[];
  equity: EquityPoint[];
  position: PaperPositionEvent | null;
  status: LiveAutoStatus;
}
