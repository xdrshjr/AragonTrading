# AI 自动交易 · 交易所侧兜底自愈与回测净额真值（交易周期模式 · 第四轮）

> **状态**：**v2（已评审，有条件通过）** —— 评审记录见 §零，评审结论见文末 §十二。
> **上游**：`ai-trade-holding-horizon-modes`（第 1 轮，已合入 `11246ae`）、
> `ai-trade-horizon-acceptance-loop`（第 2 轮）、`ai-trade-turnover-budget-and-backstop`
> （第 3 轮）——第 2+3 轮已一并合入 `e7fdda4`（63 文件）。
> **本轮闭合**：调查约束 **#8**（长持仓必须保留交易所侧持久化止盈止损）的后半程，
> 与调查约束 **#7** 在回测世界的最后一公里（第 3 轮登记的欠账 **D-9**）。
> **范围纪律**：只加法、可一键回退、不改任何既有门禁判定式、不动 `live_auto`、零 DB 迁移。

---

## 零、评审记录（v1 → v2）

> 评审方式：把 v1 的 40 条依赖核实**逐条重跑**（结论：40 条全部属实，无一条行号漂移——这是本仓库
> 迄今第一份零漂移的依赖表），再沿着「数据从产生到上屏、从请求到落单」的每一跳补查 v1 **没有**
> 追过的 6 条（response_model 过滤、notes 宿主、status 推送节律、判定价年龄、平仓路径的名义上限
> 豁免、`horizon_rule is None` 时的快照塌陷）。**四条 P0 全部出自这 6 条补查**——它印证了第 2 轮
> 评审留下的那条方法论：**依赖表只能守住作者已经想到的那些跳**。
>
> 全部 P0 / P1 已在正文改毕（每条在正文相应位置带 `[P0-n]` / `[P1-n]` 标记）。P2 列在此处备查，
> 由实现节点自行取舍，但**不得静默忽略**：取舍结论写进 §十一。

### P0（阻断，v2 已修）

| # | 问题 | 为什么是 P0 | 修法（正文落点） |
|---|---|---|---|
| **P0-1** | **`algoClOrdId` 里没有任何标的 / 会话身份。** v1 的式子是 `f"aragonh{epoch_hex}{attempt:02x}"`，`epoch_hex` 取会话 `_position_seq`——而 `_position_seq` 每个会话从 **0** 起（`session.py:190`），且**每个成员是一个独立会话**（`manager.stop_member` 按 `(group_id, inst_id)` 找的就是成员各自的 `AiTradeSession`）。于是一个 3 标的分组里，三名成员的第一次补挂算出来的 ID **逐字节相同**；会话重启后同样与上一会话相撞 | 幂等设计的**唯一**目的是让重复请求被 OKX 以「重复 ID」拒绝而不是变成第二张单。ID 一旦跨成员碰撞，这个机制就从「防重复」翻转成「**第二个成员永远补不上**」——而且失败原因是一个看起来完全正确的去重码，最难排查的一类。多标的是本产品的默认形态（第 6 轮 `ai-trade-multi-symbol` 起） | §2.2.4 改为 `arh{sid6}{epoch4}{att2}`，`sid6` 由 `(inst_id, session.started_ts)` 派生的稳定短 hash；新增 T-31b 跨成员不碰撞用例 |
| **P0-2** | **`blocked(coverage_unknown)` 这一格永不可达。** v1 §2.2.1 把 a/b/c/d 写成合取式，c 是「`hasStop is False` **或** `coverageRatio` 有数且 `< min_coverage`」，d 是「`coveredSz is not None`，否则 `blocked(coverage_unknown)`」。但 `coveredSz is None` 只可能发生在**有止损腿、张数与 `closeFraction` 都不可解析**时（`exchange_backstop.py:192-212`），此时 `hasStop` 为真且 `coverageRatio` 为 `None` ⇒ **c 先不成立 ⇒ 返回 `idle`**，d 永远轮不到。反过来 `hasStop` 为假时 `_covered_size` 恒返回 `0.0`，`coveredSz` 也不可能是 `None` | v1 自己把 (d) 称作「本轮最关键的一条安全判据」，还为它配了 T-5、一个降级码、一句 prompt 文案与一个前端标签——**四处呈现挂在一个恒不生效的判据上**。这与第 3 轮 P0 群、第 2 轮 P0 群是同一个形状：新判据取自不携带该信息的入参 / 排在一个先返回的分支之后 | §2.2.1 的 c 改为「`hasStop is False` **或** `coverageRatio is None` **或** `coverageRatio < min_coverage`」（未知也进入处置流，交由 d 归类）；§4.5 判据顺序保持 `coverage_unknown` 最先；新增 T-5b 守「有止损腿但张数不可核对 ⇒ blocked 而非 idle」 |
| **P0-3** | **`backstopRepair` 块会被 `response_model` 整块丢掉，且请求侧的回填链整条缺失。** `GET /ai-trade/horizon-profiles` 声明了 `response_model=AiTradeHorizonProfilesOut`（`api/ai_auto.py:298`），该模型是显式字段列表（`schemas.py:739-757`）——`profiles_wire` 多下发一个键，FastAPI 序列化时**静默剔除**。而 v1 §4.2 恰恰规定「整块缺席 ⇒ 前端不渲染控件」，于是控件永远不渲染。请求侧同样断链：v1 §3.2 只写了「`api/ai_auto.py` 的 `_resolve_horizon` 回填 `backstopRepair`」，但 `_resolve_horizon` 只是 `resolve_horizon_rule(raw.model_dump(), …)` 的两行透传（`api/ai_auto.py:150-151`），真正的解析点在 `hold_horizon.resolve_horizon_rule`，而 `HorizonRule`（`hold_horizon.py:141-179`）没有承载模式的字段、`rule_wire` 也没有镜像位 | 两端同时断：用户**看不到**控件、即使看到了值也**到不了**运行时。整条轴 C 与 `arm` 模式一起变成死码，而 §6 的用例表里没有一条会红（U-20 用的是手造 wire，S 组不碰 profiles 端点） | §3.2 补 `schemas.AiTradeHorizonProfilesOut.backstopRepair` + `AiTradeHorizonIn.backstopRepair`；§3.2 补 `hold_horizon.HorizonRule.backstop_repair_mode` 字段、`resolve_horizon_rule` 回填、`rule_wire` 镜像；§6.1 新增 S-19/S-20 两条**端点级**用例（打真端点，断言键在回执里） |
| **P0-4** | **张数的两个真源指向两个不同的仓。** v1 §2.2.2 写 `sz = snap_size(exchange_abs_pos − coveredSz)`，而 `plan_repair` 是纯函数、手里只有检测层的 `positionSz`——那个数是 `executor.entry_qty_sz`（`exchange_backstop.py:435`），即**本会话自管仓**，不是账户实时仓。`_abs_open_pos`（`okx/trading.py:194-206`）按 `(instId, posSide)` 汇总的是**账户上该方向的全部持仓**。两者在「账户上另有 `live_auto` / 手工持仓」时不相等。且 v1 的 H2 对 `sz > abs_pos` 是**抛 `LIVE_BACKSTOP_SIZE_EXCEEDS`**而非收敛 | 两个方向都出事：`abs_pos > 自管仓`时，补出来的止损**覆盖了本层并不拥有的仓位**——触发时替用户平掉了他手工建的仓，这正是 v1 §2.1 第 2 条不变式想守却没守住的那一半（「永不建立敞口」守住了，「永不处置别人的敞口」漏了）；`abs_pos < 本地读数`（部分成交后本地未及对齐）时，H2 抛错、烧掉一次 H3 配额、并向用户推一条吓人的失败——把一次读数偏差渲染成一次真实故障 | §2.2.2 的式子改为 `sz = snap_size(max(0, min(abs_pos, self_position_sz) − covered_sz))`，且**下单时序里按实时 `abs_pos` 重算一次**（`plan` 里的数只是预览）；§2.2.3 H2 第 2 条由「越界即抛」改为「越界即向下收敛，收敛后 `< min_sz` 才拒」；新增 B-19b / T-7b |

### P1（重要，v2 已修）

