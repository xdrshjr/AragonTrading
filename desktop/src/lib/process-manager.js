'use strict';
// 子进程 spawn / HTTP 就绪轮询 / 优雅终止 + 兜底 tree-kill (spec §4.2 / §4.3)。
// 复刻 scripts/dev.py 已验证的「先优雅 → 超时强杀整棵进程树」策略，杜绝 uvicorn --reload /
// next dev 的工作子进程变孤儿占住 18760/18761（否则 Windows 再启即 WinError 10013）。
//
// 运行期零 npm 依赖（P1-3 / C3）：仅用 Electron + Node 内置模块（child_process / http / net / fs）。

const { spawn, execFile } = require('child_process');
const http = require('http');
const net = require('net');
const fs = require('fs');

const IS_WIN = process.platform === 'win32';

function delay(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

/** 单地址 bind 探测：能 listen 即空闲，探测后立即释放（同 dev.py）。host=null 走 net 默认（通配 :: / 0.0.0.0）。 */
function tryBind(port, host) {
  return new Promise((resolve) => {
    const srv = net.createServer();
    srv.once('error', () => resolve(false));
    srv.once('listening', () => srv.close(() => resolve(true)));
    if (host) srv.listen(port, host);
    else srv.listen(port);
  });
}

/**
 * 无害的端口占用预检：未被占用（可绑）才返回 true。
 *
 * 显式传 host 时只探该地址；缺省（host=null）依次探两类地址，两者都可绑才算空闲：
 *   1) 127.0.0.1：后端/前端实际绑定的 IPv4 回环地址。Windows 上通配 bind 不会与已被独占的
 *      127.0.0.1:port 冲突，故必须按真实 host 探测才能发现回环独占的占用者，否则预检漏检导致
 *      启动时 Errno 10048（见 docs/diagnoses/desktop-backend-port-bind-conflict）。
 *   2) 通配（:: / 0.0.0.0）：捕获 IPv6 `::` / 双栈上的残留监听者（见
 *      docs/diagnoses/desktop-packaged-static-assets：stale next start 监听 `::` 被回环探测漏掉）。
 * 两次探测**串行**执行，避免双栈系统上 `::`(dual-stack) 与 127.0.0.1 互相占用导致的误报。
 */
function portFree(port, host = null) {
  if (host) return tryBind(port, host);
  return tryBind(port, '127.0.0.1').then(
    (ok) => (ok ? tryBind(port, null) : false),
  );
}

/** 单次 HTTP 探测：拿到任意响应即视为「在听」（含 4xx/5xx）。自带短超时，避免被慢响应拖死。 */
function probeHttp(url, perTryTimeoutMs = 4000) {
  return new Promise((resolve) => {
    let settled = false;
    const done = (ok) => {
      if (!settled) {
        settled = true;
        resolve(ok);
      }
    };
    const req = http.get(url, (res) => {
      res.resume(); // 排空响应体，释放 socket
      done(true);
    });
    req.setTimeout(perTryTimeoutMs, () => {
      req.destroy();
      done(false);
    });
    req.on('error', () => done(false));
  });
}

/** 轮询直到「在听」或超时。ready = 拿到任意 HTTP 响应（监听即就绪）。 */
async function waitForHttp(url, timeoutMs, { intervalMs = 600, isAborted } = {}) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (typeof isAborted === 'function' && isAborted()) return false;
    if (await probeHttp(url)) return true;
    await delay(intervalMs);
  }
  return false;
}

/** 单次 HTTP GET 取响应体文本；任何错误/超时返回 null。用于占用者身份签名校验（不抛）。 */
function httpGetText(url, perTryTimeoutMs = 4000) {
  return new Promise((resolve) => {
    let settled = false;
    const done = (v) => {
      if (!settled) {
        settled = true;
        resolve(v);
      }
    };
    const req = http.get(url, (res) => {
      const chunks = [];
      res.on('data', (d) => chunks.push(d));
      res.on('end', () => done(Buffer.concat(chunks).toString('utf-8')));
      res.on('error', () => done(null));
    });
    req.setTimeout(perTryTimeoutMs, () => {
      req.destroy();
      done(null);
    });
    req.on('error', () => done(null));
  });
}

/**
 * 单次 HTTP POST（JSON），**任何失败一律返回 null 且绝不抛**（ai-trade-pause-resume §3.8）。
 *
 * 唯一调用方是退出前的 `quit-pause` 钩子：它是**纯优化**，正确性由后端的启动对账兜底
 * （强杀 / 崩溃 / 断电时它根本不会被执行）。故这里的每一条错误路径都必须静默降级——一个
 * best-effort 的钩子绝不能让用户点了「关闭」之后卡住或看到报错。
 */
