"use client";

import { useQuery } from "@tanstack/react-query";
import clsx from "clsx";
import type { Dispatch, MouseEventHandler, SetStateAction } from "react";
import { useEffect, useMemo, useRef, useState } from "react";
import ChartWorkspace from "@/components/Chart/ChartWorkspace";
import AppMenu from "@/components/Desktop/AppMenu";
import WindowControls from "@/components/Desktop/WindowControls";
import AccountModeSwitch from "@/components/Header/AccountModeSwitch";
import FactorButton from "@/components/Factor/FactorButton";
import FactorLab from "@/components/Factor/FactorLab";
import RightPanel from "@/components/RightPanel/RightPanel";
import SettingsGearButton from "@/components/Settings/SettingsGearButton";
import SettingsPanel from "@/components/Settings/SettingsPanel";
import { Button } from "@/components/ui";
import PanelResizeHandle from "@/components/ui/PanelResizeHandle";
import { api } from "@/lib/api";
import { getDesktopPlatform, getWindowControls } from "@/lib/desktop";
import {
  LATEST_CANDLE_LIMIT,
  liveCandleRefreshIntervalMs,
  resolveChartRange,
  shouldApplyPublicCandle,
  shouldStreamMarket,
} from "@/lib/chartLive";
import { colorForIndex } from "@/lib/compare";
import { foldTradeTape } from "@/lib/marketDepth";
import { createMarketThrottle } from "@/lib/marketThrottle";
import { widthFromRightEdge } from "@/lib/panelResize";
import {
  mergePaperEquity,
  mergePaperTrade,
  paperSessionsToOverlays,
  selectFirstAvailablePaperStrategy,
  visiblePaperTrades,
} from "@/lib/paperMulti";
import { DEFAULT_RIGHT_PANEL_WIDTH, useAppStore } from "@/lib/store";
import { liveAutoApi, liveAutoWS } from "@/lib/liveAutoApi";
import type { LiveAutoSnapshot, LiveAutoStatus } from "@/lib/liveAutoTypes";
import type {
  Candle,
  CompareResult,
  EquityPoint,
  LiveMarketState,
  MarketTicker,
  MarketTrade,
  OrderBookSnapshot,
  PaperPositionEvent,
  PaperSnapshot,
  PaperStatus,
  PaperStrategySession,
  StrategyEquityPoint,
  StrategyTrade,
  Trade,
} from "@/lib/types";
import { marketWS, paperWS, type WsEvent } from "@/lib/ws";

const BACKTEST_TRADE_LIMIT = 5000;

