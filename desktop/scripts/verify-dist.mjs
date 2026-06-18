// verify:dist —— 打包产物端到端发布验收冒烟（spec: desktop-release-acceptance v2，§4.2 / §6 / §8）。
//
// 目标：证明 `dist/win-unpacked/` 里**已构建的真实产物**「真的能用、且用的是真实数据」——而非仅
//   「构建产物存在」。驱动后端 exe（:18760）+ Next standalone server.js（:18761，用本进程 node 跑），
//   以 HTTP 断言坐实：
//     · 可用       —— 后端 /api/health 200、前端 / 200 text/html（§8.1）
//     · 数据真实   —— /api/candles 头 X-Aragon-Data-Source: live + 形状合理 + health.cachedFallbackEnabled===false（C1）
//     · 功能正常   —— 前端 BFF /api/py/health 经 standalone rewrites 真打到后端（端到端集成）
//     · 干净退出   —— 拆解后 18760/18761 轮询 ≤5s 内重新可绑（无孤儿进程）
//   PASS/FAIL 以退出码表达，可在本机/脚本反复运行。
//
// 设计要点（spec §3）：
//   D1 —— 验收「打包产物」而非真起整个 GUI：无界面、可脚本化、不受 ambient ELECTRON_RUN_AS_NODE 干扰。
//   D2 —— 默认必须拿到 live 真实数据；OKX 不可达只接受显式 5xx（--allow-okx-down → WARN-PASS），绝不把缓存/假数据当 live。
//   D3 —— 复用 electron-free 的 config.js / process-manager.js（单一真源，端口/清理策略不漂移），不另写一套。
//
// 运行：node scripts/verify-dist.mjs [--allow-okx-down] [--keep-running] [--unpacked <dir>] [--json]
// 退出码见 §8.2：0=PASS/WARN-PASS；10=产物缺失；11=端口被占；20=后端未就绪；21=health 非 ok；
//   22=candles 非 live（且未 --allow-okx-down）；23=K 线形状不合理；24=cachedFallbackEnabled!==false（同上）；
//   30=前端未就绪；31=前端首页异常；32=BFF 代理异常；40=拆解后轮询 ≤5s 仍端口未释放。

