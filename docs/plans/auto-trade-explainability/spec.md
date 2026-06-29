# 实盘自动交易可解释性审计（auto-trade-explainability）开发方案

> 版本：**v2**（方案评审与修复 · Subtask #1；基线 v1 见文末「评审结论」）
> 适用范围：仅实盘**自动交易**（`live_auto` 模块，真实 OKX 下单）。回测 / 单策略实时模拟（paper）可复用同一数据形状，但本期不强制改造。
> 关联模块：`live-auto-trading`、`live-auto-trading-hardening`、`factor-layer`、`live-trading-mode`。

---

## 〇、评审记录（Review Notes · v2）

> 本节由 Subtask #1（方案评审与修复）写入。评审方式：把 v1 对现有代码的**每一处事实声明**与真实源码逐一核对（核对文件见下），再就 **可行性 / 完整性 / 一致性 / 适度性** 四维提出问题，所有 **P0 / P1** 已就地修复并在对应章节标注 `（v2 修复）`，**P2** 列出并按需就地修订或记为「可接受/实现期注意」。

**核对的真实源码**（行号为评审当时）：
`engine/factors/scoring.py`（`compose_score` 82-115、`causal_zscore`/`directional_values`/`member_names`、`DEFAULT_NORM_WINDOW=200`/`DEFAULT_CLIP=4.0`）、`strategies/base.py`（`Signal` 17-22，Pydantic BaseModel）、`strategies/factor_score_strategy.py`（`prepare` 71-96 / `on_candle` 98-114）、`live_auto/executor.py`（`apply`/`_project_signals`/`_do_open`/`_do_close`/`_settle_close` 191-200 /`_settle_benign_close` 202-208 /`_on_reconcile_ok` 234-244 /`flatten`+`_settle_flatten_ok` 268-299 /`_make_trade` 354-363；共 371 行）、`live_auto/session.py`（`_on_confirmed`/`_monitor_tick`/`_handle_flatten_result`/`_drive_signals`/`snapshot`/`run_id`；共 426 行，**无** `_dispatch`）、`db/models.py`（`Trade` 67-85，FK→`strategy_runs.id`，已含 `stop_loss_price`/`take_profit_price`）、`db/session.py`（`_ensure_trade_risk_columns` 幂等补列）、`db/repositories.py`（`append_trade` 244-250 **自带 commit**、`list_trades`、`persist_backtest_result`）、`schemas.py`（`trade_out` 243-250，恒含全部键）、`api/runs.py`（`GET /runs/{id}/trades` 用 `trade_out`；`main.py` 以 `prefix="/api"` 挂载，前端经 `/api/py` Next rewrite 代理）、`apps/web/lib/types.ts`（`Trade` 195-212，`reason` 必填、无 `explain/clOrdId/ordId`）、`apps/web/lib/store.ts`（`liveAutoTrades` + `appendLiveAutoTrade` 328）、`apps/web/app/page.tsx`（`handleLiveAutoEvent`→`appendLiveAutoTrade` 740）、`components/Strategy/AutoTradeEventLog.tsx`（挂载于 `StrategyPanel.tsx`，`mode==="paper"`）、`StrategyPanel.tsx`（845 行）、`TradesTable.tsx`（94 行，无展开）、`globals.css`（`--long/--short/--muted/--accent` 明暗双主题齐备）、`vitest.config.ts`（jsdom、jest-dom 未全局 setup）。

**结论速览**：无 **P0**；**P1 共 2 项**（均已 v2 修复）；**P2 共 6 项**（措辞类已就地修订，其余记为实现期注意/可接受）。v1 对 scoring/strategy/DB/schemas/前端 store 与挂载点的绝大多数声明**经核对属实**（含 `appendLiveAutoTrade`、`StrategyPanel` 挂载点、`/api/py` 路径——均正确）。

