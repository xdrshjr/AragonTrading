// build:web (spec §4.6 ② / R1 / R2)：
//  1) 在 apps/web 下以 NEXT_OUTPUT_STANDALONE=1 且 PY_BACKEND_URL=http://127.0.0.1:18760 跑 next build
//     —— PY_BACKEND_URL 必须构建期设置（standalone 把 rewrites 烤进 required-server-files.json，运行期改不了，P1-5）；
//        显式用 IPv4 避免 Windows localhost→::1 而后端只绑 127.0.0.1（R14）。
//  2) 递归定位 .next/standalone 内的 server.js（monorepo 可能嵌套，D7 / R2，不硬编码）；
//  3) 把整棵 standalone/ 拷到 desktop/resources/web/，再把 .next/static、public 拷到 server.js 同级；
//  4) 写 desktop/resources/web/web-server.json（记录 server.js 相对路径，供主进程读取）。
//
// 用 Electron 自带 Node / 系统 Node 直接跑 next 可执行脚本（§11 实施发现：规避 spawn pnpm.cmd 限制）。

import { spawnSync } from 'node:child_process';
import {
  cpSync,
  existsSync,
  mkdirSync,
  readdirSync,
  rmSync,
  writeFileSync,
} from 'node:fs';
import { dirname, join, relative, sep } from 'node:path';
import { fileURLToPath } from 'node:url';

const SCRIPTS_DIR = dirname(fileURLToPath(import.meta.url));
const DESKTOP = dirname(SCRIPTS_DIR);
const REPO = dirname(DESKTOP);
const WEB = join(REPO, 'apps', 'web');
const NEXT_DIR = join(WEB, '.next');
const STANDALONE = join(NEXT_DIR, 'standalone');
const RES_WEB = join(DESKTOP, 'resources', 'web');
const NEXT_BIN = join(WEB, 'node_modules', 'next', 'dist', 'bin', 'next');

function log(msg) {
  console.log(`[build-web] ${msg}`);
}

function fail(msg) {
  console.error(`[build-web] ✗ ${msg}`);
  process.exit(1);
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

function runNextBuild() {
  if (!existsSync(NEXT_BIN)) {
    fail(`未找到 next 可执行脚本：${NEXT_BIN}\n请先在 apps/web 执行 \`pnpm install\`。`);
  }
  log('next build（standalone, PY_BACKEND_URL=http://127.0.0.1:18760）…');
  const res = spawnSync(process.execPath, [NEXT_BIN, 'build'], {
    cwd: WEB,
    stdio: 'inherit',
    env: {
      ...process.env,
      ELECTRON_RUN_AS_NODE: '1', // 若由 Electron 自带 Node 调用本脚本，确保 next 子进程亦为 node
      NEXT_OUTPUT_STANDALONE: '1',
      PY_BACKEND_URL: 'http://127.0.0.1:18760',
      NODE_ENV: 'production',
    },
  });
  if (res.status !== 0) {
    console.error(
      '[build-web] ✗ next build 失败。\n' +
        '  若为 Windows EPERM symlink（pnpm 符号链接 + 未开发者模式，R1）：\n' +
        '  请到「设置 → 隐私和安全性 → 开发者选项」启用「开发人员模式」，或以管理员运行后重试。',
    );
    process.exit(res.status || 1);
  }
}

function assemble() {
  if (!existsSync(STANDALONE)) {
    fail(`未生成 standalone 产物：${STANDALONE}（确认 next.config.mjs 的 output 受 NEXT_OUTPUT_STANDALONE 控制）`);
  }
  const serverJsAbs = findServerJs(STANDALONE);
  if (!serverJsAbs) {
    fail(`在 ${STANDALONE} 内未找到 server.js`);
  }
  const relServer = relative(STANDALONE, serverJsAbs); // e.g. "server.js" 或 "apps/web/server.js"
  log(`standalone server.js：${relServer}`);

  // 清并整棵拷贝 standalone → resources/web
  if (existsSync(RES_WEB)) rmSync(RES_WEB, { recursive: true, force: true });
  mkdirSync(RES_WEB, { recursive: true });
  cpSync(STANDALONE, RES_WEB, { recursive: true });

  // server.js 在 resources/web 中的位置与同级目录（standalone 不含 .next/static 与 public，须手动补，Next 文档）
  const serverDirInRes = join(RES_WEB, dirname(relServer));
  const staticSrc = join(NEXT_DIR, 'static');
  if (existsSync(staticSrc)) {
    cpSync(staticSrc, join(serverDirInRes, '.next', 'static'), { recursive: true });
    log('已拷 .next/static');
  } else {
    log('（警告）未找到 .next/static');
  }
  const publicSrc = join(WEB, 'public');
  if (existsSync(publicSrc)) {
    cpSync(publicSrc, join(serverDirInRes, 'public'), { recursive: true });
    log('已拷 public');
  }

  const manifest = {
    serverJs: relServer.split(sep).join('/'), // POSIX 化，跨平台一致
    builtAt: new Date().toISOString(),
    nextVersion: '14.2.15',
  };
  writeFileSync(join(RES_WEB, 'web-server.json'), JSON.stringify(manifest, null, 2), 'utf-8');
  log(`✓ 写 web-server.json：${JSON.stringify(manifest)}`);
}

function main() {
  runNextBuild();
  assemble();
  log('✓ build:web 完成。');
}

main();
