# 02 · 标定与验证：给已经在跑的阈值一个来历

> **指针**：根因编号沿用 [`docs/improve/01-ai-autotrading/00-总纲与现状诊断.md:124-136`](../01-ai-autotrading/00-总纲与现状诊断.md) 的 D-1~D-18，本轮新编号见 [00 号 §二](./00-overview-and-rediagnosis.md)。本篇不另记实施状态。
>
> **读者与用途**：写给实盘操作者与后端实现工程师。读完这一篇，你应当能判断当前会话跑的是哪一组阈值、下一个该动哪一项、观察多少笔之后才能下结论、以及什么读数出现时必须回滚。

> **写作前提（必读）**：四档预设（`off` / `steady` / `balanced` / `aggressive`）、八个阈值的逐字段覆盖入参（`AiTradeDisciplineIn` 共 10 个字段，另两个是 `enabled` 与 `preset`，见 `schemas.py:806-815`）、前端档位选择器与只读阈值表**全部已经实现并默认启用**。落点分别在 `ai_auto/discipline.py:44`、`schemas.py:790-820`、`AiTradeLaunchModal.tsx:1105-1121` 与 `:1387-1428`。本篇**不设计档位体系**，只补三样今天确实不存在的东西：**阈值的标定依据、单变量调整编排、自动回滚**。

**本篇结论先行**：交易纪律预设已经在生产路径上默认生效，`config.py:538` 的 `ai_trade_discipline_enabled = True` 与 `:539-540` 的默认预设 `balanced` 让每一次从 API 层发起的启动都套上一份会话私有的阈值副本。缺的是那张冻结阈值表的来历：`ai_auto/discipline.py:36` 对自己写的是「**这些数字没有经过回测校准**」，`:24-34` 说明它们取自默认成本断面与 ATR 粗估的推演。本篇给出每一项的标定方法、一次只动一项的调整顺序、以及三条自动回滚判据。**编排的第一步是按 N-1 判定这个周期值不值得调**，确认值得之后才轮到动参数。

---

## 一、今天到底跑的是哪套数

这是全集最容易读反的一处，值得把机制完整讲一遍。

**纪律预设覆盖的那 16 个键里，十个是闸的总开关或门禁阈值，出厂默认值全停在关闭位**（`k_min` 0.0、`min_consensus` 0、`min_confidence` 0.0、`sizing_enabled` False、`effective_exposure_cap_mult` 0.0、`stop_atr_mult` 0.0、刹车三档全为 0、硬止盈 0.0）；另外六个带着非零出厂值（`vol_target_annual` 0.20、`kelly_lambda` 0.25、`realized_vol_window` 96、`stop_rr` 2.0、`brake_slow_size_mult` 0.50、`brake_loss_cooldown_bars` 4.0），它们所属的闸没装配时不起作用；出厂取值见 `config.py:513-515`、`:519`、`:528`、`:531`，四档对照见 [00 号 §1.2](./00-overview-and-rediagnosis.md)。两类的共同点是实盘跑的都不是它们。`config.py:533-535` 逐字写明：那一整排 `ai_trade_*` 阈值的既有默认值本轮一个都没动，改变的只有一件事，从 API 层启动的会话会默认套上一份预设副本。因此「打开 `config.py` 看到 `edge_gate_k_min = 0.0` 就断定门禁没在工作」是一个明确的误读，那段注释本身就是为了防止这种读法而写的。

**副本机制**：`api/ai_auto.py:131` 用 `settings.model_copy(update=preset.updates)` 派生一份新的 Settings 对象。`ai_auto/discipline.py:14-17` 说明了为什么必须是副本而不是 `setattr`：`get_settings()` 是进程级缓存单例，而 `LiveExecutor` 被 `ai_auto` 与 `live_auto` 共用同一个 settings 对象，直接改全局会让 AI 侧的纪律阈值静默溢出到非 AI 链路，改写非 AI 会话的费率与止盈时点。这是一个已经发生过的坑。

**解析优先级**（`api/ai_auto.py:102`，三层）：

