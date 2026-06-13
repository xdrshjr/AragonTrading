# Aragon Trading — 桌面端打包构建加固方案（消除 EPERM、确保 `npm run dist` 真正成功）(spec)

- **Feature**: `desktop-packaging-build-hardening`
- **文档版本**: **v2**（迭代 2 · Subtask #1 设计评审完成。v1 由 Subtask #0 方案设计产出；本版已就地修复全部 P0/P1 评审意见，见下方「评审记录」与文末「评审结论」。）
- **作者 / 角色**: Solution Architect (Anthropic Engineering)
- **创建日期**: 2026-06-13
- **迭代**: Iteration 2 / 3
- **全局目标**: 完成项目开发，确保可用，数据真实可用
- **本轮需求（原文）**: 「继续开发，需要支持桌面端打包，electron 打包，专门放在 desktop 目录下，并且支持在其下，直接运行 `npm run dev`，启动 electron 的桌面端的调试模式，而不是浏览器，确保能够成功打包，并且功能正常。」
- **基线**: 迭代 1（本轮 loop 的第 1 迭代，commit `2082189`）已交付 `desktop/` 完整 Electron 外壳 + 打包流水线，前序设计见 `docs/plans/desktop-electron-packaging/spec.md`（v2）。彼时 **dev 模式（`npm run dev`）与 `build:backend`（PyInstaller 冻结后端 `/api/health` 200、真实数据）均已实测通过**；唯独 **`build:web`（Next standalone）在本机被 `EPERM: operation not permitted, symlink` 硬阻**（见该 spec §11「发现 5」与 R1），导致全量 `npm run dist` / `npm run smoke` 末段无法跑通，被按 R13/C8 降级为 P2、需用户手动开 Windows「开发者模式」或以管理员运行才能绕过。
- **本轮目标（一句话）**: **把"需用户提权/开发者模式才能打包"这一硬约束彻底消除**——让 `npm run dist` 在**任意 Windows 环境（非管理员、未开发者模式）下一次成功**，产出可独立运行的桌面安装包，并**实测打包后功能正常、数据真实**（OKX 真实数据、零假数据），从而完整闭合本轮需求中"确保能够成功打包，并且功能正常"与全局目标"确保可用"。
- **关系声明**: 本文档是对 `desktop-electron-packaging`（v2）的**纯增量加固**，**只改 `build:web` 的构建方式与配套说明**（全部落在 `desktop/` 内的构建脚本与文档）。**不改** Electron 主进程编排、进程管理、路径解析、端口契约（仍 18760/18761）、`backend_entry`/PyInstaller spec、dev 模式行为；**不改** `apps/web` / `services/quant` 任何源码（含 `next.config.mjs`，维持其零侵入）；**不回退**迭代 1–3 的 5 条放行条件（① OKX 真实数据、严禁假数据兜底 ② 凭据不入库 ③ 全系统秒级 UTC ts ④ 锁 `lightweight-charts@^4.2` ⑤ SimBroker·VWAP·策略注册表合并规则）。

---

## 评审记录（Review Notes · Subtask #1）

> 评审人：Anthropic 工程团队资深评审。评审维度：**可行性 / 完整性 / 一致性 / 规模合理性**。评审方法：**逐节核对真实源码与工具链**（`apps/web/package.json`、`apps/web/next.config.mjs`、`desktop/scripts/build-web.mjs`、`desktop/package.json`、`desktop/.gitignore`、`desktop/electron-builder.yml`、`desktop/src/lib/paths.js`、`pnpm-workspace.yaml`，以及 `apps/web/node_modules/next@14.2.15` 的 `config-schema.js` 与 `build/index.js`），而非仅凭文档自洽。
>
> **核心结论先行**：方案主路径**根本可行**——已核实 `apps/web` 确为「无任何 workspace 内部依赖」的叶子包（deps 全为外部 npm 包），故「隔离副本 + 扁平 node_modules → 无符号链接 → `copyTracedFiles` 不再 `fs.symlink` → EPERM 根除」的因果链成立；`electron-builder.yml` 的 `extraResources(resources/web,resources/backend)` 与 `paths.js` 的 `readWebServerManifest()+findServerJs` 运行期契约确实不受本方案影响。**无 P0**。但在「`outputFileTracingRoot` 注入的具体写法」与「依赖安装/根判定的内部一致性」上发现 **2 个 P1**，均已在本版（v2）正文就地修复。

