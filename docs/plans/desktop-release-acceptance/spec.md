# Aragon Trading — 桌面端发布验收与体验收尾方案（让「确保可用 / 数据真实可用」可机器复验）(spec)

> **版本**：v2（Subtask #1 评审完成：逐节核对真实代码后定级问题清单，P0×0 / P1×5 已就地修复 / P2×6 已记录；详见下「评审记录」与文末「评审结论」）
> **Feature slug**：`desktop-release-acceptance`
> **迭代**：Loop 3/3（最终轮，收尾验收）
> **全局目标**：完成项目开发，确保可用，数据真实可用
> **本轮上下文**：桌面端 Electron 打包（`desktop/`）已在前两轮设计并落地、且已提交——
> `npm run dev` 起原生窗口可用、`npm run dist` 免提权一次成功（产物 `desktop/dist/Aragon Trading-0.1.0-x64.exe` ≈ 120 MB + `win-unpacked/`），EPERM symlink 已从根上消除。
> 因此本轮**不重写打包**，而是补齐「**可被自动、可复验地证明它真的能用、且用的是真实数据**」这块缺口，并做产品身份/图标的收尾。

---

## 评审记录（Review Notes · Subtask #1）

> 评审方法：逐节对照**真实代码**核验，而非纸面推断。已核对：`desktop/src/lib/{config,paths,process-manager}.js`、`desktop/src/main.js`、`desktop/scripts/build-web.mjs`、`desktop/scripts/backend_entry.py`、`desktop/package.json`、`desktop/electron-builder.yml`、`services/quant/app/main.py`、`services/quant/app/api/market.py`、`services/quant/app/config.py`、`apps/web/next.config.mjs`、`.github/workflows/ci.yml`、`desktop/dist/win-unpacked/resources/web/web-server.json`（实测 `serverJs:"server.js"`）、根/桌面 `README.md`。
>
> **结论摘要**：核心设计（驱动 `win-unpacked/` 真实产物做端到端 HTTP 验收、复用 electron-free 的 `config.js`/`process-manager.js`、以响应头 `X-Aragon-Data-Source` 坐实真实数据、OKX 不可达只接受显式 502）**与真实代码完全吻合，无 P0**。`/api/health`(status ok / okx up·down / cachedFallbackEnabled)、`/api/candles`(成功 `live` 头、body `{ts,o,h,l,c,vol}`、ts 秒级、OKXError→502)、`/api/py/:path*`→`{PY_BACKEND_URL}/api/:path*` 代理、backend exe 接受 `--port`、manifest `serverJs` 相对 POSIX 路径——逐一对照属实。下列 **P1 均已在文档正文就地修复**，P2 已记录待 Subtask #2 酌情采纳。

### 四维核验结论

- **可行性（Feasibility）**：✅ 全部接口签名/产物路径/响应头经真实代码核对属实；`verify-dist.mjs` 以纯 `node` 跑 standalone `server.js`、`createRequire` 引 CJS 两库、`portFree/waitForHttp/ProcessManager` 复用——均可实现。
- **完整性（Completeness）**：发现 4 处错误路径/清理/真实性硬约束的缺口（P1-1/P1-2/P1-3 与 P2 若干），已就地补齐。
- **一致性（Consistency）**：发现 2 处与既有约定/本方案自身原则冲突（P1-4 `check` 未覆盖新文件；P1-5 CI job 与 D6/R9「CI 不下载 120 MB Electron」原则自相矛盾），已就地修正。
- **合理性（Right-sizing）**：✅ 纯增量、零源码改动、四目标对齐全局目标，规模恰当不过度；仅 P2 级微调。

### 问题清单

