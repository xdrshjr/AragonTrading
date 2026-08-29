import { describe, expect, it } from "vitest";
import {
  DEFAULT_DISCIPLINE_PRESET,
  DISCIPLINE_COPY,
  DISCIPLINE_PRESETS,
  describeContrarianQuota,
  disciplineRow,
  formatFiredCount,
  isNakedPosition,
  memberDisciplines,
  summarizeDisciplines,
} from "./aiTradeDiscipline";
import { useAppStore } from "./store";
import { groupStateFromStatus } from "./useAiTradeGroupStream";
import type {
  AiTradeDisciplineRow,
  AiTradeDisciplineWire,
  AiTradeGroupRow,
} from "./aiTradeTypes";

function row(over: Partial<AiTradeDisciplineRow> = {}): AiTradeDisciplineRow {
  return {
    key: "edgeGate",
    label: "最小 edge 门禁",
    armed: true,
    threshold: "毛 edge ≥ 1.5 × 成本",
    source: "preset",
    note: null,
    firedCount: 0,
    tone: null,
    ...over,
  };
}

describe("summarizeDisciplines", () => {
  it("counts only what the backend says is armed (never re-derives the judgement)", () => {
    const summary = summarizeDisciplines([
      row({ key: "a", armed: true }),
      row({ key: "b", armed: false }),
      row({ key: "c", armed: true }),
    ]);
    expect(summary.armed).toBe(2);
    expect(summary.total).toBe(3);
    expect(summary.label).toBe("已生效 2/3");
  });

  it("returns a null label for an empty digest so the strip does not render an empty shell", () => {
    expect(summarizeDisciplines([]).label).toBeNull();
    expect(summarizeDisciplines(undefined).label).toBeNull();
    expect(summarizeDisciplines(null).label).toBeNull();
  });

  it("surfaces danger and warn tones separately — a warning must not read as an alarm", () => {
    const summary = summarizeDisciplines([
      row({ key: "exchangeBackstop", armed: false, tone: "danger" }),
      row({ key: "volSizing", armed: true, tone: "warn" }),
      row({ key: "atrStop", armed: true }),
    ]);
    expect(summary.danger).toBe(true);
    expect(summary.warnings).toBe(1);
  });
});

describe("isNakedPosition", () => {
  // [P0-3]：判据只读后端已经判好的那一行。在前端把三态覆盖率判据再写一遍，就是两份口径。
  it("is true only when the backstop row itself is marked dangerous", () => {
    expect(isNakedPosition([row({ key: "exchangeBackstop", armed: false, tone: "danger" })]))
      .toBe(true);
  });

  it("stays false for the unverified state — a false alarm would void the real one", () => {
    expect(isNakedPosition([
      row({ key: "exchangeBackstop", armed: false, tone: null, note: "尚未向交易所核实覆盖率" }),
    ])).toBe(false);
  });

  it("stays false when covered, when the row is missing, and for an empty digest", () => {
    expect(isNakedPosition([row({ key: "exchangeBackstop", armed: true })])).toBe(false);
    expect(isNakedPosition([row({ key: "edgeGate", armed: true, tone: "danger" })])).toBe(false);
    expect(isNakedPosition([])).toBe(false);
    expect(isNakedPosition(undefined)).toBe(false);
  });
});

describe("formatFiredCount", () => {
  // 「算不出」与「一次都没触发」是两个结论，绝不可互相冒充。
  it("renders an em dash for null and a real count for zero", () => {
    expect(formatFiredCount(null)).toBe("—");
    expect(formatFiredCount(undefined)).toBe("—");
    expect(formatFiredCount(0)).toBe("0 次");
    expect(formatFiredCount(7)).toBe("7 次");
  });
});

describe("memberDisciplines", () => {
  const member: AiTradeDisciplineWire = {
    preset: "balanced", presetLabel: "均衡",
    rows: [row({ key: "exchangeBackstop", armed: false, tone: "danger" })],
  };
  const group: AiTradeDisciplineWire = {
    preset: "balanced", presetLabel: "均衡",
    rows: [row({ key: "exchangeBackstop", armed: true })],
  };

  it("prefers the member digest — the backstop row is the one that forks per symbol", () => {
    expect(memberDisciplines(member, group)).toBe(member);
  });

  it("falls back to the group receipt only when the member has no rows yet", () => {
    expect(memberDisciplines(undefined, group)).toBe(group);
    expect(memberDisciplines({ rows: [] }, group)).toBe(group);
    expect(memberDisciplines(undefined, undefined)).toBeNull();
  });
});

describe("disciplineRow", () => {
  it("returns null instead of throwing when an older backend omits a row", () => {
    expect(disciplineRow([row({ key: "edgeGate" })], "edgeGate")?.key).toBe("edgeGate");
    expect(disciplineRow([row({ key: "edgeGate" })], "signalPrior")).toBeNull();
    expect(disciplineRow(undefined, "edgeGate")).toBeNull();
  });
});

describe("preset copy", () => {
  it("covers every preset id and never claims the thresholds are calibrated", () => {
    for (const preset of DISCIPLINE_PRESETS) {
      const copy = DISCIPLINE_COPY[preset];
      expect(copy.label.length).toBeGreaterThan(0);
      expect(copy.detail.length).toBeGreaterThan(0);
      expect(copy.detail).not.toMatch(/已校准|经回测验证/);
    }
    expect(DISCIPLINE_PRESETS).toContain(DEFAULT_DISCIPLINE_PRESET);
  });
});