| # | 问题 | 修法 |
|---|---|---|
| **P1-1** | **H2 第 3 条把名义上限套在一条减仓腿上，与仓库既有纪律正面冲突。** `close_live_position` 的 docstring 明写「平仓**不**应用名义上限与反向减仓护栏（平仓即减仓，是动作目的）；仅以实时 `\|pos\|` 二次校验 `sz≤\|pos\|`」（`okx/trading.py:224-225`）。而 `live_max_order_notional_usd` 默认只有 **1000 USDT**（`config.py:51`）：单笔开仓受它约束，但**多次加仓累积出的持仓可以远超它**——于是仓位越大、越裸露、越需要兜底，补挂越是被本地拒死 | §2.2.3 H2 第 3 条改为「名义只做**记录**，不做拒绝判据；真正的上界是 `sz ≤ abs_pos`」，并逐字引用 `trading.py:224` 的既有纪律；B-10 相应改判 |
| **P1-2** | **H1 不判判定价年龄。** v1 只判 `mark_px` 有限且为正。但「补一张会立刻触发的止损 = 立刻市价平仓」这条风险的真正触发条件是**价格陈旧**——用一个 90 秒前的价去算「现价仍在止损有利侧」，正是本仓库 `ai_trade_profit_guard_max_px_age_s` 这个旋钮存在的全部理由，且 `hold_horizon` 已经复用了同一个数（`HorizonRule.max_px_age_s`，`hold_horizon.py:177-179`）。第 3 轮的 D-1 阻断级修正（新鲜度判据取错时钟 ⇒ 保护层 90% 时间恒 unavailable）就是这条的前车之鉴 | §2.2.3 新增 H1-e0：`px_age_s ≤ rule.max_px_age_s` 否则 `blocked(px_stale)`；`px_age_s` 由 `session._last_px_wall` 派生（`session.py:1212` 的既有口径，**唯一真源**），经 `schedule_repair` 形参传入；值域 / prompt / 前端标签三处同步加码 |
| **P1-3** | **状态流转对用户不可见。** v1 §2.4.3 规定「其余状态变化随既有 status 快照下发」——但 `status` 事件只在**换向 / 唤醒跳过计数 / 决策轮 / 停会话**时推（`session.py:367/415/516/527/1353/1556`），并**没有**挂在 5s 盯市 tick 上。而自愈层的目标用户恰恰是 `trend_hold`：唤醒周期 **0**、决策轮以小时计。于是 `planned → placing → placed → verified` 的整条流转可以几个小时不上屏，「立即补挂」按钮点完之后界面纹丝不动 | §2.4.3 改为：**状态或 `blockedReason` 发生变化即补推一次 status**（沿用 `session.py:524-527` 唤醒跳过计数的既有范式：变化才推，天然节流）；§3.2 `session.py` 预算由 14 提到 **18**（多一个 2 行的 `_push_status` 薄封装），并在 §6.3 放行条件 4 同步改数 |
| **P1-4** | **未装配周期契约时，自愈层有写路径却没有任何呈现面。** `HorizonRuntime.refresh` 在 `rule is None` 时直接返回 `None`（`horizon_runtime.py:151-152`），`last_snapshot` 恒 `None` ⇒ `status.horizon` 为 `null` ⇒ 挂在 `horizon.backstop.repair` 上的 wire、卡片第五行、prompt 子句**全部消失**；而 `RepairRuntime` 本身只受自己的运维总闸约束，`arm` 模式照样发单 | §2.2.6 增加装配前置条件：**`horizon_rule is None ⇒ `rule_from_settings` 返回 `None`**（整层不装配）。一条用户完全看不见的自动写路径不允许存在——这条比「少一个功能」严重得多。新增 T-1b |
| **P1-5** | **`RepairRuntime` 的依赖纪律自相矛盾，且人工通道拿不到断面。** §4.5 写「绝不 import `hold_horizon` / `horizon_runtime`」是对的，但同时写「只 import `exchange_backstop` 的三个常量与 `exchange_oco_px`」——而它必须调 `okx.order_builder` / `okx.trading.place_backstop_stop` / `db.session.get_sessionmaker` 才能下单落审计（`exchange_backstop.py:26-28` 本身就 import 了 `..okx.trade_client` 与 `..okx.trading`，先例俱在）。另一处矛盾：`schedule_repair` 的 docstring 说「运行时不碰 executor」，但 `repair_now(backstop_wire, now_wall)` 只收 wire，`mark_px` / `local_sl_px` / `order_in_flight` / `px_age_s` 无从取得 | §4.5 改为**两段分层**：`plan_repair` 纯函数（零 import 业务模块）／`RepairRuntime` 允许 import `okx.*` 与 `db.session`（与 `exchange_backstop` 同级）。断面收敛为一个 `RepairFacts` 值对象 + 唯一读取点 `facts_from_executor(executor, px_age_s=…)`，**自动与人工共用**；`repair_now` 改收 `RepairFacts` |
| **P1-6** | **轴 B 落地后，回测面板会继续说一句已经变成假话的话；且披露句挂错了宿主。** `backtest.py:1094-1095` 现有一句 notes：「已知欠账：回测里给换手迟滞与 edge 门禁用的**成本估算**仍不含资金费（实盘含），两者的不对称留待下一轮…」——轴 B 正是来还这笔账的，不改它就等于让面板对用户撒谎。另一半：v1 §3.2 把新的口径句挂在 `hold_horizon_text.format_backtest_notes`，而该函数 `rule is None ⇒ 返回 []`（`hold_horizon_text.py:421-422`）——**未装配周期契约的对照臂完全没有披露**，而对照臂恰恰是验收时最需要对拍的那条 | §2.4.4 / §3.2 把资金费口径句的宿主改为 `backtest._funding_notes()`（`backtest.py:1084`，本就是资金费口径的唯一披露出口），并**同时改写** `:1094-1095` 那句欠账披露；F-12/13 改为断言 `_funding_notes()` 的产物 |
| **P1-7** | **`turnoverSeverity` 加两格会静默打乱 chip 的分支映射。** 该函数返回 0…4 的整数且**越小越严重**（`aiTradeTurnover.ts:186-201`），而 `describeTurnoverChip` 用 `severity === 0/1/2/3` 硬编码分支读它（`:208-229`）。中间插两格 ⇒ 冷却态被渲染成预算态、预算态返回 `null`——面板上最显眼的那一格开始说错话。v1 §3.4 只列了 `turnoverSeverity` | §2.4.2 / §3.4 明写两函数**必须同一处改**，并给出自愈的 chip 文案；新增 U-15b（chip 在 `planned` / `failed` 下的标签与色调） |
| **P1-8** | **`AiTradeLaunchModal.tsx` 不在文件计划里** ⇒ 新控件没有状态宿主，值到不了 `POST /ai-trade/start`（`tradingHorizonPayload` 在 `AiTradeLaunchModal.tsx:444-450` 组装，`AiTradeHorizonFields` 是纯受控组件，`AiTradeHorizonFieldsProps` 里没有任何新字段的位置）。且该文件带着一条**成文的复发纪律**：新增受控输入**必须**塞进既有的 `[open]` effect（`:309-310` / `:318-319` / `:335-337`），另写 effect = `ai-trade-modal-revert-recurrence` 复发（输入约 1 秒后被冲回默认） | §3.4 补 `AiTradeLaunchModal.tsx`（state + 塞进既有 `[open]` effect + 进 payload）与 `AiTradeHorizonFieldsProps` 两个新 prop；§6.2 新增 U-21：**改动新控件后等待一个渲染周期，值不得回退**（复发守门用例） |
| **P1-9** | **轴 B 对 edge 门禁的那一半在默认配置下恒为零。** `evaluate_edge_gate` 第一句就是 `k_min <= 0 ⇒ return None`（`edge_gate.py:210-212`），而 `ai_trade_edge_gate_k_min` 默认 **0.0**（`config.py:401`）。v1 §2.3.3 的影响面表把「最小 edge 门禁变严」与 G3 并列陈述，读起来像是两条都会发生 | §2.3.3 表格加一列前置条件并如实标注；F-10/11 改为**显式把 `k_min` 设为正数**再断言（否则是一条恒过的空用例）；§6.4 runbook L4 同步 |
| **P1-10** | **自愈控件可能被跨日条件门控吞掉。** 同屏最相近的既有元素 `BackstopNotice` 只在 `targetHoldMaxSeconds >= 86400` 时渲染（`AiTradeHorizonFields.tsx:378-379`），即只有 `short_swing` / `trend_hold` 看得见。v1 §2.4.1 只说「在既有『换手纪律』两行契约摘要之后」，没有声明门控 —— 实现节点顺手挂进 `BackstopNotice` 是最省事的写法，而随单 OCO 丢失与持仓周期长短无关 | §2.4.1 明写：控件**不受跨日条件门控**，四个预设 + `custom` 一律渲染；U-20b 用 `hourly_swing` 断言控件在场 |

### P2（建议，实现节点自行取舍，结论写进 §十一）

| # | 问题 | 建议 |
|---|---|---|
| P2-1 | 曲线起点之前的那几根 K 线 `snapshot_at` 恒 `None` ⇒ **同一次回测里前若干根不含资金费、其后含**，而 notes 是一个全局布尔 | 统计「含资金费的决策轮 / 总决策轮」并如实写进 notes（`覆盖 N/M 轮`） |
| P2-2 | runbook L4 的「`netPnl` **应下降**」推不出来：资金费**不进权益曲线与 metrics**（`backtest.py:1091-1093`），轴 B 只改门禁入参 ⇒ 交易序列变化后 pnl 方向未定 | 判据改为「`cost_bps` 上升且 `avgHold` 不降」；`netPnl` 只观察不判定 |
| P2-3 | 审计行没写 `algoId` / `algoClOrdId` 落哪一列 | `ord_id=<algoId>`、`cl_ord_id=<algoClOrdId>`（`LiveOrderAuditValues` 两列都是自由 String，零迁移） |
| P2-4 | `place_backstop_stop` 的 `AsyncSession` 由谁开未写 | 沿用 `live_auto/executor.py:132-134` 的 `get_sessionmaker()` 短生命周期 session（**绝不复用长 session**，R3） |
| P2-5 | 按钮请求态归属矛盾（§2.4.2 要三态、§3.4 要卡片无状态） | 父组件传 `repairPending` / `repairResult`（按 `instId` 键），卡片仍无状态 |
| P2-6 | 自愈模式未进前端偏好回填链（`defaultTradingHorizon`），用户每次开弹窗都要重选 `arm` | 明确取舍：**建议不回填**（每次重新授权更安全），但要在 §十一 写下这是一次有意的选择而非遗漏 |
| P2-7 | `turnoverSeverity` 里硬编码的覆盖阈 `0.999`（`aiTradeTurnover.ts:194`）是既有债，新增分支若沿用会二次固化 | 新增分支只读 wire 的 `state`，不复制任何阈值 |
| P2-8 | 端点路径里的 `instId` 需 `encodeURIComponent` | 沿用 `aiTradeApi.stopMember` 的既有写法 |

---

## 一、概述

### 1.1 要解决的问题

前三轮把「交易周期模式」这件事做到了这样一个位置：契约可选（4 预设 + custom）、平仓侧有
G1/G2/G3 三道确定性闸、开仓侧有 G4/G5 两道闸、验收指标是净额口径、交易所侧兜底**从一句没核实
过的话变成了一份带三态 `basis` 的核实结果**。对着代码逐行核对，还剩两个张着口的缺口，而且它们
恰好分别卡在这条特性链的**安全前提**与**验收前提**上。

**缺口 #1 —— 检测到「没有兜底」之后，系统什么都不做。**

第 3 轮建成的 `exchange_backstop.py` 能诚实地告诉模型与用户三件事：交易所侧现在**确实**挂着什么
（`basis == "exchange"`）、覆盖了持仓的百分之多少（`coverageRatio`）、以及最危险的那一格——
**本地回执声称挂过止损、而交易所侧确认一条都没有**（`mismatch`，`exchange_backstop.py:147`）。
但它对这个结论的全部处置只有两件：改写 prompt 的措辞（`hold_horizon_text.py:335-341`），
以及由 G6 拦下「在裸露仓位上继续加仓」（`exchange_backstop.py:269-301`）。

也就是说：**一个 `trend_hold` 会话（目标持仓 1–3 天、唤醒周期 0）在发现自己的止损单不存在之后，
会继续裸奔 1–3 天。** 系统知道，模型知道，用户在卡片上也能看到——唯独没有任何一层去把那张单
补回来。而第 1 轮的宪章原话是「即使本进程退出它们依然生效，故你**不必每轮盯盘**，可以按目标
周期规划退出」（`hold_horizon_text.py:192`，理由写在同文件 `:96` 与 `:173`）：整条长周期模式的
授权链条，就架在这张单真的存在之上。第 3 轮已经给这句话加了一道闸——`backstop_armed=False`
时改口（`hold_horizon_text.py:187`）——但「有核实通道」与「核实结果是有单」是两件事，
后者今天仍然只能靠运气。

第 3 轮把不做自动补挂写进了非目标，并给了明确的理由与顺位：

> **不做交易所侧兜底的自动补挂 / 重挂**（§2.3.5 三条理由）。这是本轮之后**最该做的下一件事**，
> 且已具备前提：本轮建成的检测层就是它的触发器。
> —— `ai-trade-turnover-budget-and-backstop/spec.md:1273-1274`

本轮就是那件事。它需要一条**全新的下单写路径**（`POST /api/v5/trade/order-algo`），
这是本仓库全部 AI 侧安全层第一次新增下单能力，因此本轮的设计重心不在「怎么发这个请求」，
而在**用什么锁住它，才不会让一个安全层变成一台亏钱机器**（§2.2.3 的三道锁与 §七 的风险表）。

**缺口 #2 —— 回测的成本估算里，资金费恒为零。**

第 2 轮已经把资金费做实了：`FundingLedger` + `BacktestFundingCurve` 拉真实历史结算点、按真实
周期计提，回测的**盈亏**因此是含资金费的（`backtest.py:621 / 632 / 639 / 655`）。但**成本模型**
的两个入口在回测侧仍然硬传 `funding=None, funding_enabled=False`：

| 位置 | 代码 | 消费方 |
|---|---|---|
| `backtest.py:698-703` | `query_for_preview(..., funding=None, funding_enabled=False)` | `HorizonFacts.cost_bps` → **G3 换手迟滞阈** |
| `backtest.py:778-783` | `evaluate_edge_gate(..., funding_enabled=False)` | **最小 edge 门禁** |

结果是：**回测里的 G3 与 edge 门禁按「资金费为 0」在判定，实盘按真实资金费在判定。**
对 `hourly_swing`（目标持仓 0.1–2h）这点差别可以忽略；对 `trend_hold`（1–3 天，
`expected_hold_bars` 派生后跨 9 个 8h 结算点）它是一笔与一次往返手续费同量级的钱——
按 0.01%/8h、9 期计，约 **9 bps**，而 `intraday_swing` 的一整趟双边 taker 费也不过 10 bps 量级。
方向还全是一边倒的：**回测系统性地比实盘乐观，而且模式越长越乐观。**

这条缺口的要害不在数字，在于它污染的是**验收路径本身**。调查原文的最后一段写得很清楚：

> 建议先采用「4H + 唤醒周期 0 + 低杠杆」做净额回测，验收指标至少包含平均持仓时间、每日换手
> 次数、手续费占毛利润比例、**资金费后净收益**和不同退出来源。没有通过样本外回测前，
> 不宜直接放大实盘资金。

一个在成本估算里把资金费当成 0 的回测，没有资格为「资金费后净收益」背书——**用它去挑模式，
挑出来的一定是最长的那个**。第 3 轮把它登记为欠账 D-9 并说明了不修的理由（「修它会当场改变
G3 迟滞与 edge 门禁在回测里的判定，与本轮『不动任何既有门禁判定式』的边界正面冲突」）。
本轮的边界与第 3 轮不同：本轮**就是**来改这个判定的，而且改的方向是**让回测向实盘收敛**。

### 1.2 要建的东西

三条轴，全部加法，全部可一键回到不装配，全部只会让系统更保守或更诚实：

**轴 A · 交易所侧兜底自愈（H 层：`backstop_repair.py`）。** 新纯核心 + 新运行时 + 一条新的
**只补止损腿**的写路径。触发器就是第 3 轮的检测结果（`basis == "exchange"` 且核实为未覆盖），
执行体是一张 `reduceOnly` / `posSide` 锁死的条件单，**张数恒为「交易所实时持仓 − 已被覆盖」的
差额**，触发价恒取本地回执口径的止损价并**必须**位于现价的不利侧。三道锁（H1 判定锁 / H2 越界锁 /
H3 频率锁）与两种模式（`plan` 仅提醒 / `arm` 自动补挂）见 §2.2。

**轴 B · 回测净额真值。** 把第 2 轮已经拉到手的真实历史资金费曲线接进回测的成本模型，
使 G3 与 edge 门禁在回测里读到与实盘同口径的往返成本；曲线不可用时**逐字回退**到今天的行为
（绝不用一个猜出来的 8h 去污染判定），并在回测免责里如实说明本次用的是哪一种口径。

**轴 C · 呈现与知情（后端算了 ≠ 用户看得见）。** 启动弹窗新增一个两段式的自愈模式控件与一行
契约摘要；运行卡新增一行自愈状态与一颗**「立即补挂」**按钮（人工授权的单次执行）；prompt 的
兜底行新增自愈子句；回测面板新增资金费口径行。

### 1.3 为什么这样切

- **A 是 B 的因，B 是 A 的验收。** 长周期模式之所以敢让模型「不必每轮盯盘」，唯一凭据是交易所
  侧那张单；而用户之所以敢选长周期模式，唯一凭据是回测里的净收益。两条凭据一条修事实、一条
  修口径，是同一件事的两面。
- **触发器已经建好，本轮只加执行体。** 判定「有没有兜底」的全部难题（三态 `basis`、三态覆盖率、
  `closeFraction` 与 `reduceOnly` 的归属判定）第 3 轮已经解决并配了守门用例。本轮**一行都不改**
  `exchange_backstop.py`，只在它的产物上加一层执行。这既是最小改动面，也让「检测」与「执行」
  的职责边界天然清晰。
- **新增下单路径这件事，必须一次做对。** 本仓库此前所有 AI 侧安全层的共同纪律是「绝不新增下单
  路径」，本轮是第一次破例。因此设计上采取三条超额约束：**只减不增**（补出来的单只可能减少
  敞口，永不建立敞口）、**只补差额**（不知道覆盖了多少就不补）、**默认不自动执行**
  （`plan` 模式 + 一键人工授权，用户在弹窗里显式选择才进 `arm`）。见 §九 D-1。
- **规模自我克制。** 三条轴各自独立回退（三个总闸）；不动任何既有门禁的判定式（轴 B 改的是
  门禁的**入参口径**，不是判定式）；不新增 DB 列；不改 `live_auto` 与 `engine/simulator`；
  不撤单、不改价、不补止盈腿、不自动平仓。

### 1.4 前置事实核对（本轮依赖的既有构件，逐条已按行核实）

| # | 依赖 | 位置 | 核实结论 |
|---|---|---|---|
| 1 | 检测层取数 | `exchange_backstop.py:55-95` | `fetch_algo_backstop` 并发查 `oco`+`conditional`，失败返回 `None`，**绝不上抛** |
| 2 | 检测层分类 | `exchange_backstop.py:99-149` | 三态 `basis` + 三态 `coverageRatio`；`coveredSz` 算不出即 `None`（:192-212） |
| 3 | 覆盖判据的归属锁 | `exchange_backstop.py:170-189` | `_is_closing_leg` 先判 `posSide` 归属再判方向；`reduceOnly` 只作充分条件 |
| 4 | G6 覆盖闸 | `exchange_backstop.py:269-301` | 五条同时成立才拦，`coverageRatio is None` 一律放行（fail-open） |
| 5 | 检测层运行时 | `exchange_backstop.py:305-421` | 单飞 `_task` + `force_next()` + `close()`；`wire(now_wall)` 是唯一只读出口 |
| 6 | 会话侧调度缝 | `session.py:486-487` | `schedule_from_executor(self._backstop, self.executor, ...)` 已在盯市 tick 上 |
| 7 | 开仓成交后立即复核 | `session.py:312` | `self._backstop.force_next()` 已挂在 `_dispatch` 的开仓分支 |
| 8 | 兜底价真源 | `profit_guard_runtime.py:74-89` | `exchange_oco_px` 首选最近一笔开仓 `trade` 的触发价，取不到才现算（`source="derived"`） |
| 9 | **签名 POST 客户端** | `trade_client.py:27-58` | `_post` 已具备：JSON 字节与签名预像严格一致、429 指数退避、`TransportError` 重试、非 429 的 4xx/5xx 直抛 |
| 10 | **下单业务码归一范式** | `trade_client.py:60-75` | `place_order` 先看顶层 `code` 再看 `data[0].sCode`，任一非 `"0"` 即抛原始 sCode |
| 11 | **`order-algo` 写端点不存在** | `trade_client.py:60-247` | 全类的写方法只有 `place_order`（:60）/ `cancel_order`（:77）/ `close_position`（:145）/ `set_leverage`（:168）四个，**没有任何算法委托写路径**——本轮必须新增 |
| 12 | 平仓腿方向纯函数 | `order_builder.py:212-219` | `_close_side(pos_side, pos)`：平多 `sell` / 平空 `buy`；net 模式按 `pos` 符号 |
| 13 | 两种持仓模式的锁侧写法 | `order_builder.py:222-244` | `net_mode ⇒ reduceOnly=True 且不带 posSide`；对冲 ⇒ 带 `posSide` 且不带 `reduceOnly` |
| 14 | 取整与格式化纯函数 | `order_builder.py:75-91` | `snap_size` 向下取整、`snap_price` 就近取整、`fmt_step` 按步进位数字符串化 |
| 15 | 随单 OCO 的既有写法 | `order_builder.py:115-127` | `slTriggerPx` + `slOrdPx="-1"` + `slTriggerPxType="last"`——本轮补挂的三个字段与之逐字一致 |
| 16 | 下单时序范式 | `okx/trading.py:144-157` | `_load_meta → ensure_instrument_supported → posMode → get_positions → validate → place → audit` |
| 17 | 实时持仓是 sz 上限的唯一可信源 | `okx/trading.py:194-200` `_abs_open_pos` | 平仓路径已确立「绝不信本地 pos」的纪律（`close_live_position`:220 起），本轮补挂沿用 |
| 18 | 审计表 | `db/repositories.py:908-930` | `LiveOrderAuditValues` 含 `ord_type` / `sl_trigger_px` / `sz` / `status` / `error_code`，**零迁移**即可承载补挂留痕 |
| 19 | 执行器持有的元数据 | `live_auto/executor.py:83-101` | `creds` / `account_id` / `inst_id` / `td_mode` / `pos_mode` / `ct_val` / `lot_sz` / `min_sz` / `settings` 齐备；**无 `tick_sz`**（须由 `_load_meta` 取） |
| 20 | 在飞挂单标记 | `live_auto/executor.py:118` | `self.resting` 非 `None` ⇒ 有一笔限价委托在途（本轮 H1 的排除条件之一） |
| 21 | 兜底文案五分支 | `hold_horizon_text.py:318-352` | `backstop` 键缺席 ⇒ 逐字返回本轮之前的两句；在场则按 `basis` 分五支 |
| 22 | 未核实态文案 | `hold_horizon_text.py:355-372` | `local` / `unavailable` 共用一句，差别只在括号里的原因 |
| 23 | 周期快照装配 | `hold_horizon.py:607-662` | `build_snapshot(..., backstop=None)`；`backstop is not None ⇒ snapshot["backstop"] = backstop` |
| 24 | 预设表 wire | `hold_horizon.py:468-469` | `profiles_wire` 已下发 `backstopCheckEnabled` / `backstopPollSeconds` 两键，本轮同款追加 |
| 25 | 运行时转发 | `horizon_runtime.py:179` | `backstop=None if self.backstop is None else self.backstop.wire(now_wall)` |
| 26 | **回测成本入口 1** | `backtest.py:698-703` | `funding=None, funding_enabled=False` → `HorizonFacts.cost_bps` → G3 |
| 27 | **回测成本入口 2** | `backtest.py:778-783` | `evaluate_edge_gate(..., funding_enabled=False)` |
| 28 | 回测已有的真实费率曲线 | `backtest.py:416-441` `_load_funding_curve` | 分页拉 `get_funding_rate_history`，异常全吞，产物落 `self._curve` |
| 29 | 曲线纯核心 | `funding_ledger.py:389-426` | `BacktestFundingCurve`：`points_in` / `available`（≥2 点）/ `interval_hint`（最近两点之差） |
| 30 | 成本模型的资金费口径 | `cost_model.py:137-159` | `funding_periods = ceil(hold_s / interval)`（至少 1 期）；只取不利侧；`rate is None ⇒ degraded` |
| 31 | 「有意不建模」vs「缺数据」 | `cost_model.py:209-234` `query_for_preview` | `funding_enabled=False ⇒ rate=0.0 ⇒ confidence 保持 ok`；`funding=None 且 enabled ⇒ rate=None ⇒ degraded` |
| 32 | edge 门禁已开的资金费入参 | `edge_gate.py:185-190` | 签名已有 `funding=None` 与 `funding_enabled` 两个形参，**本轮无需改签名** |
| 33 | 快照形状 | `funding.py:25-38` `FundingSnapshot` | `rate` / `funding_ts` / `next_funding_ts` / `interval_s` / `fetched_wall` / `interval_known`；模块**不 import 任何 ai_auto 模块**（`funding_ledger` 可安全引用，无环） |
| 34 | 前端载体 | `aiTradeTypes.ts:407-423` / `:469-471` | `AiTradeBackstopWire` 是 `horizon.backstop` 的**整对象**透传（不吞新键） |
| 35 | 前端文案层 | `aiTradeTurnover.ts:105-180` | `describeBackstop` 五分支镜像后端；`null` 灰、`0` 红的色彩纪律 |
| 36 | 运行卡 | `TurnoverBackstopCard.tsx:88-95` | `buildRows` 四行拼装，全空 ⇒ 整卡不渲染 |
| 37 | 降级码标签表 | `apps/web/lib/aiTrade.ts:495-555` | 前缀匹配表，已含 `horizon_backstop_uncovered`（:543） |
| 38 | 前端 API 层 | `apps/web/lib/aiTradeApi.ts:43-57` | 成员级 POST 的既有写法：`/ai-trade/${groupId}/member/${instId}/stop` |
| 39 | 成员级端点范式 | `api/ai_auto.py:282-288` + `manager.py:540-551` | `stop_member` 按 `(group_id, inst_id)` 查成员，查不到回 `None` → 404 |
| 40 | 请求契约落点 | `schemas.py:555-595` `AiTradeHorizonIn` | 校验只在本层做（422），服务端 `_resolve_horizon`（`api/ai_auto.py:138`）只回填不复夹 |
| **41** | **[P0-3] 预设表是 `response_model`** | `api/ai_auto.py:298` + `schemas.py:739-757` | `AiTradeHorizonProfilesOut` 是显式字段列表 ⇒ `profiles_wire` 多下发的键会被 FastAPI **静默剔除**；新块必须同时在 schema 上开字段 |
| **42** | **[P0-3] 契约真正的解析点** | `hold_horizon.py:299-344` `resolve_horizon_rule` | `api/ai_auto.py:150-151` 的 `_resolve_horizon` 只是两行透传；`HorizonRule`（:141-179）无模式字段、`rule_wire`（:380-404）无镜像位——回填链要在 `hold_horizon` 里补 |
| **43** | **[P1-3] status 的推送节律** | `session.py:367/415/516/527/1353/1556` | 全量 `_status_payload()` 只在换向 / 唤醒跳过计数 / 决策轮 / 停会话时推，**不挂 5s 盯市 tick**；`:524-527` 是「计数成功即补推一次」的既有范式 |
| **44** | **[P1-2] 判定价年龄的唯一真源** | `session.py:226/626/1212` `_last_px_wall` | `px_age_s = max(0, now - _last_px_wall)`；`hold_horizon` 已复用 `ai_trade_profit_guard_max_px_age_s` 作阈（`HorizonRule.max_px_age_s`） |
| **45** | **[P1-1] 平仓路径豁免名义上限** | `okx/trading.py:224-225` | 「平仓**不**应用名义上限与反向减仓护栏（平仓即减仓，是动作目的）；仅以实时 `\|pos\|` 二次校验 `sz≤\|pos\|`」——补挂腿同属减仓腿，沿用同一纪律 |
| **46** | **[P1-4] 未装配周期契约即无快照** | `horizon_runtime.py:151-152` | `rule is None ⇒ refresh() 返回 None ⇒ last_snapshot 恒 None ⇒ status.horizon 为 null`；挂在 `horizon.*` 下的一切呈现随之消失 |
| **47** | **[P1-6] 资金费口径的既有披露出口** | `backtest.py:1084-1099` `_funding_notes` | 已含一句「已知欠账：成本估算仍不含资金费」——轴 B 落地后**必须同时改写它**，否则面板继续说假话 |
| **48** | **[P1-9] edge 门禁默认空转** | `edge_gate.py:210-212` + `config.py:401` | `k_min <= 0 ⇒ 直接 return None`，而默认 `ai_trade_edge_gate_k_min = 0.0`：轴 B 对 edge 门禁的影响默认恒为零 |
| **49** | **[P1-8] 弹窗的受控输入纪律** | `AiTradeLaunchModal.tsx:282-337` / `:444-450` | 新增受控输入**必须**塞进既有 `[open]` effect（另写 effect = `modal-revert` 复发）；`tradingHorizonPayload` 在 `:444` 组装 |
| **50** | **[P1-7] chip 与 severity 的耦合** | `aiTradeTurnover.ts:186-201` / `:204-230` | `turnoverSeverity` 返回 0…4（**越小越严重**），`describeTurnoverChip` 用 `severity === 0/1/2/3` 硬编码读它——插格必须两处同改 |
| **51** | 预设表的迟滞系数 | `hold_horizon.py:105-138` `PROFILES` | 四个预设 `hysteresis_k ∈ [1.2, 2.0]` 均为正 ⇒ **G3 在默认配置下是活的**，轴 B 经 G3 这条路可观测（与 #48 相反） |

> **⚠️ 实现节点必读（工作树现状）**：`git status` 显示工作树里混着**不属于本特性**的在制变更：
> `ai_trade_enabled` 总闸翻转（`.env.example` / `services/quant/app/config.py` /
> `services/quant/tests/test_config.py` / `desktop/src/lib/config.js`）、`desktop/scripts/*` 打包脚本、
> `.claude-index/` 与 `docs/diagnoses/*` 等未跟踪目录。**本轮的精确暂存必须逐条剔除它们**
> （沿用第 3 轮 R-8 的做法：混合文件按 hunk 级暂存，且 patch 必须以 **bytes** 读写——
> Windows 上 `write_text` 会翻 CRLF 弄坏 patch）。

---

## 二、技术设计

### 2.1 架构位置与数据流

```
  ┌───────────────── 盯市 tick（session._monitor_tick，5s）─────────────────┐
  │  reconcile → 保护层 → horizon.refresh → cost.schedule_maintenance      │
  │                                                                        │
  │  schedule_from_executor(self._backstop, ...)     ← 第 3 轮：只读核实    │
  │        │  单飞后台任务 · 只在持仓时发 · TTL 120s                        │
  │        ▼                                                               │
  │  BackstopRuntime.wire(now) ── basis/coverageRatio/hasStop/mismatch      │
  │        │                                                               │
  │        ▼                                                               │
  │  ★ schedule_repair(self._repair, self._backstop, executor, ...)         │
  │        │                                                               │
  │        ├─ plan_repair(...)  ← 纯函数：三道锁（H1/H2/H3）                │
  │        │      ├─ 不适用 ⇒ None                     ⇒ state="idle"      │
  │        │      ├─ 被锁   ⇒ RepairPlan(blocked=...)  ⇒ state="blocked"   │
  │        │      ├─ mode="plan"  ⇒ 方案就绪、等人工   ⇒ state="planned"   │
  │        │      └─ mode="arm"   ⇒ 立即执行           ⇒ state="placing"   │
  │        │                                                               │
  │        └─ 单飞后台任务 place_backstop_stop(...)                        │
  │               ├─ 成功 ⇒ state="placed" + backstop.force_next()         │
  │               │        （下一次核实确认覆盖达标才升 "verified"）        │
  │               └─ 失败 ⇒ state="failed" + WS error + force_next()       │
  └────────────────────────────────────────────────────────────────────────┘

  人工通道：POST /api/ai-trade/{gid}/member/{inst}/backstop/repair
            → 同一个 plan_repair + 同一条 place_backstop_stop（绕过 mode，不绕过 H1/H2/H3）

  决策轮（不变）：… → horizon(G1/G2/G3) → cooldown → turnover(G4/G5) → backstop(G6) → …
            ★ 本轮**不新增任何门禁**：自愈层从不否决、从不改写 decision。
```

**两条硬边界**：

1. **自愈层永不参与决策。** 它不返回 `TradeDecision`、不产生降级码、不进 `gate_reason` 的
   或链。它唯一的输出是「交易所侧多了一张止损单」这个事实，以及一段如实的文案。
   本轮不新增任何 G 编号，正是为了让这条边界在命名上就刺眼。
2. **自愈层永不建立敞口。** 它发出的每一张单都带 `reduceOnly`（net 模式）或 `posSide`
   （对冲模式）锁死为平仓腿，且张数恒 `≤` 交易所实时持仓的未覆盖部分。这条不变式写进
   `backstop_repair.py` 的模块 docstring 顶部（放行条件 2）。

### 2.2 轴 A：交易所侧兜底自愈

#### 2.2.1 什么时候算「需要补挂」

判据**只取自第 3 轮已经算好的那份 wire**，不做第二次分类（本仓库已为「同一口径两处各存一份」
出过三次事故）。需要补挂 ⟺ 下列全部成立：

| 判据 | 取值来源 | 不成立时的 `state` |
|---|---|---|
| a | `wire["basis"] == "exchange"` | `idle`（未核实就动手 = 盲目下单，绝不允许） |
| b | `side in ("long", "short")` 且 `positionSz > 0` | `idle` |
| c | **[P0-2]** `hasStop is False` **或** `coverageRatio is None` **或** `coverageRatio < min_coverage` | `idle` |
| d | `coveredSz is not None`（**已覆盖张数必须可知**） | `blocked(coverage_unknown)` |

> **[P0-2] c 为什么必须收下 `coverageRatio is None`。** v1 把 c 写成「`hasStop is False` 或
> `coverageRatio` **有数**且 `< min_coverage`」，于是 d 变成一格死码：`coveredSz is None` 只可能
> 发生在「有止损腿、但张数与 `closeFraction` 都不可解析」时（`exchange_backstop.py:192-212`），
> 那一格里 `hasStop` 为真、`coverageRatio` 为 `None` ⇒ **c 先不成立、直接 `idle`**，d 永远轮不到；
> 而 `hasStop` 为假时 `_covered_size` 恒返回 `0.0`，`coveredSz` 同样不可能是 `None`。一个降级码、
> 一句 prompt、一个前端标签、一条 T-5 用例全挂在一个不可达分支上——这正是本特性链前三轮反复
> 出过的那个形状。**c 的语义应是「覆盖没达标**或**还不知道达没达标」，两者都要进入处置流；
> 「不知道」由 d 归类成 `blocked(coverage_unknown)` 并如实告诉用户，而不是悄悄当成「没事」。
>
> 注意这**不会**误伤最常见的随单 OCO：`sz` 为 0 / 缺省而 `closeFraction == "1"` 的形态下
> `coverageRatio` 被算成 `1.0`（`exchange_backstop.py:135-137`），c 因「已达标」不成立 ⇒ `idle`，
> 与 v1 行为逐字相同。

> **(d) 是本轮最关键的一条安全判据**，它的理由与 G6 的 fail-open 完全对称、方向却相反：
> G6 面对「不知道」时**放行**（不知道不等于没有，不该剥夺交易能力）；自愈层面对「不知道」时
> **不动手**（不知道覆盖了多少，就算不出该补多少张）。若强行按全仓补，在**对冲模式**下补出来的
> 那张单不带 `reduceOnly`（`order_builder.py:238-241` 的既有纪律：对冲模式靠 `posSide` 锁侧），
> 一旦原有止损其实存在，两张单同时触发就会**把仓位打穿到反向**。这不是理论风险，
> 它是「把安全层变成亏钱机器」的最短路径。
>
> `coveredSz` 的三态语义见 `exchange_backstop.py:192-212`：核实成功且一条止损腿都没有 ⇒ `0.0`
> （确定的结论）；有腿但交易所未回报可核对的张数 ⇒ `None`（不知道）。因此「一张都没挂」这条
> 最常见、也最需要自愈的路径，(d) 恒成立。

#### 2.2.2 补什么单：价、量、方向

- **只补止损腿。** 沿用第 3 轮 D-7：兜底的定义是「最坏情况下有人替你退出」，止盈腿不构成兜底。
  本轮**不补止盈**（非目标 §十）。
- **触发价** `stop_px` = `exchange_oco_px(executor, side)` 的 `sl_px`（`profit_guard_runtime.py:74-89`，
  首选最近一笔开仓成交回执里的 `stopLossPrice`，取不到才现算）。**不新建第二条求价路径**——
  这个数就是「我下单时打算附上的那个价」，补挂的语义正是「把当初打算挂的那张单补上」。
  取不到（两条路径都是 `None`）⇒ `blocked(no_local_px)`。
- **张数** `sz` = **[P0-4]** `snap_size(max(0, min(exchange_abs_pos, self_position_sz) − covered_sz), lot_sz)`。
  三个入参各有各的角色，**缺一不可**：

  | 入参 | 取自 | 它守的是什么 |
  |---|---|---|
  | `exchange_abs_pos` | 补挂时序里**现拉**的 `get_positions()`（`okx/trading.py:194-206` `_abs_open_pos`） | 「绝不信本地读数」——本地偏大时不能按本地下 |
  | `self_position_sz` | 检测层 wire 的 `positionSz`（= `executor.entry_qty_sz`，`exchange_backstop.py:435`） | 「**绝不处置本层不拥有的仓**」——账户上另有 `live_auto` / 手工持仓时，`abs_pos` 是全账户口径 |
  | `covered_sz` | 检测层 wire，**原样转发不重算** | 只补差额 |

  > v1 只写了 `exchange_abs_pos − coveredSz`，两端都会出事。**偏大方向**：账户上另有非本层持仓时，
  > 补出来的那张 `reduceOnly` 止损覆盖的是**全账户**该方向的仓，一旦触发就替用户平掉了他自己建的
  > 仓——§2.1 第 2 条不变式守住了「永不建立敞口」，却漏了「永不处置别人的敞口」，而后者同样是
  > 真实资金。**偏小方向**：部分成交后本地读数暂时偏大时，v1 的 H2 会抛 `LIVE_BACKSTOP_SIZE_EXCEEDS`、
  > 烧掉一次 H3 配额、并向用户推一条吓人的失败事件——把一次正常的读数时序差渲染成一次故障。
  >
  > `plan_repair` 手里只有 `self_position_sz`（纯函数、无 IO），故它算出的 `sz` 是**预览值**；
  > `place_backstop_stop` 在拿到实时 `abs_pos` 后**必须按上式重算一次**，并以重算结果下单
  > （B-19b 守它：入参给一个偏大的 `sz`，断言实际下单的 `sz` 是收敛后的那个数）。

  取整后 `< min_sz` ⇒ `blocked(size_below_min)`（尾仓补不出来是事实，如实说，不四舍五入放大）。
- **方向** 由 `order_builder._close_side(pos_side, pos)` 给出（依赖核实 #12），
  锁侧写法由 `pos_mode` 决定（依赖核实 #13），**一行都不新写**。

#### 2.2.3 三道锁

**H1 · 判定锁（纯函数，`plan_repair`）** —— §2.2.1 的 a/b/c/d 四条，外加三条：

- **[P1-2] e0. 判定价必须新鲜：`px_age_s ≤ rule.max_px_age_s`，否则 `blocked(px_stale)`。**

  > e 那条判据（「现价仍在止损的有利侧」）的**全部效力都依赖这个价是新的**。拿一个 90 秒前的价
  > 去论证「还没越过」，恰恰是「补一张会立刻触发的止损」最现实的发生路径——市场在这 90 秒里
  > 走过去了，而系统按旧价确信自己是安全的。本仓库为此专门有一个旋钮
  > （`ai_trade_profit_guard_max_px_age_s`），且 `hold_horizon` 已经复用它作
  > `HorizonRule.max_px_age_s`（`hold_horizon.py:177-179`）——「判定价多老算老」在一个会话里
  > 只能有一个答案，本层是第三个复用方，**不新开旋钮**。
  >
  > `px_age_s` 的唯一真源是 `session._last_px_wall`（`session.py:226/626/1212`），由
  > `schedule_repair` 作为形参传入纯核心。**绝不**在本层另找一个时钟——第 3 轮的 D-1 阻断级修正
  > （新鲜度判据取了只在确认根更新的 `_last_candle_wall` ⇒ 15m 执行下保护层 90% 时间恒
  > unavailable）就是取错时钟的成本。

- **e. 现价必须仍在止损价的有利侧，且距离 ≥ `min_gap_bps`。**
  多头要求 `stop_px < mark_px × (1 − gap)`，空头要求 `stop_px > mark_px × (1 + gap)`。
  不满足 ⇒ `blocked(px_through)`（已越过）或 `blocked(px_too_close)`（太近）。

  > 这是三道锁里唯一一条「不补比补更安全」的判据，必须写在最刺眼的位置：**补一张会立刻触发的
  > 止损，等于立刻市价平仓。** 那不是兜底，那是替模型做了一个它没做的退出决策，而且是在最坏的
  > 价位上做。价格已经越过意向止损时，正确的动作是**高声报告**（WS error + prompt 明说
  > 「现价已越过本地止损价、交易所侧无兜底、退出完全在你手上」），把决定权交回给决策层与
  > 利润保护层——它们本来就是干这个的。
  >
  > `mark_px` 恒取 `executor` 断面的实时价（与 `schedule_from_executor` 同源）；
  > 非有限 / 非正 ⇒ `blocked(no_mark_px)`（沿用 `hold_horizon._is_bad_px` 的「算不出绝不动手」极性）。

- **f. `executor.resting is None`。** 有限价委托在途时持仓正在变化，此刻算出的差额张数下一秒
  就可能过期 ⇒ `blocked(order_in_flight)`。

**H2 · 越界锁（纯函数，`validate_algo_stop`，在 `okx/order_builder.py`）** —— 与
`validate_order` / `validate_close` 同址同范式，本地拦截、**绝不发往 OKX**：

1. `stop_px > 0` 且有限；
2. **[P0-4]** 取整前先按 §2.2.2 的式子**向下收敛**到 `min(abs_pos, self_position_sz) − covered_sz`，
   取整后再判 `sz ≥ min_sz`，不足即抛 `LIVE_BACKSTOP_REJECTED_LOCAL`；**收敛之后仍 `> abs_pos`
   在数学上不可能**，故 `LIVE_BACKSTOP_SIZE_EXCEEDS` 只作为最后一道断言存在（命中即说明上游算错了，
   属于必须炸的内部不变式违反，而不是一次正常的用户可见失败）。
   > v1 把「超界」写成一次正常的抛错路径，于是一次本地读数时序差就会被渲染成一次真实故障并烧掉
   > 一次 H3 配额。**能收敛的越界不该抛**——这与 `validate_close` 对「缺省 sz = 全仓 `|pos|`」的
   > 既有姿态（`order_builder.py:257`）同源。
3. **[P1-1] 名义价值只做记录，不作拒绝判据。** 本仓库的既有纪律写得很清楚：「平仓**不**应用名义
   上限与反向减仓护栏（平仓即减仓，是动作目的）；仅以实时 `|pos|` 二次校验 `sz≤|pos|`」
   （`okx/trading.py:224-225`）。补挂腿同属减仓腿，沿用同一条。
   > v1 沿用了 `validate_order` 的 `live_max_order_notional_usd`（默认仅 **1000 USDT**，
   > `config.py:51`）。那个常数守的是「单笔**新增**敞口不得过大」；而持仓是多次加仓累积出来的，
   > 完全可以合法地远超它。照 v1 写法，**仓位越大、越裸露、越需要兜底，补挂越是被本地拒死**——
   > 一条只在无关紧要的小仓上生效的安全层。
4. `pos_mode == "net_mode"` ⇒ payload 必须含 `reduceOnly: True`；否则必须含 `posSide`
   ——**二者必居其一**，都没有即抛 `LIVE_BACKSTOP_REJECTED_LOCAL`。这条是 §2.1 第 2 条不变式的
   代码化断言，它守的是「永不建立敞口」。

**H3 · 频率锁（运行时，`RepairRuntime`）**：

| 锁 | 默认 | 语义 |
|---|---|---|
| `max_per_epoch` | 2 | 单个持仓 epoch 内最多补 2 次（`note_open` 观察到新 epoch 即清零）。`0` ⇒ 本层整体关闭（自动与人工都不补） |
| `cooldown_s` | 60 | 两次补挂尝试之间的最小间隔；失败也计入 |
| `max_per_session` | 8 | 单会话硬上限，触顶后 `blocked(attempts_exhausted)` 并保持到会话结束 |
| 单飞 | — | 上一发在途即跳过（与 `BackstopRuntime._task` 同款 `done()` 判活） |

> H3 存在的理由是**故障放大**：如果交易所因某种原因持续拒单（保证金不足、标的进入
> 限制状态、触发价校验规则变化），一个没有频率锁的自愈层会在每个 tick 打一发，
> 5 秒一次、一天 17280 次。频率锁把最坏情况压到「每个持仓周期 2 次、每个会话 8 次」，
> 且每一次失败都带着交易所原始 sCode 呈现给用户。

#### 2.2.4 幂等：`algoClOrdId` 是确定性的

`_post` 在 `TransportError` 上会重试同一 body（`trade_client.py:39-52`），而「超时但其实成功」
是下单路径最经典的一类事故。因此本轮的 `algoClOrdId` **必须由计划确定性生成**，不含时间戳随机段：

```
# [P0-1] 三段式：会话身份 + 持仓周期 + 尝试序号。前两段缺一不可。
algoClOrdId = f"arh{sid6}{epoch4}{attempt:02x}"     # 共 3+6+4+2 = 15 字符，全字母数字
# sid6    = sha256(f"{inst_id}|{session.started_ts}").hexdigest()[:6]
#           —— 会话身份。**确定性**（同一会话内每次算都相同、不含随机段与调用时刻），
#              但跨成员、跨重启互不相同。
# epoch4  = f"{session._position_seq & 0xFFFF:04x}"（同一持仓周期恒定）
# attempt = 本 epoch 内的第几次尝试（0-based，与 H3 的计数同源）
```

> **为什么 v1 的两段式必然撞车。** v1 只用 `(epoch, attempt)`，而 `_position_seq` **每个会话都从
> 0 起**（`session.py:190`），且**每个成员是一个独立 `AiTradeSession`**（`manager.stop_member`
> 按 `(group_id, inst_id)` 找的就是成员各自的会话）。于是一个 3 标的分组里，三名成员的第一次
> 补挂算出来的 ID 逐字节相同；会话重启后也与上一会话相撞。幂等设计的**唯一**目的是让重复请求被
> OKX 以「重复 ID」拒绝而不是变成第二张单——ID 一旦跨成员碰撞，这个机制就从「防重复」翻转成
> 「**第二个成员永远补不上**」，而且失败原因会是一个看起来完全正确的去重码。多标的自第 6 轮
> （`ai-trade-multi-symbol`）起就是本产品的默认形态。
>
> `started_ts` 取 `AiTradeSession.started_ts`（`session.py:194`，会话生命周期内恒定），
> 因此本式仍然**不含任何调用时刻**——`_post` 的重试与人工按钮落在同一个 `(会话, epoch, attempt)`
> 上时依旧恒等。T-31 守「同一三元组恒等」，**T-31b 守「换成员 / 换会话即不同」**。

于是：`_post` 的自动重试、本层的下一次尝试、以及人工按钮，**只要落在同一个
`(会话, epoch, attempt)` 上就恒等**——重复请求由 OKX 以「重复的自定义 ID」拒绝，而不是变成第二张
止损单。
（OKX 对 `algoClOrdId` 的唯一性约束与其返回码需在 §八 第 2 步的真实探测中记录实测值，
**不得硬编猜测**；若实测发现 OKX 不对算法委托做 ID 去重，则按 §七 R-3 降级为
「每次尝试前强制先核实一次，核实到已有覆盖即放弃本次补挂」。）

#### 2.2.5 状态机（用户与模型看到的同一份）

```
        ┌──────────────────────────── idle ◄──────────────────┐
        │  （无仓 / 覆盖达标 / 未核实）                        │
        ▼                                                     │
   需要补挂 ──H1/H2/H3 任一不过──► blocked(reason) ────────────┤
        │                                                     │
        ├─ mode == "plan"  ──► planned ──人工按钮/授权──┐      │
        │                                              │      │
        └─ mode == "arm"   ─────────────────────────────┤      │
                                                       ▼      │
                                                   placing    │
                                                       │      │
                                    ┌──────成功────────┴──失败─┴──► failed(sCode)
                                    ▼
                                 placed  ──下一次核实确认覆盖达标──► verified
                                    │
                                    └──下一次核实仍未覆盖────────► blocked(verify_failed)
```

> **`placed → verified` 这一跳是本轮的骨气所在。** 第 3 轮存在的全部理由是「不许说没核实过的
> 话」，那条纪律必须同样适用于**系统自己刚做的事**：OKX 回了一个 `algoId` 不等于那张单真的挂在
> 那里（可能被风控立刻撤销、可能触发价被判非法而部分接受）。因此补挂成功后立刻
> `backstop.force_next()`，在下一次核实回来之前，文案与卡片一律说
> **「已发出，正在核实」**，不说「已补挂」。

#### 2.2.6 模式与授权（`plan` / `arm`）

| 模式 | 谁触发写请求 | 默认 | 用户在哪里选 |
|---|---|---|---|
| `plan`（仅提醒） | **只有人工按钮 / 人工端点** | ✅ 默认 | 启动弹窗「交易所侧兜底自愈」两段式控件 |
| `arm`（自动补挂） | 盯市 tick 自动 + 人工按钮 | — | 同上（显式切换） |

**[P1-4] 装配前置条件（硬约束）**：`rule_from_settings` 在下列任一情况下返回 `None`（整层不装配、
`repair` 整键缺席、一个写请求都不发）：运维总闸关 / `mode` 非法 / `max_per_epoch == 0` /
**本会话未装配交易周期契约（`horizon_rule is None`）**。

> 最后一条是评审加的，理由是一条硬边界：`HorizonRuntime.refresh` 在 `rule is None` 时直接返回
> `None`（`horizon_runtime.py:151-152`），`last_snapshot` 因此恒为 `None`，`status.horizon` 下发
> `null`——挂在 `horizon.backstop.repair` 上的 wire、运行卡第五行、prompt 子句**同时全部消失**。
> 而 `RepairRuntime` 只受自己的运维总闸约束，`arm` 模式照样会发单。**一条用户完全看不见的自动
> 下单路径不允许存在**，这比「少一个功能」严重得多。少装配一层的代价是可接受的：未装配周期契约
> 的会话本就不在本特性链的目标场景里。T-1b 守它。

拍板理由见 §九 D-1。要点：本轮是本仓库第一条 AI 侧自动写路径，**默认自动执行与默认死码之间，
第三条路是「默认给方案 + 一键执行」**——它既不静默花钱，也不是没有落点的常量。
`plan` 模式下用户看到的不是一句警告，而是**一个可以立刻执行的具体方案**
（止损价、张数、覆盖率），一次点击完成。

#### 2.2.7 Prompt 文案（`hold_horizon_text._exchange_snapshot_line` 的加法子句）

**冻结纪律**：`backstop.repair` 键缺席（总闸关 / `plan_repair` 返回 `idle` 且从未补过 / 回测）
⇒ 本函数**逐字节返回第 3 轮的那五句**，由冻结用例守（放行条件 5）。键在场时按 `state` 追加
**一句**（绝不多于一句，兜底行已经是 prompt 里最长的一段）：

| `state` | 追加子句 |
|---|---|
| `planned` | `系统已算出补挂方案（止损 {px} / {sz} 张），但本会话为「仅提醒」模式——在人工挂出前，请按「没有兜底」规划退出。` |
| `placing` / `placed` | `系统已发出一张补挂止损（{px} / {sz} 张），正在向交易所核实；核实通过前不要按「已有兜底」放松退出计划。` |
| `verified` | `其中 {sz} 张由系统自动补挂并已核实通过（{ago}前）。` |
| `blocked(px_through)` | `⚠ 现价已越过本地止损价 {px}，系统拒绝补挂一张会立刻触发的止损（那等于立刻市价平仓）——退出决策完全在你手上。` |
| `blocked(px_stale)` **[P1-2]** | `系统本可补挂止损，但当前判定价已过期（{age}），无法确认现价是否仍在止损的有利侧，故暂缓——当前仍无持久化兜底。` |
| `blocked(其余)` | `系统本可补挂止损但被暂缓（{原因}），当前仍无持久化兜底。` |
| `failed` | `⚠ 系统尝试补挂止损失败（{sCode 中文标签}），当前仍无持久化兜底。` |

> 每一句都以「当前**有没有**兜底」结尾，不以「系统做了什么」结尾——模型需要的是事实，
> 不是工单状态。

### 2.3 轴 B：回测净额真值

#### 2.3.1 曲线 → 快照（`funding_ledger.BacktestFundingCurve.snapshot_at`）

新增一个**纯查表**方法，返回与实盘同一个 `FundingSnapshot` 类型（依赖核实 #33，无环）：

```python
def snapshot_at(self, ts: int, *, now_wall: float = 0.0) -> FundingSnapshot | None:
    """`ts` 时点可见的最近一次真实结算费率；曲线不可用 ⇒ None。

    `interval_s` 取 `interval_hint`（最近两点之差），`interval_known=True`——历史曲线
    自带每一次真实结算时刻，这是本仓库唯一一个「结算周期真的可知」的场景。
    """
```

三条纪律：

1. **严格无未来函数**：只取 `point.ts <= ts` 的最后一点；一个都没有 ⇒ `None`。
2. **`available` 是前置条件**（`len(points) >= 2`，`funding_ledger.py:416-419`）：单点算不出周期，
   算不出周期就表达不了「几期」，宁可回退。
3. **`interval_hint` 只描述最近两点**：OKX 会动态改周期（调查约束 #7），用最近两点估当下周期
   是本仓库既有的口径（`funding_ledger.py:421-426`），本轮不发明第二种。

#### 2.3.2 两个入口的改法

```python
# backtest.py 新增私有薄封装（唯一真源，两处调用点共用）
def _cost_funding(self, ts: int):
    """回测成本模型的资金费入参：(snapshot | None, funding_enabled)。

    **曲线不可用 ⇒ 逐字回退 `(None, False)`**——`funding_enabled=True` + `funding=None`
    在 `cost_model` 里的语义是「数据缺失 ⇒ degraded ⇒ 门禁阈值收紧」（cost_model.py:148-159 /
    edge_gate），那会让「拉不到历史费率」这件观测层的小事，静默改变回测的门禁判定。
    回退到 `(None, False)` 才是「有意不建模」，与今天逐字节相同。
    """
    if not self.settings.ai_trade_funding_cost_backtest_enabled:
        return None, False
    snap = self._curve.snapshot_at(ts)
    return (snap, True) if snap is not None else (None, False)
```

两处调用点各改**一行入参**（判定式一个字符不动）：

| 位置 | 改前 | 改后 |
|---|---|---|
| `backtest.py:700-701` | `funding=None, funding_enabled=False` | `funding=f, funding_enabled=fe`（`f, fe = self._cost_funding(ts)`） |
| `backtest.py:783` | `funding_enabled=False` | `funding=f, funding_enabled=fe`（同上） |

> `_horizon_facts(i, ts, ref_px)` 已有 `ts` 形参；`_run_decision` 里 `ts` 在第 747 行即已取到，
> 两处都无需新增形参传递链。

**方向必须与实盘一致**：`_horizon_facts` 已按**持仓**方向取 `side=pos.side`
（`backtest.py:699`），`evaluate_edge_gate` 按**决策**方向取（`query_for_decision`）——这两处
方向口径的差别是第 2 轮 [P1-2]/[P1-3] 已经拍过板的，本轮**不动**。

#### 2.3.3 影响面与自认

| 被影响的判定 | 前置条件 | 方向 | 量级（`trend_hold`，1D 执行，`expected_hold_bars≈3`） |
|---|---|---|---|
| G3 换手迟滞阈 `= k × cost_bps` | **默认即生效**：四个预设的 `hysteresis_k ∈ [1.2, 2.0]` 全为正（`hold_horizon.py:105-138`） | **变严**（获利了结门槛升高 ⇒ 持仓更久） | `cost_bps` +约 9 bps（0.01%×9 期）⇒ 阈值 ×`k` |
| 最小 edge 门禁 | **[P1-9] 默认恒不生效**：`evaluate_edge_gate` 第一句即 `k_min <= 0 ⇒ return None`（`edge_gate.py:210-212`），而 `ai_trade_edge_gate_k_min` 默认 **0.0**（`config.py:401`） | 只有在运维显式把 `k_min` 调正之后才**变严** | 同上 |
| 回测**盈亏** | — | **不变**（第 2 轮已按真实曲线计提，且资金费**不进权益曲线与 metrics**，`backtest.py:1091-1093`） | 0 |

> **[P1-9] 为什么必须把这一列写出来。** v1 把两条判定并列陈述，读起来像是「改一处、两处同时变严」。
> 实际上默认配置下只有 G3 这一条路是活的。这不影响本轴的价值（G3 恰恰就是调查约束 #5 的执行体），
> 但它决定了两件事：F-10/11 **必须显式把 `k_min` 设成正数**再断言，否则那两条是恒过的空用例；
> §6.4 的 runbook L4 也不能拿 edge 门禁的计数当观察指标。

**必须自认的两件事**（写进回测 notes，§2.4.4）：

1. 本次回测的成本估算**是否**含资金费，以及用的是哪一种口径（`curve` / `off` / `unavailable`）；
2. 含资金费时给出**实际用到的结算周期**（`interval_hint`）与**跨的期数**
   （`cost_model.funding_periods`，依赖核实 #30）——「按 8h 假设」这句话本轮之后**不该再出现**
   在任何一份回测结论里。

### 2.4 轴 C：呈现与知情

#### 2.4.1 启动弹窗（`AiTradeHorizonFields.tsx`）

在既有「换手纪律」两行契约摘要之后，新增一个**两段式分段控件**与一行说明：

```
交易所侧兜底自愈        [ 仅提醒 ]  [ 自动补挂 ]
  开启自动补挂后，当系统向交易所核实到「止损单不存在 / 覆盖不足」时，会自动补挂一张
  只减不增的止损（只补差额张数，触发价取本次开仓时的止损价）。它永不平仓、永不加仓，
  且在现价已越过止损价时拒绝补挂。仅提醒模式下系统只给出方案，由你一键执行。
```

五条 UI 纪律：

- **全部文案与默认值由 `GET /ai-trade/horizon-profiles` 下发**（依赖核实 #24 的既有做法），
  前端一个字、一个数都不硬编——第 3 轮 R-3/G-10 的教训。
  **[P0-3] 前置条件**：`AiTradeHorizonProfilesOut` 是 `response_model`（`api/ai_auto.py:298` +
  `schemas.py:739-757`），**`profiles_wire` 多下发的键会被 FastAPI 静默剔除**——新块必须同时在
  schema 上开字段，否则「整块缺席 ⇒ 不渲染」这条规则会把控件永久关掉，而 §6 的用例表里没有
  任何一条会红（U-20 用的是手造 wire）。S-19 打真端点守它。
- **[P1-10] 控件不受跨日条件门控。** 同屏最相近的既有元素 `BackstopNotice` 只在
  `targetHoldMaxSeconds >= 86400` 时渲染（`AiTradeHorizonFields.tsx:378-379`），顺手挂进去是最省事
  的写法——但**随单 OCO 丢失与持仓周期长短无关**，`hourly_swing` 的仓一样会裸露。四个预设 +
  `custom` 一律渲染本控件。U-20b 用 `hourly_swing` 断言控件在场。
- **[P1-8] 状态宿主是 `AiTradeLaunchModal.tsx`，不是 `AiTradeHorizonFields.tsx`。** 后者是纯受控
  组件（`AiTradeHorizonFieldsProps`，`:40-58`），本轮给它加两个 prop
  （`backstopRepairMode` / `onBackstopRepairModeChange`）；state 与 `tradingHorizonPayload`
  的组装（`AiTradeLaunchModal.tsx:444-450`）都在弹窗里。
  **新 state 必须塞进既有的 `[open]` effect**（`:282-337`）——该文件里有一条成文纪律：另写 effect
  就是 `ai-trade-modal-revert-recurrence` 的复发根因（输入约 1 秒后被冲回默认），且**每加一个新
  字段就再破一次**。U-21 是这条的守门用例。
- **两段控件的默认选中项来自 `backstopRepair.defaultMode`**，不写死 `"plan"`。
- 「自动补挂」被选中时，说明文字下方追加一行 `--accent` 色的确认语：
  `本会话将允许系统在你不在场时向交易所发出止损委托。` ——这是本轮唯一一处「系统会自己花钱」
  的授权点，必须让用户在点「启动」之前读到。

#### 2.4.2 运行卡（`TurnoverBackstopCard.tsx` + `aiTradeBackstopRepair.ts`）

`buildRows` 追加**第五行** `repair`（`describeRepair(wire.repair)`；键缺席 ⇒ `null` ⇒ 不渲染）：

| `state` | value | tone | 右侧按钮 |
|---|---|---|---|
| `idle` | —（**整行不渲染**） | — | — |
| `planned` | `方案就绪` | `accent` | **立即补挂** |
| `blocked` | `已暂缓` | `muted`（`px_through` / `verify_failed` 为 `short`） | 仅 `px_through` 外的可重试原因显示 **重试** |
| `placing` / `placed` | `已发出 · 核实中` | `accent` | — |
| `verified` | `已补挂 · 已核实` | `long` | — |
| `failed` | `补挂失败` | `short` | **重试** |

- **色彩纪律沿用第 3 轮**（`aiTradeTurnover.ts:1-9`）：灰=还不知道、红=确定的坏消息，
  **不用黄色**。`planned` 用 `accent`（可行动），不用红——「有方案待执行」不是坏消息。
- **[P1-7] 排序：`turnoverSeverity` 与 `describeTurnoverChip` 必须同一处改。**
  追加两格——`failed` 与 `blocked(px_through)` 并入最高优先级（与 `mismatch` 同级），
  `planned` 排在「覆盖不足」之后、「冷却中」之前。
  > `turnoverSeverity` 返回的是 **0…4 的整数、越小越严重**（`aiTradeTurnover.ts:186-201`），而
  > `describeTurnoverChip` 用 `severity === 0/1/2/3` 的**硬编码分支**读它（`:204-230`）。中间插两格
  > 而不同步改 chip，冷却态就会被渲染成预算态、预算态返回 `null`——面板上最显眼的那一格开始说错话，
  > 且没有任何类型错误。本轮同时给 chip 补两句自愈文案（`failed` ⇒「交易所兜底补挂失败」/
  > `planned` ⇒「交易所兜底待补挂」），否则「最紧要的一格」会漏掉本轮新造的最紧要状态。
  > U-15b 守它。**新分支只读 `state`，不复制任何阈值**（该函数里已硬编码了一个 `0.999`，
  > 是既有债，不要二次固化，见 [P2-7]）。
- **按钮的三态**：可点 / 请求中（禁用 + 「补挂中…」）/ 刚完成（禁用 2s 并显示结果）。
  失败时把**交易所原始 sCode 的中文标签**显示在 `detail` 里，不显示裸码。
  **[P2-5] 状态归属**：卡片仍**无状态**，请求态由父组件按 `instId` 键传入
  （`repairPending` / `repairResult` 两个可选 prop）——§3.4 的「卡片保持无状态」与本条不矛盾。

#### 2.4.3 事件与轨迹

- **WS `error` 事件**（复用既有通道，`exchange_backstop.py:396-401` 的同款写法）只在两种情况下推：
  `failed`（补挂失败，含 sCode）与 `blocked(px_through)` 由假翻真的那一刻。
- **[P1-3] 其余状态变化：`state` 或 `blockedReason` 一变就补推一次 `status` 快照。**

  > v1 写的是「随既有 status 快照下发」——但全量 `_status_payload()` **只在换向 / 唤醒跳过计数 /
  > 决策轮 / 停会话时推**（`session.py:367/415/516/527/1353/1556`），**没有挂在 5s 盯市 tick 上**。
  > 而自愈层的目标用户恰恰是 `trend_hold`：建议唤醒周期 **0**、决策轮以小时计。照 v1 写法，
  > `planned → placing → placed → verified` 的整条流转可以几个小时不上屏，用户点完「立即补挂」
  > 之后界面纹丝不动——**后端算了 ≠ 用户看得见**，本仓库为这条已经栽过五次（放行条件 8 就是为它
  > 而立的）。
  >
  > 实现范式是现成的：`session.py:524-527`（唤醒跳过计数成功即补推一次 status）。**变化才推**
  > 天然节流，不需要新造节流机制；一个持仓周期内最多 2 次补挂 × 每次至多 4 次状态跃迁 ⇒ 上限
  > 8 条，远低于既有的决策轮频率。`schedule_repair` 因此多收一个 `on_change` 回调（会话侧传一个
  > 2 行的 `_push_status` 薄封装），`session.py` 预算相应由 14 提到 **18**（§3.2 / 放行条件 4 已同步）。
  > S-21 守它：`plan` 模式下人工触发一次补挂，断言**恰好**多推了 status 且 `state` 已流转。

  其余一律不制造事件流噪音。
- **不新增决策轨迹段**：自愈不参与决策（§2.1 边界 1），把它塞进决策轨迹会让用户误以为它
  影响了本轮决策。它的历史落在审计表（依赖核实 #18）与运行卡上。

#### 2.4.4 回测面板

**[P1-6] 宿主是 `backtest._funding_notes()`（`backtest.py:1084`），不是
`hold_horizon_text.format_backtest_notes`。** 两条理由：

1. `format_backtest_notes` 的第一句就是 `rule is None or not rule.enabled ⇒ return []`
   （`hold_horizon_text.py:421-422`）。挂在那里，**未装配周期契约的对照臂完全没有披露**——而
   对照臂恰恰是验收时最需要对拍的那条曲线。而成本估算的口径变化与周期契约装不装配无关：
   `_horizon_facts` 与 `evaluate_edge_gate` 在两种臂上都会跑。
2. `_funding_notes()` 本来就是资金费口径的唯一披露出口（第 2 轮建的），把第二句资金费口径的话
   放到另一个函数里，等于给同一件事开第二个出口。

追加 / 改写共**三处**：

| # | 分支 | 文案 |
|---|---|---|
| 1 | 含资金费 | `本次回测的往返成本估算已含资金费（真实历史曲线，结算周期约 {interval_hint}，按目标持仓跨 {periods} 期，覆盖 {n}/{m} 个决策轮）；G3 换手迟滞{（k_min>0 时追加）与最小 edge 门禁}据此判定。` |
| 2 | 回退 | `本次回测的往返成本估算**不含**资金费（{原因：总闸关 / 历史曲线不可得}），G3{与 edge 门禁}的判定因此比实盘宽松；持仓越长偏差越大，不得据此为长周期模式背书。` |
| 3 | **改写既有句** | `backtest.py:1094-1095` 现有的「已知欠账：回测里给换手迟滞与 edge 门禁用的**成本估算**仍不含资金费（实盘含），两者的不对称留待下一轮…」**必须删除并由上面两句取代** |

> **第 3 处是评审补的，它是一条会变成假话的既有文案。** 轴 B 正是来还那笔账的；不改它，面板会
> 在同一份 notes 里同时说「已含资金费」和「仍不含资金费」。本仓库已经为「没有展示落点的免责常量
> 即死码」栽过一次，这是它的镜像：**一条在事实变化后没被撤下的免责，比没有免责更坏**。
>
> 括号里的两处「{与 edge 门禁}」是条件文本：只在 `settings.ai_trade_edge_gate_k_min > 0` 时出现
> （[P1-9]），否则那句话本身就是一句没核实过的承诺——`_build_notes` 里已有
> `if self.settings.ai_trade_edge_gate_k_min > 0` 的同款先例（`backtest.py:1073`）。
> 「覆盖 {n}/{m} 个决策轮」见 [P2-1]：曲线起点之前的若干根 `snapshot_at` 恒 `None`，
> 一个全局布尔说不清这件事。

### 2.5 明确不碰的东西（边界）

- `exchange_backstop.py` **一行不改**（只读它的产物）。
- 任何既有门禁的**判定式**（轴 B 改的是入参口径，`estimate_round_trip_cost` 与
  `evaluate_edge_gate` 的函数体一个字符不动）。
- `live_auto`（非 AI）链路、`engine/simulator`、`paper` 链路。
- `profit_guard` / `base_rules` / `turnover_discipline` 三个纯核心。
- 撤单、改价、补止盈腿、自动平仓（全部列入 §十 非目标）。
- 数据库 schema（零迁移，复用 `live_order_audit`）。

---

## 三、文件 / 模块变更计划

### 3.1 后端 · 新建

| 文件 | 意图 | 预算 |
|---|---|---|
| `services/quant/app/ai_auto/backstop_repair.py` | 自愈纯核心（`RepairRule` / `RepairPlan` / `plan_repair` / `repair_wire`）+ `RepairRuntime`（单飞、计数、状态机）+ `schedule_repair` 一行缝 | ≤ 330 行 |
| `services/quant/tests/test_ai_trade_backstop_repair.py` | 纯核心用例：三道锁、状态机、wire 形状 | 约 **39** 例（评审 +5） |
| `services/quant/tests/test_ai_trade_backstop_repair_io.py` | `place_algo_order` / `build_algo_stop_payload` / `validate_algo_stop` / `place_backstop_stop` 时序与审计 | 约 **23** 例 |
| `services/quant/tests/test_ai_trade_backstop_repair_session.py` | 会话接线、端点（含**真端点**契约链路）、prompt 分支冻结、WS 事件、status 补推 | 约 **22** 例 |
| `services/quant/tests/test_ai_trade_backtest_funding_cost.py` | 轴 B：`snapshot_at` 无未来函数、回退等价、两处入参、notes（含旧欠账句的反向冻结） | 约 **18** 例 |

### 3.2 后端 · 修改

| 文件 | 改法 | 硬预算 |
|---|---|---|
| `okx/trade_client.py` | 新增 `place_algo_order(payload)`（`POST /api/v5/trade/order-algo`，业务码归一与 `place_order` 逐字同构） | +26 行 |
| `okx/order_builder.py` | 新增 `AlgoStopInput` + `build_algo_stop_payload` + `validate_algo_stop`（H2 越界锁）；**复用** `_close_side` / `snap_size` / `snap_price` / `fmt_step` | +62 行 |
| `okx/trading.py` | 新增 `place_backstop_stop(session, creds, account_id, stop, *, settings)`：`_load_meta → ensure_instrument_supported → posMode → get_positions → validate → place_algo_order → 审计` | +48 行 |
| `ai_auto/funding_ledger.py` | `BacktestFundingCurve.snapshot_at(ts)` | +22 行 |
| `ai_auto/backtest.py` | `_cost_funding(ts)` + 两处入参 + **[P1-6]** `_funding_notes()` 的两句新口径 + **改写** `:1094-1095` 的既有欠账句 + [P2-1] 覆盖计数 | **+30 行**（由 20 上调，改判见 [P1-6]/[P2-1]） |
| `ai_auto/horizon_runtime.py` | 构造多收一个 `repair=`（鸭子类型，只要求 `.wire()`）；`_snapshot` 里合入 `backstop["repair"]` 子键 | **+12 行** |
| `ai_auto/hold_horizon.py` | `profiles_wire` 追加 `backstopRepair` 块；**[P0-3]** `HorizonRule.backstop_repair_mode` 新字段 + `resolve_horizon_rule` 回填（唯一解析点）+ `rule_wire` 镜像 | **+26 行**（由 14 上调） |
| `ai_auto/hold_horizon_text.py` | `_exchange_snapshot_line` 追加 `_repair_clause(...)` | **+30 行**（资金费口径句已按 [P1-6] 移出本文件） |
| `ai_auto/session.py` | 装配 `RepairRuntime`（**须在 `HorizonRuntime` 之前**）+ 盯市一行 `schedule_repair` + `note_epoch` 一行 + `close()` + 人工入口 `repair_backstop()` + **[P1-3]** 2 行的 `_push_status` 薄封装 | **+18 行（硬顶，由 14 上调）** |
| `ai_auto/manager.py` | `repair_member(group_id, inst_id)` 转发（镜像 `stop_member`，`manager.py:540-551`） | +12 行 |
| `api/ai_auto.py` | `POST /ai-trade/{group_id}/member/{inst_id}/backstop/repair`（`_resolve_horizon` **不改**——它只是两行透传，回填在 `hold_horizon`，见 [P0-3]） | +22 行 |
| `schemas.py` | `AiTradeHorizonIn.backstopRepair`（`Literal["plan","arm"] \| None`，422 守值域）+ `AiTradeBackstopRepairOut` + **[P0-3]** `AiTradeHorizonProfilesOut.backstopRepair` 字段 | **+26 行**（由 18 上调） |
| `config.py` | 7 个新配置项（§5.1） | +22 行 |
| `.env.example` | 同步一段 | +14 行 |

### 3.3 前端 · 新建

| 文件 | 意图 |
|---|---|
| `apps/web/lib/aiTradeBackstopRepair.ts` | 自愈状态机的中文化与色调（**零计算**，镜像 `aiTradeTurnover.ts` 的既有姿态） |
| `apps/web/lib/aiTradeBackstopRepair.test.ts` | 七个 `state` × 全部 `blockedReason` 的标签与色调断言（约 20 例） |

### 3.4 前端 · 修改

| 文件 | 改法 |
|---|---|
| `apps/web/lib/aiTradeTypes.ts` | `AiTradeBackstopRepairWire` 接口 + `AiTradeBackstopWire.repair?`；`AiTradeHorizonProfilesOut.backstopRepair`；启动请求的 `backstopRepair` 字段 |
| `apps/web/lib/aiTradeApi.ts` | `repairBackstop(groupId, instId)`（**[P2-8]** `instId` 走 `encodeURIComponent`，镜像既有 `stopMember`） |
| `apps/web/lib/aiTradeTurnover.ts` | **[P1-7]** `turnoverSeverity` 追加两格（`failed` / `px_through` 与 `mismatch` 同级；`planned` 居中）**与 `describeTurnoverChip` 的分支同一处改**（后者用 `severity === 0/1/2/3` 硬编码读前者），并补两句自愈 chip 文案 |
| `apps/web/components/Agent/AiTradeHorizonFields.tsx` | 两段式自愈控件 + 说明 + 授权确认行（全部文案来自 profiles wire）；新增两个 prop `backstopRepairMode` / `onBackstopRepairModeChange`。**[P1-10] 不挂进 `BackstopNotice` 的跨日条件分支**（`:378-379`），四预设 + custom 一律渲染 |
| **`apps/web/components/Agent/AiTradeLaunchModal.tsx`** | **[P1-8] v1 漏列。** 新 state 的宿主 + 塞进既有 `[open]` effect（`:282-337`，另写 effect = `modal-revert` 复发）+ 进 `tradingHorizonPayload`（`:444-450`） |
| **`apps/web/components/Agent/AiTradeLaunchModal.test.tsx`** | **[P1-8]** U-21 复发守门：改动新控件后等待一个渲染周期，值不得回退 |
| `apps/web/components/Agent/aiTradeStats/TurnoverBackstopCard.tsx` | `buildRows` 第五行 + 行内按钮（新增可选 `onRepair` / `repairPending` / `repairResult` prop，**卡片本身保持无状态**，[P2-5]） |
| `apps/web/components/Agent/aiTradeStats/AiTradeStatsLive.tsx` | 把 `onRepair` 接到 `aiTradeApi.repairBackstop(group.groupId, m.instId)` + 按 `instId` 键的请求态（`group.groupId` 与 `m.instId` 均已在手，`:149-155`） |
| `apps/web/components/Agent/aiTradeStats/TurnoverBackstopCard.test.tsx` | 追加第五行与按钮的用例 |
| `apps/web/lib/aiTradeTurnover.test.ts`（既有） | **[P1-7]** severity 重排后既有断言必须同步 |
| `apps/web/lib/aiTradeHorizon.ts` | profiles wire 新块的只读取用（不复制任何数值/文案） |

---

## 四、接口设计

### 4.1 `POST /api/ai-trade/{group_id}/member/{inst_id}/backstop/repair`（新增）

人工授权的**单次**补挂。与自动路径共用同一个 `plan_repair` 与同一条 `place_backstop_stop`，
**只绕过 `mode`，绝不绕过 H1/H2/H3**。

```jsonc
// 200 —— 已发出（注意：不是「已补挂」，核实前一律用 placed）
{"instId": "BTC-USDT-SWAP", "state": "placed",
 "algoId": "6xxxx", "algoClOrdId": "aragonh0003a00",
 "stopLossPx": 61234.5, "sz": 0.3, "coveredBefore": 0.0, "positionSz": 0.3}

// 409 —— 计划不可执行（H1/H2/H3 任一不过）；`blockedReason` 与卡片、prompt 三处同一份值域
{"error": {"code": "AI_TRADE_BACKSTOP_NOT_REPAIRABLE",
           "message": "现价已越过本地止损价，补挂会立刻触发（等于立刻市价平仓）",
           "blockedReason": "px_through"}}

// 404 —— 分组 / 成员不存在（镜像 `stop_member` 的既有姿态）
// 502 —— 交易所拒单：{"code": "<原始 sCode>", "message": "<中文标签>"}
```

守门：**不守 `ai_trade_enabled` 总闸**（与 `/ai-trade/{gid}/stop` 同理——补挂是收敛风险的动作，
任何时候都该能执行）；守 `ai_trade_backstop_repair_enabled` 运维总闸（关 ⇒ 404，因为整层未装配）。

异常映射（端点层唯一的翻译点）：`AiTradeRepairBlocked` → 409 + `blockedReason`；
`OKXPrivateError` → 502 + 原始 sCode 与中文标签；成员/分组缺失 → 404。
**绝不把异常文本原样外泄**：沿用 `place_live_order` 的既有纪律，先过
`okx/accounts.py:59` 的 `_redact(text, secret_key, passphrase)` 再出网。

### 4.2 `GET /api/ai-trade/horizon-profiles`（既有端点 · 加法字段）

```jsonc
{
  "profiles": [ /* 不变 */ ],
  "backstopCheckEnabled": true,        // 既有
  "backstopPollSeconds": 120,          // 既有
  "backstopRepair": {                  // ★ 新增（整块缺席 ⇒ 前端不渲染控件）
    "defaultMode": "plan",
    "modes": [
      {"id": "plan", "label": "仅提醒",
       "detail": "系统只给出补挂方案，由你一键执行"},
      {"id": "arm",  "label": "自动补挂",
       "detail": "核实到止损缺失时由系统自动补挂，本会话将允许系统在你不在场时发出止损委托"}
    ],
    "note": "补挂只补止损腿、只补差额张数、永不平仓、永不加仓；现价已越过止损价时拒绝补挂。",
    "maxPerEpoch": 2, "maxPerSession": 8
  }
}
```

> **[P0-3] 本块必须同时在 `AiTradeHorizonProfilesOut` 上开字段**（`schemas.py:739-757`）。该端点声明了
> `response_model`（`api/ai_auto.py:298`），FastAPI 会按模型字段序列化——`profiles_wire` 里多下发的键
> **静默消失**，而 §4.2 又规定「整块缺席 ⇒ 前端不渲染控件」，两条一叠就是「控件永远不出现，且没有
> 任何用例会红」。落法：新增 `AiTradeBackstopRepairModeOut`（`id` / `label` / `detail`）与
> `AiTradeBackstopRepairOut`（`defaultMode` / `modes` / `note` / `maxPerEpoch` / `maxPerSession`），
> 后者作为 `backstopRepair: AiTradeBackstopRepairOut | None = None` 挂上去。**S-19 打真端点断言键在
> 回执里**——手造 wire 的前端用例（U-20）守不住这一格。

### 4.3 `POST /api/ai-trade/start`（既有 · `tradingHorizon` 加法字段）

```jsonc
"tradingHorizon": { /* 既有字段不变 */ , "backstopRepair": "plan" }   // "plan" | "arm" | null
```

`null` / 缺省 ⇒ 服务端按 `settings.ai_trade_backstop_repair_default_mode` 回填
（**只回填、不复夹**，沿用既有纪律）。运维总闸关 ⇒ 字段被忽略且回执里整键缺席。

> **[P0-3] 回填点在 `hold_horizon.resolve_horizon_rule`（`hold_horizon.py:299-344`），不在
> `api/ai_auto.py`。** v1 写「`_resolve_horizon` 回填」是找错了文件：那个函数只是
> `resolve_horizon_rule(raw.model_dump(), settings, bar=bar)` 的两行透传（`api/ai_auto.py:150-151`），
> 且**实盘与回测共用它**。完整链路必须一次补齐三处，缺一即断：
> ① `AiTradeHorizonIn.backstopRepair`（`Literal["plan","arm"] | None`，值域在 schema 层 422）；
> ② `HorizonRule.backstop_repair_mode` 新字段 + `resolve_horizon_rule` 里的回填（唯一解析点）；
> ③ `rule_wire` 镜像该字段（`hold_horizon.py:380-404`）——否则 `config_json.tradingHorizon` 与
> `group_status` 的回执里看不到它，用户无法核对「我选的和落地的是不是同一个」，那正是第 1 轮
> [P1-9] 立下的规矩。会话侧则从 `horizon_rule.backstop_repair_mode` 取值构造 `RepairRule`。

### 4.4 `GET /api/ai-trade/status` 与 WebSocket（既有 · 加法键）

`horizon.backstop.repair` 为**嵌套加法键**（沿用第 3 轮 D-10：嵌在既有整对象透传的
`backstop` 里，不必动 `store.ts` 的逐键手抄 reducer）。形状见 §5.4。

### 4.5 内部函数签名（下游按此实现，不再二次决策）

> **[P1-5] 依赖纪律按「两段分层」表述（v1 的一句话写法自相矛盾）。**
> v1 写「只 import `exchange_backstop` 的三个常量与 `exchange_oco_px`」，但 `RepairRuntime` 必须调
> `okx.order_builder` / `okx.trading.place_backstop_stop` / `db.session.get_sessionmaker` 才能下单
> 落审计——照字面执行只有两条路：要么静默破例，要么发明一套注入。而先例本就摆在那里：
> `exchange_backstop.py:26-28` 自己就 import 了 `..okx.trade_client` 与 `..okx.trading`。
> 正确的表述是分层而非白名单：
>
> | 层 | 允许 import | 禁止 |
> |---|---|---|
> | **纯核心** `plan_repair` / `RepairRule` / `RepairPlan` / 常量 | 只有标准库 | 一切业务模块（含 `okx.*`） |
> | **运行时** `RepairRuntime` / `schedule_repair` | `okx.order_builder`、`okx.trading`、`db.session`、`exchange_backstop` 的常量、`profit_guard_runtime.exchange_oco_px` | **`hold_horizon` / `horizon_runtime`**（后者要读本模块的 wire，反向即成环） |
>
> 另一处矛盾同样由此消除：v1 的 `schedule_repair` docstring 说「运行时不碰 executor」，但
> `repair_now(backstop_wire, now_wall)` 只收 wire，`mark_px` / `local_sl_px` / `order_in_flight` /
> `px_age_s` 无从取得。v2 把断面收敛成一个值对象 `RepairFacts` + **唯一读取点**
> `facts_from_executor(...)`，自动与人工两条通道共用同一份口径（这正是本仓库「同一口径两处各存
> 一份」出过三次事故之后立下的规矩）。

```python
# ai_auto/backstop_repair.py —— 依赖方向见上表（纯核心零业务 import；运行时可用 okx.* 与 db.session，
# 但**绝不** import `hold_horizon` / `horizon_runtime`）。

