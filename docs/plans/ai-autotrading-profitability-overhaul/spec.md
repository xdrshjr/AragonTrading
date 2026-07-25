# AI 自动交易盈利能力改进计划（文档集）· 开发方案 spec

- **Feature**: `ai-autotrading-profitability-overhaul`
- **版本**: **v2（已评审并就地修复；评审结论见文末）**
- **产出物**: `docs/improve/01-ai-autotrading/` 下 9 个中文 Markdown 文档（≤10 个，单篇约 2000 字）
- **本节点边界**: 只产出本设计文档；不写实现代码、不改 `docs/plans/ai-autotrading-profitability-overhaul/` 以外的任何源文件、不执行 `git commit`

---

## 评审记录（Review Notes · v1 → v2）

评审方式：逐节复核 v1，并对第二节 15 条根因的**每一条 `file:line` 证据回读源码验证**（含
`config.py` / `ai_auto/*` / `live_auto/*` / `okx/*` / `engine/factors/*` / `requirements.txt`）。
维度：可行性 / 完备性 / 一致性（对 `CLAUDE.md`）/ 规模适配。下列问题**已在 v2 正文就地修复**，
修复位置写在每条末尾。

### P0（阻断级：会污染整份文档集的核心论点）

| 编号 | 问题 | 证据 | 修复 |
|---|---|---|---|
| **P0-1** | **核心成本论点与代码事实不符，且漏掉了最强的一条根因。** v1 §一断言"**全额市价成本**"，D-1 以 `config.py:280-281` 的 `default_taker_fee_bps=5.0`/`default_slippage_bps=2.0` 作为**实盘**往返成本、并称 `_do_open` "默认市价"。三处均不成立 | ① `ai_trade_limit_orders_enabled` **默认 True**（`config.py:163`），`_do_open` 是 `ord_type="market" if limit_px is None else "limit"`（`executor.py:260-263`），maker 路径早已上线且默认开；② 这两个 bps 常量**只被 `SimBroker`/回测/纸面路径消费**（`engine/simulator.py:103-109`、`ai_auto/backtest.py:248-249`、`paper/session.py:60-61`），`LiveExecutor` 全链路**从不引用**；③ 实盘每条成交记录硬编码 `"fee": 0.0`（`executor.py:779`），`_close_pnl` 是纯价差×数量（`executor.py:732-738`）——**实盘已实现盈亏是毛口径** | 重写 §一第二段与 D-1；新增 **D-16（实盘 PnL 零费率入账）**并置于贡献度首位；§二结论重排；§四把 01 号文档的首要任务从"maker-first 改造"改为"净额 PnL 口径修复"；§十新增"费率入账完整性"指标 |

### P1（必须修复：不修则下游文档会写出不可实现或自相矛盾的内容）

| 编号 | 问题 | 证据 | 修复 |
|---|---|---|---|
| **P1-1** | 元标签模型"复用 `engine/factors/tree_ensemble.py`"不可行 | 该模块只导出 `fit_rf_importances` / `fit_gbdt_importances` / `fit_extra_trees_importances`（`tree_ensemble.py:234/270/305`），返回**特征重要度向量**；无分类器、无 `predict_proba`、无模型持久化；且 **scikit-learn 不在 `services/quant/requirements.txt`** 内 | §3.3(02) 改为：元模型 = **新增纯 numpy 模块**，复用 `_build_tree`/`_predict_tree` 分裂原语（`tree_ensemble.py:196/220`）与 `BUILTIN_SEED`；明确"不得假定 sklearn 可用"；给出退化到校准 logistic 的兜底 |
| **P1-2** | `SignalPrior{strategyId,…}` 无法由 `engine/signal_edge.py` 产出 | 该模块只覆盖 **8 个内置信号检测器**（`signal_edge.py:50-107`、`available_signals():306`），键是 `signal` 名而非 `strategyId`；`forward_returns` 的入参是信号名 | §3.2 主线 B 改为**两路来源**：内置信号沿用 `signal_edge`；策略级先验由既有 `run_backtest`/`SimBroker` 按 (instId,bar,regime) **离线重放**产出。并注明 `_summarize_horizon` 已内建 `cost_bps`（`signal_edge.py:241`），先验必须记录其成本口径以免与 01 号双重计费 |
| **P1-3** | 提案接口签名违反 `CLAUDE.md` 硬阈值，而 §5.2 又要求"逐字一致"写进文档作为单一真源 | `estimate_round_trip_cost(...)` 6 形参、`resolve_size_fraction(...)` 7 形参，均 > `CLAUDE.md`「函数形参 ≤5」；且 07 提案的落点 `ai_auto/session.py` 现为 **1404 行**，已是 ≤800 行红线的 1.75 倍 | §5.2 全部签名改为聚合入参（`CostQuery` / `SizingInputs` 数据类），≤5 形参；新增 §5.4「落地位置硬约束」：新机制一律进**新模块**，禁止继续增肥 `session.py` |
| **P1-4** | D-10「结构性催交易推力」把 `_format_hold_streak` 当证据，与代码事实相反 | `prompts.py:374-386` 的 docstring 明写"措辞必须**对称**……防止注记本身变成过度交易的推手"，正文亦含"这也不是必须开仓的指令" | D-10 改写：删除该证据，改用**可验证的频率证据**——墙钟 `ai_trade_wake_interval_default_min=2.0`（`config.py:184`）与每根确认 K 线**双触发**叠加 |
| **P1-5** | D-14「回测不可外推」漏掉最致命的一条 | AI 回测硬编码 `allow_limit=False`（`backtest.py:232/240`）、`reflection_enabled` 默认 `False`（`backtest.py:130`），而实盘两个总闸默认均为 **True**（`config.py:163/135`）——**回测与实盘的决策链结构性不同构**，比"每单必成"严重得多 | D-14 补该证据；§3.3(06) 增加"闸门前必须先做**链路同构性对账**"的硬前置 |
| **P1-6** | 资金费率（D-2）的落地路径缺失，与"零依赖新增"表述冲突 | `okx/rest_client.py` 只有 `/api/v5/public/instruments`（`rest_client.py:227-236`），无 funding 端点；`fundingCarryBps` 需新增公共端点 + 轮询 + 缓存 | §3.3(01) 补：新增 `GET /api/v5/public/funding-rate(-history)` 只读调用、缓存 TTL、**取数失败即整项缺席降级**（沿用既有"键缺失即整段省略"范式）；§四澄清"零依赖新增"仅指第三方 Python 包，不含新 OKX 只读端点 |
| **P1-7** | §7.1 验收条款自相矛盾，下游按此自查会得到互斥结论 | "≥1800 且 ≤2600（按中文字符计，00 号可到 2400）"——"00 号可到 2400"比统一上限 2600 更低；§四表格又写 00 号 ~2200；"总量 ≥17000"与下限 9×1800=16200 不自洽 | §7.1 改为单一口径：每篇 1800~2600，00 号目标 2200；总量 ≥17000 且 ≤23000 |
| **P1-8** | 最小 edge 门禁**未规定在既有门禁链中的插入位置**，而本仓库该顺序是硬约束 | 现链路：铁律（`session.py:571-572`）→ 利润保护冷却（`:574`）→ 共识（`:575`）→ 反思（`:578-579`）→ 执行（`:580`）；`session.py:568-570` 的注释已把"落在 decide 之后、反思之前"确立为先例 | §3.2 主线 A 明确规定：edge 门禁插在**共识门禁之后、反思之前**，并给出理由（被拦轮此时已是 hold，反思对 hold 直通 ⇒ 自动零 LLM 成本） |

### P2（建议修复，不阻断；v2 已一并处理）

