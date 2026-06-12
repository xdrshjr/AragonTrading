/** @type {import('next').NextConfig} */

// 后端 BFF 代理目标 (spec §4.1)。把 /api/py/* 转发到 Python FastAPI 的 /api/*。
// 注意：spec §9.1 的后端真实路径含 /api 前缀，故此处补上 /api（见 spec 实施过程发现的方案缺陷 D1）。
const PY_BACKEND_URL = process.env.PY_BACKEND_URL || "http://localhost:8000";

const nextConfig = {
  reactStrictMode: true,
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