| 编号 | 维度 | 严重度 | 问题 | 处置 |
|---|---|---|---|---|
| P1-1 | 完整性/可行性 | **P1** | v1 §2.1/§2.4 把所有平仓解释的挂载点都归到 `_do_close`，与实际不符：① 平仓 trade 实际只在 **`_settle_close`** 一处生成（`_do_close` 触达 signal_close/reverse/max_hold；**`flatten`→`_settle_flatten_ok`** 触达 session_stop，且 flatten 返回**嵌套 `trade` 的 dict** 而非事件 list，由 session `_handle_flatten_result` 推送）；② `external_close` 对应的 `_settle_benign_close`（OCO 已平）与 `_on_reconcile_ok`（侦测外部/手动平仓）**当前只推 status 事件、根本不产生 trade**——给「已有 trade 挂 kind」无从谈起。 | **已 v2 修复**：见 §2.1、§2.4、§三、§六、R11。把 explain 收口注入 `_settle_close`（单一 choke point）并经 flatten 路径透传；`external_close` 明确界定为在两条 reconcile/flatten 分支**新增合成平仓 trade**（仅记录已发生的交易所侧平仓，不下任何单），best-effort + 异常隔离。 |
| P1-2 | 可行性 | **P1** | v1 称「从 `place_order` 回执取 clOrdId/ordId」，但 `_do_open` **丢弃** `_place_order(order)` 返回（executor.py:166），且 `place_live_order` 是否返回 clOrdId/ordId 未经证实；平仓侧 `_close_position` 的 `result` 含 `clOrdId`（`_settle_flatten_ok` 已用）但未必含 `ordId`。把 clOrdId/ordId 当作必得字段有落空风险。 | **已 v2 修复**：见 §2.4、§5.2。clOrdId/ordId 一律标注 **best-effort 可选**；开仓侧改为捕获现被丢弃的 `_place_order` 返回；任一缺失安全留空，**绝不**因此阻塞 trade/explain/下单。 |
| P2-1 | 一致性 | P2 | session **无** `_dispatch`；现有推送内联在 `_on_confirmed`/`_monitor_tick`/`_handle_flatten_result` 三处；v1 §2.5 写的 `_settle_*` 属 executor 而非 session。 | **已 v2 就地修订** §2.5：列明三处真实推送点，三者统一改走 `_dispatch` 才能注入 `explain.meta` 并落库。 |
| P2-2 | 完整性 | P2 | `flatten()` 返回 dict（嵌套 `trade`），非事件 list；v1 §2.4「apply/reconcile/flatten 返回结构不变」措辞易误导。 | **已 v2 就地修订** §2.4：补注 flatten 的嵌套形态及其 `trade` 由 `_handle_flatten_result` 推送。 |
| P2-3 | 完整性 | P2 | v1 未点明落库的提交语义。`append_trade`（244-250）**每次自带 commit**；新 `append_live_trade` 须镜像之，否则 best-effort 落库不持久，违背「刷新可复盘」验收项。 | **已 v2 就地修订** §2.5、§三：`append_live_trade` 镜像 `append_trade` 自带 commit；session 开短生命周期 session 调用。 |
| P2-4 | 完整性 | P2 | seeded/hydrated 持仓（`_make_trade` 的 `seeded`）产生的 trade 无信号来源，展开会「空洞」。 | **已 v2 就地修订** §2.4、R7：此类 trade 回落 atomic/legacy 解释（类别=持仓初始化/外部种子），展开始终有内容。 |
| P2-5 | 适度性/一致性 | P2 | `StrategyPanel.tsx` 已 845 行（>800 软上限）。 | 记为实现期注意：新表/详情/因子图必须独立成件（v1 已如此规划），仅在面板加**最小挂载**（≤ 数行），不得把表逻辑塞进 `StrategyPanel`。 |
| P2-6 | 一致性 | P2 | `trade_out`（243-250）**恒含全部键**（非条件包含）。 | **已 v2 就地修订** §三、§5.2：新增 `explain` 键宜**恒在**（缺省/坏 JSON → `null`），与既有风格一致；`reason` 在前端类型中为必填。 |

---

## 一、概述（Overview）

当前实盘自动交易（`services/quant/app/live_auto/*`）已经能在每根**已确认 K 线**上把策略信号翻译成真实 OKX 订单：`LiveAutoSession._drive_signals` 调 `strategy.on_candle(...)` 拿到 `Signal` 列表，经 `LiveExecutor.apply(...)` 下真单 / 平仓，并通过 `/ws/live-auto/{sid}` 把 `trade` 事件流到前端。每笔 trade 已经携带一个**短文本** `reason`（如「复合得分多头」「得分落入退出带」「max_hold」「reverse」），但它只够当图表 marker 的 tooltip，**不足以支撑审计**：用户看不到「这一刻的复合得分是多少、离阈值多远、哪几个因子在推动、各自方向 / 权重 / 贡献是多少」，也看不到平仓究竟是策略信号、时间止盈、反手、还是会话级止损触发的。前端目前甚至**没有**自动交易的成交明细表（trade 事件只驱动图表 markers 与事件流 `AutoTradeEventLog`），更谈不上「逐笔展开看触发原因」。

本方案给自动交易补上一条**端到端的可解释性链路**：在决策发生的那一刻（策略 `on_candle` / 执行器生命周期分支）捕获一份**结构化触发解释** `TriggerExplain`，把它随 trade 一起持久化（`trades.explain_json`）、随 WS 流到前端，并在一张**可逐行展开的「自动交易成交记录」表**中以「顶级设计、符合人机交互最佳实践」的方式呈现。展开后用户能看到：本笔决策的类别（开仓 / 平仓 / 时间止盈 / 反手 / 会话止损 / 交易所侧外部平仓）、一句话原因、关键指标对照（复合得分 vs 进场阈值、持仓根数 vs 上限、会话累计盈亏 vs 阈值）、风控快照（止盈止损触发价），以及——当策略是**因子组合（`factor_score`）**时——一张**按贡献度排序的因子归因图**：每个成员因子的方向、原始方向化值、因果 z-score、权重、对复合得分的贡献，以及它在本次触发中的占比。

设计遵循三条既有架构约束：(1) **单一因果归一化真源**——因子归因复用 `engine.factors.scoring.causal_zscore` / 权重解析，绝不在解释期重算方向或换窗口，杜绝前视泄漏；(2) **不新增绕过护栏的下单路径**——解释是 trade 的旁路元数据，**下单 / 平仓 / 撮合 / 护栏 / `live_order_audit` 调用链一字不动**；v2 唯一的行为新增是对「交易所侧已发生的 OCO / 外部平仓」**补记一条只读的 `external_close` 成交行**（§2.4），它**不调用任何下单 / 平仓接口**，仅把既有事实落成可审计记录；(3) **诚实降级**——解释构建是纯函数且全程异常隔离，构建 / 落库失败只回落「仅 reason」或原 status，**绝不**阻塞真实下单或拖垮会话。解释内容只含行情 / 因子数值，永不含任何凭证。

---

## 二、技术设计（Technical Design）

### 2.1 架构与数据流

