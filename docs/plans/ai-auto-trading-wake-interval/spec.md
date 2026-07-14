# AI 自动交易 · Agent 唤醒周期（wake-interval）方案设计

- **Feature 目录**：`docs/plans/ai-auto-trading-wake-interval/`
- **文档版本**：v2（Subtask #1 方案评审修复；v1 由 Subtask #0 产出）
- **关联既有特性**：`ai-auto-trading`、`ai-trade-multi-symbol-and-timeframe`、`ai-auto-trading-agent-trace`、`ai-trade-reasoning-chain`
- **变更性质**：**纯加法**——为「启动 AI 自动交易」新增一个可配置的**墙钟定时唤醒**决策触发源；默认 2 分钟、用户可设任意分钟；对既有「确认 K 线 → 决策」路径**零回归**（会话构造默认关闭，仅经启动请求显式开启）。

---

## 评审记录（Review Notes · Subtask #1 方案评审，v1 → v2）

评审维度：可行性（Feasibility）/ 完备性（Completeness）/ 一致性（Consistency）/ 规模适配（Right-sizing）。已逐节比对 v1 方案与实际代码（`session.py` / `manager.py` / `api/ai_auto.py` / `config.py` / `schemas.py` / `trace.py` / `live_auto/executor.py` / `ai_auto/multi_timeframe_context.py` 与前端 `aiTrade.ts` / `aiTradeTypes.ts` / `store.ts` / `useAiTradeGroupStream.ts` / `AiTradeLaunchModal.tsx` / `AiTradeToggle.tsx` / `desktop/src/lib/config.js`）。

**核对属实（可实现、规模适配、无 P0）**：v1 行号引用（`feed_candle`:312、`_run_decision_round`:339、`_monitor_loop`:251、`poll_interval_s`:124 = `live_auto_poll_interval_s` 默认 **5s**）、零回归范式（`position_step_pct=0.0`）、`_resolve_position_step` 解析器、`store.ts` v28 + `aiTradeConfig` 进 partialize + migrate null 守卫、`desktop/config.js` 第 72 行 `startsWith('AI_TRADE_')` 前缀白名单、`config.py` `extra="ignore"` 均属实。核心并发/资金护栏复用路径成立。以下按严重度列出问题与**就地修复**：

| # | 维度 | 严重度 | 问题 | 处置 |
|---|---|---|---|---|
| RN-1 | 可行性 | **P1** | §2.7 将 `AiTradeLaunchOpts.wakeIntervalMinutes` 定为**非可选** `number \| null`，却要求「回测分支不带」；`handleConfirm` 回测 `onConfirm({...})` 不含该键 → **tsc 编译失败**（违反 DoD「tsc clean」）。 | 已改为**可选** `wakeIntervalMinutes?: number \| null`（对齐既有 backtest-only 可选字段 `startTs?`/`decisionEveryBars?`/`minConsensus?`）；live 分支显式带上、回测分支省略。见 §2.7、§三表。 |
| RN-2 | 一致性 | **P1** | `AiTradeLaunchModal.tsx` 存在**已归档复发缺陷**（输入约 1s 回退；最近修复 commit `7ec6259` 把 reset effect 依赖收敛为 `[open]`）。v1 仅写「useEffect([open]) 重置」，未钉死实现约束；若 #2 直觉性地新起以 `defaultWakeIntervalMinutes` 为依赖的 effect / 每 render 重置，将**再触发**该缺陷。 | 已在 §2.7、§八补**强制约束**：新输入 reset 必须是**现有 `[open]` effect 内的单行** `setWakeIntervalRaw(...)`，state 用 `useState` 初值；**严禁**把 `defaultWakeIntervalMinutes`（或任何新值）加入依赖数组或另起 effect。 |
| RN-3 | 一致性 | P2 | `session.py` 现 **811 行**，已超 CLAUDE.md §二「单文件 ≤ 800 行」软阈值，本特性再加约 30–40 行；§一要求阈值突破须在评审中记录理由。 | 已在 §八记录：决策管线高内聚，强拆会割裂 `feed_candle`/`_run_decision_round`/`_maybe_interval_wake` 单一驱动语义，**接受既有技术债、不为本特性强拆**；新方法仍守 ≤50 行 / 单一职责。后续再扩应整体抽离轨迹/事件扇出子模块。 |
| RN-4 | 完备性 | P2 | §2.2 只论证 `build_market_context`/`build_market_digest`（走 `base_df()`、与 ts 无关）在定时轮不变，**未覆盖** `_build_multi_timeframe_context(ts, ref_px)`——它依赖 `ts`（→ `as_of_ts = ts + BAR_SECONDS[bar]`）与 `ref_px`。 | 已在 §2.2 补：定时轮传 `ts=int(now)`（int，过 `build_multi_timeframe_context` 的 `type(as_of_ts) is int` 闸）；`_causal_frame` 以 `ts+BAR_SECONDS ≤ as_of_ts` 因果截断、HTF 帧两根间不变 → 纳入帧同上一确认根，仅 `last_px` 刷新支撑/阻力距离（即「同技术面、新价位」），**无 None 降级、无崩溃**。 |
| RN-5 | 完备性 | P2 | §6.1 `interval_wake_triggers_decision_between_candles` 述「await `_maybe_interval_wake()` → rounds_completed 增 1」，但该法只 `create_task` 即返回，自增须待任务完成。 | 已在 §6.1 补：断言前须 `await session._decision_task`（复用既有 `_settle(session)` 帮手，与 `feed_candle` 决策轮测试同范式）。 |
| RN-6 | 一致性 | P2 | §2.4 述 trigger「透传到 `_build_decision_payload`」但未点明形参接线；`new_round_trace` 从 `payload.get("trigger")` 读，须保证 `_begin_round_trace` 前 payload 已含 trigger。 | 已在 §2.4 明确：`_build_decision_payload(..., trigger="candle")` 增末位形参并写 `payload["trigger"]`；`_run_decision_round` 收 `trigger` 后透传给 payload 构造与 `_emit_decision`。 |
| RN-7 | 一致性 | P2 | §2.6 `_create_group_run` 落 `round(wake_interval_s/60,4)`，关闭态存 `0.0`，而 `group_status`/`info` 关闭态回显 `None`——审计表征不一。 | 已改为 `round(...) if req.wake_interval_s > 0 else None`，与回执口径统一。 |
| RN-8 | 完备性 | P2 | §2.2 `_current_px()` 若以真值判断（`_last_live_px or _ref_px()`），价为 0 的异常断面会误回落。 | 已在 §2.2 注明用 `is not None` 判定（价恒 > 0 时二者等价，防御性更稳）。 |
| RN-9 | 一致性 | P2 | §2.7 称 `validateWakeInterval`「与后端夹紧口径对齐」，实为前端越界**报错**、后端**夹紧**（与既有 `validateContextRounds` 前端报错范式一致）。 | 已修措辞为「前端越界即时报错、后端防御性夹紧，区间一致」。 |

