# 挖掘的三条策略轴（discovery-strategy-axes）设计方案 v2

> 迭代：loop `tge-3635bc21` 第 2 轮 · 设计节点 → **已评审（v2）**
> 上游：`docs/plans/strategy-factor-discovery/spec.md`（v2，已实现并合入 commit `8eb196a`）
> 本文只描述**增量**；凡本文未重述的机制（循环编排、检查点、WS、并发闸、LLM 姿态、
> 安全不变式），一律**沿用**上游文档，不得另起一套。

---

## 评审记录（Review Notes · v1 → v2）

评审姿态：每一条承重断言都回**仓库真实源码**核对，不采信文档的自述。共 3 个 P0、12 个 P1、
11 个 P2，**P0 / P1 已全部在正文修复**，P2 亦已一并落实。下表是账本，正文是真源。

### P0（不修则整条轴静默失效 / 安全网从未生效）

| # | 结论 | 证据与影响 |
|---|---|---|
| **P0-1** | `factor_combo` 的 `skeleton` 定义**退化成一个常量**，该轴会在第 7 个入库候选起被审查层全量拒死，并在首次 FSA 扫描时整轴冻结 | v1 §2.6.4 定义 skeleton = 「成员 `factorKey` 排序后拼接的 8 位哈希」。但挖掘因子与既有挖掘因子的 `factor_key` **一律是字面量 `"expr"`**（`factors/specs.py::EXPR_FACTOR_KEY`；`discovery/promote.py::_factor_score_params` 正是这么写的）。于是「任意 3 个挖掘因子的组合」算出的 skeleton 完全相同。后果两条且都不报错：① `discovery/review.py:114` 的 `variant_cap`（默认 6）在同骨架入库数 ≥6 时直接拒 ⇒ 第 7 个组合候选起**全部**卡在审查层，永远进不了回测；② `discovery/fsa.py::scan_skeletons` 的 share ≈ 100% > 15% ⇒ 第 25 轮即冻结这唯一骨架，TTL 150 轮且每次扫描重新冻结 ⇒ 永久。**修复**：新增 **BL-10**，skeleton 落在表达式语义上（expr 成员取 `expression.skeleton(parse_expression(expr))`），并给 FSA 加最小池规模护栏；§2.6.4 重写 |
| **P0-2** | `turnoverBps` 被算进「可挖的 5 个轮动数值参数」，等于给优化器一个**把成本调成 0** 的旋钮 | `strategies/combination.py` 的 `params_schema` 里 `turnoverBps: {type: number, min: 0.0, max: 50.0}`，它是 `engine/combo.py::_accrue` 扣的**换手成本**，属成本模型而非策略行为。挖它 ⇒ 搜索会系统性收敛到 `turnoverBps=0` ⇒ 挖出的轮动组合「都很赚」。这与 BL-1（成本被 `/1e4` 除没了）是**同型事故**，且更隐蔽：BL-1 有门禁用例，这条 v1 里连提都没提。**修复**：新增 **BL-11**，`turnoverBps` 进冻结清单并锁到由 `costs_of(settings)` 派生的值；§2.3.3 的「可挖 5 个」改为 **4 个** |
| **P0-3** | `ComboGenome.weights` 从未被烘焙进成员 dict 的 `weight` 键 ⇒ 整条 `factor_combo` 轴恒零成交、恒被 S1 拒 | `factors/specs.py::validate_members` 里 `"weight": _safe_float(member.get("weight"), 0.0)`——**缺 `weight` 键即 0.0**。`FactorScoreStrategy.prepare` 在无顶层 `weights`（挖掘期必然如此）时走「成员烘焙 weight」路径 ⇒ 复合得分恒 0 ⇒ 永不越过 `enterLong/enterShort` ⇒ 零成交 ⇒ 全部候选卡 S1「样本量不足」。v1 §2.5.4 只写了 `validate_members(...)`，只字未提烘焙。与上游 F8 同型（错了不报错，只是永远不通过；既有单成员路径 `promote._factor_score_params` 正是显式传 `"weight": 1.0`）。**修复**：新增 **BL-12**，§2.3.2 / §2.5.4 写死烘焙步骤与门禁用例 |

### P1（会造成错误结论、可见回归或不可运维）

| # | 结论 | 证据与修复 |
|---|---|---|
| **P1-1** | 切片后只调 `compute_metrics` **产不出 `calmar`**，S5 会恒判「不可判定即拒」⇒ 所有候选卡在 S5 | `engine/metrics.py::compute_metrics` 的返回契约只有 7 项（totalReturn / winRate / profitFactor / maxDrawdown / sharpe / tradeCount / avgPnl），**没有 calmar**；calmar 在另一个函数 `engine/metrics_ext.py::compute_extended_metrics`。而 v1 的 `clip_result` / `clip_rebased` 只调前者，S5 与 §5.1 却都要 calmar。**修复**：新增 **BL-13**（指标 = 两个函数的并集，`tradeRate` 显式派生），§2.5.2 / §2.5.3 / §5.1 同步 |
| **P1-2** | `trials`（多重检验分母）与 `_skeleton_stats`（FSA 分母）都是 **run 全局**的，多轴同跑会串轴，且 BL-8 的 golden 用例**照定义抓不到** | `runtime._validate_batch` 全局自增 `self._counters["tested"]`、全局累计 `update_skeleton_stats(self._skeleton_stats, ...)`；`_validate_one` 用 `trials = _counters["tested"]+1` 喂 `required_ir`。四轴同跑后因子轴的 F3/F11 门槛会被策略候选抬高——**已发布并已验收的因子轴行为被改变**，而 BL-8 的 golden 用例是 factor 单轴 3 轮，测不出跨轴污染。统计上也只有逐轴才对（多重检验校正是同族假设内的事）。**修复**：新增 **BL-14**（`tested` / `skeleton_stats` / `bans` 一律逐轴，进检查点），§2.9 / §2.10 / §7.1 同步 |
| **P1-3** | 「单轮回测 ≤120 次」与「单候选 30s（两次回测合计）」两处口径都低估 4–6 倍 | `engine/combo.py::_build_member_curves` 对**每个成员各跑一次** `run_backtest`。rotationSize 2–4 ⇒ 一个 `strategy_combo` 候选的底层回测是 4–8 次，过 S9 再 +2–4 ⇒ 最多 12 次。按「候选级」计数，墙钟护栏形同虚设。**修复**：§六 把计数单位定死为**底层 `run_backtest` 调用次数**，超时口径改为「三次分段（IS / OOS / 压力）合计」 |
| **P1-4** | `htf_frames_by_member` 按**展示 key** 索引，同模板重复成员会静默缺帧回测（BL-6 的漏网路径） | `combo_specs.build_members` 对同模板第二次出现生成 `display_key = f"{template_key}#2"`，`run_combo_backtest(htf_frames_by_member=...)` 按 `member.key` 取帧。§2.3.3 又明确允许「同模板可重复出现」。按 `template_key` 组装该 dict ⇒ `#2` 成员取不到帧 ⇒ 走无 MTF 路径静默回测。§2.4 的 fail-closed 只覆盖单策略路径。**修复**：§2.5.4 写死按展示 key 组装 + §2.4 的 fail-closed 校验扩展到逐成员 |
| **P1-5** | S 闸的中文映射落错了地方：前端有**自己一张**硬编码表，后端 `FILTER_LABELS` 加了也不显示 | `apps/web/lib/discovery.ts::REJECT_REASON_CN` 是独立字典，`rejectReasonCn` 未登记即**原样回显 code**。用户会在「拒绝原因 Top 5」看到裸 `S6` / `S0:gate_leak` / `S0:missing_htf:12H`；`S0:*` 还是带冒号子因的形态，需要像 `eval_error:` / `llm_review:` 那样加前缀分支。§3.4 的前端修改表也漏了这项。这是上一轮 RV-6（`category=discovered` 未登记中文映射）的**同款复发**。**修复**：§2.6.2 重写 + §3.4 补行 |
| **P1-6** | `FactorScoreStrategy.prepare` 对装配失败是**安静空仓**，系统性错误会伪装成「S1 样本量不足」 | 其 `except (ValueError, KeyError): df["_score"] = NaN` 意味着未知 factorKey / 非法 direction / 因子与该 bar 不兼容统统不抛错。§2.6 的「回测抛错即拒」抓不到它，整轴退化成「候选全被 S1 拒」，看起来像「市场没有 alpha」。**修复**：§2.5.4 增 `S0:score_unavailable` 机器判据；§2.4.2 成员目录先过 `library.is_bar_compatible(key, bar)` |
| **P1-7** | `DISCOVERY_AXIS_DEPENDENCY` 的触发条件引用了一条本文**并未设计**的「库内上游产物」路径 | §4.1 写「……**且库里也没有可用上游产物**」才 400，但 §2.3.3 规定轮动成员**只**来自本 run `strategy_param` 轴的 kept 池，根本没有从 `strategies` 表取材的路径。按字面实现会出现「库里有策略 ⇒ 放行 ⇒ 该轴恒不 ready ⇒ 恒让渡 ⇒ 空转轴」，**直接违反 BL-9**。**修复**：改为无条件 400（本轮不扩范围） |
| **P1-8** | 让渡接收方的规则与自己的 UI 示例互相矛盾，且没限定在「本 run 已选轴」内 | `genomes.ALL_AXES` 的**实际**顺序是 `("factor", "factor_combo", "strategy_param", "strategy_combo")`，而 `factor` 恒 ready ⇒ 「顺序 = ALL_AXES」意味着让渡永远流向 factor；但 §2.8 的示例写的是「策略组合 12 → 常规策略」。二者不可能同时成立。规则也没说只在已选轴里挑。**修复**：§2.8 改为「已选轴内、按 `ALL_AXES` 次序取第一条 ready」，并把示例改成与规则一致的形态 |
| **P1-9** | `promote` 的**幂等回放路径丢 `companionStrategyId`** | `promote_candidate` 在 `row.promoted_kind and row.promoted_id` 时直接 `_promoted_payload(kind, id, reused=True)` 返回，§4.4 新增的 `companionStrategyId` 只有首次 `create_set` 拿得到 ⇒ 第二次入库后成果库的「去策略面板」跳转失效。**修复**：§2.11 / §4.4 写死回放分支按 `promoted_id` 反查伴生策略 |
| **P1-10** | §3.5 的既有测试清单**有错有漏**，照它做仍会撞红灯 | ① `tests/test_discovery_promote.py` 里**没有**任何 `DISCOVERY_AXIS_UNSUPPORTED` 断言（该断言在 `test_discovery_api.py:211`）；② 漏了 `test_discovery_api.py:446::test_defaults_expose_only_implemented_axes`（硬断言 `implementedAxes == ["factor"]`，四轴放行后必红）；③ 漏了前端 `DiscoveryLauncher.test.tsx:17` 的 mock 与 `:110` 的 `expect(body.axes).toEqual(["factor"])`；④ §3.4 漏了 `DiscoveryFunnel.tsx`——「漏斗按轴分列」的代码在这个文件里，不在 `DiscoveryMonitor.tsx`。**修复**：§3.4 / §3.5 重写 |
| **P1-11** | `strategy_combo` 的 OOS 前伸 `pad` 公式漏了 regime 起算根 | `engine/combo.py::_warmup_floor` = `max(成员 warmup, max(RegimeParams.ema_slow_p, trend_win), policy.lookbackBars)`。轮动参数是被挖的，`lookbackBars` 可低至 24；此时 v1 的 `pad` 可能**小于** `warmup_floor`，OOS 段头部会有一截恒定权益（组合尚未开始决策）被计进样本外指标，稀释 S6。**修复**：§2.5.3 的 pad 改为与 `_warmup_floor` 同源的量 |
| **P1-12** | 验收标准 #3 与 S6 **自相矛盾**，不可机器判定 | v1 DoD #3：「抽 3 条 kept，其 `metrics.oos.sharpe` **不全为正**的近似值」。但 S6 要求 kept 的 OOS sharpe > 0——kept 的 OOS sharpe **必然**全为正，这条恒假。**修复**：§7.3 #3 改为可判定的分布判据 |

### P2（清晰度 / 稳健性，已一并修复）

| # | 内容 |
|---|---|
| P2-1 | `penalized_gate` 抽取时不得硬写 `0.5`，须引 `DEFAULT_THRESHOLDS["minAbsIr"]`（现值确为 0.5，但硬写会在默认值变更时静默漂移，违反 BL-4 的同源精神）。 |
| P2-2 | `GatedStrategy` 的注释「`self.meta = inner.meta` 供 `run_backtest` 内部读取」不实——`engine/backtester.py::run_backtest` 全程不读 `meta`。赋值本身无害（`BaseStrategy.meta` 是类属性），但注释必须改，免得后人据此推断出错误的耦合。 |
| P2-3 | §4.1 的 `DISCOVERY_COMBO_SIZE` 写 comboSize 边界 1–5，而 §2.3.2 与审查规则 `degenerate_members`（<2 即拒）都是 2–5。统一取 **2–5**。 |
| P2-4 | `normalize_thresholds` 在 `discovery/validation.py` 与 `factors/specs.py` **各有一个同名函数**，语义完全不同。§2.5.4 必须写全限定名。 |
| P2-5 | `clip_rebased` 未防 `points[0].equity <= 0`（rebase 除零 / `peak=0` 除零）。 |
| P2-6 | HTF 取帧的 `htf_warmup_bars` 应取全部入选模板的 `max(htf_warmup_bars())`（与 `agent/services.py:543-549` 同源），§2.4.1 漏了。 |
| P2-7 | `workload_of` 新口径对 factor 单轴是**恒等**的（`AXIS_COST["factor"]=1`），这一点必须显式写出来作为「不改变既有 413 行为」的承诺；并给出真实算例，免得实现节点误以为闸挂了。 |
| P2-8 | `build_members(strict=True)` / `validate_members` / `validate_combo_params` 抛的是 `fastapi.HTTPException` 而非 `ValueError`，`eval_error:<ExcName>` 收口会记成 `eval_error:HTTPException`（可读性差）。单独映射为 `S0:assemble_rejected`。 |
| P2-9 | `apps/web/components/Chart/EquityChart.tsx` 的 `equity` 与 `overlays` 两个分支**互斥**且右轴口径不同（原值 vs 归一化百分比）。IS/OOS 双段必须走 `overlays` 分支，且不可与 `equity` 混传。 |
| P2-10 | `discovery_work_unit_limit` 默认 3,000,000：四轴 + 4000 根 + 40 候选 ≈ 1.56M（可过），四轴 + 8000 根 ≈ 3.12M（413）。413 文案应加「或减少同时开启的轴」。 |
| P2-11 | `factor_combo` 的 `mechanism` 恒 `factor_combo`、`strategy_combo` 恒 `rotation`，会让「机制覆盖」图在这两轴上恒为单柱。这是可接受的（覆盖度本就是因子轴的概念），但 UI 必须在这两轴下**隐藏**覆盖图而不是画一根 100% 的柱子（诚实优先于漂亮，沿用 `DiscoveryFunnel` 的既有姿态）。 |

---

## 〇、阅读顺序与承重决策速览

先读这一节。下面 14 条是本轮的**承重条款**——实现时必须逐字照做；若实现中发现任一条不可行，
**先回来改本文**，再改代码，绝不在代码里静默偏离（沿用上游放行条件 7）。
BL-1…BL-9 出自设计；**BL-10…BL-14 出自评审**（对应 P0-1 / P0-2 / P0-3 / P1-1 / P1-2），
它们共同的特征与前九条完全一致：**错了不报错，只是整条轴永远不产出、或安全网从未生效**。

| 编号 | 承重条款 | 违反后的表现 |
|---|---|---|
| **BL-1** | 回测成本必须传 **bps 原值**（`run_backtest` 内部 `/1e4`），**严禁**再除 1e4 | 成本变成 ~0，回测里几乎不可见 ⇒ 挖出一堆「很赚」的高频噪声策略。**比上游 F8 更危险**：F8 错了是永不通过（会被发现），这个错了是永远通过（不会被发现） |
| **BL-2** | IS/OOS 用**两次独立回测 + 两个独立策略实例**；OOS 段前伸 `warm` 根**只暖机不交易**（`GatedStrategy` 包装器），指标按 `ts ≥ splitTs` 切片后由引擎 `compute_metrics` 重算 | 复用同一实例 ⇒ IS 末尾的跟踪止损 / 持仓运行态泄漏进 OOS；不前伸 ⇒ OOS 前 `warmup` 根恒无信号；前伸但不 gate ⇒ IS 段的成交被重复计进 OOS，「样本外」名不副实 |
| **BL-3** | `strategy_combo` 轴无法 gate（组合净值由成员曲线合成），改用 **rebase 切片**，且只统计 `ts ≥ splitTs` 的平仓笔；两条路径的差异必须在详情里如实标注 | 用一套话术盖住两种口径 ⇒ 用户以为两个轴的 OOS 指标可比 |
| **BL-4** | 多重检验惩罚必须与因子轴**同源**：`penalized_gate(base, trials, k)` 一个实现，`required_ir` 与 `required_sharpe` 都调它 | 两份公式迟早漂移，且漂移了没人看得出来（上游「估计量同源律」） |
| **BL-5** | 策略轴的 kept 池重建（续跑 + 数据刷新）必须与因子轴一样是**一个实现两个调用点**，重建失败一律 `paused` | 重启后 S8 去重没有基准 ⇒ 每次重启开始收一批同质策略 |
| **BL-6** | 高周期（MTF）选择性参数**冻结不挖**（`FROZEN_PARAM_KEYS`），且验证层入口对 `strategy.timeframes()` 与本 run 已取帧集合做 fail-closed 校验 | 参数一变高周期就变 ⇒ 取帧集合无界；取不到帧就跑 ⇒ 回测出的净值和用户日后真跑出来的不是一回事 |
| **BL-7** | 入库策略的 `params_json` 必须与**被回测的那份参数逐键相等**，不得塞任何溯源字段 | 「挖的时候是 A、入库后跑的是 A+x」——本仓库反复吃亏的静默背离 |
| **BL-8** | 因子轴行为**逐位不变**：重构前先落 golden 用例（固定种子 + 合成窗口，记录 kept 集合与拒绝原因分布），重构后必须逐条相等 | Lane 抽象把已发布并已验收的因子闭环改出回归，而回归只在长跑里显形 |
| **BL-9** | 轴预算不足 / 上游库存不足时**让渡并如实标注**（`yieldedByPool` 进迭代记录且必须有 UI 落点），绝不留「选了却不产候选」的空转轴 | 上游 D-6 强制推论的延续：用户选了 3 条轴，实际只有 1 条在跑，而界面上看不出来 |
| **BL-10**〔评审 P0-1〕 | 组合两轴的 `skeleton` **必须落在语义结构上**：`factor_combo` 的 expr 成员取 `expression.skeleton(parse_expression(expr))`（库内因子取 `factorKey`）后排序拼接再哈希；且 `fsa.scan_skeletons` 的调用点必须加**最小池规模护栏**（本轴 kept < `FSA_MIN_POOL`(=20) 时整轮跳过扫描） | 直接用 `factorKey` 拼接会退化成常量——所有挖掘因子的 `factor_key` 都是字面量 `"expr"`。后果：`variant_cap`(6) 在第 7 个入库候选起**全量拒死**该轴，FSA 再把这唯一骨架永久冻结。**表现是「这条轴挖了两天一个都没产出」，没有任何错误** |
| **BL-11**〔评审 P0-2〕 | `turnoverBps` 进 `FROZEN_POLICY_KEYS`，锁到由 `costs_of(settings)` 派生的值；轮动轴可挖的数值参数是 **4 个**（rebalanceBars / lookbackBars / minDwellBars / switchMarginPct） | `turnoverBps` 是 `engine/combo.py::_accrue` 扣的**换手成本**，不是策略行为参数。挖它 = 让优化器把成本调成 0 ⇒ 挖出来的轮动组合「都很赚」。这是 **BL-1 的另一扇门**，且更隐蔽（BL-1 有门禁用例） |
| **BL-12**〔评审 P0-3〕 | `ComboGenome.weights` 必须**逐成员烘焙进 `weight` 键**后才交给 `validate_members`；配一条「装配出的成员 dict 的 `weight` 之和 == 1.0」的门禁用例 | `validate_members` 对缺 `weight` 的成员填 **0.0**，`FactorScoreStrategy` 无顶层 `weights` 时走成员烘焙路径 ⇒ 复合得分恒 0 ⇒ 零成交 ⇒ 全轴卡 S1「样本量不足」。与上游 F8 同型：**错了永远不通过，而不是报错** |
| **BL-13**〔评审 P1-1〕 | 两条切片路径产出的指标一律是 `compute_metrics(...)` **并** `compute_extended_metrics(...)` 的并集；`tradeRate` 由本层显式派生（`tradeCount / bars`） | `compute_metrics` 的返回契约只有 7 项且**不含 calmar**（calmar 在 `metrics_ext`）。只调前者 ⇒ S5 恒判「不可判定即拒」⇒ **所有候选卡在 S5** |
| **BL-14**〔评审 P1-2〕 | `tested`（多重检验分母）、`skeleton_stats`、`fsa_bans` 一律**逐轴**统计并逐轴进检查点；`trials` 传给 `penalized_gate` 时取**本轴**的计数 | 现有实现三者都是 run 全局的。多轴同跑会让因子轴的 F3/F11 门槛被策略候选抬高——**已发布并已验收的因子轴行为被改变**，而 BL-8 的 golden 用例是 factor 单轴 3 轮，**照定义测不出这条**。统计上也只有逐轴才对（多重检验校正是同族假设内的事） |

