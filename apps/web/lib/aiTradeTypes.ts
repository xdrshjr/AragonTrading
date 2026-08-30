// AI 自动交易 wire 类型（camelCase，与后端 schemas.py 的 AiTrade* 同构）。
// 运行期句柄（sessionId 等）为瞬态（store 不入 partialize）；aiTradeConfig 为持久化偏好。
import type { FeeRatesWire, ReconcileWire } from "./aiTradeCost";
import type { AiTradeRealizedStats } from "./aiTradeProfitability";
import type {
  PaperPositionEvent,
  ProfitGuardCooldown,
  ProfitGuardSnapshot,
  Trade,
} from "./types";

// 保护快照的形状真源在 types.ts（aiTradeTypes 已 import 它，反向 import 会成环）；此处只做转出口，
// 使 AI 自动交易侧的消费点无需跨文件二次 import。
export type { ProfitGuardCooldown, ProfitGuardSnapshot } from "./types";

// cancel_order（ai-trade-limit-orders M-11）：撤回本会话未成交的限价挂单，不开不平。
export type AiTradeAction = "open_long" | "open_short" | "close" | "hold" | "cancel_order";
export type AiStance = "long" | "short" | "flat";
export type AiLatestSignal = "open_long" | "open_short" | "close" | "none";

// 单策略此刻喂给 AI 的立场视图（P0-1：stance=影子持久立场，latestSignal=本根边沿）。
export interface StrategySignalView {
  strategyId: number;
  name: string;
  stance: AiStance;
  latestSignal: AiLatestSignal;
  strength: number;
  reason: string;
}

// 市场体制上下文（E1）：engine/regime.classify_regime 的紧凑视图（取值∈真实 regime 枚举）。
export interface MarketContext {
  primary: string;      // trend_up | trend_down | range
  volatility: string;   // high_volatility | low_volatility | normal
  event: string;        // crash | breakout | none
  confidence: number;
  labels: string[];
}

// 策略共识度（E2）：信号板逐策略持久立场聚合出的多/空/空仓票 + 净立场 + 一致度。
export interface Consensus {
  longVotes: number;
  shortVotes: number;
  flatVotes: number;
  total: number;
  netStance: AiStance;
  agreement: number;    // max(long,short)/total ∈ [0,1]
}

// 市场证据摘要（R1，ai-trade-reasoning-chain）：与后端 market_digest.build_market_digest 产物 wire 镜像。
// 单技术因子读数：value 可为 null（数据不足/除零），label 为定性枚举（前端另做中文化）。
export interface FactorReading {
  value: number | null;
  label: string;
}

// 价格走势证据：趋势 / 动量 / 均线结构 / 摆动结构 / 区间位置（分母 0 时对应字段为 null）。
export interface PriceAction {
  changePct: number;
  trend: string;               // up | down | flat
  emaFast: number;
  emaSlow: number;
  emaRelation: string;         // fast_above | fast_below
  emaSlopePct: number | null;
  rangePos: number | null;     // ∈[0,1]
  swingHigh: number;
  swingLow: number;
  consecUp: number;
  consecDown: number;
  lastBar: { o: number; h: number; l: number; c: number; bodyPct: number | null };
}

// 成交量证据：量能水平 / 量比 / zscore / 放缩趋势 / 异常放量。
export interface VolumeContext {
  last: number;
  sma: number;
  ratio: number | null;
  zscore: number | null;
  trend: string;               // rising | falling | flat
  spike: boolean;
}

// 一组固定经典技术因子读数（RSI/MACD 柱/布林%B/ATR%/动量 ROC）。
export interface FactorReadings {
  rsi14: FactorReading;
  macdHist: FactorReading;
  bollPctB: FactorReading;
  atrPct: FactorReading;
  rocPct: FactorReading;
}

// 市场证据摘要（R1）：任一子段可为 null（数据不足），整体在禁用/窗口不足时为 null（prompt/前端省略该段）。
export interface MarketDigest {
  priceAction: PriceAction | null;
  volume: VolumeContext | null;
  factors: FactorReadings | null;
}

export interface CandidateLevel {
  price: number;
  distancePct: number;
  bars: string[];
}

export interface MultiTimeframeFrame {
  bar: string;
  sampleCount: number;
  closeTs: number;
  ageSeconds: number;
  stale: boolean;
  regime: MarketContext | null;
  digest: MarketDigest | null;
}

export interface MultiTimeframeContext {
  executionBar: string;
  asOfTs: number;
  frames: MultiTimeframeFrame[];
  trendAlignment: {
    status: "bullish" | "bearish" | "mixed" | "range" | "insufficient";
    bullishBars: string[];
    bearishBars: string[];
    rangeBars: string[];
  };
  rsiOverboughtBars: string[];
  rsiOversoldBars: string[];
  nearestSupport: CandidateLevel | null;
  nearestResistance: CandidateLevel | null;
  missingBars: string[];
  staleBars: string[];
}

// ---- 唤醒感知上下文（ai-trade-wake-aware-context §5.3）----
// 唤醒原因八态（与后端 wake_reason.WAKE_KINDS 镜像；判定顺序即优先级）。
// `signal_flip` 插在 `bar_advanced` 之后：真的收了新根永远优先，除此之外「某个策略的立场正在
// 翻转」是信息量最高的一件事（ai-trade-wake-scheduler-closed-loop 轴 C）。
export type WakeKind =
  | "candle_close"
  // 情报告警（ai-trade-intel-actionability 轴 A）：插在 `candle_close` 之后、`bar_advanced`
  // 之前。它是**外生**的既成事实，而且这一轮之所以存在就是因为它——标成 `bar_advanced` 会让
  // 唤醒句说「你补看一根新 K 线」，真正的原因从标题里消失。
  | "intel_alert"
  | "bar_advanced"
  | "signal_flip"
  | "resting_order"
  | "profit_tier"
  | "price_breakout"
  | "position_watch"
  | "heartbeat";

// 本轮唤醒断面：kind 是标签，alsoTrue 保留全部同时成立的条件（判定只决定标签，绝不丢证据）。
// 阈值随 wire 下发是刻意的——静默的门禁在本仓库有前科，用户必须能看到「判据是多少、实际是多少」。
export interface WakeInfo {
  kind: WakeKind;
  trigger: "candle" | "interval";
  executionBar?: string | null; // ATR 倍数的量纲周期名（文案必须写「0.91×30m ATR」而非裸「ATR」）
  sinceLastRoundS?: number | null;
  priceMovePct?: number | null;
  priceMoveAtr?: number | null; // **未**折算的原始倍数（物理量）
  moveThresholdAtr?: number | null; // 配置值（未折算）
  moveThresholdPct?: number | null;
  horizonScale?: number | null; // sqrt(min(1, wake_s / bar_s))：把执行周期 ATR 折算到唤醒窗口
  moveThresholdAtrEff?: number | null; // 实际判据 = moveThresholdAtr × horizonScale
  skipStreak?: number | null; // 连续跳过计数（触到上限即强制放行一轮）
  barAdvanced?: boolean;
  restingAgeS?: number | null;
  guardStatus?: string | null; // 本 tick 利润保护快照 status（profit_tier 文案的取值来源）
  lastGuardStatus?: string | null;
  alsoTrue?: WakeKind[];
  dropped?: boolean; // 被 in-flight 守卫丢弃的 K 线轮
  backlog?: WakeBacklogInfo | null; // 仅补跑轮出现（轴 A）
  flip?: WakeFlipInfo | null; // 仅 signal_flip 轮出现（轴 C）
  intel?: WakeIntelInfo | null; // 仅 intel_alert 轮出现（actionability 轴 A）
}

// 触发本轮的那一条高危市场情报（ai-trade-intel-actionability 轴 A）。**不含 URL、不含域名、
// 不含正文**（提示注入防护姿态）。`inDigest=false` ⇒ 唤醒句会把摘要就地内联进 prompt 并如实
// 说明「它没出现在下方情报段中」——「有编号」与「真的在 prompt 里」不是同一件事。
export interface WakeIntelInfo {
  alertId: number;
  itemId: number;
  ref: string;
  inDigest: boolean;
  headline: string;
  summary: string;
  label: string; // 「风险事件·偏空·高」，后端下发
  category: string;
  tier: string;
  stance: string;
  impact: string;
  domainCount: number;
  quoted: boolean;
  confidence: number;
  severity: number;
  ageMin: number;
}

// 上一轮研判期间被在飞守卫吞掉的唤醒（轴 A）。`waitedS` 的口径是「自**首次**丢弃至今」，
// 不是上一轮的研判耗时——后端并没有把轮耗时记进 backlog，两者不可混读。
export interface WakeBacklogInfo {
  droppedIntervals?: number;
  droppedBars?: number;
  waitedS?: number;
  chain?: number; // 本轮是补跑链的第几轮（背靠背补跑的计数，非补跑轮启动即归零）
}

// 未确认根上的**投影净共识**立场翻转（轴 C）。`provisional` 恒为 true：它是预演结论，
// 信号板与全部门禁仍以最近确认根为准，UI 必须如实标注，绝不呈现成既成事实。
export interface WakeFlipInfo {
  netStanceFrom?: string;
  netStanceTo?: string;
  strategies?: { strategyId?: number; name?: string; from?: string; to?: string }[];
  provisional?: boolean;
  heldS?: number; // 该翻转已连续保持多久（**只按有效采样推进**，行情没动过的采样不计）
  samples?: number;
  barTs?: number;
}

// 自上一决策轮的价格变动：**与周期无关**，故挂在 TacticalContext 顶层而非逐周期重复。
// atrBar 显式声明这个 ATR 的量纲周期是执行周期，不是战术周期自身的 ATR。
export interface TacticalChange {
  pct: number | null;
  atrMult: number | null;
  atrBar: string;
}

export interface TacticalFrame {
  bar: string;
  sampleCount: number;
  closeTs: number;
  ageSeconds: number;
  stale: boolean;
  regime?: MarketContext | null;
  digest?: MarketDigest | null;
  closedBars?: number | null; // 上一决策轮之后该级别**收线**的根数（按收线时刻计，不是开盘）
}

// 战术周期证据（严格短于执行周期、贴唤醒节奏的 1~2 个周期）；frames 由粗到细，与 prompt 同序。
export interface TacticalContext {
  executionBar: string;
  wakeIntervalS?: number | null;
  asOfTs: number;
  executionPrimary?: string | null;
  alignment: "aligned" | "diverged" | "insufficient";
  changeSinceLastRound?: TacticalChange | null;
  frames: TacticalFrame[];
  missingBars: string[];
  staleBars: string[];
  maxChars?: number | null; // 服务端渲染字符上限（前端不消费，声明它只为让类型如实描述 wire）
}

// 当前挂着的限价委托（ai-trade-limit-orders §4.2；与后端 AiTradeRestingOrderOut / resting_payload 同构）。
// 会话至多一笔（不变式 I-1）；ageS/expiresInS 为后端装配时刻的断面秒数，非前端实时倒计时。
export interface AiTradeRestingOrder {
  intent: string;        // open | close
  side: string;          // long | short
  px: number;            // 委托价（USDT）
  sz: number;            // 张数
  ageS: number;          // 已挂秒数
  expiresInS: number;    // 距 TTL 自动撤销的剩余秒数
  filledSz: number;      // 已成交（已采纳）张数；未成交为 0
}

// 本轮 AI 决策（受约束）：action + 可选仓位占比/置信度 + 理由 + 信号板 + 执行结果 + 体制/共识/降级（E1/E2/E3）。
export interface AiTradeDecision {
  ts: number;
  round: number;
  action: AiTradeAction;
  sizeFraction?: number | null;
  confidence?: number | null;
  // 委托方式（ai-trade-limit-orders §2.2）：总闸关闭 / 旧快照无此键 → 视同 market。
  orderType?: "market" | "limit" | null;
  limitPrice?: number | null;             // 限价委托价；市价决策恒为 null
  // 本轮结束时的挂单断面（无挂单 → null）；跳过轮不带此段（_emit_decision_skipped）。
  restingOrder?: AiTradeRestingOrder | null;
  reason: string;
  signalBoard: StrategySignalView[];
  executed?: {
    ok?: boolean;
    action?: string;                        // 门禁后最终动作（后端 _execute_decision 回带；与 wire §5.1 一致）
    skipped?: boolean;
    traded?: boolean;
    reason?: string;
    side?: string;
    qty?: number;
    positionAfter?: string;
  } | null;
  marketContext?: MarketContext | null;   // E1（可为 null）
  consensus?: Consensus | null;           // E2
  degradeReason?: string | null;          // E3（low_consensus(k/n) / decision_timeout / … 或 null）
  analysis?: string | null;               // R1：研判思维链有界摘录（≤400 字；完整全文见 agentTraces）
  marketDigest?: MarketDigest | null;     // R1：价格走势/成交量/因子证据摘要（可为 null）
  multiTimeframeContext?: MultiTimeframeContext | null;
  trigger?: "candle" | "interval";        // wake-interval：唤醒来源（K 线唤醒 / 墙钟定时唤醒）
  // 唤醒感知上下文（wake-aware-context §4.3）：总闸关闭 / 老事件 → null 或缺席。
  wake?: WakeInfo | null;
  tacticalContext?: TacticalContext | null;
  tacticalTier?: "full" | "brief" | null;
  contextDisclosure?: "full" | "repeat" | null;
  // 下单前二次反思裁决（ai-trade-pre-trade-reflection §2.6）：无反思轮（hold / 关闭）为 null。
  reflectionVerdict?: "confirm" | "abort" | null;
  reflectionReason?: string | null;
  // 截至本轮的连续真实观望轮数（hold-inertia-observability N5）；跳过轮事件 / 老后端无此键。
  holdStreak?: number;
}

// 反思裁决枚举（与后端 ReflectionVerdict.verdict 镜像）。
export type AiReflectionVerdict = "confirm" | "abort";