> **复核确认的正确设计（无需改）**：① 定时唤醒复用 `_last_bar_index` 作 `executor.apply(bar_index=)` 安全——`max_hold` 以确认根计（两根间冻结、绝不早退），开仓 `entry_bar` 落最近确认根，下一确认根 `_cur_bar − entry_bar` 正确 +1；`executor.apply` 无基于 bar_index 的去重会拒绝重复 apply。② `_deciding` 的 check→set 间无 await，与 `feed_candle` 同为事件循环内原子，定时轮 / K 线轮 / 盯市止盈止损绝不并发写 executor（`_exec_lock` 串行）。③ 定时唤醒**不**发 `decision_skipped`（在飞即静默跳过、下 tick 再判），避免每 5s 事件刷屏——刻意且正确。④ 默认 `wake_interval_s=0` 全链关闭，直接构造会话 / 跑盯市的既有测试逐字零回归。

---

## 一、Overview（需求与目标）

「启动 AI 自动交易」当前的决策脑（`AiTradeSession`）**只有一个决策触发源**：OKX 公共 WS 每推来一根**确认 K 线**（`feed_candle` 且 `candle.confirm=True`），会话就步进各策略影子信号板、随即触发**一轮 AI 决策**。也就是说，Agent 的「唤醒思考」节奏被**死绑在 K 线交易级别**上——若用户选了 15 分钟级别，Agent 就每 15 分钟才思考一次，中途行情剧烈波动也要等下一根 K 线收盘才可能反应（交易所侧 OCO 止盈止损仍生效，但 AI 的主动研判被冻结）。用户需求把当前触发源归纳为两类：**① 策略触发唤醒**（策略在确认根产生边沿信号时）与 **② K 线周期唤醒**（每根确认 K 线收盘）；在代码里这两者其实**同源**——都发生在确认 K 线到达的那一刻（确认根既是 K 线边界，也是策略信号刷新点）。

本需求要求**额外**增加**第三个唤醒源**：一个**独立于 K 线周期的墙钟定时唤醒**，默认每 **2 分钟**唤醒一次让 Agent 思考决策，用户可在启动弹窗自行设置任意分钟数。举例：K 线交易级别为 15 分钟、用户设 2 分钟唤醒周期时，Agent 的唤醒决策变为「① 每 2 分钟定时唤醒思考一次；② 策略触发（即确认 K 线收盘刷新信号）时唤醒」——即在两根 15 分钟 K 线之间，Agent 也能每 2 分钟对**当前实时价 + 持仓 + 最近一次信号板**重新研判，从而更及时地止盈止损/调仓，而不必干等 15 分钟。

设计核心是**加法而非替换**：保留原有「确认 K 线 → 决策」触发（它同时承载「策略触发」与「K 线周期」两语义），叠加一条墙钟定时唤醒。当定时周期 < K 线周期时（如 2 分钟 < 15 分钟），观感上就是「每 2 分钟 + 每根 K 线收盘各触发一次」；当定时周期 ≥ K 线周期时，确认 K 线仍照常触发（决策频率不会低于 K 线频率）。定时唤醒复用**同一决策管线**（同一 `AiTradeDecider`、同一 `LiveExecutor` 守护执行、同一 `_deciding` in-flight 守卫、同一 `_exec_lock` 写串行、同一共识门禁与安全回落），因此**继承全部资金与并发护栏**，仅仅是「什么时候唤醒」多了一种来源。定时唤醒轮与 K 线唤醒轮通过「唤醒计时器随每次决策开始而重置」自动合流去重，绝不并发。

---

## 二、Technical design（技术方案）

### 2.1 现状：单一触发源与关键代码路径

- **触发点**：`services/quant/app/ai_auto/session.py::AiTradeSession.feed_candle`（session.py:312）。确认根 → `append_confirmed` → `evaluate_signal_board`（步进影子，快、无 LLM）→ 若 `_deciding` 则跳过、否则置 `_deciding=True` 并 `create_task(_run_decision_round(...))`（session.py:333-337）。
- **决策轮**：`_run_decision_round`（session.py:339）→ 装配 `payload` → `decider.decide` → 共识门禁 → `_execute_decision`（经 `_exec_lock` 串行写 `LiveExecutor`）→ `commit_round`（记 journal / 到期滚动摘要）→ 推 `decision` / `agent_trace` / `summary` 等 WS 事件；`finally` 复位 `_deciding`。
- **盯市循环**：`_monitor_loop`（session.py:251）每 `poll_interval_s`（= `live_auto_poll_interval_s`，config 默认 **5s**，session.py:124）跑一次 `_monitor_tick`：reconcile + 盯市 + staleness + 会话级止盈止损——**当前不触发任何 AI 决策**。
- **信号板快照语义（并发安全的基石）**：`signals.evaluate_signal_board` 每次调用都**返回全新的 `StrategySignalView` dataclass 列表**（`stance` 为字符串值快照，`risk_params` 为只读引用）——即便随后确认根再次步进影子，也是**新建**视图并重绑引用，旧列表不被 mutate。故「把上一次确认根的信号板存下、供定时唤醒复用」是安全的：在飞的定时决策持有的是旧快照，后续确认根不会污染它。

