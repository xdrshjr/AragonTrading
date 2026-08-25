# AI 交易经验上下文图（ai-trade-experience-context-graph）· 方案设计 **v4**

> 节点 #0（方案设计）产物，**经节点 #1 评审后修订**。本文件是下游节点（#2 开发 / #3 终审）的
> **唯一实施依据**，自包含——实现者不需要读任何历史版本。
>
> **关于版本号**：流水线模板要求「评审后把版本标记为 v2」，指的是**评审后版本**这一语位。本特性的
> 文档序列已经走到 v3，且真正的 v2 已归档为 `spec-v2-archived.md`；照字面回写 v2 会与那份归档件
> 撞名，制造一个比版本号本身更贵的歧义。故**评审后版本记为 v4**（= 模板意义上的「v2」）。
>
> **v4 的存在理由**：节点 #1 不采信本文档 §2.2 的任何取证结论，在 `services/quant/.venv` 里对在制
> 代码做了**可执行的实测**（`hasattr` 探针 / `importlib` 冒烟 / 框架句字符实算 / SQLite 参数上限
> 实测 / 前后端行数实测），共发现 **6 条 P0、9 条 P1、8 条 P2**。其中 **P0-1 使整条特性在今天的
> 代码上 100% 静默空转**，**P0-2 使四条产品能力写而不可读**。全部 P0/P1 已在正文逐条落修（修法
> 编号 **E-1…E-15**），详见 §评审记录。
>
> **v3 的存在理由**：本特性在此前一轮流水线里已经走完「设计 → 评审 → 开发（中途终止）」，
> 工作树里因此留着一批**未提交、未接线、零测试**的在制代码（12 个新模块 + 1 个数据层 + 3 张表
> + 27 项配置 + 3 个只读访问器）。v3 = **对这批在制代码逐文件实读的承接盘** + **审计发现的
> 6 条缺陷的修法** + **完成整条链路所需的全部剩余设计**。历史版本 v2 已归档为
> `spec-v2-archived.md`（只作评审留痕，**不再是实施依据**；两者冲突时以本文件为准）。
>
> 阅读顺序：**§评审记录（v4 增量最大的一节，六条 P0 全在这里）** → §零（速览）→
> §二（现状取证与承接盘）→ §四（技术设计）。

---

## 评审记录（节点 #1 · 设计评审）

> **评审方式**：不采信本文档 §2.2 的任何取证结论。本节的每一条都在 `services/quant/.venv` 里跑过
> 可执行探针（`hasattr` / `importlib` 冒烟 / 字符长度实算 / SQLite 参数上限实测 / `wc -l`），
> 或逐行读过被引用的源码位置；不成立的猜测已被自己删掉（例：曾怀疑 4000 个 id 的 `IN` 子句会撞
> SQLite 参数上限，实测 `SQLITE_LIMIT_VARIABLE_NUMBER = 250000`，**不成立，故不写进本节**）。
>
> **定级沿用 §2.2 的口径**：**P0** = 会损坏真实资金操作路径，**或让特性静默失效**；
> **P1** = 会让产品对用户 / 模型说假话，或让一条写在文档里的承诺静默不兑现；**P2** = 卫生问题。
>
> **结果**：**P0 六条、P1 九条、P2 八条**。P0/P1 十五条已在正文逐条落修（修法编号 **E-1…E-15**），
> P2 八条全部记录在案，其中 4 条同时已落修、4 条转为明确留档。

### 评审总表

| 编号 | 结论 | 落点 | 修法 |
|---|---|---|---|
| **P0-1** | `repo.upsert_lesson_nodes` **没有被再导出**，而它是物化的唯一写路径 ⇒ 每次物化恒抛 `AttributeError`，且被 `_materialize_bounded` 的 `except Exception` 吞成一行 warning ⇒ 图永不建立 ⇒ `load_session_graph` 恒 `None` ⇒ **整条特性 100% 静默空转** | §2.1 / §8.1 | **E-1** |
| **P0-2** | `narrative` / `manual` / `imported` 三类经验**在检索层结构性不可达**：它们没有任何 `applies_in` 边（进不了候选集）且 `weight == 0.0`（`score` 恒 0 被剔除）⇒ 蒸馏、手工新增、导入、**置顶**四条产品能力全部「写得进、读不出」；I-4 承诺的「升 `iron` 只有一条路径——用户显式置顶」**在代码里不存在** | §3.2 / §4.5 / §4.6 / §4.7 | **E-2** |
| **P0-3** | 设置中心的「阈值设置」是**纯写点**：`PUT /settings/experience` 写 `app_settings`，但没有任何读点把它并回会话与物化实际使用的 `Settings`（`RetrievalConfig.from_settings` / `PromoteRule.from_settings` / `MaterializeOptions` 读的都是进程级 `get_settings()` 单例）⇒ 用户把「铁律样本门槛」从 20 改成 10、保存、**永远没有任何效果，且不报错** | §五 / §4.10 | **E-3** |
| **P0-4** | 关停链**死线倒挂**：`drain_experience_tasks()` 内部 `asyncio.wait(timeout=distill_timeout_s + 5)` = **50s**，而 `main._run_shutdown` 的**单步死线是 `shutdown_step_timeout_s = 20.0`**，超时即 `cancel` 该步 ⇒ drain 自己那半段「cancel 剩余任务」的清理代码**永远不会执行**，恰恰在它被设计来应对的那一种情况下 | §4.9 / §6.5 | **E-4** |
| **P0-5** | 终局物化 / 蒸馏只挂在 `_stop_session`，而**桌面端退出的默认动作是 `pause` 不是 `stop`**（`ai_trade_shutdown_action` 默认 `"hold"` ⇒ `pause_group("app_quit")`）⇒ 最常见的退出路径上**终局物化根本不触发**，去抖攒下的脏标志随进程消亡 ⇒「经验随交易不断更新」在日常使用里不兑现 | §4.1 / §4.5 / §4.9 | **E-5** |
| **P0-6** | `asyncio.timeout` **停不掉 `run_in_threadpool` 里已经开跑的 CPU 工作**：20s 超时只解除等待者，线程仍会把整轮全库重建跑完。于是「在飞」标志被提前清掉，60s 后又起一个 ⇒ 大库上变成**每 60 秒叠一个永不完成的全库重建**，吃满 anyio 默认 40 线程池并饿死回测 / 挖掘 ⇒ D-3 这条 P0 修法**自身**成为新的规模墙 | §4.5 / §6.5 | **E-6** |
| **P1-1** | `FRAME_OVERHEAD["l0"] = 70` **小于真实框架句长度**：实算页眉 40 字（大库 46）+ 页脚 38 字 = **78～84**。于是 `l0_max_chars ∈ [194, 201]` **通过**启动期校验却让第一条 120 字铁律装不下 ⇒ **整层一个字都不渲染**——正是 I-6 规则②声称「拒绝启动、不静默降级」要消灭的那一种。且 `PUT /settings/experience` 写的 `l0MaxChars` **完全绕过**该校验器（它只在 `Settings` 构造时跑） | §3.2 / §五 / §6.5 | **E-7** |
| **P1-2** | `harvestGapTotal` **有读点没有写点**：`harvest_gap` 只活在 `ExperienceRuntime` 的内存里，会话一结束就归零，而 §五 的 `GET /overview` 与 §7.1 的概览条都要显示它 ⇒ 设置中心那个数字**恒为 0**。`materializeHealth` 同形：§6.4 说写进 `app_settings`，但 §8.1 的 `service.py` 改动清单里**没有**「把它读出来拼进 `overview()`」这一步 | §五 / §6.4 / §8.1 | **E-8** |
| **P1-3** | **自动静音没有解除路径**：`mark_stale_lessons` 置 `muted=True, mute_reason="stale"`，而 `upsert_lesson_nodes._apply_update` **从不触碰** `muted` / `mute_reason` ⇒ 一条经验只要安静过一轮物化就**永久沉降**，即便后来拿到成百上千条新证据也再不会回到 prompt。§4.7 档位表里「**且未被新证据佐证**」这半句在代码里没有对应物 | §4.5 / §4.7 | **E-9** |
| **P1-4** | `POST /import` 是**无上限的不可信输入**：R-8 列了六条防线，唯独没有条数 / 体积上限，而它是一次性 JSON body（导出是 NDJSON 流，导入不是）；`mode="replace"` 还先逐条 `delete` 再逐条 `create`，**不在一个事务里**，中途失败留下「删了一半」的库 | §五 / §十 | **E-10** |
| **P1-5** | **`lib/httpTimeout.ts` 完全没有被接线**：`/experience/rebuild`、`/export`、`/import` 都会落到 `FAST_MS = 20_000`，而 F-2 自己论证了全库重建在大库上超 20 秒是常态 ⇒ **重建按钮必然假超时**；且 `/experience/*` 不匹配任何 `WRITE_*` 表 ⇒ 一次超时的 `replace` 导入会被显示成「失败」而不是「结果未知」。仓库里 `/ai-trade/prior/rebuild` 就是现成的先例 | §七 / §8.3 | **E-11** |
| **P1-6** | W-7 引用了 `self._on_experience_dirty()`，但**没有任何 W-\* 条目把这个回调装配到 `AiTradeSession` 上**（W-6 只加一个 `experience` 形参）⇒ 实现者必须自己发明一个接口。同时 W-7 的「平仓分支……在 `_inject_explain_meta(trade)` **之后**」是**自相矛盾**的：`_inject_explain_meta` 位于 `if/else` **之外**，close 侧 hook 只能是一个**新的** `if side == "close"` 块 | §4.10 / §8.2 | **E-12** |
| **P1-7** | D-4 把退化匹配收紧为「≥2 个 `seq` 匹配即放弃」之后，**那两个槽永远留在 `_open_slots` 里**——该字典既无容量上限也无淘汰规则，长会话下单调增长。仓库对每一个会话内缓冲都有成文的有界纪律（`_CAPACITY = 200` / `_SESSION_EVENT_CAP = 200` / 提交 `f48a714` 的 capped trade buffers），这里是唯一的例外 | §4.3 / §4.10 | **E-13** |
| **P1-8** | §4.5 写「由 `materialize_lock()` 串行化，**单事务完成**」与源码不符：`rebuild()` 里 `upsert_lesson_nodes` / `mark_stale_lessons` / `replace_edges` **各自 `await session.commit()`**，是**四次独立提交**。于是除 E-6 之外还有一条：取消或异常落在两次提交之间会留下「新节点 + 旧边」的**撕裂图**，而 `match(l) = Σ A(c)·w_applies(l,c)` 对没有边的新节点恒为 0 ⇒ **L0 静默变空** | §4.5 | **E-14** |
| **P1-9** | 「关闭经验模式」会**连采集一起关掉**（`writing` 依赖 `rule.enabled`），与需求「经验是累积的数字资产」相悖，且启动弹窗的文案（一个父复选框 + 一个「写回」子复选框）**看起来像是只关读**。这是一个产品决策而非 bug——但它必须被**明确决定并说出来**，否则用户关一次就永久失去那段时间的收据 | §4.10 / §7.2 / §十一 | **E-15** |
| P2-1 | 行数取证有误：`db/ai_experience_repositories.py` 实测 **452** 行（文档两处写 520）；`db/repositories.py` 实测 **1052**（文档写 1029，那是改动前的值，而同段其余四个数都是改动后的值）。§8.1 的净增预算据此需要重算 | §2.1 / §2.2 / §8.1 | 已落修 |
| P2-2 | §8.1 计划新增的 `count_sessions()` **已经存在**——`ai_experience_repositories.overview()` 内部就有那条 `COUNT(DISTINCT group_id)`，再加一个是重复实现 | §8.1 | 已落修 |
| P2-3 | `GET /experience/graph` 的 `edges` 是 **snake_case**（`src_id` / `dst_id`，`load_edges` 原样透出）而 `nodes` 走 `_node_wire` 是 camelCase——同一个回执里两套命名，`lib/experienceTypes.ts` 只能照抄这份不一致 | §五 | 已落修 |
| P2-4 | §五 把 `enabled`（**运维总闸**）暴露成设置中心的可写字段，与启动弹窗那个复选框会互相矛盾：总闸关掉后弹窗仍显示勾选且可勾 | §五 / §7.2 | 已落修 |
| P2-5 | G-5 用例把两个 `sleep(120)` 的任务留在 `_experience_tasks` 里就结束——事件循环关闭时会产生 pending task 噪声（本仓库在 `a8ed650` 上已经为同形问题付过学费：drain 只 cancel 不 await ⇒ `GeneratorExit` 红在别的模块） | §9.1 | 留档（已在 G-5 补一句收尾要求） |
| P2-6 | `sessions` 的真实定义是「**事件表里** distinct `group_id` 的个数」，即「产生过至少一笔已结算往返的分组数」，与页眉文案「你过往 N 次自动交易」不等价（空跑一整天没成交的会话不计入）。按 I-9 的严格口径，这属于「数字是真的、文案是歪的」 | §4.8 | 留档（已在 §4.8 注明口径） |
| P2-7 | §2.1 说 `db/repositories.py`「本轮 +1 个再导出」，§8.1 表说「再导出 +2」——同一件事两个数 | §2.1 / §8.1 | 已落修（统一为 +3，见 E-1） |
| P2-8 | 图谱布局是本特性里最重的一段自研代码（120 节点 × 300 次 Fruchterman–Reingold 迭代 ≈ 4×10⁶ 次浮点运算）。需求只要求「可视化管理」，一个**确定性放射 / 分层布局**（语境按轴分环、经验挂在其邻域）能以十分之一的代码拿到同等甚至更好的可读性，且天然免疫 A-20 的顺序敏感 | §7.1 | 留档（不阻断，见 §7.1 的取舍说明） |

### 六条 P0 的取证细节

#### P0-1 · 物化的唯一写路径没有被再导出（**特性今天 100% 空转**）

`service.rebuild()`（`experience/service.py:110`）调用 `repo.upsert_lesson_nodes(...)`，而
`db/repositories.py:1035-1052` 的再导出块里**没有这个符号**。实测：

```
$ services/quant/.venv/Scripts/python -c "from app.db import repositories as repo; ..."
OK   append_experience_events        OK   load_experience_nodes
OK   experience_materialize_lock     OK   mark_stale_experience_lessons
OK   experience_overview             OK   replace_experience_edges
...（另 13 个符号全部 OK）
MISS upsert_lesson_nodes            ← 唯一缺席的那个，恰好是物化的唯一写路径
```

失败形态是**最坏的一种**：`AttributeError` 落进 `_materialize_bounded` 的 `except Exception`，
只写一行 `warning`；图因此永不建立；`load_session_graph` 因
`if not any(n["kind"] == "lesson" ...)` 恒返回 `None`；`ExperienceRuntime.armed` 恒 `False`；
payload 无 `experience` 键——**日志全绿、门禁全过、连 G-2 零回归门禁也全绿，而一个字都不喂**。
本文档 §2.1 把 `db/repositories.py` 判为「保留、零缺陷」，正是这条被漏掉的原因。它也说明一件事：
「模块能 `import`」不等于「函数能被调到」——`importlib` 冒烟对本包 11 个模块全绿，照样没发现它。

#### P0-2 · 四分之三的经验来源在检索层结构性不可达

`retrieval._collect`（`:177-186`）只收「与激活语境有 `applies_in` 边」的经验；
`retrieval._score`（`:115-125`）算 `score = match · node.weight · recency · scope · (pinned?1.5:1)`；
`retrieve`（`:161`）随后 `[item for item in scored if item.score > 0]`。而：

- `service.sanitize_lesson`（`:266-288`）给 `manual` / `imported` / `narrative`（`distill.validate`
  也走它）一律写 `"support": 0, "weight": 0.0`；
- 唯一会更新 `weight` 的是 `upsert_lesson_nodes`，它的入参来自 `graph.materialize`，而
  materialize **只产出 `stat` 桶**（§4.5 成文：「`source != "stat"` 的节点完全不动」）；
- 边更极端：`replace_edges`（`:302`）**先 `delete(AiExperienceEdge)` 全清**再插入 materialize
  计划里的边——即便有人手工给一条 manual 经验插了 `applies_in`，下一次物化也会把它抹掉。

于是三条来源的经验**连候选集都进不去**，`weight` 是多少都无所谓。直接后果四条：
① §4.9 整个蒸馏子系统（实施顺序里的 P4）是**只写不读**的；② §五 的 `POST /nodes` 手工新增是
装饰；③ `POST /import` 导进来的经验永不生效；④ **§7.1 的「置顶」按钮和它旁边那句
「置顶的经验不受证据门槛约束」是假的**，而 I-4 明写「升 `iron` 只有一条路径——用户显式置顶」。
一个把「管理员可视化管理经验」写在需求第三条的特性，不能让管理员写进去的东西永远读不出来。

#### P0-3 · 设置中心的阈值面板没有读点

`RetrievalConfig.from_settings(self.settings)` / `PromoteRule.from_settings(settings)` /
`_options(settings)` 读的全是 pydantic `Settings`。§五 的 `PUT /api/settings/experience` 写的是
`app_settings` 稀疏 KV。两者之间**没有任何桥**。本仓库对这件事已有成文范式：
`_resolve_discipline`（`api/ai_auto.py:89-112`）产出**会话私有的 `Settings` 派生副本**，并在注释里
逐字写明「派生副本晚一步产生，那一行预设就是死字母**且不报任何错**」。本特性重复了那个坑，
而且是在一个**用户界面上明确可写**的面板上重复的——这正是 I-8 那条不变式的镜像形态。

#### P0-4 · 关停链死线倒挂

`main.py:74` 逐步执行关停，单步死线取 `get_settings().shutdown_step_timeout_s`，实测默认 **20.0**；
`_run_shutdown`（`:131-135`）超时即 `task.cancel()`，之后只再给 `_SHUTDOWN_CANCEL_GRACE_S = 1.0` 秒。
而 §4.9 让 `drain_experience_tasks()` 内部 `asyncio.wait(..., timeout=distill_timeout_s + 5)` = **50 秒**。
50 > 20 ⇒ **drain 永远走不到自己那句「cancel 剩余任务」**；外层的 cancel 把 drain 打断在
`asyncio.wait` 上，于是那些后台任务活着穿过 `("db_engine", dispose_engine)` 那一步——SQLite 引擎
在还有活任务持着会话的情况下被 dispose。姿态也不对：**退出软件时没有任何理由再等一次 45 秒的
LLM 调用**，事件早已落盘，蒸馏是加法。

#### P0-5 · 终局物化挂错了退出路径

`manager.shutdown()`（`:1485-1505`）实读：`ai_trade_shutdown_action` 默认 `"hold"` ⇒ 走
`pause_group(reason="app_quit")`，**不经 `_stop_session`**。而 §4.9 把终局物化与蒸馏都 spawn 在
`_stop_session` 返回之前。结论：**桌面端正常退出时，两者都不发生**。叠加 D-3 的去抖（距上次成功
物化不足 60 秒只置脏标志），最后一个窗口内平掉的仓位**永远不会进图**，直到用户下次手工点
「重建图谱」或某次运行中周期物化恰好越过去抖窗口。「经验是累积的」这条需求在最常见的使用节奏
（跑一会儿 → 关软件）下不兑现，而且不报错。

#### P0-6 · 超时停不掉线程池里的活

`_materialize_bounded` 用 `asyncio.timeout(...)` 包住 `rebuild()`，而 `rebuild()` 的重活在
`await run_in_threadpool(materialize, ...)`。`asyncio` 的取消**只能解除 await 者**，`materialize`
所在的那个工作线程会继续把整轮全库重建跑完。于是：20 秒后任务被判为「结束」⇒ 在飞标志被清 ⇒
60 秒后去抖窗口过期 ⇒ **再起一个全库重建**，而上一个还在跑。库越大叠得越快，直到吃满 anyio
默认 40 个工作线程——那个线程池是**全进程共用**的，回测 / 因子挖掘 / 打分预览全都排在它后面。
D-3 是为修 F-2 的规模墙而设计的，照 v3 实现它会**制造一堵更硬的墙**，并且同样不报错。

### 十五条修法索引（E-*，正文已逐条落地）

| 修法 | 对应 | 一句话 | 正文位置 |
|---|---|---|---|
| **E-1** | P0-1 / P2-1 / P2-2 / P2-7 | `db/repositories.py` 的再导出块补 `upsert_lesson_nodes`（**第 0 步，先于一切**）；行数取证订正为 452 / 1052；删掉重复的 `count_sessions()` 计划；再导出条数统一为 **+3** | §2.1 · §8.1 · §十二 |
| **E-2** | P0-2 | 物化时**为每条非 `stat` 经验按其 `signature_json` 生成 `applies_in` 边**（权重 1.0），并给 `manual` / `imported` / `narrative` 一个可配置的起始证据权重 `manual_seed_weight`；`_score` 对 `pinned` 取证据下限 `pinned_evidence_floor` | §4.5 · §4.6 · §4.7 · §6.5 |
| **E-3** | P0-3 | 新增**唯一**派生点 `resolve_experience_settings(session, settings) -> Settings`（镜像 `_resolve_discipline` 的 `model_copy`），启动 / 重建 / 预演 / 后台物化四处全部经它取 `Settings` | §4.10 · §五 · §8.2 |
| **E-4** | P0-4 | drain 改为**先 cancel 后有界收敛**，死线 `experience_drain_timeout_s`（默认 **5.0**，硬约束 < `shutdown_step_timeout_s`），并由启动期校验器钉死这条不等式 | §4.9 · §6.5 |
| **E-5** | P0-5 | 终局物化改挂在**两条**退出路径上：`_stop_session` **与** `pause_group`；脏标志落 `app_settings["experience.dirty"]`，冷启动时被消费 | §4.1 · §4.5 · §4.9 |
| **E-6** | P0-6 | 物化的并发控制改为**进程级单槽 + `finally` 释放**（在飞标志的生命周期覆盖线程里的真实工作），去掉 `asyncio.timeout` 对 CPU 段的假约束；超时只用于**排队等待**，不用于已开跑的计算 | §4.5 · §6.5 |
| **E-7** | P1-1 | `FRAME_OVERHEAD` 订正为 `{"l0": 96, "l1": 40}`（按 7 位数字的最坏页眉实算 + 裕量），并新增一条把**常量与真实渲染长度钉在一起**的用例；`PUT /settings/experience` 复用同一个下限判据 | §3.2 · §五 · §6.5 · §9.2 |
| **E-8** | P1-2 | `harvest_gap` 增加持久化写点（会话收尾累加进 `app_settings["experience.harvest_gap_total"]`）；`service.overview()` 补齐 `materializeHealth` / `harvestGapTotal` 两个读点 | §4.10 · §五 · §6.4 · §8.1 |
| **E-9** | P1-3 | 物化 upsert 时，若该 `key` 本轮重新出现且 `mute_reason != "user"` ⇒ **解除自动静音**；`muted`「绝不覆盖」的范围收窄为 `mute_reason == "user"` 的那一类 | §4.5 · §4.7 · §9.2 |
| **E-10** | P1-4 | `POST /import` 增加 `import_max_nodes`（默认 2000）条数闸与 `replace` 模式的**单事务**要求；越界回 `422 EXPERIENCE_IMPORT_TOO_LARGE`，**不静默截断** | §五 · §十 |
| **E-11** | P1-5 | `lib/httpTimeout.ts` 接线：`/experience/rebuild`、`/experience/export`、`/experience/import` 进长跑表；`/experience/` 家族 + `rebuild`/`import` 末段进写类表 | §七 · §8.3 · §9.1 |
| **E-12** | P1-6 | `AiTradeSession.__init__` 增加 `on_experience_dirty: Callable[[], None] | None = None`；W-7 明确写成「`_inject_explain_meta` 之后**新开**一个 `if side == "close"` 块」；净增预算相应调整 | §4.10 · §8.2 |
| **E-13** | P1-7 | `_open_slots` 增加容量上限 `_OPEN_SLOT_CAP = 64` 与 FIFO 淘汰，淘汰即计 `harvestGap`（被看见的丢弃，而不是无声增长的字典） | §4.3 · §4.10 · §9.2 |
| **E-14** | P1-8 | 订正「单事务」的措辞为**四次提交的真实形态**，并规定提交顺序为「先边后节点」不可行时的替代闸：**物化失败或被取消 ⇒ 在 `materialize_health` 里落 `torn: true`**，`GET /overview` 如实下发、UI 明示「上次重建未完成，图可能不完整」 | §4.5 · §五 · §7.1 |
| **E-15** | P1-9 | 明确决定：`enabled:false` = **既不读也不写**（关就是全关）；启动弹窗与设置中心各写一句明文；「只写不读」不做，记入 §十一 | §4.10 · §7.2 · §十一 |

---

## 零、本轮结论速览（TL;DR）

| 维度 | 结论 |
|---|---|
| 需求四条 | 上下文图存储 ✅ 设计完备且**纯核心已写完**；图检索 ✅ 同上；渐进式披露 ✅ 同上；可视化管理 ❌ **一行未写**；累积更新 ✅ 设计完备、触发链需重做（见 F-1/F-2） |
| 在制代码可用度 | **约 55%**：纯核心（taxonomy / events / aggregate / graph / promote / retrieval / disclosure / distill / rules）质量高、与设计一致，**予以保留**；`runtime.py` 与 `service.py` 需按 §2.2 的 6 条修法改；接线层（API / schemas / session / manager / prompts / trace / main）与**全部前端、全部测试**为零 |
| 阻断级发现 | **F-1（P0）**：物化被挂进 `stop()` 的 await 链 ⇒ 3 成员分组停止最坏 +15s ⇒ 真实资金停止按钮弹「结果未知」。这正是上一轮 P0-1 修掉的故障**从另一扇门复发** |
| | **F-2（P0）**：所谓「增量物化」实为**每笔平仓一次全库重建**，且跑在 `_dispatch` 热路径上；库变大后 5s 预算恒超时 ⇒ 同会话新鲜度承诺**静默失效** |
| | **F-3（P1，产品诚实性）**：L0 页眉把 `sessions` 写死为 0、`events` 取本会话计数 ⇒ 首轮 prompt 逐字告诉模型「来自你过往 **0** 次自动交易的 **0** 笔已结算往返」，同时喂给它 3 条铁律。**prompt 里出现一个假数字，比不出现这一段更坏** |
| 新增不变式 | **I-8**（观测量必须有渲染落点）、**I-9**（prompt 里的每个数字必须为真） |
| 净新增配置 | 27 → **30**（新增物化去抖 / 慢物化阈值 / 物化事件规模上限三项，见 §6.5） |
| 工作量重心 | 前端（13 个新文件）+ 接线（11 处）+ 测试（0 → **52** 条），后端纯核心按 6+15 条修法微调 |
| **v4 评审结论** | **6 条 P0 / 9 条 P1 / 8 条 P2**（见 §评审记录）。最重的两条：**P0-1** `repo.upsert_lesson_nodes` 未再导出 ⇒ 物化恒抛且被吞 ⇒ **整条特性今天 100% 静默空转**；**P0-2** `narrative`/`manual`/`imported` 三类经验无 `applies_in` 边且 `weight=0` ⇒ 蒸馏 / 手工新增 / 导入 / **置顶**四条能力写而不可读 |
| v4 新增不变式 | **I-10**（每一项可写配置必须有一条被实测走到的读路径）|
| v4 净新增配置 | 30 → **36**（`manual_seed_weight` / `pinned_evidence_floor` / `drain_timeout_s` / `import_max_nodes` / `materialize_max_events` / `harvest_gap` 持久化键，见 §6.5）|

