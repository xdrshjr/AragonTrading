"use client";

import dynamic from "next/dynamic";
import { useEffect, useMemo, useRef, useState } from "react";
import LiveBottomPanel from "@/components/Live/LiveBottomPanel";
import PaperPositionsTable from "@/components/Strategy/PaperPositionsTable";
import PositionPanel from "@/components/Strategy/PositionPanel";
import TradesTable from "@/components/Strategy/TradesTable";
import { Tabs } from "@/components/ui";
import PanelResizeHandle from "@/components/ui/PanelResizeHandle";
import type { IndicatorKey } from "@/lib/indicators";
import { ratioFromRightEdge } from "@/lib/panelResize";
import { buildPaperPositionRows } from "@/lib/paperMulti";
import {
  DEFAULT_CHART_SPLIT_RATIO,
  DEFAULT_DEPTH_PANEL_RATIO,
  useAppStore,
} from "@/lib/store";
import type {
  BottomTab,
  Candle,
  DataSource,
  EquityPoint,
  LiveMarketState,
  Overlay,
  PaperPositionEvent,
  PaperStrategySession,
  StrategyEquityPoint,
  StrategyTrade,
  Trade,
} from "@/lib/types";
import ChartHeaderBar from "./ChartHeaderBar";
import ChartToolbar, { type ChartToolbarProps } from "./ChartToolbar";
import InstrumentSidebar from "./InstrumentSidebar";
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
  paperPositionsByStrategyId?: Record<number, PaperPositionEvent | null>;
  paperEquityByStrategyId?: Record<number, StrategyEquityPoint[]>;
  paperTradesByStrategyId?: Record<number, StrategyTrade[]>;
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
    paperPositionsByStrategyId = {},
    paperEquityByStrategyId = {},
    paperTradesByStrategyId = {},
  } = props;
  const {
    instId,
    bar,
    mode,
    accountMode,
    chartPane,
    bottomTab,
    paneTab,
    paperSessionId,
    chartSplitRatio,
    depthPanelRatio,
    setInst,
    setChartPane,
    setBottomTab,
    setPaneTab,
    setChartSplitRatio,
    setDepthPanelRatio,
  } = useAppStore();

  const [hoverTs, setHoverTs] = useState<number | null>(null);
  useEffect(() => setHoverTs(null), [instId, bar]);

  // 纵向分栏拖拽：以 section 为测量容器，比例 = 指针 Y 相对 section 顶部的占比（store 负责夹取）。
  // setPointerCapture 把拖拽期事件锁定到手柄，卸载即自动释放，无需手动清理 window 监听。
  const splitContainerRef = useRef<HTMLElement>(null);
  const splitDraggingRef = useRef(false);
  const onSplitPointerDown = (e: React.PointerEvent<HTMLDivElement>) => {
    e.preventDefault();
    e.currentTarget.setPointerCapture(e.pointerId);
    splitDraggingRef.current = true;
  };
  const onSplitPointerMove = (e: React.PointerEvent<HTMLDivElement>) => {
    if (!splitDraggingRef.current) return;
    const rect = splitContainerRef.current?.getBoundingClientRect();
    if (!rect || rect.height <= 0) return;
    setChartSplitRatio((e.clientY - rect.top) / rect.height);
  };
  const onSplitPointerUp = (e: React.PointerEvent<HTMLDivElement>) => {
    splitDraggingRef.current = false;
    if (e.currentTarget.hasPointerCapture(e.pointerId)) {
      e.currentTarget.releasePointerCapture(e.pointerId);
    }
  };
  const onSplitReset = () => setChartSplitRatio(DEFAULT_CHART_SPLIT_RATIO);

  // 盘口列调宽：以上区 flex-row 为测量容器，比例 = (右边缘 − 指针 X) / 容器宽（store 负责夹取）。
  const topRef = useRef<HTMLDivElement>(null);
  const onResizeDepth = (clientX: number) => {
    const rect = topRef.current?.getBoundingClientRect();
    if (!rect) return;
    setDepthPanelRatio(ratioFromRightEdge(rect.right, rect.width, clientX));
  };
  const onDepthReset = () => setDepthPanelRatio(DEFAULT_DEPTH_PANEL_RATIO);

  const byTs = useMemo(() => new Map(candles.map((c) => [c.ts, c])), [candles]);
  const tsIndex = useMemo(() => new Map(candles.map((c, i) => [c.ts, i])), [candles]);
  const displayCandle = (hoverTs != null ? byTs.get(hoverTs) : undefined) ?? candles.at(-1) ?? null;
  const displayIndex = displayCandle ? (tsIndex.get(displayCandle.ts) ?? -1) : -1;
  const prevClose = displayIndex > 0 ? candles[displayIndex - 1].c : (displayCandle?.o ?? null);
  const markPrice = lastPrice ?? candles.at(-1)?.c ?? null;

  const paperPositionRows = useMemo(
    () =>
      buildPaperPositionRows(
        paperStrategies,
        paperPositionsByStrategyId,
        paperTradesByStrategyId,
        paperEquityByStrategyId,
        markPrice,
      ),
    [
      paperStrategies,
      paperPositionsByStrategyId,
      paperTradesByStrategyId,
      paperEquityByStrategyId,
      markPrice,
    ],
  );

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
      : mode === "combo"
        ? "运行组合回测后显示组合净值曲线"
        : mode === "paper"
          ? "启动实时模拟后显示净值曲线"
          : "运行回测后显示净值曲线";

  return (
    <section
      ref={splitContainerRef}
      className="flex h-full min-h-0 min-w-0 flex-col"
    >
      <div
        ref={topRef}
        className="flex min-h-0 min-w-0 flex-row"
        style={{ flexGrow: chartSplitRatio, flexShrink: 1, flexBasis: 0 }}
      >
        {/* 左栏：盘口面板已迁出到 K 线右侧（需求二），此处仅保留标的列表并占满整列高度。 */}
        <div className="flex min-h-0 shrink-0 basis-[16%] min-w-[220px] max-w-[300px] flex-col border-r border-[var(--border)]">
          <div className="min-h-0 flex-1">
            <InstrumentSidebar
              active={instId}
              onSelect={setInst}
              livePrice={market.lastPrice}
              changePct={sessionChangePct}
            />
          </div>
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

        {/* 需求二：盘口 / 成交量列移到 K 线右侧，默认约 15%，左边框可拖（夹取 [0.10,0.30] + 像素 [160,420]）。 */}
        <PanelResizeHandle
          onResize={onResizeDepth}
          onReset={onDepthReset}
          ariaLabel="拖动调整盘口面板宽度，双击复位"
        />
        <div
          className="flex min-h-0 min-w-[160px] max-w-[420px] flex-col overflow-auto"
          style={{ flexGrow: 0, flexShrink: 0, flexBasis: `${depthPanelRatio * 100}%` }}
        >
          <MarketDepthPanel market={market} activeCandle={displayCandle} />
        </div>
      </div>

      <div
        role="separator"
        aria-orientation="horizontal"
        title="拖动调整图表与下方面板高度，双击复位"
        onPointerDown={onSplitPointerDown}
        onPointerMove={onSplitPointerMove}
        onPointerUp={onSplitPointerUp}
        onDoubleClick={onSplitReset}
        style={{ touchAction: "none" }}
        className="group flex h-1.5 shrink-0 cursor-row-resize items-center justify-center border-t border-[var(--border)] bg-[var(--surface-2)] hover:bg-[var(--accent-soft)]"
      >
        <div className="h-0.5 w-8 rounded-full bg-[var(--border)] group-hover:bg-[var(--accent)]" />
      </div>

      <div
        className="flex min-h-[140px] flex-col"
        style={{ flexGrow: 1 - chartSplitRatio, flexShrink: 1, flexBasis: 0 }}
      >
        {accountMode === "live" ? (
          // 实盘模式：下方改为左右分栏（左下单面板 / 右成交记录 · 当前持仓，需求）。
          <LiveBottomPanel instId={instId} markPrice={markPrice} />
        ) : (
          <>
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
          ) : mode === "paper" ? (
            <PaperPositionsTable rows={paperPositionRows} instId={instId} />
          ) : (
            <PositionPanel trades={bottomTrades ?? overlayTrades} markPrice={markPrice} instId={instId} />
          )}
        </div>
          </>
        )}
      </div>
    </section>
  );
}
