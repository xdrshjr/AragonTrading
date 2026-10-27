// 交易日志的 wire 类型（ai-trade-audit-journal §3.3）。与后端 `api/schemas_journal.py`
// 与 `journal/service.py` 的装配一一对应。
//
// ⚠️ 门禁层名**只从 wire 来**：`JournalGateRow.label` 由服务端按 `open_funnel.LAYER_LABELS`
// 下发。本文件（以及 `lib/journal*.ts` 与 `components/Journal/**`）里**一个层名字面量都不许有**
// ——在前端补一张表就是本仓明令禁止的第二份真源，而它漂移时不报任何错。
// 由源码扫描用例 `components/Journal/journalNoLayerLabels.source.test.ts` 冻结。

/** 关联置信度：`exact` 绿 ✓ / `heuristic` 琥珀 ≈ / `none` 灰 —（形状 + 文案 + 颜色三者齐全）。 */
export type LinkConfidence = "exact" | "heuristic" | "none";

/** 采集粒度。`minimal` 档不含模型输入 / 输出，故后训练导出被禁用。 */
export type CaptureTier = "minimal" | "standard" | "full";

export type ExportFormat = "audit-csv" | "audit-jsonl" | "sft-jsonl";

/** 上下文重建模式。`full` = 整段多轮对话；`lastTurnOnly` = 只要最后一轮（样本会被打标）。 */
export type ContextMode = "full" | "lastTurnOnly";

export interface JournalSession {
  groupId: string;
  status: string;
  instIds: string[];
  runIds: number[];
  bar: string;
  startedTs: number;
  endedTs: number | null;
  decisionRows: number;
  eventRows: number;
}

export interface JournalSessionsPage {
  items: JournalSession[];
  nextCursorTs: number | null;
}

/** 列表页的 17 键摘要。`linkConfidence` 由服务端读时关联给出，前端不重算。 */
export interface DecisionBrief {
  id: number;
  groupId: string;
  runId: number;
  instId: string;
  bar: string;
  roundNo: number;
  ts: number;
  wallTs: number;
  trigger: string;
  action: string;
  finalAction: string;
  sizeFraction: number | null;
  confidence: number | null;
  degradeReason: string | null;
  reflectionVerdict: string | null;
  executed: boolean;
  linkConfidence: LinkConfidence | null;
}

export interface DecisionsPage {
  items: DecisionBrief[];
  nextBeforeId: number | null;
  /** 恒为 `null`：列表页不做 COUNT（十万行上那是一次全扫）。 */
  total: null;
}

/** 门禁链的一行。四态：`armed === null` ⇒ 未知（**不是**未装配）。 */
export interface JournalGateRow {
  key: string;
  /** 中文层名，**随 wire 下发**（真源 `open_funnel.LAYER_LABELS`）。 */
  label: string;
  armed: boolean | null;
  verdict: "pass" | "veto" | "skip";
  readings?: Record<string, unknown>;
}

export interface JournalFillLeg {
  side?: string;
  px?: number | null;
  sz?: number | null;
  fee?: number | null;
  pnl?: number | null;
  clOrdId?: string;
  ordId?: string;
  reason?: string;
  source?: "round" | "post_round";
}

export interface JournalFillRef {
  traded: boolean;
  skipped?: boolean;
  action?: string | null;
  side?: string | null;
  qty?: number | null;
  positionAfter?: number | null;
  reason?: string | null;
  closeBlocked?: string | null;
  legs: JournalFillLeg[];
}

export interface JournalTrip {
  exitId?: number;
  exitTs?: number;
  exitPx?: number | null;
  pnl?: number | null;
  fee?: number | null;
  exitReason?: string | null;
  realizedBps?: number;
  rMultiple?: number;
  roundTripFeeUsd?: number;
  exitSource?: string;
  holdSeconds?: number;
}

export interface JournalOrderReceipt {
  id: number;
  instId: string;
  direction: string;
  ordType: string;
  px: number | null;
  sz: number;
  status: string;
  clOrdId: string;
  ordId: string;
  errorCode: string;
  createdTs: number;
}

export interface JournalOutcome {
  linkConfidence: LinkConfidence;
  fill: Record<string, unknown> | null;
  trip: JournalTrip | null;
  order: JournalOrderReceipt | null;
  noFillReason: string | null;
  fillSource: string | null;
}

export interface JournalTelemetry {
  provider?: string;
  model?: string;
  inputTokens?: number;
  outputTokens?: number;
  latencyMs?: number;
  retries?: number;
  reasoningDepth?: string;
  captureTier?: CaptureTier;
  reasoning?: Record<string, unknown>;
}

/** 提示词块：`stored` 有原文；`rebuildable` 要点按钮重建；`none` 是 minimal 档。 */
export type JournalPromptBlock =
  | { source: "stored"; text: string }
  | { source: "rebuildable"; storedSha: string }
  | { source: "none" };

