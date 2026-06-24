import type { Candle, MarketTicker, MarketTrade, OrderBookLevel, OrderBookSnapshot } from "./types";

// 把一批新成交一次性折叠进既有成交带：prev 在同 key 上胜出（不被新数据覆盖），整体按 ts 降序后截断。
// 一次 Map 遍历完成，避免节流 flush 时对每条 pending 调用一次 mergeTradeTape 的 O(limit) 重复折叠。
export function foldTradeTape(
  prev: MarketTrade[],
  incoming: MarketTrade[],
  limit = 40,
): MarketTrade[] {
  const byKey = new Map<string, MarketTrade>();
  for (const row of prev) byKey.set(tradeKey(row), row);
  for (const trade of incoming) {
    const key = tradeKey(trade);
    if (!byKey.has(key)) byKey.set(key, trade);
  }
  return Array.from(byKey.values())
    .sort((a, b) => b.ts - a.ts)
    .slice(0, limit);
}

export function mergeTradeTape(
  prev: MarketTrade[],
  trade: MarketTrade,
  limit = 40,
): MarketTrade[] {
  return foldTradeTape(prev, [trade], limit);
}

export function resolveLivePrice(
  candle: Candle | null,
  trade: MarketTrade | null,
  ticker: MarketTicker | null,
): number | null {
  const sources = [
    candle ? { ts: candle.ts, price: candle.c, rank: 0 } : null,
    ticker ? { ts: ticker.ts, price: ticker.last, rank: 1 } : null,
    trade ? { ts: trade.ts, price: trade.price, rank: 2 } : null,
  ].filter((item): item is { ts: number; price: number; rank: number } => item != null);
  if (sources.length === 0) return null;
  sources.sort((a, b) => b.ts - a.ts || b.rank - a.rank);
  return sources[0].price;
}

export function bookSpread(
  snapshot: OrderBookSnapshot | null,
): { value: number; pct: number } | null {
  const ask = snapshot?.asks[0]?.price;
  const bid = snapshot?.bids[0]?.price;
  if (ask == null || bid == null || bid === 0) return null;
  const value = ask - bid;
  return { value, pct: value / bid };
}

export function bookSideTotals(levels: OrderBookLevel[]): number[] {
  let total = 0;
  return levels.map((level) => {
    total += level.qty;
    return total;
  });
}

function tradeKey(trade: MarketTrade): string {
  return trade.tradeId ?? `${trade.ts}:${trade.side}:${trade.price}:${trade.qty}`;
}
