'use strict';
// 子进程 spawn / HTTP 就绪轮询 / 优雅终止 + 兜底 tree-kill (spec §4.2 / §4.3)。
// 复刻 scripts/dev.py 已验证的「先优雅 → 超时强杀整棵进程树」策略，杜绝 uvicorn --reload /
// next dev 的工作子进程变孤儿占住 18760/18761（否则 Windows 再启即 WinError 10013）。
//
// 运行期零 npm 依赖（P1-3 / C3）：仅用 Electron + Node 内置模块（child_process / http / net / fs）。

const { spawn } = require('child_process');
const http = require('http');
const net = require('net');

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
};
