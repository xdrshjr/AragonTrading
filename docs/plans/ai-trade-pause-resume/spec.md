# AI 自动交易：暂停 / 继续（跨进程可续跑的会话契约）

> **版本**：**v2**（评审节点产出；v1 为设计节点产出，全部 P0 / P1 已在正文闭合）
> **迭代**：loop `tge-1b47f030` 第 1 轮 · Subtask #0「方案设计」→ Subtask #1「方案 Review」
> **评审基线**：`HEAD = 6d9a72f`（与设计节点同一工作树）。评审节点逐条重打开了 v1 引用的
> 每一处 `file:line`，实测结论记在 §评审记录；v1 的判定被推翻或修正的地方，正文已改写为 v2 口径。
> **特性目录**：`docs/plans/ai-trade-pause-resume/`
> **需求原文**：【AI 自动交易】开启后不支持暂停；用户电脑关机 / 关软件后，再启动就得**重新启动交易**。
> 需要「暂停 / 继续」：可对正在进行的交易暂停；暂停后用户重新联网、开启软件，点对应的**继续**按钮
> 即可继续 AI 自动交易。
> **勘察基线**：`HEAD = 6d9a72f`（`feat: ship the AI auto-trading master gate ON by default…`）。
> 本文引用的每一处 `file:line` 均在该提交的工作树上逐条打开核对过。
> **v2 [P2-1] 的更正**：v1 的这句自述对 `main.py` 不成立（四处引用偏移 1~4 行，实测
> `shutdown()` 在 `:74` 而非 `:78`），已在正文就地改正；其余文件的引用经评审节点重新逐条
> 打开，**逐字精确**。所有 `executor.py:NNN` 的行号正确但路径前缀缺 `live_auto/`，亦已补齐。
> **一句话结论**：本轮真正要交付的不是一个「暂停按钮」，而是**把一份至今只存在于进程内存里的
> 真实资金会话契约落到磁盘上，并让它在任何一种进程死亡方式之后都能被安全地重新接管**。

---

## 评审记录（v1 → v2）

> 评审维度：**可行性 / 完备性 / 一致性 / 规模适配**。方法：把 v1 引用的每一处 `file:line`
> 在 `HEAD=6d9a72f` 上重新打开核对，再按「这条判据在真实资金上会怎么坏」逐条压力测试。
> **结论先说**：v1 的九条勘察实证与绝大多数 `file:line` 经复核**属实且精确**（`session.py`、
> 前端三文件、桌面两文件的引用逐字命中），主体架构（新表 + 双谓词 + 冷续跑七步 + 判定矩阵）
> **予以保留**。下列 P0 / P1 均已在正文闭合，P2 保留为实现节点的注意事项。
>
> 每条格式：`编号（维度）现象 → 根因 → 已在正文的闭合位置`。

### P0（不闭合即不可合入）

| # | 维度 | 现象与根因 | 闭合位置 |
|---|---|---|---|
| **P0-1** | 完备性 / 真实资金 | **会把用户离线期间自己开的仓位当成"我们的仓位"接管。** §3.6 的判定矩阵只比 `(side, size)`，而 S3 唯一的取数口 `fetch_live_positions` → `_normalize_position`（`okx/accounts.py:154-167`）**把 `posId` / `cTime` / `uTime` 全丢了**。于是「AI 的多头 0.6 张在离线期间被 OCO 平掉、用户随后手工又开了 0.6 张多头」这条完全现实的路径会被判成 `matched` 并**静默接管**——直接违反 R-1，也把风险表第 2 行的缓解措施架空（矩阵拿不到能证伪的字段）。 | §3.5 S3'、§3.6 新增 `foreign_position` 格、§6.2 `position.posId`/`openCTime`、§四 文件计划新增 `okx/accounts.py`、G-26 |
| **P0-2** | 一致性 / 需求本身 | **默认配置会让本轮需求原地失效，并新增一条"每次退出都平真仓"的行为。** §3.8 给桌面端加了一条**可靠**的 `quit-pause` HTTP 钩子（HTTP 能跑通，与 D-4 里那条跑不通的 lifespan 完全不同），而 §6.4 把 `ai_trade_shutdown_action` 默认成 `"flatten"`。合起来：用户关软件 ⇒ 后端**必定**收到并平仓归档 ⇒ 重开后**没有任何可续跑的卡**——正是需求原文要消灭的那件事。v1 §八风险 1 的缓解 ② 自称"与今天一致"，但 D-4 刚证明今天这条路**几乎从不执行**，所以它不是"保持一致"，是**新增**了一条真实资金平仓路径。 | §3.3 `shutdown` 分派、§3.8、§6.4 默认改 `"hold"`、§八风险 1、G-27 |
| **P0-3** | 完备性 / 可靠性 | **可续跑的真值只活在内存里，而装载它的那步被设计成"失败只 warning"。** §3.7 的 `_hydrate_paused_ai_trade()` 整段 try/except 吞异常；§3.3 的容量闸只读 `self._paused_ref`；§四的历史排除沿用 `api/ai_auto.py:550` 那条**内存派生**的 `exclude_group_id`。水合失败（DB 忙 / JSON 坏 / 一次偶发异常）⇒ 三件事同时发生：① `status()` 回 `None` ⇒ **D-9 的互斥原样复发**，用户可以对同一账户再开一套自动化；② 容量闸放行，可以在带仓的暂停契约之上再起一个新分组；③ 历史列表把这个**仍持真仓**的分组当成已完成会话展示。而 §八风险 11 自己已经写明「**以状态表为准**」——设计却在三个判定点都用了内存。 | §3.3 容量闸改读状态表、§3.7 水合失败置 `_hydrate_failed` 并 fail-closed、§四 历史排除改为按 DB `status`、G-28 |

### P1（合入前必须闭合）

| # | 维度 | 现象与根因 | 闭合位置 |
|---|---|---|---|
| **P1-1** | 完备性 | §3.1.2 那张表（v1 自称"唯一的安全关键表"）**漏了两处**：`feed_candle` 确认根路径上的 `_window.maybe_refresh_htf`（`session.py:691`）与 `_window.maybe_refresh_tactical`（`session.py:693`）。两者都是 REST 取数，且按 v1 自己给 row 12 的理由（"战术窗口只服务决策轮"）就该在暂停期跳过。另外表里没有写明 `evaluate_signal_board`（影子步进，`:698`）**必须继续跑**——不写清楚，实现会把 row 17 的门禁上移到 `:690` 从而冻结影子立场，续跑后整块信号板是陈旧的。 | §3.1.2 新增 row 12b / row 16b + row 17 插点约束 |
| **P1-2** | 可行性 | **G-3 的冻结判据算术上不成立。** 实测 `grep -c "self\.state" services/quant/app/ai_auto/session.py` = **16**，而 v1 的表有 19 行（v2 补 row 12b 后 20 行）——因为其中 9 行（row 2/3/7/8/9/10/11/12/12b）根本不含 `self.state`（它们"随 loop"或"随 feed_candle"）。改造后总数还会因 `pause()` / `resume()` / row 17 新增而变。写死 `== 19` 的门禁**第一天就是红的**，等于没有门禁。 | §3.1.2 表尾、G-3 改为「点位清单冻结 + 禁止第三种写法」 |
| **P1-3** | 一致性 | §3.7 把 `reconcile_orphan_ai_trade_runs` 的返回从 `int` 改成 `dict`，会**打破既有用例** `tests/test_ai_auto_history.py:287-288`（`assert first == 1 and second == 0`），直接违反本 spec §七 开头「既有用例一条都不许改口径」。同址 `db/session.py:184-186` 的 `if reconciled:` + `%d` 日志也会一起坏。 | §3.7 签名保持 `-> int`，组信息走独立只读函数 |
| **P1-4** | 一致性 | §九.4 断言「**不改 `live_auto`**：它不共用本文任何代码路径」——**为假**。`ai_auto/session.py:24` 就是 `from ..live_auto.executor import LiveExecutor`；`api/ai_auto.py:94-95` 的注释更是明文写着「`LiveExecutor` 被 `ai_auto` 与 `live_auto` 共用同一个 settings 对象」。全文 6 处 `executor.py:NNN` 也都缺 `live_auto/` 前缀（行号本身正确）。这条错误陈述的危险在于：它会让实现节点以为「改 executor.py 只影响 AI 侧」。 | §3.2、§九.4 改写；全文路径补 `live_auto/` 前缀 |
| **P1-5** | 完备性 | §3.4.2 的 W1 写点在 `manager.py:276-277` 之间，即**在 `_launch_group_or_rollback` 之前**。启动失败走回滚时（`manager.py:602-617` 把全部成员 `session.stop("start_failed")`、`self._group = None`）磁盘上会**留下一条 `status='running'` 的幽灵契约**——而 §八风险 11 的自愈只覆盖 `paused` 行。注意 `session.start()` 只做暖机 + WS + monitor、**不下任何单**，所以把 W1 挪到启动成功之后零信息损失。 | §3.4.2 W1 改写 + 回滚清理 |
| **P1-6** | 完备性 | **缺一个写点**：会话**自终止**（暂停期的会话止盈止损触发 `stop()`、`stale_feed` 强平、启动回滚）之后，`strategy_runs` 已落 `stopped`，但 `ai_trade_group_states` 仍是 `paused`。于是 G-5 断言的「状态表置 `ended`」**没有任何实现落点**，且进程内会留一张指向已平仓会话的续跑卡。 | §3.4.2 新增 W5 |
| **P1-7** | 完备性 / 真实资金显示 | §3.6 `closed_offline` 写 `pnl = realizedPnl` **口径错**。`_normalize_position_history`（`okx/trading.py:646-669`）的 docstring 写明官方关系式 `realizedPnl = pnl + fee + fundingFee + liqPenalty`，即 `realizedPnl` 是**已扣费且含资金费**的；而 `trades.pnl` 的口径由该 run 的 `costBasis`（`ai_trade_net_pnl_enabled`）决定，资金费另有 `funding_ledger` 台账。直接写进去会：`net_mode=False` 时被 `local_net_view`（`reconcile.py:39-65`）**再减一次腿费**，资金费与台账**双记**。另外 `positions-history` 按 `posId` 汇总整个持仓生命周期，若暂停**之前**已有部分平仓落过本地 trade，这一笔会**重复计入**。 | §3.6 `closed_offline` 段改写 + G-13 扩写 |
| **P1-8** | 完备性 | §3.6 把「暂停时无仓、离线期间被外部开仓」（`expected=None & actual≠None`）塞进 `size_increased`（见 G-12）。该格的判据写的是"方向同、size 大于 expected"——`expected=None` 时既没有方向可比也没有 size 可比，判据与文案**都不成立**，UI 会显示一句自相矛盾的话。这正是 §十.7 要评审重点质疑的那一格。 | §3.6 新增 `opened_offline` 独立格 |
| **P1-9** | 完备性 / 真实资金 | §5.3 的 `onDrift: "flatten"` 出路在 `size_increased` / `side_flipped` 上会**平掉用户自己加的那部分仓位**（我们从未开过的名义额）。`LiveExecutor.flatten`（`live_auto/executor.py:663-704`）自己承诺"绝不触碰非自管持仓"，而续跑时本地根本没有自管断面可依。"未经授权替用户平仓"比"拒绝续跑"坏得多。 | §3.6 处置列、§5.3 `onDrift` 值域收敛 |
| **P1-10** | 完备性 | §3.3 没有覆盖**既有** `stop_member`（`manager.py:638-650`）的清组判据 `if not any(m.session.state == "running" …): self._group = None`。分组进入 `paused` 后没有任何成员是 `running`，于是对一个 paused 组调用一次 `stop_member`（既有公开端点 `api/ai_auto.py:369`）就会**把整个占位清掉**：其余 paused 成员的 monitor 仍在后台跑（`is_active`）却再也路由不到，互斥与续跑卡同时消失。 | §3.3 改造清单新增 `stop_member` |
| **P1-11** | 一致性 | §3.0 的 `pause_reason` 稳定枚举里 `disabled_by_config` 在 §3.7 的 `resume_enabled = pause_enabled and ai_trade_enabled` 下**永不产生**（AI 总闸一关就直接走"逐字回退老行为 running→stopped"）。两个后果：① 枚举里有一格死值 + 一段永不显示的 UI 文案（本仓库明令禁止"算了没落点"）；② 更要紧的是**极性错了**——"运维关掉 AI 总闸"不该导致"把一个仍持真实仓位的会话静默归档"，那正是 §1.2 立论要堵的洞。 | §3.7 极性改写、§3.0 枚举表 |
| **P1-12** | 完备性 / HCI | §5.4 `end` 的 `flatten` 默认 `false`。**带仓时**这会造出一个"本地已归档、交易所上仍在"的孤儿仓——与 §1.2 第三段的立论直接自相矛盾（那一段正是拿"今天已经存在孤儿持仓，只是没人告诉用户"来论证本轮的必要性）。 | §5.4 带仓时 `flatten` 改为必填、§3.9.3 结束确认框 |

### P2（记录级；实现节点按需处置，不阻断合入）

| # | 现象 | 建议 |
|---|---|---|
| P2-1 | `main.py` 的四处 `file:line` **系统性偏移 1~4 行**：`get_ai_trade_manager().shutdown()` 实际在 **`:74`**（v1 写 `:78`）、`_backfill_companion_strategies` 在 `:85`（写 `:88`）、`_resume_discovery_runs` 在 `:142`（写 `:143`）。与卷首"每一处 `file:line` 均逐条打开核对过"的自述不符。**其余文件的引用经复核逐字精确**。 | 正文已就地更正 |
| P2-2 | §3.9.2 写 `Badge tone="warn"`，但 `components/ui/index.tsx:126` 的联合类型只有 `neutral \| long \| short \| accent`。 | 正文改用 `accent`；若坚持新增 `warn` 必须进 §四 文件计划（它会影响全仓 Badge 消费者） |
| P2-3 | W2 说挂在「`_dispatch` 循环末尾」——实测 `session.py:341` 的 `await self._push(event)` 对**每一个**事件都执行（含 status/candle），不只成交。 | 正文改为挂在 `:340` `_persist_trade` 之后 |
| P2-4 | `check_now` 复用 `schedule_check` 的私有路径时必须**先 `force_next()`**：`exchange_backstop.py:357-360` 的 TTL（`ai_trade_backstop_poll_ttl_s`，默认 120s）会让它在窗口内直接空转返回旧读数。 | 已写进 §3.1.3 的 docstring 契约 |
| P2-5 | `snapshot_run_metrics` **不需要**新仓储函数：`repositories.finalize_run`（`:318-334`）本就是 `end_ts: int \| None = None` 且 `if end_ts is not None` 才写。 | 已在 §3.1.3 ⑥ 注明 |
| P2-6 | 三个"算了没落点"的配置/枚举：`pause_reason.stale_feed_pause`（预留死值）、`ai_trade_resume_auto`（与 §九.7「不做自动续跑」冲突，无读点）、`ai_trade_resume_backstop_required`（§3.6 未给读点）。 | 前两个已删；第三个已在 §3.6 给出读点 |
| P2-7 | 暂停期 `_check_staleness` 仍活（row 4），故 `live_auto_flatten_on_stale`（`config.py:118`，**默认 `False`**）为真时，行情断流会把一个"保留持仓的暂停"直接平掉并终结会话。默认关，但运维可开。 | 已在 §3.1.2 row 4 与 §八风险 13 写明 |
| P2-8 | §5.1 回执里的 `position` 必须在 ④ `cancel_resting` **之后**读：三态撤单（`live_auto/executor.py:393+`）可能返回 `filled` 并当场改写 `projected`，先读会给出"已空仓"的假回执。 | 已在 §3.1.3 注明 |
| P2-9 | 冷续跑卡的横幅**不得**声称"交易所侧止损 58000 仍生效"——冷启时尚未向交易所核实过任何东西。 | 已在 §3.9.2 分冷/热两套文案 |
| P2-10 | 续跑卡只在 `accountMode === "live"` 渲染（既有重发现 effect `useAiTradeGroupStream.ts:96` 的前置就是它）。 | 已在 §3.9.4 注明 |
| P2-11 | `start_wire_json` 只落请求体：`AiTradeGroupStartIn`（`schemas.py:840`）只有 `accountId`，**绝不**落 apiKey/secret/passphrase。 | 已在 §6.1 写死 |
| P2-12 | 存量债 §十.6 漏记两项：`session.py` **1867 行**、`manager.py` **962 行**（均远超 CLAUDE.md 的 800 行红线，且本轮还要各加 ≤95 / ≤150 行）。 | 已补记 |
| P2-13 | 多成员组的续跑必须写明是**整组原子**（任一成员 `blocked` ⇒ 整组拒绝），否则实现会去猜"逐成员续跑"。 | 已在 §3.5 S4 写明 |
| P2-14 | `matched` 但离线期间"先加仓再减回同量"时，`entry_bar` 反推用的 `openWallTs` 是旧的 ⇒ `bars_in_position` 偏大 ⇒ 时间强平提前。 | 用交易所 `cTime` 替代本地 `wallTs`（与 P0-1 同一处改动顺带解决） |

### 复核确认为"设计正确、无需改动"的几处（防止下游二次质疑）

1. **D-4（`taskkill /PID /T` 无 `/F`）的结论成立**：`process-manager.js:365-376` 逐字命中，后端子进程确以 `windowsHide` 且**非** `detached` 拉起（`:320-328`）；`stop()` 的 8s 宽限后 `_forceKill()` 用的是 `/F /T`（`:418-438`）。无论 `taskkill` 无 `/F` 是否偶发成功，Windows 都不会把它转成 Python 可捕获的信号 ⇒ lifespan 大概率不跑。v1 的态度（"即便实测能优雅退出，本设计也不改"）**正确，予以保留**。
2. **表 row 13 效果上是安全的**：`_maybe_signal_flip_wake`（`:854-877`）本身**没有** `self.state` 判据，但它第一件事就是 `if not self._preview_ready(): return`，而 `_preview_ready`（`:879-883`）里有 `self.state == "running"`。故暂停期它自然早退。v1 表格把判据记在 `:535`（那只是调用点）**位置不准但结论正确**，正文已改记为 `:881`。
3. **`status()` 语义变更确实只有一个消费者**：全仓 `grep -rn "\.status()" services/quant/app` 只有 `live_auto/manager.py:469` 一处真实调用，且只判 `is not None`。G-11 有效。
4. **D-2 的字段清单逐字属实**：`_create_group_run`（`manager.py:904-952`）的 config 字典确实没有 `accountId` / `stopLossPct` / `takeProfitPct` / `priming` / `primingBars` / `capitalUsd`，而 `manager.py:868-871` 确实在缺值时回落 `ai_trade_default_sl_pct` / `ai_trade_default_tp_pct`。"照 `config_json` 续跑会静默换掉用户的止损"成立。
5. **D-7 的前端三处逐字精确**：`useAiTradeGroupStream.ts:64` 的 `state: "running",`、`aiTradeTypes.ts:1284` 的 `"running" | "stopped"`、`AiTradePanel.tsx:62` 的 `stopped` 推导，行号全部命中。
6. **规模适配总评**：4 个新后端文件 + 2 个新前端组件 + 25 条门禁，对一个动真实资金的跨进程生命周期特性**不算过度设计**。唯一偏重的是 §3.6 的八格矩阵——但复核后不仅不该砍，还得**加两格**（P0-1 / P1-8）。真正该砍的是三个没有读点的配置（P2-6），已删两个。

---

## 零、本轮结论摘要（先读这一段）

1. **今天没有「暂停」这个动作，只有「停止」，而停止一定平仓。** `stop_group` → `session.stop()` →
   `executor.flatten()` 是唯一的退出路径（`manager.py:620-631`、`session.py:427-452`、
   `live_auto/executor.py:663-704`）。前端开关关掉就是 `stopGroup`（`AiTradeToggle.tsx:184-192`）。
   用户想要的「先停手、别平仓、回来再继续」在当前代码里**无法表达**。
2. **今天没有任何磁盘状态描述「这次 AI 交易是怎么配置的」。** `AiTradeManager` 只有
   `self._group`（`manager.py:244-247`）且是进程级单例（`manager.py:955-962`）。
   `strategy_runs.config_json` 记了大部分参数但**独缺 `accountId` / `stopLossPct` / `takeProfitPct` /
   `priming` / `primingBars`**（`manager.py:915-946` 逐字核对）——其中两个是交易所侧 OCO 止损止盈
   百分比，**恰恰是最不能丢的两个数**。所以「照 config_json 续跑」是一条会静默丢风控参数的路。
3. **今天的启动对账会主动把上次残留的会话归档。** `_reconcile_orphan_ai_trade_runs`
   （`db/session.py:173-188` → `ai_auto_repositories.py:651-670`）在每次进程启动时把
   `mode='ai-trade' AND status='running'` 一律置 `stopped`。**这行代码就是用户抱怨的那句
   「再次启动后就得重新启动交易」的实现**。
4. **桌面端退出几乎肯定不会走 FastAPI 的 lifespan 关停钩子。** `_gracefulStop()` 用的是
   `taskkill /PID /T`（**无 `/F`**，`process-manager.js:364-376`），目标是一个 `windowsHide`
   的无窗口 Python 子进程；Windows 对这类进程通常直接拒绝（"can only be terminated forcefully"），
   随后 8s 宽限到期由 `_forceKill()` 强杀（`process-manager.js:417-438`，用的是 `/F /T`）。
   ⇒ `main.py:74` 那句 `await get_ai_trade_manager().shutdown()`（今天唯一的退出平仓路径）
   **是个 best-effort，多数情况下根本没执行**。
   ⇒ 任何依赖「优雅关停时把状态写下来」的设计**从构造上就是坏的**。本轮的持久化必须是
   **运行期就写好**的，让「断电」和「点暂停」在磁盘上无法区分。
   > **v2 [P0-2] 的推论**：既然今天这条平仓路径几乎从不执行，那么本轮 §3.8 新加的
   > `quit-pause` **HTTP** 钩子（它是能跑通的）就**不是"保持现状"，而是新增了一条
   > "每次关软件都平真仓"的路径**。所以 `ai_trade_shutdown_action` 的默认值必须是
   > `"hold"`——默认平仓会把本轮需求原地废掉（关了软件回来就没有可续跑的东西了）。
5. 因此本轮的四件事：
   **① 会话层真正的 `pause/resume`（极性：只关"增风险"侧）**；
   **② 一张新表 `ai_trade_group_states` 承载可续跑契约（`create_all` 自动建，零 `ALTER`）**；
   **③ 启动对账从「归档」改为「置可续跑的 paused」**；
   **④ 一套"绝不静默接管"的离线差异复核界面**。
