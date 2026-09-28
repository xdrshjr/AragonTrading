import { StrictMode, useEffect, useRef } from "react";
import { act, cleanup, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { agentApi } from "@/lib/agentApi";
import { agentWS } from "@/lib/agentWs";
import { useAppStore } from "@/lib/store";
import type { WsEvent } from "@/lib/ws";
import { useAgentConversation } from "./useAgentConversation";

vi.mock("@/lib/agentWs", () => ({ agentWS: vi.fn(() => ({
  connect: vi.fn(), close: vi.fn(), send: vi.fn(),
})) }));
vi.mock("@/lib/agentApi", () => ({ agentApi: {
  messages: vi.fn().mockResolvedValue([]), clear: vi.fn(),
} }));

function connection(index = -1) {
  const calls = vi.mocked(agentWS).mock.calls;
  const at = index < 0 ? calls.length - 1 : index;
  const emit = (event: WsEvent & { seq?: number }) => calls[at][1](event);
  return { id: calls[at][0], emit, state: calls[at][2]!,
    ws: vi.mocked(agentWS).mock.results[at].value };
}

beforeEach(() => {
  vi.clearAllMocks();
  useAppStore.setState({ agentConversationId: "old-conversation" });
});
afterEach(cleanup);

it("同一消息重试复用UUID，新消息另建UUID并传递账户模式", () => {
  const { result } = renderHook(useAgentConversation);
  const socket = connection();
  act(() => {
    socket.state(true);
    result.current.send("查看持仓");
    socket.emit({ type: "error", data: { message: "断线" } });
    result.current.retry();
    socket.emit({ type: "done", data: {} });
    result.current.send("重新生成预览");
  });
  const requests = socket.ws.send.mock.calls.map(([request]: [any]) => request);
  expect(requests[0].data.clientMessageId).toMatch(/^[0-9a-f-]{36}$/);
  expect(requests[1].data.clientMessageId).toBe(requests[0].data.clientMessageId);
  expect(requests[2].data.clientMessageId).not.toBe(requests[0].data.clientMessageId);
  expect(requests[0].data.ui.accountMode).toBe(useAppStore.getState().accountMode);
});

it("首次空内存ID先恢复持久化会话，不能覆盖清空后保存的新ID", () => {
  useAppStore.setState({ agentConversationId: "" });
  const persisted = JSON.parse(localStorage.getItem("aragon-ui")!);
  persisted.state.agentConversationId = "persisted-new-context";
  localStorage.setItem("aragon-ui", JSON.stringify(persisted));
  const hydrated = vi.spyOn(useAppStore.persist, "hasHydrated")
    .mockReturnValue(false);
  renderHook(useAgentConversation);
  expect(useAppStore.getState().agentConversationId).toBe("persisted-new-context");
  expect(connection().id).toBe("persisted-new-context");
  hydrated.mockRestore();
});

it("清空所有聊天状态，保留交易对象，新请求只走新socket", () => {
  const { result } = renderHook(useAgentConversation);
  const old = connection();
  act(() => {
    old.state(true);
    old.emit({ type: "ready", data: { configured: true, model: "old" } });
    result.current.send("旧问题");
    old.emit({ type: "error", data: { message: "失败" } });
    result.current.setInput("旧草稿");
  });
  expect(result.current.canRetry).toBe(true);
  const group = useAppStore.getState().aiTradeGroup;
  act(() => expect(result.current.clearContext()).toBe(true));
  expect(result.current).toMatchObject({ messages: [], config: null,
    running: false, connected: false, input: "", error: null, canRetry: false });
  expect(useAppStore.getState().aiTradeGroup).toBe(group);
  expect(connection().id).not.toBe(old.id);
  expect(old.ws.close).toHaveBeenCalled();
  const notice = result.current.contextNotice;
  act(() => {
    result.current.retry();
    result.current.send("");
    result.current.send("x".repeat(16001));
    result.current.send("离线问题");
  });
  expect(result.current.contextNotice).toBe(notice);
  const current = connection();
  act(() => { current.state(true); result.current.send("新问题"); });
  expect(current.ws.send).toHaveBeenCalledWith(expect.objectContaining({
    type: "user_message", data: expect.objectContaining({ text: "新问题" }),
  }));
  expect(old.ws.send).toHaveBeenCalledTimes(1);
  expect(result.current.contextNotice).toBeNull();
  expect(agentApi.clear).not.toHaveBeenCalled();
});

it("同tick发送阻止清空与重复发送；停止后立即清空不删除历史", () => {
  const { result } = renderHook(useAgentConversation);
  const old = connection();
  act(() => {
    old.state(true);
    result.current.send("问题");
    result.current.send("重复");
    expect(result.current.clearContext()).toBe(false);
  });
  expect(old.ws.send).toHaveBeenCalledTimes(1);
  expect(useAppStore.getState().agentConversationId).toBe(old.id);
  act(() => {
    result.current.stop();
    expect(result.current.clearContext()).toBe(true);
  });
  expect(old.ws.send).toHaveBeenLastCalledWith({ type: "cancel" });
  const current = connection();
  act(() => { current.state(true); result.current.send("新问题"); });
  act(() => old.emit({ type: "done", data: {} }));
  expect(result.current.running).toBe(true);
  expect(agentApi.clear).not.toHaveBeenCalled();
});

const oldEvents = [
  { type: "ready", data: { configured: true, model: "old" } },
  { type: "history", data: { messages: [{ id: 1, role: "user", ts: 0,
    blocks: [{ type: "text", text: "旧历史" }] }] } },
  { type: "message_start", data: { id: "old", role: "assistant" } },
  { type: "text_delta", data: { id: "old", text: "旧消息" } },
  { type: "thinking_delta", data: { id: "old", text: "旧思考" } },
  { type: "tool_call", data: { id: "tool", name: "test", input: {} } },
  { type: "tool_result", data: { id: "tool", ok: true, summary: "旧工具" } },
  { type: "message_end", data: { id: "old" } },
  { type: "done", data: {} },
  { type: "error", data: { message: "旧错误" } },
  { type: "resumed", data: { ok: false, reason: "gap" } },
  { type: "heartbeat", data: {} },
  { type: "ui_action", data: { action: "show_chart", instId: "OLD", runId: 999 } },
  { type: "ui_action", data: { action: "show_factor", instId: "OLD", factorKey: "old" } },
];

it.each(oldEvents)("清空后旧事件 $type 无消息或工作台副作用", (event) => {
  const { result } = renderHook(useAgentConversation);
  const old = connection();
  act(() => result.current.clearContext());
  const current = connection();
  act(() => {
    current.state(true);
    current.emit({ type: "message_start", seq: 2,
      data: { id: "new", role: "assistant" } });
  });
  const state = useAppStore.getState();
  const messages = result.current.messages;
  act(() => { old.emit({ ...event, seq: 999 }); old.state(false); });
  expect(result.current.messages).toBe(messages);
  expect(result.current).toMatchObject({ connected: true, running: true,
    error: null, config: null });
  expect(useAppStore.getState()).toBe(state);
  act(() => { current.state(false); current.state(true); });
  expect(current.ws.send).toHaveBeenLastCalledWith({
    type: "resume", data: { lastSeq: 2 },
  });
});

it("外部ID同tick更换后旧动作、事件及排队消息不能写入", () => {
  const { result } = renderHook(useAgentConversation);
  const old = connection();
  const actions = result.current;
  act(() => {
    old.state(true);
    actions.send("已排队");
    useAppStore.setState({ agentConversationId: "external" });
    actions.send("不应发送");
    actions.retry();
    actions.stop();
    old.emit({ type: "ui_action", data: { action: "show_factor", instId: "OLD" } });
  });
  expect(old.ws.send).toHaveBeenCalledTimes(1);
  expect(result.current.messages).toEqual([]);
  expect(result.current.running).toBe(false);
  expect(connection().id).toBe("external");
  act(() => { connection().state(true); actions.send("旧闭包"); });
  expect(connection().ws.send).not.toHaveBeenCalled();
});

it("新连接首次失败仍显示反馈，打开连接后清除错误", () => {
  const { result } = renderHook(useAgentConversation);
  act(() => result.current.clearContext());
  const id = connection().id;
  act(() => connection().state(false));
  expect(result.current.error).toContain("正在重连");
  expect(useAppStore.getState().agentConversationId).toBe(id);
  act(() => connection().state(true));
  expect(result.current.error).toBeNull();
});

it("message_start与断线重连同tick仍保留流并发送正确resume锚点", async () => {
  const { result } = renderHook(useAgentConversation);
  const current = connection();
  act(() => {
    current.state(true);
    current.emit({ type: "message_start", seq: 7,
      data: { id: "stream", role: "assistant" } });
    current.state(false);
    current.state(true);
  });
  expect(result.current.running).toBe(true);
  expect(result.current.messages[0].streaming).toBe(true);
  expect(current.ws.send).toHaveBeenLastCalledWith({
    type: "resume", data: { lastSeq: 7 },
  });
  vi.mocked(agentApi.messages).mockResolvedValueOnce([
    { id: 8, role: "assistant", ts: 0,
      blocks: [{ type: "text", text: "权威回答" }] },
  ]);
  await act(async () => {
    current.emit({ type: "resumed", data: { ok: false, reason: "gap" } });
    current.emit({ type: "done", data: {} });
  });
  expect(result.current.messages.map((message) => message.text)).toEqual(["权威回答"]);
  expect(result.current.running).toBe(false);
});

it("StrictMode空ID只生成一次，旧scope和卸载后REST失去写权", async () => {
  useAppStore.setState({ agentConversationId: "" });
  const newConversation = vi.spyOn(useAppStore.getState(), "newConversation");
  let resolve!: (history: never[]) => void;
  vi.mocked(agentApi.messages).mockReturnValueOnce(new Promise((done) => {
    resolve = done;
  }));
  const { result, unmount } = renderHook(useAgentConversation, {
    wrapper: ({ children }) => <StrictMode>{children}</StrictMode>,
  });
  expect(newConversation).toHaveBeenCalledOnce();
  act(() => result.current.setInput("草稿"));
  expect(result.current.input).toBe("草稿");
  act(() => {
    connection().emit({ type: "resumed", data: { ok: false, reason: "gap" } });
    connection().emit({ type: "done", data: {} });
  });
  const state = result.current;
  unmount();
  await act(async () => resolve([]));
  expect(result.current).toBe(state);
  newConversation.mockRestore();
});

it("StrictMode同ID重放恢复已排队草稿、消息及续流镜像", () => {
  const { result } = renderHook(() => {
    const conversation = useAgentConversation();
    const initialized = useRef(false);
    useEffect(() => {
      if (initialized.current) return;
      initialized.current = true;
      conversation.setInput("保留草稿");
      connection().emit({ type: "message_start", seq: 9,
        data: { id: "preserved", role: "assistant" } });
    }, [conversation]);
    return conversation;
  }, { wrapper: ({ children }) => <StrictMode>{children}</StrictMode> });
  expect(result.current.input).toBe("保留草稿");
  expect(result.current.running).toBe(true);
  expect(result.current.messages[0].id).toBe("preserved");
  const old = connection(0);
  act(() => {
    connection().state(true);
    old.emit({ type: "error", data: { message: "过期" } });
    old.state(false);
  });
  expect(result.current.error).toBeNull();
  expect(result.current.connected).toBe(true);
  expect(connection().ws.send).toHaveBeenLastCalledWith({
    type: "resume", data: { lastSeq: 9 },
  });
});
