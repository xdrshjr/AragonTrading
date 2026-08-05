# 策略与因子挖掘（strategy-factor-discovery）设计方案 v2

> 需求出处：主任务「基于中金 Loop Engineering 因子挖掘报告，给本项目加【策略与因子挖掘】
> 功能」。要点四条：① Agent 自动挖掘因子/策略并自行回测、按结果决定是否保留；
> ② 可 24h 长期运行、开启后持续挖掘；③ 全过程有记录、可回溯、可管理；
> ④ 策略涵盖因子策略 / 常规策略 / 组合策略。
>
> 本文是**唯一实现依据**。下游工程师应能逐行照做而无需再做架构决策。凡本文写「必须 /
> 严禁 / 唯一真源」的地方都是承重条款，改动须回到本文修订。

---

## 评审记录（v1 → v2）

评审人：Anthropic 工程团队资深评审。评审范围：可行性 / 完备性 / 一致性 / 规模适配四维，
逐节对照仓库真实代码核实（不接受文档自述，凡「已核实」均指已读源码确认）。

**结论摘要：3 个 P0、8 个 P1、7 个 P2。全部 P0 / P1 已在 v2 正文直接修正**，修正处均在下表
「落点」列给出小节号；P2 中影响可读性与可实现性的 7 条亦一并修正。原 v1 的架构主干
（D-1 `expr` 桥 / D-2 导入边界 / D-3 检查点拆两半 / D-4 验证 fail-closed / D-5 零改既有表）
**经核实全部成立，不推翻**；下述问题集中在「承重细节写得不够死，照做会踩坑」。

### P0（会让功能不可用或让核心安全闸空转，必须改）

| # | 问题 | 证据 | 落点 |
|---|---|---|---|
| P0-1 | **F6 的「OOS 与 IS 同号」判据在 `direction="auto"` 下恒真，最强的过拟合防线整条空转。** `evaluation._eval_horizon` 返回的 `rankIc` 是**已按方向乘过 `dir_mult` 的值**，而 `resolve_direction` 的定义就是「取符号使主 horizon RankIC 为正」。若 IS / OOS 各自以 `auto` 调一次 `evaluate_factor_key`，两段的 `rankIc` **都会被构造成非负**，F6 永远通过。v1 §2.12 只说「验证期已冻结」，没说在**哪一段**冻结、也没说必须把冻结后的字面量传给 OOS。 | `evaluation.py:179-181`（`rankIc = dir_mult * rank_ic`）、`evaluation.py:186-198`（`resolve_direction`） | §2.7 新增「IS/OOS 求值与定号协议」 |
| P0-2 | **F8 的成本门槛量纲错配 10000 倍，实现后没有任何候选能入库。** `topBottomSpread` 是小数收益差（如 0.004），而 `settings.default_taker_fee_bps=5.0` / `default_slippage_bps=2.0` 是 **bps**（`simulator.py` 内部 `/1e4` 才用）。v1 写的 `costMult × (2×(fee+slip))` 直译即 `2.0 × 2 × 7.0 = 28`，等于要求 top-bottom 价差 > 2800%。这会让 DoD #4「至少一个候选能通过 11 条闸入库」永不可达，且在 24/7 长跑里表现为「搜索很难」而非报错。 | `config.py:409-410`、`engine/simulator.py:108-109`（`fee_rate = taker_fee_bps / 1e4`） | §2.7 F8 |
| P0-3 | **§2.2 声称的「入库时写 `name="expr:<hash8>"` 即得稳定互异列名」不成立——`validate_members` 会把 `name` 键直接丢掉。** 该函数的返回构造只保留 `{factorKey, params, weight, direction}` 四键。挖掘因子入 `factor_sets` 必经此边界（`factors/service.py` 的 323/427/452/550 四处）。落库后 `member_names` 只能回落 `factorKey`，而挖掘组合的**每个成员 factorKey 都是 `expr`**，于是列名退化为 `expr` / `expr#2` / `expr#3` 这种**纯位置序**；成员一旦重排，`assert_weights_cover` 的键集相等检查仍通过，训练权重却被静默错配到别的因子上。既有库因每个成员多为不同 key 而少见此路径，本特性会把它变成常态。 | `factors/specs.py:44-56`（构造中无 `name`）、`engine/factors/scoring.py:84-93`、`factors/specs.py:168-181` | §2.2 表 + §2.12 |

### P1（正确性 / 零回归 / 可交付性风险，必须改）

| # | 问题 | 证据 | 落点 |
|---|---|---|---|
| P1-1 | **两条既有测试会被本特性直接打红，而 §3.2 变更计划一个测试文件都没列——与 DoD #10「无新增红灯」正面冲突。** ① `test_factor_library.py::test_catalog_consistency` 断言 `{f["key"] for f in available_factors()} == set(FACTOR_LIBRARY)`，一旦 `available_factors()` 过滤 `hidden` 即红；② `test_factor_mining.py::test_default_candidates_cover_whole_library` 断言 `default_candidates("1H")` 的 key 集 `== set(FACTOR_LIBRARY)`，一旦跳过 hidden 即红。另有 `test_factor_library.py` 的因果性 / 形状两条用例会以 `params=None` 遍历到 `expr`，故 `_expr_factor` 的降级返回**必须与 df 等长、index 0..n-1**，否则第三条也红。 | `tests/test_factor_library.py:66-73`、`tests/test_factor_mining.py:34-36`、同文件 `:16-36` | §2.2、§3.2、§3.6（新增） |
| P1-2 | **F9「换手代理 ≤ 0.35」依赖一个仓库里并不存在的指标。** `evaluation.py` 的模块 docstring 提到「换手代理」，但 `FactorHorizonStat` / `FactorEvalResult` 两个 dataclass 里**没有任何 turnover 字段**，全包 grep `turnover` 零命中。v1 的「出处」列写「防高频噪声因子」而未给算法，下游无法实现。 | `engine/factors/evaluation.py:33-63`（字段全集）、`rg turnover engine/factors` 无结果 | §2.7 F9 |
| P1-3 | **并发闸用 `manager.active_count() < N` 判定，正是仓库已经修过的 TOCTOU 缺陷。** `weight_train_manager` 的 H-B3 明确把「先查 `active_count` 再注册」改成**同步无 await 的 `reserve()` 预占**，就是因为判定与注册之间可被另一个请求插入。发现层照抄旧写法等于把已修的坑重新挖一遍。 | `factors/weight_train_manager.py:358-380`（`active_count` / `reserve` / `release`）、`factors/weight_model_service.py:335` | §2.14 闸2、§4.1 |
| P1-4 | **续跑后 F10 的双通道相关性去重会静默失效。** F10 要求新候选与**每一条**已入库因子比对「分段 IC 序列相关 < 0.70 且因子值序列相关 < 0.85」，但 `series_json` 只持久化 ≤24 点分段 IC 与 ≤10 桶分位，**因子值序列根本没落库**，检查点也不含它。进程重启 / `resume` 后 kept 池只剩元信息，值级去重无从计算。v1 全文未规定重建。这会让长跑（本特性的核心卖点）在每次重启后开始收同质因子。 | §5.3 `series_json` 定义、§2.10 检查点字段表 | §2.10 新增「续跑期 kept 池重建」 |
| P1-5 | **入库形状越界：`Factor.name` 是 `String(128)`，而 v1 规定 `name=<中文渲染>`（`describe_text` 上限 512）；`category=<机制族>` 会污染既有类别词表。** 深表达式的中文渲染轻易过 128 字符（SQLite 不强制截断，但契约被破坏且 UI 列表破形）。更实在的问题是 `category`：`MemberPalette` 的 `CategoryChips` 是从**库目录** `available_factors()` 派生的，13 个机制族名不会出现在任何 chip；而按 category 服务端过滤时，挖掘因子会从「我的因子」筛选结果里整体消失。 | `db/models.py` `Factor.name: String(128)` / `category: String(32)`、`components/Factor/MemberPalette.tsx:151-155,465-490` | §2.12 |
| P1-6 | **IS / OOS 分别调 `evaluate_factor_key` 会各自重新暖机，指标不可比。** 表达式窗口最长 240（`WINDOW_LADDER` 顶格），若把 K 线切片后各自 `compute_factor`，OOS 段前 240 根全是 NaN——在 30% 尾段（默认 1200 根）里白丢 20%，且与 IS 段算出的同一表达式**不是同一条序列**。 | `library.compute_factor` 每次从切片头部重算 rolling；`evaluate_factor(df, values, ...)` 支持外部传值 | §2.7「IS/OOS 求值与定号协议」 |
| P1-7 | **v1 的「最小可交付范围」未定义，「完成」不可判定。** §2.4 定义四条搜索轴、§7.3 DoD 却只要求「至少一个候选入库」，而 §9 又把另外三轴放在第 7 步「每轴独立可验收」。于是同一份文档既可以理解为「四轴全做才算完」，也可以理解为「只做 factor 轴即可」。按 §3 的行数预算，四轴 + 五分区前端合计 21 新后端文件 / 16 新前端文件 / 24 个测试文件，一次性交付明显超出单轮可控规模。 | §2.4 / §7.3 / §9 三处口径不一致 | §〇 新增 D-6、§7.3、§9 |
| P1-8 | **自动续跑的安全姿态有缺口。** `discovery_auto_resume=True` + `discovery_enabled` 可被关掉这两条没有联动约束：v1 §2.1 纪律 4 只说「按 `auto_resume` 决定续跑还是 `paused`」，没说总闸关闭时**必须**一律 `paused`。否则运维把总闸关掉后，重启进程仍会拉起 24/7 的 LLM 调用循环——写类端点 403 了，后台却在烧配额。另外续跑后 `llmMaxCallsPerHour` 的计数窗口如何起算也未定义。 | §2.1 纪律 4、§6 配置表 | §2.1 纪律 4、§2.11 |

### P2（可读性 / 工程细节，v2 已一并修正）

| # | 问题 | 落点 |
|---|---|---|
| P2-1 | `main.py` 里 discovery 是**第 16 个**路由（现有 15 个：market/strategies/runs/paper/ws_market/agent/accounts/trading/factors/live_auto/ai_auto/app_settings/workspace/weight_models/weight_batches），v1 写「第 15 个」。 | §3.2 |
| P2-2 | 数据刷新失败的捕获面写窄了：`load_range_candles` 在区间无已确认 K 线时抛的是 **`HTTPException(400, EMPTY_RANGE)`**，不是 `OKXError`；只捕 `OKXError` 会让后台循环直接崩成 `failed`。另外 `factors.service._candle_cache` 的 TTL 只有 90s、cap 16，**不是 run 级缓存**，R-3 不能靠它。 | §2.9、§8 R-3 |
| P2-3 | 审查规则 `constant_or_empty` 需要真正求值，与「第一道：确定性规则（纯函数）」的定性冲突，且会与验证层重复算一遍。 | §2.6 |
| P2-4 | 缺单轮墙钟预算：`candidateTimeoutS=20s × candidatesPerIteration=40` 最坏 800s，远超默认 300s 间隔，v1 只有 `overrun` 标记而无上限。 | §2.9 |
| P2-5 | §3.4 的「`components/Factor/FactorTable.tsx` / 参数编辑器」指向不存在的改点——仓库里**没有**按 `paramsSchema` 逐类型渲染的因子参数编辑器（`FactorTable.tsx` 只在第 165 行透传 params 给评估）。 | §3.4 |
| P2-6 | `run_in_threadpool` 与 API 请求共用 anyio 默认线程池；24/7 挖掘会与交易 / 回测请求争抢，v1 无并发预算约束。 | §2.9 |
| P2-7 | `discovery/__init__.py` 若在模块层再导出 `validation` / `searchers`，会让 `library._expr_factor` 的延迟导入把 `engine.factors.evaluation` 整条链拉起，形成 `engine ↔ discovery` 的脆弱回边。 | §2.2、§3.1 |

### 未改动的评审判断（记录理由，避免下游重复讨论）

- **状态码 409 vs 429**：仓库两种先例都有（`weight_model_service.py:336` 用 429 表并发上限，`ai_auto/manager.py:564` 用 409 表「单活跃独占」）。发现层 `discovery_max_runs` 默认 1、语义是「单活跃独占」，故**维持 409**，只修 TOCTOU（P1-3）。
- **`FactorSpec` 加 `hidden` 字段**：已核实 12 条既有构造全部是「5 位置参 + 可选 `intraday_only=`」，末位加带默认值的 `hidden` 确实零改既有构造。**判定成立，不改。**
- **D-5「不给既有表加列」**：已核实 `db/session.py` 有 4 个 `_ensure_*_columns` ALTER 补丁，正是为绕开 `create_all` 不补列。本设计只加三张新表，**判定成立，不改。**
- **`is_bar_compatible` / `mining._merge_key` / `validate_factor_key` 无须改**：逐一核实无误。
- **报告数字不得引用（R-14）与「真实数据硬约束」**：与仓库既有姿态一致，**保留**。

---

## 〇、阅读顺序与承重决策速览

先读这五条，它们决定了后面所有细节为什么长这样：

| # | 承重决策 | 一句话理由 |
|---|---|---|
| D-1 | 挖掘产物落在**新增的 `expr` 通用表达式因子**上（`FACTOR_LIBRARY["expr"]`，参数 `{"expr": "<规范式>"}`） | 让「挖出来的因子」从诞生那一刻就能走完既有全部管线（评估 / 权重训练 / 组合 / 回测 / AI 实盘上下文），**零改因子层**，见 §2.2 |
| D-2 | 挖掘循环是**新独立包** `app/discovery/`，与 `live_auto` / `ai_auto` / `trading` **零 import 依赖** | 发现层永不下单。该边界由一条 import 图断言测试守门（§7.1 T-B12） |
| D-3 | 检查点真源拆两半：**已测哈希在 DB 表**（`discovery_candidates.expr_hash` 唯一索引），**小状态在 `discovery_runs.checkpoint_json`** | 24/7 跑三天会攒十万级候选，JSON 大对象既写不动也不原子；DB 唯一索引天然幂等去重 |
| D-4 | 验证层（回测 / 联合过滤）**硬编码、fail-closed**；生成 + 审查的 LLM 子代理**fail-open** | 与报告一致：LLM 负责供给与边界精判，达标与否绝不由 LLM 说了算；LLM 挂掉时循环退化为纯演化搜索而不是停摆 |
| D-5 | 只新增 3 张表、**不给任何既有表加列** | 仓库既知硬伤：`create_all` 不会给既有表补列，补列必须写 `_ensure_*_columns` ALTER 补丁；本特性主动绕开该雷区 |
| D-6 | **v1 的「完成」= `factor` 单轴闭环**；`factor_combo` / `strategy_param` / `strategy_combo` 是 v1.1+ 的加法增量（v2 新增，评审 P1-7） | 四轴同时落地是 21 新后端文件 + 16 新前端文件 + 24 测试文件，单轮不可控。`factor` 轴自带完整的「生成→审查→验证→入库→回溯」闭环，独立满足需求四条要点；其余三轴复用同一循环，随时可加而无需回改架构。见 §7.3 与 §9 |

**D-6 的强制推论（不得含糊）**：未实现的搜索轴**必须在 UI 上不可选**（`DiscoveryLauncher` 的轴选择器
只渲染 `GET /discovery/config/defaults` 回的 `implementedAxes`，绝不渲染灰掉的「敬请期待」占位），
`POST /discovery/runs` 收到未实现轴一律 400 `DISCOVERY_AXIS_UNSUPPORTED`。**严禁**出现「能选、能启动、
静默不产候选」的空转轴——那正是本仓库反复吃过亏的失败模式（门禁 / 计数器算了但没有落点）。

---

## 一、概述（Overview）

**做什么。** 在工作台最左侧 `NavRail` 新增第三个一级入口「策略与因子挖掘」，点进去是一块与
「因子实验室」同级的全屏工作区 `DiscoveryLab`。用户在这里配置一次**挖掘任务**（标的 / 周期 /
数据窗口 / 启用哪几条搜索轴 / 过滤阈值 / LLM 用量上限），点「开始」后后端起一个可 24 小时长
跑的**发现循环**：每一轮按「生成 → 审查 → 验证 → 入库」四步推进一次迭代，生成层用变异 / 交叉 /
参数扰动 / 随机探索 / LLM 机制引导五种操作产出候选，审查层先用确定性规则挡掉退化与越界表达
式、再抽样交 LLM 子代理做经济学含义精判，验证层用**硬编码**的评估与真实回测跑联合过滤，达标
者入候选库并更新检查点。整个过程经 WebSocket 实时流回前端：迭代时间线、五维来源堆叠、漏斗
（生成 / 审出 / 验证 / 入库）、入库率曲线、机制族覆盖、被 FSA 冻结的骨架、LLM 调用轨迹。

**怎么落地才不制造孤岛。** 本项目已经有一整套成熟的因子与策略基建：`engine/factors/` 的纯计算
因子库 / 评估（IC、RankIC、分段 IR、分位单调性）/ 训练 / 打分，`engine/backtester.run_backtest`
的纯回测，`factors/weight_train_manager.py` 的「流式作业 + 事件缓冲 + 协作式取消 + 必达终态」
长跑范式，`ai_auto/decider.py` 的「只放行一个工具 + 自行累积 partial_json + 安全回落」LLM 受
约束调用范式。本设计**不重造任何一个**：发现循环的作业管理镜像 `WeightTrainManager`，LLM 子
代理镜像 `decider`，回测直接调 `run_backtest` 纯函数，因子评估直接调 `evaluate_factor_key`。
唯一真正新增的引擎能力是**因子表达式 AST**（`discovery/expression.py`）——因为现有因子库是
「12 个具名因子 × 数值参数」的固定目录，而报告的变异 / 交叉必须作用在可拆解的表达式树上。这
棵树通过 D-1 的 `expr` 桥接因子回流既有管线：一个挖出来的因子在库里长成
`{factorKey: "expr", params: {"expr": "sub(ts_mean(overnight,20),delta(amplitude,5))"}}`，
`compute_factor` / `build_factor_matrix` / `factor_score` 策略 / 权重训练 / 组合打分 / AI 交易
的因子上下文**一行不改**就能吃下它。