6. **真实资金的三条红线**（贯穿全文，任何一条被违反即本轮不可合入）：
   - **R-1 暂停绝不平仓，续跑绝不静默接管**。方向不符 / 张数变大 / **持仓身份对不上**（v2 [P0-1]：
     `posId` 变了或 `cTime` 晚于暂停时刻 ⇒ 那是**用户自己开的仓**，不是我们的）/ 读不到持仓
     ⇒ **拒绝续跑**并给出路。
   - **R-2 暂停只关"开"，不关"平"**。保护性平仓、会话止盈止损、交易所侧兜底核实与自愈补挂
     在暂停期**照旧运行**；被暂停掉的只有「起新决策轮 / 开仓 / 加仓 / 挂新限价单」。
   - **R-3 续跑不新建 run**。逐成员复用同一 `run_id`，成交 / 指标 / 历史聚合天然连续；
     一次交易在历史列表里永远只有一条。

---

## 一、概述

### 1.1 在建什么

给「AI 自动交易」补上一组**跨进程**的生命周期动作：

| 动作 | 语义 | 对真实资金的影响 |
|---|---|---|
| **暂停**（pause） | 停止一切新的决策轮与开仓；**保留持仓**（也可选择先平仓再暂停）；保留全部启动参数 | 不下任何开仓单；暂停瞬间**撤掉**未成交限价挂单 |
| **继续**（resume） | 用同一份契约、同一批 `run_id` 重新武装会话；重新接管持仓 | 接管前必须通过离线差异复核 |
| **结束**（end） | 归档一个已暂停的分组（可选顺带平仓） | 与今天的「停止」等价，只是入口从 paused 态进入 |
| **停止**（stop，既有） | 平掉自管持仓 + 落 Run 终态 | 行为**逐字不变** |

「继续」必须在**两种**处境下都成立：
- **热续跑**：软件没关，用户只是点了暂停 —— 会话对象还在内存里，重新武装即可。
- **冷续跑**：软件关过（正常退出 / 强杀 / 崩溃 / 断电 / 换网络） —— 进程里什么都没有，
  必须从磁盘把契约读回来、重建会话、重新接管交易所侧那个真实存在的仓位。

用户口中的「关闭后再次启动」走的是**冷续跑**，这也是本轮全部工程难度所在。

### 1.2 为什么要这样建（而不是更简单）

一个「更简单」的做法是：暂停就平仓，继续就照原参数重新启动一遍。它被否决，因为：

- **平仓不是免费的。** 每次往返都要付双边手续费与滑点；本仓库已经为此建了整套成本记账与
  最小 edge 门禁（`cost_runtime.py` / `edge_gate.py`）。让用户为「我今晚要关电脑」而被迫
  实现一次盈亏，正是那套门禁在阻止的事。
- **「重新启动一遍」会丢掉本仓库最贵的那部分状态。** 已实现 edge 校准、往返账本、
  持仓周期验收卡、滚动摘要、开仓漏斗——全部按 `run_id` 聚合。新起一个 run 会把一次交易
  在历史里劈成两条，`totalReturn` 的分母错、验收卡样本被拦腰截断。
- **最关键：不做持久化并不能让风险消失，只能让风险变得不可见。** 第 4 条勘察结论已经说明，
  今天强杀进程时**持仓仍在交易所上**，而本地把 run 归档成 `stopped` —— 也就是说
  **今天已经存在"孤儿持仓"，只是没人告诉用户**。本轮的持久化顺带把这个洞堵上：
  哪怕用户不点「继续」，冷启动后也会被明确告知「你有一个带仓的暂停会话」。

### 1.3 交付形态（用户视角）

- 运行中：AI 面板头部出现 **⏸ 暂停** 按钮（与既有开关分工：开关=启停，暂停=挂起）。
- 点暂停 → 弹一个**决定持仓归属**的确认框：`保留持仓暂停`（默认）/ `平仓后暂停`。
  带仓暂停时，框内**当场向交易所核实一次**兜底止损是否真的挂着，并给出「补挂」直连按钮。
- 已暂停（软件仍开着）：头部显示 `已暂停` 徽标 + **▶ 继续** + **结束**。
- 关掉软件、重开、联网后：头部出现一张 **「已暂停的 AI 自动交易」续跑卡**，写明
  标的 / 周期 / 预算 / 离线时长 / 是否带仓；点 **继续** 弹**续跑复核框**（离线期间发生了什么、
  契约有没有漂移），勾选确认后才真正续跑。
- 任何时刻都能选择「结束并归档」，不必为了清掉这张卡而被迫续跑。

---

## 二、勘察实证：九条（逐条附 `file:line`）

### D-1（阻断级）唯一的退出动作一定平仓，"暂停"在代码里不可表达

`AiTradeManager.stop_group`（`manager.py:620-631`）逐成员调 `_stop_session` →
`AiTradeSession.stop`（`session.py:427-452`），其中 `session.py:443`
`self.flatten_result = await self.executor.flatten(ref_px, ts)` 是无条件的。
前端 `AiTradeToggle.onToggle`（`AiTradeToggle.tsx:184-192`）把开关的 `false` 直接映射到
`stopGroup`。**结论**：产品语义上「暂停」= 后端缺一个动作 + 前端缺一个入口，两侧都要新增。

### D-2（阻断级）启动契约只活在内存里；落库的那份**独缺 OCO 止损止盈百分比**

`AiTradeManager.__init__`（`manager.py:244-247`）只有 `self._group` 与一把 `_start_lock`；
`get_ai_trade_manager()`（`manager.py:955-962`）是进程级单例。
唯一沾到磁盘的是 `_create_group_run`（`manager.py:905-952`）写进 `strategy_runs.config_json`
的那个字典，它有 `strategyIds / budgetUsd / leverage / takeProfitUsd / stopLossUsd /
contextRefreshRounds / groupId / allocationPct / allocationMode / totalBudgetUsd /
positionStepPct / wakeIntervalMinutes / baseRules / profitGuard / tradingHorizon / sizing /
discipline / costBasis / feeRates`，但**没有**：

| 缺失字段 | 后果 |
|---|---|
| `accountId` | 续跑不知道用哪个 OKX 账户的凭证（多账户时会挑到"默认账户"，可能不是原账户）|
| `stopLossPct` / `takeProfitPct` | 续跑后**单仓交易所侧 OCO 百分比回落服务端默认**（`manager.py:867-870`），用户设的 1% 止损会变成出厂 5% ——一个不会报错的风控失真 |
| `priming` / `primingBars` | 暖机根数回落默认，与纪律预设的 `primingBars` 抬升（`api/ai_auto.py:136-141`）产物不一致 |
| `capitalUsd`（用户原始输入） | 只存了夹紧后的结果，续跑时若账户余额变化无法重算 |

**结论**：续跑的真源必须是**用户按下启动键时那份 `AiTradeGroupStartIn` 的原样 dump**，
而不是 `config_json`。

### D-3（阻断级）启动对账主动把残留会话归档 —— 这正是用户抱怨的那行代码

`init_db`（`db/session.py:68-82`）在建表后调 `_reconcile_orphan_ai_trade_runs`
（`db/session.py:173-188`），后者调 `repo.reconcile_orphan_ai_trade_runs`
（`ai_auto_repositories.py:651-670`）：把每一条 `mode='ai-trade' AND status='running'`
置 `stopped` + 回填 `end_ts` + `metrics.reconciled=True`。
注意它**只动数据库，不看交易所**——所以强杀时若持仓还在，本地已经"结案"，
而交易所上那个真实仓位没人再看它一眼。

**对照先例（照抄它）**：`_reconcile_orphan_discovery_runs`（`db/session.py:191-206`）
对挖掘任务的处理是 `running → paused`，判定顺序固定为
`discovery_enabled → run.autoResume → 续跑`，`pauseReason` 如实区分
`disabled_by_config` / `process_restart`（`discovery/manager.py:129-160`），
真正拉起循环由 `main.py:143-174` 的 `_resume_discovery_runs` 在静置延时后完成。
**本仓库已经有一套被验证过的"崩溃即隐式暂停 + 延时续跑"范式，本轮直接镜像它，
只把极性调过来（真实资金不自动续跑，见 §3.7）。**

### D-4（阻断级）桌面端退出很可能永远不触发后端的关停钩子

- `main.js:554-559` `before-quit` → `gracefulQuit()`（`main.js:486-504`）→ `pm.stop()`。
- `ProcessManager.stop(graceMs = 8000)`（`process-manager.js:417-438`）先对每个子进程调
  `_gracefulStop()`，等至多 8s，仍存活则 `_forceKill()`。
- `_gracefulStop()`（`process-manager.js:364-376`）在 Windows 上是
  `spawn('taskkill', ['/PID', pid, '/T'])`——**没有 `/F`**。而后端子进程是以
  `windowsHide` 拉起的无窗口进程（`process-manager.js:327` 注释明确"不 detached，避免独立控制台窗口"），
  `taskkill` 无 `/F` 对这类进程通常直接失败。
- ⇒ `main.py:56-88` lifespan 的 `yield` 之后那一串（含 `await get_ai_trade_manager().shutdown()`，
  `main.py:78`）**多数情况下不会执行**。

**设计结论（本轮最重要的一条）**：磁盘状态必须在**运行期**就写好（开仓 / 平仓事件即写 + 低频心跳），
使「优雅退出」退化为一个**纯优化**（§3.8 的 `quit-pause` 端点），而不是正确性的前提。
> 实现节点须实测确认这条（`taskkill /PID /T` 对 uvicorn 子进程的真实行为），并把实测结果记进
> §实施过程发现的方案缺陷。**即便实测发现它其实能优雅退出，本设计也不改**——正确性不该
> 建立在一个平台相关的信号语义上。

### D-5（阻断级）把 `state` 置成 `"paused"` 就完事 = 把整套风控一起拆掉

`_monitor_loop`（`session.py:487-495`）的循环条件是 `while self.state == "running"`。
一旦 `state` 变成任何别的值，循环**退出**，随之停摆的是 `_monitor_tick`
（`session.py:497-536`）里的全部内容：

| 停摆的东西 | 位置 | 停掉的后果 |
|---|---|---|
| `executor.reconcile` | `:501-503` | 不再与交易所对齐持仓；外部平仓不再被发现 |
| `_emit_equity` / `_emit_position` | `:507-508` | 面板数字冻结在暂停那一刻 |
| `_check_staleness` | `:509` | 行情断连不再告警 |
| `_maybe_profit_exit` | `:514` | **利润保护层不再平仓** |
| `_expire_profit_cooldown` | `:517` | 冷却期永不过期 |
| `_maybe_session_exit` | `:518` | **会话止盈 / 止损不再生效** |
| `schedule_from_executor`（兜底核实）| `:524-525` | 不再核实交易所侧止损是否真在 |
| `schedule_repair`（兜底自愈）| `:528-531` | 裸仓不再自动补挂 |

**结论**：`pause` 必须区分两个谓词——`is_active`（running ∪ paused，盯市与风控的作用域）
与 `state == "running"`（决策与开仓的作用域）。这张重定向表就是 §3.1 的核心交付物。

### D-6（阻断级）续跑若走既有 `start_group`，会被自己的前置门禁 100% 拒绝

`_member_preflight_fail`（`manager.py:401-417`）：
- `:408-410` 该标的已有持仓 ⇒ `AI_TRADE_PREEXISTING_POSITION`；
- `:411-413` 该标的已有未成交普通挂单 ⇒ `AI_TRADE_PENDING_ORDER_EXISTS`。

而"带仓暂停后续跑"的**定义**就是该标的上有持仓。
⇒ 续跑必须是一条**独立的**装配路径（`resume.py`），复用 `_build_member` 的装配逻辑，
但把"已有持仓"这一条从**拒绝**改为**受控接管**（§3.6 的判定矩阵），
其余各条门禁（不支持的标的 / 预算过小 / 未知挂单）一条不放。

### D-7（阻断级）前端 `state` 是**写死的字符串**，新增的组级状态会被静默吞掉

`groupStateFromStatus`（`useAiTradeGroupStream.ts:24-68`）是**逐键手抄**的映射，
第 64 行 `state: "running",` **写死**。同一函数的注释（`:53-63`）自己记着本仓库
已经在这里被"静默吞新键"咬过两次（`acceptance/exits/funding` 与 `discipline`）。
TS 侧 `AiTradeGroupState["state"]` 的联合类型是 `"running" | "stopped"`
（`aiTradeTypes.ts:1284`），`AiTradePanel.tsx:62` 据它算 `stopped`。
⇒ 三处必须同步改，且必须有一条用例专门守 `state` 不再被写死（否则暂停态在
"刷新页面后重发现"这条路径上恒显示为运行中——最难被发现的那种坑）。

### D-8 历史列表会把"已暂停"的分组当成已完成的历史会话展示

`list_ai_trade_groups`（`ai_auto_repositories.py:286-320`）的 `where` 只有
`mode == 'ai-trade' AND group_id IS NOT NULL`，**不看 `status`**；API 层
（`api/ai_auto.py:544-553`）只排除**内存里那个**活跃分组的 id。
⇒ 冷启后一个可续跑的 paused 分组会同时出现在「历史会话」列表里，
用户会看到同一次交易既"已结束"又"可继续"。

### D-9 与 live-auto 的互斥在冷启后失效，可能出现双管同一账户

AI 侧 `_require_capacity`（`manager.py:784-793`）判的是
`any(m.session.state == "running")`；live-auto 侧反向读
`get_ai_trade_manager().status()`（`manager.py:666-674` 的注释明确这条依赖）。
冷启后内存里没有分组 ⇒ `status()` 返回 `None` ⇒
**一个带仓的 AI 暂停会话完全拦不住用户去启动"策略自动交易"**，
两套自动化会同时管同一个实盘账户的预算。
⇒ manager 必须在冷启后持有一个**轻量占位**，使 `status()` 非空（live_auto 零改动即恢复互斥）；
AI 侧自己的容量闸也要显式识别 paused。

---

## 三、技术设计

### 3.0 状态机与四条不变式

```
                 ┌──────────────────────────── stop（既有，平仓+落终态）──────────────┐
                 │                                                                    ▼
   [无分组] ──start──► running ──pause──► paused ──resume──► running ...          [stopped/归档]
                 ▲        │                  │                                        ▲
                 │        │                  └──── end（归档，可选平仓）──────────────┘
                 │        └── 进程死亡（强杀/崩溃/断电）
                 │                     │
                 └─────────────────────┴─ 下次 init_db 的启动对账 ⇒ paused(process_restart)
```

`pause_reason` 值域（**稳定枚举，落库、进 wire、前端按它选文案**）：

| 值 | 何时产生 | UI 文案要点 |
|---|---|---|
| `user` | 用户在界面上点暂停 | 「你暂停了它」 |
| `app_quit` | 桌面端退出前 `quit-pause` 成功 | 「软件退出时自动暂停」 |
| `process_restart` | 启动对账发现残留 running | 「上次未正常退出，已自动暂停」 |
| `disabled_by_config` | 启动对账时 `ai_trade_pause_enabled=True` 但 **`ai_trade_enabled=False`**（v2 [P1-11]）| 「本机已关闭 AI 自动交易，此会话只能结束归档」 |

> **v2 [P1-11]：`disabled_by_config` 的极性被修正过。** v1 的 §3.7 把 `resume_enabled` 定义成
> `pause_enabled and ai_trade_enabled`，于是 AI 总闸一关就直接走"逐字回退老行为（running→stopped）"，
> 这一格**永远产生不出来**（死枚举 + 一段永不显示的文案）。更要紧的是极性错了：
> **"运维关掉 AI 总闸"绝不该等于"把一个仍持真实仓位的会话静默归档"**——那正是 §1.2 立论要堵的洞。
> v2 的分工是：**是否落 paused** 只由 `ai_trade_pause_enabled` 决定（整轴总闸，关 ⇒ 逐字回退老行为）；
> **是否允许 resume** 才由 `ai_trade_enabled` 决定（关 ⇒ 卡片仍出现，但只有「结束」可点，`resume` 403）。
>
> v1 里还有一个 `stale_feed_pause`「（预留，本轮不产生）」——**已删**。本仓库明令禁止留下没有
> 产生点的枚举值（"算了没落点 = 死数据"）。

四条不变式（实现与测试都按它们对齐）：

- **I-1（极性不变式）** `paused` 只封锁**增风险**侧：起新决策轮、开仓、加仓、反手、挂新限价单。
  **减风险**侧一律照旧：保护性平仓、会话止盈止损、时间强平、交易所侧兜底核实、兜底自愈补挂、
  撤挂单、`reconcile`。
  > 反例说明为什么必须写成不变式：如果暂停顺手把利润保护也停了，那么"用户暂停后去睡觉"
  > 这条路径上，一个已经浮盈 8% 的仓位会失去它唯一的本地退出执行者——而用户以为自己做的是
  > 一个**降低**风险的动作。
- **I-2（单占位不变式）** 任一时刻至多一个 AI 分组占位，无论它在内存里是完整会话（热）还是
  轻量占位（冷）。`AiTradeManager.status()` 在两种情况下都必须非空。
- **I-3（run 复用不变式）** 续跑**不新建 `strategy_runs` 行**，逐成员复用同一 `run_id`。
- **I-4（诚实不变式）** 暂停期间发生的一切（离线被 OCO 平掉 / 被外部加仓 / 挂单成交 /
  契约漂移）在续跑**之前**必须以差异清单呈现并要求用户确认；读不到就说读不到，**绝不推测、
  绝不编造盈亏数字**。

### 3.1 会话层：`AiTradeSession.pause()/resume()` 与判据重定向表

#### 3.1.1 两个谓词

在 `AiTradeSession` 上加（紧邻 `poll_interval_s`，`session.py:281-283`）：

```python
_ACTIVE_STATES = ("running", "paused")     # 模块级常量，紧邻 _ACTION_LABEL（session.py:124）

@property
def is_active(self) -> bool:
    """会话是否仍在**盯市与风控**作用域内（running ∪ paused）。

    与 `state == "running"`（**决策与开仓**作用域）刻意分开：不变式 I-1 的全部实现落点就是
    这两个谓词在 16 处判据上的分配（见 spec §3.1.2 的重定向表）。任何新增的判据都必须
    显式回答自己属于哪一侧，**禁止**新写 `state != "stopped"` 这类第三种写法。
    """
    return self.state in _ACTIVE_STATES
```

#### 3.1.2 判据重定向表（逐处对照，实现节点照此逐行改）

| # | 位置 | 现判据 | 新判据 | 归属 / 理由 |
|---|---|---|---|---|
| 1 | `_monitor_loop` `session.py:489`,`:491` | `state == "running"` | `is_active` | 风控侧；D-5 的根因就在这两行 |
| 2 | `_monitor_tick` `reconcile` `:501-503` | 随 loop | 随 loop | 风控侧：与交易所对齐是暂停期最该做的事 |
| 3 | `_emit_equity`/`_emit_position` `:507-508` | 随 loop | 随 loop | 面板在暂停期仍须显示真实浮盈 |
| 4 | `_check_staleness` `:639` | `!= "running"` | `not is_active` | 风控侧；含 `:647` 的 `flatten_on_stale → stop("stale_feed")`（减风险，保留）。**v2 [P2-7]**：`live_auto_flatten_on_stale`（`config.py:118`）**默认 `False`**，故默认不会发生；但运维一旦开启，行情断流会把一个「保留持仓的暂停」平掉并终结会话。实现须在 `pause()` 的回执 `notice` 与暂停横幅里如实说明这一条件，且 G-5 的用例要同时覆盖它 |
| 5 | `_maybe_profit_exit` `:1319` | `!= "running"` | `not is_active` | 风控侧（I-1 的招牌条款）|
| 6 | `_apply_profit_exit` 内层 `:1352` | `!= "running"` | `not is_active` | 同上（内层复检必须同口径，否则外层放行内层拦下）|
| 7 | `_maybe_session_exit` `:1257-1266` | 无判据（随调用点）| 保留（随 `_monitor_tick`）| 风控侧：会话止盈损是用户的风险契约 |
| 8 | `_expire_profit_cooldown` `:517` | 随 loop | 随 loop | 纯记账 |
| 9 | `_cost.schedule_maintenance` `:521-523` | 随 loop | 随 loop | 只读维护（资金费 / 费率 / 对账）|
| 10 | `schedule_from_executor`（兜底核实）`:524-525` | 随 loop | 随 loop | 风控侧：**暂停期它是唯一还在核实"止损真挂着吗"的东西** |
| 11 | `schedule_repair`（兜底自愈）`:528-531` | 随 loop | 随 loop | 风控侧 |
| 12 | `_window.maybe_refresh_tactical` `:534`（monitor tick）| 随 loop | **paused 时跳过** | 战术窗口只服务决策轮；暂停期刷它是白烧 REST 配额 |
| **12b** | **`_window.maybe_refresh_htf` `:691` + `maybe_refresh_tactical` `:693`（`feed_candle` 确认根路径）** | 随 `feed_candle` | **paused 时跳过** | **v2 [P1-1] 新增：v1 漏了这两处。** 与 row 12 同一条理由（都是只服务决策轮的 REST 取数），而暂停期 `feed_candle` 仍然入窗（row 16）⇒ 不跳过的话，每根 K 线都会照打两次 REST，row 12 省下的配额被这里原样烧掉 |
| 13 | `_preview_ready` `:881`（`_maybe_signal_flip_wake` 在 `:855` 第一件事就调它）| `== "running"` | **不改** | 增风险侧入口。**v2 更正**：v1 把判据记在 `:535`，那只是 `_monitor_tick` 里的**调用点**、本身无判据；`_maybe_signal_flip_wake`（`:854-877`）自己也没有 `self.state`。真正的门在 `_preview_ready`——**结论（暂停期自然早退）正确，位置记错了**。实现节点不要因为"看不到判据"就在 `:854` 另加一个 |
| 14 | `_maybe_interval_wake` `:552` | `!= "running"` | **不改** | 增风险侧入口 |
| 15 | `_maybe_catchup_wake` `:838` | `!= "running"` | **不改** | 增风险侧入口（决策轮 `finally` 里那次补跑也因此自然失效）|
| 16 | `feed_candle` 顶部 `:666` | `!= "running"` | `not is_active` | 暂停期**仍要入窗**：刷新 `_last_live_px` / `_last_px_wall`（保护层判定价年龄的唯一真源，`:243-247`）、`_atr_px`、`bar_anchor_ts`，并推 `candle` 事件 |
| 17 | `feed_candle` 决策起点（**新增**，插在 `:702` 的 `_push candle` 之后、`:703` 的 `if self._deciding` 之前）| — | `if self.state != "running": return` | 增风险侧：暂停期绝不起决策轮。**v2 [P1-1] 插点约束（不得上移）**：`:698` 的 `evaluate_signal_board`（影子 SimBroker 逐根步进）与 `:696-701` 的 `_last_board` / `_last_bar_index` / `_last_live_px` 赋值**必须在暂停期继续执行**——它们是纯本地计算、不下任何单，却是「续跑后第一轮拿到的是最新信号板而不是暂停那一刻的化石」的唯一保证。把门禁提到 `:690` 会静默冻结影子立场，且这类错误**不会报任何错**，只会让续跑后的第一轮决策基于一份陈旧证据 |
| 18 | `_execute_decision` 内层 `:1181` / `_execute_cancel` `:1203` | `!= "running"` | **不改** | 已在 `_exec_lock` **内**（`:1180`），是 pause 的天然安全网（见 3.1.3）|
| 19 | `stop()` 幂等闸 `:429-430` | `!= "running"` | `not is_active` | 必须能从 paused 直接停（并平仓）|