| 编号 | 严重度 | 位置 | 问题 | 处理（已就地修复 / 记录） |
|---|---|---|---|---|
| P1-1 | P1 | §4.2 step 7-8、§7、§6.2 | 未规定清理在 `finally` 中执行。任一断言**中途 FAIL 抛错**时，`pm.stop()` 与临时 DB 删除可能被跳过 → 后端/前端子进程成孤儿占住 18760/18761 → 下次运行直接撞 exit-11（端口被占），使「可反复运行」的验收工具一次失败即不可复用。 | **已修复**：§4.2 改为「断言主体 try / 清理 finally」结构，`pm.stop()`+临时 DB unlink 无论 PASS/FAIL 必执行（`--keep-running` 除外）；§7、§6.2 同步。 |
| P1-2 | P1 | §4.2 step 8、§8.2(码40) | 残端口断言写成「拆解后**单次** `portFree`」。Windows 下 `taskkill /T` 异步、子进程（uvicorn/node 树）socket 释放有数百 ms~数秒延迟，单次检查会**偶发假 FAIL**（§8.1 已说「数秒内恢复」却未落到 step8 实现）。 | **已修复**：step 8 改为**带超时轮询**（≤5s，间隔 250ms）`portFree(18760)&&portFree(18761)`，仍不释放才判 exit-40；§8.2 措辞同步。 |
| P1-3 | P1 | §4.2 step 3/4a、§4.5 | 「诚实默认」依赖 `cfg.backendEnv` 不设 `ALLOW_CACHED_FALLBACK`，但 `backendEnv` 先 `...process.env` 展开——若**操作机 ambient 环境已导出 `ALLOW_CACHED_FALLBACK=true`** 会泄漏进打包后端，悄悄放开缓存回退，违背本轮「数据真实」核心硬约束。且 step 4a 读到 `cachedFallbackEnabled` 却未断言。 | **已修复**：§4.2 step 3 明确 verify-dist 在传给子进程前**显式删除** `env.ALLOW_CACHED_FALLBACK`（并清 `ELECTRON_RUN_AS_NODE`，防 ambient 泄漏）；step 4a **断言** `/api/health.cachedFallbackEnabled===false`（默认；`--allow-okx-down` 下仅告警），把硬约束做成主动校验而非被动期望。 |
| P1-4 | P1 | §5、§6.1、§8.5 | `desktop/package.json` 的 `check` 由**逐文件枚举**的 `check:*` 组成（main/devlaunch/preload/config/paths/pm/buildweb），**未含本轮新增的 `verify-dist.mjs`**。G4 的 CI desktop job 跑 `npm run check`，却**检不到本轮引入的核心新文件**的语法错误——CI 假绿。 | **已修复**：§5/§6.1 增「`desktop/package.json` 新增 `check:verifydist`（`node --check scripts/verify-dist.mjs`）」；§8.5 注明 `make-icons.py` 为 Python、`node --check` 不覆盖（CI 静态门仅覆盖 JS，如实声明）。 |
| P1-5 | P1 | §4.1、§5、§10 | G4 的 CI desktop job 写作 `npm install --include=dev`。但 `desktop/` **无 lockfile**（故不能用 `npm ci`），且 `npm install` 会触发 `electron` 的 postinstall **下载 ≈111 MB 二进制**（desktop/README 第 82 行已记此下载在受限网络易断流）——这与本方案 **D6/R9 自己定的「CI 只做静态校验、绝不下载 120 MB Electron」原则正面矛盾**，会让「静态」job 既慢又抖、甚至红。 | **已修复**：§5/§10 改为 `npm install --ignore-scripts --no-audit --no-fund`（跳过 electron postinstall 下载，npm-run-all 等纯 JS 包正常装）+ 显式 `runs-on: ubuntu-latest`（与既有 backend/frontend job 一致、最省）；§4.1 增设计说明。 |
| P2-1 | P2 | §9 R1、§8.6 | OKX 可达依赖 `proxyEnv()` 透传，而它**仅转发 `process.env` 中已存在**的 `HTTP(S)_PROXY` 等；系统级代理（Clash）若未导出为环境变量，打包后端取不到代理 → 默认 `verify:dist` 硬 FAIL。 | 已记录：R1 补「须确保 `HTTP_PROXY/HTTPS_PROXY` **已导出为环境变量**（非仅系统代理开启）；本机 shell 启动已自动导出 `127.0.0.1:7897`」。非新增风险（与既有 dev/prod 取数同机制）。 |
| P2-2 | P2 | §4.2 step 3-6 | verify-dist 未给 `ProcessManager` 传 `onUnexpectedExit`；子进程**启动后早退**（如 backend 崩）时只会等满 60s/90s 超时才报「未就绪」，诊断不精确、浪费时间。 | 已记录：建议传 `onUnexpectedExit` 回调，子进程早退即立刻 FAIL 并打印 `code/signal`（实现增强，非阻塞）。 |
| P2-3 | P2 | §6.2 `--keep-running` | `--keep-running` 的语义未在 §4.2 落明：保留子进程时必须**跳过 step 7/8 且不删临时 DB**（否则删了正在用的 DB / 误判残端口）。 | 已记录并在 §4.2 finally 注中点明：`--keep-running` 时跳过 stop/残端口断言、保留临时 DB，并打印「需手动 `taskkill` 释放 18760/18761」。 |
| P2-4 | P2 | §4.2 step 4b | 形状阈值 `body.length>=100`（请求 `limit=120`）。若上游用 OKX history-candles（单次上限 100）恰返回 100 即压线通过，边界略紧。 | 已记录：建议放宽下界（如 `>= Math.min(100, limit) ` 或 `>= 50`）以免上游分页/限频抖动误判；不阻塞。 |
| P2-5 | P2 | §4.4、§5 | `.icns` Pillow 写出受限，R6/R-icns 已兜底（尽力出、失败保留占位）。 | 仅确认：占位 `.icns` 不阻塞 Windows 验收（`win.icon` 用 `.ico`），方案处理得当。 |
| P2-6 | P2 | §6.2 `--json` | `--json` 旁路输出标为「实现可选」，对上层脚本解析友好度欠规范。 | 已记录：保持可选；若实现，结构同 §7 的 `steps[]`+汇总。 |

---

## 1. Overview（概述）

前两轮已经把既有 Web 版（`apps/web` Next.js + `services/quant` FastAPI）完整封装成了一个 Electron 桌面应用：开发期 `npm run dev` 直接起原生调试窗口（而非浏览器），生产期 `npm run dist` 免开发者模式 / 免管理员一次产出 Windows 安装包与免安装版。两轮的设计文档分别是 `docs/plans/desktop-electron-packaging/spec.md`（打包外壳与流水线）与 `docs/plans/desktop-packaging-build-hardening/spec.md`（隔离扁平构建根除 EPERM）。从 `git log` 与 `desktop/dist/` 的实际产物看，这两件事都已落地并提交。

问题在于：**「打包成功」与「打包出来的东西真的能用、且用的是真实数据」是两回事，而后者目前只能靠人工双击验证、无法复验。** 现有的自动校验只到「构建产物存在」为止——`build:backend` 末尾会冒烟一次 `/api/health`（但 OKX 不可达也算过），`scripts/smoke_e2e.py` 验证真实数据链路（但它跑的是 `services/quant` 源码 + venv，**不是**冻结后的 `aragon-quant.exe`），`npm run smoke` 只做到 `electron-builder --dir`（出 `win-unpacked/`，**不启动、不断言**）。也就是说，没有任何一步会去**拉起打包后的后端 exe + Next standalone 产物，断言它们在固定端口起得来、前端能经 BFF 代理打到后端、后端能取到 OKX 真实 K 线、退出后端口无残留**——而这恰恰就是全局目标「确保可用，数据真实可用」的字面含义。

本轮（最终轮）因此聚焦四件**纯增量、不动既有打包**的收尾：(1) 新增打包产物端到端验收冒烟 `npm run verify:dist`，驱动 `win-unpacked/` 里的真实产物跑通「后端就绪 → 真实 OKX K 线 → 前端就绪 → 前端经 `/api/py/*` 代理打到后端 → 优雅退出无残端口」并以退出码给出 PASS/FAIL；(2) 在该冒烟里坐实**真实数据硬约束**（断言 `X-Aragon-Data-Source: live` 且 K 线形状合理，OKX 不可达时只接受「诚实 5xx」、绝不接受假数据）；(3) 桌面外壳补「关于 Aragon Trading」对话框与窗口图标，把版本号/构建信息/数据来源诚实声明呈现给用户；(4) 用确定性脚本生成真正的品牌图标集替换占位图标，并给 CI 加一个桌面静态校验 job + 一份发布验收清单。**`apps/web` 与 `services/quant` 维持零源码改动**（这是整个桌面方案的不变量）。

---

## 2. Goals / Non-Goals（本轮）

### Goals

