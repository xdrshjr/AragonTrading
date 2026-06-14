// build:web —— 隔离扁平构建，根除 Windows EPERM symlink（spec: desktop-packaging-build-hardening v2）。
//
// 背景：迭代 1 的 build:web 直接在 apps/web 上跑 `next build`（standalone）。pnpm 的 node_modules
//   内部大量符号链接（.pnpm 虚拟 store），Next 的 copyTracedFiles 遇符号链接会用 fs.symlink 重建，
//   而 Windows 普通进程无 SeCreateSymbolicLinkPrivilege → EPERM（须开发者模式/管理员才能绕过）。
//
// 本方案（§4.2）：在隔离副本 desktop/.web-build/ 上做扁平（无符号链接）安装后再构建——
//   1) 清理/重建隔离目录 BUILD（--clean 全清；默认保留 node_modules 复用加速）；
//   2) 复制 apps/web 源码进 BUILD（排除 node_modules/.next 等重型目录，及 pnpm/workspace 标记 N2）；
//   3) 仅在副本里注入 experimental.outputFileTracingRoot=BUILD（N1：14.2.x 该键仅 experimental 有效），
//      让 Next 不上溯到 monorepo 根、standalone 产物扁平；
//   4) 扁平安装依赖（优先 pnpm --node-linker=hoisted 复用本地 store；npm install --include=dev 兜底，
//      均产出无符号链接的真实 node_modules，D2/D5/N3）；
//   5) 以 NEXT_OUTPUT_STANDALONE=1 + PY_BACKEND_URL=http://127.0.0.1:18760 跑 next build
//      —— node_modules 无符号链接 → copyTracedFiles 全程真实文件复制 → 不再 fs.symlink → 无 EPERM；
//   6) 把 BUILD/.next/standalone 装配进 desktop/resources/web/（补 .next/static、public，写 web-server.json）。
//
// 不动 apps/web 源码（含 next.config.mjs）、不动主进程/端口契约、不动 dev 模式；改动仅落在本脚本。
// 子进程用 shell:true 调 npm/pnpm，规避 Node 对 .cmd 的 spawn 限制（CVE-2024-27980，迭代 1 §11 发现 2）。

import { spawnSync } from 'node:child_process';
import {
  cpSync,
  existsSync,
  mkdirSync,
  readFileSync,
  readdirSync,
  renameSync,
  rmSync,
  writeFileSync,
} from 'node:fs';
import { basename, dirname, join, relative, sep } from 'node:path';
import { fileURLToPath } from 'node:url';

const SCRIPTS_DIR = dirname(fileURLToPath(import.meta.url));
const DESKTOP = dirname(SCRIPTS_DIR);
const REPO = dirname(DESKTOP);
const WEB = join(REPO, 'apps', 'web');
const BUILD = join(DESKTOP, '.web-build'); // 隔离构建副本（git 忽略）
const BUILD_NEXT = join(BUILD, '.next');
const STANDALONE = join(BUILD_NEXT, 'standalone');
const RES_WEB = join(DESKTOP, 'resources', 'web');
const CLEAN = process.argv.slice(2).includes('--clean');

// 复制源码时按 basename 排除的目录/文件：重型产物 + pnpm/workspace 标记（后者会误导 Next 的工作区根推断，N2）。
const EXCLUDE_NAMES = new Set([
  'node_modules',
  '.next',
  'coverage',
  '.turbo',
  '.git',
  'pnpm-lock.yaml',
  'pnpm-workspace.yaml',
  '.npmrc',
  'tsconfig.tsbuildinfo',
]);

function log(msg) {
  console.log(`[build-web] ${msg}`);
}

function fail(msg) {
  console.error(`[build-web] ✗ ${msg}`);
  process.exit(1);
}

function directoryHasEntries(dir) {
  try {
    return readdirSync(dir).length > 0;
  } catch {
    return false;
  }
}

