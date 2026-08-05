import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { DiscoveryDefaults } from "@/lib/discoveryTypes";
import DiscoveryLauncher from "./DiscoveryLauncher";

const startMock = vi.fn();

vi.mock("@/lib/discoveryApi", () => ({
  discoveryApi: {
    start: (...args: unknown[]) => startMock(...args),
  },
}));

function defaults(over: Partial<DiscoveryDefaults> = {}): DiscoveryDefaults {
  return {
    // D-7：四条轴全部实现（v1 只有 factor 的时代已经结束）。
    implementedAxes: ["factor", "factor_combo", "strategy_param", "strategy_combo"],
    allAxes: ["factor", "factor_combo", "strategy_param", "strategy_combo"],
    bars: ["1m", "15m", "1H", "4H", "1D"],
    windowBars: { default: 4000, min: 200, max: 8000 },
    candidatesPerIteration: { default: 40, min: 1, max: 200 },
    iterationIntervalS: { default: 300, min: 60, max: 3600 },
    horizon: { default: 6, min: 1, max: 100 },
    dataRefreshS: { default: 21600, min: 0 },
    maxKept: { default: 200, min: 1, max: 2000 },
    maxKeptPerAxis: { default: 200, min: 1, max: 2000 },
    axisInfo: {
      factor: { label: "因子", desc: "在价量表达式空间里搜索单因子",
                backtestsPerCandidate: 0, dependsOn: [], cost: 1 },
      factor_combo: { label: "因子组合策略", desc: "把因子装成带权重与阈值的组合",
                      backtestsPerCandidate: 2, dependsOn: [], cost: 10 },
      strategy_param: { label: "常规策略", desc: "在内置策略模板的参数空间里搜索",
                        backtestsPerCandidate: 2, dependsOn: [], cost: 8 },
      strategy_combo: { label: "策略轮动组合", desc: "把已挖到的策略装进轮动模板",
                        backtestsPerCandidate: 4, dependsOn: ["strategy_param"], cost: 20 },
    },
    axisLabels: { factor: "因子", factor_combo: "因子组合策略",
                  strategy_param: "常规策略", strategy_combo: "策略轮动组合" },
    templates: [
      { key: "momentum_breakout", name: "暴涨动量突破", family: "breakout",
        familyCn: "突破", minable: true, reason: "", paramCount: 7, htfBars: [] },
      { key: "regime_rotation", name: "趋势自适应策略组合", family: "rotation",
        familyCn: "策略轮动", minable: false, reason: "组合与因子类模板由专用轴挖掘",
        paramCount: 0, htfBars: [] },
    ],
    strategyThresholds: { minTrades: 20, minSharpe: 0.8, maxDrawdown: 0.35, minCalmar: 1.0,
                          oosRetain: 0.4, maxEquityCorr: 0.85 },
    comboSize: { default: { min: 2, max: 5 }, min: 2, max: 5 },
    rotationSize: { default: { min: 2, max: 4 }, min: 2, max: 4 },
    thresholds: { minAbsIc: 0.03, minAbsIr: 0.5 },
    evolution: {
      budget: { mutate: 0.25, crossover: 0.25, perturb: 0.15, random: 0.15, llm: 0.2 },
      adaptEvery: 10,
      fsaEvery: 25,
      fsaThreshold: 0.15,
      fsaBanTtlIters: 150,
      variantCap: 6,
      coldStartMin: 5,
    },
    llm: { enabled: true, reviewSample: 5, maxCallsPerHour: 24, timeoutS: 60 },
    workUnitLimit: 3_000_000,
    enabled: true,
    maxRuns: 1,
    ...over,
  };
}

function open(config = defaults()) {
  return render(
    <DiscoveryLauncher open defaults={config} onClose={vi.fn()} onStarted={vi.fn()} />,
  );
}

beforeEach(() => {
  startMock.mockReset();
  startMock.mockResolvedValue({ runId: 1, status: "running", config: {}, startedTs: 0 });
});
afterEach(cleanup);