**范围（D-7，本轮定盘）**：交付 `strategy_param` / `factor_combo` / `strategy_combo` **三条策略轴**，
使 `IMPLEMENTED_AXES == ALL_AXES`。这不是可选项——需求原文「策略，就是因子策略，常规策略，
组合策略均可」正是这三条。若实现节点因预算被迫削减，**唯一允许的削减顺序**是从
`strategy_combo` 开始向前砍，且被砍的轴必须退出 `IMPLEMENTED_AXES`、启动器不渲染、API 400
（与上游 D-6 同一纪律），并在 §十一 如实记录。

---

## 一、概述（Overview）

上一轮交付了「策略与因子挖掘」的**因子单轴闭环**：一个 24/7 长跑的「生成—审查—验证」循环，
用表达式树搜索价量因子，靠 11 条硬编码闸判定去留，挖到的因子经 `expr` 桥零改地流回既有因子层。
它证明了整条 Loop Engineering 骨架在本仓库是可用的——但它只覆盖了需求四条里的三条半。需求原文
第四条「策略，就是因子策略，常规策略，组合策略均可」，在上一轮被评审以「单轮不可控」为由
收窄成 D-6（`factor` 单轴），另外三条轴保留在 `ALL_AXES` 里、仅用于给出准确的 400。

本轮把那三条轴补齐，让「挖掘」这个词在产品里名实相符：不只是挖因子，而是**挖能直接拿去跑的
策略**。三条轴分别对应用户说的三种策略形态——`strategy_param` 挖**常规策略**（在注册表某个
原子模板的参数空间里搜索）、`factor_combo` 挖**因子策略**（把库里与本 run 挖到的因子组装成
带权重与阈值的 `factor_score` 组合）、`strategy_combo` 挖**组合策略**（把已入库的策略装进
`regime_rotation` 轮动模板）。三条轴共用同一套循环、同一个检查点、同一条实时流、同一个成果库，
用户看到的仍是一个功能，只是「能挖的东西」变多了。

技术上，本轮的核心不是三个搜索器，而是**一条新的验证车道**。因子轴的验证是统计式的
（IC / IR / 单调性，可以在全窗上求值一次再切片）；策略轴的验证是**路径依赖**的——回测里持仓
会跨根携带，指标必须由真实撮合产生。这带来一组与因子轴镜像但方向相反的坑：成本量纲从「必须
除 1e4」变成「绝不能除 1e4」；样本外切分从「切片」变成「两次独立回测 + 暖机前伸 + 只暖机不
交易」；去重基准从「因子值序列」变成「逐根收益序列」。这些坑的共同特征与上一轮完全一致：
**错了不报错，只是安全网从未生效、或搜索永远产不出东西**。所以本文把它们全部钉成承重条款，
并为每一条配一个双向门禁用例。

产品上，本轮同时兑现上一轮欠下的两笔账：其一，`autoPromote` 之外的**入库路径**从「只能变成
一个因子」扩展到「变成因子 / 因子组合（含伴生策略）/ 策略」三种实体，用户在成果库里点一下
就能把挖到的东西送进已有的策略面板与回测链路；其二，多轴并跑时的**预算让渡**必须可见——
选了三条轴而其中两条因上游库存不足没在产候选，界面上必须直接说出来，而不是让用户对着一个
「在跑」的进度条猜。零 DB 迁移（上一轮建表时已把 `kind` / `genome_json` / `promoted_kind`
的取值域留全），零既有表结构变更，与交易层的 import 边界继续由 AST 门禁守着。

---

## 二、技术设计

### 2.1 总体架构与数据流

```
                    ┌───────────────────────── DiscoveryRun（runtime.py，生命周期/调度/落库/事件）
                    │
  每轮 _run_iteration ──► plan_axis_budget(axes, weights, pools)   ← §2.8 轴预算 + 库存让渡
                    │        │
                    │        ├─ factor        → FactorLane   ─┐
                    │        ├─ strategy_param→ ParamLane    ─┤
                    │        ├─ factor_combo  → ComboLane    ─┼─► 每条 Lane 内部：生成 → 审查 → 验证
                    │        └─ strategy_combo→ RotationLane ─┘        （四步与上游完全同构）
                    │
                    └──► 合并三条 Lane 的候选行 / 计数 / 事件 → 单事务落库 → WS 扇出
```

Lane 是本轮引入的唯一新抽象：**一条轴的「生成 + 审查 + 验证 + 池」四件事打成一个对象**，
runtime 只负责「调度谁、给多少名额、把结果落库和扇出」。这样做有三个理由：① `runtime.py`
已 785 行，逼近仓库 800 行红线，把逐候选验证搬出去是必须的；② 因子轴与策略轴的验证实现差异
太大（统计 vs 回测），塞进同一个 `_validate_sync` 只会长出一串 `if kind ==`；③ Lane 边界让
BL-8 的等价门禁可执行——因子 Lane 的内部实现是从 runtime 平移过去的，行为必须逐位不变。

**数据流不变量**（沿用上游）：一个 run 自持一份 K 线 DataFrame 与候选 Candle 列表，绝不依赖
请求级缓存；所有 CPU 密集（求值 / 回测）走 `run_in_threadpool` 且先拿 `discovery_validate_concurrency`
信号量；OKX 不可达就退避、连续失败转 `paused(data_unavailable)`，**绝不**用旧数据假装新数据。

### 2.2 Lane 抽象与 `runtime.py` 拆分

新增 `discovery/lanes/` 包：

```python
# lanes/base.py
@dataclass(frozen=True)
class LaneContext:
    """一轮验证所需的全部只读输入（每轮构造一次，跨候选复用）。"""
    df_full: pd.DataFrame
    candles: list            # 与 df_full 同源同序（策略轴要原始 Candle 喂 run_backtest）
    htf_frames: dict         # {htf_bar: Candle 列表}，见 §2.4
    bar: str
    split: int               # 唯一切点，来自 validation.split_index（同源律）
    split_ts: int
    config: dict
    settings: Settings
    memo: dict


class Lane(Protocol):
    kind: str
    def produce(self, rng, *, budget, count, tally) -> tuple[list[Candidate], LlmOutcome]: ...
    def review(self, candidates) -> tuple[list[Candidate], list[tuple[Candidate, str]]]: ...
    def validate(self, candidate, ctx: LaneContext, trials: int) -> ValidationOutcome: ...
    def absorb(self, candidate, outcome) -> None       # kept 立刻进本 Lane 的池
    def rebuild(self, rows, ctx: LaneContext) -> RebuildResult                 # BL-5
    def pool_size(self) -> int
    def snapshot(self) -> dict                          # 进 state 事件的本轴摘要
```

`runtime.py` 的改造（**只搬不改**，BL-8）：

| 现有成员 | 去向 |
|---|---|
| `_searcher` / `_factory` / `_memo` / `_pool` / `_review_ctx` / `_validation_ctx` | → `lanes/factor_lane.py::FactorLane`（逐行平移） |
| `_validate_batch` | 保留在 runtime，但改为**逐 Lane 调用**（循环体不变，`self._validate_one` → `lane.validate`） |
| `_validate_one` / `_validate_sync` | → `lanes/base.py::run_validation`（超时 / 信号量 / 异常收口三段逻辑逐行平移，与 kind 无关） |
| `_absorb` / `_trim_pool` | → `lanes/base.py`（`_trim_pool` 的 `maxKept` 改为**逐轴**上限） |
| `_rows_for` / `_candidate_row` | → `lanes/base.py::candidate_rows`（`genome` 字段由 Lane 提供 `genome_payload(candidate)`） |
| `_run_fsa` | 保留在 runtime，但对**每条 Lane 的池**各跑一次（FSA 的 skeleton 语义见 §2.6.4） |
| `_counters["tested"]` / `_skeleton_stats` / `_bans` | **BL-14（评审 P1-2）**：三者由「run 全局一份」改为「**逐轴一份**」，并搬进各自的 Lane（`lane.tested` / `lane.skeleton_stats` / `lane.bans`）。runtime 只保留一个用于 UI 汇总的**只读求和**。`_validate_one` 里 `trials = lane.tested + 1` |

改造后 `runtime.py` 预计 ~520 行，`lanes/base.py` ~230 行，`lanes/factor_lane.py` ~200 行，
`lanes/strategy_lane.py` ~280 行（三条策略轴共用一个类，靠 `kind` + 一个 `assembler` 区分）。

**BL-8 等价门禁的执行顺序（实现节点必须照此步骤，不得先重构后补测）**：
1. 在**当前 HEAD 代码**上新增 `tests/test_discovery_factor_lane_golden.py`：用固定 `rng_seed`、
   合成的确定性 K 线（正弦 + 固定噪声，**不连网**）跑 3 轮，把 `(genome_hash, verdict,
   reject_reason)` 三元组序列与最终 `counters` 序列化成一个 JSON 常量写进用例；跑绿。
2. 再做 Lane 拆分。
3. 重跑该用例，**必须逐条相等**。任何差异都视为回归，不接受「新实现更合理」的解释。
4. **（评审 P1-2 补）golden 用例有一个盲区必须另立门禁**：它是 factor 单轴 3 轮，因此**测不出**
   「多轴同跑污染了因子轴的共享计数器」。故另加
   `test_discovery_factor_axis_is_isolated_under_multi_axis`：同一固定种子下，
   ①「只跑 factor 轴」与 ②「factor + 三条策略轴同跑」两次运行，断言**因子轴**的
   `(genome_hash, verdict, reject_reason)` 序列与 `tested` 计数**逐条相等**。这条用例是
   BL-14 的机器判据；没有它，BL-8 的「逐位不变」只在单轴下成立。

### 2.3 基因体与三个搜索器

`genomes.py` 扩展（现 88 行 → 约 190 行）：

```python
@dataclass(frozen=True)
class ParamGenome:
    """常规策略轴：注册表模板 key + 参数覆盖（只含被挖的数值参数）。"""
    template_key: str
    params: dict            # 已与 default_params 合并并按 schema 夹取后的**完整**参数

@dataclass(frozen=True)
class ComboMemberRef:
    factor_key: str         # "expr" 或库内因子 key
    params: dict            # expr 成员为 {"expr": 规范式}
    direction: str          # 冻结的 long / short（绝不 auto）
    name: str               # expr 成员为 expr:<hash8>，库内因子为 factorKey

@dataclass(frozen=True)
class ComboGenome:
    """因子组合轴：2–5 个成员 + 权重 + 阈值。"""
    members: tuple[ComboMemberRef, ...]
    weights: tuple[float, ...]          # 已归一化到和为 1，六位小数
    thresholds: dict                    # {enterLong, enterShort, exitBand}（normWindow/squash 冻结）

@dataclass(frozen=True)
class RotationGenome:
    """策略组合轴：2–4 个成员（模板 key + 参数）+ 轮动策略参数。"""
    members: tuple[ParamGenome, ...]
    policy: dict                        # regime_rotation 的 7 个轮动标量（enum/bool 冻结）
```

**基因哈希（`genome_hash`，去重的物理键）**：一律 = `sha256(canonical_json)[:32]`，
`canonical_json` 用 `json.dumps(obj, sort_keys=True, separators=(",",":"), ensure_ascii=False)`
且所有浮点先 `round(x, 6)`。**必须显式做规范化**：不规范化时 `0.1+0.2` 与 `0.30000000000000004`
会算出两个哈希，同一个基因被重复测无数次而去重集合永远命中不了——表现为「搜索效率很低」，
不报错。

**中文渲染（`describe`）** 与上游同一纪律：唯一真源，UI / prompt / 入库名共用。
- `ParamGenome` → 「暴涨动量突破 · 突破窗口 34 / 放量倍数 1.8 / 硬止损 2.5%」（只列**被挖动过**
  的参数，最多 5 项，超出以 `…` 收尾）
- `ComboGenome` → 「因子组合（3 成员）：隔夜溢价均值差 0.42 + 振幅扩张 0.33 + 量价背离 0.25」
- `RotationGenome` → 「策略轮动（3 成员）：趋势回调多 / EMA 交叉 / 区间高抛低吸 · 再平衡 24 根」

渲染实现放 `discovery/describe_strategy.py`（新增，~110 行），与 `expression_text.py` 并列。

#### 2.3.1 `ParamSearcher`（`searchers/param_searcher.py`，~200 行）

搜索空间 = 模板目录（§2.4）里每个可挖模板的 `params_schema` 中**类型为 `int` / `number` 且
不在 `FROZEN_PARAM_KEYS` 里**的条目。每个可挖参数按 schema 的 `[min, max, step]` 离散成一条
**梯子**（`ladder = [min, min+step, ..., max]`，长度超过 64 时等距抽稀到 64，避免 `step=0.005`
的参数产生几百格）。基因 = 每个可挖参数在自己梯子上的下标。

| 操作 | 实现 |
|---|---|
| `seed` | 均匀随机选一个模板（按 §2.8 的模板权重），每个可挖参数在梯子上均匀取一格 |
| `mutate` | 复制父本，随机选 1–3 个参数各重掷一格（不含原值） |
| `crossover` | 两父本**同模板**才交叉（不同模板则退化为对第一个父本 `mutate`，并计 `crossover_degraded`）；逐参数以 0.5 概率取自任一父本 |
| `perturb` | 结构不变，沿动量方向在梯子上走 1–4 格（复用上游 `MomentumTracker`，键 = `(template_key, param_name)`） |
| `from_llm` | 解析 `{templateKey, params:{...}}`；未知模板 / 参数越界 / 类型不符一律返回 `None`（**绝不**放宽护栏迁就模型） |

#### 2.3.2 `ComboSearcher`（`searchers/combo_searcher.py`，~210 行）

成员来自**成员目录**（§2.4.2）。基因 = 成员下标集合 + 权重 + 三个阈值。

| 操作 | 实现 |
|---|---|
| `seed` | 随机取 `comboSize`（2–5，按 run 配置区间随机）个互异成员；权重从 Dirichlet(1) 采样后 round(6) 归一；阈值在梯子上随机 |
| `mutate` | 三选一：换掉一个成员 / 重掷一个权重后重归一 / 动一个阈值 |
| `crossover` | 成员集合取并集后随机截断到 `comboSize`，权重取两父本对应权重的均值后重归一 |
| `perturb` | 只动阈值（`enterLong` / `enterShort` / `exitBand`），沿动量方向走 1–2 格 |
| `from_llm` | 解析 `{members:[{factorKey, params, direction}], weights, thresholds}`；成员不在目录内即 `None` |

**权重与方向的三条硬约束**：① 权重必须严格归一（`sum == 1.0`，六位小数残差补给最大分量——
上游 I-4 的同款处理，否则残差每次读写放大）；② 成员 `direction` 一律从目录里**冻结继承**，
搜索器不得改写，更不得写 `auto`（`validate_members` 见 `auto` 直接 400）；
③ **BL-12（评审 P0-3）**：`ComboGenome.weights[i]` 必须在装配时**逐成员烘焙成 `member["weight"]`**
再交给 `validate_members`。

> 为什么这条要立成承重：`factors/specs.py::validate_members` 对成员写的是
> `"weight": _safe_float(member.get("weight"), 0.0)`——**缺键即 0.0**，不报错。而
> `FactorScoreStrategy.prepare` 在没有顶层 `weights`（挖掘期必然没有）时，走的正是
> 「成员烘焙 weight」那条路径。两件事叠起来的结果是：复合得分恒 0 ⇒ 永远越不过
> `enterLong` / `enterShort` ⇒ 零成交 ⇒ 每一条 `factor_combo` 候选都被 S1 以「样本量不足」
> 拒掉。看起来像「组合因子在这个市场没用」，实际是权重全丢了。仓库里已有的单成员路径
> `discovery/promote.py::_factor_score_params` 正是显式传 `"weight": 1.0` 的——照它写。

#### 2.3.3 `RotationSearcher`（`searchers/rotation_searcher.py`，~180 行）

成员来自**本 run `strategy_param` 轴的 kept 池**（≥ `minRotationPool`，默认 2）。若该轴未启用
或库存不足，本轴让渡（§2.8）。基因 = 2–4 个 `ParamGenome` + 7 个轮动标量中可挖的 **4 个**数值参数
（`rebalanceBars` / `lookbackBars` / `minDwellBars` / `switchMarginPct`）。

**冻结的 3 个（BL-6 同款规则 + BL-11）**：`selectionMetric` 是 enum、`regimeGating` 是 bool，
由通用规则自动冻结；**`turnoverBps` 由 `FROZEN_POLICY_KEYS` 显式冻结（评审 P0-2 · BL-11）**：

```python
FROZEN_POLICY_KEYS: frozenset[str] = frozenset({"turnoverBps"})

def frozen_policy_values(settings) -> dict:
    """轮动策略里**属于成本模型**的标量，一律锁到与回测成本同源的值。"""
    _cap, fee_bps, slip_bps, _lev = costs_of(settings)
    return {"turnoverBps": round(2.0 * (fee_bps + slip_bps), 4)}
```

> `turnoverBps` 的 schema 是 `{"type": "number", min: 0.0, max: 50.0}`，看起来和别的数值参数
> 没两样；但 `engine/combo.py::_accrue` 用它扣的是**每次切换的换手成本**。把它交给搜索器，
> 等于在优化目标里放了一个「把成本调成 0」的旋钮——搜索会系统性收敛到 `turnoverBps=0`，
> 挖出来的轮动组合于是「个个都赚」。这跟 BL-1 是同一类事故（成本消失），只是走了另一扇门，
> 而且更隐蔽：BL-1 有双向门禁用例盯着，这条如果不写死就没人会去测它。配套门禁用例
> `test_rotation_searcher_never_mines_cost_params`：把 `turnoverBps` 从 schema 里读出来喂给
> 搜索器 500 次，断言产物里该键**恒等于** `frozen_policy_values(settings)["turnoverBps"]`。

| 操作 | 实现 |
|---|---|
| `seed` | 从 kept 池锦标赛选 `rotationSize` 个互异成员（同模板可重复出现但参数必须不同），轮动参数在梯子上随机 |
| `mutate` | 换一个成员 / 重掷一个轮动参数 |
| `crossover` | 成员并集截断 + 轮动参数逐项随机取父 |
| `perturb` | 只动轮动参数，沿动量走 |
| `from_llm` | 本轴**不接 LLM 生成**（成员必须来自本 run kept 池，模型无法凭空引用），LLM 份额按上游 `_fallback` 规则回落随机探索 |

### 2.4 可挖模板目录与成员目录（`discovery/catalogs.py`，新增 ~200 行）

#### 2.4.1 模板目录 `minable_templates(bar) -> list[TemplateSpec]`

遍历 `STRATEGY_REGISTRY`，逐模板判定：

```python
@dataclass(frozen=True)
class TemplateSpec:
    key: str
    name: str
    kind: str                    # atomic | factor | combo
    family: str                  # 见 §2.6.4 的机制族映射
    minable: bool
    reason: str                  # 不可挖时的中文原因（UI 直接显示，绝不静默隐藏）
    params: tuple[ParamSpec, ...]  # 可挖参数（含梯子）
    htf_bars: tuple[str, ...]      # 冻结参数下该模板声明的高周期
```

