"use client";

import { useState } from "react";
import clsx from "clsx";
import type { ToolCall } from "@/lib/agentTypes";

// 工具名 → 友好中文标签 + 图标（缺省回落原 name）。
const TOOL_LABELS: Record<string, { label: string; icon: string }> = {
  list_instruments: { label: "查询标的列表", icon: "📋" },
  get_market_summary: { label: "行情快照", icon: "📈" },
  get_candles: { label: "拉取 K 线", icon: "🕯️" },
  show_chart: { label: "切换主图", icon: "🖼️" },
  list_strategies: { label: "策略列表", icon: "🧩" },
  run_backtest: { label: "运行回测", icon: "⚙️" },
  compare_strategies: { label: "策略对比", icon: "⚖️" },
  label_market_regime: { label: "市场体制分析", icon: "📍" },
  list_runs: { label: "运行历史", icon: "🗂️" },
  get_run_metrics: { label: "绩效指标", icon: "📊" },
  get_run_trades: { label: "成交明细", icon: "🧾" },
  get_run_equity: { label: "净值曲线", icon: "💹" },
};

function labelFor(name: string): { label: string; icon: string } {
  return TOOL_LABELS[name] ?? { label: name, icon: "🔧" };
}

export default function ToolCallCard({ call }: { call: ToolCall }) {
  const [open, setOpen] = useState(false);
  const { label, icon } = labelFor(call.name);
  const status = !call.done ? "running" : call.ok ? "ok" : "error";
  const hasInput = Object.keys(call.input || {}).length > 0;

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
            status === "ok" && "bg-[color-mix(in_srgb,var(--long)_16%,transparent)] text-[var(--long)]",
            status === "error" && "bg-[color-mix(in_srgb,var(--short)_16%,transparent)] text-[var(--short)]",
          )}
        >
          {status === "running" ? "执行中…" : status === "ok" ? "完成" : "失败"}
        </span>
        <span className="text-[var(--muted)]">{open ? "▾" : "▸"}</span>
      </button>
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
