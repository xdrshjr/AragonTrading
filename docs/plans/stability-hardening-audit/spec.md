# 稳定性与体验加固 · 缺陷审计与修复方案（spec v2）

> 版本：**v2**（Subtask #1 评审已完成；P0 2 条、P1 7 条**已在正文改掉**，P2 6 条如实记录）
> 日期：2026-08-16
> 基线：`master` @ `ec3c9f9`
> 产出节点：Subtask #0（方案设计）→ Subtask #1（设计评审）。本文件**只**是方案，不含实现代码。
> 正文中带 **[v2]** 标记的段落即本轮评审改写处；未标记处与 v1 逐字相同。

---

## 评审记录（Subtask #1）

评审姿态与设计节点一致：**每一条意见都要能用一条命令或一处代码事实钉住**，钉不住的不写。全部结论
均在 `M:/takoAI/AragonTrading` 干净工作树上复核过（本机 Node `v22.18.0`、pytest-asyncio `0.25.2`、
Next `14.2.15`、Tailwind `3.4.13`）。

**先说结论：v1 的证据链是可信的。** 逐条复核了 8 条缺陷引用的**每一个行号**，全部命中：
`WeightBatchArena.test.tsx:62/68` 的宽类型形参与 `factorTypes.ts:8` 的 11 项 `TrainMethod` 联合、
`test_agent_ws.py:72-76 / 130-133 / 137` 的两处 20000 次自旋与不关停的 `_manager`、
`manager.py:183/191/239/268` 的宽限计时器与 `shutdown`、`main.py:63-72` 的四个只 cancel 不 await 的句柄、
`ai_auto_repositories.py:606/652` 的 `holdSeconds` 与 `_trip_meta`、`executor.py:851/863` 的双时钟写点、
`repositories.py:285-296` 的 meta 信封、`error.tsx` 有而 `global-error.tsx` 无、`fixed inset-0` 恰 2 处、
`prefers-reduced-motion` / `transitionend` / `animationend` 命中恰 **0**、`desktop/package.json` 无 `test`。
`tsc --noEmit` 本机复跑，**逐字复现**了 §2.1 那条 `TS2322`（`WeightBatchArena.test.tsx(68,97)`）。
故本评审**不推翻任何一条缺陷判定**，全部意见都落在「修法本身能不能成立」与「验收能不能兑现」上。

### P0（阻断：不改则本轮的核心目标——把门禁修绿——就地失效）

| # | 结论 | 证据 | 处置 |
|---|---|---|---|
| P0-1 | **D5b 打开的 desktop 门禁会在 CI 上直接红**。`"test": "node --test \"scripts/*.test.mjs\""` 里的 glob 是**引号包住**的，`sh` 不会展开，只能靠 `node --test` 自己的 glob 匹配——那是 Node 21+ 才有的能力；而 `.github/workflows/ci.yml` 的 desktop job 钉死 `node-version: "20"`。结果是把 CI 从「红在 Typecheck」换成「红在 Desktop」，本轮全部价值归零（R7 预见了这类风险，但归因错了对象）。 | `ci.yml:62-63` 为 `node-version: "20"`；本机 `node --version` = `v22.18.0`，`node --test "scripts/*.test.mjs"` → `# pass 17 / # fail 0` | **已改**：D5b 修法第 3 条同步把 desktop job 抬到 `node-version: "22"`，并在 §8.2 增加「按 CI 原样命令跑一遍」的硬验收 |
| P0-2 | **D1 修绿之后，CI 的 `Unit tests` 与 `Build` 是 6 周以来第一次真正执行，而 spec 没有任何一条验收覆盖它们**。`WeightBatchArena.test.tsx` 自 `17a2158`（2026-07-02）引入即带该类型错误，此后 frontend job 每次都停在第 3 步——`pnpm test` 与 `pnpm build` 在 CI 姿势下已连续 6 周零执行。把第一道闸修绿而不验证它后面那两道，等于把红点往后挪一格。 | `git log -1 --date=short -- apps/web/components/Factor/WeightBatchArena.test.tsx` → `17a2158 2026-07-02`；`ci.yml:46-51` 三步顺序 | **已改**：§8.2 新增 G6/G7 两条门禁命令（CI 原样姿势），§8.3 DoD 第 1 条把它们纳入必贴输出 |

### P1（正确性 / 可兑现性：修法本身不成立或验收无法达成，均已在正文改掉）

| # | 结论 | 证据 | 处置 |
|---|---|---|---|
| P1-3 | **D3 的口径规则与它声称对齐的前端并不同构，且 §七「两条腿都有 wallTs」是错的**。前端是**逐腿** `wallTs ?? ts`；v1 提的是「双腿全有才用墙钟，否则双腿全退 `ts`」。而 `resume.py:359-368` 合成的 `offline_exit` 平仓腿**从不带 `wallTs`**（它自带 `explain`，连 `append_live_trade` 的 meta 合成分支都进不去），其 `ts` 又是交易所回读的**真实墙钟秒**。这类往返在 v1 规则下恰好退回「墙钟 − bar 对齐」——正是它自己写下的「绝不各取一半」。 | `autoTradePnl.ts:90-91`；`resume.py:359-368`（`explain.meta` 无 `wallTs`）；`repositories.py:286` 的 `explain is None` 前置条件 | **已改**：D3 改为**逐腿 `wallTs ?? ts`**，并补上使其成立的不变量证明（见 D3 §根因下的「两条不变量」）；§七的事实断言一并订正 |
| P1-4 | **D4 的收敛无死线，且新用例要驱动真实 lifespan——那会在离线用例里发起真实网络**。`_okx_link_sweeper` 起步即 `await clock.sync(get_rest_client().get_server_time_ms)`（`main.py:266-267`），真实 HTTP；lifespan 还会拉起体检守护、跑 `init_db()` 的启动对账（`conftest.py:70-97` 明写这一步会跨用例污染）、并逐个 `shutdown()` 八个 manager。用一条「验证关停确定性」的用例引入一条**新的**顺序依赖，与 D2 自相矛盾。 | `main.py:63-85`、`main.py:259-276`；`conftest.py:70-78` | **已改**：D4 抽出 `_cancel_background(tasks, *, timeout)` 助手（cancel → 有界 `asyncio.wait` → 取回异常并 warning），用例只测该助手；§五表第 9 项由 `test_main_lifespan.py` 改为 `test_main_shutdown.py` |
| P1-5 | **D2 的夹具兑现不了它自己写的验收**。`AgentManager.shutdown()` 体内**一个 `await` 都没有**（`manager.py:268-275` 是纯同步的 cancel + clear），所以 `await manager.shutdown()` 返回时，被取消的 `_grace_countdown` 任务**一次都没有被调度过**；用例结束事件循环即关，`Task was destroyed but it is pending!` 照旧会打。这正是 D4 自己写下的那句「cancel() 只是投递取消」——D2 的夹具踩的是同一个坑。 | `manager.py:268-275`（无 await）；`manager.py:191-197` 的 `_grace_countdown` | **已改**：D2 修法第 4 条改为「shutdown 前先抓走 `_grace_timers` 句柄，shutdown 后 `await asyncio.gather(*timers, return_exceptions=True)`」 |
| P1-6 | **D6 的 `global-error.tsx` 会以「无任何样式」渲染——退化成它要防的那个形状**。它替换的是根 layout，而 `@tailwind` 三行与全部 CSS 变量都在 `globals.css` 里、由 `app/layout.tsx:2` 导入；根 layout 不渲染 ⇒ 样式表不下发 ⇒ 崩溃页是一段无样式黑字。 | `layout.tsx:2`（唯一的 `globals.css` 导入点）；`globals.css:1-3`；Next 14 App Router 的 `global-error` 契约 | **已改**：D6 修法第 3 条要求 `global-error.tsx` **自己** `import "./globals.css"`，并如实声明它只会以浅色主题出现（`.dark` 由 `store.ts:1003/1009` 在客户端挂到 documentElement，根级边界下不会执行） |
| P1-7 | **D5 的接线缺三道守卫**：(a) 退出期（`quitting`，`main.js:29`）渲染进程本来就会消失，此时自动重载 = 把正在关的窗口拉回来，`dialog.showMessageBox(win, …)` 还会对已销毁的 `win` 抛错；(b) `unresponsive` 弹模态对话框会在长任务渲染时形成弹窗风暴，且 Electron 有配对的 `responsive` 事件而 v1 没用；(c) 接线直接塞进 `createWindow()` 会把它从 60 行推到 ~85 行——**它已经超了** CLAUDE.md 的 50 行阈值。 | `main.js:29 / 219-278`（60 行）/ `488-489`；既有 `wireWindowShortcuts(win)` 就是同款抽法（`main.js:272/362`） | **已改**：D5 修法第 2 条改为新增 `wireRendererHealth(win)` 函数；三处事件全部前置 `quitting || win.isDestroyed()` 守卫；`unresponsive` 降为只记日志 + 配对记 `responsive` |
| P1-8 | **D5 的两个新接口是「有写点没读点」的死码**——本仓最高频的缺陷形状（spec 自己在 D3b 刚点名过）。`describeRendererGone().level` 在整段接线里没有任何读点；`createReloadBudget().reset()` 同样只在测试里被调用，而且它一旦被真的接上（例如在加载成功后清零）就会把「崩 → 重载 → 加载成功 → 清零 → 再崩」变成无限自愈循环，比白屏更糟。 | v1 §D5 修法 2 的接线代码；v1 §六 的函数签名 | **已改**：`level` 与 `reset()` 双双从契约里删除（§D5 与 §六 同步） |
| P1-9 | **D3b 的版式指令二选一未决，且两个选项都落不了地**。「新增一行两列网格 **或** 并入第二行」——而第二行已是满 4 格（`ProfitabilityCard.tsx:72-77`），单格另起一行在 `sm:grid-cols-4` 下是 3/4 空位。本卡自己的语言其实很明确：**中位类「带分母读数」一律走 lib 纯函数 + 一行文案**（`travelToCostText` / `riskDistributionText`），`ProfitabilityCard.tsx:78` 的注释甚至写着「这一行是本轮最该上屏的那个数」。 | `ProfitabilityCard.tsx:66-85`；`aiTradeProfitability.ts:163-193` | **已改**：D3b 改为新增纯函数 `holdDurationText(pooled)` 于 `lib/aiTradeProfitability.ts`，卡片渲染一行 `data-testid="ai-profitability-hold"`；§五表相应 24 → 26 个文件 |

### P2（一致性 / 表述精度：如实记录，正文按需微调，不构成阻断）

| # | 结论 | 处置 |
|---|---|---|
| P2-10 | D3 的**回退分支自己也是混合时钟**：`_pair_round_trips` 的 docstring 已成文「AI 自动交易的 `trades.ts` 由两套时钟写入」。docstring 不得声称「两腿都用 `ts`」是同一口径。 | 正文 D3 的注释改写为「best-effort 回退（口径可能混合，见 `_pair_round_trips` 的既有说明）」 |
| P2-11 | `AgentManager.shutdown()`（`manager.py:268-275`）与 D4 是**同一形状**的缺陷，且长在**产品代码**里。本轮不做是对的（它的 run 任务另有 `_drain` 收口），但必须写进「不做」清单，否则下游会顺手扩大。 | 已加入 §十 第 7 条 |
| P2-12 | §8.2 第 5 条「期望 ≥ 17 + 新增」在 CI 上**不成立**：本机 17 passed 含**未跟踪**的 `ensure-wincodesign.test.mjs`，CI 检出只有 2 个已跟踪文件。 | §8.2 改为分别写清本机与 CI 的期望（`git ls-files desktop/scripts` 已复核：仅 `config.test.mjs` / `config.okx-link.test.mjs` 两个测试文件被跟踪） |
| P2-13 | D6 的 `CrashFallback` 必须**逐字复刻** `error.tsx:33-36` 那个 `<pre>` 的**两个 JSX 子节点**结构，否则文本节点合并会让 `error.test.tsx:34` 的 `getByText("未知错误")` 失配——那是事故回归护栏。 | 已写进 D6 修法第 1 条的硬约束 |
| P2-14 | D8 迁移后可访问名会由 `aria-label="因子组合探索训练"` 变成标题文本；实测**无用例依赖旧名**（`WeightExploreModal.test.tsx` 全文无 dialog 查询），可以改，但新用例要显式钉住新名。另：`className="max-w-4xl"` 覆盖原语的 `max-w-2xl` 依赖 Tailwind 工具类出现顺序，`AiBacktestModal.tsx:66` 已是同一写法，属既有范式。 | 已写进 D8 与 §8.1 |
| P2-15 | `ai_auto_repositories.py` 现 714 行，加 D3 两个助手后 ≈ 745 行，逼近 CLAUDE.md 的 800 行阈值。本轮可接受，但下次再往该文件加读数前必须先拆。 | 已加入 §九 R10 |

### 四个维度的总评

- **可行性**：8 条缺陷的修法在现有技术栈上全部可落地，无新依赖。唯二真正的可行性缺口是 P0-1（Node 版本）与
  P1-4（用例驱动真实 lifespan 会摸网络），均已改掉。
- **完备性**：边界与错误路径覆盖得相当细（`wallTs` 缺席、坏 JSON、`ERR_ABORTED`、老数据回退都写到了）。
  漏的是**上线路径本身**——「把一道红门禁修绿」这件事的下游影响（P0-2）与「新开的门禁在 CI 上是不是绿」
  （P0-1）没有被当成需求的一部分。这两条已补进 §8.2 / §8.3。
- **一致性**：与 CLAUDE.md 的冲突只有一处（P1-7c 的 50 行阈值），已改。与仓库既有范式的偏离两处
  （P1-3 的前后端口径不同构、P1-9 的卡片版式），均已收敛到既有写法。
- **规模合宜**：范围拿捏得当——不重构超阈文件、不碰下单热路径、零迁移、零 wire 变更，这三条「不做」写得
  比「做」还清楚。唯一的轻度过度设计是 P1-8 的两个死接口，已删。24 → 26 个文件仍在一轮迭代的合理体量内。

---

## 一、概述

本轮的需求是「检查这个项目的 BUG 和缺陷并修复，确保稳定稳健以及顶级」。这类需求最容易走偏的两条路
是：**其一**，把「顶级」理解成再加一个大功能，于是在一个已经有 69,969 行后端与 58,396 行前端源码的系统
上继续摊大饼；**其二**，把「检查缺陷」做成一份没有证据的代码风格清单，读起来很像回事，落到代码上一条
也不成立。本方案两条都不走：**每一条缺陷都必须能用一条可复现命令把它逼出来**，命令与实测输出一并写进
本文档；能修的当轮修掉，修不动的写清楚为什么不修。