1. 运维总闸。`ai_trade_discipline_enabled = False` 时直接返回原 settings 对象，`is` 相等，整轴不装配（`:113-114`）。这是本特性**唯一的一键回滚路径**，优先级高于请求体。
2. 用户显式。请求体里的 `preset` 与八个覆盖字段（映射表在 `discipline.py:109-118`）。把「关闭」与覆盖字段同时发出会得到 422 `AI_TRADE_DISCIPLINE_CONFLICT`（`:117-123`），静默丢弃用户填的数在本仓库是明令禁止的形状。
3. 服务端默认。取 `ai_trade_discipline_default_preset`，出厂为 `balanced`。

**两条容易踩的运行期行为**。`min_consensus` 会被按本会话真实策略数夹紧（`discipline.py:224-231`），单策略会话选 `steady` 时门槛 2 被夹到 1，并在回执的 `adjustments` 里如实披露。装配仓位层之后暖机根数可能被自动抬高（`api/ai_auto.py:136-141`），因为波动率窗口与相关性样本要求更多收盘价；这条抬升同样会如实披露，默认 `primingBars = 300` 时不触发。

**解析顺序有硬约束**：`_resolve_discipline` 必须排在所有其它 `_resolve_*` 之前（`api/ai_auto.py:97-100`）。预设表里含 `ai_trade_profit_guard_hard_tp_return_pct`，派生副本晚一步产生，那一行预设就是死字母且不报任何错。改动本篇提案的任何接入点时，这条顺序不可动。

**一张速查表：某个键在一次真实会话里会不会被覆盖。**

| 键的来源 | 是否被预设副本覆盖 | 怎么改 |
|---|---|---|
| 冻结表里的 16 个键（`discipline.py:47-102`） | 是 | 换档位，或用八个覆盖字段里对应的那个 |
| `OVERRIDE_TO_SETTING` 之外的冻结表键，共 8 个：`sizing_enabled`、`kelly_lambda`、`realized_vol_window`、`effective_exposure_cap_mult`、`stop_rr`、`brake_slow_size_mult`、`brake_loss_cooldown_bars`、`profit_guard_hard_tp_return_pct` | 是，但**无法逐项覆盖** | 只能换档位，或改 `agent.env` 后重启 |
| 其余 176 个 `ai_trade_*` | 否 | 改 `agent.env`（前缀 `AI_TRADE_`，透传见 `desktop/src/lib/config.js:74-82`）后重启后端 |

第二行是一个容易被忽略的缺口。16 个冻结表键里只有 8 个开了逐字段覆盖（`discipline.py:109-118` 的 `OVERRIDE_TO_SETTING`），另外 8 个只能整档切换或改环境变量；这 8 个里有 6 个是真的随档位变的（`kelly_lambda` 0.15/0.25/0.40、`effective_exposure_cap_mult` 1.5/2.0/3.0、`stop_rr` 2.0/2.0/1.8、`brake_slow_size_mult` 0.50/0.50/0.60、`brake_loss_cooldown_bars` 6.0/4.0/3.0、`profit_guard_hard_tp_return_pct` 0.30/0.50/0.0），`sizing_enabled` 与 `realized_vol_window` 三档同值（本次逐档对拍 `discipline.py:47-102`）。最该被注意的是 `effective_exposure_cap_mult`：它是多标的分组唯一的组合敞口约束，三档差了一倍，却没有任何覆盖入口。这条记进 [04 号 §六](./04-product-and-robustness.md) 的「配置可达性」指标。

---

## 二、标定方法（本篇核心）

**基准是预设现值，不是 0。** 每一项标定都要回答同一组问题：用哪个实测读数反推、样本量前置条件是多少、样本不足时的默认动作是什么。默认动作一律是「不动这一项」。

数据来源统一走 `GET /ai-trade/history/profitability`（`api/ai_auto.py:543-561`），它支持按 `instId` / `bar` / `sinceTs` / `preset` / `horizonProfile` 过滤，返回的是**池化**口径。池化而非对各组求平均，这条在 `profitability.py:12-13` 有实测依据：池化胜率 40.74% 与各组胜率的算术平均 36.11% 相差 4.63 个百分点。

