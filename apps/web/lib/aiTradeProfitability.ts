// 跨会话盈利体检 wire 类型 + 纯格式化（ai-trade-realized-edge-calibration 轴 B/C · §5.1）。
//
// 两条呈现纪律（两张新卡与两处 `realized` 读点**共用**本文件，绝不各写一份口径）：
// - 任何一个比例 / 中位数类读数都必须与它自己的分母同屏出现——一个在 11% 子样本上算出来的成本
//   与一个在 100% 样本上算出来的胜率并排放着而不说明，本身就是误导；
// - 文案不得出现任何收益承诺 / 校准宣称类措辞（词表见 `aiTradeProfitability.test.ts` 的 G-10 扫描；
//   **此处刻意不逐字复述那几个词**——本文件自己就在扫描集里，写出来会把冻结门禁自己打红）。
//   这是一张历史统计，不是对未来的承诺。

export interface AiTradePooled {
  roundTrips: number;
  netPnlUsd: number | null;
  grossPnlUsd: number | null;
  feeUsd: number | null;
  winRate: number | null;
  avgWinUsd: number | null;
  avgLossUsd: number | null;
  payoff: number | null;
  breakevenPayoff: number | null;
  expectancyUsd: number | null;
  expectancyR: number | null;
  rSamples: number;
  tStat: number | null;
  medianHoldSeconds: number | null;
  medianMoveBps: number | null;
  medianCostBps: number | null;
  travelToCostRatio: number | null;
  feeCoverage: number | null;
  feeTrips: number;
  costBasisAssumedTrips: number;
}

export interface AiTradeRiskProfile {
  rSamples: number;
  rP10: number | null;
  rP50: number | null;
  rP90: number | null;
  reachedOneRShare: number | null;
  withinFifthRShare: number | null;
  plannedRrMedian: number | null;
  stopUtilizationP50: number | null;
  medianStopAtrMult: number | null;
  stopAtrCoverage: number | null;
}

export interface AiTradeSliceRow {
  key: string;
  trips: number;
  netPnlUsd: number | null;
  winRate: number | null;
  expectancyUsd: number | null;
  medianRMultiple: number | null;
}

export interface AiTradeCalibrationBlock {
  n: number;
  coverage: number | null;
  conclusive: boolean;
  medianEstEdgeBps?: number | null;
  medianRealizedGrossBps?: number | null;
  medianEstCostBps?: number | null;
  medianRealizedCostBps?: number | null;
  medianErrorBps?: number | null;
  signAgreementRate?: number | null;
  modelConstantBps?: number | null;
  note?: string | null;
  buckets?: AiTradeConfidenceBucket[];
  monotonic?: boolean | null;
}

export interface AiTradeConfidenceBucket {
  key: string;
  trips: number;
  winRate: number | null;
  expectancyUsd: number | null;
  medianRMultiple: number | null;
}

export interface AiTradeProfitabilityResponse {
  sampleTrips: number;
  minTrips: number;
  conclusive: boolean;
  costBasisAssumedTrips: number;
  pooled: AiTradePooled;
  risk: AiTradeRiskProfile;
  slices: Record<string, AiTradeSliceRow[]>;
  calibration: {
    edgeNumerator: AiTradeCalibrationBlock;
    costModel: AiTradeCalibrationBlock;
    confidenceReliability: AiTradeCalibrationBlock;
  };
  verdict: { code: string; title: string; detail: string };
}

// 会话级已实现读数（`aiStats.realized` 与 `costSnapshot.realized` **同形**——后端 `realized_stats()`
// 是唯一产物，两条通道同源，故此处一个接口描述两处）。
export interface AiTradeRealizedStats {
  trips: number;
  medianMoveBps: number | null;
  medianRealizedNetBps: number | null;
  medianRMultiple: number | null;
  // R 的分母不是 `trips`——无止损 / 合约乘数未知的往返算不出 R。老后端缺键 ⇒ 该行不带分母。
  rSamples?: number | null;
  reachedOneRShare: number | null;
  medianStopAtrMult: number | null;
  edgeErrorMedianBps: number | null;
  costErrorMedianBps: number | null;
  estimateCoverage: number | null;
  feeCoverage: number | null;
}

// 结论徽标的色调（`verdict.code` → tone）。文案一律取后端下发的 `title` / `detail`，前端不重写。
export const VERDICT_TONES: Record<string, "long" | "short" | "neutral" | "accent"> = {
  positive_significant: "long",
  positive_not_significant: "accent",
  no_edge_yet: "neutral",
  negative_significant: "short",
  insufficient_samples: "neutral",
};

// 会话级「已实现」三项的最小样本：少于它整段不渲染（绝不显示一个 1 笔样本的中位数）。
export const REALIZED_MIN_TRIPS = 3;

/** 算不出一律「—」，**绝不**显示 0（那是一个不同的结论）。 */
export function fmtOrDash(value: number | null | undefined, digits = 2): string {
  return value == null || !Number.isFinite(value) ? "—" : value.toFixed(digits);
}

export function fmtBps(value: number | null | undefined): string {
  return value == null || !Number.isFinite(value) ? "—" : `${value.toFixed(1)} bps`;
}

export function fmtRate(value: number | null | undefined): string {
  return value == null || !Number.isFinite(value) ? "—" : `${(value * 100).toFixed(1)}%`;
}

export function fmtUsd(value: number | null | undefined, digits = 4): string {
  return value == null || !Number.isFinite(value) ? "—" : `${value.toFixed(digits)} USDT`;
}

/**
 * 覆盖率一句话。**`coverage < 1` 时必须显示**（G-8）：一个在子样本上算出来的读数，若不把分母
 * 摆出来，读者会默认它是全样本结论。`coverage == null` 同样要说——「不知道覆盖率」也是信息。
 */
