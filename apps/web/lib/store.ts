// 全局 UI / 选择态 (Zustand) (spec §8.2)。
// F3：用 persist 中间件把选择态/主题落 localStorage（key "aragon-ui"），刷新后恢复上次工作上下文。
import { create } from "zustand";
import { createJSONStorage, persist } from "zustand/middleware";
import { type ComboDraft, DEFAULT_COMBO_DRAFT } from "./combo";
import { DEFAULT_INDICATORS, type IndicatorKey } from "./indicators";
import { FALLBACK_INSTRUMENTS } from "./instruments";
import type {
  AccountMode,
  BacktestKind,
  Bar,
  BottomTab,
  ChartPane,
  ChartView,
  Mode,
  PaperPositionEvent,
  PaperStrategySession,
  StrategyListTab,
  Trade,
  WalkForwardResult,
} from "./types";
import type { LiveAutoEvent, LiveAutoStatus } from "./liveAutoTypes";

type Theme = "light" | "dark";
// 标的侧边栏页签：自选 / 现货 / 合约（"swap" 承载 SWAP+FUTURES 合约页签，RV-10）。
export type InstSidebarTab = "watch" | "spot" | "swap";
// 右侧面板页签：AI 助手 / 交易策略（Agent Trading Copilot §5.3，默认 "agent"）。
export type RightTab = "agent" | "strategy";
// 一级功能视图（左侧主导航 NavRail 驱动）：trade=交易工作台，factor=因子实验室；持久化跨刷新恢复。
export type WorkbenchView = "trade" | "factor";

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

// 高级回测（滑动窗口 / Walk-forward）默认窗口参数：镜像后端建议预设，持久化跨刷新恢复。
export interface WfParams {
  windowBars: number;
  stepBars: number;
  resetCapital: boolean;
}
export const DEFAULT_WF_PARAMS: WfParams = { windowBars: 1000, stepBars: 200, resetCapital: true };

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

// 自动交易事件流环形缓冲上限（hardening H6）：超限裁最旧，杜绝长跑无界增长。
const LIVE_AUTO_EVENTS_MAX = 50;

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
  strategyListTab: StrategyListTab;
  // 回测子模式（普通回测 | 高级回测/滑动窗口）：持久化；高级回测下渲染窗口分布而非单条净值。
  backtestKind: BacktestKind;
  // 高级回测窗口参数（持久化）；运行结果 walkForwardResult 不持久化（避免刷新后陈旧）。
  wfParams: WfParams;
  walkForwardResult: WalkForwardResult | null;
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
  // 实时自动交易运行期态（live-auto-trading §5.4，全部瞬态、不入 partialize，刷新即复位）。
  liveAutoSessionId: string | null;
  liveAutoStatus: LiveAutoStatus | null;
  liveAutoTrades: Trade[];                        // → overlayTrades（图表 markers）
  liveAutoPosition: PaperPositionEvent | null;
  liveAutoEvents: LiveAutoEvent[];                // 自动交易事件流（环形≤50，error/降级/终止可见性，H6）
  liveAutoAcknowledged: boolean;                  // 首启风险勾选会话记忆，刷新复位
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
  // 一级功能视图（左侧主导航 NavRail）：activeView 持久化（刷新恢复上次视图）；
  // 因子实验室可见性由 activeView === "factor" 派生。factorLabSection 持久化；
  // factorOverlay 瞬态（show_factor ui-action 驱动主图叠加因子/得分 pane）。
  activeView: WorkbenchView;
  factorLabSection: string;
  factorOverlay: FactorOverlay | null;

  setInst: (instId: string) => void;
  setBar: (bar: Bar) => void;
  setStrategyId: (id: number | null) => void;
  setRunId: (id: number | null) => void;
  setChartView: (view: ChartView) => void;
  setChartPane: (pane: ChartPane) => void;
  setBottomTab: (tab: BottomTab) => void;
  setPaneTab: (key: IndicatorKey | null) => void;
  setMode: (mode: Mode) => void;
  setStrategyListTab: (tab: StrategyListTab) => void;
  setBacktestKind: (kind: BacktestKind) => void;
  setWfParams: (patch: Partial<WfParams>) => void;
  setWalkForwardResult: (result: WalkForwardResult | null) => void;
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
  setLiveAutoSessionId: (id: string | null) => void;
  setLiveAutoStatus: (status: LiveAutoStatus | null) => void;
  setLiveAutoTrades: (trades: Trade[]) => void;
  appendLiveAutoTrade: (trade: Trade) => void;
  setLiveAutoPosition: (pos: PaperPositionEvent | null) => void;
  pushLiveAutoEvent: (event: LiveAutoEvent) => void;
  setLiveAutoAcknowledged: (v: boolean) => void;
  clearLiveAutoRuntime: () => void;
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
  setActiveView: (view: WorkbenchView) => void;
  setFactorLabSection: (section: string) => void;
  setFactorOverlay: (overlay: FactorOverlay | null) => void;
}

