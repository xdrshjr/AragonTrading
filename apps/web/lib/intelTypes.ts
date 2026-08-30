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
}

export interface IntelRound {
  id: number;
  instId: string;
  kind: "collect" | "curate";
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
}

export interface IntelProbeResult {
  ok: boolean;
  latencyMs: number;
  sampleTitle: string;
  message: string;
}
