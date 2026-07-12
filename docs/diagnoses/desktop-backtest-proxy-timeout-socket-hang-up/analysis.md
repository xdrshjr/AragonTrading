# 桌面端「回测失败」——实为 Next 前端代理 30s 超时把连接重置（socket hang up / ECONNRESET）

> 节点说明：本文件由**评审节点（Subtask #1）**生成。上游定位节点（Subtask #0）被标记完成，但
> **未在 `docs/diagnoses/` 落下任何 `analysis.md`**（最近一次 analysis.md 为 2026-07-09，与本次无关；
> `scratch/`、`.bug-diagnosis/` 亦无当日产物）。为不阻断下游 Subtask #2，评审节点**基于用户提供的运行态
> 日志证据 + 逐一打开源码核对**，重建了完整证据链，并在文末追加 `## 评审结论`。全过程遵守节点约束：
> **只写本 `analysis.md`，不写修复代码，不 `git commit`。**

---

## Problem description（问题描述）

用户报告：**桌面端（打包态 aragon-desktop）回测失败**，要求定位并修复，确保回测稳健可靠、不失败。

关键澄清（与初判不同）：**这次不是回测逻辑失败——后端回测实际成功了；用户看到的“失败”发生在前端
代理层，连接被重置。**

运行态证据（最近 1 小时窗口 `2026-07-12 09:41:44 ~ 10:41:44`）：

- 后端仅一条策略 15 的回测 HTTP 记录，且**成功**：
  `%APPDATA%/aragon-desktop/logs/backend.log:431684`
  ```
  POST /api/strategies/15/backtest HTTP/1.1" 200 OK
  ```
- 回测结果随后可被读到（后端仍健康）：
  `backend.log:431688` —— `GET /api/runs/1421`、`.../trades`、`.../equity` 均 `200 OK`。
- 数据库里最近生成的 3 个回测**全部 `done`**（后端确实跑完并落库）：
  - run 1421，`30m`，done，284 trades
  - run 1422，`5m`，done，380 trades
  - run 1423，`1m`，done，932 trades
- 真正像“失败”的日志在**前端**：
  `%APPDATA%/aragon-desktop/logs/web.log:1210` / `web.log:1222`
  ```
  Failed to proxy http://127.0.0.1:18760/api/strategies/15/backtest Error: socket hang up
      code: 'ECONNRESET'
  ```
- 最近 1 小时后端**唯一** ERROR 是 `backend.log:432378` 的 WebSocket `ConnectionResetError [WinError 10054]`，
  发生在**行情 WS 连接关闭**时，与回测计算无关（见下文“为什么该 WS 错误是干扰项”）。

一句话结论：**Next 前端 standalone 代理请求后端时，在 30 秒硬超时处主动重置了上游连接，导致界面认为
回测失败；但后端回测（在线程池里）继续跑完并成功落库。**

---

## Reproduction steps（复现步骤）

1. 打开桌面端 Aragon Trading（打包态，`prod`）。前端为 Next standalone `server.js`
   （`desktop/src/main.js:188-197`、`desktop/src/lib/paths.js:63-72`），后端为 `aragon-quant.exe --port 18760`
   （`desktop/src/main.js:164-172`、`desktop/scripts/backend_entry.py`）。
2. 选择一个策略（如 id=15）、标的与周期，并把**回测区间拉长**——尤其**低周期（`1m`/`5m`）叠加长日历跨度**，
   使 `(endTs-startTs)/bar` 折算根数偏大（逼近取数上限 `backtest_max_bars=20000`，`services/quant/app/config.py:183`）。
3. 点击“运行回测”。前端经 `api.backtest(...)`（`apps/web/lib/api.ts:139-159`）
   `POST /api/py/strategies/15/backtest`，由 Next rewrite 透传到后端 `/api/strategies/15/backtest`
   （`apps/web/next.config.mjs:15-22`）。