```text
[决策时刻] 策略 / 执行器生命周期分支
  策略信号解释：FactorScoreStrategy.prepare 一次性算 score_breakdown（逐成员贡献，向量化、因果）
                → on_candle 在第 i 根取断面，构造 Signal.explain（kind=factor_score）
  生命周期解释：LiveExecutor 在 max_hold / reverse / session_stop / 外部平仓分支，
                经 live_auto/explain.py 纯构造器合成 TriggerExplain
        │
        ▼
[执行] LiveExecutor.apply → _do_open / _do_close → 真实 OKX 下单（既有护栏 + live_order_audit 不变）
        开仓 trade 在 _do_open 经 _make_trade(... explain=...) 生成；
        平仓 trade 统一在 _settle_close(... explain=...) 这**唯一处** _make_trade 生成——
          ├ _do_close 触达：signal_close / reverse / max_hold
          └ flatten → _settle_flatten_ok 触达：session_stop（flatten 返回**嵌套 trade 的 dict**，
                       由 session._handle_flatten_result 取 trade 推送，非事件 list）
        外部平仓：_settle_benign_close（OCO 已平）/ _on_reconcile_ok（侦测外部/手动平仓）
          原本仅推 status，**v2 新增**在此**合成**一条 kind=external_close 的平仓 trade（仅记录已发生的
          交易所侧平仓，**不下任何单**），best-effort + 异常隔离。
        _make_trade(... explain=, cl_ord_id=, ord_id=)：explain 必带；clOrdId/ordId **best-effort 可选**
          （开仓取 _place_order 回执——现被丢弃，需改为捕获；平仓取 _close_position 的 result.clOrdId；缺失留空）。
        │  trade dict（含 explain）
        ▼
[会话] LiveAutoSession._dispatch(events) 统一分发（替代 _on_confirmed / _monitor_tick /
        _handle_flatten_result 三处内联 _push）：
        ① WS 扇出 {"type":"trade","data":trade}（explain 随行，向后兼容）
        ② 持久化审计：repo.append_live_trade(run_id, trade) 落 trades 行 + explain_json
           （镜像既有 append_trade 的自带 commit；best-effort，失败仅 logger.warning）
        ③ 注入 explain.meta（strategyName/kind/instId/bar/barTs）
        │
        ▼
[前端] page.tsx handleLiveAutoEvent → store.liveAutoTrades（已存在）
        新增 <AutoTradeTradesTable>（StrategyPanel 自动交易区）：逐行可展开
        展开 → <TradeExplainDetail>（摘要 + 指标对照 + 风控 + <FactorContributionBars> 因子归因图）
        刷新后：可经 GET /runs/{runId}/trades 拉回历史（trade_out 现已含 explain）
```

关键点：解释的**产生**分两路汇合到 trade —— 信号驱动的开 / 平由**策略**给（factor_score 给满因子归因；原子策略由 `explain.py` 的通用构造器据 `reason` + 断面合成简版），生命周期触发的平仓由**执行器**给。两路最终都落到 `_make_trade(... explain=...)`，下游（WS / 持久化 / 前端）只认 trade 上的 `explain`，与决策来源解耦。

### 2.2 因子归因：`score_breakdown`（单一因果真源）

在 `engine/factors/scoring.py` 新增纯函数 `score_breakdown(...)`，**把 `compose_score` 现有的「逐成员加权 z-score 求和」循环抽出来并产出逐成员明细**，再让 `compose_score` 调它求和 / squash —— 即 `compose_score` 成为 `score_breakdown` 的薄封装，**数值逐位等价**，彻底避免「解释一套、决策另一套」的双口径风险（与项目「因子层单一归一化契约」一致）。

```python
@dataclass
class MemberContribution:
    name: str          # member_names() 的稳定列名
    factor_key: str
    direction: str     # long | short（冻结）
    weight: float
    value: pd.Series        # 方向化原始值 dir·raw（directional_values）
    zscore: pd.Series       # causal_zscore(value, norm_window, clip)
    contribution: pd.Series # weight * zscore（pre-squash 贡献）

@dataclass
class ScoreBreakdown:
    composite: pd.Series    # Σ contribution（pre-squash）
    score: pd.Series        # squash 后（= compose_score 输出，逐位等价）
    members: list[MemberContribution]
    squash: bool

def score_breakdown(df, members, weights=None, *, norm_window=DEFAULT_NORM_WINDOW,
                    clip=DEFAULT_CLIP, squash=True, bar="") -> ScoreBreakdown: ...
```

- 复用既有 `member_names` / `directional_values` / `causal_zscore` 与「`weights or 成员自带 weight`、weight==0 跳过」的权重解析，保证与 `compose_score` 同口径。
- `compose_score` 改为：`bd = score_breakdown(...); return bd.score`（保持签名 / 返回不变，所有现有调用者零感知）。

### 2.3 策略侧解释（factor_score）

`factor_score_strategy.py`：
- `prepare(...)`：在算 `_score` 的同一处改调 `score_breakdown(...)`，把 `ScoreBreakdown` 存到 `self._breakdown`（与 `df` 等长、暖机段 NaN）；`df["_score"] = bd.score.to_numpy()`（数值不变）。`members` 为空 / 解析失败仍走原「安静空仓」分支，`self._breakdown = None`。
- `on_candle(...)`：在产出多 / 空 / 平信号时，调本文件新增的私有构造 `_build_explain(i, side, value, thresholds)`，从 `self._breakdown` 取第 i 根断面，组装 `Signal.explain`（`kind="factor_score"`，含 `factors` 列表、`composite`、`metrics`：复合得分 / 进多阈值 / 进空阈值 / 退出带 / 仓位强度）。平仓信号（落退出带）同样给 `factors` 断面（让用户看到「平仓那一刻各因子状态」）。
- 异常隔离：`_build_explain` 整体 `try/except`，失败回 `None`（执行器随后回落通用构造器据 `reason` 合成），**绝不**让解释构建影响信号本身。

### 2.4 执行器侧解释与生命周期分支

`live_auto/executor.py`（实测 371 行；下述挂载点对照真实调用图，已修正 v1 的归属错误）：

