"use client";

import { type ReactNode, useCallback, useEffect, useRef, useState } from "react";
import { Button } from "@/components/ui";
import { applyShowChart } from "@/lib/agentActions";
import { agentApi } from "@/lib/agentApi";
import { agentWS } from "@/lib/agentWs";
import {
  appendUserMessage,
  dropStreaming,
  reconcileHistory,
  reduceAgentEvent,
} from "@/lib/agentReducer";
import type {
  AgentServerEvent,
  AgentUiContext,
  ChatMessage,
  ResumedReason,
  UiActionStore,
} from "@/lib/agentTypes";
import { useAppStore } from "@/lib/store";
import type { ReconnectingWS, WsEvent } from "@/lib/ws";
import AgentComposer from "./AgentComposer";
import AgentEmptyState from "./AgentEmptyState";
import AgentMessageList from "./AgentMessageList";
import AgentUnconfigured from "./AgentUnconfigured";
import AiTradeAgentTrace from "./AiTradeAgentTrace";
import AiTradeStatsButton from "./AiTradeStatsButton";
import AiTradeToggle from "./AiTradeToggle";

type PanelView = "chat" | "aitrade";

interface AgentConfig {
  configured: boolean;
  provider: string;
  model: string;
}

// 单条用户输入软上限（RS-14）：与后端 _MAX_USER_TEXT_CHARS 一致，避免撑爆模型上下文。
const MAX_INPUT_CHARS = 16000;

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
  // AI 自动交易「决策执行轨迹」视图（ai-trade-multi-symbol-and-timeframe §4.2）：与交互式对话共存的分段
  // 视图。泛化为「分组」：右侧轨迹按聚焦标的展示各成员逐轮思考（AiTradeAgentTrace 内含标的段控）。
  const aiTradeGroup = useAppStore((s) => s.aiTradeGroup);
  const instId = useAppStore((s) => s.instId);
  const [panelView, setPanelView] = useState<PanelView>("chat");
  // 记忆「已自动切入过」的 sessionId：自动切换每会话仅一次，之后尊重用户手动来回切换（R7）。
  const autoSwitchedRef = useRef<string | null>(null);

  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [config, setConfig] = useState<AgentConfig | null>(null);
  const [connected, setConnected] = useState(false);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [input, setInput] = useState("");
  const wsRef = useRef<ReconnectingWS | null>(null);
  // 连接态变迁探针（H8a）：检测 true→false 瞬断 / false→true 重连。
  const connectedRef = useRef(false);
  // 最近一条 user 文本（H12 失败重试用）。
  const lastUserTextRef = useRef<string>("");
  // 续流对账锚点（RS-1/RS-5）：最近收到的最大 seq（仅 run 事件推进，heartbeat 不推进）。
  const lastSeqRef = useRef(0);
  // 是否存在在途 streaming 气泡（onConnState 据此决定瞬断保留与重连发 resume，RS-11/RS-12）。
  const streamingRef = useRef(false);
  // 缓冲空洞待 REST 兜底重建标记（RS-11 gap 路径）：下一个 done 时拉权威历史。
  const pendingGapReconcileRef = useRef(false);
  // 当前会话 id 镜像（供稳定的 REST 兜底闭包，不触发 effect 重连）。
  const convIdRef = useRef(conversationId);

  useEffect(() => {
    convIdRef.current = conversationId;
  }, [conversationId]);

  // 镜像在途流式态：供 onConnState（稳定闭包）读取最新值。
  useEffect(() => {
    streamingRef.current = messages.some((m) => m.streaming);
  }, [messages]);

  // 首次挂载时若无会话 id，生成一个（SSR 安全：水合后于客户端执行）。
  useEffect(() => {
    if (!conversationId) newConversation();
  }, [conversationId, newConversation]);

  // AI 自动交易分组出现→自动切入「执行轨迹」（每 groupId 仅一次）；清空→回落「对话」（R7）。
  const aiTradeGroupId = aiTradeGroup?.groupId ?? null;
  useEffect(() => {
    if (aiTradeGroupId) {
      if (autoSwitchedRef.current !== aiTradeGroupId) {
        autoSwitchedRef.current = aiTradeGroupId;
        setPanelView("aitrade");
      }
    } else {
      autoSwitchedRef.current = null;
      setPanelView("chat");
    }
  }, [aiTradeGroupId]);

  // 缓冲空洞兜底（RS-11 gap）：经既有 REST 拉权威历史重建（零新端点，最终一致）。
  const reconcileViaRest = useCallback(async () => {
    const id = convIdRef.current;
    if (!id) return;
    try {
      const msgs = await agentApi.messages(id);
      setMessages((prev) => reconcileHistory(prev, msgs));
    } catch {
      /* REST 兜底失败：静默，下次 ready+history 仍会重建（最终一致） */
    }
  }, []);

  // resumed 控制事件分流（RN-1）：ok=true 保留续流；ok=false 按 reason 丢弃在途 uuid 气泡。
  const handleResumed = useCallback(
    (data: { ok: boolean; reason?: ResumedReason }) => {
      if (data.ok) return; // 已重放并转实时续流：保留在途气泡，无需动作
      setMessages((prev) => dropStreaming(prev)); // DB history 权威，丢弃在途避免重复（RN-1）
      setError(null);
      if (data.reason === "gap") {
        pendingGapReconcileRef.current = true; // run 仍在跑：等下一个 done 再 REST 重建
      } else {
        setRunning(false); // completed / cancelled / none：run 已终结，history 已权威重建
      }
    },
    [],
  );

  const handleDone = useCallback(() => {
    setRunning(false);
    if (pendingGapReconcileRef.current) {
      pendingGapReconcileRef.current = false;
      void reconcileViaRest();
    }
  }, [reconcileViaRest]);

  const handleEvent = useCallback(
    (raw: WsEvent) => {
      const ev = raw as AgentServerEvent;
      const seq = (raw as { seq?: number }).seq;
      // lastSeq 仅由带 seq 的 run 事件推进（heartbeat/resumed/ready/history 无 seq，RS-1/RN-2）。
      if (typeof seq === "number" && seq > lastSeqRef.current) lastSeqRef.current = seq;
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
          if (ev.data.action === "show_factor") {
            const st = useAppStore.getState();
            if (ev.data.instId) st.setInst(ev.data.instId);
            if (ev.data.bar) st.setBar(ev.data.bar);
            st.setFactorOverlay({
              kind: ev.data.factorSetId != null ? "score" : "factor",
              factorKey: ev.data.factorKey,
              factorSetId: ev.data.factorSetId,
            });
            st.setFactorLabSection(ev.data.factorSetId != null ? "builder" : "evaluate");
            st.setActiveView("factor");
          } else {
            applyShowChart(ev.data, uiActionStore());
          }
          break;
        case "resumed":
          handleResumed(ev.data);
          break;
        case "heartbeat":
          // 纯活性帧（RS-7/RN-2）：仅表征连接存活，不入消息流、不推进 lastSeq。
          break;
        case "done":
          handleDone();
          break;
        case "error":
          setRunning(false);
          setError(ev.data.message);
          break;
      }
    },
    [handleResumed, handleDone],
  );

  // 连接态变迁回调（RS-11/RS-12）：瞬断保留在途气泡（"回答已保留"），重连发 resume 续流。
  const onConnState = useCallback((open: boolean) => {
    const was = connectedRef.current;
    connectedRef.current = open;
    setConnected(open);
    if (was && !open) {
      // 瞬断：有在途回答则保留其流式态（不 settleStreaming），仅置轻状态；否则复位 running。
      if (streamingRef.current) {
        setError("连接中断，正在重连…（回答已保留）");
      } else {
        setRunning(false);
        setError("连接中断，正在重连…");
      }
    } else if (!was && open) {
      setError(null);
      // 重连成功：仅当本地确有在途 streaming 气泡才发 resume{lastSeq}（避免协议噪音，RS-12）。
      if (streamingRef.current) {
        wsRef.current?.send({ type: "resume", data: { lastSeq: lastSeqRef.current } });
      }
    }
  }, []);

  // 连接（会话 id 变更即重连到新会话 → 收到空 history → 清空消息流）。
  useEffect(() => {
    if (!conversationId) return;
    setMessages([]);
    setRunning(false);
    setError(null);
    connectedRef.current = false;
    lastSeqRef.current = 0;
    pendingGapReconcileRef.current = false;
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
      // 输入长度护栏（RS-14）：超限即提示并拦截（后端另有防御性截断）。
      if (trimmed.length > MAX_INPUT_CHARS) {
        setError("消息过长，请精简后再发送。");
        return;
      }
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

  // 一键清除上下文：换新会话 id（既有 effect 重置消息流 + 重连空会话）并 best-effort 删旧会话行。
  // 运行中禁用（按钮亦 disabled）：避免孤儿 run 把消息回写已删除会话、复活旧行（R2）。
  const clearContext = useCallback(() => {
    if (running) return;
    const old = convIdRef.current;
    newConversation();
    if (old) void agentApi.clear(old).catch(() => {}); // best-effort purge：失败仅旧行残留（R3）
  }, [running, newConversation]);

  const unconfigured = config !== null && !config.configured;
  // RV-8：分组存在时 aitrade 视图优先于 unconfigured 空态渲染（后端 LLM 必已配置，否则分组无法启动）。
  const showTrace = panelView === "aitrade" && !!aiTradeGroup;
  // 分组聚合：任一成员运行中即视为运行（tab 上仅以脉冲点表征运行状态，不再显示具体轮数）。
  const aiTradeMembers = aiTradeGroup ? Object.values(aiTradeGroup.members) : [];
  const aiTradeAnyRunning = aiTradeMembers.some((m) => m.session?.state === "running");

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
        <div className="flex items-center gap-2">
          <Button variant="ghost" size="sm" onClick={newConversation} aria-label="新对话">
            新对话
          </Button>
          <AiTradeStatsButton />
          <AiTradeToggle />
        </div>
      </header>

      <AiTradeLifecycleNotice />

      {aiTradeGroup && (
        <div
          role="tablist"
          aria-label="AI 助手视图"
          className="flex items-center gap-1 border-b border-[var(--border)] px-3 py-1.5"
        >
          <ViewTab active={panelView === "chat"} onClick={() => setPanelView("chat")}>
            对话
          </ViewTab>
          <ViewTab active={panelView === "aitrade"} onClick={() => setPanelView("aitrade")}>
            <span className="flex items-center gap-1.5">
              执行轨迹
              {aiTradeAnyRunning && (
                <span className="inline-block h-1.5 w-1.5 animate-pulse rounded-full bg-[var(--long)]" />
              )}
            </span>
          </ViewTab>
        </div>
      )}

      <div className={`min-h-0 flex-1 ${showTrace ? "" : "overflow-y-auto"}`}>
        {showTrace ? (
          <AiTradeAgentTrace group={aiTradeGroup} activeInstId={instId} />
        ) : unconfigured ? (
          <AgentUnconfigured provider={config.provider} model={config.model} />
        ) : messages.length === 0 ? (
          <AgentEmptyState onPick={send} />
        ) : (
          <AgentMessageList messages={messages} />
        )}
      </div>

      {error && !showTrace && (
        <div className="flex items-center justify-between gap-2 border-t border-[var(--border)] bg-[color-mix(in_srgb,var(--short)_10%,transparent)] px-3 py-1.5 text-[11px] text-[var(--short-ink)]">
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

      {!unconfigured && !showTrace && (
        <AgentComposer
          value={input}
          onChange={setInput}
          onSend={() => send(input)}
          onStop={stop}
          onClear={clearContext}
          running={running}
          disabled={!connected}
        />
      )}
    </div>
  );
}

/**
 * AI 自动交易生命周期回执的**持久**提示（ai-trade-pause-resume §3.10 / §5.4）。
 *
 * 两个来源都是后端单点生成的判定性事实：暂停回执的 `notice`（含「平仓其实没成功」「此刻没有任何
 * 持久化兜底」）与结束回执的 `orphanPositions`（「这些仓从此没人管了」）。**刻意不做成 toast**：
 * 这两句话的有效期是「直到用户真的去处理了那笔仓」，而不是三秒。挂在 AgentPanel 头部之下是因为
 * 结束之后 AI 面板整块会消失，只有这里在两种态下都还在。
 */
function AiTradeLifecycleNotice() {
  const notice = useAppStore((s) => s.aiTradeLifecycleNotice);
  const setNotice = useAppStore((s) => s.setAiTradeLifecycleNotice);
  if (!notice) return null;
  return (
    <div
      role="status"
      data-testid="ai-trade-lifecycle-notice"
      className="flex items-start gap-2 border-b border-[var(--accent)] bg-[var(--accent-soft)] px-3 py-1.5"
    >
      <span className="flex-1 text-[10px] leading-relaxed text-[var(--accent-ink)]">{notice}</span>
      <button
        type="button"
        aria-label="关闭提示"
        onClick={() => setNotice(null)}
        className="shrink-0 text-[10px] text-[var(--muted)] transition-colors hover:text-[var(--text)]"
      >
        知道了
      </button>
    </div>
  );
}

/** 视图分段按钮（对话 / 执行轨迹）：置于标题行下方子行，与既有 AiTradeToggle 启停开关区分（RV-3）。 */
function ViewTab({
  active,
  onClick,
  children,
}: {
  active: boolean;
  onClick: () => void;
  children: ReactNode;
}) {
  return (
    <button
      type="button"
      role="tab"
      aria-selected={active}
      onClick={onClick}
      className={`rounded-[7px] px-2 py-1 text-[11px] font-medium transition-colors duration-150 ease-smooth ${
        active
          ? "bg-[var(--surface-2)] text-[var(--text)]"
          : "text-[var(--muted)] hover:text-[var(--text)]"
      }`}
    >
      {children}
    </button>
  );
}
