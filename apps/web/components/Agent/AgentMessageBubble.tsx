"use client";

import clsx from "clsx";
import type { ChatMessage } from "@/lib/agentTypes";
import ToolCallCard from "./ToolCallCard";

export default function AgentMessageBubble({ message }: { message: ChatMessage }) {
  const isUser = message.role === "user";
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
        </div>
      )}
      {message.toolCalls.length > 0 && (
        <div className="flex w-full flex-col gap-1.5">
          {message.toolCalls.map((call) => (
            <ToolCallCard key={call.id} call={call} />
          ))}
        </div>
      )}
    </div>
  );
}
