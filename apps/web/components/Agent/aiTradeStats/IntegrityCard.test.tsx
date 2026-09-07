import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, it } from "vitest";
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
