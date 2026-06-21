"use client";

import clsx from "clsx";
import type { AgentUsage, ChatMessage } from "@/lib/agentTypes";
import ToolCallCard from "./ToolCallCard";

// 极简 token 用量行（H5/H12，复用 --muted；字段缺失即省略）。
function usageLabel(usage?: AgentUsage): string | null {
  if (!usage) return null;
  const parts: string[] = [];
  if (usage.inputTokens != null) parts.push(`入 ${usage.inputTokens}`);
  if (usage.outputTokens != null) parts.push(`出 ${usage.outputTokens}`);
  return parts.length ? `token · ${parts.join(" / ")}` : null;
}

export default function AgentMessageBubble({ message }: { message: ChatMessage }) {
  const isUser = message.role === "user";
  const tokens = isUser ? null : usageLabel(message.usage);
  return (
    <div className={clsx("flex flex-col gap-1.5", isUser ? "items-end" : "items-stretch")}>
      {(message.text || message.streaming || !message.toolCalls.length) && (
        <div
          className={clsx(
            "max-w-[92%] whitespace-pre-wrap break-words rounded-[12px] px-3 py-2 text-[13px] leading-relaxed",
            isUser
              ? "self-end bg-[var(--accent)] text-white"
              : "self-start bg-[var(--surface-2)] text-[var(--text)]",
          )}
        >
          {message.text}
          {message.streaming && !message.text && (
            <span className="text-[var(--muted)]">正在思考…</span>
          )}
          {message.streaming && message.text && (
            <span className="ml-0.5 inline-block h-3.5 w-[2px] translate-y-0.5 animate-pulse bg-current align-middle" />
          )}
          {message.interrupted && (
            <span className="ml-1 text-[10px] text-[var(--muted)]">· 已中断</span>
          )}
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
