import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import OpenFunnelCard from "./OpenFunnelCard";
import type { AiTradeOpenFunnel } from "@/lib/aiTradeTypes";

// 开仓漏斗卡（tradability-and-open-funnel 轴 C · G-12）。
// 两条呈现门控：老会话缺键 ⇒ 整卡不渲染（不崩）；零意愿 ⇒ 显式写「样本不足」而不是画 0%。

const LAYERS = ["baseRuleMaBand", "consensus", "confidence", "edgeGate", "other"];

function funnel(over: Partial<AiTradeOpenFunnel> = {}): AiTradeOpenFunnel {
  return {
    intents: 12,
    blocked: { edgeGate: 11, consensus: 1 },
    topBlocker: { key: "edgeGate", label: "最小 edge 门禁", count: 11 },
    llmDegraded: 0,
    sizingZeroed: 0,
    submitted: 0,
    filled: 0,
    conversion: 0,
    layers: LAYERS,
    scope: "本会话全程累计",
    ...over,
  };
}

afterEach(cleanup);

describe("OpenFunnelCard", () => {
  it("旧读数显示分类不完整，v2 执行未知和未提交均单列", () => {
    const view = render(<OpenFunnelCard funnel={funnel({ conversion: .5 })} />);
    expect(screen.getByText(/历史分类不完整/)).toBeInTheDocument();
    expect(screen.getByTestId("ai-open-funnel-card").textContent).not.toContain("50.0%");
    view.rerender(<OpenFunnelCard funnel={funnel({
      schemaVersion: 2, coverage: "complete", intents: 4, blocked: {},
      executionBlocked: { below_min_size: 2 }, outcomeUnknown: 1,
      submitted: 1, scope: "当前运行段累计",
    })} />);
    expect(screen.getByText("执行未提交")).toBeInTheDocument();
    expect(screen.getByText("回执待核实")).toBeInTheDocument();
    expect(screen.getByText("已接收订单")).toBeInTheDocument();
    expect(screen.queryByTestId("ai-open-funnel-broken")).toBeNull();
  });
  it("老会话缺键 ⇒ 整卡不渲染且不崩（渲染门控）", () => {
    const { container } = render(<OpenFunnelCard funnel={null} />);
    expect(container).toBeEmptyDOMElement();
    render(<OpenFunnelCard funnel={undefined} />);
    expect(screen.queryByTestId("ai-open-funnel-card")).toBeNull();
  });

  it("零开仓意愿 ⇒ 显式写「样本不足」，绝不出现任何百分比", () => {
    render(<OpenFunnelCard funnel={funnel({ intents: 0, blocked: {}, conversion: null })} />);
    const empty = screen.getByTestId("ai-open-funnel-empty");
    expect(empty).toHaveTextContent("样本不足");
    expect(empty).toHaveTextContent("尚未提出过任何开仓意图");
    expect(screen.queryByTestId("ai-open-funnel-stages")).toBeNull();
    expect(screen.getByTestId("ai-open-funnel-card").textContent).not.toMatch(/\d+\.\d%/);
  });

  it("有样本 ⇒ 画五格漏斗 + 逐层拦截明细 + 口径词", () => {
    render(<OpenFunnelCard funnel={funnel({ conversion: 0.0 })} />);
    expect(screen.getByTestId("ai-open-funnel-stages")).toBeInTheDocument();
    expect(screen.getByTestId("ai-open-funnel-blocker-edgeGate")).toHaveTextContent("11 次");
    expect(screen.getByTestId("ai-open-funnel-card")).toHaveTextContent("本会话全程累计");
    // 口径必须写清：只归给第一个拦下它的闸，且平仓侧不进本图。
    expect(screen.getByTestId("ai-open-funnel-card")).toHaveTextContent("只归给第一个拦下它的闸");
    expect(screen.getByTestId("ai-open-funnel-card")).toHaveTextContent("平仓侧");
  });

  it("不闭合的数据必须被显式警示，而不是悄悄画一张错图", () => {
    render(<OpenFunnelCard funnel={funnel({ intents: 2, blocked: { edgeGate: 5 } })} />);
    expect(screen.getByTestId("ai-open-funnel-broken")).toHaveTextContent("不闭合");
  });

  it("llmDegraded 的中文标签是「模型调用失败」而不是「降级」", () => {
    render(<OpenFunnelCard funnel={funnel({ intents: 0, blocked: {}, llmDegraded: 3 })} />);
    expect(screen.getByTestId("ai-open-funnel-empty")).toHaveTextContent("模型调用失败");
  });
});
