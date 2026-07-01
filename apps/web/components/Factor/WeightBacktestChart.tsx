"use client";

import {
  ColorType,
  type IChartApi,
  type LineStyle,
  type SeriesMarker,
  type Time,
  type UTCTimestamp,
  createChart,
} from "lightweight-charts";
import { useEffect, useRef } from "react";
import {
  buildTradeMarkerEvents,
  buildTradeTrailSegments,
  tradeToneForEvent,
} from "@/lib/tradeTrails";
import type { Candle, EquityPoint, Trade, TradeMarkerEvent } from "@/lib/types";

/**
 * Factor Lab 内嵌的 slim K 线回测回放视图（lightweight-charts v4）：蜡烛 + 买卖 markers + 盈亏成交连线 +
 * 左价轴净值曲线。刻意不含指标副图 / chartSync / 挂单价线（那是 CandleChart 的重装配），保持面板轻量。
 * 由父组件 `next/dynamic(..., { ssr: false })` 动态引入（规避 lightweight-charts 的 SSR 问题）。
 *
 * P1-1：v4 不接受 `var(--x)` CSS 变量，故本地 `cssVar()` 把设计令牌解析为具体色值；建图 effect 依赖带
 * `theme`，明暗切换即重建，确保配色正确。空 `candles`/`trades` 不崩（空态直接返回空图）。
 */
export default function WeightBacktestChart({
  candles,
  trades,
  equity,
  theme,
}: {
  candles: Candle[];
  trades: Trade[];
  equity: EquityPoint[];
  theme: "light" | "dark";
}) {
  const containerRef = useRef<HTMLDivElement>(null);

  // 建图 + 灌数据一次成型：回测结果是单次不可变载荷，数据 / 主题变化即整体重建（挂载建图、卸载 remove）。
  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;

    const hasEquity = equity.length > 0;
    const chart: IChartApi = createChart(el, {
      layout: {
        background: { type: ColorType.Solid, color: "transparent" },
        textColor: cssVar("--muted"),
        fontFamily: cssVar("--font-mono") || "monospace",
      },
      grid: {
        vertLines: { color: cssVar("--border"), style: 1 },
        horzLines: { color: cssVar("--border"), style: 1 },
      },
      rightPriceScale: { borderColor: cssVar("--border") },
      leftPriceScale: { visible: hasEquity, borderColor: cssVar("--border") },
      timeScale: { borderColor: cssVar("--border"), timeVisible: true, secondsVisible: false },
      autoSize: true,
    });

    const candleSeries = chart.addCandlestickSeries({
      upColor: cssVar("--long"),
      downColor: cssVar("--short"),
      borderUpColor: cssVar("--long"),
      borderDownColor: cssVar("--short"),
      wickUpColor: cssVar("--long"),
      wickDownColor: cssVar("--short"),
    });
    candleSeries.setData(
      candles.map((c) => ({
        time: c.ts as UTCTimestamp,
        open: c.o,
        high: c.h,
        low: c.l,
        close: c.c,
      })),
    );

    // 买卖 / 平仓 markers（BUY 上箭下沿 / SELL 下箭上沿 / CLOSE 圆点含 pnl）。
    const events = buildTradeMarkerEvents(trades);
    candleSeries.setMarkers(events.map((event) => markerForEvent(event)));

    // 盈亏成交连线：盈实线、亏虚线（复用 tradeTrails 段落几何；颜色按方向本地解析）。
    for (const segment of buildTradeTrailSegments(trades)) {
      if (!segment.canRenderLine) continue;
      const line = chart.addLineSeries({
        priceScaleId: "right",
        color: segment.direction === "long" ? cssVar("--long") : cssVar("--short"),
        lineWidth: 2,
        lineStyle: segment.lineStyle as LineStyle | undefined,
        priceLineVisible: false,
        lastValueVisible: false,
      });
      line.setData([
        { time: segment.entry.ts as UTCTimestamp, value: segment.entry.price },
        { time: segment.exit.ts as UTCTimestamp, value: segment.exit.price },
      ]);
    }

    // 左价轴净值曲线（--accent）：按 ts 去重升序（lightweight-charts 要求 time 严格升序不重复）。
    if (hasEquity) {
      const equitySeries = chart.addLineSeries({
        priceScaleId: "left",
        color: cssVar("--accent"),
        lineWidth: 2,
        priceLineVisible: false,
        lastValueVisible: true,
        title: "净值",
      });
      const byTs = new Map<number, number>();
      for (const point of equity) byTs.set(point.ts, point.equity);
      const data = Array.from(byTs, ([ts, value]) => ({ time: ts as UTCTimestamp, value }));
      data.sort((a, b) => (a.time as number) - (b.time as number));
      equitySeries.setData(data);
    }

    chart.timeScale().fitContent();

    return () => chart.remove();
  }, [candles, trades, equity, theme]);

  return <div ref={containerRef} className="h-[320px] w-full" />;
}

function cssVar(name: string): string {
  if (typeof window === "undefined") return "#000";
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

function markerForEvent(event: TradeMarkerEvent): SeriesMarker<Time> {
  const trade = event.trade;
  if (event.kind === "entry-long") {
    return {
      time: trade.ts as UTCTimestamp,
      position: "belowBar",
      color: cssVar("--long"),
      shape: "arrowUp",
      text: "BUY",
    };
  }
  if (event.kind === "entry-short") {
    return {
      time: trade.ts as UTCTimestamp,
      position: "aboveBar",
      color: cssVar("--short"),
      shape: "arrowDown",
      text: "SELL",
    };
  }
  const tone = tradeToneForEvent(event);
  const closeColor =
    tone === "long" ? cssVar("--long") : tone === "short" ? cssVar("--short") : cssVar("--muted");
  return {
    time: trade.ts as UTCTimestamp,
    position: "aboveBar",
    color: closeColor,
    shape: "circle",
    text: `CLOSE ${trade.pnl >= 0 ? "+" : ""}${trade.pnl.toFixed(1)}`,
  };
}
