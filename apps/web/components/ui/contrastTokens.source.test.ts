import { describe, expect, it } from "vitest";
import fs from "node:fs";
import path from "node:path";

// 本轮四条源码级门禁（trust-boundary-and-signal-integrity G9 ②③④⑤）。
//
// 为什么全是源码扫描而不是渲染用例：这四条守的都是「**没有做**某件事」——没有把面色当文字用、
// 没有引用不存在的 token、没有漏接超时、没有让客户端比代理先放弃。渲染用例只能证明现有代码
// 的行为，证明不了「新写的那一处也遵守」；而这四类漏改的共同点恰恰是 `tsc` 与 `vitest` 都不会红。
//
// 职责边界（与 app/globals.source.test.ts 分工，避免造出第二个所有者）：
//   · CSS 取值 + WCAG 配对矩阵重算 → app/globals.source.test.ts（它**已经**是 globals.css 的门禁归属地）
//   · `.tsx` 用法扫描 / 未定义 token / fetch 超时 / 时长比较 → 本文件
//
// core.autocrlf=true：跨行正则一律写 \r?\n，否则干净检出上恒红。

const WEB_ROOT = path.resolve(__dirname, "..", "..");
const GLOBALS_CSS = path.join(WEB_ROOT, "app", "globals.css");

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

// 扫描根**必须同时含 `components/` 与 `app/` 与 `lib/`**。既有的 warnToken 扫描只看 `components/`，
// 而本轮已经证实 `lib/markdown.ts`（Markdown 链接色）与 `lib/aiTradeAcceptance.ts`（验收徽标文字色）
// 各藏着一处面色当文字用——只扫 components 的话这两处会永远看不见。
const SOURCES = [
  ...collect(path.join(WEB_ROOT, "components")),
  ...collect(path.join(WEB_ROOT, "app")),
  ...collect(path.join(WEB_ROOT, "lib")),
];

describe("② 面色不得用作文字色（D2）", () => {
  it("至少扫到了应有规模的源码（防止扫描根写错而空转变绿）", () => {
    expect(SOURCES.length).toBeGreaterThan(200);
  });

  it("不存在 text-[var(--short|long|accent)]", () => {
    const offenders = SOURCES.filter((s) =>
      /text-\[var\(--(short|long|accent)\)\]/.test(s.text),
    ).map((s) => s.file);
    expect(offenders).toEqual([]);
  });

  it("不存在把面色写进内联 color 的写法（backgroundColor / borderColor 不受限）", () => {
    // `\bcolor:` 只匹配小写开头的 `color:`；`backgroundColor:` / `borderColor:` 的 C 是大写，天然不命中。
    //
    // ⚠️ 判据不能收窄成「`color:` 紧跟一个面色字面量」。盈亏着色几乎全是**条件表达式**
    // （`color: v > 0 ? "var(--long)" : "var(--short)"`、`color: ext ? "var(--long)" : N`），
    // 只匹配紧跟形态会把它们全部放过：本轮就有 4 个文件、9 处这样漏网，其中
    // `BacktestMetricsGrid` 同一张表格里「平均每笔」已经是墨色、紧挨着的「平均盈利」还是面色。
    // 所以判据是「同一行里出现了 `color:` 且出现了面色」——面色只能进 backgroundColor /
    // borderColor / 图表 series，进不了任何一个 `color:`。
    const offenders: string[] = [];
    for (const source of SOURCES) {
      source.text.split("\n").forEach((line, i) => {
        if (/\bcolor:/.test(line) && /var\(--(short|long|accent)\)/.test(line)) {
          offenders.push(`${source.file}:${i + 1}`);
        }
      });
    }
    expect(offenders).toEqual([]);
  });

  it("--accent 面上不再出现 text-white（对比度浅 3.28 / 暗 2.69）", () => {
    const offenders = SOURCES.filter((s) =>
      s.text.split("\n").some((line) => /bg-\[var\(--accent\)\]/.test(line) && /text-white/.test(line)),
    ).map((s) => s.file);
    expect(offenders).toEqual([]);
  });

  // 这条是 globals.source.test.ts 配对矩阵里那个**排除项**的看守：暗色 --short-ink on --accent-soft
  // 只有 4.41，所以矩阵里没有这一格；能这么排除，前提是产品里二者不再相遇。把 danger 按钮的 hover 底
  // 改回 --accent-soft，这条立刻红——排除项因此是可判定的，不是约定。
  it("--short-ink 不得与 --accent-soft 底同行出现（矩阵排除项的看守）", () => {
    const offenders = SOURCES.filter((s) =>
      s.text.split("\n").some(
        (line) => /var\(--short-ink\)/.test(line) && /bg-\[var\(--accent-soft\)\]/.test(line),
      ),
    ).map((s) => s.file);
    expect(offenders).toEqual([]);
  });

  it("墨色确实被用起来了（否则上面几条会因为无人使用而空转变绿）", () => {
    const users = SOURCES.filter((s) => /var\(--(short|long|accent)-ink\)/.test(s.text));
    expect(users.length).toBeGreaterThan(100);
  });
});

