import { describe, expect, it } from "vitest";
import fs from "node:fs";
import path from "node:path";
import { AXIS_LABEL_ZH, AXIS_VALUES, VALUE_LABEL_ZH } from "./experience";

// 门禁 G-7 · 前后端中文词表同步（ai-trade-experience-context-graph §4.2）。
//
// 轴与取值的中文展示名在前后端各有一份：后端 `experience/taxonomy.py` 的 `AXIS_LABEL_ZH` /
// `VALUE_LABEL_ZH` 供 prompt 与图节点 label 用，前端这一份供设置中心与轨迹徽标用。
// 两份漂移的失败形态是**静默**的：用户在设置中心看到「持仓盯market」而 prompt 里写的是
// 「持仓盯市」，或者某个取值在界面上直接回显成英文枚举——跑不出错，也没有任何用例会红。
//
// core.autocrlf=true：跨行正则一律写 `\r?\n`，否则干净检出上恒红（先例 contrastTokens.source.test.ts）。

const TAXONOMY = path.resolve(
  __dirname, "..", "..", "..", "services", "quant", "app", "ai_auto", "experience", "taxonomy.py",
);
// 九组唤醒注册表里**只有 `AXIS_VALUES.wake` 真正静默**（#6/#7 由 tsc 强制、#5+#8 由本文件的
// 前两条 `it` 双向兜住）。G-9 的前端一侧因此**扩这条既有用例**而不是新建第二条源码扫描——
// 同一个 Python 常量被两条用例各解析一次，第二天就会有一条被改坏而没人发现。
const WAKE_REASON = path.resolve(
  __dirname, "..", "..", "..", "services", "quant", "app", "ai_auto", "wake_reason.py",
);

/** 从 Python 常量表里解析 `"key": "值",` 对（只认单行形式，与源文件写法一致）。 */
function parsePythonMap(source: string, name: string): Record<string, string> {
  const block = new RegExp(`${name}: dict\\[str, str\\] = \\{\\r?\\n([\\s\\S]*?)\\r?\\n\\}`)
    .exec(source);
  if (!block) throw new Error(`taxonomy.py 里找不到常量表 ${name}——这条门禁会假绿`);
  const out: Record<string, string> = {};
  const pair = /"([^"]+)":\s*"([^"]+)"/g;
  let match: RegExpExecArray | null;
  while ((match = pair.exec(block[1])) !== null) out[match[1]] = match[2];
  return out;
}

describe("前后端中文词表同步（G-7）", () => {
  const source = fs.readFileSync(TAXONOMY, "utf8");

  it("轴的中文名逐条一致", () => {
    const backend = parsePythonMap(source, "AXIS_LABEL_ZH");
    // 后端的 key 是 `AXIS_*` 常量（`AXIS_SYMBOL: "标的"`），解析出来是常量名而不是轴 id，
    // 故按**值**比对：11 条中文名必须一一对应，多一条少一条都算漂移。
    const backendLabels = Object.values(backend).length
      ? Object.values(backend)
      : Array.from(source.matchAll(/AXIS_\w+:\s*"([^"]+)"/g)).map((m) => m[1]);
    // 空集合与空集合是相等的——先证明我们真的解析到了东西，否则这条门禁会永远绿。
    expect(backendLabels.length).toBe(11);
    expect(new Set(backendLabels)).toEqual(new Set(Object.values(AXIS_LABEL_ZH)));
  });

  it("取值的中文名逐条一致（含那一处被注释点名的笔误防线）", () => {
    const backend = parsePythonMap(source, "VALUE_LABEL_ZH");
    // 后端在表外还有一行 `VALUE_LABEL_ZH["position_watch"] = "持仓盯市"`（对表内笔误的物理防线），
    // 前端必须跟着它而不是跟着表内那一条。
    const patch = /VALUE_LABEL_ZH\["([^"]+)"\]\s*=\s*"([^"]+)"/g;
    let match: RegExpExecArray | null;
    while ((match = patch.exec(source)) !== null) backend[match[1]] = match[2];
    // `UNKNOWN` 那一条在后端用常量作键，解析不到，单独补上。
    backend.unknown = "未知";

    expect(Object.keys(backend).length).toBeGreaterThan(20);   // 同上：先证明解析到了东西
    const missing = Object.entries(backend)
      .filter(([key, label]) => VALUE_LABEL_ZH[key] !== label);
    expect(missing).toEqual([]);
    expect(VALUE_LABEL_ZH.position_watch).toBe("持仓盯市");
  });

  it("AXIS_VALUES.wake 覆盖后端全部 WAKE_KINDS（G-9 前端一侧）", () => {
    // 漏一个值的症状：**经验图预演选择器里根本选不到那一类**，而 tsc / vitest 都不会红
    // ——`AXIS_VALUES` 是 `Record<string, string[]>`，多一个少一个都合法。
    // core.autocrlf=true。跨行正则写成 CR 可选 + LF 的形态，否则干净检出上恒红。
    const source = fs.readFileSync(WAKE_REASON, "utf8");
    const block = /WAKE_KINDS = \(([\s\S]*?)\)/.exec(source);
    if (!block) throw new Error("wake_reason.py 里找不到 WAKE_KINDS——这条门禁会假绿");
    const kinds = Array.from(block[1].matchAll(/"([a-z_]+)"/g)).map((m) => m[1]);
    expect(kinds.length).toBeGreaterThan(5);      // 先证明真的解析到了东西
    const declared = new Set(AXIS_VALUES.wake);
    expect(kinds.filter((kind) => !declared.has(kind))).toEqual([]);
    expect(declared.has("unknown")).toBe(true);
    // 反向：前端多出来的那一条永远匹配不到任何一条经验样本。
    expect(AXIS_VALUES.wake.filter((v) => v !== "unknown" && !kinds.includes(v))).toEqual([]);
  });

  it("前端没有多出后端不认识的取值（多出来的那一条永远匹配不到任何东西）", () => {
    const backend = parsePythonMap(source, "VALUE_LABEL_ZH");
    backend.unknown = "未知";
    const extra = Object.keys(VALUE_LABEL_ZH).filter((key) => !(key in backend));
    expect(extra).toEqual([]);
  });
});
