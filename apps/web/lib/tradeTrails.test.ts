import { describe, expect, it } from "vitest";
import {
  buildTradeMarkerEvents,
  buildTradeTrailSegments,
  sortTradesForTrail,
  tradeToneForEvent,
} from "./tradeTrails";
import type { Trade } from "./types";

function trade(overrides: Partial<Trade> & Pick<Trade, "ts" | "side">): Trade {
  return {
    id: overrides.id,
    runId: overrides.runId ?? 7,
    ts: overrides.ts,
    side: overrides.side,
    price: overrides.price ?? 100,
    qty: overrides.qty ?? 1,
    fee: overrides.fee ?? 0.1,
    pnl: overrides.pnl ?? 0,
    positionAfter: overrides.positionAfter ?? 0,
    reason: overrides.reason ?? "",
  };
}

describe("trade trail helpers", () => {
  it("pairs one long entry and one close into a winning long segment", () => {
    const entry = trade({ id: 1, ts: 10, side: "long", price: 100 });
    const exit = trade({ id: 2, ts: 20, side: "close", price: 110, pnl: 9 });

    const segments = buildTradeTrailSegments([entry, exit]);

    expect(segments).toHaveLength(1);
    expect(segments[0]).toMatchObject({
      direction: "long",
      entry,
      exit,
      pnl: 9,
      isWin: true,
      canRenderLine: true,
    });
  });

  it("pairs one short entry and one close into a short segment", () => {
    const entry = trade({ id: 3, ts: 10, side: "short", price: 100 });
    const exit = trade({ id: 4, ts: 20, side: "close", price: 90, pnl: 8 });

    const segments = buildTradeTrailSegments([entry, exit]);

    expect(segments).toHaveLength(1);
    expect(segments[0].direction).toBe("short");
    expect(segments[0].pnl).toBe(8);
  });

  it("sorts trades by timestamp and stable fallback index before pairing", () => {
    const close = trade({ ts: 20, side: "close", price: 110, pnl: 10 });
    const entry = trade({ ts: 10, side: "long", price: 100 });

    const sorted = sortTradesForTrail([close, entry]);
    const segments = buildTradeTrailSegments([close, entry]);

    expect(sorted).toEqual([entry, close]);
    expect(segments).toHaveLength(1);
    expect(segments[0].entry).toBe(entry);
    expect(segments[0].exit).toBe(close);
  });

  it("handles a same-timestamp reverse without crossing old and new entries", () => {
    const firstEntry = trade({ id: 1, ts: 10, side: "long", price: 100 });
    const firstExit = trade({ id: 2, ts: 20, side: "close", price: 105 });
    const secondEntry = trade({ id: 3, ts: 20, side: "short", price: 104 });
    const secondExit = trade({ id: 4, ts: 30, side: "close", price: 95 });

    const segments = buildTradeTrailSegments([
      secondExit,
      secondEntry,
      firstExit,
      firstEntry,
    ]);

    expect(segments).toHaveLength(2);
    expect(segments[0].entry).toBe(firstEntry);
    expect(segments[0].exit).toBe(firstExit);
    expect(segments[1].entry).toBe(secondEntry);
    expect(segments[1].exit).toBe(secondExit);
  });

  it("returns orphan close marker events without creating segments", () => {
    const orphan = trade({ id: 5, ts: 10, side: "close", pnl: -1 });

    expect(buildTradeTrailSegments([orphan])).toEqual([]);
    expect(buildTradeMarkerEvents([orphan])).toMatchObject([
      { trade: orphan, kind: "orphan-close" },
    ]);
  });

  it("keeps an unclosed final entry as a marker without a segment", () => {
    const entry = trade({ id: 6, ts: 10, side: "short", positionAfter: -1 });

    expect(buildTradeTrailSegments([entry])).toEqual([]);
    expect(buildTradeMarkerEvents([entry])).toMatchObject([
      { trade: entry, kind: "entry-short", direction: "short" },
    ]);
  });

  it("marks same-candle entry and close pairs as not line-renderable", () => {
    const entry = trade({ id: 7, ts: 10, side: "long", price: 100 });
    const exit = trade({ id: 8, ts: 10, side: "close", price: 101, pnl: 1 });

    const segments = buildTradeTrailSegments([entry, exit]);
    const markers = buildTradeMarkerEvents([entry, exit]);

    expect(segments).toHaveLength(1);
    expect(segments[0].canRenderLine).toBe(false);
    expect(markers).toHaveLength(2);
    expect(markers[0].pairedSegmentId).toBe(segments[0].id);
    expect(markers[1].pairedSegmentId).toBe(segments[0].id);
  });

  it("does not mutate the original trade array", () => {
    const input = [
      trade({ id: 10, ts: 20, side: "close" }),
      trade({ id: 9, ts: 10, side: "long" }),
    ];
    const snapshot = input.map((t) => ({ ...t }));

    buildTradeTrailSegments(input);
    buildTradeMarkerEvents(input);

    expect(input).toEqual(snapshot);
    expect(input.map((t) => t.id)).toEqual([10, 9]);
  });

  it("classifies marker tones by event kind and realized PnL", () => {
    const longEntry = trade({ ts: 10, side: "long" });
    const shortEntry = trade({ ts: 11, side: "short" });
    const winningClose = trade({ ts: 12, side: "close", pnl: 1 });
    const losingClose = trade({ ts: 13, side: "close", pnl: -1 });

    expect(tradeToneForEvent({ id: "a", trade: longEntry, kind: "entry-long" }))
      .toBe("long");
    expect(tradeToneForEvent({ id: "b", trade: shortEntry, kind: "entry-short" }))
      .toBe("short");
    expect(tradeToneForEvent({ id: "c", trade: winningClose, kind: "close" }))
      .toBe("long");
    expect(tradeToneForEvent({ id: "d", trade: losingClose, kind: "close" }))
      .toBe("short");
  });
});
