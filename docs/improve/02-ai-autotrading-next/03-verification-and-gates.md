# 03 · 验证体系：让每次参数变更可被证伪

> **指针**：根因编号沿用 [`docs/improve/01-ai-autotrading/00-总纲与现状诊断.md:124-136`](../01-ai-autotrading/00-总纲与现状诊断.md) 的 D-1~D-18，本轮新编号见 [00 号 §二](./00-overview-and-rediagnosis.md)。闸 0 到闸 4 的编号沿用前作 [`06-离线评估与上线闸门.md`](../01-ai-autotrading/06-离线评估与上线闸门.md)，本篇不重编。
>
> **读者与用途**：写给后端实现工程师与做上线裁决的人。读完这一篇，你应当能判断一次阈值变更该跑哪几道闸、每道闸的输入输出字段是什么、以及哪些结论在这套体系下**注定拿不到**。

**本篇结论先行**：回测的决策链已经与实盘同构，这是 `ai_auto/backtest.py:3-7` 写成一等命题的事；纪律预设在两侧也已同源，回测启动体接同一个 `_resolve_discipline`（`api/ai_auto.py:689-692`，契约在 `schemas.py:1071-1074`）。缺的是把这份同构性变成**产物**的那一步：`ai_auto/gate_report.py` 今天不存在，`engine/walk_forward_purge.py:10-11` 明确记录 `plan_purged_windows` 本轮不做。结果是没有任何一份可复现的报告能支撑一次阈值变更。**本篇的交付物是一份报告，不是一套新引擎。**

---

## 一、现状盘点

### 1.1 已经有的

| 能力 | 落点 | 备注 |
|---|---|---|
| 同构回测运行器 | `ai_auto/backtest.py`（1385 行） | 决策脑不变，只换执行落地与数据源（`:3-7`） |
| 回测的三重成本护栏 | `config.py:558-560` | 决策间隔、轮数硬上限 400、并发作业上限 2 |
| 纪律预设两侧同源 | `api/ai_auto.py:689-692` | 实盘与回测共用一个解析点，R-3 |
| 事件研究的分段与 purge-embargo | `engine/walk_forward_purge.py`（50 行） | `plan_segments` 与 `purge_crossing_events`，供 `prior_builder` 消费 |
| walk-forward 窗口切分 | `engine/backtester.py:26` 的 `plan_walk_forward_windows` | 只切窗口，不含 purge |
| 跨会话池化体检 | `ai_auto/profitability.py`（276 行） | 冻结结论表，含「样本不足，不下结论」 |
| 归因读数 | `ai_auto/attribution.py`（287 行）+ `stats_ext.py`（168 行） | 净期望、成本占比、t 统计量、漏斗统计 |
| 校准三问 | `ai_auto/calibration.py`（120 行） | 门禁分子、成本模型、置信度三条可靠性读数 |

### 1.2 缺的

**闸门报告产物**。`ai_auto/gate_report.py` 在前作 06 号里是一个提案模块，至今未建。今天一次阈值变更能拿出的最好证据是一张统计页截图。

**训练与测试窗口的 purge**。`engine/walk_forward_purge.py:10-11` 里有一句裁决记录：`plan_purged_windows`（train/test 窗口规划）本轮不做，理由是它没有消费方，窗口的真实形状要等 out-of-fold 先验那一轮才知道。这条裁决在当时是对的，但它意味着**闸 1 今天没有实现基座**，本篇必须把它重新提上来。

**决策重放，以及它所需要的落库面**。今天落库的只有成交行（`db/repositories.py:278-310`），观望轮与被拦下的开仓意图都不进库，因此没有任何一条路径能用历史输入重跑一遍确定性层。

**影子灰度与 CI 挂钩**。两者都不存在。

### 1.3 闸 0 有一部分注定对不齐

前作 06 号的自我反驳二预判了这件事，本次回读确认它已经从「未对齐」变成了「有意不对齐」。`backtest.py:290-293` 把 `allow_limit=False` 显式写出并说明理由：`SimBroker._pending` 是「次根开盘无条件成交」的一根队列，`_Order` 连价格字段都没有，放任 Agent 在回测里挂限价而每单必成会系统性高估限价策略。实盘侧 `ai_trade_limit_orders_enabled` 默认 `True`（`config.py:196`）。

反思层同理：回测的 `reflection_enabled` 默认 `False`（`backtest.py:155`），实盘的 `ai_trade_reflection_enabled` 默认 `True`（`config.py:168`），而 `AiBacktestStartIn`（`schemas.py:1041-1074`）没有对应的入参，请求侧无法对齐。

