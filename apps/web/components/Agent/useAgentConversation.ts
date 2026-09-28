"use client";

import { useEffect, useRef, useState } from "react";
import { applyShowChart } from "@/lib/agentActions";
import { agentApi, type AgentConfig } from "@/lib/agentApi";
import { agentWS } from "@/lib/agentWs";
import {
  appendUserMessage, dropStreaming, reconcileHistory, reduceAgentEvent,
} from "@/lib/agentReducer";
import type { AgentServerEvent, ChatMessage } from "@/lib/agentTypes";
import { useAppStore } from "@/lib/store";
import type { ReconnectingWS, WsEvent } from "@/lib/ws";

interface ConversationScope {
  id: string;
  generation: number;
  disposed: boolean;
}

interface ConversationState {
  messages: ChatMessage[];
  config: AgentConfig | null;
  connected: boolean;
  running: boolean;
  error: string | null;
  input: string;
  canRetry: boolean;
  contextNotice: string | null;
}

function emptyState(): ConversationState {
  return {
    messages: [], config: null, connected: false, running: false,
    error: null, input: "", canRetry: false, contextNotice: null,
  };
}

type Publish = React.Dispatch<React.SetStateAction<ConversationState>>;

// 同步镜像覆盖同 tick 发送/清空/重连；React updater 仅计算状态，不写镜像。
class ConversationController {
  state = emptyState();
  scope: ConversationScope | null = null;
  ws: ReconnectingWS | null = null;
  initializedId = "";
  generation = 0;
  lastSeq = 0;
  lastUserText = "";
  streaming = false;
  pendingGap = false;

  constructor(private publish: Publish) {}

  isCurrent(scope: ConversationScope | null): scope is ConversationScope {
    return !!scope && !scope.disposed && this.scope === scope
      && scope.id === useAppStore.getState().agentConversationId;
  }

  update(patch: Partial<ConversationState>, scope = this.scope) {
    if (!this.isCurrent(scope)) return;
    this.state = { ...this.state, ...patch };
    this.publish((previous) => this.isCurrent(scope)
      ? { ...previous, ...patch } : previous);
  }

  updateRunning(running: boolean, scope: ConversationScope) {
    this.update({ running, canRetry: !!this.lastUserText && !running }, scope);
  }

  updateMessages(messages: ChatMessage[], scope: ConversationScope) {
    this.streaming = messages.some((message) => message.streaming);
    this.update({ messages }, scope);
  }

  reset() {
    this.state = emptyState();
    this.lastSeq = 0;
    this.lastUserText = "";
    this.streaming = false;
    this.pendingGap = false;
    this.publish(this.state);
  }

  disconnect(scope: ConversationScope, ws: ReconnectingWS) {
    scope.disposed = true;
    if (this.scope === scope) this.scope = null;
    ws.close();
    if (this.ws === ws) this.ws = null;
  }

  connect(id: string) {
    if (this.initializedId !== id) {
      this.reset();
      this.initializedId = id;
    }
    const scope = { id, generation: ++this.generation, disposed: false };
    this.scope = scope;
    this.update({ ...this.state, connected: false }, scope);
    const ws = agentWS(id,
      (event) => this.onEvent(scope, event),
      (open) => this.onConnection(scope, open));
    this.ws = ws;
    ws.connect();
    return () => this.disconnect(scope, ws);
  }

  onConnection(scope: ConversationScope, open: boolean) {
    if (!this.isCurrent(scope)) return;
    const wasConnected = this.state.connected;
    this.update({ connected: open }, scope);
    if (!open) {
      if (!this.streaming) this.updateRunning(false, scope);
      const error = this.streaming
        ? "连接中断，正在重连…（回答已保留）"
        : "连接失败，正在重连…";
      this.update({ error }, scope);
    } else if (!wasConnected) {
      this.update({ error: null }, scope);
      if (this.streaming) {
        this.ws?.send({ type: "resume", data: { lastSeq: this.lastSeq } });
      }
    }
  }

  async reconcile(scope: ConversationScope) {
    if (!this.isCurrent(scope)) return;
    try {
      const history = await agentApi.messages(scope.id);
      if (!this.isCurrent(scope)) return;
      this.updateMessages(
        reconcileHistory(this.state.messages, history), scope,
      );
    } catch {
      // 对账失败后保留当前回答，下一次权威 history 仍可恢复。
    }
  }

  onResumed(
    scope: ConversationScope,
    event: Extract<AgentServerEvent, { type: "resumed" }>,
  ) {
    if (event.data.ok) return;
    this.updateMessages(dropStreaming(this.state.messages), scope);
    this.update({ error: null }, scope);
    if (event.data.reason === "gap") this.pendingGap = true;
    else this.updateRunning(false, scope);
  }

