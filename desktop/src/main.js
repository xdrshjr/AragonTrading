'use strict';
// Electron 主进程 (spec §4)：单实例锁 → 模式判定（app.isPackaged 唯一真源，P2-5）→ 端口预检
// → 拉起后端(18760)+前端(18761) → 就绪后开窗加载应用 → 退出时优雅终止 + 兜底 tree-kill。
//
// dev：复用仓库 venv 跑 uvicorn --reload、用 Electron 自带 Node 跑 next dev（§11 实施发现）。
// prod：跑 PyInstaller 后端 exe + ELECTRON_RUN_AS_NODE 跑 Next standalone server.js。

const path = require('path');
const fs = require('fs');
const { app, BrowserWindow, dialog, ipcMain, shell, Menu } = require('electron');

const cfg = require('./lib/config');
const paths = require('./lib/paths');
const {
  ProcessManager,
  portFree,
  waitForHttp,
  reclaimPort,
  reapRecordedPids,
  writeRecordedPids,
  removeRecordedPids,
  httpGetText,
} = require('./lib/process-manager');

let win = null;
let pm = null;
let quitting = false;
let enginesReady = false; // 两个子进程均就绪并已开窗后置真，作为「运行期崩溃」判据（P2-4）

// ----------------------------- 日志 -----------------------------
function openLogStreams() {
  // dev：转控制台（返回 null）；prod：写 userData/logs/{backend,web,main}.log
  if (!paths.isPackaged()) return { backend: null, web: null, main: null };
  const dir = paths.ensureLogsDir();
  const open = (name) => fs.createWriteStream(path.join(dir, `${name}.log`), { flags: 'a' });
  return { backend: open('backend'), web: open('web'), main: open('main') };
}

let logStreams = { backend: null, web: null, main: null };
function logMain(msg) {
  const line = `${new Date().toISOString()} ${msg}\n`;
  if (logStreams.main) logStreams.main.write(line);
  else process.stdout.write(`[main] ${msg}\n`);
}

// ----------------------------- 端口自愈 / 子进程留痕 (round-3) -----------------------------
function pidFilePath() {
  return path.join(app.getPath('userData'), 'aragon-pids.json');
}

// 后端 /api/health 应答签名校验：以本应用特有的响应字段为权威判据（R1），
// 避免仅凭通用镜像名 python.exe / node.exe 误杀无关进程。
async function backendHealthMatches() {
  const body = await httpGetText(cfg.BACKEND_HEALTH_URL, 4000);
  if (!body) return false;
  try {
    const j = JSON.parse(body);
    return !!j && typeof j === 'object' && 'okx' in j && 'cachedFallbackEnabled' in j;
  } catch {
    return false;
  }
}

// 端口占用者身份护栏（C/R1）：仅当占用者确属本应用才放行强杀，杜绝「误杀陌生进程」。
// 判据：非通用镜像名 aragon-quant.exe（后端可执行体）∥ 后端端口应答本应用 /api/health 签名。
function makePortOwnerGuard(port) {
  return async (pid, image) => {
    if (image && image.toLowerCase() === 'aragon-quant.exe') return true;
    if (port === cfg.BACKEND_PORT) return backendHealthMatches();
    return false;
  };
}

// 记录本次拉起的子进程 PID + 镜像名 + 端口到 userData/aragon-pids.json，
// 供下次启动在「异常退出（force-kill / 崩溃）未触发 before-quit」时清扫残留（B）。
function persistChildPids() {
  if (!pm) return;
  const children = pm.children
    .filter((c) => c.pid != null)
    .map((c) => ({
      name: c.name,
      pid: c.pid,
      image: path.basename(c.command),
      port: c.name === 'backend' ? cfg.BACKEND_PORT : cfg.WEB_PORT,
    }));
  writeRecordedPids(pidFilePath(), children);
}