**正确处置是在报告里标注结论的适用范围**，不是伪造一个对齐。闸 0 的产物因此包含一列「本次对齐状态」，取值为「已对齐」「有意不对齐（附理由）」「无法对齐（附影响）」。

---

## 二、五道闸的落地形态

沿用前作 06 号的编号与含义（该文件本次已核实，闸 0 在 `:21-31`、闸 1 在 `:33-45`、闸 2 在 `:47-49`、闸 3 在 `:51-59`、闸 4 在 `:61-63`）。本篇把每道闸变成 `ai_auto/gate_report.py` 的一个可执行检查项。

| 闸 | 输入 | 输出字段 | 通过判据 |
|---|---|---|---|
| **闸 0** 链路同构性 | 实盘 settings 副本 + 回测 spec | `rows[]`（配置项、回测值、实盘值、对齐状态、影响） | 无「无法对齐」行，或全部「无法对齐」行都带影响说明 |
| **闸 1** purged walk-forward | 历史 K 线 + 窗口计划 + 标签长度 | `windows[]`（区间、笔数、净收益）、`medianNetBps`、`positiveRatio` | 净收益中位数 > 0 且为正的窗口占比 ≥ 0.60，窗口数 ≥ 8 |
| **闸 2** 成本敏感性 | 闸 1 产物 + 成本乘数 1.5 | `medianNetBpsStressed` | 乘 1.5 后中位数仍 > 0 |
| **闸 3** 决策重放 | 历史 payload 批 + 新旧参数 | `actionAgreement`、`openRateBefore/After`、`evShift` | 一致率 0.50 ~ 0.90，且开仓率不为 0 且不低于改造前的 30% |
| **闸 4** 纸面灰度 | 纸面会话 50 笔 | `slippageModelVsActualBps` | 偏差 < 3 bps |

**闸 1 需要先补 `plan_purged_windows`。** 它是一个纯函数，签名在前作 06 号 `:78-79` 已经写好：给定窗口计划与标签长度，返回 `(train_start, train_end, test_start, test_end)`，其中 `train_end` 已扣 purge、`test_end` 后留 embargo。实现时复用 `walk_forward_purge.plan_segments` 的切法，避免与既有事件研究口径分叉（`walk_forward_purge.py:7-8` 已经为这条写了一句「绝不在此另立一套切分口径」）。purge 长度取标签的最长持有期，embargo 取 purge 的一半，两个默认值与 `ai_trade_prior_embargo_bars = 12`（`config.py:502`）保持同一量级。

**报告的最小字段集**：`gateId`、`verdict`（`pass` / `blocked` / `not_applicable`）、`inputs`（含数据区间与样本量）、`metrics`、`notes`、`runCount`（同一参数族累计运行次数）、`createdTs`。`runCount` 是防过拟合的核心字段，理由见 §六。

**存放位置**：`docs/gate-reports/<yyyy-mm-dd>-<参数键>-<序号>.md`，与仓库既有的 `docs/diagnoses/` 同级。报告是文档而非数据库记录，因为它的读者是人。

> 提案（proposal）· 未实现

```text
# ai_auto/gate_report.py（约 200 ~ 240 行，离线工具，绝不进决策轮）
GateVerdict = Literal["pass", "blocked", "not_applicable"]

def gate0_isomorphism(live_settings, backtest_spec) -> dict:
    # rows[]: {key, backtest_value, live_value, status, impact}
    # status ∈ {"aligned", "intentionally_diverged", "unalignable"}

def gate1_walk_forward(windows, *, label_bars, purge_bars, embargo_bars) -> dict
def gate2_cost_sensitivity(gate1_result, *, cost_multiplier=1.5) -> dict
def gate3_replay(replay_result, *, baseline) -> dict
def gate4_paper(paper_trips) -> dict

def build_gate_report(results, *, param_key, run_count) -> dict:
    # verdict = "blocked" 当且仅当任一子闸 verdict 为 "blocked"
    # "not_applicable" 不阻断，但必须在报告里写明原因
```

`build_gate_report` 的裁决规则刻意把 `not_applicable` 与 `blocked` 分开：闸 3 在决策快照表建成之前恒为前者，若把它当作阻断，整套体系在落地当天就会卡死；若把它当作通过，报告就在撒谎。第三种取值是唯一诚实的出路。

---

## 三、决策重放

**要回答的问题**：若当时 `k_min` 取 2.0 而不是现行的 1.5，会怎样。今天回答这个问题的唯一办法是改配置重跑一次真实会话，成本是时间加真金白银。

