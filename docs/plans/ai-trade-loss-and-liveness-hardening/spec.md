# 【AI 自动交易】亏损根因与活性加固 — 设计文档

- **feature**: `ai-trade-loss-and-liveness-hardening`
- **状态**: **v2，已评审（有条件通过）** —— 评审记录见下一节，评审结论见文末 §十二
- **上一轮基线**: `5337da1`（执行周期趋势基调，56 文件）
- **证据取样时间**: 2026-09-02（v2 复核取样同日，见 §2.9）
- **证据来源**: 生产库 `%APPDATA%/aragon-desktop/aragon.db`（119 MB）、桌面端
  `%APPDATA%/aragon-desktop/logs/backend.log`（252 MB，2026-06-13 → 2026-09-02）

---

## 评审记录（v1 → v2）

> 评审口径：可行性 / 完备性 / 一致性（对照 `CLAUDE.md` 与仓库既有成文纪律）/ 规模合理性。
> **每一条都在本机 HEAD（`5337da1` + 在制变更）上核到了代码或数据**，行号与数字见各条「证据」。
> P0 / P1 已在正文逐条改掉，改动位置写在「已改」里；P2 记录在案，不阻断实施。

### 一、结论摘要

v1 的**诊断骨架是对的**，其六条量化证据我逐条复算全部命中（见 §2.9 的复算记录）。
但**四条轴里有三条，其修法所依赖的那个事实在本仓库里不成立**——判据读了一个不存在的键、
信号取自一个几乎不产出的通路、拦截落在一条同时被续跑复用的函数上。这不是措辞问题：
照 v1 原文实施，**轴 B 恒空转、轴 D 在它要治的那次事故里恒不触发、轴 A 会把 50 条带着真实
持仓的暂停会话永久锁死在「无法续跑」**。三者都属于本仓库反复出现的同一形态——
**「新判据取自不携带该信息的入参」**（见 blackboard `AC-P0-1` / `AC-FINAL-1` / `AC-FINAL-2`）。
v2 已逐条改掉，并额外补进三组本轮新采到的生产读数（§2.9），其中一组把 D3 的判定
从「推断」升级为「强佐证」。

### 二、P0（阻断级；不改则该轴无效或造成新的资金风险）

| # | 结论 | 证据 | 已改 |
|---|---|---|---|
| **P0-1** | **轴 D 的输入信号在生产事故现场几乎不产出**。v1 让看门狗数「本轮 `degrade` 码连续 N 轮相同」，但 `degrade` 只在**模型提出了受闸动作**时才非空：模型看到 `maBand: null` 后自己写 `hold`，全链路一道闸都不会开火。实测 run 1629：456 轮里 `openFunnel.intents` 仅 **12**、`degradedRounds` 仅 **2**，且 digest 里 `base_rule_band_unavailable` 只在末尾两轮连着出现。`warn_rounds=20` 在这条数据上**永不可达**——v1 最重要的那条承诺（G-D6「456 轮后必然 halt」）用它自己的信号源做不到 | `session.py:1667-1810` 的 `degrade` 合成链；run 1629 `metrics_json`（`roundsCompleted=456`/`tradeCount=0`/`openFunnel.intents=12`/`degradedRounds=2`） | §4.4 改写：阻塞信号改为**与模型动作无关的结构性探针**（`payload` 建好即可判定），并接管既有 `_band_unavailable_streak`；G-D6 改为按探针断言 |
| **P0-2** | **轴 B 的判据读了一个不存在的键**。v1 判据是 `backstop.covered is not True`，但 `classify_backstop` 的 wire 里**根本没有 `covered`**——它只给 `basis` / `coverageRatio`，「覆盖」是两者的合取，唯一派生点是 `pause.is_backstop_covered`（其 docstring 逐字写着这件事）。且 `HorizonFacts` 由 `build_horizon_facts(executor, readings, …)` 装配，那里**拿不到 backstop 运行时**。照原文实施 ⇒ `backstop_covered` 恒 `None` ⇒ 轴 B 恒走「今日行为」分支，是一条**永远不生效且不报错**的死改动 | `exchange_backstop.py:125-149`（wire 键集）、`pause.py:230-243`（`is_backstop_covered` 与「wire 里没有 covered」的成文说明）、`horizon_runtime.py:93-116`（`build_horizon_facts` 的入参只有 executor / readings） | §4.2 改写：覆盖态在 `HorizonRuntime._snapshot` 用它**已经在建**的那份 wire 现算（`_backstop_wire` 就在同一函数里），经 `build_snapshot` 的新 keyword 传进 `release_reason_for`；`HorizonFacts` 不动 |
| **P0-3** | **A-1 的 422 会把 50 条带仓的暂停会话永久锁死**。v1 把互锁放进「启动装配处」，而那处是 `api/ai_auto.build_start_request` —— **冷续跑走的是同一个函数**（`resume.py:87-93` 延迟导入并重跑它），且 `_rebuild_request` 把任何 `HTTPException` 一律翻成 409 `AI_TRADE_RESUME_CONTRACT_INVALID`「启动契约已失效，无法继续」。实测生产里 `(利润保护未装配, 持仓周期已装配)` 的组合有 **50 条 run**；它们暂停后再也续不回来，而续不回来的会话可能正持有真实仓位 | `api/ai_auto.py:402`（start 调用）与 `resume.py:87-93`（cold resume 调用同一个 `build_start_request`）、`resume.py:95-101`（HTTPException → 409 契约失效）；配置分布复算见 §2.9 | §4.1 A-1 改写：判据落在 `ai_trade_start` **端点内**（`build_start_request` 之后、`start_group` 之前），装配链保持纯净；并新增 `G-A8` 冻结「冷续跑不受互锁影响」 |
| **P0-4** | **轴 D 的 halt 走错了暂停通道，且会自我 cancel**。v1 写 `pause(reason="liveness_blocked", hold_position=True)`：① `Session.pause` 的形参是 `flatten`，**没有 `hold_position`**（那是 `build_pause_notice` 的形参）；② 组状态行 W4 落盘、`group.state` / `pause_reason` / `paused_ts`、情报调度暂停、经验层物化**全在 `manager.pause_group` 里**——绕过它 ⇒ 磁盘上这一组仍是 `running`，进程重启后会被复活成「运行中」；③ `Session.pause` 的第 ③ 步是 cancel 决策任务，**从决策轮内部调用它 = 自己 cancel 自己** | `session.py:988`（`async def pause(self, reason="user", *, flatten=False)`）、`manager.py:878-920`（`pause_group` 的 ①~⑧，含 `_persist_group_state`「失败必须上抛」）、`session.py:991-1007`（动作顺序不得重排，③ cancel 决策任务） | §4.4 改写：halt 改为**投递一个停手请求**由 manager 在决策轮之外调 `pause_group(group_id, reason="liveness_blocked", flatten=False)`；并明写这是**组级**停手（`pause_group` 无成员粒度），代价写进 §九 R11 |
| **P0-5** | **E-1 会把已经稳定的机器码改名，静默打掉一批已有中文标签**。实测 259 笔平仓里 **37 笔的 `reason` 已经是机器码**：`external_close`(20) / `session_stop`(7) / `profit_guard_committed_exit`(6) / `profit_guard_trailing`(2) / `offline_exit`(1)。其中四个在前端 `DEGRADE_REASON_LABELS` 里**有条目**，而 `exit_attribution._BACKTEST_REASONS` 与 `is_deterministic_reason()` 又按 `reason` 的**字面量**吃这些值。把它们统一改写成 `EXIT_SOURCES` 取值（`external` / `sessionStop` / `profitGuard`）⇒ 标签表当场失配、回测归因判据当场失灵，**且两处都不报错** | 生产库 `reason` 分布（上列五个短码）、`aiTrade.ts:503-560` `DEGRADE_REASON_LABELS`、`exit_attribution.py:44-52`（`_BACKTEST_REASONS`）与 `:96-105`（`classify_backstest` 字面量优先） | §4.5 E-1 收窄：**只改今天写 prose 的那一条通路**（LLM 平仓的 `reason` 与开仓腿），已有机器码逐字不动；新增 `G-E11` 冻结「五个既有短码在改动前后逐字不变」 |
| **P0-6** | **E-8 是一处没有总闸的真实下单行为变更，且它要的读数在 executor 里不存在**。「下单前可用保证金预检 ⇒ 逐档降档」会改变真实提交张数，却是 §四.5 表里唯一既标 ★ 又没有任何开关的一项，与 §4.0 原则 4 直接矛盾；同时 `LiveExecutor` 全文没有任何 `avail` / `available` 读数——可用保证金只在 `manager._gather_context` 的**启动期**取过一次（`manager.py:485`）。照原文实施要么每次开仓多打一发 REST（热路径新增 IO），要么拿一个启动期的陈旧值去决定真实仓位 | `grep -n "avail" live_auto/executor.py` 零命中；`manager.py:485` `avail = next((cfg.get("availUsdt") …))`；`okx/trading.py:74` 的 `51008 → INSUFFICIENT_BALANCE` 是**下单后**的交易所回执 | §4.5 E-8 降级为**观测 + 可选行为**：本轮默认只把 `INSUFFICIENT_BALANCE` 记进漏斗末格与 `explain.meta`；降档行为挂新总闸 `ai_trade_sizing_margin_precheck`（默认 `False`），并明写取数口径与新增 IO 的代价 |

### 三、P1（会造成静默失效、返工或误判回归）

| # | 结论 | 证据 | 已改 |
|---|---|---|---|
| **P1-1** | **D4 的「没有任何跨轮聚合、没有告警」不成立**。`session.py:160` 就有 `_BAND_UNAVAILABLE_ALERT_ROUNDS = 3`，`_emit_base_rule_trace` 每轮推进 `_band_unavailable_streak` 并在第 3 轮推一条 `AI_TRADE_BASE_RULE_UNAVAILABLE` error 事件。真正的缺陷是它 **(a) 判据是 `== 3` 故只发一次、(b) 是瞬态 WS 事件、不进 `group_status()`（后连的客户端永远看不到）、(c) 不升级、不停手**。同款先例还有 `_EDGE_UNAVAILABLE_ALERT_ROUNDS = 20`。并排再造一个计数器就是本仓库反复警告的「第二份口径」 | `session.py:160/163/257/2379-2386/2550-2551` | §三 D4 与 §4.4 一并改写：轴 D **接管**既有 streak，不新建第二份计数；`liveness.py` 只做「码 → 档位」的纯判定 |
| **P1-2** | **`BLOCKING_CODES` 五个成员里三个在仓库里不存在，第四个带参数后缀故永不命中**。`tradability_infeasible`：可交易性是**启动期探针**，全仓没有任何一处产出该降级码（只有设置键 `ai_trade_tradability_infeasible_veto_rate`）；`no_compliant_range`：全仓零命中；`feed_stale`：是 `lifecycle.py:29` 的**未武装码**，不是决策轮降级码；`base_rule_ma_band` 实际形如 `base_rule_ma_band(pctB=0.42)`，按等值比永不命中、按原样比则每轮换码 ⇒ `streak` 恒 1 | `grep` 结果（三码零命中 / `lifecycle.py:29`）、`base_rules.py:218`、`aiTrade.ts:668` 前端一律 `startsWith` 前缀匹配 | §4.4 重写码表：改用**结构性阻塞码**（本轮新定义、有唯一产出点），并对带参数码统一**前缀归一**（与 `describeDegradeReason` 同口径） |
| **P1-3** | **新豁免码的前端镜像文件写错了**。release 文案表在 `apps/web/lib/aiTradeHorizon.ts` 的 `HORIZON_RELEASE_LABEL`（`:280`），不在 `lib/aiTrade.ts`；而 `aiTradeHorizon.ts` **根本没进 §五.2 的变更清单**。漏改不报错——`HORIZON_RELEASE_LABEL[…] ?? "限制已解除"` 会把原因静默吃掉，用户看到「限制已解除」却不知道为什么 | `aiTradeHorizon.ts:280/302/325`、`hold_horizon_text.py:66-73` `_RELEASE_LABEL` | §4.2 与 §五.2 改：镜像文件更正为 `lib/aiTradeHorizon.ts`；`G-B6` 的等值门禁改成比对这两张表 |
| **P1-4** | **`GET /ai-trade/stats` 端点不存在**，§六.1 的第三块 wire 契约悬空。`aiStats` 只有两条通路：`group_status()`（裸 `dict`，安全）与 `GET /ai-trade/history/{groupId}`（**绑 `response_model=AiTradeHistoryDetailOut`**）。F 轴的 `exitDiscretion` 要挂在强类型的 `AiTradeExitMixOut` / `AiTradeHistorySummary` 上，不改 schema 就会被 **response_model 静默吞掉**——本仓库已成文的前科 | `api/ai_auto.py` 无 `/ai-trade/stats` 路由；`:769` `response_model=AiTradeHistoryDetailOut`；`schemas.py:633-638`（`AiTradeExitMixOut` 只有两个字段）、`:1484-1512`（`AiTradeHistorySummary`） | §六.1 端点更正为 `group_status()` + `GET /ai-trade/history/{groupId}` 两处；§五.1 补 `schemas.py` 的 F 轴条目，§五.2 补 `lib/aiTradeHistoryTypes.ts` |
| **P1-5** | **E-6 与既有 `costBasisMixed` 撞车**，D10 的「面板上没有任何一处告诉用户」不成立。该键从 `ai_auto_repositories.py:167` 一路到 `AiTradeStatsDetail.tsx:232/235` **全链路已存在并已渲染**。另起一个 `costBasisMix` 对象 = 同一个事实两份真源 | `ai_auto_repositories.py:167`、`schemas.py:1391/1505`、`AiTradeStatsDetail.tsx:194/232/235` | §4.5 E-6 改为**扩展既有键**：`costBasisMixed` 旁加 `costBasisCounts:{net,gross}`，文案由既有那一行升级为带笔数；§三 D10 的措辞按事实更正 |
| **P1-6** | **E-5 的 `holdSeconds` 已有写点**，再写一份就是两个写点两种语义。实测 259 笔平仓中 **64 笔的 `explain.meta` 已带 `holdSeconds`**，写点是 `trip_ledger.py:241` 经 `_cost.apply_trip_meta`（成本层装配时才有） | 生产库 meta 键频次统计；`trip_ledger.py:241`、`session.py:608-610` | §4.5 E-5 减为**四**键，并显式写明 `holdSeconds` 沿用既有写点、缺席时如实显示「未记录」 |
| **P1-7** | **A-2 的落点自相矛盾，且只在写侧剥离救不了已经落盘的偏好**。v1 前一句要求「弹窗恒不恢复 `enabled`」，后一句又说「单点落在 store，不在弹窗里做」。更要命的是：193 条 run 的 `enabled:false` **已经在用户的 localStorage 里**，升级后第一次打开弹窗读到的仍是 `false` ——救不了它要救的那批人 | `AiTradeToggle.tsx:613`、`AiTradeLaunchModal.tsx:295-297`、`store.ts:1128`（setter）/`:1328`（partialize）/`:1295` `version: 30`/`:1464-1487`（v28/v29 迁移范式与「必须 null 守卫」的成文教训） | §4.1 A-2 改写为**两点齐改**：写侧剥离（`setAiTradeConfig`）+ **persist `version: 31` 迁移**把已落盘的 `profitGuard.enabled` 剥掉；弹窗读点不动（`?? DEFAULTS.enabled` 本就兜底）。§五.2 标注 `store.ts` 需改三处 |
| **P1-8** | **C-1 的落点错位**。`wake_interval_s` 由 `api/ai_auto._resolve_wake_interval_s` 解析、`discipline.adjustments` 由 `_resolve_discipline` 产出——两者都在 API 装配层；`wake_runtime.py` 只应放那个纯函数。v1 把「抬升 + 回执」也写在 `wake_runtime.py`，而 §五.1 的 `api/ai_auto.py` 一行只写了 A-1 | `api/ai_auto.py:247`（`_resolve_wake_interval_s`）、`:99-166`（`_resolve_discipline` 与可变 `adjustments`）、`:466/474`（两者在同一处构造） | §4.3 C-1 拆成两半（纯函数在 `wake_runtime.py`、抬升与回执在 `api/ai_auto.py`）；§五.1 相应补写 |
| **P1-9** | **§8.1 的回归基线严重过期**。v1 写「后端 3627 passed + 7 已知红」，实测本机 HEAD `pytest --collect-only -q` 为 **5026 collected（2 deselected）**。用 3627 当通过线，会把 1400 个用例的缺失读成正常 | `services/quant/.venv/Scripts/python -m pytest tests --collect-only -q` → `5026/5028 tests collected (2 deselected) in 12.01s` | §8.1 A2 更新基线，并改成「**实施节点自己先跑一遍**取当日基线」，不再钉死一个会过期的数 |
| **P1-10** | **E-2 自相矛盾且会写出死码**。§四.5 要在 `db/session.py::_ensure_trade_risk_columns` 「加一行幂等 ALTER」扩宽 `reason`，而 §七 自己写着「SQLite 不强制 VARCHAR 长度，故老库无需 ALTER」。事实上该补列器只会 `ADD COLUMN`（`reason` 已在列集里 ⇒ 这一行恒不执行），且 SQLite 根本不支持 `ALTER COLUMN` | `db/session.py:107-128`（只有 `if name not in columns: ADD COLUMN`）、`db/models.py:87` | §4.5 E-2 与 §五.1 一并改：**不触碰 `db/session.py`**；`String(64)→String(128)` 只对新建库生效，老库靠 SQLite 不强制长度，两条路径都由用例冻结 |
| **P1-11** | **A1「零回归门禁」的命题不成立**。E-1（改 `trades.reason` 取值）、E-4（写 `start_ts`）、E-5（改 `explain.meta`）、E-6 / E-7（改 stats wire）、E-8（改真实下单张数）**都没有总闸**。「全部总闸置 False ⇒ payload/prompt/decision/snapshot 逐字节等于 `5337da1`」在 `decision`（E-1 改 close 的 `reason` 字面量）与落库形状上都不成立 | §四.5 表与 §六.3 设置表的交集为空的那几行 | §8.1 A1 的断言范围如实收窄为「A/B/C/D/F 五轴 + E-3 / E-8 两个带闸项」，并新增 `A1b` 明确列出**六处有意的、无闸的加法变更**及其兼容性用例 |

### 四、P2（记录在案，不阻断实施）

| # | 结论 | 证据 |
|---|---|---|
| P2-1 | §4.1 写的 `AiTradeStartRequest` 实为 `AiTradeGroupStartRequest`（本轮只有分组启动一条路） | `api/ai_auto.py:455` |
| P2-2 | 计数不符：§五 头写「修改后端 18 个」而表里 20 行；§5.1/§5.3 写「9 个设置」而 §六.3 表 10 行；`services/quant/scripts/audit_ai_trade_pnl.py`（A0 脚本）**没进任何一张变更表** | §五 / §六.3 / §8.1 自比 |
| P2-3 | E-1 说「三处 `_make_trade(..., reason=)` 平仓调用点」，executor 里实际只有两处 | `live_auto/executor.py:611` / `:981` |
| P2-4 | E-7 的 `expired` 是**跨轮事件级**读数（限价单常在数轮后才撤），那时 `_intent_pending` / `_intent_side` 早已清零 —— 必须像 `note_filled(side)` 那样**显式带方向**，否则 `expiredBySide` 恒 0 且不报错（directional-symmetry `P0-1` 的同款坑） | `open_funnel.py:246-258`（`note_filled` 的成文教训） |
| P2-5 | R10 的 `@pytest.mark.audit` 默认排除，需要同时改 `pyproject.toml` 的 `addopts`（现为 `-m 'not network'`）与 `markers` 注册表 | `services/quant/pyproject.toml` |
| P2-6 | `AI_TRADE_PAUSE_REASON_TEXT`（`aiTrade.ts:1566`）今天只有 4 条，**已经漏了 `resume_rollback`**（后端 `PAUSE_REASON_TEXT` 有 5 条、类型联合有 5 个）。本轮加第 6 个原因前应顺手补齐，且必须同改 `aiTradeTypes.ts` 的 `AiTradePauseReason` 联合类型（§五.2 未列） | `pause.py:20-36`、`aiTradeTypes.ts:1525-1538`、`aiTrade.ts:1566-1572` |
| P2-7 | A-4 的 `inert_thresholds(..., sizing_rule=…)` 形参本轮无判据消费；另 `aggressive` 预设的 `hard_tp=0`（= 关闭）今天照样渲染成「利润保护·硬止盈 0%」，同属「显示了但不作数」，建议纳入同一张判据表 | `discipline.py:107/155`、`discipline.py:164-180` |
| P2-8 | §2.6 的「WS 断连 2936」与我复算的 `disconnect` 2981、「HTTP 429 1152」与裸计 3541 不同（匹配式不同所致，不影响结论）。A0 脚本必须把**匹配式本身**写进去，否则下一轮无法复现同一个数 | 见 §2.9 复算记录 |

