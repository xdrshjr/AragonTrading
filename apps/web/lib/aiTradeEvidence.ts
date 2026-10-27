// 「实证准入」的 wire 类型与展示纯函数（ai-trade-evidence-gated-capital §3.5 / §5）。
//
// 三态、区间、gating 全部由后端 `evidence_gate.py` 判定并下发；本文件只负责把它们说成人话，
// **不重算任何统计**——前端再算一遍就是第二个真源，而两边的估计量（n−1 标准差、z）一旦
// 漂开，界面就会说出与门禁相反的结论。

/** 档位：严格 = 先证后投；均衡 = 证伪即停；仅观察 = 只记录不拦截。 */
export type AiTradeEvidencePolicy = "strict" | "balanced" | "observe";
export type AiTradeEvidenceState = "proven" | "unproven" | "disproven";
export type AiTradeEvidenceSideKey = "long" | "short";
/** `pooled` = 同方向跨标的合并样本判定（只可能判成「已证伪」，永远不能判成「已证实」）。 */
export type AiTradeEvidenceBasis = "bucket" | "pooled";
/** 此刻这个方向的开仓会不会下真单（后端按档位与三态推出，前端不重算）。 */
export type AiTradeEvidenceGating = "live" | "shadow";

/** 启动请求 `evidenceGate`（§5.1）。整键缺省 ⇒ 服务端按 settings 装配。 */
export interface AiTradeEvidenceGateIn {
  enabled: boolean;
  policy: AiTradeEvidencePolicy;
}

/** 分组回执 `AiTradeGroupRow.evidenceGate`（生效值；未装配 ⇒ `null`）。 */
export interface AiTradeEvidenceGateReceipt {
  enabled: boolean;
  policy: AiTradeEvidencePolicy;
  minSamples: number;
  window: number;
  z: number;
  maxAgeDays: number;
  pooledDisprove?: boolean;
  refPolicy: string;
}

/** 状态键与证据查询共有的那部分读数（同一个渲染函数吃两种来源）。 */
export interface AiTradeEvidenceReading {
  state: AiTradeEvidenceState;
  basis: AiTradeEvidenceBasis;
  n: number;
  meanBps: number | null;
  lcbBps: number | null;
  ucbBps: number | null;
  winRate: number | null;
  gating: AiTradeEvidenceGating;
}

/** 成员 status `evidence.sides.<side>`（§5.2）。 */
export interface AiTradeEvidenceSide extends AiTradeEvidenceReading {
  openShadow: boolean;
  sessionShadows: number;
  sessionTraded: number;
}

export interface AiTradeEvidenceSessionCounts {
  intents: number;
  shadows: number;
  repeats: number;
  matured: number;
  traded: number;
  voided: number;
  dropped: number;
}

/** 成员 status 键 `evidence`（未装配 ⇒ **整键缺席**；某个方向的键也可能缺席）。 */
export interface AiTradeEvidenceStatus {
  policy: AiTradeEvidencePolicy;
  minSamples: number;
  refPolicy: string;
  sides: Partial<Record<AiTradeEvidenceSideKey, AiTradeEvidenceSide>>;
  session: AiTradeEvidenceSessionCounts;
  snapshotError: boolean;
}

/** `GET /ai-trade/evidence` 的一个桶（§5.3）。 */
export interface AiTradeEvidenceBucketOut extends AiTradeEvidenceReading {
  instId: string;
  side: AiTradeEvidenceSideKey;
  seBps: number | null;
  backfillN: number;
  openN: number;
  lastMaturedTs: number | null;
}

export interface AiTradeEvidenceRuleOut {
  policy: AiTradeEvidencePolicy;
  minSamples: number;
  window: number;
  z: number;
  maxAgeDays: number;
}

/** 某标的每个方向最近 7 天的影子产生速度（笔 / 天）；无数据的方向键缺席。 */
export type AiTradeEvidenceRates = Record<
  string,
  Partial<Record<AiTradeEvidenceSideKey, number>>
>;

export interface AiTradeEvidenceResponse {
  bar: string;
  refPolicy: string;
  asOf: number;
  rule: AiTradeEvidenceRuleOut;
  buckets: AiTradeEvidenceBucketOut[];
  recentShadowRatePerDay: AiTradeEvidenceRates;
}

