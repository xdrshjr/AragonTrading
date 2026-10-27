# AI 自动交易：从「量得出偏差」到「关得掉亏损」—— 已实现 edge 闭环与按标的节流器

> 版本：**v1**（设计节点产出，待评审）
> 特性目录：`docs/plans/ai-trade-edge-closed-loop-governor/`
> 所属循环：loop `tge-b211370f` 第 1 轮（设计）
> 需求原文：「这个项目，是一个顶级量化 Agent 项目……当前阶段【AI 自动交易】功能，排查其中的 BUG 和不合理的地方，修复完善，确保其稳健可靠顶级，可以看看桌面端的日志，看看之前的交易，看看一直亏损的原因，并查阅代码，诊断可能的 BUG，并进行修复。」
> 勘察范围：用户本机生产库 `%APPDATA%/aragon-desktop/aragon.db`（211 个 `ai-trade` run / 519 条成交腿 / 256 笔已实现平仓）、桌面端 `logs/backend.log` 最近 60 MB（508,368 行）、`services/quant/app/ai_auto/` 全部 88 个模块与前端 `apps/web/components/Agent/` 全部 59 个组件。
> 本文所有数字都可用 §附录 A 的脚本在同一份生产库上逐位复算。

---

## 零、先读这一段：本轮的结论

**一句话结论：本系统的入场信号在毛口径上几乎没有 edge（每笔 −0.04 R），而每笔往返要付 7–10 bps 的真实成本；一个零 edge 的系统叠加正成本，必然稳定亏损。而系统里唯一有资格阻止这件事的那道门禁（最小 edge 门禁），它的分子从上线那天起就没有接过任何数据源，因此一次都没有开火。**

三条支撑：

1. **入场没有 edge（第一根因）。** 把每一笔 AI 开仓按它**自己当时挂出的止损/止盈**向前推演真实 K 线：168 笔可评估的开仓里 21 笔先触止损、8 笔先触止盈、139 笔在 96 根内两边都没碰到；已决样本的止盈命中率 **8/29 = 27.6%**，而 `plannedRr ≈ 2.0` 的盈亏平衡命中率约 **33%**。全样本 hold-to-plan 的 R 合计 **−6.86**、均值 **−0.041**。
2. **成本把「几乎为零」变成「稳定为负」（第二根因）。** 全量 256 笔往返 Σ 盈亏 **−52.12 USDT**、Σ 腿费 **25.44 USDT**。最近 6 个真实会话里，手续费占该会话亏损的 **7%–100%**；其中两个会话（`ca5eb6a6`、`0db79288`）的毛利被手续费**完全吃光**。换手率高达 **13–17 腿/日**，`c0a9f47b` 会话在 79 小时内于 100 USDT 预算上烧掉 **10.20 USDT ≈ 本金的 10.2%** 的纯手续费。
3. **门禁在结构上无法察觉前两条（第三根因，也是本轮真正要修的东西）。** 最小 edge 门禁的分子是 `min(1.0 × ATR, OCO 止盈距离)` —— 一个「若走对能吃到多少」的**幅度**，不是「概率加权后能赚多少」的**期望**。实测该分子均值 **+59.6 bps**，同批往返的已实现毛额均值 **−14.7 bps**，**系统性高估约 74 bps**。最近一组会话的纪律回执里，`edgeGate` 装配阈值「毛 edge ≥ 1.5 × 成本」、`firedCount = **0**`。仓库里本来有一条能把这个分子换成真期望的通路（`signal_prior` 的 `prior_ev_bps`），但生产库里 `ai_prior:%` 的键有 **0 条**，三个总闸默认 `False`，四档纪律预设里**没有任何一档装配它们**。

### 两条必须写在最前面的反直觉结论

> **反直觉 ①：「AI 频繁提前平仓才是罪魁」——这个直觉是错的，本轮不按它做设计。**
> 对 24 笔有完整记录的 Agent 主动离场做反事实：实际 ΣR **−9.75**（均 −0.406），若按它自己的计划持有到止损/止盈则 ΣR **−9.02**（均 −0.376）——**按计划持有总共只多赢 0.73 R，而且只有 8/24（33%）的单笔情况下更好**。Agent 的离场相对它自己的计划近似 **EV 中性**。谁要是把本轮做成「锁住 Agent 不许平仓」，就会在不改变亏损的前提下引入一次「锁死止损」事故。
>
> **反直觉 ②：「利润保护层平仓胜率 85.7%，多让它平仓就会赚钱」——这是幸存者结构，不是因果。**
> 利润保护棘轮必须**先 arm**（浮盈达到装配阈）才可能触发，它在结构上只出现在赢的那一半样本里。`ExitSourceCard.tsx:28-30` 早已写明「`exitMix` **不参与任何判定**……把它做成门槛会诱导出『故意不主动平仓、等保护层』这种劣化行为」。**本轮沿用该结论**，不新增任何以退出来源占比为判据的门禁。

### 本轮做什么

把仓库里**已经存在但从未通电**的那条 EV 通路接上水源，并在它旁边补一个不依赖模型配合的确定性刹车：

- **轴 A｜给 EV 分子接上水源**：新增第三路先验来源 `realized` —— 用本账户**自己已结算的往返**按 `(instId, bar, side)` 聚合出 `p̂ / W̄ / L̄`，走 `signal_prior` 既有的同一套契约、同一条 `costBasis == "gross"` 铁律、同一条「`Σw == 0` ⇒ 整条目缺席」铁律，喂给 `session.py:2477` 那个今天恒为 `None` 的 `prior_ev_bps`。
- **轴 B｜按标的的确定性节流器（Edge Governor）**：当某个 `(instId, bar)` 的已实现净期望在足够样本上持续为负时，**确定性地**把该标的降为半仓，再持续为负则降为「只平不开」——三档、带滞回、带墙钟上限、纯函数、可逐字节回退。这是「一直亏损」的直接刹车，而且它**不需要模型同意**。
- **轴 C｜把亏损侧的换手摩擦补上**：`hold_horizon` 的迟滞闸今天对 `move <= 0` **一律放行**，于是「亏了就砍」这条路径不受任何成本约束——而它正是 30 笔、−96.22 USDT 的那条路径。本轴给它补一道**带完整豁免表**的对称闸：亏损幅度还不足一次往返成本、且浮亏还不到半个风险单位时，本轮不平；触及止损、行情陈旧、亏损解除阈、会话停止、保护层与交易所侧路径**全部豁免**。
- **轴 D｜默认装配与换手成本前置（HCI）**：四档纪律预设补上三个先验/节流键；启动弹窗在**下单之前**把「这个标的最近 N 笔的已实现期望」和「按当前唤醒频率×标的数，预计每日手续费 = 预算的百分之几」摆在用户面前；利润保护的「粘性关闭」补一行常显警示。
- **轴 E｜可见性与可靠性小修**：LLM 超时/失败今天只进日志（`summarize failed` 28 次、`decide attempt failed` 17 次），给它一个 `aiStats` 落点；桌面端前端端口回退到 18762 后**整个应用静默变只读**（236 条 `origin rejected`）。

---

## 一、勘察证据

> 口径：`mode = 'ai-trade'` 的全部 run；「往返」= `abs(trades.pnl) > 1e-12` 的平仓腿；bps 类读数取 `trades.explain_json.meta` 里由 `trip_ledger.settle` 落下的 17 键。复算脚本见 §附录 A。

### D-1｜全量战绩：胜率 41.0%、赔率 0.861，而盈亏平衡需要 1.44

| 读数 | 值 |
|---|---|
| 已实现平仓（往返） | **256** 笔 |
| Σ 盈亏 | **−52.115 USDT** |
| Σ 腿费（全部 519 腿） | **25.444 USDT** |
| 胜 / 负 | 105 / 151 ⇒ 胜率 **41.0%** |
| 均盈 / 均亏 | **+0.7411** / **−0.8605** ⇒ 赔率 **0.861** |
| Profit factor | **0.599** |
| 41% 胜率的盈亏平衡赔率 | **1.44** |

### D-2｜近 6 个真实会话：手续费吃掉 7%–100% 的亏损，换手 13–17 腿/日

| 会话 `group_id` | 起始 | 腿数 | 跨度 | 腿/日 | Σ 费 | Σ 盈亏 | 费 / \|亏\| |
|---|---|---|---|---|---|---|---|
| `0a743955…` | 08-14 | 2 | 11.4 h | 4.2 | 0.35 | −4.78 | 7% |
| `1573be73…` | 08-17 | 5 | 4.6 h | 26.3 | 1.20 | −5.72 | 21% |
| `fd49f82b…` | 08-18 | 27 | 173.7 h | 3.7 | 5.38 | −9.90 | **54%** |
| `c0a9f47b…` | 08-26 | 56 | 79.0 h | **17.0** | **10.20** | **−29.09** | 35% |
| `ca5eb6a6…` | 08-29 | 22 | 39.5 h | 13.4 | 2.37 | −0.01 | **≈100%**（毛利 +2.36 被费吃光） |
| `0db79288…` | 08-31 | 17 | 27.0 h | 15.1 | 1.73 | −1.73 | **100%** |

`c0a9f47b` 的总预算是 **100 USDT**（`ai_trade_group_states.start_wire_json.capitalUsd`），79 小时烧掉 10.20 USDT 手续费 = **本金的 10.2%，约 3.1%/日**。这不是「策略不好」，这是**换手频率本身在亏钱**。

