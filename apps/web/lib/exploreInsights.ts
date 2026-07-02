// 因子组合探索「洞察与引导精修」纯助手 + 类型（factor-combo-exploration-insights §2.2/§2.4）：
// computeInsights（对成功候选做因子级归因：边际 IC 提升 / 登顶命中率 / 平均学习权重 / 大小分桶）与
// suggestRefinement（据洞察生成下一轮聚焦候选）。全程纯函数、零 IO、确定性（无 Date.now / 随机，便于快照测试），
// 组件只消费。零后端往返：归因所需数据（succeeded 候选的 icOut/weights + 弹窗持有的 LocalCombo 成员）客户端已全有。
import { memberIdentity } from "./factor";
import type { FactorMember, TrainMethod } from "./factorTypes";
import { candidateIcOut } from "./weightBatch";
import type { CandidateState } from "./weightBatchTypes";
import { MAX_EXPLORE_CANDIDATES, MAX_EXPLORE_POOL, type LocalCombo } from "./weightExplore";

// 单因子贡献（按 memberIdentity 聚合；member 为该身份的代表成员，供调用方 labelOf 映射展示名）。
export interface FactorContribution {
  factorKey: string;
  identity: string; // memberIdentity(m)：factorKey|规范化params|direction（跨组合稳定聚合键）
  member: FactorMember; // 该身份的代表成员（首次出现），供 labelOf(m) 取中文名；面板不再自持 pool
  appearCount: number; // 含该因子的成功候选数
  absentCount: number; // 不含该因子的成功候选数
  meanIcWith: number | null; // 含该因子候选的平均 icOut（appearCount>0 才有值）
  meanIcWithout: number | null; // 不含该因子候选的平均 icOut（absentCount>0 才有值）
  marginalLift: number | null; // meanIcWith - meanIcWithout；两侧任一为空 → null（不伪造 0）
  bestIc: number | null; // 含该因子候选的最优 icOut
  topHitRate: number | null; // 含该因子候选落入「样本外 IC 前四分位」的比例（分母=含该因子候选数）
  meanAbsWeight: number | null; // 含该因子候选中该因子学习权重 |w| 的均值（缺键跳过样本，全缺 → null）
}

// 组合大小分桶（判断「加因子变好还是过拟合」）。
export interface SizeBucket {
  size: number;
  count: number;
  meanIc: number | null;
  bestIc: number | null;
}

// 一次探索的完整洞察。
export interface ExploreInsights {
  factors: FactorContribution[]; // 按 marginalLift 降序（null 殿后，tie 用 bestIc→appearCount→identity）
  sizes: SizeBucket[]; // 按 size 升序
  succeededCount: number; // 参与统计的成功候选数（succeeded 且 icOut 有限且能定位到组合）
  totalCount: number; // 候选总数（含失败 / 取消 / 排队）
  hasEnoughSamples: boolean; // succeededCount>=2 且存在「含/不含」对比的因子，才认为归因有意义
}

// 引导精修建议（一律落为 manual 组合：确定、可解释、无隐式枚举爆炸）。
export interface RefinementSuggestion {
  strategy: "manual";
  combos: number[][]; // pool 内下标数组（已去重、已按 cap 截断，附截断标注）
  methods: TrainMethod[]; // 默认=上轮最佳候选的方法（单方法，聚焦；用户仍可在配置态改）
  pool: FactorMember[]; // 精修池=上轮池（保持下标口径一致）
  rationale: string[]; // 每个建议组合的一句话理由（与 combos 等长）
  truncated: boolean; // 因 cap 截断时为 true（UI 明示，绝不静默丢弃）
}

// 归因样本（内部）：一个成功候选 = 一个样本（同组合多方法各计一次，方法鲁棒性亦是信号）。
interface Sample {
  icOut: number;
  size: number;
  identities: Set<string>;
  memberByIdentity: Map<string, FactorMember>;
  weights: Record<string, number> | null;
}

// 均值：空集 → null（绝不假装 0，与 icOut 缺失口径一致）。
function mean(values: number[]): number | null {
  if (values.length === 0) return null;
  return values.reduce((a, b) => a + b, 0) / values.length;
}