describe("DiscoveryLauncher — 两步式启动器", () => {
  it("轴选择器渲染四条已实现的轴，且**不出现任何占位文案**（D-7 强制推论）", () => {
    open();
    const axes = screen.getByLabelText("搜索轴");
    expect(axes.querySelectorAll("input[type=checkbox]")).toHaveLength(4);
    expect(screen.getByLabelText("搜索轴 策略轮动组合")).toBeTruthy();
    expect(screen.queryByText(/敬请期待/)).toBeNull();
  });

  it("实时显示工作量预估与窗口天数", () => {
    open();
    expect(screen.getByText(/工作量预估 160,000/)).toBeTruthy();
    expect(screen.getByText(/约覆盖 166\.7 天/)).toBeTruthy();
  });

  it("工作量超限时给出明确错误并禁用下一步（与后端 413 同口径，文案含「减少轴」）", () => {
    open(defaults({ workUnitLimit: 1000 }));
    expect(
      screen.getByText("工作量超出上限，请减少候选数、缩短窗口，或减少同时开启的轴"),
    ).toBeTruthy();
    expect(screen.getByRole("button", { name: "下一步" })).toBeDisabled();
  });

  it("参数按 defaults 的上下界夹紧（越界输入不会被原样提交）", () => {
    open();
    const window = screen.getByLabelText("数据窗口（根）") as HTMLInputElement;
    fireEvent.change(window, { target: { value: "999999" } });
    expect(Number(window.value)).toBe(8000);
    fireEvent.change(window, { target: { value: "1" } });
    expect(Number(window.value)).toBe(200);
  });

  it("「恢复默认」走 defaults 回落（唯一真源是 /discovery/config/defaults）", () => {
    open();
    const horizon = screen.getByLabelText("前瞻根数") as HTMLInputElement;
    fireEvent.change(horizon, { target: { value: "20" } });
    expect(Number(horizon.value)).toBe(20);
    fireEvent.click(screen.getByRole("button", { name: "恢复默认" }));
    expect(Number((screen.getByLabelText("前瞻根数") as HTMLInputElement).value)).toBe(6);
  });

  it("两步流转：下一步进阈值页，上一步可返回", () => {
    open();
    fireEvent.click(screen.getByRole("button", { name: "下一步" }));
    expect(screen.getByLabelText("最小 |RankIC|")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "上一步" }));
    expect(screen.getByLabelText("标的")).toBeTruthy();
  });

  it("提交时把归一化后的配置发给后端", async () => {
    open();
    fireEvent.click(screen.getByRole("button", { name: "下一步" }));
    fireEvent.click(screen.getByRole("button", { name: "开始挖掘" }));
    await waitFor(() => expect(startMock).toHaveBeenCalledTimes(1));
    const body = startMock.mock.calls[0][0] as Record<string, unknown>;
    // R-15 守门人：默认只勾 factor，单轴用户的默认路径观感零变化。
    // **不许为了让它过而改默认**——改了就是把多轴 UI 的复杂度强加给单轴用户。
    expect(body.axes).toEqual(["factor"]);
    expect(body.windowBars).toBe(4000);
    expect(body.autoPromote).toBe(false); // autoPromote 默认关（已拍板，不可改）
  });

  it("勾选策略轮动轴会自动带上它依赖的常规策略轴（否则提交必然 400）", async () => {
    open();
    fireEvent.click(screen.getByLabelText("搜索轴 策略轮动组合"));
    fireEvent.click(screen.getByRole("button", { name: "下一步" }));
    fireEvent.click(screen.getByRole("button", { name: "开始挖掘" }));
    await waitFor(() => expect(startMock).toHaveBeenCalledTimes(1));
    const body = startMock.mock.calls[0][0] as Record<string, unknown>;
    expect(body.axes).toEqual(["factor", "strategy_param", "strategy_combo"]);
  });

  it("选了策略轴才显示策略阈值折叠区（单轴用户看不到多余控件）", () => {
    open();
    expect(screen.queryByText("策略轴阈值（高级）")).toBeNull();
    fireEvent.click(screen.getByLabelText("搜索轴 常规策略"));
    fireEvent.click(screen.getByRole("button", { name: "下一步" }));
    expect(screen.getByText("策略轴阈值（高级）")).toBeTruthy();
  });

  it("后端报错时显示错误而不是静默失败", async () => {
    startMock.mockRejectedValue(new Error("工作量超出上限"));
    open();
    fireEvent.click(screen.getByRole("button", { name: "下一步" }));
    fireEvent.click(screen.getByRole("button", { name: "开始挖掘" }));
    await waitFor(() => expect(screen.getByRole("alert").textContent).toContain("工作量超出上限"));
  });

  it("open=false 时不渲染任何内容", () => {
    const { container } = render(
      <DiscoveryLauncher open={false} defaults={defaults()} onClose={vi.fn()} onStarted={vi.fn()} />,
    );
    expect(container.textContent).toBe("");
  });
});