### D-3｜估计 vs 实现：门禁分子系统性高估约 74 bps

60 笔带开仓时刻估计的往返上：

| 读数 | 均值 | 中位 | p10 | p90 |
|---|---|---|---|---|
| `estEdgeBps`（门禁分子） | **+59.59** | +49.02 | +29.39 | +108.95 |
| `realizedGrossBps`（已实现毛额） | **−14.66** | −20.21 | −103.79 | +86.12 |
| `estCostBps` | +11.04 | +10.49 | +8.81 | +14.60 |
| `realizedCostBps` | +7.89 | +7.02 | +6.95 | +10.00 |

成本模型是**保守且准确的**（估 11.0 / 实 7.9，方向安全）。问题**全部**在分子。

### D-4｜edge 门禁装配了、也没坏，但一次都没开火

最近一组会话（`0db79288`，`discipline.preset = balanced`）的 `resolved_echo_json.discipline.rows`：

| 纪律行 | armed | 阈值 | firedCount |
|---|---|---|---|
| `edgeGate` | ✔ | 毛 edge ≥ **1.5 ×** 成本 | **0** |
| `drawdownBrake` | ✔ | 减速 8%（×0.5）/ 停开 15% | **0** |
| `lossStreak` | ✔ | 连亏 4 笔 / 冷却 60 分钟 | **0** |
| `exposureCap` | ✔ | 2× 单标的满仓名义额 | **0** |
| `atrStop` | ✔ | 2×ATR / RR 2 | 52 |
| `volSizing` | ✔ | 年化 20% / λ=0.25 / 窗口 96 根 | 3 |
| `profitGuard` | **✘** | — | —（回执注解：「未装配 ⇒ 浮盈回吐不会触发保护性平仓」） |

代入 D-3：`1.5 × 11.04 = 16.6 bps < 59.59 bps`。**分子高估到这个程度，任何 `k_min ≤ 5` 的阈值都注定空转。**

### D-5｜EV 分子从来没有水源（本轮的核心机械缺陷）

| 事实 | 证据 |
|---|---|
| `estimate_gross_edge_bps` 的分子链是 `min(atr_target_mult × ATR, OCO 止盈距离, prior_ev)` | `edge_gate.py:78-140` |
| `prior_ev` 只在 `ai_trade_edge_prior_enabled` 为真时进 `min` 链 | `edge_gate.py:132-136`；`session.py:2477-2478` |
| 三个总闸默认 **False** | `config.py:589`（`signal_prior_enabled`）、`:590`（`weighted_consensus_enabled`）、`:593`（`edge_prior_enabled`） |
| 四档纪律预设 `steady/balanced/aggressive/off` **一档都没装配这三个键** | `discipline.py:47-131`（17 个键，无先验相关键）；与 D-4 回执逐项吻合 |
| 先验快照存 `app_settings`，键前缀 `ai_prior:` | `db/ai_prior_repositories.py:19-21` |
| **生产库里 `ai_prior:%` 的键有 0 条** | `select count(*) from app_settings where key like 'ai_prior:%'` ⇒ `0`（全库仅 9 个 `app_settings` 键，均为 workspace / experience / intel） |
| 快照只能由运维手动调重建端点产生 | `api/ai_auto.py:766-775`；`prior_service.rebuild_prior_snapshot` 需要真实 K 线 + 策略帧，实测未被调用过 |
| `edge_gate` 的文档自己承认分子是幅度不是期望 | `calibration.py:14-17`：「第 2 轮的分子是 `min(atr_target_mult × ATR, OCO 止盈距离)`，是『若走对能吃到多少』的**幅度**而非期望，故理论上应当系统性高于已实现毛额中位」 |
| `k_min` 出厂默认 **0.0**（门禁整体空转） | `config.py:557` |

### D-6｜反事实推演（决定性证据）

对每一笔带 `stop_loss_price` / `take_profit_price` 的 AI 开仓，取同 `(instId, bar)` 的 `candles_cache`，从**开仓根之后**逐根前推最多 96 根；同根同时触及止损与止盈时**保守判为先触止损**：

| 样本 | n | 止损 | 止盈 | 96 根内未决 | ΣR | 均 R | 中位 R |
|---|---|---|---|---|---|---|---|
| 全部可评估开仓 | **168**（另 93 笔无缓存 K 线） | 21 | 8 | 139 | **−6.86** | **−0.041** | −0.073 |
| 已决子样本的止盈命中率 | 29 | — | **8 / 29 = 27.6%** | — | — | — | — |
| RR≈2.0 的盈亏平衡命中率 | — | — | **≈33.3%** | — | — | — | — |

只看 Agent 主动离场的 24 笔（有 `rMultiple` 记录）：

| | ΣR | 均 R | 中位 R | 胜率 |
|---|---|---|---|---|
| 实际（Agent 决定何时平） | **−9.75** | −0.406 | −0.469 | 21% |
| 反事实（按自己的计划持有） | **−9.02** | −0.376 | **−1.000** | 21% |
| 「按计划持有更好」的笔数 | **8 / 24 = 33%** | | | |

⇒ **入场无 edge 是主因；Agent 的离场相对它自己的计划近似 EV 中性。**

### D-7｜退出来源分层（63 笔带完整 meta 的往返，仅作诊断、不作判据）

| `exitSource` | n | 胜率 | 均 netBps | Σ USDT |
|---|---|---|---|---|
| `agent` | 32 | 25.0% | −47.6 | −68.90 |
| `agentOverride` | 7 | 14.3% | −28.7 | −10.01 |
| `external` | 15 | 60.0% | +39.5 | +22.67 |
| `profitGuard` | 7 | 85.7% | +40.5 | +14.14 |
| `exchangeOco` | 1 | 0% | −95.6 | −4.78 |
| `sessionStop` | 1 | 100% | +6.0 | +0.30 |

**读法见 §零 反直觉 ②：`profitGuard` 的胜率是幸存者结构，不构成因果。** 本表只用于确认「亏损集中在 Agent 主动通路」，不用于任何门禁判据。

### D-8｜R 从来走不满：`plannedRr = 2.0` 是结构上不可能兑现的承诺

- `attribution.realized_stats` 的 `reachedOneRShare`（`abs(R) ≥ 1` 占比，UI 标签「触及 ±1R 占比」，`attribution.py:166` / `ProfitabilityCard.tsx:76`）在生产上为 **0**。
- Agent 主动离场 39 笔：**R ≥ 1 的有 0 笔**；中位 R **−0.45**；30 笔亏损在触及止损前被砍（均 **−0.56 R**，合计 −96.22 USDT），9 笔盈利在到达目标前被兑现（均 **+0.21 R**，合计 +17.31 USDT）。
- `plannedRr` 中位 **2.00**（`stopAtrMult` 中位 ≈ 2.0）。

⇒ 2×ATR 的止损距离与系统实际持有的位移（`|realizedGrossBps|` 中位 20–45 bps）**量纲不匹配**。真正决定盈亏的是「20–45 bps 的位移 vs 7–10 bps 的成本」，R 只是一个从没被用到的单位。这解释了为什么以 R 为口径的一切读数都在生产上「看起来正常」却毫无约束力。

### D-9｜亏损侧换手零摩擦（可直接落地的代码级缺陷）

`hold_horizon._first_gate_reason` 的迟滞闸（`hold_horizon.py:697-703`）：

```python
if GATE_HYSTERESIS not in released:
    move = snapshot.get("moveBps")
    required = snapshot.get("requiredMoveBps")
    # `move <= 0` 一律放行：止损、割肉、认错离场永远不受迟滞约束（D-4）。
    if move is not None and required is not None and 0 < float(move) < float(required):
        return f"{REASON_HYSTERESIS}(move=…,req=…)"
```

判据 `0 < move < required` 使整条闸**只约束盈利侧的小额换手**。亏损侧（`move <= 0`）——也就是 D-8 里那 30 笔、−96.22 USDT 的路径——**完全不受成本约束**。原注释的理由（不能挡止损）成立，但它把「不能挡真止损」实现成了「不挡任何亏损平仓」，这两者之间还有一大片本该收费的区域。

### D-10｜离场优先级 override 的准入是一次字符串长度检查

`exit_priority._evidence_ok`（`exit_priority.py:177-181`）：

```python
def _evidence_ok(evidence: str | None, min_chars: int) -> bool:
    text = str(evidence or "").strip()
    return len(text) >= max(1, int(min_chars or 0))
```

默认 `evidence_min_chars = 10`（`exit_priority.py:69`、`config.py:441-447`）。实测 Agent 平仓 `headline` **平均 147 字符、最长 306 字符** —— 这道门在实践中**恒开**。该通路实测 7 笔、胜率 **14.3%**、均 −28.7 bps。四条降档判据里另外三条（`intent_streak ≥ 2`、会话配额 6、`hold ≥ 60 s`）都是真约束，只有「证据」这一条是形式化的。

### D-11｜偏差已经量出来了，但没有任何一条回流路径