function postJsonSafe(url, body, timeoutMs = 2000) {
  return new Promise((resolve) => {
    let settled = false;
    const done = (v) => {
      if (!settled) {
        settled = true;
        resolve(v);
      }
    };
    const payload = Buffer.from(JSON.stringify(body ?? {}), 'utf-8');
    let req;
    try {
      req = http.request(
        url,
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', 'Content-Length': payload.length },
        },
        (res) => {
          const chunks = [];
          res.on('data', (d) => chunks.push(d));
          res.on('end', () => done(Buffer.concat(chunks).toString('utf-8')));
          res.on('error', () => done(null));
        },
      );
    } catch {
      return done(null); // 非法 URL 等同步抛错：同样静默降级
    }
    req.setTimeout(timeoutMs, () => {
      req.destroy();
      done(null);
    });
    req.on('error', () => done(null));
    req.end(payload);
  });
}

// ===================== 端口自愈：定位占用者 → 身份护栏 → 强杀 → 轮询释放 =====================
// round-3：自动化弹窗里原本要求用户手敲的 Get-NetTCPConnection | Stop-Process（见 main.js 端口预检）。
// 仅用内置模块 + 系统自带命令（PowerShell / netstat / tasklist / lsof），维持运行期零 npm 依赖。

/** 跑一条命令并捕获 stdout；带超时，永不 reject（失败返回 ok=false）。 */
function runCmd(cmd, args, timeoutMs = 5000) {
  return new Promise((resolve) => {
    execFile(
      cmd,
      args,
      { timeout: timeoutMs, windowsHide: true, maxBuffer: 1 << 20 },
      (err, stdout) => resolve({ ok: !err, stdout: stdout || '' }),
    );
  });
}

/** 从多行文本里解析出正整数 PID（去重）。 */
function parsePidLines(text) {
  const out = new Set();
  for (const line of String(text).split(/\r?\n/)) {
    const n = Number(line.trim());
    if (Number.isInteger(n) && n > 0) out.add(n);
  }
  return [...out];
}

/** Windows：Get-NetTCPConnection 反查 LISTEN 该端口的 OwningProcess（主路径，analysis 推荐）。 */
async function winListenerPidsViaPowershell(port) {
  const script =
    `Get-NetTCPConnection -LocalPort ${port} -State Listen ` +
    '| Select-Object -ExpandProperty OwningProcess';
  const { ok, stdout } = await runCmd('powershell', [
    '-NoProfile', '-NonInteractive', '-Command', script,
  ]);
  return ok ? parsePidLines(stdout) : [];
}

/** Windows：netstat -ano 兜底解析 LISTENING 行的 PID（PowerShell 不可用 / 无返回时）。 */
async function winListenerPidsViaNetstat(port) {
  const { stdout } = await runCmd('netstat', ['-ano']);
  const pids = new Set();
  const needle = `:${port}`;
  for (const line of stdout.split(/\r?\n/)) {
    const parts = line.trim().split(/\s+/);
    if (parts.length < 5 || !/^TCP/i.test(parts[0])) continue;
    if (parts[3].toUpperCase() !== 'LISTENING' || !parts[1].endsWith(needle)) continue;
    const n = Number(parts[4]);
    if (Number.isInteger(n) && n > 0) pids.add(n);
  }
  return [...pids];
}

/** 解析监听指定端口的进程 PID 列表（Win：PowerShell→netstat 兜底；POSIX：lsof）。 */
async function resolveListenerPids(port) {
  if (IS_WIN) {
    const pids = await winListenerPidsViaPowershell(port);
    return pids.length ? pids : winListenerPidsViaNetstat(port);
  }
  const { stdout } = await runCmd('lsof', ['-ti', `tcp:${port}`, '-sTCP:LISTEN']);
  return parsePidLines(stdout);
}

/** 取 PID 当前镜像名（Win：tasklist CSV；POSIX：ps comm）；进程不存在返回 null。 */
async function processImageName(pid) {
  if (IS_WIN) {
    const { ok, stdout } = await runCmd('tasklist', [
      '/FI', `PID eq ${pid}`, '/NH', '/FO', 'CSV',
    ]);
    const m = ok && stdout.match(/^"([^"]+)"/m);
    return m ? m[1] : null;
  }
  const { ok, stdout } = await runCmd('ps', ['-p', String(pid), '-o', 'comm=']);
  const name = ok ? stdout.trim() : '';
  return name ? name.split('/').pop() : null;
}

/** PID 是否存活（信号 0 仅探测存在性；EPERM 视为存活）。 */
function isPidAlive(pid) {
  try {
    process.kill(pid, 0);
    return true;
  } catch (e) {
    return !!e && e.code === 'EPERM';
  }
}