export interface AiTradeEvidenceBackfillIn {
  instIds?: string[] | null;
  bar: string;
  days?: number;
}

export interface AiTradeEvidenceBackfillOut {
  inserted: number;
  skipped: {
    candlesMissing: number;
    atrUnavailable: number;
    duplicate: number;
    liveCovered: number;
  };
  truncated: boolean;
  remaining: number;
  notes: string[];
}

export const EVIDENCE_DEFAULT_POLICY: AiTradeEvidencePolicy = "strict";

export interface EvidencePolicyOption {
  value: AiTradeEvidencePolicy;
  label: string;
  /** 向导第 5 步的摘要短名（「实证准入：严格」）。 */
  short: string;
  hint: string;
  recommended: boolean;
}

export const EVIDENCE_POLICY_OPTIONS: readonly EvidencePolicyOption[] = [
  {
    value: "strict",
    label: "严格 · 先证后投",
    short: "严格",
    hint:
      "某个方向在该标的上至少有 10 笔参考样本、且均值下界为正，才会下真实委托；" +
      "在此之前只记影子。",
    recommended: true,
  },
  {
    value: "balanced",
    label: "均衡 · 证伪即停",
    short: "均衡",
    hint: "证据不足时照常下单，只有被证明亏钱的方向会停下。",
    recommended: false,
  },
  {
    value: "observe",
    label: "仅观察",
    short: "仅观察",
    hint: "从不拦截，只记录证据。适合你想先看看数据的时候。",
    recommended: false,
  },
];

export function evidencePolicyOption(
  policy: AiTradeEvidencePolicy,
): EvidencePolicyOption {
  return (
    EVIDENCE_POLICY_OPTIONS.find((option) => option.value === policy) ??
    EVIDENCE_POLICY_OPTIONS[0]
  );
}

/** 统计卡页脚的两行口径说明（常量，组件里不拼）。 */
export const EVIDENCE_FOOTNOTES: readonly string[] = [
  "样本按固定参考离场规则计算（收益锁 → 2×ATR 止损 / 4×ATR 止盈 → 24 根时间离场），" +
    "与真实离场无关；真实盈亏见上方已实现卡片。",
  "「最小 edge 门禁」衡量的是波动能否覆盖成本；「实证准入」衡量的是这个方向过去是否真的赚钱。",
];

export const EVIDENCE_SHADOW_TITLE =
  "该方向的开仓只记录、不下真实委托，证据下界转正后自动恢复";

export const EVIDENCE_SNAPSHOT_ERROR_TEXT = "证据读取失败，按未证实处理";

export const EVIDENCE_POOLED_NOTE = "由同方向合并样本判定";

const STATE_LABEL: Record<AiTradeEvidenceState, string> = {
  proven: "已证实",
  unproven: "未证实",
  disproven: "已证伪",
};

const SIDE_LABEL: Record<AiTradeEvidenceSideKey, string> = { long: "多", short: "空" };
const SIDE_ORDER: readonly AiTradeEvidenceSideKey[] = ["long", "short"];

/** 三态中文标签；未知值原样回退（后端新增一态时不至于变成空白）。 */
export function evidenceStateLabel(state: string): string {
  return STATE_LABEL[state as AiTradeEvidenceState] ?? state;
}

export function evidenceSideLabel(side: AiTradeEvidenceSideKey): string {
  return SIDE_LABEL[side];
}

/**
 * 带符号的 bps 读数：正数带 `+`，负号用 U+2212（与等宽数字对齐，不与连字符混淆）。
 * 缺失 / 非有限 ⇒ `—`。
 */
export function fmtSignedBps(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return "—";
  const text = Math.abs(value).toFixed(1);
  if (text === "0.0") return "0.0";
  return value > 0 ? `+${text}` : `−${text}`;
}

/**
 * 一个方向的读数文案。
 *
 * 未证实且样本不足 ⇒ 进度 `7/10`；否则 `均值 +3.1 bps（下界 +0.4）`。已证伪改报**上界**：
 * 它的判据正是上界 < 0，报下界会让用户以为「还差一点就转正」。
 */