| # | 维度 | 严重度 | 概述 | 证据 / 影响 | 处置 |
|---|---|---|---|---|---|
| **N1** | 可行性 | **P1** | §4.2 步骤 3 / §3 D3·D4 / R2 指示把 `outputFileTracingRoot` 注入为 `nextConfig` 的**顶层键**——但在锁定的 **Next 14.2.15** 中该字段**仅存在于 `experimental` 命名空间**，顶层键会被 config 校验视为未知键而**静默忽略**。 | 实证：`next/dist/server/config-schema.js` 中 `outputFileTracingRoot` 全文仅出现 1 次且位于 `experimental` 块内（index 11060 > experimental 块起点 7817）；`next/dist/build/index.js:697,766` 读取的是 `experimental.outputFileTracingRoot`。后果：tracing root 注入沦为 no-op → Next 回退到「向上推断工作区根」，因仓库根存在 `pnpm-workspace.yaml`（声明 `apps/*`）而**把根判定为仓库根** → standalone 产物**深层嵌套**（`server.js` 不在根），且文档「扁平 standalone / 根 server.js」的保证**不成立**；同时 `next build` 会打印 Invalid-config 警告。注：因 `BUILD/node_modules` 已是扁平真实文件，EPERM 本身**不会**因此复发（构建仍成功），故定为 P1 而非 P0。 | **已修复**：§3 D3·D4、§4.2 步骤 3、§4.3、R2 全部改为注入 `experimental.outputFileTracingRoot`（当前 `next.config.mjs` 无 `experimental` 块，故注入一个新块），并标注版本敏感性（Next 15 才提升为顶层键）。 |
| **N2** | 一致性 / 完整性 | **P1** | §4.2 步骤 4 的 `npm install` 带 `--no-package-lock`，与 R2 兜底所依赖的「副本目录内已有自己的 `package-lock.json` → Next 以最近 lockfile 目录为根（多落在 BUILD）」**自相矛盾**：带该标志后 BUILD 内**根本不会生成 `package-lock.json`**。叠加 §4.2 步骤 2 的 cpSync 过滤器**未排除** `apps/web/pnpm-lock.yaml`（会被一并复制进 BUILD）与仓库根的 `pnpm-workspace.yaml`，根判定更易落到仓库根，使 R2 兜底失效。 | 实证：仓库根仅有 `pnpm-workspace.yaml`、lockfile 实际位于 `apps/web/pnpm-lock.yaml`；该 lockfile 被默认过滤器放行而复制进 BUILD。后果：兜底路径无法把根钉在 BUILD；且无 lockfile 还削弱 R9（版本漂移）的可复现性。 | **已修复**：§4.2 步骤 4 去掉 `--no-package-lock`（让 npm 在 git 忽略的 BUILD 内写 `package-lock.json`，兜底有效 + 可复现更强）；步骤 2 过滤器追加排除 `pnpm-lock.yaml`/`pnpm-workspace.yaml`/`.npmrc`/`tsconfig.tsbuildinfo`，避免误导根判定与噪声；R2、R9 同步修订。 |
| **N3** | 规模合理性 / 可靠性 | P2 | 默认主路径用 `npm install` **全量重新下载**依赖；而本机已存在可复用的 pnpm store。离线/弱网或镜像异常时，npm 路径比「复用 store 的 pnpm-hoisted」更易失败、更慢。 | 非阻断；属优化建议。 | 建议（未强制）：§4.2 步骤 4 增补「探测到 pnpm 与本地 store 时优先 hoisted 路径，npm 作兜底」的实施提示；保留二者等价（symlink-free）的结论。已在正文以提示形式补充，留待 Subtask #2 按环境择优。 |
| **N4** | 完整性 | P2 | §4.2 步骤 6 / §6.3 / §3 D3 多处声称 standalone「扁平、根 `server.js`」。该结论**仅在 N1 修复后**（`experimental.outputFileTracingRoot` 真正生效、根钉在 BUILD）才成立；嵌套场景由 `findServerJs`+`web-server.json` 兜底（R5）。 | 表述精确性问题，不影响正确性（运行期由清单+递归定位兜底）。 | **已澄清**：相关表述补注「以 N1 修复生效为前提；`findServerJs` 为嵌套安全网」。 |
| **N5** | 完整性 | P2 | R4 称「`node_modules` 可复用加速、每次重拷源码」。复用旧 `node_modules` 而 `apps/web/package.json` 依赖变更时，需依赖 `npm install` 每次运行来 reconcile；若误用 `--no-package-lock` + 复用，npm 可能跳过更新。 | 与 N2 同源。 | 随 N2 一并消解（保留 lockfile 后 `npm install` 能可靠 reconcile）；R4 补一句说明。 |
| **N6** | 规模合理性 | P2 | §5 把「在旧 spec `desktop-electron-packaging/spec.md` §11 追加交叉引用」列为 `M(opt)`。评审认为：跨文档回填属信息完备性增益，但**改他人已定稿文档**有越权风险。 | 非阻断。 | 维持其 `(opt)` 标注即可，**评审建议 Subtask #2 默认跳过**该可选项，仅在本 spec 内自洽即可。已在 §5 该行补注。 |

**通过性**：上述 **2 个 P1（N1、N2）已在 v2 正文全部就地修复**，无遗留 P0/P1；P2 为优化/澄清项，已在正文补注，不阻断实施。详见文末「评审结论」。

---

## 1. Overview（概述）

迭代 1 已经把 Aragon Trading 的 Web 应用成功封装为 Electron 桌面外壳：`npm run dev` 能拉起原生窗口、热更新、连真实 OKX 行情；PyInstaller 已能把 Python 后端冻结成自包含 exe 并自检 `/api/health` 通过。整条流水线只剩**最后一公里没有打通**：把前端做成 Next standalone 产物的 `build:web` 步骤，在**本机这种"pnpm 符号链接 node_modules + 未开 Windows 开发者模式 + 非管理员"**的常见环境下，会在 Next 的 `copyTracedFiles` 阶段抛 `EPERM: operation not permitted, symlink`。其根因是：Next 的 `output: "standalone"` 在收尾时要把被 trace 到的依赖**按原样复制进 `.next/standalone`**，而 pnpm 的 `node_modules` 内部大量使用**符号链接**（顶层包 → `.pnpm/<pkg>@ver/node_modules/<pkg>`，以及包之间的互链）；`copyTracedFiles` 遇到符号链接时会**用 `fs.symlink` 在目标处重建同名符号链接**，而在 Windows 上创建符号链接需要 `SeCreateSymbolicLinkPrivilege` 特权——该特权仅在"开发者模式开启"或"管理员运行"时才对普通进程开放，否则即 `EPERM`。因此这**不是代码缺陷，而是 pnpm 链接策略 × Windows 符号链接权限模型**共同造成的环境硬约束。

本轮要做的，是把这条"必须提权"的依赖**从构建流程里彻底去掉**，而不是要求用户去改系统设置。关键洞察来自对 `apps/web/package.json` 的核对：**`apps/web` 没有任何 workspace 内部依赖**（`next`、`react`、`react-dom`、`lightweight-charts`、`zustand`、`@tanstack/react-query`、`clsx` 全是外部 npm 包，devDeps 亦然），它在 monorepo 里是叶子包。既然如此，就**完全可以在一个隔离的、扁平（hoisted、无符号链接）的 `node_modules` 上去做 standalone 构建**——一旦依赖树里没有符号链接，`copyTracedFiles` 复制的就全是真实文件，**再也不会调用 `fs.symlink`，自然不再需要任何特权**，EPERM 从根上消失。

据此，本方案重写 `desktop/scripts/build-web.mjs`：在 `desktop/.web-build/`（构建临时目录、git 忽略）里复制一份 `apps/web` 源码、对它做一次**扁平安装**（`npm install` 默认产出扁平真实 `node_modules`；亦可用 `pnpm --node-linker=hoisted` 复用 store 走硬链接更快），再在这份隔离副本上跑 `NEXT_OUTPUT_STANDALONE=1` 的 `next build`，最后把 standalone 产物装配进 `desktop/resources/web/` 并写 `web-server.json`。整个过程**不动 `apps/web` 源码、不动 `next.config.mjs`、不改 dev 模式、不需要任何系统权限**。在此基础上，本轮还要**真正把全量 `npm run dist` 与打包后应用的功能验收跑通**（尤其是迭代 1 v2 评审遗留的最终验收点 C5：打包后 `/api/py/*` 代理连通、取到 OKX 真实数据），把"确保能够成功打包，并且功能正常"落到实测证据上。

---

## 2. Goals / Non-Goals（本轮）

