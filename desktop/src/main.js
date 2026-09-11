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
const agentEnvLib = require('./lib/agent-env');
const { openRotating, resolveLogOptions } = require('./lib/rotating-log');
const {
  describeRendererGone,
  createReloadBudget,
  describeLoadFailure,
} = require('./lib/renderer-health');
const { wireExternalLinks } = require('./lib/external-links');
const dirAccess = require('./lib/dir-access');
const { describeStartupFailure } = require('./lib/startup-failure');
const {
  ProcessManager,
  portFree,
  waitForHttp,
  reclaimPort,
  reapRecordedPids,
  writeRecordedPids,
  removeRecordedPids,
  httpGetText,
  postJsonSafe,
} = require('./lib/process-manager');

let win = null;
let pm = null;
let quitting = false;
let enginesReady = false; // 两个子进程均就绪并已开窗后置真，作为「运行期崩溃」判据（P2-4）
// G1（诊断 desktop-packaged-wco-header-right-still-unclickable）：渲染层经 IPC 回传的 Window Controls
// Overlay 运行期状态；供「关于」展示「WCO 是否真接管 caption」，把不可观测的命中托管变为可读事实。
let lastOverlayState = null;

// ----------------------------- 日志 -----------------------------
// 轴 D-1（ai-trade-silent-degradation-repair）：日志参数的取值链是**三级回落**，不是只读
// `process.env`。`openLogStreams()` 跑在 `bootstrap()` 的第一行，而 `agentEnv` 要到后端子进程
// 装配时才被读（:backendSpec）；一个从开始菜单 / 桌面图标启动的 Electron 进程，`process.env`
// 里**不会有**任何用户设的键——只读 `process.env` 的话，「ARAGON_LOG_MAX_BYTES=0 ⇒ 逐字节回到
// 今天」这句承诺在唯一的产品形态里是空的（评审 P0-5）。
function openLogStreams() {
  // dev：转控制台（返回 null）；prod：写 userData/logs/{backend,web,main}.log（带上限轮转）
  if (!paths.isPackaged()) return { backend: null, web: null, main: null };
  const dir = paths.ensureLogsDir();
  const opts = resolveLogOptions(agentEnvLib.loadAgentEnv);
  const open = (name) => openRotating(dir, name, opts);
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
  // prod：首启播种 userData/agent.env，解析 AGENT_* 注入后端子进程（dev 经 services/quant/.env 读取）。
  let agentEnv;
  if (m === 'prod') {
    try {
      agentEnvLib.seedUserAgentEnv();
      agentEnv = agentEnvLib.loadAgentEnv();
    } catch (e) {
      logMain(`加载 Agent 凭证出错（忽略，Agent 将提示未配置）：${e && e.message}`);
    }
  }
  const env = cfg.backendEnv({
    mode: m,
    dbPath: m === 'prod' ? paths.userDataDbPath() : undefined,
    agentEnv,
  });
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

// Windows 系统标题栏控件叠加（titleBarOverlay）配色：color=顶栏背景(--bg)、symbolColor=三键字形(--text)，
// 与 apps/web/app/globals.css 的明/暗 token 对齐；高度等于顶栏 h-12=48px。
// 主题切换时由渲染层经 aragon:win:set-overlay-theme 调 win.setTitleBarOverlay 同步（避免三键配色突兀）。
const TITLEBAR_OVERLAY = {
  dark: { color: '#1a1915', symbolColor: '#ece8df' },
  light: { color: '#f7f5f2', symbolColor: '#1f1e1b' },
};
const TITLEBAR_OVERLAY_HEIGHT = 48;
function overlayForTheme(theme) {
  const c = theme === 'light' ? TITLEBAR_OVERLAY.light : TITLEBAR_OVERLAY.dark;
  return { color: c.color, symbolColor: c.symbolColor, height: TITLEBAR_OVERLAY_HEIGHT };
}

// ----------------------------- 窗口 -----------------------------
function createWindow() {
  // 标题栏按平台分流（诊断 desktop-header-right-and-live-toggle-unclickable）：
  //   win  → 系统托管标题栏 + Controls Overlay（titleBarStyle:'hidden' + titleBarOverlay）。caption 命中
  //          交还系统，根治「无边框(frame:false)+默认可缩放」在还原态把顶栏右簇（模拟盘/实盘、主题、
  //          设置、窗口键）吞为 caption 致单击不下发的缺陷（三轮 -webkit-app-region CSS 补丁均未根治）。
  //   mac  → 隐藏标题栏 + 系统左侧红绿灯（titleBarStyle:'hidden' + 红绿灯位移）。
  //   linux→ 完全无边框、右侧自绘控制键（titleBarOverlay 在 Linux 支持不全，维持原路径不动）。
  const isMac = process.platform === 'darwin';
  const isWin = process.platform === 'win32';
  const opts = {
    width: 1440,
    height: 900,
    // 两列地板 = 左栏 220 + 右策略 380 + 图表可用 ≈260 = 860；放开到 860 让窗口可收窄至
    // 1366×768@150%(≈910 DIP) 等高 DPI/小屏内，避免右侧策略列被顶出屏幕（见 workstation-layout 诊断）。
    minWidth: 860,
    minHeight: 680,
    backgroundColor: '#0b0d12',
    title: 'Aragon Trading',
    show: true,
    ...(isWin
      ? {
          // 初始用暗色叠加匹配 loading.html / backgroundColor；渲染层挂载后据实际主题再同步。
          titleBarStyle: 'hidden',
          titleBarOverlay: overlayForTheme('dark'),
        }
      : isMac
        ? {
            frame: true,
            titleBarStyle: 'hidden',
            trafficLightPosition: { x: 16, y: 16 },
          }
        : { frame: false }),
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
  // 渲染进程崩溃 / 无响应 / 加载失败的兜底（spec D5）。
  wireRendererHealth(win);
  // 外链交给系统浏览器（market-intel §3.9 / G-10）：情报的来源链接是本仓库第一次需要外链，
  // 不接这一行，打包后点击来源会开出一个无外壳的 Electron 窗口，且 will-navigate 可被劫持。
  wireExternalLinks(win, { shell, log: logMain });
  // 就绪前先显示内置过渡页，避免白屏 / ERR_CONNECTION_REFUSED 闪现（spec §4.2 step8）。
  win.loadFile(path.join(__dirname, 'loading.html'));
  win.on('closed', () => {
    win = null;
  });
}

// 不再自动重载时的最后一道出口：说清发生了什么，并给三条出路（重载 / 看日志 / 退出）。
// 抽成独立函数是为了让 wireRendererHealth 留在 50 行阈值内（CLAUDE.md §二）。
function promptRendererGone(targetWin, info, isLive) {
  dialog
    .showMessageBox(targetWin, {
      type: 'error',
      title: '界面进程已退出',
      message: info.message,
      detail: '已停止自动重载以免反复崩溃。后端与已落库数据不受影响。',
      buttons: ['重新载入', '打开日志', '退出'],
      defaultId: 0,
      cancelId: 2,
    })
    .then(({ response }) => {
      if (!isLive()) return; // 用户可能在对话框开着时就退出了应用
      if (response === 0) targetWin.reload();
      else if (response === 1) shell.openPath(paths.ensureLogsDir());
      else targetWin.close();
    })
    // 这一笔 catch 不是防御性噪音：主进程的未处理 Promise 拒绝会经 Node 的默认策略落到
    // `process.on('uncaughtException')`（本文件末尾），而那个钩子会 `app.exit(1)` ——
    // 即崩溃兜底本身会把它正要救的这个会话杀掉。宁可只留一行日志。
    .catch((err) => logMain(`renderer gone 对话框失败：${(err && err.message) || err}`));
}

// 渲染进程健康接线（spec D5）。三个事件共用一份重载配额：它们都是「窗口自愈」这一件事。
// 抽成函数而不是塞进 createWindow：后者已 60 行，再塞 ~25 行就越过 CLAUDE.md 的 50 行阈值；
// wireWindowShortcuts 本来就是本文件「窗口接线抽成同名函数」的既有范式。
function wireRendererHealth(targetWin) {
  const budget = createReloadBudget({});
  // 退出期渲染进程本来就会消失：此时自愈 = 把正在关的窗口拉回来，且对已销毁的 win 弹窗会抛。
  const isLive = () => !quitting && targetWin && !targetWin.isDestroyed();

  targetWin.webContents.on('render-process-gone', (_e, details) => {
    const info = describeRendererGone(details);
    logMain(`renderer gone: ${info.reason} — ${info.message}`); // 任何分支都不吞掉这一笔
    if (!isLive() || info.expected) return;   // clean-exit 不是故障：记完日志就收手，不自愈也不弹窗
    if (info.recoverable && budget.allow(Date.now())) {
      targetWin.reload();
      return;
    }
    promptRendererGone(targetWin, info, isLive); // 配额用尽 / 不值得自愈 → 把选择权交回用户
  });

  // 只记日志、不弹窗：长任务渲染期间 unresponsive 会反复触发，弹模态即弹窗风暴；
  // 配对记 responsive 才能在日志里读出「卡了多久」——这正是「外壳日志全绿 ≠ 没崩」要补的那一笔。
  targetWin.on('unresponsive', () => logMain('renderer unresponsive（界面暂时无响应，未做任何处置）'));
  targetWin.on('responsive', () => logMain('renderer responsive（界面已恢复响应）'));

  targetWin.webContents.on('did-fail-load', (_e, code, desc, url, isMainFrame) => {
    if (!isMainFrame) return;
    const line = describeLoadFailure(code, desc, url);
    if (!line) return; // -3 ERR_ABORTED：正常导航打断，忽略
    logMain(`load failed: ${line}`);
    if (!isLive()) return;
    targetWin.loadFile(path.join(__dirname, 'loading.html')); // 回自带过渡页，而不是 Chromium 英文错误页
    if (budget.allow(Date.now())) {
      // 最常见的成因是前端进程刚死 / 正在重起，一次有界重试就能自己回来；配额用尽即停在过渡页，
      // 用户仍可 Ctrl+R（wireWindowShortcuts 已接）——绝不无限重试。
      setTimeout(() => {
        if (isLive()) targetWin.loadURL(cfg.WEB_URL);
      }, 1500);
    }
  });
}

// 构建溯源戳来源：构建期注入的 ARAGON_COMMIT（dev/CI 可设）优先；其次取打包期 electron-builder
// extraMetadata 烤进 package.json 的 aragonCommit（由 pack.ps1 经 --config.extraMetadata 注入）。
function buildCommit() {
  if (process.env.ARAGON_COMMIT) return process.env.ARAGON_COMMIT;
  try {
    const pkg = require('../package.json');
    if (pkg && pkg.aragonCommit) return String(pkg.aragonCommit);
  } catch {
    /* 未注入则省略，不强依赖 */
  }
  return null;
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
  // 构建溯源戳（诊断 desktop-header-right-and-live-toggle-unclickable §六.4）：让运行中的 app 显示其构建
  // commit，便于把「跑了旧会话」与「真代码 bug」一眼区分。优先取构建期注入的 env，其次取打包期
  // electron-builder extraMetadata 烤进 package.json 的 aragonCommit；二者皆缺则省略（不强依赖）。
  const commit = buildCommit();
  if (commit) lines.push(`构建：${commit}`);
  // WCO 运行期断言（G1，诊断 desktop-packaged-wco-header-right-still-unclickable）：展示渲染层经
  // aragon:win:report-overlay 回传的实际 Window Controls Overlay 状态，把「系统是否真接管顶栏 caption」
  // 从盲区变为可读，让真机人工门可判别（仅 win 平台有意义，其它平台 supported 恒 false 故不展示）。
  if (process.platform === 'win32') {
    const wco = lastOverlayState;
    if (!wco) {
      lines.push('WCO active：未上报（渲染层尚未回传）');
    } else if (wco.supported) {
      const r = wco.rect;
      const rectStr = r ? `  ·  安全区 ${r.width}×${r.height} @(${r.x},${r.y})` : '';
      lines.push(`WCO active：${wco.visible ? 'true' : 'false'}${rectStr}`);
    } else {
      lines.push('WCO active：不支持（navigator.windowControlsOverlay 缺失）');
    }
  }

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

// 后训练数据目录的系统「选择文件夹」对话框（ai-trade-trajectory-capture §2.15）。取消 ⇒ null。
// defaultPath 只在渲染层给了合法绝对路径时才带（dirAccess.pickDefaultPath），否则回落系统默认。
async function pickTrajectoryDirectory(opts) {
  const options = {
    title: '选择后训练数据保存目录',
    properties: ['openDirectory', 'createDirectory', 'dontAddToRecent'],
  };
  const defaultPath = dirAccess.pickDefaultPath(opts);
  if (defaultPath) options.defaultPath = defaultPath;
  const res = win
    ? await dialog.showOpenDialog(win, options)
    : await dialog.showOpenDialog(options);
  if (res.canceled || !res.filePaths || !res.filePaths[0]) return null;
  return res.filePaths[0];
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
    // AI 自动交易：退出前给后端一次「落暂停」的机会（best-effort，2s 硬超时）。
    // **正确性不依赖它** —— 强杀 / 崩溃 / 断电由后端的启动对账兜底（spec §3.7）；而 Windows 上
    // `_gracefulStop()` 用的 `taskkill /PID /T`（无 `/F`）对一个 windowsHide 的无窗口 Python
    // 子进程通常直接失败，8s 宽限后被 `/F /T` 强杀，故 FastAPI 的 lifespan 关停钩子大概率跑不到。
    // 这条 HTTP 钩子是那条路的**可靠**替代：它默认走 `hold`（带仓暂停），重开后有一张续跑卡。
    // 注意路径是后端自己的 `/api/*`（`/api/py/*` 是 Next 的代理前缀，直连后端时不存在）。
    const paused = await postJsonSafe(
      `http://${cfg.BACKEND_HOST}:${cfg.BACKEND_PORT}/api/ai-trade/quit-pause`, {}, 2000);
    if (paused) logMain(`AI 自动交易退出前处置：${paused}`);
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
  // 后训练数据目录（ai-trade-trajectory-capture §2.15）。**只打开目录、从不打开文件**：
  // shell.openPath 对文件的语义是「用关联程序打开」，故 IPC 边界只接受存在的绝对目录。
  ipcMain.handle('aragon:pick-directory', (_e, opts) => pickTrajectoryDirectory(opts));
  ipcMain.handle('aragon:open-path', async (_e, target) => {
    const rejected = await dirAccess.openPathRejection(target);
    if (rejected) return rejected;
    return (await shell.openPath(target)) || ''; // 空串 = 成功（Electron 约定）
  });

  // 窗口控制 IPC（spec §4.3 / §6）：自定义标题栏的最小化 / 最大化切换 / 关闭 /
  // 查询最大化态 + 触发既有「关于」对话框。关闭仅 win.close()，落既有退出清理链（D3）。
  ipcMain.on('aragon:win:minimize', () => win && win.minimize());
  ipcMain.on('aragon:win:maximize-toggle', () => {
    if (!win) return;
    win.isMaximized() ? win.unmaximize() : win.maximize();
  });
  ipcMain.on('aragon:win:close', () => win && win.close());
  ipcMain.handle('aragon:win:is-maximized', () => !!win && win.isMaximized());
  // 主题切换时同步 Windows 系统标题栏控件叠加配色（仅 win，且窗口以 titleBarOverlay 创建时可用）。
  ipcMain.on('aragon:win:set-overlay-theme', (_e, theme) => {
    if (process.platform !== 'win32' || !win || typeof win.setTitleBarOverlay !== 'function') return;
    win.setTitleBarOverlay(overlayForTheme(theme === 'light' ? 'light' : 'dark'));
  });
  // G1：记录渲染层回传的 Window Controls Overlay 运行期状态（feature-detect 在渲染层完成，主进程仅存值）。
  ipcMain.on('aragon:win:report-overlay', (_e, state) => {
    lastOverlayState = state && typeof state === 'object' ? state : null;
  });
  ipcMain.on('aragon:show-about', () => showAbout());

  // bootstrap 的兜底（spec D2）：没有它，启动期任何预料之外的异常都只是一个未处理的 Promise
  // 拒绝 → uncaughtException → app.exit(1)，用户看到的是「双击后什么都没发生」。
  app.whenReady().then(bootstrap).catch((err) => {
    try {
      logMain(`bootstrap failed: ${err && err.stack ? err.stack : err}`);
      // logsDir() 是纯路径拼接；**绝不在这里调 ensureLogsDir()**——它会建目录、会失败，
      // 而「建日志目录失败」恰恰是本条要兜的头号失败模式，兜底自己抛等于什么都没做。
      const { title, body } = describeStartupFailure(err, paths.logsDir());
      dialog.showErrorBox(title, body);
    } catch (inner) {
      // 极早期（GUI 未就绪 / 磁盘只读）连日志和对话框都不可用：那就只剩退出这一件事可做。
      try {
        process.stderr.write(`[main] startup failure handler failed: ${inner}\n`);
      } catch { /* 无处可写 */ }
    }
    gracefulQuit().finally(() => app.exit(1));
  });

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
