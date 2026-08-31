# AI 自动交易 · 情报装配状态真值化与热重装（ai-trade-intel-arm-state）

> 版本：**v2**（v1 → v2 由方案评审节点改写，见《评审记录》与《评审结论》）
> 作者：方案设计节点（第 2 轮 · 节点 #0）
> 评审：方案评审节点（第 2 轮 · 节点 #1）
> 承接：`52f466e`（市场情报层）→ `7e2d5be`（情报闭环）→ `4b9c352`（情报证据接地）
> → `d17eed6`（情报可行动化）
> 本轮需求（用户原话）：
>
> > 继续修改完善：已经设置好了信号源，但打开【市场情报】面板，仍然显示【尚未配置情报检索源】，
> > 貌似没有正确识别状态和设置，需要修复，使得设置后，去对应面板就能够生效。

---

## 评审记录（v1 → v2）

> 评审方式：**不采信正文的任何一条引用**，E-1…E-18 与 §十二 的 16 个行数全部在 `baf2e3d`
> 之后的工作树上重新实读（工作树带一处未提交改动：`api/ai_intel.py` 的两个 204 端点 `-> Response`
> 修复，故该文件行号已含 +11 偏移——v1 的 E-4/E-5/E-17 三条行号正是按这份工作树写的，逐条对得上）。
>
> **复测结论**：18 条取证 **17 条成立**，1 条更正（E-8，见 P2-1）；§十二 的 16 个行数
> **逐个 `wc -l` 复测无漂移**（service 766 / taxonomy 519 / search 538 / runtime 455 /
> scheduler 153 / api-ai_intel 618 / app_settings 268 / manager 2058 / schemas 2903 /
> config 1384 / IntelSourceSettings 519 / IntelEmptyStates 121 / IntelList 139 /
> intelTypes 551 / IntelModal 592 / IntelImpactTab 196）；补充取证三条全部成立
> （`set_app_setting` 第 433 行确实 `await session.commit()`；`.env.example` 在**仓库根**且
> `test_ai_intel_wiring.py:430` 是双向对齐用例；`/settings/intel*` 不在 `httpTimeout.ts` 的
> 任何一张表里 ⇒ `FAST_MS` 20 秒且非写路径）；`ai_trade_intel_*` 家族实测 **82** 项、
> `INTEL_SETTING_BRIDGE` 实测 **23** 条，与 §十一 / §5.2 的登记一致。
>
> 本轮的问题**不在取证**，而在**修法落点**：四条 P0 里有三条是「新通路绕过了它自己声称的约束」——
> 与本仓库上一轮终审记下的同一种形状。

### P0（阻断级 · 不改则本轮的原命题修不好，或后端起不来）

| # | 缺陷 | 取证 | 修法落点 |
|---|---|---|---|
| **P0-1** | **热重装会造出一条身份被抹掉的流水线。** `build_group_runtime` 的两个壳分支（`runtime.py:416-418` 与 `:429-431`）构造 `IntelDeps` 时**只传 `settings` 与 `sessionmaker`**，`group_id` / `bar` / `change_24h_pct` 三个字段一个都没传，于是取 dataclass 默认值 `""` / `"15m"` / `None`。而 §3.2.3 的 `_build_deps(prev, …)` 明写「`prev` 只贡献分组身份」——**壳恰恰是没有分组身份的那一个**。后果不是抽象的：`deps.group_id` 被写进 `ai_intel_items.group_id`（`service.py:358` 的 `item_to_payload(group_id=deps.group_id)`）与三种轮存根（`service.py:341,414,593` / `service_curate.py:176` / `service_verify.py:84`），`deps.bar` 进 `corpus.build_context`（`service.py:256` / `service_verify.py:186`），`deps.change_24h_pct` 决定「近 24 小时涨跌」那一行在不在。**而「开跑时没配源、跑起来之后才去设置里配」正是本轮用户报告的那一种处境**——修好之后采到的每一条情报都挂着空 `groupId`，Agent 拿到的是写死的 15m 上下文，且没有任何东西会报错。 | `runtime.py:416-418` / `:429-431`（两个壳分支）；`service.py:170-193`（`IntelDeps` 的默认值）；`service.py:256-257,341,358,414,593` | §3.2.3：`_build_deps` 升为**壳与实装共用的唯一 deps 构造点**，分组身份三字段由**显式形参**传入而不是从 `prev` 继承；`build_group_runtime` 的两个壳分支改调它。新增 **G-27** 冻结。 |
| **P0-2** | **`arming.py` 与 `service.py` 模块级成环，先导入 `arming` 的那条路径必然 `ImportError`。** §3.1.2 的 `arming.py` 写着 `from . import service as _svc  # 只用 degrade_wire；延迟导入见下`，而正文**从未给出那个延迟导入**；同时 §十二 要求 `service.py` `from .arming import ArmState, arm_wire, classify_config, resolve_arm`。任何先解析 `arming` 的入口（`api/ai_intel.py` 的 `from ..ai_auto.intel import arming`、`test_ai_intel_arming.py` 的直接 import）都会走到：arming 执行到第 2 行 import → service 开始执行 → `from .arming import ArmState` → arming 尚未定义 `ArmState` → **`ImportError: cannot import name 'ArmState' from partially initialized module`**。整个后端起不来，且打包冒烟即死在这里（与本工作树里那笔 204 崩溃同一种「装配期崩」）。 | `service.py:727` 是 `degrade_wire` 的家；`search.py` 与 `taxonomy.py` 实读**都是叶子模块**（无任何 `from .` 包内导入），故环**只由 `service` 这一条边构成** | §3.1.2 + §6.1 + §九：把 `degrade_wire` **纯搬运**到 `taxonomy.py`（它与 `DEGRADE_TEXT` / `degrade_message` 本就同源），`service.py` 原样重新导出（照抄它第 756-766 行 `settings_bridge` 那次「单向搬家、调用点一个字节不改」的先例）；`arming.py` 只依赖 `search` + `taxonomy` 两个叶子。新增 **G-28** 冻结导入顺序。 |
| **P0-3** | **`POST /settings/intel/rearm` 恰恰救不了它唯一声称要救的那种处境。** §3.3.3 写明这个端点存在的理由是「`_wire_intel` 是 fail-open 的，一次装配失败会让分组在整个生命周期里都没有情报层」。而 `_wire_intel` 的 `except`（`manager.py:1492-1493`）吞掉异常之后 **`intel_runtime.register` 从未被调到**，`_RUNTIMES` 里什么都没有；§3.2.3 的 `rearm_group` 第一行就是 `runtime = _RUNTIMES.get(group_id); if runtime is None: return {…"reason": "not_running"…}`。于是用户点【重新装配】拿到的是「AI 自动交易未在运行」——**一句在分组正跑着的时候确定错误的话**，而自救入口对它自己的立项理由束手无策。 | `manager.py:1469-1493`（fail-open 的 `except` 在 `register` 之前）；§3.2.3 第 514-517 行 | §3.3.1：`manager.rearm_intel` 在 `rearm_group` 回 `not_running` **而分组确实在跑**时，回落走一次完整的 `_wire_intel(group)`（它本就幂等且 fail-open），并把结果按同一张 wire 回执。新增 **G-26** 冻结。 |
| **P0-4** | **`intel_runtime.arm_for(inst_id)` 无法与 `_summary` 同源，实现出来必然是第二份判据。** §3.3.5 要求 `_require_runtime` 用 `arm_for(inst_id)`，并注明「与 `_summary` 同一个判定」。但 `_summary` 的 `resolve_arm` 吃的是 `resolve_intel_settings(session, settings)` 的**派生副本**——那是一次 **DB 读**；`arm_for` 住在 `runtime.py`、是同步函数、没有会话。在「没有任何 runtime」这条**最需要说实话的**支路上（P0-3 的同一处境），它拿不到 provider / key 的库内值，只能猜——于是本轮花整章消灭的那句假话，会从 `GET /intel/{instId}` 挪到「立即采集」的 409 里原样复活。 | `settings_bridge.py:65-96`（`resolve_intel_settings` 是 `async` 且查库）；`api/ai_intel.py:392,411,504,526` 四个调用点**全部**已有 `session` 与 `settings` 在作用域内 | §3.3.5：删掉 `arm_for`，把 `_require_runtime` 改成 `async def _require_runtime(session, inst_id, settings)`，与 `_summary` **调同一个 `resolve_arm`**；四个调用点各加一个 `await`。**判据只留一份**。 |

### P1（放行前必须改毕）

| # | 缺陷 | 取证 / 后果 | 修法落点 |
|---|---|---|---|
| **P1-1** | `_build_deps` 的签名与它自己的调用点对不上：§3.2.3 声明 `def _build_deps(prev, *, settings, provider_cfg, make_client)`（4 参），而三行之上的 `rearm_group` 调的是 `_build_deps(runtime.deps, settings=…, api_key=api_key, provider_cfg=…, make_client=…)`（5 参，多一个 `api_key`）。§十二 又登记成 `_build_deps(4)`。 | 直接 `TypeError` | §3.2.3 定稿签名（含 `api_key`、含分组身份，见 P0-1）；§十二 的形参登记同步改正 |
| **P1-2** | **`_static_summary` 上的三个新键无人写**：§3.1.4 承诺「三个新键恒在位（**含 `_static_summary`**）」、G-7 把它列为四条通路之一，而 §3.1.5 的写点只有 `summary_for` 与 `read_summary` 两处。更糟的是 §3.1.5 给 E-18 开的方子 `self._notify(inst_id, self.summary_for(inst_id) or summary)` **保留了裸 dict 回退**——`summary_for` 回 `None` 时推出去的仍是一份没有 arm 四键的字典，前端读到 `undefined` 会把「没装上」画成「一切正常」（这正是 §3.1.4 自己引用的那个坑）。 | `runtime.py:37-64` / `:129-132` | §3.1.5：`_static_summary` 内部展开 `arm_wire(arm_of_code(code))`（**签名不变**，故 `test_ai_intel_alert_shutdown.py:135` 的调用点一个字节不改），新增纯函数 `arming.arm_of_code(code) -> ArmState`；E-18 的修法去掉裸 dict 回退 |
| **P1-3** | **R-4 的「逐条已核对」只核对了 `providerConfigured`，漏掉 `degrade.code` 的爆炸半径。** `arm_wire` 会用**新码**覆写 `degrade`，而未装配的默认 `Settings()`（`ai_trade_intel_enabled` 实测默认 **True**、`provider="none"`）解出来的是 `provider_unset`，不是 `no_provider`。三条既有断言因此必红，而 v1 一条都没登记：`test_ai_intel_scheduler.py:86`、`test_ai_intel_api.py:245`（两条都断言 `degrade["code"] == "no_provider"`）、`test_ai_intel_api.py:198`（断言 `error.code == "intel_no_provider"`，`_require_runtime` 改造后变 `intel_provider_unset`）。 | 三处逐行实读；`config.py:976` `ai_trade_intel_enabled: bool = True` | §7.2 逐条登记这三处为**刻意更新**并给出新期望值；R-4 改写 |
| **P1-4** | **G-25 的命题不可满足。** 它要求 `classify_config(derived) == ""` 当且仅当 `resolve_search_provider(...) is not None`。但 `classify_config` 的**第一条**判据是总闸（`disabled`），而 `resolve_search_provider` 根本不看总闸——只要用户「配好了源却关着总闸」，左边非空、右边非 `None`，等价性恒不成立。一条写下来就注定判红的闸，等于没有闸。 | §3.1.2 `classify_config` 第一行 vs `search.py:438-452` | §7.1：G-25 的等价命题改成 `classify_provider(provider, key)` ⇄ `resolve_search_provider`（那才是同一张真值表），总闸那一段另立 G-25b |
| **P1-5** | `ArmState.__post_init__` 用 `assert` 守不变式。它跑在**每一次 summary 读**的路径上：`python -O` 下静默失效（不变式形同不存在），非 `-O` 下抛一个**没有任何定位线索**的裸 `AssertionError` 并变成 REST 500。两头都违反 CLAUDE.md 五（「错误信息必须包含定位线索」）。 | §3.1.2 第 227-228 行 | 改抛 `ValueError` 并在消息里带全部四个字段值；G-3 的期望异常同步改 |
| **P1-6** | **公私成员名对不上，实现出来就是 `AttributeError`。** §3.2.2 定义 `self._arm_pending`，§3.2.3 的 `rearm_group` 却读写 `runtime.arm_pending`（无下划线）与 `runtime.arm_fingerprint`（§5.2 登记为公开）与 `runtime.arm.armed`（§3.2.2 有 `arm` property ✓）。三个名字两套约定。 | §3.2.2 vs §3.2.3 vs §5.2 | 统一：`arm` / `arm_pending` / `arm_fingerprint` 三个**只读 property** + 私有字段；`arm_pending` 退化成 `_arm.reason` 的投影，置位收进 `rearm` / `mark_arm_pending` 两个方法，外部不直接赋值 |
| **P1-7** | **arm 四键有了第二个手抄写点。** §3.0 第 3 条立的规矩是「`providerConfigured` / `intelEnabled` / `armed` / `armReason` 的赋值只允许发生在 `arm_wire()` 一处」，而 §3.3.4 的 `_intel_wire` `arm` 块把同样四个事实用**另一套键名**（`reason` 而不是 `armReason`）再拼一遍。这就是本轮开篇点名的 `groupStateFromStatus` 形状：两张手抄表，漂移了不报错。 | §3.0 第 3 条 vs §3.3.4 | §3.3.4：`arm` 块由 `arming.arm_settings_wire(state, …)` **在 arming.py 内**装配，与 `arm_wire` 共用同一个 `ArmState`→键名的私有映射；新增 **G-29** 断言两块的四个布尔恒同值 |
| **P1-8** | **`armReason` 与 `degrade` 在 pending 时互相矛盾。** §3.1.5 的 `_summary` 里，`arm` 经 `read_summary` 走 `arm_wire(arm)`（**没有 `pending=`**）；而同一个返回里 `degrade` 从 `cached` 覆盖回来，缓存那一份是 `summary_for` 用 `arm_wire(self._arm, pending=self._arm_pending)` 打的戳。于是一次 pending 的重装期间：`armReason == ""`（说一切正常），`degrade.code == "arm_pending"`（说正在生效中）。**同一份 wire 的两个键说两句相反的话**——正是本轮立项要消灭的东西。 | §3.1.5 的两段代码 | §3.1.5：`resolve_arm` 负责把 pending 一并解出来（它拿得到 `runtime`），`read_summary` 收到的 `ArmState` 已经是终态；`arm_wire` 的 `pending=` 形参删掉，`arm_pending` 降为 `ARM_REASONS` 里的一个普通 reason |
| **P1-9** | **指纹在「应用之前」写入，并发两次保存交错后会进入一个吸收态。** `rearm_group` 里 `runtime.arm_fingerprint = fingerprint` 排在 `wait_for(runtime.rearm(...))` **之前**，而 `rearm` 内部还要按 `seq` 决定应不应用。两个 PUT（A=seq1、B=seq2）在 `resolve_intel_settings` 那次 `await` 上交错时，可能出现「B 应用了配置、A 后写了指纹」⇒ 运行时跑着 B、指纹说是 A ⇒ 此后任何一次把设置改回 A 的保存都会被 `changed=False` 短路，**A 永远装不上，且不报错**。 | §3.2.3 第 531-534 行；两个 handler 之间确实有 `await`（DB 读） | §3.2.3：指纹**随 `seq` 一起进 `rearm`，在同一把锁内、与 `self.deps` 同时赋值**；`rearm_group` 不再直接写它。新增 **G-30** 用交错序冻结 |
| **P1-10** | **§3.2.4 的竞态清单漏了一行：`_sync_scheduler` 的停机分支会打断 `_after_round`。** `_run` 的 `async with self._lock` 在 `await self._after_round(...)` **之前就退出了**（`runtime.py:193-207`），所以 `rearm` 拿到锁时，上一轮的 `_after_round` 可能正在跑；紧接着 `_sync_scheduler` 的 `scheduler.cancel(group_id)` 取消的正是承载它的那个 `_loop` 任务 ⇒ 这一轮的快照刷新与 **`_alert` 告警交接**被静默丢弃。告警交接是「一条够格的高危情报当场多问一次 AI」那条链的第二跳（`d17eed6`），把它丢在一次开关拨动上，症状是「今天为什么一次都没告警」这个问题**恰好在这一次没有答案**。 | `runtime.py:193-212`；`scheduler.cancel` 只 cancel 不等 | §3.2.4 补一行结论 + §3.2.2 的 `_sync_scheduler` 停机分支改为「先等 `_in_flight` 清空（有界，复用同一条死线）再 cancel」，超时则如实降级为直接 cancel 并记一行 warning |

### P2（已一并改毕）

| # | 缺陷 | 处置 |
|---|---|---|
| P2-1 | **E-8 更正**：`build_group_runtime` 实读是 **10** 个 keyword 形参（group_id / members / settings / sessionmaker / provider_cfg / make_client / bar / change_24h_pct / notify / notify_alert），不是 11。 | E-8 正文改正 |
| P2-2 | §十二 说「加进 `__all__`」——`service.py` 实读**没有 `__all__`**（`grep -n "__all__"` 零命中），它的重新导出靠的是 `# noqa: E402,F401`。 | §十二 改正为照抄 `settings_bridge` 那次的 `noqa` 写法 |
| P2-3 | `_is_group_member(inst_id)` 只在 §3.1.5 与 §6.1 出现名字，正文从未给出实现。 | §3.1.5 给出实现：读 `get_ai_trade_manager().group_status()` 的 `members`（`app_settings.py:158` 已经是这个用法） |
| P2-4 | §4.1 的错误码表漏了第四个 `_require_runtime` 调用点 `POST /api/intel/{instId}/verify`（`api/ai_intel.py:504`）。 | §4.1 补一行 |
| P2-5 | 闸编号乱序（G-25 排在 G-24 之前）；R-11 说「在 G-23 里断言不含 `apiKey` 字样」，而 G-23 正文没有这一句。 | §7.1 重排并把该断言写进 G-23 |
| P2-6 | `test_ai_intel_service.py` 里那次 `read_summary(..., provider_configured=True)` 实读在 **`:613`**，不是 `:612`。 | §7.2 改正 |
| P2-7 | §7.2 把八个前端 fixture 一律写成「补 3 键」，但 `IntelImpactTab.test.tsx:122` 传的是 `summary({ providerConfigured: false })` 并期望空态 `not-armed`；判据改读 `armed` 之后它会落到 `no-citations`，与用例里第四个 case 撞成同一段文案，让「四种空态各自文案不同」那条**既有**断言判红。 | §7.2 单独登记该行改为 `summary({ armed: false })` |
| P2-8 | §十一 的 `Field(..., le=10.0)` 与校验器 `>= 10.0 即抛` 在 `10.0` 这一点上重叠（Field 放行、校验器拒绝）。 | 保留双层（与既有 `drain_timeout_s` 同形）但把 Field 收成 `lt=10.0`，并在注释里写明校验器守的是「未来有人放宽 Field」这条路 |
| P2-9 | §6.3 的「桌面壳零改动」与前两轮先例不一致：`desktop/scripts/config-passthrough.test.mjs` 在 closed-loop 与 actionability 两轮**都补过哨兵键**。前缀白名单确实天然覆盖 `AI_TRADE_INTEL_REARM_DEADLINE_S`，但「不补」需要写出来。 | §6.3 补一句显式裁决：补一行哨兵键（1 行、不改任何产品代码），理由是这张表的价值就在于「前缀规则被人改窄时要立刻红」 |
| P2-10 | §3.4.3 的三张状态卡里，`not_running` 那一张没有按钮，但正文没写「【重新装配】在 `not_running` 下同样不渲染」。 | §3.4.3 补一句（与 §3.4.2 的 `REARM_CODES` 对齐） |
| P2-11 | `arm_wire` 产出的 `degrade.retryInS` 恒为 `None` 未登记：前端 `degradeText` 因此不显示倒计时——这是**对的**（装配状态没有「多久后自动重试」这回事），但要写出来，否则实现者会去编一个。 | §3.1.2 `arm_wire` docstring 补一句 |

---

## 〇、本轮定位：这不是一个显示 bug

用户报告的是一句错误的文案，但那句文案本身是对的——它忠实地渲染了后端下发的
`providerConfigured=false`。真正的缺陷有两层，都在后端：

1. **`providerConfigured` 这个键从来就不是「检索源配没配」。** 它在两条通路上都被定义成
   「这个进程里此刻有没有一个 `enabled=True` 的 `IntelRuntime`」——一个**装配事实**冒充了一个
   **配置事实**。于是「用户没配源」「用户配了源但总闸关着」「配全了但这一次装配抛异常被
   fail-open 吞掉」「自动交易根本没在跑」这四种完全不同的处境，被压成同一个 `false`、同一句
   「尚未配置情报检索源」、同一个「去设置检索源」按钮——而后三种情况下，用户点进设置一看，
   源和 key 都在。**系统在对用户说一句关于用户自己配置的假话。**
2. **情报层的设置在分组启动那一刻被冻结，此后永不重读。** `build_group_runtime` 只在
   `manager.start_group` 与冷 `resume_group` 各跑一次；`PUT /settings/intel` 只写
   `app_settings` 表，**没有任何通路**通知已注册的运行时。`IntelRuntime.deps.settings` 是一份
   装配期的派生快照，`scheduler._tick` 每次读的都是它。所以被冻结的不只是检索源——采集间隔、
   日配额、整理频率、核查开关、抓取开关、告警阈值、战绩与矛盾两把总闸，**全部**在一个正在跑的
   分组上改不动，而且每一次保存都返回 200、面板上开关照动、回读也是新值。

