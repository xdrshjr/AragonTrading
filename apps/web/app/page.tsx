"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import clsx from "clsx";
import type { Dispatch, MouseEventHandler, SetStateAction } from "react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import AiBacktestModal from "@/components/Chart/AiBacktestModal";
import ChartWorkspace from "@/components/Chart/ChartWorkspace";
import WindowControls from "@/components/Desktop/WindowControls";
import AccountModeSwitch from "@/components/Header/AccountModeSwitch";
import DiscoveryLab from "@/components/Discovery/DiscoveryLab";
import FactorLab from "@/components/Factor/FactorLab";
import NavRail from "@/components/Nav/NavRail";
import RightPanel from "@/components/RightPanel/RightPanel";
import SettingsGearButton from "@/components/Settings/SettingsGearButton";
import SettingsPanel from "@/components/Settings/SettingsPanel";
import { Button } from "@/components/ui";
import PanelResizeHandle from "@/components/ui/PanelResizeHandle";
import { api } from "@/lib/api";
import {
  getDesktopPlatform,
  getWindowControls,
  subscribeWindowControlsOverlay,
} from "@/lib/desktop";
import {
  LATEST_CANDLE_LIMIT,
  liveCandleRefreshIntervalMs,
  resolveChartRange,
  shouldApplyPublicCandle,
  shouldStreamMarket,
} from "@/lib/chartLive";
import { isUsableCandle, routeMarketFrame } from "@/lib/marketFrames";
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
import { useLiveAutoGroupStream } from "@/lib/useLiveAutoGroupStream";
import { useAiTradeGroupStream } from "@/lib/useAiTradeGroupStream";
import { useWorkspaceSync } from "@/lib/useWorkspaceSync";
import type { LiveAutoEvent, LiveAutoSnapshot, LiveAutoStatus } from "@/lib/liveAutoTypes";
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
    liveAutoGroup,
    aiTradeGroup,
    aiBacktestJobId,
    setAiBacktestJobId,
    theme,
    backtestStartTs,
    backtestEndTs,
    indicators,
    showSettings,
    activeView,
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
    pushLiveAutoEvent,
    toggleTheme,
    setRightPanelWidth,
  } = useAppStore();

  // 自选自动交易分组运行期流（重发现孤儿分组 + 每成员一条 WS，按 instId 路由进 store）。
  useLiveAutoGroupStream();

  // AI 自动交易「分组」运行期流（重发现孤儿分组 + 每成员一条 WS，按 instId 路由进 store）。
  useAiTradeGroupStream();

  // workspace 偏好后端同步（watchlist-auto-trade-persistence §6）：启动以后端为权威水合自选 +
  // 自动交易成员，之后去抖写穿，使关闭 / 强杀重启后偏好不丢（与下方 rehydrate() effect 协同）。
  useWorkspaceSync();

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
  // 因子实验室「首激活后常驻」惰性挂载：从未打开者不触发因子查询；一旦打开即保持挂载（hidden）以保态。
  const [hasOpenedFactor, setHasOpenedFactor] = useState(false);
  // 策略与因子挖掘同款惰性挂载：从未打开者不触发任何挖掘查询（DoD #13：未启动时 0 额外请求）。
  const [hasOpenedDiscovery, setHasOpenedDiscovery] = useState(false);
  const loadingMore = useRef(false);
  const lastMarketTsRef = useRef<number | null>(null);
  // 右侧面板调宽：以 <main> 右边缘为锚，宽度 = 右边缘 − 指针 X（store 负责夹取 [320,640]）。
  const mainRef = useRef<HTMLElement>(null);
  const onResizeRight = (clientX: number) => {
    const rect = mainRef.current?.getBoundingClientRect();
    if (!rect) return;
    setRightPanelWidth(widthFromRightEdge(rect.right, clientX));
  };

  const queryClient = useQueryClient();
  const health = useQuery({ queryKey: ["health"], queryFn: api.health, refetchInterval: 15_000 });
  const okxUp = health.data ? health.data.okx === "up" : null;
  const latencyMs = health.data?.okxLatencyMs ?? null;
  const lastOkTs = health.data?.lastOkTs ?? null;
  // OKX 链路详情（okx-link-resilience §3.4）。`refetchInterval` **取函数式**：面板展开时 5s、
  // 收起时 30s——顶栏已经有一条 15s 的 /api/health 轮询，再无条件叠一条定频轮询是白烧的往返。
  // 端点缺失（老后端）时 `data` 为 undefined，ConnectionStatus 自会回落到 okxUp 二元展示。
  const [linkPanelOpen, setLinkPanelOpen] = useState(false);
  const connectivity = useQuery({
    queryKey: ["okxLink"],
    queryFn: api.okxConnectivity,
    refetchInterval: () => (linkPanelOpen ? 5_000 : 30_000),
    retry: false,
  });
  const probeConnectivity = useCallback(async () => {
    const snapshot = await api.okxProbe();
    queryClient.setQueryData(["okxLink"], snapshot);
    return snapshot;
  }, [queryClient]);

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
        pushLiveAutoEvent,
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
    pushLiveAutoEvent,
  ]);

  // 节流约束：ref 推进必须随入帧同步发生（ref 无渲染成本，candle 分支门控要读最新值），
  // 只有 setLastMarketTs 这个 state 写入才纳入节流（由 throttle 的 flush 统一落库）。
  const advanceMarketTs = (ts: number) => {
    // 非有限值一律不推进：Math.max 一旦吃进 NaN 就永久锁死这个时钟（`??` 拦不住 NaN），
    // 之后 K 线分支的 `lastMarketTs <= candle.ts` 恒假，顶部最新价会静默冻结到下次重挂。
    if (!Number.isFinite(ts)) return;
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
      // 路由判据集中在 lib/marketFrames：白名单 + K 线形状校验。这条流上不止跑 K 线
      // （还有链路状态帧），把判据写成兜底式 else 曾令整个应用白屏，见该模块注释。
      (event) =>
        routeMarketFrame(event, {
          setError: setMarketError,
          advanceMarketTs,
          pushTrade: throttle.pushTrade,
          pushTicker: throttle.pushTicker,
          pushBook: throttle.pushBook,
          pushPrice: throttle.pushPrice,
          lastMarketTs: () => lastMarketTsRef.current,
          applyPublicCandle,
          onConfirmedCandle: (candle) => setLive((prev) => mergeUnique(prev, [candle])),
          onFormingCandle: setForming,
        }),
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
  // 分组活跃时取**当前选中 instId** 对应成员的成交（切换标的即换 markers，§2.7 / R7）。
  // AI 自动交易分组：当前图表标的命中某成员时取该成员成交流（切走即不显示，避免误绘）。
  const aiTradeMember = aiTradeGroup ? (aiTradeGroup.members[instId] ?? null) : null;
  const overlayTrades = aiTradeMember
    ? aiTradeMember.trades
    : liveAutoGroup
      ? (liveAutoGroup.members[instId]?.trades ?? [])
      : liveAutoSessionId
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

  // 首激活后常驻：activeView 首次为 "factor" 时置位，置位后即便切回交易也保持 FactorLab 挂载（hidden）。
  useEffect(() => {
    if (activeView === "factor") setHasOpenedFactor(true);
    if (activeView === "discovery") setHasOpenedDiscovery(true);
  }, [activeView]);

  useEffect(() => {
    document.documentElement.classList.toggle("dark", theme === "dark");
    // Windows 系统标题栏控件叠加(titleBarOverlay)配色随主题同步（非 win 平台主进程内 no-op）。
    getWindowControls()?.setOverlayTheme?.(theme);
  }, [theme]);

  // 桌面端自定义标题栏（spec §4.9 / D4 / RV-3）：平台相关的安全区 padding 必须由挂载后状态驱动，
  // 不得在渲染期直接读 platform 决定 className，否则 React 水合不一致。
  // mac → 左侧红绿灯安全区；win → 右上角系统 titleBarOverlay 三键安全区（让位右簇，避免被覆盖）。
  const [macSafeArea, setMacSafeArea] = useState(false);
  const [winOverlay, setWinOverlay] = useState(false);
  useEffect(() => {
    const platform = getDesktopPlatform();
    setMacSafeArea(platform === "darwin");
    setWinOverlay(platform === "win32");
  }, []);

  // G1（诊断 desktop-packaged-wco-header-right-still-unclickable）：把渲染层观测到的 Window Controls
  // Overlay 运行期状态（是否真激活 / 安全区矩形）回传主进程，「关于」据此展示 WCO 是否生效，使真机人工门
  // 可判别。subscribeWindowControlsOverlay 内部 feature-detect，非 WCO 平台仅回调一次 supported:false。
  useEffect(() => {
    const report = getWindowControls()?.reportOverlay;
    if (!report) return;
    return subscribeWindowControlsOverlay((state) => report(state));
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

  // 顶栏右簇应用控件（模拟盘/实盘切换、主题键、设置齿轮、窗口键）。所有平台均单行内联在标题栏行内、
  // 置于系统三键左侧（第 2 轮回退 round-1 的两段式布局，诊断 desktop-header-app-controls-inline-single-row）。
  const appControls = (
    <>
      <AccountModeSwitch />
      <Button variant="ghost" size="sm" onClick={toggleTheme} aria-label="切换主题">
        {theme === "light" ? "暗色" : "亮色"}
      </Button>
      <SettingsGearButton />
      <WindowControls />
    </>
  );

  return (
    <div className="flex h-screen flex-col bg-[var(--bg)]">
      <header
        className={clsx(
          "flex h-12 shrink-0 items-center",
          macSafeArea ? "pl-[84px] pr-4" : winOverlay ? "win-titlebar-safe pl-4" : "px-4",
        )}
        onDoubleClick={onTitleDoubleClick}
      >
        {/* 反转拖拽契约（spec §4.10 / 诊断 desktop-titlebar-buttons-unclickable-when-restored、
            desktop-header-right-and-auto-trade-toggle-unresponsive）：顶栏整体不再 drag，
            仅最左「无交互控件」品牌簇标 drag 作为唯一拖拽手柄；中部空白与右簇均不 drag。
            残留的中部 flex-1 拖拽面紧邻右簇，还原态下其 OS 命中区外溢到窗口右缘、把整片右簇
            （模拟盘/实盘、主题、Factor、设置、窗口键）吞为 caption 致单击不下发，故去其 drag。 */}
        <div className="drag-region flex items-center gap-2 self-stretch">
          <img
            src="/brand/logo.png"
            alt="Aragon Trading"
            width="24"
            height="24"
            className="h-6 w-6 shrink-0"
          />
          <span className="text-sm font-semibold tracking-tight">Aragon Trading</span>
        </div>
        <div className="flex-1 self-stretch" />
        {/* 单行内联右簇（第 2 轮回退：应用控件回到与系统三键同一行、置于其左侧；诊断
            desktop-header-app-controls-inline-single-row）。右簇为显式 no-drag 岛：反转拖拽契约下除最左
            品牌簇（唯一 .drag-region）外无任何 drag 区覆盖它；header 的 win-titlebar-safe 让出系统三键带宽，
            使右簇整体落在三键左侧安全区。标准 WCO 语义下标题栏内 no-drag 区判为 HTCLIENT、可点（VS Code 等
            生产 WCO 应用即如此）。Windows 还原态单击是否下发不可 headless 验证——以真实 Windows 还原/最大化
            两态人工点击为终审门（诊断 §六 强制人工验收门）。 */}
        <div className="no-drag flex items-center gap-2">{appControls}</div>
      </header>

      {/* 全宽分隔线（替代 header 自身 border-b）：作为 header 之后的同层流式元素落在 y≈48，
          严格位于 Windows titleBarOverlay 48px 叠加矩形（[0,48)）之外，故三键正下方亦连续不缺线
          （spec §2.3）；Web/mac/linux 无叠加矩形，视觉等价于原 border-b。 */}
      <div aria-hidden="true" className="h-px w-full shrink-0 bg-[var(--border)]" />

      {/* header 之下：左侧常驻 NavRail + 右侧内容区。交易/因子两视图始终挂载、用 hidden 互斥切换以保态
          （镜像 RightPanel 惯用法）；承载 hidden 的 wrapper 绝不带 display 工具类（评审 C-1/P0），grid 与
          mainRef 下沉内层 <main>；设置中心为「覆盖」——打开时仅给两 wrapper 追加 hidden，严禁卸载（C-2）。 */}
      <div className="flex min-h-0 flex-1">
        <NavRail />
        <div className="flex min-h-0 flex-1 flex-col">
          <div className="min-h-0 flex-1" hidden={showSettings || activeView !== "trade"}>
            <main
              ref={mainRef}
              className="no-drag grid h-full overflow-hidden"
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
                  connectivity: connectivity.data ?? null,
                  onConnectivityOpenChange: setLinkPanelOpen,
                  onConnectivityProbe: probeConnectivity,
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
          </div>

          {hasOpenedFactor && (
            <div className="min-h-0 flex-1" hidden={showSettings || activeView !== "factor"}>
              <FactorLab active={activeView === "factor" && !showSettings} />
            </div>
          )}

          {hasOpenedDiscovery && (
            <div className="min-h-0 flex-1" hidden={showSettings || activeView !== "discovery"}>
              <DiscoveryLab active={activeView === "discovery" && !showSettings} />
            </div>
          )}

          {showSettings && (
            <main className="no-drag min-h-0 flex-1 overflow-hidden">
              <SettingsPanel />
            </main>
          )}
        </div>
      </div>
      {aiBacktestJobId && (
        <AiBacktestModal jobId={aiBacktestJobId} onClose={() => setAiBacktestJobId(null)} />
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
    // 与市场流共用同一道形状闸：ts/c 不是有限数就不许进图表数据（见 lib/marketFrames）。
    if (!isUsableCandle(event.data)) return;
    const candle = event.data;
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
  pushLiveAutoEvent: (event: LiveAutoEvent) => void;
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
  // H6：下单 / 平仓失败等 error 事件不再被丢弃 —— 推入事件流（错误级，组件置顶高亮）。
  if (event.type === "error") {
    const data = event.data as { ts?: number; code?: string; message?: string };
    setters.pushLiveAutoEvent({
      ts: data.ts ?? Math.floor(Date.now() / 1000),
      level: "error",
      code: data.code,
      message: data.message ?? "自动交易发生错误",
    });
    return;
  }
  if (event.type === "status") {
    const status = event.data as LiveAutoStatus;
    setters.setLiveAutoStatus(status);
    // H6：status 携带的中文诊断 message（含降级）入事件流，使「bot 此刻在做什么」对用户透明。
    if (status.message) {
      setters.pushLiveAutoEvent({
        ts: Math.floor(Date.now() / 1000),
        level: status.degraded ? "warn" : "info",
        message: status.message,
      });
    }
    return;
  }
  if (event.type === "candle") {
    // 与市场流共用同一道形状闸：ts/c 不是有限数就不许进图表数据（见 lib/marketFrames）。
    if (!isUsableCandle(event.data)) return;
    const candle = event.data;
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
