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
const { ProcessManager, portFree, waitForHttp } = require('./lib/process-manager');

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
  const opts = {
    width: 1440,
    height: 900,
    minWidth: 1024,
    minHeight: 680,
    backgroundColor: '#0b0d12',
    title: 'Aragon Trading',
    show: true,
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

function buildMenu() {
  const isMac = process.platform === 'darwin';
  const template = [
    ...(isMac ? [{ role: 'appMenu' }] : []),
    { role: 'fileMenu' },
    { role: 'editMenu' },
    {
      label: '视图',
      submenu: [
        { role: 'reload' },
        { role: 'forceReload' },
        { role: 'toggleDevTools' },
        { type: 'separator' },
        { role: 'resetZoom' },
        { role: 'zoomIn' },
        { role: 'zoomOut' },
        { type: 'separator' },
        { role: 'togglefullscreen' },
      ],
    },
    {
      label: '帮助',
      submenu: [
        {
          label: '关于 Aragon Trading',
          click: showAbout,
        },
        {
          label: '打开日志目录',
          click: () => shell.openPath(paths.ensureLogsDir()),
        },
      ],
    },
  ];
  Menu.setApplicationMenu(Menu.buildFromTemplate(template));
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
  pm.startChild(backendSpec());
  const backendOk = await waitForHttp(cfg.BACKEND_HEALTH_URL, cfg.BACKEND_READY_TIMEOUT_MS, {
    isAborted: () => quitting,
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
  pm.startChild(webSpec());
  const webOk = await waitForHttp(cfg.WEB_URL, cfg.WEB_READY_TIMEOUT_MS, {
    isAborted: () => quitting,
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

  // 端口预检（18760 / 18761）
  const busy = [];
  for (const p of [cfg.BACKEND_PORT, cfg.WEB_PORT]) {
    if (!(await portFree(p))) busy.push(p);
  }
  if (busy.length) {
    dialog.showErrorBox(
      '端口被占用',
      `端口 ${busy.join(', ')} 已被占用，无法启动 Aragon。\n` +
        `请释放后重试：\nPowerShell> Get-NetTCPConnection -LocalPort ${busy.join(',')} | ` +
        `Select OwningProcess ; Stop-Process -Id <PID> -Force`,
    );
    app.quit();
    return;
  }

  buildMenu();
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
    logMain('前后端已停止。');
  }
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