// 主图因子叠加指令（show_factor ui-action / 因子实验室「上图」按钮设置）。
export interface FactorOverlay {
  kind: "factor" | "score";
  factorKey?: string;
  factorSetId?: number;
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
      strategyListTab: "regular",
      backtestKind: "simple",
      wfParams: { ...DEFAULT_WF_PARAMS },
      walkForwardResult: null,
      // 本轮需求：实盘模式默认开启。无就绪默认账户时由 AccountModeSwitch 水合守门回落 sim。
      accountMode: "live",
      liveAcknowledged: false,
      simBalance: DEFAULT_SIM_BALANCE,
      paperSessionId: null,
      paperSelectionIds: [],
      paperVisibleStrategyIds: [],
      paperActiveStrategyId: null,
      paperBatchGroupId: null,
      paperSessionsByStrategyId: {},
      paperStreamsEnabled: false,
      // 实时自动交易运行期态（瞬态，刷新复位；句柄由挂载时 GET /live-auto/status 重发现）。
      liveAutoSessionId: null,
      liveAutoStatus: null,
      liveAutoTrades: [],
      liveAutoPosition: null,
      liveAutoEvents: [],
      liveAutoAcknowledged: false,
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
      // 一级视图：默认「交易」工作台；因子实验室默认工作区为「因子目录」；无叠加。
      activeView: "trade",
      factorLabSection: "catalog",
      factorOverlay: null,

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
          // 对比走 compare-range（左图叠加多策略归一化净值）；回测 / 实时走 live-follow。
          // 组合已并入回测列表：选中后由自动回测刷新绩效、由「运行回测」经 run-range 显式绘制单条净值。
          chartView: mode === "compare" ? "compare-range" : "live-follow",
        }),
      setStrategyListTab: (strategyListTab) => set({ strategyListTab }),
      // 回测子模式（普通 | 高级/滑动窗口）：切到 walkforward 时**不**联动 chartView（无单条净值，
      // 左图维持 K 线视图，结果只在右侧面板呈现，§7.4）。
      setBacktestKind: (backtestKind) => set({ backtestKind }),
      // 高级回测窗口参数局部更新（合并 patch）。
      setWfParams: (patch) => set((s) => ({ wfParams: { ...s.wfParams, ...patch } })),
      setWalkForwardResult: (walkForwardResult) => set({ walkForwardResult }),
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
      // 实时自动交易运行期态（瞬态）：句柄 / 状态 / 成交流 / 持仓 / 风险勾选记忆 + 清理。
      setLiveAutoSessionId: (liveAutoSessionId) => set({ liveAutoSessionId }),
      setLiveAutoStatus: (liveAutoStatus) => set({ liveAutoStatus }),
      setLiveAutoTrades: (liveAutoTrades) => set({ liveAutoTrades }),
      appendLiveAutoTrade: (trade) =>
        set((s) =>
          s.liveAutoTrades.some(
            (t) => t.ts === trade.ts && t.side === trade.side && t.price === trade.price,
          )
            ? s
            : { liveAutoTrades: [...s.liveAutoTrades, trade] },
        ),
      setLiveAutoPosition: (liveAutoPosition) => set({ liveAutoPosition }),
      // 事件流环形追加：超上限裁最旧（H6，关键 error 由组件置顶高亮呈现）。
      pushLiveAutoEvent: (event) =>
        set((s) => ({
          liveAutoEvents: [...s.liveAutoEvents, event].slice(-LIVE_AUTO_EVENTS_MAX),
        })),
      setLiveAutoAcknowledged: (liveAutoAcknowledged) => set({ liveAutoAcknowledged }),
      clearLiveAutoRuntime: () =>
        set({
          liveAutoSessionId: null,
          liveAutoStatus: null,
          liveAutoTrades: [],
          liveAutoPosition: null,
          liveAutoEvents: [],
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
      // 一级视图切换（左侧 NavRail）：写入 activeView（持久化，跨切换/刷新保态）；
      // 切换当前因子工作区（持久化）/ 设置主图叠加（瞬态）。
      setActiveView: (activeView) => set({ activeView }),
      setFactorLabSection: (factorLabSection) => set({ factorLabSection }),
      setFactorOverlay: (factorOverlay) => set({ factorOverlay }),
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
      version: 19,
      storage: createJSONStorage(() => localStorage),
      // 只持久化选择态 / 主题 / 账户态 / 自选；绝不持久化运行期句柄（runId 会 404、paperSessionId 进程重启即失效，R6）
      partialize: (s) => ({
        instId: s.instId,
        bar: s.bar,
        strategyId: s.strategyId,
        mode: s.mode,
        strategyListTab: s.strategyListTab,
        // 高级回测子模式与窗口参数持久化；walkForwardResult 为运行期产物，**不**持久化（避免刷新后陈旧）。
        backtestKind: s.backtestKind,
        wfParams: s.wfParams,
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
        // 持久化一级视图（NavRail）与因子实验室当前工作区；factorOverlay 为瞬态，**不**入 partialize。
        activeView: s.activeView,
        factorLabSection: s.factorLabSection,
      }),
      // 逐版补默认态（即便缺省 zustand 浅合并也回落初值，migrate 仅为显式稳妥，R7/R11）。
      migrate: (persisted, version) => {
        let next = persisted as Record<string, unknown>;
        const hadLegacyComboMode = (next as { mode?: string }).mode === "combo";
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
        if (version < 13) {
          // 高级回测：补回测子模式与窗口参数默认（walkForwardResult 为瞬态，不入 partialize）。
          next = { ...next, backtestKind: "simple", wfParams: { ...DEFAULT_WF_PARAMS } };
        }
        if (version < 14) {
          // 「组合」模式页签已删（组合并入回测列表）：把历史持久化的 mode:"combo" 安全收敛到
          // "backtest"，避免水合到一个已不存在的视图（comboDraft/comboEditingId 形态不变，无需迁移）。
          if ((next as { mode?: string }).mode === "combo") next.mode = "backtest";
        }
        if (version < 15) {
          next = {
            ...next,
            strategyListTab: hadLegacyComboMode ? "combo" : "regular",
          };
        }
        if (version < 16) {
          // 本轮需求：实盘模式默认开启。无差别把老用户持久化态升到 "live"（含曾主动选 sim 者，
          // 持久化态无法区分「默认 sim」与「主动选 sim」）；无就绪默认账户者仍由 AccountModeSwitch
          // 水合守门回落 sim，故不会静默进入无凭证的实盘。
          next = { ...next, accountMode: "live" };
        }
        if (version < 17) {
          // 因子层：补因子实验室当前工作区默认（showFactorLab/factorOverlay 为瞬态，不入 partialize）。
          next = { ...next, factorLabSection: "catalog" };
        }
        if (version < 18) {
          // 实时自动交易：运行期态全部瞬态（不入 partialize），浅合并回落初值即可；此处仅显式占位稳妥。
        }
        if (version < 19) {
          // 左侧主导航：补一级视图默认（老用户从未持久化过 showFactorLab，仅补 activeView 默认即可）。
          next = { ...next, activeView: "trade" };
        }
        return next as never;
      },
      // SSR / 水合安全（C2）：先以默认 state 渲染（与服务端 HTML 一致 → 无 hydration 告警），
      // 由根客户端组件挂载后显式 useAppStore.persist.rehydrate() 补水（接受"默认→恢复"一帧闪烁）。
      skipHydration: true,
    },
  ),
);
