import { describe, expect, it } from "vitest";
import {
  buildMemberColorMap,
  clampComboPolicy,
  comboPolicySummary,
  DEFAULT_COMBO_POLICY,
  extractComboPolicy,
  groupBreakdownByRegime,
  importComboMembers,
  isCombo,
  isUserCombo,
  MAX_MEMBERS,
  MIN_MEMBERS,
  resolveMemberIds,
  selectedMembers,
  summarizeComboMembers,
  totalTimelineBars,
  validateMemberCount,
} from "./combo";
import { OVERLAY_COLORS } from "./compare";
import type { ComboBreakdownRow, ComboMemberSpec, ComboPolicy, Strategy } from "./types";

// 构造原子 / 组合策略行（仅填测试关心的字段，其余给安全默认）。
function makeStrategy(over: Partial<Strategy> & { id: number }): Strategy {
  return {
    templateKey: "trend_pullback_long",
    name: "策略",
    description: "",
    kind: "atomic",
    params: {},
    paramsSchema: {},
    enabled: true,
    builtin: false,
    ...over,
  };
}

describe("combo draft validation", () => {
  it("rejects fewer than MIN_MEMBERS", () => {
    expect(validateMemberCount(0)).not.toBeNull();
    expect(validateMemberCount(MIN_MEMBERS - 1)).not.toBeNull();
  });

  it("rejects more than MAX_MEMBERS", () => {
    expect(validateMemberCount(MAX_MEMBERS + 1)).not.toBeNull();
  });

  it("accepts a count within [MIN, MAX]", () => {
    expect(validateMemberCount(MIN_MEMBERS)).toBeNull();
    expect(validateMemberCount(MAX_MEMBERS)).toBeNull();
  });
});

describe("clampComboPolicy", () => {
  it("clamps out-of-range numeric params to schema bounds", () => {
    const wild: ComboPolicy = {
      rebalanceBars: 9999,
      lookbackBars: 1,
      selectionMetric: "sharpe",
      minDwellBars: 0,
      switchMarginPct: 5,
      regimeGating: true,
      turnoverBps: 999,
    };
    const clamped = clampComboPolicy(wild);
    expect(clamped.rebalanceBars).toBe(240);
    expect(clamped.lookbackBars).toBe(24);
    expect(clamped.minDwellBars).toBe(1);
    expect(clamped.switchMarginPct).toBe(1);
    expect(clamped.turnoverBps).toBe(50);
  });

  it("falls back to sharpe for an invalid selection metric", () => {
    const bad = { ...DEFAULT_COMBO_POLICY, selectionMetric: "bogus" as ComboPolicy["selectionMetric"] };
    expect(clampComboPolicy(bad).selectionMetric).toBe("sharpe");
  });

  it("rounds integer params and guards non-finite input", () => {
    const policy = { ...DEFAULT_COMBO_POLICY, rebalanceBars: 12.7, turnoverBps: Number.NaN };
    const clamped = clampComboPolicy(policy);
    expect(clamped.rebalanceBars).toBe(13);
    expect(clamped.turnoverBps).toBe(5); // NaN → fallback 5
  });
});

describe("buildMemberColorMap", () => {
  it("assigns stable colors by first appearance and dedupes", () => {
    const map = buildMemberColorMap(["a", "b", "a", "c"]);
    expect(map.get("a")).toBe(OVERLAY_COLORS[0]);
    expect(map.get("b")).toBe(OVERLAY_COLORS[1]);
    expect(map.get("c")).toBe(OVERLAY_COLORS[2]);
    expect(map.size).toBe(3);
  });
});

describe("totalTimelineBars", () => {
  it("sums bars and guards against zero (no divide-by-zero)", () => {
    expect(totalTimelineBars([{ bars: 10 }, { bars: 5 }])).toBe(15);
    expect(totalTimelineBars([])).toBe(1);
    expect(totalTimelineBars([{ bars: 0 }])).toBe(1);
  });
});

describe("groupBreakdownByRegime", () => {
  it("groups rows by regime preserving first-seen order and summing bars", () => {
    const rows: ComboBreakdownRow[] = [
      { regime: "trend_up", memberKey: "a", memberName: "A", bars: 10, contribution: 0.05 },
      { regime: "range", memberKey: "b", memberName: "B", bars: 8, contribution: 0.01 },
      { regime: "trend_up", memberKey: "c", memberName: "C", bars: 4, contribution: 0.02 },
    ];
    const groups = groupBreakdownByRegime(rows);
    expect(groups.map((g) => g.regime)).toEqual(["trend_up", "range"]);
    expect(groups[0].bars).toBe(14);
    expect(groups[0].rows).toHaveLength(2);
    expect(groups[1].bars).toBe(8);
  });
});

