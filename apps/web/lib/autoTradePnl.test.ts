import { describe, expect, it } from "vitest";
import {
  buildLedger,
  buildRoundTrips,
  filterRoundTrips,
  resolveRangeBounds,
  summarizeRoundTrips,
} from "./autoTradePnl";
import type { Trade } from "./types";

function trade(overrides: Partial<Trade> & Pick<Trade, "ts" | "side">): Trade {
  return {
    id: overrides.id,
    runId: overrides.runId ?? 7,
    ts: overrides.ts,
    side: overrides.side,
    price: overrides.price ?? 100,
    qty: overrides.qty ?? 1,
    fee: overrides.fee ?? 0,
    pnl: overrides.pnl ?? 0,
    pnlPct: overrides.pnlPct,
    positionAfter: overrides.positionAfter ?? 0,
    reason: overrides.reason ?? "",
    seeded: overrides.seeded,
  };
}

describe("buildRoundTrips", () => {
  it("pairs an open and a close into one round-trip carrying the backend pnlPct", () => {
    const rts = buildRoundTrips(
      "BTC-USDT-SWAP",
      [
        trade({ id: 1, ts: 10, side: "long", price: 100 }),
        trade({ id: 2, ts: 20, side: "close", price: 110, pnl: 9, pnlPct: 0.15 }),
      ],
      3,
    );
    expect(rts).toHaveLength(1);
    expect(rts[0]).toMatchObject({
      instId: "BTC-USDT-SWAP",
      direction: "long",
      pnlUsd: 9,
      pnlPct: 0.15,
      openedTs: 10,
      closedTs: 20,
      isWin: true,
    });
  });

  it("falls back to derived pnlPct (price move × leverage) when the close lacks pnlPct", () => {
    const rts = buildRoundTrips(
      "BTC-USDT-SWAP",
      [
        trade({ id: 1, ts: 10, side: "long", price: 100 }),
        trade({ id: 2, ts: 20, side: "close", price: 105, pnl: 5 }),
      ],
      4,
    );
    // (105/100 - 1) × 4 = 0.20
    expect(rts[0].pnlPct).toBeCloseTo(0.2, 10);
  });

  it("derives a positive pnlPct for a winning short (price drop)", () => {
    const rts = buildRoundTrips(
      "BTC-USDT-SWAP",
      [
        trade({ id: 1, ts: 10, side: "short", price: 100 }),
        trade({ id: 2, ts: 20, side: "close", price: 95, pnl: 5 }),
      ],
      2,
    );
    // short: -(95/100 - 1) × 2 = +0.10
    expect(rts[0].pnlPct).toBeCloseTo(0.1, 10);
  });

  it("excludes seeded (warm-start) trades before pairing", () => {
    const rts = buildRoundTrips(
      "BTC-USDT-SWAP",
      [
        trade({ id: 1, ts: 10, side: "long", price: 100, seeded: true }),
        trade({ id: 2, ts: 20, side: "close", price: 110, pnl: 9, seeded: true }),
        trade({ id: 3, ts: 30, side: "long", price: 100 }),
        trade({ id: 4, ts: 40, side: "close", price: 90, pnl: -10, pnlPct: -0.3 }),
      ],
      3,
    );
    expect(rts).toHaveLength(1);
    expect(rts[0].pnlUsd).toBe(-10);
  });

  it("returns null pnlPct when entry price is non-positive and backend pnlPct absent", () => {
    const rts = buildRoundTrips(
      "BTC-USDT-SWAP",
      [
        trade({ id: 1, ts: 10, side: "long", price: 0 }),
        trade({ id: 2, ts: 20, side: "close", price: 110, pnl: 9 }),
      ],
      3,
    );
    expect(rts[0].pnlPct).toBeNull();
  });
});

describe("buildLedger", () => {
  it("merges members without pairing across instIds", () => {
    const ledger = buildLedger([
      {
        instId: "BTC-USDT-SWAP",
        leverage: 3,
        trades: [
          trade({ id: 1, ts: 10, side: "long", price: 100 }),
          trade({ id: 2, ts: 20, side: "close", price: 110, pnl: 9, pnlPct: 0.15 }),
        ],
      },
      {
        instId: "ETH-USDT-SWAP",
        leverage: 3,
        trades: [
          trade({ id: 3, ts: 15, side: "short", price: 50 }),
          trade({ id: 4, ts: 25, side: "close", price: 45, pnl: 4, pnlPct: 0.2 }),
        ],
      },
    ]);
    expect(ledger).toHaveLength(2);
    expect(ledger.map((r) => r.instId).sort()).toEqual(["BTC-USDT-SWAP", "ETH-USDT-SWAP"]);
  });
});

