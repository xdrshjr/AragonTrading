# AI 自动交易：成交真值化与「真正会生效」的风控层

> feature: `ai-trade-fill-truth-and-effective-controls`
> version: **v2**（设计评审定稿；v1 见 git 历史）
> 评审: 2026-09-02（Anthropic 工程团队设计评审）——逐节比对源码锚点 + 只读生产库复算 + `evidence/audit.py --assert` 实跑（18/18 一致）
> 基线 commit: `ca40b6b`（静默降级修复）；上一轮 `8451163`（亏损根因与活性加固）
> 勘察日期: 2026-09-02 09:10–09:30（生产真机，只读）
> 证据可复算脚本: `docs/plans/ai-trade-fill-truth-and-effective-controls/evidence/audit.py`

---

## 评审记录（Review Notes · v1 → v2）

> 评审节点：Anthropic 工程团队设计评审 · 2026-09-02
> 评审口径：**每一条结论都必须落在一个可点开的源码锚点或一条可复算的生产读数上**。
> 已实跑核验：`evidence/audit.py --assert` 退出 0、**18 项冻结读数逐格一致**（§二 全部证据为真，这是本轮 spec 最扎实的部分）；另在同一份只读生产库上补采了三组 v1 未采的读数（见 RV-E1..RV-E3）。已核验源码锚点 40+ 处，逐条附文件与行号。

### 本轮评审补采的三组生产读数（已并入正文）

| 编号 | 读数 | 取法 | 为什么重要 |
|---|---|---|---|
| `RV-E1` | **`positionStepPct = 0.5` 占 130/147 条 ai-trade run（88.4%）**；`0.25` 占 14；缺失 3 | 全库 `strategy_runs.config_json` 分组计数 | v1 只说「用户正在用的就是它」。**粗档位不是一个断面，它是这套系统 88% 的运行态**——轴 C / 轴 D 的优先级据此从「修一个坏断面」升级为「修默认世界」 |
| `RV-E2` | 全库 147 条 run 的 `metrics_json` 里 **`trendVerdict` / `trendBias` / `execScore` 三个键的出现次数均为 0** | 全库子串扫描 | 运行中的打包版**早于 `5337da1`**（不只是早于 `ca40b6b`）。⇒ 趋势基调那一整条链在生产上**一次都没跑过**，§二 结构上采不到它的证据；而下一个包一上线它就是活的。这正是 `RV-P0-1` 能藏住的原因 |
| `RV-E3` | 全库 `openFunnel.blocked` 合计：`consensus` 1007 / `confidence` 323 / `baseRuleMaBand` 254 / `turnover` 18 / `reflection` 12 / `drawdownBrake` 1；**`trendBias` 一格为 0** | 全库聚合 | 与 `RV-E2` 互证；同时说明 §二 E-3 的「共识是最大拦截层」在**全库尺度**上同样成立（不只是最新那一组） |

### P0（六条，全部已在正文修毕）

| 编号 | 结论 | 锚点 |
|---|---|---|
| `RV-P0-1` | **轴 D 只修了两把逐字同构的闸里下游那一把，而上游那一把先咬。** `signals.py:411` 与 `trend_verdict.py:633` 是**同一行字节**：`if size <= 0 or size > proposed or (shrink_requested and size >= proposed):`。`session.py:1877` 调 `_apply_trend_bias_gate`、`:1878` 才调 `_apply_consensus_gate`——趋势基调的逆势闸**排在共识逆势通路之前**。默认值 `counter_gate_enabled=True`（`config.py:823`）、`counter_size_mult=0.5`（`:829`）；`step=0.5` 时 `0.5×0.5=0.25 →quantize→ 0.5 ⇒ size >= proposed ⇒ 报 counter_trend_size 否决`。⇒ 只改 `signals.py`，逆势开仓会在 `trendBias` 那一格被拦死，**`AC-5`（`contrarianPassed > 0`）结构上不可达**，而 `IT-4` 因为没有指定 verdict 断面**会绿**。修法：轴 D 扩为 D-0（`trend_verdict`）+ D-1（`signals`），两把闸共用**同一个**纯核心谓词与**同一把**总闸 | `signals.py:407-412` / `trend_verdict.py:630-635` / `session.py:1877-1878` / `config.py:823,829` / `RV-E1` |
| `RV-P0-2` | **A-4 的净额分支条件取错了开关。** `fee_ledger 已装配` ≠「本会话是净额口径」：`ai_trade_fee_accounting_enabled`（`config.py:543`）与 `ai_trade_net_pnl_enabled`（`:547`）是**两把独立开关**，而 `trades.pnl` 是毛还是净**只由后者决定**（`reconcile.local_net_view` 的 `net_mode` 形参、`cost_runtime._run_reconcile` 传的正是它）。`accounting=True & net_pnl=False` 的断面上，A-4 会把一条**净额** pnl 写进一个**毛额**口径的会话，而 `local_net_view` 在 `net_mode=False` 时执行 `pnl - Σ腿费`（`reconcile.py:63`）**再扣一次费** ⇒ 对账差额被人为放大、`profitability` 的历史聚合二次扣费。判据必须是 `settings.ai_trade_net_pnl_enabled` | `config.py:543,547` / `reconcile.py:55-64` / `cost_runtime.py:538` |
| `RV-P0-3` | **`_settling` 与既有平仓通路没有互锁 ⇒ 已实现盈亏可能双记。** `settling` 期间 `projected` 刻意不变（不变式 I-2），于是利润保护 / 持仓周期 / 模型 `close` / `flatten` 在这最多 90 秒里**仍会真的向交易所发平仓**；交易所回 `LIVE_NO_POSITION` ⇒ `_do_close` 走 `_settle_benign_close`（`executor.py:419` → `:663`），它已经**按 `mark_px` 合成了一条 `source="oco"` 的腿**并执行 `realized_pnl_usd += self._close_pnl(ref_px)` + `projected="flat"`。此刻 `_settling` 仍未决；下一 tick 命中结算 ⇒ 按 spec「完全复用 `_synthesize_external_close` + 立场复位六行」再加一次 `realized_pnl_usd`。`_synthesize_external_close` 自己的首行守卫（`projected == "flat" ⇒ return []`，`executor.py:1063`）只挡住第二条 `trade`，**挡不住调用方那一行 `realized_pnl_usd +=`**。R-1 的三重保险一条都不覆盖这条 | `executor.py:377-430,663-681,1060-1075` |
| `RV-P0-4` | **`match_settlement` 判据 2 会让轴 A 在净持仓模式账户上 100% 静默空转。** `_normalize_position_history`（`trading.py:778-800`）把 `direction` 归一为 `str(r.get("direction") or "")`——**缺字段即空串**；OKX 的 `positions-history.direction` 在净持仓模式下并不保证是 `long/short`，而本仓库的 `pos_mode` 由交易所账户配置决定（`manager.py:2199` 取 `config["posMode"]`，`executor.py:962` 明确同时支持 `net_mode`）。判据 2 是**严格相等 + fail-closed** ⇒ 恒不命中 ⇒ 每一笔都回落 `local_mark`，**而回落与今天逐字相同，没有任何一个读数会变红**（`AC-1` 会显示 `ratio=0`，但没人能从中读出原因）。仓库里已有反例：`resume` 的离线回读**只按 `posId` 匹配、刻意不用 `direction`**（`resume.py:383-384`）。另：判据 3 用 `str(row["posId"]) not in claimed`，而归一化后缺字段是**空串**——空串永远不在 `claimed` 里，会被当成一条合法结算认领 | `trading.py:778-800` / `resume.py:382-384` / `manager.py:2199` / `executor.py:962` |
| `RV-P0-5` | **A-4 的落账表与 `G-A3` / `G-A8` / `G-A10` 自相矛盾。** A-4 的左列表头写的是「`settlement is None`（**含总闸关** / 超时 / 匹配不中）」，并要求这一列**照样写** `settlementSource="local_mark"` + `markPxAtDetect` + `settlementWaitedS` 三键；而 `G-A3` 要求超时落账「与今天逐字相同」、`G-A8` 要求总闸关时产物 sha256 等于 `ca40b6b`、`G-A10` 要求 `settlement_fn is None`（= `live_auto` 全部路径）逐字回退。三条门禁与该表**不可能同时为真**。必须把这一列拆成两格：**总闸关 / `settlement_fn is None` ⇒ 零加法键**；**状态机真的跑过了（超时 / 幻影撤销后落账 / 匹配不中）⇒ 三键** | §4.A A-4 vs §9.1 `G-A3`/`G-A8`/`G-A10` |
| `RV-P0-6` | **八个 `settlement*` 键的装配点选在了 `live_auto` 与 `ai_auto` 的共用模块上。** §6.1 把写点定为 `executor._synthesize_external_close → build_external_close_explain`（`live_auto/explain.py:121`）。但本仓库 `explain.meta` 的**唯一**装配点是 `session._inject_explain_meta`（`session.py:664-704`），`_cost.apply_explain_meta` / `apply_trip_meta` 都从那里进，且**顺序敏感**（`apply_trip_meta` 明写「必须在 `exitSource` 打标之后」，`cost_runtime.py:526`）。落在共用模块有两个后果：①`live_auto`（非 AI）的 `external_close` explain 形状变了，撞既有冻结用例；②造出第二个 `meta` 装配现场。修法：`executor` 只在一个**轮级槽**里放 `SettlementFact | None`（逐字镜像 `session._pending_horizon_meta` 的既有范式），由 `_inject_explain_meta` 在 `exitSource` 之后 `setdefault` 进去 | `live_auto/explain.py:121-146` / `session.py:664-704,691` / `cost_runtime.py:526-546` |

### P1（十二条，全部已在正文修毕）

| 编号 | 结论 | 锚点 |
|---|---|---|
| `RV-P1-1` | **F-4 建立在对现有代码的误读上。** `describeReconcile`（`aiTradeCost.ts:114-127`）只有 `unavailable` 与 `partial` 两条早退，**`ok` 与 `drift` 共用最后那条「本地净额与交易所可比口径差 …」——差额今天就在屏幕上**。E-5 的「7.41 USDT 被绿色徽章盖住」应改述为**「数字在、结论错」**：徽章写「已对上」而同一行正文写「差 +1.528 USDT」，产品在自相矛盾。F-4 相应改为「`ok` 时把措辞收敛成『在容差内』」，删掉「恒显示差额」这个已经为真的改动 | `aiTradeCost.ts:94-127` |
| `RV-P1-2` | **轴 E 的 `max_age_s=1800` 是一条死配置。** `ai_trade_limit_order_ttl_s` 默认 **300s** 已经在 `resting.is_expired`（`resting.py:79-81`）上生效，挂单活不到 1800s，判据 4 永远轮不到 | `config.py:220` / `resting.py:79-81` |
| `RV-P1-3` | **轴 E 的判据 5 与既有守卫重叠，而 E-4 的 5 次同价重挂不在这条路径上。** `_prepare_resting_slot`（`executor.py:468-470`）已经对「同意图同向同价」返回 `"skipped"` 且**不撤不挂**（`is_same_intent` 是逐字相等，比 spec 的 8 bps 阈值**更严**）。所以那 5 次一定来自别的路径（TTL 到期撤单后重挂 / Agent `cancel_order` 动作 / `_do_close` 的预撤单）。spec 必须**先指认路径**，否则 `plan_resting_action` 会挂在一条已经有守卫的路上 | `executor.py:465-478` / `resting.py:97-104` |
| `RV-P1-4` | **C-1 的 `fraction=0.0` 会落进 `_do_open` 的 dust 分支，并对用户说一句假话。** `apply_size_fraction`（`sizing.py:471-479`）在 `fraction=0.0` 时执行 `replace(size_fraction=0.0)`，`action` 仍是 `open_*`；下游 `_do_open` 的 `sz <= 0` 分支（`executor.py:333-335`）会 `dust_skips += 1` 并推「**预算 / 杠杆过小**，单笔不足最小下单量」。于是同一件事被记两次（`sizing_zeroed` + `dust_skips`），且纪律条 `volSizing` 行（`discipline_view.py:279-282`）会对用户说一个**错误的原因**——风控解出零跟预算大小毫无关系 | `sizing.py:471-479` / `executor.py:333-335` / `discipline_view.py:271-292` |
| `RV-P1-5` | **adopt 端点的路径与全部既有成员端点不同构。** 既有五个成员端点一律是 `/ai-trade/{group_id}/member/{inst_id}/…`（`api/ai_auto.py:576,585,640,670`）。spec 写的 `/api/ai-trade/member/{instId}/adopt-orphan` 丢了 `group_id`（多分组并存时 `instId` 不唯一，而多标的分组是既有能力）、且路径参数用了驼峰 | `api/ai_auto.py:567-700` |
| `RV-P1-6` | **§三 非目标 5 把 `quantize_size_fraction` 的调用点数错了。** 不是两处，是 **6 处**：`decider.py:791`、`reflection.py:61`、`signals.py:409`、`sizing.py:391` + `:393`、`trend_verdict.py:631`。这条错数正是 `RV-P0-1` 能被漏掉的直接原因——数漏的那一处恰恰是同形缺陷所在 | 全仓 grep |
| `RV-P1-7` | **A-4 的费率残差有现成先例，spec 却当作新发明，并把先例里最重要的那道守卫弄丢了。** `resume._offline_exit_trade`（`resume.py:418-433`）已经在做「交易所往返总费 − 本地已记腿费」，并带一条 **`if fee < 0: return None`**（不自洽 ⇒ 宁可缺一笔并说明）。spec 的 `max(0.0, fact.fee_usd − self.entry_fee_usd)` 会把「不自洽」**静默夹成 0**——那正是本轮在治的那类假数字 | `resume.py:408-441` |
| `RV-P1-8` | **`trade_fee_fields` 的 `override_fee_usd` 落点不完整。** 它是 `**leg` 直通 `FeeLedger.on_trade`（`fee_ledger.py:130-149`），改签名必须同轮改 `on_trade`；且 `on_trade` 里的 `self._session_fee_usd += fee` 与返回的 `feeBps`（`leg_fee_bps(ord_type)`）都要随 override 一起改，否则会话累计费与 `explain.meta.feeBps` 会与同一条腿的 `fee` 互相打架（`explain_meta_fields` 把两者一起写进 meta，`fee_ledger.py:157-166`） | `fee_ledger.py:130-166` |
| `RV-P1-9` | **不变式 I-2「`unrealized` 冻结」没有可施加的落点。** `_on_reconcile_ok` 在幻影分支**之前**就无条件写了 `gross_unrealized_usd = self._net_upl(positions)` 与 `unrealized_usd = self._net_unrealized(...)`（`executor.py:735-736`）；交易所侧已空 ⇒ 两者恒 0，而 `projected` 还说有仓。要冻结必须在 `_settling` 非空时**显式跳过这两行**，spec 需写死 | `executor.py:732-736` |
| `RV-P1-10` | **§十一 回退矩阵只列了 15 项里的 9 项。** 2/3/4/6/12/13 六项（`deadline_s` / `poll_interval_s` / `match_tol_s` / `confirm_rounds` / `min_reprice_bps` / `max_age_s`）是**参数**不是闸，矩阵里必须显式标注「参数项，回退由所属轴的总闸承担」，否则 `G-X1`「全部 15 项置回退档」这条门禁没有可执行的定义 | §八 vs §十一 |
| `RV-P1-11` | **不变式 I-3 是循环论证。** `_settled_pos_ids` 一旦按 FIFO 淘汰，同一 `posId` **完全可以**被第二次认领——I-3 说「重复认领会被判据 3 拦下」，而判据 3 恰恰读的就是这个被淘汰掉的集合。真正的安全性来自另一条事实：**一笔往返只会进入 `settling` 一次**（`projected != "flat"` 是入口条件，落账即归 flat），故重复认领需要「同一 `posId` 在 256 笔之后又出现一次结算」，而 `posId` 在交易所侧对一次持仓生命周期唯一。I-3 必须改写成这条 | §6.3 I-3 |
| `RV-P1-12` | **门禁缺一条最重要的端到端反向断言。** `G-A1..G-A12` 十二条全是单元级的，没有任何一条断言「命中结算后 `local_net_view` 的差额收敛到 `tol` 以内」——而 `AC-2` 正是靠它判轴 A 是否有效。缺了它，轴 A 可以全部单测通过而在生产上不改善任何东西 | §9.1 轴 A vs §9.3 `AC-2` |

### P2（七条，已按注明方式处理）

| 编号 | 结论 | 处理 |
|---|---|---|
| `RV-P2-1` | 行号漂移：`_status_payload` 实为 `session.py:3404`（spec 写 3397）、`_compute_ai_stats` 实为 `:3744`（写 3742）、`resolve_consensus_gate` 降档段实为 `signals.py:407-412`（写 396-405）、`reconcile._status` 实为 `:154-159`（写 156-161） | 正文已逐处改正 |
| `RV-P2-2` | §二 E-2 缺 `positionStepPct` 的全库分布，而它是本轮最强的一条证据 | 已补为 `RV-E1` 并写进 E-2 |
| `RV-P2-3` | Q3 的「结算中」芯片只在 `AiTradeMemberRow.tsx` 有落点，历史详情页同样会看到这条腿 | 已在 §7.3 补落点 |
| `RV-P2-4` | `_build_executor` 已是 **6 个形参**（超 `MAX_FUNCTION_PARAMS=5`），轴 A 在这里加装配会让它更显眼 | 已在 §十二 登记，并明确**不新增形参**（`settlement_fn` 在函数体内用已有的 `creds` / `inst_id` 闭包构造） |
| `RV-P2-5` | `G-X2` 要求每个新键有前端读点，但 `sampleScope` / `settlementPosId` / `bookedOpenFeeUsd` 三个键在 §7.3 里找不到落点 | 已在 §7.3 / §4.G 补齐落点 |
| `RV-P2-6` | §5.1 重复描述 `SD-DEBT-204` 的细节容易与黑板漂移 | 已收敛为一条指针 |
| `RV-P2-7` | 「后端文案不得含字面 `**`」是本仓库**第 4 次复发**，而 `G-B6` / `G-F4` 只覆盖本轮新增的两个函数 | 已升级为仓库级门禁 `G-X3`（扫全部会进 `message` 的后端中文常量），理由：前端门禁看不见后端字面量，正是上一轮 `8451163` 的原话 |

### 确认为「不是缺陷，不要改」（五条，写下来防止下游重复投入）

1. **时间量纲是对的。** `_normalize_position_history` 已经把 `cTime` / `uTime` 整除 1000 归一为**秒**（`trading.py:796-797`），故 `match_settlement` 判据 4/5 直接与 `detect_ts` / `entry_ts` 可比。评审曾怀疑这里有毫秒/秒混用，**核实后收回这条反对意见**。
2. **`funding` / `liqPenalty` 不进本地账本是对的，而且是严格自洽的。** OKX 关系式经归一后为 `realized = pnl − fee − funding − liq`，故 `exchangeComparable = realized + funding + liq = pnl − fee`——**恰好等于 A-4 写进 `trades.pnl` 的那个数**。`G-A12` 必须留着。
3. **`attribution.record_close` 不需要为轴 A 另改。** 它算 `gross = pnl + fee`（`attribution.py:255-258`）；在 A-4 的残差方案下这等于「价差毛额 − 开仓腿费」，与今天同形，口径不变。
4. **`min_consensus=1` 太严** —— 维持 v1 判定：它已是最小非零值，缺陷在豁免路径而不在阈值。
5. **C-1 的漏斗路由成立。** `note_sizing`（`open_funnel.py:229-241`）判的就是 `size_fraction is None or <= 0`，`fraction=0.0` 会正确落进 `sizing_zeroed` 那一格、且**不进 `blocked`**。需要修的只有 `RV-P1-4` 那条下游误报。

---

## 一、概述（Overview）

这一轮解决的是一件比「某个门槛调错了」更根本的事：**AI 自动交易的本地账本，在两个关键位置上写的是它自己猜的数，而不是交易所说的数；而它已经装配好的一整排风险控制层，在用户实际选择的档位上，一次都没有真的作用到下单量上。** 前一轮（`8451163`）用 211 条 run / 520 条腿把「离场极性」掰正了，上一轮（`ca40b6b`）把「向外界要真话」的四条通路修通了。本轮继续沿着同一条线往下走一层：**要回来的话，有没有真的写进账本；装配好的闸，有没有真的拦住手。**

第一件事是成交真值。今天 `LiveExecutor` 在侦测到「本地有仓、交易所没仓」时，会合成一条 `external_close` 平仓成交，而这条成交的**价格取自本地的 `mark_px`**（`executor.py:741` / `executor.py:1060`）。代码自己的注释把这件事叫做「按 mark_px 捏造盈亏」，但历史上所有的防护都指向另一个方向——防止「其实没平仓却被误判为平了」（`_fill_grace` / `_snapshot_outranks_last_write`）。**真的平了、但价格是错的**这条路径，从来没有人处理过。生产实测：20 条有交易所结算读数的 ai-trade run 上，本地账本合计 **−42.84 USDT**，交易所可比口径合计 **−56.80 USDT**——**这套系统少报了 13.97 USDT，占真实亏损的 24.6%**；单条最差的 run 1608（DOGE）本地 −19.25 而交易所 −31.25。最干净的一条反证在 run 1627（SOL）：两笔往返都是 `external` 平仓，本地记 −0.87，交易所记 −5.06；把这两笔按它们自己写在 `trades.stop_loss_price` 里的止损价重算，得到 −4.96，与交易所的 −5.06 相差 1.3%。**这两笔仓位是被交易所侧的 OCO 止损打掉的，而应用把它们记成了「在当时的标记价平掉」。** 更要紧的是下游：上一轮那条最吃重的结论「每一条确定性离场通道都是正的（external +23.13 / profitGuard +14.14），唯一为负的是模型自主平仓（agent −67.52）」——其中 `external` 那 17 笔，**每一笔的价格都是本地合成的**，而 `agent` 那 36 笔是真实市价单的真实成交回执。这不意味着上一轮的结论错了，但意味着它建立在一个**两侧证据质量不对等**的对比上，而修好它是任何进一步「学什么」的前置条件。

第二件事是风控层的有效性。`ai_trade_sizing_*` 那一整套（波动率目标、凯利上限、相关性缩减、回撤刹车第一档、逆势通路强制降档）解出来的都是一个连续的 `f_raw`，而它必须经 `quantize_size_fraction` 吸附到用户选的档位阶梯上，那个函数里有一句 `gears = max(1, ...)`——**任何 `>0` 的开仓意图至少落到最小一格**。生产实测：最新那一组 5 个成员上，`scaledRounds` 与 `quantizeErasedRounds` **逐个相等**（BTC 8/8、ETH 13/13、SOL 3/3、DOGE 2/2）——**这一层每一次缩减都被档位地板原样吃了回去，命中率 100%**。ETH 那 13 轮的 `avgScaleRatio` 是 **0.1846**：风控层解出「只该开模型提议的 18%」，实际开出去的是 **100%**，超配 5.4 倍。DOGE 有两轮 `avgScaleRatio` 是 **0.0**：风控层解出的是**零**，而 `max(1, …)` 把零抬成了半仓。同一条地板还让**逆势通路整条死掉**：`contrarian_size_mult=0.5` 作用在最小格 `0.5` 上得到 `0.25`，量化吸附回 `0.5`，于是 `shrink_requested and size >= proposed` 成立，判 `low_consensus_size` 否决——生产实测 `contrarianPassed=0`（484+476+484+473+456 = 2373 个决策轮，一次都没放行过），而 `consensus` 正是最新那一组**最大的拦截层**（233 次开仓意愿里拦掉 175 次，SOL 单标的 117 里拦 106），且它的门槛已经是可能的最小值 `min_consensus=1`。

第三件事是这两件事在界面上的样子。对账卡今天对 6 条**终态**记录显示绿色的「已对上」徽章，而它们合计带着 **7.41 USDT** 的未解释差额——因为 `reconcile._status` 的 `final` 分支无条件返回 `ok`（`reconcile.py:158-161`）。纪律自检的「波动率目标仓位」行显示「已装配 / 年化 20% / λ=0.25」，而运行期它 100% 被抹掉；`quantizeErasedRounds` 这个计数器在**全仓没有任何读点**。启动弹窗的档位选择器提供「每档 50%」这个选项（用户正在用的就是它），却不说它会让回撤刹车第一档、相关性缩减、逆势通路三件东西同时失效。本轮把这些**算出来了但没人看得见**的数字全部接上落点——这不是锦上添花：本仓库反复付过代价的那个形状（「闸门把每一单都拦了」与「一切正常」在界面上长得一模一样）在这里已经发生了三次。

---

## 二、证据（生产实测，全部可复算）

