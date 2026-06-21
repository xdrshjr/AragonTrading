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
      return patchById(state, ev.data.id, (m) => ({ ...m, streaming: false }));
    default:
      return state;
  }
}

/** 追加一条用户气泡（发送时本地乐观更新；服务端不回显 user_message）。 */
export function appendUserMessage(state: ChatMessage[], id: string, text: string): ChatMessage[] {
  return [...state, { id, role: "user", text, thinking: "", toolCalls: [], streaming: false }];
}
