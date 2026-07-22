# AI 自动交易「统计」漏记已完成交易 —— 诊断报告

- **bug slug**：`ai-trade-stats-missing-round-trips`
- **诊断日期**：2026-07-22
- **诊断范围**：定位 + 根因 + 候选修复（本节点不写修复代码、不 commit）
- **结论一句话**：成交**全部**正确落库，问题出在**读侧配对**——`trades.ts` 被两套时钟写入（K 线开盘时刻 vs 墙钟），
  统计面板按 `ts` 排序做「开→平」因果配对，一旦按 `ts` 排出的序列**破坏了真实的「开→平」交替次序**，
  这笔已完成交易就被整笔丢弃（或与相邻成交错配）。生产库实测：**161 笔真实完整交易中 13 笔在统计里消失，
  6/16 个会话的收益与笔数被算错**。
  〔评审修正〕失效条件不止「同一对内 `close.ts < open.ts`」一种；「**后一对的开仓 ts 早于前一对的平仓 ts**」
  同样致命（生产库 `run_id=1548` 即此形态，其两对**各自都不逆序**）。二者合计才是 13 笔，逐项见 §8.3。

---

## 一、问题描述

用户反馈：AI 自动交易确实开仓成功、平仓成功、交易顺利完成，但点击右侧 AI 助手横栏的「统计」按钮后，
**个别已发生的交易记录没有出现在统计面板里，也没有被计入统计**。

复现面覆盖统计面板的两个页签：

- 「当前」页（运行中会话，`AiTradeStatsLive.tsx`）：右栏「交易记录」缺行，左栏 KPI（已实现总收益 / 完整交易笔数 /
  胜率 / 最大单笔盈亏）随之算错；
- 「历史」页（已停止会话，`AiTradeStatsHistoryList.tsx` / `AiTradeStatsDetail.tsx`）：列表卡片与详情
  「逐成员完整交易」同样缺行、KPI 同样算错。

用户感知是「偶发、个别」，与实测吻合：逆序只发生在特定时序组合下，生产数据里占比 **7.5%（12/161）**。

---

## 二、复现步骤

### A. 在用户自己的数据上直接复现（最快，只读，30 秒）

```bash
# 1) 先把正在运行的桌面端数据库拷出来（避免读 WAL 中途态）
cp "%APPDATA%/aragon-desktop/aragon.db" /tmp/aragon-copy.db

# 2) 扫描：因果配对（按落库顺序）vs 面板配对（按 ts 排序）
python docs/diagnoses/ai-trade-stats-missing-round-trips/repro/scan_inverted_pairs.py /tmp/aragon-copy.db
```

实测输出（2026-07-22，`C:/Users/user/AppData/Roaming/aragon-desktop/aragon.db`，143 条 ai-trade run）：

```
== 1. 各 mode 的时钟一致性 ==
mode           rows  causal  inverted   bar-ts  wall-ts
ai-trade        323     161        12      203      120     <-- 只有 ai-trade 出现逆序
backtest      49184   24319         0    49156       28
live-auto        14       6         0       14        0
paper          1391     680         0     1391        0

== 3. 逐 group 差异（用户在「统计」里看到的口径）==
groupId                                       true#  shown#    truePnl   shownPnl
aitradegrp_b9be9509c32d4b89baec0627ceae9dd3       6       4     0.3802     0.4840
aitradegrp_7726ec14276949d5aa6d282be3c93e9c       8       7    -6.3115    -6.1225
aitradegrp_1aad906717e74b1b996ee3d6b737c8c6      17      15    -0.0128    -0.0367
aitradegrp_317b8e62ea8c4abeb1f86fc24c20e6ba      10       8    -0.5340    -0.3696
aitradegrp_b2e9acb487bc4d8e8222a520c661566d      39      34     1.3984     1.9904
aitradegrp_3c3ab0ee628e40a78bac8474d5ff2439       2       1    -0.2658    -0.3232
groups=16 affected=6
```

### B. 前端最小复现（真实前端代码 + 真实生产数据两行）

```bash
cp docs/diagnoses/ai-trade-stats-missing-round-trips/repro/ts_order_repro.test.ts apps/web/lib/
cd apps/web && NODE_ENV=test npx vitest run lib/ts_order_repro.test.ts
```

实测（两个断言均失败，即缺陷成立）：

```
FAIL  lib/ts_order_repro.test.ts > drops a completed trade whose close.ts precedes open.ts
AssertionError: expected [] to have a length of 1 but got +0

FAIL  lib/ts_order_repro.test.ts > mis-pairs and drops when a bar-ts open follows a wall-clock close
AssertionError: expected [ [ 50896, 50894 ] ] to deeply equal [ [ 50892, 50894 ], [ 50896, 50898 ] ]
```

第二条同时暴露**错配**：`50892`（入场价 66409.2）本该配 `50894`，却被 `50896`（入场价 66186.5）顶替 ——
面板上那一行的入场价、持仓时长、展开的 K 线点位**全错**，而 `50898`（-0.1256）整笔消失。

### C. 后端最小复现（临时 SQLite + 真实仓储函数）

```bash
cd services/quant
PYTHONPATH=. python ../../docs/diagnoses/ai-trade-stats-missing-round-trips/repro/repro_backend_history_drop.py
```

实测：

```
trades persisted in DB      : 2
list  tradeCount            : 0 (expected 1)
list  realizedPnlUsd        : 0.0 (expected 0.0574)
detail summary.tradeCount   : 0 (expected 1)
detail summary.realizedPnl  : 0.0 (expected 0.0574)
member metrics.totalReturn  : 0.0009147410358565737 (finalize-time truth -> KPI 自相矛盾)
```

### D. 真机端到端复现（在应用里手动走一遍）

前置：`AI_TRADE_ENABLED` 与 `LIVE_TRADING_ENABLED` 均开启，已配置 OKX 实盘账户与默认模型。