### 五、评审同时确认为「不改」的三条

1. **A-5「不引入 `RELEASE_PROFIT`」的否决判断成立且证据充分**（均盈 +2.07 已低于打平所需 1.365）。保留。
2. **§十一「不把 `exitMix` 做成硬门槛」成立**，`ExitSourceCard` 的原始理由（会诱导「故意不平仓等保护层」）我逐字核对过其文档注释，与 §4.6 引用一致。保留。
3. **§4.0 原则 5「不新增 wake kind」成立**——`wake_reason` / `wake_guard` / 前端标签表确有多张手抄注册表，收益不抵风险。保留（本轮新增的 `round_skipped` 是**事件**不是 wake kind，不触发该纪律）。

---

## 一、概述

【AI 自动交易】在过去十余轮迭代里已经把「一个盈利系统需要的机制」造得相当齐全：波动率目标化仓位、
ATR 止损、三级回撤刹车、连亏熔断、有效敞口上限、最小 edge 门禁、共识门禁、利润保护棘轮、持仓周期
三闸、离场优先级、交易所兜底核实、经验图谱、市场情报层、执行周期趋势基调。但把生产库里 211 条
`ai-trade` run、520 笔成交、1047 条下单审计与 214 万行后端日志逐条拉出来对账之后，结论只有一句：
**这些机制里真正在赚钱的那几条默认是关的，而默认开着的那几条在结构上把「让利润跑」改成了「让亏损
跑」。** 这不是某个函数写错了一行，是层与层之间的**极性组合**出了问题——每一层单看都自洽，合在一起
构成一台单向棘轮：**赢面被锁住直到回吐成亏损，亏面则被放行去随时割肉。**

数据把这句话钉死了。在口径干净的净额子集（`costBasis="net"` 的 24 条 run、71 次往返）上，会话净额
**−47.36 USDT**，其中手续费 **22.26 USDT**（占毛亏的 47%）。按退出来源拆开：模型自主平仓
（`agent`）36 笔 **−67.52**、模型高优先级越权平仓（`agentOverride`）7 笔 **−10.01**；而确定性的利润
保护层平仓（`profitGuard`）7 笔 **+14.14**、用户手工平仓（`external`）17 笔 **+23.13**。
**每一条确定性离场通道都是正的，唯一为负的是 LLM 自己决定的那一条。** 更精确地：在装配了持仓周期
规则的会话里，**低于配置最短持仓时间**（`minHoldSeconds=7200`）就被平掉的 31 笔合计 **−43.28**，
而遵守了最短持仓的 40 笔合计只有 **−4.08**——十倍的差距，且违规平仓里 13 笔来源是普通 `agent`
（11 笔亏损，均值 −3.15）。与此同时，有峰值浮盈记录的 33 笔平仓中 31 笔曾浮盈，**从峰值累计回吐
75.67 USDT，其中 20 笔把一次正的峰值浮盈做成了实亏。**

第二条主线与亏损无关，但对「顶级可靠」而言更致命：**系统会在完全健康的外表下长时间空转。** 最近一
条会话跑满 **456 轮决策、0 笔成交**，其滚动摘要里模型自己写道「合规限价区间持续为空（feed 故障未修
复，已跨约 260 轮）」。根因是【基础策略】MA 通道闸是**唯一一条 fail-closed 的铁律**（通道算不出 ⇒
所有开仓一律否决），而后端日志显示行情通道在观察窗口内断连 **2936 次**、OKX 公共读不可达 **1848
次**——通道一旦长期算不出，系统就进入一个不报警、不自愈、不停止、但永远不会下单的吸收态，同时继
续烧 LLM 预算（同期 LLM 决策请求失败 1123 次、供应商 `overloaded_error` 779 次）。用户看到的是
「运行中」，实际是「已死」。

本轮的目标因此是两件事，且**只做这两件事**：**(A) 把离场侧的极性掰正**——让确定性的利润侧离场变成
结构上不可缺席的一层，并按证据收窄那条被证明为负 edge 的自由裁量通道；**(B) 给会话装上活性看门
狗**——让「长期什么都做不了」变成一个必须被看见、并最终自动停手的显式状态。附带修掉六处让上面这
两件事**在事后无法被诊断**的归因缺陷（`trades.reason` 全部被静默截断到 64 字符、`peak_pnl` 83% 为
空、`start_ts` 恒为 0、离场闸判定不进 `explain.meta`、成本口径混装、限价撤单不进漏斗）。所有行为
改动一律挂总闸，任一关闭即逐字节回退到 `5337da1`。

---

## 二、证据基线（本轮所有判断的唯一事实源）

> 复现方式见 §八「验收」的 `A0` 用例；下列数字全部来自只读打开的生产库与日志，未做任何写入。

### 2.1 成交与盈亏

| 口径 | 腿数 | 往返 | 净额 USDT | 手续费 | 胜率 | 均盈 | 均亏 | 盈亏比 |
|---|---|---|---|---|---|---|---|---|
| 全部 `ai-trade` | 520 | 257 | −53.203 | 25.568 | 40.9% | +0.741 | −0.862 | 0.86 |
| `costBasis="net"`（现行代码路径） | 144 | 71 | **−47.360** | 22.263 | 42.3% | +2.066 | **−2.667** | **0.775** |
| 历史毛额口径（`fee` 恒 0） | 376 | 186 | −5.843 | 3.306 | 40.3% | +0.211 | −0.195 | 1.08 |

40.9% 胜率下要打平需要盈亏比 **1.445**，实测 0.86；净额子集 42.3% 胜率需要 1.365，实测 0.775。
**两个口径都指向同一个结论：赢的时候赚得太少、亏的时候亏得太多**，而不是「看错方向的次数太多」。

### 2.2 按退出来源拆分（净额子集，71 次往返）

| exitSource | 笔数 | 净额 USDT | 均值 |
|---|---|---|---|
| `agent`（模型自主平仓） | 36 | **−67.524** | −1.876 |
| `agentOverride`（越权平仓） | 7 | **−10.006** | −1.429 |
| `exchangeOco` | 1 | −4.776 | −4.776 |
| `offline`（离线兜底） | 1 | −2.979 | −2.979 |
| `sessionStop` | 2 | +0.661 | +0.331 |
| `profitGuard`（利润保护层） | 7 | **+14.136** | **+2.019** |
| `external`（用户手工） | 17 | **+23.129** | **+1.360** |

### 2.3 持仓周期闸的实际约束力

装配了 `tradingHorizon`（全部为 `intraday_swing`，`minHoldSeconds=7200`）的会话共 71 次可配对往返：

| 分组 | 笔数 | 净额 USDT |
|---|---|---|
| **低于** `minHoldSeconds` 就平掉 | **31（43.7%）** | **−43.276** |
| 遵守 `minHoldSeconds` | 40 | −4.084 |

违规平仓按来源：`agent` 13 笔 −40.891（仅 2 笔盈利）、`agentOverride` 6 笔 −10.000、`external`
7 笔 +0.621、`profitGuard` 3 笔 +6.332、`sessionStop` 2 笔 +0.661。
其中 `profitGuard` / `sessionStop` 属**设计豁免**（利润保护与会话止盈损本就不受三闸约束），
`agentOverride` 属**已声明的配额通道**（`maxOverridesPerSession=6`，实测恰好 6），
**只有 `agent` 那 13 笔是「本该被 G1 拦下却放行了」的那一类**。

### 2.4 利润回吐

- `trades.peak_pnl` 在 257 笔平仓中 **213 笔为 NULL（82.9%）**。
- 有记录的 33 笔中 **31 笔峰值浮盈为正**，从峰值累计回吐 **75.667 USDT**，
  **20 笔把正的峰值浮盈做成了实亏**。
- 75.67 的回吐量级与 `agent` 通道的 −67.52 同阶——**回吐就是亏损本身**，不是旁证。

### 2.5 配置分布（211 条 run）

- `profitGuard` 回执为 `null`（= 利润保护层**未装配**）：**193 / 211**。
  其中 **45 条同时带着 `discipline.preset="balanced"`** —— 用户选了承诺「硬止盈 50%」的纪律预设，
  却因为另一个复选框而拿到了一个没有任何利润侧离场的会话。
- `wakeIntervalMinutes`：3.0（75 条）/ 2.0（63）/ 未开（58）/ 5.0（10）/ 4.0（5）。
- `tradingHorizon.profile`：`intraday_swing`（55 条，目标持仓 4–12 小时）/ 未装配（156）。
- 实测持仓分位：p10=300s、p25=720s、**p50=1800s**、p75=5400s、p90=12871s。
- **3 分钟唤醒 vs 4–12 小时目标持仓 = 单次持仓期内 80–240 次离场机会**；实测中位持仓 30 分钟
  ≈ 10 轮。

### 2.6 运行时故障（后端日志，81 天窗口）

| signature | 次数 |
|---|---|
| OKX WS 断连 / 重连 | 2936 |
| `OKX_UNREACHABLE`（公共读扫描 tick 跳过） | 1848 |
| HTTP 429 | 1152 |
| `ai-trade decide attempt failed`（单次尝试失败） | 1123 |
| LLM 供应商 `overloaded_error` | 779 |
| `ai-trade summarize failed` | 185 |
| `ai-trade decide failed`（重试耗尽，终态失败） | **136** |
| `sqlite3.IntegrityError: UNIQUE constraint failed: candles_cache` | 94 |

`live_order_audit`（1047 行）：开仓单 790（限价 602 / 市价 188），其中 **撤单 223、报错 71**；
平仓单 257（市价 251），拒单 5。错误码：`INSUFFICIENT_BALANCE` **53**、`ORDER_NOT_FOUND` 13、
`LIVE_NO_POSITION` 4、`OKX_CIRCUIT_OPEN` 2、`CONNECTION_FAILED` 2、`OKX_ERROR` 1、
`LIVE_CLOSE_SIZE_EXCEEDS` 1。**限价开仓单有 37% 从未成交而被撤销**，这些「模型想开但没开成」的
意图目前不进任何漏斗读数。

### 2.7 归因数据完整性

| 缺陷 | 实测 |
|---|---|
| `trades.reason` 为 `String(64)`，写入点 `repositories.py:303` 做 `[:64]` **静默截断** | 221 笔 LLM 平仓的 `reason` 长度**全部恰好等于 64**（100% 被截断，且无省略号、无告警） |
| `trades.fee` 初始化为 `0.0`，仅在成本层装配时回填 | 344 / 520 腿 `fee == 0`；66 / 94 条 run **每一腿都是 0** |
| `create_run()` 从不写 `start_ts` | `ai-trade` 211/211、`paper` 169/169、`live-auto` 39/39 全部 `start_ts == 0` |
| `explain.meta` 不含持仓周期闸判定 | 31 笔违规平仓的 meta 里 `exitPriority` / `horizonGate` / `releaseReason` **一个都没有** |

### 2.8 已知取样局限（诚实声明）

开仓腿与平仓腿的配对采用「按 run 先进先出」，在**部分成交 / 加仓**场景下会错配。3 笔
`hold=0s` 的记录几乎可以确定是配对伪影而非真实的零秒持仓。因此 §2.3 的 43.7% 应读作
「同一量级的强信号」，其**方向与量级**由 §2.2 的退出来源拆分（不依赖配对）独立佐证。

### 2.9 v2 评审复算与三组新证据

> 评审节点在同一份只读生产库与同一份日志上把 §2.1–§2.7 逐条重跑了一遍。**下面这一小节是本轮
> 唯一新增的事实**，其余数字维持 v1 原值（已复算命中，不再重复列出）。

**(a) 复算命中的原值（抽样）**：`ai-trade` run **211** / 成交腿 **520** / `start_ts == 0` 为
**211 / 211**；退出来源拆分逐格相等（`agent −67.524` / `agentOverride −10.006` /
`profitGuard +14.136` / `external +23.129` / `exchangeOco −4.776` / `offline −2.979` /
`sessionStop +0.661`）；`profitGuard` 回执为 `null` **193 / 211**，其中同时带
`discipline.preset="balanced"` 的恰为 **45**；日志侧 `OKX_UNREACHABLE` **1848**、
`decide attempt failed` **1123**、`decide failed` **136**、`summarize failed` **185**、
`overloaded_error` **779**、`candles_cache` UNIQUE **94**——全部逐字命中。
（两处口径差异见评审记录 P2-8：`disconnect` 我裸计到 2981、`429` 裸计到 3541。）

**(b) 新证据①——历史上真正触发过的豁免只有一条，就是 `loss_release`。**
把全部 `ai-trade` run 的 `metrics_json.aiStats.horizon.releaseReasons` 做全库聚合：

| 读数 | 值 |
|---|---|
| `releaseReasons` 全库聚合 | **`{"loss_release": 12}`** —— 其余四条豁免（`stale_px` / `epoch_estimated` / `max_hold_bars` / `exit_pending`）**一次都没有触发过** |
| `releasedRounds` 合计 | 12 |
| `minHoldVetoes` 合计 | 19（G1 真的拦下过 19 次） |
| `exitOverrideRounds` / `exitAdjudicatedRounds` | 5 / 1（离场优先级配额通道合计 6 次，与 §2.3 的 `agentOverride` 6 笔互相印证） |

这组数把 D3 从「推断」升级为**强佐证**：§2.3 拆出来的「本该被 G1 拦下却放行了」的那一类恰是
`agent` **13 笔**，而全库解除轮数是 **12**，二者同阶且同源；更关键的是——**这 12 次全部是
`loss_release`**。轴 B 因此不是在收窄一条「可能有关」的通路，而是在收窄**历史上唯一开过火的
那一条**，打击面精确、不会误伤任何一条从未生效的豁免。

**(c) 新证据②——A-1 会拦下的是 55 条里的 50 条。**
按 `config_json` 交叉统计 `(利润保护是否装配, 持仓周期是否装配)`：

| 组合 | run 数 |
|---|---|
| 保护层**未**装配 + 周期**已**装配（← A-1 要拦的致命组合） | **50** |
| 保护层已装配 + 周期已装配 | 5 |
| 保护层已装配 + 周期未装配 | 13 |
| 两者都未装配 | 143 |

即：**周期模式一共只被装配过 55 次，其中 50 次是裸的。** §九 R1 的量级按这个数写，
补救按钮的可用性因此是本轮 UI 侧最重要的一件事，而不是一句提示文案。

**(d) 新证据③——活性事故现场的真实读数（推翻 v1 对轴 D 输入信号的假设）。**
最新三条会话（同一分组的三个成员）：

| run | roundsCompleted | tradeCount | `openFunnel.intents` | 首拦截层 | `degradedRounds`（末 50 轮样本） |
|---|---|---|---|---|---|
| 1629 | 456 | **0** | **12** | `baseRuleMaBand` 12 | **2** |
| 1628 | 473 | **0** | 26 | `consensus` 19 / `baseRuleMaBand` 5 | 1 |
| 1627 | 484 | 4 | 117 | `consensus` 106 / `baseRuleMaBand` 8 | 3 |

**456 轮里只有 12 轮产生过开仓意愿**——其余 444 轮模型自己就写了 `hold`，一道闸都没开火、
`degrade` 恒 `None`。这就是评审记录 P0-1 的全部根据：任何「数连续 N 轮同一降级码」的看门狗，
在这张表上**永远数不到 2**。轴 D 的信号必须取自**与模型动作无关**的结构性事实
（`maBand` 算不算得出来），而那个事实每一轮都在 `_emit_base_rule_trace` 里被算了一次
（`_band_unavailable_streak`，见 §三 D4 的更正）。

---

## 三、根因诊断

> 每条给出：现象 → 代码锚点 → 判定。**D1–D3 是亏损根因，D4–D5 是活性根因，D6–D11 是使前两组
> 无法被事后诊断的归因缺陷。**

### D1 · 离场闸只有亏损侧豁免，没有利润侧对手盘 【P0 · 亏损主因】

`services/quant/app/ai_auto/hold_horizon.py:52` 的 `RELEASE_GATES` 冻结矩阵：

```python
RELEASE_GATES = {
    RELEASE_LOSS:            _ALL_GATES,              # G1+G2+G3 全解
    RELEASE_STALE_PX:        _ALL_GATES,
    RELEASE_EPOCH_ESTIMATED: (GATE_MIN_HOLD,),
    RELEASE_MAX_HOLD_BARS:   _ALL_GATES,
    RELEASE_EXIT_PENDING:    (GATE_EXIT_CONFIRM, GATE_HYSTERESIS),
}
```

五条豁免里，**只有 `RELEASE_LOSS` 是由「浮动盈亏」触发的，且它只在亏的那一侧存在**
（`hold_horizon.py:517`，判据 `return_pct <= -threshold`，阈值 = 止损% × 杠杆 × 0.5）。
**表里没有任何一条 `RELEASE_PROFIT`。** 于是：

- 仓位**浮亏**到半个止损距离 ⇒ 三道闸全开 ⇒ Agent 可以随时割肉；
- 仓位**浮盈**任意幅度 ⇒ 三道闸全关 ⇒ Agent 在 `minHoldSeconds`（默认 7200s）之内**没有任何
  通道**可以兑现利润。

**判定：这不是「需要给 Agent 加一条获利了结的豁免」**（那会让本已过小的 +2.07 均盈更小），
**而是「利润侧必须有一条与亏损侧同等地位的_确定性_离场」**。该层本来就存在——利润保护层的
回吐棘轮 / 保本线 / 硬止盈，且它本就豁免于三闸。问题是它在 193/211 条 run 里根本没装配（D2）。
两者合起来才是完整根因：**锁被装上了，钥匙被拔走了。**

### D2 · 利润保护层可被一个粘性复选框静默关闭，且关闭时无任何交叉告警 【P0 · 亏损主因】

- 后端语义是正确的：`profit_guard.py:310 resolve_profit_guard_rule` 明确写着「字段缺省 ⇒ 按
  settings 装配开启态」，只有运维总闸关或请求体显式 `enabled=false` 才返回 `None`。
- 前端却把这个「显式关闭」做成了**粘性偏好**：
  `AiTradeLaunchModal.tsx:296` `useState(defaultProfitGuard.enabled ?? AI_TRADE_PROFIT_GUARD_DEFAULTS.enabled)`，
  而 `defaultProfitGuard` 来自 `AiTradeToggle.tsx:613` 的 `aiTradeConfig?.profitGuard ?? …`，
  且 `aiTradeConfig` 在 `lib/store.ts:1328` 进了 `partialize` **持久化**。
  取消一次勾选 ⇒ 提交体为 `{enabled: false}` ⇒ 存进偏好 ⇒ **此后每一次启动都默认关闭，永不回弹，
  也不再提示。** 实测 193/211。
- **交叉失效无人告知**：`hard_tp_return_pct` 的**唯一消费点**在 `profit_guard.py:514/753`
  （`grep` 全仓确认）。`discipline.py:85` 的 `balanced` 预设承诺「利润保护·硬止盈 50%」，
  该值随 `discipline.thresholds` 原样落进 `config_json` 并在弹窗只读阈值表里展示给用户，
  但只要利润保护未装配，它**逐字节不作数**。45 条 run 正处于这个状态。