这正是本仓库反复清算的那一类缺陷的最纯粹形态：**「写点有、读点无」**，以及它的孪生兄弟
**「假面板」**。`settings_bridge.py` 的模块 docstring 里那句「⚠️ 这张表是本仓库最容易做成『假
开关』的地方（历轮三次前科）」写的是**漏登记 wire 键**这一种做法；本轮发现的是第四种做法——
键登记齐了、库也写进去了、`Settings` 副本也叠对了，**只是没有人再去读一次**。

所以本轮不是「把那句文案改一改」，而是：让「配没配」「开没开」「装没装上」成为三个各自独立、
各自可回答、各自有出路的事实；并让设置的每一次保存**当场生效**，不需要停掉一个正在管理真实
资金的自动交易分组。

---

## 一、概述（Overview）

本轮给情报层补上它缺失的**装配状态机**与**热重装通路**，分四条轴，全部是加法，零 DB schema
变更，零决策 prompt 字节变更。

**轴 A · 装配状态真值化。** 新增纯核心模块 `ai_auto/intel/arming.py`：一个 frozen dataclass
`ArmState`（`provider_configured` / `intel_enabled` / `armed` / `reason`）与三个确定性纯函数
（`classify_config` / `resolve_arm` / `arm_of_deps`）。「检索源到底算不算配好了」的判据从
`search.resolve_search_provider` 里抽成 `classify_provider(slug, api_key) -> str`，使配置侧判定
与真正造适配器的那一段**共用同一张真值表**（不允许出现第二份手抄）。`AiTradeIntelSummary` 的
wire 上加三个键：`intelEnabled` / `armed` / `armReason`，`providerConfigured` 的语义被修正为
「库里配没配」。四个键**恒在位、恒由同一个函数写**，REST 与 WS 两条通路取同一个真源。

**轴 B · 就地热重装。** `IntelRuntime` 新增 `rearm(deps, arm, seq)`：**换配置、不换实例**。同一个
`asyncio.Lock`、同一份快照缓存、同一个 `notify` 出口、同一个 `paused` 标志、注册表一个字节不
改——因此既不会打断正在飞的那一轮情报（`async with self._lock` 等它跑完），也不会出现「新旧
两代运行时并发跑同一个标的」这种换代竞态（本仓库在 `_rearm_feed` 上栽过一次）。锁内**不得有
任何 await**，两次赋值原子完成。等锁受硬死线约束（默认 2.5 秒），超时即把这次重装排进既有
tracked 后台任务集，请求侧立刻返回并如实回执 `pending=true`。

**轴 C · 端点接线与自救入口。** `PUT /api/settings/intel` 写完库之后当场重装并把结果放进回执；
新增 `POST /api/settings/intel/rearm` 作为**手动重装**入口——用户不必停掉一次真实资金会话就能
自救。`_require_runtime` 的 409 不再无差别地回 `no_provider`，而是回当前真正的 `armReason`。

**轴 D · 前端诚实分叉。** `IntelList.emptyKind` 的第一条判据从一个布尔拆成三级
（未配源 → 总闸关 → 未装配），各自有自己的标题、解释与**可执行的出路**；降级 banner 的按钮按
降级码分叉成「去设置 / 重新装配 / 无按钮」；设置卡新增一张**生效状态卡**，一句话回答「我刚
保存的这份设置，此刻到底有没有在跑」，并在装配失败时给出重试按钮与后端记录的失败原因。

**这一轮的验收标准只有一句话**：在 AI 自动交易**正在运行**的状态下，进设置中心选好检索源、
存好 API Key，**不做任何其它操作**，回到标的卡片点开【市场情报】，弹窗必须在 5 秒内显示为
「已配置」并开始正常排期采集。

---

## 二、代码取证（本文所有判断的锚点）

以下 18 条全部在 `d17eed6` 之后的工作树上实读，行号为实读值。

| # | 位置 | 事实 |
|---|---|---|
| E-1 | `apps/web/components/Intel/IntelList.tsx:129` | `if (summary && !summary.providerConfigured) return "no-provider";` —— 空态第一条判据，压过其余全部四种。 |
| E-2 | `apps/web/components/Intel/IntelEmptyStates.tsx:71-80` | `no-provider` 的标题正是用户看到的那句「尚未配置情报检索源」，动作是「去设置检索源」。 |
| E-3 | `apps/web/components/Intel/IntelEmptyStates.tsx:26` | banner 的 `isNoProvider = code === "no_provider" \|\| code === "disabled"` —— 两个语义完全不同的码共用同一个按钮。 |
| E-4 | `services/quant/app/api/ai_intel.py:110` | `provider_configured=intel_runtime.runtime_for(inst_id) is not None` —— REST 通路把「进程里有没有注册过运行时」当成「检索源配没配」。 |
| E-5 | `services/quant/app/api/ai_intel.py:89` | `_RUNTIME_ONLY_KEYS = ("inFlight", "degrade", "providerConfigured")` —— 缓存值覆盖库值，故壳运行时的 `False` 恒胜出。 |
| E-6 | `services/quant/app/ai_auto/intel/runtime.py:95-99` | 构造期 seed：`_static_summary(code="no_provider")`，仅当 `self.enabled` 才改写成 `providerConfigured=True` + `degrade=None`。 |
| E-7 | `services/quant/app/ai_auto/intel/runtime.py:428-431` | `provider is None` ⇒ 返回 `enabled=False` 的**壳**，且**不覆写** `_summary`（沿用构造期的 `no_provider` seed）；而上面 `ai_trade_intel_enabled=False` 的分支是显式覆写成 `disabled` 的。两条分支形状不一致。 |
| E-8 | `services/quant/app/ai_auto/intel/runtime.py:381` | `build_group_runtime` 是**唯一**的装配入口，**10** 个 keyword 形参（`group_id` / `members` / `settings` / `sessionmaker` / `provider_cfg` / `make_client` / `bar` / `change_24h_pct` / `notify` / `notify_alert`）。〔评审 P2-1 更正：v1 写的是 11〕 |
| E-8b | `services/quant/app/ai_auto/intel/runtime.py:416-418` / `:429-431` | ★ **两个壳分支构造 `IntelDeps` 时只传 `settings` 与 `sessionmaker`**，`group_id` / `bar` / `change_24h_pct` 三个字段一个都没传 ⇒ 取 dataclass 默认值 `""` / `"15m"` / `None`。壳**没有分组身份**——这是评审 P0-1 的根。 |
| E-8c | `services/quant/app/ai_auto/intel/service.py:170-193` | `IntelDeps` 的 `group_id: str = ""` / `bar: str = "15m"` / `change_24h_pct = None` 三个默认值；消费点在 `service.py:256,257,341,358,414,593` / `service_curate.py:176` / `service_verify.py:84,186`——`group_id` 直接落进 `ai_intel_items` 与三种轮存根的 `groupId` 列。 |
| E-8d | `services/quant/app/ai_auto/intel/search.py` / `taxonomy.py` | 两者**都是包内叶子模块**（除 `__future__` 外没有任何 `from .` 包内导入）；`degrade_wire` 的家是 `service.py:727`。故 `arming.py` 与 `service.py` 之间的环**只由 `degrade_wire` 这一条边构成**——评审 P0-2 的根与它的修法都在这一行。 |
| E-9 | `services/quant/app/ai_auto/manager.py:395` / `:948` | `_wire_intel` 只有两个调用点：`start_group` 与**冷** `resume_group`。热续跑（`:932`）只调 `intel_scheduler.resume`。 |
| E-10 | `services/quant/app/ai_auto/manager.py:1469-1494` | `_wire_intel` 全程 fail-open：`except Exception` 只 `logger.warning`，失败后 `_BY_INST` 里什么都没有，**且这个原因不进任何 wire**。 |
| E-11 | `services/quant/app/api/app_settings.py:223-246` | `PUT /settings/intel` 写完 `app_settings` 直接 `return await _intel_wire(...)`，**不通知任何运行时**。全仓 `grep build_group_runtime` 只有 `manager.py:1479` 一个生产调用点。 |
| E-12 | `services/quant/app/ai_auto/intel/scheduler.py:53-56` | `start()` 对 `enabled=False` 的 runtime **一个任务都不建**，只打一行 `logger.info`。 |
| E-13 | `services/quant/app/ai_auto/intel/scheduler.py:140` | `_tick` 每轮读 `runtime.deps.settings.ai_trade_intel_curate_every_n_collects` —— 冻结的是**整份** settings，不只是检索源。 |
| E-14 | `services/quant/app/ai_auto/intel/service.py:626-627` | `build_snapshot` 传 `provider_configured=deps.search is not None`。这是运行时侧唯一一处，语义与 E-4 不同源。 |
| E-15 | `services/quant/app/ai_auto/intel/search.py:438-452` | `resolve_search_provider` 的真值表：`slug in ("", "none")` 或 `api_key` 空 ⇒ `None`；未实现的 slug ⇒ 一行 `logger.info` 后 `None`。**三种「没配好」在返回值上不可区分。** |
| E-16 | `services/quant/app/ai_auto/intel/taxonomy.py:197` | `DEGRADE_TEXT["no_provider"]` 是那句文案的**后端唯一真源**（前端不二次编，`degradeText` 优先用下发的 `message`）。 |
| E-17 | `services/quant/app/api/ai_intel.py:376-383` | `_require_runtime` 对「runtime 不在」与「runtime 是壳」一律回 `409 intel_no_provider` —— 于是「立即采集」的失败提示也在撒同一个谎。 |
| E-18 | `services/quant/app/ai_auto/intel/runtime.py:129-131` | `refresh` 的 `not self.enabled` 分支 `self._notify(inst_id, summary)` 直接推**原始缓存字典**，绕过 `summary_for`（因此不带 `inFlight` 归一）。轴 A 之后这条支路会连 arm 四键一起漏掉，故必须一并修。 |

补充取证（不单独编号，但实施时会被依赖）：

- **`resolve_intel_settings` 会把库里存的 API Key 叠进派生副本**（`settings_bridge.py:92-94`：
  `updates["ai_trade_intel_search_api_key"] = stored_key`），故 `classify_config(derived)` 读
  `derived.ai_trade_intel_search_api_key` **拿得到用户在面板上存的那把 key**。这条是整轮修法能
  不能成立的地基（打包形态下 env 里没有 key，只有库里有），评审已逐分支复核四种组合
  （库内缺席 / 空串 / 纯空白 / 非空）下 `classify_config` 与 `resolve_api_key` 的判定一致——
  这也是 G-25 存在的理由。**但 `resolve_arm` / `arm_of_deps` 必须收到派生副本**：传进程级
  `Settings` 会让打包形态恒判 `key_missing`，本轮的原命题一个字都没修好。
- `settings_bridge.py:22-60` 的 `INTEL_SETTING_BRIDGE` 已含 `enabled` / `provider` 两键，
  `INTEL_API_KEY_APP_KEY` 也在位——**写入通路是完好的**，`repo.set_app_setting` 自带 `commit`。
  本轮**不需要**动这张表（这一次不是漏登记）。
- `schemas.IntelSettingsIn.provider` 是 `Literal["none","tavily","brave","serper"]`，
  `IntelSettingsOut` 无 `arm` 字段；`response_model` 会**静默丢弃**未声明的键（仓库成文教训），
  故轴 C 的新块必须同时改 `IntelSettingsOut`。
- `apps/web/lib/httpTimeout.ts`：`/settings/intel*` 不在任何长跑表里 ⇒ 客户端超时档 `FAST_MS =
  20_000`，且不在写路径表里 ⇒ 超时文案是「请检查后端是否响应」。轴 B 的服务端死线必须**远小于**
  20 秒。
- `test_ai_intel_wiring.py:430` 是 `.env.example` ⇄ `Settings.ai_trade_intel_*` 的**双向**逐键
  对齐用例，新增配置项漏登记会被它判红（这正是它存在的理由）。

---

## 三、技术设计

### 3.0 四条不可越过的方向

1. **任何一次设置保存都不得打断正在飞的那一轮情报，也不得让两轮并发。** 情报轮会花掉真金白银
   的检索额度与 LLM token，并且 `ai_intel_watch.next_due_ts` / `daily_used` 的推进依赖轮内的
   写序列。这条约束直接否决了「cancel 旧循环 + 换一个新 `IntelRuntime` 实例」这条最直观的修
   法——那会撕断在飞轮，并在新旧两个实例各持一把 `asyncio.Lock` 的重叠期里制造并发。
2. **一次失败的重装绝不允许把一条正在正常工作的情报流水线弄停。** 因此依赖对象必须**先造好**
   （在锁外、可失败），造成功之后才允许换；造失败即保留旧的，并把失败原因如实写进 wire。
3. **arm 四键只有一个写点。** `providerConfigured` / `intelEnabled` / `armed` / `armReason` 的
   赋值只允许发生在 `arming.arm_wire()` 一处，由 `IntelRuntime.summary_for` 与
   `service.read_summary` 各调一次。这条纪律是上一轮终审 F-1 的成文结论（「台账纪律从『一行只
   有两个写点』改写成『同一列只有一个人写』」）的直接套用。
4. **零 DB schema 变更、零决策 prompt 字节变更。** 装配状态是**此刻**的进程事实与**库里**的配置
   事实的函数，两者都已经有落点，不需要第三张表。`build_decision_prompt` 与
   `prompt_sections.py` 本轮**一个字节不改**。

### 3.1 轴 A · 装配状态真值化

#### 3.1.1 判据的单一真源：`search.classify_provider`

`resolve_search_provider` 现在把三种「没配好」压成同一个 `None`（E-15）。抽出纯函数：

```python
# services/quant/app/ai_auto/intel/search.py（新增，紧邻 resolve_search_provider）

def classify_provider(slug: str, api_key: str) -> str:
    """检索源配置的分类判定（**纯函数，无 IO**）。

    Returns:
        ``""`` = 可以装配；否则是 `taxonomy.ARM_REASONS` 里的一个码：
        ``"provider_unset"`` / ``"key_missing"`` / ``"provider_unknown"``。

    本函数是「检索源到底算不算配好了」的**唯一**判据。`resolve_search_provider` 与
    `arming.classify_config` 都调它——两处各手抄一份真值表，就会出现「面板说配好了、
    运行时说没配」这种两边都不报错的分叉，而那正是本轮要修的缺陷本身。
    """
    key = (slug or "none").strip().lower()
    if key in ("", "none"):
        return "provider_unset"
    if key not in PROVIDER_SLUGS[1:]:      # ("tavily", "brave", "serper")
        return "provider_unknown"
    if not (api_key or "").strip():
        return "key_missing"
    return ""
```

`resolve_search_provider` 改写为**先分类、再造适配器**，且**保留既有的那一行 `logger.info`**
（`test_ai_intel_search.py` 断言它；memory 里的 I-10 就是这条）：

```python
def resolve_search_provider(*, slug, api_key, base_url="", timeout_s=12.0,
                            max_retries=2, proxy=None):
    key = (slug or "none").strip().lower()
    verdict = classify_provider(key, api_key)
    if verdict == "provider_unknown":
        logger.info("ai-trade intel: 未实现的检索源 %s，按未配置处理（已实现：%s）",
                    key, "/".join(PROVIDER_SLUGS[1:]))
        return None
    if verdict:
        return None
    # …以下三条 if 逐字节不变（tavily / brave / serper）…
```

> ⚠️ **行为等价性是硬要求**：改写前后，对任意 `(slug, api_key)` 的返回值必须逐一相同，且
> `logger.info` 只在「拼错的 slug」这一种情况下打出。G-2 用参数化用例把这张真值表钉死。
> 注意顺序上的一处细节：原实现是**先判空 key、再判 slug 是否实现**，因此「拼错的 slug + 空 key」
> 原本走的是「空 key」那条静默分支、**不打日志**；新实现把 `provider_unknown` 排在 `key_missing`
> 之前，会让这种组合多打一行 `logger.info`。这是一个**刻意的**行为变化（用户拼错了源名，那
> 比「key 没填」更值得说出来），必须在 G-2 里显式登记，而不是让它悄悄发生。

#### 3.1.2 纯核心模块 `intel/arming.py`（新增）

```python
"""情报流水线的装配状态（arm state）——本轮唯一的新纯核心模块。

三个事实必须分开，它们各自有不同的出路：

| 事实 | 谁能改 | 出路 |
|---|---|---|
| `provider_configured` 检索源配没配 | 用户（设置中心） | 去设置里选源 + 存 key |
| `intel_enabled`      总闸开没开     | 用户（设置中心） | 打开「启用市场情报采集」 |
| `armed`              此刻真的在跑吗 | 系统             | 重新装配 / 开启自动交易 |

把三者压成一个布尔，就会让「你没配」这句话出现在一个**已经配好了**的用户面前——那不只是
文案不准，那是系统在对用户说一句关于用户自己配置的假话。
"""
from __future__ import annotations

from dataclasses import dataclass

from . import search as search_mod
from . import taxonomy as tax

# ⚠️ **本模块只许依赖 `search` 与 `taxonomy` 这两个叶子模块**（评审 P0-2 / E-8d）。
# v1 的草稿写过 `from . import service as _svc  # 只用 degrade_wire`——那会与 `service.py` 的
# `from .arming import ArmState, …` 成环：任何**先**解析 `arming` 的入口（`api/ai_intel.py` 的
# import、`tests/test_ai_intel_arming.py` 的直接 import）都会在 arming 的 import 段转去执行
# service，而 service 第一件事就是回头取 `arming.ArmState`——那时它还不存在，于是
# `ImportError: cannot import name 'ArmState' from partially initialized module`，**整个后端
# 起不来**，且打包冒烟就死在装配期（与本工作树里那笔 204 崩溃同一种形状）。
# 修法**不是**在这里改成函数内延迟导入（那只是把环藏起来，并让「谁先谁后」变成运行期才知道的
# 事），而是把 `degrade_wire` 搬回它本来的家 `taxonomy.py`（§6.1 / §九 第 0 步）。


@dataclass(frozen=True)
class ArmState:
    """装配状态的四个事实 + 一个细节（frozen；本模块外**只读**）。"""

    provider_configured: bool
    intel_enabled: bool
    armed: bool
    reason: str = ""        # tax.ARM_REASONS 之一；armed=True 时恒为 ""
    detail: str = ""        # 仅 reason == "arm_failed" 时非空（异常文本，已截断到 200 字符）

    def __post_init__(self) -> None:
        """不变式：`armed` ⇒ 三者全真。

        一个真的在跑的流水线，不可能没配源、也不可能总闸关着。这条不变式让前端可以放心地
        「先看 `armed`」，不必再写一遍与后端不同源的组合逻辑。

        Raises:
            ValueError: 四个字段自相矛盾时（消息里带全部四个值）。

        ⚠️ **绝不用 `assert`**（评审 P1-5）：本方法跑在**每一次 summary 读**的路径上。
        `python -O` 会把 assert 整条剥掉 ⇒ 不变式形同不存在；不剥掉时抛出的又是一个**没有任何
        定位线索**的裸 `AssertionError`，一路变成 REST 500。两头都违反 CLAUDE.md 五。
        """
        if self.armed and not (self.provider_configured and self.intel_enabled
                               and not self.reason):
            raise ValueError(
                "ArmState 自相矛盾：armed=True 却 "
                f"provider_configured={self.provider_configured} "
                f"intel_enabled={self.intel_enabled} reason={self.reason!r}")


def arm_of_code(code: str) -> ArmState:
    """一个降级码 → 它对应的未装配 `ArmState`（纯函数）。

    存在的理由（评审 P1-2）：`runtime._static_summary` 也必须带齐 arm 四键，而它是一个只知道
    「降级码」的模块级函数。有了本函数，静态 summary 仍然经 `arm_wire` 这**同一个**写点出键，
    §3.0 第 3 条「同一列只有一个人写」不被破坏，而 `_static_summary(inst_id, *, code)` 的签名
    也一个字节不改（`tests/test_ai_intel_alert_shutdown.py:135` 因此继续绿）。

    Args:
        code: `tax.ARM_REASONS` 之一；**不认识的码**（例如轮内降级码 ``"no_provider"``）一律
            回落成「三个布尔全假 + 原样带上该码」——诚实回落，绝不猜。
    """
    if code not in tax.ARM_REASONS:
        return ArmState(False, False, False, code)
    return ArmState(
        provider_configured=code not in ("provider_unset", "key_missing", "provider_unknown"),
        intel_enabled=code != "disabled",
        armed=False, reason=code)


def classify_config(settings) -> str:
    """只看**库里存了什么**的分类判定（纯函数）。

    Args:
        settings: **必须是 `resolve_intel_settings` 的派生副本**，不是进程级 `Settings`。
            打包形态下 API Key 只在 `app_settings` 里（env 是空的），而派生副本会把它叠上来
            （`settings_bridge.py:92-94`）；传进程级 `Settings` 会让本函数恒判 `key_missing`，
            于是本轮的原命题一个字都没修好——而且不报错。

    Returns:
        ``""`` = 配置齐备；否则 ``"disabled"`` / ``"provider_unset"`` /
        ``"key_missing"`` / ``"provider_unknown"``。

    顺序有意义：总闸排最前。一个把总闸关掉的用户不需要被追问「你还没选检索源」。
    """
    if not bool(getattr(settings, "ai_trade_intel_enabled", False)):
        return "disabled"
    return search_mod.classify_provider(
        str(getattr(settings, "ai_trade_intel_search_provider", "none")),
        str(getattr(settings, "ai_trade_intel_search_api_key", "") or ""))


