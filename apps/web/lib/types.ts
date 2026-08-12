export type Bar = "1m" | "5m" | "15m" | "30m" | "1H" | "4H" | "1D";
// 回测区间预设（strategy-backtest-config-modal §4.2）：预设换算在打开弹窗时按 now 派生，custom 走用户起止。
export type RangePreset = "7d" | "30d" | "90d" | "180d" | "1y" | "custom";
export type Mode = "backtest" | "compare" | "paper";
export type StrategyListTab = "regular" | "combo" | "factor";
// 原子 / 组合 / 因子组合得分（后端 merge_strategy 透传 kind；评审 C-P1-5）。
export type StrategyKind = "atomic" | "combo" | "factor";
export type AccountMode = "sim" | "live";
export type ChartView = "run-range" | "compare-range" | "live-follow";
export type ChartPane = "price" | "equity";
export type BottomTab = "trades" | "positions";
export type DataSource = "live" | "cache";

export interface Candle {
  ts: number;
  o: number;
  h: number;
  l: number;
  c: number;
  vol: number;
  confirm?: boolean;
}

export interface MarketTrade {
  ts: number;
  price: number;
  qty: number;
  side: "buy" | "sell";
  tradeId?: string | null;
}

export interface OrderBookLevel {
  price: number;
  qty: number;
  orders?: number | null;
}

export interface OrderBookSnapshot {
  ts: number;
  bids: OrderBookLevel[];
  asks: OrderBookLevel[];
}

export interface MarketTicker {
  ts: number;
  last: number;
  open24h: number | null;
  vol24h: number | null;
  volCcy24h: number | null;
}

export type MarketWsEvent =
  | { type: "candle"; data: Candle }
  | { type: "trade"; data: MarketTrade }
  | { type: "ticker"; data: MarketTicker }
  | { type: "book"; data: OrderBookSnapshot }
  | { type: "error"; data: { code: string; message: string } };

export interface LiveMarketState {
  instId: string;
  lastPrice: number | null;
  ticker: MarketTicker | null;
  book: OrderBookSnapshot | null;
  trades: MarketTrade[];
  connected: boolean;
  error: string | null;
  updatedTs: number | null;
}

export interface Instrument {
  instId: string;
  instType: "SPOT" | "SWAP" | "FUTURES";
  base: string;
  quote: string;
  last?: number | null;
  open24h?: number | null;
  vol24h?: number | null;
  volCcy24h?: number | null;
  changePct?: number | null;
}

export type ParamSchema = Record<
  string,
  {
    type: "number" | "int" | "bool" | "enum";
    default: unknown;
    min?: number;
    max?: number;
    step?: number;
    options?: string[];
    label: string;
  }
>;

export interface Strategy {
  id: number;
  templateKey: string;
  name: string;
  description: string;
  // 原子策略 "atomic" | 组合策略 "combo"（后端 merge_strategy 输出；旧前端忽略不影响）。
  kind?: StrategyKind;
  params: Record<string, unknown>;
  paramsSchema: ParamSchema;
  enabled: boolean;
  builtin: boolean;
  unavailable?: boolean;
}

// ---- 策略组合（regime rotation）----
export interface ComboMemberSpec {
  templateKey: string;
  params: Record<string, unknown>;
  name?: string;
}

export interface ComboPolicy {
  rebalanceBars: number;
  lookbackBars: number;
  selectionMetric: "return" | "sharpe" | "calmar";
  minDwellBars: number;
  switchMarginPct: number;
  regimeGating: boolean;
  turnoverBps: number;
}

export interface ComboTimelineSeg {
  startTs: number;
  endTs: number;
  memberKey: string;
  memberName: string;
  regime: string;
  bars: number;
  contribution: number;
}

export interface ComboBreakdownRow {
  regime: string;
  memberKey: string;
  memberName: string;
  bars: number;
  contribution: number;
}

export interface ComboPreviewResult {
  runId: number;
  metrics: Metrics;
  combo: {
    annualizedReturn: number;
    timeline: ComboTimelineSeg[];
    breakdown: ComboBreakdownRow[];
    notes?: string[];
  };
}

