# AI 自动交易 · 生命周期真值化（暂停 / 继续的稳健性与如实呈现）

- **feature**: `ai-trade-lifecycle-truth`
- **版本**: **v2**（评审节点定稿，2026-08-18；v1 = 方案设计节点产出，见文末《评审结论》）
- **上游**: `docs/plans/ai-trade-pause-resume/spec.md`（已合入 `7b55d16`，本轮是它的**加固轮**）
- **相关**: `ai-trade-wake-scheduler-closed-loop`、`okx-link-resilience`、`stability-hardening-audit`
- **一句话**: 把「AI 自动交易此刻是不是真的在跑」从**一个被赋值的字段**改成**一个被派生的真值**，
  并给它一条持续对账的通道，使界面上的每一个状态都能被后端的事实反证。

---

## 评审记录（v2 · 2026-08-18）

**评审口径**：逐节对照「可行性 / 完整性 / 一致性 / 右尺寸」四维；并按 §九 R-10 的强制要求，把
§1.2 的 12 条根因**逐条打开代码落点复核**。

**复核结论**：**12 条根因全部成立，落点行号全部对得上**（见下表），故本轮不删除、不降级任何一条
根因。评审的全部火力因此落在**「修法本身是否成立」**上 —— 而那里有 **7 处照字面实现就会出事**：
其中 4 处会**新造**缺陷（两处直接碰真实资金），2 处会让本轮目标**原地失效**（写了、跑了、但用户
一个字都看不到），1 处会让判定表里三行**永远不可达**。这与 R-10 自己写下的那句「spec 自身可能是
缺陷来源」完全同形，只是它这次说中了自己。

**全部 P0 与 P1 已在正文逐条修正**（每条都标注了 `[评审 R0-x]` / `[评审 R1-x]` 的回指），P2 只登记
不阻塞。

### 0. §1.2 十二条根因的代码复核（R-10 的强制动作）

| 根因 | 复核 | 复核证据（本次实读） |
|---|---|---|
| D-1 | ✅ 成立 | `manager.py:786-787` 确为 `await member.session.resume()`（无赋值）；`session.py:757-758` 早退回执确为 `{"ok": False, "state": self.state}`，连 `reason` 都没有 |
| D-2 | ✅ 成立 | `manager.py:981-992` 无活跃成员分支只有 `_mark_state_ended` + `_forget_paused_ref`；对比 `stop_member:737-739` 确实清了 `self._group` |
| D-3 | ✅ 成立 | `useAiTradeGroupStream.ts:112-113` 同 `groupId` 直接 `return`；15s 轮询只 patch 验收三键 |
| D-4 | ✅ 成立 | `AiTradeToggle.tsx:81-87` `refetchInterval: 15_000`，回执只取 `aiTradeEnabled` |
| D-5 | ✅ 成立 | 两处口径确实不同；且 `resolveAiTradeLifecycle` 全仓**只有 1 个调用点**（Toggle），面板从来没走过那条「唯一入口」 |
| D-6 | ✅ 成立 | `session_link.py:133-135` 判据确为 `!= "running"`；`ingest_ai_auto` 确为纯窗口 + 指标 + 推 candle |
| D-7 | ✅ 成立 | `session.py:761` 一行把两个时钟重置为 `now`；`resume()` 里没有任何 WS 体检 |
| D-8 | ✅ 成立 | `/resume` 落 `FAST_MS = 20_000`（`LONG_*` 两张表都匹配不上）；冷续跑要跑 provider 解析 + 逐成员 `_prime()` |
| D-9 | ✅ 成立 | `api/ai_auto.py:664-669` 发一帧 `error` 后 `close()`；`routeMemberEvent` 的 switch 确无 `error` 分支 |
| D-10 | ✅ 成立 | `session.py:666-668` / `:770-772` 两处 push 确在；前端无 `lifecycle` case |
| D-11 | ✅ 成立 | `manager.py:963-967` 判据确为 `if receipt is not None:` 后直接 `row["position"] = receipt.get("position")` |
| D-12 | ✅ 成立 | `_paused_wire_for_mutex:1053-1054` 回 `state:"unknown"`，而 `group_status()` / `resumable()` 在同一情形下都回 `None` |

**附带核实（spec 说对了、值得钉住的三条）**：① 链路登记簿键格式确为 `ws:{endpoint}:{instId}:{bar}:{owner}`
且 ai 会话的 `owner = f"ai:{session_id}"`（`ws_client.py:158` + `session.py:501`），§3.1 的键推导正确；
② `GET /ai-trade/status` 确实 `-> dict` 且无 `response_model`，加法键不会被吞；③ `_http_error` 确实
透传 `members`（`api/ai_trade_lifecycle.py:47-49`）。

### 1. P0（7 条 —— 照字面实现就会出事，已在正文全部修正）

| # | 维度 | 结论 | 落点 |
|---|---|---|---|
| **R0-1** | 可行性 | **B-1 的清组少了身份守卫，会清掉「别人的」分组。** `_note_session_stopped` 是经 `partial(self._note_session_stopped, group)` 绑定的**旧分组**回调。一个上轮分组的最后一个会话延迟自终止时，`self._group` 可能已经是**新分组**；无条件 `self._group = None` 会把新分组连同它的互斥位一起抹掉，界面整块消失而 Agent 还在跑 —— 这正是本轮要消灭的「说谎」的镜像版。 | §3.3 B-1 |
| **R0-2** | 一致性 | **B-3 改成 `is_active` 判据对 `live_auto` / `paper` 是回归，不是「逐字节零回归」。** spec 断言这两类会话「没有 `is_active` 属性 ⇒ `getattr(..., True)` 兜底 ⇒ 零回归」，但它们**都有 `self.state`**（`live_auto/session.py:58,188`、`paper/session.py:50,250`）：今天 `stopped` 会被早退挡掉，改判据后**永远挡不掉** —— 一个已停止的自动交易会话会继续补根、继续写窗口、继续推链路告警。 | §3.3 B-3 |
| **R0-3** | 可行性 | **B-4 的 `_last_candle_wall += paused_span` 会把时钟推到未来，让陈旧看门狗失明整个暂停时长。** 暂停期 WS 不停，`feed_candle` 的顶部判据是 `is_active`，仍会把 `_last_candle_wall` 刷到当前（`session.py:982,993`）。于是续跑瞬间该值 ≈ `now`，再加一个 3 小时的 `paused_span` ⇒ `elapsed` 为负 ⇒ **接下来 3 小时里行情真的死了也判不出陈旧**。这是在一条风控读数上开 fail-open，与 D-7 的立论正好反向。 | §3.3 B-4 |
| **R0-4** | 可行性 | **`_rearm_feed` 不能对同一个客户端 `stop()` 后再 `start()`。** `OKXMarketWSClient._stop` 是 latched `asyncio.Event`：`stop()` 置位后**从不清**，`start()` 也不清（`ws_client.py:117,130-138`），而 supervisor 的主循环是 `while not stop.is_set()`（`ws_supervisor.py:101`）⇒ 重启出来的 task 立刻退出并在 `finally` 里 `close_channel`。照字面实现的结果是**续跑之后行情永久不来**，且 `is_alive` 恒假 —— 一条比 D-7 更严重的静默死亡。 | §3.3 B-4 |
| **R0-5** | 完整性 | **B-4 与 `live_auto_flatten_on_stale` 强平路径的交互没有被处理。** `_check_staleness` 在 `elapsed > stale_after * 4` 且该开关为真时**直接 `stop("stale_feed")` 平掉真实持仓**（`session.py:939-956`，[P2-7] 的既有取舍）。今天「重置为 now」恰好挡住了「点继续 → 几秒后被强平」这条路；本轮把时钟改成如实值以后，必须显式规定：**复检先判、不武装即回滚**，绝不能让一次「继续」在几秒内变成一次强平。 | §3.3 B-2 / B-4、§3.6 |
| **R0-6** | 可行性 | **F-7 的超时分流判据接错了层，`isTimeoutError` 在那一层恒为假。** `aiTradeApi.request` 的 `catch` 已经把超时经 `toApiError` 换成了 `ApiRequestError("TIMEOUT", …)`（`aiTradeApi.ts:31`、`api.ts:59-61`），而 `isTimeoutError` 判的是 `err.name`（`"TimeoutError"` / `"AbortError"`）—— mutation 的 `onError` 拿到的早已不是那个对象。照字面写，L-7 承诺的「结果未知」**一次都不会出现**，本轮最贴近用户报告的那条修复原地失效。 | §3.4 F-7 |
| **R0-7** | 完整性 | **对账器的宿主与门禁自相矛盾，判定表七行里有三行永远不可达。** F-2 把对账挂进 `useAiTradeGroupStream` 的 15s 轮询，但那条 effect 的门是 `if (accountMode !== "live" \|\| !groupId) return`（`useAiTradeGroupStream.ts:120-121`）—— **本地没有分组时它根本不跑**，于是「重发现」「冷暂停接管」「`lifecycleUnknown`」三行判定永不执行；而 F-6 又让 `AiTradeToggle` 的 `statusQ` 也写 store，成了第二个 owner。L-3（≤15 秒自愈）在最需要它的那个态上不成立。 | §3.4 F-2 |

### 2. P1（7 条，已在正文修正）

| # | 维度 | 结论 | 落点 |
|---|---|---|---|
| **R1-1** | 完整性 | **两个新 409 的 `members` 明细在前端没有读点。** `describeStartError` 只认 `AI_TRADE_GROUP_PREFLIGHT`（`AiTradeToggle.tsx:491-497`），其余 code 一律回落 `err.message` ⇒ §3.6 承诺的「逐标的列出谁还活着 / 谁已结束」一个字都不会渲染。这是本仓库成文教训「wire 里有事实、用户那里没有」的第 N 次复发，且这次是**评审前就能看见**的那种。 | §3.4 F-10、§5.3 |
| **R1-2** | 完整性 | **冷续跑路径没有复检。** B-2 只重写热路径，而 `resume_cold_group` 结尾直接 `group.state = RUNNING`（`resume.py:238`）—— 用户报告里「关软件 → 重开 → 继续」正是这条路。复检必须提到 `resume_group` 这一层，让两条路径共用同一段判定。 | §3.3 B-2 |
| **R1-3** | 完整性 | **窄 action 不更新成员行 ⇒ 已死成员的卡片会永远显示「运行中」。** 该成员的 WS 恰好被 F-4 关掉（`NOT_FOUND` 后不再重连），于是**再也没有任何事实会到达那张卡片**。窄 action 必须按 `instId` 合并成员的生命周期字段（白名单），且绝不碰 `trades` / `position` / `decisions` / `traces`。 | §3.4 F-2 / F-9 |
| **R1-4** | 一致性 | **`resume_rollback` 不在 `AiTradePauseReason` 稳定枚举里。** 后端 `PAUSE_REASON_TEXT` 四个键（`pause.py:26-31`）、前端联合类型四个值（`aiTradeTypes.ts:1226`）都没有它 ⇒ 回滚后的暂停原因在界面上是空的。CLAUDE.md §五：稳定枚举的扩展要一次改齐所有落点。 | §3.3 B-2 |
| **R1-5** | 一致性 | **`AiTradeLifecycle` 加第四态会改变启动门禁的输入。** 唯一调用点用 `hasActiveSession: running \|\| paused` 喂 `resolveAiTradeGate`（`AiTradeToggle.tsx:90-105`）；新增 `stopped` 后该表达式会把「本地留痕的已停止组」判成可启动。这**恰好是对的**（后端此时已清组），但必须显式写下来并加守卫用例，否则下一轮有人会「顺手补上」而把启动路径重新锁死。 | §3.4 F-1 |
| **R1-6** | 完整性 | **「下一次决策」倒计时在最常见配置下没有落点。** `wake_interval_s <= 0`（未装墙钟唤醒）时 `nextWakeInS` 为 `null`，而本轮 §1.3 目标 2 把这条倒计时当作「恢复到 Agent 执行交易状态」的**主要证据**。必须给出回落：无墙钟唤醒时改显「下一根 K 线 mm:ss」（`nextBarInS`），并明确 `paused` / `!armed` 时不渲染倒计时（否则界面会对着一个不会到来的时刻读秒）。 | §3.4 F-1 / F-6 |
| **R1-7** | 右尺寸 | **整组 `armed` 与门在多标的下会把跑得好好的成员也说成「未武装」。** 2 个标的死 1 个时，`group.armed=false` 让徽标整体降级 —— 用户会以为两个都停了。徽标必须带分数（`membersArmed/membersTotal`），横幅逐标的说明。 | §3.4 F-1 / F-6 |

### 3. P2（6 条，登记不阻塞；实现节点可自行决定是否顺手做）

| # | 结论 |
|---|---|
| **R2-1** | **F-7 想新建的 `LONG_ACTION_*` 表已经存在**：`LONG_SEGMENT_SUFFIXES` 就是「家族前缀 + 末段」范式（`httpTimeout.ts:52-59`）。新建第三张表是重复造轮子，正文已改为复用它 + 一个专属档位常量。 |
| **R2-2** | **§七说的「desktop 白名单放行」这一步不需要做**：`desktop/src/lib/config.js:80` 是**前缀规则**（`AI_TRADE_*` 一律透传），新键自动放行。三处联动在本轮只剩两处。 |
| **R2-3** | `is_alive` 读到「已 done 且带异常」的 task 时应顺手取一次 `task.exception()`，否则 asyncio 会打一条 "Task exception was never retrieved" 噪音 —— 而那条噪音正是这个属性想要暴露的那件事。 |
| **R2-4** | `return {"ok": True, "state": self.state, **self.lifecycle_wire()}` 里 `state` 是重复键（`lifecycle_wire()` 自带）。值相同不会出错，但两个真源写在同一个字面量里是下一次漂移的种子。 |
| **R2-5** | R-7 把 persist 白名单的风险估高了：`partialize` 是**显式白名单**（`store.ts:1177+`），新键不写进去就不会被持久化。用例仍值得留（成本近零），风险等级从「误进白名单」降为「有人手滑加了一行」。 |
| **R2-6** | §4.1 里 `tests/test_okx_session_link.py（或既有同类）` 的括号可以去掉 —— 该文件确实存在（`services/quant/tests/test_okx_session_link.py`）。同理 §4.1 / §4.2 列出的其余 12 个测试文件全部实读存在，无需再探。 |

### 4. 评审对「不做」清单与右尺寸的判断

- **不新增分组级 WS 通道**：✅ 同意。理由与 `horizon-acceptance` D-10 一致，且本轮把已有的 15s 轮询
  升级成对账器之后，L-3 的 15 秒上界由**已经存在的请求**兑现，边际成本为零。
- **零 DB 迁移**：✅ 同意，且 §六「派生量绝不落库」的论证是本文最强的一段 —— `ai_trade_group_states.status`
  （磁盘契约）与 `lifecycle.state`（进程内会话）刻意不合并这一条，请在实现时原样保留进代码注释。
- **总闸 `ai_trade_lifecycle_truth_enabled`**：✅ 同意，但请注意它要能**真的**一键回滚：`SessionLink`
  的判据回落、`group_status()` 的派生回落、前端因 `lifecycle` 缺席而自动回落，三处缺一，回滚就只是
  声明。用例 12（逐字节相同）是这条的唯一护栏，**不可省**。
