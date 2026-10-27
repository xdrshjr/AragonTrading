# 01 · 证据层：让 edge 的分子与分母都有真数据

> **指针**：根因编号沿用 [`docs/improve/01-ai-autotrading/00-总纲与现状诊断.md:124-136`](../01-ai-autotrading/00-总纲与现状诊断.md) 的 D-1~D-18，本轮新编号见 [00 号 §二](./00-overview-and-rediagnosis.md)。实施状态看板只有前作那一份，本篇不另记。
>
> **读者与用途**：写给后端实现工程师。读完这一篇，你应当能决定微观结构与因子证据这两条数据面各自落在哪个新模块、改哪些既有文件、以及在什么读数下判定它们没做成。

**本篇结论先行**：最小 edge 门禁的判据是 `毛 edge(bps) ≥ k_min × C(bps)`，今天这个不等式的两端都建立在假设上。分母里的半价差恒等于配置常数 `1.0` bps（`config.py:451`），因为 AI 会话订阅的是一个 candle-only 包装器，books5 通道的回调被丢弃（`okx/ws_client.py:251-255`）；分子从不使用因子子系统已经算好的任何一个 IC，因为 `ai_auto/market_digest.py:13-15` 明写不接入 `engine/factors/*`。**先做分母（方案 A），再做分子（方案 B）**，因为 [02 号](./02-calibration-and-activation.md) 的全部标定工作都以分母可信为前提。

---

## 一、问题定位

### 1.1 微观结构通道已实现，AI 会话没有订阅

这两件事必须分开举证，一条证据只能证明其中一件。

**已实现的证据**：`okx/ws_client.py:179-184` 的 `_market_args` 返回三个订阅参数，其中第三个是 `{"channel": "books5", "instId": self.inst_id}`；`:226-229` 的 `_dispatch_row` 有 `books5` 分支并调用 `self._handlers.on_book(book)`；解析器在 `okx/types.py:156` 的 `parse_okx_books5`，产出 `OrderBookSnapshot`。这条链路今天在生产里是活的：图表面板的 WS 端点 `api/ws_market.py:75-89` 用的就是 `OKXMarketWSClient`，`market_channels` 取默认值 `True`，`on_book` 在 `:53-54` 把盘口快照直接转发给浏览器。

**AI 会话未订阅的证据**：`ai_auto/session.py:499` 构造的是 `OKXWSClient`，而它是一个 candle-only 兼容包装器（`ws_client.py:232-233` 的 docstring 逐字这么写）。包装器在 `:251` 把 `on_book` 固定为 `_ignore_book`，在 `:255` 把 `market_channels` 固定为 `False`。两处叠加的结果是 AI 会话既不发 books5 订阅，也不接收盘口回调。

**后果的自陈**：`ai_auto/cost_model.py:224` 在 `query_for_preview` 的参数说明里写着「盘口半价差（本轮无 books5 订阅 ⇒ None ⇒ 回落常数，D-3）」。本次检索应用代码里 `half_spread` 的 20 处引用（另有 7 处在 `services/quant/tests/`），没有任何一个调用点向 `query_for_preview` / `query_for_decision` 传入非 `None` 的 `half_spread_bps`，因此 `resolve_cost_params`（`cost_model.py:99-105`）读到的永远是 `settings.ai_trade_half_spread_fallback_bps`。

这条常数的量级值得算一遍。成本式是 `C = feeOpen + feeClose + crossingLegs × (halfSpread + impact) + fundingCarry`（`cost_model.py:3-4`），市价开仓的 `crossingLegs` 取 2（`:34`），于是默认断面上 `C = 5 + 5 + 2 × (1 + 0.5) = 13` bps（四个取值见 `config.py:449-452`），半价差贡献其中 2 bps、约 15%。`tradability.py:6` 用的正是这个 `13.0 bps`。问题不在它的绝对值，在于它**不随行情变化**：一个流动性骤降的断面与一个深度充裕的断面，在系统眼里成本完全一样，而门禁正是靠这个数去决定开不开仓。

### 1.2 因子子系统与决策链完全隔离

`ai_auto/market_digest.py:13-15` 写得很明确：此处的「因子」只取通用技术因子读数（RSI/MACD/BB/ATR/ROC），不接入 `engine/factors/*` 这套挖掘、评分与权重模型子系统。与此同时，那套子系统本身是完整的：`engine/factors/evaluation.py:201` 的 `evaluate_factor` 产出 IC / RankIC / 分段 IR / 分位统计，`:21` 的 `MIN_IC_SEGMENTS = 6` 是有效段数下限。