审计在本机对干净工作树跑了三条门禁：前端类型检查、前端单测、后端全量 pytest。结论是这套系统的**代码质量
基线相当高**——1,796 个前端用例全绿；后端几乎没有 `TODO/FIXME`，唯一的 `except: pass` 在一条合法的清理
路径上；对话框有一等的 `Modal` 原语（焦点陷阱 / Esc / portal / 滚动锁 / 焦点归还俱全）；store 里每一个会
增长的数组都带 `.slice(-MAX)` 上限。**但恰恰因为基线高，本轮找到的缺陷才更值得修**：其中两条正卡在「质量
门禁自己是坏的」这个位置上——CI 的前端类型检查步骤**每一次 push 都必红**（本地 `tsc --noEmit` 稳定报 1 个
错误），后端全量 pytest 存在一条**顺序 / 负载相关的偶发失败**。一个红着的门禁等于没有门禁：它既拦不住新
缺陷，也让人对真实的红失去敏感。

因此本轮的主线是：**先把门禁修绿，再修门禁本该拦住的那几类缺陷**。共 8 条，分三档——P0 两条（质量门禁失
效），P1 四条（记账口径失真、进程关停不确定、桌面端崩溃无兜底、根级渲染异常无兜底），P2 两条（系统偏好
未被尊重、对话框交互不一致）。**[v2]** 合计涉及 **26** 个文件（评审后 +2，见 §五），其中 9 个为新建；
**零数据库迁移、零 wire 契约破坏、不触碰任何下单热路径**。所有改动都能被自动化用例钉住，验收靠命令输出
而不是靠肉眼。

**[v2] 评审后新增的一条主线**：把门禁修绿之后**必须验证它后面那两步也是绿的**。frontend job 的
`Unit tests` 与 `Build` 因第 3 步恒红已连续 6 周零执行——只修第 3 步而不验证第 4、5 步，等于把红点
往后挪一格（详见 §评审记录 P0-2 与 §8.2 的 G6 / G7）。

---

## 二、审计方法与基线证据

审计只用三种手段：跑门禁、读热路径、按「已知缺陷形状」反查。所有命令均在 `M:/takoAI/AragonTrading`
干净工作树（`git status` 无已跟踪文件改动）上实测。

### 2.1 前端类型检查 —— **红**

```bash
cd apps/web && ./node_modules/.bin/tsc --noEmit -p tsconfig.json
```

实测输出（1 个错误，其余零告警）：

```
components/Factor/WeightBatchArena.test.tsx(68,97): error TS2322:
  Type '{ method: string; weights: ...; }' is not assignable to type 'TrainResultEvent | ...'.
    Types of property 'method' are incompatible.
      Type 'string' is not assignable to type 'TrainMethod'.
```

`.github/workflows/ci.yml` 的 frontend job 第 3 步正是 `pnpm exec tsc --noEmit`，故 **CI 在每一个分支的
每一次 push 上都失败**，且失败点在 `Typecheck`，后面的 `Unit tests` 与 `Build` 两步根本没机会跑。

### 2.2 前端单测 —— **绿**

```bash
cd apps/web && NODE_ENV=test ./node_modules/.bin/vitest run
# Test Files 162 passed (162) / Tests 1796 passed (1796) / Duration 74.03s
```

> **[v2]** 记录一条环境事实供下游复用：**本机必须带 `NODE_ENV=test`**，否则 React 会抛 act() 相关错误。
> 原因已查清（评审补充）：vitest **只在 `NODE_ENV` 未设置时**才把它置为 `test`，而本工作站的环境里
> `NODE_ENV` 预置为 `production`（`node -e "console.log(process.env.NODE_ENV)"` → `production`）。
> **CI 上没有这个前缀是对的**——ubuntu runner 的 `NODE_ENV` 为空，vitest 自己会置成 `test`。故
> `ci.yml` 的 `pnpm test` 一行**不要**去加 `NODE_ENV=test`：那会掩盖「本机与 CI 环境不同」这个事实，
> 而下一个在别的机器上跑测的人需要知道它。

### 2.3 后端全量 pytest —— **偶发红**

```bash
cd services/quant && PYTHONUTF8=1 ./.venv/Scripts/python.exe -u -m pytest -q
```

全量 3,636 条用例。实测在第 153 条上出现一个 `F`。逐段定位（收集顺序确定，未装 pytest-randomly）后，
最小复现命令与实测输出为：

```bash
cd services/quant && PYTHONUTF8=1 ./.venv/Scripts/python.exe -u -m pytest \
  tests/test_accounts_api.py tests/test_agent_config.py tests/test_agent_llm_anthropic.py \
  tests/test_agent_llm_openai.py tests/test_agent_llm_retry.py tests/test_agent_loop.py \
  tests/test_agent_manager.py tests/test_agent_manager_detach.py tests/test_agent_provider_api.py \
  tests/test_agent_provider_probe.py tests/test_agent_provider_resolve.py \
  tests/test_agent_providers_repo.py tests/test_agent_regime_tool.py tests/test_agent_repo.py \
  tests/test_agent_run_client_bind.py tests/test_agent_services_backtest.py \
  tests/test_agent_session_buffer.py tests/test_agent_signal_tool.py \
  tests/test_agent_tool_registry.py tests/test_agent_tools.py tests/test_agent_tools_api.py \
  tests/test_agent_usage_normalize.py tests/test_agent_ws.py -q
```

```
FAILED tests/test_agent_ws.py::test_ws_resume_after_grace_completion_acks_false_without_replay
>       assert any(e["type"] == "done" for e in ws_a.sent)
E       assert False
1 failed, 157 passed in 38.60s
Task was destroyed but it is pending!
task: <Task pending name='Task-1132' coro=<AgentManager._grace_countdown() running at
      services\quant\app\agent\manager.py:194> wait_for=<Future pending cb=[Task.task_wakeup()]>>
```

同一个文件**单独跑 6 条全绿**（`pytest tests/test_agent_ws.py -q` → `6 passed in 4.54s`）；换一个前置组合
时红的是**同文件的另一条**用例（`test_ws_emits_ready_history_and_full_run`）。这两点合起来说明：不是断言
写错，是**等待机制不可靠**（详见 D2）。

### 2.4 静态与结构审计

- 后端 `app/` 下 `TODO/FIXME/XXX/HACK` 命中 **0** 处（仅测试里一个用作字面量的 `sk-XXXXabcd`）。
- `except ...: pass` 全仓仅 **1** 处（`paper/manager.py:101`），位于「start 失败后 stop 兜底」的合法清理路径。
- 前端浮层：`fixed inset-0` 仅 2 处，其中 1 处是 `components/ui/index.tsx` 的 `Modal` 原语本身；另 1 处
  是 `WeightExploreModal.tsx:237` 自绘（详见 D8）。其余弹窗均已复用原语——基线很好，缺口是**单点**的。
- `apps/web/app/` 下有 `error.tsx`（段级边界）但**没有** `global-error.tsx`（根级边界）。
- `apps/web/app/globals.css` 有 `@keyframes shimmer` 无限循环动画、组件里 10 处 `animate-*` 类，
  全仓 **0** 处 `prefers-reduced-motion`。
- `desktop/scripts/*.test.mjs` 有 3 个 node 测试文件（实测 `node --test "scripts/*.test.mjs"` → 17 passed），
  但 `desktop/package.json` **没有 `test` 脚本**，CI 的 desktop job 也只跑 `npm run check`（`node --check`
  语法检查）——**这些回归测试从未被任何门禁执行过**。

---

## 三、缺陷清单

优先级定义：**P0 = 质量门禁失效**（会让后续所有缺陷失去拦截）；**P1 = 影响正确性 / 可用性 / 可恢复性**；
**P2 = 影响一致性与可达性**。

### D1 [P0] CI 前端类型门禁每次必红

- **证据**：§2.1。错误点 `apps/web/components/Factor/WeightBatchArena.test.tsx:62`：

  ```tsx
  const result = (method: string, icOut: number) => ({
    method, weights: { mom_roc: 1 }, normStats: { normWindow: 200, clip: 4 }, metrics: metrics(icOut),
    trainerVersion: null, note: "", provenance: { ... },
  });
  ```

  该对象在 `:68` 被喂进 `BatchStreamEvent` 的 `candidate → event: TrainResultEvent`，而
  `TrainResultEvent.method` 的类型是 `TrainMethod`（`lib/factorTypes.ts:8` 的 11 项字面量联合），
  `string` 不可赋值。
- **根因**：辅助函数形参写成了宽类型 `string`。调用点 `for (const [runId, method, ic] of [[11,"linear",0.1],
  [12,"xgboost",0.3]] as const)` 传进来的实参本就是字面量类型，**只是被形参擦成了 `string`**。
- **影响面**：CI frontend job 恒失败 ⇒ `Unit tests` / `Build` 两步永不执行 ⇒ 整条前端质量门禁失效。
- **修法**（两行）：
  1. `:7` 的类型导入加上 `TrainMethod`：
     `import type { TrainMethod, TrainMetrics } from "@/lib/factorTypes";`
  2. `:62` 形参收窄：`const result = (method: TrainMethod, icOut: number) => ({`
- **纪律**：**禁止**用 `as any` / `as TrainResultEvent` / `@ts-expect-error` 绕过——那等于把类型门禁在这
  一点上关掉，而本条缺陷的全部价值就在于门禁本身。
- **附带**（同属 D1，降低复发概率）：`apps/web/package.json` 的 `scripts` 增
  `"typecheck": "tsc --noEmit"`，让本地 `pnpm typecheck` 与 CI 跑的是同一件事。
- **[v2] 修绿之后的第二件事（评审 P0-2，不做即等于没修）**：该类型错误随
  `17a2158`（2026-07-02）一同进仓且此后再未被触碰，也就是说 frontend job 的 `Unit tests` 与 `Build`
  **已连续 6 周零执行**。把第 3 步修绿只是让第 4、5 步第一次有机会跑——它们是不是绿的，本 spec 之前
  一个字都没说。故实施节点**必须**在本机按 CI 的**原样命令**各跑一次并贴输出（见 §8.2 的 G6 / G7）：

  ```bash
  cd apps/web && pnpm test     # 注意：不带 NODE_ENV 前缀，与 ci.yml:49 逐字一致（理由见 §2.2）
  cd apps/web && pnpm build    # ci.yml:51；CI 不设 NEXT_OUTPUT_STANDALONE，故实为普通产物（步骤名是历史遗留）
  ```

  这两条若红，**属于本轮范围**——本轮的目标是「门禁绿」，不是「D1 那一行改完了」。真出现红且根因
  与本轮 8 条缺陷无关时，如实记进「实施过程发现的方案缺陷」并**单独**修，绝不用 `continue-on-error`
  或注释掉步骤来换一个绿。

### D2 [P0] 后端 Agent WS 用例按「循环次数」而非「墙钟」等待，整套顺序下偶发红

- **证据**：§2.3 的最小复现命令与输出。
- **根因**（两条，叠加）：

  1. **等待预算的单位错了**。`services/quant/tests/test_agent_ws.py:71-76`：

     ```python
     # 脚本耗尽：等到 run 结束（done/error）再断连，给后台任务时间推进事件。
     for _ in range(20000):
         if any(e.get("type") in ("done", "error") for e in self.sent):
             break
         await asyncio.sleep(0)
     raise WebSocketDisconnect()
     ```

     `asyncio.sleep(0)` 只让出**一次事件循环**、**不推进任何时间**。而它等的那个后台 run
     （`AgentManager.handle` → `manager.py:239` 的 `asyncio.create_task(sess.run(...))`）中途要经
     aiosqlite 的**线程池**落库——那是真实耗时。20000 次纯 CPU 让出的总预算只有几毫秒量级；机器/事件
     循环一被前置模块拖慢，预算就在 `done` 到达之前耗尽，于是 `receive_text` 提前抛
     `WebSocketDisconnect`，`assert any(... == "done")` 必然为假。**这解释了全部三个观测**：单文件跑绿
     （循环空闲、DB 热）、整套跑红、以及**红的是哪一条会变**（纯粹取决于当时的调度快慢）。
     `_FakeWSResume.receive_text`（`:130-133`）是同一段代码的第二份拷贝，同病。
  2. **计时器跨用例泄漏**。用例用 `_manager(...)`（`:137`）造 `AgentManager`，但**从不调用
     `manager.shutdown()`**。`AgentManager` 在 detach 时会起 `_grace_countdown` 任务
     （`manager.py:183`），`shutdown()`（`manager.py:268-275`）才会取消它们。于是每跑一条用例就往事件
     循环上留一个 `await asyncio.sleep(grace)` 的挂起任务——这正是输出末尾那句
     `Task was destroyed but it is pending!` 的来源，也让循环越跑越"忙"，把 (1) 的概率往上推。

- **影响面**：全量 pytest 是本仓最重的一道门禁（3,636 条）。它**偶发**红意味着两件坏事同时发生：真红被
  当成"又抖了一下"，假红消耗每一次发布前的排查时间。
