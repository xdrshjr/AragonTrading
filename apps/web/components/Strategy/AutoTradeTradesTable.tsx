"use client";

import clsx from "clsx";
import { useState } from "react";
import type { Trade } from "@/lib/types";
import { useAppStore } from "@/lib/store";
import { fmtPrice, fmtQty, fmtTime, signed } from "@/lib/format";
import { Badge } from "@/components/ui";
import { explainKindLabel, explainKindTone } from "@/lib/tradeExplain";
import { TRADES_MAX } from "@/lib/wireCaps";
import TradeExplainDetail from "./TradeExplainDetail";

/**
 * 自动交易「成交记录」表（auto-trade-explainability §三）。
 *
 * 每一笔成交可逐行展开查看结构化触发原因（HCI：整行可点 + 右侧 chevron + 键盘可达
 * `button`/`aria-expanded`，展开用渐进披露）。数据复用既有 `liveAutoTrades` store（单一数据源，
 * 与图表 markers / 事件流职责区分）。表 / 详情 / 因子图独立成件，本表仅做行渲染与展开编排。
 */
const SIDE_LABEL: Record<Trade["side"], string> = { long: "开多", short: "开空", close: "平仓" };
const SIDE_TONE: Record<Trade["side"], "long" | "short" | "neutral"> = {
  long: "long",
  short: "short",
  close: "neutral",
};

function tradeKey(trade: Trade, index: number): string {
  return trade.id != null ? `id-${trade.id}` : `${trade.ts}-${trade.side}-${trade.price}-${index}`;
}

export default function AutoTradeTradesTable() {
  const trades = useAppStore((s) => s.liveAutoTrades);
  const [expanded, setExpanded] = useState<Set<string>>(() => new Set());

  if (trades.length === 0) {
    return (
      <div className="px-4 py-6 text-center text-xs text-[var(--muted)]">
        暂无自动交易成交。开启自动交易后，每笔买卖 / 平仓将在此显示，可逐行展开查看触发原因。
      </div>
    );
  }

  // 本表自持数据源（`liveAutoTrades`），故截断在这里**本地可判定**——store 的封顶与这个判据
  // 共用同一个 `TRADES_MAX`，不需要 wire 上多一个没有第二个读点的 `tradesTotal` 字段。
  const capReached = trades.length >= TRADES_MAX;
  const ordered = trades.slice().sort((a, b) => b.ts - a.ts);
  const toggle = (key: string) =>
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });

  return (
    <section className="flex flex-col" aria-label="自动交易成交记录">
      <div className="flex items-center justify-between px-4 pb-1 pt-3">
        <h3 className="text-xs font-semibold text-[var(--text)]">自动交易成交记录</h3>
        <span className="text-[10px] text-[var(--muted)]">共 {trades.length} 笔 · 点击展开查看原因</span>
      </div>
      <div className="max-h-[360px] overflow-auto px-2">
        <ul className="flex flex-col">
          {ordered.map((trade, i) => {
            const key = tradeKey(trade, i);
            return (
              <TradeRow
                key={key}
                trade={trade}
                open={expanded.has(key)}
                onToggle={() => toggle(key)}
              />
            );
          })}
        </ul>
      </div>
      {capReached && (
        // 「看不到全部」必须是被说出来的事实（D4）：否则用户会以为自己的成交丢了。
        <p className="px-4 pb-3 pt-1 text-[10px] text-[var(--muted)]">
          实时成交仅保留最近 {TRADES_MAX} 笔，更早的记录见运行历史。
        </p>
      )}
    </section>
  );
}

function TradeRow({
  trade,
  open,
  onToggle,
}: {
  trade: Trade;
  open: boolean;
  onToggle: () => void;
}) {
  const kind = trade.explain?.kind;
  const kindTone = explainKindTone(kind);
  return (
    <li className="border-b border-[var(--border)] last:border-b-0">
      <button
        type="button"
        aria-expanded={open}
        onClick={onToggle}
        className={clsx(
          "grid w-full grid-cols-[auto_4.5rem_1fr_1fr_1fr_auto] items-center gap-2 px-2 py-2 text-left text-xs",
          "hover:bg-[var(--surface-2)]/50 focus-visible:outline focus-visible:outline-2 focus-visible:outline-[var(--accent)]",
          trade.seeded && "bg-[var(--surface-2)]/40",
        )}
      >
        <Chevron open={open} />
        <span className="flex flex-col gap-0.5">
          <Badge tone={SIDE_TONE[trade.side]}>{SIDE_LABEL[trade.side]}</Badge>
        </span>
        <span className="flex min-w-0 flex-col gap-0.5">
          <span className="truncate text-[var(--text)]">{trade.reason || "—"}</span>
          <span className="flex items-center gap-1">
            <span
              className={clsx(
                "rounded px-1 py-px text-[9px]",
                kindTone === "warn" && "bg-[var(--accent-soft)] text-[var(--accent-ink)]",
                kindTone !== "warn" && "bg-[var(--surface-2)] text-[var(--muted)]",
              )}
            >
              {explainKindLabel(kind)}
            </span>
            {trade.seeded && (
              <span
                title="基于近端真实历史的预热回填，非刚发生的实时成交"
                className="rounded-full border border-[var(--border)] px-1.5 py-px text-[9px] text-[var(--muted)]"
              >
                历史预热
              </span>
            )}
          </span>
        </span>
        {/* 轴 D：成交表读真实墙钟 `wallTs`（`ts` 已是 bar 对齐秒，同根多笔会共享同一个时刻）。 */}
        <span className="text-right tabular text-[var(--muted)]">
          {fmtTime(trade.wallTs ?? trade.ts, false)}
        </span>
        <span className="text-right tabular">{fmtPrice(trade.price)}</span>
        <span
          className="text-right tabular font-medium"
          style={{
            color: trade.pnl > 0 ? "var(--long-ink)" : trade.pnl < 0 ? "var(--short-ink)" : "var(--muted)",
          }}
        >
          {trade.side === "close" ? signed(trade.pnl) : fmtQty(trade.qty)}
        </span>
      </button>
      {open && (
        <div className="px-2 pb-3 pt-0.5">
          <TradeExplainDetail trade={trade} />
        </div>
      )}
    </li>
  );
}

function Chevron({ open }: { open: boolean }) {
  return (
    <svg
      width="12"
      height="12"
      viewBox="0 0 12 12"
      className={clsx("text-[var(--muted)] transition-transform", open && "rotate-90")}
      aria-hidden
    >
      <path d="M4 2.5L8 6l-4 3.5" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}