// 单策略回测响应（POST /strategies/{id}/backtest）。组合行后端追加 `combo`（_run_saved_combo），
// 原子行 `combo` 为 undefined。详情弹窗绩效区据此渲染年化 / 时间线（R1）。
// （v2·P1-1）全区间覆盖透明度：旧字段不变，新增三项可选，原子回测路径回传（combo 路径为 undefined）。
export interface BacktestResult {
  runId: number;
  metrics: Metrics;
  // 专业扩展绩效指标（strategy-backtest-config-modal §2.5）：原子/因子/组合回测路径加法式回传；
  // 旧数据或形状不齐时缺省（前端渲染「—」）。契约与 engine/metrics_ext.compute_extended_metrics 同构。
  extendedMetrics?: ExtendedMetrics;
  effectiveCandleCount?: number; // 实际参与回测的已确认 K 线根数
  requestedBars?: number; // (endTs-startTs)/barSec + 1，请求区间应有根数
  truncated?: boolean; // 未覆盖到 startTs（撞 max_bars 上限或 OKX 历史不足）
  combo?: ComboPreviewResult["combo"];
}

// 回测配置（弹窗态 + store 持久化偏好，strategy-backtest-config-modal §4.2）。
export interface BacktestConfig {
  bar: Bar;
  rangePreset: RangePreset;
  startTs: number; // UTC 秒；preset!=="custom" 时打开弹窗按 now 重派生
  endTs: number;
  initialCapital: number; // > 0
  feesBps: number; // taker_fee_bps, >= 0
  slippageBps: number; // slippage_bps, >= 0
  leverage: number; // >= 1
  // 止盈止损一次性覆盖（backtest-stop-loss-take-profit §2.2）：存**百分数**（5 表示 5%），留空=null=不覆盖
  // 策略自带风控。%→小数的转换仅在 riskOverrideBody 单点发生（线上契约 / RiskParams 均为小数）。
  stopLossPct?: number | null;
  takeProfitPct?: number | null;
}

// 专业扩展绩效指标（与后端 engine/metrics_ext 派生同构，camelCase）。payoffRatio 为赔率
//（平均盈利/平均亏损），区别于 Metrics.profitFactor（盈利因子＝毛盈利/毛亏损）。
export interface ExtendedMetrics {
  annualizedReturn: number;
  annualizedVolatility: number;
  sortino: number;
  calmar: number;
  maxDrawdownBars: number;
  maxDrawdownSecs: number;
  timeInMarketPct: number;
  avgWin: number;
  avgLoss: number;
  payoffRatio: number;
  largestWin: number;
  largestLoss: number;
  maxConsecutiveWins: number;
  maxConsecutiveLosses: number;
  avgHoldBars: number;
  tradesPerWeek: number;
}

// ---- 高级回测：滑动窗口 / Walk-forward（spec advanced-backtest-walk-forward §6.2）----
export type BacktestKind = "simple" | "walkforward";

export interface WalkForwardWindow {
  startTs: number;
  endTs: number;
  metrics: Metrics; // 复用现有 Metrics（totalReturn/winRate/maxDrawdown/sharpe/...）
  tradeCount: number; // == metrics.tradeCount，冗余便于表格直读
}

export interface WalkForwardSummary {
  windowCount: number;
  profitableWindows: number;
  profitableRate: number;
  medianReturn: number;
  avgReturn: number;
  worstReturn: number;
  bestReturn: number;
  avgMaxDrawdown: number; // ≤ 0
  stabilityScore: number; // [0,1]
  uncoveredBars: number; // 末尾被丢弃的尾段根数（v2·P1-2）
  notes: string[]; // 用户可读提示（窗口偏短 / 数据不足一个窗口 / 截断 等，v2·P1-2）
}

export interface WalkForwardResult {
  runId: number;
  summary: WalkForwardSummary;
  windows: WalkForwardWindow[];
}

// 自动交易触发解释（auto-trade-explainability §5.2）：与后端 TriggerExplain wire/DB JSON 同构。
export type TriggerExplainKind =
  | "factor_score"
  | "atomic"
  | "risk_exit"
  | "reverse"
  | "session_stop"
  | "external_close"
  // 利润保护层（移动止盈 / 保本 / 硬止盈）触发的强制平仓（ai-trade-profit-protection §2.7）。
  // 与 risk_exit（时间止盈）刻意分列：一个是「持仓根数到了」，一个是「已到手的浮盈正在回吐」。
  | "profit_guard";

