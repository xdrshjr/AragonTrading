import { cleanup, render } from "@testing-library/react";
import { createChart } from "lightweight-charts";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createSyncGroup } from "@/lib/chartSync";
import type { IndicatorDef } from "@/lib/indicators";
import type { Candle } from "@/lib/types";
import IndicatorPane from "./IndicatorPane";

// 在 lightweight-charts 边界处打桩：捕获 createChart 收到的 options，并返回一个最小可用的 chart 桩，
// 以断言 IndicatorPane 传给库的左价轴可见性。jsdom 无真实布局/canvas，无法复现像素错位本身，
// 故回归点收口在「副图左价轴可见性必须跟随主图（showLeftAxis）」这一根因契约上
// （chart-indicator-zoom-misalignment §B：恒显空左轴 → 固定一段距离横向错位）。
vi.mock("lightweight-charts", () => ({
  createChart: vi.fn(),
  ColorType: { Solid: "solid" },
  CrosshairMode: { Normal: 0 },
}));

type ChartStub = { applyOptions: ReturnType<typeof vi.fn> };
type SeriesStub = { setData: ReturnType<typeof vi.fn> };

const createdCharts: ChartStub[] = [];
// 捕获本图所有系列的 setData 入参，用于在数据层断言副图时间轴是否逐根覆盖全量 candle（锚系列回归点）。
const createdSeries: SeriesStub[] = [];

function makeSeriesStub() {
  const stub = {
    setData: vi.fn(),
    applyOptions: () => {},
    createPriceLine: () => ({}),
    removePriceLine: () => {},
  };
  createdSeries.push(stub);
  return stub;
}

function makeChartStub(): ChartStub {
  const stub = {
    timeScale: () => ({
      subscribeVisibleLogicalRangeChange: () => {},
      unsubscribeVisibleLogicalRangeChange: () => {},
      getVisibleLogicalRange: () => null,
      setVisibleLogicalRange: () => {},
    }),
    subscribeCrosshairMove: () => {},
    unsubscribeCrosshairMove: () => {},
    priceScale: () => ({ width: () => 64, applyOptions: () => {} }),
    applyOptions: vi.fn(),
    addLineSeries: () => makeSeriesStub(),
    addHistogramSeries: () => makeSeriesStub(),
    removeSeries: () => {},
    remove: () => {},
  };
  createdCharts.push(stub);
  return stub;
}

// 最小副图定义：compute 返回空 plots，避免触碰系列细节，聚焦左价轴可见性这一回归点。
const paneDef = {
  key: "macd",
  label: "MACD",
  description: "",
  category: "pane",
  defaultOn: false,
  paramsHint: "",
  compute: () => [],
} as unknown as IndicatorDef;

function renderPane(showLeftAxis: boolean) {
  return render(
    <IndicatorPane
      candles={[]}
      def={paneDef}
      theme="dark"
      sync={createSyncGroup()}
      showTimeAxis
      showLeftAxis={showLeftAxis}
    />,
  );
}

function lastCreateChartOptions() {
  const calls = vi.mocked(createChart).mock.calls;
  return calls[calls.length - 1]?.[1] as { leftPriceScale?: { visible?: boolean } };
}

describe("IndicatorPane left price-scale visibility", () => {
  beforeEach(() => {
    createdCharts.length = 0;
    createdSeries.length = 0;
    vi.mocked(createChart).mockReset();
    vi.mocked(createChart).mockImplementation(() => makeChartStub() as never);
  });

  afterEach(() => {
    cleanup();
  });

  it("hides its (empty) left axis when the main chart hides its left axis (no equity), preventing a constant horizontal offset", () => {
    // 旧实现把 leftPriceScale.visible 硬编码为 true：副图恒显空左轴 → 比隐藏左轴的主图多预留一段宽 →
    // 与缩放无关的固定横向错位。修复后必须随 showLeftAxis=false 一并隐藏。
    renderPane(false);
    expect(lastCreateChartOptions().leftPriceScale?.visible).toBe(false);
  });

  it("shows its left axis when the main chart shows its left axis (equity present) so widths can be equalized", () => {
    renderPane(true);
    expect(lastCreateChartOptions().leftPriceScale?.visible).toBe(true);
  });

  it("updates left-axis visibility when equity appears/disappears mid-session", () => {
    const { rerender } = renderPane(false);
    const chart = createdCharts[createdCharts.length - 1];
    rerender(
      <IndicatorPane
        candles={[]}
        def={paneDef}
        theme="dark"
        sync={createSyncGroup()}
        showTimeAxis
        showLeftAxis
      />,
    );
    expect(chart.applyOptions).toHaveBeenCalledWith({ leftPriceScale: { visible: true } });
  });
});

