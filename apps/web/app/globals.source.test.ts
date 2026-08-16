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

// 墨色（ink）分层的取值门禁（spec D2 / G9①）。
//
// 面色 `--accent` / `--long` / `--short` 是为 K 线蜡烛与色块设计的，作为正文字号的文字在浅色
// 主题下全线不达 WCAG AA（2.86–4.45），而它们承载的恰恰是这个产品的主要信息：盈亏数字与
// 20 余处 role="alert" 的错误文案。本轮新增三个墨色 + 一个反色把**文字**用法迁过去，面色取值一动不动。
//
// **必须在测试里重算，不得硬编码期望值。** 上一轮的教训原文是「一个为 AA 而生的 token 在自己
// 设计的那个配对上不达 AA」——只有让测试自己算，才挡得住「改了颜色忘了改断言」。
describe("墨色分层与配对对比度矩阵（D2 / G9①）", () => {
  const source = fs
    .readFileSync(path.resolve(__dirname, "globals.css"), "utf8")
    .replace(/\r?\n/g, "\n");

  /** 解析一个主题作用域里的全部 `--token: #rrggbb;`。 */
  function tokens(selector: string): Record<string, string> {
    const scope = source.match(new RegExp(selector + "\\s*\\{[\\s\\S]*?\\n\\}", "m"))?.[0];
    expect(scope, `未能在 globals.css 里定位 ${selector} 段`).toBeTruthy();
    const out: Record<string, string> = {};
    for (const m of (scope as string).matchAll(/(--[a-z0-9-]+):\s*(#[0-9a-fA-F]{6})\s*;/g)) {
      out[m[1]] = m[2];
    }
    return out;
  }

  const THEMES = [
    { name: "light", selector: "^:root" },
    { name: "dark", selector: "^\\.dark" },
  ];
  const INK_SURFACES = ["--bg", "--surface", "--surface-2", "--accent-soft"];

  // 判定集合显式列出——一条判据不可判定的门禁不是门禁。
  //
  // ⚠️ `--short-ink` × `--accent-soft` **不在**矩阵内（暗色实测 4.41）：那个配对此前唯一的落点是
  // `Button variant="danger"` 的 hover 底，本轮已把它改成 `--surface-2`，此后二者在产品里不再相遇。
  // 这条排除**不是**放宽阈值，它由 `components/ui/contrastTokens.source.test.ts` 里
  // 「`--short-ink` 不得落在 `--accent-soft` 底上」的用法扫描看守；把 hover 底改回去，那条立刻红。
  const PAIRS: { fg: string; bgs: string[] }[] = [
    { fg: "--accent-ink", bgs: INK_SURFACES },
    { fg: "--long-ink", bgs: INK_SURFACES },
    { fg: "--short-ink", bgs: ["--bg", "--surface", "--surface-2"] },
    { fg: "--on-accent", bgs: ["--accent"] },
    { fg: "--warn", bgs: ["--warn-soft"] },
  ];

  it.each(THEMES)("$name 主题定义了全部四个新 token", ({ selector }) => {
    const scope = tokens(selector);
    for (const name of ["--accent-ink", "--long-ink", "--short-ink", "--on-accent"]) {
      expect(scope[name], `${name} 未在 ${selector} 中定义`).toMatch(/^#[0-9a-fA-F]{6}$/);
    }
  });

  it.each(THEMES)("$name 主题：配对矩阵逐格 ≥ 4.5（现算，不硬编码）", ({ selector }) => {
    const scope = tokens(selector);
    const failures: string[] = [];
    for (const { fg, bgs } of PAIRS) {
      for (const bg of bgs) {
        const ratio = contrastRatio(scope[fg], scope[bg]);
        if (ratio < 4.5) failures.push(`${fg} on ${bg} = ${ratio.toFixed(2)}`);
      }
    }
    expect(failures, `低于 AA 的配对：${failures.join("；")}`).toEqual([]);
  });

  // 暗色三个 ink 的取值**等于原面色**，这是覆盖 301 处机械替换的那张验证网：任何一处把面色误改成
  // 墨色（或反之）都会在暗色下逐像素可见。改动这三行等于把那张网拆掉，故单独钉一条。
  it("暗色 ink 取值等于对应面色（301 处机械替换的验证网）", () => {
    const dark = tokens("^\\.dark");
    expect(dark["--accent-ink"]).toBe(dark["--accent"]);
    expect(dark["--long-ink"]).toBe(dark["--long"]);
    expect(dark["--short-ink"]).toBe(dark["--short"]);
  });

  // 反过来的一条：面色取值必须**没有**被本轮改动，否则图表 series、状态点、进度条会跟着漂移。
  it("面色取值保持不变（图表 series / 状态点 / 边框零变化）", () => {
    const light = tokens("^:root");
    expect(light["--accent"]).toBe("#cc785c");
    expect(light["--long"]).toBe("#3b8c6e");
    expect(light["--short"]).toBe("#c0573e");
    const dark = tokens("^\\.dark");
    expect(dark["--accent"]).toBe("#d98a6e");
    expect(dark["--long"]).toBe("#5fb38c");
    expect(dark["--short"]).toBe("#d97a62");
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