- **规模**：19 条单测 + 8 条手工验收对一轮「同时动会话、manager、链路、前端四层」的加固**不算多**。
  §十的交付顺序（纯核心 → P0 后端三条 → wire → 前端真值化 → 前端呈现）是对的，第 2 步之后即可消灭
  用户报告的主路径，这一点值得在实现节点被当作里程碑守住。

---

## 一、概述

### 1.1 用户报告的现象

> 「断联或者暂停之后再去恢复，它可以正常进行持单校验，完了之后**貌似**已经恢复了，但是客户端上的
> 状态却没有正确恢复到 Agent 执行交易的那个状态 —— 面板上显示已经恢复过的状态，但是 Agent 却没有
> 正常的自动交易。」

这句话里最重要的词是「**貌似**」。用户描述的不是一次功能缺失，而是一次**失信**：产品说了一句它没有
能力兑现的话。上一轮（`ai-trade-pause-resume`）把暂停 / 继续的**能力**做出来了 —— 契约落盘、冷续跑
七步、离线差异 11 格矩阵、带仓核实 —— 但它把「分组现在是什么状态」实现成了一个**由动作赋值的字段**
（`AiTradeGroup.state = "running"`），而不是**由会话事实派生的结论**。于是只要「赋值」与「事实」之间
出现任何一条缝，界面就会显示一个后端从来没有真正达成过的状态。本轮的全部工作就是把那条缝焊死。

本轮的立论与上一轮终审留下的那条成文教训完全同形 ——「**wire 里有事实，用户那里没有**」，只是这次更
进一步：**wire 里连事实都没有**，它下发的是意图。`group_status()` 里的 `"state": getattr(group, "state",
...)` 是一次赋值的回声；`POST /ai-trade/{gid}/resume` 的回执是同一个回声；前端把这个回声当作真值渲染
成「运行中」，而三条真正决定 Agent 会不会思考的事实（会话还活着吗 / 行情还在流吗 / 下一次唤醒在什么
时候）在整条链路上一个字都没有出现。

### 1.2 现象 → 根因（全部经代码核实，逐条给出落点）

| # | 严重度 | 根因 | 落点 | 用户看到的 |
|---|---|---|---|---|
| **D-1** | P0 | **热续跑丢弃逐成员回执**。`resume_group` 热路径写的是 `await member.session.resume()`（无赋值）；而 `AiTradeSession.resume()` 在 `state != "paused"` 时回 `{"ok": False, "state": ...}` 并**什么都不做**。暂停期自终止的成员（会话止盈止损 / `stale_feed` 强平）会安静地留在 `stopped`，分组照样被置 `running` 并回 200。 | `ai_auto/manager.py:774-799`、`ai_auto/session.py:747-772` | **恢复成功、Agent 不交易** —— 报告现象的最直接一条路径 |
| **D-2** | P0 | **会话自终止后分组不清**。`_note_session_stopped` 在「无任何活跃成员」时只把状态表置 `ended` 并清冷占位，**从不清 `self._group`**（对比 `stop_member` 是清的）。 | `ai_auto/manager.py:973-991` vs `:722-741` | `/ai-trade/status` 永久回一个 `state:"running"` 而成员全 `stopped` 的僵尸分组；`live_auto` 的互斥闸被它永久占住；用户再点「暂停」还会给这个僵尸写出一张新的 `paused` 契约，进入**说谎循环** |
| **D-3** | P0 | **生命周期在前端没有实时通道**。`groupStateFromStatus` 是唯一写 `group.state` 的地方，只在 ① 挂载重发现 ② start 回执 ③ resume 回执 时跑；重发现 effect 还在同 `groupId` 时直接早退。15s 轮询只 patch 验收三键。 | `lib/useAiTradeGroupStream.ts:88-140` | 后端侧发生的任何迁移（进程重启后的 `process_restart` 暂停、`quit-pause`、会话自终止、另一个窗口的操作）**页面不刷新就永远看不到** |
| **D-4** | P0 | **已经在跑的那条轮询把真话全丢了**。`AiTradeToggle` 每 15 秒打一次 `GET /ai-trade/status`，回执里 `group` / `resumable` 全在，代码只取了 `aiTradeEnabled` 一个布尔。 | `components/Agent/AiTradeToggle.tsx:79-84` | 同 D-3。**零新增请求就能修好**，这是本轮最高杠杆的一处 |
| **D-5** | P0 | **两处各推一次生命周期，口径不同**。`AiTradeToggle` 走 `resolveAiTradeLifecycle`（只看 `group.state`）；`AiTradePanel` 走 `group.state === "paused" \|\| members.some(...)`（有成员级兜底）。 | `lib/aiTrade.ts:1115-1121`、`components/Chart/AiTradePanel.tsx:69-73` | 面板徽标说「已暂停 / 已停止」，头部开关同时还是开的、还挂着「⏸ 暂停」按钮 —— 用户对着两个互相矛盾的控件做真实资金决策 |
| **D-6** | P0 | **暂停期链路事件被整段丢弃**。`SessionLink.on_stream` 顶部判据是 `state != "running"` 即 `return`。而 `ingest_ai_auto` 是**纯窗口 + 指标**、一单不下。 | `okx/session_link.py:133-137` + `:62-85` | 暂停期间断线重连的**缺根永远补不回来**（`_pending_repair` 都没被置位）；恢复后 Agent 拿着一段有洞的窗口做真实资金决策，或因窗口不连续而判定失效 |
| **D-7** | P1 | **`resume()` 无条件把陈旧时钟重置为 now**（`_last_candle_wall = _last_px_wall = now`），且**从不检查 WS 是否还活着**（注释写着「pause 从不停 WS，所以 WS 一直连着」—— 这个前提在断联 / 休眠 / 换网络后不成立）。 | `ai_auto/session.py:759-768` | 续跑后看门狗被重新上弦，一条**已经死了**的行情要再过 `bar × 3` 才被说出来；期间定时唤醒会拿陈旧价开真实仓 |
| **D-8** | P1 | **`/resume` 走 20 秒超时档**（`timeoutFor` 未把它列入长跑路径），而冷续跑要跑 `resolve_active_provider` + 逐成员 `_prime()`（`prefill_htf` / `prefill_tactical` / `fetch_priming_rows` / 逐根影子回放）+ 交易所私有取数。 | `lib/httpTimeout.ts:106-113`、`ai_auto/resume.py:208-238` | 多标的 / 慢网下客户端先放弃：**后端续跑成功、界面停在「已暂停 + 继续失败」**（报告现象的镜像版）。再点一次「继续」吃 409 |
| **D-9** | P1 | **WS 的 `NOT_FOUND` 错误帧被静默丢弃**。`/ws/ai-trade/{sid}` 对不存在的会话回一帧 `{"type":"error","code":"NOT_FOUND"}` 后关闭；`routeMemberEvent` 的 switch 没有 `error` 分支，`ReconnectingWS` 无限重连。 | `api/ai_auto.py:664-670`、`lib/useAiTradeGroupStream.ts:225-253` | 后端重启后页面停在「运行中」并静默重连风暴，**没有任何一处出声** |
| **D-10** | P1 | **`lifecycle` 事件后端推了、前端没有 case**（上一轮已如实记账的欠账）。`session.pause()` / `resume()` 各推一条 `{"type":"lifecycle"}`。 | `ai_auto/session.py:666-668`、`:770-772` | 唯一一条**实时**的暂停 / 继续信号被丢在地上 |
| **D-11** | P2 | **`pause_group` 会用一个空回执覆盖真实持仓快照**。成员非 `running` 时 `session.pause()` 早退回执里没有 `position` 键，而 `_state_member_rows` 只判 `receipt is not None` 就 `row["position"] = receipt.get("position")` → `None`。 | `ai_auto/manager.py:956-971`、`session.py:645-647` | 契约里「这个标的还有仓」这个事实被抹掉，续跑复核与「结束」的孤儿仓提示同时失真 |
| **D-12** | P2 | **水合盲区对用户不可见**。`_hydration_failed` 时 `status()` 回一个 `state:"unknown"` 的占位（给 `live_auto` 用），但 `group_status()` 与 `resumable()` 都回 `None`，`/ai-trade/status` 于是回 `group:null, resumable:null`。 | `ai_auto/manager.py:1042-1055`、`api/ai_auto.py:425-435` | 「后端不知道有没有暂停分组」在界面上表现为「没有暂停分组」—— 与真实资金上「读不到 ≠ 没有」的既定极性矛盾 |

**这 12 条是同一个形状的 12 次复发**：某处把一个**意图**（我刚刚赋了值 / 我刚刚发了请求）当作**结果**
（它现在真的在跑）来使用。

### 1.3 本轮目标

1. **单一真源**：分组生命周期由**会话事实派生**，后端一处算完，前端一个字都不再自己拼。
2. **可反证**：wire 里除了 `state` 还必须带上让人**能反驳它**的事实 —— 每个成员的会话态、行情年龄、
   链路是否连着、暖机根数、下一次唤醒还有多久。用户看到「运行中」的同时看到「下一次决策 03:12」，
   这才叫恢复到了「Agent 执行交易的状态」。
3. **持续对账**：一条已经在跑的 15s 轮询升级成生命周期对账器（零新增请求），配合成员 WS 的
   `lifecycle` / `error` 事件，使界面在**任何**后端侧迁移之后 15 秒内自愈。
4. **拒绝比谎言好**：热续跑发现成员已死 ⇒ 整组拒绝并给出真实可走的两条路，绝不置 `running` 后回 200。
5. **暂停期不是观测盲区**：链路补根、链路告警在 `paused` 下照常工作（判据从 `state == "running"` 改
   为 `is_active`，与上一轮 §3.1.2 的重定向表同一条纪律）。

### 1.4 明确不做

- **不新增分组级 WS 通道**。本仓库的 AI-trade 实时通道是**按成员会话**建的，加一条分组通道要动会话
  管理与重连逻辑；而生命周期迁移是**分钟级**事件，一条已有的 15s 轮询 + 成员 WS 的 `lifecycle` 事件
  已经足够。（同 `horizon-acceptance` D-10 的既有判断。）
- **不改暂停 / 续跑的业务语义**：11 格离线差异矩阵、冷续跑七步、`S3 fail-closed`、整组原子续跑
  （`[P2-13]`）一律不动。本轮只改「状态怎么被算出来、怎么被说出来」。
- **不做 DB 迁移**。`ai_trade_group_states` 一列不加（详见 §六）。
- **不动 `components/ui/index.tsx`**（全仓共用 kit），新增视觉全部用既有 `Badge` / `Button` 的既有
  `tone` / `variant` 值域组合实现。

---

## 二、不变式（本轮新增，须在评审与实现中逐条对照）

| 编号 | 不变式 | 违反后果 |
|---|---|---|
| **L-1** | **分组生命周期是派生量，不是存储量。** `group_status()["state"]` 必须 `= derive_group_state(各成员会话态)`，`AiTradeGroup.state` 降级为「用户意图」并改名回显为 `declaredState`。 | D-1/D-2 原样复发 |
| **L-2** | **`running` 必须可反证。** 凡是下发 `state == "running"`，同一份 wire 必须同时下发 `lifecycle.armed` 与（`armed == false` 时）逐成员的 `reason` + `reasonText`。 | 「运行中」重新变成一句无法核对的话 |
| **L-3** | **任何生命周期迁移都必须能在 ≤ 15 秒内被界面观察到**，且不依赖用户刷新页面。 | D-3/D-4 复发 |
| **L-4** | **前端不得有第二处生命周期推导。** `resolveAiTradeLifecycle` 是唯一入口，`AiTradePanel` / `AiTradeToggle` / 对话框全部消费它。新增组件同样禁止自拼。 | D-5 复发 |
| **L-5** | **续跑要么全组武装成功，要么整组拒绝。** 不存在「置了 running 但有成员没起来」的中间态。 | D-1 复发 |
| **L-6** | **暂停期是「减风险 + 观测」作用域**：链路补根、链路告警、陈旧看门狗、兜底核实一律按 `is_active` 判定；只有「会不会开新仓」按 `state == "running"` 判定。 | D-6 复发；且这是上一轮 §3.1.2 表格的原始纪律 |
| **L-7** | **超时的写请求不得渲染成「失败」。** `/pause` `/resume` `/end` 超时一律走「结果未知 → 立即对账」，由对账结果决定文案。 | D-8 复发；且违反 `httpTimeout.ts` 已成文的规则 |

---

## 三、技术设计

### 3.1 派生态：`ai_auto/lifecycle.py`（新文件，纯核心，无 IO）

新建纯函数模块，镜像 `ai_auto/pause.py` 的「纯核心判定 + 文案单点生成」范式。**零 import 会话对象**，
只吃读数结构体，便于离线单测。

```python
# services/quant/app/ai_auto/lifecycle.py
STATE_RUNNING, STATE_PAUSED, STATE_STOPPED = "running", "paused", "stopped"

ARMED_OK          = "armed"
NOT_ARMED_STOPPED = "session_stopped"     # 会话已终止（自终止 / 被停）
NOT_ARMED_PAUSED  = "session_paused"      # 会话仍在暂停态
NOT_ARMED_STALE   = "feed_stale"          # 行情年龄超过 bar × staleness_factor
NOT_ARMED_LINK    = "feed_down"           # 实时链路不可达（链路登记簿判定）
NOT_ARMED_WARMUP  = "not_warmed"          # 窗口为空，尚未暖机完成

@dataclass(frozen=True)
class MemberReadings:
    inst_id: str
    state: str                 # running | paused | stopped
    stop_reason: str | None
    pause_reason: str | None
    warmed_bars: int
    feed_age_s: float          # now_wall - _last_candle_wall（<0 视为 0）
    stale_after_s: float       # BAR_SECONDS[bar] * live_auto_staleness_factor
    link_state: str            # up | idle | degraded | down | unknown（链路登记簿口径）
    next_wake_in_s: float | None
    next_bar_in_s: float | None
    rounds_completed: int

def derive_group_state(states: list[str]) -> str:
    """任一 running ⇒ running；否则任一 paused ⇒ paused；否则 stopped（空列表同样 stopped）。"""

def classify_member(r: MemberReadings) -> tuple[str, str]:
    """→ (reason_code, reason_text)。固定判定顺序（先致命后次要）：
       stopped → paused → not_warmed → feed_down → feed_stale → armed。"""

def group_lifecycle_wire(readings: list[MemberReadings], *, declared: str,
                         paused_ts: int, pause_reason: str | None,
                         resumed_count: int, now_ts: int) -> dict:
    """分组级生命周期 wire（§5.1 的形状）。判定性文案在此**单点**生成。"""
```

**判定顺序为什么是这一条**：`stopped` 必须排第一 —— 一个已经终止的会话，它的行情年龄与暖机根数都
没有意义，先说次要原因等于把最重要的事实埋起来（这正是 D-1 现场的读感）。

`link_state` 的取值来自既有 `okx/link_state.py` 的 `LinkRegistry.channel_state(key)`，键为
`f"ws:business:{inst_id}:{bar}:ai:{session_id}"`（`okx/ws_client.py:_stream_config` 已定义该键格式）。
**零新增探测**：这是一次纯内存读。

