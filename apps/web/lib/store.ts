// 全局 UI / 选择态 (Zustand) (spec §8.2)。
// F3：用 persist 中间件把选择态/主题落 localStorage（key "aragon-ui"），刷新后恢复上次工作上下文。
import { create } from "zustand";
import { agentTradingApi } from "./agentTradingApi";
import { createJSONStorage, persist } from "zustand/middleware";
import { type ComboDraft, DEFAULT_COMBO_DRAFT } from "./combo";
import { applyDiscoveryEvent } from "./discovery";
import type { DiscoveryRunState, DiscoveryWsEvent } from "./discoveryTypes";
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
  AiTradeGroupLifecyclePatch,
  AiTradeGroupState,
  AiTradeNotice,
  AiTradeNoticeKind,
  AiTradeResumableGroup,
  AiTradeRoundTrace,
  AiTradeSessionRow,
  AiTradeStatusEvent,
  AiTradeTraceEvent,
} from "./aiTradeTypes";
import type { AiBacktestConfig } from "./aiBacktestTypes";
import type { AiTradeIntelSummary } from "./intelTypes";
import type { JournalFilters } from "./journalTypes";
import type { FactorEditTarget, FactorTargetRef, SymbolGroup } from "./factorTypes";
import type { WeightTrainResult, WeightTrainTarget } from "./weightModelTypes";
import type { WeightBatchTarget } from "./weightBatchTypes";
import type { WorkspacePrefs } from "./workspaceTypes";
import { TRADES_MAX } from "./wireCaps";
import { clampAllocationOnEdit, defaultAllocationOnAdd, evenSplit } from "./autoTrade";
import {
  AI_TRADE_DEFAULT_POSITION_STEP,
  AI_TRADE_DEFAULT_WAKE_INTERVAL_MIN,
  AI_TRADE_RECONCILE_WINDOW_MS,
} from "./aiTrade";
// 提示纪元的纯核心（notice-state-sync §5.3）：store 是它的消费者，方向恒为
// `aiTradeTypes` ← `aiTrade` ← `aiTradeNotice` ← 本文件。
import {
  AI_TRADE_NOTICE_RULES,
  currentAiTradeEpoch,
  isAiTradeNoticeBlockedBySticky,
  isAiTradeNoticeLive,
  mintAiTradeNotice,
} from "./aiTradeNotice";

// 分组级验收三键的补丁面（横切 `AiTradeGroupState` 的一个**窄**子集，故在此单独取名——
// 用 `Partial<AiTradeGroupState>` 会让这个 action 有能力覆盖 `members` / `trades`，
// 而它存在的全部理由恰恰是「不许碰那两个」）。
export type AiTradeGroupAcceptancePatch = Pick<
  AiTradeGroupState,
  "acceptance" | "exits" | "funding"
>;