// 启动端口就绪保障 (round-3)：先清扫上次异常退出残留子进程（B/R3），再预检 18760/18761；
// 仍被占用者交身份护栏自动回收（A+C）并重检；最终仍被占用才退回提示对话框并请求退出。
// 返回 true=端口已就绪可继续启动；false=已弹窗并 app.quit()，调用方应立即中止。
async function ensurePortsAvailable() {
  // 上次异常退出（force-kill / 崩溃，未触发 before-quit）遗留的本应用子进程：按记录的 PID + 镜像名
  // 校验（R3 防 PID 复用）后清扫，满足「关闭后端口应被释放」（round-3 之一，覆盖异常退出场景）。
  try {
    const reaped = await reapRecordedPids(pidFilePath(), { selfPid: process.pid, log: logMain });
    if (reaped.length) logMain(`已回收上次异常退出残留子进程：${reaped.join(', ')}`);
  } catch (e) {
    logMain(`清扫残留子进程出错（忽略，继续启动）：${e && e.message}`);
  }

  const probeBusyPorts = async () => {
    const busy = [];
    for (const p of [cfg.BACKEND_PORT, cfg.WEB_PORT]) {
      if (!(await portFree(p))) busy.push(p);
    }
    return busy;
  };

  // 自动回收（A+C）：对被占端口定位 PID，身份护栏放行后强杀并轮询至释放——即自动化原弹窗里
  // 要求用户手敲的 Get-NetTCPConnection | Stop-Process（round-3 之二），回收后再重检一次。
  let busy = await probeBusyPorts();
  if (busy.length) {
    for (const p of busy) {
      const res = await reclaimPort(p, { guard: makePortOwnerGuard(p), log: logMain });
      if (res.reclaimed.length) {
        logMain(`端口 ${p} 已自动回收占用进程 ${res.reclaimed.join(', ')} 并释放。`);
      }
    }
    busy = await probeBusyPorts();
  }
  if (!busy.length) return true;

  // 仍被占用（回收失败 / 占用者非本应用且拒绝越权强杀）→ 退回 round-2 的提示对话框，零行为回归。
  dialog.showErrorBox(
    '端口被占用',
    `端口 ${busy.join(', ')} 已被占用，无法启动 Aragon。\n` +
      `请释放后重试：\nPowerShell> Get-NetTCPConnection -LocalPort ${busy.join(',')} | ` +
      `Select OwningProcess ; Stop-Process -Id <PID> -Force`,
  );
  app.quit();
  return false;
}

// ----------------------------- 子进程启动规格 -----------------------------
function backendSpec() {
  const m = paths.mode();
  const env = cfg.backendEnv({ mode: m, dbPath: m === 'prod' ? paths.userDataDbPath() : undefined });
  if (m === 'prod') {
    return {
      name: 'backend',
      command: paths.backendExe(),
      args: ['--port', String(cfg.BACKEND_PORT)],
      cwd: paths.backendCwdProd(),
      env,
      logStream: logStreams.backend,
    };
  }
  // dev：venv python -m uvicorn app.main:app --port 18760 --reload （reload 仅 dev）
  return {
    name: 'backend',
    command: paths.venvPython(),
    args: ['-m', 'uvicorn', 'app.main:app', '--port', String(cfg.BACKEND_PORT), '--reload'],
    cwd: paths.backendCwdDev(),
    env,
    logStream: logStreams.backend,
  };
}

function webSpec() {
  const m = paths.mode();
  const env = cfg.webEnv({ mode: m });
  if (m === 'prod') {
    const { serverJs, cwd } = paths.readWebServerManifest();
    return {
      name: 'web',
      command: process.execPath, // Electron 自带 Node（ELECTRON_RUN_AS_NODE）
      args: [serverJs],
      cwd,
      env: { ...env, ELECTRON_RUN_AS_NODE: '1' },
      logStream: logStreams.web,
    };
  }
  // dev：用 Electron 自带 Node 直接跑 apps/web 的 next 可执行脚本（§11 实施发现：
  // 比 spawn pnpm.cmd 更稳健——规避 Node 对 .cmd 的 spawn 限制与 PATH 依赖，语义等同 next dev）。
  return {
    name: 'web',
    command: process.execPath,
    args: [paths.nextBinDev(), 'dev', '-p', String(cfg.WEB_PORT)],
    cwd: paths.webDirDev(),
    env: { ...env, ELECTRON_RUN_AS_NODE: '1' },
    logStream: logStreams.web,
  };
}