### 3.2 `armed`：一个能被反证的「在跑」

```
member.armed  ⟺ state == "running" ∧ warmed_bars > 0 ∧ link_state ∉ {down} ∧ feed_age_s ≤ stale_after_s
group.armed   ⟺ 全部成员 armed（整组口径，与 [P2-13] 整组原子续跑同极性）
group.membersArmed / membersTotal ：分子分母同时下发，前端绝不自己数
```

`armed` **不参与任何交易判定**，它纯粹是**呈现层的真值**：后端不会因为 `armed == false` 而拒绝下单
（那是既有 `_stale_degraded` / `_check_staleness` 的职责，不动）。这条分工必须写在代码注释里，防止
后人把它变成第二道风控闸而与既有闸漂移。

### 3.3 后端变更（B-1 … B-9）

**B-1（P0，修 D-2）** `AiTradeManager._note_session_stopped`：无活跃成员分支，在 `_mark_state_ended` /
`_forget_paused_ref` 之外**补上清组**，与 `stop_member` 的收敛逐字对齐。注释写明「三处清组点：
`stop_group` / `stop_member` / `_note_session_stopped`，判据统一为 `not any(m.session.is_active ...)`」。

```python
# [评审 R0-1] 身份守卫不可省 —— 本回调是 partial(self._note_session_stopped, group) 绑定的**旧分组**。
# 上一个分组的最后一个会话延迟自终止时，self._group 可能已经是**新分组**：无条件置 None 会把新分组
# 连同它占着的 live_auto 互斥位一起抹掉，界面整块消失而 Agent 其实还在跑 —— 这正是本轮要消灭的那句
# 谎话的镜像版（说「没在跑」而它在跑）。判据用 group_id 而不是 `is`：冷续跑会重建分组对象，同一个
# group_id 的新对象在语义上就是它自己。
if self._group is not None and self._group.group_id == group.group_id:
    self._group = None
```

**B-2（P0，修 D-1 与 L-5）** `AiTradeManager.resume_group` 热路径改为五步：

```
① 体检（锁内、无 IO）：states = [m.session.state for m in group.members]
   - 全 stopped                  ⇒ 按 B-1 的身份守卫清组（自愈历史残留）
                                    raise AiTradePreflightError("AI_TRADE_GROUP_ENDED", ..., 409)
   - 存在 stopped 且存在 paused  ⇒ raise AiTradeResumePartiallyDead(members=[...])  # 409
   - 全 paused                   ⇒ 继续
   - 存在 running                ⇒ raise AiTradePreflightError("AI_TRADE_NOT_PAUSED", ..., 409)
② 逐成员 receipt = await m.session.resume()，**收集**回执（绝不丢弃 —— D-1 的整条根因就是这一个赋值）
③ 复检 = _verify_armed(group)（**热 / 冷两条路径共用的同一段判定**，见下）
   不通过 ⇒ 回滚（对已成功的成员 await session.pause(PAUSE_REASON_RESUME_ROLLBACK, flatten=False)，
   best-effort、失败只 warning、绝不掩盖原始异常）后 raise AiTradeResumeNotArmed(members=[...])  # 409
④ 只有到这里才 group.state = RUNNING / pause_reason = None / paused_ts = 0 / resumed_count += 1
   并 _persist_group_state(...)
⑤ 冷路径（resume_cold_group 返回之后）走**同一个** ③：见下「两条路径共用复检」
```

```python
def _verify_armed(self, group) -> list[MemberReadings]:
    """③ 的单点实现：既是 L-5 的守卫，也是 [评审 R0-5] 的安全前置。

    [评审 R0-5] **必须在任何 monitor tick 之前跑完**：`_check_staleness` 在
    `elapsed > stale_after * 4` 且 `live_auto_flatten_on_stale=True`（[P2-7]）时会
    **直接 stop("stale_feed") 平掉真实持仓**。B-4 让陈旧时钟变得如实之后，一个「断了很久才点继续」
    的分组如果被置成 running 再由 monitor 去发现陈旧，用户点下的「继续」会在几秒内变成一次强平。
    故：复检判到 feed_stale ⇒ 当场不武装 ⇒ 回滚回 paused（暂停态下这条强平判据与今天逐字一致，
    不新增、也不豁免风险）；用户看到的是「继续失败，已退回暂停态（原因：行情不可达）」。
    """
```

**两条路径共用复检（[评审 R1-2]）**：`resume_cold_group` 结尾直接 `group.state = RUNNING`
（`ai_auto/resume.py:238`），而**用户报告里「关软件 → 重开 → 继续」正是这条路**。复检因此不能只挂在
热路径上，必须提到 `resume_group` 这一层：热路径 ②③④，冷路径 `resumed = await resume_cold_group(...)`
之后**同样跑 ③**，不通过则整组回滚（冷路径的回滚 = 既有 `resume_failed` 那段 `on_stopped=None` +
`session.stop()`，一字不改地复用）。冷路径刚 `_prime()` 完，正常情况下必然 armed —— 这段判定的价值
不在拦截率，而在**「说 running 之前先核实一遍」这条纪律没有第二种写法**。

**新增的暂停原因要一次改齐（[评审 R1-4]）**：`resume_rollback` 是一个**新的稳定枚举值**，
今天四个落点都没有它 —— `ai_auto/pause.py` 的 `PAUSE_REASON_*` 常量与 `PAUSE_REASON_TEXT`
（文案：「继续失败，已退回暂停」）、`apps/web/lib/aiTradeTypes.ts` 的 `AiTradePauseReason` 联合类型、
以及任何按该枚举分支的文案点。少改一处，回滚后的暂停原因在界面上就是**空的**（CLAUDE.md §五：
对外暴露的稳定枚举扩展等同于契约变更）。

**为什么不做半组续跑**：预算切分、`GroupExposureBook.unit_notional` 与验收卡口径都是分组级的，半组
续跑会让这三样同时失真 —— 与 `ai_auto/resume.py` 模块 docstring 里 `[P2-13]` 的理由**同一条**。
拒绝时的文案给两条真实可走的路：「结束并归档这个分组」/「结束后重新启动一个新分组」。

**B-3（P0，修 D-6 与 L-6）** `okx/session_link.py:SessionLink.on_stream` 的顶部判据放开到暂停期。

**[评审 R0-2] 判据不能写成 `not getattr(self._session, "is_active", True)`。** v1 的论证
（「live_auto / paper 没有 `is_active` 属性 ⇒ 兜底 True ⇒ 逐字节零回归」）**不成立**：这两类会话
**都有 `self.state`**（`live_auto/session.py:58,188`；`paper/session.py:50,250`），今天 `stopped`
会被这条早退挡住，改成 `is_active` 兜底 True 之后**永远挡不住** —— 一个已经停止的自动交易 / 模拟
会话会继续补根、继续写窗口、继续推链路告警。正确写法是显式两分支：

```python
async def on_stream(self, event: StreamEvent) -> None:
    session = self._session
    active = getattr(session, "is_active", None)
    if active is None:
        # live_auto / paper：没有 paused 态，**逐字保持今天的判据**（[评审 R0-2]）。
        if getattr(session, "state", "running") != "running":
            return
    elif not active:
        # ai_auto：只有**已终止**才退出；paused 是观测作用域（L-6 / 上一轮 §3.1.2 重定向表）。
        return
```

- `ingest_ai_auto` / `ingest_live_auto` / `ingest_paper` 三条 flavor 全是**纯窗口 + 指标 + 推 candle**，
  一单不下（已逐行核实），故它们属于 L-6 的「观测侧」。
- 上式对 `live_auto` / `paper` **逐字节零回归**（判据表达式与今天完全相同），对 `ai_auto` 只放开
  `paused` 这一个值 —— `is_active ⟺ state in {running, paused}`，故 `not is_active ⟺ state == "stopped"`。
- 补一行注释指回上一轮 §3.1.2 的重定向表，写明「本行是那张表在 `okx/` 下的第 20 行」。

**B-4（P0，修 D-7）** `AiTradeSession.resume()` 重写为「诚实续跑」：

```python
async def resume(self) -> dict:
    if self.state != "paused":
        return {"ok": False, "state": self.state, "reason": lifecycle.NOT_ARMED_STOPPED ...}
    now = time.time()
    paused_span = max(0.0, now - float(self.paused_ts or now))
    # ① 陈旧时钟**只扣掉暂停这一段，且绝不越过 now**（D-7 + [评审 R0-3]）：
    #    续跑前行情就已经断了 30 分钟的话，续跑后**立刻**判陈旧并如实告知，
    #    而不是再骗用户一个完整的 bar×3 窗口。
    #    夹取不可省：暂停期 WS 不停、`feed_candle` 的顶部判据是 `is_active`，这两个时钟在暂停期
    #    **照常被刷新到当前**（session.py:982/993）。无夹取地 += paused_span 会把它们推到**未来**，
    #    于是接下来整整 paused_span 里行情真的死了也判不出陈旧 —— 在一条风控读数上开 fail-open，
    #    与 D-7 的立论正好反向。
    self._last_candle_wall = min(now, self._last_candle_wall + paused_span)
    self._last_px_wall = min(now, self._last_px_wall + paused_span)
    self._last_decision_started_wall = now      # 首轮定时唤醒仍在 ~wake_interval_s 后（既有语义不变）
    # ② 行情通道体检 + 重新武装（断联 / 休眠 / 换网络之后 self._ws 可能已经死了）
    await self._rearm_feed()
    self._deciding = False
    self.state = "running"; self.pause_reason = None; self.resumed_ts = int(now)
    self._pause_result = None
    if self._monitor_task is None or self._monitor_task.done():
        self._monitor_task = asyncio.create_task(self._monitor_loop())
    await self._push({"type": "status", "data": self._status_payload()})
    await self._push({"type": "lifecycle", "data": {"action": "resumed", "reason": None,
                                                    "ts": self.resumed_ts,
                                                    "lifecycle": self.lifecycle_wire()}})
    return {"ok": True, "state": self.state, **self.lifecycle_wire()}

async def _rearm_feed(self) -> None:
    """WS 死了就**换一个新的**（幂等：`is_alive` 为真则零动作）。

    [评审 R0-4] **绝不能对同一个客户端 `stop()` 之后再 `start()`**：`OKXMarketWSClient._stop`
    是 latched `asyncio.Event` —— `stop()` 置位后从不清、`start()` 也不清（`ws_client.py:117,130-138`），
    而 supervisor 的主循环是 `while not stop.is_set()`（`ws_supervisor.py:101`）⇒ 重启出来的 task
    立刻退出并在 `finally` 里 `close_channel`。照那样写的结果是「续跑之后行情永久不来」且
    `is_alive` 恒假：比 D-7 更静默的一种死亡。
    """
    if self._ws is not None and self._ws.is_alive:
        return                                  # 正常热续跑路径：零动作
    old, self._ws = self._ws, None
    if old is not None:
        await old.stop()                        # 幂等；它 cancel 并 await 全部 endpoint task
    self._ws = OKXWSClient(self.inst_id, self.bar, self.feed_candle,
                           on_stream=self._link.on_stream, owner=f"ai:{self.session_id}")
    self._ws.start()        # 与 start() 的装配**逐字同源**（session.py:500-502），不另起一套
```

`OKXMarketWSClient` 新增只读属性（`okx/ws_client.py`）：

```python
@property
def is_alive(self) -> bool:
    """至少一条 endpoint 任务仍在跑。`_run_endpoint` 只吞 CancelledError，
    supervisor 抛别的异常时任务会静默死掉 —— 这个属性就是那条静默的出口。"""
    return bool(self._tasks) and any(not t.done() for t in self._tasks)
```
`OKXWSClient` 转发同名属性（它是 `OKXMarketWSClient` 的薄包装）。读到「已 done 且带异常」的 task
时顺手取一次 `task.exception()`（[评审 R2-3]）：否则 asyncio 会打一条 "Task exception was never
retrieved" —— 而那条噪音恰恰就是本属性想要说出口的那件事，不该被丢在事件循环的日志里。

> **[评审 R0-5] 一条必须写进代码注释的交互**：`_check_staleness` 在 `elapsed > stale_after * 4` 且
> `live_auto_flatten_on_stale=True` 时会**直接 `stop("stale_feed")` 平掉真实持仓**（`session.py:952-955`，
> [P2-7] 的既有取舍）。今天 `resume()` 把时钟重置为 `now`，恰好挡住了「点继续 → 几秒后被强平」这条路；
> 本轮让时钟变得如实之后，这道保护必须由 **B-2 ③ 的复检**接管：复检判到 `feed_stale` 就不武装、
> 回滚回 `paused`，用户看到的是一句「继续失败，已退回暂停态」而不是一次没人解释的强平。
> 暂停态下该强平判据与今天**逐字一致**（`_check_staleness` 本就是 `is_active` 判据），本轮不豁免、
> 也不加严 —— 只是不让「继续」这个动作成为它的触发器。

**B-5（P0，L-2）** `AiTradeSession.lifecycle_wire()` 新方法，返回 §5.2 的形状；并在
`_status_payload()` 中新增一个 `"lifecycle"` 键（加法键，`-> dict` 无 `response_model`，不会被吞）。
`nextWakeInS = max(0, wake_interval_s - (now - _last_decision_started_wall))`（`wake_interval_s <= 0` ⇒
`None`）；`nextBarInS` 由 `executor.bar_anchor_ts + BAR_SECONDS[bar] - now` 求得（`bar_anchor_ts == 0`
⇒ `None`）。

**B-6（P0，L-1）** `AiTradeManager.group_status()`：

```python
readings = [self._member_readings(m) for m in group.members]
derived  = lifecycle.derive_group_state([r.state for r in readings])
...
"state": derived,                      # ← 派生真值（前端唯一消费的那个）
"declaredState": group.state,          # ← 意图（诊断 / 告警用；两者不等即 bug 的信号）
"lifecycle": lifecycle.group_lifecycle_wire(readings, declared=group.state, ...),
```
`_member_row()` 同步加 `"lifecycle": member.session.lifecycle_wire()`（成员卡片要能逐标的说明）。

**B-7（P1，修 D-11）** `_state_member_rows`：覆盖持仓快照的条件从 `receipt is not None` 收紧为
`receipt is not None and "position" in receipt`。早退回执（成员非 `running`）不再抹掉契约里的真实持仓。

**B-8（P1，修 D-12）** `GET /ai-trade/status` 新增两个加法键：
`"lifecycleUnknown": manager.is_hydration_blind()`（新增只读方法，返回 `self._hydration_failed`）与
`"serverTs": int(time.time())`（前端倒计时用服务端时钟锚点，避免本机时钟偏移 —— 本仓库已有
`live-db-location-and-ai-trade-ts-dual-clock` 的双时钟教训）。

**B-9（P1，修 D-8）** `/resume` 的耗时如实反映到前端超时档（见 F-7）；后端侧同时把冷续跑的
**准入锁持有时间**记一条 `logger.info`（`resume cold pipeline took %.1fs`），使慢续跑可被诊断。

### 3.4 前端变更（F-1 … F-9）

**F-1（L-4）** `lib/aiTrade.ts`：

