"use client";

import { useEffect, useRef } from "react";
import { Button } from "@/components/ui";

// 底部输入：多行 textarea（自适应高度）、Enter 发送 / Shift+Enter 换行、发送 / 停止切换。
export default function AgentComposer({
  value,
  onChange,
  onSend,
  onStop,
  running,
  disabled,
}: {
  value: string;
  onChange: (v: string) => void;
  onSend: () => void;
  onStop: () => void;
  running: boolean;
  disabled?: boolean;
}) {
  const ref = useRef<HTMLTextAreaElement | null>(null);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 140)}px`;
  }, [value]);

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      if (!running && value.trim()) onSend();
    }
  };

  return (
    <div
      data-testid="agent-composer"
      className="border-t border-[var(--border)] bg-[var(--surface)] p-2.5"
    >
      <div className="flex items-end gap-2 rounded-[12px] border border-[var(--border)] bg-[var(--surface-2)] px-2.5 py-1.5 focus-within:border-[var(--accent)]">
        <textarea
          ref={ref}
          rows={1}
          value={value}
          disabled={disabled}
          onChange={(e) => onChange(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder={disabled ? "AI 助手暂不可用" : "问点什么…（Enter 发送，Shift+Enter 换行）"}
          className="max-h-[140px] flex-1 resize-none bg-transparent text-[13px] leading-relaxed text-[var(--text)] outline-none placeholder:text-[var(--muted)] disabled:cursor-not-allowed"
        />
        {running ? (
          <Button variant="danger" size="sm" onClick={onStop} aria-label="停止">
            停止
          </Button>
        ) : (
          <Button
            variant="primary"
            size="sm"
            onClick={onSend}
            disabled={disabled || !value.trim()}
            aria-label="发送"
          >
            发送
          </Button>
        )}
      </div>
    </div>
  );
}
