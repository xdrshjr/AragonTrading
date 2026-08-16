import { describe, expect, it } from "vitest";
import fs from "node:fs";
import path from "node:path";

// 警告语义色的**单一写法**门禁（spec D5）。
//
// 修法的一半是补上 `--warn` / `--warn-soft` 两个 token（见 `app/globals.source.test.ts`），
// 另一半是把六处调用统一到 `var(--warn)` 并**删掉全部内联回退**。回退值是这类问题的温床：
// 它让「token 不存在」这件事永远不会被发现——这个仓库正是因此让一个不存在的 token 活了整整
// 一个项目周期，期间警告文案在浅色主题下的对比度只有 2.78–3.16:1（AA 门槛 4.5）。
//
// 顺带钉住那个**拼错的变量名**：`--warning` 与 `--warn` 只差两个字母，而写错的那一处永远只会
// 渲染成内联回退色，看起来「能用」。
//
// core.autocrlf=true：跨行正则一律写 \r?\n，否则干净检出上恒红。

// 扫描根与 `contrastTokens.source.test.ts` 的通用扫描取齐：`components/` + `app/` + `lib/`。
// 原来只扫 `components/`，而本轮在 `lib/` 里就抓到了两处同类问题（面色当文字、指向未定义 token 的
// 内联回退）——一个只覆盖一半代码树的门禁，挡不住漂到另一半去的同一个缺陷。
const WEB_ROOT = path.resolve(__dirname, "..", "..");
const SCAN_ROOTS = ["components", "app", "lib"].map((name) => path.join(WEB_ROOT, name));

function collectSources(dir: string): { file: string; text: string }[] {
  const out: { file: string; text: string }[] = [];
  if (!fs.existsSync(dir)) return out;
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    if (entry.name === "node_modules" || entry.name.startsWith(".")) continue;
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) {
      out.push(...collectSources(full));
    } else if (/\.tsx?$/.test(entry.name)) {
      out.push({
        file: path.relative(WEB_ROOT, full).replace(/\\/g, "/"),
        text: fs.readFileSync(full, "utf8"),
      });
    }
  }
  return out;
}

describe("警告语义色的单一写法（D5）", () => {
  const sources = SCAN_ROOTS.flatMap(collectSources).filter(
    (s) => !/\.(source\.)?test\.tsx?$/.test(s.file),
  );

  it("扫描根覆盖到了 components / app / lib 三处", () => {
    expect(sources.some((s) => s.file.startsWith("components/"))).toBe(true);
    expect(sources.some((s) => s.file.startsWith("app/"))).toBe(true);
    expect(sources.some((s) => s.file.startsWith("lib/"))).toBe(true);
  });

  it("components/ 下不再出现拼错的 --warning", () => {
    const offenders = sources.filter((s) => /--warning\b/.test(s.text)).map((s) => s.file);
    expect(offenders).toEqual([]);
  });

  it("components/ 下不再出现硬编码的 #d97706", () => {
    const offenders = sources.filter((s) => /#d97706/i.test(s.text)).map((s) => s.file);
    expect(offenders).toEqual([]);
  });

  it("components/ 下的 var(--warn …) 一律无内联回退", () => {
    // `var(--warn, X)` 形态即回退；`var(--warn)` 与 `var(--warn-soft)` 是允许的写法。
    const offenders = sources
      .filter((s) => /var\(\s*--warn\s*,/.test(s.text) || /var\(\s*--warn-soft\s*,/.test(s.text))
      .map((s) => s.file);
    expect(offenders).toEqual([]);
  });

  it("至少仍有一处在用 var(--warn)（否则这三条断言会因为无人使用而空转变绿）", () => {
    const users = sources.filter((s) => /var\(--warn\)/.test(s.text));
    expect(users.length).toBeGreaterThan(0);
  });
});