---

## 一、概览（Overview）

【AI 自动交易】今天**每一轮都在产生经验，每一次停止都把它全部丢掉**。取证：决策器
`AiTradeDecider` 的滚动摘要 `_summary` 与决策日志 `_journal` 是纯内存实例字段
（`ai_auto/decider.py` 的 `commit_round`），会话终止时 `_build_run_metrics()` 只把最后一段摘要
文本塞进 `metrics_json.finalSummary`（`ai_auto/session.py:2261+`），此后再无任何读点——下一次
启动拿到的是空 `_journal` 与 `summary=None` 的全新决策器。同样，`AttributionLedger` 每笔平仓
都结算出一份信息极丰的 `SettledTrip`（净额 bps、实现成本 bps、R 倍数、计划盈亏比、退出来源、
估计 edge 与实测 edge 的误差、决策置信度、唤醒来源），却住在一个 `_CAPACITY = 200` 的内存环形
缓冲里，随进程消亡。**这个系统每天都在用真金白银买教训，然后在关掉窗口的那一刻把收据烧了。**

本特性把「每一次 AI 自动交易产生的经验与教训」变成一份**可累积、可检索、可视化、可携带**的
数字资产，并以**受控字符预算**回喂给下一次决策。核心结构是一张**上下文图**：图里有两类节点
——**语境节点**（`symbol:BTC-USDT-SWAP`、`regime:trend_up`、`volatility:high_volatility`、
`exit:profitGuard` 这类闭合词表里的坐标轴取值）与**经验节点**（一条被证据支撑的教训），以及
四类有向加权边（`applies_in` / `co_occurs` / `contradicts` / `refines`）。取经验不是 SQL 过滤，
而是一次**有界的种子激活扩散 + 加权打分 + 反例压制 + MMR 去冗**：把「本轮的语境」当作种子点亮
图上若干语境节点，沿 `co_occurs` 边做一跳软扩散（于是「震荡+高波动」学到的教训能在「震荡+常
波动」下以衰减权重浮现，而不是一无所获），再按 `applies_in` 边权 × 证据强度 × 时间衰减打分。
这既满足「高效快捷精准」，也天然给出**渐进式披露**的分层：常驻纲要（L0）只放最强的 3 条、按
语境变化触发的展开（L1）只在图判定语境真的移动时才发、完整证据（L2）只在下单前二次反思与设置
中心的可视化面板里展开；三层各有**硬字符上限**，且「本轮检索结果与上轮完全相同」时整段压成一
行指路。

产品面新增两个入口：**设置中心 →「交易经验库」**（图谱 / 列表双视图、筛选与搜索、详情抽屉含
证据与溯源、置顶 / 静音 / 手工新增 / 编辑 / 删除、从事件重建、导入导出 JSON，以及一个**检索
预演**——用户可以选一组语境，**逐字看到 Agent 下一轮会被喂什么、占多少字符**），以及**启动
弹窗的「经验模式」开关**（默认开启，遵循本仓库既有的「字段缺省 ⇒ 开启、显式 `{enabled:false}`
⇒ 关闭」语义，解析点单一）。贯穿全局的硬不变式：**经验层只影响 prompt，绝不进入任何确定性
门禁、风控、仓位或下单判据**——它与 `signals` 一样是 advisory。这不等于「爆炸半径为零」：经验
文本会进入决策与下单前反思两个**由 LLM 做判断**的位点，因此**能**改变「这一单下不下」。真正
成立的安全论证是**方向单侧**——反思官的裁决空间只有 `confirm` / `abort` / 缩仓
（`REFLECTION_TOOL_SCHEMA` 的 `verdict` 是二值枚举，`adjustedSizeFraction` 经 `apply_reflection`
对原值取 `min`，只减不增），所以经验层能造成的最坏后果是**少做一笔本该做的交易**，而不是多做
一笔、做反一笔或做大一笔。这才是它敢默认开启的理由。

---

## 二、现状取证与承接盘（本轮全部实读，不采信任何上游文档结论）

### 2.1 在制变更清单（逐文件状态）

工作树里存在一批**未提交**的在制变更（`git status` 实读）。下表是本轮逐文件打开后的判定。

**已修改（`M`，共 6 个文件 / +225 行）**

| 文件 | 改动内容 | 判定 |
|---|---|---|
| `services/quant/app/config.py` | +27 项 `ai_trade_experience_*` + 一条 `_check_experience_budgets` 启动期校验器（从 `experience.disclosure` 导入 `FRAME_OVERHEAD` / `MIN_ITEM_BUDGET`） | **保留 + 需改**；本轮再 +9 项并订正 `FRAME_OVERHEAD`（P1-1 / E-7，§6.5） |
| `services/quant/app/db/models.py` | +3 张表 `AiExperienceEvent` / `AiExperienceNode` / `AiExperienceEdge`（+118 行） | **保留**，零改动 |
| `services/quant/app/db/repositories.py` | 再导出块（泛用名加 `experience_` 前缀，避免遮蔽既有 repo 符号） | **需改（P0-1，阻断级）**：再导出块**漏掉了 `upsert_lesson_nodes`**，而它是物化的唯一写路径 ⇒ `service.rebuild` 恒抛 `AttributeError`。本轮 **+3** 个再导出（§8.1 / E-1） |
| `services/quant/app/ai_auto/attribution.py` | `AttributionLedger.last_settled()` 只读访问器 | **保留**，零改动 |
| `services/quant/app/ai_auto/cost_runtime.py` | `CostRuntime.last_settled_trip()` 转发访问器 | **保留**，零改动 |
| `services/quant/app/ai_auto/decider.py` | `AiTradeDecider.llm` 只读属性（蒸馏取 LLM 客户端） | **保留**，零改动 |

**新增未跟踪（`??`）**

| 文件 | 行 | 判定 |
|---|---|---|
| `ai_auto/experience/__init__.py` | 38 | 保留（本轮 §8.2 需再导出 `SessionGraph`） |
| `ai_auto/experience/taxonomy.py` | 191 | **保留，零改动**。十一轴取值逐字对齐 `WAKE_KINDS`(8) / `EXIT_SOURCES`(9) / `PROFILES` 键 / `market_context` 三组；`fold()` 未知折 `unknown` 不丢事件 |
| `ai_auto/experience/events.py` | 198 | 保留。`freeze_open_context` / `build_event` / `EventIdentity` / `ContextFacts` 齐备 |
| `ai_auto/experience/aggregate.py` | 381 | 保留。桶枚举 + 13 个统计量 + 6 模板 + `cost_regime_cuts` 聚合期现算（跨时可比）齐备 |
| `ai_auto/experience/graph.py` | 382 | **保留但删一处死码**（F-6：`Graph.merge_lessons` 全仓零调用点） |
| `ai_auto/experience/promote.py` | 152 | 保留，零改动。`roll_sign` / `resolve_tier` / `evidence_weight` / `resolve_scope` / `is_stale` |
| `ai_auto/experience/retrieval.py` | 281 | 保留，零改动。激活扩散 / 打分 / 反例压制（相对差异 0.25）/ MMR / `most_conflicting` |
| `ai_auto/experience/disclosure.py` | 189 | 保留，零改动。`FRAME_OVERHEAD={"l0":70,"l1":46}`、`MIN_ITEM_BUDGET=124`，「先扣框架、装不下即整段省略」已落地 |
| `ai_auto/experience/runtime.py` | 286 | **需改**（F-1 / F-2 / F-3 / F-4，见 §2.2） |
| `ai_auto/experience/distill.py` | 204 | 保留，零改动。`run_bounded` 自带 `wait_for` + 落库 + `experience.rejected_narrative` 计数 |
| `ai_auto/experience/rules.py` | 65 | 保留，零改动。「缺省即开启」单点解析，写闸取「用户意愿 ∧ 运维总闸」 |
| `ai_auto/experience/service.py` | 313 | **需改**（F-2 / F-3 / F-5） |
| `db/ai_experience_repositories.py` | **452**（v3 误记为 520，P2-1） | 保留，本轮 +2 个函数（§8.1）。**含唯一的 `upsert_lesson_nodes`**（——它没有被 `repositories.py` 再导出，见 P0-1）与 `stream_export_rows`。`count_sessions()` **不必新增**：`overview()` 里已有那条 `COUNT(DISTINCT group_id)`（P2-2） |

**完全未开始（本轮的主要工作量）**

| 领域 | 状态 |
|---|---|
| `api/experience.py`（11 端点） | **零** |
| `schemas.py`（启动请求 `experience` 字段 + 端点 In/Out） | **零** |
| `main.py`（路由注册 + 关停链等待后台任务） | **零** |
| `api/app_settings.py`（`GET/PUT /settings/experience`） | **零** |
| `api/ai_auto.py`（`build_start_request` 接线） | **零** |
| `ai_auto/manager.py`（请求字段 / `_build_member` / `_stop_session` 后台任务） | **零** |
| `ai_auto/session.py`（构造形参 / `_dispatch` 两处 hook / `inject` / 反思 / `stop`·`pause` / `_status_payload` / run metrics） | **零** |
| `ai_auto/prompts.py`（`_format_experience` + 两个插入点） | **零** |
| `ai_auto/trace.py`（`round_start` +1 键） | **零** |
| 全部前端（12 个新文件 + 4 处接线） | **零** |
| 全部测试 | **零**（`services/quant/tests/` 与 `apps/web/**` 均无任何 `experience` 用例） |

**结论**：**经验目前一条也没有被采集**——纯核心写完了，但 `session._dispatch` 没有任何 hook，
`ExperienceRuntime` 没有任何构造点。这一点必须在评审与开发两个节点被反复确认，避免「代码在那里
= 功能在跑」的误判。

**v4 补充（节点 #1 实测）**：即便把全部接线补齐，今天的在制代码**也还是一条经验都读不出来**——
`repo.upsert_lesson_nodes` 没有被再导出（P0-1），且三类非 `stat` 经验在检索层结构性不可达
（P0-2）。「代码在那里 = 功能在跑」的误判有**两层**，本文档 v3 只识破了第一层。

### 2.2 审计发现（逐条含取证与修法）

> 定级：**P0** = 会损坏真实资金操作路径或让特性静默失效；**P1** = 会让产品对用户/模型说假话，
> 或让一条承诺静默不兑现；**P2** = 卫生问题。

#### F-1（P0）· 物化被挂进 `stop()` 的 await 链，上一轮修掉的故障从另一扇门复发

**取证**：`experience/runtime.py:162` `flush()` → `await self.persist_pending()`；
`persist_pending()`（`:123`）在落盘成功后 `await self._materialize_bounded()`（`:141`），后者用
`asyncio.timeout(_MATERIALIZE_TIMEOUT_S)`，`_MATERIALIZE_TIMEOUT_S = 5.0`（`:30`）。
按设计 `flush()` 要挂在 `session.stop()` / `pause()` 上；而 `manager.stop_group`
（`manager.py:745`）**串行**遍历成员逐个 `await self._stop_session(...)`（`manager.py:1309`），
前端 `/ai-trade/{groupId}/stop` 命中写路径 + `/stop`，超时档是 `FAST_MS = 20_000`
（`lib/httpTimeout.ts`）。

**后果**：3 标的分组 × 最坏 5 秒 = **+15 秒**，叠加本就存在的平仓下单往返，客户端 20 秒放弃并
弹出「本次操作结果未知，请刷新持仓/会话状态核对后再决定是否重试」——**在真实资金的停止/平仓
按钮上制造假警报**。上一轮评审的 P0-1 正是为了消灭这条路径才把 LLM 蒸馏移出 `stop()`；物化
（CPU 密集 + 线程池 + 全库扫描）现在从同一个位置走了进来。

**修法（D-1）**：`persist_pending()` **只做 `INSERT OR IGNORE`**，不再触发物化；`flush()` 因此
恒为毫秒级本地 IO。物化统一改为 **manager 侧的 tracked 后台任务**，与蒸馏共用同一个任务集合与
同一条关停等待链（§4.9）。**机械判据**：G-6 的源码扫描用例扩为三条，第三条断言
`experience/runtime.py` 的 `flush` / `persist_pending` 方法体内**不得出现** `materialize`。

#### F-2（P0）· 「增量物化」实为每笔平仓一次全库重建，且跑在 `_dispatch` 热路径上

**取证**：`runtime._materialize_bounded`（`:141-160`）调用的是
`service.rebuild(session, settings)`——而 `rebuild`（`service.py:93`）的实现是
`load_experience_events(session, ExperienceEventQuery())`（**无过滤 = 全表**）→ `materialize(...)`
→ upsert 全部节点 → `replace_experience_edges` 全量替换边。调用点在 `persist_pending` 里，而
`persist_pending` 按设计挂在 `session._dispatch` 的平仓分支（`session.py:343-380`，该分支已有
`await self._persist_trade(trade)`）。

**后果两条，都是静默的**：
1. **热路径阻塞**：`_dispatch` 是**全部** executor 事件的唯一收口，`await` 在这里最坏挂 5 秒，
   期间该会话的 WS 扇出与后续成交落库全部排队。
2. **承诺静默失效**：事件表按设计会长到数万条（§6.1 的 `origin` 注释与 §十一.2 都以此为前提）。
   全库重建的耗时随事件数线性增长，5 秒预算迟早**恒定超时** ⇒ `except` 只记一行 warning ⇒
   「同一会话后续轮次立刻用得上」这条写在 docstring 里的承诺永远不兑现，而**没有任何地方会报错**。

**修法（D-1 + D-3）**：
- 物化移出 `_dispatch`（同 F-1 的后台任务）。
- **去抖**：manager 对同一分组同一时刻至多允许一个物化任务在飞；距上次成功物化不足
  `ai_trade_experience_materialize_min_interval_s`（新配置，默认 60 秒）时只置脏标志，不起任务；
  脏标志在下一次触发或 `_stop_session` 的终局物化时被消费。
- **规模诚实**：物化任务把 `{lastMaterializeTs, lastMaterializeMs, events}` 写进 `app_settings`
  的 `experience.materialize_health`，`GET /api/experience/overview` 如实下发，设置中心概览条显示
  「上次重建：08-24 21:30 · 耗时 1.2s · 2043 条事件」。耗时超过
  `ai_trade_experience_materialize_slow_ms`（新配置，默认 3000）时**在 UI 上明示**「经验库规模
  已接近自动重建的预算，可在设置里手动重建或导出归档」。**绝不假装它是实时的。**
- v1 明确接受「全量重建」这一实现（§十一.8 记为欠账），但**必须让它的规模墙可见**。

#### F-3（P1，产品诚实性）· L0 页眉给模型看的两个数字是假的

**取证**：`runtime.inject`（`:190-196`）装配 wire 时传
`totals={"lessons": self.graph.lesson_count(), "events": self.events_harvested, "sessions": 0}`。
`events_harvested` 是**本会话**已采集事件数（`__init__` 里初始化为 0），`sessions` 是**写死的
0**。而 `disclosure.format_l0`（`:116-121`）用这两个数渲染页眉：
`【交易经验 · 铁律】（来自你过往 {sessions} 次自动交易的 {events} 笔已结算往返）`。

**后果**：任何一次会话的**第一轮**（也是最典型的一轮），模型收到的是
「（来自你过往 **0** 次自动交易的 **0** 笔已结算往返）」**紧跟着三条铁律**。这是一段自相矛盾的
证据——轻则模型据此给经验段打折（本特性的全部收益归零），重则模型在研判里复述这个 0 让用户看到
产品在自相矛盾。**在喂给下真单的模型的 prompt 里写一个假数字，比不写这一段更坏。**

**修法（D-2）**：把「图快照」与「库存总量」绑成一个对象，从**同一次取数**里出来：
- `service.load_session_graph` 改为返回 `SessionGraph`（新 frozen dataclass，见 §4.10），字段
  `graph: Graph`、`totals: dict`（`{lessons, events, sessions}`，取自
  `repo.experience_overview(session)` 的同一次事务）、`as_of_ts: int`。
- `ExperienceRuntime.__init__(snapshot: SessionGraph | None, rule, settings, identity)`
  （形参 4 个，满足 CLAUDE.md ≤5）。`inject` 用 `self._snapshot.totals`。
- **用例 A-35 钉死**：`format_l0` 页眉里的两个整数 == 同一时刻 `GET /api/experience/overview`
  回执的 `sessions` / `events`。

#### F-4（P1）· `_take_slot` 的退化匹配可能取错开仓语境

**取证**：`runtime._take_slot`（`:110-121`）先按 `(trip.side, trip.seq)` 找槽，找不到时
**遍历所有槽取第一个 `seq` 相同的**。`note_open` 建槽用的是 `trade["side"]`（`long`/`short`），
`SettledTrip.side` 源出 `OpenSnapshot.side`，同样来自 `trade.get("side")`
（`trip_ledger.py:157`）——两者同源，正常路径恒匹配。

**后果**：退化分支只在异常路径（反手腿、部分成交交错）触发，而它一旦触发就是**默默用另一个
epoch 的语境标注这一笔往返**——事件表里多一条语境错误的样本，且永远不会报错。这比少一条更坏
（§4.3 的成文纪律：宁可缺一条并说明，也不编一条语境是猜的事件）。

**修法（D-4）**：退化匹配收紧为「**恰好只有一个**槽的 `seq` 匹配时才取；≥2 个匹配 ⇒ 放弃并计
`harvestGap`」。用例 A-36 构造两个同 `seq` 不同 `side` 的槽，断言不落事件且 `harvestGap == 1`。

#### F-5（P1）· 蒸馏结果的到达通知在 v2 里指向一条不存在的通道

**取证**：v2 §4.9 写「蒸馏结果通过下一次 `GET /api/experience/overview` 轮询**或一条
`experience_distilled` WS 事件**到达 UI」。但蒸馏按设计跑在会话**终结之后**，此时该成员的
`/ws/ai-trade/{sessionId}` 已经关闭；且本仓库的 AI-trade **没有分组级实时通道**（WS 是逐会话的）。

**后果**：照 v2 实现会做出一条永远发不出去的事件，或为了发它而新造一条 WS 通道（远超本特性
的必要复杂度）。

**修法（D-5）**：**不新增任何 WS 事件**。蒸馏产物的可见性由 `GET /api/experience/overview` 的
`lastUpdatedTs` + 设置中心的手动刷新按钮承载；启动弹窗与设置中心打开时各取一次。这是一条有意
的收窄，写进 §十一「明确不做」。

#### F-6（P2）· `Graph.merge_lessons` 是死码

**取证**：`graph.py:127` 定义，全仓 `grep -rn "merge_lessons"` 零调用点（`_materialize_bounded`
走的是「整体重建后替换 `self.graph`」）。CLAUDE.md §四明令删除僵尸代码。

**修法**：删除该方法；会话内图刷新走 §4.10 的 `ExperienceRuntime.adopt_snapshot()` 单点替换。

#### 本轮实读确认为「真」的关键取证（下游不必重复核实）

`create_all` 建缺失的表但**不**给既有表补列（`db/session.py` 的四个 `_ensure_*_columns` 补丁即
为此存在）；`AttributionLedger._CAPACITY = 200`；`EXIT_SOURCES` 九值、`WAKE_KINDS` 八值；
`build_start_request` 是 `async` 且已持有 `AsyncSession`，`resolve_track_record` 就在其中，是
「启动期一次性取数」的现成先例（`api/ai_auto.py:322-373`）；`start_wire=body.model_dump(mode="json")`
（`api/ai_auto.py:371`）使冷续跑自动带上新键；`_build_decision_payload` 的**最后一步**是
`self._wake.inject(payload, InjectionContext(...))`（`session.py:2005-2009`），`contextDisclosure`
由它写入；`_dispatch` 的平仓分支已有 `_persist_trade` 这个 IO 点，且 `exitSource` / 往返结算都
在 `_inject_explain_meta`（`session.py:381-412`）里才写；`build_reflection_prompt` 的第一行是
`evidence = build_decision_prompt(payload)`（`prompts.py:761`）；`build_decision_prompt` 的 15 个
`_format_*` 全部走「取键 → 渲染 → 缺键即整段省略」范式；`trace.round_start` 是**显式取键**装配
（`trace.py:120-128`）；设置中心是「`SettingsNav.SECTIONS` 注册表 + `SettingsPanel` 三元分支」
两处登记；`main.py` 的关停链是 `_shutdown_steps()` 的 12 步表 + `_run_shutdown` 逐步有界；
前端已超阈文件实测行数 `lib/aiTradeTypes.ts` **1787** / `components/Agent/AiTradeLaunchModal.tsx`
**1578** / `lib/aiTrade.ts` **1412** / `lib/store.ts` **1431**（`version: 30`）；后端已超阈文件
`session.py` 2409 / `manager.py` 1774 / `prompts.py` 846 / `api/ai_auto.py` 803 /
`db/repositories.py` **1052**（v3 记的 1029 是改动前的值，而同段其余四个数都是改动后的值——
同一段里两种口径混用，P2-1）。

### 2.3 承接策略

0. **★ 第 0 步（v4 新增，先于一切）**：补上 `repo.upsert_lesson_nodes` 的再导出（E-1），并跑一条
   最小冒烟——**`hasattr(repo, name)` 对本包用到的全部 16 个符号为真**。这一步不做，后面所有
   工作的验收都会在「日志全绿、一个字都不喂」的假象里进行（P0-1）。
1. **不重写纯核心**。§2.1 判为「保留」的 9 个模块与数据层直接进入本轮实现，节点 #2 的第一件事
   是**为它们补测试**（它们现在一条用例都没有），而不是重写。
2. **6 条修法（D-1…D-5 + F-6 删死码）+ v4 评审的 15 条修法（E-1…E-15）先落地**，各自带机械判据
   （G-6 四条源码扫描 + A-35…A-50）。
3. **接线与前端从零实现**，按 §4.10 的逐处清单与 §8 的净增预算。
4. **实施期若发现本文档与源码不符，以源码为准**，并把差异回写进本文件末尾的「实施过程发现的
   方案缺陷」一节（本仓库既有范式）。

---

## 三、核心概念与不变式

### 3.1 三层实体

1. **经验事件（Event）**——原子事实，append-only，**唯一真源**。一条 = 一笔已结算往返
   （`SettledTrip`）+ 它在**开仓那一刻**冻结的语境签名 + 溯源（groupId / runId / instId / bar / seq）。
2. **上下文图（Graph）**——由事件**物化派生**的节点与边，可随时从事件全量重建。这一性质是本
   设计的可修复性来源：**图算错了不是数据丢了，重算即可**。
3. **经验节点（Lesson）**——四种来源：`stat`（按语境桶聚合、过显著性门槛后模板化生成的中文
   陈述，无 LLM、可复现）、`narrative`（会话末一次有界 LLM 蒸馏，必须挂闭合词表里的语境 + 引用
   具体 trip seq）、`manual`（用户手写）、`imported`（JSON 导入）。

### 3.2 九条不变式（实施期不可违反）

- **I-1 · 不进确定性门禁 + 影响方向单侧**。经验层的产物**只**进入 prompt 文本与 UI，**绝不**
  参与 `edge_gate` / `base_rules` / `profit_guard` / `hold_horizon` / `sizing` / `discipline`
  任何一道门禁的判据，也绝不改写 `TradeDecision` 的任何字段。它进入决策与反思两个 LLM 判断位点，
  因此**可以**让一笔本会下的单不下——可依赖的是**方向**：最坏后果恒为「少做一笔」。
  机械判据：**六个门禁模块的源码里不得出现 `experience` 字样**（G-6①）。
- **I-2 · 零回归**。经验层未装配（总闸关 / 启动请求显式关 / 图为空 / 检索零条目）⇒ payload
  **无** `experience` 键 ⇒ `build_decision_prompt` 与 `build_reflection_prompt` 输出**逐字节**
  等于本特性合入前（G-2）。
- **I-3 · 决策轮零 IO**。`_build_decision_payload` 及其下游不得开任何 DB 会话。图在会话启动时
  一次性载入内存；会话内的写只挂在**已有** IO 点（`_dispatch` 的成交路径）。
- **I-4 · 叙事不得独立成铁律**。`source ∈ {narrative, manual, imported}` 的经验**永远**不能
  自动晋升到 `iron`（L0 常驻层）。要进 L0，要么被 ≥ `min_samples` 条确定性事件佐证，要么由用户
  **显式置顶**（UI 上明示「置顶的经验不受证据门槛约束」）。
- **I-5 · 语境是闭合词表**。轴与取值来自 `taxonomy.py` 常量表（`symbol` 为开放轴，取 instId
  原文）。LLM 蒸馏与 JSON 导入的输出用 `enum` / `is_valid` 限死，越界值**整条丢弃**并计
  `rejected`（设置中心可见）。未知取值折成 `unknown`，**绝不丢事件**（丢事件 = 幸存者偏差）。
- **I-6 · 字符预算是硬上限（含框架句）**。每层披露都有 `max_chars`，**页眉与页脚计入同一份
  预算**：`item_budget = max_chars − len(header) − len(footer)`；条目按 `score` 降序累加，第一条
  会超即停止，**绝不**在句中截断、**绝不**跳过它去装后面更短的。三条硬规则：① 零条目 ⇒ 整层
  **一个字都不渲染**（不得只输出页眉页脚的空壳）；② `max_chars ≥ FRAME_OVERHEAD[layer] + 124`
  由 `Field(ge=…)` + 启动期校验器共同守，违反即**拒绝启动**，不静默降级；③ 默认值按实数校准
  （L0 真实框架 78～84 字 ⇒ 3 条满长铁律需 84+372=456，故默认 480；L1 真实框架 33 ⇒ 默认 660）。
  **★ E-7（P1-1）**：v3 声明的 `FRAME_OVERHEAD = {"l0":70,"l1":46}` 与真实渲染长度不符——实算
  L0 页眉 40 字（`sessions`/`events` 各取 7 位数时 46 字）+ 页脚 38 字 = **78～84**，L1 为 13+20=**33**。
  于是 `l0_max_chars ∈ [194, 201]` 会**通过**启动期校验却让第一条 120 字铁律装不下 ⇒ 整层静默不
  渲染，正是本条规则②声称要消灭的形态。修法三件：(a) 常量订正为 `{"l0": 96, "l1": 40}`（按最坏
  页眉 + 裕量，宁可多留也绝不少留）；(b) 新增用例把**常量与真实渲染长度钉在一起**（A-39）；
  (c) `PUT /api/settings/experience` 复用**同一个**下限判据，不得绕过（§五 / A-40）。
