"use client";

import { useEffect, useRef, useState } from "react";
import type { ChatMessage } from "@/lib/agentTypes";
import AgentMessageBubble from "./AgentMessageBubble";

// 距底阈值（px）：滚动距底小于此值即视为"贴底跟随"，否则视为用户上滚阅读历史。
const PIN_THRESHOLD = 48;

export default function AgentMessageList({ messages }: { messages: ChatMessage[] }) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const endRef = useRef<HTMLDivElement | null>(null);
  const [pinned, setPinned] = useState(true);

  // 流式增量到达即变化（每个 text_delta / 工具卡 / 流式态）。
  const signature = messages
    .map((m) => `${m.id}:${m.text.length}:${m.toolCalls.length}:${m.streaming}`)
    .join("|");

  // 贴底跟随但尊重用户上滚（RS-9）：仅当 pinned 才在增量到达时自动滚到底。
  useEffect(() => {
    if (pinned) endRef.current?.scrollIntoView({ block: "end" });
  }, [signature, pinned]);

  const onScroll = () => {
    const el = containerRef.current;
    if (!el) return;
    const distance = el.scrollHeight - el.scrollTop - el.clientHeight;
    setPinned(distance < PIN_THRESHOLD);
  };

  const jumpToLatest = () => {
    setPinned(true);
    endRef.current?.scrollIntoView({ block: "end", behavior: "smooth" });
  };

  return (
    <div className="relative h-full">
      <div ref={containerRef} onScroll={onScroll} className="h-full overflow-y-auto">
        {/* role=log + aria-live：屏幕阅读器可播报流式增量（RS-10）。 */}
        <div
          role="log"
          aria-live="polite"
          aria-relevant="additions text"
          className="flex flex-col gap-3 px-3 py-3"
        >
          {messages.map((m) => (
            <AgentMessageBubble key={m.id} message={m} />
          ))}
          <div ref={endRef} />
        </div>
      </div>
      {!pinned && (
        <button
          type="button"
          onClick={jumpToLatest}
          aria-label="回到最新"
          className="absolute bottom-3 left-1/2 z-10 -translate-x-1/2 rounded-full border border-[var(--border)] bg-[var(--surface)] px-3 py-1 text-[11px] text-[var(--text)] shadow-soft transition-colors duration-150 ease-smooth hover:bg-[var(--surface-2)]"
        >
          ↓ 回到最新
        </button>
      )}
    </div>
  );
}