**可重放的部分**：确定性层全部可重放。基础策略铁律（`ai_auto/base_rules.py`）、最小 edge 门禁（`edge_gate.py`）、共识与置信度门槛、波动率目标化仓位（`sizing.py`）、有效敞口上限（`exposure_book.py`）、回撤刹车（`drawdown_brake.py`）、持仓周期铁律（`hold_horizon.py`）都是纯函数或近纯函数，喂同样的输入必得同样的输出。零 LLM 成本。

**不可重放的部分**：LLM 决策本身。同一个 payload 喂给模型两次不保证得到同一个 decision，而且新参数会改变 prompt 里的成本段与纪律段，模型的回应也会随之改变。

**为什么后者不可重放不影响结论有效性**：重放要回答的是「同一个开仓意图在新阈值下会不会被拦」，这个问题的判定完全落在确定性层。把 LLM 决策固定为历史上真实发生过的那一个，重放测的就是纯粹的门禁效应，这恰好是单变量归因需要的形状。重放**不能**回答「模型看到新的成本段之后会不会少提出几次开仓意图」，那个问题只能靠真实会话或纸面灰度回答，闸 4 就是为它设的。

> 提案（proposal）· 未实现

```text
# ai_auto/replay.py（或并入 gate_report.py）
def replay_decisions(payloads, *, old_settings, new_settings) -> dict:
    # payloads: 历史决策 payload 与当时真实产出的 decision（已落库）
    # 逐条把 decision 重新喂给确定性层，比较新旧两组的最终动作
    # 返回 {"actionAgreement": float, "openRateBefore": float, "openRateAfter": float,
    #        "blockedByLayer": {层名: 计数}, "n": int}
    # 绝不调用任何 LLM；绝不写库；绝不产生任何委托
```

**样本来源需要新建一个落库面，这是本篇最贵的一项前置工作。** 今天落库的只有成交行：`db/repositories.py:278-310` 的 `append_live_trade` 把 `LiveExecutor` 产出的 trade dict 写进 `Trade` 表，附带一个 `explain_json`。观望轮、被门禁拦下的开仓意图、当时的成本断面与 ATR 读数**都不在库里**，而重放恰恰需要它们（被拦下的那些意图正是要重新判定的对象）。

因此闸 3 的落地顺序是：先加一张只写不读的决策快照表（每轮一行，含 payload 的确定性输入子集与当轮真实产出的 decision），攒够 200 轮之后闸 3 才可跑。表的写入必须是 best-effort 且不阻塞决策轮，姿态照抄 `append_live_trade` 的调用方约定（落库失败不阻塞交易，`repositories.py:283`）。在这张表建起来之前，闸 3 的 verdict 一律填 `not_applicable` 并写明原因，**不得**用成交行反推一个假的重放结论。

**一条必须遵守的边界**：重放**只降不升**。若某个层在重放中把一个历史上被放行的开仓改成了拦截，那是有效结果；若重放把一个历史上被拦截的开仓改成了放行，需要人工复核该层的极性是否被改错。`edge_gate.py:9-10` 写明了这条不变式：唯一可能的输出变化是 `open_* → hold`，`close` / `cancel_order` / `hold` 永不被触碰。

---

## 四、样本量与显著性

**要下什么结论需要多少笔**。阈值全部复用仓库既有配置，不另立一套。

| 结论 | 需要的样本量 | 判据 | 出处 |
|---|---|---|---|
| 「这组参数的净期望显著为正」 | 池化 ≥ 30 笔往返 | `t` 的绝对值 ≥ 2.0 | `config.py:563-564` |
| 「某个置信度分箱确实更准」 | 该箱 ≥ 10 笔 | 箱内胜率对照 | `config.py:565` |
| 「战绩读数可以回喂模型」 | 同 `(instId, bar)` 历史 ≥ 20 笔 | 四条装配门禁 | `config.py:567`、`track_record.py:12-16` |
| 「成本读数可用」 | 带费往返占比 ≥ 0.5 | `feeCoverage` 门禁 | `config.py:570` |
| 「walk-forward 结论成立」 | ≥ 8 个窗口，每窗口 ≥ 30 笔 | 中位数 > 0 且正窗口占比 ≥ 0.60 | 每窗口 30 笔与两条判据出自前作 06 号 `:43`、`:45`；窗口数 ≥ 8 出自同篇 `:91`（闸 1 那一行的「采样窗口」列），本篇原样沿用、未另立新值 |