### 2.2 新增：墙钟定时唤醒作为第三触发源

在 `_monitor_tick` 末尾新增 `await self._maybe_interval_wake()`。该方法在满足全部条件时，**用「最近一次确认根的信号板 + 当前实时价」触发一轮标准决策**：

```
async def _maybe_interval_wake(self) -> None:
    # 关闭 / 非运行 / 在飞 / 行情陈旧 / 尚无信号板 → 一律不唤醒
    if (self.wake_interval_s <= 0 or self.state != "running" or self._deciding
            or self._stale_degraded or self._last_board is None):
        return
    now = time.time()
    if now - self._last_decision_started_wall < self.wake_interval_s:
        return
    self._deciding = True                 # 同步认领槽位：check→set 间无 await（与 feed_candle 同范式，P1-2）
    ref_px = self._current_px()           # 纯同步取实时价（不 await）
    self._last_decision_started_wall = now
    self._decision_task = asyncio.create_task(
        self._run_decision_round(self._last_board, self._last_bar_index, ref_px,
                                 int(now), trigger="interval"))
```

关键点：
- **in-flight 守卫复用**：`_deciding` 为单事件循环内的普通布尔；`check→set` 之间无 `await`，与 `feed_candle` 一致，保证同一时刻至多一轮决策；若定时到点但上一轮（K 线或定时）仍在飞，本次直接跳过（不排队、不并发），下个盯市 tick（≤5s 后）再判。
- **计时器合流去重**：`_last_decision_started_wall` 在**任何一轮决策开始时**都被刷新（K 线唤醒在 `feed_candle` 里刷新、定时唤醒在此刷新）。因此「刚跑完/正在跑一轮 K 线决策」会把定时器往后推 `wake_interval_s`，天然避免 K 线唤醒与定时唤醒挤在一起重复决策。
- **实时价而非陈旧收盘价**：定时唤醒的价值在于让 AI 看到**两根 K 线之间的当前价**。`feed_candle` 在收到**未确认根**（`candle.confirm=False`）时记录 `self._last_live_px = float(candle.c)`；`_current_px()` 优先返回 `_last_live_px`（用 **`is not None`** 判定，非真值——价恒 > 0 时等价，防 0 价异常断面误回落），回落到最近确认收盘价（`_ref_px()`）。市场体制/证据摘要（`build_market_context`/`build_market_digest`）走 `base_df()`（仅确认根，两根 K 线间不变），故定时唤醒轮的技术面上下文与上一确认根相同，但 `lastPx`/持仓盈亏是最新的——足以支撑「价格已逼近止损/止盈、是否提前离场」的研判。
- **多周期背景在定时轮的行为（RN-4）**：`_build_decision_payload` 亦含 `_build_multi_timeframe_context(ts, ref_px)`，它依赖 `ts` 与 `ref_px`（前两者不依赖 ts）。定时唤醒传 `ts=int(now)`（**int**，恰过 `build_multi_timeframe_context` 的 `type(as_of_ts) is int` 类型闸；单位与 bar ts 同为秒，无 ms/s 错配）；`as_of_ts = ts + BAR_SECONDS[bar]`，而 `_causal_frame` 以 `ts + BAR_SECONDS ≤ as_of_ts` 因果截断——HTF 帧仅在确认根经 `maybe_refresh_htf` 更新、两根 K 线间不变，故纳入的因果帧与上一确认根**完全相同**，仅 `last_px`（最近实时价）刷新最近支撑/阻力距离。定时轮的 `as_of_ts` 较确认轮前移至多一个执行周期，只令 HTF 帧 `ageSeconds` 略增（`stale` 阈值为 2×HTF 秒数，量级不敏感），**既不返回 None、亦不抛错**——正是想要的「同技术面、新价位」，与体制/证据摘要一致。
- **陈旧行情不唤醒**：`_stale_degraded=True`（`_check_staleness` 判定行情连接异常）时跳过定时唤醒——实时价不可信，交由交易所侧 OCO 保护，与既有「行情异常暂停响应 K 线」语义一致。
- **粒度**：唤醒判定寄生在 5s 的盯市循环上，故实际唤醒抖动 ≤ `poll_interval_s`（5s）。对分钟级周期可忽略；为此设 30s **硬下限**（见 §2.5），使抖动占比合理，且避免把决策脑（单轮 LLM 可耗时至 `ai_trade_decision_timeout_s`=60s + 重试）逼到永远在飞。**不**新起独立计时器任务：复用盯市循环少一个 writer/需取消的 task，取舍是牺牲亚秒精度（分钟级无所谓）。

### 2.3 信号板/索引/实时价的维护（`feed_candle` 与 `_prime`）

- `feed_candle`：
  - 未确认根分支：记录 `self._last_live_px = float(candle.c)` 后照常推 `candle{confirm:False}`。
  - 确认根分支：`append_confirmed` 后记录 `self._last_live_px = float(row["c"])`；`evaluate_signal_board` 的返回值存入 `self._last_board`、`self._last_bar_index = i`（供定时唤醒复用）；实际起 K 线决策轮时刷新 `self._last_decision_started_wall = time.time()`，并以 `trigger="candle"` 调 `_run_decision_round`。
- `_prime`：暖机逐根种入影子的循环里，**捕获最后一根的返回视图**存为 `self._last_board`（不额外多调一次 `evaluate_signal_board`，避免末根被双重步进）、`self._last_bar_index = len(candles)-1`、`self._last_live_px = self._ref_px()`。这样**首次定时唤醒可在会话启动 ~`wake_interval_s` 后即触发**（无需等第一根实时确认 K 线收盘，正是需求「不必干等 15 分钟」的落点）。`priming=False` 或无暖机数据时 `_last_board` 维持 `None`，定时唤醒待第一根确认根后开工（边角场景，可接受）。
- `start()`：在建立盯市任务处设 `self._last_decision_started_wall = time.time()`，使第一次定时唤醒在启动后约 `wake_interval_s` 触发（而非 t=0 立刻）。