REPAIR_MODES = ("plan", "arm")
STATE_IDLE, STATE_PLANNED, STATE_BLOCKED = "idle", "planned", "blocked"
STATE_PLACING, STATE_PLACED, STATE_VERIFIED, STATE_FAILED = (
    "placing", "placed", "verified", "failed")
# blockedReason 值域（前端标签逐条镜像，缺一即测试红）
BLOCK_COVERAGE_UNKNOWN = "coverage_unknown"
BLOCK_NO_LOCAL_PX      = "no_local_px"
BLOCK_NO_MARK_PX       = "no_mark_px"
BLOCK_PX_STALE         = "px_stale"        # [P1-2] 判定价过期（≥ max_px_age_s）
BLOCK_PX_THROUGH       = "px_through"
BLOCK_PX_TOO_CLOSE     = "px_too_close"
BLOCK_SIZE_BELOW_MIN   = "size_below_min"
BLOCK_ORDER_IN_FLIGHT  = "order_in_flight"
BLOCK_COOLING          = "cooling"
BLOCK_ATTEMPTS         = "attempts_exhausted"
BLOCK_VERIFY_FAILED    = "verify_failed"

@dataclass(frozen=True)
class RepairRule:
    mode: str                    # "plan" | "arm"
    min_gap_bps: float
    max_per_epoch: int           # 0 ⇒ 本层整体关闭
    cooldown_s: float
    max_per_session: int
    min_coverage: float          # 复用 settings.ai_trade_backstop_min_coverage（不新开旋钮）
    max_px_age_s: float          # [P1-2] 复用 settings.ai_trade_profit_guard_max_px_age_s
                                 #        （与 HorizonRule.max_px_age_s 同一个数，第三个复用方）