- **修法**：**只改测试，不改产品代码**（产品侧行为正确，是测试的等待姿势错了）。
  1. 文件顶部 `import time`、`import pytest`。
  2. 新增模块级有界等待助手（放在 `_FakeWS` 定义之前）：

     ```python
     _RUN_END_TIMEOUT_S = 10.0

     async def _await_event(sent: list[dict], kinds: tuple[str, ...],
                            timeout: float = _RUN_END_TIMEOUT_S) -> None:
         """等到 `sent` 里出现任一 `kinds` 事件；**按墙钟死线**有界等待。

         绝不能用「循环 N 次 sleep(0)」计预算：run 要经 aiosqlite 线程池落库，那是真实耗时，
         而 sleep(0) 不推进时间——整套顺序跑时预算会在 done 到达前耗尽（见 spec D2）。
         超时即返回（不抛），由调用方的断言把真失败暴露成断言错误而不是超时噪声。
         """
         deadline = time.monotonic() + timeout
         while time.monotonic() < deadline:
             if any(e.get("type") in kinds for e in sent):
                 return
             await asyncio.sleep(0.005)
     ```

  3. `_FakeWS.receive_text` 的 `for ... range(20000)` 整段替换为
     `await _await_event(self.sent, ("done", "error"))`；
     `_FakeWSResume.receive_text` 的同款循环替换为 `await _await_event(self.sent, (self._wait_for,))`。
  4. **[v2]** 计时器泄漏收口（评审 P1-5 改写）：`_manager()` 把新建的实例登记进模块级列表，并加一个
     autouse 夹具在每条用例后关停。**关键**：`AgentManager.shutdown()` 体内**一个 `await` 都没有**
     （`manager.py:268-275` 是纯同步的 cancel + clear），所以 `await manager.shutdown()` 返回时被取消的
     `_grace_countdown` **一次都还没有被调度过**——只写到这一步，`Task was destroyed but it is
     pending!` 照旧会打，本条的验收（§8.1）根本兑现不了。故必须在 shutdown **之前**抓走计时器句柄、
     之后把它们真正 `await` 掉（本仓 `pyproject.toml` 已设 `asyncio_mode = "auto"`，pytest-asyncio
     0.25.2，async 夹具可直接用）：

     ```python
     _MANAGERS: list[AgentManager] = []

     def _manager(api_key, llm, **settings_kw):
         settings = Settings(agent_api_key=api_key, agent_provider="anthropic", **settings_kw)
         manager = AgentManager(settings, llm=llm, registry=ToolRegistry(),
                                session_factory=get_sessionmaker())
         _MANAGERS.append(manager)
         return manager

     @pytest.fixture(autouse=True)
     async def _shutdown_managers():
         """用例结束即取消并**收敛**宽限计时器：否则 `_grace_countdown` 任务跨用例泄漏（spec D2 根因 2）。

         `shutdown()` 只**投递**取消（其体内无 await，见 manager.py:268-275），故必须自己把句柄
         抓出来再 await 一次——这正是 D4 写下的同一条纪律。`shutdown()` 会 `clear()` 掉
         `_grace_timers`，所以句柄只能在调用**之前**取。
         """
         yield
         while _MANAGERS:
             manager = _MANAGERS.pop()
             timers = [t for t in manager._grace_timers.values() if not t.done()]
             await manager.shutdown()                                   # 投递取消
             if timers:
                 await asyncio.gather(*timers, return_exceptions=True)  # 真正收敛
     ```

     > 夹具用 `while _MANAGERS: pop()` 而不是 `for`：一条用例可能造多个 manager，且即便中途抛错也要
     > 保证列表被清空——否则泄漏会**跨用例累积**，把本条修的问题换个更隐蔽的样子留下来。

- **验收**：§2.3 的最小复现命令连跑 3 次全绿，且输出中**不再出现** `Task was destroyed but it is
  pending!`；随后全量 pytest 一次跑通。

### D3 [P1] AI 自动交易「往返持仓时长」按 bar 对齐的 `ts` 计算，被系统性量化为 0 或整根 bar

- **证据（代码事实链）**：
  1. `services/quant/app/live_auto/executor.py:849-864`：成交的 `ts` 被**主动吸附**到所属 K 线开盘秒
     （`trade["ts"] = self._align_bar_ts(wall)`），真实墙钟另存 `trade["wallTs"]`（AI 链路才带该键）。
  2. `services/quant/app/db/repositories.py:285-296`：`append_live_trade` 即便一笔成交**没有** explain，
     只要带 `wallTs` 就会合成 `{"meta": {"wallTs": ...}}` 信封落库 ⇒ **一笔往返的两条腿在 DB 里都有
     `explain_json.meta.wallTs`**。
  3. `services/quant/app/db/ai_auto_repositories.py:606`：
     `"holdSeconds": max(0, int(trip.exit.ts or 0) - int(trip.entry.ts or 0))` —— 用的是 bar 对齐的 `ts`。
  4. 消费点：`services/quant/app/ai_auto/profitability.py:105` 的
     `"medianHoldSeconds": _median([...t.get("holdSeconds")...])`，进「跨会话盈利体检」响应。
  5. **[v2] 一条 v1 漏掉的写点（评审 P1-3）**：`services/quant/app/ai_auto/resume.py:359-368` 的
     `_offline_exit_trade` 会合成一条 `reason="offline_exit"` 的平仓腿，它**自带 `explain`**
     （`kind="offline_exit"`），因此连 `append_live_trade` 那段「无 explain 才合成 meta 信封」的分支
     （`repositories.py:286` 的 `explain is None` 前置）都进不去 —— 这条腿**永远没有 `wallTs`**，而它的
     `ts` 取自交易所 `positions-history` 的 `uTime`（`okx/trading.py:665` 已归一到秒），是**真实墙钟**。
- **根因**：读侧沿用了**已被量化**的 `ts` 作时间差。1H 周期下，一笔 20 分钟的往返若两腿落在同一根 bar，
  差值为 **0**；跨一根则恒为 **3600**。`max(0, ...)` 把负差（先墙钟后 bar 的组合）压成 0，掩盖了症状。
- **[v2] 使修法成立的两条不变量**（评审补充；没有它们，「缺 `wallTs` 就退回 `ts`」只是个听天由命的兜底）：
  1. **有 `wallTs` ⟺ `ts` 被吸附**。`executor._make_trade` 里两件事由**同一个** `self.bar_seconds > 0`
     判据控制：`_align_bar_ts` 只在 `bar_seconds > 0` 时真的吸附（`executor.py:838-839`，否则逐字回退
     `wall`），而 `trade["wallTs"] = wall` 也只在 `bar_seconds > 0` 时写（`executor.py:863-864`）。
     于是**这条腿没有 `wallTs` ⇒ 它的 `ts` 本来就是墙钟**，回退不丢精度。
  2. 唯一的例外是上面第 5 条那种**非 executor 写入**的腿（`offline_exit`），而它的 `ts` 同样是真实墙钟。
  两条合起来即：**`wallTs ?? ts` 逐腿取值，在每一条现存写路径上都等于「这条腿的真实墙钟秒」**——这正是
  前端 `autoTradePnl.ts:90-91` 的写法。故后端必须与它**逐字同构**，而不是另立一条「双腿全有才用」的规则。
- **前端早已修过同一处**：`apps/web/lib/autoTradePnl.ts:87-91` 就是这条缺陷的正解与注释原文
  （「若沿用已被吸附到 K 线开盘秒的 `ts`，1H 周期下一笔 20 分钟的往返会被算成…」），全前端一律
  `wallTs ?? ts`（`components/Chart/TradeTooltip.tsx:37`、`Strategy/TradesTable.tsx:96` 等 6 处）。
  **后端这一处是唯一漏网的**，于是同一笔往返在前端与后端读出两个持仓时长。
- **影响面**：`medianHoldSeconds` 失真；且它是「交易周期模式」这一整套纪律的**事后验证读数**——量化成 0
  会让「持有得够不够久」这个问题永远得到一个假答案。
- **修法**（`services/quant/app/db/ai_auto_repositories.py`）：
  1. 把 `_trip_meta`（`:652`，当前**只**读平仓腿）旁边新增一个逐腿助手：

     ```python
     def _leg_wall_ts(trade: Trade) -> int | None:
         """某一腿的真实墙钟秒（`explain_json.meta.wallTs`）；缺键 / 坏 JSON / 非正数 ⇒ None。"""
         meta = _load_config(getattr(trade, "explain_json", None)).get("meta")
         if not isinstance(meta, dict):
             return None
         try:
             wall = int(meta.get("wallTs") or 0)
         except (TypeError, ValueError):
             return None
         return wall if wall > 0 else None
     ```

  2. **[v2]** 新增持仓时长纯函数（评审 P1-3 改写：**逐腿** `wallTs ?? ts`，与前端 `autoTradePnl.ts:90-91`
     逐字同构）：

     ```python
     def _trip_hold_seconds(trip: _Trip) -> int:
         """往返持仓秒数：**逐腿**取 `wallTs ?? ts`，与前端 `autoTradePnl` 同一条规则。

         `trades.ts` 被 `_align_bar_ts` 吸附到 K 线开盘秒，1H 周期下一笔 20 分钟的往返会被算成 0
         或整根 bar（spec D3）。逐腿回退是安全的、而不是听天由命：`executor._make_trade` 里「吸附
         `ts`」与「写 `wallTs`」由**同一个** `bar_seconds > 0` 判据控制，故缺 `wallTs` 的 executor
         腿其 `ts` 本就是墙钟；唯一的非 executor 写点 `resume._offline_exit_trade` 也只写墙钟秒
         （交易所 `uTime`，已归一到秒）。**绝不能写成「双腿全有才用墙钟」**——`offline_exit` 平仓腿
         永远没有 `wallTs`，那条规则会让这类往返退回「墙钟 − bar 对齐」，恰好是它想避免的混用，
         而且与前端读出两个不同的数（评审 P1-3）。

         负差压 0 沿用既有 `max(0, ...)`：真实的往返不会先平后开，出现负差即数据异常，报 0 比报一个
         负持仓时长诚实（口径与 v1 逐字不变）。
         """
         entry = _leg_wall_ts(trip.entry) or int(trip.entry.ts or 0)
         exit_ = _leg_wall_ts(trip.exit) or int(trip.exit.ts or 0)
         return max(0, exit_ - entry)
     ```

     > **老数据回退的行为承诺（评审 P2-10 订正）**：2026-07 墙钟上线前的往返两腿都无 `wallTs`，
     > 逐腿回退后**两腿都取 `ts`**，与修改前逐值相同——`_add_trip` 造一条两腿都无 `explain_json` 的
     > 往返即可钉住（§8.1 已列）。但**不要**把这句写成「回退分支是单一口径」：`_pair_round_trips`
     > 的既有 docstring 已成文「AI 自动交易的 `trades.ts` 由两套时钟写入」，回退分支本身是一个
     > **best-effort** 估计，只是它在**所有既有写路径上都不劣于**修改前的值。

  3. `_trip_row`（`:606`）改为 `"holdSeconds": _trip_hold_seconds(trip),`。
- **口径纪律**：**不改** `_load_trades_by_run` 的 SQL 排序（仍 `ts, id` 升序）、**不改**
  `_pair_round_trips` 的 `id` 配对键、**不动** `_aggregate_group.endedTs` 与 `_group_started_ts`。
  这三处的「首/末元素即最早/最晚」语义由 SQL 排序保证，动一个就要连带改三个（见该文件 `:80-90` 的既有
  说明）。本条只改**一个派生读数的取值来源**，不改任何顺序语义。

### D3b [P1] `medianHoldSeconds` 有写点、没有读点

- **证据**：`grep -rn "medianHoldSeconds" apps/web --include=*.tsx --include=*.ts | grep -v test` 只命中
  两处**类型声明**（`lib/aiTradeProfitability.ts:24`、`lib/aiTradeTypes.ts:629`），
  `components/Agent/aiTradeStats/ProfitabilityCard.tsx` 从未渲染它。后端算了、wire 传了、类型接了，
  **用户一眼也看不到**。
- **[v2] 修法**（评审 P1-9 定版；v1 的「两列网格 **或** 并入第二行」二选一未决，且两个选项都落不了地：
  两行指标网格各 4 格**都已满**（`:66-71` / `:72-77`），单格另起一行在 `sm:grid-cols-4` 下是 3/4 空位）。
  按本卡**自己的既有语言**落地——中位类「带分母读数」在本卡一律是 **lib 纯函数 + 一行文案**
  （`travelToCostText` / `riskDistributionText` 就是这么写的；`:78` 的注释甚至写着「这一行是本轮最该
  上屏的那个数」，可见文案行不是次要位置，而是本卡放结论的地方）：

  1. `apps/web/lib/aiTradeProfitability.ts` 新增纯函数（放在 `travelToCostText` 旁边，与它同段）：

     ```ts
     /** 中位持仓时长一行（读数与分母同屏）；无样本 ⇒ `formatHoldDuration` 自己给 `—`，绝不编一个 0。 */
     export function holdDurationText(pooled: AiTradePooled): string {
       return `中位持仓时长 ${formatHoldDuration(pooled.medianHoldSeconds)}（${pooled.roundTrips} 笔）`;
     }
     ```

     `formatHoldDuration` 从 `@/lib/aiTradeHorizon` 导入（`aiTradeHorizon.ts:71` 已导出：`null` / 非有限 /
     ≤0 ⇒ `"—"`，与本卡「缺数据就写破折号、绝不编」的既有姿态一致）。分母取 `roundTrips` 是**对的**：
     `profitability.py:105` 的 `_median` 吃的正是全部 `trips` 的 `holdSeconds`，不是子样本。
  2. `ProfitabilityCard.tsx` 在 `travelToCostText` 那一行之后插入一行（同款 `<p>` 版式）：

     ```tsx
     <p className="text-[10px] leading-relaxed text-[var(--muted)]" data-testid="ai-profitability-hold">
       {holdDurationText(pooled)}
     </p>
     ```
- **纪律**：本卡的文案受一条词表扫描门禁约束（`lib/aiTradeProfitability.test.ts:160-176` 的 G-10 段，
  扫描集为 `lib/aiTradeProfitability.ts` / `ProfitabilityCard.tsx` / `CalibrationCard.tsx` **三个**文件，
  禁用词 `已校准|经回测验证|预期年化|稳定盈利|保证`）。本条新增的两处文案落在其中**两个**被扫描的文件里，
  措辞只允许是「中位持仓时长」这类中性描述。每个读数必须与它的分母同屏——故文案里带 `（N 笔）`。

### D4 [P1] 应用关停只 `cancel()` 不 `await`，后台协程可能在事件循环关闭时仍挂起

- **证据**：`services/quant/app/main.py:63-72`：

  ```python
  sweeper = asyncio.create_task(_weight_train_sweeper())
  resumer = asyncio.create_task(_resume_discovery_runs())
  hydrator = asyncio.create_task(_hydrate_paused_ai_trade())
  link_sweeper = asyncio.create_task(_okx_link_sweeper())
  yield
  link_sweeper.cancel(); hydrator.cancel(); resumer.cancel(); sweeper.cancel()
  await get_health_guard().shutdown()
  ...
  ```

  四个句柄全部只 `cancel()`、**没有一个被 await**。`cancel()` 只是**投递**一次取消，协程要等到下一次被
  调度才会真正走完 `except CancelledError: return`。后面紧跟着的是 `dispose_engine()` /
  `aclose_all()`——即引擎与 HTTP 客户端可能在这些协程真正收尾之前就被拆掉。
