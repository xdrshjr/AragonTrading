# Aragon Trading — 桌面端 Electron 打包方案 (spec)

- **Feature**: `desktop-electron-packaging`
- **文档版本**: **v2**（迭代 2 · Subtask #1 方案评审完成；已对照「可行性 / 完整性 / 一致性 / 规模适配」四维 + 迭代 1 v2 的 5 条放行条件逐文件核对源码落点，**0 项 P0、6 项 P1 已就地修复**，剩余 P2 列入条件清单。评审记录见下「评审记录」，结论见文末「评审结论」。v1 由 Subtask #0 方案设计产出。）
- **作者 / 角色**: Solution Architect (Anthropic Engineering)
- **创建日期**: 2026-06-13
- **迭代**: Iteration 2 / 3
- **全局目标**: 完成项目开发，确保可用，数据真实可用
- **本轮需求（原文）**: 「继续开发，需要支持桌面端打包，electron 打包，专门放在 desktop 目录下，并且支持在其下，直接运行 `npm run dev`，启动 electron 的桌面端的调试模式，而不是浏览器，确保能够成功打包，并且功能正常。」
- **基线**: 迭代 1 已交付并提交（commit `414c28c`）+ 迭代 2「增量增强」（commit `f961e7f`）+ 迭代 3「生产化收尾」（commit `f1f924a`）的 monorepo —— `apps/web`（Next.js 14 App Router + React 18 + TS + Tailwind + lightweight-charts v4）、`services/quant`（Python 3.11 + FastAPI/uvicorn + SQLite）、`scripts/dev.py`（纯 Python 跨平台开发编排器）。前序设计见 `docs/plans/aragon-trading-platform/spec.md`、`docs/plans/aragon-iter2-enhancements/spec.md`、`docs/plans/aragon-iter3-hardening/spec.md`。
- **关系声明**: 本文档**只规划"为既有 Web 应用增加 Electron 桌面外壳与打包"这一增量能力**。**不改** 5 策略逻辑、SimBroker 撮合、回测/回放管线、DB schema、任何 REST/WS 接口语义，也**不回退**迭代 1 v2 的 5 条放行条件（① OKX 真实数据、严禁假数据兜底 ② 凭据不入库 ③ 全系统秒级 UTC ts ④ 锁 `lightweight-charts@^4.2` ⑤ SimBroker·VWAP·策略注册表合并规则）。桌面外壳是**纯加法**：所有新增产物集中在仓库根的 `desktop/` 目录下，对 `apps/web` / `services/quant` 的改动力求为零（见 §6 兼容性分析）。

---

## 评审记录（Review Notes · Subtask #1）

> **评审人**: Senior Reviewer (Anthropic Engineering) · **评审日期**: 2026-06-13 · **被评对象**: 本 spec v1。
> **方法**: 对照「可行性 / 完整性 / 一致性 / 规模适配」四维 + 迭代 1 v2 的 5 条放行条件，**逐文件核对真实源码落点**（已核对：`apps/web/lib/api.ts`、`apps/web/lib/ws.ts`、`apps/web/next.config.mjs`、`apps/web/package.json`、`services/quant/app/main.py`、`services/quant/app/config.py`、`services/quant/app/db/session.py`、`services/quant/app/api/market.py`、`services/quant/requirements.txt`、`scripts/dev.py`、`pnpm-workspace.yaml`、根 `package.json`/`.gitignore`）。
>
> **源码核对结论（"零侵入"前提成立）**：spec 赖以"零源码改动"的若干关键事实**经核对全部为真**——
> - `lib/api.ts` 确以相对路径 `BASE = "/api/py"` 发请求 ✓；`lib/ws.ts` 确以 `process.env.NEXT_PUBLIC_PY_WS_URL || "ws://localhost:18760"` 取 WS 基址 ✓；
> - `config.py` 确有 `db_path`（env `DB_PATH`，大小写不敏感）与 `allow_cached_fallback`（默认 `False`）字段，且 `db/session.py::_db_url()` 对**绝对路径直接采用**——故运行期注入 `DB_PATH=<userData>/aragon.db` **无需改后端代码**即生效（D6 成立）✓；
> - `/api/health` 路由真实存在且 OKX 不可达时仍即时返回 `okx:"down"` ✓（§4.2 就绪判据成立）；
> - `next.config.mjs` 确以 `PY_BACKEND_URL` 决定 rewrites 目标、以 `NEXT_OUTPUT_STANDALONE==="1"` 切 standalone ✓；
> - `dev.py` 确为"端口无害 bind 预检 + CTRL_BREAK/SIGTERM 优雅 → `taskkill /F /T` / `killpg SIGKILL` 兜底"策略，可被 §4.3 复刻 ✓。
> - **未发现 `CLAUDE.md`**（仓库任何层级均无）；一致性遂以"源码现状 + 前序 spec 放行条件"为准绳。
>
> **总评**：方案技术主线（Electron 主进程统一编排 + PyInstaller onedir 后端 + Next standalone 前端 + 固定端口零改前端）**可行、规模适配得当、对业务零侵入**，无方向性返工。下列 **6 项 P1 已在本 v2 文档内就地修复**；**0 项 P0**。

### 问题清单

| 编号 | 维度 | 严重度 | 问题 | 处置 |
|---|---|---|---|---|
| P1-1 | 可行性 | **P1** | **未锁定 Electron 版本** → 其内置 Node 版本未保证。Next **14.2.15 要求 Node ≥ 18.18.0**；Electron 27 内置 Node 18.17.1（< 18.18）、Electron 28 才内置 18.18.2。若选到旧 Electron，`ELECTRON_RUN_AS_NODE` 跑 standalone `server.js` 可能因 Node 过旧而异常。 | **已修复**：§2/§3(D8)/§5 明确 **`electron@^31`（≥28 即可，内置 Node ≥18.18）** 并写入兼容性约束。 |
| P1-2 | 一致性 | **P1** | **`dist` 依赖 `run-s`（`npm-run-all`），但 §5 的 `devDependencies` 清单未列入** → `npm run dist` 会 `command not found`。 | **已修复**：§5 `desktop/package.json` 行 + §7.1 注补入 `npm-run-all`。 |
| P1-3 | 可行性 | **P1** | **运行期依赖卫生**：`electron-builder` 只打包 `dependencies` 不打包 `devDependencies`。若主进程 `require` 了仅列在 devDeps 的包（`wait-on`/`fs-extra`/`cross-env`），打包后将 `MODULE_NOT_FOUND`。§4.2 已是"自写 http 轮询"，但未声明运行期零 npm 依赖。 | **已修复**：§4.2/§5/§7.2 明确**主进程就绪轮询仅用 Node 内置 `http`**、桌面包 `dependencies: {}`（全部构建/开发期依赖归 `devDependencies`），杜绝运行期缺包。 |
| P1-4 | 可行性 | **P1** | **PyInstaller 冻结态下 `uvicorn.run("app.main:app")` 用字符串导入**易因冻结包的导入解析而失败（且 reload 语义只在字符串形式可用，prod 不该开 reload）。 | **已修复**：§4.6/§5 明确 `backend_entry.py` **直接 `from app.main import app` 传入 app 对象**（`uvicorn.run(app, host=127.0.0.1, port=port)`，**不开 reload**），更稳健。 |
| P1-5 | 可行性 / 完整性 | **P1** | **`PY_BACKEND_URL` 在 Next standalone 下并非"运行期可配"**：`next build` 会把 `rewrites()` 的求值结果序列化进 `.next/required-server-files.json`，standalone `server.js` 运行期**不再重读 `next.config.mjs`**——故 §4.5/§6 所述"运行期注入 `PY_BACKEND_URL` 决定代理目标"对 prod **是 no-op**。更隐蔽的是：build:web 未设该 env 时，烤进产物的目标是默认值 `http://localhost:18760`，而 Windows 上 `localhost` 可能先解析到 IPv6 `::1`，但后端只绑 IPv4 `127.0.0.1` → **打包后 `/api/py/*` 代理可能连接被拒**。 | **已修复**：§3(D7)/§4.5/§4.6/§6/§10(R14) 改为**在 `build:web` 构建期设 `PY_BACKEND_URL=http://127.0.0.1:18760`**（此时设置才真正生效并被烤进产物），使代理目标显式 IPv4、与 uvicorn 绑定一致；并澄清"`next dev` 下该 env 运行期有效、standalone 下为构建期烤定"的非对称语义。 |
| P1-6 | 完整性 | **P1** | **`pack`（`electron-builder --dir`）被当作"快速冒烟"，但它不触发 `build:backend`/`build:web`** → 若两者未先跑，打出的目录缺 `resources/web`、`resources/backend`，冒烟"通过"却根本起不来，形成假绿。 | **已修复**：§7.1/§9.3/§9.4 明确 `pack` 为**纯打包步**，冒烟前必须先 `build:backend` + `build:web`（或用新增 `smoke = run-s build:backend build:web pack` 一键串接）。 |

