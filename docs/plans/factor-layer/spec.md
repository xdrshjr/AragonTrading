# Factor Layer（独立因子层）开发方案 — spec v2

> 需求：给回测 / 交易策略 / 对比功能，添加因子相关能力——因子计算、挖掘、评估、
> 随机森林 / 线性回归 / XGBoost 等因子权重训练、因子组合得分；作为工具提供给 Agent、
> 作为策略的交易信号；并提供因子可视化查看 / 编辑 / 激活取消的交互界面。**不把"因子"硬塞
> 进现有策略类**，而是新增一层独立的 Factor Layer：
> `行情数据 → 因子计算 → 因子评估/挖掘 → 因子组合得分 → 策略用得分做交易决策`。
>
> 本文档由 **Subtask #0（方案设计）** 产出，仅写设计、不写实现、不提交。Subtask #1（评审）
> 将在顶部追加「评审记录」、文末追加「评审结论」并升 v2；Subtask #2（开发）按 §5 文件清单
> 逐项落码，并把实施中发现的方案缺陷回填 §11。

- **版本**：v2（经 Subtask #1 评审，P0/P1 已就地修复）
- **特性目录名**：`factor-layer`
- **作者**：Subtask #0（solution architect）；评审与修复：Subtask #1（senior reviewer）
- **状态**：已评审 · **有条件通过**（条件见文末「评审结论」）

---

## 0. 评审记录（Review Notes，由 Subtask #1 填写）

> 评审方式：把方案逐节对照「可行性 / 完整性 / 一致性 / 适度性」四维，并**对照实际代码库逐条核验**
> 方案声称复用的既有符号与范式（`signal_edge.py` / `combo.py` / `CombinationStrategy` /
> `indicators.py` / `candles_to_df` / `load_range_candles` / `_seed_builtin_strategies` /
> `StrategyMeta.kind` / `BaseStrategy` / `merge_strategy` / `ToolRegistry` / `AgentToolState` /
> `SIGNAL_EDGE_REASONING_SCAFFOLD` / `main.py` 路由数 / `requirements.txt` /
> `_guard_compare_workload` / `run_in_threadpool` / 前端 `SettingsPanel` / `store.ts` persist /
> `IndicatorPane` / `plots.ts` / `chartSync` / `api.ts`）。**绝大多数复用声明属实**；下列为核验中
>发现的问题。所有 **P0/P1 已在文档正文就地修复**；P2 为建议项，作为「评审结论」的落地条件。
>
> 严重度定义：**P0** = 会产出错误结果或破坏既有能力的硬伤，必须修复；**P1** = 与代码现实冲突 /
> 自相矛盾 / 关键缺口，会让开发返工，必须修复；**P2** = 文档准确性 / 健壮性建议，落地时遵循即可。

| # | 严重度 | 维度 | 节 | 问题 | 处置 |
|---|---|---|---|---|---|
| C-P0-1 | **P0** | 完整性/正确性 | §2.5 / §2.6 | 训练端 `build_factor_matrix` 用「固定 normStats 标准化 X」拟合权重，打分端 `compose_score` 改用「滚动 z-score」——同一组权重被作用到**量纲不一致**的特征上，复合得分被静默扭曲（无报错、结果错），直接违背全局目标「稳健可靠」。 | 已修复：统一「**唯一因果归一化契约**」——训练与打分共用同一个因果滚动 z-score（窗口 `normWindow`），权重在该变换后的特征上学习并在同一变换下使用；`normStats` 仅记录 `{normWindow, clip}`，不再存固定 mean/std。 |
| C-P1-1 | **P1** | 一致性/正确性 | §2.2 / §2.6 | `direction="auto"` 在打分时「取评估出的 IC 符号」。若在 `compose_score` 的整段样本上现算 IC 符号来定方向，早期 bar 的方向用到了后期数据 → **样本内前视泄漏**，与方案自称「严格因果」矛盾。 | 已修复：`compose_score` 不接受 `auto`；组合成员方向必须是冻结的 `long/short`，在「加入组合」时由评估窗口一次性定号，打分阶段绝不重算。 |
| C-P1-2 | **P1** | 完整性/一致性 | §2.7 / §2.8 / §3.1 | 方案让 `factor_score` 走**既有** backtest/compare/walk-forward，但这些路径经 `_prepare_strategy` 不会把 `{factorSetId}` 展开成 members；而变更清单从未修改 `agent/services.py` 的注入点 → 引用式组合在回测时拿不到成员。 | 已修复：改为**创建时内联快照**（与 combo 自包含决策、R5 一致）——建 `factor_score` 策略时把组合 `members+thresholds` 快照进 `params_json`；`prepare()` 直接读、引擎零 IO；既有回测路径**零改动**。`{factorSetId}` 仅作创建期便捷入参，一次性展开为内联快照。 |
| C-P1-3 | **P1** | 一致性 | §2.4 / §4.2 / §6.1 | 挖掘超量行为自相矛盾：§2.4「抛 400」、§4.2 返回 `truncated` 标志、§6.1「413/400」三处不一致。 | 已修复：工作量超限统一抛 **413 `FACTOR_MINE_TOO_LARGE`**（沿用 `_guard_compare_workload` 的 413 语义）；`truncated` 仅表示「返回行数被 topN 截断」，绝不表示静默的工作量截断。 |
| C-P1-4 | **P1** | 完整性 | §2.6 / §2.7 | 得分阈值无意义：若 `squash=False` 得分量纲无界，固定 `enterLong/enterShort` 失去基准；且阈值默认值缺失，开发会瞎猜。 | 已修复：默认 `squash=True`（得分压到 `[-1,1]`）；给出默认阈值 `enterLong=enterShort=0.3, exitBand=0.1`；`strength=clip(\|score\|, 0, 1)`。 |
| C-P1-5 | **P1** | 一致性 | §3.2 | 前端 `StrategyKind` 当前仅 `"atomic" \| "combo"`，`StrategyDetail.tsx` **根本未按 `kind` 分支**；方案默认二者已存在。 | 已修复：变更清单新增 `lib/types.ts`（`StrategyKind` 增补 `"factor"`），并明确 `StrategyDetail.tsx` 需**新增** `kind==="factor"` 分支（非「修改既有分支」）。 |
| C-P1-6 | **P1** | 可行性 | §6.2 / §9 | DoD 要求 `npm run check` 全绿，但 `apps/web/package.json` **无 `check` 脚本**（仅 `lint`/`test`/`build`）。 | 已修复：前端门禁改为 `npm run lint` + `npm run test` + `npm run build`（Next.js build 内含 tsc 类型检查），不再引用不存在的 `npm run check`。 |
| C-P1-7 | **P1** | 可行性 | §3.2 | 变更清单改「`components/Header/*`」，但项目**无该目录**——header 内联在 `app/page.tsx`，齿轮按钮在 `components/Settings/SettingsGearButton.tsx`。 | 已修复：新增 `components/Factor/FactorButton.tsx`（镜像 `SettingsGearButton`），并在 `app/page.tsx` 内联 header 处接线（与齿轮并列）。 |
| C-P2-1 | P2 | 准确性 | §1 / §3.1 / §4.4 | 工具数量口径不一：正文「6 个」却列出 7 个名字（含 `show_factor` ui-action）。 | 已就地统一为「**6 个数据工具 + 1 个 `show_factor` ui-action（共 7 项）**」。 |
| C-P2-2 | P2 | 完整性 | §2.3 | 连续因子沿用布尔事件的 `occurrences`/`MIN_SIGNALS` 语义不清。 | 已就地定义：`occurrences` = 非 NaN 的 (因子, 前瞻收益) 配对样本数；`< MIN_SIGNALS` 标「结论谨慎」。 |
| C-P2-3 | P2 | 准确性 | §3.2 | 误称复用 `ApiError`；实际类名为 `ApiRequestError`，且 BASE 已是 `/api/py`。 | 已就地更正为 `request()` / `ApiRequestError`。 |
| C-P2-4 | P2 | 准确性 | §3.1 / §5.3 | `_combo_template_strategy_id` 在 `agent/services.py`（非 `db/session.py`）。 | 已就地把 `_factor_score_template_strategy_id` 归到 `agent/services.py`，并补对应变更行。 |
| C-P2-5 | P2 | 健壮性 | §2.2 | `px_vwap_dist`（仅日内）在日线/跨日 bar 与挖掘全库扫描下的行为未定义。 | 已就地定义：非日内 bar 返回全 NaN（coverage=0）并在挖掘中跳过该候选，绝不让整轮扫描报错。 |
| C-P2-6 | P2 | 一致性 | §3.1 / §4.4 | 新增 `category="factor"` 须能在「设置中心 · Agent 工具管理」按类别正确分组渲染。 | 落地条件：确认该管理面板按 `category` 数据驱动分组（新类别零改动可显示），否则补一行前端分组配置。 |
| C-P2-7 | P2 | 完整性 | §2.3 | `t = IR·sqrt(K)` 未定义 IC 分段数 K 及其下限，K 过小则 t 不可信。 | 落地条件：`K` 默认 6、下限 6；`K<6` 时不输出 t（标「分段不足」）。 |
| C-P2-8 | P2 | 完整性 | §2.7 / §8 | 未声明 `factor_score` 进入**实时/实盘**时与既有实时交易护栏（启用闸门、名义本金上限）的关系。 | 已就地声明：`factor_score` 作为普通策略**完全继承**既有实时交易护栏，不新增任何旁路。 |