**判定：两个独立控件，其中一个静默作废另一个的承诺** —— 本仓库反复出现的「显示了但不作数」
同形缺陷的第 N 次复发。修法必须是**结构性的**（让致命组合不可表达），不能只加一句文案。

### D3 · 决策节奏与持仓周期量纲失配，且亏损侧豁免把这个失配变现 【P0】

`wakeIntervalMinutes=3` × `targetHold 4–12h` ⇒ 单次持仓被问 80–240 次「要不要走」。
每一轮都是一次让 `agent` 通道（净额 −67.52）开火的机会。三道闸本应压住这个频率，但
D1 的亏损侧豁免恰好在**最该继续持有的时刻**（浮亏、情绪最差）把闸全部打开。
实测：13 笔 `agent` 违规平仓中 11 笔是亏损，均值 −3.15，合计 −40.89。

**判定：`RELEASE_LOSS` 的 G1 解除是负 edge 的。** 它在设计文档里的理由是「亏到解除阈时必须允许
随时离场」——但**这个保护职能已经由交易所侧 OCO 止损承担了**，而 OCO 是在真止损位、不是在半个
止损位。`RELEASE_LOSS` 实际提供的是一条「在半止损位由 LLM 自由裁量提前割肉」的通道，实测为负。

> **v2 补强（评审新证据 §2.9(b)）**：这条判定在 v1 里是**交叉推断**得到的（因为 D9 —— 释放原因
> 不进 `explain.meta`，无法逐笔确认是哪一条豁免放的行）。评审节点改从另一个不依赖配对、也不依赖
> `explain` 的读数取证：全部 run 的 `aiStats.horizon.releaseReasons` 全库聚合是
> **`{"loss_release": 12}`，其余四条豁免一次都没触发过**，`releasedRounds` 合计同为 12。
> 与「本该被 G1 拦下却放行了」的 `agent` **13 笔**同阶同源。
> 结论因此从「推断」升级为「强佐证」，且顺带证明**轴 B 的打击面是精确的**：它收窄的正是历史上
> 唯一开过火的那条豁免，不会误伤任何一条从未生效的通路。
> 仍然保留的诚实边界：12 与 13 的对应关系是**统计上的**，逐笔归因要等 E-5 落地后才能直接回答——
> 故 §十 的实施顺序把 E 轴排在 B 轴之前，且 §8.3 新增 `G-B7` 要求上线后先用 E-5 的读数复核。

### D4 · 唯一 fail-closed 的铁律 + 长期 feed 故障 = 静默空转吸收态 【P0 · 活性】

- `base_rules.py:215`：MA 通道算不出 ⇒ `_veto("base_rule_band_unavailable")`。注释明写
  「fail-closed（§2.6/P0-4）」——**这是全链路唯一一条 fail-closed 的铁律**，其余（利润保护、
  持仓周期、edge 门禁、可交易性）全部 fail-open。
- `base_rules.py:133`：`len(df) < rule.period + 1`（默认 21 根）即返回 `None`。
- `session.py:2965` 每轮现算 `build_ma_band(self._window.base_df(), self.base_rule)`。
- 日志：WS 断连 ~2.9k 次、OKX 公共读不可达 1848 次。
- 结果（生产实证）：最新会话 `roundsCompleted=456`、`tradeCount=0`，模型摘要原文
  「**合规限价区间持续为空（feed 故障未修复，已跨约 260 轮）**」。

**判定：系统进入了一个「运行中 / 无成交 / 不报警 / 不自愈 / 不停手」的吸收态，持续 260+ 轮。**
fail-closed 的极性本身可以辩护（无法验证开单位置就不该开单），**但它必须是响亮的**。

> **v2 更正（评审 P1-1，v1 这里说错了两件事）**：
>
> ① **跨轮聚合与告警都已经存在**。`session.py:160` 就有 `_BAND_UNAVAILABLE_ALERT_ROUNDS = 3`，
> `_emit_base_rule_trace`（`session.py:2367-2386`）每轮推进 `self._band_unavailable_streak`，
> 并在第 3 轮 `_push` 一条 `AI_TRADE_BASE_RULE_UNAVAILABLE` 的 `error` 事件。同款先例还有
> `_EDGE_UNAVAILABLE_ALERT_ROUNDS = 20`（`session.py:163/2550`）。
> **真正的缺陷是那条告警的三个性质**：判据是 `== 3` 故**一整个会话只发一次**；它是**瞬态 WS
> 事件**、不进 `group_status()`，所以之后才连上来的客户端永远看不到它；它**不升级、不停手**。
> 轴 D 因此应当**接管**这个已经在跑的计数器，而不是并排再造一份——两份计数同源不同口径，
> 正是本仓库反复付过学费的失效模式。
>
> ② **`degradeReason` 不是可用的信号源**。它只在**模型提出了受闸动作**时才非空。实测
> （§2.9(d)）：run 1629 的 456 轮里只有 **12 轮**产生过开仓意愿，`degradedRounds` 只有 **2**；
> 其余 444 轮模型看到 `maBand: null` 后自己就写了 `hold`，`degrade` 恒 `None`。
> 也就是说 v1 设想的「同一降级码连续 20 轮」在这次事故的真实数据上**永远数不到 2**。
>
> 可用的信号是 `_emit_base_rule_trace` 里那个**与模型动作无关**的事实：`band is None`。
> 它每一轮都被算一次、与 LLM 说什么无关，正是活性看门狗该数的东西。§4.4 据此重写。

### D5 · LLM 是唯一的离场执行者，而它有 136 次终态不可用 【P1 · 活性】

`decide failed`（重试耗尽）136 次、`decide attempt failed` 1123 次、`summarize failed` 185 次。
决策失败的安全回落是 `hold`。在利润保护层未装配（D2）的会话里，**「回落为 hold」等于「这个仓位
此刻没有任何离场机制」**——既没有模型，也没有保护层，只剩交易所 OCO（而 OCO 覆盖本身还要靠
`backstop_check` 事后核实）。

### D6 · `trades.reason` 静默截断到 64 字符，机器可读的离场分类被 prose 挤掉 【P1 · 归因】

`db/repositories.py:303`：`reason=str(trade.get("reason", ""))[:64]`，列定义 `String(64)`
（`db/models.py:87`）。而 `live_auto/executor.py:913` 把**模型的中文 headline 原样**塞进
`reason`。实测 221 笔 LLM 平仓的 `reason` 长度**全部恰好 64**。
后果：`reason` 既不是可聚合的分类（每笔都不同），也不是可读的说明（半句话被切断，且按字节
截断会切碎 UTF-8）。本轮的全部离场归因只能靠 `explain_json.meta.exitSource` 反推——而那是
后来才加的键，历史行没有。

### D7 · `peak_pnl` 83% 为空，利润捕获率不可算 【P1 · 归因】

`profit_guard_runtime.py:171` 只在 `unavailable_reason is None` 时缓存 `peakUnrealizedPnlUsd`，
而 `session.py:506` 在平仓时 `take_peak_pnl()` 读取即消费。规则未装配（D2）⇒ `refresh()` 第一行
`if self.rule is None: return None` ⇒ 峰值永远不被缓存 ⇒ `peak_pnl` 恒 NULL。
**峰值浮盈是一个纯派生量（`qty × signed(peak_px − entry)`），它不需要任何规则就能算。**
把它绑在保护层的装配上，等于「关掉保护层的同时也关掉了『保护层本该拦住多少钱』这个读数」——
恰恰关掉了唯一能证明该开保护层的证据。

### D8 · `strategy_runs.start_ts` 恒为 0 【P2 · 归因】

`db/repositories.py:132 create_run()` 不接受也不写 `start_ts`，模型默认 0。
`ai-trade` 211/211、`paper` 169/169、`live-auto` 39/39 全为 0。
后果：会话时长不可得 ⇒ 换手率/日、年化投影、每小时成本这类读数要么无法计算，要么被迫改用
`group_started_ts`（27 条 `ai-trade` run 连这个也是 NULL）。

### D9 · 持仓周期闸的判定不进 `explain.meta` 【P1 · 归因】

`session.py:581 _inject_explain_meta` 写入 `source/instId/bar/barTs/wallTs/clOrdId/…`，
成本与趋势归因也各自补键，但**没有任何一个键记录本次平仓时持仓周期闸的判定**：
`exitPriorityLevel` / `horizonGate` / `horizonRelease` 全库零命中。
**「这笔为什么能在最短持仓之内平掉」在事后是不可回答的**——本文档第二节的
结论是靠交叉推断得到的，产品里没有任何一处能直接给出。

> **v2 更正（评审 P1-6）**：`holdSeconds` **不属于缺失的那一组**。它已经有唯一写点
> （`trip_ledger.py:241` 的字段映射，经 `session.py:610 self._cost.apply_trip_meta`），实测
> 259 笔平仓中 **64 笔**的 `explain.meta` 已带该键（其余是成本层未装配的历史行）。
> 故 E-5 由五键减为**四**键；`holdSeconds` 沿用既有写点，缺席的老行如实显示「未记录」，
> **绝不再写第二份**——两个写点两种语义（一个是 trip 口径、一个会是 epoch 口径）迟早漂移，
> 而漂移的正好是本轮唯一要回答的那个问题。

### D10 · 成本口径混装进同一组聚合 【P2 · 归因】

66/94 条有成交的 run 全腿 `fee == 0` 且 `config_json` 无 `costBasis` 键（毛额口径），
其余 24 条为 `costBasis="net"`。两者的 `trades.pnl` 语义不同却被同一批聚合相加
（§2.1 三行的均盈/均亏差一个数量级即由此而来）。

> **v2 更正（评审 P1-5）**：v1 原文写「面板上没有任何一处告诉用户」——**不成立**。
> `costBasisMixed` 已经是一条**全链路已存在且已渲染**的读数：
> `db/ai_auto_repositories.py:167` 现算 → `schemas.py:1391/1505` 出 wire →
> `AiTradeStatsDetail.tsx:232/235` 渲染（口径 KPI 退回「口径未知」+ 一行说明）。
> 真正缺的只有一件事：**它是个布尔，说不出「多少笔」**，用户因此无法判断这份混装到底
> 污染了多大比例的读数。E-6 据此改为**扩展既有键**（加计数），绝不另起 `costBasisMix`
> 对象——同一个事实两份真源，是本仓库明令禁止的形态。

### D11 · 限价开仓撤单不进开仓漏斗，且 `INSUFFICIENT_BALANCE` 高频 【P2】

限价开仓单 602 笔中 **223 笔被撤销（37%）**：模型决定开、闸全放行、单也发出去了，
但价格没到 ⇒ 撤单 ⇒ 这次意图在漏斗末格（`note_filled`）不留痕。漏斗因此系统性高估
「意图 → 成交」的转化率。另有 53 次 `INSUFFICIENT_BALANCE`：仓位求解结果超过可用保证金，
以报错而非降档收场。

---

## 四、技术设计

### 4.0 设计原则（本轮不可动摇）

1. **不放宽模型的自由裁量权，只补确定性通道。** 证据是 `agent` 通道 −67.52 / `profitGuard`
   通道 +14.14。任何「让模型更容易平仓」的修法都与证据反向。
2. **让致命组合不可表达**，而不是加一句提示文案。
3. **fail-open 极性不变**，但「长期什么都做不了」必须从静默变响亮，并最终自动停手。
4. **每条行为改动挂独立总闸**，任一关闭即逐字节回退 `5337da1`；观测侧读数在总闸关闭时**照发**
   （反事实读数），使下一轮有据可依地定阈值。
   **（v2 补，评审 P1-11）本条有六处有意的例外**——E-1/E-2/E-4/E-5/E-6/E-7 是数据完整性修复，
   给它们挂闸等于「可以选择继续写坏数据」。例外必须**逐条列在 §8.1 A1b 里并各有兼容性用例**，
   不得靠 A1 的措辞含混带过。E-8 原本是第七处，但它改的是**真实提交张数**而非落库形状，
   故本轮降级为观测并补挂 `ai_trade_sizing_margin_precheck`。
5. **不新增 wake kind**（那要改九张手抄注册表，见既有 `AC-2`）；**不新增 DB 表、不新增 DB 列**。
   （v2 修正评审 P1-10：本轮**一列都不加**，故 `db/session.py::_ensure_trade_risk_columns`
   不被触碰。`reason` 扩宽在 SQLite 上是零迁移的，详见 §七。）

### 4.1 轴 A · 利润侧确定性离场（治 D1 + D2）

#### A-1 结构性互锁：装配了持仓周期锁，就必须装配利润侧钥匙

新增 `Settings.ai_trade_require_profit_exit_with_horizon: bool = True`（总闸）。

**判据本体是一个纯函数**，放 `discipline.py`（与 `inert_thresholds` 同一处，A-4 复用它）：

```python
def unguarded_horizon(profit_rule, horizon_rule, *, enabled: bool) -> bool:
    """「装了持仓周期锁却没有利润侧钥匙」——本轮唯一的致命组合判据（唯一真源）。"""
    return bool(enabled) and profit_rule is None \
        and horizon_rule is not None and horizon_rule.min_hold_s > 0
```

#### A-1 的落点：**只在 `ai_trade_start` 端点内，绝不在装配链里** 【v2 修正，评审 P0-3】

```
@router.post("/ai-trade/start")
async def ai_trade_start(...):
    _require_ai_enabled(settings)
    req = await build_start_request(session, settings, body)   # ← 装配链保持纯净
    if unguarded_horizon(req.profit_rule, req.horizon_rule,
                         enabled=settings.ai_trade_require_profit_exit_with_horizon):
        raise HTTPException(422, detail={...AI_TRADE_UNGUARDED_HORIZON...})
    await get_ai_trade_manager().start_group(req)              # ← 原有链路一行不动
```

**为什么这一条是阻断级的**：v1 说的「启动装配处」就是 `api/ai_auto.build_start_request`
（`api/ai_auto.py:414`），而**冷续跑走的是同一个函数**——`ai_auto/resume.py:87-93` 延迟导入
`build_start_request` 并用 `start_wire` 原文重跑它（「绝不另写一份」是那里的成文纪律），
且 `_rebuild_request` 把任何 `HTTPException` 一律翻成
`409 AI_TRADE_RESUME_CONTRACT_INVALID`「启动契约已失效，无法继续」。
把 422 放进装配链 ⇒ 生产里那 **50 条「周期已装配 + 保护层未装配」的会话**（§2.9(c)）
一旦暂停就**再也续不回来**，而续不回来的会话可能正持有真实仓位。
互锁的职责是「不让新的致命会话被创建」，**不是「把已经存在的会话锁死」**——
落在端点内两件事都成立：新启动被拦、续跑照常。

错误体必须给出**可执行的三条出路**（前端逐条渲染为按钮，不自造文案）：
① 开启利润保护；② 关闭持仓周期模式（`profile="custom"` 全默认 ⇒ `min_hold_s=0`）；
③ 运维侧置 `AI_TRADE_REQUIRE_PROFIT_EXIT_WITH_HORIZON=false`。
**总闸关 ⇒ 该判据整段不执行，行为逐字节回退。**

> 选 422 而不是「后端自动补装保护层」，是因为后者会静默改变用户提交的语义；本仓库对
> 「服务端替我改了什么」的既有纪律是**必须显式回执**（`discipline.adjustments`）。
>
> 落地细节（v2 补）：`req` 的类型是 `AiTradeGroupStartRequest`（评审 P2-1，v1 写成了
> `AiTradeStartRequest`），本轮只有分组启动一条路；`profit_rule` / `horizon_rule`
> 都是它的既有字段（`api/ai_auto.py:473-474`），**无需新增取数**。

#### A-2 利润保护偏好非粘性（前端）【v2 修正，评审 P1-7】

目标不变：**用户可以每次关它，但不能一次关掉、永远关掉。**
落点是 `lib/store.ts` **一个文件里的两处**——只做其中一处都不成立：

1. **写侧剥离**（管未来）：`setAiTradeConfig`（`store.ts:1128`）在 `set` 之前剥掉
   `profitGuard.enabled`。剥完之后，弹窗读到的 `defaultProfitGuard.enabled` 是 `undefined`，
   `AiTradeLaunchModal.tsx:296` 既有的 `?? AI_TRADE_PROFIT_GUARD_DEFAULTS.enabled` 兜底
   自然回弹为 `true`。**弹窗与 `AiTradeToggle.tsx:613` 一行都不用改**（v1 说「弹窗恒不恢复
   `enabled`」与「单点落在 store」自相矛盾；正确答案是后者，因为读点本来就有兜底）。
2. **迁移剥离**（管已经落盘的那 193 条偏好）：persist 现在是 `version: 30`（`store.ts:1295`），
   新增 `if (version < 31)` 一步，把已持久化的 `aiTradeConfig.profitGuard.enabled` 剥掉。
   **必须 null 守卫**——`aiTradeConfig` 的初始值与多个版本落点都是 `null`，对 `null` 取属性
   赋值会抛 `TypeError` 直接崩掉水合（`store.ts:1464-1487` 的 v28 / v29 已为此写下成文教训，
   照抄那个范式即可）。

只做第 1 步的后果：**恰恰救不了它要救的那批人**——193 条 run 的 `enabled:false` 此刻就躺在
用户的 `localStorage` 里，升级后第一次打开弹窗读到的仍然是「关」。

#### A-3 关闭时的二次确认与后果清单（前端）

`ProfitGuardFields.tsx` 在 `enabled=false` 时渲染一条 `role="alert"` 的红色后果清单，
逐条列出**将失效的具体机制及其当前取值**，数值一律来自后端下发（沿用
`/ai-trade/horizon-profiles` 的既有纪律，前端不硬编码任何一个数）：

- 回吐棘轮（武装 5% / 回吐阶梯 35%→28%→20%）
- 保本线（浮盈 3% 后上移至入场价 +0.2%）
- **本纪律预设承诺的硬止盈**（`balanced` = 50%）—— 明确写「该项由利润保护层执行，关闭后不生效」
- 峰值浮盈与利润捕获率读数
- 保护性平仓后的同向冷却（180s）

启动按钮在该状态下需要**第二次点击确认**（`data-testid="ai-profit-guard-unguarded-confirm"`）。

#### A-4 纪律阈值表标注失效项（后端）

`discipline.py` 新增纯函数 `inert_thresholds(thresholds: dict, *, profit_rule, sizing_rule,
horizon_rule) -> list[str]`：返回「在本会话中**其消费层未装配**因而不作数」的阈值键。
本轮唯一成员判据：`ai_trade_profit_guard_hard_tp_return_pct` 在 `profit_rule is None` 时失效。
产物挂进 `config_json.discipline.inert` 与 `group_status()` 回执，前端只读阈值表对这些行
加删除线 + 「未装配，不生效」尾注。
**这是 D2「显示了但不作数」的通用修法**，后续新增阈值只需往这张表里加一行判据。

#### A-5 不引入 `RELEASE_PROFIT`（明确的**否决**决定，写进文档以防下一轮重犯）

评审时最自然的提案是给 `RELEASE_GATES` 加一条利润侧豁免以求「对称」。**本轮明确否决**：
实测均盈 +2.07 已经低于打平所需，放行更早的获利了结会让它更低。真正的对称性由 A-1 保证——
亏损侧的对手盘是**确定性的利润保护线**，不是**自由裁量的提前止盈**。

### 4.2 轴 B · 收窄被证明为负 edge 的亏损侧豁免（治 D3）

新增 `Settings.ai_trade_loss_release_requires_uncovered: bool = True`（总闸）。
改 `hold_horizon.release_reason_for`：`RELEASE_LOSS` 命中后，按**交易所兜底覆盖状态**分档，
而不是无条件返回全解：

| 兜底 OCO 状态 | 解除的闸 | 理由 |
|---|---|---|
| **未覆盖 / 未知**（`covered is not True`） | `G1+G2+G3`（今天的行为） | 没有确定性止损兜底，必须保留 Agent 随时止损的自由（fail-open） |
| **已覆盖** | `G2+G3`（**不解 G1**） | 确定性止损已在真止损位兜底；实测提前割肉通道为 −40.89 / 13 笔 |

