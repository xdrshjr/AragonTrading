import fs from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";

// 情报文本的素材来自不可信网页：`dangerouslySetInnerHTML` 在这里等于把 XSS 邀请进来。
// React 默认转义已经足够，这条扫描守的是「有人为了渲染一段富文本而临时开个口子」。
//
// 正则写 `\r?\n`：本仓库 `core.autocrlf=true`，写死 `\n` 会在干净检出上恒红。
const DIR = path.join(process.cwd(), "components", "Intel");

function sources(): { file: string; text: string }[] {
  // 排除用例文件本身：它必须写出那个字符串才能扫描它，按原文扫会把自己判成违规。
  return fs
    .readdirSync(DIR)
    .filter((name) => /\.(tsx|ts)$/.test(name) && !/\.test\.tsx?$/.test(name))
    .map((name) => ({ file: name, text: fs.readFileSync(path.join(DIR, name), "utf8") }));
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
      "IntelImpactTab.tsx",
      "IntelList.tsx",
      "IntelModal.tsx",
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
    const chips = sources().find((s) => s.file === "IntelSourceChips.tsx")!.text;
    expect(chips).toMatch(/target="_blank"[\s\S]{0,80}rel="noopener noreferrer"/);
  });
});