- **I-7 · 成本口径不混用**。事件落 `cost_basis`（`net` | `gross`，取自会话
  `settings.ai_trade_net_pnl_enabled`）与 `fee_known`。任何 bps 类统计**只**聚合
  `cost_basis == "net" && fee_known == True` 的事件；被排除条数如实计入 `excluded` 并在 UI 显示。
- **I-8 · 观测量必须有渲染落点**〔本轮新增〕。后端算出来却没有任何 UI 渲染位置的观测量**等于
  死码**，一律不许留。本特性受此约束的六个键：`harvestGap`、`graphAsOfTs`、`excludedByCostBasis`、
  `rejectedNarrative`、`truncated`、`materializeHealth`。节点 #3 逐个核对渲染位置。
- **I-9 · prompt 里的每个数字必须为真**〔本轮新增，F-3 的教训〕。经验段渲染出的任何统计量
  （页眉的会话数与往返数、每条铁律的 `n` 与 `t`）都必须与设置中心同一时刻可查到的值一致。
  「先渲染一个占位数字，以后再接真值」在本特性里是**禁止**的——它喂的是要下真单的模型。
- **I-10 · 每一项可写配置必须有一条被实测走到的读路径**〔v4 新增，P0-3 的教训〕。凡是在 UI 上
  可写、或经 `PUT` 落进 `app_settings` 的配置项，都必须存在**一条被用例实际走过的读路径**把它
  并回运行时真正读的那个对象。本仓库已有成文范式：`_resolve_discipline` 产出会话私有的
  `Settings` 派生副本，注释里逐字写着「派生副本晚一步产生，那一行预设就是死字母**且不报任何
  错**」。本特性受此约束的 13 项阈值统一经 `resolve_experience_settings`（§4.10）取值，
  A-41 逐项钉死。**「写得进、读不到」与「算了却看不见」（I-8）是同一枚硬币的两面。**

---

## 四、技术设计

### 4.1 端到端时序（v3 修订版；★ 标出与 v2 的差异）

```
【启动】POST /ai-trade/start  { experience: {enabled:true, ...} | 整键缺省 }
  api/ai_auto.py::build_start_request
      ├─ experience/rules.py::resolve_experience(body.experience, settings)   ← 唯一解析点
      └─ experience/service.py::load_session_graph(db, settings=…, rule=…)
             ★ 返回 SessionGraph{graph, totals, as_of_ts}（totals 与图同一次取数，I-9 / F-3）
  manager.start_group → _build_member
      └─ ExperienceRuntime(snapshot, rule, settings, identity) → AiTradeSession(experience=…)

【每一决策轮】session._build_decision_payload
      └─ ★ 必须是本方法的最后一步，排在 self._wake.inject(...) 之后
         self._experience.inject(payload, seed=ContextSeed(axes=runtime.seed_axes(...)))
             ├─ retrieval.retrieve(graph, seed, cfg) → RetrievalResult
             ├─ disclosure.experience_tier(result, prev_ids, payload["contextDisclosure"])
             └─ payload["experience"] = wire（含 maxChars 与 totals，随证据下发）
      prompts.build_decision_prompt → _format_experience(payload) → 延迟导入 disclosure.format_experience

【下单前反思】session._reflect_before_execute
      └─ self._experience.inject_reflection(payload, decision)
             → payload["experienceCounter"]（与提议动作最相冲突的 1 条，≤240 字）

【平仓成交】session._dispatch（side == "close"）
      ★ 落点必须在 self._inject_explain_meta(trade) 之后 —— exitSource 与往返结算都在那一步才写
      ├─ trip = self._cost.last_settled_trip()
      ├─ self._experience.note_close(trade, meta, trip)     ← 纯内存组装
      └─ await self._experience.persist_pending()           ← ★ 只 INSERT OR IGNORE，毫秒级
      ★ 不在这里物化（F-1/F-2）：改为通过 on_dirty 回调通知 manager 起后台任务

【开仓成交】session._dispatch（else 分支）
      └─ self._experience.note_open(trade, seq=self._position_seq, facts=ContextFacts(...))

【暂停 / 停止】session.pause() / stop()
      └─ await self._experience.flush()      ← ★ 恒等于 persist_pending()：纯本地 INSERT，毫秒级
      ★ 物化与蒸馏都不在这条 await 链上

【★ E-5（P0-5）】终局物化挂在**两条**退出路径上，不是一条
      manager._stop_session(...)  → 返回前 spawn  _experience_finalize(member)
      manager.pause_group(...)    → 返回前 spawn  _experience_finalize(member, distill=False)
      理由：`ai_trade_shutdown_action` 默认 "hold" ⇒ 关软件走的是 pause_group 而**不是** stop，
      只挂 _stop_session 等于「桌面端正常退出时终局物化根本不发生」

【会话终结后（脱离 stop 的 await 链）】manager 侧的 tracked 后台任务集 _experience_tasks
      ├─ service.materialize_bounded(...)    ← 终局物化（去抖豁免）
      ├─ distill.run_bounded(llm, inputs, settings)   ← 有界 LLM 蒸馏，失败只 warning
      └─ 完成后把新 SessionGraph 交给仍在跑的成员：member.session.experience.adopt_snapshot(fresh)

【运行中的周期物化】manager._on_experience_dirty(group_id)
      └─ 去抖：距上次成功物化 < materialize_min_interval_s ⇒ 只置脏标志
      └─ 否则 spawn 一个 _experience_tasks 任务：materialize_bounded → adopt_snapshot 广播

【app 关停】main.py::_shutdown_steps 追加一步
      └─ ("ai_trade_experience", lambda: get_ai_trade_manager().drain_experience_tasks())
             ★ E-4（P0-4）：**先 cancel，再有界收敛**，死线 experience_drain_timeout_s（默认 5.0）
               —— 必须 < shutdown_step_timeout_s(20.0)，否则外层先 cancel、内层清理永不执行

【设置中心】GET /api/experience/{overview,graph,lessons,nodes/{id},export}
            POST /api/experience/{rebuild,preview,nodes,import}
            PATCH/DELETE /api/experience/nodes/{id}
            GET/PUT /api/settings/experience
```

### 4.2 语境闭合词表（`experience/taxonomy.py`，**已实现，零改动**）

十一条坐标轴，取值全部来自仓库里**已经存在且已被验证**的枚举，不新造分类器：

| 轴 `axis` | 取值 | 来源 |
|---|---|---|
| `symbol` | instId 原文（**开放轴**） | 会话字段 |
| `bar` | `1m`/`5m`/`15m`/`30m`/`1H`/`4H`/`12H`/`1D` | `okx.types.BAR_SECONDS` 键 |
| `regime` | `trend_up` / `trend_down` / `range` / `unknown` | `market_context.py` |
| `volatility` | `high_volatility` / `low_volatility` / `normal` / `unknown` | 同上 |
| `event` | `crash` / `breakout` / `none` / `unknown` | 同上 |
| `side` | `long` / `short` / `unknown` | `SettledTrip.side` |
| `wake` | `WAKE_KINDS` 八值 + `unknown` | `SettledTrip.wake_kind`。**注意**：`payload["trigger"]` 的 `candle`/`interval` 是另一个二值枚举，混用会让整条轴永不匹配 |
| `exit` | `EXIT_SOURCES` 九值（已含 `unknown`） | `meta["exitSource"]` |
| `horizon` | `PROFILES` 键 + `none` + `unknown` | `HorizonRule.profile_id`（**不是** wire 键 `profile`） |
| `cost_regime` | `low` / `mid` / `high` / `unknown` | 由 `realized_cost_bps` 在**聚合期**按三分位现算 |
| `confidence` | `low`(<0.5) / `mid`([0.5,0.75)) / `high`(≥0.75) / `unknown` | `SettledTrip.decision_confidence` |

**`cost_regime` 是唯一一条「聚合期现算、不冻结进事件」的轴**：事件只落原始量 `cost_bps`，
分位点在**每次物化 / 重建时**按 `(inst_id, bar)` 分组从当前事件全集现算（样本 <
`cost_regime_min_samples` 时回落全库分位并把 `costRegimeFallback: true` 如实带进 `stats_json`）。
于是整个语料在每次重建后恒用同一把尺子，跨时可比性得以保留，且「重建幂等」照旧成立。分位点回写
`app_settings.experience.cost_regime_cuts`，那是**只读展示与预演用的缓存**，不是聚合的真源。

规范化：`canonical_key(axis, value) -> "axis:value"`。中文展示名走 `AXIS_LABEL_ZH` /
`VALUE_LABEL_ZH` 两张常量表，**前后端各一份、由一条源码扫描用例钉死同步**（仓库既有范式：
`wireCaps.source.test.ts`；注意 CRLF——正则须写 `\r?\n`，否则干净检出上恒红）。

### 4.3 事件采集（`experience/events.py` + `runtime.note_open/note_close`）

- **语境冻结在开仓时**：`note_open(trade, seq=…, facts=ContextFacts(...))` 把当轮的九轴
  （`symbol/bar/regime/volatility/event/side/wake/horizon/confidence`）冻结进一个 `dict[str,str]`，
  按 `(side, seq)` 存槽（与 `OpenSnapshot` 同一 epoch 身份，不另造身份）。理由：出场时体制可能
  已经变了，用出场语境标注一笔入场决策的教训是错的。**部分成交同一 epoch 不重新冻结**。
- **结果补齐在平仓时**：`note_close(trade, meta, trip)` 三个入参各有分工：
  - `meta` = `trade["explain"]["meta"]`，本层**只读不判**的部分是 `exitSource` 与 `wallTs`
    （沿用 `apply_trip_meta` 只读该键、绝不自己调 `classify_live` 的先例）；
  - `trip` = **`SettledTrip` 本体**，由 `CostRuntime.last_settled_trip()` 取得。**为什么非取本体
    不可**：`trip_meta_wire` 只发 17 个键，**不含** `fee_known` / `side` / `entry_ts` / `exit_ts` /
    `edge_error_bps`，而 I-7 的口径闸靠 `fee_known`、§6.1 的列要 `side` 与 `entry_ts`、§4.4 的
    「估计偏差型」模板要 `edge_error_bps`。只读 meta 会让这三处**静默**失效且全程不报错。
  - `trip is None`（无开仓断面 ⇒ `settle_close` 返回 `{}`）⇒ **不落事件**，改计 `harvestGap += 1`。
  - **★ D-4**：`(side, seq)` 找不到槽时的退化匹配收紧为「**恰好只有一个** `seq` 相同的槽才取；
    ≥2 个匹配 ⇒ 放弃并计 `harvestGap`」。宁可缺一条并说明，也不编一条语境是猜的事件。
- **双时钟**：事件同时落 `exit_ts`（秒级 bar 时钟，取 `trip.exit_ts`）与 `exit_wall_ts`（墙钟，
  取 `meta["wallTs"]`，缺席回落 `time.time()`）；`entry_ts` 取 `trip.entry_ts`，`entry_wall_ts`
  由 `note_open` 当场记下——**`SettledTrip` 不带任何墙钟字段**（`OpenSnapshot.wall_ts` 在
  `settle()` 里被消费掉了），这是入场墙钟必须由 `note_open` 自己冻结的原因。**时间衰减用墙钟，
  历史查询用 bar 钟**。
- **★ E-13（P1-7）· 开仓语境槽必须有界**：D-4 收紧退化匹配之后，「≥2 个 `seq` 匹配」的那两个槽
  **永远不会被弹出**，而 `_open_slots` 既无容量上限也无淘汰规则 ⇒ 长会话下单调增长。本仓库对每
  一个会话内缓冲都有成文的有界纪律（`AttributionLedger._CAPACITY = 200` / `_SESSION_EVENT_CAP =
  200` / 提交 `f48a714` 的 capped trade buffers），这里不能是例外。修法：`_OPEN_SLOT_CAP = 64`
  + FIFO 淘汰，**每淘汰一个即 `harvest_gap += 1`**——被看见的丢弃，而不是无声增长的字典（A-42）。
- 幂等键：`(group_id, inst_id, seq, exit_ts)` UNIQUE ⇒ 续跑 / 重放 / 并发重复写全部安全。

### 4.4 统计层聚合（`experience/aggregate.py`，**已实现，零改动**）

对事件集合枚举**语境桶**（桶 = 语境签名的一个子集），枚举范围刻意受限以防组合爆炸：

- 一阶桶 `{symbol}` × 每条其他轴；二阶桶 `{symbol,bar}` × 每条其他轴；
- 三阶桶只枚举 4 组业务上确有意义的组合：`{symbol,bar,regime,side}`、
  `{symbol,bar,volatility,side}`、`{symbol,bar,wake,side}`、`{symbol,bar,exit}`；
- 跨标的桶（`scope="global"` 候选）：把 `symbol` 轴摘掉的同形桶。

每桶算 `n / wins / winRate / meanNetBps / medianNetBps / stdNetBps / tStat / meanHoldS /
meanCostBps / costShare / meanEdgeErrorBps / firstTs / lastTs / symbols / excluded`。
**只统计 `cost_basis=="net" && fee_known` 的事件（I-7）**，被排除条数落 `excluded`。

模板化陈述 `render_label` 在 6 个模板里按主导信号择一（亏损型 / 盈利型 / 退出型 / 估计偏差型 /
换手型 / 置信度型），产出**恒 ≤ 120 字**（各字段有格式化上限），满足 I-6 的「第一条一定装得下」。

### 4.5 图谱物化（`experience/graph.py` + `service.rebuild`）

- **语境节点**：桶签名里出现过的每个 `axis:value`，`support` = 该语境下的事件数。
- **经验节点**：每个过门槛的桶一个，`key = "lesson:" + sha1(桶签名 + 模板 id)[:12]`。同一桶重算
  时 `key` 不变 ⇒ **更新而非新增**（累积语义）。
- **`applies_in(lesson → context)`**：`w = 桶内该语境的事件数 / 桶的事件数`。
- **`co_occurs(context ↔ context)`**：归一化点互信息
  `npmi = ln(p(a,b)/(p(a)p(b))) / (−ln p(a,b))`，只保留 `npmi > θ_co` 且共现支撑数 ≥
  `co_min_support`。无向存两条有向边（读侧只做单向索引查找）。
- **`contradicts(l1 ↔ l2)`**：两条经验语境签名 Jaccard ≥ 0.6 但 `meanNetBps` 符号相反且两侧
  `|t| ≥ t_threshold`。
- **`refines(l_new → l_old)`**：同 `key` 的新版本覆盖旧版本时，旧版本被复制为 `archived` 节点
  并被指向。**绝不静默覆盖**——版本链是审计线索，也是「经验不断更新」这条需求的诚实实现。

**物化是「按 `key` upsert」，不是「全量替换」**（这条在上一轮评审里定为阻断级，实现已落地，
本轮只复述以免回退）：
- 新桶 ⇒ INSERT；已存在 ⇒ UPDATE `label`/`weight`/`support`/`stats_json`/`signature_json`/
  `last_seen_ts`/`updated_ts`。
- **绝不覆盖的六个字段**：`pinned`、`muted`、`mute_reason`（`user` 来源的）、`first_ts`、
  `created_ts`，以及 `prev_sign`/`sign_streak`（按 §4.7 前滚而非重置）。
  否则会同时制造两个静默故障：① 抹掉用户亲手按下的静音（而 `DELETE /nodes/{id}` 对 `stat` 节点
  回 409 时给的可执行提示正是「改用静音」——产品告诉用户的做法，产品自己不兑现）；② 让
  `iron` 的「符号连续一致」门槛在结构上永不成立 ⇒ L0 恒空 ⇒ 整个特性「装配成功、日志全绿、一个
  字都不喂」。
- **本轮未出现的 `stat` 节点不删**：改置 `muted=True, mute_reason="stale"` 并保留证据链。
- **★ E-9（P1-3）· 自动静音必须可解除**：`_apply_update` 今天**从不触碰** `muted` / `mute_reason`
  ⇒ 一条经验只要安静过一轮物化就被 `mark_stale_lessons` 判为 `stale` 并**永久沉降**，即便后来拿到
  成百上千条新证据也再回不到 prompt——而 §4.7 档位表里写的是「`now − lastTs > stale_days` **且未被
  新证据佐证**」，那半句在代码里没有对应物。修法：**「绝不覆盖」的范围收窄为「用户意志」**——
  upsert 时若该 `key` 本轮重新出现且 `mute_reason != "user"`，则**清空** `muted` / `mute_reason`；
  `mute_reason == "user"` 的一律不动。判据：`pinned` 与 `mute_reason=="user"` 的静音**绝不覆盖**，
  `mute_reason ∈ {"stale","flipped"}` 的静音**必须随新证据解除**（A-43 正反两向各钉一条）。
- **★ E-2（P0-2）· 非 `stat` 经验必须被接进图**：`source != "stat"` 的节点今天既没有 `applies_in`
  边（`_collect` 因此收不到它们）、`weight` 又恒为 `0.0`（`_score` 乘完即 0，被 `score > 0` 剔除）
  ⇒ **蒸馏 / 手工新增 / 导入 / 置顶四条能力全部写而不可读**。修法两件，都在物化这一步做：
  ① 物化计划里**为每一条 `source ∈ {narrative, manual, imported}` 的经验，按其 `signature_json`
  逐轴生成 `applies_in` 边**（`w = 1.0`，语境节点缺席则顺带建出来）——必须在这里做，因为
  `replace_edges` 会先 `delete(AiExperienceEdge)` 全清，任何在别处插的边都会被下一次物化抹掉；
  ② 它们的 `weight` 取 `ai_trade_experience_manual_seed_weight`（默认 **0.35**，见 §6.5）而不是 0，
  语义是「一条没有统计支撑、但有人明确写下来的经验」，恰好低于一条 `n=8, |t|=2.0` 的统计经验。
  **`source != "stat"` 的节点的其余字段（`label` / `detail` / `pinned` / `muted` / `tier`）仍然
  完全不动**——物化只负责把它们**接进图**，不改写它们的内容与用户意志。
- 由 `ai_experience_repositories.materialize_lock()`（模块级 `asyncio.Lock`）串行化。
  **★ E-14（P1-8）· 「单事务完成」是错的**：`rebuild()` 实读为**四次独立提交**
  （`upsert_lesson_nodes` / `mark_stale_lessons` / `replace_edges` 各自 `await session.commit()`，
  外加 `set_app_setting`）。后果：取消或异常落在两次提交之间会留下「新节点 + 旧边」的**撕裂图**，
  而 `match(l) = Σ A(c)·w_applies(l, c)` 对没有边的新节点恒为 0 ⇒ **L0 静默变空**。v1 **不强行改成
  单事务**（那要动数据层四个函数的提交语义，回归面远大于收益），改为**让撕裂可见**：物化编排层
  用 `try/except/else` 把「未走完全部四步」的情况写进 `materialize_health.torn = true`，
  `GET /overview` 如实下发，§7.1 概览条以 `--warn` 明示「上次重建未完成，图可能不完整，建议手动
  重建」。**能修的修，修不动的说出来——绝不留一个不报错的半成品**（A-44）。
- **唯一写路径是 `upsert_lesson_nodes`**，编排层禁止写第二份 SQL——两份必漂移，而这里漂移的是
  用户亲手按下的「静音」。**★ E-1（P0-1）**：这个函数**今天没有被 `db/repositories.py` 再导出**，
  于是 `service.rebuild` 恒抛 `AttributeError` 并被 `except Exception` 吞成一行 warning ⇒ 图永不
  建立 ⇒ 整条特性静默空转。**这是实施顺序里的第 0 步**（§十二）。

#### ★ 物化的四个触发点（D-1 / D-3，v3 重做；v4 由 E-5 补上「冷启动补物化」一条）

| 触发点 | 位置 | 去抖 | 超时 |
|---|---|---|---|
| **运行中周期物化** | manager `_on_experience_dirty(group_id)`（由 session 在 `persist_pending` 成功后**同步、不 await** 地回调） | 距上次成功物化 < `materialize_min_interval_s`(60s) ⇒ 只置脏标志；**进程级单槽**：`_materialize_busy` 为真即只置脏标志（E-6） | **不对 CPU 段设 `asyncio.timeout`**（E-6）；只对「排队等锁」设 `materialize_timeout_s`(20s) |
| **会话终局物化** | manager `_stop_session` **与 `pause_group`** 返回前各 spawn 一次（E-5） | **豁免去抖**（这是最后一次机会） | 同上 |
| **冷启动补物化** | `main` 的 lifespan 启动段：`app_settings["experience.dirty"]` 为真 ⇒ spawn 一次（E-5） | 无 | 同上 |
| **用户手动重建** | `POST /api/experience/rebuild` | 无 | 无（请求级；CPU 部分走 `run_in_threadpool`，沿用 `run_backtest` / `prior_service` 范式）。**客户端超时必须落长跑档**，见 §七 的 E-11 |

**★ E-6（P0-6）· 「有界超时」对线程池里的活是假的**：`_materialize_bounded` 用 `asyncio.timeout`
包住 `rebuild()`，而 `rebuild()` 的重活在 `await run_in_threadpool(materialize, ...)`。asyncio 的
取消**只能解除 await 者**——那个工作线程会把整轮全库重建跑完。于是 20 秒后任务被判为「结束」⇒
在飞标志被清 ⇒ 60 秒后去抖窗口过期 ⇒ **再起一个全库重建，而上一个还在跑**。库越大叠得越快，
直到吃满 anyio 默认 40 个工作线程；那个线程池**全进程共用**，回测 / 因子挖掘 / 打分预览全排它后面。
修法三件：
1. **并发控制改为进程级单槽 + `finally` 释放**：`_materialize_busy` 这个布尔的生命周期必须覆盖
   「线程里的真实工作」全程，而不是「等待者存活的那一段」。忙时只置脏标志，绝不叠第二个任务。
2. **去掉对 CPU 段的 `asyncio.timeout`**；`materialize_timeout_s` 的语义收窄为「**排队等
   `materialize_lock` 最多等这么久**」，等不到就放弃本次（脏标志留着，下次再来）。
3. **规模闸**：单次自动物化读取的事件数上限 `materialize_max_events`（默认 200000）；超过即
   **不自动物化**，只在 `materialize_health` 里落 `overflow: true` 并由 §7.1 明示「经验库已超过自动
   重建规模，请手动重建或导出归档」。**宁可停下来说清楚，也不要一个永远跑不完的后台任务。**

物化任务完成后：① 把 `{lastMaterializeTs, lastMaterializeMs, events, slow, torn, overflow}` 写进
`app_settings["experience.materialize_health"]`（后三个键分别来自 E-6 / E-14 / E-6，六个键**全部**
在 §7.1 有渲染落点，I-8）；② 重新 `load_session_graph` 得到一份新的 `SessionGraph`，并对**当前
分组内仍在运行的每个成员**调用 `member.session.experience.adopt_snapshot(fresh)`——单点引用替换，
同一事件循环内无竞态；③ 清掉 `app_settings["experience.dirty"]`。
这就是「经验是累积的，随着交易的进行不断更新」在**同一会话内**的兑现方式。

**★ E-5（P0-5）· 脏标志必须能跨进程活下来**：v3 把脏标志放在 manager 的内存字典
（`_experience_dirty: dict[str, bool]`）里，而**桌面端正常退出走的是 `pause_group` 不是
`_stop_session`**（`manager.shutdown()` 实读：`ai_trade_shutdown_action` 默认 `"hold"`）⇒ 终局物化
根本不触发，攒下的脏标志随进程消亡 ⇒ 最后一个去抖窗口内平掉的仓位**永远不会进图**。修法两件：
① 终局物化同时挂在 `_stop_session` **与** `pause_group` 上（`pause` 侧只做物化不做蒸馏——暂停不是
终结，没有「会话回顾」可写）；② 脏标志**落库**（`app_settings["experience.dirty"]`），并在
`main` 的 lifespan 启动段消费一次。这两条合起来才让「经验是累积的」在最常见的使用节奏
（跑一会儿 → 关软件 → 明天再开）下真的成立（A-45）。

### 4.6 检索算法（`experience/retrieval.py`，**已实现，零改动**，纯函数、零 IO）

**种子在决策轮只有 7 轴**：`ContextSeed` 在 `_build_decision_payload` 时刻装配，而 `side`
（本轮要开多还是开空）与 `confidence`（模型置信度）**此刻都还不存在**——两者要到 `decide()`
返回之后才有。

| 位点 | 可用轴 |
|---|---|
| 决策轮（L0/L1） | `symbol`/`bar`/`regime`/`volatility`/`event`/`wake`/`horizon`（7 轴）；**已有持仓时**额外把 `side` 置为**当前持仓方向**（已发生的事实，不是提议） |
| 反思轮（L2） | 上述 7～8 轴 + **提议动作的 `side`** |
| 检索预演 | 用户手选，全 11 轴均可 |

**步骤 1 · 种子激活扩散（一跳，有界）**

```
A(c)  = 1.0                                     c ∈ seed
A(c') = max_{c ∈ seed} α · w_co(c, c')          c' ∉ seed 且 w_co ≥ θ_co
```

按 `A` 降序保留前 `M_ctx`（默认 24）个扩散语境。**刻意只做一跳**：两跳的收益是把传递性带进来，
代价是相关性被稀释到无法解释，且会把不同标的的语境串起来。

**步骤 2 · 候选收集**：所有与激活语境有 `applies_in` 边、且 `muted == False` 的经验节点，按
`weight` 降序取前 `candidate_cap`（默认 200）。

**步骤 3 · 打分**