#### 覆盖态从哪里来 【v2 重写，评审 P0-2 —— v1 这一段读了一个不存在的键】

v1 写「`backstop.covered is not True`」并把它挂到 `HorizonFacts` 上。**两处都不成立**：

- **`covered` 不是 wire 里的键。** `classify_backstop`（`exchange_backstop.py:125-149`）只产出
  `basis` / `coverageRatio` / `hasStop` / `coveredSz` / `mismatch` 等。「覆盖」是
  **`basis == "exchange"` 且 `coverageRatio >= min_coverage`** 两个条件的合取，其**唯一派生点**
  是 `pause.is_backstop_covered(wire, min_coverage)`（`pause.py:230-243`），那个函数的
  docstring 逐字写着「wire 里**没有** `covered` 这个键」。照 v1 原文写判据 ⇒ 恒取到 `None`
  ⇒ 恒落「未覆盖」⇒ **轴 B 是一条永远不生效、且不会报任何错的死改动**。
- **`HorizonFacts` 拿不到它。** 断面由 `build_horizon_facts(executor, readings, *, stale_feed,
  cost_bps)`（`horizon_runtime.py:93`）装配，那里只有 executor 与 readings，**没有 backstop
  运行时**。要塞进去就得让会话侧再算一次覆盖率——那是第二份口径，且必然与快照里的
  `snapshot["backstop"]` 在时间上错位。

**正确落点：在它已经算好的地方现算，走同一条既有派生式。**
`HorizonRuntime._snapshot`（`horizon_runtime.py:214-227`）本来就在同一个函数体里调
`self._backstop_wire(now_wall)` 建那份 wire，把它顺手判一次即可：

```python
# horizon_runtime.py::HorizonRuntime._snapshot
wire = self._backstop_wire(now_wall)
covered = None if wire is None else is_backstop_covered(          # 唯一派生点，不另写判据
    wire, float(getattr(self.backstop.settings,
                        "ai_trade_backstop_min_coverage", 0.999)))
return build_snapshot(self.rule, self.state, facts,
                      turnover=..., backstop=wire,
                      backstop_covered=covered)                    # ← 新增 keyword-only
```

```python
# hold_horizon.py
def build_snapshot(rule, state, facts, *, turnover=None, backstop=None,
                   backstop_covered: bool | None = None) -> dict: ...
def release_reason_for(rule, state, facts, *, return_pct,
                       backstop_covered: bool | None = None) -> str | None: ...
```

三条结构性保证（**都不靠默认值碰巧成立**）：

- `backstop_covered=None` 是形参默认值 ⇒ 回测（`backtest.py:867` 不传该键）、
  未装配核实层（`self.backstop is None` ⇒ `wire is None`）、总闸关闭（wire 是
  `local(disabled)` 空壳 ⇒ `basis != "exchange"` ⇒ `False`）三条路径**全部逐字回退今天的行为**。
- 判据复用 `pause.is_backstop_covered`，与 `discipline_view.naked_backstop` / 续跑门禁**同一个阈值
  同一个函数**，本轮**不新开旋钮**（沿用 `ai_trade_backstop_min_coverage`）。
- `HorizonRuntime` 「不持有 settings」的既有约束不被破坏：阈值经 `self.backstop.settings` 读，
  与同类里 `backstop_gate()`（`horizon_runtime.py:411`）的既有姿势逐字同构。
- **依赖方向**：`hold_horizon` 不 import `pause`（`pause` 反向依赖它）；判定发生在
  `horizon_runtime` 这一层，纯核心只收一个 `bool | None`。

其余实现要点：

- `RELEASE_GATES` 表本身**不改**（它是冻结口径）。新增 `RELEASE_LOSS_COVERED = "loss_release_covered"`
  一行，值 `(GATE_EXIT_CONFIRM, GATE_HYSTERESIS)`。`release_reason_for` 返回哪一个由上表决定，
  且**必须留在同一个 `if` 分支的最前面**——四条既有豁免的相对顺序是冻结口径（`hold_horizon.py:509-512`
  的成文纪律），新码是 `RELEASE_LOSS` 的**同位替换**，不占新位次。
- 快照 `releaseReason` 因此可能取到新值 ⇒ **两张既有镜像表各加一行**：后端
  `hold_horizon_text.py:66-73` 的 `_RELEASE_LABEL`，前端
  **`apps/web/lib/aiTradeHorizon.ts:280` 的 `HORIZON_RELEASE_LABEL`**
  （**v2 更正，评审 P1-3：v1 写的 `lib/aiTrade.ts` 是错的**，那里放的是 `DEGRADE_REASON_LABELS`，
  与 release 码不是同一张表）。漏改不报错——`HORIZON_RELEASE_LABEL[…] ?? "限制已解除"`
  会把原因静默吃掉，用户看到「限制已解除」却读不到为什么。
- 总闸关 ⇒ 恒返回 `RELEASE_LOSS`，逐字节回退。

> 注意：本改动**只影响 Agent 主动平仓**。止损、交易所 OCO、利润保护层、会话止盈损、强制时间
> 平仓一律不经三闸（`gated_kind` 已保证），不受任何影响。

### 4.3 轴 C · 节奏与持仓周期的相干性（治 D3 的频率侧）

#### C-1 唤醒下限派生（后端）【v2 拆成两半，评审 P1-8】

**① 纯函数**（`wake_runtime.py`，零 IO、不碰 settings）：

```python
def coherent_wake_floor_s(horizon_rule, *, divisor: int) -> float:
    """与目标持仓相干的唤醒周期下限；未装配 / divisor<=0 ⇒ 0.0（= 不约束）。"""
```

**② 抬升与回执**（`api/ai_auto.py`，**不在 `wake_runtime.py`**）：
`wake_interval_s` 的解析点是 `_resolve_wake_interval_s`（`api/ai_auto.py:247`），
而 `discipline.adjustments` 是 `_resolve_discipline`（`:99-166`）产出的**可变列表**——
两者都住在 API 装配层，且在 `build_start_request` 里就挨着构造（`:466` 与 `:474`）。
纯核心模块既拿不到 `adjustments`，也不该开始知道「服务端替我改了什么」这条通道的存在。
故抬升写成装配层的一行：

```python
floor_s = coherent_wake_floor_s(horizon_rule, divisor=settings.ai_trade_wake_horizon_divisor)
wake_s = _resolve_wake_interval_s(body.wakeIntervalMinutes, settings)
if floor_s > 0 and wake_s > 0 and wake_s < floor_s:
    discipline.adjustments.append({...})     # 与 _validate_sizing 的既有追加姿势同构
    wake_s = floor_s
```

新增 `Settings.ai_trade_wake_horizon_divisor: int = 0`（**默认 0 = 关闭**，本轮只做观测与提示，
不改行为；下一轮按验收卡定值，建议 24）。

> 这段代码同时会在**冷续跑**时重跑一遍（`resume.py` 复用 `build_start_request`）。这是**安全的
> 且是想要的**：抬升是幂等的纯派生（同一份 `start_wire` 得到同一个 `wake_s`），且它写的是
> `adjustments` 回执而不是抛错——与 A-1 必须避开续跑路径的理由（那是 422，会锁死会话）
> 恰好相反，两者不可混为一谈。

#### C-2 无可行动作即跳过整轮（后端，省 LLM 预算 + 断掉冲动通道）

新增 `Settings.ai_trade_skip_round_when_gated: bool = False`（默认关）。
开启后，在建 payload 之前先算一次持仓周期快照；当**全部**下列条件成立时跳过本轮 LLM 调用，
只推一条 `round_skipped` 事件与轨迹段：

1. 有持仓（`status == "holding"`，即非 `flat`、非 `released`）；
2. `releasedGates` 为空且 `minHoldRemainingSeconds > 0`；
3. 本轮唤醒不是 `signal` / `intel_alert` / `manual` 这类**外部事件驱动**的唤醒；
4. 利润保护快照 `status` 不在 `ARMED_STATUSES`（已武装说明有事可做，必须让模型看到）。

条件 3/4 是安全内核：**跳过的只能是「结构上不可能产生任何合法动作」的那种轮**。

> 收益量化：实测中位持仓 30 分钟、唤醒 3 分钟 ⇒ 一次持仓约 10 轮，其中前
> `minHoldSeconds/interval` 轮结构上不可能平仓。同时直接缓解 §2.6 的 1123 次 LLM 失败与
> 779 次供应商过载——少打的每一次都是稳的。

### 4.4 轴 D · 活性看门狗（治 D4 + D5）

> **本节在 v2 被整体重写**（评审 P0-1 / P0-4 / P1-1 / P1-2）。v1 的三个前提在本仓库里都不成立：
> 信号源几乎不产出、码表里三个码不存在、停手走的那条 API 不存在且会自我 cancel。
> 目标与阈值不变，改的是「数什么」「谁来停」。

#### D-0 信号源：**结构性阻塞探针**，与模型说什么无关

v1 数的是「本轮 `degrade` 码」。而 `degrade` 只在**模型提出了受闸动作**时才非空——
生产事故现场 456 轮里只有 12 轮产生过开仓意愿（§2.9(d)），看门狗永远数不到 2。
本轮改为在**决策 payload 建好之后、把它交给 LLM 之前**，算一次**与模型动作无关**的探针：

```python
# liveness.py —— 纯函数，零 IO、零 await
BLOCK_MA_BAND   = "ma_band_unavailable"     # payload.baseRules.maBand is None 且铁律已装配
BLOCK_NO_PX     = "mark_px_unavailable"     # 判定价不可信（_is_bad_px 同一谓词）
BLOCK_FEED      = "feed_degraded"           # 会话级 _stale_degraded 为真
BLOCK_LLM       = "llm_unavailable"         # 本轮 decide 重试耗尽（终态失败）
BLOCKING_CODES = (BLOCK_MA_BAND, BLOCK_NO_PX, BLOCK_FEED, BLOCK_LLM)

def probe(*, ma_band_armed: bool, ma_band: dict | None,
          mark_px_ok: bool, stale_feed: bool, llm_failed: bool) -> str | None:
    """本轮**结构上**是否无法开仓；能开则 None。顺序即优先级，自上而下取第一条。"""
```

四条码全部**有唯一产出点、无参数后缀、值域封闭**，故 streak 的「同码连续」判据成立：

| 码 | 唯一判定源 | 为什么它是「结构性」的 |
|---|---|---|
| `ma_band_unavailable` | `payload["baseRules"]["maBand"] is None`（`session.py:2965` 每轮现算） | 与 LLM 输出无关；它正是 D4 事故的那个事实 |
| `mark_px_unavailable` | `hold_horizon._is_bad_px(mark_px)`（**复用既有谓词**，不写第二份） | 没有可信判定价就不可能合法下单 |
| `feed_degraded` | `session._stale_degraded` | 会话级降级标志，已有唯一写点 |
| `llm_unavailable` | `decide` 重试耗尽的终态失败（D5，日志实测 136 次） | 决策器不可用 ⇒ 持仓此刻无人看管 |

> **v1 的四个码为什么不能用**（评审 P1-2）：`tradability_infeasible` 全仓不存在——可交易性是
> **启动期探针**，只有设置键 `ai_trade_tradability_infeasible_veto_rate`；`no_compliant_range`
> 全仓零命中；`feed_stale` 是 `lifecycle.py:29` 的**未武装码**，不是决策轮降级码；
> `base_rule_ma_band` 实际形如 `base_rule_ma_band(pctB=0.42)`（`base_rules.py:220`），
> 按等值比永不命中、按原样比则每轮换码 ⇒ streak 恒 1。
> 若将来确实要把带参数的降级码纳进来，**必须先前缀归一**（`code.split("(")[0]`），
> 与前端 `describeDegradeReason` 的 `startsWith` 口径（`aiTrade.ts:668`）同源。

#### D-1 接管既有 streak，**不新建第二份计数** 【评审 P1-1】

`session.py:257` 的 `self._band_unavailable_streak` 与 `session.py:160` 的
`_BAND_UNAVAILABLE_ALERT_ROUNDS = 3` **已经在跑**，`_emit_base_rule_trace` 每轮推进它。
本轮**不新增第二个计数器**，而是：

- 把 `_band_unavailable_streak` 的推进点上提为 `LivenessRuntime.advance(...)` 的一个输入，
  既有第 3 轮那条 `AI_TRADE_BASE_RULE_UNAVAILABLE` error 事件**逐字保留**（它是「第一声」，
  比新横幅早 17 轮，有独立价值）；
- 看门狗只在既有 streak 之上加两档判定与两个出口（`degraded` 横幅 / `halt` 停手）。
- 总闸 `ai_trade_liveness_enabled=False` ⇒ 装配不发生，`_band_unavailable_streak` 与那条
  error 事件**逐字节回退到今天**。

#### D-2 纯核心形状

```python
@dataclass(frozen=True)
class LivenessRule:
    warn_rounds: int = 20      # 连续多少轮同码阻塞后置 degraded（0=关）
    halt_rounds: int = 60      # 连续多少轮后请求自动暂停（0=关）

@dataclass(frozen=True)
class LivenessState:
    code: str | None = None
    streak: int = 0
    first_wall: float = 0.0

def advance(state, *, blocking_code, filled, now_wall) -> LivenessState: ...
def verdict(state, rule) -> tuple[str, str | None]:   # ("ok"|"degraded"|"halt", 文案码)
```

判据（**逐条冻结**）：

- `blocking_code is None` **或** 本轮有成交 ⇒ `streak = 0`，`code = None`（**任何一次成交都
  清零**——系统证明了自己还活着）。
- `blocking_code` 与上一轮不同 ⇒ `streak = 1`，换码（连续性按**同一个**阻塞码计，
  在两个码之间来回跳动不算长期阻塞）。
- 相同 ⇒ `streak += 1`。
- `streak >= warn_rounds` ⇒ `degraded`；`>= halt_rounds` ⇒ `halt`。
- `warn_rounds`/`halt_rounds` 为 `0` ⇒ 该档**永不触发**（与本仓库既有 `0=关` 惯例一致）。

#### D-3 会话侧接线

- `degraded`：`group_status()` 的成员行新增 `liveness: {level, code, streak, sinceWall, message,
  advice}`，WS 推一条 `liveness` 事件（**只在 `level` 跃迁时发**，防刷屏）。
  前端 AI 交易面板顶部渲染一条**可点开的**橙色横幅，文案必须包含「阻塞码 + 连续轮数 +
  起始时间 + 一条可执行建议」，**全部由后端下发**（`liveness_text.py`，前端不硬编码）。

- `halt`：**投递一个停手请求，由 manager 在决策轮之外执行** 【v2 重写，评审 P0-4】

  ```python
  # session.py：决策轮内**只置意图**，绝不在这里 await 暂停
  if level == "halt" and not self._liveness_halt_requested:
      self._liveness_halt_requested = True
      await self._push(liveness_halt_event(...))      # 先让用户看见
      self._request_group_pause("liveness_blocked")   # 交给 manager 的既有任务通道
  ```

  ```python
  # manager.py：在决策轮之外执行既有整组暂停
  await self.pause_group(group_id, reason=pause_core.PAUSE_REASON_LIVENESS, flatten=False)
  ```

  **v1 写的 `pause(reason=…, hold_position=True)` 有三处不成立**：
  ① `Session.pause` 的形参是 `flatten`（`session.py:988`），**没有 `hold_position`**——那是
  `pause_core.build_pause_notice` 的形参；「不平仓」对应的是 `flatten=False`（也正是默认值）。
  ② 组状态行 W4 落盘（`_persist_group_state`，「失败必须上抛」）、`group.state` /
  `pause_reason` / `paused_ts`、情报调度暂停、经验层物化——**全在 `manager.pause_group`
  里**（`manager.py:878-920`）。绕过它直接调 `session.pause` ⇒ 磁盘上这一组仍是 `running`，
  进程重启后会被复活成「运行中」，而它恰恰是刚刚被判定为「已死」的那个会话。
  ③ `Session.pause` 的第 ③ 步是 **cancel 决策任务**（`session.py:991-1007` 明写动作顺序不得
  重排）。从 `_run_decision_round` 内部 `await` 它 = **自己 cancel 自己**。

  **绝不自动平仓**——阻塞态下我们恰恰不确定行情是否可信（`flatten=False` 由用例 `G-D4` 冻结）。

  **粒度是组级，不是成员级**：`pause_group` 没有成员粒度（`stop_member` 有，但那是「停」不是
  「暂停」，且不可续跑）。因此**一个成员长期阻塞会把整组停手**。这是有意接受的代价，理由：
  阻塞码里三条（`ma_band_unavailable` / `mark_px_unavailable` / `feed_degraded`）的根因是
  行情通道，几乎必然是组级同因；而「只停一个、另外两个继续在同一条坏通道上跑」是更差的结果。
  代价与缓解写进 §九 R11，横幅文案必须**逐字说明「将暂停整组」**。

  `pause_core` 新增第 6 个原因常量 `PAUSE_REASON_LIVENESS = "liveness_blocked"`，
  **必须一次改齐四处镜像**（`pause.py` 的常量与 `PAUSE_REASON_TEXT`、
  `apps/web/lib/aiTradeTypes.ts` 的 `AiTradePauseReason` 联合、
  `apps/web/lib/aiTrade.ts:1566` 的 `AI_TRADE_PAUSE_REASON_TEXT`）。
  顺带补齐 `resume_rollback`——它今天在前端文案表里是缺的（评审 P2-6），
  症状正是那张表自己的注释预言的「界面上就是空的」。

新增设置：
`ai_trade_liveness_enabled: bool = True`（总闸；关 ⇒ 不装配、整键缺席、逐字回退）、
`ai_trade_liveness_warn_rounds: int = 20`、`ai_trade_liveness_halt_rounds: int = 60`。

**D5 的处置**：`decide failed`（重试耗尽的终态）计入 `llm_unavailable`，走同一条看门狗。
连续 20 轮 LLM 终态不可用正是「持仓无人看管」的时刻。
（诚实边界：日志里 136 次终态失败是 81 天的总量，**是否曾在单会话内连续 20 轮不可知**——
这条码本轮先建、先观测，阈值下一轮按 §8.7 的口径校准，不预先声称它「已校准」。）

### 4.5 轴 E · 归因数据完整性（治 D6–D11）

| 编号 | 修法 | 落点 |
|---|---|---|
| **E-1** | `trades.reason` 写**机器码**——但**只改今天写 prose 的那条通路**：LLM 平仓（`agent` / `agentOverride`）与开仓腿。prose 留在 `explain.headline`（本就未截断、实测 520/520 行都有） | `live_auto/executor.py:611` 的模型平仓调用点 + `:329`/`:566` 的开仓调用点；`ai_auto/session.py` 合成平仓处 |
| **E-2** | 列宽 `String(64)` → `String(128)`；写入点截断时 `logger.warning` 一次（不再静默）；截断按**字符**而非字节。**不触碰 `db/session.py`** | `db/models.py:87`、`db/repositories.py:303` |
| **E-3** | 峰值浮盈与保护层规则解耦：`ProfitGuardRuntime` 未装配时仍推进一个**只算峰值、不做任何判定**的轻量 epoch（复用 `advance_state`，它只吃 `state`/`facts`、不需要 `rule`），`take_peak_pnl()` 照常供数 | `profit_guard_runtime.py`（新增 `_peak_only` 分支）；新增总闸 `ai_trade_peak_tracking_always: bool = True` |
| **E-4** | `create_run(..., start_ts=…)` 新增 keyword-only 形参，`ai_auto` / `live_auto` / `paper` 三个调用点传 `int(time.time())`；**不回填历史行** | `db/repositories.py:132`；三处调用点 |
| **E-5** | `_inject_explain_meta` 增补**四**键：`horizonGate`（`pass`/`veto`/`released`/`idle`，直接取既有 `hold_horizon.gate_status()` 产物）、`horizonRelease`、`minHoldSeconds`、`exitPriorityLevel`。**`holdSeconds` 不在其中**——它已有唯一写点 | `session.py:581`；读侧 `schemas.py::trade_out` 提回顶层 |
| **E-6** | 成本口径混装**补上笔数**：扩展既有 `costBasisMixed` 旁加 `costBasisCounts: {net, gross}`，把既有那行提示升级为「本区间含 M 笔毛额口径成交（不含手续费）」。**不另起 `costBasisMix` 对象** | `db/ai_auto_repositories.py:167`（现算点）、`schemas.py:1391/1505`；`AiTradeStatsDetail.tsx:232-236` |
| **E-7** | 限价开仓撤单进漏斗：新增末格 `expired`（区别于 `filled`），在撤单回执处记点。记点函数签名必须是 `note_expired(side: str)`——**显式带方向** | `open_funnel.py`；`aiTradeStats/OpenFunnelCard.tsx` |
| **E-8** | **本轮只做观测**：`INSUFFICIENT_BALANCE` 记进漏斗与 `explain.meta`。逐档降档行为挂新总闸 `ai_trade_sizing_margin_precheck`（**默认 `False`**），并须先落实可用保证金的取数口径 | `sizing_runtime.py` / `live_auto/executor.py` 提交前 |

