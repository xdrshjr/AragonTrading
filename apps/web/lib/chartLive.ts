import type { Bar, ChartView, Mode } from "./types";

export type ChartRange = {
  instId: string;
  bar: string;
  startTs: number;
  endTs: number;
};

export const LATEST_CANDLE_LIMIT = 3;

export function resolveChartRange({
  chartView,
  mode,
  runRange,
  instId,
  bar,
  backtestStartTs,
  backtestEndTs,
}: {
  chartView: ChartView;
  mode: Mode;
  runRange: ChartRange | null;
  instId: string;
  bar: Bar | string;
  backtestStartTs: number;
  backtestEndTs: number;
}): ChartRange | null {
  if (chartView === "live-follow") return null;
  if (chartView === "run-range") return runRange;
  if (chartView === "compare-range" && mode === "compare") {
    return { instId, bar, startTs: backtestStartTs, endTs: backtestEndTs };
  }
  return null;
}

export function shouldFollowLive(
  chartView: ChartView,
  mode: Mode,
  okxUp: boolean | null,
): boolean {
  return chartView === "live-follow" && mode !== "paper" && okxUp !== false;
}

export function liveCandleRefreshIntervalMs(bar: Bar | string): number {
  if (bar === "1m") return 15_000;
  if (bar === "5m" || bar === "15m" || bar === "30m") return 30_000;
  if (bar === "1H" || bar === "4H") return 60_000;
  return 120_000;
}