> 维度小结：**可行性** — 后端栈足以支撑（numpy/pandas 自实现 + 可选 ML 探测），无技术阻断；前端有
> 3 处路径/脚本与现实不符（C-P1-5/6/7，已修）。**完整性** — 训练↔打分归一化契约、阈值基准、挖掘
> 超量、auto 方向因果等关键缺口已补。**一致性** — 与 combo 自包含范式、413 工作量护栏、`kind` 透传、
> persist v16→v17 对齐。**适度性** — 需求本身即「计算+评估+挖掘+训练+组合+可视化+Agent 工具+UI」，
> 文件规模（后端 6 引擎模块 + service + api + 7 工具 + 前端 11 文件 + 7 测试）与之匹配，未过度设计；
> 已在「实现顺序」中保留可分阶段交付的边界（engine 先行、UI 殿后）。

---

## 1. 概述（Overview）

Aragon Trading 现有结构是「**策略直接从指标生成信号**」：每个 `BaseStrategy` 子类在
`on_candle` 里读 `prepare` 算好的指标列，直接产出 `Signal(side, strength)`。这套结构清晰，
但把「**这个量化特征到底有没有预测力**」这件事埋进了策略内部——无法单独度量一个特征对未来
收益的解释力，也无法把多个特征用机器学习方法组合成一个统一的打分。本方案在不改动既有 5+ 原子
策略、SimBroker、回测 / 对比 / 高级回测管线的前提下，**新增一层独立、纯计算、严格因果的因子层**，
把「特征」从「策略」里解耦出来，使其可被单独计算、评估、挖掘、训练、组合，并最终以「组合得分」
的形式重新喂回策略层做交易决策。

因子层复用项目已经验证过的两条主干能力：一是 `services/quant/app/engine/signal_edge.py`
里的**事件研究 / 前瞻收益**范式（纯函数、严格因果、edge-trigger、空样本安全口径、相对基线
超额）——本方案的因子评估（IC / RankIC / 分位单调性）就是它的「连续值版」推广；二是
`engine/combo.py` + `agent/services.py` 里**「DB 存规格 → service 解析 → 注入策略 params →
线程池回测」**的组合编排范式——本方案的「因子组合得分策略」`FactorScoreStrategy` 完全沿用这套
模式（像 `regime_rotation` 那样：规格存在 `params_json`，`prepare()` 内部解析并算出得分列，
engine 层零 IO）。因此因子层不是另起炉灶，而是把两条成熟主干「拼接」成需求所要的
`计算 → 评估/挖掘 → 训练 → 组合 → 决策` 数据流。

交互上，因子层提供一个**独立的「因子实验室」全屏模态**（镜像设置中心 `SettingsPanel` 的
modal + 左导航范式），覆盖五个工作区：因子目录（库浏览与新建）、我的因子（已存 / 已挖掘因子
的表格——可视化查看、编辑、**激活 / 取消**、删除）、因子评估（IC 曲线 + 分位收益条形图 +
前瞻收益表）、因子挖掘（按区间 / 类别扫描并按 RankIC IR 排名）、权重训练与组合（选成员 → 选
方法 linear/random_forest/xgboost → 训练得到权重 → 保存为因子组合）。因子组合一旦保存，便以
`kind="factor"` 的注册策略身份**自动出现在现有策略列表 / 回测 / 对比**里，复用既有 UI，不另造
回测入口。同时新增 **6 个因子数据工具**（`list_factors` / `compute_factor` / `evaluate_factor` /
`mine_factors` / `train_factor_weights` / `score_factor_combo`）**+ 1 个 `show_factor` ui-action
（共 7 项）**，经既有 `ToolRegistry` + `AgentToolState` 自动获得「设置中心 · Agent 工具管理」里的
启用开关（新增 `category="factor"`，须确认该面板按 category 数据驱动分组，见评审 C-P2-6）。

**关键技术决策（贯穿全文）**：项目刻意只依赖 `numpy + pandas`（`requirements.txt` 明确「零新增
三方依赖」，C9 自实现指标以规避兼容问题；桌面端 PyInstaller 冻结也容不下 sklearn/xgboost 的体量）。
而需求显式要求「随机森林 / 线性回归 / XGBoost」。本方案以**可插拔训练器注册表 + 能力探测**化解此
张力：`linear`（纯 numpy 岭回归，**永远可用、默认**）+ `equal` / `ic_weight`（零训练基线，永远
可用）作为核心；`random_forest` / `xgboost` 作为**可选训练器**——运行时用 `importlib.util.find_spec`
探测，缺库即如实返回「训练器不可用，请安装 requirements-ml.txt」的显式错误（HTTP 400 /
`ToolResult(ok=False)`），**绝不假数据、绝不静默降级**（C1）。核心因子能力因此零新增强制依赖即可
完整工作，同时为进阶用户保留 RF/XGB 选项。

---

## 2. 技术设计（Technical design）

### 2.1 分层与数据流

因子层落在「engine（纯计算）/ service（编排取数）/ strategy（消费得分）/ api（路由）/ agent tools
（薄包装）/ web（UI）」六个既有分层里，新增一个 `engine/factors/` 子包与一个 `app/factors/`
service 子包，**不动**任何现有 engine / strategy / 路由的行为：

```
真实 OKX K 线
  │  services.load_range_candles(rest, instId, bar, startTs, endTs, max_bars)   ← 既有，全区间分页 + confirm 过滤 + 空区间 400
  ▼
candles_to_df(candles) → df[ts,o,h,l,c,vol] 升序                                ← 既有 engine/backtester.py
  ▼
[1] 因子计算   engine/factors/library.py
      FACTOR_LIBRARY: {key: FactorSpec(cn, category, directionHint, paramsSchema, compute)}
      compute_factor(df, key, params) -> pd.Series（与 df 等长、index 0..n-1、严格因果，只用 ≤ i 数据）
  ▼
[2] 因子评估   engine/factors/evaluation.py
      evaluate_factor(df, values, direction, horizons, quantiles) -> FactorEvalResult
        · IC = Pearson(factor[i], fwdRet[i,h])，RankIC = Spearman；IC 序列的 mean/std/IR/t
        · 分位前瞻收益（quantile buckets）单调性、top-bottom 价差、coverage、|autocorr| 换手代理
  ▼  因子挖掘   engine/factors/mining.py
      mine_factors(df, candidates, horizons) -> 按 |RankIC IR| 降序的候选排名（纯计算，无 IO）
  ▼
[3] 因子权重训练 engine/factors/training.py
      build_factor_matrix(df, members) -> (X[n,k] 因果对齐, colNames, normStats)
      label = forward_return_label(df, horizon)（因果，丢尾部 h 行）
      TRAINERS: {linear(numpy ridge), equal, ic_weight}（恒可用） + {random_forest, xgboost}（可选探测）
      train_weights(X, y, names, method, config) -> TrainResult(weights/importances, normStats, metrics, available, note)
  ▼
[4] 因子组合得分 engine/factors/scoring.py
      compose_score(df, members, weights, normWindow, squash) -> pd.Series 复合得分（z-score 加权和，可 tanh 压缩到 [-1,1]）
  ▼
[5] 策略决策   strategies/factor_score_strategy.py
      FactorScoreStrategy(meta.kind="factor")：prepare() 用 compose_score 算出 score 列；
        on_candle(i)：score[i] ≥ enterLong → long（strength=clip(|score|,0,1)）; ≤ -enterShort → short; 落入退出带 → close
```