- `_make_trade(...)`（354-363）增**关键字**形参 `explain: dict | None = None`、`cl_ord_id: str | None = None`、`ord_id: str | None = None`，写进 trade dict（`explain`、`clOrdId`、`ordId`）。`reason`/`seeded` 等既有键不变（`reason` 作 headline 与图表 tooltip）。**seeded（持仓初始化/外部种子）trade 无信号来源**：`explain` 回落 `build_atomic_explain(...)`（kind=`atomic`，文案「持仓初始化/外部种子，非策略主动信号」），保证展开不空洞（见 R7）。
- `_project_signals`（121-141）：当前**只透传 `reason` 字符串**。新增把原始 `Signal`（或其 `.explain`）一并透传到 `_do_open / _do_close`，使开 / 平能拿到 factor_score 的满因子归因；动作元组扩一位、`apply`（106-119）随之透传。
- `_do_open`（153-175）：下单成功后 `explain = signal.explain or build_atomic_explain(...)`（factor_score 用信号解释；原子策略走通用构造器）。**`_place_order(order)` 的返回当前被丢弃（166 行），改为捕获**并尽量取 `clOrdId/ordId`（**best-effort**，缺失留空，绝不阻塞）→ `_make_trade(... explain=, cl_ord_id=, ord_id=)`。
- **平仓 trade 的唯一生成点是 `_settle_close(...)`（191-200）**——v1 误归到 `_do_close`。修正后：
  - `_do_close`（177-189）据 `reason ∈ {signal_close, reverse, max_hold}` 选 `explain.py` 构造器（含「持仓根数 vs max_hold」「原方向→新方向」等指标；退出带平仓若带 `signal.explain` 优先用之），把 explain 透传给 `_settle_close`；`_settle_close` 在 `_make_trade("close", ... explain=...)` 处挂上（`result.clOrdId` 一并透传，best-effort）。
  - **session_stop 不经 `_do_close`**，而是 `flatten`（268-293）→ `_settle_flatten_ok`（295-299）→ `_settle_close(... "session_stop", explain=build_session_stop_explain(...))`。`flatten` 返回**嵌套 `trade` 的 dict**（`{ok, closedSz, clOrdId, trade}`），由 session `_handle_flatten_result` 取出推送（非事件 list）。
- **external_close（v2 行为变更，P1-1 修复）**：`_settle_benign_close`（202-208，OCO 已平）与 `_on_reconcile_ok`（234-244，侦测外部/手动平仓）**当前只推 status、不产生 trade**。v2 在这两条分支**新增合成一条 `kind="external_close"` 平仓 trade**（pnl 取 `_close_pnl(...)`，explain 经 `build_external_close_explain(...)` 说明「交易所侧止盈止损或外部干预触发，非策略主动信号；本系统据 reconcile/flatten 校正」）。**该 trade 仅记录已经发生的交易所侧平仓，不调用任何下单 / 平仓接口**，故不触碰护栏；整体 best-effort + `try/except` 隔离，合成失败回落原 status 事件，绝不影响 reconcile/flatten 主流程。

`apply(...)` 返回 `list[dict]` 事件、`reconcile(...)` 返回 `list[dict]` 事件——结构不变，trade 事件 `data` 仅多 `explain/clOrdId/ordId`（含上面新增的 external_close trade 事件）；`flatten(...)` 返回 **dict（嵌套 `trade`）不变**，其 `trade` 同样携带 `explain`。

### 2.5 会话侧：分发 + 持久化审计

`live_auto/session.py`（实测 426 行，**当前无 `_dispatch`**——推送内联在三处）：
- 新抽 `_dispatch(events)`：遍历 executor 返回的事件，`_push` 之外，对 `type=="trade"` 的事件**先注入** `explain.meta`（`strategyName/strategyKind/instId/bar/barTs=该确认根 ts`），再 `await self._persist_trade(trade)`。**当前三处内联 `_push` 统一改走 `_dispatch`**：`_on_confirmed`（驱动信号 + reconcile 后扇出）、`_monitor_tick`（盯市 reconcile 扇出，含 v2 新增的 external_close trade）、`_handle_flatten_result`（取 flatten 返回 dict 的 `trade` 推送 session_stop）。（注：`_settle_*` 是 executor 内部方法，不在 session——v1 此处措辞已更正。）
- `_persist_trade(trade)`：开短生命周期 session（`async with get_sessionmaker()() as s:`）调 `repo.append_live_trade(s, self.run_id, trade)`。**`append_live_trade` 镜像既有 `append_trade`（repositories.py:244-250）的「自带 `await s.commit()`」语义**——否则 best-effort 落库不持久、违背「刷新可复盘」验收。整体 `try/except` + `logger.warning`（best-effort，**绝不**因落库失败中断真实交易 / 平仓，沿用 H4 诚实降级风格）。这把自动交易从「仅内存 + 流」升级为「**可持久审计**」：刷新 / 重启后历史触发仍可经 `GET /api/py/runs/{runId}/trades`（FastAPI 侧 `/api/runs/...`）复盘。
- `snapshot()`（含 `"trades": list(self.executor.trades)`，已含 explain）WS 订阅即重放，零额外改动。

### 2.6 序列：一笔 factor_score 开多的端到端

1. 确认根到达 → `_on_confirmed` → `_drive_signals` → `strategy.on_candle` 返回 `Signal(side="long", strength, reason="复合得分多头", explain={kind:"factor_score", factors:[…], composite:{score,enterLong,…}, metrics:[…]})`。
2. `executor.apply` → `_do_open`：`place_live_order`（既有护栏 + 审计），成功；`_make_trade(... explain=signal.explain, cl_ord_id, ord_id)` → trade dict。
3. 会话 `_dispatch`：注入 `explain.meta` → `_push({"type":"trade",...})`（前端实时插行）+ `_persist_trade`（落 `trades` 行 + `explain_json`）。
4. 前端 `handleLiveAutoEvent` → `appendLiveAutoTrade` → `<AutoTradeTradesTable>` 新增一行；用户点开 → `<TradeExplainDetail>` 渲染指标对照 + `<FactorContributionBars>` 因子归因图。