describe("resolveRangeBounds", () => {
  const NOW = 1_751_200_600;

  it("returns [0, now] for the all range", () => {
    expect(resolveRangeBounds("all", NOW)).toEqual({ startTs: 0, endTs: NOW });
  });

  it("returns the trailing 7d / 30d windows", () => {
    expect(resolveRangeBounds("7d", NOW)).toEqual({ startTs: NOW - 7 * 86400, endTs: NOW });
    expect(resolveRangeBounds("30d", NOW)).toEqual({ startTs: NOW - 30 * 86400, endTs: NOW });
  });

  it("anchors today at local midnight and ends at now", () => {
    const bounds = resolveRangeBounds("today", NOW);
    const midnight = new Date(NOW * 1000);
    midnight.setHours(0, 0, 0, 0);
    expect(bounds.startTs).toBe(Math.floor(midnight.getTime() / 1000));
    expect(bounds.endTs).toBe(NOW);
    expect(bounds.startTs).toBeLessThanOrEqual(NOW);
  });

  it("uses custom bounds and falls back to all when custom is omitted", () => {
    expect(resolveRangeBounds("custom", NOW, { startTs: 100, endTs: 200 })).toEqual({
      startTs: 100,
      endTs: 200,
    });
    expect(resolveRangeBounds("custom", NOW)).toEqual({ startTs: 0, endTs: NOW });
  });
});

describe("filterRoundTrips", () => {
  const rts = buildLedger([
    {
      instId: "BTC-USDT-SWAP",
      leverage: 3,
      trades: [
        trade({ id: 1, ts: 100, side: "long", price: 100 }),
        trade({ id: 2, ts: 200, side: "close", price: 110, pnl: 9, pnlPct: 0.15 }),
        trade({ id: 3, ts: 300, side: "long", price: 100 }),
        trade({ id: 4, ts: 400, side: "close", price: 90, pnl: -10, pnlPct: -0.3 }),
      ],
    },
  ]);

  it("includes round-trips whose closedTs is within the closed interval (boundaries inclusive)", () => {
    expect(filterRoundTrips(rts, { startTs: 200, endTs: 400 })).toHaveLength(2);
    expect(filterRoundTrips(rts, { startTs: 201, endTs: 400 })).toHaveLength(1);
    expect(filterRoundTrips(rts, { startTs: 0, endTs: 199 })).toHaveLength(0);
  });
});

describe("summarizeRoundTrips", () => {
  const rts = buildLedger([
    {
      instId: "BTC-USDT-SWAP",
      leverage: 3,
      trades: [
        trade({ id: 1, ts: 100, side: "long", price: 100 }),
        trade({ id: 2, ts: 200, side: "close", price: 110, pnl: 12, pnlPct: 0.36 }),
        trade({ id: 3, ts: 300, side: "long", price: 100 }),
        trade({ id: 4, ts: 400, side: "close", price: 90, pnl: -4, pnlPct: -0.12 }),
      ],
    },
  ]);

  it("aggregates total pnl, return rate, count, win rate, best and worst", () => {
    const s = summarizeRoundTrips(rts, 100);
    expect(s.totalPnlUsd).toBe(8);
    expect(s.totalReturnPct).toBeCloseTo(0.08, 10); // 8 / 100
    expect(s.count).toBe(2);
    expect(s.wins).toBe(1);
    expect(s.losses).toBe(1);
    expect(s.winRate).toBeCloseTo(0.5, 10);
    expect(s.bestUsd).toBe(12);
    expect(s.worstUsd).toBe(-4);
  });

  it("returns totalReturnPct 0 when the capital base is non-positive", () => {
    expect(summarizeRoundTrips(rts, 0).totalReturnPct).toBe(0);
    expect(summarizeRoundTrips(rts, -50).totalReturnPct).toBe(0);
  });

  it("returns a zero summary (winRate 0, not NaN) for empty input", () => {
    const s = summarizeRoundTrips([], 100);
    expect(s).toEqual({
      totalPnlUsd: 0,
      totalReturnPct: 0,
      count: 0,
      wins: 0,
      losses: 0,
      winRate: 0,
      bestUsd: 0,
      worstUsd: 0,
    });
    expect(Number.isNaN(s.winRate)).toBe(false);
  });
});