// ----------------------------- 窗口 -----------------------------
function createWindow() {
  // 去系统框架（spec §4.1 / D1）：win/linux 完全无边框、右侧自绘控制键；
  // mac 保留左侧红绿灯并整合进内容区（titleBarStyle:'hidden' + 红绿灯位移）。
  const isMac = process.platform === 'darwin';
  const opts = {
    width: 1440,
    height: 900,
    minWidth: 1024,
    minHeight: 680,
    backgroundColor: '#0b0d12',
    title: 'Aragon Trading',
    show: true,
    frame: false,
    ...(isMac
      ? {
          frame: true,
          titleBarStyle: 'hidden',
          trafficLightPosition: { x: 16, y: 16 },
        }
      : {}),
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
    },
  };
  // 窗口图标（G3，防御性）：dev 期 desktop/build/icon.png 可达即设；
  // 打包后窗口图标由 exe 图标承载（build/ 不入 asar），故 exists 才设、不存在则不设，零回归。
  const iconPng = path.join(__dirname, '..', 'build', 'icon.png');
  if (fs.existsSync(iconPng)) opts.icon = iconPng;

  win = new BrowserWindow(opts);
  // 最大化态变化回推渲染层，驱动「最大化↔还原」图标切换（spec §4.3）。
  const pushMax = () =>
    win && win.webContents.send('aragon:win:maximized-changed', win.isMaximized());
  win.on('maximize', pushMax);
  win.on('unmaximize', pushMax);
  win.on('enter-full-screen', pushMax);
  win.on('leave-full-screen', pushMax);
  // 补偿被移除原生菜单的视图加速键（重载 / DevTools / 缩放 / 全屏，跨平台）。
  wireWindowShortcuts(win);
  // 就绪前先显示内置过渡页，避免白屏 / ERR_CONNECTION_REFUSED 闪现（spec §4.2 step8）。
  win.loadFile(path.join(__dirname, 'loading.html'));
  win.on('closed', () => {
    win = null;
  });
}

// 「关于 Aragon Trading」对话框（G3，§4.3 / §6.3）：版本 / 运行期版本 / appId / 端口 /
// 数据来源诚实声明 / 一键打开日志目录。放在桌面外壳（不碰渲染层），维持 apps/web 零源码改动（D4）。
function showAbout() {
  const v = process.versions;
  const lines = [
    `Aragon Trading  v${app.getVersion()}`,
    '多策略量化模拟交易平台（桌面端）',
    '',
    `Electron ${v.electron}  ·  Node ${v.node}  ·  Chromium ${v.chrome}`,
    'appId: com.aragon.trading',
    `端口：后端 ${cfg.BACKEND_HOST}:${cfg.BACKEND_PORT} / 前端 ${cfg.WEB_HOST}:${cfg.WEB_PORT}`,
    '',
    '数据来源：仅采用 OKX 真实行情；OKX 不可达时显式报错，绝不使用假数据兜底。',
    `日志目录：${paths.logsDir()}`,
  ];
  // 构建 commit 可选——构建期注入了 ARAGON_COMMIT 才附上，否则省略（不强依赖）。
  if (process.env.ARAGON_COMMIT) lines.push(`构建：${process.env.ARAGON_COMMIT}`);

  dialog
    .showMessageBox(win, {
      type: 'info',
      title: '关于 Aragon Trading',
      message: 'Aragon Trading',
      detail: lines.join('\n'),
      buttons: ['打开日志目录', '确定'],
      defaultId: 1,
      cancelId: 1,
    })
    .then(({ response }) => {
      if (response === 0) shell.openPath(paths.ensureLogsDir());
    });
}

// 安装应用菜单（spec §4.2 / D2，按平台分流）：
// - win/linux：彻底移除窗口内菜单栏（用户诉求「不要菜单栏」）。
// - mac：全局菜单不在窗口内，保留最小原生菜单（appMenu 含 Cmd+Q；editMenu 提供
//   Cmd+C/V/X/A/Z；viewMenu 提供重载/缩放/全屏），否则文本框剪贴板与撤销快捷键失效。
// 原菜单的「关于 / 打开日志」改由渲染层 AppMenu 经 IPC 触发；视图加速键由 wireWindowShortcuts 补偿。
function installAppMenu() {
  if (process.platform !== 'darwin') {
    Menu.setApplicationMenu(null);
    return;
  }
  Menu.setApplicationMenu(
    Menu.buildFromTemplate([{ role: 'appMenu' }, { role: 'editMenu' }, { role: 'viewMenu' }]),
  );
}