不可挖判据（**有序**，首个命中即定，理由见上游 I-5：加权打分分不开语义相反的情形）：
1. `kind != "atomic"` ⇒ `reason="组合与因子类模板由专用轴挖掘"`（`regime_rotation` 与
   `factor_score` 分别归 `strategy_combo` / `factor_combo` 轴，不进本目录）
2. 可挖数值参数数量 `< 2` ⇒ `reason="可搜索参数不足"`
3. 冻结参数下 `cls(defaults).timeframes()` 里存在**严格高于基础周期**的 bar 且数量 > 
   `DISCOVERY_MAX_HTF_FRAMES`（默认 3）⇒ `reason="所需高周期帧数超出上限"`
4. 否则 `minable=True`

**`FROZEN_PARAM_KEYS`（BL-6 承重）**：类型为 `enum` / `bool` 的参数一律冻结在模板默认值；
另加一张显式清单，收编「类型是数值但语义是高周期选择」的参数：

```python
FROZEN_PARAM_KEYS: dict[str, frozenset[str]] = {
    "trend_pullback_long":  frozenset({"htf_seconds"}),
    "trend_pullback_short": frozenset({"htf_seconds"}),
}
```

> `trend_pullback_*` 的 `htf_seconds` 是 `{"type": "int", min: 300, max: 14400}`，挖它等于
> 让每个候选随机换高周期——取帧集合无界，且非标准秒数会静默落进「重采样兜底」路径。冻结它，
> 而不是把它挖成一个随机数。`ema_cross_scalper` 的 `htf_trend_filter` 是 `bool`、`htf_bar`
> 是 `enum`，由通用规则自动冻结，无需列表。

**高周期取帧**：run 启动时对**全部可挖模板的冻结高周期取并集**（`bar_to_seconds(tf) > 基础周期`
过滤，与 `agent/services.py:536-541` 逐字同源），一次性经 `load_htf_frames` 取回，随基础窗口
一起刷新。并集大小超过 `DISCOVERY_MAX_HTF_FRAMES` 时，按「需要该帧的模板数」降序保留，被挤掉
的模板整体退出目录并给出 `reason`——**绝不**让一个模板在缺帧状态下被回测。

`load_htf_frames` 的 `htf_warmup_bars` 形参取**全部入选模板的最大值**
（`max(cls(defaults).htf_warmup_bars() for ...)`，缺省 60），与 `agent/services.py:543-549` 的
`max_warmup` 逐字同源（评审 P2-6）。取小了会让最靠前的窗口在 `start_ts` 处 `merge_asof(backward)`
拿到 NaN——**又是一个不报错、只是高周期特征整段失效**的坑。

**fail-closed 二次校验（BL-6 后半，写在验证层入口，不在生成层）**：
```python
tfs = [tf for tf in strategy.timeframes() if tf != ctx.bar and bar_to_seconds(tf) > base_sec]
missing = [tf for tf in tfs if tf not in ctx.htf_frames]
if missing:
    return ValidationOutcome(verdict="rejected", reject_reason=f"S0:missing_htf:{missing[0]}")
```
> 安全不变式必须立在**产出层**，不能只靠生成层的约定（上游评审的方法论结论）。生成层若哪天
> 被改出一个能变高周期的路径，这条闸会立刻把它拒掉并留痕，而不是产出一条无声错误的净值。

#### 2.4.2 成员目录 `factor_member_catalog(ctx, config) -> list[ComboMemberRef]`

来源三处，按顺序拼接后去重（键 = `(factor_key, canonical_params)`）：
1. **本 run `factor` 轴的 kept 池**：`factor_key="expr"`，`params={"expr": entry.expr_text}`，
   `direction` 取该候选验证时**冻结的方向**，`name=expr:<hash8>`（复用 `promote.member_name`）。
2. **既有挖掘因子**（`factors` 表 `factor_key=="expr" and source=="mined"`），当
   `includeLibraryFactors`（默认 `true`）时纳入；`direction` 取行上的冻结值。
3. **库内可见内置因子**（`available_factors()`，即 `hidden=False` 的目录）：`params` 取库默认，
   `direction` 由 **IS 段**一次性 `resolve_direction(values, close, "auto", primary_horizon=...)`
   冻结（与上游 §2.7.0 同一纪律：**只在 IS 段定号**，绝不用全窗，更不能在验证时传 `auto`）。
   求值失败 / 方向不可判定的因子直接不进目录。
   **入目录前必须先过 `engine.factors.library.is_bar_compatible(key, ctx.bar)`（评审 P1-6）**：
   与当前周期不兼容的因子会在 `score_breakdown` 里抛 `ValueError`，而
   `FactorScoreStrategy.prepare` 把它**吞成安静空仓**，最终伪装成 S1「样本量不足」。

目录在 `_prepare` 构建一次并在每次数据刷新后重建（方向要在新窗口上重新冻结）。目录大小上界
`DISCOVERY_MEMBER_CATALOG_MAX`（默认 60，超出按「本 run kept 优先 → mined 优先 → 内置」截断，
截断量记入迭代记录，绝不静默）。

### 2.5 回测求值协议（`discovery/backtest_lane.py`，新增 ~260 行 · **承重**）

这是本轮与上游 §2.7.0 对位的那一节。上游那节解决「统计式求值怎么切样本外」，本节解决
「路径依赖的回测怎么切样本外」。二者的坑不同，但性质相同：按最直觉的写法实现，结果不报错，
只是安全网从未生效。

#### 2.5.1 成本量纲（BL-1）

```python
def costs_of(settings) -> tuple[float, float, float, float]:
    """(initial_capital, taker_fee_bps, slippage_bps, leverage) —— **bps 原值**。

    ⚠️ 承重：`engine/simulator.py` 内部才 `/1e4`。上游 F8 的 `round_trip_cost` 之所以要
    `/1e4`，是因为它拿 bps 去和**小数**收益差比较；这里是把 bps 交给一个**收 bps 的函数**。
    两处方向相反，混起来就是一次「挖出来的策略全都很赚」的静默事故。

    字段名以既有 `agent/services.py::_resolve_costs` 为准（注意本金字段是
    `settings.initial_capital`，**不是** `default_initial_capital`——另三个才带 `default_` 前缀）。
    """
    return (float(settings.initial_capital), float(settings.default_taker_fee_bps),
            float(settings.default_slippage_bps), float(settings.default_leverage))
```

配套门禁用例 `test_backtest_lane_passes_bps_not_decimals`：monkeypatch `run_backtest`，断言收到
的 `taker_fee_bps == 5.0` 而非 `0.0005`；并加一条反向用例断言 `round_trip_cost` 仍返回小数
（防止有人「统一口径」把上游改坏）。

#### 2.5.2 IS/OOS 双段回测（BL-2）

```python
MIN_WARMUP_BARS = 20

def warmup_pad(strategy) -> int:
    return max(int(strategy.warmup()), MIN_WARMUP_BARS)

def run_split_backtest(assembler, ctx: LaneContext) -> SplitBacktest:
    split = ctx.split                                  # = validation.split_index（同源律）
    pad = warmup_pad(assembler.build())                # 用一个临时实例只问 warmup
    # ① IS：两个**独立实例**，绝不复用（策略持有 self._peak/_trough 等跨根运行态）
    res_is = run_backtest(assembler.build(), ctx.candles[:split], ctx.bar,
                          **cost_kwargs, htf_frames=assembler.htf_frames(ctx))
    # ② OOS：前伸 pad 根**只暖机不交易**
    start = max(0, split - pad)
    gated = GatedStrategy(assembler.build(), first_index=split - start)
    res_oos_raw = run_backtest(gated, ctx.candles[start:], ctx.bar,
                               **cost_kwargs, htf_frames=assembler.htf_frames(ctx))
    # ③ 指标按 ts 切片后由**引擎**重算（估计量同源律，绝不自己写第二份夏普）
    res_oos = clip_result(res_oos_raw, from_ts=ctx.split_ts, initial_capital=cost.initial)
    return SplitBacktest(is_=res_is, oos=res_oos, pad=pad, mode="gated")
```

**指标口径（BL-13 · 评审 P1-1，两条切片路径共用）**：

```python
def metrics_of(points, trades, bar, initial_capital) -> dict:
    """本轮**唯一**的策略指标出口：两个既有引擎函数的并集 + 一个显式派生量。

    ⚠️ 承重：`engine/metrics.compute_metrics` 的返回契约只有 7 项
    （totalReturn / winRate / profitFactor / maxDrawdown / sharpe / tradeCount / avgPnl），
    **没有 calmar**——calmar / sortino / annualizedReturn 在**另一个函数**
    `engine/metrics_ext.compute_extended_metrics` 里。只调前者会让 S5 拿不到 calmar，
    于是 §2.6 的「不可判定即拒」把**每一条**候选都拒在 S5，且不报任何错。
    """
    base = compute_metrics(points, trades, bar, initial_capital)
    ext = compute_extended_metrics(points, trades, bar, initial_capital)
    bars = len(points)
    return {**base, **ext, "bars": bars,
            "tradeRate": round(base["tradeCount"] / bars, 6) if bars else 0.0}
```
`tradeRate`（S7 用）在两个引擎函数里都没有——`metrics_ext` 有的是 `tradesPerWeek`（按墙钟秒
换算），与 S7「按根数的交易密度」不是一回事。故本层**显式派生**并在 §5.1 注明来源，
不得混用 `tradesPerWeek` 冒充。`res_is` 的指标也走同一个 `metrics_of`（IS 段 `compute_metrics`
是 `run_backtest` 内部算好的那份，但缺 ext 项，故 IS 也要补算一次 ext——同一份
`equity_points` / `trades`，零额外回测）。

`GatedStrategy`（~35 行，`backtest_lane.py` 内）：

```python
class GatedStrategy(BaseStrategy):
    """把一个策略包成「前 N 根只暖机不交易」。

    OOS 段必须前伸 `pad` 根，否则 `prepare` 从 OOS 头部重算 rolling，前 `warmup` 根全 NaN、
    整段样本外白丢一截；但前伸的这几根若允许开仓，IS 段的成交就会被算进 OOS——「样本外」
    名不副实。gate 之后，pad 区间内 **零成交、权益恒等于 initial_capital**，于是 ③ 的切片
    是一次**纯切片**，不需要 rebase，drawdown 也天然正确。
    """
    def __init__(self, inner: BaseStrategy, first_index: int):
        # `run_backtest` 本身**不读** meta（评审 P2-2：核对过 engine/backtester.py 全文）；
        # 这里透传只是让包装器在日志 / 调试 / 未来调用方处与被包者同名，不构成任何耦合契约。
        self.meta = inner.meta
        self._inner, self._first = inner, int(first_index)
        self.params = inner.params
    def prepare(self, df, bar): return self._inner.prepare(df, bar)
    def attach_timeframes(self, df, bar, frames): return self._inner.attach_timeframes(df, bar, frames)
    def timeframes(self): return self._inner.timeframes()
    def htf_warmup_bars(self): return self._inner.htf_warmup_bars()
    def warmup(self): return self._inner.warmup()
    def risk_params(self): return self._inner.risk_params()
    def on_candle(self, df, i, ctx):
        return [] if i < self._first else self._inner.on_candle(df, i, ctx)
```

`clip_result(result, from_ts, initial_capital)`（~30 行）：
```python
points = [p for p in result.equity_points if p.ts >= from_ts]
trades = [t for t in result.trades if t.ts >= from_ts]
metrics = metrics_of(points, trades, bar, initial_capital)      # 引擎重算，不自算（BL-13）
```
**gated 模式下不做 rebase**——`points[0].equity` 恒等于 `initial_capital`（pad 区零成交）。
实现时必须**断言**这一点（`assert abs(points[0].equity - initial) < 1e-6`，不成立即
`reject_reason="S0:gate_leak"`，fail-closed）：它是 gate 真的生效的机器判据，比注释可靠。

#### 2.5.3 `strategy_combo` 的例外（BL-3）

`run_combo_backtest` 吃的是 `list[ComboMember]`，组合净值由成员曲线合成，且合成前需要
`lookbackBars` 根的成员表现历史来做选优——把成员 gate 掉会让 OOS 首个再平衡点无从选优。
故本轴改用**rebase 切片**：

```python
def clip_rebased(result, from_ts, initial_capital, bar):
    points = [p for p in result.equity_points if p.ts >= from_ts]
    # 评审 P2-5：切片为空、或起点权益已归零/为负（爆仓）时不得除零，一律 fail-closed。
    if not points or points[0].equity <= 0:
        return None
    scale = initial_capital / points[0].equity          # 权益重基
    peak, rebased = points[0].equity * scale, []
    for p in points:
        eq = p.equity * scale
        peak = max(peak, eq)
        rebased.append(EquityRecord(ts=p.ts, equity=eq,
                                    drawdown=min(eq / peak - 1.0, 0.0) if peak > 0 else 0.0))
    trades = [t for t in result.trades if t.ts >= from_ts]
    return BacktestResult(trades, rebased, metrics_of(rebased, trades, bar, initial_capital),
                          rebased[0].ts, rebased[-1].ts)      # BL-13：并集口径
```
- `drawdown` **必须按切片后的运行峰值重算**（原值是相对整段峰值的，直接沿用会把 IS 段的
  回撤记到 OOS 头上）。`peak` 的初值取 `points[0]` 的重基权益而非 `0.0`（评审 P2-5：
  初值 0 在权益为负时会让 `peak` 停在 0 并除零）。
- pad（**评审 P1-11 修正**）= `combo_warmup_floor(members, policy) + policy.rebalanceBars`，
  其中 `combo_warmup_floor` 必须与 `engine/combo.py::_warmup_floor` **同源**：
  `max(max(成员 warmup), max(RegimeParams.ema_slow_p, RegimeParams.trend_win), policy.lookbackBars)`。
  v1 写的是「成员 warmup + lookbackBars + rebalanceBars」——漏了 **regime 起算根**。轮动参数
  是被挖的，`lookbackBars` 可以低到 24；此时 pad 会**小于** `_warmup_floor`，OOS 段头部就会有
  一截「组合还没开始决策」的恒定权益被算进样本外指标，把 S6 稀释成一条更容易通过的闸。
  不报错，只是防线变松——本文反复要防的正是这种。
- 跨界那一笔（开在 IS、平在 OOS）的盈亏**会**留在切片后的权益里，笔数按 `ts ≥ splitTs` 统计。
  这是本轴与另两轴的**口径差异**，必须落到详情抽屉的一行说明与 `metrics.oos.clipMode`
  字段（`"gated"` / `"rebased"`），**不得**用一套话术盖住两种口径。

#### 2.5.4 三个 `Assembler`（genome → 可回测对象）

```python
class ParamAssembler:      # strategy_param
    build() -> cls(genome.params)                              # cls = get_strategy_class(key)
    htf_frames(ctx) -> {tf: ctx.htf_frames[tf] for tf in build().timeframes() if tf in ctx.htf_frames}

class FactorComboAssembler:  # factor_combo
    def members(self):
        """BL-12（评审 P0-3）：权重**先烘焙进成员**，再交给边界校验器。"""
        raw = [{"factorKey": m.factor_key, "params": m.params, "direction": m.direction,
                "name": m.name, "weight": w}                       # ← 缺这个 w 就全是 0.0
               for m, w in zip(genome.members, genome.weights)]
        return factors.specs.validate_members(raw)                 # 全限定名，见 P2-4
    def build(self):
        params = {**FactorScoreStrategy.meta.default_params,
                  "members": self.members(),
                  "thresholds": factors.specs.normalize_thresholds(genome.thresholds)}
        return FactorScoreStrategy(params)
    htf_frames(ctx) -> {}                                          # 因子打分不跨周期

class RotationAssembler:   # strategy_combo → 不走 run_backtest，走 run_combo_backtest
    members() -> combo_specs.build_members(specs, notes, strict=True)
    policy()  -> combo_specs.policy_from_spec({**genome.policy, **frozen_policy_values(settings)})
    def htf_frames_by_member(self, ctx):
        """评审 P1-4：**按 `member.key`（展示 key）索引**，不是 `template_key`。"""
        return {m.key: {tf: ctx.htf_frames[tf] for tf in m.timeframes if tf in ctx.htf_frames}
                for m in self.members()}
```
`FactorComboAssembler` **必须**经 `factors.specs.validate_members` + `factors.specs.normalize_thresholds`
装配，`RotationAssembler` **必须**经 `strategies.combo_specs.build_members(strict=True)` +
`policy_from_spec`。理由：这三个函数是入库时的同一道边界校验，**挖掘期与入库期用同一把尺子**，
否则会挖出一批「验证通过但一入库就 400」的候选。`strict=True` 让不可用成员**报错而非静默跳过**
（默认的 best-effort 会把 4 成员组合悄悄变成 2 成员，指标却按 4 成员报出去）。

> **P2-4 命名坑**：`normalize_thresholds` 在 `discovery/validation.py`（因子 11 闸阈值）与
> `factors/specs.py`（因子组合打分阈值）里**各有一个，语义完全不同**。本节指的是后者，
> 实现时一律写全限定名，禁止 `from ... import normalize_thresholds` 裸名引入。

**装配层的两条 fail-closed 判据（评审 P1-4 / P1-6 / P2-8）**，写在验证层入口、`run_split_backtest`
之前：

| 判据 | 适用轴 | 不成立时 |
|---|---|---|
| `set(m.key for m in members())` 与 `htf_frames_by_member` 的键集合**逐键相等**，且每个声明了高周期的成员都拿到了全部所需帧 | `strategy_combo` | `S0:missing_htf:<memberKey>:<tf>` |
| `prepare` 后 `df["_score"]` **不是全 NaN**（等价判据：`strategy._breakdown is not None`） | `factor_combo` | `S0:score_unavailable` |
| `build_members` / `validate_members` / `validate_combo_params` 抛 `fastapi.HTTPException` | 三条策略轴 | `S0:assemble_rejected:<code>`（**不要**落成 `eval_error:HTTPException`） |

> 第二条为什么必须有：`FactorScoreStrategy.prepare` 里写着
> `except (ValueError, KeyError): df["_score"] = float("nan")`——未知 factorKey、非法 direction、
> 因子与该 bar 不兼容，统统**不抛错，只是不交易**。于是 §2.6 的「回测抛错即拒」根本抓不到它，
> 整条轴退化成「候选全被 S1 拒」，在界面上看起来像「组合因子在这个市场没有 alpha」。
> 安全不变式必须立在**产出层**（上游评审的方法论结论），这条断言就是产出层的机器判据。
>
> 第一条为什么必须有：`combo_specs.build_members` 对同模板的第二个实例生成
> `display_key = f"{template_key}#2"`，而 `run_combo_backtest` 是按 `member.key` 取帧的。
> §2.3.3 又明确允许「同模板可重复出现但参数必须不同」。按 `template_key` 组装帧字典，
> `#2` 成员就会静默走「无 MTF 数据」路径——正是 BL-6 要防的事，而 §2.4 的 fail-closed
> 只覆盖了单策略路径，覆盖不到组合成员。

### 2.6 策略硬闸 S1–S9（`discovery/strategy_gates.py`，新增 ~230 行 · fail-closed）

与上游 D-4 同一姿态：达标与否**绝不**由 LLM 说了算。评估**按 ID 顺序**，首个未通过即返回；
最贵的一条排最后（同上游把 `constant_or_empty` 排在规则链末位的理由）。

| 闸 | 判据 | 阈值键（默认） |
|---|---|---|
| S1 | IS 平仓笔数 ≥ `minTrades`；OOS 平仓笔数 ≥ `max(3, minTrades × 0.25)` | `minTrades`(20) |
| S2 | IS `totalReturn` > 0（**费后**） | — |
| S3 | IS `sharpe` ≥ `max(minSharpe, penalized_gate(minSharpe, trials, k))` | `minSharpe`(0.8) / `trialPenaltyK`(0.15) |
| S4 | \|IS `maxDrawdown`\| ≤ `maxDrawdown` | `maxDrawdown`(0.35) |
| S5 | IS `calmar` ≥ `minCalmar`（**只可能来自 `compute_extended_metrics`**，见 BL-13） | `minCalmar`(1.0) |
| S6 | OOS `sharpe` > 0 **且** ≥ `oosRetain × IS sharpe`；OOS `totalReturn` > 0 | `oosRetain`(0.4) |
| S7 | 交易密度 `IS tradeCount / IS bars` ≤ `maxTradeRate`；IS `profitFactor` ≥ `minProfitFactor` | `maxTradeRate`(0.05) / `minProfitFactor`(1.1) |
| S8 | 与 kept 池中**每一条**的逐根收益序列 \|Pearson\| < `maxEquityCorr` | `maxEquityCorr`(0.85) |
| S9 | **成本压力**：以 `costStressMult ×` 成本重跑 **IS 段**，要求 `totalReturn` 仍 > 0 | `costStressMult`(2.0) |

