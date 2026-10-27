import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";
import { toCsv } from "./export";

/**
 * CSV 转义的**共享黄金样本**（ai-trade-audit-journal §2.5.1 / 评审 RP1-9）。
 *
 * 前后端各有一份 CSV 转义实现，而**一个进程里没有另一份**（后端跑 pytest、前端跑 vitest），
 * 且本侧的 `escapeField` 是模块私有的（唯一公开出口是 `toCsv`）。所以两侧断言的不是彼此，
 * 而是**同一组提交进仓库的夹具文件**：任一侧漂移都会在自己的 CI 上判红，而夹具本身的改动
 * 必然出现在 diff 里、必须两侧同时改。
 *
 * ⚠️ 两处环境细节，缺一这道门禁就会空转：
 * 1. 夹具必须**按字节读**再解码——Node 的 `readFileSync(path, "utf-8")` 不做换行归一，
 *    但比较用的正则一律写 `\r?\n`（本仓 `core.autocrlf=true`，写死 `\n` 在干净检出上恒红）。
 * 2. `undefinedIndices` 指的那些位置必须**真的替换成 `undefined`**：JSON 里没有 undefined，
 *    而「undefined 与 null 都归空串」正是 `escapeField` 的第一条分支，不测就是漏测。
 */
// `import.meta.url` 在 jsdom 环境下不是 file: URL（vitest 会把它换成一个虚拟地址），
// 故按**进程工作目录**定位——vitest 一律在 `apps/web` 内跑（本仓既有约定）。
const FIXTURES = resolve(process.cwd(), "../../docs/plans/ai-trade-audit-journal/fixtures");

interface Cases {
  column: { key: string; label: string };
  values: unknown[];
  undefinedIndices: number[];
}

function loadCases(): Cases {
  return JSON.parse(readFileSync(resolve(FIXTURES, "csv_escape_cases.json"), "utf-8")) as Cases;
}

function loadGolden(): string {
  // 按字节读再解码：与后端 `read_bytes().decode("utf-8")` 同一口径。
  return readFileSync(resolve(FIXTURES, "csv_escape_golden.csv")).toString("utf-8");
}

// UTF-8 BOM：前置后 Excel 才会按 UTF-8 解析中文表头（U+FEFF）。`toCsv` 不带 BOM，
// 由调用方拼上——夹具带的是**完整文件**，故这里补上。
const BOM = "﻿";

describe("CSV 转义 — 与后端共享的黄金样本", () => {
  it("经 toCsv 跑 20 组刁钻输入，输出与夹具逐字节相同", () => {
    const cases = loadCases();
    const values = cases.values.map((value, index) =>
      cases.undefinedIndices.includes(index) ? undefined : value,
    );
    const rows = values.map((value) => ({ [cases.column.key]: value }));
    const output = BOM + toCsv(rows, [cases.column]);
    expect(output).toBe(loadGolden());
  });

  it("夹具本身覆盖了逗号 / 引号 / CRLF / 前导空格 / BOM 字符 / null / undefined", () => {
    // 一道只喂普通字符串的转义门禁，与没有门禁在工程上是同一件事。
    const cases = loadCases();
    expect(cases.values.length).toBe(20);
    const asText = cases.values.map((v) => (typeof v === "string" ? v : ""));
    expect(asText.some((v) => v.includes(","))).toBe(true);
    expect(asText.some((v) => v.includes('"'))).toBe(true);
    expect(asText.some((v) => /\r\n/.test(v))).toBe(true);
    expect(asText.some((v) => /^\s/.test(v))).toBe(true);
    expect(asText.some((v) => v.includes("﻿"))).toBe(true);
    expect(cases.values.some((v) => v === null)).toBe(true);
    expect(cases.undefinedIndices.length).toBeGreaterThan(0);
  });

  it("行分隔符是 CRLF（正则写 \\r?\\n 只是为了不被检出方式影响判定）", () => {
    const golden = loadGolden();
    expect(golden.includes("\r\n")).toBe(true);
    expect(golden.startsWith(BOM)).toBe(true);
    // 行数 = 表头 1 + 20 组输入，但含 CRLF 的那几个值自身被引号包裹、不算换行分隔。
    expect(golden.split(/\r?\n/).length).toBeGreaterThan(20);
  });
});
