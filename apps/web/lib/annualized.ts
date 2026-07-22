// 预计年化收益纯函数（ai-trade-stats-two-column-and-kline §3.3）。
//
// 全部无副作用、绝不抛：据「全会话已实现收益率 + 真实经历时长 + 完整交易笔数」把当前收益速度**线性外推**
// 为年化收益率。短样本外推天然放大（YEAR/elapsed 在 12h 即达 730×），故 P1-2 定死诚实护栏——
// 最小样本门禁（默认 ≥6h 且 ≥3 笔）不满足即显式降级、不出数字，绝不对极短样本给天文数字
// （CLAUDE.md「绝不伪造数据 / 显式降级」）。口径 = **仅已实现收益**，与时间维度筛选解耦（P2-5，见调用点）。

export type AnnualizedReason = "no-elapsed" | "too-short" | "too-few-trades" | "wipeout";

export interface AnnualizedProjection {
  ok: boolean; // false → 样本不足 / 无法估算，不出数字
  annualizedPct: number | null; // 线性外推年化收益率（小数）；ok=false 时为 null
  elapsedSec: number; // 真实经历时长（透传，供卡片展示样本基数）
  tradeCount: number; // 完整交易往返数（透传，供卡片展示样本基数）
  reason?: AnnualizedReason; // 降级原因（ok=false 时给出）
}

const YEAR_SECONDS = 365 * 86_400;
export const MIN_ELAPSED_SEC = 6 * 3600; // 默认门禁：≥6h（可调，调整须在 .claude-index/config.md 记录）
export const MIN_TRADES = 3; // 默认门禁：≥3 笔完整交易

/**
 * 线性外推年化收益率（非复利爆炸），带最小样本门禁与安全降级。
 *
 * @param params.returnPct 全会话已实现收益率（小数，如 0.05 表示 +5%）
 * @param params.elapsedSec 真实经历时长（秒）；live = now-startedTs，history = endedTs-startedTs
 * @param params.tradeCount 全会话完整往返数
 * @param params.minElapsedSec 最小经历时长门禁（默认 MIN_ELAPSED_SEC）
 * @param params.minTrades 最小交易笔数门禁（默认 MIN_TRADES）
 * @returns AnnualizedProjection；样本不足 / elapsed<=0 / 本金归零则 ok=false 并给出 reason，绝不抛
 */
export function projectAnnualizedReturn(params: {
  returnPct: number;
  elapsedSec: number;
  tradeCount: number;
  minElapsedSec?: number;
  minTrades?: number;
}): AnnualizedProjection {
  const { returnPct, elapsedSec, tradeCount } = params;
  const minElapsed = params.minElapsedSec ?? MIN_ELAPSED_SEC;
  const minTrades = params.minTrades ?? MIN_TRADES;

  if (!(elapsedSec > 0)) {
    return { ok: false, annualizedPct: null, elapsedSec, tradeCount, reason: "no-elapsed" };
  }
  if (elapsedSec < minElapsed) {
    return { ok: false, annualizedPct: null, elapsedSec, tradeCount, reason: "too-short" };
  }
  if (tradeCount < minTrades) {
    return { ok: false, annualizedPct: null, elapsedSec, tradeCount, reason: "too-few-trades" };
  }
  // 本金已归零 / 为负：年化外推无意义（避免把 -100% 放大成天文负值误导）。
  if (1 + returnPct <= 0) {
    return { ok: false, annualizedPct: null, elapsedSec, tradeCount, reason: "wipeout" };
  }

  // 线性外推：按当前已实现收益速度持续一年（非复利，避免短样本指数爆炸）。
  const annualizedPct = returnPct * (YEAR_SECONDS / elapsedSec);
  return { ok: true, annualizedPct, elapsedSec, tradeCount };
}

export type AnnualizedConfidence = "low" | "medium" | "high";

const DAY_SECONDS = 86_400;
const CONFIDENCE_ORDER: Record<AnnualizedConfidence, number> = { low: 0, medium: 1, high: 2 };

/**
 * 年化外推的样本可信度分档（纯函数，绝不抛）：据经历时长与完整交易笔数各自分档，取两者的较低者，
 * 避免任一维度（时长或笔数）虚高。阈值为经验值（可调，调整须在 .claude-index/config.md 记录）。
 *
 * high：≥30 天 且 ≥30 笔；medium：≥7 天 且 ≥10 笔；否则 low。仅影响提示语，不改任何年化数值口径。
 *
 * @param elapsedSec 真实经历时长（秒）
 * @param tradeCount 完整交易往返数
 */
export function annualizedConfidence(elapsedSec: number, tradeCount: number): AnnualizedConfidence {
  const days = elapsedSec / DAY_SECONDS;
  const timeTier: AnnualizedConfidence = days >= 30 ? "high" : days >= 7 ? "medium" : "low";
  const tradeTier: AnnualizedConfidence = tradeCount >= 30 ? "high" : tradeCount >= 10 ? "medium" : "low";
  return CONFIDENCE_ORDER[timeTier] <= CONFIDENCE_ORDER[tradeTier] ? timeTier : tradeTier;
}
