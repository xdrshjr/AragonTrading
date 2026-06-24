import { cleanup, render } from "@testing-library/react";
import { createChart } from "lightweight-charts";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { IndicatorKey } from "@/lib/indicators";
import type { Candle } from "@/lib/types";
import CandleChart from "./CandleChart";

// 在 lightweight-charts 边界处打桩：jsdom 无真实布局 / canvas，无法复现像素级「K 线落在可视价窗外」本身，
// 故回归点收口在根因契约上——切标的（instId/bar 变化）时主图右价轴必须被 applyOptions({autoScale:true})
// 复位，让随后的 setData 重新按新标的价量级拟合（chart-candles-disappear-on-symbol-switch）。
vi.mock("lightweight-charts", () => ({
  createChart: vi.fn(),
  ColorType: { Solid: "solid" },
  CrosshairMode: { Normal: 0 },
}));

// 捕获主图右价轴（蜡烛系列所在）applyOptions 的入参，用于断言换标的时 autoScale 被复位为 true。
let rightPriceScaleApplyOptions: ReturnType<typeof vi.fn>;

function makeCandleSeriesStub() {
  return {
    setData: vi.fn(),
    setMarkers: () => {},
    createPriceLine: () => ({}),
    removePriceLine: () => {},
    applyOptions: () => {},
    priceScale: () => ({ applyOptions: rightPriceScaleApplyOptions }),
  };
}

function makeLineSeriesStub() {
  return {
    setData: () => {},
    applyOptions: () => {},
    priceScale: () => ({ applyOptions: vi.fn() }),
  };
}

function makeChartStub() {
  return {
    timeScale: () => ({
      subscribeVisibleLogicalRangeChange: () => {},
      unsubscribeVisibleLogicalRangeChange: () => {},
      getVisibleLogicalRange: () => null,
      setVisibleLogicalRange: () => {},
      fitContent: () => {},
    }),
    subscribeCrosshairMove: () => {},
    unsubscribeCrosshairMove: () => {},
    priceScale: () => ({ width: () => 64, applyOptions: () => {} }),
    applyOptions: () => {},
    addCandlestickSeries: () => makeCandleSeriesStub(),
    addLineSeries: () => makeLineSeriesStub(),
    addHistogramSeries: () => makeLineSeriesStub(),
    removeSeries: () => {},
    remove: () => {},
  };
}

const NO_INDICATORS = {} as Record<IndicatorKey, boolean>;

// 围绕 base 价构造 3 根 K 线，用于模拟不同价格量级的标的（BTC≈6 万 vs DOGE≈0.1）。
function makeCandles(base: number): Candle[] {
  return [0, 1, 2].map((i) => ({
    ts: 1000 + i * 60,
    o: base,
    h: base,
    l: base,
    c: base,
    vol: 1,
  }));
}

function renderChart(instId: string, candles: Candle[]) {
  return render(
    <CandleChart candles={candles} indicators={NO_INDICATORS} theme="dark" instId={instId} bar="1m" />,
  );
}

describe("CandleChart price-axis autoScale reset on symbol switch", () => {
  beforeEach(() => {
    rightPriceScaleApplyOptions = vi.fn();
    vi.mocked(createChart).mockReset();
    vi.mocked(createChart).mockImplementation(() => makeChartStub() as never);
  });

  afterEach(() => {
    cleanup();
  });

  it("resets the right price axis autoScale to true when the instrument changes, so a far-apart price magnitude re-fits instead of disappearing", () => {
    const { rerender } = renderChart("BTC-USDT-SWAP", makeCandles(60000));
    rightPriceScaleApplyOptions.mockClear(); // 忽略首屏复位，仅关注「切标的」这一离散事件

    rerender(
      <CandleChart
        candles={makeCandles(0.1)}
        indicators={NO_INDICATORS}
        theme="dark"
        instId="DOGE-USDT-SWAP"
        bar="1m"
      />,
    );

    expect(rightPriceScaleApplyOptions).toHaveBeenCalledWith({ autoScale: true });
  });

  it("does not reset autoScale on intra-symbol candle updates so real-time ticks keep the user's manual zoom", () => {
    const { rerender } = renderChart("BTC-USDT-SWAP", makeCandles(60000));
    rightPriceScaleApplyOptions.mockClear();

    // 同一标的内 candles 变化（实时 tick）：instId/bar 不变 → 复位 effect 不应触发（避免方案 B 的体验回归）。
    rerender(
      <CandleChart
        candles={makeCandles(61000)}
        indicators={NO_INDICATORS}
        theme="dark"
        instId="BTC-USDT-SWAP"
        bar="1m"
      />,
    );

    expect(rightPriceScaleApplyOptions).not.toHaveBeenCalled();
  });

  it("resets autoScale again when switching period (bar) on the same instrument", () => {
    const { rerender } = renderChart("BTC-USDT-SWAP", makeCandles(60000));
    rightPriceScaleApplyOptions.mockClear();

    rerender(
      <CandleChart
        candles={makeCandles(60000)}
        indicators={NO_INDICATORS}
        theme="dark"
        instId="BTC-USDT-SWAP"
        bar="1H"
      />,
    );

    expect(rightPriceScaleApplyOptions).toHaveBeenCalledWith({ autoScale: true });
  });
});
