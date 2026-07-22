"use client";

import {
  ColorType,
  type IChartApi,
  type ISeriesApi,
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
import type { Candle, Trade, TradeMarkerEvent } from "@/lib/types";

/**
 * 单笔完整交易往返的行内 slim K 线（ai-trade-stats-two-column-and-kline §3.4）：蜡烛 + 买卖 / 平仓 markers +
 * 开→平成交连线。逐字镜像 `Factor/WeightBacktestChart` 的建图范式，去掉左价轴净值曲线、容器更矮，
 * 只呈现该往返窗口的价格走势与买卖点位。
 *
 * P1-1：v4 不接受 `var(--x)` CSS 变量，故本地 `cssVar()` 把设计令牌解析为具体色值；建图 effect 依赖带
 * `theme`，明暗切换即重建。本组件**只**由 `TradeCandleChartPanel` 经 `next/dynamic(..., { ssr:false })`
 * 引入（规避 lightweight-charts 的 SSR 问题），绝不静态 import。空 `candles`/`trades` 不崩。
 */
export default function TradeCandleChart({
  candles,
  trades,
  theme,
}: {
  candles: Candle[];
  trades: Trade[];
  theme: "light" | "dark";
}) {
  const containerRef = useRef<HTMLDivElement>(null);

  // 建图 + 灌数据一次成型：窗口 K 线是单次不可变载荷，数据 / 主题变化即整体重建（挂载建图、卸载 remove）。
  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;

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
      priceLineVisible: false, // P1-2：关闭默认末值价线，令图上只剩开仓 / 平仓两条有语义的参考线
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

    drawTradeReferenceLines(candleSeries, trades); // E2-a：叠加开仓价 / 平仓价两条水平参考线（右轴带价签、直观醒目）

    // 开→平成交连线：盈实线、亏虚线（复用 tradeTrails 段落几何；颜色按方向本地解析）。
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

    chart.timeScale().fitContent();

    return () => chart.remove();
  }, [candles, trades, theme]);

  return <div ref={containerRef} className="h-[240px] w-full" />;
}

function cssVar(name: string): string {
  if (typeof window === "undefined") return "#000";
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

// E2-a 开 / 平价水平参考线（模块级 helper，镜像 markerForEvent / cssVar 范式，避免撑破建图 effect 的方法长度阈值）。
// Trade.pnl 为必填 number、side 为 "long"|"short"|"close"（types.ts）；唯一调用点恒传 [entry, exit]（TradeCandleChartPanel），
// 故直接解构、按真实契约取值，不做可空兜底。参考线随 chart.remove() 整体销毁，零泄漏。
function drawTradeReferenceLines(candleSeries: ISeriesApi<"Candlestick">, trades: Trade[]): void {
  const [entryTrade, exitTrade] = trades;
  if (entryTrade) {
    candleSeries.createPriceLine({
      price: entryTrade.price,
      color: cssVar("--muted"),
      lineWidth: 1,
      lineStyle: 2 /* Dashed */ as LineStyle,
      axisLabelVisible: true,
      title: "开仓",
    });
  }
  if (exitTrade && exitTrade !== entryTrade) {
    candleSeries.createPriceLine({
      price: exitTrade.price,
      // 平仓价参考线按盈亏着色（P2-5：与 CLOSE marker 的 pnl 语义一致）。
      color: exitTrade.pnl >= 0 ? cssVar("--long") : cssVar("--short"),
      lineWidth: 1,
      lineStyle: 2 /* Dashed */ as LineStyle,
      axisLabelVisible: true,
      title: "平仓",
    });
  }
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
