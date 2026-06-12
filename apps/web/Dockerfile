# Aragon Trading — 前端镜像（多阶段：Node20 + Next standalone）(spec §4.5 / F5)。
#
# 构建上下文 = apps/web（锁文件 apps/web/pnpm-lock.yaml，C3）。上下文不含仓库根
# pnpm-workspace.yaml，故 Next 不会探到 monorepo 工作区根，standalone 入口为**扁平**的
# /app/.next/standalone/server.js（避免嵌套路径致 MODULE_NOT_FOUND，C3 / R5）。

# ---------- builder ----------
FROM node:20-alpine AS builder
WORKDIR /app
RUN npm install -g pnpm@9

# NEXT_PUBLIC_* 在 build 期 baked 进前端 bundle；用"浏览器可达"地址（compose 注入，R5）
ARG NEXT_PUBLIC_PY_WS_URL=ws://localhost:8000
ENV NEXT_PUBLIC_PY_WS_URL=$NEXT_PUBLIC_PY_WS_URL
# Linux 容器内符号链接可用，开启 standalone 产物（Windows 本机经 env 关闭以避 EPERM，spec §11 D2）
ENV NEXT_OUTPUT_STANDALONE=1

# 先装依赖（利用层缓存）
COPY package.json pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile

COPY . .
RUN pnpm build

# ---------- runner ----------
FROM node:20-alpine AS runner
WORKDIR /app
ENV NODE_ENV=production
ENV PORT=3000
# PY_BACKEND_URL 由 compose 注入（Next 服务端 rewrite 代理目标，默认见 next.config.mjs）

# standalone 自带最小化 node_modules 与扁平 server.js；再补 .next/static（无 public 目录）
COPY --from=builder /app/.next/standalone ./
COPY --from=builder /app/.next/static ./.next/static

EXPOSE 3000
CMD ["node", "server.js"]