### 2.4 决策轮打通 `trigger` 标签

`_run_decision_round(self, board, i, ref_px, ts, trigger: str = "candle")` 新增末位形参（默认 `"candle"`，保持既有正位调用零改动）。`trigger` 透传到：
- `_build_decision_payload(self, round_no, ts, ref_px, board, trigger: str = "candle")` **增末位形参**并在返回 dict 内写 `payload["trigger"] = trigger`（RN-6：`_run_decision_round` 须把收到的 `trigger` 传入 `_build_decision_payload`，使 payload 在 `_begin_round_trace` 之前即含该键；供 prompt/轨迹显示，决策安全性不依赖它）。
- `_begin_round_trace` → `trace_event("round_start", ..., trigger=trace["trigger"])`；`trace.new_round_trace` 增一行从 `payload.get("trigger")` 读入 `trace["trigger"]`（`_begin_round_trace` 在 consensus 之后调用，此时 payload 已含 trigger）。
- `_emit_decision(..., trigger=trigger)` → decision 事件 `data["trigger"]`（老前端忽略未知键，向前兼容）。
- `_emit_decision_skipped`（仅 `feed_candle` 的 K 线跳过路径调用）固定 `trigger="candle"`。

这样前端可清晰区分三类唤醒来源：`trigger="candle"` 且信号板有边沿 → **策略触发**；`trigger="candle"` 无边沿 → **K 线收盘常规复盘**；`trigger="interval"` → **周期唤醒**。

### 2.5 配置解析与夹紧（`config.py` + `api/ai_auto.py`）

新增三项 `Settings`（与既有 `ai_trade_*` 同段声明，`BaseSettings extra="ignore"` 须显式声明才不被丢；`AI_TRADE_` 前缀已在 desktop `config.js` 透传白名单内，故**无需改 desktop 侧任何文件**——本特性不引入新 env 注入）：

| 设置 | 默认 | 含义 |
|---|---|---|
| `ai_trade_wake_interval_default_min: float` | `2.0` | 未显式传值时的默认唤醒周期（分钟）。 |
| `ai_trade_wake_interval_floor_s: float` | `30.0` | 唤醒周期硬下限（秒），防高频 LLM 烧钱 / 抖动占比过大。 |
| `ai_trade_wake_interval_ceil_min: float` | `240.0` | 唤醒周期硬上限（分钟，= 4h）。 |

`api/ai_auto.py` 新增纯解析器（与 `_resolve_position_step` 同风格，**夹紧而非拒绝**，UX 更友好）：

```
def _resolve_wake_interval_s(minutes: float | None, settings: Settings) -> float:
    """分钟 → 秒并夹紧：None→默认；<=0→0(关闭，退出口)；否则夹到 [floor_s, ceil_min*60]。"""
    if minutes is None:
        minutes = settings.ai_trade_wake_interval_default_min
    if minutes <= 0:
        return 0.0
    lo = settings.ai_trade_wake_interval_floor_s
    hi = settings.ai_trade_wake_interval_ceil_min * 60.0
    return min(max(minutes * 60.0, lo), hi)
```

在 `ai_trade_start` 里 `wake_interval_s = _resolve_wake_interval_s(body.wakeIntervalMinutes, settings)`，塞进 `AiTradeGroupStartRequest`。

### 2.6 管理器与会话构造透传（`manager.py` + `session.py`）

- `AiTradeGroupStartRequest` 增字段 `wake_interval_s: float = 0.0`（默认 0 = 关闭，保证**直接构造该 dataclass 的既有测试零回归**，与 `position_step_pct=0.0` 同范式）。
- `AiTradeGroup` 增字段 `wake_interval_s: float`；`_build_group` 由 `req.wake_interval_s` 填入；`group_status()` 追加 `wakeIntervalMinutes = round(wake_interval_s/60, 4) if wake_interval_s > 0 else None`。
- `_build_member` 构造 `AiTradeSession(..., wake_interval_s=ctx.req.wake_interval_s)`。
- `AiTradeSession.__init__` 增形参 `wake_interval_s: float = 0.0`；存 `self.wake_interval_s` 并初始化 `self._last_board=None`、`self._last_bar_index=0`、`self._last_live_px=None`、`self._last_decision_started_wall=0.0`。**默认 0** → 定时唤醒关闭，既有直接构造会话的单测行为**逐字不变**（不跑盯市循环 / 不流逝墙钟则本就不触发；即便跑也因 `wake_interval_s<=0` 直接 return）。
- `_status_payload()` / `info()` 追加 `wakeIntervalMinutes`（同上换算）。
- `_create_group_run` 的 `config` dict 追加 `"wakeIntervalMinutes": round(req.wake_interval_s/60, 4) if req.wake_interval_s > 0 else None`（RN-7：关闭态存 `None`，与 `group_status`/`info` 回执口径一致；落 free-form `config_json`，**零 DB 迁移**，供历史/审计观测）。

### 2.7 前端：弹窗输入 + 透传 + 持久化 + 轨迹标签

