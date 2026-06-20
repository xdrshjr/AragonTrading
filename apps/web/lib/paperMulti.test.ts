import { describe, expect, it } from "vitest";
import {
  buildPaperPositionRows,
  mergePaperEquity,
  mergePaperTrade,
  paperSessionsToOverlays,
  resolveMaxPaperSessions,
  selectFirstAvailablePaperStrategy,
  sessionsFromReconcile,
} from "./paperMulti";
import type {
  PaperSessionRow,
  PaperStrategySession,
  StrategyEquityPoint,
  StrategyTrade,
} from "./types";

function paperSessionRow(over: Partial<PaperSessionRow>): PaperSessionRow {
  return {
    sessionId: "s",
    runId: 1,
    instId: "BTC-USDT-SWAP",
    bar: "1H",
    state: "running",
    equity: 10000,
    positionSide: "flat",
    strategyId: 1,
    groupId: null,
    ...over,
  };
}

const sessions: Record<number, PaperStrategySession> = {
  1: {
    strategyId: 1,
    strategyName: "Alpha",
    sessionId: "s1",
    runId: 11,
    color: "#111111",
    state: "running",
  },
  2: {
    strategyId: 2,
    strategyName: "Beta",
    sessionId: "s2",
    runId: 22,
    color: "#222222",
    state: "running",
  },
};

describe("paper multi helpers", () => {
  it("mergePaperTrade appends under strategy id without mutating previous arrays", () => {
    const first: StrategyTrade = {
      strategyId: 1,
      strategyName: "Alpha",
      color: "#111111",
      ts: 100,
      side: "long",
      price: 10,
      qty: 1,
      fee: 0,
      pnl: 0,
      positionAfter: 1,
      reason: "open",
    };
    const second = { ...first, ts: 200, price: 11 };
    const initial = { 1: [first] };

    const merged = mergePaperTrade(initial, second);

    expect(merged[1]).toEqual([first, second]);
    expect(initial[1]).toEqual([first]);
  });

  it("mergePaperEquity upserts by strategy id and timestamp", () => {
    const initialPoint: StrategyEquityPoint = {
      strategyId: 1,
      ts: 100,
      equity: 10000,
      drawdown: 0,
      transient: true,
    };
    const confirmedPoint: StrategyEquityPoint = {
      strategyId: 1,
      ts: 100,
      equity: 10020,
      drawdown: 0,
    };

    const merged = mergePaperEquity({ 1: [initialPoint] }, confirmedPoint);

    expect(merged[1]).toEqual([confirmedPoint]);
  });

  it("selectFirstAvailablePaperStrategy keeps active id or falls back", () => {
    expect(selectFirstAvailablePaperStrategy(2, sessions)).toBe(2);
    expect(selectFirstAvailablePaperStrategy(9, sessions)).toBe(1);
    expect(selectFirstAvailablePaperStrategy(null, {})).toBeNull();
  });

  it("paperSessionsToOverlays returns visible strategy curves with stable colors", () => {
    const overlays = paperSessionsToOverlays(
      sessions,
      {
        1: [{ strategyId: 1, ts: 100, equity: 10000, drawdown: 0 }],
        2: [{ strategyId: 2, ts: 100, equity: 10010, drawdown: 0 }],
      },
      [2],
    );

    expect(overlays).toEqual([
      {
        id: 2,
        name: "Beta",
        color: "#222222",
        equity: [{ strategyId: 2, ts: 100, equity: 10010, drawdown: 0 }],
      },
    ]);
  });

  it("resolveMaxPaperSessions falls back when health omits the field", () => {
    expect(resolveMaxPaperSessions()).toBe(3);
    expect(resolveMaxPaperSessions(12)).toBe(12);
  });

  it("sessionsFromReconcile keeps only running rows, resolving name/color/group", () => {
    const rows: PaperSessionRow[] = [
      paperSessionRow({ strategyId: 1, sessionId: "s1", runId: 11, groupId: "paper_g1" }),
      paperSessionRow({ strategyId: 2, sessionId: "s2", runId: 22, groupId: "paper_g1" }),
      paperSessionRow({ strategyId: 3, sessionId: "s3", runId: 33, state: "stopped" }),
    ];

    const { sessions, groupId } = sessionsFromReconcile(rows, { 1: "Alpha", 2: "Beta" });

    expect(groupId).toBe("paper_g1");
    expect(Object.keys(sessions)).toEqual(["1", "2"]);
    expect(sessions[1]).toEqual({
      strategyId: 1,
      strategyName: "Alpha",
      sessionId: "s1",
      runId: 11,
      color: "#6366f1",
      state: "running",
    });
    expect(sessions[2].strategyName).toBe("Beta");
    expect(sessions[2].color).toBe("#10b981");
  });

  it("sessionsFromReconcile falls back on name and nulls group when single or mixed", () => {
    const single = sessionsFromReconcile([paperSessionRow({ strategyId: 7, sessionId: "s7" })], {});
    expect(single.groupId).toBeNull();
    expect(single.sessions[7].strategyName).toBe("Strategy 7");

    const mixed = sessionsFromReconcile(
      [
        paperSessionRow({ strategyId: 1, groupId: "paper_a" }),
        paperSessionRow({ strategyId: 2, groupId: "paper_b" }),
      ],
      {},
    );
    expect(mixed.groupId).toBeNull();
  });
});