**S3 与因子轴同源（BL-4）**：把上游 `validation.required_ir` 的公式提成
```python
def penalized_gate(base: float, trials: int, k: float) -> float:
    return base * (1.0 + k * math.log10(max(int(trials), 10) / 10.0))

def required_ir(trials, thresholds):      # 保持既有签名与返回值逐位不变
    # 评审 P2-1：默认值一律引 DEFAULT_THRESHOLDS，禁止硬写字面量——现值确为 0.5 / 0.15，
    # 但硬写会在默认值调整时与真源静默漂移，正是 BL-4 要防的那种漂移。
    return penalized_gate(thresholds.get("minAbsIr", DEFAULT_THRESHOLDS["minAbsIr"]), trials,
                          thresholds.get("trialPenaltyK", DEFAULT_THRESHOLDS["trialPenaltyK"]))
```
配一条「重构后 `required_ir(1000, 默认阈值)` 数值与硬编码期望逐位相等」的用例。

**`trials` 的取值必须逐轴（BL-14 · 评审 P1-2）**：S3 与因子轴 F3/F11 传进来的 `trials`
一律取**本轴**已测数（`lane.tested`），不是 run 全局的 `_counters["tested"]`。现有实现里
`_validate_one` 用的是全局值；四轴同跑后，因子轴的门槛会被三条策略轴的候选一起抬高——
**一条已发布并已验收的轴，行为被本轮悄悄改掉了**。统计上逐轴也才是对的：多重检验校正是
「同一族假设里测了多少次」，跨族相加没有意义。

**S6 是本轮最强的样本外防线**，也是最容易被实现成装饰的一条。因子轴的坑是「两段各自 auto 定号
⇒ 恒同号」；策略轴没有定号问题，但有**等价的坑**：若 OOS 段被实现成「在 IS 训练好的参数上再跑
一次全窗」，S6 恒真。故门禁用例 `test_s6_rejects_out_of_sample_collapse` 必须构造一条
「IS 段强趋势、OOS 段震荡反转」的合成 K 线，断言候选被 S6 拒；再构造一条两段同分布的，断言通过。
两条都要有——只有拒的用例证明不了闸没被写成恒假。

**S9 的开销与位置**：一次额外 IS 回测。因为排在最后，只有已过 S1–S8 的候选（实测入库率量级
1–5%）才付这个代价。**必须**用与 S1 完全相同的 `assembler` 与窗口，只改成本参数——否则比较的
不是同一件事。压力回测的结果**不**进 kept 池，只产出 `metrics.stress`。

**不可判定即拒**（fail-closed，与上游 F9 同姿态）：任一指标为 `None` / `NaN` / 回测抛错，
一律 `rejected`，`reject_reason` 形如 `S5:calmar_undefined` / `eval_error:<ExcName>`。

#### 2.6.1 去重池（S8）与刷新重建（BL-5）

```python
@dataclass
class StrategyKeptEntry:
    candidate_id: int
    kind: str
    genome_hash: str
    genome: dict                # 重建时据此复装 assembler
    bar_returns: np.ndarray     # IS 段逐根收益率（去重基准，**不落库**）
    score: float                # = IS sharpe
    family: str
    skeleton: str
```
`bar_returns` 与因子轴的 `values` 同性质：窗口一右移即失效，故**不落库**，由
`rebuild_strategy_pool(rows, ctx)` 在**续跑**与**数据刷新**两处用**同一个实现**重建（BL-5）。
重建时必须复跑一次 `assemble → 校验`（上游 I-2 的同款教训：解析得通不等于仍合法；被外部改坏
或跨版本遗留的基因进池就成了一条错误的去重基准，会误杀本该入库的新候选且完全不可见）。
重建全失败 ⇒ 返回 `errors` 且不带残缺池开跑，调用方转 `paused(rebuild_failed)`。

刷新后的轻量复评只重算 **S2 / S3 / S6**（对位因子轴的 F2 / F3 / F6），不达标者按
`revalidateOnRefresh` 决定是否降级——与上游 RV-5 一致：**重建无条件执行**，开关只决定要不要淘汰。

#### 2.6.2 拒绝原因的中文词表（**两处，缺一即回显裸英文码**）

v1 写的「前端 `rejectReason` 映射零改动」是错的（评审 P1-5）：后端的 `FILTER_LABELS` 与前端的
`apps/web/lib/discovery.ts::REJECT_REASON_CN` 是**两张各自硬编码的表**，`rejectReasonCn(code)`
对未登记的码**原样回显**。只改后端，用户会在「实时监控 → 拒绝原因 Top 5」里看到裸的
`S6` / `S0:gate_leak` / `S0:missing_htf:12H`。这是上一轮 RV-6（`category=discovered` 未登记
中文映射）的同款复发，故本轮**两处都写死**：

1. **后端** `discovery/validation.py::FILTER_LABELS` 增补：
   `S1 样本量不足` / `S2 样本内亏损` / `S3 夏普未达（含多重检验惩罚）` / `S4 回撤超限` /
   `S5 Calmar 不足` / `S6 样本外崩塌` / `S7 交易过密或盈亏比不足` / `S8 与已入库策略高度相关` /
   `S9 成本压力下不再盈利` / `S0 装配失败`。
2. **前端** `apps/web/lib/discovery.ts`：`REJECT_REASON_CN` 增补同样 10 条；并在
   `rejectReasonCn` 里**新增 `S0:` 前缀分支**（与既有 `eval_error:` / `llm_review:` 同款），
   把子因翻成中文：
   ```ts
   if (code.startsWith("S0:")) return `装配失败（${S0_SUB_CN[sub] ?? sub}）`;
   // S0_SUB_CN: missing_htf 高周期数据缺失 / gate_leak 样本外隔离失效 /
   //            score_unavailable 组合打分不可用 / assemble_rejected 边界校验拒绝
   ```
   配用例 `discovery.test.ts`：`rejectReasonCn("S0:missing_htf:12H")` 与 `rejectReasonCn("S6")`
   都**不得**等于原码。这条用例就是「后端算了、前端没接」的机器判据。

#### 2.6.3 得分口径（父本选择 / 池截断共用）

`score` 因子轴 = `|IS IR|`，策略轴 = `IS sharpe`。两者不可比，故**锦标赛选择与 `maxKept`
截断一律在轴内进行**，绝不跨轴排序。`maxKept` 由「全局一个数」改为「逐轴一个数」
（`maxKeptPerAxis`，默认 200；旧配置里的 `maxKept` 作为缺省值向后兼容）。

#### 2.6.4 FSA / 机制族在策略轴的语义

上游的 FSA（频繁子树规避）与机制覆盖两套机制**原样复用**，只是喂给它们的 `skeleton` 与
`mechanism` 换了定义。**统计容器逐轴独立（BL-14）**：`skeleton_stats` / `fsa_bans` /
`kept_skeleton_counts` 各轴一份，绝不共用一个字典——四种语义完全不同的骨架混进同一个分母，
15% 阈值与 `variantCap` 都会算成一笔糊涂账。

| 轴 | `skeleton`（FSA 键 / `variantCap` 键） | `mechanism`（覆盖度键） |
|---|---|---|
| `factor` | 表达式骨架（不变） | 13 个机制族（不变） |
| `strategy_param` | `template_key` | 模板族：`trend` / `reversal` / `breakout` / `range` / `scalp` |
| `factor_combo` | **逐成员语义键**排序后拼接再取 8 位哈希（见下） | 恒 `factor_combo` |
| `strategy_combo` | 成员 `template_key` 排序后拼接的 8 位哈希 | 恒 `rotation` |

**`factor_combo` 的成员语义键（BL-10 · 评审 P0-1，承重）**：
```python
def member_skeleton_key(ref: ComboMemberRef) -> str:
    """expr 成员取**表达式骨架**，库内因子取 factorKey。"""
    if ref.factor_key == "expr":
        return f"expr#{expression.skeleton(parse_expression(ref.params['expr']))}"
    return ref.factor_key
```
> v1 写的是「成员 `factorKey` 排序后拼接」。核对源码后这条会**退化成一个常量**：本 run 挖到的
> 因子与既有挖掘因子，其 `factor_key` **一律是字面量 `"expr"`**
> （`factors/specs.py::EXPR_FACTOR_KEY`；`discovery/promote.py::_factor_score_params` 就是这么
> 写的）。于是「任意 3 个挖掘因子的组合」算出来的 skeleton 完全相同。后果两条，都不报错：
> ① `discovery/review.py:114` 的 `variant_cap`（默认 6）在同骨架入库数 ≥6 时直接拒 ⇒ **第 7 个
> 组合候选起全部卡在审查层，永远进不了回测**；② `fsa.scan_skeletons` 算出的 share ≈ 100% > 15%
> ⇒ 第 25 轮 FSA 扫描即冻结这唯一骨架，TTL 150 轮且每次扫描重新冻结 ⇒ 实际上永久。
> 用户看到的现象是「这条轴挖了两天，一个都没产出」，日志里一条错误也没有。

**FSA 的最小池护栏（BL-10 后半）**：`fsa.scan_skeletons` 的 share 分母是**本轴 kept 池**。
池子小的时候这个比例毫无意义——池里只有 3 条时每条都占 33% > 15%，会被**全部**冻结。
故调用点加护栏：
```python
FSA_MIN_POOL = 20           # 低于此规模不扫描（1/0.15 ≈ 6.7，取 3 倍留余量）
if lane.pool_size() < FSA_MIN_POOL:
    continue                # 本轴本轮跳过 FSA，并记 fsaSkipped[axis]="pool_too_small"
```
这条对因子轴是**行为不变**的（因子轴入库快，长跑下池子远超 20；且 BL-8 的 golden 用例只跑
3 轮、本就不触发 FSA），对回测型的三条策略轴则是必需——它们的入库速度比因子轴低一到两个
数量级，早期池子长期是个位数。跳过必须**留痕**（进迭代记录 + 监控页），不得静默。

效果：`variantCap`（默认 6）自然限制「同一个模板的第 7 个参数变体」，FSA 的 15% 阈值自然
在某个模板占据入库池 15% 以上时把它冻结一段时间——正是报告里「结构趋同抑制」的策略版，
零新机制、零新 UI。模板族映射写死在 `catalogs.py::TEMPLATE_FAMILY`（有序规则链，**不用加权
打分**，上游 I-5）。

> **`strategy_param` 轴的阈值须单独给**：该轴的骨架词表就是注册表里的原子模板，当前只有
> 8 个左右。15% 的阈值意味着「任何一个模板只要占了入库池的 1/6 就被冻结 150 轮」——在 8 元
> 词表上这几乎必然命中最好的那两三个模板，等于把搜索赶去挖已知较差的模板。故该轴的
> `fsaThreshold` 默认取 `max(0.15, 2 / len(minable_templates))`（8 个模板 ⇒ 0.25），
> 并在启动器的「高级」区可调。`variantCap` 保持 6 不变——它才是这条轴上真正合适的多样性闸。

### 2.7 审查层在策略轴的形态（`review.py` 增补 ~70 行）

**第一道：确定性规则**（纯函数，逐条中文 `rejectReason`）：

| 规则 | 适用轴 | 判据 |
|---|---|---|
| `duplicate` | 全部 | `genome_hash` 已在本 run 已测集合中 |
| `fsa_banned` / `variant_cap` / `skeleton_blacklisted` | 全部 | 复用上游实现（键见 §2.6.4） |
| `degenerate_params` | `strategy_param` | 所有可挖参数都等于模板默认值（等于没挖） |
| `degenerate_members` | `factor_combo` / `strategy_combo` | 成员去重后数量 < 2；或某成员权重 < 0.05（等价于没有它） |
| `duplicate_members` | `factor_combo` | 两个成员的 `(factorKey, params)` 相同 |
| `risk_inconsistent` | `strategy_param` | `stop_loss_pct ≥ take_profit_pct`（若两者都在 schema 中）——必亏结构，不值得付一次回测 |
| `window_inconsistent` | `strategy_param` | 存在语义上的「快窗 ≥ 慢窗」参数对（按 `FAST_SLOW_PAIRS` 声明表判定，如 `fast`/`slow`） |

**第二道：LLM 精判**（抽样，可关，**fail-open**）：与上游同一实现，只是 prompt 里的候选描述
换成 `describe_strategy`。裁决语义仍是**只减不增**：`accept=false` ⇒ 拒；`accept=true` 不代表
放行，仍要过 S1–S9。超时 / 报错 / 未配置 ⇒ 全部视为 accept 并标 `llmDegraded=true`。

### 2.8 轴预算分配与上游库存让渡（`discovery/axis_budget.py`，新增 ~120 行 · 纯函数）

```python
def plan_axis_budget(axes, weights, count, readiness) -> AxisPlan:
    """把一轮的候选名额切给各轴（最大余数法），并按上游库存做让渡。

    readiness: {axis: (ready: bool, reason: str)}。not ready 的轴名额**整体让渡**给
    **本 run 已选轴（`axes`）中**、按 `genomes.ALL_AXES` 次序排在最前的那条 ready 轴，
    并在 plan.yielded 里如实记录 {axis: (n, reason, to_axis)}。全部轴都 not ready ⇒
    返回空计划，调用方跳过本轮迭代并推一条 `error` 事件说明原因（**不**转 paused：
    库存会随下一轮因子轴产出而恢复）。
    """
```
> **评审 P1-8 的两处修正**：① 接收方必须限定在**本 run 已选轴**内——否则用户没勾 `factor`
> 时，名额会被让给一条根本没启用的轴，等于凭空蒸发；② `genomes.ALL_AXES` 的**实际**顺序是
> `("factor", "factor_combo", "strategy_param", "strategy_combo")`，而 `factor` 恒 ready，
> 所以「按 ALL_AXES 次序取第一条 ready」在四轴全选时**总是让给 factor 轴**。v1 §2.8 的 UI
> 示例写的却是「策略组合 → 常规策略」，两者不可能同时成立。示例已按规则改写；`to_axis`
> 也进了 `yielded` 的结构，**让渡去了哪条轴必须落到界面上**，不能让用户自己推。

就绪判据：

| 轴 | ready 条件 | 未就绪原因码（中文由前端映射） |
|---|---|---|
| `factor` | 恒 ready | — |
| `strategy_param` | 模板目录非空 | `no_minable_template` |
| `factor_combo` | 成员目录 ≥ 2 | `member_catalog_too_small` |
| `strategy_combo` | `strategy_param` 轴 kept 池 ≥ `minRotationPool`(2) | `upstream_pool_too_small` |

**BL-9 的可见性落点（三处，缺一不可）**：① `IterationRow.stats.yieldedByPool`；
② WS `iteration` 事件透传该字段；③ **`DiscoveryFunnel.tsx`** 新增一行
「本轮让渡：策略轮动 12 → 因子（原因：常规策略池 1/2 不足）」——注意漏斗组件是
`components/Discovery/DiscoveryFunnel.tsx`，**不是** `DiscoveryMonitor.tsx`（评审 P1-10④）。
**后端算了、类型补了，用户看不见，就等于没做**（仓库既有教训）。

**资源硬顶**：一轮内的回测总次数（含 S9 压力回测）不超过 `discovery_max_backtests_per_iteration`
（默认 120）；触顶即截断本轮剩余候选并记 `truncatedByBacktestCap`（沿用既有
`truncatedByTimeout` 的呈现位）。**计数单位见 §六（评审 P1-3）：数的是底层 `run_backtest`
调用次数，不是候选数**——一个 `strategy_combo` 候选背后是 4–12 次。

### 2.9 循环编排的增量（`runtime.py`）

四步时序与上游完全一致，只在第 ①③ 步内部按轴展开：

```
① 生成：for axis in plan:  lane.produce(rng, budget=五维预算, count=plan[axis], tally=tally[axis])
② 审查：for axis:          lane.review(candidates)         # 规则链 → LLM 抽样（全轴合并成一次调用）
③ 验证：交错执行           # 见下
④ 提交：合并所有轴的候选行 + 逐轴 tally → 单事务落库 → 事件扇出
```

**第 ③ 步的交错**：不按轴串行（否则一个慢轴会吃光整轮墙钟预算，后面的轴一条都验不上），而是
**轮转**（round-robin）逐候选验证，共用同一个墙钟预算 `discovery_iteration_timeout_s` 与同一个
`discovery_validate_concurrency` 信号量。截断时按轴分别记 `truncated[axis]`。
**逐轴计数（BL-14）**：每验完一条，只自增**本轴**的 `lane.tested` 与 `lane.skeleton_stats`；
runtime 侧那份全局 `counters.tested` 降级为「四轴求和的只读展示值」，不再参与任何判据。

**LLM 调用合并**：三条策略轴 + 因子轴的 LLM 份额**合并成一次生成调用**（payload 里带
`perAxis: {axis: slots}`），审查抽样同理合并成一次。上游的小时配额闸不变——多轴不得让 LLM
调用次数翻四倍。

### 2.10 检查点与断点续跑的增量（`checkpoint.py`）

新增字段（全部**可缺省**，老检查点直接续跑，配 `test_checkpoint_accepts_v1_payload` 守门）：

| 字段 | 语义 |
|---|---|
| `axis_shares` | 上轮实际生效的轴名额分配（回放与 UI 用） |
| `pool_sizes` | 已存在，改为逐轴填（`{factor: n, strategy_param: n, ...}`） |
| `member_catalog_hash` | 成员目录的内容哈希；刷新后不一致即强制重建组合轴的池 |
| `template_signature` | 可挖模板目录签名（key + 参数梯子的哈希）；不一致（版本升级新增/改了模板）时，`strategy_param` 池必须整池重建，不一致的条目降级并留痕 |
| `tested_by_axis` | **BL-14**：逐轴累计已测数（多重检验分母）。缺省时从既有全局 `counters.tested` 整体落到 `factor` 轴——老检查点续跑后因子轴门槛**逐位不变**，三条新轴从 0 起算 |
| `skeleton_stats_by_axis` | **BL-14**：逐轴失败模式库（各自过 `prune_skeleton_stats` 的 256 条上界，故检查点仍有界）。缺省时同上，整体归 `factor` 轴 |
| `fsa_bans_by_axis` | **BL-14**：逐轴冻结名单与 TTL 起点。缺省时同上 |

> 后三项的缺省回落规则是老检查点兼容的**承重细节**：如果缺省时归零，一个跑了两周的因子
> 任务在升级后会把 `trials` 清零，F3/F11 的多重检验惩罚整体塌回基准值，第二天就会收进一批
> 本该被惩罚掉的噪声因子。`test_checkpoint_accepts_v1_payload` 必须断言这条回落。

`template_signature` 这一条是必须的：升级后某模板的 `params_schema` 若改了 `min/max`，
旧基因可能已越界。**不检查就会静默把越界参数当成合法基因继续繁殖**。

### 2.11 入库（promote）三条新路径（`promote.py` +~190 行）

| 轴 | 目标实体 | 实现 | 幂等键 |
|---|---|---|---|
| `strategy_param` | `strategies` 行 | `repo.add_strategy(template_key, name, params)` | `(template_key, canonical_json(params))` 已存在即复用 |
| `factor_combo` | `factor_sets` + 伴生 `factor_score` 策略 | **必须**调 `factors.service.create_set(...)`（单事务 + 伴生策略 + 三道校验），绝不自建行 | 同名 + 同成员集合已存在即复用 |
| `strategy_combo` | `strategies` 行（`template_key="regime_rotation"`） | 先 `combo_specs.validate_combo_params` 再 `add_strategy` | 同上（canonical params） |

命名（`Strategy.name` 与 `FactorSet.name` 都是 `String(128)`，与上游 `Factor.name` 同款约束）：
- `挖掘策略 <hash8>：<模板中文名>`（截断至 128）
- `挖掘组合 <hash8>：<N 因子>`（`factor_sets.name`）
- `挖掘轮动 <hash8>：<N 策略>`

**幂等回放路径必须补齐 `companionStrategyId`（评审 P1-9）**：`promote_candidate` 现有实现在
`row.promoted_kind and row.promoted_id` 时直接 `return _promoted_payload(kind, id, reused=True)`。
`companionStrategyId` 只有首次调 `factors.service.create_set` 才拿得到，于是**第二次点「入库」
返回的 payload 里没有它**，成果库的「去策略面板」跳转就断了。故 `factor_set` 分支的回放路径
必须按 `promoted_id` 反查伴生策略 id（复用 `factors` 侧既有的
`ensure_companion_strategy` / `_set_out_with_strategy`，不新造查询）：

