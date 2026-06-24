// 全局 UI / 选择态 (Zustand) (spec §8.2)。
// F3：用 persist 中间件把选择态/主题落 localStorage（key "aragon-ui"），刷新后恢复上次工作上下文。
import { create } from "zustand";
import { createJSONStorage, persist } from "zustand/middleware";
import { type ComboDraft, DEFAULT_COMBO_DRAFT } from "./combo";
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

// 右侧「AI 助手 / 交易策略」面板像素宽：用户拖拽左边框调宽，夹取 [320, 640]，持久化刷新恢复。
// MAX 640 为宽屏（默认窗口 1440）上限；近最小窗口（860）放大右栏会触发 overflow-hidden 优雅裁切，
// 双击复位或拉宽窗口即恢复（不溢出硬保证包络为右栏 ≤ 默认 380，详见 spec §6.2）。
export const DEFAULT_RIGHT_PANEL_WIDTH = 380;
const MIN_RIGHT_PANEL_WIDTH = 320;
const MAX_RIGHT_PANEL_WIDTH = 640;

// 盘口列占上区宽度比例：盘口面板移到 K 线右侧后默认约 15%，夹取 [0.10, 0.30]；
// 像素安全边界（min-w 160 / max-w 420）由组件兜底，与比例取较小 / 较大者，互不冲突。
export const DEFAULT_DEPTH_PANEL_RATIO = 0.15;
const MIN_DEPTH_PANEL_RATIO = 0.1;
const MAX_DEPTH_PANEL_RATIO = 0.3;

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
  // 实盘「真实下单」风险勾选的会话记忆（瞬态，不持久化，刷新即复位 → 每会话首单仍需勾选，C1-3）。
  liveAcknowledged: boolean;
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
  rightPanelWidth: number;
  depthPanelRatio: number;
  rightTab: RightTab;
  agentConversationId: string;
  // 策略组合草稿（成员 id + 轮动参数）：持久化，跨刷新恢复上次搭建的组合上下文。
  comboDraft: ComboDraft;
  // 组合编辑指针：null=新建态，number=正在编辑该组合 id。瞬态（不入 partialize），
  // 刷新即复位，避免指向已删行（无需 bump 持久化 version）。
  comboEditingId: number | null;
  // 设置中心（spec §5.3）：showSettings 为瞬态（不持久化，刷新回工作台）；settingsSection 持久化。
  showSettings: boolean;
  settingsSection: string;

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
  setLiveAcknowledged: (v: boolean) => void;
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
  setRightPanelWidth: (px: number) => void;
  setDepthPanelRatio: (ratio: number) => void;
  setRightTab: (tab: RightTab) => void;
  setAgentConversationId: (id: string) => void;
  newConversation: () => void;
  setComboDraft: (draft: ComboDraft) => void;
  setComboEditingId: (id: number | null) => void;
  setShowSettings: (show: boolean) => void;
  setSettingsSection: (section: string) => void;
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
      liveAcknowledged: false,
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
      // 全新安装只走初始化器、不触发 migrate，故面板尺寸默认值必须写在此处（与 chartSplitRatio 并列）。
      rightPanelWidth: DEFAULT_RIGHT_PANEL_WIDTH,
      depthPanelRatio: DEFAULT_DEPTH_PANEL_RATIO,
      // 右侧默认激活「AI 助手」页签；会话 id 首次为空，挂载时生成并落盘以便重连恢复。
      rightTab: "agent",
      agentConversationId: "",
      comboDraft: { memberIds: [], policy: { ...DEFAULT_COMBO_DRAFT.policy } },
      comboEditingId: null,
      // 设置中心：默认在工作台（showSettings=false）；默认设置项为「AI 模型服务」。
      showSettings: false,
      settingsSection: "ai-models",

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
          // 组合回测后用 runId 在左图画单条组合净值（复用 run-range）；对比走 compare-range；余者实时。
          chartView:
            mode === "compare"
              ? "compare-range"
              : mode === "combo"
                ? "run-range"
                : "live-follow",
        }),
      // 账户模式写：选「实盘」时联动 chartView="live-follow"（§5.3）；「模拟盘」保持现状，
      // setMode("paper") 由组件另行编排。实盘进入由组件做凭证 / 开关守门（C1-5），此处只落状态。
      setAccountMode: (accountMode) =>
        set(accountMode === "live" ? { accountMode, chartView: "live-follow" } : { accountMode }),
      // 实盘风险勾选会话记忆（仅记忆勾选本身，不跳过逐单明细复核弹窗，C1-3）。
      setLiveAcknowledged: (liveAcknowledged) => set({ liveAcknowledged }),
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
      // 组合草稿：成员选择 / 轮动参数变更（持久化，跨刷新恢复）。
      setComboDraft: (comboDraft) => set({ comboDraft }),
      // 组合编辑指针：进入编辑置 id，保存 / 放弃置 null（瞬态，不持久化）。
      setComboEditingId: (comboEditingId) => set({ comboEditingId }),
      // 设置中心：切换设置面板（瞬态）/ 切换当前设置项（持久化）。
      setShowSettings: (showSettings) => set({ showSettings }),
      setSettingsSection: (settingsSection) => set({ settingsSection }),
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
      // 右侧面板宽：夹取到 [MIN, MAX] 像素；非有限数忽略（守住边界，与 chartSplitRatio 同模式）。
      setRightPanelWidth: (n) =>
        set((s) =>
          Number.isFinite(n)
            ? {
                rightPanelWidth: Math.min(
                  Math.max(n, MIN_RIGHT_PANEL_WIDTH),
                  MAX_RIGHT_PANEL_WIDTH,
                ),
              }
            : { rightPanelWidth: s.rightPanelWidth },
        ),
      // 盘口列比例：夹取到 [MIN, MAX]；非有限数忽略（像素安全边界由组件 min-w/max-w 兜底）。
      setDepthPanelRatio: (n) =>
        set((s) =>
          Number.isFinite(n)
            ? {
                depthPanelRatio: Math.min(
                  Math.max(n, MIN_DEPTH_PANEL_RATIO),
                  MAX_DEPTH_PANEL_RATIO,
                ),
              }
            : { depthPanelRatio: s.depthPanelRatio },
        ),
    }),
    {
      name: "aragon-ui",
      version: 12,
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
        rightPanelWidth: s.rightPanelWidth,
        depthPanelRatio: s.depthPanelRatio,
        rightTab: s.rightTab,
        agentConversationId: s.agentConversationId,
        comboDraft: s.comboDraft,
        // 持久化当前设置项；showSettings 为瞬态故**不**入 partialize（刷新默认回工作台，R-Migrate）。
        settingsSection: s.settingsSection,
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
        if (version < 9) {
          next = { ...next, settingsSection: "ai-models" };
        }
        if (version < 10) {
          // 实盘交易模式：liveAcknowledged 为瞬态（不入 partialize），迁移仍显式补默认稳妥。
          // accountMode="live" 的水合守门（缺默认账户 / 开关关闭即回落 sim，C1-5）须依赖后端异步数据，
          // 故由 AccountModeSwitch 挂载时校验，迁移此处不动 accountMode。
          next = { ...next, liveAcknowledged: false };
        }
        if (version < 11) {
          // 策略组合：补默认组合草稿（成员空 + 轮动参数默认）。
          next = { ...next, comboDraft: { ...DEFAULT_COMBO_DRAFT, policy: { ...DEFAULT_COMBO_DRAFT.policy } } };
        }
        if (version < 12) {
          // 工作台面板尺寸：补右侧面板宽与盘口列比例默认（追加字段，浅合并本就回落初值，迁移显式稳妥）。
          next = {
            ...next,
            rightPanelWidth: DEFAULT_RIGHT_PANEL_WIDTH,
            depthPanelRatio: DEFAULT_DEPTH_PANEL_RATIO,
          };
        }
        return next as never;
      },
      // SSR / 水合安全（C2）：先以默认 state 渲染（与服务端 HTML 一致 → 无 hydration 告警），
      // 由根客户端组件挂载后显式 useAppStore.persist.rehydrate() 补水（接受"默认→恢复"一帧闪烁）。
      skipHydration: true,
    },
  ),
);
