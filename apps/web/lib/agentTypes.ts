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

// 归一化 token 用量（后端 normalize_usage 输出的 camelCase 形状，RN-2；字段可缺失）。
export interface AgentUsage {
  inputTokens?: number | null;
  outputTokens?: number | null;
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

// show_factor ui-action directive（驱动因子实验室叠加因子值 / 组合复合得分）。
export interface ShowFactorAction {
  action: "show_factor";
  factorKey?: string;
  factorSetId?: number;
  instId?: string;
  bar?: Bar;
}

// 续流 resumed 控制事件的失败原因（RN-1）：run 已终结 / 缓冲空洞 / 会话不存在。
export type ResumedReason = "completed" | "cancelled" | "gap" | "none";

// Server → Client 事件（{type,data} 信封）。
// 韧性流式（iteration 3, RS-1/RS-4/RS-7）：run 生命周期事件信封追加**可选** `seq`（连接级
// ready/history 与纯活性 heartbeat 不带 seq）；新增 resumed/heartbeat 两个追加事件，老前端忽略。
export type AgentServerEvent =
  | { type: "ready"; data: { conversationId: string; provider: string; model: string; configured: boolean } }
  | { type: "history"; data: { messages: AgentMessage[] } }
  | { type: "message_start"; seq?: number; data: { id: string; role: "assistant" } }
  | { type: "text_delta"; seq?: number; data: { id: string; text: string } }
  | { type: "thinking_delta"; seq?: number; data: { id: string; text: string } }
  | { type: "tool_call"; seq?: number; data: { id: string; name: string; input: Record<string, unknown> } }
  | { type: "tool_result"; seq?: number; data: { id: string; name: string; ok: boolean; summary: string } }
  | { type: "ui_action"; seq?: number; data: ShowChartAction | ShowFactorAction }
  | { type: "message_end"; seq?: number; data: { id: string; usage?: AgentUsage } }
  | { type: "done"; seq?: number; data: { cancelled?: boolean } }
  | { type: "error"; seq?: number; data: { code: string; message: string } }
  // 续流握手响应（RS-2/RN-1）：ok=true 已重放并转实时续流；ok=false 按 reason 丢弃在途气泡。
  | { type: "resumed"; data: { ok: boolean; reason?: ResumedReason; fromSeq?: number } }
  // 纯活性心跳（RS-7/RN-2）：不带 seq、不入缓冲、不入消息流；仅用于半开检测 / 活性指示。
  | { type: "heartbeat"; data: Record<string, never> };

// Client → Server 续流请求（RS-12）：仅当本地有在途 streaming 气泡时发送。
export interface AgentResumeRequest {
  type: "resume";
  data: { lastSeq: number };
}

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
  usage?: AgentUsage; // 本轮 token 用量（H5/H12，可选展示）
  interrupted?: boolean; // 断连收束的在途气泡（H8a/RN-3）
}

// ---------------- AI 模型服务商（设置中心） ----------------
export type ApiStandard = "anthropic" | "openai";
export type ProviderKind = "official" | "custom";

// 服务商（与后端 AgentProviderOut 对齐）：**绝不含 raw key**，仅 hasKey/keyPreview。
export interface AgentProvider {
  id: number;
  slug: string;
  kind: ProviderKind;
  label: string;
  apiStandard: ApiStandard;
  baseUrl: string;
  model: string;
  userAgent: string;
  hasKey: boolean;
  keyPreview: string;
  isDefault: boolean;
  deletable: boolean;
  createdTs: number;
  updatedTs: number;
}

// 表单草稿（新增 / 编辑共用）：apiKey 留空表示保留原 key（编辑）或不填（新增）。
export interface ProviderDraft {
  label: string;
  apiStandard: ApiStandard;
  baseUrl: string;
  apiKey: string;
  model: string;
  userAgent?: string;
}

// 连通性测试结果（与后端 ProviderTestResult 对齐）。
export interface ProviderTestResult {
  ok: boolean;
  latencyMs?: number;
  model?: string;
  code?: string;
  message?: string;
}

// 当前生效服务摘要（GET /agent/providers.active）。
export interface ProviderActive {
  configured: boolean;
  providerId: number | null;
  label: string;
  provider: string;
  model: string;
}

// GET /agent/providers 响应。
export interface ProvidersListResponse {
  providers: AgentProvider[];
  defaultId: number | null;
  active: ProviderActive;
}

// ---------------- Agent 工具管理（设置中心 · Agent 工具管理） ----------------
export type AgentToolCategory =
  | "market"
  | "ui"
  | "strategy"
  | "analysis"
  | "factor"
  | "runs"
  | "general";

// 单个工具（与后端 AgentToolOut 对齐）：元信息来自注册表 + 合并后的启用态。
export interface AgentTool {
  name: string;
  description: string;
  category: AgentToolCategory;
  isUiAction: boolean;
  enabled: boolean;
  paramsSchema: { type?: string; properties?: Record<string, any>; required?: string[] };
}

// GET /agent/tools、POST /agent/tools/bulk 响应。
export interface AgentToolsResponse {
  tools: AgentTool[];
  enabledCount: number;
  totalCount: number;
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
