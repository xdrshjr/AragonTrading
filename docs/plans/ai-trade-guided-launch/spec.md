# AI 自动交易 · 引导式启动（ai-trade-guided-launch）

> 状态：**v2**（已按设计评审意见就地修订；P0×3 / P1×8 全部修毕，P2×6 记录在案）
> 作者：方案设计节点（Subtask #0）｜评审与修订：设计评审节点（Subtask #1）
> 涉及车道：`apps/web`（AI 自动交易启动弹窗）+ `services/quant`（一个默认值 + 一个只读加法字段）
> 零 DB 迁移 · 零新端点 · `POST /api/ai-trade/start` 请求体逐字段不变

---

## 评审记录（v2 · 设计评审节点）

评审维度：**可行性**（本仓当前技术栈能不能建）/ **完整性**（边界、错误路径、灰度）/
**一致性**（CLAUDE.md 与仓库既有约定）/ **规模适配**（是否过度设计或欠规格）。

> 评审先**逐条复核了 §二 的 F-1 ~ F-12**：12 条取证在当前工作树上全部核对通过（行号、默认值、
> 判据表达式、用例条数、`response_model` 静默剔除的风险），故本轮不推翻事实基线；下表的每一条
> 都来自它**没有查到**的那几类事实，已补成 F-13 ~ F-18。
>
> 三条 P0 与八条 P1 **均已在正文就地修毕**（每条给出落点）；六条 P2 记录在案、不阻断实施。

### P0（阻断实施）

| id | 维度 | 结论 | 已修落点 |
|---|---|---|---|
| P0-1 | 一致性 | **G-4「既有 `noWrapContract.source.test.ts` 按目录扫描、自动覆盖新文件」是错的。** 它的 G2 确实按目录扫全仓 `components/**` + `app/**`，但判据是 `expect(seen).toBe(TOTAL_ANCHORS)` 的**等值冻结**（现值 19；实测全仓恰为 19 处、分布在 15 个文件）。§4.3 要求步骤轨行容器带 `data-nowrap="row"` ⇒ 一加锚点，G2 当场判红（20 ≠ 19），而 v1 §七 的 30 项变更清单里没有这个文件、§8.3 也没写要改它（v2 已补为第 31 项）。反过来，若实现者为了让门禁保持绿而不加锚点，Type A 契约在全部新文件上就**没有任何冻结**——G-4 就成了一条永远不会红的门禁，比没有更坏 | §4.3 锚点段、§7.3 新增第 31 项、§8.3 G-4 改写 |
| P0-2 | 完整性 | **`tradabilityNeedsConfirm` 在改造后仍是「由一个已卸载的子组件写入」的父级 `useState`。** §4.2.4 只上提了取数与跃迁检测，没说这个值必须**改为派生**。它今天的唯一写点是子组件里的 `onNeedsConfirmChange`（`AiTradeTradabilityFields.tsx:129`，父侧 state 在 `AiTradeLaunchModal.tsx:326`）。分步之后用户不访问第 5 步 ⇒ 子组件从不挂载 ⇒ 没有任何人写它 ⇒ `tradabilityBlocked` 恒 `false`、CTA 文案恒为「确认并开始」。W-14 / R-5 会直接失败；而更坏的收场是有人为了让用例变绿去改断言——那样一次真实资金启动就绕过了本轮唯一一次 fail-open 确认，正是 RK-2 声称要消灭的那个失效 | §4.2.4 改写、§8.2 新增 W-20、§8.3 新增 G-6 |
| P0-3 | 完整性 | **U-2 的参照集选错了。** `directionError` / `trendError` 进 `baseValid`（`AiTradeLaunchModal.tsx:720-721`）因而进 `ready`，但**不在 `firstError` 里**（:792-809——它们各自就地渲染在字段旁，:1375 / :1389）。按 U-2 字面「归属表并集 === `firstError` 读的集合」施工 ⇒ 这两项没有任何步骤归属 ⇒ 第 5 步的「下一步」不被它们挡、第 6 步横幅也不提它们，而 `ready === false` 让启动按钮**灰着且全屏没有一句理由**。单屏形态下这尚可忍（错误就在同一屏的字段旁），分步之后是死路 | §4.2.3 归属表与参照集改写 + `review` 兜底、§8.1 U-2 改写、§8.2 新增 W-21 |

### P1（须在实施前修毕）

| id | 维度 | 结论 | 已修落点 |
|---|---|---|---|
| P1-1 | 可行性 | **N-10「跳到确认」的判据 `ready` 在实盘首启恒 `false`。** `store.ts:799` 的 `aiTradeAcknowledged` 默认 `false`，且 :475 的注释写明「首启风险勾选会话记忆，**刷新复位**」；而它是 live 段 `ready` 的合取项（:724），勾选框本身又在第 6 步。于是这个「老手不该被六次点击惩罚」的出口，恰好对**每一次刷新后的首次启动**都不存在，R-4 按字面不可达 | N-10、R-4、W-19 |
| P1-2 | 可行性 | **`<md` 折叠若渲染第二棵 DOM 树，全部导航用例当场撞「found multiple elements」。** jsdom 不求值 CSS 媒体查询（`app/globals.source.test.ts:7` 已把这件事写成文），`hidden md:flex` 的轨与 `md:hidden` 的横向进度条会**同时**存在于用例 DOM 里，`gotoStep` 的 `findByRole("tab", { name })` 必抛 | §4.3 布局段、§8.2 新增 W-22 |
| P1-3 | 完整性 | **I-1 的「零丢失」范围未定义，且已知至少两条既有非阻断警示落在冻结的 8 条之外**：`AiTradeExperienceFields.tsx:170`（`ai-trade-experience-off-note`，落在第 4 步）与 `lib/aiTradeHorizon.ts:279-287`（唤醒周期「启动时会被自动抬到约 N 分钟」，落在第 2 步）。U-4 又禁止 `collectRisks` 产出注册表外的 id ⇒ 这两条**永远进不了第 6 步**，而 §一 与 I-1 的措辞都声称「任何一条」都会再出现一次。一条只覆盖自己想得起来的那几项、却自称全覆盖的不变式，比不声称更坏 | 注册表扩为 10 条、§4.2.5 新增「已知排除项」、§8.1 U-3 / U-4 |
| P1-4 | 可行性 | **`useDebouncedValue` 在本仓不存在**（全仓 `grep -r useDebounce apps/web` 零命中），§4.2.4 却直接引用它，§七 也没有它的落点 | §4.2.4 明确为 hook 文件私有小函数、不新增文件 |
| P1-5 | 一致性 | **G-2 与 §4.2.4 自相矛盾。** G-2 禁「该文件里第二个『把受控 state 写回默认值』的 effect」，而上提之后的跃迁重置做的正是 `setTradabilityConfirmed(false)`。按 G-2 字面，本轮自己的修法就是违例——一条会把正确实现判红的门禁，会被下一个人直接删掉 | §8.3 G-2 判据精确化 |
| P1-6 | 一致性 | **`rovingTabIndex` 是 `components/ui/index.tsx:531` 的模块私有函数（未 `export`）**，N-6「复用既有 `rovingTabIndex`」不成立；且它只认 `←/→` + `Home/End`，而 ≥md 的步骤轨是**纵向**的——纵向 tablist 的键盘期望是 `↑/↓` + `aria-orientation="vertical"`。改那个共享函数去支持 `↑/↓` 会连带改变 11 个既有 `Tabs` 调用方的按键消费行为，明显超出本轮范围 | N-6 改写：轨内自带键位映射，**不动** `ui/index.tsx` |
| P1-7 | 完整性（灰度） | **既有环境不随默认值翻转。** 桌面壳按 `AI_TRADE_` 前缀把用户 `agent.env` 里的键透传给后端子进程（`desktop/src/lib/config.js:80`），而 `.env.example` 至今写着 `=true`。照它抄过一份的用户在本轮之后**照旧被 422**，而工单会写着「已修复」。§十 只写了「置 `true` 即回滚」，没写「已经是 `true` 的那些环境怎么办」 | §10.3 新增、§4.1.2 remedies 落点提示、R-2 增一档 |
| P1-8 | 完整性 | **附录 A 的迁移规则「首次出现…插入 `gotoStep`」是单数的**，而跨两步的用例真实存在（`AiTradeToggle.test.tsx:246-248` 同一个用例先动第 5 步的「允许回吐（%）」、再动第 3 步的「单仓止损%」）。一条被机械照做就会漏插的迁移配方，正是 RK-4 所怕的「改动中混入语义漂移」的入口——漏插之后最省事的修法就是删断言 | 附录 A 迁移规则改写 + 该用例范例 |

### P2（记录在案，不阻断实施）

| id | 结论 |
|---|---|
| P2-1 | §4.4 的 `aiTradeLaunchFormat.ts` 清单漏列 7 个同类纯函数：`fromLocalInput` / `toLocalInput` / `fmtOcoPct` / `rawPctToFraction` / `rawToNumber` / `numToRaw` / `secondsToHoursRaw`（`AiTradeLaunchModal.tsx:1561-1707`）。已在 §4.4 补一句「其余同文件纯函数一并搬出、逐条零改」 |
| P2-2 | `effectiveMinHoldSeconds` 的非 `custom` 分支：wire 上 `minHoldSeconds` 的类型是 `number \| null \| undefined`（`profile_wire` 对 `target_hold_min_s is None` 的条目下发 `null`，`hold_horizon_profiles.py:52`），须 `?? 0`。**行为本就正确**（`null > 0` 为 `false`），红的只会是 `tsc` |
| P2-3 | N-1（任意跳转）与 N-2（本步错误挡「下一步」）并存 ⇒ 那个 `disabled` 是**提示**而非拦截（用户点轨即可绕过）。已在 N-2 明说，免得下一个读者把它当护栏 |
| P2-4 | 内容区应带 `role="tabpanel"` + `aria-labelledby` 指向当前 tab。既有 `Tabs` 刻意不加 `aria-controls`，理由是它的调用方普遍没有独立 panel（`ui/index.tsx:429` 有成文说明）——本轮有，故不适用那条豁免 |
| P2-5 | 新增文案一律不写 Markdown 强调符：JSX 里的 `**粗体**` 会原样显示星号，本仓已为此复发 4 次。风险条目的 `title` / `detail` 若取自后端字符串同理 |
| P2-6 | 实施前先记一份**全量红基线**（工作树上有并发兄弟节点的未跟踪文件与在制改动）。R-12 的「除既有红外全绿」没有基线就不可判定 |

---

## 一、概述

本轮解决两件互相独立、但都落在**同一个入口**上的问题：用户在左侧「AI 助手」面板拨动
【AI 自动交易】开关时弹出的那个启动弹窗（`apps/web/components/Agent/AiTradeLaunchModal.tsx`）。

**第一件事是一道拦路的运维互锁。** `ai-trade-loss-and-liveness-hardening` 轴 A 引入了
`AI_TRADE_REQUIRE_PROFIT_EXIT_WITH_HORIZON`（`services/quant/app/config.py:1171`，默认 `True`）：
当会话「装了持仓周期锁（最短持仓 > 0）却关掉了利润保护层」时，`POST /ai-trade/start` 直接
422 `AI_TRADE_UNGUARDED_HORIZON`。它当初的动机是真的——生产库 211 条 run 里 193 条没有利润侧
离场通道，其中 45 条还选了承诺「硬止盈 50%」的纪律预设。但它把一个**判断题**变成了**禁令**：
交易周期模式默认勾选（`horizonEnabled` 初值 `true`）、默认模式 `intraday_swing` 的
`minHoldSeconds = 7200`，于是「取消勾选利润保护」这一个动作就让启动整个失败。用户的要求很明确：
不开启利润保护时也应当能启动自动交易。本轮把这道闸的默认值翻成 `False`，**但不删除它**——运维
仍可重新武装；同时把「你正在没有任何自动兑现通道的情况下运行」从一条 422 降级为一条**永远
看得见、但从不阻断**的风险披露。这是本仓库既有纪律的直接应用：服务端替用户改了什么必须显式回执，
而"把用户挡在门外"从来不是本项目对不确定性的正确回应。

**第二件事是这个弹窗本身。** 它现在是 1937 行、一屏 20 个分区、一次性铺开的长滚动表单：交易周期
模式、经验模式、市场情报、时间级别、基础策略铁律、策略多选、多标的分配、资金摘要、六个金额输入、
仓位档位、唤醒周期、研判深度、纪律预设、方向对称与趋势基调、可交易性预检、利润保护层、上下文
轮数、真实资金知情确认——全部并排堆在 `max-h-[62vh]` 的滚动容器里。它违反了 CLAUDE.md 的 800 行
红线（超出 2.4 倍），更重要的是它违反了人机交互的基本判断：**一次要求用户同时做 20 个决定，等于
一个都没有真正被决定**。本轮把它改造成一个**六步引导式向导**：每一步只回答一个问题，最后一步把
全部选择连同风险清单摊开供确认。

分步化有一个天然的、必须被设计掉的副作用：**藏起来的警示等于消失的警示**。今天那些非阻断提示
（档位分辨率会让三层风控失效、可交易性预检判定当前周期几乎开不出单、总预算被夹紧、逆势档位与
唤醒周期打架）之所以还起作用，是因为它们和启动按钮在同一屏。分步之后，一个从未翻到第 5 步的
用户就再也看不到第 5 步的警示。因此本设计的核心不变式是**风险零丢失**（I-1）：任何一条在某一步
上产生的非阻断警示，必须在最后一步的风险清单里再出现一次；反过来，最后一步不得出现任何不来自
某个具体步骤的警示。这条不变式由一张冻结的风险注册表 + 一条等值门禁钉死，而不是靠实现者的记性。

---

## 二、现状取证（本设计的事实基线，评审请勿重新推翻）

