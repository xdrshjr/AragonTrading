# 信任边界与信号真实性加固（trust-boundary-and-signal-integrity）

> 方案设计文档 **v2**（v1 = 方案架构节点；v2 = 设计评审节点就地整改）· 迭代 3/3
> 上游：`docs/plans/stability-hardening-audit/spec.md`（第 1 轮，已合入 `a8ed650`）、
> `docs/plans/resilience-and-hci-hardening/spec.md`（第 2 轮，已合入 `f48a714`）
> 本节点**只产出/修改本文件**，不写实现代码、不改 `docs/plans/trust-boundary-and-signal-integrity/` 以外的任何文件、不执行 `git commit`。

---

## 评审记录（Review Notes · v1 → v2）

### 0. 评审方法与本轮独立复核

评审节点**不接受 v1 的任何数字**，全部重新读源码 / 重新实测。本轮评审自己跑出来的：

| 复核项 | 评审节点实测 | 与 v1 的关系 |
|---|---|---|
| D1 跨源预检 | `preflight 200` / `ACAO: *` / `allow-methods: DELETE,GET,HEAD,OPTIONS,PATCH,POST,PUT` / `allow-headers: content-type` / `middleware: ['CORSMiddleware']` / 8 条 `/api/trading/*` | **逐字复现**，成立 |
| WCAG 全表（明暗 × 6 色 × 3 底 + 三个 ink 候选 + `--on-accent` + 按钮） | 与 §2.1(b) **逐位吻合**（含 `--muted` 达标、`--accent`/`--long` 比 `--short` 更差） | 成立 |
| 用量计数 | `text-[var(--short\|long\|accent)]` = **301 处 / 116 文件**（`short 168 / accent 103 / long 30`）；`bg-[var(--accent)]` = **23 处 / 21 文件**，其中同行带 `text-white` **7 处** | 成立 |
| WS 路由数 | `@router.websocket` = **9** | 成立 |
| starlette | `cors.py:76` `if scope["type"] != "http": ... return`；`applications.py:132` `user_middleware.insert(0, ...)`（后加者在外） | 成立，v1 的中间件装配顺序判断正确 |
| 外键 | `models.py` `ForeignKey` **8 处**、`ondelete` **0 处**；`_configure_sqlite` 只设 `busy_timeout` + `journal_mode` | 成立 |
| 台账 | `executor.py:123` `list[dict]`，append 在 310/517/560/925，零裁剪 | 成立 |
| G1 | `npx tsc --noEmit` → **exit 0**（评审节点本轮亲跑） | 成立 |
| `deque` 可行性 | `deque(maxlen=5)[-2:]` → **`TypeError: sequence index must be integer, not 'slice'`**（实测） | **推翻 v1 的 D5 第 2 步**，见 P0-2 |
| `fetch` 调用点 | `apps/web/lib` 下 **20 个** `fetch(` 分布在 **18 个文件**（`aiTradeHorizonApi.ts` 独占 3 个、无共享包装、抛裸 `Error`） | **修正 v1 的「18 个模块各加一行」**，见 P1-7 |

**七条缺陷判断（D1–D7）全部成立，无一条被推翻。** 问题出在修法上：v1 有三处修法**自身携带了它要消灭的那个缺陷**，
另有五处修法在本仓的既有约束下不可执行或不完整。以下 17 条按严重度排列，P0/P1 已在正文就地整改。

### P0

**P0-1 · D1 放行 `Origin: null`，等于给整套信任边界留了一个三行 HTML 就能走通的正门**（§4.1）

v1 写「本设计**放行** `null` 但记 `logger.info`」，理由是「`file://` 页面与部分 sandboxed iframe 会发 `null`」。
后半句正是问题：**带 `sandbox` 且不含 `allow-same-origin` 的 iframe，其源是不透明源，请求一律带 `Origin: null`**，
而这个 iframe 可以由**任意网页**当场创建：

```html
<iframe sandbox="allow-scripts" srcdoc="
  <script>fetch('http://127.0.0.1:18760/api/trading/close-all',
    {method:'POST',headers:{'Content-Type':'application/json'},body:'{}'})</script>">
```

这条请求携带 `Origin: null` → v1 的 `is_allowed_origin` 返回 `True` → 直达 `/api/trading/close-all`。
D1 自己论证过的那句「CORS 只藏响应、不拦执行」在这里原样成立，**OriginGuard 这一层被自己放过了**。
WS 侧更糟：WS **完全没有 CORS**，响应可读，所以同一个 sandboxed iframe 可以订阅 9 条流里的任意一条，
把用户的实盘会话、成交、持仓**读走**。OWASP 对 `Origin: null` 的既有结论是「等同于通配符，绝不可信」。

整改：`Origin: null` **一律拒绝**（HTTP 403 / WS 1008）。`null` 是**浏览器**发的，而威胁模型正是浏览器；
「缺失 `Origin`」才是「非浏览器」的可靠判据（`fetch`/XHR 的非 GET/HEAD 请求与 WS 握手必带 `Origin`），
那一条保持放行不变。代价是 `file://` 本地调试页调不通本机 API——写进 README 与 §十，并按 v1 自己的要求
用 `npm run verify:dist` 兜一遍。§二/§三/§4.1/§6/§8/§九/§十 已同步。

**P0-2 · D5 第 2 步的 `deque` 会在两条实时快照路径上直接抛 `TypeError`（已实测）**（§三 D5 / §4.4 / §七）

`app/wire_caps.py:25` 的 `tail_trades` 实现是 `list(trades[-cap:])`，而 **`collections.deque` 不支持切片**：

```
>>> deque([1,2,3], maxlen=5)[-2:]
TypeError: sequence index must be integer, not 'slice'
```

`tail_trades(self.executor.trades)` 的调用点是 `live_auto/session.py:467` 与 `ai_auto/session.py:2021`
——**两条会话的快照下发主路径**。把 `self.trades` 换成 `deque` 会让实时/AI 自动交易的每一次快照下发抛异常。
第二处：`ai_auto/resume.py:279` `executor.trades = continuity["trades"]` 直接赋一个普通 `list`，
**静默把 `maxlen` 丢掉**——恢复之后封顶不复存在，而没有任何东西会红。

整改：**放弃 `deque`，容器类型保持 `list[dict]` 不变**，在 `_record_trade` 内部按块裁剪。
这样 `tail_trades`、`resume` 赋值、类型标注、任何 `json` 序列化路径**全部零改动**——D5 的目标（内存护栏）
一样达成，而外部契约面积从「四处已知 + N 处未知」缩到 0。§三 D5、§4.4、§七、§5.5 已改写。

**P0-3 · D1 的 `ALLOWED_ORIGINS` 在打包桌面端根本传不进后端，一旦白名单不匹配即砖化且用户无从补救**（§4.1 / §六 / §五）

`desktop/src/lib/config.js` 的 `backendEnv` 只透传五个前缀：
`AGENT_ | LIVE_ | AI_TRADE_ | DISCOVERY_ | OKX_`。`ALLOWED_ORIGINS` 一个都不沾，
于是 prod（PyInstaller 后端**不读 `.env`，只读进程 env**）下 `userData/agent.env` 里写什么都无效。
R1 的缓解「`ALLOWED_ORIGINS` 可覆盖」在真实交付形态下**是空的**：Origin 白名单一旦与实际入口不符，
症状是「点什么都 403」，而用户**没有任何一条路**能把它改回来。

这不是新坑，是**第三次**踩同一个坑——该文件的注释自陈：
「DISCOVERY_ 为必改透传……漏改即所有 `DISCOVERY_` env 静默失效」「OKX_ 为必改透传……本仓库已为它栽过两次」。

整改：配置项改名 `SECURITY_ALLOWED_ORIGINS`（`Settings.security_allowed_origins`），
`backendEnv` 白名单加 `SECURITY_` 前缀，并按 `desktop/scripts/config.test.mjs` 里 `DISCOVERY_*` / `OKX_LINK_*`
两条既有用例的**同一范式**补一条。文件计划新增 `desktop/src/lib/config.js`、`desktop/scripts/config.test.mjs`。

### P1

**P1-4 · D4 的对话框栈若挂在 `[open, onClose]` 那个 effect 上，缺陷会原样复活，而固化用例照样绿**（§三 D4）

`ui/index.tsx` 里注册 `keydown` 的是 `useEffect(..., [open, onClose])`——**依赖里有 `onClose`**。
v1 说「`Modal` 在 `open` 的 effect 里 push 自己」，但没有指明是哪一个 effect；挂到 keydown 那个上就会：
`onClose` 身份一变 → cleanup 把自己 `splice` 掉 → 重新 `push` 到**栈顶**。
实证调用链：`FactorLab.tsx:301` 写的是 `onClose={() => setGroupModalOpen(false)}`（内联箭头），
`SymbolGroupModal.tsx:124` 把它原样交给 `<Modal onClose={onClose}>`——**外层 Modal 的 `onClose` 每次
FactorLab 重渲染都换新身份**。于是内层 `ConfirmDialog` 开着的时候，只要 FactorLab 因为任何状态/流事件重渲染一次，
外层就被重新推回栈顶，Esc 又开始关外层。更糟的是这条复活**依赖重渲染时机**，而 §三 D4 要固化的那条
`Modal.test.tsx` 用例是静态渲染，**它会一直是绿的**。

整改：栈的 push/splice 必须落在**独立的 `useEffect(..., [open])`** 里（`open` 是布尔，身份天然稳定），
与 keydown effect 分开；并补一条「父组件重渲染后栈顶不变」的回归用例。§三 D4 已改写。

**P1-5 · 暗色 `--short-ink` 在自己的配对底 `--accent-soft` 上只有 4.41，触自己立的 G9；且 G9 的「配对底」集合从未定义**（§三 D2 / §8.1）

实测：`--short-ink`(暗 `#d97a62`) on `--accent-soft`(暗 `#3a2c25`) = **4.41 < 4.5**。
唯一的真实落点是 `ui/index.tsx:36` —— `variant === "danger"` 写着
`bg-transparent text-[var(--short)] hover:bg-[var(--accent-soft)]`，即**危险按钮 hover 态**。
本仓已有先例把这件事钉成规矩：`app/globals.source.test.ts` 里那条
「`--warn` 是为 AA 而生的 token，它必须在**自己配套的 `--warn-soft` 底色**上也达标」
就是第 2 轮把 `#9a5a0d`（自配对 4.46）换成 `#8f5209` 的产物。v1 在同一个位置重犯。

同时 G9 只写「对**各自配对底** ≥ 4.5」，而"配对底"是哪些从未定义——一条判据不可判定的门禁不是门禁。

整改两件事：
1. 危险按钮 hover 底 `--accent-soft` → `--surface-2`（`--short-ink` 上 明 **5.73** / 暗 **4.74**，双双达标），
   与 `variant="ghost"` 的 `hover:bg-[var(--surface-2)]` 同一写法；顺带修掉「破坏性按钮的 hover 底是品牌赭」这处语义错配。
   三个 ink 的取值**一个不动**，暗色文字取值仍等于原面色（P1-9 要保的那张验证网因此完整保留）。
2. G9 的配对矩阵**显式列出**：三个 ink × {`--bg`,`--surface`,`--surface-2`,`--accent-soft`}，
   外加 `--on-accent` × `--accent`、`--warn` × `--warn-soft`（沿用既有断言）。整改后全 24 格实测最低 **4.61**。

**P1-6 · D5 往 continuity 载荷里加三个持久化键，与 `resume.py` 明写的不变式正面冲突，而且完全不必要**（§三 D5 / §七 / §九 R8）

`ai_auto/resume.py:433 rebuild_continuity` 的 docstring 原文：
「**派生量绝不落库**：落两份必然漂移，而这里漂移的是真实资金的盈亏显示。」
它从 DB 的**全量 `Trade` 行**重建 `trades`（`select(Trade).where(Trade.run_id==run_id)`，无裁剪），
所以三个计数器**全都是纯派生量**：

```
trade_count        = len(continuity["trades"])
open_count         = sum(1 for t in trades if t["side"] in ("long","short"))
realized_pnl_total = continuity["realizedPnlUsd"]   # 早就有了，local_net_view 同一条规则
```

v1 却要把它们写进会话状态 JSON，再为「老载荷缺键」设计一套 `or 0` 兜底 + 「UI 必须标注为『本次恢复后』」
的降级口径（§七 + R8）。这是**给一个不存在的问题造了一个真的缺陷**：既违反该模块自陈的不变式，
又主动让恢复后的统计变得不准，还得在 UI 上为这份不准写一句解释。

整改：`resume` 走 `executor.restore_trades(trades)`（一次赋值 + 派生三个计数器 + 施加封顶），
§七的三个新键删除、§九 R8 删除、「本次恢复后」口径整段删除。恢复后计数器与暂停前**逐位相同**。

**P1-7 · D3 的接线口径「18 个模块的 `request` 包装各加一行」不成立，会漏掉两个写在包装外的调用点**（§三 D3 / §5.3 / §8.2）

实测 `apps/web/lib` 下 `fetch(` 共 **20 个调用点 / 18 个文件**：`aiTradeHorizonApi.ts` 一个文件里有 **3 个**
彼此独立的 `fetch`（`horizon-profiles` / `discipline-presets` / `tradability`），**没有共享 `request` 包装**，
错误抛的是裸 `new Error(...)` 而不是 `ApiRequestError`。按 v1 的口径「各加一行」，其中 2 个会被漏掉，
而 `tsc` / `vitest` **都不会红**——这正是本轮要消灭的那类「静默不完整」。

整改：验收判据改成**源码扫描**——`apps/web/lib/**` 下每一个 `fetch(` 都必须经 `withTimeout(...)`，
零例外（`discoveryAssetApi.ts` 自带 signal 的那个也走 `withTimeout` 的不覆盖分支）；
计数口径改为「20 个调用点 / 18 个文件」；并要求该文件的三处错误一并升级为 `ApiRequestError`，
否则 `toApiError` 的超时文案在这三条路径上落不了地。

**P1-8 · D3 的 `LONG_MS === proxyTimeout` 违反 D3 自己写下的判据**（§三 D3 / §8.2）

v1 写：「`LONG_MS` **必须等于** `proxyTimeout`——客户端绝不能比代理先放弃，否则会把『代理还在等』误报成『超时了』。」
判据是对的，取值是错的：**客户端计时从 `fetch()` 调用那一刻起算，代理计时从 Next 收到请求才起算**，
取等值时客户端**必然**先触发。v1 用自己的取值违反了自己的规则。

整改：`LONG_MS = PROXY_TIMEOUT_MS + 15_000`（315s），G9 断言从「相等」改为
「`LONG_MS > next.config.mjs 的 proxyTimeout`」，并把裕量的理由写进源码注释。

**P1-9 · D2 的验收「暗色逐像素不变」与它自己的 `--on-accent` 改动直接矛盾**（§8.2 / §九 R3）

23 处 `bg-[var(--accent)]` 中同行带 `text-white` 的 **7 处**（实测，逐一列出见 §三 D2）要改成 `--on-accent`，
暗色取值 `#1a1915`——**白字变近黑字**，是全应用最显眼的一处视觉变化（含 `Button variant="primary"`
与 Agent 面板的用户消息气泡）。而 §8.2 与 R3 都写着「暗色应逐像素不变」。
按字面执行，这条验收**必然判失败**；实施节点只能选择「改验收」或「漏改 7 处」，两条都是坏结局。

整改：验收射程限定为——「除 ① 这 7 处 `bg-[var(--accent)]` 上的文字色、② `Button variant="danger"`
的 hover 底之外，暗色逐像素不变」，并把这两项单独列为**必须目视确认**的条目。

**P1-10 · D1 用 `BaseHTTPMiddleware`，而本轮的 D3 恰好会把它的已知故障模式从罕见变成日常**（§4.1 / §5.1）

`BaseHTTPMiddleware` 会为每个请求起一个 anyio task group 把下游 app 包起来，它有一个长期存在的已知行为：
**客户端在响应产生前断开时抛 `RuntimeError: No response returned`**。这个仓库此前几乎不会碰到它
（浏览器 `fetch` 没有默认超时，请求只会一直等）——而 D3 做的正是**给所有请求装上客户端主动 abort**，
其中 `LONG_MS` 那一档专门对着 1–3 分钟的长回测。两条修法叠在一起，
「客户端中途断开」从罕见事件变成**每一次超时都会发生**的常规事件。

整改：`OriginGuardMiddleware` 改为**纯 ASGI 中间件**（`__init__(self, app, ...)` + `async def __call__(scope, receive, send)`，
非 `http` scope 直接 `await self.app(...)`）。它只读一个 header、不碰 body、不构造 `Request`，
约 20 行，没有 task group、没有 body 缓冲、没有上述故障模式。§4.1 与 §5.1 已改写。

**P1-11 · D6 要走的下行通道不存在，v1 给的兜底通道也走不通，而 v1 把这个决定推给了实施节点**（§三 D6 / §5.6 / §九 R10）

实测 `paper/session.py` 的 `self._push` 类型全集为 **`status` / `candle` / `equity` / `signal` / `position` / `trade`**
——**没有 `note`**。v1 的兜底「若无，则改走既有 `signal.reason` 文案」同样不成立：
`signal` 只在 `self.strategy.on_candle(...)` **产出信号时**才推（`session.py:337-340`），
而"全员打分失败"这一根 K 线恰恰**不产出任何信号**，那条路上根本没有帧可搭。
R10 已经识别出「又造一个没人读的 wire 字段（本仓第 4 次）」这个风险，却把决定权留给了实施节点——
一条已知走不通的路，不该以「实施节点须先确认」的形式交付。

整改（评审节点当场决策，不下放）：**本轮 D6 只做服务端**——收窄 `except`、`logger.warning` 出声、
`scoring_failures` 计数、全员失败时保持在任者。UI 落点需要在 `status` 载荷加字段**并同时**在模拟盘面板加读点，
那是 UI 改动，与 §十 第 10 条冲突，明确列入 §十作为具名跟进。§5.6 的 `paper/session.py` 一行随之删除。

### P2（不阻断，已在正文按条采纳）

**P2-12 · D2 的文件枚举漏掉一个高流量表面：`AgentMessageBubble.tsx`。**
实测 `bg-[var(--accent)]` 涉及 21 个文件，其中 **5 个不在那 116 个文件里**；这 5 个里有 **1 个带文字**——
`components/Agent/AgentMessageBubble.tsx:62` `"self-end bg-[var(--accent)] text-white"`，
即 **Agent 面板里用户自己每一条消息的气泡**（白字 on 赭，明 3.28 / 暗 2.69）。
v1 的枚举命令只 grep `text-`，跑出来的 116 里没有它，"116 个文件"这个数会让实施节点直接漏掉它。
→ D2 的组件文件数订正为 **117**，§5.2 补第二条枚举命令并显式点名该文件。