export function coverageText(n: number, coverage: number | null | undefined, total?: number): string | null {
  if (coverage != null && Number.isFinite(coverage) && coverage >= 1) return null;
  const denom = total != null && Number.isFinite(total) ? `${total}` : "全部";
  return `覆盖 ${n} / ${denom} 笔（${fmtRate(coverage)}）`;
}

/**
 * 赔率与盈亏平衡赔率的成对呈现。**单独一个 1.014 没有任何意义**——它只有和「该胜率下要求多少」
 * 并排才构成结论，故本函数一次给出两个数，调用点不得只取其一。
 */
export function payoffText(pooled: Pick<AiTradePooled, "payoff" | "breakevenPayoff">): string {
  return `${fmtOrDash(pooled.payoff, 3)} / 需 ${fmtOrDash(pooled.breakevenPayoff, 3)}`;
}

/**
 * 「位移 / 成本」一行：这个比值是本轮最该上屏的那个数，而它**必须**带着 `feeCoverage` 一起出现
 * （[P0-4]）——生产库上 89% 的往返腿费恒 0，那是「没记」不是「免费」。
 */
export function travelToCostText(pooled: AiTradePooled): string {
  if (pooled.travelToCostRatio == null || !Number.isFinite(pooled.travelToCostRatio)) {
    return `位移中位 ${fmtBps(pooled.medianMoveBps)}；成本样本不足（${pooled.feeTrips} 笔带费率记录），不下结论`;
  }
  return (
    `位移中位 ${fmtBps(pooled.medianMoveBps)} / 往返成本 ${fmtBps(pooled.medianCostBps)}` +
    ` = ${fmtOrDash(pooled.travelToCostRatio, 2)}×（取自 ${pooled.feeTrips} 笔带费率记录的往返，覆盖 ${fmtRate(pooled.feeCoverage)}）`
  );
}

/**
 * R 分布一行——**这是本轮头条结论的那几个数**：实测 193 笔里 100% 的 `|R| < 0.2`、无一笔触及过
 * ±1R，「典型交易只走完了自己止损距离的 2.83%」。
 *
 * 它同时是一条纪律：`rP10` / `rP90` / `withinFifthRShare` / `plannedRrMedian` / `medianStopAtrMult`
 * 后端逐个算了，若一个读点都没有，它们就是死数据——本仓库最高频的缺陷形状。每一项照例带分母。
 */
export function riskDistributionText(risk: AiTradeRiskProfile): string {
  const parts = [
    `R 分布 p10 / p50 / p90 = ${fmtOrDash(risk.rP10, 3)} / ${fmtOrDash(risk.rP50, 3)}` +
      ` / ${fmtOrDash(risk.rP90, 3)}（${risk.rSamples} 笔可算）`,
    `|R| < 0.2 占比 ${fmtRate(risk.withinFifthRShare)}`,
    `计划盈亏比中位 ${fmtOrDash(risk.plannedRrMedian, 2)}`,
  ];
  if (risk.medianStopAtrMult != null) {
    parts.push(
      `计划止损 ≈ ${fmtOrDash(risk.medianStopAtrMult, 1)}×ATR（覆盖 ${fmtRate(risk.stopAtrCoverage)}）`,
    );
  }
  return parts.join("；");
}

/** 口径推定的一句话；一笔都没推定 ⇒ `null`（不写「0 笔靠推定」这种噪音）。 */
export function costBasisText(assumed: number, total: number): string | null {
  if (!assumed || total <= 0) return null;
  return `其中 ${assumed} / ${total} 笔的盈亏口径为推定（老会话未落 costBasis，按出厂默认还原）`;
}

/** 会话级「已实现」三项；样本不足 ⇒ `null` ⇒ 调用点整段不渲染。 */
export function realizedRows(
  stats: AiTradeRealizedStats | null | undefined,
): { label: string; value: string }[] | null {
  if (!stats || stats.trips < REALIZED_MIN_TRIPS) return null;
  // R 的分母与另外两项不同（无止损 / 合约乘数未知的往返算不出 R），故它自带自己的那一个。
  const rLabel = stats.rSamples == null ? "R 倍数中位" : `R 倍数中位（${stats.rSamples} 笔可算）`;
  return [
    { label: `位移中位（${stats.trips} 笔）`, value: fmtBps(stats.medianMoveBps) },
    { label: rLabel, value: fmtOrDash(stats.medianRMultiple, 3) },
    { label: "已实现净额中位", value: fmtBps(stats.medianRealizedNetBps) },
  ];
}

/**
 * 一笔成交的 R 倍数（**唯一读点**，realized-edge-calibration 轴 A）。
 *
 * 两条通道的落点不同，故必须在这里收口：历史侧由 `trade_out` 提到顶层 `rMultiple`；实时侧只在
 * `explain.meta.rMultiple` 里——executor 成交 dict 的顶层键集是冻结的，新键只能搭 explain 信封。
 * 算不出（无止损 / 合约乘数未知 / 老数据）⇒ `null`，**绝不是 0**。
 */
export function readRMultiple(trade: {
  rMultiple?: number | null;
  explain?: { meta?: { rMultiple?: number | null } } | null;
}): number | null {
  const value = trade.rMultiple ?? trade.explain?.meta?.rMultiple ?? null;
  return value != null && Number.isFinite(value) ? value : null;
}

/** 会话级两个覆盖率的一行小字（分母纪律的落地；两处读点共用）。 */
export function realizedCoverageText(stats: AiTradeRealizedStats): string {
  return `带门禁估计 ${fmtRate(stats.estimateCoverage)} · 带费率记录 ${fmtRate(stats.feeCoverage)}`;
}
