# 成果资产台账与持续样本外体检（discovery-asset-ledger）设计方案 v2

> 迭代：loop `tge-3635bc21` 第 3 轮 · 设计节点
> 上游一：`docs/plans/strategy-factor-discovery/spec.md`（v2，已实现并合入 commit `8eb196a`）
> 上游二：`docs/plans/discovery-strategy-axes/spec.md`（v2，已实现并合入 commit `79a930d`）
> 本文只描述**增量**；凡本文未重述的机制（四条搜索轴、五维演化、FSA、11+9 条硬闸、循环编排、
> 检查点、WS、LLM 姿态、发现层永不下单的安全不变式），一律**沿用**两份上游文档，不得另起一套。

---

## 评审记录（Review Notes · v1 → v2）

> 评审节点：loop `tge-3635bc21` 第 3 轮 · 设计评审。逐节对照**可行性 / 完整性 / 一致性 /
> 规模适配**四维，且每一条承重论断都回到**仓库源码**上实核（而不是采信本文的自述）。
> 结论：3 条 P0 + 10 条 P1 + 7 条 P2，**全部已在本文正文改毕**，版本升 v2。
>
> 本轮的三条 P0 有一个共同形状，与本文 §〇 自己写的判据完全吻合：**错了不报错，只是这条
> 功能永远给出一个看起来很正常、实际上毫无意义的结论**。P0-1 让所有老资产永久静音成
> `unknown`（而 D-26 把 `unknown` 定义成中性色、界面上看不出异常）；P0-2 让本轮"最重要的
> 一条验收标准"变成一条永远变不绿的用例；P0-3 让"同源身份"这句话在因子轴上根本不成立。

### P0（阻断级，不改就必然交付出一个静默失效的功能）

| # | 位置 | 问题 | 处置 |
|---|---|---|---|
| **P0-1** | §2.3 / AL-2 | **体检取数窗口从 `now` 固定回看，导致老资产永久 `unknown`。** v1 写 `checkStart = checkEnd − windowBars × step`、`freshStartTs = max(minedEndTs, checkStart)`、`warmupBars = split`。资产一旦老过 `windowBars × step`（默认 15m × 1500 ≈ **15.6 天**），`minedEndTs` 就落到 `checkStart` 左边 ⇒ `freshStartTs == 取数窗口第一根` ⇒ `split == 0` ⇒ `warmupBars == 0 < 300` ⇒ **H2 恒触发、永久 `unknown`**。本轮功能的全部价值在"时间越久证据越硬"，而这条 bug 恰好在证据开始变硬的那一刻把它关掉；又因为 `unknown` 按 D-26 是中性文案「暂不评级」，界面上**没有任何异常**。附带缺陷：fresh 段随时间无界增长（一年后拿一年数据当"新样本"是另一种错口径）。 | §2.3 重写：窗口**锚定在 `freshStartTs`** 而非 `now`——新增 `discovery_health_warmup_bars`（默认 400）、派生 `maxFreshBars = windowBars − warmupBars`、`freshStartTs = max(minedEndTs, checkEnd − maxFreshBars × step)`。夹上界后暖机段恒有 `warmupBars` 根、组取数长度仍恒为 `windowBars` 根（证明见 §2.3）。AL-2 同步改写；§7.1 新增「老资产不得永久 unknown」的两点门禁；§6 加一个键。 |
| **P0-2** | §2.4 / AL-1 / §7.3-1 | **AL-1 的失效机理定位错了，导致"双向门禁"第 2 条按 v1 的构造不可能变绿。** 已核 `validation.evaluate_split`：`resolve_direction` 是在 **IS 段**（体检语境下 = **暖机段**）定号，**不是**在 fresh 段。所以「不冻结方向 ⇒ fresh 段 rankIc 又被翻成正的 ⇒ 体检恒健康」这句话不成立。按 v1 给的构造（`two_regime_candles(is_bars=600, oos_bars=300)`，`split_at` 落在 600），auto 会在趋势段定出与冻结值**相同**的方向，两条用例都判 `H4`——第 2 条断言必红。DoD #1 把它列为"本轮最重要的一条"，实现节点撞上必红用例后只有两条路：删测试，或改判据；两条路都把 AL-1 降级成摆设。 | 改写 AL-1 的机理（真正的危害是：**暖机段已翻面时 auto 会跟着翻，于是拿一把"随行情变形的尺子"去量衰减**，且与登记快照口径不同源）；§2.4 换成可判定的三段构造：`two_regime_candles(is_bars=300, oos_bars=900)` + `split_at` 落进反号段深处，使 auto 在暖机段定出 `short`、fresh `rankIc > 0` 不判 H4，而冻结 `long` 判 H4。§7.1 / §7.3-1 同步。 |
| **P0-3** | §2.2.1 / AL-4 | **`factor_match_key` 与 `_find_existing_factor` 的现状比较键不是同一个键**，把它当"纯提取"就是静默改语义。已核 `promote._find_existing_factor`：比的是 `params_json["expr"]` 与 `expr_text` 的**逐字符串相等**；而 v1 提出的 `factor_match_key = expr_hash(parse_expression(expr_text))` 是**规范式哈希**（交换律同哈希）。二者对"`add(a,b)` 与 `add(b,a)`"给出相反答案。照 v1 写：要么改坏已发布的入库幂等（R-8 的"零变更全绿"不再是安全网，且新引入 `parse_expression` 对历史脏数据抛错的路径），要么 AL-4 的同源承诺在因子轴上根本不成立。 | §2.2.1 明写**采纳规范式哈希**并如实标注这是**语义变更**（不是纯提取）：`_find_existing_factor` 改为「先按规范式哈希比，解析失败的历史行回落逐字比」，配 `test_discovery_promote.py` **新增**用例而非零变更；R-8 改写；新增 D-27 拍板。 |

### P1（必须改，否则会在实现或上线时炸）

| # | 位置 | 问题 | 处置 |
|---|---|---|---|
| **P1-1** | §2.2.2 / §2.12 | `register_kept(run.spec, …)` **拿不到** `mined_window`。已核 `runtime.RunSpec` 是 frozen dataclass，字段只有 `(run_id, inst_id, bar, symbols, config, rest, settings, resume, llm_client)`——既无 `checkpoint`（`startTs/endTs` 在 `run._checkpoint.data_window`），也无 df（`splitTs` 要 ts 数组）。 | 签名改为显式传 `MinedWindow`，由 `_commit` 侧组装；§2.12 调用链同步。 |
| **P1-2** | §2.12 | **组内共享 `memo` 的前提没写死，共享错了会静默算错。** 已核 `validation._context_of` 用**固定键** `__discovery_field_context__` 缓存 `build_context(ctx.df_full)`，**不校验 df 身份**。同一个 memo 见到第二份 df，第二个资产就在**别人的字段上下文**上求值——不抛错、指标照算、全是错的。 | §2.3 增「一组一份 df、per-asset 差异全部经 `split_at` 表达、memo 一组一份跨组必新建」的承重纪律 + 机器判据。 |
| **P1-3** | §2.3 H3 / §2.5.1 | **H3 的分母取不到。** `SplitEvaluation.coverage` 是**全窗**有限值占比（已核 `np.isfinite(values).mean()`），含暖机段的 rolling NaN；H3 要的是 fresh 段。照 v1 写就是把暖机 NaN 的锅算到资产头上。 | 明写从 `split.values[split_at:]` 现切一刀算（不新写指标，AL-3 不受影响）。 |
| **P1-4** | §2.6 要点 4 / AL-9 / §7.3-9 | **AL-9 的计数口径自相矛盾且少算一半。** §2.6 说累加 `assembler.backtests_per_segment()`；DoD #9 说「每个 `strategy_combo` 底层 4 次」= `2 × per_segment`。已核 `run_split_backtest`：gated 返回 `backtests=2`、combo 返回 `2 × per_segment`——真实开销是**两段**。按 `per_segment` 计，硬闸放行量是设计值的 **2 倍**（R-1 的配额护栏直接失守一半）。 | AL-9 / §2.6 一律钉到 `2 × backtests_per_segment()` 预估、实际按 `SplitBacktest.backtests` 回冲；并明写"体检不需要 IS 段指标但仍付两段代价"是**有意接受**的（D-28）。 |
| **P1-5** | §2.6 要点 7 | **正常路径的 `next_check_ts` 怎么推进没写**（v1 只给了 `unknown` 一支）。`list_due_assets` 是 `WHERE next_check_ts <= now ORDER BY next_check_ts ASC LIMIT batch`——不推进就等于队头 8 条资产**每 tick 被反复体检**、其余永远排不上，而 `summary.guard` 显示一切正常。 | §2.3 补四条分支表（healthy/unknown/error/取数失败）。 |
| **P1-6** | §2.5.3 | **策略资产的 `retention` 在分母为负时符号反转。** 因子侧两边取 `abs`，策略侧 v1 写的是裸比值 `fresh.sharpe / accept.oos.sharpe`。已核 `strategy_gates`：S6 只挡"样本外崩塌"，**不要求** OOS 夏普为正 ⇒ 分母可为负 ⇒ 越差的 fresh 夏普给出越高的 retention，UI 上是一条更长的绿条。 | 明写 `accept.oos.sharpe <= 1e-9 ⇒ retention = null`；H5 同步给出负分母时的行为。 |
| **P1-7** | §2.2.1 / §2.2.3 | **`factor_combo` 的身份键忽略权重，会把不同资产并成一行且只留第一份基因。** 已核 `_find_existing_set` 的幂等键只含 `(factorKey, params, direction)`，**不含 `weight`**。同成员不同权重的两条成果 ⇒ 同 `asset_key` ⇒ AL-11 只做加法 ⇒ `genome_json` / `accept_metrics_json` 永远是第一条的 ⇒ 此后所有体检都在评第一份权重，第二条的真实基因**不落任何地方**。 | 保持与 promote 同源（AL-4 优先），但 `sources_json` 每条溯源**必须**带 `genomeHash` 与该次的 `acceptSummary`，详情抽屉在多基因时显式提示；写进 §2.2.3 / §5.1 / §2.9 导出。 |
| **P1-8** | §3.4 | **前端变更计划漏了 `apps/web/lib/discoveryTypes.ts`。** `DiscoveryCounters` 是显式 interface（`tested/kept/llmCalls?/prunedCandidates?/prunedIterations?/rejected`），`DiscoveryMonitor` 读 `counters.ledgerRegistered` 会直接打红 `tsc --noEmit`，而 DoD 15 要求"不新增 tsc 错误"。（已核 `applyDiscoveryEvent` 的 `state` 分支是 `{...prev, ...patch}` 整对象合并，**运行期不会吞键**，reducer 无需改。） | §3.4 补该文件。 |
| **P1-9** | 全局（上线口径） | **存量成果零回填，上线第一天台账是空的。** 登记只发生在 `_commit`（AL-5）；仓库里 commit `8eb196a` / `79a930d` 两轮已经跑出的 kept 行**永远不会**进台账，除非同一条被重新挖到。用户点开新分区看到空表 + 「台账为空但有 run 在跑」的空态——正好把"功能上线"读成"功能坏了"。 | 新增 §2.12：一次性、幂等、有界、fail-open 的启动回填（镜像 `main.py::_backfill_companion_strategies` 的既有姿态）；空态文案同步。 |
| **P1-10** | §2.2.2 | **策略阈值的配置键名没钉死。** 已核 `service.py` 归一化后的 run config：因子阈值在 `thresholds`、策略阈值在 **`strategyThresholds`**。取错了就是拿因子阈值表喂 `normalize_strategy_thresholds`——它会把不认识的键**全部丢掉、回落默认值且不报错**，于是 AL-3 第二句（冻结登记时阈值）整条空转。 | §2.2.2 登记表逐轴写死取哪个键，并加一条门禁。 |

### P2（已一并修正）

| # | 问题 | 处置 |
|---|---|---|
| P2-1 | §3.2 / §3.6 说 13 个 `DISCOVERY_HEALTH_*` 键，§6 表实际 14 行 | 统一为 **15**（含 P0-1 新增的 `discovery_health_warmup_bars`） |
| P2-2 | §4.8（筛选式导出触顶回 `truncated/matched`）与 §4.10（超 500 且未给 `ids` ⇒ 413）互相矛盾 | 钉死：`ids` 超 500 ⇒ 413；筛选式 ⇒ 截断 + `truncated/matched` |
| P2-3 | §4.4 的 `503 DISCOVERY_DATA_UNAVAILABLE` 未进 §4.10 汇总 | 补入 |
| P2-4 | §2.2.3 说前端拿 `GET /discovery/runs` 比对悬空溯源，§4.2 又说服务端下发 `runExists` | 统一为**服务端**算 |
| P2-5 | `POST /assets/{id}/check` 同步执行，`asset_timeout_s` 默认 45s ⇒ 单个 HTTP 请求可能挂 45 秒 | §2.8 补前端超时与 loading 约定 |
| P2-6 | §2.4 写 `trend_candles(seed=5)`，实际签名 `trend_candles(n, *, seed=5, …)`（`n` 必填） | 改正 |
| P2-7 | `horizon` 其实候选 `metrics_json["horizon"]` 里就有（已核 `_build_metrics`），绕 `run.config` 反而会被"两次登记之间用户改了配置"污染 | 登记优先取候选自带的 |

### 四维总评

- **可行性**：技术栈完全够用，零 DB 迁移属实（两张全新表 + `create_all`，已核 `db/session.py` 的 ALTER 补丁器只在给既有表加列时才需要）；`evaluate_split` 两个带默认值的可选字段确实是纯加法；`window_loader` 平移与 `promote_source` 拆层可行。唯一真正难的地方是 §2.3 的窗口口径——v1 在这里翻车（P0-1）。
- **完整性**：错误路径与降级姿态写得相当细（取数失败不写 check 行、迟滞、fail-open 计数、不静默截断）。缺口集中在**调度闭环**（P1-5 `next_check_ts`）、**上线口径**（P1-9 回填）与**跨资产共享状态**（P1-2 memo）三处。
- **一致性**：与 `CLAUDE.md` 的行数 / 形参 / 命名 / 测试纪律一致；与两份上游的安全不变式一致（发现层零下单、验证不由 LLM 说了算、绝不合成数据）；§2.5.4 的「本轮已淘汰 vs 已衰减」命名纪律是本文的加分项。不一致主要是文内自相矛盾（P1-4 / P2-1 / P2-2 / P2-4）。
- **规模适配**：范围拿捏得住——不做自动停用（D-19）、不做 WS（D-21）、不做导入（开放问题 3）、不做多资产对比（开放问题 1）都是对的减法。P1-9 的回填是本轮**唯一**必要的加法，且被限制成一次性幂等启动任务。

---

## 〇、阅读顺序与承重决策速览

先读这一节。下面 12 条是本轮的**承重条款**（AL = Asset Ledger），实现时必须逐字照做；
若实现中发现任一条不可行，**先回来改本文**，再改代码，绝不在代码里静默偏离
（沿用上游放行条件 7）。

它们共同的特征与两份上游的 F/S/BL 条款完全一致：**错了不报错，只是这条功能永远给出
一个看起来很正常、实际上毫无意义的结论**。本轮的功能是「告诉用户挖出来的东西现在还灵不灵」，
所以一条错误的「健康」比崩溃危险得多——崩溃会被发现，虚假的绿灯不会。

| 编号 | 承重条款 | 违反后的表现 |
|---|---|---|
| **AL-1** | 体检因子资产时，`evaluate_split` **必须使用资产落库时冻结的 `direction`**，严禁再走 `resolve_direction(..., "auto", ...)` 重新定号 | 现状 `evaluate_split` 是无条件 `auto` 定号，而**它定号用的是 IS 段——在体检语境下就是「fresh 段之前那一小截暖机数据」**（评审 P0-2 已核源码，v1 误写成"在 fresh 段翻面"）。真正的危害是：**尺子会跟着行情变形**——一条已经在暖机窗口里翻了面的因子，`auto` 会顺着把方向也翻过去，于是 fresh 段的 `rankIc` 重新变正、`H4` 通过、衰减被判成健康；而且这把尺子与登记快照（分母）**根本不是同一把**，`retention` 也随之失去意义。这是上游迭代 1 评审 P0-1（F6 空转）在同一个函数上的同型复发，且这次没有任何别的闸能兜住它 |
| **AL-2** | 体检的判级**只能**用「挖掘窗口右端之后新产生的那一段」（fresh 段）；fresh 段的起点**必须锚定 `minedWindow.endTs` 并对长度夹上界**，取数窗口由它反推（**不得**从 `now` 固定回看）；fresh 根数 / 暖机根数 / 覆盖率任一不足，一律判 `unknown`，**绝不给健康结论** | 用整段窗口判级 = 拿资产自己的训练集给自己打分，刚入库的资产会立刻拿到一个漂亮的绿灯，而那个绿灯里没有一个 bit 的新信息。反过来，若像 v1 那样从 `now` 固定回看 `windowBars` 根再取 `max(minedEndTs, checkStart)`（评审 P0-1），资产一老过 `windowBars × step`（15m 下约 15.6 天）暖机段就恒为 0 根 ⇒ **H2 恒触发 ⇒ 全部老资产永久 `unknown`**；按 D-26 `unknown` 是中性文案，界面上看不出任何异常，功能在最有价值的那一刻悄悄关掉 |
| **AL-3** | 体检必须与挖掘用**同一把尺子**：① 因子走 `validation.evaluate_split` + `engine.factors.evaluation.evaluate_factor`，策略走 `backtest_lane.run_split_backtest` + `metrics_of`，本轮**不得**新写任何指标计算；② 判级阈值取**资产登记时冻结的那份** `thresholds`，不取当前默认值 | ① 两份指标实现迟早漂移，且漂移了没人看得出来（上游「估计量同源律」）；② 用当前默认阈值判级 ⇒ 用户调一次默认阈值，全部历史资产的健康度集体跳变，时间线上出现一条与市场无关的断崖 |
| **AL-4** | 资产身份 = **语义键**（`asset_key`），且必须与 `promote.py` 的三个 `_find_existing_*` **同源**：把它们的比较键抽成模块级纯函数，`identity_key` 与它们共用。**因子轴的那把键要从"逐字文本"升级为"规范式哈希"，这是一次有意的语义变更，必须配新测试而不是靠旧测试零变更**（评审 P0-3） | 用候选 id 当身份 ⇒ 台账退化成候选表的另一份拷贝，跨 run 归一价值为零；两套身份键各写一份 ⇒ 出现「promote 认为是同一条、台账认为是两条」的分裂，用户看到两行资产指向同一个因子，且其中一行永远显示「未入库」。而若照 v1 的措辞把它当"纯提取"做掉：`_find_existing_factor` 现状比的是 `params_json["expr"]` **逐字符串相等**，`expr_hash(parse_expression(...))` 是**规范式**（交换律同哈希），二者对 `add(a,b)` / `add(b,a)` 给出相反答案——嘴上说"行为不变"、手上把已发布的入库幂等改了，是最难查的一类事故 |
| **AL-5** | 登记点是**候选被判 kept 并落库之后**，不是 `promote` 之后；登记失败一律 **fail-open**（不拖垮迭代）但**必须计数并在 UI 上如实呈现** | 只在 promote 登记 ⇒ 用户没点入库的成果永远进不了台账，而「已通过全部过滤但还没入库」恰恰是最需要跨 run 汇总去看的那一批。fail-open 不计数 ⇒ 台账悄悄变空，看起来像「最近没挖到东西」 |
| **AL-6** | 体检守护**只**读 OKX 行情、**只**写 `discovery_assets` / `discovery_asset_checks` 两张新表。**严禁**改 `factors` / `factor_sets` / `strategies` / `strategy_runs` 的任何行，**严禁**下单，**严禁**改任何 run 的状态。降级只在台账内标注（advisory），停用与否是用户的显式动作 | 上游 D-2 / R-8 安全不变式的延续。一个后台协程自动停用用户正在用的策略，是本项目能犯的最严重的错误 |
| **AL-7** | 守护受 `discovery_enabled` **与** `discovery_health_enabled` 双闸；**每个 tick 开头重读一次 settings**，任一为假即本 tick 零外部副作用（不取数、不写库、不打日志刷屏）后继续 sleep | 「写类端点 403 但后台还在烧 OKX 配额」是假安全姿态（上游评审 P1-8）。而只在启动时判一次，则运行期关不掉 |
| **AL-8** | 降级必须有**迟滞**（连续 `failStreak` 次不达标才判 `decayed`，默认 2）；`unknown` 与 `error` **不得**计入连续失败计数；取数失败**不写 check 行**（整组跳过），仅求值/回测失败才写 `status="error"` 的 check 行 | 无迟滞 ⇒ 一次数据抖动就把资产打成「已衰减」，用户会关掉整个功能；`unknown` 计数 ⇒ 一段行情空档期把全部资产降级；取数失败写 check 行 ⇒ 一次网络抖动给每个资产的健康时间线插一根尖刺，此后再也读不出真实趋势 |
| **AL-9** | 资源硬闸按**底层 `run_backtest` 调用次数**计（不是按资产计），预估口径一律 **`2 × assembler.backtests_per_segment()`**（`run_split_backtest` 跑 **IS + OOS 两段**，已核 `SplitBacktest.backtests`），实际按返回的 `backtests` 回冲；单 tick 封顶 `discovery_health_max_backtests_per_tick`；单资产墙钟超时 `discovery_health_asset_timeout_s`；被闸挡下的资产**必须 `log` 并计数**，不得静默跳过 | 上游迭代 2 评审 P1-3 的教训：`strategy_combo` 一个资产的底层回测是 2×成员数 次，按资产计数的护栏形同虚设。而只按 `backtests_per_segment()`（不乘 2）计（v1 §2.6 要点 4 的写法，评审 P1-4），护栏放行量正好是设计值的**两倍**——R-1 那条"宁可慢"的配额纪律直接失守一半，且表现为"没触发过闸"，看起来完全正常。静默跳过 ⇒ 队列尾部的资产永远排不上，而界面上显示「一切正常」 |
| **AL-10** | 导出必须是**自包含可回溯快照**：含资产键、genome/表达式、冻结方向、挖掘窗口口径、登记时的完整指标与阈值、体检序列、当前状态、`schemaVersion`；**不得**含任何密钥 / 账户 / 本机路径 | 导出成 UI 表格的转储 ⇒ 换台机器打开发现无法复现任何一个数字，「回溯」名不副实 |
| **AL-11** | 同一资产在多个 run 被挖到必须**合并为一行多溯源**；`first_found_ts` 取最早，`sources_json` 上限 20 条（保留最早 1 + 最近 19）且 `source_count` 记真实总数。台账**自持** genome / 指标快照，**不依赖候选行存在**（run 可被删除） | 不合并 ⇒ 见 AL-4；依赖候选行 ⇒ 用户删掉一个旧 run，台账里一批资产的详情和「入库」按钮同时失效，而删除是完全合法的操作 |
| **AL-12** | 体检窗口的 `instId` / `bar` / `horizon` **一律取该资产自己的**（登记时落库），**不得**取当前活跃 run 的；取数按 `(instId, bar)` 分组，一组一次 | 不同 run 可以是不同标的 / 不同周期。用错了就是拿另一个标的的数据给这条资产打分，**而且不报错**——指标照样算得出来，只是全是错的 |