describe("isCombo / isUserCombo", () => {
  it("classifies a user combo and excludes builtin / unavailable / atomic rows", () => {
    const atomic = makeStrategy({ id: 1, kind: "atomic" });
    const builtinCombo = makeStrategy({ id: 2, kind: "combo", builtin: true });
    const userCombo = makeStrategy({ id: 3, kind: "combo", builtin: false });
    const deadCombo = makeStrategy({ id: 4, kind: "combo", builtin: false, unavailable: true });
    expect(isCombo(userCombo)).toBe(true);
    expect(isCombo(atomic)).toBe(false);
    expect(isUserCombo(userCombo)).toBe(true);
    expect(isUserCombo(builtinCombo)).toBe(false);
    expect(isUserCombo(deadCombo)).toBe(false);
    expect(isUserCombo(atomic)).toBe(false);
  });
});

describe("selectedMembers / resolveMemberIds round-trip", () => {
  const candidates = [
    makeStrategy({ id: 1, templateKey: "trend_pullback_long", name: "顺势回踩做多", params: { a: 1 } }),
    makeStrategy({ id: 2, templateKey: "range_scalping_strategy", name: "区间剥头皮", params: { b: 2 } }),
  ];

  it("snapshots draft ids to member specs and resolves them back", () => {
    const members = selectedMembers([1, 2], candidates);
    expect(members).toEqual([
      { templateKey: "trend_pullback_long", params: { a: 1 }, name: "顺势回踩做多" },
      { templateKey: "range_scalping_strategy", params: { b: 2 }, name: "区间剥头皮" },
    ]);
    expect(resolveMemberIds(members, candidates).memberIds).toEqual([1, 2]);
  });

  it("matches templateKey+name first, then falls back to templateKey", () => {
    const renamed: ComboMemberSpec[] = [
      { templateKey: "trend_pullback_long", params: {}, name: "旧名字（已改）" },
    ];
    expect(resolveMemberIds(renamed, candidates).memberIds).toEqual([1]);
  });

  it("drops members whose template no longer exists", () => {
    const members: ComboMemberSpec[] = [
      { templateKey: "trend_pullback_long", params: {}, name: "顺势回踩做多" },
      { templateKey: "ghost_template", params: {}, name: "幽灵成员" },
    ];
    const { memberIds, dropped } = resolveMemberIds(members, candidates);
    expect(memberIds).toEqual([1]);
    expect(dropped).toEqual(["幽灵成员"]);
  });
});

describe("importComboMembers", () => {
  const candidates = Array.from({ length: 9 }, (_, i) =>
    makeStrategy({ id: i + 1, templateKey: `tpl_${i + 1}`, name: `策略${i + 1}` }),
  );

  it("unions, dedupes and clamps imported members to MAX_MEMBERS", () => {
    const combo = makeStrategy({
      id: 100,
      kind: "combo",
      params: {
        members: candidates.map((s) => ({ templateKey: s.templateKey, params: {}, name: s.name })),
      },
    });
    const { memberIds, importedCount } = importComboMembers(combo, candidates, [1, 2]);
    expect(memberIds).toHaveLength(MAX_MEMBERS); // 夹取到上限
    expect(memberIds.slice(0, 2)).toEqual([1, 2]); // 现有成员保留在前
    expect(importedCount).toBe(MAX_MEMBERS - 2); // 只新增填满上限的部分
  });

  it("reports unavailable members as dropped without importing them", () => {
    const combo = makeStrategy({
      id: 101,
      kind: "combo",
      params: {
        members: [
          { templateKey: "tpl_3", params: {}, name: "策略3" },
          { templateKey: "missing", params: {}, name: "失效成员" },
        ],
      },
    });
    const { memberIds, importedCount, dropped } = importComboMembers(combo, candidates, []);
    expect(memberIds).toEqual([3]);
    expect(importedCount).toBe(1);
    expect(dropped).toEqual(["失效成员"]);
  });
});

describe("extractComboPolicy", () => {
  it("overlays defaults and clamps out-of-range values", () => {
    const policy = extractComboPolicy({ rebalanceBars: 9999, selectionMetric: "calmar" });
    expect(policy.rebalanceBars).toBe(240); // 夹取到上界
    expect(policy.lookbackBars).toBe(DEFAULT_COMBO_POLICY.lookbackBars); // 缺省回落默认
    expect(policy.selectionMetric).toBe("calmar");
  });

  it("falls back to defaults for an invalid selection metric", () => {
    expect(extractComboPolicy({ selectionMetric: "bogus" }).selectionMetric).toBe(
      DEFAULT_COMBO_POLICY.selectionMetric,
    );
  });
});

describe("combo summaries", () => {
  it("renders stable policy and member summary text", () => {
    expect(comboPolicySummary({ ...DEFAULT_COMBO_POLICY, selectionMetric: "sharpe", regimeGating: true }))
      .toBe("夏普选优 · 体制门控");
    expect(comboPolicySummary({ ...DEFAULT_COMBO_POLICY, selectionMetric: "return", regimeGating: false }))
      .toBe("收益率选优");
    const combo = makeStrategy({
      id: 1,
      kind: "combo",
      params: {
        members: [
          { templateKey: "a", params: {} },
          { templateKey: "b", params: {} },
        ],
        selectionMetric: "calmar",
        regimeGating: true,
      },
    });
    expect(summarizeComboMembers(combo)).toBe("2 个成员 · 卡玛选优 · 体制门控");
  });
});
