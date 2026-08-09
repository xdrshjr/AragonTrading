// AI 自动交易纯件（无副作用、易单测）：开关守门文案矩阵、策略/轮数/预算校验、动作与停止原因中文化。
// 预算派生/夹紧复用 live-auto 同款（0.01 默认 / 0.05 上限，与后端 ai_trade_default/max_capital_frac 对齐）。
import { clampBudgetToCap, deriveDefaultBudget } from "./liveAuto";
import type { AccountMode } from "./types";
import type {
  AiTradeAction,
  AiTradeDecision,
  AiTradeGivebackTier,
  AiTradeProfitGuardRule,
  AiTradeRoundTrace,
  AiTradeStopReason,
  Consensus,
  CandidateLevel,
  FactorReading,
  FactorReadings,
  MarketContext,
  MultiTimeframeFrame,
  PriceAction,
  ProfitGuardCooldown,
  ProfitGuardSnapshot,
  TacticalFrame,
  VolumeContext,
  WakeInfo,
  WakeKind,
} from "./aiTradeTypes";

export { clampBudgetToCap, deriveDefaultBudget };

// 上下文刷新轮数区间（与后端 ai_trade_min/max_context_rounds 对齐；默认 10）。
export const AI_TRADE_MIN_CONTEXT_ROUNDS = 3;
export const AI_TRADE_MAX_CONTEXT_ROUNDS = 200;
export const AI_TRADE_DEFAULT_CONTEXT_ROUNDS = 10;
export const AI_TRADE_MAX_STRATEGIES = 8;
// AI 自动交易「分组」标的数上限（与后端 ai_trade_max_symbols=5 对齐）：弹窗回填 autoTradeMembers 时至多取
// 前 N 个（超出提示并禁增，P1-2）。刻意 < live_auto_max_group_symbols(8)：N 个 decider 每轮各一次 LLM 调用。
export const AI_TRADE_MAX_SYMBOLS = 5;
// 单仓 OCO 比例夹紧上限（fraction，与后端 ai_trade_max_sl_pct/tp_pct 对齐，E4 弹窗预览用）。
export const AI_TRADE_MAX_SL_PCT = 0.2;
export const AI_TRADE_MAX_TP_PCT = 0.5;

// ---- Agent 墙钟定时唤醒周期（ai-auto-trading-wake-interval §2.7）----
// 在既有「确认 K 线 / 策略触发 → 决策」之外叠加第三个唤醒源：墙钟定时唤醒。默认 2 分钟、用户可设任意分钟；
// 区间与后端 ai_trade_wake_interval_floor_s(30s=0.5min) / ceil_min(240) 对齐（前端越界即时报错、后端防御性夹紧）。
export const AI_TRADE_DEFAULT_WAKE_INTERVAL_MIN = 2;
export const AI_TRADE_WAKE_INTERVAL_FLOOR_MIN = 0.5;
export const AI_TRADE_WAKE_INTERVAL_CEIL_MIN = 240;

// ---- 仓位操作档位（ai-trade-position-sizing-steps §2.1/§4.4）----
// 用户在启动弹窗选择离散档位步长；AI 每次开仓的 sizeFraction 被服务端量化吸附到档位阶梯上（硬保证在
// signals.quantize_size_fraction）。默认 0.25 与后端 ai_trade_default_position_step_pct 对齐。
export const AI_TRADE_DEFAULT_POSITION_STEP = 0.25;

// 弹窗预设档位（[R-6] 仅 10/20/25/50/100%，**不含**「连续/AI 自由」——需求明确判定该行为错误）。
// value 为 fraction 步长；连续（step=0）仅作后端零回归默认 + API 退出口，不在此露出。
export const AI_TRADE_POSITION_STEP_OPTIONS: { value: number; label: string }[] = [
  { value: 0.1, label: "每档 10%（10/20/…/100%）" },
  { value: 0.2, label: "每档 20%（20/40/…/100%）" },
  { value: 0.25, label: "每档 25%（25/50/75/100%）" },
  { value: 0.5, label: "每档 50%（50/100%）" },
  { value: 1, label: "每次全仓（100%）" },
];

// ---- 【基础策略】铁律层（ai-trade-base-strategy-ma-band §三 前端）----
// 与后端 ai_trade_base_rule_* 默认值对齐；弹窗初值与所有读点的兜底真源（[R-7] 偏好键为可选）。
export const AI_TRADE_BASE_RULE_DEFAULTS: {
  enabled: boolean;
  period: number;
  stdMult: number;
  tolerance: number;
  directional: boolean;
} = { enabled: true, period: 20, stdMult: 2, tolerance: 0.15, directional: false };

// 「附近」的容差档位（%B 带宽归一，跨周期同义）。0.25 是**退化点**——三锚区首尾相接后规则等价于
// 「只要在带内即可开单」，故文案直书「仅限带内（最宽松）」，且后端锁死上界不接受更大值（R-4）。
export const AI_TRADE_BAND_TOLERANCE_OPTIONS: { value: number; label: string }[] = [
  { value: 0.05, label: "极严（贴轨 ±5% 带宽）" },
  { value: 0.1, label: "严格（±10% 带宽）" },
  { value: 0.15, label: "标准（默认，±15% 带宽）" },
  { value: 0.2, label: "宽松（±20% 带宽）" },
  { value: 0.25, label: "仅限带内（最宽松）" },
];

// ---- 利润保护层（ai-trade-profit-protection §三 前端）----
// 与后端 ai_trade_profit_guard_* 默认值对齐；弹窗初值与所有读点的兜底真源（偏好键为可选）。
// 全部收益率为**保证金收益率口径**：诊断根因 ② —— 5× 杠杆下标的 10% 等于保证金 +50%，
// 用标的口径写阈值，普通的小幅盈利根本触发不到。
export const AI_TRADE_PROFIT_GUARD_DEFAULTS: {
  enabled: boolean;
  armReturnPct: number;
  givebackPct: number;
  breakevenArmReturnPct: number;
  breakevenOffsetReturnPct: number;
  minHoldSeconds: number;
  hardTakeProfitReturnPct: number;
  givebackTiers: AiTradeGivebackTier[];
  atrFloorMult: number;
  atrCapMult: number;
  cooldownSeconds: number;
  cooldownScope: "same_side" | "both";
} = {
  enabled: true,
  armReturnPct: 0.05,
  givebackPct: 0.4,
  breakevenArmReturnPct: 0.03,
  breakevenOffsetReturnPct: 0.002,
  minHoldSeconds: 30,
  hardTakeProfitReturnPct: 0,
  // 与后端 `ai_trade_profit_guard_*` 的默认逐值对齐（弹窗只是回填，真源仍在服务端 config）。
  givebackTiers: [
    { peakReturnPct: 0.1, givebackPct: 0.35 },
    { peakReturnPct: 0.25, givebackPct: 0.28 },
    { peakReturnPct: 0.5, givebackPct: 0.2 },
  ],
  atrFloorMult: 0.8,
  atrCapMult: 3,
  cooldownSeconds: 180,
  cooldownScope: "same_side",
};