取数口径：只读打开 `%APPDATA%/aragon-desktop/aragon.db`（`file:...?mode=ro`，119.7MB，36 张表），外加 `%APPDATA%/aragon-desktop/logs/backend.log`（256.9MB）。**运行中的桌面端是 `ca40b6b` 之前的打包版**（进程起于 2026-09-02 01:16，日志里 `intel reader blocked ... (private_host)` 仍在刷 ⇒ 上一轮的 netpolicy 修复不在这个二进制里），故以下全部是**修复前的稳态行为**。

### E-1　外部平仓的价格是本地合成的，账本因此系统性少报亏损

| 读数 | 值 | 取法 |
|---|---|---|
| 有交易所结算读数的 ai-trade run | 20 | `aiStats.reconcile.exchangeComparableUsd is not None` |
| 本地净额合计 | **−42.835 USDT** | `Σ reconcile.localNetUsd` |
| 交易所可比口径合计 | **−56.803 USDT** | `Σ reconcile.exchangeComparableUsd` |
| 差额 | **+13.968 USDT**（少报 24.6% 的真实亏损） | 前两行相减 |
| 单条最差 | run 1608 DOGE：本地 −19.253 / 交易所 −31.248（差 11.995） | — |
| 次差 | run 1627 SOL：本地 −0.871 / 交易所 −5.056（差 4.186，5.8×） | — |
| 反向偏差也存在 | run 1606 ETH：本地 −9.347 / 交易所 −3.074（多报 6.273） | 说明这不是单向的费率口径差 |

**根因的直接验算（run 1627 / SOL，两笔往返、两条 `external` 平仓）**：

| 往返 | 本地开仓价 | 本地平仓价 | 本地记录的止损价 | 按止损价重算的毛额 |
|---|---|---|---|---|
| #1 | 101.80（qty 2.45） | 101.96（`external`，+0.217） | 100.9309 | −2.129 |
| #2 | 100.89（qty 2.47） | 100.52（`external`，−1.088） | 99.8824 | −2.489 |

两笔按止损价重算的毛额合计 **−4.618**；交易所回报的毛额 = `realizedPnl + fee` = `−5.056 + 0.496` = **−4.560**。误差 1.3%。而本地记录的毛额是 **−0.522**。结论：**这两笔仓位是被交易所侧的 OCO 止损成交掉的，本地按侦测那一刻的 `mark_px` 记了一个完全不同的价格。**

同一条 run 的**费率**也对得上第二个假设：本地记 `0.3487`（限价开仓腿按 `feeBps=2.0` 的 maker 常数、平仓腿按 `5.0`），交易所收 `0.4963`；两条腿都按 taker `5.0` 重算得 `0.4983`——**限价开仓腿实际是 taker 成交，而本地无条件按 maker 记账**。全库 `trades.explain.meta` 里 `('limit', 2.0)` 的开仓腿共 **61** 条，`('market', 5.0)` 共 28 条。

**下游影响面**（全库 211 条 ai-trade run / 520 条腿，按 `explain.meta.exitSource` 归并）：

| exitSource | 笔数 | 净额合计 | 价格来源 |
|---|---|---|---|
| `agent` | 36 | **−67.524** | 真实市价单回执 |
| `external` | 17 | **+23.129** | ★ 本地 `mark_px` 合成 |
| `profitGuard` | 7 | +14.136 | 真实市价单回执 |
| `agentOverride` | 7 | −10.006 | 真实市价单回执 |
| `exchangeOco` | 1 | −4.776 | ★ 本地 `mark_px` 合成 |
| `sessionStop` | 2 | +0.661 | 真实市价单回执 |
| `offline` | 1 | −2.979 | 真实市价单回执 |
| （无 `exitSource` 的历史腿） | 188 | −5.843 | — |

★ 的两行合计 18 笔，是上一轮「确定性通道全是正的」这条结论里唯一一条**正贡献最大**的通道，也是唯一一条价格由本地合成的通道。

### E-2　档位量化把每一次风险缩减都抹掉了（命中率 100%）

最新那一组（group `aitradegrp_0db79288…`，5 成员，`positionStepPct = 0.5` ⇒ 阶梯只有 `[0.5, 1.0]` 两格）：

| run | 标的 | 决策轮 | `sizing.scaledRounds` | `quantizeErasedRounds` | `avgScaleRatio` | `corrScaledRounds` | 成交 |
|---|---|---|---|---|---|---|---|
| 1625 | BTC | 536 | 8 | **8** | 0.6000 | 8 | 10 |
| 1626 | ETH | 476 | 13 | **13** | **0.1846** | 13 | 4 |
| 1627 | SOL | 484 | 3 | **3** | 0.7977 | 2 | 4 |
| 1628 | DOGE | 473 | 2 | **2** | **0.0000** | 2 | 0 |
| 1629 | SATS | 456 | 0 | 0 | — | 0 | 0 |

- `scaledRounds == quantizeErasedRounds` **逐行相等**：这一层解出的每一次缩减都被地板吃回去了。
- ETH 的 `avgScaleRatio = 0.1846`：解算结果是模型提议的 18.46%，实际下单是 100%（**5.4 倍超配**）。
- DOGE 的 `avgScaleRatio = 0.0`：解算结果是**零**（`corr_scale` 值域是 `[0,1]`，`sizing.py:127`），`quantize_size_fraction` 的 `max(1, …)` 把它抬成最小一格 0.5。这两轮恰好都是限价单没成交，是运气不是设计。
- 五条 run 的 `winRate` 全是 **0.0**，`totalReturn` 分别 −1.00% / −0.95% / −0.87% / 0 / 0。
- **【评审补采 `RV-E1`】这不是一个坏断面，这是这套系统的默认世界**：全库 147 条 ai-trade run 的 `config_json.positionStepPct` 分布为 **`0.5` 占 130 条（88.4%）**、`0.25` 占 14 条、缺失 3 条。换言之「阶梯只有两格」是 88% 的运行态，轴 C 与轴 D 修的不是边角。

`sizing.py` 的模块 docstring 已经**如实登记过**这件事（「诚实声明（分辨率）……第一档减速（×0.5）与相关性缩减在最常见断面上因此是 no-op……本模块不为自己绕过档位，而是把这件事如实暴露成 `SizingOutcome.scale_erased` + `fraction_raw`」）。本轮的判定是：**登记是对的，但「暴露」这一半没做完**——`quantizeErasedRounds` 在 `apps/web` 全仓 grep 零命中，纪律自检的 `volSizing` 行也不读它。

### E-3　逆势通路一次都没放行过，因为它的强制降档被同一块地板吃掉

| 读数 | 值 |
|---|---|
| `openFunnel.directionBalance.contrarianPassed` | **0 / 0 / 0 / 0 / 0**（五个成员全零，2373 个决策轮） |
| `openFunnel.topBlocker` | `consensus`（BTC/ETH/SOL/DOGE 四个成员全是它） |
| 组内开仓意愿被 `consensus` 拦下 | **175 / 233 = 75.1%**（SOL 单标的 106/117 = 90.6%） |
| 生效门槛 | `ai_trade_min_consensus = 1`（**已经是可能的最小非零值**） |
| `metrics_json` 里残留的细分码 | `low_consensus_size` × 14（BTC 9 / SOL 3 / ETH 2）、`low_consensus_conf` × 4 |

`signals.resolve_consensus_gate`（`signals.py:324-405`）的降档段：

```
proposed = 0.5                                  # 最小格（step=0.5 时阶梯 = [0.5, 1.0]）
target   = 0.5 × contrarian_size_mult(0.5) = 0.25
size     = quantize_size_fraction(0.25, 0.5)
         = ladder[max(1, round(0.25/0.5)) - 1] = ladder[0] = 0.5
shrink_requested and size >= proposed  ⇒ True   ⇒ 否决 low_consensus_size
```

于是**逆势通路只在模型提议 100% 仓位时才可能放行**——安全阀只对最大的那一档开门。这个断面在 `signals.py` 的注释里被记为实施期缺陷 I-1（「粗档位把降档吸附回原值即退回观望」），但当时的处置是**报否决**，没人量过它把整条通路变成了什么。

**【评审补充 `RV-P0-1`：同一块地板上还坐着第二把闸，而且它先咬】** `trend_verdict.resolve_trend_gate` 的降档段（`trend_verdict.py:630-635`）与上面这段是**逐字同构**的——第 `633` 行与 `signals.py:411` 是同一行字节：

```python
if size <= 0 or size > proposed or (shrink_requested and size >= proposed):
```

而 `session._run_decision_round` 的调用序是 `_apply_trend_bias_gate`（`session.py:1877`）**先于** `_apply_consensus_gate`（`:1878`），`open_funnel.LAYERS` 的层序同样把 `trendBias` 排在 `consensus` 之前。默认值 `ai_trade_trend_counter_gate_enabled=True`（`config.py:823`）、`ai_trade_trend_counter_size_mult=0.5`（`:829`）。在 `step=0.5` 上：

```
proposed = 0.5;  0.5 × 0.5 = 0.25  →quantize→ 0.5  ⇒ shrink_requested and size >= proposed
⇒ 报 counter_trend_size 否决（进 openFunnel 的 trendBias 那一格）
```

**生产库对此零证据**（`RV-E2`：147 条 run 的 `metrics_json` 里 `trendVerdict` / `trendBias` / `execScore` 三个键出现次数**均为 0**，即运行中的打包版早于 `5337da1`）——这不是「它没问题」，而是「它还没上线」。下一个包一发，这把闸就是活的，且**排在共识逆势通路前面**。故轴 D 必须同轮覆盖两把闸（见 §4.D 的 D-0 / D-1）。

### E-4　限价挂单的寿命等于唤醒间隔，撤单重挂里有 5 次价格逐字相同

`live_order_audit` 里属于最新那一组的 46 行（`created_ts >= 1788183795`）：

| 读数 | 值 |
|---|---|
| `('accepted','limit')` / `('canceled','limit')` / `('accepted','market')` / `('error','limit')` | 21 / 16 / 8 / 1 |
| 配对成功的「挂上→撤掉」 | 16 对 |
| 挂单存活时长 | min 2s / **median 198s** / max 245s / mean 168.6s |
| 唤醒间隔 `wakeIntervalMinutes` | **3.0 分钟 = 180s** |
| 重挂价与上一次逐字相同（`+0.0 bps`） | **5 次**（ETH ×4、SOL ×1） |
| 重挂价差 ≤ 10 bps | 再 5 次（−1.9 / −2.0 / −9.8 / −4.1 / −6.1 bps） |
| 组内 `submitted → filled` | **26 → 9（34.6%）**；ETH 单标的 **13 → 2（15.4%）** |
| 组内端到端 `intents → filled` | **233 → 9（3.9%）** |

挂单寿命的中位数（198s）与唤醒间隔（180s）几乎相等，说明**挂单不是被行情否掉的，是被下一次决策轮例行撤掉的**。5 次「撤掉再按同一个价格挂回去」是纯粹的空转：它既没有改变任何经济含义，又各消耗一次私有写请求（上一轮 `ca40b6b` 刚为私有请求配额做过一整条轴 C）。

### E-5　终态对账无条件报「已对上」，而同一行正文写着 7.41 USDT 的差额（产品在自相矛盾）

`reconcile._status`（`reconcile.py:154-159`）：

```python
if final:
    return "partial" if mismatch else "ok"
return "drift" if abs(diff) > tol else "ok"
```

实测（`tol = max(0.5, 0.05 × |comparable|)`）：

| run | 标的 | `final` | `coverageMismatch` | `diffUsd` | `tol` | 显示 |
|---|---|---|---|---|---|---|
| 1622 | SOL | True | False | **−1.912** | 0.500 | ✅ 已对上 |
| 1620 | BTC | True | False | **+1.528** | 0.500 | ✅ 已对上 |
| 1623 | DOGE | True | False | **+1.194** | 0.500 | ✅ 已对上 |
| 1577 | SOL | True | False | **+1.123** | 0.500 | ✅ 已对上 |
| 1621 | ETH | True | False | **−0.967** | 0.500 | ✅ 已对上 |
| 1610 | BTC | True | False | **−0.683** | 0.500 | ✅ 已对上 |

**6 条、合计 7.408 USDT。**

**【评审更正 `RV-P1-1`】差额并没有被藏起来——被说错的是结论。** `describeReconcile`（`apps/web/lib/aiTradeCost.ts:114-127`）只有 `unavailable` 与 `partial` 两条早退，`ok` 与 `drift` **共用**最后那条 `本地净额与交易所可比口径差 {diff}{ratio}`。所以用户在这一屏上同时看到「✅ 已对上」与「差 +1.528 USDT（3.4%）」——**徽章与它下面那行字互相打脸**。这比「藏起来」更坏：它训练用户不去读那个数。v1 据此推导出的 F-4「`ok` 分支改为恒显示差额」**是一个不存在的改动**，已在 §4.F F-4 改正。

`final` 分支当初的理由（P1-5）是「刚平的那一笔 `uTime` 有结算延迟 ⇒ 终态对账会系统性少一笔，呈现为伪 drift，而这偏偏是用户最常看的那一屏」。这个理由是对的，但**「少一笔」正是 `coverageMismatch`（`samples != localTrips`），它已经由 `partial` 单独接住了**——上面 6 条全部 `mismatch=False`，即样本数与本地往返数**完全对齐**，差额纯粹来自价格/费率口径。这是一次「为了消除伪阳性，把真阳性一起消掉」的过度收敛。

### E-6　`actionCounts` / `holdRate` 的分母与旁边那张图不是同一个

run 1627 的同一张卡上并排出现：

- `decisionRounds: 484`、`actionCounts: {open_long: 0, open_short: 0, close: 0, hold: 50, cancel_order: 0}`、`holdRate: 1.0`
- `openFunnel: {intents: 117, submitted: 3, filled: 2, scope: "本会话全程累计"}`

`_compute_ai_stats`（`session.py:3742-3800`）的 docstring 写明「计数在**保留的 ≤50 条决策样本**上求；`decisionRounds` 取会话真实完成轮数」——口径是清楚的，但 wire 上没有任何一个字段承载「这 50 是样本不是全程」，前端也没有把它写进标签。用户看到的是「484 轮里 100% 观望、一次都没想过开仓」，而真相是「想开 117 次，其中 106 次被同向共识拦下」。

### E-7　交易所侧存在应用不知道的持仓时，没有任何一条通路会发现

`LiveExecutor._on_reconcile_ok`（`executor.py:720-750`）只有一个方向的分支：

```python
if (self.projected != "flat" and abs_pos <= 0 and not grace
        and self._snapshot_outranks_last_write()
        and not self._external_close_suppressed()):
```

**全文没有 `projected == "flat" and abs_pos > 0` 的对称分支**（`grep -n "abs_pos" executor.py` 只有 `:689`、`:734`、`:737` 三处）。唯一会独立向交易所要持仓真值的地方是 `session._read_exchange_position`（`session.py:1162`），而它**只在暂停时被调用一次**。后果：一笔因「撤单与成交竞态」「限价单部分成交」「用户手工下单」而出现在交易所的持仓，对自动交易链是完全不可见的——不受持仓周期闸管、不受利润保护管、不受活性看门狗管，它只会在 `reconcile` 的 `coverageMismatch` 上留下一个数字。实测已经出现过两次：run 1601（`samples=2 / localTrips=1`）与 run 1613（`samples=3 / localTrips=4`）。E-4 里那 16 次撤单说明这条竞态在生产上是活跃的。

### E-8　三件看起来像缺陷、本轮明确判定为**不是**的事（写下来防止下游重复投入）

1. **`fee_ledger` 用本地常数即时记账，不等实测值** —— 这是 `fee_ledger.py` 模块 docstring 第 2 条的成文纪律（「把『盈亏是否正确』变成网络可用性的函数」是更坏的设计）。本轮**不推翻它**：轴 A 改的是**外部平仓那一条腿**（它今天用的本来也不是回执，而是本地 `mark_px`），以及往返总费率的**残差补正**，开仓腿的即时记账一个字节不动。
2. **`reconcile` 只观测不回写盈亏** —— 同上（`reconcile.py` 模块 docstring / C-5）。轴 A **不是**回写：它把「用哪个价落这条腿」的决定推迟到能拿到真值为止，一条腿仍然只被写一次。
3. **`min_consensus=1` 太严** —— 不是。它已经是最小非零值，且共识门禁本身有明确的保护意图。真正的缺陷在**逆势通路那条豁免路径被档位吃掉了**（E-3），修它不需要动任何一个阈值。

---

## 三、目标与非目标

### 目标

| 轴 | 一句话 | 优先级 |
|---|---|---|
| **A** | 外部平仓的价格与费率向交易所要，要不到再退回今天的本地合成——一条腿只写一次，写的时候用当时能拿到的最好真值 | P0 |
| **B** | 交易所侧出现应用不知道的持仓时，必须被发现、被说出来、并把该标的的开仓路径钉死；**绝不自动平仓、绝不自动接管** | P0 |
| **C** | 档位量化不得把「风控解出零」翻译成「开最小一格」；档位对风控层的杀伤力必须在**启动之前**说清楚 | P0 |
| **D** | 逆势通路的强制降档改用**档位语义**（掉一格），已在最低格即视为已降档并放行；**两把逐字同构的闸（`trend_verdict` 与 `signals`）同轮改、共用同一个纯核心谓词与同一把总闸**（`RV-P0-1`） | P0 |
| **E** | 限价挂单不再每轮撤重挂：价没动到阈值就留着，绝不撤掉再按同一个价格挂回去 | P1 |
| **F** | 终态对账不得无条件报「已对上」；对账卡带上轴 A 的结算覆盖率 | P1 |
| **G** | `actionCounts` 口径显式化；`quantizeErasedRounds` 接上读点 | P2 |

### 非目标（本轮明确不做，附理由）

1. **不动 `min_consensus` / `min_confidence` / `edge_gate_k_min` 任何一个阈值。** 本轮的判定是这些门槛本身没错，错的是它们的豁免路径与下游量化。调阈值会让轴 D 的效果无法归因。
2. **不接手兄弟 spec `ai-trade-edge-closed-loop-governor`（`docs/plans/ai-trade-edge-closed-loop-governor/spec.md`，未提交、未实现）的入场 edge 轴。** 相反：**本轮轴 A 是它的前置条件**——它的核心是把已实现 edge 回灌成先验，而已实现 edge 今天有 18 笔的价格是本地合成的（E-1）。先修水源，再修水管。
3. **不改 `fee_ledger` 的开仓腿即时记账口径**（见 E-8 第 1 条）。
4. **不做「自动接管孤儿持仓」。** 轴 B 只做侦测、告警与开仓封锁；接管必须是用户的一次显式点击。
5. **不改 `quantize_size_fraction` 本身。**（`RV-P1-6` 更正）它有 **6 个调用点**，不是两个：`decider.py:791`（决策器对模型自报仓位的吸附）、`reflection.py:61`（反思收缩）、`signals.py:409`（共识逆势降档）、`sizing.py:391` + `:393`（风控解算与 `scale_erased` 判据）、`trend_verdict.py:631`（**趋势基调逆势降档**）。v1 数漏的那两处恰恰是本轮要改的那两把闸——`RV-P0-1` 能藏住的直接原因就是这条错数。轴 C 的改动仍全部落在 `sizing.resolve_size_fraction` 内；轴 D 落在 `signals.py:407-412` 与 `trend_verdict.py:630-635` 两处，**函数本身一个字节不动**。
6. **不改 `AI_TRADE_DEFAULT_POSITION_STEP`（0.25）。** 只往选项表里**加**一档更细的 5%，并把「你选的这一档会让哪些机制失效」说出来。改默认值会让全仓上百条冻结断言与用户已落盘的偏好一起动。
7. **不新增任何 WS 事件类型。** 新读数一律走既有 `status` / `error` 两条通道的加法键（镜像 `liveness` / `truthSources` 两轮的既有姿态）。
8. **不做 DB 迁移。** 全部新读数走 `trades.explain_json`（free-form）与 `strategy_runs.metrics_json`（free-form）。

---

## 四、技术设计

### 4.0　六条贯穿全轴的原则

1. **每一条轴一把独立总闸，任一置回退档 ⇒ 该链路逐字节回到 `ca40b6b`。** 回退档必须是**默认值以外的那一档**能回退，也必须是**默认值本身**在关掉时能回退（见 §十一 回退矩阵）。
2. **判定与 IO 分离。** 每条轴的判据都住在一个**零 IO、绝不抛、可单测的纯核心模块**里；`executor` / `session` / `manager` 里只留取数与装配。
3. **真实下单行为的每一处改变都必须挂总闸**，且默认值要在 §八 逐条声明并说明为什么是那个默认。
4. **fail-safe 的方向是「照旧」，不是「不做」。** 轴 A 任何一步异常 ⇒ 立刻走今天的 `local_mark` 路径落账（**绝不把一条已经发生的平仓丢掉**）；轴 B 任何一步异常 ⇒ 不判孤儿（**误报会把真报一起废掉**，`discipline_view.py:14` 的成文纪律）。
5. **一条成交腿只被写一次。** 轴 A **不做**任何形式的「先落一条、之后回改」——回改会污染已经喂给 `trip_ledger` / `exit_attribution` / `experience` / `calibration` 的样本，而那是不可逆的。
6. **算出来的每一个数都要有读点。** 本轮新增的任何计数器，若在 §七 的变更清单里找不到一个前端落点，就是本轮不该加的东西（门禁 `G-X2` 用源码扫描冻结）。

### 4.A　轴 A：成交真值化（外部平仓改为向交易所要结算价）

#### A-1　今天的时序（问题所在）

```
monitor tick (每 live_auto_poll_interval_s=5s)
  └─ executor.reconcile(mark_px, ts)
       └─ _read_positions()                       # 账户级持仓（ca40b6b 起走单飞缓存）
       └─ _on_reconcile_ok(mark_px, positions, ts)
            if projected != flat and abs_pos <= 0 and 三条既有守卫全成立:
                _synthesize_external_close(mark_px, ts, source="external")   # ★ 价格 = 本地 mark_px
                realized_pnl_usd += _close_pnl(mark_px)
                projected = "flat"
```

#### A-2　新时序（引入 `settling` 中间态）

```
monitor tick
  └─ executor.reconcile(mark_px, ts)
       └─ _on_reconcile_ok(mark_px, positions, ts)
            ① self._settling is None 且判定成立
                 ⇒ self._settling = SettlementPending(detect_ts=ts, mark_px=mark_px,
                                                      deadline=ts + settlement_deadline_s,
                                                      entry_ts=..., direction=..., qty=...)
                   推 status 事件「交易所侧已平仓，正在取回结算价（最多 N 秒）」
                   ★ 本轮不改 projected / realized_pnl_usd / unrealized（立场保持不变）
            ② self._settling is not None
                 a. abs_pos > 0 再次出现  ⇒ 幻影：清 _settling，推 status「持仓仍在，已撤销结算等待」
                 b. 按 settlement_poll_interval_s 节流 ⇒ await self._settlement_fn()
                    rows → settlement.match_settlement(rows, query, claimed=self._settled_pos_ids, tol_s=…)
                    命中 ⇒ _finish_settling(fact)          # 按交易所口径落账
                 c. ts >= deadline ⇒ _finish_settling(None) # 按 _settling.mark_px 落账（逐字等于今天）
```

**【`RV-P0-3` 修订】`_finish_settling` 绝不是「复用六行」那么简单——它必须是幂等的，而且 `settling` 期间必须把出站平仓通路一起锁上。**

v1 的写法有一条灾难级的空档：`settling` 期间 `projected` 刻意保持不变（不变式 I-2），于是在这最多 90 秒里，**利润保护 / 持仓周期离场 / 模型 `close` / `flatten` / `stop` 仍会真的向交易所发平仓**。交易所回 `LIVE_NO_POSITION` ⇒ `_do_close` 走 `_settle_benign_close`（`executor.py:419` → `:663`），而它已经**按 `mark_px` 合成了一条 `source="oco"` 的腿**并执行了`realized_pnl_usd += self._close_pnl(ref_px)` + `projected = "flat"`。此刻 `_settling` 仍未决；下一 tick 命中结算再走一次立场复位六行 ⇒ **已实现盈亏被加了两次**。`_synthesize_external_close` 自己的首行守卫（`projected == "flat" ⇒ return []`，`executor.py:1063`）只挡住第二条 `trade`，**挡不住调用方那一行 `realized_pnl_usd +=`**。R-1 的三重保险（死线 / 收尾兜底 / 异常回落）一条都不覆盖这个形状。

故本轮把 A-2 的时序补成四条，缺一条即本轴不算做完：

