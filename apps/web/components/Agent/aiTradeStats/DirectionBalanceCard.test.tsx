import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import DirectionBalanceCard from "./DirectionBalanceCard";
import type {
  AiTradeDirectionBalance,
  AiTradeOpenFunnel,
  AiTradeSideFunnel,
} from "@/lib/aiTradeTypes";

// 方向分布卡（ai-trade-directional-symmetry 轴 D · D-4）。
//
// 四条守卫，每一条都对应一次「界面说了一句数据不支持的话」的风险：
// 1. 老会话 / 总闸关（缺方向轴）⇒ 整卡不渲染，绝不挂一排 0；
// 2. 零方向样本 ⇒ 写「样本不足」，绝不造百分比；
// 3. **两种病因的文案不可互换**——「提过做空但被拦」要动纪律旋钮，「从未提过做空」要查信号板；
// 4. `unknownIntents > 0` ⇒ 披露句必须出现（它占实测样本的 21%，不披露就会把结论坐实成假话）。

const LAYERS = ["baseRuleMaBand", "consensus", "confidence", "edgeGate", "other"];

function side(over: Partial<AiTradeSideFunnel> = {}): AiTradeSideFunnel {
  return { intents: 0, blocked: {}, sizingZeroed: 0, submitted: 0, filled: 0, ...over };
}

function balance(over: Partial<AiTradeDirectionBalance> = {}): AiTradeDirectionBalance {
  return {
    longIntents: 0,
    shortIntents: 0,
    longFilled: 0,
    shortFilled: 0,
    shortIntentShare: null,
    shortFillShare: null,
    unknownIntents: 0,
    unknownFilled: 0,
    contrarianPassed: 0,
    minSamples: 8,
    verdict: "insufficient",
    ...over,
  };
}

function funnel(
  bySide: Partial<Record<"long" | "short" | "unknown", AiTradeSideFunnel>>,
  bal: Partial<AiTradeDirectionBalance> = {},
): AiTradeOpenFunnel {
  return {
    intents: 0,
    blocked: {},
    topBlocker: null,
    llmDegraded: 0,
    sizingZeroed: 0,
    submitted: 0,
    filled: 0,
    conversion: null,
    layers: LAYERS,
    scope: "本会话全程累计",
    bySide: {
      long: side(bySide.long),
      short: side(bySide.short),
      unknown: side(bySide.unknown),
    },
    directionBalance: balance(bal),
  };
}

afterEach(cleanup);

describe("DirectionBalanceCard", () => {
  it("老会话 / 总闸关（无方向轴）⇒ 整卡不渲染且不崩", () => {
    const { container } = render(<DirectionBalanceCard funnel={null} />);
    expect(container).toBeEmptyDOMElement();
    render(<DirectionBalanceCard funnel={undefined} />);
    expect(screen.queryByTestId("ai-direction-balance-card")).toBeNull();
  });

  it("有 openFunnel 但缺方向轴（老后端）⇒ 同样整卡不渲染", () => {
    const legacy = { ...funnel({}), bySide: null, directionBalance: null } as AiTradeOpenFunnel;
    const { container } = render(<DirectionBalanceCard funnel={legacy} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("方向可辨样本不足 ⇒ 写「样本不足」，且不出现任何百分比", () => {
    render(
      <DirectionBalanceCard
        funnel={funnel(
          { long: side({ intents: 3, filled: 1 }) },
          { longIntents: 3, longFilled: 1, verdict: "insufficient" },
        )}
      />,
    );
    const verdict = screen.getByTestId("ai-direction-verdict");
    expect(verdict).toHaveTextContent("样本不足");
    expect(verdict.textContent ?? "").not.toMatch(/%/);
  });

  it("提过做空但被拦下 ⇒ 点名拦截层且指向「放宽逆势通路」（门禁问题）", () => {
    render(
      <DirectionBalanceCard
        funnel={funnel(
          {
            long: side({ intents: 41, filled: 41 }),
            short: side({ intents: 6, blocked: { consensus: 5 }, filled: 1 }),
          },
          {
            longIntents: 41,
            shortIntents: 6,
            longFilled: 41,
            shortFilled: 1,
            shortIntentShare: 6 / 47,
            verdict: "long_skewed",
          },
        )}
      />,
    );
    const verdict = screen.getByTestId("ai-direction-verdict");
    expect(verdict).toHaveTextContent("提出过 6 次做空意愿");
    expect(verdict).toHaveTextContent("同向共识门槛");
    expect(verdict).toHaveTextContent("逆势通路");
    // 病因不可互换：这一支绝不能说「不是门禁问题」。
    expect(verdict.textContent ?? "").not.toContain("从未提出过");
  });

  it("从未提过做空 ⇒ 明说「不是门禁问题」，并指向信号板与研判协议", () => {
    render(
      <DirectionBalanceCard
        funnel={funnel(
          { long: side({ intents: 41, filled: 41 }) },
          {
            longIntents: 41,
            longFilled: 41,
            shortIntentShare: 0,
            verdict: "long_skewed",
          },
        )}
      />,
    );
    const verdict = screen.getByTestId("ai-direction-verdict");
    expect(verdict).toHaveTextContent("从未提出过做空意愿");
    expect(verdict).toHaveTextContent("这不是门禁问题");
    expect(verdict).toHaveTextContent("信号板");
    // 病因不可互换：这一支绝不能把用户引向纪律旋钮（那是另一种病因的补救动作）。
    expect(verdict.textContent ?? "").not.toContain("可在启动弹窗放宽");
    expect(verdict.textContent ?? "").not.toContain("被「");
  });

  it("unknownIntents > 0 ⇒ 披露句必须出现，且方向不可知那一行有可见落点", () => {
    render(
      <DirectionBalanceCard
        funnel={funnel(
          {
            long: side({ intents: 41, filled: 41 }),
            unknown: side({ intents: 12, blocked: { confidence: 12 } }),
          },
          { longIntents: 41, longFilled: 41, unknownIntents: 12, verdict: "long_skewed" },
        )}
      />,
    );
    expect(screen.getByTestId("ai-direction-verdict")).toHaveTextContent(
      "另有 12 轮在方向可见之前就被「置信度门槛」拦下",
    );
    expect(screen.getByTestId("ai-direction-row-unknown")).toHaveTextContent("想开 12");
  });

  it("两侧都提过 ⇒ 判 balanced 并如实给出做空占比；中心 0 轴始终可见", () => {
    render(
      <DirectionBalanceCard
        funnel={funnel(
          {
            long: side({ intents: 10, filled: 6 }),
            short: side({ intents: 8, filled: 4 }),
          },
          {
            longIntents: 10,
            shortIntents: 8,
            longFilled: 6,
            shortFilled: 4,
            shortIntentShare: 8 / 18,
            shortFillShare: 0.4,
            verdict: "balanced",
          },
        )}
      />,
    );
    expect(screen.getByTestId("ai-direction-verdict")).toHaveTextContent("方向没有塌缩");
    expect(screen.getByTestId("ai-direction-axis")).toBeInTheDocument();
    expect(screen.getByTestId("ai-direction-bar-short")).toBeInTheDocument();
  });
});