| 阈值 | `balanced` 现值 | 用什么实测读数反推 | 样本量前置条件 | 样本不足时 |
|---|---|---|---|---|
| `edge_gate_k_min` | 1.5 | `calibration.py` 问一（门禁分子准不准）的 `signAgreementRate` 与已实现毛额中位数，配合问二（成本模型准不准）给出的实测往返成本 | 带费往返 ≥ 30 笔（`config.py:563`），且腿费覆盖率 ≥ 0.90 | 不动。在 89% 腿费缺失的样本上标 `k_min` 等于把噪声固化 |
| `min_confidence` | 0.50 | `calibration.py` 问三的置信度可靠性曲线，按 `CONFIDENCE_BUCKETS` 分箱后看每箱的实际胜率 | 每箱 ≥ 10 笔（`ai_trade_profitability_bucket_min_trips`，`config.py:565`） | 不动。分箱不足时曲线的形状由个位数样本决定 |
| `min_consensus` | 1 | 按共识度分组的池化净期望对照 | 每组 ≥ 30 笔 | 不动。且注意它会被策略数夹紧，标定值必须 ≤ 本会话策略数 |
| `vol_target_annual` | 0.20 | 已实现波动率分布与实际下单仓位分布的对照，看目标波动是否真的约束了仓位 | 滚动 100 轮 | 不动 |
| `kelly_lambda` | 0.25 | **本轮不标定**。`profitability.py:7-8` 明确禁止把体检卡的 `winRate` 当作 `kelly_fraction` 的 `p_win` 输入，那是历史统计而非对未来的估计 | 不适用 | 恒不动 |
| `stop_atr_mult` | 2.0 | 止损距离与往返成本带的关系。`config.py:542-543` 记录实测 BTC 1m 上 62.3% 的 2×ATR 止损比一次市价往返还窄；成本下界由 `ai_trade_stop_cost_floor_mult`（`:544`，默认 2.0）兜住 | 带费往返 ≥ 30 笔 | 不动。成本下界已在生效，先看它触发了多少次 |
| `stop_rr` | 2.0 | 实际退出距离分布（`ai_auto/exit_attribution.py` 的退出来源）与目标盈亏比的偏差 | 已结算往返 ≥ 30 笔 | 不动 |
| `brake_slow_dd_pct` / `brake_halt_dd_pct` | 0.08 / 0.15 | 历史会话权益回撤分布的分位数。注意 `config.py:524-526` 写明这两个阈值**必须按杠杆定标**，3× 为锚点时 8% / 15% 等价于标的价反向 2.7% / 5.0% | 已停止会话 ≥ 10 个 | 不动 |
| `brake_loss_streak` | 4 | 连亏长度的经验分布，取使误触发率低于 5% 的分位 | 已结算往返 ≥ 50 笔 | 不动 |
| `effective_exposure_cap_mult` | 2.0 | 多标的分组的相关性矩阵与实际有效敞口的历史分布 | 多标的会话 ≥ 5 个 | 不动 |

**一条必须写进标定记录的注解**：`calibration.py:11-14` 说明门禁分子是 `min(atr_target_mult × ATR, OCO 止盈距离)`，它是「若走对能吃到多少」的幅度而非期望，理论上应当系统性高于已实现毛额中位（后者含走错的那一半）。因此 `signAgreementRate` 实际度量的是胜率，**不得被读成门禁预测准确率**。把这两者搞混会让 `k_min` 被标到一个系统性偏低的值。

**标定记录的最小字段集**：阈值键、旧值、新值、依据的读数名与取值、样本量、采样区间、执行人、执行时间、对应的闸门报告 id。缺任何一项即视为没有标定记录。

**一次标定的执行步骤**（每步给出预期输出）：

1. 调用 `GET /ai-trade/history/profitability?instId=<标的>&bar=<周期>&preset=balanced`。预期输出：一份池化体检卡，含 `roundTrips`、`feeTrips`、`feeCoverage`、`winRate`、`payoff`、`breakevenPayoff`、`tStat` 与一条冻结结论。
2. 检查 `feeCoverage`。预期输出：≥ 0.90。低于该值时停止，本次不产出任何标定值，转去修 [01 号](./01-evidence-layer.md) 指出的口径问题。
3. 检查结论码。预期输出：非 `insufficient_samples`。是该码时停止，延长采样区间后重来。
4. 按上表取对应读数反推候选值，写入标定记录。预期输出：一条含全部九个字段的记录。
5. 把候选值提交 [03 号](./03-verification-and-gates.md) 的闸门报告。预期输出：报告 verdict 为 `pass`，此时候选值才允许进入 §三 的编排。