---

## 一、概述（Overview）

前两轮把「策略与因子挖掘」这台机器造了出来：迭代 1 交付因子轴的生成—审查—验证三步闭环、
五维演化、FSA 频繁子树规避、11 条硬编码联合过滤与 24/7 断点续跑；迭代 2 补齐
`strategy_param` / `factor_combo` / `strategy_combo` 三条策略轴与一条路径依赖的回测车道，
`IMPLEMENTED_AXES == ALL_AXES`。用户需求四条里的前两条（「Agent 自动挖掘并自回测、按结果决定
是否保留」「可 24 小时长期运行」）与第四条（「因子策略 / 常规策略 / 组合策略均可」）已经兑现。

**第三条没有兑现完**：「相关记录要有，能够进行回溯和管理」。今天的成果只活在**单个 run 的
上下文里**——唯一入口是 `GET /discovery/runs/{runId}/candidates`，同一条表达式在三个 run 被挖到
就是三条互不相干的记录，run 一停、一删，成果就散了。更要命的是**入库之后就断了**：
`promote` 建完 `factors` / `strategies` 行，发现层再也不管它。而因子是会衰减的——这正是
参考报告自己承认的头号局限（信号源高度集中、有效搜索仅 3 天、样本外持续性未经检验）。
现状里唯一沾边的机制是 `revalidateOnRefresh` 触发的 `rebuild_kept_pool`，但它**只在该 run
还在跑的时候、只对该 run 的 kept 池、只重算 F2/F3/F6 三条轻量闸**，run 停了就彻底停了。
用户拿到的是一张「三天前的体检报告」，而界面上没有任何东西告诉他这份报告已经过期。

本轮交付两样东西，合起来把「挖到」变成「管得住」。**其一是成果资产台账**：资产
（factor / factor_combo / strategy_param / strategy_combo 四类）升格为跨 run 的第一公民，
按语义键归一，自持 genome、冻结方向、挖掘窗口口径、登记时的完整指标与阈值，携带全部溯源
（哪个 run、哪一轮、什么演化来源找到的），并在候选被判 kept 的那一刻就登记——不必等用户点入库。
**其二是持续样本外体检**：一个独立于 run 生命周期的后台守护，周期性地在**挖掘窗口右端之后
新产生的那一段数据**上，用**与挖掘完全同一把尺子**重评每个资产，产出健康快照时间序列、
留存度（retention）与带迟滞的四态健康状态机。这是本项目里唯一能提供**真样本外**证据的机制：
挖掘时的 OOS 段是回溯样本外（数据在挖掘时已经存在），而体检窗口是时间给的真样本外
（数据在挖掘时还不存在，任何形式的过拟合都无法触及它）。

设计姿态上，本轮把「诚实」放在「好看」前面：新增样本不够就判 `unknown` 而不是给一个
勉强的绿灯；一次网络抖动不写进健康时间线；降级只标注不代劳；导出的是能在另一台机器上
复现每个数字的自包含快照。本轮**零 DB 迁移**（两张全新表，`create_all` 加法建表，不动任何
既有表、不加任何既有列）、**零新增 WebSocket**（体检是小时级低频，轮询足够，D-21）、
**发现层与交易层依然零 import**（`test_discovery_import_boundary.py` 的机器判据继续守门）。

---

## 二、技术设计

### 2.1 总体架构与数据流

```
                       ┌──────────── 既有（不改行为） ────────────┐
  DiscoveryRun 循环 ──▶ Lane.absorb ──▶ repo 落候选行(kept) ──────┤
                       └────────────────────┬────────────────────┘
                                            │  ★ 新增登记钩子（AL-5，fail-open + 计数）
                                            ▼
                                   assets.register_kept()
                                   ├─ identity_key(kind, …)   ← 与 promote 同源（AL-4）
                                   ├─ upsert：新建 / 合并溯源（AL-11）
                                   └─ next_check_ts = 挖掘窗口右端 + minFreshBars×step
                                            │
   ┌────────────────────────────────────────┴───────────────────────────────────┐
   │                        discovery_assets（新表 · 跨 run）                     │
   └────────────────────────────────────────┬───────────────────────────────────┘
                                            │
   HealthGuard（新守护协程，main.py lifespan）│ 每 tick：
   ① 双闸判定（AL-7）                        │
   ② 选到期资产 batch（archived=0 且 next_check_ts<=now，按 next_check_ts 升序）
   ③ 按 (instId,bar) 分组，一组一次取数（AL-12）
   ④ 逐资产体检：                            ▼
        因子 → validation.evaluate_split(ctx{split_at, frozen_direction})   ← AL-1/AL-2/AL-3
        策略 → backtest_lane.run_split_backtest(assembler, LaneContext{split})
   ⑤ health.grade(...) → 状态机 + 迟滞（AL-8）
   ⑥ 写 discovery_asset_checks 一行 + 更新资产聚合列
                                            │
   ┌────────────────────────────────────────┴───────────────────────────────────┐
   │                     discovery_asset_checks（新表 · 有界保留）                 │
   └────────────────────────────────────────┬───────────────────────────────────┘
                                            │  REST（无 WS）
                    ┌───────────────────────┴────────────────────────┐
                    │  前端「成果资产」分区（第 6 个分区）              │
                    │  台账表 / 筛选 / 健康徽标 / 详情抽屉 / 体检折线   │
                    │  动作：立即体检 · 归档 · 备注 · 入库 · 导出       │
                    └────────────────────────────────────────────────┘
```

三条边界纪律，与两份上游一字不改地继承：

1. **发现层永不下单**——新增模块一律不得 import `live_auto` / `ai_auto` / `trading` /
   `trade_client` / `paper`，由 `test_discovery_import_boundary.py` 的 AST 门禁逐模块守。
2. **验证不由 LLM 说了算**——本轮**完全不调用 LLM**。健康判级是纯确定性规则（AL-3）。
3. **绝不合成数据**——取数失败即退避重试，连续失败即整组跳过并在守护状态上如实标注，
   绝不用旧帧、插值或任何"近似"的行情把体检跑完。

### 2.2 资产身份、登记与溯源合并（AL-4 / AL-5 / AL-11）

#### 2.2.1 身份键与 promote 同源（AL-4 · 承重）

`promote.py` 今天有三处幂等查找，各自内联了一个比较键：

| 位置 | 现状比较键 |
|---|---|
| `_find_existing_factor` | `params_json["expr"]` 与候选 `expr_text` **逐字相等** |
| `_find_existing_strategy` | `template_key` 相等 **且** `canonical_json(params_json)` 相等 |
| `_find_existing_set` | 成员集合 `[factorKey, params, direction]` 排序后的 `canonical_json` |

**实现要求**：把这三个键抽成 `promote.py` 的模块级纯函数，原 `_find_existing_*` 改为调用
它们；`assets.identity_key` 也调用同一批函数。其中 `strategy_match_key` 与
`member_set_match_key` 是**纯提取**（逐字复刻现状比较键，行为不变）；
**`factor_match_key` 不是**——见下面的红框。

> ### ⚠️ 因子轴的键是一次**有意的语义变更**（评审 P0-3 · 承重）
>
> `_find_existing_factor` 现状比的是 `params_json["expr"]` 与 `expr_text` 的**逐字符串相等**；
> 而语义身份要的是**规范式哈希**（`expr_hash` 对交换律算子给出同哈希）。两者对
> `add(a,b)` / `add(b,a)` 给出**相反**答案，不可能同时成立。本轮**采纳规范式哈希**，理由是
> AL-4 的危害（同一个因子在台账里占两行、其中一行永远显示「未入库」）比"入库幂等更宽松"
> 的代价大得多。随之而来的三条必须落实，**一条都不能省**：
>
> 1. `_find_existing_factor` 改为：先按 `factor_match_key(row_expr) == factor_match_key(expr_text)`
>    比；**历史脏数据兜底**——任一侧 `parse_expression` 抛错时，该行回落到逐字比较并 `logger.debug`
>    留痕（**绝不**让一条解析不了的历史行把整次入库炸掉）；
> 2. R-8 的「`test_discovery_promote.py` 零变更全绿」在因子轴上**不再成立**，改为
>    「零变更全绿 **+ 新增一条** `add(a,b)` 与 `add(b,a)` 复用同一 `factors` 行的用例」；
>    策略轴与组合轴的两个套件仍是零变更全绿（那两处确实是纯提取）；
> 3. 这条变更记入 **D-27**，实现节点不得反悔改回逐字比较——改回去等于 AL-4 空转。

```python
# discovery/promote.py（新增三个模块级纯函数，_find_existing_* 改为调用它们）
def factor_match_key(expr_text: str) -> str:        # 规范式 → 稳定哈希（见上方红框：语义变更）
    return expr_hash(parse_expression(expr_text))

def strategy_match_key(template_key: str, params: dict) -> str:
    return canonical_json([str(template_key), dict(params)])

def member_set_match_key(members: list[dict]) -> str:
    return canonical_json(sorted(
        [[str(m.get("factorKey") or ""), dict(m.get("params") or {}),
          str(m.get("direction") or "long")] for m in members],
        key=canonical_json))
```

```python
# discovery/assets.py
_PREFIX = {"factor": "f", "factor_combo": "c",
           "strategy_param": "s", "strategy_combo": "r"}

def identity_key(kind: str, *, expr_text: str = "", genome: dict | None = None) -> str:
    """跨 run 的资产语义身份（≤48 字符，落 `discovery_assets.asset_key` 唯一索引）。"""
    genome = genome or {}
    if kind == "factor":
        raw = promote.factor_match_key(expr_text)
    elif kind == "strategy_param":
        raw = promote.strategy_match_key(genome.get("templateKey") or "",
                                         genome.get("params") or {})
    elif kind == "factor_combo":
        raw = promote.member_set_match_key(promote.combo_members(genome))
    elif kind == "strategy_combo":
        raw = promote.strategy_match_key(promote.ROTATION_TEMPLATE_KEY,
                                         promote.rotation_params(genome))
    else:
        raise ValueError(f"未知搜索轴：{kind}")
    return f"{_PREFIX[kind]}:{hashlib.sha1(raw.encode()).hexdigest()[:32]}"
```

> `promote.combo_members` / `promote.rotation_params` 由现有 `_combo_members` 与
> `_promote_rotation` 内联的那段参数装配**提取**而来（同样是纯提取，不改语义）。
> `strategy_combo` 复用 `strategy_match_key` 是刻意的：它入库后就是一条
> `regime_rotation` 策略，身份必须与 `_promote_rotation` 的幂等键一致，否则「入库过的
> 轮动组合」在台账里会显示成未入库。已核 `combo_specs.validate_combo_params` 只校验不改写
> （返回 `None`、不动入参），因此 `rotation_params(genome)` 可以是一个纯函数，与
> `_promote_rotation` 里被送去比对的那份 `params` 逐字节相同。

> ### `factor_combo` 的身份**不含权重**——已知且有意（评审 P1-7）
>
> 已核 `_find_existing_set` 的幂等键只有 `(factorKey, params, direction)`，**不含 `weight`**。
> 于是「同一组成员、不同权重」的两条挖掘成果会拿到**同一个 `asset_key`**，按 AL-11 合并成
> 一行。这与 promote 同源（AL-4 优先级更高），但代价必须显式承接，否则第二条的真实基因
> **不落任何地方**、此后所有体检都在评第一份权重：
>
> - `sources_json` 的每条溯源**必须**带 `genomeHash` 与 `acceptSummary`
>   （`{isRankIc | oosSharpe, trials}`，≤4 个标量），使被合并掉的变体仍可回溯；
> - 资产行上新增派生只读字段 `variantCount = len({source.genomeHash})`；`>1` 时详情抽屉
>   顶部固定一句「本资产合并了 N 组不同权重的成果，体检口径取首次登记的那一组」；
> - `test_discovery_assets_identity.py` 增一条：同成员不同权重 ⇒ 一行、`variantCount == 2`、
>   `genome_json` 保持首次那份。

**门禁（放行条件）**：`test_discovery_assets_identity.py` 用 `monkeypatch` 把
`promote.strategy_match_key` 换成返回常量的桩，断言 `identity_key("strategy_param", …)`
的输出随之改变。这是「一个实现两个调用点」的行为判据——比注释和 AST 结构断言都可靠
（上游迭代 1 的 `rebuild_kept_pool` 单一实现门禁用的是同一招）。

#### 2.2.2 登记点与 fail-open（AL-5）

**插入点必须精确到行**：`iteration.IterationRunner._commit` 里，`await self._persist(...)`
返回之后、`self._emit(...)` 之前（现状是 `iteration.py:226-229`）。三条理由，缺一都会出错：

- **必须在 `_persist` 之后**：`repo.commit_iteration` 才是候选行真正落库的地方，而
  `sources_json` 要记 `candidateId`——在此之前候选还只是 `repo.CandidateRow` 数据类，没有 id。
- **绝不能放进 `_persist` 内部**：`_persist` 带一次有界重试（`iteration.py:284-299`），
  登记若在重试圈内抛错，会连带把**已经提交成功的** `commit_iteration` 再跑一遍。
- **必须在 `_emit` 之前**：这样两个计数能搭上同一轮的 `state` 事件（见下）。

`commit_iteration` 只回 `{"prunedCandidates", "prunedIterations"}`、**不回候选 id**
（已核源码），因此 id 由 `assets.register_kept` 自己按 `(run_id, genome_hash)` 反查一次
（该组合有唯一索引 `uq_discovery_candidate`，一条 `SELECT id, genome_hash … WHERE run_id=?
AND genome_hash IN (…)` 即可，**不改** `commit_iteration` 的签名与行为）。

**签名必须显式带上挖掘窗口**（评审 P1-1）：v1 写的是 `register_kept(run.spec, …)`，但已核
`runtime.RunSpec` 是 frozen dataclass，字段只有
`(run_id, inst_id, bar, symbols, config, rest, settings, resume, llm_client)`——**既没有
`checkpoint`**（`startTs` / `endTs` 在 `run._checkpoint.data_window`），**也没有 df**
（`splitTs` 要 ts 数组）。因此挖掘窗口由 `_commit` 侧组装后显式传入：

```python
# discovery/assets.py
@dataclass(frozen=True)
class MinedWindow:
    start_ts: int; end_ts: int; is_ratio: float; window_bars: int; split_ts: int

    @staticmethod
    def of(run) -> "MinedWindow":
        """从运行时组装（**唯一**组装点，避免第二份口径）。"""
        window = dict(run._checkpoint.data_window)
        is_ratio = float(run.spec.config.get("isRatio", 0.7))
        ts = run._df["ts"]
        cut = validation.split_index(len(ts), is_ratio)
        return MinedWindow(start_ts=int(window.get("startTs") or 0),
                           end_ts=int(window.get("endTs") or 0), is_ratio=is_ratio,
                           window_bars=int(run.spec.config.get("windowBars", 4000)),
                           split_ts=int(ts.iloc[min(cut, len(ts) - 1)]) if len(ts) else 0)
```

```python
# discovery/iteration.py::_commit（本轮新增 ~15 行，紧接 pruned 计数之后）
kept_rows = [row for row in rows if row.verdict == "kept"]
if kept_rows:
    try:
        stats = await assets.register_kept(run.spec, rows=kept_rows,
                                           window=assets.MinedWindow.of(run))
        run._counters["ledgerRegistered"] = (
            int(run._counters.get("ledgerRegistered", 0)) + stats.created + stats.merged)
    except Exception as exc:            # noqa: BLE001 —— 台账是观测面，绝不拖垮迭代
        run._counters["ledgerErrors"] = int(run._counters.get("ledgerErrors", 0)) + 1
        logger.warning("asset ledger register failed for run %d iter %d: %s",
                       run.run_id, index, exc)
self._emit(iteration_row, rows)
```

`register_kept` 自开一次 `get_sessionmaker()` 会话（与 `_persist` 同姿态），**不复用**
已提交的那个。

**两个计数的落点**（AL-5 后半句：*只计数不呈现等于没计数*——上游迭代 2 终审 RV-1 记录过的
同型缺陷）：

- 它们写进 `run._counters`，因此**同轮**就随 `_emit` 推的 `state` 事件下发
  （`runtime.state_snapshot()["counters"]` 是 `dict(self._counters)` 的整份拷贝，已核）；
- 落库晚一轮——`counters_json` 是在**下一轮**的 `commit_iteration` 里写的。这是有意接受的
  代价（换取不动 `commit_iteration` 的单事务语义），必须在实现注释里写明，免得后人看到
  「刚登记完刷新页面计数是 0」以为是 bug；
- **不进** `DiscoveryIteration.stats_json`——那一行在登记发生之前就已经组装并落库了，
  硬塞进去要么得改 `commit_iteration` 的事务，要么得补一条 UPDATE，两者都不值得。
- 前端落点：`DiscoveryMonitor.tsx` 顶部计数条 + 总览「成果资产」卡（§2.10）。

登记内容（全部来自候选行与 run 配置，**不依赖任何外部查询**）：

