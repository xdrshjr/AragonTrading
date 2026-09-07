import { describeIntegrity, readIntegrity } from "./aiTradeIntegrity";
import { describe, expect, it } from "vitest";
import { groupStateFromStatus } from "./useAiTradeGroupStream";
import { useAppStore } from "./store";
import type { AiTradeGroupRow } from "./aiTradeTypes";

describe("运行事实读取", () => {
  it("REST 重发现与 WS 状态补丁保留同一成员的事实及成交", () => {
    const integrity = { schemaVersion: 2,
      diagnosis: { code: "model_hold", message: "当前证据不足" } };
    const member = { instId: "SATS-USDT-SWAP", sessionId: "session",
      runId: 1, allocationPct: 100, bar: "1m", state: "running",
      strategyIds: [], strategyNames: [], budgetUsd: 100, leverage: 1,
      contextRefreshRounds: 10, roundsCompleted: 1, roundsSinceSummary: 1,
      positionSide: "flat", sessionPnlUsd: 0, startedTs: 1, integrity };
    const response: AiTradeGroupRow = { groupId: "g", bar: "1m",
      strategyIds: [], strategyNames: [], totalBudgetUsd: 100, leverage: 1,
      contextRefreshRounds: 10, createdTs: 1, members: [member] };
    const group = groupStateFromStatus(response);
    expect(group.members[member.instId].session?.integrity).toBe(integrity);
    useAppStore.setState({ aiTradeGroup: group });
    const trades = group.members[member.instId].trades;
    const fresh = { ...integrity, asOfTs: 2 };
    useAppStore.getState().patchAiTradeMemberStatus(member.instId,
      { state: "running", integrity: fresh });
    const updated = useAppStore.getState().aiTradeGroup!.members[member.instId];
    expect(updated.session?.integrity).toBe(fresh);
    expect(updated.trades).toBe(trades);
    useAppStore.setState({ aiTradeGroup: null });
  });
  it("旧服务端和未知版本保持未知，不能生成零收益", () => {
    expect(readIntegrity(undefined)).toBeNull();
    expect(readIntegrity({ schemaVersion: 99 })).toBeNull();
    expect(describeIntegrity(undefined)).toContain("历史分类不完整");
    expect(describeIntegrity({ schemaVersion: 99 })).toBe("此版本详情暂不可读");
  });

  it("完整保留服务端来源、范围和诊断，不从标的名猜测", () => {
    const wire = { schemaVersion: 2, asOfTs: 123,
      diagnosis: { code: "risk_limited", message: "仓位受总敞口限制" },
      economics: { scope: "run", basis: "local_estimate",
        tradingNetUsd: null, fundingBasis: "unknown" } };
    expect(readIntegrity(wire)).toBe(wire);
    expect(describeIntegrity(wire)).toBe("仓位受总敞口限制");
  });
});