def arm_of_deps(deps) -> ArmState:
    """运行时侧的 arm：`deps.search is not None` 是**最硬**的证据。

    它就是那个真的会去发检索请求的对象——它在，就说明装配的那一刻配置是齐的。故
    `armed ⇒ provider_configured` 由这里直接兑现，而不是再去 settings 上问一次
    （离线用例常常拿一个裸 `object()` 当 search runner，settings 却是默认值）。
    """
    if getattr(deps, "search", None) is not None:
        return ArmState(True, True, True)
    return arm_of_code(classify_config(deps.settings) or "arm_failed")


def resolve_arm(settings, *, runtime, member: bool) -> ArmState:
    """REST 侧的 arm（`api/ai_intel._summary`、`_require_runtime` 与
    `api/app_settings._intel_wire` **共用同一份判据**）。

    Args:
        settings: **派生副本**（`resolve_intel_settings` 的产物），不是进程级 Settings。
        runtime: 该标的的 `IntelRuntime`（`runtime_for(inst_id)`），可为 None。
        member: 该标的此刻是不是正在跑的自动交易分组的成员。

    判定顺序（guard clause 阶梯，每一条都对应一句不同的话）：
        ① runtime 在、enabled、且**没有待应用的重装** ⇒ armed
        ② runtime 在、enabled、但 `runtime.arm_pending` ⇒ `arm_pending`
           （评审 P1-8：pending 在**这里**就解进 `ArmState.reason`，绝不留给 `arm_wire` 的
           一个额外形参——那会让 `read_summary` 那条不传 pending 的通路说 `armReason=""`、
           而缓存那条说 `degrade.code="arm_pending"`，同一份 wire 的两个键互相矛盾。）
        ③ 配置本身不齐（总闸关 / 未选源 / 没存 key / 源不认识）⇒ 那个码
        ④ 配置齐、但这个标的不在分组里 ⇒ `not_running`
        ⑤ 配置齐、在分组里、却没有 enabled 的 runtime ⇒ `arm_failed`
           （`detail` 取 `runtime.arm_error_for(...)` 记下的最近一次原因；没有记录时是
           `"原因未记录"`，绝不留空——见 §3.1.3 的 `{reason}` 占位）
    """


def arm_wire(state: ArmState) -> dict:
    """`AiTradeIntelSummary` 上 arm 四键 + `degrade` 的**唯一**写点（§3.0 第 3 条）。

    Returns:
        ``{"providerConfigured","intelEnabled","armed","armReason"}``；未装配时另带
        ``"degrade"``（码与 `armReason` **同名**，文案取 `taxonomy.DEGRADE_TEXT` 那张单一
        真源表，`retryInS` 恒为 ``None``）。

    ⚠️ `retryInS=None` 是刻意的（评审 P2-11）：装配状态没有「多久后自动重试」这回事——它要么
    等用户动手（去设置 / 点重新装配），要么等一轮跑完（`arm_pending`，而那一轮多久跑完并不
    可知）。编一个倒计时出来，会得到一个走到 0 却什么都没发生的计时器，比不显示更坏。
    前端 `degradeText` 在 `retryInS == null` 时天然不拼那半句，无需改动。

    ⚠️ **没有 `pending=` 形参**（评审 P1-8）：pending 由 `resolve_arm` / `IntelRuntime` 解进
    `state.reason`，本函数只做映射不做判定。
    """


def arm_settings_wire(state: ArmState, *, group_running: bool,
                      armed_members: list[str]) -> dict:
    """`GET/PUT/POST /api/settings/intel` 回执里那个 `arm` 块的**唯一**写点（§3.3.4）。

    与 `arm_wire` **共用同一个 `ArmState`**，四个布尔逐字段由同一处映射出来（评审 P1-7）：
    v1 让 `_intel_wire` 在路由层另拼一份、还把 `armReason` 改叫 `reason`，那就是本文开篇点名的
    `groupStateFromStatus` 形状——两张手抄表，漂移了不报错。

    Returns:
        9 键：``providerConfigured`` / ``intelEnabled`` / ``armed`` / ``reason`` /
        ``message`` / ``detail`` / ``groupRunning`` / ``armedMembers`` / ``pending``。
        ``reason`` 与 `arm_wire` 的 ``armReason`` **恒同值**（G-29 冻结）；
        ``pending`` = ``state.reason == "arm_pending"``，不是第二个自变量。
    """


def settings_fingerprint(settings, api_key: str) -> tuple:
    """「这份设置到底变没变」的指纹（纯函数）。

    把 `INTEL_SETTING_BRIDGE` 的 23 个字段值 + `bool(api_key)` + `api_key` 的 sha256 前 8
    字符拼成一个元组。**绝不把明文 key 放进指纹**（它进日志与回执的可能性太高）。

    只覆盖桥表里那 23 个键是够的：其余旋钮（`search_base_url` / `search_timeout_s` /
    `search_max_retries` / `proxy_url` / `verify_max_tokens` / `reader_*`）**只能由 env 给**，
    而进程活着的时候 env 不会变。
    """
```

#### 3.1.3 值域与文案（`taxonomy.py`）

```python
# ---------------- 装配状态码（单一真源，前后端共用） ----------------
# 顺序 = 判定顺序 = 「越靠外层的原因越先解释」。
ARM_REASONS: tuple[str, ...] = (
    "disabled",           # 总闸关（复用既有降级码，文案已在 DEGRADE_TEXT）
    "provider_unset",     # 没选源
    "key_missing",        # 选了源、没存 key
    "provider_unknown",   # 源名不在已实现列表里
    "not_running",        # 配置齐，但自动交易没在跑 / 该标的不在分组内
    "arm_failed",         # 配置齐、分组在跑，但装配抛异常（fail-open 吞掉了）
    "arm_pending",        # 设置已保存，重装排队中（等当前这一轮跑完）
)
```

`DEGRADE_TEXT` 追加 6 条（`disabled` 已在表内，复用）：

| 码 | 文案 |
|---|---|
| `provider_unset` | 尚未选择情报检索源，情报采集未启动 |
| `key_missing` | 已选择检索源，但还没有保存它的 API Key，情报采集未启动 |
| `provider_unknown` | 配置的检索源不在已实现列表内，已按未配置处理 |
| `not_running` | AI 自动交易未在运行，该标的的情报不会自动采集 |
| `arm_failed` | 情报流水线装配失败（{reason}），检索源配置本身没有问题 |
| `arm_pending` | 设置已保存，当前这一轮情报跑完后自动生效 |

> ⚠️ `arm_failed` 带 `{reason}` 占位。`degrade_message` 在占位缺失时**保留原样文本**（不抛，
> `taxonomy.py:225-228` 的 `except (KeyError, IndexError): return template`），意味着漏传会让
> 用户看到字面的 `{reason}`。故 `arm_wire` 必须无条件传 `reason=state.detail or "原因未记录"`。
> G-6 用一条用例把这件事钉死。
>
> ⚠️ **`degrade_wire` 本轮从 `service.py` 纯搬运到 `taxonomy.py`**（评审 P0-2）：它与
> `DEGRADE_TEXT` / `degrade_message` 本就同源，而留在 `service.py` 会让 `arming.py` 为了拼一个
> 三键字典去 import `service`，与 `service` 反向 import `arming` 成环。`service.py` 原样重新
> 导出（`from .taxonomy import degrade_wire  # noqa: F401`），故 `runtime.py:44,149` 等既有
> `service.degrade_wire(...)` 调用点**一个字节不改**——照抄 `service.py:756-766` 那次
> `settings_bridge` 单向搬家的先例。顺带把 `service.py` 从 766 行降到 762 行。
>
> ⚠️ **`no_provider` 这个旧码保留不动**：`service.py:251` / `service_curate.py:92` /
> `service_verify.py:147,214` 仍在用它表达「轮跑起来了但 `deps.search` 是 None」这一种**轮内**
> 降级。本轮不碰那条路径，也不给它改名——改名等同于破坏性变更（CLAUDE.md 五）。

#### 3.1.4 wire 形状（`AiTradeIntelSummary` 三个新键）

```jsonc
{
  "instId": "BTC-USDT-SWAP",
  // …既有 17 个键一字不改（instId/counts/unread/lastSeenTs/lastCollectTs/
  //   lastCurateTs/nextDueTs/inFlight/degrade/dailyUsed/dailyCap/citations/verify/
  //   quoteHitRate/alerts24h/lastAlertTs/efficacy）…
  "providerConfigured": true,   // ★ 语义修正：库里配没配（不再是「进程里装没装」）
  "intelEnabled": true,         // ★ 新：总闸
  "armed": true,                // ★ 新：此刻真的在跑
  "armReason": "",              // ★ 新：armed=false 时的原因码（ARM_REASONS 之一）
  "degrade": null               // 未装配时 = { code: armReason, message, retryInS: null }
}
```

三个新键**恒在位**（含 `_static_summary`），这是历轮四次踩过的同一个坑的成文纪律：
前端读到 `undefined` 会把「没装上」画成「一切正常」。TS 侧一律声明为**必填**，让 `tsc` 强制
所有 fixture 跟上——这正是 `WAKE_LABELS` 那次（`Record<WakeKind,…>` 强制而非静默）证明有效的
做法。

#### 3.1.5 装配点：`read_summary` / `build_snapshot` / `_summary` / `_static_summary` 的改法

```python
# service.py
async def read_summary(db, inst_id: str, *, settings, arm, now=None) -> dict:
    ...
    return {
        # …既有键不变…
        **arming.arm_wire(arm),          # ★ 覆盖 providerConfigured + degrade，另加三键
    }
```

- `arm` 取代原来的 `provider_configured: bool`（形参个数不变，仍是 5）。
- `arm_wire` 放在返回字典的**末尾展开**，故它对 `degrade` 的写入（仅未装配时）会覆盖上面那行
  `"degrade": None`；而 `IntelRuntime.refresh` 里那段「本轮降级」的覆写仍然在 `read_summary`
  **之后**发生，故一次真实的轮内降级仍然胜出。三者的优先级：轮内降级 > 未装配 > 无。

```python
# service.build_snapshot
async def build_snapshot(deps: IntelDeps, inst_id: str, *, arm=None) -> dict:
    arm = arm if arm is not None else arming.arm_of_deps(deps)
    ...
    summary = await read_summary(db, inst_id, settings=deps.settings, arm=arm, now=now)
```

`arm` 是**带默认值的 keyword-only 形参** ⇒ 既有测试调用点（`build_snapshot(deps, INST)`）一个
字节不改，且 `test_ai_intel_service.py:404` 那条 `providerConfigured is True` 的断言靠
`arm_of_deps` 的「`deps.search` 在即为真」继续成立。生产路径由 `IntelRuntime.refresh` 显式传
`self.arm`。

```python
# api/ai_intel.py
_RUNTIME_ONLY_KEYS = ("inFlight", "degrade")        # ★ providerConfigured 移出


def _is_group_member(inst_id: str) -> bool:
    """这个标的此刻是不是正在跑的自动交易分组的成员（**同步、不查库**）。

    评审 P2-3：v1 只给了名字。判据取 `group_status()` 的 `members`——`api/app_settings.py:158`
    的成本估算已经是这个用法，本轮不发明第二个口径。`group_status()` 在无活跃分组时回 `None`
    （`manager.py:1612-1622`），故「没在跑」与「跑着但不含这个标的」在这里合流成同一个 False，
    与 §十三 Q4 的裁决一致。
    """
    status = get_ai_trade_manager().group_status() or {}
    return any(str(m.get("instId")) == inst_id for m in (status.get("members") or []))


async def _summary(session, inst_id, settings) -> dict:
    derived = await intel_service.resolve_intel_settings(session, settings)
    runtime = intel_runtime.runtime_for(inst_id)
    arm = arming.resolve_arm(derived, runtime=runtime, member=_is_group_member(inst_id))
    cached = intel_runtime.intel_summary_for(inst_id)
    fresh = await intel_service.read_summary(session, inst_id, settings=derived, arm=arm)
    if cached is None:
        return fresh
    return {**fresh, **{k: cached[k] for k in _RUNTIME_ONLY_KEYS if k in cached}}
```

> ⚠️ **`degrade` 仍然从缓存优先**是刻意的：一次真实的轮内降级（`search_failed` / `circuit_open`
> / `budget_exhausted`）只有运行时知道，它比一个静态的 arm 结论更值得显示。而未装配时缓存里的
> `degrade` 本来就已经是 arm 码（由 `summary_for` 打戳），两条通路同值。
>
> ⚠️ **这条「缓存优先」正是 P1-8 的隐患所在，故 `arm_pending` 必须由 `resolve_arm` 解出来。**
> v1 让 `summary_for` 用 `arm_wire(self._arm, pending=self._arm_pending)` 打戳、而 `_summary`
> 这条通路的 `read_summary` 不传 `pending`：一次 pending 的重装期间，同一份 wire 里
> `armReason == ""`（说一切正常）而 `degrade.code == "arm_pending"`（说正在生效中）——两个键
> 说两句相反的话，且不报错。v2 的修法：`arm_pending` 是 `ARM_REASONS` 里一个**普通的 reason**，
> 由 `resolve_arm`（REST 侧，读 `runtime.arm_pending`）与 `IntelRuntime.arm`（运行时侧，
> `rearm_group` 置位时就把 `_arm` 换成 `arm_of_code("arm_pending")` 的那一份）各自解进
> `ArmState`；`arm_wire` **没有 `pending=` 形参**，只做映射不做判定。G-31 冻结两条通路同值。

```python
# runtime.py
def summary_for(self, inst_id: str) -> dict | None:
    summary = self._summary.get(inst_id)
    if summary is None:
        return None
    # arm 四键在**出口**统一打戳：缓存里的那一份可能是上一次装配期写的，而 rearm 之后它必须
    # 立刻说新话。这也是 §3.0 第 3 条「同一列只有一个人写」的落点。
    return {**summary, "inFlight": inst_id in self._in_flight,
            **arming.arm_wire(self._arm)}


def _static_summary(inst_id: str, *, code: str) -> dict:
    """未装配 / 已关闭时的静态 summary。**签名一个字节不改**（评审 P1-2）。"""
    return {
        # …既有 16 个键一字不改（原 18 键去掉 providerConfigured 与 degrade）…
        **arming.arm_wire(arming.arm_of_code(code)),   # ★ 四键 + degrade 从同一个写点出
    }
```

> ⚠️ **`_static_summary` 也必须经 `arm_wire` 出键**（评审 P1-2）。v1 的 §3.1.4 承诺「三个新键
> 恒在位（含 `_static_summary`）」、G-7 也把它列为四条通路之一，可 §3.1.5 的写点只有
> `summary_for` 与 `read_summary` 两处——静态 summary 会漏掉三键。修法是新增纯函数
> `arming.arm_of_code(code)`（§3.1.2），让它**签名不变**地经同一个 `arm_wire` 出键：
> `tests/test_ai_intel_alert_shutdown.py:135` 的 `_static_summary(INST, code="no_provider")`
> 调用点因此一个字节不改，而 §3.0 第 3 条「同一列只有一个人写」也没被破坏。
> 顺带把它硬编的 `"providerConfigured": False` 与 `"degrade": service.degrade_wire(code)`
> 两行删掉（它们从此由 `arm_wire` 写），以及 `__init__` 里 `runtime.py:96-98` 那三行
> 「`if self.enabled: seed[...] = True`」的首帧兜底——出口打戳之后它是纯粹的死码。

E-18 的那条支路一并修：`refresh` 的 `not self.enabled` 分支改为

```python
if not self.enabled:
    if inst_id not in self._summary:
        # 兜底也必须走同一个写点：**绝不**把一份没有 arm 四键的裸字典推出去。前端读到
        # `undefined` 会把「没装上」画成「一切正常」（§3.1.4 引的那个坑，历轮四次）。
        self._summary[inst_id] = _static_summary(inst_id, code=self._arm.reason
                                                 or "no_provider")
    summary = self.summary_for(inst_id)
    self._notify(inst_id, summary)
    return summary
```

与 `enabled` 分支同形。v1 写的 `self._notify(inst_id, self.summary_for(inst_id) or summary)`
**保留了裸 dict 回退**，那条 `or` 的右手边正是要消灭的那份字典（评审 P1-2）。

### 3.2 轴 B · 就地热重装（`IntelRuntime.rearm`）

#### 3.2.1 为什么是「换配置」而不是「换实例」

| 换实例（被否决） | 换配置（本方案） |
|---|---|
| 必须 `scheduler.cancel` ⇒ **撕断在飞的那一轮**（token 与检索额度白花，轮存根可能不落库） | `async with self._lock` 等它跑完，一轮都不撕 |
| 新旧两个实例各持一把 `asyncio.Lock` ⇒ 重叠期可能对同一标的并发跑两轮 | 同一把锁，串行性质是构造式保证的 |
| `_summary` / `_digest` / `_digest_ids` 全部清零 ⇒ 弹窗计数闪回 0 | 缓存原地保留 |
| `paused` 标志要手工搬运，漏搬 = 在一个已暂停的分组上偷偷放开采集 | 同一个实例，天然保留 |
| `_BY_INST` 换代竞态（旧实例的收尾回调覆盖新实例的 summary） | 注册表一个字节不改，不存在换代 |

#### 3.2.2 实现骨架

```python
# runtime.py

class IntelRuntime:
    def __init__(self, ...):
        ...
        self._arm: ArmState = arming.arm_of_deps(deps)   # ★ 装配期算一次
        self._arm_seq = 0
        self._arm_fingerprint: tuple = ()
        self._retired = False

    # ---------- 三个只读 property（评审 P1-6：外部一律经它们读，绝不直接摸私有字段） ----------
    @property
    def arm(self) -> ArmState:
        return self._arm

    @property
    def arm_pending(self) -> bool:
        """是否有一次重装排在队里等这一轮跑完。

        **不是**一个独立的自变量：它就是 `self._arm.reason == "arm_pending"`。v1 把它做成第二个
        字段，于是 `read_summary` 那条不传 `pending` 的通路与缓存那条打了戳的通路会在同一份 wire
        上说两句相反的话（评审 P1-8）。这里让它退化成 `_arm` 的一个投影，物理上杜绝分叉。
        """
        return self._arm.reason == "arm_pending"

    @property
    def arm_fingerprint(self) -> tuple:
        return self._arm_fingerprint

    def mark_arm_pending(self) -> None:
        """把这个运行时标成「重装排队中」（`rearm_group` 的超时支路唯一的置位入口）。

        **不动 `enabled`、不动 `deps`**：旧配置继续跑，只是界面上多一句「设置已保存，当前这一轮
        情报跑完后自动生效」。
        """
        self._arm = arming.arm_of_code("arm_pending")

    async def rearm(self, deps: IntelDeps, arm: ArmState, seq: int,
                    fingerprint: tuple) -> bool:
        """就地换掉这个运行时的配置。**同一实例、同一把锁、同一份缓存。**

        Args:
            deps / arm: `_build_deps` 在**锁外**造好的新依赖与新状态（造它可能失败，而一次
                失败的重装绝不允许把一条正在正常工作的流水线弄停——§3.0 第 2 条）。
            seq: 单调递增的重装序号。**只应用 `seq >= self._arm_seq` 的那一次**：用户连点
                两个开关会排出两次后台重装，晚发起的那一次必须赢。
            fingerprint: 这份配置的指纹。★ 评审 P1-9：它**必须和 `deps` 在同一把锁内、同一
                时刻赋值**。v1 让 `rearm_group` 在 `wait_for` **之前**先写 `arm_fingerprint`，
                而应不应用还要等 `rearm` 里按 `seq` 裁决——两个 PUT 在 `resolve_intel_settings`
                那次 `await` 上交错时，会出现「B 的配置应用了、A 的指纹后写进去了」：运行时跑着
                B、指纹说是 A，此后任何一次把设置改回 A 的保存都被 `changed=False` 短路，
                **A 永远装不上，且不报错**。指纹是「此刻真的在跑的那份配置」的标签，谁应用谁贴。

        Returns:
            是否真的应用了。`retired`（分组已拆卸）或 `seq` 过期 ⇒ False。

        ⚠️ **锁内不得有任何 await。** 五次赋值原子完成，故一次被 `wait_for` 取消的等待要么
        完全没应用、要么完全应用，不存在中间态。
        """
        if self._retired:
            return False
        async with self._lock:                     # 等在飞轮跑完；绝不打断它
            if self._retired or seq < self._arm_seq:
                return False
            self.deps, self._arm, self._arm_seq = deps, arm, seq
            self._arm_fingerprint = fingerprint
            self.enabled = arm.armed
        await _sync_scheduler(self)                # 起 / 停调度循环（延迟导入，同 invalidate）
        return True

    def retire(self) -> None:
        """分组拆卸时置位。此后 `rearm` / `refresh` / `invalidate` 全部是 no-op。"""
        self._retired = True
```