DB 里新增两张表 `factors` / `factor_sets`：前者是「单个因子定义实例」（库 key + params + 方向 +
**enabled 激活态** + source 来源 + 缓存评估），对应需求里「看到所有挖掘出的因子、编辑、激活取消」；
后者是「命名的因子组合（含训练出的权重 / 重要度 + 归一化统计 + 复合 IC）」，可被
`FactorScoreStrategy` 引用。

### 2.2 因子库（FACTOR_LIBRARY，唯一真源 C6）

`engine/factors/library.py` 内置一组**严格因果、相对量纲**的因子（全部复用
`engine/indicators.py` 的 `ema/sma/rsi/bollinger/atr/vwap_session` 纯函数，杜绝重复实现）。
每条目 `FactorSpec(cn, category, directionHint, paramsSchema, compute)`，`compute(df, params)`
返回与 df 等长的 `pd.Series`（NaN 暖机段由评估 / 组合层统一按「跳过」处理，绝不补零外推 C1）。
初版至少覆盖五类共 ≥12 个因子：

| key | 类别 category | 一句话定义（cn） | directionHint | 主要 params |
|---|---|---|---|---|
| `mom_roc` | momentum | N 根收益率动量 | long | `win=20` |
| `mom_ema_slope` | momentum | EMA 斜率（归一化） | long | `period=20` |
| `trend_ema_dist` | trend | 收盘相对 EMA 偏离度 | long | `period=60` |
| `trend_adx_proxy` | trend | 方向性强度代理（基于 ATR/区间） | long | `period=14` |
| `rev_rsi` | reversal | RSI 反向（超买超卖） | short | `period=14` |
| `rev_boll_z` | reversal | 收盘在布林带内的 z 位置 | short | `period=20,std=2` |
| `vol_atr_pct` | volatility | ATR / 收盘（相对波动） | short | `period=14` |
| `vol_realized` | volatility | 对数收益滚动标准差 | short | `win=20` |
| `volu_zscore` | volume | 成交量 z-score | long | `win=20` |
| `volu_obv_slope` | volume | OBV 斜率（资金流向） | long | `win=20` |
| `liq_amihud` | liquidity | Amihud 非流动性（|ret|/vol） | short | `win=20` |
| `px_vwap_dist` | mean_revert | 收盘相对 session VWAP 偏离 | short | （仅日内 bar） |

`directionHint` 表达「因子值高时倾向看多还是看空」，评估时据此把因子方向化；用户可在因子表里
**覆盖方向**（`direction = long | short | auto`，`auto` 取评估出的 IC 符号）。**因果约束（评审 C-P1-1）**：
`auto` 仅用于「评估 / 我的因子」展示，其符号由**当期评估窗口**一次性定号并冻结；一旦该因子被「加入组合」，
其在 `members` 里的 `direction` 必须是已冻结的 `long | short` 具体值。**`compose_score` 不接受 `auto`**——
打分阶段绝不在整段样本上现算 IC 符号来定方向（否则早期 bar 的方向用到后期数据，构成样本内前视泄漏）。

**bar 兼容性（评审 C-P2-5）**：`px_vwap_dist` 依赖 session VWAP，仅对**日内 bar** 有意义。对日线 /
跨日 bar，`compute_factor` 返回**全 NaN**（`coverage=0`），评估 / 组合层按既有「NaN 跳过」口径处理；
挖掘（§2.4）扫描全库时，对不兼容当前 bar 的候选**直接跳过**（标注 `note="bar_incompatible"`），
绝不让单个候选异常拖垮整轮扫描。

模块导出：`FACTOR_LIBRARY`、`available_factors() -> list[dict]`（目录，供 UI / Agent primer
共用真源）、`compute_factor(df, key, params) -> pd.Series`、`factor_meta(key) -> dict | None`。

### 2.3 因子评估（evaluation.py，signal_edge 的连续值推广）

复用 `signal_edge.py` 的因果前瞻范式，但把「布尔事件」换成「连续因子值」：

- **前瞻收益**：`fwd_ret[i,h] = c[i+h]/c[i] - 1`（或 `next_open` 口径对齐撮合），仅 `i+h ≤ n-1`
  的根计入（右端因果截断，绝不补零，沿用 `signal_edge._horizon_returns` 的截断逻辑）。
- **IC**：`pearson(factor[i], dir·fwd_ret[i,h])`；**RankIC**：`spearman(...)`（用 `pd.Series.rank`
  做秩相关，纯 numpy/pandas）。对每个 horizon 输出一对。
- **滚动 IC 序列 → IR**：把样本按时间等切成 `K` 段（默认 `K=6`，**下限 6**）算逐段 IC，得
  `ICmean / ICstd / IR=ICmean/ICstd / t=IR·sqrt(K)`，度量因子的**稳定性**（直接服务全局目标
  「稳健、可靠、高成功率」）。**当有效段数 `< 6`（样本太短无法稳定切段）时不输出 `t`**（置 `None`
  并标 `note="segments_insufficient"`），避免给出不可信的显著性（评审 C-P2-7）。
- **分位前瞻收益**：把因子值按当期可见分布分 Q 档（默认 5），算各档平均前瞻收益与
  **单调性得分**（Spearman(档位序, 档位均收益)）、top-bottom 价差。
- **覆盖率 coverage**（非 NaN 占比）、**换手代理**（1 - |lag1 自相关|，越高越频繁翻转 → 成本越高）、
  **样本充分性**：`occurrences` 在连续因子语境下**定义为「非 NaN 的 (因子值, 前瞻收益) 配对样本数」**
  （而非布尔事件计数）；`occurrences < MIN_SIGNALS`（沿用既有 `MIN_SIGNALS=20` 常量）时标注「结论谨慎」（评审 C-P2-2）。

导出 `@dataclass FactorEvalResult`（字段 camelCase，与前端对齐，序列化即 `asdict`，沿用
`signal_edge.HorizonStat` 风格）+ `evaluate_factor(df, values, direction, *, horizons, quantiles,
ic_segments) -> FactorEvalResult` + `evaluate_factor_key(df, key, params, direction, ...)` 便捷封装。

### 2.4 因子挖掘（mining.py）

`mine_factors(df, candidates, *, horizons, top_n) -> list[dict]`：对候选集合（默认整库 ×
可选小参数网格）逐一 `compute_factor` + `evaluate_factor`，按主排序键 `abs(RankIC IR @ 主
horizon)` 降序，返回 `[{factorKey, params, direction, category, ic, rankIc, ir, monotonicity,
coverage, occurrences, note}]`。纯计算、无 IO、严格因果；候选数 × 区间根数设工作量上界（沿用
`agent.services._guard_compare_workload` 的「expanded_count × effective_bars ≤ LIMIT」护栏思路，
该护栏现实现为 **HTTP 413**），**超限统一抛 413 `FACTOR_MINE_TOO_LARGE`**（与既有护栏 413 语义一致，
评审 C-P1-3）。返回体里的 `truncated` 仅表示「返回行数被 `topN` 截断」，**绝不**用于表示静默的工作量
截断——工作量超限只会 413、不会悄悄少算。挖掘结果可由 service 层「保存 top-N 为 `factors` 行
（source="mined"）」。

### 2.5 因子权重训练（training.py，可插拔训练器 + 能力探测）

