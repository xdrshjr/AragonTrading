"use client";

import { useQuery } from "@tanstack/react-query";
import { type ReactNode, useEffect, useMemo, useState } from "react";
import { Badge, Button, Modal, Select } from "@/components/ui";
import { api } from "@/lib/api";
import {
  AI_BACKTEST_DEFAULT_DECISION_EVERY,
  AI_BACKTEST_MAX_ROUNDS,
  defaultDecisionEvery,
  planRounds,
  validateBacktestRange,
} from "@/lib/aiBacktest";
import {
  DEFAULT_DISCIPLINE_PRESET,
  DISCIPLINE_COPY,
  DISCIPLINE_PRESETS,
} from "@/lib/aiTradeDiscipline";
import {
  AI_TRADE_BAND_TOLERANCE_OPTIONS,
  AI_TRADE_BASE_RULE_DEFAULTS,
  AI_TRADE_DEFAULT_CONTEXT_ROUNDS,
  AI_TRADE_DEFAULT_POSITION_STEP,
  AI_TRADE_DEFAULT_REASONING_DEPTH,
  AI_TRADE_DEFAULT_WAKE_INTERVAL_MIN,
  AI_TRADE_GIVEBACK_TIER_PRESETS,
  AI_TRADE_MAX_SL_PCT,
  AI_TRADE_MAX_STRATEGIES,
  AI_TRADE_MAX_SYMBOLS,
  AI_TRADE_MAX_TP_PCT,
  AI_TRADE_POSITION_STEP_OPTIONS,
  AI_TRADE_PROFIT_GUARD_DEFAULTS,
  REASONING_DEPTH_HINTS,
  REASONING_DEPTH_LABELS,
  deriveDefaultBudget,
  marginReturnFromPricePct,
  positionLadder,
  priceMoveFromMarginPct,
  validateContextRounds,
  validateDirectionRule,
  validateProfitGuard,
  validateStrategySelection,
  validateTrendRule,
  validateWakeInterval,
} from "@/lib/aiTrade";
import { validateHorizon } from "@/lib/aiTradeHorizon";
import { aiTradeDisciplineApi, aiTradeHorizonApi } from "@/lib/aiTradeHorizonApi";
import {
  clampGroupBudgetToCap,
  DEFAULT_AUTO_TRADE_MAX_BUDGET_PCT,
  groupBudgetCap,
  isAutoTradable,
  isGroupBudgetClamped,
  memberBudget,
  totalAllocation,
} from "@/lib/autoTrade";
import { autoTradeSettingsApi } from "@/lib/autoTradeSettingsApi";
import { fmtPrice } from "@/lib/format";
import {
  clampPct,
  LIVE_AUTO_DEFAULT_LEVERAGE,
  LIVE_MAX_ORDER_NOTIONAL_USD,
  parseOptionalLeverage,
  parseOptionalPct,
  parseOptionalUsd,
} from "@/lib/liveAuto";
import type {
  AiTradeBackstopRepairMode,
  AiTradeBaseRules,
  AiTradeDirectionRule,
  AiTradeDisciplineIn,
  AiTradeDisciplinePresetsResponse,
  AiTradeGivebackTier,
  AiTradeHorizonProfile,
  AiTradeHorizonProfileId,
  AiTradeHorizonRule,
  AiTradeMaBandRule,
  AiTradeProfitGuardRule,
  AiTradeReasoningDepth,
  AiTradeTrendRule,
} from "@/lib/aiTradeTypes";
import type { AutoTradeAllocationMode, AutoTradeMember } from "@/lib/liveAutoTypes";
import { tradingApi } from "@/lib/tradingApi";
import type { Bar } from "@/lib/types";
import { AI_TRADE_EXPERIENCE_DEFAULTS } from "@/lib/experience";
import type { AiTradeExperienceRule } from "@/lib/experienceTypes";
import AiTradeDirectionFields from "./AiTradeDirectionFields";
import AiTradeExperienceFields from "./AiTradeExperienceFields";
import AiTradeHorizonFields, { type HorizonCustomKey } from "./AiTradeHorizonFields";
import AiTradeTradabilityFields, {
  TRADABILITY_CONFIRM_REQUIRED_TEXT,
} from "./AiTradeTradabilityFields";
import ProfitGuardFields from "./ProfitGuardFields";

export type AiTradeLaunchMode = "live" | "backtest";

// 时间级别候选（镜像 ChartToolbar 的 BARS；弹窗「时间级别」下拉可编辑）。
const BARS: Bar[] = ["1m", "5m", "15m", "30m", "1H", "4H", "1D"];

export interface AiTradeLaunchOpts {
  mode: AiTradeLaunchMode;
  members: { instId: string; allocationPct: number }[]; // 实盘多标的（回测取 members[0] 兜底）
  allocationMode: AutoTradeAllocationMode;               // even | max_ratio
  instId: string;                                        // 回测单标的（members[0] 兜底）
  bar: string;                                           // 可编辑周期（取代对 store.bar 的直读）
  strategyIds: number[];
  capitalUsd: number | null;       // live=分组总预算；backtest=初始资金（留空＝服务端默认）
  leverage: number | null;
  takeProfitUsd: number | null;
  stopLossUsd: number | null;
  stopLossPct: number | null;      // E4：单仓 OCO 止损%（fraction，留空＝config 默认）
  takeProfitPct: number | null;    // E4：单仓 OCO 止盈%
  positionStepPct: number;         // 仓位操作档位步长（fraction；AI 每次开仓规模只能落在该档位阶梯上）
  // 【基础策略】铁律（实盘/回测两 tab 同得，实盘与回测的默认必须一致，P1-4）：勾选 ⇒ 带完整参数；
  // 取消勾选 ⇒ `{maBandEntry:{enabled:false}}`（**显式关闭**，与「字段缺省=默认开启」语义严格区分）。
  baseRules: AiTradeBaseRules;
  // 利润保护层（**实盘与回测同源**，adaptive §7.2a）：回测改用 bar 内 OHLC 极值 + 保守成交序后，
  // 该字段两模式都提交。勾选 ⇒ 带完整参数；取消勾选 ⇒ `{enabled:false}`（显式关闭，与「字段缺省=默认开启」区分）。
  profitGuard?: AiTradeProfitGuardRule;
  // 交易周期模式（**实盘与回测同源**，holding-horizon §4.3）：选中模式 ⇒ 带 `{enabled,profile,…}`；
  // 取消勾选 ⇒ `{enabled:false}`（**显式关闭**，与「字段缺省=默认开启」语义严格区分）。
  tradingHorizon?: AiTradeHorizonRule;
  // 经验模式（experience-context-graph §7.2）：勾选 ⇒ 带 `{enabled,scope,write}`；取消勾选 ⇒
  // `{enabled:false}`（**显式关闭**，与「字段缺省=默认开启」语义严格区分——后者是老客户端
  // 没发这个字段，前者是用户亲手取消了勾选，合成一个判据用户就再也关不掉这个功能）。
  experience?: AiTradeExperienceRule;
  // 交易纪律预设（**实盘与回测同源**，discipline-preset R-3）：只带 `{preset}`，阈值一律由服务端
  // 的冻结表决定。整键不发 ⇒ 服务端按 `ai_trade_discipline_default_preset` 装配（老客户端同样受保护）。
  discipline?: AiTradeDisciplineIn;
  // 方向对称性 / 逆势通路（**实盘与回测同源**，directional-symmetry §7.1）：勾选 ⇒ 带
  // `{contrarianEnabled:true, ...留空即服务端默认}`；取消勾选 ⇒ `{contrarianEnabled:false}`
  // （**显式关闭**，与「字段缺省=默认开启」语义严格区分）。
  direction?: AiTradeDirectionRule;
  // 执行周期趋势基调（**实盘与回测同源**，execution-trend-verdict §5.1）：勾选 ⇒ 带
  // `{enabled,counterGateEnabled,…}`；取消勾选 ⇒ `{enabled:false}`（**显式关闭**，与
  // 「字段缺省=默认开启」语义严格区分）。两侧走同一个 `_resolve_trend`（评审 RP1-10）。
  trend?: AiTradeTrendRule;
  contextRefreshRounds: number;
  // Agent 墙钟定时唤醒周期（分钟）：[RN-1] 声明为**可选**（对齐 backtest-only 可选字段），仅 live 分支透传、
  // base 不含、回测省略——若定为非可选，回测 onConfirm 不带该键将 tsc 编译失败。0/关闭 = 仅按 K 线与策略触发。
  wakeIntervalMinutes?: number | null;
  // 研判深度档（deep-reasoning-chain §3.5.3 / P2-2）：与 `wakeIntervalMinutes` 同款——**仅 live
  // 分支透传、base 不含**。落进 `base` 会让用户在回测 tab 选「深度」直接推翻 §3.6 那道成本护栏，
  // 而那正是它存在的理由。回测档只读展示，改档走设置页。
  reasoningDepth?: AiTradeReasoningDepth | null;
  // 回测专属（mode==="backtest" 时有效）
  startTs?: number;
  endTs?: number;
  decisionEveryBars?: number | null;   // UI 已解析为智能默认，永不因越界自拒（R5）
  minConsensus?: number | null;
}

const DAY = 86_400;

// 服务端单仓 OCO 默认值（与后端 ai_trade_default_sl_pct=0.05 / ai_trade_default_tp_pct=0.10 对齐）。
// 本轮**不改这两个数值**（它们是进程崩溃 / 断网时唯一还生效的交易所侧兜底，静默收紧会让存量会话止损
// 前移），只做「显性化 + 可记忆」——把「留空＝默认」这条隐藏语义写成用户看得见的具体数字。
const AI_TRADE_DEFAULT_OCO_SL_PCT = 0.05;
const AI_TRADE_DEFAULT_OCO_TP_PCT = 0.1;

/**
 * 启动 AI 自动交易弹窗（ai-trade-multi-symbol-and-timeframe §4.2/§4.3）：顶部「实盘 / 回测」段控。
 * 实盘段：**多标的选择器**（默认回填左侧「自动交易」面板的 autoTradeMembers，至多 AI_TRADE_MAX_SYMBOLS 个、
 * 超出提示并禁增；每成员 allocation% + 移除 + 均分 + even/max_ratio 段控）+ **可编辑时间级别** + 多选策略 +
 * 预算/杠杆/OCO/总止盈损 + 上下文轮数 + 真实资金勾选。回测段：仍单标的（默认 members[0]），同享可编辑周期。
 */
// ★ A-3：关掉利润保护后的二次确认提示（与既有 `TRADABILITY_CONFIRM_REQUIRED_TEXT` 同款位置）。
const UNGUARDED_CONFIRM_REQUIRED_TEXT =
  "已关闭利润保护：请先阅读上方的后果清单并勾选确认，再启动。";