  onControl(scope: ConversationScope, event: AgentServerEvent) {
    switch (event.type) {
      case "ready":
        this.update({ config: event.data }, scope);
        break;
      case "ui_action":
        applyUiAction(event.data);
        break;
      case "resumed":
        this.onResumed(scope, event);
        break;
      case "done":
        this.updateRunning(false, scope);
        if (this.pendingGap) {
          this.pendingGap = false;
          void this.reconcile(scope);
        }
        break;
      case "error":
        this.updateRunning(false, scope);
        this.update({ error: event.data.message }, scope);
        break;
    }
  }

  onEvent(scope: ConversationScope, raw: WsEvent) {
    if (!this.isCurrent(scope)) return;
    const event = raw as AgentServerEvent;
    if ("seq" in event && typeof event.seq === "number") {
      this.lastSeq = Math.max(this.lastSeq, event.seq);
    }
    if (event.type === "history") {
      this.updateMessages(
        reconcileHistory(this.state.messages, event.data.messages), scope,
      );
      return;
    }
    if (event.type === "message_start") {
      this.updateRunning(true, scope);
      this.update({ error: null }, scope);
    }
    const messages = reduceAgentEvent(this.state.messages, event);
    if (messages !== this.state.messages) this.updateMessages(messages, scope);
    this.onControl(scope, event);
  }

  send(text: string, id: string) {
    const scope = this.scope;
    if (!this.isCurrent(scope) || scope.id !== id) return;
    const trimmed = text.trim();
    if (!this.ws || !trimmed || this.state.running) return;
    if (trimmed.length > 16000) {
      this.update({ error: "消息过长，请精简后再发送。" }, scope);
      return;
    }
    if (!this.state.connected) {
      this.update({ error: "未连接，无法发送，请稍候重连。" }, scope);
      return;
    }
    this.lastUserText = trimmed;
    this.updateRunning(true, scope);
    const userId = `u-${Date.now()}-${Math.random().toString(36).slice(2)}`;
    this.updateMessages(
      appendUserMessage(this.state.messages, userId, trimmed), scope,
    );
    this.update({ error: null, input: "", contextNotice: null }, scope);
    const s = useAppStore.getState();
    const ui = {
      instId: s.instId, bar: s.bar, mode: s.mode, strategyId: s.strategyId,
      backtestStartTs: s.backtestStartTs, backtestEndTs: s.backtestEndTs,
    };
    this.ws.send({ type: "user_message", data: { text: trimmed, ui } });
  }

  stop(id: string) {
    const scope = this.scope;
    if (!this.isCurrent(scope) || scope.id !== id) return;
    this.ws?.send({ type: "cancel" });
    this.updateRunning(false, scope);
  }

  clearContext() {
    if (this.state.running) return false;
    const scope = this.scope;
    const ws = this.ws;
    if (scope) scope.disposed = true;
    this.scope = null;
    this.ws = null;
    ws?.close();
    this.reset();
    useAppStore.getState().newConversation();
    this.initializedId = useAppStore.getState().agentConversationId;
    this.state = { ...this.state, contextNotice:
      "上下文已清空，下一次提问将开始新的对话。"
      + "历史记录保留，自动交易状态不受影响。" };
    this.publish(this.state);
    return true;
  }
}

function applyUiAction(
  action: Extract<AgentServerEvent, { type: "ui_action" }>["data"],
) {
  const store = useAppStore.getState();
  if (action.action !== "show_factor") {
    applyShowChart(action, store);
    return;
  }
  if (action.instId) store.setInst(action.instId);
  if (action.bar) store.setBar(action.bar);
  store.setFactorOverlay({
    kind: action.factorSetId != null ? "score" : "factor",
    factorKey: action.factorKey, factorSetId: action.factorSetId,
  });
  store.setFactorLabSection(
    action.factorSetId != null ? "builder" : "evaluate",
  );
  store.setActiveView("factor");
}

/** 管理交互式会话；清空只建立新上下文，不删除历史或操作交易会话。 */
export function useAgentConversation() {
  const id = useAppStore((state) => state.agentConversationId);
  const [state, setState] = useState(emptyState);
  const controllerRef = useRef<ConversationController | null>(null);
  if (!controllerRef.current) {
    controllerRef.current = new ConversationController(setState);
  }
  const controller = controllerRef.current;
  useEffect(() => {
    if (useAppStore.getState().agentConversationId) return;
    let active = true;
    const initialize = () => {
      const store = useAppStore.getState();
      if (active && !store.agentConversationId) store.newConversation();
    };
    // skipHydration 的子 effect 先于 Home；生成 ID 前先恢复，避免覆盖旧存档。
    const hydration = useAppStore.persist.hasHydrated()
      ? undefined : useAppStore.persist.rehydrate();
    if (hydration instanceof Promise) void hydration.then(initialize);
    else initialize();
    return () => { active = false; };
  }, [id]);
  useEffect(() => {
    if (id) return controller.connect(id);
  }, [id, controller]);
  return {
    ...state,
    setInput: (input: string) => controller.update({ input }),
    send: (text: string) => controller.send(text, id),
    retry: () => controller.send(controller.lastUserText, id),
    stop: () => controller.stop(id),
    clearContext: () => controller.clearContext(),
  };
}