@dataclass(frozen=True)
class RepairFacts:
    """[P1-5] 决策瞬刻的断面（**唯一读取点** `facts_from_executor`，自动与人工共用）。"""

    side: str                    # executor.projected（投影立场）
    mark_px: float | None
    px_age_s: float              # 由 session._last_px_wall 派生（session.py:1212 同款）
    local_sl_px: float | None    # exchange_oco_px(executor, side)[1]，只读转发不重算
    order_in_flight: bool        # executor.resting is not None

@dataclass(frozen=True)
class RepairPlan:
    side: str                    # "long" | "short"
    stop_px: float | None
    sz: float | None             # 差额张数（未取整；取整在 H2 里按 lot_sz 做）
    covered_sz: float | None
    position_sz: float | None
    state: str
    blocked_reason: str | None

def rule_from_settings(settings, mode: str | None, *,
                       horizon_armed: bool) -> RepairRule | None:
    """总闸关 / `mode` 非法 / `max_per_epoch == 0` / **`horizon_armed is False`**
    ⇒ `None`（整层不装配 ⇒ wire 整键缺席 ⇒ 一个写请求都不发）。

    [P1-4] 最后一条是硬安全约束：未装配周期契约时 `HorizonRuntime.refresh` 恒返回 `None`
    （`horizon_runtime.py:151-152`），本层的 wire / 卡片 / prompt 会同时全部消失——**一条用户
    完全看不见的自动下单路径不允许存在**。
    """