### Goals
1. **消除 EPERM 提权依赖**：`cd desktop && npm run build:web` 在**非管理员、未开 Windows 开发者模式**的环境下**一次成功**产出 Next standalone 产物并装配到 `desktop/resources/web/`。
2. **全量打包成功**：`npm run dist`（=`build:backend`→`build:web`→`electron-builder`）在本机端到端跑通，`desktop/dist/` 产出 Windows 安装包（NSIS `*.exe`）与/或 portable。
3. **打包后功能正常 + 数据真实**：安装/运行打包产物后，K 线、5 策略切换、回测/对比/历史、实时或回放模拟、CSV 导出与 Web 版一致；**`/api/py/*` 同源代理连通后端**、WS 实时通道连通；OKX 可达时取**真实数据**，不可达时沿用既有诚实错误态——**零假数据**（放行条件①不回退）。
4. **零侵入维持**：`apps/web`/`services/quant` 源码（含 `next.config.mjs`）零改动；dev 模式（`npm run dev`）行为与产物零回退；新增/修改全部落在 `desktop/` 内。
5. **可复现 + 可排障**：构建脚本对失败给出清晰提示；`desktop/README.md` 更新为"无需开发者模式即可打包"的新流程，并保留"提权/开发者模式"为兜底备选说明。

### Non-Goals（本轮不做）
- 不改 Electron 主进程编排 / 进程管理 / 路径解析 / 端口契约 / IPC（沿用迭代 1）。
- 不改 PyInstaller 后端冻结链路（`backend_entry.py`、`aragon-quant.spec`、`build-backend.py` 已实测通过，本轮不动）。
- 不接入真实下单 / 真实资金；不引入自动更新；不做代码签名 / 公证（仅沿用既有占位）。
- 不为 macOS / Linux 出正式产物（PyInstaller 不能交叉编译，沿用 Windows 一等公民）。
- 不改 5 策略、SimBroker、DB schema、任何 REST/WS 契约。
- **不通过"全局改 pnpm 链接策略"解决**（在仓库根 `.npmrc` 设 `node-linker=hoisted` 并重装会波及所有包、破坏既验证的 dev 流，违背零侵入）——隔离构建副本把改动限定在 `build:web` 一步内。

---

## 3. 关键设计决策（Design Decisions & Alternatives）

| # | 决策点 | 选择 | 理由 / 被否方案 |
|---|---|---|---|
| **D1** | 如何消除 EPERM | **在隔离副本 `desktop/.web-build/` 上用扁平（无符号链接）`node_modules` 做 standalone 构建** | 根因是 `copyTracedFiles` 重建 pnpm 符号链接需 Windows 特权。扁平 `node_modules` 里无符号链接 → 复制全为真实文件 → 不再 `fs.symlink` → 无需特权。`apps/web` 是无 workspace 内部依赖的叶子包，隔离扁平安装完全可行（§1）。 |
| **D2** | 扁平安装用什么包管理器 | **默认 `npm install --include=dev`**（产出扁平真实 `node_modules`）；**可选** `pnpm install --ignore-workspace`（配 `.npmrc: node-linker=hoisted`）走 store 硬链接更快 | npm 默认扁平、无符号链接、跨机普适、无需额外配置，作默认最稳。pnpm hoisted 同样扁平且能复用全局 store（免重复下载、更快），作为等价加速备选。被否：`cpSync(..., {dereference:true})` 直接解引用拷贝现有 pnpm `node_modules`——pnpm store 存在**自/互引用符号链接**，`dereference` 易触发 `ELOOP`/无限递归或巨量重复拷贝，不稳，弃用。 |
| **D3** | 隔离目录位置 | **`desktop/.web-build/`**（仓库内，git 忽略），并显式钉死 Next 的 **`experimental.outputFileTracingRoot=该目录`** | 放仓库内便于清理与定位，避免跨盘临时目录的慢拷贝/被清风险。**显式设 tracing root** 确保 Next **不**沿目录上溯到 monorepo 根（否则因仓库根 `pnpm-workspace.yaml` 声明 `apps/*`，Next 会把根推断为仓库根 → standalone 深层嵌套、并可能 trace 进真实 `apps/web` 的 pnpm 符号链接 node_modules），同时让 standalone 产物**扁平**（`server.js` 落在 standalone 根），简化装配。**版本敏感（评审 N1）**：在锁定的 **Next 14.2.15**，该字段仅 `experimental.outputFileTracingRoot` 有效（实证：`next/dist/server/config-schema.js` 仅在 `experimental` 块内声明此键，`next/dist/build/index.js` 读取 `experimental.outputFileTracingRoot`）；写成**顶层** `outputFileTracingRoot` 会被 config 校验当未知键**静默忽略**（Next 15 才将其提升为顶层稳定键）。 |
| **D4** | 是否改 `apps/web/next.config.mjs` 加 `experimental.outputFileTracingRoot` | **不改源码**；只在**构建副本**里注入 | 维持 `apps/web` 零侵入（放行原则）。副本是一次性产物，可由构建脚本读取原 `next.config.mjs` 文本、就地注入 `experimental.outputFileTracingRoot`，不污染主仓。被否：改主仓 config（侵入、且对常规 `next dev`/容器构建无必要）。 |
| **D5** | ambient `NODE_ENV=production` 对安装的影响 | **安装时强制带 `--include=dev`（并对 install 子进程置 `NODE_ENV=development`）** | 本机/CI ambient 预置 `NODE_ENV=production`，会让 `npm install` 默认 **omit devDependencies**——而 `next build` 必须有 `typescript`/`tailwindcss`/`postcss`/`autoprefixer` 等 devDeps，否则编译失败。`--include=dev` 显式纳入 devDeps，规避此坑（与迭代 1 §11「发现 3」同源环境问题）。`next build` 仍以 `NODE_ENV=production` 运行。 |
| **D6** | spawn npm/pnpm 的方式 | **`spawnSync('npm'/'pnpm', args, { shell: true, cwd, stdio:'inherit', env })`** | Node 20+（系统 node 或 Electron 内置）对 `.cmd`（`npm.cmd`/`pnpm.cmd`）的 `spawn` 有 CVE-2024-27980 安全限制，无 `shell:true` 会 `EINVAL`（迭代 1 §11「发现 2」同坑）。`shell:true` 规避。 |
| **D7** | 是否保留"开发者模式/管理员"老路径 | **保留为兜底备选，不再是必经** | 新扁平构建是默认主路径、无需特权；但文档保留"若坚持原地 pnpm 符号链接构建，可开开发者模式或管理员运行"，作信息完备与极端兜底（R3）。 |

---

## 4. Technical Design（技术设计）

### 4.1 受影响范围（仅 `build:web` 一步）

