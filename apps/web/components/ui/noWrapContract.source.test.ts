import { describe, expect, it } from "vitest";
import fs from "node:fs";
import path from "node:path";

/**
 * 交易工作台「文字零折行」排版契约的**源码冻结门禁**
 * （workstation-text-nowrap-contract §9.2；本文件头是这条契约的唯一权威副本，spec 只是它的展开说明）。
 *
 * ── 为什么需要一道源码门禁 ─────────────────────────────────────────────
 * 这套修法**全部由类名承载**，而类名是最容易在重构里被顺手删掉的东西；jsdom 没有布局引擎，
 * 普通渲染用例察觉不到像素回归。所以「行契约还在不在」只能在源码文本上冻结。
 *
 * ── 五类行（实现者必须先判断行的类型，再套写法，不得自由发挥）────────────
 *  A 单行栏      内容必须留在一行的头部 / 状态条 / 控制条。
 *                行容器：`flex items-center justify-between gap-2 whitespace-nowrap` + `data-nowrap="row"`；
 *                左簇 `min-w-0`；右簇 `shrink-0`；**行内恰好一处** `min-w-0 truncate` + `title`。
 *                退化：指定文本出省略号。
 *  B 可回流控件栏 工具栏 / chip 组：条目彼此独立、整块换行不难看。
 *                行容器：`flex flex-wrap … whitespace-nowrap` + `data-nowrap="row"`；每个条目 `shrink-0`。
 *                退化：整个条目掉到下一行，**条目内部永不折**。
 *  C 数据表      外层 `overflow-x-auto` 宿主 + `<table className="w-full min-w-[Npx]">` +
 *                `<thead … whitespace-nowrap>` + 数据 `<tr … whitespace-nowrap>`；
 *                展开详情 `<td colSpan>` 显式 `whitespace-normal`。退化：容器横向滚动。
 *  D 散文        空态 / 错误 / 免责 / 展开详情：显式 `whitespace-normal break-words`。
 *                退化：正常换行 —— **这是期望行为**。
 *  S 继承子树根   一整块面板 / 网格（本身不是一行）：`whitespace-nowrap` + `data-nowrap="scope"`；
 *                块内**每个**可压缩文本各自 `min-w-0 truncate` + `title`。
 *
 * ── 四条铁律 ──────────────────────────────────────────────────────────
 *  1. 左簇必须 `min-w-0`（flex item 默认 `min-width:auto`，不清零则 `truncate` 永不触发）。
 *  2. 右簇必须 `shrink-0`（压力永远流向左侧那一处指定文本，而不是随机压某个按钮）。
 *  3. 每行只能有一处 `truncate`（两处截断 = 两处都读不出来）。这条只约束 Type A 的一行之内；
 *     Type S 覆盖的是一棵子树，其中不同行各有自己的吸收点，互不冲突。
 *  4. 锚点有且只有两个取值：`row`（Type A/B 的行）与 `scope`（Type S 的继承子树根）。
 *  5. **本身还会回流的条目，`shrink-0` 必须与 `max-w-full` 同行出现。** `shrink-0` 让该条目按
 *     max-content 定死，它自己的 `flex-wrap` 就永远轮不到触发 —— 于是「窄了整块掉下一行」退化成
 *     「窄了整块横向溢出」，再被祖先的 `overflow-hidden` 裁掉（终审实测：`ChartHeaderBar` 的 OHLC
 *     组按 max-content 固定在 626px，中栏 240px 时溢出 402px）。G7 守这一条。
 *
 * ── 职责边界（避免造出第二个所有者）──────────────────────────────────
 *  · CSS 取值 / WCAG 配对          → `app/globals.source.test.ts`
 *  · 面色当文字用 / 未定义 token    → `components/ui/contrastTokens.source.test.ts`
 *  · AI 助手面板两横栏              → `components/Agent/agentToolbarNoWrap.source.test.ts`
 *  · **交易工作台排版契约          → 本文件**
 *
 * core.autocrlf=true：所有读入内容先把 `\r\n` 归一化，否则干净检出上跨行匹配恒红。
 */

const COMPONENTS_DIR = path.join(__dirname, "..");
const APP_DIR = path.join(__dirname, "..", "..", "app");

function read(relToComponents: string): string {
  return fs.readFileSync(path.join(COMPONENTS_DIR, relToComponents), "utf8").replace(/\r\n/g, "\n");
}