1. 启动 AI 自动交易，**周期选 15m 或 30m**（周期越大越容易命中），唤醒间隔保留默认 **2 分钟**
   （`ai_trade_wake_interval_default_min = 2.0`，config.py:184）；
2. 等待一次**定时唤醒轮**（执行轨迹里 `trigger=interval`）真实开仓 —— 该成交的 `ts` 是墙钟；
3. 在**同一根 K 线走完之前**让它平仓：等下一次 K 线确认轮（`trigger=candle`）给出 close，或直接点「停止」
   （`stop()` 用最后一根 K 线的 ts）—— 该成交的 `ts` 是**这根 K 线的开盘时刻**，比第 2 步的墙钟**更早**；
4. 点「统计」按钮 → 「当前」页右栏「交易记录」看不到这笔已完成交易，左栏「完整交易 N 笔 / 已实现总收益」
   也不含它；停止会话后切到「历史」页详情，同样看不到。

判定标准：`sqlite3 aragon.db "select id,ts,side,pnl from trades where run_id=<该成员 run> order by id"`
能看到这两行成交确实在库里，但面板不显示 —— 即「记录了、没统计」。

---

## 三、根因分析

### 3.1 写侧：`trades.ts` 由两套互不相容的时钟写入

`Trade.ts` 的语义在 AI 自动交易里被写乱了 —— 同一个 run 内，不同代码路径给的是不同口径的时间：

| 路径 | 传给 executor 的 `ts` | 口径 | 代码 |
|---|---|---|---|
| K 线确认轮（`trigger=candle`） | `int(row["ts"])` | **K 线开盘时刻**（比墙钟最多晚一整根 bar） | `services/quant/app/ai_auto/session.py:465` → `:479` |
| 定时唤醒轮（`trigger=interval`） | `int(now)` | **墙钟** | `services/quant/app/ai_auto/session.py:369-370` |
| monitor 盯市轮（限价成交采纳 / 外部平仓合成） | `int(time.time())` | **墙钟** | `services/quant/app/ai_auto/session.py:321-324` |
| 停止会话 flatten | `self._ref_ts()` = 最后一根 K 线 ts | **K 线开盘时刻** | `services/quant/app/ai_auto/session.py:264-268`、`:304-306` |

`ts` 一路透传到成交行，中途没有任何归一：

```python
# services/quant/app/ai_auto/session.py:756-758
events = await self.executor.apply(signals, ref_px=ref_px, bar_index=i, ts=ts,
                                   order_plan=order_plan)
events += await self.executor.reconcile(ref_px, ts)
```

```python
# services/quant/app/live_auto/executor.py:766
trade = {"ts": int(ts), "side": side, "price": float(price), ...}
```

```python
# services/quant/app/db/repositories.py:294-295
session.add(Trade(
    run_id=run_id, ts=int(trade.get("ts", 0)), side=str(trade.get("side", "")), ...
```

两个墙钟来源在生产里**默认全开**：`ai_trade_wake_interval_default_min = 2.0`（config.py:184）、
`ai_trade_limit_orders_enabled = True`（config.py:163）。实测三个受影响会话的 `config_json` 均为
`wakeIntervalMinutes: 2.0 / 3.0`。

于是在 15m 周期下，绝大多数决策轮是定时唤醒轮（墙钟 ts），而 K 线轮与 stop 给出的是**最多落后 15 分钟**的
bar 开盘 ts —— 「先开后平」的一对成交，`ts` 顺序完全可能是反的。

**生产数据实证**（`scan_inverted_pairs.py` 第 1 段）：ai-trade 的 323 条成交里 **203 条 bar 对齐、120 条墙钟**，
而 live-auto / paper / backtest 三个模式**全部单一时钟、零逆序** —— 印证这是 AI 自动交易特有的缺陷
（live-auto 没有定时唤醒、也不下限价单，`resting` 恒为 None，其墙钟路径只可能产生「平仓晚于开仓」的正序）。

真实逆序样本（生产库 `run_id=1557`，ETH-USDT-SWAP / 15m）：

```
id=50914  ts=1784701626  07-22 06:27:06  WALL_CLK  side=long   px=1912.94  pnl=0.0
id=50915  ts=1784700900  07-22 06:15:00  BAR_TS    side=close  px=1913.64  pnl=0.0574
                         ^^^^^^^^^^^^^^ 平仓的 ts 比开仓早 12 分 06 秒
```

### 3.2 读侧：往返配对以 `ts` 为排序键，逆序即整笔丢弃

后端历史聚合逐字按 `(ts, id)` 排序后做单持仓因果配对，**孤儿平仓直接丢弃**：

```python
# services/quant/app/db/ai_auto_repositories.py:50-64
def _pair_round_trips(trades: list[Trade]) -> list[tuple[Trade, Trade]]:
    pairs: list[tuple[Trade, Trade]] = []
    open_trade: Trade | None = None
    for t in sorted(trades, key=lambda x: (x.ts, x.id or 0)):   # <-- ts 当排序键
        if t.side in ("long", "short"):
            open_trade = t
        elif open_trade is not None:
            pairs.append((open_trade, t))
            open_trade = None
    return pairs                                                 # 孤儿平仓 / 悬挂开仓丢弃
```

汇总口径（`realized / winRate / best / worst / tradeCount`）全部只看 `pairs`：

```python
# services/quant/app/db/ai_auto_repositories.py:67-90
for _entry, exit_trade in pairs:
    pnl = exit_trade.pnl or 0.0
    realized += pnl
```

前端逐字同构（这是当初「后端汇总与前端复算逐值吻合」的设计要求，所以两边**同时**错）：

```ts
// apps/web/lib/tradeTrails.ts:110-121
function indexTrades(trades: Trade[]): IndexedTrade[] {
  return trades.map((trade, index) => ({ trade, index }))
    .sort((a, b) => {
      if (a.trade.ts !== b.trade.ts) return a.trade.ts - b.trade.ts;   // <-- ts 当主排序键
      return tradeOrder(a) - tradeOrder(b);
    });
}

// apps/web/lib/tradeTrails.ts:71-91
function buildPairedTrails(trades: Trade[]): PairedTrail[] {
  ...
  if (!open) continue;      // <-- 孤儿平仓静默丢弃
```

