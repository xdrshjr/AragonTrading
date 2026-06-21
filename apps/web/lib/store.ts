// 全局 UI / 选择态 (Zustand) (spec §8.2)。
// F3：用 persist 中间件把选择态/主题落 localStorage（key "aragon-ui"），刷新后恢复上次工作上下文。
import { create } from "zustand";
import { createJSONStorage, persist } from "zustand/middleware";
import { DEFAULT_INDICATORS, type IndicatorKey } from "./indicators";
import { FALLBACK_INSTRUMENTS } from "./instruments";
import type {
  AccountMode,
  Bar,
  BottomTab,
  ChartPane,
  ChartView,
  Mode,
  PaperStrategySession,
} from "./types";

type Theme = "light" | "dark";
// 标的侧边栏页签：自选 / 现货 / 合约（"swap" 承载 SWAP+FUTURES 合约页签，RV-10）。
export type InstSidebarTab = "watch" | "spot" | "swap";
// 右侧面板页签：AI 助手 / 交易策略（Agent Trading Copilot §5.3，默认 "agent"）。
export type RightTab = "agent" | "strategy";

// 生成新会话 id（SSR 安全：crypto 不可用时回落空串，由组件挂载后补）。
function makeConversationId(): string {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return crypto.randomUUID();
  }
  return "";
}
const DEFAULT_RANGE_DAYS = 30;
const defaultEndTs = Math.floor(Date.now() / 1000);
const defaultStartTs = defaultEndTs - DEFAULT_RANGE_DAYS * 24 * 60 * 60;

// 模拟资金（支柱 A）：默认与后端 settings.initial_capital=10000 对齐；夹取区间防误输入。
export const DEFAULT_SIM_BALANCE = 10000;
const MIN_SIM_BALANCE = 100;
const MAX_SIM_BALANCE = 1e8;

// 左侧纵向分栏比例：图表区(上)占左侧高度的比例，1-ratio 即「成交记录/当前持仓」(下)区。
// 夹取上区于 [0.4, 0.85] ⇒ 下区于 [15%, 60%]，尊重图表 min-h-[320px] 与下区 min-h-[140px] 硬下限。
export const DEFAULT_CHART_SPLIT_RATIO = 0.8;
const MIN_CHART_SPLIT_RATIO = 0.4;
const MAX_CHART_SPLIT_RATIO = 0.85;

interface AppState {
  instId: string;
  bar: Bar;
  strategyId: number | null;
  runId: number | null;
  chartView: ChartView;
  chartPane: ChartPane;
  bottomTab: BottomTab;
  paneTab: IndicatorKey | null;
  mode: Mode;
  accountMode: AccountMode;
  simBalance: number;
  paperSessionId: string | null;
  paperSelectionIds: number[];
  paperVisibleStrategyIds: number[];
  paperActiveStrategyId: number | null;
  paperBatchGroupId: string | null;
  paperSessionsByStrategyId: Record<number, PaperStrategySession>;
  paperStreamsEnabled: boolean;
  theme: Theme;
  backtestStartTs: number;
  backtestEndTs: number;
  indicators: Record<IndicatorKey, boolean>;
  watchlist: string[];
  instSidebarTab: InstSidebarTab;
  chartSplitRatio: number;
  rightTab: RightTab;
  agentConversationId: string;

  setInst: (instId: string) => void;
  setBar: (bar: Bar) => void;
  setStrategyId: (id: number | null) => void;
  setRunId: (id: number | null) => void;
  setChartView: (view: ChartView) => void;
  setChartPane: (pane: ChartPane) => void;
  setBottomTab: (tab: BottomTab) => void;
  setPaneTab: (key: IndicatorKey | null) => void;
  setMode: (mode: Mode) => void;
  setAccountMode: (m: AccountMode) => void;
  setSimBalance: (n: number) => void;
  setPaperSessionId: (id: string | null) => void;
  setPaperSelectionIds: (ids: number[]) => void;
  togglePaperSelectionId: (id: number) => void;
  setPaperVisibleStrategyIds: (ids: number[]) => void;
  togglePaperVisibleStrategyId: (id: number) => void;
  setPaperActiveStrategyId: (id: number | null) => void;
  setPaperBatchGroupId: (id: string | null) => void;
  setPaperSessionsByStrategyId: (sessions: Record<number, PaperStrategySession>) => void;
  setPaperStreamsEnabled: (enabled: boolean) => void;
  clearPaperRuntime: () => void;
  setBacktestRange: (startTs: number, endTs: number) => void;
  toggleTheme: () => void;
  setTheme: (t: Theme) => void;
  toggleIndicator: (key: IndicatorKey) => void;
  setIndicators: (rec: Record<IndicatorKey, boolean>) => void;
  resetIndicators: () => void;
  addToWatchlist: (instId: string) => void;
  removeFromWatchlist: (instId: string) => void;
  toggleWatchlist: (instId: string) => void;
  setInstSidebarTab: (tab: InstSidebarTab) => void;
  setChartSplitRatio: (ratio: number) => void;
  setRightTab: (tab: RightTab) => void;
  setAgentConversationId: (id: string) => void;
  newConversation: () => void;
}