4. 观察：
   - **短区间**（后端 < 30s 返回）：正常显示回测结果。
   - **长/低周期区间**（后端耗时 > 30s）：**约 30 秒后**前端报“回测失败”，`web.log` 出现
     `Failed to proxy ... socket hang up / ECONNRESET`；与此同时后端把该回测**跑完并落库 `done`**、
     `backend.log` 记 `POST .../backtest 200 OK`。

> 触发本质与“用户是否关闭程序”无关：只要**单次回测的墙钟时长 > 30 秒**即触发。低周期 / 长区间只是让
> 取数（分页 K 线）+ 计算更容易越过 30 秒门槛。

---

## Root cause analysis（根因分析）

### 主根因：Next standalone 代理对每个 `/api/py/*` 请求**硬编码 30 秒 `proxyTimeout`**，长回测被其在传输层重置

调用链（浏览器 → Next standalone server → http-proxy → uvicorn/FastAPI → OKX）全程**只占用一条 HTTP 连接、
同步等待返回**，而这条连接被 Next 代理的 30 秒超时**从上游侧掐断**：

1. **前端 `fetch` 无超时、无 AbortController**（`apps/web/lib/api.ts:62-97`，仅 `cache:"no-store"`）。
   故浏览器不会主动超时——排除“前端主动放弃”。
2. **回测是一条同步长请求**：`strategies.py:100-116` 的 `backtest` 路由直接
   `return await services.run_single_backtest(...)`；`run_single_backtest`
   （`services/quant/app/agent/services.py:247-295`）先 `load_range_candles`（分页拉全区间 K 线，
   `agent/services.py:82-111`）再 `_execute_and_persist`（执行并落库），**无 BackgroundTasks、无 jobId、无轮询**，
   请求返回前一直占着这条连接。
3. **Next standalone 代理对该请求施加 30 秒硬超时**——这是本 bug 的机制核心：
   `next@14.2.15/.../server/lib/router-utils/proxy-request.js:24-35`
   ```js
   const proxy = new HttpProxy({
     target, changeOrigin: true, ignorePath: true, ws: true,
     // we limit proxy requests to 30s by default, in development ...
     proxyTimeout: proxyTimeout === null ? undefined : proxyTimeout || 30000,
     ...
   });
   ```
   该 `proxyTimeout` 由 `router-server.js:269` 传入 `config.experimental.proxyTimeout`；本仓库
   `apps/web/next.config.mjs` **未设 `experimental.proxyTimeout`**（`config-shared.js:112` 默认 `undefined`），
   于是 `undefined || 30000` = **30000ms**。`http-proxy` 的 `proxyTimeout` 作用于**出站（proxy→后端）套接字**，
   到点即**销毁该 socket**并抛 `error`。
4. **超时触发的正是观测到的日志**：`proxy-request.js:71-72` 的错误处理
   ```js
   proxy.on("error", (err) => { console.error(`Failed to proxy ${target}`, err); ... })
   ```
   打印出 `Failed to proxy http://127.0.0.1:18760/api/strategies/15/backtest Error: socket hang up
   code: 'ECONNRESET'` —— 与 `web.log:1210 / 1222` **逐字一致**。
5. **后端不受代理放弃的影响，继续跑完并落库**：回测计算不阻塞事件循环（`run_single_backtest` 全程 `async`，
   CPU 密集段经 `run_in_threadpool` 下放线程池，`agent/services.py` 多处 `run_in_threadpool` 佐证），故后端
   **仍能应答后续请求**——与日志中随后多条 `200`（`/api/runs/1421`、trades、equity）一致。`_execute_and_persist`
   在组织响应体**之前**就把 run 提交为 `done`，因此即便 30s 时上游 socket 已被代理销毁、200 响应发不回浏览器，
   **DB 里的 run 1421/1422/1423 仍是 `done`**（284/380/932 trades）。这正是“后端成功、前端失败”的成因。

**因果闭环**：`回测墙钟 > 30s` → Next 代理 `proxyTimeout(30000)` 到点销毁上游 socket → `http-proxy` 抛
`ECONNRESET`「socket hang up」→ `Failed to proxy` 写入 `web.log`、UI 记为回测失败 → 但后端线程池里的回测
照常算完、落库 `done`、（对已断开的连接）记 `200`。

