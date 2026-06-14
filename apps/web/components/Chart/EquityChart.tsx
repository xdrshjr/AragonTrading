"use client";

import {
  ColorType,
  CrosshairMode,
  type IChartApi,
  type ISeriesApi,
  type UTCTimestamp,
  createChart,
} from "lightweight-charts";
import { useEffect, useRef } from "react";
import { PRICE_SCALE_MIN_WIDTH } from "@/lib/chartSync";
import { normalizeEquity } from "@/lib/compare";
import type { EquityPoint, Overlay } from "@/lib/types";

// 独立净值图（chart-workspace-layout #7）：从 K 线图剥离净值，避免两类量纲挤在一张图上。
// 单条 equity（回测 / 实时，原值右轴）或多条归一化 overlays（对比，百分比右轴）二选一；
// 二者互斥（compare 无单条 equity，backtest/paper 无 overlays），故共用右价轴无冲突。
interface EquityChartProps {
  equity?: EquityPoint[]; // 回测 / 实时单条
  overlays?: Overlay[]; // 对比多条（归一化）
  theme: "light" | "dark";
  loading?: boolean;
  error?: string | null;
  emptyHint?: string; // 三模式空态提示（回测未跑 / 实时未起 / 对比未跑，R12/C4）
}

function cssVar(name: string): string {
  if (typeof window === "undefined") return "#000";
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

export default function EquityChart({
  equity,
  overlays,
  theme,
  loading,
  error,
  emptyHint,
}: EquityChartProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const equitySeriesRef = useRef<ISeriesApi<"Line"> | null>(null);
  const overlaySeriesRef = useRef<Map<number, ISeriesApi<"Line">>>(new Map());

  const hasOverlays = !!overlays && overlays.length > 0;
  const hasEquity = !!equity && equity.length > 0;
  const isEmpty = !hasOverlays && !hasEquity;

  // 创建图表（仅客户端，挂载一次；卸载时 remove，避免内存泄漏 R6）。
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
      timeScale: { borderColor: cssVar("--border"), timeVisible: true, secondsVisible: false },
      autoSize: true,
    });
    chartRef.current = chart;
    equitySeriesRef.current = chart.addLineSeries({
      color: cssVar("--accent"),
      lineWidth: 2,
      priceLineVisible: false,
      lastValueVisible: true,
      title: "净值",
    });
    return () => {
      chart.remove();
      chartRef.current = null;
      equitySeriesRef.current = null;
      overlaySeriesRef.current.clear();
    };
  }, []);

  // 主题切换时更新颜色。
  useEffect(() => {
    chartRef.current?.applyOptions({
      layout: { textColor: cssVar("--muted") },
      grid: {
        vertLines: { color: cssVar("--border") },
        horzLines: { color: cssVar("--border") },
      },
    });
    equitySeriesRef.current?.applyOptions({ color: cssVar("--accent") });
  }, [theme]);

  // 单条净值折线（有 overlays 时清空，让位给对比曲线）。
  useEffect(() => {
    const series = equitySeriesRef.current;
    if (!series) return;
    if (hasOverlays || !equity || equity.length === 0) {
      series.setData([]);
      return;
    }
    // lightweight-charts 要求 time 严格升序且不重复；按 ts 去重（保留最后值）后升序。
    const byTs = new Map<number, number>();
    for (const p of equity) byTs.set(p.ts, p.equity);
    const data = Array.from(byTs, ([ts, value]) => ({ time: ts as UTCTimestamp, value }));
    data.sort((a, b) => (a.time as number) - (b.time as number));
    series.setData(data);
  }, [equity, hasOverlays]);

  // 多策略对比净值叠加（compare 模式）：归一化为百分比，挂右价轴。
  useEffect(() => {
    const chart = chartRef.current;
    if (!chart) return;
    const map = overlaySeriesRef.current;
    const next = overlays ?? [];
    const nextIds = new Set(next.map((o) => o.id));
    for (const [id, series] of map) {
      if (!nextIds.has(id)) {
        chart.removeSeries(series);
        map.delete(id);
      }
    }
    for (const ov of next) {
      let series = map.get(ov.id);
      if (!series) {
        series = chart.addLineSeries({
          priceScaleId: "right",
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
  }, [overlays]);

  return (
    <div className="relative h-full w-full">
      <div ref={containerRef} className="h-full w-full" />

      {isEmpty && !loading && !error && (
        <div className="absolute inset-0 flex items-center justify-center">
          <div className="rounded-[12px] border border-[var(--border)] bg-[var(--surface)] px-5 py-4 text-center text-xs text-[var(--muted)] shadow-soft">
            {emptyHint ?? "运行回测 / 模拟后显示净值曲线"}
          </div>
        </div>
      )}

      {loading && !error && (
        <div className="absolute left-1/2 top-3 -translate-x-1/2 rounded-full bg-[var(--surface)] px-3 py-1 text-xs text-[var(--muted)] shadow-soft">
          正在加载净值…
        </div>
      )}

      {error && (
        <div className="absolute inset-0 flex items-center justify-center bg-[var(--bg)]/80 backdrop-blur-sm">
          <div className="rounded-[12px] border border-[var(--short)] bg-[var(--surface)] px-5 py-4 text-center text-xs text-[var(--short)] shadow-soft">
            {error}
          </div>
        </div>
      )}
    </div>
  );
}