| 编号 | 问题 | 修复 |
|---|---|---|
| P2-1 | D-3 只引 `ws_client.py:183-205`，未指出 AI 会话的实际取数点 | 补 `ai_auto/session.py:275`（AI 会话构造的正是 `market_channels=False` 的 candle-only 包装器） |
| P2-2 | §3.3(06) 称回测免责段为"五条"，实为六条 | 改为"六条（①~⑥）"，`backtest.py:91-102` |
| P2-3 | D-15 称 `_compute_ai_stats` "只有动作计数/holdRate/平均置信度/降级数" | 补：还有四个反思计数（`session.py:1400-1403`），但仍无任何显著性/净期望口径 |
| P2-4 | 主线 C 的 "purged/embargo walk-forward" 被读作复用 | 明确：`plan_walk_forward_windows` 已有，**purge/embargo 全仓库零实现**，属新增 |
| P2-5 | 影子 broker 已按 5/2 bps 计费（`signals.py:30-31,85-86`），先验若再计一次成本会双重计费 | 在主线 B 与 §3.3(02) 各加一句成本口径去重要求 |
| P2-6 | §7.3 说可用一次性脚本自查但未给判据与编码约束 | §7.3 补：UTF-8 无 BOM / LF、`Path.write_text(..., encoding="utf-8")`、校验脚本用完即删不入库 |

---

## 一、概述（Overview）

本项目的【AI 自动交易】已经是一条完整、工程质量相当高的链路：多策略影子 `SimBroker` 回放出持久立场
→ 信号板 + 市场体制 + 多周期背景 + 技术因子摘要装配成决策 payload → `AiTradeDecider` 用受约束的
单工具（`submit_trade_decision`）产出决策 → 依次经【基础策略 MA 通道铁律】→【利润保护冷却】→
【共识门禁】→【下单前二次反思】四层确定性门禁 → `LiveExecutor` 守护执行并附交易所侧 OCO。围绕它还有
利润保护棘轮、限价委托状态机、认知轨迹回放、统计看板与 AI 回测。也就是说：**问题不在"工程没做完"，
而在"这条链路优化的目标函数不是净收益"**。当前所有确定性层（铁律 / 反思 / 保护）解决的都是"少犯错"，
没有任何一层回答"这笔交易的期望收益是否覆盖交易成本"。

用户反馈的现象是"实盘交易总是亏损"。按量化交易的基本恒等式，单笔净期望
`E = p·W − (1−p)·L − C`（p 胜率、W 平均盈利、L 平均亏损、C 往返交易成本）。**本仓库的问题恰恰出在
等式最右边那一项：C 在实盘链路里既没有被扣，也没有被看见。**

三条经回读源码确认的事实（v2 评审修正，替换 v1 的"全额市价成本"论断）：

1. **实盘已实现盈亏是毛口径。** `LiveExecutor._close_pnl` 是纯价差 × 数量
   （`services/quant/app/live_auto/executor.py:732-738`），每条成交记录硬编码 `"fee": 0.0`
   （`executor.py:779`）。会话累计盈亏 `session_pnl_usd`（`executor.py:147-149`）由它累加，而会话级
   止盈止损（`session.py:898-907`）、利润保护棘轮、统计看板与 AI 决策上下文里的"会话盈亏"全部读这个值。
   **每一笔往返都被系统报得比真实结果好约一个往返成本**，模型因此在一个系统性乐观的反馈里学习与决策。
2. **`default_taker_fee_bps=5.0` / `default_slippage_bps=2.0`（`config.py:280-281`）只服务于模拟。**
   它们的全部消费方是 `SimBroker`（`engine/simulator.py:103-109`）、AI 回测（`ai_auto/backtest.py:248-249`）、
   纸面与影子路径（`paper/session.py:60-61`、`signals.py:30-31`）。`LiveExecutor` 从不引用它们。
   也就是说 `C ≈ 14 bps` 是**回测世界的成本**；实盘世界的 C 既没进 PnL，也没进任何决策条件。
3. **maker 路径早已上线且默认开启，不是待建能力。** `ai_trade_limit_orders_enabled` 默认 `True`
   （`config.py:163`），`_do_open` 按决策是否携带限价发出 `limit` 或 `market`
   （`executor.py:260-263`），背后有完整的挂单状态机（`live_auto/resting.py`）与 `ai_trade_limit_order_ttl_s=300`。
   真正缺的不是限价能力，而是**"何时该挂、挂多远、不成交怎么办"的策略与其成本记账**——仓库里
   连一个 `maker_fee_bps` 常量都没有，limit 成交与 market 成交在成本口径上无从区分。

在此基础上，频率侧的推力是可验证的：默认 2 分钟一次墙钟唤醒
（`ai_trade_wake_interval_default_min=2.0`，`config.py:184`）与每根确认 K 线唤醒**双触发**叠加，
把决策轮次推高。**高频率 × 未验证 edge × 从不入账的成本 = 结构性负期望**，这就是"总是亏损"
最可能的主因，而不是某个 bug。

因此本改进计划的定位不是"再加一层保护"，而是把 AI 自动交易从**叙事驱动（narrative-driven）**改造成
**期望值驱动（EV-driven）**：给每一笔候选交易一个可证伪的期望收益估计，让成本、胜率先验、波动率、
体制条件、组合相关性都进入同一个决策不等式；同时把仓库里已有但与 AI 决策完全割裂的资产（因子层
IC/RankIC 评估、权重模型、组合因子、非线性训练器、`signal_edge` 事件研究、walk-forward 回测）接进来，
形成"离线学统计先验 → 在线做条件判断 → 事后做归因与再校准"的闭环。本 spec 规定这套改进计划以
**9 篇中文文档**交付，每篇约 2000 字，落在 `docs/improve/01-ai-autotrading/`，写法对齐顶级量化机构的
内部改造方案：每条建议都必须锚定本仓库的真实代码路径、给出可度量的验收指标与回滚条件。

---

## 二、现状诊断（代码级根因清单）

以下 18 条是撰写文档集的**事实底座**（v1 为 15 条，v2 评审后修正 5 条、新增 3 条），
每条都在代码中可验证；下游文档必须引用这些路径，不得凭空发挥。
**编号一经发布即冻结**：修正只改描述与证据，不重排编号，D-16~D-18 一律追加在尾部。