// 决策认知轨迹阶段（ai-auto-trading-agent-trace §5.3；与后端 trace.PHASES 镜像）。
// reflection_*：下单前二次反思三阶段（ai-trade-pre-trade-reflection §4.4）。
export type AiTradeTracePhase =
  | "round_start"
  | "thinking"
  | "analysis"
  | "tool_call_start"
  | "tool_call"
  | "result"
  | "summary"
  | "error"
  | "retry"
  | "reflection_start"
  | "reflection"
  | "reflection_verdict"
  // 【基础策略】铁律门禁段（ai-trade-base-strategy-ma-band §2.9）：仅规则装配的会话每轮一条。
  | "base_rule"
  // 最小 edge 门禁段（ai-trade-cost-accounting-and-edge-gate §5.3）：仅 k_min>0 的会话每轮一条。
  | "edge_gate"
  // 仓位求解 + ATR 止损 + 回撤刹车段（vol-sizing-and-drawdown-brake §3.9）：仅规则装配的会话每轮一条。
  | "sizing"
  // 持仓周期铁律段（ai-trade-holding-horizon-modes §4.4）：仅契约装配的会话每轮一条（含放行轮）。
  | "horizon"
  // 浅研判追问的流式文本（ai-trade-deep-reasoning-chain §4.2 / P0-3）：**独立相位**，归约进
  // `reaskAnalysis` 而非 `analysis`——后者是纯追加、唯一清零点是 `retry`，并进去会把 S1–S12
  // 追加成两遍，而追问**本来就可能被拒收**。两份并列呈现是刻意的。
  | "reask";

// ---- 交易周期模式（ai-trade-holding-horizon-modes §4.1/§5.4，与后端 hold_horizon 逐键镜像）----
export type AiTradeHorizonProfileId =
  | "hourly_swing"
  | "intraday_swing"
  | "short_swing"
  | "trend_hold"
  | "custom";

// 启动请求里的周期契约入参；整个字段省略 ⇒ 服务端按 settings 装配默认模式（D-2「缺省即开启」）。
export interface AiTradeHorizonRule {
  enabled: boolean;
  profile?: AiTradeHorizonProfileId | null;
  minHoldSeconds?: number | null;        // 最短主动平仓窗口（秒；0=不约束）
  exitConfirmRounds?: number | null;     // 离场连续确认轮数（1=不约束）
  turnoverHysteresisK?: number | null;   // 换手迟滞倍数（0=不约束）
  // **不是纯展示字段**：targetHoldMaxSeconds 是 expectedHoldBars 的唯一派生源（成本资金费期数 +
  // 先验前瞻窗口），targetHoldMinSeconds 同时派生 confirmWindowSeconds 与 minHoldSeconds 默认值。
  targetHoldMinSeconds?: number | null;
  targetHoldMaxSeconds?: number | null;
  enforceMaxHold?: boolean | null;       // true ⇒ 把 executor 的 max_hold_bars 设为 expectedHoldBars
  // 以下三键**仅出现在回执镜像里**（服务端解析后的生效值 + 两个派生量，[P1-9]/[P0-3]）。
  title?: string | null;
  targetLabel?: string | null;
  confirmWindowSeconds?: number | null;
  expectedHoldBars?: number | null;
  gateReverse?: boolean | null;
  // ---- 开仓侧换手纪律（turnover-budget §4.2）----
  // 入参：`null` ⇒ 按契约派生；`0` ⇒ **显式关闭**该闸（不是「无限长 / 无限多」）。
  reentryCooldownSeconds?: number | null;
  turnoverBudgetPerDay?: number | null;
  // 以下两键**仅出现在回执镜像里**（服务端派生后的实际值）：只回显入参等于把派生关系藏起来。
  reentryScope?: "same_side" | "both" | null;
  turnoverBudgetCapacity?: number | null;
  // 交易所侧兜底自愈模式（backstop-repair §4.3）：入参 `null`/缺省 ⇒ 服务端按运维缺省回填；
  // 回执镜像同一个键，用户据此核对「我选的和落地的是不是同一个」。
  backstopRepair?: AiTradeBackstopRepairMode | null;
}

// `GET /ai-trade/horizon-profiles` 的单个预设条目。**除三个字符串外全部可空**（[P1-8]）：`custom`
// 条目这些格子恒为 null，前端对 `bar === null` 不执行任何联动。
export interface AiTradeHorizonProfile {
  id: AiTradeHorizonProfileId;
  title: string;
  description: string;
  targetLabel?: string | null;
  bar?: string | null;
  wakeIntervalMinutes?: number | null;
  minHoldSeconds?: number | null;
  exitConfirmRounds?: number | null;
  turnoverHysteresisK?: number | null;
  targetHoldMinSeconds?: number | null;
  targetHoldMaxSeconds?: number | null;
  expectedHoldBars?: number | null;      // 按该条目自带 bar 的**预览值**；实际按用户所选 bar 现算
  confirmWindowSeconds?: number | null;  // 同上
  leverageMaxHint?: number | null;
  // 验收门槛预览（horizon-acceptance §4.1）：全部由后端现算下发，前端不复制任何一个数。
  acceptance?: AiTradeAcceptanceThresholds | null;
  // 开仓侧换手纪律的三个**预览**值（turnover-budget §4.1）；`custom` 恒为 null。
  reentryCooldownSeconds?: number | null;
  turnoverBudgetPerDay?: number | null;
  turnoverBudgetCapacity?: number | null;
}

export interface AiTradeHorizonProfilesResponse {
  enabled: boolean;
  defaultProfile: AiTradeHorizonProfileId;
  estimatedCostBps?: number | null;      // 按配置常数估的往返成本（非实时盘口，UI 须标「约」）
  lossReleaseFrac: number;               // 解除阈 = stopLossPct × leverage × frac（[P1-10] 现算）
  lossReleaseReturnPctFallback: number;  // 无交易所止损时的兜底解除阈
  // 三个只读运维标志（turnover-budget §4.1 / [P2-4]）：弹窗那句「每 N 分钟核实一次」必须读
  // `backstopPollSeconds`，**前端不得硬抄一个 2 分钟**——运维改了配置而文案不动，就又造出
  // 一句没核实的话。
  turnoverGateEnabled?: boolean;
  backstopCheckEnabled?: boolean;
  backstopPollSeconds?: number;
  // 交易所侧兜底自愈块（backstop-repair §4.2）；运维总闸关 ⇒ **整块缺席** ⇒ 前端不渲染控件。
  // 标签、说明、缺省模式与两个上限**全部由后端下发**，前端一个字、一个数都不硬编。
  backstopRepair?: AiTradeBackstopRepairConfig | null;
  // 离场优先通道的只读说明块（exit-priority §6.2）；总闸关 ⇒ 整块缺席 ⇒ 前端不渲染。
  // 同上一条纪律：档位标签、配额、地板与那句安全承诺**全部由后端下发**，前端一个字都不硬编。
  exitPriority?: AiTradeExitPriorityConfig | null;
  profiles: AiTradeHorizonProfile[];
}

/** 离场优先通道的一个档位（`id` 是模型要填进 `exitConviction` 的字面量）。 */
export interface AiTradeExitLevelOption {
  id: AiTradeExitConviction;
  label: string;
  detail: string;
}

export interface AiTradeExitPriorityConfig {
  enabled: boolean;
  overrideRounds: number;
  overrideMinHoldSeconds: number;
  maxOverridesPerSession: number;
  evidenceMinChars: number;
  levels: AiTradeExitLevelOption[];
  /** 「只放行平仓、永不放行开仓」那句安全承诺——它是 X-1 在 UI 上的唯一落点。 */
  note: string;
}

export type AiTradeBackstopRepairMode = "plan" | "arm";

/** 自愈控件的一个选项；`detail` 是授权含义的说明（`arm` 那句必须写清「系统会替你下单」）。 */
export interface AiTradeBackstopRepairModeOption {
  id: AiTradeBackstopRepairMode;
  label: string;
  detail: string;
}

export interface AiTradeBackstopRepairConfig {
  defaultMode: AiTradeBackstopRepairMode;
  modes: AiTradeBackstopRepairModeOption[];
  note: string;
  maxPerEpoch: number;
  maxPerSession: number;
}

// ---- 开仓侧换手纪律与交易所侧兜底的运行期 wire（turnover-budget §5.4）----
// 两者都挂在**运行期快照** `horizon` 下（不是契约 `tradingHorizon`）；未装配 ⇒ **整键缺席**。
export interface AiTradeTurnoverWire {
  cooldownActive: boolean;
  cooldownRemainingSeconds?: number | null;   // 未激活 ⇒ null（**绝不用 0 冒充**）
  cooldownSide?: "long" | "short" | null;
  cooldownScope: "same_side" | "both";
  cooldownSeconds?: number | null;
  budgetEnabled: boolean;
  // 恒 "session"：令牌桶是会话内存态、启动即满桶 ⇒ 停会话重开即回满（[P1-5] / D-13）。
  // 三处文案（弹窗 / 卡片 / 验收自认）必须照此措辞，绝不写一句做不到的「每日」。
  budgetScope: "session";
  tokens?: number | null;
  capacity?: number | null;
  perDay?: number | null;
  nextTokenSeconds?: number | null;           // null ⇒ 已有可用令牌
  blocked: boolean;
  reverseWouldDowngrade: boolean;             // 提议反手时再入场腿会被否、只执行平仓腿
  gateSkipped?: string | null;                // 立场落在值域外时的如实记号（fail-open）
  reentryVetoes: number;
  budgetVetoes: number;
  opensCounted: number;
}

// ---- 交易纪律预设与生效自检（ai-trade-discipline-preset-and-integrity 轴 A / 轴 B）----
export type AiTradeDisciplinePreset = "steady" | "balanced" | "aggressive" | "off";

/** 启动请求里的纪律入参；**整键缺省 ⇒ 服务端按默认预设装配**（老客户端同样受保护）。 */
export interface AiTradeDisciplineIn {
  enabled?: boolean;
  preset?: AiTradeDisciplinePreset;
  // 逐项覆盖（留空=按预设）。`0` 一律表示「显式关闭该闸」。
  // **与 `enabled=false` / `preset="off"` 同时出现 ⇒ 服务端 422**（静默丢弃显式输入是禁止的形状）。
  edgeGateK?: number | null;
  minConsensus?: number | null;
  minConfidence?: number | null;
  volTargetAnnual?: number | null;
  stopAtrMult?: number | null;
  brakeSlowDdPct?: number | null;
  brakeHaltDdPct?: number | null;
  brakeLossStreak?: number | null;
}

/**
 * 生效纪律自检的一行（13 行固定顺序，后端 `discipline_view` 装配）。
 *
 * `threshold` / `firedCount` 为 `null` 表示**算不出**，绝不是 0——只报「装配了」而不报「拦了多少」
 * 的面板，在「闸门把每一单都拦了」这个最危险的断面上与「一切正常」长得一模一样。
 */
export interface AiTradeDisciplineRow {
  key: string;
  label: string;
  armed: boolean;
  threshold?: string | null;
  source: string;                 // preset | rule | runtime
  note?: string | null;           // armed=false 时后端**恒**给（为什么没生效、怎么开）
  firedCount?: number | null;     // 本会话累计触发次数；算不出 ⇒ null
  tone?: "ok" | "warn" | "danger" | null;
}

/** 会话级 `status.disciplines`：13 行 + [P1-9] 恒真自检提示（一堆闸武装着却一单没开过）。 */
export interface AiTradeDisciplineDigest {
  rows: AiTradeDisciplineRow[];
  hint?: string | null;
  // 逆势通路的会话配额（directional-symmetry [P2-4]）：**不落库、冷续跑归零**是一个有意的
  // 取舍，但「本会话已用 N/M」必须让用户看得见——否则「重启刷配额」是一个既存在又不可见的
  // 行为。未装配 / 总闸关 ⇒ 整键缺席 ⇒ 那一行不渲染。
  contrarian?: AiTradeContrarianQuota | null;
}

/** `status.disciplines.contrarian`：本会话逆势试探的已用 / 剩余 / 上限。 */
export interface AiTradeContrarianQuota {
  used: number;
  remaining: number;
  maxPerSession: number;
}

/** 分组级 `group.discipline`：静态预设回执 + 上面那份 digest（`rows` / `hint` 平铺在同层）。 */
export interface AiTradeDisciplineWire extends AiTradeDisciplineDigest {
  preset: AiTradeDisciplinePreset;
  presetLabel: string;
  thresholds?: Record<string, number | boolean> | null;
  // 服务端替用户改过的每一处（共识夹紧 / 暖机根数抬升 / 最小下单量风险），**如实列出**。
  adjustments?: { field: string; from?: number | null; to?: number | null; reason: string }[];
}

/** 只读阈值表的一行（`GET /ai-trade/discipline-presets`）。中文标签与已格式化的值全由后端下发。 */
export interface AiTradeDisciplineThresholdRow {
  key: string;
  label: string;
  value: string;
  off: boolean;      // 该阈值取 0 = 这条闸在本预设下**显式关闭**（灰显，不是「没数据」）
}

export interface AiTradeDisciplinePresetOption {
  id: AiTradeDisciplinePreset;
  label: string;
  rows: AiTradeDisciplineThresholdRow[];
}

export interface AiTradeDisciplinePresetsResponse {
  enabled: boolean;            // 运维总闸；false ⇒ 本机整轴关闭，UI 须如实说明而不是把表藏起来
  defaultPreset: AiTradeDisciplinePreset;
  presets: AiTradeDisciplinePresetOption[];
}

export type AiTradeBackstopBasis = "exchange" | "local" | "unavailable";

export interface AiTradeBackstopWire {
  basis: AiTradeBackstopBasis;
  unavailableReason?: string | null;
  checkedAgoSeconds?: number | null;          // null ⇒ 从未核实
  stopLossPx?: number | null;                 // **交易所读数**；未核实态恒 null
  takeProfitPx?: number | null;
  hasStop?: boolean | null;
  coveredSz?: number | null;
  positionSz?: number | null;
  // **`null` 是合法值**＝「还不知道」，与 `0`＝「确认裸露」是两个结论（[P0-2] / D-14）：
  // 前者一律灰色、G6 放行；只有后者与「有数且低于阈」才是确定的坏消息。
  coverageRatio?: number | null;
  orders?: number | null;
  localStopLossPx?: number | null;            // 本地成交回执口径（**不是**交易所读数）
  localTakeProfitPx?: number | null;
  mismatch: boolean;                          // 本地声称有止损、交易所侧确认一条都没有
  // 自愈层的**嵌套加法子键**（backstop-repair §4.4 / D-10）：嵌在这个整对象透传的 wire 里，
  // 故 `store.ts` 的逐键手抄 reducer 一行都不必动。未装配 ⇒ **整键缺席**（不是 null）。
  repair?: AiTradeBackstopRepairWire;
}

