import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import type { AiTradeRoundTrace } from "@/lib/aiTradeTypes";
import AiTradeReasoningBlock from "./AiTradeReasoningBlock";

// 分阶段研判视图（ai-trade-deep-reasoning-chain §7.6 F-5 … F-10）。
// 安全铁律回归（F-8）：模型输出即不可信输入——正文走 `renderMarkdown`，绝不产生原始 HTML。

afterEach(cleanup);

function twelveStages(): string {
  const parts: string[] = [];
  for (let i = 1; i <= 12; i += 1) parts.push(`## S${i} CODE${i}\nbody of stage ${i}`);
  return parts.join("\n");
}

function trace(patch: Partial<AiTradeRoundTrace> = {}): AiTradeRoundTrace {
  return {
    round: 1,
    ts: 1_700_000_000,
    signalBoard: [],
    thinking: "",
    analysis: twelveStages(),
    reaskAnalysis: "",
    reasoning: {
      depth: "deep",
      chars: 900,
      stages: 12,
      truncated: false,
      reasked: false,
      reaskAdopted: false,
      deadlineHit: false,
      inputTokens: 12083,
      outputTokens: 3401,
      elapsedMs: 42_160,
    },
    state: "done",
    ...patch,
  } as AiTradeRoundTrace;
}

