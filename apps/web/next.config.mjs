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