- **影响面**：关停期出现 `Task was destroyed but it is pending!` 噪声；极端时序下
  `_hydrate_paused_ai_trade` 的重试段可能在 session maker 已被 dispose 之后再摸一次 DB。**桌面端每次退出
  都会走这条路**（`desktop/src/main.js:498` 的 quit-pause 钩子之后即 `pm.stop()`）。
- **[v2] 修法**（`services/quant/app/main.py`；评审 P1-4 改写：**收敛必须有死线**，且必须抽成可单测的
  助手——不是为了好看，是因为 v1 的用例打算驱动**真实 lifespan**，那会在离线用例里发起真实网络，见下）：

  ```python
  _BACKGROUND_SETTLE_TIMEOUT_S = 5.0

  async def _cancel_background(tasks: tuple[asyncio.Task, ...],
                               *, timeout: float = _BACKGROUND_SETTLE_TIMEOUT_S) -> None:
      """取消并**有界收敛**常驻后台协程（spec D4）。

      `cancel()` 只是**投递**一次取消：协程要等到下一次被调度才会走完 `except CancelledError: return`。
      不 await 就往下 `dispose_engine()` / `aclose_all()`，协程可能在半路被连根拔掉。

      死线是硬性的：关停路径上一个吞掉取消的协程**绝不能**反过来把整条关停链堵死（桌面端每次退出都
      走这条路）。超时不抛，如实 warning——「没停干净」是要被看见的事实，不是要被制造的异常。
      `asyncio.wait` 不会替你取回异常，故对已完成的任务显式取一次，既避免 "exception was never
      retrieved" 噪声，也让真异常留下日志。
      """
      for task in reversed(tasks):   # 取消顺序逐字保持既有：link_sweeper → hydrator → resumer → sweeper
          task.cancel()
      done, pending = await asyncio.wait(tasks, timeout=timeout)
      for task in done:
          if not task.cancelled() and task.exception() is not None:
              logger.warning("background task ended with %r", task.exception())
      if pending:
          logger.warning("%d background task(s) did not settle in %.1fs", len(pending), timeout)
  ```

  lifespan 内相应改为两行：

  ```python
  background = (sweeper, resumer, hydrator, link_sweeper)
  yield
  await _cancel_background(background)
  ```

  其余关停调用顺序**一行不动**（该顺序由多份既有 spec 约定：健康守护 → agent → discovery → paper →
  ai_trade → ai_backtest → live_auto → 批次泵 → 训练作业 → REST → 连接池 → engine）。
- **[v2] 用例只测 `_cancel_background`，绝不驱动真实 `lifespan(app)`**（评审 P1-4 的核心一条）。理由是
  三条可验证的事实，不是洁癖：
  1. `_okx_link_sweeper` **起步即发真实网络请求**——`clock.is_stale(...)` 在新时钟上恒为真，紧接着
     `await clock.sync(get_rest_client().get_server_time_ms)`（`main.py:265-267`）。一条离线单测里
     不该有出站 HTTP（`pyproject` 的 `-m 'not network'` 只排除**打了标记**的用例，拦不住这个）。
  2. lifespan 会 `init_db()` 并跑启动对账，而 `tests/conftest.py:70-78` 的既有夹具明写这一步会把上一个
     用例残留的 run 置 `paused` 从而**跨用例污染**——用一条「验证关停确定性」的用例引入一条新的顺序
     依赖，与本轮 D2 自相矛盾。
  3. 退出段还会逐个 `await` 八个进程级单例 manager 的 `shutdown()`（含 ai_trade 的含平仓路径）。
     这些都不是本条缺陷要验证的东西。
  用例形状（`services/quant/tests/test_main_shutdown.py`，两条）：**(a)** 造一条 `asyncio.sleep(30)` 的
  cancel-safe 任务，`await _cancel_background((task,))` 后断言 `task.done()`、且函数在毫秒级返回；
  **(b)** 造一条**吞掉** `CancelledError` 的任务，用 `timeout=0.05` 调用，断言函数**仍然返回**（关停不被
  堵死）且 `task.done()` 为假——用例末尾必须让该任务真正结束（置一个退出标志再 cancel 一次并 await），
  否则本用例自己就成了新的泄漏源。

### D5 [P1] 桌面端渲染进程崩溃 / 无响应 / 加载失败**完全没有兜底**

- **证据**：`grep -n "render-process-gone\|unresponsive\|did-fail-load" desktop/src/main.js` → **0 命中**。
  主进程只有 `uncaughtException`（`:572`）。`win.loadURL(cfg.WEB_URL)`（`:476`）失败时，窗口停在 Chromium
  的错误页；渲染进程 OOM 或崩溃时，窗口变白且**日志里一个字都没有**。
- **为什么这条值得单列**：本仓有一次成建制的白屏事故（`docs/diagnoses/desktop-white-screen-market-ws-link-frame/`），
  当时的教训是「外壳日志全绿 ≠ 没崩」。事故后补的是**渲染层**的 `app/error.tsx`；**外壳层的同类兜底至今
  是空的**——而渲染进程崩溃恰恰是 React 错误边界**接不住**的那一类（进程已经没了）。一个 7×24 挂着行情
  WS 与图表的桌面端，这条路径是迟早会走到的。
- **修法**（两部分，严格分离「纯逻辑」与「Electron 接线」）：
  1. **新建** `desktop/src/lib/renderer-health.js` —— 纯函数，**不得 `require('electron')`**
     （`paths.js` / `agent-env.js` 依赖 electron，故在纯 node 测试里不可用；`config.js` 是本仓已有的
     「可单测的 lib」范式）。导出三个函数：

     - **[v2]** `describeRendererGone(details)` → `{ reason, message, recoverable }`：把 Electron 的
       `details.reason`（`crashed` / `oom` / `killed` / `abnormal-exit` / `launch-failed` /
       `integrity-failure`）翻成一句中文人话与是否值得自动重载；`killed` 与用户主动退出不建议自动重载。
       （评审 P1-8：v1 的返回里还有一个 `level`，但整段接线**没有任何读点**——「有写点没读点」是本仓
       最高频的缺陷形状，spec 自己在 D3b 刚点名过。已从契约里删掉；日志级别由接线处按 `recoverable`
       现判，不必让纯函数替调用方决定。）
     - **[v2]** `createReloadBudget({ limit = 3, windowMs = 60_000 })` → `{ allow(nowMs) }`：滑动窗口
       重载配额。**没有配额就没有自愈**——崩溃-重载-再崩溃的死循环比白屏更糟。`nowMs` 由调用方注入
       （保持纯函数、可测）。（评审 P1-8：v1 还导出一个 `reset()`，同样只在测试里被调用；更糟的是它
       一旦被真接上——例如「加载成功即清零」——就会把「崩→重载→加载成功→清零→再崩」变成一个**无限**
       自愈循环。已删除：滑动窗口是唯一的机制，测试要新配额就新造一个。）
     - `describeLoadFailure(errorCode, errorDescription, validatedURL)` → `string | null`：返回 `null`
       表示**应当忽略**（`errorCode === -3` 即 `ERR_ABORTED`，正常导航打断会产生它，不是故障）。
  2. **[v2]** `desktop/src/main.js` **新增函数** `wireRendererHealth(win)`，由 `createWindow()` 在
     `wireWindowShortcuts(win)` 之后调用一行（评审 P1-7c：`createWindow()` 现为 `:219-278` 共 **60 行**，
     **已经超**了 CLAUDE.md 的 50 行阈值，再塞 ~25 行接线就是 85 行；而 `wireWindowShortcuts(win)`
     （`main.js:272/362`）本来就是本文件「窗口接线抽成同名函数」的既有范式，照抄即可）：

     ```js
     // 渲染进程健康接线（spec D5）。三个事件共用一份重载配额：它们都是"窗口自愈"这一件事。
     function wireRendererHealth(win) {
       const budget = createReloadBudget({});
       // 退出期渲染进程本来就会消失：此时自愈 = 把正在关的窗口拉回来，且对已销毁的 win 弹窗会抛。
       const isLive = () => !quitting && win && !win.isDestroyed();

       win.webContents.on('render-process-gone', (_e, details) => {
         const info = describeRendererGone(details);
         logMain(`renderer gone: ${info.reason} — ${info.message}`);
         if (!isLive()) return;
         if (info.recoverable && budget.allow(Date.now())) { win.reload(); return; }
         dialog.showMessageBox(win, { type: 'error', title: '界面进程已退出',
           message: info.message, detail: '已停止自动重载以免反复崩溃。后端与已落库数据不受影响。',
           buttons: ['重新载入', '打开日志', '退出'], defaultId: 0, cancelId: 2 })
           .then(({ response }) => {
             if (!isLive()) return;
             if (response === 0) win.reload();
             else if (response === 1) shell.openPath(paths.ensureLogsDir());
             else win.close();
           });
       });

       // 只记日志、不弹窗（评审 P1-7b）：长任务渲染期间 unresponsive 会反复触发，弹模态即弹窗风暴；
       // 配对记 responsive 才能在日志里读出"卡了多久"，这正是"外壳日志全绿 ≠ 没崩"要补的那一笔。
       win.on('unresponsive', () => logMain('renderer unresponsive（界面暂时无响应，未做任何处置）'));
       win.on('responsive', () => logMain('renderer responsive（界面已恢复响应）'));

       win.webContents.on('did-fail-load', (_e, code, desc, url, isMainFrame) => {
         if (!isMainFrame) return;
         const line = describeLoadFailure(code, desc, url);
         if (!line) return;                    // -3 ERR_ABORTED：正常导航打断，忽略
         logMain(`load failed: ${line}`);
         if (!isLive()) return;
         win.loadFile(path.join(__dirname, 'loading.html'));   // 回自带过渡页，而不是 Chromium 英文错误页
         if (budget.allow(Date.now())) {
           // 最常见的成因是前端进程刚死/正在重起，一次有界重试就能自己回来；配额用尽即停在过渡页，
           // 用户仍可 Ctrl+R（wireWindowShortcuts 已接）——绝不无限重试。
           setTimeout(() => { if (isLive()) win.loadURL(cfg.WEB_URL); }, 1500);
         }
       });
     }
     ```

     `dialog` 已在 main.js 顶部导入（`showAbout` 在用）；`shell` 同理（`aragon:open-logs` 在用）；
     `quitting`（`main.js:29`）、`cfg`（`:12`）、`paths`（`:13`）、`logMain`（`:45`）均在模块作用域。
- **诚实声明**：接线本身需要真实 Electron 运行时，**不做自动化测试**；自动化只覆盖 `renderer-health.js`
  的三个纯函数。接线的验收走人工步骤（§8.4）。

### D5b [P1] 桌面端已有的回归测试从未被任何门禁执行

- **证据**：`desktop/scripts/` 下有 `config.test.mjs`、`config.okx-link.test.mjs`
  （另有未跟踪的 `ensure-wincodesign.test.mjs`）。实测
  `cd desktop && node --test "scripts/*.test.mjs"` → **17 passed**。但 `desktop/package.json` 的 `scripts`
  里**没有 `test`**，`.github/workflows/ci.yml` 的 desktop job 只有 `npm run check`（= 一串 `node --check`
  语法检查）。**这两个文件守着「打包默认注入两个自动交易总闸」这种一旦回归就会静默改变生产行为的断言，
  却从来没跑过。**
- **修法**：
  1. `desktop/package.json` 增 `"test": "node --test \"scripts/*.test.mjs\""`
     （Node 22 下必须用 glob 形态；实测 `node --test scripts` 会被当成模块路径而 `MODULE_NOT_FOUND`）。
  2. 同文件 `check:*` 增 `"check:rendererhealth": "node --check src/lib/renderer-health.js"`
     （`check` 是 `run-s check:*`，新键自动纳入）。
  3. **[v2]**（评审 P0-1）`.github/workflows/ci.yml` 的 desktop job 做**两处**改动，缺一即 CI 变红：

     ```yaml
     - uses: actions/setup-node@v4
       with:
         # node --test 的 glob 匹配是 Node 21+ 的能力，而 package.json 里的 glob 是**带引号**的
         # （sh 不展开），Node 20 下会把它当成一个不存在的文件路径。本机实测 v22.18.0 可用。
         node-version: "22"
     ...
     # 这些用例只依赖 node 内置与 src/lib 下的纯模块，不需要 Electron 二进制（与 --ignore-scripts 兼容）。
     - name: Unit tests
       run: npm test
     ```

     > 为什么不用「去掉引号让 sh 自己展开」：那样 Windows 上（cmd / PowerShell 都不展开 glob）本地
     > `npm test` 就会失效，等于把跨平台差异塞进一条门禁里。抬 Node 版本是**一处**改动、两边都真。
     > 另：frontend / backend 两个 job 的版本**不动**（它们与本条无关，改动越小越好归因）。

### D6 [P1] 缺根级错误边界 `global-error.tsx`，根布局抛错仍是白屏

- **证据**：`apps/web/app/` 下有 `error.tsx`、**无** `global-error.tsx`。Next.js App Router 的契约是：
  `error.tsx` 接的是**本段 children** 的渲染异常；**根 layout / 根 template 自身抛出的异常只有
  `global-error.tsx` 能接**，否则落到 Next 内置兜底——即 `error.tsx` 注释里写的那句「整棵 React 树换成
  一行英文黑字，打包桌面端看上去就是整页白屏」。
- **影响面**：`app/layout.tsx` 渲染 `Providers`（`app/providers.tsx`，构造 QueryClient）。今天它足够简单，
  但**兜底的价值恰恰在于不预设明天**；且这一层一旦炸，用户拿到的是本项目已经付过一次代价的那个白屏。