- **G1（P0，本轮核心）**：新增 `desktop/scripts/verify-dist.mjs` + `npm run verify:dist`，对**已构建的 `win-unpacked/` 产物**做端到端验收，PASS/FAIL 以退出码表达，可在本机/脚本反复运行。
- **G2（P0）**：在 G1 中坐实「数据真实可用」——断言打包后端能取到 OKX **真实** K 线（`X-Aragon-Data-Source: live` + 形状校验），OKX 不可达时仅接受**诚实错误态**（显式 5xx），永不把缓存/假数据当 live 通过。
- **G3（P1）**：桌面外壳新增「关于 Aragon Trading」菜单项与对话框（productName / version / Electron·Node·Chrome 版本 / appId / **数据来源诚实声明** / 一键打开日志目录），并为 `BrowserWindow` 显式设置窗口图标。
- **G4（P1）**：CI 增加 `desktop` 静态校验 job（`npm run check`）；`desktop/README.md` 增「发布验收清单」段，把每条全局目标映射到一条可执行校验。
- **G5（P2，体验收尾）**：用确定性脚本 `desktop/scripts/make-icons.py` 生成真正的多尺寸品牌图标，替换现有 ≈1.2 KB 占位 `build/icon.{ico,png,icns}`。

### Non-Goals（本轮不做）

- **不重写**既有打包流水线（`build:backend` / `build:web` / `dist:only` / `dev`）与运行期编排（`main.js`），只做增量。
- **不改** `apps/web` / `services/quant` 任何源码、不改端口（18760/18761）、不改 DB schema、不新增交易策略/业务功能。
- **不做**代码签名 / 公证 / 自动更新（继续沿用 `electron-builder.yml` 占位，留待后续）。
- **不出** mac `.dmg` / linux `AppImage` 正式产物（PyInstaller 不能交叉编译，须各自平台构建，R12 沿用）。
- **不把** `verify:dist` 塞进托管 CI（需 Windows + 真实网络 + 先完成 ≈分钟级构建与 120 MB Electron 下载，见 D6）。

---

## 3. 关键设计决策（Design Decisions & Alternatives）

- **D1 — 验收方式：驱动「打包产物」而非「启动整个 GUI」。**
  `verify:dist` 复用 `main.js` prod 分支的子进程规格（后端 exe + `node` 跑 Next standalone `server.js`），用 HTTP 断言，全程**无界面**。
  *被否方案*：用 `electron win-unpacked/Aragon Trading.exe` 真起 GUI 再自检——需要显示器 / xvfb、慢且易抖，且 ambient `ELECTRON_RUN_AS_NODE=1` 干扰；GUI 编排（窗口/菜单/退出）改由**人工启动清单**（§8.4）覆盖。
- **D2 — PASS 要求 `live` 真实数据，OKX 不可达只接受「诚实错误」。**
  默认 `verify:dist` 必须拿到 `X-Aragon-Data-Source: live` 才算「数据真实可用」；提供 `--allow-okx-down` 逃生阀：离线/受限网络下，`/api/candles` 返回**显式 5xx**（而非假数据）也判 PASS-with-warning，但**绝不**把 `cache` 或任何兜底当 live。
  *被否方案*：任意 200 即通过——会把缓存/假数据冒充实时，违背硬约束 C1。
- **D3 — 复用 `config.js` 与 `process-manager.js`（二者均 Electron-free），不另写一套。**
  端口/URL/超时/env 组装来自 `src/lib/config.js`；spawn/HTTP 就绪轮询/优雅终止+兜底 tree-kill 来自 `src/lib/process-manager.js`（已确认仅依赖 `child_process`/`http`/`net`，无 `electron`）。单一真源，避免端口/清理策略漂移。
  *被否方案*：在 verify 里重抄一套 spawn/kill——必然与运行期实现漂移。
- **D4 — 「关于」对话框放在桌面外壳（`main.js` 菜单），不碰渲染层。**
  维持 `apps/web` 零源码改动的不变量：版本/数据来源声明走 `dialog.showMessageBox`，而非往前端塞 footer。
- **D5 — 图标用确定性脚本生成（Pillow 画图元），不引入美术资源/ SVG 渲染器。**
  `make-icons.py` 用 Pillow 在品牌深色底（`#0b0d12`）上画圆角块 + 字母「A」单字母标，导出多尺寸 `.png` 与多分辨率 `.ico`，可被纯编码 agent 复现、可重跑。
  *被否方案*：手绘 PNG 二进制 blob——不可复现、不可审计。
- **D6 — `verify:dist` 不进托管 CI，作为本地/发布前手动验收门。**
  托管 CI 缺 Windows 后端 exe、缺可达 OKX、且每次要下 120 MB Electron + 跑分钟级构建，强行入 CI 既慢又抖且会假绿。CI 只新增**静态校验** job；`verify:dist` 在发布机本地跑（§8）。**如实声明**此覆盖边界，不假称 CI 已覆盖端到端。
  *与 P1-5 一致*：该静态 job 用 `npm install --ignore-scripts`（`desktop/` 无 lockfile，且必须**跳过** electron postinstall 的 111 MB 二进制下载，否则就违背本决策初衷），只为让 `node --check` 跑起来——连 Electron 二进制都不需要。

---

## 4. Technical Design（技术设计）

### 4.1 受影响范围

| 类别 | 内容 |
|---|---|
| 新增 | `desktop/scripts/verify-dist.mjs`（G1/G2）、`desktop/scripts/make-icons.py`（G5） |
| 增量修改 | `desktop/src/main.js`（菜单加「关于」+ 窗口图标，G3）、`desktop/package.json`（新增 scripts，G1/G5）、`.github/workflows/ci.yml`（加 `desktop` job，G4）、`desktop/README.md`（发布验收清单，G4）、`README.md`（一句话指向，G4）、`build/icon.{ico,png,icns}`（图标资产替换，G5） |
| 零改动（不变量） | `apps/web/**`、`services/quant/**`、`desktop/scripts/{build-backend.py,build-web.mjs,dev-launch.js}`、`desktop/src/lib/{config,paths,process-manager}.js`、`desktop/electron-builder.yml`、`desktop/aragon-quant.spec`、端口 18760/18761、DB schema |

### 4.2 `verify:dist` 端到端时序（核心，G1/G2）

`verify-dist.mjs` 为 ESM；用 `createRequire(import.meta.url)` 引入 CJS 的 `config.js` 与 `process-manager.js`。