**边界与诚实声明。** 参考报告做的是 **A 股全市场截面**因子（`rank_cs` 排名、分 10 组多头超额、
1 万个候选 / 581 轮 / 3 天）；本项目做的是 **OKX 单标的（或命名标的组）时序**信号，公共 K 线只有
`ts/o/h/l/c/vol` 六列。因此本设计做了三处**必须的**改写：① 所有截面算子换成时序算子（`rank_cs`
→ `ts_rank` 时序分位），量纲归一由 `ts_zscore` / 比值结构承担；② 「超额收益」换成绝对收益 +
成本后夏普 / Calmar，评价口径统一到仓库既有 `compute_metrics` / `compute_extended_metrics`；
③ 报告里 overnight（隔夜跳空）在 A 股是最强信号源，在 7×24 永续合约上跨根跳空极弱——本设计
保留该字段但**不给它任何先验加权**，机制族按加密市场重排（§2.3.2）。报告中 18.3% 年化超额等
数字**不构成本功能的预期收益**，文档与 UI 都不得引用。任何一处数据不可得时一律显式降级并如实
标注，绝不以合成数据冒充真实行情（沿用仓库「真实数据硬约束」）。

**非目标（v1 明确不做）。** ① 发现层不下任何真实订单、不写 watchlist / 自动交易成员 /
live-auto / ai-trade 的任何配置（D-2 + 导入边界测试）；② 不做多标的截面因子（本项目无截面
样本）；③ 不做分布式 / 多进程搜索（单进程 + 线程池足够，桌面端是主要形态）；④ 不做因子的
自动上实盘（入库到 `strategies` / `factors` 是显式动作，或由默认**关闭**的 `autoPromote` 开关
控制）；⑤ 不改任何既有表结构、不改既有 API 的响应形状。

---

## 二、技术设计

### 2.1 总体架构与数据流

```text
DiscoveryLab (apps/web/components/Discovery/*)
  │  REST  /api/py/discovery/*            （启动 / 暂停 / 恢复 / 停止 / 列表 / 候选 / 入库）
  │  WS    /api/ws/discovery/{runId}      （state / iteration / candidate / done）
  ▼
api/discovery.py ──► discovery/service.py（守门：总闸 / 并发 / 参数 / 工作量 / 标的合法性）
                          │
                          ▼
                 discovery/manager.py（进程内注册表；镜像 WeightTrainManager）
                          │  持有 N 个（默认 1）
                          ▼
                 discovery/runtime.py :: DiscoveryRun
                   ├─ 数据：agent.services.load_range_candles → candles_to_df（真实 OKX，进程内缓存）
                   ├─ 每轮四步：
                   │    ① generate  discovery/evolution.py + searchers/*      （五维演化）
                   │    ② review    discovery/review.py + llm_agents.py       （规则 + LLM 精判）
                   │    ③ validate  discovery/validation.py                   （硬编码闸，threadpool）
                   │    └ ④ commit  discovery/repositories.py                 （单事务：候选 + 迭代 + 检查点）
                   ├─ FSA：discovery/fsa.py（每 K 轮扫骨架频次 → 冻结）
                   ├─ 事件：_push → 环形缓冲 + 订阅者 Queue 扇出
                   └─ 调度：wall-clock 间隔 + 数据刷新 + 协作式取消 + 必达终态
```

四条纪律，全部照抄仓库既有范式，不得省略：

1. **CPU 密集全走 `run_in_threadpool`**（表达式求值 / 评估 / 回测），绝不阻塞事件循环。
2. **每个 run 必达终态**（`succeeded` / `failed` / `cancelled` / `stopped`）；落库失败也必须先置终态
   再重试一次，绝不让 WS 永久悬挂（`weight_train_manager` H-B1 教训）。
3. **协作式取消**：`request_cancel()` 置标志，四步之间与每个候选之间检查；关停走
   「置标志 → 带超时 join → `force_cancel` 兜底」三段（H-B4）。
4. **启动对账**：进程启动时 `discovery` 表里 `status="running"` 的行必是上次崩溃残留，按
   `auto_resume` 决定「自动续跑」还是「置 `paused` + `pauseReason="process_restart"`」，
   绝不留幽灵进行中（镜像 `_reconcile_orphan_ai_trade_runs`，且**整段 try/except、失败仅 warning**）。

   **总闸联动（v2 修正，评审 P1-8）**：自动续跑**必须**先读 `settings.discovery_enabled`。
   总闸为 `False` 时，无论 run 自己的 `autoResume` 是什么，一律置
   `paused` + `pauseReason="disabled_by_config"`，**绝不**拉起后台循环。理由：写类端点 403 却在后台
   继续烧 LLM 配额与 OKX 配额，是「关了但没关」的假安全姿态。判定顺序固定为
   `discovery_enabled` → `run.autoResume` → 续跑，任一为假即 `paused`，且 `pauseReason` 如实区分
   （`disabled_by_config` / `process_restart`），UI 据此给不同的下一步引导。

### 2.2 D-1：`expr` 通用表达式因子——与既有因子层零摩擦的桥

现有 `FACTOR_LIBRARY` 是 12 条 `FactorSpec` 的固定目录，`Factor` 表行只存 `factor_key + params_json`。
新增**一条**库条目即可让任意挖掘表达式复用整条管线：

```python
# engine/factors/library.py（新增，约 25 行）
def _expr_factor(df: pd.DataFrame, params: dict) -> pd.Series:
    """通用表达式因子：params["expr"] 为 discovery 规范式；解析/求值失败 → 全 NaN（安静降级）。

    降级契约（v2 硬约束，评审 P1-1）：任何失败路径都必须返回
    ``pd.Series(np.nan, index=df.reset_index(drop=True).index)``——**与 df 等长、index 0..n-1**。
    既有 ``test_factor_library.py`` 的因果性 / 形状两条用例会以 ``params=None`` 遍历全库到本条目，
    返回空 Series 或长度不符即打红。
    """
    # 延迟导入 + **直达子模块**（绝不 `from ...discovery import ...`）：避免 discovery/__init__
    # 的再导出把 validation → engine.factors.evaluation 整条链拉起，形成 engine ↔ discovery
    # 回边（评审 P2-7）。expression.py 只依赖 numpy/pandas，无反向依赖。
    from ...discovery.expression import evaluate_expression_text
    return evaluate_expression_text(df, str((params or {}).get("expr", "")))

FACTOR_LIBRARY["expr"] = FactorSpec(
    "自动挖掘表达式因子", "discovered", "long",
    {"expr": {"type": "expr", "default": "", "label": "因子表达式", "readonly": True}},
    _expr_factor, hidden=True)
```

`FactorSpec` 增一个 `hidden: bool = False` 字段（末位带默认值 ⇒ 既有 12 条构造零改）。**必须**同步
处理下列全部四个「遍历 `FACTOR_LIBRARY`」的现场（漏一个就会出事，已逐一核实）：

| 现场 | 现状 | 必须的改法 | 漏改的症状 |
|---|---|---|---|
| `library.available_factors()` | 全量返回 | 过滤 `hidden` | Agent primer / 因子目录出现一条永远算不出值的空表达式 |
| `agent/tools/factor_tools.py:46 FACTOR_KEY_ENUM` | `tuple(FACTOR_LIBRARY)` | 换 `visible_factor_keys()` | Agent 工具枚举里出现 `expr`，模型会用空参数调用 |
| `db/session.py::_seed_builtin_factors` | 遍历全库 seed | 跳过 `hidden` | 「我的因子」首屏多一条空壳内置因子，且不可删 |
| `engine/factors/mining.py::default_candidates` | 遍历全库 × 网格 | 跳过 `hidden` | 经典挖掘每次多扫一个全 NaN 候选 |

**⚠️ 上表四处改点会直接打红两条既有测试**（v2 新增，评审 P1-1）。它们**必须**在同一提交里同步更新，
且这两处更新本身就是「过滤真的生效了」的门禁，绝不是为了让测试变绿而放宽断言：

| 既有测试 | 现断言 | 必须改成 |
|---|---|---|
| `tests/test_factor_library.py::test_catalog_consistency` | `{f["key"] for f in available_factors()} == set(FACTOR_LIBRARY)` | `== set(visible_factor_keys())`，并**追加**断言 `"expr" in FACTOR_LIBRARY and "expr" not in visible_factor_keys()` |
| `tests/test_factor_mining.py::test_default_candidates_cover_whole_library` | `{c["factorKey"] for c in default_candidates("1H")} == set(FACTOR_LIBRARY)` | `== set(visible_factor_keys())`，并**追加**断言候选集不含 `expr` |

同文件的 `test_compute_factor_is_strictly_causal` / `test_compute_factor_shape_matches_df` 会以
`params=None` 遍历到 `expr`，**不需要改测试**——但 `_expr_factor` 的降级返回必须满足上文的等长同 index
契约，否则它们也红。`test_factor_library_expansion.py` 的 `len(FACTOR_LIBRARY) == 20` 是**既有红灯基线**
（该扩展从未落地），加 `expr` 后仍红，**不算本特性回归**；须在干净 HEAD worktree 实测对账后如实标注。

**不需要**改的（已核实）：`is_bar_compatible`（`expr` 非 intraday_only ⇒ 恒兼容）；
`mining._merge_key`（键含 params ⇒ 不同表达式天然不同键）；
`factors/specs.py::validate_factor_key`（key 在库中即通过）。

**⚠️ v1 在此处有一条事实性错误，v2 纠正（评审 P0-3）。** v1 写「`scoring.member_names` 已优先取
`member.get("name")` ⇒ 入库时写 `name="expr:<hash8>"` 即得稳定互异列名」——**该链路走不通**：
`specs.validate_members` 的返回构造只保留 `{factorKey, params, weight, direction}` 四键，
**`name` 会被无声丢弃**，而挖掘组合入 `factor_sets` 必经此边界（`factors/service.py` 的
323 / 427 / 452 / 550 四个调用点）。落库后 `member_names` 只能回落 `factorKey`；由于挖掘组合的
**每个成员 `factorKey` 都是 `expr`**，列名会退化成 `expr` / `expr#2` / `expr#3` 这种纯位置序：
成员一旦重排，`assert_weights_cover` 的键集相等检查照样通过，训练权重却被静默错配到别的因子上。

因此 `factors/specs.py::validate_members` 必须做**两处**加法（都只在 `factorKey=="expr"` 分支生效，
非 `expr` 成员逐字节行为不变，保零回归）：

1. **透传并强制 `name`**：`expr` 成员必须带 `name`，格式固定 `expr:<hash8>`（`<hash8>` =
   `expr_hash(node)[:8]`），正则 `^expr:[0-9a-f]{8}$` 校验；缺失或格式错 → 400
   `FACTOR_MEMBER_NAME_REQUIRED`。透传后写进返回 dict，使 `member_names` 拿到稳定且与表达式绑定的
   列名——**列名不再依赖成员顺序**。非 `expr` 成员不带 `name`，返回形状与今天逐字节一致。
2. **校验表达式可解析**：`params["expr"]` 必须能被 `parse_expression` 解析，否则 400
   `FACTOR_EXPR_INVALID`（边界一次性校验，进入内部后信任）。

`db/models.py` 的 `FactorSet.members_json` 列注释同步从 `[{factorKey,params,weight,direction}]`
更新为 `[{factorKey,params,weight,direction,name?}]`（纯注释，无迁移）。

### 2.3 因子表达式 AST（`discovery/expression.py`）

#### 2.3.1 节点与语法

```python
@dataclass(frozen=True)
class Node:
    op: str                      # "field" | "const" | 算子名
    field: str = ""              # op == "field"
    const: float | None = None   # op == "const"
    window: int | None = None    # 时序算子窗口（必须落在 WINDOW_LADDER 上）
    args: tuple["Node", ...] = ()
```

**字段（叶子，全部从 `ts/o/h/l/c/vol` 派生，量纲已标注）：**

| 字段 | 定义（严格因果） | 量纲 |
|---|---|---|
| `close` / `open` / `high` / `low` | 原始列 | price |
| `volume` | 原始 `vol` | volume |
| `ret` | `c/c[-1]-1` | ratio |
| `overnight` | `o/c[-1]-1`（永续上极弱，保留但无先验加权） | ratio |
| `amplitude` | `(h-l)/c` | ratio |
| `body` | `(c-o)/c` | ratio |
| `up_shadow` | `(h-max(o,c))/c` | ratio |
| `down_shadow` | `(min(o,c)-l)/c` | ratio |
| `hl_ratio` | `h/l-1` | ratio |
| `turnover` | `vol*c` | quote |
| `typical` | `(h+l+c)/3` | price |
| `hour_sin` / `hour_cos` | `sin/cos(2π·UTC小时/24)`（日内周期性；非日内 bar 恒 0） | ratio |

**算子：**

| 算子 | 元数 | 窗口 | 语义 | 量纲规则 |
|---|---|---|---|---|
| `ts_mean` `ts_std` `ts_sum` `ts_max` `ts_min` `ema` | 1 | 是 | 滚动/指数统计 | 同入参（`ts_std` 入 price ⇒ price） |
| `ts_zscore` | 1 | 是 | `(x-mean)/std` | ratio（**归一化出口**） |
| `ts_rank` | 1 | 是 | 窗口内时序分位 `[0,1]` | ratio（**报告 `rank_cs` 的时序对应物**） |
| `ts_skew` | 1 | 是 | 滚动偏度 | ratio |
| `delta` | 1 | 是 | `x - x[-N]` | 同入参 |
| `roc` | 1 | 是 | `x/x[-N]-1` | ratio |
| `ts_delay` | 1 | 是 | `x[-N]` | 同入参 |
| `ts_corr` | 2 | 是 | 滚动 Pearson | ratio |
| `add` `sub` | 2 | 否 | 元素加减 | **两侧量纲必须相同**，否则拒绝 |
| `mul` | 2 | 否 | 元素乘 | 至少一侧 ratio，否则拒绝（price×price 无意义） |
| `div` | 2 | 否 | 元素除（分母 0 → NaN） | 同量纲 ⇒ ratio；异量纲 ⇒ 拒绝，除非分母 ratio |
| `neg` `abs` `sign` `log1p` | 1 | 否 | 一元 | `sign` ⇒ ratio；其余同入参（`log1p` 要求 ratio） |

`WINDOW_LADDER = (3, 5, 8, 13, 20, 30, 45, 60, 90, 120, 180, 240)`。窗口**只能**取梯子上的值：
它把连续参数空间离散化，使「同结构不同窗口」的哈希不会爆炸，也让参数扰动有明确的「走一格」语义。

**复杂度护栏**：`MAX_DEPTH = 5`，`MAX_NODES = 15`，`MIN_NODES = 3`（报告的「最小复杂度门槛」），
同一表达式内不同窗口的数量 ≤ 4。

#### 2.3.2 机制族（13 个，按加密永续重排）

`MECHANISM_FAMILIES`：`trend_persistence`（趋势持续）、`momentum_reversal`（短周期反转）、
`volatility_regime`（波动率状态）、`range_compression`（波动压缩—扩张）、`volume_price_divergence`
（量价背离）、`liquidity_impact`（流动性冲击）、`breakout_structure`（突破/区间结构）、
`shadow_pressure`（影线压力）、`mean_reversion_band`（通道均值回归）、`cross_scale_confirm`
（跨尺度确认）、`intraday_seasonality`（日内周期性）、`gap_premium`（跨根跳空，永续弱）、
`orderflow_proxy`（主动买卖代理——**必须在 UI 与 prompt 里显式标注为代理**：OKX 公共 K 线不含
主动买卖量，只能用 `body × volume` 之类近似）。

每族给一组「原型模板 + 偏好字段 + 偏好算子」，供随机探索的数据驱动分布与 LLM 机制引导使用。
族归属由 `classify_mechanism(node)` 纯函数按「使用了哪些字段 + 外层算子」打分裁定（唯一真源，
UI 分类、LLM prompt 覆盖度统计、候选表分组全部读它）。

#### 2.3.3 规范式、哈希与骨架

- `to_text(node) -> str`：前缀式，参数内联，如 `sub(ts_mean(overnight,20),delta(amplitude,5))`。
  交换律算子（`add` / `mul`）的两个子树按 `to_text` 字典序排序后再序列化 ⇒ `add(a,b)` 与
  `add(b,a)` **必然同哈希**（否则去重形同虚设）。
- `parse_expression(text) -> Node`：严格解析，任何非法 token / 未知算子 / 窗口不在梯子上 →
  抛 `ExpressionError`（含定位信息：出错位置与期望符号）。
- `expr_hash(node) -> str`：`sha256(to_text(node))[:16]`。**已测集合的唯一键**。
- `skeleton(node) -> str`：叶子 → `?`、窗口 → `_`，如 `sub(ts_mean(?,_),delta(?,_))`。FSA 的键。
- `subtrees(node) -> list[(path, Node)]`：交叉与变异的替换点枚举。
- `evaluate(node, ctx) -> np.ndarray`：带**子表达式记忆化**（键 = `to_text`），交叉产生的共享子树
  只算一次；任何数值异常（除零 / overflow / 非有限）→ 该节点结果置 NaN 并向上传播，绝不抛。
  求值全程 `numpy`/`pandas` 的 rolling/ewm/shift，**严禁**任何 `shift(-n)` 式前视。

#### 2.3.4 中文渲染（`discovery/expression_text.py`）

