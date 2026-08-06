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

  // 回归门禁（ai-trade-stats-missing-round-trips）：AI 自动交易的 trades.ts 由两套时钟写入
  // （K 线轮 = bar 开盘时刻 / 定时唤醒轮 = 墙钟），按 ts 排序会打乱真实的开→平交替。
  // 配对必须按因果序（落库 id 升序 / 数组到达序），否则整笔丢弃或张冠李戴。
  it("keeps a completed trade whose close.ts precedes open.ts (dual-clock ts)", () => {
    // 生产库 run_id=1557（ETH-USDT-SWAP / 15m）真实两行：
    // 开仓来自定时唤醒轮（墙钟 06:27:06），平仓来自 K 线轮（bar 开盘 06:15:00）。
    const rts = buildRoundTrips(
      "ETH-USDT-SWAP",
      [
        trade({ id: 50914, ts: 1784701626, side: "long", price: 1912.94, qty: 0.82,
          positionAfter: 0.82 }),
        trade({ id: 50915, ts: 1784700900, side: "close", price: 1913.64, qty: 0.82,
          pnl: 0.0574 }),
      ],
      5,
    );
    expect(rts).toHaveLength(1);
    expect(rts[0].entry.id).toBe(50914);
    expect(rts[0].exit.id).toBe(50915);
    expect(rts[0].pnlUsd).toBeCloseTo(0.0574, 10);
  });

  it("does not mis-pair when a bar-ts open follows a wall-clock close", () => {
    // 生产库 run_id=1548（BTC-USDT-SWAP / 15m）真实四行：两对对内均正序，但跨对重叠 ——
    // 50896 开仓（bar 17:45:00）的 ts 早于 50894 平仓（墙钟 17:47:05）。
    const rts = buildRoundTrips(
      "BTC-USDT-SWAP",
      [
        trade({ id: 50892, ts: 1784654324, side: "short", price: 66409.2, qty: 0.16,
          positionAfter: -0.16 }),
        trade({ id: 50894, ts: 1784656025, side: "close", price: 66200.0, qty: 0.16,
          pnl: 0.3347 }),
        trade({ id: 50896, ts: 1784655900, side: "short", price: 66186.5, qty: 0.16,
          positionAfter: -0.16 }),
        trade({ id: 50898, ts: 1784659083, side: "close", price: 66265.0, qty: 0.16,
          pnl: -0.1256 }),
      ],
      5,
    );
    expect(rts.map((r) => [r.entry.id, r.exit.id])).toEqual([
      [50892, 50894],
      [50896, 50898],
    ]);
    expect(rts[0].entry.price).toBe(66409.2);   // 张冠李戴时这里会是 66186.5
  });

  it("keeps a dual-clock round trip on the live snapshot path (trades carry no id)", () => {
    // 「当前」页的成交来自 WS 快照 / 尾部追加，executor 构造的成交字典**无 id**，
    // 因果序只能取数组到达序——这条路径与上面带 id 的历史路径必须同样救回这笔交易。
    const rts = buildRoundTrips(
      "ETH-USDT-SWAP",
      [
        trade({ ts: 1784701626, side: "long", price: 1912.94, qty: 0.82, positionAfter: 0.82 }),
        trade({ ts: 1784700900, side: "close", price: 1913.64, qty: 0.82, pnl: 0.0574 }),
      ],
      5,
    );
    expect(rts).toHaveLength(1);
    expect(rts[0].openedTs).toBe(1784701626);
    expect(rts[0].pnlUsd).toBeCloseTo(0.0574, 10);
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

// ============ 净额口径镜像（ai-trade-cost-truth-and-net-pnl §3.5 F-2，G-7）============
describe("buildRoundTrips cost basis (G-7：与后端 _Trip 逐笔同构)", () => {
  it("defaults to gross and stays byte-for-byte identical for non-AI callers (fee ≡ 0)", () => {
    // live-auto（非 AI）成交的 fee 恒为 0.0（账本只由 ai_auto 注入），减零即恒等——**结构性**保证。
    const trades = [
      trade({ id: 1, ts: 10, side: "long", price: 100 }),
      trade({ id: 2, ts: 20, side: "close", price: 110, pnl: 9 }),
    ];
    const [rt] = buildRoundTrips("BTC-USDT-SWAP", trades, 3);
    expect(rt.pnlUsd).toBe(9);
    expect(rt.feeUsd).toBe(0);
    expect(rt.grossPnlUsd).toBe(9);
  });

  it("reconstructs the net pnl from a gross row by subtracting every leg fee", () => {
    const trades = [
      trade({ id: 1, ts: 10, side: "long", price: 100, fee: 1.5 }),
      trade({ id: 2, ts: 20, side: "close", price: 110, pnl: 10, fee: 1.5 }),
    ];
    const [rt] = buildRoundTrips("BTC-USDT-SWAP", trades, 3, { costBasis: "gross" });
    expect(rt.pnlUsd).toBe(7);
    expect(rt.feeUsd).toBe(3);
    expect(rt.grossPnlUsd).toBe(10);
    expect(rt.isWin).toBe(true);
  });

  it("takes a net row verbatim and restores the gross side by adding the fees back", () => {
    const trades = [
      trade({ id: 1, ts: 10, side: "long", price: 100, fee: 1.5 }),
      trade({ id: 2, ts: 20, side: "close", price: 110, pnl: 7, fee: 1.5 }),
    ];
    const [rt] = buildRoundTrips("BTC-USDT-SWAP", trades, 3, { costBasis: "net" });
    expect(rt.pnlUsd).toBe(7);
    expect(rt.grossPnlUsd).toBe(10);
  });

  it("sums the fees of EVERY leg since the previous close, including overwritten open legs", () => {
    // 后端 `_pair_round_trips` 的同一组夹具：部分成交产生两条开仓腿，配对只留最后一条。
    // 按「配对那条腿的 fee」计会得到 5.0（漏记 1.0）——这条断言正是 B-5 要防的漏记。
    const trades = [
      trade({ id: 1, ts: 10, side: "long", price: 100, fee: 1 }), // 3 张
      trade({ id: 2, ts: 11, side: "long", price: 102, fee: 2 }), // 再 7 张
      trade({ id: 3, ts: 20, side: "close", price: 110, pnl: 10, fee: 3 }),
    ];
    const [rt] = buildRoundTrips("BTC-USDT-SWAP", trades, 3, { costBasis: "gross" });
    expect(rt.feeUsd).toBe(6);
    expect(rt.pnlUsd).toBe(4);
  });

  it("does not let one round-trip's fees leak into the next", () => {
    const trades = [
      trade({ id: 1, ts: 10, side: "long", price: 100, fee: 1 }),
      trade({ id: 2, ts: 20, side: "close", price: 110, pnl: 10, fee: 1 }),
      trade({ id: 3, ts: 30, side: "short", price: 110, fee: 2 }),
      trade({ id: 4, ts: 40, side: "close", price: 105, pnl: 5, fee: 2 }),
    ];
    const rts = buildRoundTrips("BTC-USDT-SWAP", trades, 3, { costBasis: "gross" });
    expect(rts.map((r) => r.feeUsd)).toEqual([2, 4]);
    expect(rts.map((r) => r.pnlUsd)).toEqual([8, 1]);
  });

  it("collects leg fees in causal (id) order, not array order", () => {
    // AI 自动交易的 trades.ts 由两套时钟写入；腿费归集必须与配对钉死同一个排序键（P2-4）。
    const trades = [
      trade({ id: 2, ts: 20, side: "close", price: 110, pnl: 10, fee: 3 }),
      trade({ id: 1, ts: 10, side: "long", price: 100, fee: 1 }),
    ];
    const [rt] = buildRoundTrips("BTC-USDT-SWAP", trades, 3, { costBasis: "gross" });
    expect(rt.feeUsd).toBe(4);
  });

  it("propagates the cost basis through buildLedger", () => {
    const members = [
      {
        instId: "BTC-USDT-SWAP",
        leverage: 3,
        trades: [
          trade({ id: 1, ts: 10, side: "long", price: 100, fee: 1 }),
          trade({ id: 2, ts: 20, side: "close", price: 110, pnl: 10, fee: 1 }),
        ],
      },
    ];
    expect(buildLedger(members, { costBasis: "gross" })[0].pnlUsd).toBe(8);
    expect(buildLedger(members)[0].pnlUsd).toBe(8); // 默认 gross
  });
});
