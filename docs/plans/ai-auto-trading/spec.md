# AI 自动交易（AI Auto-Trading）开发方案

- **Feature key**: `ai-auto-trading`
- **文档版本**: **v2**（iteration 1 · Subtask #1 方案评审与修复节点产出；在 Subtask #0 v1 基础上，经并行代码核验后就地修复全部 P0/P1）。本节点仅评审与修订本 `spec.md`，不写实现代码、不改任何源文件、不 `git commit`。评审记录见下方「## 评审记录」、评审结论见文末「## 评审结论」。
- **作者**: 方案设计节点（solution architect）
- **关联红线（真实资金安全，一等需求）**: 本功能是继「策略驱动实时自动交易」（`docs/plans/live-auto-trading/spec.md` v2 + `watchlist-auto-trade-group/spec.md` v2，均已实现并测试）之后的**又一种自动下单类型**——把**当前 AI Agent 作为自动交易的决策者**。因为它同样**无人值守地用真实资金下单**，故 CLAUDE.md「实盘/自动交易一律显式守门与审计、真实下单绝不绕过既有护栏与审计」是硬约束：本方案**不新增任何绕过 `okx/trading.py` 服务层的下单路径**，一切真单仍经 `LiveExecutor` → `okx/trading.py`（继承 `LIVE_TRADING_ENABLED` 守门、单笔名义上限、净仓反向拦截、唯一 clOrdId、`live_order_audit` 留痕、脱敏），并叠加**新的自动化专用总闸 `AI_TRADE_ENABLED`（默认关闭）**、**与既有 live-auto 的互斥独占**、**预算/名义双上限**、**会话级止盈止损**、**无既有持仓门禁**、**LLM 决策失败/超时一律回落 `hold`（绝不在不确定时下单）**等补偿控制。
- **复用基线（最大化复用三条已存在且经测试的链路）**：
  1. **守护执行**：`live_auto/executor.py::LiveExecutor`（`apply(signals)` 单向单仓状态机 + 张数换算 + 交易所侧 OCO + net_mode 反手清算确认 + 失败不崩 + reconcile 真源校正 + flatten 停止），**零改动**复用。
  2. **AI 能力层**：`agent/` 的 provider 解析（`providers.resolve_active_provider` / `llm.factory.make_llm_client_from_config`）+ 归一化 LLM 客户端（`LLMClient.stream(system, messages, tools)`）+ 工具/`ToolResult` 原语，**零改动**复用；AI 决策与摘要都跑在既有 LLM 抽象上。
  3. **策略信号**：`paper/strategy_factory.build_paper_strategy_spec(row)` + `strategies/base.BaseStrategy.on_candle(df, i, ctx)` + **每策略独立 `engine/simulator.SimBroker` 影子回放**（`paper/session.py` 的 `broker.context()` 范式 + `_prime_window`/`_prefill_htf` 暖机，**注意无 `_prime_history`**），复用来为**每个选中策略**计算**持久立场（stance）**组成信号板（见 §2.2 / P0-1）。

  **核心设计一句话**：新增一个 `ai_auto/` 后端包，把 `LiveAutoSession` 逐根驱动循环里的「单策略信号 → `executor.apply`」替换为「**多策略信号板 → AI Agent 综合研判出一个受约束的交易决策 → 映射为 `Signal` → 同一个 `LiveExecutor.apply` 守护执行**」；并在决策器上实现**按用户设定轮数的滚动摘要式上下文压缩**（每 N 轮把此前执行过程摘要成一段背景、清空历史、以纯净上下文继续）。前端在 AI 助手面板「新对话」按钮右侧加一个「AI 自动交易」开关，点击弹出启动弹窗（多选策略 + 总预算/杠杆/总止盈/总止损 + 上下文刷新轮数），运行态标的与情况显示在左侧交易面板。

---

## 评审记录（Review Notes · Subtask #1 · v2）

> 评审人：资深评审节点（senior reviewer）。评审维度：**可行性 / 完备性 / 一致性 / 规模适中**。下列每条均经**并行代码核验**（逐一核对 `live_auto/`、`agent/`、`strategies/`、`paper/`、`config.py` + `db/`、前端 `apps/web`、桌面 `desktop/` 的**真实签名/行为**）后得出，非纸面推断。P0/P1 已在本 v2 正文**就地修复**；P2 为精度/加固项，已就地校正或列入「评审结论」条件。

### P0（阻断级，必修，已修复）

| 编号 | 维度 | 问题 | 修复位置 |
|---|---|---|---|
| **P0-1** | 可行性 | **多策略「信号板」的立场语义不成立**。§2.2（v1）以「单根 `on_candle` 返回的 `Signal.side`、无信号即 `none`」定义各策略当前立场，但代码核验证伪：**(a)** `on_candle` 是**边沿触发**——仅在事件根（金叉/突破/触止损）返回一条 `Signal`，其余根返回 `[]`，故信号板将**几乎恒为 none**，AI 无从「综合研判」；**(b)** `Signal.side` 的 `Literal` 只允许 `{long,short,close}`（`strategies/base.py:17-25`），**根本不存在中性/`none`/`flat` 的信号值**，立场并非策略会 emit 的东西；**(c)** `close` 只有当 `ctx.position_side` 已 long/short **且**该策略沿**它自己**的成交轨迹递推了内部状态（`self._peak/_trough/_trail_armed`，见 `momentum_breakout.py:43-100`、`trend_pullback_long/short`）时才产生——v1 把**共享 `LiveExecutor` 的真实持仓**喂给每个策略（原「保证多策略看到一致仓位上下文」），策略会在它**从未开过的仓位**上评估平/开，语义错误。综合导致 AI 输入空洞且不自洽，核心需求落空。 | **§2.2 重写**（每策略独立 `SimBroker` 影子回放求持久立场） |

### P1（严重，必修，已修复）