```python
def build_factor_matrix(df, members, *, norm_window) -> tuple[np.ndarray, list[str], dict]:
    """逐成员 compute_factor → 方向化 → **施加与打分端 §2.6 完全相同的因果滚动 z-score
    （窗口 norm_window）** → 因果对齐成矩阵 X[n,k]；返回 (X, names, normStats)。
    丢弃任一因子为 NaN 的暖机行（统一 dropna，绝不插值 C1）。"""

def forward_return_label(df, horizon) -> np.ndarray:
    """y[i] = c[i+horizon]/c[i]-1，因果，尾部 horizon 行无标签（与 X 对齐裁掉）。"""

TRAINERS: dict[str, Trainer]  # name -> Trainer（capability + fit）
def train_weights(X, y, names, *, method, config) -> TrainResult:
    """按 method 取训练器；不可用即 TrainResult(available=False, note=安装提示)，绝不假权重。"""
```

- **时序切分**：训练 / 验证按时间先后切（默认前 70% 训练、后 30% 验证），**绝不打乱**（杜绝未来
  泄漏，鲁棒性硬保证）；`TrainResult.metrics` 同时回 in-sample 与 **out-of-sample** 复合得分 IC，
  UI 据此提示「样本外 IC 显著低于样本内 → 过拟合风险」。
- **训练器**：
  - `equal`：等权（恒可用，零训练基线）。
  - `ic_weight`：权重 ∝ 各因子 RankIC IR（符号取 direction）（恒可用）。
  - `linear`：在**已经过因果滚动 z-score 的 X**（见下「归一化契约」）上做**岭回归闭式解**
    `w=(XᵀX+λI)⁻¹Xᵀy`（`np.linalg.solve`），系数归一到 `sum|w|=1`（恒可用、默认方法）。
  - `random_forest`：`sklearn.ensemble.RandomForestRegressor` → `feature_importances_` 作权重
    （**可选**，`find_spec("sklearn")` 探测）。
  - `xgboost`：`xgboost.XGBRegressor` → gain 重要度（**可选**，`find_spec("xgboost")` 探测）。
- `TrainResult(method, weights: dict[name,float], normStats, metrics: dict, available: bool,
  trainerVersion: str | None, note: str)`，序列化进 `factor_sets.model_json`。`normStats` 只记录
  归一化**契约参数** `{normWindow, clip}`，**不**记录固定 mean/std（见下）。

> **归一化契约（评审 C-P0-1，唯一真源）**：因子标准化在**训练端 `build_factor_matrix` 与打分端
> `compose_score` 共用同一个因果滚动 z-score 函数**（同一 `norm_window`、同一 clip 口径）。权重在该
> 变换后的特征上学习，又在同一变换后的特征上使用——两端量纲一致，学到的权重直接可用。**严禁**训练端
> 用「整窗固定 mean/std」标准化、打分端却用「滚动 z-score」：那会让权重作用到不同量纲的特征上，静默
> 扭曲复合得分。`norm_window` 由组合 `thresholds.normWindow`（默认 200）统一提供，训练与打分必须取同值。

### 2.6 因子组合得分（scoring.py）

`compose_score(df, members, weights, *, norm_window=200, squash=True) -> pd.Series`：

1. 逐成员 `compute_factor` → 方向化（`dir·value`）。**`dir` 必须是冻结的 `+1/-1`（long/short）；
   不接受 `auto`**（评审 C-P1-1，杜绝打分期按全样本 IC 现算符号造成的前视泄漏）。
2. **因果滚动 z-score**（窗口 `norm_window`，默认 200；用 `rolling(window).mean()/std()`，只用
   ≤ i 数据），消除量纲差异。**此变换必须与训练端 `build_factor_matrix` 逐字一致**（归一化契约，
   §2.5）。
3. 加权求和 `Σ wⱼ·z(factorⱼ)`，**默认 `squash=True`**，用 `tanh` 压缩到 `[-1,1]`，使阈值有恒定
   基准（评审 C-P1-4）；`squash=False` 仅供高级用户，此时得分无界、需自定阈值。
4. 返回与 df 等长 Series（暖机段 NaN）。

纯计算、无 IO、严格因果——这是「策略用得分做交易决策」的唯一数值来源，回测与实时共用同一函数。

### 2.7 因子组合策略（factor_score_strategy.py，复用 combo 注入范式）

```python
@register_strategy
class FactorScoreStrategy(BaseStrategy):
    meta = StrategyMeta(key="factor_score", name="因子组合得分", kind="factor",
                        description="读取因子组合复合得分，按阈值产出多空/平仓信号", ...)
    def prepare(self, df, bar):           # 解析 self.params["factorSet"] → compose_score → df["_score"]
    def warmup(self):                     # max(各因子暖机, norm_window)
    def on_candle(self, df, i, ctx):      # 读 df["_score"].iloc[i] → Signal
```

- **规格注入（评审 C-P1-2，改为创建时内联快照）**：与 `regime_rotation` 的**自包含**决策一致——
  组合规格 `{members:[{factorKey,params,weight,direction}], thresholds:{enterLong,enterShort,
  exitBand,normWindow,squash}}` 直接存 `strategies.params_json`，`prepare()` 直接读取、引擎层零 DB/IO。
  「从因子组合新建策略」时，由**创建流程**（前端 `FactorSetBuilder` 或 Agent 经 `factors/service` 的
  `resolve_set_to_spec` 助手）把所选 `factor_sets` 行的 `members+thresholds` **一次性快照内联**进
  `params_json`；`{factorSetId}` 仅作创建期便捷入参、创建时即展开为内联快照。**因此既有
  `agent/services._prepare_strategy` 与 backtest/compare/walk-forward 路径零改动**（它们本就直接用
  `params_json` 实例化），无需新增「实例化前解析 factorSetId」的注入逻辑。该决策同时消除 R5 的悬空引用。
- **阈值默认（评审 C-P1-4）**：`thresholds` 默认 `{enterLong:0.3, enterShort:0.3, exitBand:0.1,
  normWindow:200, squash:true}`；`squash=true` 下得分 ∈ `[-1,1]`，这些阈值才有恒定基准。
- **空 / 失效保护**：`members` 为空或某 `factorKey` 不在库内 → `prepare` 标记 `df["_score"]=NaN`，
  `on_candle` 在 NaN 段不产信号（**不**抛错，避免拖垮回测；与 combo 的 `on_candle` 直接抛错策略
  不同——这里走「安静空仓」更稳，因为 factor_score 会进 compare 多策略混排）。**但创建 / 编辑组合的
  API（§4）须在边界处对空 members / 未知 factorKey / 非法 direction 做一次性显式校验并报 400**——
  「安静空仓」只用于回测运行期，绝不用来掩盖配置错误（C1 边界一次性校验）。
- **实时 / 实盘护栏（评审 C-P2-8）**：`factor_score` 只是新增的一类普通策略，进入实时 / 实盘时
  **完全继承既有实时交易护栏**（启用闸门、名义本金上限、人工确认门），**不新增任何旁路**。
- 注册即被 `_seed_builtin_strategies` 自动种入一行 builtin 锚（像 `regime_rotation` 锚那样，供
  「预览未保存组合」时落 runId）。`merge_strategy` 已透传 `meta.kind`，前端据 `kind==="factor"`
  把该策略详情的「编辑」入口指向因子实验室。

### 2.8 关键代码路径（端到端时序）

**评估一个因子（UI 或 Agent）**：
`POST /api/factors/evaluate {factorKey,params,direction,instId,bar,startTs,endTs,horizons}` →
`factors/service.evaluate(...)` → `load_range_candles`（真实 OKX）→ `candles_to_df` →
`run_in_threadpool(compute_factor + evaluate_factor)` → 返回 `FactorEvalResult` dict（IC 曲线 /
分位 / 前瞻表）→ 若 `factorId` 给定则把摘要缓存进 `factors.eval_json`。

