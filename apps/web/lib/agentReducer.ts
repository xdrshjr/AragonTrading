// Agent 事件归约：把流式 Server 事件 / history 折叠为 ChatMessage[]（纯函数，带单测）。
import type {
  AgentBlock,
  AgentMessage,
  AgentServerEvent,
  ChatMessage,
  ToolCall,
} from "./agentTypes";

function emptyAssistant(id: string): ChatMessage {
  return { id, role: "assistant", text: "", thinking: "", toolCalls: [], streaming: true };
}

function textOf(blocks: AgentBlock[]): string {
  return blocks
    .filter((b): b is Extract<AgentBlock, { type: "text" }> => b.type === "text")
    .map((b) => b.text)
    .join("");
}

function toolCallsOf(blocks: AgentBlock[]): ToolCall[] {
  return blocks
    .filter((b): b is Extract<AgentBlock, { type: "tool_use" }> => b.type === "tool_use")
    .map((b) => ({ id: b.id, name: b.name, input: b.input, done: true }));
}

// 把某轮 tool 消息的 tool_result 块回填到此前 assistant 气泡的工具卡（按 toolUseId 配对）。
function applyToolResults(out: ChatMessage[], blocks: AgentBlock[]): void {
  for (const block of blocks) {
    if (block.type !== "tool_result") continue;
    for (let i = out.length - 1; i >= 0; i -= 1) {
      const call = out[i].toolCalls.find((c) => c.id === block.toolUseId);
      if (call) {
        call.ok = block.ok;
        call.summary = block.summary;
        call.done = true;
        break;
      }
    }
  }
}

/** 重建历史：user/assistant 成气泡，tool 行回填工具卡（不单列气泡，P1-B）。 */
export function buildHistory(messages: AgentMessage[]): ChatMessage[] {
  const out: ChatMessage[] = [];
  for (const m of messages) {
    if (m.role === "tool") {
      applyToolResults(out, m.blocks);
      continue;
    }
    out.push({
      id: String(m.id),
      role: m.role,
      text: textOf(m.blocks),
      thinking: "",
      toolCalls: toolCallsOf(m.blocks),
      streaming: false,
    });
  }
  return out;
}

function patchById(
  state: ChatMessage[],
  id: string,
  patch: (m: ChatMessage) => ChatMessage,
): ChatMessage[] {
  return state.map((m) => (m.id === id ? patch(m) : m));
}

function addToolCall(
  state: ChatMessage[],
  data: { id: string; name: string; input: Record<string, unknown> },
): ChatMessage[] {
  for (let i = state.length - 1; i >= 0; i -= 1) {
    if (state[i].role === "assistant") {
      const next = [...state];
      const call: ToolCall = { id: data.id, name: data.name, input: data.input, done: false };
      next[i] = { ...next[i], toolCalls: [...next[i].toolCalls, call] };
      return next;
    }
  }
  return state;
}

function patchToolResult(
  state: ChatMessage[],
  data: { id: string; ok: boolean; summary: string },
): ChatMessage[] {
  return state.map((m) => {
    if (!m.toolCalls.some((c) => c.id === data.id)) return m;
    return {
      ...m,
      toolCalls: m.toolCalls.map((c) =>
        c.id === data.id ? { ...c, ok: data.ok, summary: data.summary, done: true } : c,
      ),
    };
  });
}

/** 把单个 Server 事件折叠进 ChatMessage[]；非消息类事件（ready/ui_action/done/error）原样返回。 */
export function reduceAgentEvent(state: ChatMessage[], ev: AgentServerEvent): ChatMessage[] {
  switch (ev.type) {
    case "history":
      return buildHistory(ev.data.messages);
    case "message_start":
      return [...state, emptyAssistant(ev.data.id)];
    case "text_delta":
      return patchById(state, ev.data.id, (m) => ({ ...m, text: m.text + ev.data.text }));
    case "thinking_delta":
      return patchById(state, ev.data.id, (m) => ({ ...m, thinking: m.thinking + ev.data.text }));
    case "tool_call":
      return addToolCall(state, ev.data);
    case "tool_result":
      return patchToolResult(state, ev.data);
    case "message_end":
      return patchById(state, ev.data.id, (m) => ({
        ...m,
        streaming: false,
        usage: ev.data.usage ?? m.usage,
      }));
    default:
      return state;
  }
}

/** 追加一条用户气泡（发送时本地乐观更新；服务端不回显 user_message）。 */
export function appendUserMessage(state: ChatMessage[], id: string, text: string): ChatMessage[] {
  return [...state, { id, role: "user", text, thinking: "", toolCalls: [], streaming: false }];
}

/**
 * 断连即收束：把仍 `streaming` 的在途气泡置为已结束并标记中断（H8a / RN-3）。
 *
 * 任何断连都触发服务端 cancel（永不再有 done 到达原 msg_id），故必须就地收束，
 * 否则气泡永远停在"流式中"，且会成为重连重复（reconcileHistory）的根源。
 */
export function settleStreaming(state: ChatMessage[]): ChatMessage[] {
  return state.map((m) => (m.streaming ? { ...m, streaming: false, interrupted: true } : m));
}

/**
 * 重连以 DB 历史为权威重建（替代 v1 的 merge-by-id，RN-1）。
 *
 * 1. 已完成轮以 `history`（DB-int id）整体重建——天然无重复、无 id 空间错配。
 * 2. 仅当 `prev` 中存在恰一条仍 `streaming` 的在途气泡时追加保留它（history 罕见地在
 *    流式中途到达的兜底）；否则不保留。
 * 3. **绝不跨 `uuid`(客户端流式 id) / `DB-int`(history id) 边界按 id 去重**——v1 缺陷根因。
 */
export function reconcileHistory(prev: ChatMessage[], history: AgentMessage[]): ChatMessage[] {
  const rebuilt = buildHistory(history);
  const inFlight = prev.filter((m) => m.streaming);
  if (inFlight.length === 1) {
    return [...rebuilt, inFlight[0]];
  }
  return rebuilt;
}