表里的 0.5 与 [00 号 §五](./00-overview-and-rediagnosis.md) M4 出口条件里的 0.90 是两条服务不同判断的线，不要互相替换。`config.py:570` 的 0.5 管的是「这一句成本文案能不能下发给模型」，低于它就整行省略而不是下发一个由「没记费率」算出来的 0.0 bps（`config.py:568-569`）；M4 的 0.90 管的是「这批往返能不能拿去标定一个真实资金上的阈值」，后者容不下 50% 的缺失率。闸门报告引用覆盖率时必须写明自己用的是哪一条。

**分组池化是唯一出路**。`profitability.py:3-4` 记录：一个分组的往返数中位只有个位数，28 个分组里 10 组零成交。在单个分组上永远攒不出 30 笔，于是每一层新纪律上线时都带着 5 笔样本的「证据」，下一轮再叠一层，证据基座永远追不上机制复杂度。

池化的两条纪律必须一起用：一切比例都按 `Σwins / Σtrips` 池化，**永远不对比值求平均**（`profitability.py:12-13` 给了实测差距 4.63 个百分点）；每个比例与中位数类读数都必须与它自己的分母同行出现，`expectancyR` 的分母是 `rSamples`，与 `expectancyUsd` 的 `roundTrips` 不是同一个数（`:14-15`）。

**一个诚实的限制**：池化会把不同标的、不同周期、不同纪律档位的往返混在一起。`GET /ai-trade/history/profitability` 提供了 `instId` / `bar` / `preset` / `horizonProfile` 四个过滤维度（`api/ai_auto.py:543-561`），闸门报告必须记录本次用了哪几个过滤条件。不记录过滤条件的 t 值没有意义。

---

## 五、流程约束

写成命令式步骤，每步给出预期输出。

1. **判定变更是否需要报告。** 修改 `config.py` 里任一 `ai_trade_*` 的出厂默认值，**或**修改 `ai_auto/discipline.py:47-102` 的冻结预设表，都需要。只改注释或只改标签不需要。预期输出：一个是或否的判定，写在 PR 描述里。
2. **跑闸 0。** 输入本次变更后的实盘 settings 副本与对应的回测 spec。预期输出：一张逐项对账表，每行带对齐状态。存在「无法对齐」行且未附影响说明时，后续闸门一律不跑。
3. **跑闸 1 与闸 2。** 预期输出：`windows[]` 至少 8 行，`medianNetBps` 与 `positiveRatio` 两个数，以及成本乘 1.5 之后的同两个数。
4. **跑闸 3。** 输入 200 条历史 payload。预期输出：`actionAgreement` 落在 0.50 到 0.90 之间，`openRateAfter` 不为 0。开仓率为 0 时立即判定双重计费并阻断。
5. **跑闸 4（仅高风险变更）。** 预期输出：50 笔纸面往返的实测滑点与模型滑点偏差 < 3 bps。
6. **归档报告并在 PR 里链接它。** 预期输出：`docs/gate-reports/` 下一个新文件，PR 描述里有指向它的相对链接。
7. **合并。** 报告 verdict 非 `pass` 的变更不进主干。

**哪种变更跑哪几道闸**：

| 变更类型 | 闸 0 | 闸 1 | 闸 2 | 闸 3 | 闸 4 |
|---|---|---|---|---|---|
| 改 `config.py` 里某个 `ai_trade_*` 出厂默认值 | 必跑 | 必跑 | 必跑 | 必跑 | 视风险 |
| 改 `discipline.py` 冻结预设表的某一行 | 必跑 | 必跑 | 必跑 | 必跑 | 必跑 |
| 新增一个默认关闭的总闸 | 必跑 | 免 | 免 | 免 | 免 |
| 只改文案、标签或注释 | 免 | 免 | 免 | 免 | 免 |
| 紧急修复（生产事故止血） | 必跑 | 免 | 免 | 必跑 | 免 |

改冻结表比改 `config.py` 默认值更严，因为前者直接改变每一个新会话的实际行为，而对应的出厂默认值只在纪律整轴关闭时才露面。**这条宽松只对冻结表覆盖的那 16 个键成立**：其余 176 个 `ai_trade_*` 没有任何预设盖住它们，改它们的出厂默认值就是改每一次会话的实际行为，上表第一行对它们照样适用，其中闸 4 那一格的「视风险」要按会话行为被改变的幅度从严判断。这条区分与 [02 号 §一](./02-calibration-and-activation.md) 的「出厂默认与会话生效是两套数」是同一件事的另一面，也是它最容易被推广过头的地方。