**P2-13 · G9 的落点与既有设施重复。**
`apps/web/app/globals.source.test.ts` **已经**是 globals.css token 的门禁归属地，并且**已经**内置了
`relativeLuminance` / `contrastRatio` 与「token 必须在自己配对底上达标」的先例。
v1 新建 `components/ui/contrastTokens.source.test.ts` 承担全部三项，会造出第二个所有者。
→ 职责拆分：**CSS 取值 + WCAG 重算**扩进 `app/globals.source.test.ts`；
**`.tsx` 用法扫描 + 未定义 token 回退扫描**放在 `components/ui/contrastTokens.source.test.ts`。
文件计划补 `app/globals.source.test.ts`。
另：`warnToken.source.test.ts` 的扫描根是 `components/`（`path.resolve(__dirname, "..")`），
新扫描必须**同时覆盖 `app/`**；实测 `app/**` 当前命中为 0，故 §5.2 里写的 `apps/web/app/**/*.tsx` 是空集，表述已订正。

**P2-14 · 端口同步点从五处变六处。**
`desktop/README.md` §五「端口同步点（务必同步！）」是一张**显式五条**的清单，`desktop/src/lib/config.js:4`
的注释也写着「本目录为第 5 处」。`DEFAULT_ORIGINS` 里硬编码 `18761` 会成为**第 6 处**，
不写进那张表，下一次改端口必漏。→ 文件计划补 `desktop/README.md`；
`docker-compose.yml` 的 `web` 服务补一条 `SECURITY_ALLOWED_ORIGINS` 透传（远程部署时是唯一入口，
compose 注释里已经写着「远程部署须改为宿主可达地址」）。

**P2-15 · D6 的机制描述有偏差，照它写用例会测不到东西。**
`_choose_member`（`live_combo.py:111-125`）在全员 `0.0` 且在任者在 `scores` 里时，
`threshold = 0.0 + 0.0`、`0.0 > 0.0` 为假 → **既有代码已经保住了在任者**。
真正落到 `eligible[0]` 只有两条路径：① `active_key is None`（首次选择）；
② `active_key not in scores`（在任者被 regime gating 判为不合格）。
结论不变（第一个被选中的成员从此被永久钉住），但用例判据必须按这两条真实路径写，
否则用例会在**既有行为**上通过，什么都没验证到。§三 D6 与 §8.2 已改写。

**P2-16 · D7 没有预算阀。**
「打开外键让 G3 变红 → 红即真缺陷 → 修删除顺序」这条纪律是对的，但没有上界：
若受影响的删除路径超出本轮 153/176 的计划面，实施节点会被卡死在最后一批。
→ §九 R9 补一条出口：修复面若超出本轮计划，D7 **单批回退**并连同实测的红清单一起挂进 §十，
不牵连其余六批（这也正是把 D7 排在最后一批的本意）。

**P2-17 · G3 的排除项可能已经失效。**
`tests/test_factor_library_expansion.py` 是第 2 轮记录的**未跟踪在制文件**。若它此后被清理或合入，
照抄这条排除项会让报告里出现「排除了一个不存在的东西」的假声明。
→ §8.1 G3 补一句：排除项以 R12 要求的**当轮基线**为准，实测不存在则如实写「本轮无排除项」。

**P2-18 · `.env.example` 的位置与生效路径要写清。**
它在**仓库根**（`services/quant/` 下没有 `.env.example`，只有 `.env`）；后端 `Settings` 的
`env_file=".env"` 是**相对 CWD**（dev 下即 `services/quant/.env`），而 **prod 的 PyInstaller 后端完全不读 `.env`，
只读进程 env**——这正是 P0-3 的成因。§六已补这段说明。

### 处置汇总

- P0 **3 条**、P1 **8 条**：**全部已在正文就地整改**，无遗留。
- P2 **7 条**：全部已在正文按条采纳（P2 亦无遗留，只是不作为放行前提）。
- 无一条 D1–D7 的缺陷判断被推翻；被推翻的是 v1 的三处**修法**（`Origin: null` 放行、`deque`、continuity 加键）。

---

## 一、概述（Overview）

前两轮把这个仓库里「**失败但不出声**」的路径清了一遍：第 1 轮把红着的质量门禁修绿、给桌面外壳和根布局补上崩溃兜底；第 2 轮给关停链加了硬死线、给启动失败加了对话框、给四条会话流加了空闲保活、给展示缓冲加了封顶，并补齐了 `--warn` token 与 Tabs 的无障碍语义。两轮下来，**进程边界**和**流的活性**已经相当结实。

本轮换一个平面。前两轮问的是「出事了用户看得见吗」，本轮问的是「**这个产品对外说的话是不是真的**」——它对**谁**开放（信任边界）、它的**颜色**在说什么（语义色的可读性）、它的**等待**什么时候结束（超时语义）、它报出来的**数字**是不是真值（统计口径与静默降级）。这四件事在一个会拿着用户 OKX 私钥下真实委托的桌面应用里，是同一类问题的四个面：**系统在没有说谎的意图下，事实上说了不准确的话**。

审计从零开始，**不复用前两轮的 D1–D8 / D1–D9**，全部结论都有本轮实测取证（命令与输出见 §二）。共发现 7 条缺陷：1 条 P0（本机 API 没有任何信任边界，任意网页可读可写真实交易接口）、3 条 P1（浅色主题三个语义色作为文字全线不达 WCAG AA，含主按钮与危险按钮；REST 客户端 18 个模块里 17 个没有超时；`--on-accent` 是一个被引用却从未定义的 token）、2 条 P2（嵌套弹窗 Esc 一次关两层，已复现；真实成交台账既无界又同时被当作统计与换手闸的真值）、1 条 P2 与 1 条 P3（组合模拟盘打分静默吞异常导致永远选第一个成员；SQLite 外键从未启用）。

其中 **D2（对比度）与 D5（台账封顶）正是第 2 轮 §十「明确不做」里挂账的两条**——第 2 轮把它们判为「需要独立轮次」，本轮就是那一轮。本文档在 D2 上给出了一条第 2 轮当时没有看到的低风险路径：**不动 `--accent` / `--long` / `--short` 本身**（图表 series、状态点、进度条、边框逐字节不变），只新增三个 text-grade 墨色 token 并把 301 处**文字**用法迁过去，从而绕开第 2 轮担心的「牵动全应用多空配色与图表 series 颜色」。

---

## 二、取证方法与基线（本轮实测）

本节所有数字都是**本轮在当前工作树上真跑出来的**，不是从上游文档抄的。凡是本轮没跑的，一律标注「继承自上一轮」，绝不冒充本轮结论。

> **[v2]** 评审节点**不接受**本节的任何数字，全部重新实测/复算了一遍：跨源预检逐字复现、
> WCAG 全表逐位吻合、301/116/23/7/9/8/20 各项计数全部核对、`tsc --noEmit` 亲跑 exit 0。
> 明细见开头的「评审记录 §0」。本节数字**全部成立**，无一条被推翻。

| 门禁 | 命令 | 本轮实测结果 |
|---|---|---|
| G1 前端类型 | `cd apps/web && npx tsc --noEmit` | **exit 0**（绿） |
| G2 前端用例 | `cd apps/web && NODE_ENV=test npx vitest run` | **169 files / 1858 tests / 0 failed**（48.7s） |
| G4 后端 lint | `cd services/quant && .venv/Scripts/python -m ruff check .` | **All checks passed, exit 0** |
| G3 后端用例 | `pytest -q` | **本轮未跑**（约 12 分钟）。继承第 2 轮终审：CI 视角 3648 passed / 0 failed（排除未跟踪在制文件 `tests/test_factor_library_expansion.py`） |
| G5 桌面用例 | `cd desktop && npm test` | **本轮未跑**。继承第 2 轮：本地 31 pass、干净检出 24 pass |

### 2.1 取证命令与关键输出

**（a）跨源预检：任意站点能否对本机交易接口发起带 JSON body 的 POST？**

```python
from fastapi.testclient import TestClient
from app.main import app
c = TestClient(app)
r = c.options("/api/trading/close-all", headers={
    "Origin": "https://evil.example.com",
    "Access-Control-Request-Method": "POST",
    "Access-Control-Request-Headers": "content-type"})
```

```
preflight status: 200
  access-control-allow-origin: *
  access-control-allow-methods: DELETE, GET, HEAD, OPTIONS, PATCH, POST, PUT
  access-control-allow-headers: content-type
GET /api/health status: 200 | ACAO: *
middleware stack: ['CORSMiddleware']
trading routes: ['/api/trading/close', '/api/trading/close-all', '/api/trading/config',
                 '/api/trading/fills', '/api/trading/leverage', '/api/trading/open-orders',
                 '/api/trading/order', '/api/trading/positions']
```

**（b）WCAG 2.1 相对亮度复算**（脚本见 §8.1 G9，逐色对 `--bg` / `--surface` / `--surface-2` 三个底）：

```
===== LIGHT =====
        fg |  on --bg  on --surface  on --surface-2   AA(4.5)?
    --text |    15.32         16.53           14.53   PASS
   --muted |     5.17          5.58            4.90   PASS
  --accent |     3.01          3.25            2.86   **FAIL**
    --long |     3.74          4.03            3.54   **FAIL**
   --short |     4.13          4.45            3.91   **FAIL**
    --warn |     5.71          6.16            5.42   PASS      ← 第 2 轮 D5 的产物，达标
===== DARK =====   全部 PASS（--accent 6.54 / --long 6.96 / --short 5.79 on --bg）
```

```
Button variant="primary"（text-white on bg --accent）
  light  #ffffff on #cc785c = 3.28   **FAIL AA**
  dark   #ffffff on #d98a6e = 2.69   **FAIL AA**（连大字号 3:1 都不到）
Button variant="danger"（text --short on 各底）
  light  --short on --bg 4.13 / --surface 4.45 / --surface-2 3.91 / --accent-soft(hover) 3.42
```

**（c）嵌套弹窗 Esc 复现**（临时探针用例，跑完即删，`git status` 已复核归零）：

```
FAIL components/ui/__probe_nested_esc.test.tsx > Esc inside a nested ConfirmDialog
     must not also close the parent Modal
AssertionError: expected "spy" to not be called at all, but actually been called 1 times
Test Files  1 failed | 169 passed (170)
```

**（d）starlette CORS 中间件不覆盖 WebSocket**：`services/quant/.venv/Lib/site-packages/starlette/middleware/cors.py:76`
→ `if scope["type"] != "http":  # pragma: no cover` 直接放行。仓库有 **9 条** `@router.websocket` 路由。

**（e）前端 fetch 超时覆盖率**：`apps/web/lib/*.ts` 中 18 个模块各自持一份 `fetch` 包装，
只有 `discoveryAssetApi.ts` 一个用了 `AbortSignal.timeout`；其余 17 个**无 signal、无超时**。

**（f）SQLite 外键**：`PRAGMA foreign_keys` 在开发库上读到 **0**（关闭）；`app/db/models.py`
有 8 处 `ForeignKey` 声明、**零处 `ondelete`**；开发库实测孤儿行 `trades: 0/240`、`equity_points: 0/6539`。

**（g）真实成交台账**：`live_auto/executor.py:123` `self.trades: list[dict] = []`，
appends 在 310 / 517 / 560 / 925，**零处裁剪**；`engine/simulator.py:117` 同形。

---

## 三、缺陷清单（逐条：取证 → 根因 → 行级修法）

### D1 · P0 · 本机 API 没有任何信任边界

**取证**：§2.1(a)。中间件栈里**只有 `CORSMiddleware`**，全仓没有任何鉴权依赖；
`app/main.py:367-376` 写着 `allow_origins=["*"]`；`app/config.py:80` `live_trading_enabled: bool = True`
（**默认开**）；`app/api/trading.py` 的 8 条路由用库内明文密钥（`_creds_from_row`）向 OKX 下真实委托。
后端绑定 `127.0.0.1`（`desktop/scripts/backend_entry.py:22`），因此**不是**局域网暴露。

**根因**：这套 CORS 配置写于「单机本地工具、前端走 Next 同源代理」的年代（注释原文如此），
而产品此后长出了「真实下单 + 明文私钥 + 24/7 无人值守」三件事，配置却没跟着收。
两个容易被绕过的直觉必须点破：

1. **CORS 保护的是「读」，不是「执行」。** 浏览器只在拿不到 `ACAO` 时**隐藏响应**，
   请求本身早已到达服务端并被执行。本轮实测的预检返回了 `ACAO: *` **且** `allow-headers: content-type`，
   意味着 `Content-Type: application/json` 的跨源 POST 是**被明确放行**的——
   `POST /api/trading/close-all` 不是理论风险，是配置上已经批准的动作。
2. **WebSocket 完全不过 CORS**（§2.1(d)）。就算把 `allow_origins` 收紧，9 条 WS 路由仍然对任意
   `Origin` 敞开。**修 CORS 而不修 WS，等于只锁了前门。**

**威胁模型**（明确写清，避免防御性噪音）：攻击者是**用户浏览器里的任意网页**（广告位、被投毒的第三方站点）。
本机已被植入原生恶意软件不在模型内——那种情况下它可以直接读 `%APPDATA%/aragon-desktop/aragon.db`，
应用层再怎么设防都无意义。判据因此很干净：**浏览器发起的跨源请求必带 `Origin` 头**
（`fetch` 即使 `mode:'no-cors'` 也带；所有 POST 都带），所以「`Origin` 缺失」可靠地等价于「不是浏览器」。

**修法**（三层，缺一不可）：

1. **收紧 CORS**：`app/main.py` 的 `allow_origins=["*"]` → `get_settings().allowed_origin_list()`。
   默认值 `["http://localhost:18761", "http://127.0.0.1:18761"]`（Next 前端端口，见 `apps/web/package.json` 的 `next dev -p 18761`）。
   `allow_credentials` 维持 `False`，`expose_headers` 维持不变。
2. **新增 `OriginGuardMiddleware`**，装在 CORS **之前**（ASGI 中间件后进先出，`applications.py:132`
   `user_middleware.insert(0, ...)` ⇒ 后调用 `add_middleware` 者在外层，故它要在 CORS 之后调用）：
   `scope["type"] == "http"` 且请求带 `Origin` 且不在白名单 → 直接 `403 {"error":{"code":"ORIGIN_NOT_ALLOWED", ...}}`，
   **不进路由**。这一层才是真正拦住「执行」的那层；CORS 只负责让浏览器同时也读不到。

   **[v2·P1-10] 它必须是纯 ASGI 中间件，不是 `BaseHTTPMiddleware`。** 后者为每个请求起一个 anyio
   task group 把下游 app 包起来，其已知故障模式是「客户端在响应产生前断开 → `RuntimeError: No response returned`」。
   这个仓库此前几乎碰不到它（浏览器 `fetch` 没有默认超时，请求只会一直等），而**本轮的 D3 做的正是
   给所有请求装上客户端主动 abort**——两条修法叠加会把这个故障模式从罕见变成「每一次超时都发生」。
   纯 ASGI 形态只读一个 header、不碰 body、不构造 `Request`，约 20 行，没有上述任何一条。
3. **9 条 WS 路由加 `guard_ws_origin`**：`Origin` 存在且不在白名单 → `await websocket.close(code=1008)`；
   缺失 → 放行（非浏览器客户端，如 `scripts/smoke_e2e.py`、`desktop/scripts/verify-dist.mjs` 的 node http）。

**[v2·P0-1] `Origin: null` 一律拒绝，不放行。** v1 曾写「放行 `null` 但记 `logger.info`」，理由是
「`file://` 页面与部分 sandboxed iframe 会发 `null`」——后半句正是这条修法的破口：
**带 `sandbox` 且不含 `allow-same-origin` 的 iframe 拥有不透明源，其请求一律带 `Origin: null`，
而这个 iframe 可以由任意网页当场创建**：

```html
<iframe sandbox="allow-scripts" srcdoc="
  <script>fetch('http://127.0.0.1:18760/api/trading/close-all',
    {method:'POST',headers:{'Content-Type':'application/json'},body:'{}'})</script>">
```

放行 `null` ⇒ 上面三行 HTML 直达 `/api/trading/close-all`，本条修法等于没做；
WS 侧更糟——**WS 完全没有 CORS，响应可读**，同一个 iframe 可以订阅 9 条流里的任意一条，
把用户的实盘会话/成交/持仓读走。OWASP 对 `Origin: null` 的既有结论是「等同于通配符，绝不可信」。
因此判据收敛为**两态**：

| `Origin` | 判定 | 理由 |
|---|---|---|
| 缺失 | **放行** | 浏览器的非 GET/HEAD `fetch`/XHR 与 WS 握手**必带** `Origin`；缺失可靠地等价于「不是浏览器」 |
| `"null"` | **拒绝** | 它是**浏览器**发的不透明源，而威胁模型正是浏览器 |
| 在白名单 | 放行 | — |
| 其余 | 拒绝 | — |

代价：`file://` 本地调试页调不通本机 API。这是有意的取舍，写进 README 与 §十；
实施节点须按 v1 自己定的要求跑一遍 `npm run verify:dist` 确认 Electron 渲染进程不受影响
（它加载的是 `http://127.0.0.1:18761`，`desktop/src/main.js:550` `win.loadURL(cfg.WEB_URL)`，不依赖 `null`）。

**关于 `ws_market.py` 的零 diff 约束**：第 2 轮硬约束 2 要求 `app/api/ws_market.py` 零 diff，
那条约束的**射程是「不得给它加空闲保活帧」**——理由是「那条流上静默即证据，加保活会把 45s 看门狗致盲」。
Origin 守卫**不产生任何帧**，也不改变静默语义，因此不与该约束冲突。但本轮仍必须**重跑**第 2 轮留下的
那条反向用例（idle 时 `ws_market` 不发任何东西），把「没被好意破坏」这件事重新钉一遍。

---

### D2 · P1 · 浅色主题三个语义色作为文字全线不达 AA（含主按钮与危险按钮）

**取证**：§2.1(b)。占用面积：`text-[var(--short)]` **168 处**、`text-[var(--accent)]` **103 处**、
`text-[var(--long)]` **30 处**，共 **301 处、分布在 116 个非测试 `.tsx` 文件**；
`bg-[var(--accent)]` **23 处**，其中 7 处在同一行配 `text-white`（含 `ui/index.tsx` 的 `Button variant="primary"`）。

**根因**：`--long` / `--short` 是**为 K 线蜡烛设计**的颜色——在图表上它们是大色块，对比度不是问题；
但它们被复用成了**正文字号的涨跌数字**与**全应用的错误文案**（`role="alert"` 的 20 余处用的就是 `text-[var(--short)]`）。
`--accent` 同理：品牌赭色作为面色很好看，作为 13px 文字只有 3.01:1。
**暗色主题全部达标**（5.79–6.96），所以这是一个**只在浅色下发生**的系统性问题——也正因为只在一半场景发生，
它躲过了此前所有轮次。

三处后果按严重度排序：

| 位置 | 实测 | 后果 |
|---|---|---|
| `Button variant="primary"`（白字 on `--accent`） | 浅 **3.28**、暗 **2.69** | 全应用主行动按钮。**暗色连大字号 3:1 都不到** |
| `Button variant="danger"`（`--short` 字） | 3.91–4.45，hover 底 `--accent-soft` 上 **3.42** | 删除 / 平掉全部 / 停止会话——最需要看清的那颗按钮 |
| 301 处正文（盈亏数字、错误文案） | 2.86–4.45 | 亏损红与盈利绿本身就是这个产品的主要信息载体 |

