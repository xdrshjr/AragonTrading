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
  // ---- fill-truth-and-effective-controls 轴 A/F 的两个加法键（老会话缺席 ⇒ 可选）----
  /** 本次判定用的容差；有它，`ok` 的措辞才答得出「为什么这也叫已对上」。 */
  tolUsd?: number | null;
  /**
   * 外部平仓的结算覆盖率。`exchange + localMark === 0` 时后端**整键省略**——「一次外部平仓都
   * 还没发生」与「发生了但一次都没拿到真值」是两件完全不同的事，用同一个 0 表示会让验收判据
   * `AC-1` 的分母失去意义。
   */
  settlementCoverage?: { exchange: number; localMark: number; ratio: number } | null;
}

// 一次费率取数失败的事实（后端 `okx.types.FeeAttemptError.wire()` 产物，
// ai-trade-silent-degradation-repair 轴 A-5）。它存在的全部理由是：生产上
// `GET /account/trade-fee` **1,524 次请求 0 次成功**，而这个失败此前没有任何一处宿主。
export interface FeeRateAttemptError {
  /** 阶梯上哪一跳：`family`（instFamily）| `type`（账户通用档）。 */
  rung: string;
  /** HTTP 状态码；`null` = 传输层失败（那里确实没有状态码）。 */
  httpStatus: number | null;
  code: string;
  msg: string;
}

// 官方费率档位读数（后端 `ai_auto/fee_rates.FeeRateCache.wire` 产物）。
export interface FeeRatesWire {
  makerBps: number;
  takerBps: number;
  level: string;
  ruleType: string;
  /**
   * 值域自轴 A-2 起是 4 个：`exchange:family`（阶梯第 1 跳）/ `exchange:type`（第 2 跳，账户
   * 通用档）/ `exchange`（**本轮之前的老记录，必须继续被识别**）/ `config`（本地保守常数）。
   */
  source: "exchange" | "exchange:family" | "exchange:type" | "config" | string;
  rebate: boolean;
  ageS: number;
  stale: boolean;
  makerBpsRaw: number;
  takerBpsRaw: number;
  /** 是否真的尝试过取数（轴 A-5；老回执缺席 ⇒ `undefined`）。 */
  attempted?: boolean;
  /** 最近一次取数失败的原因；`null` = 没失败过。 */
  lastError?: FeeRateAttemptError | null;
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
  // 轴 F-4：`ok` 的措辞把「为什么这也叫已对上」当场答掉。合入前 `ok` 与 `drift` 共用这一句，
  // 于是用户会同时看到绿色的「已对上」徽章与「差 +1.528 USDT」——徽章与它下面那行字互相打脸，
  // 而那比「把差额藏起来」更坏：它训练用户不去读那个数。轴 F-1 落地后 `ok` 就真的意味着
  // 「在容差内」了，这里如实把容差写出来。
  const within = rec.status === "ok" && rec.tolUsd != null
    ? `，在容差 ${+rec.tolUsd.toFixed(4)} USDT 内`
    : "";
  return { ...meta, detail: `本地净额与交易所可比口径差 ${diff}${ratio}${within}${mismatch}。` };
}

/**
 * 结算覆盖率一行：外部平仓里有多少笔拿到了交易所的真实结算价（轴 A/F）。
 *
 * `settlementCoverage` 整键缺席（一次外部平仓都还没发生 / 轴 A 未装配）⇒ `null` ⇒ **整行不渲染**。
 * 绝不显示一个 `0/0`——「还没发生」与「发生了但一次都没拿到」是两件完全不同的事。
 */
export function describeSettlementCoverage(
  rec: ReconcileWire | null | undefined,
): { text: string; tone: "ok" | "warn" | "muted" } | null {
  const coverage = rec?.settlementCoverage;
  if (!coverage) return null;
  const total = (coverage.exchange ?? 0) + (coverage.localMark ?? 0);
  if (total <= 0) return null;
  const pct = +((coverage.ratio ?? 0) * 100).toFixed(1);
  const tone = (coverage.ratio ?? 0) >= 0.9 ? "ok" : (coverage.ratio ?? 0) > 0 ? "warn" : "muted";
  return {
    tone,
    text: `外部平仓中 ${coverage.exchange}/${total}（${pct}%）拿到了交易所结算价`
      + (coverage.localMark ? `，其余 ${coverage.localMark} 笔按本地标记价入账` : ""),
  };
}

/** 恒等式自检徽标：`null`（没跑过）⇒ 不显示，绝不显示一个未经验证的「通过」。 */
export function describeIdentityCheck(rec: ReconcileWire | null | undefined): string | null {
  if (!rec || rec.identityHolds == null) return null;
  return rec.identityHolds
    ? "交易所口径自检通过"
    : `交易所口径自检未通过（最大偏离 ${+(rec.identityMaxGapUsd ?? 0).toFixed(6)} USDT）`;
}

/**
 * 费率来源徽标：官方档位 vs 本地常数——数从哪来决定它可不可信，绝不含糊其辞。
 *
 * 值域 2 → 4（轴 A-2）：阶梯的两跳各有自己的档位语义，把它们压成同一句话会让「我这个账户
 * 的真实档位到底取到没有」重新变得不可回答；老值 `exchange` **必须继续被识别**——生产库里
 * 已经落了带这个值的历史记录。
 *
 * `lastError` 尾注（轴 A-5）：本地常数只说「这是猜的」是不够的，还要说**为什么猜**。三周来
 * 用户看到的一直是一个没有原因的降级，而原因就写在每一次 400 的响应体里。
 */
export function describeFeeRates(rates: FeeRatesWire | null | undefined): string | null {
  if (!rates) return null;
  const origin = feeOriginLabel(rates);
  const stale = rates.stale ? "（读数已陈旧）" : "";
  const head = `${origin}：maker ${+rates.makerBps.toFixed(2)} / taker ${+rates.takerBps.toFixed(2)} bps${stale}`;
  const why = describeFeeRateError(rates.lastError);
  return why ? `${head} —— ${why}` : head;
}

/** `source` → 中文档位名（四个值域各一句，未知值一律按「本地保守常数」保守处理）。 */
export function feeOriginLabel(rates: FeeRatesWire): string {
  const level = rates.level ? ` ${rates.level}` : "";
  if (rates.source === "exchange:family") return `交易所官方档位${level}`;
  if (rates.source === "exchange:type") return `交易所账户通用档位${level}`;
  if (rates.source === "exchange") return `交易所官方档位${level}`;
  return "本地保守常数";
}

/**
 * 取数失败的一句人话；没有失败过 ⇒ `null`（调用方据此不渲染尾注）。
 *
 * **原始码只出现在括号里**：第一屏给的是「交易所拒绝了费率查询」，`51000` 这样的码是给排查
 * 用的补充信息，不是给用户读的主语。
 */
export function describeFeeRateError(
  error: FeeRateAttemptError | null | undefined,
): string | null {
  if (!error || (!error.code && !error.msg)) return null;
  const detail = [error.code, error.msg].filter(Boolean).join(" ");
  if (error.httpStatus === null || error.httpStatus === undefined) {
    return `未能连上交易所查询费率（${detail}），成本与净额按保守常数计算`;
  }
  return `交易所拒绝了费率查询（${detail}），成本与净额按保守常数计算`;
}