describe("③ 内联回退不得指向 globals.css 里没有的 token（D2）", () => {
  const css = fs.readFileSync(GLOBALS_CSS, "utf8").replace(/\r?\n/g, "\n");
  const defined = new Set(
    Array.from(css.matchAll(/^\s*(--[a-z0-9-]+):/gm), (m) => m[1]),
  );

  it("globals.css 里解析到了合理数量的 token（防止解析失败而空转变绿）", () => {
    expect(defined.size).toBeGreaterThan(15);
    expect(defined.has("--accent-ink")).toBe(true);
    expect(defined.has("--on-accent")).toBe(true);
  });

  // `var(--x, fallback)` 是这类缺陷的温床：它让「token 不存在」这件事**永远不会被发现**。
  // 本仓已为它栽过三次——`--warn`（活了整整一个项目周期）、`--on-accent`（靠内联 #fff 兜底）、
  // `--success`/`--danger`/`--chart-1..8`（`lib/aiTradeAcceptance.ts` 里 10 处死跳转，
  // 注释还写着「全部取自既有 --chart-* 设计 token」）。
  it("源码里所有 var(--x, …) 的 --x 都在 globals.css 里有定义", () => {
    const offenders: string[] = [];
    for (const source of SOURCES) {
      for (const m of source.text.matchAll(/var\(\s*(--[a-z0-9-]+)\s*,/g)) {
        if (!defined.has(m[1])) offenders.push(`${source.file}: ${m[1]}`);
      }
    }
    expect(offenders).toEqual([]);
  });
});

describe("④ lib/** 下每一个 fetch 都经 withTimeout（D3）", () => {
  const LIB = collect(path.join(WEB_ROOT, "lib"));

  // 口径对账：D3 施工前是 **20 个调用点 / 18 个文件**（`aiTradeHorizonApi.ts` 一个文件独占 3 个，
  // 彼此独立、无共享包装、抛的还是裸 `Error`）。那一轮把它们收敛进该文件私有的 `getJson`，
  // 于是 20 → 18；文件数不变。收敛不是「合并 18 个 *Api.ts 的包装」（那条明确在范围外），
  // 而是让「同一个文件里第 4 次复制粘贴 fetch 却忘了接超时」在结构上不可能发生。
  //
  // ai-trade-experience-context-graph 新增 `experienceApi.ts`：**2 个调用点 / 1 个文件**
  // （通用 `request` 一处 + NDJSON 导出一处——后者要拿 `res.text()` 而不是 `res.json()`，
  // 走不了通用包装）。于是 18 → 20 个调用点、18 → 19 个文件。**这个数字每次都要人来对一遍**，
  // 那正是它存在的理由：一个新 `*Api.ts` 忘了接超时时，按钮会静默冻结最长 5 分钟。
  //
  // ai-trade-market-intel 新增 `intelApi.ts`：**1 个调用点 / 1 个文件**（全部端点共用
  // 同一个通用 `request`）。于是 20 → 21 个调用点、19 → 20 个文件。
  it("扫到了全部 fetch 调用点（21 个调用点 / 20 个文件）", () => {
    const sites = LIB.reduce((n, s) => n + (s.text.match(/\bfetch\(/g)?.length ?? 0), 0);
    const files = LIB.filter((s) => /\bfetch\(/.test(s.text));
    expect(sites).toBe(21);
    expect(files.length).toBe(20);
  });

  // 判据是**调用点**不是文件：`aiTradeHorizonApi.ts` 一个文件里就有 3 个彼此独立的 fetch 且没有共享
  // 包装。按「一个文件加一行」施工会漏掉其中两个，而 tsc 与 vitest 都不会红。
  it("每一个 fetch( 都在同一个调用里带上 withTimeout(", () => {
    const offenders: string[] = [];
    for (const source of LIB) {
      source.text.split("\n").forEach((line, i) => {
        if (/\bfetch\(/.test(line) && !/withTimeout\(/.test(line)) {
          offenders.push(`${source.file}:${i + 1}`);
        }
      });
    }
    expect(offenders).toEqual([]);
  });
});

describe("⑤ 客户端超时必须严格大于代理超时（D3）", () => {
  it("LONG_MS > next.config.mjs 的 experimental.proxyTimeout", () => {
    const nextConfig = fs.readFileSync(path.join(WEB_ROOT, "next.config.mjs"), "utf8");
    const proxyTimeout = Number(nextConfig.match(/proxyTimeout:\s*(\d+)/)?.[1]);
    expect(Number.isFinite(proxyTimeout)).toBe(true);

    const httpTimeout = fs.readFileSync(path.join(WEB_ROOT, "lib", "httpTimeout.ts"), "utf8");
    const mirrored = Number(httpTimeout.match(/PROXY_TIMEOUT_MS\s*=\s*([\d_]+)/)?.[1].replace(/_/g, ""));
    expect(mirrored).toBe(proxyTimeout);

    // **严格大于，不是相等。** 客户端计时从发起请求那一刻起算，代理计时从 Next 服务端收到请求才
    // 起算——取等值时客户端必然先触发，正好把「代理还在等」误报成「超时了」，即 D3 自己要避免的那件事。
    const margin = Number(httpTimeout.match(/LONG_MS\s*=\s*PROXY_TIMEOUT_MS\s*\+\s*([\d_]+)/)?.[1]
      ?.replace(/_/g, ""));
    expect(margin).toBeGreaterThan(0);
    expect(mirrored + margin).toBeGreaterThan(proxyTimeout);
  });
});
