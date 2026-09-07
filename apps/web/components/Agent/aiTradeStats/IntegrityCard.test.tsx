import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it } from "vitest";
import type { IntegritySnapshot } from "@/lib/aiTradeIntegrity";
import IntegrityCard from "./IntegrityCard";

afterEach(cleanup);

it("展开详情保留服务端结构化原因", () => {
  render(<IntegrityCard instId="BTC-USDT-SWAP" integrity={{
    schemaVersion: 2,
    diagnosis: { code: "consensus", message: "同向资格不足",
      details: { reason: "low_consensus_quota(0/1)" } },
  }} />);
  expect(screen.getByText("low_consensus_quota(0/1)")).toBeInTheDocument();
});

it("未知快照版本不解释其中的经济或漏斗读数", () => {
  render(<IntegrityCard instId="BTC-USDT-SWAP" integrity={{
    schemaVersion: 99,
    diagnosis: { code: "model_hold", message: "不应读取的新协议" },
    economics: { scope: "run", basis: "local_estimate", tradingNetUsd: 123 },
  }} />);
  expect(screen.getByText("此版本详情暂不可读")).toBeInTheDocument();
  expect(screen.queryByText("不应读取的新协议")).toBeNull();
  expect(screen.queryByText("123.00 USDT")).toBeNull();
});

it("未知成本不冒充绿色零，诊断和覆盖可键盘读取", () => {
  render(<IntegrityCard instId="SATS-USDT-SWAP" integrity={{
    schemaVersion: 2, asOfTs: 100, runStartedTs: 1,
    sampleThroughRound: 8,
    diagnosis: { code: "pending_unknown", message: "回执待核实" },
    entryQuota: { remaining: 0, coverage: "legacy_unknown" },
    economics: { scope: "run", basis: "local_estimate",
      coverage: "partial", tradingNetUsd: null,
      fundingCostUsd: null, netAfterFundingUsd: null },
  }} />);
  expect(screen.getByTitle("回执待核实")).toBeInTheDocument();
  expect(screen.getByText("交易净额（含双腿手续费）")).toBeInTheDocument();
  expect(screen.getAllByText("暂不可得").length).toBeGreaterThan(2);
  expect(screen.getByText(/旧额度不可确认/)).toBeInTheDocument();
  expect(screen.getByText("展开运行明细").tagName).toBe("SUMMARY");
});

it("本地与官方结果分开，资金费与调整各有明确标签", () => {
  render(<IntegrityCard instId="BTC-USDT-SWAP" integrity={{
    schemaVersion: 2,
    diagnosis: { code: "model_hold", message: "模型自主观望" },
    economics: { scope: "run", basis: "mixed", coverage: "complete",
      tradingNetUsd: 8.3, fundingCostUsd: .3, netAfterFundingUsd: 8,
      exchangeTradingNetUsd: 8.3, exchangeNetAfterFundingUsd: 8,
      exchangeRealizedPnlUsd: 7.8, liquidationCostUsd: .4,
      settlementAdjustmentUsd: .2 },
  }} />);
  expect(screen.getByText("官方最终已实现结果")).toBeInTheDocument();
  expect(screen.getByText("清算费用")).toBeInTheDocument();
  expect(screen.getByText("结算调整")).toBeInTheDocument();
});

function settlementWire(overrides: Record<string, unknown> = {}) {
  return { schemaVersion: 2, settlementValidation: {
    schemaVersion: 1, status: "matched", reason: null, detailReason: null,
    asOfTs: 123, epochKey: "epoch-1", settlementKey: "settlement-1",
    identityBasis: "observed_position", amountCoverage: "complete",
    queryCoverage: "single_page", priceBasis: "exchange_aggregate_residual",
    feeAdjustment: { kind: "aggregate_residual", exchangeTripFeeUsd: 0,
      bookedLocalFeeUsd: .3, residualFeeUsd: -.3, feeBasis: "estimated" },
    ...overrides,
  }, memory: { evidenceVersion: 3, summaryEvidenceVersion: null,
    legacyNarrative: true } } as IntegritySnapshot;
}