**训练 + 保存因子组合**：
`POST /api/factor-sets/train {members,method,config,instId,bar,startTs,endTs,horizon}` →
`service.train(...)` → 取数 → `build_factor_matrix` + `forward_return_label` →
`run_in_threadpool(train_weights)` → 返回 `{weights,metrics,available,note}`；前端确认后
`POST /api/factor-sets {name,members,method,modelJson,metricsJson}` 落库 → 用户在策略面板
「从因子组合新建策略」→ 创建流程把该组合的 `members+thresholds` **快照内联**进策略 `params_json`
（`create_strategy(templateKey="factor_score", params={members,thresholds})`；`factorSetId` 若给出则
在此一次性展开为快照）→ 此后走**既有** `/strategies/{id}/backtest` / `/strategies/compare` /
`/strategies/{id}/walk-forward`，**`_prepare_strategy` 无需改动**——它本就直接用 `params_json` 实例化，
内联快照让引擎层零 DB 依赖即可拿到完整规格（评审 C-P1-2）。

**因子 / 得分上图**：`POST /api/factors/compute`（或 `/factor-sets/{id}/score`）→ 返回
`[{ts,value}]` 序列 → 前端作为一条振荡指标 pane（复用 `IndicatorPane` / `plots.ts` / `chartSync`
范式）叠加在 K 线下方；Agent 经 `show_factor` ui-action 驱动同一叠加（复用 `ShowChartTool` 范式）。

---

## 3. 文件 / 模块变更计划（File / module change plan）

### 3.1 后端（services/quant）

| 文件 | 新建/修改 | 一句话意图 |
|---|---|---|
| `app/engine/factors/__init__.py` | 新建 | 因子子包导出聚合（library/evaluation/mining/training/scoring）。 |
| `app/engine/factors/library.py` | 新建 | `FACTOR_LIBRARY` 唯一真源 + `compute_factor` + `available_factors`（复用 indicators 纯函数，严格因果）。 |
| `app/engine/factors/evaluation.py` | 新建 | IC/RankIC/IR/分位单调性/覆盖/换手评估（signal_edge 连续值推广，纯计算）。 |
| `app/engine/factors/mining.py` | 新建 | 库 × 参数网格扫描并按 RankIC IR 排名 + 工作量护栏。 |
| `app/engine/factors/training.py` | 新建 | 因子矩阵构建 + 标签 + 可插拔训练器注册表（linear/equal/ic_weight 恒可用，rf/xgb 探测）。 |
| `app/engine/factors/scoring.py` | 新建 | 因果滚动 z-score 加权复合得分 `compose_score`（回测/实时唯一数值源）。 |
| `app/strategies/factor_score_strategy.py` | 新建 | `FactorScoreStrategy`（`kind="factor"`，prepare 解析得分、on_candle 按阈值产信号）。 |
| `app/strategies/__init__.py` | 修改 | 导入新策略触发 `@register_strategy`（追加到 BUILTIN_ORDER 末尾）。 |
| `app/factors/__init__.py` | 新建 | factors service 子包标记。 |
| `app/factors/service.py` | 新建 | 编排：取数（复用 `agent.services.load_range_candles`）+ 线程池计算 + `resolve_set_to_spec(set_row) -> {members,thresholds}` 助手（创建策略时把因子组合**快照内联**进 `params_json`，引擎零 IO；不在回测期注入）。 |
| `app/factors/specs.py` | 新建 | 因子 / 组合规格解析与校验（镜像 `strategies/combo_specs.py`：`build_members`/`spec_from_row`）。 |
| `app/db/models.py` | 修改 | 新增 `Factor`、`FactorSet` ORM 模型（JSON 列存 params/members/model/metrics）。 |
| `app/db/repositories.py` | 修改 | 新增 factor / factor_set 的 CRUD + 列表 + 评估缓存写入。 |
| `app/db/session.py` | 修改 | `_seed_builtin_factors`（可选种入若干内置因子）+ factor_score builtin 锚由既有 seed 自动覆盖。 |
| `app/schemas.py` | 修改 | 新增 factor / factor-set 请求模型 + `factor_out`/`factor_set_out` 序列化器（camelCase）。 |
| `app/api/factors.py` | 新建 | 因子 / 因子组合 REST 路由（CRUD + evaluate/compute/mine/train/score）。 |
| `app/main.py` | 修改 | `app.include_router(factors.router, prefix="/api")`（**第 9 个路由**，现有 8 个，已核验）。 |
| `app/agent/services.py` | 修改 | 新增 `_factor_score_template_strategy_id`（预览未保存因子组合时复用 builtin 锚落 runId，**与既有 `_combo_template_strategy_id` 同文件、同范式**；评审 C-P2-4）。**不**改 `_prepare_strategy`（C-P1-2 内联快照后无需）。 |
| `app/agent/tools/factor_tools.py` | 新建 | 6 个因子 Agent 工具（薄包装 service/engine，category="factor"）。 |
| `app/agent/tools/registry.py` | 修改 | 把因子工具加入 `_ALL_TOOLS`。 |
| `app/agent/prompts.py` | 修改 | 系统提示追加因子能力 primer + 因子推理脚手架（镜像 `SIGNAL_EDGE_REASONING_SCAFFOLD`）。 |
| `requirements-ml.txt` | 新建 | 可选 ML 依赖（scikit-learn / xgboost），核心不依赖，文档说明探测降级。 |

### 3.2 前端（apps/web）

| 文件 | 新建/修改 | 一句话意图 |
|---|---|---|
| `lib/factorTypes.ts` | 新建 | 因子 / 评估 / 组合 / 训练结果的 TS `interface`（与后端 camelCase 对齐）。 |
| `lib/factorApi.ts` | 新建 | 因子 REST 客户端（复用 `api.ts` 的 `request()` 与 `ApiRequestError`；BASE 已是 `/api/py`，经 Next.js rewrite 到后端 `/api`，评审 C-P2-3）。 |
| `lib/factor.ts` | 新建 | 纯展示/几何 helper：IC 格式化、分位条形几何、权重条几何、组合规格校验。 |
| `lib/factor.test.ts` | 新建 | `lib/factor.ts` 纯函数单测（正常 + 异常路径）。 |
| `lib/factorSeries.ts` | 新建 | 因子 / 得分序列 → lightweight-charts 点位映射（复用 ohlc/indicators 范式）。 |
| `components/Factor/FactorLab.tsx` | 新建 | 因子实验室全屏模态外壳（镜像 SettingsPanel：focus trap/Esc/左导航/5 工作区）。 |
| `components/Factor/FactorCatalog.tsx` | 新建 | 因子目录：库浏览 + 「加入我的因子」。 |
| `components/Factor/FactorTable.tsx` | 新建 | 我的因子表：查看 / 编辑 / **激活取消(Switch)** / 删除 + IC/IR 列。 |
| `components/Factor/FactorEvalPanel.tsx` | 新建 | 因子评估：IC 曲线 + 分位收益条形 + 前瞻收益表。 |
| `components/Factor/FactorMiningPanel.tsx` | 新建 | 因子挖掘：区间/类别 → 排名表 → 保存为因子 / 加入组合。 |
| `components/Factor/FactorSetBuilder.tsx` | 新建 | 权重训练与组合：选成员 + 方法选择 + 训练 → 权重条 + 复合 IC + 保存。 |
| `components/Factor/FactorScoreChart.tsx` | 新建 | 复合得分 / 单因子值的振荡 pane（复用 IndicatorPane/chartSync）。 |
| `components/Factor/FactorButton.tsx` | 新建 | 「因子」入口按钮，**镜像 `components/Settings/SettingsGearButton.tsx`**（项目无 `components/Header/` 目录，header 内联在 `app/page.tsx`；评审 C-P1-7）。 |
| `lib/types.ts` | 修改 | `StrategyKind` 由 `"atomic" \| "combo"` **增补 `"factor"`**（后端 `merge_strategy` 已透传 `kind`，前端类型须跟上；评审 C-P1-5）。 |
| `components/Strategy/StrategyDetail.tsx` | 修改 | **新增** `kind==="factor"` 分支（当前组件不按 `kind` 分支）：显示「在因子实验室编辑」+ 引用的因子组合摘要（评审 C-P1-5）。 |
| `lib/store.ts` | 修改 | 新增 `showFactorLab`(瞬态)/`factorLabSection`(持久)/`factorOverlay`(瞬态) + persist v16→17 + migrate。 |
| `app/page.tsx` | 修改 | 在**内联 header**（既有 `<header>` 区，齿轮按钮旁）接入 `<FactorButton/>`；并挂载 `<FactorLab/>` 模态（与 `SettingsPanel` 并列，沿用 `showSettings` 同款「flag 切换主区」范式），接 store 开关。 |