function replaceDirectory(stageDir, targetDir) {
  const backupDir = `${targetDir}.previous-${process.pid}`;
  if (existsSync(backupDir)) rmSync(backupDir, { recursive: true, force: true });

  let backedUp = false;
  if (existsSync(targetDir)) {
    renameSync(targetDir, backupDir);
    backedUp = true;
  }

  try {
    renameSync(stageDir, targetDir);
  } catch (e) {
    if (backedUp && !existsSync(targetDir) && existsSync(backupDir)) {
      renameSync(backupDir, targetDir);
    }
    throw e;
  }

  if (backedUp) rmSync(backupDir, { recursive: true, force: true });
}

/** 递归查找名为 server.js 的文件；跳过 node_modules 以免误命中第三方包的 server.js。 */
function findServerJs(root) {
  const stack = [root];
  while (stack.length) {
    const dir = stack.pop();
    let entries;
    try {
      entries = readdirSync(dir, { withFileTypes: true });
    } catch {
      continue;
    }
    for (const ent of entries) {
      const full = join(dir, ent.name);
      if (ent.isDirectory()) {
        if (ent.name === 'node_modules') continue;
        stack.push(full);
      } else if (ent.name === 'server.js') {
        return full;
      }
    }
  }
  return null;
}

// 步骤 1 + 2：重建隔离目录并复制源码（保留既有 node_modules 以复用，除非 --clean）。
function prepareBuildDir() {
  if (CLEAN && existsSync(BUILD)) {
    log('--clean：清空隔离目录 .web-build/');
    rmSync(BUILD, { recursive: true, force: true });
  }
  mkdirSync(BUILD, { recursive: true });
  // 清掉上次构建的 .next（保留 node_modules 以复用加速）；next build 会重建 .next/standalone。
  if (existsSync(BUILD_NEXT)) rmSync(BUILD_NEXT, { recursive: true, force: true });

  log('复制 apps/web 源码 → .web-build/（排除 node_modules/.next 与 pnpm/workspace 标记）…');
  cpSync(WEB, BUILD, {
    recursive: true,
    force: true,
    filter: (src) => {
      const base = basename(src);
      if (EXCLUDE_NAMES.has(base)) return false;
      if (base.endsWith('.log')) return false;
      return true;
    },
  });
}