| 编号 | 事实 | 取证位置 |
|---|---|---|
| F-1 | 互锁总闸默认 `True` | `services/quant/app/config.py:1171` |
| F-2 | `.env.example` 登记为 `true` | `.env.example:882` |
| F-3 | 判据唯一真源，`enabled=False ⇒ 恒 False` | `services/quant/app/ai_auto/discipline.py:250-283` |
| F-4 | 422 落点**必须**留在端点内（冷续跑复用装配链，422 会被翻成 409「启动契约已失效」） | `services/quant/app/api/ai_auto.py:475-511` |
| F-5 | 弹窗 1937 行；`max-h-[62vh]` 单滚动容器内 20 个分区 | `AiTradeLaunchModal.tsx:812-1540` |
| F-6 | 前端二次确认判据 `mode==="live" && !profitGuardEnabled && !unguardedConfirmed`——**比服务端判据宽**（服务端还要求周期已装配且 `min_hold_s > 0`） | `AiTradeLaunchModal.tsx:731` |
| F-7 | 可交易性预检的取数是子组件内的裸 `useEffect` + 350ms debounce，`prevLevel` 用 `useRef` 做跃迁检测 | `AiTradeTradabilityFields.tsx:91-120` |
| F-8 | 弹窗**只有一个** reset effect，依赖收敛为 `[open]`；另写第二个 effect 是 `ai-trade-modal-revert-recurrence` 的复发根因 | `AiTradeLaunchModal.tsx:425-503` |
| F-9 | `GET /ai-trade/horizon-profiles` 声明了 `response_model`，`profiles_wire` 多下发的键会被 FastAPI **静默剔除** | `app/schemas.py:759-766`（P0-3 成文教训） |
| F-10 | 弹窗组件用例 79 条；`AiTradeToggle.test.tsx` 另有 4 处直接操作弹窗字段并点 CTA | `AiTradeLaunchModal.test.tsx`、`AiTradeToggle.test.tsx:161/190/216/246` |
| F-11 | 后端 `test_g_a1` / `test_g_a8` 依赖**进程默认为 True** 才能走到 422 分支 | `test_ai_trade_loss_liveness_session.py:154/220` |
| F-12 | `test_new_settings_defaults_are_the_documented_ones` 显式断言该键默认 `True` | `test_ai_trade_loss_liveness_core.py:480` |
| F-13 | **零折行门禁不是「自动覆盖」**：G2 按目录扫 `components/**` + `app/**`，但判据是 `expect(seen).toBe(TOTAL_ANCHORS)`（现值 **19**，实测全仓恰 19 处 / 15 个文件）；G3 另有一张 25 项的 `MANAGED` 逐文件计数表（`===`，不是 `≥`） | `components/ui/noWrapContract.source.test.ts:61-96 / 168-197`（v2 新增，P0-1） |
| F-14 | `rovingTabIndex` 是**模块私有**函数（未 `export`），且只认 `←/→` + `Home/End` | `components/ui/index.tsx:531`（v2 新增，P1-6） |
| F-15 | `aiTradeAcknowledged` 默认 `false` 且**刷新复位**（不是持久化偏好），而 live 段 `ready` 与它合取 | `lib/store.ts:475/799`、`AiTradeLaunchModal.tsx:724`（v2 新增，P1-1） |
| F-16 | `firstError` **不含** `directionError` / `trendError`——两者只进 `baseValid`/`ready`，各自就地渲染在字段旁 | `AiTradeLaunchModal.tsx:720-721 / 792-809 / 1375 / 1389`（v2 新增，P0-3） |
| F-17 | 弹窗里**还有两条**非阻断警示不在本轮 8 条注册表内：经验模式「既不读取也不沉淀」、唤醒下限「启动时会被自动抬到约 N 分钟」 | `AiTradeExperienceFields.tsx:170`、`lib/aiTradeHorizon.ts:279-287`（v2 新增，P1-3） |
| F-18 | 前端用例跑在 jsdom（`vitest.config.ts`，无 setup 文件、无 `matchMedia` 桩）⇒ **媒体查询不求值**，`hidden md:*` 与 `md:hidden` 两棵树在用例里同时存在。作为对照：`contrastTokens.source.test.ts` ②③ 与 `warnToken.source.test.ts` 确实是纯目录扫描（G-5 的自动覆盖成立），**只有** `noWrapContract` 不是 | `apps/web/vitest.config.ts`、`contrastTokens.source.test.ts:23-47`（v2 新增，P1-2） |

---

## 三、目标与非目标

### 3.1 目标

- **G-1**：不开启利润保护时，`POST /ai-trade/start` 默认放行（无需改任何环境变量）。
- **G-2**：互锁**不被删除**——运维置 `AI_TRADE_REQUIRE_PROFIT_EXIT_WITH_HORIZON=true` 即逐字节
  恢复今天的行为（422 + 三条 remedies + 前端二次确认）。
- **G-3**：启动弹窗改为六步引导，每一步只承担一类决定，且每一步都能凭默认值直接「下一步」。
- **G-4**：最后一步是**全量只读摘要 + 风险清单 + 启动**；风险零丢失（I-1）。
- **G-5**：`AiTradeLaunchOpts`（提交体）与 `POST /ai-trade/start` 请求体**逐字段不变**——本轮不改
  任何一条交易语义。
- **G-6**：新增与改写的每一个前端文件 ≤ 800 行（CLAUDE.md 红线），并由一条门禁冻结。

### 3.2 非目标（明确不做）

- **N-1**：不改任何交易语义、护栏阈值、决策链路、执行器、回测车道。
- **N-2**：不删除 `unguarded_horizon` / `_require_profit_exit_with_horizon` / 三条 remedies。
- **N-3**：不新增端点、不新增 `Settings` 键、不做 DB 迁移。
- **N-4**：不做「启动配置保存为模板 / 方案库」（诱人，但超出本轮范围，且会引入一份需要与服务端
  预设表对账的新持久化真源）。
- **N-5**：不改 `AiTradeToggle` 的生命周期（暂停 / 继续 / 结束 / 冷续跑）逻辑。
- **N-6**：不把已存在的会话（含暂停中、冷续跑）纳入本轮任何判据——互锁只拦新建，这条不动。

---

## 四、技术设计

### 4.1 轴 A：解除互锁（默认关），但不删除它

#### 4.1.1 服务端三行改动

1. `services/quant/app/config.py:1171`
   `ai_trade_require_profit_exit_with_horizon: bool = True` → `= False`，并把 docstring / 行注释
   改写为：**默认关**；置 `True` 即重新武装 §4.1 互锁（422 + 三条 remedies），语义与今天逐字相同。
2. `.env.example:882` `AI_TRADE_REQUIRE_PROFIT_EXIT_WITH_HORIZON=true` → `=false`，并改写其上方
   那段注释：从「装了周期锁却关掉保护层 ⇒ 新启动被 422 拦下」改述为「**默认不拦**；用户可以在
   不开启利润保护的情况下自动交易，代价（浮盈无自动兑现通道）由启动弹窗最后一步的风险清单
   如实披露。机构侧若要恢复强制互锁，置 `true`」。
3. `services/quant/app/api/ai_auto.py:475-511` `_require_profit_exit_with_horizon`——**函数体
   一个字节不改**（判据、落点、三条 remedies、错误体形状全部保留）。只在 docstring 顶部补一段：
   默认已关，本函数是运维可选的加固闸；落点仍必须留在端点内（F-4 的理由不因默认值改变而失效）。

> **为什么不删。** 删掉它就没有任何办法把互锁装回去，而这道闸当初是从 211 条真实 run 的对账里
> 长出来的。默认值是产品决定，代码路径是能力——本轮只改前者。这也让轴 A 的回滚成本是**一个环境
> 变量**，而不是一次 revert。

#### 4.1.2 前端：把「阻断」降级为「披露」，并让判据与服务端逐字同构

今天前端的二次确认判据（F-6）比服务端宽：它不看周期是否装配、不看 `min_hold_s`。默认值翻转之后
如果不动它，症状是「后端放行了、前端还在拦」——一个用户永远解释不了的假门禁。

**做法**：把互锁总闸的状态**下发给前端**（§5.1 的加法字段），前端据它决定是否阻断；无论阻断与否，
风险披露恒在。

```ts
// apps/web/lib/aiTradeLaunchRisk.ts
/**
 * 「装了持仓周期锁却没有利润侧钥匙」——与后端 `discipline.unguarded_horizon` **逐字同构**。
 * 两侧同值由 U-5 的真值表用例钉死（表格逐行照抄 `test_g_a9_unguarded_horizon_truth_table_is_frozen`）。
 */
export function unguardedHorizon(input: {
  profitGuardEnabled: boolean;
  horizonEnabled: boolean;
  effectiveMinHoldSeconds: number;   // 见下方 effectiveMinHoldSeconds()
  interlockArmed: boolean;           // = wire.requireProfitExitWithHorizon
}): boolean {
  return input.interlockArmed
    && !input.profitGuardEnabled
    && input.horizonEnabled
    && input.effectiveMinHoldSeconds > 0;
}
```

`effectiveMinHoldSeconds` 同样是纯函数：非 `custom` 模式取 `profiles.find(id).minHoldSeconds ?? 0`
（**服务端下发值，前端不复制任何一个数**；`?? 0` 不是防御性写法而是类型事实——`profile_wire` 对
`target_hold_min_s is None` 的条目下发 `null`，见 `hold_horizon_profiles.py:52`，漏了它红的是 `tsc`
而不是行为，【v2 · P2-2】）；`custom` 模式取 `horizonCustom.minHoldRaw` 解析值，**空串 ⇒ 0**（与
服务端「custom 预设 `min_hold_s=0`」逐字一致，即空串是安全退出口）。预设表尚未落地
（`horizonQ.isPending` / 读取失败）⇒ **先于模式分支**返回 `0` ⇒ 不阻断——注意这条判定必须排在
`custom` 分支之前：`horizonProfileId` 在预设表落地之前会回落成字面量 `"custom"`
（`AiTradeLaunchModal.tsx:605-606`），先分支就会去读一个与服务端无关的用户输入。
**不确定一律不翻译成否决**，这是本仓库四次「永不开单」事故之后的成文纪律。

于是：

| 互锁总闸 | 组合是否致命 | 前端行为 | 服务端行为 |
|---|---|---|---|
| `false`（本轮默认） | 是 | 风险清单里一条 `danger` 条目；**不要求勾选、不阻断** | 200 放行 |
| `false` | 否 | 无 | 200 |
| `true`（运维武装） | 是 | 恢复今天的二次确认勾选 + 提交路径拦截 | 422 + 三条 remedies |
| `true` | 否 | 无 | 200 |

> **注意极性。** `profitGuardOff` 这条风险条目的渲染条件是「利润保护未勾选」**本身**，不是
> `unguardedHorizon()`——即便没有周期锁，关掉利润保护也值得在最后一步说一句（今天弹窗里那份
> 「将失效的机制」后果清单原样保留、原样搬进风险条目的 `detail`）。`unguardedHorizon()` 只决定
> 它的 `tone` 是 `warn` 还是 `danger`、以及**是否需要勾选**。

**【v2 · P1-7】互锁武装时必须说出「是谁在拦你」。** `interlockArmed === true` 只可能来自运维侧的
显式配置，而本轮的产品承诺是「默认放行」。两者相遇时，用户看到的是一个**与发行说明矛盾**的
二次确认框。故 `profitGuardOff` 在 `requiresAck` 为真时，`detail` 末尾恒追加一句由前端固定给出的
说明：本机构已开启运维互锁 `AI_TRADE_REQUIRE_PROFIT_EXIT_WITH_HORIZON`。同理，422 错误横幅里
第三条 remedy（`ops_override`，今天本就只作说明不作按钮）下方补一行**前端本地**文案，列出这个键
在本产品里的三个可能落点：进程环境变量、`services/quant/.env`、桌面端的 `agent.env`
（`desktop/src/lib/config.js:80` 按 `AI_TRADE_` 前缀整段透传）。
**后端错误体一个字节不改**——这一行的所有者是前端，不是 `remedies`。

---

### 4.2 轴 B：六步引导式启动

#### 4.2.1 步骤模型（单一真源：`apps/web/lib/aiTradeLaunchSteps.ts`）

```ts
export type AiTradeLaunchStepId =
  | "scope"      // 1 标的与策略
  | "rhythm"     // 2 周期与节奏
  | "capital"    // 3 资金与仓位
  | "cognition"  // 4 Agent 认知
  | "guards"     // 5 纪律与护栏
  | "review";    // 6 确认并启动

export interface AiTradeLaunchStepDescriptor {
  id: AiTradeLaunchStepId;
  /** 步骤条与内容区标题的**唯一**来源（G-3 门禁：Step*.tsx 里不得出现字面量标题）。 */
  title: string;
  /** 一句话说明「这一步在回答什么问题」，渲染在内容区标题下方。 */
  question: string;
  /** 该步在给定模式下是否存在（本轮六步两模式皆存在，形参为将来留口）。 */
  visibleIn: readonly ("live" | "backtest")[];
}

export const AI_TRADE_LAUNCH_STEPS: readonly AiTradeLaunchStepDescriptor[] = [ /* 六项 */ ];
export function stepsFor(mode: "live" | "backtest"): AiTradeLaunchStepDescriptor[];
```

**六步的内容划分**（每一格都对应今天弹窗里已经存在的分区，本轮不新增任何一个交易参数）：

| # | 步骤 | 回答的问题 | 承载的分区（今天的行号） |
|---|---|---|---|
| 1 | `scope` 标的与策略 | 对谁交易、凭哪些信号 | 模式段控（`AiTradeLaunchModal.tsx:818-836`）、多标的分配器（:1032-1136，live）、回测单标的（:1141-1152）、回测区间（:1155-1160）、策略多选（:1002-1029） |
| 2 | `rhythm` 周期与节奏 | 多久做一次决策、一笔拿多久 | `AiTradeHorizonFields`（:855-877）、时间级别（:896-914）、Agent 唤醒周期（:1289-1318，live）、上下文刷新轮数（:1473-1481）、决策间隔 / 共识门槛（:1452-1471，backtest） |
| 3 | `capital` 资金与仓位 | 拿多少钱、每次下多大 | 资金摘要 Row 表（:1163-1213）、六个金额 / 比例输入（:1215-1246）、仓位操作档位 + 分辨率警示（:1248-1286） |
| 4 | `cognition` Agent 认知 | 让 Agent 想多深、拿什么当证据 | 研判深度（:1320-1346，live；backtest 为只读说明）、`AiTradeExperienceFields`（:878-885）、`AiTradeIntelFields`（:888-895，backtest 整卡不渲染） |
| 5 | `guards` 纪律与护栏 | 什么时候**不**开单、浮盈怎么兑现 | 纪律预设（:1351-1358）、基础策略铁律（:917-1000）、`AiTradeDirectionFields`（:1363-1391）、`AiTradeTradabilityFields`（:1394-1406）、`ProfitGuardFields`（:1410-1437） |
| 6 | `review` 确认并启动 | 我到底要启动一个什么东西 | 逐步只读摘要（新）、风险清单（新）、二次确认（:1438-1453）、真实资金知情（:1483-1497）、错误横幅 + remedies（:1499-1535）、CTA（:1538-1553） |