def plan_repair(backstop_wire: dict | None, *, rule: RepairRule | None, facts: RepairFacts,
                attempts_epoch: int, attempts_session: int,
                last_attempt_wall: float | None, now_wall: float) -> RepairPlan | None:
    """H1 判定锁（纯函数、无 IO、绝不抛）。不适用 ⇒ `None`（≠ blocked）。

    判据顺序**即**降级码优先级，且必须与 §2.2.1 / §2.2.2 / §2.2.3 逐行同序：
    coverage_unknown → no_local_px → no_mark_px → **px_stale** → px_through → px_too_close →
    size_below_min → order_in_flight → attempts_exhausted → cooling。顺序固定的理由与
    `hold_horizon` 一致：用户看到的那一个原因，必须是最具体、最能指导下一步动作的那一个。
    （`px_stale` 排在 `px_through` **之前**是刻意的：价过期时「有没有越过」这个结论本身不成立，
    先报「不知道现价」比先报一个用旧价算出来的越过结论更诚实。）

    [P0-4] 本函数算出的 `sz` 只用 `backstop_wire["positionSz"]`（本会话自管仓），是**预览值**；
    真正下单的张数由 `place_backstop_stop` 在拿到交易所实时 `abs_pos` 后按 §2.2.2 重算。
    """

def facts_from_executor(executor, *, px_age_s: float) -> RepairFacts:
    """[P1-5] 断面的**唯一**读取点（`schedule_repair` 与人工端点共用，绝不各读一份）。"""

class AiTradeRepairBlocked(Exception):
    """人工通道被 H1/H2/H3 拦下（`reason` 取自上面的值域，API 层据此回 409）。"""
    def __init__(self, reason: str, message: str) -> None: ...

class RepairRuntime:
    def __init__(self, settings, rule: RepairRule | None, executor, inst_id: str, *,
                 session_key: str) -> None:
        """`session_key` = `f"{inst_id}|{session.started_ts}"`，`algoClOrdId` 的会话身份段
        由它派生（[P0-1]）。"""
    def note_epoch(self, seq: int) -> None:
        """新持仓 epoch ⇒ 本 epoch 计数清零（由 session 在 `note_open` 同一处调用）。"""
    def schedule(self, backstop_wire: dict | None, *, facts: RepairFacts, now_wall: float,
                 push=None, on_change=None) -> None:
        """同步、单飞、绝不阻塞盯市 tick；`mode == "plan"` ⇒ 只落状态、不发请求。

        [P1-3] `on_change`：`state` 或 `blockedReason` 变化时调用一次（会话侧传补推 status 的
        薄封装）。**变化才调**，天然节流。
        """
    async def repair_now(self, backstop_wire: dict | None, *, facts: RepairFacts,
                         now_wall: float) -> dict:
        """人工通道：等待结果并返回 §4.1 的回执；被 H1/H2/H3 拦下 ⇒ 抛 `AiTradeRepairBlocked`。"""
    def wire(self) -> dict | None:
        """§5.4 的 `repair` 子对象；`rule is None` ⇒ `None` ⇒ 整键缺席。"""
    def close(self) -> None:
        """会话停止：在途任务回来后不再写状态、不再推事件（镜像 `BackstopRuntime.close`）。"""

def schedule_repair(repair: RepairRuntime | None, backstop, executor, *,
                    px_age_s: float, now_wall: float, push=None, on_change=None) -> None:
    """会话侧只留这一行；断面经 `facts_from_executor(executor, px_age_s=…)` 读取（唯一读取点）。

    [P1-2] `px_age_s` 必须由会话传入（真源是 `session._last_px_wall`，`session.py:1212` 同款
    算式）——`executor` 上没有这个数，本层也**绝不**另找一个时钟。
    """
```

```python
# okx/order_builder.py
@dataclass(frozen=True)
class AlgoStopInput:
    inst_id: str
    pos_side: str        # long | short | net
    pos: float           # 带符号张数（net 判向用）
    td_mode: str
    sz: float            # 待补差额张数（未取整）
    stop_px: float
    algo_cl_ord_id: str

def build_algo_stop_payload(stop: AlgoStopInput, meta: InstrumentMeta,
                            pos_mode: str) -> dict:
    """补挂止损的 `/trade/order-algo` 请求体。

    `ordType="conditional"` + `slTriggerPx` + `slOrdPx="-1"` + `slTriggerPxType="last"`
    —— 与随单 OCO 的 `build_attach_algo_orders`（:115-127）**逐字一致**，不发明第二套写法。
    锁侧：`net_mode ⇒ reduceOnly=True 且不带 posSide`；对冲 ⇒ 带 `posSide` 且不带 `reduceOnly`
    （与 `build_close_order_payload`:238-241 同款）。
    """

def validate_algo_stop(stop: AlgoStopInput, meta: InstrumentMeta, *,
                       abs_pos: float, self_position_sz: float, covered_sz: float) -> dict:
    """H2 越界锁；命中即抛，**绝不发往 OKX**。返回 `{"sz": 收敛并取整后的张数}`。

    [P0-4] 先按 §2.2.2 向下收敛：`sz = snap_size(max(0, min(abs_pos, self_position_sz)
    − covered_sz), lot_sz)`，**能收敛的越界不抛**（本地读数偏大是正常的时序差，不是故障）。
    [P1-1] **不再收 `max_notional_usd`**：名义上限守的是「单笔新增敞口」，而补挂腿是减仓腿——
    沿用 `close_live_position` 的既有豁免（`okx/trading.py:224-225`），真正的上界是 `sz ≤ abs_pos`。

    Raises:
        OKXPrivateError("LIVE_BACKSTOP_REJECTED_LOCAL", …): 价非法 / 收敛后 < minSz /
            锁侧字段缺失。
        OKXPrivateError("LIVE_BACKSTOP_SIZE_EXCEEDS", …): **内部不变式断言**——收敛后仍
            `> abs_pos` 在数学上不可能，命中即说明上游算错，必须炸。
    """
```

```python
# okx/trading.py
async def place_backstop_stop(session: AsyncSession, creds: AccountCredentials,
                              account_id: int, stop: AlgoStopInput, *,
                              settings: Settings) -> dict:
    """补挂止损时序：守门 → posMode → 实时 |pos| → H2 → place_algo_order → 审计。

    与 `place_live_order`（:144）的三点差异（都是刻意的）：
      · **不设杠杆**（补挂不改变仓位参数）；
      · **不查 `_would_reduce_net_position`**（本单本就是减仓单，那条守卫的语义正相反）；
      · 审计行 `ord_type="conditional"`、`px=None`、`sl_trigger_px=stop.stop_px`。
    """
```

```python
# okx/trade_client.py
async def place_algo_order(self, payload: dict) -> dict:
    """`POST /api/v5/trade/order-algo`；成功返回 `data[0]`（含 `algoId` / `algoClOrdId`）。

    业务码判定与 `place_order`（:60-75）**逐字同构**：先顶层 `code` 再 `data[0].sCode`，
    任一非 `"0"` 即抛原始 sCode。**本方法只被补挂路径调用**，不对外开放通用算法委托能力。
    """
```

---

## 五、数据模型

### 5.1 配置（`services/quant/app/config.py`，全部新增）

```python
# ---- 交易所侧兜底自愈（ai-trade-backstop-repair-and-net-backtest §2.2）----
# 第 3 轮把「交易所侧到底挂着什么」做成了可核实的事实，但检测到「没有兜底」之后系统什么都不做：
# 一个 trend_hold 会话会带着一个裸露的仓位继续跑 1–3 天。本段补上执行体。
# 总闸关 ⇒ 整层不装配、`horizon.backstop.repair` 整键缺席、prompt 逐字回退到第 3 轮的五句、
# 人工端点 404、一个写请求都不发。
ai_trade_backstop_repair_enabled: bool = True
# 缺省模式（启动请求未显式指定时回填）。**默认 plan**：本轮是本仓库第一条 AI 侧自动写路径，
# 默认给方案 + 一键执行，而不是默认自动花钱（§九 D-1）。
ai_trade_backstop_repair_default_mode: Literal["plan", "arm"] = "plan"
# 触发价与现价之间要求的最小距离（bps）。补一张会立刻触发的止损 = 立刻市价平仓，
# 那是把安全层变成亏钱机器的最短路径（H1-e）。
ai_trade_backstop_repair_min_gap_bps: float = Field(default=15.0, ge=0, le=1000)
# 单持仓周期内最多补几次；**0 ⇒ 本层整体关闭（自动与人工都不补）**，不是「无限次」。
ai_trade_backstop_repair_max_per_epoch: int = Field(default=2, ge=0, le=10)
ai_trade_backstop_repair_cooldown_s: float = Field(default=60.0, ge=0, le=3600)
ai_trade_backstop_repair_max_per_session: int = Field(default=8, ge=0, le=100)
# 覆盖率阈值**不新开旋钮**：恒复用 `ai_trade_backstop_min_coverage`（G6 的同一个数，默认 0.999）。
# 「多少算覆盖不足」在一个会话里只能有一个答案（沿用 `ai_trade_profit_guard_max_px_age_s`
# 被 hold_horizon 复用的既有先例）。
# [P1-2] 判定价年龄阈同理**不新开旋钮**：恒复用 `ai_trade_profit_guard_max_px_age_s`（默认 90s，
# 与 `HorizonRule.max_px_age_s` 同一个数）。本层是它的第三个复用方。用一个过期的价去论证
# 「现价还没越过止损」，正是「补一张会立刻触发的止损」最现实的发生路径（H1-e0）。

# ---- 回测净额真值（§2.3）----
# 回测的**记账**自第 2 轮起已含真实资金费，但**成本估算**两个入口仍硬传 funding_enabled=False，
# 于是 G3 与 edge 门禁在回测里按「资金费为 0」判定，模式越长越乐观。
# 关 ⇒ 两处入参逐字节回退到第 3 轮（`(None, False)`），回测结果逐字节相同。
ai_trade_funding_cost_backtest_enabled: bool = True
```

### 5.2 内存态（全部会话级，进程内）

| 位置 | 字段 | 语义 |
|---|---|---|
| `RepairRuntime` | `rule` | `None` ⇒ 整层不装配 |
| | `_state` / `_blocked_reason` | 当前状态机格与原因 |
| | `_epoch_seq` / `_attempts_epoch` / `_attempts_session` | H3 计数（`note_epoch` 清零前者） |
| | `_last_attempt_wall` | H3 冷却基准（成功与失败都写） |
| | `_last_result` | `{ok, ts, algoId, algoClOrdId, stopPx, sz, code, message}` |
| | `_task` / `_closed` | 单飞句柄与停会话闸（镜像 `BackstopRuntime`） |
| `AiTradeSession` | `self._repair` | 装配点，紧邻 `self._backstop`（`session.py:173`） |

### 5.3 落库（**零迁移**）

- **补挂留痕**走既有 `live_order_audit`（依赖核实 #18）：`ord_type="conditional"`、
  `direction=<持仓方向>`、`sl_trigger_px=<触发价>`、`sz=<张数>`、`px=None`、`lever=None`、
  `status ∈ {accepted, rejected, error}`、`error_code=<归一码>`。**新增的只是 `ord_type` 的一个
  新取值**，没有新列、没有新表。
- **`repair` 不进 `metrics_json`**（沿用第 3 轮 D-6：一个 3 天前的「已核实」在历史页上只会骗人）。
  历史页整块不渲染自愈状态；需要复盘时看审计表。

### 5.4 wire 形状（`horizon.backstop.repair`，嵌套加法键）

```jsonc
{
  "mode": "plan",                    // "plan" | "arm"
  "state": "planned",                // idle|planned|blocked|placing|placed|verified|failed
  "blockedReason": null,             // state=="blocked" 时必填，值域见 §4.5
  "plannedStopPx": 61234.5,          // 方案价；算不出 ⇒ null
  "plannedSz": 0.3,                  // 方案张数（差额，未取整）；算不出 ⇒ null
  "coveredSz": 0.0,                  // 来自检测层，原样转发（**不重算**）
  "positionSz": 0.3,
  "attemptsEpoch": 0, "maxPerEpoch": 2,
  "attemptsSession": 0, "maxPerSession": 8,
  "coolingSeconds": null,            // 冷却剩余；未冷却 ⇒ null（**绝不用 0 冒充**）
  "lastResult": {                    // 从未尝试 ⇒ null
    "ok": true, "ts": 1786300000, "algoId": "6xxxx",
    "algoClOrdId": "aragonh0003a00", "stopPx": 61234.5, "sz": 0.3,
    "code": null, "message": null
  }
}
```

**三条形状纪律**（下游按此实现，评审逐条核对）：

1. **`coveredSz` / `positionSz` 原样转发检测层**，本模块不重算——两处各算一份正是本仓库反复
   出事的形态。
2. **`null` 与 `0` 是两个结论**（沿用第 3 轮 [P0-2]）：`plannedSz == null` 是「算不出」、
   `0` 是「不需要补」；`coolingSeconds == null` 是「未冷却」、`0` 是「刚好到点」。
3. **`state` 与 `blockedReason` 是唯一真源**：prompt、卡片、端点错误体三处**读同一份值域**，
   任一处新增取值而另两处未跟进，用例即红（放行条件 7）。

---

## 六、测试与验收标准

### 6.1 后端单测（新增约 102 条）

**T 组 · `test_ai_trade_backstop_repair.py`（纯核心，约 39 例）**

| # | 用例 | 断言 |
|---|---|---|
| T-1 | `rule_from_settings` 总闸关 ⇒ `None` | 整层不装配 |
| T-2 | `max_per_epoch == 0` ⇒ `rule_from_settings` 返回 `None` | 0 是关闭不是无限 |
| T-3 | `basis != "exchange"` ⇒ `plan_repair` 返回 `None` | **未核实绝不动手** |
| T-4 | `hasStop` 为真且 `coverageRatio >= min_coverage` ⇒ `None` | 覆盖达标不动手 |
| T-1b | **[P1-4]** `horizon_armed=False` ⇒ `rule_from_settings` 返回 `None` | 没有呈现面就不装配写路径 |
| T-5 | `coverageRatio is None` 且 `coveredSz is None` ⇒ `blocked(coverage_unknown)` | **不知道就不补** |
| T-5b | **[P0-2]** `hasStop is True` + `coverageRatio is None` + `coveredSz is None` ⇒ `blocked(coverage_unknown)`（**不是** `idle`） | 守 (d) 可达；v1 的 c 会让本例落 `idle` |
| T-5c | `hasStop is True` + `coveredSz is None` + `closeFraction=="1"` ⇒ `coverageRatio==1.0` ⇒ `None`（`idle`） | 最常见的随单 OCO 形态**不得**被 T-5b 的放宽误伤 |
| T-6 | `coveredSz == 0.0`（确认一张都没挂）⇒ 产出方案，`sz == positionSz` | 最常见路径必须通 |
| T-7 | `coveredSz == 0.5, positionSz == 1.0` ⇒ `sz == 0.5` | **只补差额** |
| T-7b | **[P0-4]** `positionSz == 1.0` 而实时 `abs_pos == 3.0`（账户另有他仓）⇒ 下单 `sz == 1.0` | **永不处置本层不拥有的仓** |
| T-8 | `local_sl_px is None` ⇒ `blocked(no_local_px)` | 无价可补 |
| T-9 | `mark_px` 为 `None` / `NaN` / `<= 0` ⇒ `blocked(no_mark_px)` | 三个入参各一例 |
| T-9b | **[P1-2]** `px_age_s > max_px_age_s` ⇒ `blocked(px_stale)`，且**优先于** `px_through` | 过期价不得用来论证「还没越过」 |
| T-10~11 | 多头 `stop_px >= mark_px` / 空头 `stop_px <= mark_px` ⇒ `blocked(px_through)` | **越过即拒** |
| T-12~13 | 距离恰为 `min_gap_bps` ⇒ 通过；差一个 bp ⇒ `blocked(px_too_close)` | 边界双向 |
| T-14 | `order_in_flight` ⇒ `blocked(order_in_flight)` | |
| T-15~16 | `attempts_epoch >= max_per_epoch` / `attempts_session >= max_per_session` ⇒ `blocked(attempts_exhausted)` | |
| T-17 | `now - last_attempt < cooldown_s` ⇒ `blocked(cooling)` 且 `coolingSeconds` 为正 | |
| T-18 | **降级码优先级**：同时触发 `coverage_unknown` 与 `px_through` ⇒ 前者胜出 | 与 §4.5 的固定顺序逐行对齐 |
| T-19 | `mode == "plan"` 且判据全过 ⇒ `state == "planned"`（**不是** `placing`） | |
| T-20 | `mode == "arm"` 且判据全过 ⇒ `state == "placing"` | |
| T-21~24 | `wire()` 的四条形状纪律（`rule is None ⇒ None`、`coveredSz` 原样转发、`null≠0`、`lastResult` 从未尝试为 `null`） | |
| T-25 | `note_epoch(seq)` 换 epoch ⇒ `attempts_epoch` 清零、`attempts_session` **不清零** | |
| T-26~28 | 状态机：`placing → placed → verified`；`placed` 后核实仍未覆盖 ⇒ `blocked(verify_failed)` | **不许自己给自己发绿勾** |
| T-29 | 补挂成功后调用了 `backstop.force_next()` | 假件断言 |
| T-30 | `close()` 之后在途任务回来不写状态、不推事件 | 镜像 `BackstopRuntime` |
| T-31 | `algoClOrdId` 对同一 `(会话, epoch, attempt)` **恒等**，换 attempt 才变 | 幂等（§2.2.4） |
| T-31b | **[P0-1]** 同一 `(epoch, attempt)`、不同 `inst_id` / 不同 `started_ts` ⇒ ID **必须不同**；长度 ≤32 且全字母数字 | 多标的分组与会话重启不得撞 ID |
| T-32~34 | 单飞：连续三次 `schedule` 只产生一个 task；`plan` 模式下 `schedule` 一个请求都不发 | |

**B 组 · `test_ai_trade_backstop_repair_io.py`（约 23 例）**

- B-1~4 `build_algo_stop_payload`：net 模式带 `reduceOnly` 不带 `posSide`；对冲模式反之；
  三个 sl 字段与 `build_attach_algo_orders` 逐字一致；`sz` 按 `lot_sz` 向下取整。
- B-5~6 平仓腿方向：多头 ⇒ `side == "sell"`、空头 ⇒ `"buy"`（复用 `_close_side`）。
- B-7~12 `validate_algo_stop`：价非法 / 收敛后 `< min_sz` / 锁侧字段缺失（**造一个既无
  `reduceOnly` 又无 `posSide` 的 payload`**）各抛对应码；合法 ⇒ 回收敛并取整后的 sz。
  **[P1-1] B-10 改判**：名义价值远超 `live_max_order_notional_usd`（如 5000 USDT 的累积仓）
  ⇒ **必须通过**，不得拒单（沿用 `okx/trading.py:224-225` 的减仓腿豁免）。