```
match(l)    = Σ_c A(c) · w_applies(l, c)
evidence(l) = sat(support) · conf(|t|)
              sat(n) = clip( ln(1+n) / ln(1+N_iron), 0, 1 )
              conf(t) = clip( |t| / t_iron, 0, 1 )
recency(l)  = 0.5 ** ( (now_wall − last_seen_wall) / (halflife_days · 86400) )
scope(l)    = 1.0  若 l 的 symbol 语境 ∈ seed
              0.7  若 l.scope == "global"（支撑跨 ≥ global_min_symbols 个标的）
              0.0  否则                        ← 跨标的污染的硬闸（R-4）
score(l)    = match · evidence · recency · scope · (pinned ? 1.5 : 1.0)

★ E-2（P0-2）· evidence 的下限：
evidence(l) = max( sat(support)·conf(|t|),
                   pinned_evidence_floor  若 l.pinned,          ← 用户置顶 = 用户提供的证据
                   manual_seed_weight     若 l.source ≠ "stat" ) ← 有人写下来 ≠ 没有证据
理由：三类非 stat 经验出生即 support=0 / weight=0.0，乘进 score 恒为 0，而
`retrieve` 随后 `[item for item in scored if item.score > 0]` 会把它们全部剔除。
不设下限，I-4 承诺的「升 iron 只有一条路径——用户显式置顶」在代码里根本不存在。
```

**步骤 4 · 反例压制**：对 `contradicts` 边相连且同时进入候选前列的两条经验，判据用**相对差异**
`rel = |m₁ − m₂| / max(m₁, m₂, ε)`（绝对阈值会随激活语境数漂移，故不可用）：
`rel > 0.25` ⇒ 保留高分者、低分者记入 `suppressed`（UI 可见、prompt 不出现）；
`rel ≤ 0.25`（势均力敌）⇒ **两条都不进 L0/L1**，打包成一条「争议」记录送 L2。
理由：把一个未解决的冲突当成事实讲给交易员是产品在撒谎；讲给风控复核官才是对的。

**步骤 5 · MMR 去冗**

```
先过滤：drop l if score(l) <= 0        ← 必须先剔除，否则 norm 会除零
若过滤后为空 ⇒ 该层整段不渲染（不是渲染空壳）
pick = argmax_l (1−λ)·norm(score(l)) − λ·max_{p∈picked} jaccard(ctx(l), ctx(p))
```

**复杂度** `O(|seed|·deg_co + |candidate|·|A| + k²)`，数千节点下为微秒级纯内存运算。
**决定性**：所有排序均以 `(score, node.key)` 二元组为键，杜绝浮点并列时的不稳定顺序。

### 4.7 晋升 / 衰减 / 版本链（`experience/promote.py`，**已实现，零改动**）

| 档位 | 门槛 | 披露 |
|---|---|---|
| `candidate` | `n < min_samples`(8) 或 `|t| < t_threshold`(2.0) | 不进 prompt，UI 可见 |
| `normal` | `n ≥ 8` 且 `|t| ≥ 2.0` | L1 可选 |
| `iron` | `n ≥ iron_samples`(20) 且 `|t| ≥ iron_t`(2.5) 且 `sign_streak ≥ 2` | L0 可选 |
| `archived` | 被 `refines` 指向的旧版本 | 仅 UI 版本链 |
| `muted` | 用户静音，或 `now − lastTs > stale_days`(90) 且未被新证据佐证 | 全部不披露；UI「已沉降」可筛 |

**符号一致性的落点**：物化 upsert 时先读既有行的 `prev_sign`，与本次 `sign(meanNetBps)` 比较
——相同 `sign_streak += 1`，不同则 `sign_streak = 1` 并触发翻转处理，两者随行写回。
**符号翻转 = 降档 + 建 `contradicts` 边 + 建 `refines` 链**，而不是就地改数——用户因此能看到
「这条经验曾经是反过来的，在 X 月被 N 笔新样本推翻」，这正是「经验累积、不断更新」所要求的东西。

**`last_seen_ts` 的语义是硬约定**：它是该经验**最近一条支撑事件的墙钟**（`max(event.exit_wall_ts)`，
与 `stats_json.lastTs` 同值同源），**不是**最近一次物化的时刻——后者由 `updated_ts` 单独承载。
两者若被合成一个字段，`recency(l)` 会在每次重建后被拨回 `now` ⇒ 衰减因子恒为 1 ⇒ 三年前的教训与
昨天的同权，**且不会报错**。

`narrative` / `manual` / `imported`：起始档 `candidate`，被 ≥ `min_samples` 条同桶事件佐证后可升
`normal`；**升 `iron` 只有一条路径——用户显式置顶**（I-4）。

**★ E-2（P0-2）· 这条路径今天不通，必须修**。三件事缺一不可，缺任何一件「置顶」都还是个假按钮：
① 物化要给它们建 `applies_in` 边（§4.5），否则 `_collect` 连候选都收不到；
② `evidence` 要有下限（§4.6），否则 `score` 恒 0 被剔除；
③ `retrieve` 的分层判据 `iron = (tier == "iron") or pinned` 已经正确，**无需改动**——
   一条被置顶的 `candidate` 本就应当进 L0，这是 I-4 写下的语义。
**验收 A-46 逐条钉死**：手工新增一条经验 → 置顶 → 用它的语境做检索预演 → 它必须出现在 `l0` 里；
不置顶时它必须**不**出现（`candidate` 不进 prompt）。这两个断言一起，才叫「置顶真的有用」。

### 4.8 渐进式披露（`experience/disclosure.py`，**已实现，零改动**）

| 层 | 触发 | 条数 | 字符上限（含框架句） | 落点 |
|---|---|---|---|---|
| **L0 常驻纲要** | 每轮 | ≤3（`iron` + `pinned`） | **480** | `build_decision_prompt` |
| **L1 语境展开** | 仅 `tier == "full"` 轮 | ≤3（`normal`，与 L0 去重） | **660** | 同上，紧随 L0 |
| **L2 反例/证据** | 下单前反思轮 | 1 条（最相冲突者或争议对） | **240** | `build_reflection_prompt` 的追加段 |

**关于 L2 必须写明的事实**：`build_reflection_prompt` 的第一行是
`evidence = build_decision_prompt(payload)`（`prompts.py:761`），所以反思轮看到的**不是** 240 字，
而是「决策轮那份 L0/L1（按当轮档位渲染）+ 这 240 字的反例」。这是**有意**的——复核官需要看到
交易员看到的同一份证据才能复核；但它意味着：① 反思轮的经验字符预算是 `L0+L1+L2` 的和；②
若当轮档位是 `repeat`，复核官看到的 L0 是那一行压缩形态，**反例段因此必须自带足够的自解释**
（不得写「见上文第 2 条」这种指向可能已被压缩掉的内容的引用）。

**档位判定** `experience_tier(result, prev_ids, context_disclosure)`：
- `full` — 会话首轮、或本轮 L0∪L1 的经验 id 元组与上轮**不同**、或既有
  `contextDisclosure == "full"`；
- `brief` — id 元组不变且 `contextDisclosure == "full"` ⇒ 只渲染 L0；
- `repeat` — id 元组不变且 `contextDisclosure == "repeat"` ⇒ **整段压成一行**
  `【交易经验】同上 N 条铁律（未变化）`。

**这一档位机制是本设计对「防止上下文爆炸」的正面回答**，其收益来自一个非显然的乘数：
`commit_round` 每轮往 `_context` append **三条**消息，且 `build_decision_prompt(payload)` 在那里
被**再渲染一次**原样塞进 user 消息；`_trim_context` 的帽子 `ai_trade_max_context_messages` 默认
40 条 ≈ 13 轮，但 `summarize()` 会**整体重置** `_context`，周期 `context_refresh_rounds` 默认
**10 轮** ⇒ 稳态下真实驻留 ≈ 10 轮。于是一段每轮 480 字的经验块在留存窗口里的真实占用是
`480 × 10 ≈ 4.8 KB`；加上档位机制后，稳态（语境未变）每轮只留约 22 字，**留存窗口占用从约
4.8 KB 降到约 0.7 KB**，仅在语境真的移动的那几轮付全额。

**注意 `commit_round` 的二次渲染**：档位判定的结果必须住在 `payload["experience"]["tier"]` 里
（本设计已如此），**绝不能**在渲染函数内部现场重算——两次渲染之间任何状态漂移都会让 `_context`
里的那一份与模型真正看到的那一份不一致。

**渲染形态**（L0 示例）：

```
【交易经验 · 铁律】（来自你过往 37 次自动交易的 2043 笔已结算往返）
1. BTC-USDT-SWAP 15m · 高波动+突破 做多：23 笔净额均值 −31bps（t=−2.8），成本吃掉毛额 68%。
2. BTC-USDT-SWAP 15m · 由「利润保护」了结的 41 笔净额 +54bps；由「Agent 主动平」了结的 37 笔 −12bps。
3. 全标的 · 置信度<0.5 的开仓：58 笔胜率 38%，显著低于 ≥0.75 组的 61%。
以上为统计结论，非指令；与当前证据冲突时以当前证据为准，并在研判中说明理由。
```

页眉里的 `37` 与 `2043` **必须是经验库的真实总量**（I-9 / F-3），来源见 §4.10 的 `SessionGraph`。
**口径必须写准（P2-6）**：`sessions` 的真实定义是「事件表里 distinct `group_id` 的个数」，即
**产生过至少一笔已结算往返的分组数**——空跑一天没成交的会话不计入。文案因此定为
「来自 **{sessions}** 组有成交记录的自动交易的 **{events}** 笔已结算往返」，而不是含糊的
「你过往 N 次自动交易」。按 I-9 的严格口径，「数字是真的、文案是歪的」同样算说假话。
页眉长度随之变化，**必须与 E-7 的 `FRAME_OVERHEAD` 一起重算**（A-39 会当场发现不一致）。
末句是必须的：没有它，模型会把统计结论当成硬规则执行，那等于让经验层绕过 I-1 变成一道影子门禁。

### 4.9 会话末叙事蒸馏（`experience/distill.py`，**已实现**；接线为零）

- **时机：脱离 `stop()` 的 await 链，作为 manager 侧的有界后台任务**。
  `session.stop()` 逐字不变，只多一行 `await self._experience.flush()`（纯本地 INSERT，毫秒级）；
  `manager._stop_session()` 在**返回前** spawn 一个 tracked task。
- **★ E-5（P0-5）· 同时挂 `pause_group`**：`manager.shutdown()` 实读——`ai_trade_shutdown_action`
  默认 `"hold"` ⇒ 关软件走 `pause_group("app_quit")`，**不经 `_stop_session`**。只挂 stop 等于
  「桌面端正常退出时终局物化与蒸馏都不发生」。故 `pause_group` 返回前也 spawn 一次，
  **但只做物化不做蒸馏**（暂停不是终结，没有「会话回顾」可写，也不该在关软件时起一次 45 秒的
  LLM 调用）。
- **任务体**（`manager._experience_finalize(member)`）串行两步，**共用一个 `_experience_tasks`
  集合**：① `service.materialize_bounded(...)`（终局物化，豁免去抖）；②
  `distill.run_bounded(decider.llm, runtime.distill_inputs(), settings)`。任一步失败只 warning。
  `done_callback` 从集合里摘掉自己（防泄漏）。
- **app 关停**：`main._shutdown_steps()` 追加一步
  `("ai_trade_experience", lambda: get_ai_trade_manager().drain_experience_tasks())`。
  **★ E-4（P0-4）· v3 的死线是倒挂的**：`_run_shutdown` 的**单步**死线取
  `get_settings().shutdown_step_timeout_s`，实测默认 **20.0**，超时即 `cancel` 该步并只再给
  `_SHUTDOWN_CANCEL_GRACE_S = 1.0` 秒；而 v3 让 drain 内部等 `distill_timeout_s + 5 = 50` 秒。
  50 > 20 ⇒ **drain 永远走不到自己那句「cancel 剩余任务」**，外层的 cancel 把它打断在
  `asyncio.wait` 上，那些后台任务于是活着穿过 `("db_engine", dispose_engine)` 那一步——引擎在还有
  活任务持着会话的情况下被 dispose。修法：drain 的姿态改为**先 cancel、再有界收敛**，与同文件
  `_cancel_background` 逐字一致：
  ```python
  async def drain_experience_tasks(self) -> None:
      tasks = {t for t in _experience_tasks if not t.done()}
      if not tasks:
          return
      for task in tasks:
          task.cancel()                       # ① 先取消：退出软件时没有理由再等一次 45 秒 LLM
      await asyncio.wait(tasks, timeout=self._settings.ai_trade_experience_drain_timeout_s)
      # ② 有界收敛（默认 5.0s，硬约束 < shutdown_step_timeout_s）；收不干净就抛下它继续
  ```
  **不等蒸馏跑完是对的**：确定性事件在这之前**已经落盘**，蒸馏是加法，图可以随时从事件重建。
  新增启动期校验器把 `drain_timeout_s < shutdown_step_timeout_s` 钉死（§6.5），违反即拒绝启动
  ——这条不等式一旦倒挂，失败是完全静默的（A-47）。
- **为什么必须这样**：`stop_group` 串行遍历成员，而 `/ai-trade/{groupId}/stop` 的客户端超时是
  `FAST_MS = 20_000` 且被判为写路径——一次 45 秒的 LLM 调用（或一次 5 秒的物化 ×3 成员）会让
  停止按钮弹出「本次操作结果未知」。**在真实资金的停止/平仓按钮上制造一次假警报，代价远大于
  这个特性的全部收益。**
- **输入**：`runtime.distill_inputs()` 给出 `{instId, bar, groupId, runId, injectedKeys, trips[≤40]}`。
  `injectedKeys` 让蒸馏 prompt 排除本会话已经喂过的经验——不然模型会把喂进去的话原样吐回来，
  那正是 R-1「自我强化幻觉回路」的第一步。
- **输出**：强制工具调用 `submit_trade_lessons`，schema：
  ```
  { lessons: [ { statement: str(≤120), detail: str(≤600),
                 context: { axis: enum-value, ... },   ← 轴与取值均 enum，越界即整条丢弃
                 tripSeqs: [int] (≥1),                 ← 无引用即整条丢弃
                 polarity: "positive"|"negative" } ]   (≤ distill_max_lessons)
  }
  ```
- **落地**：`source="narrative"`, `tier="candidate"`；被拒条数累加进
  `app_settings["experience.rejected_narrative"]`，由 `GET /overview` 下发、设置中心显示（I-8）。
- **★ 不新增 WS 事件**（F-5）：会话已终结，其 WS 已关闭，且本仓库 AI-trade 无分组级实时通道。
  可见性靠 `GET /overview` 的 `lastUpdatedTs` + 设置中心刷新按钮。

### 4.10 运行时装配与**逐处接线清单**

#### `SessionGraph`（新增，`experience/service.py`）

```python
@dataclass(frozen=True)
class SessionGraph:
    """会话启动时的一次性快照：图 + 库存总量 + 快照时刻（三者同一次取数，I-9）。"""
    graph: Graph
    totals: dict          # {"lessons": int, "events": int, "sessions": int}
    as_of_ts: int
```

#### `resolve_experience_settings`（★ E-3 新增，`experience/service.py`）

```python
async def resolve_experience_settings(session: AsyncSession, settings) -> Settings:
    """把 `app_settings["experience.*"]` 并回 `Settings` 的**唯一**派生点（不变式 I-10）。

    镜像 `api/ai_auto.py::_resolve_discipline` 的成文姿态：产出**派生副本**
    （`settings.model_copy(update=...)`），**绝不 `setattr` 全局单例**——`get_settings()` 是进程级
    缓存，且 `LiveExecutor` 被 `ai_auto` 与 `live_auto` 共用同一个对象，就地改会让阈值静默溢出到
    非 AI 链路（本仓库的历史坑）。

    缺记录即回落 `config.py` 默认（沿用 `get_auto_trade_settings` 的稀疏语义）；
    越界值一律**夹紧到 `Field` 的域**并记一行 warning，绝不让一个 DB 里的坏值把会话打死。
    """
```

**为什么必须有这一步（P0-3）**：`RetrievalConfig.from_settings` / `PromoteRule.from_settings` /
`_options(settings)` 读的全是 pydantic `Settings`，而 §五 的 `PUT /api/settings/experience` 写的是
`app_settings` 稀疏 KV——**两者之间没有任何桥**。不补这一步，设置中心那个「阈值设置」面板就是一个
用户改得动、系统永远不读的假面板，而且不报错。

**四个必经调用点**（漏一个即该路径用的是旧阈值，A-41 逐条钉死）：

| # | 位置 | 顺序约束 |
|---|---|---|
| 1 | `api/ai_auto.py::build_start_request` | **在 `_resolve_discipline` 之后**（它先把 `settings` 换成纪律派生副本），本步在其结果上再派生一次 |
| 2 | `POST /api/experience/rebuild` | 取数前 |
| 3 | `POST /api/experience/preview` | 取数前——否则预演用的阈值与真实会话不同，§7.1 那一屏又变成假话 |
| 4 | manager 侧后台物化任务 | 每次任务开始时取一次（用户可能在两次物化之间改了阈值） |

#### `SessionGraph` 的取数（承 D-2）

`load_session_graph()` 返回 `SessionGraph | None`（未装配 / 无经验节点 / 取数失败 ⇒ `None`；
**取数失败一律降级为「无经验」——一个读不到历史的增强读数绝不能挡住真实资金会话的启动**）。
`totals` 取自同一事务里的 `repo.experience_overview(session)`。

#### `ExperienceRuntime` 门面（`experience/runtime.py`，签名 v3 修订）

```python
class ExperienceRuntime:
    def __init__(self, snapshot: SessionGraph | None, rule: ExperienceRule | None,
                 settings, identity: EventIdentity | None = None) -> None: ...
    # 未装配（snapshot is None / rule is None / rule.enabled is False）⇒ 以下全部空转
    @property
    def armed(self) -> bool: ...          # 读侧总闸
    @property
    def writing(self) -> bool: ...        # 写侧总闸（只读模式：用经验但不沉淀）
    def note_open(self, trade, *, seq, facts: ContextFacts) -> None: ...
    def note_close(self, trade, meta, trip) -> None: ...
    async def persist_pending(self) -> None: ...   # ★ 只 INSERT OR IGNORE，不物化（D-1）
    async def flush(self) -> None: ...             # ★ 恒等于 persist_pending()
    def take_dirty(self) -> bool: ...              # ★ 新增：自上次取走以来是否有新事件落盘
    def adopt_snapshot(self, snapshot: SessionGraph) -> None: ...  # ★ 新增：后台物化完成后单点替换
    async def persist_harvest_gap(self) -> None: ...  # ★ E-8：收尾把 harvest_gap 累加进 app_settings
    def inject(self, payload, *, seed: ContextSeed) -> None: ...
    def inject_reflection(self, payload, decision) -> None: ...
    def distill_inputs(self) -> dict | None: ...
    def seed_axes(self, *, market_context, wake_kind, horizon_profile_id, position_side) -> dict: ...
    def stats(self) -> dict | None: ...

# ★ E-13（P1-7）：开仓语境槽有界
_OPEN_SLOT_CAP = 64      # 超出即 FIFO 淘汰最老的一个，并 harvest_gap += 1（被看见的丢弃）
```

**★ E-8（P1-2）· `harvestGap` 必须能活过会话**：`harvest_gap` 今天只是 `ExperienceRuntime` 的一个
实例字段，会话一结束就归零；而 §五 的 `GET /overview` 与 §7.1 的概览条都要显示 `harvestGapTotal`
⇒ **那个数字恒为 0**，而它恰恰是用来告诉用户「有 K 笔往返没采到、这张表偏乐观」的。这是 I-8
「观测量必须有渲染落点」的镜像形态：**有渲染落点、没有取数来源**。修法：`flush()` 之后（`stop` 与
`pause` 两条路径都走得到）调用 `persist_harvest_gap()`，把本会话的增量累加进
`app_settings["experience.harvest_gap_total"]`；`service.overview()` 把它与
`app_settings["experience.materialize_health"]` 一并读出来拼进回执（A-48）。

`inject` 写入的 wire 形状（**随证据下发 `maxChars`**，与 `tacticalContext` 同范式，使渲染层只吃
payload、不必拿 `Settings`）：

```json
{
  "tier": "full|brief|repeat",
  "l0": [{"key":"lesson:ab12…","text":"…","support":23,"tStat":-2.8,"source":"stat"}],
  "l1": [ … ],
  "disputed": [{"a":"lesson:…","b":"lesson:…","axis":"regime"}],
  "totals": {"lessons":148, "events":2043, "sessions":37},
  "maxChars": {"l0":480, "l1":660},
  "graphAsOfTs": 1755000000
}
```

#### 逐处接线清单（节点 #2 照此逐条落地）

| # | 文件 · 位置 | 改动 | 硬约束 |
|---|---|---|---|
| W-1 | `api/ai_auto.py::build_start_request`（`:322-373`） | `rule = resolve_experience(body.experience, settings)`；`snapshot = await load_session_graph(session, settings=settings, rule=rule)`；两者填进 `req` | **必须在 `_resolve_discipline` 之后**——纪律预设会把 `settings` 换成派生副本，早一步读到的是旧对象 |
| W-2 | `ai_auto/manager.py::AiTradeGroupStartRequest` | `+experience_rule: ExperienceRule | None = None`、`+experience_snapshot: SessionGraph | None = None`（**均带默认 None**，直接构造该 dataclass 的既有测试逐字节零回归） | |
| W-3 | `ai_auto/manager.py::_build_member`（`:627`） | 构造 `ExperienceRuntime(req.experience_snapshot, req.experience_rule, req.settings, EventIdentity(group_id=…, run_id=run_id, inst_id=plan.inst_id, bar=req.bar))` 并透传给 `AiTradeSession` | `run_id` 在方法尾部才确定 ⇒ runtime 必须在 `run_id` 之后构造 |
| W-4 | `ai_auto/manager.py`（模块级） | `_experience_tasks: set[asyncio.Task]`、`_experience_dirty: dict[str, bool]`、`_experience_last_ts: dict[str, float]`、`_schedule_experience(coro)`、`_on_experience_dirty(group_id)`、`_experience_finalize(member)`、`drain_experience_tasks()` | 逻辑本体放 `experience/service.py`；manager 只做调度（净增预算见 §8.2） |
| W-5 | `ai_auto/manager.py::_stop_session`（`:1309`） | 返回**前** `self._schedule_experience(self._experience_finalize(session))` | **不得 await**（G-5 时延门禁） |
| **W-5b** | `ai_auto/manager.py::pause_group`（★ E-5 / P0-5） | 返回**前** `self._schedule_experience(self._experience_finalize(session, distill=False))` | `shutdown_action` 默认 `"hold"` ⇒ **关软件走的是这条路**，不挂这里等于终局物化在最常见的退出路径上不发生 |
| W-6 | `ai_auto/session.py::__init__`（`:141`） | **两个**关键字形参（★ E-12 / P1-6）：`experience: ExperienceRuntime | None = None` 与 `on_experience_dirty: Callable[[], None] | None = None` → `self._experience = experience or ExperienceRuntime(None, None, settings)`（空对象模式，读侧无需判空）、`self._on_experience_dirty = on_experience_dirty or (lambda: None)` | 两者**都带默认值** ⇒ 既有构造点零回归。v3 的 W-7 引用了 `self._on_experience_dirty()` 却没有任何条目把它装配上去——实现者只能自己发明一个接口，那正是接线清单存在的意义 |
| W-7 | `ai_auto/session.py::_dispatch`（`:343-380`） | **两处，位置不同**（★ E-12 / P1-6）：① **既有的 `else`（开仓）分支内**追加 `self._experience.note_open(trade, seq=self._position_seq, facts=…)`；② 在 `self._inject_explain_meta(trade)` 与 `await self._persist_trade(trade)` **之后**，**新开**一个 `if trade.get("side") == "close":` 块，内含 `note_close(...)` → `await persist_pending()` → `if self._experience.take_dirty(): self._on_experience_dirty()`（同步回调，**不 await**） | `exitSource` 与往返结算都在 `_inject_explain_meta` 里才写，挂早了 meta 是空的。**注意 v3 的措辞是自相矛盾的**：`_inject_explain_meta` 位于 `if/else` **之外**，所以「平仓分支……在它之后」不存在——close 侧 hook 只能是一个**新的**判据块，净增因此是 7 行而不是 6 行 |
| W-8 | `ai_auto/session.py::_build_decision_payload`（`:1959-2010`） | **最后一行**（`self._wake.inject(...)` 之后、`return payload` 之前）：`self._experience.inject(payload, seed=ContextSeed(axes=self._experience.seed_axes(...), now_wall=time.time()))` | 顺序错 ⇒ `contextDisclosure` 缺席 ⇒ 档位恒 `full` ⇒ 压缩静默失效（A-31 钉死） |
| W-9 | `ai_auto/session.py::_reflect_before_execute`（`:1413`） | 构造反思 payload 后 `self._experience.inject_reflection(payload, decision)` | |
| W-10 | `ai_auto/session.py::stop`（`:541`）· `pause`（`:626`） | `_finalize_run` 之前 `await self._experience.flush()`，紧接 `await self._experience.persist_harvest_gap()`（★ E-8） | 两者都是毫秒级本地 INSERT / UPDATE（G-5）；`harvest_gap` 不落库 ⇒ 设置中心那个数字恒为 0 |
| W-11 | `ai_auto/session.py::_status_payload`（`:2147`）· `_build_run_metrics`（`:2261`） | `"experience": self._experience.stats()`（未装配 ⇒ `None`） | |
| W-12 | `ai_auto/prompts.py` | `_format_experience(payload)`（3 行，延迟导入 `disclosure.format_experience`）；`build_decision_prompt` 在 `_format_track_record` 之后、`_digest_lines` 之前插入；`build_reflection_prompt` 在 `_format_reflection_decision` 之后追加 `_format_counter(payload.get("experienceCounter"))` | 沿用「长文本渲染下沉、`prompts.py` 只留 3 行」的既有纪律 |
| W-13 | `ai_auto/trace.py::round_start`（`:120-128`） | `"experience": payload.get("experience")` 一键 | 显式取键装配，漏抄 ⇒ 前端永远收不到，**且不报错** |
| W-14 | `main.py` | `app.include_router(experience.router, prefix="/api")`；`_shutdown_steps()` 追加 `("ai_trade_experience", …drain_experience_tasks())`，**排在 `("ai_trade", …)` 之后**；lifespan 启动段消费一次 `app_settings["experience.dirty"]`（★ E-5） | 顺序：先停会话再收后台任务。drain 的死线必须 < `shutdown_step_timeout_s`（★ E-4），否则外层先 cancel、内层清理永不执行 |
| **W-15** | `apps/web/lib/httpTimeout.ts`（★ E-11 / P1-5） | `LONG_EXACT_PREFIXES` 追加 `/experience/rebuild`、`/experience/export`、`/experience/import`；`WRITE_PREFIXES` 追加 `/experience/rebuild`、`/experience/import`。**必须走 `WRITE_PREFIXES`（前缀表）而不是 `WRITE_ACTION_FAMILIES` + `WRITE_ACTION_SUFFIXES`（家族+末段表）**：往末段表里加 `/rebuild` 会让**既有的** `/ai-trade/prior/rebuild` 也被重判为写路径，那是在一个与本特性无关的按钮上改文案；而 `/experience/*` 没有路径参数，前缀表本就是为它这种形状准备的 | 不接这一处，重建按钮在大库上**必然**假超时（F-2 自己论证过全库重建会超 20 秒），而一次超时的 `replace` 导入会被显示成「失败」而不是「结果未知」。先例：`/ai-trade/prior/rebuild` 就在那张表里 |