**顺序的理由**：`guards` 排在 `review` 之前，是让「利润保护那一格」与「最后一屏的风险清单」相邻
——本轮轴 A 的主体正是这一格，用户关掉它之后**下一屏**就会看到后果，而不是翻回去找。
`cognition` 排在 `capital` 之后，是因为研判深度会与唤醒周期打架（`exhaustive` 档单轮 5–10 分钟），
那条提示需要在**两步都已确定**之后才说得出口（见 §4.2.5 的 `deepWakeTooShort`）。

#### 4.2.2 导航契约（N-1 ~ N-10）

- **N-1 自由跳转。** 步骤条上任意一步随时可点，不要求「已访问」。理由：每一步都有合法默认值，
  且全部表单状态住在父组件；把用户锁在第 1 步只是把「一股脑」换成了「闯关」。
- **N-2 「下一步」只被本步自有的错误挡住**（`stepError(id, model)`），跨步错误不挡本步。
  被挡时按钮 `disabled` 并在其左侧同屏渲染该错误原文（**不是**一个笼统的「请检查输入」）。
  **【v2 · P2-3】它是提示，不是拦截。** N-1 允许任意跳转，用户点一下轨就绕过去了；全流程**唯一**
  的真拦截仍然只有一处（`review` 步的 `ready` + `handleConfirm` 里那三个判据）。写明这一点是为了
  让下一个读者不要把它当护栏用——把校验拆成两处所有权，正是本轮最想避免的形状。
- **N-3 启动按钮只在 `review` 步出现**，其 `disabled` 判据**逐字沿用今天的 `ready`**——不新增、
  不放松、不收紧。文案沿用今天的三态（`确认并开始 AI 自动交易` / `仍要启动 AI 自动交易` /
  `启动中…`；回测段 `开始 AI 回测` / `回测启动中…`）。
- **N-4 步骤状态点。** 每一步在轨上带 `data-state` ∈ `{default, edited, warn, error}`，并以
  **形状 + 文字**区分（空心圆 / 实心圆 / 三角 / 叉），不靠颜色单独承载语义（仓库既有可达性纪律）。
- **N-5 步骤摘要。** 每一步在轨上渲染一行 `stepSummary(model, id)`（纯函数）产出的现值摘要，
  例如 `3 个标的 · 4 策略`、`日内波段 · 1H · 唤醒 30 分`。这是「分步之后仍能一眼看全」的唯一载体，
  也是把「一步一步」做成引导而不是幻灯片的关键。
- **N-6 键盘与可达性。** 步骤条 `role="tablist"` + `aria-orientation="vertical"`（≥md 为纵向轨），
  每项 `role="tab"` + `aria-selected` + roving tabindex；内容区 `role="tabpanel"` +
  `aria-labelledby` 指向当前 tab（**【v2 · P2-4】**：既有 `Tabs` 刻意不加 `aria-controls` 的理由是
  它的调用方普遍没有独立 panel（`ui/index.tsx:429` 有成文说明），本轮有独立 panel，故那条豁免不适用）。
  切步后焦点移到内容区标题（`<h3 tabIndex={-1}>`），并由一个 `aria-live="polite"` 区域播报
  「第 N 步，共 M 步：<标题>」。
  **【v2 · P1-6】键位映射写在轨文件内，不复用也不改 `ui/index.tsx`。** `rovingTabIndex`
  （`components/ui/index.tsx:531`）是**模块私有**函数，没有 `export`；而且它只认 `←/→`，纵向轨的
  键盘期望是 `↑/↓`。把它改成同时认 `↑/↓` 会连带改变 11 个既有 `Tabs` 调用方对这两个键的消费行为
  （它们目前放行给页面滚动），那是一次没人要求过的行为变更。故轨文件内自带一个 6 行的纯函数
  `railKeyToIndex(key, current, count)`：认 `↑/↓/←/→/Home/End`（纵向轨上 `←/→` 作为等价别名保留，
  横向折叠态下同一份映射天然仍然正确），其余按键回 `null` 即不 `preventDefault`。
  `Alt+←/→` 上/下一步（全局，与轨是否有焦点无关）。
- **N-7 `Esc` 仍然是「关闭弹窗」**，**不**劫持为「上一步」。两义会造成误关或误退，而弹窗承载的是
  一次真实资金授权。
- **N-8 步骤位置进那个既有的 `[open]` reset effect**——**绝不另写第二个 reset effect**
  （F-8 / `ai-trade-modal-revert-recurrence` 的复发根因）。由 G-2 源码门禁冻结。
- **N-9 步骤位置以 `StepId` 存储，不以数字下标存储。** 下标在两种模式下指向不同步骤，是必然的
  错位来源；切换模式后若当前步在新模式下不可见，收敛到 `stepsFor(mode)` 中最近的可见步。
- **N-10 「跳到确认」。** 底部操作条常驻一个次级按钮直达 `review`——老手不该被六次点击惩罚。
  **【v2 · P1-1】判据不是 `ready`，是「第 1–5 步各自的 `stepError` 全为 `null`」**（记作
  `inputStepsClean`）。用 `ready` 是错的：live 段的 `ready` 与 `acknowledged` 合取
  （`AiTradeLaunchModal.tsx:724`），而 `aiTradeAcknowledged` 默认 `false` 且**刷新即复位**
  （`store.ts:475/799`，它是会话记忆不是持久化偏好），那个勾选框本身又在第 6 步——于是这个出口
  恰好对**每一次刷新后的首次启动**都不存在，而那正是最需要它的时刻（R-4 按 v1 的字面不可达）。
  `inputStepsClean === false` 时该按钮不渲染（渲染一个点了没用的按钮更坏）。

#### 4.2.3 校验归属表（`STEP_ERROR_OWNERS`，冻结）

分步之后最容易犯的错是「某个校验没有归属」：它照旧拦住启动按钮，但没有任何一步的「下一步」会
被它挡住——用户走到第 6 步才被告知第 3 步填错了。故归属表必须是**全覆盖且无重叠**的划分，并由
U-2 用例断言。

> **【v2 · P0-3】参照集不是 `firstError`。** v1 把 U-2 的参照集写成「今天 `firstError` 读的那个
> 集合」，而 `directionError` / `trendError` **不在 `firstError` 里**——它们只进 `baseValid`
> （`AiTradeLaunchModal.tsx:720-721`）因而进 `ready`，各自就地渲染在自己那张卡的字段旁
> （:1375 / :1389），从来不进那条错误横幅。照 v1 字面施工，这两项会**没有任何步骤归属**：第 5 步的
> 「下一步」不被它们挡、第 6 步横幅不提它们，而 `ready === false` 让启动按钮**灰着且全屏没有一句
> 理由**。单屏形态下这尚可忍（错误就在同一屏的字段旁），分步之后是死路。
>
> 故参照集改为二者之并：
> **`READY_INPUTS` ∪ `BANNER_INPUTS`**，其中 `READY_INPUTS` = `ready` 表达式（含 `baseValid`）
> 读到的全部校验量，`BANNER_INPUTS` = `firstError` 读到的全部校验量。归属表必须覆盖这个并集。

**`review` 步的兜底（新增，与归属表配套）**：当 `ready === false` 而 `firstError === null` 时
（今天唯一的成因就是 `directionError` / `trendError`，但判据不写死在这两项上），`review` 步在 CTA
上方渲染一行：「第 N 步（<步骤标题>）有未解决的输入错误」+ 一个跳转到该步的「去处理」按钮，
其中 N 取归属表里**第一个** `stepError` 非空的步骤。
**`firstError` 的表达式本身逐字节不改**（它守着既有断言的次序），这一行是它旁边的补集，不是它的
替代品——否则 79 条既有用例里任何一条断言横幅原文的都会漂移。

| 校验量（今天的变量名） | 归属步 | 模式 |
|---|---|---|
| `membersError` | `scope` | live |
| `instSupported`（→「标的不支持自动交易」） | `scope` | backtest |
| `strategyError` | `scope` | 两者 |
| `rangeError` | `scope` | backtest |
| `horizonError` | `rhythm` | 两者 |
| `wakeError` | `rhythm` | live |
| `roundsError`（上下文刷新轮数） | `rhythm` | 两者 |
| `everyError` / `minConsensusError` / `roundsCapError` | `rhythm` | backtest |
| `inputError`（预算 / 杠杆 / 总止盈损 / 单仓 TP·SL%） | `capital` | 两者 |
| —（本步永不产生阻断错误） | `cognition` | 两者 |
| `baseRuleError` / `profitGuardError` | `guards` | 两者 |
| `directionError` / `trendError`（**`firstError` 之外**，只进 `ready`；见上方 P0-3） | `guards` | 两者 |
| `tradabilityBlocked` / `unguardedBlocked` / `acknowledged` 未勾 / 服务端 `error` | `review` | 见文 |

`stepError(id, model)` 只返回**当前模式下有效**的那些（例如 live 段不看 `everyError`）。
`review` 步的错误横幅继续用今天的 `firstError`（次序不变），两者共用同一批表达式、不各写一份；
`firstError` 覆盖不到的那两项由上文的 `review` 兜底行负责说出口。

#### 4.2.4 状态与取数的归属（本轮最容易写错的一处）

分步之后子组件会随步骤切换**真实卸载**。这对纯展示组件无害，但对**自带取数与内部 ref 的**
`AiTradeTradabilityFields`（F-7）是致命的：

- 用户在第 5 步看到「ok」，回到第 2 步把周期改成 `1m`，然后直达第 6 步启动——探针**没有重跑**
  （组件已卸载），`tradabilityNeedsConfirm` 仍是 `false`，于是一个 98.6% 开仓意图会被否决的周期
  **一句提示都不会出现**。这是分步化引入的新失效方式，必须在设计层消灭，而不是靠"记得别这么用"。
- `prevLevel` 这个 `useRef` 随卸载丢失 ⇒「由非 infeasible 跃迁为 infeasible 时重置确认态」这条
  纪律失效 ⇒ 用户在 A 周期上确认过的风险被静默套用到 B 周期。

**做法**：把探针**上提到父组件的表单 hook**，用 `useQuery` 取代裸 `useEffect`：

```ts
// useAiTradeLaunchForm.ts
const tradabilityQ = useQuery({
  queryKey: ["aiTrade", "tradability", budgetInst, bar, disciplinePreset, stopLossPct.value],
  queryFn: () => aiTradeTradabilityApi.probe(budgetInst, bar, disciplinePreset, stopLossPct.value),
  enabled: open && !!budgetInst && !!bar,
  staleTime: 30_000,          // 步骤间往返不重复打后端
  retry: false,               // 失败 ⇒ 不阻碍任何操作（后端已保证 200 + unknown）
});
```

`AiTradeTradabilityFields` 随之降为**纯展示**：props 由 `data / loading / failed` 三个字段注入，
组件内不再有任何 `useEffect`、不再有 `prevLevel`。跃迁检测与 350ms 防抖一并上提到 hook。

**【v2 · P0-2】`tradabilityNeedsConfirm` 必须由 `useState` 改为派生值，`onNeedsConfirmChange`
这个 prop 整个删掉。** v1 只说了「上提取数」，没说这一句——而这正是分步化在这条链路上唯一会
真出事的地方：

- 今天 `tradabilityNeedsConfirm` 是**父组件的 `useState`**（`AiTradeLaunchModal.tsx:326`），它的
  **唯一写点**在子组件的一个 effect 里（`AiTradeTradabilityFields.tsx:129` 的
  `onNeedsConfirmChange?.(level === "infeasible")`）。
- 分步之后，一个从不访问第 5 步的用户使那个子组件**从不挂载** ⇒ 没有任何人写这个 state ⇒
  `tradabilityBlocked` 恒 `false`、CTA 文案恒为「确认并开始 AI 自动交易」。W-14 与 R-5 会直接失败。
- 更坏的收场是有人为了让用例变绿去改断言：那样一次真实资金启动就绕过了本轮唯一一次 fail-open
  确认——「判据取自不携带该信息的入参」，正是 RK-2 声称要消灭的那个形状本身。

故：

```ts
// useAiTradeLaunchForm.ts —— 三者全部是派生值，没有一个是 state
const tradabilityView = useMemo(() => describeTradability(tradabilityQ.data ?? null),
                                [tradabilityQ.data]);
const tradabilityNeedsConfirm = tradabilityView?.level === "infeasible";
const tradabilityBlocked = tradabilityNeedsConfirm && !tradabilityConfirmed;
```

`tradabilityConfirmed` 仍是 state（它是用户输入），其「由非 `infeasible` 跃迁到 `infeasible` 即
重置」的既有纪律照搬到 hook 里的一个 `useRef<string | null>` + 一个**依赖数组为
`[tradabilityView?.level]`** 的 effect——父组件全程不卸载，这个 `ref` 因而真的能跨步骤活下来
（这正是 v1 想要的效果，只是 v1 没有给它一个不依赖子组件挂载的写点）。
该 effect 是本文件里唯一被允许存在的第二个 effect，已在 §8.3 G-2 里逐条具名。