**顺带纠正上一轮的一处判断**：第 2 轮 §十 把 `--muted` 与 `--short` 并列为「待整改」。
本轮实测 `--muted` 浅色 5.17 / 5.58 / 4.90、暗色 5.91 / 5.42 / 4.84，**全部达标**，不需要动。
反过来，`--long` 与 `--accent` 上一轮没有点名，实际比 `--short` 更差。**照着上一轮的清单改会改错两次。**

**修法**（关键决策：**不动**已有三个 token 的取值）：

新增四个 token，取值经 WCAG 2.1 相对亮度复算（候选与实测值见 §2.1(b) 同一脚本）：

```css
:root {                       /* 浅色 */
  --accent-ink: #9e4a2b;      /* on --bg 5.56 / --surface 6.00 / --surface-2 5.28 */
  --long-ink:   #2b6b53;      /* 5.79 / 6.25 / 5.49 ；--accent-soft 上 4.80 */
  --short-ink:  #9e3f27;      /* 6.04 / 6.52 / 5.73 ；--accent-soft 上 5.01 */
  --on-accent:  #1f1e1b;      /* on --accent(#cc785c) 5.09 */
}
.dark {                       /* 暗色：三个 ink 与现值相同，故暗色渲染逐字节不变 */
  --accent-ink: #d98a6e;
  --long-ink:   #5fb38c;
  --short-ink:  #d97a62;
  --on-accent:  #1a1915;      /* on --accent(#d98a6e) 6.54 */
}
```

- `--accent` / `--long` / `--short` **取值不变** ⇒ 图表 series 颜色、状态点、进度条填充、边框、
  `accent-*` 类全部零变化。这正是第 2 轮判定「牵动图表」而搁置的那个风险，被这条设计绕开了。
- 301 处 `text-[var(--x)]` → `text-[var(--x-ink)]`（纯机械替换，`tsc` + G2 兜底）。
- 23 处 `bg-[var(--accent)]` 里**带文字标签**的把 `text-white` → `text-[var(--on-accent)]`；
  不带文字的（圆点 / 进度条 / 纯边框）**不改**。实测同行带 `text-white` 的恰好 **7 处**，逐一列出（勿凭记忆）：

  | 文件:行 | 说明 |
  |---|---|
  | `components/ui/index.tsx:32` | `Button variant="primary"` —— 全应用主行动按钮 |
  | `components/Agent/AgentMessageBubble.tsx:62` | **用户自己每一条消息的气泡**（见下方 [v2·P2-12]） |
  | `components/Chart/IndicatorMenu.tsx:71` | 计数徽标 |
  | `components/Discovery/DiscoveryCandidateTable.tsx:125` | 选中行 |
  | `components/Factor/MemberPalette.tsx:316` | 角标 |
  | `components/Factor/SymbolGroupModal.tsx:318` | 角标 |
  | `components/Strategy/BacktestConfigModal.tsx:401` | 选中段 |

- **[v2·P2-12] 组件文件数是 117，不是 116。** `bg-[var(--accent)]` 涉及 **21 个文件**，其中 **5 个不在
  `text-` 那 116 个文件里**；这 5 个里有 1 个**带文字**——`AgentMessageBubble.tsx`。
  v1 的枚举命令只 grep `text-`，跑出来的 116 里没有它，照着 116 做**必然漏掉 Agent 面板的用户消息气泡**
  （白字 on 赭，明 3.28 / 暗 2.69，是全应用出现频率最高的表面之一）。§5.2 已补第二条枚举命令。

- **[v2·P1-5] 危险按钮的 hover 底同时要改：`--accent-soft` → `--surface-2`。**
  实测暗色 `--short-ink`(`#d97a62`) on 暗色 `--accent-soft`(`#3a2c25`) = **4.41 < 4.5**，
  而唯一的真实落点正是 `ui/index.tsx:36`：
  `variant === "danger" && "bg-transparent text-[var(--short)] hover:bg-[var(--accent-soft)]"`。
  本仓已经把这件事钉成规矩——`app/globals.source.test.ts` 里那条
  「`--warn` 是为 AA 而生的 token，它必须在**自己配套的 `--warn-soft` 底色**上也达标」
  就是第 2 轮把 `#9a5a0d`（自配对 4.46）换成 `#8f5209` 的产物。**在同一个位置重犯一次，
  等于修法自身携带了它要消灭的那个缺陷。**
  改 hover 底而不是改 ink 取值，是因为后者会把「暗色 ink 取值 = 原面色 ⇒ 暗色文字逐像素不变」
  这张覆盖 301 处机械替换的验证网（§九 R3）一起拆掉。改完实测：
  `--short-ink` on `--surface-2` = 明 **5.73** / 暗 **4.74**，双双达标；
  顺带修掉「破坏性按钮的 hover 底是品牌赭色」这处语义错配，写法与 `variant="ghost"` 的
  `hover:bg-[var(--surface-2)]` 一致（既有约定，不是新发明）。

- **[v2·P1-9] 暗色不是「逐像素不变」，射程必须写清。**
  上表 7 处 `text-white` → `--on-accent`（暗 `#1a1915`）是**白字变近黑字**，是本轮最显眼的视觉变化；
  再加上 danger 的 hover 底。所以准确表述是：**除这两项之外**，暗色逐像素不变。
  §8.2 的验收已按此射程改写，并把这两项单列为必须目视确认的条目。
- **`--on-accent` 本来就已经被引用**：`components/Agent/AiTradeHorizonFields.tsx:467` 写着
  `text-[var(--on-accent,#fff)]`——引用了一个**从未定义**的 token，靠内联 `#fff` 兜底。
  这与第 2 轮 D5 消灭的「六处内联回退一个不存在的 `--warn`」是**同一个缺陷的第二次复发**。
  定义 token 之后必须**同时删掉那个内联回退**，并由 §8 的源码扫描永久禁止「`var(--x, fallback)` 指向未定义 token」。

---

### D3 · P1 · REST 客户端 18 个模块里 17 个没有超时

**取证**：§2.1(e)。`apps/web/lib/api.ts:65` 的 `rawRequest` 是全应用的中央 REST 客户端，
`fetch` 调用**不带 `signal`**；另外 16 个 `*Api.ts` 各自复制了同形的 `request` 包装，同样不带。

**根因与真实后果**（必须精确，不能夸大）：浏览器 `fetch` 本身**没有默认超时**。
唯一的兜底是 `apps/web/next.config.mjs` 的 `experimental.proxyTimeout: 300000`——那是
**Next standalone 代理**的 5 分钟传输层超时（它本身是为长回测被 30s 默认值打断而抬上去的，
见 `docs/diagnoses/desktop-backtest-proxy-timeout-socket-hang-up/`）。所以准确表述是：

- 后端**进程死了** → 连接立即拒绝，前端正常报错。**这条路径没问题。**
- 后端**活着但不回**（事件循环被占、线程池被挖掘/训练占满、某个 OKX 调用挂住）
  → 打包/standalone 路径下按钮**冻结最长 5 分钟**、期间零反馈；
  `next dev` 与任何直连后端的路径**没有任何兜底**。

**为什么这是 P1 而不是 P2**：这个仓库前两轮的主线就是「让每一条失败路径出声」，
第 2 轮给 4 条 WS 流补了空闲保活、给客户端配了 45s 停摆看门狗——**HTTP 这一侧被整轮跳过了**。
一个「停止会话」按钮转了 5 分钟圈还什么都不说，和一条假装连着的 WS 是同一种失信。

**修法**：

1. 新增 `apps/web/lib/httpTimeout.ts`：
   - `export const FAST_MS = 20_000;`（普通读写）
   - `export const PROXY_TIMEOUT_MS = 300_000;`（= `next.config.mjs` 的 `experimental.proxyTimeout`）
   - `export const LONG_MS = PROXY_TIMEOUT_MS + 15_000;`（315s，重计算路径）

     **[v2·P1-8] 这里必须是「严格大于」，不是「等于」。** v1 写「`LONG_MS` **必须等于** `proxyTimeout`
     ——客户端绝不能比代理先放弃」：判据对，取值错。**客户端计时从 `fetch()` 调用那一刻起算，
     代理计时从 Next 服务端收到请求才起算**，取等值时客户端**必然**先触发，
     正好落进 v1 自己要避免的「把『代理还在等』误报成『超时了』」。15s 裕量覆盖排队/连接/传输，
     理由须写进源码注释；G9 的断言随之从「相等」改为 `LONG_MS > proxyTimeout`。
   - `export function timeoutFor(path: string): number`：按显式前缀表返回 `LONG_MS`，
     其余一律 `FAST_MS`。长跑前缀表（逐条来自本轮实读，不猜）：
     `/strategies/{id}/backtest`、`/strategies/compare`、`/strategies/{id}/multi-backtest`、
     `/strategies/{id}/walk-forward`、`/strategies/combo/preview`、`/factors/mine`、
     `/factors/evaluate`、`/factor-sets/{id}/score`、`/weight-models/train`、
     `/ai-trade/prior/rebuild`、`/ai-backtest/`。
   - `export function withTimeout(init, ms)`：`{...init, signal: init?.signal ?? AbortSignal.timeout(ms)}`
     ——**调用方自带 signal 时不覆盖**（`discoveryAssetApi.ts` 已有的那个必须原样保留）。
   - `export function toApiError(err, ms, path)`：把 `TimeoutError` / `AbortError` 映射成
     `ApiRequestError("TIMEOUT", <文案>, 0)`。
2. **[v2·P1-7] 口径是「20 个 `fetch(` 调用点 / 18 个文件」，不是「18 个模块各加一行」。**
   实测 `apps/web/lib` 下 `fetch(` 共 20 处：16 个文件各 1 处（都在自己的 `request` 包装里）、
   `api.ts` 1 处（`rawRequest`）、而 **`aiTradeHorizonApi.ts` 独占 3 处**
   （`horizon-profiles` / `discipline-presets` / `tradability`）——它**没有共享 `request` 包装**，
   三处各自裸调 `fetch`，且抛的是 `new Error(...)` 而不是 `ApiRequestError`。
   按 v1 的「各加一行」执行，其中 2 处会被漏掉，而 `tsc` 与 `vitest` **都不会红**
   ——这正是本轮要消灭的那类静默不完整。因此：
   - 三处一并接 `withTimeout`，并**一并升级为 `ApiRequestError`**，否则 `toApiError` 的超时文案
     在这三条路径上没有落点；
   - 验收判据改成**源码扫描**（并入 G9 同一个扫描文件）：`apps/web/lib/**` 下每一个 `fetch(`
     都必须在同一个调用里经 `withTimeout(...)`，零例外。
3. **写类接口的超时文案必须是「未确认」而不是「失败」**。这条是本缺陷里最重要的一句：
   一个超时的 `POST /api/trading/order` **可能已经成交了**。把它显示成「下单失败」，
   用户会重下一单——那是本仓最不能犯的错（参见 `7b55d16` 的提交语「never let it claim a
   position was closed when it wasn't」）。因此：
   - 读类（GET）超时文案：`请求超时（20 秒），请检查后端是否响应。`
   - 写类（`tradingApi.order/close/closeAll/leverage`、`aiTradeApi` 的 start/stop/pause/resume、
     `liveAutoGroupApi` start/stop、`paper` start/stop）超时文案：
     `请求超时（20 秒）——本次操作结果未知，请刷新持仓/会话状态核对后再决定是否重试。`
   - 判据由 `httpTimeout.ts` 里的一张显式写类路径表决定，**不靠 HTTP 方法猜**
     （`/okx/connectivity/probe` 是 POST 但只读）。

---

### D4 · P2 · 嵌套弹窗 Esc 一次关两层

**取证**：§2.1(c)，探针用例已实测失败（`outerClose` 被调用 1 次，期望 0 次）。
真实调用点：`components/Factor/SymbolGroupModal.tsx:177` 把 `<ConfirmDialog>` 渲染在 `<Modal>` **内部**。

**根因**：`ui/index.tsx:324` 每个 `Modal` 都在 `window` 上注册**捕获期** `keydown`。
两个 `Modal` 同开时，两个监听器挂在**同一个 EventTarget** 上；
`e.stopPropagation()`（`index.tsx:300`）按 DOM 规范**只阻止事件传到其它节点，不阻止同一节点上的其它监听器**
——那需要 `stopImmediatePropagation`。外层 `Modal` 先挂载故先执行，于是 Esc 先关掉外层，
用户想「退出这个删除确认」，结果整个标的组管理器一起消失、工作上下文丢失。

**修法**（`ui/index.tsx`，两处）：

1. 模块级栈 `const dialogStack: symbol[] = []`。`Modal` 在一个**专用的 `useEffect(..., [open])`** 里
   `push` 自己的 `useRef(Symbol())`，清理时按值 `splice` 移除（**不是 `pop`**——卸载顺序未必是栈序）。

   **[v2·P1-4] 这个 effect 必须与 keydown effect 分开，依赖数组只能是 `[open]`。**
   注册 `keydown` 的那个 effect 依赖是 **`[open, onClose]`**；把 push/splice 挂上去，
   `onClose` 身份一变就会「splice 掉自己 → 重新 push 到**栈顶**」。实证调用链：
   `FactorLab.tsx:301` 写的是 `onClose={() => setGroupModalOpen(false)}`（内联箭头），
   `SymbolGroupModal.tsx:124` 把它原样交给 `<Modal onClose={onClose}>`——
   **外层 Modal 的 `onClose` 每次 FactorLab 重渲染都换新身份**。于是内层 `ConfirmDialog` 开着时，
   只要 FactorLab 因任何状态/流事件重渲染一次，外层就被推回栈顶，Esc 又开始关外层。
   更糟的是这条复活**依赖重渲染时机**，而下面要固化的那条静态渲染用例**会一直是绿的**。
   `open` 是布尔，身份天然稳定，专用 effect 因此只在真正开/关时动栈。
2. `onKeyDown` 首行守卫：`if (dialogStack[dialogStack.length - 1] !== myId) return;`
   ——只有栈顶对话框响应 Esc **与** Tab 陷阱。这同时修掉一个更隐蔽的问题：
   目前外层的 Tab 陷阱在内层打开时仍在跑，只是恰好每个分支都不命中；
   任何一次 `FOCUSABLE` 选择器调整都会让它开始抢焦点。
3. 保留 `stopPropagation`，**额外**加 `stopImmediatePropagation()` 作为纵深防御，并写清理由。

回归用例：把 §2.1(c) 的探针固化进 `components/ui/Modal.test.tsx`（不新建文件），
**并补第二条**：内层开着时让外层的 `onClose` 换一次身份（`rerender` 传新的内联箭头）再按 Esc，
断言外层 `onClose` 仍为 0 次——没有这一条，P1-4 那个复活路径无人看守。

---

### D5 · P2 · 真实成交台账既无界，又同时是统计与换手闸的真值

**取证**：§2.1(g)。读点（本轮逐个实读）：

| 读点 | 用途 | 封顶后会怎样 |
|---|---|---|
| `live_auto/session.py:489-490` | `tradeCount = len(executor.trades)`、`avgPnl = realized / len(...)` | 直接低报 |
| `ai_auto/session.py:2076-2077` | 同上 | 直接低报 |
| `ai_auto/discipline_runtime.py:95` | `opens = sum(1 for t in executor.trades if ...)` —— **换手闸判据** | 闸门被放松，直接影响真实下单 |
| `ai_auto/session.py` 的 `_cost.reconcile_tick(trades=...)` | 成本对账窗口 | 净额盈亏失真 |
| `ai_auto/profit_guard_runtime.py:81`、`ai_auto/session.py:452` | `reversed(...)` 倒序找最近一笔 | 安全（近端） |
| `ai_auto/reconcile.py`、`ai_auto/resume.py:279` | 对账 / 恢复时整体赋值 | 恢复后计数归零 |

> 这张读点表**不是我列的**——`app/wire_caps.py` 的模块 docstring 里，第 2 轮已经把前四条
> 逐条写下来了，作为「为什么绝不裁剪服务端会计真值」的理由。本轮做的是把那份理由里
> 的每一条**逐个消解**，而不是绕开它。第四条（成本对账窗口）是本轮从该 docstring 里
> 补回来的——我自己按读点扫描时漏了它，这也正说明封顶必须在计数器之后。

**根因**：一个只增列表同时承担了三种角色——**明细台账**、**计数器**、**闸门判据**。
第 2 轮把前端展示缓冲封到了 2000 并**明确拒绝**动这三处后端真值（其硬约束 4：
「`git diff` 里出现对这三者的任何裁剪即视为越界，直接回退该批」），把它挂进 §十 留给下一轮。
**本轮就是那一轮，并显式解除该约束——但只在下面这个前置条件满足之后。**

**修法（顺序不可颠倒）**：

- **第 1 步（本轮必做）**：`live_auto/executor.py` 新增显式计数器
  `trade_count: int`、`open_count: int`、`realized_pnl_total: float`，
  并把 4 处 `self.trades.append(trade)`（310 / 517 / 560 / 925）**全部收敛到一个私有
  `_record_trade(trade)`**——让「新增一个 append 点却忘了计数」在结构上不可能发生。
  把上表前三个读点改读计数器。

  **[v2·P1-6] 恢复路径走「派生」，不是「落库」。** v1 要求 `resume.py` 的 continuity 载荷
  携带三个计数器并持久化，这与该模块自己写下的不变式正面冲突——
  `ai_auto/resume.py:433 rebuild_continuity` 的 docstring 原文：
  「**派生量绝不落库**：落两份必然漂移，而这里漂移的是真实资金的盈亏显示。」
  而它从 DB 的**全量 `Trade` 行**重建 `trades`（`select(Trade).where(Trade.run_id == run_id)`，无裁剪），
  三个计数器因此**全都可以就地派生**：

  ```python
  trade_count        = len(continuity["trades"])
  open_count         = sum(1 for t in trades if t["side"] in ("long", "short"))
  realized_pnl_total = continuity["realizedPnlUsd"]   # 早已存在，与 local_net_view 同一条规则
  ```

  所以 `resume.py:279` 的 `executor.trades = continuity["trades"]` 改为
  `executor.restore_trades(continuity["trades"], realized_total=continuity["realizedPnlUsd"])`
  ——一次赋值 + 派生三个计数器 + 施加封顶，**一个新持久化键都不加**。
  §七的三个新键、§九 R8、以及「老库恢复后计数从 0 起、UI 须按『本次恢复后』口径标注」整段
  随之删除：恢复后计数器与暂停前**逐位相同**，没有可标注的降级，也就不需要那句解释。
