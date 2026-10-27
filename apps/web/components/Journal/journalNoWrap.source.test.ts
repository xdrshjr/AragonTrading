import { readdirSync, readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

/**
 * 表格契约的源码扫描门禁（ai-trade-audit-journal §2.6.3）。
 *
 * 工作台既有的 9 张表全是 `w-full` 无 `nowrap`，10 列的表在 1440 默认窗口下已折行
 * （`docs/plans/workstation-layout-*` 已记录）。本轮新增的表**一律采用仓库里已经正确的那种
 * 写法**（`Factor` / `Settings` 下 8 张表的既有范式），不是发明：
 *
 * ```tsx
 * <div className="… overflow-x-auto">
 *   <table className="w-full min-w-[880px] …">
 *     <th className="whitespace-nowrap …">   <td className="whitespace-nowrap …">
 * ```
 *
 * ⚠️ 正则一律写 `\r?\n`：本仓 `core.autocrlf=true`，写死 `\n` 在干净检出上恒红（既有教训）。
 */
const DIR = resolve(process.cwd(), "components/Journal");

function sources(): { name: string; text: string }[] {
  return readdirSync(DIR)
    .filter((name) => name.endsWith(".tsx") && !name.includes(".test."))
    .map((name) => ({ name, text: readFileSync(resolve(DIR, name), "utf-8") }));
}

/** 取 `<table` 之前那一段源码里最后一个 `<div ... className="..."` 的类名。 */
function enclosingDivClass(text: string, tableIndex: number): string {
  const before = text.slice(0, tableIndex);
  const divIndex = before.lastIndexOf("<div");
  if (divIndex < 0) return "";
  const chunk = before.slice(divIndex);
  const match = chunk.match(/className="([^"]*)"/);
  return match ? match[1] : "";
}

describe("Journal 表格契约（源码扫描）", () => {
  it("每一个 <table 的最近祖先 div 都带 overflow-x-auto，且表自身带 min-w-[", () => {
    const offenders: string[] = [];
    for (const { name, text } of sources()) {
      let cursor = text.indexOf("<table");
      while (cursor >= 0) {
        const openTagEnd = text.indexOf(">", cursor);
        const tag = text.slice(cursor, openTagEnd);
        if (!/min-w-\[/.test(tag)) offenders.push(`${name}: <table 缺 min-w-[`);
        if (!/overflow-x-auto/.test(enclosingDivClass(text, cursor))) {
          offenders.push(`${name}: <table 的容器缺 overflow-x-auto`);
        }
        cursor = text.indexOf("<table", openTagEnd);
      }
    }
    expect(offenders).toEqual([]);
  });

  it("三张主表都在（决策轮次 / 成交与订单 / 系统事件），且各自恰好一张表", () => {
    const expected = ["JournalDecisionTable.tsx", "JournalFillTable.tsx", "JournalEventTable.tsx"];
    const byName = new Map(sources().map((s) => [s.name, s.text]));
    for (const name of expected) {
      const text = byName.get(name);
      expect(text, `${name} 不存在`).toBeTruthy();
      expect((text ?? "").split("<table").length - 1).toBe(1);
    }
  });

  it("表头与单元格一律 whitespace-nowrap（截断吸收点由容器的横向滚动承担）", () => {
    const offenders: string[] = [];
    for (const { name, text } of sources()) {
      if (!text.includes("<table")) continue;
      // 用 `<th(?![a-z])` 而不是裸 `<th`：后者会把 `<thead …>` 一并匹配进来，于是这道门禁会在
      // 一个本来就不该有 whitespace-nowrap 的标签上恒红——一道会误报的门禁，下一个人只会把它
      // 删掉。`<td` 允许极少数刻意换行的说明列，但必须显式写出 whitespace-*（沉默不算表态）。
      const headers = text.match(/<th(?![a-z])[^>]*>/g) ?? [];
      expect(headers.length, `${name}: 没扫到任何 <th ⇒ 这道门禁在本文件上是空转的`)
        .toBeGreaterThan(0);
      for (const tag of headers) {
        if (!/whitespace-nowrap/.test(tag)) offenders.push(`${name}: <th 缺 whitespace-nowrap`);
      }
      for (const tag of text.match(/<td[^>]*>/g) ?? []) {
        if (!/whitespace-/.test(tag) && !/className=\{/.test(tag)) {
          offenders.push(`${name}: <td 既没有 whitespace-* 也不是动态类名`);
        }
      }
    }
    expect(offenders).toEqual([]);
  });

  it("正则本身写的是 \\r?\\n（本仓 core.autocrlf=true，写死 \\n 在干净检出上恒红）", () => {
    const self = readFileSync(resolve(DIR, "journalNoWrap.source.test.ts"), "utf-8");
    // 本文件里若出现按行切分，必须用 `\r?\n`。这条守的是**门禁自己**不会在别人的检出上空转。
    const lineSplits = self.match(/split\(\/[^/]*\/\)/g) ?? [];
    for (const expr of lineSplits) {
      if (expr.includes("n")) expect(expr).toContain("\\r?\\n");
    }
  });
});
