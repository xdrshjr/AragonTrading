"use client";

import { useQuery } from "@tanstack/react-query";
import dynamic from "next/dynamic";
import { useEffect, useMemo, useRef, useState } from "react";
import ChartToolbar from "@/components/Chart/ChartToolbar";
import StrategyPanel from "@/components/Strategy/StrategyPanel";
import { Button } from "@/components/ui";
import { api } from "@/lib/api";
import { useAppStore } from "@/lib/store";
import type { Candle, EquityPoint, Trade } from "@/lib/types";
import { paperWS, type WsEvent } from "@/lib/ws";

// lightweight-charts 仅客户端渲染（R6）
const CandleChart = dynamic(() => import("@/components/Chart/CandleChart"), { ssr: false });

export default function Page() {
  const { instId, bar, mode, runId, paperSessionId, theme, toggleTheme } = useAppStore();

  const [older, setOlder] = useState<Candle[]>([]);
  const [live, setLive] = useState<Candle[]>([]);
  const [liveEquity, setLiveEquity] = useState<EquityPoint[]>([]);
  const [liveTrades, setLiveTrades] = useState<Trade[]>([]);
  const [jumpToTs, setJumpToTs] = useState<number | null>(null);
  const loadingMore = useRef(false);

  // 健康检查（连接状态点反映真实 OKX 可达性，C1）
  const health = useQuery({ queryKey: ["health"], queryFn: api.health, refetchInterval: 15_000 });
  const okxUp = health.data ? health.data.okx === "up" : null;

  // 真实历史 K 线
  const candlesQ = useQuery({
    queryKey: ["candles", instId, bar],
    queryFn: () => api.candles(instId, bar, 300),
    retry: 0,
  });

  // 切换标的/周期 → 清空增量与实时缓冲
  useEffect(() => {
    setOlder([]);
    setLive([]);
  }, [instId, bar]);

  const candles = useMemo(
    () => mergeCandles(older, candlesQ.data ?? [], live),
    [older, candlesQ.data, live],
  );

  const loadMore = async (beforeTs: number) => {
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
    queryKey: ["trades", runId],
    queryFn: () => api.trades(runId as number),
    enabled: !!runId && mode === "backtest",
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

  const overlayEquity = mode === "paper" ? liveEquity : equityQ.data;
  const overlayTrades = mode === "paper" ? liveTrades : tradesQ.data;

  // 初始化主题
  useEffect(() => {
    document.documentElement.classList.toggle("dark", theme === "dark");
  }, [theme]);

  const chartError =
    candlesQ.isError && candles.length === 0
      ? (candlesQ.error as Error)?.message || "请求失败"
      : null;

  return (
    <div className="flex h-screen flex-col bg-[var(--bg)]">
      <header className="flex h-12 shrink-0 items-center justify-between border-b border-[var(--border)] px-4">
        <div className="flex items-center gap-2">
          <div className="flex h-6 w-6 items-center justify-center rounded-[7px] bg-[var(--accent)] text-xs font-bold text-white">
            A
          </div>
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
        <section className="flex min-h-[360px] flex-col border-b border-[var(--border)] lg:border-b-0 lg:border-r">
          <div className="shrink-0 border-b border-[var(--border)] px-4 py-2.5">
            <ChartToolbar okxUp={okxUp} onJumpDate={setJumpToTs} />
          </div>
          <div className="relative flex-1">
            <CandleChart
              candles={candles}
              equity={overlayEquity}
              trades={overlayTrades}
              theme={theme}
              loading={candlesQ.isFetching && candles.length === 0}
              error={chartError}
              onLoadMore={loadMore}
              jumpToTs={jumpToTs}
            />
          </div>
        </section>

        <aside className="min-h-[300px] overflow-hidden bg-[var(--surface)]/40">
          <StrategyPanel />
        </aside>
      </main>
    </div>
  );
}

function mergeCandles(older: Candle[], base: Candle[], live: Candle[]): Candle[] {
  return mergeUnique(mergeUnique(older, base), live);
}

function mergeUnique(a: Candle[], b: Candle[]): Candle[] {
  const map = new Map<number, Candle>();
  for (const c of a) map.set(c.ts, c);
  for (const c of b) map.set(c.ts, c);
  return Array.from(map.values()).sort((x, y) => x.ts - y.ts);
}
