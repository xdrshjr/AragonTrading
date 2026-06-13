'use strict';
// dev / packaged 路径解析 (spec §4.4)。
// dev：复用仓库内 venv 与 apps/web 源码；prod：用 extraResources 落在 resourcesPath 下的
// 后端可执行体与 Next standalone 产物（路径取自 build:web 写的 web-server.json 清单）。

const path = require('path');
const fs = require('fs');
const { app } = require('electron');

const IS_WIN = process.platform === 'win32';

// dev：本文件位于 <repo>/desktop/src/lib/paths.js → 上溯三级到仓库根 (spec §4.4)。
// prod（asar 内）不使用 REPO_ROOT —— 仅 dev 分支引用它。
const REPO_ROOT = path.resolve(__dirname, '..', '..', '..');

// 模式唯一真源：app.isPackaged（P2-5）。ARAGON_MODE 仅用于开发期增强（如强制开 DevTools）。
function isPackaged() {
  return app.isPackaged;
}

function mode() {
  return isPackaged() ? 'prod' : 'dev';
}

// ---- dev 资源 ----
function venvPython() {
  const rel = IS_WIN ? path.join('Scripts', 'python.exe') : path.join('bin', 'python');
  return path.join(REPO_ROOT, 'services', 'quant', '.venv', rel);
}

function backendCwdDev() {
  return path.join(REPO_ROOT, 'services', 'quant');
}

function webDirDev() {
  return path.join(REPO_ROOT, 'apps', 'web');
}

// dev 前端：直接用 Electron 自带 Node 跑 apps/web 内的 next 可执行脚本（见 §11 实施发现）。
function nextBinDev() {
  return path.join(webDirDev(), 'node_modules', 'next', 'dist', 'bin', 'next');
}

// ---- prod 资源（resourcesPath/backend、resourcesPath/web）----
function resourcesRoot() {
  return process.resourcesPath;
}

function backendExe() {
  const name = IS_WIN ? 'aragon-quant.exe' : 'aragon-quant';
  return path.join(resourcesRoot(), 'backend', name);
}

function backendCwdProd() {
  return path.join(resourcesRoot(), 'backend');
}

function webResourceDir() {
  return path.join(resourcesRoot(), 'web');
}

/**
 * 读取 build:web 写的清单，解析 standalone server.js 的真实路径与 cwd。
 * monorepo 下 server.js 可能落在 web/apps/web/server.js（嵌套），故不硬编码（D7 / R2）。
 */
function readWebServerManifest() {
  const manifestPath = path.join(webResourceDir(), 'web-server.json');
  const raw = fs.readFileSync(manifestPath, 'utf-8');
  const manifest = JSON.parse(raw);
  // serverJs 为相对 resources/web/ 的 POSIX 路径；path.join 在 Windows 下亦正确处理正斜杠。
  const serverJs = path.join(webResourceDir(), manifest.serverJs);
  return { serverJs, cwd: path.dirname(serverJs), manifest };
}

// ---- userData（prod 落可写区，D6）----
function userDataDbPath() {
  return path.join(app.getPath('userData'), 'aragon.db');
}

function logsDir() {
  return path.join(app.getPath('userData'), 'logs');
}

function ensureLogsDir() {
  const dir = logsDir();
  fs.mkdirSync(dir, { recursive: true });
  return dir;
}

module.exports = {
  IS_WIN,
  REPO_ROOT,
  isPackaged,
  mode,
  venvPython,
  backendCwdDev,
  webDirDev,
  nextBinDev,
  resourcesRoot,
  backendExe,
  backendCwdProd,
  webResourceDir,
  readWebServerManifest,
  userDataDbPath,
  logsDir,
  ensureLogsDir,
};