---

## 三、文件 / 模块变更计划（File / Module Change Plan）

| 文件 | 新建/修改 | 一句话意图 |
|---|---|---|
| `services/quant/app/engine/factors/scoring.py` | 修改 | 新增 `score_breakdown(...)`/`MemberContribution`/`ScoreBreakdown`；`compose_score` 改为其薄封装（数值逐位等价，单一因果真源）。 |
| `services/quant/app/strategies/base.py` | 修改 | `Signal` 增可选字段 `explain: dict | None = None`（默认 None，全策略向后兼容）。 |
| `services/quant/app/strategies/factor_score_strategy.py` | 修改 | `prepare` 改算并缓存 `score_breakdown`；新增 `_build_explain` 在 `on_candle` 组装 `kind="factor_score"` 解释（含因子归因断面）。 |
| `services/quant/app/live_auto/explain.py` | 新建 | 纯构造器：`build_atomic_explain`/`build_risk_exit_explain`/`build_reverse_explain`/`build_session_stop_explain`/`build_external_close_explain`，统一 `TriggerExplain` 形状 + 标签文案；含「不含凭证」断言。 |
| `services/quant/app/live_auto/executor.py` | 修改 | `_make_trade` 接 `explain/cl_ord_id/ord_id`（kw-only）；`_project_signals`+`apply` 透传原始 `Signal`；`_do_open` 捕获现被丢弃的 `_place_order` 回执；平仓解释统一在 **`_settle_close`** 挂载（覆盖 signal_close/reverse/max_hold + flatten 的 session_stop）；**（v2）`_settle_benign_close`/`_on_reconcile_ok` 新增合成 `external_close` 平仓 trade**（仅记录，不下单）；seeded trade 回落 atomic 解释。 |
| `services/quant/app/live_auto/session.py` | 修改 | 新抽 `_dispatch(events)` 注入 `explain.meta` 并 `_persist_trade`；**当前三处内联 `_push`（`_on_confirmed`/`_monitor_tick`/`_handle_flatten_result`）统一改走 `_dispatch`**（best-effort 落库审计）。 |
| `services/quant/app/db/models.py` | 修改 | `Trade` 增 `explain_json: Mapped[str | None]`（Text，nullable）。 |
| `services/quant/app/db/session.py` | 修改 | `_ensure_trade_risk_columns` 的 additive 迁移集合加 `explain_json: TEXT`（既有 SQLite 幂等补列）。 |
| `services/quant/app/db/repositories.py` | 修改 | 新增 `append_live_trade(session, run_id, trade: dict)`：把 live-auto trade dict（含 `explain`）落 `Trade` 行 + `explain_json`；**镜像既有 `append_trade` 的自带 `await session.commit()` 语义**（best-effort 持久）。 |
| `services/quant/app/schemas.py` | 修改 | `trade_out` 新增 `explain` 键（`explain_json` → dict；缺列 / 坏 JSON 安全回 `None`，**键恒在**与既有恒含全键风格一致）；输出 camelCase。 |
| `apps/web/lib/types.ts` | 修改 | 新增 `TriggerExplain`/`FactorContribution`/`TriggerMetric` 接口；`Trade` 增 `explain?: TriggerExplain`、`clOrdId?`、`ordId?`。 |
| `apps/web/lib/tradeExplain.ts` | 新建 | 纯件：kind/side 标签与色调、因子贡献条几何（归一化宽度、diverging 零轴）、top-driver 选取、legacy `reason` 回落解释。 |
| `apps/web/components/Strategy/AutoTradeTradesTable.tsx` | 新建 | 自动交易「成交记录」表：逐行可展开（按钮 + `aria-expanded`），空 / 加载态，历史预热角标复用。 |
| `apps/web/components/Strategy/TradeExplainDetail.tsx` | 新建 | 展开详情：一句话原因 + 指标对照网格 + 风控快照 + 条件渲染因子归因图。 |
| `apps/web/components/Strategy/FactorContributionBars.tsx` | 新建 | 因子贡献 diverging 水平条形图（SVG / 纯 div，按 |贡献| 排序、多空配色、阈值标线）。 |
| `apps/web/components/Strategy/StrategyPanel.tsx` | 修改 | 在自动交易区（`mode==="paper"`，紧随既有 `<AutoTradeEventLog/>`）挂载 `<AutoTradeTradesTable>`。**仅加最小挂载（≤ 数行）**——此文件已 845 行（>800 软上限），表 / 详情 / 因子图逻辑必须独立成件，不得内联。 |
| `apps/web/components/Strategy/TradesTable.tsx` | 修改（P2，可选） | 复用 `TradeExplainDetail` 让回测 / 模拟成交表也可展开（仅当 trade 含 explain）。 |
| `services/quant/tests/test_factor_score_breakdown.py` | 新建 | `score_breakdown` 与 `compose_score` 数值一致；逐成员贡献符号 / 量纲正确；因果（无未来泄漏）。 |
| `services/quant/tests/test_live_auto_explain.py` | 新建 | 执行器 / 会话为 open/close/max_hold/reverse/session_stop/external 各分支挂 explain；落库经 `trade_out` 往返；解释不含凭证。 |
| `apps/web/lib/tradeExplain.test.ts` | 新建 | 条形几何、top-driver、legacy 回落、标签映射纯函数测试。 |
| `apps/web/components/Strategy/AutoTradeTradesTable.test.tsx` | 新建 | 展开 / 收起切换、因子行渲染、空态（`NODE_ENV=test`）。 |

> 不改：`api/live_auto.py`、`api/runs.py`（`/runs/{id}/trades` 经 `trade_out` 自动带上 explain，无需改路由）、`okx/*`、下单护栏与 `live_order_audit` 链路。

