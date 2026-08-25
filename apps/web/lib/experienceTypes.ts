// 交易经验库的全部 wire 类型（ai-trade-experience-context-graph §五 / §六）。
//
// **全部住在本文件**：`lib/aiTradeTypes.ts` 实测 1787 行，已远超 CLAUDE.md 的 800 行阈值，
// 本特性在那边只允许两行（`AiTradeConfig.experience?` 与 `AiTradeSessionRow.experience?`，
// 类型从这里 import）。「超了就再下沉一层，而不是再放宽阈值」是本仓库的成文纪律。
//
// 命名口径：后端回执**统一 camelCase**（含边——`_edge_wire` 把 `src_id`/`dst_id` 翻成
// `srcId`/`dstId`）。同一个回执里两套命名会逼着这份类型照抄一份不一致，那正是评审 P2-3 修掉的。

/** 语境坐标轴 id（闭合词表，与后端 `taxonomy.AXES` 逐字对齐；`symbol` 是开放轴）。 */
export type ExperienceAxis =
  | "symbol" | "bar" | "regime" | "volatility" | "event" | "side"
  | "wake" | "exit" | "horizon" | "cost_regime" | "confidence";

/** 经验来源。`context` 是语境节点，不是经验。 */
export type ExperienceSource = "stat" | "narrative" | "manual" | "imported" | "context";

/** 档位。`archived` 只出现在版本链里。 */
export type ExperienceTier = "candidate" | "normal" | "iron" | "archived";

/**
 * 静音原因。
 *
 * - `user`：人类意志，物化与自动化**永不**覆盖；
 * - `stale` / `flipped`：物化产出的自动静音（新证据即解除，E-9）；
 * - `ineffective`：★ W-C 新增——「注入之后的表现与它自己的结论系统性相反」。
 *   注意这是**共现**不是因果：我们并不知道模型是否真的采纳了它（J-5）。
 */
export type ExperienceMuteReason = "user" | "stale" | "flipped" | "ineffective" | null;

/**
 * 一条经验的「注入后表现」（★ W-C）。
 *
 * **绝不是「贡献」**：`node.weight` 说的是「这个语境桶历史上赚不赚」，本结构说的是「把这句话
 * 喂进去之后，那些单后来怎么样」。两者是完全不同的量，而后者只是共现关系。
 */
export interface ExperienceUtility {
  /** 被注入过几次（只计**开了仓**的轮次）。 */
  injected: number;
  /** 其中口径合格（净额 + 已知费率）、真的进了 bps 聚合的条数。 */
  scored: number;
  /** 注入之后那些往返的净额均值。 */
  meanNetAfter: number | null;
  tAfter: number | null;
  /** 事实与它的断言同向的比例（**「与事实一致率」，不是「准确率」**）。 */
  agreement: number | null;
  /** 该经验注入当时断言的净额符号（冻结值，绝不回填）。 */
  claimSign: number;
  /** 是否已被那条唯一的失效判据判定（三重门槛，且只导向静音）。 */
  ineffective: boolean;
}

/** 效用面板的一行（`GET /api/experience/utility`）。 */
export interface ExperienceUtilityRow extends ExperienceUtility {
  key: string;
  id: number;
  label: string;
  muted: boolean;
  muteReason: ExperienceMuteReason;
  pinned: boolean;
}

/** 效用面板回执。 */
export interface ExperienceUtilityPage {
  items: ExperienceUtilityRow[];
  total: number;
  asOfTs: number;
  /** 失效判据的三重门槛之二，如实下发使 UI 能把「为什么被静音」说清楚（I-8）。 */
  minSamples: number;
  tThreshold: number;
}

/** 一条注入记录（详情抽屉的最近 N 条）。 */
export interface ExperienceInjectionRow {
  id: number;
  groupId: string;
  instId: string;
  seq: number;
  exitTs: number;
  lessonKey: string;
  layer: "l0" | "l1";
  claimSign: number;
  injectedWallTs: number;
  netBps: number | null;
  feeKnown: boolean;
  costBasis: string;
}

