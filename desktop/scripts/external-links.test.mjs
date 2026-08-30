/**
 * Regression tests for external link handling (src/lib/external-links.js).
 *
 * Guards the invariant that decides whether a source link in the market-intel panel opens in the
 * user's real browser or hijacks the desktop shell: only http(s) may reach `shell.openExternal`,
 * every `window.open` is denied a new Electron window, and `will-navigate` never lets an external
 * URL replace the main window.
 *
 * Run (Node 20 compatible — the package script's glob needs Node 21+):
 *   node --test scripts/external-links.test.mjs
 */

import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { test } from "node:test";

const require = createRequire(import.meta.url);
const { isExternalUrl, isInternalNavigation, wireExternalLinks } = require(
  "../src/lib/external-links.js",
);

/** 假窗口：记录 setWindowOpenHandler 与 will-navigate 的接线，供逐条断言。 */
function fakeWin(currentUrl = "http://127.0.0.1:3000/") {
  const listeners = {};
  return {
    opened: [],
    webContents: {
      getURL: () => currentUrl,
      setWindowOpenHandler(handler) {
        this._openHandler = handler;
      },
      on(event, handler) {
        listeners[event] = handler;
      },
      _listeners: listeners,
    },
  };
}

function fakeShell() {
  const calls = [];
  return { calls, openExternal: (url) => calls.push(url) };
}

test("只有 http / https 被判为可外开", () => {
  assert.equal(isExternalUrl("https://theblock.co/x"), true);
  assert.equal(isExternalUrl("http://example.com"), true);
  for (const url of [
    "mailto:a@b.c",
    "file:///etc/passwd",
    "javascript:alert(1)",
    "data:text/html,<h1>x</h1>",
    "",
    null,
    "not a url",
  ]) {
    assert.equal(isExternalUrl(url), false, `${String(url)} 不该被外开`);
  }
});

test("同 origin 与 file: 判为应用内导航，其余不是", () => {
  const here = "http://127.0.0.1:3000/workspace";
  assert.equal(isInternalNavigation("http://127.0.0.1:3000/settings", here), true);
  assert.equal(isInternalNavigation("file:///C:/app/loading.html", here), true);
  assert.equal(isInternalNavigation("https://evil.example/x", here), false);
});

test("window.open 恒 deny 新窗口；http(s) 转交系统浏览器", () => {
  const win = fakeWin();
  const shell = fakeShell();
  wireExternalLinks(win, { shell });
  const result = win.webContents._openHandler({ url: "https://coindesk.com/a" });
  assert.deepEqual(result, { action: "deny" }, "绝不允许开出无外壳的 Electron 窗口");
  assert.deepEqual(shell.calls, ["https://coindesk.com/a"]);
});

test("window.open 遇到非 http(s) 时 deny 且**不调** openExternal", () => {
  const win = fakeWin();
  const shell = fakeShell();
  wireExternalLinks(win, { shell });
  for (const url of ["mailto:a@b.c", "file:///etc/passwd", "javascript:alert(1)"]) {
    const result = win.webContents._openHandler({ url });
    assert.deepEqual(result, { action: "deny" }, url);
  }
  assert.deepEqual(shell.calls, [], "一个交易客户端不该代为打开这些协议");
});

test("will-navigate：外部地址被拦下并转交系统浏览器，主窗口不被换掉", () => {
  const win = fakeWin();
  const shell = fakeShell();
  wireExternalLinks(win, { shell });
  let prevented = false;
  win.webContents._listeners["will-navigate"](
    { preventDefault: () => (prevented = true) },
    "https://theblock.co/story",
  );
  assert.equal(prevented, true, "不拦下它，用户会失去整个工作台且没有返回路径");
  assert.deepEqual(shell.calls, ["https://theblock.co/story"]);
});

test("will-navigate：应用自身的 origin 原样放行", () => {
  const win = fakeWin("http://127.0.0.1:3000/");
  const shell = fakeShell();
  wireExternalLinks(win, { shell });
  let prevented = false;
  win.webContents._listeners["will-navigate"](
    { preventDefault: () => (prevented = true) },
    "http://127.0.0.1:3000/settings",
  );
  assert.equal(prevented, false);
  assert.deepEqual(shell.calls, []);
});

test("will-navigate：非 http(s) 被拦下且不外开", () => {
  const win = fakeWin();
  const shell = fakeShell();
  wireExternalLinks(win, { shell });
  let prevented = false;
  win.webContents._listeners["will-navigate"](
    { preventDefault: () => (prevented = true) },
    "javascript:alert(1)",
  );
  assert.equal(prevented, true);
  assert.deepEqual(shell.calls, []);
});

test("缺 shell 时静默不接线（绝不抛，避免拖垮窗口创建）", () => {
  const win = fakeWin();
  assert.doesNotThrow(() => wireExternalLinks(win, {}));
  assert.equal(win.webContents._openHandler, undefined);
});