- **第 2 步（本轮做，但必须是独立批次、独立跑 G3）**：给 `self.trades` 加封顶，
  常量落在第 2 轮已建的单一真源 `app/wire_caps.py`，取值 `TRADE_LEDGER_MAX = 20000`
  （按 AI 会话最密的档位估算，远超任何现实会话；它是**内存护栏**，不是展示策略）。

  **[v2·P0-2] 容器类型保持 `list[dict]`，不得改成 `deque`——实测会直接抛 `TypeError`。**
  `app/wire_caps.py:25` 的 `tail_trades` 实现是 `list(trades[-cap:])`，而 `deque` **不支持切片**：

  ```
  >>> from collections import deque; deque([1,2,3], maxlen=5)[-2:]
  TypeError: sequence index must be integer, not 'slice'
  ```

  `tail_trades(self.executor.trades)` 的调用点是 `live_auto/session.py:467` 与 `ai_auto/session.py:2021`
  ——**两条会话的快照下发主路径**，换成 `deque` 即每一次下发都炸。第二处：
  `ai_auto/resume.py:279` 直接赋一个普通 `list`，会**静默把 `maxlen` 丢掉**，恢复之后封顶不复存在。
  因此封顶改为在 `_record_trade` 内部按块裁剪（`if len(self.trades) > CAP + SLACK: del self.trades[:len(self.trades) - CAP]`，
  按块删摊薄 `del` 的 O(n) 成本）。这样 `tail_trades`、`resume` 赋值、类型标注、
  任何 `json` 序列化路径**全部零改动**——目标（内存护栏）一样达成，而外部契约面积从
  「四处已知 + N 处未知」缩到 0。`app/wire_caps.py` 的模块 docstring 须同步更新：
  它现在写着「绝不裁剪服务端的会计真值」，本轮的前提是**先有计数器再封顶**，理由要就地改写而不是删掉。

  `engine/simulator.py:117` 的 `broker.trades` **本轮不动**——它是回测/模拟撮合的完整产物，
  会被整体落库与出指标，封顶会改变回测结果本身。理由写进 §十。
- **红线用例**：一条断言「4 个 append 点都经 `_record_trade`」的结构用例
  （对 `executor.py` 源码做正则扫描，仿第 2 轮 `warnToken.source.test.ts` 的做法），
  一条「封顶触发后 `trade_count` 仍等于真实累计」的行为用例，
  外加一条 **`tail_trades(executor.trades)` 在封顶触发后仍能正常返回**的用例
  （这条专门钉 P0-2：容器类型一旦被换回 `deque`，它立刻红）。

---

### D6 · P2 · 组合模拟盘打分静默吞掉全部异常，退化成「永远选第一个成员」

**取证**：`paper/live_combo.py:157` —— `except Exception: return 0.0  # noqa: BLE001`，
无日志、无计数、无 UI 落点。`_choose_member`（:111-125）在全部候选都是 `0.0` 时，
`max(eligible, key=...)` 按 Python 稳定语义返回 **`eligible` 的第一个元素**；
`active_key is None` 时直接采纳。本轮已实跑确认调用链可达
（`LiveComboStrategy.on_candle` → `_choose_member` → `_score_member`，
且 `paper/session.py:335` 在**事件循环内**同步调用它）。

**根因**：两条叠加。
（1）`except Exception` 把「这个成员这根 K 线算不出来」和「这个策略类根本跑不起来」合并成同一个结果 `0.0`；
（2）`0.0` 又恰好是一个**合法分数**，于是「全员失败」和「全员表现持平」在下游不可区分，
系统平静地把资金一直交给成员列表里的第一条策略，并且**在整个 UI 上不留一点痕迹**。
对一个宣称「按近端表现自动轮动」的功能来说，这是最坏的一种失败：它看起来在工作。

**[v2·P2-15] 机制要说准，否则用例会测不到东西。** 全员 `0.0` 且**在任者在 `scores` 里**时，
`threshold = incumbent + abs(incumbent) * margin = 0.0`，`0.0 > 0.0` 为假 ⇒ **既有代码已经保住了在任者**。
真正落到 `eligible[0]` 的只有两条路径：
① `active_key is None`（首次选择）；② `active_key not in scores`（在任者被 regime gating 判为不合格）。
结论不变——**第一个被选中的成员从此被永久钉住**——但下面的修法 3 与 §8.2 的用例判据必须按这两条真实路径写，
否则用例会在**既有行为**上通过，什么都没验证到。

**顺带记录一处口径分歧（本轮不改，只写清）**：回测车道
`engine/combo.py:_build_member_curves` 是**每个成员整段回测一次、再从连续净值曲线上取窗口打分**；
模拟盘车道 `live_combo._score_member` 是**每根 K 线对每个成员在 `lookback_bars` 窗口上重跑一次隔离回测**。
后者每个窗口都从空仓重启，因此**同一个组合在回测与模拟盘里可能选出不同成员**。
这是既有设计差异、不是本轮引入，改动它属于策略语义变更，明确列入 §十。

**修法**（`paper/live_combo.py`，三处）：

1. 收窄捕获：只吞 `(ValueError, KeyError, IndexError, ZeroDivisionError, pd.errors.PandasError)`，
   其余原样上抛（CLAUDE.md §五：不要为「理论上不会发生」的分支写防御性 try）。
2. 出声：`logger.warning("combo member %s scoring failed at bar %d: %s", member.key, i, exc)`，
   并把失败计入 `self.scoring_failures: dict[str, int]`。
3. 不静默兜底：`_choose_member` 在**全部** eligible 成员本根都打分失败时，
   保持在任者——包括 v1 没有覆盖的第 ② 条路径（`active_key not in scores`，即在任者被 regime gating
   判为不合格）：此时若**本根全员打分失败**，不得改选 `eligible[0]`，仍保持 `active_key`；
   只有 `active_key is None`（首次选择）才落到第一个，因为那时没有在任者可保。

   **[v2·P1-11] 本轮不加 UI 落点，理由与去处都写死在这里。**
   v1 要求「向会话追加一条 note，由 `paper/session.py` 的既有 `_push` 通道下发
   （`{"type":"note"}` 已有前端消费点，实施节点需先确认；若无则改走 `signal.reason`）」。
   实测两条路都不通：`paper/session.py` 的 `_push` 类型全集是
   **`status` / `candle` / `equity` / `signal` / `position` / `trade`——没有 `note`**；
   而 `signal` 只在 `self.strategy.on_candle(...)` **产出信号时**才推（`session.py:337-340`），
   「全员打分失败」这一根 K 线恰恰**不产出任何信号**，那条路上根本没有帧可搭。
   §九 R10 已经识别出「又造一个没人读的 wire 字段（本仓第 4 次）」这个风险，却把决定权留给了实施节点
   ——一条已知走不通的路，不该以「实施节点须先确认」的形式交付。

   因此**本轮 D6 只做服务端**：收窄 `except` + `logger.warning` 出声 + `scoring_failures` 计数
   + 保持在任者。这已经把「静默」变成「可诊断」（backend.log 有名有姓、计数可断言）。
   UI 落点需要在 `status` 载荷加字段**并同时**在模拟盘面板加读点，属 UI 改动，
   与 §十 第 10 条冲突，作为**具名跟进**列入 §十第 11 条，不留悬念。

---

### D7 · P3 · SQLite 外键从未启用（护栏缺失，非现网故障）

**取证**：§2.1(f)。`db/session.py:46-52` 的 `_configure_sqlite` 设了 `busy_timeout` 与 `journal_mode=WAL`，
**没有 `foreign_keys`**；SQLite 默认 OFF，实测连接读到 `0`。开发库**当前零孤儿行**。

**根因**：8 处 `ForeignKey` 声明在运行期**完全不生效**——它们今天只是 SQLAlchemy 的元数据装饰。
所有删除路径靠手写清理子表，而**没有任何东西在保证这件事**：将来任意一次新增子表 / 新增删除路径，
漏掉一张表就会静默漏出孤儿行，`git diff` 上看不出来，任何门禁都不会红。

**诚实标注**：这是**护栏缺失**，不是正在发生的数据损坏。之所以仍要在本轮做，是因为它的成本是一行，
而它的收益是把一整类未来缺陷从「静默」变成「立即 IntegrityError」。

**修法**：`_configure_sqlite` 增 `cursor.execute("PRAGMA foreign_keys=ON")`，新增一条用例断言
（a）连接上该 pragma 为 1；（b）删除一个仍有 `trades` 子行的 `strategy_runs` 会抛 `IntegrityError`。

**风险与处置纪律**（重要）：打开外键**可能让既有删除路径变红**。
若 G3 出现红，**那条红本身就是一个真缺陷**（说明该路径此前一直在漏孤儿行）：
正确动作是修删除顺序，**绝不是把 pragma 关回去**。这条纪律必须写进实施节点的硬约束。

---

## 四、技术设计（架构 / 关键代码路径 / 时序）

### 4.1 信任边界（D1）

新增一个**极薄**的 `app/security/` 包，只做纯判定，不持有状态：

```
Settings.security_allowed_origins: str   # 逗号分隔，"" 表示用默认（env: SECURITY_ALLOWED_ORIGINS）
        .allowed_origin_list() -> list[str]

app/security/origins.py
    DEFAULT_ORIGINS: tuple[str, ...]     # ("http://localhost:18761", "http://127.0.0.1:18761")
    normalize(origin: str) -> str        # 去尾斜杠、小写 scheme+host、保留端口
    is_allowed_origin(origin: str | None, allowed: list[str]) -> bool
        # None / ""     -> True   （非浏览器：非 GET/HEAD 的 fetch/XHR 与 WS 握手必带 Origin）
        # "null"        -> False  （不透明源 = 浏览器，见下）
        # 其余：normalize 后精确匹配

app/security/origin_middleware.py
    class OriginGuardMiddleware:                      # 纯 ASGI，非 BaseHTTPMiddleware（P1-10）
        def __init__(self, app, allowed: list[str]): ...
        async def __call__(self, scope, receive, send):
            # scope["type"] != "http" -> await self.app(...)（WS 由 guard_ws_origin 管）

app/api/ws_guard.py
    async def guard_ws_origin(websocket) -> bool      # False 表示已 close(1008)
```

**[v2·P0-1] 关于 `Origin: null`：拒绝，不放行。** 完整论证见 §三 D1 的 [v2·P0-1] 段与两态判据表。
一句话：`null` 是**浏览器**（sandboxed iframe 的不透明源）发出来的，而本轮的威胁模型正是浏览器；
放行它等于给整条边界留一个任意网页三行 HTML 就能走通的正门，WS 侧还能直接读走实盘会话。
Electron 渲染进程加载的是 `http://127.0.0.1:18761`（`desktop/src/main.js:550` `win.loadURL(cfg.WEB_URL)`），
不依赖 `null`；代价只是 `file://` 本地调试页调不通本机 API，写进 README 与 §十。
实施节点须跑一遍 `npm run verify:dist` 确认打包形态不受影响。

**[v2·P0-3] 配置项必须能真的传进打包后的后端。** `desktop/src/lib/config.js` 的 `backendEnv`
只透传 `AGENT_ | LIVE_ | AI_TRADE_ | DISCOVERY_ | OKX_` 五个前缀，而 prod 的 PyInstaller 后端
**不读 `.env`、只读进程 env**。所以配置项命名为 **`SECURITY_ALLOWED_ORIGINS`** 并把 `SECURITY_`
加进那份白名单——否则 R1 的缓解「可覆盖」在真实交付形态下是空的，白名单一旦不匹配就是
「点什么都 403 且无从补救」。该文件注释自陈这类漏改「从不报错，本仓库已为它栽过两次」，
本条是第三次，必须按 `desktop/scripts/config.test.mjs` 里 `DISCOVERY_*` / `OKX_LINK_*` 的同一范式补用例。

请求时序（HTTP）：

```
请求 → OriginGuardMiddleware（纯 ASGI）
        ├ scope 非 http（WS/lifespan） → 直接放行（WS 由 guard_ws_origin 管）
        ├ 无 Origin 头                 → 放行（非浏览器）
        ├ Origin == "null"             → 403 ORIGIN_NOT_ALLOWED（不透明源 = 浏览器）
        ├ Origin ∈ 白名单              → 放行
        └ Origin ∉ 白名单              → 403 ORIGIN_NOT_ALLOWED（不进路由，不碰 DB，不碰 OKX）
      → CORSMiddleware（只对放行者补 ACAO）
      → 路由
```

WS 时序：`websocket.accept()` **之前**先 `guard_ws_origin`；不合规即 `close(1008)`，
**不发任何业务帧**，也不注册订阅（避免泄漏一次快照）。

### 4.2 设计 token 分层（D2）

引入一个此前隐含、现在显式的分层：

```
面色（surface / mark）      --accent  --long  --short          ← 图表、色块、边框、圆点。本轮不动
墨色（ink，正文可读）        --accent-ink  --long-ink  --short-ink   ← 新增，仅用于 text-
反色（on-surface 标签）      --on-accent                        ← 新增，仅用于 accent 面上的文字
```

规则写进 `globals.css` 注释并由 §8 的源码扫描强制：
**`text-` 只能引用 ink / `--text` / `--muted` / `--warn` / `--on-accent`，不得直接引用面色。**

**[v2·P1-5] 配对矩阵（G9 的判定集合，显式列出——不可判定的门禁不是门禁）：**

| 前景 | 背景集合 | 明色实测最低 | 暗色实测最低 |
|---|---|---|---|
| `--accent-ink` | `--bg` / `--surface` / `--surface-2` / `--accent-soft` | **4.61**（soft） | **4.98**（soft） |
| `--long-ink` | 同上 | **4.80**（soft） | **5.31**（soft） |
| `--short-ink` | 同上 | **5.01**（soft） | **4.74**（`--surface-2`） |
| `--on-accent` | `--accent` | **5.09** | **6.54** |
| `--warn` | `--warn-soft`（沿用既有断言） | 5.07 | 6.14 |

暗色 `--short-ink` 一行的 `--accent-soft` 格原为 **4.41（不达标）**，唯一落点是 danger 按钮 hover 底；
按 §三 D2 的 [v2·P1-5] 把该 hover 底改成 `--surface-2` 之后，全 24 格最低 4.61。

### 4.3 HTTP 超时（D3）

18 个文件各自复制 `request` 包装是既有事实（不在本轮重构范围）。
本轮**不合并它们**，只让每一个都 `import { withTimeout, timeoutFor, toApiError } from "./httpTimeout"`，
把决策集中到一个文件、把接线留在原地——回归面最小。
`httpTimeout.ts` 是**纯函数模块**（无 React、无 fetch），因此可以被单测直接覆盖。

**[v2·P1-7]** 接线单位是**调用点**不是文件：实测 20 个 `fetch(` / 18 个文件，
`aiTradeHorizonApi.ts` 一个文件占 3 个且无共享包装。判据由源码扫描守（见 §8.1 G9 第 ④ 项）。

### 4.4 计数器先行（D5）

```
LiveExecutor.__init__:  trades: list[dict]            # 类型不变（P0-2：deque 不支持切片）
                        trade_count / open_count / realized_pnl_total
        _record_trade(t):  trades.append(t); trade_count += 1
                           if t["side"] in ("long","short"): open_count += 1
                           realized_pnl_total += float(t.get("pnl") or 0.0)
                           if len(trades) > CAP + SLACK:      # 按块裁，摊薄 del 的 O(n)
                               del trades[:len(trades) - CAP]
        restore_trades(rows, *, realized_total):
                           trades[:] = rows            # 恢复入口唯一化
                           trade_count = len(rows)
                           open_count  = sum(1 for t in rows if t["side"] in ("long","short"))
                           realized_pnl_total = realized_total
                           # 再施加一次同样的按块裁剪
四个 append 点  →  统一改调 _record_trade
读点 ×3        →  改读计数器
resume         →  改调 restore_trades（三个计数器**就地派生**，零新增持久化键，P1-6）
```

---

## 五、文件 / 模块变更计划

> 计数口径：**新建 = N，修改 = M**。D2 的 116 个文件是机械替换，逐个列出没有信息量，
> 故按目录聚合并给出**可复现的枚举命令**；其余全部逐文件列出。

> **v2 编号约定**：v1 的行号 `#1…#154` 原样保留（它们只是标签）；评审新增的行一律用**字母后缀**
> （`#18a`…）标注，便于与 v1 逐行对照。真实路径数以每小节标题与 §5.8 的合计为准。

### 5.1 D1 信任边界（22 个路径：6 新 + 16 改）

| # | 文件 | N/M | 意图 |
|---|---|---|---|
| 1 | `services/quant/app/security/__init__.py` | N | 新包，空导出 |
| 2 | `services/quant/app/security/origins.py` | N | `DEFAULT_ORIGINS` / `normalize` / `is_allowed_origin` 纯函数 |
| 3 | `services/quant/app/security/origin_middleware.py` | N | `OriginGuardMiddleware`，仅拦 http scope |
| 4 | `services/quant/app/api/ws_guard.py` | N | `guard_ws_origin`，不合规 `close(1008)` |
| 5 | `services/quant/tests/test_origin_guard.py` | N | 纯函数 + 中间件 403/放行/无 Origin 三态 |
| 6 | `services/quant/tests/test_ws_origin_guard.py` | N | 9 条路由逐条拒绝恶意 Origin；`ws_market` 静默反向用例重跑 |
| 7 | `services/quant/app/config.py` | M | 新增 `security_allowed_origins: str` + `allowed_origin_list()`（P0-3） |
| 8 | `services/quant/app/main.py` | M | `allow_origins` 收紧；`OriginGuardMiddleware`（纯 ASGI）装在 CORS 之前 |
| 9 | `services/quant/app/api/agent.py` | M | WS 路由加守卫（1 条） |
| 10 | `services/quant/app/api/ai_auto.py` | M | WS 路由加守卫（2 条） |
| 11 | `services/quant/app/api/discovery.py` | M | 同上 |
| 12 | `services/quant/app/api/live_auto.py` | M | 同上 |
| 13 | `services/quant/app/api/paper.py` | M | 同上 |
| 14 | `services/quant/app/api/weight_batches.py` | M | 同上 |
| 15 | `services/quant/app/api/weight_models.py` | M | 同上 |
| 16 | `services/quant/app/api/ws_market.py` | M | 同上（零帧改动，见 D1 说明） |
| 17 | `.env.example`（**仓库根**，`services/quant/` 下没有这个文件） | M | 记录 `SECURITY_ALLOWED_ORIGINS` 及其安全含义 |
| 18 | `README.md` | M | 「安全」段：本机 API 的信任边界、为什么不再是 `*`、为什么拒绝 `Origin: null`、`file://` 调试页的影响、403 排障 |
| **18a** | `desktop/src/lib/config.js` | M | **[P0-3]** `backendEnv` 白名单加 `SECURITY_` 前缀——否则打包后该配置项恒失效 |
| **18b** | `desktop/scripts/config.test.mjs` | M | **[P0-3]** 按 `DISCOVERY_*` / `OKX_LINK_*` 两条既有用例的同一范式补一条透传用例 |
| **18c** | `desktop/README.md` | M | **[P2-14]** §五「端口同步点」由**五处**改为**六处**，第 6 处 = `app/security/origins.py` 的 `DEFAULT_ORIGINS` |
| **18d** | `docker-compose.yml` | M | **[P2-14]** `web`/`quant` 补 `SECURITY_ALLOWED_ORIGINS=${SECURITY_ALLOWED_ORIGINS:-}` 透传（远程部署时的唯一入口） |

