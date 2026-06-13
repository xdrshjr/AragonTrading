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

> ✅ **无需开发者模式 / 管理员特权**。`build:web` 不再原地用 pnpm 符号链接的 `node_modules` 跑 standalone，
> 而是先在隔离副本 `.web-build/` 里做一次**扁平安装**（无符号链接的真实 `node_modules`），再 `next build`——
> 这样 Next 的 `copyTracedFiles` 全程只复制真实文件、从不 `fs.symlink`，Windows 的符号链接特权约束（EPERM）
> 从根上消失（方案见 `docs/plans/desktop-packaging-build-hardening/spec.md`）。

三步：

| 步骤 | 命令 | 产物 |
|---|---|---|
| `build:backend` | `python scripts/build-backend.py` | PyInstaller onedir 后端 exe → `resources/backend/`（含自检冒烟 `/api/health`） |
| `build:web` | `node scripts/build-web.mjs` | 隔离副本 `.web-build/` 扁平安装 + Next standalone（构建期 `PY_BACKEND_URL=http://127.0.0.1:18760`）→ `resources/web/` + `web-server.json` |
| `electron-builder` | `dist:only` | `dist/` 下 Windows NSIS 安装包 + portable |

> ⏱ **首次 `build:web` 较慢**：会在 `.web-build/` 做一次扁平依赖安装（探测到 pnpm 时复用本地 store、走硬链接免下载，
> 否则 `npm install` 全量下载），并占用约数百 MB 磁盘。`.web-build/` 已 git 忽略、可随时删除（下次自重建）；
> 二次构建复用其 `node_modules` 显著更快。需从零重装可跑 `npm run build:web:clean`。

**快速冒烟**（不出安装包，只出免安装目录；**务必用 `smoke` 而非裸 `pack`**）：

```powershell
npm run smoke       # = build:backend → build:web → electron-builder --dir
```

> ⚠️ `npm run pack`（`electron-builder --dir`）是**纯打包步**，不会触发 `build:backend`/`build:web`；
> 单独跑会因缺 `resources/{backend,web}` 而失败或打出空壳。冒烟一律走 `npm run smoke`。

打包后的应用：DB 落 `%APPDATA%/Aragon Trading/aragon.db`，日志落 `%APPDATA%/Aragon Trading/logs/`。
目标机无需预装 Python / Node / pnpm。

## 发布验收清单（`npm run verify:dist` —— 让「确保可用 / 数据真实可用」可机器复验）

「打包成功」≠「打包出来的东西真的能用、且用的是真实数据」。`verify:dist` 驱动**已构建的 `dist/win-unpacked/`
真实产物**（后端 exe + Next standalone server.js），用 HTTP 断言把全局目标的每条子句映射到一条可执行校验
（设计见 `docs/plans/desktop-release-acceptance/spec.md`）：

| 全局目标子句 | 验收手段 | 通过判据 |
|---|---|---|
| 能成功**打包** | `npm run smoke`（或 `npm run dist`） | 出 `win-unpacked/`（与/或安装包），无 EPERM、无报错退出 |
| **可用**（起得来） | `npm run verify:dist` 后端/前端就绪步 | 后端 60s 内 `/api/health` 200；前端 90s 内 `/` 200 `text/html` |
| **数据真实可用** | `npm run verify:dist` candles 步 | `/api/candles` 头 `X-Aragon-Data-Source: live` + 形状（≥50 根、ts 升序秒级、c>0）；且 `/api/health.cachedFallbackEnabled===false` |
| **功能正常**（端到端集成） | `npm run verify:dist` BFF 步 | 前端 `/api/py/health` 200（standalone rewrites 代理真打到后端） |
| 退出**干净**（无孤儿占端口） | `npm run verify:dist` 拆解步 | 拆解后 18760/18761 轮询 ≤5s 内重新可绑 |

**命令速查（Windows / PowerShell）：**

```powershell
cd desktop
npm run check            # 静态语法校验（含 verify-dist.mjs）
npm run release:check    # 一条龙：构建免安装产物（smoke）→ 立刻端到端验收（联网，要求 live 真实数据）
# 仅对已构建产物再验收（不重新构建）：
npm run verify:dist
# 离线 / 受限网络（仍禁假数据，只接受诚实 5xx）：
npm run verify:dist -- --allow-okx-down
# 排障：保留子进程不拆解（自行 taskkill 释放 18760/18761）：
npm run verify:dist -- --keep-running
# 重生成品牌图标（改图标时）：
npm run icons
```

