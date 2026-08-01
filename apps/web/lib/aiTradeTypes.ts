// AI 自动交易 wire 类型（camelCase，与后端 schemas.py 的 AiTrade* 同构）。
// 运行期句柄（sessionId 等）为瞬态（store 不入 partialize）；aiTradeConfig 为持久化偏好。
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
  | "sizing";

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
  status?: AiTradeBaseRuleStatus;
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
  // §5.4 会话内观测通道：成本统计只读快照。前端本轮**不消费**（阶段一的读者是运维，读 WS 原始事件），
  // 声明它只为让类型如实描述 wire——未识别键在 reducer 的 default 分支被忽略。
  costSnapshot?: Record<string, unknown> | null;
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