```python
async def _sync_scheduler(runtime: "IntelRuntime") -> None:
    """让调度循环与 `runtime.enabled` 对齐。**只在起循环这一侧是纯同步的。**"""
    from . import scheduler

    if runtime.enabled:
        scheduler.start(runtime)        # 已有循环时它自己 return（既有幂等保护）
        return
    # ★ 评审 P1-10：**不能直接 cancel**。`_run` 的 `async with self._lock` 在
    # `await self._after_round(...)` **之前**就退出了（`runtime.py:193-207`），所以此刻上一轮
    # 的 `_after_round` 可能正在跑；`scheduler.cancel` 取消的正是承载它的那个 `_loop` 任务 ⇒
    # 这一轮的快照刷新与 **`_alert` 告警交接**被静默丢弃。告警交接是「一条够格的高危情报当场
    # 多问一次 AI」那条链的第二跳（`d17eed6`），把它丢在一次开关拨动上，症状是「今天为什么一次
    # 都没告警」这个问题**恰好在这一次没有答案**——而那正是上一轮花整章去消灭的东西。
    # 故：先给 `_after_round` 一个**有界**的收尾窗口（复用同一条重装死线），超时才硬 cancel
    # 并如实记一行 warning。等待是有界的，不会把「关掉总闸」这个动作和一轮情报绑在一起。
    await _await_round_settled(runtime, timeout_s=runtime.deps.settings
                               .ai_trade_intel_rearm_deadline_s)
    scheduler.cancel(runtime.group_id)
    for inst_id in runtime.members:     # 停下来这件事也必须立刻在界面上看得见
        scheduler.schedule(runtime.refresh(inst_id))
```

> `scheduler.start` 起的 `_loop` 开头就是 `for inst_id in members: await runtime.refresh(inst_id)`
> ——所以「装上」这件事**自带**一次立刻的 summary 推送，弹窗会在一秒内自己变过来，用户不必
> 重开它。这是既有代码送给本轮的一份现成礼物，实施时不要重复推。
>
> ⚠️ `_sync_scheduler` 因为多了那段有界等待而变成 `async`，故 `rearm` 里对它的调用是
> `await _sync_scheduler(self)`，且这次 await 在**锁外**（锁内不得有 await 的纪律不变）。
> `_await_round_settled` 只轮询 `runtime._in_flight` 是否清空，**不取锁**（取锁会与它自己刚
> 释放的那把锁排队，把有界等待变成无界）。

#### 3.2.3 分组级入口 `runtime.rearm_group`

```python
async def rearm_group(*, group_id: str, settings, provider_cfg, make_client,
                      deadline_s: float = 2.5) -> dict:
    """重装一个分组的情报运行时（`manager.rearm_intel` 的唯一实现）。

    `sessionmaker` / `members` / `bar` / `change_24h_pct` / `notify` / `notify_alert` 全部
    **从已注册的那个运行时上取**——它们是分组身份的一部分，与设置无关；这也让形参收在 5 个
    以内（CLAUDE.md 二）。

    Returns:
        `{"rearmed": bool, "pending": bool, "armed": bool, "reason": str, "detail": str,
          "changed": bool}`。**绝不抛**：调用点是一个设置保存请求，情报层的任何失败都不该
        让「保存设置」这个动作失败（与 `_wire_intel` 的 fail-open 同源）。

    Note:
        `_RUNTIMES` 里没有这个分组时回 `not_running` 并**把裁决权交回 `manager.rearm_intel`**
        ——★ 评审 P0-3：`_wire_intel` 的 fail-open 是在 `register` **之前**吞的异常
        （`manager.py:1469-1493`），所以「分组在跑、却一个 runtime 都没有」正是本轮那个自救
        入口唯一要救的处境。在这里直接回 `not_running` 就是对一个正在跑的分组说
        「AI 自动交易未在运行」——一句确定错误的话，而且自救入口对自己的立项理由束手无策。
        修法见 §3.3.1：manager 回落走一次完整的 `_wire_intel`。
    """
    runtime = _RUNTIMES.get(group_id)
    if runtime is None:
        return {"rearmed": False, "pending": False, "armed": False,
                "reason": "not_running", "detail": "", "changed": False}
    async with sessionmaker_of(runtime)() as db:
        derived = await service.resolve_intel_settings(db, settings)
        api_key = await service.resolve_api_key(db, derived)
    fingerprint = arming.settings_fingerprint(derived, api_key)
    if fingerprint == runtime.arm_fingerprint and not runtime.arm_pending:
        return {..., "changed": False}          # 一次真正的空操作，连锁都不碰
    try:
        deps, arm = _build_deps(settings=derived, api_key=api_key, provider_cfg=provider_cfg,
                                make_client=make_client, ident=identity_of(runtime.deps))
    except Exception as exc:                    # noqa: BLE001 —— 见 docstring
        logger.warning("ai-trade intel rearm build failed (%s): %s", group_id, exc)
        record_arm_error(group_id, exc)
        return {"rearmed": False, ..., "reason": "arm_failed", "detail": str(exc)[:200]}
    seq = next_arm_seq()
    try:
        await asyncio.wait_for(runtime.rearm(deps, arm, seq, fingerprint),
                               timeout=deadline_s)
    except asyncio.TimeoutError:
        # 有一轮在飞（可达 60 秒）。**不阻塞这个 HTTP 请求**：把重装排进既有 tracked 任务集
        # （它已被 `_shutdown_steps` 第 7 步 drain，故本轮不新增任何关停步骤），并如实回执。
        runtime.mark_arm_pending()              # ★ 只改 `_arm`，不动 `enabled` / `deps`
        scheduler.schedule(runtime.rearm(deps, arm, seq, fingerprint))
        return {"rearmed": False, "pending": True, "armed": runtime.arm.armed,
                "reason": "arm_pending", "detail": "", "changed": True}
    clear_arm_error(group_id)
    return {"rearmed": True, "pending": False, "armed": arm.armed,
            "reason": arm.reason, "detail": arm.detail, "changed": True}
```

`_build_deps` 由 `build_group_runtime` 与 `rearm_group` **共用**（这是本轮唯一一次对既有装配
路径的重构，且 `build_group_runtime` 的公开签名一个字节不改，故
`test_ai_intel_wiring.py:184` 那个 `fake_build(**kwargs)` 替身不受影响）：

```python
@dataclass(frozen=True)
class GroupIdentity:
    """分组身份：**与设置无关、重装时必须原样带过去**的四个字段。"""

    sessionmaker: object
    group_id: str
    bar: str
    change_24h_pct: object | None


def identity_of(deps: IntelDeps) -> GroupIdentity:
    return GroupIdentity(deps.sessionmaker, deps.group_id, deps.bar, deps.change_24h_pct)


def _build_deps(*, settings, api_key: str, provider_cfg, make_client,
                ident: GroupIdentity) -> tuple[IntelDeps, ArmState]:
    """按**已解析好的** settings 造一份新依赖（纯装配，无 DB 会话、无出网）。

    Args:
        settings: `resolve_intel_settings` 的派生副本。
        api_key: `resolve_api_key` 的产物（**显式入参**——评审 P1-1：v1 的签名里没有它，
            而三行之上的调用点却传了 `api_key=`，实现出来就是 `TypeError`）。
        ident: 分组身份。★ **评审 P0-1**：这四个字段**必须显式传**，绝不能「从 `prev` 继承」。
            `build_group_runtime` 的两个壳分支（`runtime.py:416-418` / `:429-431`）构造
            `IntelDeps` 时只传了 `settings` 与 `sessionmaker`，`group_id` / `bar` /
            `change_24h_pct` 三个字段取的是 dataclass 默认值 `""` / `"15m"` / `None`——
            **壳恰恰是没有分组身份的那一个**。而「开跑时没配源、跑起来之后才去设置里配」正是
            本轮用户报告的那一种处境：从壳继承身份，会让修好之后采到的每一条情报都挂着空
            `groupId`（`service.py:358` 的 `item_to_payload(group_id=deps.group_id)`）、
            三种轮存根同样落空 `groupId`（`service.py:341,414,593` / `service_curate.py:176` /
            `service_verify.py:84`）、Agent 拿到的是写死的 15m 市场上下文
            （`service.py:256` / `service_verify.py:186`）、「近 24 小时涨跌」整行消失
            （`service.py:257`）——**而且没有任何东西会报错**。

    Returns:
        `(deps, arm)`。检索源装不上 ⇒ 返回一个 `search=None` 的 deps 与一个未装配的
        `ArmState`——**不抛**，「没配源」是一个正常状态而不是错误。

    ⚠️ 本函数是 `IntelDeps` 的**唯一**构造点。`build_group_runtime` 的两个壳分支一并改调它，
    于是 E-7 那处「两条分支形状不一致」与 P0-1 那处「壳没有身份」被同一次重构一起消灭：
    壳的 `_static_summary` 也改由 `arm.reason` 出码（`_static_summary(inst_id, code=arm.reason)`），
    不再一条分支显式覆写、另一条沿用构造期 seed。
    """
```

#### 3.2.4 竞态清单（逐条给出结论）

| 竞态 | 结论 |
|---|---|
| 重装 vs 在飞轮 | `async with self._lock` 等它；轮体拿的是创建协程那一刻传入的 `deps` 局部引用（`service.run_collect_round(self.deps, …)`），换 `self.deps` 不影响它 |
| 重装 vs `_after_round` | `_after_round` 在锁**外**，可能用新 deps 刷快照——**这是想要的**（新设置立刻反映在快照上） |
| 两次并发重装 | `_arm_seq` 单调递增，晚发起的赢；`asyncio.Lock` 保证串行应用 |
| 重装 vs `unregister` | `retire()` 先置位，`rearm` 早返回 False；孤儿 deps 被 GC |
| 重装 vs 关停 | 后台重装是 tracked task（`scheduler.schedule`），已被既有 drain 收敛；**不新增任何关停步骤** |
| `wait_for` 取消 | 取消点在 `lock.acquire()`；`async with` 的 `__aexit__` 保证不会留下被持有的锁；锁内无 await ⇒ 无部分应用 |
| 重装 vs `paused` | 同一实例 ⇒ `paused` 自动保留；`_sync_scheduler` 只管起停循环，`_run` 里的 `self.paused` 闸不变 |
| ★ **停机分支 vs `_after_round`**（评审 P1-10） | **v1 漏了这一行。** `_run` 的 `async with self._lock` 在 `await self._after_round(...)` **之前**就退出（`runtime.py:193-207`），故 `rearm` 拿到锁时上一轮的 `_after_round` 可能正在跑；`_sync_scheduler` 的 `scheduler.cancel` 取消的正是承载它的那个 `_loop` 任务 ⇒ **快照刷新与 `_alert` 告警交接被静默丢弃**。结论：停机分支先给一个**有界**收尾窗口（`_await_round_settled`，复用 `rearm_deadline_s`），超时才硬 cancel 并记 warning。G-32 冻结 |
| ★ **指纹 vs 应用**（评审 P1-9） | **v1 漏了这一行。** 指纹与 `deps` 在**同一把锁内同时**赋值（`rearm` 的第四个形参）。v1 让 `rearm_group` 在 `wait_for` 之前先写指纹，两个 PUT 在 `resolve_intel_settings` 那次 `await` 上交错即可造出「跑着 B、指纹说 A」，此后改回 A 的保存被短路吞掉、**A 永远装不上且不报错**。G-30 冻结 |
| ★ **重装 vs「压根没注册过」** | 不是竞态而是一条**缺失的支路**（评审 P0-3）：`_wire_intel` fail-open 在 `register` 之前吞异常 ⇒ `_RUNTIMES` 为空 ⇒ `rearm_group` 回 `not_running`。由 `manager.rearm_intel` 回落一次完整 `_wire_intel` 兜住。G-26 冻结 |

### 3.3 轴 C · 端点接线与自救入口

#### 3.3.1 `manager.rearm_intel`（薄转发）

```python
# manager.py（判定全在 intel/runtime.py，本文件已 2058 行，只留薄转发）
async def rearm_intel(self, *, deadline_s: float | None = None) -> dict:
    """设置改动之后就地重装情报运行时。**绝不抛**。"""
    group = self._group
    if group is None:
        return intel_runtime.ARM_NOT_RUNNING
    settings = self._group_settings(group)
    try:
        cfg = await resolve_active_provider(get_sessionmaker(), settings)
    except Exception as exc:            # noqa: BLE001 —— LLM provider 不可用不该挡住设置保存
        intel_runtime.record_arm_error(group.group_id, exc)
        return {**intel_runtime.ARM_NOT_RUNNING, "reason": "arm_failed",
                "detail": str(exc)[:200]}
    result = await intel_runtime.rearm_group(
        group_id=group.group_id, settings=settings, provider_cfg=cfg,
        make_client=make_llm_client_from_config,
        deadline_s=(deadline_s if deadline_s is not None
                    else settings.ai_trade_intel_rearm_deadline_s))
    if result["reason"] != "not_running":
        return result
    # ★ 评审 P0-3：`rearm_group` 回 `not_running` **而分组明明在跑**，只可能是一种处境——
    # `_wire_intel` 的 fail-open 在 `register` **之前**就把异常吞了（`manager.py:1469-1493`），
    # 于是 `_RUNTIMES` 里什么都没有。而那**恰恰是** §3.3.3 那个自救入口唯一的立项理由。直接把
    # `not_running` 回给用户，等于在一个正跑着的分组上说「AI 自动交易未在运行」——一句确定错误
    # 的话，而且自救入口对自己要救的那件事束手无策。
    # 回落走一次完整装配：`_wire_intel` 本就幂等（`register` 覆盖同 key、`scheduler.start` 自带
    # 幂等保护）且 fail-open，重跑一次没有任何副作用；失败原因由它的 `except` 记进 `_ARM_ERRORS`。
    await self._wire_intel(group, provider=cfg)
    return intel_runtime.arm_result_for(group.group_id)
```

`_wire_intel` 的 `except` 分支同时 `record_arm_error(group.group_id, exc)`，成功分支
`clear_arm_error(group.group_id)`——**这是 E-10 那个「fail-open 把原因吞了」的正面修法**：
原因从此有一个进程级落点，并经 `resolve_arm` 的 `arm_failed` 分支到达界面。

> `arm_result_for(group_id)` 是 `runtime.py` 里的一个薄读函数（读 `_RUNTIMES` + `_ARM_ERRORS`，
> 按 `rearm_group` 的**同一张 6 键回执**返回），使这条回落支路与正常支路的 wire 形状逐键一致
> ——两处各拼一份回执必漂移，而这块面板的全部价值就在于「失败要出声」。
>
> ⚠️ `manager.py` 因此从「只加一个薄转发」变成「加一个薄转发 + 一条回落」，预估增量由 +25 行
> 调到 +34 行（§十二 已同步）。判定仍然全在 `intel/runtime.py`，manager 这一段只有一个
> `if` 与两次调用。

#### 3.3.2 `PUT /api/settings/intel` 接线

```python
@router.put("/settings/intel", response_model=IntelSettingsOut)
async def update_intel_settings(body, session, settings):
    ...                                        # 既有写库段一个字节不改
    # ★ 写完立刻重装：没有这一跳，本轮要修的缺陷原封不动（E-11）。
    # 它**绝不抛**、**绝不让保存失败**——情报层是加法流水线（与 `_wire_intel` 同源的取舍）。
    await get_ai_trade_manager().rearm_intel()
    return await _intel_wire(session, settings)
```

顺序不可换：必须先 `set_app_setting`（带 commit）再重装，否则 `rearm_group` 里那次
`resolve_intel_settings` 读到的还是旧行。

#### 3.3.3 新增 `POST /api/settings/intel/rearm`

```python
@router.post("/settings/intel/rearm", response_model=IntelSettingsOut)
async def rearm_intel_pipeline(session, settings) -> dict:
    """手动重新装配情报流水线（幂等）。

    存在的理由：`_wire_intel` 是 fail-open 的，一次装配失败（LLM provider 暂时不可用、
    检索源 key 刚过期）会让分组在整个生命周期里都没有情报层，而用户此前**唯一**的自救手段是
    停掉一次正在管理真实资金的自动交易会话再重开。那个代价与这个问题完全不成比例。
    """
    await get_ai_trade_manager().rearm_intel()
    return await _intel_wire(session, settings)
```

不设 `_require_ai_enabled`（与两个既有 `/settings/intel*` 端点一致）。走既有
`OriginGuardMiddleware`。客户端超时档 `FAST_MS`（20s），服务端死线 2.5s ⇒ 裕量充足。

#### 3.3.4 `_intel_wire` 的 `arm` 块

```python
# api/app_settings.py —— 路由层**不拼这个块**，只把三个它自己知道的事实交给 arming
"arm": arming.arm_settings_wire(
    arm, group_running=status is not None,
    armed_members=[m for m in members if intel_runtime.runtime_for(m) is not None
                   and intel_runtime.runtime_for(m).enabled]),
```

```jsonc
{
  "providerConfigured": true,
  "intelEnabled": true,
  "armed": true,
  "reason": "",                       // ARM_REASONS 之一；与 summary 的 armReason 恒同值
  "message": "情报采集正在运行：BTC-USDT-SWAP 等 3 个标的",   // 后端下发，前端不二次编
  "detail": "",                       // arm_failed 时的异常文本（已截断）
  "groupRunning": true,
  "armedMembers": ["BTC-USDT-SWAP", "ETH-USDT-SWAP", "SOL-USDT-SWAP"],
  "pending": false                    // = (reason === "arm_pending")，不是第二个自变量
}
```

`message` 由后端拼（`DEGRADE_TEXT` 对应码 + armed 时的那一句），理由与 `degradeText` 的既有
纪律完全一致：两处各写一份文案必漂移，而这块面板的全部价值就在于「失败要出声」。

> ⚠️ **这个块必须由 `arming.arm_settings_wire` 装配，绝不在路由层手拼**（评审 P1-7）。
> §3.0 第 3 条立的规矩是「arm 四个事实的赋值只允许发生在 `arm_wire()` 一处」，而 v1 让
> `_intel_wire` 用**另一套键名**（`reason` 而不是 `armReason`）把同样四个布尔再拼一遍——那正是
> 本文开篇点名的 `groupStateFromStatus` 形状：两张手抄表，漂移了不报错。v2 把两个 wire 收进
> 同一个模块、共用同一个 `ArmState` → 键名的私有映射；**G-29** 断言两块的四个布尔对同一个
> `ArmState` 恒同值。
>
> `reason` 与 `armReason` 两个键名保持不同是**刻意**的：summary 那份是平铺在 17 个既有键里的，
> 必须带 `arm` 前缀才不至于和 `degrade.code` 混淆；settings 那份已经嵌在 `arm` 对象里，再叫
> `armReason` 就是 `arm.armReason`。**同值不同名可以，两处各判一次不行**——G-29 守的正是后者。

#### 3.3.5 `_require_runtime` 的 409 分叉

```python
async def _require_runtime(session, inst_id: str, settings):
    """取该标的的情报运行时；未装配 ⇒ 409，**并说出真正的原因**。

    ★ 评审 P0-4：v1 写的是同步 `_require_runtime(inst_id)` + 一个新的
    `intel_runtime.arm_for(inst_id)`，并注明「与 `_summary` 同一个判定」——**那是做不到的**。
    `_summary` 的判据吃的是 `resolve_intel_settings(session, settings)` 的派生副本，那是一次
    **DB 读**；而 `arm_for` 住在 `runtime.py`、是同步函数、没有会话。在「一个 runtime 都没有」
    这条**最需要说实话**的支路上（P0-3 的同一处境），它拿不到 provider / key 的库内值，只能猜
    ——于是本轮花整章消灭的那句假话，会从 `GET /intel/{instId}` 原样挪到「立即采集」的 409 里。

    故：本函数改成 `async`，与 `_summary` **调同一个 `resolve_arm`**，判据只留一份。
    四个调用点（`api/ai_intel.py:392,411,504,526`）**全部**已有 `session` 与 `settings` 在
    作用域内，各加一个 `await` 即可，形参 3 个（CLAUDE.md 二）。
    """
    runtime = intel_runtime.runtime_for(inst_id)
    if runtime is not None and runtime.enabled:
        return runtime
    derived = await intel_service.resolve_intel_settings(session, settings)
    arm = arming.resolve_arm(derived, runtime=runtime, member=_is_group_member(inst_id))
    raise HTTPException(409, detail={
        "code": f"intel_{arm.reason or 'no_provider'}",
        "message": tax.degrade_message(arm.reason or "no_provider",
                                       reason=arm.detail or "原因未记录")})
```

于是「立即采集」在一个**配好了但没装上**的标的上，回的是「情报流水线装配失败（…）」而不是
「尚未配置情报检索源」。前端 `ApiRequestError.code` 已经是原样透传，无需改动错误渲染。

> ⚠️ 兜底码用 `no_provider`（而不是 v1 的 `not_armed`）：`ARM_REASONS` 里没有 `not_armed`，
> 拼出来的 `intel_not_armed` 会让 `degrade_message` 走到「未知降级」回落，用户看到的是一句
> 裸码。`no_provider` 是既有码、文案在表内、前端 `SETTINGS_CODES` 也认得它。

### 3.4 轴 D · 前端诚实分叉

#### 3.4.1 空态三级判据（`IntelList.emptyKind`）

```ts
/**
 * 空态判据（顺序有意义）：未配源 > 总闸关 > 未装配 > 搜索无结果 > 归档页签 > 从未采集 > 本层为空。
 *
 * 前三条必须排在最前，且**必须互相分开**：它们是仅有的三种「用户不动手就永远不会有情报」的
 * 状态，而它们的出路完全不同——第一种要去选源存 key，第二种只要打开一个开关，第三种连设置都
 * 不用碰（点一次「重新装配」，或者根本就是自动交易没在跑）。把三者压成一句「尚未配置情报检索
 * 源」，会让一个**已经配好了**的用户去设置里反复确认自己没配错，然后断定这个功能坏了。
 */
if (summary && !summary.providerConfigured) return "no-provider";
if (summary && !summary.intelEnabled)       return "intel-off";
if (summary && !summary.armed)              return "not-armed";
```

`IntelEmptyStates` 新增两种 kind：