export default function AiTradeLaunchModal({
  open,
  onClose,
  onConfirm,
  submitting,
  acknowledged,
  onAcknowledge,
  bar: propBar,
  defaultMembers,
  defaultAllocationMode,
  defaultInstId,
  defaultStrategyIds,
  defaultContextRounds,
  defaultPositionStepPct,
  defaultWakeIntervalMinutes,
  defaultReasoningDepth,
  defaultBaseRule,
  defaultProfitGuard,
  defaultTradingHorizon,
  defaultExperience,
  defaultStopLossPct,
  defaultTakeProfitPct,
  error,
  errorRemedies = null,
  liveGateReason = null,
  defaultStartTs,
  defaultEndTs,
  defaultDecisionEveryBars,
}: {
  open: boolean;
  onClose: () => void;
  onConfirm: (opts: AiTradeLaunchOpts) => void;
  submitting: boolean;
  acknowledged: boolean;
  onAcknowledge: (value: boolean) => void;
  bar: string;                        // K 线所选周期（回填「时间级别」下拉默认值）
  defaultMembers: AutoTradeMember[];  // 来自 autoTradeMembers（可 > 上限，弹窗内截断并提示，P1-2）
  defaultAllocationMode: AutoTradeAllocationMode;   // 来自 store.autoTradeAllocationMode（P1-2）
  defaultInstId: string;              // 当前图表标的（autoTradeMembers 空时回退单成员）
  defaultStrategyIds: number[];
  defaultContextRounds: number;
  defaultPositionStepPct: number;     // 上次所选仓位操作档位（aiTradeConfig?.positionStepPct ?? 默认，回填弹窗）
  defaultWakeIntervalMinutes: number; // 上次所选 Agent 唤醒周期（aiTradeConfig?.wakeIntervalMinutes ?? 默认，回填弹窗）
  // 上次所选研判深度档（可选：老 localStorage 无此键 ⇒ 回落 AI_TRADE_DEFAULT_REASONING_DEPTH）。
  defaultReasoningDepth?: AiTradeReasoningDepth;
  defaultBaseRule: AiTradeMaBandRule; // 上次所选【基础策略】参数（aiTradeConfig?.baseRules ?? 默认，回填弹窗）
  defaultProfitGuard: AiTradeProfitGuardRule; // 上次所选利润保护参数（aiTradeConfig?.profitGuard ?? 默认）
  // 上次所选交易周期模式（aiTradeConfig?.tradingHorizon）；**无本地预设兜底**——模式表由服务端下发，
  // 未选过时留 undefined 并采用接口返回的 defaultProfile（前端不复制任何一个预设数值，R-3）。
  defaultTradingHorizon?: AiTradeHorizonRule;
  defaultExperience?: AiTradeExperienceRule;   // 上次所选经验模式（缺省 ⇒ 本地默认全开）
  // 上次填过的单仓 OCO（诊断根因 ③：每次都被清空 ⇒ 用户不知不觉一直用 10%/5% 的宽默认）。
  defaultStopLossPct?: number;
  defaultTakeProfitPct?: number;
  error?: string | null;
  // ai-trade-loss-and-liveness-hardening A-1：422 `AI_TRADE_UNGUARDED_HORIZON` 随错误体带回的
  // **可执行补救**。文案由后端下发、前端只负责把它渲染成按钮并把对应的开关真的拨过去。
  errorRemedies?: { id: string; label: string }[] | null;
  liveGateReason?: string | null;     // 实盘不可用原因（非 null → 段控禁用实盘、默认落回测，R6）
  defaultStartTs?: number;
  defaultEndTs?: number;
  defaultDecisionEveryBars?: number;
}) {
  const liveAvailable = liveGateReason == null;
  const [mode, setMode] = useState<AiTradeLaunchMode>(liveAvailable ? "live" : "backtest");
  const [members, setMembers] = useState<AutoTradeMember[]>([]);
  const [allocationMode, setAllocationMode] = useState<AutoTradeAllocationMode>(defaultAllocationMode);
  const [bar, setBar] = useState<Bar>(propBar as Bar);
  const [instId, setInstId] = useState(defaultInstId);   // 回测单标的
  const [addSymbolRaw, setAddSymbolRaw] = useState("");
  const [addError, setAddError] = useState<string | null>(null);
  const [experience, setExperience] = useState<AiTradeExperienceRule>(
    defaultExperience ?? AI_TRADE_EXPERIENCE_DEFAULTS);
  const [selectedIds, setSelectedIds] = useState<number[]>(defaultStrategyIds);
  const [capitalRaw, setCapitalRaw] = useState("");
  const [leverageRaw, setLeverageRaw] = useState("");
  const [takeProfitRaw, setTakeProfitRaw] = useState("");
  const [stopLossRaw, setStopLossRaw] = useState("");
  const [stopLossPctRaw, setStopLossPctRaw] = useState(pctToRaw(defaultStopLossPct));
  const [takeProfitPctRaw, setTakeProfitPctRaw] = useState(pctToRaw(defaultTakeProfitPct));
  const [roundsRaw, setRoundsRaw] = useState(String(defaultContextRounds || AI_TRADE_DEFAULT_CONTEXT_ROUNDS));
  const [positionStep, setPositionStep] = useState(defaultPositionStepPct || AI_TRADE_DEFAULT_POSITION_STEP);
  // Agent 墙钟定时唤醒周期（分钟，仅实盘段）：初值取 defaultWakeIntervalMinutes（用 ?? 保留 0=关闭偏好）。
  const [wakeIntervalRaw, setWakeIntervalRaw] = useState(
    String(defaultWakeIntervalMinutes ?? AI_TRADE_DEFAULT_WAKE_INTERVAL_MIN),
  );
  // 研判深度档（仅实盘段）：初值取上次所选，缺省回落默认档。
  const [reasoningDepth, setReasoningDepth] = useState<AiTradeReasoningDepth>(
    defaultReasoningDepth ?? (AI_TRADE_DEFAULT_REASONING_DEPTH as AiTradeReasoningDepth),
  );
  // 【基础策略】铁律（默认开启；数值走 raw 串以免输入中途被 Number 归一，与既有金额输入同风格）。
  const [baseRuleEnabled, setBaseRuleEnabled] = useState(
    defaultBaseRule.enabled ?? AI_TRADE_BASE_RULE_DEFAULTS.enabled,
  );
  const [baseRulePeriodRaw, setBaseRulePeriodRaw] = useState(
    String(defaultBaseRule.period ?? AI_TRADE_BASE_RULE_DEFAULTS.period),
  );
  const [baseRuleStdRaw, setBaseRuleStdRaw] = useState(
    String(defaultBaseRule.stdMult ?? AI_TRADE_BASE_RULE_DEFAULTS.stdMult),
  );
  const [baseRuleTolerance, setBaseRuleTolerance] = useState(
    defaultBaseRule.tolerance ?? AI_TRADE_BASE_RULE_DEFAULTS.tolerance,
  );
  const [baseRuleDirectional, setBaseRuleDirectional] = useState(
    defaultBaseRule.directional ?? AI_TRADE_BASE_RULE_DEFAULTS.directional,
  );
  // 交易纪律预设（discipline-preset 轴 A）：**只存一个 id**，阈值一概不在前端保存或重算——
  // 生效值恒以启动回执 `discipline` 为准（前后端各存一份口径必漂移，`edgeSource` 的成文教训）。
  const [disciplinePreset, setDisciplinePreset] = useState(DEFAULT_DISCIPLINE_PRESET);
  // 方向对称性 / 逆势通路（directional-symmetry D-5）：默认开启（与服务端「字段缺省即开启」的
  // 语义一致）；三个数值走 raw 串、**留空即服务端默认**——前端不存任何默认数值（唯一真源在
  // `config.py`，前端存一份下次调参就会静默分叉）。
  const [directionEnabled, setDirectionEnabled] = useState(true);
  const [directionConfidenceRaw, setDirectionConfidenceRaw] = useState("");
  const [directionQuotaRaw, setDirectionQuotaRaw] = useState("");
  const [directionSizeMultRaw, setDirectionSizeMultRaw] = useState("");
  // 执行周期趋势基调（execution-trend-verdict §5.1 / 评审 RP1-12）：四个受控状态**必须与上面
  // 那批并列住在父组件**，且它们的重置一律走下面那个依赖收敛为 `[open]` 的既有 effect——
  // 另写第二个 reset effect 正是 ai-trade-modal-revert-recurrence 的复发根因（输入约 1 秒
  // 后被冲回默认）。默认全开，与服务端「字段缺省即开启」的语义一致。
  const [trendEnabled, setTrendEnabled] = useState(true);
  const [trendCounterGate, setTrendCounterGate] = useState(true);
  const [trendVoteEnabled, setTrendVoteEnabled] = useState(true);
  const [trendAllowanceEnabled, setTrendAllowanceEnabled] = useState(true);
  const [trendConfidenceRaw, setTrendConfidenceRaw] = useState("");
  const [trendSizeMultRaw, setTrendSizeMultRaw] = useState("");
  // 可交易性预检的二次确认态（轴 A）：受控 state 留在父组件，子组件内不写任何 reset effect。
  const [tradabilityConfirmed, setTradabilityConfirmed] = useState(false);
  // 是否**要求**那次确认（= 预检判定 infeasible）。由子组件按 level 上报，**刻意不进下面那个
  // `[open]` 重置 effect**：它不是用户输入而是探针的派生事实，重置它反而会在「关掉又打开、参数
  // 没变故 level 没变」时让子组件不再上报 ⇒ 拦截静默失效。用户输入 `tradabilityConfirmed` 才重置。
  const [tradabilityNeedsConfirm, setTradabilityNeedsConfirm] = useState(false);
  // ★ A-3：关掉利润保护后的**二次确认**（镜像上面 `tradabilityConfirmed` 的既有范式）。
  // 用户可以关它，但必须在看过后果清单之后再按一次——这是「让致命组合难以被无意中选中」
  // 与「不剥夺用户的选择权」之间唯一诚实的落点。
  const [unguardedConfirmed, setUnguardedConfirmed] = useState(false);
  // 利润保护层（默认开启）：四个数值输入一律走**百分数**（用户口径），提交时统一 /100 转 fraction。
  const [profitGuardEnabled, setProfitGuardEnabled] = useState(
    defaultProfitGuard.enabled ?? AI_TRADE_PROFIT_GUARD_DEFAULTS.enabled,
  );
  const [armReturnRaw, setArmReturnRaw] = useState(pctToRaw100(defaultProfitGuard.armReturnPct,
    AI_TRADE_PROFIT_GUARD_DEFAULTS.armReturnPct));
  const [givebackRaw, setGivebackRaw] = useState(pctToRaw100(defaultProfitGuard.givebackPct,
    AI_TRADE_PROFIT_GUARD_DEFAULTS.givebackPct));
  const [breakevenArmRaw, setBreakevenArmRaw] = useState(pctToRaw100(
    defaultProfitGuard.breakevenArmReturnPct, AI_TRADE_PROFIT_GUARD_DEFAULTS.breakevenArmReturnPct));
  const [minHoldRaw, setMinHoldRaw] = useState(String(
    defaultProfitGuard.minHoldSeconds ?? AI_TRADE_PROFIT_GUARD_DEFAULTS.minHoldSeconds));
  // 自适应四项（本轮新增）：阶梯走预设下拉（阶梯是结构化数组，逐档手填对用户毫无收益）；
  // ATR 上下界与冷却秒同走 raw 串，避免输入中途被 Number 归一（与既有金额输入同风格）。
  const [tierPreset, setTierPreset] = useState(() => matchTierPreset(defaultProfitGuard.givebackTiers));
  const [atrFloorRaw, setAtrFloorRaw] = useState(String(
    defaultProfitGuard.atrFloorMult ?? AI_TRADE_PROFIT_GUARD_DEFAULTS.atrFloorMult));
  const [atrCapRaw, setAtrCapRaw] = useState(String(
    defaultProfitGuard.atrCapMult ?? AI_TRADE_PROFIT_GUARD_DEFAULTS.atrCapMult));
  const [cooldownRaw, setCooldownRaw] = useState(String(
    defaultProfitGuard.cooldownSeconds ?? AI_TRADE_PROFIT_GUARD_DEFAULTS.cooldownSeconds));
  // 交易周期模式（holding-horizon §2.11）：`profileRaw = null` 表示「本次尚未主动选过」⇒ 采用接口
  // 下发的 defaultProfile（绝不在前端硬编码一个默认模式 id 去和服务端各存一份）。
  const [horizonEnabled, setHorizonEnabled] = useState(defaultTradingHorizon?.enabled ?? true);
  const [horizonProfileRaw, setHorizonProfileRaw] = useState<AiTradeHorizonProfileId | null>(
    defaultTradingHorizon?.profile ?? null,
  );
  const [horizonCustom, setHorizonCustom] = useState(() => seedHorizonCustom(defaultTradingHorizon));
  // 交易所侧兜底自愈模式（backstop-repair §2.4.1）。`null` = 本次尚未主动选过 ⇒ 采用接口下发的
  // `defaultMode`（同 `horizonProfileRaw` 的既有姿态：绝不在前端硬编一个默认值与服务端各存一份）。
  // **[P2-6] 刻意不进偏好回填链**（`defaultTradingHorizon`）：`arm` 是「允许系统在你不在场时下单」
  // 的授权，每次重新授权比记住上次更安全——这是一次有意的选择，不是遗漏。
  const [repairMode, setRepairMode] = useState<AiTradeBackstopRepairMode | null>(null);
  const [startInput, setStartInput] = useState("");
  const [endInput, setEndInput] = useState("");
  const [everyRaw, setEveryRaw] = useState("");
  const [minConsensusRaw, setMinConsensusRaw] = useState("");

  // 回填种子：autoTradeMembers 非空则取前 AI_TRADE_MAX_SYMBOLS 个；否则回退当前图表标的单成员 100%（P1-2）。
  const seededMembers = useMemo<AutoTradeMember[]>(() => {
    const src = defaultMembers.length > 0 ? defaultMembers : [{ instId: defaultInstId, allocationPct: 100 }];
    return src.slice(0, AI_TRADE_MAX_SYMBOLS).map((m) => ({ ...m }));
  }, [defaultMembers, defaultInstId]);
  const truncated = defaultMembers.length > AI_TRADE_MAX_SYMBOLS;
  const atCap = members.length >= AI_TRADE_MAX_SYMBOLS;

  const strategiesQ = useQuery({ queryKey: ["strategies"], queryFn: api.strategies });
  // 账户级总预算上限百分比（设置中心可调，默认 100%）：与非 AI 多标的弹窗同源，让「改总预算即联动每标的分配」。
  // 仅实盘用总预算天花板；回测走 initialCapital，故 enabled 收敛到 live 段。
  const settingsQ = useQuery({
    queryKey: ["settings", "auto-trade"],
    queryFn: autoTradeSettingsApi.get,
    enabled: open && mode === "live",
  });
  const maxBudgetPct = settingsQ.data?.effectiveMaxBudgetPct ?? DEFAULT_AUTO_TRADE_MAX_BUDGET_PCT;
  const budgetInst = mode === "live" ? (members[0]?.instId ?? "") : instId;
  const configQ = useQuery({
    queryKey: ["trading", "config", budgetInst, null],
    queryFn: () => tradingApi.config(budgetInst),
    enabled: open && !!budgetInst && mode === "live",
  });
  const availUsdt = configQ.data?.availUsdt ?? null;
  // 模式预设表（只读、不守 AI 总闸）：**按 bar 取**——`estimatedCostBps` 的资金费期数随执行周期变，
  // 契约摘要第三行的「需 ≥N bps」必须与门禁真正判定的数同源（实盘 / 回测两段都要，故不收敛 mode）。
  const horizonQ = useQuery({
    queryKey: ["aiTrade", "horizonProfiles", bar],
    queryFn: () => aiTradeHorizonApi.profiles(bar),
    enabled: open,
  });
  // 纪律预设的只读阈值表（与周期预设表同款姿态：**只读、不守总闸、数值与标签全由后端下发**）。
  // 与 `bar` 无关，故 queryKey 不带它。取不到 ⇒ 表格整段不渲染，四张卡与提交路径照常可用。
  const disciplineQ = useQuery({
    queryKey: ["aiTrade", "disciplinePresets"],
    queryFn: aiTradeDisciplineApi.presets,
    enabled: open,
  });

  // ★ A-3（loss-and-liveness-hardening）：当前纪律预设里的「利润保护·硬止盈」那一行。
  // **整句取自后端下发的只读阈值表**（`label` + 已格式化的 `value`），前端不复制任何一个数字
  // ——这张表下次调参时若与真正生效的那组数分叉，比不给更坏（`horizon-profiles` 的成文纪律）。
  // 取不到表 / 该预设不含这一行（如 `aggressive` 的硬止盈就是 0）⇒ null ⇒ 后果清单不渲染那一条。
  // ★ A-4：本次启动中会失效的阈值键。判据与后端 `discipline.inert_thresholds()` **逐字同构**
  // （`profit_rule is None` ⇒ 硬止盈失效），两侧同值由 `G-A6` 钉死。启动之前服务端还没被问过，
  // 故这里现算一次；启动之后权威值随 `group_status().discipline.inert` 下发。
  const inertThresholdKeys = profitGuardEnabled ? [] : ["ai_trade_profit_guard_hard_tp_return_pct"];

  const hardTakeProfitText = (() => {
    const rows = disciplineQ.data?.presets.find((p) => p.id === disciplinePreset)?.rows;
    const row = rows?.find((r) => r.key === "ai_trade_profit_guard_hard_tp_return_pct");
    return row && !row.off ? `${row.label} ${row.value}` : null;
  })();

  // 弹窗每次打开：回落默认段 / 成员 / 分配模式 / 周期 / 策略 / 轮数 / 区间并清空金额输入。
  useEffect(() => {
    if (!open) return;
    setMode(liveAvailable ? "live" : "backtest");
    setMembers(seededMembers);
    setAllocationMode(defaultAllocationMode);
    setBar(propBar as Bar);
    setInstId(seededMembers[0]?.instId ?? defaultInstId);
    setAddSymbolRaw("");
    setAddError(null);
    setSelectedIds(defaultStrategyIds);
    setRoundsRaw(String(defaultContextRounds || AI_TRADE_DEFAULT_CONTEXT_ROUNDS));
    setPositionStep(defaultPositionStepPct || AI_TRADE_DEFAULT_POSITION_STEP);
    setWakeIntervalRaw(String(defaultWakeIntervalMinutes ?? AI_TRADE_DEFAULT_WAKE_INTERVAL_MIN));
    setReasoningDepth(defaultReasoningDepth ?? (AI_TRADE_DEFAULT_REASONING_DEPTH as AiTradeReasoningDepth));
    setBaseRuleEnabled(defaultBaseRule.enabled ?? AI_TRADE_BASE_RULE_DEFAULTS.enabled);
    setBaseRulePeriodRaw(String(defaultBaseRule.period ?? AI_TRADE_BASE_RULE_DEFAULTS.period));
    setBaseRuleStdRaw(String(defaultBaseRule.stdMult ?? AI_TRADE_BASE_RULE_DEFAULTS.stdMult));
    setBaseRuleTolerance(defaultBaseRule.tolerance ?? AI_TRADE_BASE_RULE_DEFAULTS.tolerance);
    setBaseRuleDirectional(defaultBaseRule.directional ?? AI_TRADE_BASE_RULE_DEFAULTS.directional);
    // 趋势基调的六个受控输入**必须塞进这个既有 [open] effect**（见下方注释：另写 effect =
    // ai-trade-modal-revert-recurrence 复发）。默认全开 + 两个数值留空 = 服务端默认。
    setTrendEnabled(true);
    setTrendCounterGate(true);
    setTrendVoteEnabled(true);
    setTrendAllowanceEnabled(true);
    setTrendConfidenceRaw("");
    setTrendSizeMultRaw("");
    setProfitGuardEnabled(defaultProfitGuard.enabled ?? AI_TRADE_PROFIT_GUARD_DEFAULTS.enabled);
    // ★ A-3 的二次确认**必须塞进这个既有 [open] effect**（绝不另写第二个 reset effect：
    // 非稳定依赖的第二个 effect 正是 ai-trade-modal-revert-recurrence 的复发根因）。
    setUnguardedConfirmed(false);
    setArmReturnRaw(pctToRaw100(defaultProfitGuard.armReturnPct,
      AI_TRADE_PROFIT_GUARD_DEFAULTS.armReturnPct));
    setGivebackRaw(pctToRaw100(defaultProfitGuard.givebackPct,
      AI_TRADE_PROFIT_GUARD_DEFAULTS.givebackPct));
    setBreakevenArmRaw(pctToRaw100(defaultProfitGuard.breakevenArmReturnPct,
      AI_TRADE_PROFIT_GUARD_DEFAULTS.breakevenArmReturnPct));
    setMinHoldRaw(String(defaultProfitGuard.minHoldSeconds
      ?? AI_TRADE_PROFIT_GUARD_DEFAULTS.minHoldSeconds));
    // 本轮新增的四个受控输入**必须塞进这个既有 [open] effect**（绝不另写 effect：非稳定依赖的第二个
    // reset effect 正是 ai-trade-modal-revert-recurrence 的复发根因——输入约 1 秒后被冲回默认）。
    setTierPreset(matchTierPreset(defaultProfitGuard.givebackTiers));
    setAtrFloorRaw(String(defaultProfitGuard.atrFloorMult
      ?? AI_TRADE_PROFIT_GUARD_DEFAULTS.atrFloorMult));
    setAtrCapRaw(String(defaultProfitGuard.atrCapMult
      ?? AI_TRADE_PROFIT_GUARD_DEFAULTS.atrCapMult));
    setCooldownRaw(String(defaultProfitGuard.cooldownSeconds
      ?? AI_TRADE_PROFIT_GUARD_DEFAULTS.cooldownSeconds));
    // 交易周期模式的三个新状态同样**必须塞进这个既有 [open] effect**（见上一段注释：另写 effect =
    // ai-trade-modal-revert-recurrence 复发）。
    setHorizonEnabled(defaultTradingHorizon?.enabled ?? true);
    setHorizonProfileRaw(defaultTradingHorizon?.profile ?? null);
    setHorizonCustom(seedHorizonCustom(defaultTradingHorizon));
    setRepairMode(null);   // 同上：新增受控输入**必须**塞进这个既有 [open] effect（U-21 守它）
    setDisciplinePreset(DEFAULT_DISCIPLINE_PRESET);   // 同上（discipline-preset 轴 A 的受控输入）
    // 方向对称性的四个受控输入**必须塞进这个既有 [open] effect**（见上一段注释：另写 effect =
    // ai-trade-modal-revert-recurrence 复发，输入约 1 秒后被冲回默认）。
    setDirectionEnabled(true);
    setDirectionConfidenceRaw("");
    setDirectionQuotaRaw("");
    setDirectionSizeMultRaw("");
    setTradabilityConfirmed(false);   // 同上（tradability 轴 A：**必须**塞进这个既有 [open] effect）
    setCapitalRaw("");
    setLeverageRaw("");
    setTakeProfitRaw("");
    setStopLossRaw("");
    // 诊断根因 ③：这两项过去恒被清空，用户便在不知情下一直吃 10%/5% 的宽默认；改为回填上次偏好。
    setStopLossPctRaw(pctToRaw(defaultStopLossPct));
    setTakeProfitPctRaw(pctToRaw(defaultTakeProfitPct));
    const nowSec = Math.floor(Date.now() / 1000);
    setStartInput(toLocalInput(defaultStartTs ?? nowSec - 30 * DAY));
    setEndInput(toLocalInput(defaultEndTs ?? nowSec));
    setEveryRaw(defaultDecisionEveryBars ? String(defaultDecisionEveryBars) : "");
    setMinConsensusRaw("");
    // 依赖收敛为 [open]：seed 只在弹窗 open false→true 的那一次转换执行，避免父树重渲染/非稳定
    // 默认引用（seededMembers、defaultStrategyIds 等）把正在编辑的表单每秒冲回默认（inputs-revert 复发根因）。
  }, [open]); // eslint-disable-line react-hooks/exhaustive-deps

  const toggleId = (id: number) =>
    setSelectedIds((prev) => (prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id]));

  // ---------- 成员编辑（实盘多标的）----------
  const updateAllocation = (target: string, raw: string) => {
    const n = Number(raw);
    setMembers((prev) =>
      prev.map((m) => (m.instId === target ? { ...m, allocationPct: Number.isFinite(n) ? n : 0 } : m)),
    );
  };
  const removeMember = (target: string) =>
    setMembers((prev) => prev.filter((m) => m.instId !== target));
  const applyEvenSplit = () =>
    setMembers((prev) => {
      if (prev.length === 0) return prev;
      const each = Math.floor((100 / prev.length) * 100) / 100;
      return prev.map((m) => ({ ...m, allocationPct: each }));
    });
  const handleAddSymbol = () => {
    const sym = addSymbolRaw.trim().toUpperCase();
    if (!sym || atCap) return;
    if (!isAutoTradable(sym)) {
      setAddError("仅支持 USDT 本位永续 / 交割合约自动交易");
      return;
    }
    if (members.some((m) => m.instId === sym)) {
      setAddError("标的已在列表中");
      return;
    }
    setAddError(null);
    const remaining = Math.max(1, Math.round(100 - totalAllocation(members)));
    const pct = allocationMode === "even" ? Math.min(100, remaining) : 100;
    setMembers((prev) => [...prev, { instId: sym, allocationPct: pct }]);
    setAddSymbolRaw("");
  };

  const capital = parseOptionalUsd(capitalRaw);
  const leverage = parseOptionalLeverage(leverageRaw);
  const takeProfit = parseOptionalUsd(takeProfitRaw);
  const stopLoss = parseOptionalUsd(stopLossRaw);
  const stopLossPct = parseOptionalPct(stopLossPctRaw);
  const takeProfitPct = parseOptionalPct(takeProfitPctRaw);
  const roundsValue = Number.parseInt(roundsRaw, 10);
  const inputError =
    capital.error || leverage.error || takeProfit.error || stopLoss.error ||
    stopLossPct.error || takeProfitPct.error || null;
  const strategyError = validateStrategySelection(selectedIds);
  const roundsError = validateContextRounds(roundsValue);
  // Agent 唤醒周期（仅实盘生效）：空→null（关闭）、否则解析为数；越界即时报错（后端另做防御性夹紧，RN-9）。
  const wakeMinutes = wakeIntervalRaw.trim() === "" ? null : Number(wakeIntervalRaw);
  const wakeError = validateWakeInterval(wakeMinutes);

  // 【基础策略】参数（两模式共用；关闭勾选时不校验——关闭态提交体只含 enabled:false）。
  const baseRulePeriod = Number.parseInt(baseRulePeriodRaw, 10);
  const baseRuleStd = Number(baseRuleStdRaw);
  const baseRuleError = !baseRuleEnabled
    ? null
    : !Number.isInteger(baseRulePeriod) || baseRulePeriod < 2 || baseRulePeriod > 200
      ? "基础策略均线周期需为 2~200 的整数"
      : !Number.isFinite(baseRuleStd) || baseRuleStd <= 0 || baseRuleStd > 5
        ? "基础策略标准差倍数需在 (0, 5] 之间"
        : null;
  const baseRulesPayload: AiTradeBaseRules = {
    maBandEntry: baseRuleEnabled
      ? {
          enabled: true,
          period: baseRulePeriod,
          stdMult: baseRuleStd,
          tolerance: baseRuleTolerance,
          directional: baseRuleDirectional,
        }
      : { enabled: false },
  };

  // 利润保护参数（**实盘与回测同源**，adaptive §7.2a I-1/I-2）：UI 收百分数，此处统一 /100 转 fraction；
  // ATR 倍数与冷却秒是原始量纲，不做换算。
  const profitGuardPayload: AiTradeProfitGuardRule = profitGuardEnabled
    ? {
        enabled: true,
        armReturnPct: rawPctToFraction(armReturnRaw),
        givebackPct: rawPctToFraction(givebackRaw),
        breakevenArmReturnPct: rawPctToFraction(breakevenArmRaw),
        minHoldSeconds: Number(minHoldRaw),
        givebackTiers: tiersForPreset(tierPreset),
        atrFloorMult: Number(atrFloorRaw),
        atrCapMult: Number(atrCapRaw),
        cooldownSeconds: Number(cooldownRaw),
      }
    : { enabled: false };
  // 两模式都校验（P2-8：只改校验不改 canSubmit / 错误横幅，回测下校验会跑但拦不住提交）。
  const profitGuardError = validateProfitGuard(profitGuardPayload);
  // 双口径换算的唯一真源；杠杆留空 ⇒ null ⇒ 只显示保证金口径并附一句「填写杠杆后显示」（诚实降级，不猜默认杠杆）。
  const armPriceMovePct = priceMoveFromMarginPct(profitGuardPayload.armReturnPct ?? null, leverage.value);
  const slMarginPct = marginReturnFromPricePct(
    stopLossPct.value ?? AI_TRADE_DEFAULT_OCO_SL_PCT, leverage.value);
  const tpMarginPct = marginReturnFromPricePct(
    takeProfitPct.value ?? AI_TRADE_DEFAULT_OCO_TP_PCT, leverage.value);

  // 交易周期模式（两模式共用；关闭勾选时提交体只含 enabled:false）。`profileId` 未选过即取接口
  // 下发的 defaultProfile——前端不复制服务端的默认选择。
  // 预设表未就绪（加载中 / 读取失败）且用户没主动选过 ⇒ 提交体**不带 profile 键**，交由服务端按
  // `ai_trade_horizon_default_profile` 装配（§4.2「字段缺省即默认模式」）。前端拿 `custom` 顶上会把
  // 一个本应按默认模式生效的会话静默降级成空转契约——那是本层最不该出现的失效方式。
  const horizonProfile = horizonProfileRaw ?? horizonQ.data?.defaultProfile ?? null;
  const horizonProfileId = (horizonProfile ?? "custom") as AiTradeHorizonProfileId;
  // 自愈模式：用户没主动选过 ⇒ **不带该键**，交由服务端按运维缺省回填（与 `profile` 同款姿态：
  // 前端拿一个本地默认顶上，会让「弹窗显示的」与「实际落地的」在运维改配置后静默分叉）。
  const tradingHorizonPayload: AiTradeHorizonRule = horizonEnabled
    ? {
        enabled: true,
        ...(horizonProfile ? { profile: horizonProfile } : {}),
        ...(repairMode ? { backstopRepair: repairMode } : {}),
        ...horizonOverrides(horizonProfileId, horizonCustom),
      }
    : { enabled: false };
  const activeHorizonProfile =
    horizonQ.data?.profiles.find((p) => p.id === horizonProfileId) ?? null;
  // §2.8 防线 1（「跨日持仓必须有交易所侧止损」）**无需在此另写一条阻断**：`parseOptionalPct` 对
  // 「显式填 0」本就返回 error 并并入 `inputError`（`0` 解析不出 value），故该输入根本到不了这里；
  // 留空则走服务端默认 5%，契约摘要会按它现算解除阈并写明来源。再加一条判据只会是永不触发的死码。
  const horizonError = validateHorizon(tradingHorizonPayload);
  // [P0-1] 切换模式**只联动 bar / 唤醒周期**，严禁触碰 profitGuard.minHoldSeconds：那个字段控制的是
  // **利润保护层**在开仓瞬间的噪音抑制（默认 30 秒），而保护层在该窗口内整层停摆。把它抬到 3600 秒
  // 等于造出「G1 禁止 AI 平仓 **且** 保护层不工作」的双关窗口，而同屏契约摘要还在承诺「保护层不受
  // 本层限制」。两层的最短持有期解决的是两个不同问题（本层：换手成本；保护层：开仓瞬间的一跳噪音）。
  const alignToProfile = (profile: AiTradeHorizonProfile | undefined) => {
    if (!profile || profile.bar == null) return;
    setBar(profile.bar as Bar);
    if (profile.wakeIntervalMinutes != null) setWakeIntervalRaw(String(profile.wakeIntervalMinutes));
  };
  const handleProfileSelect = (profile: AiTradeHorizonProfile) => {
    setHorizonProfileRaw(profile.id);
    alignToProfile(profile);   // 切到 custom 时 `bar === null` ⇒ 不联动任何字段（[P1-8]）
  };

  // 成员校验（实盘）：非空、皆可自动交易、各 pct ∈ (0,100]，even 模式 Σ ≤ 100（避免提交即 422）。
  const sumPct = totalAllocation(members);
  const hasUnsupportedMember = members.some((m) => !isAutoTradable(m.instId));
  const membersError =
    members.length === 0
      ? "请至少保留一个自动交易标的"
      : hasUnsupportedMember
        ? "含不支持自动交易的标的（仅 USDT 本位永续 / 交割合约）"
        : members.some((m) => !(m.allocationPct > 0 && m.allocationPct <= 100))
          ? "每个标的百分比须在 (0, 100] 之间"
          : allocationMode === "even" && sumPct > 100 + 1e-9
            ? "各标的百分比之和不可超过 100%（或切换为「独立上限」）"
            : null;
  const oversubscribed = allocationMode === "max_ratio" && sumPct > 100 + 1e-9;

  const instSupported = isAutoTradable(instId);   // 回测单标的

  // 回测区间 + 决策间隔 + 计划轮数（R5：UI 默认永不越界自拒）。
  const startTs = fromLocalInput(startInput);
  const endTs = fromLocalInput(endInput);
  const rangeError =
    startTs == null || endTs == null ? "请选择回测区间起止时间" : validateBacktestRange(startTs, endTs);
  const smartEvery = useMemo(
    () =>
      startTs != null && endTs != null
        ? defaultDecisionEvery(startTs, endTs, bar, AI_BACKTEST_DEFAULT_DECISION_EVERY, AI_BACKTEST_MAX_ROUNDS)
        : AI_BACKTEST_DEFAULT_DECISION_EVERY,
    [startTs, endTs, bar],
  );
  const everyOverride = everyRaw.trim() ? Number.parseInt(everyRaw, 10) : null;
  const everyError = everyRaw.trim() && (!Number.isInteger(everyOverride) || (everyOverride ?? 0) < 1)
    ? "决策间隔需为 ≥1 的整数"
    : null;
  const everyValue = everyOverride && everyOverride >= 1 ? everyOverride : smartEvery;
  const minConsensusValue = minConsensusRaw.trim() ? Number.parseInt(minConsensusRaw, 10) : null;
  const minConsensusError =
    minConsensusRaw.trim() && (!Number.isInteger(minConsensusValue) || (minConsensusValue ?? 0) < 0)
      ? "共识门槛需为 ≥0 的整数"
      : null;
  const plannedRounds =
    startTs != null && endTs != null && !rangeError ? planRounds(startTs, endTs, bar, everyValue) : 0;
  const roundsCapError =
    plannedRounds > AI_BACKTEST_MAX_ROUNDS
      ? `计划决策轮数 ${plannedRounds} 超上限 ${AI_BACKTEST_MAX_ROUNDS}，请缩短区间或增大决策间隔`
      : null;

  // 生效总预算预览（分组总额；availUsdt 取 members[0] 的 config，账户级，P2-2）。留空默认仍取保守 avail×1%
  // （deriveDefaultBudget），天花板改用账户级 maxBudgetPct（avail×maxBudgetPct%，原写死 5%）——故改总预算
  // 即按 total×pct/100 联动每标的分配；口径与后端 cap_frac 逐配置严格相等（effectiveMaxBudgetPct/100）。
  const previewBudget = useMemo(() => {
    if (availUsdt == null) return null;
    const raw = capital.value ?? deriveDefaultBudget(availUsdt);
    return clampGroupBudgetToCap(raw, availUsdt, maxBudgetPct);
  }, [availUsdt, capital.value, maxBudgetPct]);
  // 填值超过硬上限时 previewBudget 被静默夹回上限——显式回显「已夹紧」，避免用户误以为输入未生效。
  const isClamped = isGroupBudgetClamped(capital.value, availUsdt, maxBudgetPct);
  const budgetCap = groupBudgetCap(availUsdt, maxBudgetPct);

  const usingDefault = capital.value == null;
  // 方向对称性（directional-symmetry D-5）：三个数值**留空即整键不发** ⇒ 服务端回填默认。
  // 越界在这里即时报错（区间与后端 `AiTradeDirectionIn` 的 `Field` 逐字一致），但**不阻断启动
  // 按钮**——校验错误进 `directionError`，与既有 `baseRuleError` / `profitGuardError` 同款并进
  // `baseValid`。
  const directionPayload: AiTradeDirectionRule = {
    contrarianEnabled: directionEnabled,
    contrarianMinConfidence: rawToNumber(directionConfidenceRaw),
    contrarianMaxPerSession: rawToNumber(directionQuotaRaw),
    contrarianSizeMult: rawToNumber(directionSizeMultRaw),
  };
  const directionError = validateDirectionRule(directionPayload);
  // 趋势基调：两个数值**留空即整键不发** ⇒ 服务端回填默认；三把子闸的极性不同，各自独立下发。
  const trendPayload: AiTradeTrendRule = {
    enabled: trendEnabled,
    counterGateEnabled: trendCounterGate,
    counterMinConfidence: rawToNumber(trendConfidenceRaw),
    counterSizeMult: rawToNumber(trendSizeMultRaw),
    consensusVoteEnabled: trendVoteEnabled,
    contrarianAllowanceEnabled: trendAllowanceEnabled,
  };
  const trendError = validateTrendRule(trendPayload);

  // 利润保护校验并入 baseValid：两模式共用（P2-8），避免回测下「校验会跑但拦不住提交」。
  const baseValid =
    !inputError && !strategyError && !roundsError && !baseRuleError && !profitGuardError
    && !horizonError && !directionError && !trendError;
  const ready =
    mode === "live"
      ? baseValid && !membersError && !wakeError && acknowledged
      : baseValid && instSupported && !!instId && !rangeError && !everyError && !minConsensusError && !roundsCapError;
  // 可交易性预检的放行判定（轴 A §3.1.3）。**刻意不并进 `ready`**：`ready` 直接驱动
  // `disabled={!ready}`，而本轴的极性纪律是「启动按钮永不禁用」（R-1：把用户挡在门外的探针比给错
  // 建议的探针更坏）。故按钮照旧可点，拦在提交路径上并同屏给出该做什么——若只把勾选态存进 state
  // 而没有任何读点，那一行二次确认就纯粹是装饰，一次点击照样能在几乎开不出单的周期上起会话。
  const tradabilityBlocked = tradabilityNeedsConfirm && !tradabilityConfirmed;
  // ★ A-3：关掉利润保护即需要二次确认（**只在实盘段**——回测不动真钱，那里再拦一次是噪音）。
  // 与 `tradabilityBlocked` 逐字同款的极性：**按钮永不禁用**，拦在提交路径上并同屏说清理由。
  const unguardedBlocked = mode === "live" && !profitGuardEnabled && !unguardedConfirmed;
  const handleConfirm = () => {
    if (!ready || submitting || tradabilityBlocked || unguardedBlocked) return;
    const base = {
      strategyIds: selectedIds,
      capitalUsd: capital.value,
      leverage: leverage.value,
      takeProfitUsd: takeProfit.value,
      stopLossUsd: stopLoss.value,
      stopLossPct: stopLossPct.value,
      takeProfitPct: takeProfitPct.value,
      positionStepPct: positionStep,                // 仓位操作档位（实盘/回测两 tab 同得）
      baseRules: baseRulesPayload,                  // 【基础策略】铁律（实盘/回测同源，P1-4）
      tradingHorizon: tradingHorizonPayload,        // 交易周期模式（同一服务端解析点，两侧同源）
      // 经验模式：整键**恒下发**——`{enabled:false}` 是「用户亲手取消了勾选」，与「整键缺省 ⇒
      // 服务端默认开启」严格区分。回测侧由服务端恒不装配（§十一.2），前端不重复那条判据。
      experience,
      // 交易纪律预设（同一服务端解析点 `_resolve_discipline`，实盘与回测两侧同源，R-3）。
      // **只下发 preset，不下发任何阈值**：阈值的唯一真源是后端那张冻结表。
      discipline: { preset: disciplinePreset },
      // 方向对称性：整键**恒下发**——`{contrarianEnabled:false}` 是「用户亲手取消了勾选」，与
      // 「整键缺省 ⇒ 服务端默认开启」严格区分。三个数值留空即 `null` ⇒ 服务端回填默认。
      direction: directionPayload,
      // 趋势基调：整键**恒下发**——`{enabled:false}` 是「用户亲手取消了勾选」，与「整键缺省
      // ⇒ 服务端默认开启」严格区分。**实盘与回测两侧同源**（同一个 `_resolve_trend`）。
      trend: trendPayload,
      contextRefreshRounds: roundsValue,
      bar,                                          // 可编辑周期真正生效（不再被 store.bar 覆盖）
    };
    if (mode === "live") {
      // [RN-1] wakeIntervalMinutes 仅 live 分支带上（base 不含、回测省略）；空→0=关闭定时唤醒。
      onConfirm({
        mode: "live", members, allocationMode, instId: members[0]?.instId ?? instId,
        ...base, wakeIntervalMinutes: wakeMinutes ?? 0, profitGuard: profitGuardPayload,
        // [P2-2] 与 wakeIntervalMinutes 同款：**仅 live 分支带上**，绝不落进 base。
        reasoningDepth,
      });
      return;
    }
    onConfirm({
      mode: "backtest",
      members,
      allocationMode,
      instId,
      ...base,
      // F3：回测提交体也带 profitGuard（与实盘同字段同语义、同一服务端解析点）。
      profitGuard: profitGuardPayload,
      startTs: startTs ?? undefined,
      endTs: endTs ?? undefined,
      decisionEveryBars: everyValue,          // 已解析为智能默认，永不越界自拒（R5）
      minConsensus: minConsensusValue,
    });
  };

  const firstError =
    strategyError ||
    roundsError ||
    inputError ||
    baseRuleError ||
    profitGuardError ||
    horizonError ||
    (mode === "live"
      ? membersError || wakeError
      : instSupported
        ? null
        : "标的不支持自动交易（仅 USDT 本位永续 / 交割合约）") ||
    (mode === "backtest" ? rangeError || everyError || minConsensusError || roundsCapError : null) ||
    // 排在既有校验之后：它不是输入非法，而是一次需要用户拍板的风险确认，不该盖住真正的填写错误。
    (tradabilityBlocked ? TRADABILITY_CONFIRM_REQUIRED_TEXT : null) ||
    // ★ A-3：同上极性、同上位次。
    (unguardedBlocked ? UNGUARDED_CONFIRM_REQUIRED_TEXT : null) ||
    error;

  const title = mode === "backtest" ? "AI 自动交易 · 回测" : "启动 AI 自动交易";

  return (
    <Modal open={open} onClose={onClose} labelledBy="ai-trade-launch-title" className="max-w-lg">
      <div className="flex items-center justify-between border-b border-[var(--border)] px-5 py-3.5">
        <h2 id="ai-trade-launch-title" className="text-sm font-semibold text-[var(--text)]">
          {title}
        </h2>
        {mode === "backtest" ? <Badge tone="accent">模拟回测</Badge> : <Badge tone="short">真实资金</Badge>}
      </div>

      <div className="flex max-h-[62vh] flex-col gap-3 overflow-auto px-5 py-4">
        {/* 模式段控（实盘 / 回测）。 */}
        <div className="flex gap-1 rounded-md bg-[var(--surface-2)] p-0.5">
          <ModeTab
            active={mode === "live"}
            disabled={!liveAvailable || submitting}
            title={liveGateReason ?? undefined}
            onClick={() => setMode("live")}
          >
            实盘
          </ModeTab>
          <ModeTab active={mode === "backtest"} disabled={submitting} onClick={() => setMode("backtest")}>
            回测
          </ModeTab>
        </div>

        <p className="text-[11px] leading-relaxed text-[var(--muted)]">
          {mode === "backtest" ? (
            <>
              AI 将在所选历史区间上<strong className="text-[var(--text)]">逐根决策</strong>（纯模拟撮合、
              不下真单、不动真实资金），完成后展示全套回测指标与 AI 决策过程，可先评估再决定是否投入真实资金。
            </>
          ) : (
            <>
              AI 助手将作为决策者，综合研判所选<strong className="text-[var(--text)]">多个策略</strong>的实时信号，
              对下方<strong className="text-[var(--text)]">全部标的</strong>同时在每根 K 线上决定开多 / 开空 /
              平仓 / 观望，真单经既有守护执行器落地并继承全部资金护栏。
            </>
          )}
        </p>

        {/* 交易周期模式（holding-horizon §2.11）：**挂在最顶部**——在「时间级别」之上、在【基础策略】
            之上。位置即优先级声明：它决定了下面几乎所有字段的合理取值（执行周期、唤醒节奏、杠杆）。 */}
        <AiTradeHorizonFields
          data={horizonQ.data ?? null}
          loading={horizonQ.isPending}
          enabled={horizonEnabled}
          onEnabledChange={setHorizonEnabled}
          profileId={horizonProfileId}
          onProfileChange={handleProfileSelect}
          custom={horizonCustom}
          onCustomChange={(key: HorizonCustomKey, value: string) =>
            setHorizonCustom((prev) => ({ ...prev, [key]: value }))
          }
          bar={bar}
          leverage={leverage.value}
          stopLossPct={stopLossPct.value}
          defaultStopLossPct={AI_TRADE_DEFAULT_OCO_SL_PCT}
          backstopRepairMode={repairMode}
          onBackstopRepairModeChange={setRepairMode}
          wakeMinutes={wakeMinutes}
          onAlign={() => alignToProfile(activeHorizonProfile ?? undefined)}
          disabled={submitting}
        />

        {/* 经验模式（experience-context-graph §7.2）：挂在【交易周期模式】之后、时间级别之前。 */}
        <AiTradeExperienceFields
          value={experience}
          onChange={setExperience}
          disabled={submitting}
          backtest={mode === "backtest"}
        />

        {/* 时间级别（默认 K 线所选周期，可修改，两模式共用）。 */}
        <label className="flex flex-col gap-1 text-[11px] text-[var(--muted)]">
          <span>时间级别（默认 K 线所选周期，可修改）</span>
          <Select
            value={bar}
            disabled={submitting}
            onChange={(e) => setBar(e.target.value as Bar)}
            className="text-xs"
            aria-label="时间级别"
          >
            {BARS.map((b) => (
              <option key={b} value={b}>
                {b}
              </option>
            ))}
          </Select>
        </label>

        {/* 【基础策略】铁律区块（ai-trade-base-strategy-ma-band §三）：刻意置于「交易策略」清单**之上**
            ——它不是策略库里的第四个条目，而是凌驾于所选全部策略之上的开单纪律。默认勾选，可取消。 */}
        <div className="flex flex-col gap-1.5 rounded-md border border-[var(--accent-soft)] bg-[color-mix(in_srgb,var(--accent)_5%,transparent)] px-3 py-2">
          <label className="flex cursor-pointer items-start gap-2 text-xs">
            <input
              type="checkbox"
              checked={baseRuleEnabled}
              disabled={submitting}
              onChange={(e) => setBaseRuleEnabled(e.target.checked)}
              className="mt-0.5 h-3.5 w-3.5 shrink-0 accent-[var(--accent)]"
              aria-label="基础策略（铁律 · 默认开启）"
            />
            <span className="flex flex-col gap-0.5">
              <span className="font-medium text-[var(--text)]">基础策略（铁律 · 默认开启）</span>
              <span className="text-[11px] leading-relaxed text-[var(--muted)]">
                <strong className="text-[var(--text)]">MA 通道开单约束</strong>：无论选了哪些交易策略，
                开多 / 开空只允许在所选时间级别 MA 通道的
                <strong className="text-[var(--text)]">上轨、中轨或下轨附近</strong>发生，
                不满足即由系统直接降级为观望（限价委托按<strong className="text-[var(--text)]">委托价</strong>判定）。
                平仓与撤单不受限制。
              </span>
            </span>
          </label>
          {baseRuleEnabled && (
            <details className="text-[11px] text-[var(--muted)]">
              <summary className="cursor-pointer select-none text-[var(--accent-ink)]">高级参数</summary>
              <div className="mt-1.5 flex flex-col gap-1.5">
                <div className="flex items-center gap-2">
                  <label className="flex flex-1 items-center gap-1.5">
                    <span className="shrink-0">均线周期</span>
                    <input
                      type="text"
                      inputMode="numeric"
                      value={baseRulePeriodRaw}
                      disabled={submitting}
                      onChange={(e) => setBaseRulePeriodRaw(e.target.value)}
                      aria-label="基础策略均线周期"
                      className="w-full rounded border border-[var(--border)] bg-[var(--bg)] px-1.5 py-1 text-right text-xs text-[var(--text)] outline-none focus:border-[var(--accent)] disabled:opacity-50"
                    />
                  </label>
                  <label className="flex flex-1 items-center gap-1.5">
                    <span className="shrink-0">带宽倍数 σ</span>
                    <input
                      type="text"
                      inputMode="decimal"
                      value={baseRuleStdRaw}
                      disabled={submitting}
                      onChange={(e) => setBaseRuleStdRaw(e.target.value)}
                      aria-label="基础策略带宽倍数"
                      className="w-full rounded border border-[var(--border)] bg-[var(--bg)] px-1.5 py-1 text-right text-xs text-[var(--text)] outline-none focus:border-[var(--accent)] disabled:opacity-50"
                    />
                  </label>
                </div>
                <label className="flex flex-col gap-1">
                  <span>「附近」的容差（带宽归一，跨周期同义）</span>
                  <Select
                    value={String(baseRuleTolerance)}
                    disabled={submitting}
                    onChange={(e) => setBaseRuleTolerance(Number(e.target.value))}
                    className="text-xs"
                    aria-label="基础策略容差"
                  >
                    {AI_TRADE_BAND_TOLERANCE_OPTIONS.map((o) => (
                      <option key={o.value} value={o.value}>
                        {o.label}
                      </option>
                    ))}
                  </Select>
                </label>
                <label className="flex cursor-pointer items-start gap-2">
                  <input
                    type="checkbox"
                    checked={baseRuleDirectional}
                    disabled={submitting}
                    onChange={(e) => setBaseRuleDirectional(e.target.checked)}
                    className="mt-0.5 h-3.5 w-3.5 shrink-0 accent-[var(--accent)]"
                    aria-label="基础策略方向性模式"
                  />
                  <span className="leading-relaxed">
                    方向性（做多只许下轨 / 中轨，做空只许上轨 / 中轨）——
                    <strong className="text-[var(--text)]">保守模式，趋势突破类策略慎用</strong>。
                  </span>
                </label>
              </div>
            </details>
          )}
        </div>

        {/* 多选策略清单（≥1、≤上限，两模式共用）。 */}
        <div className="flex flex-col gap-1 text-[11px] text-[var(--muted)]">
          <span>
            交易策略（可多选，AI 综合研判其信号）· 已选{" "}
            <span className="font-medium text-[var(--text)]">{selectedIds.length}</span>/{AI_TRADE_MAX_STRATEGIES}
          </span>
          <div className="max-h-40 overflow-auto rounded-md border border-[var(--border)]">
            {(strategiesQ.data ?? []).length === 0 && (
              <div className="px-3 py-2 text-xs text-[var(--muted)]">
                {strategiesQ.isError ? "策略列表读取失败" : "加载中…"}
              </div>
            )}
            {(strategiesQ.data ?? []).map((s) => (
              <label
                key={s.id}
                className="flex cursor-pointer items-center gap-2 border-t border-[var(--border)] px-3 py-1.5 text-xs first:border-t-0 hover:bg-[var(--surface-2)]"
              >
                <input
                  type="checkbox"
                  checked={selectedIds.includes(s.id)}
                  disabled={submitting}
                  onChange={() => toggleId(s.id)}
                  className="h-3.5 w-3.5 shrink-0 accent-[var(--accent)]"
                />
                <span className="truncate text-[var(--text)]">{s.name}</span>
              </label>
            ))}
          </div>
        </div>

        {mode === "live" ? (
          /* 实盘：多标的分配选择器（默认回填 autoTradeMembers）。 */
          <div className="flex flex-col gap-1.5 text-[11px] text-[var(--muted)]">
            <div className="flex items-center justify-between gap-2">
              <span>
                自动交易标的（默认取自左侧「自动交易」面板）· 已选{" "}
                <span className="font-medium text-[var(--text)]">{members.length}</span>/{AI_TRADE_MAX_SYMBOLS}
              </span>
              <div className="flex items-center gap-1">
                <AllocModeTab active={allocationMode === "even"} disabled={submitting} onClick={() => setAllocationMode("even")}>
                  均分份额
                </AllocModeTab>
                <AllocModeTab active={allocationMode === "max_ratio"} disabled={submitting} onClick={() => setAllocationMode("max_ratio")}>
                  独立上限
                </AllocModeTab>
                <button
                  type="button"
                  disabled={submitting || members.length === 0}
                  onClick={applyEvenSplit}
                  className="rounded border border-[var(--border)] px-2 py-0.5 text-[10px] text-[var(--text)] hover:bg-[var(--surface-2)] disabled:opacity-40"
                >
                  均分
                </button>
              </div>
            </div>
            {truncated && (
              <p className="rounded border border-[var(--border)] bg-[var(--surface-2)] px-2 py-1 text-[10px] leading-relaxed text-[var(--muted)]">
                左侧已配置 {defaultMembers.length} 个标的，已达 AI 自动交易标的上限 {AI_TRADE_MAX_SYMBOLS}，
                仅前 {AI_TRADE_MAX_SYMBOLS} 个入选（可在此手动增删）。
              </p>
            )}
            <div className="overflow-hidden rounded-md border border-[var(--border)]">
              <div className="flex items-center justify-between bg-[var(--surface-2)] px-3 py-1.5 text-[10px] font-semibold uppercase tracking-wide text-[var(--muted)]">
                <span>标的</span>
                <span>{allocationMode === "max_ratio" ? "上限% · 预算预览" : "百分比% · 预算预览"}</span>
              </div>
              {members.map((m) => (
                <div
                  key={m.instId}
                  className="flex items-center justify-between gap-2 border-t border-[var(--border)] px-3 py-1.5 text-xs"
                >
                  <span className="flex items-center gap-1.5 truncate text-[var(--text)]">
                    {m.instId}
                    {!isAutoTradable(m.instId) && (
                      <span className="text-[10px] text-[var(--short-ink)]">不支持</span>
                    )}
                  </span>
                  <span className="flex items-center gap-2">
                    <input
                      type="text"
                      inputMode="decimal"
                      value={String(m.allocationPct)}
                      disabled={submitting}
                      onChange={(e) => updateAllocation(m.instId, e.target.value)}
                      aria-label={`${m.instId} 百分比`}
                      className="w-14 rounded border border-[var(--border)] bg-[var(--bg)] px-1.5 py-1 text-right text-xs text-[var(--text)] outline-none focus:border-[var(--accent)] disabled:opacity-50"
                    />
                    <span className="tabular text-[10px] text-[var(--muted)]">
                      {previewBudget != null
                        ? `${fmtPrice(memberBudget(previewBudget, m.allocationPct))} USDT`
                        : "—"}
                    </span>
                    <button
                      type="button"
                      disabled={submitting}
                      onClick={() => removeMember(m.instId)}
                      aria-label={`移除 ${m.instId}`}
                      className="rounded px-1 text-[var(--muted)] hover:text-[var(--short-ink)] disabled:opacity-40"
                    >
                      ✕
                    </button>
                  </span>
                </div>
              ))}
              {/* 新增标的（达上限即禁用）。 */}
              <div className="flex items-center gap-2 border-t border-[var(--border)] px-3 py-1.5">
                <input
                  type="text"
                  value={addSymbolRaw}
                  placeholder={atCap ? `已达上限 ${AI_TRADE_MAX_SYMBOLS} 个` : "新增标的（USDT 永续 / 交割）"}
                  disabled={submitting || atCap}
                  onChange={(e) => setAddSymbolRaw(e.target.value.trim().toUpperCase())}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") {
                      e.preventDefault();
                      handleAddSymbol();
                    }
                  }}
                  aria-label="新增自动交易标的"
                  className="flex-1 rounded border border-[var(--border)] bg-[var(--bg)] px-2 py-1 text-xs uppercase text-[var(--text)] outline-none focus:border-[var(--accent)] disabled:opacity-50"
                />
                <button
                  type="button"
                  disabled={submitting || atCap || !addSymbolRaw.trim()}
                  onClick={handleAddSymbol}
                  className="rounded border border-[var(--border)] px-2 py-1 text-[11px] text-[var(--text)] hover:bg-[var(--surface-2)] disabled:opacity-40"
                >
                  添加
                </button>
              </div>
            </div>
            {addError && <span className="text-[10px] text-[var(--short-ink)]">{addError}</span>}
            {oversubscribed && (
              <p className="rounded border border-[var(--border)] bg-[var(--surface-2)] px-2 py-1 text-[10px] leading-relaxed text-[var(--muted)]">
                各标的上限合计 {Math.round(sumPct * 100) / 100}%（&gt; 100%）：多标的同时触发时组合敞口可能超过总预算，
                真实敞口仍受总预算上限（可用余额 × {maxBudgetPct}%）、单笔名义上限与单仓 OCO 约束。
              </p>
            )}
          </div>
        ) : (
          /* 回测：仍单标的（默认 members[0]），同享上方可编辑周期。 */
          <label className="flex flex-col gap-1 text-[11px] text-[var(--muted)]">
            <span>标的（回测单标的，默认首个自动交易标的，仅 USDT 本位永续 / 交割合约）</span>
            <input
              type="text"
              value={instId}
              disabled={submitting}
              onChange={(e) => setInstId(e.target.value.trim().toUpperCase())}
              className="rounded-md border border-[var(--border)] bg-[var(--bg)] px-2 py-1.5 text-xs uppercase text-[var(--text)] outline-none focus:border-[var(--accent)] disabled:opacity-50"
            />
          </label>
        )}

        {mode === "backtest" && (
          <div className="grid grid-cols-2 gap-3">
            <DateField label="回测起始" value={startInput} onChange={setStartInput} disabled={submitting} />
            <DateField label="回测结束" value={endInput} onChange={setEndInput} disabled={submitting} />
          </div>
        )}

        {mode === "live" ? (
          <div className="flex flex-col gap-px overflow-hidden rounded-md bg-[var(--border)]">
            <Row label="交易账户" value="设置中心 · 默认实盘账户" />
            <Row
              label="可用余额"
              value={
                availUsdt != null
                  ? `${fmtPrice(availUsdt)} USDT`
                  : configQ.isError
                    ? "读取失败"
                    : "读取中…"
              }
            />
            <Row
              label={
                usingDefault
                  ? "默认总预算（可用余额 × 1%）"
                  : isClamped
                    ? `生效总预算（预览 · 已按 ${maxBudgetPct}% 上限夹紧）`
                    : "生效总预算（预览）"
              }
              value={previewBudget != null ? `${fmtPrice(previewBudget)} USDT` : "—"}
            />
            <Row
              label="生效杠杆"
              value={leverage.value != null ? `${leverage.value}×` : `默认 ${LIVE_AUTO_DEFAULT_LEVERAGE}×`}
            />
            <Row label="单笔名义上限" value={`≤ ${LIVE_MAX_ORDER_NOTIONAL_USD} USDT/单`} />
            <Row
              label="单仓兜底 OCO（交易所侧）"
              value={`SL ${fmtOcoDual(stopLossPct.value, AI_TRADE_MAX_SL_PCT, AI_TRADE_DEFAULT_OCO_SL_PCT, slMarginPct)} / TP ${fmtOcoDual(takeProfitPct.value, AI_TRADE_MAX_TP_PCT, AI_TRADE_DEFAULT_OCO_TP_PCT, tpMarginPct)}`}
            />
          </div>
        ) : (
          <div className="flex flex-col gap-px overflow-hidden rounded-md bg-[var(--border)]">
            <Row
              label="计划决策轮数"
              value={
                <span className={roundsCapError ? "text-[var(--short-ink)]" : undefined}>
                  {plannedRounds} 轮 · 间隔 {everyValue} 根/次（上限 {AI_BACKTEST_MAX_ROUNDS}）
                </span>
              }
            />
            <Row label="初始资金" value={capital.value != null ? `${fmtPrice(capital.value)} USDT` : "默认"} />
            <Row
              label="单仓 OCO（模拟撮合）"
              value={`SL ${fmtOcoDual(stopLossPct.value, AI_TRADE_MAX_SL_PCT, AI_TRADE_DEFAULT_OCO_SL_PCT, slMarginPct)} / TP ${fmtOcoDual(takeProfitPct.value, AI_TRADE_MAX_TP_PCT, AI_TRADE_DEFAULT_OCO_TP_PCT, tpMarginPct)}`}
            />
            <Row label="手续费 / 滑点" value="taker 5bps / 2bps（同其它回测）" />
          </div>
        )}

        <div className="grid grid-cols-2 gap-3">
          <Field
            label={mode === "backtest" ? "初始资金（USDT）" : "总预算（USDT）"}
            placeholder="留空＝默认"
            value={capitalRaw}
            onChange={setCapitalRaw}
            disabled={submitting}
            hint={
              mode === "live" && isClamped && capital.value != null
                ? `输入 ${fmtPrice(capital.value)} 超过可用余额 ${maxBudgetPct}% 上限（${fmtPrice(budgetCap)} USDT），已按上限 ${fmtPrice(budgetCap)} 计算`
                : undefined
            }
          />
          <Field label="杠杆（×）" placeholder="留空＝默认" value={leverageRaw} onChange={setLeverageRaw} disabled={submitting} />
          <Field label={mode === "backtest" ? "会话止盈（USDT）" : "总止盈（USDT）"} placeholder="留空＝不触发" value={takeProfitRaw} onChange={setTakeProfitRaw} disabled={submitting} />
          <Field label={mode === "backtest" ? "会话止损（USDT）" : "总止损（USDT）"} placeholder="留空＝不触发" value={stopLossRaw} onChange={setStopLossRaw} disabled={submitting} />
          <Field
            label="单仓止损%（如 0.05＝5%）"
            placeholder={ocoPlaceholder(AI_TRADE_DEFAULT_OCO_SL_PCT, leverage.value, "-")}
            value={stopLossPctRaw}
            onChange={setStopLossPctRaw}
            disabled={submitting}
            hint={leverage.value == null ? "保证金口径按下单杠杆换算，填写杠杆后显示" : undefined}
          />
          <Field
            label="单仓止盈%（如 0.10＝10%）"
            placeholder={ocoPlaceholder(AI_TRADE_DEFAULT_OCO_TP_PCT, leverage.value, "+")}
            value={takeProfitPctRaw}
            onChange={setTakeProfitPctRaw}
            disabled={submitting}
          />
        </div>

        {/* 仓位操作档位（两模式共用）：约束 AI 每次开仓规模只能落在离散档位阶梯上，而非任意小数金额。 */}
        <label className="flex flex-col gap-1 text-[11px] text-[var(--muted)]">
          <span>仓位操作档位（AI 每次开仓规模）</span>
          <Select
            value={String(positionStep)}
            disabled={submitting}
            onChange={(e) => setPositionStep(Number(e.target.value))}
            className="text-xs"
            aria-label="仓位操作档位"
          >
            {AI_TRADE_POSITION_STEP_OPTIONS.map((o) => (
              <option key={o.value} value={o.value}>
                {o.label}
              </option>
            ))}
          </Select>
          <span className="leading-relaxed text-[var(--muted)]">
            可用档位：{positionLadder(positionStep).map((g) => `${Math.round(g * 100)}%`).join(" / ")}；档位仅约束
            <strong className="text-[var(--text)]">开仓规模</strong>（非分批加/减仓，平仓为全平），AI 每次开仓将吸附到最近的合法档位。
          </span>
        </label>

        {/* Agent 唤醒周期（仅实盘段）：在「确认 K 线 / 策略触发 → 决策」之外叠加墙钟定时唤醒；回测走逐根决策、
            与墙钟无关，故不露出。留空 / 0 = 关闭定时唤醒。 */}
        {mode === "live" && (
          <label className="flex flex-col gap-1 text-[11px] text-[var(--muted)]">
            <span>Agent 唤醒周期（分钟）</span>
            <input
              type="text"
              inputMode="decimal"
              value={wakeIntervalRaw}
              placeholder={String(AI_TRADE_DEFAULT_WAKE_INTERVAL_MIN)}
              disabled={submitting}
              onChange={(e) => setWakeIntervalRaw(e.target.value)}
              aria-label="Agent 唤醒周期（分钟）"
              className="rounded-md border border-[var(--border)] bg-[var(--bg)] px-2 py-1.5 text-xs text-[var(--text)] outline-none focus:border-[var(--accent)] disabled:opacity-50"
            />
            <span className="leading-relaxed text-[var(--muted)]">
              除按 K 线与策略触发外，每隔此分钟数 Agent 也会用<strong className="text-[var(--text)]">当前实时价</strong>重新研判；
              越小唤醒越频繁、LLM 调用越多；留空 / 0 = 仅按 K 线与策略触发。
            </span>
            {reasoningDepth === "exhaustive" && wakeMinutes != null && wakeMinutes > 0 && wakeMinutes < 6 && (
              // **非阻断**提示（不禁用提交）：交易节奏由用户决定，静默替用户改参数是更坏的形态。
              <span className="rounded border border-[var(--border)] bg-[var(--surface-2)] px-2 py-1 leading-relaxed text-[var(--text)]">
                「极致」档单轮可能 5–10 分钟，而当前唤醒周期是 {wakeMinutes} 分钟——推理会挤占交易节奏，
                建议把周期调到 ≥ 6 分钟（仅提示，不阻止启动）。
              </span>
            )}
          </label>
        )}

        {/* 研判深度（deep-reasoning-chain §3.5.3）：**仅实盘段露出**。回测档由服务端独立取
            `AI_TRADE_REASONING_DEPTH_BACKTEST`（默认「标准」，§3.6 的成本护栏），此处只读展示——
            若把选择器落进两 tab 共用的 base，用户在回测 tab 选「深度」会直接推翻那道护栏。 */}
        {mode === "live" ? (
          <label className="flex flex-col gap-1 text-[11px] text-[var(--muted)]">
            <span>研判深度（思维链）</span>
            <Select
              value={reasoningDepth}
              disabled={submitting}
              onChange={(e) => setReasoningDepth(e.target.value as AiTradeReasoningDepth)}
              className="text-xs"
              aria-label="研判深度"
            >
              {(["off", "standard", "deep", "exhaustive"] as const).map((d) => (
                <option key={d} value={d}>
                  {REASONING_DEPTH_LABELS[d]}
                </option>
              ))}
            </Select>
            <span className="leading-relaxed text-[var(--muted)]">
              {REASONING_DEPTH_HINTS[reasoningDepth]}
            </span>
          </label>
        ) : (
          <p className="rounded-md border border-[var(--border)] bg-[var(--surface-2)] px-2 py-1.5 text-[11px] leading-relaxed text-[var(--muted)]">
            回测使用<strong className="text-[var(--text)]">独立</strong>的研判深度档（默认「标准」），
            以免一次 500 轮回测变成一笔意外的高额 token 账单；如需调整请到设置中心改{" "}
            <code>AI_TRADE_REASONING_DEPTH_BACKTEST</code>。
          </p>
        )}

        {/* 交易纪律预设（discipline-preset 轴 A）：**实盘与回测两段都渲染**——两侧走同一个服务端
            解析点，只在一边露出就会让用户拿回测调出来的配置在实盘失真（base_rules P1-4 的成文教训）。
            这是本轮唯一改变默认行为的地方，故必须在此**明示**而不是静默生效。 */}
        <DisciplinePresetFields
          value={disciplinePreset}
          onChange={setDisciplinePreset}
          disabled={submitting}
          table={disciplineQ.data ?? null}
          inertKeys={inertThresholdKeys}
        />

        {/* 方向对称 · 逆势通路（directional-symmetry D-5）：紧接「交易纪律预设」——那张阈值表里
            的「同向共识门槛」正是本块要放开的那一道闸，两块不相邻就读不懂。**实盘与回测两段都
            渲染**：两侧走同一个 `_resolve_direction`，只在一边露出就会让用户拿回测调出来的配置
            在实盘失真（base_rules P1-4 的成文教训）。 */}
        <AiTradeDirectionFields
          value={directionPayload}
          onChange={(next) => setDirectionEnabled(next.contrarianEnabled)}
          confidenceRaw={directionConfidenceRaw}
          quotaRaw={directionQuotaRaw}
          sizeMultRaw={directionSizeMultRaw}
          onRawChange={(key, raw) => {
            if (key === "confidence") setDirectionConfidenceRaw(raw);
            else if (key === "quota") setDirectionQuotaRaw(raw);
            else setDirectionSizeMultRaw(raw);
          }}
          disabled={submitting}
          error={directionError}
          trend={trendPayload}
          onTrendChange={(next) => {
            setTrendEnabled(next.enabled);
            setTrendCounterGate(next.counterGateEnabled !== false);
            setTrendVoteEnabled(next.consensusVoteEnabled !== false);
            setTrendAllowanceEnabled(next.contrarianAllowanceEnabled !== false);
          }}
          trendConfidenceRaw={trendConfidenceRaw}
          trendSizeMultRaw={trendSizeMultRaw}
          onTrendRawChange={(key, raw) => {
            if (key === "confidence") setTrendConfidenceRaw(raw);
            else setTrendSizeMultRaw(raw);
          }}
          trendError={trendError}
        />

        {/* 可交易性预检（tradability-and-open-funnel 轴 A）：挂在「交易纪律」正下方——它算的正是
            「这组纪律阈值在这个周期上会拦掉多少」，两块必须相邻才读得懂。只提示、不阻断。 */}
        <AiTradeTradabilityFields
          open={open}
          instId={budgetInst}
          bar={bar}
          preset={disciplinePreset}
          stopLossPct={stopLossPct.value}
          onPickBar={(next) => setBar(next as Bar)}
          confirmed={tradabilityConfirmed}
          onConfirmedChange={setTradabilityConfirmed}
          onNeedsConfirmChange={setTradabilityNeedsConfirm}
          disabled={submitting}
        />

        {/* 利润保护层（**实盘与回测两段都渲染**，adaptive §7.2a I-2）：第 1 轮把回测列为非目标，是因为
            当时只想得到「用收盘价近似」这一种做法；本轮 F3 改用 bar 内 OHLC 极值 + 保守成交序，故回测
            段照常露出，但文案必须写明是**保守近似**，不得让用户以为回测 = 实盘。 */}
        <ProfitGuardFields
          mode={mode}
          enabled={profitGuardEnabled}
          onEnabledChange={setProfitGuardEnabled}
          armReturnRaw={armReturnRaw}
          onArmReturnChange={setArmReturnRaw}
          givebackRaw={givebackRaw}
          onGivebackChange={setGivebackRaw}
          breakevenArmRaw={breakevenArmRaw}
          onBreakevenArmChange={setBreakevenArmRaw}
          minHoldRaw={minHoldRaw}
          onMinHoldChange={setMinHoldRaw}
          tierPreset={tierPreset}
          onTierPresetChange={setTierPreset}
          atrFloorRaw={atrFloorRaw}
          onAtrFloorChange={setAtrFloorRaw}
          atrCapRaw={atrCapRaw}
          onAtrCapChange={setAtrCapRaw}
          cooldownRaw={cooldownRaw}
          onCooldownChange={setCooldownRaw}
          disabled={submitting}
          preview={guardPreview(armReturnRaw, givebackRaw, armPriceMovePct)}
          hardTakeProfitText={hardTakeProfitText}
        />

        {mode === "live" && !profitGuardEnabled && (
          // ★ A-3 的二次确认（镜像可交易性预检那一行的既有范式）。**按钮不禁用**，只在提交路径
          // 上拦一次并同屏说清理由——把用户挡在门外的探针比给错建议的探针更坏（轴 A 的极性）。
          <label className="flex cursor-pointer items-start gap-2 px-1 text-[11px] leading-relaxed text-[var(--text)]">
            <input
              type="checkbox"
              checked={unguardedConfirmed}
              disabled={submitting}
              onChange={(e) => setUnguardedConfirmed(e.target.checked)}
              className="mt-0.5 h-3.5 w-3.5 shrink-0 accent-[var(--short)]"
              data-testid="ai-profit-guard-unguarded-confirm"
              aria-label="我已了解关闭利润保护的后果，仍要启动"
            />
            <span>我已了解上述后果，仍要在<strong>没有任何自动兑现通道</strong>的情况下启动。</span>
          </label>
        )}

        {mode === "backtest" && (
          <div className="grid grid-cols-2 gap-3">
            <Field
              label="决策间隔（根/次）"
              placeholder={String(smartEvery)}
              value={everyRaw}
              onChange={setEveryRaw}
              disabled={submitting}
              hint="AI 每 K 根决策一次，越大越省 LLM（盯市/风控不降频）"
            />
            <Field
              label="共识门槛（可选）"
              placeholder="留空＝默认"
              value={minConsensusRaw}
              onChange={setMinConsensusRaw}
              disabled={submitting}
              hint="开仓需同向策略数 ≥ 此值，0=关闭"
            />
          </div>
        )}

        <Field
          label="上下文刷新轮数"
          placeholder={String(AI_TRADE_DEFAULT_CONTEXT_ROUNDS)}
          value={roundsRaw}
          onChange={setRoundsRaw}
          disabled={submitting}
          hint="每 N 轮把 AI 执行过程摘要为上下文、清空历史保持纯净（保留连续记忆的同时守住 token 预算）"
        />

        {mode === "live" && (
          <label className="flex items-start gap-2 text-xs leading-relaxed text-[var(--text)]">
            <input
              type="checkbox"
              checked={acknowledged}
              disabled={submitting}
              onChange={(e) => onAcknowledge(e.target.checked)}
              aria-label="我已知晓这是真实资金 AI 自动交易"
              className="mt-0.5 h-4 w-4 shrink-0 accent-[var(--accent)]"
            />
            <span>
              我已知晓这是<strong className="text-[var(--short-ink)]">真实资金 AI 自动交易</strong>
              ，AI 将对上述全部标的据所选策略信号自主决定开仓 / 平仓，盈亏由本人承担。
            </span>
          </label>
        )}

        {firstError && (
          <p role="alert" className="text-[11px] font-medium text-[var(--short-ink)]">
            {firstError}
          </p>
        )}

        {errorRemedies && errorRemedies.length > 0 && (
          // ★ A-1：422 的三条**可执行**出路。文案逐条由后端下发（前端不自造），而每一条按钮
          // 真的把对应的开关拨过去——「知道坏了却不知道该点哪里」是本轮明确要消灭的形态。
          // `ops_override` 是运维侧动作（改 env 后重启后端），前端拨不动它，故只渲染成说明。
          <div className="flex flex-wrap items-center gap-1.5" data-testid="ai-start-remedies">
            {errorRemedies.map((remedy) =>
              remedy.id === "ops_override" ? (
                <span
                  key={remedy.id}
                  className="rounded border border-dashed border-[var(--border)] px-2 py-0.5 text-[10px] text-[var(--muted)]"
                >
                  {remedy.label}
                </span>
              ) : (
                <button
                  key={remedy.id}
                  type="button"
                  disabled={submitting}
                  data-testid={`ai-start-remedy-${remedy.id}`}
                  onClick={() => {
                    if (remedy.id === "enable_profit_guard") setProfitGuardEnabled(true);
                    if (remedy.id === "disable_horizon") setHorizonEnabled(false);
                  }}
                  className="rounded border border-[var(--accent)] bg-[var(--accent-soft)] px-2 py-0.5 text-[10px] font-medium text-[var(--accent-ink)] hover:opacity-90 focus-visible:outline focus-visible:outline-2 focus-visible:outline-[var(--accent)] disabled:opacity-50"
                >
                  {remedy.label}
                </button>
              ),
            )}
          </div>
        )}
      </div>

      <div className="flex items-center justify-end gap-2 border-t border-[var(--border)] px-5 py-3">
        <Button variant="ghost" size="sm" onClick={onClose} disabled={submitting}>
          取消
        </Button>
        {/* 按钮**永不因预检而禁用**（轴 A 的 fail-open 极性）：`infeasible` 只把文案改成「仍要
            启动」并在提交路径上要一次显式确认，拦截理由同屏写在上面的错误横幅里。 */}
        <Button variant="primary" size="sm" onClick={handleConfirm} disabled={!ready || submitting}>
          {mode === "backtest"
            ? submitting
              ? "回测启动中…"
              : "开始 AI 回测"
            : submitting
              ? "启动中…"
              : tradabilityNeedsConfirm
                ? "仍要启动 AI 自动交易"
                : "确认并开始 AI 自动交易"}
        </Button>
      </div>
    </Modal>
  );
}

