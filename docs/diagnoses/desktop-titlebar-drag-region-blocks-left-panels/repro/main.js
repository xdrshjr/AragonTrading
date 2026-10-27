'use strict';
const { app, BrowserWindow } = require('electron');
const path = require('path');

app.commandLine.appendSwitch('disable-gpu');

app.whenReady().then(async () => {
  const isMac = process.platform === 'darwin';
  const win = new BrowserWindow({
    width: 800,
    height: 600,
    show: false,
    frame: false, // replicate desktop/src/main.js win/linux
    webPreferences: { offscreen: false },
  });
  await win.loadFile(path.join(__dirname, 'page.html'));
  // give layout a tick
  await new Promise((r) => setTimeout(r, 300));
  const result = await win.webContents.executeJavaScript('window.__regResult');
  process.stdout.write('\n===COMPUTED -webkit-app-region===\n' + result + '\n===END===\n');
  app.quit();
});

app.on('window-all-closed', () => app.quit());