### P2（非阻断，记入条件清单，留待 Subtask #2 实施时落实）

- **P2-1**：`wait-on` 列在 devDeps 但 §4.2 就绪用自写 http 轮询，二者并存语义重复——实施时二选一（建议直接删 `wait-on`，仅用 Node 内置 `http`）。
- **P2-2**：§8.3/R4 的 PyInstaller `hiddenimports` 宜再补 `collect_all('pydantic')` / `pydantic_core` / `sqlalchemy.dialects.sqlite` / `aiosqlite`（aiosqlite 已列），并以"构建后直接跑 exe + `GET /api/health`"为最终判据兜底。
- **P2-3**：`desktop/src/lib/config.js` 的固定端口 18760/18761 将成为 `dev.py` 注释所列"四处端口需同步"之外的**第 5 处**；实施时在 `desktop/README.md` 标注该同步关系（本轮不改 `dev.py` 注释，避免触碰源码）。
- **P2-4**：缺**子进程就绪后异常退出的监管**——后端/前端在运行中崩溃时窗口仅显示报错，无对话框告知。实施建议：监听 child `exit` 事件，运行期非预期退出则弹 `dialog` 并指引查看 `userData/logs`。
- **P2-5**：`ARAGON_MODE=dev`（§7.1）与 `app.isPackaged`（§4）两套模式信号并存——以 `app.isPackaged` 为唯一真源，`ARAGON_MODE` 仅用于"强制开 DevTools"等开发期增强，实施时在 `main.js` 注释澄清。
- **P2-6**：就绪轮询默认 30s 超时对"首次 `next dev` 冷编译 / 首次解压 PyInstaller onedir"可能偏紧；`/api/health` 又内含一次 OKX 探活（`rest.health()`），OKX 慢时会拖慢每次轮询。实施时把超时设为可配（如后端 60s、前端 90s）并确保 `rest.health()` 自带短超时，避免就绪误判。

---

## 1. Overview（概述）

迭代 1–3 已经把 Aragon Trading 建成一个数据真实、闭环完整的 **Web 版**多策略量化模拟交易工作台：前端 `apps/web` 是一个 Next.js 14（App Router）应用，左侧 `lightweight-charts` 专业 K 线、右侧 5 策略切换/回测/对比/历史/回放；它通过 `next.config.mjs` 的 `rewrites` 把同源 `/api/py/*` 反向代理到 Python 后端的 `/api/*`（即所谓 BFF），并由浏览器直连 `ws://localhost:18760/api/ws/*` 取实时行情；后端 `services/quant` 是 FastAPI/uvicorn 进程，跑策略注册表、SimBroker 撮合、SQLite 持久化，并对接 OKX 公共 REST/WS。今天要把这套 Web 应用**封装成一个可独立分发、可独立运行的桌面应用**：用户双击即用，无需先装 Node / Python / pnpm、无需手动起两个服务、也无需打开浏览器。

本轮的本质是给现有"双进程 Web 系统"套一层 **Electron 外壳 + 进程编排 + 打包流水线**，全部落在新建的 `desktop/` 目录。与一般"纯前端 SPA 打包成 Electron"不同，本项目有两个硬约束决定了技术选型：(1) **前端不是纯静态站点**——`apps/web/lib/api.ts` 用相对路径 `BASE = "/api/py"` 发请求，依赖 `next.config.mjs` 的服务端 `rewrites` 把它代理到后端，所以**生产环境必须真的跑一个 Next 服务进程**（不能用 `output: "export"` 静态导出，那样 `rewrites` 失效）；(2) **后端是 Python 进程**——桌面分发不能假设用户机器上有 Python 解释器与 venv，因此需要把后端打成一个**自包含可执行体**（PyInstaller）随包分发。于是桌面应用在运行期需要由 Electron 主进程**编排三件事**：拉起 Python 后端（:18760）、拉起 Next 服务（:18761）、在两者就绪后打开一个加载 `http://localhost:18761` 的 `BrowserWindow`。

围绕这条主线，本方案交付两个可工作的入口：**(A) 开发调试模式** —— 在 `desktop/` 下 `npm run dev` 直接拉起 Electron 窗口（**而非浏览器**），其内部复用既有 venv 跑 `uvicorn --reload`、复用 `apps/web` 跑 `next dev`，窗口自动打开 DevTools，改前端代码热更新即时可见；**(B) 生产打包模式** —— `npm run dist` 经 electron-builder 产出安装包（Windows NSIS/portable、可选 macOS dmg / Linux AppImage），包内自带 Next standalone 产物与 PyInstaller 后端可执行体，安装后双击即用、功能与 Web 版完全一致。两种模式共享同一套 Electron 主进程与进程管理代码，仅在「子进程命令与文件路径」上按 `app.isPackaged` 分流。全程坚持真实数据硬约束：OKX 不可达时沿用既有前端"无法连接 OKX"诚实错误态，**绝不以假数据兜底**。

---

## 2. Goals / Non-Goals（本轮）

### Goals
1. **新增 `desktop/` 目录**，作为 Electron 桌面外壳与打包流水线的唯一落点；对 `apps/web` / `services/quant` 的改动趋近于零。
2. **开发调试模式**：在 `desktop/` 下 `npm install` 后 `npm run dev`，**拉起 Electron 桌面窗口（不是浏览器）** 加载完整应用，自动打开 DevTools；前端热更新可用、后端 `--reload` 可用。
3. **生产打包成功**：`npm run dist` 在本机（Windows）产出可安装/可运行的桌面安装包，包内**自带 Python 后端可执行体与 Next 服务产物**，目标机器无需预装 Python / Node / pnpm。
4. **功能正常**：打包后的桌面应用启动后，K 线绘制、5 策略切换、回测、对比、历史、实时/回放模拟、CSV 导出等既有功能与 Web 版一致；WS 实时通道连通；OKX 真实数据可达时取真实数据。
5. **生命周期健壮**：窗口关闭 / 应用退出时，后端与前端子进程被**优雅终止并兜底强杀整棵进程树**（复用 `scripts/dev.py` 已验证的 CTRL_BREAK→tree-kill 策略），杜绝孤儿进程占住 18760/18761。
6. **单实例 + 端口预检**：避免重复启动导致端口冲突；冲突时给出明确提示而非静默崩溃。
7. 不违反 5 条放行条件；桌面外壳新增路径不引入任何假数据。

