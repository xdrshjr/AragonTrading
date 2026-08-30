// R1–R14 无头验收：真实浏览器 + 真实 CSS + 真实拖拽。
// 量的是「有没有真的折行」（Range.getClientRects().length > 1），不是「类名写没写」。
const fs = require("node:fs");
const path = require("node:path");
const { chromium } = require("playwright");
const { respond } = require("./fixtures");
const auditSource = require("./audit")();

const BASE = "http://localhost:18763/";
const CHROME = "C:/Program Files/Google/Chrome/Application/chrome.exe";
const OUT = process.argv[2];
fs.mkdirSync(OUT, { recursive: true });

const HANDLES = {
  sidebar: '[role="separator"][aria-label^="拖动调整标的侧栏宽度"]',
  depth: '[role="separator"][aria-label^="拖动调整盘口面板宽度"]',
  right: '[role="separator"][aria-label^="拖动调整右侧面板宽度"]',
};

/** 真实拖拽：把手柄拖到视口 X = targetX。pointer capture 由手柄自己接管。 */
async function dragTo(page, sel, targetX, steps = 12) {
  const el = await page.$(sel);
  if (!el) return false;
  const box = await el.boundingBox();
  if (!box) return false;
  const y = box.y + box.height / 2;
  await page.mouse.move(box.x + box.width / 2, y);
  await page.mouse.down();
  await page.mouse.move(targetX, y, { steps });
  await page.mouse.up();
  await page.waitForTimeout(150);
  return true;
}

const SCENARIOS = [
  { id: "R1-R4-sidebar-min-200", w: 1440, h: 900,
    note: "R1/R2/R3：左侧栏拖到最窄 200px（停止态 + 8 个自选）",
    drag: async (p) => { await dragTo(p, HANDLES.sidebar, 60); } },
  { id: "R5-R14-overconstrained", w: 1440, h: 900,
    note: "R5/R14：右栏拖到 640 + 左栏拖到 460 的过约束档",
    drag: async (p) => { await dragTo(p, HANDLES.right, 800); await dragTo(p, HANDLES.sidebar, 900); } },
  { id: "R6-narrow-860", w: 860, h: 900,
    note: "R6：桌面端最窄窗口 860（N3 已知残留档）",
    drag: async (p) => { await dragTo(p, HANDLES.sidebar, 60); } },
  { id: "R6-wide-1920", w: 1920, h: 1000,
    note: "R6：1920 宽档回程（默认三档宽）" },
  { id: "R10-rightpanel-320", w: 1200, h: 900, strategyPanel: true, openRun: true,
    note: "R10：右栏拖到最窄 320px（交易策略面板 + 运行历史 + 指标卡）",
    drag: async (p) => { await dragTo(p, HANDLES.right, 1180); } },
  { id: "R11-dark-1024", w: 1024, h: 900, theme: "dark",
    note: "R11：暗色主题 1024 档",
    drag: async (p) => { await dragTo(p, HANDLES.sidebar, 60); } },
  { id: "R7-R8-live-bottom", w: 1200, h: 900, strategyPanel: true, openRun: true, bottomTab: "当前持仓",
    note: "R7/R8：实盘下方右栏（10 列委托表 / 持仓表）",
    drag: async (p) => { await dragTo(p, HANDLES.right, 1180); } },
  { id: "R9-trade-explain-expanded", w: 860, h: 900, strategyPanel: true, openRun: true, expandTrades: true,
    note: "R9：最窄档展开成交触发原因详情（散文仍换行 / 指标网格按容器掉列）",
    drag: async (p) => { await dragTo(p, HANDLES.right, 840); } },
  { id: "R4-drag-sweep", w: 1440, h: 900, sweep: true,
    note: "R4：左侧栏在 200↔460 之间连续拖动 20 次" },
];

(async () => {
  const browser = await chromium.launch({ executablePath: CHROME });
  const report = [];
  const seenErrors = new Set();
  for (const sc of SCENARIOS) {
    const ctx = await browser.newContext({ viewport: { width: sc.w, height: sc.h }, deviceScaleFactor: 1 });
    await ctx.route("**/api/py/**", async (route) => {
      const url = new URL(route.request().url());
      const rel = url.pathname.replace(/^\/api\/py/, "") + url.search;
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(respond(rel)) });
    });
    const page = await ctx.newPage();
    page.on("pageerror", (e) => {
      const k = e.message;
      if (seenErrors.has(k)) return;
      seenErrors.add(k);
      console.error("[pageerror " + sc.id + "] " + (e.stack || e.message).split(String.fromCharCode(10)).slice(0, 4).join(" | "));
    });
    await page.goto(BASE, { waitUntil: "domcontentloaded" });
    await page.waitForTimeout(4500);
    if (sc.theme === "dark") {
      const btn = await page.$('button:has-text("暗色")');
      if (btn) { await btn.click(); await page.waitForTimeout(500); }
    }

    if (sc.strategyPanel) {
      const tab = await page.$('[role="tab"]:has-text("交易策略")');
      if (tab) { await tab.click(); await page.waitForTimeout(700); }
    }
    if (sc.openRun) {
      // 运行历史里点开一条 → 主图与下方成交表拿到真实 run（Type C 的九张表才会渲染）。
      const histTab = await page.$('[role="tab"]:has-text("历史")');
      if (histTab) { await histTab.click(); await page.waitForTimeout(600); }
      const row = await page.$('table tbody tr');
      if (row) { await row.click(); await page.waitForTimeout(900); }
    }
    if (sc.bottomTab) {
      const tab = await page.$(`[role="tab"]:has-text("${sc.bottomTab}")`);
      if (tab) { await tab.click(); await page.waitForTimeout(600); }
    }
    if (sc.expandTrades) {
      const row = await page.$('tbody tr.cursor-pointer');
      if (row) { await row.click(); await page.waitForTimeout(500); }
    }
    if (sc.drag) await sc.drag(page);

    let sweep = null;
    if (sc.sweep) {
      sweep = [];
      for (let i = 0; i < 20; i += 1) {
        const target = i % 2 === 0 ? 60 : 900;
        await dragTo(page, HANDLES.sidebar, target, 6);
        const probe = await page.evaluate(() => {
          const el = Array.from(document.querySelectorAll("div[style]")).find(
            (e) => e.style.width && String(e.className).includes("min-w-[200px]"),
          );
          const center = Array.from(document.querySelectorAll("div")).find(
            (e) => typeof e.className === "string" && e.className.includes("min-w-[240px]"),
          );
          return {
            sidebar: el ? Math.round(el.getBoundingClientRect().width) : null,
            styleW: el ? el.style.width : null,
            sidebarScrollX: el ? el.scrollWidth - el.clientWidth : null,
            center: center ? Math.round(center.getBoundingClientRect().width) : null,
          };
        });
        sweep.push(probe);
      }
    }

    const result = await page.evaluate(auditSource);
    await page.screenshot({ path: path.join(OUT, `${sc.id}.png`), fullPage: false });
    report.push({ scenario: { id: sc.id, w: sc.w, h: sc.h, note: sc.note }, result, sweep });
    const g = result.geom;
    console.log(`[${sc.id}] wrapped=${result.wrapped.length} anchors=${result.anchors.length} sidebar=${g.sidebar}(style ${g.sidebarStyleWidth}) center=${g.center} depth=${g.depthWidth} overflow=${g.horizontalOverflow}`);
    await ctx.close();
  }
  await browser.close();
  fs.writeFileSync(path.join(OUT, "audit.json"), JSON.stringify(report, null, 2), "utf8");
  console.log("DONE");
})().catch((e) => { console.error(e); process.exit(1); });