---

## 三、单变量调整编排

**强制单变量**：一次只动一项。同时动两项时，任何一个读数的变化都无法归因到某一项上，而下一轮又会在这个不可归因的基础上继续调。

**第 0 步永远是可交易性复核。** 仓库已有的对症建议规则链（`ai_auto/discipline_view.py:113-121`）第一条命中即返回，其中第 2 条明写：`tradability_level == "infeasible"` 时首要建议是换周期，且必须写明「换预设解决不了」。这条规则被刻意排在「点名首拦截层」之前，因为 D-1 的实测表就是它的依据。文案在 `:139-144`，它已经把「这个标的在当前时间级别上的单笔波动幅度覆盖不了一笔往返成本」说得很清楚。

| 步 | 动哪一项 | 从 | 到（候选） | 观察窗口 | 看哪个读数 | 通过判据 | 失败动作 |
|---|---|---|---|---|---|---|---|
| 0 | 不动参数 | 当前周期 | 更长周期 | 一次探针调用 | `tradability.level` 与 `alternatives` | `level` 为 `ok` 或 `marginal` | 判定 `infeasible` 则换周期后重新开始编排，**不进入第 1 步** |
| 1 | 观察基线 | 不动 | 不动 | 滚动 30 笔往返 | 池化净期望与 t 值 | 样本量达 30 笔 | 样本不足则延长观察，不得跳过 |
| 2 | `stop_atr_mult` | 2.0 | 2.5 或 3.0 | 滚动 30 笔 | 止损触发占比、成本下界触发次数 | 净期望不劣于基线且止损触发占比下降 | 回到 2.0 |
| 3 | `edge_gate_k_min` | 1.5 | 2.0（收紧）或 1.2（放宽） | 滚动 30 笔 | 闸门生效率、决策转化率 | 闸门生效率落在 0.10 ~ 0.60 且净期望改善 | 回到 1.5 |
| 4 | `min_confidence` | 0.50 | 按可靠性曲线取的分位 | 滚动 30 笔 | 分箱胜率与该箱样本量 | 被拦下的那一箱实际胜率确实更低 | 回到 0.50 |
| 5 | 刹车三档 | 0.08 / 0.15 / 4 | 按回撤分布取的分位 | 滚动 10 个会话 | 刹车触发次数与触发后的后续表现 | 触发之后的回撤确实被截断 | 回到预设值 |

方向的选择有讲究。第 3 步同时列了收紧与放宽两个候选，选哪一个取决于第 1 步基线里的闸门生效率：高于 0.60 时**不要**先放宽 `k_min`，先回到第 0 步复核周期，因为 `discipline_view.py:108-111` 已经记录最宽松档在 1m 上仍有 93.0% 否决率，放宽 k 只能把曲线整体平移一点。

**观察窗口为什么统一取 30 笔往返。** 这个数直接复用 `ai_trade_profitability_min_trips = 30`（`config.py:563`），它是仓库里「下结论的样本下限」的唯一真源。为编排另立一个窗口长度会让同一批往返在两处得到两个结论。刹车三档取 10 个会话而非 30 笔，是因为回撤是会话级读数，往返数不是它的分母。

**第 2 步为什么排在第 3 步之前。** 止损距离决定了单仓 OCO 止盈距离，而止盈距离是 edge 门禁分子里被取最小值的两项之一（`tradability.py:24-27`）。先动止损再动门禁，第 3 步观察到的变化才归因得清；反过来做，第 3 步的读数会被第 2 步的分子变化污染。

---

## 四、自动回滚

三条判据，任一命中即回滚，且回滚是自动的而非提示性的。

| 判据 | 阈值 | 数据来源 |
|---|---|---|
| 连续零开仓 | 连续 200 轮决策无任何成交，且 `open_funnel.intents > 0`（模型确实想开过） | `ai_auto/open_funnel.py` 的漏斗计数 |
| 决策转化率跌破下限 | 产生成交的轮次 / 决策轮次 < 0.05，滚动 200 轮 | `ai_auto/attribution.py` |
| 净期望恶化 | 调整后的池化净期望低于调整前，且差值的 t 统计量绝对值 ≥ 2.0 | `profitability.py` + `config.py:564` |

