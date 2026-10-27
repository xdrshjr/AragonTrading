import { readdirSync, readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

/**
 * 门禁层名的第二份真源禁令（ai-trade-audit-journal §六 第 6 条 / 评审 RP0-3）。
 *
 * `gate_veto.py` 的开篇纪律 1 逐字写着：「绝不在本模块另写一张层名表……两处各写一份必然漂移，
 * 而漂移的表现（模型与界面读到一个不存在的层名）**不报任何错**。」
 *
 * 于是本轮的层名一律随 `gates[].label` 从服务端下发（真源 `open_funnel.LAYER_LABELS`），
 * 前端**一个层名字面量都不许有**。名单从后端夹具 `fixtures/layer_labels.json` 读，
 * 而那份夹具由后端用例 `test_ai_trade_journal_gates.py` 保证与真源逐项同步——
 * 一道读着过期名单的门禁，与没有门禁在工程上是同一件事。
 */
const JOURNAL_DIR = resolve(process.cwd(), "components/Journal");
const LIB_DIR = resolve(process.cwd(), "lib");
const FIXTURE = resolve(
  process.cwd(),
  "../../docs/plans/ai-trade-audit-journal/fixtures/layer_labels.json",
);

function layerLabels(): string[] {
  const data = JSON.parse(readFileSync(FIXTURE, "utf-8")) as {
    layers: string[];
    labels: Record<string, string>;
  };
  return data.layers.map((key) => data.labels[key]);
}

function scanned(): { path: string; text: string }[] {
  const files: { path: string; text: string }[] = [];
  for (const name of readdirSync(JOURNAL_DIR)) {
    if (!name.endsWith(".tsx") && !name.endsWith(".ts")) continue;
    if (name.includes(".test.")) continue;      // 门禁自身与组件用例不在扫描范围内
    files.push({ path: `components/Journal/${name}`,
                 text: readFileSync(resolve(JOURNAL_DIR, name), "utf-8") });
  }
  for (const name of readdirSync(LIB_DIR)) {
    if (!name.startsWith("journal") || !name.endsWith(".ts")) continue;
    if (name.includes(".test.")) continue;
    files.push({ path: `lib/${name}`, text: readFileSync(resolve(LIB_DIR, name), "utf-8") });
  }
  return files;
}

describe("门禁层名只许来自 wire（源码扫描）", () => {
  it("夹具本身是有效的（13 层，每层一个非空中文名）", () => {
    // 名单为空时这道门禁会「全绿」——那是最坏的一种绿。故先守名单自己。
    const labels = layerLabels();
    expect(labels.length).toBe(13);
    expect(labels.every((label) => typeof label === "string" && label.length > 0)).toBe(true);
  });

  it("components/Journal/** 与 lib/journal*.ts 里不出现任何一个层名中文字面量", () => {
    const labels = layerLabels();
    const offenders: string[] = [];
    for (const { path, text } of scanned()) {
      for (const label of labels) {
        if (text.includes(label)) offenders.push(`${path}: ${label}`);
      }
    }
    expect(offenders,
      "层名一律随 gates[].label 从服务端下发；在前端补一张表就是第二份真源，" +
      "而它漂移时不报任何错").toEqual([]);
  });

  it("扫描范围非空（一道扫不到任何文件的门禁等于没有门禁）", () => {
    const files = scanned();
    expect(files.length).toBeGreaterThan(5);
    expect(files.some((f) => f.path === "components/Journal/JournalGateChain.tsx")).toBe(true);
    expect(files.some((f) => f.path === "lib/journal.ts")).toBe(true);
  });

  it("门禁链组件确实是从 props 里取 label 的（而不是自己拼）", () => {
    const text = readFileSync(resolve(JOURNAL_DIR, "JournalGateChain.tsx"), "utf-8");
    expect(text).toContain("row.label");
  });
});
