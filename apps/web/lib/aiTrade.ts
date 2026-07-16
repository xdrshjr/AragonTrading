// AI 自动交易纯件（无副作用、易单测）：开关守门文案矩阵、策略/轮数/预算校验、动作与停止原因中文化。
// 预算派生/夹紧复用 live-auto 同款（0.01 默认 / 0.05 上限，与后端 ai_trade_default/max_capital_frac 对齐）。
import { clampBudgetToCap, deriveDefaultBudget } from "./liveAuto";
import type { AccountMode } from "./types";
import type {
  AiTradeAction,
  AiTradeDecision,
  AiTradeRoundTrace,
  AiTradeStopReason,
  Consensus,
  CandidateLevel,
  FactorReading,
  FactorReadings,
  MarketContext,
  MultiTimeframeFrame,
  PriceAction,
  VolumeContext,
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
  if (degradeReason) return { label: `已降级观望（${degradeReason}）`, tone: "short" };
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
  trace: Pick<AiTradeRoundTrace, "signalBoard" | "trigger">,
): AiTradeTriggerView {
  const fired = (trace.signalBoard ?? []).filter((s) => s.latestSignal !== "none");
  if (fired.length === 0) {
    if (trace.trigger === "interval") {
      return { label: "周期唤醒 · 常规复盘", tone: "accent" };
    }
    return { label: "K 线收盘 · 常规复盘", tone: "muted" };
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