| 已有能力 | 位置 | 消费者 | 是否影响交易 |
|---|---|---|---|
| `calibration.edge_numerator`（正是 D-3 的 `medianErrorBps`） | `calibration.py:37-55` | `track_record_service.py:17` | **否**（只读报表） |
| `attribution.realized_stats`（`reachedOneRShare` / `edgeErrorMedianBps`） | `attribution.py:151-176` | `aiStats` 卡片 | **否** |
| `track_record.build_track_record`（把胜率/赔率写进 prompt） | `track_record.py:31-60` | `prompts.py` | **仅作为证据**——该模块文案硬约束 ③ 明写「不得出现任何指令性措辞让模型『必须减少交易』——这一段是**证据**，不是门禁」 |
| `manager.track_records`（按 `instId` 键的战绩快照） | `manager.py:212-215` | payload 注入 | **否** |

⇒ **全链路没有任何一处让「已实现战绩」改变「下一笔能不能开、开多大」。** 上一轮（`ai-trade-realized-edge-calibration`，已合入 `b2672dc`）交付的是**测量**；本轮要交付的是**闭环**。

> 这条判断本身要谨慎：上一轮之所以刻意只做证据不做门禁，是因为本仓库有 **4 次**「恒真判据 ⇒ 永不开单」的事故前科（见该 spec 的 P1-9）。本轮的回答不是「那就把证据写得更凶」——**给 LLM 看证据不是控制回路**，实测中模型看到证据后依然照常开仓。本轮的回答是：加一条**不依赖模型配合的确定性节流**，并把「永不开单」的防线做成显式的验收项（§七 G-11 / G-12、§八 R-1）。

### D-12｜桌面端日志（最近 60 MB / 508,368 行）

| 现象 | 次数 | 今天的落点 |
|---|---|---|
| `app.ai_auto.decider: ai-trade summarize failed: TimeoutError(<n>s)` | **28** | 仅日志 |
| `app.ai_auto.decider: ai-trade decide attempt failed`（`LLM_NETWORK_ERROR` / `incomplete chunked read`） | **17** | 仅日志（有重试，但用户看不到「本会话已重试 N 次」） |
| `aragon.security: origin rejected: 'http://localhost:18762' on PUT /api/workspace/watchlist` / `ws origin rejected … /api/ws/agent/…` | **236** | 仅日志；**界面无任何提示** |
| `aragon.okx.ws: … disconnected (ConnectionResetError / ConnectionClosedError)` | 数百 | 已由 `feed_watchdog` / `feed_runtime` 自愈（上一轮 `30ff91b`），**本轮不动** |
| `experience.materialize_health = {slow: true, lastMs: 9035.4, events: 1}` | — | 单事件增量物化 9.0 秒，无告警 |

`origin rejected` 的机制：`DEFAULT_ORIGINS` 只含 `http://localhost:18761` / `http://127.0.0.1:18761`（`security/origins.py:22-25`），而 18761 被占用时 Next 会自增到 18762。此时**前端所有写请求与所有 WS 都被后端拒绝**，用户看到的是一个「打得开但什么都不生效」的界面——包括启动自动交易。

### D-13｜三条「查过，判定不是缺陷，不要改」的结论

写在这里是为了避免下游节点做无用功、或据此提交一次没有价值的改动：

1. **`strategy_runs.start_ts` 在 211/211 条 `ai-trade` run 上恒为 0** —— 看起来像漏写，实际口径完整：起点解析链是 `group_started_ts → min(start_ts) → 首笔成交 ts → 0`（`db/ai_auto_repositories.py:233-237`）。生产库里 184 条有 `group_started_ts`，其余 27 条是分组化之前的老 run，落到首笔成交 ts。**不改。**
2. **`reachedOneRShare` 用的是 `abs(v) >= 1.0`**，把 −1R 止损也计入 —— 看起来像符号缺陷，但 UI 标签是「触及 **±**1R 占比」（`ProfitabilityCard.tsx:76`），命名与口径自洽。**不改**；本轮另加一个只算盈利侧的读数（§3.4 `reachedOnePosRShare`），两者并排。
3. **`trades.reason` 声明为 `VARCHAR(64)`，实际存着几百字的中文决策理由**（SQLite 不强制长度）。已核对全部读取点：展示真源是 `explain_json.headline`，无一处依赖该长度或做截断。**不改**，仅在此登记。

---

## 二、根因与修法的对应关系

| # | 根因 | 证据 | 本轮修法 | 轴 |
|---|---|---|---|---|
| **RC-1** | 最小 edge 门禁的分子是**幅度**不是**期望**，且从未接过数据源 ⇒ 永不开火 | D-3 / D-4 / D-5 | 新增第三路先验来源 `realized`，用自己的已结算往返产出真 EV，喂进既有的 `prior_ev_bps` 注入点 | **A** |
| **RC-2** | 战绩已经量出来了，但没有任何一条回流到「能不能开、开多大」 | D-11 | 确定性三档节流器（normal / throttle / probation），不依赖模型配合 | **B** |
| **RC-3** | 亏损侧换手不受任何成本约束，13–17 腿/日 × 7–10 bps 稳定失血 | D-2 / D-9 | 迟滞闸对称化 + 完整豁免表 + fail-open | **C** |
| **RC-4** | 保护机制默认全关、预设也没装；换手成本在下单前完全不可见 | D-4 / D-5 | 预设补三键；启动弹窗前置「标的战绩条」与「每日成本预算」；`profitGuard` 粘性关闭警示 | **D** |
| **RC-5** | LLM 失败与端口回退失败只进日志，用户看到的是「静默地什么都没发生」 | D-12 | `aiStats` 失败计数落点；桌面端把实际前端端口写进后端 Origin 白名单 | **E** |

**明确不是根因（不要照它做设计）**：Agent 主动离场时机（D-6 反事实）、退出来源占比（D-7 幸存者结构）、行情断连（已由 `30ff91b` 自愈）、`start_ts = 0`（D-13）。

---

## 三、技术设计

### 3.0 总体架构与决策轮插入点

今天的决策轮门禁链（`session.py:1665-1697`，顺序冻结）：

```
decider.decide()                         ← LLM 原始输出（stage1_decision 在此留存）
  → note_intent(discipline / horizon)    ← 漏斗分母、连续确认计数
  → _resolve_exit_priority()             ← 离场优先级档位（解闸集合来源）
  → _apply_base_rule_gate()      :1671   ← MA 通道铁律
  → _apply_horizon_gate()        :1678   ← G1 最小持有 / G2 离场确认 / G3 迟滞
  → _apply_profit_cooldown_gate():1681
  → _horizon.turnover_gate()     :1683   ← G4/G5 换手预算
  → _horizon.backstop_gate()     :1686   ← G6 兜底覆盖
  → _sizing.gate()               :1688   ← 回撤刹车 / 连亏熔断（账户级）
  → _apply_consensus_gate()      :1689   ← E2 同向共识
  → _apply_edge_gate()           :1692   ← 最小 edge 门禁（本轮的分子来源在此）
  → _reflect_before_execute()    :1696   ← 下单前二次反思
  → 执行
```

本轮的两个新增插入点：

```
  → _sizing.gate()               :1688
  → ★ _apply_edge_governor()     新增   ← 轴 B：按 (instId,bar) 的已实现 edge 三档节流
  → _apply_consensus_gate()      :1689
  → _apply_edge_gate()           :1692   ← 轴 A：分子改由 realized 先验参与 min 链
```

轴 C 不新增调用点，只在 `hold_horizon._first_gate_reason` 内部把 G3 扩成对称判据。

**四条不可动摇的不变式（改任何一处前先读）**：

1. **只会更保守**：轴 A 的先验只经 `min` 进链（`edge_gate.py:130-136` 的 EV-1 单调保守），轴 B 只输出 `size_mult ≤ 1` 或降级为 `hold`，轴 C 只把 `close` 降级为 `hold`。**任何一轴都不得凭空产生一个原本不会发生的开仓或平仓。**
2. **总闸关 ⇒ 逐字节回退**：三轴各自一个总闸；关闭时相关函数返回**同一个** decision 对象（`is` 相等），不 `replace`、不发事件、不写 degrade（沿用 `edge_gate.py` 的 `k_min <= 0` 范式）。
3. **样本不足 ⇒ 权重严格为 0，并如实写明**：借用本仓库情报层先验的成文纪律 —— 缺样本时**绝不**产生一个假装有信息的常数，`payload` 整键缺席、UI 显式写「样本 8/20，先验未生效」。
4. **fail-open 的方向按层性质定**：轴 A / 轴 B（拦开仓）估算不可用 ⇒ **放行**（与 `edge_gate` 一致，理由同 `edge_gate.py:12-16`：ATR 暖机断面上 fail-closed 会复现「永不开单」事故）；轴 C（拦平仓）任一输入不可算 ⇒ **放行**，且豁免表优先于判据。

### 3.1 轴 A｜第三路先验来源 `realized`

#### 3.1.1 为什么是「接水源」而不是「造新管道」

`signal_prior.py` / `prior_builder.py` / `prior_runtime.py` 已经实现了完整的 `EV = p·W − (1−p)·L` 机械：条目模型、体制分桶、衰减加权、logit 聚合、输出夹取、`Σw == 0 ⇒ None`、`costBasis == "gross"` fail-closed。`edge_gate` 也已经留好了注入点。**唯一缺的是数据**：现有两路来源（`signal_edge` 事件研究、`strategy_replay` 策略回放）都只能由离线 rebuild 端点产生，而生产上从没人调用过（D-5）。

新增第三路 `source = "realized"`，它的独特之处是**在线可得**：每结算一笔往返就多一个样本，不需要 K 线回放、不需要运维介入、不需要线程池。

#### 3.1.2 事件同源律（本轴最容易做错的一条）

