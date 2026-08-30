import fs from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";

// 源码扫描：冻结 E-1 的约束——`AiTradeMemberRow` 的根节点是一个 `<button>`，而按钮不能嵌套按钮。
// 情报按钮与离场卡因此都必须是它的**兄弟节点**。
//
// 正则一律写 `\r?\n`：本仓库 `core.autocrlf=true`，写死 `\n` 会在干净检出上恒红
// （先例：`contrastTokens.source.test.ts`）。
const CHART = path.join(process.cwd(), "components", "Chart");
const PANEL = fs.readFileSync(path.join(CHART, "AiTradePanel.tsx"), "utf8");
const ROW = fs.readFileSync(path.join(CHART, "AiTradeMemberRow.tsx"), "utf8");

describe("情报按钮绝不嵌进标的行", () => {
  it("AiTradeMemberRow 里不出现 AiTradeIntelButton", () => {
    expect(ROW).not.toMatch(/AiTradeIntelButton/);
  });

  it("AiTradeMemberRow 的根节点仍是 <button>（这条约束的**理由**本身）", () => {
    const body = ROW.split(/export default function AiTradeMemberRow/)[1] ?? "";
    expect(body).toMatch(/return\s*\(\s*\r?\n\s*<button/);
  });

  it("AiTradePanel 里情报按钮与行组件是兄弟节点，不是父子", () => {
    // 行组件与情报按钮各自独立成行；两者之间不得出现「行组件包住按钮」的形态。
    expect(PANEL).toMatch(/<AiTradeMemberRow[^>]*\/>/s);
    expect(PANEL).toMatch(/<AiTradeIntelButton instId=\{m\.instId\} \/>/);
    expect(PANEL).not.toMatch(/<AiTradeMemberRow[\s\S]*?<AiTradeIntelButton[\s\S]*?<\/AiTradeMemberRow>/);
  });

  it("行容器用**具名** group/member（匿名 group 会被最近的祖先接管）", () => {
    expect(PANEL).toMatch(/group\/member/);
  });

  it("两个文件都在 800 行阈值内（外移是本特性的前置步骤而非副产品）", () => {
    for (const [name, text] of [["AiTradePanel.tsx", PANEL], ["AiTradeMemberRow.tsx", ROW]] as const) {
      expect(text.split(/\r?\n/).length, name).toBeLessThan(800);
    }
  });
});