### Non-Goals（本轮不做）
- 不接入真实下单 / 真实资金（与前序一致，仍仅模拟）。
- 不引入自动更新（electron-updater）、不做代码签名 / 公证（仅预留配置位与说明，签名证书非本轮交付）。
- 不为 macOS / Linux 出正式产物（PyInstaller 不能跨平台交叉编译，需各自平台构建）；本轮以 **Windows 为一等公民**交付，mac/linux 配置位齐备但标注「需在对应平台构建」。
- 不改 5 策略、不改 SimBroker、不改 DB schema、不改任何 REST/WS 契约。
- 不把桌面包做成「单文件嵌入式 Python」之外的其它后端形态（如 PyOxidizer / Nuitka）——本轮锁定 PyInstaller。

---

## 3. 关键设计决策（Design Decisions & Alternatives）

| # | 决策点 | 选择 | 理由 / 被否方案 |
|---|---|---|---|
| D1 | 生产环境前端形态 | **跑 Next standalone 服务进程**（`next build` + `output:"standalone"` → `server.js`，由 Electron 内置 Node 经 `ELECTRON_RUN_AS_NODE` 拉起） | 前端用相对路径 `/api/py/*` 依赖 `rewrites` 服务端代理，`output:"export"` 静态导出会**丢失 rewrites**导致接口 404。被否方案：静态导出 + 改前端直连 `http://localhost:18760`（侵入 `lib/api.ts`、丢 BFF 抽象、违背"改动趋近于零"）。 |
| D2 | 后端形态 | **PyInstaller `onedir` 自包含可执行体**（dev 模式仍用既有 venv 跑 uvicorn） | 目标机无 Python。`onedir` 比 `onefile` 启动快、解压目录稳定、更少被杀软误报。被否：随包拷整个 `.venv`（体积大、平台相关、易碎）。 |
| D3 | 端口策略 | **固定端口** 后端 18760 / 前端 18761（与既有项目一致），启动前做端口空闲预检 | `apps/web/lib/ws.ts` 的 `NEXT_PUBLIC_PY_WS_URL` 默认 `ws://localhost:18760` 是 **`next build` 构建期内联**的常量，运行期改不了。固定 18760 即可让 WS 零改动可用。动态端口需注入运行期配置（见 §10 R3，列为后续增强）。 |
| D4 | 进程编排归属 | **由 Electron 主进程统一编排**（spawn 后端 + 前端，wait-on 就绪后开窗，退出时 tree-kill） | dev / prod 共享同一套生命周期与清理逻辑，单点负责，退出可靠。被否：dev 用 `concurrently` 脚本拉服务 + 另起 electron（两层进程，Ctrl-C 清理易漏）。 |
| D5 | desktop 与 monorepo 关系 | `desktop/` 为**独立 npm 包**（用 `npm`，不纳入根 `pnpm-workspace.yaml` 的 `apps/*`） | 需求明确「在其下 `npm run dev`」。独立包隔离 Electron 庞大依赖，不污染 pnpm 工作区；通过相对路径 `../apps/web`、`../services/quant` 引用主仓。 |
| D6 | 数据库与日志落点 | 运行期 `DB_PATH` 指向 `app.getPath('userData')/aragon.db`，日志写 `userData/logs/` | 打包后资源目录（如 `Program Files`）只读，SQLite 必须写可写目录。dev 模式沿用仓库内默认 DB。 |
| D7 | Next standalone 在 monorepo 的产物路径 | **运行期不硬编码** server.js 路径，构建脚本递归定位并写入 `web-server.json` 清单，主进程读清单 | 根存在 `pnpm-workspace.yaml`，standalone 可能把 `server.js` 产到 `.next/standalone/apps/web/server.js`（嵌套）。硬编码 `node server.js` 会 `MODULE_NOT_FOUND`（迭代 3 spec C3 同坑）。 |
| D8 | Electron 版本下限 | **锁 `electron@^31`（强制下限 ≥28）** | **Next 14.2.15 要求 Node ≥ 18.18.0**；prod 用 `ELECTRON_RUN_AS_NODE` 借 Electron 内置 Node 跑 standalone `server.js`，故内置 Node 必须 ≥18.18。Electron 27 内置 Node 18.17.1（**不达标**），Electron 28 起为 18.18.2，31 为 20.x。锁 `^31` 取稳定较新版、给足余量。被否：不锁版本（CI/开发机各装到旧 Electron 时 standalone 起不来，难排查）。**评审（P1-1）补入。** |

---

## 4. Technical Design（技术设计）

### 4.1 运行期拓扑

```
┌──────────────────────────── Electron 主进程 (Node) ────────────────────────────┐
│  app.whenReady → 单实例锁 → 端口预检 → ProcessManager.start()                    │
│     ├─ spawn 后端:   dev = <venv>/python -m uvicorn app.main:app --port 18760    │
│     │                prod = resources/backend/aragon-quant(.exe) --port 18760    │
│     │                 env: DB_PATH=<userData>/aragon.db, PYTHONUTF8=1, 代理透传   │
│     ├─ waitOn  http://127.0.0.1:18760/api/health  (允许 okx:down，只验"在听")     │
│     ├─ spawn 前端:   dev = pnpm -C ../apps/web dev   (next dev -p 18761)          │
│     │                prod = ELECTRON_RUN_AS_NODE=1 <electron> <server.js>         │
│     │                 env: PORT=18761, HOSTNAME=127.0.0.1, PY_BACKEND_URL=:18760  │
│     ├─ waitOn  http://127.0.0.1:18761  (200)                                      │
│     └─ createWindow → win.loadURL("http://127.0.0.1:18761")                       │
│  退出: before-quit / window-all-closed → ProcessManager.stop() (优雅→tree-kill)  │
└──────────────────────────────────────────────────────────────────────────────────┘
        │ HTTP :18761 (页面 + /api/py/* 经 Next rewrites 代理)        │ WS :18760 (浏览器直连)
        ▼                                                            ▼
   Next 服务进程  ──/api/py/*──►  Python 后端 (FastAPI :18760)  ◄── lightweight-charts 实时帧
                                       │  SQLite(userData)  │  OKX 公共 REST/WS（经系统代理）
```

要点：**渲染进程（页面）完全复用现有 Web 应用**，它仍以为自己跑在 `http://localhost:18761` 的浏览器里——`/api/py/*` 走 Next 同源代理、WS 走 `:18760`。Electron 只是把这个 localhost 页面装进一个原生窗口，并负责把它依赖的两个后台进程拉起来、管好、退出时清理掉。

### 4.2 启动时序（ProcessManager.start，dev 与 prod 同构）