`signal_prior.py:8-11` 的第一条口径：「离线统计的条件事件 `A` 与在线取用该条目的条件 `A'` 必须是**同一个断言**。否则 `p / W / L` 不是估计得不准，而是**没有定义**——而它要去当真实资金开仓门禁的分子。」

`realized` 路的断言必须写死为：

> **A = 「本会话在 `(instId, bar)` 上、朝 `side` 方向、由 AI 主动开出一笔仓位」**
> **A' = 「本轮 `decision.action ∈ {open_long, open_short}`，且 `(instId, bar)` 与条目一致、方向与条目一致」**

由此推出四条**必须**的过滤（缺一条这路先验就是无定义的）：

1. **只取 AI 主动开出的往返**：`SettledTrip` 必须能追溯到 `explain.meta.source == "ai"` 的开仓腿。续跑接管的既有仓位、外部手动开仓一律剔除。
2. **方向分开**：`long` 与 `short` 是两个条目，绝不合并。
3. **`costBasis` 恒 `gross`**：`W̄ / L̄` 一律取 `SettledTrip.realized_gross_bps`，**绝不**取 `realized_net_bps`。成本在门禁的分母侧独立出现，取净额等于双重计费——`prior_builder.py:6-7` 已经把这条写成铁律（R-1，有「反思致永不开单」同类事故前科）。
4. **剔除退出来源为 `external` / `sessionStop` / `windowEnd` 的往返**：那三种退出不是这条信号的自然结果，把它们计入会把「用户手动干预」记成「信号的 edge」。`exchangeOco` / `profitGuard` / `agent` / `agentOverride` **保留**（它们都是本系统计划内的退出方式）。

#### 3.1.3 聚合与层级收缩

```
scope 三级（自上而下取第一个样本量达标的）：
  L0  (instId, bar, side)          ← 最贴切，样本最少
  L1  (instId, bar)                ← 方向合并；仅当 L0 不达标时使用，且 EV 取两方向的保守者
  L2  全账户 (bar, side)           ← 仅当 L1 不达标；权重按 §3.1.4 收缩
  ——三级都不达标 ⇒ 整条目缺席（返回 None），逐字回退到合入前行为
```

单条目的产出（复用 `SignalPriorEntry` 的字段语义，`avg_loss_bps` 存**正数**幅度）：

```
n            = 该 scope 的合格往返数
p̂            = wins / n                                （wins: realized_gross_bps > 0）
W̄            = mean(realized_gross_bps | > 0)          （bps，正）
L̄            = mean(|realized_gross_bps| | <= 0)       （bps，正）
ev_bps       = p̂ · W̄ − (1 − p̂) · L̄
```

三条与 `prior_builder` 逐字同构的保护：

- **零亏损样本专治**：亏损样本数 `< _MIN_LOSS_SAMPLES (= 5)` 时 `L̄` 不可信，`EV` 退化为 `p̂·W̄` 这个不可能的乐观分子 ⇒ **整条目缺席**（`prior_builder.py:32-33` 已有同款常量，直接复用）。
- **概率夹取**：`p̂` 先按 `_LOGIT_CLAMP (0.02, 0.98)` 夹再聚合，输出按 `_OUTPUT_CLAMP (0.05, 0.95)` 夹（`signal_prior.py:30-32`）。
- **`Σw == 0` ⇒ None**：不产生任何「假装有信息的常数」。

#### 3.1.4 小样本收缩（本轴的风险核心）

`n` 很小时 `p̂` 的噪声足以把一条正常信号判死。收缩规则（确定性、纯函数）：

```
w(n) = clamp((n − n_min) / (n_full − n_min), 0, 1)          n_min = ai_trade_realized_prior_min_trips  (默认 12)
                                                            n_full = ai_trade_realized_prior_full_trips (默认 40)
ev_effective = w(n) · ev_bps + (1 − w(n)) · ev_fallback
ev_fallback  = 上一级 scope 的 ev_effective；L2 之上无 fallback ⇒ 条目缺席
```

**硬约束**：`w(n) == 0`（即 `n < n_min`）时**本级不产生任何数值**，直接下沉到下一级；这与「样本不足时权重严格为 0 并如实写『样本 8/12，先验未生效』」是同一条纪律。`w` 严禁写成 `max(w, 0.1)` 这类「至少给一点权重」的形式——那正是把噪声当信号。

#### 3.1.5 在线更新与持久化

- **写点**：`AttributionLedger.settle_close`（`attribution.py:122-137`）返回 `SettledTrip` 之后，紧贴其后调用一次 `RealizedPriorRuntime.note_settled(trip)`。这是**唯一**写点（单点收口纪律，与 `exit_attribution` 的「单一收口打标」同构）。
- **会话内**：运行时持有一份增量聚合（纯内存计数器：`n / wins / Σwin_bps / Σloss_bps` 四个数按 scope 累加），决策轮**零 IO**。
- **跨会话**：会话启动时一次性从 `app_settings` 读 KV `ai_realized_edge:{instId}:{bar}`（**零迁移**，复用 `ai_prior` 的同款 KV 姿态，`db/ai_prior_repositories.py` 直接扩一组函数）；会话结束（`stop` / `pause` / `flatten` 三条出口）与每结算 `ai_trade_realized_prior_flush_trips`（默认 5）笔时各落盘一次。
- **落盘内容**：按 scope 的四元计数 + `schemaVersion` + `updatedTs` + `instId` / `bar`（键与内容同源，杜绝写错行——`ai_prior_repositories.py:30-40` 的成文教训）。**不落原始往返明细**，那已经在 `trades` 表里。

#### 3.1.6 接入 `edge_gate`

`session.py:2477` 今天是：

```python
prior_ev_bps=self._prior.last_edge_bps(decision.action),
prior_binding=bool(self.executor.settings.ai_trade_edge_prior_enabled))
```

改为（`PriorRuntime` 内部合并，`session.py` **净增 0 行**）：

```python
# PriorRuntime.last_edge_bps 内部：两路来源取保守者，缺席者不参与
#   offline = 既有 signal_edge / strategy_replay 聚合（今天恒 None）
#   realized = 本轮新增（RealizedPriorRuntime）
#   返回 min(可得者)；两者皆缺席 ⇒ None（逐字回退）
```

**为什么取 `min` 而不是加权平均**：`edge_gate.py:130-136` 的 EV-1 单调保守是全链路承诺 ——「先验只经 `min` 进链 ⇒ 分子只会更小、绝不放大 ⇒ 被否的轮次仍被否」。加权平均可能把一个悲观的 realized 读数**抬高**，凭空造出新仓位。

`prior_binding` 的总闸改为 `ai_trade_edge_prior_enabled or ai_trade_realized_prior_binding`（两个独立总闸，前者管离线路、后者管 realized 路），保证任一路可以单独回退。

### 3.2 轴 B｜Edge Governor（按标的的确定性节流器）

#### 3.2.1 为什么必须是确定性的、而不是又一段 prompt

D-11 已经说明：战绩早已进了 prompt（`track_record.py`），模型看到之后照常开仓。本仓库处理这类问题的成熟范式是**铁律层**（`base_rules` 的 MA 通道、`profit_guard` 的棘轮）——纯函数、与 LLM 无关、只会更保守。Governor 就是这一层在「已实现 edge」维度上的实例。

#### 3.2.2 三档状态机（纯函数，`edge_governor.py`）

```
输入（全部来自轴 A 的同一份聚合，不另建第二份统计）：
  n              该 scope 的合格往返数
  ev_bps         已实现净期望（bps，净口径：realized_net_bps，与轴 A 的毛口径刻意不同，见下）
  loss_streak    该 scope 连续亏损往返数
  level_since_s  当前档位已持续的墙钟秒数

档位（自上而下取第一条成立的）：
  probation   n >= probation_min_trips (默认 24) 且 ev_bps <= -probation_bps (默认 15)
              ⇒ 该 scope 本轮 open_* 硬降级为 hold（「只平不开」），close 完全不受影响
  throttle    n >= throttle_min_trips (默认 12) 且 ev_bps < 0
              ⇒ size_fraction 乘以 throttle_size_mult (默认 0.5)
  normal      其余（含样本不足）
```

**净口径 vs 毛口径**：轴 A 必须用毛口径（否则双重计费）；轴 B 必须用**净**口径（它回答的是「这个标的实际在赚钱还是亏钱」，成本本来就该算进去）。两个口径在同一份 `SettledTrip` 上并存，`realized_gross_bps` 与 `realized_net_bps` 是两个已存在的字段（`trip_ledger.py:225-245`），**不新增任何计算**。这一条必须写进代码注释，否则下一个维护者一定会把它们统一掉。

#### 3.2.3 滞回与解除（防「永久 probation」）

| 规则 | 值 | 理由 |
|---|---|---|
| `probation` 的**墙钟上限** | `ai_trade_governor_probation_max_s`，默认 **43200**（12 h） | 到期无条件回落 `throttle` 并留痕。**没有任何档位可以永久生效**——这是「永不开单」防线的第一道 |
| 从 `throttle` 回 `normal` | 需 `ev_bps >= +hysteresis_bps`（默认 5）**或** 该 scope 连续 3 笔往返为正 | 单纯的 `ev_bps >= 0` 会在阈值附近抖动 |
| 从 `probation` 回 `throttle` | 需 `ev_bps > -probation_bps + hysteresis_bps` **或** 墙钟到期 | 同上 |
| `throttle` 档**仍然开仓**（半仓） | — | 这是防线的第二道：`throttle` 不冻结样本增长，scope 有机会自证 |
| 档位切换必须留痕 | 一条 `edge_governor` 轨迹事件 + `aiStats.edgeGovernor` 一行 | 「为什么这个标的今天不开仓了」必须一眼可答 |

