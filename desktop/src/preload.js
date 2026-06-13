'use strict';
// 预加载脚本：经 contextBridge 暴露最小只读 API（spec §7.2）。
// contextIsolation:true + nodeIntegration:false → 渲染层（即既有 Web 应用）不获 Node 能力。
const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('aragon', {
  // 应用版本（同步取自主进程）
  appVersion: () => ipcRenderer.sendSync('aragon:get-version'),
  // 平台字符串（preload 可读 process.platform，复制为不可变值暴露）
  platform: process.platform,
  // 打开日志目录（prod 落 userData/logs；主进程 shell.openPath）
  openLogsDir: () => ipcRenderer.invoke('aragon:open-logs'),
});