- **修法**（顺带把两处兜底的视觉统一，这正是「顶级设计」的落点：崩溃页也是产品的一部分）：
  1. **新建** `apps/web/components/ui/CrashFallback.tsx`：把 `error.tsx:26-45` 的展示部分整体抽出，
     props 为 `{ title: string; description: string; error?: { message?: string; digest?: string };
     onRetry?: () => void; retryText?: string }`。**可见文案逐字保持不变**（`工作台渲染失败` /
     `未知错误` / `错误编号：` / 按钮 `重试`），否则 `app/error.test.tsx` 会红——那是一次真实事故的回归
     用例，只能被满足、不能被改。
     **[v2] 附加硬约束（评审 P2-13）**：那个 `<pre>` 里的**两个 JSX 子节点结构也要逐字复刻**——
     「`message` 或 `未知错误`」与「有 `digest` 才追加的那段换行 + `错误编号：`」必须仍是
     **两个并列子节点**（照抄 `error.tsx:33-36`），不得合并成一个模板字符串。合并会让 `<pre>` 的文本
     节点变成一个整体，`error.test.tsx:34` 的 `getByText("未知错误")` 精确匹配随即失配。
  2. `apps/web/app/error.tsx` 改为渲染 `<CrashFallback title="工作台渲染失败" ... onRetry={reset} />`，
     `useEffect` 里的 `console.error` 原样保留。
  3. **新建** `apps/web/app/global-error.tsx`（`"use client"`）：必须自带 `<html lang="zh-CN"><body>`
     外壳（Next 要求：它替换的是根 layout），内部渲染
     `<CrashFallback title="应用启动失败" description="..." onRetry={reset} retryText="重新载入" />`。
     文案要点：说明后端与已落库数据不受影响、给出错误编号、给出唯一出路。
     **[v2] 它必须自己 `import "./globals.css";`（评审 P1-6，不写就等于没修）**：`@tailwind` 三行与
     全部 CSS 变量都在 `globals.css`，而唯一的导入点是 `app/layout.tsx:2`——根级边界替换的正是这个
     layout，它不渲染 ⇒ 样式表不下发 ⇒ 崩溃页退化成一段无样式黑字，**恰好是这次事故里被判定为「白屏」
     的那个形状**。
     **[v2] 并如实声明一条降级**：主题类 `.dark` 由 `lib/store.ts:1003/1009` 在客户端挂到
     `documentElement`，根级边界下那段代码不会执行，故本页恒以 `:root` 的**浅色**取值渲染。这是可接受的
     ——一个只在根布局炸掉时出现的页面，「看得见、说人话、有出路」远比「跟随主题」重要；但要写在文件
     注释里，免得下一个人把它当成 bug 去追。

### D7 [P2] 未尊重系统「减少动态效果」偏好

- **证据**：`app/globals.css:74-82` 有 `@keyframes shimmer` + `.skeleton { animation: shimmer 1.4s
  ease-in-out infinite; }`；组件里另有 10 处 `animate-*`（脉冲 / 旋转），全仓 `prefers-reduced-motion`
  命中 **0**。
- **为什么修**：这是无障碍与人机交互的基础项（WCAG 2.3.3），对前庭功能敏感人群是真实困扰；一个满屏
  常驻脉冲点、骨架微光、连接状态呼吸灯的**行情类**界面尤其明显。
- **修法**（`apps/web/app/globals.css`，追加在「焦点可达性」段之后）：

  ```css
  /* 尊重系统「减少动态效果」偏好（WCAG 2.3.3）：装饰性动画一律压到最短，但**不移除任何信息**
     —— 状态仍由颜色 / 文案表达，这里关掉的只是"动"。全仓无 transitionend/animationend 监听
     （已核实），故压缩时长不会吊死任何回调。 */
  @media (prefers-reduced-motion: reduce) {
    *,
    *::before,
    *::after {
      animation-duration: 0.01ms !important;
      animation-iteration-count: 1 !important;
      transition-duration: 0.01ms !important;
      scroll-behavior: auto !important;
    }
  }
  ```

### D8 [P2] `WeightExploreModal` 自绘遮罩，绕开了本仓一等的 `Modal` 原语

- **证据**：`apps/web/components/Factor/WeightExploreModal.tsx:232-243` 自绘
  `fixed inset-0 z-50 ... bg-black/50` 遮罩，`role="dialog"` / `aria-modal` 挂在**遮罩**上而非对话框
  面板上；`:133-135` 自己监听 `Escape`。相较 `components/ui/index.tsx:251-341` 的 `Modal`，它缺：
  **焦点陷阱**（Tab 会走到背后的整页控件上）、**初始焦点**、**关闭后焦点归还**、**body 滚动锁**、
  **portal**（受任何祖先 `transform` / `overflow` 的层叠上下文影响）。
- **影响面**：这是一个会跑数十分钟训练任务的长驻弹窗；键盘用户一旦 Tab 出去就再也回不来。全仓只有这
  **一处**破例，修掉即全站一致。
- **修法**：改用原语，保留其宽版式与长内容滚动：

  ```tsx
  <Modal open onClose={onClose} labelledBy={titleId} className="max-w-4xl">
    <div className="flex flex-col gap-4 overflow-y-auto p-4">
      ...原有内容，标题 span 加 id={titleId}...
    </div>
  </Modal>
  ```

  `Modal` 外框已是 `max-h-[85vh] flex-col overflow-hidden`，故滚动交给内层容器；`titleId` 用 `useId()`。
  **[v2]**（评审 P2-14）两条已复核的事实：① 可访问名会由现在的 `aria-label="因子组合探索训练"` 变成标题
  文本「探索训练 · 因子组合探索」——实测 `WeightExploreModal.test.tsx` **全文没有任何 dialog 查询**，
  改名不会打红既有用例，但新用例要显式钉住新名（§8.1）；② `className="max-w-4xl"` 与原语自带的
  `max-w-2xl` 并存、靠 Tailwind 工具类出现顺序决定胜负，这在本仓是**既有范式**（`AiBacktestModal.tsx:66`
  逐字同款，另有 `max-w-lg/xl/3xl/md` 共 10 余处），照抄即可，不必引入 tailwind-merge。
  同时**删除** `:133-135` 自建的 Escape 监听（原语已在捕获期处理，留着会双重关闭）与 `:238` 的
  `onClick={onClose}` / `:242` 的 `stopPropagation`（原语的遮罩点击用 `e.target === e.currentTarget`
  判定，语义更严）。

---

## 四、技术设计

### 4.1 改动落点全景

```
质量门禁层   apps/web/components/Factor/WeightBatchArena.test.tsx   D1  类型收窄，CI 转绿
             apps/web/package.json                                  D1  typecheck 脚本
             services/quant/tests/test_agent_ws.py                  D2  墙钟死线 + 管理器关停并收敛夹具
             desktop/package.json + .github/workflows/ci.yml        D5b 让既有 node 测试真正进门禁（含 Node 22）

记账口径层   services/quant/app/db/ai_auto_repositories.py          D3  holdSeconds 改逐腿 wallTs ?? ts
             apps/web/lib/aiTradeProfitability.ts                   D3b 新增 holdDurationText 纯函数
             apps/web/components/Agent/aiTradeStats/ProfitabilityCard.tsx  D3b 把算好的数给用户看

进程与外壳   services/quant/app/main.py                             D4  抽 _cancel_background：cancel + 有界收敛
             desktop/src/lib/renderer-health.js（新）+ main.js      D5  渲染进程崩溃/无响应/加载失败兜底

渲染兜底层   apps/web/components/ui/CrashFallback.tsx（新）          D6  两级边界共用同一张崩溃页
             apps/web/app/error.tsx / global-error.tsx（新）        D6  段级 + 根级

体验一致层   apps/web/app/globals.css                               D7  prefers-reduced-motion
             apps/web/components/Factor/WeightExploreModal.tsx      D8  收编进 Modal 原语
```

### 4.2 关键路径与时序

**（a）D2 —— Agent WS 用例为什么会偶发红**

```
测试协程                          事件循环                          aiosqlite 线程池
   |  ws_agent(ws_a, "conv")          |                                    |
   |--- manager.handle() ------------>| create_task(sess.run(...))         |
   |  receive_text() 进入等待           |  run: LLM 事件 → 落库 --------->  | (真实耗时，毫秒~十毫秒级)
   |  [旧] for _ in range(20000):      |                                    |
   |        await sleep(0)   ← 只让出一次调度、不推进时间；总预算 ≈ 几 ms    |
   |  预算耗尽 → WebSocketDisconnect    |                                    |
   |  assert any(type=="done") → False |<---- done 事件此刻才到 -------------|
   |  [新] while monotonic() < 死线:    |                                    |
   |        await sleep(0.005) ← 按墙钟等，线程池有机会跑完 → done 命中，正常断连
```

**（b）D3 —— 双时钟如何把持仓时长量化掉**

```
写侧（executor._make_trade）：ts = align_bar(wall)   ，wallTs = wall
   开仓 09:07  →  ts=09:00  wallTs=09:07
   平仓 09:27  →  ts=09:00  wallTs=09:27          （1H 周期，两腿同一根 bar）

读侧（_trip_row）  [旧] exit.ts - entry.ts = 0 秒        ← 一笔 20 分钟的往返读成 0
                   [新] (exit.wallTs ?? exit.ts) - (entry.wallTs ?? entry.ts) = 1200 秒
                        ↑ **逐腿**回退，与前端 autoTradePnl.ts:90-91 逐字同构（[v2] 评审 P1-3）
                   [回退成立的依据] 缺 wallTs 的 executor 腿其 ts 本就是墙钟（写 wallTs 与吸附 ts
                        由同一个 bar_seconds>0 判据控制）；唯一的非 executor 写点 offline_exit
                        （resume.py:359）也只写墙钟秒 —— 故逐腿取值处处等于「这条腿的真实墙钟」
```

> **[v2] 一条 v1 会踩的坑（评审 P1-3）**：若按「双腿全有墙钟才用墙钟」写，`offline_exit` 平仓腿
> （永远无 `wallTs`、`ts` 却是墙钟）会把整笔往返推回 `exit.ts(墙钟) − entry.ts(bar 对齐)`——一个两种
> 口径相减的数，且与前端读出的值不同。这正是本条要消灭的东西。

**（c）D5 —— 渲染进程崩溃的恢复路径**

```
render-process-gone(details)
  → logMain(...)                           先记日志：任何分支都不吞掉这一笔
  → quitting || win.isDestroyed()          [v2] 退出期直接返回：不重载、不弹窗（评审 P1-7a）
  → describeRendererGone(details)          纯函数：翻译 + 是否值得自愈
  → recoverable && budget.allow(now)       60s 内最多 3 次
      ├─ 是 → win.reload()                 用户侧表现为"闪一下自己回来了"
      └─ 否 → dialog[重新载入 / 打开日志 / 退出]   不再自动重载，避免崩溃循环
  → 让"外壳日志全绿 ≠ 没崩"这句教训不再成立

unresponsive / responsive               [v2] 只记日志、不弹窗（评审 P1-7b：长任务渲染会反复触发）
did-fail-load(主框架, 非 -3)             → loading.html + 配额内一次 1.5s 后重试 loadURL；用尽即停在过渡页
```

### 4.3 贯穿本轮的四条不变量

1. **单一真源**：`holdSeconds` 的时间源只有一条规则——**逐腿** `wallTs ?? ts`，与前端 `autoTradePnl`
   **同一份**口径（**[v2]** 评审 P1-3 订正：v1 写的是「双腿全有才用墙钟」，那反而会在 `offline_exit`
   往返上造出两种口径相减）。崩溃页的视觉只有一份（`CrashFallback`），段级与根级共用。
2. **缺失即缺失**：`wallTs` 缺席就诚实回退，不编一个时间；`medianHoldSeconds` 为空就显示 `—`。
3. **门禁只能变严，不能变松**：D1 禁止 `as any`；D2 只改测试的等待姿势、不改产品行为；D5b 只增门禁步骤。
4. **不碰下单热路径**：本轮**没有一个改动**落在 `ai_auto/session.py` / `live_auto/executor.py` /
   `okx/trading.py` 上。D3 只改读侧派生量。

---

## 五、文件 / 模块变更计划

| # | 文件 | 动作 | 一句话意图 | 缺陷 |
|---|---|---|---|---|
| 1 | `apps/web/components/Factor/WeightBatchArena.test.tsx` | 改 | `result()` 形参由 `string` 收窄为 `TrainMethod`，并补类型导入 | D1 |
| 2 | `apps/web/package.json` | 改 | `scripts` 增 `"typecheck": "tsc --noEmit"` | D1 |
| 3 | `services/quant/tests/test_agent_ws.py` | 改 | 两处 20000 次自旋改为墙钟死线 `_await_event`；新增 autouse 夹具关停 **并收敛** `AgentManager` 的宽限计时器 | D2 |
| 4 | `services/quant/app/db/ai_auto_repositories.py` | 改 | 新增 `_leg_wall_ts` / `_trip_hold_seconds`，`_trip_row.holdSeconds` 改**逐腿** `wallTs ?? ts` | D3 |
| 5 | `services/quant/tests/test_ai_trade_profitability_api.py` | 改 | `_add_trip` 支持逐腿 `wallTs`（**关键字参数 + 默认 None**，既有调用点零改）；新增三条持仓时长用例 | D3 |
| 6 | `apps/web/lib/aiTradeProfitability.ts` | 改 | **[v2]** 新增纯函数 `holdDurationText(pooled)`（口径与文案的单一来源） | D3b |
| 6b | `apps/web/lib/aiTradeProfitability.test.ts` | 改 | **[v2]** 断言 `holdDurationText`：有值 / `null` ⇒ `—`；G-10 词表段自动覆盖新文案 | D3b |
| 7 | `apps/web/components/Agent/aiTradeStats/ProfitabilityCard.tsx` | 改 | 新增「中位持仓时长（N 笔）」一行（`data-testid="ai-profitability-hold"`） | D3b |
| 7b | `apps/web/components/Agent/aiTradeStats/ProfitabilityCard.test.tsx` | 改 | 断言该行渲染，且 `medianHoldSeconds: null` ⇒ `—` | D3b |
| 8 | `services/quant/app/main.py` | 改 | **[v2]** 抽出 `_cancel_background(tasks, *, timeout)`：cancel 后有界 `asyncio.wait` + 取回异常；lifespan 调用一行 | D4 |
| 9 | `services/quant/tests/test_main_shutdown.py` | 新建 | **[v2]** 只测 `_cancel_background`（cancel-safe 任务收敛 / 吞取消的任务超时仍返回）；**不驱动真实 lifespan** | D4 |
| 10 | `desktop/src/lib/renderer-health.js` | 新建 | 纯函数：崩溃原因翻译 / 重载配额 / 加载失败判读（不依赖 electron） | D5 |
| 11 | `desktop/src/main.js` | 改 | **[v2]** 新增 `wireRendererHealth(win)` 并由 `createWindow()` 调一行（不把接线塞进已 60 行的 `createWindow`） | D5 |
| 12 | `desktop/scripts/renderer-health.test.mjs` | 新建 | `node --test` 覆盖三个纯函数的正常与边界路径（导入姿势照抄 `config.test.mjs`：`createRequire` + `require('../src/lib/renderer-health.js')`） | D5 |
| 13 | `desktop/package.json` | 改 | 增 `test` 脚本与 `check:rendererhealth` | D5/D5b |
| 14 | `.github/workflows/ci.yml` | 改 | **[v2]** desktop job 增 `npm test` 一步 **且** `node-version` 20 → 22 | D5b |
| 15 | `apps/web/components/ui/CrashFallback.tsx` | 新建 | 崩溃页展示组件（段级 / 根级共用，文案逐字沿用既有） | D6 |
| 16 | `apps/web/app/error.tsx` | 改 | 改为渲染 `CrashFallback`，保留 `console.error` 与全部可见文案 | D6 |
| 17 | `apps/web/app/global-error.tsx` | 新建 | 根级错误边界（自带 `<html><body>`），根 layout 抛错不再白屏 | D6 |
| 18 | `apps/web/components/ui/CrashFallback.test.tsx` | 新建 | 断言标题 / 错误正文 / 错误编号 / 重试回调 | D6 |
| 19 | `apps/web/app/globalError.source.test.ts` | 新建 | 源码扫描：`global-error.tsx` 存在、是客户端组件、含 `<html>`/`<body>`、用 `CrashFallback` | D6 |
| 20 | `apps/web/app/globals.css` | 改 | 追加 `@media (prefers-reduced-motion: reduce)` 段 | D7 |
| 21 | `apps/web/app/globals.source.test.ts` | 新建 | 源码扫描：reduced-motion 段存在且覆盖 animation/transition | D7 |
| 22 | `apps/web/components/Factor/WeightExploreModal.tsx` | 改 | 迁移到 `Modal` 原语，删除自建 Escape / 遮罩点击 | D8 |
| 23 | `apps/web/components/Factor/WeightExploreModal.test.tsx` | 改 | 补：Esc 关闭、焦点陷入对话框、`role="dialog"` 落在面板上 | D8 |
| 24 | `docs/plans/stability-hardening-audit/spec.md` | 新建 | 本文件 | — |

