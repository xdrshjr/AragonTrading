// 主图 + 各副图的时间轴双向同步（必选）+ 跨图十字光标贯穿（增强）+ 价格轴动态等宽对齐（RV-1）。
// v4.2 不支持原生多窗格（panes 是 v5），副图用多个 createChart 垂直堆叠，靠本模块把缩放 / 平移 /
// 十字光标 / 价格轴宽度对齐到像素级一致，做到「适配正确、完美」（spec §4.4）。
import type { IChartApi, ISeriesApi, MouseEventParams, SeriesType } from "lightweight-charts";

// 价格轴保底下限；真实宽度由 alignPriceScaleWidths 动态测量回写（RV-1）。
export const PRICE_SCALE_MIN_WIDTH = 64;

type AnySeries = ISeriesApi<SeriesType> | null | undefined;

export interface SyncGroup {
  // 注册一个 chart 进入同步组，返回 unregister。getSeries 供跨图十字光标用（取本图任一系列）。
  register(chart: IChartApi, getSeries?: () => AnySeries): () => void;
  // 当前组内所有 chart（主 + 副），供等宽对齐遍历。
  getCharts(): IChartApi[];
}

export function createSyncGroup(): SyncGroup {
  const members = new Map<IChartApi, { getSeries?: () => AnySeries }>();
  let applyingRange = false; // 时间轴广播防回环 guard（R5）
  let applyingCross = false; // 十字光标广播防回环 guard
  let applyingWidths = false; // 重对齐回写价轴宽度期间抑制再次调度，断自激反馈环（RV-1 缩放补丁）
  let alignRaf: number | null = null; // rAF 合并去抖句柄：同一帧多次范围事件只重对齐一次

  // 可见逻辑范围变化（缩放/平移）后，按 requestAnimationFrame 合并地重对齐主/副图左右价轴等宽。
  // 这是缩放错位根因的修复点：原等宽对齐只在容器 resize / 量级等离散事件触发，缩放路径从不重对齐，
  // 致两图右价轴宽各自漂移、绘图区边缘错位。此处补上缩放触发，同一帧多次 range 事件只对齐一次。
  const scheduleAlign = () => {
    if (typeof requestAnimationFrame === "undefined" || alignRaf !== null) return;
    alignRaf = requestAnimationFrame(() => {
      alignRaf = null;
      applyingWidths = true;
      try {
        // 热路径用「非重置」变体：不先塌回保底下限，避免逐帧闪缩；写入收敛到不动点即自然停手。
        realignPriceScaleWidths(Array.from(members.keys()));
      } finally {
        applyingWidths = false;
      }
    });
  };

  return {
    register(chart, getSeries) {
      members.set(chart, { getSeries });

      // 时间轴：任一图可见逻辑范围变化 → 广播到其余图（applying 期间忽略他图回调，防回环）。
      const onRange = (range: { from: number; to: number } | null) => {
        if (applyingRange || applyingWidths || !range) return;
        applyingRange = true;
        try {
          for (const other of members.keys()) {
            if (other === chart) continue;
            try {
              other.timeScale().setVisibleLogicalRange(range);
            } catch {
              /* 目标图暂无数据时忽略 */
            }
          }
        } finally {
          applyingRange = false;
        }
        // 广播完逻辑范围后，合并调度一次价轴等宽重对齐，使缩放后两图绘图区左右边缘持续一致。
        scheduleAlign();
      };
      chart.timeScale().subscribeVisibleLogicalRangeChange(onRange as never);

      // 十字光标：源图取 param.time → 其余图 setCrosshairPosition（无 time 时 clear）。
      const onCross = (param: MouseEventParams) => {
        if (applyingCross) return;
        applyingCross = true;
        try {
          for (const [other, meta] of members) {
            if (other === chart) continue;
            const s = meta.getSeries?.();
            if (!s) continue;
            if (param.time !== undefined) {
              other.setCrosshairPosition(NaN, param.time, s);
            } else {
              other.clearCrosshairPosition();
            }
          }
        } finally {
          applyingCross = false;
        }
      };
      chart.subscribeCrosshairMove(onCross);

      // 新图注册后，于下一帧（数据已布局）对齐到组内首个 chart（主图）的当前可见范围，
      // 避免新开 pane 初始范围不一致（register 在 mount 时早于 setData，故延一帧，RV-1 同理）。
      if (members.size > 1) {
        const ref = members.keys().next().value as IChartApi | undefined;
        if (ref && ref !== chart && typeof requestAnimationFrame !== "undefined") {
          requestAnimationFrame(() => {
            if (!members.has(chart) || !members.has(ref)) return;
            const r = ref.timeScale().getVisibleLogicalRange();
            if (r) {
              try {
                chart.timeScale().setVisibleLogicalRange(r);
              } catch {
                /* ignore */
              }
            }
          });
        }
      }

      return () => {
        try {
          chart.timeScale().unsubscribeVisibleLogicalRangeChange(onRange as never);
          chart.unsubscribeCrosshairMove(onCross);
        } catch {
          /* chart 可能已 remove */
        }
        members.delete(chart);
      };
    },

    getCharts() {
      return Array.from(members.keys());
    },
  };
}

