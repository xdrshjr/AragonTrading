// 标的市场情报层的 wire 类型（ai-trade-market-intel §3.7 / §4.1）。
// camelCase，与后端 `db/ai_intel_repositories.py` 的 `intel_*_out` 逐键同构——那三个函数是后端
// 唯一的 wire 装配点，改这里必须同时改那里（两处各抄一份必漂移）。

/** 层级：存储与管理的主轴（用户口中的「长期 / 中期 / 超短」）。 */
export type IntelTier = "ultra" | "mid" | "long";
/** 类别：内容类型，与层级**正交**（一条 macro 可以住在超短，也可以住在长期）。 */
export type IntelCategory =
  | "market"
  | "sentiment"
  | "macro"
  | "onchain"
  | "project"
  | "risk";
export type IntelStance = "bullish" | "bearish" | "neutral";
export type IntelImpact = "low" | "medium" | "high";
export type IntelStatus = "active" | "archived" | "superseded" | "muted";
/** 核查判定（ai-trade-intel-closed-loop 轴 C）。`""` = 尚未核查过，与三个判定并列。 */
export type IntelVerdict = "" | "confirmed" | "refuted" | "unresolved";

export interface IntelSource {
  url: string;
  title: string;
  publisher: string;
  domain: string;
  publishedTs: number;
}

export interface IntelItem {
  id: number;
  instId: string;
  tier: IntelTier;
  category: IntelCategory;
  headline: string;
  summary: string;
  stance: IntelStance;
  impact: IntelImpact;
  /** 0..1。**服务端求解**，不是模型自报——模型给自己打分永远是 0.8。 */
  confidence: number;
  eventTs: number;
  capturedTs: number;
  updatedTs: number;
  expiresTs: number;
  status: IntelStatus;
  pinned: boolean;
  /** 用户手改过。与 `pinned` 并列的第二把免死金牌：自动清理一律跳过它。 */
  userEdited: boolean;
  undated: boolean;
  /** 事件在未来（今晚的 CPI、下周的解锁）。影响 TTL 与升级判据。 */
  isScheduled: boolean;
  origin: "agent" | "user";
  sources: IntelSource[];
  sourceCount: number;
  domainCount: number;
  dedupKey: string;
  supersedesId: number | null;
  promoteCount: number;
  demoteCount: number;
  lastCuratedTs: number;
  curationNote: string;
  createdTs: number;
  // ---- closed-loop §5.2 的十个新键（与 `intel_item_out` 逐键同构）----
  /** 被引用总次数（含 hold 轮）。 */
  citeCount: number;
  /** 作为**入场**依据被引用的次数。 */
  citeEntryCount: number;
  /** 已结算且非 `void` 的引用数（`void` = 结算尝试发生过、净额不可得）。 */
  settledCount: number;
  winCount: number;
  /** 已结算净额累加。**关联**净额——共现统计，不是因果归因。 */
  netPnlUsd: number;
  lastCitedTs: number;
  /** `refuted` ⇒ 已停止喂给交易 Agent（首击即生效，受保护条目同样）。 */
  verdict: IntelVerdict;
  verdictTs: number;
  verdictNote: string;
  verifyCount: number;
}

export interface IntelRound {
  id: number;
  instId: string;
  kind: "collect" | "curate" | "verify";
  groupId: string;
  startedTs: number;
  finishedTs: number;
  status: "ok" | "degraded" | "failed";
  searchProvider: string;
  queries: string[];
  docCount: number;
  domainCount: number;
  accepted: number;
  rejected: number;
  merged: number;
  /** 拒收码直方图（`{"bad_ref": 2, ...}`）。 */
  rejects: Record<string, number>;
  /** 整理动作直方图（含被驳回的）。 */
  actions: Record<string, number>;
  inputTokens: number;
  outputTokens: number;
  degradeCode: string;
  errorText: string;
}

/** 降级态。**文案由后端下发**，前端不二次编（两处各写一份必漂移）。 */
export interface IntelDegrade {
  code: string;
  message: string;
  retryInS: number | null;
}

export interface AiTradeIntelSummary {
  instId: string;
  counts: { ultra: number; mid: number; long: number; archived: number };
  /** `capturedTs > lastSeenTs` 的 active 条数（**服务端算**，真值在 `ai_intel_watch.last_seen_ts`）。 */
  unread: number;
  lastSeenTs: number;
  /** `null` ⇒ 从未成功采集过 ⇒ 该标的**不注入**决策 prompt。 */
  lastCollectTs: number | null;
  lastCurateTs: number | null;
  nextDueTs: number | null;
  inFlight: boolean;
  degrade: IntelDegrade | null;
  providerConfigured: boolean;
  dailyUsed: number;
  dailyCap: number;
  /** closed-loop 轴 B 的会话级读数。`netPnlUsd` 是**关联**净额（共现，不是因果）。 */
  citations: {
    cited: number;
    entryCited: number;
    settledTrips: number;
    winCount: number;
    netPnlUsd: number;
    neverCited: number;
  };
  /** closed-loop 轴 C 的判定分布。`pending` = 尚未核查过，与 `unresolved` 分开计。 */
  verify: {
    confirmed: number;
    refuted: number;
    unresolved: number;
    pending: number;
    lastVerifyTs: number | null;
  };
}

