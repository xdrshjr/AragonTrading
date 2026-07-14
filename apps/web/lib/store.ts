// 全局 UI / 选择态 (Zustand) (spec §8.2)。
// F3：用 persist 中间件把选择态/主题落 localStorage（key "aragon-ui"），刷新后恢复上次工作上下文。
import { create } from "zustand";
import { createJSONStorage, persist } from "zustand/middleware";
import { type ComboDraft, DEFAULT_COMBO_DRAFT } from "./combo";
import { DEFAULT_INDICATORS, type IndicatorKey } from "./indicators";
import { FALLBACK_INSTRUMENTS } from "./instruments";
import type {
  AccountMode,
  BacktestConfig,
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
import type {
  AutoTradeAllocationMode,
  AutoTradeMember,
  LiveAutoEvent,
  LiveAutoStatus,
} from "./liveAutoTypes";
import type {
  AiTradeConfig,
  AiTradeDecision,
  AiTradeGroupState,
  AiTradeRoundTrace,
  AiTradeSessionRow,
  AiTradeStatusEvent,
  AiTradeTraceEvent,
} from "./aiTradeTypes";
import type { AiBacktestConfig } from "./aiBacktestTypes";
import type { FactorEditTarget, FactorTargetRef, SymbolGroup } from "./factorTypes";
import type { WeightTrainResult, WeightTrainTarget } from "./weightModelTypes";
import type { WeightBatchTarget } from "./weightBatchTypes";
import type { WorkspacePrefs } from "./workspaceTypes";
import { clampAllocationOnEdit, defaultAllocationOnAdd, evenSplit } from "./autoTrade";
import { AI_TRADE_DEFAULT_POSITION_STEP, AI_TRADE_DEFAULT_WAKE_INTERVAL_MIN } from "./aiTrade";

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

// 主图 K 线与副图技术指标的内部纵向分栏比例（chart-indicator-pane-resize）：主蜡烛图占
// CandleChart 内部列高的比例，1-ratio 即副图技术指标窗格高度。夹取主图于 [0.4, 0.85] ⇒
// 副图于 [15%, 60%]，配合两区 min-h-[120px] 像素下限防塌陷。默认 0.8 与既有 flex-[4]/flex-1
//（4:1）像素等价。
export const DEFAULT_CHART_INDICATOR_RATIO = 0.8;
const MIN_CHART_INDICATOR_RATIO = 0.4;
const MAX_CHART_INDICATOR_RATIO = 0.85;

// 右侧「AI 助手 / 交易策略」面板像素宽：用户拖拽左边框调宽，夹取 [320, 640]，持久化刷新恢复。
// MAX 640 为宽屏（默认窗口 1440）上限；近最小窗口（860）放大右栏会触发 overflow-hidden 优雅裁切，
// 双击复位或拉宽窗口即恢复（不溢出硬保证包络为右栏 ≤ 默认 380，详见 spec §6.2）。
export const DEFAULT_RIGHT_PANEL_WIDTH = 380;
const MIN_RIGHT_PANEL_WIDTH = 320;
const MAX_RIGHT_PANEL_WIDTH = 640;

// 左侧标的侧栏像素宽：用户拖拽右边框调宽，夹取 [200, 460]，双击复位到默认，持久化刷新恢复。
// 与 rightPanelWidth 同套包络策略（有限数守卫 + 夹取 + partialize + 版本迁移补默认）。
export const DEFAULT_INSTRUMENT_SIDEBAR_WIDTH = 240;
const MIN_INSTRUMENT_SIDEBAR_WIDTH = 200;
const MAX_INSTRUMENT_SIDEBAR_WIDTH = 460;

// 盘口列占上区宽度比例：盘口面板移到 K 线右侧后默认约 15%，夹取 [0.10, 0.30]；
// 像素安全边界（min-w 160 / max-w 420）由组件兜底，与比例取较小 / 较大者，互不冲突。
export const DEFAULT_DEPTH_PANEL_RATIO = 0.15;
const MIN_DEPTH_PANEL_RATIO = 0.1;
const MAX_DEPTH_PANEL_RATIO = 0.3;

// 自动交易事件流环形缓冲上限（hardening H6）：超限裁最旧，杜绝长跑无界增长。
const LIVE_AUTO_EVENTS_MAX = 50;
// AI 自动交易决策日志环形缓冲上限（同 live-auto，超限裁最旧）。
const AI_TRADE_DECISIONS_MAX = 50;
// AI 自动交易决策认知轨迹环形上限（与后端 trace._TRACES_MAX 镜像；超限裁最旧）。
const AI_TRADE_TRACES_MAX = 20;

/** 依 agent_trace 事件构造在飞轮次桩（输入快照 + 空 thinking/analysis + deciding 态）。 */
function emptyRoundTrace(evt: AiTradeTraceEvent): AiTradeRoundTrace {
  return {
    round: evt.round,
    ts: evt.ts,
    lastPx: evt.lastPx,
    position: evt.position ?? null,
    signalBoard: evt.signalBoard ?? [],
    marketContext: evt.marketContext ?? null,
    consensus: evt.consensus ?? null,
    marketDigest: evt.marketDigest ?? null,
    multiTimeframeContext: evt.multiTimeframeContext ?? null,
    trigger: evt.trigger,   // wake-interval：唤醒来源随 round_start 装配进轮次（供 describeTrigger 区分周期唤醒）
    thinking: "",
    analysis: "",
    toolCallPending: false,
    toolCall: null,
    executed: null,
    degradeReason: null,
    summary: null,
    errorMessage: null,
    retries: 0,
    lastRetryReason: null,
    state: "deciding",
  };
}

/** 单轮 phase 归约（纯函数）：把一条非 round_start 事件应用到已定位的轮次对象上。 */
function reduceRoundPhase(t: AiTradeRoundTrace, evt: AiTradeTraceEvent): AiTradeRoundTrace {
  switch (evt.phase) {
    case "thinking":
      return { ...t, thinking: t.thinking + (evt.delta ?? "") };
    case "analysis":
      return { ...t, analysis: t.analysis + (evt.delta ?? "") };
    case "tool_call_start":
      return { ...t, toolCallPending: true };
    case "tool_call":
      return { ...t, toolCall: evt.input ?? null, toolCallPending: false };
    case "result":
      return {
        ...t,
        executed: evt.executed ?? null,
        degradeReason: evt.degradeReason ?? null,
        state: evt.executed?.skipped ? "skipped" : "done",
      };
    case "summary":
      return { ...t, summary: evt.summary ?? null };
    case "error":
      return { ...t, state: "error", errorMessage: evt.message ?? null };
    case "retry":
      // §4.4：重试前重置本轮累积（丢弃失败尝试的研判文本 + 工具挂起指示）、复位 deciding、累加计数。
      return {
        ...t,
        thinking: "",
        analysis: "",
        toolCallPending: false, // R-1：与后端一致，清失败尝试残留的工具挂起指示
        state: "deciding",
        retries: (t.retries ?? 0) + 1,
        lastRetryReason: evt.reason ?? null,
      };
    default:
      return t;
  }
}

/**
 * 轨迹归约（纯函数、幂等、容乱序）：把一条 agent_trace 事件应用到轮次列表。
 * round_start 按 round 去重新建（已存在则刷新输入快照、保留累积文本）；其余 phase 定位该轮追加，
 * 若该轮不存在（丢过 round_start）则以事件补建桩轮次；超环形容量裁最旧。
 */
export function applyAiTradeTracePhase(
  traces: AiTradeRoundTrace[],
  evt: AiTradeTraceEvent,
): AiTradeRoundTrace[] {
  const idx = traces.findIndex((t) => t.round === evt.round);
  if (evt.phase === "round_start") {
    const seed = emptyRoundTrace(evt);
    const next =
      idx >= 0
        ? traces.map((t, i) =>
            i === idx ? { ...seed, thinking: t.thinking, analysis: t.analysis } : t,
          )
        : [...traces, seed];
    return next.slice(-AI_TRADE_TRACES_MAX);
  }
  const base = idx >= 0 ? traces[idx] : emptyRoundTrace(evt);
  const updated = reduceRoundPhase(base, evt);
  if (idx >= 0) return traces.map((t, i) => (i === idx ? updated : t));
  return [...traces, updated].slice(-AI_TRADE_TRACES_MAX);
}

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
  // 运行回测配置弹窗偏好（strategy-backtest-config-modal §五）：区间预设/周期/成本/杠杆；持久化跨刷新恢复。
  // 只持久化 preset 与自定义值本身；预设区间在打开弹窗时按 now 重派生（绝不持久化陈旧绝对时间当预设结果）。
  backtestConfig: BacktestConfig | null;
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
  // 自选自动交易分组（watchlist-auto-trade-group）：autoTradeMembers 为持久化偏好态（watchlist 的有序
  // 子集 + 各自百分比）；liveAutoGroup 为瞬态运行期态（刷新由 GET /live-auto/group/status 重发现）。
  autoTradeMembers: AutoTradeMember[];
  // 分配模式偏好（auto-trade-budget-and-allocation-settings §5.2，持久化，默认 "even"）：决定成员
  // 百分比的夹取 / 默认语义与守门口径；max_ratio 下各标的为独立上限（Σ 可超 100）。
  autoTradeAllocationMode: AutoTradeAllocationMode;
  liveAutoGroup: LiveAutoGroupState | null;
  // AI 自动交易「分组」运行期态（ai-trade-multi-symbol-and-timeframe §4.2，瞬态、不入 partialize，
  // 刷新由 GET /ai-trade/status 重发现）。成员按 instId 映射，各承载会话视图 / 成交 / 持仓 / 决策 / 轨迹 /
  // 摘要（镜像 liveAutoGroup）。aiTradeConfig 为持久化偏好（记忆上次策略集合与刷新轮数，进 partialize）。
  aiTradeGroup: AiTradeGroupState | null;
  aiTradeAcknowledged: boolean;                   // 首启风险勾选会话记忆，刷新复位
  aiTradeConfig: AiTradeConfig | null;            // 持久化偏好（进 partialize）
  // AI 回测（ai-auto-trading-backtest §5.4）：aiBacktestJobId 瞬态（GET /ai-trade/backtest/{id} 重发现）；
  // aiBacktestConfig 为持久化偏好（记忆决策间隔 / 刷新轮数，进 partialize，v25 承载无需升版）。
  aiBacktestJobId: string | null;
  aiBacktestConfig: AiBacktestConfig | null;
  theme: Theme;
  backtestStartTs: number;
  backtestEndTs: number;
  indicators: Record<IndicatorKey, boolean>;
  watchlist: string[];
  // 因子实验室标的组（factor-lab-symbol-groups §3.2）：持久化偏好态 + 后端权威水合；成员来自自选标的但
  // **不**按当前自选裁剪（§3.2 决策，避免静默掏空）。factorLabTarget 为工作台目标指针（记忆上次选择）。
  symbolGroups: SymbolGroup[];
  factorLabTarget: FactorTargetRef | null;
  instSidebarTab: InstSidebarTab;
  chartSplitRatio: number;
  // 主图 K 线与副图技术指标的内部分栏比例（主图占内部列高，1-ratio 即副图高）；持久化偏好。
  chartIndicatorRatio: number;
  rightPanelWidth: number;
  // 左侧标的侧栏像素宽（用户可拖拽右边框调宽，双击复位）；持久化偏好。
  instrumentSidebarWidth: number;
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
  // 跨视图编辑交接载荷（瞬态，不入 partialize）：「因子组合策略」列表 / 交易面板「在因子实验室编辑」
  // 写入，FactorSetBuilder 挂载时消费一次性水合并立即清空。不持久化（避免刷新后水合到陈旧编辑指针）。
  factorEditTarget: FactorEditTarget | null;
  // 权重模型工作区交接态（瞬态，不入 partialize，weight-model-workspace §3.4）：
  // weightTrainTarget —— builder 点「训练权重」写入，WeightModelPanel 消费一次性切到实时训练视图；
  // weightTrainResult —— WeightTrainLive「应用到组合并返回」写入，FactorSetBuilder 消费水合 trained；
  // boundModelId —— 同搭建会话内反复训练复用同一模型（R-02 去重记忆），刷新即复位。
  weightTrainTarget: WeightTrainTarget | null;
  weightTrainResult: WeightTrainResult | null;
  // weightBatchTarget —— builder / 列表点「多模型训练」写入，WeightModelPanel 消费一次性切到竞技场启动器
  // （瞬态，不入 partialize，随 weightTrainTarget 一并复位，weight-multi-model-training §3.4）。
  weightBatchTarget: WeightBatchTarget | null;
  boundModelId: number | null;

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
  setBacktestConfig: (cfg: BacktestConfig) => void;
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
  // 自动交易分组成员（持久化）增删 / 改百分比 / 平均分配 / 重排（含「移出自选即移出自动交易」不变量）。
  addAutoTradeMember: (instId: string) => void;
  removeAutoTradeMember: (instId: string) => void;
  setAutoTradeAllocation: (instId: string, pct: number) => void;
  setAutoTradeAllocationMode: (mode: AutoTradeAllocationMode) => void;
  evenSplitAutoTradeMembers: () => void;
  reorderAutoTradeMembers: (fromIndex: number, toIndex: number) => void;
  // 自动交易分组运行期态（瞬态）：整组写入 / 成员事件路由 / 清理。
  setLiveAutoGroup: (group: LiveAutoGroupState | null) => void;
  setLiveAutoMemberStatus: (instId: string, status: LiveAutoStatus | null) => void;
  setLiveAutoMemberPosition: (instId: string, position: PaperPositionEvent | null) => void;
  setLiveAutoMemberTrades: (instId: string, trades: Trade[]) => void;
  appendLiveAutoMemberTrade: (instId: string, trade: Trade) => void;
  clearLiveAutoGroup: () => void;
  // AI 自动交易「分组」运行期态（瞬态）：分组视图 + 逐成员 setter（会话视图 / 成交 / 持仓 / 决策 / 轨迹 /
  // 摘要，按 instId 路由，镜像 liveAutoGroup member setter）+ 风险勾选 + 清理；aiTradeConfig 为持久化偏好。
  setAiTradeGroup: (group: AiTradeGroupState | null) => void;
  setAiTradeMemberSession: (instId: string, session: AiTradeSessionRow | null) => void;
  patchAiTradeMemberSession: (instId: string, patch: Partial<AiTradeSessionRow>) => void;
  patchAiTradeMemberStatus: (instId: string, status: AiTradeStatusEvent) => void;
  setAiTradeMemberTrades: (instId: string, trades: Trade[]) => void;
  appendAiTradeMemberTrade: (instId: string, trade: Trade) => void;
  setAiTradeMemberPosition: (instId: string, pos: PaperPositionEvent | null) => void;
  pushAiTradeMemberDecision: (instId: string, decision: AiTradeDecision) => void;
  setAiTradeMemberDecisions: (instId: string, decisions: AiTradeDecision[]) => void;
  setAiTradeMemberTraces: (instId: string, traces: AiTradeRoundTrace[]) => void;
  upsertAiTradeMemberTracePhase: (instId: string, evt: AiTradeTraceEvent) => void;
  setAiTradeMemberSummary: (instId: string, summary: string | null) => void;
  setAiTradeAcknowledged: (v: boolean) => void;
  clearAiTradeRuntime: () => void;
  setAiTradeConfig: (config: AiTradeConfig | null) => void;
  // AI 回测：活跃作业句柄（瞬态，断线重发现）+ 持久化偏好 setter。
  setAiBacktestJobId: (id: string | null) => void;
  setAiBacktestConfig: (config: AiBacktestConfig | null) => void;
  setBacktestRange: (startTs: number, endTs: number) => void;
  toggleTheme: () => void;
  setTheme: (t: Theme) => void;
  toggleIndicator: (key: IndicatorKey) => void;
  setIndicators: (rec: Record<IndicatorKey, boolean>) => void;
  resetIndicators: () => void;
  addToWatchlist: (instId: string) => void;
  removeFromWatchlist: (instId: string) => void;
  toggleWatchlist: (instId: string) => void;
  // 标的组增删改（factor-lab-symbol-groups §3.2）：不可变纯 CRUD（id / updatedTs 由调用方注入）；写穿后端 + localStorage。
  addSymbolGroup: (group: SymbolGroup) => void;
  updateSymbolGroup: (id: string, patch: Partial<Omit<SymbolGroup, "id">>) => void;
  removeSymbolGroup: (id: string) => void;
  // 工作台目标指针（记忆上次选择，跨会话恢复）：null=默认焦点单标的。
  setFactorLabTarget: (target: FactorTargetRef | null) => void;
  // 后端水合：用后端权威值整体覆盖 workspace 偏好切片（watchlist-auto-trade-persistence §2.2），
  // 并重申不变量 autoTradeMembers ⊆ watchlist。仅由 useWorkspaceSync 首次 GET 调用（写穿不回写，P1-B）。
  hydrateWorkspace: (prefs: WorkspacePrefs) => void;
  setInstSidebarTab: (tab: InstSidebarTab) => void;
  setChartSplitRatio: (ratio: number) => void;
  setChartIndicatorRatio: (ratio: number) => void;
  setRightPanelWidth: (px: number) => void;
  setInstrumentSidebarWidth: (px: number) => void;
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
  setFactorEditTarget: (target: FactorEditTarget | null) => void;
  setWeightTrainTarget: (target: WeightTrainTarget | null) => void;
  setWeightBatchTarget: (target: WeightBatchTarget | null) => void;
  setWeightTrainResult: (result: WeightTrainResult | null) => void;
  setBoundModelId: (id: number | null) => void;
}