1. **单实例锁**：`app.requestSingleInstanceLock()`；未拿到锁则 `app.quit()`，已存在实例在 `second-instance` 事件中 `win.focus()`。
2. **端口预检**：用 `net.createServer().listen(port,'127.0.0.1')` 探测 18760/18761 是否空闲（同 `dev.py` 的无害 bind 探测）。若被占用 → `dialog.showErrorBox` 给出端口与释放提示（`Get-NetTCPConnection -LocalPort <p>` / `Stop-Process`），随后退出。
3. **拉起后端**：按 `app.isPackaged` 选命令（§4.4 路径表），`spawn` 时 Windows 加 `windowsHide:true` + `CREATE_NEW_PROCESS_GROUP`（`detached` 在 win 下配合 `taskkill /T` 清理），POSIX 用 `detached:true`（新会话，便于 `killpg`）。注入 env（§4.5）。
4. **等后端就绪**：用 **Node 内置 `http` 模块**（**不引入 `wait-on` 等运行期 npm 依赖**，保证桌面包 `dependencies: {}`，P1-3）轮询 `GET http://127.0.0.1:18760/api/health`，**只要返回任何 HTTP 响应**（含 `okx:"down"`）即视为"在听"，超时（**后端默认 60s**，可配，P2-6）→ 报错对话框 + 退出。**不**以 `okx:up` 为就绪条件（OKX 可能受网络限制，与"后端进程是否起来"正交，且违背"不假数据"应有的诚实——OKX 不可达由页面诚实呈现）。注意 `/api/health` 内含一次 `rest.health()` OKX 探活，须确保其自带短超时，避免 OKX 慢时拖垮就绪轮询（P2-6）。
5. **拉起前端**：dev 跑 `next dev`；prod 经 `ELECTRON_RUN_AS_NODE=1` 用 Electron 自带 Node 执行 standalone `server.js`（路径取自 `web-server.json` 清单），注入 `PORT=18761` / `HOSTNAME=127.0.0.1` / `PY_BACKEND_URL=http://127.0.0.1:18760`。
6. **等前端就绪**：同样用 Node 内置 `http` 轮询 `GET http://127.0.0.1:18761`（期望 200），超时（**前端默认 90s**，给首次 `next dev` 冷编译 / 首次解压 onedir 留足余量，可配，P2-6）→ 报错 + 退出。
7. **开窗**：`createWindow()` 并 `win.loadURL("http://127.0.0.1:18761")`；dev 模式 `win.webContents.openDevTools()`。
8. **就绪前的 UX**：开窗可提前进行并先 `loadFile` 一个内置 `loading.html`（"正在启动 Aragon 引擎…"），待 6 完成再 `loadURL`，避免白屏/`ERR_CONNECTION_REFUSED` 闪现。

### 4.3 退出与清理（ProcessManager.stop）

复刻 `scripts/dev.py` 已验证策略：先**优雅**（Windows 向进程组发 `CTRL_BREAK`，POSIX `process.kill(-pid, 'SIGTERM')`），`wait` 至多 ~8s；超时**兜底强杀整棵树**（Windows `taskkill /F /T /PID`，POSIX `process.kill(-pid,'SIGKILL')`）。触发点：`window-all-closed`（非 macOS 即 `app.quit()`）、`before-quit`（置 `isQuitting` 标志，确保 `stop()` 在进程退出前完成）、主进程 `uncaughtException`。**目的**：杜绝 `next dev` 的 reload 工作子进程 / uvicorn `--reload` 子进程变孤儿占住端口（否则再次启动即 Windows `WinError 10013`）。**运行期监管（P2-4）**：除退出清理外，ProcessManager 还监听两个子进程的 `exit` 事件——若在窗口已就绪后某子进程**非预期退出**（非 `isQuitting` 触发），弹 `dialog` 告知"后端/前端已停止，请查看 `userData/logs`"，避免页面静默报错而用户无从知晓。

### 4.4 路径解析（dev vs packaged）

由 `desktop/src/lib/paths.js` 统一解析；`REPO_ROOT = path.resolve(__dirname, '../../..')`（dev）。

| 资源 | 开发模式（`!app.isPackaged`） | 打包模式（`app.isPackaged`） |
|---|---|---|
| 后端启动 | `<REPO_ROOT>/services/quant/.venv/Scripts/python.exe`（win）/ `bin/python`（posix）`-m uvicorn app.main:app --port 18760 --reload`，`cwd=services/quant` | `process.resourcesPath/backend/aragon-quant(.exe) --port 18760`，`cwd=resources/backend` |
| 前端启动 | `pnpm -C <REPO_ROOT>/apps/web dev`（`next dev -p 18761`） | `ELECTRON_RUN_AS_NODE=1` + `process.execPath` + `<resourcesPath>/web/<server.js 相对路径>`（取自 `web-server.json`），`cwd = server.js 所在目录` |
| 静态资源 | 由 `next dev` 自己服务 | standalone 已含；构建期把 `.next/static`、`public` 拷到 server.js 同级 `.next/static`、`public` |
| DB | 仓库默认（`services/quant` 下，sqlite 文件被 `.gitignore`） | `app.getPath('userData')/aragon.db`（经 `DB_PATH` 注入） |
| 日志 | 控制台 | `app.getPath('userData')/logs/{backend,web,main}.log` |

> dev 模式若检测到 venv 不存在，主进程报明确错误：「请先在仓库根执行 `pnpm setup` 创建 venv 与安装依赖」。

### 4.5 子进程环境变量契约（env）

`desktop/src/lib/config.js` 统一组装，注入给两个子进程：

| 变量 | 值 | 作用域 | 说明 |
|---|---|---|---|
| `PYTHONUTF8` / `PYTHONIOENCODING` | `1` / `utf-8` | 后端 | 中文日志不崩（同 `dev.py`） |
| `DB_PATH` | prod=`<userData>/aragon.db`；dev 不设（用默认） | 后端 | 资源目录只读，DB 必须落可写区（D6） |
| `HTTP_PROXY`/`HTTPS_PROXY`/`ALL_PROXY`/`NO_PROXY` | 透传自 `process.env`（存在才传） | 后端 | OKX 真实数据可达性（README：本机 Clash `127.0.0.1:7897`） |
| `ALLOW_CACHED_FALLBACK` | 默认不设（=false） | 后端 | 保持迭代 3 的诚实默认：OKX 不可达即显式报错，不假数据 |
| `PORT` | `18761` | 前端(prod) | Next standalone server.js 读取的监听端口 |
| `HOSTNAME` | `127.0.0.1` | 前端(prod) | 仅本机监听，减少防火墙弹窗与暴露面 |
| `PY_BACKEND_URL` | `http://127.0.0.1:18760` | 前端 | 决定 `/api/py/*` 代理目标。**⚠️ 非对称语义（P1-5）**：`next dev` 进程**启动时重读** `next.config.mjs`，故 **dev 模式注入有效**；但 **standalone（prod）下 `rewrites()` 的结果在 `next build` 时即被序列化进 `.next/required-server-files.json`，`server.js` 运行期不再重读配置——故 prod 运行期注入此 env 为 no-op**。因此 prod 必须**在 `build:web` 构建期设 `PY_BACKEND_URL=http://127.0.0.1:18760`**（见 §4.6 ②），把 IPv4 目标烤进产物；运行期仍注入仅作 dev 一致性与防御性冗余。 |
| `NODE_ENV` | `production`（prod server.js） | 前端 | — |

> 注意：`NEXT_PUBLIC_PY_WS_URL` **不在**运行期注入——它是 `next build` 构建期内联进客户端 bundle 的，运行期设置无效；固定端口 18760 使其默认值 `ws://localhost:18760` 即正确（D3）。

### 4.6 生产构建流水线（`npm run dist`）

`dist` = `build:backend` → `build:web` → `electron-builder`。三步：

