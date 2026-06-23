"use client";

import {
  ColorType,
  CrosshairMode,
  type IChartApi,
  type IPriceLine,
  type ISeriesApi,
  type LineStyle,
  type UTCTimestamp,
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
  // 左价轴可见性须与主图保持一致。主图左轴仅净值视图可见（CandleChart leftPriceScale.visible=hasEquity），
  // 而副图左轴恒为空（系列一律挂右轴）。若副图左轴恒显、主图左轴隐藏，副图就比主图多预留一条左轴宽，
  // 绘图区左缘整体右移，形成与缩放无关的「固定一段距离」横向错位——这无法靠等宽对齐修复（不可见价轴宽
  // 恒为 0，minimumWidth 撑不开）。跟随主图可见性即可消除该常量偏移（chart-indicator-zoom-misalignment §B）。
  showLeftAxis: boolean;
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
export default function IndicatorPane({
  candles,
  def,
  theme,
  sync,
  showTimeAxis,
  showLeftAxis,
}: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const mapRef = useRef<Map<string, PlotSeries>>(new Map());
  // 全域 whitespace 锚系列：逐根覆盖全部 candle 时间，把副图 time scale 的 index 0 钉在 candle[0]。
  // 独立于 mapRef 管理，故不进入跨图十字光标的 getSeries（避免复发 chart-crosshair-sync-value-is-null）。
  const anchorSeriesRef = useRef<ISeriesApi<"Line"> | null>(null);
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
        visible: showLeftAxis,
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

    // 锚系列让副图时间轴逐根覆盖全部 candle，使副图 index 0 = candle[0]，与主图（candleSeries 全量、
    // 原点钉在 candle[0]）逻辑索引一一对齐。否则副图指标丢弃暖机前导点后 index 0 晚于主图，
    // 仅按 setVisibleLogicalRange 同步即产生与缩放无关的固定 W 根横向错位、时间维度十字光标贯穿同样偏移
    // （chart-indicator-constant-bar-offset）。whitespace（仅 time、无 value）不绘制、不进 autoscale、
    // 不破坏副图 fixedRange（RSI 0~100 等）。
    anchorSeriesRef.current = chart.addLineSeries({
      priceScaleId: "right",
      lastValueVisible: false,
      priceLineVisible: false,
      crosshairMarkerVisible: false,
    });

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
      anchorSeriesRef.current = null;
      mapRef.current.clear();
    };
    // sync / def 在本组件生命周期内稳定（按 key={def.key} 渲染）；showTimeAxis / showLeftAxis
    // 由下方独立 effect 跟踪。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // 最底部 pane 变化时（开关指标导致）即时更新本图时间轴可见性（RV-2）。
  useEffect(() => {
    chartRef.current?.applyOptions({ timeScale: { visible: showTimeAxis } });
  }, [showTimeAxis]);

  // 左价轴可见性跟随主图（净值存废）即时更新，保持两图绘图区左缘对齐、消除固定横向错位（§B）。
  useEffect(() => {
    chartRef.current?.applyOptions({ leftPriceScale: { visible: showLeftAxis } });
  }, [showLeftAxis]);

  // 数据 / 主题变化 → 重算指标并 reconcile；fixedRange 经 autoscaleInfoProvider 锁定值域（挂全部系列，RV-4）。
  useEffect(() => {
    const chart = chartRef.current;
    if (!chart) return;

    // 锚系列随 candles 持续逐根覆盖（实时新增 / 左加载更早历史 / 切标的），空 candles → []，
    // 始终保持副图 index 0 = candle[0]。
    anchorSeriesRef.current?.setData(candles.map((c) => ({ time: c.ts as UTCTimestamp })));

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
