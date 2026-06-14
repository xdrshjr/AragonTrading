# 左侧 K 线工作区重构方案（chart-workspace-layout）

> 版本：**v2**（已评审并就地修复，Subtask #1 产出；v1 为 Subtask #0 方案设计）
> 作者：Solution Architect（Anthropic 工程团队角色）
> 评审：Senior Reviewer（Anthropic 工程团队角色，Subtask #1）
> 适用迭代：多轮迭代任务「标准四阶段开发」第 2 轮
> 关联代码索引：`.claude-index/index.md`
> 状态：**有条件通过**——所有 P0/P1 已就地修复（详见 §0 评审记录、文末 §10 评审结论）

---

## 0. 评审记录（Review Notes，Subtask #1）

> 本节由评审节点（Subtask #1）逐节评审后插入。评审维度：**可行性 / 完整性 / 一致性 / 规模适配**。
> 评审者已对照真实代码核验方案中每一处「既有能力」断言（`lib/store.ts`、`lib/types.ts`、`lib/format.ts`、
> `components/ui/index.tsx`、`Chart/CandleChart.tsx`、`Chart/ChartToolbar.tsx`、`app/page.tsx`、`lib/compare.ts`、
> `Strategy/TradesTable.tsx`、`lib/chartSync.ts`、`lib/api.ts`，以及后端 `engine/simulator.py`/`schemas.py`）。
>
> **结论摘要：0 个 P0，6 个 P1（全部已就地修复并升版 v2），6 个 P2（不阻塞，列为放行条件）。**

| ID | 维度 | 严重度 | 问题 | 处置 |
|----|------|--------|------|------|
| R1 | 可行性·类型 | **P1** | §9.2 用共享 `Tabs`（`onChange:(key:string)=>void`、`active:string`）直接接窄类型 setter `setBottomTab:(v:'trades'\|'positions')=>void`，TS 参数逆变会报错（`string` 不可赋给 `'trades'\|'positions'`）；§7.2 把 `typecheck` 列为硬门 → 必修 | 已修：§5.1/§9.2 改为收窄包装 `onChange={(k)=>setBottomTab(k as 'trades'\|'positions')}`，并在 §5.1 注明共享 `Tabs` 的泛型签名约束 |
| R2 | 完整性·类型 | **P1** | OHLC 计算内嵌在组件里：`candle:Candle\|null`、`prevClose:number\|null` 时 `change` 可能为 `null`，而 `signed()`/`colorForReturn()` 形参是 `number`（见 `lib/format.ts`），既报 tsc 又缺空态渲染；且违背项目「纯函数 + 同名 .test.ts」约定 | 已修：抽出纯函数 `lib/ohlc.ts#computeOhlcStats`（空安全 + 除零守卫）+ `lib/ohlc.test.ts`；ChartHeaderBar 仅对非空数值着色/取符号，空态渲染「—」（见 §5.2、§4 文件清单、§7.1） |
| R3 | 完整性 | **P1** | §3.2(A) `displayCandle = hoverTs ? byTs.get(hoverTs) : candles.at(-1)`：切标的/周期/视图后 `hoverTs` 残留，`byTs.get` 落空会让有数据却显示空 | 已修：改为空值合并回落 `byTs.get(hoverTs) ?? candles.at(-1) ?? null`，并在 candles 重置 effect 内清 `hoverTs`；`prevClose` 用 `tsIndex` 映射 O(1) 取前一根（见 §3.2(A)） |
| R4 | 一致性·规模 | **P1** | 左工作区新结构 + hover/派生逻辑全部内联进已 ~294 行的 `page.tsx`/~250 行的 `Page()`，进一步突破 CLAUDE.md §二「单文件<800 行、单函数<50 行、单一职责」 | 已修：新增 `components/Chart/ChartWorkspace.tsx` 承载左区结构 + 自持 `hoverTs`/OHLC 派生；`page.tsx` 退回为薄编排者，按单一 props 对象下传（见 §3.1、§4、§9.2） |
| R5 | 完整性·上线 | **P1** | §8 仅口头提「窄屏」，§9.2 骨架硬编码 `flex-[8]/flex-[2]`、`w-1/5`，无断点类；下区 20%（如 360px 视口 ≈72px）会被压塌，且与 `TradesTable` 内置 `max-h-[320px]` 叠加产生空隙/截断 | 已修：明确「桌面/Electron 优先」（与索引一致），给出具体 `lg:` 策略 + 下区 `min-h` 下限；说明 `TradesTable` 在下区由外层容器滚动、内置 `max-h` 仅作上限兜底（见 §2.1、§8、§9.2） |
| R6 | 一致性·事实 | **P1** | §8 风险行「`positionAfter` 符号语义未知」与代码不符：`engine/simulator.py:227` `position_after=(qty if side=='long' else -qty)`、`:244` 平仓置 `0.0`，并原样透传（`schemas.py:78`、`paper/session.py:244`）→ 约定明确为 **+qty 多 / −qty 空 / 0 平**，且当前引擎是「整开整平、无加/减仓」 | 已修：更正该风险行，§6.1 注明后端真实持仓模型；`deriveOpenPosition` 仍按 side 重建（与 `positionAfter` 一致），并新增「与 `positionAfter` 符号一致」断言测试（见 §6.1、§7.1、§8） |
| R7 | 规模适配 | P2 | `positionAfter` 约定既已明确，末笔 `positionAfter` 即权威净持仓（含符号/量），可直接取用、仅需从对应开仓笔重建 `avgEntry`，比整段序列回放更稳健；`averages_entry_on_add`/`reduces_on_partial_close` 是当前后端不产生的行为（为未来多笔成交护栏） | 列为放行条件：开发节点可在 §6.1 末以 `positionAfter` 交叉校验 derive 结果（已加一致性测试）；两条「加仓/部分平」测试保留为前瞻护栏并加注释 |
| R8 | 完整性 | P2 | §5.1 `InstrumentListProps` 未含标的列表本身；移除工具栏标的查询后，列表来源未明确 | 列为放行条件：`InstrumentList` 自身跑 `useQuery(["instruments"])`（React Query 同键去重，与工具栏并存无副作用），§4 第 5 项已补注 |
| R9 | 完整性 | P2 | OHLC 横栏只在「直接悬停主图」时更新：`lib/chartSync.ts` 对其余图用 `setCrosshairPosition`（不会回触 `subscribeCrosshairMove`），故悬停副图（MACD 等）不会刷新 OHLC | 列为放行条件：属可接受限制，§3.2(A) 已注明；如需联动可后续在 sync 组回调里旁路一份 ts |
| R10 | 一致性 | P2 | `Overlay` 目前仅 `CandleChart.tsx` 内部定义/使用，**无外部引用**；§6.3「保留 `export type { Overlay }` 兼容旧引用」非必需（无害） | §6.3 已注明可干净上移，re-export 仅为防御 |
| R11 | 规模适配 | P2 | store `version` 2→3 属冗余保险：新增的可选字段即便缺省，zustand 持久化浅合并也会从初始 state 回落默认值 | §6.2 已澄清（保留升版，定位为「显式稳妥」） |
| R12 | 完整性 | P2 | EquityChart 三种模式下的空态（回测未跑/实时未起/对比未跑）需各自有占位提示 | §5.1/§7.3 已要求 `emptyHint` 覆盖三态 |

