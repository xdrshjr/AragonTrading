'use strict';
// 渲染进程健康的纯逻辑（spec D5）：崩溃原因翻译 / 重载配额 / 加载失败判读。
//
// 本文件**不得 require('electron')**：那会让它在纯 node 测试里不可用（`paths.js` / `agent-env.js`
// 就是这样被挡在门外的）。`config.js` 是本仓已有的「可单测的 lib」范式，此处照抄。
// 接线（事件订阅、弹窗、reload）留在 main.js 的 wireRendererHealth()，需要真实 Electron 运行时。
//
// 它存在的理由：本仓有过一次成建制的白屏事故，事后补的是**渲染层**的 error boundary，而渲染进程
// 崩溃恰恰是 React 错误边界接不住的那一类（进程已经没了）。一个 7×24 挂着行情 WS 与图表的桌面端，
// 这条路径迟早会走到。

/** Electron `render-process-gone` 的 reason → 中文人话 + 是否值得自动重载。 */
const GONE_REASONS = {
  crashed: { message: '界面进程崩溃（renderer crashed）', recoverable: true },
  oom: { message: '界面进程内存不足被系统回收（out of memory）', recoverable: true },
  'abnormal-exit': { message: '界面进程异常退出（abnormal exit）', recoverable: true },
  'launch-failed': { message: '界面进程启动失败（launch failed）', recoverable: false },
  'integrity-failure': { message: '界面进程完整性校验失败（integrity failure）', recoverable: false },
  // killed：多半是用户 / 系统主动结束进程（任务管理器、退出流程），自动拉回来反而是在跟用户较劲。
  killed: { message: '界面进程被结束（killed）', recoverable: false },
  // clean-exit 是 Chromium 的 NORMAL_TERMINATION：**它根本不是崩溃**。既不该自愈（把一个刚被
  // 正常关掉的渲染进程拉回来），也不该弹「界面进程已退出」的错误框吓用户——只记一行日志。
  'clean-exit': { message: '界面进程正常退出（clean exit）', recoverable: false, expected: true },
};

/**
 * 把 `render-process-gone` 的 details 翻成可记日志、可展示的一句话。
 *
 * @param {{reason?: string, exitCode?: number}} details Electron 回调原样传入。
 * @returns {{reason: string, message: string, recoverable: boolean, expected: boolean}}
 *   `recoverable` 为真表示「值得自动重载一次」；未知 reason 一律按不可恢复处理——
 *   宁可让用户看见一个说人话的对话框，也不要对一个我们看不懂的状态反复自动重载。
 *   `expected` 为真表示「这不是故障」（当前仅 `clean-exit`）：调用方记完日志即收手，
 *   既不自愈也不弹窗。它有明确读点（`wireRendererHealth`），不是装饰性字段。
 */
function describeRendererGone(details) {
  const reason = (details && details.reason) || 'unknown';
  const known = GONE_REASONS[reason];
  const exitCode = details && typeof details.exitCode === 'number' ? details.exitCode : null;
  const suffix = exitCode == null ? '' : `（exit code ${exitCode}）`;
  if (!known) {
    return { reason, message: `界面进程已退出：${reason}${suffix}`, recoverable: false, expected: false };
  }
  return {
    reason,
    message: `${known.message}${suffix}`,
    recoverable: known.recoverable,
    expected: known.expected === true,
  };
}

/**
 * 滑动窗口重载配额。**没有配额就没有自愈**——崩溃 → 重载 → 再崩溃的死循环比白屏更糟。
 *
 * `nowMs` 由调用方注入（保持纯函数、可测）。刻意**不提供** `reset()`：一旦它被接上
 * （例如「加载成功即清零」），「崩 → 重载 → 加载成功 → 清零 → 再崩」就会变成一个无限自愈循环。
 * 滑动窗口是唯一的机制；测试要新配额就新造一个。
 *
 * @param {{limit?: number, windowMs?: number}} [opts]
 * @returns {{allow(nowMs: number): boolean}} `allow` 返回是否放行本次重载，放行即记账。
 */
function createReloadBudget(opts) {
  const { limit = 3, windowMs = 60_000 } = opts || {};
  const stamps = [];
  return {
    allow(nowMs) {
      while (stamps.length > 0 && nowMs - stamps[0] >= windowMs) stamps.shift();
      if (stamps.length >= limit) return false;
      stamps.push(nowMs);
      return true;
    },
  };
}

/** `did-fail-load` 里表示「正常导航被打断」的错误码，不是故障。 */
const ERR_ABORTED = -3;

/**
 * 把 `did-fail-load` 的三个参数翻成一行日志文本。
 *
 * @param {number} errorCode Chromium 网络错误码。
 * @param {string} errorDescription Chromium 的英文描述。
 * @param {string} validatedURL 失败的 URL。
 * @returns {string|null} `null` 表示**应当忽略**（`-3` ERR_ABORTED：切页 / 主动打断会产生它）。
 */
function describeLoadFailure(errorCode, errorDescription, validatedURL) {
  if (errorCode === ERR_ABORTED) return null;
  const desc = errorDescription || 'unknown error';
  return `${validatedURL || '(no url)'} → ${desc}（code ${errorCode}）`;
}

module.exports = { describeRendererGone, createReloadBudget, describeLoadFailure };
