// 策略组合（regime rotation）纯助手：草稿校验、参数夹取、时间线配色、分体制贡献汇总。
// 与 format.ts / compare.ts 同约定：纯函数 + 同名 combo.test.ts 单测，组件仅消费此处逻辑。
import { OVERLAY_COLORS } from "./compare";
import type { ComboBreakdownRow, ComboPolicy } from "./types";

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