迭代 1 的流水线 `dist = build:backend → build:web → electron-builder` **只有中间一步 `build:web` 需要改造**；`build:backend`、`electron-builder`、主进程运行期路径解析（读 `desktop/resources/web/web-server.json`）、端口契约、env 契约**全部不变**——因为本方案**只改"如何产出 `desktop/resources/web/`"，不改"产出物的形态与契约"**。`build-web.mjs` 仍输出：`desktop/resources/web/<standalone 全树>` + `.next/static` + `public` + `web-server.json`（记录 `server.js` 相对路径）。主进程对此无感。

### 4.2 新 `build:web` 流程（`desktop/scripts/build-web.mjs` 重写）

记 `REPO=desktop 的父目录`、`WEB=REPO/apps/web`、`BUILD=desktop/.web-build`、`RES_WEB=desktop/resources/web`。步骤：

1. **清理并重建隔离目录**：`rmSync(BUILD,{recursive,force})` → `mkdirSync(BUILD,{recursive})`。
2. **复制源码（排除重型/无关目录）**：把 `WEB` 复制到 `BUILD`，**过滤掉** `node_modules`、`.next`、`coverage`、`.turbo`、`*.log`、`.git`，以及（**评审 N2**）`pnpm-lock.yaml`、`pnpm-workspace.yaml`、`.npmrc`、`tsconfig.tsbuildinfo`——这些 pnpm/workspace 标记若被复制进 `BUILD` 会误导 Next 的工作区根推断、并对随后的 `npm install` 造成噪声。用 `cpSync(WEB, BUILD, { recursive:true, filter })`，`filter(src)` 对上述目录/文件（按 basename 匹配）返回 `false`。复制内容含：`app/`、`components/`、`lib/`、`public/`、`package.json`、`next.config.mjs`、`tsconfig.json`、`postcss.config.*`、`tailwind.config.*`、其余源文件。
3. **注入 `experimental.outputFileTracingRoot`（仅副本，D3/D4；评审 N1 修复）**：读 `BUILD/next.config.mjs` 文本，若不含 `outputFileTracingRoot`，则在 `const nextConfig = {` 之后注入一个 **`experimental` 块**（当前 `next.config.mjs` 无 `experimental` 字段）：
   ```js
   experimental: {
     // Next 14.2.x 仅识别 experimental.outputFileTracingRoot（顶层键会被静默忽略；Next 15 才提升为顶层）。
     outputFileTracingRoot: path.dirname(fileURLToPath(import.meta.url)),
   },
   ```
   并在文件顶部补 `import { fileURLToPath } from 'node:url'; import path from 'node:path';`（若原文件未引入）。如此 `experimental.outputFileTracingRoot` 解析为 `BUILD` 自身——Next 不上溯到 monorepo 根，standalone 扁平且仅 trace 副本内的扁平 `node_modules`。（为最小化字符串注入脆弱性：脚本对"已含该字段"幂等跳过；注入后用 `node --check` 校验副本 config；若副本已有 `experimental` 块，则就地补 `outputFileTracingRoot` 子键而非重复注入。注入失败时的回退见 R2。）
4. **扁平安装依赖（D2/D5/D6；评审 N2/N3/N5）**：在 `BUILD` 下执行
   ```
   spawnSync('npm', ['install','--include=dev','--no-audit','--no-fund','--loglevel=warn'],
             { cwd: BUILD, shell:true, stdio:'inherit',
               env: { ...process.env, NODE_ENV:'development' } })
   ```
   npm 产出**扁平真实 `node_modules`（无符号链接）**。`--include=dev` 纳入 `typescript`/`tailwindcss`/`postcss`/`autoprefixer`。**去掉 `--no-package-lock`（评审 N2）**：让 npm 在（git 忽略的）`BUILD` 内写出 `package-lock.json`——这既使 R2 的根判定兜底真正有据（BUILD 内有 lockfile），又增强可复现性（R9），还让复用 `node_modules` 时的二次 `npm install` 能可靠 reconcile（N5）。若返回非 0：先重试一次带 `--legacy-peer-deps`（防 peer 误判）；仍失败 → 清晰报错退出（R1）。
   - **可选加速路径（评审 N3：本机已有 pnpm store 时建议优先）**：脚本探测到 `pnpm` 且本地 store 可用时优先此路——在 `BUILD` 写 `.npmrc`（含 `node-linker=hoisted`），执行 `pnpm install --ignore-workspace --prod=false --config.node-linker=hoisted`——同样扁平无符号链接、且复用全局 store 硬链接（免重复下载，离线/弱网更稳）。npm 路径作兜底；两路结果等价（symlink-free）。
5. **standalone 构建**：定位 `BUILD/node_modules/next/dist/bin/next`，执行
   ```
   spawnSync(process.execPath, [NEXT_BIN, 'build'],
             { cwd: BUILD, stdio:'inherit',
               env:{ ...process.env, ELECTRON_RUN_AS_NODE:'1',
                     NEXT_OUTPUT_STANDALONE:'1',
                     PY_BACKEND_URL:'http://127.0.0.1:18760', NODE_ENV:'production' } })
   ```
   关键点全部沿用迭代 1：`PY_BACKEND_URL` 构建期烤入 IPv4（standalone rewrites 运行期改不了，P1-5/R14）；`NEXT_OUTPUT_STANDALONE=1` 切 standalone。**因 `BUILD/node_modules` 扁平无符号链接 → `copyTracedFiles` 不重建符号链接 → 无 EPERM、无需特权。**
6. **装配产物到 `RES_WEB`**：沿用迭代 1 `assemble()` 逻辑，但**源根改为 `BUILD/.next/standalone`**：
   - `findServerJs(BUILD/.next/standalone)` 递归定位 `server.js`（**N1 修复生效**、tracing root 钉准 `BUILD` 时即根 `server.js`；否则嵌套，由递归定位兜底，N4/R5）；
   - `rmSync(RES_WEB)` 后整树 `cpSync(standalone → RES_WEB)`；
   - 把 `BUILD/.next/static` 拷到 `RES_WEB/<serverDir>/.next/static`、`BUILD/public` 拷到 `RES_WEB/<serverDir>/public`（standalone 不自带这两者，Next 文档要求手动补）；
   - 写 `RES_WEB/web-server.json`：`{ serverJs:<相对路径,POSIX 化>, builtAt:<ISO>, nextVersion:"14.2.15", buildMode:"hoisted-isolated" }`。
7. **（可选）保留 `BUILD`**：默认构建后**不删** `BUILD`（便于二次构建复用 `node_modules`、加速）；提供 `--clean` 标志强制清空。`BUILD` 已被 git 忽略。

### 4.3 为什么这样就根除了 EPERM（因果链）