**【v2 · P1-4】防抖不引入新文件、不引入新依赖。** `useDebouncedValue` 在本仓**不存在**
（`grep -r useDebounce apps/web` 零命中），v1 直接引用了它而 §七 也没有它的落点。改为在
`useAiTradeLaunchForm.ts` 内写一个文件私有的 `useDebounced<T>(value, ms)`（一个 `useState` +
一个 `[value, ms]` 依赖的 `setTimeout` effect，卸载清理），只喂 `queryKey`、不喂任何提交体字段。
它是 G-2 允许的第三个 effect，同样已具名。

> 经验模式卡与情报卡（`AiTradeExperienceFields` / `AiTradeIntelFields`）**刻意不动**：它们的取数是
> 纯展示性的（库存条数、字数），既不喂任何判据、也不进提交体，重挂载重取一次是可接受的成本。
> 本轮**不为它们改写取数方式**——宁可少改一处，不改一处没核过必要性的。

#### 4.2.5 风险汇聚（不变式 I-1）

```ts
// apps/web/lib/aiTradeLaunchRisk.ts
export const AI_TRADE_LAUNCH_RISK_IDS = [
  "gearResolution",            // 仓位档位过粗 ⇒ 三层风控的缩减被量化吸附抹掉
  "tradabilityInfeasible",     // 可交易性预检判定当前周期几乎开不出单
  "profitGuardOff",            // 利润保护未装配（tone 随 unguardedHorizon() 升为 danger）
  "inertThresholds",           // 纪律预设里那一行硬止盈「显示了但不作数」
  "allocationOversubscribed",  // 独立上限模式下各标的上限合计 > 100%
  "budgetClamped",             // 填写的总预算被账户级上限夹回
  "membersTruncated",          // 左侧配置的标的数超过 AI_TRADE_MAX_SYMBOLS，已截断
  "deepWakeTooShort",          // 「极致」研判档 × 唤醒周期 < 6 分钟
  // 【v2 · P1-3】以下两条是评审在源码里搜出来的、v1 漏掉的既有非阻断警示。它们各自住在一个
  // **用户最可能整步跳过**的步骤里（第 4 步 / 第 2 步），恰恰是 I-1 最该覆盖的那两条。
  "experienceOff",             // 经验模式整关：本次会话既不读取也不沉淀（AiTradeExperienceFields.tsx:170）
  "wakeFloorRaised",           // 唤醒周期低于与目标持仓相干的下限，启动时会被服务端抬上去
                               // （lib/aiTradeHorizon.ts:279-287；仅 wakeHorizonDivisor > 0 时活）
] as const;

export interface AiTradeLaunchRisk {
  id: (typeof AI_TRADE_LAUNCH_RISK_IDS)[number];
  /** 渲染落点；`steps[0]` 同时是风险清单上「去处理」按钮的跳转目标。 */
  steps: AiTradeLaunchStepId[];
  tone: "warn" | "danger";
  title: string;
  detail: string;
  /** 需要一次显式勾选才能启动（仅 `tradabilityInfeasible` 与互锁武装时的 `profitGuardOff`）。 */
  requiresAck?: boolean;
}

export function collectRisks(model: AiTradeLaunchRiskInput): AiTradeLaunchRisk[];
```

**渲染规则（两处，且只有两处）**：

1. 每条风险在其 `steps` 列出的步骤上**就地内联**渲染（用户在那一步就该看见）。
2. 每条风险**无条件**在 `review` 步的风险清单里再渲染一次，带 `data-risk-id={id}`。

**I-1 的可证伪判据**：`collectRisks()` 产出的 `id` 集合，与 `review` 步实际渲染出的
`[data-risk-id]` 集合**逐元素相等**（W-13 / W-14 两条用例，其中 W-14 特意**从不访问**第 5 步）。

**【v2 · P1-3】I-1 的作用域是一张显式清单，不是一句「任何一条」。** 上面那条判据只能证明
「注册表里的都到了第 6 步」，证明不了「弹窗里所有非阻断警示都在注册表里」——后者没有任何机械
判据能覆盖（新警示可以出现在任何一个子组件的任何一行 JSX 里）。v1 的措辞是「任何一条」，而评审
在源码里当场搜出两条不在册的（F-17）。故本节改为**双向显式**：

- **在册的 10 条**：由 `AI_TRADE_LAUNCH_RISK_IDS` 冻结，走上面那条等值判据。
- **已知排除项（本轮明确不进风险清单，各附理由）**：
  - `AiTradeHorizonFields` 的「契约摘要」四行——它是**当前选择的说明**，不是警示，进了清单会把
    一份必然出现的常规文案永久钉在最后一屏，把真警示淹掉。
  - `AiTradeExperienceFields` 的「只读模式」提示（`value.write === false`）——它是用户**主动选择
    的一个正常档位**的回执，`--muted` 色，与 `experienceOff` 的 `--warn` 是两个不同的东西。
  - `AiTradeIntelFields` / `AiTradeDirectionFields` 内的档位说明文案——同上，说明而非警示。
- **实施纪律**：往任一 `Step*.tsx` 或其子组件里新增一处 `var(--warn)` 文本时，必须同时给出
  「进注册表」或「进上面这张排除清单」的判断。这条不由门禁守（守不住），由本节写明——
  **宁可把边界写死在文档里，也不要留一条自称全覆盖、实际只覆盖自己想得起来那几项的不变式。**

`deepWakeTooShort` 的 `steps = ["rhythm", "cognition"]`：它的**成因**在第 4 步（选了「极致」），
**修法**在第 2 步（把唤醒周期调大），故两步都渲染，跳转目标是 `rhythm`。这类跨步耦合正是分步化
最容易丢东西的地方，用 `steps: StepId[]` 一次性表达清楚，不留隐式约定。

#### 4.2.6 服务端 remedies 在向导里的落点

`errorRemedies`（422 随错误体带回的三条可执行出路）继续渲染在 `review` 步的错误横幅下方，行为
不变：`enable_profit_guard` 拨 `profitGuardEnabled`（第 5 步的状态）、`disable_horizon` 拨
`horizonEnabled`（第 2 步的状态）、`ops_override` 只作说明不作按钮。**新增一行就地回执**：点击后
在按钮下方渲染「已为你开启利润保护（第 5 步 · 纪律与护栏）」，并把对应步骤在轨上标为 `edited`
——按钮把一个用户看不见的步骤的开关拨过去，却不告诉他拨的是哪一格，是分步化带来的新暗箱。

---

### 4.3 轴 C：视觉与交互（美观 · 优雅 · 顶级）

- **布局**：弹窗宽度 `max-w-lg` → `max-w-3xl`。`≥md` 两栏：左侧 200px 步骤轨（`shrink-0`），
  右侧内容区（`min-w-0 flex-1`）；`<md` 轨折成顶部横向条。桌面端最小窗宽 860 下按此断点验证。
  **【v2 · P1-2】`role="tab"` 的 DOM 全局只渲染一次。** 响应式**只改同一棵树的 CSS**
  （容器 `flex-col md:flex-row`、轨 `w-full md:w-[200px]`、条目 `overflow-x-auto md:overflow-visible`），
  **不得**写成 `hidden md:flex` 的轨 + `md:hidden` 的第二份进度条那种两棵树的形态：前端用例跑在
  jsdom，媒体查询不求值（F-18），两棵树会同时存在于用例 DOM 里，`gotoStep` 的
  `findByRole("tab", { name })` 当场抛 `found multiple elements`——**79 条既有用例的迁移会全部撞上
  这一条**。同理，v1 提到的 `<details>` 折叠若采用，也只能包裹**那一棵**轨，不得复制一份标题出来。
  窄档下轨条目改为横向可滚（`overflow-x-auto` + 条目 `shrink-0`），摘要行在 `<md` 下隐藏
  （`hidden md:block`，纯文本、不带 `role`，不影响上面那条唯一性）。
- **步骤轨条目**（零折行契约 Type A）：行容器
  `flex items-center gap-2 whitespace-nowrap` + `data-nowrap="row"`；序号徽标 `shrink-0`
  （未访问＝空心数字，已访问无错＝空心勾，当前＝实心，有错＝叉，有警示＝三角）；标题与摘要
  **恰好一处** `min-w-0 truncate` + `title`。
  **【v2 · P0-1】这一处锚点必须同步登记进既有门禁，否则它当场判红。**
  `noWrapContract.source.test.ts` 的 G2 虽然按目录扫全仓，但判据是
  `expect(seen).toBe(TOTAL_ANCHORS)` 的**等值冻结**（现值 19，实测全仓恰 19 处 / 15 个文件，F-13）。
  故本轮**必须**改那个门禁文件（已补进 §7.3 第 31 项）：`MANAGED` 增一行
  `{ file: "Agent/launch/AiTradeLaunchStepRail.tsx", anchors: 1, marker: "AI_TRADE_LAUNCH_STEPS" }`、
  `expect(MANAGED).toHaveLength(25)` → `26`、`TOTAL_ANCHORS` `19` → `20`。
  **轨文件是全轮唯一允许出现 `data-nowrap` 的新文件**：六个 `Step*.tsx` 一律不带锚点（它们是内容区，
  文本本就该按 Type D 正常换行），这样 `20` 这个数才是可对账的。
- **进度**：轨顶「第 N 步 / 共 M 步」+ 一条 2px 进度条（`role="progressbar"` +
  `aria-valuenow/min/max`）。
- **配色**：一律走既有 token（`--bg / --surface / --surface-2 / --border / --text / --muted /
  --accent / --accent-soft / --accent-ink / --warn / --warn-soft / --short-ink`）。
  禁止面色当文字色（`text-[var(--accent)]` 等，`contrastTokens.source.test.ts` ② 已冻结）；
  `var(--warn)` 不带内联回退（`warnToken.source.test.ts` 已冻结）。当前步用 `--accent-soft` 底 +
  `--accent-ink` 字，左缘 2px `--accent` 竖线。
- **动效**：切步 120ms `opacity 0→1` + `translateY(4px→0)`，纯 CSS、不引库；
  `@media (prefers-reduced-motion: reduce)` 下整体关闭。**不做**高度动画（会在异步数据落地时抖）。
- **密度**：沿用弹窗既有的 `text-[11px] / text-xs` 密度与 `gap-3` 节奏——本轮是重组，不是换皮；
  换一套字号会让 79 条既有用例的文案断言与视觉回归同时发生，两者混在一起没人能定位。
- **底部操作条**：左「上一步」（首步隐藏），右「下一步」/「确认并开始 AI 自动交易」，中间可选
  「跳到确认」（N-10）。全部 `whitespace-nowrap`。
- **首焦点**：给 `Modal` 传 `initialFocusRef` 指向内容区标题，避免焦点默认落在步骤轨第一个 tab 上
  （用户一进来就在"导航"里，而不是在"内容"里）。

---

### 4.4 轴 D：文件拆分与 800 行红线

`AiTradeLaunchModal.tsx` 今天 1937 行。拆分后每个文件的预算（含注释）：

```
components/Agent/AiTradeLaunchModal.tsx            ≈ 220   壳 + 步骤编排 + 底部操作条 + 对外类型
components/Agent/launch/useAiTradeLaunchForm.ts    ≈ 520   全部 useState / 派生 / payload / 单一 [open] effect / 提升后的探针
components/Agent/launch/AiTradeLaunchStepRail.tsx  ≈ 130
components/Agent/launch/StepScope.tsx              ≈ 260
components/Agent/launch/StepRhythm.tsx             ≈ 180
components/Agent/launch/StepCapital.tsx            ≈ 190
components/Agent/launch/StepCognition.tsx          ≈ 110
components/Agent/launch/StepGuards.tsx             ≈ 150
components/Agent/launch/StepReview.tsx             ≈ 270
components/Agent/launch/DisciplinePresetFields.tsx ≈ 150   （从主文件原样搬出，逻辑零改）
components/Agent/launch/LaunchPrimitives.tsx       ≈ 140   Field / Row / DateField / ModeTab / AllocModeTab
lib/aiTradeLaunchSteps.ts                          ≈ 180   步骤表 / 归属表 / 摘要（纯函数）
lib/aiTradeLaunchRisk.ts                           ≈ 210   风险注册表（v2：10 条）/ collectRisks / unguardedHorizon / effectiveMinHoldSeconds（纯函数）
lib/aiTradeLaunchFormat.ts                         ≈ 170   主文件 :1561-1707 的**全部**纯函数，逐条零改
```

> **【v2 · P2-1】`aiTradeLaunchFormat.ts` 的清单是「主文件 :1561-1707 之间的全部纯函数」，不是
> 一张记忆里的名单。** v1 只列了 `fmtOcoDual / ocoPlaceholder / guardPreview / pctToRaw* /
> tiersForPreset / matchTierPreset / horizonOverrides / seedHorizonCustom`，漏了同一区间里同类的
> 七个：`fromLocalInput` / `toLocalInput` / `fmtOcoPct` / `rawPctToFraction` / `rawToNumber` /
> `numToRaw` / `secondsToHoursRaw`。判据用区间而不用名单，是因为名单会漏而区间不会。

**对外接口零破坏**：`AiTradeLaunchMode`、`AiTradeLaunchOpts` 与默认导出继续从
`components/Agent/AiTradeLaunchModal.tsx` 导出（必要时 re-export），`AiTradeToggle.tsx` 的
import 与 props 逐字节不变。

---

## 五、接口设计

### 5.1 `GET /api/ai-trade/horizon-profiles`（加法字段，唯一的 wire 变更）

```jsonc
{
  "enabled": true,
  "defaultProfile": "intraday_swing",
  // …既有键全部不变…
  "requireProfitExitWithHorizon": false   // ← 新增
}
```

| 字段 | 类型 | 语义 |
|---|---|---|
| `requireProfitExitWithHorizon` | `boolean` | 运维互锁总闸的**只读**回执。`false`（本轮默认）⇒ 前端不阻断，只披露；`true` ⇒ 前端恢复二次确认，且服务端会 422。 |

**三处必须同步改，漏一处就是静默失效**（F-9）：

1. `services/quant/app/ai_auto/hold_horizon_profiles.py::profiles_wire` — 加一行
   `"requireProfitExitWithHorizon": bool(settings.ai_trade_require_profit_exit_with_horizon)`。