```ts
/** 唯一入口。优先吃后端派生的 `lifecycle.state`，其次 `group.state`，最后冷占位。 */
export function resolveAiTradeLifecycle(group, resumable): AiTradeLifecycle
/** 「运行中但没真的在跑」：running ∧ !armed。 */
export function resolveAiTradeArmed(group): { armed: boolean; rows: NotArmedRow[] }
/** 徽标四态 + 色调（单点）：idle | running | paused | stopped，running 再按 armed 分两档。 */
export function describeAiTradeLifecycleBadge(...): { label: string; tone: BadgeTone }
/** 下一次动作倒计时文案（服务端锚点 + 本地 tick），`null` ⇒ 不渲染。 */
export function formatNextWake(lifecycle, nowTs): string | null
```
`AiTradePanel` 里那两行自拼的 `stopped` / `paused` 判据**删除**，改调本模块。

**[评审 R1-5] `AiTradeLifecycle` 从三态加宽到四态是一次契约变更，必须显式接住。** 该类型今天
只有 `"idle" | "running" | "paused"`，且**全仓只有一个调用点**（`AiTradeToggle.tsx:90`），它把结果
喂给启动门禁：`hasActiveSession: running || paused`。加上 `stopped` 之后这个表达式会把「本地留痕的
已停止分组」判成**可启动** —— 这**恰好是对的**（B-1 之后后端此刻已经清了组，容量闸不会 409），
但必须写在这里并配一条守卫用例，否则下一轮一定有人「顺手把 stopped 也算进 hasActiveSession」，
把「会话已结束却再也开不了新的」这个死结重新系上。

**[评审 R1-7] 徽标要带分数，不要用整组与门抹掉活着的成员。** `group.armed` 是全成员与门
（§3.2），2 个标的死 1 个时它为 `false` —— 只渲染「运行中 · 未武装」会让用户以为两个都停了。
徽标文案取 `membersArmed/membersTotal`：全部武装 ⇒「运行中」；部分 ⇒「运行中 · 1/2 未武装」（warn 色）；
零 ⇒「运行中 · 未武装」（warn 色）。逐标的原因由 `NotArmedBanner` 按 `notArmed[]` 展开。

**[评审 R1-6] 倒计时要有回落，并且绝不在不该走秒的时候走秒。** `wake_interval_s <= 0`（**未装墙钟
唤醒是常见配置**）时 `nextWakeInS` 为 `null`，而 §1.3 目标 2 恰恰把这条倒计时当成「已经恢复到 Agent
执行交易的状态」的主要证据 —— 没有回落，最常见的那个配置下证据面直接缺席。判定顺序（单点）：

```
nextWakeInS 非空 ⇒ 「下一次决策 mm:ss」
否则 nextBarInS 非空 ⇒ 「下一根 K 线 mm:ss」（K 线收盘同样会起决策轮，它是如实的第二证据）
否则 ⇒ null（不渲染，绝不显示「--:--」占位）
lifecycle !== "running" 或 armed === false ⇒ 一律 null（对着一个不会到来的时刻读秒是新的谎话）
```

**F-2（L-3，修 D-3/D-4，最高杠杆）** `lib/useAiTradeGroupStream.ts`：把既有 15s 轮询从「验收三键」
升级为「**生命周期 + 验收三键**」的统一对账器，**零新增请求**：

```ts
// [评审 R0-7] 门禁只留 accountMode，**必须去掉 `!groupId` 早退**（今天是
// `if (accountMode !== "live" || !groupId) return`，useAiTradeGroupStream.ts:120-121）。
// 不去掉的话，下面判定表七行里「重发现」「冷暂停接管」「lifecycleUnknown」三行**永远不可达** ——
// 恰恰是「本地什么都没有、后端其实有东西」这个最需要自愈的态。
const pull = () => {
  if (document.hidden) return;
  aiTradeApi.status().then((res) => {
    reconcileAiTradeLifecycle(res, {...setters});   // ← 新增纯函数，可单测
  }).catch(() => setAiTradeSyncState("unknown"));   // 连不上后端 ≠ 没有分组（L-7 极性）
};
```

**[评审 R0-7] 对账只能有一个 owner，且它必须挂在页面级 hook 上。** `AiTradeToggle` 的 `statusQ`
今天也在 15s 轮询同一个端点（D-4），若它同时写 store 就成了第二个 owner：两处各按自己的时序覆盖
同一份生命周期，正是 D-5「两处口径」的换皮复发。收敛方式：

| 谁 | 本轮之后 |
|---|---|
| `useAiTradeGroupStream`（**唯一 owner**，挂在 `page.tsx`，不随任何面板折叠 / 归档而卸载） | 15s（快窗口 3s）拉 `/ai-trade/status` → `reconcileAiTradeLifecycle`，并把 `aiTradeEnabled` 一并写进 store |
| `AiTradeToggle.statusQ` | `refetchInterval` 撤掉（只保留挂载时那一次，供首屏门禁），`aiTradeEnabled` 改读 store |

这样实盘模式下的**总请求数从 2 次 / 15 秒降到 1 次 / 15 秒**（今天有分组时是两条轮询在打同一个端点），
R-6 担心的 3 秒快窗口压力也被这笔削减抵掉。宿主选页面级 hook 而不是 Toggle 的理由是成文教训：
组件会随面板可见性消失，而**自愈能力不能随之消失**（上一轮「提示必须挂在两种态下都还在的地方」同源）。

`reconcileAiTradeLifecycle(res, setters)`（新纯函数，落在 `lib/aiTrade.ts`，随 `aiTrade.test.ts` 建用例）
的判定表 —— **这张表是本轮前端的核心**：

| 后端回执 | 本地状态 | 动作 |
|---|---|---|
| `group` 非空，同 `groupId` | 有同组 | `patchAiTradeGroupLifecycle({state, pauseReason, pausedTs, resumedCount, lifecycle, acceptance, exits, funding})`（**窄 action，绝不碰 members / trades**） |
| `group` 非空，异 `groupId` | 有旧组 | `setAiTradeGroup(groupStateFromStatus(res.group))`（换组，重建成交流是对的） |
| `group` 非空 | 无组 | 同上（重发现） |
| `group` 空、`resumable` 非空 | 任意 | `setAiTradeGroup(null)` + `setAiTradeResumable(res.resumable)`（冷暂停接管） |
| `group` 空、`resumable` 空、`lifecycleUnknown` | 任意 | **什么都不清**，只置 `aiTradeSyncState = "unknown"`（L-7 的极性：读不到 ≠ 没有） |
| `group` 空、`resumable` 空、本地有组且成员全 `stopped` | — | 置本地 `state:"stopped"` 并**保留面板**（终态留痕，见 F-5），不 `clearAiTradeRuntime` |
| `group` 空、`resumable` 空、本地有组且成员未全 stopped | — | `clearAiTradeRuntime()` + 一条 `aiTradeLifecycleNotice`：「AI 自动交易会话已在后端结束（可能是软件重启或会话自行终止），请到实盘面板核对持仓」 |

轮询节奏：常态 15s；**动作后进入快节奏** —— `useAppStore.aiTradeReconcileUntil`（新瞬态字段，
时间戳）非空且未过期时用 3s，供 pause / resume / end 之后的 30 秒窗口做即时收敛。
**`visibilitychange` 转前台时立刻补拉一次**：`document.hidden` 那道既有守卫会跳过隐藏期的所有 tick，
不补这一次，L-3 的「≤15 秒」在「切回标签页」这条最常见路径上要退化成「最坏 30 秒」。L-3 的准确
表述因此是：**前台 ≤15 秒（快窗口 ≤3 秒），转入前台的瞬间立即对账**。

**[评审 R1-3] 窄 action 必须同时合并成员行的生命周期字段。** 只 patch 分组级的话，一个已死成员的
卡片会**永远**显示「运行中」—— 它的 WS 恰好被 F-4 关掉了（`NOT_FOUND` 后不再重连），再也没有任何
事实会到达那张卡片。合并规则（白名单，逐字写死）：按 `instId` 取 `res.group.members[i]` 的
`state` / `pauseReason` / `pausedTs` / `stopReason` / `degraded` / `lifecycle` 六个键覆盖
`member.session`；**绝不触碰** `trades` / `position` / `decisions` / `traces` / `summary`
（后者是 WS 流的产物，覆盖即闪烁 / 丢历史）。成员缺席于回执时 ⇒ 保留本地行不动（由 F-5 的终态留痕
负责它的去向），**不删**。

**F-3（修 D-10）** `routeMemberEvent` 新增：

```ts
case "lifecycle": {
  const evt = event.data as AiTradeLifecycleEvent;   // {action, reason, ts, lifecycle}
  setters.patchAiTradeMemberSession(instId, {
    state: evt.action === "paused" ? "paused" : "running",
    pauseReason: evt.reason ?? null, pausedTs: evt.ts, lifecycle: evt.lifecycle,
  });
  setters.requestAiTradeReconcile();     // 触发一次即时对账（分组级结论仍由后端算）
  return;
}
```
**注意**：本 case **不**直接写分组级 `state` —— 分组结论是 `derive_group_state` 的产物，前端只负责
把成员事实更新掉并请后端重算一次。这条纪律避免把 L-1 在前端重新破一次。

**F-4（修 D-9）** `routeMemberEvent` 新增 `case "error"`：`NOT_FOUND` ⇒ `requestAiTradeReconcile()`；
同时 `useAiTradeGroupStream` 记录该 `sessionId` 进一个 `deadSessions` 集合，**停止对它重连**
（`ws.close()`），避免重连风暴。其余错误码 ⇒ 落 `member.session.message` 由既有状态行渲染。

**F-5（终态留痕）** 分组在后端消失但成员已如实 `stopped` 时，面板保留一张**已停止卡**：标的、
`stopReason` 中文、会话净额、以及一枚「知道了」按钮（点击才 `clearAiTradeRuntime()`）。理由与
`end` 的孤儿仓提示同源 —— 会话自行终止（会话止损 / 强平）是用户**必须**知道的事，面板整块消失
等于没说。

**F-6（HCI 主体）** `AiTradeToggle` 与 `AiTradePanel` 的四态 + 一个正交标志：

```
idle      →  开关 off，可开启
running   →  开关 on（绿），[⏸ 暂停]，徽标「运行中」+ 「下一次决策 03:12」倒计时 chip
             armed == false 时徽标降级为「运行中 · 未武装」（warn 色）并展开 NotArmedBanner
resuming  →  开关 on（禁用 + 脉冲），徽标「正在恢复…」，按钮全禁用（本地瞬态，不来自后端）
paused    →  开关 off（禁用），徽标「已暂停」，[▶ 继续] [结束]
stopped   →  开关 off，徽标「已停止」，[知道了]
unknown   →  徽标「与后端同步中…」，动作按钮**全部禁用**（读不到就不让点，L-7 极性）
```

`resuming` 这一态是本轮 HCI 的关键：从点下「继续」到 `lifecycle.armed === true` 之间**可能有几十秒**
（冷续跑要暖机），而今天这段时间界面上什么都没有 —— 用户只能靠猜。新的续跑对话框在提交后**不立刻
关闭**，改为进入「正在武装」清单（见 F-8）。

**F-7（修 D-8，L-7）** `lib/httpTimeout.ts`：把 `/ai-trade/{groupId}/resume` 归入长跑档。

**[评审 R2-1] 不要新建第三张表 —— 那张表已经存在。** `LONG_SEGMENT_SUFFIXES`
（`httpTimeout.ts:52-59`）就是「家族前缀 + 末段」范式本身（`{ prefix, suffix }` 对），v1 提议的
`LONG_ACTION_FAMILIES/SUFFIXES` 与它同形且更弱（笛卡尔积会把 `/ai-trade/{id}/resume-preview` 之外
的组合也一起放宽）。正确改法是**在既有表里加两行 + 加一个专属档位**：

```ts
const LONG_SEGMENT_SUFFIXES = [
  ...,
  { prefix: "/ai-trade/", suffix: "/resume", ms: RESUME_MS },          // 冷续跑：provider + 逐成员 _prime()
  { prefix: "/ai-trade/", suffix: "/resume-preview", ms: RESUME_MS },  // 预检同样要读交易所
] as const;
```
（`ms` 为可选字段，缺省仍取 `LONG_MS`，既有六行**一字不改**。）档位取
`RESUME_MS = 180_000`（3 分钟）：`FAST_MS < RESUME_MS < PROXY_TIMEOUT_MS`，故代理不会先放弃、
而客户端也不会像 `LONG_MS` 那样一等 5 分钟。`isWritePath` 无需改动 —— `/resume` 已在
`WRITE_ACTION_SUFFIXES` 里（实读核实），超时文案本来就会说「结果未知」。

**[评审 R0-6] 超时分流的判据接错了层，必须改。** `aiTradeApi.request` 的 `catch` 已经把超时经
`toApiError` 换成了 `ApiRequestError("TIMEOUT", …)`（`aiTradeApi.ts:31`、`api.ts:59-61`），而
`isTimeoutError` 判的是原始异常的 `err.name`（`"TimeoutError"` / `"AbortError"`）—— 到 mutation 的
`onError` 那一层它**恒为 false**，L-7 承诺的「结果未知」一次都不会出现。正确判据：

```ts
const isTimeout = e instanceof ApiRequestError && e.code === "TIMEOUT";
```
命中即：**不写 error**，改为 `setAiTradeLifecycleNotice(e.message)`（那条文案由 `timeoutMessage`
单点生成，已经是「本次操作结果未知……」，前端不要再拼第二句）+ `requestAiTradeReconcile()`，
由 15s→3s 的快对账给出最终结论。`pause` / `end` 两个动作**同款处理**（L-7 说的是三个端点，
不是只有 `/resume`）。

**F-8（HCI）** `AiTradeResumeDialog` 增加第三屏「正在武装」：逐成员四行清单（会话已建 / 行情已连 /
已接管持仓 / 下一次决策），每行的真值全部取后端 `lifecycle`，**前端一个判断都不下**。
全部就绪 ⇒ 1 秒后自动关闭并在头部闪一条成功提示；超过 `ARMING_TIMEOUT_MS = 45_000` 仍未 armed ⇒
把清单原地变成如实的失败说明 + 两个出路按钮（「重试继续」/「结束并归档」）。

**F-9（store）** `lib/store.ts` 新增（全部瞬态、不持久化）：

```ts
aiTradeSyncState: "ok" | "unknown";                 // 后端水合盲区 / 对账失败
aiTradeReconcileUntil: number | null;               // 快对账窗口截止时间戳（ms）
patchAiTradeGroupLifecycle: (patch: Partial<...>) => void;   // 窄 action，与 patchAiTradeGroupAcceptance 同款
requestAiTradeReconcile: () => void;                // 置 aiTradeReconcileUntil = now + 30_000
setAiTradeSyncState: (s) => void;
```
**`patchAiTradeGroupLifecycle` 必须是窄 action**：用 `setAiTradeGroup(groupStateFromStatus(...))` 做
这件事会把每个成员的 `trades` 清空（那条映射写死 `trades: []`），而它跑在一个 3~15 秒的轮询里 ——
成交流会周期性消失。这条与 `patchAiTradeGroupAcceptance` 的注释是同一条教训，务必照抄。