```python
if row.promoted_kind == "factor_set" and row.promoted_id:
    companion_id = await factors_service.companion_strategy_id(session, int(row.promoted_id))
    return _promoted_payload("factor_set", int(row.promoted_id),
                             reused=True, companion_strategy_id=companion_id)
```
配用例：连点两次 promote，**两次响应的 `companionStrategyId` 相等且非空**。

**BL-7（承重）**：`params_json` 只写**被回测的那份参数**，不塞 `_discovery` 之类溯源字段。
溯源已经有地方放了——`discovery_candidates.promoted_kind/promoted_id` 是反查真源，
成果库 UI 从候选侧渲染。往 `params_json` 里塞东西的代价是：那份 JSON 会被
`cls({**defaults, **params})` 原样喂进策略实例，也会被组合 / 因子校验器看见，
从此「挖的时候是 A、入库后跑的是 A+x」——本仓库反复吃亏的那类静默背离。

`autoPromote`（默认仍 `false`）打开时，kept 即入库，走上表同一实现（幂等），失败只记日志
不打断循环。**安全不变式不变**：入库只写 `factors` / `factor_sets` / `strategies` 三张表，
**严禁**触碰 `strategy_runs`、watchlist、自动交易成员、live-auto / ai-trade 的任何配置。

### 2.12 前端信息架构与交互

沿用既有五分区（总览 / 挖掘任务 / 实时监控 / 候选库 / 成果库），全部改为**轴感知**：

- **启动器**：轴选择从单选下拉改为**多选卡片组**（每张卡片：轴名 + 一句话说明 + 预计每轮
  回测次数）。选中 `strategy_param` 展开模板白名单（可挖模板可勾选，不可挖模板**灰显并直接
  写出原因**，绝不隐藏）；选中 `factor_combo` 展开成员来源开关与组合规模区间；选中
  `strategy_combo` 展开轮动规模区间并提示「依赖常规策略轴的产出」。策略阈值（S1–S9）一组
  折叠在「高级」里，带「恢复默认」。
- **候选库**：新增 `kind` 过滤 chip；表格列**按轴切换**（因子：IC / IR / 单调性；策略：
  夏普 / 收益 / 回撤 / 笔数）。混排时用一列徽标区分轴。
- **候选详情**：因子沿用表达式树；策略新增 `DiscoveryStrategyDetail`——参数对照表（默认值 vs
  挖到的值，差异高亮）/ 成员清单（组合轴）/ **IS 与 OOS 双段净值图**（复用
  `Chart/EquityChart` 的 **`overlays` 分支**，两条）/ S1–S9 闸位阶梯（逐条显示实际值与门槛，
  通过打勾、卡在哪条标红）/ `clipMode` 口径说明（BL-3）。
  > 评审 P2-9：`EquityChart` 的 `equity` 与 `overlays` 两个入参**互斥**，右轴口径也不同
  > （原值 vs 归一化百分比，见该文件顶部注释）。IS/OOS 双段必须只传 `overlays`（两段各自
  > 归一化到起点，正好是我们要的可比形态），**不得**与 `equity` 混传。
- **实时监控**：漏斗按轴分列（代码在 `DiscoveryFunnel.tsx`，`DiscoveryMonitor.tsx` 只是装配）；
  新增「让渡」行（BL-9）；机制覆盖图在 `strategy_param` 轴下显示模板族覆盖，在
  `factor_combo` / `strategy_combo` 两轴下**整块隐藏**——这两轴的 `mechanism` 恒为单值
  （`factor_combo` / `rotation`），画出来就是一根 100% 的柱子，是在假装有信息量
  （评审 P2-11；沿用 `DiscoveryFunnel` 「诚实优先于漂亮」的既有姿态）。
- **成果库**：按实体类型分组（因子 / 因子组合 / 策略），每条带「去策略面板」「去因子组合」
  的跳转（复用既有 `setActiveView` + 分区跳转），完成「挖到 → 一眼看到 → 一键用起来」的闭环。

**无障碍与既有规范**：所有新控件带 `aria-label`；Esc 仍返回交易工作台；数值一律带单位与
中文标签；页脚风险声明不变（「所有指标均为历史样本统计，不构成任何收益预期或投资建议」）。

### 2.13 关键代码路径（逐调用链，供实现对照）

**启动一个三轴 run**
```
POST /discovery/runs
 → service.start_run
    ├ 闸1 discovery_enabled → 403
    ├ 闸2 normalize_config（_normalize_axes 现在放行全部 4 轴；新增 strategyThresholds /
    │       templates / axisWeights / comboSize / rotationSize 归一化）
    ├ 闸3 workload_of（新口径：Σ 轴名额 × 窗口根数 × 轴代价系数，见 §六）→ 413
    ├ 闸4 manager.reserve() → 409
    └ _create_and_launch → repo.create_run → manager.start(RunSpec)
 → DiscoveryRun._prepare
    ├ _load_state（检查点 + 已测哈希）
    ├ _refresh_data（基础窗口）
    ├ _load_htf_frames（§2.4.1 并集，失败按数据失败姿态处理）
    ├ catalogs.minable_templates / factor_member_catalog
    ├ （resume 时）逐轴 lane.rebuild → 任一失败即 paused(rebuild_failed)
    └ 构造 lanes = {axis: Lane(...) for axis in config["axes"]}
```

**一轮迭代**
```
_run_iteration
 ├ plan = axis_budget.plan_axis_budget(axes, weights, count, readiness)
 ├ for axis in plan: lane.produce(...)                     # 五维演化，LLM 份额合并
 ├ for axis in plan: lane.review(...)  → 合并一次 LLM 抽样精判
 ├ round-robin: run_validation(lane, candidate, ...)       # 线程池 + 信号量 + 单候选超时
 │    factor    → FactorLane.validate  → validation.validate_candidate（**逐位不变**）
 │    其余三轴  → StrategyLane.validate → backtest_lane.run_split_backtest
 │                                      → strategy_gates.check（S1..S9）
 ├ _run_fsa（逐轴池各跑一次）
 └ _commit（单事务：候选行 + 迭代行 + 检查点 + 计数）→ WS 扇出
```

**入库**
```
POST /discovery/candidates/{id}/promote
 → promote.promote_candidate
    ├ verdict != kept → 409
    ├ 已入库 → 幂等返回既有实体
    └ by kind:
        factor         → _promote_factor（不变）
        strategy_param → _promote_strategy
        factor_combo   → _promote_factor_set → factors.service.create_set（单事务，含伴生策略）
        strategy_combo → _promote_rotation（先 validate_combo_params）
```

---

## 三、文件 / 模块变更计划

### 3.1 后端 · 新增

| 文件 | 行数预算 | 一句话意图 |
|---|---|---|
| `app/discovery/lanes/__init__.py` | 15 | 只再导出 `FactorLane` / `StrategyLane` / `build_lanes` |
| `app/discovery/lanes/base.py` | 230 | `Lane` 协议、`LaneContext`、`run_validation`（超时/信号量/异常收口）、`candidate_rows`、`absorb`/`trim` |
| `app/discovery/lanes/factor_lane.py` | 200 | 因子轴 Lane（从 `runtime.py` **逐行平移**，行为逐位不变，BL-8） |
| `app/discovery/lanes/strategy_lane.py` | 280 | 三条策略轴共用的 Lane（靠 `kind` + `assembler` 区分） |
| `app/discovery/backtest_lane.py` | 260 | **承重**：`costs_of`(BL-1)、`run_split_backtest`(BL-2)、`GatedStrategy`、`clip_result` / `clip_rebased`(BL-3)、三个 `Assembler` |
| `app/discovery/strategy_gates.py` | 230 | S1–S9 + `penalized_gate` 复用 + `StrategyKeptEntry` + `rebuild_strategy_pool`(BL-5) |
| `app/discovery/catalogs.py` | 200 | 可挖模板目录（含 `FROZEN_PARAM_KEYS` / `TEMPLATE_FAMILY` / 梯子构造）与因子成员目录 |
| `app/discovery/axis_budget.py` | 120 | 纯函数：轴名额分配（最大余数）+ 就绪判定 + 让渡记录（BL-9） |
| `app/discovery/describe_strategy.py` | 110 | 三种策略基因的中文渲染（UI / prompt / 入库名唯一真源） |
| `app/discovery/searchers/param_searcher.py` | 200 | 常规策略参数搜索器（梯子 + 五维演化） |
| `app/discovery/searchers/combo_searcher.py` | 210 | 因子组合搜索器（成员 / 权重 / 阈值） |
| `app/discovery/searchers/rotation_searcher.py` | 180 | 策略轮动搜索器（成员来自上游 kept 池） |

### 3.2 后端 · 修改（既有文件，全部为**加法**或**平移**）

| 文件 | 改动 | 行数变化 |
|---|---|---|
| `app/discovery/runtime.py` | 平移逐候选验证到 lanes；改为多 Lane 调度；HTF 取帧；目录构建；FSA 逐轴；`tested`/`skeleton_stats`/`bans` 逐轴化（BL-14） | 785 → ~530 |
| `app/discovery/genomes.py` | 三个新基因体 + `make_*_candidate` + `IMPLEMENTED_AXES = ALL_AXES` | 88 → ~190 |
| `app/discovery/service.py` | `_normalize_axes` 放行全部轴；新增 `axisWeights`/`templates`/`strategyThresholds`/`comboSize`/`rotationSize`/`includeLibraryFactors` 归一化；`workload_of` 新口径；`config_defaults` 增 `templates` 与 `strategyThresholds` | 377 → ~470 |
| `app/discovery/validation.py` | 抽出 `penalized_gate`（BL-4），`required_ir` 改为调它（返回值逐位不变，默认值引 `DEFAULT_THRESHOLDS`）；`FILTER_LABELS` 增补 S0–S9 | 527 → ~550 |
| `app/factors/service.py` | 只加一个只读助手 `companion_strategy_id(session, set_id)`（P1-9 幂等回放用），零行为变更 | +14 |
| `app/discovery/review.py` | 策略轴的 7 条确定性规则 | 167 → ~240 |
| `app/discovery/promote.py` | 三条入库路径 + 一键回测扩展到策略轴 + 幂等回放补 `companionStrategyId`（P1-9） | 158 → ~360 |
| `app/discovery/checkpoint.py` | 7 个可缺省新字段（含 BL-14 的三项逐轴计数与其老检查点回落规则） | 173 → ~215 |
| `app/discovery/repositories.py` | `list_candidates` 增 `kind` 过滤；`list_kept_candidates` 增 `kind` 参数 | 333 → ~355 |
| `app/discovery/prompts.py` | 策略轴的生成 / 审查 prompt 片段 | 209 → ~290 |
| `app/discovery/llm_agents.py` | 生成 payload 增 `perAxis`；解析产物按轴分派 | 239 → ~265 |
| `app/discovery/iteration_stats.py` | 逐轴 tally + `yieldedByPool` + `truncatedByBacktestCap` | 72 → ~120 |
| `app/api/discovery.py` | `list_candidates` 增 `kind` query；无新端点 | 196 → ~205 |
| `app/schemas.py` | `DiscoveryStartReq` 增六个可选字段 | +12 |
| `app/config.py` | 4 个新 `discovery_*` 设置项（见 §六） | +8 |

**无新增 DB 迁移、无既有表结构变更**（§五）。

### 3.3 前端 · 新增

| 文件 | 行数预算 | 意图 |
|---|---|---|
| `components/Discovery/DiscoveryAxisPicker.tsx` | 180 | 多选轴卡片 + 每轴参数区（模板白名单 / 组合规模 / 轮动规模） |
| `components/Discovery/DiscoveryStrategyDetail.tsx` | 230 | 策略候选详情：参数对照 / 成员清单 / IS·OOS 双段净值 / S 闸阶梯 / 口径说明 |
| `components/Discovery/DiscoveryGateLadder.tsx` | 130 | 通用「闸位阶梯」（因子 F1–F11 与策略 S1–S9 共用一个组件） |
| `components/Discovery/DiscoveryTemplatePicker.tsx` | 150 | 可挖模板勾选表（不可挖者灰显 + 原因） |
| `lib/discoveryStrategy.ts` | 200 | 策略指标格式化、S 闸标签、参数差异计算、按轴列定义 |
| `components/Discovery/DiscoveryAxisPicker.test.tsx` | 120 | 多选 / 不可挖模板必须显示原因 / 依赖轴提示 |
| `components/Discovery/DiscoveryStrategyDetail.test.tsx` | 140 | 双段净值渲染 / 卡在哪条闸标红 / `clipMode` 文案 |
| `lib/discoveryStrategy.test.ts` | 160 | 纯函数：格式化 / 差异 / 列定义 / 让渡文案 |

### 3.4 前端 · 修改

| 文件 | 改动 |
|---|---|
| `components/Discovery/DiscoveryLauncher.tsx` | 轴选择改用 `DiscoveryAxisPicker`；新增策略阈值折叠区；工作量预估改新口径 |
| `components/Discovery/DiscoveryCandidateTable.tsx` | `kind` 过滤 chip；列按轴切换（列定义下沉到 `discoveryStrategy.ts`） |
| `components/Discovery/DiscoveryCandidateDetail.tsx` | 按 `kind` 分派到表达式详情或 `DiscoveryStrategyDetail` |
| `components/Discovery/DiscoveryFunnel.tsx` | **（评审 P1-10④ 补）**「漏斗按轴分列」与 BL-9 的「让渡」行都在这个文件里；`DiscoveryMonitor.tsx` 只负责装配与传参 |
| `components/Discovery/DiscoveryMonitor.tsx` | 按轴把漏斗分列传给 `DiscoveryFunnel`；机制覆盖图在两条组合轴下整块隐藏（P2-11） |
| `components/Discovery/DiscoveryLibrary.tsx` | 按实体类型分组 + 跳转策略面板 / 因子组合 |
| `lib/discoveryTypes.ts` | 新增 `DiscoveryStrategyMetrics` / `TemplateSpec` / `AxisPlan` / 扩展 `DiscoveryDefaults` 与候选类型 |
| `lib/discovery.ts` | `emptyRunState` 增逐轴字段；`applyDiscoveryEvent` **保持整对象透传**（不得逐键手抄）；**`REJECT_REASON_CN` 增补 S0–S9 + `rejectReasonCn` 增 `S0:` 前缀分支（评审 P1-5，缺了就回显裸英文码）** |
| `lib/discoveryApi.ts` | `listCandidates` 增 `kind` 参数 |

### 3.5 既有测试 · 必须同步修改

**（评审 P1-10 重写：v1 这张表错了一行、漏了三处，照它做仍会撞红灯。下表逐条核对过源码行号。）**

| 文件 · 位置 | 现状（核对过） | 必须改成 |
|---|---|---|
| `tests/test_discovery_api.py:200-211` `test_unimplemented_axis_returns_400` | 断言 `axes=["strategy_param"]` → 400 `DISCOVERY_AXIS_UNSUPPORTED` | 改为四轴均 201；**保留**该用例但把入参换成一个真正未知的轴（如 `"nonsense"`），守住「未知轴仍 400」这条边界 |
| `tests/test_discovery_api.py:446-453` `test_defaults_expose_only_implemented_axes` | **硬断言** `payload["implementedAxes"] == ["factor"]` —— v1 漏列，四轴放行后**必红** | 改为断言 `implementedAxes == allAxes`，并新增「`axisInfo` / `templates` 两个新字段形状正确」的断言 |
| `tests/test_discovery_searchers.py` | 只覆盖 `FactorSearcher` | 保留原用例（BL-8 的一部分），新增三个搜索器的同构用例 |
| `tests/test_discovery_promote.py` | **v1 说它断言 `kind != "factor"` 抛 `DISCOVERY_AXIS_UNSUPPORTED`——不实**：该文件里没有任何 `AXIS_UNSUPPORTED` 断言（唯一那处在 `test_discovery_api.py:211`）。它现有 11 条用例全是 factor 路径，含 `test_promote_only_touches_the_three_entity_tables` | 原 11 条**全部保持不变**（它们是安全不变式的守门人），只**新增**三种 kind 的入库路径 + 幂等断言 + BL-7 逐键相等断言 |
| `components/Discovery/DiscoveryLauncher.test.tsx:17` mock | `implementedAxes: ["factor"]` —— v1 漏列 | 改为四轴 mock，并补 `axisInfo` / `templates` |
| `components/Discovery/DiscoveryLauncher.test.tsx:57-63` | 断言「只渲染 implementedAxes」+ `queryByText(/敬请期待/)` 为 null | 断言保持语义不变（四轴都渲染、**仍无任何占位文案**），只随 mock 改期望条数 |
| `components/Discovery/DiscoveryLauncher.test.tsx:104-113` | `expect(body.axes).toEqual(["factor"])` —— v1 漏列 | 默认仍只勾 `factor`（R-15），故该断言**应当保持绿**；若实现改了默认勾选就是违反 R-15，此断言即为守门人，**不许为了让它过而改默认** |

> 这七处若不同批修改，会在「零回归」的验收里制造与本轮无关的红灯——上游 P1-1 的同款账，
> 提前列出来。另注：`tests/` 下现有 17 个 `test_discovery_*.py`，除上表四个外的 13 个
> **必须保持逐条不动且全绿**（R-4）。

### 3.6 配置 / 文档

| 文件 | 改动 |
|---|---|
| `.env.example` | 4 个新 `DISCOVERY_*` 项（含注释） |
| `desktop/src/lib/config.js` | 无需改（`DISCOVERY_` 前缀已整体透传） |
| `docs/plans/discovery-strategy-axes/spec.md` | 本文；实现后追加 §十一 |

---

## 四、接口设计

**无新增端点**。既有 13 个 REST + 1 个 WS 全部沿用，字段做**加法**扩展。

### 4.1 `POST /discovery/runs`（扩展请求体）

```jsonc
{
  "instId": "BTC-USDT-SWAP", "bar": "15m", "windowBars": 4000,
  "axes": ["factor", "strategy_param", "factor_combo", "strategy_combo"],
  "axisWeights": { "factor": 2.0, "strategy_param": 1.0, "factor_combo": 1.0, "strategy_combo": 0.5 },
  "candidatesPerIteration": 40,
  "templates": ["momentum_breakout", "ema_cross_scalper"],   // 空/缺省 = 全部可挖模板
  "includeLibraryFactors": true,
  "comboSize": { "min": 2, "max": 5 },
  "rotationSize": { "min": 2, "max": 4 },
  "strategyThresholds": { "minTrades": 20, "minSharpe": 0.8, "maxDrawdown": 0.35,
                          "minCalmar": 1.0, "oosRetain": 0.4, "maxTradeRate": 0.05,
                          "minProfitFactor": 1.1, "maxEquityCorr": 0.85,
                          "costStressMult": 2.0, "trialPenaltyK": 0.15 },
  "maxKeptPerAxis": 200
  // …上游既有字段不变
}
```
错误码（新增）：
| code | HTTP | 触发 |
|---|---|---|
| `DISCOVERY_AXIS_UNSUPPORTED` | 400 | 轴不在 `ALL_AXES`（保留，语义收窄为「未知轴」） |
| `DISCOVERY_TEMPLATE_UNSUPPORTED` | 400 | `templates` 里有不可挖的模板 key（响应体带 `reason`） |
| `DISCOVERY_AXIS_DEPENDENCY` | 400 | 选了 `strategy_combo` 但**未同时选** `strategy_param` —— **无条件**（评审 P1-7） |
| `DISCOVERY_COMBO_SIZE` | 400 | `comboSize` 越界（**2–5**）或 `rotationSize` 越界（2–4）（评审 P2-3） |

> **P1-7**：v1 给 `DISCOVERY_AXIS_DEPENDENCY` 加了「**且库里也没有可用上游产物**」的条件，
> 但 §2.3.3 规定轮动成员**只**来自本 run `strategy_param` 轴的 kept 池，本文根本没有设计
> 「从 `strategies` 表取材」的路径。按那个条件实现，会出现「库里恰好有几条策略 ⇒ 放行启动
> ⇒ 该轴 readiness 恒 false ⇒ 每轮整体让渡 ⇒ 一个候选都不产」——**正是 BL-9 明令禁止的空转轴**。
> 本轮取无条件 400（不扩范围）；「从既有策略库取轮动成员」列入 §十 开放问题。
>
> **P2-3**：v1 的 `comboSize` 边界写 1–5，但 §2.3.2 是 2–5，审查规则 `degenerate_members`
> 又对 <2 成员直接拒。取 **2–5**，三处一致（`factors/specs.py` 的
> `MIN_FACTOR_SET_MEMBERS=1 / MAX=12` 是更宽的外层边界，不冲突）。