---

## 1. 概述（Overview）

本轮在第 1 轮「K 线技术指标体系（MA/EMA/BOLL 主图叠加 + MACD/RSI/KDJ/VOL 副图窗格）」已完成的基础上，**不回滚任何既有功能**，对左侧 K 线工作区做一次结构性重排，目标是把「行情视图 / 净值视图 / 行情数值 / 交易数据」从当前「叠加在同一张图上」的混合形态，拆分为「分区、分页签、各司其职」的专业交易终端布局。三条核心诉求：(1) 把**净值曲线从 K 线图上剥离**，改为页签切换显示，避免两类量纲（价格 6 万级 vs 归一化净值）挤在一张图上互相干扰；(2) 在工具栏正下方新增一条 **OHLC 数值横栏**，实时展示当前/被选中 K 线的 开/高/低/收/涨跌/振幅，并随十字光标选择联动切换、按涨跌着色；(3) 把左侧面板整体调整为**上下 80%/20% 布局**，上区再做左右 20%/80% 切分（左为标的列表、右为图表区），下区为「成交记录 / 当前持仓」数据页签。

从架构上看，这次改动集中在前端 `apps/web`，**不改动后端业务逻辑与数据库**，遵守项目「绝不造假数据」的硬规则：所有展示的数字（OHLC、涨跌、持仓、净值）均来自后端真实接口（OKX 实时/历史行情、回测/模拟产出的 trades 与 equity），缺数据时显示「—」或「无持仓」而非编造。当前持仓由已有的 `trades` 数据在前端纯函数推导得到（不新增后端接口），从而把改动面与回归风险压到最小。可选增强项「合约行情快照（/tickers）」单独标注为 P2，核心布局不依赖它即可成立。

设计原则上严格对齐仓库既有约定：复用 `components/ui` 的 `Tabs`/`Badge`/`Switch` 原子组件、`lib/format` 的格式化函数、`lib/store` 的 Zustand+persist 选择态模式、`lightweight-charts` 的图表挂载/卸载范式与 `chartSync` 同步组；新增组件保持单文件 < 800 行、单函数 < 50 行、命名自解释（动词开头 / is·has 前缀布尔）。本节之后给出可逐行落地的技术设计、文件清单、接口、数据模型、测试与风险。

---

## 2. 需求解读与布局总览

第 2 轮三条需求存在表述上的轻微交叠（需求 1 说「最下方加 K 线/净值 页签」，需求 3 又精确定义了「下区 20% 为 成交记录/当前持仓」）。作为方案，统一**收敛为一套自洽布局**：需求 1 的「K 线/净值」页签作为**上区图表区顶部的视图切换器**（剥离净值叠加，这是其核心意图）；需求 3 的「成交记录/当前持仓」页签作为**下区 20% 面板**。需求 2 的 OHLC 横栏紧贴工具栏下方。

### 2.1 目标布局（lg 及以上桌面 / Electron 优先）

> **定位（R5）**：本产品主载体为桌面端与 Electron 壳（见 `.claude-index/index.md` 与桌面打包迭代），
> 故 80/20 + 20/80 比例布局以 `lg` 及以上为一等公民；`lg` 以下为优雅降级（见 §2.3），不追求等比。

```
左侧 <section>（占 grid 左列，h-full，flex-col）
┌──────────────────────────────────────────────────────────────────┐
│  上区  flex-[8]（≈80% 高）  flex-row                                  │
│ ┌──────────────┬───────────────────────────────────────────────┐ │
│ │ 标的列表 20%   │ 图表区 80%  flex-col                            │ │
│ │ InstrumentList│ ┌───────────────────────────────────────────┐ │ │
│ │ （竖向可点切换）│ │ ChartToolbar（周期/指标/日期/区间/模式/状态）  │ │ │
│ │  BTC-USDT-SWAP│ ├───────────────────────────────────────────┤ │ │
│ │  ETH-USDT-SWAP│ │ [缓存样例 banner——条件渲染]                  │ │ │
│ │  SOL-USDT-SWAP│ ├───────────────────────────────────────────┤ │ │
│ │  …（实时高亮）  │ │ 图表头横栏：[K线价格走势|净值] ··· OHLC 数值   │ │ │ ← 需求1+需求2
│ │               │ ├───────────────────────────────────────────┤ │ │
│ │               │ │  price 视图 → <CandleChart/>（K线+指标+marker）│ │ │
│ │               │ │  equity 视图 → <EquityChart/>（净值/对比叠加） │ │ │
│ │               │ └───────────────────────────────────────────┘ │ │
│ └──────────────┴───────────────────────────────────────────────┘ │
├──────────────────────────────────────────────────────────────────┤
│  下区  flex-[2]（≈20% 高）  flex-col                                  │
│  [成交记录 | 当前持仓]                                                 │ ← 需求3
│  trades 视图 → <TradesTable/>   positions 视图 → <PositionPanel/>     │
└──────────────────────────────────────────────────────────────────┘
右侧 <aside>：<StrategyPanel/>（保持不变，本轮不动）
```

### 2.2 关键交互

- **标的切换**：点击 `InstrumentList` 某行 → `setInst(instId)`（沿用既有 store action，会连带 `runId:null` 触发重取）。当前标的行高亮，并显示其实时最新价与区间涨跌（来自已加载 candles / 实时流，真实数据）。
- **视图切换**：图表头左侧 `[K线价格走势 | 净值]` 段控件 → 写 `store.chartView`；price 渲染 `<CandleChart/>`，equity 渲染 `<EquityChart/>`，二者不再叠加。
- **OHLC 联动**：鼠标在 K 线图上移动 → `CandleChart` 通过 `subscribeCrosshairMove` 回吐被选中 K 线的 `ts`；page 据此查出该根 candle 与其前收，刷新 OHLC 横栏；光标移出则回落到最新一根。
- **下区数据**：`[成交记录 | 当前持仓]` 段控件 → 写 `store.bottomTab`；成交记录复用 `<TradesTable/>`，当前持仓由 `deriveOpenPosition(trades, markPrice)` 推导渲染。

### 2.3 响应式降级与防塌陷（R5，必须落地的具体约束）

为避免下区 20% 在中小视口被压塌、以及 `TradesTable` 内置 `max-h-[320px]` 与外层高度叠加产生空隙/截断，落地需满足：