> **v2 [P1-2]：v1 那句「只此 19 处 / `self.state` 出现次数 == 19」是算术错误，已作废。**
> 实测 `grep -c "self\.state" services/quant/app/ai_auto/session.py` = **16**，而本表有 20 行——
> 因为其中 9 行（2 / 3 / 7 / 8 / 9 / 10 / 11 / 12 / 12b）**根本不含 `self.state`**（它们"随 loop"
> 或"随 feed_candle"，靠调用者的判据间接生效）。改造后这个数还会再变（row 17 新增 1 处、
> `pause()` / `resume()` / `is_active` 各带若干处）。**任何写死数字的冻结用例第一天就是红的。**
>
> 正确的冻结方式（G-3 v2）分三条，全部是**点位级**而非计数级：
> 1. **归类完备**：把 `grep -n "self\.state" session.py` 的每一行输出，与本表 + 一份
>    「不参与判定的赋值 / 回显点」白名单（当前为 `:214` 初始化、`:431` `stop()` 赋值、
>    `:1636` `_status_payload`、`:1699` `info`，加上 `pause()`/`resume()` 新增的赋值）逐行配平；
>    出现任何两边都没有的行即红。**清单本身在实现时写进用例常量并附一行归属说明。**
> 2. **禁止第三种写法**：断言全文**不存在** `state != "stopped"` / `state in (` 之类的自造判据
>    （只允许 `== "running"` / `!= "running"` / `is_active` / `not is_active` 四种形态）。
> 3. **正则必须写 `\r?\n`**（`core.autocrlf=true`，写死 `\n` 在干净检出上恒红）。

#### 3.1.3 `pause()` 的动作顺序（顺序本身是安全性的一部分）

```python
async def pause(self, reason: str = "user", *, flatten: bool = False) -> dict:
    """挂起本会话：只停"增风险"侧，保留盯市与全部保护层（不变式 I-1）。

    动作顺序是安全性的一部分，**不得重排**：
      ① 先置 `paused` —— `_execute_decision` 的复检（session.py:1181）在 `_exec_lock`
         **内**，故此后任何还没拿到锁的在飞轮都不可能再下单；
      ② 再 `async with self._exec_lock: pass` 排空 —— 若某一轮**已经**拿着锁在下单，
         就等它把回执记完（这比在 place_order 的 await 上把它 cancel 掉安全得多：
         那会造成"交易所有单、本地无记录"）；
      ③ 才 cancel 决策任务（此刻它已无害；`_run_decision_round` 的 `finally`
         （session.py:822-826）会把 `_deciding` 复位，故续跑后能正常起轮）；
      ④ 撤掉未成交限价挂单（暂停期没有任何东西在盯它的 TTL 与部分成交）；
      ⑤ **向交易所读一次持仓断面**：`await fetch_live_positions(self.executor.creds)`
         （复用 executor 已持有的凭证，**零 executor 改动**；`live_auto/executor.py:153` 走的
         就是这个函数）。三点理由：
           · v2 [P2-8]：**必须在 ④ 之后**——三态撤单可能返回 `filled` 并当场改写 `projected`，
             先读会给出一份"已空仓"的假回执，而用户正要据它决定关不关电脑；
           · v2 [P0-1]：这是 `posId` / `cTimeS` 的**唯一采集点**（S3' 加的三键），
             没有它续跑就没有身份可比；
           · 用交易所真值而不是 `executor.projected`：暂停是一次用户主动的、每会话一次的动作，
             多打一次私有 REST 完全值得，而"我关机时到底还有多少仓"这个数不该来自本地投影。
         读失败 ⇒ **不阻断暂停**（暂停是减风险动作，恒可执行），但回执 `position` 置
         `{"unavailable": true, …}`、状态表该成员 `position` 落 `null` + `positionUnknown: true`，
         **续跑时该成员直接判 `unverifiable_identity`**（诚实不变式 I-4）；
      ⑥ 带仓则**同步等一次**交易所侧兜底核实（软件即将关闭，这是用户唯一一次
         知道"关机后还有没有止损"的机会）；
      ⑦ 落 run metrics（status='paused'，不写 end_ts）；
      ⑧ 写状态表（W4）与内存 `group.state`——**⑧ 必须在 ⑦ 之后**，见 §3.4.2 W1/W4 的极性。
    幂等：非 `running` 直接回上次结果。
    """
```

- ④ 用既有 `executor.cancel_resting("pause", ts)`（**`live_auto/executor.py:383-391`** —— v2 [P1-4]
  更正路径：`ai_auto` 下没有 `executor.py`，`LiveExecutor` 是与 `live_auto` **共用**的那一个），
  事件经 `self._dispatch` 扇出与落库（与 `flatten` 的撤单事件同路径，`session.py:461-468`）。
- ⑥ 需要给 `BackstopRuntime` 加一个**可等待**的入口（`exchange_backstop.py`，紧邻
  `force_next` `:364-366`）：

```python
async def check_now(self, now_wall: float, *, side: str, position_sz: float | None,
                    local_sl_px: float | None, local_tp_px: float | None,
                    push=None, timeout_s: float = 8.0) -> dict | None:
    """同步等一次核实并回 `wire()`（暂停回执 / 续跑复核专用）。

    **复用 `schedule_check` 的同一条私有路径**（单飞、TTL、异常全吞），只是把在途任务
    `await` 出来——绝不在此另写一份取数逻辑（两份取数迟早给出两种"覆盖率"）。
    超时 / 异常一律回 `self.wire(now_wall)`（那已是一个诚实的 `unavailable`），**绝不抛**。

    v2 [P2-4]：**必须先 `self.force_next()` 再调 `schedule_check`**。`schedule_check`
    （`exchange_backstop.py:357-360`）在 `ai_trade_backstop_poll_ttl_s`（默认 **120s**）内会
    直接 `return` 不建任务，于是 `check_now` 会 `await` 到一个早已 `done()` 的旧任务并
    原样回一份**上一次**的读数——用户看到的"已核实覆盖 100%"其实是两分钟前的事实。
    """
```

- ⑦ 把 `_finalize_run`（`session.py:1719-1765`）里"构造 metrics"的那一段抽成
  `def _build_run_metrics(self) -> dict`，`_finalize_run` 与新增的
  `async def snapshot_run_metrics(self, *, status: str) -> None` 共用它。
  **绝不复制那段富化代码**（它有 20 行 `aiStats` 合并与一个 best-effort try，抄一遍必漂移）。
  > v2 [P2-5]：**不需要新的仓储函数**。`repositories.finalize_run`（`:318-334`）的签名已经是
  > `(session, run_id, *, status, metrics=None, end_ts=None)` 且 `if end_ts is not None` 才写
  > `run.end_ts`——直接 `finalize_run(s, run_id, status="paused", metrics=…)`（不传 `end_ts`）
  > 即满足"落 metrics、置 paused、不写 end_ts"。函数名叫 finalize 但它只是一次 UPDATE。

#### 3.1.4 `resume()`（热续跑）的动作顺序

```python
async def resume(self) -> dict:
    """从 paused 回到 running（会话对象仍在内存里的那条路径）。

    ① 校验 `state == "paused"`（否则 409，绝不静默）；
    ② 重置三个时钟，避免"续跑瞬间连打 N 轮"：
       `_last_decision_started_wall = now`（定时唤醒下一次在 ~wake_interval_s 后，
       与 `start()` 的 `session.py:396-397` 同一条理由）、
       `_last_candle_wall = _last_px_wall = now`（否则暂停期累积的时长会被
       `_check_staleness` 立刻判成陈旧并触发降级）；
    ③ `_deciding = False`（防御性：pause 的 cancel 已由 finally 复位，此处兜底）；
    ④ `state = "running"`；
    ⑤ 推一条 status + 一条 `resumed` 事件（供轨迹面板留痕）。
    **不重新暖机、不重建 WS**：热续跑期间 `self._ws` 一直连着（pause 从不停 WS，
    这是它与 `stop()` 的第三个区别）。
    """
```

> **pause 与 stop 的区别（写进 docstring，防后人"顺手统一"）**：
> stop 会 ① 停 WS（`session.py:437-438`）② 平仓（`:443`）③ `_backstop.close()` /
> `_repair.close()`（`:435-436`）④ 落 run 终态（`:450`）。pause **四件都不做**。

### 3.2 执行层：暂停期 `LiveExecutor` 的行为

**不改 `LiveExecutor` 任何一行。** 它是纯被动对象：`apply()` 只在决策轮里被调用，
`reconcile()` 只在 `_monitor_tick` 里被调用。暂停 = 没有人再调 `apply()`，
而 `reconcile()` 照旧被调。这是本轮能做到"零回归"的结构性原因。

> **v2 [P1-4]：`LiveExecutor` 是与 `live_auto` 共用的，不是 AI 侧私有的。**
> 它的文件是 **`services/quant/app/live_auto/executor.py`**（`ai_auto/` 下**没有** `executor.py`），
> 由 `ai_auto/session.py:24` 的 `from ..live_auto.executor import LiveExecutor` 引入；
> `ai_auto/session.py` 还共用 `live_auto/explain.py` 与 `live_auto/resting.py`。
> `api/ai_auto.py:94-95` 的注释更是明文钉着「`LiveExecutor` 被 `ai_auto` 与 `live_auto`
> **共用同一个 settings 对象**」。
> 所以 §九.4 那句「`live_auto` 不共用本文任何代码路径」**是错的**（v2 已改写）。真正成立的
> 命题是更强的那一句：**本轮对 `LiveExecutor` 零改动**，因此 `live_auto` 零影响。
> 反过来说：一旦实现节点发现"顺手在 executor 里加一个 paused 判据"更省事，那就是在
> **同时改 live_auto 的真实资金执行器**——本轮明令禁止。全文所有 `executor.py:NNN` 的行号
> 经复核正确，只是路径前缀缺 `live_auto/`，v2 已逐处补齐。

唯一需要注意的既有行为：`_on_reconcile_ok`（`live_auto/executor.py:611-640`）在
「自管有仓、交易所无仓」时会合成一条 `external_close` 成交。暂停期这条逻辑**仍然生效**，
于是"暂停期间被交易所侧 OCO 平掉"会在**软件还开着**的情况下被自动发现并如实记账 ——
这正是我们想要的，不需要任何新代码。冷续跑（软件关着）时它管不到，由 §3.6 的判定矩阵接手。

### 3.3 分组层：`AiTradeManager` 的新方法与冷占位

新增（`manager.py`）：

```python
@dataclass
class PausedGroupRef:
    """冷启后的**轻量**分组占位：只承载"存在一个可续跑分组"这个事实 + 展示所需的最少字段。

    刻意**不含** AiTradeSession —— 建会话要暖机、要 WS、要 LLM 客户端，那是 `resume` 的工作，
    不是"知道自己有个暂停会话"的代价。它存在的第一理由是不变式 I-2：让 `status()` 非空，
    使 `live_auto._require_capacity` 零改动地恢复互斥（D-9）。
    """
    group_id: str
    bar: str
    account_id: int
    total_budget_usd: float
    leverage: float
    strategy_names: list[str]
    paused_ts: int
    pause_reason: str
    resumed_count: int
    members: list[dict]          # [{instId, runId, allocationPct, budgetUsd, lastPosition|None}]
    start_wire: dict             # 原样的启动请求体（续跑的唯一真源）
    resolved_echo: dict          # 落地回执快照（供"续跑前后逐字段对照"）
```

| 方法 | 签名 | 职责 |
|---|---|---|
| `pause_group` | `async (group_id, *, reason="user", flatten=False) -> dict \| None` | 逐成员 `session.pause(...)`；聚合回执；`self._group.state = "paused"`；**不清 `self._group`**；写 `ai_trade_group_states` |
| `resume_group` | `async (group_id, *, acknowledged: bool) -> AiTradeGroup` | 热：逐成员 `session.resume()`；冷：委托 `ai_auto/resume.py` 重建；成功后 `state="running"` + 写状态表 + **清 `_paused_ref`**。v2 [P1-9]：`on_drift` 形参已删（漂移只有"拒绝"一条路，理由见 §5.3）|
| `end_group` | `async (group_id, *, flatten: bool \| None, acknowledged: bool) -> dict \| None` | 归档 paused 分组：平仓与否由调用方**显式**给（v2 [P1-12]：带仓时 `flatten is None` ⇒ 400）；走 `session.stop` 或只落 run 终态 + 状态表置 `ended` + 清占位；回执必带 `orphanPositions` |
| `resumable` | `() -> dict \| None` | 冷占位的只读 wire（`GET /ai-trade/status` 的 `resumable` 键）|
| `adopt_paused_ref` | `(ref: PausedGroupRef) -> None` | 由 lifespan 的水合协程调用，装上冷占位（**不启动任何东西**）|

改造：
- `status()` / `group_status()`（`manager.py:666-705`）：
  - `group_status()` 顶部 `group is None` 时**不再直接回 `None`**，而是 `return None`
    保持原样（`group` 键语义不变：它只描述内存里的真实会话）；
  - 新增 `state: group.state` 键到返回字典（**紧邻 `createdTs`**）；
  - `status()` 改为 `self.group_status() or self._paused_wire_for_mutex()`
    —— 让 live_auto 侧那句 `is not None` 在冷占位下也为真（D-9）。
  - **v2 [P0-3]：`_paused_wire_for_mutex()` 在 `self._hydration_failed` 为真时也返回非空。**
    `status()` 必须保持**同步**（`live_auto/manager.py:469` 在同步函数里调它），所以它不能查 DB；
    唯一安全的做法是让"我不知道有没有暂停分组"这个状态本身**也算占位**。
    盲区不会长期存在：任何一次 AI 侧 `_require_capacity` 的 DB 读成功即把它清掉（上面那段）。
    水合协程自身也要**有界重试 3 次 / 退避 1s**，把 `_hydration_failed=True` 压成罕见事件。
    > 这条的极性说明必须写进 `_paused_wire_for_mutex` 的 docstring：**"读不到"与"没有"
    > 在真实资金上不是同一件事**；在这两者之间选默认值时，永远选更保守的那一个。
    > ⚠️ 这是本轮唯一一处**改变既有函数返回值语义**的地方。`status()` 的现有唯一消费者
    > 是 `live_auto/manager.py` 的互斥判定（`manager.py:669-672` 注释已钉死这条依赖），
    > 它只判 `is not None`，故安全。实现节点须 `grep -rn "\.status()" services/quant/app`
    > 复核没有第二个消费者读它的字段（G-11 冻结这一条）。
- `_require_capacity()`（`manager.py:784-793`）：**改为 `async def`**（唯一调用点
  `start_group` `manager.py:265` 已在 `async with get_admission_lock(), self._start_lock` 内，
  `live_auto` 侧那个同名方法是**另一个函数**、不受影响），在两条既有闸之前加第一条：

```python
# v2 [P0-3]：真值取**状态表**，不取内存。内存 `_paused_ref` 只是展示与互斥的缓存，
# 而它的装载（§3.7 的水合协程）按仓库惯例是「失败只 warning 不阻断启动」——
# 一次偶发的水合失败绝不该变成「可以在一份带仓的暂停契约之上再开一个新分组」。
# 这条 DB 读同时**自愈** `_hydration_failed`：读到了就说明 DB 可用，盲区解除。
paused = await load_paused_group_brief()          # ai_trade_state_repositories，只读一行
self._hydration_failed = False                    # 能读到即解除盲区（对 live_auto 同步生效）
if paused is not None or (self._group is not None and self._group.state == "paused"):
    raise AiTradeSessionLimitError(
        "AI_TRADE_PAUSED_GROUP_EXISTS",
        "已有一个已暂停的 AI 自动交易分组（请先继续或结束它）", 409)
```
  > 若这条 DB 读**本身**抛异常：`raise AiTradeSessionLimitError("AI_TRADE_RESUME_UNVERIFIABLE",
  > "无法确认是否存在已暂停的 AI 自动交易分组，暂不启动新分组（请稍后重试）", 409)`。
  > **极性与 §3.5 S3 一致：读不到就不开新仓，绝不 fail-open。**

- **`stop_member()`（`manager.py:638-650`）—— v2 [P1-10] 新增的必改点。** 它末尾那句
  `if not any(m.session.state == "running" for m in group.members): self._group = None`
  在 paused 分组下**恒真**（没有任何成员是 running），于是对一个 paused 组调一次既有的
  `POST /ai-trade/{gid}/member/{inst}/stop`（`api/ai_auto.py:369`，公开端点）就会**把整个占位清掉**：
  其余 paused 成员的 monitor 还在后台跑（`is_active`）却再也路由不到，互斥与续跑卡同时消失。
  改法两条，缺一不可：
  1. 判据换成 `if not any(m.session.is_active for m in group.members)`（**唯一真源仍是 `is_active`**）；
  2. 清组的同时必须走 §3.4.2 的 W5 把状态表置 `ended`（否则磁盘上留一张指向空分组的续跑卡）；
     若仍有 `is_active` 成员，则只从 `members_json` 摘掉这一个成员。

- `shutdown()`（`manager.py:767-774`）：按 `settings.ai_trade_shutdown_action` 分派 ——
  **`"hold"`（v2 默认，见 [P0-2]）** 走 `pause_group(reason="app_quit")`；`"flatten"`（可选）
  走既有 `_stop_session`。paused 分组在两种配置下都**只落状态、不平仓**。
  > **v2 [P0-2]：默认值从 `"flatten"` 改成了 `"hold"`。** v1 的理由是"与今天行为逐字相同"，
  > 但 D-4 刚刚证明**今天这条路几乎从不执行**（`taskkill` 无 `/F` + 8s 后 `/F` 强杀，
  > lifespan 的 `main.py:74` 大概率跑不到）。而 §3.8 新加的 `quit-pause` 是一条**HTTP** 钩子，
  > 它是**能跑通的**。两件事合起来，`"flatten"` 默认不是"保持现状"，而是**新增**了
  > 「每次关软件都平掉真实仓位并归档」——用户重开后什么都续不了，本轮需求原地失效。

- **`_paused_ref` 的生命周期（v2 补全，I-2 的实现落点）**：只在两处赋非空
  （lifespan 水合 `adopt_paused_ref` / `pause_group` 走冷路径时不赋——热暂停用 `self._group`），
  在三处清空（`resume_group` 成功、`end_group` 成功、`stop_group` 命中该 group_id）。
  **`_group` 与 `_paused_ref` 永不同时非空**（G-9 冻结这一条）。

### 3.4 持久化：一张新表 + 四个写点 + 「派生量绝不落库」

#### 3.4.1 为什么是新表而不是加列

本仓库有明文教训：`create_all` **不会**给既有表补列，加列必须写 `ALTER TABLE` 幂等补丁
（`db/session.py:84-170` 有四个这样的函数，`:150-170` 的注释直接写着"绝不指望 create_all
给既有表补列"）。而 `create_all` **会**建缺失的**表**。
⇒ 新表 = 零迁移代码、老库自动获得、不可能出现 `no such column`。

#### 3.4.2 四个写点（覆盖"断电与点暂停不可区分"）

| # | 触发时机 | 写什么 | 为什么必须有它 |
|---|---|---|---|
| W1 | **`_launch_group_or_rollback` 返回成功之后**（`manager.py:277` 那句 `return await …` 拆成先拿结果再写盘）| 整行 upsert，`status='running'` | 契约在**第一秒**就在磁盘上；此后任何时刻断电都可续跑 |
| W2 | `_dispatch` 里**每一笔成交落库之后**（`session.py:340` 的 `await self._persist_trade(trade)` 之后）| 只更新该成员的 `position` / `positionSeq` / `restingClOrdId` / `knownClOrdIds` | 持仓事实**只在成交时变**；事件驱动写 = 精确且零轮询开销 |
| W3 | 盯市 tick 上按 `ai_trade_pause_persist_interval_s`（默认 60s）节流 | 只更新 `last_seen_ts` | 离线时长文案与超龄判定的分母 |
| W4 | `pause_group` / `end_group` / `resume_group` | 整行 upsert（`status` + `pause_reason` + `paused_ts` + `resumed_count`）| 显式转移 |
| **W5** | **会话自终止之后**（v2 [P1-6] 新增）：`session.stop()` 落 `stopped` 的**全部**来源 —— 暂停期触发的会话止盈止损（`_maybe_session_exit`）、`stale_feed` 强平（`:647`）、启动回滚 `stop("start_failed")`、`stop_member` 清掉最后一个 `is_active` 成员 | 状态表置 `status='ended'`（或摘掉该成员）| **没有它，G-5 断言的「状态表置 ended」就没有任何实现落点**：`strategy_runs` 已是 `stopped` 而状态表仍 `paused` ⇒ 进程内留一张指向已平仓会话的续跑卡，用户点「继续」会走到一个 `closed_offline` + run 已终态的怪状态 |

> **v2 [P1-5]：W1 的写点从"`self._group` 赋值之后"挪到了"启动成功之后"。**
> v1 的写点在 `manager.py:276-277` 之间，即**在 `_launch_group_or_rollback` 之前**。
> 启动失败时回滚（`manager.py:602-617`）会把全部成员 `stop("start_failed")` 并 `self._group = None`，
> 磁盘上却留下一条 `status='running'` 的**幽灵契约**——而 §八风险 11 的自愈只覆盖 `paused` 行。
> 挪到启动成功之后**零信息损失**：`session.start()` 只做暖机 + 建 WS + 起 monitor，
> **不下任何单**（`session.py:385-425`），启动失败时交易所上不可能有本会话开出的仓位，
> 也就没有任何东西需要被续跑。W5 同时兜住回滚路径（若 W1 因竞态已写入）。

W2/W3 一律 **best-effort**：包 `try/except` + `logger.warning`，失败绝不阻塞热路径
（镜像 `_persist_trade`，`session.py:376-382`）。W1/W4/W5 失败则**必须**上抛
——契约没落地就说续跑可用是欺骗（这条极性差异要写进注释）。
> W5 有一处例外：它挂在 `stop()` 的收尾上，而 `stop()` 本身承诺"平仓失败也要完成停止流程"。
> 故 W5 失败时**不得**吞掉 `stop()` 的结果，而是 `logger.error` + 在停止回执里带一个
> `stateRowStale: true`；下次启动的水合会按 §八风险 11（run 已 `stopped` 而状态表仍 `paused`
> ⇒ 置 `ended` + warning）自愈。**这是全文唯一允许状态表短暂落后于 run 的地方，必须写进注释。**