「窄」的准确边界（[评审 R1-3]）：它**要**写分组级七键 + 逐成员的六个生命周期键（见 F-2 的白名单），
**不**写 `members[].trades / position / decisions / traces / summary`，**不**增删成员槽。
`aiTradeEnabled` 也由本 action 一并落 store（F-2 的单 owner 收敛所需），故 store 再加一个瞬态字段：

```ts
aiTradeEnabled: boolean;      // 后端总闸，唯一写点 = 对账器（Toggle 不再自己轮询）
```

**F-10（[评审 R1-1]，L-2）** `AiTradeToggle.describeStartError` 必须认得新错误码，否则本轮两个新
409 的 `members` 明细**一个字都不会出现**。今天它只认 `AI_TRADE_GROUP_PREFLIGHT`
（`AiTradeToggle.tsx:491-497`），其余 code 一律回落 `err.message` —— 而 §3.6 承诺的正是「逐标的列出
谁还活着、谁已结束」。改法（判据从「一个 code」变成「一张 code 白名单」，函数体不动）：

```ts
const MEMBER_DETAIL_CODES = new Set([
  "AI_TRADE_GROUP_PREFLIGHT",
  "AI_TRADE_RESUME_PARTIALLY_DEAD",   // 谁还活着 / 谁已结束
  "AI_TRADE_RESUME_NOT_ARMED",        // 谁没武装成功、卡在哪一步
]);
```
并把它从 `AiTradeToggle.tsx` 提到 `lib/aiTrade.ts`（续跑对话框 F-8 的失败屏要渲染同一份明细，
留在组件里就会被抄第二遍 —— 那是 D-5 的形状）。**成员级 `message` 一律由后端下发**：本仓库
「稳定语义码直出 UI」的事故已经发生过一次，前端不得按 code 自己拼中文。

### 3.5 关键时序

**（一）热暂停 → 热续跑（会话健在）**

```
UI ─POST /pause─▶ manager.pause_group ─▶ 逐成员 session.pause()
                                         └─ push status + lifecycle{action:"paused"}
   ◀─200 {state, notice, members}        └─ W4 整行 upsert（失败上抛）
UI: setAiTradeLifecycleNotice(notice) + requestAiTradeReconcile()
    ── WS lifecycle 到达 ⇒ 成员即时置 paused（≈100ms）
    ── 3s 对账回来 ⇒ 分组派生态 = paused（权威确认）
UI ─POST /resume─▶ ① 体检（全 paused）② 逐成员 resume（含 _rearm_feed）③ 复检 ④ 置 running
   ◀─200 group_status（state=running + lifecycle.armed）
UI: 对话框进入「正在武装」→ armed 为真即自动关闭
```

**（二）冷续跑（软件重启后）**

与上一轮的七步流水线**逐字不变**，只在两端加东西：入口处前端进入 `resuming` 态并把超时档抬到 3 分钟；
出口处回执带 `lifecycle`，前端据它渲染武装清单。中途每完成一个成员，后端 `logger.info` 一行
（诊断用，不新增 wire）。

**（三）断联自愈（本轮新增，L-6）**

```
WS 断  ─▶ supervisor 退避重连（既有，不动）
        └─ SessionLink.on_stream(down) ── 暂停期**不再被吞**（B-3）⇒ 45s 后如实推一条状态行
WS 回  ─▶ SessionLink.on_stream(live) ⇒ _pending_repair 判定 ⇒ 独立 task 拉缺根 → ingest_ai_auto
        └─ 暂停期同样执行 ⇒ 续跑时窗口是**连续**的
续跑   ─▶ _rearm_feed()：is_alive 为假才重建 WS（幂等，正常路径零动作）
        └─ _last_candle_wall += 暂停时长 ⇒ 若行情本来就断着，下一个 monitor tick 立即判陈旧并出声
```

**（四）会话自终止（D-1/D-2 的现场）**

```
暂停期会话止损触发 ─▶ session.stop("session_stop_loss") ─▶ on_stopped = _note_session_stopped
                    ├─ 仍有活跃成员 ⇒ 从契约摘掉本成员（既有）
                    └─ 无活跃成员   ⇒ 置 ended + 清冷占位 + **清 self._group（B-1 新增）**
UI（3~15s 内）：对账发现 group=null/resumable=null 且本地成员全 stopped
             ⇒ 保留「已停止」卡 + stopReason 中文 + [知道了]
用户点「继续」（竞态窗口内）：后端 ① 体检 ⇒ 全 stopped ⇒ 409 AI_TRADE_GROUP_ENDED（B-2）
             ⇒ 前端渲染后端文案，**绝不**出现「恢复成功但不交易」
```

### 3.6 失败矩阵（每格都必须有一句用户能读懂的话）

| 场景 | 后端行为 | 稳定语义码 | 界面 |
|---|---|---|---|
| 全成员已终止时点「继续」 | 409 + 自愈清组 | `AI_TRADE_GROUP_ENDED` | 「这个分组的所有会话都已结束（原因：会话止损）。请重新启动一个新的 AI 自动交易分组。」 |
| 部分成员已终止时点「继续」 | 409 + `members` 明细 | `AI_TRADE_RESUME_PARTIALLY_DEAD` | 逐标的列出谁还活着 / 谁已结束 + 两条出路 |
| 续跑后复检未武装 | 409 + 回滚到 paused | `AI_TRADE_RESUME_NOT_ARMED` | 「继续失败，已退回暂停态（原因：BTC-USDT-SWAP 实时链路不可达）。链路恢复后可再试。」 |
| 冷续跑取数失败 | 409（既有） | `AI_TRADE_RESUME_UNVERIFIABLE` | 既有文案不动 |
| `/resume` 客户端超时 | — | — | 「结果未知，正在与后端核对…」+ 3s 快对账给结论（**绝不说失败**） |
| 运行中但行情陈旧 | 200，`armed=false` | `feed_stale` | 徽标「运行中 · 未武装」+ 横幅「行情已 12 分钟没有新 K 线，Agent 暂不响应；交易所侧止盈止损仍生效」 |
| 后端水合盲区 | `lifecycleUnknown=true` | — | 「与后端同步中…」+ 动作按钮全禁用 |
| WS 会话不存在 | 一帧 error 后关闭 | `NOT_FOUND` | 停止重连 + 立即对账（由对账决定最终态） |
| 续跑复检判到行情不可达 / 陈旧（[评审 R0-5]） | 409 + 回滚到 paused，**不置 running、不触发强平** | `AI_TRADE_RESUME_NOT_ARMED` | 「继续失败，已退回暂停态（原因：BTC-USDT-SWAP 行情已 47 分钟没有新 K 线）。行情恢复后可再试；期间交易所侧止盈止损仍生效。」 |
| 多标的中部分成员未武装（[评审 R1-7]） | 200，`armed=false` + `membersArmed=1/2` | 逐成员 `reason` | 徽标「运行中 · 1/2 未武装」+ 横幅**逐标的**说明，绝不用整组与门把还在正常交易的那个也说成停了 |
| `/pause` `/end` 客户端超时（[评审 R0-6]） | — | `TIMEOUT` | 与 `/resume` 同款：「结果未知」+ 快对账，三个写端点一视同仁 |

---

## 四、文件 / 模块变更计划

### 4.1 后端（`services/quant/`）

| 文件 | 新建/修改 | 意图（一行） |
|---|---|---|
| `app/ai_auto/lifecycle.py` | **新建**（~170 行） | 派生态与 `armed` 的纯核心判定 + 判定性文案单点生成 |
| `app/ai_auto/manager.py` | 修改 | B-1 清组；B-2 续跑四步；B-6 `group_status` 派生态 + `lifecycle` 键；B-7 持仓覆盖收紧；新增 `_member_readings` / `is_hydration_blind` |
| `app/ai_auto/session.py` | 修改 | B-4 诚实 `resume()` + `_rearm_feed()`；B-5 `lifecycle_wire()` 并挂进 `_status_payload`；`lifecycle` 事件带上 `lifecycle` 载荷 |
| `app/okx/session_link.py` | 修改（1 行 + 注释） | B-3：`on_stream` 判据 `state == "running"` → `is_active`（暂停期补根与告警不再被吞） |
| `app/okx/ws_client.py` | 修改（~10 行） | 新增 `is_alive` 只读属性（`OKXMarketWSClient` + `OKXWSClient` 转发） |
| `app/api/ai_auto.py` | 修改 | B-8：`/ai-trade/status` 加 `lifecycleUnknown` / `serverTs` 两个加法键 |
| `app/api/ai_trade_lifecycle.py` | 修改 | `/resume` 的新错误类映射（`_http_error` 已透传 `members`，只需让新异常继承 `AiTradePreflightError` 并带 `.members`） |
| `tests/test_ai_trade_lifecycle_core.py` | **新建** | `derive_group_state` / `classify_member` / `group_lifecycle_wire` 的离线用例（含 11 组组合） |
| `tests/test_ai_trade_resume_guard.py` | **新建** | B-2 四步的四条分支 + 回滚（含 `_FakeSession` 扩展） |
| `tests/test_ai_trade_pause_manager.py` | 修改 | 补 B-1 的清组用例（自终止后 `status()` 必须回 `None`） |
| `tests/test_ai_trade_pause_session.py` | 修改 | 补 B-4：陈旧时钟只扣暂停段、`_rearm_feed` 幂等、`resume()` 回执带 `lifecycle` |
| `tests/test_okx_session_link.py` | 修改 | 补 B-3：`state == "paused"` 时补根**必须**发生；`live_auto` / `paper` 的 `stopped` 会话**必须仍被挡住**（[评审 R0-2] 的回归护栏） |
| `tests/test_ai_trade_pause_api.py` | 修改 | 补 §3.6 的四条错误码 + `/status` 新键 |

**`_FakeSession` 三处替身同步（成文教训，勿忘）**：`test_ai_trade_group.py` / `test_ai_trade_api.py` /
`test_auto_trade_admission.py` 各有一个 `_FakeSession`，本轮 manager 新读到 `lifecycle_wire()` 与
`_member_readings` 需要的属性（`stop_reason` / `_last_candle_wall` / `_window` / `_ws`），**三个替身都要
跟上**，否则那三个文件会整片红。建议在 `lifecycle.py` 里让 `MemberReadings` 的构造点
（`manager._member_readings`）对缺失属性一律 `getattr(..., 默认值)`，把替身的改动面压到最小。

### 4.2 前端（`apps/web/`）

| 文件 | 新建/修改 | 意图（一行） |
|---|---|---|
| `lib/aiTrade.ts` | 修改 | F-1 四个新导出（唯一推导点）+ F-2 的 `reconcileAiTradeLifecycle` 纯函数 + F-10 迁入的 `describeStartError`（[评审 R1-1]） |
| `lib/aiTradeTypes.ts` | 修改 | 新增 `AiTradeLifecycleWire` / `AiTradeNotArmedRow` / `AiTradeLifecycleEvent`；`AiTradeGroupRow` 加 `lifecycle` / `declaredState`；`AiTradeGroupStatusResponse` 加 `lifecycleUnknown` / `serverTs` |
| `lib/useAiTradeGroupStream.ts` | 修改 | F-2 轮询升级为对账器（含快节奏）；F-3 `lifecycle` case；F-4 `error` case + `deadSessions`；`groupStateFromStatus` 补 `lifecycle` 键（**逐键手抄的映射，不补就永远拿不到**） |
| `lib/store.ts` | 修改 | F-9 两个瞬态字段 + 三个 action（窄 action，绝不碰 `members` / `trades`） |
| `lib/httpTimeout.ts` | 修改 | F-7：在**既有** `LONG_SEGMENT_SUFFIXES` 加两行 + 可选 `ms` 档位（[评审 R2-1]：不新建第三张表） |
| `components/Agent/AiTradeToggle.tsx` | 修改 | F-6 四态 + `resuming`；`statusQ` 撤掉自轮询改读 store（[评审 R0-7] 单 owner）；超时分流按 `code === "TIMEOUT"`（[评审 R0-6]）；快对账触发 |
| `components/Agent/AiTradeResumeDialog.tsx` | 修改 | F-8「正在武装」第三屏（逐成员四行清单 + 45s 如实失败） |
| `components/Chart/AiTradePanel.tsx` | 修改 | F-1 消灭自拼判据；新增 `NotArmedBanner` + 「下一次决策」倒计时 chip + F-5 已停止卡 |
| `lib/aiTrade.test.ts` | 修改 | 新增导出的用例（含 `reconcileAiTradeLifecycle` 七行判定表逐格） |
| `lib/useAiTradeGroupStream.pause.test.ts` | 修改 | `lifecycle` / `error` 事件路由 + 快对账 |
| `lib/httpTimeout.test.ts` | 修改 | `/ai-trade/g1/resume` 必须落长跑档；`/ai-trade/g1/stop` 仍落 20s |
| `components/Chart/AiTradePanel.test.tsx` | 修改 | 四态徽标 + `NotArmedBanner` + 倒计时 |
| `components/Agent/AiTradeToggle.test.tsx` | 修改 | `resuming` 态、超时不报「失败」、`unknown` 禁用动作 |
| `components/Agent/AiTradeResumeDialog.test.tsx` | 修改 | 武装清单三屏流转 |

### 4.3 文档

| 文件 | 意图 |
|---|---|
| `docs/plans/ai-trade-lifecycle-truth/spec.md` | 本文件（评审节点升 v2，实现节点追加「实施过程发现的方案缺陷」段） |

---

## 五、接口设计

### 5.1 `GET /api/ai-trade/status`（加法，不破坏既有键）

```jsonc
{
  "group": {
    "groupId": "aitradegrp_xxx",
    "state": "running",              // ★ 派生真值（derive_group_state），前端唯一消费点
    "declaredState": "running",      // ☆ 意图（诊断用；与 state 不等即 bug 信号）
    "pauseReason": null, "pausedTs": 0, "resumedCount": 2,
    "lifecycle": {
      "state": "running",
      "armed": false,                // ★ running 是否**真的**在武装状态
      "membersArmed": 1, "membersTotal": 2,
      "asOfTs": 1755500000,
      "notArmed": [
        { "instId": "ETH-USDT-SWAP", "state": "running",
          "reason": "feed_stale",
          "reasonText": "行情已 12 分钟没有新 K 线，Agent 暂不响应；交易所侧止盈止损仍生效" }
      ],
      "nextWakeInS": 192,            // 全组最近一次唤醒（null = 未装定时唤醒）
      "nextBarInS": 480
    },
    "members": [ { "instId": "...", "state": "running", "lifecycle": { /* §5.2 */ }, ... } ]
  },
  "aiTradeEnabled": true,
  "resumable": null,
  "lifecycleUnknown": false,         // ★ 后端水合盲区（读不到 ≠ 没有）
  "serverTs": 1755500000             // ★ 倒计时锚点（双时钟纪律）
}
```

### 5.2 成员级 `lifecycle`（同时出现在 `/status` 的成员行、WS `status.lifecycle`、WS `lifecycle` 事件）

```jsonc
{
  "state": "running", "armed": true,
  "reason": "armed", "reasonText": "",
  "warmedBars": 300,
  "feedAgeS": 7, "staleAfterS": 180,
  "linkState": "up",
  "nextWakeInS": 192, "nextBarInS": 480,
  "roundsCompleted": 41,
  "pauseReason": null, "pausedTs": 0, "resumedTs": 1755499880,
  "stopReason": null
}
```