/**
 * 采集完整性（★ W-A）。
 *
 * `reasons` 的键集是**闭合的六个**，零值的键照常下发：列出来才知道「这条路今天没走过」与
 * 「这条路不存在」的区别。
 */
export interface ExperienceHarvest {
  gapTotal: number;
  adoptedTotal: number;
  reasons: {
    noOpenContext: number;
    noTrip: number;
    slotEvicted: number;
    persistFailed: number;
    offlineNoReadback: number;
    offlineInconsistent: number;
  };
}

/** 启动请求里的经验模式规则（「字段缺省 ⇒ 开启」的语义在**后端**单点解析）。 */
export interface AiTradeExperienceRule {
  /** false = 本次会话**既不读取也不沉淀**经验（关就是全关，E-15）。 */
  enabled: boolean;
  /** symbol = 只用本标的 + 通用经验；global = 放宽跨标的权重。 */
  scope: "symbol" | "global";
  /** 是否把本次会话的经验写回资产（只读模式 = enabled && !write）。 */
  write: boolean;
}

/** 一条经验（或语境）节点。 */
export interface ExperienceNode {
  id: number;
  kind: "lesson" | "context";
  key: string;
  axis: string | null;
  value: string | null;
  label: string;
  detail: string;
  source: ExperienceSource;
  scope: "symbol" | "global";
  tier: ExperienceTier;
  pinned: boolean;
  muted: boolean;
  muteReason: ExperienceMuteReason;
  support: number;
  weight: number;
  stats: Record<string, unknown>;
  signature: Partial<Record<ExperienceAxis, string>>;
  firstTs: number;
  lastSeenTs: number;
  signStreak: number;
  createdTs: number;
  updatedTs: number;
}

/** 一条有向边。 */
export interface ExperienceEdge {
  id: number;
  srcId: number;
  dstId: number;
  rel: "applies_in" | "co_occurs" | "contradicts" | "refines";
  weight: number;
  support: number;
  updatedTs: number;
}

/**
 * 物化健康（F-2 的规模墙露出点）。
 *
 * 六个键**全部**在 §7.1 的概览条有渲染落点——后端算了却没有呈现位置的观测量等于死码（I-8）。
 * `slow` / `torn` / `overflow` 三个状态位的存在理由是同一条：一个跑不完的后台任务如果不说话，
 * 用户看到的就是「经验不再更新了，但哪里都没有报错」。
 */
export interface ExperienceMaterializeHealth {
  lastTs: number;
  lastMs: number;
  events: number;
  /** 单次耗时越过告警线 ⇒ 规模已接近自动重建预算。 */
  slow: boolean;
  /** 上次重建没走完全部四次提交 ⇒ 图可能「新节点 + 旧边」（E-14）。 */
  torn: boolean;
  /**
   * 事件数超过**全量重建**的规模闸 ⇒ 自动的**全量**重建已停止（E-6）。
   *
   * ★ 本轮之后它只约束全量：增量物化的成本与库大小近似无关，故大库上 `mode==="incremental"`
   * 时这一位恒为 false——那正是「规模墙被推开了」这句承诺的可见形态。
   */
  overflow: boolean;
  /** 上次物化走的是哪条路（★ W-B）。 */
  mode: "full" | "incremental" | "";
  /** 走全量的原因（五条判据之一）；增量 ⇒ null。 */
  fullReason: string | null;
  /** 水位线之后还剩多少条没吃（满批时 > 0）。一个悄悄吃不完的队列必须能被看见。 */
  pendingEvents: number;
}

/** `GET /api/experience/health` —— 概览条与启动弹窗共用的**轮询**读数。 */
export interface ExperienceHealth extends ExperienceMaterializeHealth {
  epoch: number;
  watermarkEventId: number;
  eventCount: number;
  lastMode: string;
  /** 因「注入后表现与其结论系统性相反」被自动静音的条数（启动弹窗那一行取它，零新增请求）。 */
  utilityMuted: number;
}