`intents > 0` 这个前置条件不能省。`open_funnel.py:3-6` 说明了原因：全仓此前没有分母，只有拦截计数而没有「模型这一轮到底想不想开仓」的统计。若模型一次都没提出过开仓意图，那是模型侧问题而非纪律侧，放宽一个从未触发的闸没有任何作用，`discipline_view.py:118-119` 的第 3 条规则正是为此而设。

**回滚的落点只有两个**，不允许有第三个：

1. **回到该会话启动时的预设**。`ai_auto/discipline.py` 的冻结表是阈值取值的唯一真源，回滚就是重新读它。
2. **整轴关闭**。`ai_trade_discipline_enabled = False`，等价于 `off`，逐字回退（`api/ai_auto.py:113-114`）。

**绝不新写一条阈值分支。** 回滚路径上若出现一个「回退专用的保守值」，仓库里就有了第二份阈值口径，它与冻结表迟早分叉。这一条与 `AiTradeLaunchModal.tsx:230-232` 记录的教训同源：前端只存一个预设 id，阈值一概不在前端保存或重算，生效值恒以启动回执为准，因为前后端各存一份口径必漂移（`edgeSource` 逐键手抄吞新键是成文先例）。

---

## 五、交付物

**新模块 `ai_auto/activation_plan.py`**（预估 220 到 260 行，纯核心 + 一个计数器）。

**它与 `ai_auto/discipline.py` 的分工必须写死**：`discipline` 拥有「阈值取什么值」，`activation_plan` 只拥有「按什么顺序动、观察多久、什么条件下回滚」。两者不得各存一份阈值。`activation_plan` 读阈值的唯一方式是调用 `discipline.preset_table(preset_id)`（`discipline.py:193-195` 已经提供了这个只读副本接口）。

> 提案（proposal）· 未实现

```text
# ai_auto/activation_plan.py
@dataclass(frozen=True)
class PlanStep:
    index: int
    setting_key: str | None      # None = 第 0 步（可交易性复核，不动参数）
    from_value: float | int | None
    to_value: float | int | None
    window_trips: int
    metric_keys: tuple[str, ...]

def next_step(plan_state, *, facts) -> PlanStep | None:
    # facts 含 tradability_level / trips / conversion_rate / pooled_expectancy / t_stat
    # 返回 None = 编排已走完，或当前观察窗口未满
    # tradability_level == "infeasible" ⇒ 恒返回第 0 步，绝不推进

def rollback_verdict(plan_state, *, facts) -> dict | None:
    # 三条判据任一命中 ⇒ 返回 {"reason": ..., "target": "preset" | "off"}
    # 未命中 ⇒ None。本函数不执行回滚，只判定（判据在纯核心，执行在运行时层）
```

**一份操作 runbook**，写成命令式步骤而非可能性描述。每一步给出预期输出：

1. 打开统计页「当前」标签，读可交易性预检卡。预期输出：一个 `ok` / `marginal` / `infeasible` 判定与一个否决率百分比。判定为 `infeasible` 时执行第 2 步，否则跳到第 3 步。
2. 在启动弹窗里点「改用 <更长周期>」按钮，或改选一个 `bar` 更长的交易周期模式，然后重启会话。预期输出：新会话的预检卡判定变为 `ok` 或 `marginal`。**不要改预设**。
3. 让会话跑满 30 笔往返。预期输出：统计页「历史」标签的盈利体检卡给出一个非「样本不足」的结论。
4. 按 §三 的表逐步调整，每步只动一项，每步跑满该步的观察窗口。预期输出：每步产出一份「调整前 / 调整后」对照读数。
5. 每一步结束后核对 §四 的三条回滚判据。预期输出：任一命中即自动回到启动时的预设，并在回执里如实记录回滚原因。

---

## 六、验收指标