/** 回吐阶梯预设（弹窗下拉；`[]` = 关闭阶梯，退回固定回吐比例）。 */
export const AI_TRADE_GIVEBACK_TIER_PRESETS: {
  value: string;
  label: string;
  tiers: AiTradeGivebackTier[];
}[] = [
  { value: "standard", label: "标准（10%→35%／25%→28%／50%→20%）", tiers: AI_TRADE_PROFIT_GUARD_DEFAULTS.givebackTiers },
  {
    value: "tight",
    label: "激进落袋（10%→28%／25%→20%／50%→12%）",
    tiers: [
      { peakReturnPct: 0.1, givebackPct: 0.28 },
      { peakReturnPct: 0.25, givebackPct: 0.2 },
      { peakReturnPct: 0.5, givebackPct: 0.12 },
    ],
  },
  {
    value: "loose",
    label: "宽松持有（25%→35%／50%→30%）",
    tiers: [
      { peakReturnPct: 0.25, givebackPct: 0.35 },
      { peakReturnPct: 0.5, givebackPct: 0.3 },
    ],
  },
  { value: "off", label: "关闭阶梯（始终用固定回吐比例）", tiers: [] },
];

const AI_TRADE_STEP_EPS = 1e-9;

/**
 * 档位阶梯（[R-5] 后端 signals.position_ladder 的等价前端镜像，预览文案用）：`{k·step | k=1..maxGears}`，
 * 顶格 ≤ 100%。`step<=0` → `[]`（连续/不约束）。`maxGears=floor((1+eps)/step)` 保证顶格不越 100%。
 */
export function positionLadder(step: number): number[] {
  if (step <= 0) return [];
  const maxGears = Math.floor((1 + AI_TRADE_STEP_EPS) / step);
  const ladder: number[] = [];
  for (let k = 1; k <= maxGears; k += 1) ladder.push(Number((k * step).toFixed(6)));
  return ladder;
}

// 开关守门输入（不含策略数——策略在启动弹窗内多选；运行态开关恒可点用于停止）。
export interface AiTradeGateInput {
  accountMode: AccountMode;
  hasReadyDefault: boolean;
  aiTradeEnabled: boolean;
  agentConfigured: boolean;
  liveAutoActive: boolean;
  hasActiveSession: boolean;
}

/** 守门文案矩阵：返回禁用原因（悬浮提示），全部通过返回 null。运行态恒返回 null（开关用于停止）。 */
export function resolveAiTradeGate(input: AiTradeGateInput): string | null {
  if (input.hasActiveSession) return null;
  if (input.accountMode !== "live") return "请先切换到实盘账户模式";
  if (!input.hasReadyDefault) return "请先在设置中配置默认实盘账户";
  if (!input.aiTradeEnabled) return "AI 自动交易未启用（需后端开启 AI_TRADE_ENABLED）";
  if (!input.agentConfigured) return "请先在设置中心配置 AI 模型";
  if (input.liveAutoActive) return "已有活跃策略自动交易分组，请先停止（与 AI 互斥）";
  return null;
}

/** 校验多选策略：至少 1 个、至多上限；返回错误文案或 null。 */
export function validateStrategySelection(
  ids: number[],
  max: number = AI_TRADE_MAX_STRATEGIES,
): string | null {
  if (ids.length === 0) return "请至少选择一个交易策略";
  if (ids.length > max) return `最多选择 ${max} 个策略，请减少勾选`;
  return null;
}

/** 校验上下文刷新轮数：须为区间内整数；返回错误文案或 null。 */
export function validateContextRounds(
  rounds: number,
  min: number = AI_TRADE_MIN_CONTEXT_ROUNDS,
  max: number = AI_TRADE_MAX_CONTEXT_ROUNDS,
): string | null {
  if (!Number.isInteger(rounds) || rounds < min || rounds > max) {
    return `上下文刷新轮数需为 ${min}~${max} 的整数`;
  }
  return null;
}

/**
 * 校验 Agent 墙钟定时唤醒周期（分钟）：空（null）/0 = 关闭定时唤醒，合法；否则须为 [min, max] 内有限数。
 * 前端越界即时报错（与 validateContextRounds 前端报错范式一致），后端 _resolve_wake_interval_s 另做防御性
 * 夹紧——二者区间一致、职责互补（前端给即时反馈、后端兜边界），RN-9。返回错误文案或 null。
 */
export function validateWakeInterval(
  minutes: number | null,
  min: number = AI_TRADE_WAKE_INTERVAL_FLOOR_MIN,
  max: number = AI_TRADE_WAKE_INTERVAL_CEIL_MIN,
): string | null {
  if (minutes == null || minutes === 0) return null; // 空/0 = 关闭，合法
  if (!Number.isFinite(minutes) || minutes < min || minutes > max) {
    return `Agent 唤醒周期需为 ${min}~${max} 分钟（留空或 0 表示关闭）`;
  }
  return null;
}

const ACTION_LABELS: Record<AiTradeAction, string> = {
  open_long: "开多",
  open_short: "开空",
  close: "平仓",
  hold: "观望",
  cancel_order: "撤单",
};

/** AI 决策动作中文化（open_long→「开多」…）。 */
export function describeAiAction(action: string): string {
  return ACTION_LABELS[action as AiTradeAction] ?? action;
}

const ORDER_TYPE_LABELS: Record<string, string> = { market: "市价", limit: "限价" };

/**
 * 委托方式中文化（ai-trade-limit-orders §2.2）：market→「市价」、limit→「限价」。
 * 缺省（旧快照无此键 / 总闸关闭）视同市价；未知取值缺省回原值（不臆造，同 describeRegime）。
 */
export function describeOrderType(orderType: string | null | undefined): string {
  if (!orderType) return ORDER_TYPE_LABELS.market;
  return ORDER_TYPE_LABELS[orderType] ?? orderType;
}

const STANCE_LABELS: Record<string, string> = { long: "多", short: "空", flat: "空仓" };

/** 影子立场中文化（long→「多」，short→「空」，flat→「空仓」）。 */
export function describeStance(stance: string): string {
  return STANCE_LABELS[stance] ?? stance;
}

const STOP_REASON_LABELS: Record<AiTradeStopReason, string> = {
  user_stop: "手动停止",
  session_stop_loss: "触发会话止损",
  session_take_profit: "触发会话止盈",
  shutdown: "服务重启",
  stale_feed: "行情中断保护",
  start_failed: "启动失败",
};

/** 停止原因中文化（缺省回「已停止」）。 */
export function describeStopReason(reason: string | null | undefined): string {
  if (!reason) return "已停止";
  return STOP_REASON_LABELS[reason as AiTradeStopReason] ?? "已停止";
}

// 市场体制真实枚举 → 中文（R-P1-2：映射 engine/regime 真值，缺省回原值不臆造）。
const REGIME_PRIMARY_LABELS: Record<string, string> = {
  trend_up: "上升趋势",
  trend_down: "下降趋势",
  range: "震荡",
};
const REGIME_VOLATILITY_LABELS: Record<string, string> = {
  high_volatility: "高波动",
  low_volatility: "低波动",
  normal: "常波动",
};
const REGIME_EVENT_LABELS: Record<string, string> = {
  breakout: "突破",
  crash: "急跌",
};

/**
 * 市场体制上下文中文化（E1）：`趋势结构 · 波动 · 事件`（如「上升趋势 · 高波动 · 突破」）。
 * 事件为 none 时省略；任一未知枚举缺省回原值（不臆造，R-P1-2）；空上下文返回空串。
 */
export function describeRegime(mc: MarketContext | null | undefined): string {
  if (!mc) return "";
  const parts = [
    REGIME_PRIMARY_LABELS[mc.primary] ?? mc.primary,
    REGIME_VOLATILITY_LABELS[mc.volatility] ?? mc.volatility,
  ];
  if (mc.event && mc.event !== "none") {
    parts.push(REGIME_EVENT_LABELS[mc.event] ?? mc.event);
  }
  return parts.filter(Boolean).join(" · ");
}

