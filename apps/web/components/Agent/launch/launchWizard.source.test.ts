import { describe, expect, it } from "vitest";
import fs from "node:fs";
import path from "node:path";
import { AI_TRADE_LAUNCH_STEPS } from "@/lib/aiTradeLaunchSteps";

/**
 * 引导式启动向导的**源码冻结门禁**（ai-trade-guided-launch §8.3 G-1 ~ G-6）。
 *
 * 这里守的四件事都有一个共同点：**渲染用例察觉不到它们的回归**。
 *  · G-1 行数超红线不会让任何断言变红；
 *  · G-2 第二个 reset effect 只在「父树以 ~1Hz 重渲染」时才现形，而静态渲染的用例照样是绿的；
 *  · G-3 轨上写「资金与仓位」、内容区写「资金设置」是分步 UI 最常见的漂移，没有任何断言会红；
 *  · G-6 `tradabilityNeedsConfirm` 一旦写回 `useState`，只有「从不访问第 5 步」那一条路径会红。
 *
 * core.autocrlf=true：读入内容先把 `\r\n` 归一化，否则干净检出上跨行匹配恒红。
 */

const LAUNCH_DIR = __dirname;
const AGENT_DIR = path.join(__dirname, "..");

function read(rel: string): string {
  return fs.readFileSync(path.join(AGENT_DIR, rel), "utf8").replace(/\r\n/g, "\n");
}

function launchFiles(): string[] {
  return fs
    .readdirSync(LAUNCH_DIR)
    .filter((name) => /\.tsx?$/.test(name) && !name.includes(".test."))
    .sort();
}

/** 依赖数组的源码切片：`useEffect(… , [deps]);` 里那对方括号之间的原文。 */
function effectDeps(src: string): string[] {
  const deps: string[] = [];
  for (const m of src.matchAll(/useEffect\(/g)) {
    const from = m.index ?? 0;
    // 向后找该 `useEffect(` 的结尾：最后一个 `[` … `]` 后跟 `)` 的那一段。
    const tail = src.slice(from, from + 20000);
    const closing = tail.match(/\n\s*\},\s*\[([^\]]*)\]\)/);
    deps.push(closing ? closing[1].trim() : "<unparsed>");
  }
  return deps;
}