- B-13~16 `place_algo_order`：顶层 `code != "0"` / `sCode != "0"` 抛原始码；成功回 `data[0]`；
  429 走退避（复用 `_post` 的既有用例范式）。
- B-17~22 `place_backstop_stop` 时序：拒单落 `status="rejected"` 审计行；成功落
  `status="accepted"` 且 `ord_type == "conditional"`、`ord_id=<algoId>`、`cl_ord_id=<algoClOrdId>`
  （[P2-3]）；**不调用 `set_leverage`**；**不调用 `_would_reduce_net_position`**；异常路径也必落审计；
  DB session 由 `get_sessionmaker()` 短生命周期开（[P2-4]，镜像 `executor.py:132-134`）。
- **B-19b [P0-4]** 入参 `sz` 偏大（本地读数 1.0、实时 `abs_pos` 0.4）⇒ 实际下单 `sz == 0.4`，
  **不抛 `LIVE_BACKSTOP_SIZE_EXCEEDS`、不落 rejected 审计**（能收敛的越界不是故障）。

**S 组 · `test_ai_trade_backstop_repair_session.py`（约 22 例）**

- S-1 未装配（总闸关）⇒ `horizon.backstop` 里**无 `repair` 键** + prompt 兜底行
  **逐字节等于第 3 轮**（冻结用例，读取 HEAD 期望串）。
- S-2~7 §2.2.7 六个分支的 prompt 子句各一例，且每句**以「有没有兜底」结尾**（正则断言）。
- S-8 `plan` 模式的会话跑 10 个 tick ⇒ `place_algo_order` 调用次数为 **0**。
- S-9 `arm` 模式且检测到未覆盖 ⇒ 恰好 1 次调用（单飞）。
- S-10 补挂成功 ⇒ 推了一条 status；**未推 error**。
- S-11 补挂失败 ⇒ 推一条 `error`，`code` 为归一码，`message` 含中文标签、**不含裸 sCode 之外的
  任何凭证片段**。
- S-12 `px_through` 由假翻真 ⇒ 推一条 `error`；连续三个 tick 仍为真 ⇒ **不再重复推**（节流）。
- S-13~15 端点：404（组/成员不存在）、409（`blockedReason` 与 wire 同值）、200（回执字段齐全）。
- S-16 端点在 `ai_trade_enabled=False` 时仍可调用（收敛风险的动作不守总闸）。
- S-17 `session.py` 的新增行数 `≤ 18`（[P1-3] 由 14 上调；AST 计数断言，沿用第 3 轮的范式）。
- S-18 决策链**未被改动**：装配自愈层前后 `gate_reason` 的合成路径与降级码集合逐字相等。
- **S-19 [P0-3]** 打真 `GET /ai-trade/horizon-profiles`（`response_model` 生效的那条路径），断言
  `backstopRepair` 块在回执里且 `defaultMode` / `modes` / `maxPerEpoch` 齐全。**手造 wire 的前端
  用例守不住这一格**——这是本轮唯一一条只能由端点级用例发现的失效。
- **S-20 [P0-3]** `POST /ai-trade/start` 带 `tradingHorizon.backstopRepair="arm"` ⇒ 会话的
  `RepairRule.mode == "arm"`，且 `group_status()` 的 `tradingHorizon` 回执里镜像该值；
  带非法值 ⇒ **422**（值域在 schema 层）。
- **S-21 [P1-3]** `plan` 模式下人工触发一次补挂 ⇒ **恰好多推一条 `status`**，且其中
  `horizon.backstop.repair.state` 已流转；连续三个 tick 状态不变 ⇒ **不再重复推**。
- **S-22 [P1-4]** `horizon_rule is None` 的会话 ⇒ 无 `repair` 键、`place_algo_order` 调用次数为 0。

**F 组 · `test_ai_trade_backtest_funding_cost.py`（约 18 例）**

- F-1~3 `snapshot_at`：只取 `ts <= 目标` 的最后一点（**无未来函数**）；点数 `< 2` ⇒ `None`；
  空曲线 ⇒ `None`。
- F-4 `interval_s == interval_hint` 且 `interval_known is True`。
- F-5~6 `_cost_funding`：总闸关 ⇒ `(None, False)`；曲线不可用 ⇒ `(None, False)`
  （**不是** `(None, True)`）。
- F-7 **等价门禁**：总闸关时，同一份行情跑两遍回测（本轮前后）⇒ `metrics` 与 `gate_reason`
  序列逐字节相同。
- F-8 总闸开且曲线可用 ⇒ `HorizonFacts.cost_bps` 严格大于关闭时（多头付费方向）。
- F-9 空头且费率为正 ⇒ `funding_carry_bps == 0`（只取不利侧，`cost_model.py:148-159`）。
- F-10~11 **[P1-9] 必须先把 `settings.ai_trade_edge_gate_k_min` 设成正数**（默认 0.0 ⇒
  `evaluate_edge_gate` 第一句就 `return None`，不设则这两条是恒过的空用例）；再断言入参同步改、
  `confidence` 保持 `"ok"`（有数不是 degraded）。
- F-12~13 **[P1-6]** 断言 `backtest._funding_notes()`（**不是** `format_backtest_notes`）的产物：
  两个分支各一例、含 `interval_hint` 与 `periods` 两个数、含 [P2-1] 的覆盖计数；并断言
  **旧的那句「已知欠账：…成本估算仍不含资金费」已不再出现**（冻结式反向断言）。
- **F-13b [P1-6]** `horizon_rule is None`（对照臂）时资金费口径句**仍然出现**——
  它挂在 `_funding_notes()` 上，不受周期契约装配与否影响。
- F-14~16 `trend_hold` 长周期下 `funding_periods >= 2`；`hourly_swing` 下 `== 1`；
  `expected_hold_bars` 仍取自契约（不回退全局常数）。

### 6.2 前端单测（新增约 24 条）

- U-1~7 `describeRepair` 七个 `state` 的标签、色调、按钮可见性。
- U-8~14 全部 `blockedReason` 的中文标签**逐条存在**（缺一即红——防「后端加了新码、前端显示
  原始英文」这类第 3 轮已经踩过的坑）。
- U-15~16 `turnoverSeverity` 新两格的排序。
- **U-15b [P1-7]** `describeTurnoverChip` 在 `failed` / `planned` 下的标签与色调；且**既有四格
  （mismatch / 覆盖不足 / 冷却 / 预算耗尽）的 chip 文案在插格后逐字不变**——这条才是真正的守门
  用例（severity 重排最容易打断的就是它们）。
- U-17~18 卡片：`repair` 键缺席 ⇒ 第五行不渲染；`state=="idle"` ⇒ 同样不渲染。
- U-19 按钮请求态：点击后禁用且文案变「补挂中…」（态由父组件传入，[P2-5]）。
- U-20 弹窗：两段控件的默认选中项**来自 profiles wire**，不是硬编码（把 wire 的
  `defaultMode` 改成 `"arm"` ⇒ 默认选中项跟着变）。
- **U-20b [P1-10]** `hourly_swing`（`targetHoldMaxSeconds` 远小于 86400）下控件**仍然渲染**
  ——守它没被顺手挂进 `BackstopNotice` 的跨日分支。
- **U-21 [P1-8]** 复发守门：在弹窗打开态下切到 `arm`，`await` 一个渲染周期（并触发一次父树
  重渲染）后值**仍为 `arm`**。这条对应 `ai-trade-modal-revert-recurrence`——该缺陷的形态就是
  「新字段写在了另一个非稳定依赖的 effect 里，约 1 秒后被冲回默认」，每加一个新字段就复发一次。

### 6.3 放行条件（Definition of Done，逐条可核对）

1. §6.1 / §6.2 全部用例为绿；`ruff` 干净；`tsc` 除既有 `WeightBatchArena.test.tsx` 外无新增错误。
2. **`backstop_repair.py` 模块 docstring 顶部**逐字写着两条不变式：
   「本层永不参与决策」「本层发出的每一张单都只可能减少敞口」。
3. **一次真实的 `order-algo` 实测**（模拟盘或极小仓位实盘）已执行，并把
   `algoId` / `algoClOrdId` 去重行为 / `sz` 与 `closeFraction` 的实际接受情况 / 拒单 sCode
   四项**实测取值**写进 §十一 实施发现段。**不得硬编猜测映射**（沿用第 3 轮 [P0-2] 的纪律）。
4. `session.py` 新增 `≤ 18` 行、`backtest.py` `≤ 30` 行、`horizon_runtime.py` `≤ 12` 行、
   `hold_horizon.py` `≤ 26` 行、`schemas.py` `≤ 26` 行（三处数字均由评审按 [P1-3]/[P1-6]/[P0-3]
   上调；**记录表里的数字算错即判据失效**，第 3 轮已为此栽过一次）；
   超支即在文末登记欠账并说明，不得默默超支。
5. **冻结用例双向都有**：未装配 ⇒ prompt 兜底行与第 3 轮逐字节相同；装配 ⇒ 含且仅含一句自愈子句。
6. **轴 B 等价门禁**（F-7）在总闸关时逐字节通过，且实现节点**实际跑过一次开关对照回测**，
   把两次的 `avgHold` / `turnoverPerDay` / `netPnl` 三个数写进实施发现段。
7. `state` / `blockedReason` 的值域在**后端常量、prompt 文案表、前端标签表、端点错误体**四处
   一致（用例 U-8~14 + S-13 守），**含新增的 `px_stale`**（[P1-2]）。
7b. **[P0-3] 端到端链路必须有一条真端点用例走通**：`GET /horizon-profiles` 回执里有
   `backstopRepair`（S-19）、`POST /start` 的选择落到 `RepairRule.mode` 且在 `group_status` 回执里
   镜像（S-20）。**手造 wire 的前端用例不构成对这条链路的验收。**
7c. **[P0-1] 跨成员不碰撞**：在一个 3 标的分组上肉眼或用例确认三名成员算出的 `algoClOrdId`
   互不相同（T-31b + 放行条件 8 的真实会话观察）。
8. 合入前在**真实运行的会话**上肉眼确认：弹窗控件出现（含 `hourly_swing`，[P1-10]）、卡片第五行
   有数、chip 文案正确（[P1-7]）、按钮可点、**点击后无需等到下一个决策轮即可看到状态流转**
   （[P1-3]——这是本条最容易假通过的一格：在决策频繁的 `hourly_swing` 上看不出问题，必须在
   `trend_hold`／唤醒周期 0 的会话上验）。后端算了 ≠ 用户看得见，本仓库已为此栽过五次。
9. 全量回归与**干净 HEAD worktree 实测**的红灯基线逐条对账（不靠记忆），结果写进实施发现段。
   已知基线：`test_ai_trade_prior_session` 7 例、`test_factor_library_expansion` 7 例（孤儿文件）、
   全量顺序下的 `test_agent_ws` flake。
10. 精确暂存：`git add <具体路径>`，剔除 §1.4 列出的四组在制变更；`git log -1 --stat`
    只含本特性文件。

### 6.4 上线后的观察窗口（runbook）

| 层级 | 观察什么 | 判据 |
|---|---|---|
| L0 | `plan` 模式跑满 24h | 卡片是否出现过 `planned`；出现过说明检测→方案链路通 |
| L1 | 人工点一次「立即补挂」 | 端点回 200 且下一次核实把 `placed` 升为 `verified` |
| L2 | 切 `arm` 模式跑 3 个持仓周期 | `attemptsSession` 不失控（应 `≤ 3`）；无 `failed` |
| L3 | 观察 `px_through` / **`px_stale`** 出现频次 | 前者频繁 ⇒ 本地止损价与实盘偏离过大，回看 `exchange_oco_px` 的 `derived` 分支；后者频繁 ⇒ 行情链路有问题，因为 `_last_px_wall` 本该每个盯市 tick 都在更新（[P1-2]） |
| L4 | 轴 B 开关对照回测 | **[P2-2]** 判据是「同一区间 `trend_hold` 的 `cost_bps` 上升且 `avgHold` 不降」；**`netPnl` 只观察不判定**——资金费不进权益曲线与 metrics（`backtest.py:1091-1093`），轴 B 只改门禁入参，交易序列变化后 pnl 的方向推不出来，拿它当判据会在实现正确时误报失败。edge 门禁的计数**不作为观察项**（默认 `k_min=0` 恒空转，[P1-9]） |

---

## 七、风险与缓解

| # | 风险 | 影响 | 缓解 |
|---|---|---|---|
| R-1 | **补挂的单立刻触发** ⇒ 变成一次最坏价位的市价平仓 | 真实资金损失 | H1-e（`px_through` / `px_too_close`）+ T-10~13 双向边界用例 + `min_gap_bps` 可调 |
| R-2 | **重复止损打穿到反向**（对冲模式下补挂腿不带 `reduceOnly`） | 真实资金损失，且方向反转 | (d) `coveredSz is None ⇒ 不补` + 只补差额 + H2 的 `sz <= abs_pos` + 锁侧字段必居其一 |
| R-3 | 超时但其实成功 ⇒ 第二张单 | 同 R-2 | 确定性 `algoClOrdId`（§2.2.4）+ 若实测发现 OKX 不去重则降级为「每次尝试前强制先核实」 |
| R-4 | 交易所持续拒单 ⇒ 每 tick 打一发 | 配额耗尽 / 风控标记 | H3 三重频率锁 + 单飞 + 失败也计入冷却 |
| R-5 | 自愈成功给了用户虚假安全感 | 决策放松 | `placed → verified` 必须过一次真实核实才改口（§2.2.5）；prompt 每句以「有没有兜底」结尾 |
| R-6 | 轴 B 让回测变差 ⇒ 用户以为特性坏了 | 误判 | notes 明说口径变化与方向；runbook L4 给出预期变化方向；总闸可一键回退 |
| R-7 | 历史费率曲线拉不到 ⇒ 静默 degraded 改变门禁 | 回测判定漂移 | `_cost_funding` 曲线不可用时**逐字回退** `(None, False)`（F-5~6 守） |
| R-8 | 工作树混着他人在制变更 | 提交污染 | §1.4 的剔除清单 + hunk 级暂存 + patch 以 bytes 读写（Windows CRLF） |
| R-9 | `order-algo` 的字段假设与实际不符 | 补挂恒失败 | 放行条件 3 的真实实测**先于**实现定稿；不符即按实测改，不硬编猜测 |
| R-10 | 自愈层被误读成「又一道门禁」 | 后续维护走偏 | 不新增 G 编号、不进 `gate_reason`、不进决策轨迹；docstring 顶部写死边界 |
| **R-11** | **[P0-1]** `algoClOrdId` 跨成员 / 跨重启碰撞 ⇒ 第二个成员永远补不上，且失败码看起来「完全正确」 | 安全层在多标的下静默失效 | 三段式 ID（会话身份段由 `(inst_id, started_ts)` 派生）+ T-31b + 放行条件 7c |
| **R-12** | **[P0-4]** 账户上另有 `live_auto` / 手工持仓时，按全账户 `abs_pos` 补出的止损会替用户平掉他自己的仓 | 真实资金，且是「安全层造成的损失」这一最坏叙事 | `sz ≤ min(abs_pos, 自管仓)` + T-7b + `place_backstop_stop` 下单前重算 |
| **R-13** | **[P1-3]** 状态流转不上屏 ⇒ 用户点完按钮界面无反应，重复点击 | 体验劣化 + H3 配额被人工消耗 | 状态变化即补推 status + 按钮请求态 + S-21 |
| **R-14** | **[P1-6]** 面板同时说「已含资金费」与「仍不含资金费」 | 用户对整份回测失去信任 | 改写 `backtest.py:1094-1095` 的既有欠账句 + F-12/13 的反向冻结断言 |

---

## 八、实施顺序（给下游代码开发节点）

> **[评审补] 第 0 步：先把 §零 的四条 P0 在纸面上确认一遍。** P0-1（ID 三段式）、P0-2（判据 c）、
> P0-3（三处回填链 + response_model 字段）、P0-4（张数 `min(abs_pos, 自管仓)`）——这四条都是
> **写完再改成本极高**的结构性决定：P0-3 牵动 5 个文件的签名，P0-4 牵动纯核心与 IO 层的分工。

1. **先做轴 B**（最小、最独立、零真实资金面）：`snapshot_at` → `_cost_funding` → 两处入参 →
   **`_funding_notes()` 的两句新口径 + 删旧欠账句（[P1-6]）** → F 组用例 + 等价门禁。
   **这一步跑绿之前不碰轴 A**。
2. **真实 `order-algo` 打一发**（放行条件 3），把四项实测写进 §十一。**先于** `build_algo_stop_payload`
   定稿；若实测与假设不符，按实测改设计并在 §十一 记录。
3. `okx` 三件套：`place_algo_order` → `build_algo_stop_payload` / `validate_algo_stop` →
   `place_backstop_stop` + B 组用例（全部脱网，用假件）。
4. 纯核心 `plan_repair` + `RepairRule/RepairPlan` + T-1~24（**先写用例后写实现**，
   降级码优先级表逐行对齐）。
5. `RepairRuntime` 状态机 + T-25~34。
6. 会话接线（`session.py` 三处 + `schedule_repair`）+ `horizon_runtime` 转发 + S 组用例。
7. 文案层（`hold_horizon_text`）+ 冻结用例双向。
8. **契约链路一次做全（[P0-3]）**：`schemas`（三处：`AiTradeHorizonIn.backstopRepair` /
   `AiTradeBackstopRepairOut` / **`AiTradeHorizonProfilesOut.backstopRepair`**）→
   `hold_horizon`（`HorizonRule` 字段 + `resolve_horizon_rule` 回填 + `rule_wire` 镜像 +
   `profiles_wire` 新块）→ 端点 + `manager.repair_member` → S-13~16 + **S-19/S-20（真端点）**。
9. 前端：类型 → `aiTradeBackstopRepair.ts` → **`turnoverSeverity` 与 `describeTurnoverChip` 同一处改
   （[P1-7]）** → 卡片第五行 → 按钮接线 → **`AiTradeLaunchModal` 的 state（塞进既有 `[open]`
   effect）+ `AiTradeHorizonFields` 控件（[P1-8]/[P1-10]）** → U 组用例。
10. 全量回归 + 干净 HEAD worktree 红灯基线对账 + 行数预算对账 + §十一 补齐。

---

## 九、有意偏离与拍板记录

| # | 偏离 | 拍板理由 |
|---|---|---|
| D-1 | **默认 `plan` 而非 `arm`** | 本仓库此前所有 AI 侧安全层的纪律是「绝不新增下单路径」，本轮是第一次破例。默认自动执行与默认死码之间的第三条路是「默认给方案 + 一键执行」：它既不静默花钱，也不是没有落点的常量（本仓库为「总闸默认关 ⇒ 整个特性空转」栽过一次，为「没有展示落点的免责常量即死码」栽过一次，本条同时避开两者）。用户在弹窗里一次点击即可进 `arm`，且那次点击附带明确的授权文案 |
| D-2 | **`coveredSz` 不可知时不补**（与 G6 的 fail-open 方向相反） | 两层的失败方向本就不同：G6 面对「不知道」时放行（不该因不确定剥夺交易能力），自愈层面对「不知道」时不动手（不知道覆盖多少就算不出补多少张，硬补可能打穿到反向）。同一份「不知道」在两层给出相反动作，恰恰因为两层的错误代价相反 |
| D-3 | **只补止损腿，不补止盈腿** | 沿用第 3 轮 D-7：兜底的定义是「最坏情况下有人替你退出」。止盈腿不构成兜底，补它只会增加一条会改变盈亏分布的写路径 |
| D-4 | **不撤单、不改价（no amend / no cancel）** | 撤单需要判断「哪张是我的、哪张是用户手工挂的」，而算法委托的归属判定本身就是第 3 轮花了一整条 P0 才做对的事。只补差额是唯一一种**无论判断对错都不会让事情变糟**的操作 |
| D-5 | **不新增 G 编号，改用 H1/H2/H3** | 自愈层不参与决策、不否决、不进 `gate_reason`。用 G 编号命名会让下一个维护者去 `gate_reason` 的或链里找它，找不到再自己加一条——那正是要防的形态 |
| D-6 | **`placed` 与 `verified` 是两个状态** | 第 3 轮存在的全部理由是「不许说没核实过的话」。这条纪律必须同样适用于系统自己刚做的事：OKX 回了 `algoId` 不等于那张单真的在那里 |
| D-7 | **覆盖率阈值复用 `ai_trade_backstop_min_coverage`，不新开旋钮** | 「多少算覆盖不足」在一个会话里只能有一个答案。第 3 轮 D-1 已为「同一口径两处各存一份」定过调 |
| D-8 | **轴 B 曲线不可用时回退 `(None, False)` 而非 `(None, True)`** | `(None, True)` 在 `cost_model` 里是「数据缺失 ⇒ degraded ⇒ 门禁收紧」，那会让一次观测层的网络抖动静默改变回测判定。回退到「有意不建模」才与今天逐字节相同 |
| D-9 | **`max_per_epoch == 0` 表示整层关闭，而非无限次** | 与第 3 轮 G4 的 `frac=0 ⇒ 关闭本闸`（不是「无限长」）逐字同款语义。数值旋钮的 0 在本特性链里恒表示「关」 |
| D-10 | **自愈状态不落库** | 沿用第 3 轮 D-6：一个 3 天前的「已核实」在历史页上只会骗人。复盘走审计表 |
| D-11 | **人工端点不守 `ai_trade_enabled` 总闸** | 补挂是收敛风险的动作，与 `/stop` 同理：任何时候都该能执行。它守的是运维总闸（关 ⇒ 整层未装配 ⇒ 404） |
| **D-13** | **[P1-4] 未装配周期契约即整层不装配**（而不是「装配但不可见」） | 本层唯一的呈现面挂在 `horizon.backstop.repair` 上，而 `HorizonRuntime.refresh` 在 `rule is None` 时恒返回 `None`。可选项只有两个：给自愈层另开一条独立的 wire 载体（要动 `store.ts` 的逐键手抄 reducer，正是第 3 轮 [P0-1] 踩过的坑），或者不装配。选后者：**一条用户完全看不见的自动下单路径，比少一个功能危险得多** |
| **D-14** | **[P1-1] 补挂腿不受 `live_max_order_notional_usd` 约束** | 那个常数守的是「单笔**新增**敞口」；补挂腿只减不增，且持仓由多次加仓合法累积而成，可以远超它。仓库既有纪律早已写明平仓路径豁免它（`okx/trading.py:224-225`）。照旧写法，仓位越大越裸露、补挂越是被本地拒死——一条只在小仓上生效的安全层 |
| **D-15** | **[P1-2] 判定价年龄阈复用 `ai_trade_profit_guard_max_px_age_s`（第三个复用方）** | 与 D-7 同一条原则：「判定价多老算老」在一个会话里只能有一个答案。第 3 轮 D-1 的阻断级修正（新鲜度判据取错时钟 ⇒ 保护层 90% 时间恒 unavailable）已证明这一格出错的代价 |
| **D-16** | **[P1-6] 资金费口径句挂 `backtest._funding_notes()`，且同时删掉旧的欠账句** | 挂在 `format_backtest_notes` 会让未装配周期契约的对照臂完全没有披露（该函数 `rule is None ⇒ []`），而对照臂正是验收时最该对拍的那条。旧欠账句在本轮之后是假话——**一条在事实变化后没被撤下的免责，比没有免责更坏** |
| D-12 | **本轮不修第 3 轮欠账 D-13（换手预算跨会话持久化）** | 跨会话持久化要落库、要定义「同一契约」的身份（换标的？换预算？换模式？），属于另一轮；且第 3 轮已用「说清楚」代替「做到」（弹窗、卡片、验收卡三处逐字自认「本会话口径」），不构成谎言。继续登记为欠账（§十） |