// 自愈状态机（后端 `backstop_repair` 的常量逐条镜像）。`placed` 与 `verified` 是**两个**状态：
// OKX 回了一个 algoId 不等于那张单真的挂在那里，核实通过前一律说「已发出，正在核实」。
export type AiTradeRepairState =
  | "idle" | "planned" | "blocked" | "placing" | "placed" | "verified" | "failed";

export interface AiTradeRepairResult {
  ok: boolean;
  ts: number;
  algoId?: string | null;
  algoClOrdId?: string | null;
  stopPx?: number | null;
  sz?: number | null;
  code?: string | null;
  // 失败原因的中文标签，由后端**预渲染**（归一码的值域归 `okx.trading` 所有，前端抄一份必漂移
  // 成一个原始英文串）。呈现只读这一个键，`code` 只作核对线索。
  codeLabel?: string | null;
  message?: string | null;
}

export interface AiTradeBackstopRepairWire {
  mode: AiTradeBackstopRepairMode;
  state: AiTradeRepairState;
  blockedReason?: string | null;              // state==="blocked" 时必填（值域四处镜像）
  plannedStopPx?: number | null;              // 算不出 ⇒ null
  plannedSz?: number | null;                  // **null=算不出、0=不需要补**，两个结论
  coveredSz?: number | null;                  // 检测层原样转发（本层不重算）
  positionSz?: number | null;
  attemptsEpoch: number;
  maxPerEpoch: number;
  attemptsSession: number;
  maxPerSession: number;
  coolingSeconds?: number | null;             // 未冷却 ⇒ null（**绝不用 0 冒充**）
  lastResult?: AiTradeRepairResult | null;    // 从未尝试 ⇒ null
}

// 判定态：空仓 / 持仓中 / 限制已解除。
export type AiTradeHorizonStatus = "flat" | "holding" | "released";
// 门禁段状态：放行 / 否决 / 豁免 / 空仓（与后端 `hold_horizon.gate_status` 镜像）。
export type AiTradeHorizonGateStatus = "pass" | "veto" | "released" | "idle";

// 每轮快照（payload / decision / status / trace 共用的唯一 wire，后端 §5.4）。
export interface AiTradeHorizonSnapshot {
  profileId: AiTradeHorizonProfileId;
  title: string;
  targetLabel?: string | null;
  status: AiTradeHorizonStatus;
  side?: string;
  holdSeconds: number;
  barsInPosition?: number | null;
  targetHoldMinSeconds: number;
  targetHoldMaxSeconds: number;
  minHoldSeconds: number;
  minHoldRemainingSeconds: number;
  exitConfirmRounds: number;
  exitConfirmStreak: number;
  confirmWindowSeconds: number;
  confirmWindowRemainingSeconds?: number | null;
  confirmWindowElapsed: boolean;
  turnoverHysteresisK: number;
  moveBps?: number | null;
  costBps?: number | null;
  requiredMoveBps?: number | null;
  returnPct?: number | null;
  lossReleaseReturnPct: number;
  released: boolean;
  releaseReason?: string | null;
  releasedGates: string[];               // 逐闸解除矩阵（[P0-2]：只写 released 会留下歧义）
  blockedStreak: number;
  blockedStreakNote?: boolean;           // 未武装 ⇒ **整键缺席**（不是 false）
  expectedHoldBars?: number | null;
  openedWallEstimated: boolean;
  gateReverse: boolean;
  asOfWall: number;                      // 本快照采集时刻（[P1-7] 双快照契约的可验证凭据）
  exchangeStopLossPx?: number | null;
  exchangeTakeProfitPx?: number | null;
  // 两个嵌套加法键（turnover-budget [P1-1] / D-10）：挂在**这里**而不是 `tradingHorizon`——
  // 前者是运行期快照、后者是契约。嵌套即可自动搭上 payload / decision / status / trace 四条
  // 既有载体（均为整对象透传），无需逐键补抄 reducer（`edgeSource` 那次的教训）。
  // 未装配 / 总闸关 / 回测 ⇒ **整键缺席**（不是 null）。
  turnover?: AiTradeTurnoverWire;
  backstop?: AiTradeBackstopWire;
  // 离场优先级与离场意图兑现（exit-priority §5.3）：同样挂在**快照**下而不是契约下——嵌套即自动
  // 搭上 payload / decision / status / trace 四条既有载体（均为整对象透传），`store.ts` 的逐键手抄
  // reducer 一行都不必动。未装配 / 本轮非平仓 / 未武装 ⇒ **整键缺席**（不是 null）。
  exitPriority?: AiTradeExitPriorityWire;
  exitCommitment?: AiTradeExitCommitmentWire;
}

/**
 * `POST /ai-trade/{gid}/member/{inst}/exit` 的回执（exit-priority §6.2）。
 *
 * 后端**刻意返回裸 dict、不绑 `response_model`**（同类两个端点同款）：`response_model` 会静默吞掉
 * 新 wire 键，那是本仓库出过事的形态。故此处声明的是「至少有这些键」，多余键原样保留。
 */
export interface AiTradeMemberExitResult {
  ok: boolean;
  instId: string;
  groupId?: string;
  flattened?: Record<string, unknown> | null;
  positionAfter?: string;
  restingCancelled?: boolean;
  message?: string;
}

/** 五档离场优先级（exit-priority §4.3.1 冻结判定表）；与后端 `exit_priority.LEVELS` 镜像。 */
export type AiTradeExitLevel = "none" | "normal" | "expedite" | "adjudicated" | "override";

/** 模型显式声明的离场确信度（决策工具 `exitConviction` 的值域）。 */
export type AiTradeExitConviction = "normal" | "high" | "final";

/** `final` 声明未能兑现为 `override` 的四种原因（自上而下取第一条）。 */
export type AiTradeExitDowngrade =
  | "evidence_missing"
  | "ledger_short"
  | "quota_exhausted"
  | "min_hold_floor";

/**
 * 每轮离场优先级快照（exit-priority §5.3）。
 *
 * `releasedGates` 与豁免矩阵的同名键**并列而不合并**：两者在轨迹上并排展示，用户据此分清
 * 「是行情替你解的锁」还是「是你的确信度替你解的锁」。
 */
export interface AiTradeExitPriorityWire {
  level: AiTradeExitLevel;
  declared: AiTradeExitConviction;
  downgradeReason?: AiTradeExitDowngrade | null;
  releasedGates: string[];
  intentStreak: number;
  intentLapses: number;
  holdSeconds: number;
  overrideRounds: number;
  overrideMinHoldSeconds: number;
  overridesUsed: number;
  overridesRemaining: number;
  maxOverridesPerSession: number;
  adjudicated: boolean;
  /** 本轮是否**允许**挂起（≡ 降档原因是 `ledger_short` 且配额未尽）。 */
  mayPend: boolean;
  /** 本轮是否已挂起待裁决。 */
  pending: boolean;
  /** 挂起时「还差什么」的原门禁码（放行码与门禁码语义不同，故单独一键，不混进 `reason`）。 */
  pendingReason?: string | null;
  /** 非 null ⇒ 本笔离场**未经复核官复核**（基础设施失败下的 fail-open 放行）。 */
  failOpenReason?: string | null;
  reason?: string | null;
}

/** 离场意图兑现状态（exit-priority §5.3）；未装配 / 未武装 ⇒ 整键缺席。 */
export interface AiTradeExitCommitmentWire {
  armed: boolean;
  attempts: number;
  maxAttempts: number;
  ageSeconds: number;
  maxAgeSeconds: number;
  retryIntervalSeconds: number;
  priorityLevel: string;
  reasonCode: string;
  lastError?: string | null;
  /** 已到顶（次数或时长），不再自动重试 ⇒ 面板红条 + 【立即离场】按钮。 */
  stuck: boolean;
}

// 落库 `metricsJson.aiStats.horizon`（§2.12）；零样本时六个派生量为 **null 而非 0**（[P2-3]）。
export interface AiTradeHorizonStats {
  profileId: string;
  title: string;
  minHoldSeconds: number;
  exitConfirmRounds: number;
  turnoverHysteresisK: number;
  expectedHoldBars?: number | null;
  minHoldVetoes: number;
  unconfirmedVetoes: number;
  hysteresisVetoes: number;
  reverseVetoes: number;
  releasedRounds: number;
  releaseReasons: Record<string, number>;
  confirmWindowReleases: number;
  roundTrips: number;
  estimatedEpochs: number;
  // 开仓侧换手纪律的三个只增计数（turnover-budget §4.3）。`budgetVetoes` 是「模型想换手而被制度
  // 拦住」的次数——它比换手率本身更能说明周期模式是否与该标的、该行情匹配。
  reentryVetoes?: number;
  budgetVetoes?: number;
  opensCounted?: number;
  // 离场优先级的七个只增计数（exit-priority §5.4 / X-9）。**这一组必须有展示落点**：
  // 「Agent 用掉了几次最高优先级、其中几次被降档、几次是没经复核就放行的」是这条通道唯一能被
  // 事后证伪的读数——记了却没人读，等于没记。老记录缺键 ⇒ 整行不渲染（不显示一排误导性的 0）。
  exitExpediteRounds?: number;
  exitOverrideRounds?: number;
  exitAdjudicatedRounds?: number;
  exitFinalDeclared?: number;
  exitFinalDowngraded?: number;
  exitPendingReleases?: number;
  exitFailOpenRounds?: number;
  avgHoldSeconds?: number | null;
  medianHoldSeconds?: number | null;
  minHoldObservedSeconds?: number | null;
  maxHoldObservedSeconds?: number | null;
  withinTargetRate?: number | null;
  turnoverPerDay?: number | null;
}

// ---- 持仓周期验收闭环（ai-trade-horizon-acceptance-loop）：四个 wire 类型与后端 schemas 逐字对齐 ----
// 三条口径纪律（读点必须遵守，否则卡片会撒谎）：
// 1. `value == null` 与 `value === 0` 是两个结论：前者「没算成」、后者是真实读数，绝不 `?? 0`；
// 2. `verdict === "insufficient"` 永远不显示为绿色——它是「什么都还不知道」，不是「快通过了」；
// 3. `exits` / `funding` 是**顶层键**，`acceptance` 为 null 时它们仍可非空（关掉周期模式跑对照臂
//    是上线观察窗口的必要一臂，数据记了却看不到等于没记）。
export type AiTradeAcceptanceVerdict = "pass" | "fail" | "insufficient";

export interface AiTradeAcceptanceRow {
  key: string;
  label: string;
  verdict: AiTradeAcceptanceVerdict;
  value?: number | null;
  unit: string;                 // seconds | ratio | count | usd
  display: string;              // 后端渲染好的展示串（前端不复制任何格式化规则）
  target: string;
  note?: string | null;
  inPositionValue?: number | null;  // 第 3 行的在场口径对照值
  basis?: string | null;            // 第 5 行的资金费口径
}

export interface AiTradeExitSourceRow {
  source: string;
  count: number;
  share?: number | null;
  netUsd: number;
  avgHoldSeconds?: number | null;
  winRate?: number | null;      // 样本 < 5 ⇒ null ⇒ 显示「样本不足」，绝不用 2 笔冒充结论
}

export interface AiTradeExitMix {
  totalExits: number;
  bySource: AiTradeExitSourceRow[];
}

export interface AiTradeFundingStats {
  enabled: boolean;
  basis: string;                // settled+accrued | accrued | unavailable
  accruedClosedUsd: number;
  accruedOpenUsd?: number | null;
  settledUsd?: number | null;
  fundingCostUsd?: number | null;   // null=口径不可得，0=真的没跨过结算点（两个结论）
  crossings: number;
  intervalKnown: boolean;
  intervalS?: number | null;    // intervalKnown=false ⇒ null（后端绝不下发兜底的 8h）
  lastRateBps?: number | null;
  staleTicks: number;
  unknownIntervalTicks: number;
  capHits: number;
  settledTrusted: boolean;
  note: string;
}

export interface AiTradeAcceptance {
  profileId: string;
  title: string;
  verdict: AiTradeAcceptanceVerdict;
  sampleFloor: number;
  roundTrips: number;
  truncatedSamples: number;
  sampleNote: string;
  rows: AiTradeAcceptanceRow[];
  // 顶层同名键的镜像（同一对象）；前端一律读顶层，此处只供就近取数。
  exitMix?: AiTradeExitMix | null;
  funding?: AiTradeFundingStats | null;
  diagnostics: string[];
  // 多标的分组的成员级摘要（R-10）：合并只用于总判定，但「一个赚一个亏被平均成还行」是这张卡
  // 最危险的读法。单标的分组 ⇒ 整键缺席。
  bySymbol?: AiTradeAcceptanceSymbol[] | null;
}

export interface AiTradeAcceptanceSymbol {
  instId: string;
  roundTrips: number;
  netPnlUsd: number;
  verdict?: AiTradeAcceptanceVerdict | null;
}

// 弹窗的「验收门槛预览」（选模式那一刻就看见将被按什么标准判定）。
export interface AiTradeAcceptanceThresholds {
  sampleFloor: number;
  minAvgHoldSeconds?: number | null;
  minWithinTargetRate?: number | null;
  maxTurnoverPerDay?: number | null;
  maxFeeToGross: number;
  requiresPositiveNetAfterFunding: boolean;
}

// 【基础策略】MA 通道开单约束（与后端 base_rules.MaBandRule 镜像；数值字段留空→服务端按 settings 回填）。
export interface AiTradeMaBandRule {
  enabled: boolean;
  period?: number | null;      // 中轨 SMA 周期（2~200）
  stdMult?: number | null;     // 上下轨标准差倍数（0~5）
  tolerance?: number | null;   // %B 锚区容差（0~0.25；0.25=仅限带内）
  directional?: boolean | null; // 做多不追顶 / 做空不杀底（默认关）
}