- **弹窗 `AiTradeLaunchModal.tsx`**（**仅实盘段**露出：新输入置于某 `mode === "live"` 块内；回测走 `decisionEveryBars` 逐根决策、与墙钟无关）：新增「Agent 唤醒周期（分钟）」文本输入。
  - **状态与回填**：`const [wakeIntervalRaw, setWakeIntervalRaw] = useState(...)`，初值取 `defaultWakeIntervalMinutes`（prop，来自 `aiTradeConfig?.wakeIntervalMinutes ?? AI_TRADE_DEFAULT_WAKE_INTERVAL_MIN`）——完全**镜像既有 `positionStep` 的实现范式**（`useState` 初值 + 在**现有 `[open]` effect 内**加一行 `setWakeIntervalRaw(...)` 重置）。
  - **⚠️ RN-2 强制约束（防复发缺陷）**：该输入的 reset **必须**是现有 `[open]` 依赖 effect（`AiTradeLaunchModal.tsx:172-197`）内的**单行** `setWakeIntervalRaw(...)`。**严禁**：① 另起一个以 `defaultWakeIntervalMinutes` 为依赖的 `useEffect`；② 把 `defaultWakeIntervalMinutes` 或 `wakeIntervalRaw` 加入任何依赖数组；③ 在渲染体内直接以 prop 派生 state。该文件有已归档复发缺陷「输入约 1s 回退」（最近修复 `7ec6259` 即把 reset effect 依赖收敛为 `[open]`），每新增字段若违反上述约束即再破。
  - **校验**：`validateWakeInterval`（空/0 = 关闭合法；否则须为 `[AI_TRADE_WAKE_INTERVAL_FLOOR_MIN, AI_TRADE_WAKE_INTERVAL_CEIL_MIN]` 内有限数，否则报错）。**RN-9**：前端越界**即时报错**（与既有 `validateContextRounds` 前端报错范式一致），后端 `_resolve_wake_interval_s` 另做**防御性夹紧**——二者区间一致、职责互补（前端给即时反馈、后端兜边界），非同一「夹紧」语义。
  - **透传**：`AiTradeLaunchOpts` 增**可选** `wakeIntervalMinutes?: number | null`（**RN-1**：对齐既有 backtest-only 可选字段 `startTs?`/`decisionEveryBars?`/`minConsensus?`；若定为非可选，回测 `onConfirm` 不带该键将 tsc 编译失败）。`handleConfirm` **仅 `live` 分支**在 `onConfirm({ mode: "live", ..., ...base, wakeIntervalMinutes: parseWake(wakeIntervalRaw) })` 带上它（`base` 为 live/backtest 共享对象，故该键**不放入 `base`**；回测分支省略）。文案提示「越小唤醒越频繁、LLM 调用越多；留空/0 = 仅按 K 线与策略触发」。
- **Toggle `AiTradeToggle.tsx`**：给弹窗传 `defaultWakeIntervalMinutes={aiTradeConfig?.wakeIntervalMinutes ?? AI_TRADE_DEFAULT_WAKE_INTERVAL_MIN}`（镜像既有 `defaultPositionStepPct` 接线）；`startM` 的 `aiTradeApi.start({...})` 请求体加 `wakeIntervalMinutes: opts.wakeIntervalMinutes`；`onSuccess` 把 `wakeIntervalMinutes: opts.wakeIntervalMinutes ?? undefined` 一并写入 `setAiTradeConfig`（整对象替换，记忆上次偏好须取自 `opts`——`res` 不回显该键，与既有 `positionStepPct` 的 `[R-4]` 教训同）。
- **类型 `aiTradeTypes.ts`**：`AiTradeGroupStartReq` / `AiTradeConfig` / `AiTradeSessionRow` / `AiTradeStatusEvent` / `AiTradeGroupRow` / `AiTradeGroupState` 增可选 `wakeIntervalMinutes?: number`；`AiTradeDecision` / `AiTradeTraceEvent` / `AiTradeRoundTrace` 增可选 `trigger?: "candle" | "interval"`。
- **纯件 `aiTrade.ts`**：加常量 `AI_TRADE_DEFAULT_WAKE_INTERVAL_MIN=2`、`AI_TRADE_WAKE_INTERVAL_FLOOR_MIN=0.5`、`AI_TRADE_WAKE_INTERVAL_CEIL_MIN=240`；加 `validateWakeInterval(minutes)`（与后端夹紧口径对齐：空/0 合法=关闭，否则须为 [下限,上限] 内有限数）；`describeTrigger` 扩展为读 `trace.trigger`——`trigger==="interval"` 且无边沿信号时返回「周期唤醒 · 常规复盘」（tone=accent），其余分支不变。
- **流路由 `useAiTradeGroupStream.ts`**：**无逻辑改动**（新增字段皆可选、随事件 data 透传进类型对象）；仅 `groupStateFromStatus` 顺带把 `status.wakeIntervalMinutes` 映射进 `AiTradeGroupState`（1 行，供面板显示「唤醒周期 N 分钟」）。
- **store `store.ts`**：`aiTradeConfig` 已入 partialize；因 `AiTradeConfig` 新增可选键，**版本 28 → 29**，`migrate` 加一档（镜像 `positionStepPct` 的 null 守卫：`cfg` 非空则 `wakeIntervalMinutes: cfg.wakeIntervalMinutes ?? AI_TRADE_DEFAULT_WAKE_INTERVAL_MIN`，`cfg` 为 null 不动）。读点一律 `?? AI_TRADE_DEFAULT_WAKE_INTERVAL_MIN` 兜底，故老配置无此键也安全。

### 2.8 时序（15m 级别 + 2min 周期）

```
启动 → _prime（种影子 + 存 _last_board/_last_live_px）→ _last_decision_started_wall=now
 t+2m  monitor tick 判定 now-start≥120s → 定时唤醒（trigger=interval，用 _last_board + 实时价）→ 重置计时器
 t+4m  定时唤醒 … t+6m … t+14m 定时唤醒
 t+15m 确认 K 线到达 → 步进影子 → 更新 _last_board → K 线决策（trigger=candle，策略触发/常规复盘）→ 重置计时器
 t+17m 定时唤醒 …（周而复始）
 任一时刻上一轮在飞 → 新触发被 _deciding 跳过，下个 tick 再判
```

---

## 三、File / module change plan（文件变更清单）