export interface TriggerMetric {
  label: string;
  value: string;
  tone?: "long" | "short" | "neutral" | "warn";
  hint?: string;
}

export interface FactorContribution {
  factorKey: string;
  name: string;
  direction: "long" | "short";
  value: number; // 方向化原始值 dir·raw
  zscore: number; // 因果滚动 z（与训练 / 打分同窗口、同 clip）
  weight: number;
  contribution: number; // weight * zscore（pre-squash 贡献）
  share: number; // |contribution| / Σ|contribution| ∈ [0,1]
  isTopDriver: boolean; // 主要驱动因子（前 3 且 share≥0.15）
}

export interface TriggerExplain {
  schema: number;
  kind: TriggerExplainKind;
  side: "long" | "short" | "close";
  headline: string;
  summary?: string;
  metrics: TriggerMetric[];
  composite?: {
    score: number;
    preSquash: number;
    squash: boolean;
    enterLong?: number;
    enterShort?: number;
    exitBand?: number;
  };
  factors?: FactorContribution[];
  risk?: {
    stopLossPrice?: number | null;
    takeProfitPrice?: number | null;
    stopLossPct?: number | null;
    takeProfitPct?: number | null;
    maxHoldBars?: number | null;
  };
  meta?: {
    strategyName?: string;
    strategyKind?: string;
    instId?: string;
    bar?: string;
    barTs?: number;
    clOrdId?: string;
    ordId?: string;
    // AI 自动交易的唤醒来源归因（ai-trade-wake-aware-context §2.9 写入，本轮读出）。
    // 只打在 AI 决策**直接**产生的成交上（atomic / reverse）；保护层平仓 / 会话止损 / 外部干预
    // 由确定性层触发、与唤醒无关，按设计缺席——那不是缺陷，打上去反而会让胜率归因整体失真。
    wakeKind?: string;
    /**
     * 本笔往返的 R 倍数（realized-edge-calibration 轴 A）。**实时通道只有这里读得到**：
     * executor 成交 dict 的顶层键集是冻结的（`test_ai_trade_fee_ledger._BASE_OPEN_KEYS`），
     * 新键只能搭 explain 信封；历史通道另由 `trade_out` 提到 `Trade.rMultiple` 顶层。
     * 两处都走 `readRMultiple()` 这一个读点，绝不在渲染点各写一份兜底链。
     */
    rMultiple?: number | null;
  };
}

export interface Trade {
  id?: number;
  runId?: number;
  /**
   * **K 线开盘秒**（AI 自动交易自 ai-trade-discipline-preset-and-integrity 轴 D 起对齐；
   * 其余链路仍是墙钟秒）。图表标记与悬停索引读它——它必须落在某一根真实 K 线上。
   */
  ts: number;
  /**
   * 真实**墙钟**秒（轴 D 新增）。同一根 K 线内的多笔成交会共享 `ts`，只有它单调不减，
   * 故成交表排序 / 悬停展示 / 导出一律读 `wallTs ?? ts`。老数据 / 非 AI 链路缺席 ⇒ 回退 `ts`。
   */
  wallTs?: number | null;
  side: "long" | "short" | "close";
  price: number;
  qty: number;
  fee: number;
  pnl: number;
  // 平仓收益率（已实现盈亏 / 开仓占用保证金）。仅实盘自动交易平仓携带；开仓 / 回测 / 模拟成交无此字段。
  pnlPct?: number | null;
  // 本次自动订单按成员最终预算折算的仓位比例（fraction）。
  positionPct?: number | null;
  /**
   * 本持仓周期的**峰值浮盈**（USD，随平仓成交落库；ai-trade-profit-protection-adaptive §2.4）。
   * 保护层未装配 / 快照一路不可用 / 老库缺列 ⇒ `null`——**绝不是 0**（0 会被聚合当成
   * 「峰值就是 0」，把捕获率算成 0 或 ∞）。开仓成交无此字段。
   */
  peakPnlUsd?: number | null;
  /**
   * 本笔往返的 R 倍数 = 已实现净额 / 计划风险单位（|开仓价 − 止损价| × 张数 × 合约乘数）。
   * 与 `wallTs` / `peakPnlUsd` 同款范式：搭 `explain` 信封落库、`trade_out` 提回顶层。
   * 无止损 / 合约乘数未知 / 老数据 ⇒ `null`——**绝不是 0**（一个被 0 兜底的 R 比没有 R 更糟）。
   */
  rMultiple?: number | null;
  stopLossPct?: number | null;
  takeProfitPct?: number | null;
  maxHoldBars?: number | null;
  stopLossPrice?: number | null;
  takeProfitPrice?: number | null;
  positionAfter: number;
  reason: string;
  seeded?: boolean;
  // 自动交易可解释性（auto-trade-explainability）：可空，历史 / 非自动交易成交无该字段。
  explain?: TriggerExplain | null;
  clOrdId?: string;
  ordId?: string;
}