it("上次结算标明持仓依据、费用调整来源及未认证的旧摘要", () => {
  render(<IntegrityCard instId="SOL-USDT-SWAP"
    integrity={settlementWire()} />);
  expect(screen.getByText("结算依据")).toBeInTheDocument();
  expect(screen.getByText("上次结算")).toBeInTheDocument();
  expect(screen.getByText("本次持仓匹配通过")).toBeInTheDocument();
  expect(screen.getByText(/交易所持仓观察/)).toBeInTheDocument();
  expect(screen.getByText(/累计成交差额/)).toBeInTheDocument();
  expect(screen.getByText(/单页查询/)).toBeInTheDocument();
  expect(screen.getByText("往返费用调整")).toBeInTheDocument();
  expect(screen.getByText(/费用来源：含本地估算/)).toBeInTheDocument();
  expect(screen.getByText("0.00 USDT")).toBeInTheDocument();
  expect(screen.getByText("-0.30 USDT")).toBeInTheDocument();
  expect(screen.queryByText(/手续费返现/)).not.toBeInTheDocument();
  expect(screen.getByText("上下文证据版本")).toBeInTheDocument();
  expect(screen.getByText(/当前记录 v3/)).toBeInTheDocument();
  expect(screen.getByText(/摘要证据未确认/)).toBeInTheDocument();
  expect(screen.getByText(/包含旧叙述/)).toBeInTheDocument();
});

it("本地估算保留完整服务端长原因，截止原因缺失时使用具体失败原因", () => {
  const message = "结算身份与本运行不符".repeat(40);
  const { rerender } = render(<IntegrityCard instId="SATS-USDT-SWAP"
    integrity={settlementWire({ status: "local_estimate",
      reason: "deadline_exceeded", detailReason: "future_reason", message,
      feeAdjustment: null })} />);
  expect(screen.getByText(`本地估算：${message}`)).toBeInTheDocument();
  rerender(<IntegrityCard instId="SATS-USDT-SWAP"
    integrity={settlementWire({ status: "local_estimate",
      reason: "deadline_exceeded", detailReason: "missing_amount",
      feeAdjustment: null })} />);
  expect(screen.getByText("本地估算：结算金额缺失")).toBeInTheDocument();
  expect(screen.queryByText("往返费用调整")).not.toBeInTheDocument();
});

it("旧记录、未知结算版本和未知证据版本不显示伪认证或费用", () => {
  const { rerender } = render(<IntegrityCard instId="BTC-USDT-SWAP"
    integrity={{ schemaVersion: 2 }} />);
  expect(screen.getByText("旧记录未提供校验结果")).toBeInTheDocument();
  expect(screen.getByText(/旧记录未提供证据版本/)).toBeInTheDocument();
  rerender(<IntegrityCard instId="BTC-USDT-SWAP" integrity={{
    ...settlementWire({ schemaVersion: 99 }),
    memory: { evidenceVersion: 99, summaryEvidenceVersion: 99 },
  } as IntegritySnapshot} />);
  expect(screen.getByText("此版本结算详情暂不可读")).toBeInTheDocument();
  expect(screen.getByText(/此版本证据暂不可读/)).toBeInTheDocument();
  expect(screen.queryByText("本次持仓匹配通过")).not.toBeInTheDocument();
  expect(screen.queryByText("往返费用调整")).not.toBeInTheDocument();
});

it("当前等待与已实测零费用分别呈现，摘要原生按钮可以获得键盘焦点", () => {
  render(<IntegrityCard instId="ETH-USDT-SWAP"
    integrity={settlementWire({ status: "pending", reason: "not_found",
      feeAdjustment: { kind: "aggregate_residual", exchangeTripFeeUsd: 0,
        bookedLocalFeeUsd: null, residualFeeUsd: 0, feeBasis: "measured" },
    })} />);
  expect(screen.getByText("当前结算")).toBeInTheDocument();
  expect(screen.queryByText("上次结算")).not.toBeInTheDocument();
  expect(screen.getByText("结算待确认：未找到结算记录")).toBeInTheDocument();
  expect(screen.getByText(/费用来源：实测/)).toBeInTheDocument();
  expect(screen.getAllByText("0.00 USDT")).toHaveLength(2);
  const summary = screen.getByText("展开运行明细");
  summary.focus();
  expect(summary).toHaveFocus();
  fireEvent.click(summary);
  expect(summary.parentElement).toHaveAttribute("open");
});