import { createRequire } from 'node:module';
import http from 'node:http';
import { existsSync, readFileSync, unlinkSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

// 复用 CJS 的 config.js / process-manager.js（二者均 Electron-free，仅依赖 child_process/http/net，D3）。
// 注意：不引 paths.js —— 它 require('electron')，本脚本是纯 node 进程，无 electron 上下文（故 web-server.json 自行读取）。
const require = createRequire(import.meta.url);
const cfg = require('../src/lib/config.js');
const { ProcessManager, portFree, waitForHttp, delay } = require('../src/lib/process-manager.js');

const IS_WIN = process.platform === 'win32';
const SCRIPTS_DIR = dirname(fileURLToPath(import.meta.url));
const DESKTOP = dirname(SCRIPTS_DIR);

// ----------------------------- CLI 解析 -----------------------------
const argv = process.argv.slice(2);
const ALLOW_OKX_DOWN = argv.includes('--allow-okx-down');
const KEEP_RUNNING = argv.includes('--keep-running');
const JSON_OUT = argv.includes('--json');
function argValue(name) {
  const i = argv.indexOf(name);
  return i >= 0 && i + 1 < argv.length ? argv[i + 1] : null;
}
const UNPACKED = argValue('--unpacked') || join(DESKTOP, 'dist', 'win-unpacked');

// ----------------------------- 路径解析（§4.2 step0）-----------------------------
const RES = join(UNPACKED, 'resources');
const BACKEND_DIR = join(RES, 'backend');
const BACKEND_EXE = join(BACKEND_DIR, IS_WIN ? 'aragon-quant.exe' : 'aragon-quant');
const WEB_RES = join(RES, 'web');
const WEB_MANIFEST = join(WEB_RES, 'web-server.json');

// ----------------------------- 报告记录（§7）-----------------------------
const steps = [];
let warnings = 0;
const startedAt = Date.now();

function log(msg) {
  process.stdout.write(`[verify:dist] ${msg}\n`);
}

class VerifyFail extends Error {
  constructor(code, label, detail) {
    super(detail || label);
    this.code = code;
    this.label = label;
    this.detail = detail;
  }
}

function recordPass(id, label, detail) {
  steps.push({ id, label, ok: true, detail });
  log(`✓ ${label}${detail ? ` — ${detail}` : ''}`);
}
function recordWarn(id, label, detail) {
  steps.push({ id, label, ok: true, warn: true, detail });
  warnings += 1;
  log(`⚠ ${label}${detail ? ` — ${detail}` : ''}`);
}
/** 记录失败步骤并抛出 VerifyFail（携带退出码）。控制流见 §4.2：步骤 3-6 在 try、7-8 在 finally。 */
function fail(code, id, label, detail) {
  steps.push({ id, label, ok: false, detail });
  throw new VerifyFail(code, label, detail);
}

// ----------------------------- HTTP 工具 -----------------------------
/** 取完整响应（状态码 + 头 + body 文本）。用于断言；与 process-manager 的「在听即就绪」探测互补。 */
function httpGetFull(url, timeoutMs = 12_000) {
  return new Promise((resolve, reject) => {
    const req = http.get(url, (res) => {
      const chunks = [];
      res.on('data', (c) => chunks.push(c));
      res.on('end', () =>
        resolve({
          status: res.statusCode,
          headers: res.headers,
          body: Buffer.concat(chunks).toString('utf-8'),
        }),
      );
    });
    req.setTimeout(timeoutMs, () => req.destroy(new Error(`HTTP 超时 ${timeoutMs}ms：${url}`)));
    req.on('error', reject);
  });
}

function httpPostJson(url, body, timeoutMs = 20_000) {
  return new Promise((resolve, reject) => {
    const payload = body == null ? '' : JSON.stringify(body);
    const req = http.request(url, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Content-Length': Buffer.byteLength(payload),
      },
    }, (res) => {
      const chunks = [];
      res.on('data', (c) => chunks.push(c));
      res.on('end', () =>
        resolve({
          status: res.statusCode,
          headers: res.headers,
          body: Buffer.concat(chunks).toString('utf-8'),
        }),
      );
    });
    req.setTimeout(timeoutMs, () => req.destroy(new Error(`HTTP POST timeout ${timeoutMs}ms: ${url}`)));
    req.on('error', reject);
    req.write(payload);
    req.end();
  });
}

function parseJsonOrFail(res, code, id, label) {
  try {
    return JSON.parse(res.body);
  } catch {
    fail(code, id, label, `non-JSON: ${res.body.slice(0, 160)}`);
  }
}

function extractNextStaticAssets(html, ext) {
  const pattern = new RegExp(`\\/_next\\/static\\/[^"'<>\\s)]+\\.${ext}(?:\\?[^"'<>\\s)]*)?`, 'g');
  return [...new Set([...html.matchAll(pattern)].map((m) => m[0].replace(/&amp;/g, '&')))];
}

function contentTypeMatches(kind, contentType) {
  const value = String(contentType || '').toLowerCase();
  return kind === 'css' ? value.includes('text/css') : value.includes('javascript');
}

async function verifyStaticAssets(html) {
  const cssAssets = extractNextStaticAssets(html, 'css');
  const jsAssets = extractNextStaticAssets(html, 'js');
  if (cssAssets.length === 0) fail(31, 'web-static-css', '首页引用 CSS 静态资源', '未找到 /_next/static/*.css');
  if (jsAssets.length === 0) fail(31, 'web-static-js', '首页引用 JS 静态资源', '未找到 /_next/static/*.js');

  for (const asset of cssAssets) {
    const res = await httpGetFull(new URL(asset, cfg.WEB_URL).toString());
    if (res.status !== 200) fail(31, 'web-static-css', `CSS ${asset} 200`, `状态码 ${res.status}`);
    if (!contentTypeMatches('css', res.headers['content-type'])) {
      fail(31, 'web-static-css', `CSS ${asset} text/css`, `content-type=${res.headers['content-type'] || '(无)'}`);
    }
  }
  recordPass('web-static-css', '前端 CSS 静态资源可用', `${cssAssets.length} 个 /_next/static CSS`);

  for (const asset of jsAssets) {
    const res = await httpGetFull(new URL(asset, cfg.WEB_URL).toString());
    if (res.status !== 200) fail(31, 'web-static-js', `JS ${asset} 200`, `状态码 ${res.status}`);
    if (!contentTypeMatches('js', res.headers['content-type'])) {
      fail(31, 'web-static-js', `JS ${asset} JavaScript`, `content-type=${res.headers['content-type'] || '(无)'}`);
    }
  }
  recordPass('web-static-js', '前端 JS 静态资源可用', `${jsAssets.length} 个 /_next/static JS`);
}