### 3.3 测试（与实现同目录约定）

| 文件 | 新建 | 意图 |
|---|---|---|
| `services/quant/tests/test_factor_library.py` | 新建 | 因子计算因果性 / 形状 / NaN 暖机 / 库目录一致性。 |
| `services/quant/tests/test_factor_evaluation.py` | 新建 | IC/RankIC 正负号、分位单调性、空样本安全口径、右端因果截断。 |
| `services/quant/tests/test_factor_training.py` | 新建 | linear 权重确定性、时序切分无泄漏、rf/xgb 缺库时 `available=False` 显式降级。 |
| `services/quant/tests/test_factor_scoring.py` | 新建 | 滚动 z-score 因果性、空成员 → NaN、tanh 压缩边界。 |
| `services/quant/tests/test_factor_score_strategy.py` | 新建 | 阈值产信号、空组合安静空仓、warmup 正确、回测可跑通。 |
| `services/quant/tests/test_factors_api.py` | 新建 | CRUD / evaluate / mine / train / score 路由 + 激活取消 + 错误码（真实 OKX 桩）。 |
| `services/quant/tests/test_factor_tools.py` | 新建 | 6 个数据工具的 ok/ok=False 摘要与 payload + `show_factor` ui-action 负载 + 启用闸门。 |

---

## 4. 接口设计（Interface design）

所有路由挂 `/api`；字段 camelCase；时间秒级 UTC；错误体统一 `{error:{code,message}}`（沿用
`main.py` 异常处理器）。取数一律走 `load_range_candles`（区间）或 `get_candles`（最新 limit 根，
剔 forming），**绝不假数据**（C1）。

### 4.1 因子库与因子 CRUD

- `GET  /api/factors/library` → `[{key,cn,category,directionHint,paramsSchema}]`（静态库目录）。
- `GET  /api/factors?enabled=&category=` → `[Factor]`（已存因子列表，支持激活态 / 类别过滤）。
- `POST /api/factors` `{factorKey,name,params,direction,source?}` → `201 Factor`。
- `PUT  /api/factors/{id}` `{name?,params?,direction?,enabled?}` → `Factor`（**enabled 即激活/取消**）。
- `DELETE /api/factors/{id}` → `204`（mined/custom 可删；内置 builtin 因子 `409 BUILTIN_READONLY`）。

### 4.2 计算 / 评估 / 挖掘

- `POST /api/factors/compute` `{factorKey,params,instId,bar,startTs,endTs}` →
  `{instId,bar,points:[{ts,value}],coverage}`（上图用）。
- `POST /api/factors/evaluate` `{factorKey|factorId,params?,direction?,instId,bar,startTs,endTs,
  horizons?,quantiles?}` → `FactorEvalResult`（IC 曲线 / 分位 / 前瞻表）；给 `factorId` 时缓存摘要。
- `POST /api/factors/mine` `{instId,bar,startTs,endTs,categories?,horizons?,topN?,persist?}` →
  `{items:[RankedFactor],truncated}`（`truncated` 仅指「返回行数被 `topN` 截断」）；**工作量超限 →
  413 `FACTOR_MINE_TOO_LARGE`**（绝不静默少算，评审 C-P1-3）；`persist=true` 时把 topN 落
  `factors`(source="mined")。

### 4.3 因子组合（factor sets）与训练

- `GET  /api/factor-sets?enabled=` → `[FactorSet]`。
- `POST /api/factor-sets` `{name,members,method,modelJson,metricsJson,enabled?}` → `201 FactorSet`。
- `PUT  /api/factor-sets/{id}` / `DELETE /api/factor-sets/{id}`（同因子 CRUD 语义）。
- `POST /api/factor-sets/train` `{members,method,config?,instId,bar,startTs,endTs,horizon?}` →
  `{method,weights,normStats,metrics:{icIn,icOut,...},available,note}`；`available=false`（缺
  rf/xgb 库）→ HTTP `400 FACTOR_TRAINER_UNAVAILABLE`（含安装提示），绝不回假权重。
- `POST /api/factor-sets/{id}/score` `{instId,bar,startTs,endTs}` →
  `{points:[{ts,score}],latest,coverage}`（上图 / 当前打分）。

### 4.4 Agent 工具签名（input_schema 摘要）

| 工具 name | category | 关键入参 | 产出 |
|---|---|---|---|
| `list_factors` | factor | `enabledOnly?` | 库 + 已存因子目录（summary + payload）。 |
| `compute_factor` | factor | `factorKey,params?,instId,bar,limit?/startTs,endTs` | 最近值 + 简述。 |
| `evaluate_factor` | factor | `factorKey,instId,bar,horizons?,startTs?,endTs?` | IC/RankIC/IR/分位摘要 + 推理脚手架。 |
| `mine_factors` | factor | `instId,bar,startTs,endTs,categories?,topN?` | RankIC IR 排名 top-N。 |
| `train_factor_weights` | factor | `members,method?,instId,bar,startTs,endTs,horizon?` | 权重 + 样本内外 IC（缺库 ok=False）。 |
| `score_factor_combo` | factor | `factorSetId,instId,bar` | 复合得分最新值 + 近窗统计。 |
| `show_factor` (ui-action) | factor | `factorKey?/factorSetId?` | 驱动前端叠加因子/得分 pane。 |

工具异常统一在 `ToolRegistry.execute` 收敛为 `ToolResult(ok=False)`（OKX 不可达 / 校验失败 /
训练器缺失皆如实回报）；启用态经 `AgentToolState` 自动接入设置中心，禁用工具双闸门拦截。

---

## 5. 数据模型（Data model）

### 5.1 新增 ORM（app/db/models.py，所有 ts 秒级 UTC，C2）

```python
class Factor(Base):
    __tablename__ = "factors"
    id: int (pk)
    factor_key: str(64)        # 指向 FACTOR_LIBRARY（非真源——库才是真源 C6）
    name: str(128)
    params_json: Text = "{}"   # 覆盖库默认参数
    direction: str(8) = "auto" # long | short | auto
    category: str(32) = ""     # 冗余存，便于列表过滤
    source: str(16) = "custom" # builtin | mined | custom
    enabled: bool = True       # 激活 / 取消（需求显式）
    eval_json: Text | None     # 最近一次评估摘要缓存（IC/RankIC/IR + 评估区间），可空
    notes: str(256) = ""
    created_at / updated_at: DateTime

class FactorSet(Base):
    __tablename__ = "factor_sets"
    id: int (pk)
    name: str(128)
    members_json: Text = "[]"  # [{factorKey,params,weight,direction}]（自包含快照，不引用 factors.id）
    method: str(24) = "linear" # equal | ic_weight | linear | random_forest | xgboost
    config_json: Text = "{}"   # 训练配置（horizon/split/正则等）+ thresholds（enterLong/enterShort/exitBand/normWindow/squash）
    model_json: Text = "{}"    # 训练产物：weights/importances/normStats/trainerVersion
    metrics_json: Text | None  # 复合得分样本内外 IC 等
    enabled: bool = True
    created_at / updated_at: DateTime
```

`members_json` 采用**自包含快照**（与 `ComboMemberIn` 同决策：不引用其它行主键，避免因子被删/改后
组合悬空）。`factor_score` 策略的 `params_json` 形如 `{"factorSetId": 7}`（引用保存的组合）**或**
`{"members":[...],"thresholds":{...}}`（内联临时组合），service 层二选一解析注入。

### 5.2 关键内存结构（dataclass，camelCase 序列化）

- `FactorEvalResult{factorKey,direction,instId?,bar?,bars,windowStartTs,windowEndTs,coverage,
  occurrences,horizons:[{h,ic,rankIc,icMean,icStd,ir,tStat,monotonicity,topBottomSpread,
  quantiles:[{q,meanRet,count}]}],note}`。
- `TrainResult{method,weights:{name:float},normStats,metrics:{icIn,icOut,r2In,r2Out,nTrain,nValid},
  available:bool,trainerVersion:str|None,note:str}`。
