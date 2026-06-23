'use strict';
// 诊断探针：测量无边框可缩放窗口在「还原 vs 最大化」两态下，
// 外框矩形(getBounds) 与 Web 内容矩形(getContentBounds) 的差值（= OS 帧内缩进 frame inset）。
// 该差值正是 -webkit-app-region 命中区在两态间错位的根因输入。
// 同时读取顶栏 no-drag 按钮的计算 -webkit-app-region（应恒为 no-drag，排除 CSS 因素）。
//
// 运行（在 desktop/ 下，需清除 ELECTRON_RUN_AS_NODE）：
//   node_modules/electron/dist/electron.exe ../docs/diagnoses/desktop-titlebar-buttons-unclickable-when-restored/repro/probe.js

const { app, BrowserWindow } = require('electron');
const path = require('path');

function rect(b) {
  return `x=${b.x} y=${b.y} w=${b.width} h=${b.height}`;
}

async function readButton(win) {
  return win.webContents.executeJavaScript(`(() => {
    const btn = document.getElementById('gear');
    const r = btn.getBoundingClientRect();
    const cs = getComputedStyle(btn).webkitAppRegion;
    const hdr = getComputedStyle(document.querySelector('header')).webkitAppRegion;
    return { btnRect: { x: r.x, y: r.y, w: r.width, h: r.height }, btnRegion: cs, headerRegion: hdr };
  })();`);
}

app.whenReady().then(async () => {
  const win = new BrowserWindow({
    width: 1000,
    height: 700,
    frame: false, // 与生产同：win 无边框
    // resizable 默认 true → WS_THICKFRAME（与生产同，未显式设置）
    show: true,
    webPreferences: { contextIsolation: true, nodeIntegration: false },
  });

  await win.loadFile(path.join(__dirname, 'page.html'));
  // 等一帧，确保布局/拖拽区已上报
  await new Promise((r) => setTimeout(r, 500));

  const lines = [];
  const log = (s) => {
    lines.push(s);
    process.stdout.write(s + '\n');
  };

  log('=== RESTORED (非最大化) ===');
  log('isMaximized: ' + win.isMaximized());
  log('getBounds       : ' + rect(win.getBounds()));
  log('getContentBounds: ' + rect(win.getContentBounds()));
  log('button/header   : ' + JSON.stringify(await readButton(win)));

  win.maximize();
  await new Promise((r) => setTimeout(r, 500));

  log('=== MAXIMIZED (最大化) ===');
  log('isMaximized: ' + win.isMaximized());
  log('getBounds       : ' + rect(win.getBounds()));
  log('getContentBounds: ' + rect(win.getContentBounds()));
  log('button/header   : ' + JSON.stringify(await readButton(win)));

  require('fs').writeFileSync(path.join(__dirname, 'result.txt'), lines.join('\n'), 'utf-8');
  app.quit();
});
