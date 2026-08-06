// AI 自动交易成本 / 对账读数的中文化与派生（ai-trade-cost-truth-and-net-pnl §3.5 F-1）。
//
// 全部纯函数、无副作用、不读时钟。**只翻译后端已经算出来的数，绝不在前端重算任何成本或差额**：
// 本轮修的正是「同一个量两处各算一次必然漂移」这一类缺陷，再在前端立第二个口径等于自打耳光。
// 唯一的例外是 `buildRoundTrips`（`autoTradePnl.ts`）的往返净额——那是与后端 `_Trip.net_pnl`
// 逐字同构、并由同一组夹具双侧断言（G-7）的镜像实现，不是新口径。

export type CostBasis = "net" | "gross";

// 交易所对账读数（后端 `ai_auto/reconcile.build_reconciliation` 产物，形状见 spec §5.4）。
export interface ReconcileWire {
  status: "ok" | "drift" | "partial" | "unavailable";
  samples: number;
  localTrips: number;
  final: boolean;
  coverageMismatch: boolean;
  localNetUsd: number;
  diffUsd: number | null;
  diffRatio: number | null;
  exchangeRealizedUsd: number | null;
  exchangeComparableUsd: number | null;
  exchangeFeeUsd: number | null;
  exchangeFundingFeeUsd: number | null;
  exchangeLiqPenaltyUsd: number | null;
  identityHolds: boolean | null;
  identityMaxGapUsd: number | null;
  windowStartTs: number;
}

// 官方费率档位读数（后端 `ai_auto/fee_rates.FeeRateCache.wire` 产物）。
export interface FeeRatesWire {
  makerBps: number;
  takerBps: number;
  level: string;
  ruleType: string;
  source: "exchange" | "config" | string;
  rebate: boolean;
  ageS: number;
  stale: boolean;
  makerBpsRaw: number;
  takerBpsRaw: number;
}

/** 盈亏口径徽标文案；`assumed` 为真时必须说明这是**推定**而非已知（P0-3）。 */
export function describeCostBasis(basis: CostBasis | undefined, assumed = false): string {
  if (basis === "net") return assumed ? "净额（推定）" : "净额（已扣手续费）";
  if (basis === "gross") return assumed ? "毛额（推定）" : "毛额（未扣手续费）";
  return "口径未知";
}

/** 口径 tooltip：把「数字为什么变小了」一次说清楚，避免被当成新 bug（R-1）。 */
export const COST_BASIS_HINT =
  "净额口径 = 价差盈亏 − 开仓手续费 − 平仓手续费，也就是「此刻平掉能拿到手的钱」；"
  + "它比毛额低的那一部分，正是你本来就已经付掉的钱。不含资金费与强平罚金——那两项只出现在"
  + "交易所对账口径里。";

/** bps → 「12.5 bps（0.125%）」；不可数 → `—`。 */
export function formatCostBps(bps: number | null | undefined): string {
  if (bps == null || !Number.isFinite(bps)) return "—";
  return `${+bps.toFixed(2)} bps（${+(bps / 100).toFixed(3)}%）`;
}

/** 成本占毛利比：`feeUsd / |grossPnlUsd|`；毛利为 0 → null（不造一个 ∞ 或 0）。 */
export function costShareOfGross(
  feeUsd: number | null | undefined,
  grossPnlUsd: number | null | undefined,
): number | null {
  if (feeUsd == null || grossPnlUsd == null) return null;
  const denom = Math.abs(grossPnlUsd);
  return denom > 0 ? feeUsd / denom : null;
}

// 对账状态 → 中文短语 + 语气（渲染层据 tone 选颜色）。
const RECONCILE_LABEL: Record<string, { label: string; tone: "ok" | "warn" | "muted" }> = {
  ok: { label: "已对上", tone: "ok" },
  drift: { label: "存在漂移", tone: "warn" },
  partial: { label: "部分对账", tone: "muted" },
  unavailable: { label: "暂无样本", tone: "muted" },
};

export interface ReconcileView {
  label: string;
  tone: "ok" | "warn" | "muted";
  detail: string;
}

/**
 * 对账读数 → 可直接渲染的中文视图；`null` / 缺 status ⇒ `null`（调用方据此**整段隐藏**）。
 *
 * `unavailable` 与 `partial` 绝不显示差额——「没样本」不等于「对上了」，把 0 差额画出来是在
 * 假装系统验证过一件它没验证过的事。`partial` 额外说明成因（终态那一笔尚未在交易所侧结算）。
 */
export function describeReconcile(rec: ReconcileWire | null | undefined): ReconcileView | null {
  if (!rec || !rec.status) return null;
  const meta = RECONCILE_LABEL[rec.status] ?? RECONCILE_LABEL.unavailable;
  if (rec.status === "unavailable") {
    return { ...meta, detail: "该窗口内交易所侧尚无已结算持仓，无法比对。" };
  }
  const diff = rec.diffUsd == null ? "—" : `${rec.diffUsd >= 0 ? "+" : ""}${+rec.diffUsd.toFixed(4)} USDT`;
  const ratio = rec.diffRatio == null ? "" : `（${+(rec.diffRatio * 100).toFixed(2)}%）`;
  if (rec.status === "partial") {
    return { ...meta, detail: `差 ${diff}${ratio}；最后一笔尚未在交易所侧结算，稍后可重新对账。` };
  }
  const mismatch = rec.coverageMismatch ? "；本地往返数与交易所结算数不等，可能存在非本会话的手工持仓" : "";
  return { ...meta, detail: `本地净额与交易所可比口径差 ${diff}${ratio}${mismatch}。` };
}

/** 恒等式自检徽标：`null`（没跑过）⇒ 不显示，绝不显示一个未经验证的「通过」。 */
export function describeIdentityCheck(rec: ReconcileWire | null | undefined): string | null {
  if (!rec || rec.identityHolds == null) return null;
  return rec.identityHolds
    ? "交易所口径自检通过"
    : `交易所口径自检未通过（最大偏离 ${+(rec.identityMaxGapUsd ?? 0).toFixed(6)} USDT）`;
}

/** 费率来源徽标：官方档位 vs 本地常数——数从哪来决定它可不可信，绝不含糊其辞。 */
export function describeFeeRates(rates: FeeRatesWire | null | undefined): string | null {
  if (!rates) return null;
  const origin = rates.source === "exchange"
    ? `交易所官方档位${rates.level ? ` ${rates.level}` : ""}`
    : "本地保守常数";
  const stale = rates.stale ? "（读数已陈旧）" : "";
  return `${origin}：maker ${+rates.makerBps.toFixed(2)} / taker ${+rates.takerBps.toFixed(2)} bps${stale}`;
}