| 文件 | 变更 | 一句话意图 |
|---|---|---|
| `services/quant/app/config.py` | 改 | 新增 `ai_trade_wake_interval_default_min/floor_s/ceil_min` 三项设置。 |
| `services/quant/app/schemas.py` | 改 | `AiTradeGroupStartIn` 增 `wakeIntervalMinutes: float | None = Field(default=None, ge=0)`；`AiTradeSessionRow`/`AiTradeGroupRow` 增可选 `wakeIntervalMinutes`（契约镜像）。 |
| `services/quant/app/api/ai_auto.py` | 改 | 加 `_resolve_wake_interval_s`；`ai_trade_start` 解析并塞入 `AiTradeGroupStartRequest.wake_interval_s`。 |
| `services/quant/app/ai_auto/manager.py` | 改 | `AiTradeGroupStartRequest`/`AiTradeGroup` 增 `wake_interval_s`；`_build_group`/`group_status`/`_build_member`/`_create_group_run` 透传与回显。 |
| `services/quant/app/ai_auto/session.py` | 改 | 新增 `wake_interval_s` 形参与 `_last_board/_last_bar_index/_last_live_px/_last_decision_started_wall` 状态；`_maybe_interval_wake`/`_current_px`；`feed_candle`/`_prime`/`start`/`_monitor_tick`/`_run_decision_round`/`_build_decision_payload`/`_begin_round_trace`/`_emit_decision`/`_status_payload`/`info` 接线 `trigger` 与定时唤醒（核心）。 |
| `services/quant/app/ai_auto/trace.py` | 改 | `new_round_trace` 从 `payload` 读入并存 `trace["trigger"]`（round_start 轨迹带唤醒来源）。 |
| `apps/web/lib/aiTradeTypes.ts` | 改 | 各请求/会话/分组/决策/轨迹类型增可选 `wakeIntervalMinutes` 与 `trigger`。 |
| `apps/web/lib/aiTrade.ts` | 改 | 唤醒周期常量 + `validateWakeInterval` + `describeTrigger` 支持 `interval`。 |
| `apps/web/components/Agent/AiTradeLaunchModal.tsx` | 改 | 实盘段新增「Agent 唤醒周期（分钟）」输入 + 校验 + 随 `onConfirm`（**仅 live 分支**）透传；`AiTradeLaunchOpts.wakeIntervalMinutes?` **可选**（RN-1）；reset 只在既有 `[open]` effect 内单行（RN-2）。 |
| `apps/web/components/Agent/AiTradeToggle.tsx` | 改 | 传入默认唤醒周期；`startM` 带上 `wakeIntervalMinutes` 并持久化到 `aiTradeConfig`。 |
| `apps/web/lib/useAiTradeGroupStream.ts` | 改 | `groupStateFromStatus` 映射 `wakeIntervalMinutes`（无其它逻辑改动）。 |
| `apps/web/lib/store.ts` | 改 | persist 版本 28→29 + `migrate` 补 `wakeIntervalMinutes` 默认（null 守卫）。 |
| `services/quant/tests/test_ai_trade_wake_interval.py` | 新增 | 定时唤醒触发/跳过/关闭/实时价/计时器重置 + 夹紧解析后端测试。 |
| `apps/web/components/Agent/AiTradeLaunchModal.test.tsx` | 改 | 断言实盘段渲染唤醒周期输入、默认值、校验、透传（回测段不渲染）。 |
| `apps/web/components/Agent/AiTradeToggle.test.tsx` | 改 | 断言 `startM` 请求体含 `wakeIntervalMinutes` 且持久化。 |
| `apps/web/lib/aiTrade.test.ts`（如存在，否则并入既有前端纯件测试） | 改/新增 | `validateWakeInterval` 与 `describeTrigger(interval)` 单测。 |

> 说明：`start`/`status` 路由手搓 dict 回执、无 `response_model`，故会话/分组视图新增字段无需改 schema 即可下发；schema 的可选字段仅作契约/TS 对齐镜像（沿用 `AiTradeDecisionOut` 的「文档镜像」范式）。

---

## 四、Interface design（接口设计）

### 4.1 REST：`POST /api/py/ai-trade/start`（`AiTradeGroupStartIn`）

请求体新增一个可选字段（其余字段不变）：

```jsonc
{
  "members": [{ "instId": "BTC-USDT-SWAP", "allocationPct": 100 }],
  "bar": "15m",
  "strategyIds": [12, 34],
  "wakeIntervalMinutes": 2,   // 新增：可选。null/缺省→服务端默认(2)；0→关闭定时唤醒；>0→夹到 [0.5, 240] 分钟
  "contextRefreshRounds": 10
  // …capitalUsd/leverage/stopLossPct/positionStepPct 等既有字段
}
```

- 校验：schema `Field(ge=0)`（0 合法=关闭）；服务端 `_resolve_wake_interval_s` **夹紧**到 `[ai_trade_wake_interval_floor_s, ai_trade_wake_interval_ceil_min*60]` 秒（不拒绝越界，向 UX 友好收敛）。
- 回执（`group_status()` dict）新增 `wakeIntervalMinutes`（夹紧后生效值，`null` 表示关闭），每成员 `info()` 亦回显同值。

### 4.2 WebSocket：`/api/ws/ai-trade/{sessionId}`（信封 `{type,data}` 不变）

- `decision` 事件 `data` 新增可选 `trigger: "candle" | "interval"`；`round_start` 型 `agent_trace` 事件 `data` 新增可选 `trigger`；`status`/`snapshot.status` 新增可选 `wakeIntervalMinutes`。均为**加法字段**，老前端忽略未知键，向前兼容。

### 4.3 CLI / env

无新增 CLI。三项设置可经 `AI_TRADE_WAKE_INTERVAL_DEFAULT_MIN` / `AI_TRADE_WAKE_INTERVAL_FLOOR_S` / `AI_TRADE_WAKE_INTERVAL_CEIL_MIN` 环境变量覆盖（`AI_TRADE_` 前缀已在 desktop `config.js` 透传白名单内，无需改动打包侧）。

---

## 五、Data model（数据结构）