| kind | 标题 | 说明 | 动作 |
|---|---|---|---|
| `intel-off` | 市场情报采集已关闭 | 检索源已经配好了。打开设置里的「启用市场情报采集」，这个标的就会按节律开始采集；已经存下来的情报一条都不会丢。 | 去设置里开启 |
| `not-armed` | 检索源已配好，但这个标的的情报流水线没有在跑 | 文案取 `summary.degrade.message`（后端下发，区分 `not_running` / `arm_failed` / `arm_pending`）。 | `arm_failed` ⇒【重新装配】；其余无按钮 |

`no-provider` 的**标题一字不改**（仍是「尚未配置情报检索源」），因为在它真正成立的那一种情况
下，那句话本来就是对的；既有用例 `intel-empty-no-provider` 因此继续绿。

#### 3.4.2 降级 banner 的按钮分叉

```ts
// IntelEmptyStates.tsx
const SETTINGS_CODES = new Set(["no_provider", "disabled", "provider_unset",
                                "key_missing", "provider_unknown"]);
const REARM_CODES = new Set(["arm_failed"]);
// not_running / arm_pending：**不给按钮**。给一个点了什么也不发生的按钮，比不给更坏。
```

`lib/intel.ts` 的 `WEAK_DEGRADE` 追加 `not_running` 与 `arm_pending`：一个「自动交易没在跑」
的标的把按钮常染成警示色，等于把警示色用废（这与既有 `verify_no_corpus` 的登记同源）。
`arm_failed` / `provider_unset` / `key_missing` / `provider_unknown` 是**强提示**，保持染色。

#### 3.4.3 设置卡的「生效状态」卡（新组件）

`IntelSourceSettings.tsx` 现在 519 行，把这块挂成新文件 `IntelArmStatusCard.tsx`（约 90 行），
渲染在「情报检索源」卡**正下方**——用户刚存完 key，视线就在那里：

```
┌────────────────────────────────────────────────────────────┐
│ ● 已生效                                                    │
│ 情报采集正在运行：BTC-USDT-SWAP 等 3 个标的                  │
└────────────────────────────────────────────────────────────┘

┌────────────────────────────────────────────────────────────┐
│ ○ 已保存，尚未生效                        [ 重新装配 ]      │
│ 情报流水线装配失败（LLM provider 未配置），检索源配置本身没  │
│ 有问题。                                                    │
└────────────────────────────────────────────────────────────┘

┌────────────────────────────────────────────────────────────┐
│ ○ 已保存，等自动交易开启后生效                              │
│ AI 自动交易未在运行，该标的的情报不会自动采集。              │
└────────────────────────────────────────────────────────────┘
```

- 状态点的颜色：`armed` ⇒ `--long-ink`；`pending` ⇒ `--accent`；其余 ⇒ `--warn`。
- 【重新装配】只在 `reason === "arm_failed"` 时渲染（其余情况它是一个必然无效的按钮）。
  **`not_running` 与 `arm_pending` 同样不渲染它**（评审 P2-10）：前者要用户去开自动交易、
  后者只要等一轮跑完，给一个点了什么也不发生的按钮比不给更坏。判据与 §3.4.2 的 `REARM_CODES`
  **是同一个 `Set`**（从 `lib/intel.ts` 导出，设置卡与弹窗共用，不在两处各列一遍）。
- 文案**全部**取 `arm.message` / `arm.detail`，前端一个字都不编。

#### 3.4.4 影响力页签

`IntelImpactTab.resolveEmptyKind` 的第一条 `if (summary && !summary.providerConfigured) return
"not-armed"` 改为 `if (summary && !summary.armed) return "not-armed"` —— 它的 kind 本来就叫
`not-armed`，本轮才第一次名副其实。

### 3.5 关停 / 生命周期 / 并发

- **不新增任何关停步骤。** 后台重装走既有 `intel_scheduler.schedule`（tracked 任务集），已被
  `main._shutdown_steps` 第 7 步 `drain_intel_tasks` 收敛。
- `unregister(group_id)` 追加一行 `runtime.retire()`（在 pop 之后、清 `_BY_INST` 之前）。
- `_ARM_ERRORS: dict[str, str]` 是模块级（与 `_RUNTIMES` 同款理由：让「它活得比 manager 实例
  久」在类型上就不可能发生）；`unregister` 时一并清除该分组的条目，避免下一个分组读到上一个
  分组的失败原因。
- 进程级 `_ARM_SEQ` 单调计数器（`itertools.count`），只增不减、不按分组分区——序号只用于
  比大小，跨分组共用一个计数器不会出错且少一层字典。

---

## 四、接口设计

### 4.1 REST

| 方法 | 路径 | 变化 |
|---|---|---|
| `GET` | `/api/settings/intel` | 回执**加** `arm` 块（9 键）。既有 29 个键一字不改。 |
| `PUT` | `/api/settings/intel` | 写库后**当场重装**；回执同上。请求体一字不改。 |
| `POST` | `/api/settings/intel/rearm` | **新增**。无请求体。回执 = `IntelSettingsOut`（含 `arm`）。 |
| `GET` | `/api/intel/{instId}` | `summary` **加** 3 键（`intelEnabled` / `armed` / `armReason`），`providerConfigured` 语义修正。 |
| `POST` | `/api/intel/{instId}/seen` | 同上（同一个 `_summary`）。 |
| `POST` | `/api/intel/{instId}/collect` | 409 的 `code` / `message` 按 `armReason` 分叉（不再一律 `intel_no_provider`）。 |
| `POST` | `/api/intel/{instId}/curate` | 同上。 |
| `POST` | `/api/intel/{instId}/verify` | 同上（**评审 P2-4 补登记**：`api/ai_intel.py:504` 是第三个 `_require_runtime` 调用点，v1 的表漏了它）。 |
| `POST` | `/api/intel/items/{itemId}/verify` | 同上（同一个 `_require_runtime`，`:526`）。 |

> ⚠️ `_require_runtime` 改成 `async`（§3.3.5 / 评审 P0-4）之后，**四个**调用点
> （`:392` collect / `:411` curate / `:504` verify / `:526` item-verify）都要改成
> `await _require_runtime(session, instId, settings)`。漏改一个是 `TypeError: object
> IntelRuntime can't be used in 'await' expression` 的反面——拿到的是一个协程对象，
> `runtime.lock` 立刻 `AttributeError`。四处一次改完，不留半路。

**错误码增量**（`_require_runtime`）：`intel_disabled` / `intel_provider_unset` /
`intel_key_missing` / `intel_provider_unknown` / `intel_not_running` / `intel_arm_failed` /
`intel_arm_pending`。旧码 `intel_no_provider` **保留**（`resolve_arm` 无法归类时的兜底），故
既有前端错误处理不会因为一个未知码而崩。

### 4.2 WebSocket

**不新增任何事件类型。** 既有 `intel` 事件与 `snapshot.intel` 的 payload 各多 3 个键
（`routeMemberEvent` 的两个装配点已经共用同一个 setter，故前端零接线变更）。

### 4.3 决策 prompt

**一个字节不改。** `build_decision_prompt` / `prompt_sections.py` / `intel/prompts.py` 全部
不在本轮的文件变更清单里。装配状态是一个**面板事实**，它不该出现在一次真实资金决策的
prompt 里——一条「情报流水线没装上」的说明对模型的交易判断没有任何用处，只会占预算。

---

## 五、数据模型

### 5.1 DB

**零变更。** 不新建表、不加列、不改索引、不新增 patcher。`db/session.py` 与
`db/ai_intel_repositories.py` 均不在文件变更清单里。

理由：装配状态 = f(库里的 `app_settings` 行, 进程里的运行时) —— 两个自变量都已经有落点。
把它落库只会造出第三份可能与前两份不一致的副本。

### 5.2 内存形状

```python
# intel/arming.py
@dataclass(frozen=True)
class ArmState:
    provider_configured: bool
    intel_enabled: bool
    armed: bool
    reason: str = ""     # tax.ARM_REASONS 之一
    detail: str = ""     # 仅 arm_failed；异常文本，截断到 200 字符

# intel/runtime.py（模块级）
_ARM_ERRORS: dict[str, str] = {}          # group_id -> 最近一次装配失败的原因
_ARM_SEQ = itertools.count(1)             # 进程级单调重装序号

# intel/runtime.py（实例级新增；★ 评审 P1-6：**全部私有 + 三个只读 property**，
# 外部一律经 `runtime.arm` / `runtime.arm_pending` / `runtime.arm_fingerprint` 读）
IntelRuntime._arm: ArmState               # 唯一真值；`arm_pending` 是它的一个投影
IntelRuntime._arm_seq: int
IntelRuntime._arm_fingerprint: tuple      # settings 指纹，用于「真的没变就别动锁」
IntelRuntime._retired: bool

# 三个只读 property + 一个置位方法（写点全部收敛在类内）
IntelRuntime.arm -> ArmState
IntelRuntime.arm_pending -> bool          # == (self._arm.reason == "arm_pending")
IntelRuntime.arm_fingerprint -> tuple
IntelRuntime.mark_arm_pending() -> None

# ★ v1 的 `_arm_pending: bool` **已删除**（评审 P1-8）：pending 是 `ARM_REASONS` 里的一个
# 普通 reason，不是与 `_arm` 并列的第二个自变量。并列的那一刻，`read_summary`（不传 pending）
# 与 `summary_for`（传 pending）就会在同一份 wire 上说两句相反的话。
```

```python
# intel/runtime.py（新增的分组身份载体，见 §3.2.3 / 评审 P0-1）
@dataclass(frozen=True)
class GroupIdentity:
    sessionmaker: object
    group_id: str
    bar: str
    change_24h_pct: object | None
```

`settings_fingerprint(settings, api_key) -> tuple` 是纯函数：把 `INTEL_SETTING_BRIDGE` 的
23 个字段值 + `bool(api_key)` + `api_key` 的 sha256 前 8 字符拼成一个元组。**绝不把明文 key
放进指纹**（它会进日志与回执的可能性太高）。

### 5.3 值域

`taxonomy.ARM_REASONS`（7 项，见 §3.1.3）与 `DEGRADE_TEXT` 的 6 条新文案。TS 侧
`intelTypes.ts` 声明 `type IntelArmReason = "" | "disabled" | "provider_unset" | ...`，与后端
逐字对齐；`IntelArm` 接口 9 键。

---

## 六、文件变更清单

### 6.1 后端（新 1 / 改 11）

| 文件 | 新/改 | 一句话意图 |
|---|---|---|
| `services/quant/app/ai_auto/intel/arming.py` | 新 | 装配状态纯核心：`ArmState` + `arm_of_code` / `classify_config` / `arm_of_deps` / `resolve_arm` / `arm_wire` / `arm_settings_wire` / `settings_fingerprint`。**只依赖 `search` 与 `taxonomy` 两个叶子模块**（评审 P0-2） |
| `services/quant/app/ai_auto/intel/search.py` | 改 | 抽出 `classify_provider`（三码真值表单一真源），`resolve_search_provider` 改调它 |
| `services/quant/app/ai_auto/intel/taxonomy.py` | 改 | `ARM_REASONS` 值域 + `DEGRADE_TEXT` 追加 6 条文案；★ **`degrade_wire` 从 `service.py` 纯搬运进来**（评审 P0-2：破 `arming` ⇄ `service` 的环） |
| `services/quant/app/ai_auto/intel/service.py` | 改 | `read_summary(..., arm=)`；`build_snapshot(..., arm=None)`；`degrade_wire` **搬出**并原样重新导出（`from .taxonomy import degrade_wire  # noqa: F401`，故 `service.degrade_wire(...)` 的既有调用点一个字节不改）。★ **不再重新导出 `arming` 的任何名字**（评审 P0-2：那条 `from .arming import …` 正是环的另一半）——调用方一律 `from . import arming` |
| `services/quant/app/ai_auto/intel/runtime.py` | 改 | `_arm` / `_arm_seq` / `_arm_fingerprint` / `_retired` + 三个只读 property + `mark_arm_pending()`；`GroupIdentity` / `identity_of()` / `_build_deps()`（**`IntelDeps` 的唯一构造点**，两个壳分支一并改调它 —— 评审 P0-1）；`rearm()` / `rearm_group()` / `_sync_scheduler()` / `_await_round_settled()` / `record_arm_error` / `clear_arm_error` / `arm_result_for`；`summary_for` 与 `_static_summary` 出口打戳；`refresh` 的 disabled 支路修 E-18；`unregister` 调 `retire`。★ **不再有 `arm_for`**（评审 P0-4：判据只留 `resolve_arm` 一份） |
| `services/quant/app/ai_auto/intel/scheduler.py` | 改 | `start()` 的幂等语义写进 docstring（本轮第一次有第二个调用点） |
| `services/quant/app/ai_auto/manager.py` | 改 | 新增薄转发 `rearm_intel()` + **一条 `not_running` 回落走 `_wire_intel`**（评审 P0-3）；`_wire_intel` 的 except 记录 arm error、成功分支清除 |
| `services/quant/app/api/ai_intel.py` | 改 | `_summary` 改用 `resolve_arm`；`_RUNTIME_ONLY_KEYS` 收缩为 2 键；`_require_runtime` **改 `async` 并与 `_summary` 共用 `resolve_arm`**（评审 P0-4），四个调用点各加 `await`；新增 `_is_group_member`（读 `group_status().members`，评审 P2-3） |
| `services/quant/app/api/app_settings.py` | 改 | `_intel_wire` 加 `arm` 块（**由 `arming.arm_settings_wire` 装配，路由层不手拼** —— 评审 P1-7）；PUT 后重装；新增 `POST /settings/intel/rearm` |
| `services/quant/app/schemas.py` | 改 | 新增 `IntelArmOut`；`IntelSettingsOut.arm` |
| `services/quant/app/config.py` | 改 | 新增 `ai_trade_intel_rearm_deadline_s` + 一条启动期校验器 |
| `.env.example` | 改 | 登记 `AI_TRADE_INTEL_REARM_DEADLINE_S`（不登记会被既有双向对齐用例判红） |

### 6.2 前端（新 1 / 改 8）

| 文件 | 新/改 | 一句话意图 |
|---|---|---|
| `apps/web/components/Settings/IntelArmStatusCard.tsx` | 新 | 设置卡下方的「生效状态」卡 + 【重新装配】按钮 |
| `apps/web/lib/intelTypes.ts` | 改 | `AiTradeIntelSummary` 加 3 必填键；新增 `IntelArmReason` / `IntelArm`；`IntelSettings.arm` |
| `apps/web/lib/intel.ts` | 改 | `WEAK_DEGRADE` 加 2 码；新增 `degradeAction(degrade)` 返回 `"settings" \| "rearm" \| "none"` |
| `apps/web/lib/intelApi.ts` | 改 | 新增 `rearm()` |
| `apps/web/components/Intel/IntelEmptyStates.tsx` | 改 | 2 个新 kind（`intel-off` / `not-armed`）+ banner 按钮三分叉 |
| `apps/web/components/Intel/IntelList.tsx` | 改 | `emptyKind` 三级判据 + 新 kind 的 props 透传 |
| `apps/web/components/Intel/IntelModal.tsx` | 改 | 给 banner 与空态接 `onRearm`（调 `intelApi.rearm()` 后 `load()`） |
| `apps/web/components/Intel/IntelImpactTab.tsx` | 改 | `not-armed` 判据改读 `armed` |
| `apps/web/components/Settings/IntelSourceSettings.tsx` | 改 | 挂 `IntelArmStatusCard`；`save()` 之后把回执里的 `arm` 一并存下 |

### 6.3 桌面壳（改 1 · 仅测试文件）

**产品代码零改动。** 不新增 IPC、不新增窗口、不改 `agent.env` 透传规则。新增的配置项前缀仍是
`AI_TRADE_INTEL_`，天然被既有**按前缀**的白名单覆盖。

| 文件 | 新/改 | 一句话意图 |
|---|---|---|
| `desktop/scripts/config-passthrough.test.mjs` | 改 | 追加一行哨兵键 `AI_TRADE_INTEL_REARM_DEADLINE_S` |

> 评审 P2-9：v1 写的是「桌面壳零改动」，但 closed-loop 与 actionability **两轮都补过哨兵键**
> （该文件第 28-37 行两段注释即两轮的登记）。前缀白名单确实天然覆盖新键，**补这一行不是为了
> 让它通过，而是为了在将来有人把前缀规则改窄时立刻判红**——那正是这张表存在的全部理由。
> 一行、纯测试文件、不碰任何产品代码。

### 6.4 文档

| 文件 | 意图 |
|---|---|
| `docs/plans/ai-trade-intel-arm-state/spec.md` | 本文 |
| `README.md` | 在既有「AI 自动交易的标的市场情报」段落末尾追加一句：设置检索源之后**当场生效**，不需要重启自动交易；面板会分别说清「没配源 / 总闸关 / 没装上」三种情况 |

---

## 七、测试与验收

### 7.1 验收总闸（G-1 … G-32）

**轴 A · 真值化**

- **G-1** `classify_provider` 的真值表：`("none","k")→provider_unset`、`("", "k")→provider_unset`、
  `("tavily","")→key_missing`、`("bogus","k")→provider_unknown`、`("tavily","k")→""`；
  大小写与前后空格归一（`" Tavily "` 等价于 `"tavily"`）；`api_key` 是纯空白 ⇒ `key_missing`。
- **G-2** `resolve_search_provider` 在改写前后对 5 组入参的返回值逐一相同（`None` / 三个适配器
  类型）；`provider_unknown` 那一组仍打出且**只打出**一行 `logger.info`；**并显式登记**
  「拼错的 slug + 空 key」这一组由静默改为打日志（§3.1.1 的刻意行为变化）。
- **G-3** `ArmState` 的不变式：构造 `armed=True, provider_configured=False` 抛 **`ValueError`**
  （不是 `AssertionError` —— 评审 P1-5），且异常消息里**同时含**四个字段的值。
- **G-4** `arm_of_deps`：`deps.search` 非 None ⇒ `(True, True, True, "")`，**即便 settings 是
  默认值**（provider="none"）——这条冻结 `test_ai_intel_service.py:404` 的既有断言。
- **G-5** 四种未装配处境各自回不同的 `reason`：总闸关 / 未选源 / 没存 key / 源不认识。
- **G-6** `arm_wire` 对每一个 `ARM_REASONS` 元素都能拿到一句**不含 `{` 的**中文文案
  （即：`arm_failed` 必然带上 `reason` 占位的实参）；且每一份 `degrade` 的 `retryInS` 恒为
  `None`（评审 P2-11：装配状态没有「多久后自动重试」这回事）。
- **G-6b** ★ **`arm_of_code` 的回落**（评审 P1-2）：`arm_of_code("no_provider")`（一个**不在**
  `ARM_REASONS` 里的轮内降级码）回 `(False, False, False, "no_provider")` 而不是抛；
  `arm_of_code("disabled")` 回 `provider_configured=True, intel_enabled=False`。
- **G-7** `AiTradeIntelSummary` 的 wire 上，三个新键在**四条通路**上都在位且同值：
  `_static_summary` / `IntelRuntime.summary_for` / `service.read_summary` / REST `_summary`。
  ★ `_static_summary` 那一条**必须真的断言键在位**（评审 P1-2：v1 只在 §3.1.4 口头承诺，
  写点却只有两处）；断言方式是直接调 `intel_runtime._static_summary(INST, code="disabled")`
  并检查四个键——**该函数的签名一个字节不改**，`test_ai_intel_alert_shutdown.py:135` 继续绿。
- **G-8** ★ **本轮的核心回归**：`app_settings` 里写好 `provider=tavily` + key 之后，
  `GET /api/intel/{instId}` 的 `summary.providerConfigured` 为 `true`，**即使进程里一个
  `IntelRuntime` 都没有**（此时 `armed=false`、`armReason="not_running"`）。
  ★ **key 必须只写进 `app_settings`、env 留空**（评审补充取证）：那是打包形态下唯一的存 key
  途径，也是本轮用户报告的那一种处境；把 key 塞进 env 的用例会让这条闸恒绿而什么都没验到。
- **G-9** 反向冻结：`provider="none"` 时 `providerConfigured` 为 `false` 且
  `armReason="provider_unset"`（旧行为的那一半必须原样保住）。

**轴 B · 热重装**

- **G-10** ★ **本轮的验收原命题**：一个 `enabled=False` 的壳运行时 + 写好 provider 与 key 之后，
  调一次 `rearm_group`，该运行时**同一个实例**变成 `enabled=True`、`deps.search is not None`，
  且 `intel_scheduler.loops()` 里多出这个分组的循环。
- **G-11** 反向：把 `ai_trade_intel_enabled` 写成 `false` 再重装 ⇒ 同一实例 `enabled=False`、
  循环被 cancel、每个成员各收到一次带 `armReason="disabled"` 的 summary 推送。
  （这条同时覆盖 E-18 的修法：推出去的那一份**必须**带齐 arm 四键。）
- **G-12** 缓存不闪回：重装前后 `runtime.summary_for(inst)["counts"]` 与
  `runtime.digest_for(inst)` 的对象内容不变。
- **G-13** `paused=True` 的运行时重装之后仍然 `paused=True`（漏搬 = 在暂停的分组上偷偷放开采集）。
- **G-14** 在飞轮不被撕断：持锁模拟一轮在飞，`rearm_group(deadline_s=0.05)` 返回
  `pending=True` 且**不抛**；释放锁后后台任务把配置应用上（`enabled` 翻真）。
  ★ 并断言 pending 期间 `runtime.arm.reason == "arm_pending"` 而 `runtime.enabled` **未变**
  （`mark_arm_pending` 只改 `_arm`，不动 `enabled` / `deps`）。