`describe(node) -> str` 把表达式渲染成一句中文，如
「20 根隔夜溢价均值 减去 5 根振幅差分」。**唯一真源**：LLM 审查 prompt、候选表、详情抽屉、
入库后的因子 `name`/`notes` 全部读它，杜绝「prompt 里的解释和 UI 里的解释不是一回事」。

### 2.4 四条搜索轴与 `Searcher` 协议

需求「策略 = 因子策略 / 常规策略 / 组合策略均可」落成四条轴，共用同一套循环：

| 轴 key | 基因 | 验证方式 | 入库目标 |
|---|---|---|---|
| `factor` | `ExprGenome`（表达式树） | `evaluate_factor_key` 的 IC/IR/单调性联合过滤 | `factors` 行（`factor_key="expr"`） |
| `factor_combo` | `ComboGenome`（2–5 个**已入库**因子 + 权重 + 阈值） | 组装 `factor_score` 参数 → `run_backtest` 纯回测 | `factor_sets` + 配套 `factor_score` 策略 |
| `strategy_param` | `ParamGenome`（注册表某原子策略 + 参数向量） | `run_backtest` 纯回测 | `strategies` 行（该模板 + params 覆盖） |
| `strategy_combo` | `RotationGenome`（2–4 个**已入库**策略 + 轮动策略参数） | combo 路径回测 | `strategies` 行（`kind="combo"`） |

```python
class Searcher(Protocol):
    kind: str
    def seed(self, rng, ctx) -> Genome: ...          # 冷启动/随机探索
    def mutate(self, rng, parent, ctx) -> Genome: ...
    def crossover(self, rng, a, b, ctx) -> Genome: ...
    def perturb(self, rng, parent, ctx, momentum) -> Genome: ...
    def from_llm(self, payload: dict, ctx) -> Genome | None: ...   # 解析 LLM 产出，非法返回 None
    def genome_hash(self, g: Genome) -> str: ...
    def describe(self, g: Genome) -> str: ...        # 中文一句话
```

`ParamGenome` 的变异 = 在 `params_schema` 声明的 `[min,max,step]` 内走 1–3 步；交叉 = 逐参数随机
取父本；扰动 = 沿动量方向走。`ComboGenome` / `RotationGenome` 的变异 = 换一个成员 / 调一个权重 /
调一个阈值；交叉 = 成员集合的均匀交叉后去重截断。**依赖顺序**：`factor_combo` 只从本 run 已入库
（`kept=True`）的因子里取材，`strategy_combo` 只从已入库策略里取材——因此 run 配置里若同时启用
多轴，循环会按 `factor → factor_combo → strategy_param → strategy_combo` 的**优先级分配预算**，
且上游库存不足 `min_pool`（默认 3）时该轴本轮预算自动让渡给上游轴（并在迭代记录里如实标注
`yielded_by_pool`，绝不静默空转）。

### 2.5 五维演化与自适应预算（`discovery/evolution.py`）

默认预算与报告一致：`mutate 25% / crossover 25% / perturb 15% / random 15% / llm 20%`。

- **冷启动**：`kept` 数 < `cold_start_min`（默认 5）时，`random` 与 `llm` 各占 50%，其余为 0。
- **父本选择**：从 `kept` 候选按 `score = |IR|`（因子轴）或 `sharpe`（策略轴）做**锦标赛选择**
  （tournament size 3），而非全局最优——避免过早收敛。
- **数据驱动特征分布**（报告「第一次自适应」）：随机探索的字段抽样权重 = `1 + 2 × 该字段在 kept
  候选中的出现率`，窗口抽样分布 = 以 kept 候选窗口的中位数为中心的梯子邻域。
- **动量追踪器**（参数扰动方向）：对每个 `(skeleton, 窗口槽位)` 维护 EWMA 梯度
  `g ← β·g + (1-β)·(Δscore / Δladder_index)`（`β=0.7`）；步长
  `step = clip(round(base_step × |g| / (mean|g| + ε)), 1, 4)`，方向取 `sign(g)`。状态**有界**：
  只保留最近活跃的 64 个键（LRU），进检查点。
- **自适应调整**（每 `adapt_every=10` 轮跑一次 `adapt_budget(counters) -> budget`，纯函数）：

  | 触发条件（用最近 3 个调整窗口的计数） | 动作 |
  |---|---|
  | 近邻入库率 > 全局入库率 × 2 | `mutate` `perturb` 各 +0.05（各自封顶 0.40） |
  | 因「与已入库高相关」被拒的比例 > 0.30 | `random` `llm` 各 +0.05 |
  | 存在 0 覆盖的机制族且已跑满 `mech_probe_after`（默认 50）轮 | `llm` +0.05 |
  | 已覆盖 ≥3 个机制族且入库率连续 3 窗下降 | `crossover` +0.05 |

  所有分量夹到 `[0.05, 0.40]` 后归一化到 1.0。**纯函数 + 输入是计数器**⇒ 可单测、可复现。

### 2.6 审查层（`discovery/review.py` + `discovery/llm_agents.py`）

**第一道：确定性规则**（纯函数，逐条给出中文 `rejectReason`）：

| 规则 | 判据 |
|---|---|
| `degenerate_outer` | 外层算子退化：`ts_rank(ts_rank(x,N),M)`、`sign(sign(x))`、`abs(abs(x))`、`ts_mean(ts_mean(x,N),M)`（同算子直接嵌套） |
| `homogeneous_simplify` | 可化简：`sub(x,x)`、`div(x,x)`、`add(x,0)`、`mul(x,1)` |
| `dimension_mismatch` | 违反 §2.3.1 量纲规则 |
| `complexity_out_of_range` | 深度 / 节点数 / 窗口种类越界 |
| `duplicate` | `expr_hash` 已在本 run 的已测集合中 |
| `fsa_banned` | 骨架在 FSA 冻结名单中 |
| `variant_cap` | 同骨架已入库变体数 ≥ `variant_cap`（默认 6） |
| `skeleton_blacklisted` | 该骨架已测 ≥20 次且入库 0 次（失败模式库） |
| `constant_or_empty` | 求值后有限值比例 < `min_coverage`（默认 0.6）或方差为 0 |

**⚠️ `constant_or_empty` 不是纯函数规则**（v2 澄清，评审 P2-3）。它需要真正在数据窗上求值，因此：
① 它**单独排在规则链末位**，前 8 条纯函数规则先把绝大多数候选挡掉，只有幸存者才付出求值代价；
② 它与验证层**共用同一份记忆化求值结果**（`evaluate(node, ctx)` 的记忆化键 = `to_text`，见 §2.3.3），
求值发生一次、两处复用，**严禁**审查算一遍验证再算一遍；③ 它随验证层一起在
`run_in_threadpool` 中执行，绝不在事件循环里跑。

**第二道：LLM 精判（抽样，可关）**。从通过第一道的候选中随机抽 ≤ `llm_review_sample`（默认 5）条，
连同 `describe()` 的中文渲染与本 run 的机制覆盖摘要，一次调用交给审查子代理，返回逐条
`{hash, accept: bool, reason: str, mechanism: str}`。裁决语义**只减不增**：`accept=false` ⇒ 拒
（`rejectReason="llm_review:<reason>"`）；`accept=true` 不代表放行，仍要过验证层。
**fail-open**：超时 / 报错 / 解析失败 / 未配置 LLM 服务 ⇒ 视为全部 `accept=true` 并在迭代记录里
标 `llmDegraded=true`（附原因码），循环继续。

### 2.7 验证层（`discovery/validation.py`，硬编码 · fail-closed）

窗口切分：run 的数据窗按时间切 `in-sample 70% / out-of-sample 30%`（OOS 是**尾段**，严格晚于 IS）。

#### 2.7.0 IS/OOS 求值与定号协议（v2 新增，评审 P0-1 / P1-6 · **承重**）

v1 只说「IS/OOS 切分」与「验证期已冻结方向」，没写清怎么算——按最直觉的写法（切片后各调一次
`evaluate_factor_key(..., direction="auto")`）会同时踩两个坑，其中一个让 F6 整条空转。故把协议钉死：

**坑一（P0-1，安全闸空转）**：`evaluation._eval_horizon` 返回的 `rankIc` 是**已乘过 `dir_mult`
的方向调整值**（`evaluation.py:179-181`），而 `resolve_direction` 的定义就是「取符号使主 horizon
RankIC 为正」（`evaluation.py:186-198`）。若 IS 与 OOS 各自以 `auto` 定号，**两段的 `rankIc` 都会被
构造成非负**，F6 的「同号」判据恒真——最强的一条样本外防线变成装饰。

**坑二（P1-6，指标不可比）**：`compute_factor` 每次都从传入 df 的头部重算 rolling。把 K 线切片后
再各自 `compute_factor`，OOS 段前 `max_window`（梯子顶格 240）根全成 NaN；默认 30% 尾段约 1200 根，
白丢 20%，且两段算出的**不是同一条序列**。

**唯一合法实现（下游照此写，不得变体）：**

```python
# 1) 在【全窗】上求值一次——rolling 暖机只发生一次，两段共用同一条序列
values = evaluate_expression(node, ctx_full)          # np.ndarray，与 df_full 等长
split   = int(len(df_full) * is_ratio)                # is_ratio 默认 0.7

# 2) 按下标切片（df 与 values 必须**同步**切，绝不各切各的）
df_is,  v_is  = df_full.iloc[:split],  values[:split]
df_oos, v_oos = df_full.iloc[split:],  values[split:]

# 3) 在【IS 段】一次性定号并冻结
direction = resolve_direction(v_is, df_is["c"].to_numpy(float),
                              "auto", primary_horizon=horizon)

# 4) 两段都用**同一个冻结字面量**评估——严禁再传 "auto"
res_is  = evaluate_factor(df_is,  pd.Series(v_is),  direction, factor_key="expr", ...)
res_oos = evaluate_factor(df_oos, pd.Series(v_oos), direction, factor_key="expr", ...)
```

三条随之而来的硬约束：

- **必须调 `evaluate_factor`（外部传值），不得调 `evaluate_factor_key`（内部重算）。** 后者只适合
  单段评估，本层用它即等于重新引入坑二。§2.14 的调用链已按此更正。
- **`forward_return` 的右端截断天然阻断跨段泄漏**：在 `df_is` 上算 h 步前瞻收益时，IS 尾部最后 h 根
  返回 NaN 并被 `_paired` 剔除（`evaluation.py:92-107`），**不会**读到 OOS 的第一根。因此这里不需要
  额外留 gap，也**严禁**为了「补上那 h 根」去拼接两段——那才是泄漏。
- **F6 因此是真正的样本外检验**：IS 的 `rankIc` 因定号而恒非负（这是选择的代价，由 F11 的多重检验
  惩罚约束），OOS 的 `rankIc` 在冻结方向下**可正可负**，同号与强度保留才有信息量。

**因子轴联合过滤（11 条，全部通过才入库）：**

| # | 条件 | 默认阈值 | 出处 |
|---|---|---|---|
| F1 | 覆盖率 ≥ `minCoverage` 且配对样本数 ≥ `minSamples` | 0.6 / 200 | 空样本安全口径 |
| F2 | IS `|RankIC @ 主 horizon|` ≥ `minAbsIc` | 0.03 | 报告 `|IC|>0.03` |
| F3 | IS `|IR|` ≥ `requiredIr`（见 F11 惩罚） | 0.5 | 报告「夏普>0.5」的时序对应 |
| F4 | 有效分段 ≥6 且 `|tStat|` ≥ `minAbsT` | 2.0 | `evaluation._segment_ir` 已产出 |
| F5 | 分段 RankIC 同号占比 ≥ `minSignStability` | 0.6 | 报告「年度稳定性」 |
| F6 | OOS `RankIC` 与 IS **同号**，且 `|OOS RankIC|` ≥ `oosRetain × |IS RankIC|` | 0.5 | 报告「近 9/12 月超额>0」 |
| F7 | 分位单调性 ≥ `minMonotonicity` | 0.6 | 已有 `monotonicity` |
| F8 | 成本后 top-bottom 价差 > `costMult × roundTripCost`，其中 **`roundTripCost = 2 × (fee_bps + slip_bps) / 1e4`**（⚠️ 见下方量纲注） | 2.0 × 0.0014 = 0.0028 | 报告「单边千一」的等价物 |
| F9 | 换手代理 `turnoverProxy` ≤ `maxTurnover`（**本设计自算，见下方定义**） | 0.35 | 防高频噪声因子 |
| F10 | 与**每一条**已入库因子：分段 IC 序列相关 < 0.70 **且** 因子值序列相关 < 0.85 | — | 报告「IC 相关性<0.70」+ 值级去重 |
| F11 | 多重检验惩罚：`requiredIr = minAbsIr × (1 + k·log10(max(trials,10)/10))`，`k=0.15` | — | **本设计新增**，见 §8 R-1 |

**⚠️ F8 量纲注（v2 修正，评审 P0-2 · 承重）。** `settings.default_taker_fee_bps` (=5.0) 与
`default_slippage_bps` (=2.0) 的单位是 **bps**——`engine/simulator.py:108-109` 是
`self.fee_rate = taker_fee_bps / 1e4` 才拿去用的；而 `topBottomSpread`
（`evaluation.py:158`）是**小数收益差**。v1 写的 `2.0 × (2×(fee+slip))` 直译即
`2.0 × 2 × 7.0 = 28`，等于要求 top-bottom 价差 > 2800%，**没有任何候选能通过**，DoD #4 永不可达，
而且在 24/7 长跑里表现为「搜索很难」而不是报错——是最难被发现的一类失效。故 `roundTripCost`
**必须**先除 `1e4` 再比较：

```python
fee_bps, slip_bps = settings.default_taker_fee_bps, settings.default_slippage_bps
round_trip_cost = 2.0 * (fee_bps + slip_bps) / 1e4    # 生产默认 = 0.0014（14 bps 往返）
passed_f8 = (stat.topBottomSpread or 0.0) > thresholds["costMult"] * round_trip_cost
```

`test_discovery_validation.py` **必须**有一条量纲回归用例：以生产默认成本构造
`topBottomSpread = 0.003` 的桩，断言 F8 通过；`0.001` 的桩断言 F8 不通过。数值门槛落在千分位而非
两位数，即证明没有再犯 1e4 错配。

**⚠️ F9 定义（v2 补齐，评审 P1-2）。** v1 把「换手代理」当成既有指标引用，但仓库里**没有**：
`evaluation.py` 的模块 docstring 提过这个词，`FactorHorizonStat` / `FactorEvalResult` 两个 dataclass
里**没有任何 turnover 字段**（全包 grep `turnover` 零命中）。故本层自算，定义钉死如下（纯函数，
落 `discovery/validation.py`，与 F10 的值级相关共用同一份因子值，不额外求值）：

```python
def turnover_proxy(values: np.ndarray, *, quantiles: int = 5) -> float | None:
    """分位档位的逐根切换率——因子隐含的换仓频率代理，[0,1]，越大越高频。

    以 `pd.qcut` 把有限值分成 `quantiles` 档（与 F7 单调性同一分档口径），统计相邻两根之间
    档位发生变化的比例。有限样本 < 2 → None（视为 F9 不可判定 ⇒ **拒绝**，fail-closed）。
    """
```

判定：`turnoverProxy is None` ⇒ 记 `rejectReason="F9"` 并拒（验证层 fail-closed，不可判定即不入库）。
`turnoverProxy` 与 `trialsAtAccept` 一并写入 `metrics_json`，候选详情必须展示——它是用户判断
「这个因子是不是靠高频噪声取胜」的唯一依据。

**策略三轴联合过滤：**

| # | 条件 | 默认阈值 |
|---|---|---|
| S1 | IS 与 OOS 成交数各 ≥ `minTrades` | 20 / 8 |
| S2 | IS 与 OOS 总收益均 > 0 | — |
| S3 | IS `sharpe` ≥ `minSharpe` 且 OOS `sharpe` ≥ `minSharpe × 0.5` | 0.8 |
| S4 | `calmar` ≥ `minCalmar` | 1.0 |
| S5 | `maxDrawdown` ≥ `-maxDdAllowed` | -0.35 |
| S6 | 与每条已入库策略的逐根收益相关 < `maxEquityCorr` | 0.70 |
| S7 | 手续费 / 滑点用 `settings` 生产默认（`_resolve_costs`），**严禁**用 0 成本 | — |

**执行细节**：每个候选的验证在 `run_in_threadpool` 中执行，单候选墙钟超时
`candidateTimeoutS`（默认 20s）；超时 ⇒ `rejectReason="validate_timeout"`，绝不拖垮整轮
（沿用 `mine_factors` 的「单候选异常不拖垮整轮」纪律）。任一步抛异常 ⇒ 记
`rejectReason="eval_error:<类型>"` 并继续。

### 2.8 FSA 频繁子树规避与失败模式库（`discovery/fsa.py`）

- 每 `fsaEvery`（默认 25）轮，统计**已入库**候选的骨架频次；`share > fsaThreshold`（0.15）的骨架
  进冻结名单，记 `{skeleton, share, bannedAtIter}`。
- **与报告的有意偏离**：冻结带 TTL（`fsaBanTtlIters`，默认 150 轮）——报告是永久冻结，但报告只跑了
  3 天；24/7 长跑下永久冻结会把搜索空间越切越小直至饿死。TTL 到期后该骨架重新参与频次统计（若仍
  超阈值则立即再冻结）。TTL 与冻结历史全部落迭代记录，UI 可回溯。
- **失败模式库**：`skeletonStats[skeleton] = {tested, kept}`；`tested ≥ 20 且 kept == 0` ⇒ 进
  `skeletonBlacklist`（生成阶段直接跳过，见 §2.6 规则）。已测哈希集合来自 DB（D-3），生成阶段
  用它做 O(1) 去重。

### 2.9 循环编排与 24/7 调度（`discovery/runtime.py`）

单轮时序（`_run_iteration`）：