- **无 DB schema 迁移**。运行配置沿用既有 `strategy_runs.config_json`（free-form），追加 `wakeIntervalMinutes` 键即可。
- **进程内会话新增状态**（`AiTradeSession`）：
  - `wake_interval_s: float`（0=关闭）
  - `_last_board: list[StrategySignalView] | None`（最近一次确认根信号板快照，定时唤醒复用）
  - `_last_bar_index: int`（对应窗口位置索引）
  - `_last_live_px: float | None`（最近一次未确认/确认根收盘实时价）
  - `_last_decision_started_wall: float`（最近一轮决策开始的墙钟，定时器基准）
- **前端持久化偏好**（`AiTradeConfig`，进 partialize，store v29）：新增可选 `wakeIntervalMinutes?: number`。
- **配置项**（见 §2.5 表）。

---

## 六、Testing & acceptance criteria（测试与验收）

### 6.1 后端单测（`test_ai_trade_wake_interval.py`，真实 `AiTradeSession`，注入 candle / 直调方法 + mock 时钟）

- `interval_wake_triggers_decision_between_candles`：`wake_interval_s=120`，暖机 + 一根确认根决策后，`_last_decision_started_wall` 回拨 121s、置 `_last_live_px`，`await _maybe_interval_wake()` 起任务后**须 `await session._decision_task`（复用既有 `_settle(session)` 帮手，RN-5）**再断言 → `rounds_completed` 增 1，最新 `decision` 事件 `trigger=="interval"`。（`_maybe_interval_wake` 只 `create_task` 即返回，不 await 直接断言必然失败。）
- `interval_wake_reuses_last_board_and_live_px`：定时轮 `payload.signalBoard` == 上一确认根的板、`lastPx == _last_live_px`。
- `interval_wake_skipped_when_deciding`：`_deciding=True` → `_maybe_interval_wake` 不起新任务。
- `interval_wake_skipped_when_stale`：`_stale_degraded=True` → 不唤醒。
- `interval_wake_disabled_when_zero`：`wake_interval_s=0` → 时间流逝也不唤醒。
- `candle_decision_resets_interval_timer`：确认根决策后 `_last_decision_started_wall` 为近值 → 紧邻的 `_maybe_interval_wake` 不立即触发。
- `no_board_before_first_candle_no_wake`：`priming=False`、`_last_board is None` → 不唤醒。
- `resolve_wake_interval_clamps`：`_resolve_wake_interval_s`：`None→默认*60`；`0→0`；`0.1→floor_s`；`9999→ceil_min*60`；正常值原样*60。
- 既有 `test_ai_trade_*`（决策轮/共识/多周期/回测）**全绿零回归**（会话默认 `wake_interval_s=0`）。

### 6.2 前端单测

- `AiTradeLaunchModal.test.tsx`：实盘段渲染「Agent 唤醒周期」输入且默认 2；回测段**不**渲染；改值后 `onConfirm` 的 live opts 含 `wakeIntervalMinutes`；非法值（负数/非数）出错阻止提交；空/0 视为关闭且可提交。
- `AiTradeToggle.test.tsx`：`startM` 请求体含 `wakeIntervalMinutes`；成功后写入 `aiTradeConfig`。
- `aiTrade.test.ts`：`validateWakeInterval`（空/0 合法、越界报错、区间内 OK）；`describeTrigger` 对 `trigger==="interval"` 且无边沿返回「周期唤醒 · 常规复盘」。

### 6.3 验收标准（Definition of Done for feature）

1. 启动弹窗（实盘）可设置 Agent 唤醒周期，默认 2 分钟；空/0 = 关闭。
2. K 线级别 15m、周期设 2m 时，实测 Agent 约每 2 分钟触发一轮决策（`trigger=interval`），且每根确认 K 线仍触发一轮（`trigger=candle`），二者不并发、不重复堆叠。
3. 定时唤醒轮使用**当前实时价 + 最近信号板 + 持仓**研判，经同一守护执行器落地，继承全部资金/并发护栏。
4. 后端 pytest 全绿（含新测）、`ruff` clean；前端 vitest 全绿、`tsc` clean（除既有无关的 `WeightBatchArena.test.tsx` 预存错误）。
5. `git status` 可见本 `spec.md`；无 DB 迁移、无 desktop `config.js` 改动。

---

## 七、Risks & mitigations（风险与缓解）

| # | 风险 | 缓解 |
|---|---|---|
| R-1 | **LLM 成本/限频**：过小的唤醒周期 → 高频决策 → 烧 token / 触发限频。 | 30s 硬下限（`ai_trade_wake_interval_floor_s`）+ 弹窗文案警示；`_deciding` 守卫使实际频率不低于单轮 LLM 时延（自调速）。 |
| R-2 | **既有测试回归**：默认周期若非 0，会话在直接构造/跑盯市的测试里意外多决策。 | 会话构造默认 `wake_interval_s=0`（与 `position_step_pct=0.0` 同零回归范式）；仅经启动请求显式开启（默认 2 由 API 解析注入）。 |
| R-3 | **并发写 `LiveExecutor`**：定时轮与 K 线轮/盯市止盈止损同时写。 | 复用现成 `_deciding`（至多一轮在飞）+ `_exec_lock`（串行化一切 executor 写）；定时唤醒不新增并发路径。 |
| R-4 | **陈旧数据决策**：行情中断时定时唤醒仍以旧价研判。 | `_stale_degraded` 时跳过定时唤醒；交易所侧 OCO 仍生效。 |
| R-5 | **信号板快照被后续确认根污染**：在飞定时轮持有的板被 mutate。 | `evaluate_signal_board` 每次返回全新视图列表、后续根重绑引用不改旧列表——快照天然冻结（§2.1）。 |
| R-6 | **时间戳时间线混淆**：定时轮用墙钟 `ts`、K 线轮用 bar `ts`。 | 决策列表按 `round`（唯一自增）排序/去重，非按 `ts`；`trigger` 字段显式标注来源，前端据此文案区分。 |
| R-7 | **stop 竞态**：定时轮起任务后会话被 stop。 | `_execute_decision` 内 `state != "running"` 早退（既有护栏）；`stop()` 取消 `_decision_task`。 |
| R-8 | **首轮过早**：`_last_decision_started_wall` 初值使 t=0 立刻唤醒。 | `start()` 里初始化为 `time.time()`，首次定时唤醒在启动后约 `wake_interval_s` 触发。 |
| R-9 | **store 迁移遗漏**：老 `aiTradeConfig` 无新键。 | v28→29 迁移 + 所有读点 `?? 默认` 兜底；`aiTradeConfig` 可为 null 守卫。 |
| R-10 | **回测被误加墙钟**：回测走逐根、无墙钟语义。 | 唤醒周期仅实盘段露出与生效；`AiBacktestRunner` 路径不接线，回测零改动。 |