// 【基础策略】铁律集合（本期只有一条预置规则）；整个字段省略 ⇒ 服务端默认**开启**。
export interface AiTradeBaseRules {
  maBandEntry?: AiTradeMaBandRule | null;
}

// 铁律门禁判定态：放行 / 否决 / 通道数据不可用（fail-closed）。
export type AiTradeBaseRuleStatus = "pass" | "veto" | "unavailable";

// 最小 edge 门禁判定态：放行 / 否决 / 估算不可用。
// 与铁律层**同名三态但极性相反**：本层 unavailable 时开仓一律**放行**（fail-open），文案不得反过来说。
export type AiTradeEdgeGateStatus = "pass" | "veto" | "unavailable";

// 利润保护层入参（与后端 profit_guard.ProfitGuardRule 镜像；数值字段留空→服务端按 settings 回填）。
// 全部收益率为**保证金收益率口径**（5× 杠杆下标的 10% ≈ 保证金 +50%，用标的口径根本触发不到）。
export interface AiTradeProfitGuardRule {
  enabled: boolean;
  armReturnPct?: number | null;              // 武装移动止盈的峰值保证金收益率（0~5）
  givebackPct?: number | null;               // 自峰值允许回吐比例（0~1）
  breakevenArmReturnPct?: number | null;     // 武装保本线的峰值收益率（0=关闭保本）
  breakevenOffsetReturnPct?: number | null;  // 保本线正向偏移（覆盖手续费）
  minHoldSeconds?: number | null;            // 最短持有期（秒）
  hardTakeProfitReturnPct?: number | null;   // 硬止盈收益率（null 与 0 等价，均表关闭）
  // ---- 自适应（ai-trade-profit-protection-adaptive §4.1）：留空→服务端按 settings 回填 ----
  // 利润阶梯：峰值收益越高、允许回吐越少。阈值须严格递增、回吐比例须单调不增（后端 422 硬约束）；
  // `[]` = 显式关闭阶梯（退回固定 givebackPct），与「字段留空 = 用服务端默认阶梯」严格区分。
  givebackTiers?: AiTradeGivebackTier[] | null;
  atrFloorMult?: number | null;              // 回吐**距离**下界 = 该倍数 × ATR；0 = 关闭下界
  atrCapMult?: number | null;                // 回吐**距离**上界 = 该倍数 × ATR；0 = 关闭上界
  cooldownSeconds?: number | null;           // 保护性平仓后的冷却秒数；0 = 关闭冷却
  cooldownScope?: "same_side" | "both" | null;
  // 承诺退出线总闸（ai-trade-profit-protection-commitment C1）：仅**只读回执**（后端 rule_wire 下发），
  // 前端据此决定是否展示承诺线区块；不作为可提交的会话参数（承诺是系统能力、由运维 settings 控制）。
  commitEnabled?: boolean | null;
}

// 利润阶梯的一档：峰值保证金收益率越过 `peakReturnPct` 即改用该档的 `givebackPct`。
export interface AiTradeGivebackTier {
  peakReturnPct: number;
  givebackPct: number;
}

// 会话成本快照（ai-trade-cost-truth-and-net-pnl §5.4 / P1-1）：后端 `build_cost_stats` 的唯一产物，
// 同时作为 WS `costSnapshot` 与落库 `metricsJson.aiStats`——**两条通道必然同形**，故此处一个接口
// 描述两处。`feeRates` / `reconcile` 挂**顶层**，`sessionFeeUsd` 挂 `cost` 子对象（与实测键同族）。
// 全部可选：老会话 / 总闸关的会话根本不下发这些键，读点一律走「缺键即整段隐藏」的降级范式。
export interface AiTradeCostSnapshot {
  cost?: Record<string, number | null> & { sessionFeeUsd?: number };
  edgeGate?: Record<string, number | null>;
  decisionConversionRate?: number | null;
  feeRates?: FeeRatesWire | null;
  reconcile?: ReconcileWire | null;
  priorCoverage?: number | null;
  priorAgeS?: number | null;
}

// 深度思维链的逐轮元数据（ai-trade-deep-reasoning-chain §4.2）：**整份**挂在 `result` 相位上
// （沿用 `horizonWire` 整对象透传的先例），后端刻意不新增 `result` 子相位——新增一个相位在本
// 仓库的代价是 8 个前端镜像点，而这是一份轮末才定型的只读读数。off 档 ⇒ 整键缺席 ⇒ 全段不渲染。
export interface AiTradeReasoningWire {
  depth: string; // off | standard | deep | exhaustive
  chars: number; // 研判全文字符数（截断后）
  stages: number; // 命中的 `## S<k>` 阶段标记数
  truncated: boolean; // 输出预算耗尽在工具调用之前（provider 归一口径）
  reasked: boolean; // 本轮触发过浅研判追问
  reaskAdopted: boolean; // 追问结果被采纳（false = 追问跑了但保留首次决策）
  deadlineHit: boolean; // 本轮因轮级墙钟闸提前收敛
  inputTokens?: number | null;
  outputTokens?: number | null;
  elapsedMs?: number | null;
  reflectionChars?: number | null;
  reflectionStages?: number | null;
}

// 「研判耗时逼近唤醒周期」压力提示（§3.4.3）：**只读提示，绝不自动改唤醒周期**。
// 关闭定时唤醒（`wakeIntervalMinutes = 0`）的会话恒不下发这个键——对不按周期交易的人提周期
// 建议是纯噪音。字段名是 `ewmaMs` 而非 `p50Ms`：算的是 α=0.3 的指数移动平均，不是中位数。
export interface AiTradeReasoningPressure {
  ewmaMs: number;
  intervalS: number;
  suggestIntervalMin: number;
  level: string; // warn
}

// agent_trace 事件 data（各字段按 phase 可选；前端 reducer 按 phase 归约）。
export interface AiTradeTraceEvent {
  phase: AiTradeTracePhase;
  round: number;
  ts: number;
  delta?: string; // thinking / analysis 合并增量
  name?: string; // tool_call_start / tool_call 工具名
  input?: {
    action: AiTradeAction;
    sizeFraction?: number | null;
    confidence?: number | null;
    reason: string;
    orderType?: "market" | "limit" | null; // 委托方式（缺省 market）
    limitPrice?: number | null;
    // 面向操作员的中文证据摘要（deep-reasoning-chain §3.1.7）：模型提交的可选工具入参。
    // 未装配 / off 档 / 模型没给 ⇒ **整键缺席**（不是 null）⇒ 收起态标题回退到 S12 首句。
    evidenceDigest?: string | null;
  }; // tool_call：门禁后最终决策工具入参
  executed?: AiTradeDecision["executed"]; // result（复用既有 executed 形状）
  degradeReason?: string | null; // result
  // result：深度思维链元数据（整份对象；off 档 ⇒ 整键缺席）。
  reasoning?: AiTradeReasoningWire | null;
  summary?: string; // summary
  message?: string; // error
  lastPx?: number; // round_start
  position?: unknown | null; // round_start
  signalBoard?: StrategySignalView[]; // round_start
  marketContext?: MarketContext | null; // round_start
  consensus?: Consensus | null; // round_start
  marketDigest?: MarketDigest | null; // round_start（R1：价格走势/成交量/因子证据摘要）
  multiTimeframeContext?: MultiTimeframeContext | null; // round_start（决策级中线背景）
  profitGuard?: ProfitGuardSnapshot | null; // round_start：利润保护断面（未装配→ null）
  // round_start：保护性平仓冷却期（未激活→ null）。轨迹上「本轮为什么被降级」靠它自解释。
  profitGuardCooldown?: ProfitGuardCooldown | null;
  attempt?: number; // retry：当前重试序号（1-based）
  maxAttempts?: number; // retry：本轮最大重试次数
  reason?: string; // retry：触发本次重试的上一次 degrade 原因 / reflection_verdict：反思理由
  trigger?: "candle" | "interval"; // round_start：唤醒来源（K 线唤醒 / 墙钟定时唤醒）
  // round_start：唤醒原因 + 战术周期证据 + 两个披露档（wake-aware-context §4.3）。
  wake?: WakeInfo | null;
  tacticalContext?: TacticalContext | null;
  tacticalTier?: "full" | "brief" | null;
  contextDisclosure?: "full" | "repeat" | null;
  // round_start：本轮实际喂入的交易经验（未装配 ⇒ null ⇒ 徽标整枚不渲染）。
  experience?: import("./experienceTypes").AiTradeExperienceInjection | null;
  verdict?: AiReflectionVerdict; // reflection_verdict：反思裁决（confirm/abort）
  adjustedSizeFraction?: number | null; // reflection_verdict：实际收缩后的开仓仓位占比（无收缩为 null）
  // base_rule：【基础策略】铁律门禁判定 + 本轮通道断面（band 不可用时三条线为 null，不假数据）。
  // edge_gate 复用同一个 `status` 位（三态取值相同；两个相位互不同时出现在一条事件上）。
  // horizon 相位复用同一个 `status` 位，但取值域更宽（多 `released` / `idle` 两态）——三个相位互不
  // 同时出现在一条事件上，故联合类型不会产生歧义。
  status?: AiTradeBaseRuleStatus | AiTradeHorizonGateStatus;
  zone?: "lower" | "mid" | "upper" | null;
  pctB?: number | null;
  upper?: number | null;
  mid?: number | null;
  lower?: number | null;
  // edge_gate：粗估毛 edge / 往返成本 / 阈值倍数与判据（缺失即该轮未估出，本层 fail-open）。
  edgeBps?: number | null;
  costBps?: number | null;
  kMin?: number | null;
  requiredBps?: number | null;
  costConfidence?: "ok" | "degraded" | null;
  edgeSource?: "atr" | "take_profit" | "prior" | null;
  // 信号先验读数（signal-prior-and-ev-gate §4.2）：`priorP` / `priorEvBps` 是本轮该方向的先验；
  // `priorWouldVeto` 是**反事实**——总闸③关闭（先验只展示、不进门禁）时也照发，供 L1/L2 观察
  // 窗口先看清「开闸后会拦掉多少」，再据此定 k_min。
  priorP?: number | null;
  priorEvBps?: number | null;
  priorWouldTargetBps?: number | null;
  priorWouldVeto?: boolean | null;
  priorContributors?: unknown[] | null;
  // round_start：本轮先验输入快照（三总闸全关的会话恒无此键 ⇒ 组件整段回退旧文案）。
  signalPriors?: Record<string, unknown> | null;
  // §5.4 会话内观测通道：成本统计只读快照。收紧为具名接口（cost-truth §3.5 F-5），顶层含
  // cost / edgeGate / reconcile / feeRates 四段——与后端 `build_cost_stats` 的挂载层级逐字一致。
  // **该键仍不进 trace reducer**（`aiTradeEdgeGate` 逐键手抄，未识别键被忽略）：本轮真正被组件
  // 消费的是**落库**通道 `metrics.aiStats.{reconcile,feeRates}`（历史详情的交易所对账卡），
  // 两条通道同源同形（`build_cost_stats` 是唯一产物），故此处只需如实声明形状。
  costSnapshot?: AiTradeCostSnapshot | null;
  // sizing：仓位求解 + ATR 止损 + 刹车断面（vol-sizing-and-drawdown-brake §3.9，与后端
  // trace.SIZING_TRACE_KEYS 逐键镜像）。三条规则全未装配的会话根本收不到这个相位。
  brakeLevel?: "none" | "slow" | "halt" | "cooldown" | null;
  brakeReason?: string | null;
  drawdownPct?: number | null; // **正号**口径（0.08 = 回撤 8%），与 equity 事件的负号 drawdown 不同
  lossStreak?: number | null;
  peakEquityBasis?: "markToMarket" | null; // 峰值含未实现盈亏，与利润保护层的「允许回吐」相互作用
  unrealizedShare?: number | null;
  modelFraction?: number | null;
  fVol?: number | null;
  fKelly?: number | null;
  fCap?: number | null;
  kellyStatus?: "ok" | "no_prior" | "no_cost" | "no_bracket" | "disabled" | null;
  fFinalRaw?: number | null; // **吸附前**的解；与 finalFraction 之差 = 被档位地板吃掉的量
  finalFraction?: number | null;
  scaleSource?: "model" | "vol" | "kelly" | "brake" | "correlation" | null;
  scaleErased?: boolean | null; // 缩减被档位地板整个吃掉了（计数在涨但什么也没发生）
  volSource?: "realized" | "insufficient_samples" | "disabled" | null;
  stopPct?: number | null;
  takePct?: number | null;
  // 七态（tradability-and-open-funnel 轴 B）：`atr_floored` = 止损被成本下界抬起来了；
  // `floor_above_cap` = 下界高于用户可见红线 ⇒ 整条 ATR 覆盖回退固定 pct。
  // ⚠️ 三处必须同步：本联合类型（两处）+ `lib/aiTrade.ts` 的 `STOP_SOURCE_LABEL`——漏一处即静默空串。
  stopSource?: AiTradeStopSource | null;
  effectiveExposure?: number | null;
  effectiveExposureCap?: number | null;
  corrScale?: number | null;
  exposureCapUnreachable?: boolean | null; // 其余成员自身已越界，缩到 0 也达不到上限（如实标注，不假装）
  // horizon：持仓周期铁律判定（ai-trade-holding-horizon-modes §4.4）。整份快照随事件下发（本层读数
  // 有 20 余个，逐键搬运必然在下一次加字段时漏掉）。`status` 位与 base_rule/edge_gate 互不同时出现。
  horizon?: AiTradeHorizonSnapshot | null;
  // tool_call：模型**提议**了什么、被谁拦下（exit-priority F-6）。三键作为事件的**兄弟键**下发，
  // 刻意不塞进 `input`——`input` 的语义是「模型提交的工具入参」，掺进非入参的键会让卡片本身撒谎。
  // 未被拦 / 老快照 ⇒ 三键为 null ⇒ 卡片逐字节回退到今天的样子。
  proposedAction?: AiTradeAction | null;
  proposedReason?: string | null;
  vetoedBy?: string | null;
  // 中文层名（directional-symmetry B-3）：作用域从 `horizon` 泛化到八道确定性门禁之后，
  // **标签必须由后端下发**（`discipline._THRESHOLD_LABELS` 的成文纪律）——前端抄一份，下次加
  // 一层就会把 `consensus` 这样的英文 key 怼到用户脸上。总闸关 / 老快照 ⇒ 缺席 ⇒ 逐字回退。
  layerLabel?: string | null;
  // reflection_verdict：反思**之后**才定型的最终离场档位（含 `adjudicated` / `failOpenReason`）。
  // **刻意不新开 `exit_priority` 相位**：那要付出四处逐键手抄的代价，而漏抄不会报错、只会静默
  // 功能缺失（R-9）。非反思轮读 `horizonWire.exitPriority`（整对象透传的那一份）即可。
  exitPriority?: AiTradeExitPriorityWire | null;
}

