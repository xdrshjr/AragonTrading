// Agent 事件 / 消息 / 工具调用类型（与后端 WS 协议对齐）(Agent Trading Copilot §4)。
import type { Bar, ChartView, Mode } from "./types";

// 前端连接时上报的当前工作台上下文（注入 system prompt + ui-action 默认值）。
export interface AgentUiContext {
  instId: string;
  bar: Bar;
  mode: Mode;
  strategyId?: number | null;
  backtestStartTs?: number;
  backtestEndTs?: number;
}

// 持久化 / history 消息的归一化 block（camelCase，与后端 conversation.py 一致）。
export type AgentBlock =
  | { type: "text"; text: string }
  | { type: "tool_use"; id: string; name: string; input: Record<string, unknown> }
  | { type: "tool_result"; toolUseId: string; ok: boolean; summary: string };

export interface AgentMessage {
  id: string | number;
  role: "user" | "assistant" | "tool";
  ts: number;
  blocks: AgentBlock[];
}

// show_chart ui-action directive（后端 payload 原样下发）。
export interface ShowChartAction {
  action: "show_chart";
  instId?: string;
  bar?: Bar;
  startTs?: number;
  endTs?: number;
  runId?: number;
}

// Server → Client 事件（{type,data} 信封）。
export type AgentServerEvent =
  | { type: "ready"; data: { conversationId: string; provider: string; model: string; configured: boolean } }
  | { type: "history"; data: { messages: AgentMessage[] } }
  | { type: "message_start"; data: { id: string; role: "assistant" } }
  | { type: "text_delta"; data: { id: string; text: string } }
  | { type: "thinking_delta"; data: { id: string; text: string } }
  | { type: "tool_call"; data: { id: string; name: string; input: Record<string, unknown> } }
  | { type: "tool_result"; data: { id: string; name: string; ok: boolean; summary: string } }
  | { type: "ui_action"; data: ShowChartAction }
  | { type: "message_end"; data: { id: string; usage?: Record<string, unknown> } }
  | { type: "done"; data: { cancelled?: boolean } }
  | { type: "error"; data: { code: string; message: string } };

// UI 渲染模型：一条 user/assistant 气泡 + 其下工具卡片。
export interface ToolCall {
  id: string;
  name: string;
  input: Record<string, unknown>;
  ok?: boolean;
  summary?: string;
  done: boolean;
}

export interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  text: string;
  thinking: string;
  toolCalls: ToolCall[];
  streaming: boolean;
}

// store 中 ui-action 需要的 setter 子集（lib/agentActions 据此映射）。
export interface UiActionStore {
  setInst: (instId: string) => void;
  setBar: (bar: Bar) => void;
  setBacktestRange: (startTs: number, endTs: number) => void;
  setMode: (mode: Mode) => void;
  setRunId: (id: number | null) => void;
  setChartView: (view: ChartView) => void;
}
