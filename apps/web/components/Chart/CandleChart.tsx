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
import { useEffect, useMemo, useRef } from "react";
import {
  PRICE_SCALE_MIN_WIDTH,
  alignPriceScaleWidths,
  createSyncGroup,
  type SyncGroup,
} from "@/lib/chartSync";
import { normalizeEquity } from "@/lib/compare";
import { INDICATORS, type IndicatorKey, type Plot, type ThemeColors } from "@/lib/indicators";
import type { Candle, EquityPoint, Trade } from "@/lib/types";
import IndicatorPane from "./IndicatorPane";
import { reconcilePlots, type PlotSeries } from "./plots";

// 一条对比净值叠加曲线（compare 模式，F3）
export type Overlay = { id: number; name: string; color: string; equity: EquityPoint[] };

type Props = {
  candles: Candle[];
  equity?: EquityPoint[];
  trades?: Trade[];
  overlays?: Overlay[];
  indicators: Record<IndicatorKey, boolean>;
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

function themeColors(): ThemeColors {
  return {
    long: cssVar("--long"),
    short: cssVar("--short"),
    muted: cssVar("--muted"),
    accent: cssVar("--accent"),
    text: cssVar("--text"),
  };
}

export default function CandleChart({
  candles,
  equity,
  trades,
  overlays,
  indicators,
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
  const overlaySeriesRef = useRef<Map<number, ISeriesApi<"Line">>>(new Map());
  // 主图叠加技术指标（MA/EMA/BOLL）的系列 map：key 为命名空间化 id ${def.key}:${plot.id}（RV-3）。
  const overlayIndSeriesRef = useRef<Map<string, PlotSeries>>(new Map());
  const loadMoreRef = useRef(onLoadMore);
  const loadingMoreRef = useRef(false);
  const earliestRef = useRef<number | null>(null);

  // 主图 + 各副图的时间轴 / 十字光标同步组（挂载期内稳定，懒创建一次）。
  const syncRef = useRef<SyncGroup | null>(null);
  if (syncRef.current === null) syncRef.current = createSyncGroup();

  loadMoreRef.current = onLoadMore;

  // 启用的叠加 / 副图指标（注册表顺序即渲染顺序）。
  const enabledPaneDefs = INDICATORS.filter((d) => d.category === "pane" && indicators[d.key]);
  const hasPane = enabledPaneDefs.length > 0;
  const paneSig = enabledPaneDefs.map((d) => d.key).join(",");
  const hasEquity = !!equity && equity.length > 0;
  // 价格量级（默认 BTC≈6 万 vs SOL≈百），仅量级变化才触发价轴等宽重对齐，避免实时每 tick 抖动（RV-1）。
  const priceMagnitude = useMemo(() => {
    const c = candles[candles.length - 1]?.c ?? candles[0]?.c;
    return c ? Math.round(Math.log10(Math.abs(c) + 1) * 2) : 0;
  }, [candles]);

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
      rightPriceScale: { borderColor: cssVar("--border"), minimumWidth: PRICE_SCALE_MIN_WIDTH },
      leftPriceScale: {
        visible: true,
        borderColor: cssVar("--border"),
        minimumWidth: PRICE_SCALE_MIN_WIDTH,
      },
      // 有副图 pane 时主图隐藏自身日期轴，由最底部 pane 独占，全栈只一行日期轴（RV-2）。
      timeScale: {
        visible: !hasPane,
        borderColor: cssVar("--border"),
        timeVisible: true,
        secondsVisible: false,
      },
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

    // 注册进同步组（主图为锚，getSeries 供跨图十字光标贯穿）。
    const sync = syncRef.current;
    const unregister = sync ? sync.register(chart, () => candleSeriesRef.current) : undefined;

    // 容器尺寸变化后重对齐主副价格轴等宽（autoSize 负责重排，本处仅重测回写宽度，RV-1）。
    let ro: ResizeObserver | undefined;
    if (typeof ResizeObserver !== "undefined") {
      ro = new ResizeObserver(() => {
        if (syncRef.current) alignPriceScaleWidths(syncRef.current.getCharts());
      });
      ro.observe(el);
    }

    return () => {
      ro?.disconnect();
      unregister?.();
      chart.remove();
      chartRef.current = null;
      candleSeriesRef.current = null;
      equitySeriesRef.current = null;
      overlaySeriesRef.current.clear();
      overlayIndSeriesRef.current.clear();
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

  // 多策略对比净值叠加（compare 模式，F3）：归一化为百分比，挂在与 K 线右价轴
  // 隔离的独立 "overlay" 价轴上（否则会被 6 万量级价格刻度压成贴底直线，C1）。
  useEffect(() => {
    const chart = chartRef.current;
    if (!chart) return;
    const map = overlaySeriesRef.current;
    const next = overlays ?? [];
    const nextIds = new Set(next.map((o) => o.id));

    // 移除不再需要的曲线，释放 series（R6）
    for (const [id, series] of map) {
      if (!nextIds.has(id)) {
        chart.removeSeries(series);
        map.delete(id);
      }
    }

    // 新增 / 更新曲线
    for (const ov of next) {
      let series = map.get(ov.id);
      if (!series) {
        series = chart.addLineSeries({
          priceScaleId: "overlay",
          color: ov.color,
          lineWidth: 2,
          priceLineVisible: false,
          lastValueVisible: false,
          title: ov.name,
        });
        map.set(ov.id, series);
      } else {
        series.applyOptions({ color: ov.color, title: ov.name });
      }
      const norm = normalizeEquity(ov.equity);
      series.setData(norm.map((p) => ({ time: p.ts as UTCTimestamp, value: p.value })));
    }

    if (next.length > 0) {
      // 自适应百分比区间，留出上下边距
      chart.priceScale("overlay").applyOptions({ scaleMargins: { top: 0.1, bottom: 0.2 } });
    }
  }, [overlays]);

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

  // 主图叠加技术指标（MA/EMA/BOLL）：compute → 把每个 plot.id 命名空间化为 ${def.key}:${plot.id}
  // 再交给 reconcilePlots（RV-3：建键 / 删除 / 建系列三处同一 id），挂在与 K 线同一右价轴上天然共轴。
  // 依赖 candles（实时重算最右值）/ theme（重着色）/ indicators（开关增删系列）。
  useEffect(() => {
    const chart = chartRef.current;
    if (!chart) return;
    const tc = themeColors();
    const overlayPlots: Plot[] = [];
    for (const def of INDICATORS) {
      if (def.category !== "overlay" || !indicators[def.key]) continue;
      for (const plot of def.compute(candles, tc)) {
        const id = `${def.key}:${plot.id}`;
        overlayPlots.push(plot.kind === "line" ? { ...plot, id } : { ...plot, id });
      }
    }
    reconcilePlots(chart, overlayIndSeriesRef.current, overlayPlots, { priceScaleId: "right" });
  }, [candles, theme, indicators]);

  // 主图时间轴可见性（RV-2）：开关副图导致 hasPane 变化时即时更新，任一时刻全栈只渲染一行日期轴。
  useEffect(() => {
    chartRef.current?.applyOptions({ timeScale: { visible: !hasPane } });
  }, [hasPane]);

  // 价格轴动态等宽对齐（RV-1）：仅价格量级 / 副图数量 / 主题 / 净值轴存废变化时重对齐，
  // 避免实时每 tick 抖动。新 pane 的子 chart 已在其挂载 effect（先于父 effect 执行）内注册进 sync 组，
  // 故此处 getCharts() 已含全部图。
  useEffect(() => {
    if (syncRef.current) alignPriceScaleWidths(syncRef.current.getCharts());
  }, [priceMagnitude, paneSig, theme, hasEquity]);

  return (
    <div className="relative flex h-full w-full flex-col">
      <div ref={containerRef} className="min-h-0 w-full flex-1" />

      {/* 副图指标窗格：每个启用的 pane 指标一个，稳定 key={def.key}（RV-6），最底部独占日期轴（RV-2）。 */}
      {enabledPaneDefs.map((def, i) => (
        <IndicatorPane
          key={def.key}
          candles={candles}
          def={def}
          theme={theme}
          sync={syncRef.current as SyncGroup}
          showTimeAxis={i === enabledPaneDefs.length - 1}
        />
      ))}

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