// 价格轴动态等宽对齐（RV-1）：主图右价轴宽度随价格量级变化（默认 BTC「63,000.0」≈6 万），
// 副图数值小（如「70」「-120」）。静态 minimumWidth 常量无法对齐——v4.2 typings 明确
// minimumWidth 只是下限、内容更宽时会被超过。故：先把各图同侧价轴重置回保底下限（使切到
// 低价标的时能重新收窄），下一帧读出真实像素宽，取同侧最大值回写为所有图的 minimumWidth，
// 使各图绘图区左右边缘一致 → 时间轴像素级对齐。
export function alignPriceScaleWidths(
  charts: IChartApi[],
  baseMin = PRICE_SCALE_MIN_WIDTH,
): void {
  if (charts.length === 0 || typeof requestAnimationFrame === "undefined") return;

  // 1. 重置下限（让低价标的能重新收窄，避免一次变宽后永久占位）
  for (const c of charts) {
    applyMinWidth(c, "left", baseMin);
    applyMinWidth(c, "right", baseMin);
  }

  // 2. 下一帧布局稳定后测真实宽，取同侧最大值回写
  requestAnimationFrame(() => {
    let maxLeft = baseMin;
    let maxRight = baseMin;
    for (const c of charts) {
      maxLeft = Math.max(maxLeft, measureWidth(c, "left"));
      maxRight = Math.max(maxRight, measureWidth(c, "right"));
    }
    for (const c of charts) {
      applyMinWidth(c, "left", maxLeft);
      applyMinWidth(c, "right", maxRight);
    }
  });
}

// 缩放/平移热路径专用的「非重置」等宽对齐（RV-1 缩放补丁，analysis 方案 A）。与 alignPriceScaleWidths
// 不同：不先把价轴重置回保底下限，而是直接测量各图当前左右价轴像素宽、取同侧最大值，仅当某图该侧实测
// 宽与目标值不同才回写 minimumWidth。两点收益：① 不重置 → 杜绝「先塌到 64 再撑开」的逐帧闪缩；
// ② 写入收敛到不动点（各图同侧宽相等后不再 applyOptions）→ 天然打断「对齐→改宽→范围事件→再对齐」
// 自激反馈环。代价：单标的会话内不回收多余宽度（可接受）；换标的需收窄时仍由重置版
// alignPriceScaleWidths 负责（绑定 priceMagnitude 等离散事件）。同时处理左右两侧，兼顾净值视图下
// 主图左轴随可见净值区间漂移的情形。
export function realignPriceScaleWidths(
  charts: IChartApi[],
  baseMin = PRICE_SCALE_MIN_WIDTH,
): void {
  if (charts.length === 0) return;
  let maxLeft = baseMin;
  let maxRight = baseMin;
  for (const c of charts) {
    maxLeft = Math.max(maxLeft, measureWidth(c, "left"));
    maxRight = Math.max(maxRight, measureWidth(c, "right"));
  }
  for (const c of charts) {
    applyMinWidthIfChanged(c, "left", maxLeft);
    applyMinWidthIfChanged(c, "right", maxRight);
  }
}

function measureWidth(chart: IChartApi, id: "left" | "right"): number {
  try {
    return chart.priceScale(id).width();
  } catch {
    return 0;
  }
}

// 仅当目标宽度与当前实测像素宽不同（容差 1px）才写入 minimumWidth：已对齐则不动，达到不动点即停手，
// 使缩放热路径的重对齐天然收敛、不自激（区别于无条件回写的 applyMinWidth）。
function applyMinWidthIfChanged(
  chart: IChartApi,
  id: "left" | "right",
  minimumWidth: number,
): void {
  try {
    const scale = chart.priceScale(id);
    if (Math.abs(scale.width() - minimumWidth) < 1) return;
    scale.applyOptions({ minimumWidth });
  } catch {
    /* 价格轴不可用时忽略 */
  }
}

function applyMinWidth(chart: IChartApi, id: "left" | "right", minimumWidth: number): void {
  try {
    chart.priceScale(id).applyOptions({ minimumWidth });
  } catch {
    /* 价格轴不可用时忽略 */
  }
}