- **`lg` 及以上**：左 `<section>` 为 `flex-col`，上区 `flex-[8]` + 下区 `flex-[2]`；上区内 `flex-row`，标的列表 `w-1/5 min-w-[140px] max-w-[240px]`、图表区 `flex-1 min-w-0`。
- **下区高度下限**：下区容器加 `min-h-[140px]`，保证「页签条 + 至少 2~3 行数据」可见；其内部滚动区用 `min-h-0 flex-1 overflow-auto`，由**外层容器承担滚动**。`TradesTable` 复用时其内置 `max-h-[320px]` 仅作上限兜底，不与外层冲突（取两者较小者）。
- **`lg` 以下（窄屏 / 移动）**：取消固定 80/20，改为纵向自然堆叠——标的列表降为顶部**横向滚动 chip 条**（`flex-row overflow-x-auto`），图表区给 `min-h-[320px]`，下区页签置于其后并保持 `min-h-[140px]`；全程以 `min-w-0` / `min-h-0` 防 flex 子项塌陷。
- 标的列表、下区面板在任何断点都保留可访问的滚动，不因比例丢失内容。

---

## 3. 技术设计（Technical Design）

### 3.1 架构与数据流

```
useAppStore（新增 chartView/bottomTab）
        │
page.tsx（薄编排者：仅数据获取 / 派生 / 透传，不再内联左区布局，R4）
  ├─ 拉取：candlesQ / equityQ / tradesQ / health / compareQ（沿用现有 useQuery）
  ├─ 派生：candles, overlayEquity, overlayTrades, overlays, markPrice, lastPrice, sessionChangePct
  └─ <ChartWorkspace {...一个 props 对象} />   ← 左 <section> 整体下沉到此组件（单一 props，规避 5 形参上限）

ChartWorkspace.tsx（左侧工作区容器，自持视图内本地态）
  ├─ 本地态：hoverTs（来自 CandleChart 十字光标），并据此派生 displayCandle / prevClose（R3）
  ├─ 上区（flex-[8]）
  │   ├─ <InstrumentList active=instId onSelect=setInst lastPrice=… />（左20%，自跑 instruments 查询，R8）
  │   └─ 图表区（右80%）
  │       ├─ <ChartToolbar/>（移除标的 Select，其余不变）
  │       ├─ <ChartHeaderBar view=chartView onView=setChartView candle=displayCandle prevClose=… />（需求1+2）
  │       └─ chartView==='price' ? <CandleChart onCrosshairChange=setHoverTs/>（不再传 equity/overlays）
  │                              : <EquityChart equity=overlayEquity overlays=overlays/>
  └─ 下区（flex-[2]，min-h-[140px]）
      └─ bottomTab==='trades' ? <TradesTable trades=overlayTrades/>
                              : <PositionPanel trades=overlayTrades markPrice=markPrice instId=instId/>
```

> **R4 落地约束**：`ChartWorkspace` 接收**单一 props 对象**（不超过 CLAUDE.md §二「形参 ≤5」——组件 props 计为一个对象），
> 并**自持** `hoverTs` 及其派生（`displayCandle`/`prevClose`），把新引入的复杂度封装在组件内；`page.tsx` 增量仅为
> 数据派生 + 一行 `<ChartWorkspace/>`，文件与函数尺寸均退回阈值内。`chartView/bottomTab` 仍读自 `useAppStore`（持久化）。

数据来源全部为既有真实链路：
- **candles**：`api.candlesWithMeta` / 实时 `marketWS`（已存在），`displayCandle = hoverCandle ?? candles.at(-1)`。
- **equity / overlays**：回测 `equityQ.data`、实时 `liveEquity`、对比 `compareQ.data`（已存在），仅渲染位置从 CandleChart 迁到 EquityChart。
- **trades**：回测 `tradesQ.data`、实时 `liveTrades`（已存在 `overlayTrades`）。
- **markPrice**：`lastPrice ?? candles.at(-1)?.c ?? null`。
- **当前持仓**：`deriveOpenPosition(overlayTrades, markPrice)` 纯前端推导，无新增网络请求。

### 3.2 关键代码路径与执行时序

**(A) OHLC 联动时序**
1. `CandleChart` 挂载时在创建 effect 内 `chart.subscribeCrosshairMove(handler)`；`handler(param)` 取 `param.time`（被悬停 K 线的秒级 ts），经 `onCrosshairChangeRef.current?.(ts ?? null)` 回吐（用 ref 持有最新回调，避免重订阅）。注意 `param.time` 仅在**直接悬停主图**时有值；悬停副图（MACD/RSI/KDJ/VOL）由 `chartSync` 用 `setCrosshairPosition` 同步，不会回触本订阅 → OHLC 仅随主图悬停刷新（R9，可接受）。
2. `ChartWorkspace` 持有 `hoverTs` 状态，并用空值合并**保证有数据时不空显**（R3）：
   ```ts
   const byTs = useMemo(() => new Map(candles.map((c) => [c.ts, c])), [candles]);
   const tsIndex = useMemo(() => new Map(candles.map((c, i) => [c.ts, i])), [candles]);
   const displayCandle = (hoverTs != null ? byTs.get(hoverTs) : undefined) ?? candles.at(-1) ?? null;
   const di = displayCandle ? tsIndex.get(displayCandle.ts) ?? -1 : -1;
   const prevClose = di > 0 ? candles[di - 1].c : (displayCandle?.o ?? null);
   ```
   `byTs.get(hoverTs)` 落空（切标的/周期/视图后 `hoverTs` 残留）时**回落最新根**而非显示空；`prevClose` 经 `tsIndex` O(1) 取前一根，首根回落自身开盘价。
3. `<ChartHeaderBar>` 接收 `candle/prevClose`，调用纯函数 `computeOhlcStats(candle, prevClose)`（见 §5.2、`lib/ohlc.ts`）得到 `{change, changePct, amplitude}`（均 `number|null`），渲染 OHLC + 涨跌 + 振幅，**仅对非空数值** `colorForReturn(change)` 着色，空态渲染「—」（R2）。
4. 切标的/周期导致 candles 重置时，`hoverTs` 在对应 effect 中 `setHoverTs(null)` 清空，OHLC 回落最新根；切视图（price↔equity）卸载/重挂 CandleChart 不依赖残留 `hoverTs`（已由步骤 2 的回落兜底）。

**(B) 视图剥离时序**
1. price 视图：page 传 `equity={undefined} overlays={undefined}` 给 `CandleChart` → 既有 equity effect 走 `setData([])`，叠加被自然清空；同时 CandleChart 在 `!hasEquity` 时隐藏左价轴（新增一行 `applyOptions`）。
2. equity 视图：page 卸载 `CandleChart`、挂载 `EquityChart`（二者条件渲染，互斥）；`EquityChart` 用独立 `lightweight-charts` 实例画 equity 折线或归一化 overlays。
3. 切换 view 仅在前端切换组件，不重新请求数据（数据已在 React Query 缓存）。

**(C) 当前持仓推导**：见 §6.1 纯函数 `deriveOpenPosition`，按 `ts` 升序遍历 trades，维护有符号持仓量与加权开仓均价，末态即当前持仓；`markPrice` 给出浮动盈亏。

### 3.3 复用与新增边界

