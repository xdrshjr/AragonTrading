import "@testing-library/jest-dom/vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "@/lib/api";
import type { Candle, Trade } from "@/lib/types";
import TradeCandleChartPanel from "./TradeCandleChartPanel";

// 纯图组件经 next/dynamic(ssr:false) 引入；测试内以 stub 替代（规避 lightweight-charts 在 jsdom 的异步加载不确定性）。
// stub 把收到的 candles/trades 数量落到 data-* 上，供断言「裁剪后的窗口 + trades=[entry,exit]」实际下传。
vi.mock("next/dynamic", () => ({
  default: () => (props: { candles: Candle[]; trades: Trade[] }) => (
    <div
      data-testid="trade-candle-chart"
      data-candles={String(props.candles.length)}
      data-trades={String(props.trades.length)}
    />
  ),
}));
vi.mock("@/lib/api", () => ({ api: { candlesWithMeta: vi.fn() } }));

const candlesMock = vi.mocked(api.candlesWithMeta);

// 往返：1H 周期，开仓 ts=1000 @100（多），平仓 ts=4600 @110 pnl+12。
const ENTRY: Trade = {
  id: 1, runId: 1, ts: 1000, side: "long", price: 100, qty: 1, fee: 0,
  pnl: 0, positionAfter: 1, reason: "", explain: null,
};
const EXIT: Trade = {
  id: 2, runId: 1, ts: 4600, side: "close", price: 110, qty: 1, fee: 0,
  pnl: 12, pnlPct: 0.1, positionAfter: 0, reason: "", explain: null,
};

function candle(ts: number): Candle {
  return { ts, o: 100, h: 101, l: 99, c: 100, vol: 1 };
}

function wrap(node: ReactNode, client: QueryClient) {
  return render(<QueryClientProvider client={client}>{node}</QueryClientProvider>);
}

function newClient() {
  return new QueryClient({ defaultOptions: { queries: { retry: false } } });
}

beforeEach(() => {
  candlesMock.mockReset();
});
afterEach(cleanup);

describe("TradeCandleChartPanel (ai-trade-stats-two-column-and-kline §3.4)", () => {
  it("fetches the round-trip window on mount and renders the chart with cropped candles + [entry,exit]", async () => {
    // startTs=1000-21600=-20600, endTs=4600+21600=26200；返回含两根越界 K 线，断言被客户端裁剪掉。
    candlesMock.mockResolvedValue({
      source: "live",
      candles: [candle(-30_000), candle(1000), candle(4600), candle(30_000)],
    });
    wrap(<TradeCandleChartPanel instId="BTC-USDT-SWAP" bar="1H" entry={ENTRY} exit={EXIT} />, newClient());

    await waitFor(() => expect(screen.getByTestId("trade-candle-chart")).toBeInTheDocument());
    // 拉取参数：(instId, bar, limit, before=endTs, after=startTs)。
    expect(candlesMock).toHaveBeenCalledWith("BTC-USDT-SWAP", "1H", 20, 26_200, -20_600);
    const chart = screen.getByTestId("trade-candle-chart");
    expect(chart).toHaveAttribute("data-candles", "2"); // 越界两根被裁剪
    expect(chart).toHaveAttribute("data-trades", "2"); // 恰好 [entry, exit]
  });

  it("shows a loading skeleton before the window resolves", () => {
    candlesMock.mockReturnValue(new Promise(() => {})); // 永不 resolve
    const { container } = wrap(
      <TradeCandleChartPanel instId="BTC-USDT-SWAP" bar="1H" entry={ENTRY} exit={EXIT} />,
      newClient(),
    );
    expect(container.querySelector(".skeleton")).not.toBeNull();
    expect(screen.queryByTestId("trade-candle-chart")).toBeNull();
  });

  it("degrades gracefully when the window comes back empty", async () => {
    candlesMock.mockResolvedValue({ source: "live", candles: [] });
    wrap(<TradeCandleChartPanel instId="BTC-USDT-SWAP" bar="1H" entry={ENTRY} exit={EXIT} />, newClient());

    expect(await screen.findByText(/该时段 K 线暂不可用/)).toBeInTheDocument();
    expect(screen.queryByTestId("trade-candle-chart")).toBeNull();
  });

  it("degrades gracefully (does not throw) when the fetch rejects", async () => {
    candlesMock.mockRejectedValue(new Error("okx history unavailable"));
    wrap(<TradeCandleChartPanel instId="BTC-USDT-SWAP" bar="1H" entry={ENTRY} exit={EXIT} />, newClient());

    expect(await screen.findByText(/该时段 K 线暂不可用/)).toBeInTheDocument();
  });

  it("hits the immutable cache on re-mount with the same window (no duplicate request)", async () => {
    candlesMock.mockResolvedValue({ source: "live", candles: [candle(1000), candle(4600)] });
    const client = newClient(); // 共享 client → staleTime:Infinity 命中缓存

    const first = wrap(
      <TradeCandleChartPanel instId="BTC-USDT-SWAP" bar="1H" entry={ENTRY} exit={EXIT} />,
      client,
    );
    await waitFor(() => expect(screen.getByTestId("trade-candle-chart")).toBeInTheDocument());
    expect(candlesMock).toHaveBeenCalledTimes(1);
    first.unmount();

    wrap(<TradeCandleChartPanel instId="BTC-USDT-SWAP" bar="1H" entry={ENTRY} exit={EXIT} />, client);
    await waitFor(() => expect(screen.getByTestId("trade-candle-chart")).toBeInTheDocument());
    expect(candlesMock).toHaveBeenCalledTimes(1); // 同一往返窗口不重复请求
  });
});