| 资产列 | 取值 |
|---|---|
| `asset_key` | `identity_key(kind, expr_text=…, genome=…)` |
| `kind` / `expr_text` / `genome_json` / `describe_text` / `mechanism` | 候选行同名字段 |
| `inst_id` / `bar` | **run 的**（AL-12：资产自持，日后体检据此取数） |
| `horizon` | 因子轴取**候选自己的** `metrics_json["horizon"]`（已核 `validation._build_metrics` 恒写入该键），缺失才回落 `run.config["horizon"]`；策略轴 0。**不直接读 run 配置**（评审 P2-7）：run 配置可在两次登记之间被用户改掉，那样登记的口径就与候选实际被验证的口径不一致 |
| `direction` | 因子轴取 `metrics_json["direction"]`（**冻结**，AL-1 的输入）；策略轴 `""` |
| `mined_window_json` | `asdict(MinedWindow.of(run))`，即 `{"startTs","endTs","isRatio","windowBars","splitTs"}`（见上方 P1-1 的组装点） |
| `accept_metrics_json` | 候选行 `metrics_json` **原样**（已核两条车道都产出 `{"is": …, "oos": …}` 双段结构，判级分母据此取） |
| `accept_thresholds_json` | 因子轴 `validation.normalize_thresholds(run.config["thresholds"])`；策略三轴 `strategy_gates.normalize_strategy_thresholds(run.config["strategyThresholds"])`（AL-3 第二句）。**键名逐轴写死**（评审 P1-10）：已核 `service.py` 归一化后因子阈值在 `thresholds`、策略阈值在 **`strategyThresholds`**，取错了 `normalize_strategy_thresholds` 会把不认识的键**全部丢掉、静默回落默认值**，AL-3 第二句整条空转。门禁：`test_discovery_assets_register.py` 造一个 `strategyThresholds` 全非默认值的 run，断言策略资产的 `accept_thresholds_json` 与之逐键相等（而不是等于 `DEFAULT_STRATEGY_THRESHOLDS`） |
| `trials_at_accept` | 候选行同名字段 |
| `label` | `promote.build_factor_name(...)` / `promote._strategy_name(...)` 同源的展示名 |
| `next_check_ts` | `minedWindow.endTs + minFreshBars × bar_to_seconds(bar)`（攒够新数据才第一次体检） |
| `health_status` | `"fresh"` |

#### 2.2.3 溯源合并（AL-11）

`asset_key` 已存在时**只做加法**：

- `last_found_ts = max(旧, 新)`；`first_found_ts` **不动**；`source_count += 1`；
- `sources_json` 追加
  `{"runId","candidateId","iterationIdx","foundTs","source","genomeHash","acceptSummary"}`；
  后两个字段是评审 P1-7 的要求：身份键在 `factor_combo` 上不含权重，被合并掉的变体只有
  在这里才留得下痕迹；`acceptSummary` ≤4 个标量（因子 `{isRankIc, isIr, trials}`、
  策略 `{oosSharpe, oosTotalReturn, trials}`），**不得**把整份 metrics 塞进来（本列有界）；
  超过 20 条时保留**最早 1 条 + 最近 19 条**（首次发现是溯源里最有价值的一条，绝不能被挤掉）；
- `accept_metrics_json` / `accept_thresholds_json` / `mined_window_json` / `direction`
  **一律保持首次登记的那份不动**。理由：这三样是判级的分母与口径（AL-3），换了分母，
  健康时间线在那个时点会出现一根与市场无关的断崖；
- `promoted_*` / `archived` / 健康列一律不动。

**run 被删除后**：`discovery_candidates` 行随之消失，`sources_json` 里的 `runId` /
`candidateId` 悬空。资产行**完全不受影响**（自持 genome 与指标快照）。**悬空判定一律由
服务端算**（评审 P2-4）：`GET /discovery/assets/{id}` 在组装 `sources` 时一次
`SELECT id FROM discovery_runs WHERE id IN (…)` 得到现存集合，逐条下发 `runExists: bool`；
前端**不得**自己再拉一次 `GET /discovery/runs` 做比对（两套判据迟早不一致，而且分页后的
run 列表根本不全）。前端只按 `runExists` 渲染：为假时显示灰字「任务已删除」且**不渲染**
跳转链接。

### 2.3 体检窗口口径：什么才算「真样本外」（AL-2 / AL-12 · 承重）

```
    minedWindow.startTs   minedWindow.endTs      freshStartTs             checkEnd
    │                     │                      │                        │
    ├─────── 挖掘窗口 ────┤                      │                        │
    │ (IS 段)  (回溯 OOS) │                      │                        │
    │                     ├───── 挖掘之后新产生的数据 ─────────────────────┤
    │                     │                      │                        │
                  ├─ 暖机段 warmupBars 根 ───────┤─── fresh 段（判级唯一依据）─┤
                  ↑ checkStart_i = freshStartTs − warmupBars × step
    ├────────── 组取数窗口 [checkEnd − windowBars×step, checkEnd]（恒定长度）─┤
```

- `step = bar_to_seconds(asset.bar)`；
- `checkEnd = 已确认根对齐的 now`（与 `runtime._window_bounds` 同一口径：
  `int(time.time()) // step * step - step`）；
- `warmupBars = discovery_health_warmup_bars`（默认 400，配置校验强制 ≥
  `discovery_health_min_warmup_bars`）；
- `maxFreshBars = discovery_health_window_bars − warmupBars`（默认 1500 − 400 = 1100）；
- **`freshStartTs = max(minedWindow.endTs, checkEnd − maxFreshBars × step)`** ← 承重的那一行；
- 组取数窗口 `[checkEnd − discovery_health_window_bars × step, checkEnd]`；
- `split = bisect_left(ts_array, freshStartTs)`（第一根 `ts >= freshStartTs` 的下标）；
- `freshBars = len(df) − split`；`warmupBarsActual = split`。

**为什么 `freshStartTs` 必须再夹一次上界**（评审 P0-1 · 承重）：v1 写的是
`freshStartTs = max(minedEndTs, checkStart)`，而 `checkStart` 又是**从 `now` 固定回看**
`windowBars` 根。于是资产一旦老过 `windowBars × step`（15m 下约 **15.6 天**），`minedEndTs`
就落在 `checkStart` 左边 ⇒ `freshStartTs == checkStart ==` 取数窗口第一根 ⇒ `split == 0`
⇒ `warmupBarsActual == 0 < 300` ⇒ **H2 恒触发、资产永久 `unknown`**。按 D-26，`unknown`
用中性色文案「暂不评级」——界面上**看不出任何异常**，挖了三天的东西过两周集体静音，
这正是 §〇 所说"一条错误的健康比崩溃危险得多"的最坏形态。夹上界之后：① 暖机段恒有
`warmupBars` 根；② fresh 段不会随时间无界增长（一年后拿一年数据当"新样本"是另一种
错口径，而且单次体检的墙钟会随之爆掉 `discovery_health_asset_timeout_s`）。

**组取数长度恒定的证明**（供实现者放心：夹上界不会把取数量放大）：
`freshStartTs ≥ checkEnd − maxFreshBars × step` 恒成立（它就是那个 `max` 的下界），故
`checkStart_i = freshStartTs − warmupBars × step ≥ checkEnd − (maxFreshBars + warmupBars) × step
= checkEnd − windowBars × step`。即**组内任一资产的窗口左端都不会越过**组取数窗口左端，
一组一次取 `windowBars` 根**永远够用**，与 v1 的取数量完全相同。

**取数与切片的分工（评审 P1-2 · 承重）**：一个 `(instId, bar)` 组**只取一次**、
组内**只有一份 `df_full` 对象**；每个资产的差异**全部**经 `split_at` 表达，
**绝不**给不同资产传不同的 df 切片。理由是已核的 `validation._context_of`：它用**固定键**
`__discovery_field_context__` 把 `build_context(df_full)` 缓存在 `memo` 上、**不校验 df 身份**。
一旦同一个 `memo` 见到第二份 df，第二个资产就会在**别人的字段上下文**上求值——不抛错、
指标照算、全是错的。配套纪律：`memo` **一组一份，跨组必须新建**；机器判据见 §7.1
（`test_discovery_health_runtime.py`：两个组各含一条相同表达式的资产，第二组的
`coverage` / `rankIc` 必须与它单独跑时逐位相等）。

**三条不可判定即 `unknown`**（fail-closed 到 `unknown`，不是 fail-closed 到 `decayed`——
数据不足不是资产的错）：

| 码 | 条件 | 含义 |
|---|---|---|
| **H1** | `freshBars < discovery_health_min_fresh_bars`（默认 120）或 fresh 段配对样本 < `minSamples × 0.2` | 新增样本不足 |
| **H2** | `warmupBarsActual < discovery_health_min_warmup_bars`（默认 300） | 暖机段不足。**夹上界之后这条只可能由取数缺口触发**（正常情况恒有 `warmupBars=400` 根），因此它同时是一条数据质量告警 |
| **H3** | **fresh 段**有限值占比 < `thresholds["minCoverage"]` | 覆盖率不足（通常是数据缺口） |

> **H3 的分母必须现算**（评审 P1-3）：`SplitEvaluation.coverage` 是**全窗**有限值占比
> （已核 `validation.py`：`float(np.isfinite(values).mean())`），把暖机段的 rolling NaN 也
> 算了进去——直接拿它当 H3 的输入，就是把暖机 NaN 的锅算到资产头上，而且暖机段越长越冤。
> 体检要的是 `float(np.isfinite(split.values[split_at:]).mean())`：从
> `SplitEvaluation.values` 上现切一刀即可，**不新写任何指标计算**（AL-3 ① 不受影响）。

**`next_check_ts` 的推进（评审 P1-5，四条分支缺一不可）**：

| 本次结果 | 下次体检时刻 |
|---|---|
| `healthy` / `watch` / `decayed` | `now + discovery_health_interval_s` |
| `unknown` | `now + max(interval, 补足 minFreshBars 所需秒数)`——资产在真的攒够数据时自然回队，而不是每个周期都来刷一条 `unknown` |
| `error` | `now + interval × 2^min(consecutiveError, 3)`（有界指数退避，上限 8×interval） |
| 取数失败（**不写** check 行，AL-8） | **不动** `next_check_ts`；由 `_market_backoff[(instId,bar)]` 控制整组的下次尝试 |

v1 只写了 `unknown` 一支。正常路径不推进的话，`list_due_assets` 的
`ORDER BY next_check_ts ASC LIMIT batch` 会让队头那 `batch` 条资产**每个 tick 被反复体检**、
其余资产永远排不上——而 `summary.guard` 里 `checkedTotal` 一路上涨、`skippedByCap` 为 0、
`lastError` 为空，**一切正常**。又一条"不报错的空转"。

> **为什么不复用当前活跃 run 已经在内存里的那份 df**：run 的窗口口径（`windowBars` /
> `windowMode` / `endTs`）由用户配置，与体检口径不同；而且资产的 `(instId, bar)` 未必等于
> 任何在跑 run 的。复用会静默地把两套口径混在一起。**不复用，一组一次独立取数**（AL-12）。

### 2.4 求值改造：`split_at` 与冻结方向（AL-1 / AL-3 · 承重）

这是全文风险最高的一处改动——它动的是四条已发布搜索轴共用的求值函数。

`validation.ValidationContext` 增两个**可选**字段（`frozen=True` 的 dataclass 加带默认值的
字段是纯加法，既有全部构造点一字不改）：

```python
@dataclass(frozen=True)
class ValidationContext:
    df_full: pd.DataFrame
    horizon: int
    is_ratio: float = 0.7
    fee_bps: float = 5.0
    slip_bps: float = 2.0
    thresholds: dict = field(default_factory=lambda: dict(DEFAULT_THRESHOLDS))
    memo: dict = field(default_factory=dict)
    # ---- 本轮新增（discovery-asset-ledger §2.4）----
    split_at: int | None = None      # None ⇒ 走 split_index(is_ratio)，行为逐位不变
    frozen_direction: str = ""       # "" ⇒ 走 resolve_direction(auto)，行为逐位不变
```

`evaluate_split` 只改两行（**签名不变**，参数不增，符合项目 ≤5 形参约定）：

```python
def evaluate_split(node: Node, ctx: ValidationContext) -> SplitEvaluation:
    work = ctx.df_full.reset_index(drop=True)
    values = evaluate(node, _context_of(ctx), ctx.memo)
    split = (ctx.split_at if ctx.split_at is not None
             else split_index(len(work), ctx.is_ratio))          # ← 改动 1
    split = max(1, min(split, max(len(work) - 1, 1)))            # 夹紧（越界即退化为端点）
    df_is, v_is = work.iloc[:split], values[:split]
    df_oos, v_oos = work.iloc[split:], values[split:]
    close_is = df_is["c"].to_numpy(dtype=float)
    direction = (ctx.frozen_direction if ctx.frozen_direction in ("long", "short")
                 else resolve_direction(v_is, close_is, "auto",
                                        primary_horizon=ctx.horizon))   # ← 改动 2
    ...                                                          # 以下逐行不变
```

**为什么这两行是承重的**（本段按评审 P0-2 重写，v1 的机理定位是错的）：
`evaluate_factor(df, values, "long")` 在 `direction` 是字面量时 `dir_mult = +1`，返回的
`rankIc` 是**原始符号**（可以为负）；而 `"auto"` 会挑出让 `rankIc` 非负的那一面。关键在于
**`auto` 是在哪一段挑的**——已核 `evaluate_split` 的实现：

```python
df_is, v_is = work.iloc[:split], values[:split]          # split == split_at（体检时 = 暖机段）
direction = resolve_direction(v_is, close_is, "auto", …) # ← 在 **IS 段 / 暖机段** 定号
```

也就是说 `auto` **不是**在 fresh 段翻面（v1 的说法），而是在 **fresh 段之前那一小截暖机
数据**上重新定号。真正的危害因此是两条，都足以让 AL-1 成为承重条款：

1. **尺子跟着行情变形**：一条已经在暖机窗口里翻了面的因子，`auto` 会顺着把方向也翻过去，
   于是 fresh 段的 `rankIc` 重新变正、H4 通过、**衰减被判成健康**——而这正是最该被抓到的
   那一类资产（它翻面翻得比体检窗口还早）；
2. **与分母不同源**：`retention` 的分母是登记时冻结的 `accept.is.rankIc`（在**当时冻结的
   方向**下算的），分子若用一把重新定号的尺子，两者根本不是同一个量的两次测量，比值没有
   任何含义——这是 AL-3「同一把尺子」在方向维度上的具体化。

- 传 `frozen_direction="long"`（= 登记时冻结的那一面）⇒ 一条反号了的因子，fresh 段
  `rankIc` 为**负** ⇒ H4 判 fail ⇒ 正确检出衰减；
- 不传（现状）⇒ 若暖机段已经翻面，`auto` 定出 `"short"` ⇒ fresh 段 `rankIc` 又是**正**的
  ⇒ 体检判健康。

**等价门禁（放行条件，两阶段落地）**：沿用上游 BL-8 的做法——**先在当前 HEAD 代码上**用
`tests/discovery_synthetic.py::trend_candles(1200, seed=5)`（已核签名：`n` 是**必填位置参数**，
v1 漏写，评审 P2-6）落一组 golden 常量（`direction` / `coverage` / `res_is.rankIc` /
`res_is.ir` / `ic_is` / `ic_oos` 六项，`round(…, 10)`），**再**做上述改动，改动后
`split_at=None, frozen_direction=""` 必须逐条相等。金常量写死在
`test_discovery_validation_split_at.py` 里，不得在测试内重新计算。

**双向门禁（放行条件，构造按评审 P0-2 改）**：`test_discovery_health_direction.py`。

> ⚠️ v1 给的构造（`two_regime_candles(is_bars=600, oos_bars=300)` + `split_at` 落在 600）
> **第 2 条断言必红**：那样切，暖机段整段都是**未翻面**的趋势段，`auto` 会定出与冻结值
> **相同**的方向，两条用例都判 `H4`。而 DoD #1 把这条列为"本轮最重要的一条"——实现节点
> 撞上一条永远变不绿的用例后，只有删测试或改判据两条路，两条路都把 AL-1 变成摆设。

正确的构造要让 **`split_at` 落进反号段的深处**，使暖机段本身已经翻面：

```python
candles = two_regime_candles(is_bars=300, oos_bars=900)   # 前 300 根趋势、后 900 根反向
df = candles_to_df(candles)
split_at = 900          # 暖机段 = 300 趋势 + 600 反向（反向占优）；fresh 段 = 末 300 根反向
```

断言：

1. `frozen_direction="long"`（挖掘时冻结的那一面）⇒ fresh 段 `rankIc < 0`，
   `health.grade(...)` 返回含 `H4` 的 fail；
2. `frozen_direction=""`（现状 auto）⇒ 定号翻成 `"short"` ⇒ fresh 段 `rankIc > 0` 且
   **不含** `H4`；
3. 两次调用的 `abs(rankIc)` **逐位相等**（证明差异 100% 来自定号，而不是别的地方混进来的
   数值噪声——没有这条，第 1、2 条同时绿也可能是因为两次跑的根本不是同一段数据）。

第 2、3 条是**必须写**的：它们证明「不冻结方向」确实会让整条防线空转，而不是我们凭空担心。
若实测发现 `is_bars` / `oos_bars` / `split_at` 的这组取值不足以让 `auto` 翻面（合成序列的
反向强度不够），**允许调这三个数**，但**不允许**把第 2 条改成"也判 H4"——那等于承认
AL-1 不承重，此时必须回本文改 AL-1。

### 2.5 判级规则与状态机（AL-3 / AL-8）

`discovery/health.py` 是**纯函数模块**（不 import 任何 async / DB / OKX 的东西，便于逐条
单测）。

#### 2.5.1 因子资产的判据

分母一律取 `accept_metrics_json["is"]`（登记时冻结），阈值一律取 `accept_thresholds_json`。
`retain = discovery_health_retain_frac`（默认 0.5）。

| 码 | 判据 | 结论 |
|---|---|---|
| H1 / H2 / H3 | 见 §2.3（H3 的分母是 **fresh 段**现算的有限值占比，不是 `SplitEvaluation.coverage`——评审 P1-3） | `unknown`（不计连续失败） |
| **H4** | fresh 段 `rankIc <= 0`（方向已冻结，非正即反号） | fail |
| **H5** | `abs(fresh.rankIc) < max(thresholds["minAbsIc"], abs(accept.is.rankIc) × retain)` | fail（强度衰减） |
| **H6** | `abs(fresh.ir) < thresholds["minAbsIr"] × retain` | fail（信息比恶化） |

> fresh 段的指标取 `evaluate_split(...).res_oos.horizons[0]`（`rankIc` / `ir` / `tStat` /
> `occurrences`）。**不另算**——AL-3 ①。

#### 2.5.2 策略资产的判据

分母取 `accept_metrics_json["oos"]`（策略轴登记时的样本外指标），阈值取策略阈值表。

| 码 | 判据 | 结论 |
|---|---|---|
| **H1** | fresh 段 `tradeCount < discovery_health_min_fresh_trades`（默认 5） | `unknown` |
| **H2** | fresh 段回测因缺高周期帧 / 装配失败而无法完成（`reject_reason` 以 `S0:` 开头） | `unknown`（并把 `S0:*` 原样落 `reasons`，可运维） |
| **H4** | fresh 段 `totalReturn <= 0` 或 `sharpe <= 0` | fail |
| **H5** | `accept.oos.sharpe > 1e-9` 时判 `fresh.sharpe < accept.oos.sharpe × retain`；分母 `<= 1e-9` 时**本条跳过**（此时 H4 已经承担了全部判定，见下） | fail |
| **H6** | `abs(fresh.maxDrawdown) > max(thresholds["maxDrawdown"], abs(accept.oos.maxDrawdown) × 1.5)` | fail（风险恶化） |

> **H5 为什么要显式跳过非正分母**（评审 P1-6）：已核 `strategy_gates`——S6 只挡"样本外
> 崩塌"，**不要求** OOS 夏普为正，所以 `accept.oos.sharpe` 完全可能 ≤ 0。此时
> `accept.oos.sharpe × retain` 是一个**更靠近 0 的负数**，判据方向被悄悄反转（登记时越差、
> 门槛越宽）；而真正该拦的情况（`fresh.sharpe <= 0`）H4 已经拦了。与其留一条方向不定的
> 判据，不如明写跳过。

> 策略资产**没有 H3**（覆盖率是因子概念）。`reasons` 字段如实只写实际触发的码，UI 不得
> 为了对齐而补占位——上游迭代 2 P2-11 定下的姿态：诚实优先于漂亮。