---

## 四、接口设计（Interface Design）

本期**不新增** REST / WS 端点，全部为**向后兼容的字段增量**。

### 4.1 WebSocket `/ws/live-auto/{session_id}`（既有，载荷扩展）

`trade` 事件与 `snapshot.trades[]` 的元素由 `Trade` 扩展为携带 `explain`：

```jsonc
// {"type":"trade","data": Trade}
{
  "ts": 1751200000, "side": "long", "price": 64210.5, "qty": 0.03,
  "fee": 0.0, "pnl": 0.0, "positionAfter": 0.03,
  "reason": "复合得分多头",
  "stopLossPrice": 62884.3, "takeProfitPrice": 66800.0,
  "clOrdId": "aragonAUTO0001", "ordId": "1234567890",
  "explain": { /* TriggerExplain，见 §五 */ }
}
```

### 4.2 REST `GET /api/py/runs/{run_id}/trades`（既有，元素扩展）

返回数组的每个 `trade_out` 元素新增可空字段 `explain`（历史 run 无该列时为 `null`，前端回落 legacy `reason`）。前端在刷新 / 重连后据 `run.mode === "live-auto"` 的 run 拉取并填充 `AutoTradeTradesTable`。

### 4.3 前端纯件签名（`apps/web/lib/tradeExplain.ts`）

```ts
export function explainKindLabel(kind: TriggerExplain["kind"]): string;          // 中文类别名
export function explainKindTone(kind): "long" | "short" | "neutral" | "warn";    // 角标色调
export function resolveExplain(trade: Trade): TriggerExplain;                     // 无 explain 时由 reason 合成回落
export interface ContribBar { name: string; pct: number; signedPct: number; tone: "long"|"short"; isTopDriver: boolean }
export function contributionBars(factors: FactorContribution[]): ContribBar[];   // 排序 + 归一化宽度（pct∈[0,100]）
```

`contributionBars`：按 `|contribution|` 降序，`pct = |contribution| / max|contribution| * 100`，`signedPct` 带符号供 diverging 布局；`isTopDriver` 取前 3 且 `share ≥ 0.15`。纯函数、可单测、不触 DOM。

---

## 五、数据模型（Data Model）

### 5.1 DB：`trades` 增列

| 列 | 类型 | 约束 | 说明 |
|---|---|---|---|
| `explain_json` | `TEXT` | nullable | 序列化的 `TriggerExplain`（JSON）；历史行为 `NULL`。绝不含凭证。 |

- 迁移：`db/session.py::_ensure_trade_risk_columns` 的列集合加 `"explain_json": "TEXT"`，沿用既有 `PRAGMA table_info(trades)` → `ALTER TABLE … ADD COLUMN` 幂等补列（与 `stop_loss_price` 等同机制，老库零手工迁移）。
- `reason: String(64)` 不变：仍存 headline 短文本（图表 tooltip / 列表主行）。详情走 `explain_json`，不受 64 字限制。

### 5.2 `TriggerExplain` 形状（wire camelCase / DB JSON 同构）

```ts
interface TriggerExplain {
  schema: 1;                       // 版本号；前端按版本兜底，未来可演进
  kind: "factor_score" | "atomic" | "risk_exit" | "reverse" | "session_stop" | "external_close";
  side: "long" | "short" | "close";
  headline: string;                // 一句话触发原因（= trade.reason 的本地化版本）
  summary?: string;                // 补充说明（中文，可空）
  metrics: TriggerMetric[];        // 通用键值对照（score/threshold/持仓根数/会话盈亏…）
  composite?: {                    // 仅 factor_score：复合得分与阈值
    score: number; preSquash: number; squash: boolean;
    enterLong?: number; enterShort?: number; exitBand?: number;
  };
  factors?: FactorContribution[];  // 仅 factor_score：逐成员归因（决策那一刻断面）
  risk?: {                         // 风控快照（开仓挂的交易所侧 OCO）
    stopLossPrice?: number | null; takeProfitPrice?: number | null;
    stopLossPct?: number | null; takeProfitPct?: number | null; maxHoldBars?: number | null;
  };
  meta?: {                         // 会话注入：可追溯 + 链回 OKX
    strategyName?: string; strategyKind?: string; instId?: string; bar?: string;
    barTs?: number;
    clOrdId?: string; ordId?: string;  // best-effort：开仓取 _place_order 回执、平仓取 result.clOrdId；可缺失
  };
}

interface TriggerMetric { label: string; value: string; tone?: "long"|"short"|"neutral"|"warn"; hint?: string }

interface FactorContribution {
  factorKey: string; name: string; direction: "long" | "short";
  value: number;         // 方向化原始值 dir·raw
  zscore: number;        // 因果滚动 z（与训练 / 打分同窗口、同 clip）
  weight: number;
  contribution: number;  // weight * zscore（pre-squash 贡献）
  share: number;         // |contribution| / Σ|contribution| ∈ [0,1]
  isTopDriver: boolean;  // 主要驱动因子（前 3 且 share≥0.15）
}
```

各 `kind` 的 `metrics` 约定（实现据此填）：
- `factor_score`：复合得分、进多 / 进空阈值、退出带、仓位强度。
- `risk_exit`（max_hold）：持仓根数、上限根数。
- `reverse`：原方向 → 新方向、触发反手的信号原因。
- `session_stop`（会话止盈 / 止损）：会话累计盈亏、阈值、本笔为「整仓平」。
- `external_close`：来源（交易所侧 OCO / 外部干预），本系统据 reconcile 校正。

---

## 六、测试与验收标准（Testing & Acceptance Criteria）

### 6.1 后端单测（pytest）

