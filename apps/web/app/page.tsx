"use client";

import { useQuery } from "@tanstack/react-query";
import type { Dispatch, SetStateAction } from "react";
import { useEffect, useMemo, useRef, useState } from "react";
import ChartWorkspace from "@/components/Chart/ChartWorkspace";
import AccountModeSwitch from "@/components/Header/AccountModeSwitch";
import StrategyPanel from "@/components/Strategy/StrategyPanel";
import { Button } from "@/components/ui";
import { api } from "@/lib/api";
import {
  LATEST_CANDLE_LIMIT,
  liveCandleRefreshIntervalMs,
  resolveChartRange,
  shouldApplyPublicCandle,
  shouldStreamMarket,
} from "@/lib/chartLive";
import { colorForIndex } from "@/lib/compare";
import { mergeTradeTape } from "@/lib/marketDepth";
import {
  mergePaperEquity,
  mergePaperTrade,
  paperSessionsToOverlays,
  selectFirstAvailablePaperStrategy,
  visiblePaperTrades,
} from "@/lib/paperMulti";
import { useAppStore } from "@/lib/store";
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
    paperSessionId,
    paperSessionsByStrategyId,
    paperVisibleStrategyIds,
    paperActiveStrategyId,
    paperStreamsEnabled,
    theme,
    backtestStartTs,
    backtestEndTs,
    indicators,
    setChartView,
    setPaperActiveStrategyId,
    setPaperVisibleStrategyIds,
    togglePaperVisibleStrategyId,
    toggleTheme,
  } = useAppStore();

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

  const health = useQuery({ queryKey: ["health"], queryFn: api.health, refetchInterval: 15_000 });
  const okxUp = health.data ? health.data.okx === "up" : null;
  const latencyMs = health.data?.okxLatencyMs ?? null;
  const lastOkTs = health.data?.lastOkTs ?? null;

  const runQ = useQuery({
    queryKey: ["run", runId],
    queryFn: () => api.run(runId as number),
    enabled: !!runId && mode === "backtest",
  });
  const runRange = useMemo(() => {
    const run = runQ.data;
    if (mode !== "backtest" || !run || run.endTs == null) return null;
    return { instId: run.instId, bar: run.bar, startTs: run.startTs, endTs: run.endTs };
  }, [mode, runQ.data]);
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
    enabled: !!runId && mode === "backtest",
  });
  const tradesQ = useQuery({
    queryKey: ["trades", runId, BACKTEST_TRADE_LIMIT],
    queryFn: () => api.trades(runId as number, BACKTEST_TRADE_LIMIT),
    enabled: !!runId && mode === "backtest" && chartView === "run-range",
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

  const recordMarketTs = (ts: number) => {
    lastMarketTsRef.current = Math.max(lastMarketTsRef.current ?? 0, ts);
    setLastMarketTs((prev) => Math.max(prev ?? 0, ts));
  };

  useEffect(() => {
    if (!streamMarket) return;
    const ws = marketWS(
      instId,
      bar,
      (event) => {
        if (event.type === "error") {
          setMarketError(event.data.message);
          return;
        }
        setMarketError(null);
        if (event.type === "trade") {
          setTradeTape((prev) => mergeTradeTape(prev, event.data));
          setLastPrice(event.data.price);
          recordMarketTs(event.data.ts);
          return;
        }
        if (event.type === "ticker") {
          setMarketTicker(event.data);
          setLastPrice(event.data.last);
          recordMarketTs(event.data.ts);
          return;
        }
        if (event.type === "book") {
          setOrderBook(event.data);
          recordMarketTs(event.data.ts);
          return;
        }
        const candle = event.data;
        if ((lastMarketTsRef.current ?? 0) <= candle.ts) setLastPrice(candle.c);
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
      : chartView === "run-range" && mode === "backtest"
        ? equityQ.data
        : undefined;
  const overlays =
    mode === "paper" && paperEquityOverlays.length > 0 ? paperEquityOverlays : compareOverlays;
  const overlayTrades =
    mode === "paper"
      ? paperOverlayTrades
      : chartView === "run-range" && mode === "backtest"
        ? tradesQ.data
        : undefined;
  const bottomTrades = mode === "paper" ? activePaperTrades : overlayTrades;
  const tradeCapReached =
    chartView === "run-range" &&
    mode === "backtest" &&
    (tradesQ.data?.length ?? 0) >= BACKTEST_TRADE_LIMIT;

  useEffect(() => {
    useAppStore.persist.rehydrate();
  }, []);

  useEffect(() => {
    document.documentElement.classList.toggle("dark", theme === "dark");
  }, [theme]);

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
      <header className="flex h-12 shrink-0 items-center justify-between border-b border-[var(--border)] px-4">
        <div className="flex items-center gap-2">
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
        <div className="flex items-center gap-2">
          <AccountModeSwitch />
          <Button variant="ghost" size="sm" onClick={toggleTheme} aria-label="切换主题">
            {theme === "light" ? "暗色" : "亮色"}
          </Button>
        </div>
      </header>

      <main className="grid flex-1 grid-cols-1 overflow-hidden lg:grid-cols-[1fr_380px]">
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

        <aside className="min-h-[300px] overflow-hidden bg-[var(--surface)]/40">
          <StrategyPanel />
        </aside>
      </main>
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