#### 3.2.4 与既有回撤刹车的关系（正交，不合并）

`drawdown_brake` / `_sizing.gate`（`session.py:1688`）的判据是**账户级权益回撤**与**连亏笔数**；Governor 的判据是**单个 `(instId, bar)` 的已实现净期望**。两者正交：D-4 显示回撤刹车在整个亏损期间 `firedCount = 0`——因为 −29 USDT / 100 USDT 预算的回撤确实没到 8%，但那个标的组合早就该停了。

**组合规则**：两个 `size_mult` 相乘，并施加一个下界 `ai_trade_governor_size_floor`（默认 0.25）——低于该下界直接判 `hold`，避免解出一个小于交易所最小下单量、注定被拒的仓位（这是本仓库 `discipline.f_floor` 已有的同款考虑，`discipline.py:33-35`）。

#### 3.2.5 payload 注入（只读，不含指令性措辞）

`payload["edgeGovernor"]` 只在档位非 `normal` 时出现，且**沿用 `track_record.py` 的四条文案硬约束**：数值由后端格式化好、**禁止 markdown 粗体**（`**` 在四个渲染点会显示成字面星号，本仓库已复发 3 次）、不得出现指令性措辞、每个数必带分母。

示例文案（`edge_governor_text.py`）：

> `SOL-USDT-SWAP 15m 多头方向：最近 18 笔往返的已实现净期望为 −22.4 bps（样本 18，口径为净额）。本轮该标的的开仓规模已按 0.50 倍处理。`

模型**可以**读到这句话，但它不能否决 Governor——门禁在纯函数里，不在 prompt 里。

### 3.3 轴 C｜迟滞闸的亏损侧对称化

#### 3.3.1 判据

`hold_horizon._first_gate_reason` 的 G3 分支改为（伪码，实际实现须保持早返回风格）：

```python
if GATE_HYSTERESIS not in released:
    move, required = snapshot.get("moveBps"), snapshot.get("requiredMoveBps")
    if move is not None and required is not None:
        if 0 < float(move) < float(required):
            return f"{REASON_HYSTERESIS}(move=…,req=…)"          # 既有：盈利侧小额换手
        # 新增：亏损侧小额换手（k_loss <= 0 ⇒ 本分支结构性空转，逐字节回退）
        if k_loss > 0 and float(move) < 0:
            req_loss = float(required) * k_loss
            live_r = snapshot.get("liveR")                        # 新增快照键，见 §3.3.3
            if (abs(float(move)) < req_loss
                    and live_r is not None
                    and float(live_r) > -float(loss_floor_r)):
                return f"{REASON_LOSS_CHURN}(loss=…bps,req=…bps,R=…)"
```

语义：**「亏得还不足一次往返成本的 k 倍、而且浮亏还没到半个风险单位，就不要急着砍。」**

#### 3.3.2 豁免表（优先级高于判据，冻结）

本分支**永不**在下列任一条件成立时生效——每一条都对应一次真实的止损/风控需求：

| 豁免 | 来源 | 理由 |
|---|---|---|
| `releasedGates` 已含 `GATE_HYSTERESIS` | 既有 | 与 G1/G2 同一条解闸链 |
| `release_reason_for` 命中 `RELEASE_LOSS`（浮亏达解除阈） | `hold_horizon.py:507-535` | 亏到解除阈必须允许随时离场（既有冻结口径） |
| `RELEASE_STALE`（行情陈旧 / 判定价不可信） | 同上 | 「算不出」绝不能变成「不许平」 |
| `live_r` 不可算（无止损价 / `ct_val` 未知 / 非有限） | 新增 | fail-open |
| `required`（往返成本）不可算 | 既有 | fail-open |
| `decision` 不是 Agent 主动 `close`（保护层 / 交易所 OCO / 会话停止 / 兜底自愈 / 外部平仓） | 既有豁免矩阵 `gated_kind` | 那些路径根本不经过本闸 |
| `exit_priority` 判定为 `override` 档 | 既有 `priority_gates` 并集 | 复核官已裁决，本闸不二次拦截 |

#### 3.3.3 新增快照键 `liveR`

`build_snapshot`（`hold_horizon.py:561-627`）新增一个键：

```
liveR = signed_move_px / |entry_px − stop_px|     （多头 mark−entry，空头 entry−mark；分母 <= 0 ⇒ None）
```

三个输入全部已在运行时可得：`entry_px` / `stop_px` 来自 `AttributionLedger._open`（`OpenSnapshot`，`trip_ledger.py:66-84`），`mark_px` 来自 `HorizonFacts`。**决策轮零新增 IO。** 该键同时供轴 B 的 `loss_streak` 与 UI 使用。

#### 3.3.4 总闸与默认值

- `ai_trade_horizon_loss_churn_k`：默认 **1.0**（亏损幅度须达一次往返成本才允许主动砍），`0` ⇒ 本分支结构性空转、逐字节回退。
- `ai_trade_horizon_loss_churn_floor_r`：默认 **0.5**（浮亏达半个风险单位后本闸完全放手）。

> **这是本轮唯一一条会拦下平仓的改动**，方向上比其它三轴危险。§七 G-13 / G-14 与 §八 R-5 为它单列了验收与回滚条件；§十 Q-2 把「默认值该不该是 0」列为开放问题交评审裁定。

### 3.4 轴 D｜默认装配与换手成本前置

| # | 改动 | 位置 | 说明 |
|---|---|---|---|
| **D-1** | 四档纪律预设各补三个键 | `discipline.py:47-131` | `steady`：`realized_prior_binding=True` / `governor_enabled=True` / `probation_bps=12`；`balanced`：`True / True / 15`；`aggressive`：`True / True / 25`；`off`：不出现（空 updates 即关闭）。**`edge_prior_enabled`（离线路）保持不动**——它需要运维手动重建快照，装配它等于承诺一个用户拿不到的东西 |
| **D-2** | 启动弹窗成员卡片新增「战绩条」 | `AiTradeLaunchModal.tsx` | 每个成员一行：`最近 N 笔已实现净期望 −22.4 bps（样本 18/24）· 本次将按 0.50 倍开仓`；样本不足时显示 `样本 6/12，先验未生效` 而**不是**隐藏 |
| **D-3** | 启动弹窗新增「每日成本预算」一行 | 同上 | 由 `bar` / `wakeIntervalMinutes` / 成员数 / `capitalUsd` / `leverage` / 当前费率**确定性算出**：`按当前设置，预计每日手续费约 X USDT ≈ 预算的 Y%`。D-2 实测 `c0a9f47b` 为 3.1%/日——这个数字在下单前应当是可见的 |
| **D-4** | `profitGuard` 粘性关闭警示 | 同上 | `defaultProfitGuard = aiTradeConfig?.profitGuard ?? 默认`（`AiTradeLaunchModal.tsx:206,280-281`）使一次关闭会**永久**沿用。关闭状态下常显一行小字 + 「恢复推荐值」按钮。**不改默认值、不自动打开**——那会违反「用户上次的选择」 |
| **D-5** | `DisciplineCard` 新增两行 | `discipline_view.py` + `DisciplineCard.tsx` | `realizedPrior`（装配状态 + 样本覆盖）与 `edgeGovernor`（当前档位 + 本会话触发次数） |
| **D-6** | `CalibrationCard` 新增「偏差是否已回流」状态 | `CalibrationCard.tsx` | 上一轮交付的「问一」卡片今天只说「分子高估了多少」，现在它能说「已按 realized 先验收紧 / 未回流（样本不足）」 |

### 3.5 轴 E｜可见性与可靠性

| # | 改动 | 位置 |
|---|---|---|
| **E-1** | `aiStats.llmHealth`：本会话 `decideRetries` / `decideFailures` / `summarizeTimeouts` / `lastErrorCode` 四个计数 | `decider.py` 已有全部日志点，只需把计数暴露出来；`session.py` 汇总进 `aiStats` |
| **E-2** | 桌面端把**实际**前端端口写进后端 Origin 白名单 | `desktop/src/main.js` 拉起后端时注入 `SECURITY_ALLOWED_ORIGINS=http://localhost:<webPort>,http://127.0.0.1:<webPort>`；`config.allowed_origin_list()` 已支持逗号分隔（`config.py:1361-1367`）。**不放宽 `DEFAULT_ORIGINS`**——那等于把边界让掉 |
| **E-3** | 经验图物化慢的告警 | `experience.materialize_health.slow` 已经落库（实测 `lastMs = 9035.4`），只是无人读；在 `aiStats` 里加一行 |

> E-2 严格说超出「AI 自动交易」的字面范围，但它的后果是「自动交易开不起来、且没有任何提示」。§十 Q-4 把「是否整轴移出」交评审裁定。

---

## 四、文件 / 模块变更计划

