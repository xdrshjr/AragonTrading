import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import AiTradeDirectionFields from "./AiTradeDirectionFields";
import type { AiTradeDirectionRule, AiTradeTrendRule } from "@/lib/aiTradeTypes";

// 启动弹窗的「方向对称 · 逆势通路」整段（directional-symmetry D-5）。
//
// 三条守卫：
// ① **前端不硬编码任何默认数值** —— 三个输入的占位符必须是「服务端默认」，留空即整字段不发；
// ② 关闭态下三个数值输入整体不渲染（一个改得动却毫无作用的输入框比不显示更坏）；
// ③ 校验错误如实呈现（区间与后端 `AiTradeDirectionIn` 的 `Field` 逐字一致）。

function rule(over: Partial<AiTradeDirectionRule> = {}): AiTradeDirectionRule {
  return { contrarianEnabled: true, ...over };
}

// 执行周期趋势基调分组（execution-trend-verdict §5.1）：四个受控值同样由父组件持有，
// 本组件内不得写任何 effect（ai-trade-modal-revert-recurrence 的复发根因）。
function trendRule(over: Partial<AiTradeTrendRule> = {}): AiTradeTrendRule {
  return { enabled: true, counterGateEnabled: true, ...over };
}

function renderFields(over: Partial<React.ComponentProps<typeof AiTradeDirectionFields>> = {}) {
  const onChange = vi.fn();
  const onRawChange = vi.fn();
  const onTrendChange = vi.fn();
  const onTrendRawChange = vi.fn();
  render(
    <AiTradeDirectionFields
      value={rule()}
      onChange={onChange}
      confidenceRaw=""
      quotaRaw=""
      sizeMultRaw=""
      onRawChange={onRawChange}
      trend={trendRule()}
      onTrendChange={onTrendChange}
      trendConfidenceRaw=""
      trendSizeMultRaw=""
      onTrendRawChange={onTrendRawChange}
      {...over}
    />,
  );
  return { onChange, onRawChange, onTrendChange, onTrendRawChange };
}

afterEach(cleanup);

describe("AiTradeDirectionFields", () => {
  it("默认开启，三个数值输入留空且占位符是「服务端默认」（前端不存默认值）", () => {
    renderFields();
    expect(screen.getByLabelText("启用逆势通路")).toBeChecked();
    for (const testId of ["ai-direction-confidence", "ai-direction-quota", "ai-direction-size-mult"]) {
      const input = screen.getByTestId(testId) as HTMLInputElement;
      expect(input.value).toBe("");
      expect(input.placeholder).toBe("服务端默认");
    }
  });

  it("取消勾选 ⇒ 三个数值输入整体不渲染，并向父组件上报显式关闭", () => {
    const { onChange } = renderFields();
    fireEvent.click(screen.getByLabelText("启用逆势通路"));
    expect(onChange).toHaveBeenCalledWith(expect.objectContaining({ contrarianEnabled: false }));

    cleanup();
    renderFields({ value: rule({ contrarianEnabled: false }) });
    expect(screen.queryByTestId("ai-direction-confidence")).toBeNull();
    // 关闭态的释义必须写清后果：只能顺着信号板方向开仓。
    expect(screen.getByTestId("ai-trade-direction-fields")).toHaveTextContent(
      "只能顺着信号板的方向开仓",
    );
  });

  it("输入经 raw 串上报父组件（本组件内不做任何归一，也不写任何 effect）", () => {
    const { onRawChange } = renderFields();
    fireEvent.change(screen.getByTestId("ai-direction-quota"), { target: { value: "2" } });
    expect(onRawChange).toHaveBeenCalledWith("quota", "2");
  });

  it("越界时如实呈现校验错误（不静默夹紧）", () => {
    renderFields({ error: "逆势降档系数需为 0~1（不含 0）" });
    expect(screen.getByTestId("ai-direction-error")).toHaveTextContent("逆势降档系数需为 0~1");
  });

  it("disabled ⇒ 开关与数值输入一并禁用", () => {
    renderFields({ disabled: true });
    expect(screen.getByLabelText("启用逆势通路")).toBeDisabled();
    expect(screen.getByTestId("ai-direction-confidence")).toBeDisabled();
  });
});

describe("AiTradeDirectionFields · 趋势基调分区（execution-trend-verdict §5.1）", () => {
  it("默认开启，三把子闸各自可勾选、两个数值留空且占位符是「服务端默认」", () => {
    renderFields();
    expect(screen.getByLabelText("启用执行周期趋势基调")).toBeChecked();
    expect(screen.getByLabelText("启用逆势降档闸")).toBeChecked();
    expect(screen.getByLabelText("启用顺势追加一票")).toBeChecked();
    expect(screen.getByLabelText("启用顺势放宽反向票上限")).toBeChecked();
    for (const testId of ["ai-trend-confidence", "ai-trend-size-mult"]) {
      const input = screen.getByTestId(testId) as HTMLInputElement;
      expect(input.value).toBe("");
      expect(input).toHaveAttribute("placeholder", "服务端默认");
    }
  });

  it("总闸取消勾选 ⇒ 三把子闸与两个数值输入整体不渲染（改得动却毫无作用的输入比不显示更坏）", () => {
    renderFields({ trend: trendRule({ enabled: false }) });
    expect(screen.queryByLabelText("启用逆势降档闸")).toBeNull();
    expect(screen.queryByTestId("ai-trend-confidence")).toBeNull();
  });

  it("RP1-12：用户能在弹窗里直接关掉逆势闸（此前只有一个运维 env、没有任何接口）", () => {
    const { onTrendChange } = renderFields();
    fireEvent.click(screen.getByLabelText("启用逆势降档闸"));
    expect(onTrendChange).toHaveBeenCalledWith(
      expect.objectContaining({ enabled: true, counterGateEnabled: false }),
    );
  });

  it("逆势闸关掉时两个数值输入置灰（它们此刻不产生任何作用，不能假装还在生效）", () => {
    renderFields({ trend: trendRule({ counterGateEnabled: false }) });
    expect(screen.getByTestId("ai-trend-confidence")).toBeDisabled();
    expect(screen.getByTestId("ai-trend-size-mult")).toBeDisabled();
  });

  it("三把子闸的**极性**必须写在界面上：一把收紧、两把放宽，可分别回退", () => {
    renderFields();
    const section = screen.getByTestId("ai-trade-trend-fields");
    expect(section).toHaveTextContent("收紧");
    expect(section).toHaveTextContent("放宽");
    expect(section).toHaveTextContent("不发票");
  });

  it("校验错误如实呈现（区间与后端 `AiTradeTrendIn` 的 Field 逐字一致）", () => {
    renderFields({ trendError: "逆势置信度下界需在 0~1 之间（不含 0）" });
    expect(screen.getByTestId("ai-trend-error")).toHaveTextContent("0~1");
  });
});