/**
 * 策略共识度中文化（E2）：`<N>多/<M>空/<K>空仓 · 一致度 <p>%`（如「3多/1空/1空仓 · 一致度 60%」）。
 * 空共识返回空串。
 */
export function describeConsensus(consensus: Consensus | null | undefined): string {
  if (!consensus) return "";
  const pct = Math.round((consensus.agreement ?? 0) * 100);
  return `${consensus.longVotes}多/${consensus.shortVotes}空/${consensus.flatVotes}空仓 · 一致度 ${pct}%`;
}

// ---- R1 市场证据摘要中文化（镜像 describeRegime：空→""、未知枚举缺省回原值不臆造）----
const TREND_LABELS: Record<string, string> = { up: "上行", down: "下行", flat: "横盘" };
const EMA_RELATION_LABELS: Record<string, string> = { fast_above: "多头排列", fast_below: "空头排列" };
const VOL_TREND_LABELS: Record<string, string> = { rising: "放量", falling: "缩量", flat: "平稳" };
// 因子定性枚举 → 中文（RSI/MACD/布林/ATR/动量共用一张表；缺省回原值）。
const FACTOR_LABELS: Record<string, string> = {
  overbought: "超买",
  oversold: "超卖",
  neutral: "中性",
  bullish: "偏多",
  bearish: "偏空",
  upper_break: "上破",
  lower_break: "下破",
  upper_half: "上半区",
  lower_half: "下半区",
  high: "偏高",
  low: "偏低",
  normal: "常态",
  positive: "正向",
  negative: "负向",
};

/**
 * 价格走势证据中文化（R1）：`价格 · 走势 上行(+1.2%) · 多头排列 · 区间位 78%`。
 * 空→""；未知 trend/emaRelation 缺省回原值；rangePos 为 null 时省略该段。
 */
export function describePriceAction(pa: PriceAction | null | undefined): string {
  if (!pa) return "";
  const trend = TREND_LABELS[pa.trend] ?? pa.trend;
  const sign = pa.changePct > 0 ? "+" : "";
  const parts = [`走势 ${trend}(${sign}${pa.changePct}%)`];
  if (pa.emaRelation) parts.push(EMA_RELATION_LABELS[pa.emaRelation] ?? pa.emaRelation);
  if (pa.rangePos != null) parts.push(`区间位 ${Math.round(pa.rangePos * 100)}%`);
  return `价格 · ${parts.join(" · ")}`;
}

/**
 * 成交量证据中文化（R1）：`成交量 · 放量 · 量比 1.8 · 异常放量`。
 * 空→""；未知 trend 缺省回原值；量比 null 时省略；spike 时追加「异常放量」。
 */
export function describeVolume(vol: VolumeContext | null | undefined): string {
  if (!vol) return "";
  const trend = VOL_TREND_LABELS[vol.trend] ?? vol.trend;
  const parts = [`量能 ${trend}`];
  if (vol.ratio != null) parts.push(`量比 ${vol.ratio}`);
  if (vol.spike) parts.push("异常放量");
  return `成交量 · ${parts.join(" · ")}`;
}

function describeFactorReading(name: string, r: FactorReading | undefined): string | null {
  if (!r) return null;
  return `${name} ${FACTOR_LABELS[r.label] ?? r.label}`;
}

/**
 * 技术因子读数中文化（R1）：`因子 · RSI 中性 · MACD 偏多 · 布林 上半区 · ATR 常态 · 动量 正向`。
 * 空→""；未知 label 缺省回原值。
 */
export function describeFactors(factors: FactorReadings | null | undefined): string {
  if (!factors) return "";
  const parts = [
    describeFactorReading("RSI", factors.rsi14),
    describeFactorReading("MACD", factors.macdHist),
    describeFactorReading("布林", factors.bollPctB),
    describeFactorReading("ATR", factors.atrPct),
    describeFactorReading("动量", factors.rocPct),
  ].filter((p): p is string => Boolean(p));
  return parts.length ? `因子 · ${parts.join(" · ")}` : "";
}

const MTF_ALIGNMENT_LABELS: Record<string, string> = {
  bullish: "整体偏多",
  bearish: "整体偏空",
  mixed: "多空混合",
  range: "整体震荡",
  insufficient: "证据不足",
};

/** 多周期趋势汇总中文化；未知枚举原样回显。 */
export function describeMultiTimeframeAlignment(status: string): string {
  return MTF_ALIGNMENT_LABELS[status] ?? status;
}

/** 候选支撑/阻力中文化；空值明确显示暂无。 */
export function describeCandidateLevel(level: CandidateLevel | null | undefined): string {
  if (!level) return "暂无";
  const bars = level.bars.length ? ` · ${level.bars.join("/")}` : "";
  return `${level.price} · 距现价 ${level.distancePct}%${bars}`;
}

/** 单个决策背景周期的只读证据摘要。 */
export function describeMultiTimeframeFrame(frame: MultiTimeframeFrame): string {
  const primary = frame.regime?.primary;
  const regime = primary ? (REGIME_PRIMARY_LABELS[primary] ?? primary) : "体制不足";
  const rsi = frame.digest?.factors?.rsi14;
  const rsiText = rsi ? (FACTOR_LABELS[rsi.label] ?? rsi.label) : "RSI不足";
  const rangePos = frame.digest?.priceAction?.rangePos;
  const rangeText = rangePos == null ? "" : ` · 区间位 ${Math.round(rangePos * 100)}%`;
  const stale = frame.stale ? " · 数据陈旧" : "";
  return `${frame.bar} · ${regime} · ${rsiText}${rangeText} · ${frame.sampleCount} 根${stale}`;
}

// ---- 唤醒感知上下文（ai-trade-wake-aware-context §3.3）----
const TACTICAL_ALIGNMENT_LABELS: Record<string, string> = {
  aligned: "与执行周期同向",
  diverged: "与执行周期背离",
  insufficient: "体制证据不足",
};

/** 战术周期与执行周期的体制关系中文化；未知枚举原样回显。 */
export function describeTacticalAlignment(alignment: string): string {
  return TACTICAL_ALIGNMENT_LABELS[alignment] ?? alignment;
}

/**
 * 单个战术周期的只读证据摘要：`1m · 震荡 · RSI中性 · 自上轮收 3 根 · 120 根`。
 * 与 describeMultiTimeframeFrame 视觉同构（同一套体制/因子标签表），只把「区间位」换成
 * 「自上轮收 N 根」——后者才是战术层真正逐轮变化、也是本特性存在理由的那个量。
 */
export function describeTacticalFrame(frame: TacticalFrame): string {
  const primary = frame.regime?.primary;
  const regime = primary ? (REGIME_PRIMARY_LABELS[primary] ?? primary) : "体制不足";
  const rsi = frame.digest?.factors?.rsi14;
  const rsiText = rsi ? (FACTOR_LABELS[rsi.label] ?? rsi.label) : "RSI不足";
  const closed = frame.closedBars == null ? "" : ` · 自上轮收 ${frame.closedBars} 根`;
  const stale = frame.stale ? " · 数据陈旧" : "";
  return `${frame.bar} · ${regime} · ${rsiText}${closed} · ${frame.sampleCount} 根${stale}`;
}