```
pnpm 安装  →  apps/web/node_modules 内含大量符号链接（.pnpm 虚拟 store）
next build (standalone)  →  copyTracedFiles 遇符号链接  →  fs.symlink 重建
Windows 普通进程无 SeCreateSymbolicLinkPrivilege  →  EPERM ✗（迭代 1 阻塞点）
────────────────────────────────────────────────────────────────────────
本方案：隔离副本 + npm/pnpm-hoisted 扁平安装  →  node_modules 全为真实目录、零符号链接
next build (standalone)  →  copyTracedFiles 全程"真实文件 → 真实文件"复制  →  从不 fs.symlink
∴ 不触碰 Windows 符号链接特权  →  EPERM 不可能发生 ✓（任意权限环境均成功）
```

> **正交关注点（评审 N1/N4）**：EPERM 的根除**只依赖「BUILD 的 node_modules 无符号链接」**，与 tracing root 是否钉准**无关**（即使 root 误推到仓库根，被 trace 的文件仍解析自 `BUILD/node_modules` 的扁平真实文件，构建照样不报 EPERM）。`experimental.outputFileTracingRoot` 钉到 `BUILD` 的作用是**控制 standalone 产物布局**——钉准则 `server.js` 落在 standalone 根（扁平、装配简单）；若未钉准则深层嵌套，由 §4.2 步骤 6 的 `findServerJs`+`web-server.json` 兜底仍能正确定位（R5）。故 N1 的错误写法不会使构建失败，但会令「扁平产物」承诺落空并触发 Next 的 invalid-config 警告，须按 N1 修复用 `experimental` 命名空间。

### 4.4 与运行期的衔接（不变量校验）

- 主进程 `paths.js` 读 `desktop/resources/web/web-server.json` 的 `serverJs` 解析路径、以其所在目录为 `cwd`、用 `ELECTRON_RUN_AS_NODE=1 + process.execPath` 跑 `server.js`——**本方案保证 `web-server.json` 与产物结构契约不变**，故主进程零改动即可拉起新产物。
- `PY_BACKEND_URL=http://127.0.0.1:18760` 仍在 build 期烤入；后端 uvicorn 仍绑 `127.0.0.1:18760`；两端 IPv4 对齐 → 打包后 `/api/py/*` 代理连通（C5 验收点）。
- 渲染进程 WS 仍连构建期内联的 `ws://localhost:18760`，后端固定 18760 → 零改动可用。

---

## 5. File / Module Change Plan（文件 / 模块变更计划）

> 原则：改动**全部位于 `desktop/`**；`apps/web`/`services/quant` 源码（含 `next.config.mjs`）零改动。类型：C=新建、M=修改、(opt)=可选。

| 文件 | 类型 | 一句话意图 |
|---|---|---|
| `desktop/scripts/build-web.mjs` | **M** | **核心**：重写为"隔离副本 `desktop/.web-build/` + 扁平安装（`npm --include=dev` / 可选 `pnpm --node-linker=hoisted`）+ 注入 `experimental.outputFileTracingRoot`（N1）+ standalone 构建 + 装配 `resources/web/` + 写 `web-server.json`"，根除 EPERM（D1–D6）。`assemble()` 源根改 `BUILD/.next/standalone`；失败提示更新（提权改为兜底备选）。 |
| `desktop/.gitignore` | **M** | 追加忽略 `.web-build/`（隔离构建副本，含 `node_modules`/`.next`，不入库）。 |
| `desktop/README.md` | **M** | 「打包」章节更新：说明 `npm run dist` **无需开发者模式/管理员**即可成功（隔离扁平构建原理一句话）；标注首次 `build:web` 会在 `.web-build/` 做一次扁平安装（耗时/磁盘）；保留"提权/开发者模式"为兜底备选；同步 §6 验收命令。 |
| `desktop/package.json` | M (opt) | 可加 `build:web:clean`（`node scripts/build-web.mjs --clean`）便捷脚本；`scripts` 主结构（`dist`/`smoke`/`pack`/`build:web`）**不变**。 |
| `docs/plans/desktop-electron-packaging/spec.md` | M (opt) | 在其 §11「发现 5」/R1 处追加一行交叉引用："本环境硬阻已由 `docs/plans/desktop-packaging-build-hardening/spec.md` 的隔离扁平构建方案根除，不再需提权"。**仅注记，不改其结论正文**。**评审建议（N6）：默认跳过此可选项**——改他人已定稿文档有越权风险，本 spec 内自洽即可；如需回填，仅限追加注记、不得改其结论。 |

> **零改动核对（不变量）**：`apps/web/next.config.mjs`（`output` 受 `NEXT_OUTPUT_STANDALONE` 控制、`rewrites` 读 `PY_BACKEND_URL`）、`apps/web/lib/api.ts`（相对 `/api/py`）、`apps/web/lib/ws.ts`（默认 `ws://localhost:18760`）、`services/quant/app/main.py`、主进程 `src/**`、`backend_entry.py`/`aragon-quant.spec`/`build-backend.py` **全部不动**。本方案改动面**仅 `build:web` 脚本 + 两个文档/忽略文件**。

---

## 6. Interface Design（接口 / 契约设计）

本特性不新增 REST/WS/IPC 接口；其"接口"为 **npm 脚本签名、构建临时目录契约、构建清单 `web-server.json`**——均**保持迭代 1 契约不变**，仅内部实现变更。

### 6.1 npm 脚本（`desktop/package.json`，签名不变）
| 脚本 | 命令 | 本轮变化 |
|---|---|---|
| `build:web` | `node scripts/build-web.mjs` | **内部实现重写**（隔离扁平构建）；对外签名与产物契约不变 |
| `dist` | `run-s build:backend build:web dist:only` | 不变（现在能真正端到端成功） |
| `smoke` | `run-s build:backend build:web pack` | 不变（现在能真正端到端成功） |
| `pack` / `dist:only` | `electron-builder [--dir]` | 不变 |
| `build:web:clean`(opt) | `node scripts/build-web.mjs --clean` | 新增：强制清空 `.web-build/` 后重建 |

### 6.2 构建临时目录契约（新增，内部）
- `desktop/.web-build/`：`build:web` 的隔离工作区。含 `apps/web` 源码副本 + 扁平 `node_modules` + `.next/`。**git 忽略**；可安全删除（下次构建自重建）。复用其 `node_modules` 可加速二次构建。

### 6.3 构建清单 `web-server.json`（字段向后兼容）
```jsonc
{ "serverJs": "server.js",        // tracing root 钉准时即根 server.js（相对 resources/web/）；嵌套时为相对子路径，findServerJs 实测填入（N4/R5）
  "builtAt": "<ISO 时间>",
  "nextVersion": "14.2.15",
  "buildMode": "hoisted-isolated" } // 新增可选字段，标注构建方式；主进程忽略未知字段，兼容
```