#### 3.4.3 「派生量绝不落库」

只落**不可推导**的事实：

| 落库 | 不落库（续跑时从 DB `trades` 重算）|
|---|---|
| 启动请求原文 `start_wire` | `realized_pnl_usd` = Σ 平仓腿 `pnl`（`net_mode` 口径与 `reconcile.local_net_view`（`reconcile.py:39-65`）**同一条规则**）|
| 落地回执 `resolved_echo` | `position_seq` = `side == 'close'` 的行数 |
| 成员 `runId` / `allocationPct` / `budgetUsd` | `entry_fee_usd` = 当前未平持仓周期内开仓腿 `fee` 之和 |
| 持仓身份断面（side/size/avgEntry/openWallTs）| `executor.trades`（由 `trades` 行反向重建）|
| `restingClOrdId`（暂停时若有）| 滚动摘要（从 `metrics.finalSummary` 读）|

理由：派生量落两份必然漂移，而这里漂移的是**真实资金的盈亏显示**。
`trades` 表已经是这些数字的唯一真源（`append_live_trade`，`repositories.py:278-310`，
含 `fee` / `pnl` / `stop_loss_price` / `take_profit_price` / `explain_json.meta.wallTs`）。

### 3.5 续跑（冷）七步流水线

全部装配在**新文件** `services/quant/app/ai_auto/resume.py`，由 `manager.resume_group` 调用：

| 步 | 动作 | 失败处置 |
|---|---|---|
| S1 | 读状态行（`load_resumable_ai_trade_group`）；超龄（`> ai_trade_resume_max_age_s`，默认 7d）⇒ 只许归档 | 404 / 409 `AI_TRADE_RESUME_EXPIRED` |
| S2 | 用 `start_wire` **重跑一遍 `/ai-trade/start` 的解析链**：`_resolve_account` → `_build_specs` → `_resolve_discipline`（**必须第一个**，`api/ai_auto.py:96-110` 的 [P1-2]）→ 其余 `_resolve_*` | 策略被删 / 账户凭证被清 ⇒ 409 + 明确原因 |
| S3 | 采集交易所真值：`fetch_live_positions` + `_fetch_blocking_pending`（`manager.py:367-379`）+（按需）`fetch_position_history` | **读失败 ⇒ 拒绝续跑**（极性反转，见下）|
| **S3'** | **v2 [P0-1]：让持仓带上身份。** `_normalize_position`（`okx/accounts.py:154-167`）当前把 OKX 原始行里的 `posId` / `cTime` / `uTime` **全丢了**，于是 S3 拿回来的持仓**无法回答"这是不是暂停时那一个"**。改法与本仓库 `_normalize_fill` 的既有先例完全同款——**仅"停止丢弃"，不改任何既有键**：加 `posId: str`、`cTimeS: int`（秒）、`uTimeS: int`（秒）三键，缺失容错为 `""` / `0`。| 三键缺失（老 OKX 响应 / 字段为空）⇒ 该成员判 `unverifiable_identity` ⇒ 拒绝续跑（与 S3 同极性）|
| S4 | 逐成员过 §3.6 判定矩阵 ⇒ `verdict` + 建议动作 | `blocked` ⇒ 409 携逐成员明细。**v2 [P2-13]：续跑是整组原子的** —— 任一成员 `blocked` ⇒ **整组**拒绝，绝不"能续的先续"。理由：预算切分、`GroupExposureBook` 的 `unit_notional`（`manager.py:492-496`）与验收卡口径都是**分组级**的，半组续跑会让这三样同时失真；而 §九.2 已经把"成员级暂停"列为非目标，续跑侧必须与它同口径 |
| S5 | 逐成员建 Executor / Decider / Session（复用 `_build_member` 的**同一条**装配路径，只把 `_create_group_run` 换成"取既有 run"）| 任一失败 ⇒ 整组回滚（镜像 `_launch_group_or_rollback`，`manager.py:602-617`）|
| S6 | 接管：把 §3.4.3 右列的连续量灌回 executor / decider / session；`state="running"` | — |
| S7 | `session.start()`（暖机 + WS + monitor）→ 写状态表 `status='running'`、`resumed_count += 1`、run 状态回 `running` | 失败 ⇒ 整组回滚并把状态表**留在 paused**（可再试）|

> **S3 的极性是刻意反转的。** 本仓库的旁路统计（`_probe_tradability`，`manager.py:279-301`；
> 兜底核实；成本估算）一律 **fail-open**——"探针把用户挡在门外比给错建议更坏"。
> 但**续跑不是旁路统计，它是一次开仓授权**：在读不到"我现在到底有没有仓、有多大"的情况下
> 武装一个会开真实仓位的 Agent，等于闭眼下单。故 S3 一律 **fail-closed**，
> 错误码 `AI_TRADE_RESUME_UNVERIFIABLE`，文案给"稍后重试 / 先结束归档"两条出路。
> 这条极性差异必须写进 `resume.py` 的模块 docstring（否则后人会"顺手统一"成 fail-open）。

#### S6 的六项接管细节（每项都有一个会被漏掉的坑）

| 项 | 做法 | 漏掉的后果 |
|---|---|---|
| `executor.projected` / `entry_price` / `entry_qty_sz` | 取**交易所真值**（`posSide`→side、`pos`→size、`avgPx`→entry），不取快照 | 用快照会在"离线期间部分平仓"时高估持仓 |
| `executor.entry_bar` | `cur_index - floor((now - openWallTs) / BAR_SECONDS[bar])`，下界 0。**`openWallTs` 的取值顺序（v2 [P2-14] 改）**：① 交易所真值 `position.cTimeS`（S3' 新增，**首选**）；② 本地当前持仓周期开仓腿的 `explain.meta.wallTs`（`session.py:354-355` 落的那个）；③ 都没有则回落 `cur_index` 并在回执里如实说明 | 回落时 `bars_in_position` 从 0 起算 ⇒ `max_hold_bars` 时间强平被推迟一整个持仓周期。**优先用 `cTime` 的理由**：离线期间"先加仓再减回同样张数"会让本地 `wallTs` 指向一个早已不存在的开仓腿，而 `cTime` 是交易所对**这个 posId** 的权威开仓时刻 |
| `executor.realized_pnl_usd` / `trades` / `entry_fee_usd` | §3.4.3 右列重算 | 面板"会话累计盈亏"归零；`local_net_view` 与交易所窗口对账（`reconcile.py:105-151`）必然报 drift |
| `session.started_ts` | 取**原分组** `createdTs`（不是续跑时刻）| 取续跑时刻会让窗口对账只覆盖后半段，`coverageMismatch` 恒真 |
| `session._position_seq` | 平仓腿计数 | 归零会让"保护平仓 → 同一 tick 同向重开"的 epoch 身份判定（`session.py:210-212`）失效 |
| `decider` 上下文 | `seed_from_snapshot(summary=metrics.finalSummary, last_round=metrics.roundsCompleted)`；`session.rounds_completed` 同步 | 不做则 AI 完全忘记暂停前在想什么，且轮次编号从 1 重来（轨迹面板出现两个"第 1 轮"）|

`decider` 侧的新增（`decider.py`）：把 `_refresh_summary` 里构造"摘要种子"的
`decider.py:610-615` 抽成 `def _install_summary_seed(self, text: str) -> None`，
新增的 `seed_from_snapshot` 与它**共用**——两处各写一遍那段提示语，
"历史观望不构成继续观望的理由"这句反惯性尾注（F3）在续跑路径上就会消失。

### 3.6 离线差异判定矩阵（`ai_auto/pause.py` 的纯核心）

```python
def classify_member_drift(*, expected: dict | None, actual: dict | None,
                          pending: list[dict], known_cl_ord_ids: set[str],
                          paused_ts: int, size_tol: float) -> tuple[str, str]:
    """(verdict, message)。**纯函数、无 IO、绝不抛**（判定与取数分离，便于穷举单测）。

    v2 [P0-1]：`expected` / `actual` 都必须携带**持仓身份**（`posId` / `cTimeS`）。
    只比 (side, size) 是不够的——「我们的仓被平掉、用户随后自己开了同向同量的一手」
    在只比 (side,size) 的判据下是 `matched`，那正是 R-1 明令禁止的静默接管。
    `paused_ts` 是身份判据的第二个支点：`actual.cTimeS > paused_ts` ⇒ 这一定不是我们的仓。
    """
```

| verdict | 判据 | 处置 | UI |
|---|---|---|---|
| `matched` | 方向同、\|Δsize\|/expected ≤ `size_tol`（默认 2%）、**且身份相符**（`posId` 相等，或 `posId` 两侧都缺时 `cTimeS ≤ paused_ts`）| 直接接管 | 绿点「与暂停时一致」|
| `flat_ok` | 暂停时无仓、现在也无仓 | 直接接管 | 绿点 |
| `partially_closed` | 身份相符、方向同、size 小于 expected 超容差 | 按**真值**接管；差额记入离线事件 | 黄条「离线期间部分平仓」+ 需勾选 |
| `closed_offline` | 暂停时有仓、现在无仓 | 以空仓接管；尝试回读真实退出（下段）| 黄条「离线期间已被平掉」+ 需勾选 |
| `size_increased` | 身份相符、方向同、size 大于 expected 超容差 | **拒绝** | 红条 + **一条**出路：`结束归档`（见下方 [P1-9]）|
| `side_flipped` | 身份相符但方向不同 | **拒绝** | 同上 |
| **`foreign_position`**（v2 [P0-1] 新增）| 暂停时**有**仓、现在也有仓，但**身份不符**：`posId` 不等，或 `actual.cTimeS > paused_ts` | **拒绝** | 红条「当前持仓不是暂停时那一笔（交易所记录的建仓时间在你暂停之后）。继续会让 AI 接管一笔它从未开过的仓位。」+ 出路：`结束归档` |
| **`opened_offline`**（v2 [P1-8] 新增）| 暂停时**无**仓（`expected is None`）、现在**有**仓 | **拒绝** | 红条「暂停期间该标的出现了一笔不是本会话开出的持仓」+ 出路：`结束归档` |
| **`unverifiable_identity`**（v2 [P0-1] 新增）| `actual` 存在但 `posId` 与 `cTimeS` **都**取不到（S3' 的三键缺失）| **拒绝** | 红条 +「无法确认这是不是暂停时那一笔持仓，稍后重试」|
| `unknown_order` | 该标的有 `kind ∈ (limit_open, limit_close)` 的挂单且 `clOrdId ∉ known` | **拒绝** | 红条「请先在实盘面板处理该挂单」（复用既有 `AI_TRADE_PENDING_ORDER_EXISTS` 文案口径）|
| `unreadable` | S3 取数失败 | **拒绝** | 红条 + 「稍后重试」|

> **v2 [P1-8]：为什么 `opened_offline` 必须自成一格，而不是塞进 `size_increased`。**
> v1 的 G-12 把「暂停时无仓、离线期间被外部开仓」（`expected=None & actual≠None`）判成
> `size_increased`，但那一格的判据白纸黑字写着"方向同、size 大于 expected 超容差"——
> `expected=None` 时既没有方向可比也没有 size 可比，**判据不成立、文案也不成立**
> （UI 会显示"持仓比暂停时变大了"，而暂停时根本没有持仓）。而 §3.10 定的是
> 「判定性文案由后端单点生成」，一句自相矛盾的话会**原样**下发给正拿着真钱的用户。
>
> **v2 [P1-9]：`size_increased` / `side_flipped` / `foreign_position` / `opened_offline`
> 四格的出路里删掉了「先平掉再以空仓续跑」。**
> 这四格的共同事实是：**交易所上那部分名义额里有我们没开过的**。替用户平掉它等于
> 未经授权动用他的资金——`LiveExecutor.flatten`（`live_auto/executor.py:663-704`）自己
> 承诺过"绝不触碰非自管持仓"，而续跑时本地**没有任何自管断面**可以据以只平"我们那部分"。
> 剩下的唯一出路是 `结束归档`（不平仓），并在文案里明确告诉用户：
> 「这笔持仓仍在交易所上，请到实盘面板自行处理。」

> **`ai_trade_resume_backstop_required` 的读点（v2 [P2-6]，补齐）**：在矩阵判完之后、
> 组装组级 `verdict` 之前——若该值为 `True` 且某成员 `verdict ∈ {matched, partially_closed}`
> 但其 `backstop.covered` 为假（含 `unavailable`），则该成员降为 `blocked`，
> `blockedReason="AI_TRADE_RESUME_BACKSTOP_MISSING"`。默认 `False` ⇒ 只在复核框里出黄条警示、
> 不阻断（**默认值必须与今天"裸仓只告警不阻断"的既有极性一致**，见
> `exchange_backstop.py:398-401` 的措辞）。**没有读点的配置项本仓库不允许存在。**

> `known_cl_ord_ids` 必须来自**状态表**，不能靠 `clOrdId` 前缀判断：前缀
> `aragon` / `aragonc`（`okx/trading.py:93-94`、`:192-193`）是**整个应用**共用的，
> 手动下单面板下的单也是这个前缀 —— 用前缀判会把用户自己挂的单当成"我们的遗留单"接管掉。

**`closed_offline` 的盈亏归属（本轮最容易写出假数据的一处）**：
- 能从 `fetch_position_history`（`okx/trading.py:646-669` 的归一产物，含权威 `realizedPnl`）
  取到**该 `posId`** 在 `[pausedTs, now]` 窗口内的行 ⇒ 写**一条**平仓 `trade`：
  `reason="offline_exit"`、`explain.meta.exitSource="offline"`、`ts = uTime`。

  > **v2 [P1-7]：`pnl` 绝不能直接写 `realizedPnl`——那是量纲错误，会污染真实资金的盈亏显示。**
  > `_normalize_position_history` 的 docstring 里钉着官方关系式
  > `realizedPnl = pnl + fee + fundingFee + liqPenalty`（三项已在归一时取反为"支出为正"），
  > 即 `realizedPnl` 是**已扣手续费、且含资金费与强平罚金**的口径。而本地 `trades.pnl` 的口径
  > 由该 run 的 `costBasis`（= `settings.ai_trade_net_pnl_enabled`，落在 `config_json` 里）决定，
  > 资金费另有 `funding_ledger` 台账。直接照抄会同时踩两个坑：
  > 1. **`net_mode=False`（`costBasis='gross'`）时被减两次费**：`local_net_view`
  >    （`reconcile.py:39-65`）在 `net_mode=False` 分支做的是 `pnl − Σ 腿费`，而我们写进去的
  >    `realizedPnl` 已经扣过费了；
  > 2. **资金费双记**：`realizedPnl` 里的 `fundingFee` 与 `funding_ledger` 各记一遍。
  >
  > **正确写法**（三个字段各归各位，与 `append_live_trade`（`repositories.py:278-310`）
  > 的列语义对齐）：
  > - `fee = row["fee"]`（归一后已是"正数=支出"，与本地腿费同号）；
  > - `pnl = row["realizedPnl"] + row["fee"]` 当 `costBasis == "gross"`（**把费加回去还原毛额**）；
  >   `pnl = row["realizedPnl"] − row["fundingFee"] − row["liqPenalty"]` 当 `costBasis == "net"`
  >   （**只剥掉本地不入 `trades.pnl` 的那两项**）；
  > - `explain.meta.offlineFundingUsd = row["fundingFee"]` / `offlineLiqPenaltyUsd = row["liqPenalty"]`
  >   —— 如实留痕但**不进 `pnl`**，避免与 `funding_ledger` 双记。
  >
  > **另一条：`positions-history` 一行覆盖的是整个持仓生命周期，不是"离线那一段"。**
  > 它按 **`posId` 汇总**，若暂停**之前**该持仓周期里已经发生过部分平仓并落过本地 `trades` 行，
  > 照抄整行会把那部分**重复计入**。正确做法是**只取增量**：
  > ```
  > offline_pnl = 上式算出的同口径 pnl − Σ(本地 trades 里属于同一持仓周期的平仓腿 pnl)
  > offline_fee = row["fee"]                − Σ(同上范围的平仓腿 fee 与其配对开仓腿 fee)
  > ```
  > 「同一持仓周期」的判据用**已有的** `positionSeq`：状态表 `members_json.position.positionSeq`
  > 记着暂停那一刻的序号（`_dispatch` 在任一 `side=="close"` 成交时 +1，`session.py:323-324`），
  > 本地重算时只取 `seq` 相同的那一段（§3.4.3 右列的重算已经要按 seq 分段，复用同一段代码）。
  > **若增量算出来是负的手续费或明显不自洽（`|offline_pnl| > |row.realizedPnl| * 3`），
  > 一律退回下面那条"不写任何 trade 行 + `offlineGap`"的诚实路径**——
  > 宁可缺一笔并说明，也不写一个可能重复的数。
  >
  > `posId` 在这里**只做身份判据**（P0-1 的 `foreign_position` / 取哪一行），**不做去重键**：
  > 本地开仓腿上没有 `posId`（`LiveExecutor` 产出的成交 dict 只带 `clOrdId` / `ordId`，
  > 而本轮 §3.2 明令**不改 `LiveExecutor` 任何一行**）。`posId` 的唯一来源是暂停时那次
  > `fetch_live_positions` 快照（§3.1.3 ⑤），落在 `members_json.position.posId` 里。
- 取不到 ⇒ **不写任何 trade 行**。改为把
  `metrics.aiStats.offlineGap = {"instId":…, "verdict":"closed_offline", "pnlUnknown": true, …}`
  写进该 run，并让续跑复核框与统计看板都显示「离线期间该仓已被平掉，盈亏未能从交易所回读，
  本段统计缺这一笔」。
  > 绝不写 `pnl=0.0`：`trades.pnl` 是 `Mapped[float]` 且 `append_live_trade`
  > （`repositories.py:301`）会把 `None` 兜成 `0.0` —— 那是一条"这笔交易不赚不亏"的**假数据**，
  > 会污染胜率、验收卡与已实现 edge 校准。宁可缺一笔并说明，也不编一个 0。

### 3.7 启动对账改造：`running → paused`

改 `ai_auto_repositories.reconcile_orphan_ai_trade_runs`（`:651-670`）为

```python
async def reconcile_orphan_ai_trade_runs(session, *, resume_enabled: bool = False) -> int:
    """启动期孤儿对账。`resume_enabled=False` ⇒ **逐字保持既有行为**（running→stopped）。

    True ⇒ running 的 ai-trade run 置 `paused`（不回填 end_ts、不写 metrics.reconciled），
    并把对应的 `ai_trade_group_states` 行置 `paused` + `pause_reason=…`（见下）。
    幂等：`paused` 行第二次启动不再被扫（判据是 `status == 'running'`，与 discovery 同款）。

    Returns:
        被对账的**条数**（两种模式同口径）。**v2 [P1-3]：返回类型必须保持 `int`。**
        v1 想改成 `dict` —— 那会直接打破既有用例 `tests/test_ai_auto_history.py:287-288`
        （`assert first == 1 and second == 0`）与 `db/session.py:184-186` 的 `%d` 日志，
        违反本 spec §七 开头「既有用例一条都不许改口径」。分组信息走下面那个**独立的只读函数**。
    """


async def list_paused_ai_trade_group_ids(session) -> list[str]:
    """对账后取"当前处于 paused 的 ai-trade group_id 列表"（只读、幂等、与上面那个函数解耦）。

    水合协程与历史列表排除都读它。**刻意不塞进上面那个函数的返回值**：那会把一个
    有既有用例钉着的稳定签名改掉，而这里要的只是一次多余成本近乎为零的只读查询。
    """
```

`db/session.py:173-188` 的包装按 **`settings.ai_trade_pause_enabled`（只此一个）** 决定
`resume_enabled`，`pause_reason` 按下表两分（**照抄** `discovery/manager.py:144-146` 的写法）：

| `ai_trade_pause_enabled` | `ai_trade_enabled` | 对账结果 | `pause_reason` |
|---|---|---|---|
| `False` | 任意 | **逐字回退老行为**：running → `stopped` + `end_ts` + `metrics.reconciled` | —（整轴关，状态表不参与）|
| `True` | `True` | running → `paused` | `process_restart` |
| `True` | `False` | running → `paused`（**仍然 paused，不归档**）| `disabled_by_config` |

> **v2 [P1-11]：第三行是修正过的极性。** v1 把 `resume_enabled` 定义成
> `pause_enabled and ai_trade_enabled`，于是 AI 总闸一关就直接走第一行——两个后果：
> ① §3.0 枚举里的 `disabled_by_config` **永远产生不出来**（死值 + 永不显示的文案）；
> ② 更要紧的是，**"运维关掉 AI 总闸"变成了"把一个仍持真实仓位的会话静默归档"**，
> 这正是 §1.2 第三段立论要堵的那个洞，不该由本轮新造一个入口出来。
> v2 的分工：**落不落 paused** 由整轴总闸 `ai_trade_pause_enabled` 单独决定；
> **能不能 resume** 才由 `ai_trade_enabled` 决定（卡片照出，只有「结束」可点，`resume` 403，
> 文案见 §3.0）。`ai_trade_pause_enabled=False` 仍是一键回滚到本轮之前的完整行为。

`main.py` 的 lifespan 加一句水合（**紧随 `_resume_discovery_runs`（`main.py:142`）之后，同一姿态**）：

```python
hydrator = asyncio.create_task(_hydrate_paused_ai_trade())   # 冷占位（不启动任何交易）
```

`_hydrate_paused_ai_trade()`：读状态表 → 建 `PausedGroupRef` → `adopt_paused_ref`。
**整段 try/except，失败只 warning 不阻断启动**（与 `main.py:85-104`
`_backfill_companion_strategies` 同款）。

> **v2 [P0-3]：水合失败不能只是 warning 就完了。**
> "失败只 warning" 这条仓库惯例是给**观测面**（补建伴生策略、台账回填）定的；本协程装载的是
> **互斥与容量闸的真值**，吞掉它等于把 D-9 原样放回来。v2 加两条，都很轻：
> 1. **有界重试**：3 次、退避 1s（DB 在启动瞬间被 `init_db` 占着是最常见的失败原因）。
> 2. **失败即置盲区标志**：`manager._hydration_failed = True`。它让 `status()` 回一个非空占位
>    （§3.3，`live_auto` 零改动地保持互斥），并让 AI 侧 `_require_capacity` 的那次 DB 读
>    在成功时**顺手把它清掉**。
>
> 换句话说：**水合仍然不阻断启动（惯例不破），但"我不知道"被显式表示出来，而不是被表示成"没有"。**