### 为什么是 ECONNRESET（传输层）而不是 4xx/5xx（应用层）

- 若是**取数失败**（OKX 不可达 / 业务码非 0），后端会返回结构化 **502**（`okx_error_handler`），不是 socket 重置。
- 若是**区间内无 K 线**，`load_range_candles` 抛 **400 `EMPTY_RANGE`**（`agent/services.py:108-110`）——同样是
  结构化错误体。
- 而观测到的是**没有任何应用层错误体、纯连接被重置**。能产生“连接被重置且无应用层错误体”的唯一情形，就是
  请求在返回前被**传输层切断**——此处即 Next 代理 30s `proxyTimeout` 到点销毁 socket。与“短区间 200、
  长区间 socket hang up”严格自洽。

### 为什么“后端仍记 200 + 随后多条 200”不矛盾

- 代理在 30s 销毁的是**出站连接**；后端线程池里的回测任务并未被取消，算完后照常提交 run 并生成响应，uvicorn
  仍按其访问日志约定记 `200`（响应写向已断开的 socket 会静默失败，但 run 已先落库）。
- 浏览器虽没拿到回测响应体（内含 `runId`），但前端在失败后**刷新 runs 列表 / 用户查看历史**即可读到已落库的
  run 1421（`GET /api/runs/1421` 等**都是短请求、< 30s，能正常经代理返回 200**）。这也旁证：**问题只出在
  “单条超过 30s 的长请求”这一类，短请求不受影响。**

### 为什么那条 WebSocket `ConnectionResetError [WinError 10054]` 是干扰项

- 行情 / paper WS **不走 Next `/api/py/*` 代理**：`apps/web/lib/ws.ts:6` 里
  `WS_BASE = NEXT_PUBLIC_PY_WS_URL || "ws://localhost:18760"`，浏览器**直连后端 WS**
  `ws://localhost:18760/api/ws/*`。故该 WS 的 10054 是行情连接关闭时后端侧的常规重置，
  **与回测 HTTP 代理路径无因果**，也说明**修改 HTTP 代理超时不会影响 WS**（见回归审计）。

### 对“回测逻辑失败”初判的更正

- DB 中 3 条 run 均 `done` 且带成交数（284/380/932），后端 `POST .../backtest 200 OK`——**这些都是后端回测
  成功的直接证据**。若是回测逻辑本身失败，应见 4xx/5xx 结构化错误或 run 落 `failed`，而非 `done`+200。
- 因此根因**不在** `run_single_backtest` / 回测引擎 / 策略计算，而在**前端代理的 30s 传输层超时**。

### 与既有同类诊断的同构性（佐证）

`web.log` 出现 `Failed to proxy ... socket hang up / ECONNRESET` 的签名，此前已在
`docs/diagnoses/desktop-paper-batch-start-socket-reset`（`/api/paper/batch/start`）与
`docs/diagnoses/factor-lab-train-long-range-socket-hang-up`（`/api/factor-sets/train`）出现。它们同属
**“经 Next `/api/py/*` 代理的同步长请求被传输层重置”**这一类。本报告首次把该类的**具体机制**精确定位到
**Next 14.2.15 代理的 30s `proxyTimeout`**（`proxy-request.js:31`）——这是三者共同的传输层成因。
（注：factor-lab 那轮的修复是给**训练**加“工作量守卫 413 fail-fast”，配置项 `factor_train_max_bars` 已落地，
见 `config.py:187`；那属于**训练侧限流**，并未触及代理超时，故回测这条长请求仍暴露在 30s 门槛下。）

---

## Impact surface（影响面）

- **直接受影响**：桌面端“运行回测”（`/api/strategies/{id}/backtest`）在**单次墙钟 > 30s**时必现“假失败”。
  低周期（`1m`/`5m`）+ 长区间最易触发（取数根数逼近 `backtest_max_bars=20000`）。
