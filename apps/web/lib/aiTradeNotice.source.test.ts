import { describe, expect, it } from "vitest";
import fs from "node:fs";
import path from "node:path";

// 本轮八条源码级门禁（ai-trade-notice-state-sync §8.3，G-N1 ~ G-N8）。
//
// 为什么全是源码扫描而不是渲染用例：这八条守的都是「**没有做**某件事」——没有绕过铸造点直写裸
// 字符串、没有留下一个不带纪元的旧写点、没有让读点绕过谓词、没有造出模块环、没有漏登记一种
// kind、没有让回测车道沾上一个符号、没有把面色当文字用、没有引用一个不存在的 token。
// 渲染用例只能证明现有代码的行为，证明不了「下一个人新写的那一处也遵守」；而这几类漏改的共同点
// 恰恰是 `tsc` 与 `vitest` 都不会红——本仓库为「新增枚举漏改一处落点」已经付过至少四次代价。
//
// core.autocrlf=true：跨行正则一律写 `\r?\n`，写死 `\n` 会在干净检出上恒红（成文教训）。

const WEB_ROOT = path.resolve(__dirname, "..");
const REPO_ROOT = path.resolve(WEB_ROOT, "..", "..");

type Source = { file: string; text: string };

/** 递归收集一棵子树下的 `.ts` / `.tsx`（排除测试与 node_modules）。 */
function collect(dir: string, out: Source[] = []): Source[] {
  if (!fs.existsSync(dir)) return out;
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    if (entry.name === "node_modules" || entry.name.startsWith(".")) continue;
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) {
      collect(full, out);
    } else if (/\.tsx?$/.test(entry.name) && !/\.test\.tsx?$/.test(entry.name)) {
      out.push({
        file: path.relative(WEB_ROOT, full).replace(/\\/g, "/"),
        text: fs.readFileSync(full, "utf8"),
      });
    }
  }
  return out;
}

const SOURCES = [
  ...collect(path.join(WEB_ROOT, "lib")),
  ...collect(path.join(WEB_ROOT, "components")),
  ...collect(path.join(WEB_ROOT, "app")),
];

function read(rel: string): string {
  return fs.readFileSync(path.join(WEB_ROOT, rel), "utf8");
}