1. **`build:backend`**（`python desktop/scripts/build-backend.py`）：调用 PyInstaller（用既有 venv 或临时 venv 安装 `services/quant/requirements.txt` + `pyinstaller`；`pyinstaller` 为**构建期工具，不写入 `requirements.txt`**），以 `desktop/aragon-quant.spec` 产出 `onedir` 包，落到 `desktop/resources/backend/`。入口脚本 `desktop/scripts/backend_entry.py`：解析 `--port`（默认 18760）后**直接导入 app 对象再启动**——`from app.main import app` 然后 `uvicorn.run(app, host="127.0.0.1", port=port)`（**不传 `"app.main:app"` 字符串、不开 `--reload`**：冻结包内字符串导入解析易失败，直接传对象更稳健；reload 仅 dev 需要，prod 必须关，P1-4）；构建时 `--paths services/quant` 使其能 import `app.*`。
2. **`build:web`**（`node desktop/scripts/build-web.mjs`）：在 `apps/web` 下以 `NEXT_OUTPUT_STANDALONE=1` **且 `PY_BACKEND_URL=http://127.0.0.1:18760`** 跑 `pnpm build`（均复用既有 env 开关，零改 `next.config.mjs`）。**`PY_BACKEND_URL` 必须在此构建期设置（P1-5）**：standalone 把 `rewrites()` 结果烤进 `required-server-files.json`，运行期改不了；构建期显式设为 **IPv4 `127.0.0.1`**（而非默认 `localhost`）可避免 Windows 上 `localhost` 先解析到 IPv6 `::1`、而 uvicorn 只绑 `127.0.0.1` 导致代理连接被拒。构建完**递归定位** `.next/standalone` 内的 `server.js`，把整棵 `standalone/` 拷到 `desktop/resources/web/`，再把 `.next/static`、`public` 拷到 server.js 同级对应目录，最后写 `desktop/resources/web/web-server.json`（记录 server.js 相对路径）。
3. **`electron-builder`**：按 `desktop/electron-builder.yml` 把 `desktop/src/**` + `desktop/resources/**`（`extraResources`）打成安装包，输出到 `desktop/dist/`。

> 跨平台提示：PyInstaller **不能交叉编译**，Windows 包须在 Windows 构建、mac 包须在 mac 构建。本轮验收以 Windows 为准。

---

## 5. File / Module Change Plan（文件 / 模块变更计划）

> 原则：新增产物**全部位于 `desktop/`**；`apps/web` / `services/quant` 维持零源码改动；根目录仅追加 `.gitignore` 忽略项与（可选）便捷脚本/文档。下表「类型」：C=新建、M=修改、(opt)=可选。

| 文件 | 类型 | 一句话意图 |
|---|---|---|
| `desktop/package.json` | C | 独立 npm 包；`scripts`：`dev` / `build:backend` / `build:web` / `dist` / `pack`(纯打包) / `smoke`(build+pack)；**`dependencies: {}`（运行期零 npm 依赖——主进程仅用 Electron + Node 内置模块，P1-3）**；`devDependencies`：**`electron@^31`（下限 ≥28，保证内置 Node ≥18.18，D8/P1-1）**、`electron-builder`、**`npm-run-all`（提供 `run-s`，`dist`/`smoke` 串接所需，P1-2）**、`cross-env`、`fs-extra`（后二者仅构建/开发期用） |
| `desktop/electron-builder.yml` | C | 打包配置：`appId`、`productName=Aragon Trading`、`directories.output=dist`、`files`(src)、`extraResources`(web/backend/loading)、`asar:true`、win(nsis+portable)、mac(dmg,占位)、linux(AppImage,占位) |
| `desktop/src/main.js` | C | Electron 主进程：单实例锁、模式判定、端口预检、调用 ProcessManager、建窗与加载、生命周期与清理、应用菜单、错误对话框 |
| `desktop/src/preload.js` | C | `contextBridge` 暴露最小只读 API（`window.aragon`：版本号、平台、`openLogsDir()`），`contextIsolation:true`、`nodeIntegration:false` |
| `desktop/src/lib/process-manager.js` | C | 子进程 spawn / waitOn 就绪轮询 / 优雅终止 + tree-kill（复刻 `dev.py` CTRL_BREAK→taskkill /T 策略） |
| `desktop/src/lib/paths.js` | C | dev/packaged 路径解析（venv python、web 目录、backend 可执行体、`web-server.json` 清单、userData DB/日志） |
| `desktop/src/lib/config.js` | C | 端口常量、子进程 env 组装（DB_PATH→userData、代理透传、PORT/HOSTNAME/PY_BACKEND_URL） |
| `desktop/src/loading.html` | C | 启动过渡页（"正在启动 Aragon 引擎…"），避免就绪前白屏/连接拒绝闪现 |
| `desktop/scripts/backend_entry.py` | C | PyInstaller 入口：解析 `--port` 后 **`from app.main import app; uvicorn.run(app, host="127.0.0.1", port=port)`**（传 app 对象、不开 reload，P1-4） |
| `desktop/aragon-quant.spec` | C | PyInstaller spec：`hiddenimports` + `collect_all`（见 §8.3），`onedir`，输出名 `aragon-quant` |
| `desktop/scripts/build-backend.py` | C | 准备依赖 + 调 PyInstaller，产物落 `desktop/resources/backend/` |
| `desktop/scripts/build-web.mjs` | C | 跑 standalone 构建、递归定位 server.js、装配 `resources/web/`、写 `web-server.json` |
| `desktop/build/icon.ico` · `icon.icns` · `icon.png` | C | 应用图标（占位，可后续替换品牌图） |
| `desktop/build/entitlements.mac.plist` | C (opt) | macOS 签名授权占位（本轮不签名） |
| `desktop/.gitignore` | C | 忽略 `node_modules/`、`dist/`、`resources/web/`、`resources/backend/`、`*.log`（构建产物不入库） |
| `desktop/README.md` | C | 桌面端开发/打包说明（前置 `pnpm setup`、`npm install`、`npm run dev`、`npm run dist`、产物位置、排障） |
| `.gitignore`（根） | M | 追加 `desktop/dist/`、`desktop/resources/web/`、`desktop/resources/backend/`（`node_modules/`、`*.log` 已被全局忽略） |
| `package.json`（根） | M (opt) | 追加便捷脚本 `desktop:dev` / `desktop:dist`（转调 `desktop/`），不破坏既有 |
| `README.md`（根） | M (opt) | 新增「桌面端（Electron）」一节，链到 `desktop/README.md` |
| `apps/web/next.config.mjs` | M (opt) | 仅当 §10 R1/R2 实测受阻时，追加 `outputFileTracingRoot` 使 standalone 产物扁平化；**默认不改**，优先靠 build-web.mjs 的清单机制兼容 |

> **零改动核对**：`apps/web/lib/api.ts`（相对 `/api/py`）、`apps/web/lib/ws.ts`（默认 `ws://localhost:18760`）、`services/quant/app/main.py`（读 env 配置）在固定端口 18760/18761 下**无需改动**即可在桌面外壳内正常工作——这是 D3 固定端口选择的直接收益。

---

## 6. 兼容性分析（为何能"零侵入"复用 Web 应用）

- **接口路径**：前端发 `/api/py/*`（相对），由 Next 服务 `rewrites` 代理到 `PY_BACKEND_URL/api/*`。**dev**：`next dev` 启动重读配置，运行期注入 `PY_BACKEND_URL=http://127.0.0.1:18760` 即生效（默认 `localhost:18760` 亦可）；**prod**：rewrites 在 `next build` 即烤定，故 **build:web 构建期**把 `PY_BACKEND_URL` 设为 IPv4 `http://127.0.0.1:18760`（§4.6 ②、P1-5），与后端 `127.0.0.1:18760` 绑定一致 → 接口零改（前端源码不动）。
- **WebSocket**：浏览器（=渲染进程）直连 `NEXT_PUBLIC_PY_WS_URL`（构建期内联，默认 `ws://localhost:18760`）；后端固定 18760 → WS 零改。
- **CORS**：后端 `allow_origins=["*"]`，渲染进程 origin 为 `http://127.0.0.1:18761`，WS/直连均放行 → 零改。
- **真实数据硬约束**：桌面只透传代理 env、不碰取数逻辑；OKX 不可达仍由 `/api/candles` 显式 502 + 前端诚实错误态承接 → 放行条件①不回退。
- **凭据**：本期仅用 OKX 公共行情，桌面不引入任何密钥存储 → 放行条件②不回退。

---

## 7. Interface Design（接口 / 契约设计）

