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

/** 静音原因：`user` 是人类意志（物化永不覆盖），另两个是自动的（新证据即解除，E-9）。 */
export type ExperienceMuteReason = "user" | "stale" | "flipped" | null;

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
  /** 事件数超过自动重建规模闸 ⇒ 自动重建已停止（E-6）。 */
  overflow: boolean;
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
  items: ExperienceNode[];
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
  /** **本会话**漏采的往返数（与概览条那个库存累计口径不同，两者都要有）。 */
  harvestGap: number;
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
}

/** 导入回执（边与事件恒被拒——它们是派生物 / 带着别人的成本口径）。 */
export interface ExperienceImportResult {
  imported: { nodes: number; edges: number; events: number };
  rejected: { nodes: number; edges: number; events: number };
  /** 文件内部按「同一句话 + 同一语境」判定为重复而被合并的条数（**绝不静默合并**）。 */
  deduped: number;
}
