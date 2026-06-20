'use strict';
// 加载窗关闭键（spec §4.5 / D7）：经同一 preload 暴露的 window.aragon.windowControls.close()
// 触发 win.close() → 既有退出清理链，使后端慢启动时用户仍可主动关闭无边框加载窗（R-4）。
document.getElementById('loading-close')?.addEventListener('click', () => {
  window.aragon?.windowControls?.close();
});