> **[v2]** 表内 **26** 个文件（24 项编号 + 评审新增的 6b / 7b 两项）即本轮**全部**改动面。实施节点若发现
> 必须超出此表，先在本文件「实施过程发现的方案缺陷」段落记录原因，再动手。
>
> 与 v1 的差异只有三处，均由评审引入：D3b 从「改 1 个组件」变成「lib 纯函数 + 组件 + 两个测试」
> （P1-9），D4 的用例文件由 `test_main_lifespan.py` 改名为 `test_main_shutdown.py` 并换了被测对象
> （P1-4），`ci.yml` 那一行多改一个 `node-version`（P0-1）。**没有新增任何一个功能面**。

---

## 六、接口设计

本轮**不新增、不删除、不重命名任何 REST / WebSocket 端点**，也不改动任何响应体的键集。

- **HTTP / WS**：零变更。`GET /api/ai-trade/profitability` 的响应形状不变；其中
  `pooled.medianHoldSeconds` 的**语义**由「bar 对齐秒之差」修正为「真实墙钟秒之差（缺墙钟则回退）」。
  键名、类型（`number | null`）、可空性一律不变，故 `response_model` 与前端类型零改动。
- **CLI**：新增 `pnpm --filter aragon-web typecheck`（等价 `cd apps/web && pnpm typecheck`）与
  `cd desktop && npm test`。二者都是既有能力的显式入口，不改变任何已有命令的行为。
- **[v2] 新增内部函数签名**（Node，`desktop/src/lib/renderer-health.js`，CommonJS 导出；评审 P1-8 已删掉
  `level` 与 `reset()` 两个无读点的成员）：

  ```js
  /** @param {{reason?: string, exitCode?: number}} details */
  // [终审 F5] 实际落地多一个 `expected`（仅 `clean-exit` 为真，有读点，见 F5）
  function describeRendererGone(details): { reason: string, message: string,
                                           recoverable: boolean, expected: boolean }
  function createReloadBudget(opts?: { limit?: number, windowMs?: number }): { allow(nowMs: number): boolean }
  function describeLoadFailure(errorCode: number, errorDescription: string, validatedURL: string): string | null
  module.exports = { describeRendererGone, createReloadBudget, describeLoadFailure };
  ```

- **新增内部函数签名**（Python，`app/db/ai_auto_repositories.py`，模块私有）：

  ```python
  def _leg_wall_ts(trade: Trade) -> int | None
  def _trip_hold_seconds(trip: _Trip) -> int
  ```

- **[v2] 新增内部函数签名**（Python，`app/main.py`，模块私有；评审 P1-4）：

  ```python
  async def _cancel_background(tasks: tuple[asyncio.Task, ...], *, timeout: float = 5.0) -> None
  ```

- **[v2] 新增前端纯函数契约**（TS，`lib/aiTradeProfitability.ts`；评审 P1-9）：

  ```ts
  export function holdDurationText(pooled: AiTradePooled): string   // 恒返回一句带分母的中文；无样本 ⇒ "—"
  ```

- **新增组件契约**（TSX，`components/ui/CrashFallback.tsx`）：

  ```tsx
  export default function CrashFallback(props: {
    title: string;
    description: string;
    error?: { message?: string; digest?: string };
    onRetry?: () => void;
    retryText?: string;   // 默认 "重试"
  }): JSX.Element
  ```

---

## 七、数据模型

**零数据库迁移。** 不新增列、不改列类型、不写迁移脚本、不动 `_ensure_trade_risk_columns` 一类的补列器。

本轮唯一与持久化有关的是 D3，它读的是**已经在库里的**数据：

```
trades.explain_json  (TEXT, nullable)
  └─ { "kind": ..., "meta": { "wallTs": <int 秒>, "exitSource": ..., "clOrdId": ..., ... } }
                                 ▲
                                 └── 由 append_live_trade（repositories.py:285-296）落盘：
                                     一笔成交**没有 explain**时，只要带 wallTs 就会合成 {"meta": {...}} 信封
```

**[v2] 关于「哪些腿有 `wallTs`」的准确事实**（评审 P1-3 订正；v1 此处写的是「AI-trade 往返的两条腿都有
`wallTs`」——**不成立**）：

| 写路径 | 有 `wallTs`？ | 该腿的 `ts` 是什么 |
|---|---|---|
| `executor._make_trade`，`bar_seconds > 0`（AI-trade 常规腿） | **有** | 吸附到 K 线开盘秒 |
| `executor._make_trade`，`bar_seconds <= 0`（本轴未装配） | 无 | `_align_bar_ts` 逐字回退，**本就是墙钟** |
| `resume._offline_exit_trade`（`resume.py:359-368`，离线平仓回读） | **无** | 交易所 `uTime`，**真实墙钟秒**（`okx/trading.py:665` 已 //1000） |
| `live_auto`（非 AI 链路） | 无（键集冻结门禁守着） | 吸附到 K 线开盘秒 |
| 2026-07 墙钟上线前的老数据 | 无 | 吸附到 K 线开盘秒 |

第三行是 v1 漏掉的那条：`_offline_exit_trade` 自带 `explain`，故连 `append_live_trade` 那段「无 explain
才合成 meta 信封」的分支都进不去。**它正是「逐腿 `wallTs ?? ts`」优于「双腿全有才用墙钟」的原因**——
前者对这条腿给出真实墙钟，后者会把整笔往返推回混合口径。

读取纪律与既有 `_trip_meta` 完全一致：坏 JSON / 缺列 / 非 dict ⇒ 视为缺席，逐腿回退 `ts`。
老数据与非 AI 链路（`live_auto` 的成交 dict 刻意不带 `wallTs`，其形状被
`test_ai_trade_switches_never_leak_into_non_ai_executor` 的键集冻结门禁守着）**两腿同时**走回退分支，
**行为逐字不变**。

前端 store / localStorage（`aragon-ui`，当前 `version: 30`）**不需要升版**：本轮没有任何持久化偏好的
增删改。

---

## 八、测试与验收标准

### 8.1 新增 / 修改用例清单

| 用例 | 位置 | 断言要点 |
|---|---|---|
| `_await_event` 生效 | `services/quant/tests/test_agent_ws.py` | 既有 6 条用例在 §2.3 的前置组合下连跑 3 次全绿 |
| 管理器不泄漏 | 同上（autouse 夹具） | 运行输出中不再出现 `Task was destroyed but it is pending!`。**[v2]** 该断言只有在夹具**真的 await 了**被取消的计时器时才成立（`shutdown()` 体内无 await，评审 P1-5） |
| 墙钟持仓时长 | `services/quant/tests/test_ai_trade_profitability_api.py` | 两腿 `ts` **相同**（同一根 bar）、`wallTs` 相差 1200 ⇒ `holdSeconds == 1200`（旧实现为 0） |
| 老数据回退 | 同上 | 两腿均无 `wallTs` ⇒ `holdSeconds == exit.ts - entry.ts`（既有行为不变） |
| **[v2]** 单腿缺墙钟（`offline_exit` 形状） | 同上 | 开仓腿有 `wallTs`（`ts` 为 bar 开盘秒）、平仓腿**无** `wallTs` 而 `ts` 为墙钟 ⇒ `holdSeconds == exit.ts − entry.wallTs`，即**逐腿**取值。这条用例**取代** v1 那条「走 `ts` 回退、绝不混用」——按评审 P1-3，混用恰恰是 v1 规则的产物 |
| lifespan 收敛 | **[v2]** `services/quant/tests/test_main_shutdown.py`（新） | ①cancel-safe 任务：`await _cancel_background((task,))` 后 `task.done()` 为真；②吞掉 `CancelledError` 的任务 + `timeout=0.05`：函数**仍返回**且不抛，`task.done()` 为假（用例末尾必须真正收掉它）。**不驱动真实 `lifespan(app)`**（理由见 D4） |
| 盈利体检卡新读数 | `apps/web/lib/aiTradeProfitability.test.ts` + `apps/web/components/Agent/aiTradeStats/ProfitabilityCard.test.tsx` | 纯函数：`medianHoldSeconds=1271, roundTrips=193` ⇒ 含「21 分钟」与「（193 笔）」；`null` ⇒ 含 `—`。组件：`data-testid="ai-profitability-hold"` 存在且文本与纯函数一致 |
| 崩溃页 | `apps/web/components/ui/CrashFallback.test.tsx`（新） | 标题 / 错误正文 / `错误编号：` / 点「重试」触发 `onRetry` 一次 |
| 段级边界不回归 | `apps/web/app/error.test.tsx`（既有，**不得修改**） | 重构后依然全绿——这是那次白屏事故的回归护栏 |
| 根级边界存在 | `apps/web/app/globalError.source.test.ts`（新） | 文件存在；含 `"use client"`、`<html`、`<body`、`CrashFallback` |
| 减少动态效果 | `apps/web/app/globals.source.test.ts`（新） | `globals.css` 含 `prefers-reduced-motion: reduce` 段，且含 `animation-duration` 与 `transition-duration` |
| 探索弹窗可达性 | `apps/web/components/Factor/WeightExploreModal.test.tsx` | `role="dialog"` 落在面板上；按 Esc 触发 `onClose` 恰一次；打开后焦点在对话框内 |
| 渲染健康纯函数 | `desktop/scripts/renderer-health.test.mjs`（新） | `oom`/`crashed` ⇒ `recoverable=true`；`killed` ⇒ `false`；配额第 4 次 `allow` 为假、跨窗口后恢复；`errorCode === -3` ⇒ `describeLoadFailure` 返回 `null` |

> **源码扫描类用例的仓库级坑**（务必遵守）：本仓 `core.autocrlf=true`，干净检出上文件是 CRLF。
> 任何跨行正则**必须**写 `\r?\n`，否则在干净检出上恒红。

### 8.2 门禁命令与期望输出

```bash
# G1) 前端类型（本轮 P0-D1 的直接验收）
cd apps/web && pnpm exec tsc --noEmit                 # 期望：无任何输出，退出码 0

# G2) 前端单测（基线 1796，本轮新增用例后应 ≥ 1802）
cd apps/web && NODE_ENV=test pnpm test                # 期望：Test Files 全 passed
#    NODE_ENV 前缀是**本机**必需（本机环境预置 NODE_ENV=production，见 §2.2）；CI 上不要加

# G3) 后端全量（基线 3636，本轮新增用例后应 ≥ 3641）
cd services/quant && PYTHONUTF8=1 ./.venv/Scripts/python.exe -u -m pytest -q   # 期望：0 failed

# G4) D2 专项：§2.3 的最小复现命令，连跑 3 次
#    期望：3 次均 "N passed"，且输出无 "Task was destroyed but it is pending!"

# G5) 桌面端
cd desktop && npm run check     # 期望：全部 node --check 通过（含新增的 check:rendererhealth）
cd desktop && npm test          # [v2] 期望：**本机** ≥ 17 + 新增（本机 17 含未跟踪的
                                # ensure-wincodesign.test.mjs）；**CI 检出只有 2 个已跟踪测试文件**
                                # + 新增的 renderer-health.test.mjs，故 CI 上的数应显著更小。
                                # 复核方式：git ls-files desktop/scripts

# ── [v2] 以下两条为评审 P0-2 新增：D1 修绿后，CI 的第 4/5 步是 6 周以来第一次执行 ──
# G6) 按 CI 原样姿势跑前端单测（**不带** NODE_ENV 前缀，与 ci.yml:49 逐字一致）
cd apps/web && pnpm test        # 期望：与 G2 同为全绿；若两者结果不同，说明存在环境耦合，必须查清

# G7) 按 CI 原样姿势构建（ci.yml:51；CI 未设 NEXT_OUTPUT_STANDALONE，故实为普通产物）
cd apps/web && pnpm build       # 期望：Compiled successfully，退出码 0；.next/ 已在 .gitignore
```

### 8.3 Definition of Done

1. **[v2]** §8.2 **七**条门禁命令（G1–G7）全部达到期望输出，**且把实际输出贴进实施节点的完成报告**
   （不接受"应该绿了"）。G6 / G7 是评审 P0-2 加的：本轮的目标是「门禁绿」，不是「D1 那一行改完了」。