### 4.11 暂停 / 续跑 / 多标的并发

- **续跑（配置）**：`resume.py` 的 S2 用 `start_wire` 原文重跑 `build_start_request`
  （`api/ai_auto.py:371` 实读 `start_wire=body.model_dump(mode="json")`），`experience` 键在原文里
  ⇒ 续跑自动同配置，**这一半无需额外代码**。图会被重新载入（拿到暂停期间其它会话沉淀的新经验），
  这是期望行为。
- **续跑与离线平仓的采集缺口（必须正视）**：有两条真实路径**不会**产生经验事件，而它们**恰好
  偏向亏损样本**，不处理就是教科书式的幸存者偏差：

  | 路径 | 取证 | 为什么采不到 |
  |---|---|---|
  | 暂停期间被交易所平掉的持仓 | `resume.settle_closed_offline` | 直接 `repo.append_live_trade(...)` 落库，**完全不经 `session._dispatch`** ⇒ 无 `record_close` / `apply_trip_meta` / `note_close` |
  | 续跑接管的既有持仓 | `resume.adopt_position(...)` | 接管**不调** `_cost.note_open` ⇒ 平仓时 `settle_close` 返回 `{}` ⇒ 连 `SettledTrip` 都不存在，更没有开仓瞬刻的语境签名 |

  **v1 的处置：不补采、但如实计数并露出**。补采要么得复刻一份开仓语境（猜出来的语境比没有语境
  更坏），要么得在续跑链上新开一条采集通路（那条链跑在真实资金上，收益不抵风险）。故：
  `runtime.harvest_gap` 计数 → `status.experience.harvestGap` / `aiStats.experience.harvestGap`
  → §7.3 的灰色注记与 §7.1 概览条各有一处渲染落点（I-8）。写进 §十一 作为明确欠账。
- **幂等**：事件唯一键 `(group_id, inst_id, seq, exit_ts)` 使续跑重放不双计。
- **并发**：一个分组 N 个成员会话并行写事件。写入 `INSERT OR IGNORE`；物化走模块级
  `asyncio.Lock` + 单事务；载入是只读、无锁。**物化任务按 `group_id` 去重**，N 个成员同时变脏
  只会产生一个任务。

---

## 五、接口设计（wire）

全部走 `/api` 前缀，经既有 `OriginGuardMiddleware`（ASGI 中间件，新路由自动受保护，无需逐路由
接线）。错误体统一 `{error:{code,message}}`（`main.py` 全局处理器）。

| 方法 · 路径 | 入参 | 回执 | 说明 |
|---|---|---|---|
| `GET /api/experience/overview` | — | `{lessons:{iron,normal,candidate,muted}, events, sessions, symbols:[…], lastUpdatedTs, rejectedNarrative, excludedByCostBasis, materializeHealth:{lastTs,lastMs,events,slow,torn,overflow}, harvestGapTotal}` | 概览条。`materializeHealth` 是 F-2 的规模墙露出点（I-8），六个键在 §7.1 各有渲染落点。**★ E-8（P1-2）**：`materializeHealth` 与 `harvestGapTotal` 今天**都没有取数来源**——前者 §8.1 的 `service.py` 改动清单里漏了「读 `app_settings` 拼进回执」这一步，后者根本没有持久化写点（`harvest_gap` 只活在会话内存里）。两者必须一并补上，否则概览条上那两个数字**恒为 0**，而它们恰恰是用来说「这张表偏乐观」的 |
| `GET /api/experience/graph` | `instId? bar? axis? tier? source? q? limit=120` | `{nodes:[…], edges:[{srcId,dstId,rel,weight,support}], total, truncated}` | 图谱取数；按 `weight` 降序截断，`truncated` 如实下发。**P2-3**：`service.graph_view` 今天把 `repo.load_edges` 的行**原样**透出（`src_id` / `dst_id`，snake_case），而 `nodes` 走 `_node_wire` 是 camelCase——同一个回执两套命名。本轮统一为 camelCase（加一个 `_edge_wire`），`lib/experienceTypes.ts` 因此不必照抄一份不一致 |
| `GET /api/experience/lessons` | 同上 + `offset/limit` | `{items:[…], total}` | 列表视图（无障碍等价形态） |
| `GET /api/experience/nodes/{id}` | `evidenceLimit=20` | `{node, stats, contexts:[…], versions:[…], contradicts:[…], evidence:[{groupId,instId,bar,entryTs,exitTs,netBps,exitSource}]}` | 详情抽屉 |
| `POST /api/experience/nodes` | `{statement, detail, context:{axis:value}, polarity}` | `{node}` | 手工新增（`source="manual"`, `tier="candidate"`） |
| `PATCH /api/experience/nodes/{id}` | `{statement? detail? pinned? muted? tier?}` | `{node}` | `tier` 只允许在 `candidate/normal/iron` 间**降档**，或对 `pinned` 升 `iron` |
| `DELETE /api/experience/nodes/{id}` | — | `204` | 仅 `source ∈ {manual, narrative, imported}`；`stat` 类回 `409 EXPERIENCE_STAT_NODE_UNDELETABLE`（重建会复活，删除是假动作），提示改用「静音」 |
| `POST /api/experience/rebuild` | `{scope:"stat"}` | `{nodes, edges, stale, inserted, updated, elapsedMs, torn}` | 从事件全量重建；CPU 部分走 `run_in_threadpool`；阈值经 `resolve_experience_settings` 取（★ E-3）。**客户端必须落长跑档**（★ E-11：`lib/httpTimeout.ts` 的 `LONG_EXACT_PREFIXES` 追加 `/experience/rebuild`），否则 20 秒 `FAST_MS` 在大库上**必然**假超时——而 F-2 自己就论证了全库重建会超 20 秒 |
| `POST /api/experience/preview` | `{instId, bar, regime?, volatility?, event?, side?, wake?, horizon?, confidence?, costRegime?}` | `{tier, l0:[…], l1:[…], l0Text, l1Text, disputed:[…], chars:{l0,l1,total,budget}, seedActivation:[{key,a}], graphAsOfTs, liveSessions:[{instId, graphAsOfTs, diverged}]}` | **检索预演**；回执必须带 `graphAsOfTs` 与运行中会话的快照时刻对照 |
| `GET /api/experience/export` | `instId? since? until? kind=all\|graph\|events` | **NDJSON 流**（`StreamingResponse`，首行 `{"version":1,"exportedTs":…,"counts":{…}}`，其后每行一个 node / edge / event） | **必须流式**：跑半年的库是几万条事件，一次性拼进内存再序列化会在桌面端把整个进程顶起来；`counts` 让接收方能校验完整性。批大小 `export_page_size` |
| `POST /api/experience/import` | `{version, nodes, edges, events, mode:"merge"\|"replace"}` | `{imported:{nodes,edges,events}, rejected:{nodes,edges,events}}` | **只导入经验节点**，边与事件一律计入 `rejected`（边是派生物、事件带别人的成本口径，I-7）。见 R-8。**★ E-10（P1-4）**：R-8 列了六条防线却漏了**体量**——它是一次性 JSON body（导出是 NDJSON 流，导入不是），`nodes` 条数无上限。故：`len(nodes) > import_max_nodes`（默认 2000）⇒ 回 `422 EXPERIENCE_IMPORT_TOO_LARGE` 并在文案里给出实际条数与上限，**绝不静默截断**；`mode="replace"` 的「先删手工与导入节点、再逐条写入」必须在**同一个事务**里，中途失败整体回滚——否则留下一个「删了一半」的经验库，而用户看到的只是「导入失败」 |
| `GET /api/settings/experience` | — | `{enabled, defaultOn, writeEnabled, minSamples, ironSamples, tThreshold, halfLifeDays, staleDays, l0Max, l0MaxChars, l1Max, l1MaxChars, distillEnabled}` | 放在 `api/app_settings.py`，与 `/settings/auto-trade` 并列（一致性优先）。**`enabled` 是运维总闸，本端点上为只读**（P2-4：它可写就会与启动弹窗那个复选框互相矛盾——总闸关掉后弹窗仍显示勾选且可勾。总闸只经环境变量 / `.env` 调整，与本仓库其它「一键回滚」总闸一致） |
| `PUT /api/settings/experience` | 同上**除 `enabled` 外**的可写子集 | 同上 | 越界由 schema 触发 422。**★ E-7（P1-1）**：`l0MaxChars` / `l1MaxChars` 必须复用与 `_check_experience_budgets` **同一个**下限判据（`FRAME_OVERHEAD[layer] + MIN_ITEM_BUDGET`，从 `disclosure` 导入，绝不在 schema 里抄一份数字）——那个校验器只在 `Settings` 构造时跑，`PUT` 会**完全绕过**它，于是用户能从界面上把 L0 配成一个「永远渲染不出任何东西」的值而毫无提示 |

**「所见即 Agent 所得」必须说准**：`preview` 读的是**当前 DB 里的图**，而运行中的会话用的是
**启动那一刻载入内存的快照**（I-3 使然），两者会因后台物化与其它成员会话的写入而分叉。所以
回执带 `graphAsOfTs` 与 `liveSessions[]`（每个运行中会话的 `graphAsOfTs` 与 `diverged` 标志，
由 manager **现查内存会话**得到，不查 DB），UI 必须在分叉时明说（§7.1）。

**启动请求增量**（`AiTradeGroupStartIn` 可选字段；`AiBacktestStartIn` 同款字段但 v1 恒关）：

```jsonc
"experience": {
  "enabled": true,     // 整键缺省 ⇒ 按 ai_trade_experience_default_on（默认 true）装配
                       // ★ E-15：enabled:false = 本次会话**既不读也不写**（关就是全关）
  "scope": "symbol",   // "symbol"（默认）| "global"（放宽跨标的权重）
  "write": true        // 是否把本次会话的经验写回资产（默认 true；回测恒 false）
                       // enabled:true + write:false = 只读模式：用经验但不沉淀
}
```

**★ E-15（P1-9）· 「关闭经验模式」的语义必须被明确决定并说出来**：`rules.resolve_experience` 在
`enabled:false` 时返回 `None`，而 `ExperienceRuntime.writing` 依赖 `rule.enabled` ⇒ **关掉读的同时
也关掉了采集**。这与需求「经验是累积的数字资产」有张力，而 §7.2 的 UI（一个父复选框 + 一个「写回」
子复选框）看起来像是只关读——用户会以为收据还在攒。本轮**确认保留「关就是全关」**（理由：一个被
用户明确关掉的功能仍然往库里写，是更坏的意外；只读模式已由 `write:false` 提供），但必须补两件：
① 启动弹窗与设置中心各写一句明文「关闭后本次会话**既不读取也不沉淀**经验」；
② 「只写不读」这个第三态**明确不做**，写进 §十一。**决定可以任选，静默不可以。**

**会话状态增量**（`_status_payload` / `snapshot` / `aiStats`）：

```jsonc
"experience": {"enabled":true, "scope":"symbol", "write":true, "tier":"full",
               "l0":3, "l1":2, "injections":41, "chars":312,
               "eventsHarvested":7, "harvestGap":2, "graphAsOfTs":1755000000}
```

注意 `harvestGap` 在这里是**本会话**的增量，而 `GET /overview` 的 `harvestGapTotal` 是**库存累计**
（由 E-8 的 `persist_harvest_gap()` 在会话收尾累加）。两者口径不同、**都要有**：前者回答「这次跑
漏了几笔」，后者回答「这张表整体偏乐观多少」。任何一个缺席，§7.1 或 §7.3 就有一处渲染落点拿不到
数字——而按 I-8，那等于死码。

`store.patchAiTradeMemberStatus` 实读为 `{...m.session, ...status}` 展开而非逐键手抄
（`store.ts:963-970`），故只要 `AiTradeSessionRow` 的 TS 类型补上该字段，新键即可安全流到前端。

---

## 六、数据模型

三张新表，**全部是新表、不给既有表加列** ⇒ `create_all` 自动建、老库首次启动即补、零迁移代码、
不可能 `no such column`（`db/session.py` 的四个 `_ensure_*_columns` 补丁就是为「事后加列」存在的）。
**这三张表已在工作树里实现（`db/models.py` +118 行），本轮零改动**，下表为契约留档。

### 6.1 `ai_experience_events`（原子事实，append-only，唯一真源）

| 列 | 类型 | 说明 |
|---|---|---|
| `id` | Integer PK autoincr | |
| `group_id` / `run_id` | String(48) index / Integer index | 溯源到一次 AI 交易与一条 run |
| `inst_id` / `bar` | String(32) / String(8) | |
| `seq` | Integer | 会话内持仓序号（epoch 身份的一半） |
| `origin` | String(8) default `"live"` | `live`\|`backtest`\|`adopted`。**v1 恒为 `live`**；保留它的理由不是「已有第二个取值」，而是**事后加列必须写 ALTER 补丁，而现在加它代价为零**。聚合层照写 `WHERE origin='live'`，语义是口径闸 |
| `entry_ts` / `exit_ts` | Integer | 秒级 UTC（bar 时钟） |
| `entry_wall_ts` / `exit_wall_ts` | Integer | 墙钟（时间衰减用；双时钟不混用） |
| `side` | String(8) | `long`\|`short` |
| `context_json` | Text | 开仓瞬刻冻结的九轴语境签名 |
| `outcome_json` | Text | `SettledTrip` 的 wire |
| `cost_basis` / `fee_known` | String(8) / Boolean | I-7 的过滤依据 |
| `net_bps` / `gross_bps` / `cost_bps` / `edge_error_bps` / `hold_seconds` | Float(nullable) / Float | 从 `outcome_json` **提列**——聚合是热路径，逐行解 JSON 会让重建在几万条事件上肉眼可见地慢 |
| `exit_source` | String(16) | `EXIT_SOURCES` 九值之一；同时进 `context_json` 的 `exit` 轴 |
| `created_ts` | Integer | |

约束：`UniqueConstraint(group_id, inst_id, seq, exit_ts, name="uq_ai_exp_event")`；
`Index("ix_ai_exp_event_scope", inst_id, bar, exit_ts)`；`Index("ix_ai_exp_event_group", group_id)`。

### 6.2 `ai_experience_nodes`（物化图节点）

| 列 | 类型 | 说明 |
|---|---|---|
| `id` | Integer PK | |
| `kind` | String(8) | `context` \| `lesson` |
| `key` | String(96) **unique** index | `axis:value` 或 `lesson:<12hex>` |
| `axis` / `value` | String(24) / String(64) nullable | 仅 context 节点 |
| `label` / `detail` | String(200) / Text | 展示句（≤120 字）/ 长文（≤600 字） |
| `source` | String(12) | `stat`\|`narrative`\|`manual`\|`imported`\|`context` |
| `scope` | String(8) | `symbol` \| `global` |
| `tier` | String(12) | `candidate`\|`normal`\|`iron`\|`archived` |
| `pinned` / `muted` | Boolean | 用户意志（覆盖自动档位） |
| `mute_reason` | String(24) nullable | `user`\|`stale`\|`flipped` |
| `support` / `weight` | Integer / Float | 证据条数（排序热路径去规范化）/ `evidence(l)` ∈ [0,1] |
| `stats_json` / `signature_json` | Text | 桶统计全量 / 语境签名 |
| `first_ts` / `last_seen_ts` | Integer | **墙钟秒**。`last_seen_ts` = 该经验**最近一条支撑事件**的 `exit_wall_ts`，**不是**最近一次物化的时刻（后者由 `updated_ts` 承载）。两者若被合成一个，`recency` 会在每次重建后被拨回 now ⇒ 衰减恒为 1 ⇒ 三年前的教训与昨天同权，**且不会报错** |
| `prev_sign` / `sign_streak` | Integer / Integer | 上次物化时 `meanNetBps` 的符号 / 符号连续一致次数（`iron` 门槛第三条读它） |
| `created_ts` / `updated_ts` | Integer | `created_ts` 在 upsert 时**绝不覆盖** |

索引：`ix_ai_exp_node_pick(kind, tier, muted)`、`ix_ai_exp_node_axis(axis, value)`。
**upsert 绝不覆盖的字段**：`pinned`、`muted`、`mute_reason`（`user` 来源的）、`first_ts`、
`created_ts`；`prev_sign`/`sign_streak` 按 §4.7 前滚。数据层为此提供**单点** upsert 函数
`upsert_lesson_nodes`，编排层禁止写第二份 SQL。

### 6.3 `ai_experience_edges`（物化图边）

| 列 | 类型 | 说明 |
|---|---|---|
| `id` | Integer PK | |
| `src_id` / `dst_id` | Integer FK(`ai_experience_nodes.id`) index | |
| `rel` | String(16) | `applies_in`\|`co_occurs`\|`contradicts`\|`refines` |
| `weight` / `support` | Float / Integer | §4.5 各自定义域 / 共现支撑条数 |
| `updated_ts` | Integer | |

`UniqueConstraint(src_id, dst_id, rel, name="uq_ai_exp_edge")`、`Index("ix_ai_exp_edge_src", src_id, rel)`。

### 6.4 设置与运行健康的持久化

复用既有 `app_settings` 稀疏 KV 表。键：
`experience.enabled` / `.default_on` / `.write_enabled` / `.min_samples` / `.iron_samples` /
`.t_threshold` / `.half_life_days` / `.stale_days` / `.l0_max` / `.l0_max_chars` / `.l1_max` /
`.l1_max_chars` / `.distill_enabled` / `.cost_regime_cuts`（物化时重算回写，只读缓存）/
`.rejected_narrative`（蒸馏越界计数）/ **`.materialize_health`**（F-2 的规模墙露出点：
`{lastTs, lastMs, events, slow, torn, overflow}`——后两键来自 E-14 / E-6）/
**`.harvest_gap_total`**（★ E-8 新增：会话收尾累加，`GET /overview` 的 `harvestGapTotal` 的**唯一**
取数来源；不加这一项，概览条那个数字恒为 0）/ **`.dirty`**（★ E-5 新增：去抖攒下的脏标志必须能跨
进程活下来，否则关软件即丢；由 `main` 的 lifespan 启动段消费）。读取方缺记录即回落 `config.py`
默认（沿用 `get_auto_trade_settings` 的稀疏语义）。

**★ E-3（P0-3）· 这些键必须有读点**：`.min_samples` / `.iron_samples` / `.t_threshold` /
`.half_life_days` / `.stale_days` / `.l0_max` / `.l0_max_chars` / `.l1_max` / `.l1_max_chars` /
`.default_on` / `.write_enabled` / `.distill_enabled` 共 12 项是**用户在设置中心可写**的，而运行时
真正读的是 pydantic `Settings`——两者之间必须有 §4.10 的 `resolve_experience_settings` 这座桥，
否则整块面板是个「改得动、永远不生效、还不报错」的假面板（不变式 I-10）。

### 6.5 `config.py`（现有 27 项 + 本轮新增 3 项 = **30 项**）

现有 27 项已在工作树中实现，取值见 `config.py` 的 `# ---- 交易经验上下文图 ----` 段：
`enabled` / `default_on` / `write_enabled` / `min_samples`(8) / `iron_samples`(20) /
`t_threshold`(2.0) / `iron_t_threshold`(2.5) / `half_life_days`(30) / `stale_days`(90) /
`l0_max`(3) / `l0_max_chars`(480) / `l1_max`(3) / `l1_max_chars`(660) /
`reflection_max_chars`(240) / `seed_expand_alpha`(0.5) / `co_edge_min_weight`(0.15) /
`co_edge_min_support`(5) / `max_expanded_contexts`(24) / `candidate_cap`(200) /
`mmr_lambda`(0.5) / `graph_node_cap`(4000) / `global_min_symbols`(3) / `distill_enabled` /
`distill_timeout_s`(45) / `distill_max_lessons`(5) / `cost_regime_min_samples`(30) /
`export_page_size`(500)。

**本轮新增三项**（全部服务于 F-1 / F-2 的修法）：

```python
# 后台物化去抖：距上次成功物化不足该秒数 ⇒ 只置脏标志不起任务（防每笔平仓触发一次全库重建）。
ai_trade_experience_materialize_min_interval_s: float = Field(default=60.0, ge=0, le=3600)
# ★ E-6 修订语义：这不是「CPU 段的硬超时」——`asyncio.timeout` 停不掉 `run_in_threadpool` 里
# 已经开跑的线程。它只约束「排队等 materialize_lock 最多等多久」，等不到就放弃本次（脏标志留着）。
ai_trade_experience_materialize_timeout_s: float = Field(default=20.0, gt=0, le=300)
# 慢物化告警线：单次耗时超过它即在 materialize_health.slow 置真，UI 明示规模墙（I-8）。
ai_trade_experience_materialize_slow_ms: int = Field(default=3000, ge=100, le=600000)
```

**★ v4 评审再新增六项（30 → 36）**：

```python
# ---- E-2（P0-2）：让非 stat 经验真的能被检索到 ----
# manual / imported / narrative 出生即 support=0、weight=0.0，乘进 score 恒为 0 并被
# `score > 0` 剔除 ⇒ 蒸馏 / 手工新增 / 导入三条写入路径全是只写不读。给它们一个起始证据权重，
# 语义是「没有统计支撑、但有人明确写下来」——刻意低于一条 n=8 / |t|=2.0 的统计经验。
ai_trade_experience_manual_seed_weight: float = Field(default=0.35, ge=0, le=1)
# 用户显式置顶 = 用户提供的证据。不设这个下限，I-4 承诺的「升 iron 只有一条路径——用户显式
# 置顶」在代码里根本不存在（§7.1 那句「置顶的经验不受证据门槛约束」也就是假的）。
ai_trade_experience_pinned_evidence_floor: float = Field(default=0.60, ge=0, le=1)

# ---- E-4（P0-4）：关停 drain 的死线，**必须** < shutdown_step_timeout_s（默认 20.0）----
# 倒挂时外层先 cancel，drain 自己那半段「cancel 剩余任务」永远不执行，后台任务活着穿过
# dispose_engine。姿态与 main._cancel_background 的 5.0 秒逐字一致：先 cancel、再有界收敛。
ai_trade_experience_drain_timeout_s: float = Field(default=5.0, gt=0, le=60)

# ---- E-6（P0-6）：自动物化的规模闸 ----
# 超过即**不自动物化**，只在 materialize_health.overflow 置真并由 UI 明示。宁可停下来说清楚，
# 也不要一个永远跑不完、每 60 秒还叠一个的后台任务。
ai_trade_experience_materialize_max_events: int = Field(default=200000, ge=1000, le=5000000)

# ---- E-10（P1-4）：导入是不可信输入，体量也是 ----
ai_trade_experience_import_max_nodes: int = Field(default=2000, ge=1, le=100000)

# ---- E-15（P1-9）：启动弹窗与设置中心那句明文的开关（默认开；关掉只是不显示那行说明）----
ai_trade_experience_disclose_off_semantics: bool = True
```

**★ E-7（P1-1）· `FRAME_OVERHEAD` 必须订正**（`experience/disclosure.py`）：

```python
# 实算（节点 #1 evidence）：
#   L0 页眉 "【交易经验 · 铁律】（来自 37 组有成交记录的自动交易的 2043 笔已结算往返）" = 40 字，
#          totals 各取 7 位数时 = 46 字；L0 页脚 = 38 字  ⇒ 真实框架 78～84
#   L1 页眉 13 + 页脚 20 = 33
# v3 声明的 {"l0": 70, "l1": 46} 使 l0_max_chars ∈ [194, 201] **通过**启动期校验却整层渲染不出
# 任何东西——正是 I-6 规则② 声称「拒绝启动、不静默降级」要消灭的形态。宁可多留，绝不少留。
FRAME_OVERHEAD = {"l0": 96, "l1": 40}
```

**三条由 `Field` 与启动期校验器共同守住的自洽性约束**（违反即拒绝启动，**不静默降级**）：
1. `l0_max_chars ≥ FRAME_OVERHEAD["l0"] + MIN_ITEM_BUDGET` 且 L1 同理（**已实现**，
   `_check_experience_budgets`）；
2. `materialize_timeout_s` 与 `distill_timeout_s` **只被后台任务读**，绝不出现在任何同步等待
   `stop()` 的路径上（G-6②③ 源码扫描钉死）；
3. `materialize_min_interval_s = 0` 是合法的（开发调试用），但 §九 的 G-5 时延门禁必须在**默认
   配置**下跑。
4. **★ E-4 新增**：`ai_trade_experience_drain_timeout_s < shutdown_step_timeout_s`。这条不等式
   一旦倒挂，失败是**完全静默**的（外层 cancel 掉 drain，内层清理不执行，日志只多一行
   「shutdown step … did not settle」）。由启动期校验器守，违反即拒绝启动。
5. **★ E-7 新增**：`FRAME_OVERHEAD[layer] ≥ len(最坏页眉) + len(页脚)`。由一条**单测**守
   （A-39），而不是靠人肉核对——页眉长度随 `sessions` / `events` 的位数变化，任何一个写死的常量
   都必须被一条会随渲染函数一起演化的用例钉住。

---

## 七、前端设计