```
monitor tick
  └─ executor.reconcile(mark_px, ts)
       └─ _on_reconcile_ok(mark_px, positions, ts)
            ⓪ self._settling is not None
                 ⇒ 跳过 gross_unrealized_usd / unrealized_usd 这两行的无条件重写（★ RV-P1-9）
                   —— 它们在 `abs_pos == 0` 时恒被写成 0，而 projected 还说有仓；
                      I-2 要求的「冻结在侦测那一刻」只能在这里施加，别处没有落点。
            ①②③ 同下（侦测 / 轮询 / 死线）
```

**互锁（`_settling_blocks_close()`，与既有 `_external_close_suppressed()` 逐字同形）**：

1. `_do_close` / `flatten` 在 `self._settling is not None` 时**直接短路**，回一条 status「该持仓已在交易所侧平掉，正在取回结算价，本轮不再发平仓单」，**不发任何交易所写请求**。这与 `_external_close_suppressed` 立的是同一条规矩（`executor.py:632-639`：「代价只是延后一轮」），方向也一致——**延后比编一个数诚实**。
2. `session` 侧的离场判定（利润保护 / 持仓周期 / 模型 close）读到该短路 ⇒ 记既有的`last_close_block`，**不新增降级码**（`RV-P0-5` 的同一条纪律：`settling` 是中间态不是拦截）。
3. `_finish_settling` **首行自检**：若此刻 `projected == "flat"` 或 `entry_qty_sz <= 0`（说明这笔仓已被别的通路落过账），则**只清 `_settling`、推一条 status、一个字节都不落**。门禁 `G-A13` 直接构造这个场景（先 `settling`、再从外部把 `projected` 打成 flat、再喂命中的结算行），断言 `realized_pnl_usd` 只被加过一次、`trades` 只多一条腿。
4. 只有在自检通过时，`_finish_settling(fact)` 才复用今天的 `_synthesize_external_close` + 立场复位六行，多传一个 `settlement` 形参。

#### A-3　纯核心 `live_auto/settlement.py`（新文件，零 IO、绝不抛）

```python
@dataclass(frozen=True)
class SettlementQuery:
    inst_id: str
    direction: str        # "long" | "short"（本地 projected）
    entry_ts: int         # 本地开仓腿 ts（秒）
    detect_ts: int        # 侦测到交易所已平仓的那一秒

@dataclass(frozen=True)
class SettlementFact:
    pos_id: str
    close_px: float
    gross_pnl_usd: float  # row["pnl"]（正=盈利）
    fee_usd: float        # row["fee"]（已由 _normalize_position_history 归一为「正=支出」）
    funding_usd: float
    liq_usd: float
    close_ts: int         # row["uTime"]（秒）

def match_settlement(rows: list[dict], query: SettlementQuery, *,
                     claimed: set[str], tol_s: int) -> SettlementFact | None
```

**匹配判据（全仓唯一一处，实盘 / 回测 / 单测共用）**，顺序即优先级：

1. `str(row["instId"]) == query.inst_id`
2. **方向一致性检查（`RV-P0-4` 修订：fail-open，不是 fail-closed）**：
   `d = str(row.get("direction") or "")`；**只有当 `d in ("long", "short")` 时**才要求`d == query.direction`，否则**这条判据整条跳过**。
   理由：`_normalize_position_history`（`trading.py:788`）写的是 `str(r.get("direction") or "")`——**缺字段即空串**；而 OKX 的 `positions-history.direction` 在净持仓模式下并不保证是 `long/short`，本仓库的 `pos_mode` 又是从交易所账户配置取的（`manager.py:2199` 读 `config["posMode"]`，`executor.py:962` 明确同时支持 `net_mode`）。v1 的严格相等在这类账户上会让 `match_settlement` **恒不命中**，而不命中的回落与今天**逐字相同**——于是整条轴 A 变成 100% 静默空转，`AC-1` 只会显示一个 `ratio=0`，没人能从中读出原因。**仓库里已有反例**：`resume` 的离线回读只按 `posId` 匹配、**刻意不用 `direction`**（`resume.py:383-384`）。
   丢掉这条判据不会放松安全性：判据 1（`instId`）、4/5（两个时间窗）、7（取最早）合起来已经把「上一笔往返」排除掉了，而**同一 `instId` 同一时间窗内同时存在多空两笔已结算持仓**在单仓不变式（本执行器全程只持一笔仓）下不可能发生。
3. `pos_id = str(row.get("posId") or "")`；**`pos_id == ""` ⇒ 本行直接淘汰**（`RV-P0-4`：归一化对缺字段回落空串，而空串永远不在 `claimed` 里，v1 的写法会把一条没有身份的结算行当成合法命中并认领）。非空时要求 `pos_id not in claimed` —— 一行结算**只能被认领一次**
4. `int(row["uTime"]) >= query.detect_ts - tol_s` —— 结算不得早于侦测（早于即属上一笔往返）
5. `int(row["cTime"]) <= query.entry_ts + tol_s` —— 开仓不得晚于本地开仓腿
6. `float(row["closeAvgPx"]) > 0` —— **绝不用 0 价入账**（`_normalize_position_history` 对缺字段回落 0.0）
7. 多行同时命中 ⇒ 取 `uTime` **最小**的一行（最早的那次结算）

`tol_s` 由 `ai_trade_settlement_match_tol_s` 提供（默认 60）。命中后调用方（`executor`）负责把 `pos_id` 加进 `self._settled_pos_ids`（`set`，按插入序封顶 256，超出即丢最旧）。**淘汰的安全性论证见 §6.3 I-3（v1 的论证是循环的，已按 `RV-P1-11` 改写）。**

同文件另提供：

```python
def settlement_meta_wire(fact: SettlementFact | None, *, mark_px: float,
                         waited_s: int, booked_open_fee_usd: float) -> dict
```
产出进 `explain.meta` 的加法键（**算不出即整键省略，绝不写 `null`**，沿用 `trip_ledger.trip_meta_wire` 的成文纪律）：

| 键 | 含义 |
|---|---|
| `settlementSource` | `"exchange"` / `"local_mark"` |
| `settlementPosId` | 交易所 `posId`（`exchange` 时才有） |
| `settlementCloseTs` | 交易所 `uTime`（秒） |
| `settlementWaitedS` | 从侦测到落账等了多少秒 |
| `markPxAtDetect` | 侦测那一刻的本地 `mark_px` |
| `settlementPriceGapBps` | `(close_px − mark_px) / mark_px × 1e4`（两者都有才写）——**这是本轮最重要的一个观测量**：它把「合成价差多少」从推断变成读数 |
| `exchangeGrossPnlUsd` / `exchangeFeeUsd` | 交易所口径的毛额与往返总费 |
| `bookedOpenFeeUsd` | 本地已记的开仓腿费（供 A-4 的残差核对） |

#### A-4　落账口径（`_synthesize_external_close` 新增 keyword-only 形参 `settlement`）

**三列，不是两列**（`RV-P0-5`：v1 把「总闸关 / `settlement_fn is None`」与「状态机跑过了但没拿到」并成一列，那一列却要求写三个加法键——与 `G-A3` / `G-A8` / `G-A10` 三条门禁**不可能同时为真**）：

| 量 | ⓐ 总闸关 或 `settlement_fn is None`（含 `live_auto` 全部路径） | ⓑ 状态机跑过了但没拿到（超时 / 匹配不中 / `settlement_fn` 抛异常） | ⓒ 命中结算 |
|---|---|---|---|
| 状态机 | **整条不存在** | 存在 | 存在 |
| `exit_price` | `ref_px`（= `mark_px`） | `_settling.mark_px` | `fact.close_px` |
| `pnl` | `self._close_pnl(ref_px)` | `self._close_pnl(_settling.mark_px)` | `settings.ai_trade_net_pnl_enabled` ⇒ `fact.gross_pnl_usd − fact.fee_usd`；否则（毛额口径）⇒ `fact.gross_pnl_usd` |
| `fee` | `trade_fee_fields(...)` | `trade_fee_fields(...)` | `fact.fee_usd − self.entry_fee_usd`，**为负即判不自洽 ⇒ 整条降级为 ⓑ** |
| `pnl_pct` | `self._close_return_pct(ref_px)` | 同左 | `self._close_return_pct(fact.close_px)` |
| `realized_pnl_usd` | `+= self._close_pnl(ref_px)` | 同左 | `+= pnl`（同上一行） |
| `explain.meta` | **零加法键**（逐字节回退，`G-A8` / `G-A10` 守） | `settlementSource="local_mark"` + `markPxAtDetect` + `settlementWaitedS` 三键 | 全部八键 |

**ⓐ 与 ⓑ 的区别是本轮最容易写错的一处**：ⓐ 必须是**零差异**（`live_auto` 与全部既有单测走的就是它，而 `_BASE_OPEN_KEYS` 那条键集冻结用例守着非 AI 执行器的成交形状）；ⓑ 是「我们真的去问了、没问到」，**必须留痕**——否则 `settlementCoverage.localMark` 分不清「没装配」与「问不到」，而这正是 `AC-1` 的分母。

**`pnl` 的判据是 `ai_trade_net_pnl_enabled`，不是「`fee_ledger` 已装配」（`RV-P0-2`）**：`ai_trade_fee_accounting_enabled`（`config.py:543`）与 `ai_trade_net_pnl_enabled`（`:547`）是**两把独立开关**，而 `trades.pnl` 是毛还是净**只由后者决定**——`reconcile.local_net_view` 的 `net_mode` 形参取的就是它（`cost_runtime._run_reconcile`），`net_mode=False` 时它会执行 `pnl - Σ腿费`（`reconcile.py:63`）把净额**再还原一次**。若按 v1 的「`fee_ledger` 已装配」判，在 `accounting=True & net_pnl=False` 的断面上会往一个毛额口径的会话里写一条净额 pnl ⇒ 对账差额被人为放大、`db/ai_auto_repositories._Trip.net_pnl` 与 `profitability` 的历史聚合二次扣费。

**`fee` 的残差有现成先例，且先例里那道守卫不能丢（`RV-P1-7`）**：`resume._offline_exit_trade`（`resume.py:418-433`）已经在做同一件事——`fee = 交易所往返总费 − Σ本地已记腿费`，随后 **`if fee < 0: return None`**（不自洽 ⇒ 宁可缺一笔并说明）。v1 的 `max(0.0, …)` 会把「本地已记的开仓腿费比交易所收的往返总费还大」这种**明确的不自洽**静默夹成 0——那正是本轮在治的那类假数字。本轮改为：为负 ⇒ 落 `local_mark`（ⓑ 列）并在 `explain.meta` 写 `settlementSource="local_mark"`、`settlementRejectReason="fee_residual_negative"`，让它在数据上可见。

**八个 `settlement*` 键的装配点在 `session`，不在 `live_auto`（`RV-P0-6`）**：`live_auto/explain.py` 与 `live_auto/executor.py` 是 `live_auto` 与 `ai_auto` **共用**的模块，而本仓库 `explain.meta` 的**唯一**装配点是 `session._inject_explain_meta`（`session.py:664-704`）——`_cost.apply_explain_meta` / `apply_trip_meta` 都从那里进，且**顺序敏感**（`apply_trip_meta` 明写「必须在 `exitSource` 打标之后」，`cost_runtime.py:526`）。把新键写进 `build_external_close_explain` 有两个后果：①`live_auto`（非 AI）的 `external_close` explain 形状变了；②造出第二个 `meta` 装配现场。

**修法（唯一）**：`executor` 落账时只把 `SettlementFact | None` + `waited_s` + `mark_px` 放进一个**轮级槽** `self._pending_settlement_meta`（逐字镜像 `session._pending_horizon_meta` 的既有范式：只打在平仓成交上、**消费即清空**），由 `session._inject_explain_meta` 在 `exitSource` 打标**之后**调 `settlement.settlement_meta_wire(...)` 并 `setdefault` 进去。`build_external_close_explain` **一个字节不改**。这样 ⓐ 列的「零加法键」是**构造性**成立的（`live_auto` 根本没有 `_inject_explain_meta` 这一步）。

**为什么费率取「往返总额减开仓腿」而不是直接覆盖两条腿**：开仓腿已经落过库、已经进过换手闸与成本模型，回改它违反原则 5。而 `trip_ledger.SettledTrip.fee_usd` 的定义本来就是 `open_fee_usd + close_fee`（`trip_ledger.py` 硬约束 1b），故把残差全部记在平仓腿上时，**往返口径是精确的**，只有腿间分摊是近似的——而全仓没有任何一个消费者按单腿费率做判定（`realizedCostBps` / `costErrorBps` / `reconcile` 全部是往返口径）。这一点必须写进 `_synthesize_external_close` 的 docstring。

**`fee` 的写法（`RV-P1-8`：改一个形参不够）**：`trade_fee_fields` 是 `**leg` 直通 `FeeLedger.on_trade`（`fee_ledger.py:130-149`），所以 `override_fee_usd` 必须**同轮**落到三处，缺一处即口径打架：

1. `trade_fee_fields(ledger, *, override_fee_usd=None, **leg)` —— `None` ⇒ 逐字回退；
2. `FeeLedger.on_trade(..., override_fee_usd=None)` —— 非 `None` 时 `fee = override_fee_usd`，并且 `self._session_fee_usd += fee` 累加的**也必须是它**（否则 `costSnapshot.cost` 会与这条腿的 `fee` 不一致）；
3. 返回的 `feeBps` 改为**按本腿名义值现算**（`override / notional × 1e4`）而不是 `leg_fee_bps(ord_type)` —— `explain_meta_fields`（`fee_ledger.py:157-166`）把 `feeUsd` 与 `feeBps` 一起写进 `meta`，两者不同源就会出现「费 0.31 而 bps 说 2.0」这种自相矛盾的一行。

`register()` 那一步**不受影响**：合成成交没有 `clOrdId`，`register` 首行就早退（`fee_ledger.py:158`「无 `clOrdId` 的合成成交不挂账」），故不会污染 fills 审计样本。

**资金费与强平罚金刻意不进本地账本**（`fact.funding_usd` / `fact.liq_usd` 只进 `explain.meta`）：`reconcile.build_reconciliation` 的 `exchangeComparable = realized + funding + liq` 这条可比口径**依赖本地不记这两项**（`reconcile.py:20-26`）。把它们记进来会让对账的差额恒等于它们本身。

#### A-5　取数缝（第八条可注入缝）

`LiveExecutor.__init__` 增 `settlement_fn: Callable[[], Awaitable[list[dict]]] | None = None`，**默认 `None` ⇒ live_auto 与全部既有单测逐字回退**（`_settling` 状态机在 `settlement_fn is None` 时**整条不存在**，`_on_reconcile_ok` 走今天的立即合成）。

装配点唯一：`ai_auto/manager.py::_build_executor` 的**函数体内**（`manager.py:2190-2213`）。**不新增形参**（`RV-P2-4`：该函数已是 6 个形参，超 `MAX_FUNCTION_PARAMS=5`；`creds` 与 `inst_id` 都已在作用域里，直接闭包构造即可），绑

```python
settlement_fn=lambda: fetch_position_history(creds, inst_id, limit=20)
```

`limit=20` 而不是 reconcile 的 100：这条取数是**高频、窄窗口**的（只关心刚刚那一笔），20 行足以覆盖任何现实的并发结算，且把请求体积压到最小。**`creds is None`（纸面 / 回测）⇒ 不传这条缝**，等价于总闸关。

节流：`ai_trade_settlement_poll_interval_s`（默认 8.0）。`_settling` 为 `None` 时**一个请求都不发**——镜像 `fee_ledger.audit_fills` 的 `has_pending()` 短路（`fee_ledger.py:359`）。

#### A-6　停手与暂停时的收尾

`session.stop()` / `session.pause()` 的既有收尾链里，在**真实资金平仓之后、落库之前**插一步：若 `executor._settling` 未决，走一次带硬超时的结算取数（复用 `ai_trade_reconcile_stop_timeout_s`，默认 3.0s），仍不命中 ⇒ 按 `local_mark` 立即落账。**绝不把一条已经发生的平仓留在 `settling` 里带进关停**——那会让这笔往返在历史详情里整条消失。

#### A-7　对回测的影响：零

`ai_auto/backtest.py` 走的是 `SimBroker`，根本不经 `LiveExecutor.reconcile`。轴 A 一行都不进回测链路。门禁 `G-A9` 用 sha256 冻结回测 runner 的产物。

### 4.B　轴 B：孤儿持仓侦测

#### B-1　纯核心 `ai_auto/orphan.py`（新文件）

```python
@dataclass(frozen=True)
class OrphanFacts:
    projected: str          # 本地立场
    abs_pos: float          # 交易所侧该标的净持仓绝对值
    settling: bool          # 是否正处于轴 A 的 settling 中间态
    fill_grace: bool        # 既有的「刚成交宽限」
    streak: int             # 已连续判定成立的轮数（调用方持有）

def classify_orphan(facts: OrphanFacts, *, confirm_rounds: int) -> str
    # -> "none" | "pending" | "confirmed"
```

判据（fail-closed：任一不确定即 `"none"`）：

1. `facts.settling` 或 `facts.fill_grace` 为真 ⇒ `"none"`（这两种状态下「交易所有仓、本地 flat」是**正常的中间态**）
2. `facts.projected != "flat"` ⇒ `"none"`（本地有仓，走轴 A 那一侧）
3. `not math.isfinite(facts.abs_pos)` 或 `facts.abs_pos <= 0` ⇒ `"none"`
4. `facts.streak + 1 >= confirm_rounds` ⇒ `"confirmed"`，否则 `"pending"`

`confirm_rounds` 默认 3（`ai_trade_orphan_confirm_rounds`，≈15s @ poll 5s）——它是 `_fill_grace`（`executor.py:735`）的**镜像**：那一条防的是「刚开的仓交易所还没跟上」，这一条防的是「刚下的开仓单成交了、本地还没 adopt」。两者是同一枚硬币的两面，注释里必须互相点名。

同文件另提供 `orphan_wire(view) -> dict` 与 `orphan_message(view) -> str`（**文案里不得出现字面 `**`**——后端文案在 JSX 里显字面星号是本仓库第 4 次复发的缺陷，门禁 `G-B6` 双侧断言）。

#### B-2　接线

- `executor.py`：`_on_reconcile_ok` 尾部增对称分支，持 `self._orphan_streak` 与 `self.orphan: dict | None`。**只写状态，不下任何单。**
- `session.py`：`_status_payload()` 增 `orphan` 键（`_status_payload` 是**同步方法**，`session.py:3397`——`orphan` 是进程内读数，零 DB 读，可以搭这班车；这一点必须在实现时复核，上一轮 F-15 已经为同形问题付过一次代价）。
- 首次转 `confirmed` ⇒ 推一条 `error` 事件 `AI_TRADE_ORPHAN_POSITION`（**幂等**：`self._orphan_alerted` 标志，`resume()` 里清零）。
- **开仓路径硬拒**：`session` 的开仓分支在 `orphan == "confirmed"` 时把 `degrade` 置 `orphan_position_blocked`。三处必须同轮改，缺一即静默：
  1. `open_funnel.LAYERS` 增 `"orphanPosition"`（插在 `"baseRuleMaBand"` 之后、`"horizon"` 之前——它是比任何纪律更靠前的**事实性**阻断）
  2. `open_funnel._DEGRADE_PREFIXES` 增 `("orphan_position", "orphanPosition")`
  3. `open_funnel.LAYER_LABELS` 增 `"orphanPosition": "交易所侧存在未接管持仓"`
  4. 前端 `apps/web/lib/aiTrade.ts::DEGRADE_REASON_LABELS` 增同码（该表用 `find(startsWith)`、**顺序敏感**，`orphan_position` 与既有任何前缀都不撞，排在表首即可）
  5. 前端 `apps/web/lib/aiTradeFunnel.ts` 的层序镜像同步

#### B-3　用户出口

- `POST /api/ai-trade/{group_id}/member/{inst_id}/adopt-orphan`（见 §五；`RV-P1-5` 已改正路径）：把该持仓按 `resume.py:367-400` 既有的采纳路径接进当前会话（它已经会读 `fetch_position_history` 建断面）。**必须二次确认**：请求体带 `confirm: true`，缺失即 `400`。
- 既有的手动平仓入口（`api/ai_auto.py` 的手动离场端点）不变。
- 告警文案给三条能真的点下去的补救：①「接管这笔持仓」②「到实盘面板手动平掉」③「停止该标的的自动交易」。

### 4.C　轴 C：档位分辨率

#### C-1　运行期（两把闸，落点唯一：`sizing.resolve_size_fraction`）

`SizingOutcome` 增 `floor_verdict: str = "none"`（值域 `none | solved_zero | below_gear_floor`）。

```python
    f_raw = min(entry, f_core) * brake * corr
    # ★ 新增（轴 C-1）：在吸附之前先判「这个解算结果该不该变成一张单」
    if policy.zero_is_veto and f_raw <= _EPS:
        return SizingOutcome(fraction=0.0, fraction_raw=0.0, floor_verdict="solved_zero", …)
    if policy.floor_ratio > 0 and 0 < f_raw < inputs.step * policy.floor_ratio:
        return SizingOutcome(fraction=0.0, fraction_raw=round(f_raw, _ROUND),
                             floor_verdict="below_gear_floor", …)
    fraction = quantize_size_fraction(f_raw, inputs.step)      # 以下逐字不动
```

`policy` 是新的 `SizingFloorPolicy`（frozen dataclass，两字段），由 `parse_sizing_rule` 的同一处从 settings 解析。`inputs.step <= 0`（连续档位）⇒ 第二条判据整条跳过（`step*ratio == 0`）。

- `ai_trade_sizing_zero_is_veto`：默认 **True**。理由：`corr_scale ∈ [0,1]`、`brake_mult ∈ (0,1]`，解出精确的 0 只有一个含义——「这一单的风险预算是零」。把它翻译成「开最小一格」不是保守化，是把一个明确的否决改成一次开仓。生产实测已经发生（E-2，DOGE 两轮）。这是本轮**唯一一处默认开启的真实下单行为收紧**，且它的方向只可能是「少开一单」。
- `ai_trade_sizing_floor_ratio`：默认 **0.0（关）**。理由：它会把「解出 0.19 而最小格是 0.5」这类断面也变成不开仓，杀伤面比上一条大得多，且合适的比值需要先有 `belowFloorVetoes` 的观测数据。默认 0 ⇒ 该判据逐字不存在。

`fraction == 0.0` 走的是**既有**的 `note_sizing` ⇒ `sizing_zeroed` 漏斗格（`open_funnel.py:229-241`，判据就是 `size_fraction is None or <= 0`，且**不进 `blocked`**），零新增漏斗层。

**【`RV-P1-4` 补】但只到这里是不够的——这一轮必须在 `_execute_decision` 之前停住。** `apply_size_fraction`（`sizing.py:471-479`）在 `fraction=0.0` 时执行 `replace(decision, size_fraction=0.0)`，**`action` 仍是 `open_*`**；这个决策继续往下走会撞进 `_do_open` 的 `sz <= 0 or sz < min_sz` 分支（`executor.py:333-335`），后果有两个，都不能接受：

1. **同一件事被记两次**：`openFunnel.sizing_zeroed` +1，`executor.dust_skips` 也 +1；
2. **产品对用户说了一句假话**：那条分支推的 status 是「**预算 / 杠杆过小**，单笔不足最小下单量」，而纪律自检 `volSizing` 行（`discipline_view.py:279-282`）会据 `dust_skips` 写「本会话已有 N 次因解算仓位低于交易所最小下单量而跳过开仓——提高总预算或该标的百分比可消除」。**风控解出零跟预算大小毫无关系**，照这条提示去加预算只会让下一次开得更大。

故 `SizingRuntime.resolve` 在 `outcome.floor_verdict != "none"` 时必须把决策**改成 `hold`**（与既有各闸的否决产物逐字同形），并在 `session` 侧记一条**不进 `blocked`** 的既有 `sizing_zeroed`；`dust_skips` 一个字节不动。门禁 `G-C7` 断言：`f_raw == 0` 的一轮里 `dust_skips` **保持不变**、且 status 事件里**不出现「预算」二字**。

新增计数（`sizing_runtime._Counters`，进 `aiStats.sizing`）：`solvedZeroVetoes` / `belowFloorVetoes`。

#### C-2　启动期披露（无行为改变、无总闸——它只是把已经为真的事说出来）

新纯核心 `ai_auto/gear_resolution.py`：

