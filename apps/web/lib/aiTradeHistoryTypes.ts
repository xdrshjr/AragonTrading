// AI 自动交易统计历史 wire 类型（ai-trade-stats-dashboard §3.8）：与后端 schemas.AiTradeHistory* 逐字对齐。
// 皆为组件 / lib 局部类型，不入 store（历史走 TanStack Query，不持久化）。时间戳全为秒（§6.2 / P2-8）。
import type { FeeRatesWire, ReconcileWire } from "./aiTradeCost";
import type {
  AiTradeAcceptance,
  AiTradeExitMix,
  AiTradeFundingStats,
  AiTradeHorizonStats,
  AiTradeOpenFunnel,
  AiTradeTrendStats,
} from "./aiTradeTypes";
import type { AiTradeRealizedStats } from "./aiTradeProfitability";
import type { Trade } from "./types";

// 历史会话列表单行 KPI（后端逐成员配对聚合；口径同 autoTradePnl.summarizeRoundTrips）。
export interface AiTradeHistoryEntry {
  groupId: string;
  strategyName: string;
  bar: string;
  instIds: string[];
  startedTs: number;
  endedTs: number;
  status: string;
  totalBudgetUsd: number;
  realizedPnlUsd: number;
  returnPct: number;
  tradeCount: number;
  winRate: number;
  bestUsd: number;
  worstUsd: number;
  // 成本三键（cost-truth §5.6）：realizedPnlUsd 已是**净额**，与 feeUsd / grossPnlUsd 并列才能
  // 逐笔解释「数字为什么下移」。老后端不下发 ⇒ 读点 `?? 0` 兜底，故全部可选。
  feeUsd?: number;
  grossPnlUsd?: number;
  costBasisMixed?: boolean;
}

export interface AiTradeHistoryListOut {
  items: AiTradeHistoryEntry[];
  total: number;
  limit: number;
  offset: number;
}

export interface AiTradeHistorySummary {
  realizedPnlUsd: number;
  returnPct: number;
  tradeCount: number;
  winRate: number;
  bestUsd: number;
  worstUsd: number;
  // 利润捕获率五键（ai-trade-profit-protection-adaptive §2.4.3）：与前端 summarizeProfitCapture
  // 公式逐字对应。**历史页一律取后端这份，不在前端重算**（老库缺列时后端已按 getattr 降级为 0）。
  // 老后端不下发这五键 ⇒ 读点一律 `?? 0` 兜底，故全部可选。
  peakPnlUsd?: number;
  capturedPnlUsd?: number;
  captureRatio?: number;
  capturePairs?: number;
  noPeakCount?: number;
  // cost-truth：捕获率五键**分子分母同为毛额**（不随净额总闸变化）；手续费口径看这三键。
  feeUsd?: number;
  grossPnlUsd?: number;
  costBasisMixed?: boolean;
  // 周期验收闭环（horizon-acceptance [P1-9]）：**后端合并各成员 run 的原始计数器后判定一次**，
  // 故这里恒是**一张**组级卡而不是 N 张（比例类指标不可在前端平均）。任一成员缺
  // `acceptanceInput`（本特性上线前的老记录）⇒ 两键缺席 ⇒ 整卡不渲染。
  horizonAcceptance?: AiTradeAcceptance | null;
  exits?: AiTradeExitMix | null;
  funding?: AiTradeFundingStats | null;
}

// metrics_json 解析（全可选，缺失即优雅降级——崩溃 / 对账会话没有富化字段）。
// 深度思维链的会话级观测（§3.4.4）。`inputTokens/outputTokens` 在
// `tokensProviderKnown === false` 时为 **null 而非 0**。
export interface AiTradeReasoningStats {
  depth: string;
  rounds: number;
  medianChars?: number | null;
  medianStages?: number | null;
  truncatedRounds?: number;
  reaskedRounds?: number;
  reaskAdoptedRounds?: number;
  deadlineHitRounds?: number;
  contentFilteredRounds?: number;
  inputTokens?: number | null;
  outputTokens?: number | null;
  tokensProviderKnown?: boolean;
  reflectionRounds?: number;
  reflectionMedianChars?: number | null;
}

