import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { DiscoveryCandidateDetail } from "@/lib/discoveryTypes";
import DiscoveryStrategyDetail from "./DiscoveryStrategyDetail";

// lightweight-charts 在 jsdom 下不可用；本用例关心的是**传给它的是哪个入参**
// （评审 P2-9：IS/OOS 双段必须走 overlays 分支，绝不与 equity 混传）。
const chartProps: Record<string, unknown>[] = [];
vi.mock("@/components/Chart/EquityChart", () => ({
  default: (props: Record<string, unknown>) => {
    chartProps.push(props);
    return <div data-testid="equity-chart" />;
  },
}));

function detail(over: Partial<DiscoveryCandidateDetail> = {}): DiscoveryCandidateDetail {
  return {
    id: 1,
    kind: "strategy_param",
    genomeHash: "h",
    exprText: "",
    describe: "暴涨动量突破 · 突破窗口 34 / 放量倍数 1.8",
    mechanism: "breakout",
    source: "mutate",
    verdict: "kept",
    rejectReason: "",
    createdTs: 0,
    iterationIdx: 12,
    metrics: {
      kind: "strategy_param",
      templateKey: "momentum_breakout",
      trials: 1832,
      requiredSharpe: 1.02,
      costs: { initialCapital: 10000, takerFeeBps: 5, slippageBps: 2, leverage: 1 },
      clipMode: "gated",
      warmupPad: 34,
      is: { sharpe: 1.43, totalReturn: 0.31, maxDrawdown: -0.18, calmar: 1.72,
            tradeCount: 96, tradeRate: 0.034 },
      oos: { sharpe: 0.92, totalReturn: 0.11, maxDrawdown: -0.14, calmar: 0.98,
             tradeCount: 38 },
      stress: { costMult: 2, totalReturn: 0.12 },
      maxEquityCorr: 0.41,
      paramDiff: [{ key: "breakout_window", label: "突破窗口", default: 20, value: 34 }],
    },
    series: {
      equityIs: [{ ts: 1, equity: 10000 }, { ts: 2, equity: 11000 }],
      equityOos: [{ ts: 3, equity: 10000 }, { ts: 4, equity: 10500 }],
      gates: [
        { id: "S1", label: "样本量", value: 96, gate: 20, passed: true },
        { id: "S6", label: "样本外留存", value: -0.2, gate: 0.57, passed: false },
      ],
    },
    genome: {},
    ...over,
  } as DiscoveryCandidateDetail;
}

afterEach(() => {
  chartProps.length = 0;
  cleanup();
});

describe("DiscoveryStrategyDetail — 策略候选详情", () => {
  it("IS/OOS 双段各一条净值，且**只走 overlays 分支**（与 equity 互斥，P2-9）", () => {
    render(<DiscoveryStrategyDetail detail={detail()} />);
    expect(screen.getByTestId("equity-chart")).toBeTruthy();
    const props = chartProps[0];
    expect(Array.isArray(props.overlays)).toBe(true);
    expect((props.overlays as unknown[]).length).toBe(2);
    expect(props.equity).toBeUndefined();
  });

  it("双栏指标齐全，含只能来自扩展指标的 Calmar", () => {
    render(<DiscoveryStrategyDetail detail={detail()} />);
    expect(screen.getByText("样本内（IS）")).toBeTruthy();
    expect(screen.getByText("样本外（OOS）")).toBeTruthy();
    expect(screen.getAllByText("Calmar").length).toBe(2);
    expect(screen.getByText("1.72")).toBeTruthy();
  });

  it("卡住的闸标出来并显示实际值与门槛", () => {
    render(<DiscoveryStrategyDetail detail={detail()} />);
    expect(screen.getByText("卡在这条")).toBeTruthy();
    expect(screen.getByText("样本外留存")).toBeTruthy();
  });

  it("参数对照表高亮挖到的值", () => {
    render(<DiscoveryStrategyDetail detail={detail()} />);
    expect(screen.getByText("突破窗口")).toBeTruthy();
    expect(screen.getByText("20")).toBeTruthy();
    expect(screen.getByText("34")).toBeTruthy();
  });

  it("clipMode=rebased 时显示轮动轴专属的口径说明（BL-3）", () => {
    render(
      <DiscoveryStrategyDetail
        detail={detail({
          kind: "strategy_combo",
          metrics: { ...detail().metrics, clipMode: "rebased" },
        })}
      />,
    );
    const note = screen.getByText(/样本外口径/).textContent ?? "";
    expect(note).toContain("权益重基");
    expect(note).toContain("不可直接比较");
  });

  it("成本口径与风险声明必须出现（历史统计，不构成收益预期）", () => {
    render(<DiscoveryStrategyDetail detail={detail()} />);
    const footer = screen.getByText(/不构成任何收益预期/).textContent ?? "";
    expect(footer).toContain("5 bps");
    expect(footer).toContain("滑点 2 bps");
  });

  it("没有净值序列时图表拿到空 overlays 而不是崩掉", () => {
    render(<DiscoveryStrategyDetail detail={detail({ series: {} })} />);
    expect((chartProps[0].overlays as unknown[]).length).toBe(0);
  });
});
