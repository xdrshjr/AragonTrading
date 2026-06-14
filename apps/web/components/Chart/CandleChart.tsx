"use client";

import {
  ColorType,
  CrosshairMode,
  type IChartApi,
  type IPriceLine,
  type ISeriesApi,
  type LineStyle,
  type MouseEventParams,
  type SeriesMarker,
  type Time,
  type UTCTimestamp,
  createChart,
} from "lightweight-charts";
import { useEffect, useMemo, useRef, useState } from "react";
import {
  PRICE_SCALE_MIN_WIDTH,
  alignPriceScaleWidths,
  createSyncGroup,
  type SyncGroup,
} from "@/lib/chartSync";
import { normalizeEquity } from "@/lib/compare";
import { INDICATORS, type IndicatorKey, type Plot, type ThemeColors } from "@/lib/indicators";
import { selectActivePane } from "@/lib/paneTabs";
import {
  buildTradeMarkerEvents,
  buildTradeTrailSegments,
  tradeToneForEvent,
} from "@/lib/tradeTrails";
import type {
  Candle,
  EquityPoint,
  Overlay,
  Trade,
  TradeMarkerEvent,
  TradeTrailSegment,
} from "@/lib/types";
import { Tabs } from "@/components/ui";
import IndicatorPane from "./IndicatorPane";
import { reconcilePlots, type PlotSeries } from "./plots";
import TradeTooltip from "./TradeTooltip";

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
  // 十字光标悬停主图回吐被选中 K 线秒级 ts；移出为 null（chart-workspace-layout 需求 2）。
  onCrosshairChange?: (ts: number | null) => void;
  // 副图页签当前选中态（来自 store，可空）+ 点击回调（= store.setPaneTab，仅传启用项）。
  paneTab?: IndicatorKey | null;
  onPaneTabChange?: (key: IndicatorKey) => void;
  livePrice?: number | null;
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
  onCrosshairChange,
  paneTab,
  onPaneTabChange,
  livePrice,
}: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const candleSeriesRef = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const priceLineRef = useRef<IPriceLine | null>(null);
  const equitySeriesRef = useRef<ISeriesApi<"Line"> | null>(null);
  const overlaySeriesRef = useRef<Map<number, ISeriesApi<"Line">>>(new Map());
  const trailSeriesRef = useRef<Map<string, ISeriesApi<"Line">>>(new Map());
  // 主图叠加技术指标（MA/EMA/BOLL）的系列 map：key 为命名空间化 id ${def.key}:${plot.id}（RV-3）。
  const overlayIndSeriesRef = useRef<Map<string, PlotSeries>>(new Map());
  const loadMoreRef = useRef(onLoadMore);
  const loadingMoreRef = useRef(false);
  const earliestRef = useRef<number | null>(null);
  // 用 ref 持有最新十字光标回调，避免重订阅（创建 effect 仅挂载一次执行，R9/§9.1）。
  const onCrosshairRef = useRef(onCrosshairChange);
  const tradeEventsByTsRef = useRef<Map<number, TradeMarkerEvent[]>>(new Map());
  onCrosshairRef.current = onCrosshairChange;
  const [tradeTooltip, setTradeTooltip] = useState<{
    events: TradeMarkerEvent[];
    x: number;
    y: number;
  } | null>(null);

  // 主图 + 各副图的时间轴 / 十字光标同步组（挂载期内稳定，懒创建一次）。
  const syncRef = useRef<SyncGroup | null>(null);
  if (syncRef.current === null) syncRef.current = createSyncGroup();

  loadMoreRef.current = onLoadMore;

  const tradeSegments = useMemo(() => buildTradeTrailSegments(trades ?? []), [trades]);
  const tradeEvents = useMemo(() => buildTradeMarkerEvents(trades ?? []), [trades]);
  const tradeEventsByTs = useMemo(() => {
    const map = new Map<number, TradeMarkerEvent[]>();
    for (const event of tradeEvents) {
      const existing = map.get(event.trade.ts);
      if (existing) existing.push(event);
      else map.set(event.trade.ts, [event]);
    }
    return map;
  }, [tradeEvents]);
  tradeEventsByTsRef.current = tradeEventsByTs;

  // 启用的副图指标（注册表顺序即页签顺序）；单显切换：任一时刻只渲染当前选中的一个窗格。
  const enabledPaneDefs = INDICATORS.filter((d) => d.category === "pane" && indicators[d.key]);
  const hasPane = enabledPaneDefs.length > 0;
  const activeKey = selectActivePane(
    enabledPaneDefs.map((d) => d.key),
    paneTab ?? null,
  );
  const activePaneDef = enabledPaneDefs.find((d) => d.key === activeKey) ?? null;
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
      // 净值剥离后（price 视图无 equity）隐藏左价轴，避免残留空轴（§3.2(B)）。
      leftPriceScale: {
        visible: hasEquity,
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

    // 十字光标回吐：仅直接悬停主图时 param.time 有值（秒级 ts）；移出或悬停副图为 null（R9）。
    const onCross = (param: MouseEventParams) => {
      const t = param.time;
      const ts = typeof t === "number" ? t : null;
      onCrosshairRef.current?.(ts);
      const point = param.point;
      const events = ts == null ? undefined : tradeEventsByTsRef.current.get(ts);
      if (!point || !events?.length) {
        setTradeTooltip(null);
        return;
      }
      setTradeTooltip({ events, x: point.x, y: point.y });
    };
    chart.subscribeCrosshairMove(onCross);

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
      chart.unsubscribeCrosshairMove(onCross);
      chart.remove();
      chartRef.current = null;
      candleSeriesRef.current = null;
      priceLineRef.current = null;
      equitySeriesRef.current = null;
      overlaySeriesRef.current.clear();
      trailSeriesRef.current.clear();
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

  useEffect(() => {
    const series = candleSeriesRef.current;
    if (!series) return;
    if (priceLineRef.current) {
      series.removePriceLine(priceLineRef.current);
      priceLineRef.current = null;
    }
    if (livePrice == null) return;
    priceLineRef.current = series.createPriceLine({
      price: livePrice,
      color: cssVar("--accent"),
      lineWidth: 1,
      lineStyle: 2,
      axisLabelVisible: true,
      title: "LIVE",
    });
  }, [livePrice, theme]);

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

  useEffect(() => {
    const chart = chartRef.current;
    if (!chart) return;
    const map = trailSeriesRef.current;
    const drawable = tradeSegments.filter((segment) => segment.canRenderLine);
    const nextIds = new Set(drawable.map((segment) => segment.id));

    for (const [id, series] of map) {
      if (!nextIds.has(id)) {
        chart.removeSeries(series);
        map.delete(id);
      }
    }

    for (const segment of drawable) {
      let series = map.get(segment.id);
      const color = trailColor(segment);
      if (!series) {
        series = chart.addLineSeries({
          priceScaleId: "right",
          color,
          lineWidth: 2,
          lineStyle: segment.lineStyle as LineStyle | undefined,
          priceLineVisible: false,
          lastValueVisible: false,
        });
        map.set(segment.id, series);
      } else {
        series.applyOptions({
          color,
          lineStyle: segment.lineStyle as LineStyle | undefined,
        });
      }
      series.setData([
        { time: segment.entry.ts as UTCTimestamp, value: segment.entry.price },
        { time: segment.exit.ts as UTCTimestamp, value: segment.exit.price },
      ]);
    }
  }, [tradeSegments, theme]);

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

  // 更新买卖/平仓 markers
  useEffect(() => {
    const series = candleSeriesRef.current;
    if (!series) return;
    if (tradeEvents.length === 0) {
      series.setMarkers([]);
      setTradeTooltip(null);
      return;
    }
    series.setMarkers(tradeEvents.map((event) => markerForEvent(event)));
  }, [tradeEvents, theme]);

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

  // 左价轴随净值存废（§3.2(B)）：price 视图无 equity 时隐藏，避免残留空轴。
  useEffect(() => {
    chartRef.current?.applyOptions({ leftPriceScale: { visible: hasEquity } });
  }, [hasEquity]);

  // 价格轴动态等宽对齐（RV-1）：仅价格量级 / 当前副图 / 副图存废 / 主题 / 净值轴存废变化时重对齐，
  // 避免实时每 tick 抖动。切页签时不同副图值域不同（RSI 0~100 / MACD 小数 / VOL 大数），须以
  // activeKey 为依赖重测重写同侧价轴宽度（§3.2(B)/(E)）；新 pane 子 chart 已在其挂载 effect
  //（先于父 effect 执行）内注册进 sync 组，故此处 getCharts() 已含当前图。
  useEffect(() => {
    if (syncRef.current) alignPriceScaleWidths(syncRef.current.getCharts());
  }, [priceMagnitude, activeKey, hasPane, theme, hasEquity]);

  return (
    <div className="relative flex h-full w-full flex-col">
      <div ref={containerRef} className="min-h-0 w-full flex-[4]" />
      {tradeTooltip && (
        <TradeTooltip events={tradeTooltip.events} x={tradeTooltip.x} y={tradeTooltip.y} />
      )}

      {/* 副图指标容器（≈20% 高）：顶部靠左短标签页签 + 下方仅渲染当前选中的单个窗格（单显切换）。 */}
      {hasPane && activePaneDef && (
        <div className="flex min-h-[120px] flex-1 flex-col border-t border-[var(--border)]">
          <div className="shrink-0 px-2">
            {/* 评审 R-1：页签用短标签（指标键大写：MACD/RSI/KDJ/VOL），与 §2 示意图一致；
                注册表 d.label 为长名（如「RSI 相对强弱」）会撑宽容器顶部，违背「靠左紧凑」。 */}
            <Tabs
              variant="underline"
              tabs={enabledPaneDefs.map((d) => ({ key: d.key, label: d.key.toUpperCase() }))}
              active={activePaneDef.key}
              onChange={(k) => onPaneTabChange?.(k as IndicatorKey)}
            />
          </div>
          <div className="relative min-h-0 flex-1">
            <IndicatorPane
              key={activePaneDef.key}
              candles={candles}
              def={activePaneDef}
              theme={theme}
              sync={syncRef.current as SyncGroup}
              showTimeAxis
            />
          </div>
        </div>
      )}

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
  const color =
    tone === "long"
      ? cssVar("--long")
      : tone === "short"
        ? cssVar("--short")
        : cssVar("--muted");

  return {
    time: trade.ts as UTCTimestamp,
    position: "aboveBar",
    color,
    shape: "circle",
    text: `CLOSE ${trade.pnl >= 0 ? "+" : ""}${trade.pnl.toFixed(1)}`,
  };
}

function trailColor(segment: TradeTrailSegment): string {
  return segment.direction === "long" ? cssVar("--long") : cssVar("--short");
}