> 行数预算按 CLAUDE.md：单文件 ≤ 800、单方法 ≤ 50、形参 ≤ 5（超出一律改聚合形参对象）、圈复杂度 ≤ 10、单行 ≤ 80 列。
> `session.py` 当前 **3390 行**、`manager.py` **2114 行**，两者均已远超 800 行上限。**本轮不做重构**（§九 非目标），但对这两个文件的净增设硬预算：`session.py ≤ +26 行`、`manager.py ≤ +18 行`，超出即按仓库既有范式**下沉到 runtime 模块**（先例：`prior_service.py`、`cost_runtime.py`）。

### 4.1 后端新增

| 文件 | 预算 | 一句话意图 |
|---|---|---|
| `services/quant/app/ai_auto/realized_prior.py` | ≤ 280 行 | 纯核心：已结算往返 → 按 scope 的四元计数 → 三级层级收缩 → `SignalPriorEntry`（`source="realized"`） |
| `services/quant/app/ai_auto/realized_prior_runtime.py` | ≤ 220 行 | 会话级运行时：启动加载 KV、`note_settled` 增量、节流落盘、`last_edge_bps` 降级链（镜像 `prior_runtime.py` 范式） |
| `services/quant/app/ai_auto/edge_governor.py` | ≤ 240 行 | 纯核心：三档状态机 + 滞回 + 墙钟上限 + `size_mult` 求解 + 稳定原因码 |
| `services/quant/app/ai_auto/edge_governor_text.py` | ≤ 130 行 | 档位文案（payload / trace / UI 同一真源；禁止 `**`） |

### 4.2 后端修改

| 文件 | 净增 | 一句话意图 |
|---|---|---|
| `app/config.py` | +14 字段 | 轴 A 4 个、轴 B 7 个、轴 C 2 个、轴 E 1 个；全部带 `ge/le` 约束与中文注释 |
| `app/ai_auto/signal_prior.py` | +10 | `SOURCE_REALIZED` 常量；聚合白名单接纳第三路；`entry_to_wire` 的 `source` 枚举扩容 |
| `app/ai_auto/prior_runtime.py` | +26 | `last_edge_bps` 改为「两路取 `min`、缺席者不参与」；新增 `realized` 装配状态与降级码 |
| `app/ai_auto/attribution.py` | +18 | `settle_close` 之后转发 `note_settled`（唯一写点）；`realized_stats` 加 `reachedOnePosRShare` / `cutBeforeStopShare` |
| `app/ai_auto/hold_horizon.py` | +34 | G3 亏损侧分支 + `liveR` 快照键 + `REASON_LOSS_CHURN` 码 + 豁免表 |
| `app/ai_auto/hold_horizon_text.py` | +16 | 新码文案（与既有 `REASON_HYSTERESIS` 同款句式） |
| `app/ai_auto/session.py` | **≤ +26** | `_apply_edge_governor` 薄封装 + 一次 `note_settled` 转发 + `aiStats` 三个新块的汇总 |
| `app/ai_auto/manager.py` | **≤ +18** | `AiTradeGroupStartRequest` 加 `realized_snapshots` / `governor_states`（按 `instId` 键，与 `prior_snapshots` 同款姿态，`manager.py:211-215`） |
| `app/ai_auto/discipline.py` | +12 | 三档预设各补 3 键（`off` 不动） |
| `app/ai_auto/discipline_view.py` | +30 | 两个新行 `realizedPrior` / `edgeGovernor` |
| `app/ai_auto/open_funnel.py` | +14 | Governor 进开仓漏斗的分子/分母（否则「被 Governor 拦了几次」不可观测） |
| `app/ai_auto/trace.py` | +26 | `edge_governor` 轨迹段 builder |
| `app/db/ai_prior_repositories.py` | +34 | `ai_realized_edge:{instId}:{bar}` 的 load/save（**零新表、零迁移**，复用 `app_settings`） |
| `app/db/ai_auto_repositories.py` | +40 | 按 scope 聚合历史已结算往返（会话启动期一次性；复用既有 `_Trip` 复算，不另写一份口径） |
| `app/api/ai_auto.py` | +46 | 2 个端点（§五）；路由注册顺序须在 `/{group_id}` **之前**（`api/ai_auto.py:681`、`:699` 的成文教训） |
| `app/schemas.py` | +38 | 3 个 response_model **显式声明**新键——`response_model` 会静默吞掉未声明的 wire 键（`schemas.py:1369`、`:1428`、`:985` 的成文教训） |

### 4.3 前端

| 文件 | 一句话意图 |
|---|---|
| `apps/web/lib/aiTradeTypes.ts` | `AiTradeEdgeGovernor` / `AiTradeRealizedPrior` 类型 |
| `apps/web/lib/aiTradeAcceptance.ts` | 档位 → 标签 / 色板 / 排序（单一真源） |
| `apps/web/components/Agent/aiTradeStats/EdgeGovernorCard.tsx`（新） | 档位、依据、样本、滞回剩余时间、本会话触发次数 |
| `apps/web/components/Agent/aiTradeStats/CalibrationCard.tsx` | 「偏差是否已回流」状态行（轴 D-6） |
| `apps/web/components/Agent/aiTradeStats/DisciplineCard.tsx` | 两个新行（轴 D-5） |
| `apps/web/components/Agent/AiTradeLaunchModal.tsx` | 成员战绩条 + 每日成本预算行 + `profitGuard` 粘性关闭警示（轴 D-2/D-3/D-4） |
| `apps/web/components/Agent/AiTradeStatsPanel.tsx` | 挂载新卡；**必须挂在 `items.length === 0` 早返回之前**（`AiTradeStatsHistoryList.tsx:49-57` 已有同款踩坑先例） |
| `desktop/src/main.js` | 注入 `SECURITY_ALLOWED_ORIGINS`（轴 E-2） |

### 4.4 测试

| 文件 | 覆盖 |
|---|---|
| `services/quant/tests/test_ai_trade_realized_prior.py`（新） | 事件同源律四条过滤、毛口径铁律、零亏损样本、三级收缩、`w(n)==0` 严格缺席 |
| `services/quant/tests/test_ai_trade_edge_governor.py`（新） | 三档判定、滞回、墙钟上限、`size_mult` 下界、与刹车相乘、`hold` 降级 |
| `services/quant/tests/test_ai_trade_loss_churn_gate.py`（新） | 判据、七条豁免逐条、`k=0` 逐字节回退、`liveR` 不可算即放行 |
| `services/quant/tests/test_ai_trade_governor_wiring.py`（新） | 装配 / 不装配双臂的开仓意图对拍（G-11）、总闸关闭时的对象同一性（`is` 相等） |
| `services/quant/tests/test_ai_trade_discipline.py`（改） | 三档新键进 `test_every_preset_survives_the_real_validator` |
| 前端 `EdgeGovernorCard.test.tsx` / `AiTradeLaunchModal.test.tsx`（改） / `DisciplineCard.test.tsx`（改） | 见 §七 |

---

## 五、接口设计

### 5.1 REST

```
GET  /api/ai-trade/realized-edge?instId=BTC-USDT-SWAP&bar=15m
     → 200 {
         instId, bar,
         scopes: [{ level: "L0"|"L1"|"L2", side: "long"|"short"|"both",
                    n, minTrips, weight, pHat, avgWinBps, avgLossBps, evBps,
                    effectiveEvBps, armed: bool, note: string|null }],
         governor: { level, sinceTs, expiresTs|null, sizeMult, reason, firedCount },
         updatedTs
       }
     → 404 { code: "no_samples" }   ← 一笔合格往返都没有；**不返回空统计冒充有数据**

POST /api/ai-trade/realized-edge/rebuild   { instId, bar }
     → 200 同上（从 trades 表全量重算并覆盖 KV；幂等）
     → 409 { code: "session_running" }     ← 该 (instId,bar) 正在会话中，避免与在线增量写竞态
```

**路由注册顺序硬约束**：两条都必须注册在 `/api/ai-trade/history/{group_id}` **之前**，否则被路径参数吞掉（`api/ai_auto.py:681`、`:699` 已有成文教训）。

### 5.2 WebSocket（决策轨迹）

新增一个轨迹段，与既有 `edge_gate` 段同级、紧随其后：

```jsonc
{ "type": "edge_governor", "roundNo": 87, "ts": 1788284327,
  "data": { "level": "throttle", "prevLevel": "normal", "sizeMult": 0.5,
            "scope": "SOL-USDT-SWAP|15m|long", "n": 18, "evBps": -22.4,
            "reason": "governor_throttle(n=18,ev=-22.4bps)",
            "expiresInS": null, "armed": true } }
```

`level == "normal"` 且**未发生档位切换**时**不发事件**（避免每轮刷屏；与 `edge_gate` 的「空转即零事件」同款）。

### 5.3 会话 wire（`aiStats` 加法块）

```jsonc
"aiStats": {
  "edgeGovernor": { "level": "throttle", "sizeMult": 0.5, "firedCount": 6,
                    "scope": "SOL-USDT-SWAP|15m|long", "n": 18, "evBps": -22.4,
                    "sinceTs": 1788280000, "expiresTs": null },
  "realizedPrior": { "armed": true, "level": "L0", "n": 18, "minTrips": 12,
                     "weight": 0.214, "evBps": -14.2, "note": null },
  "llmHealth": { "decideRetries": 17, "decideFailures": 3,
                 "summarizeTimeouts": 28, "lastErrorCode": "LLM_NETWORK_ERROR" }
}
```

三个块全部**整键可缺席**（未装配 / 无样本 ⇒ 不出现），前端按「键缺席即不渲染该卡」处理。