async function verifyPaperBatchSmoke() {
  const strategiesRes = await httpGetFull(`${cfg.WEB_URL}/api/py/strategies?enabled=true`);
  if (strategiesRes.status !== 200) {
    fail(33, 'paper-batch-strategies', 'BFF /api/py/strategies?enabled=true 200', `status ${strategiesRes.status}`);
  }
  const strategies = parseJsonOrFail(strategiesRes, 33, 'paper-batch-strategies', 'strategies JSON');
  const strategyIds = (Array.isArray(strategies) ? strategies : [])
    .filter((item) => item && item.enabled !== false && !item.unavailable && Number.isInteger(item.id))
    .map((item) => item.id)
    .slice(0, 2);
  if (strategyIds.length < 2) {
    fail(33, 'paper-batch-strategies', 'at least two enabled strategies', `found ${strategyIds.length}`);
  }

  const startRes = await httpPostJson(`${cfg.WEB_URL}/api/py/paper/batch/start`, {
    strategyIds,
    instId: 'BTC-USDT-SWAP',
    bar: '1H',
    initialCapital: 10000,
    source: 'live',
    speed: 1,
    replayBars: 10,
    priming: false,
    primingBars: 10,
  });
  if (startRes.status !== 200) {
    fail(33, 'paper-batch-start', 'BFF /api/py/paper/batch/start 200', `status ${startRes.status}: ${startRes.body.slice(0, 240)}`);
  }
  const startBody = parseJsonOrFail(startRes, 33, 'paper-batch-start', 'batch start JSON');
  if (!startBody.groupId || !Array.isArray(startBody.sessions)) {
    fail(33, 'paper-batch-start', 'batch start shape', startRes.body.slice(0, 240));
  }
  const startedIds = startBody.sessions.map((item) => item.strategyId);
  if (JSON.stringify(startedIds) !== JSON.stringify(strategyIds)) {
    fail(33, 'paper-batch-start', 'batch strategy ids match selection', `expected ${strategyIds.join(',')}, got ${startedIds.join(',')}`);
  }
  smokeGroupId = startBody.groupId;

  const sessionsRes = await httpGetFull(`${cfg.BACKEND_URL}/api/paper/sessions`);
  if (sessionsRes.status !== 200) {
    fail(33, 'paper-batch-sessions', 'backend /api/paper/sessions 200', `status ${sessionsRes.status}`);
  }
  const sessions = parseJsonOrFail(sessionsRes, 33, 'paper-batch-sessions', 'paper sessions JSON');
  const runningForGroup = (Array.isArray(sessions) ? sessions : [])
    .filter((item) => item && item.groupId === startBody.groupId)
    .map((item) => item.strategyId)
    .sort((a, b) => a - b);
  const expectedIds = [...strategyIds].sort((a, b) => a - b);
  if (JSON.stringify(runningForGroup) !== JSON.stringify(expectedIds)) {
    fail(33, 'paper-batch-sessions', 'sessions include started batch group', `expected ${expectedIds.join(',')}, got ${runningForGroup.join(',')}`);
  }
  recordPass('paper-batch-start', '多策略 paper 批量启动可用', `group=${startBody.groupId}, strategies=${strategyIds.join(',')}`);
}

/** 轮询直到所有端口可绑（释放）或超时。Windows taskkill /T 异步、子进程树 socket 释放有数百 ms~秒级延迟（P1-2）。 */
async function waitPortsFree(ports, timeoutMs = 5_000, intervalMs = 250) {
  const deadline = Date.now() + timeoutMs;
  // 至少探一次
  for (;;) {
    const results = await Promise.all(ports.map((p) => portFree(p)));
    if (results.every(Boolean)) return true;
    if (Date.now() >= deadline) return false;
    await delay(intervalMs);
  }
}

// ----------------------------- 主流程 -----------------------------
let exitCode = 0;
let earlyExit = null; // P2-2：子进程在就绪轮询期间早退即记录，配合 waitForHttp 的 isAborted 立刻 FAIL