| 编号 | 维度 | 问题 | 修复位置 |
|---|---|---|---|
| **P1-1** | 完备性/可靠性 | **决策轮与 monitor 循环并发改写同一 `executor`**。`_drive_decision`（含最长 `ai_trade_decision_timeout_s=60s` 的 LLM 延迟 + 下单）是长临界区，而 monitor 循环每 `poll_interval_s=5s` 跑 `reconcile` 与 `_maybe_session_exit`（会话止盈止损**平仓**）。二者并发触碰同一 `LiveExecutor.projected`，可致**并发/重复下单或状态损坏**。live-auto 的 `_drive_signals` 是快同步、无 LLM，从无此窗口——AI 首次引入长临界区，v1 未加保护。 | **§2.6** 引入会话级 `asyncio.Lock` |
| **P1-2** | 完备性/可靠性 | **慢 LLM 致决策轮跨 bar 重叠**。若 LLM 慢于出 bar 间隔，新确认 bar 会在上一轮决策**未返回时**再次触发 `_drive_decision`，造成重叠决策/重复下单。§8 仅提「一根内不重复决策」，未防**跨 bar 重叠**，v1 无 in-flight 守卫。 | **§2.6** in-flight 守卫（跳过并记事件） |
| **P1-3** | 完备性/一致性 | **`ai_trade_max_context_rounds=200` 与 `ai_trade_max_context_messages=40` 冲突**。每决策轮向 `_context` 追加约 3 条消息（user + tool_use + tool_result），约 **12–13 轮**即触 40 条硬上限并**左裁中间轮**；当用户设 `contextRefreshRounds > ~12`，v1 的 `_summarize()`（以 `self._context` 为素材）会**对已被裁剪的历史做摘要**→静默丢信息，「每 N 轮纯净上下文」承诺被部分击穿。 | **§2.4** 摘要改以结构化 `_journal` 为唯一真源 |
| **P1-4** | 可行性/**安全** | **互斥独占存在 TOCTOU 竞争**。§2.5⑥ 与反向校验各自「读对方 status 是否 None」再注册自己，但两个并发 start（一 AI、一 live-auto）可**各自读到对方 idle 后同时启动**，账户敞口翻倍——正是互斥要防的。v1 无跨管理器共享锁，check-and-register 非原子。 | **§2.5** 进程级准入锁包裹「检查+注册」，反向校验落 `_require_capacity` 单一choke point |
| **P1-5** | 可行性/一致性 | **LLM 层无工具强制；流/工具契约与实际不符**。核验：`LLMClient.stream(*, system, messages, tools)` **keyword-only**，**无 `tool_choice`**（`anthropic_client` 不下发、`openai_client` 硬编码 `"auto"`），故「本轮必须且只能调用一次 `submit_trade_decision`」**无法在层面强制**，只能靠 prompt（安全由 hold 回落兜底，**非**安全漏洞，但措辞须纠正为「尽力引导 + 回落兜底」）；真实流事件是 `tool_use_start`/`tool_use_args_delta`/`tool_use_end`（**非** v1 的 `tool_use_*`），调用方须**自行拼接 `partial_json` 再 `json.loads`**；决策工具须封装为 `ToolSchema(name, description, input_schema=DECISION_TOOL_SCHEMA)` 对象，裸 dict 传 `tools=[]` 会因属性访问崩溃。 | **§2.3 / §4** 校正流事件与工具契约、改「强制」措辞 |
| **P1-6** | 可行性/完备性 | **桌面打包无法透传 AI 总闸**。`desktop/src/lib/config.js` `backendEnv()` 的 prod 白名单仅透传 `AGENT_` 或 `LIVE_` 前缀（`key.startsWith`），`AI_TRADE_ENABLED`（含任何 `AI_TRADE_` 前缀）**均不透传**；且后端 `Settings` 为 `extra="ignore"`，未声明字段的 env 会被静默丢弃。v1「多半不改」为误判——打包/prod 将**永远无法启用** AI 自动交易。 | **§3 桌面行** 升为**必改**（加 `AI_TRADE_` 前缀透传） |
| **P1-7** | 一致性/可行性 | **左侧面板宿主文件指认错误**。核验：`AutoTradePanel`/`AutoTradeGroupConfirmModal` 实渲染于 **`WatchlistTab.tsx`**（左侧标的栏），**非** `ChartWorkspace.tsx`（后者只消费 `overlayTrades` 作图表 marker）；需求亦明确要「**左侧**交易面板」。v1 §3 让 `ChartWorkspace.tsx ✎ 渲染 <AiTradePanel>` 指错文件与位置。 | **§2.7 / §3** 宿主改为 `WatchlistTab.tsx` |

### P2（精度/加固；已就地校正或列入结论条件）

| 编号 | 维度 | 问题（均已就地校正，除非注明） |
|---|---|---|
| **P2-1** | 一致性 | 复用锚点精度：`_prime_history` **不存在**（实为 `_prime_window` / `_prefill_htf`）；`get_positions` 是 `OKXPrivateClient` 方法，`LiveExecutor` 经 `fetch_live_positions` 读仓；`_redact` 定义于 `okx/accounts.py`（`trading.py` 复用）；registry 围栏参数名为 `enabled`（`enabled_tools` 是会话层名）；`resolve_active_provider` 为 **async** 且判空基于 `api_key` 非 model；`make_llm_client_from_config` 为**同步**；`Signal` 是 pydantic `BaseModel`（非 dataclass）。 |
| **P2-2** | 完备性 | `LiveExecutor.__init__` 为**全 keyword-only 且几乎无默认**，需 `creds/account_id/inst_id/td_mode/pos_mode/leverage/budget_usd/risk/ct_val/lot_sz/min_sz/settings`；AI 会话须**镜像 `live_auto/manager._build_member`** 取标的元数据（ct_val/lot_sz/min_sz）与凭证并显式传 `risk`（AI 用固定默认 `RiskParams`，来自新增 `ai_trade_default_sl_pct/tp_pct`）。已在 §2.3/§2.6 补注。 |
| **P2-3** | 一致性 | `accountMode` 取值为 `"sim"/"live"`（**非** `"paper"`）；`accountMode==="live"` 门禁本身正确，实现时勿写 `"paper"`（纸面模式是另一字段 `mode==="paper"`）。已注。 |
| **P2-4** | 规模适中 | `ai_auto/session.py` 镜像 460+ 行的 `LiveAutoSession`，实现时须按 CLAUDE.md 阈值（单文件 ≤800 行、单函数 ≤50 行）**预先拆分**（会话/监控/扇出 helper 分离，如 `live_auto` 布局）。**列入结论条件**。 |
| **P2-5** | 一致性（**正向确认**） | 运行 `mode` 是自由 `String(16)`、无 enum/CHECK；`strategy_runs.{mode,config_json,metrics_json}` 为原生列——`"ai-trade"` **无需任何迁移**（区别于 `trades`/`weight_train_runs` 需 `db/session.py` ALTER 补丁）。已在 §5.3 明确。 |

---

## 一、Overview（概述）

当前 Aragon Trading 工作台已有两类自动交易，本质都是**确定性地严格执行策略信号**：策略在每根确认 K 线上产出 `Signal`，`LiveExecutor` 按固定规则把它翻译成真单（开/平/反手），人不介入。它的优点是可预测、可回测；局限是**单策略、无综合研判**——多个策略信号冲突时无法权衡，也无法结合市场状态做取舍。

本方案新增**第三种自动交易类型：AI 自动交易**。它把系统里已有的「AI 助手」（对话式 Agent，跑在后端、经既有 LLM 抽象接入模型）**提升为自动交易的决策者**：用户一次性选中**多个策略**，系统在每个「决策轮」（对齐所选周期的每根确认 K 线）为每个策略计算其在最新一根上的**当前立场**（会开多/开空/平仓/无动作、仓位强度、触发原因），汇成一张**信号板**连同当前持仓、会话盈亏、预算、市场快照一起交给 Agent；Agent **综合研判**这些信号，输出**一个受严格约束的交易决策**（开多 / 开空 / 平仓 / 持币观望），系统把该决策映射为一个 `Signal` 交给**与既有 live-auto 完全相同的守护执行器** `LiveExecutor` 落成真单。于是「AI 作为决策者、根据多策略信号实时决策」这一需求，被落在既有的、经审计的真实资金执行链路之上，**不放大任何护栏缺口**。

需求同时对 **AI 上下文**提出明确要求：Agent 长时间运行会累积大量决策历史，撑爆上下文且注意力被稀释。故用户在启动时可设定「**上下文刷新轮数**」`contextRefreshRounds`；决策器每累积到该轮数，就把此前若干轮的**决策序列、持仓与盈亏演变、哪些策略信号有效/无效、关键经验教训** LLM 摘要成一段简洁的**执行摘要**，用它**替换**掉冗长历史（清空历史对话），后续轮以「纯净上下文 + 滚动摘要」继续决策；下一次摘要会把上一段摘要一并纳入，形成**滚动累积**的长期记忆。这既守住 token 预算，又保留了「此前干了什么、效果如何」的连续性。

工程层面：后端新增一个 **`ai_auto/` 包**（`decider` 决策器 + `signals` 信号板 + `session` 会话 + `manager` 单活跃会话注册表 + `prompts`），一组受 `AI_TRADE_ENABLED` 守门的 **`/api/ai-trade/*` REST** 与一个 **`/ws/ai-trade/{session_id}` WebSocket**（事件协议与 `/ws/live-auto/{id}` 同构，另加 `decision`/`summary` 两类新事件）；前端在 `AgentPanel` 头部加「AI 自动交易」开关、一个 `AiTradeLaunchModal` 启动弹窗（多选策略 + 参数 + 刷新轮数）、一个左侧 `AiTradePanel` 状态卡（标的/持仓/会话盈亏/最新 AI 决策与理由/当前上下文摘要/决策日志/停止），并新增 `aiTradeApi`/`aiTradeTypes`/`aiTrade` 三个 lib 与瞬态 store 切片。**AI 决策器复用既有 Agent LLM 层，但不复用交互式 `AgentSession`**（避免与用户聊天抢占同一会话与 BUSY 守卫，并让决策循环对上下文重置有完全控制权）。真实下单**一律经 `LiveExecutor`**，与 live-auto 全程互斥独占（二者不同时下单，避免账户敞口叠加）。

v1 范围收敛为**单标的**（AI 会话同时只交易一个标的，镜像 live-auto 的「单活跃会话」独占，标的默认取当前图表标的、可在弹窗中选择），**多标的 AI 自动交易列为后续增强**（可复用 `LiveAutoManager` 的分组机制叠加），以把真实资金敞口与实现复杂度控制在可靠、可评审的范围内。

---

## 二、Technical design（技术设计）

### 2.1 架构与数据流（端到端）

```text
浏览器/Electron（accountMode==="live" 实盘 + Agent 已配置模型）
  ├─ 右侧 AgentPanel 头部「新对话」右侧：<AiTradeToggle>（Switch「AI 自动交易」）
  │     ├─ 守门：accountMode==="live" && hasReadyDefault && aiTradeEnabled && agentConfigured
  │     │        && 无活跃 live-auto 分组 && 无活跃 AI 会话
  │     ├─ 打开 → <AiTradeLaunchModal>：多选策略(≥1) + 标的(默认当前) + 总预算/杠杆/总止盈/总止损
  │     │                               + 上下文刷新轮数 contextRefreshRounds + 真实资金勾选
  │     └─ 确认 → aiTradeApi.start({ instId, bar, strategyIds[], capitalUsd?, leverage?,
  │                                   takeProfitUsd?, stopLossUsd?, contextRefreshRounds })
  ├─ 经 Next 同源代理 /api/py/* → FastAPI :18760
  │     └─ api/ai_auto.py（受 AI_TRADE_ENABLED ∧ LIVE_TRADING_ENABLED 双闸守门）
  │           └─ ai_auto/manager.AiTradeManager.start(...)（单活跃 AI 会话 + 与 live-auto 互斥）
  │                 ├─ 前置门禁：双闸 / Agent 已配置 / 就绪默认账户 / 标的支持 / 无既有持仓
  │                 │            / 无活跃 live-auto 分组 / 策略可实例化 / 轮数合法 / 预算夹紧
  │                 └─ ai_auto/session.AiTradeSession（镜像 LiveAutoSession 逐根驱动）
  │                       ├─ OKX 公共 WS 实时 K 线 + 暖机 priming（仅热指标，不下单）
  │                       ├─ 每根确认 K 线 = 一个「决策轮」：
  │                       │    1) ai_auto/signals.evaluate_signal_board(specs, df, shadow_brokers)
  │                       │         └ 逐策略 on_candle → 回放进各自影子 SimBroker → 持久 stance + 本根 latestSignal（P0-1）
  │                       │    2) ai_auto/decider.AiTradeDecider.decide(signalBoard, realPosition, pnl, summary)
  │                       │         └ 复用 Agent LLM 层 llm.stream(system=, messages=, tools=[ToolSchema(submit_trade_decision)])（kw-only）
  │                       │         └ 自拼 tool_use_start/args_delta/end 的 partial_json → TradeDecision{action,sizeFraction,confidence,reason}
  │                       │         └ 失败/超时/无有效/非法决策 → hold（安全回落，覆盖"无法强制工具"缺口 P1-5）
  │                       │    3) 映射决策为 Signal → LiveExecutor.apply([signal])（守护真单；经 _exec_lock 串行 P1-1）
  │                       │    4) 记决策日志；每 contextRefreshRounds 轮 → decider.summarize()（以 _journal 为素材 P1-3）重置上下文
  │                       ├─ monitor 循环（poll 间隔）：reconcile 真源校正 + 会话级止盈止损（经 _exec_lock）+ staleness
  │                       └─ 停止：撤挂单 + close 自管持仓 + 落 Run 终态 + 推 status
  └─ 前端 /ws/ai-trade/{id}（事件 snapshot/status/trade/position/equity/candle/signal
        + 新增 decision / summary / error，与 live-auto 同信封 {type,data}）
        ├─ trade → store.aiTrade.trades → 图表 overlayTrades（buildTradeMarkerEvents → markers）
        ├─ decision → AiTradePanel 决策日志 + 最新决策卡（action/理由/置信度/信号板）
        ├─ summary → AiTradePanel 上下文摘要卡（AI 当前长期记忆）
        └─ status/equity/position → 左侧 AI 自动交易状态卡（标的/持仓/会话盈亏/已完成轮数）
```

要点与关键决策：

- **AI 是决策者，`LiveExecutor` 是守护执行者**：Agent **绝不**获得裸下单工具（`place_order`/`close_position`）。决策器**不经交互式 `AgentSession`/`ToolRegistry`**，而是**只把唯一一个 `ToolSchema(submit_trade_decision)` 传给 `llm.stream(tools=[...])`**——模型在该调用里**物理上没有别的工具可调**，这是比 registry 围栏（会话层 `enabled` 硬门 + dispatch 二次围栏，`agent/tools/registry.py:72/109-117`，仅用于交互式路径）**更强**的边界。决策再由 `LiveExecutor.apply` 落单。这样把「LLM 不可控」这一根本风险，**约束在一个有界决策 + 既有全部资金护栏**之内，是本方案安全姿态的基石（对齐 live-auto 评审「逐单确认缺失以补偿控制对冲」的判定）。
- **复用 Agent LLM 层、不复用交互式会话**：决策器直接用 `resolve_active_provider(session_factory, settings)`（`agent/providers.py:121`）解析默认 provider + `make_llm_client_from_config` 造 `LLMClient`，逐轮 `await llm.stream(...)`。**不**经 `AgentManager.handle`/`AgentSession.run`——因为（a）会与用户正在进行的聊天抢占同一 conversation 与 BUSY 守卫；（b）决策循环需要对「清空历史 + 注入摘要」的上下文重置有完全控制权，而交互式会话的持久化/续接语义（`agent_messages` 落库、resume）会掣肘。这仍**充分满足**需求「利用已有 AI 助手功能、让 AI 介入自动交易」——AI 能力（模型接入、工具、prompt 脚手架）被复用，开关也确实落在 AI 助手面板。
- **单标的、单活跃 AI 会话、与 live-auto 互斥**：`AiTradeManager` 容量上限 1；且**启动前置校验 `get_live_auto_manager().group_status() is None`**，并在 live-auto 启动路径（`_require_capacity`）**反向校验 `get_ai_trade_manager().status() is None`**，两侧「检查+注册」经**共享 `AUTO_TRADE_ADMISSION_LOCK` 原子准入**（§2.5/P1-4，杜绝 TOCTOU）——二者共用同一实盘账户预算，绝不同时下单叠加敞口。
- **账户唯一 + 持仓真源在 OKX**：固定用设置中心默认实盘账户；会话内不缓存/伪造持仓，`LiveExecutor` 每根 reconcile 经 `fetch_live_positions`（P2-1，非 `get_positions`）校正 `projected`，前端持仓/成交真源另由既有真实账户视图呈现。

### 2.2 决策轮与多策略信号板（`ai_auto/signals.py`）— **v2 已修复 P0-1**

一个「决策轮」= 所选 `bar` 的一根确认 K 线（对齐信号新鲜度：策略只在新 bar 上产生新信号；不在 bar 内重复决策，避免过度交易）。`AiTradeSession._on_confirmed`（镜像 `live_auto/session.py:309-328`）在每根确认 K 线上调用 `_drive_decision`（替代既有 `_drive_signals`）。

**立场语义（P0-1 纠正，先厘清再设计）**：策略的 `on_candle`（`strategies/base.py:104-107`）是**边沿触发**——它仅在事件根（金叉/突破/触止盈止损）返回一条 `Signal`，其余根返回 `[]`；且 `Signal.side` 的 `Literal` 只有 `{long,short,close}`（`base.py:17-25`），**没有中性/`none`/`flat` 值**。因此「读单根 `on_candle` 返回值当作策略当前立场、无信号即 none」**不成立**：信号板会几乎恒为 none，AI 无从综合研判。更关键的是，`close` 立场只有当 `ctx.position_side` 已 long/short **且**该策略沿**它自己**的成交轨迹递推了内部状态（`self._peak/_trough/_trail_armed`，见 `momentum_breakout.py:43-100`）时才产生——若把**共享 `LiveExecutor` 的真实持仓**喂给每个策略，策略会在它**从未开过的仓位**上评估平/开，语义错误。

**正确机制：每策略独立影子回放（per-strategy shadow replay）**。为**每个选中策略**各维护**一个独立的 `SimBroker`**（`engine/simulator.py`，纸面/回测同款、已测的确定性执行器），让该策略在**自己的**影子仓位上演进；其「立场」即该影子 broker 的 `position_side`——一个**持久、真有意义**的多/空/空仓状态，而非某根的边沿返回值。这正是 `paper/session.py` 已验证的「策略 → SimBroker → `broker.context()`」范式（`session.py:328-330`），只是这里对多个策略各跑一份影子。**影子 broker 只为「读立场」，绝不下任何真单**；真单一律只经 AI 决策 → `LiveExecutor`（§2.3），二者严格隔离。

1. **构建帧（共享取数，逐策略各自 prepare）**：会话维护共享 `_candles`/HTF 窗口（同 `LiveAutoSession`，避免重复取数）；每策略经 `spec.strategy.prepare(candles_to_df(list(self._candles)), bar)`（`prepare/attach_timeframes/risk_params` 均在 `spec.strategy` 上，**非** frozen `PaperStrategySpec` 本身），需 MTF 者再 `spec.strategy.attach_timeframes(...)`。
2. **逐策略影子回放求立场**：`evaluate_signal_board(specs, df_map, i, shadow_brokers) -> list[StrategySignalView]`——`specs = [build_paper_strategy_spec(row) for row in selected]`（`paper/strategy_factory.py:30`，原样复用，含 atomic/combo 校验与参数合并）。对每个策略 `k`：
   - 取该策略**自己的**上下文 `ctx_k = shadow_brokers[k].context(i, ref_px)`（每策略独立 `SimBroker`，`BrokerContext.position_side ∈ {long,short,flat}`）；
   - `signals = spec.strategy.on_candle(df_k, i, ctx_k)`（返回 `list[Signal]`）；把本根信号**回放进该策略自己的 `shadow_brokers[k]`**（开/平影子仓），使 `self._peak` 等内部状态沿一致轨迹递推、`close` 立场可达；
   - 立场视图 `StrategySignalView{ strategyId, name, stance, latestSignal, strength, reason, riskParams }`：`stance = shadow_brokers[k].context(...).position_side`（`long|short|flat`，**持久立场**）；`latestSignal ∈ {open_long,open_short,close,none}`（本根 `on_candle` 边沿，"本轮是否有新动作"）；`riskParams = spec.strategy.risk_params()`（`base.py:95-102`，含 `stop_loss_pct/take_profit_pct/max_hold_bars`，供 Agent 参考）。
3. **AI 真实持仓单独入参（与策略影子解耦）**：AI 决策器所控的**真实**持仓（`LiveExecutor.projected` + 未实现 PnL）作为信号板**之外**的独立一行喂给 prompt。AI 综合「各策略此刻想多/想空/想平（stance + latestSignal + reason）」与「我当前真实持有什么、盈亏多少」自行研判——这才是需求要的「综合研判多策略信号」。
4. **组装决策输入**（`ai_auto/prompts.build_decision_prompt`）：紧凑文本/JSON，含 bar ts、最新价、**当前真实自管持仓**（side/entry/size/未实现 PnL）、会话累计盈亏 vs 预算、会话止盈止损阈值、`signalBoard[]`（逐策略 stance+latestSignal+reason+riskParams）+ 可选市场状态（复用 `label_market_regime` 语义，可选）。

> 复用要点：**没有现成「当前立场」函数**，但其内核全部是既有经测代码——`build_paper_strategy_spec` + `on_candle` + **`SimBroker` 影子回放（`paper/session.py` 同款 `broker.context()` 范式）** + 暖机步进；`evaluate_signal_board` 仅是新增薄封装，不重写回测/取数。暖机阶段（§2.6）同样对**每个**影子 broker 逐根回放种入其内部状态，故起手第一根实时决策时各策略立场已就绪。

### 2.3 AI 决策 → 真实订单映射（`AiTradeDecider` + `LiveExecutor`）

**决策工具（唯一放行工具）** `submit_trade_decision`。下面是它的 **`input_schema` 载荷**（JSON Schema）；喂给 LLM 层时**必须**封装为 `ToolSchema(name="submit_trade_decision", description=..., input_schema=DECISION_TOOL_SCHEMA)` 对象（`agent/llm/base.py` 的 `ToolSchema`，三字段 `name/description/input_schema`）——**裸 dict 传 `stream(tools=[...])` 会因客户端按属性访问 `.name/.description/.input_schema` 而崩溃**（`anthropic_client.py:67`）：

```jsonc
{
  "type": "object",
  "properties": {
    "action":       { "type": "string", "enum": ["open_long", "open_short", "close", "hold"] },
    "sizeFraction": { "type": "number", "minimum": 0, "maximum": 1 },   // 预算名义占比；close/hold 忽略
    "confidence":   { "type": "number", "minimum": 0, "maximum": 1 },
    "reason":       { "type": "string", "maxLength": 500 }              // 决策理由（面板展示 + 摘要素材）
  },
  "required": ["action", "reason"]
}
```

`AiTradeDecider.decide(...)` 逐轮调用 `llm.stream(...)`（**注意：`stream(*, system, messages, tools)` 参数全为 keyword-only**）：`await llm.stream(system=ai_trade_system_prompt, messages=self._context + [round_user_msg], tools=[DECISION_TOOL])`。**流事件契约（P1-5 校正）**：LLM 层**不**产出单一 `tool_use` 事件、也**不**替调用方拼接入参；真实事件序列是 `tool_use_start`（携 `id`/`name`）→ 若干 `tool_use_args_delta`（携 `partial_json` 片段）→ `tool_use_end`（携 `id`）（`agent/llm/base.py`）。故决策器须**自行**在 `tool_use_start` 记下 `name`、跨 `tool_use_args_delta` 拼接 `partial_json`、于 `tool_use_end` 后 `json.loads` 得到 `submit_trade_decision` 入参 → `TradeDecision`（可参照 `agent/loop.py` 的 `_ToolAcc` 累积器思路，但本决策器独立实现、不复用交互式会话）。**关于「强制调用一次」（P1-5 关键澄清）**：LLM 层**无 `tool_choice`/工具强制**（Anthropic 请求体不下发 `tool_choice`、OpenAI 硬编码 `"auto"`），故「本轮必须且只能调用一次 `submit_trade_decision`」**无法在 API 层面强制**——只能由系统 prompt **强力引导**，模型仍可能不调用或多调用。这**不构成安全漏洞**：下面的**安全回落**把「未产生工具调用 / 产生多次（取第一条有效者）/ 入参非法」一律归为 `hold`，即「引导 + 回落兜底」共同保证每轮都有一个受约束的确定结果。

**安全回落（安全关键，非可选）**：以下任一 → 决策一律取 `hold`（不下单）并推 `status` 降级事件：LLM 连接/超时失败（`ai_trade_decision_timeout_s`，默认 60s）；未产生工具调用（只回文本）；工具入参 JSON 非法/枚举越界；`action ∈ {open_long, open_short}` 但 `sizeFraction` 缺失或 ≤0。`sizeFraction` 越界一律夹紧 `[0,1]`。可选：`confidence < ai_trade_min_confidence`（默认 0，不启用）时降级为 `hold`。

**决策 → `Signal` 映射**（喂给**既有** `LiveExecutor.apply(signals, ref_px=, bar_index=, ts=)`，`live_auto/executor.py:115`）：

| `TradeDecision.action` | 映射 `Signal` | `LiveExecutor` 行为（既有语义，零改动） |
|---|---|---|
| `open_long`  | `Signal(side="long",  strength=sizeFraction, reason, explain=aiMeta)` | flat→开多；同向→跳过；反向→先平后开（net_mode 带清算确认） |
| `open_short` | `Signal(side="short", strength=sizeFraction, reason, explain=aiMeta)` | 同上（对称） |
| `close`      | `Signal(side="close", reason)` | 有仓→平仓；flat→跳过 |
| `hold`       | `[]`（不产信号） | 无动作 |

`explain=aiMeta` 携 `{ source:"ai", confidence, signalBoardDigest }`，经既有 `_inject_explain_meta`/`append_live_trade` 透传到 trade（面板可展示「AI 决策」归因），不改既有 explain 通道契约。张数换算、单笔名义上限、交易所侧 OCO、net_mode 反手清算确认、失败不崩、`LIVE_NO_POSITION` 良性、reconcile 全部由 `LiveExecutor` 既有实现兜住。

**每仓交易所侧 OCO（兜底止损止盈）**：AI 会话构造 `LiveExecutor` 时传入**默认 `RiskParams`**（来自 config：`ai_trade_default_sl_pct` / `ai_trade_default_tp_pct`，如 0.05 / 0.10；置 0 即不挂 OCO），作为「bot 死/断线后交易所端仍生效」的安全网；`max_hold_bars` 默认 `ai_trade_default_max_hold_bars=0`（禁用强制时间平仓，正常退出交由 AI 的 `close` 决策 + 会话级止盈止损）。这些默认可被 config 覆盖。（AI 在决策里自定义每单 SL/TP 列为后续增强。）

### 2.4 上下文管理：轮次滚动摘要式压缩（本方案的新增核心）

现状：交互式 Agent 的上下文裁剪仅是**固定近 N 条滑窗**（`loop.py::_trimmed_messages`，`agent_max_context_messages=40`，`config.py:156`），**无摘要、无 token 预算、无压缩**。本方案在**独立的决策器**里实现需求要求的滚动摘要机制（不改动 `agent/loop.py`，避免动交互式路径）：

`AiTradeDecider` 内部状态：`_context: list[LlmMessage]`（回喂模型的滚动上下文）、`_rounds_since_summary: int`、`_summary: str`（当前长期记忆）、`_journal: list[dict]`（每轮 {round, signalBoard, decision, executed, positionAfter, sessionPnl}，供摘要与审计）。

每决策轮（`decide` 之后）：
1. 把本轮 `round_user_msg`（决策输入）+ assistant 的 `tool_use`（决策）+ 一条精简合成 `tool_result`（执行结果摘要）**追加**进 `_context`，`_rounds_since_summary += 1`，并把结构化条目 push 进 `_journal`。
2. **触发摘要**：当 `_rounds_since_summary >= contextRefreshRounds`：
   - `summary = await self._summarize()`（**P1-3：以结构化 `_journal` 为唯一素材真源，不以可能被裁剪的 `_context` 为素材**）：把自上次摘要以来 `_journal` 的每轮条目（{round, signalBoard, decision, executed, positionAfter, sessionPnl}）**紧凑序列化**为一条 user 文本，连同上一段 `_summary`（滚动纳入）喂 `await llm.stream(system=summary_system, messages=[LlmMessage("user",[TextBlock(journal_digest)])], tools=[])` 收集纯文本。`summary_instruction`（并入 `summary_system` 或该 user 文本）＝「总结迄今的自动交易执行情况：决策序列与结果、持仓与盈亏演变、哪些策略信号被验证有效/无效、关键经验教训与当前策略偏好，形成**简洁**的执行摘要，作为后续决策的背景，不要罗列每一轮细节。」**为何改用 `_journal`**：每轮向 `_context` 追加约 3 条消息，约 12–13 轮即触 `ai_trade_max_context_messages=40` 硬上限并左裁中间轮；若仍以 `_context` 摘要，则 `contextRefreshRounds > ~12` 时摘要素材已被静默截断。改以 `_journal` 后，摘要**无损**覆盖「自上次摘要以来的每一轮」，与模型可见窗口 `_context` 的裁剪相互独立——`_context` 的 40 条上限仅为「喂给决策模型的滚动窗口」的护栏，摘要真源始终是完整 `_journal`。摘要完成后 `_journal` 截断至空（其信息已被 `_summary` 承载）。
   - **重置上下文**（清空历史、保纯净）：`self._context = [ LlmMessage("user", [TextBlock("【历史执行摘要（截至第 {round} 轮）】\n" + summary)]), LlmMessage("assistant", [TextBlock("已了解此前执行情况，将据此继续决策。")]) ]`；`self._summary = summary`；`self._rounds_since_summary = 0`；`_journal` 截断（仅留最近若干条或清空，摘要已承载）。
   - 推 `summary` WS 事件 → 前端上下文摘要卡实时更新。
   - **滚动累积**：因上一段 `summary` 仍在 `_context` 里，下一次 `_summarize()` 会把它与新几轮一并纳入 → 长期记忆随时间**演进而不无限膨胀**。
3. **硬上限兜底**：两次摘要之间若 `_context` 超 `ai_trade_max_context_messages`（默认 40），按「保留起始摘要种子 + 丢弃最旧的中间轮、并使首条为 user 轮」裁剪（复用 `_trimmed_messages` 的左修剪思路），防极端参数下越界。

`contextRefreshRounds` 由用户在弹窗设定，服务端校验 `∈ [ai_trade_min_context_rounds(默认3), ai_trade_max_context_rounds(默认200)]`，默认 `ai_trade_default_context_rounds=10`。摘要 max_tokens 复用 `agent_max_tokens`。摘要文本随会话 WS 下发、并在停止时写入 Run 指标（审计），**不新增表**。

### 2.5 启动前置门禁与互斥（安全关键）

`AiTradeManager.start(req)` 在建会话前**顺序校验**，任一不过即拒绝并回结构化错误（绝不假启动）：

1. `AI_TRADE_ENABLED ∧ LIVE_TRADING_ENABLED` 均为 `true`，否则 `403 AI_TRADE_DISABLED`（自动化默认关闭闸为新增 `ai_trade_enabled=False`；写链路总闸 `live_trading_enabled` 现默认 True，故 AI 路径**必须显式同时校验两闸**，不得依赖服务层内已强制）。
2. **Agent 已配置模型**：`resolve_active_provider(...) is not None`，否则 `400 AGENT_NOT_CONFIGURED`（无模型无法决策）。
3. **就绪默认账户**（含 secret/passphrase），否则 `400 ACCOUNT_NOT_CONFIGURED`。
4. **标的为 USDT 本位 SWAP/FUTURES**（`fetch_trading_config` 的 `instrumentSupported`），否则 `400 LIVE_INSTRUMENT_UNSUPPORTED`。
5. **该标的当前无任何既有持仓**（`fetch_live_positions` 该 instId `pos==0`），否则 `409 AI_TRADE_PREEXISTING_POSITION`（守红线「不动现有持仓」）。
6. **互斥独占（安全关键，P1-4：原子准入，杜绝 TOCTOU）**：AI 与 live-auto 共用同一实盘账户预算，绝不同时下单。校验：无活跃 AI 会话（本管理器 cap=1）**且** `get_live_auto_manager().group_status() is None`，否则 `409 AI_TRADE_SESSION_LIMIT`；**反向**：live-auto 启动时校验 `get_ai_trade_manager().status() is None`，否则回 `409 LIVE_AUTO_SESSION_LIMIT`（既有码）。**但「读对方 status → 注册自己」若非原子，两个并发 start（一 AI 一 live-auto）会各自读到对方 idle 后双双启动，敞口翻倍**。故引入一把**进程级共享准入锁** `AUTO_TRADE_ADMISSION_LOCK`（模块级 `asyncio.Lock`，AI 与 live-auto 两侧共用），两侧的 start 都在 `async with AUTO_TRADE_ADMISSION_LOCK:` 内完成「检查对方空闲 + 注册自己」这段临界区，使准入串行化、check-and-register 原子化。反向校验统一落在 `LiveAutoManager._require_capacity`（单标的 `start` 与 `start_group` 的**单一 choke point**，`manager.py:453-457`）内，而非散落到 `api/live_auto.py` 各启动路径，避免漏改其一。
7. **策略集合合法**：`1 ≤ len(strategyIds) ≤ ai_trade_max_strategies(默认8)`，逐个 `get_strategy_row` 存在且 `build_paper_strategy_spec` 可实例化，否则 `400 AI_TRADE_STRATEGIES_INVALID`。
8. **轮数合法**：`contextRefreshRounds ∈ [min,max]`，否则 `400 AI_TRADE_CONTEXT_ROUNDS_INVALID`。
9. **预算硬上限夹紧**：读 `availUsdt`（复用 `fetch_trading_config` 回执）+ 读**账户级** `maxBudgetPct`（`repo.get_auto_trade_settings`）；`cap_frac = min(maxBudgetPct/100, ai_trade_max_capital_frac(默认0.05))`；最终 `budgetUsd = min(capitalUsd or availUsdt×ai_trade_default_capital_frac(1%), availUsdt×cap_frac)`，回执如实告知夹紧后值（复用 live-auto 的 `_resolve_budget_group` 同款语义）。

### 2.6 暖机、reconcile、停止与清理（镜像 live-auto，复用其可靠性设施）

- **executor 构造（P2-2）**：`LiveExecutor.__init__` 为**全 keyword-only 且几乎无默认**，须 `creds/account_id/inst_id/td_mode/pos_mode/leverage/budget_usd/risk/ct_val/lot_sz/min_sz/settings`。AI 会话**镜像 `live_auto/manager._build_member`** 解析凭证、经 `fetch_trading_config` 拿标的元数据（`ct_val/lot_sz/min_sz`、`posMode/tdMode`）并显式传入；`risk` 传**固定默认 `RiskParams`**（`stop_loss_pct=ai_trade_default_sl_pct`、`take_profit_pct=ai_trade_default_tp_pct`、`max_hold_bars=ai_trade_default_max_hold_bars`）——这与 live-auto「`risk` 取自 `strategy.risk_params()`」的差异是**刻意**的：AI 会话的兜底 OCO 与单个影子策略解耦，由会话统一设定。
- **priming（仅热指标，不下单，P2-1 校正方法名）**：复用 `LiveAutoSession._prime_window` / `_prefill_htf`（**注意：不存在 `_prime_history`**）的思路，为**每个策略的影子 `SimBroker`**（§2.2）逐根回放种入指标与内部状态；priming 阶段忽略一切**真实**决策、真实 `LiveExecutor.projected` 恒 flat（门禁 5 已保证起始无持仓）。真实决策/下单从第一根实时确认 K 线起。
- **monitor 循环**（`poll_interval_s`，复用 `live_auto_poll_interval_s=5`）：`reconcile` 真源校正 `projected` + 未实现 PnL；`_push_status_if_side_changed`；`_emit_equity`/`_emit_position`；staleness 看门狗；`_maybe_session_exit`（会话级止盈止损：累计盈亏 ≥ `+takeProfitUsd` 或 ≤ `−stopLossUsd` → 自动平仓 + 停会话）。这些**复用** `LiveAutoSession` 已加固语义，但**所有会触碰 `executor` 的写操作（`_maybe_session_exit` 的平仓、stop 的 flatten）须经下述会话锁**。
- **并发安全（P1-1，本方案相对 live-auto 的新增关键点）**：AI 决策轮 `_drive_decision` 含最长 `ai_trade_decision_timeout_s=60s` 的 LLM 调用 + 下单，是**长临界区**；而 monitor 循环每 5s 可能触发 `_maybe_session_exit` 平仓——二者并发改写同一 `LiveExecutor.projected` 会导致并发/重复下单或状态损坏（live-auto 的 `_drive_signals` 是快同步、无此窗口）。故会话持一把 `self._exec_lock = asyncio.Lock()`：**一切 `executor` 写路径**（决策的 `executor.apply`、会话止盈止损平仓、stop 的 `flatten`）都在 `async with self._exec_lock:` 内串行执行；`reconcile` 的**只读**校正可在锁外，但其对 `projected` 的写回也纳入锁。
- **决策轮不重叠（P1-2）**：会话持 `self._deciding: bool`（或复用 `_exec_lock.locked()`）。当一根新确认 K 线到来而上一轮 `_drive_decision` **仍在进行**（慢 LLM），本轮**跳过**（不排队、不并发），并推一条 `decision{action:"hold", executed:{skipped:true, reason:"decision_in_flight"}}` 事件入决策日志。启动时**额外提示**：当 `ai_trade_decision_timeout_s ≥ 所选 bar 的秒数`（建议 `bar ≥ 15m` 留足决策时间），以避免频繁跳过。
- **停止与清理**：用户关开关 / 调 stop → `AiTradeSession.stop(reason)`：撤该 instId 挂单 + `executor.flatten`（有界重试 + 诚实 `residual`）平自管持仓；取消 WS/monitor/决策任务；`repo.finalize_run(status="stopped", metrics={ totalReturn, tradeCount, roundsCompleted, summaryCount, finalSummary, ... })` 落终态；推 `status{state:"stopped", stopReason}`。会话级止盈止损触发同样自动执行。进程退出经 `main.py` lifespan `await get_ai_trade_manager().shutdown()`（紧随 live-auto 关停）。
- **前端重载孤儿**：`aiTradeSessionId` 等运行期态**瞬态不持久化**；前端挂载 `GET /ai-trade/status` 重发现并重连 WS（镜像 live-auto 单会话 `page.tsx` 重发现）。

### 2.7 前端交互设计（美观优雅、符合 HCI 最佳实践）

- **开关插入点**：`components/Agent/AgentPanel.tsx:295-297`——把独占右侧的 `新对话 <Button>` 包进 `flex items-center gap-1.5` 行，在其右侧追加 `<AiTradeToggle>`（`ui/Switch`，label「AI 自动交易」）。开关**直接订阅全局 store**（`accountMode`/`agentConversationId` 等）+ react-query 取 `accounts`（`hasReadyDefault`）与 `/ai-trade/status`（`aiTradeEnabled` + 是否已有活跃会话），守门文案就地悬浮显示（缺实盘/缺账户/未启用/模型未配置/live-auto 占用各有文案）。运行态开关恒可点（用于停止）。
- **启动弹窗** `AiTradeLaunchModal`（克隆 `Chart/AutoTradeGroupConfirmModal` 的骨架与视觉，基于 `ui/Modal`）：
  - **多选策略**：一个可勾选策略清单（`useQuery(["strategies"])` + checkbox 列表，支持全部/组合/因子分组，复用 `strategyCatalog` 风格），至少选 1、至多 `ai_trade_max_strategies`；
  - **标的**：默认当前图表 `instId`，可改（仅 USDT SWAP/FUTURES，前端 `isAutoTradable` 二次校验）；周期默认当前 `bar`；
  - **四个金额/杠杆字段**（沿用既有弹窗 verbatim 文案）：`总预算（USDT）留空＝默认`、`杠杆（×）留空＝默认`、`总止盈（USDT）留空＝不触发`、`总止损（USDT）留空＝不触发`；
  - **上下文刷新轮数**：number 输入或步进（`ui/Slider`/`Field`），提示「每 N 轮把执行过程摘要为上下文、清空历史保持纯净」，默认 10、范围 [min,max]；
  - **只读摘要预览**：交易账户 / 可用余额 `availUsdt` / 默认预算(availUsdt×1%) / 生效杠杆 / 单笔名义上限 `≤1000 USDT/单`（复用 `deriveDefaultBudget`/`clampBudgetToCap` 预览）；
  - **真实资金风险勾选** + 主 CTA「确认并开始 AI 自动交易」（`ready = 选了≥1策略 && acknowledged && !inputError && !hasUnsupported`）。
- **左侧状态面板** `Chart/AiTradePanel.tsx`（镜像 `Chart/AutoTradePanel` 头部：运行中脉冲点 / 会话盈亏 / 停止按钮）：展示标的、喂给 AI 的策略清单、当前持仓方向（多/空/空仓）、会话盈亏 vs 预算、**最新 AI 决策卡**（action 中文化「开多/开空/平仓/观望」+ 置信度 + 理由 + 本轮信号板：各策略 `stance`（多/空/空仓）与 `latestSignal`（本轮是否有新动作））、**上下文摘要卡**（AI 当前长期记忆，随 `summary` 事件更新）、**决策日志**（近 N 轮，errors/降级/`skipped` 置顶）、已完成轮数 / 距下次摘要还剩几轮。**宿主（P1-7 校正）**：`AutoTradePanel`/`AutoTradeGroupConfirmModal` 实际渲染在 **`Chart/WatchlistTab.tsx`**（左侧标的栏，`WatchlistTab.tsx:247/289`），**不是** `ChartWorkspace.tsx`（后者只把 `overlayTrades` 作为 prop 消费、画图表 marker）。故 `<AiTradePanel>` 亦渲染于 **`WatchlistTab.tsx`**（活跃 AI 会话时，与 `AutoTradePanel` 并列的左侧区域），恰好满足需求「标的信息与情况显示在**左侧**交易面板」。
- **图表 markers**：`app/page.tsx` 计算 `overlayTrades` 时，活跃 AI 会话优先取 `aiTrade.trades`（live 模式原无策略 overlay，纯增量）。
- **WS 订阅**：新增 `useAiTradeStream`（镜像 `useLiveAutoGroupStream`/单会话 effect）：`accountMode==="live"` 时 `GET /ai-trade/status` 重发现 → 一条 `aiTradeWS(sessionId, onEvent)` → 事件路由进 `aiTrade` 瞬态切片。
- 全程沿用既有设计 token（`--accent`/`--surface`/`--border`/`--long`/`--short`）、暗/亮主题、`ui` 原语（`Switch`/`Modal`/`Button`/`Badge tone="short"`/`Select`/`Field`/`Tabs`），与工作台一致、隆重但不喧宾夺主。

---

## 三、File / module change plan（文件 / 模块变更计划）

> 约定：✚=新建，✎=修改。所有改动遵循 CLAUDE.md 阈值（单文件 ≤800 行、单函数 ≤50 行、≤5 形参、圈复杂度 ≤10、嵌套 ≤4、命名自解释、公开 API 写 docstring）。真实下单**一律经 `LiveExecutor`→`okx/trading.py`**，不新增绕过护栏的下单路径。行号为当前工作树引用，Subtask #1 须复核。

### 后端 `services/quant/app/`

| 文件 | 操作 | 一句话意图 |
|---|---|---|
| `ai_auto/__init__.py` | ✚ | 包导出（`AiTradeManager`/`get_ai_trade_manager`/`AiTradeSessionLimitError`/`AiTradePreflightError`）。 |
| `ai_auto/signals.py` | ✚ | `evaluate_signal_board(specs, df_map, i, shadow_brokers) -> list[StrategySignalView]`：**每策略独立 `SimBroker` 影子回放**（`on_candle` → 回放进该策略自己的影子 broker → 读 `broker.context().position_side` 得持久 `stance` + 本根边沿 `latestSignal`；P0-1），复用 `build_paper_strategy_spec` + `engine/simulator.SimBroker` + `paper/session` 同款范式；纯函数便于单测。 |
| `ai_auto/decider.py` | ✚ | `AiTradeDecider`：持 `LLMClient` + 滚动 `_context`/`_journal`/`_summary`/`_rounds_since_summary`；`decide(...)→TradeDecision`（强制 `submit_trade_decision`，失败/超时/非法回落 `hold`）；`summarize()`（§2.4 摘要+重置）；决策工具 schema 常量。 |
| `ai_auto/prompts.py` | ✚ | `build_ai_trade_system_prompt(...)`（AI 决策者角色/纪律/风险偏好/只用真数据/必须调用决策工具）、`build_decision_prompt(board,position,pnl,summary)`、`build_summary_prompt()`、`DECISION_TOOL_SCHEMA`。 |
| `ai_auto/session.py` | ✚ | `AiTradeSession`：镜像 `LiveAutoSession` 逐根驱动（OKX 公共 WS + 每策略影子 broker priming（`_prime_window`/`_prefill_htf`）+ monitor 循环 + reconcile + 会话级止盈止损 + flatten/stop + WS 扇出），`_drive_decision` 用「信号板→decider→映射 Signal→`executor.apply`」替代 `_drive_signals`；持 **`_exec_lock`（P1-1，串行化一切 executor 写）+ `_deciding` in-flight 守卫（P1-2，跳过重叠轮）**；维护决策轮计数、触发 `decider.summarize()`、推 `decision`/`summary` 事件。**须按 CLAUDE.md 阈值预拆分（P2-4：会话/监控/扇出 helper 分文件，≤800 行/≤50 行/函数）**。 |
| `ai_auto/manager.py` | ✚ | `AiTradeManager`：单活跃 AI 会话注册表（cap=1）+ §2.5 前置门禁（含**与 live-auto 互斥，经共享 `AUTO_TRADE_ADMISSION_LOCK` 原子准入**，P1-4）+ lifecycle（start/stop/status/get/shutdown）+ `session_id=f"aitrade_{uuid.hex}"`；`get_ai_trade_manager()` 单例。共享准入锁建议置于 `ai_auto/__init__.py` 或独立 `ai_auto/admission.py`，供 `api/live_auto.py`/`LiveAutoManager._require_capacity` 复用。 |
| `api/ai_auto.py` | ✚ | 路由 `POST /ai-trade/start`、`POST /ai-trade/{id}/stop`（不守门，恒可停）、`GET /ai-trade/status`（只读）、`WS /ws/ai-trade/{id}`（`{type,data}` 同信封 + `decision`/`summary`）；写类经 `_require_ai_enabled` 双闸守门；错误体 `{error:{code,message}}`。 |
| `live_auto/manager.py` | ✎ | 在**单一 choke point** `_require_capacity`（`manager.py:453-457`，`start` 与 `start_group` 均经此）内新增**反向互斥校验** `get_ai_trade_manager().status() is None`（否则 `LiveAutoSessionLimitError`→`409 LIVE_AUTO_SESSION_LIMIT`），并让 `start/start_group` 的「检查+注册」临界区包在共享 `AUTO_TRADE_ADMISSION_LOCK` 内（P1-4，与 §2.5⑥ 原子对称）。仅加校验/加锁，不动既有下单/护栏。为避免循环 import，对 `get_ai_trade_manager` 采用函数内延迟导入。 |
| `config.py` | ✎ | 新增 `ai_trade_enabled: bool=False`（自动化专用总闸，AI 下单**默认关闭闸**）、`ai_trade_default_capital_frac=0.01`、`ai_trade_max_capital_frac=0.05`、`ai_trade_default_sl_pct=0.05`、`ai_trade_default_tp_pct=0.10`、`ai_trade_default_max_hold_bars=0`、`ai_trade_max_strategies=8`、`ai_trade_default_context_rounds=10`、`ai_trade_min_context_rounds=3`、`ai_trade_max_context_rounds=200`、`ai_trade_max_context_messages=40`、`ai_trade_decision_timeout_s=60.0`、`ai_trade_min_confidence=0.0`。复用 `live_max_order_notional_usd`/`live_default_leverage`/`live_auto_poll_interval_s` 等。**不改** `live_trading_enabled` 默认。注：`ai_trade_max_context_rounds(200)` 与 `ai_trade_max_context_messages(40)` **相互独立**（摘要真源是 `_journal` 而非模型窗口 `_context`，P1-3）。**必须真的声明这些字段**：`Settings` 为 `extra="ignore"`，未声明的 `AI_TRADE_*` env 会被**静默丢弃**（P1-6 关联）。 |
| `schemas.py` | ✎ | 新增 `AiTradeStartIn`/`AiTradeStartOut`/`AiTradeSessionRow`/`AiTradeStatusOut`/`AiTradeDecisionOut`/`StrategySignalViewOut`（camelCase wire，见 §五）。 |
| `main.py` | ✎ | `from .api import ... ai_auto`；`app.include_router(ai_auto.router, prefix="/api")`；lifespan 关停增 `await get_ai_trade_manager().shutdown()`（紧随 `get_live_auto_manager().shutdown()`）。 |
| `db/repositories.py` | （复用） | 复用 `create_run(mode="ai-trade", config={strategyIds,contextRefreshRounds,budgetUsd,leverage,tp,sl})` + `finalize_run` + `append_live_trade` + `get_strategy_row` + `get_auto_trade_settings`；**无新表**（决策日志/摘要进 Run config/metrics JSON + 会话 WS 流）。 |

### 前端 `apps/web/`

| 文件 | 操作 | 一句话意图 |
|---|---|---|
| `lib/aiTradeTypes.ts` | ✚ | camelCase 类型：`AiTradeStartReq`/`AiTradeStartResult`/`AiTradeSessionRow`/`AiTradeStatusResponse`/`AiTradeStatus`/`AiTradeDecision`/`StrategySignalView`/`AiTradeStopReason`/`AiTradeSessionState`。 |
| `lib/aiTradeApi.ts` | ✚ | `/api/py/ai-trade/*` REST（`start`/`stop`/`status`，复用 `request`）+ `aiTradeWS(id,onEvent,onState)`（复用 `ReconnectingWS`，`/api/ws/ai-trade/{id}`）。 |
| `lib/aiTrade.ts` | ✚ | 纯件：`resolveAiTradeGate(input)→string|null`（守门文案矩阵）、`validateStrategySelection`、`validateContextRounds`、预算派生/夹紧（复用 `deriveDefaultBudget`/`clampBudgetToCap`）、`describeAiAction`（open_long→「开多」…）、`describeStopReason`。 |
| `lib/aiTrade.test.ts` | ✚ | 上述纯件单测（守门分支、策略数/轮数边界、留空默认派生、action/stop 文案）。 |
| `lib/useAiTradeStream.ts` | ✚ | 挂载于 `page.tsx`：`accountMode==="live"` 时 `GET /ai-trade/status` 重发现 + 一条 `aiTradeWS` 订阅 + 事件按 type 路由进 store（含 `decision`/`summary`）。 |
| `components/Agent/AiTradeToggle.tsx` | ✚ | AI 助手头部「AI 自动交易」`Switch` + 守门禁用态与悬浮提示；开→弹 `AiTradeLaunchModal`，关→确认后停会话。 |
| `components/Agent/AiTradeLaunchModal.tsx` | ✚ | 启动弹窗：多选策略清单 + 标的/周期 + 总预算/杠杆/总止盈/总止损 + 上下文刷新轮数 + 只读预览 + 真实资金勾选 + CTA。 |
| `components/Agent/AgentPanel.tsx` | ✎ | `:295-297` 把 `新对话 <Button>` 包进 flex 行，右侧插入 `<AiTradeToggle>`；仅此处结构微调，不动 WS/消息逻辑。 |
| `components/Chart/AiTradePanel.tsx` | ✚ | 左侧 AI 自动交易状态卡：运行态/标的/策略清单/持仓/会话盈亏/最新决策卡/上下文摘要卡/决策日志/停止（镜像 `AutoTradePanel` 视觉）。 |
| `components/Chart/WatchlistTab.tsx` | ✎ | **（P1-7 宿主校正：原误指 `ChartWorkspace.tsx`）** 活跃 AI 会话时在左侧标的栏渲染 `<AiTradePanel>`（与既有 `AutoTradePanel`(`:247`)/`AutoTradeGroupConfirmModal`(`:289`) 并列）。`ChartWorkspace.tsx` 仅**消费** `overlayTrades` prop 画图表 marker，不承载 AI 面板，无需为本面板改动（marker 数据由 `page.tsx` 的 `overlayTrades` 计算处注入）。 |
| `app/page.tsx` | ✎ | 挂 `useAiTradeStream()`；`overlayTrades` 计算处：活跃 AI 会话 → 取 `aiTrade.trades`（纯增量）。 |
| `lib/store.ts` | ✎ | 新增**瞬态**（不入 `partialize`）：`aiTradeSessionId`/`aiTradeSession: AiTradeSessionState|null`/`aiTradeAcknowledged`/`aiTradeDecisions: AiTradeDecision[]`(环形)/`aiTradeSummary: string|null` 及 setter；可选**持久化**偏好 `aiTradeConfig{strategyIds,contextRefreshRounds}`（进 `partialize`）；`version` 24→25（**已核验当前为 24**）+ migrate 补默认。守门用 `accountMode==="live"`（**注 P2-3：`AccountMode` 取值 `"sim"/"live"`，非 `"paper"`；纸面模式是另一字段 `Mode`**）。 |

### 测试

| 文件 | 操作 | 一句话意图 |
|---|---|---|
| `services/quant/tests/test_ai_trade_signals.py` | ✚ | `evaluate_signal_board` 逐策略 **stance**（影子 `SimBroker` 回放，含状态化策略经暖机后 `close` 可达、多策略影子互不污染、`latestSignal` 仅边沿非 none，P0-1），桩策略/合成行情。 |
| `services/quant/tests/test_ai_trade_decider.py` | ✚ | 桩 `LLMClient`（`tool_use_start/args_delta/end` 流）：自拼 `partial_json` 解析→映射 Signal；LLM 超时/无工具调用/多次调用/非法入参/缺 sizeFraction → 一律 `hold`（P1-5）；`summarize()` 每 N 轮触发、**以 `_journal` 为素材（P1-3）**、`_context` 被摘要种子重置、滚动累积、`_journal` 清空；`sizeFraction` 夹紧。 |
| `services/quant/tests/test_ai_trade_api.py` | ✚ | `ASGITransport` + 桩：`AI_TRADE_ENABLED=false`→403；Agent 未配置→400；既有持仓→409；活跃 live-auto→409 互斥（及反向）；轮数/策略越界→400；start→status→stop 生命周期；停止平仓被调用。 |
| `services/quant/tests/test_ai_trade_session.py` | ✚ | replay/桩行情：priming 不下单；首实时根触发一轮决策→`executor.apply` 被调用一次且入参正确；reconcile 校正 projected；会话级止损触发自动停；每 N 轮推 `summary` 事件；**`_exec_lock` 串行化 + `_deciding` 跳过重叠轮（P1-1/P1-2）**。 |
| `services/quant/tests/test_auto_trade_admission.py` | ✚ | **P1-4**：并发 AI+live-auto `start` 经 `AUTO_TRADE_ADMISSION_LOCK` 串行、至多一方成功、另一方 409（TOCTOU）。 |
| `services/quant/tests/test_live_auto_api.py` | ✎ | 补：活跃 AI 会话时 live-auto 启动→409（`_require_capacity` 反向互斥校验，P1-4）。 |
| `apps/web/lib/aiTrade.test.ts` | ✚ | 见上。 |
| `apps/web/components/Agent/AiTradeToggle.test.tsx` | ✚ | 守门禁用态；开→弹窗；确认→`aiTradeApi.start` 入参正确（`NODE_ENV=test`）。 |
| `apps/web/components/Agent/AiTradeLaunchModal.test.tsx` | ✚ | 多选策略校验、轮数/预算输入校验、未勾选风险不可确认、`hasUnsupported` 禁用。 |

### 桌面 `desktop/`

| 文件 | 操作 | 一句话意图 |
|---|---|---|
| `src/lib/config.js` | ✎（**必改，P1-6**） | `backendEnv()` 的 prod 白名单当前**仅**透传 `AGENT_` 或 `LIVE_` 前缀（`key.startsWith`，`config.js:67-73`），`AI_TRADE_ENABLED`（含任何 `AI_TRADE_` 前缀）**不会**透传，打包/prod 将无法启用 AI 自动交易。**必须**在该条件新增一条 `key.startsWith('AI_TRADE_')` 的 OR 分支。因总闸**默认 OFF**（安全姿态），**不**为其加 prod 默认注入行（对比 `LIVE_AUTO_TRADING_ENABLED ??= 'true'`）；用户在 prod 需显式提供 env 方可开启。dev 下后端直接读 `services/quant/.env`，不受此白名单影响。 |

---

## 四、Interface design（接口设计）

所有 `/ai-trade/*` **写类**接口在 `AI_TRADE_ENABLED=false`（或底层 `LIVE_TRADING_ENABLED=false`）时返回 `403 {error:{code:"AI_TRADE_DISABLED", message:"AI 自动交易未启用"}}`；`GET /ai-trade/status`、`POST /ai-trade/{id}/stop` 为只读/恒可停，不受该闸限制。错误体统一 `{error:{code,message}}`，message 先经 `_redact` 脱敏。

### 4.1 `POST /api/ai-trade/start`
```jsonc
// AiTradeStartIn（金额/杠杆字段可选，留空→保守默认）
{
  "instId": "BTC-USDT-SWAP",
  "bar": "1H",
  "strategyIds": [12, 7, 23],       // 多选，1..ai_trade_max_strategies
  "accountId": null,                // 省略=默认实盘账户
  "capitalUsd": null,               // 预算(USDT)，留空→ availUsdt × ai_trade_default_capital_frac(1%)
  "leverage": null,                 // 留空→ live_default_leverage(3)
  "takeProfitUsd": null,            // 会话级止盈金额阈值；留空→不触发
  "stopLossUsd": null,              // 会话级止损金额阈值；留空→不触发
  "contextRefreshRounds": 10,       // 上下文刷新轮数，∈[min,max]
  "priming": true,
  "primingBars": 300
}
// → AiTradeStartOut
{ "sessionId": "aitrade_7f3a…", "runId": 512, "instId": "BTC-USDT-SWAP", "bar": "1H",
  "strategyIds": [12,7,23], "budgetUsd": 12.34, "leverage": 3, "contextRefreshRounds": 10 }
```
启动前执行 §2.5 全部前置门禁；任一不过回对应错误（`AI_TRADE_DISABLED`=403、`AGENT_NOT_CONFIGURED`/`ACCOUNT_NOT_CONFIGURED`/`LIVE_INSTRUMENT_UNSUPPORTED`/`AI_TRADE_STRATEGIES_INVALID`/`AI_TRADE_CONTEXT_ROUNDS_INVALID`=400、`AI_TRADE_PREEXISTING_POSITION`/`AI_TRADE_SESSION_LIMIT`=409）。`budgetUsd` 为夹紧后最终生效预算。

### 4.2 `POST /api/ai-trade/{session_id}/stop`
```jsonc
{ "sessionId": "aitrade_7f3a…", "stopReason": "user_stop",
  "run": { "id": 512, "metrics": { "roundsCompleted": 42, "summaryCount": 4, "totalReturn": 0.031, "tradeCount": 6 } },
  "flattened": { "ok": true, "closedSz": 1, "clOrdId": "aragonc…" } }
```

### 4.3 `GET /api/ai-trade/status`
```jsonc
{ "session": {
    "sessionId": "aitrade_7f3a…", "runId": 512, "state": "running",
    "instId": "BTC-USDT-SWAP", "bar": "1H",
    "strategyIds": [12,7,23], "strategyNames": ["EMA 交叉剥头皮","暴涨动量突破","布林带均值回归"],
    "budgetUsd": 12.34, "leverage": 3, "takeProfitUsd": null, "stopLossUsd": null,
    "contextRefreshRounds": 10, "roundsCompleted": 42, "roundsSinceSummary": 2,
    "positionSide": "long", "sessionPnlUsd": 0.42, "startedTs": 1751000000,
    "lastDecision": { "ts": 1751003600, "round": 42, "action": "hold", "confidence": 0.4, "reason": "信号分歧，观望" },
    "contextSummary": "前 40 轮：偏多头；EMA 金叉在趋势段有效…", "degraded": false },
  "aiTradeEnabled": true }
```

### 4.4 `WS /api/ws/ai-trade/{session_id}`（事件协议与 `/ws/live-auto/{id}` 同信封 `{type,data}`）
连接即原子「subscribe→status→snapshot」（防丢事件，对齐 live-auto）。事件 `type`：
- `snapshot`：`{ trades, equity, position, status, decisions:AiTradeDecision[], contextSummary:string|null }`
- `status`：`{ state, positionSide, sessionPnlUsd, budgetUsd, leverage, roundsCompleted, roundsSinceSummary, contextRefreshRounds, degraded, stopReason? }`
- `decision`（**新增**）：`{ ts, round, action:"open_long"|"open_short"|"close"|"hold", sizeFraction, confidence, reason, signalBoard:[{strategyId,name,stance,latestSignal,strength,reason}], executed:{ ok, skipped?, reason?, side?, qty? } }`（`executed.skipped=true,reason:"decision_in_flight"` 表示本轮因上一轮未完成而跳过，P1-2）
- `summary`（**新增**）：`{ ts, round, summary }`（每 contextRefreshRounds 轮，上下文已重置）
- `trade`：单笔已执行真单 `Trade{ts,side,price,qty,fee,pnl,reason,stopLossPrice,takeProfitPrice,explain,...}`（驱动图表 markers）
- `position`/`equity`/`candle`/`signal`/`error`：与 live-auto 同构（`error` 为脱敏单笔失败提示或 LLM 降级，不崩会话）。

### 4.5 后端 → OKX / LLM（全部经既有服务层，无新增上游端点）
下单/平仓/设杠杆一律经 `LiveExecutor` → `okx/trading.py`（`place_live_order`/`close_live_position`/`set_account_leverage`，含 `attachAlgoOrds`、`autoCxl=true`、签名/唯一 clOrdId/审计/脱敏）；查持仓经 `okx/trading.fetch_live_positions`（P2-1：`get_positions` 是 `OKXPrivateClient` 方法，`LiveExecutor.reconcile` 默认经 `fetch_live_positions` 读仓）——**零新增签名代码**。AI 决策/摘要经既有 `LLMClient.stream`（**keyword-only 参数**）→ 已配置 provider（KIMI/Anthropic/OpenAI 标准），**零新增模型接入代码**。

---

## 五、Data model（数据模型）

### 5.1 后端 Pydantic（`schemas.py`，camelCase wire）
```python
class AiTradeStartIn(BaseModel):
    instId: str = Field(min_length=1, max_length=32)
    bar: str = "1H"
    strategyIds: list[int] = Field(min_length=1)         # 上限在服务端按 ai_trade_max_strategies 校验
    accountId: int | None = None
    capitalUsd: float | None = Field(default=None, gt=0)
    leverage: float | None = Field(default=None, gt=0, le=125)
    takeProfitUsd: float | None = Field(default=None, gt=0)
    stopLossUsd: float | None = Field(default=None, gt=0)
    contextRefreshRounds: int = Field(default=10, ge=1)  # 精确区间在服务端按 min/max 校验
    priming: bool = True
    primingBars: int = Field(default=300, ge=10, le=PAPER_WINDOW_MAX)

class AiTradeStartOut(BaseModel):
    sessionId: str; runId: int; instId: str; bar: str
    strategyIds: list[int]; budgetUsd: float; leverage: float; contextRefreshRounds: int

class StrategySignalViewOut(BaseModel):
    strategyId: int; name: str
    stance: str                                      # long|short|flat（影子 broker 持久立场，P0-1）
    latestSignal: str                                # open_long|open_short|close|none（本根边沿）
    strength: float; reason: str

class AiTradeDecisionOut(BaseModel):
    ts: int; round: int; action: str                 # open_long|open_short|close|hold
    sizeFraction: float | None; confidence: float | None; reason: str
    signalBoard: list[StrategySignalViewOut]
    executed: dict | None                            # {ok, skipped?, side?, qty?}

class AiTradeSessionRow(BaseModel):
    sessionId: str; runId: int; state: str
    instId: str; bar: str
    strategyIds: list[int]; strategyNames: list[str]
    budgetUsd: float; leverage: float
    takeProfitUsd: float | None; stopLossUsd: float | None
    contextRefreshRounds: int; roundsCompleted: int; roundsSinceSummary: int
    positionSide: str; sessionPnlUsd: float; startedTs: int
    lastDecision: AiTradeDecisionOut | None; contextSummary: str | None
    degraded: bool | None

class AiTradeStatusOut(BaseModel):
    session: AiTradeSessionRow | None
    aiTradeEnabled: bool
```

### 5.2 会话内存形态（`ai_auto/*`）
```text
TradeDecision(dataclass): action, size_fraction, confidence, reason
StrategySignalView(dataclass): strategy_id, name, stance, latest_signal, strength, reason, risk_params  # P0-1: stance=影子持久立场, latest_signal=本根边沿
AiTradeDecider:
  llm: LLMClient                       # 复用 make_llm_client_from_config
  system_prompt: str                   # build_ai_trade_system_prompt
  _context: list[LlmMessage]           # 滚动上下文（回喂模型）
  _summary: str                        # 当前长期记忆
  _journal: list[dict]                 # 每轮结构化条目（摘要/审计）
  _rounds_since_summary: int
  context_refresh_rounds: int
  settings: Settings                   # 超时/max_tokens/上限
AiTradeSession:
  session_id, run_id, inst_id, bar, strategy_ids, strategy_names
  specs: list[PaperStrategySpec]       # build_paper_strategy_spec 逐策略
  shadow_brokers: list[SimBroker]      # P0-1: 每策略一个影子 broker，读持久立场（绝不下真单）
  executor: LiveExecutor               # 复用（固定默认 RiskParams + budget/leverage + 标的元数据，见 §2.6/P2-2）
  decider: AiTradeDecider
  take_profit_usd|None, stop_loss_usd|None, state, started_ts
  rounds_completed: int
  _exec_lock: asyncio.Lock             # P1-1: 串行化一切 executor 写（决策/会话止盈止损/flatten）
  _deciding: bool                      # P1-2: in-flight 守卫，跨 bar 重叠则跳过
  _candles: deque; _equity: deque; _subscribers: set[asyncio.Queue]; _ws; _monitor_task
```

### 5.3 持久化（无新表）
- **每笔真单**：经既有 `create_live_order_audit` 写 `live_order_audit`；成交经 `append_live_trade` 落 `trades.explain_json`（含 `explain.meta.source="ai"`、confidence、signalBoardDigest）。
- **会话级**：复用 `runs` 表——`create_run(mode="ai-trade", config={ instId, bar, strategyIds, budgetUsd, leverage, takeProfitUsd, stopLossUsd, contextRefreshRounds })`；停止时 `finalize_run(status="stopped", metrics={ roundsCompleted, summaryCount, finalSummary, totalReturn, tradeCount, avgPnl })` 落终态，进「运行历史」可复现。
- **前端**：运行期句柄 `aiTradeSessionId` 等**瞬态不持久化**（与 `paperSessionId`/`liveAutoSessionId` 一致）；可选偏好 `aiTradeConfig{strategyIds,contextRefreshRounds}` 进 `partialize`。

### 5.4 store 增量（`lib/store.ts`）
```ts
// 瞬态（不入 partialize，刷新即复位，GET /ai-trade/status 重发现）
aiTradeSessionId: string | null;
aiTradeSession: AiTradeSessionState | null;          // {sessionId,instId,bar,strategyNames,status,position,...}
aiTradeDecisions: AiTradeDecision[];                 // 环形≤50，决策日志
aiTradeSummary: string | null;                       // 当前上下文摘要
aiTradeTrades: Trade[];                              // → overlayTrades（图表 markers）
aiTradeAcknowledged: boolean;                        // 首启风险勾选会话记忆
// 可选持久化偏好（进 partialize；version 24→25 + migrate 补默认）
aiTradeConfig: { strategyIds: number[]; contextRefreshRounds: number } | null;
```

---

## 六、错误码归一（新增本地码，复用既有 `_map_trade_code` / 错误体）

| 码 | HTTP | 语义 / 中文提示 |
|---|---|---|
| `AI_TRADE_DISABLED` | 403 | AI 自动交易未启用（设 `AI_TRADE_ENABLED=true` 且 `LIVE_TRADING_ENABLED=true`） |
| `AGENT_NOT_CONFIGURED` | 400 | 未配置 AI 模型，无法决策（请在设置中心配置默认模型） |
| `ACCOUNT_NOT_CONFIGURED` | 400 | 默认实盘账户缺凭证（复用既有码） |
| `LIVE_INSTRUMENT_UNSUPPORTED` | 400 | 仅支持 USDT 本位 SWAP/FUTURES（复用既有码） |
| `AI_TRADE_PREEXISTING_POSITION` | 409 | 该标的已有持仓，AI 自动交易拒绝启动（守「不动现有持仓」） |
| `AI_TRADE_SESSION_LIMIT` | 409 | 已有活跃 AI 会话或活跃策略自动交易分组（单活跃独占 + 与 live-auto 互斥，请先停止） |
| `AI_TRADE_STRATEGIES_INVALID` | 400 | 策略选择非法（数量越界或存在不可实例化策略） |
| `AI_TRADE_CONTEXT_ROUNDS_INVALID` | 400 | 上下文刷新轮数越界（`[ai_trade_min_context_rounds, ai_trade_max_context_rounds]`） |
| `LIVE_AUTO_SESSION_LIMIT`（反向） | 409 | live-auto 启动时检测到活跃 AI 会话（互斥，复用既有码） |
| （下单期）`INSUFFICIENT_BALANCE`/`LEVERAGE_EXCEEDS`/`LIVE_WOULD_REDUCE_POSITION`/`LIVE_NO_POSITION`/… | — | 全部复用 `okx/trading._map_trade_code`，以 `error` 事件推 WS，不崩会话（`LIVE_NO_POSITION` 平仓路径良性） |

---

## 七、Testing & acceptance criteria（测试与验收）

### 7.1 后端单测（离线 pytest，CI 必跑；铁律：一律占位假凭证，严禁写真实凭证；OKX/LLM 全桩）
- `test_ai_trade_signals.py`（**P0-1 核心测**）：`evaluate_signal_board` 逐策略 **stance** 正确——桩合成行情下，边沿触发 `open_long`/`open_short`/`close` 边沿信号回放进各自影子 `SimBroker` 后，`stance` 反映**持久**多/空/空仓（非某根边沿）；状态化策略（`momentum_breakout`）经暖机逐根回放后 `close` 立场**可达**；**多策略影子互不污染**（策略 A 的影子持仓不影响策略 B 的 stance）；`latestSignal` 仅在边沿根非 `none`。
- `test_ai_trade_decider.py`：桩 `LLMClient` 产出 `tool_use_start/args_delta/end` 事件序列→决策器**自拼 `partial_json`** 解析 `submit_trade_decision`→映射 Signal 正确；**LLM 超时 / 只回文本无工具调用 / 产生多次工具调用（取首条有效）/ 入参 JSON 非法 / `open_*` 缺 `sizeFraction` → 一律 `hold`（安全回落）且推降级事件**（P1-5）；`sizeFraction` 越界夹紧 `[0,1]`；**`summarize()` 到第 N 轮触发、以 `_journal` 为素材（非 `_context`）、`_context` 被摘要种子重置且首条为 user 轮、`_summary` 滚动纳入上一段、`_journal` 摘要后清空**（P1-3）；`contextRefreshRounds>12` 时摘要仍无损（断言素材来自 `_journal` 全量）。
- `test_ai_trade_api.py`：`AI_TRADE_ENABLED=false`（或 `LIVE_TRADING_ENABLED=false`）→403（先于一切）；Agent 未配置→400；标的已有持仓→409；**活跃 live-auto 分组时启动→409 互斥，且反向（活跃 AI 时 live-auto 启动→409）**；策略数/轮数越界→400；start→status→stop 生命周期；stop 调用 `flatten` + `finalize_run`。
- `test_ai_trade_session.py`：replay/桩行情——priming 不下任何真单（每策略影子 broker 也不触真单）；首实时确认根触发一轮决策（断言 `executor.apply` 调用一次且映射 Signal 入参正确）；reconcile 校正 projected；会话级 `stopLossUsd` 触发自动平仓+停；每 `contextRefreshRounds` 轮推一次 `summary` 事件；**并发安全（P1-1/P1-2）：桩慢 LLM，令 `_maybe_session_exit` 平仓与决策轮并发→断言经 `_exec_lock` 串行、无重复/交错下单；上一轮未完成时新 bar 到来→断言本轮 `skipped:"decision_in_flight"` 而非并发**。
- `test_auto_trade_admission.py`（**P1-4 新增**）：桩两个并发 `start`（AI + live-auto）→断言经 `AUTO_TRADE_ADMISSION_LOCK` 串行、**至多一方成功**、另一方 409（覆盖 TOCTOU 竞争）。

### 7.2 前端单测（vitest，`NODE_ENV=test`，CI 必跑）
- `aiTrade.test.ts`：`resolveAiTradeGate` 文案矩阵（缺实盘/缺账户/未启用/模型未配置/live-auto 占用/未选策略）；策略数/轮数/预算校验；留空→保守默认派生；`describeAiAction`/`describeStopReason` 文案。
- `AiTradeToggle.test.tsx`：守门禁用态；开→弹窗；确认→`aiTradeApi.start` 入参正确；活跃时开关用于停止。
- `AiTradeLaunchModal.test.tsx`：多选策略校验、轮数/预算输入校验、未勾选风险不可确认、含不支持标的禁用。

### 7.3 真实账户联调（opt-in，手动/网络标记，不进 CI）
按 README「真实数据硬约束 + 代理」前置，使用任务下发的开发测试账号。**联调铁律**：
1. 资金/标的限制：**最多用开发账号资金的 1%**；在 **BTC**（`BTC-USDT-SWAP`）以 **0.01 手** 量级测试（弹窗预算 ≤1% 可用余额、`live_max_order_notional_usd` 维持 1000、`leverage` ≤3）。
2. 启动前确认 BTC 标的**当前无持仓**（前置门禁 5 会再校验）。
3. 选 2~3 个策略、设小 `contextRefreshRounds`（如 3）→ 确认；观察：Agent 每根出决策卡（action+理由+信号板）、首笔 AI 决策若为开仓则真单成交（`live_order_audit` 落 `accepted`）、图表出现 entry marker、随单 OCO 在 OKX 生效；到第 3 轮出 `summary` 事件、上下文摘要卡更新。
4. 触发 AI `close` 决策或会话止损或点「停止」→ 自管持仓被市价平、marker 出现 close、持仓归零。
5. 关 `AI_TRADE_ENABLED` 复跑 start→断言 403；启动一个 live-auto 分组后再启 AI→断言 409 互斥。
6. 全程**绝不对已有持仓标的自动交易**；联调结束确认无残留持仓/挂单。

### 7.4 Definition of Done（本设计交付物）
- 本 `spec.md` 已建并见于 `git status`；含 Overview / Technical design / File change plan / Interface design / Data model / Testing & acceptance / Risks 全部小节，篇幅 ≥800 字。
- 文件/模块变更计划逐项可被下游工程师按行实现，无需再决策。

---

## 八、Risks & mitigations（风险与缓解）

| 风险 | 影响 | 缓解 |
|---|---|---|
| **LLM 决策不可控致误下单（AI 特有最高优先级）** | 资损 | ① **AI 无裸下单工具**——决策器**只把唯一一个 `ToolSchema(submit_trade_decision)` 传给 `llm.stream`**，模型物理上无别的工具可调（比 registry 围栏更强）；② **失败/超时/无工具/多工具/非法/低质决策一律回落 `hold`**（覆盖「LLM 层无法强制工具」缺口 P1-5），绝不在不确定时下单；③ 真单仍经 `LiveExecutor`→`okx/trading.py` 继承单笔名义上限(1000)/预算硬上限(availUsdt×5%)/net 反向拦截/审计；④ 会话级止盈止损金额阈值自动停；⑤ **自动化默认关闭闸** `AI_TRADE_ENABLED=false` + 写链路总闸 `LIVE_TRADING_ENABLED`；⑥ 启动确认弹窗 + 真实资金勾选；⑦ 联调 0.01 手 BTC、≤1% 资金。 |
| **AI 与 live-auto 同账户敞口叠加** | 资损放大 | **互斥独占 + 原子准入（P1-4）**：`AiTradeManager` 前置校验无活跃 live-auto 分组；`LiveAutoManager._require_capacity` 反向校验无活跃 AI 会话；两侧「检查+注册」经**共享 `AUTO_TRADE_ADMISSION_LOCK`** 串行，杜绝并发 start 的 TOCTOU 双双通过；任一活跃即另一启动报 409。 |
| **触碰用户既有持仓**（红线「不动现有持仓」） | 资损/越权 | 前置门禁 5：启动即校验该标的无既有持仓（`AI_TRADE_PREEXISTING_POSITION`）；bot 只管自开仓；反向单仍被 `LIVE_WOULD_REDUCE_POSITION` 拦。 |
| **逐单确认被自动化绕过**（偏离 `live-trading-mode` C1-3） | 安全设计争议 | **刻意且有界的偏离**（对齐 live-auto 评审判定）：单次启动确认弹窗 + 真实资金勾选为知情同意闸，叠加 AI 无裸下单工具 + hold 安全回落 + 预算/名义双上限 + 会话止盈止损 + 无既有持仓门禁 + 单活跃独占 + 与 live-auto 互斥 + 自动化默认关闭闸，构成充分补偿控制。 |
| **上下文摘要丢失关键信息 / 决策漂移** | 决策质量下降 | 摘要 prompt 明确保留「决策序列、盈亏演变、有效/无效信号、经验教训」；滚动累积（上一段摘要纳入下一次）；**摘要以完整 `_journal` 为素材（P1-3），与模型窗口 `_context` 的 40 条裁剪解耦，避免大 `contextRefreshRounds` 下静默丢信息**；摘要随 WS 下发可人工审阅；摘要失败不崩会话（保留上一段摘要继续）。 |
| **多策略信号板立场语义错误**（边沿触发/无中性 side/喂错持仓；P0-1） | 决策输入空洞或错误 | **每策略独立影子 `SimBroker` 回放**求持久 `stance`（`paper/session` 同款 `broker.context()` 范式）+ 边沿 `latestSignal`；各策略只看**自己**的影子持仓、互不污染；真实持仓单列一行；影子与真实 `LiveExecutor` 严格隔离（影子绝不下真单）。 |
| **bot 内部状态与真实账户漂移**（OCO 已平/外部干预/强平） | 误判方向、重复下单 | 复用 `LiveExecutor.reconcile` 每根真源校正 projected；「projected 非 flat 但真实已平」→ 归 flat 并推 status。 |
| **LLM 延迟高于出 bar 间隔 / 决策慢 / 决策轮重叠** | 错过决策轮 / 并发下单 | 决策超时 `ai_trade_decision_timeout_s` 内未返回→本轮 `hold`；决策与 monitor 循环解耦（reconcile/风控独立跑），但**一切 executor 写经 `_exec_lock` 串行（P1-1）**；**上一轮未完成时新 bar 到来→`_deciding` 守卫跳过本轮（`skipped:"decision_in_flight"`，不并发，P1-2）**；建议 `bar ≥ 15m` 留足决策时间。 |
| **前端重载致 WS 孤儿** | UI 与真实脱节 | `aiTradeSessionId` 瞬态不持久化；挂载 `GET /ai-trade/status` 重发现并重连 WS；后端会话独立于 socket 存活（镜像 live-auto）。 |
| **LLM token 成本随时长累积** | 成本 | 滚动摘要把上下文压到常数量级；`ai_trade_max_context_messages` 硬上限；`agent_max_tokens` 限单次输出；决策轮对齐 bar（非高频）。 |
| **进程退出残留自动会话/持仓** | 无人管理真实仓 | `main.py` lifespan `get_ai_trade_manager().shutdown()`（含平仓）；停止默认平仓退出 + `autoCxl` 撤 OCO。 |
| **密钥泄露** | 安全 | 全程经既有服务层与既有 LLM 抽象；接口绝不回 secret/passphrase/apiKey；message 单点 `_redact`；审计表不存密钥。 |

---

## 九、实施顺序建议（供 Subtask #2 参考）

1. 后端纯/编排件先行：`ai_auto/signals.py`（`evaluate_signal_board` + **每策略影子 `SimBroker` 回放**，P0-1）+ `ai_auto/decider.py`（`tool_use_*` 流自拼解析 + hold 回落 + **`_journal` 摘要重置**，P1-3/P1-5）+ `ai_auto/prompts.py` + `ai_auto/admission.py`（共享 `AUTO_TRADE_ADMISSION_LOCK`，P1-4）+ `test_ai_trade_signals.py`/`test_ai_trade_decider.py`（桩 LLM）。
2. `config.py` ai_trade_*（**须声明字段**，P1-6）+ `schemas.py` 契约；复用 `create_run`/`finalize_run`/`append_live_trade`/`get_auto_trade_settings`。
3. `ai_auto/session.py`（镜像 LiveAutoSession 流水线 + `_drive_decision` + **`_exec_lock`/`_deciding`**，P1-1/P1-2；**按阈值预拆分**，P2-4）+ `ai_auto/manager.py`（前置门禁 + cap=1 + 原子准入互斥）+ `api/ai_auto.py` + `live_auto/manager._require_capacity` 反向互斥 + `main.py` 接线 + `test_ai_trade_api.py`/`test_ai_trade_session.py`/`test_auto_trade_admission.py`。
4. 前端纯件：`lib/aiTradeTypes.ts`/`aiTradeApi.ts`/`aiTrade.ts` + `aiTrade.test.ts`。
5. UI：`AiTradeToggle`/`AiTradeLaunchModal`（AgentPanel `:295-297` 接线）+ `AiTradePanel`（**`WatchlistTab.tsx` 接线**，P1-7）+ `useAiTradeStream`（page.tsx）+ store 瞬态/偏好/migrate v25 + 组件测试。
6. 桌面 `config.js` 白名单**必改**：prod 透传条件加 `|| key.startsWith('AI_TRADE_')`（P1-6）。
7. 真实账户 opt-in 联调（§7.3，0.01 手 BTC、≤1% 资金、小刷新轮数、`bar≥15m`），通过后收尾。

---

## 实施过程发现的方案缺陷（Issues Found During Implementation · Subtask #2）

> 均为**实现级签名/精度勘误**（非架构性缺陷），按放行条件就地采用正确写法，架构与安全姿态不变。

1. **`SimBroker.context()` 无参**（§2.2 步骤2 写作 `shadow_brokers[k].context(i, ref_px)`）：核验 `engine/simulator.py:132` 的真实签名是 `context() -> BrokerContext`（无 `i`/`ref_px` 形参；`bars_in_position` 由内部 `_cur_bar - entry_bar` 计算，`equity` 用内部持仓价）。影子回放**按真实签名**调 `context()`，`stance` 直接读 `shadow.pos.side`（或 `context().position_side`）。
2. **影子回放采用「逐根增量」范式（对齐 `paper/session._prime_history`）而非「整窗预备」**：为使暖机段与实时段的**窗口位置索引 `i=len(candles)-1` 完全一致**（避免 deque 滑窗导致 `entry_bar` 索引漂移），信号板的 `SimBroker` 四阶段驱动严格镜像 `paper/session._process_confirmed` 的 `set_risk→fill_pending→apply_risk→mark→(warmup 后) context→on_candle→submit` 顺序；`stance` 在 `submit` 前经 `context().position_side` 读取（持久立场），`latestSignal` 取本根 `on_candle` 边沿。暖机对每个影子 broker 逐根回放同一序列（每策略独立），与 live-auto 每会话独立暖机的既有性能量级一致。
3. **决策轮以「后台任务 + `_deciding` 守卫」实现跨 bar 不重叠（P1-2）**：`feed_candle` 在短临界区（`self._lock`）内完成去重/入窗/建帧后，若 `self._deciding` 为真则推 `decision{skipped:"decision_in_flight"}` 并直接返回；否则置 `_deciding=True` 并 `asyncio.create_task(_run_decision_round(...))`。决策任务内 LLM 调用（长）不持 `self._lock`（否则阻塞 monitor 循环 60s），仅在 `executor.apply` 时经 `self._exec_lock` 串行（P1-1）；monitor 循环的 reconcile/会话止盈止损平仓/stop 的 flatten 亦经**同一** `_exec_lock`。
4. **共享准入锁落点**：`AUTO_TRADE_ADMISSION_LOCK` 定义于 `ai_auto/admission.py`（仅 `import asyncio`，零重依赖）；`ai_auto/__init__.py` 用 PEP 562 `__getattr__` 惰性导出 manager 符号，避免 `live_auto/manager.py` 顶层 `from ..ai_auto.admission import ...` 触发的循环 import。`live_auto/manager.start_group` 的检查+注册临界区在**既有 `self._start_lock` 外再包一层共享锁**（顺序：共享→实例，AI 侧仅取共享锁，无死锁），反向 AI 互斥校验落 `_require_capacity` 单一 choke point（内部惰性 `from ..ai_auto import get_ai_trade_manager`）。

---

> 版本：**v2**（iteration 1 · Subtask #1 方案评审与修复；在 Subtask #0 v1 基础上经并行代码核验就地修复全部 P0/P1） · 复用基线：`live_auto/`（executor/session/manager，经测）、`agent/`（LLM 层/工具/provider，经测）、`paper/strategy_factory` + `strategies/base.on_candle` + **`engine/simulator.SimBroker` 影子回放**（策略立场）。核心创新：把 AI Agent 约束为「多策略信号板 → 单一有界决策」的决策者，经既有 `LiveExecutor` 守护执行，并实现轮次滚动摘要式上下文压缩。评审记录见文首「## 评审记录」、评审结论见下「## 评审结论」。

---

## 评审结论（Review Verdict · Subtask #1）

**结论：有条件通过（Approved with conditions）。**

v1 的整体架构方向**正确且经代码核验可行**——把 AI 约束为「多策略信号板 → 单一有界 `submit_trade_decision` 决策 → 既有 `LiveExecutor` 守护执行」，复用 `live_auto`/`agent` LLM 层/`paper` 策略工厂三条经测链路，安全姿态（无裸下单工具、hold 回落、双闸、与 live-auto 互斥、无既有持仓门禁、预算/名义双上限）设计扎实；运行 `mode="ai-trade"` 无需任何 DB 迁移、复用 `runs`/`live_order_audit` 亦已核验成立。

评审发现 **1 个 P0 + 7 个 P1 + 5 个 P2**，**全部 P0/P1 已在本 v2 正文就地修复**（见各节 `Px-y` 标注），核心修复为：**(P0-1)** 用「每策略独立 `SimBroker` 影子回放求持久立场」取代不成立的「单根 `on_candle` 边沿即立场」；**(P1-1/P1-2)** 会话级 `_exec_lock` 串行化 + `_deciding` in-flight 守卫；**(P1-3)** 摘要改以完整 `_journal` 为素材、与 40 条模型窗口解耦；**(P1-4)** 共享 `AUTO_TRADE_ADMISSION_LOCK` 原子准入杜绝互斥 TOCTOU；**(P1-5)** 校正流事件/工具契约、明确「引导 + 回落兜底」而非 API 强制；**(P1-6)** 桌面白名单必改；**(P1-7)** 左侧面板宿主改 `WatchlistTab.tsx`。

**放行条件（Subtask #2 实现时必须落实，否则视为未完成）：**

1. **【P0-1 必须按 v2 §2.2 实现】** 信号板必须走「每策略独立影子 `SimBroker` 回放 → 读 `position_side` 得持久 `stance` + 边沿 `latestSignal`」；**严禁**回退到「把共享 `LiveExecutor` 持仓喂给各策略 `on_candle` 读单根返回值」的 v1 写法。`test_ai_trade_signals.py` 须证 `close` 立场可达且多策略影子互不污染。
2. **【P1-1/P1-2 并发安全】** 一切 `executor` 写路径（决策 `apply`、会话止盈止损平仓、`flatten`）必须经**同一** `_exec_lock`；决策轮重叠必须被 `_deciding` 跳过（非并发、非排队）。须有并发测试佐证。
3. **【P1-4 原子互斥】** AI 与 live-auto 的「检查对方空闲 + 注册自己」必须包在**共享**准入锁内；反向校验落 `LiveAutoManager._require_capacity` 单一 choke point。须有并发 start 测试佐证至多一方成功。
4. **【P1-3 无损摘要】** `summarize()` 素材必须是 `_journal`（非可能被裁剪的 `_context`）；`ai_trade_max_context_rounds` 与 `ai_trade_max_context_messages` 保持解耦。
5. **【P1-5 契约】** 决策工具必须封装为 `ToolSchema`；须自行累积 `tool_use_start/args_delta/end` 的 `partial_json`；「必须调用一次」仅由 prompt 引导，正确性由 hold 回落兜底。
6. **【P1-6 桌面】** `desktop/src/lib/config.js` prod 白名单必须补 `AI_TRADE_` 前缀透传，且 `config.py` 必须**声明** `ai_trade_*` 字段（`extra="ignore"` 否则静默丢弃）。
7. **【P1-7 宿主】** `<AiTradePanel>` 渲染于 `WatchlistTab.tsx`（左侧），不是 `ChartWorkspace.tsx`。
8. **【P2-4 规模】** `ai_auto/session.py` 等须遵守 CLAUDE.md 阈值（单文件 ≤800 行、单函数 ≤50 行、≤5 形参、圈复杂度 ≤10、嵌套 ≤4），镜像 `live_auto` 布局预先拆分。
9. **【安全铁律不变】** 真实下单一律经 `LiveExecutor`→`okx/trading.py`，不新增绕过路径；`AI_TRADE_ENABLED` 默认 OFF；联调 ≤1% 资金、0.01 手 BTC、`bar≥15m`、启动前确认无既有持仓。

上述条件均已在 v2 正文写明具体落点，Subtask #2 可逐条实现、逐条测试验收，无需再做设计决策。