系统内部已经有一条把「历史统计」接进决策的成熟先例，就是信号先验（`ai_auto/signal_prior.py`）。它值得逐条抄作业：`:9-11` 的事件同源律要求离线统计的条件事件与在线取用的条件必须是同一个断言；`:12-13` 要求 `costBasis` 恒为 `gross`，对非 `gross` 条目直接丢弃而不做反演；`:14-15` 要求 `Σw == 0` 或契约不满足时整键缺席。方案 B 的字段契约必须与这三条对齐。

### 1.3 持有成本的真值化程度

资金费率这条已经有账本。`ai_auto/funding_ledger.py`（480 行）的四条口径里，`:14-17` 规定结算周期取不到就不计提、绝不按 8 小时猜，`:18-19` 规定 `fundingCostUsd` 为 `None` 与为 `0` 是两个结论。这个姿态是本篇两个新数据面的模板：缺席就说缺席，不用常数冒充。

真正的缺口在成本模型的持有期假设上。`config.py:454` 的 `ai_trade_cost_expected_hold_bars` 默认 `8`，而交易周期模式里 `short_swing` 的目标持仓是 12 到 48 小时、`trend_hold` 是 1 到 3 天（`ai_auto/hold_horizon.py:118-129`）。`ai_auto/cost_model.py:115-117` 已经为这个开口留了参数并写明「不同步开口会让回测按 8 根算资金费、实盘按 12 到 72 根算」。**本篇不改这个开口**，只把它记为需要在 [03 号 §五](./03-verification-and-gates.md) 的闸门报告里逐次核对的一项。

---

## 二、方案 A：微观结构接入

**新模块**：`ai_auto/microstructure.py`（预估 160 到 200 行，纯核心 + 一个轻量缓存）。

**职责边界**：接收 `OrderBookSnapshot` 序列，产出一个 `MicrostructureSnapshot`，只含三个字段：`halfSpreadBps`、`topDepthUsd`、`asOfTs`。它不算成本、不做判定、不接触任何 decision。成本装配仍然只发生在 `cost_model.py`，本模块只是把一个此前恒为 `None` 的入参填上真值。

**订阅方式**：把 `ai_auto/session.py:499` 的 `OKXWSClient` 换成 `OKXMarketWSClient`，`market_channels` 按新总闸取值，`on_trade` / `on_ticker` 传空实现，只接 `on_book`。这条改法的好处是完全复用 `api/ws_market.py:75-89` 已经在生产里跑着的那条路径，不新建任何连接管理代码。

**代价必须在这里写清楚，因为它决定了 §六 的风险量级**：`market_channels` 是一个整体开关，没有逐通道选择器。打开它会一次性订阅 `trades` / `tickers` / `books5` 三个通道（`ws_client.py:179-184` 的 `_market_args` 返回的是一个成组的列表），并为 public 端点**多起一条连接循环**（`:123-129`；K 线走 business 端点，市场通道走 public 端点，一条 socket 装不下两者）。AI 会话因此从一条 WS 连接变成两条，另外两个通道的帧收下即丢。想只订 books5 就必须给 `ws_client.py` 加一个通道选择器，那是本方案之外的改动，须单独列进 §四 的允许触碰文件表并给它自己的行数上限。

**采样频率**：books5 是高频推送，决策轮是分钟级。缓存只保留最近一帧与一个滚动中位数（窗口取 `ai_trade_realized_vol_window` 的十分之一，即约 10 帧），装配时读中位数。**绝不在决策轮内做任何遍历**，这是 `signal_prior.py:3-5` 已经写明的热路径纪律。

**降级姿态**（沿用 `funding` 的既有做法）：

| 断面 | `halfSpreadBps` | 成本 `confidence` | 门禁行为 |
|---|---|---|---|
| 总闸关 | `None` | 保持 `ok` | 逐字回退到今天的常数路径 |
| 总闸开、订阅正常 | 实测中位数 | `ok` | 按实测分母判定 |
| 总闸开、盘口陈旧或缺席 | `None` | `degraded` | 按 `ai_trade_edge_gate_degraded_mult` 收紧（`config.py:458`） |

第三行是关键：数据缺席时保持 `degraded` 而不按 0 计，与 `cost_model.py:222-223` 已经写明的「有意不建模不是数据缺失」是同一条纪律的两面。

**写入成本模型的哪一项**：只写 `CostQuery.half_spread_bps` 这一个入参（`cost_model.py:210` 的签名已经开好口）。冲击成本 `ai_trade_impact_bps` 本轮不动，它需要成交量分布，盘口快照回答不了。