### 5.4 决策 payload（只读注入）

`payload["edgeGovernor"]`（仅 `level != "normal"` 时出现）与 `payload["realizedPrior"]`（仅 `armed` 时出现），文案由 `edge_governor_text.py` 单点生成，遵守 §3.2.5 的四条硬约束。

---

## 六、数据模型

### 6.1 持久化：零新表、零迁移

复用 `app_settings`（`key VARCHAR(64)` / `value TEXT`）：

```
键：ai_realized_edge:{instId}:{bar}
    例：ai_realized_edge:BTC-USDT-SWAP:15m  = 34 字符（上限 64，余量充足）
值：
{
  "schemaVersion": 1,
  "instId": "BTC-USDT-SWAP", "bar": "15m", "updatedTs": 1788284327,
  "scopes": {
    "long":  { "n": 18, "wins": 6, "sumWinBps": 214.6, "sumLossBps": 508.3,
               "sumNetBps": -403.1, "lossStreak": 2 },
    "short": { "n": 5,  "wins": 2, "sumWinBps":  61.0, "sumLossBps":  93.4,
               "sumNetBps":  -46.2, "lossStreak": 0 }
  },
  "governor": { "long": { "level": "throttle", "sinceTs": 1788280000 },
                "short": { "level": "normal", "sinceTs": 1788240000 } }
}
```

**只存计数，不存明细**：明细已经在 `trades` 表里；存两份必然漂移。坏 JSON 由 `repositories.get_app_setting` 统一回落 `default`（`ai_prior_repositories.py:6-8` 的成文理由：一条半解析的快照会让门禁拿着残缺统计去开真实仓位）。

### 6.2 内存形状

```python
@dataclass(frozen=True)
class RealizedScope:          # realized_prior.py
    inst_id: str; bar: str; side: str
    n: int; wins: int
    sum_win_bps: float; sum_loss_bps: float; sum_net_bps: float
    loss_streak: int

@dataclass(frozen=True)
class GovernorState:         # edge_governor.py
    level: str               # normal | throttle | probation
    since_wall: float
    fired_count: int

@dataclass(frozen=True)
class GovernorOutcome:       # edge_governor.py 的唯一产物
    decision: object         # 未受约束时返回同一个对象（is 相等）
    level: str
    size_mult: float
    reason: str | None
    wire: dict
    gated: bool              # 本轮是否受本层约束（观望轮不进分母，沿用 edge_gate.py:70-74 的教训）
```

### 6.3 无 DB 迁移

`db/session.py` 的 `_ensure_*_columns` 补丁器**不需要新增**：本轮不加任何列、不加任何表。这一条与上一轮情报层的 `CL-2`（`create_all` 不会给既有表加列，必须写 patcher）形成对照——本轮刻意选择 KV 形状正是为了绕开它。

---

## 七、测试与验收

### 7.1 门禁清单（G-1 … G-18）

| # | 门禁 | 判据 |
|---|---|---|
| **G-1** | 事件同源律 | 构造 4 类不合格往返（非 AI 开仓 / 反方向 / `external` 退出 / `sessionStop` 退出），断言均被 `realized_prior` 剔除 |
| **G-2** | 毛口径铁律 | 用一组 `gross ≠ net` 的往返，断言 `W̄ / L̄` 取自 `realized_gross_bps`；把入参换成净额时用例必须变红 |
| **G-3** | 零亏损样本 | 亏损样本 < 5 时整条目缺席（返回 `None`），而非产出 `p̂·W̄` |
| **G-4** | `w(n) == 0` 严格缺席 | `n < min_trips` 时**本级不产生任何数值**，直接下沉；断言不存在任何「至少给一点权重」的路径 |
| **G-5** | 三级收缩 | L0 不足 → L1；L1 不足 → L2；三级皆不足 → `None` |
| **G-6** | EV-1 单调保守 | 对 200 组随机输入，断言接入 realized 先验后的 `target_bps` **恒 ≤** 接入前；一次反例即失败 |
| **G-7** | 总闸逐字节回退 | 三个总闸各自关闭时，`decide → 执行` 的完整轨迹与合入前**逐字节相同**（沿用仓库既有 `zero-diff` 用例范式） |
| **G-8** | 对象同一性 | 门禁不受约束时返回**同一个** decision 对象（`assert out.decision is decision`） |
| **G-9** | Governor 三档 | 阈值边界（`n = min_trips ± 1`、`ev = -probation_bps ± ε`）逐点断言 |
| **G-10** | Governor 滞回与墙钟 | `probation` 超过 `max_s` 后**无条件**回落；滞回带内不抖动 |
| **G-11** | **「永不开单」双臂对拍** | 同一组回放输入，装配 / 不装配 Governor 两臂各跑一遍，断言装配臂的 `openFunnel.intents > 0`。**开仓意图归零即视为回归，总闸回滚**（沿用上一轮 P1-9 的成文防线） |
| **G-12** | 漏斗可观测 | 被 Governor 拦下的轮次必须出现在 `openFunnel` 的分子里，且 `aiStats.edgeGovernor.firedCount` 同步递增 |
| **G-13** | 亏损换手闸七条豁免 | 逐条构造命中场景，断言**全部放行**；特别是 `RELEASE_LOSS` 与 `liveR is None` 两条 |
| **G-14** | 亏损换手闸 `k=0` 回退 | `k_loss = 0` 时该分支结构性空转，`_first_gate_reason` 的输出与合入前逐字相同 |
| **G-15** | 净/毛口径不被统一 | 源码扫描门禁：`edge_governor.py` 内不得出现 `realized_gross_bps`，`realized_prior.py` 内不得出现 `realized_net_bps`（两个口径各自的单一真源） |
| **G-16** | 文案无字面星号 | 源码扫描：`edge_governor_text.py` / `track_record` 类后端文案中不得出现 `**`（本仓库已复发 3 次） |
| **G-17** | `response_model` 显式声明 | 三个新 wire 键各有一条断言「经真实 FastAPI 路由往返后仍存在」 |
| **G-18** | KV 零迁移 | 用一份 `52f466e`-era 的 `app_settings` 建库跑 `init_db`，断言无 DDL、读写正常 |

### 7.2 前端用例

- `EdgeGovernorCard.test.tsx`：三档各渲染一次；样本不足时**显式**渲染「样本 6/12，先验未生效」而不是隐藏该卡。
- `AiTradeLaunchModal.test.tsx`：成员战绩条在无样本时的文案；每日成本预算行的数值与给定入参逐位一致；`profitGuard` 关闭时警示行可见（断言**可见文本**，不用 `title` / `hover`——`Kpi` 组件无 `hint` 形参且悬停提示等于没显示，上一轮 P1-10 的教训）。
- `DisciplineCard.test.tsx`：两个新行的 armed / 未 armed 两态。
- 全部组件用例须在 `NODE_ENV=test` 下运行（否则 React `act()` 报错，仓库既有约束）。

### 7.3 上线后的验收口径（观察窗口 ≥ 2 周 / ≥ 40 笔往返）

| 指标 | 现状 | 目标 | 读点 |
|---|---|---|---|
| `edgeGate.firedCount` / 受约束开仓轮 | **0 / n** | 落入 20%–70% 的 L2 放行区间 | `DisciplineCard` |
| `calibration.edgeNumerator.medianErrorBps` | **≈ +74 bps** | ≤ +30 bps | `CalibrationCard` |
| 会话级 `Σ 手续费 / |Σ 亏损|` | **35%–100%** | ≤ 35%（`ai_trade_acceptance_max_fee_to_gross` 的既有阈） | `HorizonAcceptanceCard` |
| 腿 / 日 | **13–17** | ≤ 8 | `TurnoverBackstopCard` |
| `openFunnel.intents` | 基线 | **> 0**（G-11 硬红线） | `OpenFunnelCard` |

**明确不承诺的**：本轮**不承诺账户转为盈利**。入场 alpha 的改造属于 discovery / 因子层，不在本轮范围（§九）。本轮承诺的是「**当系统没有 edge 时，它会确定性地少交易乃至停手，并且用户看得见为什么**」。任何 UI 文案与文档都不得出现「已校准 / 经回测验证 / 可稳定盈利」这类措辞（`discipline.py:36-38` 的既有纪律）。

---

## 八、风险与缓解