export interface AiTradeRunMetrics {
  totalReturn?: number;
  tradeCount?: number;
  avgPnl?: number;
  roundsCompleted?: number;
  finalSummary?: string | null;
  reconciled?: boolean;
  equityDigest?: { ts: number; equity: number; drawdown: number }[];
  decisionsDigest?: {
    ts: number;
    round: number;
    action: string;
    executed?: boolean;
    confidence?: number | null;
    reason?: string | null;
    reflectionVerdict?: string | null; // R2-07：后端 _digest_decisions 已下发（缺省 null）
  }[];
  aiStats?: {
    decisionRounds: number;
    actionCounts: Record<string, number>;
    holdRate: number;
    avgConfidence: number;
    degradedRounds: number;
    // hold-inertia-observability O2（全可选，老记录缺键即优雅降级——aggregate 全缺返 null 隐藏整行）：
    // 反思四计数为近 50 轮保留样本口径；wakeSkips / maxHoldStreak 为会话级全程直取。
    reflectionRuns?: number;
    reflectionConfirms?: number;
    reflectionAborts?: number;
    reflectionFailClosed?: number;
    wakeSkips?: number;
    maxHoldStreak?: number;
    // 唤醒调度闭环观测（wake-scheduler-closed-loop §4.4）：加法写既有 JSON 列，老记录缺键 ⇒
    // 整段隐藏（绝不显示一排误导性的 0）。`drops` 与 status.wakeDrops **同读**一个后端计数。
    wakeScheduler?: {
      drops?: number;
      catchups?: number;
      catchupCeilingHits?: number;
      positionWatchSkips?: number;
      signalFlipWakes?: number;
      // ⚠️ actionability 评审 P1-7：**这是一处纯声明位**。后端 `session.py` 把
      // `self._wake.stats(...)` **整体赋值**进 `metrics["aiStats"]["wakeScheduler"]`，故新键
      // 天然落库，与本类型毫无关系；而 `wakeScheduler` 在 `apps/web` 里**至今没有任何读点**
      // （历史页看不到任何调度计数，登记为既有欠账）。补它是为了类型与 wire 不分叉，
      // **不是**防丢失措施——照「缺它就永久丢失」去写用例，只会得到一条断言类型声明的空闸。
      intelAlertWakes?: number;
      intelAlertDrops?: number;
      previewTimeouts?: number;
      previewErrors?: number;
      previewDisabled?: boolean;
    };
    // 深度思维链观测（ai-trade-deep-reasoning-chain §3.4.4）：加法写既有 JSON 列，老记录 /
    // off 档缺键 ⇒ 整段隐藏（绝不显示一排误导性的 0）。
    // `tokensProviderKnown=false` 时 `inputTokens/outputTokens` 为 **null 而非 0**——「没花
    // token」与「provider 没告诉我们花了多少」是两个截然不同的结论，混成 0 会误导后续调参。
    reasoning?: AiTradeReasoningStats;
    // 成本真值化（cost-truth §5.4，**顶层**加法键）：老记录缺键 ⇒ 整段隐藏（绝不显示误导性的 0）。
    feeRates?: FeeRatesWire;
    reconcile?: ReconcileWire;
    // 交易周期层观测（holding-horizon §2.12）：加法写既有 JSON 列，老记录缺键 ⇒ 整段隐藏。
    // 零样本时六个派生量为 **null 而非 0**（[P2-3]：「目标区间内占比 0%」与「还没有样本」是两个
    // 截然不同的结论）。
    horizon?: AiTradeHorizonStats;
    // 持仓周期验收闭环（horizon-acceptance §5.3）：单 run 口径的卡片 + 退出分布 + 资金费，
    // 外加 `acceptanceInput`（**原始计数器**）——后者是分组历史在后端合并成一张卡的唯一输入，
    // 老记录缺它 ⇒ 组级卡整键缺席（绝不用半组数据冒充整组结论，[P1-9]）。
    horizonAcceptance?: AiTradeAcceptance | null;
    exits?: AiTradeExitMix | null;
    funding?: AiTradeFundingStats | null;
    acceptanceInput?: Record<string, unknown> | null;
    // 会话级已实现读数（realized-edge-calibration 轴 A）：经既有 `costSnapshot` 通道并入
    // `aiStats`，故与实时侧**同形同源**。老记录缺键 ⇒ 整段隐藏。
    realized?: AiTradeRealizedStats | null;
    // [P1-9] 本会话到底有没有把历史战绩喂给模型（事后按臂对账用）。
    trackRecordShown?: boolean;
    // 开仓漏斗（tradability-and-open-funnel 轴 C）：经 `build_funnel_stats` 落进 `aiStats`，
    // 故与实时侧**同形同源**。方向轴两键（`bySide` / `directionBalance`）随它整体带上
    // （directional-symmetry 轴 D，零 DB 迁移）。老记录缺键 ⇒ 方向卡整卡不渲染。
    openFunnel?: AiTradeOpenFunnel | null;
    // 执行周期趋势基调（execution-trend-verdict §3.7.5）：经 `build_trend_stats` 落进
    // `aiStats`，与实时侧的成员级 `status.trendVerdict` **同形同源**（同一次 `stats_wire()`
    // 调用的产物）。老记录缺键 ⇒ 整卡不渲染（绝不显示一排误导性的 0）。
    trendVerdict?: AiTradeTrendStats | null;
  };
}

export interface AiTradeHistoryMember {
  runId: number;
  instId: string;
  leverage: number;
  budgetUsd: number;
  allocationPct: number;
  strategyNames: string[];
  status: string;
  costBasis?: "net" | "gross";   // 该成员 run 的盈亏口径（老后端不下发 ⇒ undefined）
  metrics: AiTradeRunMetrics;
  trades: Trade[];
}

export interface AiTradeHistoryDetailOut {
  groupId: string;
  strategyName: string;
  bar: string;
  startedTs: number;
  endedTs: number;
  status: string;
  totalBudgetUsd: number;
  // 口径是否为**推定**（cost-truth P0-3）：老 run 缺 costBasis 时按运维配置推定，UI 必须把
  // 「推定」与「已知」分开显示——两件事不可合并成一个徽标。
  costBasisAssumed?: boolean;
  members: AiTradeHistoryMember[];
  summary: AiTradeHistorySummary;
}
