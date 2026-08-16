import { describe, expect, it } from "vitest";
import fs from "node:fs";
import path from "node:path";

// 「减少动态效果」偏好的源码扫描（spec D7）。
//
// 为什么是源码扫描：jsdom 不实现 CSS 媒体查询求值，`matchMedia` 也只是个桩，渲染用例钉不住
// 「这条规则真的写进了样式表」。而本条要守的恰恰就是这一件事——它是一条**全局**规则，一旦被
// 后来的重构顺手删掉，界面会静默地重新开始动，没有任何用例会红。
//
// core.autocrlf=true：跨行正则一律写 \r?\n，否则干净检出上恒红。

describe("尊重系统「减少动态效果」偏好（D7）", () => {
  const source = fs
    .readFileSync(path.resolve(__dirname, "globals.css"), "utf8")
    .replace(/\r?\n/g, "\n");

  it("globals.css 里有 prefers-reduced-motion: reduce 段", () => {
    expect(source).toMatch(/@media\s*\(prefers-reduced-motion:\s*reduce\)\s*\{/);
  });

  it("该段同时压住 animation 与 transition（只关一半等于没关）", () => {
    const block = source.match(
      /@media\s*\(prefers-reduced-motion:\s*reduce\)\s*\{[\s\S]*?\n\}/,
    )?.[0];
    expect(block).toBeTruthy();
    expect(block).toContain("animation-duration");
    expect(block).toContain("animation-iteration-count");
    expect(block).toContain("transition-duration");
  });
});
