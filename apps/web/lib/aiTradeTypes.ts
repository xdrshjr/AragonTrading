// AI 自动交易 wire 类型（camelCase，与后端 schemas.py 的 AiTrade* 同构）。
// 运行期句柄（sessionId 等）为瞬态（store 不入 partialize）；aiTradeConfig 为持久化偏好。
import type { FeeRatesWire, ReconcileWire } from "./aiTradeCost";
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
  | "horizon";

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
  profiles: AiTradeHorizonProfile[];
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
  }; // tool_call：门禁后最终决策工具入参
  executed?: AiTradeDecision["executed"]; // result（复用既有 executed 形状）
  degradeReason?: string | null; // result
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
  stopSource?: "atr" | "fixed" | "skipped_resting" | "skipped_position_live" | "skipped_reversal" | null;
  effectiveExposure?: number | null;
  effectiveExposureCap?: number | null;
  corrScale?: number | null;
  exposureCapUnreachable?: boolean | null; // 其余成员自身已越界，缩到 0 也达不到上限（如实标注，不假装）
  // horizon：持仓周期铁律判定（ai-trade-holding-horizon-modes §4.4）。整份快照随事件下发（本层读数
  // 有 20 余个，逐键搬运必然在下一次加字段时漏掉）。`status` 位与 base_rule/edge_gate 互不同时出现。
  horizon?: AiTradeHorizonSnapshot | null;
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
  thinking: string; // 累积深度思考（未开启扩展思考时通常为空）
  analysis: string; // 累积自然语言研判分析（流式主可见内容）
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
  stopSource?: "atr" | "fixed" | "skipped_resting" | "skipped_position_live" | "skipped_reversal" | null;
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
  roundsCompleted: number;
  roundsSinceSummary: number;
  positionSide: string;
  sessionPnlUsd: number;
  startedTs: number;
  lastDecision?: AiTradeDecision | null;
  contextSummary?: string | null;
  degraded?: boolean | null;
  wakeIntervalMinutes?: number | null; // wake-interval：Agent 墙钟定时唤醒周期（分钟；null=关闭）
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
  // hold-inertia-observability O2：第 1 轮后端已发 wakeSkips（本轮补齐类型）；holdStreak 为本轮新增。
  wakeSkips?: number;
  holdStreak?: number;
  // 唤醒调度闭环四计数（口径见 AiTradeSessionRow 上的注释；全量 spread 合并进 member.session）。
  wakeDrops?: number;
  wakeCatchups?: number;
  positionWatchSkips?: number;
  signalFlipWakes?: number;
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
  baseRules?: AiTradeBaseRules | null;  // 【基础策略】铁律（字段省略→服务端默认开启）
  profitGuard?: AiTradeProfitGuardRule | null;  // 利润保护层（字段省略→服务端默认开启）
  tradingHorizon?: AiTradeHorizonRule | null;   // 交易周期模式（字段省略→服务端默认开启）
  discipline?: AiTradeDisciplineIn | null;      // 交易纪律预设（字段省略→服务端默认 balanced）
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
}

export type AiTradeGroupStartResult = AiTradeGroupRow;

export interface AiTradeGroupStatusResponse {
  group: AiTradeGroupRow | null;
  aiTradeEnabled: boolean;
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
  state: "running" | "stopped";
  startedTs: number;
  members: Record<string, AiTradeGroupMemberState>;
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