2. `services/quant/app/schemas.py::AiTradeHorizonProfilesOut` — **显式声明**
   `requireProfitExitWithHorizon: bool = False`。该端点声明了 `response_model`，未声明的键会被
   FastAPI 静默剔除；而前端规则是「键缺席 ⇒ 按 `false` 处理」，两条一叠就是「互锁武装了却永远
   拦不住」，且没有任何手造 wire 的用例会红。
3. `apps/web/lib/aiTradeTypes.ts::AiTradeHorizonProfilesResponse` — 加
   `requireProfitExitWithHorizon?: boolean;`（可选：老后端不发该键 ⇒ `undefined` ⇒ 前端按 `false`
   处理 ⇒ 不阻断，与本轮默认同向，是安全的降级方向）。

**为什么挂在 `horizon-profiles` 而不是新开端点**：该互锁的判据的另一半（`min_hold_s`）本来就在这张
表里，弹窗为了算 `effectiveMinHoldSeconds` 必须拉它；再开一个端点等于让前端为一个布尔值多发一次
请求，还多一个可能失败的取数点。且这张表已有 `turnoverGateEnabled` / `backstopCheckEnabled` /
`wakeHorizonDivisor` 三个**同类的只读运维标志**，本字段是它们的自然第四个。

### 5.2 不变的接口

- `POST /api/ai-trade/start`：请求体、回执、错误码、`remedies` 三条——**逐字节不变**。
- `GET /api/ai-trade/discipline-presets`、`GET /api/ai-trade/tradability`：不变。
- 前端 `AiTradeLaunchOpts`：不变（这是「本轮不改任何交易语义」的支点）。

---

## 六、数据模型

### 6.1 服务端

**无 DB 迁移、无新表、无新列、无新 `Settings` 键。** 唯一变化是一个既有布尔配置项的默认值：

| 键 | 今天 | 本轮 |
|---|---|---|
| `ai_trade_require_profit_exit_with_horizon` | `True` | `False` |

### 6.2 前端内存形状（新增，全部是派生态，不落 `localStorage`）

```ts
/** 向导位置：以 StepId 存储（N-9）；进那个既有的 [open] reset effect（N-8）。 */
const [stepId, setStepId] = useState<AiTradeLaunchStepId>("scope");

/** 步骤轨条目的渲染态（纯派生，不入 state）。 */
interface AiTradeLaunchStepView {
  id: AiTradeLaunchStepId;
  title: string;
  index: number;             // 1-based，供「第 N 步 / 共 M 步」
  state: "default" | "edited" | "warn" | "error";
  summary: string;           // stepSummary(model, id)
  error: string | null;      // stepError(model, id)
}
```

**持久化边界不变**：`AiTradeToggle` 侧的 `aiTradeConfig`（上次所选参数的回填源）逐键不变——
向导位置**刻意不持久化**，每次打开都从第 1 步开始（与 `repairMode` 的既有姿态同源：一次启动是一次
完整的授权，不该从上次停下的地方接着走）。

---

## 七、文件 / 模块变更清单

### 7.1 服务端（5 个源文件 + 3 个用例文件）

| # | 文件 | 动作 | 一句话意图 |
|---|---|---|---|
| 1 | `services/quant/app/config.py` | 改 | `ai_trade_require_profit_exit_with_horizon` 默认 `True → False`，注释改述为「默认不拦，机构可武装」 |
| 2 | `.env.example` | 改 | 同键登记值 `true → false`，并改写其上方注释段落 |
| 3 | `services/quant/app/ai_auto/hold_horizon_profiles.py` | 改 | `profiles_wire` 下发 `requireProfitExitWithHorizon` |
| 4 | `services/quant/app/schemas.py` | 改 | `AiTradeHorizonProfilesOut` 显式声明该键（否则 `response_model` 静默剔除） |
| 5 | `services/quant/app/api/ai_auto.py` | 改（仅 docstring） | 说明互锁默认已关、落点仍必须留在端点内 |
| 6 | `services/quant/tests/test_ai_trade_loss_liveness_core.py` | 改 | 默认值断言翻转为 `False` 并移入「只建不开」那一组；新增 `profiles_wire` 携带该键的断言 |
| 7 | `services/quant/tests/test_ai_trade_loss_liveness_session.py` | 改 | `G-A1` / `G-A8` 显式置 `True` 才走 422 分支；**新增 `G-A2b`**：不传该键的 `Settings()` 下同一份 body 得 200 |
| 8 | `services/quant/tests/test_ai_trade_hold_horizon.py` | 改 | 新增**真端点**用例断言新键没被 `response_model` 吞掉（照抄 `test_ai_trade_backstop_repair_session.py:440` 的既有范式） |

### 7.2 前端新增（14 个）

| # | 文件 | 一句话意图 |
|---|---|---|
| 9 | `apps/web/lib/aiTradeLaunchSteps.ts` | 步骤表 / `stepsFor` / `STEP_ERROR_OWNERS` / `stepError` / `stepSummary`（纯函数） |
| 10 | `apps/web/lib/aiTradeLaunchSteps.test.ts` | U-1 / U-2：步骤序列冻结 + 归属表全覆盖无重叠 |
| 11 | `apps/web/lib/aiTradeLaunchRisk.ts` | 风险注册表 / `collectRisks` / `unguardedHorizon` / `effectiveMinHoldSeconds` |
| 12 | `apps/web/lib/aiTradeLaunchRisk.test.ts` | U-3 / U-4 / U-5：**十条**风险的触发表（v2 补录 `experienceOff` / `wakeFloorRaised`）+ 与后端真值表逐行同构 |
| 13 | `apps/web/lib/aiTradeLaunchFormat.ts` | 从主文件搬出的纯格式化 helper（逻辑零改） |
| 14 | `apps/web/components/Agent/launch/useAiTradeLaunchForm.ts` | 全部受控状态 / 派生 / payload 装配 / **唯一**的 `[open]` reset effect / 上提后的可交易性探针 |
| 15 | `apps/web/components/Agent/launch/AiTradeLaunchStepRail.tsx` | 步骤轨（tablist + 状态点 + 摘要 + 进度条） |
| 16 | `apps/web/components/Agent/launch/StepScope.tsx` | 第 1 步 |
| 17 | `apps/web/components/Agent/launch/StepRhythm.tsx` | 第 2 步 |
| 18 | `apps/web/components/Agent/launch/StepCapital.tsx` | 第 3 步 |
| 19 | `apps/web/components/Agent/launch/StepCognition.tsx` | 第 4 步 |
| 20 | `apps/web/components/Agent/launch/StepGuards.tsx` | 第 5 步 |
| 21 | `apps/web/components/Agent/launch/StepReview.tsx` | 第 6 步（逐步摘要 + 风险清单 + 二次确认 + CTA 区） |
| 22 | `apps/web/components/Agent/launch/DisciplinePresetFields.tsx` | 从主文件原样搬出（逻辑零改） |
| 23 | `apps/web/components/Agent/launch/LaunchPrimitives.tsx` | `Field` / `Row` / `DateField` / `ModeTab` / `AllocModeTab` |
| 24 | `apps/web/components/Agent/launch/launchWizard.source.test.ts` | 门禁 G-1（行数）/ G-2（唯一 reset effect）/ G-3（标题单一真源） |

### 7.3 前端改写（7 个 —— v2 因 P0-1 增列第 31 项）

| # | 文件 | 一句话意图 |
|---|---|---|
| 25 | `apps/web/components/Agent/AiTradeLaunchModal.tsx` | 1937 行 → ≈220 行的壳：调 hook、渲染轨 + 当前步 + 底部操作条；对外类型与默认导出不变 |
| 26 | `apps/web/components/Agent/AiTradeTradabilityFields.tsx` | 取数与跃迁检测上提到 hook，降为纯展示（props 收 `data / loading / failed`） |
| 27 | `apps/web/components/Agent/AiTradeTradabilityFields.test.tsx` | 由 mock 取数改为 props 注入；三条纪律的断言逐字保留 |
| 28 | `apps/web/lib/aiTradeTypes.ts` | `AiTradeHorizonProfilesResponse` 加 `requireProfitExitWithHorizon?: boolean` |
| 29 | `apps/web/components/Agent/AiTradeLaunchModal.test.tsx` | 79 条既有用例按附录 A 加导航前缀（**断言语义一律不改**）+ 新增 W-1…W-19 |
| 30 | `apps/web/components/Agent/AiTradeToggle.test.tsx` | 4 处（:161 / :190 / :216 / :246-248）加导航前缀；:246 那条**跨两步**，见附录 A 的迁移规则 |
| 31 | `apps/web/components/ui/noWrapContract.source.test.ts` | **【v2 · P0-1】必改**：`MANAGED` 增 `Agent/launch/AiTradeLaunchStepRail.tsx`（`anchors: 1`）、`toHaveLength(25→26)`、`TOTAL_ANCHORS(19→20)`。不改则步骤轨那一处 `data-nowrap="row"` 会让既有 G2 当场判红（等值冻结，不是「只要不引入违例」） |

---

## 八、测试与验收标准

### 8.1 纯函数用例（vitest，`apps/web/lib/*.test.ts`）

| id | 断言 |
|---|---|
| U-1 | `stepsFor("live")` 与 `stepsFor("backtest")` 的 id 序列**冻结**为 `[scope, rhythm, capital, cognition, guards, review]` |
| U-2 | **【v2 · P0-3 改写】** `STEP_ERROR_OWNERS` 的并集 === `READY_INPUTS ∪ BANNER_INPUTS`（前者是 `ready` 表达式含 `baseValid` 读到的全部校验量，后者是 `firstError` 读到的全部）；且**无任何一项归属两步**（全覆盖 + 无重叠）。用例须显式点名 `directionError` / `trendError` 归属 `guards`——它们只在 `READY_INPUTS` 里，是 v1 参照集漏掉的那两项 |
| U-3 | **十条**风险各自的触发 / 不触发断面（每条至少一正一反）。**【v2 · P1-3】** 其中 `experienceOff` / `wakeFloorRaised` 是评审补录的两条：前者判据是 `experience.enabled === false`，后者是 `wakeHorizonDivisor > 0 && wakeSeconds < targetHoldMinSeconds / divisor`（除数为 `0` 时**恒不触发**——那时说一句「会被抬到 N 分钟」就是一句假话，`lib/aiTradeHorizon.ts:279-281` 已有成文） |
| U-4 | `collectRisks()` 只会产出 `AI_TRADE_LAUNCH_RISK_IDS` 中的 id（防止实现方偷偷加一条清单外的风险） |
| U-5 | `unguardedHorizon()` 真值表与后端 `test_g_a9_unguarded_horizon_truth_table_is_frozen` **逐行同构**（含 `interlockArmed=false ⇒ 恒 false`、`horizon 装了但 minHold=0 ⇒ false`） |
| U-6 | `effectiveMinHoldSeconds()`：非 custom 取服务端下发值；custom 空串 ⇒ `0`；预设表未落地 ⇒ `0` |
| U-7 | `stepSummary()` 六步各产出非空且不含 `undefined` / `NaN` 字面量 |
| U-8 | **【v2 · P2-2】** `effectiveMinHoldSeconds()` 在 `minHoldSeconds: null` 的条目上返回 `0`（`profile_wire` 对 `target_hold_min_s is None` 的条目下发 `null`）；且 `horizonQ.isPending === true` 时**先于**模式分支返回 `0`（此时 `horizonProfileId` 会回落成字面量 `"custom"`，先分支就会去读一个与服务端无关的用户输入） |

### 8.2 组件用例（vitest + testing-library）

**迁移范式**（写进测试文件顶部的两个 helper，79 条既有用例按附录 A 机械套用）：

```ts
async function gotoStep(id: AiTradeLaunchStepId) {
  fireEvent.click(await screen.findByRole("tab", { name: new RegExp(STEP_TITLE[id]) }));
  return screen.findByRole("heading", { level: 3, name: new RegExp(STEP_TITLE[id]) });
}
async function submitLaunch(name: RegExp = /确认并开始 AI 自动交易/) {
  await gotoStep("review");
  fireEvent.click(await screen.findByRole("button", { name }));
}
```

新增用例：