/** `POST /api/experience/prune` 回执。 */
export interface ExperiencePruneResult {
  matched: number;
  deleted: number;
  skippedPinned: number;
  skippedUserMuted: number;
}

/** 概览条读数（`GET /api/experience/overview`）。 */
export interface ExperienceOverview {
  lessons: { iron: number; normal: number; candidate: number; muted: number; archived: number };
  events: number;
  /** 「产生过至少一笔已结算往返的分组数」——空跑一天没成交的会话不计入（口径见 §4.8）。 */
  sessions: number;
  symbols: string[];
  lastUpdatedTs: number;
  /** 成本口径不可比、被排除在 bps 统计外的往返数（I-7）。 */
  excludedByCostBasis: number;
  /** 蒸馏输出越界被整条丢弃的条数（I-5）。 */
  rejectedNarrative: number;
  /** 续跑接管 / 暂停期离线平仓导致采不到的往返累计数（R-15 的幸存者偏差露出点）。 */
  harvestGapTotal: number;
  /** ★ W-A · 采集完整性明细（补采条数 + 逐原因）。 */
  harvest: ExperienceHarvest;
  /** ★ W-C · 效用闭环；总闸关 ⇒ **整键缺席**（不是一个恒为 0 的假读数）。 */
  utility?: { scored: number; ineffective: number };
  materializeHealth: ExperienceMaterializeHealth;
  costRegimeCuts: Record<string, unknown>;
}

/** 图谱取数回执。`truncated` 必须**如实**呈现——静默截断读起来像「全都在这儿了」。 */
export interface ExperienceGraphView {
  nodes: ExperienceNode[];
  edges: ExperienceEdge[];
  total: number;
  truncated: boolean;
}

/** 列表视图回执（与图谱**同数据源同筛选**，A-22）。 */
export interface ExperienceLessonPage {
  items: (ExperienceNode & { utility?: ExperienceUtility })[];
  total: number;
}

/** 一条溯源证据（详情抽屉的最近 N 笔支撑往返）。 */
export interface ExperienceEvidenceRow {
  id: number;
  groupId: string;
  instId: string;
  bar: string;
  entryTs: number;
  exitTs: number;
  netBps: number | null;
  exitSource: string;
  side: string;
}

/** 详情抽屉回执。 */
export interface ExperienceNodeDetail {
  node: ExperienceNode;
  stats: Record<string, unknown>;
  contexts: (ExperienceNode & { edgeWeight: number })[];
  versions: ExperienceNode[];
  contradicts: ExperienceNode[];
  evidence: ExperienceEvidenceRow[];
  /** ★ W-C · 「注入后表现」区；无注入样本 / 总闸关 ⇒ 整键缺席。 */
  utility?: ExperienceUtility & { injections: ExperienceInjectionRow[] };
}

/** 检索预演里被逐字渲染的一条经验（与 prompt 里那一份同源）。 */
export interface ExperienceDisclosureItem {
  key: string;
  id: number;
  text: string;
  support: number;
  tStat: number | null;
  source: ExperienceSource;
  tier: ExperienceTier;
  pinned: boolean;
  score: number;
  match: number;
  recency: number;
}

/** 运行中会话的快照对照行（`diverged` 由**服务端**判定，前端不重算）。 */
export interface ExperienceLiveSession {
  instId: string;
  graphAsOfTs: number;
  diverged: boolean;
}

/** 检索预演回执。 */
export interface ExperiencePreview {
  tier: "full" | "brief" | "repeat";
  l0: ExperienceDisclosureItem[];
  l1: ExperienceDisclosureItem[];
  disputed: { a: string; b: string }[];
  suppressed: string[];
  totals: { lessons: number; events: number; sessions: number };
  maxChars: { l0: number; l1: number };
  graphAsOfTs: number;
  l0Text: string;
  l1Text: string;
  chars: { l0: number; l1: number; total: number; budget: { l0: number; l1: number } };
  seedActivation: { key: string; a: number }[];
  liveSessions: ExperienceLiveSession[];
}