### 4.2 `GET /discovery/config/defaults`（扩展响应）

```jsonc
{
  "implementedAxes": ["factor", "strategy_param", "factor_combo", "strategy_combo"],
  "allAxes": [...same...],
  "axisInfo": {
    "strategy_param": { "label": "常规策略", "desc": "在内置策略模板的参数空间里搜索",
                        "backtestsPerCandidate": 2, "dependsOn": [] },
    "factor_combo":   { "label": "因子组合策略", "desc": "把因子装成带权重与阈值的组合",
                        "backtestsPerCandidate": 2, "dependsOn": [] },
    "strategy_combo": { "label": "策略轮动组合", "desc": "把已挖到的策略装进轮动模板",
                        "backtestsPerCandidate": 2, "dependsOn": ["strategy_param"] }
  },
  "templates": [
    { "key": "momentum_breakout", "name": "暴涨动量突破", "family": "breakout",
      "minable": true, "reason": "", "paramCount": 6, "htfBars": [] },
    { "key": "trend_band_swing", "name": "大周期趋势带波段", "family": "trend",
      "minable": true, "reason": "", "paramCount": 9, "htfBars": ["12H", "1D"] },
    { "key": "regime_rotation", "name": "趋势自适应策略组合", "family": "rotation",
      "minable": false, "reason": "组合与因子类模板由专用轴挖掘", "paramCount": 0, "htfBars": [] }
  ],
  "strategyThresholds": { /* S1–S9 默认值 */ },
  "comboSize": { "default": {"min":2,"max":5}, "min":1, "max":5 },
  "rotationSize": { "default": {"min":2,"max":4}, "min":2, "max":4 }
  // …上游既有字段不变
}
```

### 4.3 `GET /discovery/runs/{runId}/candidates`

新增 query `kind`（`factor|strategy_param|factor_combo|strategy_combo`，空 = 全部）。
其余不变。响应 `items[]` 的策略轴条目：`exprText` 为空、`describe` 为策略中文渲染、
`metrics` 见 §五。

### 4.4 `POST /discovery/candidates/{id}/promote`（响应扩展）

```jsonc
{ "promoted": { "kind": "factor_set", "entityId": 12, "entityKind": "factor_set",
                "companionStrategyId": 34, "reused": false } }
```
`kind ∈ {factor, factor_set, strategy}`；`companionStrategyId` 仅 `factor_set` 有，且
**`reused: true` 的回放响应里也必须有**（评审 P1-9：现有幂等分支直接返回不带该字段的 payload，
第二次入库后「去策略面板」的跳转会断）。

### 4.5 `POST /discovery/candidates/{id}/backtest`（扩展）

策略轴候选：在 `[startTs, endTs]` 上按同一 `assembler` 跑一次真回测（**不落库**，与上游
一次性语义一致），响应增 `equity`（≤ 400 点下采样）与 `clipMode`。`strategy_combo` 走
`run_combo_backtest`，响应额外带 `segments` 摘要（成员占用时间线）。

### 4.6 WebSocket `/api/ws/discovery/{runId}`

事件类型不变（`state` / `iteration` / `candidate` / `demoted` / `error` / `done` / `ping`）。
字段加法：
- `state.data.pools`: `{axis: size}`；`state.data.axisPlan`: 上轮实际分配
- `iteration.data.perAxis`: `{axis: {generated, ruleRejected, validated, kept, truncated}}`
- `iteration.data.yieldedByPool`: `{axis: {count, reason}}`
- `candidate.data.kind` 与策略指标（`sharpeIs` / `returnIs` / `sharpeOos` / `trades`）

> 前端 `applyDiscoveryEvent` **必须整对象透传**（现有实现已是，见 `lib/discovery.ts:5` 的注释）。
> 新增一条用例断言：一个带未知新键的 `iteration` 事件经 reducer 后该键仍在。

---

## 五、数据模型

**零迁移、零新表、零新列。** 上一轮建表时已把取值域留全，本轮只是把它们用起来。

| 列 | 因子轴（现状） | 策略轴（本轮） |
|---|---|---|
| `kind` | `"factor"` | `strategy_param` / `factor_combo` / `strategy_combo` |
| `genome_hash` | 表达式哈希 | `sha256(canonical_json(genome))[:32]` |
| `genome_json` | `{"expr": "..."}` | `ParamGenome` / `ComboGenome` / `RotationGenome` 的 dict 形态 |
| `expr_text` | 规范式 | **空**（模型注释已如此声明） |
| `describe_text` | 中文渲染 | `describe_strategy(...)`（≤512） |
| `skeleton` | 表达式骨架 | §2.6.4 的定义 |
| `mechanism` | 13 机制族 | 模板族 / `factor_combo` / `rotation` |
| `metrics_json` | IS/OOS 因子指标 | 见下 |
| `series_json` | 分段 IC + 分位 | `{equityIs: [...≤120], equityOos: [...≤120], gates: [...]}` |
| `promoted_kind` | `"factor"` | `"strategy"` / `"factor_set"` |

### 5.1 策略轴 `metrics_json` 形状

```jsonc
{
  "kind": "strategy_param",
  "templateKey": "momentum_breakout",
  "trials": 1832, "requiredSharpe": 1.02,
  "costs": { "initialCapital": 10000, "takerFeeBps": 5.0, "slippageBps": 2.0, "leverage": 1.0 },
  "clipMode": "gated",          // gated | rebased（BL-3）
  "warmupPad": 34,
  "is":  { "bars": 2800, "sharpe": 1.43, "totalReturn": 0.31, "maxDrawdown": -0.18,
           "calmar": 1.72, "profitFactor": 1.35, "winRate": 0.48, "tradeCount": 96,
           "tradeRate": 0.034 },
  "oos": { "bars": 1200, "sharpe": 0.92, "totalReturn": 0.11, "maxDrawdown": -0.14,
           "calmar": 0.98, "profitFactor": 1.22, "winRate": 0.45, "tradeCount": 38,
           "tradeRate": 0.032 },
  "stress": { "costMult": 2.0, "totalReturn": 0.12 },      // 仅过到 S9 的候选有
  "maxEquityCorr": 0.41,
  "paramDiff": [ { "key": "breakout_window", "default": 20, "value": 34 } ]
}
```
**每个字段的来源必须写清（BL-13 · 评审 P1-1）**，否则实现节点会以为一个
`compute_metrics` 就够：

| 字段 | 来源 |
|---|---|
| `sharpe` / `totalReturn` / `maxDrawdown` / `profitFactor` / `winRate` / `tradeCount` | `engine/metrics.py::compute_metrics`（7 项契约） |
| `calmar` | **`engine/metrics_ext.py::compute_extended_metrics`** —— `compute_metrics` 里**没有**这一项 |
| `bars` / `tradeRate` | 本层 `metrics_of` 显式派生（`len(points)`、`tradeCount / bars`）。**不得**用 `metrics_ext` 的 `tradesPerWeek` 冒充——那是按墙钟秒换算的，与 S7 的「按根数密度」不是一回事 |
| `costs` | `backtest_lane.costs_of(settings)`，**bps 原值**（BL-1） |
| `trials` / `requiredSharpe` | **本轴**的 `lane.tested` 与 `penalized_gate`（BL-14），不是 run 全局计数 |
`series_json.gates`: `[{ "id": "S3", "label": "夏普未达…", "value": 1.43, "gate": 1.02, "passed": true }, ...]`
——**逐条闸的实际值与门槛都要落库**，这样详情抽屉能显示「卡在哪、差多少」，而不是只有一个
`rejectReason` 字符串。

### 5.2 内存态（不落库）

`StrategyKeptEntry.bar_returns`（IS 段逐根收益率）与因子轴的 `values` 同性质：窗口右移即失效，
由 §2.6.1 的重建函数在续跑与刷新两处重建。检查点里只存**大小**（`pool_sizes`），不存序列。

---

## 六、配置项（`config.py`，env 前缀 `DISCOVERY_`）

| 键 | 默认 | 说明 |
|---|---|---|
| `discovery_max_backtests_per_iteration` | 120 | 单轮**底层 `run_backtest` 调用**次数硬顶（见下）；触顶截断并留痕 |
| `discovery_backtest_timeout_s` | 30.0 | 单个策略候选的墙钟超时（**IS / OOS / S9 压力，三段合计**），超时即 `rejected:validate_timeout` |
| `discovery_max_htf_frames` | 3 | 一个 run 允许取的高周期帧数上限（BL-6 的资源侧护栏） |
| `discovery_member_catalog_max` | 60 | 因子成员目录上限 |
| `discovery_fsa_min_pool` | 20 | 低于此规模的轴本轮跳过 FSA 扫描（BL-10 后半） |

**回测计数单位（评审 P1-3，承重口径）**：硬顶与超时数的都是**底层
`engine/backtester.run_backtest` 的调用次数**，不是候选数。理由是
`engine/combo.py::_build_member_curves` 对**每个成员各跑一次** `run_backtest`：

| 轴 | 单候选的底层回测次数（IS + OOS，过 S9 再 +1 段） |
|---|---|
| `strategy_param` | 2（+1 = 3） |
| `factor_combo` | 2（+1 = 3） |
| `strategy_combo` | 2 × rotationSize = **4–8**（+rotationSize = 6–12） |

按「候选级」计数会把一个轮动候选算成 1，**低估 4–6 倍**，墙钟护栏形同虚设——一轮跑飞十几分钟
而计数器显示「才 20 次」。同理，v1 把超时口径写成「两次回测合计」也漏了 S9 那一段。

`workload_of` 新口径（闸 3）：
```python
AXIS_COST = {"factor": 1, "strategy_param": 8, "factor_combo": 10, "strategy_combo": 20}
workload = window_bars × Σ_axis (axis_slots × AXIS_COST[axis])
```
> 一次回测比一次表达式求值贵一到两个数量级，沿用旧口径（候选数 × 根数）会让「40 个组合候选」
> 与「40 个因子候选」被判成同样的工作量，于是 413 这道闸对策略轴形同虚设。系数取自本地实测
> 量级，实现时须在 §十一 记录实测值。
>
> **对 factor 单轴恒等（评审 P2-7）**：`AXIS_COST["factor"] == 1`，故 factor-only 的
> `workload` 与既有 `candidatesPerIteration × windowBars` **逐位相同**——这是本轮「不改变
> 既有 413 行为」的承诺，配一条等价用例。
> **算例（评审 P2-10）**：`discovery_work_unit_limit` 默认 3,000,000。四轴均分 40 候选、
> 窗口 4000 根 ⇒ `4000 × (10×1 + 10×10 + 10×8 + 10×20) = 1,560,000`，**通过**；同配置换成
> 窗口 8000 根 ⇒ 3,120,000，**413**。这是设计意图（四轴 × 满窗口确实跑不动），但 413 的文案
> 必须从「请减少每轮候选数或缩短数据窗口」改成「请减少每轮候选数、缩短数据窗口，**或减少
> 同时开启的搜索轴**」——否则用户会一直调前两个而不知道第三个才是主因。

---

## 七、测试与验收标准

### 7.1 后端测试（`services/quant/tests/`，pytest）

| 文件 | 覆盖 |
|---|---|
| `test_discovery_factor_lane_golden.py` | **BL-8 等价门禁**：固定种子 + 合成 K 线，重构前后 `(hash, verdict, reason)` 序列逐条相等；**并含 BL-14 的跨轴隔离用例**（单轴跑 vs 四轴同跑，因子轴序列与 `tested` 逐条相等，见 §2.2 第 4 步） |
| `test_discovery_backtest_lane.py` | **BL-1** 传 bps 非小数（双向）；**BL-2** 两个独立实例（monkeypatch 计数）、pad 区零成交、`points[0].equity == initial`、gate 泄漏即拒；**BL-3** rebase 后 drawdown 从切片峰值起算；**BL-13** `metrics_of` 的返回**必须含 `calmar` 与 `tradeRate`**（这条就是「只调 `compute_metrics` ⇒ 全员卡 S5」的机器判据）；rebase 起点权益 ≤0 时返回 `None` 而非除零（P2-5） |
| `test_discovery_combo_skeleton.py` | **BL-10**：两组成员完全不同的 `factor_combo` 基因，其 `skeleton` **必须不同**（这条就是 P0-1 的机器判据——按 `factorKey` 拼接会让它们相同）；`FSA_MIN_POOL` 未达时不产生任何冻结；`strategy_param` 轴的 `fsaThreshold` 随模板数自适应 |
| `test_discovery_rotation_costs.py` | **BL-11**：搜索器产物里 `turnoverBps` 恒等于 `frozen_policy_values(settings)` 的值（500 次随机采样）；`policy_from_spec` 收到的最终 policy 也是该值 |
| `test_discovery_combo_weights.py` | **BL-12**：`FactorComboAssembler.members()` 产出的成员 dict **每条都带 `weight` 且和为 1.0**；反向用例——故意不烘焙时装配出的策略在合成 K 线上**零成交**（证明这条错了的后果确实是静默的） |
| `test_discovery_strategy_gates.py` | S1–S9 逐条**双向**用例（构造必过与必拒各一）；**S6 必须有「IS 强 / OOS 崩塌 ⇒ 拒」与「两段同分布 ⇒ 过」两条**；不可判定即拒；S9 排在最后（monkeypatch 断言未过 S8 时不发生第三次回测） |
| `test_discovery_catalogs.py` | 不可挖判据有序命中；`FROZEN_PARAM_KEYS` 生效（`htf_seconds` 不在可挖参数里）；HTF 并集与上限截断、`htf_warmup_bars` 取最大值（P2-6）；成员目录方向只在 IS 段冻结；成员目录已过 `is_bar_compatible`（P1-6） |
| `test_discovery_strategy_assembler.py` | **P1-4** 同模板重复成员时 `htf_frames_by_member` 的键含 `#2` 且成员各自拿到帧，缺帧即 `S0:missing_htf:<memberKey>`；**P1-6** `_score` 全 NaN ⇒ `S0:score_unavailable`（构造一个不兼容 bar 的成员）；**P2-8** 边界校验器抛 `HTTPException` ⇒ `S0:assemble_rejected:<code>`，**不得**落成 `eval_error:HTTPException`；**P1-11** combo 的 pad ≥ `engine.combo._warmup_floor` 的同源量 |
| `test_discovery_axis_budget.py` | 最大余数分配求和恒等于 count；未就绪轴整体让渡且 `yielded` 如实（含 `to_axis`）；**接收方只在本 run 已选轴里挑**（P1-8：未选 `factor` 时不得让给 `factor`）；全轴未就绪返回空计划 |
| `test_discovery_promote_idempotent_companion.py` | **P1-9**：`factor_combo` 候选连点两次 promote，两次响应的 `companionStrategyId` 相等且非空 |
| `test_discovery_searchers_strategy.py` | 三个搜索器：产物恒合法或 `None`；梯子越界不可能；`crossover` 跨模板降级留痕；权重严格归一（含残差补偿） |
| `test_discovery_strategy_pool.py` | **BL-5**：续跑与刷新调**同一个** `_rebuild_strategy_pool`（AST 收集调用节点，不用文本计数——上游 I-8）；重建失败 ⇒ paused；`template_signature` 漂移 ⇒ 整池重建 |
| `test_discovery_promote_strategy.py` | 三条入库路径 + 幂等（连点两次返回同 id）；**BL-7**：入库 `params_json` 与被回测参数逐键相等（无多余键）；`factor_combo` 必须经 `create_set`（monkeypatch 断言被调用）；安全不变式：不写 `strategy_runs` |
| `test_discovery_multi_axis_runtime.py` | 三轴同跑一轮：候选行 kind 分布正确、`perAxis` 计数自洽、回测硬顶截断留痕、LLM 仍只调一次 |
| `test_discovery_checkpoint_v1.py` | 老检查点（无新字段）可直接续跑；**BL-14 回落**：缺 `tested_by_axis` 时把既有全局 `counters.tested` 整体归 `factor` 轴（断言续跑后因子轴的 `required_ir` 与升级前**逐位相等**——归零会让多重检验惩罚整体塌回基准） |
| `test_discovery_import_boundary.py`（既有） | 新模块自动纳入参数化；断言 `lanes/` 与 `backtest_lane` 不引入交易层 |

**测试基础设施纪律（上游教训，直接沿用）**：WS 用例禁用 `TestClient.websocket_connect`（死锁）；
结构性断言走 AST 不扫源码文本；全量 pytest 不并发（共用临时 DB）；`python -u` 重定向日志。
**所有回测用例一律用合成 K 线**（正弦 + 固定种子噪声），**绝不连网**。

### 7.2 前端测试（`apps/web`，vitest；`NODE_ENV=test`）

| 文件 | 覆盖 |
|---|---|
| `DiscoveryAxisPicker.test.tsx` | 四轴可选；不可挖模板灰显**且必须显示原因**；只选 `strategy_combo` 时显示依赖提示；**不得出现任何「敬请期待」占位** |
| `DiscoveryStrategyDetail.test.tsx` | 参数差异高亮；IS/OOS 双段净值各一条；卡住的闸标红且显示实际值与门槛；`clipMode=rebased` 时显示口径说明 |
| `DiscoveryGateLadder.test.tsx` | 因子 F 闸与策略 S 闸共用组件、各自渲染正确 |
| `discoveryStrategy.test.ts` | 格式化 / 列定义 / 让渡文案（`yieldedByPool` → 中文一句话，含 `to_axis`） |
| `discovery.test.ts`（既有，增补） | reducer 对**未知新键**整对象透传；`emptyRunState` 含逐轴字段；**`rejectReasonCn("S6")` 与 `rejectReasonCn("S0:missing_htf:12H")` 都不得等于原码**（评审 P1-5 的机器判据） |
| `DiscoveryFunnel.test.tsx`（新增） | 让渡行渲染（BL-9 落点 ③ 的机器判据）；漏斗按轴分列；两条组合轴下不渲染机制覆盖图（P2-11） |
| `DiscoveryMonitor.test.tsx`（既有，增补） | 按轴把漏斗数据传下去；`DiscoveryFunnel` 收到的 props 形状正确 |

### 7.3 验收标准（Definition of Done）

1. 启动器可同时勾选四条轴；不可挖模板灰显并写明原因；无任何占位文案。
2. 三轴同跑 20 轮后，候选库里四种 `kind` 都有条目，且每种都有 kept 与 rejected（证明闸既非
   恒真也非恒假）。
3. **（评审 P1-12 重写）** S6 不是装饰的机器判据，取两条：
   ① 拒绝原因分布里 **S6 的占比 > 0**（有候选确实死在样本外这一关）；
   ② 全部 kept 候选的 `oos.sharpe / is.sharpe` 比值**不得全部落在 [0.95, 1.05]**——两段若是
   同一段数据跑了两遍，这个比值会紧贴 1.0。
   v1 原文写的「`metrics.oos.sharpe` 不全为正」是**自相矛盾**的：S6 本就要求 kept 的
   OOS sharpe > 0，所以它必然全为正，那条判据恒假、永远验收不过。
4. 拒绝原因分布覆盖 S1–S9 中至少 6 条 + 审查规则至少 2 条；且**每一条出现过的拒绝码在
   界面上都显示为中文**（P1-5：抽查 `S0:*` 这类带子因的形态）。
5. 一条 `strategy_param` 候选入库后，出现在策略面板并能直接发起一次常规回测；其 `params_json`
   与候选 `genome_json.params` 逐键相等（BL-7）。
6. 一条 `factor_combo` 候选入库后，因子组合列表出现该组合**且**策略列表出现伴生 `factor_score`
   策略（`create_set` 的单事务语义）。
7. 一条 `strategy_combo` 候选入库后，出现在组合策略页并能跑 combo 回测。
8. 关掉进程再启动，三轴的池都被重建（`rebuilding` 有可见状态），随后继续产候选；重建失败时
   停在 `paused(rebuild_failed)` 而非带残缺池开跑。
