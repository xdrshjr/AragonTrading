import { describe, expect, it } from "vitest";
import type { IChartApi } from "lightweight-charts";
import { reconcilePlots, type PlotSeries } from "./plots";

describe("reconcilePlots", () => {
  it("hides_series_titles_by_default", () => {
    const chart = makeChart();
    const map = new Map<string, PlotSeries>();

    reconcilePlots(chart.api, map, [
      {
        kind: "line",
        id: "ma:5",
        title: "MA5",
        color: "#fff",
        data: [],
      },
    ]);

    expect(chart.lineOptions[0].title).toBe("");
  });

  it("keeps_series_titles_when_enabled", () => {
    const chart = makeChart();
    const map = new Map<string, PlotSeries>();

    reconcilePlots(
      chart.api,
      map,
      [
        {
          kind: "histogram",
          id: "macd:hist",
          title: "MACD",
          data: [],
        },
      ],
      { showSeriesTitle: true },
    );

    expect(chart.histogramOptions[0].title).toBe("MACD");
  });
});

function makeChart(): {
  api: IChartApi;
  lineOptions: Record<string, unknown>[];
  histogramOptions: Record<string, unknown>[];
} {
  const lineOptions: Record<string, unknown>[] = [];
  const histogramOptions: Record<string, unknown>[] = [];
  const series = {
    applyOptions: () => undefined,
    setData: () => undefined,
  } as unknown as PlotSeries;

  return {
    api: {
      addLineSeries: (options: Record<string, unknown>) => {
        lineOptions.push(options);
        return series;
      },
      addHistogramSeries: (options: Record<string, unknown>) => {
        histogramOptions.push(options);
        return series;
      },
      removeSeries: () => undefined,
    } as unknown as IChartApi,
    lineOptions,
    histogramOptions,
  };
}
