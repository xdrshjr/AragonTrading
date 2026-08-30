'use strict';
// 外链处理（ai-trade-market-intel §3.9「外链劫持」行 / G-10）。
//
// 本文件**不得 require('electron')**：那会让它在纯 node 测试里不可用（`renderer-health.js` /
// `config.js` 是本仓已有的「可单测的 lib」范式，此处照抄）。`shell` 由调用方注入。
//
// 为什么本轮才需要它：全仓此前**没有任何** `target="_blank"`，桌面壳因此既没有
// `setWindowOpenHandler` 也没有 `will-navigate` 守卫。市场情报的来源链接是本仓库第一次需要外链——
// 不补这一层，打包后点击来源会开出一个**无外壳的 Electron 窗口**（没有地址栏、没有返回键，且
// 仍带着应用的会话），而 `will-navigate` 的缺席意味着任何一次导航都能把主窗口整个换掉。
//
// 判据只有一条：**只有 http / https 交给系统浏览器，其余一律拒绝**。`mailto:` / `file:` /
// `javascript:` 三者都不该由一个交易客户端代为打开。

/** 允许交给系统浏览器的协议。字符串比较，不做任何模糊匹配。 */
const ALLOWED_PROTOCOLS = ['http:', 'https:'];

/**
 * 判定一个 URL 是否可以交给系统浏览器打开。
 *
 * @param {string} url 待判定的地址（可能不是合法 URL）。
 * @returns {boolean} 仅 http / https 为真；解析失败一律为假（**宁可不开，也不猜**）。
 */
function isExternalUrl(url) {
  if (typeof url !== 'string' || !url) return false;
  try {
    return ALLOWED_PROTOCOLS.includes(new URL(url).protocol);
  } catch {
    return false;
  }
}

/**
 * 判定一次导航是否属于「应用自身」。
 *
 * @param {string} url 目标地址。
 * @param {string} currentUrl 当前窗口地址（`win.webContents.getURL()`）。
 * @returns {boolean} 同 origin 为真；`file:` 协议按 `file://` 整体视为自身（打包后的 loading.html）。
 */
function isInternalNavigation(url, currentUrl) {
  if (typeof url !== 'string' || !url) return false;
  try {
    const target = new URL(url);
    if (target.protocol === 'file:') return true;
    if (typeof currentUrl !== 'string' || !currentUrl) return false;
    const current = new URL(currentUrl);
    return target.origin === current.origin;
  } catch {
    return false;
  }
}

/**
 * 给窗口挂上外链处理。
 *
 * @param {object} win BrowserWindow（或测试替身）；需有 `webContents.setWindowOpenHandler` 与 `on`。
 * @param {object} deps 注入依赖。
 * @param {{openExternal: (url: string) => unknown}} deps.shell Electron 的 `shell`（测试可替身）。
 * @param {(msg: string) => void} [deps.log] 日志（默认 no-op）。
 * @returns {void}
 */
function wireExternalLinks(win, deps) {
  const shell = deps && deps.shell;
  const log = (deps && deps.log) || function noop() {};
  if (!win || !win.webContents || !shell) return;
  const wc = win.webContents;

  // ① `window.open` / `target="_blank"`：**恒 deny 新窗口**，只有 http(s) 转交系统浏览器。
  //    「deny + openExternal」是一对，不能只做前者（链接会变成哑的）也不能只做后者（Electron
  //    仍会开出那个无外壳窗口）。
  wc.setWindowOpenHandler(function handle(details) {
    const url = details && details.url;
    if (isExternalUrl(url)) {
      shell.openExternal(url);
    } else {
      log(`blocked window.open for non-http(s) url: ${String(url)}`);
    }
    return { action: 'deny' };
  });

  // ② `will-navigate`：应用自身的 origin 放行，其余 `preventDefault` 后按 ① 的规则处理。
  //    没有这一条，一次 `location.href = 'https://…'` 会把主窗口**整个换掉**——用户会失去
  //    整个工作台，且没有任何返回路径。
  wc.on('will-navigate', function onWillNavigate(event, url) {
    if (isInternalNavigation(url, wc.getURL && wc.getURL())) return;
    if (event && typeof event.preventDefault === 'function') event.preventDefault();
    if (isExternalUrl(url)) {
      shell.openExternal(url);
      return;
    }
    log(`blocked navigation to non-http(s) url: ${String(url)}`);
  });
}

module.exports = { ALLOWED_PROTOCOLS, isExternalUrl, isInternalNavigation, wireExternalLinks };