---

## 八、实施注意（供 #2 代码开发节点）

- **CLAUDE.md 文件行数阈值（RN-3，须知情）**：`session.py` 现为 **811 行**，已超 §二「单文件 ≤ 800 行」软阈值（**既有技术债、非本特性引入**）；本特性再加约 30–40 行。评审裁定：决策管线高内聚，`feed_candle` / `_run_decision_round` / `_maybe_interval_wake` 共享大量会话内部态（`_deciding`/`_last_board`/`_exec_lock`/窗口/执行器），强拆会割裂单一驱动语义、增加跨模块耦合，**收益为负**——故**接受既有超限、不为本特性强拆**（按 CLAUDE.md §一「阈值调整须在评审中说明理由」在此显式记录）。硬性要求：新增方法（`_maybe_interval_wake`/`_current_px`）各 ≤50 行、单一职责；`_run_decision_round`/`_build_decision_payload` 仅加末位形参与透传，不重构主体。若后续再有增量，应先把「轨迹装配 + 事件扇出」整体抽离为子模块再扩。
- **前端弹窗 reset 复发缺陷（RN-2，硬约束）**：`AiTradeLaunchModal.tsx` 的唤醒周期输入 reset **只能**写在现有 `[open]` 依赖 effect（`:172-197`）内的单行 `setWakeIntervalRaw(...)`；**严禁**新起以 `defaultWakeIntervalMinutes` 为依赖的 effect、严禁把任何新值加入依赖数组、严禁渲染体内派生 state。此为已归档复发缺陷（输入约 1s 回退，`7ec6259` 修复），务必逐字遵循既有 `positionStep` 范式。
- **`AiTradeLaunchOpts.wakeIntervalMinutes?` 声明为可选（RN-1）**：`handleConfirm` 仅 `live` 分支带上、`base` 不含该键、回测分支省略——否则 tsc 编译失败（DoD 要求 tsc clean）。
- 精确暂存（#3）：本特性触及的前端目录与其它在制特性共享，提交须 `git add <具体路径>` 逐文件，勿 `git add -A`。
- 不改 `LiveExecutor`、不改回测、不改 live-auto、不新增 DB 列。

---

## 评审结论（Review Verdict）

**结论：有条件通过（Approved with conditions）。**

方案在**可行性、一致性、规模适配**三维均达标：所有引用的代码路径 / 行号 / 既有范式（`position_step_pct=0.0` 零回归、`_resolve_position_step` 解析、store v28 migrate null 守卫、desktop `AI_TRADE_` 前缀白名单、`extra="ignore"`）经逐一核对属实；核心「墙钟定时唤醒作为第三触发源、复用同一决策管线与全部资金/并发护栏」的设计正确且**无 P0**——`_deciding` 原子守卫、`_exec_lock` 串行、`_stale_degraded` 跳过、stop 竞态早退、快照冻结、默认 0 全链关闭均成立，`_last_bar_index` 复用对 `max_hold` 计账安全。规模适配得当（纯加法、零 DB 迁移、零 desktop 改动、不新增并发路径）。

**两个 P1 已就地修复**：RN-1（`AiTradeLaunchOpts.wakeIntervalMinutes` 改可选，杜绝 tsc 失败）、RN-2（钉死弹窗 reset-effect 复发缺陷的实现约束）。**七个 P2 已就地修复/澄清**（RN-3 会话文件超限理由记录、RN-4 多周期背景在定时轮的安全性论证、RN-5 测试须 await 决策任务、RN-6 trigger 形参接线、RN-7 config 关闭态存 None、RN-8 `_current_px` 用 `is not None`、RN-9 前后端校验语义澄清）。**当前无未决 P0/P1**。

**放行条件（#2 代码开发节点须逐条落实）**：
1. **[RN-2 硬约束]** `AiTradeLaunchModal.tsx` 唤醒周期输入 reset 只写在现有 `[open]` effect 内单行，严禁新 effect / 新依赖 / 渲染体派生 state——逐字遵循 `positionStep` 范式（防已归档复发缺陷）。
2. **[RN-1]** `AiTradeLaunchOpts.wakeIntervalMinutes?` 声明为可选，仅 `handleConfirm` 的 live 分支透传、`base` 不含、回测省略。
3. **[RN-5]** 新增 `interval_wake_*` 测试起任务后须 `await session._decision_task`（复用 `_settle`）再断言。
4. **[RN-3]** 接受 `session.py` 811 行既有超限（已记录理由），新方法各 ≤50 行 / 单一职责，不重构 `_run_decision_round` 主体。
5. **[RN-6/7]** trigger 形参按 §2.4 修订接线；`_create_group_run` 关闭态落 `None`。
6. 收口验证（DoD）：后端 pytest 全绿（含新测）+ `ruff` clean；前端 vitest 全绿 + `tsc` clean（除既有无关 `WeightBatchArena.test.tsx` 预存错误）；`git status` 仅见本特性相关文件；无 DB 迁移、无 desktop `config.js` 改动。

以上条件均为实现期须遵循的既定约束（已写入 §2.x / §六 / §八），不构成方案性阻塞；#2 可据 v2 直接逐行实现。