export interface IntelTierBucket {
  items: IntelItem[];
  total: number;
}

export interface IntelReadResponse {
  instId: string;
  summary: AiTradeIntelSummary;
  tiers: Record<IntelTier, IntelTierBucket>;
  archivedTotal: number;
  mutedTotal: number;
}

export interface IntelSourceInput {
  url: string;
  title?: string;
  publishedTs?: number | null;
}

export interface IntelItemInput {
  tier: IntelTier;
  category: IntelCategory;
  headline: string;
  summary: string;
  stance: IntelStance;
  impact: IntelImpact;
  eventTs?: number | null;
  sources: IntelSourceInput[];
  pinned?: boolean;
}

export interface IntelItemPatch {
  tier?: IntelTier;
  category?: IntelCategory;
  headline?: string;
  summary?: string;
  stance?: IntelStance;
  impact?: IntelImpact;
  pinned?: boolean;
  status?: "active" | "archived" | "muted";
  /** 「我不同意」：清 verdict、置 userEdited、**不清 verifyCount**（撤销出口不是旋转门）。 */
  clearVerdict?: true;
}

export interface IntelSettings {
  enabled: boolean;
  injectEnabled: boolean;
  provider: string;
  /** **绝不含明文密钥**：只有 `hasKey` 与末四位预览。 */
  hasKey: boolean;
  keyPreview: string;
  collectIntervalMin: number;
  curateEveryNCollects: number;
  dailyCap: number;
  estimate: { roundsPerDay: number; inputTokensPerDay: number };
  // ---- closed-loop：三把安全单键 + 五个核查旋钮（与 `INTEL_SETTING_BRIDGE` 逐键对应）----
  citeEnabled: boolean;
  attributionEnabled: boolean;
  verifyEnabled: boolean;
  verifyEveryNCurates: number;
  verifyMaxItems: number;
  verifyDocsPerItem: number;
  blockedSweepEnabled: boolean;
  citeMaxRefs: number;
}

export interface IntelSettingsPatch {
  enabled?: boolean;
  injectEnabled?: boolean;
  provider?: string;
  /** 三态：`""`/缺席 = 保持不变；`"__clear__"` = 清除；其余 = 写入。 */
  apiKey?: string;
  collectIntervalMin?: number;
  curateEveryNCollects?: number;
  dailyCap?: number;
  citeEnabled?: boolean;
  attributionEnabled?: boolean;
  verifyEnabled?: boolean;
  verifyEveryNCurates?: number;
  verifyMaxItems?: number;
  verifyDocsPerItem?: number;
  blockedSweepEnabled?: boolean;
  citeMaxRefs?: number;
}

export interface IntelProbeResult {
  ok: boolean;
  latencyMs: number;
  sampleTitle: string;
  message: string;
}

// ---------------- closed-loop 新增的读写类型（§4.1 #13-#19） ----------------

/** 一条来源取舍（黑白名单）。`instId` 为空 = 全局生效。 */
export interface IntelFeedbackRow {
  id: number;
  scope: string;
  key: string;
  action: "block" | "trust";
  instId: string;
  createdTs: number;
}

export interface IntelFeedbackInput {
  scope?: "domain";
  key: string;
  action: "block" | "trust";
  instId?: string;
}

/** 当前库存里出现过的域名聚合（信源页签上半）。 */
export interface IntelSourceRow {
  domain: string;
  itemCount: number;
  lastTs: number;
  /** `""` = 未表态；`blocked` / `trusted` = 用户已取舍。 */
  state: "" | "blocked" | "trusted";
  /** 生效的那条取舍记录的作用域（`""` = 全局）。撤销必须按它发，否则删不中那一行。 */
  stateInstId: string;
}

/** 影响力页签的全部读数。`netPnlUsd` 一律称「**关联**净额」（R-1）。 */
export interface IntelImpactReport {
  instId: string;
  totals: {
    citeCount: number;
    citeEntryCount: number;
    settledCount: number;
    winCount: number;
    netPnlUsd: number;
  };
  top: IntelItem[];
  worst: IntelItem[];
  neverCited: number;
}

/** 一条引用明细。`netPnlUsd` 可空：`null` = 结算不可得（`outcome="void"`）。 */
export interface IntelCitationRow {
  id: number;
  itemId: number;
  instId: string;
  runId: number;
  roundNo: number;
  citedTs: number;
  refToken: string;
  clOrdId: string;
  role: "entry" | "exit" | "hold";
  proposedAction: string;
  finalAction: string;
  traded: boolean;
  positionSeq: number;
  settled: boolean;
  settledTs: number;
  netPnlUsd: number | null;
  holdS: number;
  exitSource: string;
  outcome: "" | "win" | "loss" | "flat" | "void";
  createdTs: number;
}

/** 检索源健康读数（设置卡上的一行只读文案）。 */
export interface IntelProviderHealth {
  provider: string;
  rounds: number;
  ok: number;
  failed: number;
  degraded: number;
  breakerRemainingS: number;
}