### 5.3 `POST /api/ai-trade/{groupId}/resume`

- **成功**：200，回执 = `group_status()`（已含 `state` / `lifecycle`）。语义从「我把它置成 running 了」
  变成「**我核实过它现在是 running，而且这是它武装到什么程度的证据**」。
- **新增失败码**（均 409，`detail = {code, message, members?}`，`members` 与既有
  `AI_TRADE_GROUP_PREFLIGHT` 同形，前端 `describeStartError` 直接复用）：
  - `AI_TRADE_GROUP_ENDED`
  - `AI_TRADE_RESUME_PARTIALLY_DEAD`
  - `AI_TRADE_RESUME_NOT_ARMED`
- **[评审 R1-1] 前端读点不是自动的**：`ApiRequestError.members` 这条通路是通的（`aiTradeApi.ts` 解析
  `body.error.members`，`_http_error` 也确实透传），但**渲染**那一层今天只认 `AI_TRADE_GROUP_PREFLIGHT`。
  两个新码必须一并进 F-10 的白名单，否则「wire 里有事实、用户那里没有」第 N+1 次复发。
- **`members[].message` 由后端生成**，`reason` 用 §3.1 的稳定码，前端只做「`instId`：`message`」的拼接。
- 既有 `AI_TRADE_NOT_PAUSED` / `AI_TRADE_RESUME_BLOCKED` / `AI_TRADE_RESUME_EXPIRED` /
  `AI_TRADE_RESUME_UNVERIFIABLE` / `AI_TRADE_RESUME_NOT_ACKNOWLEDGED` / `AI_TRADE_RESUME_CONTRACT_INVALID`
  **一律不动**。

### 5.4 WS 事件（`/api/ws/ai-trade/{sessionId}`）

| 事件 | 变化 |
|---|---|
| `status` | `data` 新增 `lifecycle` 键（加法） |
| `lifecycle` | 既有信封 `{action, reason, ts}` 新增 `lifecycle` 键；**前端本轮才第一次消费它** |
| `error` | 协议不变（`{code, message}`）；**前端本轮才第一次消费它** |

### 5.5 前端内部契约（无 HTTP，但同样是接口）

```ts
type AiTradeLifecycle = "idle" | "running" | "paused" | "stopped";
resolveAiTradeLifecycle(group, resumable): AiTradeLifecycle          // 唯一推导点（L-4）
resolveAiTradeArmed(group): { armed: boolean; rows: NotArmedRow[] }
reconcileAiTradeLifecycle(res: AiTradeGroupStatusResponse, setters): void  // §3.4 F-2 判定表
```

---

## 六、数据模型

**零 DB 迁移。** 表结构一列不加，理由与上一轮 `rebuild_continuity` 的不变式同源：

> **派生量绝不落库 —— 落两份必然漂移，而这里漂移的是「Agent 到底在不在跑」。**

- `ai_trade_group_states.status`（`running` / `paused` / `ended`）**语义不变**：它是**磁盘契约**的状态，
  回答的是「有没有一份可以续跑的东西」；本轮新增的 `lifecycle.state` 是**进程内会话**的状态，回答的是
  「它现在在不在跑」。两者刻意不合并 —— 冷启后前者有值而后者不存在，正是「可续跑」这一态的定义。
- `AiTradeGroup.state` 保留但**降级为意图**，仅经 `declaredState` 回显，且只用于诊断与
  「`declaredState != state` 即 bug」的告警日志。
- 新增的全部结构都是**进程内瞬态**：`MemberReadings`（frozen dataclass）、`lifecycle` wire（每次现算）。
- 前端 `store` 新增两个字段均为**瞬态、不进 persist 白名单**（与 `aiTradeGroup` / `aiTradeResumable`
  同类）。

---

## 七、配置

新增 **1 个**（其余全部复用既有）：

| 键 | 默认 | 作用 |
|---|---|---|
| `ai_trade_lifecycle_truth_enabled` | `True` | 整轴总闸。`False` ⇒ `group_status()` 回落 `getattr(group,"state",...)`、不下发 `lifecycle` 键、`SessionLink` 判据回落 `state == "running"`、前端因 `lifecycle` 缺席自动回落到 `group.state`（**一键逐字回滚到本轮之前**） |

复用（不新增、不改默认值）：`ai_trade_pause_enabled`、`live_auto_staleness_factor`、
`live_auto_poll_interval_s`、`okx_link_down_alert_s`、`okx_link_gap_repair_max_bars`。

> ⚠️ 配置联动（成文教训，**本轮实测只剩两处**）：① `services/quant/app/config.py` 真的声明字段
> （`extra="ignore"` 会吞未声明的 env）；② `.env.example` 补文档。
> **[评审 R2-2]** 第三处「`desktop/src/lib/config.js` 白名单放行」本轮**不需要做**：那里是
> **前缀规则**（`key.startsWith('AI_TRADE_')` 一律透传，`config.js:80`），`AI_TRADE_LIFECYCLE_TRUTH_ENABLED`
> 自动放行。写一条本不需要的改动进 spec，比漏一条更难发现 —— 实现节点请勿照抄 v1 那句。

前端常量（`lib/aiTrade.ts` / `lib/httpTimeout.ts`）：
`AI_TRADE_RECONCILE_FAST_MS = 3_000`、`AI_TRADE_RECONCILE_WINDOW_MS = 30_000`、
`AI_TRADE_ARMING_TIMEOUT_MS = 45_000`、`RESUME_MS = 180_000`。

---

## 八、测试与验收标准

### 8.1 后端单测（`services/quant/tests/`，pytest）

**纯核心（离线、零 IO、必须先写）**
1. `derive_group_state`：`[]` → `stopped`；`["stopped"]*n` → `stopped`；`["paused","stopped"]` → `paused`；
   `["running","paused"]` → `running`；`["running"]*n` → `running`。
2. `classify_member` 判定顺序：同时满足 `stopped` 与 `feed_stale` 时**必须**回 `session_stopped`。
3. `group_lifecycle_wire`：`membersArmed/Total` 计数正确；`notArmed` 只含未武装成员；
   `nextWakeInS` 取全组最小值；无定时唤醒 ⇒ `null`。

**回归 bug（每条先写能稳定复现的用例，再动业务代码 —— CLAUDE.md §七）**
4. `resumes_are_rejected_when_a_member_session_died`（D-1）：构造 2 成员分组，把其中一个置 `stopped`，
   `resume_group` **必须** 409 `AI_TRADE_RESUME_PARTIALLY_DEAD`，且 `group.state` 仍是 `paused`。
5. `resume_rolls_back_when_post_check_fails`（L-5）：让第二个成员的 `resume()` 回 `ok=False`，
   断言第一个成员被回滚到 `paused` 且抛 `AI_TRADE_RESUME_NOT_ARMED`。
6. `group_is_cleared_when_last_session_self_terminates`（D-2）：触发 `on_stopped` 至无活跃成员，
   断言 `manager.status() is None` 且 `group_status() is None`。
7. `link_events_are_handled_while_paused`（D-6）：会话置 `paused`，喂一条 `live` 的 `StreamEvent`
   （`downtime_s > bar_seconds`），断言补根 task 被创建且窗口被追加；再对一个 `live_auto` 假会话
   （无 `is_active` 属性）喂同样事件，断言行为**逐字不变**。
8. `resume_does_not_reset_stale_clock`（D-7）：`_last_candle_wall` 设为 2 小时前、`paused_ts` 设为
   10 分钟前，`resume()` 后断言 `now - _last_candle_wall ≈ 110 分钟`（**不是 0**），且下一个
   `_check_staleness` 立即置 `_stale_degraded`。
9. `resume_rearms_dead_ws`（D-7）：`is_alive` 为假 ⇒ `resume()` 后 WS 被重建；为真 ⇒ 零动作（幂等）。
10. `pause_receipt_without_position_does_not_wipe_snapshot`（D-11）。
11. `status_exposes_hydration_blind_spot`（D-12）：`note_hydration_failed()` 后
    `GET /ai-trade/status` 的 `lifecycleUnknown` 为 `true`。
12. **总闸回滚**：`ai_trade_lifecycle_truth_enabled=False` 时 `/status` 的回执与本轮之前**逐字节相同**
    （不含 `lifecycle` / `declaredState` / `lifecycleUnknown` / `serverTs`）。

### 8.2 前端单测（`apps/web`，vitest；组件用例须 `NODE_ENV=test`）

13. `reconcileAiTradeLifecycle` 七行判定表**逐格**一条用例（含「读不到时什么都不清」那一格）。
14. `lifecycle` / `error` 事件路由；`NOT_FOUND` 之后**不再重连**。
15. `resolveAiTradeLifecycle` 四态 + `resolveAiTradeArmed`；`AiTradePanel` 与 `AiTradeToggle`
    在同一份 store 快照下**必须给出同一个态**（L-4 的守卫用例）。
16. `timeoutFor("/ai-trade/g1/resume") === RESUME_MS`；`timeoutFor("/ai-trade/g1/stop") === FAST_MS`。
17. 续跑超时 ⇒ 界面**不出现「失败」二字**，出现「结果未知」并触发快对账。
18. `unknown` 态下 `暂停` / `继续` / `结束` 三个按钮 `disabled` 均为真。
19. 「下一次决策」倒计时以 `serverTs` 为锚点（伪造 `Date.now()` 偏移 1 小时，断言文案不受影响）。

**评审新增（每条对应一个 P0/P1，缺一即该条修法没有护栏）**

20. `stop_of_old_group_does_not_clear_new_group`（[评审 R0-1]）：建组 A → 令其全部成员自终止 →
    起组 B → **再**触发一次组 A 的 `on_stopped` 回调，断言 `manager.status()` 仍是组 B。
21. `stopped_live_auto_session_is_still_ignored_by_link`（[评审 R0-2]）：一个 `state == "stopped"`
    的 `live_auto` 假会话喂 `StreamEvent`，断言**没有**补根 task、**没有**告警 —— 与本轮之前逐字一致。
22. `resume_never_pushes_feed_clock_into_the_future`（[评审 R0-3]）：暂停期持续喂确认根（模拟 WS 不停）
    使 `_last_candle_wall ≈ now`，暂停 3 小时后 `resume()`，断言 `_last_candle_wall <= now`，
    且随后人为静默 `stale_after + 1` 秒即判陈旧（**不是**要再等 3 小时）。
23. `rearm_feed_builds_a_new_client`（[评审 R0-4]）：把 `is_alive` 置假，断言 `resume()` 后
    `session._ws` **不是**同一个对象且新对象已 `start()`；`is_alive` 为真时断言对象**未被更换**。
24. `resume_refuses_instead_of_flattening_a_stale_group`（[评审 R0-5]）：`live_auto_flatten_on_stale=True`
    且行情已陈旧超过 `stale_after * 4` 时点继续，断言 409 `AI_TRADE_RESUME_NOT_ARMED`、
    分组回到 `paused`、且**没有**任何平仓调用发生。
25. `timeout_is_recognised_after_toApiError`（[评审 R0-6]）：让 `aiTradeApi.resume` 抛
    `ApiRequestError("TIMEOUT", …)`，断言界面**不出现「失败」二字**、出现「结果未知」、触发快对账
    （直接对 DOMException 断言的用例会绿着放过这个 bug，**必须**走真实的 `toApiError` 路径）。
26. `reconciler_runs_without_a_local_group`（[评审 R0-7]）：`aiTradeGroup === null` 时挂载 hook，
    断言轮询照常发起，且「后端有组 ⇒ 重发现」「后端只有 resumable ⇒ 冷暂停接管」两行判定真的执行。
27. `dead_member_card_stops_saying_running`（[评审 R1-3]）：分组仍在、某成员 `stopped` 且其 WS 已关，
    断言 15 秒内该成员卡片的态由对账改写为「已停止」，而 `trades` / `position` **未被清空**。
28. `resume_rollback_reason_has_text`（[评审 R1-4]）：回滚后的 `pauseReason` 在后端
    `PAUSE_REASON_TEXT` 与前端联合类型里都存在，断言界面渲染出中文而不是空串。
29. `badge_reports_the_fraction`（[评审 R1-7]）+ `countdown_falls_back_to_next_bar`（[评审 R1-6]）：
    2 成员 1 个未武装 ⇒ 徽标含「1/2」；`nextWakeInS === null` 且 `nextBarInS` 非空 ⇒ 显示「下一根 K 线」；
    `paused` / `!armed` ⇒ **不渲染**倒计时。

### 8.3 手工验收（真实资金前必须逐条走通，在模拟账户 + 小额实盘各一遍）

| # | 操作 | 期望 |
|---|---|---|
| A1 | 运行中点「暂停」 | ≤1 秒内徽标变「已暂停」、开关变灰禁用、暂停横幅出现、`notice` 如实（含平仓失败 / 无兜底时的强提示） |
| A2 | 暂停态点「继续」 | 对话框进入「正在武装」清单 → 全绿后自动关闭 → 徽标「运行中」+ 倒计时开始走 |
| A3 | A2 之后等到倒计时归零 | **真的**起了一轮决策（执行轨迹面板出现新一轮） |
| A4 | 暂停期拔网线 30 分钟再插回，然后「继续」 | 补根发生（K 线连续）；若插回前点继续 ⇒ 徽标「运行中 · 未武装」+ 明确说明行情不可达；网络恢复后自动转「运行中」 |
| A5 | 暂停期人为触发会话止损 | 面板出现「已停止」卡 + 原因；点「继续」得到 `AI_TRADE_GROUP_ENDED` 的中文说明，**不会**出现「恢复成功但不交易」 |
| A6 | 关软件（`quit-pause`）→ 重开 → 「继续」 | 冷续跑全程有进度反馈；3 分钟内不超时；武装清单逐项变绿 |
| A7 | 页面开着的情况下重启后端 | ≤15 秒内界面自愈（转为「可继续」卡或「已停止」卡），**不再**停在「运行中」 |
| A8 | 关掉 `ai_trade_lifecycle_truth_enabled` | 全部行为逐字回退到本轮之前 |
| A9 | 多标的（2 个）运行中，人为让其中一个的行情断掉 | 徽标「运行中 · 1/2 未武装」+ 横幅**只**点名断掉的那个；另一个的成交与决策照常流动（[评审 R1-7]） |
| A10 | 用断点 / 代理把 `/resume` 卡住 3 分钟以上 | 界面**不出现「失败」二字**，出现「结果未知，正在与后端核对…」，随后由快对账给出真结论（[评审 R0-6]） |
| A11 | 暂停期让行情断到超过 `stale_after × 4`，且把 `live_auto_flatten_on_stale` 打开，然后点「继续」 | **不发生任何平仓**；界面显示「继续失败，已退回暂停态（原因：行情不可达）」（[评审 R0-5]，须在模拟账户上做） |

### 8.4 基线

- 后端：`pytest -k "ai_trade or ai_auto or session_link"` 在**干净 worktree**（先 `cp services/quant/.env`）
  上取基线，本轮只许增不许减。**全量 pytest 不得并发跑**，且须 `python -u`。
- 前端：`pnpm vitest run` 基线同上；`apps/web/node_modules` 残缺时用
  `pnpm install --ignore-workspace --frozen-lockfile --force`（**必须带 `--ignore-workspace`**）。