2. **[v2]** §五 的 **26** 个文件全部落地；未落地项必须在本文件「实施过程发现的方案缺陷」段写明原因。
3. `git status` 中不含构建产物 / 临时文件；提交用 `git add <具体路径>`，**严禁 `git add -A`**
   （工作树上常驻大量与本轮无关的未跟踪诊断文档与在制文件）。
4. 提交信息以 `feat:` 前缀 + 一句话摘要；`git log -1 --stat` 列出的文件均落在 §五 表内。

### 8.4 人工验收（D5 接线，无法自动化的部分）

1. `cd desktop && npm run dev` 拉起调试窗口。
2. 在 DevTools Console 执行 `process.crash?.()`，或从任务管理器结束该应用的**渲染进程**（不是主进程）。
3. 期望：窗口在 1 秒内自行重载恢复；`%APPDATA%/aragon-desktop/logs/` 下的主进程日志出现
   `renderer gone: crashed — ...` 一行。
4. 连续触发 4 次：第 4 次不再自动重载，改为弹出「重新载入 / 打开日志 / 退出」对话框。
5. **[v2]（评审 P1-7a 新增，必做）退出期不得自愈**：正常点关闭按钮退出应用，确认日志里**没有**
   `renderer gone` 之后的重载，也没有任何对话框弹出——`quitting` 守卫就是为这一步存在的。

---

## 九、风险与缓解

| # | 风险 | 等级 | 缓解 |
|---|---|---|---|
| R1 | D3 改变了 `medianHoldSeconds` 的历史读数，用户会看到"数字变了" | 中 | 这正是修复目的（旧值是被 bar 量化的错值）。回退分支保证老数据行为逐字不变；文案带分母 `（N 笔）`，读数含义自解释。实施时在完成报告里写清"该读数口径已修正"。 |
| R2 | D2 的 10s 死线在极慢的 CI 机器上仍可能超时 | 低 | 死线设为常量 `_RUN_END_TIMEOUT_S = 10.0`（比原预算大三个数量级）；超时**不抛异常**，仍由原断言给出可读失败信息，不会把超时伪装成别的错。 |
| R3 | D6 重构 `error.tsx` 可能打破那次白屏事故的回归用例 | 中 | 硬约束：`app/error.test.tsx` **一个字都不许改**，可见文案逐字保留。重构后先单独跑该文件，绿了再继续。 |
| R4 | D7 的全局 `transition-duration: 0.01ms` 可能吊死依赖 `transitionend` 的逻辑 | 低 | 已核实全仓 `transitionend` / `animationend` 监听为 **0** 处。若未来引入，需改为白名单式收敛。 |
| R5 | D8 迁移到 `Modal` 后长内容布局回归（原版是 `items-start` + 外层滚动） | 中 | 原语外框已 `max-h-[85vh] flex-col overflow-hidden`，滚动下沉到内层 `overflow-y-auto`。迁移后必须人工开一次「探索训练」弹窗，确认长列表可滚、底部按钮不被裁。 |
| R6 | D5 的自动重载可能掩盖真实崩溃 | 中 | 配额（60s/3 次）+ 每次都写日志 + 超额后强制弹窗。**绝不静默无限重载**。 |
| R7 | D5b 打开 desktop 测试门禁后 CI 变红（`ensure-wincodesign.test.mjs` 目前未跟踪） | 中 | 该文件是未跟踪的在制文件，**本轮不提交它**；CI 检出里不存在该文件，`node --test "scripts/*.test.mjs"` 只会跑到已跟踪的两个（`git ls-files desktop/scripts` 已复核：仅 `config.test.mjs` / `config.okx-link.test.mjs`；本地实测 17 passed 含未跟踪那个，CI 上只会更少）。实施节点须在提交前用 `git stash -u` 或干净 worktree 复验一次。 |
| **[v2] R7b** | 同一条门禁的**另一个**、也是更致命的红因：Node 版本（评审 P0-1） | 高 | `node --test` 的 glob 匹配是 Node 21+ 才有的能力，而 package.json 里的 glob 带引号（`sh` 不展开）。CI desktop job 现钉 `node-version: "20"` ⇒ 一上线就红。修法见 D5b 第 3 条：同一次改动里把它抬到 `"22"`（本机实测版本）。 |
| **[v2] R10** | `ai_auto_repositories.py` 现 714 行，加 D3 两个助手后 ≈ 745 行，逼近 CLAUDE.md 的 800 行阈值（评审 P2-15） | 低 | 本轮可接受（两个助手合计 ~30 行且与 `_trip_meta` 同段，属就近扩展）。但下次再往该文件加读数前必须先拆——**不要**在本轮顺手拆（§十 第 2 条）。 |
| R8 | 工作树上有大量未跟踪的在制文件，误提交风险高 | 高 | 只用 `git add <具体路径>` 逐个暂存 §五 表内文件；提交前 `git status --porcelain` 与 `git log -1 --stat` 双向核对。 |
| R9 | 全量 pytest 单进程耗时长（本机 3,636 条约需数十分钟） | 低 | 已知事实，非缺陷。**禁止并发跑两个 pytest 进程**——`tests/conftest.py` 在导入期就把整套用例钉在同一个临时 DB 上，并发即互相污染。 |

---

## 十、本轮明确不做（Out of scope）

写清楚不做什么，和写清楚做什么同样重要——下游实施节点**不得**擅自扩大到以下范围：

1. **不统一 AI-trade 的写入时钟**。`ts` 被 `_align_bar_ts` 吸附是图表标记落点的刚需；把写侧改成墙钟会
   连带动到 `session.py` 决策轮里那个「一形参两用」的 `ts`（同时喂 `executor.apply` 与 prompt 的
   `_build_multi_timeframe_context`），即动 prompt 冻结门禁。本轮只修**读侧派生量**（D3）。
2. **不重构任何超阈文件**。`schemas.py`(2494) / `session.py`(2213) / `aiTradeTypes.ts`(1607) 等确实超过
   CLAUDE.md 的 800 行阈值，但拆分是一次独立的、需要自己的 spec 与回归策略的工程；在一次以"修缺陷"为
   目标的迭代里顺手拆文件，只会让本轮的每一条验收都失去可归因性。
3. **不动下单 / 撮合 / 风控热路径**（`ai_auto/session.py`、`live_auto/executor.py`、`okx/trading.py`）。
4. **不引入任何新依赖**（无新 npm 包、无新 pip 包）。
5. **不改 UI 的信息架构与配色体系**。D3b / D6 / D7 / D8 都是在既有设计语言内的补齐，不是重设计。
6. **不提交**工作树上既有的未跟踪在制文件（`docs/diagnoses/**` 新增目录、`desktop/scripts/ensure-wincodesign*`、
   `services/quant/app/db/live_auto_repositories.py` 等）。
7. **[v2] 不修 `AgentManager.shutdown()` 里同形状的「只 cancel 不 await」**（`manager.py:268-275`，评审
   P2-11）。它与 D4 是同一个缺陷形状，但长在产品代码里、且它取消的 run 任务另有 `_drain` 收口
   （`api/agent.py`），改它要连带重新论证 agent 关停时序——那是另一轮的事。**本轮只在测试夹具里
   补收敛**（D2 修法 4），产品代码零 diff。写在这里是为了让下一个人知道：这不是被漏掉的，是被决定的。
8. **[v2] 不给 CI 加 `continue-on-error`、不注释掉任何门禁步骤**。G6 / G7 若红，只能修红因或如实记录，
   绝不用「把门关小一点」换一个绿——那正是本轮 P0 两条缺陷的成因。

---

## 实施过程发现的方案缺陷

> 本节由 Subtask #2（实施节点）追加。每一条都给出「怎么发现的」与「实际怎么改的」，
> 凡超出 §五 26 个文件表的改动一律记在这里。

### F1 D2 的根因在**另一个文件**里还有第二处，spec 只点了 `test_agent_ws.py`

- **怎么发现的**：按 §8.2 的 G4 连跑 3 次（本轮 D2 修完之后），**第 1 次**红在
  `tests/test_agent_manager_detach.py::test_resume_after_completion_acks_false_completed_without_replay`
  —— `assert sess.running is False` 得到 `True`。第 2、3 次全绿（158 passed）。第 1 次跑时本机
  同时在跑前端全量 vitest，即**负载更高**，这正是 D2 根因 1 描述的那个条件。
- **它是同一个缺陷形状**：`test_agent_manager_detach.py:140` 用 `await asyncio.sleep(0.1)  # 等 run 自然完成`
  当等待预算。run 要经 aiosqlite 线程池落库（真实耗时），100ms 在有负载时不够 ⇒ 断言在 run 还没
  跑完时就先执行。与 D2 的「循环 N 次 sleep(0)」只是**计量单位不同**（固定墙钟 vs 循环次数），
  同为「拿一个猜出来的常数当收敛判据」。
- **为什么必须本轮修**：§8.3 的 DoD 第 1 条要求 G3（全量 0 failed）与 G4（连跑 3 次全绿）。
  这条不修，本轮的核心目标「把偶发红的门禁修绿」就只完成了一半——换一条用例继续偶发红。
- **实际改法**（**只改测试，产品代码零 diff**，与 D2 同一条纪律）：`tests/test_agent_manager_detach.py`
  新增模块级 `_await_run_started(sess, timeout=10.0)`（墙钟死线 + 5ms 轮询，超时不抛），
  `_start_slow_run` 里先 `_await_run_started(sess)` 确认 run 真的起跑，再保留原 `sleep(0.08)` 让它
  产出事件（`_SlowLLM` 每 20ms 一条，这一段是**产出量**预算而非**完成**预算，语义正确，故保留）。
  > `test_resume_after_completion...` 那处 `sleep(0.1)` 起初也打算换成同一个助手的「等到 `running`
  > 为假」形态，但 F1b 查明**等那个标志本身就不够**（`AgentSession.run` 的 finally 先置
  > `_running=False`、再 await 心跳收尾），故最终改成直接 `await AgentManager.drain(...)` 等任务
  > **真正**结束——见下条。
- **超出 §五 表的文件**：`services/quant/tests/test_agent_manager_detach.py`（第 **27** 个文件）。

### F1b 同一条根因的**第三处**：run 任务被 `drain` 取消后没人 await，`GeneratorExit` 会红在别的模块

- **怎么发现的**：F1 修完后再跑 G4，第 1 次仍红。抓全输出定位到红的是
  `tests/test_agent_provider_api.py::test_delete_official_returns_400`（以及另一次的
  `test_list_is_masked_and_has_seeded_providers`）——**与 agent run 毫无关系的两条用例**，
  错误是 `sqlalchemy/util/_concurrency_py3k.py:196: GeneratorExit`，而 captured log 里明写：

  ```
  ERROR asyncio: Task was destroyed but it is pending!
  task: <Task pending coro=<AgentSession.run() running at app/agent/loop.py:265>
        wait_for=<Future finished result=<sqlite3.Connection ...>>>
  ```

  把批次收窄到 9 个文件即**稳定复现**（连续 2 次必红），故不是负载抖动，是**顺序**依赖。
- **根因**：`AgentManager.drain()` 超时后只 `task.cancel()`、**不 await**（`manager.py`）。
  `test_agent_manager_detach.py` 的 `_teardown` 依赖它收尾，于是一个仍握着 aiosqlite greenlet
  的 run 任务被留在已关闭的事件循环上；它被 GC 时把 `GeneratorExit` 抛进当时正在等待该 greenlet
  的协程——那已经是**后面某个模块**的用例了。这是本轮 D4 / D2 根因 2 的**同一形状第三次出现**
  （只 cancel 不 await），只是这次的受害者不是自己而是无辜的下游用例。
- **为什么之前没露头**：`test_resume_after_completion...` 原本那句 `sleep(0.1)` 顺带给了这些
  「已取消但没被 await」的任务一段调度时间。F1 把它换成条件等待后，那段"意外的宽限"没了——
  **F1 没有制造这个缺陷，只是把它从概率性掩盖变成了确定性暴露**。这恰恰是本轮想要的：
  一个偶发红换成一个稳定红，才修得动。
- **实际改法**（仍**只改测试，产品代码零 diff**——§十 第 7 条明写 `AgentManager` 的同形缺陷本轮不动）：
  `tests/test_agent_manager_detach.py` 新增 `_settle_run_task(sess)`（`cancel()` 后
  `asyncio.gather(..., return_exceptions=True)`），`_teardown` 在 `drain` 之后调用它；
  `test_resume_after_completion...` 改为直接 `await AgentManager.drain(sess.run_task, 10.0)`
  等任务**真正**结束（`AgentSession.run` 的 finally 先置 `_running=False` 再 await 心跳收尾，
  故只等那个标志会在任务仍挂起时就返回），并补上此前缺失的 `await mgr.shutdown()`。
- **验收**：收窄批次连跑 3 次 → `75 passed`，输出无 `Task was destroyed but it is pending!`；
  G4 在并发构建负载下连跑 3 次 → 3 × `158 passed`。
- **残留（如实记录，本轮不追）**：全量跑的 warnings 摘要里仍有一条
  `PytestUnhandledThreadExceptionWarning: RuntimeError: Event loop is closed`（aiosqlite 的工作线程在
  某条用例的事件循环关闭后回调），**它不再产生任何失败**（G3 = 0 failed）。它说明整套里仍有**某处**
  在关循环时留着一次在途 DB 操作——但归因需要再跑一次全量（本机 ~14 分钟）才能拿到那条用例的名字，
  且它既不在 §五 的 26 个文件里、也不构成红。下一轮若要追：
  `pytest -q -W error::pytest.PytestUnhandledThreadExceptionWarning` 可把它变成一条可定位的红。

### F3 D3 说「后端这一处是唯一漏网的」——**不成立**，实盘结算侧还有一处（本轮**不修**，如实记账）

- **怎么发现的**：改完 `_trip_row` 后反查 `holdSeconds` 的全部写点，命中
  `services/quant/app/ai_auto/trip_ledger.py:214`：

  ```python
  hold_seconds=max(0.0, float(exit_ts - open_snap.ts)),   # exit_ts = close_trade["ts"]
  ```

  两条腿取的都是**被 `_align_bar_ts` 吸附过**的 `ts`，与 D3 修掉的那一行是同一个缺陷；
  而这条路径手上**明明有**墙钟：`open_snapshot(..., now_wall=...)` 把它存成了 `snap.wall_ts`
  （`trip_ledger.py:158/180`），平仓腿的成交 dict 也带 `wallTs`。它经 `trip_meta_wire` 落进
  每笔成交的 `explain.meta.holdSeconds`。
