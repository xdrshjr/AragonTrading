// 执行周期趋势基调的中文化**纯函数**（ai-trade-execution-trend-verdict §3.7.4-8）。
//
// trace 行（`AiTradeTraceRound`）与统计卡（`TrendAlignmentCard`）共用这一份：两处各写一遍
// 中文化，下一次改判据时必然分叉，而它们会并排出现在同一屏上。
//
// 三条纪律：
// 1. **判据只有后端一份**。本模块只把已经下发的枚举与数字翻成中文，**绝不**重算 direction /
//    strength / conflict——重算一次就是造出第二份会漂移的真源。
// 2. **绝不臆造数字**。`score` / `execScore` 为 null（降级轮）时写「不可用」，不写 0。
// 3. **样本不足不下结论**。低于后端下发的 `minSamples` 时写「样本 a/b，尚不足以下结论」，
//    绝不把 0/0 画成百分比（沿用开仓漏斗与信源先验那两轮的成文口径）。
import type {
  AiTradeTrendAlignment,
  AiTradeTrendStats,
  AiTradeTrendVerdict,
} from "./aiTradeTypes";

const DIRECTION_TEXT: Record<string, string> = {
  up: "偏多",
  down: "偏空",
  range: "中性",
  unknown: "不可用",
};

const STRENGTH_TEXT: Record<string, string> = {
  strong: "强",
  moderate: "中",
  weak: "弱",
};

const CONFLICT_TEXT: Record<string, string> = {
  none: "",
  htf_conflict: "与高周期背景冲突",
  tactical_conflict: "与战术周期背离",
  both: "与高周期背景冲突、且战术周期背离",
};

const ALIGNMENT_TEXT: Record<AiTradeTrendAlignment, string> = {
  aligned: "顺势",
  counter: "逆势",
  neutral: "无基调",
};

/** 顺势 / 逆势 / 归属不明三格的中文名（统计卡与 trace 行共用）。 */
export const TREND_BUCKET_LABEL: Record<string, string> = {
  aligned: "顺势",
  counter: "逆势",
  unknown: "归属不明",
};

/** 基调分布四格的中文名。 */
export const TREND_DIRECTION_LABEL: Record<string, string> = {
  up: "偏多",
  down: "偏空",
  range: "中性",
  unknown: "不可用",
};

/** 语义色调：偏多=多头色、偏空=空头色，其余中性。**不表态的态绝不借用方向色**。 */
export function trendVerdictTone(
  verdict: AiTradeTrendVerdict | null | undefined,
): "long" | "short" | "muted" {
  if (!verdict || verdict.degraded) return "muted";
  if (verdict.bias === "long") return "long";
  if (verdict.bias === "short") return "short";
  return "muted";
}

function fmtScore(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return "不可用";
  return value > 0 ? `+${value.toFixed(2)}` : value.toFixed(2);
}

/**
 * 一句话描述本轮基调（trace 行的首行）。
 *
 * 降级轮**不含任何方向词**——「算不出来」与「中性」是两个不同的结论，混起来会让用户以为
 * 系统看多空各半，而实情是它根本没有足够的成分去判。
 */
export function describeTrendVerdict(
  verdict: AiTradeTrendVerdict | null | undefined,
): string {
  if (!verdict) return "";
  const bar = verdict.bar || "执行周期";
  if (verdict.degraded) {
    const missing = missingLabels(verdict);
    const tail = missing.length ? `（成分缺失：${missing.join("、")}）` : "";
    return `本轮趋势基调 · ${bar}：不可用${tail}，本轮不给方向基调`;
  }
  const direction = DIRECTION_TEXT[verdict.direction] ?? "不可用";
  const strength = STRENGTH_TEXT[verdict.strength] ?? "—";
  const conflict = CONFLICT_TEXT[verdict.conflict] ?? "";
  const conflictTail = conflict ? `；${conflict}（只收紧、不放松）` : "";
  return (
    `本轮趋势基调 · ${bar}：${direction}（强度=${strength}）` +
    `｜执行周期分 ${fmtScore(verdict.execScore)}（决定强度，只由 ${bar} 证据算出）` +
    `｜合成分 ${fmtScore(verdict.score)}（含高周期调制，决定方向）${conflictTail}`
  );
}

/** 缺失成分的中文标签（取自 wire 自带的 label，**绝不**在前端另抄一份表）。 */
export function missingLabels(
  verdict: AiTradeTrendVerdict | null | undefined,
): string[] {
  if (!verdict) return [];
  const missing = new Set(verdict.missing ?? []);
  return (verdict.components ?? [])
    .filter((c) => missing.has(c.key))
    .map((c) => c.label || c.key);
}

/**
 * 高周期背景与本级别基调的关系。
 *
 * 措辞必须把那条硬边界写出来：高周期**可以削弱**基调的强度，但**不能反转**它的方向——这正是
 * 用户诉求「综合各级别去看，但判在我所选的级别」的可执行译文。
 */
export function describeTrendAlignment(
  verdict: AiTradeTrendVerdict | null | undefined,
): string {
  if (!verdict) return "";
  const htf = verdict.higherTimeframe;
  if (!htf || !htf.status) return "高周期背景不可用，本轮不作调制";
  const bars = [...(htf.bullishBars ?? []), ...(htf.bearishBars ?? [])];
  const barsTail = bars.length ? `（${bars.join("/")}）` : "";
  const bar = verdict.bar || "执行周期";
  if (verdict.conflict === "htf_conflict" || verdict.conflict === "both") {
    return `高周期整体=${htf.status}${barsTail}，与 ${bar} 基调冲突；它不能反转本级别的方向`;
  }
  if (htf.agree === true) return `高周期整体=${htf.status}${barsTail}，与本级别基调一致`;
  return `高周期整体=${htf.status}${barsTail}，对本级别基调不表态`;
}

/** 一次动作与基调的关系（`tool_call` 相位的基调闸行）。 */
export function describeTrendGateAlignment(alignment: AiTradeTrendAlignment): string {
  return ALIGNMENT_TEXT[alignment] ?? "无基调";
}

/**
 * 统计卡的「成绩」行判据：样本是否够下结论。
 *
 * 分母是**顺势 + 逆势**的成交笔数——`unknown` 单列、不进分母也不进任何占比（与开仓漏斗的
 * `direction_verdict` 同一条口径）。
 */
export function trendSampleText(stats: AiTradeTrendStats | null | undefined): string | null {
  if (!stats) return null;
  const directed = (stats.filled?.aligned ?? 0) + (stats.filled?.counter ?? 0);
  const need = Math.max(1, stats.minSamples || 1);
  if (directed >= need) return null;
  return `样本 ${directed}/${need}，尚不足以下结论`;
}