export type TradeDirection = "long" | "short";

export interface TradeTrailSegment {
  id: string;
  direction: TradeDirection;
  entry: Trade;
  exit: Trade;
  pnl: number;
  isWin: boolean;
  canRenderLine: boolean;
  lineColor: string;
  lineStyle?: number;
}

export interface TradeMarkerEvent {
  id: string;
  trade: Trade;
  kind: "entry-long" | "entry-short" | "close" | "orphan-close";
  direction?: TradeDirection;
  pairedSegmentId?: string;
}

export interface EquityPoint {
  ts: number;
  equity: number;
  drawdown: number;
  transient?: boolean;
}

/**
 * 利润保护层的持仓断面快照（ai-trade-profit-protection §4.2；搭 `position` 事件顺风车下发）。
 *
 * **必须定义在本文件**：`aiTradeTypes.ts` 已 import 本文件，反向 import 会成环。
 *
 * 判定价 `livePx` 恒为后端 `_current_px()`（未确认根实时价），**不是** `markPrice`——后者由调用方
 * 传入的 ref_px 决定、可能是上一根确认收盘价，二者在 15m 周期上最坏差一整根。
 */
export interface ProfitGuardSnapshot {
  ruleId: string;
  /** flat=无持仓（仅承载 lastExit）｜idle=未武装｜armed=移动止盈已武装｜breakeven_armed=仅保本已武装｜unavailable=本轮零动作 */
  status: "flat" | "idle" | "armed" | "breakeven_armed" | "unavailable";
  /** status="unavailable" 时的原因码（行情陈旧 / 无实时价 / 持仓读数异常）。 */
  unavailableReason?: "stale_feed" | "no_price" | "bad_position" | null;
  side?: "long" | "short" | null;
  entryPx?: number | null;
  livePx?: number | null;
  peakPx?: number | null;
  holdSeconds?: number | null;
  barsInPosition?: number | null;
  /** 保证金收益率口径（= 方向化价格变动% × 杠杆），不是标的涨跌幅。 */
  returnPct?: number | null;
  priceMovePct?: number | null;
  peakReturnPct?: number | null;
  /** 自峰值已回吐比例（无量纲，两种口径下数值相同）。 */
  givebackRatio?: number | null;
  /** 本地派生浮盈（不含手续费 / 资金费），与 position.unrealizedPnl（交易所 upl）口径不同，不得互相冒充。 */
  unrealizedPnlUsd?: number | null;
  peakUnrealizedPnlUsd?: number | null;
  trailStopPx?: number | null;
  breakevenPx?: number | null;
  hardTakeProfitPx?: number | null;
  /** 有效退出线：价格向下（做空为向上）触及即由系统市价平仓；未武装 / 不可用时为 null。 */
  effectiveExitPx?: number | null;
  exchangeTakeProfitPx?: number | null;
  exchangeStopLossPx?: number | null;
  /** trade=取自开仓成交回执（首选真源）｜derived=只读转发现算（须在 UI 标「估算」）。 */
  exchangeOcoSource?: "trade" | "derived" | null;
  armReturnPct?: number | null;
  givebackPct?: number | null;
  minHoldSeconds?: number | null;
  pxAgeSeconds?: number | null;
  /** 本轮**有效**回吐阈（利润阶梯选档后）；UI 必须显示这个数而不是基准 givebackPct。 */
  givebackPctEffective?: number | null;
  /**
   * 这条移动止盈线被谁定住：回吐比例 / ATR 下界 / ATR 上界 / 夹至保本线 / 本轮作废。
   * 由后端下发，前端**不重算**（三处各算一份必然漂移，[R-5] 教训）。
   */
  trailBasis?:
    | "giveback"
    | "atr_floor"
    | "atr_cap"
    | "clamped_breakeven"
    | "suppressed_at_entry"
    | null;
  /** 移动止盈线距峰值的价格距离；`suppressed_at_entry` 时为 null（线作废即距离作废）。 */
  trailDistancePx?: number | null;
  /** 入场价夹紧是否绑定（夹至保本线或整条线作废两种情形均为 true）。 */
  trailClampedAtEntry?: boolean | null;
  /** 本轮 ATR（价格单位）；样本不足 / 非有限 ⇒ null ⇒ 上下界整体不生效。 */
  atrPx?: number | null;
  /**
   * 已棘轮的承诺退出线（ai-trade-profit-protection-commitment C2）：模型自己许下、经单调棘轮兑现的
   * 第三条下方保护线；无承诺 / unavailable / flat ⇒ null。
   */
  committedExitPx?: number | null;
  /** 承诺线被哪条约束定住（`adopted` / `atr_floor` / `clamped_breakeven`）；后端下发，前端不重算。 */
  committedExitBasis?: "adopted" | "atr_floor" | "clamped_breakeven" | null;
  /**
   * 有效退出线**当前由哪条线定住**（[P1-3] 后端单点归因真源，前端只读不推）：移动止盈 / 承诺线 / 保本。
   */
  effectiveExitSource?:
    | "profit_guard_trailing"
    | "profit_guard_committed_exit"
    | "profit_guard_breakeven"
    | null;
  /** 上一次保护性平仓回执（一次性，仅紧接的下一轮出现）。 */
  lastExit?: {
    ts?: number;
    reasonCode?: string;
    label?: string;
    exitPx?: number | null;
    pnlUsd?: number | null;
    peakPnlUsd?: number | null;
  } | null;
}