- `RankedFactor{factorKey,params,direction,category,ic,rankIc,ir,monotonicity,coverage,
  occurrences,note}`。

### 5.3 迁移与种子

- 建表走既有 `Base.metadata.create_all`（`init_db`）——新表幂等创建，**不需手写迁移**（SQLite +
  新表，无 ALTER）。
- `factor_score` 注册即被既有 `_seed_builtin_strategies` 自动种入 builtin 锚行（用于预览未保存组合
  落 runId，沿用 `_combo_template_strategy_id` 思路新增 `_factor_score_template_strategy_id`）。
- 可选 `_seed_builtin_factors`：把库里若干代表性因子种成 `source="builtin"` 行，让「我的因子」首屏
  非空（幂等，按 factor_key 去重）。

---

## 6. 测试与验收标准（Testing & acceptance criteria）

### 6.1 单元 / 集成测试（pytest 后端 + vitest 前端）

- **因果性硬验证**：对每个因子构造「在第 k 根后篡改未来数据」的对照样本，断言 `compute_factor`
  在 `≤ k` 的输出逐元素不变（杜绝未来泄漏）；评估前瞻收益右端截断断言 `count` 随 horizon 增大而
  单调不增。
- **IC 符号**：构造「因子值与未来收益正相关 / 负相关」的合成序列，断言 IC/RankIC 符号正确；空样本
  走安全口径（count==0 → 0.0/None，绝不对空集 mean）。
- **训练无泄漏**：断言时序切分点严格 = `floor(n·split)`，验证集索引全大于训练集；linear 权重对固定
  输入确定可复现。
- **缺库降级**：monkeypatch `find_spec` 返回 None，断言 `train_weights(method="xgboost")` 返回
  `available=False` 且 note 含安装提示；对应路由 400 `FACTOR_TRAINER_UNAVAILABLE`、工具 `ok=False`。
- **策略可跑通**：用一个内联因子组合建 `factor_score` 策略，跑 `run_backtest` 产出非空 equity；空
  组合时安静空仓（0 成交、不抛错）。
- **路由契约**：CRUD 往返、`enabled` 切换生效、内置因子删除 409、空区间 400 `EMPTY_RANGE`、挖掘
  超量 413 `FACTOR_MINE_TOO_LARGE`、训练器缺库 400 `FACTOR_TRAINER_UNAVAILABLE`；OKX 桩复用既有
  测试夹具（不 mock 真实签名路径）。
- **前端纯函数**：`lib/factor.ts` 的 IC 格式化 / 分位几何 / 权重条几何 / 组合校验正常 + 异常路径；
  组件测试置 `NODE_ENV=test`（既有约定）。

### 6.2 验收标准（Definition of Done，下游对照）

1. 因子层五模块（library/evaluation/mining/training/scoring）+ `factor_score` 策略 + factors
   service + `factors.py` 路由 + **7 项 Agent 工具（6 数据 + 1 ui-action）** + 因子实验室 UI 全部
   落地；前端 `npm run lint` + `npm run test` + `npm run build`（Next.js build 内含 tsc 类型检查）
   与后端 `pytest` 全绿（项目**无** `npm run check` 脚本，评审 C-P1-6）。
2. 真实 OKX 数据下：可计算/评估/挖掘因子，可训练 linear 权重并保存组合，可由组合建 `factor_score`
   策略并在**既有**回测/对比/高级回测里跑出真实结果（无假数据、无静默截断）。
3. 因子实验室可视化查看因子 IC / 分位、可编辑参数与方向、可**激活/取消**、可删除；复合得分可上图。
4. RF/XGBoost 在装了 `requirements-ml.txt` 时可选用；未装时全链路给出**显式可读**的不可用提示。
5. 既有 5+ 原子策略、combo、SimBroker、回测/对比/WF、Agent Copilot 行为**零回归**（既有测试不改即过）。

---

## 7. 风险与缓解（Risks & mitigations）

| # | 风险 | 缓解 |
|---|---|---|
| R1 | RF/XGBoost 引入重依赖、撑爆桌面 PyInstaller 包、与既有「零新增三方依赖」冲突。 | 核心仅用 numpy/pandas（linear/equal/ic_weight 恒可用）；rf/xgb 走可选 `requirements-ml.txt` + 运行时 `find_spec` 探测，缺库**显式 400 / ok=False**，绝不假数据、绝不进桌面默认包。 |
| R2 | 因子 / 训练引入未来泄漏，回测虚高（直接违背"稳健可靠高成功率"目标）。 | 全 engine 层严格因果（只用 ≤ i 数据）+ 滚动 z-score 因果窗口 + 训练时序切分不打乱 + 因果性单测护栏；前瞻收益右端截断绝不补零（沿用 signal_edge 范式）。 |
| R3 | 过拟合：训练出的权重样本内漂亮、样本外失效。 | `TrainResult` 同时回样本内/外 IC，UI 红字提示衰减；岭回归带正则；挖掘/训练标注样本充分性（occurrences<MIN_SIGNALS）。 |
| R4 | 把因子硬塞进现有策略类，破坏既有结构（需求明确反对）。 | 因子层完全独立成包；factor_score 是**新增**策略而非改造旧策略；既有策略/引擎/路由零改动（仅 `__init__`/`main.py`/`registry.py` 追加注册行）。 |
| R5 | 因子组合规格引用因子行，被删/改后组合悬空。 | `members_json` 自包含快照（不引用 factors.id），与 ComboMemberIn 同决策。 |
| R6 | 训练 / 挖掘是 CPU 密集，阻塞事件循环。 | 全部经 `run_in_threadpool` 调度（沿用 `run_backtest` 范式）；挖掘 / 训练设工作量与样本上界护栏（超限 400/413）。 |
| R7 | 因子实验室宽 UI 塞进 380px 右栏，体验差。 | 采用全屏模态（镜像 SettingsPanel），右栏只保留得分上图与 factor_score 策略入口，宽工作交给模态。 |
| R8 | 新 persist 字段导致旧 localStorage 迁移异常。 | persist v16→17 + 显式 migrate 补默认（`factorLabSection`/`factorOverlay`），瞬态 `showFactorLab` 不入 partialize（沿用 showSettings 范式）。 |
| R9 | 数值不稳定（除零 / 全 NaN 列 / 常数因子 std=0）。 | z-score/IC 对 std==0 走 NaN 安全口径并在 coverage/note 标注；矩阵构建 dropna 后样本不足显式报错而非静默。 |
| R10 | Agent 因子工具增多撑大上下文 / 误用。 | 工具 summary 精简（细节入 payload 不回喂）；系统提示加因子 primer + 推理脚手架（镜像 signal_edge）；启用态 DB 可控。 |

---

## 8. 与全局目标的对齐

全局目标：「确保因子相关功能完善、稳健、可靠、高成功率」。本方案以**严格因果 + 样本内外验证 +
显式错误（不假数据）+ 零回归既有管线**四条硬约束直接服务该目标：因果性与时序切分保证「稳健」，
样本外 IC 与样本充分性标注保证「可靠」，可插拔训练器 + 能力探测降级保证在任意依赖环境下都「高成功率
地可用」，独立分层保证不破坏既有已验证能力。

---

## 9. 实现顺序建议（供 Subtask #2）

1. engine/factors（library → evaluation → scoring → training → mining）+ 单测（纯计算，最易先验证）。
2. strategies/factor_score_strategy + 注册 + seed + 回测跑通单测。
3. db models + repositories + schemas + factors service + api/factors.py + 路由挂载 + 路由测试。
4. agent/tools/factor_tools + registry + prompts + 工具测试。
5. 前端 lib（types/api/factor/factorSeries）+ 单测 → 组件（FactorLab 及子面板）→ store/page/header 接线。
6. 全量后端 `pytest` + 前端 `npm run lint` / `npm run test` / `npm run build` 回归（无 `npm run check`）。

> 严格按本「文件 / 模块变更计划」逐项落码，不做超出方案的额外重构；实施中发现的方案缺陷回填 §11。

---

## 10. 评审记录（由 Subtask #1 填写）