| 编号 | 根因 | 代码证据 | 影响 |
|---|---|---|---|
| D-1 | 交易成本**不进入任何决策条件**：`estimate` 侧无成本模型，prompt/门禁/仓位全链路无成本项 | 全仓库无 `maker_fee_bps` 常量；`config.py:280-281` 的费率/滑点仅供模拟消费（见 D-16） | 小幅 edge 被成本吃光，模型无从判断"值不值得做" |
| D-2 | 永续**资金费率**完全未建模，且**无取数通道** | `okx/rest_client.py:227-236` 只有 `/api/v5/public/instruments`，无 funding-rate 端点 | 持仓过结算点的隐性成本缺口；跨结算的持有决策无依据 |
| D-3 | 数据维度只有 OHLCV | AI 会话构造的是 candle-only 包装器 `OKXWSClient`（`ai_auto/session.py:275`），该包装器写死 `market_channels=False`（`ws_client.py:194-205`）；而 books5/trades/tickers 通道**已实现**（`ws_client.py:125-127,168-179`） | 无盘口不平衡 / 主动成交流 / OI 等微观结构证据 |
| D-4 | 因子层与 AI 决策层割裂 | `market_digest.py` 模块 docstring 明写"**不接入**重型 `engine/factors/*`" | IC/RankIC、权重模型、组合因子成果零复用 |
| D-5 | 信号板无质量先验、共识为等权投票 | `signals.StrategySignalView`（`signals.py:34-45`）、`compute_consensus`（`signals.py:59-80`） | 已失效策略与高 IC 策略等权 |
| D-6 | 仓位=固定占比，风控=固定百分比 | `_compute_sz = strength × budget × leverage`（`executor.py:691-703`）；`ai_trade_default_sl_pct=0.05 / tp_pct=0.10` | 不随波动率自适应，高波动被扫损、低波动止损过宽 |
| D-7 | 置信度未校准、门槛形同虚设 | `ai_trade_min_confidence` 默认 `0.0`（`config.py:128`）；`aiStats.avgConfidence` 从不与实际胜率对照（`session.py:1394-1403`） | 置信度不可用于仓位/门禁 |
| D-8 | 决策 prompt 无 EV 目标函数 | `build_ai_trade_system_prompt`（`prompts.py:99-127`）纪律 #3 仅"保守 ≤0.5" | 模型无从判断"值不值得做" |
| D-9 | 反思层在两个极端之间摆动 | `build_reflection_system_prompt`（`prompts.py:531-550`）：从"任何不放心即 abort"改为"默认 confirm" | 缺统计阈值，只能靠措辞调温 |
| D-10 | 决策频率由**双触发**结构性推高，且无 EV 条件抑制 | 墙钟 `ai_trade_wake_interval_default_min=2.0`（`config.py:184`）与每根确认 K 线唤醒叠加；下限仅 `ai_trade_wake_interval_floor_s=30.0`（`config.py:185`） | 无 edge 时被动过度交易（**v2 更正**：v1 曾把 `_format_hold_streak` 列为催交易推力，但 `prompts.py:374-386` 的措辞已刻意对称、正文含"这也不是必须开仓的指令"，不构成证据） |
| D-11 | 铁律层是位置约束而非胜率约束 | `base_rules.classify_zone`（`base_rules.py:156-166`）三锚区 {0,0.5,1} ± `tolerance`，默认 0.15（`config.py:207`，上界 0.25） | 默认容差下 %B∈[0,1] 的合规覆盖率为 `(0.15+0.30+0.15)/1.0 = 60%`（仅 0.15~0.35 与 0.65~0.85 两段被排除），过滤力弱且与胜率无关 |
| D-12 | 多标的按预算切分，无相关性约束 | `manager._preflight_group`（`manager.py:287-305`）按 `allocation_pct` 分预算 | BTC/ETH/SOL 名义分散、实际同向叠加 |
| D-13 | 无回撤刹车 / 连亏熔断 | 仅会话级绝对金额 TP/SL（`session.py:898-907`） | 亏损可持续到预算耗尽 |
| D-14 | 回测与实盘**决策链不同构**，回测结论不可外推 | ① 回测硬编码 `allow_limit=False`（`backtest.py:232,240`）而实盘 `ai_trade_limit_orders_enabled` 默认 True（`config.py:163`）；② 回测 `reflection_enabled` 默认 False（`backtest.py:130`）而实盘 `ai_trade_reflection_enabled` 默认 True（`config.py:135`）；③ `SimBroker` 每单必成、无部分成交，六条已知偏差见 `backtest.py:91-102` | 上线前无有效闸门；回测跑的根本不是实盘那条链 |
| D-15 | 统计口径缺显著性与净期望 | `_compute_ai_stats`（`session.py:1357-1404`）只有动作计数 / holdRate / 平均置信度 / 降级数 + 四个反思计数（`session.py:1400-1403`），**无净期望、无 t 值、无成本占比** | 无法判断"亏损是运气还是负期望" |
| **D-16** | **实盘已实现盈亏零费率入账（v2 新增，贡献度第一）** | `_close_pnl` 为纯价差 × 数量（`executor.py:732-738`）；`_make_trade` 硬编码 `"fee": 0.0`（`executor.py:779`）；`session_pnl_usd = realized + unrealized`（`executor.py:147-149`）由其累加 | 会话止盈止损（`session.py:898-907`）、利润保护棘轮、统计看板、AI 决策上下文中的"会话盈亏"**全部系统性乐观约一个往返成本**；亏损被低估，保护层被过早/过晚触发 |
| **D-17** | **限价成交与市价成交在成本口径上不可区分（v2 新增）** | 全仓库无 `maker_fee_bps` 设置项；`resting.py` 状态机只管成交与否，不产出费率归属；`explain.meta`（`session.py:248-261`）不落 `ordType`/实测滑点 | maker-first 的收益无法度量，"限价更省"只能是断言而非结论；未成交机会成本亦无观测 |
| **D-18** | **实测滑点从未被观测（v2 新增）** | 成交记录只有 `price`（`executor.py:778`），无 `expectedFillPx` 与其差值；`default_slippage_bps=2.0` 是模拟侧的**假设值**且不作用于实盘 | 成本模型无法被实盘数据校准；成本敏感性分析缺输入 |

**诊断结论（写入 00 号文档的核心论点，v2 重排）**：亏损的主因按贡献度排序为
`D-16/D-1/D-17/D-18/D-2 成本与其记账` > `D-5/D-4 信号质量` > `D-6 仓位与止损口径` >
`D-10/D-8 交易频率与目标函数` > `D-12/D-13 组合与刹车` > `D-7/D-9 校准` > `D-14/D-15 评估闭环`。
文档集的编号顺序即按此贡献度排列——**D-16 是全集第一优先级**：在盈亏口径本身有偏的前提下，
任何基于历史成交做的先验估计、参数调优与保护层标定都建立在被污染的标签上。

---

## 三、技术设计（文档集架构）

### 3.1 总体结构

本次交付物是**文档集**而非源码；其"架构"即文档之间的论证依赖关系：

```
00 总纲与现状诊断（问题定义 + 归因框架 + 路线图 + 验收指标）
   ├─ 01 交易成本与执行质量      →  确定 C（成本），给出"最小可交易 edge"阈值
   ├─ 02 信号层重构与元标签      →  确定 p（胜率先验），把信号板概率化
   ├─ 03 因子层接入 AI 决策      →  给 p 提供正交、可评估的证据源（复用 engine/factors）
   ├─ 04 决策智能体改造          →  把 01/02/03 的产物组织成 EV 不等式，改 prompt/schema/门禁
   ├─ 05 风险与仓位管理          →  确定 W/L 与敞口（波动率目标化、自适应 SL/TP、组合风险预算）
   ├─ 06 离线评估与上线闸门      →  在上线前证伪 01~05（walk-forward / 决策重放 / 成本敏感性）
   ├─ 07 实盘可观测与归因        →  上线后度量偏差、再校准，形成闭环
   └─ 08 实施路线图与里程碑      →  分三阶段落地，每阶段带验收与回滚
```

每篇文档统一采用**同一套论证骨架**（下游写作时逐节对齐，不得自由发挥结构）：

1. `## 一、问题定位`：引用第二节 D-x 编号 + 代码路径，说明现状为何导致亏损；
2. `## 二、目标与不做什么`：本篇要达成的可度量目标 + 明确的非目标（防止范围蔓延）；
3. `## 三、方案设计`：机制、公式、数据流；必须给出参数默认值与取值依据；
4. `## 四、与现有代码的接入点`：逐条列出需要新增/修改的模块与函数签名（**只写设计，不写实现**）；
5. `## 五、验收指标与门禁`：可量化指标 + 阈值 + 采样窗口 + 不达标时的处置；
6. `## 六、风险与回滚`：引入的新风险、监控信号、一键回退路径（配置开关名）。

### 3.2 三条贯穿全篇的设计主线

**主线 A —— 成本先行（Cost-first gating）。** 分两步，顺序不可颠倒：

- **A0（前置，P0 级）：先把成本记进账。** 在引入任何 edge 门禁之前，必须先修复 D-16——让实盘已实现
  盈亏变成**净额口径**（成交回执费率入 `trade["fee"]`、`_close_pnl` 扣双边费用与实测滑点）。理由很直白：
  门禁的分母（成本）与验收的分子（净收益）如果来自两套账，门禁调得再准也无法被证伪。**A0 不做，
  01~08 的一切验收指标都是在被污染的标签上做统计。**
- **A1：最小 edge 门禁。** `预期毛收益(bps) ≥ k × 往返成本(bps)`，`k` 默认 2.0。预期毛收益由 02/03 号文档
  定义的概率化信号给出（`p × 目标位距离 − (1−p) × 止损距离`，均以 bps 计），往返成本由 01 号文档定义的
  成本模型给出（taker/maker 费率 + 价差 + 冲击 + 跨结算的资金费率折算）。

门禁形态**刻意镜像已有的 `signals.apply_consensus_gate` / `base_rules.apply_ma_band_gate`**：纯函数、
显式入参、只会更保守、`close`/`hold`/`cancel_order` 永不被拦、实盘与回测共用同一核心，默认关闭时逐字回退。

**插入位置（v2 评审 P1-8 补充，硬约束）**：现有确定性链为
铁律（`session.py:571-572`）→ 利润保护冷却（`session.py:574`）→ 共识（`session.py:575`）→
下单前反思（`session.py:578-579`）→ 执行（`session.py:580`）。**edge 门禁插在共识门禁之后、反思之前**，
理由与 `session.py:568-570` 已确立的先例同构：① 必须落在 `decide` 之后，否则重试循环只认 `_map_decision`
的 degrade，落进去会每轮白烧 LLM；② 必须落在反思之前，被拦轮此时已是 `hold`，而反思对 `hold` 本就直通
（`session.py:644-646`）⇒ EV 不足的轮次自动省掉一次反思 LLM 调用，**门禁本身就是省钱项**。
回测侧须插在 `backtest.py` 中语义等价的同一位置，否则复现 D-14 的链路不同构。