// 构造 ProcessManager（onUnexpectedExit 仅在 stop() 之前的非预期退出触发；stop 中 isQuitting=true 自动静默）。
const pm = new ProcessManager({
  onUnexpectedExit: (info) => {
    if (!earlyExit) earlyExit = info;
    log(`✗ 子进程意外退出：${info.name}（code=${info.code}, signal=${info.signal}）`);
  },
});

const tmpDb = join(tmpdir(), `aragon-verify-${process.pid}.db`);
let smokeGroupId = null;

async function main() {
  log(`目标产物：${UNPACKED}`);
  log(`模式：${ALLOW_OKX_DOWN ? 'OKX 可不可达（--allow-okx-down，仍禁假数据）' : '默认（要求 live 真实数据）'}`);

  // === 步骤 1：前置产物校验（无子进程，失败直接 exit-10）===
  const missing = [];
  if (!existsSync(UNPACKED)) missing.push(UNPACKED);
  if (!existsSync(BACKEND_EXE)) missing.push(BACKEND_EXE);
  if (!existsSync(WEB_MANIFEST)) missing.push(WEB_MANIFEST);
  if (missing.length) {
    steps.push({ id: 'artifacts', label: '前置产物存在', ok: false, detail: `缺失：${missing.join(' ; ')}` });
    log(`✗ 前置产物缺失：\n    ${missing.join('\n    ')}`);
    log('  请先构建：cd desktop ; npm run smoke（出 win-unpacked/）或 npm run dist（出安装包）。');
    finishAndExit(10);
    return;
  }
  // 解析 standalone 入口（不硬编码嵌套路径，复用 web-server.json 的 serverJs，与 paths.readWebServerManifest 同源，R2）。
  let serverJs;
  let webCwd;
  try {
    const manifest = JSON.parse(readFileSync(WEB_MANIFEST, 'utf-8'));
    if (!manifest.serverJs) throw new Error('web-server.json 缺 serverJs 字段');
    serverJs = join(WEB_RES, manifest.serverJs); // serverJs 为相对 resources/web 的 POSIX 路径；join 在 Windows 下亦正确
    webCwd = dirname(serverJs);
    if (!existsSync(serverJs)) throw new Error(`standalone 入口不存在：${serverJs}`);
  } catch (e) {
    steps.push({ id: 'artifacts', label: '前置产物存在', ok: false, detail: `web 清单解析失败：${e.message}` });
    log(`✗ web 清单解析失败：${e.message}`);
    finishAndExit(10);
    return;
  }
  recordPass('artifacts', '前置产物存在', `backend exe + standalone(${serverJs.slice(WEB_RES.length + 1)})`);

  // === 步骤 2：端口预检（18760 / 18761），失败直接 exit-11 ===
  const busy = [];
  for (const p of [cfg.BACKEND_PORT, cfg.WEB_PORT]) {
    if (!(await portFree(p))) busy.push(p);
  }
  if (busy.length) {
    steps.push({ id: 'ports-precheck', label: '端口预检', ok: false, detail: `被占：${busy.join(', ')}` });
    log(`✗ 端口被占：${busy.join(', ')}。请释放后重试：`);
    log(`  PowerShell> Get-NetTCPConnection -LocalPort ${busy.join(',')} | Select OwningProcess ; Stop-Process -Id <PID> -Force`);
    finishAndExit(11);
    return;
  }
  recordPass('ports-precheck', '端口预检', `${cfg.BACKEND_PORT} / ${cfg.WEB_PORT} 均空闲`);

  // === 步骤 3-6：起进程 + 断言（try）；步骤 7-8：拆解 + 残端口（finally）。控制流硬约束见 §4.2 ⚠️ ===
  try {
    // --- 步骤 3：起后端（复用 prod 规格）---
    const backendEnv = cfg.backendEnv({ mode: 'prod', dbPath: tmpDb });
    delete backendEnv.ALLOW_CACHED_FALLBACK; // P1-3：清 ambient 泄漏，强制诚实默认——不许缓存冒充 live
    delete backendEnv.ELECTRON_RUN_AS_NODE; // R7：本脚本跑 node，不应受 ambient RUN_AS_NODE 影响
    log('启动后端 exe …');
    pm.startChild({ name: 'backend', command: BACKEND_EXE, args: ['--port', String(cfg.BACKEND_PORT)], cwd: BACKEND_DIR, env: backendEnv });
    const backendOk = await waitForHttp(cfg.BACKEND_HEALTH_URL, cfg.BACKEND_READY_TIMEOUT_MS, { isAborted: () => earlyExit !== null });
    if (!backendOk) {
      const why = earlyExit
        ? `子进程早退：${earlyExit.name} code=${earlyExit.code} signal=${earlyExit.signal}`
        : `${cfg.BACKEND_READY_TIMEOUT_MS / 1000}s 内未就绪（${cfg.BACKEND_HEALTH_URL}）`;
      fail(20, 'backend-ready', '后端就绪', why);
    }
    recordPass('backend-ready', '后端就绪', cfg.BACKEND_HEALTH_URL);

    // --- 步骤 4a：/api/health ---
    const health = await httpGetFull(cfg.BACKEND_HEALTH_URL);
    if (health.status !== 200) fail(21, 'health', 'health 200', `状态码 ${health.status}`);
    let healthJson;
    try {
      healthJson = JSON.parse(health.body);
    } catch {
      fail(21, 'health', 'health JSON', `非 JSON：${health.body.slice(0, 120)}`);
    }
    if (healthJson.status !== 'ok') fail(21, 'health', 'health status==ok', `status=${healthJson.status}`);
    const okx = healthJson.okx; // 'up' | 'down'
    // P1-3：把「数据真实硬约束」做成主动校验——cachedFallbackEnabled 必须为 false（默认）。
    if (healthJson.cachedFallbackEnabled !== false) {
      if (ALLOW_OKX_DOWN) {
        recordWarn('health-fallback', 'cachedFallbackEnabled===false', `实际 ${healthJson.cachedFallbackEnabled}（--allow-okx-down 放宽为告警）`);
      } else {
        fail(24, 'health-fallback', 'cachedFallbackEnabled===false', `实际 ${healthJson.cachedFallbackEnabled}（疑似 ambient ALLOW_CACHED_FALLBACK 泄漏放开缓存回退）`);
      }
    } else {
      recordPass('health-fallback', 'cachedFallbackEnabled===false', '诚实默认');
    }
    recordPass('health', 'health ok', `okx=${okx}`);

    // --- 步骤 4b：/api/candles（数据真实性核心断言，D2）---
    const candleUrl = `${cfg.BACKEND_URL}/api/candles?instId=BTC-USDT-SWAP&bar=1H&limit=120`;
    const candles = await httpGetFull(candleUrl);
    const dataSource = candles.headers['x-aragon-data-source']; // node 头名小写
    if (candles.status === 200 && dataSource === 'live') {
      // 真实数据：形状校验（P2-4：下界放宽至 50，容忍 OKX history-candles 单次 100 上限/分页/限频抖动）。
      let rows;
      try {
        rows = JSON.parse(candles.body);
      } catch {
        fail(23, 'candles', 'candles JSON', `非 JSON：${candles.body.slice(0, 120)}`);
      }
      if (!Array.isArray(rows) || rows.length < 50) {
        fail(23, 'candles-shape', 'K 线形状（≥50 根）', `实际 ${Array.isArray(rows) ? rows.length : '非数组'}`);
      }
      let prevTs = -Infinity;
      for (const r of rows) {
        if (typeof r.ts !== 'number' || r.ts <= prevTs) {
          fail(23, 'candles-shape', 'ts 升序', `ts=${r.ts} prev=${prevTs}`);
        }
        if (r.ts >= 2_000_000_000) {
          fail(23, 'candles-shape', 'ts 秒级（<2e9）', `ts=${r.ts}（疑似毫秒级）`);
        }
        if (!(typeof r.c === 'number' && r.c > 0)) {
          fail(23, 'candles-shape', '每根收盘价 c>0', `c=${r.c}`);
        }
        prevTs = r.ts;
      }
      recordPass('candles', '数据真实可用（live）', `${rows.length} 根真实 OKX K 线，ts 升序秒级、c>0`);
    } else if (candles.status === 200 && (dataSource === 'cache' || dataSource === 'stale')) {
      // 缓存（真实历史但非最新）：默认绝不当 live 通过（§8.3）。
      if (ALLOW_OKX_DOWN) {
        recordWarn('candles', 'candles 非 live（cache/stale）', `X-Aragon-Data-Source=${dataSource}（--allow-okx-down 放宽为告警；但仍非实时）`);
      } else {
        fail(22, 'candles', 'candles==live', `X-Aragon-Data-Source=${dataSource}（不可把缓存当实时；离线请显式 --allow-okx-down）`);
      }
    } else if (candles.status >= 500) {
      // OKX 不可达 → 后端显式 5xx（诚实错误，非假数据，C1）。
      if (ALLOW_OKX_DOWN) {
        recordWarn('candles', 'OKX 不可达（诚实 5xx）', `状态码 ${candles.status}（--allow-okx-down 判 WARN-PASS：诚实错误而非假数据）`);
      } else {
        fail(22, 'candles', 'candles==live', `状态码 ${candles.status}（OKX 不可达？请确认已导出 HTTP(S)_PROXY=127.0.0.1:7897；离线可加 --allow-okx-down）`);
      }
    } else {
      // 200 但无可信 live/cache 标注 → 视为不该出现的「无诚实标注数据」，硬约束下一律 FAIL（即便 --allow-okx-down，绝不接受假数据，§8.3）。
      fail(22, 'candles', 'candles==live', `状态码 ${candles.status}、X-Aragon-Data-Source=${dataSource || '(无)'}（既非 live 亦非诚实错误，拒绝当真实数据通过）`);
    }

    // --- 步骤 5：起前端（复用 prod 规格，用本进程 node 直接跑 standalone server.js）---
    const webEnv = cfg.webEnv({ mode: 'prod' });
    delete webEnv.ELECTRON_RUN_AS_NODE; // R7：跑 node 不应带 RUN_AS_NODE（与 main.js prod 用 Electron 自带 node 不同）
    log('启动前端 standalone server.js（本进程 node）…');
    pm.startChild({ name: 'web', command: process.execPath, args: [serverJs], cwd: webCwd, env: webEnv });
    const webOk = await waitForHttp(cfg.WEB_URL, cfg.WEB_READY_TIMEOUT_MS, { isAborted: () => earlyExit !== null });
    if (!webOk) {
      const why = earlyExit
        ? `子进程早退：${earlyExit.name} code=${earlyExit.code} signal=${earlyExit.signal}`
        : `${cfg.WEB_READY_TIMEOUT_MS / 1000}s 内未就绪（${cfg.WEB_URL}）`;
      fail(30, 'web-ready', '前端就绪', why);
    }
    recordPass('web-ready', '前端就绪', cfg.WEB_URL);

    // --- 步骤 6a：前端首页 ---
    const home = await httpGetFull(`${cfg.WEB_URL}/`);
    const ctype = home.headers['content-type'] || '';
    if (home.status !== 200) fail(31, 'web-home', '前端首页 200', `状态码 ${home.status}`);
    if (!ctype.includes('text/html')) fail(31, 'web-home', '前端首页 text/html', `content-type=${ctype}`);
    if (!(home.body.includes('<title') || home.body.includes('Aragon'))) {
      fail(31, 'web-home', '前端首页含应用标识', '未见 <title 或 Aragon');
    }
    recordPass('web-home', '前端首页可用', `200 ${ctype}`);
    await verifyStaticAssets(home.body);

    // --- 步骤 6b：前端 BFF 代理 → 后端（端到端集成，证明「功能正常」）---
    const bff = await httpGetFull(`${cfg.WEB_URL}/api/py/health`);
    if (bff.status !== 200) fail(32, 'web-bff', 'BFF /api/py/health 200', `状态码 ${bff.status}`);
    let bffJson;
    try {
      bffJson = JSON.parse(bff.body);
    } catch {
      fail(32, 'web-bff', 'BFF JSON', `非 JSON：${bff.body.slice(0, 120)}`);
    }
    if (bffJson.status !== 'ok') fail(32, 'web-bff', 'BFF status==ok', `status=${bffJson.status}`);
    recordPass('web-bff', '前端 BFF 代理可用', '/api/py/health → 后端 200（standalone rewrites 生效）');

    // --- 步骤 6c：多策略 paper 启动 smoke（复现桌面 packaged proxy 失败边界）---
    await verifyPaperBatchSmoke();
  } catch (err) {
    if (err instanceof VerifyFail) {
      exitCode = err.code;
    } else {
      exitCode = 1;
      steps.push({ id: 'internal', label: '未预期错误', ok: false, detail: String(err && err.message ? err.message : err) });
      log(`✗ 未预期错误：${err && err.stack ? err.stack : err}`);
    }
  } finally {
    if (smokeGroupId) {
      try {
        const stopped = await httpPostJson(`${cfg.BACKEND_URL}/api/paper/batch/${encodeURIComponent(smokeGroupId)}/stop`, null, 12_000);
        if (stopped.status === 200) {
          recordPass('paper-batch-cleanup', '多策略 paper smoke 清理完成', smokeGroupId);
        } else {
          recordWarn('paper-batch-cleanup', '多策略 paper smoke 清理异常', `status ${stopped.status}: ${stopped.body.slice(0, 160)}`);
        }
      } catch (e) {
        recordWarn('paper-batch-cleanup', '多策略 paper smoke 清理异常', e && e.message ? e.message : String(e));
      }
      smokeGroupId = null;
    }
    if (KEEP_RUNNING) {
      // P2-3：保留子进程与临时 DB，跳过拆解与残端口断言，给手动释放指引。
      recordWarn('teardown', '保留子进程（--keep-running）', `临时 DB 保留于 ${tmpDb}`);
      log('--keep-running：子进程保留中；排障毕请手动释放：');
      log(`  PowerShell> Get-NetTCPConnection -LocalPort ${cfg.BACKEND_PORT},${cfg.WEB_PORT} | Select OwningProcess ; Stop-Process -Id <PID> -Force`);
    } else {
      // 步骤 7：拆解（优雅 → 兜底 tree-kill）+ 删临时 DB（无论 PASS/FAIL 必执行，P1-1）。
      log('拆解子进程并清理临时 DB …');
      try {
        await pm.stop();
      } catch (e) {
        log(`(警告) 拆解子进程异常：${e && e.message}`);
      }
      try {
        unlinkSync(tmpDb);
      } catch (e) {
        if (e && e.code !== 'ENOENT') log(`(警告) 删除临时 DB 失败：${e.message}`);
      }
      // 步骤 8：残端口断言——轮询 ≤5s（P1-2）。
      const freed = await waitPortsFree([cfg.BACKEND_PORT, cfg.WEB_PORT], 5_000, 250);
      if (freed) {
        recordPass('ports-released', '退出干净（端口释放）', `${cfg.BACKEND_PORT} / ${cfg.WEB_PORT} 已重新可绑`);
      } else {
        steps.push({ id: 'ports-released', label: '退出干净（端口释放）', ok: false, detail: '轮询 5s 仍被占（疑有孤儿进程）' });
        log('✗ 拆解后端口仍被占（轮询 5s）——疑有孤儿进程。');
        if (exitCode === 0) exitCode = 40; // 仅在主流程 PASS 时才用残端口码覆盖
      }
    }
  }

  finishAndExit(exitCode);
}

