"use client";

import { fmtNum, fmtPct, fmtPrice, fmtQty, fmtTime } from "@/lib/format";
import { bookSideTotals, bookSpread } from "@/lib/marketDepth";
import type { Candle, LiveMarketState, OrderBookLevel } from "@/lib/types";

interface MarketDepthPanelProps {
  market: LiveMarketState;
  activeCandle: Candle | null;
}

export default function MarketDepthPanel({ market, activeCandle }: MarketDepthPanelProps) {
  const spread = bookSpread(market.book);
  const asks = market.book?.asks.slice(0, 5) ?? [];
  const bids = market.book?.bids.slice(0, 5) ?? [];
  const updated = market.updatedTs ? fmtTime(market.updatedTs, false) : "-";

  return (
    <div className="flex min-h-0 flex-col gap-2 px-2 py-2 text-[11px]">
      <div className="border-b border-[var(--border)] pb-2">
        <div className="flex items-center gap-1">
          <span
            className="h-1.5 w-1.5 rounded-full"
            style={{ background: market.connected ? "var(--long)" : "var(--muted)" }}
          />
          <span className="truncate font-semibold text-[var(--text)]">{market.instId}</span>
        </div>
        <div className="mt-1 truncate text-base font-semibold tabular text-[var(--text)]">
          {fmtPrice(market.lastPrice)}
        </div>
        <div className="truncate text-[var(--muted)]">
          {market.connected ? updated : "disconnected"}
        </div>
        {market.error && <div className="mt-1 text-[var(--short)]">{market.error}</div>}
      </div>

      <div className="grid grid-cols-2 gap-1 border-b border-[var(--border)] pb-2 tabular">
        <Metric label="24h Vol" value={fmtNum(market.ticker?.vol24h, 1)} />
        <Metric label="Bar Vol" value={fmtNum(activeCandle?.vol, 1)} />
        <Metric label="Spread" value={fmtPrice(spread?.value)} />
        <Metric label="Pct" value={fmtPct(spread?.pct, 3)} />
      </div>

      <BookSide side="ask" levels={[...asks].reverse()} />
      <BookSide side="bid" levels={bids} />

      <div className="min-h-0 border-t border-[var(--border)] pt-2">
        <div className="mb-1 font-medium text-[var(--muted)]">Trades</div>
        {market.trades.length === 0 ? (
          <div className="text-[var(--muted)]">Waiting for OKX trades</div>
        ) : (
          <div className="flex max-h-40 flex-col gap-1 overflow-hidden tabular">
            {market.trades.slice(0, 8).map((trade) => (
              <div
                key={trade.tradeId ?? `${trade.ts}-${trade.price}`}
                className="grid grid-cols-[32px_1fr] gap-1"
              >
                <span className={trade.side === "buy" ? "text-[var(--long)]" : "text-[var(--short)]"}>
                  {trade.side === "buy" ? "B" : "S"}
                </span>
                <span className="truncate text-right text-[var(--text)]">
                  {fmtPrice(trade.price)} / {fmtQty(trade.qty)}
                </span>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div className="min-w-0">
      <div className="truncate text-[var(--muted)]">{label}</div>
      <div className="truncate text-[var(--text)]">{value}</div>
    </div>
  );
}

function BookSide({ side, levels }: { side: "ask" | "bid"; levels: OrderBookLevel[] }) {
  const totals = bookSideTotals(levels);
  const max = Math.max(...totals, 0);
  const tone = side === "ask" ? "var(--short)" : "var(--long)";
  if (levels.length === 0) {
    return <div className="text-[var(--muted)]">Waiting for OKX {side}s</div>;
  }
  return (
    <div className="flex flex-col gap-1 tabular">
      {levels.map((level, index) => {
        const width = max > 0 ? `${Math.max(8, (totals[index] / max) * 100)}%` : "0%";
        return (
          <div key={`${side}-${level.price}`} className="relative overflow-hidden rounded-sm px-1 py-0.5">
            <div
              className="absolute inset-y-0 right-0 opacity-10"
              style={{ width, background: tone }}
            />
            <div className="relative grid grid-cols-[1fr_1fr] gap-1">
              <span style={{ color: tone }}>{fmtPrice(level.price)}</span>
              <span className="truncate text-right text-[var(--text)]">{fmtQty(level.qty)}</span>
            </div>
          </div>
        );
      })}
    </div>
  );
}
