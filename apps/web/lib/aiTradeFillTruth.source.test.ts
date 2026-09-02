import { readFileSync, readdirSync, statSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

/**
 * 门禁 `G-X2`（fill-truth-and-effective-controls 原则 6 的机械化）：
 * **本轮新增的每一个读数，在 `apps/web` 的非测试文件里至少有一个读点。**
 *
 * 理由不是洁癖。本仓库为「算出来了但没人看得见」这个形状反复付过代价——`quantizeErasedRounds`
 * 从 `vol-sizing` 那一轮起就在后端逐轮累加，而它在 `apps/web` 全仓 grep **零命中**，于是生产上
 * 「风控层每一次缩减都被档位地板吃回去了、命中率 100%」这件事，界面上一个字都没有。同一形状
 * 在 `wakeScheduler` 上至今仍是一笔欠账。
 *
 * ⚠️ 正则一律写 `\r?\n`：仓库 `core.autocrlf=true`，写死 `\n` 会在干净检出上恒红（成文教训）。
 */

const ROOT = join(__dirname, "..");
const SKIP_DIRS = new Set([
  "node_modules", ".next", ".turbo", "coverage", "dist", "build", ".git",
]);

function collectSources(dir: string, out: string[] = []): string[] {
  for (const entry of readdirSync(dir)) {
    if (SKIP_DIRS.has(entry)) continue;
    const full = join(dir, entry);
    if (statSync(full).isDirectory()) {
      collectSources(full, out);
      continue;
    }
    if (!/\.(ts|tsx)$/.test(entry)) continue;
    // 只扫**非测试**文件：一个只在用例里被引用的键，对用户而言仍然是不存在的。
    if (/\.(test|spec)\.(ts|tsx)$/.test(entry)) continue;
    out.push(full);
  }
  return out;
}

const SOURCES = collectSources(ROOT).map((file) => ({
  file: file.slice(ROOT.length + 1).replace(/\\/g, "/"),
  text: readFileSync(file, "utf8"),
}));

/**
 * 只声明类型不算读点——类型文件里出现一次不能证明界面上会显示它。
 *
 * `lib/aiTradeCost.ts` **刻意不在这张表里**：它既声明 `ReconcileWire` 也真的消费那两个键
 * （`describeReconcile` 读 `tolUsd`、`describeSettlementCoverage` 读 `settlementCoverage`），
 * 是一个货真价实的读点。把它一并算成「类型文件」会让本门禁对它们恒红，而那是门禁自己的判据
 * 错了，不是代码错了。
 */
const TYPE_ONLY = new Set([
  "lib/aiTradeTypes.ts",
  "lib/aiTradeHistoryTypes.ts",
  "lib/types.ts",
]);

/**
 * 本轮新增的读数 → 它至少要在哪一类文件里被真的用到。
 *
 * 有些键经一个 `lib/` 里的翻译函数（`describeSettlementCoverage` 之类）才到达组件——对它们，
 * 「组件里出现」要判的是**那个函数或它的渲染标记**，而不是键名本身。逐字去 grep 键名会把一条
 * 真的渲染路径判成缺失，那是门禁自己的判据错了。
 */
const KEYS: string[] = [
  // 轴 A / F
  "settlementSource",
  "settlementPosId",
  "settlementPriceGapBps",
  "settlementWaitedS",
  "settlementRejectReason",
  "settlementCoverage",
  "bookedOpenFeeUsd",
  "exchangeFeeUsd",
  "tolUsd",
  // 轴 B
  "orphan",
  "settling",
  // 轴 C / E
  "quantizeErasedRounds",
  "solvedZeroVetoes",
  "belowFloorVetoes",
  "restingKept",
  "restingRepriced",
  "restingExpired",
  // 轴 D-2 / 轴 D
  "contrarianBlocked",
  // 合入前它在 `apps/web` 全仓零命中——而它恰恰是「×0.5」那句话在两条新通路上变成假话的唯一
  // 判据。本门禁当初漏掉它，是门禁自己的覆盖面不够，不是这个键不该被守。
  "downgradeLane",
  // 轴 G-1
  "sampleScope",
  "sampleRounds",
];

describe("G-X2：本轮每个新读数都有一个真的会渲染它的落点", () => {
  it.each(KEYS)("%s is read somewhere outside the type declarations", (key) => {
    const pattern = new RegExp(`\\b${key}\\b`);
    const hosts = SOURCES.filter((s) => pattern.test(s.text)).map((s) => s.file);
    const rendering = hosts.filter((f) => !TYPE_ONLY.has(f));
    expect(
      rendering,
      `${key} 只出现在类型声明里（${hosts.join(", ") || "无"}）——它是一个「有写点没读点」的数字，`
        + "本轮明令禁止（原则 6）。要么给它一个渲染落点，要么本轮就不该新增它。",
    ).not.toEqual([]);
  });

  it("每个新读数至少有一处落在组件而不是纯 lib 里", () => {
    // `lib/` 里的消费（`describeReconcile` 之类）只是把数字翻译成一句话；把它挂到屏幕上的
    // 永远是某个组件。两者缺一，用户仍然看不见。
    // 左边是读数，右边是「它在组件里长什么样」——经翻译函数到达组件的那几个，判的是函数名 /
    // 渲染标记，因为组件里本来就不该再出现一次原始键名（那等于绕过翻译层各写一份口径）。
    const componentMarkers: [string, string][] = [
      ["settlementSource", "trade-settlement-source"],
      ["settlementCoverage", "describeSettlementCoverage"],
      ["tolUsd", "describeReconcile"],
      ["orphan", "AiTradeOrphanBanner"],
      ["settling", "ai-trade-settling-chip"],
      ["quantizeErasedRounds", "quantizeErasedRounds"],
      ["contrarianBlocked", "contrarianBlocked"],
      ["sampleScope", "sampleScope"],
      ["downgradeLane", "describeDowngradeLane"],
    ];
    for (const [key, marker] of componentMarkers) {
      const pattern = new RegExp(`\\b${marker}\\b`);
      const inComponents = SOURCES.filter(
        (s) => s.file.startsWith("components/") && pattern.test(s.text),
      );
      expect(
        inComponents.length,
        `${key} 在 components/ 下没有任何落点（找的是 ${marker}）`,
      ).toBeGreaterThan(0);
    }
  });

  it("孤儿降级码在标签表里有条目（否则漏斗上显示的是一串机器码）", () => {
    const labels = SOURCES.find((s) => s.file === "lib/aiTrade.ts");
    expect(labels).toBeTruthy();
    expect(labels!.text).toContain("orphan_position_blocked");
  });

  it("漏斗层序前后端同构：orphanPosition 在 baseRuleMaBand 与 horizon 之间", () => {
    const funnel = SOURCES.find((s) => s.file === "lib/aiTradeFunnel.ts");
    expect(funnel).toBeTruthy();
    expect(funnel!.text).toContain("orphanPosition");
  });

  it("5% 档位真的进了选项表（轴 C-2 的警示要有一条能点进去的出路）", () => {
    const options = SOURCES.find((s) => s.file === "lib/aiTrade.ts");
    expect(options!.text).toMatch(/value:\s*0\.05/);
  });

  it("档位抹除那一行必须被 scaledRounds > 0 守住（G-G3）", () => {
    // 这是一条**源码级**门禁而不是渲染用例：`SizingCard` 不导出，为守一个渲染门控把它导出去
    // 反而是更大的代价。判的是「那段 JSX 有没有被守卫包住」，够用——`0/0` 这种分母本身就是
    // 本轮在治的那类假数字（「还没缩过」与「缩了但全被吃掉」绝不能显示成同一个东西）。
    const card = SOURCES.find(
      (s) => s.file === "components/Agent/aiTradeStats/AiTradeStatsDetail.tsx",
    );
    expect(card).toBeTruthy();
    // ⚠️ `\r?\n`：仓库 core.autocrlf=true，写死 `\n` 会在干净检出上恒红（成文教训）。
    expect(card!.text).toMatch(
      /\{scaled > 0 && \(\r?\n[\s\S]{0,600}?data-testid="ai-sizing-quantize-erased"/,
    );
  });

  it("前端不硬编码「近 50 轮」——口径必须取自后端下发的 sampleScope（G-G1）", () => {
    // 环形容量是后端 `_DECISIONS_MAX` 一个常量；前端写死一个 50 等于造出第二个会漂移的真源，
    // 它一改这些标签就会集体开始说谎，而且不会有任何一处报错。
    const offenders = SOURCES.filter(
      (s) => s.file.startsWith("components/Agent/aiTradeStats/")
        && /（近 50 轮）/.test(s.text)
        && !/sampleScope/.test(s.text),
    ).map((s) => s.file);
    expect(offenders).toEqual([]);
  });
});