// store / snapshot 的已装配单轮轨迹（thinking/analysis 累积；env 环形 ≤20）。
export interface AiTradeRoundTrace {
  round: number;
  ts: number;
  lastPx?: number;
  position?: unknown | null;
  signalBoard: StrategySignalView[];
  marketContext?: MarketContext | null;
  consensus?: Consensus | null;
  marketDigest?: MarketDigest | null; // R1：价格走势/成交量/因子证据摘要（round_start 承载）
  multiTimeframeContext?: MultiTimeframeContext | null;
  trigger?: "candle" | "interval"; // wake-interval：唤醒来源（供 describeTrigger 区分周期唤醒）
  // 唤醒感知上下文（wake-aware-context §3.3）：三总闸全关的会话恒为 null ⇒ 轮头与战术块整段回退。
  // **必须与 emptyRoundTrace 的逐键手抄保持一致**——漏一个键，后端发什么前端都收不到。
  wake?: WakeInfo | null;
  tacticalContext?: TacticalContext | null;
  tacticalTier?: "full" | "brief" | null;
  contextDisclosure?: "full" | "repeat" | null;
  // 本轮实际喂入的交易经验（experience-context-graph §7.3）：**必须与 emptyRoundTrace 的逐键
  // 手抄保持一致**——漏抄这一键，后端发什么前端都收不到，且跑不出错。
  experience?: import("./experienceTypes").AiTradeExperienceInjection | null;
  thinking: string; // 累积深度思考（未开启扩展思考时通常为空）
  analysis: string; // 累积自然语言研判分析（流式主可见内容）
  // 浅研判追问的**独立**累积槽位（deep-reasoning-chain §4.2 / P0-3）：**绝不并进 `analysis`**。
  // `analysis` 是纯追加归约、唯一清零点是 `retry` 相位，而追问跑在重试循环之后——沿用同一字段
  // 会把 S1–S12 追加成两遍（导轨读出 24/12），而追问**本来就可能被拒收**。两份并列呈现是刻意的。
  // **必须与 emptyRoundTrace 的逐键手抄保持一致**——漏一个键，后端发什么前端都收不到。
  reaskAnalysis?: string;
  // 本轮研判元数据（`result` 相位整份归约）；未装配 / off 档恒为 null ⇒ 相关段整段不渲染。
  reasoning?: AiTradeReasoningWire | null;
  toolCallPending?: boolean;
  toolCall?: AiTradeTraceEvent["input"] | null;
  executed?: AiTradeDecision["executed"];
  degradeReason?: string | null;
  summary?: string | null;
  errorMessage?: string | null;
  retries?: number; // §4.4：本轮已重试次数（retry 阶段累加）
  lastRetryReason?: string | null; // 最近一次触发重试的 degrade 原因
  // 下单前二次反思（ai-trade-pre-trade-reflection §5.2）：仅交易意愿轮被写入，hold/关闭轮维持初值。
  reflection?: string; // 累积反思复核推理文本（默认 ""）
  reflectionPending?: boolean; // 反思在飞指示（默认 false）
  reflectionVerdict?: AiReflectionVerdict | null; // 反思裁决（默认 null）
  reflectionReason?: string | null;
  reflectionAdjustedSize?: number | null; // 实际收缩后的仓位占比（无收缩为 null）
  // 【基础策略】铁律门禁（ai-trade-base-strategy-ma-band §2.9）：规则未装配的会话恒为 null。
  baseRuleStatus?: AiTradeBaseRuleStatus | null;
  baseRuleZone?: "lower" | "mid" | "upper" | null;
  baseRulePctB?: number | null;
  baseRuleReason?: string | null;
  // 最小 edge 门禁（ai-trade-cost-accounting-and-edge-gate §4.3）：k_min=0（默认）的会话恒为 null。
  edgeGateStatus?: AiTradeEdgeGateStatus | null;
  edgeBps?: number | null;
  edgeCostBps?: number | null;
  edgeGateReason?: string | null;
  // 信号先验（signal-prior-and-ev-gate §3.10）：**加法键**，后端刻意不新增 trace phase。
  // `edgeSource` 决定 EdgeGateBlock 的说明文案——接入先验后「粗估毛收益 = min(ATR 预算, 单仓止盈
  // 距离)」这句话会说谎，必须按来源三分。三总闸全关的会话恒为 null。
  edgeSource?: "atr" | "take_profit" | "prior" | null;
  priorP?: number | null;
  priorEvBps?: number | null;
  signalPriors?: Record<string, unknown> | null;
  // 利润保护快照（ai-trade-profit-protection §2.7）：规则未装配的会话恒为 null；**不新增 trace phase**
  // ——保护层跑在 5s 盯市上、不是轮级判定，只随 round_start 带一份断面。
  profitGuard?: ProfitGuardSnapshot | null;
  // 冷却期断面（adaptive §4.2 三载体之二）：本轮若被冷却门禁降级，UI 据此说清原因与剩余时间。
  profitGuardCooldown?: ProfitGuardCooldown | null;
  // 仓位求解 + ATR 止损 + 回撤刹车（vol-sizing-and-drawdown-brake §3.9）：三条规则全未装配的会话恒为
  // null ⇒ SizingBlock 整段不渲染。**必须与 emptyRoundTrace 的逐键手抄保持一致**——漏一个键，后端发
  // 什么前端都收不到（跑不出错、功能静默缺失，这是阶段二第一步踩过的原坑）。
  brakeLevel?: "none" | "slow" | "halt" | "cooldown" | null;
  brakeReason?: string | null;
  drawdownPct?: number | null;
  lossStreak?: number | null;
  peakEquityBasis?: "markToMarket" | null;
  unrealizedShare?: number | null;
  modelFraction?: number | null;
  fVol?: number | null;
  fKelly?: number | null;
  fCap?: number | null;
  kellyStatus?: "ok" | "no_prior" | "no_cost" | "no_bracket" | "disabled" | null;
  fFinalRaw?: number | null;
  finalFraction?: number | null;
  scaleSource?: "model" | "vol" | "kelly" | "brake" | "correlation" | null;
  scaleErased?: boolean | null;
  volSource?: "realized" | "insufficient_samples" | "disabled" | null;
  stopPct?: number | null;
  takePct?: number | null;
  // 七态（tradability-and-open-funnel 轴 B）：`atr_floored` = 止损被成本下界抬起来了；
  // `floor_above_cap` = 下界高于用户可见红线 ⇒ 整条 ATR 覆盖回退固定 pct。
  // ⚠️ 三处必须同步：本联合类型（两处）+ `lib/aiTrade.ts` 的 `STOP_SOURCE_LABEL`——漏一处即静默空串。
  stopSource?: AiTradeStopSource | null;
  effectiveExposure?: number | null;
  effectiveExposureCap?: number | null;
  corrScale?: number | null;
  exposureCapUnreachable?: boolean | null;
  // 持仓周期铁律层（ai-trade-holding-horizon-modes §3.4 / [P1-4]）：**必须与 emptyRoundTrace 的逐键
  // 手抄保持一致**——`applyAiTradeTracePhase` 对未知相位是静默忽略，漏写不会有任何报错，只会安静地
  // 什么都不显示。契约未装配的会话恒为 null ⇒ HorizonBlock 整段不渲染。
  horizonStatus?: AiTradeHorizonGateStatus | null;
  horizonReason?: string | null;
  horizonWire?: AiTradeHorizonSnapshot | null;
  // 离场优先级（exit-priority §6.3）：**必须与 emptyRoundTrace 的逐键手抄保持一致**——
  // `applyAiTradeTracePhase` 对未知相位是静默忽略，漏写不会有任何报错，只会安静地什么都不显示。
  proposedAction?: AiTradeAction | null;
  proposedReason?: string | null;
  vetoedBy?: string | null;
  // directional-symmetry B-3：与上面三键**同为逐键手抄**——`emptyRoundTrace` 与
  // `reduceRoundPhase` 的 `case "tool_call"` 两处都要写，漏抄不报错、只会安静地什么都不显示。
  layerLabel?: string | null;
  exitPriority?: AiTradeExitPriorityWire | null;
  state: "deciding" | "done" | "skipped" | "error";
}

// GET /ai-trade/status 与 WS snapshot 的会话只读视图（info()）。
export interface AiTradeSessionRow {
  sessionId: string;
  runId: number;
  state: string;
  instId: string;
  bar: string;
  strategyIds: number[];
  strategyNames: string[];
  budgetUsd: number;
  leverage: number;
  takeProfitUsd?: number | null;
  stopLossUsd?: number | null;
  contextRefreshRounds: number;
  experience?: import("./experienceTypes").AiTradeExperienceStatus | null;   // 经验层观测（加法键）
  roundsCompleted: number;
  roundsSinceSummary: number;
  positionSide: string;
  sessionPnlUsd: number;
  startedTs: number;
  lastDecision?: AiTradeDecision | null;
  contextSummary?: string | null;
  degraded?: boolean | null;
  wakeIntervalMinutes?: number | null; // wake-interval：Agent 墙钟定时唤醒周期（分钟；null=关闭）
  // 深度研判三键（deep-reasoning-chain §4.3）：status 事件经 `patchAiTradeMemberStatus` **整体
  // spread** 合并进 `m.session`，故运行期数据本来就在——漏的只是类型声明。未装配 / off 档 ⇒
  // 三键整键缺席（不是 null）⇒ 概览条上的深度 chip / token 累计 / 压力提示条全部不渲染。
  reasoningDepth?: string;
  reasoningTokens?: { inputTokens: number; outputTokens: number };
  reasoningPressure?: AiTradeReasoningPressure;
  // hold-inertia-observability O2：唤醒守卫抑制计数 / 连续真实观望轮数（status/snapshot.status 经
  // 全量 spread 合并进 member.session；REST info() 不带 → WS 连上前短暂 undefined，呈现「—」）。
  wakeSkips?: number;
  holdStreak?: number;
  // 唤醒调度闭环观测（wake-scheduler-closed-loop §4.3）：status 事件经 store.patchAiTradeMemberStatus
  // **全量展开**合并进 member.session，故运行期数据本来就在——漏的只是类型声明，症状是 tsc 报错
  // 而非静默丢数据，但极易被「后端已下发」骗过去。
  // `wakeDrops` 的口径是「**到点了却撞上在飞轮**的唤醒次数」，其中一部分若不在飞也会被守卫跳过，
  // 故它并不等于「系统漏掉了这么多次决策」——呈现处必须写清楚。
  wakeDrops?: number;
  wakeCatchups?: number;
  positionWatchSkips?: number;
  signalFlipWakes?: number;
  // 情报告警的两个计数（actionability §3.4.4）：`intelAlertWakes` = 真的多起了一轮决策的次数，
  // `intelAlertDrops` = 到手却没能起轮（会话不在运行 / 行情陈旧 / 无信号板 / 过期 / 撞上在飞轮）。
  // 两者口径不同，**绝不相加**。
  intelAlertWakes?: number;
  intelAlertDrops?: number;
  // 持仓周期快照（holding-horizon [P1-4] 第 2 处）：`patchAiTradeMemberStatus` 把 status 整体浅合并
  // 进 `m.session`，**类型里没有这个键则面板取不到**（tsc 报错而非静默丢数据，但极易被「后端已下发」
  // 骗过去）。未装配 / 老后端 ⇒ undefined 或 null ⇒ chip 整段不渲染。
  horizon?: AiTradeHorizonSnapshot | null;
  // 验收闭环三键（horizon-acceptance [P0-3] 成员级实时通路）：status 事件经
  // `patchAiTradeMemberStatus` **整体 spread** 合并进 `m.session`，故运行期数据本来就在——
  // 漏的只是类型声明。未装配 / 零样本 ⇒ null ⇒ 卡片整段不渲染。
  acceptance?: AiTradeAcceptance | null;
  exits?: AiTradeExitMix | null;
  funding?: AiTradeFundingStats | null;
  // 生效纪律自检（discipline-preset 轴 B）：status 事件经 `patchAiTradeMemberStatus` **整体 spread**
  // 合并进 `m.session`，故运行期数据本来就在——漏的只是类型声明（tsc 报错而非静默丢数据，但极易被
  // 「后端已下发」骗过去）。老后端 ⇒ undefined ⇒ 纪律条整段不渲染。
  disciplines?: AiTradeDisciplineDigest | null;
  // tradability-and-open-funnel 三新键：可交易性启动断面 / 开仓漏斗 / 摘要健康度。
  // status 事件经 `patchAiTradeMemberStatus` **整体 spread** 合并进 `m.session`，故运行期数据本来
  // 就在——漏的只是类型声明（tsc 报错而非静默丢数据，但极易被「后端已下发」骗过去）。
  // 未装配 / 零样本 / 老后端 ⇒ null 或 undefined ⇒ 各自的卡片整段不渲染。
  tradability?: AiTradeTradabilitySnapshot | null;
  openFunnel?: AiTradeOpenFunnel | null;
  summaryHealth?: AiTradeSummaryHealth | null;
  // 会话级已实现读数（realized-edge-calibration 轴 A）：挂 `_status_payload` **顶层**而不是
  // `costSnapshot` 之下——后者只在 `k_min<=0`（门禁空转）时才发，装了纪律预设的会话一次都收不到。
  // 零往返 / 老后端 ⇒ null 或 undefined ⇒ 整段不渲染（G-9b 的实时侧读点）。
  realized?: AiTradeRealizedStats | null;
  // 成员级生命周期真值（lifecycle-truth B-5 / §5.2）：三条通路同时下发它——WS `status` 经
  // `patchAiTradeMemberStatus` **整体 spread** 合并进 `m.session`、REST 成员行经
  // `groupStateFromStatus` 逐键手抄、WS `lifecycle` 事件经窄 patch 合并。老后端 / 总闸关
  // ⇒ 整键缺席 ⇒ 成员卡的武装说明整段不渲染（自动回落到本轮之前）。
  lifecycle?: AiTradeMemberLifecycle | null;
  // 会话终止原因（`info()` 不带、WS `status` 带）：终态留痕卡要说清「为什么它自己停了」。
  stopReason?: string | null;
  // 成员级暂停断面：`info()` 与 WS `status` **两条通路都在下发**（`ai_auto/session.py`），
  // 漏的只是类型声明。窄 patch 的白名单要能引用它们（[评审 R1-3]）。
  pauseReason?: AiTradePauseReason | null;
  pausedTs?: number | null;
  // 会话状态行的一句话（行情断连 / 已恢复 / 补根结果 / WS 错误帧）。后端**早就在下发它**
  // （`_check_staleness`、`SessionLink._push_message` 都推 `{...status, message}`），但全仓一直
  // **没有读点** —— 「wire 里有事实、用户那里没有」的又一处，本轮补上（F-4）。
  message?: string | null;
}