- `test_factor_score_breakdown.py`
  - `score_breakdown(...).score` 与 `compose_score(...)` **逐位相等**（同 df/members/weights/window/clip/squash）。
  - `Σ member.contribution`（pre-squash）== `composite`；squash 后 == `score`。
  - 单成员方向翻转（long↔short）使其 `value/contribution` 变号；权重 0 的成员不计入。
  - 因果性：在 `i` 处的 `zscore` 不因 `df[i+1:]` 改变（截断重算一致）。
- `test_live_auto_explain.py`（复用 `test_live_auto_*` 桩，注入假 place/close/positions 回调）
  - 开多 / 开空 trade 的 `explain.kind=="factor_score"` 且 `factors` 非空、`side` 一致。
  - 信号平仓 / `max_hold` / 反手（均经 `_do_close`→`_settle_close`）/ 会话止损（经 `flatten`→`_settle_flatten_ok`→`_settle_close`，trade 在返回 dict 的 `trade` 字段）各得对应 `kind` 与 metrics。
  - **external_close（v2）**：`_settle_benign_close`（注入 `LIVE_NO_POSITION`）与 `_on_reconcile_ok`（注入持仓已归零）**各产生且仅产生一条 `kind="external_close"` 成交**，且**断言期间未调用 place/close 桩**（不下单）；该 trade `explain` 完整、`side=="close"`。
  - `repo.append_live_trade` 落库（含 commit）后，**另开 session** 经 `trade_out` 读回 `explain` 结构无损；`explain_json` 损坏 / 缺列时 `trade_out` 的 `explain` 安全为 `None`。
  - `explain` 任何字段都不含 `api_key/secret/passphrase`（断言子串不出现）。
  - 解释构建 / 落库抛异常时回落「仅 reason」/ 原 status，下单 / trade 仍正常产生（异常隔离）；clOrdId/ordId 缺失时 trade/explain 仍正常生成（best-effort）。

### 6.2 前端测试（vitest，`NODE_ENV=test`）

- `tradeExplain.test.ts`：`contributionBars` 排序 / 归一化（最大者 pct=100）、`isTopDriver` 选取、`resolveExplain` 对无 explain 的 legacy trade 合成回落、标签映射稳定。
- `AutoTradeTradesTable.test.tsx`：行默认收起；点击展开后 `aria-expanded=true` 且渲染 `TradeExplainDetail`；factor_score trade 渲染因子行；空态文案；jest-dom 未全局 setup → 断言走原生 DOM（`btn.disabled`、`getAttribute`）。

### 6.3 验收清单（Definition of Done 对照）

1. 实盘自动交易每笔成交（开 / 平 / 时间止盈 / 反手 / 会话止损 / 外部平仓）都带结构化 `explain`，并经 `append_live_trade`（自带 commit）持久化到 `trades.explain_json`。**其中外部 / OCO 平仓为 v2 新增的合成成交行**（此前仅有 status 事件、无成交行）——验收须确认这两条分支确实新增了 `kind="external_close"` 的成交记录且不触发任何真实下单 / 平仓调用。
2. 前端自动交易区出现「成交记录」表，**每一笔可展开**查看触发原因。
3. `factor_score` 策略展开后显示**因子归因图**（逐成员方向 / 值 / z / 权重 / 贡献 + 复合得分 vs 阈值）。
4. 刷新 / 重连后历史成交可经 `GET /api/py/runs/{id}/trades`（FastAPI 侧 `/api/runs/...`）复盘（含 explain）；须验证 `append_live_trade` 已 commit（非仅 flush），否则刷新后丢失。
5. 既有下单护栏 / `live_order_audit` / WS 协议 / 回测路径行为不变；老 DB 自动补列。external_close 仅新增**成交记录**，不新增任何下单 / 平仓 API 调用。
6. 全量 `pytest`（services/quant）与 `vitest`（apps/web）通过；新测覆盖正常路径 + 异常路径。

---

## 七、风险与缓解（Risks & Mitigations）

| # | 风险 | 影响 | 缓解 |
|---|---|---|---|
| R1 | 解释构建异常拖垮真实下单 / 平仓（真金白银路径） | 高 | 解释为纯函数 + 全程 `try/except` 隔离；失败回落「仅 reason」，**绝不**进入 `place/close` 调用栈；落库 best-effort（失败仅 `logger.warning`）。 |
| R2 | 「解释一套、决策另一套」双口径，归因与实际打分不一致 | 高 | `compose_score` 改为 `score_breakdown` 薄封装，**逐位等价**；单测钉死一致性。 |
| R3 | 老 SQLite 库缺 `explain_json` 列导致写 / 读异常 | 中 | 复用 `_ensure_trade_risk_columns` 的 `ALTER TABLE ADD COLUMN` 幂等补列；`trade_out` 对缺列 / 坏 JSON 回 `None`。 |
| R4 | 解释泄露凭证 / 敏感信息 | 高 | `explain` 只含行情 / 因子数值与公开元信息；`explain.py` 收口构造 + 单测断言无凭证子串；沿用 C7 脱敏纪律。 |
| R5 | 因子归因引入未来泄漏（解释期重算方向 / 换窗口） | 高 | 复用冻结方向的 `directional_values` 与训练 / 打分同窗口 `causal_zscore`；`score_breakdown` 不接受 `auto` 方向；因果单测。 |
| R6 | 持久化每笔 trade 增加实盘热路径 DB 写 | 中 | 短生命周期 session、单行 INSERT、best-effort 异步落库；与既有 `live_order_audit` 同量级，不阻塞撮合。 |
| R7 | 原子策略 / seeded（持仓初始化、外部种子）trade 无因子归因导致展开「空洞」 | 低 | `explain.py` 通用构造器据 `reason` + 断面给「类别 + 一句话 + 风控 + 关键指标」；seeded trade 同样回落 atomic 解释，展开始终有内容；factor 区块条件渲染。 |
| R8 | WS / DB 载荷因 explain 膨胀 | 低 | 解释体量由成员数（≤ ~12）界定，单笔 JSON 极小；`metrics/factors` 不存原始 K 线序列。 |
| R9 | 前端图表 marker / 事件流与新表重复呈现 | 低 | 新表是**审计明细**（可展开），markers / `AutoTradeEventLog` 是**实时概览**，职责区分；复用既有 `liveAutoTrades` store，单一数据源。 |
| R10 | 枚举 `kind` 未来漂移致前端空白 | 低 | `explainKindLabel` 对未知 kind 回落「自动交易」，`schema` 版本号兜底，杜绝空白。 |
| R11（v2） | external_close 新增合成成交行改变既有行为：OCO / 外部平仓此前只推 status，现多出一条成交记录，可能与图表 marker / 既有统计口径产生重复或差异 | 中 | 合成 trade 明确 `kind="external_close"` 且 `seeded`-类语义；**仅记录已发生的交易所侧平仓、绝不调用任何下单 / 平仓接口**；落库与扇出全程 best-effort + `try/except`，失败回落原 status；新测覆盖「该分支产生且仅产生一条 external_close 成交、不触发下单调用」。 |
| R12（v2） | best-effort 落库若只 flush 不 commit，则刷新后历史成交丢失，违背「可持久审计」目标 | 中 | `append_live_trade` 镜像既有 `append_trade` 的「自带 `await session.commit()`」；新测覆盖「`append_live_trade` 后另开 session 经 `trade_out` 读回」。 |

