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

  it("sorts trades by timestamp for rendering while pairing follows arrival order", () => {
    // sortTradesForTrail 是渲染顺序（ts 升序，markers 必须升序喂给 lightweight-charts）；
    // 配对走因果序——无 id 时数组序即到达序，所以「先到的 close」是孤儿平仓，不倒配后到的 open。
    const close = trade({ ts: 20, side: "close", price: 110, pnl: 10 });
    const entry = trade({ ts: 10, side: "long", price: 100 });

    const sorted = sortTradesForTrail([close, entry]);

    expect(sorted).toEqual([entry, close]);
    expect(buildTradeTrailSegments([close, entry])).toEqual([]);
    expect(buildTradeTrailSegments([entry, close])).toHaveLength(1);
  });

  it("pairs by db id, not ts, when the two clocks write ts out of causal order", () => {
    // 回归门禁（ai-trade-stats-missing-round-trips）：AI 自动交易的 ts 由 bar 开盘时刻与墙钟两套
    // 时钟写入，平仓 ts 可能早于开仓 ts；按 ts 排序会把这笔完整交易整笔丢弃。
    const entry = trade({ id: 50914, ts: 1784701626, side: "long", price: 1912.94 });
    const exit = trade({ id: 50915, ts: 1784700900, side: "close", price: 1913.64, pnl: 0.0574 });

    const segments = buildTradeTrailSegments([entry, exit]);

    expect(segments).toHaveLength(1);
    expect(segments[0].entry).toBe(entry);
    expect(segments[0].exit).toBe(exit);
    expect(segments[0].canRenderLine).toBe(false); // 双时钟残留：ts 逆序故不画开→平连线
  });

  it("still emits markers in ts ascending order while pairing by db id", () => {
    // 配对改因果序后，渲染顺序必须仍由 indexTrades 保持 ts 升序：lightweight-charts 的
    // setMarkers 断言 markers 按 time 升序，喂入逆序会让 dev 构建直接抛异常、图表崩。
    const entry = trade({ id: 50914, ts: 1784701626, side: "long", price: 1912.94 });
    const exit = trade({ id: 50915, ts: 1784700900, side: "close", price: 1913.64, pnl: 0.0574 });

    const markers = buildTradeMarkerEvents([entry, exit]);

    expect(markers.map((m) => m.trade.ts)).toEqual([1784700900, 1784701626]);
    // 被救回的平仓不再是 orphan-close，两个 marker 都指向同一段
    expect(markers.map((m) => m.kind)).toEqual(["close", "entry-long"]);
    expect(markers[0].pairedSegmentId).toBe(markers[1].pairedSegmentId);
    expect(markers[0].pairedSegmentId).toBeDefined();
  });

  it("pairs a merged multi-strategy array block by block instead of interleaving by ts", () => {
    // paper 多策略把各策略成交块 flatMap 成一个无 id 数组（visiblePaperTrades）。因果序 = 数组序
    // ⇒ 逐块配对；此前按 ts 交织会让 A 策略的开仓配上 B 策略的平仓。
    const aEntry = trade({ ts: 10, side: "long", price: 100 });
    const aExit = trade({ ts: 30, side: "close", price: 110, pnl: 5 });
    const bEntry = trade({ ts: 20, side: "short", price: 50 });
    const bExit = trade({ ts: 40, side: "close", price: 45, pnl: 3 });

    const segments = buildTradeTrailSegments([aEntry, aExit, bEntry, bExit]);

    expect(segments).toHaveLength(2);
    expect(segments[0].entry).toBe(aEntry);
    expect(segments[0].exit).toBe(aExit);
    expect(segments[1].entry).toBe(bEntry);
    expect(segments[1].exit).toBe(bExit);
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
