"use client";

import dynamic from "next/dynamic";
import { useEffect, useMemo, useState } from "react";
import PositionPanel from "@/components/Strategy/PositionPanel";
import TradesTable from "@/components/Strategy/TradesTable";
import { Tabs } from "@/components/ui";
import type { IndicatorKey } from "@/lib/indicators";
import { useAppStore } from "@/lib/store";
import type {
  BottomTab,
  Candle,
  DataSource,
  EquityPoint,
  LiveMarketState,
  Overlay,
  PaperStrategySession,
  Trade,
} from "@/lib/types";
import ChartHeaderBar from "./ChartHeaderBar";
import ChartToolbar, { type ChartToolbarProps } from "./ChartToolbar";
import InstrumentList from "./InstrumentList";
import MarketDepthPanel from "./MarketDepthPanel";

const CandleChart = dynamic(() => import("./CandleChart"), { ssr: false });
const EquityChart = dynamic(() => import("./EquityChart"), { ssr: false });

const BOTTOM_TABS = [
  { key: "trades", label: "成交记录" },
  { key: "positions", label: "当前持仓" },
];

interface ChartWorkspaceProps {
  candles: Candle[];
  overlayEquity?: EquityPoint[];
  overlays?: Overlay[];
  overlayTrades?: Trade[];
  bottomTrades?: Trade[];
  tradeCapReached?: boolean;
  indicators: Record<IndicatorKey, boolean>;
  theme: "light" | "dark";
  lastPrice: number | null;
  market: LiveMarketState;
  dataSource: DataSource;
  chartLoading: boolean;
  chartError: string | null;
  onLoadMore: (beforeTs: number) => void;
  jumpToTs: number | null;
  toolbar: ChartToolbarProps;
  paperStrategies?: PaperStrategySession[];
  activePaperStrategyId?: number | null;
  visiblePaperStrategyIds?: number[];
  onActivePaperStrategyChange?: (id: number) => void;
  onToggleVisiblePaperStrategy?: (id: number) => void;
}

