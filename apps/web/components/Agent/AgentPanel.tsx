"use client";

import { type ReactNode, useEffect, useRef, useState } from "react";
import { Button } from "@/components/ui";
import { useAppStore } from "@/lib/store";
import { useAgentConversation } from "./useAgentConversation";
import AgentComposer from "./AgentComposer";
import AgentEmptyState from "./AgentEmptyState";
import AgentMessageList from "./AgentMessageList";
import AgentUnconfigured from "./AgentUnconfigured";
import AiTradeAgentTrace from "./AiTradeAgentTrace";
// 生命周期回执的提示条（notice-state-sync §7.1）：从本文件的内联函数抽出，好让它能被单测直接
// 渲染，也给判活谓词一个明确的落点（`data-testid` 随之由 ai-trade-lifecycle-notice 改名）。
import AiTradeNoticeBar from "./AiTradeNoticeBar";
import AiTradeStatsButton from "./AiTradeStatsButton";
import AiTradeToggle from "./AiTradeToggle";

type PanelView = "chat" | "aitrade";

export default function AgentPanel() {
  const {
    messages, config, connected, running, error, input, setInput,
    canRetry, send, stop, retry, clearContext, contextNotice,
  } = useAgentConversation();
  // AI 自动交易「决策执行轨迹」视图（ai-trade-multi-symbol-and-timeframe §4.2）：与交互式对话共存的分段
  // 视图。泛化为「分组」：右侧轨迹按聚焦标的展示各成员逐轮思考（AiTradeAgentTrace 内含标的段控）。
  const aiTradeGroup = useAppStore((s) => s.aiTradeGroup);
  const instId = useAppStore((s) => s.instId);
  const [panelView, setPanelView] = useState<PanelView>("chat");
  // 记忆「已自动切入过」的 sessionId：自动切换每会话仅一次，之后尊重用户手动来回切换（R7）。
  const autoSwitchedRef = useRef<string | null>(null);

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

  const unconfigured = config !== null && !config.configured;
  // RV-8：分组存在时 aitrade 视图优先于 unconfigured 空态渲染（后端 LLM 必已配置，否则分组无法启动）。
  const showTrace = panelView === "aitrade" && !!aiTradeGroup;
  // 分组聚合：任一成员运行中即视为运行（tab 上仅以脉冲点表征运行状态，不再显示具体轮数）。
  const aiTradeMembers = aiTradeGroup ? Object.values(aiTradeGroup.members) : [];
  const aiTradeAnyRunning = aiTradeMembers.some((m) => m.session?.state === "running");

  return (
    <div className="flex h-full min-h-0 flex-col">
      {/* 横栏 A：身份 + 会话动作。行级 whitespace-nowrap 沿 DOM 树继承到每一个按钮文案，
          中文因此不再逐字断行；宽度压力由**唯一**一处 min-w-0 + truncate（模型名）吸收。 */}
      <header className="flex items-center justify-between gap-2 whitespace-nowrap border-b border-[var(--border)] px-3 py-2">
        <div className="flex min-w-0 items-center gap-2">
          <span
            className={`h-1.5 w-1.5 shrink-0 rounded-full ${connected ? "bg-[var(--long)]" : "bg-[var(--muted)]"}`}
            title={connected ? "已连接" : "连接中…"}
          />
          <span className="shrink-0 text-xs font-semibold text-[var(--text)]">AI 助手</span>
          {config?.model && (
            <span className="truncate text-[10px] text-[var(--muted)]" title={config.model}>
              {config.model}
            </span>
          )}
        </div>
        <div className="flex shrink-0 items-center gap-1.5">
          <Button variant="ghost" size="sm" onClick={() => {
            if (clearContext()) setPanelView("chat");
          }} aria-label="清空上下文" disabled={running}
            title={running ? "请先停止当前回答，再清空上下文"
              : "开始空白对话；历史记录保留，自动交易不受影响"}>
            <svg width="12" height="12" viewBox="0 0 24 24"
              fill="none" stroke="currentColor" strokeWidth="1.6"
              aria-hidden="true">
              <path d="m15 3 6 6-11 12H6l-4-4L15 3Z M8 11l6 6M10 21h12" />
            </svg>
            清空上下文
          </Button>
          <AiTradeStatsButton />
        </div>
      </header>

      {!connected && !error && (
        <p role="status" className="px-3 py-2 text-xs text-[var(--muted)]">
          正在连接…
        </p>
      )}
      {contextNotice && (
        <p role="status" className="px-3 py-2 text-xs text-[var(--muted)]">
          {contextNotice}
        </p>
      )}
      {running && (showTrace || unconfigured) && (
        <div className="flex shrink-0 items-center gap-2 px-3 py-2 text-xs">
          <span>对话仍在生成</span>
          <Button variant="danger" size="sm" onClick={stop}>
            停止当前回答
          </Button>
        </div>
      )}
      <AiTradeNoticeBar />

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
          {canRetry && (
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

      {/* 横栏 B：AI 自动交易控制条。常驻贴在输入框正上方；「执行轨迹」视图下输入框不渲染时它
          自然落到面板底缘——两个视图里位置一致，用户不必重新寻找同一个开关。刻意不做条件渲染：
          随运行态出现 / 消失会在开启瞬间把整条消息流顶动一整格。排在错误条**之后**，是因为错误
          条是瞬态通知，只有常驻外壳才有资格占住「紧贴输入框」这个位置。 */}
      <div
        role="group"
        aria-label="AI 自动交易控制"
        data-testid="ai-trade-control-bar"
        className="shrink-0 whitespace-nowrap border-t border-[var(--border)] bg-[var(--surface)] px-3 py-1.5"
      >
        <AiTradeToggle />
      </div>

      {!unconfigured && !showTrace && (
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

/** 视图分段按钮（对话 / 执行轨迹）：置于标题行下方子行，与底部控制条的启停开关分居两端（RV-3）。 */
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