**同时要下发一个来源标记**：在既有的 `costSnapshot` 通道上加一个 `halfSpreadSource` 键，取值 `books5` 或 `fallback`。它是 §五「半价差实测占比」唯一可数的分子来源，也是 §五 验收步骤 2 要读的那个字段；没有它，那条指标在界面上和在报告里都算不出来。走既有通道而不新开一个 WS 键，理由照抄 `cost_runtime.py:591-592`：少一个「后端算了但没有渲染落点」的机会，那是本仓库最高频的缺陷形状。这个键计入 §四 `schemas.py` 的净增上限。

**半价差的定义必须写死在模块里**，避免与图表面板的展示口径分叉：`halfSpreadBps = (bestAsk − bestBid) / 2 / mid × 1e4`，其中 `mid = (bestAsk + bestBid) / 2`。`OrderBookSnapshot` 的 `asks` / `bids` 已经是按价排好的档位列表（`okx/types.py:156-166`），取第一档即可。分母用 `mid` 而不是最新成交价，理由是门禁判定的是一个即时可执行的往返，而最新成交价可能来自上一秒的对手方。

> 提案（proposal）· 未实现

```text
# ai_auto/microstructure.py
@dataclass(frozen=True)
class MicrostructureSnapshot:
    half_spread_bps: float | None   # None = 算不出（陈旧 / 无订阅 / 档位缺失）
    top_depth_usd: float | None     # 首档名义深度，仅用于展示与陈旧度判定
    as_of_ts: int

class MicrostructureCache:
    def on_book(self, book: OrderBookSnapshot) -> None: ...   # WS 回调，O(1)
    def snapshot(self, *, now_ts: int, max_age_s: float) -> MicrostructureSnapshot | None: ...
    # 超龄 ⇒ None ⇒ 调用方按 degraded 处理，绝不返回一个陈旧的数
```

---

## 三、方案 B：因子证据接入决策

**新模块**：`ai_auto/factor_snapshot.py`（预估 180 到 220 行）。

**接入的边界（三条，逐条对应仓库既有教训）**：

1. **只读快照**。装配时读已有的因子评估产物与权重模型产物，不在决策轮里跑任何 `evaluate_factor`。理由与 `signal_prior.py:3-5` 相同：决策轮内不引入 pandas 依赖。
2. **只进 payload 与 prompt，不进下单路径**。因子读数是给模型看的**证据**，不是门禁。这条与 `track_record.py:19-21` 的第三条文案约束一致，那里明写这一段是证据、不是门禁，且不得出现指令性措辞。
3. **缺席即整键缺席**。样本不足、快照过期、契约不满足，一律不下发该键，由 prompt 逐字节回退。参照 `track_record.py:12-16` 的四条装配门禁写法。

**字段契约**（camelCase wire，与既有 `priorSnapshot` 同风格）：

| 字段 | 含义 | 缺席规则 |
|---|---|---|
| `factors[].key` | 因子键 | 必填 |
| `factors[].ic` / `rankIc` | 主周期 IC 与 RankIC | 任一算不出该条目整条缺席 |
| `factors[].segments` | 有效段数 | `< MIN_IC_SEGMENTS`（6，`engine/factors/evaluation.py:21`）该条目缺席 |
| `factors[].direction` | 方向（由 `resolve_direction` 定，`engine/factors/evaluation.py:186`） | 必填 |
| `asOfTs` / `lookbackBars` | 快照时点与回看根数 | 必填 |
| `maxAgeS` | 陈旧度门禁 | 超龄整键缺席 |

**为什么不复用信号先验那条路**：先验条目的口径是「某个策略立场在历史上值多少钱」，因子快照的口径是「某个数值型因子与前瞻收益的相关性」。两者的条件事件不同，按 `signal_prior.py:9-11` 的事件同源律，它们不能共用一张表，也不能互相折算。

**top-N 上限**：沿用 `ai_trade_prior_top_n = 5`（`config.py:504`）的量级，本轮新开一个独立配置项，默认取 5。理由见 §六的 prompt 预算。

**快照从哪里来**：因子评估产物已经落库，本模块只做读取与筛选，不触发任何重算。若某个标的与周期组合没有可用快照，整键缺席，**不得**退化到用全标的合计冒充。这条约束的来源是 `track_record.py:17`，那里为同一个诱惑写了一句话：BTC 15m 的胜率与 PEPE 1m 的胜率不是同一个东西。

> 提案（proposal）· 未实现