**关于自动续跑的极性**：discovery 侧默认**自动**续跑（`discovery_auto_resume`），
本轮**一律不自动续跑**：挖掘任务续跑烧的是 LLM 配额，AI 自动交易续跑开的是**真实仓位**。
"用户上次开着交易，所以这次开机也自动开始交易"是一个**用户从未按下的授权**。

> **v2 [P2-6]：`ai_trade_resume_auto` 这个配置项已从 §6.4 删除。**
> v1 一边写"配置项留着（运维可开）"，一边在 §九.7 把自动续跑列为非目标——**没有任何代码会读它**。
> 本仓库明令禁止"算了没落点"的死配置（`config.py` 还正逼近 800 行红线，见 §八风险 6）。
> 要么给它一条真实的读路径，要么不留；本轮选后者。将来真要做自动续跑，那是**带自己的门禁与
> 二次授权**的一整轮，不是翻一个布尔值。

### 3.8 桌面端退出前置暂停（纯优化，不承担正确性）

`desktop/src/main.js` 的 `gracefulQuit()`（`:486-504`）在 `pm.stop()` **之前**加：

```js
// AI 自动交易：退出前给后端一次"落暂停"的机会（best-effort，2s 超时）。
// 正确性不依赖它 —— 强杀 / 崩溃 / 断电由后端启动对账兜底（spec §3.7）。
await postJsonSafe(`http://127.0.0.1:${cfg.BACKEND_PORT}/api/py/ai-trade/quit-pause`, {}, 2000);
```

`postJsonSafe` 加在 `process-manager.js`（紧邻既有 `httpGetText` / `probeHttp`），
签名 `(url, body, timeoutMs) -> Promise<string|null>`，**任何失败一律 `null` + 不抛**。

后端 `POST /ai-trade/quit-pause`：按 `ai_trade_shutdown_action` 处置**运行中**分组
（**`hold`=带仓暂停，v2 默认**；`flatten`=平仓归档，可选），paused 分组只 touch 一下时间戳。
**不守 AI 总闸**（与 `/stop` 同理：收敛动作任何时候都该能执行）。

> **v2 [P0-2]：这条钩子改变了 §3.8 的性质，必须一并把默认值改掉。**
> 本节标题说它"纯优化，不承担正确性"——**对正确性而言仍然成立**（强杀 / 崩溃 / 断电由
> §3.7 的启动对账兜底）。但它对**行为**不是中性的：D-4 证明今天那条平仓路径
> （lifespan 的 `main.py:74`）几乎从不执行，而 HTTP 钩子**是能跑通的**。
> 于是 `ai_trade_shutdown_action="flatten"` 默认下，本轮的净效果是
> **"从今往后每次关软件都会平掉真实仓位并归档"** —— 用户重开后没有任何可续跑的东西，
> 需求原文那句「关闭后再次启动就得重新启动交易」**一个字都没被解决**。
> 故 v2 把默认改为 `"hold"`：关软件 = 带仓暂停 = 重开后有一张续跑卡。
> `"flatten"` 保留为运维/用户可选项（§3.9.2 的暂停确认框里那个"平仓后暂停"就是它的手动等价物）。
>
> **配套的诚实义务**（否则 `hold` 默认反而更危险）：`quit-pause` 走 `hold` 时，
> 后端必须在同一次调用里完成 §3.1.3 ⑥ 的**同步兜底核实**，并把结果写进状态表的
> `members_json[].backstopAtPause`；下次开机的续跑卡直接展示"你上次关机时，交易所侧止损是
> 58172.8 / 或：上次关机时没有任何持久化兜底"。**桌面端不等这个结果**（2s 超时照旧），
> 但后端必须把它落盘——用户有权在重开软件时知道"我睡觉那八小时到底有没有人看着这笔仓"。

### 3.9 前端：三态控件 + 两个弹窗

#### 3.9.1 状态推导（单点，放 `lib/aiTrade.ts`）

```ts
export type AiTradeLifecycle = "idle" | "running" | "paused";
/** 唯一推导点：`group.state` 优先，其次冷占位 `resumable`。任何组件都不得自己拼这个判断。 */
export function resolveAiTradeLifecycle(
  group: AiTradeGroupState | null, resumable: AiTradeResumableGroup | null,
): AiTradeLifecycle
```

#### 3.9.2 控件（`AiTradeToggle.tsx`）

| 生命周期 | 渲染 |
|---|---|
| `idle` | 既有 `Switch`（逐字不变）|
| `running` | 文案 + `Switch`(on) + **⏸ 暂停**（`variant="ghost" size="sm"`，`aria-label="暂停 AI 自动交易"`）|
| `paused` | `Badge tone="accent"` 「已暂停」 + **▶ 继续**（primary） + **结束**（ghost）；`Switch` 保持 off 且禁用（避免"关开关"与"结束"两个入口语义重叠）|

> **v2 [P2-2]**：v1 写的 `tone="warn"` **不存在**——`components/ui/index.tsx:126` 的联合类型是
> `"neutral" | "long" | "short" | "accent"`。用 `accent`（既有告知色）即可；若确要新增 `warn`，
> 必须把 `components/ui/index.tsx` 列进 §四 文件计划并配一条 UI kit 用例（它影响全仓 Badge 消费者）。

HCI 要点（需求明确要求"符合人机交互最佳实践"）：
- **可逆性**：暂停可逆、结束不可逆 ⇒ 只有「结束」与「平仓后暂停」需要二次确认。
- **风险前置**：带仓暂停时把"关机期间谁在保护这个仓位"写在**确认前**，不是回执里。
- **状态可见**：暂停期面板顶部**常驻**一条横幅（不是 toast）。**v2 [P2-9]：冷 / 热两套文案，
  措辞的可信度必须与后端此刻真的知道多少对齐**（诚实不变式 I-4）：
  - **热暂停**（会话在内存里、盯市与兜底核实照常跑）：
    「已暂停：不再开新仓；**刚刚核实**交易所侧止损 58172.8 仍挂着 · 已暂停 3 小时 12 分」。
  - **冷续跑卡**（进程刚起，尚未向交易所发过任何请求）：**绝不能**说"止损仍生效"——
    改为「已暂停 8 小时 40 分 · 关机前最后一次核实（8 小时 40 分前）交易所侧止损为 58172.8。
    **当前状态需点「继续」后向交易所核实**」。若关机前那次核实缺失或为 `unavailable`，
    这一句整体不显示，改显「关机前未能确认是否有交易所侧兜底」。
- **等待反馈**：`继续` 按钮三态（`继续` / `正在核对持仓…` / 错误原因内联展示），
  沿用 `NakedBackstopAlert`（`AiTradePanel.tsx:284-305`）已有的三态范式。
- **绝不用 `window.confirm`**：两个弹窗都用既有 `Modal`/`Dialog` 体系（`components/ui`）。

#### 3.9.3 两个新组件

- `AiTradePauseDialog.tsx`：单选（保留持仓 / 平仓后暂停）+ 逐成员持仓与兜底覆盖行
  （数据来自 `GET /ai-trade/status` 的成员 `backstop`）+ 未覆盖时的「立即补挂」
  （直连既有 `aiTradeApi.repairBackstop`，不新增端点）。
- `AiTradeResumeDialog.tsx`：离线时长 + 契约摘要 + 逐成员 verdict 行（颜色按 §3.6）+
  漂移清单 + `verdict === "needs_confirm"` 时必须勾选的确认框（**勾选态必须真的门控提交按钮**
  —— 本仓库有过"二次确认只有写点没有读点"的成文缺陷 RV-1）。
- **`AiTradeEndDialog`（v2 [P1-12] 新增，可用既有 `ConfirmDialog.tsx` 装配，不必新建文件）**：
  「结束」是**不可逆**动作，且在**带仓**时会造出一个"本地已归档、交易所上仍在"的孤儿仓 ——
  §1.2 第三段正是拿"今天已经存在孤儿持仓，只是没人告诉用户"来论证本轮的必要性，本轮
  绝不能自己再造一个入口。故：
  - **无仓**（全部成员 `flat`）⇒ 一次普通确认即可，`flatten` 恒 `false`。
  - **带仓** ⇒ 弹窗**不给默认值**，强制二选一：`平掉这些持仓并结束`（红色，破坏性）/
    `保留持仓，仅结束本地会话`（次要）。选后者时必须再勾一个
    「我知道这些持仓将不再由任何自动化管理，我会自行在实盘面板处理」——**勾选态真的门控提交**
    （同 RV-1）。弹窗内逐成员列出「仍将留在交易所上的持仓 + 它现在有没有交易所侧止损」。

#### 3.9.4 必须同步改的既有点（否则静默失效）

| 文件:行 | 改什么 |
|---|---|
| `useAiTradeGroupStream.ts:64` | `state: "running"` → `state: row.state ?? "running"`（D-7）|
| `useAiTradeGroupStream.ts:95-110` | 重发现 effect 同时消费 `res.resumable` → `setAiTradeResumable`；**冷占位绝不 `setAiTradeGroup`**。注意既有第 100 行是 `if (cancelled \|\| !res.group) return;` —— 必须**先**消费 `resumable` 再走这条早退，否则冷占位这条路径上 `setAiTradeResumable` 永远不会被调用（这正是 D-7 那类"静默失效"的同款形状）。**v2 [P2-10]**：整个 effect 的第一行 `if (accountMode !== "live") return;`（`:96`）保持不动 —— 续跑卡**只在实盘账户模式渲染**，模拟盘不该出现真实资金的续跑入口 |
| `useAiTradeGroupStream.ts:141-146` | `memberKey` 只由**有 `sessionId`** 的成员构成（冷占位无 sessionId，否则会去连 `/ws/ai-trade/undefined`）|
| `aiTradeTypes.ts:1284` | `state: "running" \| "paused" \| "stopped"` |
| `aiTradeTypes.ts:1190-1213` | `AiTradeGroupRow` 加 `state`；新增 `AiTradeResumableGroup` / `AiTradePauseResult` / `AiTradeResumePreview` |
| `store.ts:407,521,976` | 加 `aiTradeResumable` + `setAiTradeResumable`；`clearAiTradeRuntime` **同时**清它（停止/结束后不留幽灵卡）|
| `AiTradePanel.tsx:62` | `stopped` 推导不受影响，但需新增 `paused` 分支与横幅 |

### 3.10 文案单一真源

所有面向用户的**判定性**文案（verdict 说明、暂停回执 notice、兜底覆盖说明）由**后端**
`ai_auto/pause.py` 生成并随 wire 下发，前端只渲染。理由：本仓库已有两次前后端各存一份口径
导致静默漂移的事故（`edgeSource` 逐键手抄吞新键、`horizon-profiles` 的 11×5 表）。
前端只保留**装饰性**文案（按钮名、标题、单位）。

---

## 四、文件 / 模块变更计划

### 后端（`services/quant`）

| 文件 | 新建/修改 | 一句话意图 | 预算 |
|---|---|---|---|
| `app/ai_auto/pause.py` | **新建** | 纯核心：状态常量、`classify_member_drift`、超龄判定、全部判定性文案 | ≤280 行 |
| `app/ai_auto/resume.py` | **新建** | 冷续跑七步流水线 + 连续量重建（`rebuild_executor_trades` / `rebuild_continuity` / `adopt_position`）| ≤340 行 |
| `app/api/ai_trade_lifecycle.py` | **新建** | 生命周期路由（pause / resume / end / resume-preview / quit-pause）；**不放进 `api/ai_auto.py`**（它已 759 行，加进去必破 800 行红线）| ≤170 行 |
| `app/db/ai_trade_state_repositories.py` | **新建** | 状态表的 4 个仓储函数（镜像 `ai_prior_repositories.py` 的小文件先例）| ≤160 行 |
| `app/ai_auto/session.py` | 修改 | 19 处判据重定向（§3.1.2）+ `is_active` + `pause()` / `resume()` + `_build_run_metrics` 抽取 + `snapshot_run_metrics` | 净增 ≤95 行 |
| `app/ai_auto/manager.py` | 修改 | `PausedGroupRef` + 4 个新方法 + `status()` 兼容冷占位 + 容量闸新码 + `shutdown` 分派 + W1/W4 写点 | 净增 ≤150 行 |
| `app/ai_auto/exchange_backstop.py` | 修改 | 新增可等待的 `check_now`（复用 `schedule_check` 私有路径）| 净增 ≤25 行 |
| `app/ai_auto/decider.py` | 修改 | 抽 `_install_summary_seed` + 新增 `seed_from_snapshot` | 净增 ≤20 行 |
| `app/db/models.py` | 修改 | 新增 `AiTradeGroupState` 表模型（`create_all` 自动建，零 `ALTER`）| 净增 ≤35 行 |
| `app/okx/accounts.py` | 修改 | **v2 [P0-1]**：`_normalize_position`（`:154-167`）**停止丢弃** `posId` / `cTimeS` / `uTimeS` 三键（仅加键，既有键与语义逐字不变；先例 = `_normalize_fill` 对 `clOrdId`/`tradeId`/`feeCcy` 的同款处理）。**注意**：`api/trading.py:104` 那条端点有 `response_model` 派生自 `OkxAccountPosition`（`schemas.py:1694-1704`），新键会被 FastAPI **静默吞掉** —— 这不影响本轮（续跑走的是 `fetch_live_positions` 直调，不经该端点），但**必须在注释里写明**，否则后人会以为账户面板也拿得到 | 净增 ≤6 行 |
| `app/db/ai_auto_repositories.py` | 修改 | `reconcile_orphan_ai_trade_runs` 增 `resume_enabled`（**返回类型保持 `int`**，[P1-3]）；新增只读 `list_paused_ai_trade_group_ids`；`list_ai_trade_groups` 增 **`exclude_statuses: tuple[str, ...] = ()`**（D-8 + [P0-3]：历史排除改为按 **DB `status`** 判定，不再依赖内存 id）| 净增 ≤45 行 |
| `app/db/session.py` | 修改 | 对账包装按总闸传 `resume_enabled` | 净增 ≤12 行 |
| `app/main.py` | 修改 | 挂 `ai_trade_lifecycle` 路由 + `_hydrate_paused_ai_trade` 协程 | 净增 ≤25 行 |
| `app/api/ai_auto.py` | 修改 | `GET /ai-trade/status`（`:410-414`）加 `resumable` 键；history（`:544-553`）**加一个 `exclude_statuses=("paused",)` 实参**（`exclude_group_id` 那条内存派生的既有逻辑**保留不动**）| 净增 **≤10 行**（硬预算）|

> **v2 [P0-3] 的落地口径（重要，别做过头）**：历史排除**只加 `"paused"`，不加 `"running"`**。
> 加 `"running"` 会打破既有用例 `tests/test_ai_auto_history.py:307-327`（`_add_run` 默认
> `status="running"`，`test_history_endpoints` 断言 `total == 2`），违反 §七 的零改动约定。
> `"running"` 的排除继续由既有的 `exclude_group_id` 承担 —— 那条逻辑本轮不动。
| `app/schemas.py` | 修改 | 6 个新 Pydantic 模型 | 净增 ≤80 行 |
| `app/config.py` | 修改 | **7 个**新 Settings 字段（v2 [P2-6] 删掉了 `ai_trade_resume_auto`）| 净增 **≤12 行**（现 **782 行**实测，逼近 800 红线，注释压到每项一行）|

### 前端（`apps/web`）

| 文件 | 新建/修改 | 一句话意图 |
|---|---|---|
| `components/Agent/AiTradePauseDialog.tsx` | **新建** | 暂停确认（持仓归属 + 兜底覆盖 + 立即补挂）|
| `components/Agent/AiTradeResumeDialog.tsx` | **新建** | 续跑复核（离线差异 + 契约漂移 + 强制勾选）|
| `components/Agent/AiTradeEndDialog.tsx` | **新建**（v2 [P1-12]）| 结束确认：无仓一次确认；**带仓强制二选一 + 保留持仓时强制勾选**（复用 `components/ui/ConfirmDialog.tsx` 的壳）|
| `components/Agent/AiTradeToggle.tsx` | 修改 | 三态控件 + 三个 mutation（pause / resume / end）|
| `components/Chart/AiTradePanel.tsx` | 修改 | `paused` 徽标 + 常驻暂停横幅 + 成员行暂停态 |
| `components/Agent/aiTradeStats/AiTradeStatsLive.tsx` | 修改 | 暂停期口径提示（避免把"暂停"读成"AI 停摆"）|
| `lib/aiTradeApi.ts` | 修改 | `pauseGroup` / `resumeGroup` / `endGroup` / `resumePreview` |
| `lib/aiTradeTypes.ts` | 修改 | `state` 联合类型 + 4 个新接口 |
| `lib/aiTrade.ts` | 修改 | `resolveAiTradeLifecycle` 单点推导 + 暂停时长格式化 |
| `lib/useAiTradeGroupStream.ts` | 修改 | §3.9.4 的三处（`state` 不再写死 / 消费 `resumable` / `memberKey` 过滤）|
| `lib/store.ts` | 修改 | `aiTradeResumable` + setter + `clearAiTradeRuntime` 同步清 |

### 桌面（`desktop`）

| 文件 | 修改 | 意图 |
|---|---|---|
| `src/main.js` | 修改 | `gracefulQuit()` 前置 `quit-pause`（2s 超时、失败不阻塞退出）|
| `src/lib/process-manager.js` | 修改 | 新增 `postJsonSafe`（绝不抛）|

### 测试

| 文件 | 覆盖 |
|---|---|
`tests/test_ai_trade_pause_session.py` | 会话层：19 处判据、保护层仍平仓、feed_candle 只入窗不决策、时钟重置、幂等、pause 顺序（不下单）|
`tests/test_ai_trade_pause_manager.py` | 分组层：热暂停/续跑、容量闸、与 live-auto 互斥、`status()` 冷占位、shutdown 两种 action |
`tests/test_ai_trade_pause_drift.py` | `classify_member_drift` **十一个** verdict 的穷举（纯函数，无 IO；v2 新增 `foreign_position` / `opened_offline` / `unverifiable_identity` 三格）|
`tests/test_ai_trade_resume_cold.py` | 冷续跑：run 复用、连续量重建、`entry_bar` 反推、`closed_offline` 不写假 pnl、S3 fail-closed、整组回滚 |
`tests/test_ai_trade_pause_persistence.py` | 状态表 CRUD、对账 running→paused、二次启动幂等、老库自动建表、超龄、history 排除 paused |
`tests/test_ai_trade_pause_api.py` | 端点：守门极性（pause 不守/resume 守）、404/409 码、`status` 新键、路由顺序不吞既有路径 |
`apps/web/components/Agent/AiTradeToggle.test.tsx` | 扩展：三态渲染与三个 mutation |
`apps/web/components/Agent/AiTradePauseDialog.test.tsx` | 单选默认值、带仓警示、补挂按钮 |
`apps/web/components/Agent/AiTradeResumeDialog.test.tsx` | 勾选真门控提交（守 RV-1 类缺陷）、红条禁用继续 |
`apps/web/components/Agent/AiTradeEndDialog.test.tsx` | 带仓时无默认值、保留持仓需勾选才可提交（G-31）|
`apps/web/lib/aiTrade.test.ts` | `resolveAiTradeLifecycle` + `state` 不再写死 + 冷占位不建 memberKey |
`tests/test_ai_trade_pause_identity.py` | **v2 新增**：`_normalize_position` 三键停止丢弃且既有键零变化；`foreign_position` / `opened_offline` / `unverifiable_identity` 的端到端拒绝（G-26）|

---

## 五、接口设计

统一错误体 `{error:{code,message,...}}`（经 `main.py` 全局 `HTTPException` 处理器包装）。

### 5.1 `POST /api/py/ai-trade/{group_id}/pause`

**不守 AI 总闸**（收敛动作恒可用，与 `/stop` 同理）；守 `ai_trade_pause_enabled`（关 ⇒ 403）。

```jsonc
// 请求
{ "holdPosition": true, "reason": "user" }      // 两键皆可省；holdPosition 缺省取 settings 默认(true)
// 200
{
  "groupId": "aitradegrp_8f1…", "state": "paused", "pauseReason": "user",
  "pausedTs": 1723600000, "holdPosition": true, "resumable": true,
  "members": [{
    "instId": "BTC-USDT-SWAP", "sessionId": "aitrade_…", "runId": 41,
    // v2 [P0-1]：`posId` / `openCTime` 来自 ④ 之后那次 fetch_live_positions（§3.1.3 ⑤），
    // 是续跑时唯一能回答"这还是不是同一笔仓"的东西。读失败 ⇒ position 置
    // {"unavailable": true} 而不是 null（"读不到"≠"没有仓"）。
    "position": {"side":"long","size":0.6,"avgEntry":61234.5,"unrealizedPnlUsd":12.44,
                 "posId":"1899472349827…","openCTime":1723598880},
    "restingCancelled": {"outcome":"cancelled","clOrdId":"aragon18f…"},
    "backstop": {"basis":"exchange","covered":true,"coverageRatio":1.0,
                 "stopPx":58172.8,"takePx":67357.9,"mismatch":false,"note":null},
    "flattened": null
  }],
  "notice": "已暂停。BTC-USDT-SWAP 仍持有多头 0.6 张；交易所侧止损 58172.8 已核实覆盖 100%，软件关闭期间由它兜底。"
}
```
错误：`404 NOT_FOUND`（分组不存在）/ `409 AI_TRADE_NOT_RUNNING`（已是 paused）/
`403 AI_TRADE_PAUSE_DISABLED`。

### 5.2 `GET /api/py/ai-trade/{group_id}/resume-preview`

只读、**不守 AI 总闸**（先让用户看清再决定），会发私有请求（持仓 / 挂单 / 可能的持仓历史）。

```jsonc
{
  "groupId": "aitradegrp_8f1…", "pausedTs": 1723600000, "asOfTs": 1723636120,
  "offlineSeconds": 36120, "ageExceeded": false, "resumedCount": 0,
  "contract": { "bar":"15m", "instIds":["BTC-USDT-SWAP"], "totalBudgetUsd":300.0,
                "leverage":3.0, "stopLossPct":0.01, "takeProfitPct":0.03,
                "disciplinePreset":"balanced", "strategyNames":["趋势带波段"] },
  "drift": [ {"field":"discipline.preset","from":"balanced","to":"strict",
              "reason":"服务端默认预设已变更，续跑将按新预设生效"} ],
  "members": [{
    // verdict ∈ matched | flat_ok | partially_closed | closed_offline | size_increased
    //          | side_flipped | foreign_position | opened_offline | unverifiable_identity
    //          | unknown_order | unreadable          （v2 加了中间那三个，见 §3.6）
    "instId":"BTC-USDT-SWAP","runId":41,"verdict":"partially_closed",
    "expected":{"side":"long","size":0.6,"posId":"1899472349827…","openCTime":1723598880},
    "actual":{"side":"long","size":0.4,"avgPx":61234.5,
              "posId":"1899472349827…","cTimeS":1723598880},   // v2 [P0-1]：身份必须能对上
    "backstop":{"basis":"exchange","covered":true,"coverageRatio":1.0,"mismatch":false},
    "pendingOrders":[], "offlineEvents":[{"kind":"partial_exit","sz":0.2,"pnlKnown":false}],
    "message":"离线期间该仓被部分平掉（0.6 → 0.4 张）。续跑将按交易所真值 0.4 张接管；被平掉的 0.2 张盈亏未能回读，本段统计缺这一笔。"
  }],
  "verdict": "needs_confirm",          // ready | needs_confirm | blocked
  "blockedReason": null
}
```

### 5.3 `POST /api/py/ai-trade/{group_id}/resume`

**守双闸**（`_require_ai_enabled`）+ `ai_trade_pause_enabled`：它会开新仓。

```jsonc
// 请求
{ "acknowledge": true }        // v2 [P1-9]：`onDrift` 已删（原值域 abort | flatten）
// 200 = 与 POST /ai-trade/start 完全同构的 AiTradeGroupRow（含 state:"running"、resumedCount）
```

> **v2 [P1-9]：`onDrift: "flatten"` 这条出路已从接口里删除，只留 `abort` 语义（即默认行为）。**
> 它原本要覆盖的是 `size_increased` / `side_flipped`（v2 又加了 `foreign_position` /
> `opened_offline`）这四格 —— 而这四格的**共同事实**是：交易所上那部分名义额里有**我们没开过的**。
> 替用户平掉它 = 未经授权动用他的资金。`LiveExecutor.flatten`（`live_auto/executor.py:663-704`）
> 自己承诺"绝不触碰非自管持仓"，而续跑时本地**没有任何自管断面**可以据以只平"我们那部分"
> （`entry_qty_sz` 恰恰是要靠这次续跑才重建出来的）。
> 用户在这四格下的出路是 §5.4 的 `end`（可选平仓，且带仓时强制显式选择）或去实盘面板自行处理 ——
> 两条路都让**用户**做那个决定，而不是让一个刚开机、连自己开过多少仓都还没搞清的进程替他做。

错误：`409 AI_TRADE_RESUME_BLOCKED`（携逐成员 `members` 明细，格式与
`AI_TRADE_GROUP_PREFLIGHT` 一致，前端 `describeStartError`（`AiTradeToggle.tsx:259-264`）可直接复用）/
`409 AI_TRADE_RESUME_EXPIRED` / `409 AI_TRADE_RESUME_UNVERIFIABLE` /
`400 AI_TRADE_RESUME_NOT_ACKNOWLEDGED`（`verdict=needs_confirm` 却未 `acknowledge`）。

### 5.4 `POST /api/py/ai-trade/{group_id}/end`

```jsonc
// 请求（v2 [P1-12]：`flatten` 不再有默认值）
{ "flatten": false, "acknowledgeOrphanPosition": true }
// 200 → 与 stopGroup 同构（`AiTradeGroupStopResult` + "endedFrom":"paused"
//        + "orphanPositions":[{"instId":…,"side":"long","size":0.6,"backstop":{…}}]）
```

> **v2 [P1-12]：带仓时 `flatten` 必填，且"不平仓"要求显式确认。**
> v1 让 `flatten` 默认 `false`，于是"结束"在带仓时会造出一个**本地已归档、交易所上仍在**的
> 孤儿仓 —— 而 §1.2 第三段正是拿"今天已经存在孤儿持仓，只是没人告诉用户"来论证本轮的必要性。
> 本轮**绝不能自己再造一个入口**。服务端规则：
> - 该分组**无仓** ⇒ `flatten` 可省（等价 `false`），`acknowledgeOrphanPosition` 忽略；
> - 该分组**带仓** ⇒ `flatten` **必填**；若 `flatten=false` 则 `acknowledgeOrphanPosition`
>   必须为 `true`，否则 `400 AI_TRADE_END_ORPHAN_NOT_ACKNOWLEDGED`；
> - 无论哪条，回执里**必须**带 `orphanPositions`（此次结束后仍留在交易所上的持仓 + 各自的
>   兜底覆盖情况），前端据它出一条持久提示（不是 toast）。
>
> 「带仓」的判定用 §3.1.3 ⑤ 落在状态表里的那份快照；若快照 `positionUnknown`，
> 一律**按带仓处理**（保守极性，与 S3 fail-closed 同源）。

### 5.5 `POST /api/py/ai-trade/quit-pause`（桌面端专用）

无请求体；回 `{"action":"flatten"|"hold"|"noop","groupId":…|null,"paused":bool}`。

### 5.6 `GET /api/py/ai-trade/status`（既有端点，**加法**）

```jsonc
{
  "group": { "…既有全部键…", "state": "running" },      // ← 新增 state
  "aiTradeEnabled": true,
  "resumable": null      // ← 新增：**仅**冷占位（内存无会话）时非空
}
```
> 该端点 `-> dict` 且**无 `response_model`**（`api/ai_auto.py:410-414`），故新键不会被
> FastAPI 静默吞掉（本仓库已核实过这条）。**`group` 与 `resumable` 互斥**：
> 热暂停走 `group.state="paused"`，冷暂停走 `resumable`——两者同时非空即 bug（G-9 冻结）。

### 5.7 WS 事件（加法，同信封 `{type,data}`）

- `status`：`data.state` 现可为 `"paused"`；新增 `data.pauseReason`。
  （`patchAiTradeMemberStatus` 是整体 spread，新键不会被吞。）
- 新事件 `{"type":"lifecycle","data":{"action":"paused"|"resumed","reason":…,"ts":…}}`
  —— 供执行轨迹面板留一条时间线痕迹。前端未识别的 `type` 会被
  `routeMemberEvent`（`useAiTradeGroupStream.ts:202-240`）静默忽略，故老前端零影响。

---

## 六、数据模型

### 6.1 新表 `ai_trade_group_states`（`db/models.py`，`create_all` 自动建，**零 `ALTER`**）

```python
class AiTradeGroupState(Base):
    """一次 AI 自动交易分组的**可续跑契约**（ai-trade-pause-resume §3.4）。

    为什么是新表而不是给 `strategy_runs` 加列：`create_all` 不给既有表补列
    （见 db/session.py:150-155 的成文教训），加列必须写 ALTER 补丁；而新表 create_all 会建。
    为什么不复用 `config_json`：那份缺 accountId / stopLossPct / takeProfitPct /
    primingBars（spec §D-2），照它续跑会静默把用户设的止损换成服务端默认。
    """
    __tablename__ = "ai_trade_group_states"
    __table_args__ = (Index("ix_ai_trade_group_states_status", "status"),)

    id:            Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    group_id:      Mapped[str] = mapped_column(String(48), unique=True, index=True)
    status:        Mapped[str] = mapped_column(String(16), default="running")  # running|paused|ended
    account_id:    Mapped[int] = mapped_column(Integer)
    bar:           Mapped[str] = mapped_column(String(8))
    start_wire_json:    Mapped[str] = mapped_column(Text, default="{}")   # 启动请求原文（续跑唯一真源）
    resolved_echo_json: Mapped[str] = mapped_column(Text, default="{}")   # 落地回执快照（漂移对照）
    members_json:       Mapped[str] = mapped_column(Text, default="[]")   # 见 6.2
    pause_reason:  Mapped[str | None] = mapped_column(String(32), nullable=True)
    paused_ts:     Mapped[int | None] = mapped_column(Integer, nullable=True)
    resumed_count: Mapped[int] = mapped_column(Integer, default=0)
    last_seen_ts:  Mapped[int] = mapped_column(Integer, default=0)        # 心跳（离线时长分母）
    created_ts:    Mapped[int] = mapped_column(Integer, default=0)
    updated_ts:    Mapped[int] = mapped_column(Integer, default=0)