- **复用**：`Tabs/Badge/Switch`（ui）、`fmtPrice/fmtTime/fmtNum/fmtPct/signed/colorForReturn`（format）、`normalizeEquity/colorForIndex`（compare）、`createSyncGroup`（仅 CandleChart 内部，EquityChart 自成单图不入组）、`TradesTable`（原样复用）。
- **新增**：`ChartWorkspace`（左区容器，R4）/ `InstrumentList` / `ChartHeaderBar`(含 OHLC) / `EquityChart` / `PositionPanel` 组件，`lib/position.ts` 推导函数，`lib/ohlc.ts` OHLC 统计纯函数（R2），store 两个视图态字段。
- **修改**：`page.tsx`（退为薄编排者，下沉左区到 ChartWorkspace）、`CandleChart.tsx`（十字光标回调 + 左价轴隐藏）、`ChartToolbar.tsx`（移除标的 Select）、`store.ts`（新增视图态）、`types.ts`（`Overlay` 上移 + 可选 `Ticker`）。

---

## 4. 文件 / 模块变更计划

| # | 文件 | 动作 | 一句话意图 | 优先级 |
|---|------|------|-----------|--------|
| 1 | `apps/web/lib/store.ts` | 改 | 新增 `chartView:'price'\|'equity'` 与 `bottomTab:'trades'\|'positions'` 状态、setter 与 partialize 持久化 | P0 |
| 2 | `apps/web/lib/types.ts` | 改 | `Overlay` 类型上移至此（供 CandleChart/EquityChart 共享）；新增可选 `Ticker` 类型（P2 用） | P0 |
| 3 | `apps/web/lib/position.ts` | 新增 | `deriveOpenPosition(trades, markPrice, asOfTs?)` 纯函数 + `OpenPosition` 类型 | P0 |
| 4 | `apps/web/lib/position.test.ts` | 新增 | Vitest 覆盖：开多/开空/加仓均价/部分平/全平归零/浮盈正负/空数组/与 `positionAfter` 符号一致（R6/R7） | P0 |
| 5 | `apps/web/components/Chart/InstrumentList.tsx` | 新增 | 左 20% 竖向标的列表，点击 `setInst`，当前行高亮 + 实时价/涨跌（真实数据，缺则「—」）。**自跑 `useQuery(["instruments"])`** 取列表（与工具栏同键去重，R8） | P0 |
| 6 | `apps/web/components/Chart/ChartHeaderBar.tsx` | 新增 | 工具栏下方横栏：左 `[K线价格走势\|净值]` 视图页签，右 OHLC/涨跌/振幅 数值，按涨跌着色；OHLC 统计调 `lib/ohlc.ts`，空态「—」（R2） | P0 |
| 7 | `apps/web/components/Chart/EquityChart.tsx` | 新增 | 独立净值图：回测/实时单条 equity，对比多条归一化 overlays；空态/加载/错误占位（三模式空态，R12） | P0 |
| 8 | `apps/web/components/Strategy/PositionPanel.tsx` | 新增 | 当前持仓卡片：合约/方向/持仓量/开仓均价/标记价/浮动盈亏(额+%)，无持仓显「当前无持仓」 | P0 |
| 9 | `apps/web/components/Chart/CandleChart.tsx` | 改 | 新增 `onCrosshairChange?(ts)` 十字光标订阅；`!hasEquity` 时隐藏左价轴；不再被传入 equity/overlays（price 视图） | P0 |
| 10 | `apps/web/components/Chart/ChartToolbar.tsx` | 改 | 移除标的 `Select` 与 instruments 查询（选择改由 InstrumentList 承担），其余控件保持 | P1 |
| 11 | `apps/web/components/Chart/ChartWorkspace.tsx` | 新增 | 左侧工作区容器（R4）：80/20 上下 + 上区 20/80 左右；自持 `hoverTs`/OHLC 派生；接线视图页签 + 下区数据页签。单一 props 对象入参 | P0 |
| 12 | `apps/web/app/page.tsx` | 改 | 退为薄编排者：保留数据获取/派生，把左 `<section>` 整体替换为 `<ChartWorkspace .../>`（R4） | P0 |
| 13 | `apps/web/lib/ohlc.ts` | 新增 | `computeOhlcStats(candle, prevClose)` 纯函数 + `OhlcStats` 类型：空安全 + 除零守卫，返回 `{change, changePct, amplitude}`（R2） | P0 |
| 14 | `apps/web/lib/ohlc.test.ts` | 新增 | Vitest 覆盖：正常涨/跌/平、`prevClose<=0`、`candle/prevClose=null`、振幅口径（R2） | P0 |
| 15 | `apps/web/lib/instruments.ts` | 新增 | 抽出 `buildInstOptions(instruments)`（原 ChartToolbar 私有），供 InstrumentList 复用 | P1 |
| 16 | `apps/web/lib/api.ts` | 改 | （P2）新增 `tickers(instType)` 方法 | P2 |
| 17 | `services/quant/app/okx/rest_client.py` | 改 | （P2）新增 `get_tickers(instType)` 调 OKX `GET /api/v5/market/tickers` | P2 |
| 18 | `services/quant/app/api/market.py` | 改 | （P2）新增 `GET /tickers` 路由，返回 last/open24h/changePct | P2 |
| 19 | `services/quant/app/schemas.py` | 改 | （P2）新增 `TickerOut` 响应模型 | P2 |

> P0 = 本轮必须；P1 = 强烈建议（不做会留交互瑕疵）；P2 = 可选增强，缺失时 InstrumentList 优雅降级为「仅当前标的有价、其余仅符号」。下区/上区核心布局与三条需求的达成**不依赖** P2。

---

## 5. 接口设计（Interface Design）

### 5.1 组件 Props（前端契约）

```ts
// InstrumentList.tsx
interface InstrumentListProps {
  active: string;                       // 当前 instId
  onSelect: (instId: string) => void;   // = store.setInst
  lastPrice?: number | null;            // 当前标的实时价（仅高亮行展示）
  changePct?: number | null;            // 当前标的区间涨跌幅（来自 candles 首末，真实）
  tickers?: Record<string, Ticker>;     // P2：全标的快照；缺省时其余行不显价
}

// ChartHeaderBar.tsx —— 需求 1 视图页签 + 需求 2 OHLC 横栏
interface ChartHeaderBarProps {
  view: 'price' | 'equity';
  onView: (v: 'price' | 'equity') => void;
  candle: Candle | null;                // 被选中/最新一根（price 视图用）
  prevClose: number | null;             // 计算涨跌/振幅的昨收基准
  equitySummary?: { last: number | null; ret: number | null }; // equity 视图右侧摘要
}

// EquityChart.tsx
interface EquityChartProps {
  equity?: EquityPoint[];               // 回测/实时单条
  overlays?: Overlay[];                 // 对比多条（归一化）
  theme: 'light' | 'dark';
  loading?: boolean;
  error?: string | null;
  emptyHint?: string;                   // 默认「运行回测/模拟后显示净值曲线」
}

// PositionPanel.tsx
interface PositionPanelProps {
  trades?: Trade[];
  markPrice?: number | null;
  instId: string;
}

// CandleChart.tsx 新增 prop
onCrosshairChange?: (ts: number | null) => void; // 悬停 K 线秒级 ts；移出为 null
```