// WS status 事件负载（_status_payload / 轻量 status，会话视图的可增量合并子集）。
export interface AiTradeStatusEvent {
  state: string;
  positionSide?: string;
  sessionPnlUsd?: number;
  budgetUsd?: number;
  leverage?: number;
  roundsCompleted?: number;
  roundsSinceSummary?: number;
  contextRefreshRounds?: number;
  wakeIntervalMinutes?: number | null; // wake-interval：Agent 墙钟定时唤醒周期（分钟；null=关闭）
  // 深度研判三键（同 AiTradeSessionRow）：status 事件经 patchAiTradeMemberStatus 整体 spread
  // 合并进 m.session，故两侧类型必须同时声明——只补一处，刷新页面后那三个键会在成员卡上凭空消失。
  reasoningDepth?: string;
  reasoningTokens?: { inputTokens: number; outputTokens: number };
  reasoningPressure?: AiTradeReasoningPressure;
  // hold-inertia-observability O2：第 1 轮后端已发 wakeSkips（本轮补齐类型）；holdStreak 为本轮新增。
  wakeSkips?: number;
  holdStreak?: number;
  // 唤醒调度闭环四计数（口径见 AiTradeSessionRow 上的注释；全量 spread 合并进 member.session）。
  wakeDrops?: number;
  wakeCatchups?: number;
  positionWatchSkips?: number;
  signalFlipWakes?: number;
  // 情报告警两计数（同上；口径见 AiTradeSessionRow 上的注释）。
  intelAlertWakes?: number;
  intelAlertDrops?: number;
  degraded?: boolean;
  stopReason?: string | null;
  flattenFailed?: boolean;
  message?: string;
  // 保护性平仓冷却期（adaptive §4.2 三载体之三）：未激活 / 已到期 → null（后端到期会主动补推一次
  // status，否则 chip 会一直挂着）。
  profitGuardCooldown?: ProfitGuardCooldown | null;
  // 持仓周期快照（holding-horizon §4.4）：每次 status 事件随附，供面板 chip 在无决策事件时也能更新
  // 倒计时；未装配 ⇒ null。
  horizon?: AiTradeHorizonSnapshot | null;
  // 验收闭环三键（成员级口径）：随每次 status 事件下发，是运行期卡片的**主通路**。
  acceptance?: AiTradeAcceptance | null;
  exits?: AiTradeExitMix | null;
  funding?: AiTradeFundingStats | null;
  // 生效纪律自检（轴 B 主通路）：每次 status 事件随附；裸仓事实翻转时后端会**补推一次** status
  // （轴 C [P0-2]：刻意不发 `error` 事件——`routeMemberEvent` 没有 error 分支，推了也是丢）。
  disciplines?: AiTradeDisciplineDigest | null;
  // tradability-and-open-funnel 三新键：可交易性启动断面 / 开仓漏斗 / 摘要健康度。
  // status 事件经 `patchAiTradeMemberStatus` **整体 spread** 合并进 `m.session`，故运行期数据本来
  // 就在——漏的只是类型声明（tsc 报错而非静默丢数据，但极易被「后端已下发」骗过去）。
  // 未装配 / 零样本 / 老后端 ⇒ null 或 undefined ⇒ 各自的卡片整段不渲染。
  tradability?: AiTradeTradabilitySnapshot | null;
  openFunnel?: AiTradeOpenFunnel | null;
  summaryHealth?: AiTradeSummaryHealth | null;
  // 会话级已实现读数（realized-edge-calibration 轴 A）：挂 `_status_payload` **顶层**而不是
  // `costSnapshot` 之下——后者只在 `k_min<=0`（门禁空转）时才发，装了纪律预设的会话一次都收不到。
  // 零往返 / 老后端 ⇒ null 或 undefined ⇒ 整段不渲染（G-9b 的实时侧读点）。
  realized?: AiTradeRealizedStats | null;
  // 成员级生命周期真值（lifecycle-truth B-5 / §5.2）：三条通路同时下发它——WS `status` 经
  // `patchAiTradeMemberStatus` **整体 spread** 合并进 `m.session`、REST 成员行经
  // `groupStateFromStatus` 逐键手抄、WS `lifecycle` 事件经窄 patch 合并。老后端 / 总闸关
  // ⇒ 整键缺席 ⇒ 成员卡的武装说明整段不渲染（自动回落到本轮之前）。
  lifecycle?: AiTradeMemberLifecycle | null;
}

export interface AiTradeStartReq {
  instId: string;
  bar: string;
  strategyIds: number[];
  accountId?: number | null;
  capitalUsd?: number | null;
  leverage?: number | null;
  takeProfitUsd?: number | null;
  stopLossUsd?: number | null;
  stopLossPct?: number | null;      // E4：单仓 OCO 止损%（留空→config 默认；越界服务端夹紧）
  takeProfitPct?: number | null;    // E4：单仓 OCO 止盈%
  contextRefreshRounds: number;
  priming?: boolean;
  primingBars?: number;
}

export interface AiTradeStartResult {
  sessionId: string;
  runId: number;
  instId: string;
  bar: string;
  strategyIds: number[];
  strategyNames: string[];
  budgetUsd: number;
  leverage: number;
  contextRefreshRounds: number;
  stopLossPct?: number | null;      // E4：executor 生效止损%（夹紧后回执）
  takeProfitPct?: number | null;    // E4：executor 生效止盈%
}

export interface AiTradeStatusResponse {
  session: AiTradeSessionRow | null;
  aiTradeEnabled: boolean;
}

export interface AiTradeStopResult {
  sessionId: string;
  stopReason: string | null;
  run?: unknown;
  flattened?: { ok?: boolean; closedSz?: number; residual?: { side: string; size: number } | null } | null;
}

// WS snapshot 事件负载（连接即重放当前会话全量态）。
export interface AiTradeSnapshot {
  trades: Trade[];
  equity: unknown[];
  position: PaperPositionEvent | null;
  status: AiTradeStatusEvent;
  decisions: AiTradeDecision[];
  contextSummary: string | null;
  agentTraces?: AiTradeRoundTrace[]; // 最近 ≤20 轮已装配轨迹（重连回放，已防御性冻结）
}

// ---------------- AI 自动交易「分组」（ai-trade-multi-symbol-and-timeframe §6.3，镜像 live-auto 分组）----------------
// 分组成员入参：标的 + 该标的可用仓位百分比（(0,100]，even 模式整组 Σ ≤ 100）。
export interface AiTradeGroupMemberInput {
  instId: string;
  allocationPct: number;
}

// 分组启动请求（camelCase，与后端 AiTradeGroupStartIn 同构；capitalUsd 为分组总预算，按 pct 切分）。
export interface AiTradeGroupStartReq {
  members: AiTradeGroupMemberInput[];
  bar: string;
  strategyIds: number[];
  accountId?: number | null;
  allocationMode: "even" | "max_ratio";
  capitalUsd?: number | null;
  leverage?: number | null;
  takeProfitUsd?: number | null;
  stopLossUsd?: number | null;
  stopLossPct?: number | null;      // E4：单仓 OCO 止损%（各成员同值，服务端夹紧）
  takeProfitPct?: number | null;    // E4：单仓 OCO 止盈%（各成员同值）
  contextRefreshRounds: number;
  priming?: boolean;
  primingBars?: number;
  positionStepPct?: number | null;  // 仓位操作档位步长（fraction；留空→服务端默认；0→连续）
  wakeIntervalMinutes?: number | null;  // Agent 墙钟定时唤醒周期（分钟；留空→服务端默认 2；0→关闭）
  // 研判深度档（§4.4）：留空 ⇒ 服务端用 `AI_TRADE_REASONING_DEPTH`（默认 deep）。**仅实盘下发**
  // ——回测档由服务端独立取 `AI_TRADE_REASONING_DEPTH_BACKTEST`（默认 standard，§3.6 成本护栏）。
  reasoningDepth?: AiTradeReasoningDepth | null;
  baseRules?: AiTradeBaseRules | null;  // 【基础策略】铁律（字段省略→服务端默认开启）
  profitGuard?: AiTradeProfitGuardRule | null;  // 利润保护层（字段省略→服务端默认开启）
  tradingHorizon?: AiTradeHorizonRule | null;   // 交易周期模式（字段省略→服务端默认开启）
  discipline?: AiTradeDisciplineIn | null;      // 交易纪律预设（字段省略→服务端默认 balanced）
  // 经验模式（字段省略→服务端按 `ai_trade_experience_default_on` 装配，默认开启）。
  experience?: import("./experienceTypes").AiTradeExperienceRule | null;
  // 方向对称性 / 逆势通路（字段省略→服务端按 settings 与纪律预设装配）。
  direction?: AiTradeDirectionRule | null;
}

// 分组成员行（start 回执 / status 视图共用）：完整会话行 + 分配占比。
export interface AiTradeGroupMemberRow extends AiTradeSessionRow {
  allocationPct: number;
}

// 分组只读视图（GET /ai-trade/status 与 start 回执同构）。
export interface AiTradeGroupRow {
  groupId: string;
  bar: string;
  strategyIds: number[];
  strategyNames: string[];
  totalBudgetUsd: number;       // 夹紧后最终生效的分组总预算（可能 < 用户填值）
  leverage: number;
  contextRefreshRounds: number;
  createdTs: number;
  members: AiTradeGroupMemberRow[];
  wakeIntervalMinutes?: number | null; // wake-interval：Agent 墙钟定时唤醒周期（分钟；null=关闭）
  baseRules?: AiTradeBaseRules | null; // 【基础策略】铁律回执镜像（null=未装配）
  profitGuard?: AiTradeProfitGuardRule | null; // 利润保护层回执镜像（null=未装配）
  // 交易周期模式回执镜像（null=未装配）：**服务端解析后的生效值** + 两个派生量
  // （expectedHoldBars / confirmWindowSeconds），使「弹窗预览 == 落地」可逐字段核对。
  tradingHorizon?: AiTradeHorizonRule | null;
  // 验收闭环三键的**分组合并口径**（后端 `merge_inputs` 后判定一次）：由 15s 轮询消费。
  acceptance?: AiTradeAcceptance | null;
  exits?: AiTradeExitMix | null;
  funding?: AiTradeFundingStats | null;
  // 交易纪律预设回执（轴 A）：`preset` / `adjustments` 使「弹窗选了什么、服务端替我改了什么」
  // 可逐字段核对；`rows` 是分组共享的 13 行自检（成员间只有 exchangeBackstop 会分叉，走成员 status）。
  discipline?: AiTradeDisciplineWire | null;
  // 分组生命周期（ai-trade-pause-resume §5.6）：`running` | `paused`。**声明为可选**——老后端
  // 不下发它，读点一律 `?? "running"` 兜底（`groupStateFromStatus` 此前把它写死，那正是本仓库
  // 「静默吞新键」的成文缺陷形状）。
  state?: AiTradeLifecycleState | null;
  pauseReason?: AiTradePauseReason | null;
  pausedTs?: number | null;
  resumedCount?: number | null;
  // 生命周期真值（lifecycle-truth B-6）：`state` 自本轮起是**派生真值**（由各成员会话态现算），
  // `declaredState` 是上一次动作赋的那个「意图」。两者不等即 bug 的信号，只用于诊断。
  // 老后端 / 总闸关 ⇒ 两键缺席 ⇒ 前端自动回落到 `state`（一键逐字回滚的第四处落点）。
  declaredState?: AiTradeLifecycleState | null;
  lifecycle?: AiTradeLifecycleWire | null;
  // 方向对称性回执镜像（directional-symmetry §7.1）：**服务端解析后的生效值**；
  // `null` = 未装配（总闸关 / 用户显式关闭 / 该预设的 minConsensus<=0 使门禁本就不触发）。
  direction?: (AiTradeDirectionRule & { minConsensus: number }) | null;
}

/** 分组生命周期态（后端 `group.state` 与会话 `status.state` 共用同一值域）。 */
export type AiTradeLifecycleState = "running" | "paused" | "stopped";

/**
 * 暂停原因（稳定枚举；前端按它选文案，绝不自己拼判定性说明）。
 *
 * `resume_rollback` 是 ai-trade-lifecycle-truth 新增的一格（B-2 ③ 复检未过后的整组回滚）。
 * **扩展稳定枚举要一次改齐所有落点**：后端 `pause.py` 的常量与 `PAUSE_REASON_TEXT`、本联合
 * 类型、`lib/aiTrade.ts` 的 `AI_TRADE_PAUSE_REASON_TEXT`。少改一处，回滚后的暂停原因在界面
 * 上就是**空的**。
 */
export type AiTradePauseReason =
  | "user"
  | "app_quit"
  | "process_restart"
  | "disabled_by_config"
  | "resume_rollback";

// ============ 生命周期真值（ai-trade-lifecycle-truth §5.1 / §5.2）============

/**
 * 「没武装」的稳定语义码（后端 `ai_auto/lifecycle.py` 的唯一真源）。
 *
 * 前端**只按它选颜色与图标**，判定性中文一律取同一份 wire 里的 `reasonText` ——
 * 本仓库「稳定语义码直出 UI」与「前后端各存一份口径」的事故各发生过一次。
 */
