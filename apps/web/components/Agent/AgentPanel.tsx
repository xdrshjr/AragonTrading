"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { Button } from "@/components/ui";
import { applyShowChart } from "@/lib/agentActions";
import { agentWS } from "@/lib/agentWs";
import { appendUserMessage, reduceAgentEvent } from "@/lib/agentReducer";
import type {
  AgentServerEvent,
  AgentUiContext,
  ChatMessage,
  UiActionStore,
} from "@/lib/agentTypes";
import { useAppStore } from "@/lib/store";
import type { ReconnectingWS, WsEvent } from "@/lib/ws";
import AgentComposer from "./AgentComposer";
import AgentEmptyState from "./AgentEmptyState";
import AgentMessageList from "./AgentMessageList";
import AgentUnconfigured from "./AgentUnconfigured";

interface AgentConfig {
  configured: boolean;
  provider: string;
  model: string;
}

// 从全局 store 取 ui-action 所需 setter 子集（getState 可在 hook 外调用）。
function uiActionStore(): UiActionStore {
  const s = useAppStore.getState();
  return {
    setInst: s.setInst,
    setBar: s.setBar,
    setBacktestRange: s.setBacktestRange,
    setMode: s.setMode,
    setRunId: s.setRunId,
    setChartView: s.setChartView,
  };
}

function newUserId(): string {
  return `u-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`;
}

export default function AgentPanel() {
  const conversationId = useAppStore((s) => s.agentConversationId);
  const newConversation = useAppStore((s) => s.newConversation);

  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [config, setConfig] = useState<AgentConfig | null>(null);
  const [connected, setConnected] = useState(false);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [input, setInput] = useState("");
  const wsRef = useRef<ReconnectingWS | null>(null);

  // 首次挂载时若无会话 id，生成一个（SSR 安全：水合后于客户端执行）。
  useEffect(() => {
    if (!conversationId) newConversation();
  }, [conversationId, newConversation]);

  const handleEvent = useCallback((raw: WsEvent) => {
    const ev = raw as AgentServerEvent;
    switch (ev.type) {
      case "ready":
        setConfig({ configured: ev.data.configured, provider: ev.data.provider, model: ev.data.model });
        break;
      case "history":
        setMessages(reduceAgentEvent([], ev));
        break;
      case "message_start":
        setRunning(true);
        setError(null);
        setMessages((prev) => reduceAgentEvent(prev, ev));
        break;
      case "text_delta":
      case "thinking_delta":
      case "tool_call":
      case "tool_result":
      case "message_end":
        setMessages((prev) => reduceAgentEvent(prev, ev));
        break;
      case "ui_action":
        applyShowChart(ev.data, uiActionStore());
        break;
      case "done":
        setRunning(false);
        break;
      case "error":
        setRunning(false);
        setError(ev.data.message);
        break;
    }
  }, []);

  // 连接（会话 id 变更即重连到新会话 → 收到空 history → 清空消息流）。
  useEffect(() => {
    if (!conversationId) return;
    setMessages([]);
    setRunning(false);
    setError(null);
    const ws = agentWS(conversationId, handleEvent, setConnected);
    wsRef.current = ws;
    ws.connect();
    return () => {
      ws.close();
      wsRef.current = null;
    };
  }, [conversationId, handleEvent]);

  const send = useCallback(
    (text: string) => {
      const trimmed = text.trim();
      const ws = wsRef.current;
      if (!ws || !trimmed || running) return;
      setMessages((prev) => appendUserMessage(prev, newUserId(), trimmed));
      const s = useAppStore.getState();
      const ui: AgentUiContext = {
        instId: s.instId,
        bar: s.bar,
        mode: s.mode,
        strategyId: s.strategyId,
        backtestStartTs: s.backtestStartTs,
        backtestEndTs: s.backtestEndTs,
      };
      ws.send({ type: "user_message", data: { text: trimmed, ui } });
      setRunning(true);
      setError(null);
      setInput("");
    },
    [running],
  );

  const stop = useCallback(() => {
    wsRef.current?.send({ type: "cancel" });
    setRunning(false);
  }, []);

  const unconfigured = config !== null && !config.configured;

  return (
    <div className="flex h-full min-h-0 flex-col">
      <header className="flex items-center justify-between border-b border-[var(--border)] px-3 py-2">
        <div className="flex items-center gap-2">
          <span
            className={`h-1.5 w-1.5 rounded-full ${connected ? "bg-[var(--long)]" : "bg-[var(--muted)]"}`}
            title={connected ? "已连接" : "连接中…"}
          />
          <span className="text-xs font-semibold text-[var(--text)]">AI 助手</span>
          {config?.model && (
            <span className="text-[10px] text-[var(--muted)]">{config.model}</span>
          )}
        </div>
        <Button variant="ghost" size="sm" onClick={newConversation} aria-label="新对话">
          新对话
        </Button>
      </header>

      <div className="min-h-0 flex-1 overflow-y-auto">
        {unconfigured ? (
          <AgentUnconfigured provider={config.provider} model={config.model} />
        ) : messages.length === 0 ? (
          <AgentEmptyState onPick={send} />
        ) : (
          <AgentMessageList messages={messages} />
        )}
      </div>

      {error && (
        <div className="border-t border-[var(--border)] bg-[color-mix(in_srgb,var(--short)_10%,transparent)] px-3 py-1.5 text-[11px] text-[var(--short)]">
          {error}
        </div>
      )}

      {!unconfigured && (
        <AgentComposer
          value={input}
          onChange={setInput}
          onSend={() => send(input)}
          onStop={stop}
          running={running}
          disabled={!connected}
        />
      )}
    </div>
  );
}
