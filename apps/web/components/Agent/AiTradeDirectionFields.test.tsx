import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import AiTradeDirectionFields from "./AiTradeDirectionFields";
import type { AiTradeDirectionRule } from "@/lib/aiTradeTypes";

// 启动弹窗的「方向对称 · 逆势通路」整段（directional-symmetry D-5）。
//
// 三条守卫：
// ① **前端不硬编码任何默认数值** —— 三个输入的占位符必须是「服务端默认」，留空即整字段不发；
// ② 关闭态下三个数值输入整体不渲染（一个改得动却毫无作用的输入框比不显示更坏）；
// ③ 校验错误如实呈现（区间与后端 `AiTradeDirectionIn` 的 `Field` 逐字一致）。

function rule(over: Partial<AiTradeDirectionRule> = {}): AiTradeDirectionRule {
  return { contrarianEnabled: true, ...over };
}

function renderFields(over: Partial<React.ComponentProps<typeof AiTradeDirectionFields>> = {}) {
  const onChange = vi.fn();
  const onRawChange = vi.fn();
  render(
    <AiTradeDirectionFields
      value={rule()}
      onChange={onChange}
      confidenceRaw=""
      quotaRaw=""
      sizeMultRaw=""
      onRawChange={onRawChange}
      {...over}
    />,
  );
  return { onChange, onRawChange };
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
