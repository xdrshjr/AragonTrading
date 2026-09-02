'use strict';
// 带上限的日志轮转写入器（ai-trade-silent-degradation-repair 轴 D-1）。零依赖，纯 `fs`。
//
// 它要修的事实：`main.js` 的 `openLogStreams()` 用 `fs.createWriteStream(..., {flags:'a'})`
// 纯追加，**无大小上限、无份数上限、无启动清理**。用户本机的 `backend.log` 已经 254.7 MB
// 单文件（216 万行），而用户被要求「看看桌面端的日志」——一个 254 MB 的单文件既打不开，
// 也不可能被人眼扫出任何东西。
//
// 三条不可协商的语义：
//
// 1. **打开时若既有文件已 >= maxBytes，先轮转一次**。否则本机现状那 254.7 MB 会一直挂到
//    下一次越线才被处理，而它已经越线了。
// 2. **rename 失败绝不抛、绝不丢数据**。Windows 上文件被杀软 / 编辑器占用时 rename 会
//    EBUSY/EPERM——此时继续写当前流，下一次 write 再试。一个用来保命的日志轮转器，自己
//    绝不能成为进程崩溃的原因（它跑在 `bootstrap()` 的第一行上）。
// 3. **暴露的形状与 `fs.WriteStream` 在调用点所需的子集一致**（`.write` / `.end`），故
//    `process-manager` 的 `logStream` 传参一行不改。

const fs = require('fs');
const path = require('path');

const DEFAULT_MAX_BYTES = 16 * 1024 * 1024; // 16 MB
const DEFAULT_KEEP = 3;

/** 追加模式打开；失败 ⇒ `null`（磁盘满 / 权限：不写日志远好过起不来）。 */
function openAppend(file) {
  try {
    return fs.openSync(file, 'a');
  } catch {
    return null;
  }
}

/** 当前文件已有多少字节（不存在 / 读不到 ⇒ 0：宁可多写一轮也不要在启动路径上抛）。 */
function sizeOf(file) {
  try {
    return fs.statSync(file).size;
  } catch {
    return 0;
  }
}

/**
 * rename 链：`x.{keep-1}.log` 删除 → … → `x.1.log` → `x.2.log` → `x.log` → `x.1.log`。
 *
 * 任何一步失败即整体放弃本次轮转（返回 false），调用方继续写当前流。**刻意不做部分轮转**：
 * 半个 rename 链留下的是一组名字对不上的文件，比一个超大的单文件更难排查。
 */
function rotateOnce(dir, name, keep) {
  const at = (i) => path.join(dir, i === 0 ? `${name}.log` : `${name}.${i}.log`);
  try {
    try {
      fs.unlinkSync(at(keep - 1));
    } catch {
      // 最老的一份可能本来就不存在——这不是失败。
    }
    for (let i = keep - 2; i >= 1; i -= 1) {
      if (fs.existsSync(at(i))) fs.renameSync(at(i), at(i + 1));
    }
    if (fs.existsSync(at(0))) fs.renameSync(at(0), at(1));
    return true;
  } catch {
    return false; // Windows 占用：本次不轮转，下一次 write 再试。
  }
}

/**
 * 打开一个带上限的追加写入器。
 *
 * **写是同步的（`fs.writeSync`）而不是 `fs.createWriteStream`**，这不是风格选择：
 * `WriteStream.write()` 只把字节交给内部缓冲，而轮转要 `renameSync` 那个**正被打开着**的
 * 文件——Windows 上重命名一个仍有句柄的文件会 EBUSY，缓冲里还没落盘的那几行则会写进已经
 * 改名的旧文件里。同步写让「越线 → 关句柄 → rename → 重开」这四步之间不存在任何窗口。
 * 代价可以忽略：轴 D-2 之后日增量约 0.5 MB/日（今天是 12 MB/日），而调用点
 * （`process-manager` 的三处 `logStream.write`）本来就在子进程 stdout 的事件回调里。
 *
 * @param {string} dir 日志目录（调用方保证已存在）。
 * @param {string} name 文件基名（不含 `.log`）。
 * @param {{maxBytes?: number, keep?: number}} [opts]
 *   `maxBytes = 0` ⇒ **不轮转**，语义与 `fs.createWriteStream(..., {flags:'a'})` 一致。
 * @returns {{write: (chunk: any) => boolean, end: () => void, path: () => string}}
 */