// chart-indicator-constant-bar-offset 回归点：副图指标系列丢弃暖机前导点后，其首个时间点晚于主图
// candle[0]；横轴仅按 logical index 同步（setVisibleLogicalRange），两图 index 0 时间戳不同即产生
// 与缩放无关的固定 W 根横向错位、十字光标贯穿同样偏移。修复（A-2）挂一条「全域 whitespace 锚系列」
// 逐根覆盖全部 candle，使副图 index 0 = candle[0]。jsdom 无真实布局/canvas、无法复现像素错位，故回归
// 点收口在数据层契约「副图存在一条系列其时间点集逐根等于输入 candles 的 ts」上。
describe("IndicatorPane time-axis origin alignment (constant bar offset)", () => {
  beforeEach(() => {
    createdCharts.length = 0;
    createdSeries.length = 0;
    vi.mocked(createChart).mockReset();
    vi.mocked(createChart).mockImplementation(() => makeChartStub() as never);
  });

  afterEach(() => {
    cleanup();
  });

  function makeCandles(tss: number[]): Candle[] {
    return tss.map((ts) => ({ ts, o: 1, h: 1, l: 1, c: 1, vol: 1 }));
  }

  // 取本图所有系列在最后一轮 setData 收到的时间序列。
  function seriesTimeArrays(): number[][] {
    return createdSeries.map((s) => {
      const last = s.setData.mock.calls.at(-1)?.[0] as { time: number }[] | undefined;
      return (last ?? []).map((p) => p.time);
    });
  }

  it("anchors the pane time axis to the full candle domain so its index 0 equals candle[0] (no constant bar offset)", () => {
    const tss = [1000, 2000, 3000, 4000, 5000];
    const candles = makeCandles(tss);
    // 模拟暖机丢弃：compute 返回的指标线从 candle[2] 起绘（前两根被丢弃），重现「指标系列短于 candles」。
    const warmupDef = {
      ...paneDef,
      compute: (cs: Candle[]) => [
        {
          kind: "line",
          id: "warm",
          title: "",
          color: "#fff",
          data: cs.slice(2).map((c) => ({ time: c.ts, value: 1 })),
        },
      ],
    } as unknown as IndicatorDef;

    render(
      <IndicatorPane
        candles={candles}
        def={warmupDef}
        theme="dark"
        sync={createSyncGroup()}
        showTimeAxis
        showLeftAxis={false}
      />,
    );

    // 必须存在一条系列（锚系列）其时间点逐根等于全量 candles 的 ts：首点 == candles[0].ts、长度一致。
    const full = seriesTimeArrays().find(
      (t) => t.length === candles.length && t[0] === candles[0].ts,
    );
    expect(full).toBeDefined();
    expect(full).toEqual(tss);
  });

  it("keeps the anchor covering the full domain when candles update (live append / history load)", () => {
    const initial = makeCandles([2000, 3000, 4000]);
    const { rerender } = render(
      <IndicatorPane
        candles={initial}
        def={paneDef}
        theme="dark"
        sync={createSyncGroup()}
        showTimeAxis
        showLeftAxis={false}
      />,
    );

    const grown = makeCandles([1000, 2000, 3000, 4000, 5000]);
    rerender(
      <IndicatorPane
        candles={grown}
        def={paneDef}
        theme="dark"
        sync={createSyncGroup()}
        showTimeAxis
        showLeftAxis={false}
      />,
    );

    const full = seriesTimeArrays().find(
      (t) => t.length === grown.length && t[0] === grown[0].ts,
    );
    expect(full).toEqual([1000, 2000, 3000, 4000, 5000]);
  });
});
