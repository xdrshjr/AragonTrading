'use strict';
// 预加载脚本：经 contextBridge 暴露最小只读 API + 窗口控制（spec §4.4 / §7.2）。
// contextIsolation:true + nodeIntegration:false → 渲染层（即既有 Web 应用）不获 Node 能力。
const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('aragon', {
  // 渲染层据此判定运行于桌面端（浏览器中 window.aragon 为 undefined → 安全降级）。
  isDesktop: true,
  // 应用版本（同步取自主进程）
  appVersion: () => ipcRenderer.sendSync('aragon:get-version'),
  // 平台字符串（preload 可读 process.platform，复制为不可变值暴露）
  platform: process.platform,
  // 打开日志目录（prod 落 userData/logs；主进程 shell.openPath）
  openLogsDir: () => ipcRenderer.invoke('aragon:open-logs'),
  // 「关于」对话框（补偿被移除菜单的帮助项，主进程 showAbout()）
  showAbout: () => ipcRenderer.send('aragon:show-about'),
  // 自定义标题栏窗口控制（spec §4.3 / §6）：最小化 / 最大化切换 / 关闭 / 查询与订阅最大化态。
  windowControls: {
    minimize: () => ipcRenderer.send('aragon:win:minimize'),
    toggleMaximize: () => ipcRenderer.send('aragon:win:maximize-toggle'),
    close: () => ipcRenderer.send('aragon:win:close'),
    isMaximized: () => ipcRenderer.invoke('aragon:win:is-maximized'),
    // 同步 Windows 系统标题栏控件叠加(titleBarOverlay)配色到明/暗主题（非 win 平台主进程内 no-op）。
    setOverlayTheme: (theme) => ipcRenderer.send('aragon:win:set-overlay-theme', theme),
    // G1（诊断 desktop-packaged-wco-header-right-still-unclickable）：渲染层把观测到的 Window Controls
    // Overlay 运行期状态（是否生效 / 安全区矩形）回传主进程，「关于」据此展示 WCO 是否真激活，便于真机判别。
    reportOverlay: (state) => ipcRenderer.send('aragon:win:report-overlay', state),
    // 订阅最大化态变化，返回取消订阅函数（组件卸载时调用以移除监听）。
    onMaximizeChange: (cb) => {
      const handler = (_e, value) => cb(!!value);
      ipcRenderer.on('aragon:win:maximized-changed', handler);
      return () => ipcRenderer.removeListener('aragon:win:maximized-changed', handler);
    },
  },
});