// ----------------------------- 结构化报告 + 退出 -----------------------------
function finishAndExit(code) {
  const durationMs = Date.now() - startedAt;
  const pass = code === 0;
  const summary = { pass, warnings, durationMs, exitCode: code };

  log('───────────── 验收报告 ─────────────');
  for (const s of steps) {
    const mark = s.ok ? (s.warn ? '⚠ WARN' : '✓ PASS') : '✗ FAIL';
    log(`  ${mark}  [${s.id}] ${s.label}${s.detail ? `：${s.detail}` : ''}`);
  }
  log('────────────────────────────────────');
  log(
    `${pass ? (warnings ? 'WARN-PASS' : 'PASS') : 'FAIL'}  ` +
      `(warnings=${warnings}, exit=${code}, ${(durationMs / 1000).toFixed(1)}s)`,
  );
  if (JSON_OUT) {
    // §6.2/P2-6：可选 --json 旁路输出，便于上层脚本解析（结构同 §7）。
    process.stdout.write(`__VERIFY_DIST_JSON__ ${JSON.stringify({ steps, summary })}\n`);
  }
  process.exit(code);
}

main().catch((err) => {
  // 兜底：main 自身（极少数 finally 外）抛错也尽力拆解，避免孤儿进程占端口。
  log(`✗ 致命错误：${err && err.stack ? err.stack : err}`);
  pm.stop()
    .catch(() => {})
    .finally(() => {
      try {
        unlinkSync(tmpDb);
      } catch {
        /* 忽略 */
      }
      process.exit(1);
    });
});