/**
 * 唤醒原因的判据明细（轮头 chip 之外的展开位）：把判据的**实际数值**如实呈现。
 *
 * 阈值取折算后的 `moveThresholdAtrEff` 而非配置里的 `moveThresholdAtr`——写「0.5×ATR」而实际按
 * 0.158 判，就是新造一次「说的和做的不一致」。无任何数值可讲时返回空串（轮头 chip 已经给了标签，
 * 再渲染一行同样的文字只是噪音）。
 */
export function describeWakeReason(wake: WakeInfo | null | undefined): string {
  if (!wake) return "";
  const bar = wake.executionBar ?? "执行周期";
  const details: string[] = [];
  if (wake.sinceLastRoundS != null) details.push(`距上轮 ${Math.round(wake.sinceLastRoundS)}s`);
  if (wake.priceMovePct != null) {
    const atr = wake.priceMoveAtr == null ? "" : `（≈${wake.priceMoveAtr.toFixed(2)}×${bar} ATR）`;
    details.push(`价 ${wake.priceMovePct > 0 ? "+" : ""}${wake.priceMovePct.toFixed(2)}%${atr}`);
  }
  if (wake.moveThresholdAtrEff != null) {
    details.push(`阈值 ${wake.moveThresholdAtrEff.toFixed(2)}×${bar} ATR`);
  }
  if (wake.restingAgeS != null) details.push(`挂单已 ${Math.round(wake.restingAgeS)}s`);
  if (!details.length) return "";
  return [WAKE_LABELS[wake.kind]?.label ?? wake.kind, ...details].join(" · ");
}

export type AiExecTone = "long" | "short" | "muted";
export type AiTraceTone = AiExecTone | "accent";

export interface AiTraceStateChip {
  label: string; // 研判中… / 已执行 / 已决策 / 降级观望 / 已跳过 / 异常
  tone: AiTraceTone;
}

/**
 * 轮次状态 chip（轨迹轮头右上角）：把已装配轮次的 state + 执行/降级归一为一枚状态徽标。
 * 优先级：异常 > 已跳过 > 研判中（deciding）> 降级观望（degradeReason）> 已执行（traded，按方向着色）> 已决策。
 */
export function describeTraceState(trace: {
  state: string;
  toolCall?: { action?: string } | null;
  executed?: AiTradeDecision["executed"];
  degradeReason?: string | null;
}): AiTraceStateChip {
  if (trace.state === "error") return { label: "异常", tone: "short" };
  if (trace.state === "skipped") return { label: "已跳过", tone: "muted" };
  if (trace.state === "deciding") return { label: "研判中…", tone: "accent" };
  if (trace.degradeReason) return { label: "降级观望", tone: "short" };
  if (trace.executed?.traded) {
    const action = trace.toolCall?.action;
    const tone: AiTraceTone =
      action === "open_long" ? "long" : action === "open_short" ? "short" : "muted";
    return { label: "已执行", tone };
  }
  return { label: "已决策", tone: "muted" };
}

// 降级原因的中文前缀映射（ai-trade-base-strategy-ma-band P1-9）：键为**码前缀**，因为基础策略的码带
// 参数后缀（`base_rule_ma_band(pctB=0.42)`，与既有 `low_consensus(k/n)` 同风格）。
const DEGRADE_REASON_LABELS: { prefix: string; label: string }[] = [
  { prefix: "base_rule_ma_band_direction", label: "基础策略否决：方向与所处均线锚区不符" },
  { prefix: "base_rule_band_unavailable", label: "基础策略否决：均线通道数据不足，无法验证开单位置" },
  { prefix: "base_rule_ma_band", label: "基础策略否决：开单价不在均线通道上/中/下轨附近" },
  // 利润保护三码：它们不是「降级」而是**系统主动平仓**的成交原因，但同样会出现在成交列表的原因位上。
  // §六.1b：**不写「已锁定利润」**——码本身不含盈亏符号，保本线触发 / 滑点不利时这笔可能打平甚至
  // 净亏；具体金额由成交行的 pnl 呈现，这里只说触发了什么规则。
  { prefix: "profit_guard_trailing", label: "利润保护：浮盈自峰值回吐超阈，已按保护线离场" },
  { prefix: "profit_guard_breakeven", label: "利润保护：价格跌回保本线，已离场保住本金" },
  { prefix: "profit_guard_hard_take_profit", label: "利润保护：已达硬止盈目标位" },
  // 承诺退出线（commitment C2/C3）：模型自己许下、经棘轮兑现的第三条保护线触发平仓。
  { prefix: "profit_guard_committed_exit", label: "利润保护：已触及你承诺的退出线，按承诺离场" },
  // 冷却降级码带参数后缀（profit_cooldown(120s,long)），与基础策略同为**前缀**匹配。
  { prefix: "profit_cooldown", label: "利润保护冷却：刚锁住利润，暂停同向开仓" },
  // 最小 edge 门禁（ai-trade-cost-accounting-and-edge-gate §3.7）：码带参数后缀
  // `edge_below_cost(edge=8.1bps,cost=13.0bps,k=2.0)`，同为前缀匹配、参数原样保留供排查。
  { prefix: "edge_below_cost", label: "期望收益不足以覆盖交易成本，本轮观望" },
  // 回撤刹车两码（vol-sizing-and-drawdown-brake §3.6）：同为带参数后缀的前缀码。文案必须写清**只拦
  // 开新仓**——用户看到「暂停」时最怕的是「我的仓位是不是也平不掉了」。
  { prefix: "drawdown_brake_halt", label: "回撤刹车：会话回撤越线，暂停开新仓（平仓不受影响）" },
  { prefix: "loss_streak_cooldown", label: "连亏熔断：连续亏损达阈，冷却期内暂停开新仓（平仓不受影响）" },
  // 持仓周期铁律三码（ai-trade-holding-horizon-modes §2.3.2）：同为带参数后缀的前缀码。文案必须写清
  // **只拦 AI 主动换手**——用户看到「不接受平仓」时最怕的是「我的止损是不是也失效了」。
  {
    prefix: "horizon_min_hold",
    label: "交易周期：未满最短持仓窗口，本轮不接受 AI 主动平仓（止损/止盈/利润保护层不受影响）",
  },
  {
    prefix: "horizon_exit_unconfirmed",
    label: "交易周期：离场理由需连续确认若干轮，本轮尚未满足（确认窗口到期即放行）",
  },
  {
    prefix: "horizon_turnover_hysteresis",
    label: "交易周期：浮盈尚未覆盖往返成本，获利了结推迟（浮亏离场不受此限）",
  },
];

/**
 * 降级原因中文化（P1-9）：裸英文码会直接怼到用户脸上，而基础策略是**高频**触发项（震荡市里大多数轮
 * 都可能被否）。命中前缀 → 中文说明（保留原始括号内参数供排查）；**未命中一律原样回退**——既有
 * `low_consensus(1/2)` / `decision_timeout` 等断言与展示逐字不变。
 */
export function describeDegradeReason(reason?: string | null): string {
  if (!reason) return "";
  const hit = DEGRADE_REASON_LABELS.find((item) => reason.startsWith(item.prefix));
  if (!hit) return reason;
  const suffix = reason.slice(hit.prefix.length);
  return suffix ? `${hit.label}${suffix}` : hit.label;
}