#### 2.5.3 留存度（retention）

比"分数"更有信息量、也更难被误读的一个数：

- 因子：`retention = abs(fresh.rankIc) / abs(accept.is.rankIc)`
- 策略：`retention = fresh.sharpe / accept.oos.sharpe`，**且仅当 `accept.oos.sharpe > 1e-9`**；
  分母 `<= 1e-9`（含**负值**）⇒ `retention = null`（评审 P1-6：v1 写的是裸比值，而策略轴的
  分母可以为负——那样"越差的 fresh 夏普给出越高的 retention"，UI 上是一条**更长的绿条**，
  且它出现在留存度排序的最前面）
- 因子分母 `abs(...) <= 1e-9` ⇒ `retention = null`（**绝不**填 0 或 1）
- 落库不夹紧（>1 是真实信息：这条资产比挖到时更强）；UI 展示时条形图按 `min(1, r)` 画长度，
  数值仍显示原值。

#### 2.5.4 状态机与迟滞（AL-8）

```python
def next_status(prev_fail_streak: int, reasons: list[str], *, fail_streak_gate: int
                ) -> tuple[str, int]:
    """(健康状态, 新的连续失败计数)。unknown / error 一律不动计数（AL-8）。"""
    unknowns = [r for r in reasons if r in ("H1", "H2", "H3")]
    fails = [r for r in reasons if r in ("H4", "H5", "H6")]
    if unknowns:
        return "unknown", prev_fail_streak
    if not fails:
        return "healthy", 0
    streak = prev_fail_streak + 1
    return ("decayed" if streak >= fail_streak_gate else "watch"), streak
```

- `unknowns` 优先于 `fails`：不可判定时**不给结论**，也不惩罚（AL-2）。
- `error`（求值 / 回测抛异常）由调用方直接写 `status="error"`，同样不动 `consecutive_fail`。
- `archived` **不进这个状态机**——它是用户动作，落独立的 `archived` 布尔列，与健康状态正交。
  已归档资产不进体检队列。

状态取值域：`fresh`（已登记未体检）| `healthy` | `watch` | `decayed` | `unknown` | `error`。

> **命名纪律（UI 必须遵守）**：run 内的 `DiscoveryCandidate.demoted` 与本轮的资产级
> `decayed` 是**两个不同的概念**（前者是该 run kept 池内的轻量复评淘汰，后者是跨 run 的持续
> 样本外衰减）。中文必须分开：run 内叫**「本轮已淘汰」**，资产级叫**「已衰减」**，且资产详情
> 里给一句话解释。两个词混用会让用户以为系统前后矛盾。

### 2.6 体检守护 `HealthGuard`（AL-6 / AL-7 / AL-9）

`discovery/health_runtime.py`，单例 + `get_health_guard()`，由 `main.py` 的 lifespan 起停
（镜像既有 `_weight_train_sweeper` 的 cancel-safe 姿态）。

```
async def _tick(self) -> None:
    settings = get_settings()                                     # ① 每 tick 重读（AL-7）
    if not (settings.discovery_enabled and settings.discovery_health_enabled):
        self._state.update(enabled=False); return                 #    零外部副作用
    due = await repo.list_due_assets(session, now=now,
                                     limit=settings.discovery_health_batch)   # ②
    for (inst_id, bar), group in _group_by_market(due):            # ③ AL-12
        try:
            candles, frames = await window_loader.load(...)        #    一组一次取数
        except (OKXError, HTTPException, asyncio.TimeoutError, OSError) as exc:
            self._note_data_failure(inst_id, bar, exc)             #    AL-8：不写 check 行
            continue                                               #    指数退避，整组跳过
        for asset in group:
            if self._backtests_used >= cap:                        # ④ AL-9：按底层次数计
                self._skipped += 1; logger.info("health tick capped …"); break
            await self._check_one(asset, candles, frames)
```

要点：

1. **双闸每 tick 重读**（AL-7）。启动时若闸已关，仍然创建协程但每 tick 直接返回——
   这是对上游 run manager「关闸就不起协程」姿态的**有意偏离**，理由：守护没有「在跑的任务」
   这个概念、无需启动对账，每 tick 判定既做到了零外部副作用（不取数、不写库、不调 LLM），
   又让运行期切换开关立刻生效、且可被单测确定性驱动。这条偏离记入 D-24。
2. **到期选取**：`WHERE archived = 0 AND next_check_ts <= :now ORDER BY next_check_ts ASC
   LIMIT :batch`，走 `ix_discovery_assets_due(archived, next_check_ts)` 索引。
3. **分组取数**（AL-12）：按 `(inst_id, bar)` 聚合；高周期帧按该组内**策略资产所需
   timeframes 的并集**一次取全（复用 §2.7 的 `window_loader`）；组内没有策略资产就不取帧。
   **组内共用同一份 `df_full` 与同一份 `memo`，per-asset 差异全部经 `split_at` 表达；
   `memo` 跨组必须新建**（§2.3 承重纪律，评审 P1-2）。
4. **资源闸**（AL-9）：`self._backtests_used` 累加的是**底层 `run_backtest` 次数**，
   预估口径 **`2 × assembler.backtests_per_segment()`**（因子资产计 0），资产跑完后按
   `SplitBacktest.backtests` 的真实值回冲差额；触顶即 `break` 并 `log` + 计数
   `skippedByCap`，该计数出现在 `GET /discovery/assets/summary` 的 `guard` 段。
   **乘 2 不能省**（评审 P1-4）：已核 `backtest_lane.run_split_backtest` 无条件跑 IS + OOS
   **两段**（gated 返回 `backtests=2`、combo 返回 `2 × per_segment`）。按 `per_segment` 计，
   护栏放行量正好是设计值的两倍，而表现是"从没触发过闸"——看起来完全正常。
5. **单资产超时**：`asyncio.wait_for(run_in_threadpool(...), timeout=asset_timeout_s)`；
   超时写 `status="error"`, `error="timeout"` 的 check 行（**是**资产自身的问题，写；
   与取数失败**不写**形成对照，AL-8）。
6. **失败隔离**：任一资产抛异常只影响它自己，`except Exception` 收口为 `status="error"` +
   `error=f"{type(exc).__name__}:{exc}"[:256]`，**绝不**吞掉后续资产。
7. **写库**：一次 `INSERT` check 行 + 一次资产聚合列 `UPDATE`（`health_status` / `retention` /
   `consecutive_fail` / `last_check_ts` / `next_check_ts` / `checks_count` / `last_reasons`），
   同一事务提交；随后按 `discovery_health_max_check_rows` 剪最旧并累加 `checks_pruned`
   （**绝不静默截断**，沿用上游迭代记录的保留纪律）。`next_check_ts` **四条分支**按 §2.3 末
   的表推进——正常路径不推进就等于队头 `batch` 条资产每 tick 反复体检、其余永远排不上
   （评审 P1-5）。

8. **一次体检付两段回测的代价是有意接受的**（D-28）：判级分母取的是登记快照，理论上
   `is_metrics` 用不上，但 `run_split_backtest` 是 AL-3「同一把尺子」的载体，为省一半算力去
   写一个"只跑 OOS"的分支，等于在本轮引入第二条求值路径——上游反复吃过的"估计量漂移"
   就是这么来的。代价已计入 AL-9 的 `2 ×` 口径与 R-1 的默认值。

守护自身状态（内存，不落库，供 `summary` 端点回显）：
`{"enabled", "running", "lastTickTs", "lastError", "checkedTotal", "skippedByCap",
"dataFailures", "assetLimitHits", "inFlight": [assetId…]}`（`assetLimitHits` 是
`DISCOVERY_ASSET_LIMIT` 的计数落点——它**永不**作为 HTTP 错误抛出，§4.10）。

### 2.7 取数辅助 `window_loader`（一个实现，两个调用点）

`promote._load_backtest_window` 今天已经在做「取基础区间 K 线 + 该候选所需的高周期帧」，
体检需要一模一样的东西。**不得复制**——把它**平移**到新模块 `discovery/window_loader.py`：

```python
async def load_window(rest, *, inst_id: str, bar: str, start_ts: int, end_ts: int,
                      timeframes: list[str], settings) -> tuple[list, dict]:
    """基础 K 线 + 高周期帧（缺帧由调用方的 preflight fail-closed 拦下）。"""
```

`promote._load_backtest_window` 改为 3 行的薄包装（`_required_timeframes(assembler)` 留在
`promote.py`），既有 `test_discovery_promote_strategy.py` 全绿即是这次平移的回归网。
`health_runtime` 直接调 `load_window`。

**门禁（放行条件）**：`monkeypatch` 掉 `window_loader.load_window` 后，一键回测端点与
体检守护**两条路径都必须失败**——证明确实只有一个实现。

### 2.8 立即体检、归档、备注、从台账入库

- **立即体检** `POST /discovery/assets/{id}/check`：同步跑一次（复用 `_check_one` 的同一实现），
  完成后返回新写入的 check 行。与守护的互斥用**同步预占**（`HealthGuard.claim(asset_id)`，
  无 `await`，杜绝 TOCTOU——上游 H-B3 / 评审 P1-3 已经吃过一次亏），占用中返回 409
  `DISCOVERY_HEALTH_BUSY`；`discovery_enabled` 为假返回 403（与既有写类端点一致）。
  **它是一个可能挂 45 秒的同步请求**（取数 + `discovery_health_asset_timeout_s`，评审 P2-5）：
  前端 `discoveryAssetApi.checkAsset` 的超时必须设为 `asset_timeout_s + 20s` 且**不重试**
  （重试只会撞 409），按钮进 loading 并显示「正在用最新数据重评，最长约 1 分钟」；
  `claim` 必须在 `finally` 里释放，包括请求被客户端断开的情况。
- **归档 / 取消归档** `POST …/archive` / `…/unarchive`：只改 `archived` / `archived_ts`。
  归档不删数据、不动实体、不影响已入库的 factor / strategy 行（AL-6）。
- **备注** `PATCH …`：`note` ≤256 字符，服务端截断。
- **从台账入库** `POST …/promote`：**不经候选行**（run 可能已删，AL-11）。做法是把
  `promote.promote_candidate` 拆成两层——纯提取，不改语义：

```python
@dataclass(frozen=True)
class PromoteSource:
    kind: str; genome: dict; expr_text: str; metrics: dict
    mechanism: str; genome_hash: str; run_id: int; iteration_idx: int

async def promote_source(session, src: PromoteSource) -> tuple[str, int, int | None]: ...

async def promote_candidate(session, candidate_id: int) -> dict:
    ...  # 读行 → 组 PromoteSource → promote_source(...) → mark_promoted → payload
```

  资产路径自己组 `PromoteSource`（字段全在资产行上），入库后把 `promoted_kind` /
  `promoted_id` / `companion_strategy_id` 写回**资产行**；若溯源里的候选行仍存在，
  一并 `mark_promoted`（两边一致，台账为资产视图的权威）。
  实体级幂等由既有 `_find_existing_*` 保证：两条路径重复入库拿到的是同一个实体 id。

### 2.9 导出（AL-10）

`GET /discovery/assets/export?ids=1,2,3`（`ids` 为空 = 按当前筛选条件全量，硬上限 500 条）：

```json
{
  "schemaVersion": 1,
  "exportedTs": 1785000000,
  "generator": "aragon-discovery-asset-ledger/1",
  "assets": [{
    "assetKey": "f:9c1e…", "kind": "factor", "instId": "BTC-USDT-SWAP", "bar": "15m",
    "horizon": 6, "direction": "long",
    "exprText": "sub(ma(overnight,60),delta(amplitude,5))",
    "genome": {...}, "describe": "…", "mechanism": "gap_premium",
    "minedWindow": {"startTs":…, "endTs":…, "isRatio":0.7, "windowBars":4000, "splitTs":…},
    "acceptMetrics": {...}, "acceptThresholds": {...}, "trialsAtAccept": 812,
    "sources": [{"runId":3,"candidateId":91,"iterationIdx":47,"foundTs":…,"source":"mutate",
                 "genomeHash":"7f3a…","acceptSummary":{"isRankIc":0.044,"isIr":0.62,
                                                        "trials":812}}],
    "sourceCount": 2, "variantCount": 1, "backfilled": false,
    "promoted": {"kind":"factor","entityId":18,"companionStrategyId":null},
    "health": {"status":"watch","retention":0.61,"consecutiveFail":1,"lastCheckTs":…},
    "checks": [{"checkedTs":…,"status":"healthy","reasons":[],"retention":0.94,
                "freshBars":288,"windowStartTs":…,"windowEndTs":…,
                "metrics":{"fresh":{…},"history":{…}}}]
  }]
}
```

响应头 `Content-Disposition: attachment; filename="aragon-discovery-assets-<ts>.json"`。
**禁止出现**的字段（须有一条机器判据的测试）：任何 `apiKey` / `secret` / `passphrase` /
`accountId` / 本机绝对路径。本轮**不做导入**（开放问题 3）。

### 2.10 前端信息架构与交互

导航从 5 个分区扩到 **6 个**，新分区排在「成果库」之后：

| 分区 | 说明 |
|---|---|
| 总览 / 挖掘任务 / 实时监控 / 候选库 | 不变 |
| 成果库 | **保持现状不动**（run 内视图），仅在标题栏加一个「跨任务查看 →」跳转 |
| **成果资产**（新） | 跨 run 台账：筛选 + 表格 + 健康徽标 + 详情抽屉 + 批量归档 + 导出 |

**为什么不把「成果库」直接改造成台账**：① 两者语义确实不同（「本轮成果」vs「全局资产」），
用户在长跑中最常问的两个问题分别对应这两个视图；② 零回归——`DiscoveryLibrary.tsx` 是已发布
且已终审的组件，改造它意味着重跑它全部的交互假设。代价是导航项 5→6，仍在可读范围。

**成果资产分区**（`DiscoveryAssets.tsx`）：

- 顶部筛选条（`DiscoveryAssetFilters.tsx`）：搜索框（描述 / 表达式模糊匹配）、轴、健康状态、
  入库与否、归档与否、标的 / 周期、排序（首次发现 / 最近体检 / 留存度 / 入库时 IS 强度）。
  筛选状态**不进 store 持久化**（会话内 `useState` 即可），避免下次打开时用户看到一个自己
  忘了设过的筛选而以为"资产没了"。
- 表格每行：健康徽标 + 名称/表达式（等宽） + 轴 chip + 留存度条 + 入库状态 + 首次发现 +
  最近体检（相对时间）+ 溯源计数。行点开 → 详情抽屉。
- 空态三分：**从未挖到**（引导去新建任务）/ **筛选无结果**（给「清空筛选」）/
  **台账为空但有 run 在跑**（说明「候选被判 kept 后才会登记」）。三种空态文案不同，
  绝不用一句「暂无数据」糊过去。第一种空态**必须**带一句上线口径说明（评审 P1-9）：
  「本功能上线前的历史成果已尽力回填；随任务一并删除的那部分无法恢复。」
- 批量：多选 → 归档 / 导出。

**详情抽屉**（`DiscoveryAssetDetail.tsx`）：

1. 头部：名称、轴、标的/周期、健康徽标、留存度、`archived` 标识、备注（可编辑）；
2. **体检趋势**（`DiscoveryHealthTrend.tsx`，自绘 SVG，沿用既有 `DiscoverySourceBars` /
   `ExpressionTree` 的自绘姿态，不引第三方图表库）：横轴时间、纵轴留存度折线，点按状态着色，
   `unknown` / `error` 画空心点且**不连线**（不可判定的点连成线就是在编故事）；
3. **指标双栏**：左「登记时（挖掘窗口）」、右「最近一次体检（fresh 段）」，逐项并排 +
   变化箭头；顶部一句口径说明：「右栏只统计挖掘完成后新产生的 N 根 K 线，是真正的样本外」；
4. **溯源时间线**：每条 `{runId, 第 N 轮, 演化来源, 时间}`；run 已删除的显示灰字
   「任务已删除」且不可点；
5. **表达式树 / 策略详情**：复用既有 `ExpressionTree` 与 `DiscoveryStrategyDetail`；
6. 动作区：立即体检（loading + 结果内联刷新）、入库 / 去实体页面、归档、导出本条。

**总览分区**加一张「成果资产」卡：各状态计数 + 近 24h 体检数 + 近 7 天新增衰减数 +
守护开关状态（关闭时给一句「体检守护已关闭，健康状态不会更新」——**关掉的功能必须在
界面上看得出来**）。

**中文映射的单一真源与双向门禁**：H1–H6 与六种健康状态的中文**只**在
`apps/web/lib/discoveryHealth.ts` 定义。本仓库已经连续三次栽在「后端加了码、前端另有一张表、
漏改就回显裸英文」上（迭代 1 RV-6 的 `category=discovered`、迭代 2 P1-5 的 `S*`、
迭代 2 RV-3 的裸模板标识符）。本轮两端各加一条机器判据：

- 后端 `test_discovery_health_grade.py`：断言 `health.HEALTH_CODES`（模块级常量元组）
  与 `grade()` 所有可能返回码的集合**完全相等**（用参数化穷举构造六种触发条件）；
- 前端 `discoveryHealth.test.ts`：断言本地 `HEALTH_CODE_CN` 的键集合 == 一份写死的
  `["H1"…"H6"]` 常量，且 `HEALTH_STATUS_CN` 覆盖六个状态；任一缺失即红。

### 2.11 存量成果回填（评审 P1-9 · 上线口径）

登记只发生在 `_commit`（AL-5），所以**本轮上线时台账是空的**：仓库里 commit `8eb196a` /
`79a930d` 两轮已经跑出来的 `discovery_candidates.verdict == "kept"` 行，除非同一条被重新
挖到，否则**永远不会**进台账。用户点开新分区看到的是一张空表——而本轮的卖点恰恰是
「跨 run 归一 + 回溯」，第一印象直接读成"功能坏了"。

因此**必须**有一次性回填。姿态完全镜像 `main.py::_backfill_companion_strategies`（已发布、
已终审的既有范式）：

```python
# main.py lifespan：紧跟 _backfill_companion_strategies 之后，与它同一段 try/except 姿态
async def _backfill_discovery_assets() -> None:
    """把本功能上线前已 kept 的候选一次性补进台账（幂等 / 有界 / fail-open）。"""
```

五条纪律，一条都不能省：

1. **幂等**：靠 `discovery_assets.asset_key` 的唯一索引 + `register_kept` 的既有 upsert
   路径（**同一个实现**，不另写一份合并逻辑）。跑一百次结果相同；
2. **有界**：一次最多回填 `discovery_health_max_assets` 条，按 `created_ts` **升序**
   （先回填最早的，让 `first_found_ts` 语义正确），超出即 `log` 并计数，**绝不静默截断**；
3. **fail-open**：整段 `try/except` 只 `logger.warning`，**绝不阻断启动**——台账是观测面；
4. **口径退化如实标注**：老候选的 `mined_window` 只能从 `discovery_runs.checkpoint_json`
   的 `dataWindow` 复原，`splitTs` 无法重算（当时的 df 已经不在了），一律填 `0` 并在资产上
   置 `backfilled = true`；详情抽屉对 `backfilled` 资产显示「本条为上线前回填，挖掘窗口的
   切点未知」。**不许**拿今天的窗口冒充当时的窗口——那正是 AL-3 要防的"换分母"；
5. **`next_check_ts` 立即到期**：回填资产的 `mined_window.endTs` 通常已经很旧，按 §2.3 的
   夹上界规则它们第一次体检就能拿到足量 fresh 段——这恰是本功能对老资产最有价值的一次
   评估。为避免上线瞬间一次性冲 OKX，回填时把 `next_check_ts` 按 `created_ts` 顺序
   **错峰摊到 `discovery_health_interval_s` 之内**（`now + i × interval / max(1, n)`）。

若 run 已被删除而候选行不存在，自然回填不到——这与 AL-11「资产独立于 run 生存」不矛盾：
那些成果本来就没被登记过，本轮不负责考古。空态文案（§2.10）同步补一条：
「上线前的历史成果已尽力回填；随 run 一并删除的那部分无法恢复」。

### 2.12 关键代码路径（逐调用链，供实现对照）