// 主图因子叠加指令（show_factor ui-action / 因子实验室「上图」按钮设置）。
export interface FactorOverlay {
  kind: "factor" | "score";
  factorKey?: string;
  factorSetId?: number;
}

// 自选自动交易分组运行期态（watchlist-auto-trade-group §5.4，瞬态，不入 partialize，刷新由
// GET /live-auto/group/status 重发现）。成员 runtime 按 instId 映射，承载各自会话的 WS 事件路由结果。
export interface LiveAutoGroupMemberState {
  instId: string;
  sessionId: string;
  runId: number;
  allocationPct: number;
  budgetUsd: number;
  status: LiveAutoStatus | null;
  position: PaperPositionEvent | null;
  trades: Trade[];
}
export interface LiveAutoGroupState {
  groupId: string;
  strategyId: number;
  strategyName: string;
  bar: string;
  totalBudgetUsd: number;
  leverage: number;
  state: "running" | "stopped";
  startedTs: number;
  members: Record<string, LiveAutoGroupMemberState>;
}

// 成员 runtime 局部更新（纯函数，缺组 / 缺成员安全回落不变更）。
function patchLiveAutoMember(
  group: LiveAutoGroupState | null,
  instId: string,
  updater: (m: LiveAutoGroupMemberState) => LiveAutoGroupMemberState,
): { liveAutoGroup: LiveAutoGroupState | null } {
  if (!group) return { liveAutoGroup: group };
  const member = group.members[instId];
  if (!member) return { liveAutoGroup: group };
  return { liveAutoGroup: { ...group, members: { ...group.members, [instId]: updater(member) } } };
}