Subtask #1 已完成评审：完整的 P0/P1/P2 问题清单与就地修复说明见**文档顶部「§0 评审记录（Review
Notes）」**（按任务约定置于顶部以便首屏可见）；评审结论见**文末「评审结论（Review Verdict）」**。
版本已升至 **v2**，全部 P0/P1 已在正文就地修复。

## 11. 实施过程发现的方案缺陷（由 Subtask #2 填写）

开发逐文件落地 §3 变更计划，全部门禁通过（后端 `pytest` 461 passed / 2 deselected 零回归；前端
`tsc --noEmit` 干净、`vitest` 395 passed、`next build` 成功）。落地中发现并处置如下偏差：

**D-1（§2 正文 ↔ §3.2 文件计划不一致：因子 / 得分上图载体）。** §2 正文称因子 / 得分序列「复用
`IndicatorPane`/`plots.ts`/`chartSync` 范式**叠加在 K 线下方**」，且 `show_factor` 驱动「同一叠加」；但
§3.2 文件计划仅列 `FactorScoreChart.tsx`（新建），**未列**任何 `CandleChart.tsx`/`IndicatorPane.tsx`
修改。即「主图下方叠加 pane」无对应文件落点。**决策**：按文件计划（权威「逐文件实现」清单）落地——
`FactorScoreChart.tsx` 实现为**因子实验室内自包含振荡 pane**（消费 `/factors/compute` 与
`/factor-sets/{id}/score`），`show_factor` ui-action **打开并聚焦因子实验室**（置 `factorOverlay` 瞬态 +
`factorLabSection`），而非把因子序列注入主图热路径。**理由**：主图 `IndicatorPane`/`chartSync` 由策略
回测 / 实时指标输出喂数并与主时间轴强耦合，把临时 `factor-compute` 序列接进该热路径属超出 §3.2 列出
范围的更大、更脆改动（lightweight-charts pane 生命周期 + 实时数据流）。可视化需求（查看 / 评估 / 得分
曲线）已在实验室内满足。**可回退条件**：若后续要真在主图下方叠加，仅需改 `CandleChart`/`IndicatorPane`
消费已就位的 `factorOverlay` store 字段 + 一个 factor-compute query，无需改动本层其余文件。

**D-2（`FactorScoreChart` 用内联 SVG 而非 lightweight-charts pane）。** §3.2 注「复用 IndicatorPane/
chartSync」。实测实验室为独立全屏面板、有自身布局且无需与主图时间轴同步，遂以轻量内联 SVG 振荡器
（`lib/factorSeries.ts` → `sparkline`）渲染，**视觉产出一致、耦合更低、不触碰 `chartSync`**。

**D-3（`show_factor` ui-action 前端半侧文件未在 §3.2 显式列出，按需补齐）。** §3.1 仅在后端侧列了
`factor_tools.py`（含 `ShowFactorTool`）；要让该 ui-action 端到端可用，必须补前端三处：`lib/agentTypes.ts`
（新增 `ShowFactorAction` interface + 拓宽 `ui_action` 事件联合）、`lib/agentToolMeta.ts`（7 个因子工具
标签 + `factor` 分类，配合 C-P2-6 的 `groupToolsByCategory` 数据驱动分组）、`components/Agent/AgentPanel.tsx`
（`ui_action` 处理按 `action==="show_factor"` 分派）。均为既有 `show_chart` ui-action 的同构镜像，属计划
隐含必要项，不扩张范围。

**D-4（`factorApi` `metricsJson` 精确类型）。** `sets.create/update` 的 `metricsJson` 由
`Record<string,unknown>|null` 收紧为 `TrainMetrics|null`——无索引签名的 interface 不可赋给
`Record<string,unknown>`（`tsc` TS2322）；精确类型与调用方 `trained.metrics` 及后端落库形状一致。

**D-5（`npm run lint` 在本仓不可用，类型门禁以 `tsc --noEmit` 替代）。** 本仓 `apps/web` 未装 ESLint、
无 eslint 配置，`next lint` 触发交互式初始化、**非可用门禁**。改以 `npx tsc --noEmit`（亦为 `next build`
内置的类型校验）作为类型门禁；`vitest run` + `next build` 仍为行为 / 构建门禁。C-P1-6 列出的门禁命令中，
`lint` 一项在本仓现状下不可执行，特此记录。

**D-6（等权权重和容差）。** 权重统一保留 6 位小数，等权情形下 ∑|w| 可为 1±1e-6；相应单测容差放宽至
1e-3（语义不变，仅吸收四舍五入误差）。

以上偏差均不触碰放行条件 1–6 的任一硬约束：归一化契约（训练 / 打分共用 `causal_zscore`）、因果性护栏、
内联快照自包含、错误码契约、按 category 分组、零回归均已按 §1 / §10 落地并被测试覆盖。

---

## 评审结论（Review Verdict，由 Subtask #1 填写）

**结论：有条件通过（Approved with conditions）。**

本方案架构方向正确——以**独立 Factor Layer** 解耦「特征 ↔ 策略」，严格遵守需求「不把因子硬塞进现有
策略类」，并精准复用项目两条成熟主干（`signal_edge` 事件研究范式、combo 自包含规格 + `_prepare_strategy`
回测范式）。经**逐条核验代码库**，方案声称复用的既有符号与范式绝大多数属实，技术上**无阻断性风险**：
核心因子能力仅依赖 numpy/pandas，RF/XGBoost 经可选依赖 + 运行时探测降级，与「零新增强制依赖」约束相容。

评审共发现 **1 个 P0、7 个 P1、8 个 P2**。**全部 P0 与 P1 已在本 v2 正文就地修复**（详见 §0 表格的
「处置」列）：归一化契约统一（C-P0-1）、auto 方向前视泄漏封堵（C-P1-1）、factorSetId 创建期内联快照
（C-P1-2）、挖掘超量 413 统一（C-P1-3）、得分阈值基准与 squash 默认（C-P1-4）、前端 `StrategyKind`
枚举与 `StrategyDetail` 分支补齐（C-P1-5）、`npm run check` 改为真实门禁命令（C-P1-6）、header 入口
按钮路径更正（C-P1-7）。文档据此升至 **v2**，**无遗留 P0/P1**。

**放行条件（Subtask #2 开发时必须遵守，落地后回填 §11）**：

1. **归一化契约是硬约束**：训练端 `build_factor_matrix` 与打分端 `compose_score` 必须共用同一因果滚动
   z-score（同 `normWindow`、同 clip），并配「训练权重在打分变换下复合 IC 与训练复合 IC 量级一致」的
   回归测试守护（防 C-P0-1 回潮）。
2. **因果性单测护栏**：对每个因子做「篡改未来数据，断言 ≤k 输出不变」；对训练做「时序切分点 =
   `floor(n·split)`、验证集索引全大于训练集」；对 `compose_score` 断言不接受 `auto` 方向。
3. **内联快照自包含**：`factor_score` 策略一律以 `{members,thresholds}` 内联存 `params_json`；
   `_prepare_strategy` 不得新增 factorSetId 解析逻辑；删除某 `factor_set` 不影响已创建策略。
4. **错误契约一致**：挖掘超量 413 `FACTOR_MINE_TOO_LARGE`、训练器缺库 400 `FACTOR_TRAINER_UNAVAILABLE`、
   空区间 400 `EMPTY_RANGE`、内置因子删除 409，均须有路由测试覆盖。
5. **建议项（P2）落地**：C-P2-2/5/6/7（occurrences 语义、`px_vwap_dist` 非日内全 NaN 跳过、Agent 工具
   管理按 category 分组、IC 分段 K≥6 否则不出 t）开发时按 §0 处置执行；C-P2-8 实时护栏继承须在策略接入
   实时链路时验证「无新增旁路」。
6. **零回归**：既有 5+ 原子策略 / combo / SimBroker / 回测 / 对比 / WF / Agent Copilot 既有测试**不改
   即过**；新代码遵守 `CLAUDE.md` 阈值（单文件 ≤800 行、单方法 ≤50 行、参数 ≤5、圈复杂度 ≤10、严格
   因果与显式错误传播）。

满足上述条件即视为达成本方案的「完善、稳健、可靠、高成功率」全局目标，可进入 Subtask #2 开发。
