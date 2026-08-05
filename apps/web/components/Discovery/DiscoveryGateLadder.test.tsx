import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import type { DiscoveryGateRow } from "@/lib/discoveryTypes";
import DiscoveryGateLadder from "./DiscoveryGateLadder";

const STRATEGY_GATES: DiscoveryGateRow[] = [
  { id: "S1", label: "样本量", value: 96, gate: 20, passed: true },
  { id: "S2", label: "样本内收益", value: 0.31, gate: 0, passed: true },
  { id: "S6", label: "样本外留存", value: -0.2, gate: 0.572, passed: false },
];

const FACTOR_GATES: DiscoveryGateRow[] = [
  { id: "F2", label: "样本内 RankIC", value: 0.061, gate: 0.03, passed: true },
  { id: "F10", label: "与已入库因子相关性", value: 0.91, gate: 0.85, passed: false },
];

afterEach(cleanup);

describe("DiscoveryGateLadder — 因子 F 闸与策略 S 闸共用一个组件", () => {
  it("逐条显示实际值与门槛，并标出卡在哪一条", () => {
    render(<DiscoveryGateLadder gates={STRATEGY_GATES} />);
    expect(screen.getByText("样本量")).toBeTruthy();
    expect(screen.getByText("96.0000")).toBeTruthy();
    expect(screen.getByText("20.0000")).toBeTruthy();
    expect(screen.getAllByText("卡在这条")).toHaveLength(1);
  });

  it("只有第一条未通过的闸被标红（后面的闸根本没跑）", () => {
    const gates = [...STRATEGY_GATES,
                   { id: "S7", label: "交易密度", value: 0.9, gate: 0.05, passed: false }];
    render(<DiscoveryGateLadder gates={gates} />);
    expect(screen.getAllByText("卡在这条")).toHaveLength(1);
  });

  it("因子轴的 F 闸走同一个组件、同样渲染", () => {
    render(<DiscoveryGateLadder gates={FACTOR_GATES} />);
    expect(screen.getByText("与已入库因子相关性")).toBeTruthy();
    expect(screen.getByText("F10")).toBeTruthy();
    expect(screen.getAllByText("卡在这条")).toHaveLength(1);
  });

  it("没有逐条数据时明说，并把拒绝原因翻成中文（绝不画一排空壳）", () => {
    render(<DiscoveryGateLadder gates={[]} rejectReason="S0:gate_leak" />);
    const text = screen.getByText(/本候选未产出逐条闸位数据/).textContent ?? "";
    expect(text).toContain("样本外隔离失效");
    expect(text).not.toContain("S0:gate_leak");
  });

  it("既无数据也无拒绝原因时给一句中性说明", () => {
    render(<DiscoveryGateLadder gates={undefined} />);
    expect(screen.getByText("本候选没有逐条闸位数据。")).toBeTruthy();
  });
});