function openRotating(dir, name, opts = {}) {
  const maxBytes = Number.isFinite(Number(opts.maxBytes))
    ? Math.max(0, Number(opts.maxBytes))
    : DEFAULT_MAX_BYTES;
  const keep = Math.max(1, Number(opts.keep) || DEFAULT_KEEP);
  const file = path.join(dir, `${name}.log`);

  // 语义 1：打开时就已经超限 ⇒ 先轮转（本机那 254.7 MB 会在下一次启动时立刻变成 backend.1.log）。
  if (maxBytes > 0 && sizeOf(file) >= maxBytes) rotateOnce(dir, name, keep);

  let fd = openAppend(file);
  let written = sizeOf(file);
  let closed = false;

  function closeFd() {
    if (fd === null) return;
    try {
      fs.closeSync(fd);
    } catch {
      // 句柄已经没了：不是失败，下面照常重开。
    }
    fd = null;
  }

  return {
    write(chunk) {
      if (closed) return false;
      const buf = Buffer.isBuffer(chunk) ? chunk : Buffer.from(String(chunk), 'utf-8');
      try {
        if (fd === null) fd = openAppend(file);
        if (fd === null) return false;
        fs.writeSync(fd, buf);
        written += buf.length;
      } catch {
        return false; // 写失败即放弃这一行；日志器绝不把进程一起带走。
      }
      if (maxBytes > 0 && written >= maxBytes) {
        closeFd();                       // 必须先关句柄：Windows 不让 rename 一个开着的文件
        rotateOnce(dir, name, keep);     // 失败（被占用）也不抛：接着写同一个文件，下次再试
        fd = openAppend(file);
        written = sizeOf(file);
      }
      return true;
    },
    end() {
      closed = true;
      closeFd();
    },
    path() {
      return file;
    },
  };
}

/**
 * 解析轮转参数：**三级回落** `agent.env` → `process.env` → 内建默认（轴 D-1 / 评审 P0-5）。
 *
 * 住在本模块而不是 `main.js` 里，是为了让它可被 `node --test` 直接喂一个假的 `loadAgentEnv`
 * 断言——`main.js` 顶部 `require('electron')`，在 node 下根本 require 不进来，而
 * 「参数到底是从 agent.env 读的还是只从 process.env 读的」正是这条修法唯一会失败的地方
 * （门禁 G-D7 ③）。
 *
 * @param {() => Record<string, string>} loadAgentEnv 读取 `userData/agent.env` 的函数（可能抛）。
 * @param {Record<string, string|undefined>} [processEnv] 进程环境（缺省 `process.env`）。
 * @returns {{maxBytes: number, keep: number}}
 */
function resolveLogOptions(loadAgentEnv, processEnv = process.env) {
  let fromAgentEnv = {};
  try {
    fromAgentEnv = (typeof loadAgentEnv === 'function' && loadAgentEnv()) || {};
  } catch {
    // 文件不存在 / 编码坏 / 权限不足一律静默回落——日志器绝不能成为**启动失败**的原因，
    // 而它跑在 `bootstrap()` 的第一行上。
    fromAgentEnv = {};
  }
  const env = { ...(processEnv || {}), ...fromAgentEnv };
  return {
    maxBytes: firstNumber(env.ARAGON_LOG_MAX_BYTES, DEFAULT_MAX_BYTES),
    keep: firstNumber(env.ARAGON_LOG_KEEP, DEFAULT_KEEP),
  };
}

/** 非负有限数即采纳，否则回落（`"0"` 是合法取值 ⇒ 不轮转，故不能用真值判断）。 */
function firstNumber(raw, fallback) {
  const n = Number(String(raw ?? '').trim());
  return Number.isFinite(n) && n >= 0 && String(raw ?? '').trim() !== '' ? n : fallback;
}

module.exports = { openRotating, resolveLogOptions, DEFAULT_MAX_BYTES, DEFAULT_KEEP };