### 5.2 D2 设计 token（121 个路径：1 新 + 120 改）

| # | 文件 | N/M | 意图 |
|---|---|---|---|
| 19 | `apps/web/app/globals.css` | M | 新增 4 个 token（明/暗）+ 分层规则注释 |
| 20 | `apps/web/components/ui/index.tsx` | M | `Button` primary → `--on-accent`；danger 文字 → `--short-ink`、**hover 底 `--accent-soft` → `--surface-2`**（P1-5） |
| 21 | `apps/web/components/Agent/AiTradeHorizonFields.tsx` | M | 删掉 `var(--on-accent,#fff)` 的内联回退 |
| 22 | `apps/web/components/ui/contrastTokens.source.test.ts` | N | **`.tsx` 用法扫描 + 未定义 token 回退扫描 + `fetch` 超时扫描**：见 §8.1 G9 ②③④ |
| 23 | `apps/web/components/ui/warnToken.source.test.ts` | M | 并入通用扫描，保留 `--warn` 既有断言 |
| **22a** | `apps/web/app/globals.source.test.ts` | M | **[P2-13]** CSS 取值 + WCAG 重算（§4.2 的配对矩阵）扩进这里——它**已经**是 globals.css token 的门禁归属地，且**已经**内置 `relativeLuminance`/`contrastRatio` 与「token 须在自己配对底上达标」的先例 |
| 24–119 | `apps/web/components/**/*.tsx` | M | 共 **117 个非测试文件**：116 个含 301 处 `text-[var(--short\|long\|accent)]` → `-ink`，**外加 `AgentMessageBubble.tsx`**（见下）；7 处 `bg-[var(--accent)]` 同行 `text-white` → `text-[var(--on-accent)]` |

> 枚举命令（实施节点直接用，勿凭记忆——**两条都要跑，只跑第一条会漏**）：
> `grep -rln "text-\[var(--short)\]\|text-\[var(--long)\]\|text-\[var(--accent)\]" apps/web/components --include=*.tsx`  → **116** 个文件
> `grep -rln "bg-\[var(--accent)\].*text-white" apps/web/components --include=*.tsx`  → **7** 个文件
> **[v2·P2-12]** 第二条的 7 个文件里，`components/Agent/AgentMessageBubble.tsx` **不在**第一条的 116 里
> （它只有 `bg-[var(--accent)] text-white`，没有 `text-[var(--…)]`），
> 而它渲染的是 **Agent 面板中用户自己每一条消息的气泡**（白字 on 赭，明 3.28 / 暗 2.69）。
> 只按 116 施工必然漏掉它，且 `tsc`/`vitest` 都不会红——故组件文件数订正为 **117**。
> **[v2·P2-13]** 实测 `apps/web/app/**/*.tsx` 命中为 **0**，v1 表里的 `apps/web/app/**/*.tsx` 是空集，已删；
> 但新扫描的扫描根仍必须**同时含 `components/` 与 `app/`**（既有 `warnToken.source.test.ts` 只扫 `components/`），
> 防止将来漂过去。
> 去重后 D2 实际路径数 = **117 + globals.css + 3 个测试文件 = 121**（#20/#21 已在 117 之内）。

### 5.3 D3 HTTP 超时（20 个路径：2 新 + 18 改）

| # | 文件 | N/M | 意图 |
|---|---|---|---|
| 120 | `apps/web/lib/httpTimeout.ts` | N | `FAST_MS`/`PROXY_TIMEOUT_MS`/`LONG_MS`/`timeoutFor`/`withTimeout`/`toApiError`/写类路径表 |
| 121 | `apps/web/lib/httpTimeout.test.ts` | N | 前缀匹配、写类判定、`AbortSignal` 不覆盖既有 signal、文案分叉、`LONG_MS > PROXY_TIMEOUT_MS` |
| 122 | `apps/web/lib/api.ts` | M | `rawRequest` 接线 |
| 123–139 | `accountApi.ts` `agentApi.ts` `aiBacktestApi.ts` `aiTradeApi.ts` `aiTradeHistoryApi.ts` `aiTradeHorizonApi.ts` `autoTradeSettingsApi.ts` `discoveryApi.ts` `discoveryAssetApi.ts` `factorApi.ts` `liveAutoApi.ts` `liveAutoGroupApi.ts` `tradingApi.ts` `weightBatchApi.ts` `weightExploreApi.ts` `weightModelApi.ts` `workspaceApi.ts` | M | **按调用点接，不按文件接**：合计 20 个 `fetch(`；`aiTradeHorizonApi.ts` **独占 3 个且无共享包装**，三处一并接线并升级为 `ApiRequestError`（P1-7）；`discoveryAssetApi.ts` **保留**自带 signal（走 `withTimeout` 的不覆盖分支） |

### 5.4 D4 嵌套弹窗（2 改）

| # | 文件 | N/M | 意图 |
|---|---|---|---|
| 140 | `apps/web/components/ui/index.tsx` | M | 对话框栈（**专用 `useEffect(…, [open])`**，P1-4）+ 栈顶守卫 + `stopImmediatePropagation`（与 #20 同文件） |
| 141 | `apps/web/components/ui/Modal.test.tsx` | M | 固化 §2.1(c) 的复现用例 **+ 「外层 `onClose` 换身份后栈顶不变」回归用例**（P1-4） |

### 5.5 D5 台账计数器（8 个路径：1 新 + 7 改）

| # | 文件 | N/M | 意图 |
|---|---|---|---|
| 142 | `services/quant/app/live_auto/executor.py` | M | 三个计数器 + `_record_trade` 收敛 4 个 append + `restore_trades` + **`list` 内按块裁剪**（**不用 `deque`**，P0-2） |
| 143 | `services/quant/app/wire_caps.py` | M | 新增 `TRADE_LEDGER_MAX = 20000`；模块 docstring 就地改写（「绝不裁剪会计真值」→「先有计数器再封顶」的新前提） |
| 144 | `services/quant/app/live_auto/session.py` | M | `tradeCount`/`avgPnl` 改读计数器 |
| 145 | `services/quant/app/ai_auto/session.py` | M | 同上 |
| 146 | `services/quant/app/ai_auto/discipline_runtime.py` | M | `opens` 改读 `open_count` |
| 147 | `services/quant/app/ai_auto/resume.py` | M | `:279` 改调 `restore_trades(...)`；三个计数器**就地派生，零新增持久化键**（P1-6） |
| 148 | `services/quant/tests/test_executor_ledger_counters.py` | N | 结构扫描（append 必经 `_record_trade`）+ 封顶后计数仍准 + **`tail_trades(executor.trades)` 在封顶后仍可用**（钉死 P0-2） |
| 149 | `services/quant/tests/test_ai_trade_resume_cold.py`（既有） | M | 断言恢复后三个计数器与暂停前**逐位相同**（不是「不归零」） |

### 5.6 D6 组合打分（2 个路径：1 新 + 1 改）

| # | 文件 | N/M | 意图 |
|---|---|---|---|
| 150 | `services/quant/app/paper/live_combo.py` | M | 收窄 except + 日志 + `scoring_failures` + 全员失败时保持在任者（含 `active_key not in scores` 那条路径） |
| ~~151~~ | ~~`services/quant/app/paper/session.py`~~ | — | **[P1-11] 本轮删除**：该文件的 `_push` 类型全集里没有 `note`，`signal` 又只在产出信号时才推，两条通道都不通；UI 落点转为 §十第 11 条的具名跟进 |
| 152 | `services/quant/tests/test_live_combo_scoring.py` | N | 全员抛异常时不切换成员（**按 `active_key is None` 与 `active_key not in scores` 两条真实路径写**，P2-15）、有日志、有计数 |

### 5.7 D7 外键（2 个路径：1 新 + 1 改）

| # | 文件 | N/M | 意图 |
|---|---|---|---|
| 153 | `services/quant/app/db/session.py` | M | `PRAGMA foreign_keys=ON` |
| 154 | `services/quant/tests/test_db_foreign_keys.py` | N | pragma 为 1；删父留子抛 `IntegrityError` |

### 5.8 合计（v2 重算）

| 小节 | 去重后路径数 | 新 | 改 |
|---|---|---|---|
| 5.1 D1 | 22 | 6 | 16 |
| 5.2 D2 | 121 | 1 | 120 |
| 5.3 D3 | 20 | 2 | 18 |
| 5.4 D4 | +1（`Modal.test.tsx`；`ui/index.tsx` 与 D2 重复） | 0 | 1 |
| 5.5 D5 | 8 | 1 | 7 |
| 5.6 D6 | 2 | 1 | 1 |
| 5.7 D7 | 2 | 1 | 1 |
| **合计** | **176** | **12** | **164** |

加上本文件 `spec.md` 自身，**staged 路径数按 177 计**。

> **v1 的「154 / 153」是算错的**：它把 `#24–119` 这 96 个编号槽位当成了 96 个文件，
> 而那一行实际承载 116 个文件。v1 口径下的真实值应为 173；v2 在此基础上
> `+4`（D1 的 18a–18d）`+1`（22a `globals.source.test.ts`）`+1`（D2 的 `AgentMessageBubble.tsx`）
> `−1`（D6 删除 `paper/session.py`）`−1`（v1 重复计入的 `ui/index.tsx`）= **176**。
> 去重说明：`ui/index.tsx` 在 D2（#20）与 D4（#140）各出现一次，实际是同一个文件、同一批改动，只计一次。

---

## 六、接口设计

### 6.1 新增配置项（后端 `.env` / `Settings`）

| 键 | 类型 | 默认 | 语义 |
|---|---|---|---|
| `SECURITY_ALLOWED_ORIGINS` | `str`（逗号分隔） | `""` | 空 = 用 `DEFAULT_ORIGINS`。显式配置即**完全替换**默认（不做合并，避免「以为收紧了其实没有」） |

`Settings.allowed_origin_list()` 返回 `list[str]`；空串 → `list(DEFAULT_ORIGINS)`；
逐项 `strip()` 后丢弃空项；**不接受 `*`，也不接受 `null`**——传任一即 `ValueError` 并在启动期报错退出
（一个「安全开关」允许被一键关掉，等于没有这个开关；`null` 见 §三 D1 的 [v2·P0-1]）。

**[v2·P0-3] 为什么前缀是 `SECURITY_`（不是裸 `ALLOWED_ORIGINS`）**：
`desktop/src/lib/config.js` 的 `backendEnv` 用**前缀白名单**决定哪些 env 进得了后端子进程，
现有五个前缀 `AGENT_ | LIVE_ | AI_TRADE_ | DISCOVERY_ | OKX_`；而 prod 的 PyInstaller 后端
**不读 `.env`、只读进程 env**。裸名 `ALLOWED_ORIGINS` 一个前缀都不沾 ⇒ 打包形态下这个开关恒失效，
白名单一旦不匹配即「点什么都 403 且用户无从补救」。故取 `SECURITY_` 并把它加进白名单（§5.1 #18a/#18b）。

**[v2·P2-18] 三条生效路径要分清**（这正是上一条的成因）：
`.env.example` 在**仓库根**（`services/quant/` 下只有 `.env`，没有 `.env.example`）；
`Settings.model_config` 的 `env_file=".env"` 是**相对 CWD**，dev 下即 `services/quant/.env`；
**prod 完全不读任何 `.env`**，只认由 `backendEnv` 注入的进程 env。

### 6.2 新增错误码（HTTP / WS）

| 通道 | 码 | 触发 | 载荷 |
|---|---|---|---|
| HTTP 403 | `ORIGIN_NOT_ALLOWED` | `Origin` 存在且不在白名单（含 `Origin: null`） | `{"error":{"code":"ORIGIN_NOT_ALLOWED","message":"请求来源未被允许（请检查 SECURITY_ALLOWED_ORIGINS）"}}` |
| WS close | `1008` | 同上 | 无帧（`accept()` 之前即关，不泄漏一次快照） |
| 前端 | `TIMEOUT`（`ApiRequestError.code`，`status=0`） | fetch 超时 | 读类/写类两种文案，见 D3 |

**wire 兼容性**：`ORIGIN_NOT_ALLOWED` 与 `TIMEOUT` 都走**既有**错误信封
（`{"error":{"code","message"}}` / `ApiRequestError`），**零新增 wire 形状**。

### 6.3 CSS 契约（新增 token）

`--accent-ink` / `--long-ink` / `--short-ink` / `--on-accent`，明暗两套，取值见 D2。
**契约**：任何 `text-*` 只能引用 ink 族、`--text`、`--muted`、`--warn`、`--on-accent`。
由 §8.1 G9 的源码扫描强制，违反即测试红。

### 6.4 不变的接口

零 REST 路径新增 / 删除 / 改名；零 WS 路径变化；零 CLI 变化；
`api.ts` 的所有导出签名不变（超时是包装内部行为）。

---

## 七、数据模型

**零 DB 迁移、零建表、零 ALTER。** 本轮不新增任何表或列。

两处内存形状变化，均在进程内：

```python
# app/live_auto/executor.py
self.trades: list[dict]         # **类型不变**（P0-2：deque 不支持切片，会炸 wire_caps.tail_trades）
                                # 新增：len 超过 TRADE_LEDGER_MAX(20000) 时在 _record_trade 内按块裁剪
self.trade_count: int           # 新增，单调递增
self.open_count: int            # 新增，仅 side in ("long","short") 时 +1
self.realized_pnl_total: float  # 新增
```

**[v2·P1-6] continuity 载荷零变化——三个计数器是派生量，不落库。**
v1 曾要在会话状态 JSON 里加 `tradeCount/openCount/realizedTotal` 三个键，
并为「老载荷缺键」设计 `or 0` 兜底 + 「UI 须按『本次恢复后』口径标注」的降级。整段删除，理由：

`ai_auto/resume.py:433 rebuild_continuity` 的 docstring 明写
「**派生量绝不落库**：落两份必然漂移，而这里漂移的是真实资金的盈亏显示」，
且它本来就从 DB 的**全量 `Trade` 行**重建 `trades`（无裁剪）。所以：

```python
trade_count        = len(continuity["trades"])
open_count         = sum(1 for t in continuity["trades"] if t["side"] in ("long", "short"))
realized_pnl_total = continuity["realizedPnlUsd"]     # 早已存在
```

⇒ **零新增持久化键、零 wire 变化、零老库兼容分支**，恢复后计数器与暂停前逐位相同，
也就不存在需要在 UI 上解释的降级口径。

```python
# app/paper/live_combo.py
self.scoring_failures: dict[str, int]   # 新增，memberKey -> 本会话打分失败次数
```

---

## 八、测试与验收标准

### 8.1 门禁（G1–G10；G1/G2/G4 有本轮基线，G3/G5 继承上一轮）

| 门禁 | 命令 | 通过判据 |
|---|---|---|
| G1 | `cd apps/web && npx tsc --noEmit` | exit 0（基线 exit 0） |
| G2 | `cd apps/web && NODE_ENV=test npx vitest run` | 0 failed；文件数 ≥ 169 + 新增，用例数 ≥ 1858 + 新增（基线 169/1858） |
| G3 | `cd services/quant && .venv/Scripts/python -u -m pytest -q` | CI 视角 0 failed。**排除**未跟踪在制文件 `tests/test_factor_library_expansion.py`，并在报告里写清排除项与各自贡献条数（基线 3648 passed）。**[v2·P2-17]** 排除项以 R12 要求的**当轮基线**为准：若该文件已被清理或合入，如实写「本轮无排除项」，**不得**照抄一条「排除了不存在的东西」的假声明 |
| G4 | `cd services/quant && .venv/Scripts/python -m ruff check .` | `All checks passed`，exit 0 |
| G5 | `cd desktop && npm run check && npm test` | 全绿（基线本地 31 pass）。**[v2·P0-3]** 本轮它同时是 `SECURITY_` 前缀透传用例（#18b）的门禁——`config.test.mjs` 归 `npm test` 管 |
| G6 | `cd apps/web && env -u NODE_ENV npx vitest run` | 与 G2 **同值**（证明没有环境耦合） |
| G7 | `cd apps/web && npx next build` | `Compiled successfully`，exit 0 |
| G8 | 干净检出：`git write-tree` + `git archive` → `cd desktop && node --test` | 与上一轮干净检出基线一致（24）+ 本轮新增 |
| **G9** | 新增源码扫描（**落点已按 P2-13 拆分**：① 在 `app/globals.source.test.ts`；②③④⑤ 在 `components/ui/contrastTokens.source.test.ts`） | ① 解析 `globals.css` 真实取值并**在测试里重算** WCAG，按 §4.2 **显式配对矩阵**逐格 ≥ 4.5（三个 ink × {`--bg`,`--surface`,`--surface-2`,`--accent-soft`} + `--on-accent`×`--accent` + `--warn`×`--warn-soft`，共 15 格 × 2 主题）；② `components/` **与** `app/` 下 `text-[var(--short)]` / `text-[var(--long)]` / `text-[var(--accent)]` 出现次数为 **0**；③ 不存在 `var(--x, fallback)` 指向 `globals.css` 未定义的 token；④ **[P1-7]** `apps/web/lib/**` 下每一个 `fetch(` 都经 `withTimeout(...)`，零例外（当前 20 个调用点）；⑤ **[P1-8]** `LONG_MS` **严格大于** `next.config.mjs` 的 `experimental.proxyTimeout`（读两个文件比对数值） |
| **G10** | 新增 `test_origin_guard.py` + `test_ws_origin_guard.py` | 恶意 Origin 在 HTTP 上 403、在 9 条 WS 上 close(1008)；**`Origin: null` 在 HTTP 与全部 9 条 WS 上一律拒绝**（P0-1，单列用例）；无 Origin 放行；白名单放行；`allow_origins` 里不含 `*`；`allowed_origin_list()` 对 `*` 与 `null` 抛 `ValueError` |

> **G9 必须在测试里重算，不能硬编码期望值。** 第 2 轮的教训原文：
> 「为 AA 而生的 token 在自己的配对上不达 AA」——只有让测试自己算，
> 才能挡住「改了颜色忘了改断言」。
>
> **跑测纪律（本仓既有）**：全量 pytest **不可并发**跑第二个 pytest；
> 必须用 `python -u`（否则 Windows GBK 控制台会吞输出）；
> 报告里出现的每一个数字都必须是**当次实跑**的。

### 8.2 逐缺陷验收标准

**D1**
- `curl -H "Origin: https://evil.example.com" -X POST http://127.0.0.1:18760/api/trading/close-all` → **403 `ORIGIN_NOT_ALLOWED`**，且后端日志显示**未触达路由**（不出现任何 OKX 调用）。
- **[v2·P0-1]** 同一请求把头换成 `Origin: null` → **同样 403**。这一条必须单列——它是 sandboxed iframe
  从任意网页发起攻击时的真实取值，放行它等于整条边界作废。