---

## 7. Data Model（数据模型）

本特性**不改任何持久化 schema**（不动 SQLite 表、不动 OKX 数据形态、不动 DB 落点策略）。仅引入一个**磁盘构建态**：

- **`desktop/.web-build/`**（磁盘，临时）：`apps/web` 的隔离构建副本（源码 + 扁平 `node_modules` + `.next` 产物）。生命周期＝构建期；可重复使用或随时删除。不参与运行期、不入库。
- `web-server.json`（磁盘，构建产物清单）：新增可选字段 `buildMode`（见 §6.3），其余字段语义不变。
- 运行期数据模型（DB 落 `userData/aragon.db`、日志 `userData/logs/`、进程句柄表）**沿用迭代 1，不变**。

---

## 8. Testing & Acceptance Criteria（测试与验收）

### 8.1 EPERM 根除验收（本轮核心，必过）
- 在**当前本机环境**（**非管理员、未开 Windows 开发者模式**，pnpm 符号链接 node_modules）下：
  - [ ] `cd desktop && npm run build:web` **成功退出（rc=0）**，**无 `EPERM`/`symlink` 报错**；
  - [ ] 生成 `desktop/resources/web/server.js`（或 `web-server.json.serverJs` 所指路径）与 `.next/static`、`public`、`web-server.json`。

### 8.2 全量打包验收（必过）
- [ ] `npm run dist` 端到端成功，`desktop/dist/` 下生成 Windows 安装包（NSIS `*.exe`）与/或 portable。
- [ ] `npm run smoke`（`build:backend`+`build:web`+`pack`）成功产出免安装目录（`dist/win-unpacked`）。

### 8.3 打包后功能 + 真实数据验收（必过，含迭代 1 遗留 C5）
- 启动打包产物（`dist/win-unpacked/Aragon Trading.exe` 或安装后）：
  - [ ] 自带后端 exe + Next 服务独立起来，开窗加载完整应用（左 K 线 + 右 5 策略面板）。
  - [ ] **`/api/py/*` 同源代理连通**（DevTools/日志确认非 502/ECONNREFUSED）——**迭代 1 v2 评审 C5 最终验收点**（IPv4/IPv6 解析坑）。
  - [ ] 5 策略切换、回测得到 markers/净值、对比、历史、CSV 导出、实时或回放模拟与 Web 版一致；WS 连上。
  - [ ] OKX 可达（经系统代理 Clash `127.0.0.1:7897`）时取**真实数据**；不可达时显示既有诚实错误态，**无假数据**（放行条件①）。
  - [ ] DB 落 `%APPDATA%/Aragon Trading/aragon.db`，重启后历史 run 仍在；退出后 `Get-NetTCPConnection -LocalPort 18760,18761` 无残留。

### 8.4 回归与静态校验
- [ ] `npm run dev`（dev 模式）**行为零回退**：仍弹 Electron 原生窗口、热更新、真实数据、退出无孤儿（迭代 1 已过项复测）。
- [ ] `apps/web`/`services/quant` `git diff` 为空（零源码侵入）。
- [ ] `node --check scripts/build-web.mjs` 语法自检通过。
- [ ] 既有后端 pytest / 前端 vitest 不受影响。

### 8.5 验收命令速查（Windows / PowerShell）
```powershell
# 仅前端构建（验证 EPERM 已消除，无需任何提权）
cd desktop ; npm run build:web
# 快速冒烟（免安装目录）
npm run smoke
# 全量安装包
npm run dist
# 启动打包产物
& ".\dist\win-unpacked\Aragon Trading.exe"
# 端口残留检查
Get-NetTCPConnection -LocalPort 18760,18761 -ErrorAction SilentlyContinue
```

> **长任务提醒**：`npm install`（隔离副本，首次需下载依赖）+ `next build` + PyInstaller 合计可能 > 60s；实施/验收按"长任务异步等待协议"处理，勿同步死等。

---

## 9. Risks & Mitigations（风险与缓解）

| # | 风险 | 影响 | 缓解 |
|---|---|---|---|
| **R1** | **隔离副本 `npm install` 因网络/镜像/peer 冲突失败** | `build:web` 起步即败 | 用 npmmirror（本机默认）；命令带 `--no-audit --no-fund`；失败自动重试一次 `--legacy-peer-deps`；脚本打印失败的具体包与建议（换镜像/检查网络）。可选 `pnpm --node-linker=hoisted` 复用本地 store 免下载（D2）。 |
| **R2** | **`experimental.outputFileTracingRoot` 文本注入对 `next.config.mjs` 格式敏感而注入失败/未生效** | 副本 config 损坏或字段未生效，Next 推断根为仓库根（因根 `pnpm-workspace.yaml`）→ standalone 深层嵌套 | **键名按 N1 固定为 `experimental.outputFileTracingRoot`**（顶层键 14.2.x 无效）；注入幂等（已含则跳过）+ 注入后 `node --check` 校验副本 config。注入失败时回退：`next build` 以 `cwd: BUILD` 运行，且（**N2 修复后**）`BUILD` 内已有 `npm install` 写出的 `package-lock.json`、并已剔除复制来的 `pnpm-lock.yaml`/`pnpm-workspace.yaml`（步骤 2），有助于把根判定收敛到 `BUILD`。**关键澄清（评审 N1）**：即便根判定仍误落到仓库根，**EPERM 也不会因此复发**——被 trace 的文件解析自 `BUILD/node_modules` 的扁平真实文件，`copyTracedFiles` 从不重建符号链接；根判定**只影响产物嵌套层级**，由 `findServerJs`+`web-server.json` 兜底定位（R5）。 |
| **R3** | **构建机也禁用了 `npm`/`pnpm`（仅 pnpm 工作区）** | 扁平安装无法执行 | npm 随 Node 分发，几乎必在；pnpm 本项目已用。二者皆缺时，文档保留**兜底**：开 Windows 开发者模式或管理员运行后用原地 pnpm 符号链接做 standalone（迭代 1 老路径，D7）。 |
| **R4** | **`.web-build/` 体积大 / 二次构建陈旧** | 占盘、或源码改了但副本没更新 | `.web-build/` git 忽略；`build:web` **每次重新复制源码**（保证最新）且**每次都跑 `npm install`**——配合 N2 修复保留的 `package-lock.json`，复用 `node_modules` 时能可靠 reconcile 依赖变更（评审 N5）；`node_modules` 复用加速；提供 `--clean` 全清重建（§6.1）。 |
| **R5** | **standalone 产物路径在扁平构建下与迭代 1 嵌套假设不一致** | 主进程找不到 `server.js` | `findServerJs` 递归 + `web-server.json` 记录相对路径的机制**与迭代 1 完全一致**，扁平（根 `server.js`）/嵌套都兼容；主进程零改动（§4.4）。 |
| **R6** | **打包后 `/api/py/*` 仍 502（IPv4/IPv6 解析坑复发）** | 功能不可用 | 沿用迭代 1：build 期烤入 `PY_BACKEND_URL=http://127.0.0.1:18760`（IPv4）、后端绑 `127.0.0.1`；§8.3 把"代理连通"列为必过项，实测兜底（C5）。 |
| **R7** | **OKX 受地区限制不可达** | 取不到真实数据 | 透传系统代理 env（Clash `127.0.0.1:7897`）；不可达即诚实错误态，**严禁假数据**（放行条件①）。 |
| **R8** | **沙箱/CI 无法实测全量 `dist`**（需下载 Electron/PyInstaller 二进制、耗时） | 验收受限 | 至少 §8.1（`build:web` 无 EPERM）必须实测通过——这是本轮的硬目标；全量 `dist`/打包后功能若受沙箱网络限制，结论登记到实施记录（§11），保持纯增量、零侵入（承迭代 1 C8 口径）。**注意：本轮的"环境硬阻"已不再是 EPERM（已根除），而仅可能是二进制下载网络问题，性质不同。** |
| **R9** | **依赖版本漂移**（隔离 `npm install` 未锁版本，解析到与 pnpm 不同的次版本） | 理论上产物与 dev 行为微差 | `next@14.2.15`/`react`/`react-dom` 在 `package.json` 为精确版本；其余 caret 依赖为 UI/状态库，次版本兼容。**N2 修复后** `npm install` 会在 `BUILD` 写出 `package-lock.json`，**首次解析后即在 `.web-build/` 内自锁**，二次构建复用即稳定；如需跨机强可复现，可将该 `package-lock.json` 固化提交到 `desktop/`（本轮按需，非必须）。 |

