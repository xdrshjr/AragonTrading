import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { AxisInfo, DiscoveryAxis, TemplateSpec } from "@/lib/discoveryTypes";
import DiscoveryAxisPicker, { type AxisSelection } from "./DiscoveryAxisPicker";

const ALL_AXES: DiscoveryAxis[] = [
  "factor",
  "factor_combo",
  "strategy_param",
  "strategy_combo",
];

const AXIS_INFO: Record<string, AxisInfo> = {
  factor: { label: "因子", desc: "在价量表达式空间里搜索单因子", backtestsPerCandidate: 0,
            dependsOn: [], cost: 1 },
  factor_combo: { label: "因子组合策略", desc: "把因子装成带权重与阈值的组合",
                  backtestsPerCandidate: 2, dependsOn: [], cost: 10 },
  strategy_param: { label: "常规策略", desc: "在内置策略模板的参数空间里搜索",
                    backtestsPerCandidate: 2, dependsOn: [], cost: 8 },
  strategy_combo: { label: "策略轮动组合", desc: "把已挖到的策略装进轮动模板",
                    backtestsPerCandidate: 4, dependsOn: ["strategy_param"], cost: 20 },
};

const TEMPLATES: TemplateSpec[] = [
  { key: "momentum_breakout", name: "暴涨动量突破", family: "breakout", familyCn: "突破",
    minable: true, reason: "", paramCount: 7, htfBars: [] },
  { key: "trend_band_swing", name: "大周期趋势带波段", family: "trend", familyCn: "趋势跟随",
    minable: true, reason: "", paramCount: 9, htfBars: ["12H", "1D"] },
  { key: "regime_rotation", name: "趋势自适应策略组合", family: "rotation",
    familyCn: "策略轮动", minable: false, reason: "组合与因子类模板由专用轴挖掘",
    paramCount: 0, htfBars: [] },
];

function selection(over: Partial<AxisSelection> = {}): AxisSelection {
  return {
    axes: ["factor"],
    templates: [],
    includeLibraryFactors: true,
    comboSize: { min: 2, max: 5 },
    rotationSize: { min: 2, max: 4 },
    ...over,
  };
}

function open(value = selection(), onChange = vi.fn()) {
  render(
    <DiscoveryAxisPicker
      allAxes={ALL_AXES}
      axisInfo={AXIS_INFO}
      templates={TEMPLATES}
      value={value}
      onChange={onChange}
    />,
  );
  return onChange;
}

afterEach(cleanup);

describe("DiscoveryAxisPicker — 多选轴卡片组", () => {
  it("四条轴全部可选，且**没有任何「敬请期待」占位**（D-7）", () => {
    open();
    for (const info of Object.values(AXIS_INFO)) {
      expect(screen.getByLabelText(`搜索轴 ${info.label}`)).toBeTruthy();
    }
    expect(screen.queryByText(/敬请期待/)).toBeNull();
  });

  it("每张卡片写清预计回测次数与依赖，用户能预判成本", () => {
    open();
    expect(screen.getByText(/每个候选约 4 次回测/)).toBeTruthy();
    expect(screen.getByText(/依赖：常规策略/)).toBeTruthy();
    expect(screen.getByText(/无需回测/)).toBeTruthy();
  });

  it("勾选轮动轴会自动带上常规策略轴（否则提交必然 400）", () => {
    const onChange = open();
    fireEvent.click(screen.getByLabelText("搜索轴 策略轮动组合"));
    expect(onChange).toHaveBeenCalledTimes(1);
    expect(onChange.mock.calls[0][0].axes).toEqual([
      "factor", "strategy_param", "strategy_combo",
    ]);
  });

  it("取消常规策略轴时一并取消依赖它的轮动轴", () => {
    const onChange = open(selection({ axes: ["strategy_param", "strategy_combo"] }));
    fireEvent.click(screen.getByLabelText("搜索轴 常规策略"));
    expect(onChange.mock.calls[0][0].axes).toEqual([]);
  });

  it("选中常规策略轴才展开模板白名单，且**不可挖模板必须显示原因**", () => {
    open(selection({ axes: ["strategy_param"] }));
    expect(screen.getByText("常规策略 · 模板白名单")).toBeTruthy();
    expect(screen.getByLabelText("挖掘模板 暴涨动量突破")).toBeTruthy();
    expect(screen.getByText("组合与因子类模板由专用轴挖掘")).toBeTruthy();
    // 不可挖模板绝不静默隐藏：名字与原因都必须在界面上。
    expect(screen.getByText("趋势自适应策略组合")).toBeTruthy();
  });

  it("因子组合轴展开成员来源开关与组合规模", () => {
    open(selection({ axes: ["factor_combo"] }));
    expect(screen.getByText(/纳入因子库里的既有因子/)).toBeTruthy();
    expect(screen.getByLabelText("组合成员数（下限）")).toBeTruthy();
  });

  it("轮动轴给出依赖提示，说清库存不足时会让渡", () => {
    open(selection({ axes: ["strategy_param", "strategy_combo"] }));
    expect(screen.getByText(/让渡/)).toBeTruthy();
    expect(screen.getByLabelText("轮动成员数（上限）")).toBeTruthy();
  });

  it("规模区间被夹在边界内且不允许 min > max", () => {
    const onChange = open(selection({ axes: ["factor_combo"] }));
    fireEvent.change(screen.getByLabelText("组合成员数（下限）"), { target: { value: "9" } });
    const next = onChange.mock.calls[0][0].comboSize;
    expect(next.min).toBe(5);
    expect(next.max).toBeGreaterThanOrEqual(next.min);
  });
});