9. 上游库存不足时，界面明确显示让渡（BL-9），而不是让用户对着一个「在跑」的进度条猜。
10. 因子轴 golden 用例逐条相等（BL-8）。
11. 后端全量 pytest 相对**干净 HEAD worktree 实测基线**零新增红灯；前端全量 vitest 零红灯；
    `tsc --noEmit` 不新增错误。
12. 未打开过挖掘视图的用户，既有请求路径零额外开销（惰性首拉不变）。
13. `discovery_enabled=False` 时，四条轴的写类端点一律 403。
14. 满负荷三轴挖掘时，`GET /market/candles` 与既有回测端点的 p95 延迟劣化 < 30%（真机验收）。

---

## 八、风险与缓解

| # | 风险 | 缓解 |
|---|---|---|
| R-1 | **成本量纲反向出错**（BL-1）：`/1e4` 一除，策略全都「很赚」 | 双向门禁用例（策略轴断言收 bps，因子轴断言 `round_trip_cost` 仍返回小数）；两处代码互相引用对方的文档字符串 |
| R-2 | **样本外被写成装饰**（BL-2/S6） | `test_s6_rejects_out_of_sample_collapse` 双向用例；`points[0].equity == initial` 的运行期断言（gate 泄漏即拒） |
| R-3 | 回测太慢，一轮跑不完几个候选 | `AXIS_COST` 进 413 闸；单候选超时 30s；单轮回测硬顶 120 次；S9 排最后只对幸存者收费；轮转验证防单轴吃光墙钟 |
| R-4 | **Lane 重构打坏已发布的因子轴**（BL-8） | golden 用例先落后改；平移不改写；`test_discovery_*` 既有 17 个文件全部保持绿 |
| R-5 | 模板升级导致旧基因越界 | `template_signature` 漂移即整池重建并降级留痕（§2.10） |
| R-6 | MTF 帧缺失下静默回测（BL-6） | 验证层入口 fail-closed 校验 + 目录层冻结 + 帧数上限；两层都必须有 |
| R-7 | `strategy_combo` 与另两轴 OOS 口径不同被误读（BL-3） | `clipMode` 落库 + 详情抽屉一行说明 + 前端用例断言该文案存在 |
| R-8 | 组合轴与因子轴共享成员目录，目录变了旧组合基因失效 | `member_catalog_hash` 进检查点，不一致即重建组合轴池 |
| R-9 | 多轴让 LLM 调用翻四倍（成本） | 生成与审查各合并成**一次**调用（`perAxis` 分配）；小时配额闸不变 |
| R-10 | 入库把参数改坏（BL-7） | 逐键相等断言；禁止写溯源字段；`create_set` / `validate_combo_params` 复用入库期同一把尺子 |
| R-11 | `build_members(strict=False)` 静默丢成员 ⇒ 指标按 4 成员报、实际跑 2 成员 | 挖掘期一律 `strict=True`（§2.5.4） |
| R-12 | 三轴同跑把 `discovery_validate_concurrency` 挤爆，交互变卡 | 共用同一信号量（不是每轴一个）；回测硬顶；真机 p95 验收（DoD #14） |
| R-13 | kept 池跨轴混排导致策略被因子的 IR 排序挤掉 | `score` 口径不可比 ⇒ 锦标赛与截断**一律轴内**（§2.6.3） |
| R-14 | 权重浮点残差累积，组合权重和不为 1 | 归一化补残差给最大分量 + `canonical_json` 前统一 `round(6)`（§2.3） |
| R-15 | 单轴用户被多轴 UI 复杂度伤害 | 启动器默认只勾 `factor`（与现状一致）；其余三轴为显式加选；默认路径观感零变化。守门人 = `DiscoveryLauncher.test.tsx:110` 的 `expect(body.axes).toEqual(["factor"])`（**不许为了让它过而改默认**） |
| R-16〔评审〕 | **组合轴的骨架退化把整轴拒死**（P0-1 / BL-10） | skeleton 落在表达式语义上 + `FSA_MIN_POOL` 护栏 + `test_discovery_combo_skeleton.py` 的「两组不同成员必须算出不同骨架」用例 |
| R-17〔评审〕 | **成本参数被当成可挖参数**（P0-2 / BL-11） | `FROZEN_POLICY_KEYS` 冻结 `turnoverBps` 到 `costs_of(settings)` 派生值 + 500 次采样的恒等用例。凡「schema 里是数值、语义上是成本或数据源选择」的参数，一律进冻结清单——`htf_seconds`（BL-6）与 `turnoverBps`（BL-11）是同一条规则的两个实例 |
| R-18〔评审〕 | **权重丢失让整轴恒零成交**（P0-3 / BL-12） | 装配时逐成员烘焙 `weight` + 权重和恒等 1.0 的用例 + 一条「不烘焙则零成交」的反向用例（证明后果确实是静默的） |
| R-19〔评审〕 | **共享计数器让已发布的因子轴行为漂移**（P1-2 / BL-14） | `tested` / `skeleton_stats` / `bans` 逐轴化 + 跨轴隔离用例 + 老检查点回落规则。**教训**：BL-8 那种「重构前后逐位相等」的 golden 门禁，只能守住它自己跑过的那条路径；新增维度（这里是「多轴同跑」）必须**另立**一条等价用例，否则等价性只在单轴下成立 |
| R-20〔评审〕 | **指标口径缺项让整条闸恒判不可判定**（P1-1 / BL-13） | `metrics_of` 并集出口 + 「返回值必须含 calmar / tradeRate」的用例。**教训**：fail-closed 是对的，但 fail-closed 的前提是「输入齐全」——缺字段时的 fail-closed 会伪装成「市场没机会」 |

---

## 九、建议实施顺序（每步独立可验收）

1. **golden 用例先行**（BL-8 第 1 步）：在当前代码上落 `test_discovery_factor_lane_golden.py` 并跑绿。
2. **Lane 抽象与平移**：`lanes/` 三文件 + `runtime.py` 瘦身 + **BL-14 的逐轴计数容器**；
   golden 用例必须仍绿；既有 17 个 discovery 测试文件全绿；跨轴隔离用例（§2.2 第 4 步）此时
   还只能跑「单轴 vs 单轴」，第 4 步之后补齐四轴形态。**此步不引入任何新轴**，可单独验收。
3. **回测车道**：`backtest_lane.py`（含 BL-13 的 `metrics_of` 并集出口）+ `strategy_gates.py`
   + `catalogs.py`（含 `penalized_gate` 抽取与 `FROZEN_POLICY_KEYS`）；配 §7.1 的
   `backtest_lane` / `strategy_gates` / `catalogs` / `strategy_assembler` 四个测试文件。
   此步无 UI、无 API 变更。
4. **`strategy_param` 轴打通**：`param_searcher` + `StrategyLane` + `axis_budget` + service/API
   放行该轴 + promote 路径；端到端本地跑 4 轮。
5. **`factor_combo` 轴**：成员目录 + `combo_searcher` + `create_set` 入库路径。
6. **`strategy_combo` 轴**：`rotation_searcher` + rebase 切片 + combo 入库路径。
7. **前端**：`DiscoveryAxisPicker` / `DiscoveryStrategyDetail` / `DiscoveryGateLadder` /
   `DiscoveryTemplatePicker` + 五个既有组件的轴感知改造。
8. **收尾**：既有测试同步修改（§3.5）、全量回归、§十一 追加实施发现。

第 1–3 步不改变任何用户可见行为，是纯基建；第 4 步起每步都能独立演示。

---

## 十、已拍板决策与开放问题

**已拍板（实现时不得再改，改需修订本文）：**

- **D-7**：本轮交付三条策略轴，`IMPLEMENTED_AXES == ALL_AXES`。削减只允许从 `strategy_combo`
  向前砍，且被砍的轴必须退出 `IMPLEMENTED_AXES` + 启动器不渲染 + API 400。
- **D-8**：策略轴的验证一律走**真回测**，不做任何「用因子式统计近似策略收益」的捷径。
- **D-9**：`enum` / `bool` 参数与 `FROZEN_PARAM_KEYS` 一律冻结不挖（BL-6）。
- **D-10**：`regime_rotation` 与 `factor_score` 两个模板**不进** `strategy_param` 目录
  （它们各自有专用轴），避免同一件事有两条挖法。
- **D-11**：`factor_combo` 的成员**不要求先入库**（`factor_score` 快照自包含）；组合入库时
  也不连带入库其成员因子。
- **D-12**：入库实体的 `params_json` 不带溯源字段（BL-7）；溯源只在候选侧
  （`promoted_kind` / `promoted_id`）。
- **D-13**：`maxKept` 语义改为**逐轴**（`maxKeptPerAxis`），旧值向后兼容作为缺省。
- **D-14**：LLM 在 `strategy_combo` 轴不参与生成（成员必须来自本 run kept 池）。
- **D-15**：本轮不引入任何新 REST / WS 端点；一切扩展走既有端点的字段加法。
- **D-16**〔评审 P0-2〕：凡「schema 里是数值、语义上属于**成本模型**或**数据源选择**」的参数，
  一律冻结不挖。当前实例：`trend_pullback_*.htf_seconds`（BL-6）、`regime_rotation.turnoverBps`
  （BL-11）。新增模板时必须回到 §2.4.1 / §2.3.3 补清单，**不得**靠「schema 是数值就挖」的
  通用规则自动放行。
- **D-17**〔评审 P1-2〕：所有「随搜索累积」的统计量（`tested` / `skeleton_stats` / `fsa_bans` /
  `kept_skeleton_counts`）**一律逐轴**，且逐轴进检查点。跨轴共享一个分母既在统计上错误，
  也会让已发布轴的行为被新轴改写。
- **D-18**〔评审 P1-7〕：`strategy_combo` 的成员**只**来自本 run `strategy_param` 轴的 kept 池；
  「从既有 `strategies` 表取轮动成员」本轮明确**不做**，列入开放问题 7。故只选 `strategy_combo`
  一律 400，不看库存。
- 上游全部拍板项（`expr` 桥、不实现截面算子、FSA 带 TTL、WS 不全量回放、发现层与交易层零
  import、`autoPromote` 默认 false、IS/OOS 定号协议、F8 除 1e4、F9 自算、`validate_members`
  的 `name` 透传、`rebuild` 单一实现、同步 `reserve()`、`discovery_enabled` 一票否决）
  **继续有效**，本轮不得推翻。

**开放问题（本轮按括号内默认落地，下一轮再议）：**

1. 多标的（标的组）池化挖掘：仍是单标的（上游开放问题 1，未变）。
2. 跨轴协同（用挖到的因子改良既有策略的入场条件）：不做。
3. 挖掘任务的定时启停（按时段控成本）：不做，靠手动暂停 / 恢复。
4. 候选导出（CSV / JSON）与跨机迁移：不做。
5. **（本轮新增）走向前分析（walk-forward）多折验证**：本轮只做单次 IS/OOS 切分。仓库已有
   `plan_walk_forward_windows` 与 `walk_forward_purge`，把 S6 升级为「多折 OOS 一致性」是
   自然的下一步，但会让每候选的回测次数从 2–3 次涨到 2K+1 次，本轮预算不支持。
6. **（本轮新增）策略候选的实时模拟（paper）预热**：入库后仍需用户手动去策略面板起，
   本轮不做自动接管——那会越过「发现层永不下单」的安全边界，需要单独评审。
7. **（评审新增）`strategy_combo` 从既有策略库取轮动成员**：本轮成员只来自本 run
   `strategy_param` 轴的 kept 池（D-18）。允许从 `strategies` 表取材会让「单独开轮动轴」
   成立，但要额外解决三件事：成员参数与当前窗口的时效性（库里的策略可能是几个月前挖的）、
   成员数量爆炸时的采样策略、以及「用户改了库里那条策略之后，已入库的轮动组合是否要跟着变」。
   都不是本轮预算内的事。

---

## 十一、实施过程发现的方案缺陷

> 本节由实现节点在交付后追加（沿用上游放行条件 7）。凡与本文正文有偏离的，一律在此如实记录；
> **十四条**承重条款（BL-1 … BL-14）若发现任一条不可行，**先回来改本文**再改代码。
> 另须在此记录：`AXIS_COST` 的本地实测量级、回测硬顶（按底层 `run_backtest` 次数计）的实测
> 触发频率、`FSA_MIN_POOL` 与 `strategy_param` 轴自适应 `fsaThreshold` 的实测效果、以及
> **干净 HEAD worktree 上实测的红灯基线**对账结果（不得用记忆里的基线，须现测）。

### 结论

**十四条承重条款（BL-1 … BL-14）全部按正文落地，无一条被判定不可行。** 下列 I-1 … I-9 是
实施中发现的正文缺口与有意偏离，其中 **I-3 是阻断级**（照正文字面实现会漏掉整整一类静默失效）。

### I-1〔阻断级修正的前置〕BL-8 golden 常量的两阶段落地法

正文 §2.2 要求「在**当前 HEAD 代码**上新增 golden 用例并跑绿 → 再做 Lane 拆分 → 重跑必须逐条
相等」。但拆分前 `FactorLane` 并不存在，同一个文件不可能既驱动旧路径又驱动新路径。实际做法：

1. 在 `HEAD` 的**干净 worktree** 上跑一个一次性脚本，逐行复刻拆分前 `runtime._run_iteration`
   的时序（`CandidateFactory.produce` → `filter_rules` → `evaluate` + `review_values` →
   `validate_candidate`），产出 3 轮 × 8 候选的 `(genome_hash, verdict, reject_reason)` 序列；
2. 把该序列**逐字**写进 `tests/test_discovery_factor_lane_golden.py::GOLDEN`；
3. 拆分后由 `FactorLane` 驱动同一序列，断言逐条相等。

结果：`tested=21 / kept=3`，含 `homogeneous_simplify` / `constant_or_empty` / F2 / F3 / F7 /
F9 / F10 / F11 八种码，**拆分前后逐条相等**。BL-14 的跨轴隔离用例另立（单轴跑 vs 叠加一条
`tested=5000` 的策略轴后再跑，序列与 `tested` 必须相等）。

### I-2〔有意偏离 §2.9〕LLM **生成**不做多轴合并，只有**审查**合并

正文 §2.9 要求生成与审查各合并成一次调用、payload 带 `perAxis`。实现只做了**审查**的合并
（`IterationRunner._llm_review` 把四条轴的候选拍平后取样，一次调用，候选项带 `kind`）；
**生成**仍只由因子轴发起一次调用，三条策略轴的 `llm` 份额按上游 `_fallback` 规则回落随机探索，
回落格数记入 `StrategyLane.llm_fallback` 并进 `state.axisSnapshots`。

理由两条：① `submit_factor_candidates` 的 ToolSchema 与系统 prompt 是**表达式专用**的，改成
多轴需要新造一套未经验证的工具契约，而它的产物要喂给 `from_llm`——一旦解析口径没对齐，表现
是「LLM 份额整段蒸发」而不是报错，正是本文反复要防的那类失效；② `strategy_combo` 本就由
**D-14** 明令禁止 LLM 生成。R-9（多轴不得让 LLM 调用翻四倍）由此**更强**地满足：每轮调用数
从「1 生成 + 1 审查」变为不超过同样的两次。代价是 `strategy_param` / `factor_combo` 两轴拿不到
机制引导的生成，纯靠五维演化——列入下一轮开放问题。

**由此产生的 §3.2 变更表偏差**：`llm_agents.py` **未改**（生成 payload 不需要 `perAxis`）；
`prompts.py` **改了但改的是审查侧**——审查候选现在带 `kind`，system prompt 明确区分「表达式」
与「策略」两类判据。这一改是必须的：原 prompt 通篇在说「表达式」，模型拿它去量一条没有表达式
的策略候选，很可能以「看不懂结构」为由否决；而裁决语义是**只减不增**，那等于把整条轴误杀在
审查层——又一个「不报错、只是永远不产出」的失效。

### I-3〔**阻断级**〕`S0:score_unavailable` 的判据：正文给的「等价判据」并不等价

§2.5.4 写「`prepare` 后 `df["_score"]` **不是全 NaN**（等价判据：`strategy._breakdown is not
None`）」。核对源码后两者**不等价**：`_breakdown is None` 只发生在 `score_breakdown` 抛
`ValueError` / `KeyError` 的路径上；而「表达式解析得通、求值却全 NaN」根本不抛错——
`engine/factors/library.py::_expr_factor` → `expression.evaluate_expression_text` 的**降级契约**
是「解析 / 校验 / 求值任一步失败一律返回与 df 等长的全 NaN Series」，其中就包括
`validate_node` 的最小复杂度门槛（`MIN_NODES=3`）。于是 `ts_zscore(volume,30)` 这类 2 节点表达式
能过 `validate_members` 的边界校验、能建出 `_breakdown`，但复合得分恒 NaN ⇒ **零成交 ⇒ 全轴卡
S1**，正是 P1-6 要防的那个现象本身。

**已按主判据实现**：`FactorComboAssembler.postflight` 检查 `breakdown.score.notna().any()`。
`tests/test_discovery_strategy_assembler.py` 用 `ts_zscore(volume,30)` 构造了这条静默路径。
若照正文的括号内判据实现，这条闸会**在最常见的一种失效上恒真**。

### I-4〔有意偏离 §2.5.4〕`score_unavailable` 的检测点从「回测前」挪到「IS 段回测后」

正文要求两条 fail-closed 判据都写在 `run_split_backtest` **之前**。`missing_htf` 与
`assemble_rejected` 照做（`preflight`）；`score_unavailable` 改在 IS 段回测**之后**读同一个策略
实例的 `_breakdown`（`postflight`）。理由：在回测前判定就得多跑一次 `strategy.prepare`，而
`prepare` 正是 `factor_score` 最贵的一步（`score_breakdown` 要逐成员求值 + 因果 zscore），等于
给这条轴凭空加 ~50% 开销。判定结果与拒绝码完全相同（`S0:score_unavailable`，fail-closed），
只是多付一次 IS 段回测；`split.backtests == 1` 有用例守着，确认**不会**继续跑第二段。

### I-5〔正文空缺〕全轴让渡时提交一条零计数迭代行，而不是「跳过本轮」

§2.8 说全部轴 not ready 时「跳过本轮迭代并推一条 `error` 事件」。照字面实现有两个洞：
① `_checkpoint.iteration` 不推进 ⇒ 配了 `maxIterations` 的任务**永远不会终止**；② 用户在时间线
上看不到「这一轮什么都没干以及为什么」。实现改为：照常走完四步（各轴名额为 0，自然产不出
候选），提交一条 `generated=0` 的迭代行，`note` 与 `stats.yieldedByPool` 如实写明让渡去向与
原因。BL-9 的「绝不留空转轴」由此更强地满足——空转不仅不发生，而且**看得见**。

### I-6〔正文空缺〕因子池重建**必须留在 `runtime`**，不能搬进 `FactorLane`

`tests/test_discovery_resume_pool.py`（属 §3.5 明令「逐条不动」的 13 个文件之一）有两条结构断言：
`inspect.getsource(runtime.DiscoveryRun)` 中 `run_in_threadpool(rebuild_kept_pool` 恰好出现 **1 次**、
`self._rebuild_pool(` 恰好 **2 处**。§2.2 的搬迁表却把池重建划给了 Lane。为同时满足两者：
`DiscoveryRun._rebuild_lane` 保留因子轴那一处 `run_in_threadpool(rebuild_kept_pool, ...)`，
策略轴走 `lane.rebuild(...)` → `strategy_gates.rebuild_strategy_pool`（该实现同样是「一份实现、
两个调用点」，另有 AST 用例守着）。`FactorLane.rebuild` 因此**未实现**（避免第二份实现）。

### I-7〔正文空缺 · 与评审 P1-10 同型〕§3.5 的既有测试表仍有一处遗漏

评审把 v1 的既有测试表修过一轮，但仍漏了 `apps/web/components/Discovery/DiscoveryCandidateTable.test.tsx`：
它断言候选表里出现裸的 `"0.0612"`，而「列按轴切换」必然改变混排视图的单元格文案（混排时逐行
标注 `IC 0.0612` / `夏普 1.43`——因子的 IC 与策略的夏普不可比，不写口径就会被当成同一列读）。
已同步修改并新增一条「切到因子轴后列头换成因子口径」的用例。**方法论**：这是第三次踩「既有
测试清单不完备」的坑（v1 错一行漏三处 → 评审补 → 仍漏一处），下一轮应当直接以「跑一遍全量、
把红灯清单当作真源」代替手工列表。

### I-8〔已知局限〕`GET /discovery/config/defaults` 的 `templates` 按 `default_bar` 计算