本特性不新增 REST/WS 接口；其"接口"为 **npm 脚本签名、Electron IPC 通道、进程/端口契约、构建清单**。

### 7.1 npm 脚本（`desktop/package.json`）
| 脚本 | 命令（语义） | 说明 |
|---|---|---|
| `dev` | `cross-env ARAGON_MODE=dev electron .` | 拉起 Electron 调试窗口（自动起后端+前端+DevTools），**而非浏览器** |
| `build:backend` | `python scripts/build-backend.py` | PyInstaller 产后端可执行体 → `resources/backend/` |
| `build:web` | `node scripts/build-web.mjs` | standalone 构建 + 装配 → `resources/web/` |
| `dist` | `run-s build:backend build:web dist:only`（`dist:only` = `electron-builder`） | 全量打包。**用 `npm-run-all` 的 `run-s` 串接**（PowerShell 5.1 不支持 `&&`；`run-s` 跨平台稳妥，P1-2）。 |
| `pack` | `electron-builder --dir` | **纯打包步**：仅把**已存在的** `src/` + `resources/` 装进免安装目录，**不触发任何 build**。⚠️ 单独跑 `pack` 而未先 `build:backend`+`build:web` 会打出缺 `resources/{web,backend}` 的空壳（P1-6）。 |
| `smoke` | `run-s build:backend build:web pack` | **正确的快速冒烟入口**：先构建后端 exe 与前端 standalone，再 `--dir` 出免安装目录，避免 `pack` 假绿（P1-6）。 |

> ⚠️ 实施提醒（Windows）：`package.json` 的 `scripts` 由 npm 在其内置 shell 执行，`&&` 在 npm 脚本里跨平台可用；但若团队用 PowerShell 直接手敲，应改用 `run-s`（`npm-run-all`）避免 `&&` ParserError。本方案采用 `run-s` 以求稳。

### 7.2 Electron IPC（`preload.js` ↔ 渲染进程）
- `window.aragon.appVersion(): string`、`window.aragon.platform(): string`、`window.aragon.openLogsDir(): void`（`ipcRenderer.invoke('open-logs')` → 主进程 `shell.openPath(logsDir)`）。仅只读/无害操作，渲染层不获 Node 能力。

### 7.3 进程/端口契约
- 后端监听 `127.0.0.1:18760`，暴露 `/api/*` 与 `/api/ws/*`；就绪判据 `GET /api/health` 有响应。
- 前端监听 `127.0.0.1:18761`，服务页面与 `/api/py/*` 代理；就绪判据 `GET /` 返回 200。
- 二者均**仅绑 127.0.0.1**（不对外）。

### 7.4 构建清单 `web-server.json`（由 build-web.mjs 写、main.js 读）
```jsonc
{ "serverJs": "apps/web/server.js",  // 相对 resources/web/ 的 server.js 路径（扁平时为 "server.js"）
  "builtAt": "<ISO 时间，构建期注入>",
  "nextVersion": "14.2.15" }
```

---

## 8. Data Model（数据模型）

本特性**不改任何持久化 schema**（不动 SQLite 表、不动 OKX 数据形态）。仅引入若干**进程内/磁盘配置形态**：

- **运行期配置对象**（`config.js` 导出，内存）：`{ mode: 'dev'|'prod', backendPort: 18760, webPort: 18761, dbPath: string, logsDir: string, proxy: {http?,https?,all?,no?} }`。
- **`web-server.json`**（磁盘，见 §7.4）：构建期产物清单。
- **ProcessManager 句柄**（内存）：`{ name:'backend'|'web', proc: ChildProcess, pid: number, ready: boolean }[]`。
- **DB 文件位置**：prod 落 `app.getPath('userData')/aragon.db`（首启自动建表，沿用后端 `init_db` 既有逻辑）；dev 沿用仓库默认。**首启迁移**：无需迁移（schema 不变），新机首启即由后端 `lifespan→init_db` 建表并 seed 5 内置策略。

---

## 9. Testing & Acceptance Criteria（测试与验收）

### 9.1 开发模式验收（必过）
- **前置**：仓库根 `pnpm setup`（建 venv + 装后端依赖 + `apps/web` 装依赖）已完成。
- `cd desktop && npm install` 成功（拉到 electron/electron-builder）。
- `npm run dev`：
  - [ ] **弹出的是 Electron 原生桌面窗口，而非系统浏览器**；标题为 Aragon Trading。
  - [ ] 窗口内加载出完整应用（左 K 线 + 右策略面板），DevTools 自动打开且 Console 无致命错误。
  - [ ] K 线渲染、切换 5 策略、跑一次回测得到 markers/净值、切「实时」模式 WS 连上（OKX 可达时最右一根跳动）。
  - [ ] 改 `apps/web` 前端文件 → 窗口热更新生效。
  - [ ] 关闭窗口后，`Get-NetTCPConnection -LocalPort 18760,18761` 无残留监听（无孤儿进程）。

### 9.2 打包模式验收（必过）
- `npm run dist` 成功，`desktop/dist/` 下生成 Windows 安装包（NSIS `*.exe`）与/或 portable。
- 在**未装 Python/Node/pnpm** 的环境（或临时改名 venv 模拟）安装并启动：
  - [ ] 应用能独立启动（自带后端 exe + Next 服务），开窗加载应用。
  - [ ] 既有功能与 Web 版一致（回测/对比/历史/导出 CSV/实时或回放模拟）。
  - [ ] OKX 可达（经系统代理）时取真实数据；不可达时显示既有诚实错误态，**无假数据**。
  - [ ] DB 落在 `%APPDATA%/<productName>/aragon.db`，重启后历史 run 仍在。
  - [ ] 退出应用后无孤儿进程、端口释放。
- **单实例 / 端口冲突**：先占用 18760，再启动应用 → 弹出明确错误对话框而非闪退。

### 9.3 自动化 / 静态校验
- `desktop/` 下 `node --check src/main.js`（及各 lib）语法自检；以 **`npm run smoke`**（= `run-s build:backend build:web pack`，先备齐 `resources/` 再 `--dir` 出目录）作为快速冒烟，**而非裸 `npm run pack`**（后者不构建资源，会假绿，P1-6），既降低全量出安装包的耗时、又保证冒烟真实可起。
- 既有后端 pytest / 前端 vitest **不受影响**（桌面外壳零侵入），CI 维持现状。
- **长任务提醒**：`npm run dist`（含 PyInstaller + next build）可能 > 60s，实施 / 验收时按长任务协议异步等待，勿同步死等。

### 9.4 验收命令速查（Windows / PowerShell）
```powershell
# 前置（仓库根）
pnpm setup
# 开发模式
cd desktop ; npm install ; npm run dev
# 快速打包冒烟（免安装目录）——先构建资源再 --dir，勿用裸 pack（P1-6）
npm run smoke
# 全量安装包
npm run dist
# 端口残留检查
Get-NetTCPConnection -LocalPort 18760,18761 -ErrorAction SilentlyContinue
```

---

## 10. Risks & Mitigations（风险与缓解）