export type AiTradeNotArmedReason =
  | "armed"
  | "session_stopped"
  | "session_paused"
  | "feed_stale"
  | "feed_down"
  | "not_warmed";

/** 未武装成员的逐标的一行（`lifecycle.notArmed[]`）。 */
export interface AiTradeNotArmedRow {
  instId: string;
  state: AiTradeLifecycleState;
  reason: AiTradeNotArmedReason;
  reasonText: string;
}

/**
 * 分组级 `lifecycle`（§5.1）：`state` 的**证据**。
 *
 * L-2：凡是下发 `state === "running"`，同一份 wire 必须同时下发 `armed` 与（`armed === false`
 * 时）逐成员的 `reason` + `reasonText` —— 否则「运行中」重新变成一句无法核对的话。
 * `membersArmed` / `membersTotal` 分子分母同时下发，**前端绝不自己数**（[评审 R1-7]）。
 */
export interface AiTradeLifecycleWire {
  state: AiTradeLifecycleState;
  /** 意图态（`AiTradeGroup.state`）。与 `state` 不等即 bug 的信号，只用于诊断。 */
  declaredState: AiTradeLifecycleState;
  armed: boolean;
  membersArmed: number;
  membersTotal: number;
  /** 服务端算这份 wire 的墙钟秒（倒计时锚点，避免本机时钟偏移）。 */
  asOfTs: number;
  notArmed: AiTradeNotArmedRow[];
  /** 距下一次墙钟定时唤醒（秒）；`null` = 未装墙钟唤醒（**最常见配置**，前端回落 `nextBarInS`）。 */
  nextWakeInS: number | null;
  /** 距下一根 K 线收盘（秒）；`null` = 相位未知。 */
  nextBarInS: number | null;
  pauseReason: AiTradePauseReason | null;
  pausedTs: number;
  resumedCount: number;
}

/** 成员级 `lifecycle`（§5.2）：同时出现在 `/status` 的成员行、WS `status`、WS `lifecycle` 事件。 */
export interface AiTradeMemberLifecycle {
  state: AiTradeLifecycleState;
  armed: boolean;
  reason: AiTradeNotArmedReason;
  reasonText: string;
  warmedBars: number;
  feedAgeS: number;
  staleAfterS: number;
  /** 链路登记簿口径（`up` | `idle` | `degraded` | `down` | `unknown`）。 */
  linkState: string;
  nextWakeInS: number | null;
  nextBarInS: number | null;
  roundsCompleted: number;
  pauseReason: AiTradePauseReason | null;
  pausedTs: number;
  resumedTs: number;
  stopReason: string | null;
  /**
   * 行情断连自愈的进度（ai-trade-feed-self-healing §4.1）。
   *
   * 键的存在性有**三档，缺一不可**：整键缺席（`undefined`）= 后端没上线这个特性（运维总闸关）；
   * `null` = 已上线、行情正常；对象 = 正在自愈 / 已升级。前端对前两档表现一致（不渲染），但
   * wire 里两者可被反证——这正是 `member_lifecycle_wire` 那句「前端要能区分『没数据』与『后端没
   * 上线这个键』」要求的。
   */
  feedRecovery?: AiTradeFeedRecovery | null;
}

/** 恢复卡三态。`healthy` 不会出现在 wire 里（那一档下发的是 `null`），保留是为了值域完整。 */
export type AiTradeFeedRecoveryState = "healthy" | "recovering" | "stuck";

/** 自愈阶梯的阶段码（与后端 `feed_watchdog.STAGE_*` 镜像；后端 wire 只发枚举值）。 */
export type AiTradeFeedStage = "fresh" | "degraded" | "backfill" | "relink" | "escalated";

/**
 * 成员级 `feedRecovery`（§4.1）。
 *
 * 纪律（沿用 `aiTradeExit.ts` / `aiTradeHorizon.ts` 的成文教训）：**这里的每一个数都由后端算好**
 * ——重连次数、倒计时、行情年龄一个都不许前端自己算，中文也一句都不许前端自己拼
 * （`recoveryText` 由 `feed_watchdog.describe` 单点生成）。一张前后端各存一份的表必然漂移。
 */
export interface AiTradeFeedRecovery {
  state: AiTradeFeedRecoveryState;
  stage: AiTradeFeedStage;
  attempts: { backfill: number; relink: number };
  lastAttemptTs: number;
  /** 距下一次尝试的秒数；`null` = 无待办倒计时（冷却外 / 已升级）⇒ **整行不读秒**。 */
  nextAttemptInS: number | null;
  lastOutcome: "ok" | "no_data" | "failed" | null;
  lastOutcomeText: string | null;
  /** 最后一根确认根的来源；`rest` = 当前跑在 REST 生命维持上。`null` = 尚无。 */
  source: "ws" | "rest" | null;
  /** 判定性文案的**唯一**来源（后端单点生成）。 */
  recoveryText: string;
}

/**
 * `POST /ai-trade/{gid}/member/{inst}/relink` 的回执（feed-self-healing §4.2）。
 *
 * 后端**刻意返回裸 dict、不绑 `response_model`**（同类三个端点同款）：`response_model` 会静默吞掉
 * 新 wire 键，那是本仓库出过事的形态。故此处声明的是「至少有这些键」，多余键原样保留。
 */
export interface AiTradeRelinkResult {
  ok: boolean;
  instId: string;
  groupId?: string;
  relinked?: boolean;
  appended?: number;
  fresh?: boolean;
  lastTs?: number | null;
  message?: string;
}

/**
 * WS `lifecycle` 事件（§5.4）。
 *
 * 唯一一条**实时**的暂停 / 继续信号：上一轮后端推了、前端没有 case（D-10），本轮才第一次消费它。
 */
export interface AiTradeLifecycleEvent {
  action: "paused" | "resumed";
  reason: AiTradePauseReason | null;
  ts: number;
  lifecycle?: AiTradeMemberLifecycle | null;
}

/** WS `error` 事件（§5.4）：协议不变，本轮才第一次消费它（D-9）。 */
export interface AiTradeErrorEvent {
  code: string;
  message: string;
}

/** 离线差异判定（§3.6 的十一格；判定性文案由后端 `message` 下发，前端只渲染）。 */
export type AiTradeDriftVerdict =
  | "matched"
  | "flat_ok"
  | "partially_closed"
  | "closed_offline"
  | "size_increased"
  | "side_flipped"
  | "foreign_position"
  | "opened_offline"
  | "unverifiable_identity"
  | "unknown_order"
  | "unreadable";

/** 暂停时落盘的持仓身份断面（`posId` / `openCTime` 是「这还是不是同一笔」的唯一依据）。 */
export interface AiTradePausedPosition {
  side: "long" | "short";
  size: number;
  avgEntry?: number | null;
  unrealizedPnlUsd?: number | null;
  posId?: string | null;
  openCTime?: number | null;
  positionSeq?: number | null;
}

/** 冷占位（`GET /ai-trade/status` 的 `resumable`）：**仅**内存无会话时非空，与 `group` 互斥。 */
export interface AiTradeResumableGroup {
  groupId: string;
  bar: string;
  accountId: number;
  totalBudgetUsd: number;
  leverage: number;
  strategyNames: string[];
  createdTs: number;
  pausedTs: number;
  pauseReason: AiTradePauseReason | null;
  pauseReasonText: string;
  resumedCount: number;
  offlineSeconds: number;
  offlineText: string;
  hasPosition: boolean;
  members: AiTradeResumableMember[];
}

export interface AiTradeResumableMember {
  instId: string;
  runId?: number | null;
  sessionId?: string | null;
  allocationPct?: number | null;
  budgetUsd?: number | null;
  position?: AiTradePausedPosition | null;
  positionUnknown?: boolean;
  /** 关机前**最后一次**交易所侧兜底核实的结果——UI 必须把它标成过去时（[P2-9]）。 */
  backstopAtPause?: AiTradeBackstopAtPause | null;
}

/**
 * 暂停那一刻落盘的兜底核实结果（`backstopAtPause`）。
 *
 * = 既有 `AiTradeBackstopWire` + 后端在**落盘那一点**派生的三个展示键。`covered` 不是交易所回
 * 的原生字段，而是「交易所口径 + 覆盖率达阈」的合取——**只在后端派生一次**，前端绝不自己再判
 * 一遍（判定性口径两处各写一遍必然漂移）。
 */
export interface AiTradeBackstopAtPause extends Partial<AiTradeBackstopWire> {
  covered?: boolean;
  stopPx?: number | null;
  takePx?: number | null;
  checkedAtWall?: number | null;
}

/** `POST /ai-trade/{gid}/pause` 的 200 回执。 */
export interface AiTradePauseResult {
  groupId: string;
  state: AiTradeLifecycleState;
  pauseReason: AiTradePauseReason;
  pausedTs: number;
  holdPosition: boolean;
  resumable: boolean;
  members: AiTradePauseMemberResult[];
  notice: string;
}

export interface AiTradePauseMemberResult {
  instId: string;
  sessionId: string;
  runId: number;
  allocationPct?: number;
  position?: AiTradePausedPosition | null;
  positionUnknown?: boolean;
  restingCancelled?: { outcome: string; clOrdId: string } | null;
  backstop?: AiTradeBackstopAtPause | null;
  flattened?: unknown;
}

/** `GET /ai-trade/{gid}/resume-preview` 的 200 回执（§5.2）。 */
export interface AiTradeResumePreview {
  groupId: string;
  pausedTs: number | null;
  asOfTs: number;
  offlineSeconds: number;
  offlineText: string;
  ageExceeded: boolean;
  resumedCount: number;
  pauseReason: AiTradePauseReason | null;
  pauseReasonText: string;
  contract: {
    bar: string;
    instIds: string[];
    totalBudgetUsd?: number | null;
    leverage?: number | null;
    stopLossPct?: number | null;
    takeProfitPct?: number | null;
    disciplinePreset?: string | null;
    strategyNames: string[];
  };
  drift: { field: string; from: unknown; to: unknown; reason: string }[];
  members: AiTradeResumePreviewMember[];
  verdict: "ready" | "needs_confirm" | "blocked";
  blockedReason: string | null;
}

export interface AiTradeResumePreviewMember {
  instId: string;
  runId?: number | null;
  verdict: AiTradeDriftVerdict;
  message: string;
  expected?: AiTradePausedPosition | null;
  actual?: Record<string, unknown> | null;
  backstop?: AiTradeBackstopAtPause | null;
  pendingOrders?: unknown[];
  blockedReason?: string | null;
}

/** `POST /ai-trade/{gid}/end` 的 200 回执（`orphanPositions` **恒下发**，前端据它出持久提示）。 */
export interface AiTradeEndResult {
  groupId: string;
  endedFrom: string;
  stopReason: string;
  flattened: boolean;
  orphanPositions: {
    instId: string;
    position?: AiTradePausedPosition | null;
    positionUnknown?: boolean;
    backstop?: AiTradeBackstopAtPause | null;
  }[];
  members: { instId: string; runId?: number | null; sessionId?: string | null }[];
}

export type AiTradeGroupStartResult = AiTradeGroupRow;

export interface AiTradeGroupStatusResponse {
  group: AiTradeGroupRow | null;
  aiTradeEnabled: boolean;
  /** 冷占位；**与 `group` 互斥**（两者同时非空即 bug，后端 G-9 冻结这一条）。 */
  resumable?: AiTradeResumableGroup | null;
  /**
   * 后端水合盲区（lifecycle-truth B-8，修 D-12）。
   *
   * `true` = 后端**不知道**有没有可续跑的暂停分组（DB 读失败）。这与「没有」在真实资金上
   * 不是同一件事：命中这一格时对账器**什么都不清**，只把界面切到「与后端同步中…」。
   */
  lifecycleUnknown?: boolean;
  /** 服务端墙钟秒：倒计时的锚点（双时钟纪律，本机时钟偏移不该让「下一次决策」读出荒谬的数）。 */
  serverTs?: number;
}

export interface AiTradeGroupStopResult {
  groupId: string;
  stopReason: string | null;
  members: { instId: string; sessionId: string; runId: number; flattened?: unknown }[];
}

export interface AiTradeGroupMemberStopResult {
  groupId: string;
  instId: string;
  flattened?: unknown;
}

/** 人工补挂的 200 回执（backstop-repair §4.1）；`state` 恒为 `placed`——核实前不说「已补挂」。 */
export interface AiTradeBackstopRepairResult {
  instId: string;
  state: AiTradeRepairState;
  algoId?: string | null;
  algoClOrdId?: string | null;
  stopLossPx?: number | null;
  sz?: number | null;
  coveredBefore?: number | null;
  positionSz?: number | null;
}

// 运行期成员槽（瞬态；镜像 LiveAutoGroupMemberState + AI 专属轨迹 / 决策 / 摘要）。
export interface AiTradeGroupMemberState {
  instId: string;
  sessionId: string;
  allocationPct: number;
  session: AiTradeSessionRow | null;   // 会话只读视图（info + WS status/decision/summary 增量合并）
  trades: Trade[];
  position: PaperPositionEvent | null;
  // 保护性平仓冷却期（adaptive §4.2）：**与 position 平级的独立字段**，绝不挂在 position 之下——
  // 冷却期内按定义已无持仓（position 恒 null），挂上去 chip 永远拿不到数据。由 status 事件写入。
  // 声明为**可选**（与 baseRules/positionStepPct 等既有可选键同范式）：老快照 / 既有直接构造成员对象
  // 的用例无此键，读点一律 `?? null` 兜底。
  profitGuardCooldown?: ProfitGuardCooldown | null;
  decisions: AiTradeDecision[];
  traces: AiTradeRoundTrace[];
  summary: string | null;
}