> **E-1 的边界（v2 收窄，评审 P0-5）**：`trades.reason` 今天**不是**一片 prose——实测 259 笔平仓里
> **37 笔已经是稳定机器码**：`external_close`(20) / `session_stop`(7) /
> `profit_guard_committed_exit`(6) / `profit_guard_trailing`(2) / `offline_exit`(1)。
> 其中四个在前端 `DEGRADE_REASON_LABELS`（`aiTrade.ts:503-560`）里**有条目**，
> 而 `exit_attribution._BACKTEST_REASONS` 与 `is_deterministic_reason()` 又按 `reason` 的
> **字面量**吃这类值（`exit_attribution.py:44-52` / `:96-105`）。
> 把它们统一改写成 `EXIT_SOURCES` 取值（`external` / `sessionStop` / `profitGuard`）⇒
> 中文标签当场失配、回测归因判据当场失灵，**且两处都不会报错**。
> 故 E-1 的作用域被钉死为：**只有 LLM 平仓那一条通路与开仓腿**。开仓腿写 `open_long` / `open_short`
> 并同步在 `DEGRADE_REASON_LABELS` 补两行（否则成交列表会显示裸英文码）。
> 五个既有短码逐字不动，由 `G-E11` 冻结。
>
> **E-1 的兼容性**：`reason` 是既有前端 `describeExecuted` 的输入之一。改成机器码后，
> `apps/web/lib/aiTrade.ts` 的展示分支必须**先读 `explain.headline`，取不到再回落到
> `reason`**——历史行（prose 截断）与新行（机器码）因此都能正确渲染。这一条是本改动
> **唯一**的跨端契约变更，必须有双向用例（`G-E3`）。
>
> **E-2 为什么不碰 `db/session.py`（v2 修正，评审 P1-10）**：`_ensure_trade_risk_columns`
> （`db/session.py:107-128`）的循环体只有 `if name not in columns: ALTER TABLE … ADD COLUMN`；
> `reason` 早就在列集里，加一行进那张字典**恒不执行**，是纯死码。SQLite 也不支持
> `ALTER COLUMN` 改宽度。事实是：SQLite 不强制 `VARCHAR` 长度，老库无需迁移，新建库由模型定义
> 生效——这与 §七 的说法一致，v1 的 §四.5 与 §七 在这一点上互相矛盾，现以 §七 为准。
>
> **E-7 为什么必须显式带方向（v2 补，评审 P2-4）**：`expired` 是**跨轮事件级**读数——限价开仓单
> 常在数轮之后才被撤，那时 `_intent_pending` / `_intent_side` 早已被 `note_executed` 清零。
> 照轮级标志去读，`expiredBySide` 会**恒为 0 且不报任何错**。这正是
> `open_funnel.note_filled(side)` 文档注释里写下的那个坑（directional-symmetry 评审 P0-1），
> 调用点本来就有这个值，零新取数。
>
> **E-8 为什么降级为观测（v2 修正，评审 P0-6）**：① 它是本表里唯一「改真实提交张数」却
> **没有总闸**的一项，与 §4.0 原则 4 直接冲突；② `LiveExecutor` 全文**没有任何可用保证金读数**
> （`grep -n "avail" live_auto/executor.py` 零命中），可用余额只在 `manager._gather_context`
> 的**启动期**取过一次（`manager.py:485`）。要在下单前预检，只有两条路：热路径每次开仓多打一发
> REST（新增 IO、且 §2.6 已有 1152 次 429），或拿一个启动期的陈旧值去决定真实仓位（更糟）。
> 本轮因此**先把 53 次 `INSUFFICIENT_BALANCE` 变成可见的漏斗读数**，降档留到下一轮——
> 那时才有数据回答「降一档够不够」。

### 4.6 轴 F · 把观测变成控制（治「我们早就看见了却没动」）

`ExitSourceCard.tsx` 的文档注释里已经记着与本文档 §2.2 完全一致的观察：
「16 次往返、15 次 Agent 主动平仓、唯一一次保护层平仓反而净赚 1.87 USDT」。
卡片同时明确声明 `exitMix` **不参与任何判定**，理由是「把它做成门槛会诱导『故意不平仓等保护层』」。

**那条理由是对的，本轮不推翻它。** 但「不做硬门槛」不等于「什么都不做」。新增：

- 后端 `attribution.py` 产出 `exitDiscretionVerdict`（纯读数，无门禁）：当
  `agent` 来源的往返数 ≥ `ai_trade_exit_verdict_min_samples`（默认 20）**且** 其净额为负
  **且** 至少一条确定性来源净额为正时，给出 `level: "adverse"` 与一句**数据化**的结论
  （逐字包含两个来源的笔数与净额，不得出现「已校准 / 经回测验证」这类措辞）。
- 前端在 `ExitSourceCard` 底部渲染该结论，并给一个直达「利润保护设置」的入口。
- **自动收紧 `exit_priority` 配额的能力本轮只建不开**：
  `ai_trade_exit_discretion_autotune_enabled: bool = False`。留给下一轮按验收卡定阈值。

> **wire 落点必须同时改 schema（v2 补，评审 P1-4）**：`exitDiscretion` 有两条通路，
> 其中一条**会被静默吞掉**：
> - 实时：`group_status()` → `GET /ai-trade/status`，该端点 `-> dict` 且**无 `response_model`**
>   （其 docstring 明写这是为了不让新键被吞），加法键自动可用；
> - 历史：`GET /ai-trade/history/{groupId}`，**绑 `response_model=AiTradeHistoryDetailOut`**
>   （`api/ai_auto.py:769`）。`exitDiscretion` 要挂在强类型的
>   `AiTradeExitMixOut`（`schemas.py:633-638`，今天只有 `totalExits` / `bySource` 两个字段）
>   或其宿主 `AiTradeHistorySummary`（`schemas.py:1484-1512`）上。
>   **不改 schema ⇒ FastAPI 静默丢键、面板永远是空的**——本仓库已成文的前科。
>   同理前端类型要落在 `apps/web/lib/aiTradeHistoryTypes.ts`。
> （`AiTradeHistoryMember.metrics` 是 `dict[str, Any]` 自由透传，故 E-6/E-7 挂在 `aiStats`
> 下的那部分不受此限；但 E-6 的 `costBasisCounts` 挂在 `summary` 上，同样要改 schema。）

---

## 五、文件 / 模块变更计划

> **新增文件 4 个**（`liveness.py` / `liveness_text.py` / `AiTradeLivenessBanner.tsx` /
> `scripts/audit_ai_trade_pnl.py`）、**修改后端 21 个**、**修改前端 13 个**、测试 14 个。
> 标 ★ 者为本轮**行为**变更（其余为观测 / 文案 / 数据完整性）。
> （v2 修正评审 P2-2 的三处计数不符，并补进 v1 漏列的 A0 审计脚本、`lib/aiTradeHorizon.ts`、
> `lib/aiTradeHistoryTypes.ts`、`pyproject.toml`。）

### 5.1 后端 `services/quant`

| 文件 | 动作 | 一行意图 |
|---|---|---|
| `app/ai_auto/liveness.py` | **新增** | ★ 活性看门狗纯核心：结构性阻塞探针 + 同码连续计数 → `ok/degraded/halt` 三档判定 |
| `app/ai_auto/liveness_text.py` | **新增** | 看门狗的中文文案与建议（后端下发，前端不硬编码） |
| `scripts/audit_ai_trade_pnl.py` | **新增** | A0 证据复现脚本（只读打开生产库；`@pytest.mark.audit` 默认排除）**（v1 漏列）** |
| `app/ai_auto/hold_horizon.py` | 修改 | ★ 新增 `RELEASE_LOSS_COVERED`；`build_snapshot` / `release_reason_for` 增 keyword-only `backstop_covered`。**`HorizonFacts` 不动**（v2 修正） |
| `app/ai_auto/horizon_runtime.py` | 修改 | ★ **（v1 漏列）** `_snapshot` 用它已经在建的那份 backstop wire 现算 `covered`（复用 `pause.is_backstop_covered`）并透传 |
| `app/ai_auto/hold_horizon_text.py` | 修改 | 新豁免码的披露文案（必须如实写明「已被交易所止损兜底，故不解最短持仓」） |
| `app/ai_auto/discipline.py` | 修改 | 新增 `unguarded_horizon()`（A-1 判据唯一真源）与 `inert_thresholds()` 两个纯函数；预设表不动一个数字 |
| `app/ai_auto/discipline_view.py` | 修改 | 纪律条渲染失效阈值（删除线 + 尾注） |
| `app/ai_auto/profit_guard_runtime.py` | 修改 | ★ E-3 峰值追踪与规则解耦（`_peak_only` 分支） |
| `app/ai_auto/wake_runtime.py` | 修改 | C-1 **纯函数** `coherent_wake_floor_s()`（抬升与回执不在这里，见下一行） |
| `app/ai_auto/session.py` | 修改 | ★ 接线看门狗（含接管既有 `_band_unavailable_streak`）/ C-2 跳轮 / E-5 meta 四键；**不新增 wake kind** |
| `app/ai_auto/open_funnel.py` | 修改 | E-7 新增 `expired` 末格（`note_expired(side)` **显式带方向**）；E-8 记 `INSUFFICIENT_BALANCE` |
| `app/ai_auto/attribution.py` | 修改 | F 轴 `exitDiscretionVerdict` |
| `app/ai_auto/stats_ext.py` | 修改 | 上述读数进统计 wire |
| `app/ai_auto/sizing_runtime.py` | 修改 | E-8 观测（降档行为挂 `ai_trade_sizing_margin_precheck`，默认关） |
| `app/ai_auto/pause.py` | 修改 | 新增第 6 个原因常量 `PAUSE_REASON_LIVENESS` + `PAUSE_REASON_TEXT` 一行（复用既有状态机，不新建状态） |
| `app/ai_auto/manager.py` | 修改 | ★ `config_json` 落 `discipline.inert`；`group_status()` 增 `liveness`；**接收会话侧的停手请求并调既有 `pause_group(..., flatten=False)`**（v2 修正） |
| `app/ai_auto/resume.py` | 修改 | **（v1 漏列）** 只加一条注释与用例锚点：冷续跑**不**经 A-1 互锁（`G-A8` 冻结） |
| `app/api/ai_auto.py` | 修改 | ★ A-1 互锁判据落在 `ai_trade_start` **端点内**（422 `AI_TRADE_UNGUARDED_HORIZON`）；★ C-1 抬升与 `discipline.adjustments` 回执 |
| `app/live_auto/executor.py` | 修改 | ★ E-1 **只改写 prose 的那条通路**（`:611` 模型平仓 + `:329`/`:566` 开仓腿）；E-8 观测钩子 |
| `app/db/models.py` | 修改 | E-2 `reason` `String(64)→String(128)` |
| `app/db/repositories.py` | 修改 | E-2 截断告警（按字符）；E-4 `create_run(start_ts=…)` |
| `app/db/ai_auto_repositories.py` | 修改 | **（v1 漏列）** E-6 在既有 `costBasisMixed` 现算点旁补 `costBasisCounts` |
| `app/schemas.py` | 修改 | `trade_out` 提回 E-5 四键；`liveness` / `inert` wire；**F 轴 `exitDiscretion` 与 E-6 计数必须进 `AiTradeExitMixOut` / `AiTradeHistorySummary`**，否则被 `response_model` 静默吞掉（v2 修正） |
| `app/config.py` | 修改 | 新增 **11** 个 `ai_trade_*` 设置（见 §六.3） |
| `pyproject.toml` | 修改 | **（v1 漏列）** 注册 `audit` marker 并把 `addopts` 改为 `-m 'not network and not audit'` |

> `app/db/session.py` **本轮不改**（v2 修正评审 P1-10：那个补列器只会 `ADD COLUMN`，
> 给它加一行扩宽 `reason` 是纯死码，且 SQLite 不支持 `ALTER COLUMN`）。

### 5.2 前端 `apps/web`

| 文件 | 动作 | 一行意图 |
|---|---|---|
| `components/Agent/ProfitGuardFields.tsx` | 修改 | ★ A-3 关闭后果清单 + 二次确认 |
| `components/Agent/AiTradeLaunchModal.tsx` | 修改 | 二次确认接线；422 `AI_TRADE_UNGUARDED_HORIZON` 的三按钮错误态 |
| `components/Agent/AiTradeLivenessBanner.tsx` | **新增** | 活性告警横幅（橙=degraded / 红=halt；**文案须逐字说明 halt 会暂停整组**） |
| `components/Chart/AiTradePanel.tsx` | 修改 | 挂载横幅 |
| `components/Agent/aiTradeStats/ExitSourceCard.tsx` | 修改 | F 轴结论行 + 直达利润保护入口 |
| `components/Agent/aiTradeStats/AiTradeStatsDetail.tsx` | 修改 | **（v1 漏列 / 且 v1 挂错了卡）** E-6 把既有 `costBasisMixed` 那一行升级为带笔数 |
| `components/Agent/aiTradeStats/OpenFunnelCard.tsx` | 修改 | E-7 `expired` 末格；E-8 `INSUFFICIENT_BALANCE` 计数 |
| `components/Agent/AiTradeHorizonFields.tsx` | 修改 | C-1 唤醒下限提示（`divisor>0` 时才渲染） |
| `lib/aiTrade.ts` | 修改 | 阻塞码 / 新增降级码（`open_long` / `open_short`）文案；`describeExecuted` 先读 headline；`AI_TRADE_PAUSE_REASON_TEXT` 补 `liveness_blocked` **与漏掉的 `resume_rollback`** |
| `lib/aiTradeHorizon.ts` | 修改 | **（v1 写成了 `lib/aiTrade.ts`，是错的）** `HORIZON_RELEASE_LABEL` 加 `loss_release_covered` 一行 |
| `lib/aiTradeTypes.ts` | 修改 | `AiTradeLiveness` / `inert` / `exitDiscretionVerdict` 类型；`AiTradePauseReason` 联合补 `liveness_blocked` |
| `lib/aiTradeHistoryTypes.ts` | 修改 | **（v1 漏列）** 历史侧 `exitDiscretion` / `costBasisCounts` 类型 |
| `lib/store.ts` | 修改 | ★ A-2 **两处**：`setAiTradeConfig` 写侧剥离 + persist `version: 31` 迁移剥掉已落盘的 `profitGuard.enabled`（须 null 守卫） |

> `components/Agent/AiTradeToggle.tsx` **本轮不改**（v2 修正评审 P1-7：`defaultProfitGuard`
> 照旧整对象透传即可，`enabled` 的回弹由 store 侧剥离 + 弹窗既有的
> `?? AI_TRADE_PROFIT_GUARD_DEFAULTS.enabled` 兜底自然完成，不必在两个消费点各剥一次）。
> `components/Agent/aiTradeStats/ProfitabilityCard.tsx` 同样**不改**——混装口径提示的既有落点是
> `AiTradeStatsDetail.tsx:232-236`，不是那张卡。

### 5.3 配置与文档

| 文件 | 动作 | 一行意图 |
|---|---|---|
| `.env.example`（**仓库根目录**，不是 `services/quant/`——已确认该路径下无此文件） | 修改 | 登记 **11** 个新键（漏登记 ⇒ 打包版旋钮静默失效，本仓库已栽过两次） |
| `docs/plans/ai-trade-loss-and-liveness-hardening/spec.md` | **新增** | 本文档 |

> `desktop/src/lib/config.js` **无需改动**：已确认第 80 行按 `AI_TRADE_` 前缀整体透传
> `agent.env`，新键自动可用。

---

## 六、接口设计

### 6.1 REST

**`POST /api/py/ai-trade/start`** — 新增一种 422：

```json
{ "detail": { "code": "AI_TRADE_UNGUARDED_HORIZON",
    "message": "已选择「日内波段」持仓周期（最短持仓 2 小时），但利润保护层被关闭：浮盈回吐将没有任何自动兑现通道。",
    "horizon": { "profileId": "intraday_swing", "minHoldSeconds": 7200 },
    "remedies": [
      {"id":"enable_profit_guard","label":"开启利润保护（推荐）"},
      {"id":"disable_horizon","label":"关闭持仓周期约束"},
      {"id":"ops_override","label":"运维总闸 AI_TRADE_REQUIRE_PROFIT_EXIT_WITH_HORIZON=false"} ] } }
```

**`GET /api/py/ai-trade/status`** — `members[].liveness` 新键（未装配 ⇒ **整键缺席**，不是 null）：

```json
{ "liveness": { "level": "degraded", "code": "base_rule_band_unavailable",
                "streak": 263, "sinceWall": 1788012345.0,
                "message": "已连续 263 轮因「均线通道数据不足」无法开仓；期间无任何成交。",
                "advice": "检查行情连接；或在弹窗关闭【基础策略】MA 通道约束。" } }
```

**统计读数的两条真实通路**（**v2 修正，评审 P1-4：`GET /ai-trade/stats` 这个端点并不存在**）：

| 通路 | 端点 | 是否绑 `response_model` | 结论 |
|---|---|---|---|
| 实时 | `GET /api/py/ai-trade/status` → `group.members[].metrics.aiStats` | **否**（端点 `-> dict`，其 docstring 明写这是为了不吞新键） | 加法键自动可用，无需改 schema |
| 历史 | `GET /api/py/ai-trade/history/{groupId}` | **是**（`AiTradeHistoryDetailOut`） | `members[].metrics` 是 `dict[str,Any]` 故 `aiStats.*` 自由透传；但挂在 `summary` 上的键**必须改 schema**，否则静默丢失 |

新增 / 扩展的三块只读读数：

```json
{ "summary": {
    "costBasisMixed": true,
    "costBasisCounts": { "net": 71, "gross": 186 },
    "exits": { "totalExits": 71, "bySource": [ "…既有…" ],
      "exitDiscretion": { "level": "adverse", "agentTrips": 36, "agentNetUsd": -67.52,
        "bestDeterministic": {"source":"profitGuard","trips":7,"netUsd":14.14},
        "note": "模型自主平仓 36 笔合计 -67.52 USDT；利润保护层平仓 7 笔合计 +14.14 USDT。" } } },
  "members": [ { "metrics": { "aiStats": {
      "openFunnel": { "…既有…": "…", "expired": 223, "insufficientBalance": 53 } } } } ] }
```

- `costBasisCounts` 与既有 `costBasisMixed` **同一处现算**（`db/ai_auto_repositories.py:167`），
  绝不第二处再判一次口径。
- `exitDiscretion` 嵌在既有 `AiTradeExitMixOut` 里（**加法子键**，既有两个字段形状逐字不变），
  沿用「嵌在既有整对象里，前端 reducer 一行不必动」的成文姿势。

### 6.2 WebSocket（`/ws/ai-trade/{groupId}`）

新增两种事件（均为**加法**，既有事件形状逐字节不变）：

