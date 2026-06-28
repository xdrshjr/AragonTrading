"use client";

import type {
  IChartApi,
  IPriceLine,
  ISeriesApi,
  LineStyle,
  MouseEventParams,
} from "lightweight-charts";
import { type RefObject, useEffect, useRef, useState } from "react";
import type { OrderPriceLine } from "@/lib/openOrders";

// 命中阈值（像素）：鼠标 Y 与某条委托线 Y 之差 ≤ 此值即视为悬停其上（可调，R1）。
const HOVER_PX = 6;

export interface OrderLineHover {
  line: OrderPriceLine;
  y: number; // 该线在主图坐标系中的 Y 像素（浮层 top 取此值贴合虚线）
}

function cssVar(name: string): string {
  if (typeof window === "undefined") return "#888";
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

/**
 * 主图委托价格线 reconcile + 悬停命中（C4：从 CandleChart 抽出，控制其行数与圈复杂度）。
 *
 * - 增量维护虚线（创建 / 更新 / 删除），`axisLabelVisible` 令最右价轴常显价格；`theme` 变化重着色。
 * - 自订阅十字光标移动做命中判定（与 CandleChart 既有 onCross 互不干扰），返回当前悬停的线 + Y。
 *
 * 调用须置于 CandleChart 创建图表 effect **之后**，确保挂载时 `chartRef`/`seriesRef` 已就绪。
 */
export function useOrderPriceLines(
  chartRef: RefObject<IChartApi | null>,
  seriesRef: RefObject<ISeriesApi<"Candlestick"> | null>,
  orderLines: OrderPriceLine[] | undefined,
  theme: "light" | "dark",
): OrderLineHover | null {
  const lineRefs = useRef<Map<string, IPriceLine>>(new Map());
  const orderLinesRef = useRef<OrderPriceLine[]>(orderLines ?? []);
  orderLinesRef.current = orderLines ?? [];
  const [hover, setHover] = useState<OrderLineHover | null>(null);

  // 增量 reconcile 价格线：删除已撤销线、新增 / 更新现存线（复用 livePrice 价格线写法，R5）。
  useEffect(() => {
    const series = seriesRef.current;
    if (!series) return;
    const map = lineRefs.current;
    const next = orderLines ?? [];
    const nextIds = new Set(next.map((line) => line.id));
    for (const [id, priceLine] of map) {
      if (!nextIds.has(id)) {
        series.removePriceLine(priceLine);
        map.delete(id);
      }
    }
    for (const line of next) {
      const options = {
        price: line.price,
        color: cssVar(line.colorVar),
        lineWidth: 1 as const,
        lineStyle: line.lineStyle as LineStyle,
        axisLabelVisible: true,
        title: line.label,
      };
      const existing = map.get(line.id);
      if (existing) existing.applyOptions(options);
      else map.set(line.id, series.createPriceLine(options));
    }
    if (next.length === 0) setHover(null);
  }, [orderLines, theme, seriesRef]);

  // 订阅十字光标：遍历当前委托线，命中最近一条（≤HOVER_PX）则置悬停态（R1/R6 仅高亮最近一条）。
  useEffect(() => {
    const chart = chartRef.current;
    const series = seriesRef.current;
    if (!chart || !series) return;
    const onMove = (param: MouseEventParams) => {
      const point = param.point;
      if (!point) {
        setHover(null);
        return;
      }
      let best: OrderLineHover | null = null;
      let bestDist = HOVER_PX;
      for (const line of orderLinesRef.current) {
        const y = series.priceToCoordinate(line.price);
        if (y == null) continue;
        const dist = Math.abs(y - point.y);
        if (dist <= bestDist) {
          bestDist = dist;
          best = { line, y };
        }
      }
      setHover(best);
    };
    chart.subscribeCrosshairMove(onMove);
    return () => {
      chart.unsubscribeCrosshairMove(onMove);
      for (const priceLine of lineRefs.current.values()) series.removePriceLine(priceLine);
      lineRefs.current.clear();
    };
  }, [chartRef, seriesRef]);

  return hover;
}