- **[v2·P0-1]** WS 侧同样单列：`Origin: null` 的 `ws://127.0.0.1:18760/api/ws/ai-trade/<id>` 握手被 close(1008)，
  **未收到任何一帧**（WS 无 CORS，响应可读，所以这条比 HTTP 那条更要紧）。
- 同一请求**不带** `Origin` → 行为与今天完全一致（回归保护：`scripts/smoke_e2e.py` 必须仍然 PASS）。
- 浏览器打开 `http://localhost:18761`，全功能回归：K 线、回测、模拟、AI 会话、9 条 WS 全部正常。
- `wss://` 直连测试：带恶意 `Origin` 的 `ws://127.0.0.1:18760/api/ws/market` 握手被 close(1008)，**未收到任何一帧**。
- `ws_market` 的第 2 轮反向用例（idle 静默）**重跑通过**。
- **[v2·P0-3]** 打包形态实证：`npm run verify:dist` 全绿；并在 `userData/agent.env` 里写一条
  `SECURITY_ALLOWED_ORIGINS=http://127.0.0.1:18761` 后重启，确认后端进程 env 里**确实收到**了它
  （否则这个开关在真实交付形态下是个摆设）。

**D2**
- G9 ①②③ 全绿（配对矩阵 15 格 × 2 主题逐格 ≥ 4.5）。
- **[v2·P1-9] 暗色的判据要写清射程**：人工目视 5 个最密文件（按 `grep -c` 排序取前 5）在**明/暗**两主题下截图对比——
  **除下列两项外**，暗色**逐像素不变**（三个 ink 的暗色取值等于原面色）；浅色仅文字变深，色相不变。
  两项例外**必须单独目视确认**（它们是本轮最显眼的视觉变化，写成「逐像素不变」会让验收必然判失败）：
  1. 7 处 `bg-[var(--accent)]` 上的文字 `text-white` → `--on-accent`（暗 `#1a1915`），**白字变近黑字**
     ——含 `Button variant="primary"` 与 Agent 面板的用户消息气泡；
  2. `Button variant="danger"` 的 hover 底 `--accent-soft` → `--surface-2`（P1-5）。
- `Button variant="primary"` 与 `variant="danger"`（含 **hover 态**）在明暗两主题下实测对比度 ≥ 4.5。
- 图表 series 颜色**逐字节不变**：`CandleChart.tsx:216-217` 与 `:301-302` 的
  `upColor: cssVar("--long")` / `downColor: cssVar("--short")` 四行必须在 `git diff` 中**不出现**。
  （注意：`CandleChart.tsx` **整体会有 diff**——它的 `:601-602` 那块「无法连接 OKX」错误卡用的是
  `border-[var(--short)]` + `text-[var(--short)]`，其中 `text-` 那一处属于本轮要迁的 301 处之一，
  `border-` 那一处**不迁**。把验收写成「整个文件零 diff」是错的，会逼实施节点漏改一处错误文案。）

**D3**
- 单测：`timeoutFor("/strategies/7/backtest") === LONG_MS`；`timeoutFor("/health") === FAST_MS`；
  `withTimeout({signal: mySignal}, 1000).signal === mySignal`。
- 手工：把后端断点挂住（或 `kill -STOP`），点「停止会话」→ **20 秒内**出现
  「结果未知，请刷新核对」而不是转圈或「失败」。
- **[v2·P1-8]** `LONG_MS` **严格大于** `next.config.mjs` 的 `proxyTimeout`（315000 > 300000），
  由 G9 ⑤ 断言。写成「相等」是错的：客户端计时从 `fetch()` 起算、代理计时从 Next 收到请求才起算，
  等值时客户端必先放弃，正落进 D3 自己要避免的误报。
- **[v2·P1-7]** G9 ④ 绿：`apps/web/lib/**` 下 20 个 `fetch(` 无一遗漏；
  且 `aiTradeHorizonApi.ts` 的三处错误已升级为 `ApiRequestError`（否则超时文案在这三条路径上没有落点）。

**D4**
- `Modal.test.tsx` 新用例通过：内层 Esc → 内层 `onCancel` 调 1 次、外层 `onClose` 调 **0** 次。
- **[v2·P1-4] 第二条用例（缺了它，P1-4 那条复活路径无人看守）**：内层开着时 `rerender` 外层，
  给它换一个**新身份**的 `onClose`（模拟 `FactorLab.tsx:301` 的内联箭头在父组件重渲染时的行为），
  再按 Esc → 外层 `onClose` 仍为 **0** 次。
- 手工：标的组管理 → 删除某组 → Esc → 只关掉确认框，标的组弹窗仍在。

**D5**
- 结构用例：`executor.py` 中 `self.trades.append(` 出现次数为 **1**（只在 `_record_trade` 内）。
- 行为用例：灌 25000 笔成交后 `len(executor.trades) == 20000` 而 `executor.trade_count == 25000`；
  `discipline_runtime` 的 `opens` 与真实开仓数一致。
- **[v2·P0-2] 容器类型用例**：在**封顶已触发**的状态下调 `tail_trades(executor.trades)` 必须正常返回
  长度 ≤ 2000 的新 list。这条专门钉住「不得换成 `deque`」——`deque` 不支持切片，
  换回去它立刻红（实测 `TypeError: sequence index must be integer, not 'slice'`）。
- **[v2·P1-6] 恢复用例**：pause → 进程重启 → resume 后三个计数器与暂停前**逐位相同**
  （不是「不归零」——它们由 DB 全量 `Trade` 行派生，本就该精确相等）；
  且 `git diff` 中**不出现**任何新增的持久化键（`tradeCount`/`openCount`/`realizedTotal` 一个都不许落库）。

**D6**
- 用例（路径 ①）：`active_key is None` + 全员抛 `ValueError` → 落到 `eligible[0]`（无在任者可保），
  但 `scoring_failures` 全部 +1、`caplog` 中有 warning。
- **[v2·P2-15] 用例（路径 ②，这条才是新行为）**：`active_key` 已存在但被 regime gating 判为不合格
  （`active_key not in scores`）+ 全员抛异常 → **`active_key` 保持不变**。
  修法前它会切到 `eligible[0]`，修法后不会。
  ⚠️ 不要只写「全员抛异常时 `active_key` 不变」这一条：在任者仍在 `scores` 里时，
  既有的 `switch_margin` 逻辑**本来就**保住了它（`0.0 > 0.0` 为假），那条用例在修法前就是绿的，什么都没验证。
- 用例：只有部分成员抛 → 正常在成功者中选优。
- 本轮**不验收任何 UI 落点**（P1-11：通道不存在，已转 §十第 11 条具名跟进）。

**D7**
- 用例：pragma 为 1；删父留子抛 `IntegrityError`。
- G3 若因此变红：逐条判定为**真缺陷**并修删除顺序；**报告里必须列出被修的每一条路径**。

### 8.3 完成定义（DoD）

1. §五 **§5.8 合计的 176 个源码路径 + `spec.md` = 177 个 staged 路径**逐条对账：无表外文件、无表内遗漏。
   （v1 写的「153」是错的算法产物，见 §5.8 的重算说明。）
2. G1–G10 **全部实跑**，报告写出每一项的真实数字；继承自上一轮的数字必须**显式标注为继承**。
3. `docs/diagnoses/`、`ensure-wincodesign*`、`live_auto_repositories.py`、
   `test_factor_library_expansion.py`、`.agentmesh/`、`CLAUDE.md`、`tmps/`
   **仍为未跟踪**；不得使用 `git add -A`、不得 `--no-verify`。
4. 交易热路径的**语义**零变更：`live_auto/executor.py` 只加计数与容器类型，
   任何对下单 / 风控 / 撮合判据的改动即越界。
5. 实施中发现的方案缺陷**就地补写**进本文件新增的「实施过程发现的方案缺陷」段，并在完成报告里逐条列出。

---

## 九、风险与缓解

| # | 风险 | 影响 | 缓解 |
|---|---|---|---|
| R1 | Origin 白名单漏了某个真实入口（如用户改过前端端口、Docker 部署用别的 host） | 应用自己被自己挡住，且症状是「点什么都 403」 | 默认值同时含 `localhost` 与 `127.0.0.1`；`SECURITY_ALLOWED_ORIGINS` 可覆盖 **且该覆盖在打包形态下真的生效**（P0-3：白名单加 `SECURITY_` 前缀 + #18b 用例）；403 的 message 明写「请检查 SECURITY_ALLOWED_ORIGINS」；README 增排障段；`docker-compose.yml` 显式透传（#18d）；**G10 之外还要跑一次真实浏览器全功能回归** |
| R2 | **[v2·P0-1 改写]** 拒绝 `Origin: null` 会挡掉 `file://` 本地调试页 | 用 `file://` 打的调试页调不通本机 API | 这是**刻意**的取舍：`null` 是浏览器 sandboxed iframe 的不透明源，放行它等于三行 HTML 绕过整条边界（WS 侧还能读走实盘会话）。写进 README（「本地调试请用 http 页面」）与 §十；Electron 渲染进程加载 `http://127.0.0.1:18761`，不受影响，由 `npm run verify:dist` 实证 |
| R3 | D2 的 301 处机械替换改错一处（比如把面色用法误改成 ink） | 图表/圆点颜色漂移 | 三重网：G9 ② 断言面色 `text-` 归零、`CandleChart.tsx` 的四行 series 取色要求 `git diff` 中不出现、**暗色主题在 P1-9 划定的射程内逐像素不变**（ink 暗色取值 = 原面色，任何漂移立刻可见）。**[v2·P1-9]** 射程 = 除「7 处 `bg-accent` 上的文字色」与「danger hover 底」外——保住这张网正是 P1-5 选择改 hover 底、而不是改 ink 取值的原因 |
| R4 | 新 ink 取值主观上「太深、不好看」 | 设计争议 | 取值由 WCAG 复算产生、色相与原色一致（同族更深），与第 2 轮 `--warn #8f5209` 的做法**同一套方法论**；若要调，必须重跑 G9 而不是改断言 |
| R5 | D3 的 `FAST_MS=20s` 对某个慢接口太短 | 误报超时 | 长跑前缀表逐条来自本轮实读；实施节点若发现遗漏，**加进前缀表**而不是抬高 `FAST_MS`；`LONG_MS` 与 `proxyTimeout` 由测试锁死等值 |
| R6 | 写类超时文案改成「未确认」被误读为「失败了但话说得含糊」 | 用户困惑 | 文案里带**明确的下一步动作**（刷新持仓/会话核对），并且**只对写类路径生效**，读类仍说「超时」 |
| R7 | D5 的封顶让某个没被本轮枚举到的读点低报 | 统计失真 | 读点已逐个实读列表（D5 表格）；封顶单独成批、单独跑 G3；`TRADE_LEDGER_MAX=20000` 远超现实会话，即使漏了读点也几乎不会触发 |
| ~~R8~~ | ~~D5 的 resume 载荷加键，老会话缺键~~ | — | **[v2·P1-6] 整条删除**：三个计数器改为从 DB 全量 `Trade` 行**就地派生**，不落库、无新键、无老库兼容分支，恢复后与暂停前逐位相同 —— 风险随修法一起消失，而不是被缓解 |
| **R8′** | **[v2·P0-2 新增]** 实施节点把 `self.trades` 换成 `deque` | 两条会话的快照下发路径直接抛 `TypeError`（`tail_trades` 切片） | 修法已明确要求保持 `list` + 块裁剪；§8.2 D5 有一条**专门在封顶已触发状态下调 `tail_trades`** 的用例，换回 `deque` 立刻红 |
| R9 | D7 打开外键让既有删除路径变红 | G3 变红 | 已写明纪律：红即真缺陷，修删除顺序，**不许关回 pragma**；D7 单独成批，红了也不牵连其它批。**[v2·P2-16] 出口阀**：若受影响的删除路径修复面**超出本轮 176 路径计划**，D7 **整批回退**，并把实测的红清单连同 pragma 一起挂进 §十作为下一轮的具名跟进——不允许把整轮卡死在最后一批（这也正是把 D7 排在最后的本意） |
| R10 | D6 的「note」下行通道没有前端读点 | 又造一个没人读的 wire 字段（本仓第 4 次） | **[v2·P1-11] 已实测证伪并当场决策**：`paper/session.py` 的 `_push` 类型全集里没有 `note`，`signal` 又只在产出信号时才推 ⇒ 两条通道都不通。本轮 D6 **只做服务端**（日志 + 计数 + 保持在任者），UI 落点转 §十第 11 条。风险已消除，不是被缓解 |
| R11 | 一次改 176 个路径，某一批回退时相互纠缠 | 交付风险 | §十一 按缺陷分 7 批，批间**无代码依赖**；`ui/index.tsx` 被 D2/D4 共用，故这两批**必须相邻且同批验证** |
| **R13** | **[v2·P1-10 新增]** `OriginGuardMiddleware` 被实现成 `BaseHTTPMiddleware` | 与本轮 D3 引入的客户端 abort 叠加 ⇒ `RuntimeError: No response returned` 从罕见变常规 | 修法已明确要求纯 ASGI 形态；实施节点若因任何理由改用 `BaseHTTPMiddleware`，须在报告里单独说明并补一条「长请求中途 abort 不产生 500」的用例 |
| **R14** | **[v2·P2-12 新增]** 只按 116 个文件施工 | 漏掉 `AgentMessageBubble.tsx`（用户消息气泡白字 on 赭），且 `tsc`/`vitest` 都不会红 | §5.2 已订正为 117 并给出**第二条**枚举命令；G9 ② 的扫描只能挡 `text-[var(--面色)]`，挡不住 `bg-accent + text-white`，故这一条靠文件对账 + 明暗目视兜底 |
| R12 | 本轮未实跑 G3/G5，基线是继承的 | 基线可能已漂移 | 实施节点**第一件事**是跑一遍 G3/G5 取当轮基线，再动代码；若与继承值不符，先解释差额来自哪个未跟踪文件 |

---

## 十、明确不做（Out of scope）

1. **给本机 API 加共享密钥 / token 鉴权。** Origin 白名单 + 回环绑定对「浏览器投毒」这个威胁模型
   是**相称**的处置；token 需要在 Electron ↔ Next ↔ FastAPI 三方之间做分发与轮换，
   是一次独立的架构轮次。本轮把边界立起来，不把机制堆上去。
2. ~~**拒绝 `Origin: null`**（见 R2）。~~ **[v2·P0-1] 已上收进本轮范围**：`null` 一律拒绝。
   反过来，**本轮明确不做**的是「为 `file://` 本地调试页提供替代通道」——需要调试请用 http 页面。
3. **私钥的静态加密**。`okx_accounts` 表里 `api_key/secret_key/passphrase` 是明文
   （`api/trading.py:61` 注释自陈「用库内明文密钥」）。加密需要密钥托管方案
   （DPAPI / Keychain / 用户口令），属独立轮次；本轮只把**网络侧**的洞堵上，
   并在 README 里如实写清「库文件本身等同于凭证」。
4. **`broker.trades`（`engine/simulator.py`）的封顶**。它是回测/模拟撮合的完整产物，
   会被整体落库并参与指标计算，封顶会改变回测结果本身。
5. **回测车道与模拟盘车道的组合打分口径统一**（D6 记录的那处分歧）。
   那是策略语义变更，须配套等价性门禁，独立成轮。
6. **前端 ESLint 接入**（第 2 轮 §十 第 2 条至今挂账）。需要先在 116+ 文件上测量整改量，
   与本轮的四个主题都不同轴。
7. **9 个 > 800 行文件的拆分**（`schemas.py` 2494 行、`ai_auto/session.py` 2216 行……）。
   违反 CLAUDE.md 阈值，但纯结构性改动、diff 面积巨大，须独立成轮并配套等价性门禁。
8. **18 个 `*Api.ts` 的 `request` 包装合并**。本轮只往里接一行超时；合并是重构，回归面与本轮目标不匹配。
9. **把「全会话」统计从展示缓冲上解耦**（第 2 轮 §十 第 7 条）。D5 的计数器是它的前置条件，
   完整的解耦（服务端权威计数下发）留给下一轮。
10. **任何新功能、任何 UI 布局改动、任何策略 / 风控参数调整。**
11. **[v2·P1-11] 组合打分失败的 UI 落点。** D6 本轮只做服务端（日志 + `scoring_failures` 计数 + 保持在任者）。
    要让用户看见，需要在 `paper/session.py` 的 `status` 载荷加字段**并同时**在模拟盘面板加读点
    ——这是 UI 改动（与上面第 10 条冲突），且必须一次做完两侧，否则就是本仓第 4 次「有写点没读点」。
    列为下一轮的**具名跟进**，不是"以后再说"。
12. **[v2·P2-16] D7 外键回退挂账口（条件性）。** 若打开 `PRAGMA foreign_keys` 导致的删除路径修复面
    超出本轮 176 路径计划，D7 整批回退，pragma 与实测红清单一并挂进下一轮。
13. **[实施节点] D7 已按第 12 条整批回退——下一轮的具名跟进。** 触发条件已实测发生：打开外键后
    `DELETE FROM strategies WHERE builtin = 0` 撞上仍在引用它们的 `strategy_runs`（8 条红）。
    根因不是漏写删除顺序，而是「删策略保留其交易历史」这个**有意的产品语义**恰好依赖外键关着
    （`factors/service.delete_set` 的 docstring 明写了这一点）。下一轮要做的是一次**明确的 DB 迁移**：
    `strategy_runs.strategy_id` 改可空 + `ondelete="SET NULL"`，`delete_strategy` / `delete_set`
    随之把子行的 `strategy_id` 置空而不是删掉它们——历史保住、孤儿行由数据库而不是靠运气来保证。
    做完这一步再打开 `PRAGMA foreign_keys=ON`，并把 `test_db_foreign_keys.py`（本轮已写好、随回退删除）
    重新加回来。完整证据见「实施过程发现的方案缺陷 · I-14」。

---

## 十一、实施顺序（7 批，批间无代码依赖）

```
批次 0  取当轮基线（R12）：先跑一遍 G3 + G5，不动任何代码，把两个数字记下来
批次 1  D1 信任边界（#1–#18 + #18a–#18d） → 跑 G3 + G5 + G10 + 浏览器全功能回归 + smoke_e2e
                                            + verify:dist（含 SECURITY_ALLOWED_ORIGINS 真透传实证）
批次 2  D2 token 定义与原语（#19–#23、#22a）→ 跑 G1 + G9①
批次 3  D2 机械替换（117 个组件文件）+ D4（#140–#141） → 跑 G1 + G2 + G9①②③ + 明暗双主题目视
                                            （目视含 P1-9 的两项例外）
批次 4  D3 超时（#120–#139）             → 跑 G1 + G2 + G7 + G9④⑤
批次 5  D5 计数器（#142–#149，先计数器后封顶，封顶自成子批） → 每子批各跑一次 G3
批次 6  D6 组合打分（#150、#152）          → 跑 G3
批次 7  D7 外键（#153–#154）             → 跑 G3（此批最容易变红，故排最后；红了按 R9 的出口阀处置）
最后统一跑 G1–G10 全量
```