- `AiTradeLaunchModal.test.tsx` 的抖动是**既存**的负载敏感问题（见仍 open 的
  `ai-trade-modal-revert-recurrence`），**不要**当成本轮弄坏的。
- 源码正则冻结类用例一律写 `\r?\n`（`core.autocrlf=true`）。

---

## 九、风险与缓解

| # | 风险 | 影响 | 缓解 |
|---|---|---|---|
| R-1 | **派生态改变了 `group_status()["state"]` 的语义**，可能有第三方读点依赖旧语义 | 静默行为漂移 | 实现前 `grep -rn "group_status()\|status()\[.state.\]"` 全仓核实读点；已知消费者只有前端与 `live_auto._require_capacity`（后者只判 `is not None`，不读 `state`）；`declaredState` 保留旧值兜底 |
| R-2 | **B-1 清组改变互斥时机**：僵尸分组曾长期占着 `live_auto` 的互斥位，清掉后 `live_auto` 立刻可启动 | 用户在有交易所侧遗留持仓时启动另一套自动化 | 清组路径上**必须**同时置一条 `aiTradeLifecycleNotice`（F-5 的已停止卡），把「这些持仓从此没人管」说清楚；这与 `end_group` 的孤儿仓提示是同一条口径 |
| R-3 | **B-3 把 `on_stream` 放开到暂停期**，补根会在暂停期消耗 REST 配额并写窗口 | 配额 / 数据竞态 | `ingest_ai_auto` 已确认一单不下；补根跑在独立 task 且 `_pending_repair` 有幂等守卫；配额影响与「暂停期 monitor 照常跑」同量级（既有决策），补一条注释说明取舍 |
| R-4 | **`_rearm_feed` 重建 WS 引入新的竞态**：旧 supervisor 还在退避睡眠时新的已建连 | 双份订阅、事件重复 | `is_alive` 为假才动手；重建前必须 `await self._ws.stop()`（它会 cancel 并 await 全部 task）；`_window.is_duplicate` 是既有去重护栏 |
| R-5 | **回滚（把已 resume 的成员重新 pause）会多打私有 REST**（`_read_exchange_position` + 兜底核实） | 慢 + 配额 | 回滚是**异常路径**，正确性优先于成本；且 `pause()` 是既有的减风险动作，恒可执行。回滚失败只 `warning`，不掩盖原始异常 |
| R-6 | **3 秒快对账窗口**在多标的下把 `/ai-trade/status` 打得更频繁（该端点会现算 `_acceptance_bundle`） | 后端 CPU | 窗口只有 30 秒且只在动作后开启；`document.hidden` 时不打（既有守卫）；如实测有压力，降级为「只在快窗口内请求 `?brief=1`」——**本轮不预先优化**，但把这条写进 spec 以便评审判断 |
| R-7 | **前端新增瞬态字段与 persist 白名单**：误进白名单会把一个瞬时的 `unknown` 固化到下次开机 | 开机即显示「同步中」 | **[评审 R2-5] 风险等级下调**：`partialize` 是**显式白名单**（`store.ts:1177+`），不主动写进去就不会被持久化，真实风险只剩「有人手滑加一行」。用例仍保留（成本近零）：persist 快照里**不得**出现三个新键 |
| R-8 | **`lifecycle` 是逐键手抄的映射的又一个受害者**：`groupStateFromStatus` 不补这一行，「刷新页面后重发现」这条路径上生命周期恒缺席 | 最难发现的那类坑 | 该文件已有三条同款注释（`acceptance` / `discipline` / `state`）；本轮**必须**补第四条，并在 `useAiTradeGroupStream.pause.test.ts` 里加一条专守它的用例 |
| R-9 | **`declaredState != state` 的告警日志刷屏** | 日志噪音 | 只在**迁移瞬间**记一次（用一个 `_last_logged_mismatch` 去重），且是 `logger.warning` 不是 `error` |
| R-10 | **spec 自身可能是缺陷来源**（上一轮成文教训） | 照字面写就错 | 实现节点必须在本文件追加「实施过程发现的方案缺陷」段，逐条记录；评审节点必须对 §1.2 的 12 条**逐条复核代码落点**，任何一条对不上即降级为 P2 或删除。**评审已执行：12 条全部成立（见《评审记录》§0），但修法本身逮到 7 条 P0 —— 这条风险在本轮是命中的** |
| R-11 | **[评审 R0-7] 单 owner 收敛动了 `AiTradeToggle` 的数据来源**：`aiTradeEnabled` 从 `statusQ` 改读 store，首屏 store 未水合时它是 `false` | 首屏瞬间「启动」按钮被门禁挡住 | 这与 L-7 的极性一致（读不到就不让点），但要**说人话**：门禁文案在 `aiTradeSyncState === "unknown"` 时显示「正在与后端同步…」而不是「本机未开启 AI 自动交易」——后者是一句假话 |
| R-12 | **[评审 R0-5] 复检拦下续跑之后，用户可能反复点「继续」** | 每次都打一遍私有 REST | 失败文案必须给出**可执行的下一步**（「行情恢复后可再试」/「结束并归档」），而不是一句「请重试」；`AiTradeResumeDialog` 在同一原因连续失败两次后把主按钮降级为次要按钮 |
| R-13 | **[评审 R1-3] 成员行合并的白名单会随后端加字段而过期** | 新的成员级生命周期字段悄悄拿不到 | 白名单与 §5.2 的 wire 形状写在**同一处注释**里，并在 `useAiTradeGroupStream.pause.test.ts` 加一条「§5.2 的每个键都必须在合并结果里出现」的守卫用例（同 `groupStateFromStatus` 那四条注释的范式） |

---

## 十、交付顺序（建议）

1. **纯核心先行**：`ai_auto/lifecycle.py` + `test_ai_trade_lifecycle_core.py`（零 IO，先绿）。
2. **P0 后端三条**：B-1（清组）→ B-2（续跑四步）→ B-3（链路判据），每条**先写复现用例**。
3. **B-4/B-5**：诚实 `resume()` + `lifecycle_wire()` + `ws_client.is_alive`。
4. **B-6/B-7/B-8**：wire 装配与两个加法键。
5. **前端真值化**：F-9（store）→ F-1（推导点）→ F-2（对账器）→ F-3/F-4（WS 两个 case）。
6. **前端呈现**：F-6（四态）→ F-8（武装清单）→ F-5（已停止卡）→ F-7（超时档）→ F-10（错误码白名单）。
7. **手工验收 A1~A8**，最后跑全量基线。

每一步结束都应能独立编译 / 起服务；第 2 步之后已可消灭报告现象的主路径，第 5 步之后界面自愈能力成立。

---

## 十一、术语

| 词 | 含义 |
|---|---|
| **派生态（derived state）** | 由各成员会话的 `state` 现算出的分组态，`group_status()["state"]` 的新语义 |
| **意图态（declared state）** | `AiTradeGroup.state`，某次动作赋的值；本轮起只用于诊断 |
| **armed（已武装）** | `running` 且真的具备思考条件（会话活、暖机过、链路通、行情不陈旧） |
| **对账（reconcile）** | 前端拿 `GET /ai-trade/status` 的权威回执覆盖本地生命周期投影的动作 |
| **热 / 冷续跑** | 进程内还有会话对象 / 只剩磁盘契约（沿用上一轮定义） |
| **武装清单** | 续跑对话框第三屏，逐成员四行的「它到底起来了没有」证据 |

---

---

## 评审结论

### **有条件通过**

本方案的立论是对的，而且是本仓库这一年里**最该被写下来的一条**：把「Agent 在不在跑」从一个被赋值的
字段改成一个由会话事实派生、且**带着让人能反驳它的证据**一起下发的真值。§1.2 的 12 条根因经逐条实读
**全部成立**，§二的七条不变式没有一条是凑数的，§六「派生量绝不落库」的论证足以单独存档。评审因此不在
方向上设任何条件。

条件全部落在**修法的执行**上。本轮评审逮到 7 条 P0、7 条 P1，形状高度一致：**方案在描述「改哪一行」
时是准的，在描述「改完之后那一行会发生什么」时有四次落空** —— 一次会清掉别人的分组（R0-1）、一次会
让停止了的会话继续观测（R0-2）、一次会把风控时钟推进未来（R0-3）、一次会让行情永远回不来（R0-4）。
另有两次是「写了、跑了、但用户一个字看不到」（R0-6 的超时文案、R1-1 的成员明细），一次是让判定表
三行永不可达（R0-7）。这些**已在正文逐条修正并补齐了对应用例（新增用例 20~29、手工验收 A9~A11）**，
文档本身不再有未解决的 P0 / P1。

### 放行条件（实现节点必须逐条兑现，缺一即视为本轮未完成）

1. **七条 P0 的修法照本文 v2 实现，不得回到 v1 的写法。** 尤其是 R0-2（`SessionLink` 的两分支判据）
   与 R0-4（`_rearm_feed` 必须换代），这两条 v1 的原话读起来都很合理，**照着写一定出事**。
2. **每条 P0 先有能稳定复现的用例，再动业务代码**（CLAUDE.md §七）。用例 20~29 是最低集合，
   其中 22 / 23 / 24 三条守的是**真实资金路径**，不允许用「行为显然正确」替代。
3. **A11 必须在模拟账户上真的走一遍。** 「点继续 → 几秒后被强平」是本轮唯一一条会造成直接资金损失的
   新路径，它只在 `live_auto_flatten_on_stale=True` 时存在，而那正是没人日常测的那个配置。
4. **总闸 `ai_trade_lifecycle_truth_enabled=False` 的回执必须逐字节等于本轮之前**（用例 12）。
   四处回落（`group_status` 派生、`lifecycle` 键、`SessionLink` 判据、前端因键缺席而回落）缺一，
   这个开关就只是一句声明。
5. **实现节点须在本文件追加「实施过程发现的方案缺陷」段**（R-10）。本轮评审已经证明这份 spec 会错，
   实现时再发现的每一条都要落纸 —— 包括「评审也没看出来的那些」。
6. **基线只许增不许减**，且必须在**干净 worktree** 上取（先 `cp services/quant/.env`）；
   `AiTradeLaunchModal.test.tsx` 的抖动是既存问题，不要算在本轮账上。

### 不作为放行条件（但建议顺手做）

P2 六条（R2-1 ~ R2-6）已登记在《评审记录》§3：其中 R2-1（复用既有 `LONG_SEGMENT_SUFFIXES`）与
R2-2（**不要**去改 desktop 白名单，那是前缀规则）已经直接写进正文，照做即可；其余四条属于工程卫生，
不做也不影响本轮目标。

---

*本文件 v2 = Subtask #0（方案设计）产出 + Subtask #1（设计评审）定稿。评审记录见文首，评审结论见本节；
P0 / P1 已全部在正文修正完毕，无遗留。实现节点请在本文件末尾追加「实施过程发现的方案缺陷」段。*

---

## 实施过程发现的方案缺陷

> R-10 与《评审结论》放行条件 5 的兑现。评审已经证明这份 spec 会错（7 条 P0 + 7 条 P1），
> 实现时又发现 **13 条**：其中 **2 条是阻断级**（照 v2 字面写，代码根本跑不起来或当场抛异常），
> 6 条是「照字面写就是又一次『有写点没读点』、『新造一句谎话』或**直接碰真实资金**」，
> 3 条是取舍需要被写下来。其中 **I-11 会造成直接的资金损失**，与 R0-5 同形、同后果，只是从另一扇门进来。
> 全部已按下述修法实现，并各自配了用例。

### A. 阻断级（照 v2 字面实现会直接失败）

| # | 缺陷 | 为什么会错 | 本轮修法 |
|---|---|---|---|
| **I-1** | **§3.1 让 `ai_auto/session.py` 直接调 `LinkRegistry.channel_state(key)`，而那是一条被冻结用例明令禁止的 import。** | `tests/test_okx_session_link.py::test_sessions_only_bind_and_never_reimplement_the_link_logic` 是 okx-link-resilience [评审 P1-8] 留下的**源码冻结**用例：三处 session 里不许出现任何链路实体名（`fetch_missing` / `get_link_registry` / 三个 `ingest_*`），只许有一次 `make_session_link(...)` 绑定。spec 写「`link_state` 的取值来自既有 `okx/link_state.py`」时**没有注意到这条纪律**，照字面 import 会让该用例（以及 ≤10 行绑定预算那条）当场红。 | 链路态读取**下沉**为 `SessionLink.channel_state(owner)`（`okx/session_link.py`）—— 那本来就是它该待的地方：键格式、endpoint、登记簿全是链路的知识。会话侧只留一行 `self._link.channel_state(f"ai:{self.session_id}")`。新增用例 `test_channel_state_reads_the_registry_without_probing`。 |
| **I-2** | **§3.3 B-4 的 `_rearm_feed` 伪代码直接用 `self._link.on_stream`，而 `_link` 只在 `start()` 里被赋值。** | 一个从未 `start()` 过的会话对象（启动回滚路径、以及全部会话层测试替身）调 `resume()` 会在 `_rearm_feed` 里 `AttributeError` —— 一次「继续」变成一个 500。这与本轮「拒绝比谎言好」的立论方向一致但形式相反：它是**崩溃**而不是谎言，同样不可接受。 | `link is None` ⇒ 记一条 `logger.warning` 并早退。理由写进注释：重建一条**没人处理其链路事件**的野连接（补根与告警都挂在 `_link` 上）比不重建更坏；这一格交给 B-2 ③ 的复检去说「没武装成功」。 |

### B. 「照字面写就是又一次『有写点没读点』/『新造一句谎话』」—— 其中 I-11 直接碰真实资金

