"use client";

import { useState } from "react";
import clsx from "clsx";
import { labelFor } from "@/lib/agentToolMeta";
import type { ToolCall } from "@/lib/agentTypes";
import AccountSnapshotCard from "./trading/AccountSnapshotCard";
import AutoTradeContextCard from "./trading/AutoTradeContextCard";
import TradeIntentCard from "./trading/TradeIntentCard";

export default function ToolCallCard({ call }: { call: ToolCall }) {
  const [open, setOpen] = useState(false);
  const { label, icon } = labelFor(call.name);
  const status = !call.done ? "running" : call.ok ? "ok" : "error";
  const hasInput = Object.keys(call.input || {}).length > 0;

  if (call.payload?.type === "trade_intent") {
    return <TradeIntentCard intent={call.payload.intent} />;
  }
  if (call.payload?.type === "account_snapshot") {
    return <AccountSnapshotCard snapshot={call.payload.snapshot} toolName={call.name} />;
  }
  if (call.payload?.type === "auto_trade_context") {
    return <AutoTradeContextCard context={call.payload.context} />;
  }

  return (
    <div className="overflow-hidden rounded-[10px] border border-[var(--border)] bg-[var(--surface)] text-xs">
      <button
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center gap-2 px-2.5 py-1.5 text-left transition-colors duration-150 ease-smooth hover:bg-[var(--surface-2)]"
      >
        <span className="text-sm leading-none">{icon}</span>
        <span className="font-medium text-[var(--text)]">{label}</span>
        <span
          className={clsx(
            "ml-auto inline-flex items-center gap-1 rounded-full px-1.5 py-0.5 text-[10px] font-medium",
            status === "running" && "bg-[var(--surface-2)] text-[var(--muted)]",
            status === "ok" && "bg-[color-mix(in_srgb,var(--long)_16%,transparent)] text-[var(--long-ink)]",
            status === "error" && "bg-[color-mix(in_srgb,var(--short)_16%,transparent)] text-[var(--short-ink)]",
          )}
        >
          {status === "running" ? "执行中…" : status === "ok" ? "完成" : "失败"}
        </span>
        <span className="text-[var(--muted)]">{open ? "▾" : "▸"}</span>
      </button>
      {call.code && call.done && !call.ok && call.summary && (
        <p role="alert" className="px-2.5 pb-2 text-[var(--short-ink)]">
          {call.summary}
        </p>
      )}
      {open && (
        <div className="border-t border-[var(--border)] px-2.5 py-2">
          {hasInput && (
            <pre className="mb-2 overflow-x-auto whitespace-pre-wrap break-all rounded bg-[var(--surface-2)] p-2 text-[10px] leading-relaxed text-[var(--muted)]">
              {JSON.stringify(call.input, null, 2)}
            </pre>
          )}
          {call.summary && (
            <p className="whitespace-pre-wrap break-words text-[11px] leading-relaxed text-[var(--text)]">
              {call.summary}
            </p>
          )}
          {!hasInput && !call.summary && (
            <p className="text-[11px] text-[var(--muted)]">无附加信息。</p>
          )}
        </div>
      )}
    </div>
  );
}