调用链（两个页签都落在同一对纯函数上）：

- 「当前」页：`AiTradeStatsLive.tsx:59` `buildLedger` → `autoTradePnl.ts:53-79` `buildRoundTrips` → `buildTradeTrailSegments`
- 「历史·详情」：`AiTradeStatsDetail.tsx:214-217` `buildRoundTrips`（同上）
- 「历史·列表」/详情 KPI：`api/ai_auto.py:201-222` → `ai_auto_repositories.list_ai_trade_groups / get_ai_trade_group_detail`

### 3.3 两种失效形态

| 形态 | 触发条件 | 后果 | 生产实测 |
|---|---|---|---|
| **整笔丢弃** | 一对开→平**对内逆序**（`close.ts < open.ts`），且前后没有其它成交可"顶替" | 该笔完整交易在面板上完全不存在，其 `pnl` 不计入已实现收益 / 胜率 / 最大盈亏 | 12 笔（11 个 run） |
| **错配 + 丢弃** | **跨对重叠**：后一对的开仓 ts 早于前一对的平仓 ts（对内均不逆序） | 显示出来的那一行**入场价 / 持仓时长 / 展开 K 线的开仓点位全错**，另有一笔被丢弃 | 1 笔（`run_id=1548`） |

〔评审修正〕原表把 `run_id=1548` 归为「逆序对夹在其它成交中间」，与数据不符：该 run 的两对
`(50892,50894)`、`(50896,50898)` **对内都是正序**（平仓 ts 均晚于开仓 ts），它触发的是**跨对重叠**这一独立形态。
因此 `scan_inverted_pairs.py` 第 1 段的 `inverted=12` 与第 2 段的「丢失 13 笔」是**两个口径**，不是同一个数；
把两种形态分开统计才对得上（12 + 1 = 13，逐 run 明细见 §8.3）。这一点直接决定方案 3 的成败（§8.4）。

`run_id=1548` 实测（BTC-USDT-SWAP / 15m）：

```
id=50892  ts=1784654324  17:18:44  short  px=66409.2   pnl=0.0
id=50894  ts=1784656025  17:47:05  close  px=66200.0   pnl=0.3347
id=50896  ts=1784655900  17:45:00  short  px=66186.5   pnl=0.0      <-- ts 倒退 2 分 05 秒
id=50898  ts=1784659083  18:38:03  close  px=66265.0   pnl=-0.1256
```
按 ts 排序后 `50896` 抢在 `50894` 之前，把 `50892` 顶掉 → 面板显示一行「66186.5 → 66200.0，+0.3347」（张冠李戴），
`50898` 的 -0.1256 整笔消失。

### 3.4 排除项：数据没丢，只是没被统计

- **落库完整**：`_finalize_run` 写入的 `metrics.tradeCount`（= `len(executor.trades)`，会话内存真值，
  `services/quant/app/ai_auto/session.py:1040`）与该 run 在 `trades` 表里的行数逐条比对，
  **111/111 完全一致，零差异** → `_persist_trade` 的 best-effort 落库没有丢过任何一笔。
- **按落库顺序配对是完美的**：以 `trades.id` 升序（= 落库顺序 = 真实发生顺序）配对，
  **161 笔平仓 161 笔全部成对、零孤儿**；按 `ts` 配对只剩 148 笔 → 差额 13 笔正是用户看不到的交易。
- **不是 group_id / 时间窗口筛选问题**：受影响 run 全部有 `group_id`，「当前」页默认区间是 `all`
  （`AiTradeStatsLive.tsx:26`），历史列表不做时间过滤。
- **不是 AI 自动交易之外的问题**：live-auto / paper / backtest 三个模式在同一库里 **0 逆序**。
- **自相矛盾可直接观察**：`metrics_json.totalReturn`（finalize 时按 `executor.realized_pnl_usd` 算，是对的）
  与面板 KPI（按配对结果算，是错的）在同一条 run 上并存，二者对不上即本缺陷的指纹。

---

## 四、影响面

| 受影响对象 | 具体表现 |
|---|---|
| 统计面板「当前」页（`AiTradeStatsLive.tsx`） | 右栏交易记录缺行；左栏已实现总收益 / 总收益率 / 完整交易笔数 / 胜率 / 最大单笔盈亏全部偏差；`AnnualizedProjectionCard` 的预计年化基于错误 `returnPct/tradeCount` → 年化外推同样错 |
| 统计面板「历史」列表（`AiTradeStatsHistoryList.tsx:97-106`） | 会话卡片展示的 `tradeCount / winRate / realizedPnlUsd / returnPct` 全部偏差（后端 `_summarize_pairs` 同一份 summary，`bestUsd / worstUsd` 亦然，只是仅在详情页渲染） |
| 统计面板「历史」详情（`AiTradeStatsDetail.tsx`） | 同上，且「逐成员完整交易」缺行；错配行的入场价 / 持仓时长 / 行内展开 K 线的开平点位错误 |
| K 线交易轨迹 / markers（`buildTradeMarkerEvents`、`AutoTradeRoundTripRow`） | 逆序平仓被标成 `orphan-close`，连线 `canRenderLine`（`tradeTrails.ts:104`）为 false → 不画开→平连线 |
| 数据可信度 | 实测 **6/16 个 AI 会话**的对外 KPI 是错的；极端样本 `aitradegrp_b2e9ac…`：真实 39 笔 / **+1.3984**，面板显示 34 笔 / **+1.9904**（收益虚高 42%）。方向双向都有：`aitradegrp_7726ec…` 真实 -6.3115、显示 -6.1225（亏损被低估） |
| 未受影响 | 实盘下单 / 风控 / 平仓逻辑本身；`executor.realized_pnl_usd`、`metrics_json` 的 finalize 口径；live-auto / 模拟盘 / 回测的收益统计（单一时钟，实测零逆序） |