// 线性插值分位数（升序输入，p∈[0,1]），与 numpy 默认 'linear' 同口径。
function percentile(sortedAsc: number[], p: number): number {
  if (sortedAsc.length === 0) return NaN;
  if (sortedAsc.length === 1) return sortedAsc[0];
  const rank = p * (sortedAsc.length - 1);
  const lo = Math.floor(rank);
  const hi = Math.ceil(rank);
  if (lo === hi) return sortedAsc[lo];
  return sortedAsc[lo] * (hi - rank) + sortedAsc[hi] * (rank - lo);
}

// 收集归因样本：只取 succeeded 且 icOut 经 candidateIcOut 有限、且 variantLabel 能定位到组合者
// （与排行榜「最佳」判定同源，null/NaN 一致地被排除，P2-4）。失败 / 取消 / 无组合者不入样，避免污染。
function collectSamples(states: CandidateState[], combos: LocalCombo[]): Sample[] {
  const comboById = new Map(combos.map((c) => [c.id, c]));
  const samples: Sample[] = [];
  for (const state of states) {
    if (state.status !== "succeeded") continue;
    const ic = candidateIcOut(state);
    if (ic == null) continue;
    const combo = comboById.get(state.variantLabel);
    if (!combo) continue;
    const identities = new Set<string>();
    const memberByIdentity = new Map<string, FactorMember>();
    for (const m of combo.members) {
      const id = memberIdentity(m);
      identities.add(id);
      if (!memberByIdentity.has(id)) memberByIdentity.set(id, m);
    }
    samples.push({ icOut: ic, size: combo.members.length, identities, memberByIdentity, weights: state.result?.weights ?? null });
  }
  return samples;
}

// 因子身份全集 → 代表成员（首次出现）：以 combos（本轮探索的组合全集）为准，使「所有组合都失败」的因子
// 也能以 appearCount=0 现身「样本不足」，而非被静默漏掉。
function collectUniverse(combos: LocalCombo[]): Map<string, FactorMember> {
  const universe = new Map<string, FactorMember>();
  for (const combo of combos) {
    for (const m of combo.members) {
      const id = memberIdentity(m);
      if (!universe.has(id)) universe.set(id, m);
    }
  }
  return universe;
}

// 单因子贡献：含/不含对比均值 → 边际提升；恒在（absentCount=0）或无成功候选 → marginalLift=null（不伪造 0）。
function buildContribution(
  id: string,
  member: FactorMember,
  samples: Sample[],
  q75: number,
  weightKeyOf: (m: FactorMember) => string,
): FactorContribution {
  const withS = samples.filter((s) => s.identities.has(id));
  const withoutS = samples.filter((s) => !s.identities.has(id));
  const meanIcWith = mean(withS.map((s) => s.icOut));
  const meanIcWithout = mean(withoutS.map((s) => s.icOut));
  const marginalLift = meanIcWith != null && meanIcWithout != null ? meanIcWith - meanIcWithout : null;
  const weights = withS
    .map((s) => (s.weights ? s.weights[weightKeyOf(s.memberByIdentity.get(id) ?? member)] : undefined))
    .filter((w): w is number => typeof w === "number" && Number.isFinite(w))
    .map((w) => Math.abs(w));
  return {
    factorKey: member.factorKey,
    identity: id,
    member,
    appearCount: withS.length,
    absentCount: withoutS.length,
    meanIcWith,
    meanIcWithout,
    marginalLift,
    bestIc: withS.length ? Math.max(...withS.map((s) => s.icOut)) : null,
    topHitRate: withS.length ? withS.filter((s) => s.icOut >= q75).length / withS.length : null,
    meanAbsWeight: mean(weights),
  };
}

// 组合大小分桶：按成员数分组的样本 icOut 均值 / 最优。
function buildSizeBuckets(samples: Sample[]): SizeBucket[] {
  const bySize = new Map<number, number[]>();
  for (const s of samples) {
    const arr = bySize.get(s.size) ?? [];
    arr.push(s.icOut);
    bySize.set(s.size, arr);
  }
  return [...bySize.entries()]
    .map(([size, ics]) => ({ size, count: ics.length, meanIc: mean(ics), bestIc: ics.length ? Math.max(...ics) : null }))
    .sort((a, b) => a.size - b.size);
}

// 贡献排序：marginalLift 降序（null 殿后），tie 用 bestIc 降序（null 殿后）→ appearCount 降序 → identity 升序。
function rankNull(value: number | null): number {
  return value == null ? 1 : 0;
}