> 设计基调：沿用现有设计令牌（`--surface` / `--surface-2` / `--border` / `--accent` /
> `--accent-soft` / `--accent-ink` / `--muted` / `--warn`），**不引入任何新颜色、不引入任何新
> 三方库**。破坏性操作（删除、替换式导入、重建）一律走既有 `ui/ConfirmDialog`，**不得**使用
> `window.confirm`（全仓已清零原生阻塞对话框）。所有异步按钮走既有 `httpTimeout` 包装，超时文案
> 说「未知」而不是「失败」（写操作超时不等于没写成）。
>
> **★ E-11（P1-5）· 「走既有包装」不等于「接线完成」**：`lib/httpTimeout.ts` 是一张**逐条列举**
> 的路径表，新路径不登记就落默认档。今天 `/experience/*` 一条都不在表里 ⇒ 全部落 `FAST_MS`
> (20 秒)，而 F-2 自己论证了全库重建在大库上超 20 秒是常态 ⇒ **重建按钮必然假超时**；且
> `/experience/*` 匹配不上任何 `WRITE_*` 表 ⇒ 一次超时的 `replace` 导入（它会先删除既有手工与
> 导入节点）会被显示成「**失败**」而不是「结果未知」。两处登记，缺一即静默不完整：
> `LONG_EXACT_PREFIXES` 追加 `/experience/rebuild`、`/experience/export`、`/experience/import`；
> `WRITE_PREFIXES` 追加 `/experience/rebuild`、`/experience/import`。
> 先例就在同一张表里：`/ai-trade/prior/rebuild`（长跑档）。
>
> **写类判定必须走前缀表，不能走「家族 + 末段」表**：往 `WRITE_ACTION_SUFFIXES` 里加 `/rebuild`
> 会让**既有的** `/ai-trade/prior/rebuild` 一并被重判为写路径——那是在一个与本特性无关的按钮上
> 悄悄改掉超时文案。`/experience/*` 没有路径参数，前缀表本就是为这种形状准备的
> （那张末段表的存在理由是 `/ai-trade/{groupId}/stop` 这类**带 id** 的会话动作）。

### 7.1 设置中心 →「交易经验库」

`SettingsNav.SECTIONS` 追加一项，`SettingsPanel` 的三元分支链追加一条（**两处登记，缺一即静默
不可达**）：

```tsx
// SettingsNav.tsx（现 5 项 → 6 项，插在 auto-trade 之后、about 之前）
{ key: "experience", label: "交易经验库", desc: "AI 沉淀的经验与教训" },
// SettingsPanel.tsx
) : section === "experience" ? (<ExperienceSection />
```

布局（自上而下）：

1. **概览条**：五个统计块（铁律 / 常规 / 候选 / 已沉降 / 事件样本）+ 覆盖标的 chips +
   「最近更新」相对时间。右侧工具区：`重建图谱` / `导出` / `导入` / `阈值设置`（折叠面板）。
   下方一行细灰注记，承载三个诚实性键（I-8）：
   - `excludedByCostBasis > 0` ⇒ 「另有 N 笔往返因成本口径不可比未计入统计」；
   - `harvestGapTotal > 0` ⇒ 「另有 K 笔往返因续跑/离线平仓未能采集」；
   - `materializeHealth` ⇒ 「上次重建：08-24 21:30 · 耗时 1.2s · 2043 条事件」，`slow=true` 时
     升级为 `--warn` 色注记「经验库规模已接近自动重建预算，建议手动重建或导出归档」；
     **★ E-14** `torn=true` ⇒ `--warn`「上次重建未走完全部步骤，图可能不完整（新经验可能暂时
     检索不到），建议手动重建」；**★ E-6** `overflow=true` ⇒ `--warn`「经验库已超过自动重建规模
     （N 条事件 > 上限 M），自动重建已停止，请手动重建或导出归档」。
     **这三个状态位的存在理由是同一条**：一个跑不完的后台任务如果不说话，用户看到的就是
     「经验不再更新了，但哪里都没有报错」。
2. **工具栏**：标的下拉、周期下拉、坐标轴多选 chips、档位段控（全部/铁律/常规/候选/已沉降）、
   来源段控（统计/叙事/手工/导入）、搜索框（对 `label` + `detail` 子串匹配，300ms 防抖）。
3. **主区 `Tabs`**（复用 `ui/index.tsx` 的 `Tabs`，该原语已有 `role="tablist"` 与方向键 roving）：
   - **图谱**：SVG 力导向图。
     - 布局：**确定性**——种子取自 `node.key` 的哈希，固定迭代 300 步的 Fruchterman–Reingold
       简化版（`lib/experienceGraph.ts`，纯函数、可单测），**同一份数据每次渲染位置完全一致**，
       **（P2-8 · 评审留档，不阻断）**：这是本特性里最重的一段自研代码（120 节点 × 300 迭代
       ≈ 4×10⁶ 次浮点运算）。需求只要求「可视化管理」，而一个**确定性放射 / 分层布局**（语境节点
       按 `axis` 分环、经验节点挂在其一跳邻域的角平分线上）能以约十分之一的代码量拿到同等甚至更好
       的可读性，且**天然免疫**入参顺序敏感（A-20 因此不可能变成一条时红时绿的用例）。
       节点 #2 可自行取舍：**若 FR 的实现超过 200 行或 A-20 出现任何不稳定，立即换放射布局**——
       它是加分项，不是承重墙。
       不会在筛选切换时乱跳（这是图可视化最常见的体验缺陷）。确定性的两个前提**必须写死**：
       ① 布局前把节点按 `node.key`、边按 `(src, dst, rel)` 做**全序排序**（后端回执的顺序不是
       契约，输入顺序一变坐标就变，A-20 会成为一条时红时绿的用例）；② 布局是
       `useMemo(…, [nodes, edges])` 的产物，**绝不在 render 里跑**（120 节点 × 300 迭代 ≈
       4×10⁶ 次浮点运算，落在 render 路径上会让筛选切换掉帧）。
     - 视觉：语境节点 = 小圆角矩形 chip，按 `axis` 取 11 种色相（**由既有语义色令牌的色相环推导**，
       不硬编码新颜色）；经验节点 = 圆点，半径 `∝ √support`，环色表示 `tier`（铁律 `--accent`、
       常规 `--muted`、候选虚线环、已沉降 30% 透明）。边：`applies_in` 实线（不透明度 = weight）、
       `co_occurs` 细虚线、`contradicts` 用 `--warn` 双色警示线、`refines` 箭头。
     - 交互：滚轮缩放（0.4×–3×）、拖拽平移、点击节点 → 高亮 1 跳邻域并打开详情抽屉、双击语境
       节点 → 加入筛选。**键盘**：`Tab` 在节点间按 `weight` 序移动（每个节点是
       `<g role="button" tabIndex={0}>`），`Enter` 打开抽屉，`Esc` 关闭（**只关最顶层弹层**，
       沿用既有 Esc 栈纪律）。
     - 性能与诚实：默认渲染上限 120 个节点；后端回 `truncated:true` 时**在图上明示**「已按权重
       展示前 120 / 共 N 个节点」并给「提高上限」按钮——**绝不静默截断**（静默截断读起来像
       「全都在这儿了」）。
   - **列表**：与图谱**同数据源同筛选**的可访问表格（陈述 / 档位 / 来源 / 样本 n / 净额均值 /
     t / 覆盖标的 / 最近证据时间 / 操作）。这是图谱的**无障碍等价形态**——只有图的界面对读屏
     用户不可用，而「符合人机交互最佳实践」要求二者并存。行操作：置顶 / 静音 / 编辑 / 删除。
   - **检索预演**：左侧一组语境选择器（标的 / 周期 / 体制 / 波动 / 事件 / 方向 / 唤醒 / 置信度 /
     成本档），右侧**逐字渲染**下一轮 Agent 会看到的 L0 / L1 文本块，底部一条字符预算进度条
     （`312 / 480 字`）与被压制条目（`suppressed` / `disputed`）的说明。**这是本特性最有说服力
     的一屏**：它把「Agent 到底记住了什么」从黑箱变成可核对的事实。
     **这一屏的可信度取决于它说不说实话**：预演读的是**当前**经验库，运行中的会话用的是**启动
     那一刻**的快照。故顶部必须有一条状态行，取 `preview.liveSessions[]` 现渲染：
     - 无运行中会话 ⇒ 「这就是下一次启动会话时 Agent 会看到的内容」；
     - 有会话且 `diverged=false` ⇒ 「这就是 BTC-USDT-SWAP 的 Agent 下一轮会看到的内容」；
     - 有会话且 `diverged=true` ⇒ **「BTC-USDT-SWAP 的会话正在使用 08-24 21:30 的经验库快照，
       与下方预演（当前库）不同——新沉淀的 3 条经验要到下次自动重建或下次启动会话时才会生效。」**

     少了这条状态行，这一屏在最需要它的时刻（会话正在跑）说的是假话，而**一屏假话比没有这一屏
     更坏**。
4. **详情抽屉**（右侧 `Modal` 变体，宽 460px）：陈述 / 长文 / 统计（n、胜率、净额均值与中位、t、
   平均持仓、成本占比、edge 误差、`excluded`、`costRegimeFallback`）/ 语境签名 chips / 版本链
   （`refines`）/ 冲突（`contradicts`，附「两条都不进纲要」的说明）/ 溯源证据表（最近 20 笔，
   点击跳到 AI 交易历史详情）/ 操作区（置顶 · 静音 · 编辑 · 删除）。

### 7.2 启动弹窗「经验模式」

新增组件 `components/Agent/AiTradeExperienceFields.tsx`（镜像 `ProfitGuardFields` /
`AiTradeHorizonFields` 的既有**受控组件**形态），插在【交易周期模式】之后、【上下文轮数】之前：

```
☑ 启用经验模式（推荐）                                   [查看经验库 →]
  让 Agent 参考过往 37 次自动交易沉淀的 148 条经验（当前标的 3 条铁律）。
  ○ 仅本标的经验（推荐）      ○ 含跨标的通用经验
  ☑ 把本次会话的经验写回经验库
  预计每轮额外上下文：约 312 字（上限 480 字）           [预演 →]
```

- 默认勾选（`AI_TRADE_EXPERIENCE_DEFAULTS.enabled = true`），落 `AiTradeLaunchOpts.experience`；
  取消勾选 ⇒ 提交 `{enabled:false}`（**显式关闭**，与「字段缺省 = 默认开启」严格区分——这是本
  仓库在 `baseRules` / `profitGuard` / `tradingHorizon` 上已经踩过三次的语义）。
- **★ E-15（P1-9）· 取消勾选时必须现出一行明文**：「本次会话**既不读取也不沉淀**经验——这段时间
  的交易不会进入经验库。」理由：`writing` 依赖 `rule.enabled`，关掉读的同时也关掉了采集，而这个
  卡片的形状（父复选框 + 「写回」子复选框）看起来像是只关读。**不写这一行，用户会以为收据还在
  攒**——而经验库最贵的东西就是那些收据。只想「用但不沉淀」的用户走子复选框（`write:false`）。
- **★ P2-4 · 运维总闸关闭时这个复选框不能装作能用**：弹窗打开时那次 `GET /overview` 若回
  `403`/`404`（路由未注册）或 `GET /settings/experience` 回 `enabled:false`，整张卡片降级为
  **禁用态 + 一行灰字**「经验模式已被运维关闭」，而不是显示一个勾上了却毫无作用的复选框。
- 条数与字符预估来自 `GET /api/experience/overview` + `POST /api/experience/preview`（弹窗打开时
  各取一次；**接口失败 ⇒ 只隐藏这两行数字，不影响勾选与启动**）。
- 偏好记忆：`AiTradeConfig.experience?: AiTradeExperienceRule`（**可选字段**，读点一律
  `?? AI_TRADE_EXPERIENCE_DEFAULTS` 兜底，**不改 persist version**——沿用 `baseRules` 先例，
  `store.ts` 现为 `version: 30`，动 migration 链的风险远大于收益）。
- 回测 tab：显示同一张卡但 `write` 恒关且不可勾（附说明「回测样本不写入经验库」），`enabled`
  在 v1 亦恒关（理由见 §十一.2）。

### 7.3 运行中的可见性

- `AiTradeStatsLive` 追加一张 `ExperienceCard`：「经验模式 已启用 · 本轮喂入 3 条（312 字）·
  本次会话已沉淀 7 条经验事件」，未启用时**整卡不渲染**。卡片还必须承载两个诚实性键（I-8）：
  - `harvestGap > 0` ⇒ 追加一行灰色注记「另有 2 笔往返因续跑/离线平仓未能采集」+ 说明气泡；
  - `graphAsOfTs` ⇒ 卡片副标题「经验库快照：08-24 21:30」，与 §7.1 预演状态行同一口径。
- `AiTradeAgentTrace` 的轮卡：`round_start` 的 `experience` 键以一枚可展开的「经验 ×3」徽标呈现，
  展开即**逐条显示本轮实际喂入的文本**（**加法键，不新增 trace phase**——新增相位的代价是前端
  多处镜像点，`trace.py` 已有成文决策）。**用户必须能看到 Agent 到底被喂了什么**。

### 7.4 前端净增预算（**硬判据，不是建议**）

实测三个前端文件**已超** CLAUDE.md 的 800 行阈值，故：

| 已超阈文件 | 现行数 | 本特性允许的净增 | 硬约束 |
|---|---|---|---|
| `lib/aiTradeTypes.ts` | **1787** | **+2** | 经验层的 TS 类型**全部**住在新文件 `lib/experienceTypes.ts`；此处只允许 `AiTradeConfig.experience?` 与 `AiTradeSessionRow.experience?` 两行（类型从 `experienceTypes.ts` import） |
| `components/Agent/AiTradeLaunchModal.tsx` | **1578** | **≤ +12** | 卡片本体在 `AiTradeExperienceFields.tsx`；此处只允许「一条 state + 一个渲染插入点 + 一处 body 组装」。做不到就把状态整体下沉进受控组件契约 |
| `lib/aiTrade.ts` | **1412** | **0** | `AI_TRADE_EXPERIENCE_DEFAULTS` 与所有格式化函数住在新文件 `lib/experience.ts` |
| `lib/store.ts` | **1431** | **0** | `patchAiTradeMemberStatus` 已是 `{...m.session, ...status}` 展开，新键自动流过，**一行不加**；**不改 `version: 30`** |
| `lib/httpTimeout.ts`（★ E-11） | 178 | **+5** | `LONG_EXACT_PREFIXES` 三行 + `WRITE_PREFIXES` 两行。该文件未超阈，但它是一张**逐条列举**的表：不登记 = 静默落默认档，且 `tsc` / `vitest` 都不会红（这正是它自己的 D3 注释里写着的那类失败） |

---

## 八、文件 / 模块变更计划

### 8.1 后端 · 已在工作树中存在（本轮**保留**，仅按 §2.2 修法微调）

| 文件 | 现行 | 本轮改动 |
|---|---|---|
| `ai_auto/experience/taxonomy.py` | 191 | — |
| `ai_auto/experience/events.py` | 198 | — |
| `ai_auto/experience/aggregate.py` | 381 | — |
| `ai_auto/experience/graph.py` | 382 | **删** `Graph.merge_lessons`（死码，F-6）：−23 |
| `ai_auto/experience/promote.py` | 152 | — |
| `ai_auto/experience/retrieval.py` | 281 | — |
| `ai_auto/experience/disclosure.py` | 189 | — |
| `ai_auto/experience/distill.py` | 204 | — |
| `ai_auto/experience/rules.py` | 65 | — |
| `ai_auto/experience/runtime.py` | 286 | **D-1**（`persist_pending` 去物化、`flush` 收窄）、**D-2**（`__init__` 收 `SessionGraph`、`inject` 用真 totals）、**D-4**（`_take_slot` 退化匹配收紧）、新增 `take_dirty` / `adopt_snapshot`：净 −10 |
| `ai_auto/experience/service.py` | 313 | **D-2**（`SessionGraph` 数据类 + `load_session_graph` 返回它）、**D-3**（新增 `materialize_bounded(settings, *, on_snapshot)`：去抖判定 + 健康写回）、新增 `export_stream(...)` 生成器：+95 |
| `ai_auto/experience/__init__.py` | 38 | 再导出 `SessionGraph` / `materialize_bounded`：+4 |
| `db/ai_experience_repositories.py` | **452**（P2-1 订正） | 新增 `load_nodes_page(offset, limit)` 与 `unmute_reappeared(keys)`（★ E-9）：+35。**`count_sessions()` 不必新增**——`overview()` 里已有那条 `COUNT(DISTINCT group_id)`（P2-2） |
| `db/models.py` | +118（已加） | — |
| `db/repositories.py` | +23（已加），实测全文件 **1052** 行 | **★ E-1（P0-1，第 0 步）**：再导出 **+3** —— `upsert_lesson_nodes`（**它今天缺席，而它是物化的唯一写路径**）、`load_nodes_page as load_experience_nodes_page`、`unmute_reappeared as unmute_reappeared_experience_lessons`。P2-7：v3 在 §2.1 说 +1、在本表说 +2，两处都不对 |
| `config.py` | +57（已加） | +6 项配置（★ E-2/E-4/E-6/E-10/E-15）+ 2 条新校验器（★ E-4/E-7）：+34 |
| `ai_auto/attribution.py` · `cost_runtime.py` · `decider.py` | +9 / +8 / +10（已加） | — |

### 8.2 后端 · 本轮新建 / 接线（**净增预算是硬判据**）

| 文件 | 意图 | 预估行 / 净增 |
|---|---|---|
| `api/experience.py`（新建） | 11 个端点（含 NDJSON `StreamingResponse` 导出）；每个读阈值的端点先经 `resolve_experience_settings`（★ E-3） | 360 |
| `schemas.py` | `AiTradeExperienceIn` + 端点 In/Out 模型 + `AiTradeGroupStartIn.experience` + `AiBacktestStartIn.experience` | +185 |
| `api/app_settings.py`（42 行） | `GET/PUT /settings/experience`（与 auto-trade 并列） | +45 |
| `main.py` | `include_router` 一行 + 关停链追加一步 | **+5** |
| `api/ai_auto.py`（**803 行，已超阈**） | `build_start_request` 内 2 处解析 | **+6** |
| `ai_auto/manager.py`（**1774 行，已超阈**） | 请求 +2 字段；`_build_member` 构造并透传（含 `on_experience_dirty` 回调，★ E-12）；模块级任务集与 4 个薄调度函数（**逻辑本体在 `experience/service.py`**）；`_stop_session` **与 `pause_group`** 返回前各 spawn（★ E-5）；`drain_experience_tasks` 改为先 cancel 后有界收敛（★ E-4） | **+46** |
| `ai_auto/session.py`（**2409 行，已超阈**） | `__init__` **+2 形参** +3 行（★ E-12）；`_dispatch` 两处 hook **+7**（close 侧是一个**新的** `if side == "close"` 块，不是既有分支内）；`_build_decision_payload` +4；`_reflect_before_execute` +2；`stop`/`pause` **+6**（含 `persist_harvest_gap`，★ E-8）；`_status_payload` +2；`_build_run_metrics` +2 | **+26** |
| `ai_auto/prompts.py`（**846 行，已超阈**） | `_format_experience` / `_format_counter` 两个 3 行下沉 + 两个插入点 | **+16** |
| `ai_auto/trace.py` | `round_start` +1 键 | +3 |

四个超阈后端文件合计净增 **94 行**（`session.py` 26 + `manager.py` 46 + `prompts.py` 16 +
`api/ai_auto.py` 6）——v4 相对 v3 的 +12 全部来自评审修法（E-5 的第二个 spawn 点、E-12 的回调形参
与新判据块、E-8 的收尾一行、E-4 的 drain 改写），**没有一行是新功能**。这是本设计最重要的结构约束：**所有逻辑必须在新包里，超阈文件只做接线**；
任何超出都改为再下沉一层，而不是放宽阈值（`prior_service.py` 的成文先例：「超了就再下沉一层
而不是再放宽」）。**`db/session.py` 零改动**（`create_all` 自动建新表，不需要 `_ensure_*` 补丁）。

### 8.3 前端 · 本轮新建（全部为新文件，各自 ≤ 800 行、单函数 ≤ 50 行）

| 文件 | 意图 |
|---|---|
| `lib/experienceTypes.ts` | 全部 wire 类型（rule / status / node / edge / overview / preview / graph） |
| `lib/experienceApi.ts` | REST 客户端（沿用 `autoTradeSettingsApi` 的 `httpTimeout` + 错误体解析范式） |
| `lib/experienceGraph.ts` | **确定性**力导向布局 + 邻域高亮 + 视口变换（纯函数、可单测） |
| `lib/experience.ts` | `AI_TRADE_EXPERIENCE_DEFAULTS` + 轴/取值中文展示名 + 档位与来源标签 + 字符预算格式化 |
| `components/Settings/ExperienceSection.tsx` | 区块外壳：概览条 + 工具栏 + Tabs |
| `components/Settings/experience/ExperienceToolbar.tsx` | 筛选 / 搜索 / 重建 / 导入导出 |
| `components/Settings/experience/ExperienceGraphCanvas.tsx` | SVG 图谱（缩放/平移/键盘/截断提示） |
| `components/Settings/experience/ExperienceList.tsx` | 可访问表格视图 |
| `components/Settings/experience/ExperienceDetailDrawer.tsx` | 详情抽屉（统计 / 版本链 / 冲突 / 溯源） |
| `components/Settings/experience/ExperienceEditor.tsx` | 手工新增 / 编辑表单（语境选择器受闭合词表约束） |
| `components/Settings/experience/ExperienceRetrievalPreview.tsx` | 检索预演 + 字符预算条 + 分叉状态行 |
| `components/Agent/AiTradeExperienceFields.tsx` | 启动弹窗「经验模式」卡片（受控组件） |
| `components/Agent/aiTradeStats/ExperienceCard.tsx` | 运行中会话的经验卡 |
| **接线**：`SettingsNav.tsx`(+1) · `SettingsPanel.tsx`(+2) · `AiTradeLaunchModal.tsx`(≤+12) · `lib/aiTradeTypes.ts`(+2) · `lib/aiTradeApi.ts`(+1) · `components/Agent/AiTradeTraceRound.tsx`(+~15) | |

---

## 九、测试与验收标准

> **当前基线：本特性零测试。**节点 #2 的第一件事是给 §2.1 判为「保留」的 9 个纯核心模块补测试，
> 而不是继续写新代码——没有测试的 2680 行纯核心在评审面前等于零。

### 9.1 门禁（每条须贴实测输出，**不采信上游报告**）

- **G-1** 后端全量：`services/quant/.venv/Scripts/python -u -m pytest -q`
  （**必须 `-u`**，否则 Windows 控制台缓冲会让长跑看起来像挂死；**不得并发跑全量**——
  `services/quant` 的用例共享同一个临时 DB）。红条数须与改动前基线一致。
- **G-2 · 零回归（最重要的一条）**：`test_ai_trade_experience_zero_regression.py`——同一 payload
  在「未装配经验层」下走 `build_decision_prompt`，断言输出与冻结样本**逐字节相等**；
  `build_reflection_prompt` 同款。**这条不绿，后面一行都不许接。**
- **G-3** 前端：`NODE_ENV=test pnpm --filter web vitest run`（**必须带 `NODE_ENV=test`**，
  否则组件用例报 React act() 错）。
- **G-4** `pnpm --filter web exec tsc --noEmit` 与 `ruff check services/quant` 无新增。
- **G-5 · 停止路径时延不回归**：把 `distill.run` 与 `service.materialize_bounded` 双双打成
  `await asyncio.sleep(120)` 的桩，起一个 3 成员分组，断言 `stop_group(...)` 的墙钟耗时
  **< 2 秒**、三个 run 均落终态、且两类后台任务仍在 `_experience_tasks` 里在飞。
  **这条用例是 F-1 的机械判据**——没有它，「物化与蒸馏不在停止链上」只是一句口号。
  **★ P2-5 · 用例必须自己收尾**：断言完之后**必须** `await manager.drain_experience_tasks()`
  （或逐个 cancel + `await asyncio.wait`），不能把两个 `sleep(120)` 的任务留给事件循环去关。
  本仓库在 `a8ed650` 上为同形问题付过学费：drain 只 cancel 不 await，`GeneratorExit` 红在**别的
  模块**里，排查成本远大于写这一行。`pause_group` 侧同款再跑一次（★ E-5）。
- **G-6 · 四条源码扫描门禁**（正则须 `\r?\n`，CRLF 环境；先例 `contrastTokens.source.test.ts`）：
  ① 六个门禁模块（`edge_gate` / `base_rules` / `profit_guard` / `hold_horizon` / `sizing` /
  `discipline`）的源码里**不得出现** `experience` 字样（I-1）；
  ② `ai_auto/session.py` 与 `ai_auto/manager.py` 的 `stop` / `pause` 方法体内**不得出现**
  `distill`；
  ③ **★ 新增**：`experience/runtime.py` 的 `flush` / `persist_pending` 方法体内**不得出现**
  `materialize`（F-1）。
  ④ **★ E-11 新增**：`lib/httpTimeout.ts` 里 `/experience/rebuild` 必须**出现两次**（一次在
  `LONG_EXACT_PREFIXES`、一次在 `WRITE_PREFIXES`）——两处登记缺一即静默落默认档 / 落错文案，
  而 `tsc` / `vitest` 都不会红。同时断言 `WRITE_ACTION_SUFFIXES` 里**没有** `/rebuild`
  （防止有人图省事改末段表，把既有的 `/ai-trade/prior/rebuild` 一并波及）。
- **G-7 · 前后端中文词表同步**：`experienceLabels.source.test.ts`（正则 `\r?\n`）。
- **G-8 · ★ 数据层符号齐备（P0-1 的机械判据，本轮新增，优先级仅次于 G-2）**：
  `test_experience_repo_symbols.py` 用正则扫出 `app/ai_auto/experience/**` 里全部 `repo.<name>`
  引用，逐个断言 `hasattr(app.db.repositories, name)`。**这条用例会当场抓住 P0-1**
  （`upsert_lesson_nodes` 未再导出），并让以后任何一次漏再导出都无处可藏。
  为什么必须是它而不是「跑一次物化看看」：`AttributeError` 在本包里会被 `except Exception` 吞成
  一行 warning，**跑一次物化是绿的**——只有符号齐备性检查能看见它。
- **G-9 · ★ 可写配置的读路径齐备（I-10 / P0-3 的机械判据，本轮新增）**：对 §6.4 列出的 12 项
  可写阈值，逐项 `PUT` 一个与默认值不同的合法值 → 断言 `resolve_experience_settings` 的产物
  上该字段等于新值 → 再断言 `POST /preview` 的行为随之改变（至少一项可观测量变化）。
  **逐项**是关键：漏一项就是一个「改得动、永远不生效」的假控件。

### 9.2 逐条验收