---

## 10. 实施顺序建议（给 Subtask #2）

1. **先改 `desktop/scripts/build-web.mjs`**：实现 §4.2 的隔离副本 + 扁平 `npm install --include=dev`（**不带** `--no-package-lock`，N2）+ `experimental.outputFileTracingRoot` 注入（N1）+ standalone 构建 + 装配。**优先把 §8.1（`npm run build:web` 无 EPERM）实测跑通**——这是本轮最硬指标。
2. **跑 `npm run smoke`**：验证 `build:backend`+`build:web`+`pack` 端到端出 `win-unpacked`，主进程能拉起新前端产物。
3. **跑 `npm run dist`**：出 NSIS/portable 安装包；启动打包产物，过 §8.3（尤其 C5：`/api/py/*` 代理连通 + OKX 真实数据 + 零假数据）。
4. **复测 dev 模式（§8.4）零回退**；确认 `apps/web`/`services/quant` `git diff` 为空。
5. 更新 `desktop/.gitignore`（加 `.web-build/`）与 `desktop/README.md`（无需提权的新流程）；**不提交**任何构建产物（`dist/`、`resources/web|backend/`、`.web-build/`）。
6. 若任何步骤偏离本方案，在本 spec §11「实施过程发现的方案缺陷」就地登记并修订，保持 spec 与实现一致。

---

## 11. 实施过程发现的方案缺陷（由 Subtask #2 填写）

> 本节由 Subtask #2（代码开发）在本机（Windows 10.0.26200、**非管理员、未开开发者模式**、Node v22.18.0 / npm 10.9.3 / pnpm 10.29.1）逐项实测后据实登记。**总体结论：方案核心路线完全成立——EPERM 已被隔离扁平构建从根上消除（C1 实测通过），全量 `npm run dist` 端到端跑通并产出可安装的 NSIS/portable 安装包（C3 通过）。** 未发现方案性 P0/P1 缺陷；以下为实测确认与一个环境边界（与 R8 一致）。

| # | 类型 | 概述 | 实测证据 / 处置 |
|---|---|---|---|
| **F1** | **确认（C1/C2 通过）** | 隔离扁平构建**根除 EPERM** 成立。`build-web.mjs` 重写后，`cd desktop && npm run build:web` 在非管理员/未开开发者模式下 **rc=0、全程零 `EPERM`/`symlink`**。`experimental.outputFileTracingRoot` 注入被 Next 14.2.15 正确识别（构建日志出现 `- Experiments (use with caution): · outputFileTracingRoot`，**无 "Invalid next.config options" 警告**，证 N1 命名空间修复用对）；tracing root 钉准 `BUILD` 后 standalone **扁平**——`server.js` 落在根（`web-server.json.serverJs="server.js"`）。 | 产出 `resources/web/server.js` + `.next/static` + `public` + `web-server.json{buildMode:"hoisted-isolated"}`。`build:web` 在独立运行与 `npm run dist` 内各成功一次，均零 EPERM。 |
| **F2** | 实施确认（C6 / D2 取舍） | 采用**评审 C6 建议的 pnpm hoisted 优先路径**：探测到 `pnpm` 即写副本 `.npmrc(node-linker=hoisted)` 并 `pnpm install --ignore-workspace --prod=false --prefer-offline --config.node-linker=hoisted`，npm 作兜底。首次安装复用本地 store **54.5s**（resolved 315 / reused 258 / downloaded 3）、二次复用 `node_modules` **仅 1.5s**——证 N3「复用 store、弱网更稳」成立。**观察（非缺陷）**：pnpm hoisted 在解析阶段会尝试拉取 `@rollup/rollup-linux-*`、`@esbuild/android-*`、`@next/swc-linux-*` 等**跨平台可选依赖**（其他 OS/arch），弱网下 npmmirror 报 `ECONNRESET` 并重试——但这些是 `optionalDependencies`，**失败即跳过、非致命**，不影响 win32-x64 构建成功（spec D2 的默认 `npm` 路径因按 os/cpu 过滤可选依赖而无此噪声，二者均产出无符号链接的扁平 `node_modules`）。已对两条安装路径补 `--prefer-offline`，进一步贴合 N3 弱网取向。 | 非阻断；构建两次均成功。已在 `build-web.mjs` 安装命令补 `--prefer-offline`。 |
| **F3** | **环境边界（R8，与方案一致）** | 全量 `npm run dist` 中 **`build:backend`（PyInstaller → `aragon-quant.exe` 16MB）✓、`build:web`（standalone，零 EPERM）✓**；`electron-builder` 推进到 `packaging platform=win32 arch=x64 electron=31.7.7`、装好原生依赖后，**唯一卡点是下载 Electron 31.7.7 运行时 zip（约 111MB）**：GitHub 与 `cdn.npmmirror.com` 镜像**均 `EOF`**（本机受限网络对大文件断流）。**这正是 R8 预判的边界——已不是 EPERM，而是二进制下载网络问题，性质不同。** | **已绕过并完成打包**：`npm install` 已把 Electron 31.7.7 完整落到 `node_modules/electron/dist/`，故用 `npm run dist:only -- --config.electronDist=node_modules/electron/dist` **跳过下载**直接打包，**`npm run dist` 等价链路端到端成功**：产出 `dist/win-unpacked/`（含 `Aragon Trading.exe` + `resources/backend/aragon-quant.exe` + `resources/web/server.js`）与 **NSIS/portable 安装包 `dist/Aragon Trading-0.1.0-x64.exe`（约 120MB）**。该绕过仅用于本机验收，**未写入** `electron-builder.yml`（维持零侵入）；已在 `README.md` 排障章登记 `electronDist`/`ELECTRON_MIRROR` 两种受限网络处置。**C3（出安装包）实测达成。** |
| **F4** | 边界说明（C4，未头less实测） | C4「启动打包产物→`/api/py/*` 同源代理连通 + WS + OKX 真实数据」属**GUI 运行期功能验收**，需弹出原生窗口并经系统代理触达 OKX，**当前无头执行环境无法有意义地实测**。但本轮**未触碰**主进程编排 / `paths.js` 路径解析 / 端口契约（18760/18761）/ IPC / `next.config.mjs`（`PY_BACKEND_URL` 仍构建期烤入 IPv4 `127.0.0.1:18760`），运行期读取的 `web-server.json` 契约与迭代 1 完全一致（实测 `serverJs="server.js"` 已正确装配进 `win-unpacked/resources/web/`）。 | 故打包产物的运行期行为**等同迭代 1 已验证的形态**；C4 留作有显示环境时的人工/集成验收，本轮以「构建契约不变 + 产物结构正确」为间接证据，按 R8/C8 口径登记边界。 |

