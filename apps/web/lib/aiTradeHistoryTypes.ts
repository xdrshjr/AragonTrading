// AI 自动交易统计历史 wire 类型（ai-trade-stats-dashboard §3.8）：与后端 schemas.AiTradeHistory* 逐字对齐。
// 皆为组件 / lib 局部类型，不入 store（历史走 TanStack Query，不持久化）。时间戳全为秒（§6.2 / P2-8）。
import type { FeeRatesWire, ReconcileWire } from "./aiTradeCost";
import type { EconomicsSnapshot, IntegritySnapshot } from "./aiTradeIntegrity";
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
  // ai-trade-loss-and-liveness-hardening E-6：混装到底占多少笔。`costBasisMixed` 只说得出「有」。
  // **整键缺席**（老后端）与 `{net: n, gross: 0}` 是两个结论，故读点绝不用 `?? 0` 抹平。
  costBasisCounts?: Record<string, number>;
}

export interface AiTradeHistoryListOut {
  items: AiTradeHistoryEntry[];
  total: number;
  limit: number;
  offset: number;
}

export interface AiTradeHistorySummary {
  economics?: EconomicsSnapshot | null;
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
  // E-6：同上（历史详情走 `response_model` 那条路，后端 schema 与这里两处都要有）。
  costBasisCounts?: Record<string, number>;
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
    integrity?: IntegritySnapshot | null;
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
    // ---- fill-truth-and-effective-controls：本轮四组加法键（老记录缺键 ⇒ 各自整段隐藏）----
    /**
     * 轴 G-1：`actionCounts` / `holdRate` / `avgConfidence` 的分母是**保留的 ≤N 条决策样本**，
     * 而 `decisionRounds` 是会话真实完成轮数——两个分母并排出现在同一张卡上，而 wire 上此前
     * 没有任何字段承载这件事。生产实测 run 1627 因此对用户说「484 轮里 100% 观望、一次都没
     * 想过开仓」，而真相是「想开 117 次，其中 106 次被同向共识拦下」。
     */
    sampleRounds?: number;
    sampleScope?: string;
    /** 轴 C / E：仓位求解与挂单去留的会话级计数（`build_sizing_stats` 的产物）。 */
    sizing?: AiTradeSizingStats | null;
    /** 轴 D-2：逆势通路五格细分。一次逆势否决都没发生过 ⇒ 整段缺席（绝不显示一张全零卡）。 */
    consensus?: { contrarianBlocked?: AiTradeContrarianBlocked | null } | null;
    /**
     * 情报渐进式披露的会话级读数（intel-session-binding 轴 D）。
     *
     * 未装配 / 会话未采信情报（`mode="off"`）/ 一轮都没注入过 ⇒ **整键缺席** ⇒ 那一行不渲染。
     * 绝不先显示一个会被当真的 0：「这次没喂情报」与「喂了但一个字都没进上下文」是两句
     * 完全不同的话。
     */
    intelDisclosure?: AiTradeIntelDisclosureStats | null;
  };
}

/**
 * `aiStats.intelDisclosure` 的形状（与后端 `intel/binding.disclosure_stats` 逐键同构）。
 *
 * `hidden` 与 `same` 两个数在后端页脚里就是**分两句写**的：前者是「预算装不下」，后者是
 * 「见过所以折叠」。这里同样不合并——合并之后模型与用户都会读成一个数。
 */
export interface AiTradeIntelDisclosureStats {
  /** 本会话的生效档（`index` | `full`；`off` 时整键根本不出现）。 */
  mode: string;
  roundsInjected: number;
  roundsFull: number;
  roundsRepeat: number;
  /** 单轮渲染出来的最大字符数 / 全程累计字符数（平均 = 累计 ÷ roundsInjected）。 */
  charsMax: number;
  charsTotal: number;
  expandRounds: number;
  expandItems: number;
  /** 其中因横向折叠而补给复核官的条数（复核是无状态子调用，它从没见过那几条）。 */
  expandFolded: number;
  /** 真的进了情报段的告警条数。 */
  alertPinned: number;
  /** 因本会话未采信情报而**没有**唤醒决策的告警条数。 */
  alertMuted: number;
  /**
   * 「已看过」集合被重置的次数，**按成因分格**。
   *
   * 一个只报「重置过 5 次」的整数在事后对账里回答不了唯一值得问的那个问题——是暂停造成的，
   * 是上下文被摘要吃掉了，还是压缩连续太久撞了上界。
   */
  seenReset: { pause: number; summary: number; streak: number };
}

/**
 * 仓位 / 刹车 / 挂单去留的会话级计数（`aiStats.sizing`）。
 *
 * 本轮之前 `quantizeErasedRounds` 在 `apps/web` 全仓**零读点**——`sizing.py` 的模块 docstring
 * 早已如实登记过「第一档减速与相关性缩减在最常见断面上是 no-op」，但「暴露」那一半没做完：
 * 算出来了，没人看得见。生产实测最新那一组 5 个成员上 `scaledRounds` 与 `quantizeErasedRounds`
 * **逐个相等**（命中率 100%）。
 */
export interface AiTradeSizingStats {
  gatedRounds?: number;
  scaledRounds?: number;
  scaleRate?: number;
  avgScaleRatio?: number | null;
  /** 其中有多少次缩减被档位地板原样吃了回去。它与 `scaledRounds` 不可互相换算。 */
  quantizeErasedRounds?: number;
  brakeSlowRounds?: number;
  brakeHaltRounds?: number;
  corrScaledRounds?: number;
  /** 轴 C-1：风控层解出**精确的零**而未开仓的轮数（与预算大小毫无关系）。 */
  solvedZeroVetoes?: number;
  /** 轴 C-1：解出一个低于「最小一格 × floor_ratio」的正数而未开仓的轮数（默认闸关 ⇒ 恒 0）。 */
  belowFloorVetoes?: number;
  /** 轴 E：本轮想挂的与已挂的逐字同向同价，于是保留原单不撤不重挂。 */
  restingKept?: number;
  /** 轴 E：撤掉再按新价挂回去（真正的「重挂」）。 */
  restingRepriced?: number;
  restingCancelled?: number;
  /** 其中因 TTL 到期被撤的笔数——双时钟修复之前，它在 15m 根上几乎等于全部挂单笔数。 */
  restingExpired?: number;
}

/** 轴 D-2：逆势通路被拦下的五格细分（顺序即 `_contrarian_block` 的判定序）。 */
export interface AiTradeContrarianBlocked {
  opposed?: number;
  quota?: number;
  conf?: number;
  evidence?: number;
  size?: number;
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
