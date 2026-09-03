import "@testing-library/jest-dom/vitest";
import fs from "node:fs";
import path from "node:path";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import IntelBindingStrip from "./IntelBindingStrip";
import type { AiTradeIntelBinding, AiTradeIntelSummary } from "@/lib/intelTypes";

// 情报弹窗顶部的绑定条（intel-session-binding 轴 C-3 · W6 / W3 的源码门禁）。

function binding(over: Partial<AiTradeIntelBinding> = {}): AiTradeIntelBinding {
  return {
    bound: true, groupId: "aig-1", mode: "index", injecting: true, reason: "",
    digestItemIds: [1201, 1198],
    lastRound: { round: 42, ts: 1_788_000_000, tier: "repeat", shown: 1, same: 5,
                 hidden: 3, chars: 118 },
    ...over,
  };
}

function summary(over: Partial<AiTradeIntelBinding> | null): AiTradeIntelSummary {
  return { binding: over === null ? null : binding(over) } as unknown as AiTradeIntelSummary;
}

afterEach(cleanup);

describe("IntelBindingStrip", () => {
  it("W6①：已采信 ⇒ 一行结论 + 档位 + 上一轮读数（`same` 与 `hidden` 分开说）", () => {
    render(<IntelBindingStrip summary={summary({})} />);
    const line = screen.getByTestId("intel-binding-headline");
    expect(line).toHaveTextContent("本次 AI 自动交易正在采信这些情报 · 索引档");
    expect(line).toHaveTextContent("上一轮进入上下文 1 条");
    expect(line).toHaveTextContent("另有 5 条与上一轮相同、未重复列出");
    expect(line).toHaveTextContent("另有 3 条因篇幅未展示");
  });

  it("W6②：未采信 ⇒ 说明 + 出路，且**不渲染任何可点的开关**", () => {
    render(<IntelBindingStrip summary={summary({ injecting: false, mode: "off",
                                                 reason: "session_off" })} />);
    expect(screen.getByTestId("intel-binding-headline")).toHaveTextContent(
      "本次 AI 自动交易未采信情报（启动时未开启）");
    expect(screen.getByTestId("intel-binding-session-off")).toHaveTextContent(
      "下次启动 AI 自动交易时可以在弹窗里勾选");
    // 会话中途改变证据面会让同一次会话的前后两半不可比 ⇒ 这里绝不给一个此刻点不动的开关。
    expect(screen.queryByRole("checkbox")).not.toBeInTheDocument();
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });

  it("W6③：未绑定 ⇒ 整条不渲染（对一个没在跑自动交易的标的说「未采信」是噪音）", () => {
    expect(render(<IntelBindingStrip summary={summary(null)} />).container)
      .toBeEmptyDOMElement();
    cleanup();
    expect(render(<IntelBindingStrip summary={null} />).container).toBeEmptyDOMElement();
    cleanup();
    expect(render(<IntelBindingStrip summary={summary({ bound: false })} />).container)
      .toBeEmptyDOMElement();
  });

  it("从未渲染过任何一轮（lastRound=null）⇒ 只留结论那半句，不编一个 0", () => {
    render(<IntelBindingStrip summary={summary({ lastRound: null })} />);
    const line = screen.getByTestId("intel-binding-headline");
    expect(line).toHaveTextContent("正在采信");
    expect(line).not.toHaveTextContent("上一轮进入上下文");
  });

  it("「什么是索引档？」默认收起——界面上的渐进式披露与 prompt 里的是同一条原则", () => {
    render(<IntelBindingStrip summary={summary({})} />);
    const details = screen.getByText("什么是索引档？").closest("details");
    expect(details).not.toHaveAttribute("open");
  });
});

describe("W3 源码门禁：两个字数与那个比值都不许硬编码在前端", () => {
  it("`AiTradeIntelFields.tsx` 全文不出现字面量 480 / 1200 / 2.5", () => {
    // 正则写 `\r?\n`：本仓库 `core.autocrlf=true`，写死 `\n` 会在干净检出上恒红。
    const file = path.join(process.cwd(), "components", "Agent", "AiTradeIntelFields.tsx");
    const text = fs.readFileSync(file, "utf8").replace(/\/\*[\s\S]*?\*\//g, "");
    const code = text.replace(/(^|[^:])\/\/.*(\r?\n)/g, "$1$2");
    expect(code).not.toMatch(/\b480\b/);
    expect(code).not.toMatch(/\b1200\b/);
    expect(code).not.toMatch(/\b2\.5\b/);
  });
});
