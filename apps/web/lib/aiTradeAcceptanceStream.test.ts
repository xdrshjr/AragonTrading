import { beforeEach, describe, expect, it } from "vitest";
import { groupStateFromStatus } from "./useAiTradeGroupStream";
import { useAppStore } from "./store";
import type { AiTradeGroupRow, AiTradeGroupState } from "./aiTradeTypes";

// 验收闭环的两处接线守门（ai-trade-horizon-acceptance-loop §6.2 / [P0-3] / [P1-10]）。
//
// **守对对象**是这两条用例的全部价值：
// - `groupStateFromStatus` 是**逐键手抄**的映射，它才是会静默吞新键的那个函数
//   （`patchAiTradeMemberStatus` 是整体 spread，本来就不吞键——守它等于没守）；
// - `patchAiTradeGroupAcceptance` 必须只覆盖三个加法键：用 `setAiTradeGroup` 做这件事会把
//   每个成员的 `trades` 清空，而它跑在一个 15 秒轮询里。

const ROW: AiTradeGroupRow = {
  groupId: "g1",
  bar: "1H",
  strategyIds: [1],
  strategyNames: ["S"],
  totalBudgetUsd: 1000,
  leverage: 3,
  contextRefreshRounds: 10,
  createdTs: 1_700_000_000,
  members: [
    {
      instId: "BTC-USDT-SWAP",
      sessionId: "s1",
      allocationPct: 100,
      runId: 1,
      state: "running",
      bar: "1H",
      strategyIds: [1],
      strategyNames: ["S"],
      budgetUsd: 1000,
      leverage: 3,
      contextRefreshRounds: 10,
      roundsCompleted: 0,
      roundsSinceSummary: 0,
      positionSide: "flat",
      sessionPnlUsd: 0,
      startedTs: 1_700_000_000,
    },
  ] as AiTradeGroupRow["members"],
  acceptance: {
    profileId: "short_swing",
    title: "短波段",
    verdict: "insufficient",
    sampleFloor: 20,
    roundTrips: 3,
    truncatedSamples: 0,
    sampleNote: "样本 3 / 20",
    rows: [],
    diagnostics: [],
  },
  exits: { totalExits: 3, bySource: [{ source: "agent", count: 3, share: 1, netUsd: -1 }] },
  funding: {
    enabled: true,
    basis: "accrued",
    accruedClosedUsd: 0.1,
    accruedOpenUsd: null,
    settledUsd: null,
    fundingCostUsd: 0.1,
    crossings: 1,
    intervalKnown: true,
    intervalS: 28800,
    lastRateBps: 1,
    staleTicks: 0,
    unknownIntervalTicks: 0,
    capHits: 0,
    settledTrusted: false,
    note: "本地计提",
  },
};

describe("groupStateFromStatus — 逐键手抄的那个函数不许吞新键", () => {
  it("三个加法键经映射后非空（不补映射就永远拿不到）", () => {
    const state = groupStateFromStatus(ROW);
    expect(state.acceptance?.roundTrips).toBe(3);
    expect(state.exits?.totalExits).toBe(3);
    expect(state.funding?.basis).toBe("accrued");
  });

  it("老后端不下发三键时映射为 null（而不是 undefined 漏进 store）", () => {
    const { acceptance, exits, funding, ...legacy } = ROW;
    const state = groupStateFromStatus(legacy as AiTradeGroupRow);
    expect(state.acceptance).toBeNull();
    expect(state.exits).toBeNull();
    expect(state.funding).toBeNull();
  });
});

describe("patchAiTradeGroupAcceptance — 轮询绝不清空成交流", () => {
  beforeEach(() => {
    localStorage.clear();
    useAppStore.getState().setAiTradeGroup(groupStateFromStatus(ROW));
    useAppStore.getState().setAiTradeMemberTrades("BTC-USDT-SWAP", [
      { ts: 1, side: "long", price: 100, qty: 1, fee: 0, pnl: 0 },
    ] as never);
  });

  it("连续两次补丁后 members 与各成员 trades 引用不变", () => {
    const before = useAppStore.getState().aiTradeGroup as AiTradeGroupState;
    const membersRef = before.members;
    const tradesRef = before.members["BTC-USDT-SWAP"].trades;

    const patch = { acceptance: null, exits: ROW.exits!, funding: ROW.funding! };
    useAppStore.getState().patchAiTradeGroupAcceptance(patch);
    useAppStore.getState().patchAiTradeGroupAcceptance(patch);

    const after = useAppStore.getState().aiTradeGroup as AiTradeGroupState;
    expect(after.members).toBe(membersRef);
    expect(after.members["BTC-USDT-SWAP"].trades).toBe(tradesRef);
    expect(after.members["BTC-USDT-SWAP"].trades).toHaveLength(1);
  });

  it("只覆盖三个加法键，其余分组字段原样保留", () => {
    useAppStore.getState().patchAiTradeGroupAcceptance({
      acceptance: null,
      exits: null,
      funding: null,
    });
    const after = useAppStore.getState().aiTradeGroup as AiTradeGroupState;
    expect(after.groupId).toBe("g1");
    expect(after.totalBudgetUsd).toBe(1000);
    expect(after.acceptance).toBeNull();
  });

  it("无活跃分组时静默空转（轮询与分组生命周期天然有竞态）", () => {
    useAppStore.getState().setAiTradeGroup(null);
    useAppStore.getState().patchAiTradeGroupAcceptance({
      acceptance: null,
      exits: null,
      funding: null,
    });
    expect(useAppStore.getState().aiTradeGroup).toBeNull();
  });
});