// 分组运行期态（瞬态，不入 partialize，刷新由 GET /ai-trade/status 重发现）。
export interface AiTradeGroupState {
  groupId: string;
  bar: string;
  strategyIds: number[];
  strategyNames: string[];
  totalBudgetUsd: number;
  leverage: number;
  contextRefreshRounds: number;
  wakeIntervalMinutes?: number | null; // wake-interval：Agent 墙钟定时唤醒周期（分钟；null=关闭，面板显示）
  tradingHorizon?: AiTradeHorizonRule | null;   // 分组共享的周期契约（R-12：模式是会话级契约）
  // 验收闭环三键（分组合并口径）：由 `patchAiTradeGroupAcceptance` 窄 action 补丁写入——
  // **绝不**走 `setAiTradeGroup(groupStateFromStatus(...))`，那条映射写死 `trades: []`，
  // 每轮询一次就会把成交流清空一次（[P0-3] 要防的正是这个坑）。
  acceptance?: AiTradeAcceptance | null;
  exits?: AiTradeExitMix | null;
  funding?: AiTradeFundingStats | null;
  // 分组共享的纪律预设回执。由 `groupStateFromStatus` **逐键手抄**写入——那正是本仓库「静默吞新键」
  // 缺陷的那个函数，不补就永远拿不到（`aiTradeDiscipline.test.ts` 有一条用例专门守它）。
  discipline?: AiTradeDisciplineWire | null;
  // 方向对称性回执（directional-symmetry §7.1）：**服务端解析后的生效值**，分组共享一份。
  // 同上——由 `groupStateFromStatus` 逐键手抄写入，不补这一行，「我在弹窗里关掉的逆势通路到底
  // 有没有生效」在「刷新页面后重发现」这条路径上就恒无解（`aiTradeDiscipline.test.ts` 守它）。
  direction?: (AiTradeDirectionRule & { minConsensus: number }) | null;
  // 暂停 / 继续（pause-resume D-7）：值域从 `"running" | "stopped"` 扩为三态。这里此前被
  // `groupStateFromStatus` **写死**成 "running"，故暂停态在「刷新页面后重发现」这条路径上会恒显示
  // 运行中——最难被发现的那种坑。`aiTrade.test.ts` 有一条用例专门守它。
  state: AiTradeLifecycleState;
  pauseReason?: AiTradePauseReason | null;
  pausedTs?: number | null;
  resumedCount?: number | null;
  // 生命周期真值（lifecycle-truth F-2）：由 `patchAiTradeGroupLifecycle` 窄 action 与
  // `groupStateFromStatus` 逐键手抄两条路径写入。**`state` 自本轮起消费的是后端的派生真值**，
  // 而 `lifecycle` 是让人能反驳它的那份证据；两者缺一，「运行中」就重新变成一句无法核对的话。
  declaredState?: AiTradeLifecycleState | null;
  lifecycle?: AiTradeLifecycleWire | null;
  startedTs: number;
  members: Record<string, AiTradeGroupMemberState>;
}

/**
 * 生命周期对账的**窄** patch（lifecycle-truth F-2 / F-9）。
 *
 * 「窄」的准确边界（[评审 R1-3]）：它**要**写分组级那几键 + 逐成员的六个生命周期键，
 * **绝不**写 `members[].trades / position / decisions / traces / summary`（那些是 WS 流的产物，
 * 覆盖即闪烁 / 丢历史），**也不**增删成员槽。
 *
 * 为什么不能用 `setAiTradeGroup(groupStateFromStatus(...))` 做这件事：那条映射写死
 * `trades: []`，而它跑在一个 3~15 秒的轮询里 —— 成交流会周期性消失。这条与
 * `patchAiTradeGroupAcceptance` 的注释是同一条教训。
 *
 * 白名单与 §5.2 的 wire 形状**写在同一处**（风险 R-13）：后端加了成员级生命周期字段而这里
 * 没跟上时，那个字段会悄悄拿不到。
 */
export interface AiTradeGroupLifecyclePatch {
  group: Partial<
    Pick<
      AiTradeGroupState,
      | "state"
      | "declaredState"
      | "lifecycle"
      | "pauseReason"
      | "pausedTs"
      | "resumedCount"
      | "acceptance"
      | "exits"
      | "funding"
    >
  >;
  /** 逐成员的六个生命周期键（按 `instId` 合并；缺席于回执的成员**保留本地行不动，不删**）。 */
  members: {
    instId: string;
    session: Partial<
      Pick<
        AiTradeSessionRow,
        "state" | "pauseReason" | "pausedTs" | "stopReason" | "degraded" | "lifecycle"
      >
    >;
  }[];
}

// 持久化偏好（进 partialize）：记忆上次选的策略集合与刷新轮数与仓位操作档位。
export interface AiTradeConfig {
  strategyIds: number[];
  contextRefreshRounds: number;
  // 仓位操作档位步长（fraction）；[R-7] 声明为**可选**——与「老配置无此键 / aiTradeConfig 可为 null」一致，
  // 读点一律 `aiTradeConfig?.positionStepPct ?? AI_TRADE_DEFAULT_POSITION_STEP` 兜底。
  positionStepPct?: number;
  // Agent 墙钟定时唤醒周期（分钟）；同样声明为**可选**——读点一律 `?? AI_TRADE_DEFAULT_WAKE_INTERVAL_MIN` 兜底。
  wakeIntervalMinutes?: number;
  // 【基础策略】铁律偏好；[R-7] 同样声明为**可选**——老用户 localStorage 无此键，读点一律
  // `?? AI_TRADE_BASE_RULE_DEFAULTS` 兜底（**不**为此改 persist version，避免动 migration 链）。
  baseRules?: AiTradeMaBandRule;
  // 利润保护偏好 + 单仓 OCO 偏好（诊断根因 ③：留空 = 悄悄用 10%/5%）；同为**可选**，读点一律兜底
  // （**不**为此改 persist version，沿用 baseRules 先例，避免动 migration 链）。
  profitGuard?: AiTradeProfitGuardRule;
  stopLossPct?: number;
  takeProfitPct?: number;
  // 交易周期模式偏好（holding-horizon §3.4 第 3 处）；同为**可选**，读点一律兜底
  // （**不**为此改 persist version，沿用 baseRules 先例，避免动 migration 链）。
  tradingHorizon?: AiTradeHorizonRule;
  experience?: import("./experienceTypes").AiTradeExperienceRule;   // 经验模式偏好（可选，同款兜底）
  // 研判深度档偏好（可选，同款兜底：读点一律 `?? AI_TRADE_DEFAULT_REASONING_DEPTH`）。
  // **不**为此改 persist version，沿用 baseRules 先例，避免动 migration 链。
  reasoningDepth?: AiTradeReasoningDepth;
}

// 研判深度四档（ai-trade-deep-reasoning-chain §3.1.1）：与后端 `Literal` 逐字对应。
export type AiTradeReasoningDepth = "off" | "standard" | "deep" | "exhaustive";

// ---- 止损来源七态（tradability-and-open-funnel 轴 B · R-5）----
// 五态 → 七态。**三处漏一处即静默显示空串**：本类型、`lib/aiTrade.ts` 的 `STOP_SOURCE_LABEL`、
// 以及 `lib/aiTradeSizing.test.ts:128-132` 那条按数组遍历 source 的冻结用例。
export type AiTradeStopSource =
  | "atr"
  | "atr_floored"
  | "fixed"
  | "floor_above_cap"
  | "skipped_resting"
  | "skipped_position_live"
  | "skipped_reversal";

// ---- 可交易性预检（tradability-and-open-funnel 轴 A · §5.1）----
// **判据与文案全部由后端下发，前端一个字都不重写**：一张用户按下启动键之前看的表，如果与真正
// 生效的那组数分叉，比不给这张表更坏（`edgeSource` 逐键手抄吞新键的成文教训）。
export type AiTradeTradabilityLevel = "ok" | "marginal" | "infeasible" | "unknown";

export interface AiTradeTradabilityAlternative {
  bar: string;
  vetoRateMarket: number | null;
  // 每项用它**自己的** `bar_seconds` 重算（资金费期数随周期变，`1D × 8 根 = 24 期`）。
  costBpsMarket: number | null;
  level: AiTradeTradabilityLevel;
}

export interface AiTradeTradabilityResponse {
  instId: string;
  bar: string;
  preset: string;
  level: AiTradeTradabilityLevel;
  samples: number;
  source: "live" | "cache" | null;
  asOfTs: number | null;
  atrBps: { p10: number | null; p50: number | null; p90: number | null } | null;
  costBps: { market: number | null; limit: number | null } | null;
  costConfidence: { market: string; limit: string } | null;
  degradedMultApplied: number | null;
  requiredBps: { market: number | null; limit: number | null } | null;
  vetoRate: { market: number | null; limit: number | null } | null;
  stopBps: { p50: number | null } | null;
  stopInsideCostRate: { market: number | null; limit: number | null } | null;
  // 风险单位体检（realized-edge-calibration 轴 E-0）：只提示、不阻断、算不出即整键缺席。
  bracket?: AiTradeBracketVerdict | null;
  alternatives: AiTradeTradabilityAlternative[];
  headline: string;
  advice: string | null;
  disclaimer: string;
}

// ---- 风险单位体检（realized-edge-calibration 轴 E-0 · §5.2）----
// `level` 四态：`atr_scaled`（已按 ATR 定标）/ `cost_floored`（成本下界抬起，同样是对齐后的结果）/
// `aligned`（固定百分比但量级相当）/ `oversized`（止损远大于该周期一根 K 线的波动 ⇒ 报警）。
// 文案一律由后端下发，前端一个字都不重写；`suggestedStopPct` **绝不自动填进输入框**。
export interface AiTradeBracketVerdict {
  level: "atr_scaled" | "cost_floored" | "aligned" | "oversized";
  stopSource: string;
  stopBps: number | null;
  atrP50Bps: number | null;
  stopAtrMult: number | null;
  suggestedStopPct: number | null;
  text: string;
}

// 会话 status 上的**瘦身**投影（启动断面，启动后不变）。
export interface AiTradeTradabilitySnapshot {
  level: AiTradeTradabilityLevel;
  bar?: string | null;
  vetoRateMarket?: number | null;
  headline?: string | null;
  advice?: string | null;
  samples?: number | null;
  source?: string | null;
  asOfTs?: number | null;
}

// ---- 开仓漏斗（轴 C · §5.3）----
export interface AiTradeOpenFunnel {
  intents: number;
  blocked: Record<string, number>;
  topBlocker: { key: string; label: string; count: number } | null;
  llmDegraded: number;   // **仅** LLM 故障；`low_confidence` 的唯一落点是 blocked.confidence
  sizingZeroed: number;  // 不是一道闸，是求解结果 ⇒ 画在「委托」前的最后一格
  submitted: number;
  filled: number;
  conversion: number | null;   // `intents == 0` ⇒ null（**绝不**把 0/0 画成 0%）
  layers: string[];            // 决策链顺序（后端下发，前端只做布局）
  scope: string;               // 口径词（全程累计 vs 滚动窗口）
  // ---- 方向轴（ai-trade-directional-symmetry 轴 D）----
  // 成员级 `status` 走**整体 spread** 合并进 `m.session`（`store.ts` 的 patchAiTradeMemberStatus），
  // 故这两个键**不需要**改 store.ts —— 漏的只有类型声明。总闸关 / 老会话 ⇒ 两键缺席 ⇒ 卡片不渲染。
  bySide?: Record<AiTradeIntentSide, AiTradeSideFunnel> | null;
  directionBalance?: AiTradeDirectionBalance | null;
}

// 方向三格：`unknown` **单列**、绝不并进任何一侧（置信度门槛在方向可见之前就拦掉的那些轮）。
export type AiTradeIntentSide = "long" | "short" | "unknown";

export interface AiTradeSideFunnel {
  intents: number;
  blocked: Record<string, number>;
  sizingZeroed: number;
  submitted: number;
  filled: number;
}

export type AiTradeDirectionVerdict = "balanced" | "long_skewed" | "short_skewed" | "insufficient";

export interface AiTradeDirectionBalance {
  longIntents: number;
  shortIntents: number;
  longFilled: number;
  shortFilled: number;
  shortIntentShare: number | null;   // 分母（long+short）为 0 ⇒ null，**绝不写 0**
  shortFillShare: number | null;
  // 方向不可知的开仓意愿数。**必须有可见落点**：不披露它，`verdict` 就是一句数据不支持的断言
  // ——「模型没提过做空」与「提过但在方向可见之前被置信度门槛拦掉」在这份数据里不可区分。
  unknownIntents: number;
  unknownFilled: number;
  contrarianPassed: number;          // 走逆势通路放行的轮数（**不是**降级）
  minSamples: number;                // 判「样本不足」的下界（后端下发，前端不硬编码）
  verdict: AiTradeDirectionVerdict;
}

// 方向对称性 / 逆势通路的启动入参与回执镜像（§7.1）。
export interface AiTradeDirectionRule {
  contrarianEnabled: boolean;
  contrarianMinConfidence?: number | null;
  contrarianMaxOpposingVotes?: number | null;
  contrarianMinEvidenceChars?: number | null;
  contrarianMaxPerSession?: number | null;
  contrarianSizeMult?: number | null;
}

// ---- 滚动摘要健康度（轴 D · §5.3）----
export interface AiTradeSummaryHealth {
  roundsSinceSummary: number;
  refreshRounds: number;
  failures: number;        // **连续**失败（成功即清零）
  totalFailures: number;   // 全程累计（只增不减，与上一行不是一个口径）
  lastError: string | null;
  backoffRounds: number;
  stalled: boolean;
}

// ---- 历史预设对照卡（轴 E · §5.2）----
export interface AiTradePresetComparisonGroup {
  preset: string;
  samples: number;
  conclusive: boolean;    // false ⇒ UI 必须显式写「样本不足，不下结论」
  netPnlUsd: number;
  roundTrips: number;
  winRate: number | null;
  edgeVetoRate: number | null;
  openConversion: number | null;
}

export interface AiTradePresetComparisonResponse {
  groups: AiTradePresetComparisonGroup[];
  minSamples: number;
}

// 别名（spec §3 类型清单）：会话运行期视图 / 轻量状态负载，供 store 与流式路由语义化引用。
export type AiTradeSessionState = AiTradeSessionRow;
export type AiTradeStatus = AiTradeStatusEvent;

export type AiTradeStopReason =
  | "user_stop"
  | "session_stop_loss"
  | "session_take_profit"
  | "shutdown"
  | "stale_feed"
  | "start_failed";