/** datetime-local → UTC 秒；空/非法 → null。 */
function fromLocalInput(value: string): number | null {
  if (!value) return null;
  const ms = new Date(value).getTime();
  return Number.isFinite(ms) ? Math.floor(ms / 1000) : null;
}

/** UTC 秒 → 本地 datetime-local 字符串（YYYY-MM-DDTHH:mm）。 */
function toLocalInput(ts: number): string {
  const d = new Date(ts * 1000);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

/** 单仓 OCO 比例预览：留空→「默认」；有值→夹紧到 max 后按百分比展示（如 0.9→「20%」）。 */
function fmtOcoPct(value: number | null, max: number): string {
  if (value == null) return "默认";
  return `${+(clampPct(value, max) * 100).toFixed(2)}%`;
}

/**
 * 单仓 OCO 的**双口径**渲染（诊断根因 ②/⑤）：价格口径 +「≈保证金 ±X%」。
 * 留空时不再只写「默认」——那正是让用户不知不觉吃下 10%/5% 宽阈值的元凶，改为如实写出生效默认值。
 * 杠杆留空 ⇒ `marginPct` 为 null ⇒ 只出价格口径（绝不猜后端的 live_default_leverage，[P1-9]）。
 */
function fmtOcoDual(
  value: number | null,
  max: number,
  fallback: number,
  marginPct: number | null,
): string {
  const effective = value == null ? fallback : clampPct(value, max);
  const head = `${+(effective * 100).toFixed(2)}%${value == null ? "（默认）" : ""}`;
  if (marginPct == null) return head;
  return `${head}（≈保证金 ${marginPct >= 0 ? "" : "-"}${+(Math.abs(marginPct) * 100).toFixed(1)}%）`;
}

/** OCO 输入框 placeholder：把「留空＝默认」的隐藏语义写成具体数值（+ 已知杠杆时的保证金口径）。 */
function ocoPlaceholder(fallback: number, leverage: number | null, sign: string): string {
  const margin = marginReturnFromPricePct(fallback, leverage);
  const base = `留空＝默认 ${+(fallback * 100).toFixed(2)}%`;
  return margin == null ? base : `${base}（≈保证金 ${sign}${+(margin * 100).toFixed(0)}%）`;
}

/** 百分数输入串 → fraction（空串 / 非数 → NaN，交由 validateProfitGuard 报错，不静默吞）。 */
function rawPctToFraction(raw: string): number {
  const n = Number(raw);
  return Number.isFinite(n) ? n / 100 : Number.NaN;
}

/** fraction → 百分数输入串（缺省回落给定默认，保证输入框永不出现空白/NaN）。 */
function pctToRaw100(value: number | null | undefined, fallback: number): string {
  const n = value ?? fallback;
  return String(+(n * 100).toFixed(4));
}

/** fraction → 单仓 OCO 输入串（缺省 ⇒ 空串＝沿用服务端默认，保持「留空」这一合法退出口）。 */
function pctToRaw(value: number | null | undefined): string {
  return value == null ? "" : String(value);
}

/** 利润保护实时预览（纯前端派生，无请求）；杠杆未知时诚实省略标的涨跌幅换算（[P1-9]）。 */
function guardPreview(armRaw: string, givebackRaw: string, armPriceMovePct: number | null): string {
  const arm = Number(armRaw);
  const giveback = Number(givebackRaw);
  if (!Number.isFinite(arm) || !Number.isFinite(giveback)) return "";
  const scope =
    armPriceMovePct == null
      ? `浮盈达 ${+arm.toFixed(2)}%（保证金口径）`
      : `浮盈达 ${+arm.toFixed(2)}%（≈标的 ${+(armPriceMovePct * 100).toFixed(2)}%）`;
  return `${scope}即武装 → 自峰值回吐 ${+giveback.toFixed(2)}% 或跌回保本线即由系统平仓。`;
}

/**
 * 交易周期「自定义」输入的回填种子（holding-horizon §2.11）。
 *
 * **全部留空**是本特性的安全退出口：空串 ⇒ 提交体不带该键 ⇒ 服务端按 `custom` 预设装配
 * （0 / 1 / 0 + expectedHoldBars=None）⇒ 逐字等价于本层未装配。绝不在此填一组「看起来合理」的
 * 默认数字——那会让一个本应零副作用的选择静默改写成本模型的资金费期数（[P1-1]）。
 */
function seedHorizonCustom(rule: AiTradeHorizonRule | undefined) {
  return {
    minHoldRaw: numToRaw(rule?.minHoldSeconds),
    confirmRoundsRaw: numToRaw(rule?.exitConfirmRounds),
    hysteresisRaw: numToRaw(rule?.turnoverHysteresisK),
    targetMinHoursRaw: secondsToHoursRaw(rule?.targetHoldMinSeconds),
    targetMaxHoursRaw: secondsToHoursRaw(rule?.targetHoldMaxSeconds),
  };
}

function rawToNumber(raw: string): number | null {
  // 留空 ⇒ `null` ⇒ 整字段不发 ⇒ **服务端回填默认**（前端不存任何默认数值，唯一真源在 config.py）。
  // 非数字串同样回 `null`：输入中途的半截串不该被当成一个真值提交，也不该拦住启动。
  const text = raw.trim();
  if (!text) return null;
  const n = Number(text);
  return Number.isFinite(n) ? n : null;
}

function numToRaw(value: number | null | undefined): string {
  return value == null ? "" : String(value);
}

function secondsToHoursRaw(value: number | null | undefined): string {
  return value == null ? "" : String(+(value / 3600).toFixed(4));
}

/**
 * 「自定义」模式的逐项覆盖（非 custom 模式**一个覆盖都不发**）。
 *
 * 非 custom 模式的数字全部以服务端预设为准：前端多发一份等于把那张 11 列的表抄了一遍，而本仓库
 * 已经为「前后端各存一份口径」付过学费（R-3）。空串 ⇒ 该键缺席 ⇒ 服务端按预设回填。
 */
function horizonOverrides(profileId: AiTradeHorizonProfileId, custom: ReturnType<typeof seedHorizonCustom>) {
  if (profileId !== "custom") return {};
  const out: Partial<AiTradeHorizonRule> = {};
  if (custom.minHoldRaw.trim()) out.minHoldSeconds = Number(custom.minHoldRaw);
  if (custom.confirmRoundsRaw.trim()) out.exitConfirmRounds = Number(custom.confirmRoundsRaw);
  if (custom.hysteresisRaw.trim()) out.turnoverHysteresisK = Number(custom.hysteresisRaw);
  if (custom.targetMinHoursRaw.trim()) out.targetHoldMinSeconds = Number(custom.targetMinHoursRaw) * 3600;
  if (custom.targetMaxHoursRaw.trim()) out.targetHoldMaxSeconds = Number(custom.targetMaxHoursRaw) * 3600;
  return out;
}

/** 阶梯预设 → 阶梯数组（未知 key 回落标准预设，绝不静默提交一个空阶梯）。 */
function tiersForPreset(value: string): AiTradeGivebackTier[] {
  const hit = AI_TRADE_GIVEBACK_TIER_PRESETS.find((preset) => preset.value === value);
  return (hit ?? AI_TRADE_GIVEBACK_TIER_PRESETS[0]).tiers;
}

/** 阶梯数组 → 预设 key（回填上次偏好；无匹配预设时回落标准，UI 不显示一个不存在的选项）。 */
function matchTierPreset(tiers: AiTradeGivebackTier[] | null | undefined): string {
  if (tiers == null) return AI_TRADE_GIVEBACK_TIER_PRESETS[0].value;
  const key = (list: AiTradeGivebackTier[]) =>
    list.map((t) => `${t.peakReturnPct}:${t.givebackPct}`).join(",");
  const hit = AI_TRADE_GIVEBACK_TIER_PRESETS.find((preset) => key(preset.tiers) === key(tiers));
  return (hit ?? AI_TRADE_GIVEBACK_TIER_PRESETS[0]).value;
}

/**
 * 交易纪律预设分区：4 选 1 卡片 + 每张卡的**一句话后果说明**。
 *
 * 为什么必须明示：本轮之前，从这个弹窗启动的会话拿到的是「持有全额预算、没有波动率定标、没有回撤
 * 刹车、且明知手续费吃得掉预期收益也照开」的 LLM；本轮起默认套上「均衡」预设。这是唯一一处行为
 * 默认值的改变，静默生效会让用户在下一次「AI 怎么开单变少了」时无从对账。
 *
 * 刻意**不在这里显示任何阈值数字**：阈值的唯一真源是后端那张冻结表，前端抄一份必漂移；用户要核对
 * 具体数值时，启动回执的 `discipline.thresholds` 与统计页的纪律卡都会如实给出**生效值**。
 */
function DisciplinePresetFields({
  value,
  onChange,
  disabled,
  table,
  inertKeys = [],
}: {
  value: (typeof DISCIPLINE_PRESETS)[number];
  onChange: (next: (typeof DISCIPLINE_PRESETS)[number]) => void;
  disabled: boolean;
  table: AiTradeDisciplinePresetsResponse | null;
  /**
   * ★ 轴 A-4（loss-and-liveness-hardening）：在本次启动中「其消费层未装配因而**不作数**」的
   * 阈值键。判据的**权威真源是后端** `discipline.inert_thresholds()`（其产物随 `config_json`
   * 与 `group_status().discipline.inert` 下发）；启动**之前**服务端还没被问过，故这里按同一条
   * 判据在本地现算一次，`G-A6` 用例钉死两侧同值。
   */
  inertKeys?: string[];
}) {
  const [open, setOpen] = useState(false);
  const rows = table?.presets.find((p) => p.id === value)?.rows ?? [];
  return (
    <div className="flex flex-col gap-1.5" data-testid="ai-discipline-preset">
      <span className="text-[11px] text-[var(--muted)]">交易纪律</span>
      <div className="grid grid-cols-4 gap-1.5">
        {DISCIPLINE_PRESETS.map((preset) => (
          <button
            key={preset}
            type="button"
            disabled={disabled}
            aria-pressed={value === preset}
            onClick={() => onChange(preset)}
            className="rounded-md border border-[var(--border)] bg-[var(--bg)] px-2 py-1 text-[11px] text-[var(--text)] transition-colors hover:bg-[var(--surface-2)] aria-[pressed=true]:border-[var(--accent)] aria-[pressed=true]:text-[var(--accent-ink)] disabled:opacity-50"
          >
            {DISCIPLINE_COPY[preset].label}
            {preset === DEFAULT_DISCIPLINE_PRESET && (
              <span className="block text-[9px] text-[var(--muted)]">服务端默认</span>
            )}
          </button>
        ))}
      </div>
      <span className="text-[10px] leading-relaxed text-[var(--muted)]">
        {DISCIPLINE_COPY[value].detail}
      </span>
      {table && !table.enabled && (
        <span className="text-[10px] leading-relaxed text-[var(--short-ink)]">
          本机已由运维总闸整轴关闭（AI_TRADE_DISCIPLINE_ENABLED=false）：无论这里选哪一档，本次启动
          都不会装配纪律层。
        </span>
      )}
      {rows.length > 0 && (
        <>
          <button
            type="button"
            onClick={() => setOpen((v) => !v)}
            aria-expanded={open}
            className="self-start text-[10px] text-[var(--accent-ink)] underline-offset-2 hover:underline"
          >
            {open ? "收起阈值" : "查看这一档的阈值"}
          </button>
          {open && (
            <div
              className="grid grid-cols-2 gap-x-3 gap-y-0.5 rounded-md border border-[var(--border)] bg-[var(--bg)] px-2 py-1.5"
              data-testid="ai-discipline-threshold-table"
            >
              {rows.map((r) => {
                // ★ A-4：失效的那一行加删除线 + 尾注。**「显示了但不作数」是本轮要消灭的形态**
                // ——用户选了承诺硬止盈的预设，却因为另一个复选框而拿到一个没有执行者的承诺。
                const inert = inertKeys.includes(r.key);
                return (
                  <span
                    key={r.key}
                    className="flex items-center justify-between gap-2 text-[10px]"
                    data-testid={inert ? `ai-discipline-threshold-inert-${r.key}` : undefined}
                  >
                    <span className="truncate text-[var(--muted)]">
                      {r.label}
                      {inert && <span className="ml-1 text-[9px]">（未装配，不生效）</span>}
                    </span>
                    <span
                      className="tabular shrink-0"
                      style={{
                        color: r.off || inert ? "var(--muted)" : "var(--text)",
                        textDecoration: inert ? "line-through" : undefined,
                      }}
                    >
                      {r.off ? "关闭" : r.value}
                    </span>
                  </span>
                );
              })}
            </div>
          )}
        </>
      )}
      <span className="text-[10px] leading-relaxed text-[var(--muted)]">
        这组阈值控制仓位大小、开仓门槛、回撤刹车与连亏熔断，
        <strong className="text-[var(--text)]">尚未经回测校准</strong>
        ；启动后可在「统计 → 当前」的纪律卡里逐条核对生效值与本会话的拦截次数。
      </span>
    </div>
  );
}

function ModeTab({
  active,
  disabled,
  title,
  onClick,
  children,
}: {
  active: boolean;
  disabled?: boolean;
  title?: string;
  onClick: () => void;
  children: ReactNode;
}) {
  return (
    <button
      type="button"
      disabled={disabled}
      title={title}
      onClick={onClick}
      className={`flex-1 rounded px-3 py-1.5 text-xs font-medium transition-colors ${
        active
          ? "bg-[var(--surface)] text-[var(--text)] shadow-sm"
          : "text-[var(--muted)] hover:text-[var(--text)]"
      } ${disabled && !active ? "cursor-not-allowed opacity-40" : ""}`}
    >
      {children}
    </button>
  );
}

function AllocModeTab({
  active,
  disabled,
  onClick,
  children,
}: {
  active: boolean;
  disabled?: boolean;
  onClick: () => void;
  children: ReactNode;
}) {
  return (
    <button
      type="button"
      disabled={disabled}
      onClick={onClick}
      className={`rounded px-2 py-0.5 text-[10px] font-medium transition-colors ${
        active
          ? "bg-[var(--surface)] text-[var(--text)] shadow-sm"
          : "text-[var(--muted)] hover:text-[var(--text)]"
      } ${disabled && !active ? "cursor-not-allowed opacity-40" : ""}`}
    >
      {children}
    </button>
  );
}

function Row({ label, value }: { label: string; value: ReactNode }) {
  return (
    <div className="flex items-center justify-between gap-3 bg-[var(--bg)] px-3 py-2 text-xs">
      <span className="text-[var(--muted)]">{label}</span>
      <span className="tabular text-right font-medium text-[var(--text)]">{value}</span>
    </div>
  );
}

function DateField({
  label,
  value,
  onChange,
  disabled,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  disabled?: boolean;
}) {
  return (
    <label className="flex flex-col gap-1 text-[11px] text-[var(--muted)]">
      <span>{label}</span>
      <input
        type="datetime-local"
        value={value}
        disabled={disabled}
        onChange={(e) => onChange(e.target.value)}
        className="rounded-md border border-[var(--border)] bg-[var(--bg)] px-2 py-1.5 text-xs text-[var(--text)] outline-none focus:border-[var(--accent)] disabled:opacity-50"
      />
    </label>
  );
}

function Field({
  label,
  placeholder,
  value,
  onChange,
  disabled,
  hint,
}: {
  label: string;
  placeholder: string;
  value: string;
  onChange: (v: string) => void;
  disabled?: boolean;
  hint?: string;
}) {
  return (
    <label className="flex flex-col gap-1 text-[11px] text-[var(--muted)]">
      <span>{label}</span>
      <input
        type="text"
        inputMode="decimal"
        value={value}
        placeholder={placeholder}
        disabled={disabled}
        onChange={(e) => onChange(e.target.value)}
        // 显式 aria-label：本组件的 <label> 还包着可选 hint 文本，仅靠包裹关系解析出的可访问名会被 hint
        // 污染（既影响读屏也影响按标签取元素），故以字段名为准显式声明。
        aria-label={label}
        className="rounded-md border border-[var(--border)] bg-[var(--bg)] px-2 py-1.5 text-xs text-[var(--text)] outline-none focus:border-[var(--accent)] disabled:opacity-50"
      />
      {hint && <span className="leading-relaxed text-[var(--muted)]">{hint}</span>}
    </label>
  );
}
