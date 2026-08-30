import { describe, expect, it } from "vitest";

import type { AiTradeIntelSummary } from "./intelTypes";
import { useAppStore } from "./store";

const INST = "BTC-USDT-SWAP";

/**
 * 情报 summary 的两块新读数（ai-trade-intel-closed-loop §4.2）必须**原样**到达 store。
 *
 * 已实读确认前端零改动即可带上新键：`store.ts` 的 `setAiTradeIntelSummary` 是**整对象替换**，
 * 两个上游（快照路径与 `case "intel"`）同样是整对象透传。本仓库「`store.ts` 逐键手抄会静默吞
 * WS 新键」的教训在这条通路上不成立。
 *
 * **但这件事仍然要被钉住**：某次重构把整对象换成逐键解构时，唯一的症状会是「界面上那两块永远
 * 是 0」，而不是一条报错。
 */
function summary(over: Partial<AiTradeIntelSummary> = {}): AiTradeIntelSummary {
  return {
    instId: INST,
    counts: { ultra: 2, mid: 1, long: 0, archived: 3 },
    unread: 1,
    lastSeenTs: 0,
    lastCollectTs: 1,
    lastCurateTs: null,
    nextDueTs: null,
    inFlight: false,
    degrade: null,
    providerConfigured: true,
    dailyUsed: 4,
    dailyCap: 96,
    citations: { cited: 12, entryCited: 5, settledTrips: 4, winCount: 3, netPnlUsd: 18.4,
      neverCited: 7 },
    verify: { confirmed: 2, refuted: 1, unresolved: 3, pending: 9, lastVerifyTs: 1_700_000 },
    quoteHitRate: null,
    ...over,
  };
}

describe("setAiTradeIntelSummary · closed-loop 的两块新读数", () => {
  it("`citations` 与 `verify` 原样进 store（整对象替换，不逐键手抄）", () => {
    useAppStore.setState({ aiTradeIntel: {} });
    const next = summary();

    useAppStore.getState().setAiTradeIntelSummary(INST, next);

    const stored = useAppStore.getState().aiTradeIntel[INST];
    expect(stored?.citations).toEqual(next.citations);
    expect(stored?.verify).toEqual(next.verify);
    expect(stored).toEqual(next);
  });

  it("后一次推送整体替换前一次（不做深合并，避免半新半旧的读数）", () => {
    useAppStore.setState({ aiTradeIntel: {} });
    useAppStore.getState().setAiTradeIntelSummary(INST, summary());
    useAppStore.getState().setAiTradeIntelSummary(INST, summary({
      citations: { cited: 0, entryCited: 0, settledTrips: 0, winCount: 0, netPnlUsd: 0,
        neverCited: 0 },
    }));

    expect(useAppStore.getState().aiTradeIntel[INST]?.citations.cited).toBe(0);
    expect(useAppStore.getState().aiTradeIntel[INST]?.verify.pending).toBe(9);
  });

  it("`null` 是 no-op：一次瞬时的空推送不该把已有的读数抹掉", () => {
    // 快照路径写的是 `(snap as {intel?…}).intel ?? null`——一次尚未装配好的快照会推来 `null`，
    // 而那不该让弹窗上的未读徽标与两块读数当场清零（清理由停止 / 结束那条路径负责）。
    useAppStore.setState({ aiTradeIntel: {} });
    useAppStore.getState().setAiTradeIntelSummary(INST, summary());
    useAppStore.getState().setAiTradeIntelSummary(INST, null);

    expect(useAppStore.getState().aiTradeIntel[INST]?.verify.pending).toBe(9);
  });
});