describe("groupStateFromStatus", () => {
  // 这个函数正是本仓库「静默吞新键」缺陷的那一个（逐键手抄的映射）。漏抄 `discipline` 时症状很隐蔽：
  // WS status 那条通路仍然是好的，只有「刷新页面后重发现」才会拿到一个空的纪律条。
  const wire: AiTradeDisciplineWire = {
    preset: "steady", presetLabel: "稳健",
    adjustments: [{ field: "minConsensus", from: 2, to: 1, reason: "已按本会话策略数夹紧" }],
    rows: [row()], hint: null,
  };
  const groupRow: AiTradeGroupRow = {
    groupId: "g1", bar: "1H", strategyIds: [1], strategyNames: ["S"], totalBudgetUsd: 100,
    leverage: 3, contextRefreshRounds: 10, createdTs: 1, members: [], discipline: wire,
  };

  it("carries the discipline receipt through the hand-copied mapping", () => {
    expect(groupStateFromStatus(groupRow).discipline).toEqual(wire);
  });

  it("degrades to null (not undefined) when an older backend omits the key", () => {
    const { discipline: _omit, ...withoutDiscipline } = groupRow;
    expect(groupStateFromStatus(withoutDiscipline as AiTradeGroupRow).discipline).toBeNull();
  });

  // directional-symmetry §7.1：`direction` 是**分组级**回执，落在同一个逐键手抄的映射里。
  // 漏抄它的症状与上面那条逐字同形：WS 通路好的，只有「刷新页面后重发现」拿不到生效值。
  it("carries the directional-symmetry receipt through the same hand-copied mapping", () => {
    const direction = {
      contrarianEnabled: true, minConsensus: 1, contrarianMinConfidence: 0.65,
      contrarianMaxOpposingVotes: 1, contrarianMinEvidenceChars: 60,
      contrarianMaxPerSession: 3, contrarianSizeMult: 0.5,
    };
    expect(groupStateFromStatus({ ...groupRow, direction }).direction).toEqual(direction);
    expect(groupStateFromStatus(groupRow).direction).toBeNull();
  });
});

describe("describeContrarianQuota", () => {
  // [P2-4]：配额不落库、冷续跑归零是有意的取舍，但「本会话已用 N/M」必须让用户看得见——
  // 否则「重启刷配额」是一个既存在又不可见的行为。
  it("says how much of the session quota is spent and that it resets on restart", () => {
    const text = describeContrarianQuota({ used: 1, remaining: 2, maxPerSession: 3 }, {
      contrarianEnabled: true, contrarianMinConfidence: 0.65, contrarianSizeMult: 0.5,
    });
    expect(text).toContain("本会话已用 1/3");
    expect(text).toContain("置信度 ≥0.65");
    expect(text).toContain("仓位 ×0.5");
    expect(text).toContain("重启会话后重新计数");
  });

  it("renders nothing when the lane is not armed (never a row of dashes)", () => {
    expect(describeContrarianQuota(null)).toBeNull();
    expect(describeContrarianQuota(undefined, null)).toBeNull();
  });

  it("omits a threshold the receipt does not carry instead of inventing one", () => {
    const text = describeContrarianQuota({ used: 0, remaining: 3, maxPerSession: 3 }, null);
    expect(text).toContain("本会话已用 0/3");
    expect(text).not.toContain("置信度");
    expect(text).not.toContain("仓位 ×");
  });
});

describe("naked-position fact reaches the UI through the status channel alone (G-7c)", () => {
  // [P0-2]：`routeMemberEvent` **没有** `error` 分支（`useAiTradeGroupStream.ts` 的 switch 只有七个
  // 分支），任何依赖 error 事件的告警都会被静默丢弃。故裸仓事实唯一的落点是 `status.disciplines`，
  // 它经 `patchAiTradeMemberStatus` 的**整体 spread** 合并进 `m.session`（与逐键手抄的
  // `groupStateFromStatus` 不同源）。这条用例直接钉死那条通路。
  const covered = row({ key: "exchangeBackstop", armed: true, tone: null });
  const naked = row({
    key: "exchangeBackstop", armed: false, tone: "danger",
    note: "⚠️ 当前持仓在交易所侧没有兜底止损（交易所侧一条止损都没有）。请立即补挂。",
  });

  it("delivers a covered→naked flip without any error event", () => {
    const store = useAppStore.getState();
    store.setAiTradeGroup(
      groupStateFromStatus({
        groupId: "g1", bar: "1H", strategyIds: [1], strategyNames: ["S"],
        totalBudgetUsd: 100, leverage: 3, contextRefreshRounds: 10, createdTs: 1,
        members: [
          {
            instId: "BTC-USDT-SWAP", sessionId: "s1", allocationPct: 100, runId: 1,
            state: "running", bar: "1H", strategyIds: [1], strategyNames: ["S"],
            budgetUsd: 100, leverage: 3, contextRefreshRounds: 10, roundsCompleted: 0,
            roundsSinceSummary: 0, positionSide: "long", sessionPnlUsd: 0, startedTs: 1,
          },
        ],
      }),
    );
    useAppStore.getState().patchAiTradeMemberStatus("BTC-USDT-SWAP", {
      state: "running", disciplines: { rows: [covered], hint: null },
    });
    const before = useAppStore.getState().aiTradeGroup?.members["BTC-USDT-SWAP"];
    expect(isNakedPosition(before?.session?.disciplines?.rows)).toBe(false);

    useAppStore.getState().patchAiTradeMemberStatus("BTC-USDT-SWAP", {
      state: "running", disciplines: { rows: [naked], hint: null },
    });
    const after = useAppStore.getState().aiTradeGroup?.members["BTC-USDT-SWAP"];
    expect(isNakedPosition(after?.session?.disciplines?.rows)).toBe(true);
    expect(after?.session?.disciplines?.rows[0].note).toContain("补挂");
    useAppStore.getState().clearAiTradeRuntime();
  });
});