```text
0. guard      : 检查取消标志 / 暂停标志 / 数据新鲜度
1. plan       : budget = adapt_budget(counters) if iter % adapt_every == 0 else 沿用
                axis_plan = allocate_axes(config, pool_sizes)      # §2.4 优先级 + 让渡
2. generate   : 逐轴按 budget 抽操作 → Searcher.{seed,mutate,crossover,perturb} → 候选
                llm 份额合并成 **一次** 生成子代理调用（K 条）
3. review     : 规则过滤 → 抽样 LLM 精判
4. validate   : threadpool 逐候选跑 §2.7；一旦 kept，立刻把它加进「已入库池」的内存快照
                （使同轮后续候选就能与它做相关性去重，避免同轮双胞胎双双入库）
5. fsa        : 每 fsaEvery 轮扫骨架频次
6. commit     : 单事务写 discovery_candidates(N) + discovery_iterations(1) + runs.checkpoint_json
7. emit       : state / iteration / candidate(≤ maxCandidateEvents 条) 事件
8. schedule   : next_at = now + iterationIntervalS；若上轮耗时 > 间隔，**不排队补跑**，
                立即开下一轮并在迭代记录标 overrun=true
```

**24/7 关键点：**

- **数据刷新**：每 `dataRefreshS`（默认 6h）重新分页拉真实 OKX K 线，把窗口整体右移（`rolling`
  模式）或仅追加（`fixed` 模式，默认 `rolling`）。刷新失败（OKX 不可达）⇒ 本轮标
  `skipped_data` + 指数退避重试（上限 `dataRetryMaxS`=900s），**绝不**用旧数据假装新数据、更不
  合成数据；连续失败 `dataFailureLimit`（默认 12）次 ⇒ run 转 `paused(pauseReason="data_unavailable")`
  而非 `failed`（用户回来点恢复即可）。

  **捕获面（v2 修正，评审 P2-2）**：`agent.services.load_range_candles` 在「区间内无已确认 K 线」时
  抛的是 **`fastapi.HTTPException(400, EMPTY_RANGE)`**，不是 `OKXError`（`agent/services.py:108-110`）。
  后台循环只捕 `OKXError` 会让整个 run 崩成 `failed`。故刷新的 except 面必须同时覆盖
  `OKXError`、`HTTPException`、`asyncio.TimeoutError` 与 `OSError`，四者一律走上面的退避 / 转
  `paused` 路径。
  **run 自持数据帧**：`factors.service._candle_cache` 的 TTL 只有 90s、cap 只有 16
  （`factors/service.py:76-78`），它是**请求级**缓存，**不是** run 级缓存，绝不能拿它当 R-3 的护栏。
  `DiscoveryRun` 必须在自己的实例上持有当前窗口的 `DataFrame`，只在 `dataRefreshS` 到点时才重新取数。
- **窗口右移后的可比性**：`kept` 候选的指标是在**各自入库时的窗口**上算的。为避免「今天的 0.6 IR
  和上周的 0.6 IR 混在一张榜上」，每次数据刷新后对已入库候选做一次**轻量复评**
  （`revalidateOnRefresh`，默认开，只重算 F2/F3/F6，不重跑全部 11 条）；连续 `demoteAfter`（默认 2）
  次复评不达标 ⇒ 标 `demoted=true`（保留记录、退出父本池、UI 显示「已退化」徽标）。这是长跑相对
  短跑最大的增量价值，不能省。
- **资源封顶**：`maxIterations`（0=无限）、`maxRuntimeHours`（0=无限）、`maxKept`（默认 200，到顶后
  新候选须**优于**当前最差者才能替换，形成有界精英池）、`maxCandidateRows`（默认 20000/run，超出时
  按「保留全部 kept + 最近 N 条 rejected」剪枝，剪枝量写入迭代记录）。

- **单轮墙钟预算**（v2 新增，评审 P2-4）：新增 `iterationTimeoutS`（默认 `600.0`，即默认间隔的 2 倍）。
  v1 只有 `candidateTimeoutS=20s`，最坏情况 `20s × candidatesPerIteration=40` = 800s，远超默认 300s
  间隔，却没有任何上限。到点后**停止取新候选**、把已完成的候选正常 commit、本轮记
  `truncatedByTimeout=true` 与被丢弃的候选数（**如实上报，绝不静默截断**），下一轮照常。这与
  `overrun`（本轮跑完但超过了间隔）是两件事，UI 分别展示。

- **线程池并发预算**（v2 新增，评审 P2-6）：验证层的 `run_in_threadpool` 与所有 API 请求**共用**
  anyio 的默认线程池。一个 24/7 挖掘 run 若无节制地并发提交候选，会与交易 / 回测 / 因子评估请求争抢
  线程，让交互式请求变慢——这是长跑特性最容易造成的隐性劣化。故发现层自持一个
  `asyncio.Semaphore(discovery_validate_concurrency)`（默认 `2`，硬上限 8），**所有**进入线程池的
  验证调用都必须先拿到信号量。DoD 追加一条对应验收（§7.3 第 15 条）。

### 2.10 检查点与断点续跑（`discovery/checkpoint.py`）

`checkpoint_json`（`discovery_runs.checkpoint_json`，**小而有界**，目标 < 64 KB）：

```jsonc
{
  "version": 1,
  "iteration": 137,
  "rngSeed": 20260805,                   // 每轮实际种子 = sha256(seed, iteration) → 可复现且续跑安全
  "budget": {"mutate":0.30,"crossover":0.25,"perturb":0.15,"random":0.10,"llm":0.20},
  "counters": {"tested":5421,"kept":37,"rejected":{"duplicate":1902,"F3":880,"...":0}},
  "momentum": [{"key":"sub(ts_mean(?,_),delta(?,_))#0","g":0.42,"seenIter":133}],  // ≤64 条 LRU
  "fsaBans": [{"skeleton":"...","share":0.19,"bannedAtIter":100}],
  "skeletonStats": [["sub(ts_mean(?,_),?)",{"tested":43,"kept":5}]],               // ≤256 条
  "mechanismCoverage": {"trend_persistence":6,"shadow_pressure":0},
  "dataWindow": {"startTs":1739000000,"endTs":1754400000,"lastRefreshTs":1754390000},
  "poolSizes": {"factor":37,"factor_combo":4,"strategy_param":6,"strategy_combo":1}
}
```

已测哈希**不进 JSON**（D-3）：`discovery_candidates` 上 `UniqueConstraint(run_id, genome_hash)`，
续跑时 `SELECT genome_hash WHERE run_id=?` 一次性载入内存 `set`（10 万条约 3 MB，可接受）。

**原子性**：第 6 步 commit 是**一个** DB 事务（候选 + 迭代 + 检查点同写同回滚），因此任意时点被杀
都只会丢「正在进行的那一轮」，已完成的轮次不会半写。续跑从 `checkpoint.iteration + 1` 开始。

**⚠️ 续跑期 kept 池重建（v2 新增，评审 P1-4 · 承重）。** v1 漏掉了一件事：F10 的双通道去重要求
新候选与**每一条**已入库因子比对「分段 IC 序列相关 < 0.70 **且** 因子值序列相关 < 0.85」，可是
`series_json` 只持久化 ≤24 点分段 IC 与 ≤10 桶分位（§5.3），**因子值序列根本没落库**，检查点也不含它
（它有几千个点，落进去就违反 §2.10 的 64 KB 有界目标）。于是进程重启 / `resume` 后，kept 池只剩元
信息，值级去重无从计算——长跑每重启一次就会开始收一批同质因子，而这恰恰是本特性的核心卖点所在。

故 `resume` / 自动续跑路径**必须**在开跑第一轮之前做一次显式重建，且**绝不**降级为「跳过值级去重」：

```text
resume 序列（必须按序，任一步失败即转 paused 并如实标注 pauseReason，绝不带残缺池开跑）：
 1. 载入 checkpoint（iteration / budget / momentum / fsaBans / skeletonStats / ...）
 2. SELECT genome_hash WHERE run_id=?  → 已测集合 set（D-3）
 3. 取数：按 checkpoint.dataWindow 重新拉真实 OKX K 线（同 §2.9 的捕获面与退避）
 4. 【重建】SELECT * WHERE run_id=? AND verdict='kept' AND demoted=0
    → 逐条从 genome_json 解析表达式 → 在当前窗口上求值一次
    → 重算并缓存 (值序列, 分段 IC 序列)，即 F10 两条通道各自的比对基准
    → 该步走 run_in_threadpool + §2.9 的并发信号量；maxKept 默认 200，单次重建墙钟预期 < 30s
 5. 重建期间 status 置 `running` 且 WS 首发的 state 带 `rebuilding=true`，UI 显示
    「正在重建候选池（i/N）」——绝不显示成「已在挖掘」
 6. 重建完成 → 正常进入 iteration = checkpoint.iteration + 1
```

**为什么不缓存到 DB 省掉这一步**：值序列在 `rolling` 窗口下每次数据刷新都会变（窗口右移即基准变），
落库的那份很快就是陈旧值；而 §2.9 的复评降级机制本来就要在每次刷新后重算 kept 的指标。故「重建」
与「刷新后复评」共用同一个函数 `rebuild_kept_pool(run, df)`，只有触发时机不同——这也是 v2 把它
提到承重条款的原因：**一个函数，两个调用点，不许有第二份实现。**

### 2.11 LLM 子代理装配、成本与失败姿态（`discovery/llm_agents.py`）

- **装配**：复用 `agent.providers.resolve_active_provider` + `agent.llm.factory.make_llm_client_from_config`
  （与 `AgentManager._resolve_client` 同款三级回落：DB 默认服务 → env → None）。未配置 ⇒ 生成/审查
  子代理整体停用，循环退化为纯演化（`llmDegraded="not_configured"`）。
- **受约束调用**（镜像 `ai_auto/decider.py`）：`llm.stream(tools=[单个工具])`，工具分别是
  `submit_factor_candidates` / `submit_review_verdicts`；自行累积 `tool_use_args_delta` 的
  `partial_json` 再 `json.loads`；无工具调用 / 多次调用（取首条）/ 入参非法 ⇒ 安全回落（生成：本轮
  llm 份额转给 random；审查：全 accept）。超时 `llmTimeoutS`（默认 60s），重试复用
  `agent.llm.retry.backoff_delay_ms`，最多 `llmMaxRetries`（默认 1）。
- **成本闸**：每轮**至多 2 次** LLM 调用（1 生成 + 1 审查）；`llmMaxCallsPerHour`（默认 24）为硬上限，
  触顶后本小时内跳过 LLM 份额并在 UI 显式提示（绝不静默）。每轮的 `promptChars` / `latencyMs` /
  `degradedReason` 落迭代记录，UI 可看「这一天烧了多少次调用」。
  **计数窗口的起算（v2 补齐，评审 P1-8）**：小时窗是**进程内**的滑动窗（`deque[float]` 存最近调用的
  单调时刻，逐出超过 3600s 的），**不进检查点**。因此进程重启 / 续跑后计数从 0 重新起算——这是有意
  选择：跨重启保留计数需要落库墙钟时刻，而墙钟不可信（用户可能关机三天）。代价是「反复重启可绕过
  小时闸」，用 `discovery_resume_delay_s`（默认 10s）与「每轮至多 2 次」的更硬约束共同兜底，
  且**必须**在 UI 的 LLM 轨迹卡上标注「本小时用量自进程启动起算」，不许让用户误以为是跨重启累计。
- **prompt 素材**（`discovery/prompts.py`，中文）：项目背景 + 字段/算子词表（**从 §2.3 的表自动生成，
  单一真源**）+ 当前机制覆盖 + 近期入库因子的中文渲染与指标 + 冻结骨架 + 失败模式摘要 + 本轮要
  几条。生成子代理被明确告知「只输出表达式与机制族标签，不要解释性长文」，审查子代理被告知
  「你只有否决权，不能替代硬编码验证」。

### 2.12 入库（promote）与成果管理（`discovery/promote.py`）

| 候选轴 | 入库动作 | 落库形状 |
|---|---|---|
| `factor` | 建 `Factor` 行 | `factor_key="expr"`, `params_json={"expr": "<规范式>"}`, `direction`（§2.7.0 在 IS 段冻结的 long/short）, **`category="discovered"`**, `source="mined"`, **`name="挖掘因子 <hash8>：<中文渲染截断至 100 字>"`**, `notes="discovery #<runId> iter <n> · <机制族>"` |
| `factor_combo` | 建 `FactorSet`（既有 service 会自动同步配套 `factor_score` 策略） | `members_json` 用**自包含快照**，成员必带 `name="expr:<hash8>"`（经 §2.2 加固后的 `validate_members` 透传，保证 `member_names` 稳定且与表达式绑定） |
| `strategy_param` | 建 `Strategy` 行 | `template_key=<模板>`, `params_json=<参数向量>`, `builtin=False` |
| `strategy_combo` | 建 `Strategy` 行 | `template_key="combination"`, `params_json=<成员+轮动参数>` |

**⚠️ 落库字段边界（v2 修正，评审 P1-5）。** 两处 v1 会越界：

1. **`name` 不能直接写中文渲染。** `Factor.name` 是 `String(128)`，而 `describe_text` 上限 512；
   深表达式的中文渲染轻易过 128。SQLite 不强制截断故不会报错，但契约被破坏、UI 列表与
   `MemberPalette` 的候选 chip 会破形。故入库名固定格式
   `f"挖掘因子 {hash8}：{describe[:100]}"`（总长 ≤ 128，`hash8` 保证互异且可溯源），
   **完整中文渲染留在候选表的 `describe_text` 与详情抽屉**，那里没有长度约束。
2. **`category` 不能写机制族。** `Factor.category` 承载的是既有类别词表
   （momentum / trend / reversal / volatility / volume / liquidity / mean_revert），而
   `MemberPalette` 的 `CategoryChips` 是从**库目录** `available_factors()` 派生的
   （`components/Factor/MemberPalette.tsx:151-155`）——13 个机制族名不会出现在任何 chip；更糟的是
   `factorApi.list({category})` 是服务端过滤，用户一点任何类别 chip，挖掘因子就会整体消失。
   故 `category` 一律写 **`"discovered"`**（与 `FACTOR_LIBRARY["expr"]` 的 `category` 同值，
   单一口径），机制族写进 `notes` 并由候选表的 `mechanism` 列承载真源。UI 侧在「我的因子」列表按
   `source=="mined"` 给独立徽标，不依赖 category。

**硬性不变式（安全）**：入库**只**写 `factors` / `factor_sets` / `strategies` 三张既有表，
**严禁**触碰 `strategy_runs`、watchlist、自动交易成员、live-auto / ai-trade 的任何配置。
`autoPromote` 默认 **false**（用户在候选库里勾选后点「入库」）；即便打开也只做上表动作。
入库是**幂等**的：同 `genome_hash` 重复入库返回既有实体 id 并回 200（不重复建行）。

「一键回测」：`POST /discovery/candidates/{id}/backtest` 复用既有
`agent.services.run_single_backtest`（策略轴，已入库后）或 `run_factor_score_backtest`（因子组合轴），
产出**持久化的** run，于是发现出来的东西可以直接进「运行历史」和 K 线回放 —— 这正是需求
「能够进行回溯和管理」的落点。

### 2.13 前端信息架构与交互设计

**入口**：`NavRail` 第三项，label「策略与因子挖掘」，图标为内联 SVG 雷达（同现有两枚图标的
1.6 stroke / 20×20 / currentColor 规格，不引第三方图标库）。`activeView` 类型加 `"discovery"`。
`page.tsx` 按现有 `hidden` 互斥模式挂载 `DiscoveryLab`（首次激活后常驻，保态）。

**工作区**（`DiscoveryLab`，镜像 `FactorLab` 的外壳：顶部目标/周期/区间 + 左侧分区导航）：

| 分区 | 内容 |
|---|---|
| `overview` 总览 | 当前运行卡（运行时长 / 迭代数 / 已测 / 已入库 / 入库率 / 下一轮倒计时 / 数据刷新状态）、机制族覆盖热力、近 24h 入库时间线、快捷「新建挖掘」 |
| `runs` 挖掘任务 | 任务列表（状态徽标 / 配置摘要 / 起止 / 计数）+ 启动器 `DiscoveryLauncher`（两步式，镜像 `BacktestConfigModal` 的编辑体验：第一步标的·周期·窗口·轴选择，第二步阈值与 LLM 用量，含「恢复默认」与实时工作量预估） |
| `monitor` 实时监控 | 漏斗（生成→审出→验证→入库，逐段数字 + 拒绝原因 Top5）、五维来源堆叠柱、入库率 sparkline、FSA 冻结骨架 chips、LLM 轨迹卡（调用/耗时/降级原因）、迭代时间线（虚拟滚动，贴底自动跟随 + 2 分钟空闲恢复贴底，复用 AI-trade 轨迹面板的成熟交互） |
| `candidates` 候选库 | 可筛选（轴 / 状态 / 机制族 / 拒绝原因）可排序表；行内展示表达式 pill + 中文渲染 + 关键指标；详情抽屉：表达式树可视化（自绘 SVG）+ 中文解释 + 分段 IC 柱 + 分位收益 + （策略轴）净值/回撤图；操作：入库 / 一键回测 / 加入对比 |
| `library` 成果库 | 已入库的因子 / 组合 / 策略，含「已退化」徽标、来源溯源（run #/iter #）、跳转到因子实验室或策略面板 |

**设计规范**（对齐需求「Anthropic 顶级产品 / 美观优雅 / 人机交互最佳实践」）：

- 只用既有 CSS 变量（`--accent` / `--surface` / `--border` / `--muted` / `--text`）与 Tailwind 类，
  自动适配已有明暗主题；不新增依赖、不引图标库、图表一律自绘 SVG（与 Factor Lab 一致）。