**A. 登记（每轮一次）**
```
runtime.DiscoveryRun._run_iteration
 └─ iteration.IterationRunner.run_once
     └─ IterationRunner._commit
         ├─ self._persist(iteration_row, rows)            # 既有：repo.commit_iteration 单事务
         ├─ assets.MinedWindow.of(run)                    # ★窗口口径唯一组装点（P1-1）
         ├─ assets.register_kept(run.spec, rows=kept_rows, window=…)  # ★新增，fail-open（§2.2.2）
         │   ├─ asset_repositories.candidate_ids_by_hash(run_id, hashes)   # 反查 id
         │   ├─ assets.identity_key(kind, expr_text=…, genome=…)           # AL-4
         │   │   └─ promote.factor_match_key / strategy_match_key / member_set_match_key
         │   ├─ asset_repositories.get_by_key / create_asset / merge_source  # AL-11
         │   └─ 返回 RegisterStats(created, merged, skipped)
         └─ self._emit(iteration_row, rows)               # counters 随 state 事件同轮下发
```

**B. 体检（守护，每 tick）**
```
main.lifespan → asyncio.create_task(health_runtime.run_forever())
 └─ HealthGuard._tick
     ├─ get_settings() 双闸判定                                    # AL-7
     ├─ asset_repositories.list_due_assets(now, limit)
     ├─ _group_by_market(due)                                      # AL-12
     ├─ window_loader.load_window(rest, inst_id, bar, start, end, timeframes, settings)
     │      # 组窗口 = [checkEnd − windowBars×step, checkEnd]；一组一份 df_full + 一份 memo
     └─ HealthGuard._check_one(asset, candles, frames, df_full, group_memo)
         ├─ health.window_split(df_full, asset, settings)
         │      → (split, freshBars, warmupBars, freshStartTs)      # §2.3，freshStartTs 夹上界
         ├─ 因子： ctx = ValidationContext(df_full=df_full, horizon=asset.horizon,
         │            thresholds=accept_thresholds, split_at=split,
         │            frozen_direction=asset.direction, memo=group_memo)  # AL-1/AL-2/AL-3
         │        → validation.evaluate_split(node, ctx)
         │        → fresh_coverage = np.isfinite(split_eval.values[split:]).mean()  # H3，P1-3
         ├─ 策略： assembler = strategy_lane.assembler_for(kind, GENOME_LOADERS[kind](genome),
         │            settings)
         │        ctx = LaneContext(df_full=df, candles=…, htf_frames=…, bar=…,
         │            split=split, split_ts=freshStartTs, config={}, settings=…, memo=…)
         │        → backtest_lane.run_split_backtest(assembler, ctx)      # AL-3
         ├─ health.grade(kind, fresh_metrics, accept_metrics, thresholds, …) → reasons
         ├─ health.next_status(prev_streak, reasons, fail_streak_gate=…)   # AL-8
         └─ asset_repositories.write_check(...) + update_asset_health(...) + prune
```

**C. 立即体检 / 入库 / 归档（REST）**
```
api/discovery_assets.py
 ├─ POST /discovery/assets/{id}/check    → guard.claim(id) → HealthGuard.check_now(id)
 ├─ POST /discovery/assets/{id}/promote  → assets.promote_asset(session, id)
 │                                          └─ promote.promote_source(PromoteSource(...))
 ├─ POST /discovery/assets/{id}/archive  → asset_repositories.set_archived(...)
 └─ GET  /discovery/assets/export        → export.build_snapshot(session, ids)   # AL-10
```

---

## 三、文件 / 模块变更计划

### 3.1 后端 · 新增

| 文件 | 行数预算 | 意图 |
|---|---|---|
| `services/quant/app/discovery/assets.py` | ~200 | 资产身份键（AL-4）、`MinedWindow`（P1-1 唯一组装点）、`register_kept` 登记与溯源合并（AL-5 / AL-11）、`promote_asset`、`backfill_assets`（§2.11） |
| `services/quant/app/discovery/health.py` | ~230 | **纯函数**：窗口切分 `window_split`（AL-2，含 `freshStartTs` 夹上界）、判据 H1–H6、`grade`、`next_status` 迟滞（AL-8）、`retention`、`next_check_ts` 四分支（P1-5）、`HEALTH_CODES` / `HEALTH_LABELS` |
| `services/quant/app/discovery/health_runtime.py` | ~280 | `HealthGuard` 守护：双闸（AL-7）、到期选取、分组取数（AL-12）、资源闸（AL-9）、`_check_one`、`claim` 同步预占、`run_forever` |
| `services/quant/app/discovery/asset_repositories.py` | ~250 | 两张新表的 CRUD：`create_asset` / `merge_source` / `get_by_key` / `candidate_ids_by_hash`（§2.2.2 反查）/ `list_assets`（筛选+排序+分页）/ `list_due_assets` / `write_check` / `update_asset_health` / `prune_checks` / `set_archived` / `summary_counts` |
| `services/quant/app/discovery/window_loader.py` | ~70 | §2.7 取数辅助（从 `promote` 平移；一个实现两个调用点） |
| `services/quant/app/discovery/asset_export.py` | ~110 | AL-10 自包含快照组装 + 敏感字段白名单 |
| `services/quant/app/api/discovery_assets.py` | ~200 | 10 个 REST 端点（`api/discovery.py` 已 ~190 行 13 个端点，不再往里塞） |

### 3.2 后端 · 修改（既有文件，全部为**加法**或**纯提取平移**）

| 文件 | 改动 | 行数 |
|---|---|---|
| `db/models.py` | 新增 `DiscoveryAsset` / `DiscoveryAssetCheck` 两个模型（**不动任何既有表 / 既有列**） | +75 |
| `discovery/validation.py` | `ValidationContext` 加 `split_at` / `frozen_direction` 两个可选字段；`evaluate_split` 改 2 行（§2.4，**承重**） | +8 / ~4 |
| `discovery/promote.py` | 提取 `strategy_match_key` / `member_set_match_key` / `combo_members` / `rotation_params`（纯提取）；新增 `factor_match_key` 并把 `_find_existing_factor` 改成「规范式哈希优先 + 解析失败回落逐字」（**语义变更**，§2.2.1 红框 / D-27）；拆出 `PromoteSource` + `promote_source`（§2.8）；`_load_backtest_window` 改为调 `window_loader`（§2.7） | ~+70 / −35 |
| `discovery/iteration.py` | `_commit` 里 `_persist` 之后、`_emit` 之前组 `MinedWindow.of(run)` 并调 `assets.register_kept`，fail-open + 两个 `run._counters` 计数（§2.2.2 精确插入点） | +15 |
| `discovery/iteration_stats.py` | **不改**。两个计数走 `run._counters`（随 `state` 事件同轮下发、随下一轮 `counters_json` 落库），不进 `IterationTally`——它有 `merge()` 逐轴汇总语义，而登记是 run 级、每轮一次的动作，塞进去会被误 merge | 0 |
| `main.py` | lifespan 起 `health_runtime.run_forever()` 任务 + 关停时 cancel + `await guard.shutdown()`；新增 `_backfill_discovery_assets()` 一次性回填（§2.11，镜像 `_backfill_companion_strategies` 的 try/except 姿态）；`include_router(discovery_assets.router)` | +34 |
| `config.py` | **15** 个 `discovery_health_*` 字段（§六；评审 P2-1 修正键数，P0-1 新增 `discovery_health_warmup_bars`） | +22 |

> **零迁移复核**：两张表都是全新表，`init_db` 的 `create_all` 加法建表即可；本轮**没有**
> 给任何既有表加列，因此不需要触碰 `db/session.py` 里的 ALTER 补丁器（那条路径只在给既有表
> 加列时才需要——见 weight-model 那轮的教训）。

### 3.3 前端 · 新增

| 文件 | 行数预算 | 意图 |
|---|---|---|
| `apps/web/lib/discoveryAssetTypes.ts` | ~110 | `DiscoveryAssetRow` / `DiscoveryAssetDetail` / `DiscoveryAssetCheck` / `DiscoveryAssetSummary` / 查询参数类型 |
| `apps/web/lib/discoveryAssetApi.ts` | ~90 | 10 个端点的 REST 客户端（复用 `discoveryApi.ts` 的 `request` / `buildQuery` 范式） |
| `apps/web/lib/discoveryHealth.ts` | ~130 | 健康状态 / H 码中文**单一真源**、徽标色板、留存度格式化、体检序列 → 折线点、相对时间 |
| `apps/web/lib/discoveryHealth.test.ts` | ~90 | 中文映射全覆盖门禁（§2.10）+ 纯函数用例 |
| `apps/web/components/Discovery/DiscoveryAssets.tsx` | ~230 | 台账分区主体：拉数、表格、分页、多选、批量归档 / 导出、三种空态 |
| `apps/web/components/Discovery/DiscoveryAssetFilters.tsx` | ~140 | 筛选条（搜索 / 轴 / 状态 / 入库 / 归档 / 标的 / 排序） |
| `apps/web/components/Discovery/DiscoveryAssetDetail.tsx` | ~250 | 详情抽屉六段（§2.10） |
| `apps/web/components/Discovery/DiscoveryHealthBadge.tsx` | ~60 | 健康徽标（表格 / 抽屉 / 总览三处复用，单一实现） |
| `apps/web/components/Discovery/DiscoveryHealthTrend.tsx` | ~150 | 自绘 SVG 体检折线（`unknown` / `error` 空心不连线） |
| `apps/web/components/Discovery/DiscoveryAssets.test.tsx` | ~140 | 筛选 / 排序 / 空态三分 / 批量归档 |
| `apps/web/components/Discovery/DiscoveryAssetDetail.test.tsx` | ~150 | 双栏口径文案、体检序列、悬空溯源、立即体检 409 |
| `apps/web/components/Discovery/DiscoveryHealthTrend.test.tsx` | ~80 | 空心点 / 不连线 / 单点 / 全 unknown |

### 3.4 前端 · 修改

| 文件 | 改动 |
|---|---|
| `apps/web/components/Discovery/DiscoveryLab.tsx` | `SECTIONS` 加第 6 项「成果资产」；`section === "assets"` 分支渲染 `DiscoveryAssets` |
| `apps/web/components/Discovery/DiscoveryLibrary.tsx` | 标题栏加「跨任务查看 →」按钮（`goSection("assets")`）；「已退化」分组标题改为**「本轮已淘汰」**并加 tooltip 区分资产级「已衰减」（§2.5.4 命名纪律） |
| `apps/web/components/Discovery/DiscoveryOverview.tsx` | 加「成果资产」摘要卡（各状态计数 + 近 24h 体检 + 守护开关状态） |
| `apps/web/components/Discovery/DiscoveryMonitor.tsx` | 顶部计数条加 `ledgerRegistered` / `ledgerErrors` 两项（读 `state` 事件的 `counters`，§2.2.2 落点；`ledgerErrors > 0` 时用告警色 + tooltip「本轮有成果未能登记进台账」） |
| `apps/web/lib/store.ts` | 加瞬态 `discoveryAssetId: number \| null` + setter（**不进 `partialize`**，与 `discoveryRunId` 同姿态）；`discoverySection` 取值域文档注释补 `assets` |
| `apps/web/lib/discoveryTypes.ts` | **（评审 P1-8 补漏）** `DiscoveryCounters` 加 `ledgerRegistered?: number` / `ledgerErrors?: number`。它是**显式 interface**（已核：`tested/kept/llmCalls?/prunedCandidates?/prunedIterations?/rejected`），不补就是 `tsc --noEmit` 直接报错，而 DoD 15 要求零新增 tsc 错误。**`applyDiscoveryEvent` 无需改**——已核它的 `state` 分支是 `{...prev, ...patch}` 整对象合并，运行期不会吞新键（这正是上游那条「逐键手抄会静默吞 WS 新键」的教训已经修好的地方） |

### 3.5 既有测试 · 必须同步修改

逐条核对过仓库现状，只有下面两处会因本轮改动而变红；**不得**用"顺手改绿"的方式处理，
每一处都要能说清为什么该变：

| 文件 | 原因与改法 |
|---|---|
| `services/quant/tests/test_discovery_import_boundary.py` | `_module_files()` 的 `len(files) >= 12` 断言仍成立（模块只增不减），**无需改**；但新增 6 个模块会被参数化逐个扫——`assets.py` / `health_runtime.py` 等**绝不可** import 交易层，实现时按此写即可。此处记录为"必须确认通过"而非"必须修改"。 |
| `apps/web/components/Discovery/DiscoveryLab` 相关快照/交互测试 | 仓库现无 `DiscoveryLab.test.tsx`（已核）。若实现节点新增分区时发现任何测试硬断言了 5 项导航，一并更新为 6 项并在提交说明里点名。 |

> 已核实**不会**变红的几处（避免实现节点误改）：`test_discovery_api.py::test_defaults_expose_only_implemented_axes`
> （本轮不动 `IMPLEMENTED_AXES`）、`test_discovery_promote_strategy.py`（§2.7 的
> `window_loader` 平移与策略/组合两把 match key 都是纯提取，对外行为逐字不变——它正是那几次
> 提取的回归网）、`test_discovery_factor_lane_golden.py`（§2.4 的两个新字段默认值保持现状语义）。
>
> **例外（评审 P0-3）**：`test_discovery_promote.py` 的**因子轴**部分不再是"零变更全绿"，
> 而是「零变更全绿 **+ 新增一条**」——`_find_existing_factor` 的比较键从逐字文本升级为规范式
> 哈希是**有意的语义变更**，必须新增「`add(a,b)` 与 `add(b,a)` 复用同一 `factors` 行」的用例
> 把新语义钉住，另加一条「`params_json.expr` 解析失败的历史行不炸整次入库、回落逐字比」。
> 谁把这条改成"零变更"，谁就是把 AL-4 改空了。

### 3.6 配置 / 文档

| 文件 | 改动 |
|---|---|
| `.env.example` | 新增 **15** 个 `DISCOVERY_HEALTH_*` 键 + 一段说明（评审 P2-1 修正键数）。**注意**：`desktop/src/lib/config.js` 的 prod 白名单已经放行整个 `DISCOVERY_` 前缀（已核第 75 行 `key.startsWith('DISCOVERY_')`），因此本轮**不需要**改 desktop——但必须在 `.env.example` 的注释里写明这一点，免得下一轮有人以为漏了 |
| `README.md` | 「策略与因子挖掘」段落补一小节「成果资产与持续体检」（≤12 行） |
| `docs/plans/discovery-asset-ledger/spec.md` | 本文 |

---

## 四、接口设计

全部挂在既有 `/api` 前缀下，**不新增 WebSocket**（D-21）。写类端点一律受
`discovery_enabled` 总闸（关闭 ⇒ 403 `DISCOVERY_DISABLED`），与既有挖掘端点一致。

### 4.1 `GET /discovery/assets`

Query：`kind`（空=全部）、`status`（`fresh|healthy|watch|decayed|unknown|error`）、
`promoted`（`1|0`）、`archived`（默认 `0`）、`instId`、`bar`、`mechanism`、`q`（描述/表达式
模糊）、`sort`（`firstFound|lastFound|lastCheck|retention|acceptStrength`，默认 `lastFound`）、
`order`（`desc|asc`）、`limit`（1–200，默认 50）、`offset`。

```json
{"items": [{
  "id": 12, "assetKey": "f:9c1e…", "kind": "factor", "label": "挖掘因子 9c1e…：…",
  "instId": "BTC-USDT-SWAP", "bar": "15m", "horizon": 6, "direction": "long",
  "exprText": "sub(ma(overnight,60),delta(amplitude,5))", "describe": "…",
  "mechanism": "gap_premium", "mechanismCn": "跳空溢价",
  "firstFoundTs": 1784900000, "lastFoundTs": 1785500000, "sourceCount": 2,
  "variantCount": 1, "backfilled": false,
  "acceptMetrics": {"is": {...}, "oos": {...}},
  "promoted": {"kind": "factor", "entityId": 18, "companionStrategyId": null},
  "health": {"status": "watch", "retention": 0.61, "consecutiveFail": 1,
             "lastCheckTs": 1785900000, "nextCheckTs": 1785921600,
             "reasons": ["H5"], "checksCount": 7},
  "archived": false, "note": ""
}], "total": 34}
```

### 4.2 `GET /discovery/assets/{assetId}`

在列表项基础上追加：`genome`、`minedWindow`、`acceptThresholds`、`trialsAtAccept`、
`sources`（每条带 `runExists: bool`（**服务端算**，评审 P2-4）、`genomeHash`、`acceptSummary`）、
`checks`（最近 `min(limit, 50)` 条）、`checksPruned`。404 `NOT_FOUND`。

### 4.3 `GET /discovery/assets/{assetId}/checks?limit=&offset=`

```json
{"items": [{
  "id": 91, "checkedTs": 1785900000, "status": "watch", "reasons": ["H5"],
  "retention": 0.61, "clipMode": "", "error": "",
  "windowStartTs": …, "windowEndTs": …, "freshStartTs": …,
  "freshBars": 288, "warmupBars": 1212,
  "metrics": {"fresh": {"rankIc": 0.021, "ir": 0.31, "tStat": 1.4, "occurrences": 282},
              "history": {"rankIc": 0.044, "ir": 0.62}}
}], "total": 7}
```

### 4.4 `POST /discovery/assets/{assetId}/check`

无请求体。200 返回新写入的 check 行（形状同 4.3 的一项）。
错误：403 `DISCOVERY_DISABLED`、404 `NOT_FOUND`、409 `DISCOVERY_HEALTH_BUSY`（守护或另一次
手动体检正在跑该资产）、409 `DISCOVERY_ASSET_ARCHIVED`（已归档资产不体检）、
503 `DISCOVERY_DATA_UNAVAILABLE`（取数失败，**不写 check 行**，AL-8）。

### 4.5 `POST /discovery/assets/{assetId}/archive` · `POST …/unarchive`

`{"id": 12, "archived": true, "archivedTs": 1785900000}`。归档后 `next_check_ts` 保留原值
（取消归档即恢复排队，不重置周期）。

### 4.6 `PATCH /discovery/assets/{assetId}`

Body `{"note": "…"}`（≤256，服务端截断）。返回更新后的列表项形状。

### 4.7 `POST /discovery/assets/{assetId}/promote`

响应与既有 `POST /discovery/candidates/{id}/promote` **同形状**（前端复用同一段处理逻辑）：

```json
{"promoted": {"kind": "factor_set", "entityId": 7, "entityKind": "factor_set",
              "reused": false, "companionStrategyId": 21}}
```
错误：403 / 404 / 400 `DISCOVERY_GENOME_INVALID` / 400 `FACTOR_EXPR_INVALID`。
幂等：已入库时回放（含 `companionStrategyId`，上游评审 P1-9 的同款要求）。

### 4.8 `GET /discovery/assets/export`

两种模式**互斥且行为不同**（评审 P2-2 把 v1 自相矛盾的两处钉死）：

| 模式 | 行为 |
|---|---|
| 给了 `ids`（逗号分隔） | `len(ids) > 500` ⇒ **413 `DISCOVERY_EXPORT_TOO_LARGE`**。用户点名要哪些，就不能悄悄少给几条 |
| 没给 `ids`（用 4.1 的筛选条件） | 按 `sort`/`order` 取前 500 条，响应里必回 `"truncated": true, "matched": N`——**绝不静默截断**，也**绝不** 413（筛选式导出本来就是"给我看看这一批"，413 会让用户无从下手） |

响应见 §2.9。

### 4.9 `GET /discovery/assets/summary`

```json
{"total": 34, "byStatus": {"fresh": 3, "healthy": 12, "watch": 9, "decayed": 6,
                            "unknown": 3, "error": 1},
 "promoted": 11, "archived": 4,
 "checkedLast24h": 22, "newlyDecayedLast7d": 2, "nextDueTs": 1785921600,
 "backfilled": 12,
 "guard": {"enabled": true, "running": true, "lastTickTs": 1785918000, "lastError": "",
           "checkedTotal": 418, "skippedByCap": 0, "dataFailures": 1,
           "assetLimitHits": 0, "intervalS": 21600.0, "tickS": 900.0,
           "warmupBars": 400, "windowBars": 1500}}
```

### 4.10 错误码汇总（新增）

