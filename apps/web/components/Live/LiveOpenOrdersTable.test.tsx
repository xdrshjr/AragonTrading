import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { tradingApi } from "@/lib/tradingApi";
import type { LiveOpenOrder } from "@/lib/tradingTypes";
import LiveOpenOrdersTable, { OpenOrdersTableView, sortOpenOrders } from "./LiveOpenOrdersTable";

vi.mock("@/lib/tradingApi", () => ({
  tradingApi: { openOrders: vi.fn() },
}));

function mockOrder(over: Partial<LiveOpenOrder> = {}): LiveOpenOrder {
  return {
    kind: "limit_open",
    instId: "BTC-USDT-SWAP",
    posSide: "long",
    side: "buy",
    px: 60000,
    triggerPx: null,
    sz: 1,
    state: "live",
    reduceOnly: false,
    ordId: "1003",
    algoId: null,
    createdTs: 1719500002,
    ...over,
  };
}

afterEach(cleanup);

describe("OpenOrdersTableView — 四类 kind 全量展示（F-5）", () => {
  it("renders all four kinds unfiltered with their Chinese labels", () => {
    const orders = [
      mockOrder({ ordId: "a1" }),
      mockOrder({ kind: "limit_close", ordId: "a2", side: "sell", reduceOnly: true, px: 64000 }),
      mockOrder({ kind: "tp", ordId: null, algoId: "t1", px: null, triggerPx: 70000, side: "sell" }),
      mockOrder({ kind: "sl", ordId: null, algoId: "t1", px: 57900, triggerPx: 58000, side: "sell" }),
    ];
    render(<OpenOrdersTableView orders={orders} instId="BTC-USDT-SWAP" />);
    expect(screen.getByText("限价开仓")).toBeTruthy();
    expect(screen.getByText("限价平仓")).toBeTruthy();
    expect(screen.getByText("止盈委托")).toBeTruthy();
    expect(screen.getByText("止损委托")).toBeTruthy();
  });

  it("shows attach TP/SL trigger prices for a pending limit-open order (F-4 列)", () => {
    const orders = [
      mockOrder({ ordId: "a1", attachTpTriggerPx: 70000, attachSlTriggerPx: 58000 }),
    ];
    render(<OpenOrdersTableView orders={orders} instId="BTC-USDT-SWAP" />);
    expect(screen.getByText("随单止盈")).toBeTruthy();
    expect(screen.getByText("随单止损")).toBeTruthy();
    expect(screen.getByText("70,000.0")).toBeTruthy();
    expect(screen.getByText("58,000.0")).toBeTruthy();
  });

  it("renders '—' for missing attach fields and '市价' for triggered-then-market tp px", () => {
    const orders = [
      mockOrder({ kind: "tp", ordId: null, algoId: "t9", px: null, triggerPx: 70000, side: "sell" }),
    ];
    render(<OpenOrdersTableView orders={orders} instId="BTC-USDT-SWAP" />);
    expect(screen.getByText("市价")).toBeTruthy(); // tp 触发后市价（px=null）
    expect(screen.getAllByText("—").length).toBeGreaterThanOrEqual(2); // 随单止盈/止损缺失
  });

  it("localizes known states and passes unknown states through untouched", () => {
    const orders = [
      mockOrder({ ordId: "s1", state: "live" }),
      mockOrder({ ordId: "s2", state: "partially_filled", createdTs: 1719500003 }),
      mockOrder({ ordId: "s3", state: "future_state", createdTs: 1719500004 }),
    ];
    render(<OpenOrdersTableView orders={orders} instId="BTC-USDT-SWAP" />);
    expect(screen.getByText("已挂出")).toBeTruthy();
    expect(screen.getByText("部分成交")).toBeTruthy();
    expect(screen.getByText("future_state")).toBeTruthy(); // 未知值不臆造
  });
});

describe("sortOpenOrders — 复合次序（评审 R-5）", () => {
  it("puts current-instId rows first, then createdTs desc within each group", () => {
    const orders = [
      mockOrder({ instId: "ETH-USDT-SWAP", ordId: "e1", createdTs: 300 }),
      mockOrder({ ordId: "b1", createdTs: 100 }),
      mockOrder({ ordId: "b2", createdTs: 200 }),
      mockOrder({ instId: "ETH-USDT-SWAP", ordId: "e2", createdTs: 400 }),
    ];
    const sorted = sortOpenOrders(orders, "BTC-USDT-SWAP");
    expect(sorted.map((o) => o.ordId)).toEqual(["b2", "b1", "e2", "e1"]);
  });

  it("falls back to pure createdTs desc when no current instId is given", () => {
    const orders = [
      mockOrder({ ordId: "x1", createdTs: 100 }),
      mockOrder({ ordId: "x2", createdTs: 200 }),
    ];
    expect(sortOpenOrders(orders).map((o) => o.ordId)).toEqual(["x2", "x1"]);
  });
});

describe("LiveOpenOrdersTable — query states", () => {
  function renderTable() {
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    return render(
      <QueryClientProvider client={qc}>
        <LiveOpenOrdersTable instId="BTC-USDT-SWAP" />
      </QueryClientProvider>,
    );
  }

  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("shows the loading text while the first fetch is pending", () => {
    vi.mocked(tradingApi.openOrders).mockReturnValue(new Promise(() => {}));
    renderTable();
    expect(screen.getByText(/加载委托/)).toBeTruthy();
  });

  it("shows the empty-state text when there are no open orders", async () => {
    vi.mocked(tradingApi.openOrders).mockResolvedValue({ orders: [] });
    renderTable();
    expect(await screen.findByText(/当前无未结委托/)).toBeTruthy();
  });

  it("shows an explicit error state when the fetch fails (C1 绝不假数据)", async () => {
    vi.mocked(tradingApi.openOrders).mockRejectedValue(new Error("鉴权失败"));
    renderTable();
    expect(await screen.findByText(/加载委托失败/)).toBeTruthy();
  });

  it("renders fetched orders through the view", async () => {
    vi.mocked(tradingApi.openOrders).mockResolvedValue({
      orders: [mockOrder({ ordId: "r1", attachTpTriggerPx: 70000 })],
    });
    renderTable();
    expect(await screen.findByText("限价开仓")).toBeTruthy();
    expect(screen.getByText("BTC-USDT-SWAP")).toBeTruthy();
  });
});