/**
 * 校验利润保护参数：区间与后端 schema 的 Field 边界一致（前端即时报错，后端越界 422），返回错误文案或 null。
 * `enabled=false` 时其余输入被忽略，一律合法——关闭态的残留值不该阻塞提交。
 */
export function validateProfitGuard(rule: AiTradeProfitGuardRule): string | null {
  if (!rule.enabled) return null;
  const arm = rule.armReturnPct ?? AI_TRADE_PROFIT_GUARD_DEFAULTS.armReturnPct;
  const giveback = rule.givebackPct ?? AI_TRADE_PROFIT_GUARD_DEFAULTS.givebackPct;
  const breakeven = rule.breakevenArmReturnPct ?? AI_TRADE_PROFIT_GUARD_DEFAULTS.breakevenArmReturnPct;
  const minHold = rule.minHoldSeconds ?? AI_TRADE_PROFIT_GUARD_DEFAULTS.minHoldSeconds;
  if (!Number.isFinite(arm) || arm <= 0 || arm > 5) return "武装阈值需为 0~500% 的保证金收益率";
  if (!Number.isFinite(giveback) || giveback <= 0 || giveback > 1) {
    return "允许回吐比例需为 0~100%";
  }
  if (!Number.isFinite(breakeven) || breakeven < 0 || breakeven > 5) {
    return "保本武装阈值需为 0~500%（0 表示关闭保本）";
  }
  if (!Number.isFinite(minHold) || minHold < 0 || minHold > 3600) {
    return "最短持有需为 0~3600 秒";
  }
  return validateProfitGuardAdaptive(rule);
}

/**
 * 自适应参数校验（阶梯单调性 / ATR 上下界 / 冷却秒），与后端 `AiTradeProfitGuardIn` 的 422 条件一致。
 *
 * 阶梯的两条硬约束（阈值严格递增 + 回吐比例单调不增）不是风格偏好：「赚得越多却允许回吐得越多」
 * 正是本轮要修的 bug 本身，让它通过校验等于把 bug 写进配置。
 */
function validateProfitGuardAdaptive(rule: AiTradeProfitGuardRule): string | null {
  const tiers = rule.givebackTiers ?? [];
  if (tiers.length > 6) return "回吐阶梯最多 6 档";
  let prevThreshold = 0;
  let prevGiveback: number | null = null;
  for (const tier of tiers) {
    if (!Number.isFinite(tier.peakReturnPct) || tier.peakReturnPct <= prevThreshold) {
      return "回吐阶梯的峰值收益阈值需严格递增且首档大于 0";
    }
    if (!Number.isFinite(tier.givebackPct) || tier.givebackPct <= 0 || tier.givebackPct > 1) {
      return "回吐阶梯的回吐比例需为 0~100%";
    }
    if (prevGiveback != null && tier.givebackPct > prevGiveback) {
      return "回吐阶梯的回吐比例需单调不增（赚得越多不可允许回吐更多）";
    }
    prevThreshold = tier.peakReturnPct;
    prevGiveback = tier.givebackPct;
  }
  const floorMult = rule.atrFloorMult ?? AI_TRADE_PROFIT_GUARD_DEFAULTS.atrFloorMult;
  const capMult = rule.atrCapMult ?? AI_TRADE_PROFIT_GUARD_DEFAULTS.atrCapMult;
  if (!Number.isFinite(floorMult) || floorMult < 0 || floorMult > 10) {
    return "ATR 下界倍数需为 0~10（0 表示关闭下界）";
  }
  if (!Number.isFinite(capMult) || capMult < 0 || capMult > 50) {
    return "ATR 上界倍数需为 0~50（0 表示关闭上界）";
  }
  if (floorMult > 0 && capMult > 0 && floorMult > capMult) {
    return "ATR 下界倍数不可大于上界倍数";
  }
  const cooldown = rule.cooldownSeconds ?? AI_TRADE_PROFIT_GUARD_DEFAULTS.cooldownSeconds;
  if (!Number.isFinite(cooldown) || cooldown < 0 || cooldown > 86400) {
    return "冷却秒数需为 0~86400 秒（0 表示关闭冷却）";
  }
  return null;
}

export interface AiProfitGuardView {
  label: string;
  tone: AiTraceTone;
}

/**
 * 保护层状态中文化（单点真源，判定 / prompt / UI 共用同一份语义）。未装配（快照缺失）→ null，
 * 组件整段不渲染。`unavailable` 用 short（红）——「保护层此刻不生效」必须显眼，静默的失灵最危险。
 */
export function describeProfitGuardStatus(
  snapshot: ProfitGuardSnapshot | null | undefined,
): AiProfitGuardView | null {
  if (!snapshot) return null;
  switch (snapshot.status) {
    case "unavailable":
      return { label: `利润保护暂不可用（${describeProfitGuardUnavailable(snapshot)}）`, tone: "short" };
    case "armed":
      return { label: "利润保护 · 移动止盈已武装", tone: "long" };
    case "breakeven_armed":
      return { label: "利润保护 · 保本线已武装", tone: "accent" };
    case "flat":
      return null;
    default:
      return { label: "利润保护 · 未武装", tone: "muted" };
  }
}

const PROFIT_GUARD_UNAVAILABLE_LABELS: Record<string, string> = {
  stale_feed: "行情数据陈旧",
  no_price: "实时价不可用",
  bad_position: "持仓读数异常",
};

/**
 * 保护层不可用时的**兜底责任归属**文案（单点真源，与后端 `_format_unavailable_fallback` 同语义）。
 *
 * 只有交易所侧确实挂着 OCO 才写「仍生效」：运维把 `AI_TRADE_DEFAULT_SL_PCT`/`_TP_PCT` 置 0 时后端
 * 根本不挂单，此时照写「仅交易所侧止盈止损生效」等于在保护层已停手的那一刻，告诉用户还有一条**并不
 * 存在**的防线——既没有保护层又以为有兜底，是最坏的一种失灵。
 */
export function describeProfitGuardFallback(
  snapshot: ProfitGuardSnapshot | null | undefined,
): string {
  const hasOco =
    snapshot?.exchangeTakeProfitPx != null || snapshot?.exchangeStopLossPx != null;
  return hasOco
    ? "本轮不会自动平仓，仅交易所侧止盈止损仍生效"
    : "本轮不会自动平仓，且交易所侧未挂止盈止损——此刻没有任何自动兜底";
}

/** 不可用原因中文化；未知码回落「数据异常」（绝不显示裸英文）。 */
export function describeProfitGuardUnavailable(
  snapshot: ProfitGuardSnapshot | null | undefined,
): string {
  const reason = snapshot?.unavailableReason ?? "";
  return PROFIT_GUARD_UNAVAILABLE_LABELS[reason] ?? "数据异常";
}

const TRAIL_BASIS_SUFFIX: Record<string, string> = {
  giveback: "",
  atr_floor: "（已按波动率放宽至下界）",
  atr_cap: "（已按波动率收紧至上界）",
  clamped_breakeven: "（已夹至保本线：本层不做劣于保本的出场）",
};

/**
 * 移动止盈线的中文化（单点真源，`trailBasis` 由后端下发、前端**不重算**）。
 *
 * 五个分支必须各自说清「被谁定住」，否则用户看到一条自己算不出来的线只会更不信任它（R-11）。
 * `suppressed_at_entry` 是本轮 P0 修复引入的诚实降级态：波动率下界把线推到入场价之下且未启用保本线
 * ⇒ 整条线作废、本轮不会据它平仓——绝不假装那条线还在。快照缺失 / 未武装 → null（整行不渲染）。
 */
