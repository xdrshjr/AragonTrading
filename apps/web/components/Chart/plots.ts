// 通用系列协调：按 Plot[] 泛化增 / 删 / 更新 line·histogram 系列（主图 overlay 与每个副图 pane 共用）。
// 这是「注册表驱动、零渲染改动加指标」的关键（spec §4.5）。
import type {
  AutoscaleInfoProvider,
  IChartApi,
  ISeriesApi,
  LineStyle,
} from "lightweight-charts";
import type { Plot } from "@/lib/indicators";

export type PlotSeries = ISeriesApi<"Line" | "Histogram">;

export interface ReconcileOpts {
  priceScaleId?: string;
  // 副图固定值域时给定（如 RSI 0~100）；须挂到该 pane 全部系列（RV-4），故由调用方统一传入。
  autoscaleInfoProvider?: AutoscaleInfoProvider;
}

export function reconcilePlots(
  chart: IChartApi,
  map: Map<string, PlotSeries>,
  plots: Plot[],
  opts: ReconcileOpts = {},
): void {
  const nextIds = new Set(plots.map((p) => p.id));

  // 移除已不在本轮的系列，释放句柄（防泄漏 R6）。
  // id 契约（RV-3）：reconcilePlots 对 id 语义不可知，只要求「同一 plot.id 唯一对应 map 中同一系列」。
  // 叠加层多指标共用一个 map → 调用方须先把 plot.id 命名空间化（${def.key}:${plot.id}）再传入；
  // pane 各自独占 map/chart → 裸 id 即唯一。map 键 / nextIds / 建系列三处必须用同一 id。
  for (const [id, series] of map) {
    if (!nextIds.has(id)) {
      chart.removeSeries(series);
      map.delete(id);
    }
  }

  for (const plot of plots) {
    let series = map.get(plot.id);
    if (!series) {
      if (plot.kind === "line") {
        series = chart.addLineSeries({
          priceScaleId: opts.priceScaleId,
          color: plot.color,
          lineWidth: plot.lineWidth ?? 1,
          lineStyle: plot.lineStyle as LineStyle | undefined,
          title: plot.title,
          lastValueVisible: false,
          priceLineVisible: false,
          autoscaleInfoProvider: opts.autoscaleInfoProvider,
        });
      } else {
        series = chart.addHistogramSeries({
          priceScaleId: opts.priceScaleId,
          title: plot.title,
          base: plot.base ?? 0,
          lastValueVisible: false,
          priceLineVisible: false,
          autoscaleInfoProvider: opts.autoscaleInfoProvider,
        });
      }
      map.set(plot.id, series);
    } else if (plot.kind === "line") {
      // 更新颜色 / 标题 / 线型，应对主题切换（spec §4.8）
      series.applyOptions({
        color: plot.color,
        title: plot.title,
        lineStyle: plot.lineStyle as LineStyle | undefined,
      });
    } else {
      series.applyOptions({ title: plot.title });
    }

    // 全量 setData（~300–600 根规模成本可忽略，R3）。line 用 {time,value}，histogram 用 {time,value,color}。
    if (plot.kind === "line") {
      (series as ISeriesApi<"Line">).setData(plot.data);
    } else {
      (series as ISeriesApi<"Histogram">).setData(plot.data);
    }
  }
}