- **G-15** 序号语义：连续两次 `rearm(deps_a, arm_a, 1, fp_a)` / `rearm(deps_b, arm_b, 2, fp_b)`
  乱序完成时，最终生效的恒是 `seq=2` 那一份。
- **G-16** `_build_deps` 抛异常时 `rearm_group` 回 `reason="arm_failed"` 且**旧运行时一个字段
  都没被改**（§3.0 第 2 条）。
- **G-17** `retire()` 之后 `rearm` 回 `False`、`refresh` 不再 `notify`。
- **G-18** 指纹短路：两次完全相同的设置连续重装，第二次回 `changed=False` 且**没有获取过锁**
  （用一个会记录 `acquire` 次数的替身锁断言）。
- **G-19** `build_group_runtime` 的公开签名与返回值逐字段不变（`test_ai_intel_wiring.py` 的
  三条既有用例不改一个字节即通过）。
- **G-26** ★ **评审 P0-3**：模拟 `_wire_intel` 抛异常（`monkeypatch` 让 `build_group_runtime`
  抛）⇒ `_RUNTIMES` 为空、分组仍在跑；此时 `POST /api/settings/intel/rearm` **必须真的把流水线
  装起来**（`runtime_for(inst).enabled is True`），而**不是**回一句 `not_running`。
  再加一条反向：分组确实没在跑（`self._group is None`）时仍回 `not_running`。
- **G-27** ★ **评审 P0-1**：从一个**壳**运行时（`build_group_runtime` 走 `provider is None`
  那条分支造出来的，`group_id="grp-x"` / `bar="4H"` / `change_24h_pct` 非 None）重装成功之后，
  `runtime.deps.group_id == "grp-x"`、`deps.bar == "4H"`、`deps.change_24h_pct is 原回调`。
  **这条闸直接对应本轮用户报告的那一种处境**（开跑时没配源、跑起来才去配），漏了它，修好之后
  采到的每一条情报都挂空 `groupId`、上下文用写死的 15m，而且不报错。
- **G-28** ★ **评审 P0-2**：一条**只 import `arming`** 的用例
  （`import app.ai_auto.intel.arming`，进程内此前没有 import 过 `intel.service`）必须成功；
  再加一条源码扫描断言 `intel/arming.py` 里**不出现** `from . import service` /
  `from .service import`（正则写 `\r?\n`，`core.autocrlf=true`）。
- **G-30** ★ **评审 P1-9**：指纹只在**应用成功时**更新。构造交错序——A 算出 fp(A)、B 算出
  fp(B)、B 先应用（seq 2）、A 后到（seq 1，被拒）——断言 `runtime.arm_fingerprint == fp(B)`；
  随后用**设置 A** 再调一次 `rearm_group`，必须 `changed=True` 且真的装上（v1 的写法会在这里
  回 `changed=False`，A 永远装不上）。
- **G-32** ★ **评审 P1-10**：一轮刚出锁、`_after_round` 仍在跑时把总闸关掉 ⇒ `_sync_scheduler`
  **先等**它跑完（`_alert` 的 handoff 与那次 `refresh` 都发生过）再 cancel；用一个恒不结束的
  `_after_round` 替身验证有界性：超过 `rearm_deadline_s` 即硬 cancel 并打一行 warning，
  **整个调用不超过死线的两倍**。

**轴 C · 接线**

- **G-20** ★ **端到端**：`PUT /api/settings/intel {provider:"tavily", apiKey:"k"}` 之后，
  同一个进程里 `intel_runtime.runtime_for(inst).enabled is True`，且回执的 `arm.armed is True`。
- **G-21** `PUT` 在 `rearm_intel` 抛任意异常时**仍然 200**（保存不该被情报层拖垮），且回执的
  `arm.reason == "arm_failed"`。
- **G-22** `POST /api/settings/intel/rearm` 幂等：连调两次，第二次 `arm.pending is False`、
  `armed` 不变。
- **G-23** `_require_runtime` 的 409：四种未装配处境各回不同的 `error.code`，且
  `error.message` 与 `DEGRADE_TEXT` 逐字一致（不在路由层写第二份文案）。
  ★ 并断言 `arm_failed` 那一条的 `message` **不含** `apiKey` / `sk-` 等密钥字样（R-11 的兑现
  ——评审 P2-5：v1 只在 R-11 里说「在 G-23 里断言」，G-23 正文却没有这一句）。
- **G-25** ★ **两条读 key 通路等价**：对同一份库状态，
  `search.classify_provider(derived.…search_provider, derived.…search_api_key) == ""`
  当且仅当 `search.resolve_search_provider(slug=derived.…provider,
  api_key=await resolve_api_key(db, derived), …)` 非 `None`。**必须覆盖「库内行是空串 ⇒ 回落
  env 值」与「库内行是纯空白 ⇒ 判 key_missing」两条边界**——这两条通路各读一次 key（一条经
  `resolve_intel_settings` 叠进 `Settings` 副本、一条经 `resolve_api_key` 直读库），分叉了就会
  重新造出本轮要修的那句假话，而且不报错。
  > ⚠️ 评审 P1-4：v1 把左边写成 `classify_config`，**那个命题不可满足**——`classify_config`
  > 的第一条判据是总闸（`disabled`），而 `resolve_search_provider` 根本不看总闸，于是「配好了
  > 源却关着总闸」时左边非空、右边非 `None`。一条注定判红的闸等于没有闸。等价性只在
  > `classify_provider` 这一层成立，总闸那一段另立 G-25b。
- **G-25b** `classify_config` 的**层级**：总闸关时恒回 `"disabled"`，**不论** provider 与 key
  配得多齐（「一个把总闸关掉的用户不需要被追问你还没选检索源」）；总闸开时它逐字等于
  `classify_provider(...)`。
- **G-29** ★ **评审 P1-7**：对同一个 `ArmState`，`arm_wire(state)` 的
  `providerConfigured/intelEnabled/armed/armReason` 与
  `arm_settings_wire(state, …)` 的 `providerConfigured/intelEnabled/armed/reason`
  **四对逐一相等**；且 `arm_settings_wire(...)["pending"] == (state.reason == "arm_pending")`。
- **G-31** ★ **评审 P1-8**：一次 pending 的重装期间，`GET /api/intel/{instId}` 的
  `summary.armReason` 与 `summary.degrade.code` **同为 `"arm_pending"`**（v1 的写法会让前者是
  `""`、后者是 `"arm_pending"`——同一份 wire 的两个键说两句相反的话）。

**轴 D · 前端**

- **G-24** `emptyKind` 的三级分叉：`{providerConfigured:false}` ⇒ `no-provider`；
  `{providerConfigured:true, intelEnabled:false}` ⇒ `intel-off`；
  `{providerConfigured:true, intelEnabled:true, armed:false}` ⇒ `not-armed`；三者的
  `data-testid` 各不相同，且 `not-armed` 的正文取自 `summary.degrade.message`。

### 7.2 测试文件规划

| 文件 | 新/改 | 覆盖 |
|---|---|---|
| `services/quant/tests/test_ai_intel_arming.py` | 新 | G-1 … G-7、G-6b、G-9、G-25b、G-28、G-29（纯核心，零 DB、零网络）；**G-25 除外**——它要真库，落在 `test_ai_intel_settings_rearm.py` |
| `services/quant/tests/test_ai_intel_rearm.py` | 新 | G-10 … G-19、G-27、G-30、G-32（含替身锁与替身 scheduler） |
| `services/quant/tests/test_ai_intel_api.py` | 改 | G-8、G-23、G-31；★ **三条既有断言按新语义更新**（见下方方框） |
| `services/quant/tests/test_ai_intel_settings_rearm.py` | 新 | G-20 … G-22、G-25、G-26（`PUT` / `POST rearm` 的端到端 + 两条读 key 通路等价 + 从未装配过的分组的自救） |
| `services/quant/tests/test_ai_intel_service.py` | 改 | `:613` 那次 `read_summary(..., provider_configured=True)` 改成 `arm=`；`:404` **不改**（由 G-4 保住） |
| `services/quant/tests/test_ai_intel_scheduler.py` | 改 | `:85` 不改（壳仍 `False`）；★ **`:86` 必改**（见下方方框） |
| `services/quant/tests/test_ai_intel_search.py` | 改 | G-2（把既有的「拼错 slug 按未配置处理」扩成完整真值表） |
| `services/quant/tests/test_ai_intel_wiring.py` | 改 | 仅追加 G-19；既有三条**一个字节不改** |
| `services/quant/tests/test_ai_intel_alert_shutdown.py` | **不改** | `:135` 的 `_static_summary(INST, code="no_provider")` 靠 `arm_of_code` 的回落分支继续绿（G-6b 冻结）——**这是 P1-2 选择「签名不变」修法的直接理由** |
| `apps/web/components/Intel/IntelList.armstate.test.tsx` | 新 | G-24 |
| `apps/web/components/Intel/IntelModal.test.tsx` | 改 | fixture 补 3 键；新增「装配失败 ⇒ 点【重新装配】发出 POST」一条 |
| `apps/web/components/Settings/IntelArmStatusCard.test.tsx` | 新 | 三种状态各渲染一次 + 按钮可见性（`not_running` / `arm_pending` **不得**渲染【重新装配】） |
| `apps/web/components/Chart/AiTradeIntelButton.test.tsx` / `IntelCard.test.tsx` / `lib/intel.test.ts` / `lib/store.test.ts` / `lib/useAiTradeGroupStream.intel.test.ts` / `lib/useAiTradeGroupStream.pause.test.ts` | 改 | fixture 补 3 键（`tsc` 强制，不是可选） |
| `apps/web/components/Intel/IntelImpactTab.test.tsx` | 改 | fixture 补 3 键 **+ `:122` 的 `summary({ providerConfigured: false })` 必须改成 `summary({ armed: false })`**（评审 P2-7） |
| `desktop/scripts/config-passthrough.test.mjs` | 改 | 追加哨兵键 `AI_TRADE_INTEL_REARM_DEADLINE_S`（评审 P2-9） |

> ⚠️ **评审 P1-3：`degrade.code` 的爆炸半径，v1 一条都没登记。** `arm_wire` 会用**新码**覆写
> `degrade`，而未装配的默认 `Settings()`（`config.py:976` 实测 `ai_trade_intel_enabled = True`、
> `provider="none"`）解出来的是 `provider_unset`，**不是** `no_provider`。R-4 只核对了
> `providerConfigured` 这一个键，于是漏掉三条必红的既有断言：
>
> | 位置 | 现断言 | 新期望 | 为什么这是对的 |
> |---|---|---|---|
> | `test_ai_intel_scheduler.py:86` | `degrade["code"] == "no_provider"` | `== "provider_unset"` | 壳的真实处境就是「总闸开着、一个源都没选」，`no_provider` 是它的粗粒度前身 |
> | `test_ai_intel_api.py:245` | `degrade["code"] == "no_provider"` | `== "provider_unset"` | 同上（同一种壳） |
> | `test_ai_intel_api.py:198` | `error.code == "intel_no_provider"` | `== "intel_provider_unset"` | `_require_runtime` 分叉之后，「没选源」与「配好了没装上」必须是两个码——这正是本轮的立项理由 |
>
> 三条都是**刻意的**语义升级，不是回归；但必须在实施前就登记好，否则实现者会把它们当成自己
> 改坏了什么，然后去改 `arm_wire` 让它继续回 `no_provider`——那等于把本轮做了个空。

### 7.3 跑测纪律（本仓库成文教训，逐条照抄）

1. **后端全量 pytest 不能并发跑**：`services/quant/tests` 共用同一个临时 DB。
2. **必须 `python -u`**，否则长跑输出被缓冲、看起来像挂死。
3. **控制台是 GBK**：断言消息与 `print` 里不得出现 `⇒` / `→` 这类字符（会 `UnicodeEncodeError`
   崩在无关的地方）。需要时先 `set PYTHONIOENCODING=utf-8`。
4. **前端组件用例必须 `NODE_ENV=test`**，否则 React `act()` 报错。
5. **源码正则冻结类用例一律写 `\r?\n`**（`core.autocrlf=true`）。
6. **判回归必须跑全量**，`-k` 子集不作数。入轮基线：后端 4718 passed / 7 failed（7 条全部来自
   未跟踪且从未提交的 `test_factor_library_expansion.py`，入轮前即红）；前端 vitest 211 文件 /
   2437 用例全绿 + `tsc` 干净；桌面 43 条全绿；`ruff F,E9` 干净。
7. **`.env.example` 的双向对齐用例**（`test_ai_intel_wiring.py:430`）判红之后唯一正确的动作是
   补那一行，不是放宽正则。

---

## 八、风险与缓解

| # | 风险 | 缓解 |
|---|---|---|
| R-1 | 每次拨一个开关都触发一次重装，用户连点时排出一串后台任务 | 指纹短路（G-18）+ 单调 `seq`（G-15）；就地重装不撕断任何东西，最坏情况是几次无害的赋值 |
| R-2 | `PUT` 因为等锁而变慢，开关看起来卡 | 硬死线 2.5s + 超时转后台；`Switch` 的 `busy` 态本就存在；死线 ≪ 客户端 20s 档 |
| R-3 | `asyncio.wait_for` 取消 `lock.acquire()` 在某些 Python 版本上会丢锁 | 目标运行时是 CPython 3.11（该缺陷在 3.8/3.10 已修）；且锁内无 await ⇒ 即便丢锁也不会有部分应用。G-14 直接覆盖这条路径 |
| R-13 | ★ **热重装造出一条身份被抹掉的流水线**（评审 P0-1） | `_build_deps` 的 `ident: GroupIdentity` 是**显式必填形参**，不从 `prev` 继承；`build_group_runtime` 的两个壳分支一并改调它，使「壳没有身份」这件事在类型上不再可能。G-27 冻结 |
| R-14 | ★ **`arming` ⇄ `service` 模块级成环，后端起不来**（评审 P0-2） | `degrade_wire` 纯搬运到 `taxonomy.py`；`arming` 只依赖两个叶子模块。G-28 用「只 import arming」的用例 + 一条源码扫描双重冻结 |
| R-15 | ★ **自救入口救不了它唯一要救的那种处境**（评审 P0-3） | `manager.rearm_intel` 在 `not_running` 且分组确实在跑时回落走一次 `_wire_intel`。G-26 冻结 |
| R-16 | ★ **两份 arm 判据 / 两张 arm wire 手抄表**（评审 P0-4 / P1-7） | 删掉 `arm_for`，`_require_runtime` 改 `async` 与 `_summary` 共用 `resolve_arm`；`arm` 块由 `arming.arm_settings_wire` 装配。G-29 断言两块四个布尔恒同值 |
| R-4 | `providerConfigured` 语义变更打翻既有断言 | 全仓只有 4 处断言它（`test_ai_intel_api.py:78,246` / `test_ai_intel_scheduler.py:85` / `test_ai_intel_service.py:404`），逐条已核对：前三条在默认 Settings 下仍为 `False`，第四条由 G-4 保住 |
| R-4b | ★ **`degrade.code` 的语义变更打翻另外三条断言**（评审 P1-3；v1 完全漏了这一格） | `arm_wire` 用**新码**覆写 `degrade`，默认 `Settings()`（`enabled=True` + `provider="none"`）解出的是 `provider_unset` 而不是 `no_provider`。`test_ai_intel_scheduler.py:86` / `test_ai_intel_api.py:245` / `test_ai_intel_api.py:198` 三条必红，已在 §7.2 逐条登记新期望值并写明「这是刻意升级、不是回归」——不登记的后果是实现者把 `arm_wire` 改回粗粒度码，本轮做个空 |
| R-4c | ★ **`_static_summary` 的签名若改，会打翻第四个调用点** | `tests/test_ai_intel_alert_shutdown.py:135` 直接调 `_static_summary(INST, code="no_provider")`。故 P1-2 的修法刻意选「签名不变 + 内部经 `arm_of_code` 回落」而不是「改成收 `ArmState`」；G-6b 冻结那条回落 |
| R-5 | TS 加 3 个必填键打翻大量 fixture | 这是**刻意**的：`tsc` 是这里唯一可靠的门禁（`WAKE_LABELS` 的先例）。7 个测试文件的 fixture 逐个补，不加 `?` |
| R-6 | `_ARM_ERRORS` 是模块级字典，分组结束后残留 | `unregister` 里一并清除；G-17 覆盖 |
| R-7 | `classify_provider` 的判定顺序变化让「拼错 slug + 空 key」多打一行日志 | 已在 §3.1.1 显式登记为刻意变化，并由 G-2 冻结；这是一个更诚实的行为 |
| R-8 | 重装读到的是**旧**的 `app_settings` 行（顺序错） | §3.3.2 明确要求 `set_app_setting`（自带 commit）在前、`rearm_intel()` 在后；G-20 端到端覆盖 |
| R-9 | 用户在**暂停**的分组上改设置，重装把采集偷偷放开 | 同一实例 ⇒ `paused` 天然保留；G-13 冻结 |
| R-10 | 手动【重新装配】被当成一个可以反复点的重试按钮，撞第三方限流 | 重装不发任何网络请求（`_build_deps` 只造对象）；真正的检索仍受既有熔断与日配额约束 |
| R-11 | `arm_failed` 的 `detail` 里混进密钥 | `IntelSearchError` 构造时即脱敏（既有纪律）；`detail` 另外截断到 200 字符，并在 G-23 里断言不含 `apiKey` 字样 |
| R-12 | 新配置项漏登记 `.env.example` | 既有双向对齐用例会判红（这正是它存在的理由） |

---

## 九、实施顺序（10 步，不得压缩）

0. **`degrade_wire` 从 `service.py` 纯搬运到 `taxonomy.py`**，`service.py` 原样重新导出
   → 跑 `test_ai_intel_taxonomy.py` + `test_ai_intel_service.py`，**确认零调用点改动**。
   （评审 P0-2：这一步必须排在最前——`arming.py` 一旦写出来就需要它已经在 `taxonomy` 里，
   否则会诱导实现者写出那条成环的 import。）
1. `search.classify_provider` + `resolve_search_provider` 改写 → 跑 `test_ai_intel_search*.py`
   （**先绿这一步再往下**：它是整条链的判据源头）。
2. `taxonomy.ARM_REASONS` + `DEGRADE_TEXT` 6 条 → 跑 `test_ai_intel_taxonomy.py`。
3. 新建 `intel/arming.py` + `test_ai_intel_arming.py`（G-1…G-7、G-6b、G-9、G-25b、G-28、G-29）。
   **G-28 必须在这一步就绿**：它是「后端还起不起得来」的闸，往后每一步都建立在它之上。
4. `service.read_summary(..., arm=)` + `build_snapshot(..., arm=None)` → 跑
   `test_ai_intel_service.py`（`:612` 随之改，`:404` 不改）。
5. `runtime.py`：`_arm` / `summary_for` 打戳 / `refresh` 的 E-18 修复 / `_static_summary` 清理
   → 跑 `test_ai_intel_scheduler.py` + `test_ai_intel_wiring.py`。
6. `runtime.py`：`GroupIdentity` / `identity_of` / `_build_deps` 抽出，**`build_group_runtime`
   的三条分支（disabled 壳 / no-provider 壳 / 实装）全部改调它，签名不变**；两个壳分支从此
   也带齐分组身份三字段（评审 P0-1）→ 再跑一次 `test_ai_intel_wiring.py`，**确认三条既有用例
   一个字节没改就绿**，并补 G-27。
7. `runtime.rearm` / `rearm_group` / `_sync_scheduler` / `_await_round_settled` / `retire` /
   `_ARM_ERRORS` / `arm_result_for` + `test_ai_intel_rearm.py`（G-10…G-19、G-30、G-32）。
8. `manager.rearm_intel` 薄转发 **+ `not_running` 回落走 `_wire_intel`**（评审 P0-3）+
   `_wire_intel` 的 error 记录；`api/ai_intel.py` 的 `_summary`、`_is_group_member`、
   **`_require_runtime` 改 `async` 并同步改四个调用点**（评审 P0-4）；`api/app_settings.py` 的
   `arm` 块 + PUT 接线 + 新端点；`schemas.IntelArmOut`；`config.py` + `.env.example` +
   `desktop/scripts/config-passthrough.test.mjs` 的哨兵键 → 跑 `test_ai_intel_api.py`
   （**含 §7.2 方框里那三条既有断言的更新**）+ 新建 `test_ai_intel_settings_rearm.py`
   （G-20…G-23、G-25、G-26、G-31）。
9. 前端：`intelTypes.ts` → `tsc` 判红出全部 fixture → 逐个补 → `intel.ts` / `intelApi.ts` /
   `IntelEmptyStates` / `IntelList` / `IntelModal` / `IntelImpactTab` /
   `IntelArmStatusCard` / `IntelSourceSettings` → `vitest` + `tsc`。
10. 全量回归（后端 pytest 全量、前端 vitest + tsc、桌面 node --test、`ruff F,E9`）+ README 一段。

---

## 十、非目标（本轮明确不做）

1. **不做多检索源扇出**：仍是单活，选一个。
2. **不把装配状态落库**：§5.1 已论证。
3. **不改 `INTEL_SETTING_BRIDGE`**：这一次不是漏登记（E-11 已核实写入通路完好）。
4. **不给交易 Agent 的 prompt 加任何装配状态说明**（§4.3）。
5. **不改 `no_provider` 这个旧码的名字或文案**（破坏性变更）。
6. **不做「设置改动自动重启整个分组」**：那会平掉真实仓位，代价与问题完全不成比例。
7. **不给 `base_url` / `timeout_s` / `max_retries` 加面板入口**：它们仍是 env 级旋钮。
8. **不做重装的历史台账**：一次重装不是一个需要事后审计的交易事件；失败原因保留最近一次即可。