```
0. 解析路径
   DESKTOP = <repo>/desktop
   UNPACKED = DESKTOP/dist/win-unpacked
   RES = UNPACKED/resources
   BACKEND_EXE = RES/backend/aragon-quant.exe（非 win 去 .exe）
   WEB_MANIFEST = RES/web/web-server.json → { serverJs }（相对 RES/web 的 POSIX 路径）
   SERVER_JS = join(RES/web, manifest.serverJs)；WEB_CWD = dirname(SERVER_JS)

1. 前置校验（任一缺失 → FAIL，提示「先 npm run smoke 或 npm run dist」）
   assert exists(UNPACKED) && exists(BACKEND_EXE) && exists(WEB_MANIFEST) && exists(SERVER_JS)

2. 端口预检：portFree(18760) && portFree(18761)（被占 → FAIL，给释放命令）

3. 起后端（复用 prod 规格）
   const env = cfg.backendEnv({mode:'prod', dbPath:<os.tmpdir>/aragon-verify-<pid>.db})
   delete env.ALLOW_CACHED_FALLBACK    // P1-3：清 ambient 泄漏，强制「诚实默认」——不许缓存冒充 live
   delete env.ELECTRON_RUN_AS_NODE     // 防御：verify 跑 node，不应受 ambient RUN_AS_NODE 影响（R7）
   pm.startChild({ name:'backend', command:BACKEND_EXE, args:['--port','18760'],
                   cwd:RES/backend, env })
   waitForHttp(cfg.BACKEND_HEALTH_URL, cfg.BACKEND_READY_TIMEOUT_MS)   // 60s

4. 后端断言（HTTP）
   a) GET /api/health → 200 JSON，status==='ok'，记录 okx∈{up,down}；
      **断言 cachedFallbackEnabled===false**（P1-3：把「真实数据硬约束」做成主动校验；
      仅 --allow-okx-down 下放宽为「记 WARN 不 FAIL」，以兼容刻意开回退的离线排障）
   b) GET /api/candles?instId=BTC-USDT-SWAP&bar=1H&limit=120
      - 200 + 头 X-Aragon-Data-Source=='live' →（真实数据）断言 body.length>=50（P2-4：放宽下界，
        容忍 OKX history-candles 单次 100 上限/分页/限频抖动，仍足证「拿到成片真实 K 线」）、
        ts 升序、ts<2_000_000_000（秒级）、每根 c>0    ⇒ 「数据真实可用」PASS
      - 头=='cache' 或 'stale' → FAIL（不可把缓存当 live；除非 --allow-okx-down 则记 WARN）
      - 5xx（OKX 不可达且未开回退）→ 默认 FAIL（提示代理）；--allow-okx-down 时记 WARN-PASS（诚实错误，非假数据）

5. 起前端（复用 prod 规格，但用本进程的 node 直接跑 standalone）
   const webEnv = cfg.webEnv({mode:'prod'})   // 含 PORT=18761/HOSTNAME/NODE_ENV=production
   delete webEnv.ELECTRON_RUN_AS_NODE         // P1-3 防御：跑 node 不应带 RUN_AS_NODE（R7）
   pm.startChild({ name:'web', command:process.execPath, args:[SERVER_JS], cwd:WEB_CWD, env:webEnv })
   waitForHttp(cfg.WEB_URL, cfg.WEB_READY_TIMEOUT_MS)  // 90s

6. 前端断言（HTTP，证明「功能正常」的真实集成）
   a) GET WEB_URL/ → 200，content-type text/html，body 含 '<title' 或 'Aragon'
   b) GET WEB_URL/api/py/health → 200 JSON status==='ok'   // 前端 BFF 代理真打到后端

   ⚠️ P1-1 控制流：步骤 3-6 的「起进程 + 断言」整体置于 try；步骤 7-8 的「拆解 + 残端口校验」
      置于 finally —— 任一断言抛错（FAIL）也必经清理，杜绝孤儿进程占住 18760/18761 致下次撞 exit-11。

7. （finally）拆解：pm.stop()（优雅 → 兜底 tree-kill）；删临时 DB（unlink，吞 ENOENT）。
   ── 若 --keep-running（P2-3）：跳过本步与步骤 8，保留子进程与临时 DB，打印
      「子进程保留中；排障毕请手动释放：Get-NetTCPConnection -LocalPort 18760,18761 | …; Stop-Process …」。

8. （finally）残端口断言——**轮询而非单次**（P1-2）：
   在 ≤5s 内每 250ms 重试 portFree(18760)&&portFree(18761)（Windows taskkill /T 异步、
   子进程树 socket 释放有数百 ms~秒级延迟）；超时仍占 → exit-40，否则证明无孤儿进程。

9. 打印结构化报告；退出码：0=PASS（或 WARN-PASS），非 0=FAIL（分步码见 §8.2）
```

> 关键点：第 5 步用「跑 verify 的同一个 `node`」直接跑 standalone `server.js`（standalone 目标即纯 Node ≥18），与 prod 里「Electron 自带 Node + `ELECTRON_RUN_AS_NODE=1`」语义等价但更易在脚本环境复现；第 6b 步 `/api/py/health` 经 Next standalone 的 `rewrites` 代理到 `:18760`（该目标在 `build:web` 构建期已烤定为 `http://127.0.0.1:18760`），是最贴近用户真实体验的集成断言。
> **P2-2（建议增强）**：构造 `ProcessManager` 时传 `onUnexpectedExit`，子进程在就绪轮询期间**早退**（如后端 exe 崩溃）即立刻 FAIL 并打印 `name/code/signal`，免去干等满 60s/90s 才报「未就绪」，诊断更精确。

### 4.3 「关于」对话框与窗口图标（G3，`main.js` 增量）

- `createWindow()`：`BrowserWindow` options 增 `icon`——仅当 `build/icon.png` 存在时设（dev 期 `desktop/build/icon.png` 可达；打包后窗口图标由 exe 图标承载，故 `try/exists` 防御，不存在则不设，零回归）。
- `buildMenu()` 的「帮助」子菜单增 `{ label:'关于 Aragon Trading', click: showAbout }`，置于「打开日志目录」之上。
- 新增 `showAbout()`：`dialog.showMessageBox(win, { type:'info', title:'关于 Aragon Trading', message:'Aragon Trading', detail: <见 §6.3>, buttons:['打开日志目录','确定'], defaultId:1, cancelId:1 })`；点「打开日志目录」→ `shell.openPath(paths.ensureLogsDir())`。
- 版本来自 `app.getVersion()`；运行期版本来自 `process.versions`（electron/node/chrome）；commit 可选——若构建期注入了 `process.env.ARAGON_COMMIT` 则附上，否则省略（不强依赖）。
- IPC 不变（`preload.js` 已暴露 `aragon.appVersion()`，无需新增）。