- **诚实优先于漂亮**：任何数字都要么有样本支撑要么显示「样本不足」。入库率、机制覆盖、相关性
  在样本 < 阈值时显示灰底「—」，绝不外推（复用 `AnnualizedProjectionCard` 的最小样本门禁范式）。
- 长跑友好：进行中卡片有低频脉冲点而非跑马灯；WS 断线显示明确的「重连中」而不是假装还在跑；
  暂停 / 恢复 / 停止是三个语义不同的按钮，停止走 `ConfirmDialog` 二次确认。
- 可达性：全部交互 `<button>` + `aria-label` / `aria-current`；表格有 `scope` 表头；
  抽屉带焦点陷阱与 Esc 关闭（复用 `components/ui/Modal`）；尊重 `prefers-reduced-motion`。
- 空状态有引导（「还没有挖掘任务 → 新建一个，我们会用 X 分钟跑第一轮」），错误态给可执行的下一步。

**状态与 WS**：`useDiscoveryRunStream` 一 run 一连接（镜像 `useAiTradeGroupStream`）；
纯 reducer `applyDiscoveryEvent(state, event)` 落在 `lib/discovery.ts`（可单测）。
⚠️ **仓库既知坑**：`store.ts` 里若逐键手抄 WS 字段，新增字段会被静默吞掉。本特性规定：
`DiscoveryRunState` 的更新一律走 `{...prev, ...patch}` 的整对象合并，并由
`discovery.test.ts` 的「字段往返」用例断言 `DiscoveryRunState` 的**每一个** key 都能从 WS
事件穿透到 store（新增字段忘了接线 ⇒ 测试红）。

### 2.14 关键代码路径（逐调用链，供实现对照）

```text
[启动]
POST /api/discovery/runs
 └ api/discovery.py::start_discovery
    └ discovery/service.py::start_run
       ├ 闸1 settings.discovery_enabled                      → 403 DISCOVERY_DISABLED
       ├ 闸2 校验 instId/bar/窗口/轴/阈值（越界即 400，逐字段中文消息）
       │      · 轴不在 implementedAxes 内 → 400 DISCOVERY_AXIS_UNSUPPORTED（D-6）
       ├ 闸3 工作量预估 = 轴权重 × 每轮候选数 × 窗口根数 ≤ 上限 → 413 DISCOVERY_TOO_LARGE
       ├ 闸4 manager.reserve()  ← **同步、无 await 的预占**       → 409 DISCOVERY_BUSY
       ├ 建 discovery_runs 行（status="running", checkpoint 初值）
       │      · 此后任一步抛错都必须 manager.release()（try/except 兜底，绝不泄漏名额）
       └ manager.start(DiscoveryRun(spec))  → 201 {runId, status, config}

[单轮]
DiscoveryRun._run_iteration
 ├ evolution.plan(budget, pool_sizes, counters)
 ├ searchers[axis].{seed|mutate|crossover|perturb}(rng, parents, ctx)
 ├ llm_agents.generate_candidates(client, prompt_ctx, k)     # fail-open
 ├ review.filter_rules(cands, ctx) → review.llm_review(...)   # fail-open
 ├ async with validate_sem:  run_in_threadpool(validation.validate_batch, kept_pool, cands, frames)
 │   ├ 因子轴 → discovery.expression.evaluate(node, ctx_full)          # 全窗求值一次
 │   │        → evaluation.resolve_direction(v_is, close_is, "auto", ...)   # IS 段定号并冻结
 │   │        → evaluation.evaluate_factor(df_is,  Series(v_is),  direction, ...)
 │   │        → evaluation.evaluate_factor(df_oos, Series(v_oos), direction, ...)
 │   │          ⚠️ 严禁调 evaluate_factor_key（内部重算 ⇒ 暖机重置 + auto 重定号，见 §2.7.0）
 │   └ 策略轴 → engine.backtester.run_backtest(strategy, candles, bar, ...)
 │                → engine.metrics.compute_metrics + metrics_ext.compute_extended_metrics
 ├ fsa.scan(kept_rows) → bans
 ├ repositories.commit_iteration(session, run_id, cands, iteration_row, checkpoint)   # 单事务
 └ _push({"type":"iteration"...}) / _push({"type":"state"...}) / _push({"type":"candidate"...})

[入库]
POST /api/discovery/candidates/{id}/promote
 └ discovery/promote.py::promote_candidate
    ├ factor        → db.factor_repositories.create_factor(...)                 （factor_key="expr"）
    ├ factor_combo  → factors/service.py 既有 factor_set 创建路径（自动同步 factor_score 策略）
    └ strategy_*    → db.strategy_repositories.create_strategy(...)
```

---

## 三、文件 / 模块变更计划

> 约定：行数上限按 CLAUDE.md（单文件 ≤800、单方法 ≤50、形参 ≤5、嵌套 ≤4、行宽 ≤80）。
> 「预算」列是建议行数，超了就按职责再拆一个模块，**不要**靠删注释压行。

### 3.1 后端 · 新增（`services/quant/app/discovery/`）

| 文件 | 新/改 | 预算 | 一句话意图 |
|---|---|---|---|
| `discovery/__init__.py` | 新 | 40 | 公开 API 再导出。**⚠️ 只再导出 `expression` / `expression_text` / `mechanisms` 三个零依赖模块**（评审 P2-7）：`validation` / `searchers` / `runtime` **严禁**在模块层导入，否则 `library._expr_factor` 的延迟导入会把 `engine.factors.evaluation` 整条链拉起，形成 `engine ↔ discovery` 回边。需要它们的调用方直接 `from ..discovery.validation import ...` |
| `discovery/expression.py` | 新 | 420 | 表达式 AST：字段/算子表、量纲推断、解析、规范式、哈希、骨架、子树枚举、记忆化求值 |
| `discovery/expression_text.py` | 新 | 180 | 表达式 → 中文一句话（UI 与 prompt 的唯一真源） |
| `discovery/mechanisms.py` | 新 | 200 | 13 个机制族定义、原型模板、`classify_mechanism` |
| `discovery/genomes.py` | 新 | 220 | 四种基因 dataclass + `Searcher` 协议 + 哈希/描述 |
| `discovery/searchers/factor_searcher.py` | 新 | 300 | 表达式轴：seed/mutate/crossover/perturb/from_llm |
| `discovery/searchers/param_searcher.py` | 新 | 220 | 常规策略参数向量轴（读 `params_schema` 的 min/max/step） |
| `discovery/searchers/combo_searcher.py` | 新 | 260 | 因子组合轴 + 策略轮动组合轴 |
| `discovery/evolution.py` | 新 | 280 | 五维预算、锦标赛选择、数据驱动分布、动量追踪、`adapt_budget` 纯函数 |
| `discovery/fsa.py` | 新 | 180 | 骨架频次统计、冻结/TTL、变体上限、失败模式库 |
| `discovery/review.py` | 新 | 240 | 九条确定性审查规则 + 中文拒绝原因 |
| `discovery/validation.py` | 新 | 480 | 因子 11 条 / 策略 7 条联合过滤、§2.7.0 的 IS/OOS 求值与定号协议、`turnover_proxy`（F9，本层自算）、相关性去重、多重检验惩罚、`rebuild_kept_pool`（续跑重建 + 刷新复评**共用**，§2.10） |
| `discovery/llm_agents.py` | 新 | 340 | 生成 / 审查两个受约束 LLM 子代理（镜像 decider 的工具围栏与安全回落） |
| `discovery/prompts.py` | 新 | 300 | 中文 prompt 与两个工具 schema（词表由 §2.3 表自动生成） |
| `discovery/checkpoint.py` | 新 | 200 | 检查点序列化/反序列化/有界裁剪/版本迁移 |
| `discovery/runtime.py` | 新 | 560 | `DiscoveryRun`：四步循环、调度、数据刷新、事件扇出、协作式取消、必达终态 |
| `discovery/manager.py` | 新 | 260 | 进程内注册表、并发闸、关停三段、启动对账与自动续跑 |
| `discovery/service.py` | 新 | 300 | REST 编排与四道闸、配置归一化、列表/详情投影 |
| `discovery/promote.py` | 新 | 240 | 候选 → `factors` / `factor_sets` / `strategies` 的幂等入库 |
| `discovery/repositories.py` | 新 | 340 | 三张新表的 CRUD + 单事务 `commit_iteration` + 剪枝 + 孤儿对账 |
| `api/discovery.py` | 新 | 300 | 路由 + WS 端点（订阅即发 state 快照 + 环形缓冲回放） |

### 3.2 后端 · 修改（既有文件，全部为**加法**）

| 文件 | 改动 | 风险控制 |
|---|---|---|
| `engine/factors/library.py` | `FactorSpec` 加 `hidden: bool=False`；新增 `expr` 条目与 `_expr_factor`；`available_factors()` 过滤 hidden；新增 `visible_factor_keys()` | 既有 12 条构造零改（末位默认值）；`compute_factor` 逻辑不动 |
| `engine/factors/__init__.py` | 再导出 `visible_factor_keys` | 纯加法 |
| `engine/factors/mining.py` | `default_candidates` 跳过 hidden | 一行 `if spec.hidden: continue` |
| `agent/tools/factor_tools.py` | `FACTOR_KEY_ENUM = tuple(visible_factor_keys())` | 保证 Agent 工具枚举不含 `expr` |
| `db/session.py` | `_seed_builtin_factors` 跳过 hidden；`init_db` 末尾加 `_reconcile_orphan_discovery_runs()`（整段 try/except，失败仅 warning） | 不加任何 ALTER 补丁（新表由 `create_all` 建） |
| `db/models.py` | 追加 `DiscoveryRun` / `DiscoveryIteration` / `DiscoveryCandidate` 三个模型 | 只加新表，**不给既有表加列** |
| `main.py` | `include_router(discovery.router, prefix="/api")` | **第 16 个**路由（现有 15 个，评审 P2-1） |
| `config.py` | 追加 `discovery_*` 配置块（§6） | 字段必须真声明（`extra="ignore"` 会静默吞掉未声明 env） |
| `factors/specs.py` | `validate_members` 两处加法（**均只在 `factorKey=="expr"` 分支生效**，非 expr 成员逐字节不变）：① 透传并强制 `name`，格式 `^expr:[0-9a-f]{8}$`，缺失/格式错 → 400 `FACTOR_MEMBER_NAME_REQUIRED`；② `params["expr"]` 必须能被 `parse_expression` 解析，否则 400 `FACTOR_EXPR_INVALID` | 边界一次性校验。**这是 P0-3 的修复点**：不透传 `name` 则 `member_names` 退化为纯位置序，成员重排即静默错配训练权重（§2.2） |
| `db/models.py`（注释） | `FactorSet.members_json` 列注释 `[{factorKey,params,weight,direction}]` → `[{factorKey,params,weight,direction,name?}]` | 纯注释，零迁移 |
| `schemas.py` | 追加 `DiscoveryStartReq` / `DiscoveryPromoteReq` / `DiscoveryCandidateQuery` 等 pydantic 模型 | camelCase 对齐既有风格 |

### 3.3 前端 · 新增

| 文件 | 预算 | 意图 |
|---|---|---|
| `components/Discovery/DiscoveryLab.tsx` | 320 | 全屏工作区外壳 + 五分区导航 + 工作台上下文 |
| `components/Discovery/DiscoveryOverview.tsx` | 260 | 总览仪表盘（运行卡 / 机制热力 / 入库时间线） |
| `components/Discovery/DiscoveryLauncher.tsx` | 380 | 两步式启动器（配置 + 阈值 + 工作量预估 + 恢复默认） |
| `components/Discovery/DiscoveryRunList.tsx` | 220 | 任务列表（状态徽标 / 计数 / 暂停恢复停止删除） |
| `components/Discovery/DiscoveryMonitor.tsx` | 340 | 实时监控总装（漏斗 / 来源 / 入库率 / FSA / LLM） |
| `components/Discovery/DiscoveryFunnel.tsx` | 180 | 生成→审出→验证→入库漏斗 + 拒绝原因 Top5（自绘 SVG） |
| `components/Discovery/DiscoverySourceBars.tsx` | 160 | 五维来源堆叠柱 + 自适应预算变化标注 |
| `components/Discovery/DiscoveryTimeline.tsx` | 280 | 迭代时间线（虚拟滚动 + 贴底跟随 + 2 分钟空闲恢复） |
| `components/Discovery/DiscoveryCandidateTable.tsx` | 340 | 候选表（筛选 / 排序 / 分页 / 批量入库） |
| `components/Discovery/DiscoveryCandidateDetail.tsx` | 360 | 详情抽屉（表达式树 / 中文解释 / 指标 / 图） |
| `components/Discovery/ExpressionTree.tsx` | 220 | 表达式树自绘 SVG（含量纲与窗口标注） |
| `components/Discovery/DiscoveryLibrary.tsx` | 260 | 成果库（已入库 / 已退化 / 溯源跳转） |
| `lib/discoveryTypes.ts` | 200 | 全部 WS / REST 类型（与后端 camelCase 一一对应） |
| `lib/discoveryApi.ts` | 200 | REST 封装（统一错误体解包） |
| `lib/discovery.ts` | 300 | 纯函数：`applyDiscoveryEvent` reducer、漏斗/来源/覆盖派生、拒绝原因中文映射 |
| `lib/useDiscoveryRunStream.ts` | 180 | WS 订阅 hook（镜像 `useAiTradeGroupStream`） |

### 3.4 前端 · 修改

| 文件 | 改动 |
|---|---|
| `components/Nav/NavRail.tsx` | `ITEMS` 加第三项 + `RadarIcon` 内联 SVG |
| `app/page.tsx` | `hidden` 互斥挂载 `DiscoveryLab`（首激活后常驻） |
| `lib/store.ts` | `WorkbenchView` 加 `"discovery"`；新增 `discoverySection`（持久化）+ 瞬态 `discoveryRun` + `applyDiscoveryEvent` 接线；`partialize` 加 `discoverySection`；`version` +1 并在 `migrate` 补默认 |
| `components/Factor/FactorTable.tsx` | 「我的因子」列表：`factorKey === "expr"` 的行渲染只读等宽表达式 pill + 中文解释 tooltip，并给 `source === "mined"` 独立徽标（挖掘因子的 `category` 统一为 `"discovered"`，不进既有类别 chip，见 §2.12） |
| `components/Factor/MemberPalette.tsx` | 「我的因子」源的候选 chip：`expr` 因子显示 `name`（已按 §2.12 截断至 ≤128）+ 表达式 tooltip。**无须**改 `CategoryChips`——它派生自库目录，`expr` 已被 `hidden` 过滤掉 |

### 3.5 配置 / 打包 / 文档

| 文件 | 改动 |
|---|---|
| `.env.example` | 追加 `DISCOVERY_*` 示例与注释 |
| `desktop/src/lib/config.js` | prod 透传白名单加 `key.startsWith('DISCOVERY_')`（**漏改则桌面端所有 DISCOVERY_ env 静默失效**） |
| `desktop/scripts/config.test.mjs` | 补一条断言：`DISCOVERY_` 前缀在 prod 白名单内 |
| `README.md` | 「新增功能用法」补一节「策略与因子挖掘」 |
| `.claude-index/index.md` | 特性合入后由 `regenerate index` 刷新（不在本节手改） |

### 3.6 既有测试 · 必须同步修改（v2 新增，评审 P1-1）

v1 的变更计划里一个既有测试文件都没有，但 §2.2 的四处改点会**直接打红两条现有用例**，与 §7.3 的
DoD #10「无新增红灯」正面冲突。下表是**必改清单**；这些修改本身即「hidden 过滤真的生效了」的门禁，
**不是**为了让测试变绿而放宽断言——每条都要求**加强**后的断言。

| 文件 | 现断言 | 必须改成 | 为何必红 |
|---|---|---|---|
| `services/quant/tests/test_factor_library.py::test_catalog_consistency` | `{f["key"] for f in available_factors()} == set(FACTOR_LIBRARY)` | `== set(visible_factor_keys())` **且追加** `assert "expr" in FACTOR_LIBRARY and "expr" not in visible_factor_keys()` | `available_factors()` 开始过滤 `hidden`，两侧集合不再相等 |
| `services/quant/tests/test_factor_mining.py::test_default_candidates_cover_whole_library` | `{c["factorKey"] for c in default_candidates("1H")} == set(FACTOR_LIBRARY)` | `== set(visible_factor_keys())` **且追加** `assert "expr" not in keys` | `default_candidates` 开始跳过 `hidden` |

**无须改但必须满足契约**的既有用例（改错 `_expr_factor` 就会红，属有效护栏，保留原样）：
`test_factor_library.py::test_compute_factor_is_strictly_causal` 与 `::test_compute_factor_shape_matches_df`
——两者都以 `params=None` 遍历全库，故 `_expr_factor` 在空表达式下必须返回**与 df 等长、index 0..n-1
的全 NaN Series**（§2.2 降级契约）。

**既有红灯基线**：`test_factor_library_expansion.py` 断言 `len(FACTOR_LIBRARY) == 20`，而库中实为 12
（该扩展从未落地）——这是**本特性之前就存在的红灯**，加 `expr` 后变 13 仍红。**必须**在干净 HEAD
worktree 实测对账后如实标注为非回归，**严禁**顺手「修」它（那是另一个特性的欠账，改它会掩盖真实基线）。

---

## 四、接口设计

统一约定：路径前缀 `/api`（前端经 `/api/py/*` 代理）；字段 camelCase；时间秒级 UTC；
错误体 `{"error": {"code": "...", "message": "中文"}}`（沿用 `main.py` 异常处理器）。

### 4.1 REST