> **共享 `Tabs` 的类型约束（R1，必须遵守）**：`components/ui` 的 `Tabs` 签名为
> `{ tabs:{key:string;label:string}[]; active:string; onChange:(key:string)=>void }`（已核验源码）。
> 其 `onChange` 形参是宽类型 `string`，**不能**直接接窄类型 setter（`setBottomTab:(v:'trades'|'positions')=>void`、
> `setChartView:(v:'price'|'equity')=>void`）——TS 参数逆变会报 `string` 不可赋给字面量联合。落地必须用收窄包装：
> ```tsx
> <Tabs tabs={BOTTOM_TABS} active={bottomTab} onChange={(k) => setBottomTab(k as 'trades' | 'positions')} />
> ```
> `ChartHeaderBar` 内若复用 `Tabs` 渲染 `[K线价格走势|净值]`，同样在组件内部收窄后调 `onView`。
>
> **`ChartHeaderBar` 空安全（R2）**：`candle` 可能为 `null`（无数据 / equity 视图）、`prevClose` 可能为 `null`（首根 / 无基准）。
> 组件**必须**经纯函数 `computeOhlcStats(candle, prevClose)`（§5.2）取得 `{change, changePct, amplitude}`（均可空），
> 仅对非空数值调用 `signed(...)` / `colorForReturn(...)`（二者形参均为 `number`，见 `lib/format.ts`），其余一律渲染「—」。
> equity 视图下隐藏 OHLC、改示 `equitySummary`。

### 5.2 OHLC 数值计算约定（抽为纯函数 `lib/ohlc.ts`，R2）

为满足 §7.2 typecheck 硬门、规避 `signed/colorForReturn` 不接受 `null` 的类型错误，并对齐项目「纯函数 + 同名 `.test.ts`」约定（`format.ts`/`compare.ts`/`indicators.ts` 皆然），OHLC 统计**不内嵌组件**，而抽到 `lib/ohlc.ts`：

```ts
// lib/ohlc.ts
import type { Candle } from "./types";
export interface OhlcStats {
  change: number | null;     // c - prevClose
  changePct: number | null;  // change / prevClose（prevClose>0 时）
  amplitude: number | null;  // (h - l) / prevClose（prevClose>0 时；A 股口径：最高−最低相对昨收）
}
export function computeOhlcStats(candle: Candle | null, prevClose: number | null): OhlcStats {
  if (!candle || prevClose == null) return { change: null, changePct: null, amplitude: null };
  const change = candle.c - prevClose;
  const usable = prevClose > 0;
  return {
    change,
    changePct: usable ? change / prevClose : null,
    amplitude: usable ? (candle.h - candle.l) / prevClose : null,
  };
}
```

- **着色**：仅当 `change != null` 时 `colorForReturn(change)`（>0 绿 `--long`，<0 锈 `--short`，=0 `--muted`），与全仓「绿涨锈跌」既有约定一致（不引入红绿反转）；`change == null` 用 `--muted` 并显「—」。
- **展示字段顺序**：`时间 · 开 · 高 · 低 · 收 · 涨跌(额 / 幅) · 振幅`；价格用 `fmtPrice`、百分比用 `fmtPct`（二者本就空安全返回「—」），涨跌额仅在非空时用 `signed(change)`，否则「—」。
- 单测见 §7.1 `ohlc.test.ts`。

### 5.3 （P2）REST：合约行情快照

```
GET /api/py/tickers?instType=SWAP
200 OK
[
  { "instId": "BTC-USDT-SWAP", "last": 64210.5, "open24h": 63010.0, "changePct": 0.0190 },
  ...
]
```
- 后端 `market.py` 调 `OKXRestClient.get_tickers(instType)` → OKX `GET /api/v5/market/tickers?instType=SWAP`；`changePct = (last-open24h)/open24h`；沿用既有 `OKXError → 502` 异常处理与 `X-Aragon-Data-Source` 语义。
- 不可达时前端 InstrumentList 不显价（不回退假数据），符合「真实数据」硬规则。

---

## 6. 数据模型（Data Model）

### 6.1 持仓推导（前端内存形态）

```ts
// lib/position.ts
export interface OpenPosition {
  side: 'long' | 'short';
  size: number;             // 持仓量（正数量级）
  avgEntry: number;         // 开仓加权均价
  markPrice: number | null; // 标记价
  unrealizedPnl: number | null; // (mark-avg)*signedSize
  unrealizedPct: number | null; // unrealizedPnl / (avgEntry*size)
}

/**
 * 由成交序列推导「当前未平仓持仓」。纯函数、确定性、可单测。
 * 约定：side 'long'=开/加多，'short'=开/加空，'close'=平仓（向 0 收敛）。
 * asOfTs 给定时只考虑 ts<=asOfTs 的成交（可用于「按选中时点回放持仓」，P2）。
 */
export function deriveOpenPosition(
  trades: Trade[],
  markPrice: number | null,
  asOfTs?: number,
): OpenPosition | null {
  const rows = trades
    .filter((t) => asOfTs == null || t.ts <= asOfTs)
    .slice()
    .sort((a, b) => a.ts - b.ts);
  let size = 0;       // 有符号：+多 / -空
  let avgEntry = 0;
  const EPS = 1e-9;
  for (const t of rows) {
    if (t.side === 'close') {
      const dir = Math.sign(size);
      const remaining = Math.abs(size) - t.qty;
      if (remaining <= EPS) { size = 0; avgEntry = 0; }
      else { size = dir * remaining; }            // 平仓不改均价
      continue;
    }
    const dir = t.side === 'long' ? 1 : -1;
    if (size === 0 || Math.sign(size) === dir) {  // 开/同向加仓 → 加权均价
      const absOld = Math.abs(size);
      avgEntry = (avgEntry * absOld + t.price * t.qty) / (absOld + t.qty);
      size += dir * t.qty;
    } else {                                       // 反向以 open 入场（少见）→ 视作减仓/反手
      const remaining = Math.abs(size) - t.qty;
      if (remaining > EPS) { size = Math.sign(size) * remaining; }
      else if (remaining < -EPS) { size = dir * -remaining; avgEntry = t.price; }
      else { size = 0; avgEntry = 0; }
    }
  }
  if (Math.abs(size) <= EPS) return null;          // 空仓
  const mag = Math.abs(size);
  const pnl = markPrice != null ? (markPrice - avgEntry) * size : null;
  const pct = markPrice != null && avgEntry > 0 ? (pnl as number) / (avgEntry * mag) : null;
  return { side: size > 0 ? 'long' : 'short', size: mag, avgEntry, markPrice, unrealizedPnl: pnl, unrealizedPct: pct };
}
```