export default function ChartWorkspace(props: ChartWorkspaceProps) {
  const {
    candles,
    overlayEquity,
    overlays,
    overlayTrades,
    bottomTrades,
    tradeCapReached,
    indicators,
    theme,
    lastPrice,
    market,
    dataSource,
    chartLoading,
    chartError,
    onLoadMore,
    jumpToTs,
    toolbar,
    paperStrategies = [],
    activePaperStrategyId,
    visiblePaperStrategyIds = [],
    onActivePaperStrategyChange,
    onToggleVisiblePaperStrategy,
  } = props;
  const {
    instId,
    bar,
    mode,
    chartPane,
    bottomTab,
    paneTab,
    paperSessionId,
    setInst,
    setChartPane,
    setBottomTab,
    setPaneTab,
  } = useAppStore();

  const [hoverTs, setHoverTs] = useState<number | null>(null);
  useEffect(() => setHoverTs(null), [instId, bar]);

  const byTs = useMemo(() => new Map(candles.map((c) => [c.ts, c])), [candles]);
  const tsIndex = useMemo(() => new Map(candles.map((c, i) => [c.ts, i])), [candles]);
  const displayCandle = (hoverTs != null ? byTs.get(hoverTs) : undefined) ?? candles.at(-1) ?? null;
  const displayIndex = displayCandle ? (tsIndex.get(displayCandle.ts) ?? -1) : -1;
  const prevClose = displayIndex > 0 ? candles[displayIndex - 1].c : (displayCandle?.o ?? null);
  const markPrice = lastPrice ?? candles.at(-1)?.c ?? null;

  const sessionChangePct = useMemo(() => {
    const first = candles[0]?.c;
    const last = candles.at(-1)?.c;
    return first && last ? (last - first) / first : null;
  }, [candles]);

  const equitySummary = useMemo(() => {
    const equity = overlayEquity;
    if (!equity?.length) return undefined;
    const first = equity[0].equity;
    const last = equity[equity.length - 1].equity;
    return { last, ret: first ? last / first - 1 : null };
  }, [overlayEquity]);

  const emptyHint =
    mode === "compare"
      ? "运行多策略对比后显示归一化净值曲线"
      : mode === "paper"
        ? "启动实时模拟后显示净值曲线"
        : "运行回测后显示净值曲线";

  return (
    <section className="flex h-full min-h-0 flex-col border-b border-[var(--border)] lg:border-b-0 lg:border-r">
      <div className="flex min-h-0 flex-[8] flex-col lg:flex-row">
        <div className="shrink-0 overflow-auto border-b border-[var(--border)] lg:basis-[10%] lg:min-w-[128px] lg:max-w-[180px] lg:border-b-0 lg:border-r">
          <MarketDepthPanel market={market} activeCandle={displayCandle} />
          <InstrumentList
            active={instId}
            onSelect={setInst}
            lastPrice={market.lastPrice}
            changePct={sessionChangePct}
          />
        </div>
        <div className="flex min-h-[320px] min-w-0 flex-1 flex-col">
          <div className="shrink-0 border-b border-[var(--border)] px-4 py-2.5">
            <ChartToolbar {...toolbar} />
          </div>
          {dataSource === "cache" && (
            <div className="shrink-0 border-b border-[var(--accent)] bg-[var(--accent-soft)] px-4 py-1.5 text-[11px] font-medium text-[var(--accent)]">
              缓存样例 · OKX 暂不可达，当前显示本地缓存历史。
            </div>
          )}
          {tradeCapReached && (
            <div className="shrink-0 border-b border-[var(--border)] bg-[var(--surface-2)] px-4 py-1.5 text-[11px] font-medium text-[var(--muted)]">
              Backtest trade cap reached: showing the latest 5000 returned trades.
            </div>
          )}
          <ChartHeaderBar
            view={chartPane}
            onView={setChartPane}
            candle={chartPane === "price" ? displayCandle : null}
            prevClose={prevClose}
            equitySummary={equitySummary}
            paperStrategies={paperStrategies}
            visiblePaperStrategyIds={visiblePaperStrategyIds}
            onTogglePaperStrategy={onToggleVisiblePaperStrategy}
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
                paneTab={paneTab}
                onPaneTabChange={setPaneTab}
                livePrice={market.lastPrice}
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

      <div className="flex min-h-[140px] flex-[2] flex-col border-t border-[var(--border)]">
        {mode === "paper" && paperSessionId && (
          <div className="shrink-0 border-b border-[var(--border)] bg-[var(--surface-2)] px-3 py-1.5 text-[11px] text-[var(--muted)]">
            已启动实时模拟 · 下方成交和持仓按策略标签切换查看。
          </div>
        )}
        <div className="shrink-0 px-3 pt-2">
          <Tabs
            variant="underline"
            tabs={BOTTOM_TABS}
            active={bottomTab}
            onChange={(k) => setBottomTab(k as BottomTab)}
          />
        </div>
        {paperStrategies.length > 1 && (
          <div className="shrink-0 overflow-x-auto px-3 pt-1">
            <div className="flex min-w-max gap-1">
              {paperStrategies.map((strategy) => {
                const active = activePaperStrategyId === strategy.strategyId;
                return (
                  <button
                    key={strategy.strategyId}
                    type="button"
                    onClick={() => onActivePaperStrategyChange?.(strategy.strategyId)}
                    className="h-7 rounded border px-2 text-[11px] font-medium"
                    style={{
                      borderColor: active ? strategy.color : "var(--border)",
                      color: active ? "var(--text)" : "var(--muted)",
                      background: active ? "var(--surface-2)" : "transparent",
                    }}
                  >
                    {strategy.strategyName}
                  </button>
                );
              })}
            </div>
          </div>
        )}
        <div className="min-h-0 flex-1 overflow-auto px-3 py-2">
          {bottomTab === "trades" ? (
            <TradesTable trades={bottomTrades ?? overlayTrades} />
          ) : (
            <PositionPanel trades={bottomTrades ?? overlayTrades} markPrice={markPrice} instId={instId} />
          )}
        </div>
      </div>
    </section>
  );
}