**主线 B —— 概率化而非叙事化（Probabilities over narratives）。** 信号板当前给 LLM 的是
`stance/latestSignal/strength/reason` 四元组（`signals.py:34-45`），全是"立场"，没有"这个立场历史上值多少钱"。
改造后每条信号附带 `prior{winRate, meanRet, sampleN, horizonBars, regime, decayHalfLife}`。

**先验的两路来源（v2 评审 P1-2 修正）**——v1 笼统地写"基于 `engine/signal_edge.py`"，但该模块只覆盖
**8 个内置信号检测器**（`signal_edge.py:50-107`，目录见 `available_signals()`，`signal_edge.py:306`），
其键是**信号名**而非 `strategyId`，无法直接产出 `SignalPrior{strategyId,…}`：

1. **内置信号先验**：沿用 `engine/signal_edge.forward_returns`（`signal_edge.py:265`）做事件研究；
2. **策略级先验**：由既有 `engine/backtester.run_backtest` / `SimBroker` 按 `(instId, bar, regime)`
   **离线重放**每个策略，从成交序列统计胜率与均值收益——这是唯一能按 `strategyId` 归集的路径；
3. **因子侧证据**：`engine/factors/evaluation.py` 的 IC/RankIC/IR/tStat（`evaluation.py:201-251`），
   注意 `MIN_IC_SEGMENTS = 6`（`evaluation.py:21`）是既有的样本量门槛，先验须继承同一底线。

三路均在 purged walk-forward 切分上计算并落库；在线只读快照、绝不在决策轮里现算。
**成本口径去重（P2-5）**：`signal_edge._summarize_horizon` 已内建 `cost_bps` 参数（`signal_edge.py:241`），
影子 broker 亦按 5/2 bps 计费（`signals.py:30-31,85-86`）。每条先验**必须显式记录其成本口径**
（`costBasis` 字段：`gross` | `net@Nbps`），否则与 01 号的成本模型叠加会造成双重计费、把 edge 判死。
共识由等权投票（`signals.compute_consensus`，`signals.py:59-80`）改为**按先验 IR 加权的对数几率
（log-odds）聚合**，并保留原等权票数字段向后兼容。

**主线 C —— 离线闸门与在线闭环（Gate offline, learn online）。** 任何参数变更必须先过 06 号文档定义的
**五道闸**（v2 把链路同构性对账提为第 0 道）：

- **闸 0（新增，P1-5）：链路同构性对账。** 回测与实盘的决策链默认值必须逐项对齐后才允许比较——当前
  `allow_limit`（回测恒 False，`backtest.py:232,240` vs 实盘默认 True，`config.py:163`）与
  `reflection_enabled`（回测默认 False，`backtest.py:130` vs 实盘默认 True，`config.py:135`）两项不同构，
  **不对齐就跑闸门等于在验证另一个系统**。对账结果须作为闸门报告的第一张表。
- 闸 1：purged/embargo walk-forward 下的净收益 > 0。**注意**（P2-4）：仓库已有
  `plan_walk_forward_windows`（`engine/backtester.py`，服务层 `agent/services.py:1044`）与
  `walk_forward_max_windows=200`（`config.py:287`），但 **purge / embargo 全仓库零实现**，属本计划新增，
  不得写成"复用"。
- 闸 2：成本敏感性（成本 ×1.5 仍为正）。
- 闸 3：决策重放一致性（同一 payload 重放，动作一致率与 EV 分布漂移在阈内）。
- 闸 4：纸面（paper）灰度 N 笔且实测滑点与模型偏差在阈内。

上线后由 07 号文档定义的归因表持续度量四类偏差（信号偏差 / 决策偏差 / 执行偏差 / 成本偏差），
任一超阈自动降级到只平不开。

### 3.3 关键机制的设计要点（供下游文档展开）

- **净额 PnL 口径（01，A0，P0 级前置）**：`_close_pnl`（`executor.py:732-738`）扣双边费用与实测滑点；
  `_make_trade` 的 `"fee": 0.0`（`executor.py:779`）改为取自 OKX 成交回执的真实 `fee`/`fillPx`；
  `explain.meta` 增 `ordType` / `expectedFillPx` / `slippageBps`（收口仍走 `_inject_explain_meta`，
  `session.py:248-261`）。**兼容性硬约束**：`session_pnl_usd` 变为净额后，会话止盈止损阈值
  （`session.py:898-907`）、利润保护的浮盈判据与统计看板的历史数据口径全部随之改变，
  01 号文档必须显式给出**历史数据不可比**的声明与切换开关（默认关 ⇒ 逐字回退毛口径）。
- **成本模型（01）**：`round_trip_cost_bps = fee_open_bps + fee_close_bps + 2×(half_spread_bps + impact_bps)
  + funding_carry_bps`（**费率按开/平两腿分别取值**，因为一腿可能 maker、另一腿 taker——这正是 D-17
  说的"当前不可区分"）。maker 腿把 `fee_bps` 从 taker 5 bps 降到 maker 约 2 bps 并省掉半价差，
  代价是成交概率 < 1，**因此必须引入"未成交机会成本"项，而不是简单宣称限价更省**。
  限价能力**已上线且默认开启**（`config.py:163`，状态机 `live_auto/resting.py`，
  `ai_trade_limit_order_ttl_s=300`，`config.py:169`）——改造点是**挂价策略、TTL 自适应与费率归属记账**，
  **不是新建限价能力**，下游文档不得把它写成从零引入（v2 评审 P0-1）。
- **资金费率取数（01，P1-6 补）**：`fundingCarryBps` 需要仓库**当前不存在**的公共只读端点
  `GET /api/v5/public/funding-rate` 与 `/funding-rate-history`（现有 `okx/rest_client.py:227-236` 只有
  `instruments`）。文档须写明：新增只读端点 + 内存缓存（TTL 建议 60s）+ **取数失败即整项缺席降级**
  （`fundingCarryBps=None` ⇒ 成本模型标 `confidence="degraded"` ⇒ edge 门禁自动收紧到 `k_min×1.2`，
  绝不静默按 0 计）。这条新增的是 OKX 只读网络调用，**不是新的第三方 Python 依赖**。
- **元标签（02，P1-1 修正）**：主模型（LLM + 策略信号）给方向，元模型只给"该不该做、做多大"的
  二分类概率（López de Prado 的 meta-labeling 范式，正好对症"方向判断尚可、但笔笔都做导致成本吃光"）。
  **实现前提须写清楚**：`engine/factors/tree_ensemble.py` 只导出 `fit_rf_importances` /
  `fit_gbdt_importances` / `fit_extra_trees_importances`（`tree_ensemble.py:234,270,305`），
  返回的是**特征重要度向量**，既无分类器、无 `predict_proba`，也无模型持久化；
  且 **scikit-learn 不在 `services/quant/requirements.txt` 内**（仅 numpy/pandas）。因此元模型是
  **新增的纯 numpy 模块**，复用该文件的 `_build_tree` / `_predict_tree` 分裂原语（`tree_ensemble.py:196,220`）、
  `BUILTIN_SEED=0`（`tree_ensemble.py:40`）与 `BUILTIN_MAX_TRAIN_ROWS=4000` 行封顶（`tree_ensemble.py:39`），
  **不得假定 sklearn 可用**；样本不足或训练退化时**降级为校准 logistic（同样纯 numpy）**，
  再退化则整键缺席、回退到无元标签的既有行为。标签用 triple-barrier（上轨/下轨/时间轨），
  轨宽由 ATR 定标，与 05 号文档的自适应 SL/TP 共用同一套 ATR 口径（复用
  `engine/indicators.atr`，与 `ai_trade_profit_guard_atr_period=14`（`config.py:242`）同源）。
- **因子接入（03）**：新增 `payload["factorSnapshot"]`，形状为
  `{asOfTs, model{id, kind, trainedAt, normWindow}, factors[{key, cn, value, z, ic, rankIc, weight}], score, scorePct, regime}`，
  由 `engine/factors/scoring.py` 与既有权重模型服务派生。**硬约束**：快照必须带 `asOfTs` 与
  `normWindow` 契约字段（仓库已有 `resolve_norm_contract` 单源约定），且训练期与推理期归一化窗口必须一致，
  否则一律降级为整键缺席（沿用本仓库"键缺失即整段省略、prompt 逐字回退"的既有范式）。