| Method | Path | 说明 |
|---|---|---|
| POST | `/discovery/runs` | 启动挖掘任务 → `201 {runId, status, config, startedTs}`；403 总闸关 / 400 参数（含 `DISCOVERY_AXIS_UNSUPPORTED`）/ 413 工作量超限 / 409 并发满。**守门序固定**：403 → 400 → 413 → 409（`manager.reserve()` 同步预占放最后，避免参数错也占名额），见 §2.14 |
| GET | `/discovery/runs?status=&limit=&offset=` | 任务列表（倒序）→ `{items:[RunSummary], total}` |
| GET | `/discovery/runs/{runId}` | 任务详情（含 config、counters、checkpoint 摘要、最近 20 轮迭代） |
| POST | `/discovery/runs/{runId}/pause` | 暂停 → `202 {status:"paused"}`；已终态 → 200 幂等 |
| POST | `/discovery/runs/{runId}/resume` | 恢复（从检查点续跑）→ `202 {status:"running"}`；不可恢复 → 409 |
| POST | `/discovery/runs/{runId}/stop` | 停止（不可恢复，必达终态）→ `202`/`200` 幂等 |
| DELETE | `/discovery/runs/{runId}` | 删任务（级联删迭代与候选）；仍在跑 → 409 `DISCOVERY_RUNNING` |
| GET | `/discovery/runs/{runId}/candidates?kind=&status=&mechanism=&reason=&sort=&limit=&offset=` | 候选分页（默认只回 `kept`）→ `{items:[CandidateSummary], total}` |
| GET | `/discovery/candidates/{id}` | 候选详情（表达式 / 中文 / 全量指标 / IC 分段 / 分位 / 策略轴净值点） |
| POST | `/discovery/candidates/{id}/promote` | 入库 → `{promoted:{kind, entityId, entityKind}}`；重复入库幂等回既有 id |
| POST | `/discovery/candidates/{id}/backtest` | 一键持久化回测 → `{runId, metrics, extendedMetrics}`（复用既有回测服务） |
| GET | `/discovery/config/defaults` | 返回全部阈值与预算默认值 + **`implementedAxes: AxisKind[]`**（D-6：启动器只渲染这里回的轴，前端不硬编码、不渲染未实现轴的占位）。「恢复默认」的唯一真源 |
| GET | `/discovery/vocabulary` | 字段 / 算子 / 机制族词表（UI 渲染与教学提示；与 prompt 同源） |

`POST /discovery/runs` 请求体（关键字段，全部可选者标默认）：

```jsonc
{
  "instId": "BTC-USDT-SWAP",
  "bar": "1H",
  "windowBars": 4000,                 // 200..8000
  "axes": ["factor"],                 // 至少一个；必须 ⊆ implementedAxes，否则 400（D-6）
  "axisWeights": {"factor": 1.0},     // v1 只实现 factor 轴；多轴示例见 §9 第 7 步
  "horizon": 6,                       // 主前瞻根数
  "candidatesPerIteration": 40,       // 1..200（策略轴内部自动降档）
  "iterationIntervalS": 300,          // 60..3600
  "dataRefreshS": 21600,              // 0=不刷新
  "windowMode": "rolling",            // rolling | fixed
  "maxIterations": 0, "maxRuntimeHours": 0, "maxKept": 200,
  "autoResume": true, "autoPromote": false,
  "llm": {"enabled": true, "reviewSample": 5, "maxCallsPerHour": 24, "timeoutS": 60},
  "thresholds": { "minAbsIc": 0.03, "minAbsIr": 0.5, "minAbsT": 2.0, "minCoverage": 0.6,
                  "minSamples": 200, "minSignStability": 0.6, "oosRetain": 0.5,
                  "minMonotonicity": 0.6, "costMult": 2.0, "maxTurnover": 0.35,
                  "maxIcCorr": 0.70, "maxValueCorr": 0.85, "trialPenaltyK": 0.15,
                  "minTrades": 20, "minSharpe": 0.8, "minCalmar": 1.0,
                  "maxDdAllowed": 0.35, "maxEquityCorr": 0.70 },
  "evolution": {"budget": {"mutate":0.25,"crossover":0.25,"perturb":0.15,
                           "random":0.15,"llm":0.20},
                "adaptEvery": 10, "fsaEvery": 25, "fsaThreshold": 0.15,
                "fsaBanTtlIters": 150, "variantCap": 6},
  "seed": 20260805                    // 省略则用当前秒级 UTC；落库以便复现
}
```

### 4.2 WebSocket `/api/ws/discovery/{runId}`

事件 `{"type": ..., "data": {...}}`：

| type | data | 时机 |
|---|---|---|
| `state` | 全量运行态快照（status / iteration / counters / budget / poolSizes / mechanismCoverage / fsaBans / nextIterationTs / dataWindow / llmUsage / pauseReason） | **订阅即首发**，之后每轮末发一次 |
| `iteration` | 单轮记录（index / 起止 / 各步计数 / rejectedByReason / 来源分布 / llm / durationMs / overrun / skipped） | 每轮末 |
| `candidate` | 候选摘要（id / kind / exprText / describe / verdict / 关键指标 / rejectReason / mechanism） | 每轮末，**上限 `maxCandidateEvents`（默认 20）条**：全部 kept 优先，剩余名额给拒绝样本 |
| `demoted` | `{candidateId, reason, metrics}` | 数据刷新后的复评降级 |
| `error` | `{code, message}` | 可恢复错误（数据不可达 / LLM 降级）；**不**终止流 |
| `ping` | — | 静默 20s 心跳（抄 `ws_weight_batch`） |
| `done` | `{status, summary}` | 达终态 |

**与既有 WS 的一处有意偏离**：`weight-batch` 是「订阅即全量回放」，而挖掘 run 可能跑三天、事件
数十万条，全量回放不可行。本端点改为「**首发 `state` 全量快照 + 回放最近 `WS_RING`（默认 400）
条事件**」；终态事件永远保留在环形缓冲尾部（`done` 单独持有一个槽位，绝不被挤掉），保证晚订阅
的客户端不会看到一个永远「进行中」的死流。此约定必须写进 `api/discovery.py` 的模块 docstring。

---

## 五、数据模型

三张新表，`create_all` 加法建表、**零数据迁移**、不动任何既有表（D-5）。

### 5.1 `discovery_runs`

| 列 | 类型 | 说明 |
|---|---|---|
| `id` | Integer PK | |
| `label` | String(128) | 任务名（缺省 = `{instId} {bar} 挖掘`） |
| `inst_id` / `bar` | String(32)/String(8) | 目标标的与周期 |
| `symbols_json` | Text `"[]"` | 标的组成员（单标的为 `[instId]`，为未来多标的池化留口） |
| `status` | String(16) | `running` / `paused` / `stopped` / `succeeded` / `failed` |
| `pause_reason` | String(64) `""` | `user` / `process_restart` / `data_unavailable` / `limit_reached` |
| `config_json` | Text | 归一化后的完整配置快照（阈值 / 预算 / LLM / 种子；**回溯真源**） |
| `checkpoint_json` | Text | §2.10 的小状态（有界） |
| `counters_json` | Text | 冗余计数（tested/kept/rejected 分布/llmCalls），列表页免解析大 JSON |
| `iteration` | Integer | 已完成轮数 |
| `kept_count` / `tested_count` | Integer | 反范式冗余（列表排序用） |
| `started_ts` / `last_iter_ts` / `finished_ts` | Integer/nullable | 秒级 UTC |
| `error_code` / `error_message` | String(64)/String(256) | 失败时填 |
| `created_at` / `updated_at` | DateTime | |

索引：`ix_discovery_runs_status(status, id)`。

### 5.2 `discovery_iterations`

| 列 | 类型 | 说明 |
|---|---|---|
| `id` | Integer PK | |
| `run_id` | FK → `discovery_runs.id`, index | |
| `idx` | Integer | 轮序号（自 1 起） |
| `started_ts` / `finished_ts` | Integer | |
| `budget_json` | Text | 本轮五维预算（自适应后的实际值） |
| `stats_json` | Text | `{generated, ruleRejected, llmRejected, validated, kept, rejectedByReason:{}, bySource:{}, yieldedByPool:[], overrun, skipped}` |
| `llm_json` | Text | `{calls, latencyMs, degraded, degradedReason, promptChars}` |
| `note` | String(256) | 人类可读一句话（FSA 冻结 / 预算调整 / 数据刷新等） |

索引：`ix_discovery_iter_run(run_id, idx)`。保留策略：每 run 保留最近 `maxIterationRows`（默认 5000）
条，超出剪最旧（剪枝量记入 run 的 `counters_json.prunedIterations`，绝不静默）。

### 5.3 `discovery_candidates`

| 列 | 类型 | 说明 |
|---|---|---|
| `id` | Integer PK | |
| `run_id` | FK, index | |
| `iteration_idx` | Integer | 产出轮次 |
| `kind` | String(24) | `factor` / `factor_combo` / `strategy_param` / `strategy_combo` |
| `genome_hash` | String(32) | 唯一去重键 |
| `genome_json` | Text | 基因体（表达式规范式 / 参数向量 / 成员快照） |
| `expr_text` | Text `""` | 表达式规范式（因子轴）；策略轴为空 |
| `describe_text` | String(512) | 中文渲染（列表与 prompt 共用） |
| `skeleton` | String(256) `""` | FSA 键（因子轴） |
| `mechanism` | String(48) `""` | 机制族 |
| `source` | String(16) | `mutate` / `crossover` / `perturb` / `random` / `llm` / `seed` |
| `verdict` | String(16) | `kept` / `rejected` |
| `reject_reason` | String(64) `""` | 首个未通过的条目（`F3` / `duplicate` / `llm_review:...` 等） |
| `metrics_json` | Text | 全量指标（IS/OOS 双份；策略轴含 metrics + extendedMetrics） |
| `series_json` | Text `"{}"` | 详情用的有界序列（分段 IC ≤ 24 点 / 分位 ≤ 10 桶 / 净值降采样 ≤ 300 点） |
| `trials_at_accept` | Integer | 入库时的累计试验数（多重检验回溯） |
| `demoted` | Boolean False | 复评降级标记 |
| `promoted_kind` / `promoted_id` | String(16) `""` / Integer nullable | 入库后的实体（`factor` / `factor_set` / `strategy` + id；普通整数不加外键，仿 `LiveOrderAudit.account_id`） |
| `created_ts` | Integer | 秒级 UTC |

索引 / 约束：`UniqueConstraint(run_id, genome_hash)`（**去重的物理保证**）、
`ix_discovery_cand_run(run_id, verdict, id)`、`ix_discovery_cand_hash(genome_hash)`。
保留策略：全部 `kept` 永不剪；`rejected` 每 run 保留最近 `maxCandidateRows`（默认 20000）条。

### 5.4 内存态（`DiscoveryRunState`，前端）

```ts
interface DiscoveryRunState {
  runId: number; status: RunStatus; pauseReason: string;
  iteration: number; counters: DiscoveryCounters; budget: SourceBudget;
  poolSizes: Record<AxisKind, number>; mechanismCoverage: Record<string, number>;
  fsaBans: FsaBan[]; nextIterationTs: number | null;
  dataWindow: { startTs: number; endTs: number; lastRefreshTs: number };
  llmUsage: { callsThisHour: number; degraded: boolean; degradedReason: string };
  iterations: DiscoveryIterationRow[];      // 环形，≤200
  candidates: DiscoveryCandidateRow[];      // 环形，≤300（完整列表走 REST 分页）
  connected: boolean;
}
```

---

## 六、配置项（`config.py`，env 前缀 `DISCOVERY_`）

| 字段 | 默认 | 说明 |
|---|---|---|
| `discovery_enabled` | `True` | 总闸；`False` ⇒ 写类端点 403（读类仍可看历史） |
| `discovery_max_runs` | `1` | 并发挖掘任务上限（硬上限 3） |
| `discovery_default_interval_s` | `300.0` | 迭代间隔默认（对齐报告的 5 分钟） |
| `discovery_min_interval_s` / `discovery_max_interval_s` | `60.0` / `3600.0` | 间隔夹紧区间 |
| `discovery_max_candidates_per_iter` | `200` | 单轮候选上限 |
| `discovery_max_window_bars` | `8000` | 数据窗口根数上限（取数工作量护栏） |
| `discovery_work_unit_limit` | `3_000_000` | 单轮工作量上限（候选 × 根数），超限 413（沿用 `MINE_WORK_UNIT_LIMIT` 语义） |
| `discovery_candidate_timeout_s` | `20.0` | 单候选验证墙钟超时 |
| `discovery_iteration_timeout_s` | `600.0` | **单轮**墙钟预算（v2 新增，评审 P2-4）；到点停取新候选、正常 commit 已完成者、记 `truncatedByTimeout` + 丢弃数 |
| `discovery_validate_concurrency` | `2` | 验证层进 `run_in_threadpool` 的并发信号量（v2 新增，评审 P2-6；硬上限 8）。与 API 请求共用 anyio 线程池，故必须限流 |
| `discovery_llm_enabled` | `True` | LLM 子代理总闸 |
| `discovery_llm_timeout_s` | `60.0` | 单次 LLM 调用超时 |
| `discovery_llm_max_retries` | `1` | 瞬时失败重试次数 |
| `discovery_llm_max_calls_per_hour` | `24` | 成本硬闸 |
| `discovery_max_candidate_rows` | `20000` | 每 run 拒绝候选保留上限 |
| `discovery_max_iteration_rows` | `5000` | 每 run 迭代记录保留上限 |
| `discovery_ws_ring` | `400` | WS 环形缓冲条数 |
| `discovery_auto_resume` | `True` | 进程重启后自动续跑 `running` 任务。**⚠️ 受 `discovery_enabled` 一票否决**：总闸为 `False` 时一律 `paused(disabled_by_config)`，绝不拉起后台循环（评审 P1-8，见 §2.1 纪律 4）。续跑必先跑完 §2.10 的 kept 池重建才进第一轮 |
| `discovery_resume_delay_s` | `10.0` | 自动续跑前的静置延时（等 OKX / DB 就绪） |

⚠️ 三处联动，缺一即静默失效：① `config.py` 必须**真的声明**字段（`extra="ignore"` 会吞掉未声明
的 env）；② `desktop/src/lib/config.js` prod 白名单必须放行 `DISCOVERY_` 前缀；③ `.env.example`
补文档。

---

## 七、测试与验收标准

### 7.1 后端测试（`services/quant/tests/`，pytest）

> ⚠️ 仓库既知坑：**全量 pytest 不能并发跑**（共用临时 DB）；`pytest -q` 有块缓冲，长跑请用
> `python -u -m pytest ... > log`。基线红灯（`factor_library_expansion` 孤儿测试等）须先在干净
> HEAD worktree 实测对账，不得把既有红灯算作本特性回归。

| 文件 | 覆盖 |
|---|---|
| `test_discovery_expression.py` | 解析/序列化往返、交换律同哈希、窗口梯子越界报错、量纲推断真值表、深度/节点护栏、**因果性断言**（在第 i 根之后追加数据不改变前 i 根的值）、记忆化命中、数值异常 → NaN 不抛 |
| `test_discovery_expression_text.py` | 每个算子与字段都有中文渲染；无 `KeyError` 兜底路径 |
| `test_discovery_mechanisms.py` | 13 族分类的代表性样例；未知结构回落 `unclassified` |
| `test_discovery_searchers.py` | 四轴的 mutate/crossover/perturb 产物合法（过护栏）、同种子可复现、参数向量不越 `params_schema` |
| `test_discovery_evolution.py` | `adapt_budget` 四条规则的真值表、夹紧与归一化、锦标赛选择分布、动量方向正确、状态有界（≤64） |
| `test_discovery_fsa.py` | 超阈值冻结、TTL 到期解冻并可再冻结、变体上限、失败模式黑名单触发条件 |
| `test_discovery_review.py` | 九条规则逐条命中与中文原因；合法表达式全部放行 |
| `test_discovery_validation.py` | 11 条因子闸 + 7 条策略闸逐条「刚好不过 / 刚好过」双向用例；IS/OOS 切分严格时序；多重检验惩罚随 trials 抬高门槛；相关性去重双通道；单候选超时不影响同轮其他候选。**v2 新增四条承重用例**：① **F6 非空转**（评审 P0-1）——构造一个 IS 强、OOS 反号的因子，断言 F6 **拒**；再构造 IS/OOS 同号的，断言 F6 过。若实现误传 `direction="auto"` 给 OOS，第一条必红；② **F8 量纲**（P0-2）——以生产默认成本断言 `topBottomSpread=0.003` 过、`0.001` 不过（门槛落在千分位即证明没犯 1e4 错配）；③ **F9 `turnover_proxy`**（P1-2）——逐根翻档的桩趋近 1.0、恒定档位的桩趋近 0.0、有限样本 < 2 → `None` ⇒ 拒（fail-closed）；④ **IS/OOS 同源求值**（P1-6）——断言 OOS 段前 `max_window` 根**不是** NaN（即证明用的是全窗求值后切片，而非切片后重算） |
| `test_discovery_resume_pool.py` | **v2 新增**（评审 P1-4）：`rebuild_kept_pool` 在续跑后重建出的 (值序列, 分段 IC 序列) 与首次入库时逐位一致；重建失败 ⇒ 转 `paused` 且**不**带残缺池开跑；重建期间 WS `state` 带 `rebuilding=true`；「续跑重建」与「刷新后复评」调用的是**同一个函数**（以 monkeypatch 计数断言，杜绝第二份实现） |
| `test_discovery_llm_agents.py` | 桩 LLM：正常解析、无工具调用、多次调用取首条、非法 JSON、超时 ⇒ **一律 fail-open** 且 `llmDegraded` 如实标注；调用上限触顶后跳过 |
| `test_discovery_checkpoint.py` | 序列化往返、有界裁剪、版本兼容、`iteration+1` 续跑、同 seed 同 iteration 复现同批候选 |
| `test_discovery_runtime.py` | 单轮四步顺序、协作式取消在各步之间生效、必达终态、落库失败仍达终态、超时轮 `overrun` 标记、数据不可达 → `paused(data_unavailable)` 且**绝不合成数据** |
| `test_discovery_manager.py` | 并发闸（**必须断言 `reserve()` 同步预占**：连发两个启动请求，第二个恒 409，不依赖时序；评审 P1-3）、启动路径任一步抛错后 `release()` 归还名额、关停三段、启动对账（`running` 孤儿 → 续跑或 `paused(process_restart)`）、**总闸关闭时一律 `paused(disabled_by_config)` 且不起后台任务**（评审 P1-8）、对账失败仅 warning 不阻断启动 |
| `test_discovery_api.py` | 全部端点的 200/201/400/403/409/413/404、错误体形状、WS 首发 `state` + 环形回放 + 终态不被挤掉 + `ping` |
| `test_discovery_promote.py` | 四轴入库形状；重复入库幂等；`member_names` 互异；入库后既有 `factor_score` 回测链路可跑通 |
| `test_discovery_repositories.py` | 单事务 `commit_iteration` 的原子性（中途抛异常 ⇒ 三张表都不写）、唯一约束去重、剪枝计数如实 |
| `test_discovery_import_boundary.py` | **安全门禁**：递归解析 `app.discovery` 的 import 图，断言不含 `live_auto` / `ai_auto` / `trading` / `okx.trade_client` |
| `test_factor_expr_bridge.py` | `expr` 因子走完 `compute_factor` / `evaluate_factor_key` / `build_factor_matrix` / `factor_score` 回测；`available_factors()` / `FACTOR_KEY_ENUM` / seed / `default_candidates` **均不含** `expr`；**v2 新增**（评审 P0-3）：`validate_members` 对 `expr` 成员**透传 `name`**（不带 name → 400 `FACTOR_MEMBER_NAME_REQUIRED`；格式错 → 400），且 3 个不同表达式的组合经 `validate_members → member_names` 后得到 3 个**与表达式绑定**的互异列名——**把成员顺序打乱后列名与成员的对应关系不变**（这是防止训练权重静默错配的判据）；非 `expr` 成员的返回 dict 与改动前逐键相等（零回归） |