> **[v2] 批次 0 是新增的**：§二 已如实标注 G3/G5 是**继承**上一轮的数字，R12 也要求先取当轮基线；
> 但 v1 的批次表里没有它，等于把一条硬性前置写成了纸面约定。现在它是第 0 批。

批次 2 与 3 必须相邻：`ui/index.tsx` 被 D2 与 D4 共用，拆开会让「哪一步把测试跑红了」不可分辨
——这是第 2 轮 v2 拆批时立下的同一条判据。

---

## 十二、给评审节点的自陈

**本轮相对前两轮的定位**：前两轮做的是「进程边界」与「流的活性」，本轮做的是「**对外说的话是否属实**」。
四个主题（谁能调用、颜色说什么、等待何时结束、数字是不是真值）都不与前两轮的 D 号重叠，
本文档 §二 的每一条取证都是本轮实跑。

**最值得被质疑的三处，我先写在这里**：

1. **D1 的严重度**。有人会说「绑在 127.0.0.1 就够了」。不够——回环绑定挡的是局域网，
   挡不住**用户自己浏览器里的任意网页**，而本轮实测的预检明确返回了
   `ACAO: *` + `allow-headers: content-type`，即跨源 JSON POST 是被批准的。
   同时必须记住 **CORS 只藏响应、不拦执行**，所以只收 `allow_origins` 是不够的，
   必须有 `OriginGuardMiddleware` 这一层——这是本条修法里最容易被"简化"掉、
   一旦简化就等于没修的地方。
2. **D2 的取值**。三个 ink 是我用 WCAG 2.1 相对亮度复算出来的，不是挑的。
   如果评审认为太深，正确动作是提出新候选并**重算**，不是放宽 G9 的阈值
   ——第 2 轮的评审结论里那句「修法自身携带了它要消灭的那个缺陷」值得在这里再读一遍。
3. **D5 与第 2 轮硬约束 4 的冲突**。第 2 轮明写「对这三者的任何裁剪即视为越界」。
   本轮**显式解除**该约束，但**只在计数器先落地之后**——即第 2 轮当时给出的解除条件。
   实施节点如果颠倒顺序（先封顶后加计数器），那就是第 2 轮预言的那个越界，应当直接回退该批。

**本轮未做而下轮应优先做的三件事**：私钥静态加密、前端 ESLint 接入、
把「全会话」统计从展示缓冲彻底解耦（D5 已备好前置条件）。

> 本节点只创建了 `docs/plans/trust-boundary-and-signal-integrity/spec.md`，
> 未修改任何源文件，未执行 `git commit`。
> 取证期间创建过一个临时探针用例 `apps/web/components/ui/__probe_nested_esc.test.tsx`，
> 已删除，`git status` 复核 `apps/web` 无任何改动。

---

## 评审结论（Review Verdict）

### **有条件通过（Approved with Conditions）**

**为什么是「通过」而不是「打回」**：七条缺陷判断 D1–D7 **无一条被推翻**。评审节点没有采信 v1 的任何数字，
把跨源预检、WCAG 全表、7 项用量计数、starlette 两处内部实现、9 条 WS 路由、8 处 `ForeignKey`、
`executor.py` 的 4 个 append 点全部重新实测了一遍，**逐项吻合**，`tsc --noEmit` 也由评审节点亲跑到 exit 0。
D1 更是一条真实的、可被三行 HTML 触发的、通向真实下单接口的洞——它值本轮的 P0。

**为什么是「有条件」**：v1 的问题不在识别，在修法。三处修法**自身携带了它要消灭的那个缺陷**——
放行 `Origin: null` 让信任边界形同虚设、`deque` 会炸掉它要保护的那两条快照路径、
往 continuity 里加持久化键正好违反那个模块自己写下的「派生量绝不落库」。
这与第 2 轮评审结论里那句「修法自身携带了它要消灭的那个缺陷」是同一句话的第三次应验。
11 条 P0/P1 已在正文就地整改完毕，**不留一条待办**；下面 8 条是放行的硬约束。

### 放行硬约束（实施节点必须逐条遵守，违反即视为越界并回退该批）

1. **`Origin: null` 一律拒绝**（HTTP 403 / WS 1008），不得以「兼容本地调试页」为由改回放行。
   G10 必须有**单列**的 `null` 用例，HTTP 与 9 条 WS 各一条。
2. **`LiveExecutor.trades` 的容器类型保持 `list[dict]`**，不得改成 `deque`。
   §8.2 D5 那条「封顶已触发状态下调 `tail_trades`」的用例是这条约束的看守，不得删改。
3. **零新增持久化键**：`tradeCount` / `openCount` / `realizedTotal` 一个都不许落进会话状态 JSON。
   `git diff` 里出现任一即越界。恢复后计数器必须与暂停前**逐位相同**。
4. **配置项名为 `SECURITY_ALLOWED_ORIGINS`，且 `desktop/src/lib/config.js` 的白名单必须加 `SECURITY_` 前缀**，
   并按 `config.test.mjs` 的既有范式补用例。少了这一半，这个开关在打包形态下是个摆设。
5. **`OriginGuardMiddleware` 用纯 ASGI 形态**。若确有理由改用 `BaseHTTPMiddleware`，
   须在报告里单独说明，并补一条「长请求中途 abort 不产生 500」的用例（R13）。
6. **对话框栈的 push/splice 必须在独立的 `useEffect(…, [open])` 里**，不得并进 `[open, onClose]` 那个。
   `Modal.test.tsx` 的第二条用例（外层 `onClose` 换身份后栈顶不变）是这条的唯一看守。
7. **D2 施工按 117 个组件文件**，两条枚举命令都要跑；`AgentMessageBubble.tsx` 必须在改动清单里。
   G9 ① 的配对矩阵按 §4.2 的 15 格逐格断言，**在测试里重算，不得硬编码期望值**；
   若认为某个 ink 取值不好看，正确动作是提新候选并重算，**不是放宽阈值**。
8. **批次 0 先跑 G3 + G5 取当轮基线**，再动第一行代码；报告里继承自上一轮的数字必须**显式标注为继承**，
   排除项以当轮实测为准（若 `test_factor_library_expansion.py` 已不存在，就如实写「本轮无排除项」）。

### 交付判据（DoD 的评审侧补充）

- §5.8 的 **176 个源码路径 + `spec.md` = 177** 逐条对账，无表外文件、无表内遗漏。
- G1–G10 **全部实跑**并写出真实数字；G9 由 5 个子项（①CSS 配对矩阵 ②`.tsx` 面色扫描 ③未定义 token 回退
  ④`fetch` 超时覆盖 ⑤`LONG_MS > proxyTimeout`）组成，缺一项即未完成。
- 实施中若发现本文档的新缺陷，就地补写进「实施过程发现的方案缺陷」段并在完成报告里逐条列出
  ——这是前两轮唯一真正拦住过阻断级问题的机制。

### 评审节点的自我约束

本节点**只修改了** `docs/plans/trust-boundary-and-signal-integrity/spec.md` 一个文件，
未触碰任何源代码，未执行 `git commit`。评审期间跑过的命令只有只读的读取/统计/`tsc --noEmit`
与一次 `TestClient` 内存态请求，工作树无任何改动。

> 文档版本：**v2**（评审整改版）。P0 3 条、P1 8 条**全部已在正文闭环**；P2 7 条亦全部采纳。

---

## 实施过程发现的方案缺陷（Issues Found During Implementation）

> 实施节点就地补写。每条都是**在按方案施工的过程中被实测撞到的**，不是纸面推演。
> 处置一律「就地改正 + 写清理由」，没有留待办。

### I-1（阻断级）· `pd.errors.PandasError` 不存在，照方案写会在 **import 期**炸掉整条 AI 交易链

§三 D6 的修法列了收窄捕获的异常元组，其中 `pd.errors.PandasError` 在 pandas 2.2.3 里**根本没有这个名字**
（该名早年已被移除）。照抄的后果不是「少捕一类异常」，而是模块级常量求值直接抛：

```
AttributeError: module 'pandas.errors' has no attribute 'PandasError'
  app/paper/live_combo.py:19  ← app/paper/strategy_factory.py:17 ← app/ai_auto/manager.py:42
```

于是 `from app.ai_auto import get_ai_trade_manager` 全线失败（`__init__.py` 的 PEP 562 惰性导出把它
包装成一句语焉不详的 `ImportError: cannot import name ...`），**整包 AI 自动交易起不来**。

改正：改用 pandas 现存的四个计算类异常（`IndexingError` / `InvalidIndexError` / `DataError` /
`SpecificationError`）加原有的 `ValueError / KeyError / IndexError / ZeroDivisionError`，
并把「不能写 `PandasError`」的理由与实测报错就地写进源码注释。

### I-2（P0）· `colorForReturn`：方案的枚举命令漏掉了**全应用盈亏数字的统一着色点**

§5.2 的两条枚举命令只 grep Tailwind 类（`text-[var(--x)]` / `bg-[var(--accent)] … text-white`），
因此完全看不见**内联样式**这条路。实测 `lib/format.ts:36` 的 `colorForReturn(v)` 返回面色
`var(--long)` / `var(--short)`，被 **83 处**引用且**全部**是 `style={{ color: … }}`——
它实际上就是这个产品里「亏损红与盈利绿」的单一着色点，也正是 D2 §三那张后果表里排第三的那一类
（「301 处正文：盈亏数字、错误文案」）中占比最大的一块，却一处都没被枚举到。

同一条漏网还带出三个：`components/Chart/aiBacktest/panels.tsx`（平均盈利/亏损、单笔最大盈亏
四行内联面色）、`components/Strategy/TradeExplainDetail.tsx`（「止损 @ / 止盈 @」风控快照）、
`lib/aiTradeAcceptance.ts`（验收卡的「已通过 / 未通过」徽标文字色）。

改正：`colorForReturn` 改返回墨色（**一行改动覆盖 83 处**），其余三处逐处改；G9 ② 因此新增一条
「不得把面色写进内联 `color:`」的扫描（`backgroundColor` / `borderColor` 不受限，它们用面色是对的）。

### I-3（P1）· `lib/markdown.ts` 与 `lib/aiTradeAcceptance.ts` 在方案的文件计划之外，但确实带缺陷

§5.2 的枚举根是 `apps/web/components`，而 `lib/` 下有两处真实落点：
`markdown.ts:183` 的 Markdown 链接色 `text-[var(--accent)]`（Agent 面板每条回答里的链接），
以及上条提到的 `aiTradeAcceptance.ts`。**G9 ② 的扫描根必须同时含 `components/` / `app/` / `lib/`**，
否则这两处永远看不见——「只覆盖一半代码树的门禁挡不住漂到另一半去的同一个缺陷」。
`warnToken.source.test.ts` 的扫描根也一并从 `components/` 拓到同样三处（这就是 §5.2 #23 说的「并入通用扫描」）。

### I-4（P1）· `--chart-1..8` / `--success` / `--danger`：**10 处**指向未定义 token 的死跳转

G9 ③ 一上线就抓到：`lib/aiTradeAcceptance.ts` 里 `var(--chart-1, var(--accent))` 这类两级 var 共 10 处，
而 `--chart-1..8` / `--success` / `--danger` 在 `globals.css` 里**一个都没定义**——那层间接跳转从第一天
起就是死的，渲染出来的一直是兜底值。更糟的是该文件的注释写着「全部取自既有 `--chart-*` 设计 token，
不新增任何一个」，**这句话是假的**。这与 D2 点名的 `var(--on-accent,#fff)` 是同一个形状的第三次复发。

改正：删掉死跳转（渲染**逐像素不变**，只是这份配色表现在说的是实话），文字色那两处顺带迁到墨色。
引入一套真正的 `--chart-*` 调色板是独立一轮的事，不在本轮范围。

### I-5（P1）· 对话框栈只按「挂载顺序」判栈顶是**不够的**，方案的修法漏了一半

§三 D4 的修法是「模块级栈 + 栈顶守卫」，判据隐含为入栈顺序。实测不成立：**React 的 effect 自底向上
执行（子先于父）**，所以外层 `Modal` 与内层 `ConfirmDialog` **同时挂载**时，内层先入栈、外层后入栈，
按顺序判会得出「外层在顶」。两条挂载次序的失败方向正好相反：

| 次序 | 谁先注册 keydown | 纯顺序判定的结果 |
|---|---|---|
| 先开外层、再开内层（真实路径） | 外层 | ❌ Esc 关外层——**这才是缺陷的形状** |
| 两层同时挂载 | 内层 | ✅ 恰好是对的 |

而 §2.1(c) 那条探针用例走的是**同时挂载**，所以只固化它会得到一条永远绿的用例——正是 P1-4 警告过的
那类陷阱的另一个版本。（我实测验证过：把栈顶守卫整条注释掉，只有「先后打开」那条用例会红。）

改正：判据改为 **React 树上的嵌套深度**（新增 `ModalDepthContext`，context 穿透 portal 按渲染位置传递），
挂载顺序只在同深度（两个互不嵌套的兄弟弹窗）时作为「后入者优先」的次序。`Modal.test.tsx` 两条次序
**各写一条**用例。

**连带订正 P1-4 的射程**：改用深度判定之后，「外层 `onClose` 换身份 → 重新入栈到栈顶」这条复活路径
在结构上不成立了（重新入栈改变不了深度），所以那条回归用例**守不住依赖数组本身**。入栈 effect 的依赖
仍严格保持 `[open]`（硬约束 6），但它现在是「避免无谓的 splice/push churn」，而不是正确性的唯一支点。
这一点已如实写进用例注释，不含糊其辞。

### I-6（P1）· 9 条 WS 路由加守卫会打穿仓库既有的 `_FakeWS` 约定

本仓 WS 用例一律**禁用 `TestClient`**（会与 anyio portal 死锁），改用最小 `_FakeWS` 直接 `await` 路由协程。
这些替身只实现 `accept` / `send_text` / `close`，**没有 `headers`**，于是 `guard_ws_origin` 一读 header 就
`AttributeError`，7 个既有测试文件全红。

处置：**不让生产代码去迁就替身**（`getattr(ws, "headers", None)` 这类兜底会让一次拼写错误静默关掉整条
守卫，正是本轮要消灭的形状），而是给 7 个替身各补一行 `self.headers = {}`（= 无 Origin ⇒ 非浏览器 ⇒ 放行，
既有语义逐字不变）。文件计划因此 +7：`test_agent_ws` / `test_discovery_api` / `test_weight_batch_ws` /
`test_weight_train_resilience` / `test_weight_train_ws` / `test_ws_idle_heartbeat` / `test_ws_market`。
守卫读 `websocket.scope["path"]` 而不是 `websocket.url.path`，也是为了把替身的契约面压到最小。

### I-7（P2）· `toApiError` 放在 `httpTimeout.ts` 会造成模块环

§三 D3 把 `toApiError` 列为 `httpTimeout.ts` 的导出，但它要 `new ApiRequestError(...)`，而
`ApiRequestError` 定义在 `api.ts`，`api.ts` 又要 import `httpTimeout` ⇒ `api.ts ⇄ httpTimeout.ts` 成环。
方案自己说 `httpTimeout.ts` 是「纯函数模块（无 React、无 fetch）」，让它反向依赖 `api.ts` 与这个定位冲突。

改正：`toApiError` 落在 `api.ts`（它本来就是 `ApiRequestError` 的所有者，且 18 个 `*Api.ts` **已经**
从那里 import `ApiRequestError`，接线成本为零）；`httpTimeout.ts` 保留判定所需的纯函数
`isTimeoutError` / `timeoutMessage`。依赖方向单向：`api.ts → httpTimeout.ts`。

### I-8（P2）· `fetch` 调用点口径由 20 变 18，acceptance 的数字要跟着改

§5.3 要求 `aiTradeHorizonApi.ts` 的 3 处裸 `fetch` 「一并接线并升级为 `ApiRequestError`」。把它们收敛进
该文件私有的 `getJson()` 是达成这两件事最直接的写法，副作用是 `lib/**` 的 `fetch(` 调用点从 **20 → 18**
（文件数仍是 18）。这**不是** §十 第 8 条禁止的「合并 18 个 `*Api.ts` 的 `request` 包装」——那是跨文件重构；
这里是同一个文件内消除三段复制粘贴，反而让「第 4 次复制却忘了接超时」在结构上不可能发生。
G9 ④ 的断言按实测改为 18/18，并在用例注释里写清 20 → 18 的对账。

### I-9（P2）· G9 ① 的配对矩阵格数与 §4.2 自述不一致，且必须显式排除一格

§8.1 G9 写「15 格 × 2 主题」，而 §4.2 的矩阵实际是「3 ink × 4 底 + `--on-accent`×`--accent` +
`--warn`×`--warn-soft`」= 14 格。更要紧的是：暗色 `--short-ink` on `--accent-soft` 实测 **4.41（不达标）**，
§4.2 的表格里那一行已经把最低值记成 4.74（`--surface-2`），即**默认排除了这一格**，但没写明「凭什么能排除」。

改正：矩阵里显式排除 `--short-ink` × `--accent-soft`（每主题 13 格），并给这条排除配一个**可判定的看守**
——`contrastTokens.source.test.ts` 里「`--short-ink` 不得与 `bg-[var(--accent-soft)]` 同行出现」的扫描。
把 danger 按钮的 hover 底改回 `--accent-soft`，那条立刻红。排除项因此是被证明的，不是被约定的。
（实测确认全仓 `--short` 文字落在 `--accent-soft` 底上**只有** `ui/index.tsx:36` 一处，与方案一致。）

### I-10（P2）· `docker-compose.yml` 只给 `quant` 透传，不给 `web`

§5.1 #18d 写「`web`/`quant` 补 `SECURITY_ALLOWED_ORIGINS` 透传」。但 `web` 服务是 Next 前端，它
**不读**这个变量——Origin 判定完全在后端。给一个从不读它的服务挂一个 env，正是本仓反复点名的
「有写点没读点」。故只给 `quant` 加，并在注释里写清「远程部署时浏览器地址栏里的 host 不是 localhost」
这个真实成因。

### I-11（P2）· WS 拒绝在 wire 上呈现为 HTTP 403，而不是 close 1008

§6.2 记「WS close 1008」。ASGI 应用层确实发的是 `close(code=1008)`（`test_ws_origin_guard.py` 逐条断言），
但**握手尚未 accept**，所以 uvicorn 按 ASGI 规范把它翻译成一次 **HTTP 403 握手拒绝**——实测客户端看到的是
`InvalidStatus: server rejected WebSocket connection: HTTP 403`，WS 连接**从未建立**。这比 1008 更强
（1008 意味着连接曾经建立过），验收判据「未收到任何一帧」照样成立，只是排障时该按 403 找而不是找 1008。

### I-12（P2）· `wire_caps.py` 的红线文案被跨语言用例钉着，改 docstring 必须同步改它

D5 要求把 `wire_caps.py` 的模块 docstring「就地改写」。实测 `apps/web/lib/wireCaps.source.test.ts:42`
用 `toContain("绝不裁剪服务端的会计真值")` 把那句话钉在前端用例里（跨语言源码扫描），改 docstring 即变红。