// 步骤 3：仅在副本 next.config.mjs 注入 experimental.outputFileTracingRoot=BUILD（N1/D3/D4）。
function injectTracingRoot() {
  const cfgPath = join(BUILD, 'next.config.mjs');
  if (!existsSync(cfgPath)) fail(`副本缺 next.config.mjs：${cfgPath}`);
  let cfg = readFileSync(cfgPath, 'utf-8');

  if (cfg.includes('outputFileTracingRoot')) {
    log('副本 next.config.mjs 已含 outputFileTracingRoot，幂等跳过注入');
    return;
  }

  // 补充注入所需的 import（注入值用 path.dirname(fileURLToPath(import.meta.url)) 解析为 BUILD 自身）。
  let prefix = '';
  if (!/from ['"]node:path['"]/.test(cfg)) prefix += "import path from 'node:path';\n";
  if (!/from ['"]node:url['"]/.test(cfg)) prefix += "import { fileURLToPath } from 'node:url';\n";
  if (prefix) cfg = prefix + cfg;

  const tracingLine =
    '    // Next 14.2.x 仅识别 experimental.outputFileTracingRoot（顶层键会被静默忽略；Next 15 才提升为顶层）。\n' +
    '    outputFileTracingRoot: path.dirname(fileURLToPath(import.meta.url)),';

  if (/experimental\s*:\s*{/.test(cfg)) {
    // 副本已有 experimental 块 → 就地补子键，避免重复注入。
    cfg = cfg.replace(/experimental\s*:\s*{/, (m) => `${m}\n${tracingLine}`);
  } else if (cfg.includes('const nextConfig = {')) {
    cfg = cfg.replace(
      'const nextConfig = {',
      `const nextConfig = {\n  experimental: {\n${tracingLine}\n  },`,
    );
  } else {
    fail('无法在副本 next.config.mjs 定位注入点（既无 experimental 块，也无 `const nextConfig = {`）。');
  }

  writeFileSync(cfgPath, cfg, 'utf-8');
  const chk = spawnSync(process.execPath, ['--check', cfgPath], { stdio: 'inherit' });
  if (chk.status !== 0) fail('注入后副本 next.config.mjs 语法校验失败（node --check）。');
  log('已注入 experimental.outputFileTracingRoot 并通过 node --check');
}

function hasCmd(cmd) {
  // Windows 用 `where`，类 Unix 用 `which`；shell:true 以便解析 .cmd。
  const probe = process.platform === 'win32' ? 'where' : 'which';
  const r = spawnSync(probe, [cmd], { shell: true, stdio: 'ignore' });
  return r.status === 0;
}

// 步骤 4：扁平安装依赖（无符号链接）。优先 pnpm hoisted 复用本地 store；npm install --include=dev 兜底。
function installDeps() {
  const installEnv = { ...process.env, NODE_ENV: 'development' }; // 强制纳入 devDeps（next build 需 ts/tailwind/postcss）

  if (hasCmd('pnpm')) {
    log('依赖安装：pnpm（node-linker=hoisted，复用本地 store、免重复下载、无符号链接）…');
    // 写副本本地 .npmrc 钉死 hoisted 链接策略（步骤 2 已把复制来的 .npmrc 排除，这里是全新文件）。
    writeFileSync(join(BUILD, '.npmrc'), 'node-linker=hoisted\n', 'utf-8');
    const r = spawnSync(
      'pnpm',
      ['install', '--ignore-workspace', '--prod=false', '--prefer-offline', '--config.node-linker=hoisted'],
      { cwd: BUILD, shell: true, stdio: 'inherit', env: installEnv },
    );
    if (r.status === 0) return;
    log('（pnpm 安装失败，回退 npm install）');
    // 回退前删掉 pnpm 的 .npmrc，避免 npm 误读 hoisted 等 pnpm 专属键。
    try {
      rmSync(join(BUILD, '.npmrc'), { force: true });
    } catch {
      /* 忽略 */
    }
  }

  return installWithNpm(installEnv);
}

function installWithNpm(installEnv) {
  log('依赖安装：npm install --include=dev（扁平真实 node_modules，无符号链接）…');
  const base = ['install', '--include=dev', '--no-audit', '--no-fund', '--prefer-offline', '--loglevel=warn'];
  let r = spawnSync('npm', base, { cwd: BUILD, shell: true, stdio: 'inherit', env: installEnv });
  if (r.status !== 0) {
    log('（npm install 失败，重试一次 --legacy-peer-deps 以防 peer 误判）');
    r = spawnSync('npm', [...base, '--legacy-peer-deps'], {
      cwd: BUILD,
      shell: true,
      stdio: 'inherit',
      env: installEnv,
    });
  }
  if (r.status !== 0) {
    fail(
      '隔离副本依赖安装失败。请检查：网络/镜像可达性（本机默认 npmmirror）、peer 依赖冲突。\n' +
        `  可手动排查：cd "${BUILD}" && npm install --include=dev`,
    );
  }
}

// 步骤 5：standalone 构建（BUILD/node_modules 扁平无符号链接 → 无 EPERM、无需任何系统特权）。
function runNextBuild() {
  const nextBin = join(BUILD, 'node_modules', 'next', 'dist', 'bin', 'next');
  if (!existsSync(nextBin)) {
    fail(`未在隔离副本找到 next 可执行脚本：${nextBin}（依赖安装可能未成功）。`);
  }
  log('next build（standalone, PY_BACKEND_URL=http://127.0.0.1:18760, cwd=.web-build）…');
  const res = spawnSync(process.execPath, [nextBin, 'build'], {
    cwd: BUILD,
    stdio: 'inherit',
    env: {
      ...process.env,
      ELECTRON_RUN_AS_NODE: '1', // 若由 Electron 自带 Node 调用本脚本，确保 next 子进程亦为纯 node
      NEXT_OUTPUT_STANDALONE: '1',
      PY_BACKEND_URL: 'http://127.0.0.1:18760', // 构建期烤入 IPv4（standalone rewrites 运行期改不了，P1-5/R14）
      NODE_ENV: 'production',
    },
  });
  if (res.status !== 0) {
    console.error(
      '[build-web] ✗ next build 失败。\n' +
        '  本方案已用隔离扁平 node_modules 根除 EPERM symlink；若仍报 EPERM，请确认 .web-build/node_modules\n' +
        '  确为扁平真实文件（非 pnpm 符号链接）——可加 --clean 强制重装后重试。',
    );
    process.exit(res.status || 1);
  }
}

// 步骤 6：装配 standalone 产物 → desktop/resources/web/（契约与迭代 1 一致，主进程零改动）。
function assemble() {
  if (!existsSync(STANDALONE)) {
    fail(`未生成 standalone 产物：${STANDALONE}（确认 next.config.mjs 的 output 受 NEXT_OUTPUT_STANDALONE 控制）`);
  }
  const serverJsAbs = findServerJs(STANDALONE);
  if (!serverJsAbs) {
    fail(`在 ${STANDALONE} 内未找到 server.js`);
  }
  const relServer = relative(STANDALONE, serverJsAbs); // tracing root 钉准时 "server.js"；嵌套时为相对子路径
  log(`standalone server.js：${relServer}`);

  const stage = `${RES_WEB}.staging-${process.pid}`;
  if (existsSync(stage)) rmSync(stage, { recursive: true, force: true });
  mkdirSync(stage, { recursive: true });
  cpSync(STANDALONE, stage, { recursive: true });

  // server.js 在 resources/web 中的同级目录（standalone 不含 .next/static 与 public，须手动补，Next 文档）
  const serverDirInStage = join(stage, dirname(relServer));
  const staticSrc = join(BUILD_NEXT, 'static');
  if (!directoryHasEntries(staticSrc)) {
    rmSync(stage, { recursive: true, force: true });
    fail(`未找到可打包的 .next/static：${staticSrc}`);
  }
  const staticDst = join(serverDirInStage, '.next', 'static');
  mkdirSync(join(serverDirInStage, '.next'), { recursive: true });
  cpSync(staticSrc, staticDst, { recursive: true });
  if (!directoryHasEntries(staticDst)) {
    rmSync(stage, { recursive: true, force: true });
    fail(`装配后的 .next/static 为空：${staticDst}`);
  }
  log('已拷 .next/static');

  const publicSrc = join(BUILD, 'public');
  if (existsSync(publicSrc)) {
    cpSync(publicSrc, join(serverDirInStage, 'public'), { recursive: true });
    log('已拷 public');
  }

  const serverJsInStage = join(stage, relServer);
  if (!existsSync(serverJsInStage)) {
    rmSync(stage, { recursive: true, force: true });
    fail(`装配后的 server.js 不存在：${serverJsInStage}`);
  }
  if (!directoryHasEntries(join(dirname(serverJsInStage), '.next', 'static'))) {
    rmSync(stage, { recursive: true, force: true });
    fail('manifest 指向的 server.js 旁缺少 .next/static');
  }

  const manifest = {
    serverJs: relServer.split(sep).join('/'), // POSIX 化，跨平台一致
    builtAt: new Date().toISOString(),
    nextVersion: '14.2.15',
    buildMode: 'hoisted-isolated', // 标注隔离扁平构建方式；主进程忽略未知字段，向后兼容
  };
  writeFileSync(join(stage, 'web-server.json'), JSON.stringify(manifest, null, 2), 'utf-8');
  replaceDirectory(stage, RES_WEB);
  log(`✓ 写 web-server.json：${JSON.stringify(manifest)}`);
}

function main() {
  log(`隔离扁平构建开始（BUILD=${BUILD}${CLEAN ? '，--clean' : ''}）`);
  prepareBuildDir();
  injectTracingRoot();
  installDeps();
  runNextBuild();
  assemble();
  log('✓ build:web 完成（无需开发者模式/管理员特权）。');
}

main();