describe("AiTradeReasoningBlock", () => {
  it("collapses by default and prefers evidenceDigest as the headline", () => {
    render(
      <AiTradeReasoningBlock
        trace={trace({
          toolCall: {
            action: "hold",
            reason: "理由回退",
            evidenceDigest: "采纳 4H 趋势与量能配合，推翻布林回归信号。",
          },
        })}
      />,
    );
    expect(screen.getByText(/采纳 4H 趋势与量能配合/)).toBeInTheDocument();
    // 收起态不渲染逐阶段正文（渐进式披露）。
    expect(screen.queryByText("body of stage 4")).toBeNull();
    expect(screen.getByRole("button", { expanded: false })).toBeInTheDocument();
  });

  it("expands into per-stage details with S11 and S12 open by default", () => {
    render(<AiTradeReasoningBlock trace={trace()} />);
    fireEvent.click(screen.getByRole("button", { expanded: false }));
    // F-5：S11 / S12 默认展开，其余关闭。
    const s11 = screen.getByText(/S11 证伪与事前验尸/).closest("details");
    const s12 = screen.getByText(/S12 校准结论/).closest("details");
    const s1 = screen.getByText(/S1 多周期背景/).closest("details");
    expect(s11).toHaveAttribute("open");
    expect(s12).toHaveAttribute("open");
    expect(s1).not.toHaveAttribute("open");
  });

  it("shows the truncation chip on a budget-exhausted round", () => {
    // F-6。
    render(<AiTradeReasoningBlock trace={trace({ reasoning: { ...trace().reasoning!, truncated: true } })} />);
    expect(screen.getByText("输出预算耗尽（已截断）")).toBeInTheDocument();
  });

  it("shows the deadline chip when the round converged early", () => {
    render(
      <AiTradeReasoningBlock
        trace={trace({ reasoning: { ...trace().reasoning!, deadlineHit: true } })}
      />,
    );
    expect(screen.getByText("本轮超时预算用尽，已提前收敛")).toBeInTheDocument();
  });

  it("renders only the active stage tail while streaming", () => {
    // F-7：DOM 与文本长度解耦——流式期只渲染当前阶段的尾部若干行。
    const long = `${twelveStages()}\n${Array.from({ length: 400 }, (_, i) => `l${i}`).join("\n")}`;
    const { container } = render(
      <AiTradeReasoningBlock trace={trace({ analysis: long, state: "deciding" })} />,
    );
    expect(screen.getByText(/深度研判中 · 阶段 12\/12/)).toBeInTheDocument();
    expect(container.textContent ?? "").not.toContain("l0\n");
    expect(container.textContent ?? "").toContain("l399");
    // 早期阶段的正文在流式期**不**渲染（否则一面墙的文字会重新出现）。
    expect(container.textContent ?? "").not.toContain("body of stage 1");
  });

  it("renders model text through the safe markdown path", () => {
    // F-8：安全铁律回归——绝不产生原始 HTML，`javascript:` 链接不得成为可点 href。
    const hostile =
      '## S12 VERDICT\n<img src=x onerror="alert(1)">\n[click](javascript:alert(1))';
    const { container } = render(<AiTradeReasoningBlock trace={trace({ analysis: hostile })} />);
    fireEvent.click(screen.getByRole("button", { expanded: false }));
    // 没有真的 <img> 元素、没有任何 onerror 属性——恶意标记被**转义成文字**而不是被渲染。
    expect(container.querySelector("img")).toBeNull();
    expect(container.querySelector("[onerror]")).toBeNull();
    expect(container.innerHTML).toContain("&lt;img");
    // `javascript:` 链接不得成为可点 href（渲染层只放行 http(s)）。
    const link = container.querySelector("a");
    expect(link?.getAttribute("href") ?? "").not.toContain("javascript:");
  });

  it("keeps the reask segment beside the first analysis, never overwriting it", () => {
    // F-9：两段**并列存在**；未采纳的那一份不隐藏（它是「第二次反而更浅」的现场证据）。
    render(
      <AiTradeReasoningBlock
        trace={trace({
          reaskAnalysis: "## S1 CODE1\nsecond pass",
          reasoning: { ...trace().reasoning!, reasked: true, reaskAdopted: false },
        })}
      />,
    );
    fireEvent.click(screen.getByRole("button", { expanded: false }));
    expect(screen.getByText("深化追问（未采纳，保留首次研判）")).toBeInTheDocument();
    // 首次研判仍在（12/12 阶段导轨未被追加成 24/12）。
    expect(screen.getByText(/12\/12 阶段/)).toBeInTheDocument();
  });

  it("labels an adopted reask differently", () => {
    render(
      <AiTradeReasoningBlock
        trace={trace({
          reaskAnalysis: "## S1 CODE1\ndeeper",
          reasoning: { ...trace().reasoning!, reasked: true, reaskAdopted: true },
        })}
      />,
    );
    fireEvent.click(screen.getByRole("button", { expanded: false }));
    expect(screen.getByText("深化追问（已采纳）")).toBeInTheDocument();
  });

  it("keeps the thinking block through the component swap", () => {
    // F-10：`trace.thinking` 非空 ⇒ 新组件里仍有「深度思考」折叠块；为空 ⇒ 整块不渲染。
    render(<AiTradeReasoningBlock trace={trace({ thinking: "internal notes" })} />);
    expect(screen.getByText("深度思考")).toBeInTheDocument();
    cleanup();
    render(<AiTradeReasoningBlock trace={trace()} />);
    expect(screen.queryByText("深度思考")).toBeNull();
  });

  it("falls back to flat rendering when the model ignored the protocol", () => {
    // F-3 的呈现侧：无阶段标记 ⇒ 「未分阶段」标识 + 整段渲染。
    render(<AiTradeReasoningBlock trace={trace({ analysis: "a wall of text" })} />);
    fireEvent.click(screen.getByRole("button", { expanded: false }));
    expect(screen.getByText("未分阶段")).toBeInTheDocument();
    expect(screen.getByText("a wall of text")).toBeInTheDocument();
  });

  it("keeps the pre-merge streaming look on the un-staged fallback (off depth parity)", () => {
    // §7.7 第 3 条：`off` 档下界面必须与合入前**没有任何差异**。那条路走的正是「无阶段标记」
    // 的回退分支，故它**不**做尾部截断——只有按协议分段的那条路径才需要 DOM 解耦。
    const flat = Array.from({ length: 40 }, (_, i) => `line ${i}`).join("\n");
    const { container } = render(
      <AiTradeReasoningBlock
        trace={trace({ analysis: flat, state: "deciding", reasoning: null })}
      />,
    );
    expect(container.textContent ?? "").toContain("line 0");
    expect(container.textContent ?? "").toContain("line 39");
  });

  it("renders nothing at all without analysis, thinking or an in-flight round", () => {
    const { container } = render(
      <AiTradeReasoningBlock trace={trace({ analysis: "", thinking: "", reasoning: null })} />,
    );
    expect(container.firstChild).toBeNull();
  });

  it("names the missing stages honestly instead of hiding them", () => {
    const dropped = twelveStages().replace("## S11 CODE11\nbody of stage 11\n", "");
    render(<AiTradeReasoningBlock trace={trace({ analysis: dropped })} />);
    expect(screen.getByText(/11\/12 阶段/)).toBeInTheDocument();
    expect(screen.getByText(/缺 1 段/)).toBeInTheDocument();
  });

  it("survives a degraded round settling from deciding to done with an empty analysis", () => {
    // 终审回归：降级轮（LLM 超时 / 未产出工具调用）的 `analysis` 恒为空串，于是轮次从
    // `deciding` 走到 `done` 时本组件从「渲染」变成「早退 null」。早退若写在 `useMemo` 之前，
    // 本次渲染就比上一次少调一个 Hook，React 抛「Rendered fewer hooks than expected」，
    // 整个轨迹面板随之崩掉。单独挂载一个空轮次的用例拿不到它——它只在**重渲染**时成立。
    const { container, rerender } = render(
      <AiTradeReasoningBlock
        trace={trace({ analysis: "", thinking: "", reasoning: null, state: "deciding" })}
      />,
    );
    expect(container.textContent ?? "").toContain("正在研判");
    rerender(
      <AiTradeReasoningBlock
        trace={trace({ analysis: "", thinking: "", reasoning: null, state: "done" })}
      />,
    );
    expect(container.firstChild).toBeNull();
  });
});