export function describeProfitGuardTrail(
  snapshot: ProfitGuardSnapshot | null | undefined,
): string | null {
  if (!snapshot || snapshot.status === "unavailable" || snapshot.status === "flat") return null;
  if (snapshot.trailBasis === "suppressed_at_entry") {
    return "移动止盈线本轮作废（波动率下界把它推到了入场价之下，且未启用保本线）——本轮不会据它平仓";
  }
  if (snapshot.trailStopPx == null) return null;
  const giveback = snapshot.givebackPctEffective;
  const threshold = giveback == null ? "" : `｜回吐阈 ${+(giveback * 100).toFixed(1)}%`;
  const suffix = TRAIL_BASIS_SUFFIX[snapshot.trailBasis ?? "giveback"] ?? "";
  return `移动止盈线 ${snapshot.trailStopPx}${threshold}${suffix}`;
}

// 承诺线 basis 的中文后缀（commitment [P2-5]，**独立映射**，勿复用 `TRAIL_BASIS_SUFFIX` 以免语义漂移）；
// `adopted` 须显式含此键（无后缀），否则会走 `?? ""` 兜底——但显式在测试里钉死三种 basis 各自渲染。
const COMMIT_BASIS_SUFFIX: Record<string, string> = {
  adopted: "",
  atr_floor: "（已按波动率放宽至下界）",
  clamped_breakeven: "（已夹至保本线）",
};

/**
 * 承诺退出线的中文化（单点真源，`committedExitBasis` 由后端下发、前端**不重算**）。
 *
 * 模型自己许下、经单调棘轮兑现的第三条下方保护线；`committedExitPx` 为空 / unavailable / flat → null
 * （整行不渲染）。是否「当前由承诺线定住」另由 `effectiveExitSource` 归因（[P1-3]，不在此比价推断）。
 */
export function describeCommittedExit(
  snapshot: ProfitGuardSnapshot | null | undefined,
): string | null {
  if (!snapshot || snapshot.status === "unavailable" || snapshot.status === "flat") return null;
  if (snapshot.committedExitPx == null) return null;
  const suffix = COMMIT_BASIS_SUFFIX[snapshot.committedExitBasis ?? "adopted"] ?? "";
  return `承诺退出线 ${snapshot.committedExitPx}${suffix}`;
}

/** 有效退出线当前是否由承诺线定住（[P1-3] 只读后端 `effectiveExitSource`，不比价推断）。 */
export function isEffectiveExitCommitted(
  snapshot: ProfitGuardSnapshot | null | undefined,
): boolean {
  return snapshot?.effectiveExitSource === "profit_guard_committed_exit";
}

/**
 * 冷却期中文化（与后端 `format_profit_cooldown` 同语义）；未激活 → null（chip 整块不渲染）。
 *
 * 必须同时给出剩余时间与豁免范围：只说「被拦截」会让用户（和模型）以为整个交易能力被剥夺。
 */
export function describeProfitGuardCooldown(
  cooldown: ProfitGuardCooldown | null | undefined,
): string | null {
  if (!cooldown || cooldown.remainingSeconds <= 0) return null;
  const blocked = cooldown.blockedSide === "long" ? "做多" : "做空";
  const scope =
    cooldown.scope === "both"
      ? "暂停开仓（多空均拦）"
      : `暂停${blocked}开仓（反向不受影响）`;
  return `利润保护冷却 ${cooldown.remainingSeconds}s · ${scope}；平仓不受限`;
}

const PROFIT_GUARD_REASON_LABELS: Record<string, string> = {
  profit_guard_trailing: "移动止盈",
  profit_guard_breakeven: "保本线",
  profit_guard_hard_take_profit: "硬止盈",
  profit_guard_committed_exit: "承诺退出线",
};

/** 保护触发码中文化（成交列表 / 轨迹 / 回执共用）；未知码原样回退。 */
export function describeProfitGuardReason(reason?: string | null): string {
  if (!reason) return "";
  return PROFIT_GUARD_REASON_LABELS[reason] ?? reason;
}

/**
 * 标的价格口径 % → 保证金收益率口径 %（单仓 TP/SL 双口径换算的**唯一真源**）。
 *
 * 杠杆为 null（用户留空）时**必须返回 null**：留空时后端回落 `settings.live_default_leverage`，
 * 而前端没有任何途径读到它（`tradingApi.config` 只回 `instMeta.maxLever`）。硬编码一个默认杠杆即制造
 * 第二真源——一旦运维改了 LIVE_DEFAULT_LEVERAGE，UI 会**静默**显示错误的保证金收益率，正是本特性要
 * 根治的那类信任崩塌。渲染层据 null 只显示价格口径并附一句「填写杠杆后显示保证金口径」。
 */
export function marginReturnFromPricePct(
  pct: number | null | undefined,
  leverage: number | null | undefined,
): number | null {
  if (pct == null || !Number.isFinite(pct)) return null;
  if (leverage == null || !Number.isFinite(leverage) || leverage <= 0) return null;
  return pct * leverage;
}

/**
 * 保证金收益率口径 % → 标的价格口径 %（`marginReturnFromPricePct` 的逆向，同一模块内成对，避免第二真源）。
 * 杠杆未知（用户留空）时同样返回 null——渲染层据此只写保证金口径，不给标的涨跌幅换算。
 */
export function priceMoveFromMarginPct(
  pct: number | null | undefined,
  leverage: number | null | undefined,
): number | null {
  if (pct == null || !Number.isFinite(pct)) return null;
  if (leverage == null || !Number.isFinite(leverage) || leverage <= 0) return null;
  return pct / leverage;
}

export interface AiBaseRuleView {
  label: string; // 基础策略 · 中轨锚区 · 放行 / 基础策略否决 · 观望 / 基础策略 · 数据不足 · 观望
  tone: AiTraceTone;
}

const BASE_RULE_ZONE_LABEL: Record<string, string> = {
  lower: "下轨",
  mid: "中轨",
  upper: "上轨",
};

/**
 * 【基础策略】铁律判定中文化（ai-trade-base-strategy-ma-band §2.9 单点）：三态标签 + tone。
 * 放行 → muted（常态，不抢眼）；否决 / 数据不足 → short（红，须显眼——静默的保守会被用户当成故障）。
 * 未装配（`baseRuleStatus` 为空）→ null（组件整段不渲染，老会话与老快照零影响）。
 */
export function describeBaseRule(
  trace: Pick<AiTradeRoundTrace, "baseRuleStatus" | "baseRuleZone">,
): AiBaseRuleView | null {
  const status = trace.baseRuleStatus;
  if (!status) return null;
  if (status === "unavailable") return { label: "基础策略 · 数据不足 · 观望", tone: "short" };
  if (status === "veto") return { label: "基础策略否决 · 观望", tone: "short" };
  const zone = BASE_RULE_ZONE_LABEL[trace.baseRuleZone ?? ""];
  return { label: zone ? `基础策略 · ${zone}锚区 · 放行` : "基础策略 · 放行", tone: "muted" };
}

export interface AiEdgeGateView {
  label: string; // 成本门禁 · 放行 / 成本门禁否决 · 观望 / 成本门禁 · 无法估算 · 放行
  tone: AiTraceTone;
}