### 4.4 品牌图标生成（G5，`make-icons.py`）

- 纯 Python + Pillow（构建期工具，脚本内 `pip install pillow`，不写入 `requirements.txt`，同 `pyinstaller` 的处理）。
- 画法：方形画布，底色 `#0b0d12`（与窗口 `backgroundColor` 一致），居中圆角块 + 单字母「A」（描边/留白对齐 Anthropic 克制风格）；尺寸集 `[16,24,32,48,64,128,256]`。
- 导出：`build/icon.png`（256）、`build/icon.ico`（多分辨率打包）。`.icns`（mac）Pillow 写出受限，**尽力**导出 512 `png` 占位并在脚本输出提示「mac 正式产物须在 mac 上重生成」（R-icns）。
- `electron-builder.yml` 的 `win.icon: build/icon.ico` 路径不变——只换内容，配置零改动。

### 4.5 与既有不变量的衔接校验

- 端口仍取自 `config.js`（18760/18761）；若日后改端口，§5 文件清单与 README「端口同步点（第 5 处）」仍是唯一改动点，`verify:dist` 自动随 `config.js` 走，无需另改。
- 真实数据硬约束（C1，**P1-3 强化**）：`cfg.backendEnv` 会 `...process.env` 展开，故仅「不设」`ALLOW_CACHED_FALLBACK` **不足以**保证诚实默认（ambient 可能已导出 `=true`）。verify-dist 因此**主动**：① 起后端前 `delete env.ALLOW_CACHED_FALLBACK`（§4.2 step3）；② 经 `/api/health` **断言 `cachedFallbackEnabled===false`**（§4.2 step4a）。双保险下，后端在 OKX 不可达时必返回显式 502，verify 据此判 FAIL（或 `--allow-okx-down` 判 WARN）；缓存绝无可能冒充 `live`。

---

## 5. File / Module Change Plan（文件 / 模块变更计划）

| 文件 | 动作 | 一句话意图 |
|---|---|---|
| `desktop/scripts/verify-dist.mjs` | 新增 | 驱动 `win-unpacked/` 真实产物做端到端验收（后端 exe + standalone web），断言真实 OKX 数据 + 前端代理 + 无残端口，PASS/FAIL 退出码（G1/G2） |
| `desktop/scripts/make-icons.py` | 新增 | Pillow 确定性生成多尺寸品牌图标，替换占位 `build/icon.*`（G5） |
| `desktop/package.json` | 修改 | 增 `verify:dist`、`release:check`（= smoke→verify:dist）、`icons` 脚本；**增 `check:verifydist`（`node --check scripts/verify-dist.mjs`）纳入 `check:*` 组（P1-4，否则 CI 静态门检不到本轮核心新文件）**；不动现有脚本签名（G1/G5） |
| `desktop/src/main.js` | 修改 | `createWindow` 设窗口图标（防御性）；菜单加「关于 Aragon Trading」+ `showAbout()`（G3） |
| `desktop/build/icon.ico` | 替换 | 占位 → 真品牌多分辨率 ico（G5） |
| `desktop/build/icon.png` | 替换 | 占位 → 256 品牌 png（G5） |
| `desktop/build/icon.icns` | 替换/保留 | 尽力生成；不可生成则保留占位并提示 mac 重生成（G5/R-icns） |
| `.github/workflows/ci.yml` | 修改 | 增 `desktop` job（`runs-on: ubuntu-latest`，同既有 job）：`npm install --ignore-scripts --no-audit --no-fund` + `npm run check`（**P1-5**：`desktop/` 无 lockfile 故不用 `npm ci`；`--ignore-scripts` 跳过 electron postinstall 的 ≈111 MB 二进制下载，与 D6/R9「CI 只静态、不下 120 MB Electron」一致，job 秒级且不抖。`npm-run-all` 等纯 JS 包仍正常装，`node --check` 不需 electron 二进制）（静态校验，G4） |
| `desktop/README.md` | 修改 | 增「发布验收清单」段：每条全局目标 → 一条可执行校验（G4） |
| `README.md` | 修改 | 「快速开始 / 桌面端」处补一句指向 `verify:dist` 与本 spec（G4） |
| `docs/plans/desktop-release-acceptance/spec.md` | 新增（本文件） | 本轮设计文档 |

> 严禁触碰：`apps/web/**`、`services/quant/**`、`desktop/scripts/{build-backend.py,build-web.mjs,dev-launch.js}`、`desktop/src/lib/**`、`desktop/electron-builder.yml`、`desktop/aragon-quant.spec`。

---

## 6. Interface Design（接口 / 契约设计）

### 6.1 npm 脚本（`desktop/package.json`）

| 脚本 | 命令 | 说明 |
|---|---|---|
| `verify:dist` | `node scripts/verify-dist.mjs` | 对已存在的 `dist/win-unpacked/` 做端到端验收（前置须先 `smoke`/`dist`） |
| `release:check` | `run-s smoke verify:dist` | 一条龙：构建免安装产物 → 立刻验收（发布前推荐） |
| `icons` | `python scripts/make-icons.py` | 重生成品牌图标（改图标时手动跑） |
| `check:verifydist` | `node --check scripts/verify-dist.mjs` | **P1-4**：把新增 `.mjs` 纳入 `check:*` 静态校验组（CI desktop job 经 `npm run check` 覆盖）。`make-icons.py` 为 Python，`node --check` 不适用，由 CI 静态门**不覆盖**、靠运行时 `npm run icons` 保障（如实声明） |

### 6.2 `verify-dist.mjs` CLI

```
node scripts/verify-dist.mjs [--allow-okx-down] [--keep-running] [--unpacked <dir>]
  --allow-okx-down   OKX 不可达时，诚实 5xx 也判 WARN-PASS（默认：要求 live 真实数据，否则 FAIL）
  --keep-running     断言后不拆解子进程（人工接续排障用）；**跳过残端口断言、保留临时 DB**（P2-3），
                     并打印手动释放 18760/18761 的命令。默认（不带本旗标）：finally 中拆解 + 删临时 DB + 轮询校验无残端口
  --unpacked <dir>   覆盖默认的 dist/win-unpacked 路径（默认自动定位）
```