| type | data | 何时发 |
|---|---|---|
| `liveness` | 同 REST 的 `liveness` 对象 | `level` 发生**跃迁**时发（不逐轮发，防刷屏） |
| `round_skipped` | `{ roundNo, reason:"horizon_locked", minHoldRemainingSeconds }` | C-2 跳轮时发 |

### 6.3 新增设置（全部 `Settings` 显式声明 + `.env.example` 登记）

| 键 | 默认 | 语义 |
|---|---|---|
| `ai_trade_require_profit_exit_with_horizon` | `True` | A-1 互锁总闸；关 ⇒ 判据整段不执行 |
| `ai_trade_loss_release_requires_uncovered` | `True` | B 轴总闸；关 ⇒ `RELEASE_LOSS` 恒全解（今日行为） |
| `ai_trade_liveness_enabled` | `True` | D 轴总闸；关 ⇒ 不装配、整键缺席 |
| `ai_trade_liveness_warn_rounds` | `20` | 连续阻塞多少轮置 degraded（0=关） |
| `ai_trade_liveness_halt_rounds` | `60` | 连续阻塞多少轮自动暂停（0=关） |
| `ai_trade_wake_horizon_divisor` | `0` | C-1 唤醒下限除数；**0=只观测不改行为** |
| `ai_trade_skip_round_when_gated` | `False` | C-2 跳轮总闸（默认关，先看观测） |
| `ai_trade_peak_tracking_always` | `True` | E-3 峰值追踪与保护层解耦 |
| `ai_trade_exit_verdict_min_samples` | `20` | F 轴结论的最小样本；`0` ⇒ 恒不出结论 |
| `ai_trade_exit_discretion_autotune_enabled` | `False` | F 轴自动收紧（本轮只建不开） |
| `ai_trade_sizing_margin_precheck` | `False` | **（v2 新增，评审 P0-6）** E-8 的降档行为总闸；关 ⇒ 只记观测读数、不改任何一次真实提交 |

> 共 **11** 个键，全部须在**仓库根目录**的 `.env.example` 登记（`G-Env` 门禁；
> 既有先例是 `test_ai_intel_wiring.py:480` 的 `ai_trade_intel_*` 双向对齐用例，
> 本轮照抄它的写法把家族前缀换成本轮这 11 个键即可）。
> 打包版透传无需改动：`desktop/src/lib/config.js:79-88` 已按 `AI_TRADE_` 前缀整体透传 `agent.env`。

---

## 七、数据模型

**零新表、零破坏性迁移。** 全部变更走既有幂等补列器。

| 对象 | 变更 | 迁移 |
|---|---|---|
| `trades.reason` | `VARCHAR(64)` → `VARCHAR(128)` | **零迁移、不碰 `db/session.py`**（v2 修正评审 P1-10）：`_ensure_trade_risk_columns` 只会 `ADD COLUMN`，而 `reason` 早在列集里 ⇒ 加一行是死码；SQLite 也不支持 `ALTER COLUMN`。**SQLite 不强制 VARCHAR 长度**，故老库直接就能写 128 字符；新建库由模型定义生效。已存在的 222 行截断值**不回填**（原文已不可得） |
| `trades.peak_pnl` | 语义扩展：不再要求保护层装配 | 无（列已存在）。**仍绝不填 0**——0 会被捕获率聚合当成「峰值就是 0」 |
| `strategy_runs.start_ts` | 由 `create_run` 显式写入 | 无。历史行保持 0；**读侧必须先判 `start_ts > 0` 再算时长**，取不到就如实显示「未记录」 |
| `trades.explain_json` | `meta` 增补 **4** 键（E-5；`holdSeconds` 已有写点，不重复写） | 无（JSON 列）。老行缺键 ⇒ 读侧显示「未记录」 |
| `strategy_runs.config_json` | 增补 `discipline.inert: string[]` | 无（JSON 列） |

**内存形状（新增）**

```python
# liveness.py（新文件）
LivenessRule(warn_rounds: int, halt_rounds: int)
LivenessState(code: str|None, streak: int, first_wall: float)

# hold_horizon.py —— **keyword-only 形参，不是 HorizonFacts 的字段**（v2 修正，评审 P0-2）
build_snapshot(..., *, backstop_covered: bool | None = None)
release_reason_for(..., *, backstop_covered: bool | None = None)
#   None ⇒ 视为未覆盖 ⇒ 逐字回退今日行为（回测 / 未装配 / 总闸关三条路径都落在这一格）

# horizon_runtime.py —— 覆盖态的唯一现算点，复用 pause.is_backstop_covered
HorizonRuntime._snapshot() -> dict     # 内部：wire = self._backstop_wire(now); covered = …(wire)
```

---

## 八、测试与验收标准

### 8.1 零回归门禁（先跑，红则停工）

- `A0` **证据复现脚本** `services/quant/scripts/audit_ai_trade_pnl.py`（新增，只读打开生产库）：
  重跑 §二的每一张表并断言关键数字（净额子集 71 笔 / −47.36 / `agent` −67.52 /
  `profitGuard` +14.14 / 违规 31 笔 −43.28 / `reason` 长度恒 64 / `peak_pnl` NULL 213）。
  **这是本轮唯一的「事实基线」，任何后续轮次改动都必须能重跑它。**
- `A1` 全部总闸置 `False`（`skip_round`/`autotune`/`divisor`/`margin_precheck` 保持默认关）时，
  **A / B / C / D / F 五轴与 E-3 / E-8 的行为等价**：
  `test_ai_trade_all_new_switches_off_is_byte_identical`
  —— 对同一组冻结输入，`payload` / `prompt` / `decision` / `snapshot` 四者逐字节等于 `5337da1`。
- `A1b` **（v2 新增，评审 P1-11）** A1 的命题**不覆盖**六处**有意的、无闸的加法变更**，它们必须
  各自有一条兼容性用例，且必须逐条写在这里而不是被 A1 的措辞含混过去：

  | 变更 | 为什么不挂闸 | 兼容性用例 |
  |---|---|---|
  | E-1 `reason` 机器码 | 它是数据完整性修复，挂闸等于「可以选择继续写坏数据」 | `G-E3`（新旧两种行都渲染）+ `G-E11`（五个既有短码逐字不变） |
  | E-2 列宽与告警 | 纯加法，老行不受影响 | `G-E2` |
  | E-4 `start_ts` | 纯加法，历史行保持 0 | `G-E6` |
  | E-5 `meta` 四键 | JSON 列加法，老行缺键即「未记录」 | `G-E7` |
  | E-6 / E-7 stats 加法键 | 只读读数 | `G-E8` / `G-E9` |
  | A-2 / A-3 前端默认与二次确认 | 它们改的是**默认值与确认流程**，不改任何一条服务端判据 | `G-A4` / `G-A5` |

  **`decision` 逐字节等价的例外须显式记录**：E-1 会改 close 的 `reason` 字面量，
  故 A1 的 `decision` 断言比对的是 `(action, size_fraction, confidence)` 三元组 + 门禁降级码，
  **不含**模型平仓那条通路的 `reason` 字符串。这一点写进用例 docstring，不靠读者推断。
- `A2` 全量 `pytest`（后端）+ `vitest`（前端）。**必须全量跑，不得用 `-k` 子集判回归**
  （既有教训）。
  **基线由实施节点在动第一行代码之前自己跑一遍取当日值**——本评审在 HEAD 上实测
  `pytest tests --collect-only -q` 为 **5026 collected（2 deselected）**；
  v1 写的「3627 passed」已过期约 1400 个用例（评审 P1-9），任何写死的数都会再次过期，
  故本节只钉**方法**不钉**数字**：`git stash` 干净树跑一遍存成 `baseline.txt`，改完再跑一遍 diff。
  跑法：`services/quant/.venv/Scripts/python -u -m pytest`（`-u` 必需；不得并发）。
  前端：`NODE_ENV=test pnpm -C apps/web vitest run`。

### 8.2 轴 A（利润侧确定性离场）

| 用例 | 断言 |
|---|---|
| `G-A1` | `horizon.min_hold_s>0` + `profitGuard.enabled=false` ⇒ 422 `AI_TRADE_UNGUARDED_HORIZON`，且 `remedies` 恰好三条 |
| `G-A2` | 同上但总闸关 ⇒ **200**，会话正常起（回退路径存在） |
| `G-A3` | `horizon` 未装配 + 保护层关 ⇒ **200**（互锁只针对致命组合，不扩大打击面） |
| `G-A4` | 前端：一次取消勾选并启动后，**下一次**打开弹窗 `profitGuardEnabled === true`（非粘性） |
| `G-A5` | 前端：`enabled=false` 时后果清单渲染 5 条，且硬止盈那条的数值等于服务端下发值（源码扫描门禁：`ProfitGuardFields.tsx` 内不得出现 `0.5`/`50` 字面量） |
| `G-A6` | `inert_thresholds()`：`profit_rule=None` ⇒ 返回恰含 `ai_trade_profit_guard_hard_tp_return_pct`；`profit_rule` 装配 ⇒ 返回空 |
| `G-A7` | `config_json.discipline.inert` 与 `group_status()` 两处**同值**（同一装配点，不得各算一份） |
| `G-A8` | **（v2 新增，评审 P0-3）冷续跑不受互锁影响**：构造一份「周期已装配 + 保护层未装配」的 `start_wire` 暂停契约 ⇒ `resume.assess_group` / `_rebuild_request` 正常返回，**绝不出现 409 `AI_TRADE_RESUME_CONTRACT_INVALID`**；同一份 body 走 `POST /ai-trade/start` 则 422。两条断言必须在**同一个**用例里，否则下次有人把判据挪回装配链时它照样绿 |
| `G-A9` | **（v2 新增）** `unguarded_horizon()` 纯函数逐格：`(profit=None, horizon=None)`→False、`(None, min_hold=0)`→False、`(None, min_hold>0)`→True、`(装配, min_hold>0)`→False、`enabled=False` 时**恒 False** |

### 8.3 轴 B（亏损侧豁免收窄）

| 用例 | 断言 |
|---|---|
| `G-B1` | 浮亏过阈 + `backstop_covered=True` ⇒ `releaseReason == "loss_release_covered"`，`releasedGates == ["G2","G3"]`，`agent` 的 `close` 在 `hold_s < min_hold_s` 时**仍被 G1 否决** |
| `G-B2` | 浮亏过阈 + `backstop_covered=False` ⇒ 逐字等于今日：`"loss_release"` + 三闸全解 |
| `G-B3` | `backstop_covered=None`（缺省）⇒ 同 `G-B2`（**零回归的结构性保证**） |
| `G-B4` | 总闸关 ⇒ 恒 `"loss_release"`，与 `G-B2` 逐字节相同 |
| `G-B5` | 利润保护层 / 交易所 OCO / 会话止盈损 / 强制时间平仓四条路径在 `G-B1` 场景下**均不受影响**（`gated_kind` 恒 `None`） |
| `G-B6` | 文案镜像门禁：`hold_horizon_text._RELEASE_LABEL` 与 **`apps/web/lib/aiTradeHorizon.ts` 的 `HORIZON_RELEASE_LABEL`** 的豁免码集合**等值**（v2 修正：v1 比的是 `lib/aiTrade.ts`，那是另一张表，比了也白比） |
| `G-B7` | **（v2 新增，评审 P0-2）覆盖态取自真实 wire、不是凭空的 bool**：喂一份 `basis="exchange" + coverageRatio=1.0` 的 backstop wire 给 `HorizonRuntime` ⇒ 快照 `releaseReason == "loss_release_covered"`；把 `basis` 改成 `"local"` 或把 `coverageRatio` 置 `None` ⇒ 退回 `"loss_release"`。**断言必须从 `HorizonRuntime.refresh()` 这一层进**，直接给 `build_snapshot` 传 `backstop_covered=True` 的用例**不算数**——那正好绕过了 v1 出问题的那一段 |
| `G-B8` | **（v2 新增）** 上线后复核闸：`aiStats.horizon.releaseReasons` 里 `loss_release*` 两码之和 == E-5 落下的 `horizonRelease` 为该两码的平仓笔数（±配对误差）。§2.9(b) 的全库聚合是**离线**证据，这条把它变成**在线**可复核的读数 |

### 8.4 轴 C / D（节奏与活性）

| 用例 | 断言 |
|---|---|
| `G-C1` | `divisor=24` + `targetHoldMinSeconds=14400` ⇒ 下限 600s；用户传 180s ⇒ 抬到 600s 且 `discipline.adjustments` 多一条 |
| `G-C2` | `divisor=0`（默认）⇒ 唤醒周期逐字不变、`adjustments` 不变 |
| `G-C3` | C-2 跳轮：四个条件全真 ⇒ 不调 LLM、发 `round_skipped`；**任一条件为假 ⇒ 照常调用**（逐条枚举四个反例） |
| `G-D1` | 连续 20 轮同一阻塞码且零成交 ⇒ `level=="degraded"`；第 60 轮 ⇒ `halt` 且 **`manager.pause_group(group_id, reason="liveness_blocked", flatten=False)` 被调用一次**（v2 修正：不是 `session.pause(hold_position=True)`，那个形参不存在） |
| `G-D2` | 中途**任意一次成交** ⇒ `streak` 归零（成交即活着） |
| `G-D3` | 阻塞码在两个值之间交替 ⇒ `streak` 恒 ≤ 1，永不告警 |
| `G-D4` | `halt` **绝不平仓**：`executor.submit` / `executor.flatten` 在整个 `halt` 路径上零调用；且回执里 `holdPosition is True` |
| `G-D5` | 总闸关 ⇒ `liveness` 键**缺席**（不是 `null`），WS 不发该事件；既有的 `AI_TRADE_BASE_RULE_UNAVAILABLE`（第 3 轮那一条）**逐字节照发** |
| `G-D6` | 复现 D4：喂一段 `base_df` 恒不足 21 根的行情、**且让模型每一轮都返回 `hold`（=生产实测的那种形态，`degrade` 恒 `None`）** ⇒ 第 60 轮必然处于 `halt`。**这是本轮最重要的一条回归护栏**，也是 v1 设计过不去的那一条：按「数 degrade 码」实现它必红（评审 P0-1） |
| `G-D7` | **（v2 新增）** halt **不在决策轮内 `await` 暂停**：在 `_run_decision_round` 里触发 halt 后，该轮必须正常跑完并落 trace，`asyncio.CancelledError` 零发生（v1 的写法会自己 cancel 自己，评审 P0-4 ③） |
| `G-D8` | **（v2 新增）** halt 之后 `ai_trade_group_states` 那一行的 `status == "paused"`、`pause_reason == "liveness_blocked"`——即它**真的走了 `pause_group` 的 W4 落盘**，而不是只改了内存（P0-4 ②：绕过它 ⇒ 重启后被复活成「运行中」） |
| `G-D9` | **（v2 新增）** 暂停原因四镜像等值门禁：`pause.PAUSE_REASON_TEXT` 的键集 == `aiTradeTypes.AiTradePauseReason` 的联合成员 == `aiTrade.AI_TRADE_PAUSE_REASON_TEXT` 的键集（此闸今天就是红的——`resume_rollback` 在前端文案表里缺失，评审 P2-6，本轮顺手补齐） |
| `G-D10` | **（v2 新增，评审 P1-2）** `BLOCKING_CODES` 的每一个值都**在后端有唯一产出点**、且**不含 `(` 参数后缀**（源码扫描门禁；注意 `core.autocrlf=true`，正则必须写 `\r?\n`） |

### 8.5 轴 E / F（归因完整性）

| 用例 | 断言 |
|---|---|
| `G-E1` | **模型平仓**的 `reason` 是机器码（不再是 prose），prose 完整存在于 `explain.headline` 且**未被截断**；开仓腿 `reason ∈ {open_long, open_short}` |
| `G-E11` | **（v2 新增，评审 P0-5）** 五个既有短码 `external_close` / `session_stop` / `profit_guard_committed_exit` / `profit_guard_trailing` / `offline_exit` 在改动前后**逐字不变**，且每一个都能在 `DEGRADE_REASON_LABELS` 里前缀命中；`exit_attribution._BACKTEST_REASONS` 的六个键同样逐字不变 |
| `G-E2` | 写入超长 `reason` ⇒ `logger.warning` 恰一次，且按字符截断不产生半个汉字 |
| `G-E3` | 前端 `describeExecuted`：新行（机器码 + headline）与老行（64 字截断 prose、无 headline）**均渲染出可读文案** |
| `G-E4` | 保护层未装配 ⇒ `peak_pnl` **非 NULL**（E-3 生效）；总闸 `peak_tracking_always=False` ⇒ 恢复 NULL |
| `G-E5` | `peak_pnl` 在「一路 unavailable」时仍为 `None`，**绝不为 0** |
| `G-E6` | 新建 run 的 `start_ts > 0`；读侧对 `start_ts == 0` 的历史行显示「未记录」而非 1970 年 |
| `G-E7` | 违反最短持仓的平仓，其 `explain.meta.horizonGate == "released"` 且 `horizonRelease` 非空——**§三 D9 的那个问题在产品里可直接回答**。另断言 `holdSeconds` **仍由既有写点产出**（同一笔成交只出现一次该键，evidence：`meta` 里不得有第二个写点覆盖它） |
| `G-E8` | 混装区间 ⇒ `costBasisMixed is True` 且 `costBasisCounts` 两项均 > 0，`AiTradeStatsDetail` 那一行**带上笔数**；纯净区间 ⇒ `costBasisMixed is False` 且该行逐字回退到今天的措辞 |
| `G-E9` | 限价开仓撤单 ⇒ 漏斗 `expired += 1` 且 `filled` 不变；**跨轮撤单**（撤单回执晚于 `note_executed` 数轮到达）时 `expiredBySide` 仍按成交/委托腿自己的方向落格（评审 P2-4） |
| `G-E10` | **（v2 改写）** 默认（`ai_trade_sizing_margin_precheck=False`）：`INSUFFICIENT_BALANCE` ⇒ 漏斗 `insufficientBalance += 1` 且 `explain.meta` 有记录，**提交张数逐字不变**。开闸后：降档提交且 `explain.meta.sizeDowngrade` 有值；降到最小档仍不足 ⇒ 才报错 |
| `G-F1` | 样本 < `min_samples` ⇒ `exitDiscretion.level == "insufficient"` 且文案如实写「样本 12/20，结论未生效」 |
| `G-F2` | 文案中不得出现「已校准」「经回测验证」（源码正则门禁；注意 `core.autocrlf=true`，正则必须写 `\r?\n`） |

### 8.6 端到端验收（人工，打包版）

1. 干净检出 + `cp services/quant/.env`（既有教训：不 copy 则 worktree 起不来）。
2. 开【日内波段】+ 关【利润保护】⇒ 必须被 422 拦住，且三个补救按钮都能真的解决问题。
3. **（v2 新增，对应 P0-3）** 用**老版本**起一个「日内波段 + 利润保护关」的会话 → 暂停 → 换成
   本轮的包 → 点【继续】⇒ **必须能续跑**，不得出现「启动契约已失效，无法继续」。
   这一条是本轮唯一一处「改错了会让用户的真实持仓失去托管」的地方，必须人工过。
4. **（v2 新增，对应 P1-7）** 关一次利润保护并启动 → 结束 → **重开软件** → 再开弹窗 ⇒
   利润保护必须是**开**的（迁移生效）。只做写侧剥离的实现会在这一步现原形。
5. 断开网络 20 分钟 ⇒ 面板出现橙色活性横幅并给出阻塞码与轮数；60 轮后自动暂停且**仓位仍在**，
   横幅逐字说明了「将暂停整组」；**重启软件后该组仍是 `paused`**（对应 `G-D8`）。
6. 打开统计面板 ⇒ 退出来源卡底部出现数据化结论行，点击可直达利润保护设置；
   **历史详情页**（走 `response_model` 那条路）上同一条结论也在（对应 P1-4）。

### 8.7 本轮的成功判据（诚实口径）