| id | 断言 |
|---|---|
| W-1 | 打开即停在第 1 步，且**第 3 步的字段不在文档中**（`queryByLabelText("杠杆（×）")` 为 `null`）——这是「真分步」的可证伪判据：若实现用 `hidden` 保留 DOM，本条必红 |
| W-2 | 步骤轨渲染 6 项、`role="tablist"`、当前项 `aria-selected="true"` |
| W-3 | 自由跳转：直接点第 5 步即可达（不要求逐步走过） |
| W-4 | 本步错误挡住「下一步」：第 1 步清空策略选择 ⇒ 按钮 `disabled` 且同屏出现 `strategyError` 原文 |
| W-5 | 跨步错误**不**挡本步：第 3 步填非法杠杆后，回第 1 步的「下一步」仍可点 |
| W-6 | 状态点：有阻断错误的步 `data-state="error"`，有非阻断警示的步 `data-state="warn"`（断言属性，不断言颜色） |
| W-7 | 摘要：成员改为 3 个后，轨上第 1 步摘要含「3 个标的」 |
| W-8 | 往返不丢输入：第 3 步改杠杆 → 去第 1 步 → 回第 3 步，值仍在 |
| W-9 | 重开弹窗回到第 1 步（`stepId` 确实进了 `[open]` effect） |
| W-10 | 复发守门：切步后推进 2 秒假时钟，第 2 步的自定义周期输入**不被冲回默认**（照抄既有 `[G-7]` 范式） |
| W-11 | 切到回测段：第 1 步出现区间字段、第 4 步不再渲染情报卡、第 6 步无真资金知情勾选 |
| W-12 | 第 6 步逐组摘要各带一个「修改」按钮，点击跳回对应步 |
| W-13 | **风险零丢失（就地）**：档位设为 0.5 ⇒ 第 3 步出现 `gear-resolution-warning`；跳到第 6 步 ⇒ 同一条以 `data-risk-id="gearResolution"` 出现在风险清单 |
| W-14 | **风险零丢失（从未访问）**：探针夹具置 `infeasible`，**全程不访问第 5 步**，直达第 6 步 ⇒ 风险清单含 `data-risk-id="tradabilityInfeasible"` 且 CTA 文案为「仍要启动 AI 自动交易」 |
| W-15 | **轴 A 主判据**：`requireProfitExitWithHorizon: false`（默认夹具）+ 取消利润保护 + 周期已装配 ⇒ 第 6 步**不要求**任何额外勾选，`确认并开始 AI 自动交易` 可点且 `onConfirm` 收到 `profitGuard: {enabled:false}` |
| W-16 | **轴 A 回退路径**：夹具置 `true` ⇒ 恢复二次确认（未勾选时点击不提交 + 同屏给出 `UNGUARDED_CONFIRM_REQUIRED_TEXT`） |
| W-17 | `requireProfitExitWithHorizon` 键缺席（老后端）⇒ 按 `false` 处理，不阻断 |
| W-18 | 键盘：`Alt+ArrowRight` 前进一步，且焦点落在内容区标题上 |
| W-19 | `aria-live` 区域文本含「第 2 步，共 6 步」；**【v2 · P1-1】** 在 `aiTradeAcknowledged` **为 `false`**（即刷新后的首启夹具，`ready === false`）且 1–5 步无输入错误时，「跳到确认」按钮**仍然出现**并直达第 6 步。夹具刻意不预置勾选——预置了这条就恒绿且守不住任何东西 |
| W-20 | **【v2 · P0-2】探针不随步骤卸载而失效**：探针夹具置 `infeasible`，**全程不访问第 5 步**，在第 2 步把周期改成一个仍判 `infeasible` 的值后直达第 6 步 ⇒ CTA 文案为「仍要启动 AI 自动交易」、未勾确认点击**不提交**。本条与 W-14 的区别是它**在探针入参变化之后**取值，专守「`tradabilityNeedsConfirm` 被写成了 state」这一种实现 |
| W-21 | **【v2 · P0-3】`ready` 假而横幅为空时不留死路**：在第 5 步填一个非法的逆势置信度（只产生 `directionError`，不进 `firstError`）⇒ 第 6 步启动按钮 `disabled`，**且**同屏出现「第 5 步（纪律与护栏）有未解决的输入错误」与可点的「去处理」，点击后落在第 5 步 |
| W-22 | **【v2 · P1-2】轨只有一棵**：`screen.getAllByRole("tab")` 恰好 6 个（守住「窄档另渲染一份进度条」这种两棵树的实现——它会让全部 `gotoStep` 抛 `found multiple elements`） |
| W-23 | **【v2 · P1-3】两条补录风险真的会到第 6 步**：取消经验模式勾选 ⇒ 第 4 步出现 `ai-trade-experience-off-note`，且**不访问第 4 步**的另一条路径下，第 6 步风险清单含 `data-risk-id="experienceOff"`；`wakeHorizonDivisor` 夹具置 `24` 且唤醒周期低于下限 ⇒ 第 6 步含 `data-risk-id="wakeFloorRaised"` |

### 8.3 源码门禁（`launchWizard.source.test.ts`）

| id | 断言 | 为什么必须是源码门禁 |
|---|---|---|
| G-1 | `AiTradeLaunchModal.tsx` 与 `components/Agent/launch/**` 下每个文件 ≤ 800 行 | CLAUDE.md 红线；行数回归不会让任何渲染用例变红 |
| G-2 | **【v2 · P1-5 改写】** 三条可机械判定的子判据：(a) `useAiTradeLaunchForm.ts` 中依赖数组恰为 `[open]` 的 `useEffect` **恰好 1 个**；(b) 该文件里 `useEffect(` 出现次数 **≤ 3**；(c) 除 `[open]` 那一个外，任何 `useEffect` 的依赖数组里**都不得出现 `open`**。允许存在的另外两个逐个具名：**可交易性 level 跃迁重置**（依赖 `[tradabilityView?.level]`）与**防抖**（依赖 `[value, ms]`），两者都不写回任何「默认值」 | v1 的措辞（禁「第二个把受控 state 写回默认值的 effect」）会把本轮自己的跃迁重置判成违例——那条重置做的正是 `setTradabilityConfirmed(false)`。一条会把正确实现判红的门禁，下一个人会直接删掉它。复发根因（`ai-trade-modal-revert-recurrence`）的真正形状是「**又一个依赖不稳的 effect 把正在编辑的表单冲回默认**」，(a)+(c) 才是它的判据 |
| G-3 | `Step*.tsx` 与 `AiTradeLaunchStepRail.tsx` 中不出现步骤标题的字面量（唯一真源是 `AI_TRADE_LAUNCH_STEPS`） | 轨上写「资金与仓位」、内容区写「资金设置」，是分步 UI 最常见的漂移，且没有任何断言会红 |
| G-4 | **【v2 · P0-1 改写】** 不是「自动覆盖」：`noWrapContract.source.test.ts` 的 G2 按目录扫全仓，但判据是 `expect(seen).toBe(TOTAL_ANCHORS)` 的**等值冻结**（现值 19，实测全仓恰 19 处）。本轮**必须**把该文件改成 `MANAGED` 26 项 / `TOTAL_ANCHORS = 20`，并登记步骤轨那唯一一处 `data-nowrap="row"`（§7.3 第 31 项）。反向验证：把轨上的锚点删掉 ⇒ G2 与 G3 双红 | 「一条永远不会红的门禁比没有更坏」。v1 的写法两头落空：加锚点 ⇒ 既有门禁当场红且无处可改；不加锚点 ⇒ 新文件上根本没有契约冻结 |
| G-5 | 新增文件不出现 `text-[var(--short\|long\|accent)]`、不出现带内联回退的 `var(--warn, …)` | 既有 `contrastTokens` / `warnToken` 门禁按目录扫描，自动覆盖。**这一条的「自动覆盖」经评审核实成立**（`contrastTokens.source.test.ts:23-47` 与 `warnToken.source.test.ts:23-42` 都是纯目录递归，判据是 `offenders === []` 而非计数冻结）——与 G-4 的情形正相反，两者不可类推 |
| G-6 | **【v2 · P0-2】** `useAiTradeLaunchForm.ts` 里 `tradabilityNeedsConfirm` **不得**以 `useState` / `useReducer` 形式出现（源码正则：该标识符所在行不含 `useState`），且 `AiTradeTradabilityFields.tsx` 里不再出现 `onNeedsConfirmChange` | 它一旦是 state，唯一的写点就只能在某个组件的 effect 里；而那个组件在六步里有五步是卸载的。渲染用例只有走「从不访问第 5 步」这一条路径才会红（W-20），源码判据在写下去的那一刻就红 |

### 8.4 后端用例

| id | 断言 |
|---|---|
| B-1 | `Settings().ai_trade_require_profit_exit_with_horizon is False`（改 `test_ai_trade_loss_liveness_core.py:480`） |
| B-2 | **新增 `G-A2b`**：不传该键的 `Settings(live_trading_enabled=True, ai_trade_enabled=True)` 下，`_body()`（周期锁 + 无保护层）得 **200**——这是本轮需求本体的唯一可证伪判据 |
| B-3 | `G-A1` 显式置 `True` 后仍得 422 + `remedies` 恰好三条（回退路径真的存在） |
| B-4 | `G-A8` 显式置 `True` 后仍在**同一个用例里**同时断言「start 422」与「冷续跑 200」（判据不得挪回装配链，这条不因默认值改变而放松） |
| B-5 | `profiles_wire(Settings())["requireProfitExitWithHorizon"] is False` |
| B-6 | **真端点**（`AsyncClient` 打 `GET /api/ai-trade/horizon-profiles`）回执含该键 ⇒ 证明 `response_model` 没吞它 |
| B-7 | `.env.example` 双向登记门禁（`test_g_env_...`）继续绿（它只查键存在，不查值） |

### 8.5 验收标准（产品级，可由人手动复现）

| id | 场景 | 期望 |
|---|---|---|
| R-1 | 全新环境（不改任何 env），打开弹窗 → 第 5 步取消「利润保护」→ 直达第 6 步 → 启动 | **成功启动**；第 6 步风险清单里有一条 `danger` 条目说明「浮盈没有自动兑现通道」 |
| R-2 | 置 `AI_TRADE_REQUIRE_PROFIT_EXIT_WITH_HORIZON=true` 重启后端，重复 R-1 | 第 6 步要求勾选二次确认；不勾即拦；服务端 422 时三条 remedies 逐条可点；**【v2 · P1-7】** 风险条目末尾如实说出「本机构已开启运维互锁」，错误横幅第三条 remedy 下方列出该键的三个落点 |
| R-2b | **【v2 · P1-7】灰度实证**：不改进程 env，只在桌面端的 `agent.env` 里写 `AI_TRADE_REQUIRE_PROFIT_EXIT_WITH_HORIZON=true`，重启桌面端后重复 R-1 | 表现与 R-2 完全一致（证明 `desktop/src/lib/config.js:80` 的前缀透传确实是第三个落点，也证明「发行说明说默认放行、这台机器却仍在拦」这件事对用户是**可解释**的） |
| R-3 | 打开弹窗 | 停在第 1 步；一屏之内看得完；轨上 6 步各带一行现值摘要 |
| R-4 | **在一个刚刷新过的页面上**（`aiTradeAcknowledged` 已复位为 `false`），只在第 1 步选好标的与策略，直接点「跳到确认」 | 【v2 · P1-1】按钮**存在**且可达第 6 步；在第 6 步勾上真实资金知情后成功启动（其余五步全部凭默认值）。刷新这一步是判据的一部分——v1 的 `ready` 判据下这个按钮在此场景恒不渲染 |
| R-5 | 第 2 步把周期改成 `1m`（预检判 `infeasible`），**不访问第 5 步**，直达第 6 步 | 风险清单里出现可交易性警示；CTA 文案为「仍要启动 AI 自动交易」；不勾确认点击不提交 |
| R-6 | 第 3 步把档位设为 50% | 第 3 步就地出现分辨率警示；第 6 步风险清单同时有该条 |
| R-7 | 在第 3 步改杠杆 → 回第 1 步 → 再回第 3 步 | 杠杆值仍在（步骤往返不丢输入） |
| R-8 | 关闭弹窗再打开 | 回到第 1 步，全部字段回到默认 / 上次偏好（与今天一致） |
| R-9 | 桌面端最小窗宽（860）下打开弹窗 | 轨折为顶部横向进度条；无横向溢出；无文字折行（Type A 契约） |
| R-10 | 键盘全程操作：`Tab` 进轨 → `←/→` 换步 → `Alt+→` 前进 → 最后一步 `Enter` 启动 | 全程可达；每次切步读屏播报「第 N 步，共 6 步：<标题>」 |
| R-11 | 回测段全流程 | 第 1 步含区间、第 2 步含决策间隔与计划轮数、第 4 步不含情报卡、第 6 步无真资金勾选；`onConfirm` 的 `mode==="backtest"` 提交体逐字段与今天相同 |
| R-12 | 全量回归 | `pytest`（`services/quant`）与 `vitest`（`apps/web` 内运行）除仓库既有红外全绿；`tsc --noEmit` 无新增错误 |

---

## 九、风险与缓解

| id | 风险 | 影响 | 缓解 |
|---|---|---|---|
| RK-1 | **分步把警示藏起来** —— 用户永远翻不到第 5 步，于是「这个周期几乎开不出单」一句都不会说 | 高：直接导致「开着、在跑、从不开单」的既有事故形态复发 | 不变式 I-1（§4.2.5）+ 冻结的**十条**风险注册表 + 显式的「已知排除项」清单 + W-13 / W-14 / W-23（三者都**特意不访问**产地步骤） |
| RK-2 | **探针随步骤卸载而失效**（F-7）：卸载后改周期不重跑，`prevLevel` 丢失导致确认态跨周期泄漏 | 高：判据取自不携带该信息的入参——本仓库反复付过学费的形状 | 探针上提到 hook 用 `useQuery`（`queryKey` 含 `instId/bar/preset/slPct`），子组件降为纯展示；W-14 直接守它 |
| RK-3 | **第二个 reset effect 复发**：新增的 `stepId` 被写进一个新 effect ⇒ 输入约 1 秒后被冲回默认 | 高：`ai-trade-modal-revert-recurrence` 已复发过一次 | N-8 明文 + G-2 源码门禁 + W-10 假时钟用例 |
| RK-4 | **79 条既有用例大面积改动**，改动中混入语义漂移 | 中：一次"顺手改断言"就会把一条守着真实事故的用例悄悄弱化 | 附录 A 的字段→步骤映射表 + 两个 helper；迁移规则是**只加导航前缀，一律不改断言**；评审时以 `git diff` 中"断言行的净变更为 0"为判据 |
| RK-5 | **默认放行让致命组合更容易被选中** | 中：这是需求明确要求的取舍，但不能静默 | 风险清单永久渲染 `profitGuardOff`（关掉即出现，与是否有周期锁无关）；启动回执 / 统计页既有的 `inert` 披露一律不动；ops 可一键武装（R-2） |
| RK-6 | **`response_model` 静默吞新键**（F-9） | 中：互锁武装了却永远拦不住，且没有任何用例会红 | B-6 真端点用例（不是手造 wire） |
| RK-7 | **拆文件破坏 `AiTradeToggle.tsx` 的 import** | 中：`tsc` 会红，但可能在最后一刻才发现 | `AiTradeLaunchOpts` / `AiTradeLaunchMode` / 默认导出继续从原路径导出；R-12 的 `tsc --noEmit` 是硬判据 |
| RK-8 | **步骤位置用数字下标** ⇒ 两模式间错位 | 低 | N-9 明文：以 `StepId` 存储；切模式后收敛到最近可见步 |
| RK-9 | **动效引入布局抖动** | 低 | 只做 `opacity` / `transform`，不做高度动画；`prefers-reduced-motion` 下整体关闭 |
| RK-10 | **窄窗（桌面端 860）下两栏轨挤压中间内容** | 低 | `<md` 折成顶部横向进度条；轨条目遵守 Type A 单吸收点；R-9 手动验收 |
| RK-11 | 并发兄弟节点同时在改 `apps/web/components/Agent/**` | 低 | 本轮新增文件全部落在新目录 `components/Agent/launch/`；改写集中在 4 个文件，冲突面可控 |
| RK-12 | **【v2】等值冻结型门禁被当成「自动覆盖」** —— 加一处锚点即判红一条与本轮无关的门禁，而实现者会以为是自己写错了 | 中：最省事的"修法"是把锚点删掉，于是新文件的排版契约永久失去冻结 | §7.3 第 31 项显式把门禁文件列进变更清单并给出新旧数字；G-4 改写为「必须改它」而非「它自动覆盖」；反向验证（删锚点 ⇒ 双红）写进 G-4 |
| RK-13 | **【v2】把「派生事实」写成 state** —— 分步之后写点随组件卸载而消失，值停在最后一次挂载时的样子 | 高：本轮它的具体后果是一次真实资金启动绕过 fail-open 确认 | §4.2.4 明文改为派生 + G-6 源码门禁 + W-20（**在探针入参变化之后**取值，与 W-14 互补） |
| RK-14 | **【v2】响应式折叠渲染出第二棵带 `role` 的树** —— jsdom 不求值媒体查询，全部导航用例撞 `found multiple elements` | 中：79 条用例的迁移会在第一条上就卡住，且错误信息指向 testing-library 而不是布局 | §4.3 明文「`role="tab"` 全局只渲染一次，响应式只改同一棵树的 CSS」+ W-22 计数断言 |
| RK-15 | **【v2】I-1 自称全覆盖而实际只覆盖注册表** —— 新加的警示落在第 4 / 第 2 步这类最可能被整步跳过的地方 | 中：这正是本轮不变式想消灭的形态，却发生在不变式自己的边界上 | 注册表补两条（`experienceOff` / `wakeFloorRaised`）+ §4.2.5 新增显式「已知排除项」清单 + 实施纪律：新增任一 `var(--warn)` 文本时必须二选一归位 |