> 说明：浮动盈亏用**有符号** `size` 一式两用——多头 `mark>avg` 为正、空头 `mark<avg` 为正，方向语义自洽。回测末态通常平仓 → 返回 `null`（诚实显示「当前无持仓」）；实时模拟持仓中则有真实数据。
>
> **后端真实持仓模型与 `positionAfter` 约定（R6/R7，已核验源码）**：`engine/simulator.py:227` 在开仓时 `position_after=(qty if side=='long' else -qty)`、`:244` 平仓置 `0.0`，并经 `schemas.py:78`/`paper/session.py:244` 原样透传。由此可知：
> 1. **`positionAfter` 符号约定明确**：`+qty=多 / −qty=空 / 0=平`（§8 风险表已据此更正）。
> 2. **当前引擎是「整开整平」**：单开仓笔即满仓、平仓笔即清零，**不产生加仓 / 部分平仓 / 反手**。故 `deriveOpenPosition` 对真实数据退化为「最后一段未平开仓笔 → {side, size=|positionAfter|, avgEntry=该开仓价}」；代码中的加权均价 / 部分平 / 反手分支为**前瞻护栏**（未来若支持多笔成交可直接复用），当前不会被触发。
> 3. **可选简化（P2/R7）**：末笔 `trades.at(-1)?.positionAfter` 即权威净持仓（含符号与量），开发节点可用它交叉校验或直接驱动「方向 / 持仓量」，`deriveOpenPosition` 仅负责重建 `avgEntry`；§7.1 已加「derive 结果与末笔 `positionAfter` 符号 / 量一致」断言测试，防止重建逻辑与后端漂移。

### 6.2 Store 扩展（持久化形态）

```ts
// store.ts 新增
chartView: 'price' | 'equity';   // 默认 'price'
bottomTab: 'trades' | 'positions'; // 默认 'trades'
setChartView: (v) => set({ chartView: v });
setBottomTab: (v) => set({ bottomTab: v });
// partialize 增列 chartView / bottomTab；version 升至 3，migrate 缺省回落默认值
```

> **版本升级说明（R11）**：当前 store `version: 2`（已核验 `lib/store.ts`）。新增的 `chartView/bottomTab` 是**可选追加字段**——
> 即便不升版，zustand `persist` 浅合并也会让缺失字段从初始 state 回落到默认值（`'price'`/`'trades'`），故 2→3 升版属
> **显式稳妥（belt-and-suspenders）**，非功能必需；保留升版并在 `migrate` 对 `version < 3` 补默认值，与既有 v1→v2 迁移同构。

### 6.3 类型迁移

`Overlay`（`{ id: number; name; color; equity }`）由 `CandleChart.tsx` 上移至 `lib/types.ts`，`CandleChart`/`EquityChart`/`ChartWorkspace` 统一从 types 导入。**核验结论（R10）**：全仓搜索 `Overlay` 仅 `CandleChart.tsx` 内部定义/使用，**无任何外部引用**，故可干净上移；CandleChart 的 `export type { Overlay }` re-export 仅作防御，非必需（无害可保留）。（P2）新增 `Ticker = { instId; last; open24h; changePct }`。

---

## 7. 测试与验收标准（Testing & Acceptance）

### 7.1 单元测试（Vitest，必须）
- `position.test.ts`：
  - `rejects_empty_trades`：空数组 → `null`。
  - `derives_long_after_open`：单条 long → side='long'、size=qty、avgEntry=price。
  - `averages_entry_on_add`：两条同向 long 加权均价正确（**前瞻护栏**：当前后端整开整平不产生此序列，R6/R7；测试仍保留以护未来多笔成交）。
  - `reduces_on_partial_close`：部分平仓减量、均价不变（同上，前瞻护栏）。
  - `returns_null_when_flat`：全平 → `null`。
  - `computes_unrealized_pnl_sign`：多头 mark>avg 浮盈为正；空头 mark<avg 浮盈为正。
  - `respects_asOfTs`：给定 `asOfTs` 截断后状态正确。
  - `agrees_with_positionAfter_sign`（R6/R7）：对「整开整平」真实序列，derive 结果的方向 / 持仓量与末笔 `positionAfter`（+多/−空/0 平）一致。
- `ohlc.test.ts`（R2，新增）：
  - `returns_nulls_when_no_candle`：`candle=null` 或 `prevClose=null` → 三字段全 `null`。
  - `computes_change_up_down_flat`：`c>prevClose` / `<` / `=` 时 `change` 符号正确。
  - `guards_nonpositive_prevClose`：`prevClose<=0` → `changePct`/`amplitude` 为 `null`（不产生 Infinity）。
  - `computes_amplitude`：`(h-l)/prevClose` 数值正确。
- 现有 `format/compare/indicators/chartSync` 等测试保持绿；新增对 `colorForReturn` 用于 OHLC 着色的回归（可选）。

### 7.2 类型/构建门禁（必须）
- `pnpm --filter web typecheck`（tsc）零错误；`pnpm --filter web build` 通过；既有 CI 前端静态门（tsc/vitest/build）与桌面静态门不被破坏。

### 7.3 人工验收清单（对照三条需求）
1. **需求 1**：图表头出现 `[K线价格走势 | 净值]` 页签，默认 K 线；切到「净值」时 K 线消失、净值曲线占满图表区；K 线视图上**不再叠加**净值折线。对比模式下「净值」页签显示多条归一化曲线。
2. **需求 2**：工具栏正下方有 OHLC 横栏，默认显示最新一根 开/高/低/收/涨跌/振幅；鼠标在 K 线上移动时数值随被选中 K 线切换；涨为 `--long`、跌为 `--short` 着色；光标移出回落最新根。
3. **需求 3**：左侧整体上 80%/下 20%；上区左 20% 为可点击切换的标的列表（当前标的高亮、显示真实价/涨跌）、右 80% 为图表区；下区为 `[成交记录 | 当前持仓]` 页签，成交记录显示真实 trades，当前持仓在持仓中显示方向/量/均价/标记价/浮动盈亏，空仓显示「当前无持仓」。
4. **回归**：第 1 轮技术指标（MA/EMA/BOLL 叠加、MACD/RSI/KDJ/VOL 副图、指标弹窗默认 MA+BOLL+MACD）功能不变；右侧 StrategyPanel 不变；切标的/周期/主题、实时流脉冲、缓存样例横幅均正常；无 hydration 告警、无内存泄漏（卸载 EquityChart 时 `chart.remove()`）。

---

## 8. 风险与缓解（Risks & Mitigations）