```text
# ai_auto/factor_snapshot.py
def build_factor_snapshot(rows, *, inst_id, bar, now_ts,
                          min_segments, max_age_s, top_n) -> dict | None:
    # rows: 已落库的因子评估结果（只读）
    # 返回 None 的四种情形：总闸关 / 无该 (inst_id, bar) 组合 / 全部条目段数不足 / 快照超龄
    # 返回 dict 时必带 asOfTs 与 lookbackBars，让 prompt 能如实说出这份证据有多旧

def render_factor_section(snapshot: dict | None) -> str:
    # snapshot 为 None ⇒ 返回 ""，prompt 整段省略（逐字节回退）
    # 数值全部在此格式化，prompts.py 不做任何算术（沿用 track_record.py:19 的约束）
    # 禁止 markdown 粗体：后端文案里的 ** 在四个渲染点会显示成字面星号
```

最后一条约束值得单独强调。`track_record.py:19-20` 记录这个坑在本仓库已经复发三次，`tradability.py:19-22` 为同一件事又写了一遍。新模块的任何面向用户或面向模型的字符串都用「」做强调，不用 markdown 标记。

---

## 四、接入点与硬约束

**允许触碰的文件与净增行数硬上限**。上限的核对方式是 `git diff --numstat`，超出即判该提案不合格。

| 文件 | 当前行数 | 本轮允许净增 | 改什么 |
|---|---|---|---|
| `ai_auto/session.py` | 2213 | ≤ 15 | 换 WS 客户端构造 + 两行运行时调用 |
| `ai_auto/cost_runtime.py` | 629 | ≤ 30 | 装配时把实测半价差传进 `query_for_*` |
| `ai_auto/prompts.py` | 846 | ≤ 25 | 因子证据段的渲染 |
| `services/quant/app/config.py` | 793 | ≤ 12 | 两个新总闸 + 三个参数 |
| `services/quant/app/schemas.py` | 2494 | ≤ 20 | wire 字段声明 |
| `ai_auto/microstructure.py` | 新建 | 160 ~ 200 | 方案 A 全部 |
| `ai_auto/factor_snapshot.py` | 新建 | 180 ~ 220 | 方案 B 全部 |

**重申三个已超红线的文件**：`session.py`（2213 行）、`manager.py`（1446 行）、`backtest.py`（1385 行）全部超出 `CLAUDE.md` 的 800 行硬线。本轮只承诺不再恶化，任何把新逻辑写进这三个文件的方案判为不合格。`schemas.py` 已达 2494 行，是一方最大文件，且 AI-trade 每加一个 wire 键都要改它，因此它的净增上限压得比其它文件更紧。

**新总闸命名**：`ai_trade_microstructure_enabled`、`ai_trade_factor_snapshot_enabled`，两者默认 `False`，独立可关。env 名带 `AI_TRADE_` 前缀，已在桌面端 prod 透传白名单内（`desktop/src/lib/config.js:74-82`），但**必须在 `config.py` 里真的声明**，否则 `Settings` 的 `extra="ignore"` 会把它静默丢弃（`config.py:439-440` 已为同一个坑写过警告）。

---

## 五、验收指标

| 指标 | 定义 | 阈值 | 采样窗口 | 不达标处置 |
|---|---|---|---|---|
| 半价差实测占比 | 使用实测盘口算出半价差的决策轮数 / 总决策轮数 | > 0.90 | 滚动 200 轮 | 关闭方案 A 总闸，回到常数路径并记录订阅失败原因 |
| 成本模型偏差 | 模型 `totalBps` 与实测往返成本中位数之差的绝对值 | < 5 bps | 滚动 100 笔往返（口径按带费子样本，见 `calibration.py:16-17`） | 重估成本模型，暂停 [02 号](./02-calibration-and-activation.md) 的 `k_min` 标定 |
| 因子快照装配成功率 | 装配出非空快照的决策轮数 / 总决策轮数 | > 0.80 | 滚动 200 轮 | 检查陈旧度门禁与段数下限，两者都不放宽，改为延长回看根数 |
| prompt 长度增量 | 新增两段相对基线的字符数增量 | < 8% | 每次发版抽样 20 轮 | 下调 top-N 上限，绝不删既有段落 |
| 决策轮耗时增量 | 装配两个新数据面带来的同步耗时 | < 50 ms（P95；提案估算值，非实测） | 滚动 200 轮 | 把中位数窗口缩短，或改为每 N 轮刷新一次 |

每一项都必须与它的分母同屏显示。这条在 `profitability.py:12-15` 是硬约束，本篇的新读数同样适用。