---

## 十、回滚方案

两轴**可各自独立回滚**：

- **轴 A**：置 `AI_TRADE_REQUIRE_PROFIT_EXIT_WITH_HORIZON=true` 并重启后端 ⇒ 服务端恢复 422、
  前端因 wire 回 `true` 而恢复二次确认。**无需回退任何代码**。若要连默认值一并回退：
  `config.py` 与 `.env.example` 各一行。
- **轴 B**：`git revert` 本轮对 `components/Agent/launch/`、`AiTradeLaunchModal.tsx`、
  `lib/aiTradeLaunch*.ts` 的改动即回到今天的单屏弹窗。因为 `AiTradeLaunchOpts` 与
  `POST /ai-trade/start` 逐字段不变，回滚不涉及任何数据 / 会话兼容问题（含正在运行的会话与
  冷续跑：它们读的是 `start_wire` 原文，与弹窗形态无关）。
  **注意**：轴 B 回滚会连带撤掉 §7.3 第 31 项对 `noWrapContract.source.test.ts` 的登记，
  两者必须同进同出（`TOTAL_ANCHORS` 留在 20 而锚点没了 ⇒ 门禁恒红）。

### 10.3 已有环境的迁移（【v2 · P1-7】默认值翻转**不会**追溯生效）

翻默认值只改「没有显式配置时取什么」。本产品有三个地方会给出显式配置，其中两个在用户手里：

| 落点 | 谁会有 | 翻转后的表现 |
|---|---|---|
| 进程环境变量 | 机构侧部署 | 显式值优先，**照旧生效** |
| `services/quant/.env` | 开发期 | 同上 |
| 桌面端 `agent.env` | 终端用户 | 同上——`desktop/src/lib/config.js:80` 按 `AI_TRADE_` 前缀整段透传给后端子进程 |

`.env.example` 至今写着 `=true`，凡照它抄过一份的环境，在本轮之后**照旧被 422**，而发行说明会
写着「已放行」。这是本轮唯一一处「产品承诺与用户实际所见不一致」的可能来源，处理方式是
**如实说出来，而不是让默认值去追溯**：

1. 前端的判据一律取自 wire 上的 `requireProfitExitWithHorizon`（§4.1.2），**从不**取自「本轮默认
   是 false」这句话——所以这类环境里，二次确认与 422 会自洽地一起出现，不会出现「前端放行、后端
   422」的假门禁。
2. 互锁武装时的风险条目与 remedies 落点提示（§4.1.2 末尾）负责让用户知道**是哪一个键、在哪里**。
3. 发行说明须写「**新环境**默认放行；已显式配置过该键的环境不受影响，改法见上表」——不写这一句，
   工单会以「说好的修复没生效」回来。

---

## 附录 A：字段 → 步骤映射表（既有 79 条用例的机械迁移依据）

迁移规则（**【v2 · P1-8】按步骤计，不是按用例计**）：在一条用例里，**每一个步骤**的首个查询目标
之前各插一次 `await gotoStep("<该步骤>")`；点击 CTA 的语句替换为 `await submitLaunch()`。
**断言本身一律不改。**

> v1 写的是「用例中**首次**出现下表任一查询目标之前插入」——单数。跨两步的用例真实存在，照单数
> 施工就会漏插，而漏插之后最省事的"修法"就是删掉那条断言（RK-4 所怕的正是这个）。范例
> （`AiTradeToggle.test.tsx:246-248`，同一条用例先动第 5 步、再动第 3 步）：
>
> ```ts
> await gotoStep("guards");                                     // ← 新增
> fireEvent.change(screen.getByLabelText("允许回吐（%）"), { target: { value: "35" } });
> await gotoStep("capital");                                    // ← 新增（同一条用例的第二次）
> fireEvent.change(screen.getByLabelText("单仓止损%（如 0.05＝5%）"), { target: { value: "0.03" } });
> await submitLaunch();                                         // ← 替换原来的 findByRole + click
> ```
>
> 自查判据（RK-4 的落点）：迁移提交的 `git diff` 里，**断言行（`expect(`）的净变更必须为 0**；
> 新增行只允许是 `gotoStep` / `submitLaunch` 两种。

| 查询目标（既有用例里的 `getByLabelText` / `getByRole` 名） | 步骤 |
|---|---|
| `实盘` / `回测` 段控按钮 | `scope` |
| 策略清单复选框（`均线策略` / `动量策略`） | `scope` |
| `<INST> 百分比`、`移除 <INST>`、`新增自动交易标的`、`添加`、`均分`、`均分份额`、`独立上限` | `scope` |
| 回测标的输入、`回测起始`、`回测结束` | `scope` |
| `时间级别` | `rhythm` |
| 交易周期模式单选（`日内波段` / `趋势持仓` / `自定义`）、`ai-trade-horizon-contract`、自愈模式（`仅提醒` / `自动补挂`）、自定义高级参数 | `rhythm` |
| `Agent 唤醒周期（分钟）` | `rhythm` |
| `上下文刷新轮数` | `rhythm` |
| `决策间隔（根/次）`、`共识门槛（可选）`、`计划决策轮数` | `rhythm` |
| `总预算（USDT）` / `初始资金（USDT）`、`杠杆（×）`、`总止盈（USDT）` / `会话止盈（USDT）`、`总止损（USDT）` / `会话止损（USDT）`、`单仓止损%（如 0.05＝5%）`、`单仓止盈%（如 0.10＝10%）` | `capital` |
| `可用余额` / `生效总预算` / `单笔名义上限` / `单仓兜底 OCO（交易所侧）` 等 Row | `capital` |
| `仓位操作档位`、`gear-resolution-warning` | `capital` |
| `研判深度` | `cognition` |
| 经验模式卡（`AiTradeExperienceFields` 的勾选与字段） | `cognition` |
| 市场情报卡（`AiTradeIntelFields` 的勾选与字段） | `cognition` |
| `ai-discipline-preset`、`ai-discipline-threshold-table`、`查看这一档的阈值`、四张预设卡 | `guards` |
| `基础策略（铁律 · 默认开启）`、`基础策略均线周期`、`基础策略带宽倍数`、`基础策略容差`、`基础策略方向性模式` | `guards` |
| `AiTradeDirectionFields` 的逆势 / 趋势字段 | `guards` |
| `ai-tradability-pick-bar`、`仍要在该周期上启动` | `guards`（勾选框在第 5 步；风险清单里的**同义**勾选在第 6 步，见下条） |
| `允许回吐（%）`、`武装阈（%）`、`保本武装（%）`、`最短持有（秒）`、阶梯预设、`ATR 下界` / `ATR 上界` / `冷却（秒）`、利润保护勾选 | `guards` |
| `ai-profit-guard-unguarded-confirm`、`我已知晓这是真实资金 AI 自动交易`、`ai-start-remedies` / `ai-start-remedy-*`、错误横幅、CTA | `review` |

> 注：可交易性的二次确认（`仍要在该周期上启动`）**同时**出现在第 5 步（就地）与第 6 步（风险
> 清单里）。两处是同一个受控 state 的两个渲染点，勾任意一处即生效——本轮不得因此产生两个
> `state`，也不得让第 6 步只显示不可勾选（那就又是一次「显示了但不作数」）。

---

## 附录 B：实施顺序建议

1. **轴 A 先落地并单独可验证**（8 个文件，含用例）——它与轴 B 无耦合，先合入可以立刻解除用户当前
   被拦住的痛点，也让轴 B 的 W-15 / W-16 有真实的 wire 可依。
2. **抽 hook 与纯函数**（#9 ~ #14、#26、#28）——此时 UI 仍是单屏，79 条用例应当**全绿且零改动**。
   这一步是整轮最重要的安全网：如果它让任何一条既有用例变红，说明抽取过程中改变了行为，
   必须先解决再往下走。
3. **接入向导 UI**（#15 ~ #25）+ 用例迁移（#29、#30）+ 新增 W-1…W-19。
4. **源码门禁**（#24、#31）最后加，并对每一条做一次**反向验证**（故意破坏 ⇒ 确认它真的会红），
   否则一条永远不会红的门禁比没有更坏。**【v2】** 第 31 项（`noWrapContract` 的锚点登记）是个例外：
   它必须与 §4.3 的锚点**同一次提交**落地，否则中间态是红的。

---

## 评审结论

### 有条件通过

方案的两条轴都成立，且规模是对的：轴 A 只改一个默认值而不删代码路径（回滚成本是一个环境变量而
不是一次 revert），轴 B 不动任何一条交易语义、`AiTradeLaunchOpts` 与 `POST /ai-trade/start` 逐字段
冻结。§二 的 12 条现状取证经**逐条复核全部属实**，附录 A 的字段→步骤映射把 79 条既有用例的迁移
变成了机械操作而不是重写——这两件事让本轮的风险面收敛在「新代码写得对不对」，而不是「会不会碰坏
已经在跑的东西」。核心不变式 I-1（风险零丢失）抓住了分步化真正的危险，而不是分步化本身。

评审共提 **3 条 P0、8 条 P1、6 条 P2**，P0 与 P1 **已全部在正文就地修毕**并各自补了可证伪判据
（新增 U-8、W-20 ~ W-23、G-6，改写 U-2、G-2、G-4、R-2/R-2b、R-4，新增变更清单第 31 项与 §10.3）。
三条 P0 有一个共同形状，值得在实施时一直带着：**「我以为有人在守的那件事，其实没有写点」**
——G-4 以为既有门禁自动覆盖（它是等值冻结，加锚点反而判红）、`tradabilityNeedsConfirm` 以为有人
会写它（唯一写点在一个五步都不挂载的子组件里）、U-2 以为 `firstError` 就是全集（它少了两项）。
本仓库反复付学费的就是这一种。

**放行条件（实施节点须逐条落实，缺一即视为未完成）：**

- **C1**（P0-1）`noWrapContract.source.test.ts` 与步骤轨的锚点**同一次提交**落地：`MANAGED` 26 项、
  `TOTAL_ANCHORS = 20`，并做一次反向验证（删锚点 ⇒ G2 与 G3 双红）。六个 `Step*.tsx` 一律不带锚点。
- **C2**（P0-2）`tradabilityNeedsConfirm` / `tradabilityBlocked` 落地为**派生值**，
  `onNeedsConfirmChange` 这个 prop 删除；G-6 源码门禁与 W-20 同轮落地，且 W-20 必须做过一次反向
  验证（把它改回 state ⇒ W-20 红）。
- **C3**（P0-3）归属表覆盖 `READY_INPUTS ∪ BANNER_INPUTS`；`review` 步的兜底行落地；
  `firstError` 表达式**逐字节不改**。
- **C4**（P1-1）「跳到确认」的判据是 `inputStepsClean`，不是 `ready`；R-4 / W-19 的夹具**不预置**
  `aiTradeAcknowledged`。
- **C5**（P1-2）`role="tab"` 的 DOM 全局只渲染一次，响应式只改同一棵树的 CSS；W-22 计数断言落地。
- **C6**（P1-3）注册表落地为 10 条，§4.2.5 的「已知排除项」清单写进代码注释（`aiTradeLaunchRisk.ts`
  文件头），W-23 落地。
- **C7**（P1-5）G-2 按三条子判据实现，并把两个被允许的 effect 逐个具名写进门禁的失败信息里
  ——一条门禁红了却说不清允许什么，等于逼下一个人删掉它。
- **C8**（P1-7）发行说明必须写「已显式配置过该键的环境不受影响」；R-2b 作为验收的一档实跑一次。
- **C9**（P1-8）用例迁移按**步骤**计而非按用例计；提交前自查「`expect(` 行净变更为 0」。
- **C10** 实施前记一份全量红基线（`pytest` / `vitest` / `tsc --noEmit`），R-12 的「除既有红外全绿」
  才是可判定的。工作树上有并发兄弟节点的在制改动，基线不能省。

六条 P2 不阻断实施，但建议在同一轮顺手处理（成本都在一行到一段之间），尤其 P2-5：后端文案里的
`**粗体**` 在 JSX 显字面星号这件事，本仓库已经复发过四次。


---

## 实施过程发现的方案缺陷