/**
 * 最小 edge 门禁判定中文化（ai-trade-cost-accounting-and-edge-gate §4.3 单点）：三态标签 + tone。
 * 放行 → muted（常态，不抢眼）；否决 → short（红，须显眼）；**无法估算 → accent 而非 short**——本层
 * 极性是 fail-open，此时开仓一律放行，用红色会让用户以为被拦住了，与铁律层的同名态含义正相反。
 * 未启用（`edgeGateStatus` 为空，k_min=0 即默认）→ null（组件整段不渲染，老会话与老快照零影响）。
 */
export function describeEdgeGate(
  trace: Pick<AiTradeRoundTrace, "edgeGateStatus" | "edgeBps" | "edgeCostBps" | "edgeSource" | "priorP">,
): AiEdgeGateView | null {
  const status = trace.edgeGateStatus;
  if (!status) return null;
  if (status === "unavailable") return { label: "成本门禁 · 无法估算 · 放行", tone: "accent" };
  // 分子来自先验时带上 `p=` 读数：ATR 分子是「若走对能吃到多少」的幅度，先验分子是已按胜率折算的
  // 期望，两者量级天然差一档——不标出来，用户会以为门禁突然变严了 10 倍（signal-prior §3.7 量纲提醒）。
  const prior = trace.edgeSource === "prior" && trace.priorP != null ? `p=${trace.priorP.toFixed(2)}，` : "";
  const budget =
    trace.edgeBps != null && trace.edgeCostBps != null
      ? `（${prior}${trace.edgeBps.toFixed(1)} / ${trace.edgeCostBps.toFixed(1)}bps）`
      : "";
  if (status === "veto") return { label: `成本门禁否决 · 观望${budget}`, tone: "short" };
  return { label: `成本门禁 · 放行${budget}`, tone: "muted" };
}

export interface AiSizingView {
  label: string; // 仓位层 · 未缩减 / 仓位层缩减 · 0.75→0.25 / 回撤刹车 · 只平不开 …
  tone: AiTraceTone;
  note: string; // 一句话说清「这个数是怎么来的」（来源 + 是否被档位地板吃掉）
}

// 仓位缩减来源的中文（与后端 `sizing._scale_source` 五态逐条对应）。
const SCALE_SOURCE_LABEL: Record<string, string> = {
  model: "你的提议本身就更小",
  vol: "波动率目标项绑定",
  kelly: "凯利项绑定",
  brake: "回撤刹车绑定",
  correlation: "组合相关性预算绑定",
};

// 凯利项降级码的中文（**绝不**把「不可算」说成「仓位归零」——本层的降级一律是「该项不参与 min」）。
const KELLY_STATUS_LABEL: Record<string, string> = {
  ok: "",
  no_prior: "本方向暂无胜率先验，凯利项不参与",
  no_cost: "成本估算缺席，凯利项不参与",
  no_bracket: "止盈/止损括号不可用（或成本吃掉整个止盈距离），凯利项不参与",
  disabled: "凯利项已关闭",
};

// 止损来源五态的中文（三个 skip 态必须讲清「为什么没按 ATR 挂」——那是本设计付出的真实代价）。
const STOP_SOURCE_LABEL: Record<string, string> = {
  atr: "止损距离按本轮 ATR 定标后随单挂出",
  fixed: "ATR 不可用或未开启，止损沿用固定百分比",
  skipped_resting: "有限价挂单在飞，本轮不改写止损（改了会让落库回执写上一个交易所侧不存在的价）",
  skipped_position_live: "已有持仓，本轮不改写止损（其 OCO 早已挂出，改了只会让展示口径撒谎）",
  skipped_reversal: "本轮是反手，开新仓沿用当前固定止损（fail-safe：拿到的是与实际挂单一致的距离）",
};

/**
 * 仓位 / 刹车层判定中文化（vol-sizing-and-drawdown-brake §6.3 第 6a 点）：标签 + tone + 说明。
 * 规则未装配（`brakeLevel` 与 `stopSource` 皆空）→ null（组件整段不渲染，老会话与老快照零影响）。
 *
 * 三条呈现纪律：① 刹车的两个阻断档用红色（它们真的拦了开仓），第一档减速用 accent（只是缩小）；
 * ② `scaleErased` 必须显式说出来——「减速了但被档位地板原样吃回去」与「减速生效」在数字上长得一样，
 * 不说清楚运维会据 `brakeSlowRounds` 认定刹车已验证；③ `stopSource` 的三个 skip 态要讲清**为什么**
 * 没按 ATR 挂（有挂单在飞 / 有持仓 / 反手），否则用户只会看到「止损还是老样子」。
 */
export function describeSizing(
  trace: Pick<
    AiTradeRoundTrace,
    | "brakeLevel"
    | "brakeReason"
    | "drawdownPct"
    | "modelFraction"
    | "finalFraction"
    | "fFinalRaw"
    | "scaleSource"
    | "scaleErased"
    | "kellyStatus"
    | "stopSource"
    | "exposureCapUnreachable"
  >,
): AiSizingView | null {
  const level = trace.brakeLevel ?? null;
  if (!level && !trace.stopSource) return null;
  if (level === "halt" || level === "cooldown") {
    const label = level === "halt" ? "回撤刹车 · 只平不开" : "连亏熔断 · 冷却中";
    const dd = trace.drawdownPct != null ? `（当前回撤 ${(trace.drawdownPct * 100).toFixed(1)}%）` : "";
    return { label: `${label}${dd}`, tone: "short", note: "本档只拦开新仓：平仓与撤单任何时候都不受影响。" };
  }
  const shrank =
    trace.modelFraction != null && trace.finalFraction != null && trace.finalFraction < trace.modelFraction;
  const label = shrank
    ? `仓位层缩减 · ${trace.modelFraction!.toFixed(2)}→${trace.finalFraction!.toFixed(2)}`
    : level === "slow"
      ? "回撤刹车 · 第一档减速"
      : "仓位层 · 未缩减";
  return {
    label,
    tone: level === "slow" ? "accent" : shrank ? "accent" : "muted",
    note: sizingNote(trace),
  };
}

/** `describeSizing` 的说明句：来源 → 是否被档位地板吃掉 → 凯利降级 → 敞口不可达 → 止损来源。 */
function sizingNote(
  trace: Pick<
    AiTradeRoundTrace,
    "scaleSource" | "scaleErased" | "fFinalRaw" | "finalFraction" | "kellyStatus" | "stopSource" | "exposureCapUnreachable"
  >,
): string {
  const bits: string[] = [];
  const source = trace.scaleSource ? SCALE_SOURCE_LABEL[trace.scaleSource] : "";
  if (source) bits.push(source);
  if (trace.scaleErased && trace.fFinalRaw != null && trace.finalFraction != null) {
    bits.push(
      `解出 ${trace.fFinalRaw.toFixed(3)} 但被档位地板吸附回 ${trace.finalFraction.toFixed(2)}——本轮缩减实际未生效，` +
        "想要更细的响应请把档位步长调小",
    );
  }
  const kelly = trace.kellyStatus ? KELLY_STATUS_LABEL[trace.kellyStatus] : "";
  if (kelly) bits.push(kelly);
  if (trace.exposureCapUnreachable) {
    bits.push("组合有效敞口已超上限且缩减本笔也达不到，读数如实标注为不可达（本层不否决开仓）");
  }
  const stop = trace.stopSource ? STOP_SOURCE_LABEL[trace.stopSource] : "";
  if (stop) bits.push(stop);
  return bits.join("；");
}