- **决策器改造（04）**：`submit_trade_decision` 增加三个可选字段
  `expectedEdgeBps`（模型自估毛 edge）、`invalidationPx`（失效价，方向性硬约束）、`horizonBars`（预期持有根数）；
  服务端不信任其数值，只用它做**一致性校验**（模型自估 edge 与门禁计算的 edge 差异过大即降级并留痕），
  这与仓库既有"schema 是软引导、服务端才是硬保证"的姿态一致。
- **仓位（05）**：`size_fraction` 不再由 LLM 自由给出，而是
  `f = clamp(min(kelly_fraction × λ, vol_target / realized_vol) , 0, f_max)` 后再经既有
  `quantize_size_fraction` 吸附到用户档位；LLM 的 `sizeFraction` 降级为"上限提议"（只能调小）。
  这样既保留了 `position_ladder` 单一真源，又把仓位从叙事里拿走。
- **上线闸门（06）**：必须写明本仓库 AI 回测的**六条**已知偏差（`ai_auto/backtest.py:91-102` 已逐条列出
  ①bar 内极值保守近似、②跳空按开盘成交、③回测按根推进而实盘每 5 秒盯市、④无部分成交/无挂单撤改/
  无交易所降级态、⑤开仓当根不判回撤且冷却期在 bar 时钟下几乎恒过期、⑥承诺退出线同为 bar 内近似），
  **外加 v2 新增的第七条：回测与实盘的 `allow_limit` / `reflection_enabled` 默认值不同构**（D-14）。
  据此规定"回测只用于排除明显负期望，不用于宣称正期望"。
- **归因（07）**：新增每笔往返的归因记录 `attribution{signalIds, priorWinRate, expectedEdgeBps,
  realizedEdgeBps, feeBps, slipBps, fundingBps, decisionLatencyMs, gatePath}`，落在既有
  `trade.explain.meta` 旁路（仓库已有 `_inject_explain_meta` 收口，`session.py:248-261`），零新表。
  **注意**：`realizedEdgeBps` 只有在 A0（净额 PnL 口径）落地后才有意义——在 `"fee": 0.0` 的现状下
  它恒等于毛 edge（D-16）。因此 07 号文档必须把"依赖 01-A0"写成显式前置条件。

---

## 四、文件 / 模块变更计划

**本方案不修改任何源码。** 下游"代码开发"节点的实现物 = 下表 9 个 Markdown 文件。

| 文件 | 动作 | 一句话意图 | 字数目标 |
|---|---|---|---|
| `docs/improve/01-ai-autotrading/00-总纲与现状诊断.md` | 新建 | 定义亏损归因恒等式、列 D-1~D-18 代码级根因（**D-16 置首位**）、给出三阶段路线图与全局验收指标 | ~2200 |
| `docs/improve/01-ai-autotrading/01-交易成本与执行质量.md` | 新建 | **首要任务 A0：净额 PnL 口径修复（D-16/D-17/D-18）**；其次建立往返成本模型（双腿费率/价差/冲击/资金费率）与挂价策略改造（限价能力已上线，非新建），给出最小可交易 edge 门禁 | ~2200 |
| `docs/improve/01-ai-autotrading/02-信号层重构与元标签.md` | 新建 | 把信号板从"立场"升级为"带先验的概率"，引入 triple-barrier + meta-labeling 与策略衰减淘汰 | ~2000 |
| `docs/improve/01-ai-autotrading/03-因子层接入AI决策.md` | 新建 | 把 `engine/factors` 的 IC/RankIC、权重模型、组合因子经 `factorSnapshot` 接进决策 payload | ~2000 |
| `docs/improve/01-ai-autotrading/04-决策智能体改造.md` | 新建 | 把 prompt/工具 schema/门禁链从叙事驱动改为 EV 驱动，并引入置信度校准与决策频率治理 | ~2000 |
| `docs/improve/01-ai-autotrading/05-风险与仓位管理.md` | 新建 | 波动率目标化仓位、ATR 自适应 SL/TP、组合相关性预算、回撤刹车与利润保护层的整合 | ~2000 |
| `docs/improve/01-ai-autotrading/06-离线评估与上线闸门.md` | 新建 | purged walk-forward、成本敏感性、决策重放、纸面灰度四道闸与 champion-challenger 机制 | ~2000 |
| `docs/improve/01-ai-autotrading/07-实盘可观测与归因.md` | 新建 | 每笔往返的四类偏差归因、实盘-回测漂移监控、自动降级与日/周复盘报表 | ~2000 |
| `docs/improve/01-ai-autotrading/08-实施路线图与里程碑.md` | 新建 | 三阶段（止血/结构/进化）任务分解、依赖顺序、验收门与回滚开关清单 | ~2000 |

> 目录 `docs/improve/01-ai-autotrading/` 已存在且为空，直接写入即可，**不需要**先建目录。

**明确不改动的范围**：`services/quant/**`、`apps/web/**`、`desktop/**`、`.env.example`、`CLAUDE.md`
以及 `docs/plans/**` 下的其它 feature 目录。本次交付零源码改动、零依赖新增、零 DB 迁移。

> **"零依赖新增"的口径澄清（v2 评审 P1-6）**：该断言描述的是**本次交付物本身**（9 个 md 文件）。
> 文档中**提议**的机制并非全部零新增——01 号提议的资金费率需要新的 OKX 只读端点调用，02 号提议的
> 元模型需要一个新的纯 numpy 模块。两者都**不引入新的第三方 Python 包**（`requirements.txt` 不变），
> 但下游文档必须把它们如实标注为"新增代码 / 新增网络调用"，不得借"零依赖"之名把工作量写没。

---

## 五、接口设计（文档契约 + 文档内提议的接口）

### 5.1 文档写作契约（下游必须逐条遵守）

- **语言**：简体中文；术语首次出现给出英文原名（如"元标签（meta-labeling）"）。
- **文件名**：`NN-中文短标题.md`，`NN` 为两位序号，与第四节表格逐字一致。
- **首部**：每篇以一级标题 `# NN · 标题` 开始，紧随一段 3~5 行的"本篇结论先行"摘要（写清楚
  "改什么、为什么、验收看什么"），便于交叉阅读。
- **骨架**：严格使用 3.1 节的六节骨架（一、问题定位 / 二、目标与不做什么 / 三、方案设计 /
  四、与现有代码的接入点 / 五、验收指标与门禁 / 六、风险与回滚）。
- **交叉引用**：以相对链接引用同目录文档，如 `[01 · 交易成本](./01-交易成本与执行质量.md)`；
  引用代码一律 `path/to/file.py:line` 形式（可点击）。
- **禁止**：① 不得出现未在本 spec 第二节列出的"根因"而无代码证据；② 不得给出无默认值的参数建议；
  ③ 不得承诺任何收益率数字；④ 不得贴大段源码（引用行号即可）；⑤ 不得出现"（此处省略）"类占位。
- **每篇必须包含至少一张表格**（参数表 / 指标表 / 阶段表 / 归因表任选），保证可执行性。

### 5.2 文档中提议的运行时接口（供 04/03/05 号文档统一措辞，避免三处漂移）

> 以下是**提案**的接口形状，本次不实现；写入文档时必须逐字一致，未来实现时才有单一真源。
> **v2 评审 P1-3 修正**：v1 的 `estimate_round_trip_cost`（6 形参）与 `resolve_size_fraction`（7 形参）
> 违反 `CLAUDE.md`「函数形参不超过 5 个，多了请改用配置对象、命名参数或聚合结构体」。既然本节被指定为
> 未来实现的单一真源，签名必须**当场符合项目规范**，否则等于把技术债写进契约。下列签名均已改为聚合入参。

