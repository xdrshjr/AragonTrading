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
  // ---- evidence-grounding §4.2 的四个新键（与 `intel_item_out` 逐键同构）----
  /** 是否有 >=1 条已通过逐字比对的原文引证。 */
  quoted: boolean;
  /** 已验证引文条数（准入 + 核查合计）。 */
  quoteCount: number;
  /**
   * 无引文时的**原因**，由后端下发（前端不猜——它手里没有任何能区分这四种情况的信息）：
   * `""` = 有引文 | `legacy` = 本条早于原文取证功能 | `modeOff` = 引文要求已关闭
   * | `noBody` = 该条来源均未抓到正文 | `modelOmitted` = 有正文但模型没给
   * | `verifyFailed` = 给了但一条都没比中
   */
  quoteState: IntelQuoteState;
  /** 已排期事件的兑现时刻；`0` = 未兑现 / 非排期。 */
  scheduleResolvedTs: number;
}

/** 无引证的五种原因 + 有引证的空串（后端下发，前端只做文案映射）。 */
export type IntelQuoteState =
  | ""
  | "legacy"
  | "modeOff"
  | "noBody"
  | "modelOmitted"
  | "verifyFailed";

/** 一段已通过逐字比对的原文引证（与 `intel_quote_out` 逐键同构）。 */
export interface IntelQuote {
  id: number;
  itemId: number;
  instId: string;
  roundId: number;
  /** `admit` = 准入时的原文依据；`verify` = 核查判定的依据。两组在抽屉里分开渲染。 */
  role: "admit" | "verify";
  ref: string;
  /** **文档里的原文形态**，不是模型给的那一串。 */
  text: string;
  sourceField: "body" | "snippet" | "title";
  docUrl: string;
  docDomain: string;
  docPublishedTs: number;
  /** 原文里含劝告性措辞 ⇒ 加一枚中性标记。**不是拒收**：它是真实原文。 */
  hasImperative: boolean;
  verifiedTs: number;
}

/** 一篇来源网页在正文缓存里的当前状态；缓存已淘汰 ⇒ 该 URL **不出现**在 `docs` 里。 */
export interface IntelDocState {
  url: string;
  finalUrl: string;
  domain: string;
  title: string;
  status: string;
  httpStatus: number;
  fetchedTs: number;
  bodyChars: number;
  truncated: boolean;
}

/** `GET /intel/items/{itemId}/evidence` 的回执。无引文 ⇒ 200 + 空数组（不是 404）。 */
export interface IntelEvidence {
  itemId: number;
  quoteMode: "off" | "soft" | "strict";
  quotes: IntelQuote[];
  docs: IntelDocState[];
}

/** 设置卡「原文缓存」那一行。`bodyBytes` 是**真实字节**，不是前端乘出来的。 */
export interface IntelDocumentHealth {
  cachedDocs: number;
  bodyBytes: number;
  oldestFetchedTs: number;
  lastPrunedTs: number;
  readerEnabled: boolean;
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
  // ---- evidence-grounding §4.2 的四个新计数（轮次页签的两列 + 命中率徽标）----
  /** 本轮打算取正文的篇数（含命中缓存的）。 */
  readerAttempt: number;
  /** 真的拿到正文的篇数。 */
  readerOk: number;
  /** 模型本轮声称给出的引文条数（命中率的分母）。 */
  quotesProposed: number;
  /** 通过逐字比对并落库的引文条数（命中率的分子）。 */
  quotesVerified: number;
}

// ---------------- ai-trade-intel-actionability（§4.1） ----------------

/**
 * 一行告警台账（含「没告警」与「告警了但没能唤醒」）。
 *
 * `suppressedText` / `outcomeText` **由后端下发**：进 wire 的文案由后端管，只在轮存根直方图里
 * 出现的码才由前端 `CODE_LABEL` 管（跟随 `degrade` 的既有裁决）。两处各写一份必漂移，而漂移的
 * 症状是用户看到一个裸 slug。
 */
export interface IntelAlert {
  id: number;
  instId: string;
  groupId: string;
  /** 0 = 本轮一条候选都没有（空轮也写一行 ⇒「今天为什么没告警」永远有答案）。 */
  itemId: number;
  roundId: number;
  createdTs: number;
  severity: number;
  fired: boolean;
  /** `fired=true` 时恒为空串。 */
  suppressedCode: string;
  suppressedText: string;
  /** `""` = 尚无结果；超过待处理时限仍为空 ⇒ **渲染期**判为「未在时限内响应」，不回写库。 */
  outcome: string;
  outcomeText: string;
  outcomeTs: number;
  /** `outcome="woke"` 时指向那一轮决策（可跳到轨迹）。 */
  runId: number;
  roundNo: number;
  headline: string;
  category: IntelCategory | "";
  tier: IntelTier | "";
  stance: IntelStance | "";
  impact: IntelImpact | "";
  domainCount: number;
  quoted: boolean;
  confidence: number;
  /**
   * 本轮的注入编号（`"I02"`）；`cite` 关时为空串。
   *
   * 与 `inDigest` **一起**由第三跳（`IntelRuntime._alert`）在 digest 刚重算完那一刻补写进
   * 台账——两者在建行的那一刻都还不知道（digest 尚是上一轮的）。
   */
  ref: string;
  /**
   * 这条情报**真的出现在了决策 prompt 的情报段里**吗。
   *
   * **绝不能用 `!ref` 冒充它**：`cite` 关时全库都没有编号，而条目照样在情报段里；反过来，
   * 超短层排在注入顺序最后，长期层一攒满，有编号的告警条目也进不了 prompt（R-15）。这一列
   * 就是 R-15 在界面上的落点——唤醒句那一侧已经据同一个事实分两态渲染。
   */
  inDigest: boolean;
  /** 判据明细（severity 的各分量、ageMin 等）。**自由形状**，故该端点不挂 `response_model`。 */
  reasons: Record<string, number | boolean>;
}