```python
@dataclass(frozen=True)
class GearResolution:
    step: float
    gears: int                       # len(position_ladder(step))
    min_gear: float
    erased: tuple[str, ...]          # 会被吸附回原值的机制 key
    survives: tuple[str, ...]

_MECHANISMS = (                      # (key, 中文名, 取系数的属性路径)
    ("brakeSlow",  "回撤刹车第一档",   "brake_rule.slow_size_mult"),
    ("contrarian", "逆势通路强制降档", "consensus_rule.contrarian_size_mult"),
    ("corrScale",  "相关性缩减",      None),      # 代表值 0.5（值域 [0,1]，无单一常数）
)

def describe_gear_resolution(step, *, brake_rule, consensus_rule) -> GearResolution
def gear_resolution_note(res: GearResolution) -> str | None    # 无 erased ⇒ None
```

判据（对每个机制的系数 `m`）：**这个系数作用在最小一格上会不会被吸附回原值**

```python
erased = quantize_size_fraction(min_gear * m, step) >= min_gear - _EPS
```

`step <= 0`（连续）⇒ `erased` 恒空。注意判据取的是**最小一格**而不是任意仓位：最小格是「必然会遇到」的那一格，且 `erased` 在最小格上成立即等价于「本机制存在完全失效的断面」。

三个落点（**缺任何一个即本条不算做完**）：

1. **启动弹窗**（`AiTradeLaunchModal.tsx:1207-1226` 那一段）：档位选择器下方的说明行今天只列可用档位；改为在 `erased` 非空时变 `warn` 色并逐条列出：「按当前档位，回撤刹车第一档（×0.5）、逆势通路强制降档（×0.5）、相关性缩减 在**最小一格上**会被吸附回原值，即不生效。改用「每档 25%」或更细可让它们生效。」——**这段文字必须在点「启动」之前就出现**，不是启动之后的回执。
2. **纪律自检 `volSizing` 行**（`discipline_view._vol_sizing_row`）：`note` 追加同一句 + 运行期读数「本会话已有 N/M 次缩减被档位吸附抹掉」（N=`quantizeErasedRounds`，M=`scaledRounds`），`tone="warn"`。这条 note 与既有的 `dust` note **并存**（两件不同的事），拼接顺序固定：dust 在前、档位在后。
3. **`resolved_echo.discipline.adjustments`**：追加一条 `{"key": "positionStepPct", "kind": "gear_resolution", "detail": …}` 的如实回执（该数组已有 R-1 夹紧的先例）。

#### C-3　档位选项加一档

`apps/web/lib/aiTrade.ts::AI_TRADE_POSITION_STEP_OPTIONS` 在表首插入 `{ value: 0.05, label: "每档 5%（5/10/…/100%）" }`。`AI_TRADE_DEFAULT_POSITION_STEP` **不动**（0.25）。后端无对应校验（`position_step_pct` 是自由浮点），零后端改动。

### 4.D　轴 D：逆势通路的强制降档改用档位语义（**两把闸，不是一把**）

**【`RV-P0-1` 修订】落点是两处，而且上游那一处先咬。** v1 只写了 `signals.resolve_consensus_gate`；但 `trend_verdict.resolve_trend_gate` 的降档段（`trend_verdict.py:630-635`）与它是**逐字同构**的，第 `633` 行与 `signals.py:411` 是同一行字节，而 `session.py:1877` 调前者、`:1878` 才调后者。只改下游那一把，逆势开仓会在 `openFunnel` 的 `trendBias` 那一格被 `counter_trend_size` 拦死，**`AC-5` 结构上不可达**（详见 §二 E-3 的评审补充与 `RV-E1` / `RV-E2`）。

#### D-0　共用的纯核心谓词（新增，避免第三份镜像）

两把闸的降档判定下沉到 `ai_auto/gear_resolution.py`（与 C-2 同文件，它本来就是「档位语义」的唯一真源）：

```python
def downgrade_one_gear(proposed: float, *, step: float, size_mult: float,
                       gear_downgrade: bool) -> tuple[float, str]:
    """返回 (size, lane)；lane ∈ {gear, mult, floor_is_min}。纯函数、零 IO、绝不抛。"""
```

**两把闸必须调同一个函数**——这是本轮唯一能防住「下次又只改一半」的结构性手段。`_nearest_gear_index` 也住在这里，不在 `signals.py`。

#### D-1　两个接线点（同一把总闸）

| 闸 | 文件 | 规则字段 | 由谁置 True |
|---|---|---|---|
| 趋势基调·逆势降档 | `trend_verdict.py:630-635` | `TrendVerdictRule.gear_downgrade: bool = False` | `trend_rule_from_settings` |
| 共识·逆势降档 | `signals.py:407-412` | `ConsensusRule.gear_downgrade: bool = False` | `consensus_rule_from_settings` |

两者共用**同一把**总闸 `ai_trade_contrarian_gear_downgrade`（默认 **True**）——**刻意不拆成两把**：它们是同一个缺陷的两个副本，分开回退只会造出「一半修好一半没修」这种最难归因的状态。两个 `gear_downgrade` 字段的**默认值都是 `False`**，使既有全部单测与回测逐字回退。

`downgrade_one_gear` 的内部（唯一真源）：

```python
    if not gear_downgrade or step <= 0:
        return quantize_size_fraction(proposed * size_mult, step), "mult"
    ladder = position_ladder(step)
    idx = _nearest_gear_index(ladder, proposed)            # -1 = 不在阶梯上
    if idx < 0:
        return quantize_size_fraction(proposed * size_mult, step), "mult"   # 防御，不该发生
    if idx == 0:
        # ★ step >= 1.0（每次全仓）时最低格就是 100%，「已降档」等于「不降档」——
        #   此时退回今天的否决（见 §十三 Q2 的评审裁定，门禁 G-D4 冻结）。
        return (ladder[0], "floor_is_min") if step < 1.0 else (proposed, "mult")
    return ladder[idx - 1], "gear"
```

两个接线点里的判定段（**逐字相同的两份，因为两把闸的其余上下文不同**）：

```python
    proposed = float(decision.size_fraction or 0.0)        # trend_verdict 侧走 _finite(...) or 0.0
    size, lane_note = downgrade_one_gear(
        proposed, step=position_step, size_mult=rule.<contrarian|counter>_size_mult,
        gear_downgrade=rule.gear_downgrade)
    if size <= 0 or size > proposed:                       # 真正的异常才否决
        return _veto(...)
    if lane_note == "mult" and rule.<...>_size_mult < 1.0 and size >= proposed:
        return _veto(...)                                  # 逐字保留今天的第三条判据
```

**`lane_note == "floor_is_min"` 时放行**是本轴的核心判定，理由必须写进 docstring：不变式 2 说的是「逆势通路只可能比**原否决**更宽，绝不比它更窄，也绝不放大仓位」。今天的写法在「已经是最小一格」这个断面上把它变成了**更窄**（直接退回否决），而此时 `size == proposed == min_gear`，既没有放大仓位，也确实是本通路能给出的最小注——它满足不变式 2 的两个条款，不满足的只是「必须严格小于」这个**实现细节**。生产实测这条实现细节让整条通路 2373 轮零放行（E-3）。

两侧 wire 各增三键：`sizeGearBefore` / `sizeGearAfter`（阶梯序号，`-1` 表示不在阶梯上）/ `downgradeLane ∈ {gear, mult, floor_is_min}`——`consensus` wire 与 `trendGate` wire （`session.py:2432-2433` 的 `trendGate` 兄弟键）**各写一份**，因为它们是两次不同的判定，合成一份会让「到底是哪一把闸放的行」重新不可回答。

**轴 D-2（观测）**：五个细分否决码今天只能靠 `decisionsDigest` 的文本残留去数。新增会话级五格计数器，写点唯一（`session` 记 `degrade` 的那一处，按码前缀分派），落 `aiStats.consensus.contrarianBlocked = {opposed, quota, conf, evidence, size}`，读点两处：纪律自检 `consensus` 行的 note、统计详情页的方向平衡卡。**不改 `discipline_runtime._DEGRADE_PREFIXES` 的归类**（五码继续归 `consensus` 那一格），只是并排多一份细分——归类真源仍然只有一处。

### 4.E　轴 E：限价挂单寿命与重挂纪律

**【`RV-P1-3` 前置修订：先指认路径，再设计守卫】** v1 把 E-4 的「5 次逐字同价重挂」直接翻译成「加一条 `min_reprice_bps` 阈值」，但**同价不撤不挂的守卫今天就已经存在，而且比它更严**：`_prepare_resting_slot`（`executor.py:465-478`）在 `is_same_intent(order, intent, side, px)` 为真时返回 `"skipped"` 并推「同向同价挂单仍在排队，本轮保留原单不撤不重挂」，`is_same_intent`（`resting.py:97-104`）用的是 `float(px) == float(order.px)` **逐字相等**。

所以那 5 次**一定不是**从 cancel-replace 这条路上来的。实现节点**必须先做这件事**，它是轴 E 的准入条件：

> **E-0（前置调查，不写一行产品代码）**：在 `live_order_audit` 上把那 5 次的 `(cl_ord_id, created_ts, canceled_ts, reason)` 逐条拉出来，判定它们各自属于以下哪一条路径——①`ai_trade_limit_order_ttl_s`（默认 **300s**）到期后 `is_expired` 撤单、下一轮重挂；②Agent 的 `cancel_order` 动作（`cancel_resting`）；③`_do_close` 的预撤单；④意图从 `open` 变 `close`（`is_same_intent` 判 `intent` 不等 ⇒ 不走幂等）。**调查结论写进 spec 的 E-0 一节，再决定 `plan_resting_action` 挂在哪个调用点上。**若结论是①（TTL 到期），那 `min_reprice_bps` 这条判据对它**一点用都没有**——单子已经没了，没有什么可以「保留」，正解是「TTL 到期时若价没动到阈值则**原价续挂**」，那是一条完全不同的改动。

新纯核心 `live_auto/resting_policy.py`：

```python
@dataclass(frozen=True)
class RestingRule:
    min_reprice_bps: float = 8.0     # 价差不到这个数就不重挂
    max_age_s: int = 240             # ★ RV-P1-2：必须 < ai_trade_limit_order_ttl_s（默认 300s），
                                     #   否则 is_expired 先把单子撤掉，本判据永远轮不到（v1 写 1800 = 死配置）
    enabled: bool = False            # ★ 默认 False ⇒ 逐字回退今天

@dataclass(frozen=True)
class RestingOrder:
    cl_ord_id: str
    side: str
    px: float
    placed_ts: int

@dataclass(frozen=True)
class RestingIntent:
    side: str | None                 # None = 本轮不想开了
    px: float | None

def plan_resting_action(cur: RestingOrder, want: RestingIntent, rule: RestingRule,
                        now: int) -> str:      # "keep" | "replace" | "cancel"
```

判据（顺序即优先级，fail-open 到 `"replace"`——**任何不确定都退回今天的行为**）：

1. `not rule.enabled` ⇒ `"replace"`（总闸关，逐字回退）
2. `want.side is None` 或 `want.side != cur.side` ⇒ `"cancel"`
3. `want.px` 非有限正数 或 `cur.px` 非有限正数 ⇒ `"replace"`
4. `now - cur.placed_ts >= rule.max_age_s` ⇒ `"replace"`
5. `abs(want.px - cur.px) / cur.px * 1e4 < rule.min_reprice_bps` ⇒ **`"keep"`**（不发任何请求）
6. 其余 ⇒ `"replace"`

`"keep"` 分支在 `executor` 的限价路径里意味着：**本轮既不撤单也不下单**，直接返回今天「已有 resting 单」的那条既有短路（`sizing.restingSkips` 计数器已存在，语义是「因为有挂单而跳过本轮开仓」——**不复用它**，另开 `restingKept`）。

新增计数进 `aiStats.sizing`：`restingKept` / `restingRepriced` / `restingCancelled`。

**跨字段不变式（`RV-P1-2`，`config.py` 的 `model_validator` 里断言，与既有 `alert_pending_ttl_s ≥ 2 × live_auto_poll_interval_s` 同一范式）**：`ai_trade_resting_max_age_s < ai_trade_limit_order_ttl_s`。违反即启动期报错，**绝不允许一条永远不会被求值的配置存在**。

**默认 `False` 的理由**：这是一次真实下单行为的改变（挂单会活得更久 ⇒ 成交概率与成交价分布都变），且 8 bps / 240s 这两个数字今天没有生产依据——`min_reprice_bps` 的合理值应当由本轮上线后的 `restingRepriced` 分布反推。**观测半（三个计数器与漏斗的 `expired` 格）默认开**，先把数据攒出来。

### 4.F　轴 F：对账口径

#### F-1　终态判定（`reconcile._status`）

```python
def _status(diff: float, tol: float, mismatch: bool, final: bool,
            *, strict_final: bool) -> str:
    if final:
        if mismatch:
            return "partial"
        if strict_final and abs(diff) > tol:
            return "drift"          # ★ 新：样本对齐却对不上账，就是对不上账
        return "ok"
    return "drift" if abs(diff) > tol else "ok"
```

`strict_final` 由 `ai_trade_reconcile_final_verdict_strict`（默认 **True**）经 `ReconcileParams` 传入。关 ⇒ 逐字回退。

**为什么这不会重新引入 P1-5 的伪 drift**：P1-5 担心的是「刚平的那一笔 `uTime` 有结算延迟 ⇒ 终态少一笔」，而「少一笔」的观测特征恰是 `samples != localTrips`，已由 `mismatch ⇒ partial` 单独接住。实测 6 条被误判为 `ok` 的记录**全部 `mismatch=False`**（E-5），即样本数完全对齐、差额纯粹来自价格与费率口径——正是本轮轴 A 要修的那个东西。**这两条必须一起上**：只上 F-1 会让终态屏突然多出 6 条红；只上轴 A 而不上 F-1，修好的效果在终态屏上看不见。

#### F-2　容差按本金收紧（默认关）

`tol = max(rel_tol × |comparable|, min(abs_tol_usd, capital_usd × capital_tol_ratio))`，`ai_trade_reconcile_capital_tol_ratio` 默认 **0.0 ⇒ 退化为 `max(rel_tol × |comparable|, abs_tol_usd)`，逐字等于今天**。理由：100 USDT 本金上 `abs_tol_usd=0.5` 就是 0.5% 的本金，偏宽；但轴 A 落地后差额本来就该收敛到接近零，此刻同时收紧两个判据会让归因不可能。留作评审可决的开关，建议上线后据 `settlementCoverage` 的实测再定。`capital_usd` 由 `cost_runtime` 从 `executor.budget_usd` 取，缺失 ⇒ 该项不参与 `min`。

#### F-3　结算覆盖率与成因表

- `build_reconciliation` 新增 keyword-only 形参 `settlement: dict | None`，产出加法键 `settlementCoverage = {"exchange": N, "localMark": M, "ratio": N/(N+M)}`（`N+M == 0` ⇒ 整键省略）。计数由 `executor` 持有、`cost_runtime._run_reconcile` 取。
- `drift_message` 的成因表从一条扩到三条，**顺序按本轮实测的可能性排**：①外部平仓按本地标记价入账（附 `settlementCoverage.localMark` 的实际笔数）②交易所侧存在未接管持仓（附 `coverageMismatch`）③该标的上存在非本会话的手工持仓。文案里**不得出现字面 `**`**。

#### F-4　前端（**已按 `RV-P1-1` 改正：v1 描述的那个改动并不存在**）

核实结果：`describeReconcile`（`apps/web/lib/aiTradeCost.ts:114-127`）只有 `unavailable` 与 `partial` 两条早退，**`ok` 与 `drift` 共用**最后那条 `本地净额与交易所可比口径差 {diff}{ratio}`——**差额今天就在屏幕上**。E-5 那 6 条记录呈现给用户的是「✅ 已对上」与「差 +1.528 USDT（3.4%）」并排，**徽章与它下面那行字互相打脸**。所以 v1 的「`ok` 分支改为恒显示差额」是一个**不存在的改动**，删除。

本轮 F-4 实际要做的只有两件，都是措辞与新读数：

1. `ok` 分支的 detail 收敛为「本地净额与交易所可比口径差 {diff}{ratio}，**在容差 {tol} 内**」——把「为什么这也叫已对上」当场答掉（F-1 落地后 `ok` 就真的意味着「在容差内」了）。`tol` 需要 `build_reconciliation` 把它作为加法键 `tolUsd` 一并下发（今天 wire 里没有）。
2. 追加一行结算覆盖率：「外部平仓中 N/(N+M) 拿到了交易所结算价」，`settlementCoverage` 整键缺席 ⇒ 整行不渲染。

### 4.G　轴 G：观测口径

- **G-1**：`_compute_ai_stats` 的返回值增 `sampleRounds: int`（= `len(decisions)`）与 `sampleScope: "最近 N 轮决策"`（N 取 `_decisions` 环形容量常量，**不硬编码 50**）。两处前端读点（`AiTradeStatsLive` 与 `AiTradeStatsDetail` 的动作分布）必须把口径写进标签：「动作分布（最近 50 轮）」而不是「动作分布」。`decisionRounds` 与 `openFunnel.scope`（"本会话全程累计"）并排时，两个分母各自带标签。
- **G-2**：`aiStats.sizing.quantizeErasedRounds` 接上两个读点：C-2 的纪律自检 `volSizing` 行，以及统计详情页仓位卡新增一行「档位吸附抹掉了 N/M 次缩减」（`scaledRounds == 0` ⇒ 整行不渲染，绝不显示 `0/0`）。

---

## 五、接口设计

### 5.1　REST（新增 1 个端点）

**【`RV-P1-5` 修订：路径必须与既有五个成员端点同构】** `api/ai_auto.py` 里的成员级端点一律是 `/ai-trade/{group_id}/member/{inst_id}/…`（`:576` stop、`:585` exit、`:640` relink、`:670` backstop/repair）。v1 写的 `/api/ai-trade/member/{instId}/adopt-orphan` 有两处不同构：①**丢了 `group_id`**——多标的分组是既有能力，同一 `instId` 可以同时出现在两个运行中的分组里，「不在任何运行中的分组内」这个 404 语义在那时是错的；②路径参数用了驼峰 `{instId}`，而既有全部是 `{inst_id}`。

```
POST /api/ai-trade/{group_id}/member/{inst_id}/adopt-orphan
Body: {"confirm": true}
200: {"instId": "...", "adopted": true, "qty": 2.47, "avgPx": 100.89,
      "posSide": "long", "entryTs": 1788282202, "source": "exchange"}
400: confirm 缺失或非 true              -> {"detail": {"code": "CONFIRM_REQUIRED", ...}}
404: group_id / inst_id 不在运行中的分组 -> {"detail": {"code": "MEMBER_NOT_FOUND", ...}}
409: 当前不处于 orphan=confirmed 状态    -> {"detail": {"code": "NO_ORPHAN", ...}}
502: 向交易所取持仓断面失败               -> {"detail": {"code": "OKX_UNREACHABLE", ...}}
```

**不声明 `response_model`**（本仓库 F-11：`response_model` 会静默剔除加法键，`/intel/documents/health` 已经为此付过一次代价）。`204` 不适用（本端点有响应体）。顺带一条指针（细节以黑板为准，此处**不复述**以免漂移）：黑板 `SD-DEBT-204` 是一条**本轮之外**的独立欠账，实现节点须**单独出一个 `fix:` 提交**，绝不混进本特性的暂存区（见 §十 R-12 / §十四 `N-5`）。

### 5.2　WebSocket（零新事件类型）

| 通道 | 加法键 | 形状 |
|---|---|---|
| `status.member` | `settling` | `{"active": true, "detectTs": 1788…, "deadlineTs": 1788…, "waitedS": 12, "markPx": 100.52}`；无 ⇒ 整键省略 |
| `status.member` | `orphan` | `{"state": "confirmed", "qty": 2.47, "avgPx": 100.89, "uplUsd": -1.2, "sinceTs": 1788…}`；`state == "none"` ⇒ 整键省略 |
| `error` | `AI_TRADE_ORPHAN_POSITION` | 既有 `{"code", "message"}` 形状；`message` 由 `orphan.orphan_message` 产出 |
| `error` | `AI_TRADE_PNL_RECONCILE_DRIFT` | 既有 code，`message` 换成 F-3 的三条成因表 |
| `trade` | `explain.meta.settlement*` 八键（+ 不自洽时的 `settlementRejectReason`） | 见 A-3 / A-4；**算不出即整键省略**。装配点是 `session._inject_explain_meta`，**不是** `live_auto/explain.py`（`RV-P0-6`） |

`status` 的两个新块都是**进程内读数、零 DB 读**，可以搭 `session._status_payload()`（**已核实为同步方法**，`session.py:3404`——`RV-P2-1` 更正了 v1 写的 3397）这班车。`F-15` 记的正是「往一个同步 payload 里塞 DB 读」这个坑。

### 5.3　`aiStats` 加法键汇总

```
aiStats.reconcile.settlementCoverage    = {exchange, localMark, ratio}     # 轴 A/F
aiStats.consensus.contrarianBlocked     = {opposed, quota, conf, evidence, size}  # 轴 D-2
aiStats.sizing.solvedZeroVetoes         = int                              # 轴 C-1
aiStats.sizing.belowFloorVetoes         = int                              # 轴 C-1
aiStats.sizing.restingKept              = int                              # 轴 E
aiStats.sizing.restingRepriced          = int                              # 轴 E
aiStats.sizing.restingCancelled         = int                              # 轴 E
aiStats.sampleRounds / aiStats.sampleScope                                 # 轴 G-1
```

全部经既有的 `merge_sizing_stats` / `merge_cost_stats`（**任一键冲突即抛**，`stats_ext.py:55-63`）并入，不新建合并点。

### 5.4　CLI / 脚本

`docs/plans/ai-trade-fill-truth-and-effective-controls/evidence/audit.py`（本轮随 spec 交付，见 §九 A0）：

```
python evidence/audit.py db        # 只读生产库，复算 §二 的全部读数
python evidence/audit.py settle    # 逐笔列出 external 平仓与它的止损价重算差
python evidence/audit.py --assert  # 全部读数与 spec 表格一致则退出 0，否则 1
```

---

## 六、数据模型

**零 DB 迁移。** 不新建表、不加列、不碰 `db/session.py` 的 `_ensure_*_columns` 家族。

### 6.1　落库（复用既有 free-form 列）

| 位置 | 键 | 写点 | 读点 |
|---|---|---|---|
| `trades.explain_json` → `.meta` | `settlementSource` / `settlementPosId` / `settlementCloseTs` / `settlementWaitedS` / `markPxAtDetect` / `settlementPriceGapBps` / `exchangeGrossPnlUsd` / `exchangeFeeUsd` / `bookedOpenFeeUsd` / `settlementRejectReason` | **`session._inject_explain_meta`**（在 `exitSource` 打标之后 `setdefault`），数据由 `executor._pending_settlement_meta` 这个**轮级槽**递过来——**绝不**写在 `build_external_close_explain` 里（`RV-P0-6`：那是 `live_auto` 共用模块，且会造出第二个 `meta` 装配现场） | 历史详情的成交表 + `evidence/audit.py` |
| `trades.price` / `.pnl` / `.fee` | 值本身 | 同上（**一次写入，绝不回改**） | 全链路既有读点，零改动 |
| `strategy_runs.metrics_json` → `.aiStats` | §5.3 的八组加法键 | `session.snapshot_run_metrics` 既有装配 | 统计详情页 |
| `strategy_runs.config_json` → `.discipline.adjustments` | C-2 的 `gear_resolution` 回执 | `manager` 既有装配点 | 纪律条 |

`trades.reason` 对 `external_close` 仍逐字写既有短码 `"external_close"`（上一轮 `RV-P0-5` 冻结了五个既有短码，本轮**一个字节不动**）。

### 6.2　进程内结构（随会话回收）

| 结构 | 归属 | 容量 |
|---|---|---|
| `SettlementPending`（`detect_ts` / `mark_px` / `deadline` / `entry_ts` / `direction` / `last_poll_wall`） | `LiveExecutor._settling` | 至多 1 |
| `_settled_pos_ids: set[str]` | `LiveExecutor` | 256（按插入序 FIFO 淘汰） |
| `_orphan_streak: int` / `orphan: dict \| None` / `_orphan_alerted: bool` | `LiveExecutor` | O(1) |
| `RestingRule` | `LiveExecutor`（构造期解析一次） | O(1) |
| `SizingFloorPolicy` | `SizingRuntime`（构造期解析一次） | O(1) |
| `contrarian_blocked: dict[str, int]`（五格） | `AiTradeSession` | O(1) |
| `_pending_settlement_meta: dict \| None`（轮级槽，**消费即清空**，镜像 `session._pending_horizon_meta`） | `LiveExecutor` → `session._inject_explain_meta` | 至多 1 |

### 6.3　不变式