/**
 * 保护性平仓后的冷却期视图（ai-trade-profit-protection-adaptive §4.2）。
 *
 * **不挂 `position`**：冷却期内按定义已无持仓（`position` 恒 null），挂上去等于永远送不出去。
 * 载体是 `status` 事件 → `member.profitGuardCooldown`（与 position 平级的独立字段）。
 * `remainingSeconds` 是下发瞬间的快照值，仅用于显示；是否仍在冷却一律以后端每轮重算为准
 * （前后端两个时钟各自过期必然漂移）。
 */
export interface ProfitGuardCooldown {
  activeUntilTs: number;
  remainingSeconds: number;
  scope: "same_side" | "both";
  blockedSide: "long" | "short";
  reasonCode: string;
  label: string;
}

export interface PaperPositionEvent {
  side: "long" | "short" | "flat";
  size: number;
  avgEntry: number;
  markPrice: number;
  unrealizedPnl: number;
  positionPct?: number | null;
  /** 利润保护快照（AI 自动交易且规则已装配时才有；旧前端忽略未知键，向前兼容）。 */
  profitGuard?: ProfitGuardSnapshot | null;
  /**
   * 本轮成本证据（ai-trade-cost-truth-and-net-pnl 轴 D；成本意识总闸关 ⇒ 整键缺席）。
   *
   * `unrealizedPnl` 在净额口径下已是「此刻平掉能拿到手的钱」，`breakEvenPx` 则回答「还要走多远
   * 才回本」——两者是同一件事的两个面，缺一个用户就只能靠心算，而平衡价与杠杆无关这件事恰恰是
   * 最容易算错的地方。
   */
  costAwareness?: {
    roundTripBps: number;
    roundTripPct: number;
    breakEvenPx: number | null;
    breakEvenGapPct: number | null;
    sessionFeeUsd: number | null;
    netBasis: boolean;
    source?: string;
    level?: string;
  } | null;
}

