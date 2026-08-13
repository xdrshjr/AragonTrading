import type { Bar, ChartView, MarketWsEvent, Mode } from "./types";

/**
 * 市场 WS 帧是否为图表消费的 K 线帧。未知 / 后端后加的事件类型（如 `link`）一律 false。
 *
 * 后端把链路状态 `{"type":"link"}` 作为**加法**事件类型加进同一条市场流（ws_market.py 的
 * `on_stream`），其正确性前提写在那里：「前端遇到未知 type 本就忽略」。而消费端原先用 else
 * 兜底把未匹配的帧一律当 K 线：`link` 帧被 `setForming` 写进 candles，其 `ts` 为 undefined，
 * lightweight-charts 的时间转换读 `undefined.year` 抛 TypeError，整棵 React 树卸载 → 打包
 * 桌面端开屏白屏。这里把判据反过来写成白名单，让后端的加法事件真正是加法。
 * 详见 docs/diagnoses/desktop-white-screen-market-ws-link-frame/analysis.md
 */
export function isCandleFrame(
  event: MarketWsEvent,
): event is Extract<MarketWsEvent, { type: "candle" }> {
  return event.type === "candle";
}

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

export function shouldStreamMarket(mode: Mode, okxUp: boolean | null): boolean {
  return okxUp !== false && (mode === "backtest" || mode === "compare" || mode === "paper");
}

export function shouldApplyPublicCandle(
  chartView: ChartView,
  mode: Mode,
  paperSessionId: string | null,
): boolean {
  return chartView === "live-follow" && !(mode === "paper" && paperSessionId);
}

export function liveCandleRefreshIntervalMs(bar: Bar | string): number {
  if (bar === "1m") return 15_000;
  if (bar === "5m" || bar === "15m" || bar === "30m") return 30_000;
  if (bar === "1H" || bar === "4H") return 60_000;
  return 120_000;
}