```

### 6.2 `members_json` 形状（只落不可推导的事实，§3.4.3）

```jsonc
[{
  "instId": "BTC-USDT-SWAP", "runId": 41, "sessionId": "aitrade_…",
  "allocationPct": 100.0, "budgetUsd": 300.0,
  // v2 [P0-1]：`posId` + `openCTime`（交易所 `cTime`，秒）是**持仓身份**，
  // 缺了它续跑就只能比 (side,size)，而那会把用户自己开的同向同量仓判成 matched。
  "position": { "side":"long", "size":0.6, "avgEntry":61234.5,
                "posId":"1899472349827…", "openCTime":1723598880,
                "openWallTs":1723598880, "positionSeq":3 },   // 无仓 ⇒ null
  "positionUnknown": false,      // v2：暂停时读持仓失败 ⇒ true（"读不到"≠"没有仓"，续跑判 unverifiable_identity）
  // v2 [P0-2]：关机前最后一次交易所侧兜底核实的结果（`quit-pause` 走 hold 时必须落）。
  // 冷续跑卡据它显示「关机前止损是多少 / 关机前根本没有兜底」，**并注明这是过去时**（§3.9.2）。
  "backstopAtPause": { "basis":"exchange", "covered":true, "coverageRatio":1.0,
                       "stopPx":58172.8, "checkedAtWall":1723600000 },   // 未核实 ⇒ null
  "restingClOrdId": "aragon18f…",                              // 无挂单 ⇒ null
  "knownClOrdIds": ["aragon18f…","aragonc18e…"]                // 本会话下过的单（unknown_order 判据）
}]
```
> `knownClOrdIds` 有界：只保留**当前持仓周期**内的（`_dispatch` 在 `side=="close"` 时清空），
> 避免长会话把它写成一个无界数组。

### 6.3 `strategy_runs.status` 增一个值 `"paused"`（**零迁移**：既有列是 `String(16)`）

| 状态 | 何时 | 历史列表 | 「当前」看板 |
|---|---|---|---|
| `running` | 在跑 | 排除 | 显示 |
| `paused` | 已暂停（含隐式）| **排除**（D-8）| 显示为「已暂停」|
| `stopped` | 归档 | 显示 | — |

### 6.4 新增 `Settings`（`config.py` 末尾追加，**零 DB 迁移**）

| 字段 | 默认 | 含义 |
|---|---|---|
| `ai_trade_pause_enabled` | `True` | 整轴总闸；`False` ⇒ 三个写端点 403 + 启动对账**逐字回退**老行为 |
| `ai_trade_pause_hold_position_default` | `True` | 用户暂停时"保留持仓"的默认选项 |
| `ai_trade_shutdown_action` | **`"hold"`** | 优雅退出 / `quit-pause` 对**运行中**分组的处置：`hold`（带仓暂停，**v2 默认**）\| `flatten`（平仓归档）|
| `ai_trade_resume_max_age_s` | `604800` | 超龄的暂停契约只许归档（7 天）|
| `ai_trade_resume_size_tolerance` | `0.02` | 持仓张数对账相对容差 |
| `ai_trade_resume_backstop_required` | `False` | 带仓续跑是否**强制**要求兜底已覆盖（默认只警示不阻断）。**读点在 §3.6 矩阵之后、组级 verdict 之前**（v2 [P2-6] 补齐）|
| `ai_trade_pause_persist_interval_s` | `60.0` | 心跳写点节流（W3）|

> **v2 的两处改动**：
> 1. **[P0-2] `ai_trade_shutdown_action` 默认 `"flatten"` → `"hold"`**。v1 的理由"与今天行为
>    逐字相同"在 D-4 之后不成立：今天那条路几乎不执行，而 §3.8 新加的 HTTP 钩子是执行得了的，
>    默认 `flatten` 等于**新增**「每次关软件都平真仓」并让本轮需求原地失效。
> 2. **[P2-6] 删掉 `ai_trade_resume_auto`**（原默认 `False`）。§九.7 已把自动续跑列为非目标 ⇒
>    没有任何代码会读它 ⇒ 死配置。删掉它同时给 §八风险 6（`config.py` 782 行逼近 800 红线）
>    省下一行。**本轮新增 Settings 从 8 个降为 7 个，`config.py` 净增预算 ≤12 行。**

### 6.5 内存形状增量

- `AiTradeGroup` 加 `state: str = "running"`（`manager.py:185-211` 的 dataclass 末尾，
  给默认值以保证既有直接构造它的单测逐字节零回归）。
- `AiTradeSession` 加 `pause_reason: str | None = None`、`paused_ts: int = 0`、
  `resumed_ts: int = 0`；`state` 值域扩为 `running|paused|stopped`。
- `AiTradeManager` 加 `self._paused_ref: PausedGroupRef | None = None`。

---

## 七、测试与验收判据

> 全部为**新增**用例；既有用例一条都不许改口径。若某条既有用例因本轮必须改，
> 须在「实施过程发现的方案缺陷」里逐条列出并说明理由（G-19 的既有惯例）。

| 门禁 | 判据 |
|---|---|
| **G-1** | `pause()` 后 `_monitor_task` **仍存活**，且 ≥1 次 `_monitor_tick` 内 `executor.reconcile` 被调用（D-5 的直接回归守卫）|
| **G-2** | 暂停期喂一根确认 K 线：窗口长度 +1、`_last_px_wall` 被刷新、`_atr_px` 被更新、`candle` 事件被推、**`_decision_task` 恒为 None**（判据 16/17）|
| **G-3**（冻结，**v2 [P1-2] 重写**）| 源码扫描 `session.py`，**三条子判据，都不许写死总数**：① **归类完备**——把 `self.state` 的每一处出现与「§3.1.2 表中真正含 `self.state` 的行」+「赋值/回显白名单：`:214` 初始化、`:431` `stop()`、`pause()`/`resume()` 的赋值、`:1636` `_status_payload`、`:1699` `info`」逐行配平，两边都没有的行即红（清单写进用例常量并各附一行归属）；② **禁止第三种写法**——全文不得出现 `state != "stopped"` / `state in (` 之类自造判据，只允许 `== "running"` / `!= "running"` / `is_active` / `not is_active` 四种；③ 正则**必须写 `\r?\n`**（`core.autocrlf=true`，写死 `\n` 在干净检出上恒红）。**v1 写的 `== 19` 是算术错误**：实测 `grep -c` = 16，而表里有 9 行根本不含 `self.state` |
| **G-4** | 暂停期利润保护达到触发条件 ⇒ **真的产生平仓成交**（I-1 的招牌用例）|
| **G-5** | 暂停期会话止损被触发 ⇒ 会话进入 `stopped` 且状态表置 `ended`（不再可续跑）|
| **G-6** | `pause()` 与一个持锁在飞的决策轮竞态：断言**没有任何新订单**发出，且 `_deciding` 最终为 `False`（3.1.3 的顺序正确性）|
| **G-7** | 暂停时存在未成交限价挂单 ⇒ `cancel_resting` 被调用一次，回执含 `restingCancelled` |
| **G-8** | 冷续跑复用同一 `run_id`：续跑前后 `SELECT count(*) FROM strategy_runs WHERE group_id=?` 不变（I-3）|
| **G-9**（冻结） | `GET /ai-trade/status` 的 `group` 与 `resumable` **永不同时非空**（热暂停 / 冷暂停两条路径各断言一次）|
| **G-10** | 连续量重建：给一段含 3 笔往返 + 1 笔未平开仓的 `trades`，断言 `realized_pnl_usd` / `position_seq` / `entry_fee_usd` / `len(executor.trades)` 四项与手算相等；`net_mode` 开关两态各跑一次 |
| **G-11**（冻结） | `grep -rn "\.status()" services/quant/app` 的消费者集合 == {live_auto 互斥判定, api 层}，且都只判 `is not None`（§3.3 那处语义变更的护栏）|
| **G-12**（v2 扩） | `classify_member_drift` **十一个** verdict 穷举 + 边界：`size_tol` 恰好等于容差；`expected=None & actual≠None` ⇒ **`opened_offline` 拒绝**（[P1-8]，v1 误判为 `size_increased`）；`posId` 不等 ⇒ `foreign_position` 拒绝；`posId` 两侧皆缺但 `cTimeS > paused_ts` ⇒ `foreign_position` 拒绝；`posId` 与 `cTimeS` 皆缺 ⇒ `unverifiable_identity` 拒绝 |
| **G-13**（v2 扩） | ① `closed_offline` 且 `fetch_position_history` 抛错 ⇒ **`trades` 表零新增行**，且 `metrics.aiStats.offlineGap.pnlUnknown == True`（绝不写 `pnl=0`）；② **口径用例（[P1-7]）**：给定一行 `realizedPnl=-12.0, fee=1.5, fundingFee=0.4, liqPenalty=0`，断言 `costBasis="gross"` 时落库 `pnl == -10.5 且 fee == 1.5`、`costBasis="net"` 时落库 `pnl == -12.4`，两态各跑一次 `local_net_view` 并与手算净额相等（**证明没被减两次费、资金费没被双记**）；③ **增量用例**：同一持仓周期在暂停**前**已落过一笔部分平仓 ⇒ 只写增量，且不自洽时退回 `offlineGap` |
| **G-14** | S3 取数失败 ⇒ 409 `AI_TRADE_RESUME_UNVERIFIABLE`，且**没有任何会话被创建**（fail-closed 极性）|
| **G-15** | `entry_bar` 反推：给定 `openWallTs` 与 15m 周期，断言 `bars_in_position` 与真实经过根数相等；`wallTs` 缺失时回落 `cur_index` 且回执含说明 |
| **G-16**（v2 扩） | 启动对账：`resume_enabled=True` ⇒ `running → paused` 且 `end_ts` 仍为 NULL；**第二次**启动幂等（0 改）；`False` ⇒ 逐字回退 `stopped`（老行为冻结）。**v2 [P1-3] 追加**：既有用例 `tests/test_ai_auto_history.py::test_reconcile_orphan_ai_trade_runs`（`assert first == 1 and second == 0`）**逐字不改仍通过** —— 即返回类型必须仍是 `int`。**v2 [P1-11] 追加**：`pause_enabled=True & ai_trade_enabled=False` ⇒ 仍置 `paused` 且 `pause_reason == "disabled_by_config"`（**不归档**）|
| **G-17** | 老库（无 `ai_trade_group_states` 表）跑一次 `init_db` ⇒ 表被建出、读写正常（零 `ALTER` 验证）|
| **G-18** | 存在 paused 分组时：AI 侧 `start` 409 `AI_TRADE_PAUSED_GROUP_EXISTS`；live-auto 侧 `start` 也被拒（D-9，且 `live_auto` **零改动**）|
| **G-19** | 历史列表不含 paused 分组；`GET /ai-trade/history/{gid}` 对 paused 组仍可查详情（D-8）|
| **G-20** | 超龄（`paused_ts` 早于 `now - max_age`）⇒ `resume` 409 `AI_TRADE_RESUME_EXPIRED`，`end` 仍可用 |
| **G-21** | 守门极性：`pause` / `end` / `quit-pause` 在 `ai_trade_enabled=False` 下**仍 200**；`resume` 403 |
| **G-22** | 路由顺序：新路由挂载后 `GET /ai-trade/history/profitability`、`/ai-trade/history/preset-comparison`、`GET /ai-trade/priors` 仍各回 200（既有路由不被吞）|
| **G-23** | 前端：`groupStateFromStatus({...state:"paused"})` 产出 `state === "paused"`（D-7 的直接守卫）；冷占位下 `memberKey === ""`（不建 WS）|
| **G-24** | 前端：`AiTradeResumeDialog` 在 `verdict="needs_confirm"` 且未勾选时提交按钮 `disabled`（守 RV-1 类"只有写点没有读点"缺陷）|
| **G-26**（v2 [P0-1]） | ① `_normalize_position` 加了 `posId`/`cTimeS`/`uTimeS` 后，**既有键逐字节不变**（拿一行真实 OKX 响应做黄金对照）；② 端到端：暂停时 `posId=A`，续跑前把交易所侧换成"同向、同张数、`posId=B` 且 `cTime > pausedTs`" ⇒ `resume` **409** 且 verdict `foreign_position`，且**没有任何会话被创建**；③ 同场景若只比 (side,size)（把身份判据关掉）则会判 `matched` —— 用这条**反向断言**钉死判据真的在起作用 |
| **G-27**（v2 [P0-2]） | 默认配置（`ai_trade_shutdown_action` 未设）下调 `POST /ai-trade/quit-pause`：回执 `action == "hold"`、**`flatten` 相关代码路径零调用**（mock executor 的 `flatten` 断言 0 次）、分组进 `paused`、状态表 `status='paused'` 且 `members_json[].backstopAtPause` 非空；显式设 `"flatten"` 时才走平仓归档 |
| **G-28**（v2 [P0-3]） | 水合失败（把状态表读点 mock 成抛异常，3 次重试全失败）后：① `manager.status() is not None`（live-auto 侧 `start` 被 409 拒 ⇒ **互斥不失效**）；② AI 侧 `start` 因 DB 读失败回 `409 AI_TRADE_RESUME_UNVERIFIABLE`；③ DB 恢复后再调一次 `start`，若表中确无 paused 行 ⇒ 放行且 `status()` 回 `None`（**盲区被自愈**）|
| **G-29**（v2 [P1-6]） | 暂停期触发会话止损 ⇒ `strategy_runs.status == "stopped"` **且** `ai_trade_group_states.status == "ended"`（W5 的直接守卫）；`stop_member` 停掉 paused 组的最后一个成员 ⇒ 同上，且 `self._group is None`；停掉**非**最后一个 ⇒ `self._group` 仍在、其余 paused 成员的 `_monitor_task` 仍存活（[P1-10]）|
| **G-30**（v2 [P1-5]） | `_launch_group_or_rollback` 抛错触发整组回滚 ⇒ `ai_trade_group_states` **零残留行**（W1 在启动成功后才写；若因竞态已写则 W5 清掉），下次 `start` 不被 `AI_TRADE_PAUSED_GROUP_EXISTS` 挡住 |
| **G-31**（v2 [P1-12]） | 带仓 `end` 且 `flatten` 缺省 ⇒ `400`；`flatten=false` 且未 `acknowledgeOrphanPosition` ⇒ `400 AI_TRADE_END_ORPHAN_NOT_ACKNOWLEDGED`；两者齐备 ⇒ 200 且回执 `orphanPositions` 非空。前端 `AiTradeEndDialog` 在"保留持仓"分支未勾选时提交按钮 `disabled` |
| **G-32**（v2 [P1-1]） | 暂停期喂一根**确认**根：断言 `_window.maybe_refresh_htf` / `maybe_refresh_tactical` **零调用**，而 `evaluate_signal_board` **被调用一次**且 `_last_board` / `_last_bar_index` 被刷新（插点既不能太早也不能太晚）|
| **G-25** | 全量回归：`pytest tests -q -k "ai_trade or ai_auto or ai_backtest"` 与合入前**同数通过**；前端 `npx vitest run` 同理。**（本条恒为最后一道）** |

**验收基线记录要求**：实现节点必须在 spec 里写下"改动前"与"改动后"两次的实测数字
（passed / failed / 耗时），不得只写"全部通过"。

---

## 八、风险与缓解

| # | 风险 | 影响 | 缓解 |
|---|---|---|---|
| 1 | **带仓暂停 + 关软件 ⇒ 只剩交易所侧 OCO 保护** | 极端行情下无本地退出执行者 | ① 暂停确认框**当场核实**兜底覆盖并给补挂按钮（§3.1.3 ⑥）；② **v2 [P0-2] 改**：默认 `ai_trade_shutdown_action="hold"` —— v1 用"默认平仓"当缓解措施，但那会**废掉本轮需求本身**（关机即平仓、重开无卡可续），且 D-4 已证明"与今天一致"不成立。真正的缓解是**把风险显式化而不是消灭功能**：`hold` 时强制落 `backstopAtPause`，未覆盖则暂停确认框用红条 + 「立即补挂」拦一道，用户仍可当场改选"平仓后暂停"；③ 未覆盖时文案明说"此刻没有任何持久化兜底"（复用 `exchange_backstop.py:398-401` 的既有措辞）；④ 想回到"关机即平仓"的用户/运维把 `ai_trade_shutdown_action` 设成 `"flatten"` 即可，一个字段回退 |
| 2 | **接管一个不是自己的仓位** | 真实资金被错误管理 | §3.6 判定矩阵：方向不符 / 变大 / 读不到 ⇒ 一律拒绝；`knownClOrdIds` 用状态表而非前缀。**v2 [P0-1] 补齐**：v1 的矩阵只比 `(side, size)`，对"我们的仓被平掉、用户随后自己开了同向同量的一手"这条路径**完全无能为力**（判成 `matched` 并静默接管）。v2 加**持仓身份**（`posId` / `cTimeS`，S3' 从 `_normalize_position` 停止丢弃）与三个新 verdict（`foreign_position` / `opened_offline` / `unverifiable_identity`），G-26 用一条反向断言钉死判据真的在起作用 |
| 3 | **续跑后风控参数失真**（D-2）| 用户以为止损 1% 实际 5% | 续跑真源是 `start_wire` 原文，并在复核框里逐字段展示 `contract`；`drift` 清单显式列出被服务端改写的项 |
| 4 | **`status()` 语义变更影响 live-auto** | 互斥误判 | G-11 冻结消费者集合；`live_auto` 零改动（它只判 `is not None`）|
| 5 | **19 处判据改漏一处** | 暂停期某层风控静默停摆 | G-3 源码计数冻结 + G-1/G-4/G-5 三条行为用例正面覆盖 |
| 6 | **`config.py` 逼近 800 行红线**（实测 **782**）| 破 CLAUDE.md 阈值 | **7** 个字段（v2 删了 `ai_trade_resume_auto`）注释压到每项一行（≤12 行预算 ⇒ 794 行，仍在线内）；若仍越界，在「实施过程发现的方案缺陷」里如实记为欠账，**不在本轮做跨特性拆分** |
| 7 | **`schemas.py` 已 2459 行（存量债）** | 继续恶化 | 本轮仍按仓库惯例把新模型放它（AI-trade 全部 schema 都在那），并在欠账里记一笔；拆分不属本轮范围 |
| 8 | **桌面 `quit-pause` 阻塞退出** | 用户点关闭后卡住 | 2s 硬超时 + `postJsonSafe` 绝不抛；正确性由启动对账兜底，此路径纯优化 |
| 9 | **暂停期 WS / REST 仍在跑，长期挂机耗配额** | 无谓的 OKX 配额消耗 | 判据 12 关掉战术窗口刷新；`ai_trade_backstop_poll_ttl_s`（默认 120s）与 5s 盯市 tick 本就有节流；不再新增轮询 |
| 10 | **`resume` 与 `start` 并发** | 双分组 | 二者共用 `get_admission_lock()` + `_start_lock`（`manager.py:264`）；`resume_group` 必须进**同一对**锁 |
| 11 | **状态表与 run 状态不一致**（一处写成功一处失败）| 幽灵可续跑卡 | 以**状态表**为准（它是本特性的真源），run 状态是展示派生；启动水合时若发现 run 已 `stopped` 而状态表仍 `paused` ⇒ 状态表置 `ended` 并 warning |
| 12 | **暂停被误读为"AI 坏了"** | 用户困惑 | 常驻横幅 + `AiTradeStatsLive` 的口径提示 + `pauseReason` 分**四**种文案（§3.0，v2 删了 `stale_feed_pause` 那个死值）|
| **13**（v2 [P2-7]）| **暂停期 `_check_staleness` 仍活，`live_auto_flatten_on_stale=True` 时断流会平掉"保留持仓的暂停"** | 用户以为自己做了个降低风险的动作，结果仓被平了 | 该值 `config.py:118` **默认 `False`**，故默认不发生；开启时 ① 暂停回执 `notice` 与常驻横幅必须明写这一条件、② G-5 的用例同时覆盖它。**刻意不为它加新开关**：`_check_staleness` 属于 I-1 的"减风险侧"，为暂停单独豁免它会开一个"暂停期风控半开"的口子，比现状更坏 |
| **14**（v2 [P1-4]）| **实现节点"顺手"在 `LiveExecutor` 里加一个 paused 判据** | 同一改动会**同时**改掉 `live_auto`（策略自动交易）的真实资金执行器 | `LiveExecutor` 在 `live_auto/executor.py`，被两条链路**共用**（连 settings 对象都共用，`api/ai_auto.py:94-95`）。§3.2 已把"零改动"写成结构性前提；§四 的文件计划里**没有** `live_auto/executor.py` 这一行，出现即视为越界 |

---

## 九、非目标（本轮明确不做）

1. **不做多分组并行**：仍是"单活跃 AI 分组"（含 paused 占位），I-2 不放宽。
2. **不做成员级暂停**：只做整组暂停。理由：成员级暂停会让"分组总预算切分"与
   `GroupExposureBook`（`manager.py:494-496`）的 `unit_notional` 口径分叉，收益远小于代价。
   （成员级**停止**是既有能力，保留不变。）
3. **不做暂停期的策略影子状态持久化**：冷续跑会重新暖机（`_prime`，`session.py:403-425`），
   策略内部状态从暖机窗口重建。这是可接受的：暖机不下任何单，且影子立场本就由窗口决定。
4. **不给 `live_auto`（策略自动交易）加暂停**：那是另一轮的事。
   > **v2 [P1-4] 更正 v1 的错误陈述。** v1 写的是"它不共用本文任何代码路径"——**这是假的**：
   > `ai_auto/session.py:24` 就是 `from ..live_auto.executor import LiveExecutor`，
   > 另外还共用 `live_auto/explain.py` 与 `live_auto/resting.py`，`api/ai_auto.py:94-95`
   > 更明文写着两侧**共用同一个 settings 对象**。
   > 真正成立且更强的命题是：**本轮对 `LiveExecutor` 零改动**（§3.2），
   > `live_auto` 自己的 session/manager/API 也零改动，因此 `live_auto` 零影响。
   > 这条更正不是文字游戏：照 v1 那句话去理解，实现节点会以为"在 executor 里加个 paused 判据
   > 只影响 AI 侧"，而那一改会**同时**改掉策略自动交易的真实资金执行器（§八风险 14）。
5. **不改 AI 回测**：回测 job 无真实资金、有自己的 `AiBacktestManager`。
6. **不改 `stop()` 的动作顺序**：pause 用了更安全的"先置态、再排空锁、后 cancel"，
   而 `stop()` 是"先 cancel、后在锁内 flatten"。把 stop 也改成新顺序**属于本轮之外的
   行为变更**，记为欠账（§十.6）。
7. **不做自动续跑**：冷启后**一律**要人点一下（理由见 §3.7）。
   v2 [P2-6] 顺带**删掉了 `ai_trade_resume_auto` 这个配置项** —— 既然本轮不做，留一个没有读点的
   布尔值就是死配置。将来要做，那是带自己的门禁与二次授权的一整轮。
8. **不做"暂停期间 AI 继续分析但不下单"的观察模式**：那是另一个特性（且会持续烧 LLM 配额）。

---

## 十、给下游节点的实施注意

1. **改动顺序建议（v2 已按新增门禁重排）**：
   `okx/accounts.py` 三键停止丢弃（G-26 ①，**最小、最独立，先做**）
   → `pause.py` 纯核心（G-12，含三个新 verdict）
   → `models.py` + 状态仓储（G-17）
   → `session.py` 判据表 + `pause()`/`resume()`（G-1~G-7 / G-32）
   → `manager.py`（G-18 / G-28 / G-29 / G-30）
   → `resume.py`（G-8 / G-10 / G-13~G-15 / G-26 ②③）
   → 路由（G-21 / G-22 / G-31）
   → 对账（G-16 / G-19）
   → 前端（G-23 / G-24 / G-31 前端半）
   → 桌面 + `quit-pause`（G-27）。
   **纯核心与 DB 层先绿，能让后面每一步都有一个可回滚的绿点。**
   > 把 `okx/accounts.py` 提到第一步是有意的：它是**唯一一处会被非 AI 链路读到**的改动
   > （账户面板也走 `_normalize_position`），越早跑绿越早排除掉"是不是这一改把别处弄坏了"。
2. **测试执行的三条环境事实**（本仓库成文教训，别重踩）：
   - `services/quant` 的用例**共用一个临时 DB**，全量 `pytest` **不能并发**跑；
   - 长跑请用 `python -u -m pytest …`（否则输出被缓冲，看不到进度会被误判为挂死）；
   - 前端组件用例必须 `NODE_ENV=test`（否则 React `act()` 报错）。
3. **干净 worktree 验证时先 `cp services/quant/.env`**（否则 Settings 取不到而整批红）。
4. **源码扫描类冻结用例（G-3/G-11）的正则必须写 `\r?\n`**：`core.autocrlf=true`，
   写死 `\n` 在干净检出上恒红。
5. **精确暂存**：提交用 `git add <具体路径>`，**严禁 `git add -A`**——工作树里常驻大量
   与本轮无关的未跟踪文件（`.agentmesh/`、`docs/diagnoses/*`、`.claude-index/` 等）。
   特别注意**不要**把 `services/quant/app/config.py` 里与本轮无关的默认值翻转
   （历史上 `ai_trade_enabled` 的脏改被误提交过一次）。
6. **本轮如实记下的欠账**（不得用凑数字代替；v2 补了两项实测行数）：
   - `stop()` 的 cancel/lock 顺序不如 pause 安全（§九.6）；
   - **存量超 800 行红线的文件（v2 [P2-12] 实测补全）**：`session.py` **1867**、
     `manager.py` **962**、`schemas.py` **2459**、`aiTradeTypes.ts` **1438**、
     `store.ts` **1350**、`aiTrade.ts` **1098**。本轮还要往前两个各加 ≤95 / ≤150 行。
     v1 只记了后三个前端文件，**漏了 `session.py` 与 `manager.py` 这两个本轮改得最重的**。
     存量债，本轮不拆，但必须记全——记漏了下一轮就会以为"是这一轮弄出来的"。
   - `closed_offline` 且回读失败时该笔盈亏永久缺失（只标注，不补数）；
   - `closed_offline` 的增量口径依赖 `positionSeq` 分段，若暂停前的本地 `trades` 因
     `_persist_trade` 的 best-effort 落库失败而缺行，增量会偏大 ⇒ 走"不自洽即退回 `offlineGap`"
     的兜底（G-13 ③），但那一笔仍然缺失；
   - 冷续跑丢失暂停前的 `_traces` / `_equity` / `_decisions` 环形样本（内存态，仅
     `metrics.equityDigest` / `decisionsDigest` 在暂停时被快照下来，面板会出现一段空白）；
   - **v2 新增**：`_normalize_position` 加的三键在 `api/trading.py:104` 那条端点上会被
     `OkxAccountPosition`（`schemas.py:1694-1704`）的 `response_model` 静默吞掉。
     本轮不动那个 schema（账户面板不需要这三键），但注释里要写明，免得后人误判为 bug。
7. **v1 §十.7「请评审节点重点质疑」的四问，评审节点的实测答复**：
   - ① **§3.1.2 那张表有漏项**：`feed_candle` 确认根路径上的 `maybe_refresh_htf`（`:691`）与
     `maybe_refresh_tactical`（`:693`）漏了（[P1-1]，已补 row 12b）。另外 row 13 的位置记错了
     （判据在 `_preview_ready :881`，不在 `:535`），**但结论正确**；且 G-3 的 `== 19` 是算术错误
     （实测 `grep -c` = **16**，[P1-2]）。表的其余 17 行经逐行复核**归类正确**。
   - ② **§3.6 的八个 verdict 不穷尽，缺三格**：`foreign_position`（[P0-1]，最危险的一格）、
     `opened_offline`（[P1-8]，正是那个问句里的格子 —— v1 把它塞进 `size_increased`，
     而那一格的判据与文案在 `expected=None` 时**都不成立**）、`unverifiable_identity`。
   - ③ **D-4 的 `taskkill` 结论无需实测即可确认成立**：`process-manager.js:365-376` 的
     `taskkill /PID /T`（无 `/F`）对一个 `windowsHide`、非 `detached`、无消息循环的 Python
     子进程只会发 WM_CLOSE，Windows **不会**把它转成 Python 可捕获的信号；8s 后 `_forceKill()`
     用的又是 `/F /T`。v1 那句"即便实测发现它其实能优雅退出，本设计也不改"**予以保留**——
     正确性不该建立在平台相关的信号语义上。实测仍建议做，但它是**记录性**的，不是阻断性的。
   - ④ **是的，`status()` 只有一个消费者**：全仓 `grep -rn "\.status()" services/quant/app`
     只有 `live_auto/manager.py:469` 一处真实调用（其余两处命中的是 `manager.py:16` / `:669`
     的注释），且只判 `is not None`。G-11 有效。**但 v2 又给它加了一条约束**：
     `status()` 必须保持**同步**（调用方在同步函数里），所以它绝不能查 DB ——
     这正是 [P0-3] 要用 `_hydration_failed` 内存标志而不是"查一次表"的原因。

---

## 附录 A：两条关键时序

### A.1 带仓暂停 → 关软件 → 重开 → 继续

```
用户点暂停
  │ POST /ai-trade/{gid}/pause {holdPosition:true}
  ├─ session.pause(): 置 paused → 排空 _exec_lock → cancel 决策任务
  │                   → cancel_resting → fetch_live_positions（取 posId/cTime，v2 [P0-1]）
  │                   → backstop.force_next() + check_now(await)   （v2 [P2-4]）
  │                   → snapshot_run_metrics(status="paused")
  ├─ manager: group.state="paused"（**不清 _group**）
  └─ W4: ai_trade_group_states ← status=paused, pause_reason=user, paused_ts,
                                 members[].position.posId/openCTime, backstopAtPause

（此后 monitor 仍每 5s 跑：reconcile / 保护层 / 兜底核实 / 会话止盈损 —— 不变式 I-1）

用户关软件
  ├─ before-quit → gracefulQuit()
  ├─ POST /ai-trade/quit-pause  → 已 paused ⇒ 只 touch last_seen_ts（noop）
  └─ pm.stop() → 8s 宽限 → 强杀（后端 lifespan 大概率没跑，无所谓：状态早已落盘）

用户重开软件（已联网）
  ├─ init_db → 对账：本组 run 已是 paused ⇒ 幂等 0 改
  ├─ lifespan → _hydrate_paused_ai_trade() → manager.adopt_paused_ref(ref)
  ├─ 前端 GET /ai-trade/status → {group:null, resumable:{…}} → 渲染续跑卡
  ├─ 用户点「继续」→ GET /ai-trade/{gid}/resume-preview
  │     └─ S3 读交易所：持仓 0.6 张多头仍在 **且 posId 与暂停时那一笔相同**
  │        ⇒ verdict=matched ⇒ "ready"
  │        （posId 不同 / cTime 晚于 pausedTs ⇒ foreign_position ⇒ 拒绝，v2 [P0-1]）
  └─ 用户确认 → POST /ai-trade/{gid}/resume
        └─ S2 重解析 → S5 建会话（复用 run 41）→ S6 接管 → S7 start()
           → 状态表 status=running, resumed_count=1 → run.status=running
```

### A.2 离线期间被交易所侧止损平掉

```
resume-preview: expected{long,0.6} vs actual{None}
  ⇒ verdict=closed_offline
  ⇒ 试 fetch_position_history([pausedTs, now])，按 posId 取那一行
        命中 ⇒ 写一条 close trade（reason="offline_exit", explain.meta.exitSource="offline"）
               **v2 [P1-7] 的口径换算（绝不直接写 realizedPnl）**：
                 fee = row.fee
                 pnl = row.realizedPnl + row.fee                      （costBasis="gross"）
                 pnl = row.realizedPnl − row.fundingFee − row.liqPenalty（costBasis="net"）
                 再减去同一 positionSeq 段内本地已落的平仓腿（只取增量）
                 meta.offlineFundingUsd / offlineLiqPenaltyUsd 如实留痕，但不进 pnl
        未命中 / 增量不自洽 ⇒ 不写任何行 + metrics.aiStats.offlineGap.pnlUnknown=true
  ⇒ 复核框黄条 + 必须勾选
  ⇒ 续跑后以**空仓**运行（Agent 从下一根开始正常决策）
```

---

## 实施过程发现的方案缺陷

> 实现节点（Subtask #2）逐条追加。格式：`编号（级别）现象 → 根因 → 闭合位置`。

### 验收基线（§七 要求：写下「改动前 / 改动后」两次实测数字，不得只写「全部通过」）

> 「改动前」取的是 **`HEAD = 6d9a72f` 的干净 worktree**（`git worktree add`，并按 §十.3
> 先 `cp services/quant/.env`），**不是**在制工作树上的读数 —— 后者在跑的过程中会被在制编辑
> 影响，得出的不是基线（第一次尝试就因此拿到 3 个假红，已废弃）。

| 套件 | 命令 | 改动前（干净 HEAD） | 改动后 |
|---|---|---|---|
| 后端 | `python -u -m pytest tests -q -k "ai_trade or ai_auto or ai_backtest"` | **1642 passed, 1 skipped, 0 failed**, 338.37s | **1741 passed, 0 failed**, 456.39s |
| 前端 | `NODE_ENV=test npx vitest run`（全量） | **157 files / 1754 passed, 0 failed**, 47.01s | **162 files / 1793 passed, 0 failed**, 55.6s |

**终审（Subtask #3）修完 RV-1~RV-4 之后的复测**（同命令、同工作树）：

| 套件 | 结果 |
|---|---|
| 后端 | **1745 passed, 0 failed**, 401s（较实现轮 +4 = 终审新增的 4 条用例）|
| 前端 | **162 files / 1796 passed, 0 failed**, 61.5s（较实现轮 +3 = 终审新增的 3 条用例）|
| 前端（同命令再跑一次）| **1794 passed / 2 failed** —— 两条**全部**落在 `AiTradeLaunchModal.test.tsx`，即实现轮已如实记下的那条**负载敏感既存抖动**；该文件本轮**一行未改**，单独连跑三次 **74/74 全绿 ×3**。不据此宣称全绿，也不当作本轮引入 |
| 类型 | `npx tsc --noEmit` 仅剩 `WeightBatchArena.test.tsx` 一条**既有**错误（属另一特性的在制文件，本轮零关系）|

> 终审环境记一笔（下一轮别再排查一遍）：本工作树的 `apps/web/node_modules` 一度**残缺**
> （`@alloc/quick-lru`、`@asamuzakjp/css-color` 等多个包的 pnpm store 目录悬空），症状是 vitest
> 起不来而不是用例红。修法：`pnpm install --ignore-workspace --frozen-lockfile --force`
> —— 必须带 `--ignore-workspace`，因为仓库根有 `pnpm-workspace.yaml` 但**没有**根 lockfile
> （真正的 lockfile 在 `apps/web/pnpm-lock.yaml`），不带它会直接 `ERR_PNPM_NO_LOCKFILE`。

前端净增 **5 个用例文件 / +39 条**（`AiTradePauseDialog` 6 · `AiTradeResumeDialog` 7 ·
`AiTradeEndDialog` 6 · `AiTradeResumableCard` 6 · `useAiTradeGroupStream.pause` 3 ·
`AiTradeToggle` +5 · `aiTrade` +6）。
后端净增 7 个用例文件（`pause_session` / `pause_drift` / `pause_manager` / `pause_api` /
`pause_persistence` / `pause_identity` / `resume_cold`）。

**全量后端**（`pytest tests -q`，不带 `-k`，因为本轮改了 `conftest.py` 的 autouse 夹具、影响每个
用例）：**3611 passed, 7 failed, 1 skipped**, 1006s。7 条红全部在
`tests/test_factor_library_expansion.py` —— 一个**未跟踪的、属于另一个特性的在制用例文件**
（它期望 `FACTOR_LIBRARY` 里有 `mom_tsmom_vol` 等因子，而库里根本没有 ⇒ `ValueError: 未知因子`）。
与本轮零关系，本轮也不动它。

> **前端有一条既存的、负载敏感的抖动用例**（不是本轮引入）：
> `AiTradeLaunchModal.test.tsx::keeps user edits while open when the parent re-renders with new
> default refs (guards inputs-revert recurrence)`。全量并发跑时偶发 `onConfirm` 未被调用而红，
> **单文件连跑三次全绿**（干净 HEAD 基线上单跑亦绿，未在基线上复现过抖动）。该文件本轮
> **一行未改**（见 `git status`），
> 且它守的正是仓库里那条仍处 open 的「弹窗输入约 1 秒回退」复发缺陷。如实记在此处，不当作已解决，
> 也不据此宣称本轮全绿 —— 全量前端应以「162 files / 1793 passed + 这一条已知抖动」理解。

### 阻断级（不闭合即功能空转 / 行为错误）

**I-1 `backstop.covered` 这个键在真实 wire 里根本不存在。**
spec §5.1 的回执样例、§6.2 的 `backstopAtPause` 形状、§3.6 的 `apply_backstop_requirement` 判据
都写着 `backstop.covered`，而 `BackstopRuntime.wire()` 的真实产物（`exchange_backstop.classify_backstop`，
`:120-167`）只有 `basis` / `coverageRatio` / `hasStop` / `coveredSz` / `mismatch` —— **没有 `covered`**。
照字面实现的后果是两个方向都错且**都不报错**：`ai_trade_resume_backstop_required=True` 时那道闸
恒放行（`covered` 恒 falsy ⇒ 永远走不到降级分支的反面），而暂停回执恒说「此刻没有任何持久化兜底」
——一句在兜底其实好好挂着时也照发的安全告警，比不发更坏。
**闭合**：新增纯函数 `pause.is_backstop_covered(wire, min_coverage)`（判据 = `basis == "exchange"`
且 `coverageRatio >= min_coverage`，与 `discipline_view.naked_backstop` **同源**，复用既有
`ai_trade_backstop_min_coverage`，**不新开旋钮**），并在 §3.1.3 ⑥ 的落盘点（`session._pause_backstop`）
**单点派生** `covered` / `stopPx` / `takePx` / `checkedAtWall` 写进 `backstopAtPause`。前端只渲染，
绝不自己再判一次（§3.10）。用例：`test_ai_trade_pause_drift.py::test_backstop_requirement_has_a_real_read_point`。

**I-2 桌面端 `quit-pause` 的 URL 不能带 `/api/py` 前缀。**
§3.8 写的是 `http://127.0.0.1:${cfg.BACKEND_PORT}/api/py/ai-trade/quit-pause`，但 `/api/py/*` 是
**Next 的代理前缀**（`apps/web/next.config.mjs:29` 把它 rewrite 到后端的 `/api/*`）；桌面端是
**直连后端端口**的，那条路径在 FastAPI 上不存在。而 `postJsonSafe` 按设计 fail-safe ⇒ 404 被静默
吞掉 ⇒ **这个钩子会永远不生效且没有任何症状**（正确性仍由启动对账兜底，所以连告警都不会有）。
**闭合**：改为 `http://${cfg.BACKEND_HOST}:${cfg.BACKEND_PORT}/api/ai-trade/quit-pause`，并在
`main.js` 的调用点注释里写明这条差异。

**I-3 续跑失败回滚时必须先摘掉 W5 回调。**
§3.5 S7 要求「起会话失败 ⇒ 整组回滚并把状态表**留在 paused**（可再试）」，而 §3.4.2 的 W5 挂在
`session.stop()` 的收尾上、语义是「会话自终止 ⇒ 置 `ended`」。回滚路径正好会调 `stop()` ⇒ 第一次
续跑失败就会把用户**仍持真实仓位**的可续跑契约永久归档。
**闭合**：`resume.resume_cold_group` 的回滚循环里先 `member.session.on_stopped = None` 再 `stop()`
（这些会话根本没起来，交易所上不可能有它们开出的仓位，没有任何东西需要被收敛）。
用例：`test_ai_trade_resume_cold.py::test_cold_resume_rolls_the_whole_group_back_when_a_session_fails_to_start`。

### 记录级（口径 / 预算 / 既有用例）

**I-4 `closed_offline` 的增量分段判据从 `positionSeq` 改为交易所 `cTime`。**
§3.6 要求用 `members_json.position.positionSeq` 划「同一持仓周期」，但 `_dispatch` 在**任一**
`side == "close"` 成交时就 `+1`（含**部分**平仓，`session.py` 的 P0-1 注释自己写明了这一点），
故它与一个 `posId` 的生命周期**根本不对齐**：一次部分平仓就会把同一个 posId 劈成两段，增量算出来
偏小。改用 `position.openCTime`（交易所对该 posId 的权威建仓时刻）划段；spec 要求的「不自洽即退回
`offlineGap`」兜底一字未改。用例：`test_ai_trade_resume_cold.py` 的三条 G-13。

**I-5 `classify_member_drift` 改为收一个聚合入参 `DriftInput`。**
spec 给的是 6 个关键字形参，超过 CLAUDE.md §二 的 5 个上限；本仓库已有同款先例
（`SessionReadings` / `PreviewRequest` / `AiTradeGroupStartRequest`）。判据与 11 个 verdict 的
值域**逐字未变**，只是把 spec 里以文字描述的两种「读不到」显式化为 `readable` / `position_unknown`
两个入参（前者=续跑时取数失败，后者=**暂停那一刻**就没读到 —— 两个时刻的同一条极性）。

**I-6 张数容差比较加 `+1e-9`。**
`0.6 × 1.02` 在二进制下是 `0.6120000000000001`，裸比会让一个「恰好等于 2% 容差」的断面随机翻成
`size_increased` 并**拒绝续跑**。用例：`test_size_tolerance_boundary_is_inclusive`。

**I-7 `api/ai_auto.py` 的 ≤10 行硬预算被突破（实测净增 +24 行，文件 783 行）。**
根因：§3.5 S2 要求「用 `start_wire` **重跑一遍** `/ai-trade/start` 的解析链」，而那条链原本整段
内联在 `ai_trade_start` 的函数体里。抽成 `build_start_request` 是**唯一**能复用它而不复制 25 行
装配的做法 —— 复制即漂移，且漂移的正是止损百分比与账户凭证（D-2 点名的那两个数）。预算的**目的**
（把该文件挡在 800 行红线内）达成：783 < 800。

**I-8 `manager.py` / `resume.py` 的行数预算被大幅突破。**
实测 `manager.py` 净增 **+457/-19**（现 **1400** 行，预算 ≤150）、`resume.py` **477** 行（预算 ≤340）、
`session.py` 净增 **+369/-26**（现 **2210** 行，预算 ≤95）。原因是四个新方法 + 冷占位 + 状态表五个
写点 + `shutdown` 分派 + `closed_offline` 的三段口径换算，按本仓库的注释密度写下来就是这个量。
**本轮不做跨特性拆分**（与 §十.6 同口径），如实记为欠账：`session.py` / `manager.py` / `schemas.py`
（2494）三个文件本就远超 800 行红线，本轮把它们又推高了一截。

**I-9 `schemas.py` 只加 3 个请求模型，不是 6 个。**
三个生命周期端点一律 `-> dict` 且**不设 `response_model`**：本轮自己就撞上过
`OkxAccountPosition` 把 `posId` 静默吞掉（§四 已记），而这三个回执（逐成员 verdict / 兜底覆盖 /
`orphanPositions`）正是用户拿着真钱做决定的唯一依据，少一个键就是少一条事实。理由写在
`schemas.py` 那一组模型的段首注释里。

**I-10 `AiTradeSession.stop()` 新增 `flatten: bool = True` 关键字形参。**
§3.3 的 `end_group` 要求「保留持仓、仅结束本地会话」，而既有 `stop()` **无条件**平仓。默认值保证
既有全部调用点逐字节不变；唯一传 `False` 的调用方是 `end_group` 那条用户已显式勾选确认的分支。

**I-11 五处既有用例被改动，全部是「加法」，判定口径一字未改。**
按 §七 开头与放行条件 4 的要求逐条列出：
- `tests/test_ai_trade_sizing_stats.py`：`merge_into` 落点断言从 `_finalize_run` 改为
  `_build_run_metrics`（metrics 构造被抽出以供暂停快照复用）。**断言的内容一字未改** —— 它守的
  仍是「合并发生在调用点、不在 `_compute_ai_stats` 内部」。
- `tests/test_ai_trade_group.py` / `test_ai_trade_api.py` / `test_auto_trade_admission.py` 的
  `_FakeSession`：补上 manager 新读到的 `is_active` / `state_member_row()` / `pause()` /
  `resume()` / `stop(flatten=)`。这正是那三个文件里 `discipline_wire` 的 docstring 已经写明的
  既有纪律（「真实 `AiTradeSession` 新增的**被 manager 读到的**方法，测试替身必须同步跟上」）。
- `tests/conftest.py`：新增一条 autouse 夹具，逐用例清扫 `ai_trade_group_states` 与 manager 的
  冷占位。**这不是为了让新用例通过**：本轮之后 `init_db()` 的启动对账会把上一个用例残留的
  `running` run 置成 `paused` 并把契约行一并置 `paused`，于是下一个用例的 `start` 会被容量闸
  **正确地**挡下来 —— 生产上完全正确，只是把既有用例之间早已存在的状态泄漏暴露了出来。
  姿态与既有的 `_reset_okx_link_singletons` 逐字同款。
- `tests/test_ai_auto_history.py` **逐字未改**（放行条件 4 的硬约束），其
  `test_reconcile_orphan_ai_trade_runs` 与 `test_history_endpoints` 原样通过。

**I-12 `Switch` 不加 `disabled` 形参。**
`components/ui/index.tsx` 不在 §四 的文件计划里，且它是全仓共用的 UI kit（改它要配一条 kit 用例，
同 [P2-2] 对 `Badge tone="warn"` 的判断）。暂停态下改为 `opacity-40` + 在 `AiTradeToggle` 里吃掉
点击（`noop`），语义仍是「暂停期开关不可用」。

**I-13 `resolveAiTradeGate` 的 `hasActiveSession` 必须把 `paused` 也算进来。**
不算的话，暂停期启动弹窗看起来可用，用户填完一整屏参数才在提交时吃一个
`AI_TRADE_PAUSED_GROUP_EXISTS` 409。

**I-14 D-4 的 `taskkill` 实测：本轮未做，按评审 §复核 3 的结论处理。**
评审节点已把它降为**记录性**而非阻断性（「即便实测发现它其实能优雅退出，本设计也不改」）。
本轮的持久化在**运行期**就写好（W1/W2/W3），使「断电」与「点暂停」在磁盘上无法区分，故正确性
不依赖那条信号语义；`quit-pause` 只是把「关软件」这一种情况做得更准（落 `backstopAtPause`）。

## 终审逮到的缺陷

> Subtask #3「代码 Review」节点按既有惯例逐条追加。方法：拿 §四 的文件计划与 §七 的 32 条门禁
> 逐条对表，再对每一条**面向用户的判定性输出**问一句「它说的话在最坏情况下还成立吗」。
> **四条缺陷全部落在同一个形状上：wire 里有事实，用户那里没有。** 三条是「说了一句在失败路径上
> 不成立的话」，一条是「后端算了一句话但前端没有读点」——正是本仓库反复踩的那两个坑。
> 全部已在源码就地修复并补了用例（下方每条附用例名）。

**RV-1（阻断级 · 真实资金）冷路径的「平掉这些持仓并结束」从不平仓，却回执「已平仓、无孤儿持仓」。**
`AiTradeManager.end_group` 的冷分支（进程里没有会话对象，即用户关过软件之后那条路）只调
`_archive_cold_runs` 落 run 终态，**根本没有任何平仓动作**；而回执照样带 `"flattened": true`、
`"orphanPositions": []`。前端 `AiTradeEndDialog` 又对冷热两条路径渲染**同一个**二选一单选框。
合起来就是：用户在续跑卡上点下那个红色的「平掉这些持仓并结束」，交易所上的仓位原样留着，
UI 告诉他已经平了、没有孤儿仓 —— 正是 §1.2 第三段拿来论证本轮必要性的那个洞，本轮差点自己
再造一个入口。冷态确实**做不到**平仓（没有 Executor、没有暖机窗口、没有装配好的凭证），所以
正确的处置不是补一个平仓实现，而是**当场拒绝**并把两条真实可走的路说清楚。
**闭合**：① 服务端 `_require_cold_end_holdable` fail-closed，回 `409 AI_TRADE_END_FLATTEN_UNAVAILABLE`
（拒绝**无副作用**：契约仍留在 `paused`，可继续也可再结束）；② 前端 `AiTradeEndDialog` 新增
`canFlatten`，冷态**不渲染**「平仓」那一支，改出一条说明 + 强制勾选，且提交时显式发 `flatten:false`
（发 `null` 会被服务端「带仓必须显式二选一」判成缺参数而 400，而用户面前根本没有那个选项）。
③ 同族的**热**路径也补上了：`end_group` 此前忽略 `_stop_session` 的返回值，于是「平仓单真的失败」
（`stop()` 承诺平仓失败也要完成停止流程并如实回 `residual`）同样会得到「已平仓、无孤儿持仓」。
改为按**结果**回执：`flattened = flatten_flag and not residual`，失败的那几笔如实进
`orphanPositions` —— 回执里这两个键绝不能互相打架。
用例：`test_ai_trade_pause_manager.py::test_cold_end_refuses_to_pretend_it_flattened` /
`::test_end_reports_a_failed_flatten_as_an_orphan_position`、`test_ai_trade_resume_cold.py` 的
冷 `end` 端到端 409、`AiTradeEndDialog.test.tsx`「canFlatten=false…」、
`AiTradeToggle.test.tsx`「结束（冷占位，带仓）…」。

**RV-2（阻断级 · 真实资金）「平仓后暂停」是全仓唯一一条平仓失败却静默的路径。**
`session._pause_unwind` 的平仓分支自己写了一遍事件扇出，**漏掉了 `_handle_flatten_result` 的另一半**：
`flatten_failed` / `residual` 不置、`AI_TRADE_FLATTEN_FAILED` 强提示不推。而 `pause.build_pause_notice`
在 `hold_position=False` 时**无条件**返回「已暂停，并已按你的选择平掉本会话的自管持仓」。
两处叠起来：平仓单失败（或部分成交）时，用户拿到的是一句「已平掉」，然后放心地关电脑去睡觉，
而交易所上留着一笔没有任何本地执行者看管的真实仓位。
**闭合**：① `_pause_unwind` 改为**复用 `stop()` 的同一条收尾** `_handle_flatten_result`（这正是
「同一件事只写一遍」的意义所在——那条收尾里的三件事一件都不能少）；② `build_pause_notice` 的
`not hold_position` 分支改为按 §3.1.3 ⑤ 读回的**平仓后**持仓断面判断：仍有仓 / 读不到 ⇒ 明说
「平仓没有完成」并逐成员列出残留，只有确认空仓才说已平掉。
用例：`test_ai_trade_pause_session.py::test_pause_with_flatten_reports_a_failed_flatten_instead_of_swallowing_it`、
`test_ai_trade_pause_drift.py::test_pause_notice_never_claims_a_flatten_that_did_not_happen`。

**RV-3（记录级 · HCI）续跑复核框把错误码原样糊给用户。**
`AiTradeResumeDialog` 直接渲染 `member.blockedReason`，而它的值域是稳定语义码
（`AI_TRADE_RESUME_BACKSTOP_MISSING`），不是给人读的句子。同一文件里 `VERDICT_LABEL` 已经建立了
「稳定枚举 → 装饰性中文」的既有做法，这一处只是漏了。
**闭合**：新增 `BLOCKED_REASON_LABEL`，未知码回落原文（宁可露出一个码，也不能因为映射缺一格就把
阻断原因整条吞掉）。

**RV-4（阻断级 · 完备性）两条后端单点生成的判定性事实没有任何读点。**
§3.10 要求判定性文案由后端单点生成、前端只渲染；§5.4 要求 `end` 的 `orphanPositions` 由前端
「出一条**持久**提示（不是 toast）」。实测两条都只有写点：`pauseM.onSuccess` 丢掉了整个回执
（含 `notice` —— 而 RV-2 修好之后，那正是唯一一处会说出「平仓其实没成功」的地方），
`endM.onSuccess` 也丢掉了整个回执（含 `orphanPositions`）。这是本仓库成文的「有写点没读点」形状，
且这一次被吞掉的恰好是**两句关于真实资金的告警**。归档后 AI 面板整块 `return null`，更让
「这些仓从此没人管了」失去最后一个可能的落点。
**闭合**：store 新增 `aiTradeLifecycleNotice`（**刻意不进** `clearAiTradeRuntime` —— 结束那一刻
分组正好被清掉，跟着清就等于让这条提示永远显示不出来；改为下次 `start` 成功时清），
`AiTradeToggle` 的 pause / end 两个 mutation 分别把 `notice` 与 `orphanPositions` 写进去，
`AgentPanel` 头部之下常驻渲染 + 「知道了」显式关闭（那是两种态下都还在的唯一位置）。
用例：`AiTradeToggle.test.tsx` 的「暂停回执的 notice 必须有读点」与「结束（冷占位，带仓）…」。

### 终审复核为「实现正确、无需改动」的几处（防止下游二次质疑）

1. **`spec` 与实现的一处刻意偏离，判定为正确**：§3.1.3 ⑤ 写「暂停时读持仓失败 ⇒ 续跑时该成员
   **直接**判 `unverifiable_identity`」，而 `pause._classify_position` 把它拆成两格：现在**有**仓
   ⇒ `unverifiable_identity` 拒绝（无从证明它是不是我们那一笔）；现在**无**仓 ⇒ `closed_offline`
   需勾选确认。后者是对的：续跑时那次交易所读数是**成功**的（否则整组已 `unreadable` 拒绝），
   交易所权威地说「这个标的上没有仓」，以空仓接管不可能接管到任何不属于我们的东西；按字面实现
   反而会让一个只是暂停瞬间读数抖动过的成员**永远续不回来**。文案已如实说明「这一段的盈亏可能缺失」。
2. **`_read_exchange_position` 走 `executor._read_positions` 不算「改 executor」**：它是 `LiveExecutor`
   的**可注入取数缝**（`positions_fn or _default_read_positions`，构造期赋值的实例属性），不是私有
   实现细节；`live_auto/executor.py` 在本轮 `git status` 里**一行未改**，放行条件 3 成立。
3. **`shutdown()` 返回类型从 `None` 变成 `dict` 是安全的**：`main.py` 的 lifespan 那处调用忽略返回值，
   新增的消费者只有 `/ai-trade/quit-pause`。
4. **一条如实记下的欠账（不修，说明理由）**：§5.7 新增的 WS `lifecycle` 事件（`paused` / `resumed`）
   后端**推了**，前端 `routeMemberEvent` 的 `case` 表里**没有它**，故执行轨迹面板上并没有那条时间线
   痕迹。判定为**可接受的欠账**而不是缺陷：① 暂停这件事本身已由 `status` 事件的 `state` /
   `pauseReason`、常驻横幅、看板口径提示三处如实呈现，没有任何事实被藏起来；② 轨迹面板的行是由
   `agent_trace` 的六个 phase 装配出来的「一轮决策」，往里插一条非决策行属于**面板的结构改动**，
   不是终审该顺手做的事（RV-1~RV-4 都是「已有的话说错了 / 说了没人听」，这一条是「还没做」）。
   下一轮做轨迹面板时接上它即可，wire 侧已经就绪。
5. **暂存面复核**：工作树常驻大量与本轮无关的未跟踪文件（`desktop/scripts/ensure-wincodesign*`、
   `services/quant/app/db/live_auto_repositories.py`、`tests/test_factor_library_expansion.py`、
   `docs/diagnoses/*`、`.claude-index/`、`CLAUDE.md`、`tmps/`）。逐条确认它们**不被本轮任何代码
   import**（`ai_auto_repositories.py` 的模块 docstring 已明写「不 import 同仓在制的
   `live_auto_repositories.py`」），故一律不入本次提交。`config.py` 的 diff 经逐行核对**只含**本轮
   的 7 个新字段，没有夹带任何既有默认值的翻转（§十.5 点名的那条历史教训）。

---

## 评审结论

### **有条件通过（Approved with conditions）**

**本方案可以进入实现节点。** 主体架构经复核是站得住的：把"至今只存在于进程内存里的真实资金
会话契约"落盘，用 `is_active` / `state == "running"` 两个谓词把**减风险侧**与**增风险侧**切开，
用一张新表绕开 `create_all` 不补列的既有教训，用一条独立的 `resume.py` 装配路径绕开
`_member_preflight_fail` 的必然拒绝 —— 这四个判断都正确，而且都建立在对本仓库既有代码的
真实勘察之上（九条 D-x 实证经逐条重打开，`session.py` / 前端三文件 / 桌面两文件的 `file:line`
**逐字命中**）。设计里最有价值的一条是 §零.4：**拒绝把正确性建立在"优雅关停"这个平台相关的
假设上**，转而在运行期就把状态写好，让"断电"和"点暂停"在磁盘上无法区分。这条判断是对的，
而且它顺带堵上了一个**今天就已经存在**的洞（强杀后交易所上的孤儿持仓，本地却把 run 归档）。

评审共提出 **3 个 P0、12 个 P1、14 个 P2**，全部 P0 / P1 已在 v2 正文闭合。三个 P0 里有两个
（P0-1 持仓身份、P0-2 退出默认平仓）**不是实现细节而是方向性问题**：前者会让一个自称
"绝不静默接管"的系统去管理用户自己开的仓位，后者会让本轮做完之后**需求原文一个字都没被解决**。
第三个（P0-3）是一类反复出现的形状：把安全关键的真值放在一个"失败只 warning"的装载路径后面。

### 放行条件（实现节点必须逐条满足，缺一条即视为未完成）

1. **G-26 / G-27 / G-28 三条新门禁必须真的跑绿**，它们分别是三个 P0 的行为守卫。
   其中 G-26 ③ 是一条**反向断言**（把身份判据关掉时同一场景必须判成 `matched`）——
   没有它，判据是否真的接进了判定链无从证明。
2. **G-3 必须按 v2 的三条子判据实现，且清单写进用例常量**。写死任何数字即视为未实现
   （v1 的 `== 19` 实测应为 16，这类门禁一旦"第一天就红"就会被顺手注释掉，比没有更坏）。
3. **`live_auto/executor.py` 不得出现在改动清单里**。它是与策略自动交易共用的真实资金执行器；
   §四 的文件计划里没有它，出现即越界（§八风险 14）。
4. **既有用例零改口径**：`test_ai_auto_history.py` 的 `test_reconcile_orphan_ai_trade_runs`
   与 `test_history_endpoints` 必须**逐字不改**仍通过（这是 P1-3 与 P0-3 落地口径的硬约束）。
   若实现中发现某条既有用例确实非改不可，**必须**在「实施过程发现的方案缺陷」里逐条列出理由，
   不得静默改掉。
5. **§七 的验收基线要求照旧**：写下"改动前 / 改动后"两次实测数字（passed / failed / 耗时），
   不得只写"全部通过"。干净 worktree 验证前先 `cp services/quant/.env`。
6. **提交用 `git add <具体路径>`，严禁 `git add -A`**（工作树常驻大量无关未跟踪文件），
   且特别注意**不要**把 `config.py` 里与本轮无关的默认值翻转一起带进去。

### 明确记在案的判断（下游不必再质疑，除非有新证据）

- **D-4 的 Windows `taskkill` 结论成立**，且即便将来实测发现它偶尔能优雅退出，**本设计也不改**。
- **`status()` 确实只有一个消费者**（`live_auto/manager.py:469`），且它必须保持同步 ⇒
  P0-3 只能用内存标志表达"我不知道"，不能改成查表。
- **§3.6 的矩阵不该被简化**。它是本轮唯一挡在"AI 与真实仓位之间"的判定，v2 把它从 8 格加到 11 格
  是必要的加法，不是过度设计。真正该砍的是没有读点的配置项，已砍。
- **规模适配整体合适**：4 个新后端文件 + 3 个新前端组件 + 32 条门禁，对一个动真实资金的
  跨进程生命周期特性是相称的。唯一的存量隐患是 `session.py`（1867 行）与 `manager.py`（962 行）
  本就远超 800 行红线、本轮还要再加 —— 已如实记进 §十.6 欠账，**本轮不做跨特性拆分**。

> **评审节点未改动任何源码，未执行任何 `git commit`。** 本次评审只改 `spec.md` 一个文件。
