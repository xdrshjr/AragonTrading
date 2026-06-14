"use client";

import dynamic from "next/dynamic";
import { useEffect, useMemo, useState } from "react";
import PositionPanel from "@/components/Strategy/PositionPanel";
import TradesTable from "@/components/Strategy/TradesTable";
import { Tabs } from "@/components/ui";
import type { IndicatorKey } from "@/lib/indicators";
import { useAppStore } from "@/lib/store";
import type { BottomTab, Candle, DataSource, EquityPoint, Overlay, Trade } from "@/lib/types";
import ChartHeaderBar from "./ChartHeaderBar";
import ChartToolbar, { type ChartToolbarProps } from "./ChartToolbar";
import InstrumentList from "./InstrumentList";

// lightweight-charts 仅客户端渲染（R6）。
const CandleChart = dynamic(() => import("./CandleChart"), { ssr: false });
const EquityChart = dynamic(() => import("./EquityChart"), { ssr: false });

const BOTTOM_TABS = [
  { key: "trades", label: "成交记录" },
  { key: "positions", label: "当前持仓" },
];

// 左侧工作区容器（chart-workspace-layout #11，R4）：80/20 上下 + 上区 20/80 左右；
// 自持 hoverTs 及 displayCandle/prevClose 派生，把新引入的复杂度封装在组件内，page 退为薄编排者。
// 单一 props 对象入参（组件 props 计为一个对象，满足 CLAUDE.md「形参 ≤5」）。
interface ChartWorkspaceProps {
  candles: Candle[];
  overlayEquity?: EquityPoint[];
  overlays?: Overlay[];
  overlayTrades?: Trade[];
  indicators: Record<IndicatorKey, boolean>;
  theme: "light" | "dark";
  lastPrice: number | null; // 跟随实时时的最新价；否则 null（OHLC / markPrice 回落最新收盘）
  dataSource: DataSource;
  chartLoading: boolean;
  chartError: string | null;
  onLoadMore: (beforeTs: number) => void;
  jumpToTs: number | null;
  toolbar: ChartToolbarProps; // 透传工具栏的连接 / 区间状态
}

export default function ChartWorkspace(props: ChartWorkspaceProps) {
  const {
    candles,
    overlayEquity,
    overlays,
    overlayTrades,
    indicators,
    theme,
    lastPrice,
    dataSource,
    chartLoading,
    chartError,
    onLoadMore,
    jumpToTs,
    toolbar,
  } = props;
  const { instId, bar, mode, chartPane, bottomTab, setInst, setChartPane, setBottomTab } =
    useAppStore();

  const [hoverTs, setHoverTs] = useState<number | null>(null);
  // 切标的 / 周期 → 清空 hover，避免残留 ts 让有数据却显示空（R3）。
  useEffect(() => setHoverTs(null), [instId, bar]);

  // 空值合并回落：hoverTs 落空（切标的/周期/视图后残留）时回落最新根而非空显（R3）。
  const byTs = useMemo(() => new Map(candles.map((c) => [c.ts, c])), [candles]);
  const tsIndex = useMemo(() => new Map(candles.map((c, i) => [c.ts, i])), [candles]);
  const displayCandle = (hoverTs != null ? byTs.get(hoverTs) : undefined) ?? candles.at(-1) ?? null;
  const di = displayCandle ? (tsIndex.get(displayCandle.ts) ?? -1) : -1;
  const prevClose = di > 0 ? candles[di - 1].c : (displayCandle?.o ?? null);

  const markPrice = lastPrice ?? candles.at(-1)?.c ?? null;
  // 区间涨跌幅（真实，来自已加载行情首末，§9.3），供 InstrumentList 高亮行展示。
  const sessionChangePct = useMemo(() => {
    const first = candles[0]?.c;
    const last = candles.at(-1)?.c;
    return first && last ? (last - first) / first : null;
  }, [candles]);
  const equitySummary = useMemo(() => {
    const e = overlayEquity;
    if (!e || e.length === 0) return undefined;
    const first = e[0].equity;
    const last = e[e.length - 1].equity;
    return { last, ret: first ? last / first - 1 : null };
  }, [overlayEquity]);

  // equity 视图三模式空态提示（R12/C4）。
  const emptyHint =
    mode === "compare"
      ? "运行多策略对比后显示归一化净值曲线"
      : mode === "paper"
        ? "启动实时模拟后显示净值曲线"
        : "运行回测后显示净值曲线";

  return (
    <section className="flex h-full min-h-0 flex-col border-b border-[var(--border)] lg:border-b-0 lg:border-r">
      {/* 上区 ≈80%（lg 桌面优先；窄屏纵向堆叠，R5） */}
      <div className="flex min-h-0 flex-[8] flex-col lg:flex-row">
        <div className="shrink-0 overflow-auto border-b border-[var(--border)] lg:w-1/5 lg:min-w-[140px] lg:max-w-[240px] lg:border-b-0 lg:border-r">
          <InstrumentList
            active={instId}
            onSelect={setInst}
            lastPrice={lastPrice}
            changePct={sessionChangePct}
          />
        </div>
        <div className="flex min-h-[320px] min-w-0 flex-1 flex-col">
          <div className="shrink-0 border-b border-[var(--border)] px-4 py-2.5">
            <ChartToolbar {...toolbar} />
          </div>
          {/* F2：缓存回退诚实标注——仅当后端返回缓存样例（X-Aragon-Data-Source: cache）时显示 */}
          {dataSource === "cache" && (
            <div className="shrink-0 border-b border-[var(--accent)] bg-[var(--accent-soft)] px-4 py-1.5 text-[11px] font-medium text-[var(--accent)]">
              ⚠️ 缓存样例 · 非实时——OKX 暂不可达，当前显示本地缓存的真实历史（非最新行情）
            </div>
          )}
          <ChartHeaderBar
            view={chartPane}
            onView={setChartPane}
            candle={chartPane === "price" ? displayCandle : null}
            prevClose={prevClose}
            equitySummary={equitySummary}
          />
          <div className="relative min-h-0 flex-1">
            {chartPane === "price" ? (
              <CandleChart
                candles={candles}
                trades={overlayTrades}
                indicators={indicators}
                theme={theme}
                loading={chartLoading}
                error={chartError}
                onLoadMore={onLoadMore}
                jumpToTs={jumpToTs}
                onCrosshairChange={setHoverTs}
              />
            ) : (
              <EquityChart
                equity={overlayEquity}
                overlays={overlays}
                theme={theme}
                emptyHint={emptyHint}
              />
            )}
          </div>
        </div>
      </div>

      {/* 下区 ≈20%，min-h 下限防塌陷（R5） */}
      <div className="flex min-h-[140px] flex-[2] flex-col border-t border-[var(--border)]">
        <div className="shrink-0 border-b border-[var(--border)] px-3 py-2">
          <Tabs
            tabs={BOTTOM_TABS}
            active={bottomTab}
            onChange={(k) => setBottomTab(k as BottomTab)}
          />
        </div>
        <div className="min-h-0 flex-1 overflow-auto px-3 py-2">
          {bottomTab === "trades" ? (
            <TradesTable trades={overlayTrades} />
          ) : (
            <PositionPanel trades={overlayTrades} markPrice={markPrice} instId={instId} />
          )}
        </div>
      </div>
    </section>
  );
}