| # | 风险 | 缓解 |
|---|---|---|
| **R-1** | **「恒真判据 ⇒ 永不开单」**（本仓库已发生 **4 次**） | ① `throttle` 档仍以半仓开仓，样本继续增长；② `probation` 有 12 h 墙钟上限，**无任何档位可永久生效**；③ G-11 双臂对拍设为硬红线；④ 三个独立总闸，任一可单独回滚；⑤ `openFunnel` 让「拦了几次」始终可见 |
| **R-2** | **自反馈回路**：用自己的战绩收紧自己 ⇒ 样本停止增长 ⇒ 永久 probation | 同 R-1 的 ①②；另加：`probation` 期间**仍然结算既有仓位**，样本不会归零 |
| **R-3** | 小样本噪声把一条正常信号判死 | 三级层级收缩 + `w(n)` 线性权重 + `w == 0` 严格缺席（G-4）+ `_MIN_LOSS_SAMPLES = 5` |
| **R-4** | **双重计费**：realized 先验若取净额，成本会在分子分母各扣一次，任何真实 edge 被判死 | `costBasis == "gross"` 铁律 + G-2 + G-15 源码扫描门禁 |
| **R-5** | **轴 C 锁死止损**（唯一一条拦平仓的改动） | 七条豁免（§3.3.2）优先于判据 + fail-open + `k=0` 逐字节回退（G-14）+ §十 Q-2 交评审裁定默认值 |
| **R-6** | 幸存者偏差：据 D-7 误推「多让保护层平仓」 | §零 反直觉 ② 写在最前；沿用 `ExitSourceCard.tsx:28-30` 的「不参与任何判定」结论；本轮不新增任何以退出来源为判据的门禁 |
| **R-7** | 热路径耗时 | 会话启动一次性加载 KV；决策轮只做字典算术（`O(1)`）；落盘按 `flush_trips` 节流；**决策轮零新增 IO** |
| **R-8** | 两个新口径（毛/净）被后来的维护者「统一」掉 | 代码注释写明理由 + G-15 源码扫描门禁把它冻住 |
| **R-9** | `session.py` / `manager.py` 早已超 800 行，本轮再加会更糟 | 净增硬预算（≤ +26 / ≤ +18）；超出即下沉到 runtime 模块，**不许靠删注释压行**（`prior_service.py` 的成文先例） |
| **R-10** | 工作树里有其它未提交的在制变更（`git status` 显示 30+ 未跟踪目录与 1 个已修改文件 `services/quant/app/api/ai_intel.py`） | 实现节点必须**逐路径 `add`**，只暂存本特性文件；提交前用 `git status --porcelain` 逐条核对 |

---

## 九、非目标

1. **不改造入场 alpha 本身**。D-6 证明入场没有 edge，但「造出 edge」是策略/因子挖掘的工作（`docs/plans/strategy-factor-discovery/`、`discovery/`），不是本轮。本轮只保证「没有 edge 时会停手」。
2. **不动 LLM 的交易观点**：`prompts.py` / `reasoning_protocol.py` 只做只读注入的加法，不改任何既有推理指令。
3. **不动执行侧**：`exchange_backstop` / `backstop_repair` / `okx` 适配层 / `feed_runtime` 一行不改。
4. **不动 `base_rules` / `profit_guard` / `exit_priority` 的既有判据**（`exit_priority` 的 `evidence_min_chars` 缺陷见 D-10，本轮**只记录不改**——改动它会与轴 C 在同一条平仓链上叠加两处收紧，风险不可分离评估；建议下一轮单独处理）。
5. **不重构 `session.py`（3390 行）/ `manager.py`**。
6. **不做 DB 迁移**、不加表、不加列。
7. **不修改 `DEFAULT_ORIGINS`**（轴 E-2 走注入而非放宽）。

---

## 十、待评审裁定的开放问题

| # | 问题 | 本文的选择 | 反方理由 |
|---|---|---|---|
| **Q-1** | `ai_trade_governor_enabled` 出厂默认应为 `True` 还是 `False`？ | **`True`** | 反方：本仓库所有新门禁的先例都是默认关、由预设装配。正方（本文）：「默认全关、预设也没装」正是本轮认定的第三根因（D-4/D-5），再默认关一次等于把同一个缺陷复制一遍。折中方案：默认 `True` 但 `probation_bps` 默认 0（只有 `throttle` 半仓档生效），`probation` 由预设装配 |
| **Q-2** | `ai_trade_horizon_loss_churn_k` 默认 `1.0` 还是 `0`？ | **`1.0`** | 反方：这是唯一一条拦平仓的改动，方向上比其它三轴危险，默认 0 更符合「先观察再收紧」。正方：默认 0 会让本轴变成第二个「有写点没读点」的功能——而 D-9 那 30 笔、−96.22 USDT 正是它要拦的 |
| **Q-3** | Governor 的 scope 是否应包含 `side`？ | **包含**（L0 = `instId × bar × side`） | 反方：样本被切成两半，达标更慢。正方：多头与空头在同一标的上是两个完全不同的分布（D-7 的方向不对称已有迹象），合并等于用一边的战绩去开另一边的仓，且**不会报错** |
| **Q-4** | 轴 E-2（桌面端 Origin 注入）是否整轴移出本轮？ | **保留** | 反方：与「AI 自动交易」无直接关系，属于桌面端外壳。正方：它的后果是「自动交易开不起来、且没有任何提示」（236 条日志、零用户可见信号），成本是 `main.js` 的 3 行 |

---

## 附录 A：证据复算脚本

以下脚本在用户本机对 `%APPDATA%/aragon-desktop/aragon.db` **只读**执行，可逐位复算 §一 的全部数字。运行前需 `PYTHONIOENCODING=utf-8`（Windows 控制台默认 GBK 会在中文/箭头字符上抛 `UnicodeEncodeError`）。

```python
# A-1｜D-1 / D-2：全量战绩与逐会话费亏比
import sqlite3, os, json, statistics as st
from collections import defaultdict
p = os.path.expandvars(r'%APPDATA%/aragon-desktop/aragon.db')
c = sqlite3.connect('file:' + p + '?mode=ro', uri=True)
q = """select t.id,t.run_id,t.ts,t.side,t.price,t.qty,t.fee,t.pnl,t.explain_json,
       t.stop_loss_price,t.take_profit_price,r.inst_id,r.bar,r.group_id
       from trades t join strategy_runs r on r.id=t.run_id
       where r.mode='ai-trade' order by t.id"""
rows = c.execute(q).fetchall()
closes = [r for r in rows if abs(r[7]) > 1e-12]
print(len(rows), len(closes), sum(r[7] for r in closes), sum(r[6] for r in rows))

# A-2｜D-3 / D-7 / D-8：按 explain_json.meta 的分层读数
metas = [(json.loads(r[8]) or {}).get('meta') or {} for r in rows if r[8]]
have  = [m for m in metas if m.get('realizedNetBps') is not None]
by = defaultdict(list)
for m in have: by[m.get('exitSource', '?')].append(m)

# A-3｜D-5：先验快照与总闸
print(c.execute("select count(*) from app_settings where key like 'ai_prior:%'").fetchone())

# A-4｜D-6：反事实（完整版见本轮 scratchpad 的 cf.py；同根同时触发保守判为先触止损）
#   对每笔带 sl/tp 的开仓，取同 (instId,bar) 的 candles_cache 向前推演 96 根
```

**D-6 的三条口径必须与本文一致，否则数字不可比**：① 从**开仓根之后**的第一根开始判定（不含开仓根自身）；② 同根同时触及止损与止盈**保守判为先触止损**；③ 96 根内两边都没碰到的记为「未决」，按最后一根收盘价折算 R（不丢弃、不补零）。

---

## 附录 B：本轮引用的源码坐标（评审逐条核对用）

| 主张 | 坐标 |
|---|---|
| edge 门禁分子链与 EV-1 单调保守 | `services/quant/app/ai_auto/edge_gate.py:78-140` |
| `k_min <= 0` ⇒ 门禁空转、返回同一对象 | `edge_gate.py:12-18`、`:137-160` |
| `k_min` 出厂默认 0.0 | `services/quant/app/config.py:557` |
| 三个先验总闸默认 False | `config.py:589`、`:590`、`:593` |
| 四档纪律预设表（无先验键） | `services/quant/app/ai_auto/discipline.py:44-131` |
| 分子是「幅度非期望」的成文自述 | `services/quant/app/ai_auto/calibration.py:14-17` |
| `calibration` 的唯一消费者是只读报表 | `services/quant/app/ai_auto/track_record_service.py:17` |
| 战绩只作证据、不作门禁的成文纪律 | `services/quant/app/ai_auto/track_record.py:1-24`、`:31-60` |
| 先验快照 KV 键前缀与坏 JSON 回落 | `services/quant/app/db/ai_prior_repositories.py:6-8`、`:19-21` |
| `prior_ev_bps` / `prior_binding` 注入点 | `services/quant/app/ai_auto/session.py:2469-2486` |
| 决策轮门禁链顺序 | `session.py:1665-1697` |
| G3 迟滞闸的 `move <= 0` 放行 | `services/quant/app/ai_auto/hold_horizon.py:697-703` |
| 豁免矩阵 `gated_kind` / `release_reason_for` | `hold_horizon.py:466-484`、`:507-535` |
| `override` 准入是字符串长度检查 | `services/quant/app/ai_auto/exit_priority.py:65-69`、`:157-181` |
| `OpenSnapshot`（`entry_px` / `stop_px` 在运行时可得） | `services/quant/app/ai_auto/trip_ledger.py:66-84`、`:189-230` |
| `settle_close` 单点收口 | `services/quant/app/ai_auto/attribution.py:119-137` |
| `reachedOneRShare` 与其 UI 标签 | `attribution.py:151-176`；`apps/web/components/Agent/aiTradeStats/ProfitabilityCard.tsx:76` |
| 退出来源占比「不参与任何判定」 | `apps/web/components/Agent/aiTradeStats/ExitSourceCard.tsx:14-30` |
| `profitGuard` 粘性关闭 | `apps/web/components/Agent/AiTradeLaunchModal.tsx:206`、`:279-281` |
| Origin 白名单默认只含 18761 | `services/quant/app/security/origins.py:22-25`；`config.py:1361-1367` |
| 运行起点解析链（D-13 之一） | `services/quant/app/db/ai_auto_repositories.py:233-237` |
| `manager` 的按 `instId` 键快照姿态 | `services/quant/app/ai_auto/manager.py:207-215` |