type Theme = "light" | "dark";
// 标的侧边栏页签：自选 / 现货 / 合约（"swap" 承载 SWAP+FUTURES 合约页签，RV-10）。
export type InstSidebarTab = "watch" | "spot" | "swap";
// 右侧面板页签：AI 助手 / 交易策略（Agent Trading Copilot §5.3，默认 "agent"）。
export type RightTab = "agent" | "strategy";
// 一级功能视图（左侧主导航 NavRail 驱动）：trade=交易工作台，factor=因子实验室；持久化跨刷新恢复。
export type WorkbenchView = "trade" | "factor" | "discovery" | "journal";

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
    // 唤醒感知上下文（wake-aware-context §3.3）：本函数是**逐键手抄**装配，漏抄这四键
    // `AiTradeRoundTrace` 永远拿不到唤醒原因与战术周期证据 ⇒ 后端发什么前端都收不到
    // （跑不出错、功能静默缺失，这是先验层与仓位层都踩过的原坑）。
    wake: evt.wake ?? null,
    tacticalContext: evt.tacticalContext ?? null,
    tacticalTier: evt.tacticalTier ?? null,
    contextDisclosure: evt.contextDisclosure ?? null,
    // 交易经验（experience-context-graph W-13 的前端镜像）：同款逐键手抄纪律——漏抄这一键，
    // 「经验 ×N」徽标永远拿不到数据，而 tsc / vitest 都不会红。
    experience: evt.experience ?? null,
    // 执行周期趋势基调（execution-trend-verdict §3.7.4-4）：同款逐键手抄纪律——`round_start`
    // **只经过这一个装配点**（`applyAiTradeTracePhase` 在该分支里直接 `emptyRoundTrace(evt)`，
    // 不走 `reduceRoundPhase`），漏抄这一键后端发什么前端都收不到，且 tsc / vitest 都不会红。
    trendVerdict: evt.trendVerdict ?? null,
    trendGate: null,
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
    // 下单前二次反思（ai-trade-pre-trade-reflection §5.2）：仅交易意愿轮被写入，hold/关闭轮维持初值。
    reflection: "",
    reflectionPending: false,
    reflectionVerdict: null,
    reflectionReason: null,
    reflectionAdjustedSize: null,
    // 【基础策略】铁律门禁（ai-trade-base-strategy-ma-band §2.9）：规则未装配的会话恒为初值 null。
    baseRuleStatus: null,
    baseRuleZone: null,
    baseRulePctB: null,
    baseRuleReason: null,
    // 最小 edge 门禁（ai-trade-cost-accounting-and-edge-gate §4.3）：k_min=0 的会话恒为初值 null。
    edgeGateStatus: null,
    edgeBps: null,
    edgeCostBps: null,
    edgeGateReason: null,
    // 信号先验（signal-prior-and-ev-gate §6.3）：本函数是**逐键手抄**装配，不加这几行
    // `AiTradeRoundTrace` 永远拿不到先验数据 ⇒ 后端发什么前端都收不到（跑不出错、功能静默缺失）。
    edgeSource: null,
    priorP: null,
    priorEvBps: null,
    signalPriors: evt.signalPriors ?? null,
    // 仓位 / 刹车层（vol-sizing-and-drawdown-brake §6.3 第 4 点）：本函数是**逐键手抄**装配，漏抄任一
    // 键，`AiTradeRoundTrace` 永远拿不到该读数 ⇒ 后端发什么前端都收不到（跑不出错、功能静默缺失）。
    brakeLevel: null,
    brakeReason: null,
    drawdownPct: null,
    lossStreak: null,
    peakEquityBasis: null,
    unrealizedShare: null,
    modelFraction: null,
    fVol: null,
    fKelly: null,
    fCap: null,
    kellyStatus: null,
    fFinalRaw: null,
    finalFraction: null,
    scaleSource: null,
    scaleErased: null,
    volSource: null,
    stopPct: null,
    takePct: null,
    stopSource: null,
    effectiveExposure: null,
    effectiveExposureCap: null,
    corrScale: null,
    exposureCapUnreachable: null,
    // 持仓周期铁律层（ai-trade-holding-horizon-modes §3.4 / [P1-4]）：本函数是**逐键手抄**装配，
    // 漏抄这三键 `AiTradeRoundTrace` 永远拿不到本层数据 ⇒ 后端发什么前端都收不到（跑不出错、
    // 功能静默缺失——这是先验层与仓位层都踩过的原坑）。
    horizonStatus: null,
    horizonReason: null,
    horizonWire: null,
    // 离场优先级（exit-priority §6.3 / R-9）：本函数是**逐键手抄**装配，漏抄这四键
    // `AiTradeRoundTrace` 永远拿不到本层数据 ⇒ 后端发什么前端都收不到（跑不出错、功能静默缺失）。
    proposedAction: null,
    proposedReason: null,
    vetoedBy: null,
    // directional-symmetry B-3：中文层名（后端下发）。与上面三键同为**逐键手抄**——漏抄它
    // `AiTradeRoundTrace` 永远拿不到，界面会退回到「被门禁降级」这句没有信息量的话。
    layerLabel: null,
    exitPriority: null,
    // 深度思维链（deep-reasoning-chain §4.2）：本函数是**逐键手抄**装配，漏抄这两键
    // `AiTradeRoundTrace` 永远拿不到本层数据 ⇒ 后端发什么前端都收不到（跑不出错、功能静默缺失）。
    reaskAnalysis: "",
    reasoning: null,
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
    case "reask":
      // 浅研判追问（P0-3）：累积进**独立**字段，`analysis` 一个字符都不动。两份并列呈现是
      // 刻意的——被拒收的那一份正是「模型第二次反而想得更浅」的现场证据。
      return { ...t, reaskAnalysis: (t.reaskAnalysis ?? "") + (evt.delta ?? "") };
    case "tool_call_start":
      return { ...t, toolCallPending: true };
    case "tool_call":
      // 三个加法键保真「模型提议了什么、被谁拦下」（F-6）：没有它们，界面上只剩
      // 「决策工具 · 观望」，而模型提交的其实是 `close`——那正是用户最直观的困惑来源。
      return {
        ...t,
        toolCall: evt.input ?? null,
        toolCallPending: false,
        proposedAction: evt.proposedAction ?? null,
        proposedReason: evt.proposedReason ?? null,
        vetoedBy: evt.vetoedBy ?? null,
        // directional-symmetry B-3：第四个兄弟键。`emptyRoundTrace` 与这里**两处都要写**，
        // 漏任一处即静默丢数据（该文件已有四条同款警示注释）。
        layerLabel: evt.layerLabel ?? null,
        // 趋势基调闸（execution-trend-verdict §3.7.4-5）：本层没判过的轮次后端不发这个键，
        // 故用 `?? t.trendGate ??` 保住同一轮里先到的那一份（`trendVerdict` 走的是
        // `round_start` 那条轨，两条轨不可互相顶替）。
        trendGate: evt.trendGate ?? t.trendGate ?? null,
      };
    case "result":
      return {
        ...t,
        executed: evt.executed ?? null,
        degradeReason: evt.degradeReason ?? null,
        // 整份归约（不逐键手抄）：下次后端加字段时不会漏抄——`horizonWire` 已确立这个先例。
        reasoning: evt.reasoning ?? null,
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
        // 追问槽位与后端 `_apply_retry_marker` 逐字对齐：两侧清零集合一旦分叉，重连回放
        // 与在线归约就会给出两份不同的界面。`reasoning` 只在 `result` 相位定型（后端
        // 因此无需清），这里一并置空是为了乱序 / 重放帧下不会把上一次尝试的读数留在卡上。
        reaskAnalysis: "",
        reasoning: null,
        toolCallPending: false, // R-1：与后端一致，清失败尝试残留的工具挂起指示
        state: "deciding",
        retries: (t.retries ?? 0) + 1,
        lastRetryReason: evt.reason ?? null,
      };
    case "reflection_start":
      // 进入二次反思：置在飞指示（携被复核决策快照仅走 WS，不入轮次；轮次只留 pending 与后续累积）。
      return { ...t, reflectionPending: true };
    case "reflection":
      // 反思复核推理流式增量累积。
      return { ...t, reflection: (t.reflection ?? "") + (evt.delta ?? "") };
    case "base_rule":
      // 【基础策略】铁律门禁判定：落 status/zone/%B/降级码（每轮一条；未装配会话根本收不到此相位）。
      return {
        ...t,
        // `evt.status` 是三个相位共用的位（horizon 相位多两态），此处按 base_rule 的三态窄化：
        // 相位互斥，故不会收到 released/idle。
        baseRuleStatus: (evt.status as AiTradeRoundTrace["baseRuleStatus"]) ?? null,
        baseRuleZone: evt.zone ?? null,
        baseRulePctB: evt.pctB ?? null,
        baseRuleReason: evt.reason ?? null,
      };
    case "edge_gate":
      // 最小 edge 门禁判定：落 status/edge/成本/降级码（每轮一条；k_min=0 的会话根本收不到此相位）。
      return {
        ...t,
        edgeGateStatus: (evt.status as AiTradeRoundTrace["edgeGateStatus"]) ?? null,
        edgeBps: evt.edgeBps ?? null,
        edgeCostBps: evt.costBps ?? null,
        edgeGateReason: evt.reason ?? null,
        // 分子来源 + 先验读数（同上：不抄这三键，界面永远显示旧的「min(ATR 预算, 止盈距离)」文案）。
        edgeSource: evt.edgeSource ?? null,
        priorP: evt.priorP ?? null,
        priorEvBps: evt.priorEvBps ?? null,
      };
    case "sizing":
      // 仓位求解 + ATR 止损 + 刹车判定：每轮一条（三条规则全未装配的会话根本收不到此相位）。
      // `fFinalRaw` 与 `finalFraction` 必须同时落——两者之差就是被档位地板吃掉的量，只抄后者会让
      // 界面显示「减速生效了」而实际上是 no-op（[P1-2] 的假阳性观测）。
      return {
        ...t,
        brakeLevel: evt.brakeLevel ?? null,
        brakeReason: evt.brakeReason ?? null,
        drawdownPct: evt.drawdownPct ?? null,
        lossStreak: evt.lossStreak ?? null,
        peakEquityBasis: evt.peakEquityBasis ?? null,
        unrealizedShare: evt.unrealizedShare ?? null,
        modelFraction: evt.modelFraction ?? null,
        fVol: evt.fVol ?? null,
        fKelly: evt.fKelly ?? null,
        fCap: evt.fCap ?? null,
        kellyStatus: evt.kellyStatus ?? null,
        fFinalRaw: evt.fFinalRaw ?? null,
        finalFraction: evt.finalFraction ?? null,
        scaleSource: evt.scaleSource ?? null,
        scaleErased: evt.scaleErased ?? null,
        volSource: evt.volSource ?? null,
        stopPct: evt.stopPct ?? null,
        takePct: evt.takePct ?? null,
        stopSource: evt.stopSource ?? null,
        effectiveExposure: evt.effectiveExposure ?? null,
        effectiveExposureCap: evt.effectiveExposureCap ?? null,
        corrScale: evt.corrScale ?? null,
        exposureCapUnreachable: evt.exposureCapUnreachable ?? null,
      };
    case "horizon":
      // 持仓周期铁律判定：落 status/降级码/整份快照（每轮一条；未装配的会话根本收不到此相位）。
      // `horizonWire` **必须整份落**——本层读数有 20 余个（倒计时 / 确认进度 / 迟滞阈 / 解除原因 /
      // 交易所兜底价），逐键抄一遍必然在下一次加字段时漏掉，而 chip、轨迹、统计三处都要读它。
      return {
        ...t,
        horizonStatus: (evt.status as AiTradeRoundTrace["horizonStatus"]) ?? null,
        horizonReason: evt.reason ?? null,
        horizonWire: evt.horizon ?? null,
      };
    case "reflection_verdict":
      // 反思裁决：落 verdict/reason/实际收缩后仓位并清在飞指示。
      return {
        ...t,
        reflectionPending: false,
        reflectionVerdict: evt.verdict ?? null,
        reflectionReason: evt.reason ?? null,
        reflectionAdjustedSize: evt.adjustedSizeFraction ?? null,
        // 反思之后才定型的最终离场档位（exit-priority §6.3）：本 case 早已是手抄区，只多这一行。
        exitPriority: evt.exitPriority ?? null,
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
            i === idx
              ? { ...seed, thinking: t.thinking, analysis: t.analysis, reflection: t.reflection }
              : t,
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
  agentTradingBindingPending: boolean;
  agentTradingBindingError: string | null;
  agentTradingBindingRevision: number;
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
  // 冷占位（ai-trade-pause-resume §3.9.4）：进程重启后磁盘上仍有一份可续跑契约时非空。
  // **与 `aiTradeGroup` 互斥**——热暂停走 `aiTradeGroup.state === "paused"`，冷暂停才走这里。
  aiTradeResumable: AiTradeResumableGroup | null;
  // 生命周期回执的判定性提示（notice-state-sync §5.4）。**从裸字符串换成带纪元戳的对象是刻意的**：
  // 留着旧名旧形状，漏改的写点会悄悄编译通过，而本缺陷的根因正是「有写点、没人负责清」。
  // 「它此刻还成不成立」由 `isAiTradeNoticeLive` 一个纯谓词判定，写点只负责「说什么」。
  // 刻意**不进** `clearAiTradeRuntime`：结束那一刻分组正好被清掉，跟着清等于让这条提示永远显示不出来。
  aiTradeNotice: AiTradeNotice | null;
  /** 最近一次**确定**结论的对账**请求出发**墙钟（§4.3）。0 = 从未落地过；瞬态、不进 partialize。 */
  aiTradeReconciledAtMs: number;
  aiTradeAcknowledged: boolean;                   // 首启风险勾选会话记忆，刷新复位
  aiTradeConfig: AiTradeConfig | null;            // 持久化偏好（进 partialize）
  // ---- 生命周期真值化（ai-trade-lifecycle-truth F-9）：三个字段全部**瞬态、不进 partialize** ----
  // 一个瞬时的 `unknown` 被固化到下次开机，会让用户一开机就看到「与后端同步中」（风险 R-7）。
  // `partialize` 是显式白名单，不主动写进去就不会被持久化——`store.persist.test.ts` 有一条用例守它。
  /** 后端水合盲区 / 对账失败：`unknown` 时动作按钮全禁用（L-7 极性：读不到就不让点）。 */
  aiTradeSyncState: "ok" | "unknown";
  /** 快对账窗口截止时间戳（ms）：动作之后的 30 秒内用 3s 节奏收敛，其余 15s。 */
  aiTradeReconcileUntil: number | null;
  /** 后端总闸。**唯一写点 = 对账器**（Toggle 不再自己轮询，[评审 R0-7] 的单 owner 收敛）。 */
  aiTradeEnabled: boolean;
  // 标的市场情报的 summary 切片（ai-trade-market-intel §6.2 #38）。按 instId 映射、**瞬态**
  // （不进 partialize：它是服务端真值的镜像，重连后由 WS `snapshot.intel` 与 `intel` 事件补齐）。
  //
  // 刻意**不挂在 `aiTradeGroup` 之下**：`groupStateFromStatus` 是一个逐键手抄的映射，本仓库
  // 反复在它身上栽「WS 通路是好的、刷新后重发现拿不到值」的坑（E-10）。独立切片 + 独立 REST
  // 从结构上绕开它。
  aiTradeIntel: Record<string, AiTradeIntelSummary>;
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
  // 交易日志管理台的过滤条件（ai-trade-audit-journal §3.4）。**持久化**：审计是一件会跨天
  // 反复回到同一个断面的事，每次重开都要重设一遍标的与时间范围是纯粹的摩擦。
  journalFilters: JournalFilters;
  factorLabSection: string;
  factorOverlay: FactorOverlay | null;
  // 策略与因子挖掘（strategy-factor-discovery §2.13）：discoverySection 持久化（记忆上次分区）；
  // 取值域 overview | runs | monitor | candidates | library | assets（第 6 项为成果资产台账）。
  // discoveryRun / discoveryRunId / discoveryAssetId 为运行期瞬态（**不**入 partialize，
  // 刷新由 WS 首发 state 快照与 REST 重建）。
  discoverySection: string;
  discoveryRunId: number | null;
  discoveryRun: DiscoveryRunState | null;
  /** 台账详情抽屉指针（瞬态）：与 discoveryRunId 同姿态，刷新即复位。 */
  discoveryAssetId: number | null;
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
  setAiTradeResumable: (resumable: AiTradeResumableGroup | null) => void;
  /** 铸造一条带纪元戳的提示（§4.3 的铸造闸在内；`text` 空 / 全空白 ⇒ 清空）。 */
  noteAiTradeNotice: (kind: AiTradeNoticeKind, text: string | null) => void;
  /** 用户主动关闭。与 `syncAiTradeNotice`（系统清）分开命名，好让源码门禁分辨两者。 */
  dismissAiTradeNotice: () => void;
  /** store 卫生：对账落地后把已判死的提示真删掉（渲染安全由谓词保证，这里只是不留垃圾）。 */
  syncAiTradeNotice: () => void;
  /** 成员运行期状态行的**唯一**写点（带纪元；盖戳前置条件见 §4.5）。 */
  noteAiTradeMemberMessage: (instId: string, text: string | null) => void;
  setAiTradeMemberSession: (instId: string, session: AiTradeSessionRow | null) => void;
  patchAiTradeMemberSession: (instId: string, patch: Partial<AiTradeSessionRow>) => void;
  patchAiTradeMemberStatus: (instId: string, status: AiTradeStatusEvent) => void;
  patchAiTradeGroupAcceptance: (patch: AiTradeGroupAcceptancePatch) => void;
  // 生命周期对账的三个写点（F-9）。`patchAiTradeGroupLifecycle` 是**窄** action：
  // 只写分组级那几键 + 逐成员的六个生命周期键，绝不碰 `trades` / `position` / `decisions` /
  // `traces` / `summary`（那些是 WS 流的产物，覆盖即闪烁 / 丢历史），也不增删成员槽。
  patchAiTradeGroupLifecycle: (patch: AiTradeGroupLifecyclePatch) => void;
  requestAiTradeReconcile: () => void;
  setAiTradeSyncState: (state: "ok" | "unknown", startedAtMs?: number) => void;
  setAiTradeEnabled: (enabled: boolean) => void;
  /** 单标的情报 summary 落库（WS `intel` 事件与 `snapshot.intel` 共用；`null` 是 no-op）。 */
  setAiTradeIntelSummary: (instId: string, summary: AiTradeIntelSummary | null) => void;
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
  setJournalFilters: (next: JournalFilters) => void;
  setFactorLabSection: (section: string) => void;
  setFactorOverlay: (overlay: FactorOverlay | null) => void;
  setDiscoverySection: (section: string) => void;
  setDiscoveryRunId: (runId: number | null) => void;
  setDiscoveryAssetId: (assetId: number | null) => void;
  setDiscoveryRun: (state: DiscoveryRunState | null) => void;
  applyDiscoveryWsEvent: (event: DiscoveryWsEvent) => void;
  setDiscoveryConnected: (open: boolean) => void;
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

/**
 * 从**持久化偏好**里剥掉 `profitGuard.enabled`（ai-trade-loss-and-liveness-hardening A-2）。
 *
 * 写侧（`setAiTradeConfig`）与 persist v31 迁移**共用这一个函数**：只做其中一处都不成立——
 * 写侧管未来，迁移管那 193 份已经躺在 localStorage 里的「关」。
 * `config` 为 null / 无 `profitGuard` ⇒ 原样返回（**必须 null 守卫**：aiTradeConfig 的初始值与
 * 多个版本落点都是 null，对 null 取属性赋值会抛 TypeError 直接崩掉水合，见 v28/v29 的成文教训）。
 */
function stripProfitGuardEnabled(config: AiTradeConfig | null): AiTradeConfig | null {
  if (!config || !config.profitGuard) return config;
  const { enabled: _dropped, ...rest } = config.profitGuard;
  return { ...config, profitGuard: rest };
}

/** 模式仅在绑定 CAS 成功后发布；迟到响应不得切换新会话的模式。 */
async function updateTradingMode(accountMode: AccountMode) {
  const state = useAppStore.getState();
  const cid = state.agentConversationId;
  const revision = state.agentTradingBindingRevision;
  const isCurrent = () => useAppStore.getState().agentConversationId === cid
    && useAppStore.getState().agentTradingBindingRevision === revision;
  useAppStore.setState({ agentTradingBindingPending: true,
    agentTradingBindingError: null });
  try {
    const binding = await agentTradingApi.binding(cid);
    if (!isCurrent()) return;
    await agentTradingApi.bind(cid, {
      accountId: binding?.accountId ?? null, accountMode,
      expectedVersion: binding?.version ?? 0,
    });
    if (!isCurrent()) return;
    useAppStore.setState((current) => ({ accountMode,
      ...(accountMode === "live" ? { chartView: "live-follow" as const }
        : { mode: "paper" as const }),
      agentTradingBindingRevision: current.agentTradingBindingRevision + 1,
    }));
  } catch (error) {
    if (!isCurrent()) return;
    useAppStore.setState({ agentTradingBindingError:
      error instanceof Error ? error.message : "账户模式更新失败。" });
  } finally {
    useAppStore.setState({ agentTradingBindingPending: false });
  }
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
      agentTradingBindingPending: false,
      agentTradingBindingError: null,
      agentTradingBindingRevision: 0,
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
      aiTradeResumable: null,
      aiTradeNotice: null,
      aiTradeReconciledAtMs: 0,
      aiTradeAcknowledged: false,
      aiTradeConfig: null,
      // 生命周期真值化（F-9）：三个瞬态字段。首屏 store 未水合时 `aiTradeEnabled` 是 `false`，
      // 而 `aiTradeSyncState` 恰好也是 `"ok"` —— 门禁文案因此必须区分这两件事（风险 R-11）：
      // 「正在与后端同步…」和「本机未开启 AI 自动交易」不是同一句话，后者在首屏是**假的**。
      aiTradeSyncState: "ok",
      aiTradeReconcileUntil: null,
      aiTradeEnabled: false,
      aiTradeIntel: {},
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
      // 全新安装只走初始化器、不触发 migrate，故默认值必须写在此处（与 activeView 并列）。
      journalFilters: {},
      factorLabSection: "catalog",
      factorOverlay: null,
      // 挖掘：默认分区「总览」；运行期态瞬态（全新安装只走初始化器、不触发 migrate，故默认值写在此处）。
      discoverySection: "overview",
      discoveryRunId: null,
      discoveryRun: null,
      discoveryAssetId: null,
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
      setAccountMode: (accountMode) => {
        const state = useAppStore.getState();
        if (state.agentTradingBindingPending) return;
        if (!state.agentConversationId) {
          set(accountMode === "live"
            ? { accountMode, chartView: "live-follow" } : { accountMode });
          return;
        }
        void updateTradingMode(accountMode);
      },
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
      // 成交流封顶（D4）：整列与增量两条路径都裁到末 TRADES_MAX 条。去重的 `some()` 扫描范围
      // 随之有界，会话全程的 O(n²) 上界被钉死；语义不变（保留的永远是最新的那些）。
      setLiveAutoTrades: (liveAutoTrades) => set({ liveAutoTrades: liveAutoTrades.slice(-TRADES_MAX) }),
      appendLiveAutoTrade: (trade) =>
        set((s) =>
          s.liveAutoTrades.some(
            (t) => t.ts === trade.ts && t.side === trade.side && t.price === trade.price,
          )
            ? s
            : { liveAutoTrades: [...s.liveAutoTrades, trade].slice(-TRADES_MAX) },
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
        set((s) =>
          patchLiveAutoMember(s.liveAutoGroup, instId, (m) => ({
            ...m,
            trades: trades.slice(-TRADES_MAX),   // 成交流封顶（D4）
          })),
        ),
      appendLiveAutoMemberTrade: (instId, trade) =>
        set((s) =>
          patchLiveAutoMember(s.liveAutoGroup, instId, (m) =>
            m.trades.some(
              (t) => t.ts === trade.ts && t.side === trade.side && t.price === trade.price,
            )
              ? m
              : { ...m, trades: [...m.trades, trade].slice(-TRADES_MAX) },
          ),
        ),
      clearLiveAutoGroup: () => set({ liveAutoGroup: null }),
      // AI 自动交易「分组」运行期态（瞬态）：分组视图 + 逐成员 setter（会话视图含增量合并 / 成交流 /
      // 持仓 / 决策日志（环形）/ 轨迹（环形）/ 摘要，按 instId 路由）+ 风险勾选 + 清理；aiTradeConfig 持久化。
      setAiTradeGroup: (aiTradeGroup) => set({ aiTradeGroup }),
      setAiTradeResumable: (aiTradeResumable) => set({ aiTradeResumable }),
      // 提示的**唯一**铸造点（notice-state-sync §4.3）：纪元在 store 内部读取，调用方只给「说什么」。
      // 铸造闸的极性是刻意的 —— 一句「我不知道现在是什么态」压不过一句仍然成立的「你有一笔仓没平掉」。
      noteAiTradeNotice: (kind, text) =>
        set((s) => {
          const nowMs = Date.now();
          const epoch = currentAiTradeEpoch(s.aiTradeGroup, s.aiTradeResumable, s.aiTradeReconciledAtMs);
          if (isAiTradeNoticeBlockedBySticky(s.aiTradeNotice, kind, epoch, nowMs)) return {};
          return { aiTradeNotice: mintAiTradeNotice(kind, text, epoch, nowMs) };
        }),
      dismissAiTradeNotice: () => set({ aiTradeNotice: null }),
      syncAiTradeNotice: () =>
        set((s) => {
          if (!s.aiTradeNotice) return {};
          const nowMs = Date.now();
          const epoch = currentAiTradeEpoch(s.aiTradeGroup, s.aiTradeResumable, s.aiTradeReconciledAtMs);
          return isAiTradeNoticeLive(s.aiTradeNotice, epoch, nowMs) ? {} : { aiTradeNotice: null };
        }),
      // 成员状态行的唯一写点（§4.5）。`session.message` 今天没有复位点：`_status_payload()` 不含
      // `message` 键，浅合并清不掉；L4 告警又「只出声一次」。改法是写点盖戳、读点过谓词。
      // **前置条件是 `lifecycle` 键在场**（[评审 RP1-4]）：「清一条过期回声是安全的」这条论证的
      // 全部依据，是每一种仍在持续的降级都另有一处状态派生的常驻落点在说（`AiTradeFeedRecoveryCard`
      // 读 `session.lifecycle.feedRecovery`）；而那个键是总闸门控、关闭即整键缺席 —— 落点不在场时
      // 清掉 `message` 就是抹掉一个仍在持续的降级。键不在场 ⇒ 不盖戳 ⇒ 缺席即渲染 ⇒ 逐字回退。
      noteAiTradeMemberMessage: (instId, text) =>
        set((s) => {
          const epoch = currentAiTradeEpoch(s.aiTradeGroup, s.aiTradeResumable, s.aiTradeReconciledAtMs);
          return patchAiTradeMember(s.aiTradeGroup, instId, (m) =>
            m.session
              ? {
                  ...m,
                  session: { ...m.session, message: text },
                  ...(m.session.lifecycle != null ? { messageEpoch: epoch } : {}),
                }
              : m,
          );
        }),
      setAiTradeMemberSession: (instId, session) =>
        set((s) => patchAiTradeMember(s.aiTradeGroup, instId, (m) => ({ ...m, session }))),
      patchAiTradeMemberSession: (instId, patch) =>
        set((s) =>
          patchAiTradeMember(s.aiTradeGroup, instId, (m) =>
            m.session ? { ...m, session: { ...m.session, ...patch } } : m,
          ),
        ),
      // status 事件是冷却期的唯一落库点（§4.2 三载体之三）：冷却写在 **member 层**而非 session 层，
      // 因为面板的 chip 门控须是 `member.profitGuardCooldown != null`——挂在 position 之下则平仓后
      // 恒 null，chip 永远不出现；冷却结束后端会主动再推一次 status（该键为 null）以摘掉 chip。
      patchAiTradeMemberStatus: (instId, status) =>
        set((s) => {
          // 状态行纪元（§4.5）：**当且仅当**这一帧真的携带非空 `message` 时刷戳（缺席即不动，
          // 保住「浅合并不清空」的既有语义），且同样要求 `lifecycle` 键在场（理由见
          // `noteAiTradeMemberMessage`）。
          const row = status as Partial<AiTradeSessionRow>;
          const stamp = !!row.message && row.lifecycle !== undefined;
          const epoch = stamp
            ? currentAiTradeEpoch(s.aiTradeGroup, s.aiTradeResumable, s.aiTradeReconciledAtMs)
            : null;
          return patchAiTradeMember(s.aiTradeGroup, instId, (m) => ({
            ...m,
            profitGuardCooldown: status.profitGuardCooldown ?? null,
            ...(epoch ? { messageEpoch: epoch } : {}),
            session: m.session ? { ...m.session, ...row } : m.session,
          }));
        }),
      // 分组级验收三键的**窄** action（horizon-acceptance [P0-3]）：只覆盖这三个加法键，
      // **绝不碰 `members`**。用 `setAiTradeGroup(groupStateFromStatus(...))` 去做这件事会把每个
      // 成员的 `trades` 清空（那条映射写死 `trades: []`），而它跑在一个 15 秒轮询里——
      // 成交流会每 15 秒消失一次。分组不存在时静默空转（轮询与分组生命周期天然有竞态）。
      patchAiTradeGroupAcceptance: (patch) =>
        set((s) =>
          s.aiTradeGroup ? { aiTradeGroup: { ...s.aiTradeGroup, ...patch } } : {},
        ),
      // 生命周期对账的**窄** action（lifecycle-truth F-2 / [评审 R1-3]）：分组级那几键 +
      // 逐成员的六个生命周期键。为什么必须窄：`setAiTradeGroup(groupStateFromStatus(...))`
      // 会把每个成员的 `trades` 清空（那条映射写死 `trades: []`），而本 action 跑在一个 3~15 秒
      // 的轮询里 —— 成交流会周期性消失。这条与 `patchAiTradeGroupAcceptance` 是同一条教训。
      //
      // 成员行必须一起合并，否则一个已死成员的卡片会**永远**显示「运行中」：它的 WS 恰好在
      // `NOT_FOUND` 之后被停连了，再也没有任何事实会到达那张卡片。回执里缺席的成员**保留本地
      // 行不动，不删**（它的去向由终态留痕卡负责）。
      patchAiTradeGroupLifecycle: (patch) =>
        set((s) => {
          const group = s.aiTradeGroup;
          if (!group) return {};
          const members = { ...group.members };
          for (const row of patch.members) {
            const local = members[row.instId];
            if (!local?.session) continue;
            members[row.instId] = { ...local, session: { ...local.session, ...row.session } };
          }
          return { aiTradeGroup: { ...group, ...patch.group, members } };
        }),
      requestAiTradeReconcile: () =>
        set({ aiTradeReconcileUntil: Date.now() + AI_TRADE_RECONCILE_WINDOW_MS }),
      // `"ok"` 时顺手打一枚对账时戳，打的是那次对账**请求出发**的墙钟（§4.3 / [评审 RP1-3]）：
      // 取落地时刻是错的 —— 超时断面下后端本就慢，一次 `t-100ms` 出发、`t+2000ms` 才落地的对账
      // 会用一份**不含该动作结果**的快照当场结算掉「结果未知」。形参可选、缺席即 `Date.now()`；
      // `"unknown"` 分支**绝不打戳**：读不到 ≠ 已核实。
      setAiTradeSyncState: (aiTradeSyncState, startedAtMs) =>
        set(
          aiTradeSyncState === "ok"
            ? { aiTradeSyncState, aiTradeReconciledAtMs: startedAtMs ?? Date.now() }
            : { aiTradeSyncState },
        ),
      setAiTradeEnabled: (aiTradeEnabled) => set({ aiTradeEnabled }),
      setAiTradeMemberTrades: (instId, trades) =>
        set((s) =>
          patchAiTradeMember(s.aiTradeGroup, instId, (m) => ({
            ...m,
            trades: trades.slice(-TRADES_MAX),   // 成交流封顶（D4）
          })),
        ),
      appendAiTradeMemberTrade: (instId, trade) =>
        set((s) =>
          patchAiTradeMember(s.aiTradeGroup, instId, (m) =>
            m.trades.some(
              (t) => t.ts === trade.ts && t.side === trade.side && t.price === trade.price,
            )
              ? m
              : { ...m, trades: [...m.trades, trade].slice(-TRADES_MAX) },
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
      setAiTradeIntelSummary: (instId, summary) =>
        set((s) =>
          summary
            ? { aiTradeIntel: { ...s.aiTradeIntel, [instId]: summary } }
            : {},
        ),
      // 停止 / 结束后**同时**清冷占位：只清 aiTradeGroup 会在页面上留一张指向已归档会话的
      // 续跑卡，而那张卡点下去是一次真实资金的开仓授权。**情报切片一并清**：
      // 留着的话，下一次开另一个标的时旧的未读徽标会挂在一个已经不在跑的标的上——
      // 一个指向已归档会话的红点比没有红点更坏。
      clearAiTradeRuntime: () =>
        set({ aiTradeGroup: null, aiTradeResumable: null, aiTradeIntel: {} }),
      // ai-trade-loss-and-liveness-hardening A-2：**利润保护的开关不进偏好**（写侧剥离）。
      // 合入前它是一个粘性偏好：取消一次勾选 ⇒ 提交体 `{enabled:false}` ⇒ 存进 aiTradeConfig ⇒
      // 此后每一次启动都默认关闭、永不回弹、也不再提示。实测 **193 / 211** 条 run 的利润保护层
      // 未装配，其中 45 条同时选了承诺「硬止盈 50%」的纪律预设——用户看得见那个数，它却逐字节
      // 不作数。目标不是「不让用户关」，而是**不能一次关掉、永远关掉**。
      // 剥完之后弹窗读到的 `defaultProfitGuard.enabled` 是 undefined，
      // `AiTradeLaunchModal` 既有的 `?? AI_TRADE_PROFIT_GUARD_DEFAULTS.enabled` 兜底自然回弹为
      // true——**弹窗与 AiTradeToggle 一行都不用改**（读点本来就有兜底）。
      setAiTradeConfig: (aiTradeConfig) =>
        set({ aiTradeConfig: stripProfitGuardEnabled(aiTradeConfig) }),
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
      setAgentConversationId: (agentConversationId) => set((state) => ({
        agentConversationId,
        agentTradingBindingRevision: state.agentTradingBindingRevision + 1,
      })),
      // 新对话：生成新会话 uuid（旧消息流随 conversationId 变更而失效，DB 历史仍可经其原 id 查到）。
      newConversation: () => set((state) => ({
        agentConversationId: makeConversationId(),
        agentTradingBindingRevision: state.agentTradingBindingRevision + 1,
      })),
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
      // 交易日志过滤条件：整体替换（工具条每次都给出完整对象），避免部分合并造出半旧半新的组合。
      setJournalFilters: (journalFilters) => set({ journalFilters }),
      setFactorLabSection: (factorLabSection) => set({ factorLabSection }),
      setFactorOverlay: (factorOverlay) => set({ factorOverlay }),
      // 挖掘：分区持久化；运行期态瞬态。WS 事件一律经纯 reducer applyDiscoveryEvent 落态——
      // 它对 state 事件走**整对象合并**，故后端新增字段自动穿透，绝不逐键手抄（R-11）。
      setDiscoverySection: (discoverySection) => set({ discoverySection }),
      setDiscoveryRunId: (discoveryRunId) => set({ discoveryRunId }),
      setDiscoveryAssetId: (discoveryAssetId) => set({ discoveryAssetId }),
      setDiscoveryRun: (discoveryRun) => set({ discoveryRun }),
      applyDiscoveryWsEvent: (event) =>
        set((s) => ({ discoveryRun: applyDiscoveryEvent(s.discoveryRun, event) })),
      setDiscoveryConnected: (open) =>
        set((s) => (s.discoveryRun ? { discoveryRun: { ...s.discoveryRun, connected: open } } : {})),
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
      version: 32,
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
        // 唯一的例外（§6.1 / [评审 RP1-5]）：`ended` / `backend_ended` 这两条**真钱待办**必须跨
        // 刷新存活。设计上曾以为「刷新后由对账器重新产生」，取证是假的 ——
        // `reconcileAiTradeLifecycle` 在 `if (!local) return;` 早退，刷新后根本没有 `local`。
        // **必须按 `persist` 位过滤**，不许无条件持久化：其余五种恒写 null，否则就是把一句来历
        // 不明的旧话放到一个刚对过账的界面上 —— 本轮要消灭的形状本身。水合回来照样过谓词，
        // 一旦对账发现后端有新的在跑分组即按 `validIn` 判死。无需升 persist 版本（浅合并回落 null）。
        aiTradeNotice:
          s.aiTradeNotice && AI_TRADE_NOTICE_RULES[s.aiTradeNotice.kind]?.persist
            ? s.aiTradeNotice
            : null,
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
        // 交易日志过滤条件（持久化）；会话列表 / 健康度 / 三页数据全是运行期瞬态，**不**入 partialize。
        journalFilters: s.journalFilters,
        factorLabSection: s.factorLabSection,
        // 挖掘当前分区（持久化）；discoveryRun / discoveryRunId 为运行期瞬态，**不**入 partialize
        // （刷新由 WS 首发 state 快照重建，绝不落陈旧运行态）。
        discoverySection: s.discoverySection,
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
        if (version < 30) {
          // 策略与因子挖掘：补分区默认（老用户从未持久化过 discoverySection）；
          // discoveryRun / discoveryRunId 为瞬态（不入 partialize），浅合并回落即可。
          next = { ...next, discoverySection: "overview" };
        }
        if (version < 31) {
          // ai-trade-loss-and-liveness-hardening A-2 的**另一半**：把**已经落盘**的
          // `profitGuard.enabled` 剥掉。只做写侧剥离救不了它要救的那批人——193 条 run 的
          // `enabled:false` 此刻就躺在用户的 localStorage 里，升级后第一次打开弹窗读到的仍然是
          // 「关」。剥掉之后读点的 `?? AI_TRADE_PROFIT_GUARD_DEFAULTS.enabled` 自然回弹为 true。
          // **必须 null 守卫**（镜像 v28 / v29 的成文教训）：aiTradeConfig 的初始值与多个版本落点
          // 都是 null，对 null 取属性赋值会抛 TypeError 直接崩掉水合。
          // 与 v28 / v29 同款的显式收窄：`next` 是 persist 给的 `unknown` 形状。
          const cfg = next.aiTradeConfig as AiTradeConfig | null;
          next = { ...next, aiTradeConfig: stripProfitGuardEnabled(cfg) };
        }
        if (version < 32) {
          // 交易日志（ai-trade-audit-journal §3.4）：**只补新键默认值、不删不改任何既有键**。
          // 老用户从未持久化过 journalFilters，浅合并本就回落 `{}`；显式补一行是本仓既有范式
          // （与 v19 的 activeView、v30 的 discoverySection 同款），也让这次升版在 diff 里说得出
          // 自己改了什么。
          next = { ...next, journalFilters: {} };
        }
        return next as never;
      },
      // SSR / 水合安全（C2）：先以默认 state 渲染（与服务端 HTML 一致 → 无 hydration 告警），
      // 由根客户端组件挂载后显式 useAppStore.persist.rehydrate() 补水（接受"默认→恢复"一帧闪烁）。
      skipHydration: true,
    },
  ),
);
