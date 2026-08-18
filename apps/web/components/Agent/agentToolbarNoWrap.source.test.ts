import { describe, expect, it } from "vitest";
import fs from "node:fs";
import path from "node:path";

// AI 助手面板两横栏的**排版契约**源码门禁（agent-panel-toolbar-two-rows §9.1 C）。
//
// 「按钮文案不折行」在这个仓库里没有任何基类保障：Button / Badge 都不带 whitespace-nowrap，而中文
// 的最小内容宽度就是一个字 —— 压缩的结果必然是「新对 / 话」「暂 / 停」这种把词劈成两半的排版。
// 本轮的解法是行级继承（两条横栏各写一次 whitespace-nowrap）+ 唯一一处 min-w-0/truncate 吸收压力，
// 它全部由类名承载，因此**一次误删就静默失效**：jsdom 没有布局引擎，组件用例察觉不到像素回归。
// 这四条断言就是那道防线。
//
// core.autocrlf=true：先把 \r\n 归一化再匹配，否则干净检出上跨行匹配恒红。

const AGENT_DIR = __dirname;

function readSource(name: string): string {
  return fs.readFileSync(path.join(AGENT_DIR, name), "utf8").replace(/\r\n/g, "\n");
}

const PANEL = readSource("AgentPanel.tsx");
const TOGGLE = readSource("AiTradeToggle.tsx");

/** 顶栏（横栏 A）的开标签。 */
function headerOpenTag(): string {
  const m = PANEL.match(/<header[^>]*>/);
  expect(m, "AgentPanel.tsx 里找不到 <header> 开标签").not.toBeNull();
  return m![0];
}

/** 控制条（横栏 B）的开标签。 */
function controlBarOpenTag(): string {
  const m = PANEL.match(/<div[^>]*data-testid="ai-trade-control-bar"[^>]*>/);
  expect(m, 'AgentPanel.tsx 里找不到 data-testid="ai-trade-control-bar" 的容器').not.toBeNull();
  return m![0];
}

describe("AI 助手工具栏两横栏的排版契约（源码冻结）", () => {
  it("S6：两个源文件都真的被读到且非空（否则下面几条会因路径写错而空转变绿）", () => {
    expect(PANEL.length).toBeGreaterThan(1000);
    expect(TOGGLE.length).toBeGreaterThan(1000);
    expect(PANEL).toContain("AgentComposer");
    expect(TOGGLE).toContain("AI 自动交易");
  });

  it("S1：顶栏行上声明了 whitespace-nowrap", () => {
    expect(headerOpenTag()).toContain("whitespace-nowrap");
  });

  it("S2：控制条行上声明了 whitespace-nowrap，并以 border-t 与输入框区分隔", () => {
    const tag = controlBarOpenTag();
    expect(tag).toContain("whitespace-nowrap");
    expect(tag).toContain("border-t");
  });

  it("S3：<header> 内不得再出现 AiTradeToggle（防止有人顺手把它加回顶栏）", () => {
    const block = PANEL.match(/<header[\s\S]*?<\/header>/);
    expect(block, "AgentPanel.tsx 里找不到完整的 <header> … </header>").not.toBeNull();
    expect(block![0]).not.toContain("AiTradeToggle");
    // 但它必须仍被这个文件挂着 —— 否则整簇控件就凭空消失了，而上一条照样是绿的。
    expect(PANEL).toContain("<AiTradeToggle />");
  });

  it("S4：AiTradeToggle.tsx 不再出现 max-w-[220px]（错误文案已独占第二行，不再截断）", () => {
    expect(TOGGLE).not.toContain("max-w-[220px]");
    expect(TOGGLE).toContain("whitespace-normal");
  });

  it("S5：控制条底色不得改成 --accent-soft（暗色下 --short-ink 落到 4.41，是对比度矩阵的排除项）", () => {
    expect(controlBarOpenTag()).not.toContain("bg-[var(--accent-soft)]");
  });
});