| 编号 | 验收项 | 手段 |
|---|---|---|
| A-1 | 一笔平仓成交 ⇒ 恰好落 1 条事件，语境取自**开仓**瞬刻 | `test_experience_events.py`：开仓时 regime=range、平仓时 regime=trend_up，断言事件语境为 `range` |
| A-2 | 重复 dispatch / 续跑重放 ⇒ 事件不双计 | 同一 `(group,inst,seq,exit_ts)` 写两次，断言 `count == 1` |
| A-3 | `cost_basis=="gross"` 或 `fee_known==False` 的事件不进 bps 统计，`excluded` 如实计数 | `test_experience_aggregate.py` |
| A-4 | 桶 `n < min_samples` ⇒ `candidate`，不出现在任何 prompt 层 | `test_experience_promote.py` |
| A-5 | 效应符号翻转 ⇒ 降档 + 建 `refines` + 建 `contradicts`，旧版本仍可查 | 同上 |
| A-6 | 检索**确定性**：同图同种子跑 100 次，结果序列完全一致 | `test_experience_retrieval.py` |
| A-7 | 共现扩散生效：`regime:range+volatility:high` 学到的经验，在 `regime:range+volatility:normal` 下以衰减权重出现 | 同上 |
| A-8 | 跨标的硬闸：PEPE 的 symbol 域经验在 BTC 种子下 `score == 0` | 同上 |
| A-9 | **字符预算是硬上限（含框架句）**：构造 500 条各 120 字铁律，断言 L0 **整段**（页眉+条目+页脚）≤ `l0_max_chars`、条数 ≤ 3、不在句中截断；再把 `l0_max_chars` 调到刚好装不下第一条，断言**整段一个字都不渲染** | `test_experience_disclosure.py` |
| A-10 | 档位机制：id 元组不变 + `contextDisclosure=="repeat"` ⇒ 整段压成一行且 ≤ 40 字 | 同上 |
| A-11 | **决策轮零 IO**：把 `get_sessionmaker` 打成会抛的桩，跑完整一轮 `_run_decision_round`，断言不抛 | `test_experience_session.py` |
| A-12 | 争议对（`contradicts` 且 match 接近）⇒ 两条都不进 L0/L1，进反思层 | `test_experience_retrieval.py` |
| A-13 | 蒸馏超时 / LLM 报错 ⇒ `stop()` 正常收尾，run 落终态，事件已落盘 | `test_experience_distill.py` |
| A-14 | 蒸馏输出含闭合词表外的轴/值 ⇒ 整条丢弃并累加 `experience.rejected_narrative` | 同上 |
| A-15 | **重建幂等**：同一批事件重建两次 ⇒ 节点/边集合逐值相等 | `test_experience_rebuild.py` |
| A-16 | 导入不可信 JSON：越界轴、超长文本、`tier:"iron"`、注入型字符串 ⇒ 全部被拒或夹紧，`source` 强制 `imported`、`tier` 强制 `candidate`；边与事件全部计入 `rejected` | `test_experience_import.py` |
| A-17 | `stat` 节点 DELETE ⇒ 409 且给出「改用静音」的可执行提示 | `test_experience_api.py` |
| A-18 | 启动请求整键缺省 ⇒ 按 `default_on` 装配；`{"enabled":false}` ⇒ 不装配 | `test_experience_start_wire.py` |
| A-19 | 续跑（`start_wire` 重跑解析链）⇒ 经验配置逐字段与首次启动相同 | 同上 |
| A-20 | 前端图布局确定性：同数据两次布局（且入参顺序打乱）坐标逐值相等 | `experienceGraph.test.ts` |
| A-21 | 前端截断诚实性：`truncated:true` ⇒ 必须渲染「已展示前 N / 共 M」文案 | `ExperienceGraphCanvas.test.tsx` |
| A-22 | 列表视图与图谱**同数据源**：同一筛选下条数一致 | `ExperienceSection.test.tsx` |
| A-23 | 启动弹窗默认勾选；取消 ⇒ 提交 `{enabled:false}` 而非省略整键 | `AiTradeLaunchModal.test.tsx` |
| A-24 | 前后端中文词表同步 | 见 G-7 |
| A-25 | 预演与真实注入**同源**：**同一份图快照下**，同一 seed 的 `preview.l0` 与 `runtime.inject` 的 `l0` 逐值相等（跨快照不成立，断言必须收窄，否则会变成一条时红时绿的谎言） | `test_experience_preview_parity.py` |
| A-26 | **停止路径不被后台任务拖长** | 见 G-5 |
| A-27 | **物化保留用户意志**：置顶 + 静音一条 `stat` 经验 → 触发两次物化 → 断言 `pinned`/`muted`/`mute_reason`/`first_ts`/`created_ts` 逐值不变，且该经验**没有**回到 L0/L1 | `test_experience_rebuild.py` |
| A-28 | **`iron` 真的能晋升**：喂 25 笔同符号事件 → 连续两次物化 → 断言 `sign_streak == 2` 且 `tier == "iron"` 且该条出现在 L0；第三次喂反号事件 → 断言降档 + `contradicts` + `refines` | `test_experience_promote.py` |
| A-29 | **`note_close` 取得到全部字段**：构造一笔 `fee_known=False` 的往返，断言事件 `fee_known` 为 False、`edge_error_bps`/`side`/`entry_ts` 均非空，且该事件**不进** bps 统计而进 `excluded` | `test_experience_events.py` |
| A-30 | **`trip is None` 不编事件**：无开仓断面时平仓 → 断言不落事件、`harvestGap == 1`、且该计数出现在 `status.experience` 与 `aiStats.experience` | 同上 |
| A-31 | **档位机制真的生效**：把 `experience.inject` 排到 `_wake.inject` 之前 → 断言用例**失败**（`contextDisclosure` 缺席 ⇒ 档位恒 `full`）；正确顺序下 `repeat` 轮整段 ≤ 40 字 | `test_experience_session.py` |
| A-32 | **`recency` 真的会衰减**：同一批事件重建两次（间隔一年的墙钟桩）→ 断言 `last_seen_ts` 不变、`recency` 单调下降 | `test_experience_retrieval.py` |
| A-33 | **`costRegime` 跨时可比**：先写 100 条低成本事件建库，再写 100 条高成本事件后重建 → 断言**全部** 200 条按同一组分位点重算，且分组不足 `cost_regime_min_samples` 时 `costRegimeFallback` 为真并出现在 `stats_json` | `test_experience_aggregate.py` |
| A-34 | **导出是流式的**：造 20000 条事件 → 断言端点回 `StreamingResponse`、首行 `counts` 与实际行数一致、且进程 RSS 峰值不超过基线 + 50MB | `test_experience_export.py` |
| **A-35** | **★ prompt 里的数字为真（I-9 / F-3）**：库里 37 组 / 2043 条事件时起一次会话，断言 `format_l0` 页眉的两个整数 == 同一时刻 `GET /api/experience/overview` 的 `sessions` / `events`；并断言**首轮**（`events_harvested==0`）也如此 | `test_experience_totals_parity.py` |
| **A-36** | **★ 退化槽匹配不猜（F-4）**：构造两个 `seq` 相同、`side` 不同的开仓槽，平仓时 `trip.side` 两者都不匹配 → 断言**不落事件**且 `harvestGap == 1` | `test_experience_events.py` |
| **A-37** | **★ 物化去抖（F-2 / D-3）**：连续 5 笔平仓在 10 秒内落盘 → 断言只起了 **1** 个物化任务；把 `materialize_min_interval_s` 设为 0 → 断言起了 5 个（且同一时刻至多 1 个在飞） | `test_experience_materialize.py` |
| **A-38** | **★ 会话内新鲜度真的兑现**：后台物化完成后断言 `runtime` 的快照被 `adopt_snapshot` 替换、`graphAsOfTs` 前进、且下一轮 `inject` 用到了新经验 | 同上 |

| **A-39** | **★ 框架句常量与真实渲染长度一致（E-7 / P1-1）**：对 `l0` / `l1` 各构造一个 `totals` 取 7 位数的最坏页眉，断言 `len(header) + len(footer) ≤ FRAME_OVERHEAD[layer]`；再把 `max_chars` 设为 `FRAME_OVERHEAD[layer] + MIN_ITEM_BUDGET`（下限值），断言一条满长 120 字经验**能**渲染出来 | `test_experience_disclosure.py` |
| **A-40** | **★ `PUT /settings/experience` 不得绕过下限（E-7）**：`PUT {l0MaxChars: 194}` ⇒ 422 且错误体指明下限；`PUT {l0MaxChars: 480}` ⇒ 200 | `test_experience_settings_api.py` |
| **A-41** | **★ 阈值真的会生效（E-3 / P0-3 / I-10）**：`PUT {minSamples: 3}` → 断言 ①启动一次会话拿到的 `Settings` 上该值为 3；②`POST /rebuild` 后 `n=3` 的桶被晋升为 `normal`；③`POST /preview` 的回执随之变化。**12 项阈值逐项跑一遍**（见 G-9） | `test_experience_settings_bridge.py` |
| **A-42** | **★ 开仓语境槽有界（E-13 / P1-7）**：连续 `note_open` 100 次不平仓 ⇒ 断言 `len(_open_slots) == 64` 且 `harvest_gap == 36`（被淘汰的都被计了数，不是无声消失） | `test_experience_events.py` |
| **A-43** | **★ 自动静音可解除、用户静音不可覆盖（E-9 / P1-3）**：正向——一条 `stat` 经验本轮缺席 ⇒ `muted=True, mute_reason="stale"`；下一轮它带新证据回来 ⇒ 断言 `muted == False` 且重新出现在 L0/L1。反向——用户手动静音（`mute_reason="user"`）后连跑两次物化 ⇒ 断言 `muted` 恒 `True` | `test_experience_rebuild.py` |
| **A-44** | **★ 撕裂的重建必须可见（E-14 / P1-8）**：在 `replace_edges` 上打一个抛异常的桩 ⇒ 断言 `materialize_health.torn == True`、`GET /overview` 如实下发、且**不抛**（best-effort） | `test_experience_materialize.py` |
| **A-45** | **★ 暂停路径也终局物化 + 脏标志跨进程（E-5 / P0-5）**：`pause_group()` ⇒ 断言起了物化任务且**没有**起蒸馏任务；去抖窗口内平仓后直接销毁 manager ⇒ 断言 `app_settings["experience.dirty"]` 为真，新建 manager 的启动段消费它并完成一次物化 | 同上 |
| **A-46** | **★ 置顶真的有用（E-2 / P0-2）**：`POST /nodes` 手工新增一条经验（语境 = 本标的 15m 高波动）→ 不置顶时 `POST /preview` 的 `l0` **不含**它（`candidate` 不进 prompt）→ `PATCH {pinned:true}` + 一次物化 → 断言它出现在 `l0` 里，且 `applies_in` 边已建出。**蒸馏产物（`source="narrative"`）同款跑一遍** | `test_experience_manual_lessons.py` |
| **A-47** | **★ 关停 drain 是先 cancel 后有界（E-4 / P0-4）**：`drain_timeout_s ≥ shutdown_step_timeout_s` 的配置 ⇒ 断言 `Settings` **拒绝构造**；默认配置下把一个 `sleep(120)` 任务塞进 `_experience_tasks` ⇒ 断言 `drain_experience_tasks()` 在 **< 6 秒**内返回且该任务已 `cancelled()` | `test_experience_shutdown.py` |
| **A-48** | **★ `harvestGapTotal` 有取数来源（E-8 / P1-2）**：一次会话产生 2 次 `harvestGap` → `stop()` → 断言 `GET /overview` 的 `harvestGapTotal == 2`；再跑一次会话又漏 1 笔 ⇒ 断言变成 3（累计而非覆盖）。同一用例断言 `materializeHealth` 的六个键都能从回执里读到 | `test_experience_api.py` |
| **A-49** | **★ 导入有体量闸且 replace 是原子的（E-10 / P1-4）**：`nodes` 给 2001 条 ⇒ 422 `EXPERIENCE_IMPORT_TOO_LARGE` 且**一条都没写进去**；`mode="replace"` 在写第 3 条时打桩抛异常 ⇒ 断言既有手工节点**一条不少**（整体回滚） | `test_experience_import.py` |
| **A-50** | **★ 超时档与写类判定（E-11 / P1-5）**：`timeoutFor("/experience/rebuild") === LONG_MS`、`timeoutFor("/experience/export") === LONG_MS`、`isWritePath("/experience/import") === true`、`isWritePath("/experience/overview") === false` | `httpTimeout.test.ts` |
| **A-51** | **★ 关闭经验模式时的明文（E-15 / P1-9）**：取消勾选 ⇒ 卡片渲染出「既不读取也不沉淀」那一行；`GET /settings/experience` 回 `enabled:false` ⇒ 整卡禁用且显示「已被运维关闭」 | `AiTradeExperienceFields.test.tsx` |
| **A-52** | **★ 物化在飞是进程级单槽（E-6 / P0-6）**：把 `materialize` 打成耗时 3 秒的同步桩 ⇒ 在这 3 秒内连发 5 次 `_on_experience_dirty` ⇒ 断言**只跑了 1 次** `materialize`、脏标志仍为真、且 3 秒后自动补跑 1 次；再把事件数打到 `materialize_max_events + 1` ⇒ 断言**不跑**且 `materialize_health.overflow == True` | `test_experience_materialize.py` |

### 9.3 定义完成（DoD）

1. **G-1～G-9 九道门禁全绿**，且贴出实测输出（不接受「应该会过」）。**G-8 必须最先跑**——它是
   P0-1 的机械判据，而 P0-1 的失败形态是「其余一切看起来都是绿的」。
2. **A-1～A-52 全部有对应用例且通过。**
3. 端到端手测：起一次真实小额 AI 自动交易 → 平掉一笔 → 设置中心「交易经验库」能看到新事件 →
   **停止会话（掐表：按钮必须在 2 秒内给出结果，不得出现「结果未知」文案）** → 稍后刷新概览条，
   蒸馏产物可见 → 重启一次会话，轨迹面板的「经验 ×N」徽标能展开看到被喂入的原文。
4. 关闭经验模式的会话，其决策 prompt 与本特性合入前**逐字节相同**（G-2 已自动化，手测复核一次）。
5. 手测复核 §7.1 预演状态行：会话跑起来后在设置中心手工新增一条经验，预演页必须显示「该会话
   使用的是 X 时刻的快照，与当前库不同」，而**不是**「这就是 Agent 下一轮会看到的内容」。
6. 手测复核六个诚实性键（I-8）各自的渲染落点：`harvestGap` / `graphAsOfTs` /
   `excludedByCostBasis` / `rejectedNarrative` / `truncated` / `materializeHealth`。
7. **手测复核 I-9**：在有 ≥1 条铁律的库上起会话，肉眼确认 trace 徽标展开后的 L0 页眉数字与设置
   中心概览条一致，**不是 0**。
8. **★ 手测复核 I-10（v4 新增）**：在设置中心把「铁律样本门槛」从 20 改成 5 并保存 → 点「重建
   图谱」→ 概览条的「铁律」计数**必须变化**。这一步不通过，说明 §4.10 的派生点没接上，而那是一个
   用户改得动、系统永远不读的假面板。
9. **★ 手测复核 P0-2（v4 新增）**：手工新增一条经验 → 置顶 → 在「检索预演」里选它的语境 →
   它**必须**逐字出现在 L0 文本块里。这一步不通过，说明「管理员可视化管理经验」只是能写进去。
10. **★ 手测复核 P0-5（v4 新增）**：跑一次会话平掉一笔 → **直接关闭桌面端**（不点停止）→ 重开 →
    设置中心概览条的事件数**必须**已包含那一笔，且「上次重建」时间在重启之后。

---

## 十、风险与缓解

| # | 风险 | 后果 | 缓解 |
|---|---|---|---|
| R-1 | **自我强化的幻觉回路**：模型写一条经验 → 下次读到 → 照做 → 再写一遍，最终把一个从未被验证的偏见铸成铁律 | 真实资金按幻觉交易 | I-4（叙事永不自动进 L0）+ 蒸馏输入排除本会话被喂过的经验 id（`injectedKeys`）+ 蒸馏 schema 强制引用具体 `tripSeqs` + L0 末句显式声明「统计结论非指令」 |
| R-2 | **上下文爆炸**（需求点名的风险） | 决策 prompt 膨胀 → 成本上升 / 超时 / 注意力稀释 | 三层硬字符预算（I-6，含框架句）+ 档位压缩（留存窗口占用 **约 4.8KB → 约 0.7KB**）+ A-9/A-10/A-31 钉死 |
| R-3 | **决策轮引入新 IO 面** | 决策轮阻塞 / 超时回落 hold（真实资金错过行情） | I-3：启动期一次载入 + 会话内纯内存；写只挂在**已有** IO 点；A-11 用抛异常的桩钉死 |
| R-4 | **跨标的污染**：PEPE 1m 的教训用在 BTC 1H | 用别的标的的历史开真实仓位，且不会报错 | `scope` 硬闸（symbol 域不匹配即 `score=0`）+ `global` 需跨 ≥3 标的支撑且额外 ×0.7 惩罚 |
| R-5 | **成本口径混用** | 所有 bps 类经验系统性偏移一个往返成本 | I-7：事件落 `cost_basis` + `fee_known`，聚合只吃 `net`+`fee_known`，被排除条数在 UI 明示 |
| R-6 | **双时钟混用**（bar 秒 vs 墙钟） | 时间衰减算错、历史查询错位 | 事件同时落两套时钟，衰减用墙钟、查询用 bar 钟，字段名分别 `*_ts` / `*_wall_ts` |
| **R-7** | **后台工作拖垮停止链**〔本轮 F-1：不是「可能」，在制代码里是**必然**触发〕 | 停止按钮在客户端 20 秒超时并弹「本次操作结果未知」——在真实资金的平仓按钮上制造假警报 | 物化与蒸馏**双双移出** `stop()` 的 await 链，改为 manager 侧 tracked 后台任务（§4.9）；`wait_for` 有界；app 关停链追加一步有界 `asyncio.wait` 后 cancel；失败只 warning；确定性事件在此之前**已经**落盘。**G-5 时延门禁 + G-6③ 源码扫描双重钉死** |
| **R-8** | **导入是不可信输入** | 越界档位/超长文本/注入型内容进入 prompt | 强制 `source="imported"`、`tier="candidate"`、轴与取值走 `is_valid` 闭合词表校验、长度夹紧、**边与事件一律拒收**、不解析任何可执行内容；导入前 `ConfirmDialog` 明示「导入的经验会进入 Agent 的上下文」 |
| R-9 | **SQLite 并发写**（多标的分组 N 会话同时落事件 / 物化） | `database is locked` / 图半成品 | 事件写 `INSERT OR IGNORE`（幂等键保护）；物化走模块级 `asyncio.Lock` + 单事务；**按 `group_id` 去重任务**；两者都是 best-effort，失败只 warning 不阻断交易 |
| R-10 | **图谱渲染卡顿** | 设置页假死 | 默认 120 节点上限 + 确定性布局离线计算（纯函数 + `useMemo`）+ 截断如实提示 + 列表视图作为等价出口 |
| R-11 | **超阈文件继续膨胀** | 触碰 CLAUDE.md 硬阈值、评审不通过 | §8.2 / §7.4 的净增预算是**硬判据**；任何超出必须再下沉一层，而不是放宽阈值 |
| R-12 | **经验为空时的冷启动** | 卡片空白，用户以为坏了 | 空图 ⇒ payload 无 `experience` 键（I-2 零回归路径）；UI 显示引导态「还没有经验——完成第一次自动交易后，这里会出现你的第一条」 |
| R-13 | **回测样本污染真实经验资产** | 用模拟成交的成本口径推翻真实教训 | `origin` 列 + 聚合 `WHERE origin='live'`；v1 回测侧 `write` 恒 false 且 UI 不可勾 |
| R-14 | **物化抹掉用户意志** | 用户静音的经验在下一次重建后原样复活并重新进 prompt；`iron` 因符号历史丢失而永不可达 ⇒ L0 恒空 | 物化按 `key` upsert（§4.5），六个字段绝不覆盖；符号历史落 `prev_sign`/`sign_streak`；**数据层单点 upsert 函数**，禁止第二份 SQL；A-27/A-28 钉死 |
| R-15 | **采集缺口造成幸存者偏差** | 续跑接管与暂停期离线平仓的往返（**恰好偏向亏损样本**）永不进库，整张表系统性偏乐观 | v1 **不补采、但如实计数并露出**：`harvestGap` 进 `status`/`aiStats`/概览条；写进 §十一 作为明确欠账；A-30 钉死 |
| R-16 | **经验层通过反思官否掉本该做的交易** | 一条错误铁律让复核官持续 `abort` ⇒ 用户看到「Agent 一直不开单」 | 影响方向单侧（只会少做）；I-4；反例压制把争议对挡在 L0/L1 之外；`aiStats.experience` 记录注入次数供事后分臂对账；用户可一键关闭（启动弹窗与设置中心各一处入口） |
| **R-17** | **★ 全量重建的规模墙**〔本轮 F-2〕 | 库长到数万条后自动物化恒超时 ⇒「经验随交易不断更新」在会话内静默不兑现 | 去抖 + 有界超时 + **把耗时与规模写进 `materializeHealth` 并在 UI 明示**（I-8），`slow=true` 时给出可执行建议；A-37/A-38 钉死；真正的增量物化记为 §十一.8 的明确欠账 |
| **R-18** | **★ prompt 里出现假数字**〔本轮 F-3〕 | 模型收到「来自你过往 0 次自动交易」却同时被喂 3 条铁律，自相矛盾 ⇒ 经验段被模型打折甚至复述给用户 | I-9 + `SessionGraph` 把 totals 与图绑在同一次取数上；A-35 钉死首轮也为真 |
| **R-19** | **★ 「代码在那里」≠「函数被调到」**〔v4 / P0-1〕 | 一个漏再导出的符号让整条特性静默空转，而 `importlib` 冒烟、门禁、零回归断言**全部是绿的** | G-8 符号齐备性用例（扫源码里全部 `repo.<name>` 逐个 `hasattr`）；实施顺序第 0 步先修再干；**并把「`except Exception` + 只 warning」的每一处都列进终审清单**——那是本特性最大的一类失明源 |
| **R-20** | **★ 写得进、读不到**〔v4 / P0-2 / P0-3〕 | 手工经验 / 蒸馏产物 / 设置面板阈值三者都能写入却永远不被读取；用户看到界面有反应、系统行为毫无变化 | 新增不变式 I-10；E-2 给非 `stat` 经验建边与证据下限、E-3 建唯一派生点；G-9 / A-41 / A-46 逐项钉死。**这是 I-8「算了却看不见」的镜像面，两者必须一起守** |
| **R-21** | **★ 后台重活停不住**〔v4 / P0-6〕 | `asyncio` 的取消停不掉线程池里的活 ⇒ 去抖 + 超时的组合在大库上变成「每 60 秒叠一个永不完成的全库重建」，吃满全进程共用的 anyio 线程池并饿死回测 / 挖掘 | 进程级单槽 + `finally` 释放（覆盖线程里的真实工作全程）；超时语义收窄为「等锁」；`materialize_max_events` 规模闸 + `overflow` 在 UI 明示；A-52 钉死 |
| **R-22** | **★ 关停链的死线倒挂**〔v4 / P0-4〕 | 内层等 50 秒、外层 20 秒就 cancel ⇒ 内层的清理永不执行，后台任务活着穿过 `dispose_engine`；日志里只多一行「did not settle」 | drain 先 cancel 后有界收敛（5 秒，与 `_cancel_background` 同姿态）；启动期校验器把 `drain_timeout_s < shutdown_step_timeout_s` 钉死；A-47 正反两向各一条 |

---

## 十一、明确不做（Out of scope，v1）

1. **Agent 可调用的 `expand_experience` 工具**（让模型自己决定展开哪条）。理由：
   `decider._stream_decision` 目前是「必须且只能调用一次 `submit_trade_decision`」的强制单工具
   循环，是一条被大量用例冻结的契约；加第二个工具需要改成多轮工具循环。v1 用确定性三层披露 +
   反思层反例达成同等的「按需展开」效果。
2. **回测侧读写经验**。理由：回测的成本口径与实盘不同，把实盘经验喂进回测会让回测结论无法与
   历史回测对比；且回测跑得快，几分钟就能生成上万条事件淹没资产。`origin` 列与 `write` 开关
   已为下一轮铺好路。
3. **向量检索 / embedding 语义相似**。理由：会引入新的三方依赖与模型调用，而闭合词表 + 图结构
   已足够精准；语义相似留给「同义经验合并」这个后续问题。
4. **多经验库（book）与团队共享**。当前是单机桌面应用，无多用户模型；导入导出已提供可携带性。
5. **经验对门禁的影响**（「这条铁律说不要在高波动追突破 ⇒ 直接拦截」）。理由：I-1。这类能力
   应当走已有的门禁体系并经独立评审，而不是让一段 LLM 产出的文本获得下单否决权。
6. **经验的自动 A/B 归因**（「开了经验模式赚得更多吗」）。已落 `aiStats.experience` 的观测键，
   分臂对账留给下一轮的盈利体检面板。
7. **续跑接管持仓与离线平仓的经验补采**（明确欠账）。`resume.settle_closed_offline` 与
   `adopt_position` 两条路径不经 `_dispatch`、也不建开仓断面。**不做的理由**：补采要么得复刻
   一份开仓语境（猜出来的语境比没有语境更坏），要么得在续跑链上新开一条采集通路（那条链跑在
   真实资金上，收益不抵风险）。v1 的处置是如实计数并露出（`harvestGap`）。v2 的正确做法是给
   `adopt_position` 建一个 `origin="adopted"` 的降级签名并在经验节点上标注来源。
8. **★ 真正的增量物化**（明确欠账，本轮新增）。v1 的自动物化是「全量重建 + 去抖 + 有界超时 +
   规模可见」。真正的增量（只重算受本批事件影响的桶）需要在 `aggregate` 层引入桶级脏标记与
   增量统计量合并，值得单开一份 spec。**不做的代价已被 R-17 的三重措施限定在「可见的降级」
   而不是「静默的失效」**。
9. **★ 蒸馏结果的实时推送**（本轮新增，F-5）。会话终结后其 WS 已关闭，而本仓库 AI-trade 无
   分组级实时通道；为一条通知新造 WS 通道的复杂度远超收益。改由 `GET /overview` +
   设置中心刷新按钮承载。
10. **★ 「只写不读」的第三态**（v4 新增，E-15 / P1-9）。`enabled:false` = **既不读也不写**，
    `enabled:true + write:false` = 读但不写；**没有**「不读但写」。理由：那需要在启动弹窗上再加一个
    与「启用经验模式」正交的开关，而它服务的场景（想攒收据但不想让 Agent 受影响）可以用
    「启用 + 把 L0/L1 条数配成 0」近似达成，不值得为它多一个用户要理解的状态。**代价被 §7.2 的
    那一行明文限定在「用户知道自己关掉了什么」**，而不是让他事后才发现收据没了。