/**
 * 受管文件的**逐一计数表**（G3）。`===` 而不是 `≥`——`≥` 挡不住「加一个新的、删一个旧的」。
 * 合计 20（16 个 `row` + 4 个 `scope`）。走 Type C 的 9 张表与共用 kit 不用锚点，故为 0。
 *
 * ai-trade-guided-launch §4.3（【v2 · P0-1】）新增第 26 项：AI 自动交易启动向导的**步骤轨**。
 * 本表与 `TOTAL_ANCHORS` 是**等值冻结**而不是「按目录自动覆盖」——G2 虽然扫全仓，判据却是
 * `expect(seen).toBe(TOTAL_ANCHORS)`。故新文件加一处锚点就**必须**在这里同步登记，否则 G2 与
 * G3 双红；反过来，为了让门禁保持绿而不加锚点，新文件上的 Type A 契约就没有任何冻结——
 * **一条永远不会红的门禁比没有更坏**。步骤轨是本轮**唯一**允许出现 `data-nowrap` 的新文件
 * （六个 `Step*.tsx` 一律不带锚点：它们是内容区，文本本就该按 Type D 正常换行），
 * 这样 `20` 这个数才是可对账的。
 */
const MANAGED: { file: string; anchors: number; marker: string }[] = [
  { file: "ui/index.tsx", anchors: 0, marker: "role=\"tablist\"" },
  { file: "Chart/InstrumentSidebar.tsx", anchors: 2, marker: "FilterChips" },
  { file: "Chart/InstrumentRow.tsx", anchors: 1, marker: "onToggleStar" },
  { file: "Chart/WatchOtherPanel.tsx", anchors: 1, marker: "其他自选" },
  { file: "Chart/AutoTradePanel.tsx", anchors: 3, marker: "LIVE_AUTO_MAX_GROUP_SYMBOLS" },
  { file: "Chart/AutoTradeMemberRow.tsx", anchors: 1, marker: "AutoTradeAllocationInput" },
  { file: "Chart/AiTradePanel.tsx", anchors: 1, marker: "AI 自动交易" },
  { file: "Chart/ChartToolbar.tsx", anchors: 1, marker: "IndicatorMenu" },
  { file: "Chart/ChartHeaderBar.tsx", anchors: 1, marker: "computeOhlcStats" },
  { file: "Chart/ChartWorkspace.tsx", anchors: 1, marker: "instrumentSidebarWidth" },
  { file: "Chart/MarketDepthPanel.tsx", anchors: 1, marker: "BookSide" },
  { file: "Strategy/TradesTable.tsx", anchors: 0, marker: "COLUMN_COUNT" },
  { file: "Strategy/PositionPanel.tsx", anchors: 1, marker: "deriveOpenPosition" },
  { file: "Strategy/TradeExplainDetail.tsx", anchors: 1, marker: "MetricsGrid" },
  { file: "Strategy/PaperPositionsTable.tsx", anchors: 0, marker: "PaperPositionRow" },
  { file: "Live/LiveBottomPanel.tsx", anchors: 1, marker: "RIGHT_TABS" },
  { file: "Live/LiveFillsTable.tsx", anchors: 0, marker: "FillsTableView" },
  { file: "Live/LivePositionsTable.tsx", anchors: 0, marker: "PositionOrdersRow" },
  { file: "Live/LiveOpenOrdersTable.tsx", anchors: 0, marker: "OpenOrdersTableView" },
  { file: "Live/LiveTradePanel.tsx", anchors: 2, marker: "SegToggle" },
  { file: "Strategy/RunHistory.tsx", anchors: 0, marker: "MODE_FILTERS" },
  { file: "Strategy/WalkForwardResults.tsx", anchors: 0, marker: "bestWorstWindowIndex" },
  { file: "Strategy/CompareView.tsx", anchors: 0, marker: "sortCompareItems" },
  { file: "Strategy/ComboResult.tsx", anchors: 0, marker: "groupBreakdownByRegime" },
  { file: "Strategy/MetricsCards.tsx", anchors: 1, marker: "profitFactor" },
  { file: "Agent/launch/AiTradeLaunchStepRail.tsx", anchors: 1, marker: "railKeyToIndex" },
];

const TOTAL_ANCHORS = 20;

/** 九张受管数据表：`min-w-[N]` 是「不横滚就一定不折行」的阈值常量，逐表在 §4.5.2 / §4.5.3 给定。 */
const MANAGED_TABLES = [
  "Strategy/TradesTable.tsx",
  "Strategy/PaperPositionsTable.tsx",
  "Live/LiveFillsTable.tsx",
  "Live/LivePositionsTable.tsx",
  "Live/LiveOpenOrdersTable.tsx",
  "Strategy/RunHistory.tsx",
  "Strategy/WalkForwardResults.tsx",
  "Strategy/CompareView.tsx",
  "Strategy/ComboResult.tsx",
];