| code | HTTP | 含义 |
|---|---|---|
| `DISCOVERY_HEALTH_BUSY` | 409 | 该资产正在体检中 |
| `DISCOVERY_ASSET_ARCHIVED` | 409 | 已归档资产不接受体检 |
| `DISCOVERY_DATA_UNAVAILABLE` | 503 | 立即体检取数失败（**不写 check 行**，AL-8；评审 P2-3 补入本表） |
| `DISCOVERY_EXPORT_TOO_LARGE` | 413 | 导出**显式给了 `ids` 且超过 500 条**（筛选式导出走截断 + `truncated/matched`，不抛此码——评审 P2-2） |
| `DISCOVERY_ASSET_LIMIT` | —— | 台账已达 `discovery_health_max_assets`，停止登记新资产。**不是 HTTP 错误**：它只在登记 / 回填路径内部记录并计数，出现在日志与 `summary.guard.assetLimitHits`，**永不**作为响应抛出（v1 给它标 507 会诱使实现节点真的去 raise 一个没有调用方能收到的异常——评审 P2-3） |

---

## 五、数据模型

**零迁移**：两张全新表，`create_all` 加法建表；**不动任何既有表、不加任何既有列**（D-5 延续）。
所有 `*_ts` 一律秒级 UTC（C2）。

### 5.1 `discovery_assets`

| 列 | 类型 | 说明 |
|---|---|---|
| `id` | Integer PK | |
| `asset_key` | String(48) **UNIQUE** | §2.2.1 语义身份（`f:` / `c:` / `s:` / `r:` + 32 hex） |
| `kind` | String(24) | `factor` / `factor_combo` / `strategy_param` / `strategy_combo` |
| `inst_id` | String(32) | **资产自持**（AL-12） |
| `bar` | String(8) | 同上 |
| `horizon` | Integer | 因子轴预测周期；策略轴 0 |
| `direction` | String(8) | 因子轴冻结方向（AL-1 的输入）；策略轴 `""` |
| `expr_text` | Text | 因子轴规范式；策略轴 `""` |
| `genome_json` | Text | 策略三轴的完整基因（入库与体检都靠它，AL-11） |
| `describe_text` | String(512) | 中文渲染 |
| `mechanism` | String(48) | 机制族 |
| `label` | String(128) | 展示名（与 promote 命名同源） |
| `first_found_ts` / `last_found_ts` | Integer | AL-11 |
| `source_count` | Integer | 真实总次数（可 > `len(sources_json)`） |
| `sources_json` | Text | ≤20 条溯源（最早 1 + 最近 19），每条含 `genomeHash` + `acceptSummary`（评审 P1-7） |
| `variant_count` | Integer | 被合并的不同 `genomeHash` 数（P1-7；`>1` 时详情抽屉必须显式提示体检口径取首次那一组） |
| `backfilled` | Boolean | 上线前一次性回填进来的（§2.11）；为真时 `mined_window.splitTs == 0` 且 UI 如实标注 |
| `mined_window_json` | Text | `{startTs,endTs,isRatio,windowBars,splitTs}` |
| `accept_metrics_json` | Text | 登记时指标快照（判级分母，AL-3） |
| `accept_thresholds_json` | Text | 登记时阈值快照（AL-3 第二句） |
| `trials_at_accept` | Integer | 多重检验回溯 |
| `promoted_kind` | String(16) | `""` / `factor` / `factor_set` / `strategy` |
| `promoted_id` | Integer NULL | 实体 id（不加外键，仿 `LiveOrderAudit.account_id`） |
| `companion_strategy_id` | Integer NULL | `factor_set` 的伴生策略 |
| `health_status` | String(16) | `fresh` / `healthy` / `watch` / `decayed` / `unknown` / `error` |
| `retention` | Float NULL | §2.5.3（`null` 是合法值，绝不填 0 顶替） |
| `consecutive_fail` | Integer | 迟滞计数（AL-8） |
| `last_reasons` | String(64) | 逗号分隔的 H 码 |
| `last_check_ts` / `next_check_ts` | Integer | 调度键 |
| `checks_count` / `checks_pruned` | Integer | 累计体检数 / 被剪枝数（绝不静默截断） |
| `archived` | Boolean | 用户动作，与健康状态正交 |
| `archived_ts` | Integer | |
| `note` | String(256) | 用户备注 |
| `created_at` / `updated_at` | DateTime | |

索引：`UNIQUE(asset_key)`、`ix_discovery_assets_due(archived, next_check_ts)`（守护选取的
唯一查询路径）、`ix_discovery_assets_kind(kind, health_status, id)`（列表筛选）、
`ix_discovery_assets_market(inst_id, bar)`（分组取数）。

### 5.2 `discovery_asset_checks`

| 列 | 类型 | 说明 |
|---|---|---|
| `id` | Integer PK | |
| `asset_id` | Integer FK → `discovery_assets.id`，index | |
| `checked_ts` | Integer | |
| `window_start_ts` / `window_end_ts` / `fresh_start_ts` | Integer | 口径落库（AL-2，可回溯） |
| `fresh_bars` / `warmup_bars` | Integer | |
| `status` | String(16) | 与资产同取值域（不含 `fresh`） |
| `reasons` | String(64) | 逗号分隔 H 码 / `S0:*` |
| `retention` | Float NULL | |
| `clip_mode` | String(16) | `""`（因子）/ `gated` / `rebased`（策略，沿用 BL-3 的口径标注） |
| `metrics_json` | Text | `{"fresh": {...}, "history": {...}}`，每栏 ≤12 个标量 |
| `error` | String(256) | 仅 `status="error"` 时非空 |

索引：`ix_discovery_asset_checks(asset_id, checked_ts)`。
保留：每资产 `discovery_health_max_check_rows`（默认 200）条，超出剪最旧并累加
`checks_pruned`。

### 5.3 内存态

`HealthGuard`（**不落库**）：`_inflight: set[int]`（同步预占）、`_backtests_used`、
`_state`（§2.6 末）、`_market_backoff: dict[tuple[str,str], float]`（按标的/周期的取数退避）。

`DiscoveryRun._counters` 新增两个键 `ledgerRegistered` / `ledgerErrors`（§2.2.2）：
同轮随 `state` 事件下发，随**下一轮** `commit_iteration` 落进既有的
`discovery_runs.counters_json`——**不新增列**。

---

## 六、配置项（`config.py`，env 前缀 `DISCOVERY_HEALTH_`）

| 键 | 默认 | 边界 | 说明 |
|---|---|---|---|
| `discovery_health_enabled` | `true` | | 体检守护总闸，**受 `discovery_enabled` 一票否决**（AL-7） |
| `discovery_health_tick_s` | `900.0` | `>0` | 守护轮询间隔 |
| `discovery_health_interval_s` | `21600.0` | `>0` | 单个资产的体检周期（默认 6h，与 `dataRefreshS` 同量级） |
| `discovery_health_batch` | `8` | `1–64` | 单 tick 最多处理的资产数 |
| `discovery_health_max_backtests_per_tick` | `40` | `≥1` | **按底层 `run_backtest` 次数**计的硬闸（AL-9） |
| `discovery_health_asset_timeout_s` | `45.0` | `>0` | 单资产墙钟超时 |
| `discovery_health_window_bars` | `1500` | `200–8000` | 体检取数根数（暖机 + fresh），一组一次取这么多 |
| `discovery_health_warmup_bars` | `400` | `50–4000` | **（评审 P0-1 新增）** 每个资产的暖机段根数；`freshStartTs` 与 `maxFreshBars` 都由它派生（§2.3）。校验：必须 `≥ discovery_health_min_warmup_bars` **且** `< discovery_health_window_bars`（否则 `maxFreshBars ≤ 0`，fresh 段恒空）——启动时 fail-fast，不许运行期才发现 |
| `discovery_health_min_fresh_bars` | `120` | `≥10` | fresh 段最少根数，不足即 `unknown`（AL-2） |
| `discovery_health_min_warmup_bars` | `300` | `≥50` | 暖机段最少根数，不足即 `unknown`。夹上界后正常情况恒有 `warmup_bars` 根，因此这条实际是**取数缺口**告警 |
| `discovery_health_min_fresh_trades` | `5` | `≥1` | 策略资产 fresh 段最少成交笔数 |
| `discovery_health_retain_frac` | `0.5` | `0–1` | 留存门槛（H5 / H6 的系数） |
| `discovery_health_fail_streak` | `2` | `1–10` | 迟滞（连续几次不达标判 `decayed`，AL-8） |
| `discovery_health_max_check_rows` | `200` | `≥10` | 每资产体检记录保留上限 |
| `discovery_health_max_assets` | `5000` | `≥100` | 台账规模护栏；触顶后停止登记新资产并计数（不报错、不删旧） |

`.env.example` 同步补这 **15** 条 + 一句：**`desktop/src/lib/config.js` 的 prod 白名单已放行
整个 `DISCOVERY_` 前缀，本组键无需改 desktop**。（v1 三处都写 13 条而表里是 14 行，
评审 P2-1；加上 P0-1 的 `discovery_health_warmup_bars` 后为 15。）

---

## 七、测试与验收标准

### 7.1 后端测试（`services/quant/tests/`，pytest）

> 全量 pytest **不能并发**（共用一个临时 DB）；`pytest -q` 输出块缓冲，长跑请用
> `python -u -m pytest`。这两条是本仓库反复吃过亏的既有约束。

| 套件 | 覆盖 |
|---|---|
| `test_discovery_validation_split_at.py` | **等价门禁**（§2.4）：golden 六项常量（`trend_candles(1200, seed=5)`），`split_at=None && frozen_direction=""` 逐条相等；`split_at` 越界夹紧；`split_at` 落在指定 ts 时两段长度正确 |
| `test_discovery_health_direction.py` | **AL-1 双向门禁**（构造见 §2.4，评审 P0-2）：`two_regime_candles(300, 900)` + `split_at=900`；① 冻结 `long` ⇒ fresh `rankIc<0` 且判 `H4`；② 不冻结（auto）⇒ `rankIc>0` 且**不判** `H4`；③ 两次 `abs(rankIc)` 逐位相等 |
| `test_discovery_health_window.py` | AL-2：`freshBars<min` ⇒ `H1`/`unknown`；`warmupBars<min` ⇒ `H2`；fresh 段覆盖率不足 ⇒ `H3`（**且构造一个"暖机段全 NaN、fresh 段全有限"的用例断言 H3 不触发**——这是 P1-3 的反向判据，用全窗 coverage 会红）；`unknown` 时 `next_check_ts` 顺延到"能攒够"的时刻。**P0-1 双向门禁**：`minedEndTs` 老过 `windowBars × step` 的资产 ⇒ `warmupBars == discovery_health_warmup_bars` 且**不判 H2**（v1 口径下这里必得 0）；且 `freshBars <= maxFreshBars`（fresh 段不随资产年龄无界增长） |
| `test_discovery_health_grade.py` | H4/H5/H6 逐条触发与不触发；`HEALTH_CODES` 与 `grade` 返回码集合相等（中文映射门禁前半）；`retention` 分母为 0 ⇒ `null`；**策略轴 `accept.oos.sharpe < 0` ⇒ `retention` 为 `null` 且 H5 跳过**（评审 P1-6，v1 口径下会得到一个 >1 的假留存度）；策略轴 `S0:*` ⇒ `unknown` |
| `test_discovery_health_next_check.py` | **P1-5**：四条分支各一例——`healthy` ⇒ `+interval`；`unknown` ⇒ 顺延到能攒够；`error` ⇒ 有界指数退避；取数失败 ⇒ `next_check_ts` **不变**。再加一条端到端：连续三个 tick、同一批到期资产，第 2、3 个 tick 必须取到**不同**的资产（v1 口径下三次取到同一批） |
| `test_discovery_health_streak.py` | AL-8 状态机：healthy 归零；fail×1 ⇒ `watch`；fail×2 ⇒ `decayed`；`unknown` / `error` 不动计数；`decayed` 后再 healthy 立刻恢复 |
| `test_discovery_assets_identity.py` | AL-4 monkeypatch 同源门禁；四轴键前缀；同 genome 不同顺序成员 ⇒ 同键；**P0-3**：`add(a,b)` 与 `add(b,a)` ⇒ 同 `asset_key` **且** `promote` 复用同一 `factors` 行；`params_json.expr` 解析失败的历史行不炸整次入库；**P1-7**：同成员不同权重 ⇒ 一行、`variant_count == 2`、`genome_json` 保持首次那份、两条溯源的 `genomeHash` 不同；AL-11 合并（`first_found_ts` 不变、`sources_json` 上限 20 保留最早 1 条、`source_count` 记真实值）；`accept_metrics` 不被二次登记覆盖 |
| `test_discovery_assets_register.py` | AL-5：kept 才登记；`register_kept` 抛错时迭代照常提交且 `ledgerErrors` +1；`discovery_health_max_assets` 触顶后不再新建但仍合并溯源；**P1-1**：`mined_window_json` 五个键齐全且 `endTs` 与 `run._checkpoint.data_window` 相等、`splitTs` 落在 df 的 ts 域内；**P1-10**：`strategyThresholds` 全非默认的 run ⇒ 策略资产的 `accept_thresholds_json` 与之逐键相等（**不等于** `DEFAULT_STRATEGY_THRESHOLDS`） |
| `test_discovery_assets_backfill.py` | **P1-9 §2.11**：既有 kept 候选 ⇒ 回填出行；**跑两次结果相同**（幂等）；超 `max_assets` 时截断有计数且 `log`；回填行 `backfilled=True` 且 `mined_window.splitTs == 0`；`next_check_ts` 在 `interval` 内错峰分布（不是全部相同）；回填函数抛错时**不阻断启动** |
| `test_discovery_health_runtime.py` | AL-7 双闸（任一关闭 ⇒ 该 tick 零 DB 写、零取数，用 monkeypatch 计数断言）；AL-12 分组取数（两个资产同 `(instId,bar)` ⇒ `load_window` 只调 1 次；不同 ⇒ 2 次）；**P1-2 memo 纪律**：两个组各含一条相同表达式的资产，第二组算出的 `coverage` / `rankIc` 必须与它**单独跑**时逐位相等（共用一个 memo 跨组即红）；AL-9 资源闸（触顶 `break` 且 `skippedByCap` 计数，**且按 `2 ×` 口径**——见 §7.3-9）；取数失败**不写 check 行**、求值失败**写** `error` 行；单资产超时写 `error`；`claim` 同步预占（重复 claim 返回 False）+ 请求异常时 `finally` 释放 |
| `test_discovery_window_loader.py` | §2.7 单一实现门禁：monkeypatch `load_window` 后一键回测与体检**两条路径都失败** |
| `test_discovery_assets_api.py` | 10 个端点的正常路径 + 403（总闸）+ 404 + 409（BUSY / ARCHIVED）+ 筛选/排序/分页；`promote` 在候选行被删除后**仍可用**（AL-11）且幂等回放带 `companionStrategyId` |
| `test_discovery_asset_export.py` | AL-10：`schemaVersion` 存在；自包含字段齐全；**无**敏感字段（对整个 JSON 文本做黑名单正则）；`ids` 超限 413；筛选式导出触顶回 `truncated/matched` |
| `test_discovery_import_boundary.py` | （既有，无需改）新增 6 个模块自动纳入 AST 扫描并必须通过 |

### 7.2 前端测试（`apps/web`，vitest；**必须 `NODE_ENV=test`**，否则 React act() 报错）

| 套件 | 覆盖 |
|---|---|
| `discoveryHealth.test.ts` | 中文映射门禁后半：`HEALTH_CODE_CN` 键集合 == `["H1"…"H6"]`；`HEALTH_STATUS_CN` 覆盖六态；`retention` 格式化（`null` ⇒ `—`，>1 显示原值）；相对时间 |
| `DiscoveryAssets.test.tsx` | 筛选参数正确进 query；排序切换；**三种空态文案互不相同**；多选批量归档调用；行点开抽屉 |
| `DiscoveryAssetDetail.test.tsx` | 双栏口径说明句存在且含 fresh 根数；悬空溯源渲染「任务已删除」且不可点；立即体检 409 时的提示；已归档资产隐藏「立即体检」按钮 |
| `DiscoveryHealthTrend.test.tsx` | `unknown` / `error` 点为空心且**不与相邻点连线**；单点不崩；全 `unknown` 时给文案而非空白 |
| `DiscoveryMonitor.test.tsx`（既有，增用例） | `counters.ledgerRegistered` 有渲染落点；`ledgerErrors > 0` 时告警色 + tooltip（AL-5 后半句的机器判据） |

### 7.3 验收标准（Definition of Done）

1. **AL-1 双向门禁**三条用例都绿（冻结判 `H4`、不冻结不判 `H4`、两次 `abs(rankIc)` 相等），
   且用的是 §2.4 修正后的构造（`two_regime_candles(300, 900)` + `split_at=900`）——这是本轮
   最重要的一条。**若第 2 条判不出差异，不许改判据，必须回本文改 AL-1。**
2. **§2.4 等价门禁**：golden 六项常量在改动前后逐条相等，`test_discovery_factor_lane_golden.py`
   与 `test_discovery_validation.py` 零变更全绿。
3. `freshBars` 不足时**没有任何一条路径**能产出 `healthy`（用参数化穷举所有判据组合断言）。
3b. **（P0-1）老资产不得永久静音**：构造一个 `minedWindow.endTs` 比 `now − windowBars × step`
   还早的资产，跑一次体检 ⇒ `warmupBars == discovery_health_warmup_bars`、**不判 `H2`**、
   拿到 `healthy` / `watch` / `decayed` 三态之一；同时 `freshBars <= maxFreshBars`。
   （v1 口径下这条必红——这正是它存在的意义。）
4. `HEALTH_CODES` 与 `grade()` 返回码集合相等；前端 `HEALTH_CODE_CN` 键集合相等——两条都绿。
5. AL-4 monkeypatch 同源门禁绿；四条轴各挖到一条并在台账里各占一行。
6. 同一表达式在两个 run 被挖到 ⇒ 台账**一行**、`sourceCount == 2`、`firstFoundTs` 取最早。
7. 删除某个 run 后，其资产**仍在台账**、详情可看、`promote` 仍可用（AL-11）。
8. AL-7：`DISCOVERY_HEALTH_ENABLED=false` 与 `DISCOVERY_ENABLED=false` 各自单独关闭时，
   守护 tick 的 DB 写次数与取数次数**均为 0**（monkeypatch 计数）。
9. AL-9：构造 3 个 2 成员的 `strategy_combo` 资产（`backtests_per_segment() == 2`，
   **底层 `2 × 2 == 4` 次**）+ cap=10 ⇒ 只处理 2 个（用掉 8 次），第 3 个计入 `skippedByCap`
   且有 `log`。同时断言实际累加值 == `sum(SplitBacktest.backtests)`——按 `per_segment`
   （不乘 2）计的实现会在这里处理 **3** 个（评审 P1-4）。
10. AL-6 安全：跑完一轮完整体检后，`factors` / `factor_sets` / `strategies` / `strategy_runs`
    四张表的行数与内容**逐字节不变**（测试用例前后各 dump 一次比对）。
11. `test_discovery_import_boundary.py` 全绿（新增模块零交易层依赖）。
12. 导出 JSON 通过敏感字段黑名单扫描，且用导出的 `acceptMetrics` + `minedWindow` 能在另一个
    进程里复算出同样的 `identity_key`。
13. 端到端：起一个 15m 的 factor 轴 run，跑到至少 2 条 kept ⇒ 台账出现 2 行；手动
    `POST /assets/{id}/check` ⇒ 因新增数据不足返回 `unknown`（**这正是正确行为**，
    并在 UI 上显示为「新增样本不足，暂不评级」）。
14. AL-5 后半句：`register_kept` 被 monkeypatch 成必抛后跑一轮，① 迭代照常提交（候选行数不变）、
    ② `state` 事件的 `counters.ledgerErrors == 1`、③ 前端 `DiscoveryMonitor` 有对应渲染落点
    ——三条都断言，缺任一条就是"算了但看不见"。
15. 全量回归：后端 pytest 相对**干净 HEAD worktree 实测的红灯基线**零新增
    （不得用记忆里的基线，须现测）；前端 vitest 全绿；`tsc --noEmit` 不新增错误
    （既有 `WeightBatchArena.test.tsx` 那条除外）。**注意 `DiscoveryCounters` 必须先补两个
    可选字段**，否则 `DiscoveryMonitor` 一改就打红 tsc（评审 P1-8）。