// 窗口级键盘加速键补偿（spec §4.2）：补回被移除视图菜单的重载 / 强制重载 / DevTools /
// 缩放 / 全屏，跨平台经 before-input-event 接线（mac 即便有 viewMenu 亦保留，行为幂等）。
function wireWindowShortcuts(targetWin) {
  targetWin.webContents.on('before-input-event', (event, input) => {
    if (input.type !== 'keyDown') return;
    const ctrl = input.control || input.meta;
    const key = (input.key || '').toLowerCase();
    const match = () => {
      if (ctrl && input.shift && key === 'r')
        return targetWin.webContents.reloadIgnoringCache(), true;
      if (ctrl && key === 'r') return targetWin.webContents.reload(), true;
      if (key === 'f12' || (ctrl && input.shift && key === 'i'))
        return targetWin.webContents.toggleDevTools(), true;
      if (key === 'f11')
        return targetWin.setFullScreen(!targetWin.isFullScreen()), true;
      if (ctrl && (key === '=' || key === '+')) return bumpZoom(targetWin, +0.5), true;
      if (ctrl && key === '-') return bumpZoom(targetWin, -0.5), true;
      if (ctrl && key === '0') return targetWin.webContents.setZoomLevel(0), true;
      return false;
    };
    if (match()) event.preventDefault();
  });
}

function bumpZoom(w, d) {
  w.webContents.setZoomLevel(w.webContents.getZoomLevel() + d);
}

// ----------------------------- 启动编排 -----------------------------
async function startEngines() {
  pm = new ProcessManager({
    onUnexpectedExit: (info) => {
      // 仅在两个进程都已就绪、窗口已加载应用后的非预期退出才提示（P2-4）。
      // 启动期（含 next dev 冷编译 / onedir 解压，可达数十秒）的失败由 startEngines 的
      // 「启动失败」对话框承接，故此处用就绪标志而非时间启发，避免启动期崩溃弹双重对话框。
      if (!enginesReady) return;
      const where = paths.isPackaged() ? paths.logsDir() : '控制台';
      dialog.showErrorBox(
        'Aragon 引擎已停止',
        `${info.name === 'backend' ? '后端' : '前端'}进程意外退出（code=${info.code}）。\n` +
          `请查看日志：${where}`,
      );
    },
  });

  // 后端
  logMain('启动后端 …');
  // 捕获受管子进程：就绪轮询期间一旦后端早退（如绑定端口失败 Errno 10048），立刻中止轮询并判失败，
  // 避免占住同端口的孤儿进程用其 health 200 冒充「就绪」（见 desktop-backend-port-bind-conflict；
  // 与 verify-dist.mjs 的 earlyExit 守卫一致）。
  const backend = pm.startChild(backendSpec());
  persistChildPids(); // 尽早留痕：若启动期被 force-kill，下次启动据此清扫后端孤儿
  const backendOk = await waitForHttp(cfg.BACKEND_HEALTH_URL, cfg.BACKEND_READY_TIMEOUT_MS, {
    isAborted: () => quitting || backend.exited,
  });
  if (!backendOk) {
    if (quitting) return false;
    dialog.showErrorBox(
      '后端启动失败',
      `后端在 ${cfg.BACKEND_READY_TIMEOUT_MS / 1000}s 内未就绪（${cfg.BACKEND_HEALTH_URL}）。\n` +
        (paths.isPackaged()
          ? `请查看日志：${paths.logsDir()}`
          : '请确认已在仓库根执行 `pnpm setup`（建 venv + 装后端依赖）。'),
    );
    return false;
  }
  logMain('后端就绪。');

  // 前端
  logMain('启动前端 …');
  // 同后端：受管前端早退即中止就绪轮询，杜绝被 `::`/双栈孤儿监听者冒充就绪。
  const web = pm.startChild(webSpec());
  persistChildPids(); // 两个子进程齐备后再次留痕（含前端 PID）
  const webOk = await waitForHttp(cfg.WEB_URL, cfg.WEB_READY_TIMEOUT_MS, {
    isAborted: () => quitting || web.exited,
  });
  if (!webOk) {
    if (quitting) return false;
    dialog.showErrorBox(
      '前端启动失败',
      `前端在 ${cfg.WEB_READY_TIMEOUT_MS / 1000}s 内未就绪（${cfg.WEB_URL}）。\n` +
        (paths.isPackaged()
          ? `请查看日志：${paths.logsDir()}`
          : '请确认已在 apps/web 执行 `pnpm install`。'),
    );
    return false;
  }
  logMain('前端就绪。');
  return true;
}