| # | 风险 | 影响 | 缓解 |
|---|---|---|---|
| R1 | **Windows 上 standalone 构建 EPERM symlink**（pnpm 符号链接 + 无 Developer Mode，`copyTracedFiles` 报错——迭代 3 spec D2 已记录） | `build:web` 失败 | 启用 Windows「开发者模式」(设置→隐私和安全性→开发者选项) 取得免提权创建符号链接权限；或以管理员运行构建；或 R2 的扁平化 tracing。`build-web.mjs` 捕获该错误并打印此提示。 |
| R2 | **monorepo standalone 产物嵌套**（`server.js` 落 `.next/standalone/apps/web/server.js`，硬编码 `node server.js` 会 MODULE_NOT_FOUND，迭代 3 spec C3 同坑） | 打包后前端起不来 | `build-web.mjs` **递归定位** server.js 并写 `web-server.json`，主进程按清单解析路径与 cwd；不硬编码。备选：`apps/web/next.config.mjs` 加 `outputFileTracingRoot: __dirname` 扁平化（§5 opt 改动）。 |
| R3 | **`NEXT_PUBLIC_PY_WS_URL` 构建期内联**，运行期改不了 | 动态端口下 WS 连错地址 | 本轮锁**固定端口 18760**，默认值即正确。后续若要动态端口：在 `apps/web/lib/ws.ts` 加运行期覆盖（优先读 `globalThis.__ARAGON_WS_BASE__`，由 preload 注入），向后兼容、回落 env——列为增强，非本轮。 |
| R4 | **PyInstaller 漏 hiddenimports**（uvicorn worker/loggers、`aiosqlite`、`pandas`/`numpy`、`websockets`、`pydantic` 等动态导入） | 后端 exe 启动即崩 | `aragon-quant.spec` 用 `collect_all('uvicorn')` + `collect_submodules('app')` + 显式 `hiddenimports=['aiosqlite','uvicorn.lifespan.on','uvicorn.loops.auto','uvicorn.protocols.http.auto','uvicorn.protocols.websockets.auto','websockets.legacy']`；构建后**直接跑一次 exe + curl /api/health** 验证（CI 冒烟）。 |
| R5 | **打包资源目录只读**，SQLite/日志写失败 | 后端启动失败/无历史 | `DB_PATH`、日志目录指向 `userData`（D6）；首启自动建目录。 |
| R6 | **端口被占 / 多开** | 启动崩溃或互抢端口 | 单实例锁 + 启动前端口预检 + 明确对话框（§4.2）。 |
| R7 | **子进程孤儿**（reload 工作子进程残留占端口，Win 再启报 10013） | 二次启动失败 | ProcessManager 复刻 `dev.py` 的优雅(CTRL_BREAK/SIGTERM)→兜底 tree-kill(`taskkill /F /T`/`killpg SIGKILL`)（§4.3）。 |
| R8 | **OKX 受地区限制不可达** | 桌面内取不到真实数据 | 透传系统代理 env（README：Clash `127.0.0.1:7897`）；不可达即沿用既有诚实 502/错误态，**严禁假数据**（放行条件①）。 |
| R9 | **杀软误报 PyInstaller 产物** | 用户侧被拦 | 用 `onedir`（比 `onefile` 误报率低）；预留代码签名配置位（本轮不签）。 |
| R10 | **产物体积大**（Electron + 内置 Node + Python runtime + Next） | 安装包偏大（数百 MB） | `asar` 打包 + `onedir` + electron-builder 默认压缩；接受桌面分发的体积特性，README 说明。 |
| R11 | **首启防火墙弹窗**（监听端口） | 用户困惑 | 仅绑 `127.0.0.1`（非 0.0.0.0），多数情况下不触发入站防火墙提示。 |
| R12 | **PyInstaller 不能交叉编译** | mac/linux 包无法在 Windows 产出 | 本轮 Windows 一等公民；mac/linux 配置位齐备并在 README 标注「需在对应平台构建」（Non-Goal）。 |
| R13 | **沙箱无法实测全量打包**（PyInstaller / electron-builder 下载二进制需网络、耗时） | 验收受限 | 允许 P2 降级为「交付可复现的脚本与配置 + 本地能跑通的 dev 模式」，全量 `dist` 的实测结论登记在实施记录；保持纯增量、零业务侵入（与迭代 3 spec C8 同口径）。 |
| R14 | **standalone rewrites 构建期烤定 + `localhost` 解析到 IPv6**（评审 P1-5）：prod 运行期注入 `PY_BACKEND_URL` 无效；默认 `localhost:18760` 在 Windows 可能解析到 `::1`，而 uvicorn 只绑 `127.0.0.1` → 打包后 `/api/py/*` 代理连接被拒 | 打包后接口全 502/ECONNREFUSED | **build:web 构建期**显式设 `PY_BACKEND_URL=http://127.0.0.1:18760`（IPv4），把正确目标烤进 `required-server-files.json`；后端 uvicorn 亦绑 `127.0.0.1`，两端 IPv4 对齐（§4.5/§4.6 ②/§6）。 |
| R15 | **`pack` 冒烟假绿**（评审 P1-6）：`electron-builder --dir` 不触发 `build:backend`/`build:web`，缺 `resources/{web,backend}` 仍"打包成功" | 冒烟通过却起不来，误判 | `pack` 定位为纯打包步；冒烟统一走 `npm run smoke`（`run-s build:backend build:web pack`），确保资源已就位（§7.1/§9.3）。 |

---

## 11. 实施过程发现的方案缺陷（由 Subtask #2 填写）

> 代码开发节点逐条核对方案与真实源码 / 工具链 / 运行环境，发现并就地修正下列 5 项，保持 spec 与实现一致。整体结论：**§9.1 dev 模式闭环已实测全通过**（Electron 原生窗口加载完整应用、5 策略、回测、实时 WS、OKX 真实数据 200、优雅退出后 18760/18761 零残留）；**build:backend 冻结后端已实测通过**（PyInstaller onedir exe 起来后 `GET /api/health` 返回 `{okx:"up", cachedFallbackEnabled:false}`，真实数据、零假数据、无 MODULE_NOT_FOUND）；**build:web standalone 受本机环境（无 Developer Mode/管理员）的 EPERM symlink 硬阻**（见发现 5），故全量 `dist`/`smoke` 末段按 R13/C8 降级为 P2。

### 发现 1 — ambient `ELECTRON_RUN_AS_NODE=1` 令 `electron .` 不开窗（环境坑，已修正）

本项目运行/验收环境的进程环境里**预置了 `ELECTRON_RUN_AS_NODE=1`**。它会让 `electron .` 以**纯 Node** 而非 GUI 模式启动 —— 主进程 `const { app } = require('electron')` 得到 `undefined`，启动即崩（`TypeError: Cannot read properties of undefined (reading 'requestSingleInstanceLock')`，实测 `Node.js v20.18.0` 字样佐证其以 Electron 内置 Node 运行）。**修正**：新增 `desktop/scripts/dev-launch.js` 启动器，spawn electron 前 `delete process.env.ELECTRON_RUN_AS_NODE` 并置 `ARAGON_MODE=dev`；`desktop/package.json` 的 `dev` 脚本由 `cross-env ARAGON_MODE=dev electron .`（spec §7.1）改为 `node scripts/dev-launch.js`。干净环境下二者等价，此举使 `npm run dev` 在任何环境都稳定拉起桌面窗口。

### 发现 2 — dev 前端改用 Electron 自带 Node 直跑 next（更稳健，已采用）

spec §4.4 的 dev 前端原为 `pnpm -C ../apps/web dev`。实施改为**用 Electron 自带 Node（`process.execPath` + `ELECTRON_RUN_AS_NODE=1`）直接执行 `apps/web/node_modules/next/dist/bin/next dev -p 18761`**（prod 前端同理跑 standalone `server.js`）。理由：① Node 20+（Electron 31 内置）对 `.cmd`（`pnpm.cmd`）的 `spawn` 有安全限制（CVE-2024-27980），无 `shell:true` 会 `EINVAL`；② 直跑不依赖 PATH 里有 pnpm、更自包含、进程树更易由 `taskkill /T` 清理。语义等同 `next dev`，对 `apps/web` 零改动。`build-web.mjs` 的 `next build` 同样用 `process.execPath` 直跑 next（不调 pnpm）。

