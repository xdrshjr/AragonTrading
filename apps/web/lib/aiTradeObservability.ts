// AI 自动交易运行观测派生纯库（ai-trade-hold-inertia-observability O3）。
// 与后端 session._compute_ai_stats / _REFLECTION_FAIL_CLOSED_REASONS **逐字镜像**的口径（勿单侧改动，
// 后端源文件在集合定义处有互指注释）：
// - aborts 只计模型真实否决（verdict==="abort" 且 degradeReason==="reflection_abort"，R-04 分流）；
// - failClosed 计四个基础设施码，且判定**独立于 reflectionVerdict 是否存在**——镜像后端分支位置：
//   该计数在 `if verdict is not None` 块之外（R2-02，绝不可嵌进 verdict 非空分支）。注意今日
//   fail-closed 轮实际带 verdict="abort"（decider._fail_closed 构造），故同时计入 runs——两条
//   均由 aiTradeObservability.test.ts 的字面量用例钉死；
// - 计数窗口 = decision 环形 ≤50 条（AI_TRADE_DECISIONS_MAX），与后端 _decisions 同窗同口径。
// 三个导出函数均为无副作用纯函数、不改入参。
import type { AiTradeHistoryMember } from "./aiTradeHistoryTypes";
import type { AiTradeDecision } from "./aiTradeTypes";

// 反思 fail-closed 的四个基础设施码（与后端 _REFLECTION_FAIL_CLOSED_REASONS 逐字相等，测试字面量钉死）。
export const REFLECTION_FAIL_CLOSED_REASONS: ReadonlySet<string> = new Set([
  "reflection_timeout",
  "reflection_llm_error",
  "reflection_no_tool",
  "reflection_invalid",
]);

// live 反思计数（近 ≤50 轮样本口径，非全程累计——全程口径见历史详情的 aiStats）。
export interface ReflectionCounts {
  runs: number;
  confirms: number;
  aborts: number;
  failClosed: number;
}

export function computeReflectionCounts(decisions: AiTradeDecision[]): ReflectionCounts {
  const counts: ReflectionCounts = { runs: 0, confirms: 0, aborts: 0, failClosed: 0 };
  for (const d of decisions) {
    const verdict = d.reflectionVerdict;
    const degradeReason = d.degradeReason;
    if (verdict != null) {
      counts.runs += 1;
      if (verdict === "confirm") counts.confirms += 1;
      else if (verdict === "abort" && degradeReason === "reflection_abort") counts.aborts += 1;
    }
    // 镜像后端：failClosed 判定位于 verdict 非空分支**之外**（R2-02，勿内嵌）。
    if (degradeReason != null && REFLECTION_FAIL_CLOSED_REASONS.has(degradeReason)) {
      counts.failClosed += 1;
    }
  }
  return counts;
}

// 历史详情「全成员合计」：五个计数键求和（缺键按 0）、maxHoldStreak 取最大值；
// 没有任何成员带任何新键（老会话记录）→ null（呈现层据此隐藏整行，不显示一排误导性的 0）。
export interface AiObservability {
  reflectionRuns: number;
  reflectionConfirms: number;
  reflectionAborts: number;
  reflectionFailClosed: number;
  wakeSkips: number;
  maxHoldStreak: number;
}

const SUM_KEYS = [
  "reflectionRuns",
  "reflectionConfirms",
  "reflectionAborts",
  "reflectionFailClosed",
  "wakeSkips",
] as const;

export function aggregateAiObservability(members: AiTradeHistoryMember[]): AiObservability | null {
  const agg: AiObservability = {
    reflectionRuns: 0,
    reflectionConfirms: 0,
    reflectionAborts: 0,
    reflectionFailClosed: 0,
    wakeSkips: 0,
    maxHoldStreak: 0,
  };
  let hasAnyKey = false;
  for (const member of members) {
    const stats = member.metrics.aiStats;
    if (!stats) continue;
    for (const key of SUM_KEYS) {
      const value = stats[key];
      if (typeof value === "number") {
        hasAnyKey = true;
        agg[key] += value;
      }
    }
    if (typeof stats.maxHoldStreak === "number") {
      hasAnyKey = true;
      agg.maxHoldStreak = Math.max(agg.maxHoldStreak, stats.maxHoldStreak);
    }
  }
  return hasAnyKey ? agg : null;
}

// live 连续观望取值：decision 环形自尾向头首个带 holdStreak 的条目（最新真源；跳过轮事件无此键）
// → 回退 session.holdStreak（snapshot/status 合并值）→ null（老后端 / WS 未连上，呈现「—」）。
export function latestHoldStreak(
  decisions: AiTradeDecision[],
  session?: { holdStreak?: number } | null,
): number | null {
  for (let i = decisions.length - 1; i >= 0; i -= 1) {
    const value = decisions[i]?.holdStreak;
    if (typeof value === "number") return value;
  }
  const fallback = session?.holdStreak;
  return typeof fallback === "number" ? fallback : null;
}