16. **（P1-9）上线口径**：在一个**已有 kept 候选**的库上启动服务 ⇒ 「成果资产」分区非空、
    回填行 `backfilled=true` 且详情抽屉显示「上线前回填，挖掘窗口切点未知」；重启第二次
    ⇒ 资产总数不变（幂等）。
17. **（P0-3）因子身份同源**：`add(a,b)` 与 `add(b,a)` 两条候选各自 `promote` ⇒ 台账**一行**、
    `factors` 表**一行**、两次 `promote` 返回同一 `entityId`。
18. **（P1-2）memo 不串味**：一次 tick 处理两个不同 `(instId,bar)` 组、组内含相同表达式的
    资产 ⇒ 两组算出的指标与各自单独跑时逐位相等。

---

## 八、风险与缓解

| # | 风险 | 缓解 |
|---|---|---|
| R-1 | 体检把 OKX 配额吃光，挤掉在跑的挖掘任务与实时行情 | 默认 6h/资产、tick 15min、单 tick ≤8 资产、按 `(instId,bar)` 分组一次取数（AL-12）；取数失败按标的/周期指数退避；所有配额相关默认值都调小一档，宁可慢 |
| R-2 | 用户把「健康」读成「未来会赚钱」 | 每处健康展示旁固定一句「历史样本统计，不构成收益预期」；详情双栏顶部写明 fresh 段口径与根数；`unknown` 的文案是「暂不评级」而不是「无数据」 |
| R-3 | fresh 段样本天然偏小 ⇒ 判级噪声大 | `unknown` 优先于结论（AL-2）+ 迟滞（AL-8）+ 留存度用比值而非绝对值；文档明确「体检是衰减告警，不是重新回测」 |
| R-4 | 台账登记拖慢迭代 | fail-open + 计数（AL-5）；`register_kept` 只做 upsert，无任何行情/回测调用 |
| R-5 | 资产越积越多，列表与调度变慢 | 三条索引（§5.1）+ 分页 + `discovery_health_max_assets` 护栏 + 归档淡出 |
| R-6 | run 删除后溯源悬空 | 台账自持全部判级输入（AL-11）；UI 如实标注「任务已删除」 |
| R-7 | `evaluate_split` 的改动波及四条已发布搜索轴 | 两个新字段都是**带默认值的可选项**，默认值下逐位不变；两阶段 golden 等价门禁（§2.4）；`test_discovery_factor_lane_golden.py` 零变更 |
| R-8 | `promote.py` 的提取改坏已发布的入库路径 | `strategy_match_key` / `member_set_match_key` / `combo_members` / `rotation_params` / `promote_source` / `window_loader` 均为**纯提取**，对外签名与行为不变，`test_discovery_promote_strategy.py` 零变更全绿是硬门槛。**唯一例外是因子轴的 `factor_match_key`**（评审 P0-3）：它把 `_find_existing_factor` 的比较键从逐字文本升级为规范式哈希，是**有意的语义变更**，安全网从"零变更"换成"零变更 + 两条新用例"（规范式复用 / 解析失败回落），并记 D-27 |
| R-15 | 老资产被窗口口径静默判成永久 `unknown`（评审 P0-1 的原始形态） | §2.3 的 `freshStartTs` 夹上界 + §7.3-3b 的双向门禁 + §2.11 回填资产**一上线就会踩到这条路径**（它们的 `minedEndTs` 天然很旧），等于给这条修复配了一个必经的端到端验证 |
| R-16 | 组内共享 `memo` 把 A 的字段上下文用到 B 身上（评审 P1-2） | §2.3 承重纪律「一组一份 df、差异全部经 `split_at`、memo 跨组必新建」+ §7.3-18 的逐位相等判据 |
| R-17 | 回填一次性把几千条资产的首检全压到同一分钟，打爆 OKX 配额 | §2.11 纪律 5：`next_check_ts` 按 `created_ts` 顺序错峰摊进一个 `interval`；叠加 AL-9 的单 tick 硬闸兜底 |
| R-9 | 守护协程异常退出后无人察觉，健康状态悄悄停更 | `run_forever` 外层 `try/except` 收口并把 `lastError` 写进 `_state`；`summary.guard.running` 与 `lastTickTs` 前端可见；总览卡在 `lastTickTs` 超过 3×tick 时给「守护似乎已停止」提示 |
| R-10 | 策略资产体检时缺高周期帧 ⇒ 静默走无 MTF 路径 | 沿用 BL-6 的 fail-closed：`assembler.preflight(ctx)` 返回 `S0:missing_htf:*` ⇒ 判 `unknown` 并把原码写进 `reasons`（可运维），**绝不**用无帧回测的结果判级 |
| R-11 | 手动体检与守护同时跑同一资产 ⇒ 写两条冲突的 check | 同步 `claim` 预占（无 `await`）+ 409（§2.8） |
| R-12 | 「本轮已淘汰」与「已衰减」被用户混为一谈 | §2.5.4 命名纪律 + 两处 tooltip + 详情里一句话解释 |
| R-13 | 时钟/窗口边界导致 `fresh` 段忽大忽小 | `checkEnd` 与 run 同一口径（只取已确认根）；`freshStartTs` 用 `bisect_left` 定位而非算术推算；口径三个 ts 全部落库可回溯 |
| R-14 | 台账触顶（5000）后新成果静默丢失 | 触顶只停「新建」不停「合并溯源」，且计数进 `summary.guard`，总览卡显式提示 |

---

## 九、建议实施顺序（每步独立可验收）

1. **golden 前置**：在当前 HEAD 上落 `test_discovery_validation_split_at.py` 的六项常量并跑绿
   （此时还没改 `evaluate_split`）。**先做这步，否则等价门禁失去意义**（上游 BL-8 的教训）。
2. `db/models.py` 两张表 + `asset_repositories.py` CRUD + 其单测。
3. `promote.py` 的提取 + `window_loader.py` 平移。`test_discovery_promote_strategy.py`
   零变更全绿；`test_discovery_promote.py` 零变更全绿**并新增两条因子轴用例**
   （规范式复用 / 解析失败回落，D-27）。
4. `assets.py`（`identity_key` + `MinedWindow` + `register_kept`）+ `iteration.py` 登记钩子
   + 计数；端到端起一个短 run 验证台账出行。
5. `validation.py` 的两行改动 + 重跑第 1 步的 golden（必须逐条相等）。
6. `health.py` 纯函数（窗口切分**含 `freshStartTs` 夹上界** + H1–H6 + 状态机 + retention
   + `next_check_ts` 四分支）+ 五个纯函数套件。**先写 §7.3-3b 的老资产门禁再写实现**，
   它是 P0-1 唯一的机器判据。
7. `health_runtime.py` 守护 + `main.py` 起停 + 运行时套件（双闸 / 分组 / 资源闸 / 失败隔离）。
8. `api/discovery_assets.py` 10 个端点 + `asset_export.py` + API 套件。
9. 前端：`discoveryAssetTypes` / `discoveryAssetApi` / `discoveryHealth`（含中文映射门禁）。
10. 前端组件：`DiscoveryHealthBadge` → `DiscoveryHealthTrend` → `DiscoveryAssetFilters` →
    `DiscoveryAssets` → `DiscoveryAssetDetail`，逐个配测试。
11. 前端接线：`DiscoveryLab` 第 6 分区、`DiscoveryLibrary` 跳转与改名、`DiscoveryOverview` 卡、
    `store.ts` 瞬态字段、`discoveryTypes.ts` 两个计数键（P1-8，**先补类型再改 Monitor**）。
12. `main.py` 的 `_backfill_discovery_assets()` 一次性回填（§2.11）+ 其套件；在**真有存量
    kept 候选**的库上起一次服务验收 §7.3-16。放最后是因为它依赖 `register_kept` 已稳定。
13. `.env.example` / `README.md` / 本文 §十一；全量回归 + 干净 HEAD worktree 实测红灯基线对账。

---

## 十、已拍板决策与开放问题

**已拍板（实现节点不得推翻，如需推翻先改本文）：**

- **D-19**：本轮**不做**已入库实体的自动停用/自动接管。降级只标注（AL-6）。
- **D-20**：健康判级**全程零 LLM**。判级是确定性规则；LLM 的位置在生成与审查，不在验证
  （上游 D-4 的延续）。
- **D-21**：本轮**不新增 WebSocket**。体检是小时级低频，REST 轮询 + 手动刷新足够；
  新增一条长连接的运维成本与它带来的实时性不成比例。
- **D-22**：资产**独立于 run 生存**。删 run 不删资产（AL-11）；台账没有"删除资产"这个动作，
  只有归档——挖掘记录是研究档案，删除会让"回溯"这条需求自相矛盾。
- **D-23**：判级分母与阈值**冻结在登记时刻**（AL-3 第二句）。用户改默认阈值不影响历史资产。
- **D-24**〔有意偏离上游姿态〕：守护协程**总是创建**，但每 tick 先判双闸、关闸即零副作用返回
  （§2.6 要点 1）。与 run manager「关闸就不起协程」不同，理由已在该处写明。
- **D-25**：`archived` 与健康状态**正交**（AL-8），不进状态机。
- **D-26**：`unknown` **不是**坏消息，UI 文案一律「暂不评级」，不使用警告色。
  ⚠️ 正因为它是中性文案，**任何会导致资产恒判 `unknown` 的缺陷都不会被用户发现**——
  评审 P0-1 就是这么埋进去的。凡是新增 `unknown` 分支，必须同时给一条"这条分支不该恒真"
  的反向门禁。
- **D-27**〔评审 P0-3 新增·语义变更〕：因子资产的身份键与 `_find_existing_factor` 的幂等键
  **统一为规范式哈希**（`expr_hash(parse_expression(...))`），不再是逐字文本相等。
  这是一次有意的、对已发布入库路径的行为变更，代价是"文本不同但规范式相同"的两条因子
  从此复用同一个 `factors` 行；收益是 AL-4 在因子轴上真的成立。**实现节点不得改回逐字比较**
  （改回去 = AL-4 空转），历史脏数据的兜底见 §2.2.1 红框第 1 条。
- **D-28**〔评审 P1-4 新增〕：策略资产的一次体检**付两段回测的代价**（`run_split_backtest`
  无条件跑 IS + OOS），不为省算力另写"只跑 OOS"的分支——那等于在本轮引入第二条求值路径，
  与 AL-3「同一把尺子」直接冲突。代价已计入 AL-9 的 `2 ×` 口径与 §6 的默认值。
- **D-29**〔评审 P1-9 新增〕：**必须**有一次性存量回填（§2.11）。没有它，本轮交付的是
  一个"对老用户永远空、对新用户要等几天才有内容"的分区——需求第三条（记录可回溯、可管理）
  在上线当天就不成立。
- 两份上游的全部拍板项继续有效，本轮不得推翻。

**开放问题（本轮按括号内默认落地，下一轮再议）：**

1. **多资产对比视图**（并排 2–4 条资产的指标与健康折线叠加）：不做。台账已能筛选排序，
   对比的增量价值不足以支撑一个新的交互模式。
2. **健康衰减的主动通知**（桌面通知 / 声音）：不做。仓库现有通知通道属于交易侧，
   接进来会打破发现层与交易层的零依赖边界（`test_discovery_import_boundary.py`）。
3. **台账导入**（把导出的 JSON 在另一台机器上还原）：不做。导入涉及身份冲突合并、
   阈值口径迁移与"别人挖的东西能不能信"三件事，需要单独评审。
4. **多折走向前验证（walk-forward）**：上游迭代 2 开放问题 5 仍未做。本轮的持续体检在
   *时间维度*上提供了比多折更强的证据（真样本外），但两者不互相替代。
5. **按资产自动重新挖掘**（发现某条衰减后，以它为父本定向再挖）：不做。这需要在挖掘 run
   与台账之间建立反向依赖，会让 run 的可复现性变差。
6. **多标的池化挖掘**：仍是单标的（上游开放问题 1，未变）。

---

## 十一、实施过程发现的方案缺陷

> 本节由实现节点在交付后追加（沿用上游放行条件 7）。凡与本文正文有偏离的，一律在此如实记录；
> **十二条**承重条款（AL-1 … AL-12）若发现任一条不可行，**先回来改本文**再改代码。
> 另须在此记录：① `evaluate_split` golden 六项常量的实测值；② 单次体检的实测墙钟
> （因子 / 策略两类各给一个量级）；③ `discovery_health_max_backtests_per_tick` 的实测触发
> 频率；④ **干净 HEAD worktree 上实测的红灯基线**对账结果（不得用记忆里的基线，须现测）；
> ⑤ §7.3 **十八条**验收标准的逐条状态（v2 由 15 条增至 18 条 + 一条 3b）。

> 交付：全部实现完成、**未 commit**（提交是下一个节点的职责）。后端新增 7 个模块 + 13 个测试
> 套件，前端新增 9 个文件（含 4 个测试）+ 修改 6 个，配置与文档 3 处。

### 11.1 与本文正文的偏离（逐条，均为实现期发现且已如实记录）

| # | 偏离 | 理由 |
|---|---|---|
| **I-1** | `asset_export.py` 不只承载「导出快照」，还承载**列表项 / 详情 / 体检行**三个 REST 序列化器 | 本文 §3.1 把 `api/discovery_assets.py` 定为「10 个 REST 端点，~200 行」，但**没给序列化器一个落点**。把它们塞进路由会让该文件逼近 400 行；单独再开一个文件又不在变更计划里。放进 `asset_export.py` 的收益是实打实的：导出与界面**共用同一段字段提取**，因此不可能出现「界面上看到的数字和导出的数字不一样」——这正是 AL-10 想守的东西 |
| **I-2** | `PATCH` 的请求模型 `AssetNoteReq` 定义在路由模块内，**未**加进 `schemas.py` | `schemas.py` 不在本文的变更计划里。只有一个字段、只有一个调用方，就近定义比为它扩散一个文件更合适 |
| **I-3** | `promote._strategy_name` 更名为**公开**的 `promote.strategy_label` | 台账要与 promote 的展示名同源（§2.2.2 的 `label` 一行），而调用别的模块的私有函数是把「同源」建立在一个随时会被改名的实现细节上。同一理由，`_combo_members` 提取为公开的 `combo_members` |
| **I-4** | `health.grade` / `next_check_ts` 的标量口径聚合成 `HealthLimits` / `SchedulePlan` 两个 frozen dataclass 传参 | 直接散着传会立刻越过 `CLAUDE.md` 的 5 形参上限（判级要 `min_fresh_bars` / `min_warmup_bars` / `min_fresh_trades` / `retain_frac` / `fail_streak` 五个，再加 `kind` / `fresh` / `accept` / `thresholds` 就是 9 个） |
| **I-5** | `config.py` 除了加 15 个字段，还加了一个 `model_validator` 做启动期 fail-fast | §6 的表格文字要求「启动时 fail-fast，不许运行期才发现」，但 §3.2 的变更计划只写了「+22 行字段」。没有这个校验器，`WARMUP_BARS ≥ WINDOW_BARS` 的配置会让 fresh 段恒空、全部资产永久「暂不评级」——而那是**看不出异常**的失效形态 |
| **I-6**〔口径澄清〕 | 组取数是**闭区间** `[checkEnd − windowBars×step, checkEnd]`，`load_range_candles` 实际返回 `windowBars + 1` 根 | 因此实测口径是：暖机段**恒为** `warmup_bars` 根（与 §2.3 一致），fresh 段最多 `maxFreshBars + 1` 根（比 §2.3 的措辞多一根，即 `freshStartTs` 自身那根）。测试按实测口径写死，不按文档的整数措辞 |
| **I-7** | `assetLimitHits`（`DISCOVERY_ASSET_LIMIT` 的计数）落在 `assets.py` 的**模块级计数器**，由 `HealthGuard.state()` 读出 | §2.6 把它列在守护的内存态里，但登记与回填**都不发生在守护里**（前者在 run 循环的 `_commit`、后者在 lifespan），守护根本观察不到。让 `assets.py` 反向 import `health_runtime` 会形成回边；模块级计数器 + 守护只读，是唯一不制造循环依赖的写法 |
| **I-8** | `_estimate_backtests` 在装配失败（陈旧基因 / 未知模板）时保守回落 **2** | AL-9 要的是「护栏不被绕过」。判不出成员数时按最小的一次双段计，宁可少放行一个资产，也不能因为一个异常就让它免检通过 |
| **I-10**〔命名纪律的第 3、4 处展示点〕 | §3.4 只让改 `DiscoveryLibrary.tsx` 的分组标题，但「已退化」在前端**一共 4 处**：分组标题、`DiscoveryLibrary` 的模块 docstring、`DiscoveryCandidateDetail` 的徽标、`DiscoveryCandidateTable` 的状态列 | 只改一处 = 用户在候选表看到「已退化」、在成果库看到「本轮已淘汰」、在成果资产看到「已衰减」，三个词指两件事——比不改还糟。四处全部改为**「本轮已淘汰」**并各带一句 tooltip 区分；`DiscoveryCandidateTable.test.tsx` 的对应断言同步更新（这是本轮唯一被改动的既有测试，改动理由即本条） |
| **I-9**〔实现期发现的设计缺口〕 | `next_check_ts` 的 `error` 分支要 `2^min(consecutiveError, 3)`，但 §5.1 的列表里**没有 `consecutive_error` 这一列**，§5.3 的内存态也没列它 | 唯一现成的计数是 `consecutive_fail`，而 AL-8 明令「`error` 不得动它」——拿它当退避指数，等于用一个语义完全无关的数决定重试节奏（H5 连红两次的资产一出错就直接退到 4×interval，而一条只出错从不判 fail 的资产永远退避 1×）。落地方案：`HealthGuard._error_streak` 内存态逐资产计数，任何非 `error` 结果立刻清零；重启清零是可接受的代价（退避本就只为压住瞬时故障）。判据：`test_error_backoff_uses_its_own_streak_not_the_fail_streak`（连续三次 error ⇒ `1× / 2× / 4×`，且 `consecutive_fail` 全程为 0） |

**行数预算**：§3.1 / §3.3 的预算是**按纯逻辑行**估的，实际文件含中文 docstring 与承重条款的
「违反后会怎样」注释后普遍高出 40–90%：`assets.py` 397（预算 200）、`health_runtime.py` 464
（280）、`asset_repositories.py` 361（250）、`health.py` 275（230）、`DiscoveryAssetDetail.tsx`
322（250）。**未做拆分**：`CLAUDE.md` 的硬约束是**单文件 800 行**，全部有两倍余量；而这些注释
恰恰是本轮最不该删的东西（每一条都记着「这里错了不会报错，只会静默给出一个看起来正常的结论」）。
超预算的部分是文档而不是逻辑，拆文件只会把一条承重条款切成两半。

### 11.2 实测数据（本文 §十一 明确要求记录的五项）

**① `evaluate_split` 的 golden 六项常量**（在**改动前的 HEAD** 上实测并写死在
`test_discovery_validation_split_at.py`，`trend_candles(1200, seed=5)` +
`sub(ts_mean(ret,20),ts_mean(ret,60))` + `horizon=6`）：

| 项 | 值 |
|---|---|
| `direction` | `"long"` |
| `coverage` | `0.95` |
| `res_is.rankIc` | `0.059935` |
| `res_is.ir` | `1.187469` |
| `ic_is`（12 段） | `[0.025687, 0.096809, 0.104899, 0.002972, 0.057875, 0.115997, 0.030815, 0.007867, 0.110485, 0.119843, -0.005632, 0.025874]` |
| `ic_oos`（12 段） | `[0.682759, 0.541713, 0.412808, 0.187987, 0.006897, -0.224917, -0.440887, -0.535484, -0.634483, -0.663626, -0.690148, -0.614238]` |

改动 `evaluate_split` 之后，`split_at=None && frozen_direction=""` 六项**逐条相等**，
且 `test_discovery_factor_lane_golden.py` / `test_discovery_validation.py` 零变更全绿。

**② 单次体检的实测墙钟**（1501 根 15m 等价窗口，本机）：

| 类型 | 墙钟 | 说明 |
|---|---|---|
| 因子资产 | **≈ 21 ms**（冷 memo） | 一次全窗求值 + 两段 `evaluate_factor`。组内第二条起共用 memo 的字段上下文，只剩表达式求值 |
| 策略资产（2 成员轮动） | **≈ 369 ms** | `run_split_backtest` 的 IS + OOS 两段，底层 `run_backtest` **实测 4 次** |

