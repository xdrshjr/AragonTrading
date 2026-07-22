// TEMP repro (ai-trade-stats-missing-round-trips)：证明「因果顺序正确、但 ts 逆序」的一对开→平
// 被前端往返配对整体丢弃。数据取自生产库 aragon.db run_id=1557（ETH-USDT-SWAP / 15m）。
import { describe, expect, it } from "vitest";
import { buildRoundTrips } from "./autoTradePnl";
import type { Trade } from "./types";

const t = (over: Partial<Trade>): Trade =>
  ({
    id: 0,
    runId: 1557,
    ts: 0,
    side: "long",
    price: 0,
    qty: 0,
    fee: 0,
    pnl: 0,
    positionAfter: 0,
    reason: "",
    ...over,
  }) as Trade;

describe("ai-trade round trip pairing", () => {
  it("drops a completed trade whose close.ts precedes open.ts", () => {
    // run 1557 真实两行：开仓来自「定时唤醒」轮（wall clock 06:27:06），平仓来自「K 线」轮（bar 开盘 06:15:00）。
    const trades = [
      t({ id: 50914, ts: 1784701626, side: "long", price: 1912.94, qty: 0.82, positionAfter: 0.82 }),
      t({ id: 50915, ts: 1784700900, side: "close", price: 1913.64, qty: 0.82, pnl: 0.0574 }),
    ];
    const rts = buildRoundTrips("ETH-USDT-SWAP", trades, 5);
    expect(rts).toHaveLength(1); // 期望 1 笔完整交易；实际 0 → 统计面板整笔不显示、+0.0574 不计入
  });

  it("mis-pairs and drops when a bar-ts open follows a wall-clock close", () => {
    // run 1548 真实四行（BTC-USDT-SWAP / 15m）：50894 平(wall 17:47:05) 与 50896 开(bar 17:45:00) 逆序。
    const trades = [
      t({ id: 50892, ts: 1784654324, side: "short", price: 66409.2, qty: 0.16, positionAfter: -0.16 }),
      t({ id: 50894, ts: 1784656025, side: "close", price: 66200.0, qty: 0.16, pnl: 0.3347 }),
      t({ id: 50896, ts: 1784655900, side: "short", price: 66186.5, qty: 0.16, positionAfter: -0.16 }),
      t({ id: 50898, ts: 1784659083, side: "close", price: 66265.0, qty: 0.16, pnl: -0.1256 }),
    ];
    const rts = buildRoundTrips("BTC-USDT-SWAP", trades, 5);
    expect(rts.map((r) => [r.entry.id, r.exit.id])).toEqual([
      [50892, 50894],
      [50896, 50898],
    ]); // 实际：[[50896, 50894]] —— 入场价张冠李戴，且 50898（-0.1256）整笔丢失
  });
});