describe("提示纪元的源码门禁（G-N1 ~ G-N8）", () => {
  it("扫描根真的扫到了东西（防止路径写错而八条一起空转变绿）", () => {
    expect(SOURCES.length).toBeGreaterThan(200);
    expect(SOURCES.some((s) => s.file === "lib/aiTradeNotice.ts")).toBe(true);
    expect(SOURCES.some((s) => s.file === "components/Agent/AiTradeNoticeBar.tsx")).toBe(true);
  });

  it("G-N1：`aiTradeNotice:` 作为 set 的键只出现在 lib/store.ts", () => {
    // 绕过 `noteAiTradeNotice` 直写一个裸对象 ⇒ 纪元丢失 ⇒ 这条提示再也不会自行退场，
    // 而那正是本轮修的那个缺陷的形状。
    const offenders = SOURCES.filter(
      (s) => s.file !== "lib/store.ts" && /\baiTradeNotice:\s/.test(s.text),
    ).map((s) => s.file);
    expect(offenders).toEqual([]);
  });

  it("G-N2：全仓非测试源码中不再存在 setAiTradeLifecycleNotice / aiTradeLifecycleNotice", () => {
    // 改名是**刻意**的（§5.4）：留着旧名，漏改的写点会悄悄编译通过。旧名还在就说明还有一个
    // 不带纪元的写点。
    const offenders = SOURCES.filter((s) => /aiTradeLifecycleNotice/.test(s.text)).map((s) => s.file);
    expect(offenders).toEqual([]);
  });

  it("G-N3：两个读点各至少出现一次 isAiTradeNoticeLive", () => {
    // 「有写点没读点」/「有读点但不过谓词」是本仓库复发过 N 次的形状。
    expect(read("components/Agent/AiTradeNoticeBar.tsx")).toContain("isAiTradeNoticeLive");
    expect(read("components/Chart/AiTradeMemberRow.tsx")).toContain("isAiTradeNoticeLive");
  });

  it("G-N4：lib/aiTrade.ts 不导入 ./aiTradeNotice（模块环的一端）", () => {
    const imports = /^\s*(?:import|export)\b[^\r\n]*from\s+"\.\/aiTradeNotice"/m;
    expect(imports.test(read("lib/aiTrade.ts"))).toBe(false);
  });

  it("G-N5：规则表里 validIn: 的出现次数 == AiTradeNoticeKind 的联合成员数", () => {
    // `satisfies` 在编译期守一半（漏登记即编译失败），这条在源码层再守一次：新增一种 kind 而
    // 忘了在表里给它九条判据，它的每一条提示都会被谓词的 `if (!rule) return false` 静默吞掉。
    const notice = read("lib/aiTradeNotice.ts");
    const types = read("lib/aiTradeTypes.ts");
    const union = types.match(
      /export type AiTradeNoticeKind =[\s\S]*?;/,
    );
    expect(union).not.toBeNull();
    const kinds = (union![0].match(/"[a-z_]+"/g) ?? []).length;
    expect(kinds).toBe(7);
    const rules = notice.match(/AI_TRADE_NOTICE_RULES = \{[\s\S]*?\} satisfies/);
    expect(rules).not.toBeNull();
    expect((rules![0].match(/validIn:/g) ?? []).length).toBe(kinds);
  });

  it("G-N6：回测车道零沾染（backtest.py / backtest_manager.py 全文 0 次 resume_notice）", () => {
    for (const rel of ["services/quant/app/ai_auto/backtest.py",
                       "services/quant/app/ai_auto/backtest_manager.py"]) {
      const text = fs.readFileSync(path.join(REPO_ROOT, rel), "utf8");
      expect(text).not.toContain("resume_notice");
    }
  });

  it("G-N7：lib/aiTradeTypes.ts 是叶模块，不导入 ./aiTrade 或 ./aiTradeNotice", () => {
    // G-N4 只守了 `aiTrade.ts` 那一端；这一端（叶模块被反向依赖）在 v1 里没人守，而
    // `AiTradeGroupMemberState.messageEpoch` 正是最容易诱发它的那个键（[评审 RP1-1]）。
    // 判据只看**真正的 import / export 语句**（行首锚定）：注释里提到这两个模块名是合法的，
    // 而本条第一次跑就被自己的说明性注释绊倒过一次 —— 一条会被文字绊倒的门禁不是门禁。
    const text = read("lib/aiTradeTypes.ts");
    const imports = /^\s*(?:import|export)\b[^\r\n]*from\s+"\.\/aiTrade(?:Notice)?"/m;
    expect(imports.test(text)).toBe(false);
  });

  it("G-N8：通知条不得把面色当文字用，也不得引用未定义的 soft token", () => {
    // 既有的 `contrastTokens.source.test.ts` 第 ③ 组只扫**带回退**的 `var(--x, …)` 写法，
    // 裸 `var(--long-soft)` 它看不见——那是**静默**无底色（[评审 RP1-2]）。
    const text = read("components/Agent/AiTradeNoticeBar.tsx");
    for (const face of ["long", "short", "accent"]) {
      expect(text).not.toContain(`text-[var(--${face})]`);
    }
    for (const ghost of ["--long-soft", "--short-soft"]) {
      expect(text).not.toContain(ghost);
    }
    // 正向：全仓只有 `--accent-soft` / `--warn-soft` 两个 soft 面，本组件用到的每一个 soft
    // token 都必须在 globals.css 里真的有定义（不然上面那两行就是在防一个不完整的名单）。
    const css = fs.readFileSync(path.join(WEB_ROOT, "app", "globals.css"), "utf8")
      .replace(/\r?\n/g, "\n");
    const defined = new Set(Array.from(css.matchAll(/^\s*(--[a-z0-9-]+):/gm), (m) => m[1]));
    const used = Array.from(text.matchAll(/var\(\s*(--[a-z0-9-]+)\s*\)/g), (m) => m[1]);
    expect(used.length).toBeGreaterThan(5);
    expect(used.filter((token) => !defined.has(token))).toEqual([]);
  });
});