function byContribution(a: FactorContribution, b: FactorContribution): number {
  if (rankNull(a.marginalLift) !== rankNull(b.marginalLift)) return rankNull(a.marginalLift) - rankNull(b.marginalLift);
  if (a.marginalLift != null && b.marginalLift != null && a.marginalLift !== b.marginalLift) return b.marginalLift - a.marginalLift;
  if (rankNull(a.bestIc) !== rankNull(b.bestIc)) return rankNull(a.bestIc) - rankNull(b.bestIc);
  if (a.bestIc != null && b.bestIc != null && a.bestIc !== b.bestIc) return b.bestIc - a.bestIc;
  if (a.appearCount !== b.appearCount) return b.appearCount - a.appearCount;
  return a.identity < b.identity ? -1 : a.identity > b.identity ? 1 : 0;
}

/**
 * 因子贡献洞察主入口（纯聚合，零 IO）。
 *
 * @param states 全部候选态（只对 succeeded 且 icOut 有限者统计）。
 * @param combos 弹窗持有的本地组合（id ↔ members），用于把候选映射回因子集合。
 * @param opts.weightKeyOf 成员 → result.weights 键映射（默认 m.factorKey，容忍缺键，P2-5）。
 * @returns 因子贡献榜 + 大小分桶 + 样本充足性标志。
 */
export function computeInsights(
  states: CandidateState[],
  combos: LocalCombo[],
  opts?: { weightKeyOf?: (m: FactorMember) => string },
): ExploreInsights {
  const weightKeyOf = opts?.weightKeyOf ?? ((m: FactorMember) => m.factorKey);
  const samples = collectSamples(states, combos);
  const universe = collectUniverse(combos);
  const icValues = samples.map((s) => s.icOut).sort((a, b) => a - b);
  // 前四分位阈值：成功候选 <4 退化为「≥中位数」（并由 UI 标注样本较少）；无样本时置 +∞（topHitRate 恒 0/…→null）。
  const q75 = icValues.length >= 4 ? percentile(icValues, 0.75) : icValues.length ? percentile(icValues, 0.5) : Infinity;
  const factors = [...universe.entries()]
    .map(([id, member]) => buildContribution(id, member, samples, q75, weightKeyOf))
    .sort(byContribution);
  return {
    factors,
    sizes: buildSizeBuckets(samples),
    succeededCount: samples.length,
    totalCount: states.length,
    hasEnoughSamples: samples.length >= 2 && factors.some((f) => f.marginalLift != null),
  };
}

// ---- 引导精修（据洞察生成下一轮聚焦候选） ----

// 组合下标：去重保序、界内、成员数落在 [2, MAX_EXPLORE_POOL]。非法 → null。
function normalizeCombo(indices: number[], poolSize: number): number[] | null {
  const seen = new Set<number>();
  const out: number[] = [];
  for (const i of indices) {
    if (!Number.isInteger(i) || i < 0 || i >= poolSize || seen.has(i)) continue;
    seen.add(i);
    out.push(i);
  }
  return out.length >= 2 && out.length <= MAX_EXPLORE_POOL ? out : null;
}

// 按 marginalLift 降序（null 殿后）排 pool 下标，tie 用 pool 原序（稳定，可解释）。
function rankedPoolIndices(pool: FactorMember[], liftByIdentity: Map<string, number | null>): number[] {
  const identities = pool.map((m) => memberIdentity(m));
  return pool
    .map((_, i) => i)
    .sort((a, b) => {
      const la = liftByIdentity.get(identities[a]) ?? null;
      const lb = liftByIdentity.get(identities[b]) ?? null;
      if (rankNull(la) !== rankNull(lb)) return rankNull(la) - rankNull(lb);
      if (la != null && lb != null && la !== lb) return lb - la;
      return a - b;
    });
}