**发生条件与频率**：需要「墙钟腿 → bar-ts 腿」且两者落在同一根 bar 的时间跨度内。周期越大（15m/30m）、
唤醒间隔越短（默认 2 分钟）越容易命中。生产实测 12/161 = **7.5%** 的完整交易被影响 —— 与用户描述的
「有些个别」完全吻合。

---

## 五、候选修复

### 方案 1：配对排序键改用**因果顺序**（`trades.id` / WS 到达序），不再用 `ts`

- 后端 `ai_auto_repositories._pair_round_trips`：`sorted(..., key=lambda x: (x.ts, x.id or 0))` → 按 `id` 升序；
  `_load_trades_by_run`（:174-176）的 `order_by` 同步改为 `Trade.id.asc()`；
  `_aggregate_group`（:156）取会话结束时间的 `member_trades[-1].ts` 需改成 `max(t.ts …)`（排序键换了，"最后一条"不再是"最晚一条"）。
- 前端 `tradeTrails.indexTrades`：去掉 `ts` 主排序键，改用 `tradeOrder`（`trade.id ?? index`）——
  历史路径有 `id`（`trade_out` 已带，`schemas.py:851`），实时 WS 路径无 `id` 时数组到达序即因果序。
- **需同时补**：显示层对 `closedTs < openedTs` 的持仓时长做钳制，否则明细行会出现负时长。

〔评审修正 —— 本方案上面三条各有一处需要改写，改写后反而更小、更安全，详见 §8.5～§8.7〕

1. **`_load_trades_by_run` 的 `order_by` 不要动**。只改 `_pair_round_trips` 的排序键即可（配对函数自己排序，
   与 SQL 顺序无关）。SQL 保持 `(ts, id)` 升序 ⇒ `_aggregate_group:156` 的 `member_trades[-1].ts`
   与 `_group_started_ts:141` 的 `trades_by_run[r.id][0].ts` 语义**原样成立**，连带修改归零。
   反之，若按原文改 SQL，则**必须**同时改这两处；原文只列了前者，漏了 `_group_started_ts` 的第 3 级回退（§8.5）。
2. **前端不能改 `indexTrades`**。`indexTrades` 同时决定 `buildTradeMarkerEvents` 的**渲染顺序**，其结果直接进
   `candleSeries.setMarkers(...)`（`CandleChart.tsx:150`→`:4xx`、`TradeCandleChart.tsx:81-82`、
   `WeightBacktestChart.tsx:82`）。lightweight-charts 4.2.3 的 `setMarkers` 会调
   `checkItemsAreOrdered(data, …, true)` 断言「markers 必须按 time 升序」
   （`dist/lightweight-charts.standalone.development.js:12999-13003` / `:12801-12812`）——
   一旦按因果序喂入逆序 ts，**dev 构建直接抛断言、图表崩**（production 构建剥掉了该断言，属静默未定义行为，
   见 §8.6）。正确切法：**只改配对遍历顺序（`buildPairedTrails`），`indexTrades` 原样留给渲染**。
   二者本就解耦——`PairedTrail` 记的是**原数组下标** `entryIndex/exitIndex`（`tradeTrails.ts:82-86`），
   `buildTradeMarkerEvents` 按下标回查（`:30-34`），配对顺序换了不影响 marker 排序。
3. **「需同时补显示层钳制」这条不成立，无需补**。两处持仓时长格式化都已经钳过负值：
   `AutoTradeRoundTripRow.tsx:78` `Math.max(0, Math.floor(seconds))`、
   `TradeCandleChartPanel.tsx:82` `Math.max(0, exit.ts - entry.ts)` 与 `:118` 同款。
   被救回的 12 笔会显示「持仓 0秒」（诚实但不精确，须靠方案 2 才能真正修好），不会出现负时长。

### 方案 2：统一写入时钟 —— 成交时间戳一律取**真实执行墙钟**

- `AiTradeSession.feed_candle` 传给决策轮的 `ts` 改为 `int(time.time())`；`stop()` 的 `ts` 同理；
  K 线开盘时刻继续通过 `payload["barTs"]` / `bar_index` 传递，决策上下文与 prompt 不受影响。
- **实施注意**：`_inject_explain_meta`（`session.py:196-209`）当前是 `meta.setdefault("barTs", trade.get("ts"))`，
  即 `barTs` 直接抄成交 `ts`；改成墙钟后它会一并变成墙钟，须显式改传真实 bar ts，否则 `explain.meta.barTs`
  这个"这笔成交属于哪根 K 线"的锚点就丢了。
- 或更彻底：`LiveExecutor._make_trade` 内部取墙钟 —— 但这会改变 live-auto / 模拟盘既有 `ts` 语义（成交 marker 当前贴合 K 线），回归面显著更大。

〔评审补充：本方案的爆炸半径比原文估计的大一档〕同一个 `ts` 形参在决策轮内**一形参两用**：既透传给
`executor.apply(..., ts=ts)` 变成成交时间戳（`session.py:756`），又喂进 prompt 输入
`"multiTimeframeContext": self._build_multi_timeframe_context(ts, ref_px)`（`session.py:852`），
后者内部算 `as_of_ts = ts + BAR_SECONDS[self.bar]`（`session.py:868-874`）。因此把 `feed_candle` 的 `ts`
改成墙钟会**顺带移动多周期上下文的取数断面**，即改动 prompt 输入 —— 与项目既有的「prompt 逐字节冻结门禁」
直接冲突。方案 2 若要做，必须先把这一个形参**拆成两个**（`trade_ts` 墙钟 / `bar_ts` 根开盘），
并同步处理 `_inject_explain_meta` 的 `barTs`（`session.py:196-209`）。这也解释了一个既存事实：
定时唤醒轮今天传的就是墙钟，其 `as_of_ts` 早已是「墙钟 + 一根」，说明 `ts` 语义超载是系统性的、不是新引入的。