11. **★ 真正的单事务重建**（v4 新增，E-14 / P1-8）。`rebuild()` 是四次独立提交，改成单事务要动
    数据层四个函数的提交语义，回归面远大于收益。v1 的处置是**让撕裂可见**
    （`materialize_health.torn` + UI 明示 + 一键手动重建），而不是假装它是原子的。
12. **★ 经验库的自动归档 / 分片**（v4 新增，E-6）。`materialize_max_events` 越界后 v1 只是**停止
    自动物化并明说**，不做自动归档。真正的解法是 §十一.8 的增量物化，那值得单开一份 spec。

---

## 十二、实施顺序（节点 #2 的推进次序）

> 每一步都可独立回退：P0～P3 之间任一步停下，系统的外部行为与合入前**逐字节相同**（I-2），
> 只是多了一张在后台默默积累的事件表——**这正是我们希望的失败模式**。

0. **★ P-1 · 第 0 步（v4 新增，先于一切，预计 10 分钟）**
   补 `db/repositories.py` 的 `upsert_lesson_nodes` 再导出（E-1）→ 写 **G-8**（符号齐备性用例）
   并跑绿。**为什么它必须排在补测试之前**：P0-1 的失败形态是「其余一切都是绿的」——在它没修之前
   写的每一条物化用例，都会在一个恒抛且被吞的写路径上「通过」。
1. **P0 · 给已有纯核心补测试 + 落地 6+15 条修法**
   `taxonomy` / `events` / `aggregate` / `promote` / `retrieval` / `disclosure` 的单测（A-1～A-10、
   A-29、A-32、A-33、A-36）→ D-1（`persist_pending` 去物化）→ D-2（`SessionGraph`）→
   D-4（退化槽收紧）→ F-6（删死码）→ G-6③ 源码扫描。
   **★ v4 追加到本步**：E-2（非 `stat` 经验建边 + 证据下限，A-46）、E-7（`FRAME_OVERHEAD` 订正，
   A-39）、E-9（自动静音可解除，A-43）、E-13（`_open_slots` 有界，A-42）。这四条全在纯核心里，
   与补测试是同一趟活。
   **产出**：纯核心从「无测试的 2680 行」变成「有护栏的 2680 行」，且**四条产品能力从写而不可读
   变成真的可读**。
2. **P1 · 采集接线（先把收据保住）**
   `session.__init__` 形参 → `_dispatch` 两处 hook（**close 侧必须挂在 `_inject_explain_meta`
   之后**）→ `manager._build_member` 构造 → `api/ai_auto.build_start_request` 解析与取数 →
   `schemas.py` 启动字段。此步完成后**经验已经在被真实采集**，即使还没有任何读点。
   验收：A-1 / A-2 / A-3 / A-18 / A-19 / A-29 / A-30 / A-35（totals 走通）。
3. **P2 · 物化调度（F-1 / F-2 的正解 + v4 的 E-4/E-5/E-6/E-14）**
   `service.materialize_bounded`（**进程级单槽 + `finally` 释放**，E-6）→ manager 的任务集与四个
   薄调度函数 → `_stop_session` **与 `pause_group`** 各 spawn（E-5）→ 脏标志落库 + lifespan 启动段
   消费（E-5）→ `main.py` 关停链追加一步，drain **先 cancel 后有界收敛**（E-4）→ `torn` / `overflow`
   两个状态位写进 `materialize_health`（E-14 / E-6）→ **G-5 时延门禁必须先绿**。
   验收：A-26 / A-37 / A-38 / A-44 / A-45 / A-47 / A-52。
4. **P3 · 检索与披露（先绿零回归再继续）**
   `runtime.inject` 接在 `_wake.inject` **之后** → `prompts.py` 两个插入点 → `trace.py` +1 键 →
   **G-2 零回归门禁与 G-6①② 必须先绿**。验收：A-6～A-12 / A-31 / A-35（页眉数字为真）。
5. **P4 · 蒸馏**
   `manager._experience_finalize` 的第二步接线（**不是** `stop()` 接线）→ A-13 / A-14。
6. **P5 · API 与设置**
   **先写 `resolve_experience_settings` 这个唯一派生点（E-3），再写端点**——顺序反过来会写出四个
   各自读 `get_settings()` 的端点，然后再回头改四遍。`api/experience.py` 11 端点（含 NDJSON 流式
   导出、导入体量闸 E-10、边 wire 统一 camelCase P2-3）→ `api/app_settings.py` 两端点
   （`enabled` 只读 P2-4、复用同一下限判据 E-7）→ `main.py` 注册。
   验收：A-15～A-17 / A-25 / A-34 / A-40 / A-41 / A-48 / A-49，门禁 G-9。
7. **P6 · 前端**
   **`lib/httpTimeout.ts` 的两处登记先做（E-11，5 行，A-50）**——它最便宜，且不做的话后面每一次
   「重建 / 导出 / 导入」手测都会被一个假超时干扰，让人误以为是后端的问题。
   然后 `lib/*` 与纯函数单测 → 设置中心区块（**列表视图先于图谱视图**，保证无障碍等价形态不是
   事后补的）→ 图谱（**若 FR 布局超 200 行或 A-20 不稳定，立即换确定性放射布局**，P2-8）→
   检索预演 → 启动弹窗卡片（含 E-15 的明文与 P2-4 的禁用态）→ 运行中卡片与轨迹徽标。
   验收：A-20～A-24 / A-50 / A-51。
8. **P7 · 收口**
   九道门禁 + A-1～A-52 + DoD 的 10 条（含 6 项端到端手测）。
   **终审必须逐个回答的三个问题**（它们是 v4 六条 P0 的共同形状）：
   ① 每一处 `except Exception` + 只 warning 的地方，**失败时用户会从哪里知道**？
   ② 每一个「写得进」的地方（DB 列 / `app_settings` 键 / API 入参），**读点在哪一行**？
   ③ 每一条超时 / 死线，**它约束的到底是等待者还是那个活**？

> **若工期吃紧的取舍建议**：优先砍 **P4（叙事蒸馏）** 而不是 **P6（前端可视化）**。前者是唯一
> 引入 LLM 不确定性的子系统、也是 R-1 的唯一来源，而 `stat` 子图在没有它的情况下已经能独立
> 交付需求描述的全部四条（存储 / 图检索 / 渐进式披露 / 累积更新）；后者是需求明写的「管理员在
> 设置中能可视化管理」，**不可砍**。

---

## 附录 A · v3 相对 v2 的差异索引（供节点 #1 快速复核）

| 编号 | 差异 | 类型 |
|---|---|---|
| D-1 | 物化移出 `stop()`/`pause()` 的 await 链与 `_dispatch` 热路径，改为 manager 侧 tracked 后台任务（与蒸馏共用任务集与关停等待） | **P0 修法**（F-1） |
| D-2 | 新增 `SessionGraph{graph, totals, as_of_ts}`；`ExperienceRuntime.__init__` 收它；L0 页眉数字改为库存真值 | **P0 修法**（F-3 / I-9） |
| D-3 | 物化去抖（`materialize_min_interval_s`）+ 有界超时（`materialize_timeout_s`）+ 规模健康 `materialize_health` 露出（`materialize_slow_ms`）；`config.py` 27 → 30 项 | **P0 修法**（F-2 / R-17） |
| D-4 | `_take_slot` 退化匹配收紧为「恰好一个 seq 匹配才取」，否则计 `harvestGap` | P1 修法（F-4） |
| D-5 | 取消 v2 提到的 `experience_distilled` WS 事件（会话终结后无通道），改为 overview 轮询 + 手动刷新 | P1 修法（F-5） |
| D-6 | 删除 `Graph.merge_lessons` 死码；会话内图刷新改走 `adopt_snapshot` 单点替换 | P2 修法（F-6） |
| D-7 | 新增不变式 I-8（观测量必须有渲染落点）、I-9（prompt 里的数字必须为真） | 新增约束 |
| D-8 | 门禁 G-5 扩为「物化 + 蒸馏双桩」，G-6 由两条扩为三条，新增 G-7 | 验收加强 |
| D-9 | 新增验收 A-35（totals 为真）/ A-36（退化槽不猜）/ A-37（去抖）/ A-38（会话内新鲜度兑现） | 验收加强 |
| D-10 | 新增 §二「现状取证与承接盘」：逐文件判定在制代码的保留/修改/新建，并明确「经验目前一条也没有被采集」 | 新增章节 |
| D-11 | §十一 新增两条明确欠账：真正的增量物化（8）、蒸馏结果实时推送（9） | 范围收窄 |
| D-12 | 后端超阈文件净增预算由 58 行调整为 82 行（新增 manager 的后台任务调度 20 行、session 的脏回调 2 行、prompts 的反例段 2 行） | 预算修订 |

---

## 附录 B · v4 相对 v3 的差异索引（节点 #1 评审产出）

| 编号 | 差异 | 类型 |
|---|---|---|
| E-1 | `db/repositories.py` 补 `upsert_lesson_nodes` 再导出 + 新增门禁 G-8；行数取证订正（452 / 1052）；删掉重复的 `count_sessions()` 计划 | **P0 修法**（P0-1） |
| E-2 | 物化为非 `stat` 经验按 `signature_json` 建 `applies_in` 边；新增 `manual_seed_weight` / `pinned_evidence_floor` 两项证据下限 | **P0 修法**（P0-2） |
| E-3 | 新增唯一派生点 `resolve_experience_settings`，四个必经调用点；新增不变式 I-10 与门禁 G-9 | **P0 修法**（P0-3） |
| E-4 | drain 改为先 cancel 后有界收敛，死线 `drain_timeout_s`(5.0) 并由校验器钉死 `< shutdown_step_timeout_s` | **P0 修法**（P0-4） |
| E-5 | 终局物化同时挂 `pause_group`（只物化不蒸馏）；脏标志落库并在 lifespan 启动段消费 | **P0 修法**（P0-5） |
| E-6 | 物化并发改为进程级单槽 + `finally` 释放；超时语义收窄为「等锁」；新增 `materialize_max_events` 与 `overflow` 状态位 | **P0 修法**（P0-6） |
| E-7 | `FRAME_OVERHEAD` 订正为 `{"l0":96,"l1":40}`；`PUT` 复用同一下限判据；新增 A-39 / A-40 | P1 修法（P1-1） |
| E-8 | `harvest_gap` 落库（`experience.harvest_gap_total`）；`service.overview()` 补 `materializeHealth` / `harvestGapTotal` 两个读点 | P1 修法（P1-2） |
| E-9 | 自动静音随新证据解除；「绝不覆盖」收窄为 `mute_reason == "user"` | P1 修法（P1-3） |
| E-10 | 导入体量闸 `import_max_nodes` + `replace` 单事务 | P1 修法（P1-4） |
| E-11 | `lib/httpTimeout.ts` 两处登记（长跑表 + **前缀**写类表，不动末段表）+ G-6④ + A-50 | P1 修法（P1-5） |
| E-12 | `AiTradeSession.__init__` 增 `on_experience_dirty` 形参；W-7 的 close 侧 hook 澄清为**新开**判据块 | P1 修法（P1-6） |
| E-13 | `_OPEN_SLOT_CAP = 64` + FIFO 淘汰并计 `harvestGap` | P1 修法（P1-7） |
| E-14 | 订正「单事务」措辞；撕裂即 `materialize_health.torn` 并在 UI 明示 | P1 修法（P1-8） |
| E-15 | 明确 `enabled:false` = 既不读也不写 + 两处明文；「只写不读」记入明确不做 | P1 修法（P1-9） |
| — | 验收 A-39…A-52（14 条）、门禁 G-8 / G-9、DoD 第 8～10 条、风险 R-19…R-22、明确不做第 10～12 条 | 验收加强 |
| — | P2 八条：4 条已落修（行数取证 / 重复函数 / 边 wire 命名 / 总闸只读），4 条留档（G-5 收尾 / `sessions` 口径 / 再导出条数 / 图布局取舍） | 卫生 |

---

## 实施过程发现的方案缺陷

> 节点 #2 / #3 在实施与终审中若发现本文档与源码不符，**以源码为准**，并把差异逐条回写到本节
> （沿用本仓库既有范式）。
>
> 下列 10 条由**节点 #2（开发）**在实施期发现并已按「以源码为准」的原则处置。定级沿用全文口径：
> **P0** = 会损坏真实资金操作路径或让特性静默失效；**P1** = 会让产品说假话或让一条承诺静默不
> 兑现；**P2** = 卫生 / 文档一致性。

| 编号 | 定级 | 发现 | 处置 |
|---|---|---|---|
| **I-1** | P2 | §8.1 计划新增的数据层函数 `load_nodes_page(offset, limit)` 与 **A-22（列表与图谱同数据源同筛选）互斥**：分页要在 SQL 里做，而筛选判据 `_match` 要读 `signature_json`。两条路径并存必然漂移，而漂移的形态是「同一筛选下图谱说 12 条、列表说 9 条」——用户完全无从判断哪个是真的 | **不新增该函数**（新增了就是死码，违反 CLAUDE.md §四）。`service.lessons_view` 与 `graph_view` 共用同一份 `load_experience_nodes` + 同一个 `_match`，在内存里切片分页；节点总量受 `graph_node_cap`（默认 4000）约束，代价可忽略。数据层本轮因此只 **+1** 个函数（`unmute_reappeared`），`db/repositories.py` 再导出 **+2** 而不是 +3 |
| **I-2** | P1 | 门禁 **G-6②** 的字面措辞（「`stop`/`pause` 方法体内不得出现 `distill`」）与 **★ E-5** 直接冲突：E-5 要求 `pause_group` 必须 `spawn(_experience_finalize(session, distill=False))`，那一行按字面扫**必然**违规。两条只能有一条是绿的 | 判据收紧为**更精确**的形态并写进用例 docstring：会话侧 `stop`/`pause` 一个 `distill` 字样都不许有；manager 侧禁的是 **`await ...distill...`**（spawn 不 await 是允许且必需的）。同时把三条 Python 侧扫描从「按原文正则」改为**按 AST**（去掉 docstring 后 `ast.unparse`）——这些方法的 docstring 恰恰要逐字解释「为什么这里不能有它」，按原文扫会把解释本身判成违规，逼实现者删注释来让门禁变绿，那是反向激励 |
| **I-3** | **P0** | `aggregate._combos_for` 会为**同一条事件**产出两次相同的桶签名（三阶的跨标的同形桶 `{bar, exit}` 与二阶的 `{bar} × exit` 逐字相同），而 `enumerate_buckets` 每次都 `append(index)` ⇒ 那些桶的 `event_ids` 里同一条事件出现两遍。后果：`n` 翻倍，而 `t = mean/(std/√n)` 随 √n 增大 ⇒ **一条只有 10 个样本的经验会拿着「20 笔、t=3.5」晋升成铁律**，然后把那个假数字逐字写进喂给模型的 prompt。这同时违反 **I-9**（prompt 里的每个数字必须为真）与 I-4 的证据门槛，而失败形态**完全静默**。§2.1 把 `aggregate.py` 判为「保留，零改动」正是因为它没有一条用例 | `enumerate_buckets` 内按事件去重（`seen: set[str]`）后再落桶。用例 `test_a3_...` 断言 `all(s.n <= 10)`——同一条事件绝不能在同一个桶里被数两遍 |
| **I-4** | P1 | **A-15（重建幂等）与 A-28（`iron` 真的能晋升）互相矛盾**：`iron` 的第三条门槛是「连续两次同号物化」（`sign_streak >= 2`），所以第一次与第二次重建之间 `tier` **必然**从 `normal` 变成 `iron`。把 A-15 写成「两次重建节点集合逐值相等」会让两条用例只能有一条绿 | 幂等的判据订正为「**键集合与支撑数恒定，且档位在饱和之后不再变化**」：断言 `keys(1) == keys(2)`、`snapshot(2) == snapshot(3)`、三次的节点 / 边计数相同，并在同一条用例里断言 A-28 的晋升真的发生。两条验收合并为一条，理由与判据写在用例 docstring 里 |
| **I-5** | **P0** | **★ E-6② 只写了语义、没有落点**：`materialize_timeout_s` 被订正为「排队等 `materialize_lock` 最多等这么久」，但 `rebuild()` 里是一个**裸** `async with repo.experience_materialize_lock()`。实测后果：一个持锁者随事件循环消失（用例里再普通不过）之后，**后续每一次物化永久挂起**——全量 pytest 在 26% 处硬挂，两个独立进程挂在同一位置，而日志上什么都没有 | `rebuild(..., lock_timeout)` + 新增 `_materialize_lock(timeout)` 异步上下文管理器（`asyncio.wait_for(lock.acquire())`），超时抛 `MaterializeBusyError`；`materialize_bounded` 捕获它并回 `{"ran": False, "reason": "lock_timeout"}`（脏标志留着，下次再来）。**挂起是最难诊断的一种失败：它连一行日志都不留** |
| **I-6** | **P0** | `_experience_finalize` 对**未装配**的会话也照跑：`stop` / `pause` 的每一次调用（含全部既有用例里的那些）都会凭空起一个开 DB 会话、持物化锁的后台任务。叠加 I-5，这就是那次全量挂起的**触发条件** | `_experience_finalize` 开头加 `if session.experience.rule is None: return`。这不是优化而是纪律：未装配的会话没有任何经验可终局化，为它起一个持锁的后台任务只有害处 |
| **I-7** | P1 | §7.4 判定 `lib/store.ts` 净增 **0**，理由是 `patchAiTradeMemberStatus` 已是 `{...m.session, ...status}` 展开。该理由对 **status** 键成立，但**对 trace 键不成立**：`emptyRoundTrace` 是**逐键手抄**装配（文件里自带注释「漏抄这四键 ⇒ 后端发什么前端都收不到」）。不加那一行，W-13 辛苦补上的 `round_start.experience` 在前端**永远是 undefined**，而 `tsc` / `vitest` 都不会红 | `store.ts` **+3 行**（一行赋值 + 两行注释），`lib/aiTradeTypes.ts` 相应 **+2**（`AiTradeTraceEvent.experience` 与 `AiTradeRoundTrace.experience`）。这正是 §8.2 净增预算表存在的意义：预算的作用是逼人下沉逻辑，而不是逼人漏接线 |

| **I-8** | P2 | §8.2 的净增预算（超阈四文件合计 94 行）**未把 docstring 计入**，而 CLAUDE.md §四要求「所有公开 API 必须写 docstring」。按原始口径直算，四个文件的净增是 **+188 行**（含注释与 docstring） | 已按「超了就再下沉一层而不是放宽阈值」执行：manager 的全部调度逻辑（任务集 / 去重 / 物化广播 / 终局 / 排空）下沉到**新模块** `ai_auto/experience/scheduler.py`，manager 只留五个薄转发；`main.py` 的补物化协程下沉到 `scheduler.catch_up`；`session.py` 的种子装配与九轴事实装配下沉到 `ExperienceRuntime.inject_round` / `.facts`。下沉后**代码行**（不含空行与 `#` 注释）净增：`session.py` +43 / `manager.py` +56 / `prompts.py` +21 / `api/ai_auto.py` +11 = **131**，`main.py` +5（预算 +5 ✅）。仍高于 94 的部分**全部是 docstring**（下沉出来的每个新方法都要写一份），代码语句本身已逼近预算。**这条如实记录，不当作已达标** |
| **I-9** | P2 | `test_main_shutdown.py::test_shutdown_steps_are_zero_arg_factories_in_the_legacy_order` 是一条**顺序冻结**用例，W-14 往关停链插一步必然让它变红 | 更新该冻结表并在 docstring 里写清 `ai_trade_experience` 为什么必须**紧跟 `ai_trade` 之后、`db_engine` 之前**（先停会话，再收它 spawn 的后台任务；收不干净的任务会活着穿过 `dispose_engine`）。这正是冻结用例存在的意义——它逼人解释顺序，而不是让人默默改数 |
| **I-10** | P2 | `apps/web/components/ui/contrastTokens.source.test.ts` 冻结了 `lib/**` 的 fetch 调用点数（18 / 18），新增 `experienceApi.ts`（2 个调用点：通用 `request` + NDJSON 导出）必然让它变红；`AiTradeLaunchModal.test.tsx` 未 mock 新卡片的两次取数，整文件因此偶发超时 | 冻结数订正为 20 / 19 并写清新增的两处各是什么；弹窗用例补一个模块级 `vi.mock("@/lib/experienceApi")`。后者的症状值得记一笔：卡片自己的 `catch` 会把失败**正确地**吞掉（那是产品上对的降级），于是表现不是报错而是**整个文件时快时慢**——这类「正确的降级掩盖了测试污染」很难从报错里看出来 |

**未偏离的部分**：§8 变更计划的其余每一项都已按原样落地；六条 P0 的修法（E-1…E-6）与九条 P1
（E-7…E-15）逐条实现，判据见 `services/quant/tests/test_experience_*.py` 与
`apps/web/**/experience*.test.*`。

**本轮新增的文件（§8 之外的三个）**：
- `services/quant/app/ai_auto/experience/scheduler.py`（I-8 的下沉落点：manager 的调度本体）；
- `services/quant/app/ai_auto/experience/importing.py`（`service.py` 按 §8.1 加完 `overview` 的两个
  读点、`materialize_bounded`、`resolve_experience_settings`、`export_stream` 之后是 **799 行**，
  贴着 CLAUDE.md 的 800 行阈值——下一个人加一行就破。按「超了就再下沉一层」提前拆开，
  边界取「处理**别人给的字节**」vs「处理**本地事实**」：不可信输入的夹紧与导入归前者。
  `service.py` 保留一条再导出，故 `distill.py` 与 `api/experience.py` 的调用点一字未改）；
- 13 个测试文件（后端 8 / 前端 5）。
三者都不是新功能，是「预算与阈值逼出来的结构」与「§九 要求的护栏」。

---

> 设计人：Anthropic 工程团队 · 解决方案架构（节点 #0）
> 设计方式：逐节对照实读源码与工作树在制变更，不采信任何上游文档的取证结论
> 评审人：Anthropic 工程团队 · 资深评审（节点 #1）——venv 内可执行探针 + 逐行实读源码
> 文档版本：**v4**（自包含实施依据；= 流水线模板意义上的「评审后 v2」）
> 历史留痕：`spec-v2-archived.md`（v2，仅评审留痕，**不是实施依据**）· 下一站：节点 #2 开发

---

## 评审结论（节点 #1）

### **有条件通过**（Approved with conditions）

这份设计的**结构**是对的，而且对得很扎实：三层实体（事件 / 图 / 经验）的分工清楚，「图算错了不是
数据丢了，重算即可」这条可修复性论证成立；I-1 的**方向单侧**安全论证（反思官只能 confirm / abort /
缩仓 ⇒ 最坏后果恒为「少做一笔」）是本文档里最好的一段推理，它才是这个特性敢默认开启的真正理由；
渐进式披露的档位机制配着 `commit_round` 二次渲染 × `context_refresh_rounds` 的乘数分析，是对
「防止上下文爆炸」这条需求的正面回答而不是绕过去；§二 对在制代码的逐文件承接盘，以及 F-1/F-2
两条自审 P0，都体现了「不采信上游结论」的正确姿态。

但它**不能按 v3 的样子直接进开发**，因为节点 #1 的实测发现了六条 P0，而它们共享同一个形状：
**失败是静默的，而且失败时一切看起来都是绿的**。

- **P0-1**：一个漏掉的再导出让物化恒抛 `AttributeError`，被 `except Exception` 吞成一行 warning
  ⇒ 整条特性 100% 空转，而门禁、冒烟、零回归断言**全部通过**。
- **P0-2**：三类非 `stat` 经验没有边、`weight` 恒 0 ⇒ 蒸馏、手工新增、导入、置顶四条产品能力
  **写而不可读**；I-4 白纸黑字承诺的「置顶」通往 L0 的路径在代码里根本不存在。
- **P0-3**：设置中心的阈值面板是纯写点 —— 用户改得动、系统永远不读。
- **P0-4 / P0-5 / P0-6**：三条都在「后台任务」这一层，分别是死线倒挂、挂错退出路径、超时管不住
  线程池里的活。三者合起来，「经验随交易不断累积」在**最常见的使用节奏**下不兑现。

这六条已经在本文正文逐条落修（E-1…E-6），九条 P1 同样（E-7…E-15），八条 P2 全部记录在案。
**文档层面已无未解决的 P0 / P1。**

### 放行条件（节点 #2 必须逐条兑现，节点 #3 逐条复核）

1. **第 0 步先做 E-1 与门禁 G-8**，并贴出实测输出。在 P0-1 修好之前写的任何物化用例都不作数——
   它们会在一个恒抛且被吞的写路径上「通过」。
2. **G-2（零回归）与 G-8（符号齐备）是两条硬门**，不绿则后面一行都不许接。
3. **A-46 与 A-41 必须真跑**：前者证明「置顶的手工经验真的能被 Agent 读到」，后者证明「设置里改
   的阈值真的会生效」。这两条各自对应一条 P0，也各自对应需求里明写的一句话。
4. **A-45 与 A-47 必须真跑**：前者证明关软件（走 `pause`）时经验仍在累积，后者证明关停 drain
   的死线没有倒挂。
5. **§8.2 的净增预算（94 行）是硬判据**。任何超出改为再下沉一层，不放宽阈值。
6. **不得 `git commit`**（本节点与节点 #2 的既有约束）；节点 #2 若发现本文档与源码不符，
   **以源码为准**并把差异回写进下一节「实施过程发现的方案缺陷」。
7. **终审逐个回答 §十二 P7 的三个问题**（`except` 的可见性 / 每个写点的读点 / 每条死线约束的是
   等待者还是活）——v4 的六条 P0 全都是这三个问题的反面。

### 未阻断但强烈建议

- **P2-8**：图谱布局若 FR 实现超 200 行或 A-20 出现任何不稳定，立即换确定性放射布局。它是加分项，
  不是承重墙。
- **§十二 的取舍建议依然成立且更强**：工期吃紧时优先砍 **P4（叙事蒸馏）**——它是唯一引入 LLM
  不确定性的子系统、R-1 的唯一来源，而 `stat` 子图在没有它的情况下已经能独立交付需求描述的全部
  四条。**但 P6（前端可视化管理）不可砍**，那是需求明写的。

> 评审人：Anthropic 工程团队 · 资深评审（节点 #1）
> 评审方式：venv 内可执行探针 + 逐行实读源码，不采信本文档任何上游取证结论
> 结论：**有条件通过** · 文档版本 **v4**（= 模板意义上的「评审后 v2」）· 下一站：节点 #2 开发