> 由实施节点（Subtask #2）在落地过程中记录。每一条都给出**方案原文怎么说的 / 事实是什么 /
> 本轮采取的修正做法**；下面 8 条全部已在代码里按修正做法落地，没有一条是"静默偏离"。

### I-1（P0）`useAiTradeLaunchForm.ts` 按方案的职责划分**必然超出 800 行红线**，与 G-1 自相矛盾

- **方案原文**：§4.4 给 `useAiTradeLaunchForm.ts` 的预算是「≈ 520 行：全部 useState / 派生 /
  payload / 单一 `[open]` effect / 提升后的探针」；§3.1 的 G-6 与 §8.3 的 G-1 又要求
  「新增与改写的**每一个**前端文件 ≤ 800 行」。
- **事实**：把那五件事按方案写完（且只保留必要注释：全文 922 行里注释仅 63 行、空行 37 行），
  实测 **923 行**。超出的部分不是注释冗余，而是那五件事本身的体量——单是受控状态声明就有 150 行、
  复位体 70 行、派生与校验 200 行。
- **修正做法**：新增**一个**方案未列的文件
  `apps/web/components/Agent/launch/useAiTradeLaunchState.ts`（279 行），把**受控状态声明 +
  那份一次性的复位体**整段搬过去（`seed()`），`useAiTradeLaunchForm.ts` 收到 772 行。
  这是本轮唯一一处"多建一个文件"，选它是因为它是这五件事里**最机械、最没有判断**的一段，
  切出来对全部既有判据零影响：
  - G-2 的落点不变——那个依赖收敛为 `[open]` 的 effect 仍在 `useAiTradeLaunchForm.ts` 里，
    且 `state.seed()` 全仓只有它一个调用点；
  - 新增两条子判据把这次切分本身钉死：**G-2 (d)** 断言 `useAiTradeLaunchState.ts` 里
    `useEffect(` 出现 **0 次**、且 `state.seed()` 的调用点恰好 1 个。少了这一条，下一个人
    完全可以在状态层里补一个 effect 去"顺手"复位某个新字段，而 G-2 (a)(b)(c) 只扫 form 文件、
    **看不见它**——`ai-trade-modal-revert-recurrence` 会原地复活。

### I-2（P0）`test_g_a6b` 会因轴 B 的拆分而判红，而方案的变更清单里没有它

- **方案原文**：§7.1 的服务端用例清单只列了 3 个文件（core / session / hold_horizon）。
- **事实**：`services/quant/tests/test_ai_trade_loss_liveness_core.py::
  test_g_a6b_frontend_inert_predicate_mirrors_the_backend_one` 是一条**跨车道源码扫描**门禁，
  它按字面在 `components/Agent/AiTradeLaunchModal.tsx` 里找
  `const inertThresholdKeys = profitGuardEnabled ? []`。轴 B 把这段现算表达式随全部受控派生
  搬进了 hook，该门禁当场判红——而它守的是「前端删除线与后端真正失效的那一行是同一行」。
- **修正做法**：把扫描落点改为 `launch/useAiTradeLaunchForm.ts`，**判据一个字不改**，
  并在 docstring 里写明「改的只是它住在哪个文件里」。

### I-3（P1）后果清单末句在互锁默认关之后变成一句**假话**，而 `ProfitGuardFields.tsx` 不在变更清单里

- **方案原文**：§7.3 的前端改写清单是 4 个文件，不含 `ProfitGuardFields.tsx`；§4.1.2 只说把
  「阻断」降级为「披露」。
- **事实**：`ProfitGuardFields.tsx:143` 那句「若同时开着「持仓周期」模式，**服务端会拒绝启动**」
  在本轮默认（互锁关）下对绝大多数环境**为假**。一句在多数环境里为假的警告比不给更坏：
  它会让用户以为自己被拦住了，而真正拦住他的是别的东西。
- **修正做法**：给该组件加一个只读 prop `interlockArmed`（取自 wire 的
  `requireProfitExitWithHorizon`），那一句随它如实变文：武装 ⇒ 照旧说"会拒绝启动"；
  未武装 ⇒ 改口为"不会拦住你，但这份代价会如实列在最后一步的风险清单里"。
  两条分支各有一条用例（W-16b / W-16c）。**组件内仍然零阈值字面量**（`G-A5` 继续绿）。

### I-4（P1）模式段控（实盘 / 回测）不能住在第 1 步里

- **方案原文**：§4.2.1 的分区表把「模式段控（:818-836）」划给 `scope`；附录 A 也把
  「`实盘` / `回测` 段控按钮」映射到 `scope`。
- **事实**：模式决定的是**整条向导的形状**（回测段第 1 步多出区间、第 4 步整卡消失、
  第 6 步没有真资金勾选），把它埋进第 1 步意味着"要换模式得先跳回第 1 步"，而它本身
  不是第 1 步在回答的那个问题（"对谁交易"）。
- **修正做法**：段控留在**壳里**（标题栏之下、步骤轨之上），六步之外、恒可达。附录 A 的迁移
  因此对这两个按钮**不插 `gotoStep`**（迁移脚本里显式列为 NEUTRAL）。这是一次有意的偏离，
  没有任何断言因此被弱化。

### I-5（P1）每标的预算预览与总预算输入被分到了两步，方案未提示这条跨步耦合

- **事实**：§4.2.1 把「多标的分配器」划给 `scope`、把「六个金额输入」划给 `capital`，而分配器
  里那一列「预算预览」是 `previewBudget × allocationPct` 的派生——用户在第 3 步改总预算，
  预览在第 1 步变。三条既有用例（AC-1 / AC-2 / AC-3）正好压在这条缝上。
- **修正做法**：按方案的划分执行（不搬控件），迁移时把这三条用例写成"改完去第 1 步看预览"，
  并在用例里写明理由。**没有改任何断言**。相邻性问题记录在此，留给下一轮判断是否值得
  在第 3 步补一行只读的分配预览。

### I-6（P2）`noWrapContract` 的 G3 按 `split()` 计数，注释里写出那个属性名会把计数顶成 2

- **事实**：G3 的实现是 `src.split("data-nowrap=").length - 1`，它不区分 JSX 属性与注释文本。
  步骤轨的文件头注释原本逐字写着那条契约（"行容器 … + `data-nowrap="row"`"），于是
  「本文件恰好 1 处锚点」的登记当场判红为 2。
- **修正做法**：轨文件的注释改为不写出该属性的字面量，并就地写明**为什么**不写
  （"这条规则解释自己时反而违反自己"）。同一形状在 `openTagAt` 的注释里已有先例。

### I-7（P2）W-1 ~ W-23 的落点：方案的新增文件清单里没有给它们一个文件

- **事实**：§8.2 列了 23 条新增组件用例，§7.2 的新增文件清单却只有
  `launchWizard.source.test.ts` 一个测试文件，意味着 23 条要塞进已有 1231 行的
  `AiTradeLaunchModal.test.tsx`（迁移后 1414 行），合并后约 1760 行。
- **修正做法**：新建 `components/Agent/launch/AiTradeLaunchWizard.test.tsx` 承载 W 组
  （29 条，含 W-16b/c/d 与 W-19b 四条补充），与 `AiTradeLaunchModal.test.tsx` 分工写在文件头：
  后者守**交易语义与提交体**（迁移时只加导航前缀、断言零改），前者守**分步化本身**新引入的
  失效方式。

### I-8（P2）`AiTradeTradabilityFields.test.tsx` 的「跃迁重置」用例在纯展示化之后无处安放

- **事实**：§7.3 第 27 项要求"由 mock 取数改为 props 注入；三条纪律的断言逐字保留"，但第三条
  纪律（level 跃迁 ⇒ 重置确认态）的执行者已经搬到 hook 里，子组件**没有**这个行为可断言。
- **修正做法**：该用例改写为它的**对偶**——断言本组件**不再**自己撤销确认态（props 变了就只是
  重新渲染）；真正的跃迁语义由 W-20 在弹窗层面守（它特意**在探针入参变化之后**取值，
  且全程不访问第 5 步）。另加一条"数据未落地且未失败 ⇒ 整块缺席"补齐纯展示的边界。

### I-9（P1 · 实施中自伤并当场修复）JSX children 位置的块注释会**原样印给用户**，而没有任何判据看得见

- **事实**：五个 `Step*.tsx` 里那条「已就地说出口的风险不再重复渲染」的说明一度写成 `/** … */`
  并落在 `return (` 之后的 children 位置。JSX 在那里**不把它当注释**——它是一段文本节点。
  `tsc --noEmit` 绿、428 条组件用例全绿，而弹窗第 3 步的面板上真的印着一段源码注释。
  这与本仓库反复付学费的形状是同一个：**一个没有断言的输出**。
- **修正做法**：改为 `{/* … */}`，并补一条渲染守卫：把六步各渲染一遍，断言
  `document.body.textContent` 里不含 `/*`、`*/` 与字面量 `**`（后者是"后端文案里的 Markdown
  强调符在 JSX 显星号"那条已复发四次的老坑的同一张网）。该守卫做过一次反向验证：把注释改回
  块注释 ⇒ 当场判红。

### I-10（P2）§4.2.5 渲染规则 1「就地内联」与既有控件的自渲染会**说两遍**

- **事实**：注册表里有六条风险在它的产地步骤上**本来就有**一处就地渲染（档位分辨率警示、
  预算夹紧回执、分配器合计横幅、截断横幅、经验整关提示、唤醒下限一致性提示、预检建议、
  阈值表删除线）。若按规则 1 字面再渲染一条通用条目，同一句话会在同一屏上出现两次。
- **修正做法**：每个 `Step*.tsx` 顶部显式声明一个 `ALREADY_INLINE` 集合并写明理由，通用条目
  只兜底清单外的那些（第 2/4 步的 `deepWakeTooShort`、第 5 步的 `profitGuardOff`）。
  「二选一、别两边都不管」写进注释——这与 §4.2.5 的"新增 `var(--warn)` 文本时必须二选一归位"
  是同一条纪律的两个落点。

---

## 终审记录（Subtask #3 · 代码评审与提交）

逐条对照 §七 的 31 项变更清单与放行条件 C1 ~ C10，**全部落地**（C1 的锚点登记、C2 的派生化 +
`onNeedsConfirmChange` 删除、C3 的兜底行、C4 的 `inputStepsClean`、C5 的单棵轨、C6 的十条注册表 +
文件头排除清单、C7 的三条子判据、C8 的 `.env.example` 不追溯说明、C9 的迁移自查、C10 的红基线）。

**拆分保真度**逐符号核对过，不是抽查：搬出的 16 个纯函数与 5 个排版原子在**忽略空白后与原文逐字
相等**（唯一的 `horizonOverrides` 差异是为 80 列加了一对花括号）；`aria-label` / `data-testid` /
`label=` / `placeholder=` 四张全量清单在拆分前后**零丢失**（各自 `comm -23` 为空）。

回归：`vitest` 249 文件 / 2874 用例全绿，`tsc --noEmit` 干净，`ruff` 干净，`pytest` 与基线同名同数。

### 终审改掉的四处

| # | 问题 | 判断 |
|---|---|---|
| 1 | **风险清单在回测段说假话（P1）**：`experienceOff` 与 `allocationOversubscribed` 的判据都不看 `mode`，而它们的**产地控件在回测段根本不渲染**——经验卡在回测下走的是「回测不读取也不写入经验库」那条分支（`ai-trade-experience-off-note` 整段不可达），分配器只在实盘段渲染。于是一个上次关过经验模式、或用过「独立上限」的用户切到回测，第 6 步会出现一条第 4 / 第 1 步从没说过、且在回测下本就为假的警示，「去处理」按钮把他送到一个什么都改不了的步骤 | 两条各加 `mode === "live"` 判据并写明理由，各补一条回测反例用例。这与 §4.2.5 给 `wakeFloorRaised` 定的纪律是同一条：**算不出或不成立的事，一个字都不说**；也与 `budgetClamped` / `membersTruncated` 在 hook 里已有的 mode 收敛一致 |
| 2 | **步骤轨里一个有写点没读点的导出**：`AI_TRADE_LAUNCH_STEP_COUNT` 的注释写着「供壳组件校验索引」，而壳组件从不 import 它 | 删掉该常量与随之空转的 `AI_TRADE_LAUNCH_STEPS` import；`noWrapContract` 里那一行的 `marker` 同步改为 `railKeyToIndex`（轨自己的载重符号，不会随注释漂走）。一句声称了不存在的消费者的注释，比没有注释更坏 |
| 3 | **搬迁中丢掉的三条「为什么」**：`settingsQ` 的 live-only、`horizonQ` 的按 `bar` 取、`disciplineQ` 的 queryKey 不带 `bar`，三条判据的理由在搬进 hook 时没跟过来（其余搬迁都是逐字的） | 就地补回。这三句解释的都是「为什么依赖数组长这样」，正是 CLAUDE.md §四 要求保留的那一类 |
| 4 | `test_ai_trade_hold_horizon.py` 的模块 docstring 写着「纯函数、无网络、无 broker」，而 `B-6` 在文件末尾装配了真 app 打真端点 | docstring 补一句例外并写明理由（手造 wire 的用例在 `response_model` 静默剔除这条缺陷上永远是绿的） |

### 判定为不改的三处

- **`AiTradeToggle.test.tsx` 的 `expect(` 净变更为 −3**：四处 `expect(confirm).not.toBeDisabled()`
  被收进 `submitLaunch()` 这一个 helper，每个调用点仍各跑一次——是合并，不是弱化（C9 自查通过）。
- **`AiTradeLaunchModal.test.tsx` 删掉的那一条 `expect(onConfirm).not.toHaveBeenCalled()`**：它守的
  正是轴 A 要撤掉的那次拦截，同处补了「风险披露恒在」的两条断言（W-15）。**这是本轮唯一一次
  有意的断言语义变更**，不是迁移漂移。
- **I-5 的跨步耦合**（第 3 步改总预算、预览在第 1 步变）：按方案划分执行，实施节点已记录在案，
  留给下一轮判断是否值得在第 3 步补一行只读分配预览——本轮不改，避免在终审里引入新面。