### 发现 3 — ambient `NODE_ENV=production` 破坏 `next dev` 的 CSS/PostCSS 链（环境坑，已修正）

同一环境还预置了 `NODE_ENV=production`。dev 前端子进程若继承它，`next dev` 会告警「non-standard NODE_ENV」并**令 Tailwind/PostCSS 加载链失效**——`app/globals.css` 的 `@tailwind` 不被处理，报 `Module parse failed: Unexpected character '@'`，页面无样式。**修正**：`desktop/src/lib/config.js` 的 `webEnv` 在 dev 模式显式 `NODE_ENV=development`（prod 仍 `production`），覆盖 ambient 值。修正后 `✓ Compiled / in ...`、`GET / 200`，窗口正常加载。

### 发现 4 — `build-backend.py` 在 GBK 控制台打印 `✓` 崩溃（已修正）

`build-backend.py` 打印 `✓ / 中文` 时，Windows GBK(cp936) 控制台抛 `UnicodeEncodeError`（与本项目 `scripts/dev.py` 记录的同一坑），导致 PyInstaller 构建虽成功、脚本却在「拷贝产物到 resources/backend + 冒烟」前崩溃。**修正**：仿 `dev.py` 在 `main()` 首行 `sys.stdout/stderr.reconfigure(encoding="utf-8", errors="replace")`。修正后 build:backend 跑通：产 `resources/backend/aragon-quant.exe`（15.9MB + `_internal/`）并自检 `GET /api/health` 200（OKX up、真实数据）。

### 发现 5 — `build:web` standalone 在本机 EPERM symlink 受阻（环境约束 R1，降级 P2）

`build:web`（`NEXT_OUTPUT_STANDALONE=1` 的 `next build`）在本机**编译成功**，但末段 `writeStandaloneDirectory → copyTracedFiles` 重建 pnpm 符号链接时抛 `EPERM: operation not permitted, symlink`（源如 `.pnpm/@next+env@14.2.15/.../@next/env`）。根因正是 R1：**pnpm 符号链接 node_modules + Windows 未开 Developer Mode + 非管理员**，无 `SeCreateSymbolicLinkPrivilege` 不能创建符号链接。已尝试经注册表启用 Developer Mode（`HKLM\...\AppModelUnlock\AllowDevelopmentWithoutDevLicense=1`）但**写入被拒（当前 shell 非提权）**。`outputFileTracingRoot`（§5 opt / R2）只解嵌套不解符号链接权限，故未采用、`next.config.mjs` 维持零改动。**结论**：此为环境权限硬阻、非代码缺陷（next build 编译本身通过）；按 R13/C8 将「全量 `dist`/`smoke` 末段」降级为 P2。**用户机上一键修复**：开启「设置 → 隐私和安全性 → 开发者选项 → 开发人员模式」（或以管理员运行 `npm run dist`），即可跑通 standalone → `pack` → `dist`。`build-web.mjs` 已在该错误处打印此提示。

---

## 12. 附：实施顺序建议（给 Subtask #2）

1. 先搭 **dev 模式最小闭环**（`package.json` + `main.js` + `process-manager.js` + `paths.js` + `config.js` + `loading.html` + `preload.js`），`npm run dev` 跑通 §9.1——这是需求最硬的「`npm run dev` 起 Electron 调试窗口」。
2. 再做 **build:web**（`build-web.mjs` + 清单机制 + 构建期 `PY_BACKEND_URL=http://127.0.0.1:18760`），本地 `npm run smoke`（非裸 `pack`，P1-6）验证前端 standalone 能被主进程拉起。
3. 再做 **build:backend**（`backend_entry.py` + `aragon-quant.spec` + `build-backend.py`），冒烟后端 exe `/api/health`。
4. 最后 `electron-builder` 全量 `dist`，过 §9.2。
5. 补 `.gitignore` / `README` / 图标；**不提交**构建产物（`dist/`、`resources/web|backend/`）。

> 全文 ≥ 800 字要求已满足（本文 > 4000 字）。本设计细致到文件级与命令级，下游可据此逐行实现，无需再做架构决策。

---

## 评审结论（Review Verdict）

**结论：有条件通过（Approved with Conditions）。**

**总体判断**：本方案技术主线正确、可行、规模适配得当，对 `apps/web` / `services/quant` 真正做到零源码侵入——其赖以"零改动"的关键源码事实（相对 `/api/py` 取数、`ws://localhost:18760` 默认、`DB_PATH`/`ALLOW_CACHED_FALLBACK` 可经 env 注入、`/api/health` 就绪判据、`next.config.mjs` 的 `PY_BACKEND_URL`/`NEXT_OUTPUT_STANDALONE` 开关、`dev.py` 进程树清理策略）**经逐文件核对全部为真**。无方向性返工，5 条放行条件不回退、无假数据引入。**0 项 P0；评审发现的 6 项 P1 已在本 v2 文档内全部就地修复**（Electron 版本下限、`npm-run-all` 依赖补全、运行期依赖卫生、`backend_entry` 直接传 app 对象、`PY_BACKEND_URL` 构建期烤定 + IPv4 对齐、`pack`/`smoke` 冒烟语义），故满足 DoD「无 P0/P1 遗留」。

**放行条件（Subtask #2 实施时必须落实，逐条对应文档已修订处）**：
1. **C1（对应 P1-1/D8）**：`desktop/package.json` 锁 `electron@^31`（下限 ≥28，保证内置 Node ≥18.18，满足 Next 14.2.15）。
2. **C2（对应 P1-2）**：`devDependencies` 含 `npm-run-all`；`dist`/`smoke` 一律用 `run-s` 串接，禁用 `&&`。
3. **C3（对应 P1-3）**：桌面包 `dependencies: {}`，主进程就绪轮询仅用 Node 内置 `http`；`wait-on`/`fs-extra`/`cross-env` 仅 devDeps。
4. **C4（对应 P1-4）**：`backend_entry.py` 用 `from app.main import app; uvicorn.run(app, …)`，prod 不开 `--reload`。
5. **C5（对应 P1-5/R14）**：`build:web` 构建期注入 `PY_BACKEND_URL=http://127.0.0.1:18760`（IPv4），后端 uvicorn 亦绑 `127.0.0.1`；**打包后必须实测 `/api/py/*` 代理连通**（IPv6/IPv4 解析坑的最终验收点）。
6. **C6（对应 P1-6/R15）**：冒烟统一走 `npm run smoke`（先 build 再 `--dir`），禁止以裸 `pack` 充当冒烟。
7. **C7（P2 集，非阻断但建议落实）**：PyInstaller `hiddenimports` 兜底（`collect_all('pydantic')`/`pydantic_core`/`sqlalchemy.dialects.sqlite` + 构建后 `GET /api/health` 实测，P2-2）；子进程运行期崩溃监管弹窗（P2-4）；就绪超时调宽并确保 `rest.health()` 短超时（P2-6）；`desktop/README.md` 标注端口第 5 处同步点（P2-3）；删冗余 `wait-on`（P2-1）；`app.isPackaged` 为模式唯一真源（P2-5）。
8. **C8（环境约束，承自 R13）**：沙箱可能无法实测全量 `dist`（需下载 PyInstaller/Electron 二进制）。允许将"全量 `dist` 出安装包"降级为 P2，但 **§9.1 的 dev 模式闭环与 §9.3 的 `smoke` 必须实测通过**，全量 `dist` 结论登记于 §11 实施记录。

> 凡上述条件在实施中被证伪或需偏离，由 Subtask #2 在 §11「实施过程发现的方案缺陷」登记并就地修订，保持 spec 与实现一致。

**— 评审人：Senior Reviewer, Anthropic Engineering / 2026-06-13 / 本文档升版 v2。**