退出码：`0`=PASS / WARN-PASS；其余见 §8.2（与 `smoke_e2e.py` 同风格的分步码，便于 CI/脚本定位）。

### 6.3 「关于」对话框 `detail` 文案契约（G3）

```
Aragon Trading  v<version>
多策略量化模拟交易平台（桌面端）

Electron <electron>  ·  Node <node>  ·  Chromium <chrome>
appId: com.aragon.trading
端口：后端 127.0.0.1:18760 / 前端 127.0.0.1:18761

数据来源：仅采用 OKX 真实行情；OKX 不可达时显式报错，绝不使用假数据兜底。
日志目录：<userData>/logs
```

### 6.4 复用的既有接口（不新增、不修改）

- `config.js`：`BACKEND_PORT/WEB_PORT/BACKEND_URL/WEB_URL/BACKEND_HEALTH_URL/BACKEND_READY_TIMEOUT_MS/WEB_READY_TIMEOUT_MS`、`backendEnv({mode,dbPath})`、`webEnv({mode})`。
- `process-manager.js`：`ProcessManager`、`portFree(port)`、`waitForHttp(url,timeoutMs)`、`delay(ms)`。
- 后端 HTTP（来自 `services/quant`，零改动）：`GET /api/health`、`GET /api/candles`（响应头 `X-Aragon-Data-Source: live|cache` + `X-Aragon-Stale`）。
- 前端 BFF：`GET /api/py/health`（standalone rewrites 代理到后端）。

---

## 7. Data Model（数据模型）

本轮**不引入 DB schema 变更**，仅有以下进程内/磁盘临时形状：

- **`web-server.json`（既有、只读消费）**：`{ "serverJs": "<相对 resources/web 的 POSIX 路径>", ... }`——`verify-dist` 仅读 `serverJs` 定位 standalone 入口（与 `paths.readWebServerManifest` 同源，不硬编码嵌套路径）。
- **验收报告（进程内 → stdout）**：`steps: Array<{ id:string, label:string, ok:boolean, warn?:boolean, detail?:string }>`；汇总 `{ pass:boolean, warnings:number, durationMs:number }`。可选 `--json` 旁路打印同结构（实现可选，便于上层解析）。
- **临时 DB**：`os.tmpdir()/aragon-verify-<pid>.db`，注入给打包后端（`DB_PATH`）。**P1-1：在 `finally` 中 unlink（吞 `ENOENT`），无论 PASS/FAIL 均删**（`--keep-running` 例外：保留供排障）——**绝不**触碰 `%APPDATA%/Aragon Trading/aragon.db`（保护真实用户数据）。
- **「关于」对话框**：纯运行期读取 `app.getVersion()` / `process.versions`，无持久化。

---

## 8. Testing & Acceptance Criteria（测试与验收）

### 8.1 全局目标 → 可执行校验映射（本轮核心，必过）

| 全局目标子句 | 验收手段 | 通过判据 |
|---|---|---|
| 确保**能成功打包** | `cd desktop && npm run smoke`（或 `npm run dist`） | 出 `win-unpacked/` 与/或安装包，无 EPERM、无报错退出 |
| 确保**可用**（起得来、能交互） | `npm run verify:dist` 步骤 3/5/6a | 后端 60s 内 `/api/health` 200；前端 90s 内 `/` 200 text/html |
| **数据真实可用** | `npm run verify:dist` 步骤 4b | `/api/candles` 头 `X-Aragon-Data-Source: live` + 形状校验（≥50 根、ts 升序秒级、c>0）；且 `/api/health.cachedFallbackEnabled===false`（P1-3） |
| **功能正常**（端到端集成） | `npm run verify:dist` 步骤 6b | 前端 `/api/py/health` 200（BFF 代理真打到后端） |
| 退出**干净**（无孤儿占端口） | `npm run verify:dist` 步骤 8 | 拆解后 18760/18761 数秒内重新 `portFree` |

### 8.2 `verify:dist` 退出码（分步定位）

`0`=PASS/WARN-PASS；`10`=前置产物缺失；`11`=端口被占；`20`=后端未就绪；`21`=health 非 ok；`22`=candles 非 live（且未 `--allow-okx-down`）；`23`=K 线形状不合理；`24`=`cachedFallbackEnabled!==false`（且未 `--allow-okx-down`，P1-3）；`30`=前端未就绪；`31`=前端首页异常；`32`=BFF 代理异常；`40`=拆解后**轮询 ≤5s 仍**端口未释放（P1-2）。

### 8.3 真实数据硬约束专项（必过）

- **诚实默认双保险（P1-3）**：起后端前 `delete env.ALLOW_CACHED_FALLBACK`；步骤 4a 断言 `/api/health.cachedFallbackEnabled===false`。默认运行下二者任一不满足即 FAIL（exit-24），杜绝 ambient `ALLOW_CACHED_FALLBACK=true` 泄漏放开缓存回退。
- 默认运行（联网经 Clash `127.0.0.1:7897`）：步骤 4b 必须 `live`；若得到 `cache`/`stale` → **FAIL**（不可把缓存当实时）。
- `--allow-okx-down`（离线）：步骤 4b 得到显式 5xx → WARN-PASS（诚实错误，非假数据）；若此时仍能拿到 200 非 `live` 数据 → **FAIL**（说明出现了不该有的假数据兜底）。

### 8.4 人工 GUI 启动清单（覆盖 D1 未自动化的窗口/菜单/退出）

1. `cd desktop && npm run dev` → 弹出**原生窗口**（非浏览器），左图右策略加载、5 策略可切换、K 线为真实 OKX 数据；DevTools 自动打开。
2. 菜单「帮助 → 关于 Aragon Trading」→ 对话框显示版本/数据来源声明；点「打开日志目录」可打开。
3. 关闭窗口 → 进程退出，`Get-NetTCPConnection -LocalPort 18760,18761` 应无残留。
4. （可选，发布机）双击 `dist/win-unpacked/Aragon Trading.exe` 或安装后启动 → 同上验证；任务栏/窗口图标为新品牌图标。

### 8.5 回归与静态校验（必过）

- `cd desktop && npm run check`（所有 `node --check` 通过；**P1-4 起含 `check:verifydist`** → 覆盖新增 `verify-dist.mjs`）。`make-icons.py`（Python）不在 `node --check` 覆盖范围，如实声明。
- 既有 `npm run dev`、`npm run smoke`、`npm run dist` 行为不回归（增量改动仅追加菜单项/图标/脚本）。
- 根 `pnpm test`、`scripts/smoke_e2e.py` 不受影响（未触碰其源码）。