---

## 八、实现注意事项（给下游工程师）

- 遵循 CLAUDE.md 阈值：新文件 < 800 行、方法 < 50 行、嵌套 ≤ 4、布尔条件 ≤ 3；`explain.py` 与 `tradeExplain.ts` 拆小纯函数。
- 命名：解释相关统一用 `explain` / `triggerExplain` / `contribution`，避免 `data/info/temp`。
- 注释只写「为什么」（如「为何 compose_score 必须委托 breakdown」「为何持久化 best-effort」）。
- 错误显式传播：边界（API / 落库 / OKX）一次校验；解释构建失败显式回落而非吞掉语义。
- 前端配色复用既有 token：多头 `var(--long)`、空头 `var(--short)`、中性 `var(--muted)`、强调 `var(--accent)`；不新增全局 CSS 变量。
- 交互（HCI）：表行整行可点 + 右侧 chevron 指示 + 键盘可达（`button`/`role`/`aria-expanded`）；展开用高度过渡，避免布局跳动；因子条用 diverging 零轴对齐，多空分色，top-driver 加粗 / 高亮，弱贡献淡显，符合「先概览后细节」的渐进披露。

---

## 九、评审结论（Review Verdict）

**结论：有条件通过（Approved with Conditions）。**

方案在四个维度上整体扎实：**可行性**——核心断言（`compose_score` 可抽 `score_breakdown` 单一真源、`Signal` 为可扩展的 Pydantic 模型、`Trade`/迁移/`trade_out`/`GET …/trades` 路由齐备、前端 `liveAutoTrades` store + `appendLiveAutoTrade` + `StrategyPanel` 自动交易区 + `/api/py` 代理 + CSS token 均已就位）经逐一核对**属实**；**一致性**——严格沿用「单一因果归一化真源、不绕过下单护栏、诚实降级、不泄露凭证」既有纪律，且复用既有 token / 测试范式；**适度性**——纯字段增量、无新端点、P2 复用项正确地标为可选，未过度设计。原 v1 的两个 P1（平仓 / 外部平仓解释挂载点不准确、clOrdId/ordId 采集落空风险）与六个 P2 已在 v2 就地修复或明确处置，无遗留 P0 / P1。

放行需满足以下落地条件（对应 v2 已写入正文的要求，开发 Subtask #2 须逐条遵守）：

1. **平仓解释统一在 `_settle_close` 挂载**（覆盖 signal_close / reverse / max_hold 及经 flatten 的 session_stop），不得只改 `_do_close`；session 侧经 `_handle_flatten_result` 从 flatten 返回 dict 取 `trade`。
2. **external_close 为有意的新增合成成交行**：仅在 `_settle_benign_close` / `_on_reconcile_ok` 记录已发生的交易所侧平仓，**严禁**调用任何下单 / 平仓接口；全程 best-effort + 异常隔离，失败回落原 status。须有测试钉死「产生且仅产生一条 external_close 成交、且不触发下单桩」。
3. **clOrdId / ordId 为 best-effort 可选**：开仓须捕获现被丢弃的 `_place_order` 回执，任一缺失安全留空，绝不阻塞 trade / explain / 下单。
4. **`append_live_trade` 自带 `commit`**（镜像 `append_trade`），并有「另开 session 经 `trade_out` 读回」的往返测试，确保刷新后可复盘。
5. **`compose_score` 严格委托 `score_breakdown` 且逐位等价**，单测钉死一致性与因果性（无未来泄漏），杜绝双口径。
6. **新增前端组件独立成件**，`StrategyPanel.tsx`（已 845 行 >800 软上限）仅加最小挂载；新 `.tsx` / `.ts` 各自守住 800 / 50 / 4 / 3 阈值。
7. 全量 `pytest`（services/quant）与 `vitest`（apps/web，`NODE_ENV=test`、原生 DOM 断言）通过，新测覆盖正常 + 异常路径。

满足上述条件即视为达成 Definition of Done。

---

*本文档 v2：v1（仅设计）经 Subtask #1 逐节评审，对照真实源码核验全部事实声明，就地修复全部 P0 / P1 并处置 P2。实现代码由 Subtask #2 依本 v2 落地。*