- **I-1**：一条成交腿只被写一次。`_settling` 期间**不落任何库、不推任何 `trade` 事件**；落账时一次性走既有的 `_record_trade` → `_dispatch` → `_persist_trade` 全链。
- **I-2**：`_settling` 期间 `projected` 保持不变、`realized_pnl_usd` 保持不变、`unrealized_usd` / `gross_unrealized_usd` 冻结在侦测那一刻的值（**不再随 `mark_px` 波动**——仓位已经不在了，让它继续跳是第二个假数字）。
  **施加落点是唯一的且必须显式写出（`RV-P1-9`）**：`_on_reconcile_ok` 在幻影分支**之前**就无条件执行 `self.gross_unrealized_usd = self._net_upl(positions)` 与 `self.unrealized_usd = self._net_unrealized(...)`（`executor.py:735-736`），而交易所侧已空时 `_net_upl` 恒返回 0——**不显式跳过这两行，I-2 就是一句写在纸上的空话**，界面会显示「持有中 / 浮盈 0.00」。门禁 `G-A4` 的断言必须包含「`settling` 三轮后 `unrealized_usd` 仍等于侦测那一刻的值」。
- **I-2b（`RV-P0-3` 新增）**：`_settling` 非空 ⇒ **不发任何出站平仓请求**（`_do_close` / `flatten` 直接短路），且 `_finish_settling` 首行自检 `projected == "flat" or entry_qty_sz <= 0` ⇒ 只清状态、一个字节不落。**这两条缺任一条，同一笔往返的已实现盈亏就会被加两次**（`_settle_benign_close` 与`_finish_settling` 各加一次，而 `_synthesize_external_close` 的首行守卫只挡 `trade`、挡不住调用方那一行 `realized_pnl_usd +=`）。
- **I-3（`RV-P1-11` 改写，v1 的论证是循环的）**：v1 说「重复认领会被判据 3 拦下」，而判据 3 读的恰恰是被淘汰掉的那个集合——它证明不了自己。真正的安全性来自另一条事实：**一笔往返只会进入 `settling` 一次**（入口条件是 `projected != "flat"`，落账即归 flat 且 `entry_qty_sz = 0`），而 OKX 的 `posId` 对一次持仓生命周期唯一。故「同一 `posId` 被认领两次」需要它在 256 笔结算之后**再次出现在 positions-history 里且本地又恰好处于 settling**——`limit=20` 的窄窗口取数下不可能。`_settled_pos_ids` 因此只是一层**廉价的额外保险**，不是正确性的承重墙；淘汰后最坏退化为按 `local_mark` 落账，**不会造成错账**。
- **I-4**：轴 B 只写状态与事件，**不产生任何交易所写请求**。`adopt-orphan` 是唯一会改立场的入口，且必须由用户点击。
- **I-5**：`aiStats` 的每一个新键都必须在 `apps/web` 里至少有一个非测试读点（门禁 `G-X2` 源码扫描）。

---

## 七、文件 / 模块变更计划

### 7.1　后端新增（4 个纯核心源文件 + 10 个测试文件）

| 文件 | 意图（一句话） |
|---|---|
| `services/quant/app/live_auto/settlement.py` | 结算匹配纯核心：`SettlementQuery` / `SettlementFact` / `match_settlement` / `settlement_meta_wire`（零 IO、绝不抛、无 `raise`） |
| `services/quant/app/live_auto/resting_policy.py` | 挂单去留纯核心：`RestingRule` / `plan_resting_action` 三值判定，fail-open 到 `replace` |
| `services/quant/app/ai_auto/orphan.py` | 孤儿持仓纯核心：`classify_orphan` 三态 + `orphan_wire` + `orphan_message`（fail-closed 到 `none`） |
| `services/quant/app/ai_auto/gear_resolution.py` | 档位分辨率纯核心：`describe_gear_resolution` + `gear_resolution_note` + **`downgrade_one_gear` / `_nearest_gear_index`**（`RV-P0-1`：两把逆势闸共用的唯一降档谓词，**绝不在 `signals.py` 与 `trend_verdict.py` 各写一份**），判据「系数作用在最小一格上会不会被吸附回原值」 |
| `services/quant/tests/test_settlement_core.py` | `match_settlement` 七条判据 + `settlement_meta_wire` 省略键 |
| `services/quant/tests/test_external_close_settlement.py` | executor 的 `settling` 状态机（命中 / 超时 / 幻影撤销 / 异常回退 / 停手收尾） |
| `services/quant/tests/test_orphan_position.py` | 三态判定 + 开仓硬拒 + 告警幂等 + adopt 端点四条错误码 |
| `services/quant/tests/test_sizing_floor_policy.py` | `solved_zero` / `below_gear_floor` / 两闸关时逐字回退 |
| `services/quant/tests/test_contrarian_gear_downgrade.py` | 掉一格 / 已在最低格放行 / `step >= 1.0` 时最低格**不**放行（`G-D4`）/ 连续档位退回乘性 / 闸关逐字回退；**`signals` 与 `trend_verdict` 两侧各跑一遍同一张真值表**（`G-D5`） |
| `services/quant/tests/test_resting_policy.py` | 六条判据的真值表 + 闸关恒 `replace` |
| `services/quant/tests/test_reconcile_final_verdict.py` | `final + mismatch=False + 超容差 ⇒ drift`；`mismatch ⇒ partial` 不变；`tolUsd` 加法键下发 |
| `services/quant/tests/test_settlement_double_book.py` | **`RV-P0-3` 专用**：`settling` 期间 `_do_close` / `flatten` 被短路；`_settle_benign_close` 抢先落账后 `_finish_settling` 只清状态不重复加 `realized_pnl_usd`（`G-A13`） |
| `services/quant/tests/test_settlement_net_basis.py` | **`RV-P0-2` 专用**：`accounting=True & net_pnl=False` 时命中结算落的是**毛额** pnl，`local_net_view(net_mode=False)` 还原后与交易所可比口径一致（`G-A14`） |
| `services/quant/tests/test_gear_resolution.py` | 0.5 / 0.25 / 0.05 / 连续 四档下的 `erased` 集合 |
| `services/quant/tests/test_ai_trade_fill_truth_wiring.py` | 15 项设置的 `.env.example` 双向登记 + 全闸关的零加法断言 |
| `services/quant/tests/test_ai_trade_stats_scope.py` | `sampleRounds` / `sampleScope` + 新键并入不冲突 |

### 7.2　后端修改（**18** 个源文件 + 仓库根 `.env.example`）

| 文件 | 意图 |
|---|---|
| `live_auto/executor.py` | `settling` 状态机（含 `RV-P1-9` 的 unrealized 冻结与 `RV-P0-3` 的 `_settling_blocks_close()` 互锁 + `_finish_settling` 幂等自检）+ 孤儿对称分支 + `settlement_fn` 注入缝 + `_pending_settlement_meta` 轮级槽 + 挂单去留接线 + 三组计数器 |
| ~~`live_auto/explain.py`~~ | **`RV-P0-6`：本文件一个字节不改。** 八个 meta 键的装配点是 `session._inject_explain_meta`，不是这个 `live_auto` 共用模块 |
| `live_auto/fee_ledger.py` | **三处同轮改**（`RV-P1-8`）：`trade_fee_fields` 增 `override_fee_usd`、`FeeLedger.on_trade` 同增并让 `_session_fee_usd` 累加同一个数、`feeBps` 改按本腿名义值现算；`None` ⇒ 三处逐字回退 |
| `ai_auto/manager.py` | 在 `_build_executor` **函数体内**绑 `settlement_fn`（**不新增形参**，`RV-P2-4`；`creds is None` 时不传）；`RestingRule` / `SizingFloorPolicy` 的构造期解析；孤儿告警路由 |
| `ai_auto/session.py` | `_status_payload`（`:3404`，同步）增 `settling` / `orphan` 两块；`_inject_explain_meta` 消费 `_pending_settlement_meta`（**在 `exitSource` 之后**，`RV-P0-6`）；开仓分支的孤儿硬拒；`contrarian_blocked` 五格计数；离场判定读 `_settling_blocks_close`；停手收尾的结算兜底 |
| `ai_auto/signals.py` | `ConsensusRule.gear_downgrade` 字段 + `resolve_consensus_gate`（`:407-412`）改调 `gear_resolution.downgrade_one_gear` + wire 三键 |
| `ai_auto/trend_verdict.py` | **`RV-P0-1` 新增**：`TrendVerdictRule.gear_downgrade` 字段 + `resolve_trend_gate`（`:630-635`）改调**同一个** `downgrade_one_gear` + `trendGate` wire 三键。**与 `signals.py` 共用同一把总闸** |
| `ai_auto/sizing.py` | `SizingFloorPolicy` + `SizingOutcome.floor_verdict` + 吸附之前的两条判据 |
| `ai_auto/sizing_runtime.py` | 两个新计数器 + 挂单三计数器的归集；**`floor_verdict != "none"` 时把决策改成 `hold`**（`RV-P1-4`：不这么做会撞进 `_do_open` 的 dust 分支并对用户说一个错误的原因） |
| `ai_auto/stats_ext.py` | `build_sizing_stats` 的五个加法键；`build_consensus_stats` 新函数（`contrarianBlocked`） |
| `ai_auto/reconcile.py` | `_status`（`:154-159`）的 `strict_final` 分支；`ReconcileParams` 两个新字段；`settlementCoverage`；**`tolUsd` 加法键**（F-4 的第 1 条要用它）；`drift_message` 三条成因 |
| `ai_auto/cost_runtime.py` | 把 `executor` 的结算覆盖计数与 `capital_usd` 喂进 `build_reconciliation` |
| `ai_auto/discipline_view.py` | `volSizing` 行的档位 note + `tone`；`consensus` 行的 `contrarianBlocked` note |
| `ai_auto/discipline_runtime.py` | `gate_counts` 增 `quantizeErasedRounds` / `scaledRounds` 两个透传键 |
| `ai_auto/open_funnel.py` | `LAYERS` / `LAYER_LABELS` / `_DEGRADE_PREFIXES` 三处同轮加 `orphanPosition` |
| `api/ai_auto.py` | `POST /ai-trade/{group_id}/member/{inst_id}/adopt-orphan`（`RV-P1-5`：与既有五个成员端点同构；不声明 `response_model`） |
| `config.py` | 15 项新设置（见 §八）+ 一条跨字段不变式（`resting_max_age_s < limit_order_ttl_s`，`RV-P1-2`） |
| `.env.example`（**仓库根**，不是 `services/quant/`） | 15 项双向登记 |

### 7.3　前端修改（9 个修改 + 3 个新增）

| 文件 | 意图 |
|---|---|
| `apps/web/lib/aiTradeGearResolution.ts`（新） | `describeGearResolution` 的 TS 孪生（判据与后端逐字同构，供启动弹窗在**点启动之前**实时提示）。**只镜像 `describe_gear_resolution`，不镜像 `downgrade_one_gear`**——后者是运行期判定，前端多一份就是多一个会漂移的真源 |
| `apps/web/lib/aiTradeGearResolution.test.ts`（新） | 四档真值表 + 与后端表格逐格一致的冻结断言 |
| `apps/web/lib/aiTrade.ts` | `AI_TRADE_POSITION_STEP_OPTIONS` 加 5% 一档；`DEGRADE_REASON_LABELS` 加 `orphan_position` |
| `apps/web/lib/aiTradeTypes.ts` | `settling` / `orphan` / 八组 `aiStats` 加法键的类型 |
| `apps/web/lib/aiTradeCost.ts` | `describeReconcile` 的 `ok` 分支措辞收敛为「…，在容差 {tol} 内」（**不是** v1 写的「恒显示差额」——差额今天就已经显示，`RV-P1-1`）+ 结算覆盖率一行 |
| `apps/web/lib/aiTradeFunnel.ts` | 漏斗层序镜像加 `orphanPosition` |
| `apps/web/lib/store.ts` | `settling` / `orphan` 两块进 WS 归并（**整体 spread，不逐键手抄**——逐键手抄会静默吞新键，是本仓库的成文教训） |
| `apps/web/components/Agent/AiTradeLaunchModal.tsx` | 档位选择器下方的 warn 提示（C-2 落点 ①） |
| `apps/web/components/Chart/AiTradeMemberRow.tsx` | `settling` / `orphan` 两枚状态芯片 |
| `apps/web/components/Agent/aiTradeStats/AiTradeStatsDetail.tsx` | 对账卡加结算覆盖率与差额；仓位卡加档位抹除行；动作分布加口径标签（`sampleScope` 的读点）；**成交表的平仓行加一枚「结算价来源」芯片**（`settlementSource` / `settlementPosId` / `settlementPriceGapBps` 的读点，`RV-P2-3` / `RV-P2-5`：这三个键否则在前端零读点，违反原则 6） |
| `apps/web/components/Agent/aiTradeStats/AiTradeStatsLive.tsx` | 动作分布标签加 `sampleScope`（G-1 的第二个读点） |
| `apps/web/components/Agent/aiTradeStats/DirectionBalanceCard.tsx` | `contrarianBlocked` 五格 |
| `apps/web/components/Agent/AiTradeOrphanBanner.tsx`（新） | 孤儿持仓横幅 + 三条补救按钮（镜像 `AiTradeLivenessBanner.tsx` 的既有形态） |

### 7.4　文档 / 登记

| 文件 | 意图 |
|---|---|
| `docs/plans/ai-trade-fill-truth-and-effective-controls/spec.md` | 本文件 |
| `docs/plans/ai-trade-fill-truth-and-effective-controls/evidence/audit.py` | §二 全部读数的可复算脚本（`--assert` 可进 CI） |
| `.claude-index/config.md` | 阈值偏离登记追加 **5** 行（见 §十二） |

---

## 八、配置项与总闸

全部以 `AI_TRADE_` 为前缀（**硬约束**：`desktop/src/lib/config.js` 的打包后端环境变量透传白名单是**按前缀**的，`AGENT_|LIVE_|AI_TRADE_|DISCOVERY_|OKX_|SECURITY_`；换个前缀等于这把开关在唯一的产品形态里按不下去——仓库事实 F-13）。

| # | 设置 | 类型 / 范围 | 默认 | 轴 | 关掉之后 | 默认值的理由 |
|---|---|---|---|---|---|---|
| 1 | `ai_trade_settlement_truth_enabled` | bool | **True** | A | `settling` 状态机整条不存在，逐字回退 `ca40b6b` | 它修的是一个**已经在报假数**的通路，且 fail-safe 方向就是今天的行为 |
| 2 | `ai_trade_settlement_deadline_s` | float, `(0, 600]` | **90.0** | A | — | 实测 `positions-history` 的 `uTime` 延迟是秒级；90s 给足余量，且远小于任何一次唤醒间隔 |
| 3 | `ai_trade_settlement_poll_interval_s` | float, `(0, 60]` | **8.0** | A | — | 90s 死线内约 11 次尝试；`_settling` 为空时一个请求都不发 |
| 4 | `ai_trade_settlement_match_tol_s` | int, `[0, 3600]` | **60** | A | — | 覆盖本地墙钟与交易所时钟的现实偏差 |
| 5 | `ai_trade_orphan_detect_enabled` | bool | **True** | B | 对称分支整条不存在 | 只写状态与事件、零交易所写请求（不变式 I-4），风险面为零 |
| 6 | `ai_trade_orphan_confirm_rounds` | int, `[1, 60]` | **3** | B | — | 与 `_fill_grace` 同量级（≈15s @ poll 5s） |
| 7 | `ai_trade_orphan_block_open` | bool | **True** | B | 只告警不封锁开仓 | 在一笔无人看管的持仓上再叠一笔自动仓位，是本轮能想到的最坏结果 |
| 8 | `ai_trade_sizing_zero_is_veto` | bool | **True** | C | `f_raw <= 0` 仍被抬到最小一格（逐字回退） | 本轮**唯一**默认开启的下单行为收紧；方向只可能是「少开一单」；生产已实测发生 |
| 9 | `ai_trade_sizing_floor_ratio` | float, `[0, 1)` | **0.0（关）** | C | 该判据逐字不存在 | 杀伤面大且缺生产依据，先靠 `belowFloorVetoes` 攒数据 |
| 10 | `ai_trade_contrarian_gear_downgrade` | bool | **True** | D | `signals` **与** `trend_verdict` 两处**同时**退回乘性降档（逐字回退） | 它把一条 2373 轮零放行的通路修活，且**只可能更宽、绝不放大仓位**。**一把闸管两处是刻意的**（`RV-P0-1`）：它们是同一个缺陷的两个副本，分开回退只会造出「一半修好一半没修」这种最难归因的状态 |
| 11 | `ai_trade_resting_keep_enabled` | bool | **False** | E | 每轮撤重挂（逐字回退） | 真实成交分布会变；且在 **E-0 前置调查**（`RV-P1-3`）给出结论之前，这条轴连「挂在哪个调用点上」都还不确定。观测半默认开 |
| 12 | `ai_trade_resting_min_reprice_bps` | float, `[0, 500]` | **8.0** | E | — | 实测重挂价差有 5 次是 0.0 bps、5 次在 1.9–9.8 bps；8 bps 恰好把「纯空转」与「真的改主意」分开。**注意**：0.0 bps 那 5 次已被既有的 `is_same_intent` 幂等挡住（`RV-P1-3`），故本阈值真正管的是 1.9–9.8 bps 那一档 |
| 13 | `ai_trade_resting_max_age_s` | int, `[60, 3600]` | **240** | E | — | **`RV-P1-2` 改正**：必须 **< `ai_trade_limit_order_ttl_s`（默认 300s）**，否则 `resting.is_expired` 先把单子撤掉、本判据永远轮不到（v1 写的 1800 是一条死配置）。跨字段不变式在 `config.py` 的 `model_validator` 里断言，与既有 `alert_pending_ttl_s ≥ 2 × live_auto_poll_interval_s` 同一范式 |
| 14 | `ai_trade_reconcile_final_verdict_strict` | bool | **True** | F | `final` 恒 `ok`（逐字回退） | 它修的是一句显示给用户的假话；伪阳性由既有的 `partial` 分支接住 |
| 15 | `ai_trade_reconcile_capital_tol_ratio` | float, `[0, 1]` | **0.0（关）** | F | 容差公式逐字等于今天 | 不与 F-1 同时收紧两个判据，否则效果无法归因 |

**闸与参数的区分（`RV-P1-10`，`G-X1` 的可执行定义靠它）**：15 项里只有 **1 / 5 / 7 / 8 / 9 / 10 / 11 / 14 / 15** 这 9 项是**闸**（有「回退档」这个概念）；**2 / 3 / 4 / 6 / 12 / 13** 这 6 项是**参数**，它们没有自己的回退档，回退由所属轴的总闸承担。`G-X1` 的「全部置回退档」= 把 9 把闸置回退档 + 6 个参数保持默认。

**登记门禁**：`.env.example` 在仓库**根目录**（`./.env.example`，71,553 字节）——不是 `services/quant/.env.example`（该文件不存在）。这是本仓库**第三次**在 spec 里写错这条路径（`AC-P1-1` 已经纠正过一次），故 `G-Env` 的断言是双向的：`config.py` 里每一个 `ai_trade_` 开头的新字段必须在 `.env.example` 里有一行，反之亦然；且**新增的 `.env.example` 不得出现在 `services/quant/` 下**。

---

## 九、测试与验收

### 9.0　A0：先把证据脚本写出来，再动一行代码

`evidence/audit.py` 必须在实现开始前跑通并复现 §二 的全部表格（`--assert` 退出 0）。理由：本轮有五条判据的依据是生产读数，任何一条读错，对应的轴就是在修一个不存在的问题。

### 9.1　门禁（`G-*`，全部为新增用例，判据写在名字里；v1 41 条 → v2 **54 条**）

#### 轴 A（17 条）

| ID | 判据 |
|---|---|
| `G-A1` | `match_settlement` 的七条判据各有一条**会红**的反向用例（尤其判据 3 的重复认领与判据 6 的 0 价） |
| `G-A2` | 命中 ⇒ 平仓腿的 `price` 是 `closeAvgPx`、`pnl` 是 `gross − fee`、`fee` 是 `往返总费 − 已记开仓腿费` |
| `G-A3` | 超时（`deadline` 到）⇒ 落账的 `price` / `pnl` / `fee` 与今天**逐位相同**（`_settling.mark_px` 恒等于侦测那一刻的 `mark_px`），`explain.meta` 只多 A-4 ⓑ 列的三键。**注意这与 `G-A8` / `G-A10` 不是同一件事**：那两条要求的是**零加法键**（A-4 ⓐ 列） |
| `G-A4` | `settling` 期间 `projected` / `realized_pnl_usd` 不变、`unrealized_usd` 冻结、**零 `trade` 事件、零落库** |
| `G-A5` | 幻影撤销：`settling` 中 `abs_pos` 再次 `> 0` ⇒ 清 `_settling`、不落任何腿、推一条 status |
| `G-A6` | `settlement_fn` 抛任意异常 ⇒ 立刻按 `local_mark` 落账（**不得把腿丢掉**） |
| `G-A7` | `stop()` / `pause()` 时 `_settling` 未决 ⇒ 收尾一定落账（带硬超时） |
| `G-A8` | 总闸 1 关 ⇒ `_on_reconcile_ok` 的调用序列与产物 sha256 等于 `ca40b6b` |
| `G-A9` | 回测 runner 的产物 sha256 零变化（轴 A 不进回测链路） |
| `G-A10` | `settlement_fn is None`（live_auto 全部路径）⇒ 一个请求都不发、行为逐字回退 |
| `G-A11` | `_settled_pos_ids` 满 256 后淘汰最旧 ⇒ 最坏退化为 `local_mark`，**绝不写错价** |
| `G-A12` | `fact.funding_usd` / `liq_usd` **不进** `pnl` / `fee`（否则 `reconcile` 的可比口径会自相矛盾） |
| `G-A13` | **`RV-P0-3`**：`settling` 期间 `_do_close` / `flatten` 恒短路、零交易所写请求；且构造「`_settle_benign_close` 抢先落账 → 再喂一条命中的结算行」⇒ `realized_pnl_usd` **只被加过一次**、`trades` 只多一条腿（**这条在合入前必须是红的**） |
| `G-A14` | **`RV-P0-2`**：`accounting=True & net_pnl=False` 时命中结算落的是**毛额** `pnl`；`local_net_view(net_mode=False)` 还原后与 `exchangeComparable` 之差 ≤ `tol`（**这条在合入前必须是红的**） |
| `G-A15` | **`RV-P0-4`**：`direction` 为 `""` / `"net"` / 缺字段时 `match_settlement` **照样命中**（判据 2 跳过）；`posId` 为空串时**恒不命中**（**两条都必须是反向门禁**） |
| `G-A16` | **`RV-P1-7`**：`fact.fee_usd < self.entry_fee_usd` ⇒ 整条降级为 A-4 ⓑ 列，`settlementRejectReason == "fee_residual_negative"`，**绝不出现 `fee == 0.0` 的静默夹取** |
| `G-A17` | **`RV-P1-12`（端到端，`AC-2` 的离线代理）**：喂一条真实形状的 `positions-history` 行 ⇒ 走完 `settling → 落账 → local_net_view → build_reconciliation` ⇒ `abs(diffUsd) <= tol` 且 `status == "ok"`。**今天这条链给出的是 `drift`** |

#### 轴 B（7 条）

| ID | 判据 |
|---|---|
| `G-B1` | `classify_orphan` 在 `settling=True` / `fill_grace=True` 两种断面上恒 `"none"`（这是**反向**门禁：轴 A 与轴 B 不得互相误报） |
| `G-B2` | 连续 `confirm_rounds - 1` 轮成立 ⇒ 仍是 `"pending"`，不推事件 |
| `G-B3` | 转 `confirmed` ⇒ 恰好推**一条** `AI_TRADE_ORPHAN_POSITION`；再持续 10 轮不再推第二条；`resume()` 后可再推 |
| `G-B4` | `orphan_block_open=True` 时开仓意愿被记进 `openFunnel.blocked["orphanPosition"]` 且 `sum(blocked) + submitted + sizingZeroed <= intents` 仍闭合 |
| `G-B5` | `adopt-orphan` 四条错误码各一条用例；`confirm` 缺失即 400 |
| `G-B6` | 后端与前端**双侧**断言：`orphan_message` 与横幅的 `textContent` 都不含字面 `**`（后端文案在 JSX 显星号是第 4 次复发） |
| `G-B7` | 总闸 5 关 ⇒ `_on_reconcile_ok` 的产物与 `ca40b6b` 逐字相同 |

#### 轴 C / D（13 条）