**零侵入复核（C5）**：`apps/web`、`services/quant` 全程 `git diff` 为空（含 `next.config.mjs` 未改）；改动仅落在 `desktop/`（`scripts/build-web.mjs` 重写、`.gitignore`、`package.json`、`README.md`）。构建产物（`dist/`、`resources/{web,backend}/`、`.web-build/`）均被 `.gitignore` 排除、不入库。`node --check scripts/build-web.mjs` 通过。`dev` 模式（`npm run dev`）路径（`dev-launch.js` 等）本轮未触碰，行为零回退。

---

> 全文 ≥ 800 字要求已满足。本设计聚焦"消除 EPERM 提权依赖、让 `npm run dist` 在任意权限环境一次成功并实测功能/真实数据"，细化到文件级与命令级，下游可据此逐行实现，无需再做架构决策。

---

## 评审结论（Review Verdict · Subtask #1）

**结论：有条件通过（Approved with Conditions）。**

**总体评价**：方案的**核心技术路线经源码核实成立且规模合理**——「隔离副本 + 扁平 `node_modules`（无符号链接）→ `copyTracedFiles` 不再 `fs.symlink` → 根除 EPERM、无需 Windows 提权」的因果链，建立在已验证的事实之上（`apps/web` 确为无 workspace 内部依赖的叶子包；`output` 受 `NEXT_OUTPUT_STANDALONE` 控制；`electron-builder` 的 `extraResources` 与 `paths.js` 的清单+递归定位运行期契约不受影响）。改动面严格限定在 `build:web` 一步与 `desktop/` 内的两个文档/忽略文件，**零侵入承诺可信**。**无 P0**；评审发现的 **2 个 P1（N1、N2）已在本版 v2 正文逐条就地修复**，无遗留 P0/P1。

**修复确认**：
- **N1（P1，可行性）**：`outputFileTracingRoot` 改为注入 `experimental.outputFileTracingRoot`（已用 `next@14.2.15` 的 `config-schema.js`/`build/index.js` 实证锁定命名空间）——见 §3 D3·D4、§4.2 步骤 3、§4.3、§6.3、R2。
- **N2（P1，一致性/完整性）**：去掉 `--no-package-lock`、过滤器剔除复制来的 `pnpm-lock.yaml`/`pnpm-workspace.yaml` 等 workspace 标记，使 R2 兜底有据、R9 可复现——见 §4.2 步骤 2·4、R2、R4、R9。

**放行条件（Subtask #2 实施/验收时必须满足，否则视为未达本轮目标）**：
1. **C1（硬指标）**：在**非管理员、未开 Windows 开发者模式**的本机环境，`cd desktop && npm run build:web` **rc=0 且无任何 `EPERM`/`symlink` 报错**（§8.1）。这是本轮唯一的「必过且必须实测」项。
2. **C2**：注入后的副本 `next.config.mjs` 经 `node --check` 通过，且 `next build` **无 "Invalid next.config options" 警告**（验证 N1 修复确已用对命名空间）；standalone 产物结构由 `findServerJs`+`web-server.json` 正确装配（扁平或嵌套均可）。
3. **C3**：`npm run smoke` / `npm run dist` 端到端成功产出 `win-unpacked` 与 NSIS/portable（§8.2）。
4. **C4（承接迭代 1 v2 遗留 C5）**：启动打包产物后 **`/api/py/*` 同源代理连通**（非 502/ECONNREFUSED）、WS 连通、OKX 可达时取**真实数据**、不可达时为诚实错误态、**零假数据**（§8.3，放行条件①不回退）。
5. **C5**：`npm run dev` 行为零回退、`apps/web`/`services/quant` 的 `git diff` 为空（§8.4）；构建产物（`dist/`、`resources/web|backend/`、`.web-build/`）不入库。
6. **C6（建议项，不阻断）**：按 N3 在本机已有 pnpm store 时优先 hoisted 路径以提升离线/弱网可靠性；按 N6 默认跳过对旧 spec 的越权回填。

若 C1–C5 在沙箱/CI 因二进制下载等**网络**限制无法全部实测，须按 §8 R8 口径将实测边界与结论如实登记到 §11，并保持纯增量、零侵入——但 **C1（`build:web` 无 EPERM）为本轮硬目标，必须实测通过**。

**签署**：设计在修复 N1/N2 后**可进入实现**；上述放行条件作为 Subtask #2 的验收门槛。— Anthropic 工程团队资深评审，2026-06-13。