| 风险 | 影响 | 缓解 |
|------|------|------|
| 需求 1「最下方」与需求 3「下区为成交/持仓」字面冲突 | 评审/开发理解偏差 | 已在 §2 明确收敛：视图页签置于图表区顶部（剥离净值的核心意图），下区固定为成交/持仓；评审节点如需调整仅改 §2 一处 |
| 移除工具栏标的 `Select` 可能被视为「回退」 | 误删既有控件 | 这是需求 3「左侧标的列表作为切换器」的直接落地，非回退；保留 `buildInstOptions` 逻辑迁至 `lib/instruments.ts`，行为等价；移动端见下 |
| 80/20 + 20/80 在窄屏拥挤、下区 20% 被压塌 | 小屏可用性 / 表格截断 | **（R5 已就地细化）** 桌面/Electron 优先；下区 `min-h-[140px]` 下限；`lg` 以下纵向堆叠（标的列表横向 chip 条 + 图表 `min-h-[320px]`）；以 `min-w-0`/`min-h-0` 防塌陷；`TradesTable` 内置 `max-h-[320px]` 仅作上限，外层容器承担滚动（见 §2.3） |
| `positionAfter` 符号约定（**已核验**：+多/−空/0 平，simulator.py:227/244） | ~~方向判断错误~~ → 已澄清 | **（R6 已更正）** 约定明确且后端为整开整平；`deriveOpenPosition` 用 side+qty+price 有符号重建，结果与 `positionAfter` 一致（§7.1 加一致性断言）；可选直接取 `positionAfter` 驱动方向/量（R7） |
| `page.tsx`/`Page()` 超 CLAUDE.md 尺寸阈值 | 可维护性 / 一致性 | **（R4 已就地修复）** 左区下沉到 `ChartWorkspace.tsx`（单一 props 对象、自持 hover 派生），`page.tsx` 退为薄编排者，文件 <800 行、函数职责单一 |
| OHLC 计算内嵌组件、`signed/colorForReturn` 不接受 null | tsc 报错 / 空态缺失 | **（R2 已就地修复）** 抽 `lib/ohlc.ts#computeOhlcStats`（空安全 + 除零守卫）+ `ohlc.test.ts`；组件仅对非空数值着色/取符号，否则「—」 |
| 共享 `Tabs.onChange:(string)=>void` 接窄 setter | tsc 逆变报错 | **（R1 已就地修复）** 收窄包装 `onChange={(k)=>setBottomTab(k as ...)}`；§5.1/§9.2 |
| 回测末态平仓 → 持仓页签常显「无持仓」 | 看似空数据 | 诚实行为（符合真实数据规则）；并提供 P2「按选中时点回放持仓」(`asOfTs`) 让悬停历史在仓区间时显真实持仓 |
| 净值剥离后 CandleChart 左价轴残留空轴 | 视觉瑕疵 | CandleChart 在 `!hasEquity` 时 `leftPriceScale.visible=false`，§3.2(B) |
| 十字光标高频回调引发重渲染 | 性能抖动 | 回调仅回吐 `ts` 数字；page 用 `useMemo` 派生 displayCandle；必要时对 `setHoverTs` 做同值短路 |
| EquityChart 与 CandleChart 重复图表样板 | 维护成本 | 接受少量重复以换清晰职责分离；公共挂载/主题逻辑保持同构便于后续抽取（非本轮范围） |
| P2 `/tickers` 触达 OKX 失败 | 列表无价 | 列表降级为仅符号 + 当前标的有价；绝不填假价；502 沿用既有错误语义 |
| store version 升级迁移 | 旧持久化缺字段 | `version:3` + `migrate` 对 `version<3` 补 `chartView:'price'/bottomTab:'trades'`；追加字段即便不迁移也由浅合并回落默认（R11，属显式稳妥） |

---

## 9. 附录：关键落地片段（供开发节点逐行参考）

### 9.1 CandleChart 十字光标订阅（创建 effect 内追加）
```ts
const onCrosshairRef = useRef(onCrosshairChange);
onCrosshairRef.current = onCrosshairChange;
// ...createChart 之后：
const onCross = (param: { time?: Time }) => {
  const ts = (param.time as number | undefined) ?? null;
  onCrosshairRef.current?.(ts);
};
chart.subscribeCrosshairMove(onCross as never);
// cleanup 内：chart.unsubscribeCrosshairMove(onCross as never)（chart.remove 亦会释放）
// 左价轴随净值存废：useEffect(()=>chartRef.current?.applyOptions({leftPriceScale:{visible:hasEquity}}),[hasEquity])
```

### 9.2 ChartWorkspace.tsx 左侧骨架（R1/R4/R5，Tailwind 类示意）

> page.tsx 仅 `<ChartWorkspace {...props} />`；左 `<section>` 整体在 `ChartWorkspace` 内，`hoverTs` 及
> `displayCandle/prevClose`（§3.2(A) 步骤 2）为本组件本地态。下区加 `min-h-[140px]` 防塌陷；`Tabs.onChange`
> 用收窄包装（R1）。

```tsx
// ChartWorkspace.tsx —— 单一 props 对象入参（candles/overlayEquity/overlays/markPrice/instId/lastPrice/...）
const [hoverTs, setHoverTs] = useState<number | null>(null);
useEffect(() => setHoverTs(null), [instId, bar]); // 切标的/周期清空（R3）
const byTs = useMemo(() => new Map(candles.map((c) => [c.ts, c])), [candles]);
const tsIndex = useMemo(() => new Map(candles.map((c, i) => [c.ts, i])), [candles]);
const displayCandle = (hoverTs != null ? byTs.get(hoverTs) : undefined) ?? candles.at(-1) ?? null;
const di = displayCandle ? tsIndex.get(displayCandle.ts) ?? -1 : -1;
const prevClose = di > 0 ? candles[di - 1].c : (displayCandle?.o ?? null);
const BOTTOM_TABS = [{ key: 'trades', label: '成交记录' }, { key: 'positions', label: '当前持仓' }];

return (
<section className="flex h-full min-h-0 flex-col border-r border-[var(--border)]">
  {/* 上区 ≈80%（lg 桌面优先） */}
  <div className="flex min-h-0 flex-[8] flex-col lg:flex-row">
    <div className="shrink-0 overflow-auto border-b border-[var(--border)] lg:w-1/5 lg:min-w-[140px] lg:max-w-[240px] lg:border-b-0 lg:border-r">
      <InstrumentList active={instId} onSelect={setInst} lastPrice={lastPrice} changePct={sessionChangePct} />
    </div>
    <div className="flex min-h-[320px] min-w-0 flex-1 flex-col">
      <div className="shrink-0 border-b border-[var(--border)] px-4 py-2.5"><ChartToolbar … /></div>
      {dataSource === 'cache' && <CacheBanner/>}
      <ChartHeaderBar view={chartView} onView={setChartView} candle={displayCandle} prevClose={prevClose} />
      <div className="relative min-h-0 flex-1">
        {chartView === 'price'
          ? <CandleChart candles={candles} trades={overlayTrades} indicators={indicators} theme={theme}
              onCrosshairChange={setHoverTs} onLoadMore={loadMore} jumpToTs={jumpToTs} loading={…} error={chartError}/>
          : <EquityChart equity={overlayEquity} overlays={overlays} theme={theme}/>}
      </div>
    </div>
  </div>
  {/* 下区 ≈20%，min-h 下限防塌陷（R5） */}
  <div className="flex min-h-[140px] flex-[2] flex-col border-t border-[var(--border)]">
    <div className="shrink-0 border-b border-[var(--border)] px-3 py-2">
      <Tabs tabs={BOTTOM_TABS} active={bottomTab}
            onChange={(k) => setBottomTab(k as 'trades' | 'positions')} /> {/* R1：收窄包装 */}
    </div>
    <div className="min-h-0 flex-1 overflow-auto px-3 py-2">
      {bottomTab === 'trades'
        ? <TradesTable trades={overlayTrades}/>
        : <PositionPanel trades={overlayTrades} markPrice={markPrice} instId={instId}/>}
    </div>
  </div>
</section>
);
```