| # | 缺陷 | 结论 | 本轮修法 |
|---|---|---|---|
| **I-3** | **§3.4 F-9 说 `aiTradeEnabled` 由 `patchAiTradeGroupLifecycle` 一并落 store —— 但该 action 的定义是「分组不存在时静默空转」。** | 而 `aiTradeEnabled` 恰恰在**没有分组**时也必须落：首屏门禁、`idle` 态开关的可用性、启动弹窗的实盘段禁用全读它。照字面实现，「本机开没开 AI 自动交易」这个事实在**最常见的 idle 态**下永远拿不到 —— 而 F-2 又已经把 `AiTradeToggle` 的自轮询撤掉了，那是它唯一的来源。这是本轮自己给自己造的一个 D-4。 | 拆出独立的 `setAiTradeEnabled`，仍由对账器**单点**调用（[评审 R0-7] 的单 owner 收敛不变）。用例：`reconcileAiTradeLifecycle` 的「无分组时也必须落」一条。 |
| **I-4** | **§3.4 F-4 说「其余错误码 ⇒ 落 `member.session.message` 由既有状态行渲染」—— 全仓没有任何一处读 `session.message`。** | `grep` 零命中，`AiTradeSessionRow` 里也没有这个键。更讽刺的是后端**早就在下发它**：`_check_staleness` 推「行情连接异常…」、`SessionLink._push_message` 推「实时链路已断开 N 秒 / 已恢复 / 补齐了 N 根」，全是 `{...status, message}`。照字面实现就是把一句真话写进一个没人看的字段 —— 本轮要消灭的那个形状，在本轮的修法里第 N+2 次复发。 | ① `AiTradeSessionRow` 补 `message`；② **补上渲染落点**：`AiTradePanel` 的成员行新增一行 `data-testid="ai-trade-member-message"`（`--warn` 色 + `title` 全文）。用例：`F-4 的读点：会话状态行必须真的渲染出来`。 |
| **I-5** | **§3.4 F-4 的 `error` case 若照 F-3 的形状写成 `patchAiTradeMemberStatus(instId, {state, message})`，会覆盖成员的真实 `state`。** | `patchAiTradeMemberStatus` 是**整体 spread**（`...status` 合并进 `m.session`），必须带一个 `state`。一条错误帧不是一次状态迁移 —— 替它猜一个 `state`（无论猜 `running` 还是 `stopped`）就是新造一句谎话，与 L-1 正面冲突。 | 改用窄的 `patchAiTradeMemberSession(instId, { message })`，**绝不碰 `state`**。用例：`其余错误码落成员状态行，且绝不替它猜一个 state`。 |
| **I-6** | **§4.1 只说「`test_ai_trade_pause_session.py` 补 B-4 用例」，没说那里**已经有一条用例在断言被修掉的行为**。** | `test_pause_is_idempotent_and_resume_resets_clocks` 断言 `resume()` 之后 `_last_candle_wall > 0` —— 那正是 D-7 的根因（无条件重置为 `now`）。B-4 落地后它必然红，而一条「本轮弄红了既有用例」的红在基线对比里极易被误读成回归。 | 该用例改名为 `..._resume_rearms_the_decision_clock` 并重写断言：只有决策计时器仍重置（首轮唤醒在 ~`wake_interval_s` 后，与 `start()` 同一条理由），两个行情时钟不再被抹平。注释写明「本用例在 B-4 之后改了断言」及理由。 |
| **I-7** | **`AiTradeSessionRow` 缺 `pauseReason` / `pausedTs`，而 §3.4 F-2 的窄 patch 白名单逐字列出了这两个键。** | 后端 `info()` 与 WS `status` **两条通路都在下发**它们，漏的只是类型声明；但白名单是用 `Pick<AiTradeSessionRow, …>` 写的，缺键直接编译不过。 | 补两个可选键并注明「两条通路都在下发，漏的只是类型声明」。 |
| **I-11** | **§3.3 说冷路径的回滚「= 既有 `resume_failed` 那段…一字不改地复用」—— 而那段用的是默认 `flatten=True`。** | 既有那段跑在 `session.start()` **抛错**时：会话根本没起来，交易所上不可能有它开出的仓位，平不平都一样。但本轮把它挪去承担一件**完全不同**的事——**复检未过**时的回滚，而此刻 S6 的 `_adopt_group` 已经把交易所上那笔**真实持仓**灌进了 executor。照字面「一字不改地复用」，一个「断了很久才点继续」的冷续跑会在复检判它未武装的同一秒里**把用户的真实仓位平掉** —— 那正是 [评审 R0-5] 花了整整一节要堵死的「点继续 → 几秒后被强平」，只是从另一扇门进来，而 R0-5 只检查了热路径那扇门。 | 冷路径回滚显式 `stop("resume_failed", flatten=False)`；既有 S7 的那段**一行未动**（它的语境不同，默认值在那里是对的）。理由写进 `_rollback_resume` 的 docstring。新增用例 `test_cold_rollback_never_flattens_the_adopted_position`。 |
| **I-12** | **§3.3 B-2 ① 的「全 stopped ⇒ 按 B-1 的身份守卫清组 + 409」只收敛了内存，没收敛磁盘契约。** | 清掉 `self._group` 之后，`ai_trade_group_states` 那一行**还是 `paused`**。于是用户照着那句 409 文案（「请重新启动一个新的 AI 自动交易分组」）去点启动，会被容量闸 `_require_no_paused_group` 拿 `AI_TRADE_PAUSED_GROUP_EXISTS` 当场挡回来 —— **一句给不出出路的错误提示，比不给提示更坏**，而本轮的整条立论就是「不要说做不到的话」。 | ① 那一格改为 `async`，同时 `await self._mark_state_ended(...)` + `_forget_paused_ref(...)`（与 `_note_session_stopped` 的正常收敛路径同一组动作）；② 用例断言契约变 `ended` **且 `_require_no_paused_group()` 真的不再抛**——只断言「清了内存」会绿着放过这个 bug。 |
| **I-13** | **§3.4 F-2 的快对账窗口「3 秒 / 30 秒」没有降回常态的路径。** | 判据是 `aiTradeReconcileUntil > Date.now()`，但那只是一个**时间戳** —— 它过期**不会**触发任何重渲染，挂着 `setInterval` 的那条 effect 也就永远不会重跑。照字面实现，任何一次 pause / resume / end 之后，`/ai-trade/status` 的轮询会**从此以后**都按 3 秒跑（而该端点每次都要现算 `_acceptance_bundle`）—— R-6 担心的那点压力从「动作后的 30 秒」变成「永久」。 | 快窗口开启时同时挂一个 `setTimeout`，到点把 `aiTradeReconcileUntil` 清成 `null` ⇒ effect 重跑 ⇒ 回到 15 秒；cleanup 里一并 `clearTimeout`。用例：「快对账窗口到点后自己降回常态」。 |

### C. 取舍（需要被写下来，否则下一轮会被「顺手统一」掉）

| # | 结论 |
|---|---|
| **I-8** | **`Badge` 没有 `warn` 这个 `tone`。** §3.4 F-1 / F-6 反复说「warn 色」，而 `components/ui/index.tsx` 的值域只有 `neutral \| long \| short \| accent`，且 §1.4 明确「本轮不动这个全仓共用的 kit」。取舍：**徽标**用 `accent`（与暂停横幅同色系，语义都是「需要注意但不是错误」）；**`NotArmedBanner` 是一个普通 div**，直接用设计系统在 `f48a714` 里刚加的 `--warn` / `--warn-soft` 令牌 + `⚠` 图标 —— 双通道传达（不靠颜色单通道），与 `globals.css` 里那条既有纪律同源。这样既拿到了真正的警告语义，又一行没动 kit。 |
| **I-9** | **[评审 R2-4] 的重复键按它的意见改成了嵌套。** `resume()` 的回执是 `{"ok", "state", "lifecycle": wire}` 而不是 `{**wire}`：展开会让 `state` 出现两次，两个真源写在同一个字面量里是下一次漂移的种子。manager 的 B-2 ③ 复检据 `receipt.get("ok")` 与**自己重新算的**读数判定，不依赖这个键的展开形状。 |
| **I-10** | **总闸的边界必须精确到「对外说什么」而不是「服务端怎么想」。** 用例 12 要求总闸关时 `/status` 回执逐字节相同，于是四处回落：`group_status()` 的派生态与 `declaredState` / `lifecycle` 两键、`_status_payload` 的 `lifecycle` 键、`SessionLink.on_stream` 的判据、B-2 的体检与复检整段不参与。但 `AiTradeSession.resume()` 回执里的 `lifecycle` **不受总闸门控** —— 它是复检的入参，不是给界面看的 wire；一并回滚会让复检没有输入。这条边界写进了 `session.resume()` 的注释。 |

### D. 已按 spec 实现、但值得钉住的三处

1. **`_verify_armed` 必须跑在任何 monitor tick 之前**（[评审 R0-5] 的安全前置）：本方法是纯内存判定（唯一的 `await` 在回滚分支上），而 `_monitor_loop` 的第一件事是 `await asyncio.sleep(poll_interval_s)` —— 这条时序**成立**，且已写进 docstring。用例 `resume_refuses_instead_of_flattening_a_stale_group` 断言「没有任何平仓调用发生」。
2. **`_member_readings` 的兜底值刻意选「不会造假红条」的那一组**（`warmed_bars=1` / `stale_after_s=0` / `link_state="unknown"`）：三处 `_FakeSession` 替身没有 `lifecycle_readings()`，兜底成 0 会让它们全部被判「未暖机」，那三个测试文件会整片红 —— 而它们红的是一个**不存在的缺陷**。
3. **`unknown` 链路态不算「不可达」**：`link_state ∈ {down}` 才判 `feed_down`。`unknown` 是「还没探到」（会话刚起、通道刚建），把它算成不可达会让每一次启动的头几秒都挂一条假红条。真正的风控判据仍在 `_check_staleness` 那边，本轮一行未动。

### E. 基线

**后端**
- `pytest -k "ai_trade or ai_auto or session_link or admission"`：**1798 passed / 0 failed**
  （本轮之前同一选择是 1765 passed + **2 failed**，那两条红分别是 I-6 与 I-1 —— 即「既有用例
  断言的正是被修掉的行为」和「新 import 撞上源码冻结用例」，两条都已按 A/B 节的修法处理）。
- 全量 `python -u -m pytest tests/`：**3787 passed / 7 failed / 1 skipped**。
  ⚠️ **那 7 条红与本轮无关**：全部来自 `tests/test_factor_library_expansion.py` —— 一个
  **未被 git 跟踪**（`??`）的在制文件，它断言 `len(FACTOR_LIBRARY) == 20` 而当前仓库是 13。
  `services/quant/app/factors/` 在本轮 `git status` 里是干净的，本轮一行未动因子库。
  这与 `stability-hardening-audit` 那一轮「全量 7 条红来自未跟踪在制文件」是同一种噪音。
- 新增 `tests/test_ai_trade_lifecycle_core.py`（34 条）、`tests/test_ai_trade_resume_guard.py`
  （16 条），并在 `test_ai_trade_pause_session.py` / `test_okx_session_link.py` /
  `test_ai_trade_pause_api.py` 各补了本轮的回归护栏。唯一一条被**改写**的既有断言见 I-6
  （连同理由写在用例注释里）。`ruff check app/` 全过。

**前端**
- `pnpm vitest run` 全量：**173 文件 / 2028 条全绿**（本轮新增 ~50 条）。
- `tsc --noEmit` 干净；`next build` 成功（含 lint 与类型校验，产物正常）。

**未做（须在合入前补）**
- §8.3 的手工验收 **A1~A11** 需要真实 / 模拟账户与真实行情断连，不在本节点的能力范围内。
- 其中 **A11**（`live_auto_flatten_on_stale=True` 下点「继续」不得发生任何平仓）是《评审结论》
  放行条件 3 点名要在**模拟账户上真跑一遍**的那一条。它的**离线**等价物已由两条用例覆盖 ——
  `resume_refuses_instead_of_flattening_a_stale_group`（热路径）与
  `test_cold_rollback_never_flattens_the_adopted_position`（冷路径，I-11 新发现的那扇门）——
  但真机验收仍**未完成**。

---

## 终审记录（代码 Review 节点 · 2026-08-18）

**口径**：对着《文件 / 模块变更计划》逐项验收覆盖面，再对着「改完之后那一行会发生什么」找回归；
所有问题**就地修复**，不留 TODO。

### 1. 覆盖面

§4.1 的 13 项、§4.2 的 14 项**全部落地**。唯一的偏差是 §4.1 里的
`tests/test_ai_trade_pause_manager.py`：B-1 的清组用例没有落在那里，而是随
`test_ai_trade_resume_guard.py` 一起新建（用例 6 / 20 都在，语义等价），**判定为合规**。
§七的配置联动两处（`config.py` + `.env.example`）齐备，`desktop/src/lib/config.js` 按
[评审 R2-2] 正确地**没有**去改。

### 2. 终审发现并修复的问题

| # | 位置 | 结论 | 修法 |
|---|---|---|---|
| **V-1** | `manager._verify_armed` | **B-2 ② 收集来的回执其实一次都没被读到。** 判定表按 `instId` 给回执建索引（`by_inst = {r.get("instId"): r ...}`），而 `session.resume()` 的回执形状是 `{"ok", "state", "reason"?, "reasonText"?, "lifecycle"?}` —— **里面根本没有 `instId`**。于是那张表恒空、`declined` 恒假，复检退化成「只看读数」。这正是 D-1 那个被丢弃的赋值**换了个地方原样复发**，而且守卫自己看起来还是绿的：既有用例能过，是因为它的替身在 `ok=False` 时**顺带**把 state 留在了 `paused`，读数那条路先判了失败。 | 回执改按**位置**对齐（② 那行推导式逐成员顺序生成，位置即成员）。同时补 `describe_not_armed` 为空时的文案回落 —— 否则「只有回执说了真话」那一格会抛出一句没有原因的「继续失败」。新增用例 `test_a_declining_receipt_alone_is_enough_to_refuse`（已验证：改回旧写法即红）。 |
| **V-2** | `lib/aiTrade.ts:formatCountdown` | `String(h > 0 ? m : m)` 两个分支相同 —— 一个恒等三元式，读者会以为这里有个自己没看懂的分档。 | 化简为 `String(m)`。 |
| **V-3** | `tests/test_ai_trade_pause_session.py` | `session._link = object()` 紧接着被下一行覆盖，且它带着的注释（「只取它的 `on_stream` 属性」）恰好解释的是**被覆盖掉的那一行**。 | 删掉死行，注释移到留下的那一行上。 |

### 3. 复核过、判定为「不改」的三处

- **`patchAiTradeGroupAcceptance` 在生产侧失去了调用点**（验收三键已并入 `patchAiTradeGroupLifecycle`
  的窄 patch）。它仍是 store 的公开 action 且有自己的一组用例（`aiTradeAcceptanceStream.test.ts`），
  删它要动一个本轮清单之外的测试文件。**留着**，登记在此以免下一轮把它当成新的孤岛。
- **`AiTradeToggle` 的 `aiTradeEnabled` 取 `store || statusQ.data`**，看起来与「单 owner」矛盾。
  实际是必要的：对账器住在页面级 hook 里，任何没有挂载它的宿主下 store 永远是 `false`，而这个值
  门控的是启动按钮。两个来源是**同一个端点**，不构成 D-5 那种「两套口径」。
- **`manager.py` / `session.py` / `lib/aiTrade.ts` 继续超 CLAUDE.md 的 800 行**。三个文件在本轮
  之前就已超限，方案两个节点都把落点定在它们身上；拆分是一次独立的重构，不该混进一轮行为修复。

### 4. 基线（终审复跑）

- 后端 `pytest -k "ai_trade or ai_auto or session_link or admission or ws_client"`：**1799 passed / 0 failed**
  （实现节点 1798 + V-1 的新用例 1 条）。`ruff check app/` 全过。
- 前端 `pnpm vitest run` 全量：**173 文件 / 2028 条全绿**；`tsc --noEmit` 干净。
- §8.3 的手工验收 **A1~A11 仍未做**（需要真实 / 模拟账户与真实行情断连），其中 **A11** 是
  《评审结论》放行条件 3 点名要在模拟账户上真跑一遍的那条。它的离线等价物由
  `resume_refuses_instead_of_flattening_a_stale_group`（热路径）与
  `test_cold_rollback_never_flattens_the_adopted_position`（冷路径）覆盖，**但真机验收是本次提交
  之外的欠账**，合入后须补。

### 5. 结论

**通过并合入。** 本轮把「它现在是不是真的在跑」从一个被赋值的字段改成了一个带着可反证证据下发的
真值，七条 P0 的修法与 v2 逐条对得上；V-1 是它自己身上最后一处「有写点没读点」，已修并配了能稳定
复现的用例。