> **数据真实是硬门**：默认必须实拿 `X-Aragon-Data-Source: live` + 形状校验 + `cachedFallbackEnabled===false`
> 三者同时满足才 PASS；缓存 / 假数据冒充 live 一律 FAIL。须先**导出**代理环境变量
> （`HTTP_PROXY/HTTPS_PROXY=http://127.0.0.1:7897`，非仅开系统代理开关）OKX 方可达；本机 shell 启动已自动导出。
>
> **退出码**（便于脚本定位）：`0`=PASS/WARN-PASS；`10`=产物缺失（先 `smoke`/`dist`）；`11`=端口被占；
> `20`=后端未就绪；`21`=health 非 ok；`22`=candles 非 live（且未 `--allow-okx-down`）；`23`=K 线形状不合理；
> `24`=`cachedFallbackEnabled!==false`；`30`=前端未就绪；`31`=前端首页异常；`32`=BFF 代理异常；
> `40`=拆解后轮询 5s 仍端口未释放。
>
> ⚠️ `verify:dist` **不进托管 CI**（需 Windows + 真实网络 + 分钟级构建 + 120 MB Electron 下载）；它是**本地/发布前**
> 手动验收门。CI 只新增 `desktop` 静态校验 job（`npm run check`）。窗口 / 菜单「关于」/ 退出等 GUI 交互由
> `npm run dev` 人工核对（弹原生窗口、5 策略可切、K 线真实；菜单「帮助 → 关于 Aragon Trading」显示版本与数据来源声明）。

## 三、跨平台

- **Windows 为一等公民**（本轮验收平台）。
- PyInstaller **不能交叉编译**：mac `.dmg` / linux `AppImage` 配置位已齐备，但须**在对应平台**各自构建。
- 代码签名 / 公证 / 自动更新本轮不做（`electron-builder.yml` 留有占位）。

## 四、排障

| 现象 | 处理 |
|---|---|
| `dev` 报「缺少 Python 虚拟环境」 | 先在仓库根 `pnpm setup` |
| 端口被占（启动弹对话框） | `Get-NetTCPConnection -LocalPort 18760,18761 \| Select OwningProcess` 后 `Stop-Process -Id <PID> -Force` |
| `build:web` 失败、EPERM symlink | 新流程已用隔离扁平构建根除此问题，正常不应再现。若仍遇到，先 `npm run build:web:clean` 强制重装隔离副本；**兜底**：启用 Windows「开发人员模式」（设置 → 隐私和安全性 → 开发者选项）或以管理员运行 |
| `build:web` 依赖安装失败（网络/镜像） | 隔离副本 `.web-build/` 装依赖失败：检查网络/镜像；本机有 pnpm 时优先复用其 store（免下载）；可手动 `cd .web-build ; npm install --include=dev` 排查 |
| `electron-builder` 卡在下载 `electron-v<ver>-win32-x64.zip`（EOF / 超时） | 受限网络下 GitHub / 镜像的大文件（约 111 MB）易断流。优先复用 `npm install` 已落地的本地 Electron，跳过下载：`npm run dist:only -- --config.electronDist=node_modules/electron/dist`；或设镜像后重试：`$env:ELECTRON_MIRROR='https://registry.npmmirror.com/-/binary/electron/' ; npm run dist`。此步与 EPERM 无关，纯属二进制下载网络问题 |
| 取不到 OKX 真实数据 | 设置系统代理（README：本机 Clash `127.0.0.1:7897`），桌面会透传 `HTTP(S)_PROXY` |
| 后端 exe 启动即崩 | 多为 PyInstaller 漏 `hiddenimports`；见 `aragon-quant.spec`，`build:backend` 末尾已自检 `/api/health` |

## 五、端口同步点（务必同步！）

固定端口 **18760（后端）/ 18761（前端）**。改动须同步以下**五处**（前四处见 `scripts/dev.py` 注释，本目录为第 5 处）：

1. `apps/web/package.json` 的 `dev` 脚本（`next dev -p 18761`）
2. `apps/web/next.config.mjs` 的 `PY_BACKEND_URL` 默认值
3. `apps/web/lib/ws.ts` 的 `NEXT_PUBLIC_PY_WS_URL` 默认值
4. `docker-compose.yml` 与两个 Dockerfile
5. **`desktop/src/lib/config.js`** 的 `BACKEND_PORT` / `WEB_PORT`（本桌面外壳）