**本轮不承诺盈利。** 本轮承诺四件可验证的事：

1. 「装了持仓周期锁却没有利润侧钥匙」的会话**不可能被新启动**（G-A1），
   **同时已经存在的这类会话仍然能续跑**（G-A8）——两条缺一不可。
2. 已被交易所止损兜底时，最短持仓**不再**被浮亏解除（G-B1/G-B7）——那条通道实测 −40.89 / 13 笔，
   且 §2.9(b) 已证明它是**历史上唯一开过火的豁免**。
3. 连续 60 轮无法开仓的会话**会自己停手**并说明原因（G-D1/G-D6/G-D8），
   且**在模型每轮都自己写 `hold`（`degrade` 恒 `None`）的真实形态下**依然成立。
4. 「这笔为什么在最短持仓内被平掉」在产品里**可以直接回答**（G-E7）。

下一轮据此用真实验收卡校准 `warn/halt_rounds`、`wake_horizon_divisor`、
`exit_discretion_autotune`。**任何 UI 文案与文档不得声称本轮阈值「已校准」。**

---

## 九、风险与缓解

| # | 风险 | 影响 | 缓解 |
|---|---|---|---|
| R1 | **A-1 互锁把老用户挡在门外**：周期模式一共只被装配过 **55** 次，其中 **50 次是裸的**（§2.9(c)），即这条互锁会拦下 91% 的周期会话 | 升级后第一次启动即 422，观感是「坏了」 | 错误体给三条**可点击**的补救；前端把「开启利润保护」做成默认高亮按钮，一键改好后自动重试提交；总闸可一键关。**并且 A-2 的迁移（v31）会先把粘性关闭态清掉**，所以绝大多数人升级后根本不会撞上这个 422——这两条修法必须同批上线，只上 A-1 会把 R1 放大到最坏 |
| R1b | **（v2 新增，评审 P0-3）互锁误伤续跑** | 带真实持仓的暂停会话永久无法续跑 | 判据**只在 `ai_trade_start` 端点内**，装配链（`build_start_request`，冷续跑复用它）一行不动；`G-A8` 在同一个用例里同时断言「start 422」与「resume 200」，任何把判据挪回装配链的改动都会当场判红 |
| R2 | **B 轴收窄 G1 导致真止损更晚**：极端行情下从半止损位走到真止损位，单笔亏损变大 | 单笔最大亏损上升 | 仅在 `backstop_covered is True`（**已核实**交易所侧止损存在）时收窄；未覆盖/未知一律走今日全解。另：`loss_release` 仍解 G2/G3，模型仍可在**确认轮数满足后**离场 |
| R3 | **C-2 跳轮跳掉了本该反应的轮次** | 错过突发行情 | 四条件里的第 3、4 条即为安全内核：外部事件唤醒（信号/情报/手工）与保护层已武装两种情形一律不跳；且默认关 |
| R4 | **看门狗误停**：把一段合法的「无机会」判成阻塞 | 用户被动暂停 | 判据要求「**同一个**阻塞码连续 N 轮」且「期间零成交」；而四条码全部是**结构性不可能开仓**的事实（通道算不出 / 判定价不可信 / 行情降级 / 决策器不可用），**「有机会但我不想开」在这四条里一条都不落**，`blocking_code` 为 `None` ⇒ `streak` 归零。`halt` 只暂停不平仓，一键继续 |
| R11 | **（v2 新增，评审 P0-4）halt 的粒度是组级**：`manager.pause_group` 没有成员粒度，一个成员长期阻塞会把整组停手 | 另外两个健康的成员被连坐暂停 | ① 四条阻塞码里三条的根因是行情通道，几乎必然组级同因，「只停一个、另两个继续在同一条坏通道上跑」是更差的结果；② 横幅与暂停回执必须**逐字说明将暂停整组**，不让用户在事后才发现；③ `halt_rounds=0` 可单独关掉自动停手而保留 `degraded` 横幅；④ 下一轮若要做成员粒度，需要的是 `pause_member`——那是一条新的生命周期通道，**不在本轮范围内**（本轮明确不做，见 §十一） |
| R5 | **E-1 改 `reason` 语义破坏历史读法** | 老成交显示异常 | 读侧先 `explain.meta.headline` 后回落 `reason`，双向用例 `G-E3` 冻结 |
| R6 | **E-3 峰值追踪在未装配时引入新状态**，可能与保护层装配态的 epoch 语义分叉 | 捕获率读数不可比 | `_peak_only` 复用**同一个** `advance_state`，只是不做任何判定；用例断言两条路径的 `peak_px` 在同一输入下逐字节相等 |
| R7 | **`.env.example` 漏登记新键** ⇒ 打包版旋钮静默失效 | 运维无退出口 | `G-Env` 源码扫描门禁：`config.py` 中每个新 `ai_trade_*` 字段必须在根 `.env.example` 出现（本仓库已栽两次，故做成门禁而非约定） |
| R8 | **前端硬编码后端数值** ⇒ 下次调参静默分叉 | 用户看到的与生效的不一致 | `G-A5` 源码扫描；沿用 `/ai-trade/horizon-profiles` 既有纪律 |
| R9 | **本轮触及 25 个后端文件**（v2 复核后的数），与在制未提交变更冲突 | 合入困难 | 工作树现有 40+ 未跟踪 `docs/diagnoses` 目录与 1 个已改文件（`services/quant/app/api/ai_intel.py`）；实施节点须先 `git status` 核对，**行级精确暂存**，混合文件按既有配方拆分处理 |
| R10 | 生产库 119 MB、日志 252 MB，`A0` 脚本可能拖慢 CI | CI 变慢 | `A0` 标 `@pytest.mark.audit`，**默认排除**，只在本地/发布前 opt-in 跑；CI 只跑合成夹具版。**（v2 补，评审 P2-5）默认排除不是自动的**：`services/quant/pyproject.toml` 现在的 `addopts` 是 `-m 'not network'`、`markers` 里只注册了 `network`，两处都要改，否则 A0 会进全量跑并把 CI 拖慢、且未注册 marker 会告警 |
| R12 | **（v2 新增，评审 P2-8）`A0` 只钉数字不钉匹配式** | 下一轮复现不出同一个数 | `A0` 必须把**每一条日志签名的匹配式本身**写进脚本常量（本评审复算时 `disconnect` 裸计 2981、`429` 裸计 3541，与 v1 的 2936 / 1152 不同，正是因为匹配式没写下来）；成交侧同理，配对规则（按 run FIFO）要写成脚本里一个具名函数而不是散在 SQL 里 |

---

## 十、实施顺序（建议给下游节点）

分四批，每批自成一个可回滚单元；**每批结束都跑全量测试**。

1. **第一批 · 归因（无行为变更，先让证据可得）** — E-1 ~ E-8 + `A0` 审计脚本。
   这批先做的理由：后面三批的验收全部依赖这批产生的读数；先改行为再补观测，等于蒙眼调参。
   **E-5 必须在这一批里落地**——`horizonRelease` 是第四批 B 轴 `G-B8` 上线复核的唯一数据来源。
2. **第二批 · 活性（D4/D5）** — `liveness.py` + 接线 + 横幅 + 停手请求通道。独立性最强、
   风险最低、用户可感知收益最大（不再静默空转）。
   **本批必须一次做完 `G-D7`/`G-D8`/`G-D9` 三条**（不自我 cancel、W4 真的落盘、四镜像等值），
   它们不是打磨项，是这一批「做没做对」的判据。
3. **第三批 · 利润侧确定性离场（A 轴）** — 互锁 + 非粘性（**写侧剥离 + v31 迁移，两处**）
   + 后果清单 + 失效阈值标注。
   **A-1 与 A-2 必须同批上线**：只上 A-1 会让 91% 的周期会话在升级后第一次启动就吃 422
   （§九 R1）；A-2 的迁移先把粘性关闭态清掉，绝大多数人根本不会撞上它。
4. **第四批 · 亏损侧收窄与节奏（B/C/F 轴）** — 全部默认关或默认保守，靠第一批的读数在下一轮定阈值。
   B 轴上线后先跑 `G-B8` 的在线复核，确认 `loss_release*` 的实际分布与 §2.9(b) 的离线聚合一致，
   再谈调阈。

---

## 十一、明确不做（防止下一轮重犯）

1. **不加 `RELEASE_PROFIT`**（理由见 §4.1 A-5）。
2. **不把 `exitMix` 做成硬门槛**（`ExitSourceCard` 的原始理由成立：会诱导「故意不平仓等保护层」）。
3. **不新增第 10 种 wake kind**（要改九张手抄注册表，收益不抵风险）。
4. **不回填历史数据**（截断的 `reason` 原文已不可得；`start_ts=0` 的真实起点无从考证。
   伪造一个看起来合理的值，比留一个诚实的「未记录」更糟）。
5. **不改任何一个纪律预设里的数字**（它们从未被回测校准过，本轮也不具备校准条件；
   动它们会让第一批产生的读数失去对照基线）。
6. **（v2 新增）不做成员级暂停**。轴 D 的 halt 走既有的 `manager.pause_group`，粒度就是组级。
   要做成员级就得新开一条 `pause_member` 生命周期通道（连带续跑复核、状态行、前端三处状态机），
   那是一个独立特性，不该藏在一个看门狗里搭车。代价与理由写在 §九 R11。
7. **（v2 新增）不在下单热路径新增交易所取数**。这是 E-8 被降级为观测的直接原因：
   `LiveExecutor` 没有可用保证金读数，为一次预检去多打一发 REST，在 §2.6 已有 1152 次 429
   的链路上是一笔不划算的买卖。降档留到下一轮，先用漏斗读数回答「到底差多少」。
8. **（v2 新增）不改 `RELEASE_GATES` 冻结表的既有四行，也不调整豁免的相对顺序**。
   `RELEASE_LOSS_COVERED` 是 `RELEASE_LOSS` 的**同位替换**（同一个 `if` 分支返回两者之一），
   不占新位次——`hold_horizon.py:509-512` 的成文纪律「既有豁免的相对顺序是冻结口径」照旧成立。

---

## 十二、评审结论

### 结论：**有条件通过**

v1 的诊断值得肯定：它没有停在「某个函数写错了一行」，而是把亏损归因到**层与层之间的极性组合**，
并且给出的六组量化证据我逐条复算全部命中（§2.9(a)）——211 条 run、520 条腿、退出来源逐格净额、
`193 / 211` 的保护层缺席、`45` 条「承诺了硬止盈却没有执行层」、以及六个日志签名的绝对次数，
一个数都没有虚构。**这份证据基线本身就是本轮最有价值的产物**，`A0` 脚本必须把它固化下来。

但 v1 有一个贯穿性的问题，且它在四条轴里出现了三次：**修法所依赖的那个事实，在这个仓库里不成立。**

- 轴 B 的判据读了一个 `classify_backstop` 从不产出的键，且挂在一个拿不到 backstop 运行时的
  结构体上 ⇒ 恒空转、不报错；
- 轴 D 的信号取自「本轮降级码」，而生产事故现场 456 轮里只有 12 轮产生过开仓意愿 ⇒ 看门狗
  在它唯一要治的那场事故里**永远数不到 2**；
- 轴 A 的 422 落在一条**同时被冷续跑复用**的装配函数上 ⇒ 会把 50 条可能带着真实持仓的暂停
  会话永久锁死在「启动契约已失效」。

这三条是同一个形态——**新判据取自不携带该信息的入参**，与 blackboard 里 `AC-P0-1` /
`AC-FINAL-1` / `AC-FINAL-2` 记录的是同一种失效模式。它不会在单元测试里判红（因为用例往往
直接构造那个不存在的入参），只会在生产里安静地什么都不做。这也是为什么本次评审把
`G-B7`（**断言必须从 `HorizonRuntime.refresh()` 这一层进**）和 `G-A8`（start 与 resume 在
**同一个用例里**互相对照）写成那种形状——它们守的不是功能，是「判据取自哪里」。

**6 条 P0 与 11 条 P1 已在正文逐条改掉**，改动都写明了落点与理由；8 条 P2 记录在案、不阻断实施。
另外本次评审补进三组新采到的生产读数（§2.9 b/c/d），其中一组把 D3 的判定从「交叉推断」
升级为「强佐证」：全库 `releaseReasons` 聚合是 `{"loss_release": 12}`，**其余四条豁免历史上
一次都没触发过**——轴 B 收窄的正是唯一开过火的那条通路，打击面精确。

### 放行条件（六条，缺一不可）

1. **A-1 的判据只能出现在 `ai_trade_start` 端点内**，`build_start_request` 一行不动；
   `G-A8` 必须在同一个用例里同时断言「start 422」与「冷续跑 200」。
   *（这是唯一一条「改错了会让用户的真实持仓失去托管」的地方，也是 §8.6 第 3 步人工验收项。）*
2. **A-1 与 A-2 同批上线，且 A-2 必须含 persist `version: 31` 迁移**（须 null 守卫）。
   只上 A-1 会让 91% 的周期会话在升级后第一次启动就吃 422；只做写侧剥离救不了已落盘的那 193 份偏好。
3. **轴 D 的阻塞信号必须是结构性探针**（`ma_band_unavailable` / `mark_px_unavailable` /
   `feed_degraded` / `llm_unavailable`），**且接管既有 `_band_unavailable_streak`，不新建第二份计数**。
   `G-D6` 必须在「模型每轮都自己写 `hold`、`degrade` 恒 `None`」的形态下判绿——那才是真实事故形态。
4. **轴 D 的 halt 走 `manager.pause_group(..., flatten=False)`，并在决策轮之外执行**。
   `G-D7`（不自我 cancel）与 `G-D8`（W4 真的落盘、重启后仍是 `paused`）两条一起绿才算做完。
5. **轴 B 的覆盖态在 `HorizonRuntime._snapshot` 现算、复用 `pause.is_backstop_covered`**，
   `HorizonFacts` 不动；`G-B7` 的断言从 `refresh()` 进。
   前端镜像加在 **`lib/aiTradeHorizon.ts` 的 `HORIZON_RELEASE_LABEL`**，不是 `lib/aiTrade.ts`。
6. **E-1 的作用域钉死在「今天写 prose 的那条通路」**，五个既有短码逐字不动（`G-E11`）；
   **E-8 本轮只做观测**，降档挂 `ai_trade_sizing_margin_precheck`（默认关）。

### 给实施节点的三条提醒

- **先跑基线再动代码**：本评审在 HEAD 上实测 `pytest tests --collect-only -q` 是
  **5026 collected（2 deselected）**，v1 写的 3627 已过期约 1400 个用例。
  `git stash` 干净树跑一遍存 `baseline.txt`，改完 diff——不要相信文档里任何一个写死的通过数。
- **工作树是脏的**：现有 40+ 未跟踪的 `docs/diagnoses/` 目录与一个已改文件
  （`services/quant/app/api/ai_intel.py`）。本轮触及 25 个后端 + 13 个前端文件，
  提交时必须**逐路径 add**，混合文件按既有配方行级精确暂存。
- **`.env.example` 在仓库根目录**（`services/quant/` 下没有这个文件）。11 个新键一个都不能漏，
  照抄 `test_ai_intel_wiring.py:480` 的双向对齐用例把前缀换掉即可——
  漏登记不会报错，只会让打包版的运维旋钮**静默失效**。

### 规模判断

**规模合适，不是过度设计。** 四条轴对应四个已被数据钉死的缺陷，没有一条是「顺手做了会更好」；
唯一一处越界的是 E-8（它想在热路径里新增一次取数并改真实下单），已被降级为观测。
反过来也没有欠规格：本轮**不承诺盈利**这一条写得很好，`§8.7` 那四条可验证承诺是诚实的口径，
`§十一` 的「明确不做」清单（v2 扩到 8 条）把下一轮容易重犯的坑都提前钉住了。

**审定人**：Anthropic 工程团队 · 设计评审节点
**审定日期**：2026-09-02
**文档版本**：**v2**

---

## 实施过程发现的方案缺陷

> 实施节点在落地过程中发现的、与本设计文档不符或文档未覆盖的事实。**每一条都写明「文档怎么说、
> 仓库里实际是什么、最终怎么做」**，并已按修正后的做法实施。不改文档正文——正文是评审当时的
> 判断，本节是它与代码相遇之后的差异，两者都要留着。

### I-1 · E-1 的落点：`live_auto/executor.py` 是**共用**的，改不得

**文档**：§五.1 把 E-1 落在 `app/live_auto/executor.py`（`:611` 模型平仓 + `:329`/`:566` 开仓腿）。

**事实**：`LiveExecutor` 被 **AI-trade 与 `live_auto`（非 AI 自动交易）共用**，它收到的 `reason`
来自 `Signal.reason`。在那里改写就会一并改掉 `live_auto` 的成交理由（那边的 `signal_open` /
`signal_close` 与本轮无关）。而**今天真正写 prose 的唯一一条通路**是
`ai_auto/session._decision_to_signals`——它把 `decision.reason`（模型的中文 headline）塞进
`Signal.reason`。同一个纯核心 `signals.decision_to_signals` 还被**回测**直接调用，那边的 `reason`
参与 `exit_attribution.classify_backtest` 的**字面量**判据，改它会当场打坏回测归因。

**做法**：E-1 落在 `session._decision_to_signals`（新增 `_DECISION_REASON_CODE` 三值表），
`executor.py` 与纯核心 `decision_to_signals` **一行不动**。prose 由同一处已经在建的
`_ai_explain` 写进 `explain.headline`（本就未截断）。

### I-2 · E-8 的观测钩子同理，落在 `_dispatch` 而不是 executor

`INSUFFICIENT_BALANCE` 今天只是执行器返回的一条 `error` 事件，而 `session._dispatch` 是**全部
executor 事件的唯一收口**（该方法自己的 docstring 逐字写着这件事）。观测记点因此挂在那里，
executor 侧零改动，`live_auto` 链路一个字节不受影响。

### I-3 · E-7 需要一个**只有 executor 还记得**的方向，故用「单调计数 + 差量同步」

`note_expired(side)` 要的方向必须是**委托腿自己的** side（§4.5 E-7 的成文理由），而撤单常在数轮
之后才发生，那时会话侧的轮级 `_intent_side` 早被 `note_executed` 清零。executor 内部又拿不到开仓
漏斗（漏斗是 AI-trade 的读数，`live_auto` 没有）。

**做法**：executor 新增 `open_expired_by_side` 单调计数（两个撤单收敛点各记一次），
`session._sync_open_expired()` 在 `_dispatch` 里按差量补进漏斗。**不新增 WS 事件类型**——
新事件类型会流到前端 reducer，而那是一条没人消费的通路。

### I-4 · E-1 的兼容性子句点错了函数：`describeExecuted` 不吃 `reason`

**文档**：「`reason` 是既有前端 `describeExecuted` 的输入之一……展示分支必须先读
`explain.headline`」。

**事实**：`describeExecuted(action, executed, degradeReason)` **没有 `reason` 形参**。成交理由的
真实展示落点是 `components/Strategy/AutoTradeTradesTable.tsx:114` 的 `{trade.reason || "—"}`。

**做法**：在 `lib/aiTrade.ts` 新增 `describeTradeReason(trade)`——三条来源的**唯一读序**
（`explain.headline` → 机器码经 `describeDegradeReason` 中文化 → `reason` 原样），
并接到那一行上。`G-E3` 的双向用例照 §8.5 的语义写在 `lib/aiTradeLossLiveness.test.ts`。

### I-5 · 422 的 `remedies` 到不了前端：`ApiRequestError` 不带这个字段

**事实**：`lib/aiTradeApi.ts` 的错误分支只从 `body.error` 取 `code` / `message` / `members`，
`remedies` 被丢掉。不补的话，§6.1 那三条「可执行出路」在界面上只剩一句「浮盈回吐将没有任何
自动兑现通道」——用户知道坏了，却不知道该点哪里。