export interface DecisionDetail {
  brief: DecisionBrief;
  gates: JournalGateRow[];
  vetoedBy: string | null;
  payload: Record<string, unknown> | null;
  payloadTruncated: boolean;
  output: Record<string, unknown> | null;
  analysis: string | null;
  reflection: string | null;
  thinking: string | null;
  prompt: JournalPromptBlock;
  telemetry: JournalTelemetry;
  contextRef: Record<string, unknown> | null;
  fillRef: JournalFillRef | null;
  reason: string | null;
  ordType: string | null;
  limitPx: number | null;
  captureTier: CaptureTier;
  outcome: JournalOutcome;
}

export interface PromptRebuild {
  text: string;
  shaMatch: boolean;
  storedSha: string;
  renderedSha: string;
  source: "stored" | "rendered";
}

export interface JournalEvent {
  id: number;
  groupId: string;
  runId: number | null;
  instId: string;
  ts: number;
  wallTs: number;
  level: "info" | "warn" | "error";
  category: string;
  code: string;
  message: string;
  detail: Record<string, unknown> | null;
}

export interface JournalEventsPage {
  items: JournalEvent[];
  nextBeforeId: number | null;
}

export interface JournalFillRow {
  decisionRef: { id: number; roundNo: number; linkConfidence: LinkConfidence };
  groupId: string;
  runId: number;
  instId: string;
  ts: number;
  wallTs: number;
  action: string;
  clOrdId: string;
  ordId: string;
  fill: Record<string, unknown> | null;
  trip: JournalTrip | null;
  fillSource: string | null;
  legs: JournalFillLeg[];
}

export interface JournalFillsPage {
  items: JournalFillRow[];
  nextBeforeId: number | null;
}

/** 采集健康度 + 全部口径读数。前端的每一个数字都来自这里，一个都不硬编码。 */
export interface JournalStats {
  enabled: boolean;
  captureTier: CaptureTier;
  contextChainEnabled: boolean;
  queue: { depth: number; max: number; oldestPendingMs: number };
  counters: {
    enqueued: number;
    written: number;
    dropped: number;
    droppedDecisions: number;
    droppedEvents: number;
    shedInfoEvents: number;
    writeErrors: number;
    serializeErrors: number;
    droppedOnShutdown: number;
  };
  tables: { decisions: number; events: number; promptProfiles: number; approxBytes: number };
  retention: { days: number; maxRows: number };
  limits: {
    textMaxChars: number;
    payloadMaxChars: number;
    exportMaxRows: number;
    exportPageSize: number;
    exportTimeBudgetS: number;
    eventMinLevel: string;
  };
}

/** 九个 skipped 桶。`contextIncomplete` 必须与别的桶分开显示（见 §2.5.2 的理由）。 */
export interface SkippedBuckets {
  gateVetoed: number;
  degraded: number;
  unsettled: number;
  hold: number;
  lowConfidence: number;
  promptDrift: number;
  payloadTruncated: number;
  minimalTier: number;
  contextIncomplete: number;
}

export interface ExportPreview {
  rows: number;
  approxBytes: number;
  overLimit: boolean;
  estimatedSeconds: number;
  /** 分片数。> 1 时导出中心按钮写明片数，并**顺序**触发 N 次下载。 */
  slices: number;
  /**
   * 第 2..N 片的 `beforeId` 游标（**由服务端精确算出，不是估的**）。
   *
   * **没有它，分片就是假的**：浏览器导航式的下载读不回 `eof.nextBeforeId`，前端只能一片片
   * 从头拉，最后得到 N 个一模一样的文件——比不分片更坏。长度为 `slices - 1`
   * （行数不够切时更短，届时前端只发它真的拿得到游标的那几片）。
   */
  sliceCursors: number[];
  timeBudgetS: number;
  /** `minLinkConfidence=auto` 的服务端解出值，前端**照抄不重算**。 */
  resolvedMinLinkConfidence: LinkConfidence;
  resolvedReason: string;
  skipped: SkippedBuckets;
}

/** 后训练导出的八条筛选纪律（默认值即「能直接拿去训练」的口径）。 */
export interface SftOptions {
  excludeGateVetoed: boolean;
  excludeDegraded: boolean;
  onlySettled: boolean;
  includeHold: boolean;
  minLinkConfidence: LinkConfidence | "auto";
  contextMode: ContextMode;
}

/** 列表 / 导出共用的过滤条件（持久化进 store 的 `journalFilters` 切片）。 */
export interface JournalFilters {
  groupId?: string;
  instId?: string;
  bar?: string;
  sinceTs?: number;
  untilTs?: number;
  action?: string;
  trigger?: string;
  executedOnly?: boolean;
  degradedOnly?: boolean;
  gateVetoedOnly?: boolean;
  q?: string;
}

export interface PruneResult {
  wouldDelete: { decisions: number; events: number } | null;
  deleted: { decisions: number; events: number } | null;
  protectedGroups: string[];
  prunedTs: number;
}
