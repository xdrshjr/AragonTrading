'use strict';
// 后训练数据目录的 IPC 入参校验（ai-trade-trajectory-capture §2.15）。
//
// 本文件**不得 require('electron')**：那会让它在纯 node 测试里不可用（`external-links.js` /
// `renderer-health.js` 是本仓已有的「可单测的 lib」范式，此处照抄）。
//
// 为什么 IPC 边界还要校验：渲染层是本应用自己的页面，但 `shell.openPath` 对**文件**的语义是
// 「用关联程序打开」。一个只接受「存在的绝对目录」的边界，不给「借 openPath 执行任意文件」
// 留口子。

const fs = require('fs');
const path = require('path');

/**
 * 非空字符串、不含 NUL、且在当前平台意义下是绝对路径。
 *
 * @param {unknown} value 待判定的值。
 * @returns {boolean}
 */
function isAbsolutePathString(value) {
  if (typeof value !== 'string' || !value.trim()) return false;
  if (value.includes('\0')) return false;
  return path.isAbsolute(value);
}

/**
 * 渲染层传来的 `defaultPath` 只接受绝对路径字符串；其余一律 `undefined`（对话框回落系统默认）。
 *
 * @param {unknown} opts 渲染层传入的 `{ defaultPath?: string }`（可能是任何东西）。
 * @returns {string | undefined} 合法的绝对路径，或 `undefined`。
 */
function pickDefaultPath(opts) {
  if (!opts || typeof opts !== 'object') return undefined;
  const candidate = opts.defaultPath;
  return isAbsolutePathString(candidate) ? candidate : undefined;
}

/**
 * `aragon:open-path` 的入参校验。
 *
 * 必须是异步 stat：它跑在主进程上，网络盘（`\\nas\share\…`）掉线时同步 stat 会把主进程——也就是
 * 整个窗口——卡到 SMB 超时。
 *
 * @param {unknown} target 待打开的路径。
 * @param {{ promises: { stat: (p: string) => Promise<{ isDirectory(): boolean }> } }} [fsImpl]
 *   注入的 fs（测试用）。
 * @returns {Promise<'invalid_path' | 'not_found' | 'not_directory' | null>} 拒绝原因；合法 ⇒ `null`。
 */
async function openPathRejection(target, fsImpl = fs) {
  if (!isAbsolutePathString(target)) return 'invalid_path';
  let stat;
  try {
    stat = await fsImpl.promises.stat(target);
  } catch {
    // ENOENT / EACCES / 盘被拔：对「打开一个目录」而言都等于「这里没有可打开的东西」。
    return 'not_found';
  }
  return stat.isDirectory() ? null : 'not_directory';
}

module.exports = { pickDefaultPath, openPathRejection };