async function bootstrap() {
  logStreams = openLogStreams();
  logMain(`Aragon Trading 桌面端启动（mode=${paths.mode()}, isPackaged=${paths.isPackaged()}）`);

  // venv 缺失（dev）→ 明确指引
  if (!paths.isPackaged() && !fs.existsSync(paths.venvPython())) {
    dialog.showErrorBox(
      '缺少 Python 虚拟环境',
      `未找到 ${paths.venvPython()}\n请先在仓库根执行 \`pnpm setup\`（或 \`python scripts/dev.py setup\`）创建 venv 并安装后端依赖。`,
    );
    app.quit();
    return;
  }

  // 端口就绪保障：清扫上次异常退出残留 → 预检 → 自动回收占用 → 仍占用则弹窗退出（round-3）。
  if (!(await ensurePortsAvailable())) return;

  installAppMenu();
  createWindow();

  const ok = await startEngines();
  if (!ok) {
    app.quit();
    return;
  }
  if (win) {
    win.loadURL(cfg.WEB_URL);
    // 模式真源是 app.isPackaged；ARAGON_MODE=dev 仅用于强制开 DevTools。
    if (!paths.isPackaged() || process.env.ARAGON_MODE === 'dev') {
      win.webContents.openDevTools({ mode: 'right' });
    }
  }
  // 至此前后端均已就绪、窗口已加载应用；此后子进程的非预期退出才按「运行期崩溃」弹窗（P2-4）。
  enginesReady = true;
}

// ----------------------------- 退出与清理 (spec §4.3) -----------------------------
async function gracefulQuit() {
  if (quitting) return;
  quitting = true;
  if (pm) {
    logMain('收到退出信号，正在停止前后端 …');
    await pm.stop();
    // 兜底端口清扫（R4）：pm.stop() 仅按 PID 树杀受管子进程；若仍有端口被占（逃逸的孙子进程），
    // 按身份护栏回收，确保「关闭后端口被释放」（round-3 之一，覆盖正常退出场景）。
    for (const p of [cfg.BACKEND_PORT, cfg.WEB_PORT]) {
      if (!(await portFree(p))) {
        const res = await reclaimPort(p, { guard: makePortOwnerGuard(p), log: logMain });
        if (res.reclaimed.length) logMain(`退出清扫：端口 ${p} 回收 ${res.reclaimed.join(', ')}。`);
      }
    }
    logMain('前后端已停止。');
  }
  // 正常退出已回收自身子进程：清除留痕，避免下次启动误清已不存在 / 被复用的 PID。
  removeRecordedPids(pidFilePath());
}

// ----------------------------- 生命周期接线 -----------------------------
const gotLock = app.requestSingleInstanceLock();
if (!gotLock) {
  app.quit();
} else {
  app.on('second-instance', () => {
    if (win) {
      if (win.isMinimized()) win.restore();
      win.focus();
    }
  });

  // IPC（preload 暴露的最小只读 API）
  ipcMain.on('aragon:get-version', (e) => {
    e.returnValue = app.getVersion();
  });
  ipcMain.handle('aragon:open-logs', () => shell.openPath(paths.ensureLogsDir()));

  // 窗口控制 IPC（spec §4.3 / §6）：自定义标题栏的最小化 / 最大化切换 / 关闭 /
  // 查询最大化态 + 触发既有「关于」对话框。关闭仅 win.close()，落既有退出清理链（D3）。
  ipcMain.on('aragon:win:minimize', () => win && win.minimize());
  ipcMain.on('aragon:win:maximize-toggle', () => {
    if (!win) return;
    win.isMaximized() ? win.unmaximize() : win.maximize();
  });
  ipcMain.on('aragon:win:close', () => win && win.close());
  ipcMain.handle('aragon:win:is-maximized', () => !!win && win.isMaximized());
  ipcMain.on('aragon:show-about', () => showAbout());

  app.whenReady().then(bootstrap);

  app.on('window-all-closed', () => {
    if (process.platform !== 'darwin') app.quit();
  });

  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0 && !quitting) createWindow();
  });

  // before-quit：先把子进程优雅终止 + 兜底强杀，再真正退出（确保 stop 在进程退出前完成）。
  app.on('before-quit', (e) => {
    if (quitting && pm && pm.stopped) return; // 清理已完成，放行退出
    e.preventDefault();
    gracefulQuit().finally(() => app.exit(0));
  });

  // 主进程未捕获异常：尽力清理子进程，避免孤儿占端口。
  process.on('uncaughtException', (err) => {
    logMain(`uncaughtException: ${err && err.stack ? err.stack : err}`);
    gracefulQuit().finally(() => app.exit(1));
  });
}