### 7.2 前端测试（`apps/web`，vitest）

> ⚠️ 组件用例必须 `NODE_ENV=test`（否则 React act() 报错）。

| 文件 | 覆盖 |
|---|---|
| `lib/discovery.test.ts` | `applyDiscoveryEvent` 全事件类型；**字段往返断言**（`DiscoveryRunState` 每个 key 都能被 WS 事件穿透，防止手抄漏键）；环形上限；乱序/重复事件幂等 |
| `lib/discoveryApi.test.ts` | 错误体解包、查询串拼装 |
| `components/Nav/NavRail.test.tsx` | 三项渲染、`aria-current`、点击切 `activeView`（既有用例需同步更新） |
| `components/Discovery/DiscoveryLauncher.test.tsx` | 参数夹紧、至少选一轴、工作量预估、恢复默认走 `/discovery/config/defaults` |
| `components/Discovery/DiscoveryCandidateTable.test.tsx` | 筛选/排序/分页、空态、批量入库禁用条件 |
| `components/Discovery/DiscoveryMonitor.test.tsx` | 漏斗数字一致性（各段之和 = 生成数）、样本不足显示「—」、断线态文案 |
| `components/Discovery/ExpressionTree.test.tsx` | 树布局不重叠、深度 5 可渲染、窗口/量纲标注 |

### 7.3 验收标准（Definition of Done）

> **范围前提（v2 新增，评审 P1-7）**：本节验收的是 **D-6 定义的 v1 范围 = `factor` 单轴闭环**。
> 其余三轴按 §9 第 7 步作为独立增量交付，各自复用本节第 2/4/6/7/8/9 条的同构验收；在它们落地前，
> 这些轴**必须**在启动器里不可选、在 API 上 400（D-6 强制推论）——「能选但不产候选」不算完成，算缺陷。

功能性：

1. 最左侧导航出现第三项「策略与因子挖掘」，键盘可达，选中态与既有两项视觉一致。
2. 能创建一个针对真实 OKX 标的的挖掘任务并在 UI 看到第 1 轮在 ≤2×`iterationIntervalS` 内产出
   迭代记录（生成 / 审出 / 验证 / 入库四段数字齐全）。
3. 关闭进程再启动：任务按 `autoResume` 自动续跑，`iteration` 从检查点继续（不回到 1、不重复
   测试已测哈希），或显示 `paused(process_restart)` 且点「恢复」后继续。续跑时 UI 先显示
   「正在重建候选池（i/N）」再进入挖掘（§2.10）；把 `DISCOVERY_ENABLED=false` 后重启，任务显示
   `paused(disabled_by_config)` 且**后台无任何 LLM / OKX 调用**（评审 P1-8）。
4. 至少一个候选能通过 11 条闸入库，并在候选库详情里看到：表达式树、中文解释、IS/OOS 双份指标、
   分段 IC、分位收益、`turnoverProxy`、`trialsAtAccept`。
   **反向验收（评审 P0-1/P0-2）**：随机抽 3 个已入库候选，人工核对其 OOS `rankIc` **不是**全部非负
   （若全部非负，说明 F6 在 `auto` 下空转，判定不通过）；并核对 F8 的实际门槛落在千分位量级
   （生产默认 = 0.0028），而非两位数。
5. 该候选「入库」后出现在「我的因子」，能被因子实验室评估、能进权重训练、能被组合策略引用，
   全过程**零报错**（`expr` 桥打通的判据）。
6. 「一键回测」产出的 run 出现在既有「运行历史」中并可回放 K 线。
7. 连续运行 ≥6 小时：无内存单调增长（候选/迭代环形与剪枝生效）、无 DB 无界增长、WS 断线重连后
   状态正确（首发 `state` 快照）。
8. 断网（OKX 不可达）时：任务转 `paused(data_unavailable)`，UI 明确提示，**没有任何合成数据**
   进入指标或图表。
9. LLM 未配置 / 报错时：任务照常推进，UI 显式标注「LLM 已降级：<原因>」，入库标准不变。

零回归：

10. `pnpm test`（后端 pytest 不含 network + 前端 vitest）相对**干净 HEAD worktree 实测基线**无新增红灯。
11. 既有因子目录 / Agent 因子工具枚举 / 「我的因子」首屏 seed / 经典挖掘候选集**均不含** `expr`。
12. `test_discovery_import_boundary.py` 绿：发现层与交易层零耦合。
13. 未启动任何挖掘任务时，本特性对既有请求路径 0 次额外 DB 查询、0 个后台任务。

14. **既有测试的两处必改（§3.6）已落实且是加强而非放宽**：`test_catalog_consistency` 与
    `test_default_candidates_cover_whole_library` 均改用 `visible_factor_keys()` 且各自新增了
    「`expr` 在库中但不在可见集」的正向断言。`test_factor_library_expansion.py` 的既有红灯已在干净
    HEAD worktree 实测对账并如实标注为非回归（**未被顺手修改**）。

15. **长跑不劣化交互（评审 P2-6）**：一个挖掘任务满负荷运行时，`GET /market/candles` 与
    `POST /runs`（一次普通回测）的 p95 延迟相对空载基线劣化 < 30%。这是
    `discovery_validate_concurrency` 信号量真的生效的判据。

文档：

16. `README.md` 有用法段；本 spec 的「实施过程发现的方案缺陷」小节在实现后补齐（仓库惯例）。

---

## 八、风险与缓解

| # | 风险 | 影响 | 缓解 |
|---|---|---|---|
| R-1 | **多重检验 / 过拟合**：跑几万个候选，纯噪声也能凑出 IR>0.5 的「因子」 | 最严重的正确性风险，直接让用户拿噪声去交易 | ① F11 多重检验惩罚（门槛随 `log10(trials)` 抬高）；② F6 强制 OOS 同号且保留 50% 强度；③ F8 成本后价差门槛；④ F10 双通道相关性去重；⑤ 候选详情**必须**显示 `trialsAtAccept` 与 OOS 指标，UI 文案不得出现任何收益承诺；⑥ 数据刷新后的复评降级机制（§2.9） |
| R-2 | **LLM 成本失控**：24/7 × 每 5 分钟 = 288 轮/天 | 账单与配额 | 每轮 ≤2 次调用 + `maxCallsPerHour` 硬闸 + 触顶降级（不是排队）+ UI 实时显示本小时用量；LLM 总闸可关，关掉后功能仍完整 |
| R-3 | **OKX 限频 / 不可达** | 长跑中断、或更糟：用陈旧数据当新数据 | 每 run **自持数据帧**（⚠️ v2 更正：`factors.service._candle_cache` TTL 仅 90s、cap 16，是请求级缓存，**不能**当 run 级护栏，评审 P2-2）；刷新按 `dataRefreshS` 低频；捕获面必须覆盖 `OKXError` + `HTTPException(EMPTY_RANGE)` + `TimeoutError` + `OSError`；失败指数退避 + 连续失败转 `paused`；**绝不**用缓存冒充实时（沿用仓库硬约束与 `X-Aragon-Data-Source` 精神） |
| R-4 | **DB 无界增长**（三天十万级候选） | 桌面端 SQLite 膨胀、列表变慢 | 候选/迭代双保留策略 + 剪枝计数如实上报；`series_json` 强制降采样（≤300 点）；`kept` 永不剪但受 `maxKept` 精英池约束 |
| R-5 | **搜索同质化**（报告的核心问题） | 挖出 69 个「同一个因子的变体」 | FSA 骨架冻结 + 变体上限 + 失败模式库 + 自适应预算把份额推向 random/llm + 机制族覆盖度纳入 LLM prompt |
| R-6 | **FSA 永久冻结导致饿死**（长跑特有，报告未遇到） | 跑几天后无候选可生成 | 冻结带 TTL（150 轮）并可再冻结；冻结历史可回溯 |
| R-7 | **前视泄漏**（表达式误用未来数据） | 指标虚高、实盘必亏 | 算子表只允许 rolling/ewm/shift(+N)；`test_discovery_expression.py` 有「追加未来数据不改变历史值」的机器断言；前瞻收益仍走既有 `forward_return`（右端截断不补零） |
| R-8 | **发现层误触实盘** | 资金风险 | D-2 import 边界 + 边界测试；`autoPromote` 默认关；入库只写三张实体表；发现层不引任何下单客户端 |
| R-9 | **长跑作业悬挂 / 幽灵进行中** | 用户看到永远转圈 | 必达终态 + 落库失败也先置终态 + 启动对账 + WS 终态槽位不被环形缓冲挤掉 |
| R-10 | **`expr` 桥漏改遍历现场** | Agent 工具枚举 / 因子目录出现空壳因子 | §2.2 的四处改点写死在本文 + `test_factor_expr_bridge.py` 逐处断言 |
| R-11 | **前端 WS 新字段被静默吞掉**（仓库既知坑） | 后端算了、类型补了，用户看不见 | 整对象合并 + `discovery.test.ts` 的字段往返断言 |
| R-12 | **单轮耗时超过间隔**（大窗口 + 大候选数） | 迭代堆积 | 不排队补跑，立即开下一轮并标 `overrun`；启动器给工作量预估与 413 护栏 |
| R-13 | **跨窗口指标不可比**（rolling 模式） | 榜单混着不同时期的指标 | 复评 + `demoted` 徽标 + 候选行显示各自的数据窗口 |
| R-14 | **报告数字被误读为本功能预期收益** | 误导用户 | 文档与 UI 一律不引用报告的 18.3% 等数字；总览页固定展示免责说明（沿用 README 免责声明口径） |
| R-15 | **「门禁算了但恒真 / 恒假」——本仓库反复出现的失败模式**（v2 新增，评审 P0-1/P0-2 即两个实例） | 最贵的一类缺陷：不报错、不崩、只是安全网从未生效或搜索永不产出 | 凡新增判据必须**双向用例**（刚好过 + 刚好不过）；凡跨层比较必须在文档里写死量纲与换算（§2.7 F8）；凡「同号 / 同向」类判据必须先确认被比较的量**没有被上游归一化掉符号**（§2.7.0）；验收阶段必须人工反查一次真实产出（§7.3 第 4 条反向验收） |
| R-16 | **长跑特性挤占交互式请求**（v2 新增，评审 P2-6） | 用户感知为「整个 App 变卡」，却查不到原因 | 验证层并发信号量（默认 2）+ 单轮墙钟预算 + DoD #15 的 p95 劣化门禁 |
| R-17 | **续跑后去重基准缺失**（v2 新增，评审 P1-4） | 每次重启后开始收同质因子，长跑价值归零且不可见 | `rebuild_kept_pool` 强制前置、失败即 `paused`、与刷新复评共用同一实现（§2.10） |

---

## 九、建议实施顺序（可切分为独立可验收的子步）

> **v1 的完成线在第 6 步**（D-6 / 评审 P1-7）：第 1–6 步构成 `factor` 单轴的完整闭环，满足需求四条
> 要点，是本轮的交付物；第 7 步的另外三轴是 v1.1+ 的加法增量，**在它们落地前必须在 UI 与 API 上
> 显式不可用**（D-6 强制推论），绝不能留成「能选但不产候选」的空转轴。

1. **地基**：`expression.py` + `expression_text.py` + `mechanisms.py` + `expr` 桥（§2.2 四处改点
   + §3.6 的两处既有测试必改 + `specs.validate_members` 的 `name` 透传）
   + `test_discovery_expression*.py` + `test_factor_expr_bridge.py`。此步交付后，用户已经可以手
   写表达式因子并走完既有全部管线——**独立有价值**。
2. **验证与审查**：`validation.py`（含 §2.7.0 的 IS/OOS 求值定号协议、F8 量纲、F9 `turnover_proxy`、
   `rebuild_kept_pool`）+ `review.py` + `fsa.py` + 对应测试（纯函数，无 IO，好测）。
   ⚠️ 本步含三个 P0/P1 修复点，`test_discovery_validation.py` 的四条承重用例（§7.1）**必须**与实现
   同批交付，不得留到后面补。
3. **演化与搜索器**：`evolution.py` + `genomes.py` + `searchers/*`（先只做 `factor` 轴）。
4. **循环与持久化**：`models.py` 三表 + `repositories.py` + `checkpoint.py` + `runtime.py`
   + `manager.py` + `service.py` + `api/discovery.py`（含 WS）。此步交付后后端可 24/7 跑。
5. **LLM 子代理**：`llm_agents.py` + `prompts.py`（总闸默认开，但**先在关闭状态下验收第 4 步**，
   保证「无 LLM 也能跑」这条 fail-open 姿态是真的）。
6. **前端**：NavRail + store + `lib/discovery*` + `DiscoveryLab` 五分区（先 `runs`/`monitor`，
   再 `candidates`/`library`/`overview`）。
7. **（v1.1+，超出本轮范围）其余三轴**：`factor_combo` → `strategy_param` → `strategy_combo`
   （每轴独立可验收；每落地一轴才把它加进 `implementedAxes`）。
8. **收尾**：README、`.env.example`、desktop 白名单 + 其测试、索引重生成。

---

## 十、已拍板决策与开放问题

**已拍板（实现时不得再改，改需修订本文）：**

- D-1 `expr` 桥是唯一的因子回流方式；**不**做「运行期动态注册进 `FACTOR_LIBRARY`」（进程重启即丢，
  且破坏「库是唯一真源 C6」）。
- 截面算子（`rank_cs` 等）**不实现**；报告里所有截面语义一律用时序对应物（`ts_rank` / `ts_zscore`）。
- FSA 冻结**带 TTL**（有意偏离报告的永久冻结，理由见 R-6）。
- WS **不做全量回放**（有意偏离 `weight-batch`，理由见 §4.2）。
- 发现层与交易层零 import 依赖，由测试守门。
- `autoPromote` 默认 **false**。

**v2 评审新增的拍板项（同样不得再改）：**

- **v1 = `factor` 单轴闭环**（D-6）；未实现的轴在 UI 不可选、在 API 400，绝不留空转轴。
- **IS/OOS 求值定号协议**（§2.7.0）：全窗求值一次 → 同步切片 → IS 段定号 → 两段传同一冻结字面量；
  **严禁**在验证层调 `evaluate_factor_key`。
- **F8 的 `roundTripCost` 必须除 `1e4`**（bps → 小数），并有量纲回归用例守门。
- **F9 的 `turnover_proxy` 由本层自算**（仓库无此指标），不可判定即拒（fail-closed）。
- **`validate_members` 对 `expr` 成员透传并强制 `name`**；`member_names` 的稳定性不得依赖成员顺序。
- **入库 `category` 恒为 `"discovered"`**，机制族不进既有类别词表。
- **续跑必先 `rebuild_kept_pool`**，与刷新复评共用同一实现；重建失败即 `paused`，绝不带残缺池开跑。
- **`discovery_enabled=False` 对自动续跑一票否决**（`paused(disabled_by_config)`）。
- **并发闸用同步 `reserve()` 预占**（非 `active_count()` 判定），守门序 403 → 400 → 413 → 409。

**开放问题（v1 按括号内默认落地，v2 再议）：**

1. 多标的（标的组）池化挖掘：v1 只支持单标的 + 预留 `symbols_json`（默认单标的）。
2. 因子与策略的**跨轴**协同（用挖到的因子直接改良既有策略的入场条件）：v1 不做。
3. 挖掘任务的定时启停（如只在特定时段跑以控成本）：v1 不做，靠手动暂停/恢复。
4. 候选的导出（CSV / JSON）与跨机器迁移：v1 不做（既有 CSV 导出范式可直接复用，留作快速增量）。
5. **（v2 新增）LLM 小时配额的跨重启持久化**：v1 按进程内滑动窗计（§2.11），代价是反复重启可绕过
   小时闸。若实测发现该绕过被真实触发，v1.1 再引入「落库墙钟 + 时钟回拨守卫」的持久化窗口。
