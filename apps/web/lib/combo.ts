// 策略组合（regime rotation）纯助手：草稿校验、参数夹取、时间线配色、分体制贡献汇总，
// 以及用户组合判定、成员快照 ↔ 草稿 id 往返、组合摘要文案。
// 与 format.ts / compare.ts 同约定：纯函数 + 同名 combo.test.ts 单测，组件仅消费此处逻辑。
import { OVERLAY_COLORS } from "./compare";
import { SELECTION_METRIC_LABELS } from "./comboPolicyHints";
import type { ComboBreakdownRow, ComboMemberSpec, ComboPolicy, Strategy } from "./types";

export const MIN_MEMBERS = 2;
export const MAX_MEMBERS = 8;

// 轮动参数默认值（镜像后端 regime_rotation 模板默认，单一真源在后端，此处仅 UI 初值）。
export const DEFAULT_COMBO_POLICY: ComboPolicy = {
  rebalanceBars: 24,
  lookbackBars: 168,
  selectionMetric: "sharpe",
  minDwellBars: 24,
  switchMarginPct: 0.1,
  regimeGating: true,
  turnoverBps: 5.0,
};

// 组合草稿（持久化于 store）：成员策略 id 列表 + 轮动参数。
export interface ComboDraft {
  memberIds: number[];
  policy: ComboPolicy;
}

export const DEFAULT_COMBO_DRAFT: ComboDraft = {
  memberIds: [],
  policy: { ...DEFAULT_COMBO_POLICY },
};

// 校验成员数；合法返回 null，否则返回中文错误说明（供按钮禁用态文案）。
export function validateMemberCount(count: number): string | null {
  if (count < MIN_MEMBERS) return `至少选择 ${MIN_MEMBERS} 个成员`;
  if (count > MAX_MEMBERS) return `最多选择 ${MAX_MEMBERS} 个成员`;
  return null;
}

function clampNumber(value: number, min: number, max: number, fallback: number): number {
  if (!Number.isFinite(value)) return fallback;
  return Math.min(Math.max(value, min), max);
}

// 把轮动参数夹取到与后端 params_schema 一致的合法区间（防越界提交）。
export function clampComboPolicy(policy: ComboPolicy): ComboPolicy {
  const metric: ComboPolicy["selectionMetric"] = ["return", "sharpe", "calmar"].includes(
    policy.selectionMetric,
  )
    ? policy.selectionMetric
    : "sharpe";
  return {
    rebalanceBars: Math.round(clampNumber(policy.rebalanceBars, 4, 240, 24)),
    lookbackBars: Math.round(clampNumber(policy.lookbackBars, 24, 1000, 168)),
    selectionMetric: metric,
    minDwellBars: Math.round(clampNumber(policy.minDwellBars, 1, 240, 24)),
    switchMarginPct: clampNumber(policy.switchMarginPct, 0, 1, 0.1),
    regimeGating: Boolean(policy.regimeGating),
    turnoverBps: clampNumber(policy.turnoverBps, 0, 50, 5),
  };
}

// 为时间线/图例分配稳定配色：按成员 key 在去重有序列表中的位置取色（复用对比配色板）。
export function buildMemberColorMap(memberKeys: string[]): Map<string, string> {
  const map = new Map<string, string>();
  let index = 0;
  for (const key of memberKeys) {
    if (map.has(key)) continue;
    map.set(key, OVERLAY_COLORS[index % OVERLAY_COLORS.length]);
    index += 1;
  }
  return map;
}

// 时间线总根数（用于分段宽度百分比；空或非正回落 1 防除零）。
export function totalTimelineBars(segments: { bars: number }[]): number {
  const total = segments.reduce((sum, seg) => sum + Math.max(0, seg.bars), 0);
  return total > 0 ? total : 1;
}

export interface RegimeGroup {
  regime: string;
  bars: number;
  rows: ComboBreakdownRow[];
}

// 把分体制贡献行按体制分组（保持首次出现顺序），用于结果区分组展示。
export function groupBreakdownByRegime(rows: ComboBreakdownRow[]): RegimeGroup[] {
  const order: string[] = [];
  const byRegime = new Map<string, RegimeGroup>();
  for (const row of rows) {
    let group = byRegime.get(row.regime);
    if (!group) {
      group = { regime: row.regime, bars: 0, rows: [] };
      byRegime.set(row.regime, group);
      order.push(row.regime);
    }
    group.bars += row.bars;
    group.rows.push(row);
  }
  return order.map((regime) => byRegime.get(regime) as RegimeGroup);
}

// ============================ 用户组合判定与往返 ============================

// 组合策略（kind==="combo"）。
export function isCombo(s: Strategy): boolean {
  return s.kind === "combo";
}

// 用户自定义组合：是组合、且非内置（regime_rotation 模板行）、非失效（模板已删）。
// 「我的组合策略」列表与成员选择器的「组合策略」页签均以此口径过滤。
export function isUserCombo(s: Strategy): boolean {
  return isCombo(s) && !s.builtin && !s.unavailable;
}