- **为什么本轮不修**（三条，缺一我就修了）：
  1. **超出 §五 的 26 个文件表**，且 `trip_ledger` 位于 AI-trade 的**实盘结算**路径上
     （`attribution.settle_close` ← `session.py` 的 `_inject_explain_meta`），改它就是动
     §十 第 3 条划出的那类路径；
  2. 它的值落在 `explain.meta` 里，那个键集有冻结门禁守着，改值要连带重新论证冻结用例；
  3. **目前查不到用户可见的读点**：全仓 `explain.meta.holdSeconds` 只有类型声明
     （`lib/types.ts:412`），前端两处渲染 `holdSeconds` 的地方读的都是**另一个** wire
     （`AiTradeHorizonSnapshot.holdSeconds`，其值来自 `hold_horizon.py:663` 的
     `now_wall - opened_wall`，**是墙钟，没问题**）；周期验收卡的 `holdSecondsSum` 同样取自
     `horizon_runtime.hold_samples`（`now_wall - opened_wall`，也是墙钟）。
- **给下一轮的交接**：修法是现成的——`settle()` 里逐腿取 `wallTs ?? ts`（开仓腿用
  `snap.wall_ts`，平仓腿用 `close_trade.get("wallTs")`），与本轮 `_trip_hold_seconds` 逐字同构。
  但它必须自带冻结门禁的重新论证，属于独立一轮。**记在这里是为了让下一个人知道：这不是被漏掉的，
  是被看见后决定不做的**（与 §十 第 7 条同款姿态）。

### F4 G3 的「0 failed」在**本工作树**上不可能达成——有 7 条红长在一个未跟踪的在制文件里

- **实测**：`pytest -q` → `7 failed, 3630 passed, 4 skipped, 2 deselected in 969.13s`。
  7 条**全部**来自 `services/quant/tests/test_factor_library_expansion.py`
  （`git ls-files` 无输出 ⇒ **未跟踪**），单独跑该文件即复现：
  `ValueError: 未知因子：mom_tsmom_vol`；而 `grep -c "mom_tsmom_vol" app/engine/factors/library.py` = **0**
  ——它锁定的那 8 个新因子在本工作树**根本没有实现**。这是别人的在制工作（`factor-library-crypto-expansion`），
  与本轮零关系，且按 §十 第 6 条本轮不提交它，**CI 检出里不存在这个文件**。
- **数目对得上**：`3630 + 7 + 4 = 3641 = 基线 3636 + 本轮新增 5 条`（D3 三条 + D4 两条），
  没有任何一条既有用例因本轮改动而消失或变红。
- **故 G3 的验收姿势**改为按 CI 视角复跑一次：`pytest -q --ignore=tests/test_factor_library_expansion.py`。
  下游提交节点请注意：看到那 7 条红**不要**去"修"，它们在等自己的特性代码。

### F5 [终审] `render-process-gone` 的第 7 个 reason `clean-exit` 未被枚举，会为一件正常的事弹错误框

> 本条与下面的 F6 由 Subtask #3（终审节点）追加：终审改了源码，就要按同一条纪律记在这里。

- **怎么发现的**：逐条复核 D5 的 `GONE_REASONS` 覆盖面。spec 正文列了 6 个 reason
  （`crashed` / `oom` / `killed` / `abnormal-exit` / `launch-failed` / `integrity-failure`），
  而 Chromium 的 `TerminationStatus` 还有 `NORMAL_TERMINATION`——Electron 把它翻成
  **`clean-exit`**（渲染进程自己正常退出，例如渲染侧调了 `process.exit(0)`）。
- **它为什么是缺陷**：`clean-exit` 落进 `describeRendererGone` 的「未知 reason」分支 ⇒
  `recoverable: false` ⇒ 接线直接走 `promptRendererGone`，弹出一个标题为「界面进程已退出」、
  按钮为「重新载入 / 打开日志 / 退出」的**错误对话框**。而这件事根本不是崩溃。一个崩溃兜底
  在没崩的时候吓用户，比没有兜底更伤信任。
- **为什么不是「把它也当成可自愈」**：那会引入一个更坏的形状——把一个刚被**正常**关掉的渲染
  进程再拉回来。退出期有 `quitting` 守卫，但守卫只覆盖走 `gracefulQuit` 的那条路。
- **实际改法**（三处，均在 D5 的既有落点内）：
  1. `renderer-health.js` 的 `GONE_REASONS` 增 `clean-exit` 条目，并让返回值多一个
     `expected: boolean`（未知 reason 与其余 6 个 reason 一律 `false`）。这个字段**有读点**
     （下一条），不违反评审 P1-8 删掉 `level` / `reset()` 的那条纪律。
  2. `main.js` 的 `render-process-gone` 接线：`if (!isLive() || info.expected) return;`
     ——记完日志就收手，不自愈也不弹窗。
  3. `renderer-health.test.mjs` 补一条用例钉住「不自愈且不弹窗」，并给既有的
     `killed / launch-failed / integrity-failure` 与未知 reason 两条补上 `expected === false`
     的断言（否则「哪些算故障」这条线没有护栏）。
- **超出 §五 表的文件**：无（三个文件都在表内第 10 / 11 / 12 项）。

### F6 [终审] 崩溃对话框的 Promise 没有 `.catch()`，一次拒绝会让兜底自己把应用杀掉

- **怎么发现的**：复核 D5 接线的失败路径时反查 `promptRendererGone` 的
  `dialog.showMessageBox(...).then(...)` —— 全链路没有 `catch`。
- **它为什么是缺陷**：主进程的未处理 Promise 拒绝按 Node 的默认策略会落到
  `process.on('uncaughtException')`，而本文件末尾那个钩子的动作是
  `gracefulQuit().finally(() => app.exit(1))`。也就是说：**崩溃兜底的最后一道出口一旦自己出错，
  结果是整个应用退出**——正是它要救的那个会话。
- **实际改法**：`promptRendererGone` 链尾补
  `.catch((err) => logMain(...))`，只留一行日志。文件仍在 §五 表内第 11 项。

### F2 D3 的用例需要一个 `exit_ts` 覆盖，否则第三条验收写不出真实的数

- **怎么发现的**：写 §8.1 那条「单腿缺墙钟（`offline_exit` 形状）」用例时，`_add_trip` 的平仓腿 `ts`
  写死为 `ts + 100`，而该形状要求「开仓腿 `ts` 是 bar 开盘秒、平仓腿 `ts` 是真实墙钟」——两者之差
  必须能自由设定，否则只能凑出一个 `entry.wallTs > exit.ts` 的负差（被 `max(0, ...)` 压成 0），
  验收变成一句空话。
- **实际改法**：`_add_trip` 除 spec 要求的 `entry_wall_ts` / `exit_wall_ts` 外，再加一个
  `exit_ts: int | None = None`（同为**关键字 + 默认 None**，既有 6 个调用点逐字零改）。
  不新增文件，仍在 §五 表第 5 项之内。

---

## 附录 A：全部复现命令（可直接粘贴）

```bash
# A1 前端类型（当前红，D1 修复后应无输出）
cd apps/web && ./node_modules/.bin/tsc --noEmit -p tsconfig.json

# A2 前端单测基线
cd apps/web && NODE_ENV=test ./node_modules/.bin/vitest run

# A3 后端全量（当前偶发 1 F）
cd services/quant && PYTHONUTF8=1 ./.venv/Scripts/python.exe -u -m pytest -q

# A4 D2 最小复现（当前稳定复现，见 §2.3 的 23 个文件列表）

# A5 桌面端既有 node 测试（当前 CI 未跑）
cd desktop && node --test "scripts/*.test.mjs"

# A6 全仓 reduced-motion 命中数（当前为 0）
grep -rn "prefers-reduced-motion" apps/web --include=*.css --include=*.tsx | wc -l

# A7 自绘遮罩清点（当前 2 处，其中 1 处是原语自身）
grep -rn "fixed inset-0" apps/web/components apps/web/app --include=*.tsx

# ── [v2] 评审复核期新增的四条（结论已写进 §评审记录） ──
# A8 desktop 门禁到底会跑到哪几个文件（CI 检出 = 已跟踪者）
git ls-files desktop/scripts        # 当前只有 config.test.mjs / config.okx-link.test.mjs 两个测试文件

# A9 本机 node 版本（决定 `node --test` 的 glob 能不能用；CI 现钉 20，见 P0-1）
node --version                      # 本机 v22.18.0

# A10 CI 的前端第 4/5 步已经多久没跑过（= 那条类型错误进仓多久）
git log -1 --date=short --format="%h %ad" -- apps/web/components/Factor/WeightBatchArena.test.tsx

# A11 为什么本机跑 vitest 要带 NODE_ENV=test（vitest 只在未设置时才置 test）
node -e "console.log(process.env.NODE_ENV)"    # 本机输出 production
```

## 附录 B：评审重点提示（给 Subtask #1）

请优先核这五条：

1. **D3 的回退分支是否真的覆盖了老数据**——去 `_add_trip` 造一条两腿都无 `explain_json` 的往返，确认
   `holdSeconds` 与修改前逐值相同。
2. **D2 是否只改了测试**——产品代码（`app/agent/**`、`app/api/agent.py`）应当零 diff。
3. **D6 是否真的没动 `app/error.test.tsx`**——那是事故回归护栏。
4. **D5b 打开的门禁在干净检出上是否为绿**——`ensure-wincodesign.test.mjs` 未跟踪，必须在干净 worktree
   上复验一次，否则会把 CI 从"红在类型检查"换成"红在桌面测试"。
5. **§五 的 24 个文件是否与 §三 的 8 条缺陷一一对得上**——没有孤儿文件，也没有无落点的缺陷。

> **[v2] 五条的复核结果**（本节保留原文以便对照）：
> ①**成立但需改写规则**——回退确实覆盖老数据，但 v1 的「双腿全有才用墙钟」在 `offline_exit` 上会造出
> 混合口径，已改为逐腿（P1-3）。②**成立**——D2 的修法确实只碰 `tests/test_agent_ws.py`；但夹具兑现不了
> 它自己的验收，已补收敛（P1-5）。③**成立**——`error.test.tsx` 只断言文本，重构安全；追加一条 `<pre>`
> 子节点结构的硬约束（P2-13）。④**只对了一半**——未跟踪文件这一层是对的，但真正会让 CI 红的是 Node
> 版本（P0-1）。⑤**对不上的地方已补**——D3b 缺 lib 落点、D4 的用例文件换了被测对象，现为 26 个文件。

---

## 附录 C：给实施节点的执行顺序建议（[v2] 评审补充）

不是硬性要求，但按这个顺序做，每一步都能立刻被一条命令判定，出错时的归因范围最小：

1. **D1**（两行 + 一个 script）→ 立刻跑 G1。这是唯一一条「不修就没有门禁」的，先修。
2. **G6 / G7**（CI 姿势的单测与构建）→ **在动其它代码之前先跑一次**，拿到一个干净基线。若此刻就红，
   那是存量问题，与本轮后续改动无关——这个先后顺序本身就是可归因性。
3. **D2**（测试 + 夹具）→ 跑 G4 三次，再跑 G3 一次。
4. **D3 / D3b**（后端读侧 + 前端读点）→ 跑 G3 与 G2。
5. **D4**（`_cancel_background` + 新用例）→ 跑 G3。
6. **D6 / D7 / D8**（前端三条）→ 跑 G1 / G2；D8 之后按 R5 人工开一次探索弹窗。
7. **D5 / D5b**（桌面端，最后做）→ 跑 G5，并按 §8.4 做五步人工验收。**放最后**是因为它是本轮唯一
   一处无法被自动化完全覆盖的改动，前面全绿时它出的任何问题都能被立刻归因到自己身上。
8. 提交：`git add <具体路径>` 逐个暂存 §五 表内 26 个文件 → `git status --porcelain` 核对 →
   `git log -1 --stat` 反向核对。

---

## 评审结论

**有条件通过（approved with conditions）。**

本方案的证据质量高于本仓多数 spec：8 条缺陷全部可复现、行号级引用逐条命中、修法写到了可以直接落键盘的
程度，「不做什么」比「做什么」写得还清楚。评审没有推翻任何一条缺陷判定，也没有要求扩大范围——9 条 P0/P1
全部是「修法本身不成立」或「验收兑现不了」，且已在正文改掉。

放行条件（下游实施节点必须逐条满足，缺一即视为未完成）：

1. **按 v2 正文实施，不得回退到 v1 的写法。** 九处已改写的落点：
   D1 的 CI 后续两步（P0-2）、D5b 的 Node 22（P0-1）、D3 的逐腿 `wallTs ?? ts`（P1-3）、
   D4 的 `_cancel_background` 与不驱动真实 lifespan（P1-4）、D2 夹具的计时器收敛（P1-5）、
   D6 的 `import "./globals.css"`（P1-6）、D5 的 `wireRendererHealth` + 三道守卫（P1-7）、
   renderer-health 契约删去 `level` / `reset()`（P1-8）、D3b 的 `holdDurationText` 一行文案（P1-9）。
2. **§8.2 的 G1–G7 七条命令全部实跑，实际输出贴进完成报告。** 尤其 G6 / G7——它们是「门禁真的绿了」
   与「D1 那一行改完了」之间的全部差别。
3. **G5 必须在干净检出/worktree 上复验一次**（R7 + R7b 两条红因都要排掉：未跟踪测试文件、Node 版本）。
4. **产品代码零 diff 的两处承诺要守住**：D2 只改 `tests/test_agent_ws.py`；`AgentManager.shutdown()`
   与下单/撮合/风控热路径本轮一个字不动（§十 第 3、7 条）。
5. **实施中若发现本 spec 的新缺陷**，先在本文件「实施过程发现的方案缺陷」段写清原因再动手；超出 §五
   26 个文件的改动同理。

不构成放行障碍、但请在实施中顺手带上的：P2-10（回退分支的 docstring 措辞）、P2-12（§8.2 desktop 期望值
分本机/CI 两写）、P2-14（D8 新用例钉住新的可访问名）、P2-15（`ai_auto_repositories.py` 逼近 800 行阈值，
本轮不拆但要记账）。

> 评审人：Subtask #1（设计评审节点）· 2026-08-16 · 复核基线 `master` @ `ec3c9f9`