// 生成候选组合（B / B−最弱 / B+最强 / 前缀嵌套）的原始清单（未去重、未截断）。
function draftCombos(
  bIdx: number[],
  pool: FactorMember[],
  identities: string[],
  liftByIdentity: Map<string, number | null>,
): { indices: number[]; rationale: string }[] {
  const drafts: { indices: number[]; rationale: string }[] = [];
  drafts.push({ indices: [...bIdx], rationale: "上轮最佳，作对照" });

  // B − 最弱因子（marginalLift 最低，负者优先自然落入）：仅当去掉后仍 ≥2 成员。
  let weakIdx: number | null = null;
  let weakLift = Infinity;
  for (const i of bIdx) {
    const lift = liftByIdentity.get(identities[i]) ?? null;
    if (lift != null && lift < weakLift) {
      weakLift = lift;
      weakIdx = i;
    }
  }
  if (weakIdx != null && bIdx.length - 1 >= 2) {
    drafts.push({ indices: bIdx.filter((i) => i !== weakIdx), rationale: `剔除疑似拖后腿的 ${pool[weakIdx].factorKey}` });
  }

  // B + 最强候补（不在 B、marginalLift 最高且为正的池因子）。
  const ranked = rankedPoolIndices(pool, liftByIdentity);
  const bSet = new Set(bIdx);
  const addIdx = ranked.find((i) => {
    const lift = liftByIdentity.get(identities[i]) ?? null;
    return !bSet.has(i) && lift != null && lift > 0;
  });
  if (addIdx != null) drafts.push({ indices: [...bIdx, addIdx], rationale: `补入高边际贡献的 ${pool[addIdx].factorKey}` });

  // 前缀嵌套：按贡献降序取 top-k（k = |B|-1 / |B| / |B|+1，夹在 [2, |pool|]）。
  const ks = [bIdx.length - 1, bIdx.length, bIdx.length + 1].filter((k, i, a) => k >= 2 && k <= pool.length && a.indexOf(k) === i);
  for (const k of ks) drafts.push({ indices: ranked.slice(0, k), rationale: `按贡献排序的 top-${k} 组合` });
  return drafts;
}

/**
 * 据洞察 + 上轮最佳组合生成下一轮聚焦候选（确定性、纯函数）。
 *
 * @param insights computeInsights 产物（提供各因子 marginalLift）。
 * @param pool 上一轮因子池（下标口径基准，须与 combos 一致，P1-1）。
 * @param bestComboMembers 上一轮最佳候选的组合成员。
 * @param bestMethod 上一轮最佳候选的训练方法（下一轮默认单方法聚焦）。
 * @param cap 候选上限（combos×methods 不超 cap，默认 MAX_EXPLORE_CANDIDATES）。
 * @returns manual 精修建议（去重、cap 截断并标注 truncated）。
 */
export function suggestRefinement(
  insights: ExploreInsights,
  pool: FactorMember[],
  bestComboMembers: FactorMember[],
  bestMethod: TrainMethod,
  cap: number = MAX_EXPLORE_CANDIDATES,
): RefinementSuggestion {
  const methods: TrainMethod[] = [bestMethod];
  const identities = pool.map((m) => memberIdentity(m));
  const idToIndex = new Map<string, number>();
  identities.forEach((id, i) => {
    if (!idToIndex.has(id)) idToIndex.set(id, i);
  });
  const bIdx = bestComboMembers
    .map((m) => idToIndex.get(memberIdentity(m)))
    .filter((i): i is number => i != null);
  const bOnly = normalizeCombo(bIdx, pool.length);

  // 样本不足：只回「B 本身」的安全建议（或空），据洞察精修无意义，UI 亦会以 refineDisabledReason 禁用。
  if (!insights.hasEnoughSamples) {
    return { strategy: "manual", methods, pool, combos: bOnly ? [bOnly] : [], rationale: bOnly ? ["上轮最佳，作对照"] : [], truncated: false };
  }

  const liftByIdentity = new Map(insights.factors.map((f) => [f.identity, f.marginalLift] as const));
  const drafts = draftCombos(bIdx, pool, identities, liftByIdentity);
  const maxCombos = Math.max(1, Math.floor(cap / methods.length));

  const seen = new Set<string>();
  const combos: number[][] = [];
  const rationale: string[] = [];
  let truncated = false;
  for (const draft of drafts) {
    const normalized = normalizeCombo(draft.indices, pool.length);
    if (!normalized) continue;
    const key = normalized.map((i) => identities[i]).slice().sort().join("||");
    if (seen.has(key)) continue;
    if (combos.length >= maxCombos) {
      truncated = true;
      continue;
    }
    seen.add(key);
    combos.push(normalized);
    rationale.push(draft.rationale);
  }
  return { strategy: "manual", combos, methods, pool, rationale, truncated };
}