---

## 十、非目标（本轮明确不做）

- **不做撤单 / 改价 / 补止盈腿 / 自动平仓**（D-3 / D-4）。
- **不在回测装配自愈**（沿用第 3 轮 D-5：回测没有交易所，让回测显示一个「已补挂」的绿勾，
  是本特性链要根除的那类谎言的镜像）。
- 不做换手预算的跨会话持久化（第 3 轮 D-13 的欠账，D-12 续留）。
- 不做运行中动态改周期模式 / 改预算（沿用第 1 轮结论：改模式 = 停会话重开）。
- 不做「验收未通过即自动停止会话」的自动干预（沿用第 2 轮结论）。
- 不做按标的分别设置自愈模式（分组共享一份契约，沿用周期模式的既有口径）。
- 不修第 2 轮欠账 D-9 的另一半（`cost_model` 在**实盘**结算周期未知时按 8h 保守估算）——
  实盘侧 `FundingCache` 已有 `interval_known` 且默认保守，与回测侧不同源，须单独评估。
- 不做 `session.py`（1766 行）/ `backtest.py`（1293 行）的拆分（既有行数债，本轮同样用硬预算
  压住，不新增）。
- 不改 `live_auto`（非 AI）链路的任何行为。

---

## 附：本轮与前三轮的关系（给评审节点的一页速览）

| | 第 1 轮 `holding-horizon-modes` | 第 2 轮 `horizon-acceptance-loop` | 第 3 轮 `turnover-budget-and-backstop` | **本轮** |
|---|---|---|---|---|
| 解决什么 | 持得太短 | 指标不真 | 开得太快 + 兜底是句没核实的话 | **知道没兜底却什么都不做** + **回测成本里资金费恒为 0** |
| 手段 | G1/G2/G3 平仓侧闸 | 三本账 + 五行验收卡 | G4/G5 开仓侧闸 + G6 覆盖闸 + 三态 `basis` | **H1/H2/H3 三道锁 + 一条只减不增的写路径**；**真实历史费率进回测成本模型** |
| 调查约束覆盖 | #1 #2 #3 #4 #6 #7（估算） | #7（真值·记账） | #5 #8（检测） | **#8（执行）** + **#7（回测口径的最后一公里）** |
| 新增下单路径 | 无 | 无 | 无（只读 GET） | **有（`POST /trade/order-algo`，只减不增）** |
| 状态 | 已合入 `11246ae` | 已合入 `e7fdda4` | 已合入 `e7fdda4` | 本文档 |

---

## 十一、实施过程发现的方案缺陷

> 本节由实现节点在落地过程中追加。按 spec §Constraints 的要求：**发现方案有误不得静默偏离**，
> 一律先记在这里，再按修正后的做法实现。每条给出「spec 原文怎么说 / 实际是什么 / 落地怎么做」。
> 放行条件 3（真实 `order-algo` 四项实测）、放行条件 6（轴 B 开关对照回测的三个数）、
> 放行条件 9（干净 HEAD worktree 的红灯基线）与行数预算对账，**必须**在此段留下实测记录。
> **评审补**：§零 的 8 条 P2 取舍结论也写在这里（采纳 / 不采纳 + 理由），不得静默忽略。

### 11.1 实施过程发现的方案缺陷（10 条，均已按修正后的做法落地）

| # | spec 原文怎么说 | 实际是什么 | 落地怎么做 |
|---|---|---|---|
| **I-1**（阻断级） | 依赖核实 #26 / #27 逐行核实了轴 B 要改的那两处入参 | **上一轮留下的 AST 冻结门禁守着的正是这两处**：`test_ai_trade_acceptance_wiring.py::test_backtest_cost_model_funding_args_are_unchanged`（第 2 轮 G-5 / [P1-5]）按 AST 断言 `funding=None, funding_enabled=False` **零改动**。依赖表核实了「这两行在哪」，却没追「谁在守着它们」——不改它，轴 B 一落地全量就红一条 | 把该用例**冻结方向反转**：从「不许改」改为「两处判定现场的入参必须来自**同一个** `_cost_funding`、且不得再退回字面量」，并补一条「判定现场恰好各调一次 `_cost_funding`」的唯一真源断言。原用例守的东西一条没少（仍是 AST、仍防第二条估算路径），只是被守的契约换成了新的 |
| **I-2** | §4.5 `validate_algo_stop(stop, meta, *, abs_pos, self_position_sz, covered_sz)` | 而同节的 `place_backstop_stop(session, creds, account_id, stop, *, settings)` 手里**只有 `stop`**——`self_position_sz` / `covered_sz` 无从取得，照签名实现只能在下单现场再猜一次「该补多少张」 | 两个数放进 `AlgoStopInput`（它本就是「聚合为值对象而非堆形参」的既有范式，`ClosePositionInput` 同款），签名收敛为 `validate_algo_stop(stop, meta, *, abs_pos)`。**收敛上界与下单意图一起旅行**，不存在第二处口径 |
| **I-3** | `place_backstop_stop(..., *, settings: Settings)` | [P1-1] 把名义上限从「拒绝判据」降为「只做记录」之后，`settings` **再没有任何消费方**（审计表也没有名义列）。保留一个空形参就是死参数 | 签名去掉 `settings`。这是 [P1-1] 自身的直接推论，不是额外裁剪 |
| **I-4** | H2 第 4 条：「`pos_mode == net_mode` ⇒ payload 必须含 `reduceOnly`；否则必须含 `posSide`」，并要求 B 组「造一个既无 `reduceOnly` 又无 `posSide` 的 payload」测它 | 但 `validate_algo_stop` 的签名里**没有 payload**，这条判据无处安放 | 新增纯函数 `order_builder.ensure_close_only(payload)`，由 `build_algo_stop_payload` 组装完即调用（后置不变式断言）；B 组用例可直接对手造 payload 调它。「永不建立敞口」这条不变式因此有了代码化落点 |
| **I-5**（重要） | §4.5「`mark_px` 恒取 `executor` 断面的实时价（与 `schedule_from_executor` 同源）」，`facts_from_executor(executor, *, px_age_s)` | **`LiveExecutor` 上根本没有实时价**：`reconcile(mark_px, ts)` 只用不存，`schedule_from_executor` 也从不读价（它只读 `projected` / `entry_qty_sz` / OCO 回执）。真源是会话的 `_current_px()`，与 `px_age_s` 的真源 `_last_px_wall` 同属会话时钟 | 改收会话那份**既有**聚合读数 `SessionReadings`（`profit_guard_runtime.py:36`）：`facts_from_executor(executor, readings)` / `schedule_repair(repair, backstop, executor, readings, *, push, on_change)`。判定价、判定价年龄与利润保护层 / 周期层**同一个对象**，本层不可能另取一次；顺带把 8 个散形参收敛成 4 个（守 CLAUDE.md 形参 ≤5） |
| **I-6** | `RepairRuntime.__init__(settings, rule, executor, inst_id, *, session_key)`；§2.2.5 与 T-29 要求补挂成功后 `backstop.force_next()` | 该签名里**没有任何拿得到 `backstop` 的口子**，`force_next` 无从调用；且下单通道写死在模块里，B / T 组无法脱网 | 加两个可选 kw：`verify_hook`（会话构造时传 `self._backstop.force_next`）与 `place_fn`（默认 `_default_place`，镜像 `LiveExecutor` 的五个可注入回调）。S 组另有一条用例断言 `verify_hook` 确实指向会话自己那个检测层运行时——挂错对象 ⇒ `placed` 永远等不到核实 |
| **I-7** | §3.2「`session.py` 多一个 2 行的 `_push_status` 薄封装」，`on_change` 为同步回调 | 会话侧要推的 `status` 是 **async**，而 `schedule()` 必须保持同步（绝不阻塞 5s 盯市 tick） | `RepairRuntime._notify` 判 `inspect.isawaitable`，是协程就排一个 task。会话侧因此可以直接传一个返回协程的 lambda，**那个 2 行薄封装被省掉了**（session.py 少 4 行） |
| **I-8** | §4.1 的 409 值域只列了 H1/H2/H3 的降级码 | 人工通道还有第三种失败：`plan_repair` 返回 `None`（**此刻根本不需要补**——未核实 / 无仓 / 覆盖已达标）。没有码，端点只能编一个或回 500 | 值域新增 `not_applicable`，**四处同步**（后端常量 / prompt 侧标签表 / 前端 `REPAIR_BLOCK_LABEL` / 端点错误体），T 组与 U 组各有守门用例 |
| **I-9** | — | `session_key` 要 `(inst_id, started_ts)`，而 `self.started_ts` 原本在 `self._backstop` **之后**约 20 行才赋值 | 把 `self.started_ts = int(time.time())` 上移到 `_backstop` 构造之前（净 0 行，只是位置变了） |
| **I-10** | [P2-1]「统计含资金费的决策轮 / 总决策轮」 | `_cost_funding` 有**两处**调用点（`_horizon_facts` 仅持仓时、`_run_decision` 每轮），在函数内计数会双计且分母口径不明 | 计数只挂 `_run_decision` 那一处（分母口径 = **决策轮**），并在代码里写明为什么另一处不计 |

### 11.2 放行条件逐条对账

| # | 放行条件 | 结论 |
|---|---|---|
| 1 | 用例全绿 / `ruff` 干净 / `tsc` 无新增错误 | ✅ 新增后端 **136** 例（T 52 / B 29 / S 37 / F 18）与前端 **41** 例全绿；`ruff` 对本轮全部改动文件干净；`tsc` 仅剩既有 `WeightBatchArena.test.tsx`（HEAD 已红） |
| 2 | `backstop_repair.py` docstring 顶部逐字写着两条不变式 | ✅ 「本层永不参与决策」「本层发出的每一张单都只可能减少敞口」；并保留了「只补差额是唯一一种无论判断对错都不会让事情变糟的操作」 |
| 3 | **一次真实 `order-algo` 实测** | ❌ **未执行——本轮唯一未闭合的放行条件**，详见 §11.4 |
| 4 | 行数预算 | ⚠️ 四处超支（合计 code 净增 29 行），详见 §11.3 |
| 5 | 冻结用例双向 | ✅ S-1（`repair` 键缺席 / `idle` ⇒ prompt 与上一轮逐字节相同）＋ `_clause()` 的 `startswith(base)` 断言（装配态下五支基线一个字节没改，且**恰好**追加一句） |
| 6 | 轴 B 等价门禁 + 开关对照回测的三个数 | ✅ F-7 通过；对照回测实测见 §11.5 |
| 7 | `state` / `blockedReason` 值域四处一致（含 `px_stale`） | ✅ T 组「每个码都说得出人话」+ U-8~14「每个码都有中文标签且不印裸码」+ S 组 prompt 侧断言 + 端点 409 用例 |
| 7b | 端到端链路有真端点用例 | ✅ S-19 打真 `GET /ai-trade/horizon-profiles`（`response_model` 生效的那条路径）断言 `backstopRepair` 块在回执里；请求侧走真 `_resolve_horizon` → `HorizonRule.backstop_repair_mode`，回执侧走真 `rule_wire` 与真 `group_status()`；非法值 422 |
| 7c | 跨成员不碰撞 | ✅ T-31b：三个标的 + 一次会话重启共 4 个 ID 互不相同，长度 ≤32 且全字母数字 |
| 8 | 真实运行会话上肉眼确认 | ❌ **需要人 + 真实账户**，本节点无法执行；与放行条件 3 一并登记为未闭合 |
| 9 | 全量回归与干净 HEAD worktree 的红灯基线逐条对账 | ✅ 见 §11.6 |
| 10 | 精确暂存 | ➡️ 属下一节点（提交）；本节点已在 §11.7 列出混合文件与外来 hunk 的确切位置 |
| 11 | [P1-9] F-10/11 先把 `k_min` 调正再断言 | ✅ 两条用例都显式 `ai_trade_edge_gate_k_min=0.5`；另有一条守「默认 `k_min=0` 时 notes **不得**宣称 edge 门禁也据此判定」 |
| 12 | 实测先于 `build_algo_stop_payload` 定稿 | ❌ 同放行条件 3；定稿依据是「与随单 OCO 逐字一致」，无任何硬编猜测，见 §11.4 |

### 11.3 行数预算对账（**实测**，不是估计）

计法沿用上一轮记录 `exchange_backstop.py`「437 raw / 218 code」的同一口径：**code = 去掉空行、纯注释行与
docstring 之后的行数**；raw 取 `git diff --numstat`。

| 文件 | 预算 | raw +/− | **code 净增** | 结论 |
|---|---|---|---|---|
| `ai_auto/session.py` | ≤ 18 | +26 / −2 | **19** | ⚠️ 超 1 行。I-7 已省掉 4 行（`_push_status` 薄封装），剩下的装配块（6 行，`RepairRuntime` 五个入参）压不下去 |
| `ai_auto/backtest.py` | ≤ 30 | +73 / −13 | **34** | ⚠️ 超 4 行。多出来的是 [P2-1] 的覆盖计数（3 行）与 `_cost_basis_note` 的「为什么不含」原因分支 |
| `ai_auto/horizon_runtime.py` | ≤ 12 | +19 / −3 | 7 | ✅ |
| `ai_auto/hold_horizon.py` | ≤ 26 | +46 / −2 | 24 | ✅ |
| `schemas.py` | ≤ 26 | +30 / −0 | 12 | ✅ |
| `ai_auto/hold_horizon_text.py` | +30 | +65 / −1 | **44** | ⚠️ 超 14 行。spec 只算了一个 `_repair_clause`；实际还需 `_repair_blocked_clause`（三支措辞不可合并）与 `_REPAIR_BLOCK_LABEL`（值域四处镜像的第二处，9 行表） |
| `okx/order_builder.py` | +62 | +100 / −0 | 51 | ✅ |
| `okx/trading.py` | +48 | +76 / −1 | 45 | ✅ |
| `okx/trade_client.py` | +26 | +31 / −0 | 11 | ✅ |
| `ai_auto/manager.py` | +12 | +12 / −0 | 6 | ✅ |
| `api/ai_auto.py` | +22 | +28 / −0 | 15 | ✅ |
| `ai_auto/funding_ledger.py` | +22 | +30 / −0 | 17 | ✅ |
| `config.py` | +22 | +27 / −2 | 7 | ✅（raw 含 20 行注释） |
| `ai_auto/backstop_repair.py`（新建） | ≤ 330 | 567 raw | **340 code** | ⚠️ 超 10 行（3%）。CLAUDE.md 的 800 行红线远未触及，不拆分 |
| `.env.example` | +14 | +31 / −0 | — | ⚠️ 超支，全部是注释（新增 7 个配置项，每项都要写清「0 是关不是无限」这类反直觉语义） |

**四处超支合计 code 净增 29 行**，全部登记为欠账、不默默超支。四条都不构成结构性问题：`session.py`
的落点仍是「装配 / 盯市一行 / epoch 一行 / close 一行 / 人工入口」五处——S-17 改为三条**结构**断言
（`self._repair` 出现次数 ≤6；`plan_repair(` / `AlgoStopInput` / `place_backstop_stop` 一律不得出现在
`session.py`），它比一个行数比大小更能守住「薄封装」这件真正要守的事。

> **终审重算（见 §11.10）**：终审的五处修复又加了 code，故上表在**合入时**的实测值为
> `session.py` 19、`backtest.py` **35**、`hold_horizon_text.py` **45**、`okx/trading.py` 45（预算内，
> 因为终审那张标签表拆去了新文件）、`api/ai_auto.py` 21、`backstop_repair.py` **380**，
> 另新建 `okx/error_text.py`（54 raw / 33 code，无预算，终审引入）。超支合计由 29 涨到 **71** 行，
> 逐条理由见 §11.10 表末。上表数字保留为「实现节点交付时」的快照，两份都留着是刻意的：
> 放行条件 4 要的是「不默默超支」，而不是一个被覆盖掉的历史值。

### 11.4 未闭合项：真实 `order-algo` 实测（放行条件 3 与 12）

**本节点没有交易所凭证、也没有模拟盘账户，无法发出任何真实请求**，故评审要求的四项实测
（`algoClOrdId` 的去重行为是全账户还是按标的 / `sz` 与 `closeFraction` 的实际接受情况 /
拒单 sCode / 用三段式 ID 打两发确认去重口径）**全部待办**。相应地：

1. 请求体按 §2.2.2 与随单 OCO 的 `build_attach_algo_orders` **逐字一致**组装
   （`ordType="conditional"` + `slTriggerPx` + `slOrdPx="-1"` + `slTriggerPxType="last"`），
   没有任何硬编猜测的映射——这是在「不得硬编猜测」与「必须先实测」之间唯一可走的一条；
2. **R-3 的降级路径尚未实现**：若实测发现 OKX 不按 `algoClOrdId` 去重，需按 §七 R-3 补上
   「每次尝试前强制先核实一次，核实到已有覆盖即放弃本次补挂」；
3. 因此**在完成该实测之前，不得把 `ai_trade_backstop_repair_default_mode` 改成 `arm`**，
   也不建议在生产会话里手工切 `arm`。默认 `plan` 把这条缺口的暴露面收敛为「用户显式点了一次授权
   按钮」，而人工那一发是**同步等结果**的，失败会当场看见（不是一条后台静默重试的路径）。

放行条件 8（真实会话上肉眼确认弹窗控件 / 卡片第五行 / chip / 按钮 / `trend_hold` 下点完即见状态
流转）同样需要人 + 真实账户，一并登记。

### 11.5 轴 B 开关对照回测（放行条件 6 的三个数，**实测**）

同一份合成行情（400 根 4H）、同一契约（`trend_hold`，`expectedHoldBars=18`）、同一份真实历史费率
曲线（0.02% / 8h），只翻 `ai_trade_funding_cost_backtest_enabled`：

| | `cost_bps`（G3 迟滞阈的分子） | `avgHoldSeconds` | `turnoverPerDay` | `netPnlUsd` | 资金费覆盖 |
|---|---|---|---|---|---|
| **关** | 14.0 | 224470.588 | 0.3849 | −2941.313486 | 0 / 160 轮 |
| **开** | **32.0** | 224470.588 | 0.3849 | −2941.313486 | **160 / 160 轮** |

`+18 bps` 恰为 `0.0002 × 1e4 × 9 期`（18 根 × 4H ÷ 8h = 9 期），与 §1.1 估的量级一致。
按 runbook L4 的 [P2-2] 判据：**`cost_bps` 上升且 `avgHold` 不降 ⇒ 通过**。本次三个数完全相同，是因为
这份合成行情下 G3 的迟滞阈没有卡到任何一轮（stub LLM 的平仓多发生在浮亏侧，而浮亏一律放行）——
**这恰好也是「轴 B 只改入参口径、不改判定式」的一个旁证**；`netPnl` 按 [P2-2] 只观察不判定。

### 11.6 红灯基线对账（干净 HEAD worktree **实测**，不靠记忆）

两次全量后端跑（同一台机器、同一个解释器）：

| | 工作树（本轮全部改动） | 干净 HEAD worktree（`e7fdda4`，`git worktree add --detach`） |
|---|---|---|
| 结果 | `15 failed, 3049 passed, 1 skipped` | `9 failed, 2909 passed, 2 skipped` |
| 用例总数差 | +146 | —（= 本轮新增 136 例 + 未跟踪孤儿文件 `test_factor_library_expansion.py` 的 10 例） |
| 前端全量 | `146 files / 1617 tests` **全绿** | — |

15 条红灯逐条溯源，**新增红灯为 0**：

| 红灯 | 条数 | 干净 HEAD 上是否同样红 | 溯源 |
|---|---|---|---|
| `test_ai_trade_prior_session.py` | 7 | ✅ 同样红 | HEAD 已红（上一轮已登记的既有基线） |
| `test_factor_library_expansion.py` | 7 | ➖ 该文件在 HEAD 上**不存在** | 未跟踪的孤儿测试文件（`git status` 里是 `??`），故干净检出上无从复现；与本轮无关 |
| `test_agent_ws.py::test_ws_emits_ready_history_and_full_run` | 1 | ✅ 同样红 | 全量顺序 flake（单跑绿），两侧同红 |

干净 HEAD 上另有 **1 条**工作树没有的红：`test_ai_backtest_manager.py::test_profit_rule_is_forwarded_to_spec`
（`AGENT_NOT_CONFIGURED`）。**这是 worktree 的环境差异，不是代码差异**：`.env` 是未跟踪文件、只存在于
主工作树，干净 worktree 里没有它 ⇒ `resolve_active_provider` 取不到默认模型。已在两侧各单跑一次确认
（主树 6 passed / worktree 1 failed），与本轮改动无关。

### 11.7 混合文件与外来 hunk（给提交节点）

`.env.example` 与 `services/quant/app/config.py` 在本节点开工前就已被**别的在制特性**改过
（`ai_trade_enabled` 总闸由 `False` 翻成 `True`），本轮又各自追加了自己的段落，故它们是**混合文件**：