export function formatEvidenceReading(
  reading: AiTradeEvidenceReading,
  minSamples: number,
): string {
  if (reading.state === "unproven" && reading.n < minSamples) {
    return `${reading.n}/${minSamples}`;
  }
  const mean = `均值 ${fmtSignedBps(reading.meanBps)} bps`;
  if (reading.state === "disproven") {
    return `${mean}（上界 ${fmtSignedBps(reading.ucbBps)}）`;
  }
  return `${mean}（下界 ${fmtSignedBps(reading.lcbBps)}）`;
}

export interface EvidenceSegment {
  side: AiTradeEvidenceSideKey;
  sideLabel: string;
  state: AiTradeEvidenceState;
  stateLabel: string;
  reading: string;
  isShadow: boolean;
  isPooled: boolean;
}

/** `sideSegments` 的入参：成员 status 与查询结果（经 `bucketsForInst`）都满足这个形状。 */
export interface EvidenceSidesSource {
  minSamples: number;
  sides: Partial<Record<AiTradeEvidenceSideKey, AiTradeEvidenceReading>>;
}

/** 多 / 空两段（缺席的方向不出段；来源为空 ⇒ 空数组）。 */
export function sideSegments(
  source: EvidenceSidesSource | null | undefined,
): EvidenceSegment[] {
  if (!source) return [];
  const segments: EvidenceSegment[] = [];
  for (const side of SIDE_ORDER) {
    const reading = source.sides[side];
    if (!reading) continue;
    segments.push({
      side,
      sideLabel: SIDE_LABEL[side],
      state: reading.state,
      stateLabel: evidenceStateLabel(reading.state),
      reading: formatEvidenceReading(reading, source.minSamples),
      isShadow: reading.gating === "shadow",
      isPooled: reading.basis === "pooled",
    });
  }
  return segments;
}

/** 从查询结果里取某个标的的两个方向（无桶 ⇒ `sides` 为空对象）。 */
export function bucketsForInst(
  response: AiTradeEvidenceResponse,
  instId: string,
): EvidenceSidesSource {
  const sides: EvidenceSidesSource["sides"] = {};
  for (const bucket of response.buckets) {
    if (bucket.instId === instId) sides[bucket.side] = bucket;
  }
  return { minSamples: response.rule.minSamples, sides };
}

/** 预览里是否已有任何一个「已证实」的桶（严格档的预估文案据此决定说不说）。 */
export function hasProvenBucket(response: AiTradeEvidenceResponse | null): boolean {
  return !!response && response.buckets.some((b) => b.state === "proven");
}

/** 来自历史成交回填的样本数（回填带选择偏差，界面单列）。 */
export function backfillSampleCount(response: AiTradeEvidenceResponse | null): number {
  if (!response) return 0;
  return response.buckets.reduce((sum, b) => sum + (b.backfillN ?? 0), 0);
}

function sideNeedDays(
  response: AiTradeEvidenceResponse,
  instId: string,
  side: AiTradeEvidenceSideKey,
): number | null {
  const have = response.buckets.find((b) => b.instId === instId && b.side === side)?.n ?? 0;
  const need = Math.max(0, response.rule.minSamples - have);
  if (need === 0) return 0;
  const rate = response.recentShadowRatePerDay[instId]?.[side];
  if (rate == null || !Number.isFinite(rate) || rate <= 0) return null;
  return need / rate;
}

/**
 * 按最近的开仓频率估计「每个方向攒满 minSamples 笔」要几天，取所选标的里**最慢**的那个方向。
 *
 * 任何一个还缺样本的方向拿不到速度 ⇒ `null`（最慢的那个恰恰可能是它，不猜）。
 * 全部方向都已攒满 ⇒ `null`（没有可估的等待）。结果向上取整到整天。
 */
export function daysToMinSamples(
  response: AiTradeEvidenceResponse | null,
  instIds: readonly string[],
): number | null {
  if (!response || instIds.length === 0) return null;
  let slowest = 0;
  for (const instId of instIds) {
    for (const side of SIDE_ORDER) {
      const days = sideNeedDays(response, instId, side);
      if (days == null) return null;
      slowest = Math.max(slowest, days);
    }
  }
  return slowest > 0 ? Math.ceil(slowest) : null;
}