export interface PaperStatus {
  state: string;
  warmedBars: number;
  seededTrades: number;
  initialCapital: number;
}

export interface PaperSnapshot {
  trades: Trade[];
  equity: EquityPoint[];
  position: PaperPositionEvent | null;
  status?: PaperStatus;
}

export interface PaperStrategySession {
  strategyId: number;
  strategyName: string;
  strategyKind?: StrategyKind;
  sessionId: string;
  runId: number;
  color: string;
  state: "starting" | "running" | "stopped" | "error";
}

export interface StrategyTrade extends Trade {
  strategyId: number;
  strategyName: string;
  color: string;
}

export interface StrategyEquityPoint extends EquityPoint {
  strategyId: number;
}

export interface PaperBatchSessionResult {
  strategyId: number;
  name: string;
  strategyKind?: StrategyKind;
  sessionId: string;
  runId: number;
  state: PaperStrategySession["state"];
}

export interface PaperBatchStartResult {
  groupId: string;
  instId: string;
  bar: string;
  sessions: PaperBatchSessionResult[];
}

export interface PaperBatchStopResult {
  groupId: string;
  runs: { strategyId: number; sessionId: string; run: Run }[];
}

// POST /api/paper/stop-all：停止注册表中所有 running 会话，回收配额（停止全部并重试）。
export interface PaperStopAllResult {
  stopped: number;
  runs: { strategyId: number; sessionId: string; run: Run }[];
}

export interface PaperChartFilter {
  strategyIds: number[];
}

export interface Overlay {
  id: number;
  name: string;
  color: string;
  equity: EquityPoint[];
}

export interface Metrics {
  totalReturn: number;
  winRate: number;
  profitFactor: number;
  maxDrawdown: number;
  sharpe: number;
  tradeCount: number;
  avgPnl: number;
}

export interface Run {
  id: number;
  strategyId: number;
  instId: string;
  bar: string;
  mode: Mode;
  status: string;
  startTs: number;
  endTs?: number | null;
  metrics: Metrics;
}

export interface PaperSessionInfo {
  sessionId: string;
  runId: number;
  instId: string;
  bar: string;
  state: string;
  equity: number;
  positionSide: "long" | "short" | "flat";
}

// GET /api/paper/sessions 行：session.info() 合并 _session_meta 的形态。
// strategyName 仅批量会话携带（单条 start 不写），groupId 为 null 时表示单条会话。
export interface PaperSessionRow extends PaperSessionInfo {
  strategyId: number;
  strategyName?: string;
  strategyKind?: StrategyKind;
  groupId: string | null;
}

export interface CompareItem {
  strategyId: number;
  name: string;
  runId: number;
  metrics: Metrics;
  equity: EquityPoint[];
}

export interface CompareResult {
  instId: string;
  bar: string;
  items: CompareItem[];
}

// 多样本回测（multi-sample-backtest）：单策略 × 多标的，逐标的各落一条 run；items 顺序与请求 instIds 去重后一致。
export interface MultiSampleItem {
  instId: string;
  status: "ok" | "error";
  runId?: number; // status==="ok"
  metrics?: Metrics; // status==="ok"，复用既有 Metrics
  effectiveCandleCount?: number;
  requestedBars?: number;
  truncated?: boolean;
  combo?: ComboPreviewResult["combo"]; // 组合策略多样本时透传（可选）
  error?: string; // status==="error"，非空错误消息
}

export interface MultiSampleResult {
  strategyId: number;
  bar: string;
  startTs: number;
  endTs: number;
  items: MultiSampleItem[];
}

export interface Health {
  status: string;
  okx: "up" | "down";
  okxLatencyMs: number | null;
  lastOkTs: number | null;
  wsConfigured: boolean;
  cachedFallbackEnabled?: boolean;
  maxPaperSessions?: number;
  /** OKX 链路摘要（okx-link-resilience §4.3）。**可选**：老后端没有这个键。 */
  link?: { overall: string; summary: string };
}

export interface ApiError {
  error: { code: string; message: string };
}