export interface AiExecutedView {
  label: string;    // 已成交 / 已跳过 / 已降级观望（reason） / 观望
  tone: AiExecTone; // 净立场着色（long/short/muted）
}

/**
 * 执行结果态中文化（E3）：把本轮 `executed`/`degradeReason` 归一为四态状态点（+着色）。
 * 优先级：已跳过（decision_in_flight）> 已降级观望（含 reason）> 已成交（按方向着色）> 观望。
 */
export function describeExecuted(
  action: string,
  executed: AiTradeDecision["executed"],
  degradeReason?: string | null,
): AiExecutedView {
  if (executed?.skipped) return { label: "已跳过", tone: "muted" };
  // P1-9：经 describeDegradeReason 渲染（未命中原样回退 ⇒ 既有 `low_consensus(1/2)` 展示逐字不变）。
  if (degradeReason) return { label: `已降级观望（${describeDegradeReason(degradeReason)}）`, tone: "short" };
  if (executed?.traded) {
    const tone: AiExecTone =
      action === "open_long" ? "long" : action === "open_short" ? "short" : "muted";
    return { label: "已成交", tone };
  }
  return { label: "观望", tone: "muted" };
}

export interface AiReflectionView {
  label: string; // 复核中… / 二次反思确认 / 二次反思否决 · 观望
  tone: AiTraceTone;
}

/**
 * 下单前二次反思裁决中文化（ai-trade-pre-trade-reflection §三 describeReflection 单点）：把在飞/裁决态归一
 * 为一枚标签 + tone。复核中（pending）→ accent；confirm → long（绿·校验通过放行）；abort → short（红·否决
 * 观望）。无反思（hold/关闭轮，两者皆空）→ null（组件不渲染反思段）。
 */
export function describeReflection(
  trace: Pick<AiTradeRoundTrace, "reflectionPending" | "reflectionVerdict">,
): AiReflectionView | null {
  if (trace.reflectionPending) return { label: "二次反思复核中…", tone: "accent" };
  if (trace.reflectionVerdict === "confirm") return { label: "二次反思确认执行", tone: "long" };
  if (trace.reflectionVerdict === "abort") return { label: "二次反思否决 · 观望", tone: "short" };
  return null;
}

export interface AiTradeTriggerView {
  label: string; // 触发原因短语
  tone: AiTraceTone; // long | short | muted | accent（复用轮头着色体系）
}

// 唤醒原因八态的轮头标签（wake-aware-context §3.3 + wake-scheduler-closed-loop 轴 C）：把原来
// 单调的两个兜底文案细化成八态。tone 只在「值得注意」的五态用 accent（补看新 K 线 / 立场翻转 /
// 价格异动 / 浮盈跨档 / 挂单盯守），常规心跳与持仓盯市保持 muted——满屏 accent 等于没有 accent。
// 导出供轴 D 的归因表复用标签（同一个 kind 在轨迹面板与胜率表里必须是同一句中文）。
// 声明为 `Record<WakeKind, …>` ⇒ 后端新增 kind 时 tsc 强制要求补齐，不会静默漏一个展示点。
export const WAKE_LABELS: Record<WakeKind, AiTradeTriggerView> = {
  candle_close: { label: "K 线收盘 · 信号板已重算", tone: "muted" },
  bar_advanced: { label: "补看新 K 线 · 首次研判", tone: "accent" },
  signal_flip: { label: "策略立场翻转 · 未收线预演", tone: "accent" },
  resting_order: { label: "挂单盯守 · 未成交委托", tone: "accent" },
  profit_tier: { label: "浮盈跨档 · 保护线已激活", tone: "accent" },
  price_breakout: { label: "价格异动 · 越过唤醒阈值", tone: "accent" },
  position_watch: { label: "持仓盯市 · 常规复盘", tone: "muted" },
  heartbeat: { label: "心跳复盘 · 数据未见异动", tone: "muted" },
};

// 触发方向 → 轮头着色 tone（open_long 绿 / open_short 红 / close 及未知边沿 accent）。
function toneForTriggerAction(action: string): AiTraceTone {
  if (action === "open_long") return "long";
  if (action === "open_short") return "short";
  return "accent"; // close 或未知边沿
}

/**
 * 触发原因派生（第 3 轮 R1 + wake-interval）：由本轮信号板边沿信号（latestSignal）与唤醒来源（trigger）
 * 归纳「是什么触发了这一轮」。无边沿且 trigger==="interval" → 「周期唤醒 · 常规复盘」（accent）；无边沿且
 * 其余 → 「K 线收盘 · 常规复盘」（muted）；单策略触发 → 「<动作>信号 · <策略名>」；多策略同向 →
 * 「N 策略<动作>信号」；多策略分歧 → 「信号分歧 · a开多/b开空」（沿用 describeConsensus 计数风格）。
 * 着色取多/空/其它。动作中文化复用既有 describeAiAction（同文件），不再自建标签表。
 */
export function describeTrigger(
  trace: Pick<AiTradeRoundTrace, "signalBoard" | "trigger" | "wake">,
): AiTradeTriggerView {
  const fired = (trace.signalBoard ?? []).filter((s) => s.latestSignal !== "none");
  if (fired.length === 0) {
    // 三级优先：信号板边沿 > wake.kind > trigger。wake 只把原来单调的两个兜底文案细化成七态，
    // **绝不抢先**——有边沿信号时 `开多信号 · 均线突破` 的信息量高于 `价格异动`，让 wake 抢先
    // 是功能退化而非升级。
    const kind = trace.wake?.kind;
    if (kind && WAKE_LABELS[kind]) return withCatchupMark(WAKE_LABELS[kind], trace.wake);
    if (trace.trigger === "interval") {
      return withCatchupMark({ label: "周期唤醒 · 常规复盘", tone: "accent" }, trace.wake);
    }
    return withCatchupMark({ label: "K 线收盘 · 常规复盘", tone: "muted" }, trace.wake);
  }
  if (fired.length === 1) {
    const s = fired[0];
    return {
      label: `${describeAiAction(s.latestSignal)}信号 · ${s.name}`,
      tone: toneForTriggerAction(s.latestSignal),
    };
  }
  const counts = new Map<string, number>();
  for (const s of fired) counts.set(s.latestSignal, (counts.get(s.latestSignal) ?? 0) + 1);
  if (counts.size === 1) {
    const action = [...counts.keys()][0];
    return { label: `${fired.length} 策略${describeAiAction(action)}信号`, tone: toneForTriggerAction(action) };
  }
  const tally = ["open_long", "open_short", "close"]
    .filter((a) => counts.get(a))
    .map((a) => `${counts.get(a)}${describeAiAction(a)}`)
    .join("/");
  return { label: `信号分歧 · ${tally}`, tone: "accent" };
}

/**
 * 补跑轮追加「· 补跑」标记（轴 A）。
 *
 * **必须返回新对象**：`WAKE_LABELS[kind]` 是一个**共享常量对象**，就地改写会污染全局标签表，
 * 让此后每一轮都带上补跑字样——一个只该出现在个别轮次的标记会变成永久的谎话。
 */
function withCatchupMark(
  base: AiTradeTriggerView,
  wake: WakeInfo | null | undefined,
): AiTradeTriggerView {
  const backlog = wake?.backlog;
  if (!backlog) return base;
  const dropped = (backlog.droppedBars ?? 0) + (backlog.droppedIntervals ?? 0);
  if (dropped <= 0) return base;
  return { ...base, label: `${base.label} · 补跑` };
}
