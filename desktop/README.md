# Aragon Trading — 桌面端（Electron）

把既有 Web 版（`apps/web` Next.js + `services/quant` FastAPI）封装成可独立分发、双击即用的桌面应用。
Electron 主进程统一编排：拉起 Python 后端（:18760）+ Next 服务（:18761），两者就绪后开窗加载
`http://127.0.0.1:18761`，退出时优雅终止并兜底强杀整棵进程树。**纯增量、对 `apps/web` /
`services/quant` 零源码改动**（固定端口 18760/18761 + 既有 env 开关即可，见仓库 `docs/plans/desktop-electron-packaging/spec.md`）。

> 真实数据硬约束不变：OKX 不可达时沿用既有「无法连接 OKX」诚实错误态，**绝不假数据兜底**。

---

## 一、开发调试模式（`npm run dev` 起 Electron 窗口，而非浏览器）

**前置**（在仓库根，一次性）：

```powershell
pnpm setup          # 建 services/quant/.venv + 装后端依赖 + apps/web 装依赖（= python scripts/dev.py setup）
```

**启动**（在 `desktop/` 下）：

```powershell
cd desktop
npm install         # 拉 electron / electron-builder 等（仅 devDependencies）
npm run dev         # 拉起 Electron 桌面调试窗口（自动起后端 + 前端 + DevTools）
```

- 后端：复用仓库 `services/quant/.venv` 跑 `uvicorn app.main:app --port 18760 --reload`。
- 前端：用 Electron 自带 Node 跑 `apps/web` 内的 `next dev -p 18761`（热更新可用）。
- 改前端代码 → 窗口热更新即时可见。关闭窗口后两个端口应无残留监听（无孤儿进程）。

## 二、生产打包模式（`npm run dist` 出安装包）

```powershell
cd desktop
npm run dist        # = build:backend → build:web → electron-builder（run-s 串接）
```

三步：

| 步骤 | 命令 | 产物 |
|---|---|---|
| `build:backend` | `python scripts/build-backend.py` | PyInstaller onedir 后端 exe → `resources/backend/`（含自检冒烟 `/api/health`） |
| `build:web` | `node scripts/build-web.mjs` | Next standalone（构建期 `PY_BACKEND_URL=http://127.0.0.1:18760`）→ `resources/web/` + `web-server.json` |
| `electron-builder` | `dist:only` | `dist/` 下 Windows NSIS 安装包 + portable |

**快速冒烟**（不出安装包，只出免安装目录；**务必用 `smoke` 而非裸 `pack`**）：

```powershell
npm run smoke       # = build:backend → build:web → electron-builder --dir
```

> ⚠️ `npm run pack`（`electron-builder --dir`）是**纯打包步**，不会触发 `build:backend`/`build:web`；
> 单独跑会因缺 `resources/{backend,web}` 而失败或打出空壳。冒烟一律走 `npm run smoke`。

打包后的应用：DB 落 `%APPDATA%/Aragon Trading/aragon.db`，日志落 `%APPDATA%/Aragon Trading/logs/`。
目标机无需预装 Python / Node / pnpm。

## 三、跨平台

- **Windows 为一等公民**（本轮验收平台）。
- PyInstaller **不能交叉编译**：mac `.dmg` / linux `AppImage` 配置位已齐备，但须**在对应平台**各自构建。
- 代码签名 / 公证 / 自动更新本轮不做（`electron-builder.yml` 留有占位）。

## 四、排障

| 现象 | 处理 |
|---|---|
| `dev` 报「缺少 Python 虚拟环境」 | 先在仓库根 `pnpm setup` |
| 端口被占（启动弹对话框） | `Get-NetTCPConnection -LocalPort 18760,18761 \| Select OwningProcess` 后 `Stop-Process -Id <PID> -Force` |
| `build:web` 失败、EPERM symlink | 启用 Windows「开发人员模式」（设置 → 隐私和安全性 → 开发者选项），或以管理员运行 |
| 取不到 OKX 真实数据 | 设置系统代理（README：本机 Clash `127.0.0.1:7897`），桌面会透传 `HTTP(S)_PROXY` |
| 后端 exe 启动即崩 | 多为 PyInstaller 漏 `hiddenimports`；见 `aragon-quant.spec`，`build:backend` 末尾已自检 `/api/health` |

## 五、端口同步点（务必同步！）

固定端口 **18760（后端）/ 18761（前端）**。改动须同步以下**五处**（前四处见 `scripts/dev.py` 注释，本目录为第 5 处）：

1. `apps/web/package.json` 的 `dev` 脚本（`next dev -p 18761`）
2. `apps/web/next.config.mjs` 的 `PY_BACKEND_URL` 默认值
3. `apps/web/lib/ws.ts` 的 `NEXT_PUBLIC_PY_WS_URL` 默认值
4. `docker-compose.yml` 与两个 Dockerfile
5. **`desktop/src/lib/config.js`** 的 `BACKEND_PORT` / `WEB_PORT`（本桌面外壳）