### 方案 3：读层做「逆序归一」（配对前把 `close.ts` 抬到 `max(open.ts, close.ts)`）

- 只改一处读逻辑，改动最小。
- 〔评审判定：**不成立，实测修复 0/13**〕见 §8.4。要点：「哪个 open 配哪个 close」正是待求解的问题，
  归一却需要先知道答案 —— 逻辑上循环。用唯一可实现的近似（按 ts 序取「前一个 open」抬平）在生产库上实跑，
  12 个受影响 run **无一恢复**，配对结果与「按 ts 直配」逐个相同。

### 对比

| 维度 | 方案 1（因果排序键） | 方案 2（统一写入时钟） | 方案 3（读层归一） |
|---|---|---|---|
| 侵入性 | 低：后端 3 处、前端 1 处排序/取值，全在只读聚合层 | 中：改动落在实盘决策轮 → executor 的 `ts` 透传链上 | 极低：1 处 |
| 能否修好**已落库**的 13 笔历史（12 run / 6 组） | ✅ 能（`id` 完好，零数据迁移，实测按 id 配对 161 平仓全成对） | ❌ 不能（旧行 `ts` 已写坏，需额外迁移） | ❌ **不能**（实测 0/13，§8.4） |
| 能否修好**未来**数据 | ✅ 能 | ✅ 能 | ❌ **不能**（同上） |
| 附带修好「持仓时长 / 时间窗口筛选 / K 线点位」 | ❌ 否（`ts` 仍是双时钟，需显示层钳制） | ✅ 是（`ts` 恢复单调，语义自洽） | ❌ 否 |
| 风险 | `id` 序 == 因果序需成立（实测 143 run 全成立）；`_dispatch` 在 `_exec_lock` 外，理论上存在极窄的落库乱序竞态 | 触及真实资金热路径的参数透传；需重跑 ai-trade 全套单测 + prompt 冻结门禁；须确认前端 marker 定位不依赖 bar 对齐 | 制造**第二套时间真源**，把矛盾埋进读层；后续任何按时间排序的新功能都会再次踩坑 |
| 工作量 | 小（含回归测试 0.5 天） | 中（1～1.5 天，含单测与真机验证） | 极小（但不推荐） |

---

## 六、推荐修复

> 〔评审提示〕本节为诊断节点的原始推荐，**已被文末「评审结论」收敛**：落地口径以「方案 1′ 最小因果配对」
> 为准（方案 1 的三处附带改动经复核为不必要 / 有害，方案 2 改为单独立项）。本节保留以便追溯推理过程。

**方案 1 打底 + 方案 2 收口，分两步落地；若只能做一件事，做方案 1。**

理由：

1. **只有方案 1 能立刻救回已经发生的历史。** 用户已有 6 个会话的 KPI 是错的，`ts` 已经写坏、无法从中恢复
   因果顺序；而 `trades.id` 完好且实测 **161/161 全部正确成对**，改排序键即可零迁移、零数据修改地把 13 笔
   交易恢复出来。方案 2 对旧数据无能为力。
2. **方案 1 不碰真实资金路径。** 改动全在只读聚合与纯函数里，风险面与「下单 / 平仓 / 风控」完全隔离，
   这在实盘项目里是决定性的。
3. **方案 2 作为第二步收口**，把 `ts` 恢复成「这笔成交真实发生的时刻」这一单一语义，顺带修好持仓时长、
   「今日 / 7d」时间窗口归日、以及行内 K 线的开平点位对齐 —— 这些都是方案 1 覆盖不到的次生问题。
   落地时必须保持 `barTs` 经 `payload["barTs"]` / `explain.meta.barTs` 继续传递，确保决策上下文与
   prompt 逐字节不变。
4. **方案 3 不采纳**：它把「ts 不可信」这个事实变成读层的隐式约定，制造第二套时间真源，
   与项目既有的「单一真源」约束（见 `executor.py` 关于盈亏口径真源的注释、CLAUDE.md 第五节）冲突。

### 落地时的门禁建议（对齐 CLAUDE.md 第七节「修 bug 必须先写复现测试」）

1. 先把 `repro/ts_order_repro.test.ts`（前端）与一条等价的后端用例（可参考
   `repro/repro_backend_history_drop.py`，并入 `services/quant/tests/test_ai_auto_history.py`）
   作为**先红后绿**的回归测试提交；
2. 修完后用 `repro/scan_inverted_pairs.py` 在真实库上复扫，要求
   「逐 run 差异」与「逐 group 差异」两段**输出为空**；
3. 回归：`services/quant` 的 ai-trade / live-auto 全套 + `apps/web` 的 `autoTradePnl.test.ts` /
   `tradeTrails` 相关用例 / `AiTradeStatsPanel.test.tsx`；重点确认 paper / backtest 的成交配对
   **逐字节不变**（这两个模式 `id` 序与 `ts` 序等价，实测零逆序，改排序键应当无差异）。

---

## 七、附：本次诊断产出的可复用件

| 文件 | 用途 |
|---|---|
| `repro/scan_inverted_pairs.py` | 对任意 `aragon.db` 扫描「因果配对 vs 面板配对」差异，修复前后各跑一次即为验收门禁 |
| `repro/ts_order_repro.test.ts` | 前端最小复现（真实生产数据两组），拷进 `apps/web/lib/` 即可 `vitest run` |
| `repro/repro_backend_history_drop.py` | 后端最小复现：临时 SQLite + 真实 `list_ai_trade_groups / get_ai_trade_group_detail` |

---

## 八、评审补证（reviewer verification，2026-07-22）

本节由评审节点补入：所有结论均在**同一份生产库副本**上独立复跑得出，脚本与命令一并给出，便于第三次复核。
取证方式与报告一致（先 `cp` 再读，避免 WAL 中途态）。

### 8.1 三条复现路径逐条实跑（均成立）