/**
 * 四处 `<td colSpan={…}>`（评审 P0-3 已把全部落点搜完）。第 2 处是「当前无持仓」空态格而非展开行，
 * 仍纳入——**例外比规则贵**，让这条保持成一条无例外的全量规则。
 */
const COLSPAN_CELLS = [
  "Strategy/TradesTable.tsx",
  "Strategy/PaperPositionsTable.tsx",
  "Live/LiveFillsTable.tsx",
  "Live/LivePositionsTable.tsx",
];

/**
 * 取包含下标 `at` 的那个 JSX 开标签体。
 *
 * **不得**用 `<[^>]*>`：JSX 开标签里常有箭头函数（`onChange={(next) => …}`），`[^>]*` 会在 `=>`
 * 的 `>` 处提前截断，带事件处理器的受管行会**假红**（评审 P1-3）。确定性做法是切片——向前取最近的
 * `<`、向后取下一个 `<`，两者之间即完整开标签体（JSX 开标签内部不会再出现 `<`）。
 */
function openTagAt(src: string, at: number): string {
  const start = src.lastIndexOf("<", at);
  const nextLt = src.indexOf("<", start + 1);
  const end = nextLt === -1 ? src.length : nextLt;
  return src.slice(start, end);
}

/** 文件中每一处 `needle` 所在的开标签体。 */
function openTagsContaining(src: string, needle: string): string[] {
  const tags: string[] = [];
  for (let i = src.indexOf(needle); i !== -1; i = src.indexOf(needle, i + needle.length)) {
    tags.push(openTagAt(src, i));
  }
  return tags;
}

/** 递归收集 `dir` 下的全部 .tsx/.ts 源文件（跳过测试与 node_modules）。 */
function collectSources(dir: string, out: string[] = []): string[] {
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) {
      if (entry.name === "node_modules" || entry.name.startsWith(".")) continue;
      collectSources(full, out);
      continue;
    }
    if (!/\.tsx?$/.test(entry.name)) continue;
    if (entry.name.includes(".test.")) continue;
    out.push(full);
  }
  return out;
}