describe("引导式启动向导（源码冻结）", () => {
  it("G-0 自证：launch/ 目录下确实读到了本轮的全部新文件", () => {
    const files = launchFiles();
    for (const expected of [
      "AiTradeLaunchStepRail.tsx",
      "DisciplinePresetFields.tsx",
      "LaunchPrimitives.tsx",
      "StepCapital.tsx",
      "StepCognition.tsx",
      "StepGuards.tsx",
      "StepReview.tsx",
      "StepRhythm.tsx",
      "StepScope.tsx",
      "useAiTradeLaunchForm.ts",
      "useAiTradeLaunchState.ts",
    ]) {
      expect(files, `launch/${expected} 不见了`).toContain(expected);
    }
  });

  it("G-1 CLAUDE.md 红线：壳与 launch/** 下每个文件 ≤ 800 行", () => {
    const targets = [
      { file: "AiTradeLaunchModal.tsx", src: read("AiTradeLaunchModal.tsx") },
      ...launchFiles().map((name) => ({
        file: `launch/${name}`,
        src: fs.readFileSync(path.join(LAUNCH_DIR, name), "utf8").replace(/\r\n/g, "\n"),
      })),
    ];
    for (const { file, src } of targets) {
      const lines = src.split("\n").length;
      expect(lines, `${file} 有 ${lines} 行，超过 CLAUDE.md 的 800 行红线`).toBeLessThanOrEqual(800);
    }
  });

  it("G-2 (a) `useAiTradeLaunchForm.ts` 里依赖数组恰为 [open] 的 useEffect **恰好 1 个**", () => {
    const deps = effectDeps(read("launch/useAiTradeLaunchForm.ts"));
    const openOnly = deps.filter((d) => d === "open");
    expect(
      openOnly.length,
      "第二个 [open] effect 正是 ai-trade-modal-revert-recurrence 的复发根因",
    ).toBe(1);
  });

  it("G-2 (b) 该文件里 useEffect( 出现次数 ≤ 3，且另外两个逐个具名", () => {
    const src = read("launch/useAiTradeLaunchForm.ts");
    const count = (src.match(/useEffect\(/g) ?? []).length;
    expect(
      count,
      "本文件只允许三个 effect：[open] 的 seed/reset、可交易性 level 跃迁重置（依赖 " +
        "[tradabilityView?.level]）、文件私有防抖 useDebounced（依赖 [value, ms]）。" +
        "多出来的那一个必须先说清它为什么不会把正在编辑的表单冲回默认。",
    ).toBeLessThanOrEqual(3);
    const deps = effectDeps(src);
    expect(deps).toContain("value, ms");
    expect(deps.some((d) => d.includes("tradabilityView?.level"))).toBe(true);
  });

  it("G-2 (c) 除 [open] 那一个外，任何 useEffect 的依赖数组里都不得出现 open", () => {
    const deps = effectDeps(read("launch/useAiTradeLaunchForm.ts"));
    const offenders = deps.filter((d) => d !== "open" && /\bopen\b/.test(d));
    expect(
      offenders,
      "依赖里带 open 的第二个 effect 会在弹窗打开的每一帧重跑，把用户正在编辑的字段冲回默认",
    ).toEqual([]);
  });

  it("G-3 步骤标题的唯一真源是 AI_TRADE_LAUNCH_STEPS（Step*.tsx 与轨里不出现字面量标题）", () => {
    const titles = AI_TRADE_LAUNCH_STEPS.map((s) => s.title);
    const scanned = launchFiles().filter((n) => /^(Step|AiTradeLaunchStepRail)/.test(n));
    expect(scanned.length).toBeGreaterThan(0);
    for (const name of scanned) {
      const src = fs.readFileSync(path.join(LAUNCH_DIR, name), "utf8");
      for (const title of titles) {
        expect(
          src.includes(`"${title}"`) || src.includes(`>${title}<`),
          `launch/${name} 里出现了字面量步骤标题「${title}」——轨与内容区必须读同一份表`,
        ).toBe(false);
      }
    }
  });

  it("G-5 新增文件不出现面色当文字色，也不出现带内联回退的 var(--warn, …)", () => {
    for (const name of launchFiles()) {
      const src = fs.readFileSync(path.join(LAUNCH_DIR, name), "utf8");
      expect(src, `launch/${name} 把面色当文字色用了`).not.toMatch(
        /text-\[var\(--(short|long|accent)\)\]/,
      );
      expect(src, `launch/${name} 的 var(--warn) 带了内联回退`).not.toMatch(/var\(--warn\s*,/);
    }
  });

  it("G-6 `tradabilityNeedsConfirm` 必须是派生值，且子组件不再有 onNeedsConfirmChange", () => {
    const hook = read("launch/useAiTradeLaunchForm.ts");
    const lines = hook.split("\n").filter((l) => l.includes("tradabilityNeedsConfirm"));
    expect(lines.length, "hook 里找不到 tradabilityNeedsConfirm，判据的前提失效").toBeGreaterThan(0);
    for (const line of lines) {
      expect(
        /useState|useReducer/.test(line),
        "它一旦是 state，唯一的写点就只能在某个组件的 effect 里；而那个组件在六步里有五步是卸载的" +
          "——`tradabilityBlocked` 会恒 false，一次真实资金启动就绕过了本轮唯一一次 fail-open 确认",
      ).toBe(false);
    }
    expect(read("AiTradeTradabilityFields.tsx")).not.toContain("onNeedsConfirmChange");
  });

  it("G-2 (d) 受控状态层里一个 useEffect 都没有（复位只能经由那一个 [open] effect）", () => {
    // 状态声明与复位体被切进 `useAiTradeLaunchState.ts` 只为守住 800 行红线；一旦有人在那里
    // 补一个 effect 去「顺手」复位某个新字段，`ai-trade-modal-revert-recurrence` 就原地复活，
    // 而 G-2 (a)(b)(c) 只扫 form 文件、看不见它。
    const state = read("launch/useAiTradeLaunchState.ts");
    expect(state).toContain("const seed = () => {");
    expect((state.match(/useEffect\(/g) ?? []).length).toBe(0);
    // `seed()` 只允许有一个调用点，就是 form 文件里那个 [open] effect。
    const form = read("launch/useAiTradeLaunchForm.ts");
    expect((form.match(/state\.seed\(\)/g) ?? []).length).toBe(1);
  });

  it("G-6b 步骤轨是 launch/ 下唯一带零折行锚点的文件（等值冻结的数才可对账）", () => {
    const anchor = ["data", "nowrap="].join("-");
    for (const name of launchFiles()) {
      const src = fs.readFileSync(path.join(LAUNCH_DIR, name), "utf8");
      const count = src.split(anchor).length - 1;
      const expected = name === "AiTradeLaunchStepRail.tsx" ? 1 : 0;
      expect(count, `launch/${name} 的锚点数应为 ${expected}，实际 ${count}`).toBe(expected);
    }
  });
});