| 路径 | 实跑结果 |
|---|---|
| A 生产库扫描 | 与报告**逐字一致**：ai-trade `rows=323 / causal=161 / inverted=12 / bar-ts=203 / wall-ts=120`；逐 run 12 行；逐 group 6 组、`groups=16 affected=6`。唯一差异：本次输出多一行 `ai-backtest  4  2  0  4  0`（报告粘贴的输出漏了该模式，逆序为 0，不影响任何结论） |
| B 前端最小复现 | 按报告命令把 `ts_order_repro.test.ts` 拷入 `apps/web/lib/` 实跑 `vitest`：**2 个断言全部失败**，失败信息与报告一致（`expected [ [ 50896, 50894 ] ] to deeply equal [ [ 50892, 50894 ], [ 50896, 50898 ] ]`）。复核后已删除临时文件，工作树无残留 |
| C 后端最小复现 | 实跑输出与报告一致：`trades persisted in DB: 2` / `list tradeCount: 0 (expected 1)` / `realizedPnlUsd: 0.0 (expected 0.0574)` / `detail` 同 / `member metrics.totalReturn: 0.000915`（finalize 口径仍非零 → KPI 自相矛盾的指纹成立） |

### 8.2 反证清单：五类替代解释逐条排除

报告的根因是「读侧按 ts 配对」。为确认不是「配对模型本身不适用」或「落库真的丢了行」，补做以下检验
（脚本：本节各查询均直接跑在 `trades` / `strategy_runs` 上，口径与 `_pair_round_trips` 一致）：

| 替代解释 | 检验方法 | 实测结果 | 判定 |
|---|---|---|---|
| 存在**部分平仓 / 加仓**，单持仓 open→close 模型本就会丢行 | ① 统计 ai-trade 的 `side` 词表；② 统计 `side='close'` 且 `position_after != 0` 的行 | ① `{long: 82, short: 80, close: 161}`，无 `flat` / `reduce` 等第四种取值；② **0 条** | 排除 |
| **因果序本身**就不是「开-平」严格交替（模型不成立） | 按 `id` 升序逐 run 扫描相邻两笔的开/平性质 | 违例 **0 处**（无「连续两开」，无「连续两平」） | 排除 |
| `_persist_trade` 是 best-effort，**落库丢过行** | ① 开/平/因果对计数；② `metrics_json.tradeCount`（finalize 时的会话内存真值）与该 run 的 DB 行数逐 run 比对 | ① 开 162 / 平 161 / 因果对 161 ⇒ **孤儿平仓 0**、悬挂开仓 1（末笔未平的正常在场持仓）；② **111/111 零差异**（其中 57 个零成交 run，与报告"111"口径吻合） | 排除 |
| 跨标的 / 跨成员**错配**（R-2 失效） | 16 个 ai-trade group 内检查是否有重复 `inst_id` 的成员 run | **0 个 group 出现重复 instId** ⇒ 逐 run 分桶 ≡ 逐 instId 分桶 | 排除 |
| `ts` **并列**导致的次序不定 | 统计因果对内 `open.ts == close.ts` 的对数 | 仅 1 例（`run_id=1386`，`id=44411/44412`），`(ts, id)` 次键已正确处理 | 排除 |
| 其它模式也有同样问题（则不是 AI 自动交易特有） | 逐 mode 统计逆序 | backtest 24319 对 / paper 680 对 / live-auto 6 对 / ai-backtest 2 对，逆序**全部为 0** | 排除 |

### 8.3 计数口径校正：12 逆序对 ≠ 13 笔丢失

逐 run 把「丢失笔数」拆成两种成因（对内逆序 `close.ts < open.ts`；跨对重叠「后一对开仓 ts < 前一对平仓 ts」）：

```
run     bar    lost  intra_pair_inverted  inter_pair_overlap
1475    5m      1            1                    0
1477    5m      1            1                    0
1480    5m      1            1                    0
1534    15m     1            1                    0
1535    15m     1            1                    0
1542    30m     1            1                    0
1546    30m     1            1                    0
1547    15m     2            2                    0
1548    15m     1            0                    1     <-- 唯一的跨对重叠形态
1549    15m     1            1                    0
1551    15m     1            1                    0
1557    15m     1            1                    0
TOTAL          13           12                    1
```

`run_id=1548` 的完整证据（该 run 共 13 对，仅末尾这段出问题）：

```
causal: … (50892, 50894), (50896, 50898), (50906, 50907)      <-- 两对对内均正序
by ts : … (50896, 50894),                 (50906, 50907)      <-- 50892 被顶掉、50898 整笔消失
```

**12 例对内逆序的时钟组合是单一方向的**：`{(WALL, BAR): 12}` —— 全部是「墙钟开仓 → bar-ts 平仓」，
滞后 47 秒 ~ 2613 秒（43 分，30m 周期）。这是对 §3.1 写侧机制**最强的正面证据**：不是"两个时钟可能乱序"，
而是所有已发生的逆序都恰好来自「定时唤醒轮开仓（墙钟）+ K 线轮 / stop 平仓（bar 开盘 ts）」这一条组合，
与 §3.1 的路径表一一对应。

### 8.4 方案 3（读层逆序归一）实测：修复 0/13

「把 `close.ts` 抬到 `max(open.ts, close.ts)`」隐含一个循环依赖：**要知道抬到哪个 `open`，就得先知道配对结果，
而配对结果正是待求解的东西**。唯一可实现的近似是「按 ts 序取此前最近的一个 open 来抬」，实测：

```
run=1475 causal=3 byTs=2 clamped=2 identical_to_causal=False
run=1477 causal=2 byTs=1 clamped=1 identical_to_causal=False
…（12 个受影响 run 全部如此）…
run=1557 causal=1 byTs=0 clamped=0 identical_to_causal=False
```

12 个 run **无一恢复**，配对结果与「按 ts 直配」完全相同。两个直观反例：