// 读组合行 params 里的成员快照数组（缺失或类型不符回退空数组）。
function membersOf(combo: Strategy): ComboMemberSpec[] {
  const raw = combo.params?.members;
  return Array.isArray(raw) ? (raw as ComboMemberSpec[]) : [];
}

// 正向构造器（R4c）：把草稿成员 id 按当前原子策略行快照为成员规格（保存 / 预览均依赖）。
export function selectedMembers(
  memberIds: number[],
  atomicCandidates: Strategy[],
): ComboMemberSpec[] {
  return memberIds
    .map((id) => atomicCandidates.find((s) => s.id === id))
    .filter((s): s is Strategy => Boolean(s))
    .map((s) => ({ templateKey: s.templateKey, params: s.params, name: s.name }));
}

// 把保存组合的成员快照映射回当前原子策略行 id：templateKey+name 优先，其次首个 templateKey 相等行；
// 都不命中则跳过并计入 dropped（与后端 _build_members 跳过失效成员语义一致）。去重保序。
export function resolveMemberIds(
  savedMembers: ComboMemberSpec[],
  atomicCandidates: Strategy[],
): { memberIds: number[]; dropped: string[] } {
  const memberIds: number[] = [];
  const dropped: string[] = [];
  for (const member of savedMembers) {
    const exact = atomicCandidates.find(
      (s) => s.templateKey === member.templateKey && s.name === member.name,
    );
    const byTemplate = atomicCandidates.find((s) => s.templateKey === member.templateKey);
    const match = exact ?? byTemplate;
    if (!match) {
      dropped.push(member.name ?? member.templateKey);
      continue;
    }
    if (!memberIds.includes(match.id)) memberIds.push(match.id);
  }
  return { memberIds, dropped };
}

// 从组合 Strategy.params 取出轮动参数：叠加默认 + 夹取合法区间（复用 clampComboPolicy）。
export function extractComboPolicy(params: Record<string, unknown>): ComboPolicy {
  const num = (value: unknown, fallback: number): number => {
    const n = typeof value === "number" ? value : Number(value);
    return Number.isFinite(n) ? n : fallback;
  };
  const metric = params.selectionMetric;
  const raw: ComboPolicy = {
    rebalanceBars: num(params.rebalanceBars, DEFAULT_COMBO_POLICY.rebalanceBars),
    lookbackBars: num(params.lookbackBars, DEFAULT_COMBO_POLICY.lookbackBars),
    selectionMetric:
      metric === "return" || metric === "sharpe" || metric === "calmar"
        ? metric
        : DEFAULT_COMBO_POLICY.selectionMetric,
    minDwellBars: num(params.minDwellBars, DEFAULT_COMBO_POLICY.minDwellBars),
    switchMarginPct: num(params.switchMarginPct, DEFAULT_COMBO_POLICY.switchMarginPct),
    regimeGating:
      params.regimeGating === undefined
        ? DEFAULT_COMBO_POLICY.regimeGating
        : Boolean(params.regimeGating),
    turnoverBps: num(params.turnoverBps, DEFAULT_COMBO_POLICY.turnoverBps),
  };
  return clampComboPolicy(raw);
}

// 把组合成员展开导入当前草稿：解析为原子 id → 与当前并集 + 去重 + 夹取 MAX_MEMBERS。
// 返回 { memberIds: 合并后, importedCount: 实际新增个数, dropped: 不可用成员名 }。组合不嵌套组合。
export function importComboMembers(
  combo: Strategy,
  atomicCandidates: Strategy[],
  currentMemberIds: number[],
): { memberIds: number[]; importedCount: number; dropped: string[] } {
  const { memberIds: resolved, dropped } = resolveMemberIds(membersOf(combo), atomicCandidates);
  const merged = [...currentMemberIds];
  let importedCount = 0;
  for (const id of resolved) {
    if (merged.length >= MAX_MEMBERS) break;
    if (!merged.includes(id)) {
      merged.push(id);
      importedCount += 1;
    }
  }
  return { memberIds: merged, importedCount, dropped };
}

// 组合策略文案摘要（如「夏普选优 · 体制门控」），列表行与详情弹窗复用。
export function comboPolicySummary(policy: ComboPolicy): string {
  const metric = SELECTION_METRIC_LABELS[policy.selectionMetric] ?? policy.selectionMetric;
  const parts = [`${metric}选优`];
  if (policy.regimeGating) parts.push("体制门控");
  return parts.join(" · ");
}

// 组合成员 + 轮动摘要（如「4 个成员 · 夏普选优 · 体制门控」），「我的组合策略」列表行展示。
export function summarizeComboMembers(combo: Strategy): string {
  const memberCount = membersOf(combo).length;
  const policy = extractComboPolicy(combo.params ?? {});
  return `${memberCount} 个成员 · ${comboPolicySummary(policy)}`;
}