| 文件 | 外来 hunk（须剔除） | 本轮 hunk（须暂存） |
|---|---|---|
| `.env.example` | `@@ -49,6 +49,7 @@` 新增一行 `AI_TRADE_ENABLED=true` | `@@ -175,6 +176,36 @@` 起的两段（`AI_TRADE_BACKSTOP_REPAIR_*` 与 `AI_TRADE_FUNDING_COST_BACKTEST_ENABLED`） |
| `services/quant/app/config.py` | `@@ -93,9 +93,10 @@`（注释改写 + `ai_trade_enabled: bool = False → True`） | `@@ -356,6 +357,30 @@` 起的一段 |

另有三组**完全不属于本特性**的在制改动，提交时必须整文件剔除：
`services/quant/tests/test_config.py`、`desktop/src/lib/config.js`（同属 `ai_trade_enabled` 翻转），
以及 `docs/diagnoses/*` / `.claude-index/` / `.agentmesh/` 等未跟踪目录。
沿用上一轮 R-8 的做法：混合文件按 hunk 级暂存，patch 必须以 **bytes** 读写
（Windows 上 `write_text` 会翻 CRLF 弄坏 patch）。

### 11.8 P2 取舍结论（8 条，逐条不得静默忽略）

| # | 结论 | 理由 |
|---|---|---|
| P2-1 | **采纳** | notes 如实写「覆盖 N/M 个决策轮」（实测 160/160 与 0/160，见 §11.5）。一个全局布尔说不清「曲线起点之前那几根本来就没有可见费率」这件事，而那正是「口径说一套、判定做另一套」的起点 |
| P2-2 | **采纳** | runbook L4 的判据已是「`cost_bps` 上升且 `avgHold` 不降」，`netPnl` 只观察不判定；§11.5 即按此判 |
| P2-3 | **采纳** | 审计行 `ord_id=<algoId>`、`cl_ord_id=<algoClOrdId>`（两列都是自由 String，零迁移）；B-17 逐字段断言 |
| P2-4 | **采纳** | `_default_place` 用 `get_sessionmaker()` 开短生命周期 session，绝不复用会话的长 session；docstring 写明理由 |
| P2-5 | **采纳** | 卡片保持无状态，`repairPending` 按 `instId` 键由 `AiTradeStatsLive` 传入；U-19 守三态 |
| P2-6 | **采纳「不回填」** | 这是一次**有意的选择**：`arm` 是「允许系统在你不在场时向交易所发出委托」的授权，每次重新授权比记住上次更安全。代码里写了这句理由，并配了一条用例（重开弹窗回到「仅提醒」） |
| P2-7 | **采纳** | 新增分支只读 `repair.state` / `blockedReason`，一个阈值都没复制；`turnoverSeverity` 里那个既有的 `0.999` 原样留着（是既有债，本轮不二次固化也不顺手改） |
| P2-8 | **采纳** | `aiTradeApi.repairBackstop` 的 `instId` 走 `encodeURIComponent`，与既有 `stopMember` 逐字同款 |

### 11.9 一处刻意的省略

§3.4 的文件计划里列了 `apps/web/lib/aiTradeHorizon.ts`（「profiles wire 新块的只读取用」）。
**未改动**：新块的唯一消费方是 `AiTradeHorizonFields` 里的自愈控件，而它直接读
`data.backstopRepair`（整块透传、零计算）。在 `aiTradeHorizon.ts` 里包一层 getter 不会减少任何一处
硬编码，只会多一个没有第二个调用方的间接层——那与本仓库「没有展示落点的常量即死码」的纪律同源。

### 11.10 终审（提交节点）发现与修复

> 终审把 diff 逐条对着 §零 的 4 P0 / 10 P1、§三 的文件计划、§六 的用例表与 §6.3 的 12 条放行条件
> 走了一遍。**四条 P0、十条 P1 全部真的落地**（不是只在文档里改了），F-7 等价门禁与 S-19/S-20 真端点
> 用例都在跑。以下是终审自己发现并**当场改在源码里**的五条真缺陷 + 一条本轮新造的行数债。
>
> **一条方法论**（写给下一轮）：RV-0 与 RV-2 都出在**同一个形状**上——「自动通道与人工通道共用一份
> 判定，但只有自动通道那一侧被完整实现 / 被完整测试」。§4.1 那句「只绕过 `mode`，绝不绕过
> H1/H2/H3」在纸面上是对的，代码也确实调了同一个 `plan_repair`；出事的是**两条通道之间那层状态覆盖**。
> 审「两条通道共用一份核心」的设计时，光核对「是不是同一个函数」不够，必须把**每一道锁**分别沿两条
> 通道各走一遍。

| # | 缺陷 | 为什么是真缺陷 | 修法 |
|---|---|---|---|
| **RV-0**（阻断级） | **人工通道能发出第二张全仓止损，冷却锁与次数锁一起被静默跳过。** `_settle` 在**待核实态**（`placed`，等下一次真实核实）下会把方案的 `state` 覆盖成运行时的 `placed`——那个覆盖是给**自动**路径设计的，让盯市 tick 在核实回来前不再发单。而 `repair_now` 只拒 `plan.state == blocked` ⇒ 覆盖后的 `placed` 一路走到 `_execute`。于是：连点两次「立即补挂」（或自动补挂刚发出后再点一次）会发出**两张 `reduceOnly` 全仓止损**——第二张算 `sz` 时读的 `covered_sz` 还是补挂前那份未刷新的检测层读数，故两张都是全仓 | 这是 §七 **R-2「重复止损打穿到反向」**与 **R-4「配额耗尽」**的最短触发路径，且 §4.1 明写人工通道「**只绕过 `mode`，绝不绕过 H1/H2/H3**」。更糟的是它会**反复**发生：`_execute` 每次都把 `_await_verify` 重置为 `True`，只要检测层还没回来，第 N 次点击照样放行。前端那 2.5 秒按钮禁用**不构成防线**——端点是可直接调用的，且 `state`/`blockedReason` 变化会触发一次重渲染 | `repair_now` 在读 `plan.state` **之前**先拒待核实态（`blocked(cooling)` + 「核实回来之前不补第二张」），并把 409 的原因改取**运行时**那一份（`self._blocked_reason`）——此前它取的是被覆盖前的原始判定，会与 wire 下发的 `blockedReason` 各说一套（S-13 要求两者同值）。`schedule` 的发单条件同步补一条显式 `not self._await_verify`：那条不变式此前只由「状态覆盖」间接兜住，而这次事故正是间接兜底在另一条通道上漏掉。两条用例双向守（连点两次 / 自动刚发出后再点），修复前后者必红 |
| **RV-1** | **补挂失败码没有中文标签，四处呈现全印裸英文码。** §2.2.7 的 prompt 表写的是「⚠ 系统尝试补挂止损失败（{sCode **中文标签**}）」、§2.4.2 写的是「把交易所原始 sCode 的**中文标签**显示在 `detail` 里，**不显示裸码**」、S-11 写的是「`message` 含中文标签」——三处都要求中文，而实现在 prompt、卡片、WS `error`、端点 502 四处印的都是 `_map_trade_code` 的归一码本身（`OKX_ERROR` / `ORDER_SIZE_NOT_MULTIPLE` / `ACCT_MODE_MISMATCH`…）。而这些码的中文**早就存在**——就在 `okx/trading.py:69-80` 的行尾注释里 | 这是「后端算了但用户看不见」的纯粹形态，且是**本仓库刚栽过一次**的那一格（上一轮 U-8~14 那组用例就是为「后端加了新码、前端显示原始英文」立的，但只守了 `blockedReason`，没守失败码）。更要命的是命中面：`plan` 是**默认模式** ⇒ 人工那一发的失败提示往往是用户对本层唯一的一次阅读 | 新建 `okx/error_text.py`（27 条码 → 中文，**唯一真源**），`_note_failure` 把它**预渲染**进 `lastResult.codeLabel`，prompt 与卡片只读那一个键、端点 502 的 message 改为「中文在前 + 交易所原文在后」。**刻意不在呈现层各抄一张表**：码域归 `okx.trading` 所有、会随交易所错误码演进，抄一份就等于埋下下一次漂移（这与 `blockedReason` 那份四处镜像的表不同——后者的值域是自愈层**自己**的）。adjacency 换成一条 **AST 门禁**：按 AST 读出 `_map_trade_code` 映射表里的每一个产物码，断言 `error_text` 都有中文，漏一个即红 |
| **RV-2** | **`px_through` 的 WS `error` 事件整条没实现（S-12 无声消失）。** §2.4.3 明写 WS `error` 只在两种情况下推：`failed` 与 **`blocked(px_through)` 由假翻真的那一刻**；§2.2.3 更写明此刻正确的动作是「**高声报告**（WS error + prompt 明说）」。实现只推了 `failed` 那一条，`px_through` 只落一行卡片 | 这一格是本轮最危险的状态：**持仓裸露 + 意向止损已被越过 + 系统刻意不补**。把它藏进一个用户不会盯着看的卡片行，等于建了一个「知道最坏情况却不出声」的安全层——而这正是本轮 §1.1 缺口 #1 要根除的那种形态的镜像 | `RepairRuntime.schedule` 在「状态变化」判定之后新增 `_alert_px_through`：`push` 一条 `code=AI_TRADE_BACKSTOP_PX_THROUGH` 的 `error`（带那个被越过的止损价）。节流**天然复用既有的「变化才推」**——连续多个 tick 仍为真不会重复推，而「越过 → 换成别的暂缓原因 → 再越过」会再报一次（两条用例双向守） |
| **RV-3** | **回测口径句把「曲线在手但不覆盖本区间」误报成「历史资金费曲线不可得」。** `_cost_basis_note` 的回退原因只有两支（总闸关 / 曲线不可得），而第三种情况真实存在且 [P2-1] 正是为它而立：曲线拿到了、`available is True`，但本次区间的决策轮**全落在曲线起点之前** ⇒ `covered == 0` ⇒ 落进「曲线不可得」那支 | 一条**指错方向**的免责比没有免责更贵：它会让人先去查公共端点、再去怀疑总闸，而真相是「这段行情比费率曲线更早」。这与 [P1-6] 删掉那句假欠账是同一条纪律 | 原因分支补第三支「本次区间没有任何决策轮读到可见的历史结算点」；新增一条用例，喂一条**全部结算点都在区间之后**的曲线，断言 `available is True` 且 notes 说的是第三支、**不含**「曲线不可得」 |
| **RV-4** | **端点在「运维总闸关」时回 409 而不是 §4.1 写的 404**，且这处偏离没有记在 §十一 | 偏离本身**是对的**（404 的语义是「分组 / 成员不存在」，用它表达「本层未装配」会让前端把一次配置状态误读成会话已消失），但 spec §Constraints 明写「发现方案有误不得静默偏离，一律先记再改」。一处未登记的正确偏离，在下一轮会变成一条「文档和代码不一致」的悬案 | 不改代码，**补登记**（本行即登记）：`not_applicable` 是 I-8 新增的值域码，端点据它回 409 + `blockedReason`，与「被三道锁拦下」共用同一条错误体形状；`404` 保留给分组 / 成员真的不存在 |

**本轮新造的一条行数债（终审发现，实现节点的 §11.3 没有追）**：`okx/trading.py` 由 HEAD 的
**765** 行涨到 **844** 行，**越过了 CLAUDE.md 的 800 行红线**。§11.3 只对着 spec 的「+48 行」预算打了
✅，没有对绝对行数——而 CLAUDE.md 的 800 行是项目级硬规则，且 §十 的非目标里只豁免了
`session.py` / `backtest.py` 两个既有债，`trading.py` 在本轮之前是干净的。终审的处置：

- RV-1 的标签表**没有**并进 `trading.py`（那会到 889 行），而是独立成 `okx/error_text.py`——
  adjacency 的损失由上面那条 AST 门禁补回，比「表挨着放」更硬。副作用是 `trading.py` 的 code 净增
  也回到了预算内（45 / 48）；
- 残余的 79 raw 行（`place_backstop_stop` + `_backstop_audit_values` + 一个语义码集合）**登记为欠账，
  本轮不拆**。推荐的拆法是把 `_map_trade_code` + 三个 `*_REJECT_CODES` 集合一并迁进
  `okx/error_text.py`（约 50 行，可让 `trading.py` 回到 ~790）；不在提交节点做，是因为那条错误归一
  路径由 `live_auto` 与 AI 两条链**共用**且直通真实资金，一次机械搬迁值得有自己的改动与自己的全量
  回归，而不是一次 commit 前的顺手编辑。**下一轮碰 `okx/trading.py` 的人请先还这笔。**

**放行条件 4 的最终对账**（实测，不是估计）：四处超支——`session.py` +1、`backtest.py` +5、
`hold_horizon_text.py` +15、`backstop_repair.py` **+50**（330 预算 / 380 实测），合计 **71** code 行，
外加 `.env.example` 的纯注释超支。`okx/trading.py` 因标签表拆走而回到预算内（45 / 48 ✅）。
全部登记，**无一处默默超支**；`backstop_repair.py` 657 raw 行仍在 800 红线内。
`backstop_repair.py` 超支的一半来自终审：RV-0 / RV-1 / RV-2 三条修复各带一段解释「为什么这一格必须
这么写」的注释——那些注释不计入 code，真正加的是三处判据与一张失败码转发。不拆分的理由不变：
这一层的纯核心与运行时是同一条不变式的两半（「永不建立敞口」既要在判定里成立、也要在下单现场成立），
拆开会让下一个人只读到一半。

**终审自己的验证（放行条件 1 / 9，实测）**：

| | 结果 | 对账 |
|---|---|---|
| 后端全量 | `15 failed, 3060 passed, 1 skipped` | **新增红灯 0**。15 条逐条即 §11.6 那三类：`test_ai_trade_prior_session` 7（HEAD 已红）、`test_factor_library_expansion` 7（未跟踪孤儿文件，干净检出上不存在）、`test_agent_ws::test_ws_emits_ready_history_and_full_run` 1（全量顺序 flake）。用例总数 3049 → **3060**（+11 = 终审新增的守门用例），与 §11.6 的口径连续 |
| 本轮新增用例 | 后端 **147**（T 59 / B 30 / S 39 / F 19）+ 前端 **42** | 实现节点交付时是 136 + 41；终审的五处修复各带回归用例 |
| 前端全量 | `146 files / 1618 tests` **全绿** | 1617 → 1618（+1 = U-6b） |
| 一条踩到的运行纪律 | 全量 pytest **不能与另一次 pytest 并存** | 共用一份临时库 ⇒ 第二次会在 7% 附近静默挂住（不报错、只是不动）。更阴的是**上一次跑完后进程可能不退出**（summary 已写、解释器仍在），于是「上一次已经完成」不等于「锁已释放」：起新一轮前必须确认没有残留的 pytest 进程 |
| `ruff` | 本轮全部改动文件干净 | 仓库另有 56 条**既有**告警，全在 `app/strategies/*` 与若干老用例里，与本轮无关、不顺手改 |
| `tsc` | 仅剩既有 `WeightBatchArena.test.tsx` | HEAD 已红 |

**放行条件 3 / 8 / 12 仍未闭合**（真实 `order-algo` 实测 + 真实会话肉眼验收），故
`ai_trade_backstop_repair_default_mode` 保持 `plan`，§11.4 的三条约束继续有效。RV-0 使这条约束的
分量又重了一点：`arm` 模式下自动与人工两条通道**会同时活着**，而它们之间的那层状态覆盖刚出过一次
事故——在真实实测与肉眼验收完成之前，不建议在生产会话里手工切 `arm`。

---

## 十二、评审结论

### 判定：**有条件通过**

本设计的问题定位是准确的，两条缺口都经得起逐行核对：轴 A 补的确实是「检测建成了、执行体没有」
这半程，轴 B 修的确实是「回测成本估算里资金费恒为 0」这条会污染验收路径的偏差。方案的骨架
——只补止损腿、只补差额、只减不增、默认 `plan`、`placed ≠ verified`——在方向上全部正确，且与前三轮
积累的纪律一脉相承。**v1 的 40 条依赖核实逐条重跑，全部属实、无一条行号漂移**，这是本仓库迄今
第一份零漂移的依赖表，值得记一笔。

但四条 P0 都不在那 40 条里，而在**它没有追的那几跳**上：

- **P0-1**：ID 的唯一性只在单会话内成立，多标的（本产品的默认形态）下必然碰撞；
- **P0-2**：本轮自称「最关键」的那条安全判据落在一个不可达分支上；
- **P0-3**：`response_model` 会把新块整块吃掉，且请求侧的回填链找错了文件；
- **P0-4**：张数的两个真源指向两个不同的仓，一端会替用户平掉他自己的仓。

四条的形状是同一个：**判定式本身写对了，但它读的那个入参、或它所在的那条链路，并不携带它以为
携带的信息。** 这与第 2、3 轮评审的 P0 群完全同源。方法论上的结论也一样，且这次更具体：
**依赖表只能守住作者已经想到的那些跳；评审必须自己把数据从产生到上屏、把请求从表单到落单，
各走一遍。** 本轮四条 P0 全部出自评审补查的那 6 条依赖（现已并入 §1.4 的 #41–#51）。

全部 4 条 P0 与 10 条 P1 已在正文改毕并各自带上了守门用例。以下 12 条为**放行条件**，
实现节点必须逐条落实并在 §十一 留下可核对的记录：

1. **[P0-1]** `algoClOrdId` 采用三段式（会话身份 + epoch + attempt），T-31b 断言同 `(epoch, attempt)`
   下换成员 / 换会话即不同；长度 ≤32 且全字母数字。
2. **[P0-2]** §2.2.1 判据 c 收下 `coverageRatio is None`，T-5b 守 `blocked(coverage_unknown)` 可达、
   T-5c 守 `closeFraction=="1"` 的随单 OCO 形态**不被误伤**（仍走 `idle`）。
3. **[P0-3]** 契约链路三处一次补齐（`AiTradeHorizonProfilesOut` 字段 / `HorizonRule` 字段 +
   `resolve_horizon_rule` 回填 / `rule_wire` 镜像），并由 **S-19 / S-20 两条真端点用例**守——
   手造 wire 的前端用例不构成对这条链路的验收。
4. **[P0-4]** `sz = snap_size(max(0, min(abs_pos, self_position_sz) − covered_sz))`，
   `place_backstop_stop` 在拿到实时 `abs_pos` 后**重算**，H2 对可收敛的越界**收敛而非抛**；
   T-7b + B-19b 双向守。
5. **[P1-1]** H2 不再以 `live_max_order_notional_usd` 拒单；B-10 改判为「5000 USDT 的累积仓必须
   通过」，并在实现里逐字引用 `okx/trading.py:224-225` 的既有豁免。
6. **[P1-2]** H1 新增 `px_stale`（复用 `ai_trade_profit_guard_max_px_age_s`），
   优先级排在 `px_through` **之前**；`px_age_s` 由 `session._last_px_wall` 派生并经形参传入，
   本层不得另找时钟；值域在后端常量 / prompt / 前端标签 / 端点错误体四处同步。
7. **[P1-3]** 状态或 `blockedReason` 变化即补推一次 `status`；S-21 断言「恰好多推一条」与
   「状态不变则不重复推」；`session.py` 行数预算按 18 对账。
8. **[P1-4]** `horizon_rule is None` ⇒ 整层不装配（T-1b + S-22）。**这条是本轮的安全底线**：
   不允许存在一条用户完全看不见的自动下单路径。
9. **[P1-6]** 资金费口径句落 `backtest._funding_notes()`，**并同时删除** `backtest.py:1094-1095`
   那句已经变成假话的欠账披露；F-12/13 含反向冻结断言，F-13b 守对照臂也有披露。
10. **[P1-7]/[P1-8]/[P1-10]** 前端三条：severity 与 chip 同一处改（U-15b 含「既有四格文案逐字
    不变」）；`AiTradeLaunchModal` 的新 state 塞进既有 `[open]` effect 并由 U-21 守复发；
    控件不受跨日条件门控（U-20b）。
11. **[P1-9]** F-10/11 必须显式把 `k_min` 调正后再断言；§2.3.3 与 runbook L4 已如实标注 edge 门禁
    默认空转，实现时不得再把它当作「本轮会变严的两条之一」宣传。
12. **原放行条件 3 的地位不变且更重要**：`order-algo` 的一次真实实测（`algoClOrdId` 去重行为 /
    `sz` 与 `closeFraction` 的接受情况 / 拒单 sCode）**必须先于** `build_algo_stop_payload` 定稿。
    评审额外要求：实测时**用三段式 ID 打两发**，确认 OKX 的去重是按 `algoClOrdId` 全账户去重
    还是按标的去重——这个实测结果直接决定 P0-1 的修法是否足够（若 OKX 根本不去重，按 R-3 降级为
    「每次尝试前强制先核实」）。

### 不作为放行条件、但必须回答的两个问题（写进 §十一）

- **[P2-6]** 自愈模式要不要进前端偏好回填链？评审倾向**不回填**（每次重新授权更安全），
  但这必须是一次写下来的选择，而不是一个遗漏。
- **[P2-1]** 曲线起点之前的若干根决策轮不含资金费——采用「覆盖 N/M 轮」的如实计数，
  还是接受一个全局布尔？若接受布尔，请说明为什么这不构成新的「口径说一套、判定做另一套」。

### 评审没有改、且认为 v1 判断正确的地方（供实现节点放心沿用）

- **默认 `plan` 而非 `arm`**（D-1）：本仓库第一条 AI 侧自动写路径，「默认给方案 + 一键执行」
  是恰当的第三条路，既避开「总闸默认关 ⇒ 整个特性空转」，也避开「默认自动花钱」。
- **`coveredSz` 不可知时不补**（D-2）：与 G6 的 fail-open 方向相反是对的——两层的错误代价相反。
- **`placed → verified` 两个状态**（D-6）：这条纪律必须适用于系统自己刚做的事，v1 写得很好。
- **不撤单、不改价、不补止盈腿**（D-3/D-4）：「只补差额是唯一一种无论判断对错都不会让事情变糟
  的操作」——这句话是本文档最好的一句，请在 `backstop_repair.py` 的 docstring 里保留它。
- **轴 B 曲线不可用时回退 `(None, False)` 而非 `(None, True)`**（D-8）：完全正确，
  `(None, True)` 会让一次网络抖动静默改变回测判定。评审逐行核对了 `cost_model.py:209-234` 的
  两条分支语义，与 v1 的描述逐字相符。
- **`fundingTs` 的单位**：评审专门核对了 `rest_client.py:330`（`fundingTime // 1000`），
  曲线点与回测 `ts` **同为秒**，`interval_hint` 也是秒——轴 B 不存在量纲失配。这是本特性链
  踩过两次的坑（第 1 轮 ATR 跨尺度、第 2 轮资金费结算周期），这次是干净的。
