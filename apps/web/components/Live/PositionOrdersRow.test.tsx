import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import type { LiveOpenOrder } from "@/lib/tradingTypes";
import PositionOrdersRow from "./PositionOrdersRow";

afterEach(cleanup);

function mockOrder(over: Partial<LiveOpenOrder> = {}): LiveOpenOrder {
  return {
    kind: "limit_close",
    instId: "BTC-USDT-SWAP",
    posSide: "long",
    side: "sell",
    px: 64000,
    triggerPx: null,
    sz: 3,
    state: "live",
    reduceOnly: true,
    ordId: "1001",
    algoId: null,
    createdTs: 1719500000,
    ...over,
  };
}

describe("PositionOrdersRow", () => {
  it("renders an empty-state hint when there are no orders", () => {
    render(<PositionOrdersRow orders={[]} refPx={65000} />);
    expect(screen.getByText(/暂无止盈/)).toBeTruthy();
  });

  it("renders one detail line per order with its kind label", () => {
    const orders = [
      mockOrder({ kind: "limit_close", ordId: "1001", px: 64000 }),
      mockOrder({ kind: "tp", ordId: null, algoId: "t1", px: null, triggerPx: 70000 }),
      mockOrder({ kind: "sl", ordId: null, algoId: "s1", px: 57900, triggerPx: 58000 }),
    ];
    render(<PositionOrdersRow orders={orders} refPx={65000} />);
    expect(screen.getByText("平仓")).toBeTruthy();
    expect(screen.getByText("止盈")).toBeTruthy();
    expect(screen.getByText("止损")).toBeTruthy();
  });

  it("colors the change pct green above ref and red below ref", () => {
    const orders = [
      mockOrder({ kind: "tp", ordId: null, algoId: "t1", px: null, triggerPx: 70000 }), // 高于现价 → 涨
      mockOrder({ kind: "sl", ordId: null, algoId: "s1", px: 57900, triggerPx: 58000 }), // 低于现价 → 跌
    ];
    render(<PositionOrdersRow orders={orders} refPx={65000} />);
    const up = screen.getByText(/\+7\.69%/);
    const down = screen.getByText(/-10\.77%/);
    expect(up.getAttribute("style")).toContain("var(--long)");
    expect(down.getAttribute("style")).toContain("var(--short)");
  });

  it("shows 市价 when an order has no limit/trigger price", () => {
    const orders = [mockOrder({ kind: "tp", ordId: null, algoId: "t1", px: null, triggerPx: null })];
    render(<PositionOrdersRow orders={orders} refPx={65000} />);
    expect(screen.getByText("市价")).toBeTruthy();
  });
});