6. **（v2 新增）`FACTOR_LIBRARY` 的 `hidden` 语义是否推广**：本轮只为 `expr` 引入。若将来还有别的
   「仅供内部桥接、不进目录」的条目，再评估是否把 `visible_factor_keys()` 提升为默认遍历口径。

---

## 评审结论

**有条件通过（approved with conditions）。**

架构主干经逐条对照源码核实后**成立**，予以保留：D-1 的 `expr` 桥确实能让挖掘产物零改地复用既有
因子 / 训练 / 组合 / 回测全管线（四处遍历现场已核实无遗漏）；D-2 的导入边界 + 测试守门是本特性
最重要的安全设计；D-3 把已测哈希放 DB 唯一索引、小状态放 JSON，正确规避了十万级候选下的检查点
膨胀；D-4 的「LLM 供给、硬编码裁决」与 D-5 的「零改既有表」都与仓库既有教训一致。文档对既有范式
（`WeightTrainManager` 长跑、`decider` 受约束调用、`_reconcile_orphan_*` 启动对账、真实数据硬约束）
的引用准确，不是套话。

v1 的问题集中在**承重细节写得不够死**——3 个 P0 里有 2 个（F6 空转、F8 量纲）属于「实现后不报错、
只是安全网从未生效或搜索永不产出」这一类，正是本仓库反复吃亏的失败模式（已提炼为 R-15）；第 3 个
（`validate_members` 丢 `name`）是文档对既有代码的**事实性误述**。这些在 v2 已逐条改死并配了双向
门禁用例。8 个 P1 与 7 个 P2 亦已全部在正文修正。**当前文档无未解决的 P0 / P1。**

放行须同时满足以下 **7 条条件**，缺一不得进入下一节点：

1. **范围以 D-6 为准**：本轮只交付 `factor` 单轴闭环（§9 第 1–6 步）。其余三轴不在本轮验收，且在
   落地前必须在 `implementedAxes` 之外、启动器不渲染、API 400——**不接受**「能选但不产候选」。
2. **三条量纲 / 定号承重条款照 §2.7.0 与 §2.7 逐行实现**，且 `test_discovery_validation.py` 的四条
   承重用例与实现同批交付。特别是 F6 必须有「IS 强、OOS 反号 ⇒ 拒」的用例——它是唯一能证明
   P0-1 没有复活的机器判据。
3. **`factors/specs.py::validate_members` 的 `name` 透传必须落地**，且 `test_factor_expr_bridge.py`
   要有「打乱成员顺序后列名与成员的对应关系不变」的断言；非 `expr` 成员的返回 dict 逐键零变化。
4. **§3.6 的两处既有测试必改**（`test_catalog_consistency`、`test_default_candidates_cover_whole_library`）
   随 §2.2 的四处改点同批提交，且是**加强**断言；`test_factor_library_expansion.py` 的既有红灯
   必须在干净 HEAD worktree 实测对账后如实标注为非回归，**严禁**顺手修改。
5. **`rebuild_kept_pool` 必须与刷新复评是同一个实现**（§2.10），并有 monkeypatch 计数断言守门；
   重建失败一律 `paused`，绝不带残缺池开跑。
6. **并发闸改同步 `reserve()` 预占**并有「连发两个请求，第二个恒 409」的时序无关用例；启动路径
   任一步抛错都要 `release()` 归还名额。
7. **实现完成后按仓库惯例在本文追加「实施过程发现的方案缺陷」小节**，如实记录与本设计的偏离。
   若实现中发现本文任一承重条款不可行，**先回来改本文**再改代码，不得在代码里静默偏离。

—— 评审人：Anthropic 工程团队资深评审（v1 → v2，2026-08-05）

---

## 十一、实施过程发现的方案缺陷

> 本节由实现节点在交付后追加（放行条件 7）。凡与本文正文有偏离的，一律在此如实记录；
> 承重条款（§2.7.0 定号协议、F8 量纲、F9 自算、`validate_members` 的 `name` 透传、
> `rebuild_kept_pool` 单一实现、同步 `reserve()` 预占、`discovery_enabled` 一票否决）
> **全部照做，无偏离**。

### 交付范围（与 D-6 / 放行条件 1 一致）

本轮交付 **`factor` 单轴闭环**（§9 第 1–6 步）。§3.1 变更计划里的
`searchers/param_searcher.py` 与 `searchers/combo_searcher.py` **未创建**——它们属于 v1.1+ 的
另外三轴，D-6 与放行条件 1 明确把本轮范围限定为因子轴。为兑现「绝不留空转轴」的强制推论：
`genomes.IMPLEMENTED_AXES == ("factor",)`，`GET /discovery/config/defaults` 只回它，启动器的轴
选择器只渲染它（`DiscoveryLauncher.test.tsx` 有断言：不得出现任何「敬请期待」占位），
`POST /discovery/runs` 收到其余三轴一律 400 `DISCOVERY_AXIS_UNSUPPORTED`。

### I-1（补强，fail-closed）：F1 增加 OOS 最少配对样本判据

**问题**：§2.7 的 F1 只写「配对样本数 ≥ `minSamples`」，没说这个门槛作用在哪一段。若只判
IS，OOS 段可能只剩十几个配对样本，此时 F6 的「同号」判据基本是掷硬币——最强的样本外防线
退化成噪声，却不会报错。

**处理**：`validation._check_sample_gates` 在 IS 满足 `minSamples` 之外，追加要求
OOS 配对样本 ≥ `max(2, minSamples × 0.2)`（常量 `MIN_OOS_SAMPLE_RATIO`）。不可判定即拒
（fail-closed，与 D-4 一致）。这是**收紧**而非放宽，不影响任何已达标候选的判定。

### I-2（阻断级）：`rebuild_kept_pool` 必须复跑 `validate_node`

**问题**：重建路径原本只 `parse_expression` 就直接求值。但被外部改坏 / 跨版本遗留的表达式
可能解析得通却已不合法（如量纲冲突、复杂度越界），一旦进池就成了一条**错误的去重基准**——
它会误杀本该入库的新候选，而且完全不可见。

**处理**：`rebuild_kept_pool` 在 `parse_expression` 之后立刻 `validate_node`，失败即进
`errors` 而非 `entries`。`test_discovery_resume_pool.py::test_rebuild_failure_signals_the_caller_to_pause`
守门。

**连带影响**：验证层的测试夹具必须用**过 `MIN_NODES=3` 的表达式**（生产里候选必先过审查层，
故这是同形的）。原先用的 `ts_zscore(volume,20)` 只有 2 节点，已改为 `neg(ts_zscore(volume,20))`
——`neg` 只翻符号，方向由 IS 段定号吸收，两段指标的绝对值逐位不变。

### I-3：单轮结束即判上限，不白等一个完整间隔

**问题**：§2.9 的时序把 `schedule` 放在 `commit` 之后，而上限判定在循环顶部。照此实现，
设了 `maxIterations=1` 的任务会在最后一轮跑完后**先睡满一个 `iterationIntervalS`**（默认
5 分钟）才收尾。功能上没错，但用户看到的是「明明跑完了却还转五分钟圈」。

**处理**：`_run` 在 `_run_iteration()` 之后立即复判一次 `_limit_reached()`，达标即刻
`_finalize("succeeded")`。与「不排队补跑」的 `overrun` 语义无关，两者互不影响。

### I-4：`normalize_budget` 必须容忍非 dict 输入

**问题**：检查点是外部可改的 JSON。`budget` 字段若被改成字符串，`normalize_budget` 会在
`.get()` 上抛 `AttributeError`，让一条已经跑了三天的任务在续跑时直接崩掉——
而 §2.10 的姿态明确是「断点损坏不该让 run 崩」。

**处理**：`normalize_budget` 接受 `object`，非 dict / 非数值一律逐键回落默认；
预算归一化补 `_scaled_to_unit`，把 6 位小数舍入的残差补给最大分量，保证严格求和为 1
（否则残差每次读写放大，最终表现为「预算加起来不是 100%」）。

### I-5：`classify_mechanism` 改为有序规则链，不用加权打分

**问题**：§2.3.2 只说「按『使用了哪些字段 + 外层算子』打分裁定」。按字面实现加权打分后，
26 个机制原型里有 **12 个被判到了别的族**——`sub(ts_mean(ret,20),ts_mean(ret,60))`（趋势）
因为 `sub` 与 `ret` 同时命中「量价背离」的偏好集而被判成量价背离。机制覆盖度、LLM 的
「优先补哪个方向」全都读这个函数，分错了就等于整套自适应在拿噪声做决策。

**处理**：改为**有序规则链**（最具辨识度的特征在前，首个命中即返回），并引入结构判据
`_has_band_deviation`（「原始价 − 其平滑值」判均值回归，「长短均线之差」判趋势——同一个
`sub` 算子的两种相反语义，加权打分从原理上分不开）。改后 26 个原型 **100% 归到本族**，
`test_discovery_mechanisms.py` 逐族守门。族定义与原型也据此微调（均为等价替换，
如趋势族原型由 `sub(ts_mean(ret,20),ts_mean(ret,60))` 改为 `sub(ema(close,20),ema(close,60))`）。

### I-6（可复用教训）：同名两类 `DiscoveryRun`

`db.models.DiscoveryRun`（ORM 行）与 `discovery.runtime.DiscoveryRun`（运行时对象）在本文
里用了同一个名字。同一文件里同时用到两者时，`delete(DiscoveryRun)` 会静默拿到运行时类并抛
`ArgumentError: subject table for an INSERT/UPDATE/DELETE expected`。测试里已显式起别名
（`DiscoveryRun as DiscoveryRunRow`）。后续新增模块若同时触碰两者，务必照此办理。

### I-7（测试基础设施）：WS 用例不得用 `TestClient.websocket_connect`

仓库 pytest 是 `asyncio_mode="auto"`，测试函数本身就是协程；在已运行的事件循环里调
`TestClient.websocket_connect` 会与 anyio portal 死锁，表现为**整条用例挂住**（不是失败，
是永远不返回）。`test_discovery_api.py` 改用 `_FakeWS` 直接调端点函数（镜像
`tests/test_agent_ws.py` 的既有做法）。

### I-8（测试基础设施）：结构性断言必须走 AST，不能扫源码文本

「本模块不得引用 X」这类不变式若用文本 `in source` 判定，会被**模块自己的文档字符串**打红
（文档字符串正是在申明这条不变式）。`test_discovery_import_boundary.py` 与
`test_discovery_promote.py` 均改为 AST 收集真实标识符后判定。

### 未覆盖的验收项（需真实环境人工确认）

以下 DoD 条目依赖真实 OKX 连通与长时间运行，**不在自动化测试覆盖内**，须在真机验收：

- **DoD #4 反向验收**：随机抽 3 个已入库候选人工核对其 OOS `rankIc` 不是全部非负。
  （已有等价的机器判据：`test_f6_rejects_out_of_sample_sign_flip` 构造 IS 强 / OOS 反号的
  真实数据并断言 F6 拒；本地端到端跑 4 轮 40 候选入库 3 条，拒绝分布覆盖
  F10/F2/F7/F4/F11/F9 与审查规则，证明 11 条闸既非恒真也非恒假。）
- **DoD #7**：连续运行 ≥6 小时的内存与 DB 增长观察。
- **DoD #15**：满负荷挖掘时 `GET /market/candles` 与 `POST /runs` 的 p95 延迟劣化 < 30%。
- **DoD #1 / #3 的真机部分**：键盘可达性与「关进程再启动」的续跑观感。

### 既有红灯基线（干净 HEAD worktree 实测对账）

**全量后端**：`2252 passed / 14 failed / 1 skipped`（243s）。14 条红灯**全部为既有基线**，
已在 `git worktree add <tmp> HEAD --detach` 的干净树上逐条实测对账：

| 文件 | 条数 | 判定依据 |
|---|---|---|
| `tests/test_ai_trade_prior_session.py` | 7 | **干净 HEAD worktree 上同样 7 条红**（逐条同名），与本特性无关 |
| `tests/test_factor_library_expansion.py` | 7 | 该文件是**未提交的孤儿测试**（HEAD 中不存在），断言 `len(FACTOR_LIBRARY) == 20` 且引用 `mom_tsmom_vol` 等**从未存在**的因子 key；HEAD 库为 12、加 `expr` 后为 13，两侧皆红 |

两者**均未被修改**——改它们会掩盖真实基线，且都是别的特性的欠账。

**全量前端**：`1353 passed`（126 文件），零红灯。`tsc --noEmit` 仅剩既有的
`components/Factor/WeightBatchArena.test.tsx` 类型错误（`TrainMethod` 字面量，本特性之前即存在）。

### 工作树里的他人在制（提交节点须注意）

本节点交付前，工作树已有另一特性（AI 交易总闸翻转）的未提交改动，与本特性**共享三个文件**：
`.env.example`（`AI_TRADE_ENABLED=true` 一行）、`desktop/src/lib/config.js`
（`env.AI_TRADE_ENABLED ??= 'true'` 一行 + 其上方注释）、`services/quant/tests/test_config.py`
（两个 `ai_trade_enabled` 用例，**本节点完全未触碰**）。提交时须做**行级暂存**，
只取本特性的 `DISCOVERY_*` hunk。

---

## 十二、终审发现与修复（提交前）

> 由终审节点在合入前逐条修复并补回归用例。六条全部指向同一类失效：**「状态写对了」不等于
> 「用户拿到的状态是对的」**——判活、投影、事件语义各自成立，串起来却不成立。

| # | 症状 | 根因 | 修复 | 守门用例 |
|---|---|---|---|---|
| RV-1 | 行情不可达转 `paused` 后，实时流一连就断，恢复后也连不上 | `_pause_with` 推了 `done`。而 §4.2 里 `done` **专指终态**：`api/discovery.py` 见 `done` 即关流，`snapshot()` 又把它永久钉在缓冲尾部 | `_pause_with` 只推 `state`（原因另有 `error` 事件承载） | `test_data_failure_emits_an_error_event_without_terminating_the_stream` 加断言：`done` 不得出现 |
| RV-2（阻断级） | `_prepare` 阶段取数 / 重建失败转 `paused` 后点「恢复」，接口回 `running`，实际一轮都不再跑 | 此路径下 `_run` 已 `return`，循环协程结束；`resume_run` 却只按 `is_terminal` 判活，翻个标志就返回。空壳还占着并发名额 | 新增 `DiscoveryRun.is_alive`（看 `_task.done()`）；`resume_run` 判活改用它，并在重装配前 `manager.drop()` 归还名额 | `test_pause_during_prepare_leaves_a_dead_loop_that_reports_itself`、`test_resume_relaunches_a_run_whose_loop_already_exited` |
| RV-3（阻断级） | `DISCOVERY_AUTO_RESUME` 永不生效，且不报任何错 | `reconcile_orphan_runs` 在启动路径被调**两次**（`init_db` 对账 + lifespan 续跑），而它只选 `status=="running"`——第一次已全部改成 `paused`，第二次必然空手而归 | 选取条件改为「仍 `running`」**或**「已被本轮对账置成 `paused` 且原因相同」，对同进程重复调用幂等 | `test_reconcile_is_idempotent_across_the_two_startup_calls` |
| RV-4 | 点了暂停，任务列表与 WS 仍显示「挖掘中」 | `request_pause` / `request_resume` 只置内部标志不改 `status`，而 `run_summary` **以内存态优先**（DB 行早已是 `paused`） | 两处同步改写 `status` 并即刻推一条 `state` | `test_pause_reflects_in_the_status_projection_and_resume_restores_it` |
| RV-5 | `revalidateOnRefresh=false` 时，数据刷新后「突然一条都收不到」，且不报错 | 重建被整个挂在该开关下。窗口右移后 kept 池的值序列与新窗口不再等长，F10 的值通道逐候选抛 `ValueError`，又被 `_validate_one` 吞成 `eval_error` | 重建**无条件**执行，开关下沉为 `_rebuild_pool(demote=...)` 只决定要不要淘汰；重建失败转 `paused(rebuild_failed)` | `test_refresh_rebuilds_the_dedupe_pool_even_when_revalidation_is_off` |
| RV-6 | 「我的因子」类别列显示英文 `discovered`，类别筛选里也挑不出挖掘因子 | §2.12 定的 `category="discovered"` 没在前端 `CATEGORY_LABELS` 登记 | 补 `discovered: "自动挖掘"`（与来源徽标「挖掘」区分） | 既有 `factor.test.ts` 的 `categoryLabel` 用例 |

**连带修正**：`test_discovery_resume_pool.py::test_resume_and_refresh_call_the_very_same_function`
原本用**文本计数** `source.count("self._rebuild_pool()")` 守「两个调用点」，RV-5 加了个关键字实参
就把它打红——正是 §十一 I-8 说的那类假阳性。已改为 AST 收集 `self._rebuild_pool` 调用节点。

**回归基线（终审复跑）**：后端全量 `2256 passed / 14 failed`，14 条红灯与 §十一「既有红灯基线」
**逐条同名**（7 条 `test_ai_trade_prior_session` + 7 条孤儿 `test_factor_library_expansion`），
未新增任何红灯；前端全量 `1353 passed`（126 文件）零红灯，`tsc --noEmit` 仅剩既有的
`WeightBatchArena.test.tsx` 类型错误。

**未纳入本次提交**：`desktop/scripts/config.test.mjs`（含本特性要求的 `DISCOVERY_` 前缀透传断言）
是**他人在制特性的未跟踪新文件**，整体属「AI 交易总闸翻转」。单独提交它会让一个断言
`AI_TRADE_ENABLED` prod 默认值的用例在缺少对应实现 hunk 的树上变红。该断言随那个特性一并合入。
