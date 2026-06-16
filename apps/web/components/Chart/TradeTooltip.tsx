"use client";

import { fmtNum, fmtPrice, fmtQty, fmtTime, signed } from "@/lib/format";
import { tradeToneForEvent } from "@/lib/tradeTrails";
import type { TradeMarkerEvent } from "@/lib/types";

interface TradeTooltipProps {
  events: TradeMarkerEvent[];
  x: number;
  y: number;
}

const KIND_LABEL: Record<TradeMarkerEvent["kind"], string> = {
  "entry-long": "BUY",
  "entry-short": "SELL",
  close: "CLOSE",
  "orphan-close": "CLOSE",
};

export default function TradeTooltip({ events, x, y }: TradeTooltipProps) {
  if (events.length === 0) return null;

  return (
    <div
      className="pointer-events-none absolute z-30 w-[280px] rounded-[8px] border border-[var(--border)] bg-[var(--surface)] p-2 text-[11px] text-[var(--text)] shadow-soft"
      style={{
        left: `min(max(${x + 12}px, 8px), calc(100% - 288px))`,
        top: `min(max(${y + 12}px, 8px), calc(100% - 190px))`,
      }}
    >
      <div className="mb-1.5 flex items-center justify-between">
        <span className="font-semibold">Backtest trades</span>
        <span className="tabular text-[var(--muted)]">{fmtTime(events[0].trade.ts)}</span>
      </div>
      <div className="space-y-1.5">
        {events.map((event) => (
          <TooltipRow key={event.id} event={event} />
        ))}
      </div>
    </div>
  );
}

function TooltipRow({ event }: { event: TradeMarkerEvent }) {
  const tone = tradeToneForEvent(event);
  const color =
    tone === "long"
      ? "var(--long)"
      : tone === "short"
        ? "var(--short)"
        : "var(--muted)";

  return (
    <div className="rounded-[6px] bg-[var(--surface-2)] px-2 py-1.5">
      <div className="flex items-center justify-between gap-2">
        <span className="font-semibold" style={{ color }}>
          {KIND_LABEL[event.kind]}
        </span>
        <span className="tabular text-[var(--muted)]">
          px {fmtPrice(event.trade.price)} / qty {fmtQty(event.trade.qty)}
        </span>
      </div>
      <div className="mt-1 grid grid-cols-2 gap-x-3 gap-y-0.5 tabular text-[var(--muted)]">
        <span>fee {fmtNum(event.trade.fee, 4)}</span>
        <span>pos {fmtQty(event.trade.positionAfter)}</span>
        <span style={{ color }}>
          pnl {event.trade.side === "close" ? signed(event.trade.pnl) : "-"}
        </span>
        <span className="truncate">
          {event.trade.reason || (event.kind === "orphan-close" ? "orphan close" : "open")}
        </span>
      </div>
    </div>
  );
}
