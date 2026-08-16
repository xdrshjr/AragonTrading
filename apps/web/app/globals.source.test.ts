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

// 警告语义色 token 的存在性门禁（spec D5）。
//
// 这个 token 此前**根本不存在**：六处调用各自内联回退（`#d97706` 三处、`var(--short)` 两处、
// 还有一处把变量名拼成了 `--warning`），于是同一个「警告」在同一个应用里渲染成两种颜色。
// 内联回退正是这类问题的温床——它让「token 不存在」这件事永远不会被发现，所以本轮把回退全删了，
// 而删了之后就必须有东西守着「token 真的在两个主题里都定义了」。
describe("警告语义色 token（D5）", () => {
  const source = fs
    .readFileSync(path.resolve(__dirname, "globals.css"), "utf8")
    .replace(/\r?\n/g, "\n");

  function block(selector: string): string {
    const matched = source.match(new RegExp(selector + "\\s*\\{[\\s\\S]*?\\n\\}", "m"))?.[0];
    expect(matched, `未能在 globals.css 里定位 ${selector} 段`).toBeTruthy();
    return matched as string;
  }

  it(":root 同时定义 --warn 与 --warn-soft", () => {
    const root = block("^:root");
    expect(root).toMatch(/--warn:\s*#[0-9a-fA-F]{6}\s*;/);
    expect(root).toMatch(/--warn-soft:\s*#[0-9a-fA-F]{6}\s*;/);
  });

  it(".dark 同时定义 --warn 与 --warn-soft（语义色必须是双主题的）", () => {
    const dark = block("^\\.dark");
    expect(dark).toMatch(/--warn:\s*#[0-9a-fA-F]{6}\s*;/);
    expect(dark).toMatch(/--warn-soft:\s*#[0-9a-fA-F]{6}\s*;/);
  });

  // 护栏：`--warn` 是为 AA 而生的 token，它必须在**自己配套的 --warn-soft 底色**上也达标。
  // v1 选的 #9a5a0d 在三种页面底色上都过，却在 --warn-soft 上只有 4.46 —— 一个为 AA 而生的
  // token 在自己设计的那个配对上不达 AA，是最不该留的尾巴。下次有人调浅 --warn，这条会红。
  it("--warn 与 --warn-soft 的配对对比度 ≥ 4.5（两个主题都是）", () => {
    for (const selector of ["^:root", "^\\.dark"]) {
      const scope = block(selector);
      const warn = scope.match(/--warn:\s*(#[0-9a-fA-F]{6})/)?.[1] as string;
      const soft = scope.match(/--warn-soft:\s*(#[0-9a-fA-F]{6})/)?.[1] as string;
      expect(contrastRatio(warn, soft)).toBeGreaterThanOrEqual(4.5);
    }
  });
});

/** WCAG 2.1 相对亮度（sRGB → 线性化 → 加权和）。 */
function relativeLuminance(hex: string): number {
  const channels = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255);
  const [r, g, b] = channels.map((c) => (c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4));
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

function contrastRatio(a: string, b: string): number {
  const [lighter, darker] = [relativeLuminance(a), relativeLuminance(b)].sort((x, y) => y - x);
  return (lighter + 0.05) / (darker + 0.05);
}
