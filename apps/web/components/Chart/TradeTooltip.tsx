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

function formatRiskPct(value: number | null | undefined): string | null {
  if (value === null || value === undefined || !Number.isFinite(value)) {
    return null;
  }
  return `${(value * 100).toFixed(2)}%`;
}

function formatRiskLine(
  label: string,
  pct: number | null | undefined,
  price: number | null | undefined,
): string {
  const pctText = formatRiskPct(pct);
  const hasPrice = price !== null && price !== undefined && Number.isFinite(price);
  if (pctText && hasPrice) return `${label} ${pctText} @ ${fmtPrice(price)}`;
  if (hasPrice) return `${label} ${fmtPrice(price)}`;
  if (pctText) return `${label} ${pctText}`;
  return `${label} -`;
}

export default function TradeTooltip({ events, x, y }: TradeTooltipProps) {
  if (events.length === 0) return null;

  return (
    <div
      className="pointer-events-none absolute z-30 max-h-[260px] w-[280px] overflow-hidden rounded-[8px] border border-[var(--border)] bg-[var(--surface)] p-2 text-[11px] text-[var(--text)] shadow-soft"
      style={{
        left: `min(max(${x + 12}px, 8px), calc(100% - 288px))`,
        top: `min(max(${y + 12}px, 8px), calc(100% - 260px))`,
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
  const strategy = event.trade as typeof event.trade & { strategyName?: string; color?: string };
  const tone = tradeToneForEvent(event);
  const color =
    strategy.color ??
    tone === "long"
      ? "var(--long)"
      : tone === "short"
        ? "var(--short)"
        : "var(--muted)";

  return (
    <div className="rounded-[6px] bg-[var(--surface-2)] px-2 py-1.5">
      <div className="flex items-center justify-between gap-2">
        <span className="flex min-w-0 items-center gap-1.5 font-semibold" style={{ color }}>
          <span>{KIND_LABEL[event.kind]}</span>
          {strategy.strategyName && (
            <span className="truncate text-[10px] text-[var(--muted)]">
              {strategy.strategyName}
            </span>
          )}
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
      <div className="mt-1 grid grid-cols-2 gap-x-3 gap-y-0.5 tabular text-[var(--muted)]">
        <span>
          {formatRiskLine("SL", event.trade.stopLossPct, event.trade.stopLossPrice)}
        </span>
        <span>
          {formatRiskLine("TP", event.trade.takeProfitPct, event.trade.takeProfitPrice)}
        </span>
        {event.trade.maxHoldBars !== null && event.trade.maxHoldBars !== undefined && (
          <span>Hold {event.trade.maxHoldBars}</span>
        )}
      </div>
    </div>
  );
}
