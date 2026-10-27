// WCO 复现探针（诊断 desktop-header-app-controls-unclickable-wco）。
//
// 目的：在与生产同构的 Windows Controls Overlay 窗口里，量化 3 个 headless-可测事实：
//   (1) WCO 是否真激活（navigator.windowControlsOverlay.visible）；
//   (2) env(titlebar-area-*) 安全区是否被 Chromium 填充（否则 .win-titlebar-safe padding 归零，
//       右簇滑入系统三键覆盖区）——读取 header 计算 padding-right 与 getTitlebarAreaRect()；
//   (3) 还原态 vs 最大化态的「窗口外框 vs Web 内容」帧内缩进不对称（几何缺陷根因输入）。
//
// 注意：OS 非客户区(NC)命中测试在浏览器进程内、渲染层不可观测，sendInputEvent 会绕过 NC 命中，
// 故本探针不能证明「click 未下发」；那只能在真实 Windows 还原态人工点击验证。本探针只钉死上述
// 可 headless 度量的几何/能力事实，与人工门互补。
//
// 运行（desktop/ 下，必须先清 ELECTRON_RUN_AS_NODE）：
//   node_modules/electron/dist/electron.exe ../docs/diagnoses/desktop-header-app-controls-unclickable-wco/repro/probe.js

const { app, BrowserWindow } = require('electron');
const path = require('path');
const fs = require('fs');

const OUT = path.join(__dirname, 'result.txt');
const lines = [];
const log = (s) => { lines.push(s); };

// 复刻 desktop/src/main.js:214-263 的 win 分支窗口选项。
function makeWin() {
  return new BrowserWindow({
    width: 1440, height: 900, show: true,
    backgroundColor: '#0b0d12',
    titleBarStyle: 'hidden',
    titleBarOverlay: { color: '#1a1915', symbolColor: '#ece8df', height: 48 },
    // 注意：与 main.js 一致，未设 resizable → 默认 true → WS_THICKFRAME。
    webPreferences: { contextIsolation: true, nodeIntegration: false },
  });
}

async function readState(win, tag) {
  const b = win.getBounds();
  const c = win.getContentBounds();
  log(`\n=== ${tag} ===`);
  log(`getBounds        : x=${b.x} y=${b.y} w=${b.width} h=${b.height}`);
  log(`getContentBounds : x=${c.x} y=${c.y} w=${c.width} h=${c.height}`);
  log(`帧内缩进(inset)  : dx=${c.x - b.x} dy=${c.y - b.y}`);
  const js = `(() => {
    const h = document.querySelector('header');
    const cs = getComputedStyle(h);
    const wco = navigator.windowControlsOverlay;
    let rect = null;
    try { const r = wco && wco.getTitlebarAreaRect(); if (r) rect = {x:Math.round(r.x),y:Math.round(r.y),w:Math.round(r.width),h:Math.round(r.height)}; } catch(e){}
    const btn = document.getElementById('gear').getBoundingClientRect();
    const seg = document.getElementById('live').getBoundingClientRect();
    return {
      wcoSupported: !!wco,
      wcoVisible: wco ? !!wco.visible : null,
      titlebarAreaRect: rect,
      headerPaddingRight: cs.paddingRight,
      gearAppRegion: getComputedStyle(document.getElementById('gear')).webkitAppRegion || getComputedStyle(document.getElementById('gear')).getPropertyValue('-webkit-app-region'),
      gearRect: {x:Math.round(btn.x),y:Math.round(btn.y),w:Math.round(btn.width),h:Math.round(btn.height)},
      liveRect: {x:Math.round(seg.x),y:Math.round(seg.y),w:Math.round(seg.width),h:Math.round(seg.height)},
      innerW: window.innerWidth,
    };
  })()`;
  const r = await win.webContents.executeJavaScript(js);
  log(`WCO supported    : ${r.wcoSupported}`);
  log(`WCO visible      : ${r.wcoVisible}`);
  log(`titlebarAreaRect : ${JSON.stringify(r.titlebarAreaRect)}`);
  log(`header pad-right  : ${r.headerPaddingRight}   (env 未填充 → 0px；已填充 → ≈系统三键带宽)`);
  log(`gear app-region  : ${r.gearAppRegion}`);
  log(`gear rect        : ${JSON.stringify(r.gearRect)}   innerW=${r.innerW}`);
  log(`live(实盘) rect  : ${JSON.stringify(r.liveRect)}`);
}

app.whenReady().then(async () => {
  const win = makeWin();
  await win.loadFile(path.join(__dirname, 'page.html'));
  await new Promise((res) => setTimeout(res, 800));
  await readState(win, 'RESTORED (还原态，默认启动态)');
  win.maximize();
  await new Promise((res) => setTimeout(res, 800));
  await readState(win, 'MAXIMIZED (最大化态)');
  fs.writeFileSync(OUT, lines.join('\n') + '\n', 'utf-8');
  console.log(lines.join('\n'));
  console.log('\n[probe] wrote ' + OUT);
  app.quit();
});
