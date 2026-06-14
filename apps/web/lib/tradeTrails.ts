import type {
  Trade,
  TradeDirection,
  TradeMarkerEvent,
  TradeTrailSegment,
} from "./types";

interface IndexedTrade {
  trade: Trade;
  index: number;
}

interface PairedTrail {
  segment: TradeTrailSegment;
  entryIndex: number;
  exitIndex: number;
}

const DASHED_LINE_STYLE = 2;

export function sortTradesForTrail(trades: Trade[]): Trade[] {
  return indexTrades(trades).map((item) => item.trade);
}

export function buildTradeTrailSegments(trades: Trade[]): TradeTrailSegment[] {
  return buildPairedTrails(trades).map((item) => item.segment);
}

export function buildTradeMarkerEvents(trades: Trade[]): TradeMarkerEvent[] {
  const pairByIndex = new Map<number, PairedTrail>();
  for (const pair of buildPairedTrails(trades)) {
    pairByIndex.set(pair.entryIndex, pair);
    pairByIndex.set(pair.exitIndex, pair);
  }

  let hasOpen = false;
  return indexTrades(trades).map(({ trade, index }) => {
    const pair = pairByIndex.get(index);
    if (trade.side === "long" || trade.side === "short") {
      hasOpen = !pair;
      return {
        id: markerId(trade, index),
        trade,
        kind: trade.side === "long" ? "entry-long" : "entry-short",
        direction: trade.side,
        pairedSegmentId: pair?.segment.id,
      };
    }

    const event: TradeMarkerEvent = {
      id: markerId(trade, index),
      trade,
      kind: pair || hasOpen ? "close" : "orphan-close",
      pairedSegmentId: pair?.segment.id,
    };
    if (pair) hasOpen = false;
    return event;
  });
}

export function tradeToneForEvent(
  event: TradeMarkerEvent,
): "long" | "short" | "neutral" {
  if (event.kind === "entry-long") return "long";
  if (event.kind === "entry-short") return "short";
  if (event.trade.pnl > 0) return "long";
  if (event.trade.pnl < 0) return "short";
  return "neutral";
}

function buildPairedTrails(trades: Trade[]): PairedTrail[] {
  const pairs: PairedTrail[] = [];
  let open: IndexedTrade | null = null;

  for (const item of indexTrades(trades)) {
    const { trade } = item;
    if (trade.side === "long" || trade.side === "short") {
      open = item;
      continue;
    }
    if (!open) continue;
    pairs.push({
      segment: segmentFor(open, item),
      entryIndex: open.index,
      exitIndex: item.index,
    });
    open = null;
  }

  return pairs;
}

function segmentFor(entry: IndexedTrade, exit: IndexedTrade): TradeTrailSegment {
  const direction = entry.trade.side as TradeDirection;
  const pnl = exit.trade.pnl;
  const isWin = pnl > 0;
  return {
    id: segmentId(entry, exit),
    direction,
    entry: entry.trade,
    exit: exit.trade,
    pnl,
    isWin,
    canRenderLine: entry.trade.ts < exit.trade.ts,
    lineColor: direction === "long" ? "var(--long)" : "var(--short)",
    lineStyle: isWin ? undefined : DASHED_LINE_STYLE,
  };
}

function indexTrades(trades: Trade[]): IndexedTrade[] {
  return trades
    .map((trade, index) => ({ trade, index }))
    .sort((a, b) => {
      if (a.trade.ts !== b.trade.ts) return a.trade.ts - b.trade.ts;
      return tradeOrder(a) - tradeOrder(b);
    });
}

function tradeOrder(item: IndexedTrade): number {
  return item.trade.id ?? item.index;
}

function segmentId(entry: IndexedTrade, exit: IndexedTrade): string {
  const runId = entry.trade.runId ?? exit.trade.runId;
  if (runId != null && entry.trade.id != null && exit.trade.id != null) {
    return `${runId}-${entry.trade.id}-${exit.trade.id}`;
  }
  return [
    entry.trade.ts,
    entry.index,
    exit.trade.ts,
    exit.index,
  ].join("-");
}

function markerId(trade: Trade, index: number): string {
  if (trade.runId != null && trade.id != null) return `${trade.runId}-${trade.id}`;
  return `${trade.ts}-${index}-${trade.side}`;
}