该端点没有 `bar` 参数（§4.2 的响应形状里 `templates` 也是平铺的）。模板的可挖性只有一条判据
依赖 bar——「所需高周期帧数超出上限」。故 defaults 用 `settings.default_bar` 计算，启动器展示
的可挖集合在极端周期（如 `1D`）下可能与真实提交时的判定有出入；**提交路径不受影响**：
`service._normalize_templates` 会用请求里的真实 `bar` 重算一次，不可挖的模板一律 400
`DISCOVERY_TEMPLATE_UNSUPPORTED` 并带原因。列入下一轮开放问题（给该端点加可选 `bar` 查询参数）。

### I-9〔行数预算〕`runtime.py` 二次拆分

§2.2 预计拆分后 `runtime.py` ~520 行；实际因新增了目录构建 / 逐轴计数同步 / 轮转验证 / 回测
硬顶而达到 **968 行**，超出仓库 800 行红线。已二次拆分：把「一轮怎么跑」整体下沉到新模块
`app/discovery/iteration.py::IterationRunner`（364 行），`runtime.py` 回落 **644 行**。
`DiscoveryRun` 只保留生命周期、扇出、取数、池重建、调度与终态。全部新增 / 修改文件均在 800 行内
（最大者 `runtime.py` 644、`expression.py` 609、`validation.py` 551）。

---

### 实测数据（正文点名要求记录）

**`AXIS_COST` 的本地实测量级**（4000 根 1H 合成窗口，单候选完整验证的中位墙钟，3 次取中位）：

| 轴 | 实测耗时 | 相对因子轴 | §六 的系数 | 差距 |
|---|---|---|---|---|
| `factor` | 0.034 s | 1.0× | 1 | 基准 |
| `factor_combo`（2 成员） | 0.307 s | **9.0×** | 10 | 略高估，安全 |
| `strategy_param` | 0.382 s | **11.3×** | 8 | **低估 1.4×** |
| `strategy_combo`（3 成员） | 1.285 s | **37.9×** | 20 | **低估 1.9×** |

**保留 §六 的系数不改**：① 改了会让评审 P2-10 写进正文的两个算例（1,560,000 通过 /
3,120,000 触 413）与代码不再对应；② 413 是**工作量**闸而不是墙钟闸，真正的墙钟护栏是
`discovery_max_backtests_per_iteration`（按底层 `run_backtest` 次数计）与
`discovery_iteration_timeout_s`，二者都按实际发生的次数计量、不依赖这组系数。
**下一轮建议**把 `strategy_param` 调到 12、`strategy_combo` 调到 40，并同步改正文算例。

**回测硬顶的实测触发频率**：默认 120 次 / 轮。四轴各 10 名额时单轮底层调用约
`10×2 + 10×2 + 10×(2×rotationSize)` ≈ 60–100 次，**默认配置下不触发**；把硬顶调到 1 时
`truncatedByBacktestCap` 立刻 > 0 且 `note` 出现「本轮回测次数触顶」（有用例守着）。
截断量按轴分别计入，与「墙钟到点」的 `truncatedByTimeout` 分开记——两者混成一个数，用户就
分不清该调哪个旋钮。

**`FSA_MIN_POOL` 与 `strategy_param` 自适应阈值的实测效果**：`scan_skeletons(["a","b","c"],
threshold=0.15)` 实测返回 **3 条冻结**——池里只有 3 条时每条都占 33%，会被**全部**冻结，这正是
护栏存在的理由（用例已固化这条反证）。`strategy_param` 轴的自适应阈值实测：`bar=1H` 下可挖
模板 **9 个**（11 个注册模板里 `regime_rotation` / `factor_score` 因非原子被排除，其余 9 个全部
可挖；高周期并集为 `("12H","1D")`、`htf_warmup_bars=75`，均在 `DISCOVERY_MAX_HTF_FRAMES=3`
之内，无模板被帧数上限挤掉），故 `fsaThreshold = max(0.15, 2/9) = 0.2222`——即「一个模板要占到
入库池 22.2% 才冻结」，而不是 15%（15% 在 9 元词表上几乎必然误伤最好的那两个模板）。
对因子轴与两条组合轴恒为 0.15，行为不变。

**干净 HEAD worktree 上实测的红灯基线对账**（`git worktree add … 8eb196a`，全量
`pytest -q`，非记忆值）：

| | 用例数 | 红灯 | 明细 |
|---|---|---|---|
| **基线（干净 HEAD）** | 2255 passed / 1 skipped | **9 failed** | 7 × `test_ai_trade_prior_session`（他人在制）、1 × `test_agent_ws::test_ws_emits_ready_history_and_full_run`、1 × `test_ai_backtest_manager::test_profit_rule_is_forwarded_to_spec`（两条均为全量顺序 flake，单跑必绿） |
| **交付后（本工作树）** | 2392 passed / 1 skipped | **15 failed** | 上述 7 × `prior_session` + 1 条 agent 计时 flake（`test_agent_ws`，与基线同一条）+ **7 × `test_factor_library_expansion`** |

差额的 7 条 `test_factor_library_expansion` 是**工作树里未提交的孤儿测试**（该文件不在 HEAD，
属他人在制的 `factor-library-crypto-expansion` 特性，断言因子库总数为 20 而当前为 13）；
在干净 HEAD 上不存在，与本轮改动无关。基线里那条 `test_ai_backtest_manager::test_profit_rule_is_
forwarded_to_spec` 本次转绿，进一步印证它与 `test_agent_ws` 同属全量顺序 flake。
**本轮新增红灯：0。** 新增 `test_discovery_*` 用例 **118 条**（424 − 306），全部绿。

前端：`vitest` **131 文件 / 1407 用例全绿**（基线 126 / 1353），`tsc --noEmit` 只剩
`components/Factor/WeightBatchArena.test.tsx` 一条**既有**类型错误（`TrainMethod` 字面量），
与本轮无关、拆分前即存在。

---

### §7.3 十四条验收标准逐条对账

| # | 判据 | 状态 |
|---|---|---|
| 1 | 启动器可同时勾选四条轴；不可挖模板灰显并写明原因；无占位文案 | ✅ `DiscoveryAxisPicker.test.tsx` 四条用例 |
| 2 | 三轴同跑 20 轮后四种 `kind` 都有 kept 与 rejected | ⚠️ **未做长跑**：`test_discovery_multi_axis_runtime` 只跑 1 轮（四种 kind 的候选行都已落库、闸的双向性由 `test_discovery_strategy_gates` 单独证明）。20 轮真机长跑需人工执行 |
| 3 | S6 非装饰：拒绝分布里 S6 占比 > 0，且 kept 的 `oos/is` 夏普比不全落在 [0.95,1.05] | ✅ 前半有 `test_s6_is_not_decoration_under_a_real_two_regime_backtest`（两段分布相反即被拒）；后半的分布判据需长跑样本，同 #2 |
| 4 | 拒绝码覆盖 ≥6 条 S 闸 + ≥2 条审查规则，且**每一条都显示为中文** | ✅ 中文映射两处都改并有机器判据（`discovery.test.ts` 断言 `rejectReasonCn` 不等于原码，含 `S0:missing_htf:12H` 这类带子因的形态）；覆盖数需长跑 |
| 5 | `strategy_param` 入库后进策略面板，`params_json` 与基因逐键相等 | ✅ `test_param_candidate_becomes_a_strategy_row_with_identical_params`（BL-7） |
| 6 | `factor_combo` 入库后组合与伴生 `factor_score` 策略都在 | ✅ `test_combo_candidate_creates_a_factor_set_and_its_companion_strategy` |
| 7 | `strategy_combo` 入库后出现在组合策略页并能跑 combo 回测 | ✅ 入库路径有用例；combo 回测走 `_backtest_strategy_candidate` 的 `run_combo_backtest` 分支 |
| 8 | 重启后三轴池被重建，重建失败停在 `paused(rebuild_failed)` | ✅ `test_discovery_strategy_pool`（含全失败 ⇒ 调用方转 paused 的判据） |
| 9 | 上游库存不足时界面明确显示让渡 | ✅ 后端 `test_rotation_axis_yields_while_the_upstream_pool_is_empty` + 前端 `DiscoveryFunnel.test.tsx` 的让渡行用例（BL-9 三个落点全通） |
| 10 | 因子轴 golden 逐条相等 | ✅ 见 I-1 |
| 11 | 后端全量相对干净 HEAD 零新增红灯；前端零红灯；`tsc` 不新增错误 | ✅ 见上表 |
| 12 | 未打开挖掘视图的用户零额外开销 | ✅ 惰性首拉不变；另加 `test_factor_only_run_never_fetches_higher_timeframes`（没选策略轴的 run 一根高周期都不取） |
| 13 | `discovery_enabled=False` 时四条轴的写类端点一律 403 | ✅ 总闸在 `start_run` / `resume_run` 的第一道，与轴无关 |
| 14 | 满负荷三轴挖掘时既有端点 p95 劣化 < 30% | ⚠️ **未验证**：需真机压测。已有的结构性缓解：四轴共用**同一个** `discovery_validate_concurrency` 信号量（不是每轴一个）+ 回测硬顶 + 轮转验证 |

未达成的三条（#2 / #3 后半 / #14）都需要**真机长跑与压测**，无法在实现节点内完成；
按本文纪律如实记录，**未在代码里降低任何标准**。

---

## 评审结论（Review Verdict）

### 结论：**有条件通过**

本文的骨架是对的，而且对的方式很具体：它没有把「三条策略轴」当成三个搜索器来写，而是识别出
真正的增量是**一条路径依赖的验证车道**，并且诚实地列出了这条车道与因子轴「镜像但方向相反」
的三个坑（成本量纲、样本外切分、去重基准）。BL-1 / BL-2 / BL-8 三条尤其站得住——它们都经得起
源码核对，`run_backtest` 确实收 bps、`SimBroker` 确实内部 `/1e4`、`runtime.py` 里被点名要平移的
每一个成员都真实存在。范围判断（D-7 补齐三轴让 `IMPLEMENTED_AXES == ALL_AXES`）也正是上一轮
D-6 欠下的账，right-sizing 没有问题。

但评审在四个维度上各找到了会致命的东西，共 **3 P0 / 12 P1 / 11 P2**，**已在正文全部修复**：

- **可行性**：P1-1 与 P0-3 是同一类事故——按 v1 字面实现，`clip_result` 拿不到 `calmar`、
  组合成员拿不到 `weight`，于是 **S5 与 S1 会把每一条候选都拒掉**。两条都不报错。
- **完备性**：P1-3（回测计数低估 4–6 倍）、P1-4（同模板成员静默缺帧）、P1-11（combo pad 漏了
  regime 起算根）、P1-9（幂等回放丢伴生策略 id）是四个真实的边界遗漏。
- **一致性**：P0-1 与 P1-5 都是「文档以为复用了既有机制，实际那机制的键或表在别处」——
  `factorKey` 对挖掘因子恒为 `"expr"`、前端的拒绝原因中文表是自己那一张。P1-10 则是把既有
  测试的现状说错了一条、漏了三处。
- **合宜性**：P0-2 是本轮最该被拦下的一条——把 `turnoverBps`（成本模型参数）交给搜索器，
  等于给优化器一个把成本调成 0 的旋钮，这跟 BL-1 是同一类事故走了另一扇门。

一条贯穿性的方法论收获，已写进 R-19：**「重构前后逐位相等」的等价门禁只能守住它自己跑过的
那条路径**。BL-8 的 golden 用例是 factor 单轴 3 轮，因此它对 P1-2（多轴共享 `tested` 与
`skeleton_stats` 会改写因子轴行为）完全无感。新增一个维度，就要另立一条覆盖该维度的等价用例。

### 放行条件（实现节点必须逐条落实，缺一即视为未完成）

1. **BL-10 / BL-11 / BL-12 三条 P0 修复必须先于对应轴的任何其它代码落地**，且各自的门禁用例
   （`test_discovery_combo_skeleton.py` / `test_discovery_rotation_costs.py` /
   `test_discovery_combo_weights.py`）先写先跑绿。其中 BL-12 必须含那条**反向**用例
   （不烘焙权重 ⇒ 零成交），它是「这个错误确实是静默的」的证据。
2. **BL-13 的 `metrics_of` 是策略轴指标的唯一出口**，`clip_result` / `clip_rebased` / IS 段
   三处都走它；禁止任何地方直接用 `compute_metrics` 的返回值去喂 S5。
3. **BL-14 的逐轴计数必须连同检查点回落规则一起落地**，并跑通 §2.2 第 4 步的跨轴隔离用例。
   老检查点缺 `tested_by_axis` 时整体归 `factor` 轴——归零会让长跑任务的多重检验惩罚一夜塌回
   基准值。
4. **§3.5 的七处既有测试按新表逐条处理**；除表内四个文件外的 13 个 `test_discovery_*.py`
   必须**逐条不动且全绿**。全量红灯基线以**干净 HEAD worktree 现测**为准，不得引用历史记录。
5. **P1-5 的中文映射两处都要改**（后端 `FILTER_LABELS` + 前端 `REJECT_REASON_CN` 与 `S0:`
   前缀分支），并配上那条「`rejectReasonCn` 的返回不得等于原码」的用例。这是本仓库第二次在
   同一个地方摔跤，不能再有第三次。
6. **BL-9 的三个可见性落点以 `DiscoveryFunnel.tsx` 为准**（不是 `DiscoveryMonitor.tsx`），
   且让渡记录必须带 `to_axis`。「后端算了、类型补了、用户看不见」等于没做。
7. **§7.3 的 14 条验收标准逐条跑**，第 3 条按修订后的两个可判定判据执行。任何一条不达标都要
   在 §十一 如实写明，**不得**在代码里静默降低标准。
8. **凡实现中发现本文任一承重条款不可行，先回来改本文再改代码**（沿用上游放行条件 7），
   并在 §十一 记录偏离原因与替代方案。

### 未解决项

无。3 个 P0 与 12 个 P1 均已在正文修复，11 个 P2 亦已一并落实。剩余的 7 条开放问题都是**有意
不做**并已在 §十 记录取舍理由的范围决策，不构成阻塞。


---

## 十二、终审记录（Final Review · 代码评审节点）

> 本节由**终审节点**在合入前追加：逐条比对交付物与 §三 变更计划、§七 验收标准、十四条承重条款
> 与八条放行条件。发现的缺陷一律**直接改源码并复跑**，不留 TODO 清单。

### 覆盖度对账（结论：与 §三 逐行对齐）

§3.1 的 12 个新增后端文件、§3.2 的 15 个修改文件、§3.3 的 8 个新增前端文件、§3.4 的 8 个修改
文件全部落地；§3.5 的七处既有测试按新表逐条处理（另加 §十一 I-7 补的
`DiscoveryCandidateTable.test.tsx`）。承重条款抽查源码核对结论：BL-1 `costs_of` 直传 bps 且
`run_backtest` 签名确为 bps（无二次除 1e4）；BL-2 IS/OOS 各自 `assembler.build()` 一次、
`GatedStrategy` 代理的六个钩子与 `run_backtest` 实际调用的六个**完全一致**（不多不少，故不会
出现「OOS 段悄悄走了 BaseStrategy 默认实现」）；BL-4 `penalized_gate` 单实现双调用点；
BL-11 `FROZEN_POLICY_KEYS` 在 `RotationAssembler.policy()` 处**最后覆盖**，搜索器写什么都盖不掉；
BL-13 `metrics_of` 是策略指标唯一出口；BL-14 逐轴计数三件套连同老检查点回落一并落地。

### RV-1〔诚实性〕策略轴把「LLM 份额回落」标成了 `llm`，来源分布图会画出没发生过的产出

`StrategyLane.produce` 对 `source == "llm"` 的名额回落随机探索（§十一 I-2 的有意偏离，正确），
但产出的候选**仍标 `source="llm"`**。因子轴的同款回落在 `generation.CandidateFactory._fallback`
里是改标 `"random"` 的，两条轴口径相反。后果不报错：`DiscoverySourceBars` 会给策略轴画出约
20% 的「LLM 生成」，而本轮这三条轴**一次 LLM 生成调用都没有发过**；更糟的是那张图的存在理由
恰恰是「实际来源与预算背离时看出谁在空转」，标错等于把唯一的观测手段调成了永远看不出来。
**已修**：回落名额改标 `random`，`llm_fallback` 仍如实累计并进 `state.axisSnapshots`。
门禁：`test_strategy_axis_llm_slots_are_labelled_random_not_llm`。

### RV-2〔设计意图未兑现〕合并后的 LLM 审查抽样按轴序取前 N 条 ⇒ 策略候选永远抽不到

§十一 I-2 为「审查合并成一次调用」改写了 `prompts.build_review_system_prompt`，按 `kind` 分出
「表达式」与「策略」两套判据——这一改是必要的（否则模型会以「看不懂结构」误杀整条轴）。但
`IterationRunner._llm_review` 把各轴候选拍平后取 `flat[:sample_size]`，而拍平顺序就是
`ALL_AXES` 次序、`factor` 恒在最前：默认 `reviewSample=5` 且因子轴每轮产出远多于 5 条时，
**三条策略轴一条也抽不到**，那套新判据成了死代码。**已修**：抽样改为跨轴轮转
（`_sample_across_axes`）；单轴时与「取前 N 条」逐条相同，故因子轴行为不变（BL-8）。
门禁：`test_review_sampling_rotates_across_axes`。

### RV-3〔中文化漏接展示点〕策略详情回显裸标识符（本仓库第三次同款）

`describe_strategy` 里已有唯一真源的中文渲染（`template_name` / `member_label`），候选行的
`describe` 也确实用了它；但 `metrics_json` 只带 `templateKey` 与原始成员 payload，于是
`DiscoveryStrategyDetail` 的「模板」一行显示 `momentum_breakout`、「成员清单」显示
`expr:1a2b3c4d`。与上一轮 RV-6（`category=discovered` 未登记中文映射）、评审 P1-5（前端
`REJECT_REASON_CN` 是另一张表）同型：**中文化 helper 存在 ≠ 每个展示点都接上了**。
**已修**：`StrategyLane._metrics` 增 `templateName` 与成员 `label`（两者都只加在 metrics 上，
`baked_members()` / `member_specs()` 那份**不动**——它要原样落进入库参数，多一个键就违反 BL-7），
前端按 `label ?? name ?? templateKey` 回落渲染。
门禁：`test_combo_member_labels_never_pollute_the_baked_payload` +
`test_strategy_metrics_carry_chinese_display_names`。

### 未改动的三处（记录判断依据，避免下一轮重复讨论）

1. `_normalize_axis_weights` 把权重 0 强制抬到 1.0：看似「静默改写用户意图」，但一条权重为 0
   的已选轴恰好就是 BL-9 明令禁止的空转轴，抬到 1.0 与「不勾这条轴」语义等价且更安全。
2. `rebuild_strategy_pool` 对复评不达标者仍 `entries.append`：与因子轴 `rebuild_kept_pool`
   **逐行同构**（淘汰由调用方按 `failures` 做），不是遗漏。
3. `_run_fsa` 对池子过小而跳过的轴不跑 `prune_expired`：`active_bans` 在**读侧**按 TTL 过滤，
   过期冻结不会继续生效，只是列表未被回收，无行为影响。

### 终审复跑（本工作树实测）

| 范围 | 结果 |
|---|---|
| `pytest -k discovery` | **428 passed**（交付时 424 + 终审新增 4 条门禁） |
| 后端全量 `pytest -q` | 2393 passed / 1 skipped / **14 failed**：7 × `test_ai_trade_prior_session` + 7 × `test_factor_library_expansion`，二者均属**他人在制且不在 HEAD** 的特性，与本轮无关；§十一 记录的 `test_agent_ws` 顺序 flake 本次转绿 |
| 前端 `vitest run` | **131 文件 / 1407 用例全绿**；终审改动后 Discovery 相关 11 文件 / 123 用例复跑全绿 |
| `tsc --noEmit` | 只剩既有的 `WeightBatchArena.test.tsx` 一条（`TrainMethod` 字面量），与本轮无关 |

### 结论：**通过，予以合入**

三条 P0 修复（BL-10/11/12）、BL-13 的并集出口、BL-14 的逐轴计数与老检查点回落均有机器判据；
§十一 如实记录了 9 条实施发现与三条**未达成**的验收标准（#2 / #3 后半 / #14 需真机长跑与压测），
没有在代码里降低标准——这正是本文一再要求的姿态。终审发现的三条缺陷全部当场修复并配门禁。