- `run=1557`：ts 序里平仓（`50915`）排在开仓（`50914`）**之前**，归一时"此前最近的 open"根本不存在 → 不触发抬升 → 仍是孤儿平仓被丢弃；
- `run=1548`：两对**对内都不逆序**，归一条件从头到尾不成立 → 完全不触发。

若改成「按 `id` 序遍历再归一 `ts`」，那已经是在用因果序了 —— 即方案 1 加上一层多余的时间戳改写。
故方案 3 不是"治标"，而是**无效**；报告原先仅以设计洁癖（第二套时间真源）否决它，理由偏软，此处补上硬证据。

### 8.5 后端：最小切法与原方案的差异

- **只需改一行**：`ai_auto_repositories.py:58` 的排序键。`_pair_round_trips` 自带排序，与 SQL 返回顺序无关。
- **不要动** `_load_trades_by_run:176` 的 `order_by(Trade.ts.asc(), Trade.id.asc())`。保持 SQL 为 ts 序，
  则 `_aggregate_group:156` 的 `member_trades[-1].ts`（endedTs）与 `_group_started_ts:141` 的
  `trades_by_run[r.id][0].ts`（startedTs 第 3 级回退）**语义原样成立**，连带修改归零。
  报告原文选择改 SQL，因而不得不补 endedTs；且**漏列了 `_group_started_ts:141` 这个同类回退点**——
  它同样依赖"列表首元素 == 最早 ts"。改 SQL 的路线必须两处一起改，这正是应当避免的额外工作。
- **对既有用例零影响**：`test_ai_auto_history.py` 的全部成交夹具（`:79-82`、`:87-88`、`:92-93`、`:233`）
  均按 ts 升序插入，而 `id` 为插入序自增 ⇒ ts 序 ≡ id 序，改排序键后逐值不变。
- **孪生实现须标注**：`live_auto_repositories.py:48-56` 有一份逐字同构的 `_pair_round_trips`。live-auto
  单时钟、实测 0 逆序，本次**不改**；但提交说明须点名这处孪生代码，避免两边今后漂移。

### 8.6 前端：`id` 可得性矩阵与不可动的渲染顺序

| 消费路径 | 成交是否带 `id` | 数组顺序 | 依据 |
|---|---|---|---|
| 统计「当前」页（AI-trade 分组） | **无** | 快照序 + WS 到达序 = 因果序 | 快照来自 `session.py:1010` `list(self.executor.trades)`；成交字典由 `executor.py:766` `_make_trade` 构造，**无 `id` 键**；`useAiTradeGroupStream.ts:153/165` → `store.ts:783-794` 仅整体替换 / 尾部追加 |
| 统计「历史」页（列表 / 详情） | **有** | SQL `(ts, id)` 升序 | `ai_auto.py:221` `trade_out` → `schemas.py:851` `"id": t.id` |
| 主图 `overlayTrades`（`page.tsx:489-498` 五分支互斥） | 仅「单 run DB 查询」一支有 `id`；ai-trade / live-auto / paper 三支均无 | paper 多策略支为 `paperMulti.ts:100` 的 `flatMap`（**未排序**，按策略块拼接） | 各分支互斥 ⇒ **当前不存在 id 与非 id 混装的数组** |

由此得到两条实现约束：

1. **`indexTrades` 不能改**（理由见 §5 方案 1 的评审修正 2：`setMarkers` 的升序断言）。改动只落在
   `buildPairedTrails` 的遍历顺序上；`PairedTrail` 记录的是原数组下标（`tradeTrails.ts:82-86`），
   `buildTradeMarkerEvents` 按下标回查（`:30-34`），**配对顺序与渲染顺序天然解耦**。
2. `tradeOrder` 现有的 `trade.id ?? index`（`tradeTrails.ts:119-121`）在**同一数组内口径统一**时才安全；
   它把 DB 主键空间（约 5 万量级）与数组下标空间（0..n）混进同一个比较器，一旦将来出现混装数组即静默错序。
   实现因果序时应写成**整数组二选一**判定（全员有 `id` → 按 `id` 升序；否则按数组原序），而非逐元素回退。

**行为变化（须知情接受并补测）**：paper 多策略合并数组由「跨策略按 ts 交织配对」变为「按策略块顺序配对」。
方向上是改善（块内配对正确，仅块边界的悬挂开仓可能与下一块首个平仓错配；此前是全程交织），
但确属本缺陷之外的可见变化。根因（`buildPairedTrails` 不区分 `strategyId`）不在本次范围内。

**唯一需要改写的既有用例**：`apps/web/lib/tradeTrails.test.ts:54-65`
（"sorts trades by timestamp and stable fallback index before pairing"）——它构造的是「无 `id`、数组序与 ts 序
恰好相反」的合成输入，正是因果序配对要主动放弃的语义。其余用例逐条核对后不受影响
（`:67-85` 同 ts 反转、`:105-117` 同根开平、`:119-131` 不可变性，夹具的 `id` 序与 ts 序一致）。
另：`sortTradesForTrail` 仅被该测试引用，生产代码零调用。

### 8.7 验收门禁的不足（须一并升级）

- `scan_inverted_pairs.py` 第 2 / 3 段只比较**笔数**与**盈亏和**。理论上存在「笔数相同、盈亏和相同、
  但配对身份不同」的残留错配（本次生产库实测 0 例：12 个差异 run 恰好都同时体现为笔数差异），
  作为**修复后的验收门禁**应升级为「逐 run 比较 `{(entry.id, exit.id)}` 集合相等」，否则"门禁通过"≠"错配已消除"。
- 第 1 段的 bar 对齐判定用 `ts % bar_seconds == 0`，墙钟有 `1/bar_seconds` 的概率被误判为 bar 对齐；
  仅影响该列展示，不影响逆序与差异统计。

---

## 评审结论

**结论：根因认定成立；推荐修复由「方案 1」收敛为「方案 1′ 最小因果配对」，有条件通过（3 项必须落实）。**

### 一、对根因的判定