### 8.6 验收命令速查（Windows / PowerShell）

```powershell
cd desktop
npm run check            # 静态语法校验
npm run release:check    # 构建免安装产物 → 端到端验收（联网，要求 live 真实数据）
# 离线/受限网络：
npm run smoke ; if ($?) { node scripts/verify-dist.mjs --allow-okx-down }
# 重生成品牌图标（改图标时）：
npm run icons
```

---

## 9. Risks & Mitigations（风险与缓解）

| 编号 | 风险 | 缓解 |
|---|---|---|
| R1 | OKX 受地域限制 / 代理未开 → `verify:dist` 默认硬 FAIL | 文档强调先开系统代理（Clash `127.0.0.1:7897`）。**P2-1**：`config.proxyEnv` 仅透传 `process.env` 中**已存在**的 `HTTP_PROXY/HTTPS_PROXY/ALL_PROXY`，故须确保它们**已导出为环境变量**（仅「系统代理开关」不够；本机 shell 启动已自动导出 `127.0.0.1:7897`，故默认可达）；离线用 `--allow-okx-down`（仍禁假数据）。此为既有 dev/prod 同一取数机制，非本轮新增风险 |
| R2 | standalone `server.js` 在 monorepo 下路径嵌套 | 不硬编码，复用 `web-server.json` 的 `serverJs`（同 `paths.readWebServerManifest`） |
| R3 | 残留 dev 会话占用 18760/18761 → 启动失败 | 步骤 2 端口预检，FAIL 时给 `Get-NetTCPConnection ... ; Stop-Process` 释放命令 |
| R4 | 用「跑 verify 的 node」跑 standalone 与 Electron 自带 node 版本不一致 | standalone 目标即纯 Node ≥18；README 要求 Node ≥18.18；行为等价（仅运行 server.js） |
| R5 | 误写/污染真实用户 DB | 强制用 `os.tmpdir` 临时 DB，结束即删；绝不指向 userData |
| R6 | `make-icons.py` 无法生成 `.icns` | 尽力出 png/ico；`.icns` 标注 mac 重生成（R-icns），不阻塞 Windows 验收（占位 icns 仍可让 win 构建通过） |
| R7 | ambient `ELECTRON_RUN_AS_NODE=1` / `NODE_ENV=production` 干扰 | verify 直接跑 `node`（非 electron），不受 RUN_AS_NODE 影响；`config.webEnv('prod')` 显式置 `NODE_ENV=production`；后端注入 `PYTHONUTF8` |
| R8 | 改 `main.js`（菜单/图标）回归打包 | 改动纯追加 + 防御性（图标 `exists` 才设）；改后跑 `npm run check` 与一次 `npm run smoke`+`verify:dist` 复核 |
| R9 | `verify:dist`/`dist` 入托管 CI 慢且抖（120 MB Electron 下载、无 Windows exe、无可达 OKX） | D6：CI 只做 `desktop` 静态校验；端到端验收作为本地发布门，文档如实声明覆盖边界 |
| R10 | Pillow 安装失败（网络/镜像） | `make-icons.py` 失败不阻断主流程（图标为 P2）；占位图标已能让 `dist` 成功；可改用本机已有 Pillow |

---

## 10. 实施顺序建议（给 Subtask #2）

1. **先 G1/G2（P0）**：写 `verify-dist.mjs`，跑 `npm run smoke` 出 `win-unpacked/` 后 `npm run verify:dist` 调通到 PASS（联网拿 `live`）。这是本轮价值锚点。
2. **G3（P1）**：`main.js` 加「关于」+ 窗口图标；`npm run check` + `npm run dev` 人工核对（§8.4 step1-2）。
3. **G4（P1）**：`ci.yml` 加 `desktop` job（`runs-on: ubuntu-latest` + `npm install --ignore-scripts --no-audit --no-fund` + `npm run check`，P1-5）；补 `desktop/README.md` 发布验收清单 + 根 `README.md` 一句话。
4. **G5（P2）**：`make-icons.py` 生成并替换图标；重跑 `smoke`+`verify:dist` 确认无回归。
5. 全程**不 `git commit`**（提交由 Subtask #3 精确暂存完成）；如发现方案缺陷，记入下方 §11。

---

## 11. 实施过程发现的方案缺陷（由 Subtask #2 填写）

> 由 Subtask #2 实施后回填。总体结论：**本方案 v2 与真实代码/产物高度吻合，无 P0/P1 级偏差**；所有接口签名、产物路径、standalone 行为均一次对齐落地。下列为实施中的核对结论、一处语义自洽化处理与运行时自证记录（供 Subtask #3 对照）。

### F1 — 方案核对结论（全部属实，无需偏离）

- `config.js` 接口（`BACKEND_PORT/WEB_PORT/BACKEND_URL/WEB_URL/BACKEND_HEALTH_URL/BACKEND_READY_TIMEOUT_MS/WEB_READY_TIMEOUT_MS/backendEnv/webEnv`）、`process-manager.js`（`ProcessManager/portFree/waitForHttp/delay`，含 `onUnexpectedExit` 与 `waitForHttp` 的 `isAborted` 选项）均如 §6.4 所述，二者确为 Electron-free，`createRequire` 引 CJS 成功。
- `web-server.json` 实测 `serverJs:"server.js"`（扁平构建钉准）；按 §4.2 step0 以 `join(RES/web, serverJs)` 解析、`dirname` 取 cwd，未硬编码嵌套路径。
- 后端 HTTP 契约属实：`/api/health` 含 `status/okx/cachedFallbackEnabled`；`/api/candles` 成功置头 `X-Aragon-Data-Source: live`、body `[{ts,o,h,l,c,vol}]`、`ts` 秒级升序；OKXError→`main.py` 统一转 **502**；`/api/py/health` 经 standalone rewrites 代理到 `:18760` 真打通。

### F2 — 一处语义自洽化（非偏离，记录备查）

