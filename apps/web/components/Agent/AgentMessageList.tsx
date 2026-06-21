"use client";

import { useEffect, useRef } from "react";
import type { ChatMessage } from "@/lib/agentTypes";
import AgentMessageBubble from "./AgentMessageBubble";

export default function AgentMessageList({ messages }: { messages: ChatMessage[] }) {
  const endRef = useRef<HTMLDivElement | null>(null);
  // 新消息 / 流式增量到达即滚动到底（紧贴最新内容，符合对话直觉）。
  const signature = messages
    .map((m) => `${m.id}:${m.text.length}:${m.toolCalls.length}:${m.streaming}`)
    .join("|");
  useEffect(() => {
    endRef.current?.scrollIntoView({ block: "end" });
  }, [signature]);

  return (
    <div className="flex flex-col gap-3 px-3 py-3">
      {messages.map((m) => (
        <AgentMessageBubble key={m.id} message={m} />
      ))}
      <div ref={endRef} />
    </div>
  );
}