- **同机制波及的整条 `/api/py/*` 长请求家族**（皆走同一 Next 30s 代理超时）：
  - `/api/strategies/{id}/multi-backtest`（多样本回测，`api.ts:221`）
  - `/api/strategies/{id}/walk-forward`（滑动窗口）
  - `/api/strategies/compare`（组合对比，见 `multi-strategy-combo-compare-backtest-timeout`）
  - `/api/factor-sets/train`、`/api/paper/batch/start` 等
- **用户面**：桌面端与浏览器端一致（同走 `/api/py/*` 代理）。表现为底层 `socket hang up`，**对用户不可读、
  不可诉**（无产品级错误码），且**数据其实已落库**——用户以为失败、实际结果已生成，体验割裂。
- **不受影响**：行情 / paper WebSocket（`ws.ts:6` 直连后端，不经该代理）。

---

## Candidate fixes（候选修复对比）

| 方案 | 概述 | 侵入性 | 风险 | 是否命中根因 |
|---|---|---:|---:|---|
| **A** | **抬高 Next 代理 `proxyTimeout`**：在 `apps/web/next.config.mjs` 设 `experimental.proxyTimeout` 为一个足够覆盖有界回测最坏墙钟的**正数**（如 `300000` = 5 分钟）。重建 `build:web` 使其烤进 `required-server-files.json`。 | 极低（1 行配置 + 重建） | 低 | ✅ 直接消除 30s 重置机制 |
| B | **前端对账兜底**：回测 `fetch` 命中传输层错误（无 HTTP status 的 ECONNRESET / “Failed to fetch”）时，回查该策略最新 run，若出现匹配参数的新 `done` run 则按成功呈现。 | 中 | 中（启发式匹配、需去歧义） | ⚠️ 只“遮蔽失败表象”，不阻止重置 |
| C | **改为后台任务 + 轮询**：`backtest` 立即返回 `jobId`，后台执行、前端轮询结果，彻底摆脱“单请求长占连接”。 | 高 | 中-高 | ✅ 终局架构，但改契约 |
| D | **后端 uvicorn keep-alive**：给 `backend_entry.py` 的 `uvicorn.run` 传大 `timeout_keep_alive`。 | 低 | 低 | ❌ **非本因**：keep-alive 只治**空闲连接复用**竞态，不作用于**在途长请求**；本 bug 是代理 30s `proxyTimeout` 掐断在途请求 |

**候选方案的边界/回归要点：**

- A 的取值**必须是正数**：`experimental.proxyTimeout` 的 schema 为 `z.number().gte(0).optional()`
  （`config-schema.js:279`），**`null` 不合法**（无法用 null 关闭）；且 `0` 是**陷阱**——`proxy-request.js:31`
  的 `proxyTimeout || 30000` 会把 `0` 当假值**退回 30s**。故只能给一个**够大的正数**。
- A 的取值**须 ≥ 有界回测的最坏墙钟**：回测取数被 `backtest_max_bars=20000`（`config.py:183`）夹死
  （`agent/services.py:99-103`），最坏 ≈ 200+ 次串行 OKX 分页（限频 0.12s 地板 ≈ 24s）+ RTT + CPU，量级在
  **1~3 分钟**且**必然终止**。取 `300000`（5 分钟）可从容覆盖，且与 Node HTTP server 默认
  `requestTimeout≈300s` 对齐，避免“入站 5 分钟 / 出站更长”的不一致（若未来允许回测超过 5 分钟，需同步抬高
  standalone 入站 `requestTimeout`，本轮有界回测无需）。
- A **不影响 WebSocket**：WS 直连后端、不经该代理（`ws.ts:6`），改 HTTP `proxyTimeout` 与 WS 无关。
- A 的唯一“代价”：真正卡死的后端请求会把代理连接多占到 5 分钟（现为 30s）。对单用户桌面端可接受，且**回测有界
  必然终止**；相较“对成功回测误报失败”，这是严格改善。
- B 若采用，须保证对账**幂等/去歧义**（用 `strategyId+instId+bar+startTs+endTs+createdAt>请求发起时刻` 匹配，
  避免并发同参回测串号）。

---

## Recommended fix（推荐修复）