function strategyTrade(over: Partial<StrategyTrade> & Pick<StrategyTrade, "strategyId">): StrategyTrade {
  return {
    strategyName: "S",
    color: "#000000",
    ts: 1,
    side: "long",
    price: 100,
    qty: 1,
    fee: 0,
    pnl: 0,
    positionAfter: 1,
    reason: "open",
    ...over,
  };
}

describe("buildPaperPositionRows", () => {
  it("prefers the authoritative per-strategy position with its own mark price and pnl", () => {
    const rows = buildPaperPositionRows(
      [sessions[1]],
      { 1: { side: "short", size: 0.5, avgEntry: 100, markPrice: 90, unrealizedPnl: 5 } },
      {},
      { 1: [{ strategyId: 1, ts: 1, equity: 10010, drawdown: 0 }] },
      80, // chart mark price is ignored in favour of the authoritative 90
    );

    expect(rows).toHaveLength(1);
    expect(rows[0]).toMatchObject({ strategyId: 1, strategyName: "Alpha", color: "#111111" });
    expect(rows[0].position).toEqual({
      side: "short",
      size: 0.5,
      avgEntry: 100,
      markPrice: 90,
      unrealizedPnl: 5,
      unrealizedPct: 0.1, // 5 / (100 * 0.5)
    });
    expect(rows[0].equity).toBe(10010);
  });

  it("falls back to deriveOpenPosition only before the first authoritative event", () => {
    const rows = buildPaperPositionRows(
      [sessions[2]],
      {}, // strategy 2 has no position/snapshot event yet
      { 2: [strategyTrade({ strategyId: 2, side: "long", price: 100, qty: 2 })] },
      {},
      110,
    );

    expect(rows[0].position).toMatchObject({
      side: "long",
      size: 2,
      avgEntry: 100,
      markPrice: 110,
      unrealizedPnl: 20, // (110 - 100) * 2
    });
    expect(rows[0].equity).toBeNull();
  });

  it("treats an authoritative flat (null) as no position even when trades exist", () => {
    const rows = buildPaperPositionRows(
      [sessions[1]],
      { 1: null }, // snapshot reported the strategy as flat
      { 1: [strategyTrade({ strategyId: 1, side: "long", price: 100, qty: 2 })] },
      {},
      110,
    );

    expect(rows[0].position).toBeNull();
  });

  it("treats a flat-side authoritative event as no position", () => {
    const rows = buildPaperPositionRows(
      [sessions[1]],
      { 1: { side: "flat", size: 0, avgEntry: 0, markPrice: 100, unrealizedPnl: 0 } },
      {},
      {},
      100,
    );

    expect(rows[0].position).toBeNull();
  });

  it("uses the last equity point as the per-strategy funds figure", () => {
    const rows = buildPaperPositionRows(
      [sessions[1]],
      { 1: null },
      {},
      {
        1: [
          { strategyId: 1, ts: 1, equity: 10000, drawdown: 0 },
          { strategyId: 1, ts: 2, equity: 9950, drawdown: -0.005, transient: true },
        ],
      },
      100,
    );

    expect(rows[0].equity).toBe(9950);
  });

  it("emits one row per running strategy in order", () => {
    const rows = buildPaperPositionRows([sessions[1], sessions[2]], {}, {}, {}, null);
    expect(rows.map((r) => r.strategyId)).toEqual([1, 2]);
    expect(rows.every((r) => r.position === null && r.equity === null)).toBe(true);
  });
});
