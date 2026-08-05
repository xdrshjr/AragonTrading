// 挖掘层纯函数（strategy-factor-discovery §2.13）：WS reducer + 漏斗 / 来源 / 覆盖派生 +
// 拒绝原因中文映射。零副作用、零依赖，可单测。
//
// ⚠️ 仓库既知坑（R-11）：store 里若**逐键手抄** WS 字段，后端新增的键会被静默吞掉——
// 后端算了、类型补了，用户就是看不见。故本文件的 `applyDiscoveryEvent` 一律走
// `{...prev, ...patch}` 的**整对象合并**，并由 discovery.test.ts 的字段往返用例守门。

import type {
  DiscoveryCandidateRow,
  DiscoveryIterationRow,
  DiscoveryRunState,
  DiscoveryStateEvent,
  DiscoveryWsEvent,
  SourceBudget,
} from "./discoveryTypes";

export const ITERATIONS_CAP = 200;
export const CANDIDATES_CAP = 300;

export const DEFAULT_BUDGET: SourceBudget = {
  mutate: 0.25,
  crossover: 0.25,
  perturb: 0.15,
  random: 0.15,
  llm: 0.2,
};

export const SOURCE_LABEL: Record<string, string> = {
  seed: "种子",
  random: "随机探索",
  mutate: "变异",
  crossover: "交叉",
  perturb: "参数扰动",
  llm: "LLM 机制引导",
};

/** 11 条因子闸 + 九条审查规则的中文说明（UI 唯一映射；未登记码原样显示，绝不吞成空白）。 */
export const REJECT_REASON_CN: Record<string, string> = {
  F1: "覆盖率或样本量不足",
  F2: "样本内 RankIC 太弱",
  F3: "样本内 IR 未达门槛",
  F4: "分段 t 值不显著",
  F5: "分段 RankIC 同号稳定性不足",
  F6: "样本外反号或强度衰减过大",
  F7: "分位单调性不足",
  F8: "成本后 top-bottom 价差不足",
  F9: "换手代理过高（疑似高频噪声）",
  F10: "与已入库因子高度相关",
  F11: "多重检验惩罚后门槛未达",
  degenerate_outer: "外层算子退化",
  homogeneous_simplify: "可被恒等化简",
  dimension_mismatch: "跨量纲运算",
  complexity_out_of_range: "复杂度越界",
  duplicate: "已测过同一表达式",
  fsa_banned: "结构骨架已被冻结",
  variant_cap: "同骨架变体数达上限",
  skeleton_blacklisted: "该骨架屡试不中",
  constant_or_empty: "近乎常数或有效值过少",
  validate_timeout: "单候选验证超时",
};

export const PAUSE_REASON_CN: Record<string, string> = {
  user: "已手动暂停",
  process_restart: "进程重启后待恢复",
  disabled_by_config: "挖掘总闸已关闭",
  data_unavailable: "行情数据暂不可达",
  limit_reached: "已达设定上限",
  rebuild_failed: "候选池重建失败",
};

export const STATUS_CN: Record<string, string> = {
  running: "挖掘中",
  paused: "已暂停",
  stopped: "已停止",
  succeeded: "已完成",
  failed: "已失败",
};

export function rejectReasonCn(code: string): string {
  if (!code) return "";
  if (code.startsWith("llm_review:")) {
    const detail = code.slice("llm_review:".length).trim();
    return detail ? `LLM 精判否决：${detail}` : "LLM 精判否决";
  }
  if (code.startsWith("eval_error:")) return `求值异常（${code.slice("eval_error:".length)}）`;
  return REJECT_REASON_CN[code] ?? code;
}

export function pauseReasonCn(code: string): string {
  return code ? PAUSE_REASON_CN[code] ?? code : "";
}

export function emptyRunState(runId: number): DiscoveryRunState {
  return {
    runId,
    status: "running",
    pauseReason: "",
    iteration: 0,
    counters: { tested: 0, kept: 0, rejected: {} },
    budget: { ...DEFAULT_BUDGET },
    poolSizes: {},
    mechanismCoverage: {},
    fsaBans: [],
    nextIterationTs: null,
    dataWindow: { startTs: 0, endTs: 0, lastRefreshTs: 0 },
    llmUsage: { callsThisHour: 0, limit: 0, sinceProcessStart: true, enabled: false },
    rebuilding: false,
    keptCount: 0,
    testedCount: 0,
    iterations: [],
    candidates: [],
    connected: false,
    lastError: null,
  };
}

/** 环形追加：超出上限即丢最旧（内存有界，长跑不涨，DoD #7）。 */
function ring<T>(list: T[], item: T, cap: number): T[] {
  const next = [...list, item];
  return next.length > cap ? next.slice(next.length - cap) : next;
}

function upsertIteration(
  list: DiscoveryIterationRow[],
  row: DiscoveryIterationRow,
): DiscoveryIterationRow[] {
  const at = list.findIndex((item) => item.index === row.index);
  if (at >= 0) {
    const next = [...list];
    next[at] = { ...next[at], ...row };
    return next;
  }
  return ring(list, row, ITERATIONS_CAP);
}

function upsertCandidate(
  list: DiscoveryCandidateRow[],
  row: DiscoveryCandidateRow,
): DiscoveryCandidateRow[] {
  const at = list.findIndex((item) => item.genomeHash === row.genomeHash);
  if (at >= 0) {
    const next = [...list];
    next[at] = { ...next[at], ...row };
    return next;
  }
  return ring(list, row, CANDIDATES_CAP);
}

/**
 * WS 事件 → 运行态（纯 reducer，幂等且对乱序安全）。
 *
 * `state` 走**整对象合并**而非逐键手抄：后端新增字段自动穿透到 store，杜绝
 * 「新键被静默吞掉」这类查不出来的缺陷（R-11）。
 */
