"use client";

import {
  ColorType,
  CrosshairMode,
  type IChartApi,
  type ISeriesApi,
  type SeriesMarker,
  type Time,
  type UTCTimestamp,
  createChart,
} from "lightweight-charts";
import { useEffect, useRef } from "react";
import type { Candle, EquityPoint, Trade } from "@/lib/types";

type Props = {
  candles: Candle[];
  equity?: EquityPoint[];
  trades?: Trade[];
  theme: "light" | "dark";
  loading?: boolean;
  error?: string | null;
  onLoadMore?: (beforeTs: number) => void;
  jumpToTs?: number | null;
};

function cssVar(name: string): string {
  if (typeof window === "undefined") return "#000";
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

export default function CandleChart({
  candles,
  equity,
  trades,
  theme,
  loading,
  error,
  onLoadMore,
  jumpToTs,
}: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const candleSeriesRef = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const equitySeriesRef = useRef<ISeriesApi<"Line"> | null>(null);
  const loadMoreRef = useRef(onLoadMore);
  const loadingMoreRef = useRef(false);
  const earliestRef = useRef<number | null>(null);

  loadMoreRef.current = onLoadMore;

  // 创建图表（仅客户端，挂载一次；卸载时 remove，避免内存泄漏 R6）
  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;

    const chart = createChart(el, {
      layout: {
        background: { type: ColorType.Solid, color: "transparent" },
        textColor: cssVar("--muted"),
        fontFamily: cssVar("--font-mono") || "monospace",
      },
      grid: {
        vertLines: { color: cssVar("--border"), style: 1 },
        horzLines: { color: cssVar("--border"), style: 1 },
      },
      crosshair: { mode: CrosshairMode.Normal },
      rightPriceScale: { borderColor: cssVar("--border") },
      leftPriceScale: { visible: true, borderColor: cssVar("--border") },
      timeScale: { borderColor: cssVar("--border"), timeVisible: true, secondsVisible: false },
      autoSize: true,
    });
    chartRef.current = chart;

    const candleSeries = chart.addCandlestickSeries({
      upColor: cssVar("--long"),
      downColor: cssVar("--short"),
      borderUpColor: cssVar("--long"),
      borderDownColor: cssVar("--short"),
      wickUpColor: cssVar("--long"),
      wickDownColor: cssVar("--short"),
    });
    candleSeriesRef.current = candleSeries;

    const equitySeries = chart.addLineSeries({
      priceScaleId: "left",
      color: cssVar("--accent"),
      lineWidth: 2,
      priceLineVisible: false,
      lastValueVisible: true,
      title: "净值",
    });
    equitySeriesRef.current = equitySeries;

    // 滚动到左边界 -> 增量加载更早历史（防抖 + 去重）
    const onRange = (range: { from: number; to: number } | null) => {
      if (!range || loadingMoreRef.current) return;
      if (range.from < 5 && earliestRef.current && loadMoreRef.current) {
        loadingMoreRef.current = true;
        loadMoreRef.current(earliestRef.current);
        setTimeout(() => (loadingMoreRef.current = false), 800);
      }
    };
    chart.timeScale().subscribeVisibleLogicalRangeChange(onRange as never);

    return () => {
      chart.remove();
      chartRef.current = null;
      candleSeriesRef.current = null;
      equitySeriesRef.current = null;
    };
  }, []);

  // 主题切换时更新颜色
  useEffect(() => {
    const chart = chartRef.current;
    if (!chart) return;
    chart.applyOptions({
      layout: { textColor: cssVar("--muted") },
      grid: {
        vertLines: { color: cssVar("--border") },
        horzLines: { color: cssVar("--border") },
      },
    });
    candleSeriesRef.current?.applyOptions({
      upColor: cssVar("--long"),
      downColor: cssVar("--short"),
      borderUpColor: cssVar("--long"),
      borderDownColor: cssVar("--short"),
      wickUpColor: cssVar("--long"),
      wickDownColor: cssVar("--short"),
    });
    equitySeriesRef.current?.applyOptions({ color: cssVar("--accent") });
  }, [theme]);

  // 更新 K 线
  useEffect(() => {
    const series = candleSeriesRef.current;
    if (!series || candles.length === 0) return;
    const data = candles.map((c) => ({
      time: c.ts as UTCTimestamp,
      open: c.o,
      high: c.h,
      low: c.l,
      close: c.c,
    }));
    series.setData(data);
    earliestRef.current = candles[0]?.ts ?? null;
  }, [candles]);

  // 更新净值折线
  useEffect(() => {
    const series = equitySeriesRef.current;
    if (!series) return;
    if (!equity || equity.length === 0) {
      series.setData([]);
      return;
    }
    // lightweight-charts 要求 time 严格升序且不重复。实时模拟会按 ts 推送净值，
    // 且收尾会就地更新最后一根同 ts 的点（后端 D3），故按 ts 去重（保留最后值）后升序。
    const byTs = new Map<number, number>();
    for (const p of equity) byTs.set(p.ts, p.equity);
    const data = Array.from(byTs, ([ts, value]) => ({ time: ts as UTCTimestamp, value }));
    data.sort((a, b) => (a.time as number) - (b.time as number));
    series.setData(data);
  }, [equity]);

  // 日期跳转：滚动到指定时间附近；若早于已加载范围则触发增量加载
  useEffect(() => {
    const chart = chartRef.current;
    if (!chart || !jumpToTs || candles.length === 0) return;
    const earliest = candles[0].ts;
    const latest = candles[candles.length - 1].ts;
    if (jumpToTs < earliest) {
      loadMoreRef.current?.(jumpToTs);
      return;
    }
    const span = Math.max(latest - earliest, 1);
    const half = Math.round(span * 0.08);
    try {
      chart.timeScale().setVisibleRange({
        from: (jumpToTs - half) as UTCTimestamp,
        to: (jumpToTs + half) as UTCTimestamp,
      });
    } catch {
      /* 超出范围忽略 */
    }
  }, [jumpToTs, candles]);

  // 更新买卖 markers
  useEffect(() => {
    const series = candleSeriesRef.current;
    if (!series) return;
    if (!trades || trades.length === 0) {
      series.setMarkers([]);
      return;
    }
    const markers: SeriesMarker<Time>[] = trades
      .slice()
      .sort((a, b) => a.ts - b.ts)
      .map((t) => {
        if (t.side === "long") {
          return {
            time: t.ts as UTCTimestamp,
            position: "belowBar" as const,
            color: cssVar("--long"),
            shape: "arrowUp" as const,
            text: `多 ${t.reason || ""}`.trim(),
          };
        }
        if (t.side === "short") {
          return {
            time: t.ts as UTCTimestamp,
            position: "aboveBar" as const,
            color: cssVar("--short"),
            shape: "arrowDown" as const,
            text: `空 ${t.reason || ""}`.trim(),
          };
        }
        return {
          time: t.ts as UTCTimestamp,
          position: "aboveBar" as const,
          color: cssVar("--muted"),
          shape: "circle" as const,
          text: `平 ${t.pnl >= 0 ? "+" : ""}${t.pnl.toFixed(1)}`,
        };
      });
    series.setMarkers(markers);
  }, [trades, theme]);

  return (
    <div className="relative h-full w-full">
      <div ref={containerRef} className="h-full w-full" />

      {loading && !error && (
        <div className="absolute left-1/2 top-3 -translate-x-1/2 rounded-full bg-[var(--surface)] px-3 py-1 text-xs text-[var(--muted)] shadow-soft">
          正在加载真实行情…
        </div>
      )}

      {error && (
        <div className="absolute inset-0 flex flex-col items-center justify-center gap-2 bg-[var(--bg)]/80 backdrop-blur-sm">
          <div className="rounded-[12px] border border-[var(--short)] bg-[var(--surface)] px-5 py-4 text-center shadow-soft">
            <div className="text-sm font-semibold text-[var(--short)]">无法连接 OKX</div>
            <div className="mt-1 max-w-xs text-xs text-[var(--muted)]">{error}</div>
            <div className="mt-1 text-[11px] text-[var(--muted)]">请检查代理 / 网络后重试（不会以假数据冒充真实行情）。</div>
          </div>
        </div>
      )}
    </div>
  );
}