/** 强杀整棵进程树并 await 完成（Win：taskkill /F /T；POSIX：SIGKILL）。 */
async function killPidTree(pid) {
  if (IS_WIN) {
    await runCmd('taskkill', ['/F', '/T', '/PID', String(pid)]);
    return;
  }
  try {
    process.kill(pid, 'SIGKILL');
  } catch {
    /* 进程可能已退出 */
  }
}

/** 轮询直到端口可绑（已释放）或超时。taskkill 返回后 socket 未必立即释放（R2 TOCTOU）。 */
async function waitPortFree(port, timeoutMs = 2500, intervalMs = 150) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (await portFree(port)) return true;
    await delay(intervalMs);
  }
  return portFree(port);
}

/**
 * 回收被占端口：定位监听 PID → 逐个交身份护栏 guard 判定 → 放行者强杀 → 轮询至端口释放。
 * guard(pid, image, port) => Promise<boolean>：仅返回 true（确属本应用）才强杀，否则跳过不越权。
 * @returns {Promise<{reclaimed:number[], free:boolean}>}
 */
async function reclaimPort(port, { guard, log = () => {} } = {}) {
  const pids = await resolveListenerPids(port);
  if (!pids.length) return { reclaimed: [], free: await portFree(port) };
  const reclaimed = [];
  for (const pid of pids) {
    const image = await processImageName(pid);
    let ours = false;
    try {
      ours = await guard(pid, image, port);
    } catch {
      ours = false;
    }
    if (!ours) {
      log(`端口 ${port} 占用者 PID ${pid}（${image || '未知'}）未通过身份校验，跳过、不强杀。`);
      continue;
    }
    await killPidTree(pid);
    reclaimed.push(pid);
    log(`端口 ${port} 占用者 PID ${pid}（${image || '未知'}）通过身份校验，已强杀回收。`);
  }
  return { reclaimed, free: await waitPortFree(port) };
}

// ===================== 子进程留痕（pid-file）：异常退出残留的下次启动清扫 =====================
// before-quit 在 force-kill / 崩溃时不触发，其子进程会变孤儿占住端口；故把本次拉起的子进程
// PID + 镜像名 + 端口持久化到 userData/aragon-pids.json，供下次启动按身份校验清扫（B / R3）。

/** 读取留痕的子进程记录数组；文件不存在 / 损坏均视为无残留（返回 []）。 */
function readRecordedPids(filePath) {
  try {
    const data = JSON.parse(fs.readFileSync(filePath, 'utf-8'));
    return Array.isArray(data.children) ? data.children : [];
  } catch {
    return [];
  }
}

/** 写入本次子进程留痕；写失败仅丧失下次清扫线索，不应阻断启动（吞错）。 */
function writeRecordedPids(filePath, children) {
  try {
    fs.writeFileSync(filePath, JSON.stringify({ pid: process.pid, children }, null, 2), 'utf-8');
  } catch {
    /* 留痕失败不阻断启动 */
  }
}

/** 删除留痕文件（正常退出后调用，避免下次误清已不存在 / 被复用的 PID）。 */
function removeRecordedPids(filePath) {
  try {
    fs.unlinkSync(filePath);
  } catch {
    /* 不存在即可 */
  }
}

/**
 * 清扫上次异常退出残留的本应用子进程：仅当记录 PID 仍存活、且当前镜像名仍与记录一致才强杀整树
 * （R3 防 PID 复用：被回收的 PID 须恰好复用到同名镜像才会误判，概率极低且仍属本应用同类进程）。
 * @returns {Promise<number[]>} 实际回收的 PID 列表
 */
async function reapRecordedPids(filePath, { selfPid, log = () => {} } = {}) {
  const reaped = [];
  for (const c of readRecordedPids(filePath)) {
    const pid = Number(c && c.pid);
    if (!Number.isInteger(pid) || pid <= 0 || pid === selfPid || !isPidAlive(pid)) continue;
    const image = await processImageName(pid);
    if (!image || !c.image || image.toLowerCase() !== String(c.image).toLowerCase()) {
      log(`pid-file 记录 PID ${pid} 现为 ${image || '未知'}（期望 ${c.image}），疑似 PID 复用，跳过。`);
      continue;
    }
    await killPidTree(pid);
    reaped.push(pid);
    log(`pid-file 残留子进程 ${c.name || ''} PID ${pid}（${image}）已回收。`);
  }
  return reaped;
}

class ManagedChild {
  /**
   * @param {object} o
   * @param {string} o.name 'backend' | 'web'
   * @param {string} o.command 可执行体绝对路径
   * @param {string[]} o.args
   * @param {string} o.cwd
   * @param {NodeJS.ProcessEnv} o.env
   * @param {import('fs').WriteStream|null} o.logStream prod 写文件，dev 传 null（转控制台）
   */
  constructor({ name, command, args, cwd, env, logStream }) {
    this.name = name;
    this.command = command;
    this.args = args;
    this.cwd = cwd;
    this.env = env;
    this.logStream = logStream || null;
    this.proc = null;
    this.ready = false;
    this.exited = false;
    this.onUnexpectedExit = null; // (info) => void
  }

