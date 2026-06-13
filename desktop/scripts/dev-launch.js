'use strict';
// Electron dev 启动器（§11 实施发现 2）。
//
// 背景：某些运行环境的 ambient 里预置了 ELECTRON_RUN_AS_NODE=1（本项目验收环境即如此）。
// 该变量会让 `electron .` 以**纯 Node** 而非 GUI 模式启动 —— 此时主进程里
// `const { app } = require('electron')` 得到 undefined，启动即崩
// （TypeError: Cannot read properties of undefined）。
//
// 为使 `npm run dev` 在任何环境都稳定拉起 Electron **桌面窗口**（而非浏览器/纯 Node），
// 这里在 spawn electron 前**显式删除该变量**，并设好 ARAGON_MODE=dev（强制开 DevTools）。
// 在干净环境下此举无副作用，等价于 spec §7.1 的 `cross-env ARAGON_MODE=dev electron .`。

const path = require('path');
const { spawn } = require('child_process');

delete process.env.ELECTRON_RUN_AS_NODE; // 关键：确保以 GUI 模式启动
process.env.ARAGON_MODE = 'dev';

// 纯 Node 下 require('electron') 返回 electron 可执行体的绝对路径（字符串）。
const electronPath = require('electron');
const appDir = path.resolve(__dirname, '..');

const child = spawn(electronPath, [appDir], { stdio: 'inherit', env: process.env });
child.on('close', (code) => process.exit(code == null ? 0 : code));
child.on('error', (err) => {
  console.error('[dev-launch] 启动 Electron 失败：', err);
  process.exit(1);
});
