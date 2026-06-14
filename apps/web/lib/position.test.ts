import { describe, expect, it } from "vitest";
import { deriveOpenPosition } from "./position";
import type { Trade } from "./types";

function trade(partial: Partial<Trade> & Pick<Trade, "ts" | "side" | "price" | "qty">): Trade {
  return {
    fee: 0,
    pnl: 0,
    positionAfter: 0,
    reason: "",
    ...partial,
  };
}

describe("deriveOpenPosition", () => {
  it("rejects_empty_trades", () => {
    expect(deriveOpenPosition([], 100)).toBeNull();
  });

  it("derives_long_after_open", () => {
    const pos = deriveOpenPosition([trade({ ts: 1, side: "long", price: 100, qty: 2 })], null);
    expect(pos).not.toBeNull();
    expect(pos!.side).toBe("long");
    expect(pos!.size).toBeCloseTo(2);
    expect(pos!.avgEntry).toBeCloseTo(100);
  });

  it("averages_entry_on_add", () => {
    // 前瞻护栏：当前后端整开整平不产生此序列，但 derive 须正确加权 (100*2+110*2)/4=105。
    const pos = deriveOpenPosition(
      [
        trade({ ts: 1, side: "long", price: 100, qty: 2 }),
        trade({ ts: 2, side: "long", price: 110, qty: 2 }),
      ],
      null,
    );
    expect(pos!.size).toBeCloseTo(4);
    expect(pos!.avgEntry).toBeCloseTo(105);
  });

  it("reduces_on_partial_close", () => {
    // 前瞻护栏：部分平仓减量、均价不变。
    const pos = deriveOpenPosition(
      [
        trade({ ts: 1, side: "long", price: 100, qty: 4 }),
        trade({ ts: 2, side: "close", price: 130, qty: 1 }),
      ],
      null,
    );
    expect(pos!.size).toBeCloseTo(3);
    expect(pos!.avgEntry).toBeCloseTo(100);
  });

  it("returns_null_when_flat", () => {
    const pos = deriveOpenPosition(
      [
        trade({ ts: 1, side: "long", price: 100, qty: 2 }),
        trade({ ts: 2, side: "close", price: 120, qty: 2 }),
      ],
      120,
    );
    expect(pos).toBeNull();
  });

  it("computes_unrealized_pnl_sign", () => {
    const long = deriveOpenPosition([trade({ ts: 1, side: "long", price: 100, qty: 2 })], 110);
    expect(long!.unrealizedPnl).toBeCloseTo(20); // (110-100)*2
    expect(long!.unrealizedPnl! > 0).toBe(true);

    const short = deriveOpenPosition([trade({ ts: 1, side: "short", price: 100, qty: 2 })], 90);
    expect(short!.unrealizedPnl).toBeCloseTo(20); // (90-100)*-2
    expect(short!.unrealizedPnl! > 0).toBe(true);
  });

  it("respects_asOfTs", () => {
    const rows = [
      trade({ ts: 1, side: "long", price: 100, qty: 2 }),
      trade({ ts: 5, side: "close", price: 120, qty: 2 }),
    ];
    expect(deriveOpenPosition(rows, 120)).toBeNull(); // 全序列已平
    const mid = deriveOpenPosition(rows, 120, 3); // 截断到平仓前
    expect(mid!.side).toBe("long");
    expect(mid!.size).toBeCloseTo(2);
  });

  it("agrees_with_positionAfter_sign", () => {
    // 整开整平真实序列：方向 / 持仓量须与末笔 positionAfter（+多/−空/0 平）一致。
    const openLong = [trade({ ts: 1, side: "long", price: 100, qty: 3, positionAfter: 3 })];
    const posL = deriveOpenPosition(openLong, 100);
    expect(Math.sign(posL!.side === "long" ? posL!.size : -posL!.size)).toBe(
      Math.sign(openLong.at(-1)!.positionAfter),
    );
    expect(posL!.size).toBeCloseTo(Math.abs(openLong.at(-1)!.positionAfter));

    const openShort = [trade({ ts: 1, side: "short", price: 100, qty: 3, positionAfter: -3 })];
    const posS = deriveOpenPosition(openShort, 100);
    expect(posS!.side).toBe("short");
    expect(posS!.size).toBeCloseTo(Math.abs(openShort.at(-1)!.positionAfter));

    const closed = [
      trade({ ts: 1, side: "long", price: 100, qty: 3, positionAfter: 3 }),
      trade({ ts: 2, side: "close", price: 120, qty: 3, positionAfter: 0 }),
    ];
    expect(deriveOpenPosition(closed, 120)).toBeNull(); // 末笔 positionAfter=0 ↔ 无持仓
  });
});