describe("交易工作台排版契约（源码冻结）", () => {
  it("G0 自证：26 个受管文件全部读到、非空、且各含一条标志性内容", () => {
    expect(MANAGED).toHaveLength(26);
    for (const { file, marker } of MANAGED) {
      const src = read(file);
      expect(src.length, `${file} 读到的内容过短，路径可能写错`).toBeGreaterThan(500);
      expect(src, `${file} 缺少标志性内容 ${marker}`).toContain(marker);
    }
  });

  it("G1 共用 kit：Tabs 两个变体的按钮类名各含 whitespace-nowrap", () => {
    const kit = read("ui/index.tsx");
    expect(kit).toMatch(/-mb-px shrink-0 whitespace-nowrap/);
    expect(kit).toMatch(/flex-1 whitespace-nowrap/);
  });

  it("G2 配对不变式：凡带 data-nowrap 的开标签必须同时含 whitespace-nowrap，且取值只有 row / scope", () => {
    const files = [...collectSources(COMPONENTS_DIR), ...collectSources(APP_DIR)];
    let seen = 0;
    for (const full of files) {
      const src = fs.readFileSync(full, "utf8").replace(/\r\n/g, "\n");
      if (!src.includes("data-nowrap=")) continue;
      const rel = path.relative(COMPONENTS_DIR, full).replace(/\\/g, "/");
      for (const tag of openTagsContaining(src, "data-nowrap=")) {
        seen += 1;
        const value = tag.match(/data-nowrap="([^"]*)"/)?.[1];
        expect(["row", "scope"], `${rel} 的锚点取值 ${value} 不合法`).toContain(value);
        expect(tag, `${rel} 的 data-nowrap 开标签缺 whitespace-nowrap`).toContain(
          "whitespace-nowrap",
        );
      }
    }
    expect(seen).toBe(TOTAL_ANCHORS);
  });

  it("G3 受管文件逐一计数：锚点数必须与计数表逐文件相等，合计 20", () => {
    let total = 0;
    for (const { file, anchors } of MANAGED) {
      const src = read(file);
      const count = src.split("data-nowrap=").length - 1;
      expect(count, `${file} 的 data-nowrap 处数应为 ${anchors}，实际 ${count}`).toBe(anchors);
      total += count;
    }
    expect(total).toBe(TOTAL_ANCHORS);
  });

  it("G4 Type C 冻结：九张表的 table / thead 与四处 colSpan 单元格", () => {
    for (const file of MANAGED_TABLES) {
      const src = read(file);
      const tables = openTagsContaining(src, "<table");
      expect(tables.length, `${file} 里找不到 <table 开标签`).toBeGreaterThan(0);
      for (const tag of tables) {
        expect(tag, `${file} 的 <table> 缺 w-full`).toContain("w-full");
        expect(tag, `${file} 的 <table> 缺列宽地板 min-w-[…]`).toMatch(/min-w-\[\d+px\]/);
      }
      const heads = openTagsContaining(src, "<thead");
      expect(heads.length, `${file} 里找不到 <thead 开标签`).toBeGreaterThan(0);
      for (const tag of heads) {
        expect(tag, `${file} 的 <thead> 缺 whitespace-nowrap`).toContain("whitespace-nowrap");
      }
    }

    // 只匹配 `<td` 开标签：注释与常量名里也会出现 colSpan 字样，按行匹配会误命中（评审 P2-3）。
    for (const file of COLSPAN_CELLS) {
      const src = read(file);
      const cells = openTagsContaining(src, "<td").filter((tag) => tag.includes("colSpan={"));
      expect(cells.length, `${file} 里找不到带 colSpan 的 <td 开标签`).toBeGreaterThan(0);
      for (const tag of cells) {
        expect(tag, `${file} 的 <td colSpan> 缺 whitespace-normal`).toContain("whitespace-normal");
      }
    }
  });

  it("G5 受管文件里不得用视口断点决定面板内列数", () => {
    // 这条**只管受管文件，不是全仓禁令**：弹窗（AiBacktestModal / StrategyDetailModal /
    // ComboDetailSections / AutoTradePnlSummary / aiBacktest/panels）与设置中心的宽度确实跟视口走，
    // 在那里用 sm: / lg: 是正确的。
    for (const { file } of MANAGED) {
      const src = read(file);
      expect(src, `${file} 仍在用视口断点决定面板内列数`).not.toMatch(
        /\b(sm|md|lg|xl):grid-cols-/,
      );
    }
  });

  it("G7 受管文件里 flex-wrap 与 shrink-0 同现的条目必须带 max-w-full", () => {
    // 只查条目，不查行本身：行容器（带 `data-nowrap`）的 `shrink-0` 约束的是它在**父容器**里的
    // 收缩，与它内部能不能回流无关；条目的 `shrink-0` 才会把自己钉死在 max-content 上。
    for (const { file } of MANAGED) {
      const src = read(file);
      for (const tag of openTagsContaining(src, "flex-wrap")) {
        if (!tag.includes("shrink-0") || tag.includes("data-nowrap=")) continue;
        expect(tag, `${file} 的可回流条目缺 max-w-full，窄档会整块横向溢出而不是掉行`).toContain(
          "max-w-full",
        );
      }
    }
  });

  it("G6 轴 E 冻结：中栏有 240px 地板、左侧栏可收缩、盘口列 flexShrink 不为 0", () => {
    const src = read("Chart/ChartWorkspace.tsx");

    expect(src, "中栏丢了 min-w-[240px] 地板").toContain("min-w-[240px]");

    const sidebar = openTagsContaining(src, "style={{ width: instrumentSidebarWidth }}")[0];
    expect(sidebar, "找不到左侧标的栏容器").toBeTruthy();
    expect(sidebar, "左侧栏又被钉成 shrink-0，中栏会重新变成唯一的压缩受害者").not.toContain(
      "shrink-0",
    );

    const depth = openTagsContaining(src, "depthPanelRatio * 100")[0];
    expect(depth, "找不到盘口列容器").toBeTruthy();
    expect(depth, "盘口列又被钉成 flexShrink: 0").not.toMatch(/flexShrink:\s*0\b/);

    // 三列硬地板合计 612px > 860 窗口下上区的实宽（终审实测 426px）。少了这层裁切，溢出的盘口列会
    // 盖在右侧策略面板上，而不是 K9 承诺的「被裁掉一部分」——地板与裁切必须成对存在。
    const topRow = openTagsContaining(src, "ref={topRef}")[0];
    expect(topRow, "找不到上区三列行容器").toBeTruthy();
    expect(topRow, "上区三列行缺 overflow-hidden，硬地板溢出会盖住右侧面板").toContain(
      "overflow-hidden",
    );
  });
});