/** 会话状态里的经验观测（`status.experience` / `aiStats.experience` 共用）。 */
export interface AiTradeExperienceStatus {
  enabled: boolean;
  scope: "symbol" | "global";
  write: boolean;
  tier: "full" | "brief" | "repeat" | null;
  l0: number;
  l1: number;
  injections: number;
  chars: number;
  eventsHarvested: number;
  /**
   * **本会话**漏采的往返数（与概览条那个库存累计口径不同，两者都要有）。
   *
   * ★ 保留一个版本：`lib/store.ts` 的运行中卡片是逐键手抄装配，桌面端旧包与新后端并存时
   * 删掉它会让那张卡直接空掉。新代码请读 `harvest.gap`。
   */
  harvestGap: number;
  /** ★ W-A · 本会话的采集完整性（`gap` / 补采条数 / 逐原因）。 */
  harvest?: { gap: number; adopted: number;
              reasons: Record<string, number> };
  graphAsOfTs: number;
}

/** 本轮实际喂入的经验（`round_start` 轨迹的加法键；未装配 ⇒ null）。 */
export interface AiTradeExperienceInjection {
  tier: "full" | "brief" | "repeat";
  l0: ExperienceDisclosureItem[];
  l1: ExperienceDisclosureItem[];
  disputed: { a: string; b: string }[];
  suppressed: string[];
  totals: { lessons: number; events: number; sessions: number };
  maxChars: { l0: number; l1: number };
  graphAsOfTs: number;
}

/** 阈值面板（`GET|PUT /api/settings/experience`）。 */
export interface ExperienceSettings {
  /** 运维总闸——本端点上**只读**（可写会与启动弹窗那个复选框互相矛盾，P2-4）。 */
  enabled: boolean;
  defaultOn: boolean;
  writeEnabled: boolean;
  minSamples: number;
  ironSamples: number;
  tThreshold: number;
  halfLifeDays: number;
  staleDays: number;
  l0Max: number;
  l0MaxChars: number;
  l1Max: number;
  l1MaxChars: number;
  distillEnabled: boolean;
  /** ★ W-C · 效用闭环的三项可写阈值（桥表 12 → 15 项）。 */
  utilityEnabled: boolean;
  utilityMinSamples: number;
  utilityTThreshold: number;
  /** 服务端下发的下限，使 UI 能在提交前就说清楚为什么不合法（E-7）。 */
  l0MinChars: number;
  l1MinChars: number;
}

/** 可写子集（`enabled` 不在其中）。 */
export type ExperienceSettingsPatch = Partial<Omit<
  ExperienceSettings, "enabled" | "l0MinChars" | "l1MinChars"
>>;

/** 图谱 / 列表共用的筛选条件。 */
export interface ExperienceFilters {
  instId?: string;
  bar?: string;
  axis?: string;
  tier?: string;
  source?: string;
  q?: string;
}

/** 手动重建回执。 */
export interface ExperienceRebuildResult {
  nodes: number;
  edges: number;
  stale: number;
  unmuted: number;
  inserted: number;
  updated: number;
  events: number;
  elapsedMs: number;
  torn: boolean;
  /** ★ W-B · 这一次走的是哪条路；`fullReason` 说明为什么。 */
  mode?: "full" | "incremental";
  fullReason?: string | null;
  pendingEvents?: number;
}

/** 导入回执（边与事件恒被拒——它们是派生物 / 带着别人的成本口径）。 */
export interface ExperienceImportResult {
  imported: { nodes: number; edges: number; events: number };
  rejected: { nodes: number; edges: number; events: number };
  /** 文件内部按「同一句话 + 同一语境」判定为重复而被合并的条数（**绝不静默合并**）。 */
  deduped: number;
}