处置：把那条断言从「不许裁」改成「顺序不可颠倒」（`先有计数器，再谈封顶` / `封不动任何一个判定` /
`至今不封顶`），并新增一条「`TRADE_LEDGER_MAX` 必须远高于展示封顶」——因为两个数字一旦混用，
展示口径会被当成会计口径写进 run metrics。文件计划因此 +1。

### I-13（P1）· D7 打开外键后，全量 G3 第一轮红了 **12 条**

方案的 R9 写着「红即真缺陷，修删除顺序，不许关回 pragma」。第一轮的 12 条红看起来全落在**测试造数**上，
逐条如下（**注意：这不是最终结论，见 I-14**）：

| # | 红 | 根因 | 处置 |
|---|---|---|---|
| 1 | `test_ai_trade_discipline_session::test_strictness_hint_fires_only_after_enough_rounds_with_zero_opens` | 用例直接 `executor.trades.append(...)`，绕过了 D5 的 `_record_trade` ⇒ `open_count` 仍是 0 | 改调 `_record_trade`（成交入账的唯一入口） |
| 2 | `test_ai_trade_tradability_api` × 2 | 造 `StrategyRun(strategy_id=101/102/103/109)`，而 `strategies` 表里**没有**这些行 | 新增 `_ensure_strategy()`，先把父行建出来 |
| 3 | `test_discovery_assets_backfill::test_orphan_candidates_without_a_run_are_skipped` + `test_discovery_promote_strategy` × 8 | 用例**故意**删父留子来制造孤儿，打开外键后删除直接抛 ⇒ 用例中途中断、脏数据残留在共用临时库里，把后面 8 条 promote 用例一起带红 | 见下 |

第 3 组值得单独说：那条用例守的行为（回填必须跳过孤儿候选）**依然是真实需求**——外键关了整整一个项目
周期，真实用户库里完全可能已经存在孤儿行。所以不能因为「现在造不出来了」就把用例删掉，正确做法是
**在本连接上临时 `PRAGMA foreign_keys=OFF` 来复现遗留形态**，把「这是遗留数据」写进代码，并在用例末尾
补一次清扫（本仓测试共用一个临时库，脏数据会跨文件传染——那 8 条 promote 红就是这么来的）。

改完之后**第二轮全量 G3 仍有 8 条红**——那 8 条才是真正的结论，见下一条。
最终只有第 1 条（`_record_trade`）留了下来：它属于 **D5**，与外键无关。第 2、3 组的测试改动
随 D7 一并回退，工作树对那两个文件**零 diff**。

### I-14（**阻断级 / D7 整批回退**）· 打开外键会让「删策略」这个按钮在生产上直接 500，而修好它需要一次 DB 迁移

把 I-13 那三组测试造数都修干净之后，**第二轮全量 G3 仍然红 8 条**
（`tests/test_discovery_promote_strategy.py` 全文件），根因只有一句：

```
sqlalchemy.exc.IntegrityError: (sqlite3.IntegrityError) FOREIGN KEY constraint failed
[SQL: DELETE FROM strategies WHERE strategies.builtin = 0]
```

顺着它读生产代码，D7 撞上的是一个**被明写下来的产品决定**，不是一处疏忽：

- `db/models.py:51` `strategy_runs.strategy_id` 是**非空**外键，全表**零 `ondelete`**；
- `db/repositories.py:119 delete_strategy` 只 `session.delete(row)`，`Strategy` 上**没有**任何指向
  `StrategyRun` 的 relationship ⇒ 不级联、不清理子表；
- `factors/service.py:480 delete_set` 的 docstring **原文**：「本机 SQLite 未开外键，删带 run 的
  `factor_score` 行与 `repo.delete_strategy`（交易面板删用户策略）同口径、**不因 `strategy_runs`
  外键失败**；已发起的 run 自包含、不受影响（历史经 `strategy_id` 仍可查阅）」；
- `repositories.list_runs` 确实**不 join `strategies`** ⇒ 孤儿 run 照常在历史里展示。

也就是说：**「删策略但保留它的交易历史」是这个产品有意选的语义，而它的实现方式正是依赖外键关着。**
打开外键之后只剩两条出路，两条都超出本轮范围：

| 出路 | 后果 | 为什么本轮不能做 |
|---|---|---|
| 连带删 run（及其 trades / equity_points） | 用户删一个策略 = **销毁那段真实资金的交易记录** | 这是产品语义变更，且销毁的是真金白银的历史，不能由实施节点顺手决定 |
| `strategy_id` 改可空 + `ondelete="SET NULL"` | 语义正确，历史保留 | 需要 **DB 迁移**，而 §七 明写「**零 DB 迁移、零建表、零 ALTER**」 |

**处置：按 §九 R9 / P2-16 的出口阀，D7 整批回退。** `PRAGMA foreign_keys=ON` 撤回，
`tests/test_db_foreign_keys.py` 删除，I-13 第 2、3 组的测试改动一并撤回（那两个文件**零 diff**）。
回退**不牵连其余六批**——这正是把 D7 排在最后一批的本意。

回退不等于把这件事丢掉，三处留痕，任何一处都能让下一个人接上：
1. `db/session.py` 的 `_configure_sqlite` docstring 就地写清「这里刻意没有 `foreign_keys=ON`」、
   两条出路、以及那 8 条红的实测根因——**下一个「顺手补上」的人会先读到它**；
2. §十 第 13 条（具名跟进，含所需的迁移形状）；
3. 本条。

顺带订正一处计数：`models.py` 里 `ForeignKey` 实际是 **7 处**（方案 §2.1(f) 与 §三 D7 写的是 8 处）。
逐处为 `strategies.id` ×1、`strategy_runs.id` ×2、`weight_models.id` ×1、`discovery_runs.id` ×2、
`discovery_assets.id` ×1。其中除 `strategies.id` 那一处外，其余六处的删除路径**本来就是按正确顺序写的**
（`weight_model_repositories` / `weight_batch_repositories` / `discovery/repositories` 全部先删子后删父，
全量 G3 里一条都没红）——所以 D7 真正的阻塞面**只有 `strategies → strategy_runs` 这一对**，
下一轮的修复面是可控的、具体的，不是一片未知。

### 未执行项（如实标注）

- **`npm run verify:dist`（§8.2 D1 最后一条）本轮未跑**：仓库内不存在 `desktop/release/` 产物，跑它需要
  先做一次完整 `npm run pack`（electron-builder + PyInstaller），而本机对该步骤有多份既有诊断记录
  （EBUSY / wincodesign symlink EPERM）。它要证的那件事——`SECURITY_ALLOWED_ORIGINS` 能否进到打包后端
  ——其机制就是 `backendEnv` 的前缀白名单，已由 `desktop/scripts/config.test.mjs` 新增用例覆盖，
  且该用例在 G5（本地 32 pass）与 G8（干净检出 25 pass）两处都真跑过。**这不等于跑过 verify:dist**，
  故单列于此，不含糊带过。
- **明暗双主题人工目视（§8.2 D2）本轮未做**：需要人在屏幕前看。可自动化的部分（配对矩阵重算、面色
  `text-` 归零、未定义 token、图表 series 取色不出现在 diff 里）已全部由 G9 与 `git diff` 覆盖。


---

## 终审（Final Review · 代码评审节点）

> 本节由终审节点就地补写，与代码同一次 commit。**不接受实施报告的任何数字**：下面每一个门禁
> 数字都是终审节点当次实跑的；每一条缺陷都是在自己重读源码时撞到的，逐条已在源文件里改完，
> 不留待办。

### 一、对账（DoD-1）

| 项 | 数 | 说明 |
|---|---|---|
| §5.8 计划路径 | 176 | 12 新 + 164 改 |
| 加 `spec.md` | 177 | 方案自陈的 staged 目标数 |
| 实施节点实际 staged | 189 | 12 新（含 `spec.md`；`test_db_foreign_keys.py` 随 D7 整批回退删除）+ 177 改（164 计划 + 13 个 I-1…I-14 记录在案的连带文件） |
| 终审新增 | +4 | `TradeCandleChartPanel.tsx` / `BacktestMetricsGrid.tsx` / `indicatorLegend.ts` / `IndicatorLegendOverlay.tsx`，全部来自下面的 FR-3 |
| **最终 staged** | **193** | 无表外文件；未跟踪清单（`docs/diagnoses/`、`ensure-wincodesign*`、`live_auto_repositories.py`、`test_factor_library_expansion.py`、`.agentmesh/`、`CLAUDE.md`、`tmps/`、`.claude-index/`、`.bug-diagnosis/`、`docs/examples/`、`docs/improve/`、9 个未跟踪 `docs/plans/` 子目录）逐项复核仍为未跟踪 |

放行硬约束逐条复核：1 `Origin: null` HTTP/WS 双侧单列用例在（`test_origin_guard` / `test_ws_origin_guard`）；
2 容器仍是 `list` 且有类型侧 + 行为侧两条看守；3 `git diff` 里零新增持久化键；
4 `SECURITY_` 前缀已进 `backendEnv` 白名单且有同范式用例；5 中间件是纯 ASGI（无 `BaseHTTPMiddleware`）；
6 入栈 effect 依赖数组是 `[open]`；7 面色 `text-` 全仓归零、配对矩阵在测试里现算。
`@router.websocket` 实测 9 条、`guard_ws_origin` 调用点实测 9 处，逐条落在 `accept()` 之前。

### 二、终审发现并就地修好的缺陷

**FR-1（P1）· 写类超时路径表一条都匹配不上真实路径，「结果未知」口径在所有停止/暂停/继续按钮上失效**

D3 自己写下的最重要一条是「写类超时文案必须说『未确认』而不是『失败』」，理由是一个超时的
`POST /trading/order` 可能已经成交。但 `WRITE_PREFIXES` 写的是 `/ai-trade/stop`、`/paper/stop`、
`/live-auto/group/stop` 这些**后端路由模板的形状**，而 `*Api.ts` 真正传进来的是
`/ai-trade/{groupId}/stop`、`/paper/{sessionId}/stop`、`/live-auto/group/{groupId}/stop`
——`startsWith` 一条都命中不了。于是除 `/trading/*` 与四个 `start` 之外，
**「停止会话 / 暂停 / 继续 / 结束 / 成员停止 / 兜底自愈」全部拿到读类文案**，
而 D3 §三举的例子恰恰就是「一个『停止会话』按钮转了 5 分钟圈还什么都不说」。
`httpTimeout.test.ts` 逐条断言的也是同一批不存在的路径，所以它一直是绿的——
本轮反复点名的「静默不完整」在自己的验收里再犯了一次。

改正：判定拆成两张表——无参数的走前缀表，带 `{id}` 的走「家族前缀（`/ai-trade/` `/live-auto/` `/paper/`）
+ 末段动作（`/stop` `/pause` `/resume` `/end` `/repair`）」；用例路径逐条改抄自 `*Api.ts` 的真实调用点，
并补一组反向用例（`/ai-trade/status`、`/ai-trade/{id}/resume-preview`、`/ai-trade/backtest/{id}/cancel`
仍判读类），防止末段动作误伤只读路径。

**FR-2（P1）· 长跑路径表漏掉 5 个同步重接口，它们从此在 20 秒被 abort**

`FAST_MS = 20s` 是新加的**默认**行为，所以漏进前缀表的每一条同步重接口都不是「少了个优化」，
而是**把一个原本能用（只是慢）的功能改坏**。实读后端路由逐条核对，漏了 5 条：
`/factors/compute`、`/factor-sets/train`、`/factor-sets/score-preview`（三条都是拉 K 线 + 全窗口计算，
同步返回；`/factor-sets/train` 与已在表内的 `/weight-models/train` 是同一条重计算的两个入口）、
`/weight-models/{id}/runs/{id}/backtest`（一键回测）、`/discovery/candidates/{id}/backtest`。
本仓已有 `docs/diagnoses/factor-lab-train-long-range-socket-hang-up/` 记着这条路的真实耗时。
同时表里的 `/ai-backtest/` 是**死条目**：AI 回测的真实路径是 `/ai-trade/backtest/start`，
它是异步作业（立刻回 jobId、进度走 WS），本就该走 `FAST_MS`——而用例断言的正是那条不存在的路径。

改正：5 条按形状分别补进 `LONG_EXACT_PREFIXES` 与 `LONG_SEGMENT_SUFFIXES`，删掉死条目并写清理由；
用例改用真实路径，另补一条「异步作业的启动/查询仍是 `FAST_MS`」的反向用例。
（口径与 §九 R5 一致：发现遗漏就加进前缀表，不抬高 `FAST_MS`。）

**FR-3（P1）· 内联面色当文字用的还有 9 处 / 4 个文件，G9 ② 的写法看不见它们**

I-2 已经发现「枚举命令只 grep Tailwind 类，看不见内联样式」并修好了 `colorForReturn`（一行覆盖 83 处）
与三个点名文件，但它给 G9 ② 写的判据是「`color:` **紧跟**一个面色字面量」。盈亏着色几乎全是
**条件表达式**，于是漏网如下：`Strategy/BacktestMetricsGrid.tsx` 4 处、`Strategy/AutoTradeTradesTable.tsx`
1 处、`Chart/TradeCandleChartPanel.tsx` 1 处、`Agent/AiTradeTraceRound.tsx` 1 处
（另有 `lib/indicatorLegend.ts` 1 处是**色点填充**，属正确用法）。
其中 `BacktestMetricsGrid` 最能说明问题：同一张指标表里，「平均每笔」经 `colorForReturn` 已经是墨色，
紧挨着的「平均盈利 / 平均亏损 / 单笔最大盈亏」四行仍是面色——**同一列数字两种深浅**，
而这正是 I-2 在 AI 回测面板 `aiBacktest/panels.tsx` 上修过的那四行的策略回测孪生版。

改正：4 个文件 7 处迁到墨色；G9 ② 的判据改成「同一行里出现 `color:` 且出现面色」（面色只能进
`backgroundColor` / `borderColor` / 图表 series）。`indicatorLegend.ts` 的字段随之从 `color` 改名
`swatchColor`——它是色点填充色，叫 `color` 就得给门禁开一个例外，而「被约定的例外」正是这类缺陷
复发的入口（本轮 I-9 立的规矩：排除项必须是被证明的）。改完全仓实测 0 处。

**FR-4（P2）· `realized_pnl_total` 是第三个计数器，没有任何生产读点，且与真值必然漂移**

D5 立的三个计数器里，`trade_count` / `open_count` 各自替代了一处「数列表长度」的判定，都有真实读点；
`realized_pnl_total` 没有——全仓只有它自己的三条用例读它。而已实现盈亏**本来就有真值**
`realized_pnl_usd`（平仓回执累加，续跑时由 continuity 一并回灌），再按成交腿累加一份就是
「同一个数落两份」，正是 `ai_auto/resume.rebuild_continuity` 的不变式原文所禁止的那件事：
「派生量绝不落库：落两份必然漂移，而这里漂移的是真实资金的盈亏显示」。
而且它**真的会漂**：`_synthesize_external_close` 整个包在 `except Exception: return []` 里，
合成失败时 `realized_pnl_usd` 照加（`_settle_benign_close` / `_on_reconcile_ok` 的 `+=` 在 try 之外）、
成交腿那一份不加。名字里还带着 `total`，比真值更像真值——这是一处会咬人的陷阱。

改正：删掉该字段与 `restore_trades` 的 `realized_total` 形参（调用方 `resume.py` 下一行本来就在回灌
`realized_pnl_usd`），`wire_caps.py` 红线 docstring 与 `wireCaps.source.test.ts` 的跨语言断言同步改为
「两个计数器」，并新增一条结构用例 `test_realized_pnl_has_exactly_one_accumulator`
（正则断言全文件 `self.realized_* +=` 的集合恰为 `{realized_pnl_usd}`），把「不许再加第二个累加器」
钉成可判定的。恢复用例改为断言 `continuity["realizedPnlUsd"] == 台账逐腿之和`——那正是不需要第二份
累加器的理由本身。

**FR-5（P3）· 两处注释与实现不符 + 一处 import 落位**

（a）`origin_middleware._origin_header` 的 docstring 写「大小写不敏感」，实现是 `key == b"origin"`
单一拼写比较。真正的依据是 ASGI 规范要求服务器把 header 名一律小写后放进 scope——按 CLAUDE.md §四，
docstring 是契约的一部分，说错了会诱导下一个人加一层无谓的 `lower()`；已改成陈述那条规范依据。
（b）`executor.py` 的 `from ..wire_caps import ...` 插在 `..okx.*` 两条 import 中间（ruff 选中的
`F,E9` 不含 isort，门禁不会红）；已归位到 `..okx.*` 之后。

### 三、终审自己跑的门禁（每个数字都是当次实跑）

| 门禁 | 命令 | 结果 |
|---|---|---|
| G1 | `apps/web && npx tsc --noEmit` | **exit 0**（基线 exit 0） |
| G2 | `apps/web && NODE_ENV=test npx vitest run` | **171 files / 1940 tests / 0 failed**（54.3s；实施节点 171/1920，+20 来自 FR-1/FR-2 的真实路径用例；基线 169/1858） |
| G3 | `services/quant && .venv/Scripts/python -u -m pytest -q` | **7 failed / 3720 passed / 4 skipped / 2 deselected / 772.72s**。7 条红**全部且仅**在 `tests/test_factor_library_expansion.py`（`git ls-files` 复核：仍是**未跟踪**在制文件，任何 CI 检出里都不存在），故 **CI 视角 = 3720 passed / 0 failed**；相对实施节点的 3719 恰好 +1，来自 FR-4 新增的那条结构用例 |
| G4 | `services/quant && .venv/Scripts/python -m ruff check .` | **All checks passed, exit 0** |
| G5 | `desktop && npm run check` + `npm test` | 13 项 check 全绿；**32 pass / 0 fail**（基线 31） |
| G6 | `apps/web && env -u NODE_ENV npx vitest run` | **171 / 1940**，与 G2 同值（无环境耦合） |
| G7 | `apps/web && npx next build` | **✓ Compiled successfully** |
| G8 | `git write-tree` + `git archive` 干净检出 → `desktop && node --test` | **25 pass / 0 fail**（上一轮干净检出基线 24，+1 = `SECURITY_` 透传用例） |
| G9 | ①CSS 配对矩阵现算 ②面色 `text-`/内联 `color:` 归零 ③未定义 token 回退 ④`fetch` 全经 `withTimeout` ⑤`LONG_MS > proxyTimeout` | 5 个子项全绿（含 FR-3 加强后的 ②） |
| G10 | `test_origin_guard.py` + `test_ws_origin_guard.py` | 全绿（HTTP 403 / 9 条 WS 各自拒绝恶意 Origin 与 `Origin: null`；无 Origin 与白名单放行） |

### 四、未执行项（沿用实施节点的如实标注，不替它宣称）

`npm run verify:dist` 与明暗双主题人工目视本轮**仍未执行**，理由见上一节「未执行项」。
终审节点没有补跑，也不把它们算作已完成——`SECURITY_` 透传的机制侧由 `config.test.mjs` 在 G5/G8
两处真跑覆盖，但那不等于跑过 `verify:dist`。
