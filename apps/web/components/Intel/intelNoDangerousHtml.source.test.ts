import fs from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";

// 情报文本的素材来自不可信网页：`dangerouslySetInnerHTML` 在这里等于把 XSS 邀请进来。
// React 默认转义已经足够，这条扫描守的是「有人为了渲染一段富文本而临时开个口子」。
//
// 正则写 `\r?\n`：本仓库 `core.autocrlf=true`，写死 `\n` 会在干净检出上恒红。
const DIR = path.join(process.cwd(), "components", "Intel");
// 情报的设置卡不在 `components/Intel` 里，但它渲染的是同一批面向用户的中文文案，
// 下面那条「字面星号」扫描必须一并覆盖，否则门禁对半个特性等于不存在。
const SETTINGS = path.join(process.cwd(), "components", "Settings");
const SETTINGS_FILES = ["IntelSourceSettings.tsx", "IntelReaderSettings.tsx"];

function sources(): { file: string; text: string }[] {
  // 排除用例文件本身：它必须写出那个字符串才能扫描它，按原文扫会把自己判成违规。
  return fs
    .readdirSync(DIR)
    .filter((name) => /\.(tsx|ts)$/.test(name) && !/\.test\.tsx?$/.test(name))
    .map((name) => ({ file: name, text: fs.readFileSync(path.join(DIR, name), "utf8") }));
}

/** 去掉块注释与行注释——`**` 在 JSDoc 与说明性注释里是完全正常的。 */
function codeOnly(text: string): string {
  return text.replace(/\/\*[\s\S]*?\*\//g, "").replace(/(^|[^:])\/\/.*/g, "$1");
}

describe("components/Intel/**", () => {
  it("扫到了全部情报组件（漏扫等于没扫）", () => {
    const files = sources().map((s) => s.file).sort();
    expect(files).toEqual([
      "IntelAttributionRail.tsx",
      "IntelBlockSourceDialog.tsx",
      "IntelCard.tsx",
      "IntelEditor.tsx",
      "IntelEmptyStates.tsx",
      "IntelEvidenceDrawer.tsx",
      "IntelEvidenceTrigger.tsx",
      "IntelImpactTab.tsx",
      "IntelList.tsx",
      "IntelModal.tsx",
      "IntelQuoteBlock.tsx",
      "IntelRoundsTab.tsx",
      "IntelSourceChips.tsx",
      "IntelSourcesTab.tsx",
      "IntelVerdictBadge.tsx",
    ]);
  });

  it("dangerouslySetInnerHTML 零命中", () => {
    const offenders = sources()
      .filter((s) => /dangerouslySetInnerHTML/.test(s.text))
      .map((s) => s.file);
    expect(offenders).toEqual([]);
  });

  it("外链恒带 rel=\"noopener noreferrer\"", () => {
    // evidence-grounding 评审 P2-10：本轮的外链在 `IntelQuoteBlock.tsx`，扫描范围必须跟上——
    // 只钉着老文件的门禁，对新写的那个口子等于不存在。
    for (const file of ["IntelSourceChips.tsx", "IntelQuoteBlock.tsx"]) {
      const text = sources().find((s) => s.file === file)!.text;
      expect(text, file).toMatch(/target="_blank"[\s\S]{0,80}rel="noopener noreferrer"/);
    }
  });

  it("面向用户的文案里没有字面 `**`（markdown 加粗在 JSX 里就是两颗星号）", () => {
    // 本仓库第 3 次复发（holding-horizon、tradability-and-open-funnel，本轮的
    // `IntelReaderSettings.tsx`）。写在评审记录里三次都没拦住，故立成一条会红的断言。
    // 注释里的 `**` 正常，所以先剥注释再扫；`**` 在 JS 里只有幂运算一种合法用法，
    // 而本目录一处都没有——真要用请写成 `a ** b` 并在这里放行。
    const files = [
      ...sources(),
      ...SETTINGS_FILES.map((name) => ({
        file: name,
        text: fs.readFileSync(path.join(SETTINGS, name), "utf8"),
      })),
    ];
    const offenders = files.filter((s) => /\*\*/.test(codeOnly(s.text))).map((s) => s.file);
    expect(offenders).toEqual([]);
  });
});