| ID | 判据 |
|---|---|
| `G-C1` | `f_raw == 0` 且闸 8 开 ⇒ `fraction == 0.0`、`floor_verdict == "solved_zero"`、`note_sizing` 记 `sizing_zeroed` |
| `G-C2` | 闸 8 关 ⇒ `f_raw == 0` 仍返回最小一格（**逐字回退**） |
| `G-C3` | `floor_ratio == 0` ⇒ 第二条判据在任何输入下都不触发 |
| `G-C4` | `describe_gear_resolution(0.5, …).erased` ⊇ `{brakeSlow, contrarian, corrScale}`；`(0.05, …).erased == ()`；`step<=0` ⇒ `()` |
| `G-C5` | 前后端两份 `describeGearResolution` 在四档上**逐格一致**（TS 用例内联后端表格） |
| `G-C6` | 纪律自检 `volSizing` 行在 `quantizeErasedRounds > 0` 时 `tone == "warn"` 且 note 含实际比例；`scaledRounds == 0` ⇒ 不加这句 |
| `G-C7` | **`RV-P1-4`**：`f_raw == 0` 的一轮里 `executor.dust_skips` **保持不变**，且 status 事件文案里**不出现「预算」二字**（今天这一轮会 `dust_skips += 1` 并说「预算 / 杠杆过小」——**这条在合入前必须是红的**） |
| `G-D1` | `step=0.5`、`proposed=0.5` ⇒ `lane == "contrarian"`、`size == 0.5`、`downgradeLane == "floor_is_min"`（**这条在合入前必须是红的**） |
| `G-D2` | `step=0.25`、`proposed=1.0` ⇒ `size == 0.75`（掉一格），不是 `0.5`（乘性） |
| `G-D3` | 闸 10 关 ⇒ `resolve_consensus_gate` **与** `resolve_trend_gate` 的产物都与 `ca40b6b` 逐字相同（含 `low_consensus_size` / `counter_trend_size` 两个触发断面） |
| `G-D4` | **§十三 Q2 的裁定冻结**：`step = 1.0`（每次全仓）时 `floor_is_min` **不放行**，退回今天的否决（最低格就是 100% ⇒「已降档」等于「不降档」） |
| `G-D5` | **`RV-P0-1`**：`step=0.5` / `proposed=0.5` / `alignment=counter` / `strength=strong` / `confidence=0.72` ⇒ **`resolve_trend_gate` 放行**（`lane == "counter"`、`size == 0.5`、`downgradeLane == "floor_is_min"`）。**这条在合入前必须是红的**——今天它报 `counter_trend_size` 否决，而它排在共识逆势通路**之前** |
| `G-D6` | `signals` 与 `trend_verdict` 两侧的降档结果对同一张真值表**逐格相等**（构造性证明两者共用 `downgrade_one_gear`，杜绝第三份镜像） |

#### 轴 E / F / G（12 条）

| ID | 判据 |
|---|---|
| `G-E1` | `plan_resting_action` 六条判据的完整真值表；闸关恒 `"replace"` |
| `G-E2` | 价差 `0.0 bps` ⇒ `"keep"` 且**零交易所请求**（用请求计数替身断言） |
| `G-E3` | `now - placed_ts >= max_age_s` ⇒ `"replace"`，即使价差为 0 |
| `G-E4` | **`RV-P1-2`**：`ai_trade_resting_max_age_s >= ai_trade_limit_order_ttl_s` 的配置在启动期即被 `Settings` 拒绝（跨字段不变式），**绝不允许一条永远不会被求值的配置存在** |
| `G-E5` | **`RV-P1-3`**：`plan_resting_action` 的 `"keep"` 与既有 `_prepare_resting_slot` 的同价幂等短路**不重复计数**——同一轮里 `restingKept` 与 `restingSkips` 至多有一个 +1 |
| `G-F1` | `final=True, mismatch=False, abs(diff) > tol` ⇒ `"drift"`（**合入前必须红**）；`mismatch=True` ⇒ 仍 `"partial"` |
| `G-F2` | 闸 14 关 ⇒ `_status` 逐字回退 |
| `G-F3` | `settlementCoverage` 在 `exchange+localMark == 0` 时**整键省略**（不写 `null`、不写 0） |
| `G-F4` | `drift_message` 三条成因都出现且不含字面 `**` |
| `G-G1` | `sampleRounds == len(decisions)` 且 `sampleScope` 里的数字取自环形容量常量（**不硬编码 50**） |
| `G-G2` | 前端动作分布卡的标签含「最近 N 轮」；`OpenFunnelCard` 的标签含「本会话全程累计」（两个分母各自带标签） |
| `G-G3` | 仓位卡在 `scaledRounds == 0` 时**不渲染**档位抹除那一行 |

#### 跨轴（5 条）

| ID | 判据 |
|---|---|
| `G-X1` | 15 项设置**全部置回退档**时，`status` / `events` / `metrics_json` 里**不出现任何一个本轮新键**（跨轴命题，上一轮 `LL-FINAL-3` 补过同形的一条） |
| `G-X2` | 源码扫描：`aiStats` 的每一个本轮新键在 `apps/web` 的**非测试**文件里至少有一个读点（正则须写 `\r?\n`——`core.autocrlf=true` 下写死 `\n` 会在干净检出上恒红） |
| `G-Env` | 15 项设置在仓库根 `.env.example` 双向登记；`services/quant/` 下不得出现新的 `.env.example` |
| `G-X3` | **仓库级文案门禁（`RV-P2-7`，第 4 次复发后升级）**：源码扫描 `services/quant/app/ai_auto/**.py` 与 `app/live_auto/**.py` 里所有会进入 `message` / `note` / `headline` 的中文字符串常量，断言**不含字面 `**`**。理由写在用例 docstring 里：前端的双侧断言看不见后端字面量，而 JSX 会把 `**粗体**` 原样显示成星号 |
| `G-Doc` | `.claude-index/config.md` 的阈值偏离表新增 5 行（§十二），行数为实测值而非估计值 |

### 9.2　集成用例（5 条，跨模块、用替身喂交易所回执）

1. **`IT-1` 止损成交的全链**：开仓 → 交易所侧 OCO 打掉 → `settling` → 结算命中 → 落账 → `trip_ledger.settle` 的 `rMultiple` 落在 `[-1.05, -0.95]`（今天这条链会给出 `-0.44`）。
2. **`IT-2` 结算永不到达**：`settlement_fn` 恒返回 `[]` ⇒ 90s 后按 `local_mark` 落账，且 `aiStats.reconcile.settlementCoverage.ratio == 0.0`。
3. **`IT-3` 撤单与成交竞态**：撤单回执成功但持仓出现 ⇒ 3 轮后判 `confirmed` ⇒ 开仓被拒 ⇒ `adopt-orphan` 后恢复正常开仓。
4. **`IT-4` 逆势通路端到端（`RV-P0-1` 加严）**：`step=0.5`、**趋势基调为 `strength="strong"` 且与动作方向相反（`alignment=counter`）**、共识板一边倒、模型自报 `confidence=0.72` + 62 字逆势证据 ⇒ **先过 `trendBias` 那一格**（`trendGate.downgradeLane == "floor_is_min"`）再过共识逆势通路 ⇒ 放行、`size == 0.5`、`contrarianPassed == 1`、会话配额 +1。**v1 的 `IT-4` 没有指定 verdict 断面，于是它在只修一半的实现上照样会绿**——这正是本条必须写死的原因。
5. **`IT-5` 全闸关的零回归**：15 项全置回退档，跑完整一轮决策 + 一次外部平仓 + 一次对账，产物 sha256 等于 `ca40b6b`。

### 9.3　验收（上线后按生产读数判，不看代码）

| 编号 | 判据 | 目标 |
|---|---|---|
| `AC-1` | `aiStats.reconcile.settlementCoverage.ratio` | ≥ 0.90（外部平仓里九成以上拿到了交易所结算价） |
| `AC-2` | `abs(reconcile.diffUsd)` 在 `settlementCoverage.ratio == 1.0` 的会话上 | ≤ `tol`（轴 A 有效性的唯一硬判据） |
| `AC-3` | `explain.meta.settlementPriceGapBps` 的绝对值中位数 | 落库**且在两个读点上可见**（纪律自检 `volSizing` 行 + 历史详情成交表的「结算价来源」芯片，见 §十三 Q6 裁定）。这是本轮要拿到的**新事实**：本地合成价到底差多少 bps |
| `AC-3b` | `explain.meta.settlementSource` 的取值分布 | `exchange` / `local_mark` 两值都出现过；且**没有任何一笔** `settlementRejectReason == "fee_residual_negative"` 长期占多数（若占多数，说明本地开仓腿记账系统性高于交易所，那是一条独立的缺陷，须单独立轮） |
| `AC-4` | `sizing.solvedZeroVetoes` | > 0（证明 C-1 的判据在生产上真的会命中；若长期为 0，说明 DOGE 那两轮是罕见断面，应在下一轮把闸 9 打开） |
| `AC-5` | `openFunnel.directionBalance.contrarianPassed` **与** `openFunnel.blocked["trendBias"]` **一起看**（`RV-P0-1` / `R-14`） | `contrarianPassed > 0`；且 `blocked["trendBias"]` 里的 `counter_trend_size` 占比显著下降。**只看前者会把「被上游那把闸拦死」误读成「逆势通路没机会」**——今天 `contrarianPassed` 是 0，而 `trendBias` 那一格在生产上还从没点亮过（`RV-E2`：运行中的包早于 `5337da1`） |
| `AC-6` | `openFunnel` 端到端 `intents → filled` 转化率 | 从 3.9% 提升（不设具体目标——本轮不承诺胜率，只承诺**每一层的拦截都是它真的想拦的那一次**） |
| `AC-7` | 纪律自检 `volSizing` 行在 `step=0.5` 的会话上 | `tone == "warn"` 且 note 里的比例等于 `quantizeErasedRounds/scaledRounds` |
| `AC-8` | 终态对账屏 | 不再出现 `mismatch=False` 且 `abs(diff) > tol` 的绿色「已对上」 |

### 9.4　真机人工验收（6 步，需要真实账户）

1. 用 `step=0.5` 启动，**在点启动之前**确认弹窗给出了三条失效机制的警示。
2. 挂一笔限价开仓，等它成交，手动在 OKX APP 上平掉 ⇒ 确认应用先显示「正在取回结算价」，随后落账价格**等于 APP 上的成交均价**（不是当时的标记价）。
3. 手动在 OKX APP 上开一笔该标的持仓 ⇒ 确认 3 轮内出现孤儿横幅、开仓被拒、`adopt` 按钮可用。
4. 把 `settlement_deadline_s` 临时调到 5s 并断网 ⇒ 确认 5s 后按 `local_mark` 落账、`settlementSource` 如实为 `local_mark`。
5. 老包 → 新包续跑：带着一笔真实持仓升级，确认 `resume` 不因新键报 409。
6. 9 把闸全置回退档、6 个参数保持默认，跑 30 分钟，确认界面与 `ca40b6b` 无任何可见差异。
7. **`RV-P0-3` 专项**：在 OKX APP 上手动平掉一笔持仓，**在应用显示「结算中」的那 90 秒里**点一次应用内的「手动平仓」⇒ 确认①应用**不发**任何平仓请求（看 `live_order_audit` 无新行）、②给出一句能看懂的解释、③结算落账后 `sessionPnlUsd` 与 OKX APP 上的已实现盈亏**只差手续费口径**（绝不出现约两倍的数）。
8. **`RV-P0-1` 专项**：在一段明确的单边行情里，用 `step=0.5` 让模型做一次逆势开仓（可临时把 `ai_trade_trend_counter_min_confidence` 调低以便复现）⇒ 确认它**不再**在 `openFunnel` 的 `trendBias` 那一格上以 `counter_trend_size` 结束。

### 9.5　回归基线

- 后端：`pytest` 当前基线 **5156 passed / 7 failed**，7 条既有红全部在未跟踪的在制文件 `tests/test_factor_library_expansion.py`（属 `factor-library-crypto-expansion`，本轮一行不碰）。本轮**不得新增任何一条红**。
- 前端：`vitest` **228 文件 / 2629 passed**。**必须在 `apps/web` 目录内跑**——多条 `*.source.test.ts` 用 `process.cwd()` 拼路径，用 `npx --prefix apps/web vitest run --root apps/web` 会让 7 条源码扫描门禁全部 `ENOENT` 假红（上一轮 `LL-FINAL-4` 的实测教训）。
- `tsc` / `next build` / `ruff check app tests --select F,E9` / 桌面 `node --test`（53 条）/ `from app.main import app` 全绿。

---

## 十、风险与缓解

| # | 风险 | 影响面 | 缓解 |
|---|---|---|---|
| R-1 | **轴 A 把一条已发生的平仓丢掉**（`settling` 永不结束） | 灾难级：往返在历史里消失、换手闸口径错、立场卡死 | 三重保险：①`deadline` 硬死线（默认 90s）②`stop()`/`pause()` 的收尾兜底（A-6）③任何异常立刻走 `local_mark`（原则 4）。`G-A6` / `G-A7` 各一条会红的反向用例 |
| R-2 | **结算行匹配错**（把上一笔往返的结算认成这一笔） | 严重：写进一个错价，而且是「看起来很真」的错价 | 七条判据里有三条是时间窗（`uTime >= detect_ts - tol` / `cTime <= entry_ts + tol`）+ 一条一次性认领（`claimed`）；命中多行取 `uTime` 最小；`settlementPriceGapBps` 落库使任何离谱的匹配在数据上立刻可见 |
| R-3 | **轴 A 延后落账让下游闸看到一个更旧的世界**（换手闸、持仓周期闸在 `settling` 期间读不到这笔平仓） | 中：最多晚 90s | `settling` 期间 `projected` 不变 ⇒ 这些闸看到的是「仍有持仓」，与今天「已平仓」相比**更保守**（不会多开一单）。`_external_close_suppressed` 的既有注释立的正是同一条规矩（「两条支路都比就地猜一个来源诚实，代价只是延后一轮」） |
| R-4 | **孤儿误报**把正常开仓路径钉死 | 严重：一次误报等于该标的停止交易 | fail-closed 到 `"none"`（判据 1/2/3 任一不确定即不判）+ `confirm_rounds=3` 的连续确认 + 与轴 A 的 `settling` 互斥（`G-B1` 是反向门禁）。且 `orphan_block_open` 可单独关掉，只留告警 |
| R-5 | **轴 C-1 把本来会开的单变成不开** | 中：真实下单行为变更 | 只在 `f_raw` 精确为 0 时触发（闸 8），生产实测 2373 轮里只有 2 轮；`floor_ratio` 那条杀伤更大的默认关。`sizing_zeroed` 是既有漏斗格，用户看得见 |
| R-6 | **轴 D 让逆势通路从「零放行」跳到「频繁放行」** | 中：开仓次数上升，而逆势开仓的历史成绩未知 | 逆势通路的其余四道判据（反向票上限 / 会话配额 3 / 置信度 0.65 / 逐字证据 60 字）**一条不减**；会话配额本身就是硬上限（每会话至多 3 次）。`aiStats.consensus.contrarianBlocked` 与既有的顺势/逆势成绩卡使效果可归因 |
| R-7 | **轴 E 让挂单活得更久 ⇒ 在错误的价位成交** | 中 | 默认关；`max_age_s` 是硬上限；方向变化与不再想开仓两条判据排在最前 |
| R-8 | **F-1 让终态屏突然多出一片红** | 中：观感 | 必须与轴 A 同批上线（§4.F-1 已写死这条依赖）；`drift_message` 的第一条成因就是「外部平仓按本地标记价入账」，并附实际笔数，用户看到的是解释而不是一个孤立的红 |
| R-9 | **新增 15 项设置里有一项没进 `.env.example`** ⇒ 打包后端里按不下去 | 中：回退档失效 | `G-Env` 双向断言 + 前缀白名单硬约束（§八）；`.env.example` 的路径已在 §八 显式钉死（第三次纠正同一条） |
| R-10 | **`executor.py` 已 1142 行、`session.py` 已 3791 行**，本轮继续加 | 低但必须登记 | 判定全部下沉到四个新纯核心；两个文件里只留取数与装配。`.claude-index/config.md` 逐条登记实测行数（§十二），并把「下一次触碰先把 resting 路径外移到 `live_auto/resting_flow.py`」这条既有指示**再重复一次** |
| R-11 | **本轮与兄弟 spec `ai-trade-edge-closed-loop-governor` 撞车** | 中 | 两份 spec 的交集只有 `sizing.py` 与 `signals.py`，且那份 spec 未提交未实现。本轮在 §三 非目标 2 明确声明「本轮是它的前置条件」，并建议它在本轮合入后**重新采一次证据**——它的 EC-1（`edgeErrorMedianBps`）与 EC-4（已实现 edge 回灌）都依赖被本轮修正的价格 |
| R-13 | **`settling` 与出站平仓双记已实现盈亏**（`RV-P0-3`） | 灾难级：真实资金的盈亏显示与换手闸口径同时错，且错在「多算了一笔」这个最难被察觉的方向 | I-2b 的两条（出站互锁 + `_finish_settling` 幂等自检）+ `G-A13` 一条**合入前必须红**的反向用例。**这是本轮唯一一条能同时污染真实资金账本与下游全部学习样本的风险，实施顺序里轴 A 必须独立成一个提交** |
| R-14 | **轴 D 的上游那把闸在生产上零证据**（`RV-E2`：147 条 run 里 `trendVerdict` 键出现 0 次） | 中：无法用历史数据验证 D-0 的效果，只能靠 `G-D5` 与 `AC-5` | 如实登记：D-0 是一条**前瞻性**修复（它修的是「下一个包一上线就会发生」的事）。`AC-5` 的判据因此必须同时看 `openFunnel.blocked["trendBias"]` **与** `contrarianPassed`——只看后者会把「被上游拦死」误读成「逆势通路没机会」 |
| R-15 | **轴 E 的 E-0 前置调查可能得出「本轴无事可做」的结论**（`RV-P1-3`） | 低 | 这是**可接受的**结果：轴 E 是本轮唯一一条 P1 且默认关的轴。若 E-0 判定那 5 次全部来自 TTL 到期重挂，则本轮只保留观测半（三个计数器 + 漏斗 `expired` 格），`plan_resting_action` 整条推迟到下一轮——**绝不为了让一条轴看起来做完了而把守卫挂在错的位置上** |
| R-12 | **上一轮遗留的 `SD-DEBT-204` 仍未提交**（两个 `status_code=204` 端点标注 `-> None`，打包后端 import 期 `AssertionError`） | 高（打包后端起不来），但**与本特性无关** | 本轮在 §五 显式点名，并要求实现节点**单独出一个 `fix:` 提交**，绝不混进本特性的暂存区 |

---

## 十一、回退矩阵

| 闸 | 置回退档之后，逐字节回到 `ca40b6b` 的判据 |
|---|---|
| 1 `settlement_truth_enabled=False` | `_on_reconcile_ok` 立即合成（`G-A8` sha256） |
| 5 `orphan_detect_enabled=False` | 对称分支不存在（`G-B7` sha256） |
| 7 `orphan_block_open=False` | 只告警，`openFunnel` 不出现 `orphanPosition` 格 |
| 8 `sizing_zero_is_veto=False` | `resolve_size_fraction` 逐字回退（`G-C2`） |
| 9 `sizing_floor_ratio=0.0` | 第二条判据不存在（`G-C3`）——**这是默认值** |
| 10 `contrarian_gear_downgrade=False` | `resolve_consensus_gate` **与** `resolve_trend_gate` **两处同时**逐字回退（`G-D3`） |
| 11 `resting_keep_enabled=False` | 每轮撤重挂——**这是默认值**（`G-E1`） |
| 14 `reconcile_final_verdict_strict=False` | `_status` 逐字回退（`G-F2`） |
| 15 `reconcile_capital_tol_ratio=0.0` | 容差公式逐字回退——**这是默认值** |
| **参数项 2 / 3 / 4 / 6 / 12 / 13** | **它们没有自己的回退档**（`RV-P1-10`）：2/3/4 由闸 1 承担、6 由闸 5 承担、12/13 由闸 11 承担。在矩阵里显式登记是为了让 `G-X1` 有一个可执行的定义——「全部置回退档」= **9 把闸置回退档 + 6 个参数保持默认** |
| **全部 9 把闸** | `status` / `events` / `metrics_json` 零新键（`G-X1`）；集成 `IT-5` 跑完整一轮的 sha256 |

**刻意的例外（无总闸的加法，逐条列出）**：轴 C-2 的启动期披露（三个落点）、轴 D-2 的五格计数、轴 F-3 的 `settlementCoverage`、轴 F-4 的两项措辞与读数、轴 G 的两项、以及 A-4 ⓑ 列的三个 `local_mark` 留痕键（**`RV-P0-5`：它们只在状态机真的跑过时才出现，故闸 1 关时它们构造性地不存在，不属于例外**）。理由：它们**只增加显示，不改变任何判定与任何下单行为**，给它们配总闸等于给「说真话」配一个开关。`G-X1` 的断言**刻意不覆盖这六处**，且必须在用例里逐条注明为什么。

---

## 十二、阈值偏离登记（`.claude-index/config.md` 追加行）

实现节点**必须**在 `.claude-index/config.md` 的「显式记录的阈值偏离」表里追加以下 **5** 行，行数取**实测值**（`wc -l`）而不是估计值。当前基线：

| 位置 | 阈值 | 当前实测 | 本轮预期增量与理由 |
|---|---|---|---|
| `services/quant/app/live_auto/executor.py` | `MAX_FILE_LINES=800` | **1142** | `settling` 状态机 + 孤儿分支 + 挂单去留接线，判定全在三个新纯核心（`settlement.py` / `resting_policy.py` / `orphan.py`），本文件只留取数与装配。**既有指示「先把 resting-order 路径整体外移到 `live_auto/resting_flow.py`」本轮第二次未兑现**，理由是那次外移会把轴 E 的零回归判据搅浑；**升级为硬要求：下一个触碰本文件的特性必须先还这笔债** |
| `services/quant/app/ai_auto/session.py` | `MAX_FILE_LINES=800` | **3791** | 只增四处薄接线（`_status_payload` 两块、开仓分支一处硬拒、五格计数、停手收尾一步），零纯核心。既有拆分指示不变 |
| `services/quant/app/config.py` | `MAX_FILE_LINES=800` | **1555** | 本轮 +15 项 `ai_trade_*`。前缀统一为 `AI_TRADE_` 仍是硬约束。修法与既有登记一致（按家族拆 `config/` 包并保持单一 `Settings` 聚合） |
| `apps/web/lib/aiTradeTypes.ts` / `apps/web/lib/aiTrade.ts` | `MAX_FILE_LINES=800` | **2378 / 1613** | 本轮只增类型与两处常量表；档位分辨率的判定**刻意新建** `aiTradeGearResolution.ts` 而不是塞进 `aiTrade.ts` |
| `services/quant/app/ai_auto/trend_verdict.py` | `MAX_FILE_LINES=800` | **实测值以 `wc -l` 为准** | **`RV-P0-1` 新增**：本轮只增 `TrendVerdictRule.gear_downgrade` 一个字段 + 把 `resolve_trend_gate` 的三行降档改成一次 `downgrade_one_gear` 调用（**净行数减少**）+ wire 三键。判定下沉到 `gear_resolution.py`，本文件不承接任何新逻辑 |

另：`match_settlement` / `plan_resting_action` / `describe_gear_resolution` / `downgrade_one_gear` 四个新函数的形参数量必须 ≤5（位置参数 ≤2，其余 keyword-only）——本轮**不申请** `MAX_FUNCTION_PARAMS` 偏离。

**`RV-P2-4`**：`manager._build_executor` 今天已是 **6 个形参**（既有偏离）。轴 A 的 `settlement_fn` **在函数体内用已在作用域里的 `creds` / `inst_id` 闭包构造**，**不新增形参**——不把一条既有偏离推得更远，是本轮对这条阈值的全部承诺。

---

## 十三、待决问题（**评审已逐条裁定，下表第四列即结论**）