---

## 十一、配置项清单

**新增 1 项**（`ai_trade_intel_*` 家族 82 → 83）：

```python
# 设置保存之后等待「当前这一轮情报跑完」的硬死线。超时即把重装转入后台并如实回执
# `pending=true`——**绝不**让一个设置保存请求陪着一轮 60 秒的情报轮一起等。
# 启动期校验器钉死它 < 10.0（客户端 FAST 档是 20 秒，留一半裕量给往返与写库）。
ai_trade_intel_rearm_deadline_s: float = Field(default=2.5, ge=0.2, lt=10.0)
# ⚠️ 评审 P2-8：v1 写的是 `le=10.0`，与下面那条「>= 10.0 即抛」的校验器在 `10.0` 这一点上
# 互相矛盾（Field 放行、校验器拒绝）。收成 `lt` 之后两层一致；校验器**仍然保留**——它守的是
# 「将来有人放宽 Field 上界」这条路，且它的报错信息说得出**为什么**是 10 秒（Field 说不出）。
```

`.env.example` 追加：

```
# 情报设置保存后，等待当前这一轮情报跑完的死线（秒）。超时即转后台重装，界面会显示
# 「设置已保存，当前这一轮情报跑完后自动生效」。
AI_TRADE_INTEL_REARM_DEADLINE_S=2.5
```

启动期校验器（镜像既有 `ai_trade_intel_drain_timeout_s` 那条的写法）：

```python
@model_validator(mode="after")
def _validate_intel_rearm_deadline(self):
    """重装死线必须远小于客户端 FAST 档（20 秒），否则「保存」按钮会假超时。"""
    if self.ai_trade_intel_rearm_deadline_s >= 10.0:
        raise ValueError(
            f"AI_TRADE_INTEL_REARM_DEADLINE_S({self.ai_trade_intel_rearm_deadline_s})"
            " 必须 < 10.0：客户端 REST 超时档为 20 秒，留一半裕量给往返与写库。")
    return self
```

---

## 十二、规模与阈值登记（CLAUDE.md 合规）

| 文件 | 现行行数 | 预估增量 | 预估终值 | 是否越 800 |
|---|---|---|---|---|
| `intel/arming.py` | — | +150 | 150 | 否 |
| `intel/search.py` | 538 | +20 | 558 | 否 |
| `intel/taxonomy.py` | 519 | +30 | 549 | 否（+5 是搬进来的 `degrade_wire`，评审 P0-2） |
| `intel/service.py` | 766 | +4 | 770 | 否（搬出 `degrade_wire` 抵掉 4 行，**比 v1 更宽裕**） |
| `intel/runtime.py` | 455 | +185 | 640 | 否（+35 来自 `GroupIdentity` / `_await_round_settled` / `arm_result_for` / 三个 property） |
| `intel/scheduler.py` | 153 | +6 | 159 | 否 |
| `api/ai_intel.py` | 618 | +38 | 656 | 否（`_require_runtime` 转 `async` + `_is_group_member`） |
| `api/app_settings.py` | 268 | +55 | 323 | 否 |
| `ai_auto/manager.py` | 2058 | +34 | 2092 | 既有超限，只加薄转发 + 一条 `not_running` 回落（评审 P0-3） |
| `app/schemas.py` | 2903 | +20 | 2923 | 既有超限，只加一个 Out 模型 |
| `app/config.py` | 1384 | +12 | 1396 | 既有超限 |
| `components/Settings/IntelSourceSettings.tsx` | 519 | +6 | 525 | 否 |
| `components/Settings/IntelArmStatusCard.tsx` | — | +95 | 95 | 否 |
| `components/Intel/IntelEmptyStates.tsx` | 121 | +45 | 166 | 否 |
| `components/Intel/IntelList.tsx` | 139 | +12 | 151 | 否 |
| `lib/intelTypes.ts` | 551 | +30 | 581 | 否 |

> ⚠️ **`intel/service.py` 已是 766 行，只剩 34 行裕量。** 故本轮的所有新逻辑一律进
> `arming.py`，`service.py` 只做两件事：改 `read_summary` / `build_snapshot` 的一个形参，
> 以及把 `degrade_wire` 单向搬去 `taxonomy.py` 后原样重新导出
> （`from .taxonomy import degrade_wire  # noqa: F401`，照抄它自己第 756-766 行
> `settings_bridge` 那次的写法）——**净结果是 766 → 770，比 v1 的 774 更宽裕**。
>
> 评审 P2-2 更正：v1 写的「加进 `__all__`」不成立——`service.py` 实读**没有 `__all__`**
> （`grep -n "__all__"` 零命中），它的重新导出全靠 `# noqa: E402,F401`。
>
> 评审 P0-2 更正：v1 要 `service.py` `from .arming import ArmState, arm_wire, classify_config,
> resolve_arm`——**那条 import 正是环的另一半**，本轮取消。需要 `arming` 的模块一律
> `from . import arming` 并在调用点写 `arming.xxx`（`runtime.py` / `api/ai_intel.py` /
> `api/app_settings.py` 三处）。
>
> 如果实施中 `service.py` 仍然越线，处置是**按 C-9 先例再下沉一层**，不是放宽阈值。

- 新增函数全部 ≤ 5 个形参（评审 P1-1 更正了 `_build_deps`，v1 登记的 4 与它自己的调用点
  对不上）：`classify_provider(2)` / `classify_config(1)` / `arm_of_code(1)` / `arm_of_deps(1)` /
  `resolve_arm(3)` / `arm_wire(1)` / `arm_settings_wire(3)` / `settings_fingerprint(2)` /
  `identity_of(1)` / `IntelRuntime.rearm(4 + self)` / `rearm_group(5)` /
  `_build_deps(5：settings / api_key / provider_cfg / make_client / ident)` /
  `_sync_scheduler(1)` / `_await_round_settled(2)` / `manager.rearm_intel(1 + self)` /
  `api._require_runtime(3)`。
- 新增函数全部 ≤ 50 行；最长的是 `rearm_group`（约 42 行）。`_build_deps` 因为要覆盖三条分支
  （disabled / no-provider / 实装）约 35 行，仍在阈值内；若实施中越线，按 §3.2.3 的注释把
  「造 LLM client」那段抽成 `_build_llm(...)`。
- 嵌套深度 ≤ 3；`resolve_arm` 用 guard clause 阶梯，不写 `else` 阶梯。

---

## 十三、留给评审的待决问题（**已逐条裁决**）

1. **`PUT /settings/intel` 该不该无条件触发重装？** 本文选「是」（指纹短路兜底）。反方意见：
   只在 `provider` / `apiKey` / `enabled` 三键变化时重装，其余旋钮等下一轮自然生效。但那会
   让「改了采集间隔，正在跑的分组永远不生效」这条**同源**缺陷留在原地，且判据要手工维护一份
   「哪些键需要重装」的名单——那正是本仓库反复出现的「第二份手抄真值表」。

   > **评审裁决：维持「是」。** 理由不止于「少一张手抄表」——那张名单还会**随时间腐坏**：
   > 每加一个 `INTEL_SETTING_BRIDGE` 键，就多一次「忘了往名单里加一行」的机会，而症状是
   > 「面板上改得动、系统永远不读」这条本仓库前科三次的假开关。指纹这条路把判据变成
   > 「桥表的全体字段值」，**新增键自动进指纹**，物理上不存在漏登记。
   > 附三条兑现条件：(a) 指纹必须与配置**同锁同刻**赋值（P1-9 / G-30）；(b) `changed=False`
   > 的短路必须真的连锁都不碰（G-18 用替身锁计数断言）；(c) 指纹里绝不放明文 key（§5.2）。

2. **`arm_pending` 该不该也画进标的卡片上的情报按钮（染色）？** 本文选「不」（它是弱提示，
   进了 `WEAK_DEGRADE`）。理由：pending 最多持续一轮，把按钮染黄会让告警色贬值。

   > **评审裁决：维持「不」，并补一条算术依据。** 一轮情报的上限由
   > `search_timeout_s`（≤60）+ LLM 一次调用构成，典型 20–60 秒；而 `WEAK_DEGRADE` 里既有的
   > `search_empty` 是**每轮都可能出现**的常态码。`arm_pending` 的出现频率远低于它，却持续更
   > 短——按同一把尺子，它比 `search_empty` 更没有资格染色。
   > `not_running` 同样进 `WEAK_DEGRADE`（v1 §3.4.2 已列），理由更硬：**它在自动交易没开的
   > 整段时间里恒成立**，染色等于把警示色常亮。

3. **`POST /settings/intel/rearm` 要不要设 `_require_ai_enabled`？** 本文选「不」，与两个既有
   `/settings/intel*` 端点保持一致；它不动任何资金，且在总闸关着时用户仍需要看到状态。

   > **评审裁决：维持「不」。** 补一条 v1 没说的理由：`_require_ai_enabled` 守的是
   > `ai_trade_enabled`（AI 自动交易总闸），而本端点在**那把闸关着的时候**同样要能回答
   > 「我这份检索源配置到底装没装上」——这正是 `not_running` 这个码存在的意义。加上它，
   > 用户在最需要诊断的处境下反而拿不到诊断。
   > 但**必须**在 §4.1 显式登记它走既有 `OriginGuardMiddleware`（v1 §3.3.3 已写，保留）。

4. **`not_running` 的文案是否需要区分「自动交易没在跑」与「这个标的不在分组里」？** 本文选
   「不区分」（一句「该标的的情报不会自动采集」两种情况都成立），以免为一个罕见分支多造一个
   码。若评审认为用户会困惑，可拆成第 8 个码。

   > **评审裁决：维持「不区分」，且这一条现在有了实现层面的支撑。** §3.1.5 定稿的
   > `_is_group_member` 读 `group_status()`，而它在无活跃分组时回 `None`
   > （`manager.py:1620-1622`）——两种处境在那一行天然合流成同一个 `False`。要拆成第 8 个码，
   > 就得让 `_is_group_member` 回一个三态而不是布尔，再让 `resolve_arm` 多一条分支、
   > `DEGRADE_TEXT` 多一条文案、前端多一个分叉——为一个用户根本分不清的区别付四处代价。
   > 驳回。

### 评审新增的第 5 个问题（已自行裁决，留档）

5. **`arm_settings_wire` 的 `armedMembers` 在成员很多时会不会变成一次 N 查？** 不会：
   它只读 `_BY_INST` 这个进程内字典（`runtime_for` 是一次 `dict.get`），零 DB、零网络。
   但**必须写明成员上限的来源**是 `group_status()["members"]` 而不是遍历 `_BY_INST`——后者会
   把上一个分组的残留成员算进来（`unregister` 按身份比对删，正常路径不残留，但 fail-open
   路径下 `_BY_INST` 与 `group_status()` 可能短暂不一致）。**以 `group_status()` 为准**。

---

## 附：本轮与前四轮的关系速查

| 轮次 | 提交 | 它解决的问题 | 它留下的天花板 |
|---|---|---|---|
| 1 | `52f466e` | 情报能被采到、分层、持久化 | 采到的情报无法被引用、结算、证伪 |
| 2 | `7e2d5be` | 可引用 / 可结算 / 可证伪 / 可拒绝 | 全链路唯一的「事实」只有搜索 snippet |
| 3 | `4b9c352` | 每条情报都能打开它的原文引证 | 情报存好之后，什么时候真正改变一次决策不可知 |
| 4 | `d17eed6` | 高危情报当场唤醒一次决策 + 战绩 + 去噪 | **整条流水线的「装没装上」不可知、不可改** |
| **5（本轮）** | — | 装配状态真值化 + 设置热重装 | （待下一轮） |

前四轮把情报做得越来越可信、越来越可行动——而它们共同建立在一个**从未被检查过的前提**上：
「这条流水线是装上的」。本轮就是去检查那个前提，并让它在装不上的时候说出为什么、以及怎么办。

---

## 评审结论

### 结论：**有条件通过**

方案的**问题定位是对的，而且是本轮最有价值的部分**。用户报的是一句文案，v1 却把它一路追到了
两层后端事实上：`providerConfigured` 从来就不是「配没配」（`api/ai_intel.py:110` + `:89` 的
`_RUNTIME_ONLY_KEYS` 让运行时缓存的 `False` 恒覆盖库值），以及情报层整份 settings 在
`start_group` 那一刻被冻结、此后永不重读（`build_group_runtime` 全仓只有 `manager.py:1479`
一个生产调用点）。18 条取证复测 17 条成立、16 个行数登记零漂移——这份底子扎实。
四条轴的切分（真值化 / 热重装 / 接线 / 前端分叉）也是对的：把「配没配」「开没开」「装没装上」
拆成三个各自可回答、各自有出路的事实，比任何一次文案修补都更接近问题本身。

**但四条 P0 里有三条是同一种形状——「新通路绕过了它自己声称的约束」**，这与上一轮终审记下的
形状完全一致，故必须在动键盘之前改毕：

- **P0-1** 立了「`prev` 只贡献分组身份」的规矩，而**壳恰恰是没有身份的那一个**
  （`runtime.py:416-418` / `:429-431` 三个字段全取默认值）。于是本轮头号场景——「开跑时没配源、
  跑起来才去设置里配」——修好之后采到的每一条情报都挂空 `groupId`、上下文用写死的 15m，
  **而且不报错**。
- **P0-3** 为 fail-open 造了自救入口，而自救入口的第一行就把 fail-open 造成的那种处境
  （`_RUNTIMES` 为空）判成 `not_running`——**对一个正在跑的分组说「AI 自动交易未在运行」**。
- **P0-4** 立了「arm 四键只有一个写点」，却给 `_require_runtime` 配了第二个判据
  `arm_for(inst_id)`——而它是同步的、没有会话，在最需要说实话的支路上只能猜。
- **P0-2** 是另一种：`arming.py` 那行 `from . import service`（注释写着「延迟导入见下」，
  而正文从未给出那个延迟导入）与 `service.py` 的反向 import 成环，**先解析 `arming` 的入口
  会让整个后端起不来**。

十条 P1 中权重最高的三条：`arm_wire` 覆写 `degrade` 会打翻三条 v1 完全没登记的既有断言
（R-4 只核对了 `providerConfigured` 一个键）；G-25 的等价命题**不可满足**（左边含总闸、
右边不看总闸）；`_static_summary` 上那三个「承诺恒在位」的键**在 v1 的写点表里根本没有人写**。

以上 **4 条 P0 与 10 条 P1 已在正文全部改毕**（改法逐条写进《评审记录》的「修法落点」列，
并在正文对应处标注了「评审 Px-y」的出处）；11 条 P2 一并改毕。版本已标 **v2**。

### 放行条件（C-1 … C-9）

进入实施节点之前，以下 9 条必须逐条兑现；任何一条落空，本轮的原命题**修不好或修出新洞**。

| # | 条件 | 对应 |
|---|---|---|
| **C-1** | **§九 第 0 步不得跳过**：先把 `degrade_wire` 搬进 `taxonomy.py` 并确认零调用点改动，再写 `arming.py`。G-28 必须在第 3 步就绿——它是「后端还起不起得来」的闸。 | P0-2 |
| **C-2** | `_build_deps` 是 `IntelDeps` 的**唯一**构造点，`ident: GroupIdentity` 是显式必填形参；`build_group_runtime` 的**三条**分支（disabled 壳 / no-provider 壳 / 实装）全部改调它。G-27 用一个 `bar="4H"` 的壳做正面验收。 | P0-1 |
| **C-3** | `manager.rearm_intel` 的 `not_running` 回落必须真的走一次 `_wire_intel`，并由 G-26 用「`build_group_runtime` 抛异常 ⇒ `_RUNTIMES` 为空 ⇒ 点重新装配 ⇒ 真的装上」这条端到端断言冻结。 | P0-3 |
| **C-4** | 全仓**只保留一份 arm 判据**：删掉 `arm_for`，`_require_runtime` 改 `async` 并与 `_summary` 共用 `resolve_arm`；四个调用点（`:392` / `:411` / `:504` / `:526`）一次改完。 | P0-4 |
| **C-5** | arm 四个事实的两份 wire 都由 `arming` 装配（`arm_wire` / `arm_settings_wire`），路由层**一行都不手拼**；G-29 断言两块四个布尔对同一个 `ArmState` 恒同值。 | P1-7 |
| **C-6** | `arm_pending` 是 `ARM_REASONS` 里的一个**普通 reason**，不是与 `_arm` 并列的第二个字段；`arm_wire` 没有 `pending=` 形参。G-31 断言 pending 期间 `armReason` 与 `degrade.code` 同值。 | P1-8 |
| **C-7** | 指纹与配置**同锁同刻**赋值（`rearm` 的第四个形参），`rearm_group` 不再直接写它；G-30 用交错序 + 「改回 A 必须还能装上」两段断言冻结。 | P1-9 |
| **C-8** | §7.2 方框里那**三条既有断言的新期望值**（`test_ai_intel_scheduler.py:86` / `test_ai_intel_api.py:245` / `:198`）必须按登记更新，**不得**反向去改 `arm_wire` 让它继续回粗粒度的 `no_provider`——那等于把本轮做个空。 | P1-3 |
| **C-9** | G-8 的 key **只写进 `app_settings`、env 留空**。那是打包形态下唯一的存 key 途径，也是用户报告的那一种处境；把 key 塞进 env 的用例会让这条核心闸恒绿而什么都没验到。同理，`resolve_arm` / `arm_of_deps` 收到的必须是 `resolve_intel_settings` 的**派生副本**，绝不是进程级 `Settings`。 | 补充取证 |

### 未改动、且刻意保留的三处

1. **`no_provider` 这个旧码不改名、不改文案**（§十 非目标 5）。它仍表达「轮跑起来了但
   `deps.search` 是 None」这一种**轮内**降级（`service.py:251` 等四处），与本轮的装配码不同命。
   `_require_runtime` 的兜底码也用它（而不是 v1 写的 `not_armed`——那个码不在 `ARM_REASONS` 里，
   会让 `degrade_message` 回落成一句裸码）。
2. **决策 prompt 一个字节不改**（§4.3）。装配状态是面板事实，写进一次真实资金决策的 prompt 里
   只会占预算、不会改善判断。评审复核了 `build_decision_prompt` / `prompt_sections.py` /
   `intel/prompts.py` 三者均不在文件变更清单里，**成立**。
3. **零 DB schema 变更**（§5.1）。装配状态 = f(库里的 `app_settings` 行, 进程里的运行时)，
   两个自变量都已有落点；落库只会造出第三份可能与前两份不一致的副本。评审同意，且注意到
   这让本轮成为该系列**第二次**做到 `db/session.py` 一字节不改。

### 一句话总评

**这一轮修的是「系统对用户说了一句关于用户自己配置的假话」，而 v1 的四条 P0 恰好都是同一句
假话的新载体——换了个地方继续说。** 改毕之后，`providerConfigured` 第一次真的只回答
「库里配没配」，`armed` 第一次真的只回答「此刻在不在跑」，而当它回 `false` 的时候，用户拿到的
是一个**能点的按钮**而不是一句让他去反复确认自己没配错的话。放行。

---

## 实施过程发现的方案缺陷

> 实施节点（第 2 轮 · 节点 #2）在按 §九 的 11 步落地时发现的方案缺口。每一条都**已按修正后的
> 做法实施**，未静默偏离。编号 I-1…I-9。

### I-1（P0 · 方案自相矛盾）`resolve_arm` 读不到它自己指定的那张表

§3.1.2 的 `resolve_arm` 第 ⑤ 条判据要求 `detail` 取「`runtime.arm_error_for(...)` 记下的最近一次
原因」，而 §3.5 / §6.1 把 `_ARM_ERRORS` 与三个读写函数放在 `intel/runtime.py`。**这两条不能同时
成立**：`runtime.py` 必须 `from . import arming`（它要 `arm_of_deps` / `arm_wire` / `ArmState`），
故 `arming` 依评审 P0-2 的纪律不许反向 import `runtime`——在 `arming` 里根本够不着那张表。函数内
延迟导入是评审自己否决过的做法（「那只是把环藏起来」）。

**修法**：`_ARM_ERRORS` 连同 `record_arm_error` / `clear_arm_error` / `arm_error_for` 一起住进
`arming.py`——它本就是这条判据的家；`runtime.py` **原样重新导出**三个名字
（`from .arming import ... # noqa: F401`），故 §6.1 登记的 `intel_runtime.record_arm_error(...)`
与 manager 的调用点**一个字节不改**。照抄 `degrade_wire` 这一轮、以及 `settings_bridge` 上一轮的
同一条「单向搬家 + 原样重新导出」先例。

另补一条方案没写的细节：`resolve_arm` 的 `arm_failed` 支路的前提正是「一个 runtime 都没有」，
故它**拿不到 group_id**。`arm_error_for("")` 因此回落到「最近记下的那一条」——manager 同一时刻
只持一个活跃分组（`self._group`），两者等价，并在 docstring 里写明了这个前提。

### I-2（P1）`__init__` 那三行首帧兜底只有两行是死码

§3.1.5 末尾要求把 `runtime.py:96-98` 那三行「`if self.enabled: seed[...] = True`」整段删掉，理由是
「出口打戳之后它是纯粹的死码」。**`providerConfigured` 那一行确实是死码，`degrade` 那一行不是**：
`arm_wire` 在 `armed=True` 时**刻意不写** `degrade`（那个键要留给一次真实的轮内降级，§3.1.5 自己
定的「轮内降级 > 未装配 > 无」优先级正依赖这一点），于是一个装配成功的运行时会沿用未装配 seed 里
那个 `no_provider` 的 `degrade`，弹窗在首帧显示「尚未配置情报检索源」并给出一个用户根本不需要点的
设置入口——正是这三行当初存在的理由。

