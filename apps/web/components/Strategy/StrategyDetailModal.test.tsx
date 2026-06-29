import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { CompareItem, Metrics, Strategy } from "@/lib/types";
import StrategyDetailModal from "./StrategyDetailModal";

const METRICS: Metrics = {
  totalReturn: 0.12,
  winRate: 0.5,
  profitFactor: 1.4,
  maxDrawdown: -0.08,
  sharpe: 1.2,
  tradeCount: 18,
  avgPnl: 3.2,
};

const ATOMIC: Strategy = {
  id: 1,
  templateKey: "trend_pullback_long",
  name: "顺势回踩做多",
  description: "顺势回踩进场策略",
  kind: "atomic",
  params: { fast: 12, htf_bar: "4H" },
  paramsSchema: {
    fast: { type: "int", default: 10, label: "快线周期" },
    htf_bar: { type: "enum", default: "4H", options: ["1H", "4H"], label: "高周期" },
  },
  enabled: true,
  builtin: true,
};

const MEMBER_STRATEGIES: Strategy[] = [
  { id: 5, templateKey: "trend_pullback_long", name: "顺势回踩做多", description: "", kind: "atomic",
    params: {}, paramsSchema: {}, enabled: true, builtin: true },
  { id: 6, templateKey: "range_scalping_strategy", name: "区间剥头皮", description: "", kind: "atomic",
    params: {}, paramsSchema: {}, enabled: true, builtin: true },
];

const COMBO: Strategy = {
  id: 2,
  templateKey: "regime_rotation",
  name: "我的组合A",
  description: "体制轮动组合",
  kind: "combo",
  params: {
    members: [
      { templateKey: "trend_pullback_long", params: {}, name: "顺势回踩做多" },
      { templateKey: "range_scalping_strategy", params: {}, name: "区间剥头皮" },
    ],
    rebalanceBars: 24,
    lookbackBars: 168,
    selectionMetric: "sharpe",
    minDwellBars: 24,
    switchMarginPct: 0.1,
    regimeGating: true,
    turnoverBps: 5,
  },
  paramsSchema: {},
  enabled: true,
  builtin: false,
};

const FACTOR: Strategy = {
  id: 3,
  templateKey: "factor_score",
  name: "因子组合得分",
  description: "因子复合得分策略",
  kind: "factor",
  params: {
    members: [
      { factorKey: "mom_10", direction: "long" },
      { factorKey: "rsi_14", direction: "short" },
    ],
    thresholds: { normWindow: 200 },
    provenance: { instId: "BTC-USDT-SWAP", bar: "1H", startTs: 1_700_000_000, endTs: 1_700_100_000, horizon: 5 },
  },
  paramsSchema: {},
  enabled: true,
  builtin: true,
};

describe("StrategyDetailModal", () => {
  afterEach(cleanup);

  it("renders atomic description, a read-only param grid and the HTF badge", () => {
    render(<StrategyDetailModal strategy={ATOMIC} strategies={[ATOMIC]} onClose={vi.fn()} />);
    expect(screen.getByText("顺势回踩进场策略")).toBeTruthy();
    expect(screen.getByText("策略参数")).toBeTruthy();
    expect(screen.getByText("快线周期")).toBeTruthy();
    expect(screen.getByText("12")).toBeTruthy(); // params override of the schema default
    expect(screen.getByText("参考周期：4H")).toBeTruthy();
  });

  it("renders combo overview / members / rotation via the shared sections", () => {
    render(<StrategyDetailModal strategy={COMBO} strategies={MEMBER_STRATEGIES} onClose={vi.fn()} />);
    expect(screen.getByText("概览")).toBeTruthy();
    expect(screen.getByText("成员策略 · 2")).toBeTruthy();
    expect(screen.getByText("轮动参数")).toBeTruthy();
    expect(screen.getByText("区间剥头皮")).toBeTruthy();
  });

  it("renders factor members, the minimum-bars hint and provenance", () => {
    render(<StrategyDetailModal strategy={FACTOR} strategies={[FACTOR]} onClose={vi.fn()} />);
    expect(screen.getByText(/mom_10/)).toBeTruthy();
    expect(screen.getByText(/约 260 根数据/)).toBeTruthy(); // normWindow(200) + 60
    expect(screen.getByText(/训练于/)).toBeTruthy();
  });

  it("appends this-run performance when a compareItem hits", () => {
    const item: CompareItem = { strategyId: 1, name: "顺势回踩做多", runId: 5, metrics: METRICS, equity: [] };
    render(<StrategyDetailModal strategy={ATOMIC} strategies={[ATOMIC]} compareItem={item} onClose={vi.fn()} />);
    expect(screen.getByText("本次对比绩效")).toBeTruthy();
    expect(screen.getByText("打开该策略回测")).toBeTruthy();
    expect(screen.getByText("总收益率")).toBeTruthy();
  });

  it("closes on Escape via the Modal a11y contract", () => {
    const onClose = vi.fn();
    render(<StrategyDetailModal strategy={ATOMIC} strategies={[ATOMIC]} onClose={onClose} />);
    fireEvent.keyDown(screen.getByRole("dialog"), { key: "Escape" });
    expect(onClose).toHaveBeenCalled();
  });
});
