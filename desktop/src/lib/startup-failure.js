'use strict';
// 启动失败的用户可读描述（spec D2）：纯函数，不 require('electron')、不碰文件系统。
//
// 镜像 `renderer-health.js` 的手法：把「说什么」与「怎么弹」分开——文案是可被 `node --test`
// 冻结的契约，接线（dialog.showErrorBox + app.exit）留在 main.js 的 whenReady().catch()。
//
// 它存在的理由：`app.whenReady().then(bootstrap)` 此前**没有 .catch()**，于是 bootstrap 里任何
// 预料之外的异常（日志目录建不出来、installAppMenu 抛、createWindow 抛、startEngines 拒绝）都会
// 变成一个未处理的 Promise 拒绝 → Node ≥15 抬成 uncaughtException → app.exit(1)。
// 用户看到的是「双击图标，什么都没发生」——对一个桌面产品而言，**静默消失是最差的失败模式**，
// 它甚至不如崩溃，因为崩溃至少会被操作系统报告。

/** 错误详情在对话框里的截断长度：够定位第一帧调用栈，又不至于把弹窗撑爆屏幕。 */
const DETAIL_MAX_CHARS = 800;

/**
 * 把启动期异常翻成一个可直接喂给 `dialog.showErrorBox` 的标题 / 正文。
 *
 * **对任何入参都不抛**：它是兜底路径上的最后一环，一个会抛的兜底等于没有兜底。
 * 故 `err` 允许是 Error、字符串、undefined、乃至一个 getter 会抛的怪东西。
 *
 * @param {unknown} err 启动期捕获到的异常（不限类型）。
 * @param {string} logDir 完整日志所在目录（调用方应传 `paths.logsDir()` 这类**纯路径拼接**的结果，
 *   绝不要在兜底路径上调会建目录的 `ensureLogsDir()`——建目录失败正是本条要兜的头号失败模式）。
 * @returns {{title: string, body: string}} 标题固定、正文含错误详情、日志目录与常见原因。
 */
function describeStartupFailure(err, logDir) {
  let detail;
  try {
    detail = err && err.stack ? String(err.stack) : String(err);
  } catch {
    detail = '(无法读取错误详情)';   // err 是个 toString/stack 会抛的怪东西时，兜底仍须给出可读结果
  }
  const where = logDir ? String(logDir) : '(日志目录不可用)';
  return {
    title: 'Aragon Trading 启动失败',
    body:
      '应用在启动过程中遇到无法恢复的错误，已安全退出。\n\n' +
      `错误详情：\n${detail.slice(0, DETAIL_MAX_CHARS)}\n\n` +
      `完整日志：${where}\n` +
      '常见原因：端口被占用、Python 后端依赖缺失（请在仓库根执行 `pnpm setup`）、磁盘只读。',
  };
}

module.exports = { describeStartupFailure, DETAIL_MAX_CHARS };
