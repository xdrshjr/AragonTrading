import {
  describeIntegrity, describeSettlement, evidenceVersionLabel, readIntegrity,
  readSettlementValidation, settlementBasisLabel, settlementReasonLabel,
} from "./aiTradeIntegrity";
import { describe, expect, it } from "vitest";
import { groupStateFromStatus } from "./useAiTradeGroupStream";
import { useAppStore } from "./store";
import type { AiTradeGroupRow } from "./aiTradeTypes";

describe("运行事实读取", () => {
  it("REST 重发现与 WS 状态补丁保留同一成员的事实及成交", () => {
    const integrity = { schemaVersion: 2,
      diagnosis: { code: "model_hold", message: "当前证据不足" },
      settlementValidation: { schemaVersion: 1, status: "matched",
        reason: null, feeAdjustment: { kind: "aggregate_residual",
          exchangeTripFeeUsd: 0, bookedLocalFeeUsd: .3,
          residualFeeUsd: -.3, feeBasis: "estimated" } },
      memory: { evidenceVersion: 3, summaryEvidenceVersion: null } };
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

  it("结算零值、负调整与摘要未知版本经过往返仍保持原义", () => {
    const wire = { schemaVersion: 2, settlementValidation: {
      schemaVersion: 1, status: "matched", reason: null,
      detailReason: null, epochKey: "epoch-1", settlementKey: "settled-1",
      asOfTs: 0, identityBasis: "observed_position",
      amountCoverage: "complete", queryCoverage: "single_page",
      priceBasis: "exchange_aggregate_residual", feeAdjustment: {
        kind: "aggregate_residual", exchangeTripFeeUsd: 0,
        bookedLocalFeeUsd: .3, residualFeeUsd: -.3,
        feeBasis: "estimated",
      },
    }, memory: { evidenceVersion: 3, summaryEvidenceVersion: null } };
    expect(readIntegrity(JSON.parse(JSON.stringify(wire)))).toEqual(wire);
  });

  it("旧快照缺失的新字段与显式 null 均不会补成认证结果", () => {
    const legacy = { schemaVersion: 2 };
    const missing = { ...legacy, settlementValidation: null,
      memory: { evidenceVersion: null, summaryEvidenceVersion: null } };
    expect(readIntegrity(legacy)).toEqual(legacy);
    expect(readIntegrity(missing)).toEqual(missing);
  });
});

describe("结算来源与证据文案", () => {
  it("未知结算版本不读取内部金额，未知原因仅使用服务端安全说明", () => {
    expect(readSettlementValidation({ schemaVersion: 99 })).toBeNull();
    expect(describeSettlement({ schemaVersion: 99 }))
      .toBe("此版本结算详情暂不可读");
    expect(settlementReasonLabel("future_reason", "归属仍需核实"))
      .toBe("归属仍需核实");
    expect(settlementReasonLabel("future_reason")).toBe("原因暂不可得");
    expect(settlementBasisLabel("future_basis")).toBe("来源暂不可得");
  });

  it.each([
    ["matched", "本次持仓匹配通过"],
    ["already_claimed", "结算已记账"],
    ["unavailable", "结算暂不可得：结算金额缺失"],
    ["future_status", "结算状态暂不可得：结算金额缺失"],
  ])("状态 %s 不混淆估算与结算", (status, expected) => {
    expect(describeSettlement({ schemaVersion: 1, status,
      reason: "missing_amount" })).toBe(expected);
  });

  it("区分支持的证据版本、旧记录和未来协议", () => {
    expect(evidenceVersionLabel(2)).toBe("历史证据 v2");
    expect(evidenceVersionLabel(3)).toBe("历史证据 v3");
    expect(evidenceVersionLabel(4)).toBe("v4");
    expect(evidenceVersionLabel(null)).toBe("旧记录未提供证据版本");
    expect(evidenceVersionLabel(99)).toBe("此版本证据暂不可读");
    expect(settlementBasisLabel("measured")).toBe("实测");
    expect(settlementBasisLabel("estimated")).toBe("含本地估算");
  });
});
