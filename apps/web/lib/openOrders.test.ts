import { describe, expect, it } from "vitest";
import {
  buildOrderPriceLines,
  groupOrdersByPosition,
  isChartLineOrder,
  isPositionOrder,
  openOrderKindLabel,
  openOrdersKey,
  orderKindMeta,
  pctChange,
  positionKey,
} from "./openOrders";
import type { LiveOpenOrder, LivePosition } from "./tradingTypes";

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

function mockPos(over: Partial<LivePosition> = {}): LivePosition {
  return {
    instId: "BTC-USDT-SWAP",
    instType: "SWAP",
    posSide: "long",
    pos: 5,
    avgPx: 64000,
    last: 65000,
    upl: 50,
    uplRatio: 0.01,
    lever: 3,
    marginMode: "cross",
    ...over,
  };
}

describe("openOrdersKey", () => {
  it("normalizes undefined and null accountId to the same key (C2 去重契约)", () => {
    expect(openOrdersKey(undefined)).toEqual(["trading", "openOrders", null]);
    expect(openOrdersKey(undefined)).toEqual(openOrdersKey());
    expect(openOrdersKey(7)).toEqual(["trading", "openOrders", 7]);
  });
});

describe("isPositionOrder", () => {
  it("keeps limit_close / tp / sl and drops limit_open", () => {
    expect(isPositionOrder("limit_close")).toBe(true);
    expect(isPositionOrder("tp")).toBe(true);
    expect(isPositionOrder("sl")).toBe(true);
    expect(isPositionOrder("limit_open")).toBe(false);
  });
});

describe("isChartLineOrder (F-6)", () => {
  it("includes limit_open on top of the position kinds (图表线纳入开仓挂单)", () => {
    expect(isChartLineOrder("limit_close")).toBe(true);
    expect(isChartLineOrder("tp")).toBe(true);
    expect(isChartLineOrder("sl")).toBe(true);
    expect(isChartLineOrder("limit_open")).toBe(true);
  });
});

describe("openOrderKindLabel (F-5 页签中文标签)", () => {
  it("maps the four kinds to tab labels distinct from chart-line short labels", () => {
    expect(openOrderKindLabel("limit_open")).toBe("限价开仓");
    expect(openOrderKindLabel("limit_close")).toBe("限价平仓");
    expect(openOrderKindLabel("tp")).toBe("止盈委托");
    expect(openOrderKindLabel("sl")).toBe("止损委托");
  });
});

describe("orderKindMeta — limit_open explicit branch (F-6, 评审 R-3)", () => {
  it("uses the existing --muted theme var (never the nonexistent --accent-2)", () => {
    const meta = orderKindMeta("limit_open");
    expect(meta.label).toBe("开仓挂单");
    expect(meta.colorVar).toBe("--muted");
    expect(meta.lineStyle).toBe(2);
  });

  it("keeps the three existing kinds' colors distinct and unchanged", () => {
    expect(orderKindMeta("tp").colorVar).toBe("--long");
    expect(orderKindMeta("sl").colorVar).toBe("--short");
    expect(orderKindMeta("limit_close").colorVar).toBe("--accent");
  });
});

describe("groupOrdersByPosition", () => {
  it("groups orders by (instId, posSide) and drops limit_open / unmatched positions", () => {
    const positions = [mockPos(), mockPos({ instId: "ETH-USDT-SWAP", posSide: "short" })];
    const orders = [
      mockOrder({ ordId: "a" }), // BTC long → 命中
      mockOrder({ kind: "tp", instId: "BTC-USDT-SWAP", posSide: "long", ordId: null, algoId: "t1" }),
      mockOrder({ kind: "limit_open", ordId: "b" }), // 过滤
      mockOrder({ instId: "SOL-USDT-SWAP", ordId: "c" }), // 无对应持仓 → 丢弃
      mockOrder({ kind: "sl", instId: "ETH-USDT-SWAP", posSide: "short", ordId: null, algoId: "s1" }),
    ];
    const grouped = groupOrdersByPosition(orders, positions);
    expect(grouped.get(positionKey("BTC-USDT-SWAP", "long"))?.map((o) => o.ordId ?? o.algoId)).toEqual([
      "a",
      "t1",
    ]);
    expect(grouped.get(positionKey("ETH-USDT-SWAP", "short"))?.length).toBe(1);
    expect(grouped.has(positionKey("SOL-USDT-SWAP", "long"))).toBe(false);
  });

  it("supports net mode posSide", () => {
    const positions = [mockPos({ posSide: "net" })];
    const orders = [mockOrder({ posSide: "net" })];
    const grouped = groupOrdersByPosition(orders, positions);
    expect(grouped.get(positionKey("BTC-USDT-SWAP", "net"))?.length).toBe(1);
  });
});

describe("pctChange", () => {
  it("computes a signed ratio against a positive reference price", () => {
    expect(pctChange(66000, 60000)).toBeCloseTo(0.1);
    expect(pctChange(54000, 60000)).toBeCloseTo(-0.1);
  });

  it("returns null when refPx is non-positive or null", () => {
    expect(pctChange(66000, 0)).toBeNull();
    expect(pctChange(66000, null)).toBeNull();
    expect(pctChange(66000, -5)).toBeNull();
  });
});

describe("buildOrderPriceLines", () => {
  it("keeps current instId chart-line orders incl. limit_open (F-6), price = triggerPx ?? px", () => {
    const orders = [
      mockOrder({ ordId: "1001", px: 64000, triggerPx: null }),
      mockOrder({ kind: "tp", ordId: null, algoId: "987", px: null, triggerPx: 70000 }),
      mockOrder({ kind: "limit_open", ordId: "open1", px: 60000, reduceOnly: false }), // F-6 起纳入
      mockOrder({ instId: "ETH-USDT-SWAP", ordId: "eth1" }), // 非当前 instId
    ];
    const lines = buildOrderPriceLines(orders, "BTC-USDT-SWAP", 65000);
    expect(lines.map((l) => l.id)).toEqual(["1001:limit_close", "987:tp", "open1:limit_open"]);
    expect(lines[0].price).toBe(64000);
    expect(lines[1].price).toBe(70000);
    expect(lines[0].changePct).toBeCloseTo((64000 - 65000) / 65000);
    expect(lines[2].price).toBe(60000);
    expect(lines[2].label).toBe("开仓挂单");
    expect(lines[2].colorVar).toBe("--muted");
  });

  it("gives OCO tp/sl distinct ids that do not collide (shared algoId)", () => {
    const orders = [
      mockOrder({ kind: "tp", ordId: null, algoId: "987", px: null, triggerPx: 70000 }),
      mockOrder({ kind: "sl", ordId: null, algoId: "987", px: 57900, triggerPx: 58000 }),
    ];
    const lines = buildOrderPriceLines(orders, "BTC-USDT-SWAP", 65000);
    expect(lines.map((l) => l.id)).toEqual(["987:tp", "987:sl"]);
    expect(new Set(lines.map((l) => l.id)).size).toBe(2);
  });
});