| # | 问题 | 本文的选择 | 反方 | **评审裁定** |
|---|---|---|---|---|
| Q1 | `ai_trade_sizing_zero_is_veto` 该不该默认 True？ | **True**。它把「风控解出零」从「开半仓」改回「不开」，方向只可能是少开一单，且生产已实测发生 | 它是本轮唯一默认开启的下单行为收紧；保守派会要求先观测一轮。若评审选 False，则必须同时把 `solvedZeroVetoes` 的告警阈值定下来，否则这条永远不会被打开 | **维持 True。** 判据是「`corr_scale` / `brake_mult` 解出**精确的 0**」——这不是一个阈值，是一个**恒等式**：风险预算为零。把它翻译成「开最小一格」不是保守，是把否决改成开仓。方向单调（只可能少开一单）、有既有漏斗格承载、生产已实测发生。**但必须同时落 `RV-P1-4`**（改成 `hold`、不撞 dust 分支），否则这条闸会让产品说一句假话。 |
| Q2 | 轴 D 的「已在最低格即视为已降档并放行」是不是把安全阀开得太大？ | **不是**。逆势通路的其余四道判据一条不减，会话配额 3 次是硬上限，且放行的仓位就是最小格 | 反方：在 `step=1.0`（每次全仓）的档位上，最低格就是 100%——此时「已降档」等于「不降档」。**本文接受这条反对，并提出补丁**：`step >= 1.0` 时 `floor_is_min` 不放行（退回今天的否决），门禁 `G-D4` 冻结。请评审确认这个补丁是否足够 | **补丁足够，但问题问小了。** `step >= 1.0` 的补丁接受并冻结（`G-D4`）。真正的问题是 v1 只看见了两把同形闸里的一把——见 `RV-P0-1`：`trend_verdict.py:633` 与 `signals.py:411` 是同一行字节，且**它先咬**。轴 D 已扩为 D-0（共用谓词）+ D-1（两个接线点、一把总闸）。 |
| Q3 | `settlement_deadline_s=90` 期间，界面上那笔仓位算「持有中」还是「结算中」？ | **单列「结算中」**（新芯片），未实现盈亏冻结 | 反方：多一个状态就多一处要解释的东西。但把它并进「持有中」会让用户以为还能手动平仓（实际上仓位已经不在了） | **单列「结算中」，并且这枚芯片是必需的、不是可选的。** 理由比 v1 给的更硬：`RV-P0-3` 要求 `settling` 期间**短路一切出站平仓**，那么用户点「手动平仓」时会得到一句「本轮不发平仓单」——**如果界面还写着「持有中」，这句话就变成了一个无法解释的拒绝**。芯片必须同时出现在 `AiTradeMemberRow`（实时）与历史详情的成交表（事后，`RV-P2-3`）。 |
| Q4 | 轴 E 默认关，那这一轮到底改善了什么？ | 观测半默认开（`restingKept/Repriced/Cancelled` + 漏斗 `expired` 格），下一轮据实测定阈值 | 反方：E-4 的证据（5 次逐字同价重挂）已经足够强，`min_reprice_bps=8` 完全可以默认开。请评审裁定 | **维持默认关，而且理由和 v1 给的不一样。** 反方的证据基础被 `RV-P1-3` 推翻了：`is_same_intent` 今天就已经对「同向同价」不撤不挂，且是**逐字相等**（比 8 bps 更严）。所以那 5 次不是从这条路上来的，在 **E-0 前置调查**给出路径之前，把这条判据默认开等于把守卫钉在一面没有门的墙上。 |
| Q5 | 孤儿持仓的 `adopt` 该不该在**暂停态**也可用？ | **可用**（暂停恰恰是处理它的好时机） | 反方：暂停态的会话没有盯市循环，adopt 进来的仓位无人看管，比留在孤儿态更危险。若评审选「不可用」，端点须返回 409 并说明 | **可用，但必须带一句用户能看懂的后果说明。** 反方说的「无人看管」是真的，但它把两件事比错了：留在孤儿态**同样**无人看管，而且还多一层「系统不知道它存在」。adopt 至少把它纳入了账本与历史。端点在暂停态照常 200，回执与横幅必须写明「已接管；该持仓在会话继续之前不受任何自动风控管辖，止盈止损仍由交易所侧 OCO 承担」。 |
| Q6 | `settlementPriceGapBps` 要不要做成告警（超过 N bps 推 error）？ | **本轮只落库、不告警**。阈值需要先有分布 | 反方：这正是本轮最重要的观测量，不告警等于又造一个「有写点没读点」。折中：进纪律自检的一行读数（中位数 + 样本数），不推事件 | **采纳折中方案。** 本轮只落库不推事件（阈值确实需要先有分布），但**必须有读点**——否则它就是原则 6 明令禁止的「有写点没读点」。落点定为两处：①纪律自检 `volSizing` 行的一行读数（中位数 + 样本数）；②历史详情成交表平仓行的「结算价来源」芯片（`RV-P2-5`）。 |

---

## 十四、登记但本轮不做（防止下游重复投入）

| 编号 | 事项 | 为什么不做 |
|---|---|---|
| `N-1` | 开仓腿的 maker/taker 分类改用 `fills.execType` | 它会让**开仓腿的即时记账**依赖网络，违反 `fee_ledger.py` 的成文纪律。轴 A-4 的往返残差补正已经把**往返口径**修准了，腿间分摊的近似不影响任何一个消费者 |
| `N-2` | `exit_priority` 的证据准入是字符串长度检查（黑板 `EC-7`） | 与轴 D 落在同一条共识/离场链上，两次收紧叠在一起无法归因。上一轮已登记为「只记录不修」 |
| `N-3` | 桌面端日志里没有任何一行交易生命周期记录（09-01 全天非 httpx 行只有 ~300 行 WS 重连告警） | `ca40b6b` 把日志体积压下去了，但没有把交易审计补上去。这是一条独立的、值得单独成轮的「可运维性」特性 |
| `N-4` | `holdSeconds` 取自 bar 时间戳而非墙钟（run 1627 的 `holdSeconds=3600` 恰等于两根 barTs 之差） | 双时钟问题已有独立登记（`aragon-live-db-location-and-ai-trade-ts-dual-clock`），且本轮不碰 `trip_ledger` |
| `N-5` | `SD-DEBT-204`（两个 204 端点的 `-> None`） | 与本特性无关，必须**单独出一个 `fix:` 提交**（§十 R-12） |
| `N-6` | 兄弟 spec `ai-trade-edge-closed-loop-governor` 的五条轴 | §三 非目标 2；本轮是它的前置条件 |

---

## 十五、实施顺序（强建议，按依赖排）

1. **A0** 先跑通 `evidence/audit.py --assert`（不改一行源码）。
2. **轴 A**（`settlement.py` → `fee_ledger.py` 三处 override → `executor.py` 状态机**含 I-2b 互锁与幂等自检** → `manager.py` 装配 → `session.py` 的 `_inject_explain_meta` 消费轮级槽 + 收尾）。轴 A 独立成一个提交——**它是唯一触碰真实资金账本的轴**。
   **先写 `G-A13` / `G-A14` / `G-A15` / `G-A16` 四条会红的用例，再写实现**（它们四条正是 `RV-P0-2` / `RV-P0-3` / `RV-P0-4` / `RV-P1-7` 的判据）。
3. **轴 F**（必须紧跟轴 A，否则终态屏会突然变红且没有解释）。
4. **轴 C**（`sizing.py` → `sizing_runtime.py`**含 `floor_verdict → hold`** → `gear_resolution.py` → 三个落点）。
5. **轴 D**（`gear_resolution.downgrade_one_gear` → **`trend_verdict.py` 与 `signals.py` 两个接线点必须在同一个提交里** → 五格计数 → 两个读点）。**`G-D5` 先红再绿**——它是本轴唯一能证明「上游那把闸也修了」的判据。
6. **轴 B**（`orphan.py` → `executor.py` → `open_funnel.py` 三处 → 端点 → 横幅）。
7. **轴 E**：先做 **E-0 前置调查**（只读 `live_order_audit`，不写产品代码）。结论若指向 TTL 到期重挂 ⇒ 本轮**只做观测半**，`plan_resting_action` 推迟；结论若指向 cancel-replace ⇒ 按 §4.E 实现（`resting_policy.py` → `executor.py`；默认关）。
8. **轴 G** + `.claude-index/config.md` 登记 + `.env.example` 双向登记。
9. 全量回归（后端 → 前端在 `apps/web` 内 → tsc → next build → ruff → 桌面 node --test → `from app.main import app`）。

---

---

## 评审结论

### **有条件通过（Approved with conditions）**

本轮 spec 的**证据部分是本仓库到目前为止质量最高的一份**：`evidence/audit.py --assert` 在真实生产库上实跑退出 0、18 项冻结读数逐格一致，E-1 的根因验算（run 1627 两笔按各自 `stop_loss_price` 重算毛额 −4.618 vs 交易所 −4.560，误差 1.3%）是一条几乎无法反驳的推理链。**七条轴指向的七个问题全部为真**，评审没有推翻任何一条问题陈述。

问题**全部出在修法上**，而且六条 P0 里有五条是同一个形状——**spec 自己在 §4.0 原则 2「判定与 IO 分离」里写下的那条纪律，在具体条款里没有被贯彻**：判据落在了一个不携带该信息的位置上（`RV-P0-2` 取错了开关、`RV-P0-4` 读了一个可能是空串的字段、`RV-P0-6` 选了一个共用模块），或者只覆盖了同一个缺陷的一半（`RV-P0-1` 漏掉上游那把逐字同构的闸、`RV-P0-3` 漏掉与既有平仓通路的互锁）。这不是设计者不够仔细，这是**「同形缺陷会成对出现」这条本仓库反复付过代价的规律**又一次生效——`RV-P1-6` 那条「调用点数漏了 4 个」是它最直白的证据。

P0 六条、P1 十二条**已在正文逐条修毕**，文档已定稿为 **v2**。下列九条是**放行条件**，实现节点必须逐条兑现；任一条未兑现，本轮不得进入终审。

| 编号 | 放行条件 | 验收方式 |
|---|---|---|
| `C-1` | **轴 D 的两个接线点必须在同一个提交里**，且共用 `gear_resolution.downgrade_one_gear` 这一个谓词。`signals.py` 与 `trend_verdict.py` 里**不得各写一份降档逻辑** | `G-D5`（合入前必须红）+ `G-D6`（两侧逐格相等） |
| `C-2` | **轴 A 的 `pnl` 判据必须是 `settings.ai_trade_net_pnl_enabled`**，不是「`fee_ledger` 已装配」 | `G-A14`（合入前必须红） |
| `C-3` | **`settling` 期间必须短路一切出站平仓，且 `_finish_settling` 必须幂等自检**（不变式 I-2b） | `G-A13`（合入前必须红）+ 真机验收第 7 步 |
| `C-4` | **`match_settlement` 的 `direction` 判据必须 fail-open、`posId` 空串必须淘汰** | `G-A15`（两条反向门禁） |
| `C-5` | **A-4 的 ⓐ 列必须是零加法键**：闸 1 关或 `settlement_fn is None` 时，`trade` 与 `explain` 的形状与 `ca40b6b` **逐字节相同** | `G-A8` / `G-A10` sha256 + 既有的非 AI 执行器键集冻结用例 |
| `C-6` | **八个 `settlement*` 键的装配点是 `session._inject_explain_meta`**；`live_auto/explain.py` 一个字节不改 | 源码 diff 直接看；`G-A8` 兜底 |
| `C-7` | **轴 C 的零仓位否决必须在 `_execute_decision` 之前把决策改成 `hold`**，`dust_skips` 与那句「预算 / 杠杆过小」一个字节不动 | `G-C7`（合入前必须红） |
| `C-8` | **轴 E 必须先出 E-0 前置调查结论再写产品代码**。若结论是「TTL 到期重挂」，本轮只做观测半，`plan_resting_action` 整条推迟——**这是允许的结果，不是失败** | E-0 结论写进 spec；`G-E4` 守跨字段不变式 |
| `C-9` | **`AC-5` 必须同时看 `contrarianPassed` 与 `blocked["trendBias"]`**；`SD-DEBT-204` 单独出 `fix:` 提交，绝不混进本特性暂存区 | 上线后按生产读数判；提交时逐路径 `git add`，**禁止 `git add -A`** |

### 评审可直接复跑的七条命令

```bash
# 1) 证据基线（应输出「全部一致。」并退出 0）
python docs/plans/ai-trade-fill-truth-and-effective-controls/evidence/audit.py --assert

# 2) RV-P0-1：两把闸是同一行字节
grep -n "shrink_requested and size >= proposed" services/quant/app/ai_auto/signals.py services/quant/app/ai_auto/trend_verdict.py

# 3) RV-P0-1：上游那把闸先咬
grep -n "_apply_trend_bias_gate(decision, degrade)\|_apply_consensus_gate(decision, consensus, degrade)" services/quant/app/ai_auto/session.py

# 4) RV-P1-6：quantize_size_fraction 的 6 个调用点
grep -rn "quantize_size_fraction" services/quant/app/ --include=*.py

# 5) RV-P0-2：两把开关是独立的
grep -n "ai_trade_fee_accounting_enabled\|ai_trade_net_pnl_enabled" services/quant/app/config.py

# 6) RV-P0-4：direction 归一为可能为空的字符串；时间已是秒
sed -n '778,800p' services/quant/app/okx/trading.py

# 7) RV-P1-3 / RV-P1-2：同价幂等守卫已存在；TTL 默认 300s
grep -n "is_same_intent" services/quant/app/live_auto/executor.py services/quant/app/live_auto/resting.py
grep -n "ai_trade_limit_order_ttl_s" services/quant/app/config.py
```

### 一处收回的反对意见

评审在核验中一度认为 `match_settlement` 的判据 4/5 存在**毫秒 / 秒混用**（OKX 原始回执是毫秒字符串）。核实 `_normalize_position_history`（`trading.py:796-797`）后确认它已经 `// 1000` 归一为秒，**判据 4/5 是对的，这条反对意见收回**，并已写进「确认为不是缺陷」清单第 1 条，防止下游又去改一遍。

### 对下一轮的一句话

本轮之后，「向外界要真话」（`ca40b6b`）与「把要回来的话写进账本」（本轮轴 A）就都通了。但 `RV-E2` 揭示了一件更值得下一轮认真对待的事：**用户真机上跑的那个包，落后主干至少两个特性**（`5337da1` 与 `ca40b6b` 都不在里面）。这意味着过去两轮的全部修复——包括本轮所依赖的费率真值化——**在用户那里一行都还没生效**。「怎么让修好的东西真的到达用户」本身就是一条值得成轮的特性。

---

*本文件由设计节点产出，未修改任何源码，未 `git commit`。*


---

## 实施过程发现的方案缺陷

> 实施节点产出（本轮实现期）。每一条都遵循「**不静默偏离**」：先在这里登记事实与判断，再按修正后的做法实现。
> 编号 `I-*` 与既有各轮 spec 的同名小节延续同一套体例。

### I-1（**新登记的真缺陷，spec 未涵盖**）　挂单 TTL 与判定它的时钟不是同一个 —— 轴 E 的 E-0 前置调查结论

**C-8 要求的 E-0 前置调查已完成**，结论推翻了 spec §4.E 对轴 E 的整个设计前提。

**调查方法**：`live_order_audit` 上把最新那一组的 46 行按 `cl_ord_id` 配对（accepted → canceled），
逐条算出挂单存活时长、撤单与「下一次同价下单」的间隔，再用 `%APPDATA%/aragon-desktop/logs/backend.log`
的 httpx 行逐秒复核。

**那 5 次「逐字同价重挂」分属两条路径，没有一条是 cancel-replace**：

| 路径 | 笔数 | 证据 |
|---|---|---|
| ①′ **双时钟导致的早产 TTL 过期** | 2（ETH `aragon1a05c6a129f00f` 存活 **2s**、SOL `aragon1a05debe734019` 存活 **4s**） | 日志逐行：`06:00:53.805 POST /trade/order` → `06:00:54.207 / 06:00:54.928 GET /trade/order?clOrdId=…` → `06:00:55.284 POST /trade/cancel-order` |
| ②′ **Agent 主动 `cancel_order` 后在更晚的一轮以同价重挂** | 3（ETH `…010` 存活 221s、`…01b` 226s、`…01c` 205s） | 撤单 → 下一次下单的间隔是一个完整唤醒周期（194s / 162s / 639s），故下一轮跑到时 `self.resting` 已是 `None` |

**路径 ①′ 的根因（本轮实现的那条修复）**：`_enter_resting` 用**决策轮的 `ts`** 盖 `created_ts`
（`executor.py` 的 `now_ts=ts`），而那个 `ts` 是一个**双时钟**——确认根唤醒里它是
`int(row["ts"])`，即 **K 线开盘时间戳**；定时 / 情报告警 / 信号翻转三种唤醒里它又是
`int(time.time())` 墙钟（三处 `_run_decision_round(..., ref_px, int(now), wake)`）。而
`is_expired` 恒与盯市循环的墙钟比（`_monitor_tick` 的 `now_ts = int(time.time())`）。

> 行号刻意不写：本轮自己就改动了 `session.py`，钉死的行号在合入那一刻就是错的。四处锚点分别用
> `grep -n 'ts = int(row\["ts"\])'` 与 `grep -n 'ref_px, int(now), wake))'` 可一次找齐。

15m 根 + `ai_trade_limit_order_ttl_s = 300` ⇒ 确认轮里 `expire_ts = 开盘 + 300`，而那一轮发生在
**开盘 + 900**（确认的是上一根）⇒ **挂单出生即过期**，下一个盯市 tick（2~5s）立刻撤掉。生产实测
`10:00:53` 下单（+53s 进入 10:00 这根，确认的是 09:45 那根）⇒ `expire = 09:50` ⇒ 2 秒后被撤。

连带后果三条，每一条都独立成立：

1. `resting.resting_payload` 喂给 LLM 的 `expiresInS` 恒为 `0`——**prompt 里就在告诉模型这单要死了**；
2. `_enter_resting` 推的状态文案写着「300s 未成交则自动撤销」，而它 2 秒后就撤了——**产品在说假话**；
3. 挂单从来没有机会成交。生产实测组内 `submitted → filled` 只有 26 → 9（34.6%），ETH 单标的
   **13 → 2（15.4%）**——而 E-4 原本把这归因于「挂单被下一次决策轮例行撤掉」。

**处置（对 spec 的偏离，及理由）**：

- **`plan_resting_action` / `resting_policy.py` 整条推迟**，`ai_trade_resting_keep_enabled` /
  `_min_reprice_bps` / `_max_age_s` **三项设置一并不声明**。理由：`plan_resting_action` 的落点是
  `_prepare_resting_slot`，而它只在 `self.resting is not None` 时才被走到——上述 5 次里
  `self.resting` **全部已经是 `None`**，故那条守卫对实测证据是**可证明的 no-op**（R-15 / C-8 明确
  允许这个结果：「绝不为了让一条轴看起来做完了而把守卫挂在错的位置上」）。三项设置若照样声明，
  就是 `RV-P1-2` 亲手钉死过的那种「永远不会被求值的配置」。
- **改为修路径 ①′**：新增 `ai_trade_resting_ttl_wall_clock`（默认 **True**），TTL 起点改用**与判定
  它的那个时钟同源**的墙钟。默认 True 的理由：当前行为不是一种设计上的保守，而是一次时钟错配
  ——挂单最多只会活到用户配置的那个 TTL，一秒不多；且它同时修掉上面第 2 条那句假话。置 False
  逐字回退双时钟行为，用例 `test_resting_ttl_wall_clock_gate_off_restores_bar_ts_stamp` 冻结。
- **观测半按 spec 交付但接在真的会触发的位置上**：`restingKept` 接既有 `is_same_intent` 幂等短路
  （今天这条路径**一次都没有被计数过**）、`restingRepriced` 接 cancel-replace、`restingCancelled`
  接其余撤单原因，另加 `restingExpired` 单列 TTL 到期笔数——它正是这条修复的效果读数。

**故本轮设置总数为 13 而非 15**（九把闸不变，参数从 6 项减为 4 项）；`G-X1` 的可执行定义随之为
「9 把闸置回退档 + 4 个参数保持默认」。

> ⚠️ 双时钟本身是一笔**更大的、本轮未清的欠账**：`ts` 这一个形参在四种唤醒下承载两种时钟，
> 本轮只修了它流向 `expire_ts` 的那一条支路。记忆库已有独立登记
> （`aragon-live-db-location-and-ai-trade-ts-dual-clock`，「写侧未做」）。

### I-2　`_on_reconcile_ok` 不能变成 `async` —— 否则打断 6 处既有单测的调用姿态

spec §4.A A-2 的时序把「轮询结算」画在 `_on_reconcile_ok` 内部。照写会让它必须变 `async`，而
仓库里有 **6 处**单测**同步**直接调用它（`test_ai_trade_fee_ledger.py:140`、
`test_ai_trade_net_pnl.py:87/97/98`、`test_ai_trade_silent_degradation_io.py:373/379/392`）。

**修法**：未决态的分派**上移到 `reconcile()`**——异步取数留在 `reconcile`，同步纯判定留在
`_on_reconcile_ok`。这不只是为了不动用例，它恰好就是 §4.0 原则 2「判定与 IO 分离」，并使
**不变式 I-2 变成构造性结论而不是一句断言**：重写 `unrealized_usd` 的那两行根本不在未决态的
执行路径上。

### I-3（**spec 的判据在数学上恒为真**）　`describe_gear_resolution` 无法用「最小一格」区分任何两个档位

spec §4.C C-2 写的判据是：

```python
erased = quantize_size_fraction(min_gear * m, step) >= min_gear - _EPS
```

它**对每一个 `step` 都成立**：`m < 1` ⇒ `min_gear × m < min_gear`，而 `quantize_size_fraction` 的
`max(1, …)` 保证任何正数至少落到最小一格 ⇒ 结果恒等于 `min_gear` ⇒ 判据恒真。于是 `step=0.05`
也会被判成「三个机制全部失效」——**与 spec 自己的门禁 `G-C4`（要求 `(0.05, …).erased == ()`）直接
矛盾**。

**修法**：改判**失效面占比**——数一数该系数在这套阶梯上有多少格是彻底空转，过半即判 `erased`：

```python
dead = #{g ∈ ladder | quantize_size_fraction(g × m, step) >= g − eps}
erased = m < 1 且 dead × 2 >= gears
```

`m=0.5` 时只有 `g >= 2×step` 的格降得下去，故 `dead` 恒为 1，而分母是格数：`0.5`→1/2 判、
`0.25`→1/4 不判、`0.05`→1/20 不判、`1.0`→1/1 判。四档全部符合 `G-C4`，且这个量本身更有信息量
（界面因此能说出「2 格里有 1 格不生效」而不是一句无量纲的断言）。前后端两份实现同判据，
`G-C5` 用内联的后端产物表冻结。

### I-4　`quantize_size_fraction` 的 Python 舍入是**银行家舍入**，JS 的 `Math.round` 不是

`gears = max(1, min(round(lo / step), len(ladder)))` 里的 `round()` 在 Python 上是**银行家舍入**
（`round(0.5) == 0`），而 JS 的 `Math.round(0.5) === 1`。恰好半格的输入（如 `quantize(0.25, 0.5)`）
会让前后端**给出不同的格**。TS 孪生里显式复刻了银行家舍入，并用一条专门的用例钉住这四个断面。

### I-5　`live_auto/executor.py` 引用 `ai_auto/orphan.py` 是安全的，但**只因为** `ai_auto/__init__.py` 是惰性的

`ai_auto/__init__.py` 用 PEP 562 `__getattr__` **惰性导出** manager 符号，顶层只 `from .admission
import get_admission_lock`（零重依赖）——这正是为打破 `ai_auto → live_auto → ai_auto` 而写的。
故 `live_auto/executor.py` 的 `from ..ai_auto.orphan import …` 不成环（`live_auto/manager.py:17`
已有同款先例）。**这条依赖它，改 `ai_auto/__init__.py` 的人必须知道。**

同族的另外两处**确实成环、已用延迟导入打破**，两处都在函数体内并各带一行理由：

- `signals.resolve_consensus_gate` → `gear_resolution`（后者要 `signals.position_ladder`，范式与
  同模块既有的 `contrarian_schema` 逐字相同）；
- `session.adopt_orphan` → `resume.adopt_position`（`resume` 顶层要 `manager.AiTradeGroup`）。

### I-6　`CostRuntime` 拿结算覆盖率用 setter，不串 5 层形参

spec §4.F F-3 要 `build_reconciliation` 收 `settlement` 与 `capital_usd`。从会话到那里是
`schedule_maintenance → _bounded_maintenance → _maintenance_round → reconcile_tick → _run_reconcile`
**5 跳**，再加两个实参会把中间三个纯搬运函数一起顶破「形参 ≤5」的红线。

**修法**：`CostRuntime.note_settlement(executor)` —— 一个同步、零 IO、鸭子类型读的 setter，
范式逐字镜像既有的 `claim_estimate(resting)`。会话在排后台对账**之前**调它（晚一步就会用上一轮
的旧读数）。

### I-7　轴 C 的地板判据同时装配到回测