结论：默认的 `discovery_health_asset_timeout_s = 45s` 对两类都有两个数量级的余量；真正的时间
开销在**取数**而不是求值，因此 AL-12「一组一次取数」是对的优化点，而 D-28「多付一段 IS 回测」
的代价（≈180 ms/资产）完全可以接受。

**③ `discovery_health_max_backtests_per_tick` 的实测触发频率**：默认 `40` ÷ 每个 2 成员轮动
资产 `4` 次 = 单 tick 可跑 **10 个策略资产**，而 `discovery_health_batch` 默认只有 `8`——
**默认配置下这条闸不会触发**，它是给「批量调大 batch」或「成员数更多的轮动资产」兜底的。
`test_discovery_health_runtime.py::test_backtest_cap_counts_both_segments` 用 `cap=10` +
3 个资产把它逼到触发（只处理 2 个、第 3 个计入 `skippedByCap`）；按 `per_segment`（不乘 2）
计的实现在同一场景会处理 **3** 个——这就是 P1-4 那句「放行量正好翻倍」的机器判据。

**④ 干净 HEAD worktree 上实测的红灯基线对账**（**现测，不用记忆里的基线**）：

在 `git worktree add … 79a930d --detach` 出来的干净树上跑全量 `pytest tests/ -q`：

```
干净 HEAD：10 failed, 2392 passed, 1 skipped （243.9s）
本次改动后：15 failed, 2547 passed, 1 skipped （339.0s）
```

逐条 `comm` 对账，**「改动后独有」的失败只有 7 条**，全部来自
`tests/test_factor_library_expansion.py`——该文件在 `git status` 里是 **`??` 未跟踪**
（属于工作树里另一个在制特性），HEAD 上根本不存在，故干净树里不会跑到；单独跑它
`7 failed`，与本轮零关系。反向差集（基线独有）为 2 条：
`test_agent_ws::test_ws_resume_after_grace_completion_acks_false_without_replay` 与
`test_ai_backtest_manager::test_profit_rule_is_forwarded_to_spec`，是已知的**全量顺序 flake**
（同一份代码两次全量跑出的红灯集合就不一样，本轮这次比基线还少红 2 条）。

**结论：相对干净 HEAD 基线，本轮零新增红灯。** 新增用例数 `2547 − 2392 = 155`
（本轮 13 个后端套件共 148 条，其余为孤儿文件在基线侧不存在造成的偏差）。

前端：`vitest` **135 文件 / 1443 用例全绿**；`tsc --noEmit` 只剩既有的
`components/Factor/WeightBatchArena.test.tsx` 一条（与本轮无关），**零新增**。

**⑤ §7.3 十八条验收标准的逐条状态**

| # | 标准 | 状态 | 证据 |
|---|---|---|---|
| 1 | AL-1 双向门禁三条 | ✅ | `test_discovery_health_direction.py`（3 绿）。实测：冻结 `long` ⇒ fresh `rankIc = −0.7847` 判 H4；auto ⇒ 定号翻 `short`、`rankIc = +0.7847` 不判 H4；两次 `abs` 逐位相等 |
| 2 | §2.4 等价门禁 | ✅ | 见 ①；两个既有套件零变更全绿 |
| 3 | `freshBars` 不足无路径产出 healthy | ✅ | `test_discovery_health_grade.py::test_thin_window_can_never_yield_healthy`（4 轴 × 3 × 3 = 36 组参数化穷举） |
| 3b | **P0-1** 老资产不得永久静音 | ✅ | `test_discovery_health_window.py::test_old_asset_still_gets_a_full_warmup_segment` + `test_fresh_segment_never_grows_without_bound` |
| 4 | `HEALTH_CODES` 两端集合相等 | ✅ | 后端 `test_health_codes_cover_every_code_grade_can_return`；前端 `discoveryHealth.test.ts` |
| 5 | AL-4 同源门禁 + 四轴各占一行 | ✅ | `test_identity_key_delegates_to_the_promote_match_key`（monkeypatch 行为判据）+ `test_four_axes_each_occupy_one_row` |
| 6 | 两个 run 同一表达式 ⇒ 一行 | ✅ | `test_same_expression_in_two_runs_merges_into_one_row`（`sourceCount==2`、`firstFoundTs` 取最早） |
| 7 | 删 run 后资产仍可用 | ✅ | `test_promote_from_the_ledger_works_without_any_candidate_row`（无候选行仍可入库且幂等）+ `test_detail_marks_dangling_sources_server_side` |
| 8 | AL-7 双闸零副作用 | ✅ | `test_either_switch_off_means_zero_side_effects`（两个开关各一例，取数 0 次、check 行 0 条） |
| 9 | AL-9 按 `2 × per_segment` | ✅ | `test_backtest_cap_counts_both_segments`；且实测 `SplitBacktest.backtests == 4` 与预估逐值相等 |
| 10 | AL-6 四张实体表逐字节不变 | ✅ | `test_a_full_tick_never_touches_the_entity_tables`（前后各 dump 一次比对，并断言体检确实跑过 2 条，防止判据本身空转） |
| 11 | import boundary 全绿 | ✅ | `test_discovery_import_boundary.py` 自动纳入新增 7 个模块，47 绿 |
| 12 | 导出黑名单 + 可复算身份 | ✅ | `test_discovery_asset_export.py`：整份 JSON 过黑名单正则；用导出的 `exprText` 复算 `identity_key` 与 `assetKey` 相等 |
| 13 | 端到端 15m run ⇒ 台账 2 行 + 手动体检回 `unknown` | ⚠️ **等价覆盖，未起真 OKX** | 真跑需要连网与数小时等待，不进自动化。等价覆盖：登记侧 `test_discovery_assets_register.py`（kept ⇒ 出行、窗口五键齐全）+ 体检侧 `test_discovery_health_window.py`（新增样本不足 ⇒ `unknown`，且 `next_check_ts` 顺延到能攒够）。**「新增数据不足 ⇒ unknown」这条正确行为已被独立钉住** |
| 14 | AL-5 三条（提交不受影响 / 计数 / 前端落点） | ✅ | `test_register_failure_is_fail_open_and_counted` + `test_register_success_bumps_the_visible_counter` + `DiscoveryMonitor.test.tsx` 两条（`ledger-registered` / `ledger-errors` 渲染落点 + 告警色 + tooltip） |
| 15 | 全量回归零新增红灯 / tsc 零新增 | ✅ | 见 ④ |
| 16 | 上线口径：回填非空、`backfilled` 标注、重启幂等 | ⚠️ **套件覆盖，未起真服务** | `test_discovery_assets_backfill.py` 6 条：出行 / 幂等（跑两次总数不变）/ 有界计数 / `splitTs==0` + `backfilled=True` / 错峰分布 / `main._backfill_discovery_assets` 抛错不阻断启动。未做的只是「手动起一次 uvicorn 用肉眼看」 |
| 17 | **P0-3** 因子身份同源 | ✅ | `test_commutative_variants_reuse_the_same_factor_row`（`factors` 表一行、两次 `promote` 同 `entityId`）+ `test_commutative_expressions_share_one_identity`（台账一行）+ `test_unparsable_legacy_factor_row_falls_back_to_literal_compare` |
| 18 | **P1-2** memo 不串味 | ✅ | `test_memo_is_never_shared_across_groups`（第二组的 `rankIc` / `coverage` 与它单独跑时逐位相等） |

### 11.3 十条放行条件的落实

1. §7.3-3b 老资产门禁 ✅（**先于实现落地**：`fresh_start_of` 是照着它写出来的）
2. AL-1 三条门禁 ✅，用的正是修正后的 `two_regime_candles(300, 900)` + `split_at=900`
3. D-27 ✅：`_find_existing_factor` 改规范式哈希 + 解析失败回落逐字，两条新用例；
   `test_discovery_promote_strategy.py` 仍零变更全绿
4. AL-9 `2 ×` 口径 ✅，`cap=10` + 3 个 2 成员资产的场景钉住
5. `next_check_ts` 四条分支 ✅ + 「连续三 tick 取到不同资产」端到端判据 ✅
6. memo 纪律 ✅（一组一份 `_GroupData`，per-asset 差异全部经 `split_at`）
7. §2.11 回填 ✅（幂等 / 有界 / fail-open / `backfilled` 标注 / 错峰五条纪律各一条用例）
8. `discoveryTypes.ts` 两个计数键**先补再改 Monitor** ✅（tsc 零新增）
9. `accept_thresholds_json` 逐轴取对键 ✅（`test_strategy_thresholds_snapshot_uses_strategy_config_key`
   断言与全非默认的 `strategyThresholds` 逐键相等且 **≠** `DEFAULT_STRATEGY_THRESHOLDS`）
10. H3 分母用 fresh 段现算 ✅ + 「暖机段全 NaN、fresh 段全有限 ⇒ 不判 H3」反向用例 ✅
11. 策略 `retention` 非正分母一律 `null`、H5 跳过 ✅
12. 十二条承重条款**无一发现不可行**，故本节只记上面 10 条实现期偏离，未回改正文的承重条款。

### 11.4 交给提交节点的清单（工作树里混着另一个在制特性）

本特性共 **52 个文件**（后端新增 7 模块 + 13 套件、修改 6；前端新增 9、修改 8；配置文档 3）。
工作树里还躺着**另一个在制特性**（AI 交易总闸 `ai_trade_enabled` 由 `False` 翻成 `True`）的
未提交改动，与本轮共享 **2 个混合文件**，提交时必须**行级**挑拣：

| 文件 | 本特性的部分 | **不属于**本特性、须留在工作树的部分 |
|---|---|---|
| `.env.example` | 末尾整段 `DISCOVERY_HEALTH_*`（15 键 + 注释） | `AI_TRADE_ENABLED=true` 一行 |
| `services/quant/app/config.py` | `from pydantic import Field, model_validator` 一行、15 个 `discovery_health_*` 字段、`_check_health_window` 校验器 | `ai_trade_enabled: bool = False → True` 及其上方注释块 |

**纯属另一特性、一行都不要碰**：`desktop/src/lib/config.js`、`services/quant/tests/test_config.py`。
**孤儿测试**：`services/quant/tests/test_factor_library_expansion.py`（未跟踪、独立 7 红），
与 `services/quant/app/db/live_auto_repositories.py` 一并留在工作树。

---

## 评审结论（Review Verdict）

### **有条件通过**

设计的骨架是对的，而且对得相当扎实：**AL-2「真样本外」的口径**（挖掘时数据还不存在，
任何过拟合都触不到它）是本项目到目前为止在防过拟合上最硬的一招；**AL-3「同一把尺子」**、
**AL-8「迟滞 + unknown 不计失败」**、**AL-6「降级只标注不代劳」**、**AL-11「资产自持、
独立于 run 生存」** 四条合起来，把需求第三条（记录要有、能回溯、能管理）真正落到了一个
可以长期持有的数据结构上。范围也拿捏得住——不做自动停用、不做 WS、不做导入、不做多资产
对比，全是对的减法。

但 v1 有三处**会静默交付一个假功能**的缺陷，都已在本文改毕：窗口从 `now` 固定回看让
所有老资产永久静音成 `unknown`（P0-1）；AL-1 的失效机理定位错误，导致本轮"最重要的一条
验收标准"是一条永远变不绿的用例（P0-2）；因子身份键被当成"纯提取"而实际是语义变更
（P0-3）。加上 10 条 P1，本文升 v2，**无 P0 / P1 遗留**。

### 放行条件（实现节点必须逐条兑现，缺任一条即不算完成）

1. **§7.3-3b 的老资产门禁必须先于实现落地**（TDD）：构造 `minedEndTs` 早于
   `now − windowBars × step` 的资产，断言 `warmupBars == discovery_health_warmup_bars`、
   不判 `H2`、拿到三态之一。这是 P0-1 唯一的机器判据，v1 口径下它必红。
2. **§2.4 的 AL-1 三条门禁全绿**，且用修正后的构造（`two_regime_candles(300, 900)` +
   `split_at=900`）。第 2 条（auto ⇒ 不判 H4）判不出差异时，**不许改判据，必须回本文改 AL-1**。
3. **D-27 落实**：`_find_existing_factor` 改规范式哈希 + 解析失败回落逐字，配两条新用例；
   `test_discovery_promote_strategy.py` 仍是零变更全绿。谁把因子轴那条改回"零变更"，
   谁就把 AL-4 改空了。
4. **AL-9 按 `2 × backtests_per_segment()` 计**，并用 §7.3-9 的 3×2 成员 + cap=10 场景钉住
   （按 `per_segment` 计的实现会处理 3 个而不是 2 个）。
5. **`next_check_ts` 四条分支全部实现**，且有「连续三 tick 取到不同资产」的端到端判据
   （P1-5）。
6. **memo 纪律**：一组一份 df、per-asset 差异全部经 `split_at`、memo 跨组必新建；配
   §7.3-18 的逐位相等判据（P1-2）。
7. **§2.11 的一次性回填必须交付**（D-29），含幂等、有界、fail-open、`backfilled` 标注、
   `next_check_ts` 错峰五条纪律；在**真有存量 kept 候选**的库上跑通 §7.3-16。
8. **`discoveryTypes.ts` 的两个计数键先补再改 Monitor**（P1-8），保住 DoD 15 的"零新增
   tsc 错误"。
9. **`accept_thresholds_json` 逐轴取对配置键**（`thresholds` / `strategyThresholds`），
   配 P1-10 的"非默认值逐键相等"判据——取错了不报错，只是 AL-3 第二句整条空转。
10. **H3 的分母用 fresh 段现算**，并配"暖机段全 NaN、fresh 段全有限 ⇒ 不判 H3"的反向用例
    （P1-3）。
11. **策略 `retention` 的非正分母一律 `null`、H5 一律跳过**（P1-6），配对应用例。
12. 实施中若发现十二条承重条款（AL-1…AL-12）任一不可行，**先回本文改**再改代码，并在
    §十一 如实记录——沿用上游放行条件 7。§十一 另须补记 P0-1 的实测量级：一次因子体检 /
    一次策略体检的墙钟，以及 `discovery_health_warmup_bars` 与 `discovery_health_window_bars`
    的实测取值是否够用。

### 未列为阻塞、但请实现节点留意的两点

- **`variantCount > 1` 的展示是诚实性红线**（P1-7）：`factor_combo` 的身份不含权重是与
  promote 同源的必然结果，但用户看到一行、系统只体检其中一组，这件事必须写在脸上。
- **回填资产会立刻拿到一批"真样本外"结论**，其中很可能有相当比例是 `decayed`。这是正确的
  结果，不是 bug；但 UI 首屏出现一片红是很强的信号，建议总览卡在回填后的首个周期内附一句
  「以下结论基于上线前挖到的成果的首次样本外体检」，避免用户误以为是新挖出来的东西不行。

---

## 十二、终审记录（提交节点）

> 终审节点：loop `tge-3635bc21` 第 3 轮 · 代码评审与提交。本节所述的复核与修复
> 已随本特性一并合入（52 文件 / +8000 余行，提交哈希见 `git log`）。

### 12.1 逐条核对结果

| 维度 | 结论 |
|---|---|
| **覆盖度** | §3.1 后端新增 7 模块、§3.2 既有 7 处改动（含 `iteration_stats.py` 的「不改」）、§3.3/3.4 前端新增 12（9 实现 + 3 测试）与修改 10、§3.6 配置文档 3 处——**逐条落地，无遗漏**。§7.1 的 13 个后端套件与 §7.2 的 5 个前端套件全部存在且全绿 |
| **承重条款** | 抽查源码逐条核对 AL-1（`frozen_direction` 只在 `("long","short")` 时生效，其余一律回落 `auto`，默认值下 `evaluate_split` 逐位不变）、AL-2（`fresh_start_of` 的 `max(minedEndTs, checkEnd − maxFreshBars × step)` 夹上界那一行在）、AL-3（`_measure_factor` 走 `evaluate_split`、`_measure_strategy` 走 `run_split_backtest`，本轮零新指标）、AL-8（`_status_of` 对 `error`/`unknown` 一律返回原 `consecutive_fail`）、AL-9（`_estimate_backtests` 的 `2 ×` 与 `cap` 判据构造复核，`cap=10` + 3×(2 成员) 场景确为处理 2 个）、AL-12（`_group_by_market` 按 `(instId,bar)` 且 `_GroupData` 一组一份 `memo`）——**无偏离** |
| **回归风险** | `evaluate_split` 是四条已发布搜索轴共用的求值函数，两个新字段均为带默认值的可选项；`promote.py` 的三处提取里只有 `factor_match_key` 是有意语义变更（D-27）。实测：`test_discovery_factor_lane_golden.py` / `test_discovery_promote_strategy.py` / `test_discovery_validation.py` 零变更全绿 |
| **命名 / 风格 / 注释** | 与两份上游一致；I-10 的四处「本轮已淘汰」改名逐处核对到位（分组标题 / 模块 docstring / 候选详情徽标 / 候选表状态列），且各带 tooltip 区分资产级「已衰减」 |

### 12.2 终审发现并当场修复的 5 处（均非阻断，已直接改在源码里）

| # | 位置 | 问题 | 处置 |
|---|---|---|---|
| **RV-1** | `DiscoveryHealthTrend.tsx` / `DiscoveryOverview.tsx` | **Markdown 强调语法漏进了 JSX 文本节点**：`**不与相邻点连线**` 与 `**上线前挖到的成果**` 会原样渲染成一对星号给用户看。这是「注释里的写法被顺手复制到渲染文案」的典型 | 两处改为 `<b>`。仓库里另有几处**已发布**的同款（`DiscoveryOverview` 的「关于本功能」卡、`DiscoveryMonitor` 的 LLM 提示），不属本轮范围，未动 |
| **RV-2** | `DiscoveryHealthTrend.tsx` | 折线 tooltip 里把留存度百分比**又格式化了一遍**，与 `discoveryHealth.formatRetention` 是两份实现——`null` 的显示口径迟早分叉（表格显示 `—`、tooltip 显示别的） | 改为调用 `formatRetention`，与表格同源 |
| **RV-3** | `DiscoveryHealthBadge.tsx` | docstring 声称「表格、详情抽屉、总览卡**三处**复用」，实际总览卡不画徽标（它只复用同一份中文与色板），是一句会随时间腐烂的注释 | 改写为实情 |
| **RV-4** | `config.py` | 本轮顺手删掉了 `discovery_resume_delay_s` 与「三条策略轴」注释块之间的空行，破坏了该文件既有的分组留白风格，且与本轮无关 | 复原 |
| **RV-5** | `promote.py` / `asset_repositories.py` | `promoted_payload`（由 `_promoted_payload` 更名而来）与 `candidate_ids_by_hash` 的续行缩进各差 1 列，未与左括号对齐 | 对齐 |

### 12.3 提交范围与红灯基线

- **staged 52 文件**，与 §11.4 的清单一致。工作树里另一个在制特性（AI 交易总闸
  `ai_trade_enabled` 由 `False` 翻成 `True`）**一行未动**：共享的两个文件
  （`.env.example` / `config.py`）用 `hash-object -w --path` 直造精确 blob +
  `update-index --cacheinfo` 只暂存本特性的部分（bytes 读写，避免 Windows 下 CRLF 被改写）；
  纯属另一特性的 `desktop/src/lib/config.js` / `test_config.py`、孤儿测试
  `test_factor_library_expansion.py` 与 `live_auto_repositories.py` 一律留在工作树。
- **暂存树自洽性实测**：`git write-tree` → `commit-tree` → 临时 worktree 实体化后，在那棵树上
  实跑 `pytest -k "discovery or config"` **633 passed**（这一步是必须的——index 里的
  `config.py` 与工作树里的那份并不相同，只跑工作树等于没验）。
- **全量回归**（本机现测，非记忆基线）：后端 `2547 passed / 15 failed`，15 条逐条同名于实施节点
  记录的基线——7 条 `test_ai_trade_prior_session`、7 条未跟踪孤儿
  `test_factor_library_expansion`、1 条已知全量顺序 flake `test_agent_ws`，**零新增红灯**；
  Discovery 前端全域 `12 文件 / 106 用例` 全绿；`tsc --noEmit` 只剩既有
  `WeightBatchArena.test.tsx` 一条。