**首选 A（唯一最小修复）：在 `apps/web/next.config.mjs` 设置 `experimental.proxyTimeout` 为一个足够大的正数
（建议 `300000` 毫秒），并重建 `build:web` 使其烤进 standalone 的 `required-server-files.json`。**

理由：

1. **精确命中根因**：本 bug 的机制被逐字定位到 Next 14.2.15 代理的 30s `proxyTimeout`
   （`proxy-request.js:31` ← `router-server.js:269` ← `config.experimental.proxyTimeout`），A 正是把这个
   唯一门槛抬到覆盖有界回测最坏墙钟之上，**从机制上消除重置**。
2. **最小 blast radius**：1 行配置、零 API/契约改动、零前端逻辑、零后端改动、无新依赖；不会让任何“当前成功”的
   请求变失败（只是让代理多等一会儿）。
3. **一并修好整类**：同走 `/api/py/*` 的 multi-backtest / walk-forward / compare / train / paper-batch-start
   等长请求都受同一 30s 门槛所害，A 从传输层一次性抬高门槛。
4. **B/C/D 不入最小修复**：B 是“遮蔽失败”的启发式兜底（可作后续防御纵深）；C 是长请求的正确终局架构但改契约
   与 UI 状态机（后续演进）；D 针对的是**空闲连接复用竞态**、**非本因**，单独做无效。

> 落地点（供 Subtask #2 参考，本节点不写代码）：`apps/web/next.config.mjs` 的 `nextConfig` 增
> `experimental: { proxyTimeout: 300000 }`；因 standalone 在 `next build` 期把 config 烤进
> `required-server-files.json`（`desktop/src/lib/config.js:92` 已述），**必须重跑 `build:web` 并重打包**
> 才在桌面端生效。可选：在 `verify-dist` 冒烟里加一条“经 Next 代理跑一次 > 30s 的长回测应成功返回”的守门。

---

## 评审结论（Review Verdict）

> 本节为评审节点（Subtask #1）职责所在：对根因证据逐条核证、对候选修复审计边界/并发/回归，并锁定**单一**推荐修复。
> 评审方式：对每个 `file:line` 落点逐一打开核对（含 `node_modules` 内 Next 代理实现与 config schema 源码），非仅信其文字。

### 1. 根因证据复核：通过（evidence chain is airtight）

逐点开源核对，结论成立且可复核：

- **代理 30s 硬超时（主机制）——确认**：`next@14.2.15/.../router-utils/proxy-request.js:24-35`
  `proxyTimeout: proxyTimeout === null ? undefined : proxyTimeout || 30000`；来源
  `router-server.js:269` = `config.experimental.proxyTimeout`；本仓库未设该项
  （`config-shared.js:112` 默认 `undefined`）→ 实际 **30000ms**。错误打印
  `proxy-request.js:71-72` `console.error(\`Failed to proxy ${target}\`, err)` 与 `web.log` 逐字一致。
- **回测是无后台化的同步长请求——确认**：`strategies.py:100-116` 直接 `await run_single_backtest`；
  `agent/services.py:247-295` 取数（`load_range_candles`，`:82-111`）+ 执行落库，无 jobId/轮询。
- **取数有界、最坏墙钟落在 1~3 分钟——确认**：`load_range_candles` `target=min(expected, backtest_max_bars)`
  （`agent/services.py:99-103`），`backtest_max_bars=20000`（`config.py:183`）。故**有界必然终止**，用一个大而
  有限的 `proxyTimeout` 即可覆盖——这是 A 选“正数 300000”而非幻想“关闭超时”的量化依据。
- **ECONNRESET 属传输层而非应用层——确认**：应用层失败会走 502（OKX 错误）/ 400 `EMPTY_RANGE`
  （`agent/services.py:108-110`）等**结构化错误体**；观测到的是纯连接重置、无错误体，唯一自洽解释即代理超时切断。
- **“后端 200 + 落库 done” 与代理失败并存不矛盾——确认**：CPU 段经线程池不阻塞事件循环（故随后多条 200），
  run 在组织响应体前已提交 `done`，即便 200 发不回浏览器，DB 仍 `done`。
