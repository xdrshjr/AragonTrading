"use client";

import { useState } from "react";
import clsx from "clsx";
import type { AgentUsage, ChatMessage } from "@/lib/agentTypes";
import { renderMarkdown } from "@/lib/markdown";
import ToolCallCard from "./ToolCallCard";

// 极简 token 用量行（H5/H12，复用 --muted；字段缺失即省略）。
function usageLabel(usage?: AgentUsage): string | null {
  if (!usage) return null;
  const parts: string[] = [];
  if (usage.inputTokens != null) parts.push(`入 ${usage.inputTokens}`);
  if (usage.outputTokens != null) parts.push(`出 ${usage.outputTokens}`);
  return parts.length ? `token · ${parts.join(" / ")}` : null;
}

// 一键复制（RS-10）：复制原始 markdown 文本（非渲染后 DOM）；复制后 1.5s 显示"已复制"反馈。
function CopyButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);
  const onCopy = async () => {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      /* 剪贴板不可用（非安全上下文等）：静默，非关键路径 */
    }
  };
  return (
    <button
      type="button"
      onClick={onCopy}
      aria-label="复制回复"
      className="absolute right-1 top-1 rounded-[6px] border border-[var(--border)] bg-[var(--surface)] px-1.5 py-0.5 text-[10px] text-[var(--muted)] opacity-0 transition-opacity duration-150 ease-smooth hover:text-[var(--text)] focus-visible:opacity-100 group-hover:opacity-100"
    >
      {copied ? "已复制" : "复制"}
    </button>
  );
}

// 气泡正文：用户气泡 / 流式途中走纯文本（保留换行、半截围栏不闪烁，R7）；
// assistant 完成轮走安全 markdown 富文本（RS-8）。
function MessageBody({ message, isUser }: { message: ChatMessage; isUser: boolean }) {
  if (isUser || message.streaming) {
    return <span className="whitespace-pre-wrap">{message.text}</span>;
  }
  return <div className="flex flex-col">{renderMarkdown(message.text)}</div>;
}

export default function AgentMessageBubble({ message }: { message: ChatMessage }) {
  const isUser = message.role === "user";
  const tokens = isUser ? null : usageLabel(message.usage);
  const canCopy = !isUser && !!message.text && !message.streaming;
  return (
    <div className={clsx("flex flex-col gap-1.5", isUser ? "items-end" : "items-stretch")}>
      {(message.text || message.streaming || !message.toolCalls.length) && (
        <div
          className={clsx(
            "group relative max-w-[92%] break-words rounded-[12px] px-3 py-2 text-[13px] leading-relaxed",
            isUser
              ? "self-end bg-[var(--accent)] text-white"
              : "self-start bg-[var(--surface-2)] text-[var(--text)]",
          )}
        >
          <MessageBody message={message} isUser={isUser} />
          {message.streaming && !message.text && (
            <span className="text-[var(--muted)]">正在思考…</span>
          )}
          {message.streaming && message.text && (
            <span className="ml-0.5 inline-block h-3.5 w-[2px] translate-y-0.5 animate-pulse bg-current align-middle" />
          )}
          {message.interrupted && (
            <span className="ml-1 text-[10px] text-[var(--muted)]">· 已中断</span>
          )}
          {canCopy && <CopyButton text={message.text} />}
        </div>
      )}
      {message.toolCalls.length > 0 && (
        <div className="flex w-full flex-col gap-1.5">
          {message.toolCalls.map((call) => (
            <ToolCallCard key={call.id} call={call} />
          ))}
        </div>
      )}
      {tokens && (
        <span className="self-start px-0.5 text-[10px] text-[var(--muted)]">{tokens}</span>
      )}
    </div>
  );
}