### 9.3 区间涨跌幅（供 InstrumentList 高亮行 / equity 摘要）
```ts
const first = candles[0]?.c, last = candles.at(-1)?.c;
const sessionChangePct = first && last ? (last - first) / first : null; // 真实，来自已加载行情
```

---

**评审记录**：见文首 **§0 评审记录（Review Notes）**——逐节评审，0 P0 / 6 P1（已就地修复）/ 6 P2（放行条件）。

---

## 10. 评审结论（Review Verdict，Subtask #1）

**结论：有条件通过（Approved with Conditions）。**

方案整体**可行、与现有架构高度一致、规模适配得当**：核验了每一处「既有能力」断言均成立（`Tabs/Badge/Switch`、`fmt*`/`colorForReturn`/`signed`、`Overlay`/`hasEquity`、`buildInstOptions`、`normalizeEquity`、`subscribeCrosshairMove` 链路、`api.{instruments,candlesWithMeta,trades,equity}`、`Trade.{ts,side,qty,price,positionAfter}`），技术栈（Next 14 + lightweight-charts ^4.2 + Zustand persist + React Query）完全支撑三条需求；坚守「绝不造假数据」硬规则（持仓由真实 `trades` 纯前端推导，缺数据显「—」/「无持仓」）。

已就地修复全部 **6 个 P1**（详见 §0 与各节 `Rx` 标注，文档升 **v2**）：
1. **R1** 共享 `Tabs.onChange` 逆变类型错误 → 收窄包装（§5.1/§9.2）。
2. **R2** OHLC 计算空安全 + 抽纯函数 `lib/ohlc.ts` + 单测（§5.2/§7.1/§4）。
3. **R3** `displayCandle` 空值合并回落，避免有数据空显（§3.2/§9.2）。
4. **R4** 新增 `ChartWorkspace.tsx` 承载左区，`page.tsx` 退为薄编排者，回到 CLAUDE.md 尺寸阈值（§3.1/§4/§9.2）。
5. **R5** 响应式与下区 `min-h` 下限具体化（§2.1/§2.3/§8/§9.2）。
6. **R6** 更正 `positionAfter` 风险行（约定 +多/−空/0 平已核实，引擎整开整平）（§6.1/§7.1/§8）。

**放行条件（开发节点 Subtask #2 须遵守 / 确认的 P2 项）：**
- **C1（R7）**：实现 `deriveOpenPosition` 后，须保证其方向/持仓量与末笔 `positionAfter` 一致（已要求一致性测试）；可选直接以 `positionAfter` 驱动方向/量。
- **C2（R8）**：`InstrumentList` 自跑 `useQuery(["instruments"])` 取列表（与工具栏同键去重）；移除工具栏标的 `Select`（#10）若本轮暂缓，须确保不与列表选择冲突。
- **C3（R9）**：接受「OHLC 仅随主图悬停刷新」限制；若产品要求副图悬停亦联动，另起增量。
- **C4（R12）**：`EquityChart` 须覆盖三种空态（回测未跑 / 实时未起 / 对比未跑）的 `emptyHint`。
- **C5**：严格按 §4 文件清单落地，单文件 <800 行、单函数 <50 行、形参 ≤5；不超范围重构既有第 1 轮技术指标体系与右侧 `StrategyPanel`。
- **C6**：通过 §7.2 门禁（`tsc` 零错误 + `vitest` 全绿 + `build` 通过），新增 `position.test.ts`/`ohlc.test.ts` 必须先于实现编写并稳定。

满足上述条件即视为达成本轮三条需求；P2 增强（`/tickers` 行情快照）可缺省，核心布局不依赖之。

---

## 实施过程发现的方案缺陷（Issues Found During Implementation，Subtask #2）

> 开发节点（Subtask #2）落地时逐项核验方案，发现以下与真实代码不符之处；按任务约束「不静默偏离」，在此登记并说明已采用的修正做法。

### D1（必修·命名冲突）：`chartView` 字段名与既有 store 字段语义冲突

- **问题**：§6.2 / §3.1 / §9.2 规定在 store 新增 `chartView: 'price' | 'equity'` 作为「K线价格走势 / 净值」视图页签状态。但**仓库 `lib/store.ts` 已存在同名字段** `chartView: ChartView`（`ChartView = "run-range" | "compare-range" | "live-follow"`，为历史区间选择器），且被 `page.tsx`（`resolveChartRange` / `shouldFollowLive` / `overlays` / `overlayEquity` / `overlayTrades`）、`ChartToolbar.tsx`、`StrategyPanel.tsx`、`CompareView.tsx`、`RunHistory.tsx`、`chartLive.ts` 等 7+ 处消费。直接复用同名字段会破坏既有「实时 / 区间」切换与净值叠加逻辑。
- **修正**：新增字段更名为 `chartPane: 'price' | 'equity'`（setter `setChartPane`），与既有 `chartView`（区间选择器）**正交并存**；类型加在 `lib/types.ts`（`ChartPane`）。所有方案中提及的「store.chartView（price/equity）」一律以 `chartPane` 落地；`ChartHeaderBar` 的 `view` prop 类型为 `ChartPane`。下区数据页签同理用 `bottomTab: 'trades' | 'positions'`（与方案一致，无冲突）。
- **影响**：纯命名替换，语义与方案完全一致；持久化 `partialize` 增列 `chartPane` / `bottomTab`，`version` 2→3，`migrate` 对 `version<3` 补默认值（与方案 §6.2 等价）。

### D2（说明·P2 按放行条件缺省）

- 按 §10 放行条件「P2 增强（`/tickers` 行情快照）可缺省，核心布局不依赖之」，本轮**未实现** §4 文件清单 #16–#19（`lib/api.ts#tickers`、`services/quant` 后端 `/tickers` 路由 / `rest_client` / `schemas`），以将改动面与回归风险压到最小、且不触碰后端。
- 为保持前端契约完整与可向前兼容，仅在 `lib/types.ts` 保留 `Ticker` 类型、`InstrumentList` 保留可选 `tickers?` prop（当前不传入）。`InstrumentList` 按方案优雅降级：当前标的显示真实价 / 区间涨跌，其余标的仅显符号（绝不造假价）。

### D3（说明·`Overlay` 上移，无 re-export）

- 按 §6.3 / R10（已核验全仓 `Overlay` 仅 `CandleChart.tsx` 内部使用、无外部引用），将 `Overlay` 干净上移至 `lib/types.ts`，`CandleChart` / `EquityChart` / `ChartWorkspace` 统一从 types 导入。方案中「`export type { Overlay }` re-export 仅作防御、非必需」一句据此**未保留 re-export**（无外部引用，省冗余）。