**做法**：`ApiRequestError` 新增可选 `remedies`（**镜像既有 `members` 的先例**），
`lib/api.ts` 与 `lib/aiTradeApi.ts` 两处解析点各加一行；`AiTradeToggle.tsx` 存一份
`startRemedies` 并透传给弹窗。
> §五.2 写着「`AiTradeToggle.tsx` **本轮不改**」——那句话的作用域是 A-2 的 `defaultProfitGuard`
> （评审 P1-7），它在本轮确实一行未动；这里改的是 A-1 的错误体透传，是另一件事。

### I-6 · C-1 的前端提示需要 `divisor`，而它此前不在任何一条 wire 上

§五.2 要求 `AiTradeHorizonFields.tsx` 在 `divisor > 0` 时渲染唤醒下限提示，但除数是后端 settings、
前端无从得知，而前端**不许硬编任何一个后端数值**（R-3 / G-10）。

**做法**：`GET /ai-trade/horizon-profiles` 加一个只读键 `wakeHorizonDivisor`
（`hold_horizon_profiles.profiles_wire` + `schemas.AiTradeHorizonProfilesOut`），
提示文案按下发值**现算**。默认 `0` ⇒ 那句提示整段不渲染。

### I-7 · A-4 的「只读阈值表」在**启动之前**，那时服务端还没被问过

`config_json.discipline.inert` 与 `group_status().discipline.inert` 是**启动之后**才有的权威值，
而用户需要看到删除线的时刻恰恰是**按下启动键之前**（那正是 45 条 run 的症结形成的时刻）。

**做法**：判据的权威真源仍是后端 `discipline.inert_thresholds()`；弹窗按**同一条判据**
（`profitGuardEnabled ? [] : ["ai_trade_profit_guard_hard_tp_return_pct"]`）本地现算一次，
并由 `test_g_a6b_frontend_inert_predicate_mirrors_the_backend_one` 做源码扫描钉死两侧同构。

### I-8 · 「接管既有 streak」只能靠**转移持有权**，且 `note_fill` 必须受总闸门控

§4.4 D-1 要求「接管 `_band_unavailable_streak`，不新建第二份计数」。实现上唯一能做到「真的只有
一份」的形态是：把那个 int 从 `AiTradeSession.__init__` 移走，改成读 `LivenessRuntime` 状态的
**只读 property**，并把推进点上提到每轮一次的 `advance_round()`（须排在 `_emit_base_rule_trace`
之前——那条第 3 轮告警读的正是推进后的值）。

**实施期发现的连带约束**：看门狗的「任何一次成交都清零」比合入前的 band 计数**多了一条清零判据**
（band 计数只在 `band is not None` 时清零，而band 为 None 时仍可能发生一次**平仓**成交）。
若无条件清零，`AI_TRADE_BASE_RULE_UNAVAILABLE` 的发火轮次就会与合入前不同——而 `G-D5` 要求它在
总闸关时**逐字节照发**。故 `LivenessRuntime.note_fill()` 在 `rule is None` 时**直接返回**，
并有专属用例（`test_g_d2_any_fill_resets_the_streak_but_only_while_armed`）钉死这条极性。

### I-9 · `llm_unavailable` 不可能在「把 payload 交给 LLM 之前」判定

§4.4 D-0 写「在决策 payload 建好之后、把它交给 LLM 之前，算一次探针」，但四条码里的
`llm_unavailable` 定义是「本轮 decide 重试耗尽」——它**只有在 `decide` 返回之后才可知**。

**做法**：探针落在 `decide()` 返回之后、**任何门禁之前**。这仍然满足 D-0 的实质要求
（「与模型动作无关」）：`llm_failed` 问的是「这次调用成没成」，不是「模型说了什么」；
另外三条码的输入（`payload.baseRules.maBand` / `ref_px` / `_stale_degraded`）在那一刻与 payload
建好时逐字相同。

### I-10 · C-2 的条件 3 实现成**白名单**，不是文档写的黑名单

§4.3 C-2 把条件 3 写成「本轮唤醒**不是** `signal` / `intel_alert` / `manual` 这类外部事件驱动的
唤醒」。黑名单意味着**将来任何一个新 wake kind 默认可跳**，而新 kind 多半正是「有事发生了」。
这与 `signals.decision_to_signals` 那条成文纪律（「兜底的方向必须是『不动』」）方向相反——
在这里，「不动」= 照常调 LLM。

**做法**：`hold_horizon.SKIPPABLE_WAKE_KINDS = ("heartbeat", "position_watch")` 白名单；
其余 kind（含 `candle_close`）一律不跳。总闸默认关，收益量级不受影响
（实测唤醒 3 分钟 / 执行周期 1H ⇒ 绝大多数轮次是定时唤醒）。

### I-11 · 两条既有冻结用例按**有意的加法变更**更新

- `test_ai_trade_turnover_session.py::test_round_one_gate_matrix_is_frozen`：`RELEASE_GATES` 多了
  `loss_release_covered` 一行。**既有四行逐字未动**（§十一.8 的承诺成立），故按加法更新该断言。
- `test_ai_trade_api.py` 的两条利润保护用例（`explicit_disable` / `master_switch_off`）此前提交的
  body **同时**装配了默认周期模式并关掉保护层——那正是本轮新增的致命组合，现在会吃 422。
  两条用例要验的是「显式关闭 ⇒ 保护层不装配」，与互锁无关，故显式补上 `tradingHorizon:{enabled:false}`。

### I-12 · 轴 F 的三处落点分工（文档只写了「`attribution.py` 产出」）

`exit_discretion_verdict` 的**判定本体**按文档放在 `attribution.py`（纯函数、零 import）；
两个来源子集（自主 / 确定性）**从 `EXIT_SOURCES` 派生**，故放在 `exit_attribution.py`
（`DISCRETIONARY_SOURCES` / `DETERMINISTIC_SOURCES` + 薄包装 `attach_exit_discretion`）；
**装配只发生一次**——在 `horizon_acceptance.bundle_from_input`，那是运行期 / 分组 / 历史 / 回测
四条通路**共用的唯一漏斗**（它本来就收 `settings`）。分头挂等于四份口径。

### I-13 · 「直达利润保护设置」的可达落点

§4.6 要求给一个直达入口。统计看板是一层 Modal（`AiTradeStatsButton` 自持 open state），而利润保护
开关住在**兄弟组件** `AiTradeToggle` 的启动弹窗里，两者之间没有任何现成通道。为一句结论去新增一
条全局 store 状态不划算。

**做法**：`ExitSourceCard` 收一个可选 `onOpenProfitGuard`，由统计面板传入自己的 `onClose`
——点它会关掉统计面板、回到那个开关所在的界面。**没有落点时只渲染结论、不渲染按钮**
（绝不给一个点了没反应的控件）。历史详情页不传该回调，故那里只有结论行。

### I-14 · A-2 需要把 `AiTradeProfitGuardRule.enabled` 改成可选

写侧剥离与 v31 迁移都会产出一个**不带 `enabled`** 的 `profitGuard` 对象，而该字段原为必填
`boolean`。两个读点本来就写着 `?? AI_TRADE_PROFIT_GUARD_DEFAULTS.enabled`，故改成可选是类型层面
唯一诚实的表达（提交体与回执镜像里它照旧恒有值）。

### I-15 · A0 复算与 §二 的三处小差异（如实记录，不修数）

在同一台机器、同一份生产库上跑 `scripts/audit_ai_trade_pnl.py --skip-log`：

| 读数 | §二 | 本次复算 | 说明 |
|---|---|---|---|
| `ai-trade` run / 腿 | 211 / 520 | **211 / 520** | 逐字命中 |
| 退出来源逐格净额 | 见 §2.2 | **逐格命中**（`agent −67.524` / `profitGuard +14.136` / `external +23.129` / `agentOverride −10.006` / `exchangeOco −4.776` / `offline −2.979`） | 逐字命中 |
| `releaseReasons` 全库聚合 | `{loss_release: 12}` | **`{loss_release: 12}`** | 逐字命中 |
| 保护层未装配 | 193 / 211 | **193** | 逐字命中 |
| (保护层, 周期) 交叉分布 | 143 / 13 / 50 / 5 | **143 / 13 / 50 / 5** | 逐字命中 |
| `start_ts == 0` | 211 / 211 | **211 / 211** | 逐字命中 |
| 净额子集往返 / 净额 | 71 / −47.36 | 70 / −47.72 | 配对尾巴差一笔（本脚本丢弃未配平的尾单，见 `pair_round_trips` 的成文局限） |
| `peak_pnl` 为 NULL | 213 | 215 | 采样之后又跑过会话；`--assert` 的容差为 ±2 |
| `sessionStop` | 2 笔 +0.661 | 1 笔 +0.302 | 同上（配对尾巴） |

**不修任何数**：脚本从今往后是这组读数的**可复现真源**，差异如实留在这里。

### I-16 · E-6 的 schema 要改**两处**

`_summarize_pairs` 同时供历史**列表行**（`AiTradeHistoryEntry`）与**详情 summary**
（`AiTradeHistorySummary`），两个模型都绑 `response_model`。只改一处 ⇒ 另一条通路上的
`costBasisCounts` 被 FastAPI 静默吞掉。前端 `lib/aiTradeHistoryTypes.ts` 同样两处。

### I-17 · `stats_ext.py` 不需要改（§五.1 那一行是多余的）

§五.1 给 `app/ai_auto/stats_ext.py` 写了「上述读数进统计 wire」。核对后**一行都不用改**：

- `openFunnel`（含新增的 `expired` / `insufficientBalance`）由 `open_funnel.funnel_wire()` 整对象
  产出，`build_funnel_stats` 只做 `out["openFunnel"] = funnel_wire_out` 的整体挂载；
- `exits`（含新增的 `exitDiscretion`）由 `horizon_acceptance.ai_stats_keys(bundle)` 整对象挂载。

两条通路都是**整对象透传**，故加法子键自动随行。**刻意不为了对上文档而去改一个不需要改的文件**
——那会新造一个手抄点。

### I-18 · 本轮实际触及的文件与 §五 的差集（逐条有据）

| 文件 | §五 是否列出 | 为什么动它 |
|---|---|---|
| `app/ai_auto/exit_attribution.py` | 否 | 轴 F 的两个来源子集必须**从 `EXIT_SOURCES` 派生**（I-12） |
| `app/ai_auto/horizon_acceptance.py` | 否 | 轴 F 的**唯一装配漏斗** `bundle_from_input`（四条通路共用，I-12） |
| `app/ai_auto/hold_horizon_profiles.py` | 否 | C-1 的 `wakeHorizonDivisor` 下发（I-6） |
| `app/live_auto/manager.py` / `app/paper/manager.py` | 是（「三个调用点」） | E-4 的 `start_ts` |
| `apps/web/lib/api.ts` / `lib/aiTradeApi.ts` | 否 | 422 `remedies` 的透传（I-5） |
| `apps/web/components/Agent/AiTradeToggle.tsx` | 明写「不改」 | 那句话的作用域是 A-2 的 `defaultProfitGuard`（本轮确实一行未动）；这里改的是 A-1 的错误体透传（I-5） |
| `apps/web/components/Agent/AiTradeStatsPanel.tsx` / `aiTradeStats/AiTradeStatsLive.tsx` | 否 | 轴 F 结论行的落点回调 + C-2 跳轮计数的呈现（I-13） |
| `apps/web/lib/aiTradeFunnel.ts` | 否 | E-7 的 `expired` 末格（`funnelStages` 是漏斗分格的唯一真源） |
| `apps/web/lib/useAiTradeGroupStream.ts` | 否 | §6.2 那两个新 WS 事件的**消费点**——只推不消费就是又一条「后端推了、前端丢在地上」的通路（`lifecycle` 事件的成文教训） |
| `apps/web/components/Strategy/AutoTradeTradesTable.tsx` | 否 | E-1 的真实展示落点（I-4） |
| `app/ai_auto/stats_ext.py` | 是 | **未改**，理由见 I-17 |

### I-19 · 停手请求必须在**轮末**投递（实施期自查发现的时序缺陷）

§4.4 D-3 的伪码在判到 `halt` 的那一刻就 `self._request_group_pause(...)`。照那样写**仍然会
cancel 掉在飞的那一轮**——只是比 v1 的 `await self.pause(...)` 晚了几个 await：
`manager.pause_group` 的第一步就是逐成员 `session.pause()`，而 `Session.pause` 的第 ③ 步是
**cancel 决策任务**；后台任务会在本轮的下一个 await 点把它 cancel 掉，这一轮的 trace 与决策事件
就此丢失。这是评审 P0-4 ③ 的一个更隐蔽的变体，`G-D7` 守的正是它。

**做法**：判到 halt 时只置 `_liveness_halt_pending` 并**先把横幅推给用户**；真正的投递放在
`_run_decision_round` 的 `finally`（`_flush_liveness_halt`）。这里有一条结构性保证而不是时序
碰巧：`callback()` 是**同步**的、只 `create_task`，单线程事件循环下那个任务最早也要等本协程返回
之后才会跑。用例 `test_g_d7_halt_is_delivered_only_after_the_round_has_finished` 断言回调触发时
`rounds_completed` **已经**自增。

### I-20 · `round_skipped` / `liveness` 两个 WS 事件必须有消费点

§6.2 只写了「后端新增两种事件」。前端 `routeMemberEvent` 的 `switch` 没有 `default`，未知 type
被静默丢弃——那正是上一轮 `lifecycle` 事件「后端推了整整一轮、前端没有 case」的形状。

**做法**：两条 `case` 都补上。`liveness` 直接 patch 成员的活性断面（与 `status.liveness` 同一份
wire，只是让横幅在跃迁那一刻就更新）；`round_skipped` 做一次**乐观更新**，且**计数由后端随事件
下发**（`roundsSkipped`）——前端绝不自己自增，第二个自增点必然与后端的权威值分叉。
同时 `session._status_payload` 与 `info()` 都带上 `liveness`（**同一个装配点** `_liveness_key`）：
只挂一处会让另一条通路上的横幅恒不渲染。

### I-21 · 停手任务必须在关停链里被收敛（同样是实施期自查发现的）

`_request_liveness_pause` 起的那条后台任务做的事是 `pause_group`，而 `pause_group` **会写库**
（`_persist_group_state`）。关停步骤表里 `ai_trade` 之后隔几步就是 `db_engine` 的
`dispose_engine`——一个活着穿过引擎释放的写任务会当场抛（经验层 ★ E-4 / 评审 P0-4 的同款教训）。

**做法**：`AiTradeManager.shutdown()` 第一行 `await self._drain_liveness_tasks()`。
**关停步骤表一步都不加**（那张表有专属的冻结用例）——收敛落在既有的 `ai_trade` 那一步内部。
极性与 `experience` / `intel` 两条 drain **刻意相反**：那两条 drain 的是可丢弃的攒素材工作，
先 cancel 最省事；这一条正在做的是一次暂停，半途而废会留下「会话已挂起、状态行没落盘」的中间态
——那恰恰是本轮轴 D 要消灭的形态。故**先等它跑完**（死线 5s，远小于 `shutdown_step_timeout_s`
的 20s），真超时了才 cancel。

### I-22 · 轴 F 的结论文案里出现过一个裸英文枚举（自查修掉）

第一版 `note` 拼的是 `f"{best['source']} 平仓 …"`，于是用户读到的是
「…；profitGuard 平仓 7 笔合计 +14.14 USDT。」——**一句中文里嵌一个英文枚举**，正是本仓库反复
禁止的形态（`describeDegradeReason` 的裸码、`HORIZON_RELEASE_LABEL` 漏行都是同一类）。

**做法**：标签取 `acceptance_text.EXIT_SOURCE_LABELS`（前后端六处镜像的**同一张表**），
由 `attach_exit_discretion` 喂给纯函数；`bestDeterministic` 同时带上 `label`，前端若要单独渲染
来源也不必自己映射一次。用例
`test_exit_discretion_note_uses_the_chinese_label_not_a_bare_enum` 钉死「note 里不得出现
`profitGuard`」。

### I-23 · `hold_horizon.py` 已到 791 行（下一轮再加就破 800）

CLAUDE.md 的单文件红线是 800 行。本轮往 `hold_horizon.py` 加了 `RELEASE_LOSS_COVERED`
（轴 B）与 `round_is_structurally_idle`（C-2 的判据），合计 791 行。**下一轮再往里加任何东西之前
必须先拆**——按既有范式，纯核心与「渲染 / 运行时」已经分在 `hold_horizon_text.py` /
`horizon_runtime.py`，下一刀最自然的位置是把「§2.3.3 豁免与三道闸」单独切出去。
在这里写下来，是为了不让下一个人在压力下顺手破线。

### I-24 · `A1` 的可执行形态与文档原文不同（终审补齐）

**文档**：§8.1 `A1` 要求一条 `test_ai_trade_all_new_switches_off_is_byte_identical`，
断言「payload / prompt / decision / snapshot 四者逐字节等于 `5337da1`」。

**事实**：实施完成时这条用例**不存在**——六条按轴分开的「总闸关」用例
（`G-A2` / `G-B4` / `G-D5` / `test_c2_master_switch_off_never_skips` /
`test_exit_discretion_min_samples_zero_means_the_key_is_absent` / `G-E4` 的 `False` 分支）
各自更强，但没有任何一条覆盖**跨轴**的那个命题：「11 把总闸同时置回退档时，有没有哪个键
悄悄进了 payload」。而字面意义的「逐字节等于 `5337da1`」需要一份 golden 快照，golden 会在
下一轮任何一次合法改动上变红、且红了也判断不出该不该红——那是一条会被下一个人直接删掉的门禁。

**做法**：写成 `test_a1_every_new_switch_off_adds_nothing_to_payload_status_or_events`
（11 把闸全置回退档 + 跑三轮「通道恒算不出来」的真实事故形态）：断言 WS 事件流里不出现
`liveness` / `round_skipped`、决策 payload 里不出现 `liveness` / `loss_release_covered` /
`exitDiscretion` / `roundsSkipped`、两条 status 通路上 `liveness` **整键缺席**、且 E-3 回到
「峰值恒 NULL」。**`A1b` 的六处有意加法刻意不进这条断言**——把它们也钉进来等于把「有意的加法」
冤枉成回归，下一次有人为了让它变绿会去删掉一个本该存在的键。

### I-25 · 终审修掉的两处（复核阶段发现）

1. **`liveness_text._HALT_SCOPE_TEXT` 里的 `**整个分组**` 会以字面星号显示给用户。**
   该字符串经 `status.members[].liveness.message` 一路到 `AiTradeLivenessBanner` 的
   `{liveness.message}`——那是一个 JSX **文本节点**，没有任何 markdown 渲染器。

   本仓库已为这一形态立过一道门禁：`components/Intel/intelNoDangerousHtml.source.test.ts` 的
   「面向用户的文案里没有字面 `**`」（其注释自陈是第 3 次复发才立的）。但那道门禁扫的是**前端
   源码**，而本次的字面量住在**后端**的 `liveness_text.py` 里——它结构性地看不见，这正是同一个
   坑换一条通路又出现了一次的原因。

   已去掉标记，并在**两侧**各补一道会红的断言：后端
   `test_liveness_message_says_the_whole_group_will_be_paused` 断言下发的 `message` / `advice`
   都不含 `**`（门禁与文案同处一个文件，改文案的人必然看到）；前端横幅用例断言渲染出的
   `textContent` 不含 `**`。**强调只能靠措辞，不能靠标记。**

2. **`LivenessRuntime.halt_requested` 是一个有写点没读点的僵尸属性。**
   它在 `__init__` 里被置 `False`、写进 Attributes 文档，此后全仓**再无一处读它或写它**——
   真正的幂等标志是会话侧的 `_liveness_halt_requested`（与轮末投递槽 `_liveness_halt_pending`
   成对，`resume()` 里一并清零）。留着它有两重害处：违反「删除僵尸代码」的成文纪律，且
   `reset()` 不清它——下一个人一旦改成读它，一次自动停手之后看门狗就再也不会发第二次，而那
   恰恰是【继续】按钮该起作用的时刻。已删除该属性，并把「幂等标志刻意不在这一层」写进类文档。
