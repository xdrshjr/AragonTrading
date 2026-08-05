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
    implementedAxes: ["factor"],
    allAxes: ["factor", "factor_combo", "strategy_param", "strategy_combo"],
    bars: ["1m", "15m", "1H", "4H", "1D"],
    windowBars: { default: 4000, min: 200, max: 8000 },
    candidatesPerIteration: { default: 40, min: 1, max: 200 },
    iterationIntervalS: { default: 300, min: 60, max: 3600 },
    horizon: { default: 6, min: 1, max: 100 },
    dataRefreshS: { default: 21600, min: 0 },
    maxKept: { default: 200, min: 1, max: 2000 },
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
  it("轴选择器**只**渲染 implementedAxes，绝不渲染未实现轴的占位（D-6 强制推论）", () => {
    open();
    const axis = screen.getByLabelText("搜索轴") as HTMLSelectElement;
    const options = Array.from(axis.options).map((option) => option.value);
    expect(options).toEqual(["factor"]);
    expect(screen.queryByText(/敬请期待/)).toBeNull();
    expect(screen.queryByText(/策略轮动组合/)).toBeNull();
  });

  it("实时显示工作量预估与窗口天数", () => {
    open();
    expect(screen.getByText(/工作量预估 160,000/)).toBeTruthy();
    expect(screen.getByText(/约覆盖 166\.7 天/)).toBeTruthy();
  });

  it("工作量超限时给出明确错误并禁用下一步（与后端 413 同口径）", () => {
    open(defaults({ workUnitLimit: 1000 }));
    expect(screen.getByText("工作量超出上限，请调小候选数或窗口")).toBeTruthy();
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
    expect(body.axes).toEqual(["factor"]);
    expect(body.windowBars).toBe(4000);
    expect(body.autoPromote).toBe(false); // autoPromote 默认关（已拍板，不可改）
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