/** 统计卡「本会话」列（按方向）。 */
export function evidenceSessionText(side: AiTradeEvidenceSide): string {
  const open = side.openShadow ? 1 : 0;
  return `真单 ${side.sessionTraded} · 影子 ${side.sessionShadows} · 进行中 ${open}`;
}

/** 胜率：n < 2 ⇒ `—`（一笔样本的胜率只有 0% 与 100% 两个值，没有信息量）。 */
export function fmtEvidenceWinRate(reading: AiTradeEvidenceReading): string {
  if (reading.n < 2 || reading.winRate == null) return "—";
  return `${Math.round(reading.winRate * 100)}%`;
}

/** 成员行的悬停说明：会话级合计 + 档位与参考规则版本。 */
export function evidenceRowTitle(status: AiTradeEvidenceStatus): string {
  const s = status.session;
  return [
    `档位 ${evidencePolicyOption(status.policy).short} · 至少 ${status.minSamples} 笔样本`,
    `本会话：开仓意愿 ${s.intents} · 新影子 ${s.shadows} · 重复意愿 ${s.repeats}`,
    `已结束 ${s.matured} · 真单 ${s.traded} · 作废 ${s.voided} · 未落库 ${s.dropped}`,
    `参考规则 ${status.refPolicy}`,
  ].join("\n");
}

export interface EvidenceInterval {
  /** 零线位置（百分比，恒 50：区间条以零为中心对称缩放）。 */
  zeroPct: number;
  loPct: number;
  hiPct: number;
  meanPct: number;
}

function toPct(value: number, scale: number): number {
  return Math.min(100, Math.max(0, 50 + (value / scale) * 50));
}

/**
 * 区间条几何：零线居中，`scale` 是整张卡共用的半宽（bps），使各行可以直接比较长短。
 * 读数不全 ⇒ `null`（不画条，只留数值）。
 */
export function evidenceInterval(
  reading: AiTradeEvidenceReading,
  scale: number,
): EvidenceInterval | null {
  const { lcbBps, ucbBps, meanBps } = reading;
  if (lcbBps == null || ucbBps == null || meanBps == null || !(scale > 0)) return null;
  return {
    zeroPct: 50,
    loPct: toPct(lcbBps, scale),
    hiPct: toPct(ucbBps, scale),
    meanPct: toPct(meanBps, scale),
  };
}

/** 一组读数的共用半宽：各端点绝对值的最大值；全缺 ⇒ 0（调用方据此不画条）。 */
export function evidenceScale(readings: readonly AiTradeEvidenceReading[]): number {
  let scale = 0;
  for (const r of readings) {
    for (const v of [r.lcbBps, r.ucbBps, r.meanBps]) {
      if (v != null && Number.isFinite(v)) scale = Math.max(scale, Math.abs(v));
    }
  }
  return scale;
}

/** 回填回执 → 一句话（截断 ⇒ 提示再点一次；跳过原因只列非零项）。 */
export function describeBackfill(out: AiTradeEvidenceBackfillOut): string {
  if (out.truncated) {
    return `已回填 ${out.inserted} 笔，还有 ${out.remaining} 笔没处理完，再点一次继续`;
  }
  const reasons = [
    ["K 线缺失", out.skipped.candlesMissing],
    ["ATR 不可得", out.skipped.atrUnavailable],
    ["已回填过", out.skipped.duplicate],
  ].filter(([, count]) => (count as number) > 0) as [string, number][];
  const total = reasons.reduce((sum, [, count]) => sum + count, 0);
  if (total === 0) return `回填 ${out.inserted} 笔`;
  const detail = reasons.map(([label, count]) => `${label} ${count}`).join("、");
  return `回填 ${out.inserted} 笔（跳过 ${total} 笔：${detail}）`;
}

/** `liveCovered > 0` 时的补充说明；否则 `null`。 */
export function describeLiveCovered(out: AiTradeEvidenceBackfillOut): string | null {
  const n = out.skipped.liveCovered;
  return n > 0 ? `另有 ${n} 笔已由实时影子度量，不重复计入` : null;
}