  start() {
    const opts = {
      cwd: this.cwd,
      env: this.env,
      windowsHide: true,
      stdio: ['ignore', 'pipe', 'pipe'],
    };
    // POSIX：start_new_session → 子进程自成进程组，便于 killpg 整组（同 dev.py）。
    // Windows：不 detached（避免独立控制台窗口），清理走 taskkill /T 按 PID 树强杀，无需进程组。
    if (!IS_WIN) opts.detached = true;

    this.proc = spawn(this.command, this.args, opts);

    const write = (chunk) => {
      const text = chunk.toString('utf-8');
      if (this.logStream) {
        this.logStream.write(text);
      } else {
        process.stdout.write(`[${this.name}] ${text}`);
      }
    };
    if (this.proc.stdout) this.proc.stdout.on('data', write);
    if (this.proc.stderr) this.proc.stderr.on('data', write);

    this.proc.on('exit', (code, signal) => {
      this.exited = true;
      const line = `[process-manager] ${this.name} exited (code=${code}, signal=${signal})\n`;
      if (this.logStream) this.logStream.write(line);
      else process.stdout.write(line);
      if (this.onUnexpectedExit) this.onUnexpectedExit({ name: this.name, code, signal });
    });

    this.proc.on('error', (err) => {
      const line = `[process-manager] ${this.name} spawn error: ${err && err.message}\n`;
      if (this.logStream) this.logStream.write(line);
      else process.stderr.write(line);
    });

    return this.proc;
  }

  get pid() {
    return this.proc ? this.proc.pid : null;
  }

  /** 优雅终止整组（Win：taskkill /T 无 /F；POSIX：SIGTERM 给进程组）。 */
  _gracefulStop() {
    if (!this.proc || this.exited || this.pid == null) return;
    try {
      if (IS_WIN) {
        spawn('taskkill', ['/PID', String(this.pid), '/T'], { windowsHide: true });
      } else {
        process.kill(-this.pid, 'SIGTERM');
      }
    } catch {
      /* 进程可能已退出 */
    }
  }

  /** 兜底强杀整棵进程树（Win：taskkill /F /T；POSIX：killpg SIGKILL）。 */
  _forceKill() {
    if (!this.proc || this.exited || this.pid == null) return;
    try {
      if (IS_WIN) {
        spawn('taskkill', ['/F', '/T', '/PID', String(this.pid)], { windowsHide: true });
      } else {
        process.kill(-this.pid, 'SIGKILL');
      }
    } catch {
      /* 进程可能已退出 */
    }
  }
}

class ProcessManager {
  constructor({ onUnexpectedExit } = {}) {
    /** @type {ManagedChild[]} */
    this.children = [];
    this.isQuitting = false; // 退出流程中：子进程退出属预期，不弹崩溃对话框（P2-4）
    this.stopped = false;
    this.onUnexpectedExit = onUnexpectedExit || null;
  }

  /** 创建并启动一个受管子进程；返回 ManagedChild。 */
  startChild(spec) {
    const child = new ManagedChild(spec);
    child.onUnexpectedExit = (info) => {
      if (this.isQuitting || this.stopped) return; // 退出期的退出是预期的
      if (this.onUnexpectedExit) this.onUnexpectedExit(info);
    };
    this.children.push(child);
    child.start();
    return child;
  }

  /**
   * 优雅 → 兜底 停止所有子进程（spec §4.3）。
   * 先对全部子进程发优雅终止，统一等至多 graceMs；仍存活者强杀整棵树。
   */
  async stop(graceMs = 8000) {
    if (this.stopped) return;
    this.stopped = true;
    this.isQuitting = true;

    // 先优雅（与 dev.py 一致：web 在前、api 在后由调用方决定 children 顺序）
    for (const c of this.children) c._gracefulStop();

    const deadline = Date.now() + graceMs;
    while (Date.now() < deadline) {
      if (this.children.every((c) => c.exited)) break;
      await delay(200);
    }

    // 兜底强杀
    for (const c of this.children) {
      if (!c.exited) c._forceKill();
    }
    // 给 taskkill / killpg 一点收敛时间
    await delay(300);
  }
}

module.exports = {
  ProcessManager,
  ManagedChild,
  portFree,
  probeHttp,
  waitForHttp,
  delay,
  httpGetText,
  postJsonSafe,
  reclaimPort,
  reapRecordedPids,
  writeRecordedPids,
  removeRecordedPids,
};