```text
# 成本模型（01 提案）——聚合入参，2 形参（CLAUDE.md ≤5）
CostQuery = {instId, bar, side, ordType, holdBars, bookSnapshot}
estimate_round_trip_cost(query: CostQuery, settings) -> CostBreakdown
CostBreakdown = {feeOpenBps, feeCloseBps, halfSpreadBps, impactBps, fundingCarryBps,
                 totalBps, confidence}      # confidence ∈ {ok, degraded}；degraded ⇒ 门禁收紧

# 最小 edge 门禁（01/04 提案，纯函数，镜像 apply_consensus_gate 签名范式）
apply_edge_gate(decision, edge_est, cost, *, k_min) -> (decision, gate_reason | None)
# gate_reason 形如 "edge_below_cost(edge=8.1bps,cost=14.0bps,k=2.0)"
# 插入位置：共识门禁之后、反思之前（session.py:575 与 :578 之间），见 §3.2 主线 A

# 信号先验（02 提案，离线产出、在线只读）
SignalPrior = {source, strategyId, signalKey, instId, bar, regime, winRate, meanRetBps,
               sampleN, horizonBars, ir, tStat, decayHalfLifeDays, costBasis, asOfTs}
# source ∈ {"signal_edge", "strategy_replay"}；两路来源见 §3.2 主线 B
# strategyId 与 signalKey 二选一非空；costBasis ∈ {"gross", "net@Nbps"}，防与 01 号双重计费

# 元标签模型（02 提案，新增纯 numpy 模块，不依赖 sklearn）
predict_trade_quality(features, model) -> {pWin, expectedEdgeBps, modelId, trainedAt, degraded}

# 因子快照（03 提案，进 payload["factorSnapshot"]）
FactorSnapshot = {asOfTs, model{id, kind, trainedAt, normWindow},
                  factors:[{key, cn, value, z, ic, rankIc, weight}], score, scorePct, regime}

# 仓位（05 提案）——聚合入参，1 形参（CLAUDE.md ≤5）
SizingInputs = {pWin, edgeBps, costBps, realizedVol, volTarget, kellyLambda, fMax}
resolve_size_fraction(inputs: SizingInputs) -> float   # 再经既有 quantize_size_fraction 吸附

# 归因（07 提案，挂在既有 trade.explain.meta 旁路）
TradeAttribution = {signalIds, priorWinRate, expectedEdgeBps, realizedEdgeBps,
                    feeBps, slipBps, fundingBps, decisionLatencyMs, gatePath}
```