export function applyDiscoveryEvent(
  prev: DiscoveryRunState | null,
  event: DiscoveryWsEvent,
): DiscoveryRunState | null {
  if (!prev) return prev;
  switch (event.type) {
    case "state": {
      const patch = event.data as Partial<DiscoveryStateEvent>;
      return { ...prev, ...patch, connected: true };
    }
    case "iteration":
      return { ...prev, iterations: upsertIteration(prev.iterations, event.data) };
    case "candidate":
      return { ...prev, candidates: upsertCandidate(prev.candidates, event.data) };
    case "demoted":
      return {
        ...prev,
        candidates: prev.candidates.map((row) =>
          row.id === event.data.candidateId ? { ...row, demoted: true } : row,
        ),
      };
    case "error":
      return { ...prev, lastError: event.data };
    case "done":
      return { ...prev, status: event.data.status, nextIterationTs: null };
    default:
      return prev;
  }
}

// ============================ 派生 ============================
export interface FunnelStage {
  key: string;
  label: string;
  value: number;
}

/** 漏斗：生成 → 审出 → 验证 → 入库（逐段数字，各段之和守恒）。 */
export function funnelOf(row: DiscoveryIterationRow | null): FunnelStage[] {
  const generated = row?.generated ?? 0;
  const surviving = generated - (row?.ruleRejected ?? 0) - (row?.llmRejected ?? 0);
  return [
    { key: "generated", label: "生成", value: generated },
    { key: "reviewed", label: "审出", value: Math.max(0, surviving) },
    { key: "validated", label: "验证", value: row?.validated ?? 0 },
    { key: "kept", label: "入库", value: row?.kept ?? 0 },
  ];
}

/** 拒绝原因 Top N（中文化 + 计数，降序）。 */
export function topRejectReasons(
  counts: Record<string, number> | undefined,
  limit = 5,
): { code: string; label: string; count: number }[] {
  return Object.entries(counts ?? {})
    .filter(([, count]) => Number(count) > 0)
    .sort((a, b) => Number(b[1]) - Number(a[1]))
    .slice(0, limit)
    .map(([code, count]) => ({ code, label: rejectReasonCn(code), count: Number(count) }));
}

/** 来源分布（五维堆叠柱的输入；缺席来源补 0 以保证柱宽稳定）。 */
export function sourceBreakdown(
  row: DiscoveryIterationRow | null,
): { key: string; label: string; value: number }[] {
  const by = row?.bySource ?? {};
  return Object.keys(SOURCE_LABEL).map((key) => ({
    key,
    label: SOURCE_LABEL[key],
    value: Number(by[key] ?? 0),
  }));
}

/**
 * 入库率。
 *
 * 样本不足（已测 < `minSamples`）时返回 `null` —— UI 据此显示灰底「—」而不是一个
 * 由 3 个样本外推出来的百分比（诚实优先于漂亮，§2.13）。
 */
export function keptRate(tested: number, kept: number, minSamples = 20): number | null {
  if (!Number.isFinite(tested) || tested < minSamples) return null;
  return kept / tested;
}

/** 入库率 sparkline 序列（逐轮，样本不足的轮次给 null 断点，绝不补 0）。 */
export function keptRateSeries(rows: DiscoveryIterationRow[]): (number | null)[] {
  return rows.map((row) => (row.validated > 0 ? row.kept / row.validated : null));
}

/** 机制覆盖（13 族全集，按覆盖数降序；0 覆盖的排最后，供「优先探索方向」提示）。 */
export function coverageEntries(
  coverage: Record<string, number>,
): { key: string; count: number }[] {
  return Object.entries(coverage ?? {})
    .map(([key, count]) => ({ key, count: Number(count) || 0 }))
    .sort((a, b) => b.count - a.count || a.key.localeCompare(b.key));
}

/** 下一轮倒计时（秒）；无排期或已过点返回 null（UI 显示「即将开始」而不是负数）。 */
export function countdownSeconds(nextTs: number | null, nowMs = Date.now()): number | null {
  if (!nextTs) return null;
  const seconds = Math.round(nextTs - nowMs / 1000);
  return seconds > 0 ? seconds : null;
}

/** 运行时长（秒）。 */
export function runtimeSeconds(startedTs: number, nowMs = Date.now()): number {
  return Math.max(0, Math.round(nowMs / 1000 - startedTs));
}

export function formatDuration(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds < 0) return "—";
  const days = Math.floor(seconds / 86400);
  const hours = Math.floor((seconds % 86400) / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  if (days > 0) return `${days} 天 ${hours} 小时`;
  if (hours > 0) return `${hours} 小时 ${minutes} 分`;
  if (minutes > 0) return `${minutes} 分 ${seconds % 60} 秒`;
  return `${seconds} 秒`;
}

/** 工作量预估（候选数 × 窗口根数），启动器实时显示并与 413 护栏同口径。 */
export function workloadOf(candidatesPerIteration: number, windowBars: number): number {
  return Math.max(0, Math.floor(candidatesPerIteration)) * Math.max(0, Math.floor(windowBars));
}

/** 数据窗口大致覆盖天数（启动器提示，避免用户凭感觉填根数）。 */
export function windowDays(bar: string, windowBars: number): number {
  const seconds: Record<string, number> = {
    "1m": 60,
    "5m": 300,
    "15m": 900,
    "30m": 1800,
    "1H": 3600,
    "4H": 14400,
    "1D": 86400,
  };
  return ((seconds[bar] ?? 3600) * Math.max(0, windowBars)) / 86400;
}
