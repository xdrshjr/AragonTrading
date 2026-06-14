"use client";

import { useQuery } from "@tanstack/react-query";
import { useEffect, useMemo, useRef, useState } from "react";
import ChartWorkspace from "@/components/Chart/ChartWorkspace";
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
import { useAppStore } from "@/lib/store";
import type {
  Candle,
  CompareResult,
  EquityPoint,
  LiveMarketState,
  MarketTicker,
  MarketTrade,
  OrderBookSnapshot,
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
    theme,
    backtestStartTs,
    backtestEndTs,
    indicators,
    setChartView,
    toggleTheme,
  } = useAppStore();

  const [older, setOlder] = useState<Candle[]>([]);
  const [live, setLive] = useState<Candle[]>([]);
  const [forming, setForming] = useState<Candle | null>(null); // F1：成形中（未确认）最右一根
  const [lastPrice, setLastPrice] = useState<number | null>(null); // F1：实时最新价
  const [liveWsOpen, setLiveWsOpen] = useState(false); // F1：市场实时流连接态
  const [marketTicker, setMarketTicker] = useState<MarketTicker | null>(null);
  const [orderBook, setOrderBook] = useState<OrderBookSnapshot | null>(null);
  const [tradeTape, setTradeTape] = useState<MarketTrade[]>([]);
  const [lastMarketTs, setLastMarketTs] = useState<number | null>(null);
  const [marketError, setMarketError] = useState<string | null>(null);
  const [liveEquity, setLiveEquity] = useState<EquityPoint[]>([]);
  const [liveTrades, setLiveTrades] = useState<Trade[]>([]);
  const [jumpToTs, setJumpToTs] = useState<number | null>(null);
  const loadingMore = useRef(false);
  const lastMarketTsRef = useRef<number | null>(null);

  // 健康检查（连接状态点反映真实 OKX 可达性，C1；含时延 / 最近取数时间，F2）
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

  // 对比结果（由右侧 CompareView 写入共享缓存，F3）→ 映射为左图归一化净值叠加
  const compareQ = useQuery<CompareResult | null>({
    queryKey: ["compare", instId, bar, backtestStartTs, backtestEndTs],
    queryFn: () => null, // 占位：实际数据由 CompareView 通过 setQueryData 写入，本查询不主动请求
    enabled: false,
    initialData: null,
  });
  const overlays =
    chartView === "compare-range" && mode === "compare" && compareQ.data
      ? compareQ.data.items.map((it, i) => ({
          id: it.strategyId,
          name: it.name,
          color: colorForIndex(i),
          equity: it.equity,
        }))
      : undefined;

  // 真实历史 K 线（带数据来源 meta，F2）：读 X-Aragon-Data-Source 头判断 live / cache
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
  // 主视图新鲜度：以一次性历史拉取的来源为准（query 重取时回落 live，天然覆盖切标的/周期重置，C5/C9）
  const dataSource = candlesQ.data?.source ?? "live";

  // 切换标的/周期 → 清空增量与实时缓冲（F1：连带重置成形根 / 实时价，避免显示上一标的旧值，C5）
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
    if (chartRange) return;
    if (loadingMore.current) return;
    loadingMore.current = true;
    try {
      const more = await api.candles(instId, bar, 300, beforeTs);
      if (more.length) setOlder((prev) => mergeUnique(more, prev));
    } catch {
      /* 忽略增量失败 */
    } finally {
      loadingMore.current = false;
    }
  };

  // 回测叠加数据
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

  // 实时模拟 WS：累积净值 / 成交 / 实时蜡烛
  useEffect(() => {
    if (mode !== "paper" || !paperSessionId) return;
    setLiveEquity([]);
    setLiveTrades([]);
    const ws = paperWS(paperSessionId, (e: WsEvent) => {
      if (e.type === "equity") {
        setLiveEquity((prev) => [...prev, e.data as EquityPoint]);
      } else if (e.type === "trade") {
        setLiveTrades((prev) => [...prev, e.data as Trade]);
      } else if (e.type === "candle") {
        const c = e.data as Candle;
        if (c.confirm) setLive((prev) => mergeUnique(prev, [c]));
      }
    });
    ws.connect();
    return () => ws.close();
  }, [mode, paperSessionId]);

  const recordMarketTs = (ts: number) => {
    lastMarketTsRef.current = Math.max(lastMarketTsRef.current ?? 0, ts);
    setLastMarketTs((prev) => Math.max(prev ?? 0, ts));
  };

  useEffect(() => {
    if (!streamMarket) return;
    const ws = marketWS(
      instId,
      bar,
      (e) => {
        if (e.type === "error") {
          setMarketError(e.data.message);
          return;
        }
        setMarketError(null);
        if (e.type === "trade") {
          setTradeTape((prev) => mergeTradeTape(prev, e.data));
          setLastPrice(e.data.price);
          recordMarketTs(e.data.ts);
          return;
        }
        if (e.type === "ticker") {
          setMarketTicker(e.data);
          setLastPrice(e.data.last);
          recordMarketTs(e.data.ts);
          return;
        }
        if (e.type === "book") {
          setOrderBook(e.data);
          recordMarketTs(e.data.ts);
          return;
        }
        const c = e.data;
        if ((lastMarketTsRef.current ?? 0) <= c.ts) setLastPrice(c.c);
        if (!applyPublicCandle) return;
        if (c.confirm === true) {
          setLive((prev) => mergeUnique(prev, [c]));
          setForming(null);
        } else {
          setForming(c);
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

  // compare 模式不画单策略净值 / markers（改用多条归一化 overlays，C1）
  const overlayEquity =
    mode === "paper"
      ? liveEquity
      : chartView === "run-range" && mode === "backtest"
        ? equityQ.data
        : undefined;
  const overlayTrades =
    mode === "paper"
      ? liveTrades
      : chartView === "run-range" && mode === "backtest"
        ? tradesQ.data
        : undefined;
  const tradeCapReached =
    chartView === "run-range" &&
    mode === "backtest" &&
    (tradesQ.data?.length ?? 0) >= BACKTEST_TRADE_LIMIT;

  // F3：挂载后从 localStorage 补水持久化选择态/主题（skipHydration 配合，避免 SSR 水合告警，C2）
  useEffect(() => {
    useAppStore.persist.rehydrate();
  }, []);

  // 初始化主题
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
            多策略量化模拟交易 · 欧易(OKX)真实行情
          </span>
        </div>
        <Button variant="ghost" size="sm" onClick={toggleTheme} aria-label="切换主题">
          {theme === "light" ? "🌙 暗色" : "☀️ 亮色"}
        </Button>
      </header>

      <main className="grid flex-1 grid-cols-1 overflow-hidden lg:grid-cols-[1fr_380px]">
        <ChartWorkspace
          candles={candles}
          overlayEquity={overlayEquity}
          overlays={overlays}
          overlayTrades={overlayTrades}
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

function mergeCandles(
  older: Candle[],
  base: Candle[],
  live: Candle[],
  forming?: Candle | null,
): Candle[] {
  const merged = mergeUnique(mergeUnique(older, base), live);
  // F1：成形根并入最右端——同 ts 覆盖历史/确认根，得到"最后一根实时跳动"，全程 ts 严格升序（R2）
  return forming ? mergeUnique(merged, [forming]) : merged;
}

function mergeUnique(a: Candle[], b: Candle[]): Candle[] {
  const map = new Map<number, Candle>();
  for (const c of a) map.set(c.ts, c);
  for (const c of b) map.set(c.ts, c);
  return Array.from(map.values()).sort((x, y) => x.ts - y.ts);
}