- **WS 10054 是干扰项——确认**：`ws.ts:6` WS 直连后端、不经 `/api/py/*` 代理，与回测代理路径无因果。

**结论：根因证据链成立、可复核，无需推翻。** 主根因＝**Next 14.2.15 standalone 代理对 `/api/py/*` 施加的
30 秒 `proxyTimeout` 把“单次墙钟 > 30s 的回测”在传输层重置**，令 UI 误报失败；后端回测实际成功并落库。

### 2. 候选方案复审与关键更正

- **A（抬高 `proxyTimeout`）——采纳为唯一最小修复。** 边界/并发/回归复核通过：
  - **取值约束（务必转交 Subtask #2，否则易做成“假修复”）**：`experimental.proxyTimeout` schema 为
    `z.number().gte(0).optional()`（`config-schema.js:279`）——**不接受 `null`**（“关闭超时”这条路在 14.2.15
    走不通）；**`0` 会被 `proxyTimeout || 30000` 退回 30s**（`proxy-request.js:31`）。故**必须给一个大正数**，
    建议 `300000`（5 分钟，覆盖有界回测最坏墙钟并对齐 Node 入站 `requestTimeout≈300s`）。
  - **生效前提**：standalone 在 `next build` 期把 config 烤进 `required-server-files.json`
    （`desktop/src/lib/config.js:92`），**必须重跑 `build:web` 并重打包**才在桌面端生效（纯改源码不重建无效）。
  - **无回归**：不影响 WS（`ws.ts:6` 直连）；不改任何应用契约；唯一代价是卡死请求多占代理连接至 5 分钟——
    对单用户桌面端可接受，且回测有界必终止，严格优于“对成功回测误报失败”。
- **B（前端对账兜底）——降为可选防御纵深，不入最小修复。** 它遮蔽而非消除重置，且需启发式去歧义
  （`strategyId+instId+bar+startTs+endTs+createdAt` 匹配），复杂度与误配风险高于 A。
- **C（后台任务 + 轮询）——正确终局，暂不在最小修复实施。** 改 API 契约与 UI 状态机，待 A 收口后演进。
- **D（后端 uvicorn keep-alive）——否决为“非本因”。** `backend_entry.py:31` 确未设 `timeout_keep_alive`
  （默认 5s），但 keep-alive 只作用于**请求间的空闲连接复用**，**不会中断在途的长回测请求**；本 bug 是代理
  `proxyTimeout` 掐断**在途**请求，故 D 单独无效，不予采纳。

### 3. 最终推荐修复（single recommended fix）

**唯一推荐：方案 A —— 在 `apps/web/next.config.mjs` 的 `nextConfig` 增
`experimental: { proxyTimeout: 300000 }`（毫秒，正数；不可用 `null`、不可用 `0`），随后重跑 `build:web` +
重打包，使其烤进 standalone 的 `required-server-files.json` 后在桌面端生效。**

交付 Subtask #2 的硬约束：

1. **取值为正数**（建议 `300000`）：`null` 不合 schema、`0` 会退回 30s。取值须 ≥ 有界回测最坏墙钟（~1~3 分钟）。
2. **必须重建 `build:web` 并重打包**：config 在构建期烤进 `required-server-files.json`，仅改源码不重建不生效。
3. **范围最小**：不改 API 形状、不动前端逻辑、不改后端、不引依赖；B/C/D 一律不在本次最小修复内实施。
4. **（可选守门）**：在 `desktop/scripts/verify-dist.mjs` 加一条“经 Next 代理跑一次 > 30s 的长回测应 200 返回”的
   打包态冒烟，防此类回归再次逃逸（与 `desktop-paper-batch-start-socket-reset` 推荐的 verify-dist 守门同构）。

**一句话理由**：A 以 1 行配置把被逐字定位到的 30s 代理超时（`proxy-request.js:31`）抬到覆盖有界回测最坏墙钟之上，
**从机制上根除“成功回测被前端代理误报失败”**，blast radius 最小、无回归、且一并修好整条 `/api/py/*` 长请求家族；
B 是遮蔽、C 改契约、D 非本因，均不应混入最小修复。