| 指标 | 定义 | 阈值 | 采样窗口 | 不达标处置 |
|---|---|---|---|---|
| 标定覆盖率 | 有标定记录的阈值项数 / 冻结表的 16 个键（`discipline.py:47-102` 每档的全部 settings 键；[04 号 §六](./04-product-and-robustness.md) 的「档位标定标注率」分母是另一张表的 15 行，两者作用域不同，不要互相换算） | 逐轮上升，M5 结束时 ≥ 0.50 | 每次发版 | 未覆盖的项在 UI 上必须显式标注「未标定」 |
| 编排完整率 | 走完全部步骤的编排次数 / 启动的编排次数 | ≥ 0.80 | 每季度 | 中断原因逐条归档，若多为样本不足则延长窗口 |
| 对照读数产出率 | 产出「调整前 / 调整后」对照的调整次数 / 调整总次数 | = 1.00 | 每次调整 | 无对照的调整一律回滚 |
| 回滚正确率 | 自动回滚触发后确实避免了进一步恶化的次数 / 触发总次数 | 记录为准 | 每季度 | 误触发率过高时先复核 `intents > 0` 前置条件 |
| 单变量违规数 | 一次调整同时动了两项以上的次数 | = 0 | 每次调整 | 违规即整轮编排作废重来 |

「一轮编排走完」的定义：第 0 步判定为 `ok` 或 `marginal`，第 1 步到第 5 步各自跑满观察窗口并产出对照读数，且期间没有触发自动回滚。中途换周期视为编排重新开始。

---

## 七、风险与自我反驳

**风险一：可调余量比直觉小得多。** 当前最宽松的 `aggressive` 档（`k_min = 1.0`）在 BTC 1m 上仍有 93.0% 否决率（`discipline_view.py:108-111`）。这意味着 §三 第 3 步的「放宽」方向在短周期上几乎没有空间。回滚开关是 `ai_trade_discipline_enabled = False`，它优先级高于请求体，关闭后整轴不装配。

**风险二：调紧会撞上「永不开仓」。** 仓库已有四次前科并逐条写进注释：共识门槛恒真（`discipline.py:30`）、反思致永不开单（`edge_gate.py:11-13`）、刹车第二档锁存（`drawdown_brake.py:129-131`）、换手令牌桶的容量安全阀（`turnover_discipline.py:138-141` 把 `min_capacity` 定在 2，并写明取 1 会让趋势持仓档整整 22 小时开不出下一单）。§四 的第一条回滚判据就是为这个形状设的。

**风险三：运维侧改了 env，界面上看不出来。** 八个冻结表键没有覆盖入口（见 §一 速查表第二行），只能改 `agent.env` 生效，而改完之后启动弹窗的只读阈值表仍会显示冻结表里的值。这会造成「弹窗承诺一套、运行期执行另一套」。缓解办法是让启动回执的 `discipline.thresholds` 成为唯一的生效值展示源（前端已经是这么做的，`AiTradeLaunchModal.tsx:1371` 记录了这条纪律），并在弹窗阈值表上标注「以启动回执为准」。

**自我反驳一**：若在干净样本上标定之后，池化净期望仍然与 0 在统计上无法区分（`|t| < 1.0`），正确结论是**样本量不足以支持任何阈值变更**，此时继续调参只是过拟合。这个结论必须被当作一个有效产出写进闸门报告，而不是被读成「这一轮什么都没做出来」。

**自我反驳二**：本篇给出的编排，很可能得出「这个周期上怎么调都不行」的结论。那是一个**有效结论**，它把问题从「哪个参数没调对」转移到「标的与周期选错了」，后者才是 [00 号 §二](./00-overview-and-rediagnosis.md) 排在贡献度首位的那一条。若编排跑完三轮都得到这个结论，正确动作是停止调参、按 N-3 转向盈亏比的结构问题。

**自我反驳三**：标定本身有过拟合风险。反复用同一批历史往返去反推阈值、再用同一批数据验证，本质上是把测试集当训练集。缓解办法是把标定与验证的样本按时间切开，标定只用较早的一段，验证只用较晚的一段，切点写进标定记录。这一条与 [03 号 §二](./03-verification-and-gates.md) 的闸门纪律是同一件事的两个面。

**交叉阅读**：标定所依赖的干净口径见 [01 号](./01-evidence-layer.md)，每次变更必须附的闸门报告见 [03 号](./03-verification-and-gates.md)，标定结果如何在界面上讲清楚见 [04 号 §二](./04-product-and-robustness.md)，全局排序与里程碑见 [00 号](./00-overview-and-rediagnosis.md)。