根因链**闭合**，可以据此动手：写侧 `trades.ts` 双时钟（§3.1，代码位置逐条复核无误：`session.py:465`（bar ts）、
`:370`（墙钟）、`:321`（墙钟）、`:265`（bar ts））→ 读侧按 `ts` 排序做单持仓配对（`ai_auto_repositories.py:58`、
`tradeTrails.ts:110-117`）→ 排序破坏因果交替 → 整笔丢弃或错配。正面证据是 12 例逆序**全部**呈
「墙钟开仓 → bar-ts 平仓」单一方向（§8.3），反面证据是五类替代解释被逐条排除（§8.2），
落库完整性 111/111 与「按 id 配对 161 笔平仓零孤儿」共同证明**数据没丢、只是没被统计**。

需要修正的只有**失效条件的表述**：不是「某对成交 `close.ts < open.ts`」这一种，而是「ts 序破坏了开→平交替」，
含**对内逆序 12 例**与**跨对重叠 1 例**（`run_id=1548`）。原文 §3.3 把 1548 归为逆序形态，与数据不符（已就地更正）。

### 二、最终推荐修复：方案 1′「最小因果配对」

> 一句话：**配对按因果序，显示按时间序** —— 只改「配对遍历顺序」这一件事，不动 SQL、不动渲染顺序、不动写侧。

必须落实的两处代码改动：

| # | 位置 | 改动 |
|---|---|---|
| **B1** | `services/quant/app/db/ai_auto_repositories.py:58` | `sorted(trades, key=lambda x: (x.ts, x.id or 0))` → `sorted(trades, key=lambda x: (x.id or 0))`。`_load_trades_by_run:176` 的 SQL 排序、`_aggregate_group:156` 的 endedTs、`_group_started_ts:141` 的 startedTs **一律不动** |
| **F1** | `apps/web/lib/tradeTrails.ts:71-91` `buildPairedTrails` | 遍历顺序由 `indexTrades(trades)` 改为新增的因果序 helper（整数组均有 `id` → 按 `id` 升序；否则按数组原序）。`indexTrades` **保持原样**，继续为 `buildTradeMarkerEvents` 提供 ts 升序的渲染顺序 |

必须一并落实的三项门禁：

- **T1（先红后绿）**：`repro/ts_order_repro.test.ts` 的两条断言并入 `apps/web/lib/autoTradePnl.test.ts`；
  `repro/repro_backend_history_drop.py` 的等价用例并入 `services/quant/tests/test_ai_auto_history.py`。先确认变红，再改代码。
- **T2**：改写 `tradeTrails.test.ts:54-65`（唯一编码 ts 主序配对的用例，§8.6），并补一条钉住 paper
  多策略合并数组新行为的用例。
- **T3**：`scan_inverted_pairs.py` 升级为**配对身份集合**比对（§8.7），修复后在真实库复扫，§2 / §3 两段输出须为空。

回归范围：`services/quant` 的 ai-trade / live-auto 全套；`apps/web` 的 `autoTradePnl.test.ts` /
`tradeTrails.test.ts` / `aiTradeHistory.test.ts` / `AiTradeStatsPanel.test.tsx`；重点确认 paper / backtest
的成交配对逐值不变（两者 `id` 序与 `ts` 序等价，实测 0 逆序）。

### 三、为什么是它，而不是别的

1. **只有因果排序键能救回已经发生的 13 笔。** `ts` 已经写坏且不可逆推，`trades.id` 完好（实测按 id 配对
   161 笔平仓零孤儿）；方案 2 只管未来，对 6 个已错的会话无能为力。
2. **它是真正的最小改动。** 两行级改动、零 SQL 改动、零数据迁移、零显示层补丁（时长钳制已存在，§5 修正 3）、
   不触碰下单 / 风控 / 平仓热路径。原方案 1 另外提出的 3 处改动经复核：改 SQL 排序**不必要**，
   endedTs 修补是**自己引入的连带债**（且漏了同类的 startedTs 回退点），改 `indexTrades` 会**引爆图表断言**
   （lightweight-charts `setMarkers` 要求 markers 按 time 升序，dev 构建直接抛断言）。收敛后这三处全部消失。
3. **方案 3 予以否决**：不是"治标"，是无效 —— 实测修复 0/13，且逻辑上循环依赖（§8.4）。
4. **方案 2 单独立项，不与本次捆绑。** 它确实是语义上的正解，也只有它能修好持仓时长、「今日/7d」归日、
   行内 K 线开平点位对齐；但它必须先把决策轮里「一形参两用」的 `ts` 拆成 `trade_ts` / `bar_ts`
   —— 该形参同时喂给 `executor.apply(ts=…)`（`session.py:756`）与 prompt 输入
   `_build_multi_timeframe_context(ts, ref_px)`（`session.py:852`，内部 `as_of_ts = ts + BAR_SECONDS[bar]`），
   直接撞上 prompt 冻结门禁与实盘资金路径。把它塞进本次修复会让一个只读层缺陷的修复横跨下单热路径，风险收益比不划算。

### 四、交接给实现节点的已知遗留（不要"顺手"处理）

- 被救回的 12 笔在明细行会显示「持仓 0秒」，行内 K 线的平仓点位画在开仓点位左侧，且 `canRenderLine=false`
  故不画开→平连线（三处图表消费者均已按此守卫：`CandleChart.tsx:426`、`TradeCandleChart.tsx:88`、
  `WeightBacktestChart.tsx:87`）。这是双时钟的残留、非本次引入，由方案 2 收口。
- 悬挂开仓 1 笔（末笔未平的在场持仓）被配对模型丢弃是**正确行为**，不要在本次改动里一并处理。
- `live_auto_repositories.py:48-56` 的孪生 `_pair_round_trips` 本次不改，提交说明须点名。
- paper 多策略合并数组的跨策略配对（`buildPairedTrails` 不区分 `strategyId`）是**既存缺陷**，
  本次只会让它变好一点，彻底修复不在本范围。