§4.2 step4b 与 §8.3 对「`--allow-okx-down` 下 200 非 live」的措辞略有张力（前者「cache/stale 记 WARN」、后者「200 非 live 一律 FAIL」）。实现按「**诚实优先**」自洽统一为：① `200+live`→PASS（形状校验）；② `200+cache/stale`（**有诚实标注**的真实历史）→默认 FAIL（exit-22），`--allow-okx-down` 记 WARN（遵 §4.2 字面）；③ `5xx`→默认 FAIL，`--allow-okx-down` 记 WARN-PASS（诚实错误）；④ `200` 但**既非 live 亦无 cache/stale 诚实标注**（疑似假数据）→**一律 FAIL**（即便 `--allow-okx-down`，遵 §8.3「绝不接受假数据」）。因起后端前已 `delete ALLOW_CACHED_FALLBACK` 且断言 `cachedFallbackEnabled===false`，分支②实际不可达，故此统一不改变默认/联网下的判定，仅消除文档张力。

### F3 — 运行时自证（放行条件 #4，本机联网实跑）

- `npm run check`：全 `check:*` 通过（含新增 `check:verifydist`，P1-4 落实）。
- `npm run verify:dist`（默认，对既有 `win-unpacked/`）：**PASS**，exit 0，~15s。实拿 `X-Aragon-Data-Source: live`、120 根真实 OKX K 线（ts 升序秒级、c>0）、`okx=up`、`cachedFallbackEnabled===false`、前端 `/`+`/api/py/health` 200、拆解后 18760/18761 轮询内释放，无孤儿。
- `npm run release:check`（`smoke` 全量重建 → `verify:dist`）：**PASS**，exit 0，~4min。`build:backend`（PyInstaller onedir）✓、`build:web`（隔离扁平 standalone）**无 EPERM**✓、`electron-builder --dir` 重打 `win-unpacked/`（**已嵌入本轮新生成的品牌图标**）✓、随后 verify:dist 对新产物再次 **PASS**（~28s，含冷启动）。

### F4 — 实现增强与无害观测

- 已采纳 **P2-2**：`ProcessManager` 传 `onUnexpectedExit`，配合 `waitForHttp` 的 `isAborted` —— 子进程在就绪轮询期早退即立刻 FAIL 并打印 `name/code/signal`，免干等满超时。
- **无害观测**：默认拆解时进程管理器日志打印 `backend/web exited (code=1)`，这是 Windows `taskkill /T` 强制结束被杀进程的正常返回（非真实错误）；残端口轮询确认端口已释放，PASS 不受影响。
- **R4 印证**：`verify:dist` 用「跑脚本的 node」（本机 Node v22）直接跑 standalone `server.js`（prod 主进程用 Electron 自带 Node + `ELECTRON_RUN_AS_NODE`），二者语义等价、均正常 `✓ Ready`，与 §4.2 注解一致。

---

## 评审结论（Review Verdict · Subtask #1）

### 结论：**有条件通过（Approved with Conditions）** — 版本升 **v2**

本方案经逐节对照真实代码核验：核心设计（驱动 `win-unpacked/` 真实产物做端到端 HTTP 验收、复用 electron-free 的 `config.js`/`process-manager.js`、以响应头 `X-Aragon-Data-Source: live` + `/api/health` 坐实真实数据、OKX 不可达只接受显式 502、临时 DB 隔离保护用户数据、纯增量零源码改动）**成立、可行、与代码库现状完全一致，无 P0**。它精准对齐全局目标「确保可用，数据真实可用」，并把「可用 / 数据真实 / 功能正常 / 干净退出」四子句各映射到一条可机器复验的断言——这正是最终验收轮应有的形态，规模恰当、不过度。

发现并已**就地修复全部 5 个 P1**（无遗留）：

- **P1-1**：清理逻辑入 `finally`，FAIL 路径不再遗留孤儿进程占端口（§4.2 step7/§7/§6.2）。
- **P1-2**：残端口断言改为带超时轮询，消除 Windows 异步 `taskkill` 致的假 FAIL（§4.2 step8/§8.2）。
- **P1-3**：起后端前显式 `delete ALLOW_CACHED_FALLBACK` + 断言 `cachedFallbackEnabled===false`，把「数据真实」硬约束从被动期望升级为主动校验，防 ambient 泄漏（§4.2 step3/4a、§4.5、§8.3、新增 exit-24）。
- **P1-4**：新增 `check:verifydist`，使 CI 静态门真正覆盖本轮核心新文件，杜绝假绿（§5/§6.1/§8.5）。
- **P1-5**：CI desktop job 改 `npm install --ignore-scripts`（无 lockfile + 跳过 111 MB Electron 下载）+ `runs-on: ubuntu-latest`，消除与 D6/R9 自身原则的矛盾（§4.1/§5/§10、D6）。

6 个 P2 已逐条记录（P2-1 代理须导出为环境变量；P2-2 早退检测；P2-3 `--keep-running` 语义；P2-4 形状阈值放宽至 ≥50；P2-5/P2-6 确认无碍），其中可低成本就地优化的（P2-1/2/3/4）已并入正文，余者留给 Subtask #2 酌情采纳。

### 放行条件（Subtask #2 实施时必须满足）

1. **严守 v2 正文的 P1 细化**：`finally` 清理、残端口**轮询**、env **显式 scrub** + `cachedFallbackEnabled` **断言**、`check:verifydist` 入 `check:*`、CI job 用 `--ignore-scripts`。任一缺失即视为未达本方案验收标准。
2. **数据真实性是硬门**：默认 `verify:dist` 必须实拿 `X-Aragon-Data-Source: live` + 形状校验 + `cachedFallbackEnabled===false` 三者同时满足才算 PASS；任何把 `cache`/`stale`/假数据当 live 通过的实现，一律打回。`--allow-okx-down` 仅在确证离线时使用，且仍须是显式 502（诚实错误），绝不接受假数据。
3. **不变量零破坏**：`apps/web/**`、`services/quant/**`、`desktop/src/lib/**`、`desktop/scripts/{build-backend.py,build-web.mjs,dev-launch.js}`、`electron-builder.yml`、端口 18760/18761、DB schema 维持零改动；`main.js` 改动须纯追加 + 防御性（图标 `exists` 才设）。
4. **运行时自证**：实现后须在本机联网实跑一次 `npm run release:check` 至 PASS（拿到 `live`），并跑 `npm run check` 全绿；将实跑结论与任何与本方案不符之处回填 §11，供 Subtask #3 对照。

满足上述条件即等同「通过」；本方案可进入 Subtask #2 代码开发。

— Reviewer: Subtask #1（Anthropic Eng. Design Review）｜核验依据：真实代码逐节比对（见「评审记录」开头清单）