**决策工具 schema 的增量提案（04 号文档写入）**：在 `submit_trade_decision` 的
`input_schema.properties` 上追加三个**可选**字段 `expectedEdgeBps` / `invalidationPx` / `horizonBars`，
`required` 不变；且必须沿用仓库既有的"总闸默认 False ⇒ `build_decision_tool_schema(0.0) is
DECISION_TOOL_SCHEMA` 恒等断言原样通过"的零回归约束（见 `prompts.py:175-218` 的注释）。

### 5.3 无 REST / WebSocket / CLI 变更

本次交付为纯文档，不新增或修改任何 HTTP 路由、WS 事件类型或 CLI 命令。文档中提议的接口一律标注
"提案（proposal）· 未实现"，避免被误读为已有能力。**标注样式统一为**：在每个接口代码块正上方独立一行写
`> 提案（proposal）· 未实现`，块内不再重复；正文首次提及该接口时以"（提案）"后缀标记一次即可。

### 5.4 落地位置硬约束（v2 评审 P1-3 新增）

`CLAUDE.md` 规定单文件 ≤800 行、单方法 ≤50 行、形参 ≤5、嵌套 ≤4、圈复杂度 ≤10。本计划提议的机制若
就近塞进现有文件会当场破线，因此下游文档在写"四、与现有代码的接入点"时**必须遵守**：

| 提议机制 | 禁止的落点 | 规定的落点 |
|---|---|---|
| 成本模型 / edge 门禁 | `ai_auto/session.py`（**现 1404 行，已是红线的 1.75 倍**） | 新增 `ai_auto/cost_model.py` + `ai_auto/edge_gate.py`（纯函数，镜像 `base_rules.py` / `profit_guard.py` 的"纯核心 + session 只做薄封装"范式） |
| 信号先验 / 共识加权 | 直接改 `signals.compute_consensus`（会破坏既有等权口径的回归断言） | 新增 `ai_auto/signal_prior.py`；`compute_consensus` 保持原样，新增并列的 `compute_weighted_consensus` |
| 元标签模型 | `engine/factors/tree_ensemble.py`（该文件职责是重要度，不是预测） | 新增 `engine/factors/meta_label.py`，仅复用其分裂原语与种子常量 |
| 归因写入 | 在 `session.py` 里另开注入点 | 沿用唯一收口 `_inject_explain_meta`（`session.py:248-261`），只加字段不加收口 |
| 资金费率取数 | 在决策轮内同步拉取 | 新增 `okx/rest_client` 只读方法 + 会话级缓存；**决策轮内禁止任何网络/DB IO**（与既有先验加载约定一致） |

每篇文档在"四、与现有代码的接入点"一节**必须显式写出新增模块名与预估行数**，且不得提出任何会让
`session.py` 继续增长的方案。

---

## 六、数据模型

### 6.1 交付物本身的数据模型

无数据库变更、无迁移、无新表。产物是 9 个 UTF-8（无 BOM）、LF 换行的 Markdown 文件，目录深度 1 层。

### 6.2 文档中提议的持久化模型（仅描述，不落地）

- **信号先验表（02 提案）**：建议复用既有 workspace KV / `factor_*` 系列表的存储范式，
  键 `signal_prior:{instId}:{bar}:{strategyId}:{regime}`，值为 `SignalPrior` 的 JSON；
  写入方为离线任务，读取方为会话启动时的一次性加载（**不得在决策轮内做 DB IO**）。
- **元标签模型工件（02 提案）**：复用既有权重模型工作台的模型行与 `metrics_json` 加法写范式，
  `kind="meta_label"`，避免新增表；训练/推理的归一化契约沿用 `resolve_norm_contract` 单源。
- **归因记录（07 提案）**：不新建表，写进既有 `live_trades` 行的 `explain.meta.attribution`
  子对象（`session.py:248-261` 已是唯一注入收口），历史统计端点按需聚合。
- **成本观测（01 提案）**：每笔成交记录 `expectedFillPx` 与 `actualFillPx` 之差落
  `explain.meta.slippageBps`，用于实测滑点分布，替代当前固定 `default_slippage_bps=2.0` 的假设值。

**兼容性硬约束（写入 03/04 文档）**：所有新增 payload 键一律遵循本仓库既有的
"**键缺失即整段省略、prompt 渲染逐字节回退**"范式（`prompts.py` 中 `_format_market_context` /
`_format_base_rules` / `_format_profit_guard` 一致采用），保证任何未装配 / 降级路径都能零回归退化。

---

## 七、测试与验收标准

本节点与下游文档写作节点的验收分两层。

### 7.1 交付物验收（对下游"代码开发"节点）

1. `docs/improve/01-ai-autotrading/` 下恰好存在第四节表格中的 9 个文件，文件名逐字一致；
2. **字数（v2 评审 P1-7 修正，统一口径）**：按**中文字符数**（`len(re.findall(r'[一-鿿]', text))`）
   计，**每篇 1800~2600**，其中 00 号与 01 号目标 2200（同样受 1800~2600 约束，无单独上限）；
   **9 篇合计 17000~23000**。v1 的"00 号可到 2400"与统一上限 2600 冲突，已删除；
3. 每个文件包含 3.1 节规定的六个二级标题，缺一即不合格；
4. 每个文件至少 1 张 Markdown 表格、至少 3 处 `file.py:line` 形式的代码引用；
5. 全部文件的交叉链接可解析（相对路径存在），无死链；
6. 全文无英文占位符、无 `TODO` 无责任人、无"待补充"；
7. `git status` 中这 9 个文件可见为未跟踪新增；**不得**出现 `services/`、`apps/`、`desktop/` 下的新增改动；
8. 文档中每条参数建议都带默认值与取值依据（如"`k_min=2.0`，依据：往返成本 14bps 时要求毛 edge ≥28bps，
   对应 15m 周期约 0.28% 的目标位距离，可由 ATR 校验"）；
9. **（v2 新增）** 引用的每一处 `file.py:line` 必须与写作当时的源码一致；凡引用第二节 D-x 的，
   编号与本 spec 一致（D-1~D-18），**不得**出现已被 v2 删除的 v1 论断——具体为
   "全额市价成本"、"`_format_hold_streak` 是催交易推力"、"复用 `tree_ensemble` 做二分类"、
   "回测免责段共五条"四条，出现任一即不合格。

### 7.2 方案内容验收（评审节点据此打分）

- **可证伪性**：每篇的"五、验收指标与门禁"必须给出指标名、阈值、采样窗口与不达标处置，
  不接受"提升胜率"这类不可度量的表述；
- **锚定真实代码**：改造点必须落在真实存在的模块（`ai_auto/*`、`live_auto/*`、`engine/factors/*`），
  且不得与既有硬约束冲突（如"平仓/撤单永不被门禁拦截"、"回测与实盘共用纯核心"、
  "总闸默认关闭时逐字回退"）；
- **成本闭环**：01 号文档给出的成本口径必须被 04/05/06 号文档一致引用，全篇只允许一套成本符号；
- **不承诺收益**：任何位置不得出现预期年化 / 胜率的承诺值，只允许出现"门禁阈值"与"监控阈值"；
- **风险对称**：每篇"六、风险与回滚"必须包含至少一条"本改动可能反而降低收益"的自我反驳与其监控信号。

### 7.3 无自动化测试

纯文档交付，不引入单测。校验以脚本化检查（字数 / 标题 / 表格 / 链接）与人工评审结合，
下游节点可用一次性 Python 校验片段自查后删除，**不得**把校验脚本提交进仓库。

**校验与写入的执行口径（v2 评审 P2-6 补，Windows 环境必读）**：

- 写文件一律 `Path(p).write_text(text, encoding="utf-8", newline="\n")` 或编辑器工具，
  **禁止 shell 重定向**（本机控制台默认 GBK，重定向会写坏中文）；
- 校验脚本读文件同样显式 `encoding="utf-8"`；若需 `print` 中文，先设 `PYTHONIOENCODING=utf-8`，
  否则在 GBK 控制台上会抛 `UnicodeEncodeError` 而被误判为"文件坏了"；
- 交付前逐个文件断言：`b[:3] != b"\xef\xbb\xbf"`（无 BOM）且 `b.count(b"\r\n") == 0`（纯 LF）；
- 字数判据以 7.1 第 2 条的正则为准，**不得**用 `len(text)`（会把标点、空白、代码引用一并算进去，
  从而虚高约 30%）。

---

## 八、风险与缓解

| 风险 | 影响 | 缓解 |
|---|---|---|
| 下游节点误把本方案当成"写代码"任务，直接改 `services/quant` | 污染工作树（该工作树已有多个特性未提交，见项目记忆），提交时极易混入他人 hunk | 第四节已显式声明"实现物 = 9 个 md 文件、零源码改动"；验收项 7 明确禁止源码新增 |
| 文档流于通用套话（"引入机器学习""加强风控"） | 对用户零价值，且无法指导后续实现 | 六节骨架 + 每篇 ≥3 处 `file.py:line` 引用 + 参数必须带默认值与依据 |
| 过度设计：一次提出过多机制，团队无法落地 | 计划变成书架文档 | 08 号文档强制三阶段切分；01 号的"最小 edge 门禁"被指定为 P0 唯一必做项（改动面最小、预期收益最大） |
| 提议的机制与既有硬约束冲突（如门禁拦 close、回测/实盘口径分叉） | 未来实现时引入真实资金风险 | 7.2 节把"不得与既有硬约束冲突"列为评审否决项；接口提案一律镜像 `apply_consensus_gate` 的纯函数范式 |
| 把"降低交易频率"当作万灵药，导致错过真实机会 | 收益机会成本 | 04 号文档要求频率治理必须是**条件性**的（EV 不足才抑制），并在验收指标中同时监控"错失交易"（被门禁拦下但事后验证正确的比例） |
| 成本模型高估 maker 优势 | 实际成交率下降、滑点变差，净收益反而更低 | 01 号文档强制包含"未成交机会成本"项，并把 maker-first 列为需要纸面灰度验证的项，而非直接上线 |
| 因子层接入引入前视偏差（look-ahead） | 回测虚高、实盘崩塌 | 03 号文档强制 `asOfTs` + 训练/推理归一化窗口一致性校验，任一不满足即整键缺席降级 |
| 文档编码问题（Windows 环境 CRLF / GBK） | 文件被工具链改坏 | 统一 UTF-8 无 BOM、LF；写文件一律用编辑器工具或 Python `write_text(..., encoding="utf-8")`，禁止 shell 重定向（判据见 7.3） |
| **（v2 新增）下游把已上线能力写成"待建"，重复造轮子** | 限价委托（`config.py:163` 默认开、`live_auto/resting.py` 状态机、commit 3bbc503）、利润保护棘轮、基础策略铁律层都已合并；写成新建会与在制特性冲突并浪费一个迭代 | §一与 §3.3 已就地更正；7.1 验收第 9 条把"全额市价成本"等四条 v1 论断列为**不合格项**；每篇"四、接入点"必须先写"现状已有什么" |
| **（v2 新增）A0（净额 PnL 口径）本身是破坏性变更** | `session_pnl_usd` 由毛转净后，会话止盈止损阈值（`session.py:898-907`）、利润保护的浮盈判据、统计看板历史数据口径全部改变，用户会看到"同样的行情、盈亏数字变差了" | 01 号文档必须给出独立开关（默认关 ⇒ 逐字回退毛口径）、历史数据不可比声明、以及"先只做记录再切换判据"的两步走；切换前后各留一份滚动 100 笔对照 |
| **（v2 新增）先验与成本双重计费** | `signal_edge._summarize_horizon` 已内建 `cost_bps`、影子 broker 按 5/2 bps 计费；若 01 号成本再扣一次，edge 恒为负、系统退化为永不开仓（本仓库有过"反思致永不开单"的同类事故） | `SignalPrior.costBasis` 字段为**必填**；04 号 EV 不等式在组装时按 `costBasis` 决定是否扣成本，并在 06 号闸 3（决策重放）中把"开仓率骤降为 0"列为回滚触发条件 |

---

## 九、下游节点须知（实施提示）

1. **先读后写**：写每篇前先重读本 spec 第二节的 D-x 表与对应源码行，确保引用行号准确（代码在演进，
   行号以写作当时为准，若偏移请更新引用而不是照抄）。**尤其注意**：本仓库工作树含多个在制特性，
   `ai_auto/session.py`、`live_auto/executor.py`、`config.py` 都在活跃演进。
2. **写作顺序**：`00 → 01 → 02 → 03 → 04 → 05 → 06 → 07 → 08`。00 定义符号体系（p / W / L / C / k），
   后续各篇一律复用，禁止另立符号。
2.5 **（v2 新增）先看现状再写方案**：每篇"四、与现有代码的接入点"必须以"现状已有什么"开头。
   本仓库已合并且默认开启的能力至少包括：限价委托状态机（`config.py:163`、`live_auto/resting.py`）、
   利润保护棘轮（`config.py:217-253`、`ai_auto/profit_guard.py`）、基础策略 MA 通道铁律
   （`ai_auto/base_rules.py`）、下单前二次反思（`config.py:135`、`ai_auto/reflection.py`）、
   共识门禁（`config.py:145`）、walk-forward 回测（`agent/services.py:1044`）、
   因子 IC/RankIC 评估（`engine/factors/evaluation.py`）。**把上述任一写成"待新建"即为事实错误。**
3. **单篇独立可读**：允许必要的重复（如成本公式在 01 与 04 各出现一次），但数值与符号必须一致。
4. **禁止执行**：本计划描述的机制一律标注"提案 · 未实现"；不得在文档中声称任何能力已经上线。
5. **提交范围**：最终提交只暂存 `docs/improve/01-ai-autotrading/` 下 9 个文件与本 `spec.md`，
   使用精确路径 `git add`，严禁 `git add -A`（工作树含多个在制特性）。

---

## 十、附录：全局验收指标（写入 00 号文档的指标表原型）

| 指标 | 定义 | 目标 | 采样窗口 | 不达标处置 |
|---|---|---|---|---|
| **费率入账完整性（v2 新增，A0 门禁）** | `Σ(trade.fee != 0 的平仓笔数) / Σ平仓笔数` | = 100%（当前为 **0%**，见 D-16） | 滚动 50 笔 | **未达 100% 前，本表其余一切指标不得用于判断盈利能力**——分子被污染 |
| **实测往返成本（v2 新增）** | `(feeOpenBps + feeCloseBps + 2×slipBps + fundingBps)` 的滚动中位数 | 记录为准，不设目标；用于标定 `k_min` | 滚动 100 笔 | 与模型成本偏差 >5 bps ⇒ 重估成本模型 |
| 净期望 / 笔 | `Σ净盈亏 / 往返笔数`（含费率、滑点、资金费率） | > 0 且 > 1×往返成本 | 滚动 100 笔 | 自动降级为只平不开 |
| 成本占比 | `Σ成本 / Σ毛盈亏绝对值` | < 35% | 滚动 100 笔 | 触发 01 号执行改造复审 |
| 决策转化率 | `产生成交的轮次 / 决策轮次` | 10%~35%（过高=过度交易，过低=僵死） | 滚动 200 轮 | 复审 04 号频率治理参数 |
| 置信度校准误差 | 预测胜率分箱与实际胜率的 Brier 分数 | < 0.24 | 滚动 100 笔 | 关闭以置信度为输入的仓位放大 |
| 实测滑点偏差 | `实测滑点bps − 模型滑点bps` | 绝对值 < 3 bps | 滚动 50 笔 | 重估成本模型并收紧 `k_min` |
| 回测-实盘漂移 | 同期决策动作分布的 JS 散度 | < 0.15 | 每周 | 暂停参数变更，回到 06 号闸门 |
| 最大回撤 | 会话权益峰谷比 | < 预算的 15% | 实时 | 触发回撤刹车（05 号），当日停开新仓 |

---

## 评审结论（Review Verdict）

### 结论：**有条件通过（Approved with conditions）**

v1 的整体判断是对的——把 AI 自动交易从叙事驱动改造成期望值驱动，是这个系统当前唯一值得做的大方向；
六节骨架、接口先行、门禁镜像既有范式、不承诺收益率，这些都体现了对本仓库既有约定的尊重，规模也压在
9 篇 × 2000 字这个团队能真正读完的量级内，没有过度设计。

但 v1 的**成本论证链有一处事实性错误**，且**漏掉了整份计划里贡献度最高的那条根因**：实盘已实现盈亏
根本没有扣费（`executor.py:779` 的 `"fee": 0.0`、`executor.py:732-738` 的纯价差 `_close_pnl`），
而 v1 引以为据的 `default_taker_fee_bps` / `default_slippage_bps` 只作用于回测与纸面路径。
与此同时，v1 把早已默认开启的限价能力（`config.py:163`）当成待建的 "maker-first 改造"。
这三点如果不修，00/01/04/05/07 五篇都会建立在错误前提上。上述 1 个 P0 与 8 个 P1 **均已在本文档正文
就地修复**，正文版本号已升 v2，第二节根因表由 15 条扩到 18 条（D-16~D-18 为新增，D-1/D-3/D-10/D-11/
D-14/D-15 已就地更正）。

### 放行条件（下游"代码开发"节点必须逐条满足）

1. **D-16 必须写成全集第一优先级**：00 号的贡献度排序、01 号的首要任务（A0 净额 PnL 口径修复）、
   §十的"费率入账完整性"门禁三处口径必须一致；不得把它降格为附带修复。
2. **不得复活 v1 的四条错误论断**（详见 7.1 第 9 条）：全额市价成本 / `_format_hold_streak` 是催交易推力 /
   复用 `tree_ensemble` 做二分类 / 回测免责段共五条。
3. **edge 门禁的插入位置**必须逐字写成"共识门禁之后、反思之前"，并给出 `session.py:575`→`:578` 的锚点
   与"被拦轮自动省一次反思 LLM"的理由。
4. **元标签模型不得假定 scikit-learn 可用**（`requirements.txt` 无此依赖），必须写明是新增纯 numpy 模块
   并给出降级链：树模型 → 校准 logistic → 整键缺席。
5. **信号先验必须写成两路来源**（`signal_edge` 覆盖 8 个内置检测器 / 策略级走 `run_backtest` 离线重放），
   且 `SignalPrior.costBasis` 为必填字段，防与 01 号双重计费。
6. **接入点必须遵守 §5.4 落地位置表**：新机制一律进新模块，任何让 `ai_auto/session.py`（现 1404 行）
   继续增长的方案即判不合格；接口签名形参 ≤5。
7. **06 号必须包含"闸 0 链路同构性对账"**，明确 `allow_limit` / `reflection_enabled` 在回测与实盘的
   默认值当前不同构，不对齐即不得比较。
8. **每篇"六、风险与回滚"必须至少含一条自我反驳**，其中 01 号必须包含"A0 切换会让盈亏数字变差、
   用户可能误判为系统变坏"，02/04 号必须包含"双重计费导致永不开仓"的监控信号与回滚开关。

### 未纳入本次修复的观察（不阻断，留给下游斟酌）

- `ai_auto/session.py` 已 1404 行、`live_auto/executor.py` 863 行，均超 `CLAUDE.md` 的 800 行红线。
  这是本计划**之前**就存在的技术债，本次仅通过 §5.4 约束"不再恶化"，拆分本身不在本计划范围内。
- 本计划全程未触及"AI 是否应该由 LLM 来做方向判断"这一更根本的问题。若 02 号的元标签落地后测得
  LLM 方向判断的先验胜率并不优于策略信号本身，08 号的第三阶段应当据此重新评估架构，
  而不是继续在其上叠加门禁。这一点建议写进 08 号的"阶段三退出条件"。

---

**文档结束。** 本 spec 版本 **v2**（v1 → v2 的全部修改见文首「评审记录」）。

---

## 实施过程发现的方案缺陷

下游「代码开发」节点在按 §九.1「先读后写」逐条回读源码时，发现 spec v2 有 2 处**行号引用与写作当时的源码不符**。
两处均**不影响任何论点或设计结论**，按 §九.1「若偏移请更新引用而不是照抄」的约定，已在 9 篇交付文档中
使用更正后的行号，并在此留痕。

| 编号 | 位置 | spec v2 的写法 | 源码实际 | 处置 |
|---|---|---|---|---|
| **I-1** | §3.2 主线 C 闸 1、§P2-4 | `plan_walk_forward_windows` 的服务层调用在 `agent/services.py:1044` | 该文件共 1073 行，`plan_walk_forward_windows` 的 import 在 `agent/services.py:20`，两处调用在 `:914` 与 `:984`；`:1044` 处无相关代码。定义本身在 `engine/backtester.py:26` | 交付文档统一引用 `engine/backtester.py:26`（定义）与 `agent/services.py:914,984`（调用）。**「purge / embargo 全仓库零实现、属本计划新增」这一论断经复核成立**，未受影响 |
| **I-2** | D-2、§3.3 资金费率取数 | `okx/rest_client.py:227-236` 只有 `/api/v5/public/instruments` | `get_instrument_meta` 的 `def` 在 `rest_client.py:226`（docstring 227、请求 236），起始行差 1 | 交付文档引用 `rest_client.py:226-236`。**「无 funding-rate 端点、需新增只读调用」这一论断成立**（该文件另有 `/api/v5/market/candles`、`history-candles`、`tickers`、`ticker` 四个市场端点，但公共 `public/*` 调用确实只有 `instruments` 一处） |

**另记两处经复核成立、但表述可更精确的点**（未改设计，交付文档已按更精确的写法落笔）：

- **D-10 的频率证据**：除墙钟（`config.py:184`）与确认 K 线双触发外，仓库**已有**一层去重守卫
  `wake_guard.should_skip_interval_wake`（调用在 `session.py:408-414`）。但它只在「空仓且数据未变」时跳过，
  **不是 EV 条件**，因此 D-10「无 EV 条件抑制」的结论不变；交付文档 04 号已把这层守卫如实写入
  「现状已有什么」，避免下游把它当成待建能力（对齐 §九.2.5 的硬约束）。
- **D-11 的合规覆盖率**：`classify_zone`（`base_rules.py:156-166`）的边界判据是 `dist <= tolerance + _EDGE_EPS`
  （**含**边界），与 spec 的 60% 覆盖率算法一致，结论成立。

**结论：spec v2 的 1 个 P0 + 8 个 P1 修复方向、18 条根因与 8 条放行条件全部经源码复核成立**，
无需实质性修订；本节仅为行号勘误留痕。