// AI 分组成员 runtime 局部更新（纯函数，缺组 / 缺成员安全回落不变更；镜像 patchLiveAutoMember）。
function patchAiTradeMember(
  group: AiTradeGroupState | null,
  instId: string,
  updater: (m: AiTradeGroupState["members"][string]) => AiTradeGroupState["members"][string],
): { aiTradeGroup: AiTradeGroupState | null } {
  if (!group) return { aiTradeGroup: group };
  const member = group.members[instId];
  if (!member) return { aiTradeGroup: group };
  return { aiTradeGroup: { ...group, members: { ...group.members, [instId]: updater(member) } } };
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
      // 运行回测配置偏好默认 null（全新安装只走初始化器、不触发 migrate，首开弹窗回落默认成本 + 当前区间）。
      backtestConfig: null,
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
      // 自动交易分组：成员持久化（默认空，老用户自动交易区初始为空）；运行期态瞬态（刷新复位）。
      autoTradeMembers: [],
      autoTradeAllocationMode: "even",
      liveAutoGroup: null,
      // AI 自动交易：分组运行期态瞬态（刷新由 GET /ai-trade/status 重发现）；aiTradeConfig 偏好默认 null
      // （全新安装只走初始化器、不触发 migrate，故默认值须写在此处）。
      aiTradeGroup: null,
      aiTradeAcknowledged: false,
      aiTradeConfig: null,
      aiBacktestJobId: null,
      aiBacktestConfig: null,
      theme: "light",
      backtestStartTs: defaultStartTs,
      backtestEndTs: defaultEndTs,
      indicators: { ...DEFAULT_INDICATORS },
      // 全新安装兜底（RV-1 / C-1）：只走初始化器、不触发 migrate，故默认值必须写在此处。
      watchlist: [...FALLBACK_INSTRUMENTS],
      // 标的组默认空、目标指针默认 null（全新安装只走初始化器、不触发 migrate，故默认值必须写在此处）。
      symbolGroups: [],
      factorLabTarget: null,
      instSidebarTab: "watch",
      chartSplitRatio: DEFAULT_CHART_SPLIT_RATIO,
      // 全新安装只走初始化器、不触发 migrate，故面板尺寸默认值必须写在此处（与 chartSplitRatio 并列）。
      chartIndicatorRatio: DEFAULT_CHART_INDICATOR_RATIO,
      rightPanelWidth: DEFAULT_RIGHT_PANEL_WIDTH,
      instrumentSidebarWidth: DEFAULT_INSTRUMENT_SIDEBAR_WIDTH,
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
      // 编辑交接载荷默认空（瞬态，不持久化）。
      factorEditTarget: null,
      // 权重模型工作区交接态默认空（瞬态，不持久化）。
      weightTrainTarget: null,
      weightBatchTarget: null,
      weightTrainResult: null,
      boundModelId: null,

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
      // 运行回测配置偏好写：弹窗内成功运行后乐观持久化（strategy-backtest-config-modal §三）。
      setBacktestConfig: (backtestConfig) => set({ backtestConfig }),
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
      // 自动交易成员（持久化）：加入仅当已在自选且未重复，按当前分配模式给默认百分比（§2.3）。
      addAutoTradeMember: (instId) =>
        set((s) =>
          !s.watchlist.includes(instId) || s.autoTradeMembers.some((m) => m.instId === instId)
            ? s
            : {
                autoTradeMembers: [
                  ...s.autoTradeMembers,
                  {
                    instId,
                    allocationPct: defaultAllocationOnAdd(
                      s.autoTradeMembers,
                      s.autoTradeAllocationMode,
                    ),
                  },
                ],
              },
        ),
      removeAutoTradeMember: (instId) =>
        set((s) => ({ autoTradeMembers: s.autoTradeMembers.filter((m) => m.instId !== instId) })),
      // 手改百分比：按当前模式夹取（even → [1, 100 − Σ其余]；max_ratio → [1, 100]，各自独立）。
      setAutoTradeAllocation: (instId, pct) =>
        set((s) => ({
          autoTradeMembers: s.autoTradeMembers.map((m) =>
            m.instId === instId
              ? {
                  ...m,
                  allocationPct: clampAllocationOnEdit(
                    s.autoTradeMembers,
                    instId,
                    pct,
                    s.autoTradeAllocationMode,
                  ),
                }
              : m,
          ),
        })),
      // 切换分配模式（持久化偏好）：仅改模式；既有成员值保留，后续编辑 / 默认随新模式生效（§2.4-2）。
      setAutoTradeAllocationMode: (autoTradeAllocationMode) => set({ autoTradeAllocationMode }),
      evenSplitAutoTradeMembers: () =>
        set((s) => ({ autoTradeMembers: evenSplit(s.autoTradeMembers) })),
      reorderAutoTradeMembers: (fromIndex, toIndex) =>
        set((s) => {
          const next = [...s.autoTradeMembers];
          if (
            fromIndex < 0 ||
            fromIndex >= next.length ||
            toIndex < 0 ||
            toIndex >= next.length
          ) {
            return s;
          }
          const [moved] = next.splice(fromIndex, 1);
          next.splice(toIndex, 0, moved);
          return { autoTradeMembers: next };
        }),
      // 自动交易分组运行期态（瞬态）：整组写入 / 成员事件路由 / 清理。
      setLiveAutoGroup: (liveAutoGroup) => set({ liveAutoGroup }),
      setLiveAutoMemberStatus: (instId, status) =>
        set((s) => patchLiveAutoMember(s.liveAutoGroup, instId, (m) => ({ ...m, status }))),
      setLiveAutoMemberPosition: (instId, position) =>
        set((s) => patchLiveAutoMember(s.liveAutoGroup, instId, (m) => ({ ...m, position }))),
      setLiveAutoMemberTrades: (instId, trades) =>
        set((s) => patchLiveAutoMember(s.liveAutoGroup, instId, (m) => ({ ...m, trades }))),
      appendLiveAutoMemberTrade: (instId, trade) =>
        set((s) =>
          patchLiveAutoMember(s.liveAutoGroup, instId, (m) =>
            m.trades.some(
              (t) => t.ts === trade.ts && t.side === trade.side && t.price === trade.price,
            )
              ? m
              : { ...m, trades: [...m.trades, trade] },
          ),
        ),
      clearLiveAutoGroup: () => set({ liveAutoGroup: null }),
      // AI 自动交易「分组」运行期态（瞬态）：分组视图 + 逐成员 setter（会话视图含增量合并 / 成交流 /
      // 持仓 / 决策日志（环形）/ 轨迹（环形）/ 摘要，按 instId 路由）+ 风险勾选 + 清理；aiTradeConfig 持久化。
      setAiTradeGroup: (aiTradeGroup) => set({ aiTradeGroup }),
      setAiTradeMemberSession: (instId, session) =>
        set((s) => patchAiTradeMember(s.aiTradeGroup, instId, (m) => ({ ...m, session }))),
      patchAiTradeMemberSession: (instId, patch) =>
        set((s) =>
          patchAiTradeMember(s.aiTradeGroup, instId, (m) =>
            m.session ? { ...m, session: { ...m.session, ...patch } } : m,
          ),
        ),
      patchAiTradeMemberStatus: (instId, status) =>
        set((s) =>
          patchAiTradeMember(s.aiTradeGroup, instId, (m) =>
            m.session
              ? { ...m, session: { ...m.session, ...(status as Partial<AiTradeSessionRow>) } }
              : m,
          ),
        ),
      setAiTradeMemberTrades: (instId, trades) =>
        set((s) => patchAiTradeMember(s.aiTradeGroup, instId, (m) => ({ ...m, trades }))),
      appendAiTradeMemberTrade: (instId, trade) =>
        set((s) =>
          patchAiTradeMember(s.aiTradeGroup, instId, (m) =>
            m.trades.some(
              (t) => t.ts === trade.ts && t.side === trade.side && t.price === trade.price,
            )
              ? m
              : { ...m, trades: [...m.trades, trade] },
          ),
        ),
      setAiTradeMemberPosition: (instId, position) =>
        set((s) => patchAiTradeMember(s.aiTradeGroup, instId, (m) => ({ ...m, position }))),
      pushAiTradeMemberDecision: (instId, decision) =>
        set((s) =>
          patchAiTradeMember(s.aiTradeGroup, instId, (m) => ({
            ...m,
            decisions: [...m.decisions, decision].slice(-AI_TRADE_DECISIONS_MAX),
          })),
        ),
      setAiTradeMemberDecisions: (instId, decisions) =>
        set((s) =>
          patchAiTradeMember(s.aiTradeGroup, instId, (m) => ({
            ...m,
            decisions: decisions.slice(-AI_TRADE_DECISIONS_MAX),
          })),
        ),
      setAiTradeMemberTraces: (instId, traces) =>
        set((s) =>
          patchAiTradeMember(s.aiTradeGroup, instId, (m) => ({
            ...m,
            traces: traces.slice(-AI_TRADE_TRACES_MAX),
          })),
        ),
      upsertAiTradeMemberTracePhase: (instId, evt) =>
        set((s) =>
          patchAiTradeMember(s.aiTradeGroup, instId, (m) => ({
            ...m,
            traces: applyAiTradeTracePhase(m.traces, evt),
          })),
        ),
      setAiTradeMemberSummary: (instId, summary) =>
        set((s) => patchAiTradeMember(s.aiTradeGroup, instId, (m) => ({ ...m, summary }))),
      setAiTradeAcknowledged: (aiTradeAcknowledged) => set({ aiTradeAcknowledged }),
      clearAiTradeRuntime: () => set({ aiTradeGroup: null }),
      setAiTradeConfig: (aiTradeConfig) => set({ aiTradeConfig }),
      setAiBacktestJobId: (aiBacktestJobId) => set({ aiBacktestJobId }),
      setAiBacktestConfig: (aiBacktestConfig) => set({ aiBacktestConfig }),
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
      // 取消收藏须同步移出自动交易成员（不变量：autoTradeMembers ⊆ watchlist，§2.2-1 / R5）。
      removeFromWatchlist: (instId) =>
        set((s) => ({
          watchlist: s.watchlist.filter((id) => id !== instId),
          autoTradeMembers: s.autoTradeMembers.filter((m) => m.instId !== instId),
        })),
      toggleWatchlist: (instId) =>
        set((s) =>
          s.watchlist.includes(instId)
            ? {
                watchlist: s.watchlist.filter((id) => id !== instId),
                autoTradeMembers: s.autoTradeMembers.filter((m) => m.instId !== instId),
              }
            : { watchlist: [instId, ...s.watchlist] },
        ),
      // 后端权威水合（§2.2）：整体覆盖切片，并重申 autoTradeMembers ⊆ watchlist（丢弃不在自选内的成员，
      // 与 removeFromWatchlist 同口径）。标的组**不**按自选裁剪（§3.2 决策：避免临时取消自选静默掏空已保存组），
      // 直接覆盖（后端 sanitizer 已规整）；旧后端文档缺 symbolGroups 时回落 []（缺省安全）。
      hydrateWorkspace: ({ watchlist, autoTradeMembers, autoTradeAllocationMode, symbolGroups }) =>
        set(() => {
          const inWatchlist = new Set(watchlist);
          return {
            watchlist,
            autoTradeMembers: autoTradeMembers.filter((m) => inWatchlist.has(m.instId)),
            autoTradeAllocationMode,
            symbolGroups: symbolGroups ?? [],
          };
        }),
      // 标的组不可变 CRUD（factor-lab-symbol-groups §3.2）：id / updatedTs 由调用方注入；切片变更触发写穿后端。
      addSymbolGroup: (group) => set((s) => ({ symbolGroups: [...s.symbolGroups, group] })),
      updateSymbolGroup: (id, patch) =>
        set((s) => ({
          symbolGroups: s.symbolGroups.map((g) => (g.id === id ? { ...g, ...patch } : g)),
        })),
      removeSymbolGroup: (id) =>
        set((s) => ({ symbolGroups: s.symbolGroups.filter((g) => g.id !== id) })),
      // 工作台目标指针（持久化，记忆上次选择）：由 FactorLab.setTarget 双写（本地态 + 此指针）。
      setFactorLabTarget: (factorLabTarget) => set({ factorLabTarget }),
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
      // 编辑交接载荷（瞬态）：写入后由 FactorSetBuilder 消费一次性水合并清空（不入 partialize）。
      setFactorEditTarget: (factorEditTarget) => set({ factorEditTarget }),
      // 权重模型交接态（瞬态）：builder ⇄ 实时训练视图一次性消费载荷 + 同会话模型复用记忆。
      setWeightTrainTarget: (weightTrainTarget) => set({ weightTrainTarget }),
      setWeightBatchTarget: (weightBatchTarget) => set({ weightBatchTarget }),
      setWeightTrainResult: (weightTrainResult) => set({ weightTrainResult }),
      setBoundModelId: (boundModelId) => set({ boundModelId }),
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
      // 主图/副图内部分栏比例：夹取到 [MIN, MAX]；非有限数忽略（与 chartSplitRatio 同模式）。
      setChartIndicatorRatio: (n) =>
        set((s) =>
          Number.isFinite(n)
            ? {
                chartIndicatorRatio: Math.min(
                  Math.max(n, MIN_CHART_INDICATOR_RATIO),
                  MAX_CHART_INDICATOR_RATIO,
                ),
              }
            : { chartIndicatorRatio: s.chartIndicatorRatio },
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
      // 左侧标的侧栏宽：夹取到 [MIN, MAX] 像素；非有限数忽略（镜像 setRightPanelWidth）。
      setInstrumentSidebarWidth: (n) =>
        set((s) =>
          Number.isFinite(n)
            ? {
                instrumentSidebarWidth: Math.min(
                  Math.max(n, MIN_INSTRUMENT_SIDEBAR_WIDTH),
                  MAX_INSTRUMENT_SIDEBAR_WIDTH,
                ),
              }
            : { instrumentSidebarWidth: s.instrumentSidebarWidth },
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
      version: 29,
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
        // 运行回测配置偏好（strategy-backtest-config-modal §五，持久化跨刷新恢复）。
        backtestConfig: s.backtestConfig,
        simBalance: s.simBalance,
        theme: s.theme,
        backtestStartTs: s.backtestStartTs,
        backtestEndTs: s.backtestEndTs,
        indicators: s.indicators,
        chartPane: s.chartPane,
        bottomTab: s.bottomTab,
        paneTab: s.paneTab,
        watchlist: s.watchlist,
        // 标的组与工作台目标指针（偏好态，持久化 + 后端权威水合，factor-lab-symbol-groups §3.2）。
        symbolGroups: s.symbolGroups,
        factorLabTarget: s.factorLabTarget,
        // 自动交易成员（偏好态，持久化）；liveAutoGroup 为运行期瞬态，**不**入 partialize（刷新由
        // GET /live-auto/group/status 重发现）。
        autoTradeMembers: s.autoTradeMembers,
        autoTradeAllocationMode: s.autoTradeAllocationMode,
        // AI 自动交易偏好（持久化）；aiTrade 运行期态全瞬态，**不**入 partialize（刷新由
        // GET /ai-trade/status 重发现）。
        aiTradeConfig: s.aiTradeConfig,
        // AI 回测偏好（持久化）；aiBacktestJobId 瞬态**不**入 partialize（刷新由 GET 重发现）。
        aiBacktestConfig: s.aiBacktestConfig,
        instSidebarTab: s.instSidebarTab,
        chartSplitRatio: s.chartSplitRatio,
        chartIndicatorRatio: s.chartIndicatorRatio,
        rightPanelWidth: s.rightPanelWidth,
        instrumentSidebarWidth: s.instrumentSidebarWidth,
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
        if (version < 20) {
          // 自选自动交易分组：补持久化 autoTradeMembers 默认（老用户自动交易区初始为空，自选不变）；
          // liveAutoGroup 为瞬态（不入 partialize），浅合并回落 null 即可。
          next = { ...next, autoTradeMembers: [] };
        }
        if (version < 21) {
          // 自动交易分配模式：补默认 "even"（老用户行为不变；max_ratio 为新增可选模式）。
          next = { ...next, autoTradeAllocationMode: "even" };
        }
        if (version < 22) {
          // 主图/副图内部分栏比例：补默认 0.8（追加字段，浅合并本就回落初值，迁移显式稳妥）。
          next = { ...next, chartIndicatorRatio: DEFAULT_CHART_INDICATOR_RATIO };
        }
        if (version < 23) {
          // 权重模型工作区：交接态（weightTrainTarget/weightTrainResult/boundModelId）全为瞬态、不入
          // partialize，浅合并回落 null 即可；此处显式占位稳妥（与既有迭代同范式，R-12）。
        }
        if (version < 24) {
          // 因子实验室标的组：回填持久化 symbolGroups 默认（老用户初始为空）与目标指针 factorLabTarget=null
          // （resolveFactorTarget 对缺失指针回落焦点单标的，缺省安全，factor-lab-symbol-groups §3.2 / R3）。
          next = { ...next, symbolGroups: [], factorLabTarget: null };
        }
        if (version < 25) {
          // AI 自动交易：运行期态全瞬态（不入 partialize），浅合并回落初值即可；偏好 aiTradeConfig
          // 进 partialize，补默认 null（缺失时启动弹窗回落默认策略与刷新轮数）。
          next = { ...next, aiTradeConfig: null };
        }
        if (version < 26) {
          // 左侧标的侧栏可拖拽调宽：补像素宽默认（追加字段，浅合并本就回落初值，迁移显式稳妥）。
          next = { ...next, instrumentSidebarWidth: DEFAULT_INSTRUMENT_SIDEBAR_WIDTH };
        }
        if (version < 27) {
          // 运行回测配置弹窗偏好：补默认 null（缺失时首开弹窗回落默认成本 + 当前区间，不臆造陈旧绝对时间）。
          next = { ...next, backtestConfig: null };
        }
        if (version < 28) {
          // 仓位操作档位偏好（[R-11] 防御性——读点已 `?? AI_TRADE_DEFAULT_POSITION_STEP` 兜底，新增可选
          // 字段本不强制迁移）。**[R-3] 必须 null 守卫**：aiTradeConfig 初始值 / 多版本落点均为 null，
          // 对 null 取属性赋值会抛 TypeError 致水合崩溃；仅在其为对象时补默认。
          const cfg = next.aiTradeConfig as AiTradeConfig | null;
          if (cfg) {
            next = {
              ...next,
              aiTradeConfig: { ...cfg, positionStepPct: cfg.positionStepPct ?? AI_TRADE_DEFAULT_POSITION_STEP },
            };
          }
        }
        if (version < 29) {
          // Agent 墙钟定时唤醒周期偏好（防御性——读点已 `?? AI_TRADE_DEFAULT_WAKE_INTERVAL_MIN` 兜底，新增
          // 可选字段本不强制迁移）。必须 null 守卫：aiTradeConfig 多版本落点为 null，对 null 取属性赋值会抛
          // TypeError 致水合崩溃；仅在其为对象时补默认（镜像 v28 positionStepPct 迁移）。
          const cfg = next.aiTradeConfig as AiTradeConfig | null;
          if (cfg) {
            next = {
              ...next,
              aiTradeConfig: {
                ...cfg,
                wakeIntervalMinutes: cfg.wakeIntervalMinutes ?? AI_TRADE_DEFAULT_WAKE_INTERVAL_MIN,
              },
            };
          }
        }
        return next as never;
      },
      // SSR / 水合安全（C2）：先以默认 state 渲染（与服务端 HTML 一致 → 无 hydration 告警），
      // 由根客户端组件挂载后显式 useAppStore.persist.rehydrate() 补水（接受"默认→恢复"一帧闪烁）。
      skipHydration: true,
    },
  ),
);