两条采集纪律：所有指标的分母都取**本会话的决策轮数或往返数**，不跨会话合并（跨会话池化是 [03 号 §四](./03-verification-and-gates.md) 的事，两处口径不可混用）；成本模型偏差这一项的分母只算带费子样本，因为全样本中位会被 89% 的零腿费记录压成 `0.000`（`profitability.py:18-20`）。

**验收的执行步骤**（每步给出预期输出）：

1. 在单标的会话上打开 `AI_TRADE_MICROSTRUCTURE_ENABLED=true` 启动一次会话。预期输出：启动回执里出现 `microstructure` 键，首轮成本断面的 `halfSpreadSource` 取 `books5` 而非 `fallback`（不要拿 `halfSpreadBps` 是否等于 `1.0` 当判据，真实半价差恰好落在 1.0 bps 是合法取值）。
2. 连续观察 200 轮，读 WS 的 `edge_gate` 事件。预期输出：`costSnapshot.halfSpreadSource` 为 `books5` 的轮次占比 > 0.90。
3. 断开网络 30 秒后恢复。预期输出：断开期间 `confidence` 变为 `degraded`，`halfSpreadBps` 为 `null`，恢复后自动回到 `ok`，全程无会话中断。
4. 关闭总闸重启会话。预期输出：`microstructure` 整键缺席，成本读数与打开前的基线逐字相同。

---

## 六、风险与回滚

**风险一：WS 订阅增加会加重连接压力，而市场流上新增帧类型是有前科的。** `api/ws_market.py:63-73` 逐字记录了那次事故：链路状态帧以 `ts=undefined` 进入图表数据、打崩渲染层并导致桌面端白屏（诊断存档在 `docs/diagnoses/desktop-white-screen-market-ws-link-frame`），修法是把前端改成白名单路由。连接侧的韧性旋钮已经齐备（熔断次数、冷却、连接池空闲存活、断线补根上限、降级提示，见 `config.py:66-71`），本方案不新增任何一个。缓解措施有三条：复用 `OKXMarketWSClient` 已有的重连与熔断，不新写连接管理；`trades` 与 `tickers` 的回调传空实现，帧收下即丢、不进任何决策路径；总闸默认关，先在单标的会话上观察一周。**这三条都不减少连接数**。按 §二 的说明，AI 会话会多起一条 public 端点连接、多订三个通道，这是本方案真实的增量负载。观察窗口里必须直接盯它：断线次数、熔断触发次数、`ai_trade_microstructure_enabled` 开关前后的补根量对比。

**风险二：prompt 膨胀会挤压既有段落。** 决策 prompt 里已经有市场证据摘要、信号板、成本意识段、战绩回喂、纪律自检等多段。因子证据段进来之后，若不设上限，最先被挤掉的是模型对既有段落的注意力。硬约束是 top-N 上限 5 条加长度增量 8%，并且**绝不删既有段落来腾空间**。若两者冲突，砍因子段。

**风险三：实测半价差可能让门禁变得更紧。** 常数 `1.0` bps 是一个偏乐观的假设，实测中位数在流动性一般的标的上大概率高于它。分母变大意味着同样的分子更难过门禁，开仓率会进一步下降。这是**预期内的正确方向**，因为此前的宽松来自一个假数字。但它会与 [00 号 §七](./00-overview-and-rediagnosis.md) 的「永不开仓」风险叠加，因此方案 A 上线后的第一个观察窗口必须同时盯闸门生效率，超过 0.60 即按 N-1 复核周期，而不是回头调小 `k_min`。

**自我反驳**：本篇的两个方案都不直接改善盈利。方案 A 让分母变得更准，方案 B 给模型多一层证据，两者都可能让开仓变少、短期读数变差。它们的价值在于让 [02 号](./02-calibration-and-activation.md) 标定出来的阈值可以被证伪。如果最终结论是「这个周期上怎么调都不行」，方案 A 的实测分母正是那个结论的核心证据；没有它，那个结论只是一个基于常数假设的推测。

**回滚路径**：两个总闸各自默认关，关闭后系统逐字节回退到当前行为，KV 与快照里的遗留数据不被读取，也无任何副作用。这个形状与 `config.py:487-488` 描述的信号先验三总闸完全一致，按同样的方式实现即可。

**交叉阅读**：本篇产出的干净口径供 [02 号](./02-calibration-and-activation.md) 做阈值标定，其验证形态见 [03 号](./03-verification-and-gates.md)，展示落点见 [04 号 §三](./04-product-and-robustness.md)，全局排序见 [00 号](./00-overview-and-rediagnosis.md)。