/** 一个战绩 / 信誉桶。`accuracy` **可空**：`null` = 样本不足，**绝不是 0**。 */
export interface IntelStat {
  settled: number;
  wins: number;
  netPnlUsd: number;
  confirmed: number;
  refuted: number;
  unresolved: number;
  samples: number;
  accuracy: number | null;
}

export interface IntelEfficacyCategory extends IntelStat {
  category: IntelCategory;
  /** `[-maxWeight, +maxWeight]`。**只影响排序与举证责任，不是门禁**（R-6）。 */
  weight: number;
}

/** 影响力页签「战绩与信誉」块的全部读数。 */
export interface IntelEfficacy {
  enabled: boolean;
  /** 收缩伪计数：样本少于它的桶权重恒为 0。 */
  kMin: number;
  sampleN: number;
  overall: IntelStat;
  byCategory: IntelEfficacyCategory[];
}

/**
 * 一对立场矛盾（同类别、方向相反、主题高度重叠、事件时刻同窗）。
 *
 * `openRounds` **不落库**：由两端的 `lastCuratedTs` 与整理轮流水推算。
 */
export interface IntelConflict {
  aId: number;
  bId: number;
  overlap: number;
  windowH: number;
  category: IntelCategory;
  openRounds: number;
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
  /**
   * 近 20 轮 `quotesVerified / quotesProposed`。
   * **`null` = 分母 0**（「没提过引文」），与 `0`（「提了全没中」）是两句不同的话。
   */
  quoteHitRate: number | null;
  /** actionability 轴 A：近 24 小时 fired 计数。未装配 ⇒ 0（形状恒一致，绝不 `undefined`）。 */
  alerts24h: number;
  /** 最近一次告警时刻；**`null` = 从未告警过**（与 0 分开——后者会被渲染成 1970 年）。 */
  lastAlertTs: number | null;
  /** actionability 轴 B 的摘要形态（完整的 byCategory / byDomain 由 REST 拉，不进 WS）。 */
  efficacy: IntelEfficacy;
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
  /** actionability 轴 C：当前库存里的矛盾对（老后端 ⇒ 缺席 ⇒ 徽标与筛选整段不渲染）。 */
  conflicts?: IntelConflict[];
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
  // ---- evidence-grounding：轴 A / 轴 B 的三把单键（与 `INTEL_SETTING_BRIDGE` 逐键对应）----
  readerEnabled: boolean;
  readerTopK: number;
  quoteMode: "off" | "soft" | "strict";
  /** 近 20 轮命中率；`null` = 还没提过引文。 */
  quoteHitRate: number | null;
  // ---- actionability：三把总闸 + 三个告警旋钮（与 `INTEL_SETTING_BRIDGE` 逐键对应）----
  alertEnabled: boolean;
  alertMinImpact: "medium" | "high";
  alertCooldownMin: number;
  alertDailyCap: number;
  efficacyEnabled: boolean;
  conflictEnabled: boolean;
  /**
   * 「须有逐字原文引证」的**生效值**（不是存储值）：`quoteMode="off"` 或抓取关闭时它被降级为
   * `false`，而用户存下来的偏好一个字节没动。设置卡据它把复选框置灰并写明原因。
   */
  alertRequireQuote: boolean;
  /**
   * 待处理告警的时限（秒，只读；本轮不让用户从面板改它）。
   *
   * 动态页签用它把「还没回」与「再也不会回」分开。**必须从后端读而不是在前端写死一个数**：
   * 它是一个 30–900 的配置项，写死之后一个把它调到 900 的用户会在 180 秒时看到一句
   * 「会话未在时限内响应」——而后端那一侧还在正常等着。
   */
  alertPendingTtlS: number;
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
  readerEnabled?: boolean;
  readerTopK?: number;
  quoteMode?: "off" | "soft" | "strict";
  alertEnabled?: boolean;
  alertMinImpact?: "medium" | "high";
  alertCooldownMin?: number;
  alertDailyCap?: number;
  efficacyEnabled?: boolean;
  conflictEnabled?: boolean;
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
  // ---- actionability 轴 B：这一页上做拉黑决策**唯一需要**的三列 + 准确率 ----
  confirmed: number;
  refuted: number;
  unresolved: number;
  /** `null` = 样本不足（低于 `efficacyMinDomainSamples`）。**绝不显示一个假的 0%**。 */
  accuracy: number | null;
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
  /** actionability 轴 B 的「战绩与信誉」块（老后端 ⇒ 缺席 ⇒ 整块不渲染）。 */
  efficacy?: IntelEfficacy;
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