export default function Page() {
  const {
    instId,
    bar,
    mode,
    runId,
    chartView,
    accountMode,
    paperSessionId,
    paperSessionsByStrategyId,
    paperVisibleStrategyIds,
    paperActiveStrategyId,
    paperStreamsEnabled,
    liveAutoSessionId,
    liveAutoTrades,
    theme,
    backtestStartTs,
    backtestEndTs,
    indicators,
    showSettings,
    showFactorLab,
    rightPanelWidth,
    setChartView,
    setPaperActiveStrategyId,
    setPaperVisibleStrategyIds,
    togglePaperVisibleStrategyId,
    setLiveAutoSessionId,
    setLiveAutoStatus,
    setLiveAutoTrades,
    appendLiveAutoTrade,
    setLiveAutoPosition,
    toggleTheme,
    setRightPanelWidth,
  } = useAppStore();

  // 回测（含并入回测列表的用户组合）：用单条 run 净值 + 成交 markers 在左图渲染（run-range 链路）。
  // 组合回测产出真实 run（含 equity/trades/metrics），其 run.mode 落库即 "backtest"，故并入后绘制行为不变。
  const singleRunMode = mode === "backtest";

  const [older, setOlder] = useState<Candle[]>([]);
  const [live, setLive] = useState<Candle[]>([]);
  const [forming, setForming] = useState<Candle | null>(null);
  const [lastPrice, setLastPrice] = useState<number | null>(null);
  const [liveWsOpen, setLiveWsOpen] = useState(false);
  const [marketTicker, setMarketTicker] = useState<MarketTicker | null>(null);
  const [orderBook, setOrderBook] = useState<OrderBookSnapshot | null>(null);
  const [tradeTape, setTradeTape] = useState<MarketTrade[]>([]);
  const [lastMarketTs, setLastMarketTs] = useState<number | null>(null);
  const [marketError, setMarketError] = useState<string | null>(null);
  const [paperTradesByStrategyId, setPaperTradesByStrategyId] = useState<
    Record<number, StrategyTrade[]>
  >({});
  const [paperEquityByStrategyId, setPaperEquityByStrategyId] = useState<
    Record<number, StrategyEquityPoint[]>
  >({});
  const [paperStatusByStrategyId, setPaperStatusByStrategyId] = useState<
    Record<number, PaperStatus>
  >({});
  const [paperPositionsByStrategyId, setPaperPositionsByStrategyId] = useState<
    Record<number, PaperPositionEvent | null>
  >({});
  const [jumpToTs, setJumpToTs] = useState<number | null>(null);
  const loadingMore = useRef(false);
  const lastMarketTsRef = useRef<number | null>(null);
  // 右侧面板调宽：以 <main> 右边缘为锚，宽度 = 右边缘 − 指针 X（store 负责夹取 [320,640]）。
  const mainRef = useRef<HTMLElement>(null);
  const onResizeRight = (clientX: number) => {
    const rect = mainRef.current?.getBoundingClientRect();
    if (!rect) return;
    setRightPanelWidth(widthFromRightEdge(rect.right, clientX));
  };

  const health = useQuery({ queryKey: ["health"], queryFn: api.health, refetchInterval: 15_000 });
  const okxUp = health.data ? health.data.okx === "up" : null;
  const latencyMs = health.data?.okxLatencyMs ?? null;
  const lastOkTs = health.data?.lastOkTs ?? null;

  const runQ = useQuery({
    queryKey: ["run", runId],
    queryFn: () => api.run(runId as number),
    enabled: !!runId && singleRunMode,
  });
  const runRange = useMemo(() => {
    const run = runQ.data;
    if (!singleRunMode || !run || run.endTs == null) return null;
    return { instId: run.instId, bar: run.bar, startTs: run.startTs, endTs: run.endTs };
  }, [singleRunMode, runQ.data]);
  const chartRange = useMemo(
    () =>
      resolveChartRange({
        chartView,
        mode,
        runRange,
        instId,
        bar,
        backtestStartTs,
        backtestEndTs,
      }),
    [chartView, mode, runRange, instId, bar, backtestStartTs, backtestEndTs],
  );

  const streamMarket = shouldStreamMarket(mode, okxUp);
  const applyPublicCandle = shouldApplyPublicCandle(chartView, mode, paperSessionId);
  const latestFallback = applyPublicCandle && !liveWsOpen;
  const rangeAvailable = mode === "compare" || !!runRange;

  const compareQ = useQuery<CompareResult | null>({
    queryKey: ["compare", instId, bar, backtestStartTs, backtestEndTs],
    queryFn: () => null,
    enabled: false,
    initialData: null,
  });
  const compareOverlays =
    chartView === "compare-range" && mode === "compare" && compareQ.data
      ? compareQ.data.items.map((item, index) => ({
          id: item.strategyId,
          name: item.name,
          color: colorForIndex(index),
          equity: item.equity,
        }))
      : undefined;

  const candlesQ = useQuery({
    queryKey: [
      "candles",
      chartRange?.instId ?? instId,
      chartRange?.bar ?? bar,
      chartRange?.startTs,
      chartRange?.endTs,
    ],
    queryFn: () =>
      chartRange
        ? api.candlesWithMeta(chartRange.instId, chartRange.bar, 1000, chartRange.endTs, chartRange.startTs)
        : api.candlesWithMeta(instId, bar, 300),
    enabled: chartView === "live-follow" || mode !== "backtest" || !runId || !!chartRange,
    retry: 0,
  });
  const latestCandlesQ = useQuery({
    queryKey: ["candles-latest", instId, bar],
    queryFn: () => api.candlesWithMeta(instId, bar, LATEST_CANDLE_LIMIT),
    enabled: latestFallback,
    retry: 0,
    refetchInterval: liveCandleRefreshIntervalMs(bar),
  });
  const baseCandles = useMemo(() => {
    const rows = candlesQ.data?.candles;
    if (!rows || !chartRange) return rows;
    return rows.filter((c) => chartRange.startTs <= c.ts && c.ts <= chartRange.endTs);
  }, [candlesQ.data?.candles, chartRange]);
  const dataSource = candlesQ.data?.source ?? "live";

  useEffect(() => {
    setOlder([]);
    setLive([]);
    setForming(null);
    setLastPrice(null);
    setMarketTicker(null);
    setOrderBook(null);
    setTradeTape([]);
    setLastMarketTs(null);
    setMarketError(null);
    lastMarketTsRef.current = null;
  }, [instId, bar, chartRange?.startTs, chartRange?.endTs]);

  useEffect(() => {
    if (!latestFallback) return;
    const latest = latestCandlesQ.data?.candles;
    if (!latest?.length) return;
    setLive((prev) => mergeUnique(prev, latest));
    setLastPrice(latest[latest.length - 1].c);
    setForming((prev) => (prev && latest.some((c) => c.ts === prev.ts) ? null : prev));
  }, [latestFallback, latestCandlesQ.data?.candles]);

  const candles = useMemo(
    () => (chartRange ? baseCandles ?? [] : mergeCandles(older, baseCandles ?? [], live, forming)),
    [older, baseCandles, live, forming, chartRange],
  );

  const loadMore = async (beforeTs: number) => {
    if (chartRange || loadingMore.current) return;
    loadingMore.current = true;
    try {
      const more = await api.candles(instId, bar, 300, beforeTs);
      if (more.length) setOlder((prev) => mergeUnique(more, prev));
    } finally {
      loadingMore.current = false;
    }
  };

  const equityQ = useQuery({
    queryKey: ["equity", runId],
    queryFn: () => api.equity(runId as number),
    enabled: !!runId && singleRunMode,
  });
  const tradesQ = useQuery({
    queryKey: ["trades", runId, BACKTEST_TRADE_LIMIT],
    queryFn: () => api.trades(runId as number, BACKTEST_TRADE_LIMIT),
    enabled: !!runId && singleRunMode && chartView === "run-range",
  });

  const paperStrategies = useMemo(
    () => Object.values(paperSessionsByStrategyId),
    [paperSessionsByStrategyId],
  );
  const effectiveVisibleIds =
    paperVisibleStrategyIds.length > 0
      ? paperVisibleStrategyIds
      : paperStrategies.map((session) => session.strategyId);
  const effectiveActivePaperStrategyId = selectFirstAvailablePaperStrategy(
    paperActiveStrategyId,
    paperSessionsByStrategyId,
  );

  useEffect(() => {
    if (paperStrategies.length === 0) return;
    if (paperVisibleStrategyIds.length === 0) {
      setPaperVisibleStrategyIds(paperStrategies.map((session) => session.strategyId));
    }
    const nextActive = selectFirstAvailablePaperStrategy(
      paperActiveStrategyId,
      paperSessionsByStrategyId,
    );
    if (nextActive !== paperActiveStrategyId) setPaperActiveStrategyId(nextActive);
  }, [
    paperStrategies,
    paperVisibleStrategyIds.length,
    paperActiveStrategyId,
    paperSessionsByStrategyId,
    setPaperActiveStrategyId,
    setPaperVisibleStrategyIds,
  ]);

  useEffect(() => {
    if (mode !== "paper" || !paperStreamsEnabled || paperStrategies.length === 0) return;
    setPaperTradesByStrategyId({});
    setPaperEquityByStrategyId({});
    setPaperStatusByStrategyId({});
    setPaperPositionsByStrategyId({});
    setForming(null);

    const clients = paperStrategies.map((session) => {
      const ws = paperWS(session.sessionId, (event: WsEvent) => {
        handlePaperEvent(event, session, {
          setPaperTradesByStrategyId,
          setPaperEquityByStrategyId,
          setPaperStatusByStrategyId,
          setPaperPositionsByStrategyId,
          setLive,
          setForming,
        });
      });
      ws.connect();
      return ws;
    });
    return () => clients.forEach((client) => client.close());
  }, [mode, paperStreamsEnabled, paperStrategies]);

  // 实时自动交易：挂载 / 切到实盘时 GET /live-auto/status 重发现孤儿会话（renderer 刷新后后端会话仍在跑）。
  useEffect(() => {
    if (accountMode !== "live") return;
    let cancelled = false;
    liveAutoApi
      .status()
      .then((res) => {
        if (cancelled) return;
        if (res.session && res.session.state === "running") {
          setLiveAutoSessionId(res.session.sessionId);
          setLiveAutoStatus({
            state: res.session.state,
            sessionPnlUsd: res.session.sessionPnlUsd,
            positionSide: res.session.positionSide,
            budgetUsd: res.session.budgetUsd,
            leverage: res.session.leverage,
          });
        }
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [accountMode, setLiveAutoSessionId, setLiveAutoStatus]);

  // 实时自动交易：会话句柄就绪即订阅 /ws/live-auto/{id}（事件协议与 paper 同构）。
  useEffect(() => {
    if (!liveAutoSessionId) return;
    const ws = liveAutoWS(liveAutoSessionId, (event: WsEvent) => {
      handleLiveAutoEvent(event, {
        setLiveAutoTrades,
        appendLiveAutoTrade,
        setLiveAutoPosition,
        setLiveAutoStatus,
        setLive,
        setForming,
      });
    });
    ws.connect();
    return () => ws.close();
  }, [
    liveAutoSessionId,
    setLiveAutoTrades,
    appendLiveAutoTrade,
    setLiveAutoPosition,
    setLiveAutoStatus,
  ]);

  // 节流约束：ref 推进必须随入帧同步发生（ref 无渲染成本，candle 分支门控要读最新值），
  // 只有 setLastMarketTs 这个 state 写入才纳入节流（由 throttle 的 flush 统一落库）。
  const advanceMarketTs = (ts: number) => {
    lastMarketTsRef.current = Math.max(lastMarketTsRef.current ?? 0, ts);
  };

  useEffect(() => {
    if (!streamMarket) return;
    // 自适应节流器：把高频 book/ticker/trade（含其 lastPrice）缓冲后按 1s/0.5s 节奏一次性刷入 state，
    // 把面板重渲染从 20~40+Hz 降到 1~2Hz（诊断 market-depth-refresh-too-frequent，方案 A）。
    const throttle = createMarketThrottle({
      flush: (frame) => {
        if (frame.book) setOrderBook(frame.book);
        if (frame.ticker) setMarketTicker(frame.ticker);
        // 增量折叠进既有成交带，禁止整段替换（否则成交带每周期被清空重建）。
        if (frame.trades.length) setTradeTape((prev) => foldTradeTape(prev, frame.trades));
        if (frame.lastPrice != null) setLastPrice(frame.lastPrice);
        const marketTs = frame.marketTs;
        if (marketTs != null) setLastMarketTs((prev) => Math.max(prev ?? 0, marketTs));
      },
    });
    const ws = marketWS(
      instId,
      bar,
      (event) => {
        if (event.type === "error") {
          setMarketError(event.data.message); // 故障提示保持即时，不进节流
          return;
        }
        setMarketError(null);
        if (event.type === "trade") {
          advanceMarketTs(event.data.ts);
          throttle.pushTrade(event.data);
          return;
        }
        if (event.type === "ticker") {
          advanceMarketTs(event.data.ts);
          throttle.pushTicker(event.data);
          return;
        }
        if (event.type === "book") {
          advanceMarketTs(event.data.ts);
          throttle.pushBook(event.data);
          return;
        }
        // candle：confirm/forming 维持即时（右侧 K 线需即时落定）；其价格经门控并入节流缓冲，
        // 使 flush 成为 lastPrice 的唯一写入口，避免与节流流互相打架（面板顶部最新价同步降到 1~2Hz）。
        const candle = event.data;
        if ((lastMarketTsRef.current ?? 0) <= candle.ts) throttle.pushPrice(candle.c, candle.ts);
        if (!applyPublicCandle) return;
        if (candle.confirm === true) {
          setLive((prev) => mergeUnique(prev, [candle]));
          setForming(null);
        } else {
          setForming(candle);
        }
      },
      (open) => {
        setLiveWsOpen(open);
        if (open) setMarketError(null);
      },
    );
    ws.connect();
    return () => {
      throttle.dispose(); // instId/bar 变化或卸载时清除定时器并丢弃挂起帧；effect 重建即重置全部节流基线
      ws.close();
      setLiveWsOpen(false);
    };
  }, [streamMarket, applyPublicCandle, instId, bar]);

  const paperOverlayTrades = visiblePaperTrades(paperTradesByStrategyId, effectiveVisibleIds);
  const activePaperTrades =
    effectiveActivePaperStrategyId == null
      ? paperOverlayTrades
      : paperTradesByStrategyId[effectiveActivePaperStrategyId] ?? [];
  const paperEquityOverlays = paperSessionsToOverlays(
    paperSessionsByStrategyId,
    paperEquityByStrategyId,
    effectiveVisibleIds,
  );
  const paperActiveEquity =
    effectiveActivePaperStrategyId == null
      ? undefined
      : paperEquityByStrategyId[effectiveActivePaperStrategyId];

  const overlayEquity =
    mode === "paper"
      ? paperActiveEquity
      : chartView === "run-range" && singleRunMode
        ? equityQ.data
        : undefined;
  const overlays =
    mode === "paper" && paperEquityOverlays.length > 0 ? paperEquityOverlays : compareOverlays;
  // 实时自动交易活跃时，图表 markers 取自动交易成交流（live 模式本无策略 overlay，故为纯增量）。
  const overlayTrades = liveAutoSessionId
    ? liveAutoTrades
    : mode === "paper"
      ? paperOverlayTrades
      : chartView === "run-range" && singleRunMode
        ? tradesQ.data
        : undefined;
  const bottomTrades = mode === "paper" ? activePaperTrades : overlayTrades;
  const tradeCapReached =
    chartView === "run-range" &&
    singleRunMode &&
    (tradesQ.data?.length ?? 0) >= BACKTEST_TRADE_LIMIT;

  useEffect(() => {
    useAppStore.persist.rehydrate();
  }, []);

  useEffect(() => {
    document.documentElement.classList.toggle("dark", theme === "dark");
  }, [theme]);

  // 桌面端自定义标题栏（spec §4.9 / D4 / RV-3）：mac 红绿灯安全区 padding 必须由挂载后状态驱动，
  // 不得在渲染期直接读 platform 决定 className，否则 mac 上 React 水合不一致。
  const [macSafeArea, setMacSafeArea] = useState(false);
  useEffect(() => {
    setMacSafeArea(getDesktopPlatform() === "darwin");
  }, []);

  // 双击拖拽面切换最大化（spec §4.9 / D6）。反转拖拽契约后（诊断
  // desktop-titlebar-buttons-unclickable-when-restored），仅在显式 .drag-region 拖拽面上才触发，
  // 使「可双击最大化」与「可拖动窗口」的区域一致；落在控件、菜单及顶栏空白(HTCLIENT)处一律忽略，
  // 避免在不可拖动处误触最大化。
  const onTitleDoubleClick: MouseEventHandler<HTMLElement> = (e) => {
    if (!(e.target as HTMLElement).closest(".drag-region")) return;
    getWindowControls()?.toggleMaximize();
  };

  const chartError =
    candlesQ.isError && candles.length === 0
      ? (candlesQ.error as Error)?.message || "请求失败"
      : null;
  const market: LiveMarketState = {
    instId,
    lastPrice,
    ticker: marketTicker,
    book: orderBook,
    trades: tradeTape,
    connected: liveWsOpen,
    error: marketError,
    updatedTs: lastMarketTs,
  };

  return (
    <div className="flex h-screen flex-col bg-[var(--bg)]">
      <header
        className={clsx(
          "flex h-12 shrink-0 items-center",
          "border-b border-[var(--border)]",
          macSafeArea ? "pl-[84px] pr-4" : "px-4",
        )}
        onDoubleClick={onTitleDoubleClick}
      >
        {/* 反转拖拽契约（spec §4.10 / 诊断 desktop-titlebar-buttons-unclickable-when-restored）：
            顶栏整体不再 drag，仅「无交互控件」的品牌簇与中部空白标 drag 作为拖拽面，
            交互控件留在默认/no-drag 区（HTCLIENT），规避无边框可缩放窗口还原态下
            「drag 区内 no-drag 洞不被 OS 命中测试生效」的缺陷。 */}
        <div className="flex items-center gap-2">
          <div className="drag-region flex items-center gap-2 self-stretch">
            <img
              src="/brand/logo.png"
              alt="Aragon Trading"
              width="24"
              height="24"
              className="h-6 w-6 shrink-0"
            />
            <span className="text-sm font-semibold tracking-tight">Aragon Trading</span>
            <span className="hidden text-[11px] text-[var(--muted)] sm:inline">
              多策略量化模拟交易 · OKX 真实行情
            </span>
          </div>
          <AppMenu />
        </div>
        <div className="drag-region flex-1 self-stretch" />
        <div className="no-drag flex items-center gap-2">
          <AccountModeSwitch />
          <Button variant="ghost" size="sm" onClick={toggleTheme} aria-label="切换主题">
            {theme === "light" ? "暗色" : "亮色"}
          </Button>
          <FactorButton />
          <SettingsGearButton />
          <WindowControls />
        </div>
      </header>

      {showSettings ? (
        <main className="no-drag min-h-0 flex-1 overflow-hidden">
          <SettingsPanel />
        </main>
      ) : showFactorLab ? (
        <main className="no-drag min-h-0 flex-1 overflow-hidden">
          <FactorLab />
        </main>
      ) : (
        <main
          ref={mainRef}
          className="no-drag grid flex-1 overflow-hidden"
          style={{ gridTemplateColumns: `minmax(0,1fr) 6px ${rightPanelWidth}px` }}
        >
          <ChartWorkspace
            candles={candles}
            overlayEquity={overlayEquity}
            overlays={overlays}
            overlayTrades={overlayTrades}
            bottomTrades={bottomTrades}
            tradeCapReached={tradeCapReached}
            indicators={indicators}
            theme={theme}
            lastPrice={streamMarket ? lastPrice : null}
            market={market}
            dataSource={dataSource}
            chartLoading={(runQ.isFetching || candlesQ.isFetching) && candles.length === 0}
            chartError={chartError}
            onLoadMore={loadMore}
            jumpToTs={jumpToTs}
            paperStrategies={paperStrategies}
            activePaperStrategyId={effectiveActivePaperStrategyId}
            visiblePaperStrategyIds={effectiveVisibleIds}
            onActivePaperStrategyChange={setPaperActiveStrategyId}
            onToggleVisiblePaperStrategy={togglePaperVisibleStrategyId}
            paperPositionsByStrategyId={paperPositionsByStrategyId}
            paperEquityByStrategyId={paperEquityByStrategyId}
            paperTradesByStrategyId={paperTradesByStrategyId}
            toolbar={{
              okxUp,
              latencyMs,
              lastOkTs,
              liveWsOpen: streamMarket ? liveWsOpen : undefined,
              lastPrice: streamMarket ? lastPrice : undefined,
              chartView,
              rangeAvailable,
              onChartViewChange: setChartView,
              onJumpDate: setJumpToTs,
            }}
          />

          <PanelResizeHandle
            onResize={onResizeRight}
            onReset={() => setRightPanelWidth(DEFAULT_RIGHT_PANEL_WIDTH)}
            ariaLabel="拖动调整右侧面板宽度，双击复位"
          />

          <aside className="min-h-[300px] min-w-0 overflow-hidden bg-[var(--surface)]/40">
            <RightPanel />
          </aside>
        </main>
      )}
    </div>
  );
}

interface PaperEventSetters {
  setPaperTradesByStrategyId: Dispatch<SetStateAction<Record<number, StrategyTrade[]>>>;
  setPaperEquityByStrategyId: Dispatch<SetStateAction<Record<number, StrategyEquityPoint[]>>>;
  setPaperStatusByStrategyId: Dispatch<SetStateAction<Record<number, PaperStatus>>>;
  setPaperPositionsByStrategyId: Dispatch<
    SetStateAction<Record<number, PaperPositionEvent | null>>
  >;
  setLive: Dispatch<SetStateAction<Candle[]>>;
  setForming: Dispatch<SetStateAction<Candle | null>>;
}

function handlePaperEvent(
  event: WsEvent,
  session: PaperStrategySession,
  setters: PaperEventSetters,
) {
  if (event.type === "snapshot") {
    const snap = event.data as PaperSnapshot;
    setters.setPaperTradesByStrategyId((prev) => ({
      ...prev,
      [session.strategyId]: (snap.trades ?? []).map((trade) => enrichTrade(trade, session)),
    }));
    setters.setPaperEquityByStrategyId((prev) => ({
      ...prev,
      [session.strategyId]: (snap.equity ?? []).map((point) =>
        enrichEquity(point, session.strategyId),
      ),
    }));
    setters.setPaperPositionsByStrategyId((prev) => ({
      ...prev,
      [session.strategyId]: snap.position ?? null,
    }));
    if (snap.status) {
      setters.setPaperStatusByStrategyId((prev) => ({
        ...prev,
        [session.strategyId]: snap.status as PaperStatus,
      }));
    }
    return;
  }
  if (event.type === "equity") {
    setters.setPaperEquityByStrategyId((prev) =>
      mergePaperEquity(prev, enrichEquity(event.data as EquityPoint, session.strategyId)),
    );
    return;
  }
  if (event.type === "trade") {
    setters.setPaperTradesByStrategyId((prev) =>
      mergePaperTrade(prev, enrichTrade(event.data as Trade, session)),
    );
    return;
  }
  if (event.type === "status") {
    setters.setPaperStatusByStrategyId((prev) => ({
      ...prev,
      [session.strategyId]: event.data as PaperStatus,
    }));
    return;
  }
  if (event.type === "position") {
    setters.setPaperPositionsByStrategyId((prev) => ({
      ...prev,
      [session.strategyId]: event.data as PaperPositionEvent,
    }));
    return;
  }
  if (event.type === "candle") {
    const candle = event.data as Candle;
    if (candle.confirm) {
      setters.setLive((prev) => mergeUnique(prev, [candle]));
      setters.setForming(null);
    } else {
      setters.setForming(candle);
    }
  }
}

interface LiveAutoEventSetters {
  setLiveAutoTrades: (trades: Trade[]) => void;
  appendLiveAutoTrade: (trade: Trade) => void;
  setLiveAutoPosition: (pos: PaperPositionEvent | null) => void;
  setLiveAutoStatus: (status: LiveAutoStatus | null) => void;
  setLive: Dispatch<SetStateAction<Candle[]>>;
  setForming: Dispatch<SetStateAction<Candle | null>>;
}

// 自动交易 WS 事件 → store / 图表（事件协议与 paper 同构；candle 由市场 WS 兜底，这里仅作冗余对齐）。
function handleLiveAutoEvent(event: WsEvent, setters: LiveAutoEventSetters) {
  if (event.type === "snapshot") {
    const snap = event.data as LiveAutoSnapshot;
    setters.setLiveAutoTrades(snap.trades ?? []);
    setters.setLiveAutoPosition(snap.position ?? null);
    if (snap.status) setters.setLiveAutoStatus(snap.status);
    return;
  }
  if (event.type === "trade") {
    setters.appendLiveAutoTrade(event.data as Trade);
    return;
  }
  if (event.type === "position") {
    setters.setLiveAutoPosition(event.data as PaperPositionEvent);
    return;
  }
  if (event.type === "status") {
    setters.setLiveAutoStatus(event.data as LiveAutoStatus);
    return;
  }
  if (event.type === "candle") {
    const candle = event.data as Candle;
    if (candle.confirm) {
      setters.setLive((prev) => mergeUnique(prev, [candle]));
      setters.setForming(null);
    } else {
      setters.setForming(candle);
    }
  }
}

function enrichTrade(trade: Trade, session: PaperStrategySession): StrategyTrade {
  return {
    ...trade,
    strategyId: session.strategyId,
    strategyName: session.strategyName,
    runId: trade.runId ?? session.runId,
    color: session.color,
  };
}

function enrichEquity(point: EquityPoint, strategyId: number): StrategyEquityPoint {
  return { ...point, strategyId };
}

function mergeCandles(
  older: Candle[],
  base: Candle[],
  live: Candle[],
  forming?: Candle | null,
): Candle[] {
  const merged = mergeUnique(mergeUnique(older, base), live);
  return forming ? mergeUnique(merged, [forming]) : merged;
}

function mergeUnique(a: Candle[], b: Candle[]): Candle[] {
  const map = new Map<number, Candle>();
  for (const candle of a) map.set(candle.ts, candle);
  for (const candle of b) map.set(candle.ts, candle);
  return Array.from(map.values()).sort((x, y) => x.ts - y.ts);
}
