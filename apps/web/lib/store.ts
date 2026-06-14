// 全局 UI / 选择态 (Zustand) (spec §8.2)。
// F3：用 persist 中间件把选择态/主题落 localStorage（key "aragon-ui"），刷新后恢复上次工作上下文。
import { create } from "zustand";
import { createJSONStorage, persist } from "zustand/middleware";
import { DEFAULT_INDICATORS, type IndicatorKey } from "./indicators";
import type { Bar, BottomTab, ChartPane, ChartView, Mode } from "./types";

type Theme = "light" | "dark";
const DEFAULT_RANGE_DAYS = 30;
const defaultEndTs = Math.floor(Date.now() / 1000);
const defaultStartTs = defaultEndTs - DEFAULT_RANGE_DAYS * 24 * 60 * 60;

interface AppState {
  instId: string;
  bar: Bar;
  strategyId: number | null;
  runId: number | null;
  chartView: ChartView;
  chartPane: ChartPane;
  bottomTab: BottomTab;
  mode: Mode;
  paperSessionId: string | null;
  theme: Theme;
  backtestStartTs: number;
  backtestEndTs: number;
  indicators: Record<IndicatorKey, boolean>;

  setInst: (instId: string) => void;
  setBar: (bar: Bar) => void;
  setStrategyId: (id: number | null) => void;
  setRunId: (id: number | null) => void;
  setChartView: (view: ChartView) => void;
  setChartPane: (pane: ChartPane) => void;
  setBottomTab: (tab: BottomTab) => void;
  setMode: (mode: Mode) => void;
  setPaperSessionId: (id: string | null) => void;
  setBacktestRange: (startTs: number, endTs: number) => void;
  toggleTheme: () => void;
  setTheme: (t: Theme) => void;
  toggleIndicator: (key: IndicatorKey) => void;
  setIndicators: (rec: Record<IndicatorKey, boolean>) => void;
  resetIndicators: () => void;
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
      mode: "backtest",
      paperSessionId: null,
      theme: "light",
      backtestStartTs: defaultStartTs,
      backtestEndTs: defaultEndTs,
      indicators: { ...DEFAULT_INDICATORS },

      setInst: (instId) => set({ instId, runId: null, chartView: "live-follow" }),
      setBar: (bar) => set({ bar, runId: null, chartView: "live-follow" }),
      setStrategyId: (strategyId) => set({ strategyId }),
      setRunId: (runId) => set({ runId }),
      setChartView: (chartView) =>
        set(chartView === "live-follow" ? { chartView, runId: null } : { chartView }),
      setChartPane: (chartPane) => set({ chartPane }),
      setBottomTab: (bottomTab) => set({ bottomTab }),
      setMode: (mode) =>
        set({
          mode,
          chartView: mode === "compare" ? "compare-range" : "live-follow",
        }),
      setPaperSessionId: (paperSessionId) => set({ paperSessionId }),
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
    }),
    {
      name: "aragon-ui",
      version: 3,
      storage: createJSONStorage(() => localStorage),
      // 只持久化选择态 / 主题；绝不持久化运行期句柄（runId 会 404、paperSessionId 进程重启即失效，R6）
      partialize: (s) => ({
        instId: s.instId,
        bar: s.bar,
        strategyId: s.strategyId,
        mode: s.mode,
        theme: s.theme,
        backtestStartTs: s.backtestStartTs,
        backtestEndTs: s.backtestEndTs,
        indicators: s.indicators,
        chartPane: s.chartPane,
        bottomTab: s.bottomTab,
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
        return next as never;
      },
      // SSR / 水合安全（C2）：先以默认 state 渲染（与服务端 HTML 一致 → 无 hydration 告警），
      // 由根客户端组件挂载后显式 useAppStore.persist.rehydrate() 补水（接受"默认→恢复"一帧闪烁）。
      skipHydration: true,
    },
  ),
);