**紧急修复的例外**：只允许跑闸 0 与闸 3，且必须在 PR 描述里写明跳过了哪几道闸以及为什么。这条沿用前作 06 号 `:105` 的既定做法。

### 5.1 本体系自身的验收指标

闸门体系也需要被验收，否则它会变成一份没人跑的流程文档。

| 指标 | 定义 | 阈值 | 采样窗口 | 不达标处置 |
|---|---|---|---|---|
| 报告覆盖率 | 附了闸门报告的阈值变更数 / 阈值变更总数 | = 1.00 | 每季度 | 无报告的变更一律回滚，并在下一次评审中复盘为什么绕过 |
| 闸 0 可对齐率 | 状态为「已对齐」的行数 / 对账表总行数 | 记录为准，且「无法对齐」行必须逐条带影响说明 | 每次运行 | 有「无法对齐」行且无说明即阻断后续闸门 |
| 闸 1 窗口数 | 单次运行产出的 walk-forward 窗口数 | ≥ 8 | 每次运行 | 不足则延长历史区间；缩短窗口长度不在允许范围内 |
| 同族运行次数 | 同一参数族的 `runCount` | ≤ 5 | 每次运行 | 超出即强制换用新的样本外区间 |
| 闸 0 到闸 3 耗时 | 四道闸自动化跑完的墙钟时长 | < 10 分钟 | 每次运行 | 超时则优先优化闸 3 的重放批量，不得删减闸 1 的窗口数 |
| `not_applicable` 占比 | 判定为不适用的闸数 / 5 | 逐轮下降，落地首季允许 ≤ 0.40 | 每季度 | 不下降说明前置工作（决策快照表、`plan_purged_windows`）停滞 |

---

## 六、风险与回滚

**风险一：闸门本身会成为过拟合的通道。** 反复用同一批历史数据跑闸门、按结果调参、再跑闸门，本质上是把测试集变成训练集。前作 06 号 `:101` 已经给了缓解办法并规定了阈值：同一参数族运行超过 5 次即强制换用新的样本外区间，报告必须显示累计运行次数。本篇把它落成 `runCount` 字段，缺这个字段的报告视为无效。

**风险二：闸门过严会让迭代停滞。** 五道闸全跑一遍，闸 4 的 50 笔纸面在 15m 周期上可能需要数天。若这个周期长到团队开始绕过闸门，闸门就失去了意义。缓解是把闸 0 到闸 3 做成分钟级可跑完的自动化步骤，只有闸 4 需要真实时间，并且闸 4 只对高风险变更强制。

**风险三：重放与实盘的偏差有上界，但这个上界目前未知。** 重放固定了 LLM 决策，因此它系统性地低估了参数变更的总效应（模型看到新 prompt 之后的行为变化被排除在外）。这个偏差的方向是确定的（重放给出的动作变化量是一个下界），量级未知。`[unverified: 需要一次同参数的真实会话与重放的逐轮对照才能给出量级]`。报告里必须写明这一条，不得把重放结论当作实盘结论。

**风险四：CI 挂钩的运行时长预算。** 闸 1 与闸 2 各要跑一遍 walk-forward，闸 3 要重放 200 条 payload。若挂进每次提交的 CI，会显著拉长反馈周期。建议只在触碰 `config.py` 的 `ai_trade_*` 或 `discipline.py` 冻结表的 PR 上触发，其余提交不跑。

**自我反驳**：这套闸门体系的最强结论仍然是「没有被证伪」，而不是「有正期望」。前作 06 号 `:97` 已经把这句话定为写进报告首页的纪律条款，本篇原样保留。理由是本仓库回测有七条已知偏差（六条精度问题写在 `backtest.py` 的回测免责文案里，第七条是 §1.3 那两项有意不对齐）。正期望的唯一证据来自实盘的滚动净期望统计，闸门只负责在参数进入实盘之前把明显负期望的那些挡掉。

**回滚路径**：闸门是离线工具，不参与实盘执行路径，因此不需要总闸。关闭闸门只意味着回到当前的无报告状态。真正需要回滚的是被闸门放行的参数，其回滚落点见 [02 号 §四](./02-calibration-and-activation.md)。

**交叉阅读**：闸门赖以工作的成本口径见 [01 号](./01-evidence-layer.md)，被闸门裁决的阈值变更见 [02 号](./02-calibration-and-activation.md)，报告结论怎么呈现给用户见 [04 号 §三](./04-product-and-robustness.md)，全局排序见 [00 号](./00-overview-and-rediagnosis.md)。