**修法**：删掉 `providerConfigured` 那一行，保留 `degrade` 的兜底并把判据从 `self.enabled` 换成
`self._arm.armed`（同一件事的单一真源）。同理，`_static_summary` 里 `"degrade": service.degrade_wire(code)`
改成 `"degrade": None` 这个**占位**而不是整行删掉——与 `service.read_summary` 逐字同形，占位保证
这个键**恒在位**（前端读到 `undefined` 会把「没装上」画成「一切正常」，历轮四次）。

### I-3（P1）`_await_round_settled` 等不到它要等的那件事

§3.2.2 写明 `_await_round_settled`「只轮询 `runtime._in_flight` 是否清空」，而 P1-10 的整段论证是
「`_after_round` 跑在锁外，`scheduler.cancel` 会把这一轮的快照刷新与 `_alert` 告警交接静默丢弃」。
两者对不上：`_in_flight.discard` 在 `_run` 的 `finally` 里、也就是**锁内**，故 `_after_round` 开始
跑的时候 `_in_flight` **已经空了**。按方案实现，那次等待会立刻返回，随后照旧硬 cancel——P1-10 一个
字都没修好，而且用例会绿。

**修法**：`IntelRuntime` 新增私有计数器 `_tail`（`_after_round` 前后各一次增减）与只读 property
`busy`（`_in_flight` **或** `_tail > 0`）；`_await_round_settled` 轮询 `runtime.busy`。G-32 的第一条
用例直接断言「`_in_flight` 已空而 `busy` 仍为真」，把这个区别钉死。

### I-4（P0 · ★ **本轮用户报告的真正根因**，方案与评审都没找到它）

`manager._wire_intel` 把 **`get_sessionmaker()` 的返回值**（一个 `async_sessionmaker` 实例）当作
`sessionmaker` 传给 `build_group_runtime`（`manager.py:1482`），而 `build_group_runtime` 与全仓
每一处 `deps.sessionmaker()()` 都按**两跳**来写（`IntelDeps.sessionmaker: Callable[[], object]`，
即「先取 sessionmaker、再开会话」）。于是第二跳变成 `AsyncSession()` ⇒
`TypeError: 'AsyncSession' object is not callable`。

而 `_wire_intel` 全程 fail-open：异常被 `except` 吞成一行 warning，**`intel_runtime.register` 从来
没有被调到**，`_RUNTIMES` 恒空。因此——

- `providerConfigured = runtime_for(inst) is not None`（E-4）**恒为 `False`**；
- 面板恒显示「尚未配置情报检索源」，**无论用户在设置里配得多齐**；
- 情报层从 `52f466e` 起**一轮都没有真的跑过**（`git log -S` 复核：这一行自该提交起未变）。

这正是本轮用户原话描述的那一种处境，而它比 §〇 判定的两层原因**更靠前**：轴 A 修好了「假话」的
表达，轴 B / 轴 C 修好了「设置改不动」，但如果不修这一行，`rearm_group` 仍然会在
`_RUNTIMES.get(group_id) is None` 上回 `not_running`，整轮的原命题依旧不成立。

它此前对所有门禁**完全不可见**：`test_ai_intel_wiring.py` 的三条既有用例全部
`monkeypatch.setattr(intel_runtime, "build_group_runtime", fake_build)`，真实装配路径**一次都没有被
走过**。本轮 G-26 用真实的 `_wire_intel` 做端到端断言，第一次跑就把它顶了出来。

**修法**：`sessionmaker=get_sessionmaker`（传函数本身）。另加两条冻结用例：
`test_wire_intel_passes_the_sessionmaker_factory_not_a_session`（断言身份 **并且**真的把两跳走通
——只断言身份不够，那正是这个缺陷活过四轮的原因）与 §7.2 已登记的 G-26。

### I-5（P2）`degradeAction` 的三分叉会顺手拿走一个正常工作的重试入口

§3.4.2 把 banner 按钮描述成「去设置 / 重新装配 / 无按钮」三分叉，而既有实现对
`no_provider`/`disabled` 之外的**全部**码给的是【立即重试】——`search_failed` / `circuit_open` /
`budget_exhausted` 这些**轮内**降级码正靠它。严格按三分叉实现，等于把这些码的重试按钮一并删掉。

**修法**：`degradeAction` 回 `"settings" | "rearm" | "retry" | `"none"`` 四态，`"none"` **只**留给
`not_running` / `arm_pending` 这两个「给了也没用」的码，其余保持 `"retry"`。这是方案三分叉的严格
超集，既有行为逐字节保住。

### I-6（P2）`_intel_wire` 的 `arm` 块缺一个「这个 `ArmState` 从哪来」

§3.3.4 只给了 `arm_settings_wire(arm, ...)` 的调用形态，没说 `arm` 怎么解——而 `resolve_arm` 的
入参里有一个 `runtime`，`_intel_wire` 却不是按标的来的。

**修法**：新增私有 `_intel_arm_wire(derived, member_ids, *, group_running)`：分组内每个成员共用
**同一个** `IntelRuntime`（`_BY_INST` 的每个键都指向它），故取第一个挂得上的成员即可；一个都挂不上
时 `runtime=None`，判据回落到库里的配置本身——那正是 `_wire_intel` fail-open 之后的处境。
`armedMembers` 按 §十三 第 5 问的裁决取 `group_status()["members"]`，不遍历 `_BY_INST`。

### I-7（P2）`resolve_arm` 的 pending 判据要排在 enabled **之前**

§3.1.2 的阶梯把 pending 写成「② runtime 在、enabled、但 pending」。但 `mark_arm_pending` 刻意
**不动 `enabled`**（G-14 断言了这一点），故一个**尚未装上的壳**也可能挂着待应用的重装；按方案的
顺序，那种情况会掉到第 ③ 条上说「你没配源」——又一句关于用户自己配置的假话。

**修法**：把 pending 判据提到最前（`runtime is not None and runtime.arm_pending`），其余阶梯不变。
对 §3.1.2 描述的 enabled 场景两者等价，对壳场景严格更诚实。`test_ai_intel_arming.py` 的
`test_resolve_arm_ladder` 覆盖了这条支路。

### I-8（P2 · 跑测纪律）新增的两个测试文件必须清 `app_settings`

`services/quant/tests` **共用同一个临时 DB**（§7.3 第 1 条）。`test_ai_intel_settings_rearm.py` 会往
`app_settings` 写 provider / key 行，留着会让按 env 值装配的既有用例
（`test_ai_intel_wiring.py::test_verify_client_assembly_failure_falls_back_instead_of_killing_the_group`）
读到 `provider="none"` 而判红——症状离原因很远（表现为「核查预算回落把整组降成了壳」）。
两个新文件的 autouse 夹具因此在 teardown 里 `delete(AppSetting)`。

同一条夹具还必须是 **`async`**：同步夹具跑在事件循环关闭**之后**，`task.cancel()` 会抛
`RuntimeError: Event loop is closed`，于是它下面那几行注册表清理**一行都不执行**——上一个用例起的
调度循环就这样活到了下一个用例里。

### I-9（P1）缓存里的 `degrade` 会过期，无条件让它赢会造出同一句自相矛盾的 wire

§3.1.5 把 `degrade` 留在 `_RUNTIME_ONLY_KEYS` 里并论证「未装配时缓存里的 `degrade` 本来就已经是
arm 码，两条通路同值」。**那个前提只在缓存没过期时成立**：缓存里那一份是 `summary_for` 用
**装配期**的 `_arm` 打的戳。一个开跑时没配源的壳，`_arm` 恒是 `provider_unset`；而此刻库里可能
早已配齐、只是重装失败了（`arm_failed`）。于是同一份 wire 上
`armReason="arm_failed"` 而 `degrade.code="provider_unset"`——**两个键说两句相反的话**，正是
P1-8 / G-31 花整节消灭的那种形状，只是换了一个触发路径。

**修法**：`degrade` 只在 `arm.armed` 时从缓存优先。理由是硬的——「轮内降级」这种事**只可能发生在
一个装上了的流水线上**（未装配的运行时一轮都不跑），故 `armed=False` 时缓存里的那一份必然是
装配期的戳，而 `_summary` 手上这一份是**刚刚**从库 + 进程算出来的。`inFlight` 不受影响（它与 arm
无关）。新增用例 `test_a_stale_shell_never_contradicts_the_fresh_arm_reason` 冻结。

### 规模复核（CLAUDE.md 二）

| 文件 | 方案预估 | 实测终值（终审复测） | 是否越 800 |
|---|---|---|---|
| `intel/arming.py` | 150 | 281 | 否（docstring 占大头；判定逻辑本身 < 60 行） |
| `intel/search.py` | 558 | 574 | 否 |
| `intel/taxonomy.py` | 549 | 562 | 否 |
| `intel/service.py` | 770 | 783 | 否（仍留 17 行裕量） |
| `intel/runtime.py` | 640 | 774 | 否（+134 来自 I-1/I-3 的补丁与 `_build_deps` 的取证注释） |
| `intel/scheduler.py` | 159 | 159 | 否 |
| `api/ai_intel.py` | 656 | 654 | 否（工作树上是 661——多出的 7 行属于**入轮前既有**的 204 端点修复，未随本轮提交） |
| `api/app_settings.py` | 323 | 333 | 否 |
| `components/Intel/IntelEmptyStates.tsx` | 166 | 163 | 否 |
| `components/Intel/IntelList.tsx` | 151 | 149 | 否 |
| `components/Settings/IntelArmStatusCard.tsx` | 95 | 75 | 否 |

> `intel/service.py` 只剩 17 行裕量。**下一轮再动它必须先按 C-9 下沉一层**，不是放宽阈值。

---

## 终审记录（第 2 轮 · 节点 #3）

> 终审方式：**不采信实施节点的任何一条自述**。`git diff` 与 §六《文件变更清单》、
> §7.1《G-1…G-32》、《评审结论》的 C-1…C-9、以及《实施过程发现的方案缺陷》I-1…I-9 逐条对照；
> 四道门禁（后端全量 pytest / 前端 vitest + tsc / 桌面 `node --test` / `ruff F,E9`）在本机全部
> 重跑，不引用上游节点的跑测结论。

### 一、覆盖核对（逐条通过）

| 核对项 | 结论 |
|---|---|
| §6.1 后端 新 1 / 改 11 | 到位。`arming.py` 新增；`search` / `taxonomy` / `service` / `runtime` / `scheduler` / `manager` / `api/ai_intel` / `api/app_settings` / `schemas` / `config` / `.env.example` 各按登记改毕，无一处越出清单 |
| §6.2 前端 新 1 / 改 8 | 到位。`IntelArmStatusCard.tsx` 新增，八个改动文件与清单逐一对上 |
| §6.3 桌面 改 1（仅测试文件） | 到位。产品代码零改动，只多一行哨兵键 |
| §6.4 文档 2 | 到位。README 那句「共 82 项」→「83 项」与 `.env.example` 实测 `^AI_TRADE_INTEL_` **83** 条一致 |
| C-1（先搬 `degrade_wire`，G-28 守「后端起不起得来」） | 通过。`taxonomy.degrade_wire` 是唯一定义、`service` 原样重新导出；`arming` 只依赖 `search` / `taxonomy` / `settings_bridge` 三个叶子，G-28 用**子进程**只 import `arming` 正面验收 |
| C-2（`_build_deps` 是 `IntelDeps` 唯一构造点、`ident` 必填） | 通过。三条分支全部走它；G-27 用 `bar="4H"` 的壳验收身份不丢 |
| C-3（`not_running` 回落真的走一次 `_wire_intel`） | 通过。`manager.rearm_intel` 回落 + G-26 端到端（`build_group_runtime` 抛 ⇒ `_RUNTIMES` 空 ⇒ 点重装 ⇒ 真的装上）冻结 |
| C-4（全仓只留一份 arm 判据） | 通过。无 `arm_for`；`_require_runtime` 已改 `async` 并与 `_summary` 共用 `resolve_arm`，四个调用点各加 `await` |
| C-5（两张 wire 都由 `arming` 装配） | 通过。路由层无手拼，`_arm_facts` 是两张 wire 唯一的投影，G-29 冻结 |
| C-6（`arm_pending` 只是普通 reason） | 通过。`arm_wire` 无 `pending=` 形参；`IntelRuntime.arm_pending` 退化成 `_arm` 的投影，G-31 冻结 |
| C-7（指纹与配置同锁同刻） | 通过。指纹是 `rearm` 的第四个形参，G-30 的交错序 +「改回 A 必须还能装上」两段断言冻结 |
| C-8（三条既有断言按登记升级） | 通过。`test_ai_intel_scheduler.py` / `test_ai_intel_api.py` 两处 `no_provider`→`provider_unset`、409 码 `intel_no_provider`→`intel_provider_unset`，**没有**反向去改 `arm_wire` |
| C-9（key 只写 `app_settings`、env 留空；判据吃派生副本） | 通过。`test_ai_intel_settings_rearm.py` 全程 `repo.set_app_setting` 写 key；`resolve_arm` / `arm_of_deps` / `classify_config` 的入参一律是 `resolve_intel_settings` 的派生副本 |
| I-1…I-9 | 九条全部按修正后的做法落地，且各自带用例（I-4 另有两条冻结用例：身份断言 + 两跳真的走通） |
| 零 DB schema 变更 / 零决策 prompt 字节变更 | 复核成立。`db/session.py`、`ai_auto/prompts.py`、`prompt_sections.py`、`intel/prompts.py` 一个字节都不在 diff 里 |

一处**登记与落点的偏差**（判定可接受，不返工）：§7.2 把 G-2 登记在 `test_ai_intel_search.py`，
实施把同一组断言落在了 `test_ai_intel_arming.py`（`test_resolve_search_provider_returns_same_as_before`
/ `test_unknown_slug_logs_once_and_key_missing_stays_silent` /
`test_unknown_slug_with_empty_key_now_logs_deliberately` 三条，末一条正是「拼错 slug + 空 key
由静默改为打日志」这个刻意的行为变化）。覆盖不缺，只是文件归属与登记不同。

### 二、终审直接修毕（4 条，全部改在源码里，不留 TODO）

**F-1（P2 · 死形参）`_arm_result` 的第一个形参从头到尾没有被读过。**
`_arm_result(runtime, *, rearmed, pending, arm, changed)` 的函数体只用后四个 keyword，`runtime`
在六个调用点里被逐一传入却一次都没被读——`ruff` 选的 `F` 规则集不查未用**形参**（那是 `ARG`），
所以它对现有门禁完全不可见。一个恒被传入、永不被读的参数会让下一个读者以为回执里某个键是从
运行时算出来的，而实际上六个键全部来自 `arm`。**修法**：删掉该形参，六个调用点一并收敛
（回执的六个键与取值一个字节不变，G-10 / G-16 / G-18 的整字典相等断言原样通过）。

**F-2（P2 · 契约与实现不符）两处 docstring 写着「绝不抛」，而两者都抛得出来。**
`rearm_group` 的 `resolve_intel_settings` / `resolve_api_key` 是两次真实的库读，`manager.rearm_intel`
在 `rearm_group` 之外还有 `_group_settings`；真正兑现「一次设置保存绝不因情报层失败而失败」的是
**唯一**那个 `except`——`api/app_settings._rearm_intel_pipeline`。一句做不到的「绝不抛」比没有这
句话更坏：下一个调用方会照着它省掉自己的 `try`，而症状是一次 500 落在「保存设置」上。**修法**：
把两处 docstring 改成如实的两段——装配失败被收敛成 `reason="arm_failed"` 的回执并记进
`_ARM_ERRORS`；IO 异常原样上抛，由那唯一一个 fail-open 点接住（并写明理由：把 `except Exception`
撒在每一层，真正的失败就再也没有人看得见了）。**刻意不**新增第二个 `except`（CLAUDE.md 五）。

**F-3（P2 · 注释描述了一件没有发生的事）`IntelArmStatusCard` 的 docstring 说文案取
`arm.message` / `arm.detail`，而 `detail` 从未被渲染。**
后端在 `arm_failed` 时已经把 `detail` 的异常文本拼进了 `message`（`DEGRADE_TEXT["arm_failed"]`
的 `{reason}` 占位），再单独渲染一次就是同一件事说两遍。**修法**：改注释、不改渲染，并写明
「不单独渲染 `detail`」这个决定的理由。

**F-4（P1 · 本轮新增的前端单一真源没有直接用例）`degradeAction` 的四分叉一条断言都没有。**
它是实施发现 I-5 的产物：方案原写的是「去设置 / 重新装配 / 无按钮」三分叉，而既有实现对
`search_failed` / `circuit_open` / `budget_exhausted` 这些**轮内**降级码给的是【立即重试】，
严格按三分叉实现等于把一个正常工作的重试入口顺手拿走。I-5 把它改成四态超集是对的，**但这条
偏离没有任何用例冻结**：下一次有人照着 §3.4.2 的三分叉「修正」它，`retry` 那一支会被静默删掉，
症状是用户点不动一个本来能自愈的降级，而且不报错——正是本仓库反复清算的形状。**修法**：在
`apps/web/lib/intel.test.ts` 的「降级」组补两条用例，逐码冻结 `degradeAction` 的四个分支
（含 `degradeAction(null) === "none"`）与两个新装配码在 `isSevereDegrade` 上的分级。

### 三、判定为不改（留档，附理由）

1. **`emptyKind` 把三条装配判据排在 `no-match` / `none-archived` 之前。**
   自动交易停着时（`armReason="not_running"`）搜索无结果，用户看到的是「流水线没在跑」而不是
   「没有匹配结果」。但这不是本轮造成的：`no-provider` 在本轮之前就排第一，而彼时那种处境下
   `providerConfigured` 恰恰恒为 `false`（E-4），于是用户看到的是**那句假话**。本轮把它换成了
   一句真话，排序相对 HEAD 一个位次都没退。改排序属于推翻已评审通过的设计，留给下一轮。
2. **`_is_group_member` 每次 `GET /api/intel/{instId}` 都调一次 `group_status()`。**
   那是一个会拼 `discipline` / `direction` / `tradability` 等多个 wire 的只读视图，比一个布尔重。
   但它是**纯内存**的，且实施发现 I-6 明确选择了「不发明第二个成员口径」（`api/app_settings`
   的成本估算早就是这个用法）。为省一次内存拼装而造第二份成员判据，正是本轮在修的那类分叉。
3. **`rearm_group` 六键回执里只有 `reason` 在生产上有读点。**
   `rearmed` / `pending` / `armed` / `detail` / `changed` 只被用例读。这不是「写点有、读点无」：
   按 I-6 的裁决，面板上那块 `arm` 由 `_intel_wire` 用 `resolve_arm` **重算**（因为它要按标的
   回答，而回执是按分组的），回执的职责只是把 `not_running` 这一个裁决交回 `manager`。保留整张
   表是为了让 `arm_result_for` 与 `rearm_group` 共用同一个构造点，两处各拼一份必漂移。
4. **`_build_deps` 的 `provider is None` 分支会把一条在跑的流水线换成壳。**
   它在 `classify_config` 已判「配齐」之后才可能走到，而 G-25 正是「这两张表不许分叉」的等价性
   闸。真走到了，说明适配器构造本身失败——那时如实说 `arm_failed` 并停下来，比继续假装在采诚实。

### 四、暂存范围（本次提交刻意剔除的在制变更）

`services/quant/app/api/ai_intel.py` 在工作树上是一个**混合文件**：除本轮的 arm-state 改动外，
还带着一处**入轮前就存在、且从未提交**的修复（两个 `status_code=204` 端点 `-> None` → `-> Response`
及其模块 docstring 说明，共 7 行），它属于另一条在制线（配套用例 `tests/test_api_no_body_status_routes.py`
同样未跟踪）。本次提交按行级精确暂存，只收 arm-state 的那 61 增 / 18 删，那 7 行原样留在工作树。
故本文 §规模复核 里 `api/ai_intel.py` 的**提交内**行数是 654（工作树 661）。

同理未纳入本次提交：`desktop/scripts/ensure-wincodesign*.mjs`、`services/quant/app/db/live_auto_repositories.py`、
`services/quant/tests/test_api_no_body_status_routes.py`、`tests/test_factor_library_expansion.py`、
`docs/diagnoses/**`、`.claude-index/`、`.run-log/`、`.bug-diagnosis/`、`tmps/`、`CLAUDE.md`
以及其余未跟踪的 `docs/plans/*` 目录——全部与本轮无关。

### 五、回归（终审在本机全量重跑，不引用上游节点的结论）

| 门禁 | 结果 |
|---|---|
| 后端 `python -u -m pytest tests -q`（全量、不并发） | **4940 passed / 7 failed / 1 skipped / 2 deselected**，18m46s。7 条**全部**来自未跟踪且从未提交的 `tests/test_factor_library_expansion.py`（入轮前即红，与本轮无关），本轮新增 **0** 条红 |
| 前端 `NODE_ENV=test vitest run` | **221 文件 / 2538 用例全绿**（含终审补的 2 条 `degradeAction` / 装配码分级用例） |
| 前端 `tsc --noEmit` | 干净（三个新 summary 键与 `IntelSettings.arm` 刻意不加 `?`，`tsc` 是这里唯一可靠的门禁） |
| 桌面 `node --test scripts/*.test.mjs` | **48/48 绿** |
| `ruff check app tests`（F,E9） | All checks passed |

入轮基线（方案 §7.3 第 6 条登记）是后端 4718 + 7 红；本轮把它推到 4940 + 同样的 7 红。