spec 只描述了实盘。但 `build_sizing_runtime` 是实盘与回测的**唯一**解析点，而一条只在实盘存在的
风控会让回测系统性偏乐观（它会成交一批实盘根本不会开的单）——那正是「回测与实盘各写一份」这条
本仓库反复付过代价的形状。故 `parse_floor_policy(settings)` 装在那个唯一解析点上，两侧同源；
两条判据各自的回退档不变（`zero_is_veto=False` / `floor_ratio=0.0` ⇒ 逐字回退）。

### I-8　`_settling` 建立那一轮，浮盈要冻结的是**上一轮**的值

不变式 I-2 说「冻结在侦测那一刻」。但侦测那一刻交易所侧**已经空了**，`_net_upl` 恒返回 0——直接
冻结当轮值等于冻结一个 0，界面仍会显示「持有中 / 浮盈 0.00」，即 `RV-P1-9` 点名的那个假数字原样
复发。实现里先存下进入本轮时的两个值，确认要进未决态时再回填。用例
`test_settling_freezes_stance_and_books_nothing` 在写实现之前就是红的。

### I-9　三条被既有门禁冻结、本轮**如实更新**的断言（逐条说明为什么不是放水）

| 门禁 | 改动 | 为什么这不是放水 |
|---|---|---|
| `test_live_executor_limit.py::test_resting_open_does_not_set_projected` | `expire_ts == _TS + ttl` → 按墙钟断言，并加断 `created_ts != _TS` | 它冻结的恰恰是 I-1 那个缺陷（`_TS` 是一个捏造的 1000，与墙钟无关）。同轮新增 `..._gate_off_restores_bar_ts_stamp` 把旧行为钉在回退档上 |
| `test_ai_trade_sizing_stats.py::test_merge_lands_at_the_call_site…` | 整串调用表达式比对 → 拆成两条（含 `merge_into(` 与 `metrics["aiStats"]`） | 它要守的是「合并发生在调用点而不是 `_compute_ai_stats` 里」；`merge_into` 加第二个实参后调用换了行，整串比对会在一次**纯格式变化**上变红，而那与它要守的不变式毫无关系 |
| `lib/aiTrade.test.ts::AI_TRADE_POSITION_STEP_OPTIONS` | 冻结集合加 `0.05` | 判据一个字没改（离散档位、绝不暴露 `0`），另补一条 `every(v > 0 && v <= 1)`。加 5% 是 spec §4.C C-3 明列的改动，且它是轴 C-2 那句警示唯一能点进去的出路 |

### I-10　`--long` / `--short` / `--accent` 不得进 `color:` —— 仓库级对比度门禁

`components/ui/contrastTokens.source.test.ts` 扫描全部源码，禁止面色出现在任何 `color:` 内联样式
或 `text-[var(--short|long|accent)]` 里（它们是给 `backgroundColor` / `borderColor` 用的填充色，
当文字色对比度不达标）。结算覆盖率那一行初版用了 `var(--long)`，被这道门禁当场逮住，已改
`var(--long-ink)`。**新增任何着色文案前先读那个文件。**

### I-11　`sampleScope` / `contrarianBlocked` 必须同时进 `_status_payload` 与 `aiStats`

`_compute_ai_stats` 的产物只落 `metrics_json`，而**实时页读的是成员级 `status` 快照**。只落一处会
让另一页的读点恒为空——这正是 `trendVerdict`（评审 RP1-2）与 `liveness` 已经各付过一次代价的形状。
两个键因此在 `_status_payload()` 里各有一份（都是进程内读数、零 DB 读，可以搭那班同步 payload 的车）。

### I-12　跨字段不变式换了一条，但形状相同

`RV-P1-2` 要求的 `resting_max_age_s < limit_order_ttl_s` 随轴 E 的推迟一并消失。同形的那条改为
**`ai_trade_settlement_poll_interval_s < ai_trade_settlement_deadline_s`**：节流间隔 ≥ 死线时，
未决窗内**一次取数都发不出去** ⇒ 轴 A 100% 静默空转，而界面上只会显示一个没人读得懂的
`ratio = 0`。启动期 `model_validator` 直接拒绝，范式与既有的
`alert_pending_ttl_s ≥ 2 × live_auto_poll_interval_s` 相同。

### I-13　本轮**未做**的一件 spec 列项（如实登记，不假装做完）

**§9.4 真机人工验收 8 步**需要真实账户、真实持仓与真实断网，实施节点无法执行——这是本轮唯一
一件 spec 列了而没做完的事。其中两步已各有对应的离线门禁：

- 第 7 步（`RV-P0-3` 专项：结算中的 90 秒里点一次手动平仓）↔ `G-A13`
  （`test_settling_freezes...` / `test_outbound_close_is_short_circuited_while_settling` /
  `test_finish_settling_is_idempotent_when_another_path_booked_first`）；
- 第 8 步（`RV-P0-1` 专项：`step=0.5` 上让模型做一次逆势开仓）↔ `G-D5` / `IT-4`。

**但离线门禁替代不了真机验收**：它们证明的是「给定这份输入，代码这样判」，证明不了「真机上那份
输入真的会出现」。第 2 / 3 / 4 / 5 / 6 步（手动平仓的结算价、手工开仓的孤儿告警、断网回落、
老包→新包续跑、九闸全关跑 30 分钟）没有任何离线替代品，必须由有账户的人跑一遍。

`.claude-index/config.md` 的阈值偏离登记**已按 §十二 追加**（5 行，行数取 `wc -l` 实测值），
不在未做之列。
### I-14　两处 spec 未覆盖的缺口（实现期自查发现，均已补；形状与本轮评审 P0 相同）

两条都是**「新支路绕过了既有支路上的某件事」**——与评审那五条 P0 是同一个形状。

1. **`pause()` 缺结算 drain。** A-6 写的是「`stop()` / `pause()` 的既有收尾链」，而这两者在本仓库
   是**两条独立函数**（`pause` 的第 ④ 步 `_pause_unwind` 在默认的 `flatten=False` 下只撤挂单）。
   初版只在 `stop()` 里插了 drain ⇒ 暂停契约会在「`projected` 仍说有仓 / 交易所侧早已空 / 这条腿
   还没落账」的断面上定格，冷续跑时 `resume` 拿这份契约与交易所对不上，**一笔真实发生过的平仓会
   在历史详情里整条消失**。已在 ④ 与 ⑤（读交易所持仓）之间补 drain——必须排在⑤与⑦落契约之前。
2. **未决态期间降级恢复被推迟最多 90 秒。** `_clear_degraded_if_recovered` 住在 `_on_reconcile_ok`
   里，而未决态刻意**不进**那个函数（I-2 的分派）。于是一次落在结算窗口内的连接恢复要等一整个
   `deadline` 才被承认，界面上那条「盯市已降级」会挂着不走——一条与轴 A 毫无关系的静默回归。
   已在分派处补一次调用，并加用例
   `test_connection_recovery_is_still_acknowledged_while_settling`。

### I-15　`lib/store.ts` **不在本轮 diff 里**，这是对的

§7.3 列了 `store.ts`（「`settling` / `orphan` 两块进 WS 归并，整体 spread、不逐键手抄」）。核实后
本轮**一行都不用改**：`patchAiTradeMemberStatus` 早已是

```ts
session: m.session ? { ...m.session, ...(status as Partial<AiTradeSessionRow>) } : m.session
```

——**整体 spread**，新键天然合并进去。仓库里已有两条用例把这件事写在注释里
（`aiTradeAcceptanceStream.test.ts` / `aiTradeDiscipline.test.ts`：「`patchAiTradeMemberStatus`
是整体 spread，本来就不吞键——守它等于没守」）。本轮缺的只是**类型声明**（缺了 tsc 会红，
但数据本来就在），已在 `AiTradeSessionRow` 与成员行两个接口上各补一份。

登记它是为了让终审不必再核一遍「为什么 §7.3 点名的文件没出现在 diff 里」。

### I-16　轮级槽需要一个异常出口

`_finish_settling` 先置 `_pending_settlement_meta`、再调 `_book_external_close`。而
`_synthesize_external_close` 的 `except Exception` 会返回 `[]`（合成失败 best-effort，绝不影响
reconcile 主流程）——此时槽已经置好、却**没有任何成交去认领它**，于是它会被**下一笔**平仓
`setdefault` 进去：一份张冠李戴的结算归因，而且看起来完全正常。

已在 `_finish_settling` 尾部补：本轮没产出 `trade` 事件 ⇒ 就地清空槽。用例
`test_the_meta_slot_is_cleared_when_no_trade_was_produced`（替身让 `_make_trade` 抛异常）。

这是「消费即清空」这条既有纪律的异常出口——`session._pending_horizon_meta` 那份范式里没有它，
是因为那个槽的产出方不会吞异常。

### I-17　匹配判据 5 也必须 fail-open —— 否则轴 A 对「接管来的孤儿持仓」100% 空转

判据 5（`cTime <= entry_ts + tol`）的左端取自本地开仓腿。而**接管来的孤儿持仓没有开仓腿**：
`adopt_orphan` 刻意不伪造一条（伪造会污染台账、进换手闸、进成本模型），故 `_entry_wall_ts()`
对它返回 `0`。此时 `cTime <= 0 + 60` 对任何真实时间戳都为假 ⇒ **恒不命中** ⇒ 轴 A 对这一类持仓
静默空转，而它恰恰是最需要真值的那一类（它本来就是一笔来路不明的仓）。

这与 `RV-P0-4` 判据 2 的形状完全相同：**「不知道」不等于「不匹配」**。故 `entry_ts <= 0` 时整条
判据跳过；其余五条判据（`instId` / `uTime` 时间窗 / `posId` 未认领 / 非 0 价 / 取最早）一条不减。
用例 `test_an_unknown_local_entry_time_skips_the_open_window_instead_of_rejecting_everything`
同时断言了「跳过判据 5」与「其余判据照常拦」两半。

### I-18　`settlement_pending` 需要一条前端标签

I-2b 的互锁按 spec 的指示「记既有的 `last_close_block`、**不新增降级码**」实现——没有往
`open_funnel._DEGRADE_PREFIXES` 或 `LAYERS` 加任何一格（`settling` 是中间态不是拦截）。

但 `last_close_block` 在会话侧会并进 `degrade`（`degrade or executed.get("closeBlocked")`，与既有
`exit_order_slot_busy` 同一条通路），于是它会作为 `degradeReason` 到达界面。没有标签，用户看到的
就是一串生的机器码 `settlement_pending`。已在 `DEGRADE_REASON_LABELS` 补一条，措辞与同族的
`exit_order_slot_busy` 对齐（都是「本轮真的没平成」）。

这不违反「不新增降级码」：那条纪律说的是**不要为它新造一层漏斗格 / 归类前缀**（它不是一道闸），
而不是让它以裸码示人。

---

## 终审（合入前的最后一道）发现并当场修毕的缺陷

> 终审节点产出。逐条对照 §七《文件 / 模块变更计划》、§八《配置项与总闸》、§九《测试与验收》三张表
> 之后的结论：**覆盖度全部落地**（唯二偏离是 `I-1` 的轴 E 推迟与 `I-13` 的真机验收，两条都已如实
> 登记）；13 项设置在 `config.py` 与仓库根 `.env.example` 双向对齐；§9.1 的 54 条门禁只有 `G-G3`
> 一条有表无用例。下列 7 组（`I-19`..`I-25`，其中 `I-25` 是并列的 5 处小项）是终审在源码里**直接修完**的，不留 TODO。
>
> 值得记一笔的共性：这些修法里有 5 处是**同一个形状**——「这一轮算出了一个数，却没有任何人会读它，
> 或者读它的那句话说的不是它」。本轮自己在原则 6 里点名了这个形状，还为它做了机械化门禁
> `G-X2`；这 5 条全部是从 `G-X2` 的覆盖缝里漏过去的。门禁只守它自己列进表里的那些键。

### I-19　`SettlementFact.funding_usd` / `liq_usd` 是两个死字段，而两处 docstring 都说它们进了 `explain.meta`

`settlement.py` 的字段注释写着「**只进 explain.meta，绝不进本地账本**」，
`_synthesize_external_close` 的 docstring 又复述了一遍。核实：`settlement_meta_wire` 的产物里
**没有它们**（spec §4.A A-3 的八键表里也没有——A-4 那句话与 A-3 那张表本来就对不上），
全仓唯一一处读 `fact.funding_usd` 的地方是 `test_settlement_core.py` 的一行断言。

它们不是可有可无的：交易所口径的 `pnl` 本来就不含资金费与强平罚金，于是
`exchangeGrossPnlUsd` 与用户在 OKX 上看到的那个数之间的**全部差值**就是这两项。丢掉它们，
这条腿的归因就缺一块，而这一段是它们唯一能落的地方（进本地账本会让 `reconcile` 的可比口径
`realized + funding + liq` 的差额恒等于它们本身，`G-A12` 守的就是这条）。

**处置**：`settlement_meta_wire` 命中结算时无条件补 `exchangeFundingUsd` / `exchangeLiqUsd`
（命中时这两个值是**已知的**，含已知为 0，故与 `exchangeFeeUsd` 同姿态无条件写），
`lib/types.ts` 同轮补两条类型声明。两处 docstring 因此变成真话，一个字都不用改。

### I-20　地板否决轮的 wire 是**另造**出来的，于是它与自己的产物互相矛盾

`_floor_wire` 拿 `SizingOutcome(fraction=0.0, fraction_raw=…)` 现造了一个默认产物去喂
`_outcome_wire`，而真正返回给调用方的是另一个带齐 `f_vol` / `f_kelly` / `vol_source` /
`scale_source` 的产物。后果是：在一个「波动率目标项算出来了、**正是它**把 `f_raw` 压到零」的
轮次上，面板会显示 `volSource="disabled"`、`fVol=null`，而 `fCap` 退化成 `rule.f_max`
（`min([…] + [f_max])` 里那两项都是 `None`）——**这一轮的读数会告诉用户「本轮上限就是最大仓位」**，
而事实是这一轮的解算结果是零。

这正是本轮在治的那类假数字，只不过它长在本轮自己新写的那段代码里。

**处置**：先构造真实的 `vetoed` 产物，再 `_floor_wire(vetoed, inputs, rule)`；
函数签名随之从 `(f_raw, verdict, …)` 改为 `(outcome, …)`，并把「必须由本轮真实产物构造」的
理由写进 docstring。既有断言（`out.wire["floorVerdict"] == "solved_zero"`）不受影响。

### I-21　`_gear_note` 读的那个 settings 键**不存在**，而且它读 settings 这件事与它自己的纪律相反

```python
step = float(getattr(facts.settings, "ai_trade_default_position_step", 0.0) or 0.0)
step = float(getattr(facts, "position_step", 0.0) or step)
```

两条都错：

1. `Settings` 上的字段叫 `ai_trade_default_position_step_pct`（`config.py:205`），带 `_pct` 后缀。
   `getattr(..., "ai_trade_default_position_step", 0.0)` 因此**恒返回 0.0** —— 这是一行从写下
   那一刻起就没有任何作用的死码。
2. 即便名字写对了它也是错的。`DisciplineFacts.position_step` 的字段注释与
   `discipline_runtime` 的转发注释各写了一遍同一条纪律：「步长是决策器的属性，从 settings 再读
   一次会拿到一个『用户改了但运行期并没有生效』的数」。而 `step <= 0` 是**连续档位**这个合法
   运行态本身——回落到某个默认值，等于对一个根本不存在的阶梯报警。

**处置**：删掉回落，真源只留 `facts.position_step`，并把「为什么不能回落到 settings」写在原处。

### I-22　`volSizingBelowFloor` 有写点没读点

`discipline_runtime._gate_counts` 同轮加了四个透传键，`_gear_note` 只读了三个。
`belowFloorVetoes` 恰恰是总闸 9（`ai_trade_sizing_floor_ratio`，默认 0.0 = 关）**唯一**的决策
依据——spec §八 第 9 项自己写着「合适的比值需要先有 `belowFloorVetoes` 的观测数据」。没有读点，
那份数据就永远到不了会做这个决定的人手里。

**处置**：`_gear_note` 补一句（与「解出零」那句**分开**，两者不可合并计数：一个是恒等式，
一个是可调阈值）。历史详情侧的 `SizingCard` 早已在读同名的 `aiStats` 键，本条补的是纪律自检那一侧。

### I-23　轴 D 的三个 wire 键：一个在前端**零读点**，另两个在**全仓**零读点

`downgrade_wire` 产出 `sizeGearBefore` / `sizeGearAfter` / `downgradeLane` 三个键。核实：

- `downgradeLane` 在 `apps/web` 里一次都没被读过。而 `TrendGateRow`（`AiTradeTraceRound.tsx`）
  仍然无条件写「（强制降档 ×{sizeMult}）」——那句话在轴 D 之前是对的，在之后**两条新通路上都
  是假的**：`gear` 掉的那一格由阶梯决定、与系数无关；`floor_is_min` 更是
  `sizeBefore === sizeAfter`，界面会写出「仓位 50% → 50%（强制降档 ×0.5）」这一行自相矛盾的字。
  **本轮修的是「逆势降档从来没真的发生过」，而唯一会把这件事显示给人看的那一行，说的是另一回事。**
- `sizeGearBefore` / `sizeGearAfter` 在后端、用例、`apps/web` **三处都没有读点**，且与两把闸
  wire 上早已有的 `sizeBefore` / `sizeAfter`（前端真的在渲染那一对）严格冗余。
- 另有一处不对称：`signals._veto` 用 `step=0.0` 去调 `downgrade_wire`，于是否决轮的两个格号
  一起退化成 `-1`，随后又多写一行 `wire["downgradeLane"] = lane`（`downgrade_wire` 刚刚已经
  写过）；而镜像的 `trend_verdict._veto` 只写 `downgradeLane`。两把「刻意共用同一个谓词」的闸
  在 wire 上却不同形，恰是 `RV-P0-1` 要防的那种半边状态。

**处置（对 spec §4.D「wire 三键」的偏离，及理由）**：

1. **删掉 `downgrade_wire`，两把闸各自 `wire["downgradeLane"] = lane` 一行**（各写一份、不合成，
   `RV-P0-1` 的那条理由一字不改）。两个格号键随之消失——留着它们就是本轮自己在原则 6 里明令
   禁止、并且专门做了机械化门禁去守的那个形状，而它们能回答的问题 `sizeBefore` / `sizeAfter`
   已经回答得更清楚。
2. `TrendGateRow` 新增 `describeDowngradeLane(gate)`：`gear` ⇒「在档位阶梯上下调一档」、
   `floor_is_min` ⇒「已在最低档位，无法再降，按最低档放行」、其余（含**老会话 / 总闸关**，
   那时 `downgradeLane` 整键缺席）⇒ 逐字回退旧文案。`AiTradeTrendGate` 同轮补类型。
3. `G-X2` 的 `KEYS` 补 `downgradeLane`，`componentMarkers` 补
   `["downgradeLane", "describeDowngradeLane"]`。**这条门禁当初漏掉这个键，是门禁自己的覆盖面
   不够**——它只守它列进表里的那些键，而轴 D 的键一个都没列。

### I-24　`G-G3` 有表无用例（§9.1 列了、实现没写）

`G-G3`（「仓位卡在 `scaledRounds == 0` 时**不渲染**档位抹除那一行」）是 §9.1 的 54 条门禁里
唯一一条在全仓 grep 不到的。`SizingCard` 的实现是对的（`{scaled > 0 && …}`），缺的只是把它冻住。

**处置**：在 `aiTradeFillTruth.source.test.ts` 里补一条**源码级**门禁（`SizingCard` 不导出，
为守一个渲染门控把它导出去是更大的代价）。正则按仓库成文教训写 `\r?\n`，否则
`core.autocrlf=true` 的干净检出上恒红。

### I-25　四处「说的与做的不是同一件事」

| # | 处 | 事实 | 处置 |
|---|---|---|---|
| a | `TradeExplainDetail.SettlementLine` | `settlementRejectReason` 直接渲染裸机器码：用户看到的是「降级原因 fee_residual_negative」。本仓库为这条纪律付过多次代价（`DEGRADE_REASON_LABELS` 是同一范式），而 `I-18` 刚刚为 `settlement_pending` 立过同一条规矩 | 加 `SETTLEMENT_REJECT_LABELS` 译表；**未登记的码原样透出**（显示一个没预料到的原因，好过让用户以为没有原因） |
| b | `orphan.orphan_wire` | `if math.isfinite(_num(upl_usd))` 是一个**恒真**的守卫——`_num` 已经把 NaN / inf / 非数值收敛成 `0.0`。读起来像在挡什么，实际什么都挡不住（清洁代码 §五：不为「理论上不会发生」的分支写防御） | 去掉守卫，无条件写；口径写进注释 |
| c | `AiTradeOrphanBanner` 的呈现纪律 ① | 写着「`message` 一个字都不在前端拼」，而组件里 `orphanMessage()` 逐字复述了后端那句「它不受持仓周期、利润保护与活性看门狗的任何管辖…」。纪律与实现分叉，而这条纪律存在的全部理由就是防分叉 | 改写为它**真正**在守的那条：判据与读数一个字都不在前端算；告警（后端单点下发、含三条补救）与横幅（持续挂在屏幕上的现状）分工不同 |
| d | `lib/aiTradeFunnel.LAYER_LABEL` / `sizing_runtime._to_hold` | 前者注释写「排在 `baseRuleMaBand` 之后」而字面排在它之前（查表顺序无关紧要，但注释与代码互相矛盾会训练读者不信注释）；后者夹在 import 与模块常量之间、且只空一行（仓库 `ruff` 只跑 `F,E9`，`E302` 不会红） | 各自按注释 / 按仓库风格归位 |
| e | `lib/aiTradeTypes.ts` 的活性看门狗段 | 轴 A/B 的三个新接口被**插在** `AiTradeLiveness` 的 docstring 与 `AiTradeLiveness` 本体之间：那段 docstring 因此谁都不修饰、`AiTradeSettling` 没了文档、`AiTradeAdoptOrphanResult` 头上叠了三段。与上一轮终审在 `DisciplineStrip` 上抓到的是同一个形状 | 三段 docstring 各归其位；三个新接口移到 `AiTradeLiveness` 之后并另起一条「成交真值化与孤儿持仓」段旗（此前它们落在「活性看门狗」那面旗下） |

### 终审跑测

- 后端全量 `pytest tests -q`：**5360 passed / 7 failed / 1 skipped（19 分 32 秒）**。红全部落在未跟踪的在制文件 `tests/test_factor_library_expansion.py`（属 `factor-library-crypto-expansion`，测 `app/engine/factors.py`，本轮一行未碰），与动代码之前的基线逐条相同 ⇒ **本轮新增 0 条红**。
- 前端 `vitest run`：**231 个文件 / 2672 passed，零红**。
- `ruff check app --select F,E9` 全绿；`from app.main import app` 正常；`tsc --noEmit` 全绿。

### 终审判定为「不是缺陷、不要改」（写下来防止下游重复投入）

1. **`markPxAtDetect` / `exchangeGrossPnlUsd` / `settlementCloseTs` 在 `apps/web` 没有读点，这是可以的。**
   `explain.meta` 在本仓库一直是一份**落库的取证 blob**，而不是一份渲染契约：既有的
   `tripSeq` / `notionalUsd` / `realizedGrossBps` / `stopAtrMult` / `costConfidence` 五个键
   同样零读点，且都是有意为之。本轮新增的这三个与它们同类。**`sizeGearBefore` / `sizeGearAfter`
   之所以被删，不是因为「没读点」，而是因为它们走的是 trace wire 且与已渲染的
   `sizeBefore` / `sizeAfter` 严格冗余**——两件事不要混为一谈。
2. **`aiStats.sizing` 的 `restingCancelled` 没有前端读点，这是可以的。** 同一段里既有的
   `reversalSkips` / `corrScaledRounds` / `exposureUnreachableRounds` / `restingSkips` /
   `atrStopRounds` / `floorAboveCapRounds` 六个键全部零读点。`restingCancelled` 是
   `restingExpired` 的父集，而卡片上呈现的是更有信息量的那个子集。
3. **`_note_resting_cancel` 里 `expired` 同时给 `resting_expired` 与 `resting_cancelled` 各 +1，
   这是对的。** 两处 docstring 都写着「其中」——`restingExpired ⊂ restingCancelled` 是刻意的
   包含关系，不是重复计数。
4. **`session._status_payload` 里 `contrarian_blocked()` 被调用两次，不改。** 它是一次
   `dict()` 浅拷贝、零 IO；为省这一次拷贝把它提成局部变量，会把那一整块「整键省略」的条件
   表达式拆成两行，可读性的损失大于收益。
