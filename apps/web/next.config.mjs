/** @type {import('next').NextConfig} */

// 后端 BFF 代理目标 (spec §4.1)。把 /api/py/* 转发到 Python FastAPI 的 /api/*。
// 注意：spec §9.1 的后端真实路径含 /api 前缀，故此处补上 /api（见 spec 实施过程发现的方案缺陷 D1）。
const PY_BACKEND_URL = process.env.PY_BACKEND_URL || "http://localhost:18760";

const nextConfig = {
  reactStrictMode: true,
  // F5：standalone 产物用于容器瘦镜像（只影响 next build，不影响 next dev / rewrite）。
  // 仅在容器 / CI（Linux）构建时经 NEXT_OUTPUT_STANDALONE=1 开启——Windows 本机 pnpm 的
  // node_modules 符号链接结构会令 standalone 的 copyTracedFiles 在无 Developer Mode 时报
  // EPERM symlink，故本机 `pnpm build` 默认走普通产物保持绿；Linux 容器 / CI 无此限制
  // （实施发现，见 spec §11 D2）。
  output: process.env.NEXT_OUTPUT_STANDALONE === "1" ? "standalone" : undefined,
  // 桌面端「回测失败」根因修复：Next 14.2.15 standalone 代理对每个 /api/py/* 请求默认施加
  // 30s proxyTimeout（proxy-request.js:31 ← router-server.js:269 ← 未设的
  // config.experimental.proxyTimeout），单次墙钟 > 30s 的长回测被其在传输层重置
  // （socket hang up / ECONNRESET），UI 误报失败而后端实际已跑完并落库 done。此处抬高到
  // 300000ms（5 分钟，覆盖有界回测最坏墙钟 ~1~3min）。取值必须为正数：schema 为
  // z.number().gte(0)，null 非法；0 会被 `proxyTimeout || 30000` 退回 30s。改动须重建
  // build:web 才能烤进 standalone 的 required-server-files.json。不影响 WS（ws.ts:6 直连）。
  // 详见 docs/diagnoses/desktop-backtest-proxy-timeout-socket-hang-up/analysis.md
  experimental: {
    proxyTimeout: 300000,
  },
  async rewrites() {
    return [
      {
        source: "/api/py/:path*",
        destination: `${PY_BACKEND_URL}/api/:path*`,
      },
    ];
  },
};

export default nextConfig;