export const useAppStore = create<AppState>()(
  persist(
    (set) => ({
      instId: "BTC-USDT-SWAP",
      bar: "1H",
      strategyId: null,
      runId: null,
      chartView: "live-follow",
      chartPane: "price",
      bottomTab: "trades",
      paneTab: null,
      mode: "backtest",
      accountMode: "sim",
      simBalance: DEFAULT_SIM_BALANCE,
      paperSessionId: null,
      paperSelectionIds: [],
      paperVisibleStrategyIds: [],
      paperActiveStrategyId: null,
      paperBatchGroupId: null,
      paperSessionsByStrategyId: {},
      paperStreamsEnabled: false,
      theme: "light",
      backtestStartTs: defaultStartTs,
      backtestEndTs: defaultEndTs,
      indicators: { ...DEFAULT_INDICATORS },
      // 全新安装兜底（RV-1 / C-1）：只走初始化器、不触发 migrate，故默认值必须写在此处。
      watchlist: [...FALLBACK_INSTRUMENTS],
      instSidebarTab: "watch",
      chartSplitRatio: DEFAULT_CHART_SPLIT_RATIO,
      // 右侧默认激活「AI 助手」页签；会话 id 首次为空，挂载时生成并落盘以便重连恢复。
      rightTab: "agent",
      agentConversationId: "",

      setInst: (instId) => set({ instId, runId: null, chartView: "live-follow" }),
      setBar: (bar) => set({ bar, runId: null, chartView: "live-follow" }),
      setStrategyId: (strategyId) => set({ strategyId }),
      setRunId: (runId) => set({ runId }),
      setChartView: (chartView) =>
        set(chartView === "live-follow" ? { chartView, runId: null } : { chartView }),
      setChartPane: (chartPane) => set({ chartPane }),
      setBottomTab: (bottomTab) => set({ bottomTab }),
      setPaneTab: (paneTab) => set({ paneTab }),
      setMode: (mode) =>
        set({
          mode,
          chartView: mode === "compare" ? "compare-range" : "live-follow",
        }),
      // 账户模式单字段原子写（模式联动由组件编排：选「模拟盘」时另调 setMode("paper")）。
      setAccountMode: (accountMode) => set({ accountMode }),
      // 模拟资金：夹取到 [MIN, MAX]；非有限数忽略（守住边界，R8）。
      setSimBalance: (n) =>
        set((s) =>
          Number.isFinite(n)
            ? { simBalance: Math.min(Math.max(n, MIN_SIM_BALANCE), MAX_SIM_BALANCE) }
            : { simBalance: s.simBalance },
        ),
      setPaperSessionId: (paperSessionId) => set({ paperSessionId }),
      setPaperSelectionIds: (paperSelectionIds) => set({ paperSelectionIds }),
      togglePaperSelectionId: (id) =>
        set((s) => ({
          paperSelectionIds: s.paperSelectionIds.includes(id)
            ? s.paperSelectionIds.filter((item) => item !== id)
            : [...s.paperSelectionIds, id],
        })),
      setPaperVisibleStrategyIds: (paperVisibleStrategyIds) => set({ paperVisibleStrategyIds }),
      togglePaperVisibleStrategyId: (id) =>
        set((s) => ({
          paperVisibleStrategyIds: s.paperVisibleStrategyIds.includes(id)
            ? s.paperVisibleStrategyIds.filter((item) => item !== id)
            : [...s.paperVisibleStrategyIds, id],
        })),
      setPaperActiveStrategyId: (paperActiveStrategyId) => set({ paperActiveStrategyId }),
      setPaperBatchGroupId: (paperBatchGroupId) => set({ paperBatchGroupId }),
      setPaperSessionsByStrategyId: (paperSessionsByStrategyId) =>
        set({ paperSessionsByStrategyId }),
      setPaperStreamsEnabled: (paperStreamsEnabled) => set({ paperStreamsEnabled }),
      clearPaperRuntime: () =>
        set({
          paperSessionId: null,
          paperVisibleStrategyIds: [],
          paperActiveStrategyId: null,
          paperBatchGroupId: null,
          paperSessionsByStrategyId: {},
          paperStreamsEnabled: false,
        }),
      setBacktestRange: (backtestStartTs, backtestEndTs) =>
        set({ backtestStartTs, backtestEndTs, runId: null }),
      toggleTheme: () =>
        set((s) => {
          const theme = s.theme === "light" ? "dark" : "light";
          if (typeof document !== "undefined") {
            document.documentElement.classList.toggle("dark", theme === "dark");
          }
          return { theme };
        }),
      setTheme: (theme) => {
        if (typeof document !== "undefined") {
          document.documentElement.classList.toggle("dark", theme === "dark");
        }
        set({ theme });
      },
      // 技术指标开关（spec §4.7）：逐项切换 / 整体覆盖 / 恢复默认（MA+BOLL+MACD）。
      toggleIndicator: (key) =>
        set((s) => ({ indicators: { ...s.indicators, [key]: !s.indicators[key] } })),
      setIndicators: (indicators) => set({ indicators }),
      resetIndicators: () => set({ indicators: { ...DEFAULT_INDICATORS } }),
      // 自选（spec §6.4）：有序去重；新收藏置首便于即时可见（§7）。
      addToWatchlist: (instId) =>
        set((s) =>
          s.watchlist.includes(instId)
            ? s
            : { watchlist: [instId, ...s.watchlist] },
        ),
      removeFromWatchlist: (instId) =>
        set((s) => ({ watchlist: s.watchlist.filter((id) => id !== instId) })),
      toggleWatchlist: (instId) =>
        set((s) => ({
          watchlist: s.watchlist.includes(instId)
            ? s.watchlist.filter((id) => id !== instId)
            : [instId, ...s.watchlist],
        })),
      setInstSidebarTab: (instSidebarTab) => set({ instSidebarTab }),
      // 右侧页签切换（AI 助手 / 交易策略）；两面板各自持续挂载，切换不破坏状态（§6.2#1）。
      setRightTab: (rightTab) => set({ rightTab }),
      setAgentConversationId: (agentConversationId) => set({ agentConversationId }),
      // 新对话：生成新会话 uuid（旧消息流随 conversationId 变更而失效，DB 历史仍可经其原 id 查到）。
      newConversation: () => set({ agentConversationId: makeConversationId() }),
      // 纵向分栏比例：夹取到 [MIN, MAX]；非有限数忽略（守住边界，与 simBalance 同模式）。
      setChartSplitRatio: (n) =>
        set((s) =>
          Number.isFinite(n)
            ? {
                chartSplitRatio: Math.min(
                  Math.max(n, MIN_CHART_SPLIT_RATIO),
                  MAX_CHART_SPLIT_RATIO,
                ),
              }
            : { chartSplitRatio: s.chartSplitRatio },
        ),
    }),
    {
      name: "aragon-ui",
      version: 8,
      storage: createJSONStorage(() => localStorage),
      // 只持久化选择态 / 主题 / 账户态 / 自选；绝不持久化运行期句柄（runId 会 404、paperSessionId 进程重启即失效，R6）
      partialize: (s) => ({
        instId: s.instId,
        bar: s.bar,
        strategyId: s.strategyId,
        mode: s.mode,
        accountMode: s.accountMode,
        simBalance: s.simBalance,
        theme: s.theme,
        backtestStartTs: s.backtestStartTs,
        backtestEndTs: s.backtestEndTs,
        indicators: s.indicators,
        chartPane: s.chartPane,
        bottomTab: s.bottomTab,
        paneTab: s.paneTab,
        watchlist: s.watchlist,
        instSidebarTab: s.instSidebarTab,
        chartSplitRatio: s.chartSplitRatio,
        rightTab: s.rightTab,
        agentConversationId: s.agentConversationId,
      }),
      // 逐版补默认态（即便缺省 zustand 浅合并也回落初值，migrate 仅为显式稳妥，R7/R11）。
      migrate: (persisted, version) => {
        let next = persisted as Record<string, unknown>;
        if (version < 2) {
          next = { ...next, indicators: { ...DEFAULT_INDICATORS } };
        }
        if (version < 3) {
          next = { ...next, chartPane: "price", bottomTab: "trades" };
        }
        if (version < 4) {
          next = { ...next, paneTab: null };
        }
        if (version < 5) {
          next = { ...next, accountMode: "sim", simBalance: DEFAULT_SIM_BALANCE };
        }
        if (version < 6) {
          next = { ...next, watchlist: [...FALLBACK_INSTRUMENTS], instSidebarTab: "watch" };
        }
        if (version < 7) {
          next = { ...next, chartSplitRatio: DEFAULT_CHART_SPLIT_RATIO };
        }
        if (version < 8) {
          next = { ...next, rightTab: "agent", agentConversationId: "" };
        }
        return next as never;
      },
      // SSR / 水合安全（C2）：先以默认 state 渲染（与服务端 HTML 一致 → 无 hydration 告警），
      // 由根客户端组件挂载后显式 useAppStore.persist.rehydrate() 补水（接受"默认→恢复"一帧闪烁）。
      skipHydration: true,
    },
  ),
);
