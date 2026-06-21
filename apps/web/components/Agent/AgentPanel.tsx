"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { Button } from "@/components/ui";
import { applyShowChart } from "@/lib/agentActions";
import { agentWS } from "@/lib/agentWs";
import {
  appendUserMessage,
  reconcileHistory,
  reduceAgentEvent,
  settleStreaming,
} from "@/lib/agentReducer";
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
  // 连接态变迁探针（H8a）：检测 true→false 瞬断以复位 running / 收束在途气泡。
  const connectedRef = useRef(false);
  // 最近一条 user 文本（H12 失败重试用）。
  const lastUserTextRef = useRef<string>("");

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
        // 重连以 DB 历史为权威重建：不闪烁 / 不重复 / 不孤儿（H8b / RN-1）。
        setMessages((prev) => reconcileHistory(prev, ev.data.messages));
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

  // 连接态变迁回调（H8a/RN-3）：瞬断（true→false）复位 running + 收束在途气泡；重连清提示。
  const onConnState = useCallback((open: boolean) => {
    const was = connectedRef.current;
    connectedRef.current = open;
    setConnected(open);
    if (was && !open) {
      setRunning(false);
      setError("连接中断，正在重连…");
      setMessages((prev) => settleStreaming(prev));
    } else if (!was && open) {
      setError(null);
    }
  }, []);

  // 连接（会话 id 变更即重连到新会话 → 收到空 history → 清空消息流）。
  useEffect(() => {
    if (!conversationId) return;
    setMessages([]);
    setRunning(false);
    setError(null);
    connectedRef.current = false;
    const ws = agentWS(conversationId, handleEvent, onConnState);
    wsRef.current = ws;
    ws.connect();
    return () => {
      ws.close();
      wsRef.current = null;
    };
  }, [conversationId, handleEvent, onConnState]);

  const send = useCallback(
    (text: string) => {
      const trimmed = text.trim();
      const ws = wsRef.current;
      if (!ws || !trimmed || running) return;
      // 发送前连接守卫（H8c）：未连接则提示并拦截，避免乐观追加"幽灵消息"。
      if (!connected) {
        setError("未连接，无法发送，请稍候重连。");
        return;
      }
      lastUserTextRef.current = trimmed;
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
    [running, connected],
  );

  // 失败重试（H12）：重发上一条 user 文本（受 send 内连接 / running 守卫保护）。
  const retry = useCallback(() => {
    if (lastUserTextRef.current) send(lastUserTextRef.current);
  }, [send]);

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
        <div className="flex items-center justify-between gap-2 border-t border-[var(--border)] bg-[color-mix(in_srgb,var(--short)_10%,transparent)] px-3 py-1.5 text-[11px] text-[var(--short)]">
          <span className="min-w-0 flex-1 truncate">{error}</span>
          {lastUserTextRef.current && !running && (
            <button
              type="button"
              onClick={retry}
              className="shrink-0 rounded-[6px] border border-[var(--border)] px-2 py-0.5 text-[var(--text)] transition-colors duration-150 ease-smooth hover:bg-[var(--surface-2)]"
            >
              重试
            </button>
          )}
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
