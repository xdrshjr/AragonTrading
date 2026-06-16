"use client";

import {
  ColorType,
  CrosshairMode,
  type IChartApi,
  type IPriceLine,
  type ISeriesApi,
  type LineStyle,
  createChart,
} from "lightweight-charts";
import { useEffect, useRef } from "react";
import { PRICE_SCALE_MIN_WIDTH, type SyncGroup } from "@/lib/chartSync";
import type { IndicatorDef, ThemeColors } from "@/lib/indicators";
import type { Candle } from "@/lib/types";
import { reconcilePlots, type PlotSeries } from "./plots";

type Props = {
  candles: Candle[];
  def: IndicatorDef;
  theme: "light" | "dark";
  sync: SyncGroup;
  showTimeAxis: boolean; // 仅最底部 pane 为 true，独占日期轴（RV-2）
};

function cssVar(name: string): string {
  if (typeof window === "undefined") return "#000";
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

function themeColors(): ThemeColors {
  return {
    long: cssVar("--long"),
    short: cssVar("--short"),
    muted: cssVar("--muted"),
    accent: cssVar("--accent"),
    text: cssVar("--text"),
  };
}

// 单个副图指标的独立子图：建 chart → compute → reconcile → 参考线 → 注册 sync → 卸载清理。
export default function IndicatorPane({ candles, def, theme, sync, showTimeAxis }: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const mapRef = useRef<Map<string, PlotSeries>>(new Map());
  const refLinesRef = useRef<{ series: ISeriesApi<"Line" | "Histogram">; line: IPriceLine }[]>([]);

  // 挂载：建 chart + 注册 sync（getSeries 取本图任一系列供跨图十字光标）；卸载严格清理（R6）。
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
      rightPriceScale: {
        visible: true,
        borderColor: cssVar("--border"),
        minimumWidth: PRICE_SCALE_MIN_WIDTH,
      },
      leftPriceScale: {
        visible: true,
        borderColor: cssVar("--border"),
        minimumWidth: PRICE_SCALE_MIN_WIDTH,
      },
      timeScale: {
        visible: showTimeAxis,
        borderColor: cssVar("--border"),
        timeVisible: true,
        secondsVisible: false,
      },
      autoSize: true,
    });
    chartRef.current = chart;

    const unregister = sync.register(chart, () => mapRef.current.values().next().value ?? null);

    return () => {
      unregister();
      for (const { series, line } of refLinesRef.current) {
        try {
          series.removePriceLine(line);
        } catch {
          /* series 可能已随 chart 移除 */
        }
      }
      refLinesRef.current = [];
      chart.remove();
      chartRef.current = null;
      mapRef.current.clear();
    };
    // sync / def 在本组件生命周期内稳定（按 key={def.key} 渲染）；showTimeAxis 由下方独立 effect 跟踪。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // 最底部 pane 变化时（开关指标导致）即时更新本图时间轴可见性（RV-2）。
  useEffect(() => {
    chartRef.current?.applyOptions({ timeScale: { visible: showTimeAxis } });
  }, [showTimeAxis]);

  // 数据 / 主题变化 → 重算指标并 reconcile；fixedRange 经 autoscaleInfoProvider 锁定值域（挂全部系列，RV-4）。
  useEffect(() => {
    const chart = chartRef.current;
    if (!chart) return;
    const tc = themeColors();
    const plots = def.compute(candles, tc);

    const provider = def.fixedRange
      ? () => ({
          priceRange: { minValue: def.fixedRange!.min, maxValue: def.fixedRange!.max },
        })
      : undefined;

    reconcilePlots(chart, mapRef.current, plots, {
      priceScaleId: "right",
      autoscaleInfoProvider: provider,
    });

    // 参考线（RSI 70/50/30 等）：移除旧线，按当前主题色重建到本图任一系列（共用右价轴，挂哪条都画在同一刻度）。
    for (const { series, line } of refLinesRef.current) {
      try {
        series.removePriceLine(line);
      } catch {
        /* ignore */
      }
    }
    refLinesRef.current = [];
    if (def.refLines && def.refLines.length > 0) {
      const anchor = mapRef.current.values().next().value;
      if (anchor) {
        for (const rl of def.refLines) {
          const line = anchor.createPriceLine({
            price: rl.value,
            color: rl.color ?? tc.muted,
            lineWidth: 1,
            lineStyle: 2 as LineStyle, // 虚线
            axisLabelVisible: true,
            title: rl.title ?? "",
          });
          refLinesRef.current.push({ series: anchor, line });
        }
      }
    }
  }, [candles, theme, def]);

  return (
    <div className="relative h-full w-full">
      <div ref={containerRef} className="h-full w-full" />
    </div>
  );
}
