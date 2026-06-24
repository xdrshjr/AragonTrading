# 诊断报告：左侧行情面板刷新过于频繁（market-depth-refresh-too-frequent）

> 多轮迭代任务第 2 轮 · Subtask #0「问题定位与分析」
> 上一轮（commit `3174506`）打通了 OKX depth/trades/ticker 的实时推流，本轮目标是**给这条推流链路加节流**：常规约 1s 刷新一次，剧烈变化时约 0.5s 一次。

## 一、问题描述（Problem description）

K 线图左上方的「行情深度面板」（`MarketDepthPanel`，展示 24h Vol / Bar Vol / Spread / Pct、五档 asks/bids、最新价、Trades 成交带）在第 1 轮修复推流后**刷新频率过高**，肉眼可见的疯狂闪烁/跳动，每秒重绘十几到几十次。

用户期望：
- **常规情况**：约 **1s** 刷新一次（≈ 1 Hz）。
- **剧烈变化时**：约 **0.5s** 刷新一次（≈ 2 Hz）。

这是一个**刷新频率 / 性能 / 体验**问题，**不是数据正确性问题**——数据本身正确，只是更新太密。

## 二、复现步骤（Reproduction steps）

前置：后端 `services/quant` 已启动并能连上 OKX，前端 `apps/web` 已启动。

1. 打开行情工作台（`app/page.tsx` → `ChartWorkspace`），选择一个高流动性合约，例如 `BTC-USDT-SWAP`、`1H`。
2. 确保处于会触发实时推流的状态：`streamMarket = shouldStreamMarket(mode, okxUp)` 为 `true`（`app/page.tsx:133`），此时 `useEffect` 会建立 `/ws/market` 连接（`app/page.tsx:294-342`）。
3. 观察左侧 `MarketDepthPanel`：五档买卖盘、Spread/Pct、最新价、Trades 成交带每秒刷新十几到几十次，明显闪烁。

**可量化的复现（推荐，用于交付验证前后对比）**——在 `app/page.tsx` 的 WS 回调里临时加计数即可看到 firehose：

```ts
// app/page.tsx onEvent 回调内（仅诊断用，勿提交）
if (event.type === "book") { console.count("book"); /* ... */ }
if (event.type === "ticker") { console.count("ticker"); /* ... */ }
if (event.type === "trade") { console.count("trade"); /* ... */ }
```

在 `BTC-USDT-SWAP` 上观察控制台：`book` 计数约 **~10 次/秒**（OKX `books5` 每 100ms 推一次），`ticker` 约 **~10 次/秒**，`trade` 在活跃时段可达**数十次/秒**。由于每个事件都直接触发一次 `setState`，`MarketDepthPanel` 实际重绘频率 ≈ 三者之和（**20~40+ Hz**），与「疯狂刷新」的现象吻合。也可在 `MarketDepthPanel` 顶部加渲染计数器（`useRef` 自增 + `console.log`）直接观测组件重渲染次数。

## 三、根因分析（Root cause analysis，含证据）

**根因：从 OKX 到 React 状态的整条链路没有任何节流 / 合并（throttle / coalescing），每一条 WS 消息都会立刻触发一次 React `setState`，进而触发整页重渲染。** OKX 的高频推送被原样透传到 UI。

### 3.1 上游推送频率（外部事实）

OKX V5 WebSocket：
- `books5` 五档快照：**有变化时每 100ms 推一次**（≈ 10 Hz）。
- `tickers`：**有变化时约每 100ms 推一次**。
- `trades`：**每笔成交实时推送**，活跃合约每秒可达数十条。

来源见文末 Sources。也就是说，上游本身就是一个 ~10Hz + 逐笔成交的「水龙头」。

### 3.2 后端：逐条透传，无节流

OKX 客户端对**每一行**数据都直接回调 handler（`services/quant/app/okx/ws_client.py:157-181`）：

```python
for row in data:
    await self._dispatch_row(channel, row)   # ws_client.py:157-158
...
if channel == "books5":
    book = parse_okx_books5(row)
    if book is not None:
        await self._handlers.on_book(book)   # ws_client.py:178-181
```

浏览器 WS 路由的 `on_book/on_ticker/on_trade` 同样**收到一条就转发一条**给前端（`services/quant/app/api/ws_market.py:46-53`、`90-105`）：

```python
async def on_book(book: OrderBookSnapshot) -> None:
    await _send_if_open(closed, websocket, "book", book.model_dump(by_alias=True))  # ws_market.py:52-53
```

后端全程没有任何「合并最近一帧、按固定节奏下发」的逻辑。

### 3.3 前端 WS 客户端：解析即回调，无合并

`ReconnectingWS.onmessage` 把每条消息 `JSON.parse` 后立即回调 `onEvent`（`apps/web/lib/ws.ts:41-47`），没有缓冲。

### 3.4 前端状态层：每条事件一次 setState → 整页重渲染（关键放大点）

`app/page.tsx` 的 WS 回调里，**每一个** `book/ticker/trade` 事件都同步调用 `setState`（`apps/web/app/page.tsx:299-330`）：

```ts
if (event.type === "trade") {
  setTradeTape((prev) => mergeTradeTape(prev, event.data)); // 每笔成交一次
  setLastPrice(event.data.price);
  recordMarketTs(event.data.ts);
  return;
}
if (event.type === "ticker") {
  setMarketTicker(event.data);                              // ~10Hz
  setLastPrice(event.data.last);
  recordMarketTs(event.data.ts);
  return;
}
if (event.type === "book") {
  setOrderBook(event.data);                                 // ~10Hz
  recordMarketTs(event.data.ts);
  return;
}
```

这些 state（`orderBook` / `marketTicker` / `tradeTape` / `lastPrice` / `lastMarketTs`）随后被组装进 `market: LiveMarketState`（`apps/web/app/page.tsx:407-416`）并作为 prop 传给 `ChartWorkspace` → `MarketDepthPanel`（`apps/web/components/Chart/ChartWorkspace.tsx:193`）。`MarketDepthPanel` 是纯渲染组件（`apps/web/components/Chart/MarketDepthPanel.tsx:12-71`），**market 一变就整块重绘**。

**结论**：四个层级（OKX → ws_client → ws_market → ws.ts → page.tsx）**没有任何一层做频率控制**，page.tsx 的「逐事件 setState」把 ~10Hz 的 books5 + ~10Hz 的 tickers + 逐笔 trades 三股水流叠加成 20~40+ Hz 的整页重渲染，这就是「刷新太疯狂」的直接原因。

### 3.5 关于「剧烈变化时 0.5s」的语义

需求里的「高变化」目前**代码中并不存在对应概念**——现在是「来一条画一条」，既没有 1s 的基线节流，也没有「变化大就提速到 0.5s」的自适应判断。因此本轮需要**新增**一个自适应节流器：基线 1000ms、判定为剧烈变化时缩短到 500ms。判定信号建议取「自上次刷新以来最新价/盘口顶档价的相对变动幅度」（见第六节）。

## 四、影响面（Impact surface）

- **触发条件**：`streamMarket` 为真时（`app/page.tsx:133`，即实时行情推流开启、OKX 可达）。一旦左侧面板在线，必然触发。
- **直接受影响 UI**：左侧 `MarketDepthPanel`（五档/Spread/Pct/最新价/Trades 成交带）持续高频重绘、闪烁。
- **连带受影响**：`lastPrice` 被同一批 setState 高频更新，且被多处消费——`InstrumentSidebar` 的 `livePrice`、K 线图实时价线 `livePrice`、以及作为 `markPrice` 参与持仓盈亏计算（`ChartWorkspace.tsx:141, 199, 242, 276`）。这些区域也跟着抖动。
- **性能**：每秒数十次整页 React 重渲染，CPU 占用偏高、风扇/耗电上升，桌面端（Electron）尤其明显，可能拖慢拖拽分栏、十字光标等交互。
- **非影响项**：不影响下单/风控/账户等写链路；不丢数据、不改协议；纯属刷新节奏与性能问题。

## 五、候选修复方案对比（Candidate fixes）

| 方案 | 落点 | 思路 | 侵入性 | 风险 | 工作量 | 可单测 |
|---|---|---|---|---|---|---|
| **A（推荐）前端自适应节流** | `apps/web/app/page.tsx` + 新增 `apps/web/lib/marketThrottle.ts` | 用 ref 缓存最近一帧 book/ticker、累积 trades 与 lastPrice；用「基线 1000ms / 剧烈变化 500ms」的定时器把缓存一次性刷入 state，直接降低重渲染频率 | 低～中（仅前端，集中在 page.tsx 的 WS 回调 + 一个纯函数 helper） | 低（不动后端/协议；缓存里始终保留最新帧，不丢真值） | 低 | 是（helper 为纯逻辑，契合现有 `marketDepth.ts`/`marketDepth.test.ts` 约定） |
| **B 后端按 socket 合并下发** | `services/quant/app/api/ws_market.py` | 每个浏览器连接维护「最近一帧 book/ticker + 累积 trades」，用 asyncio 定时任务按 1s/0.5s 自适应节奏 flush；candle 仍即时透传 | 中（重构路由为缓冲 + 定时任务，需处理关闭/取消） | 中（asyncio 时序、连接生命周期；须确保**不**误节流 candle；改的是 candle 也走的共享 socket） | 中 | 较难（要测异步定时与 socket 生命周期） |
| **C 在共享 OKX 客户端 `_dispatch_row` 节流** | `services/quant/app/okx/ws_client.py` | 在最底层按通道节流 | 中～高（该客户端被 paper 的 candle-only 包装 `OKXWSClient` 复用，见 `ws_client.py:184-205`） | 高（共享组件、爆炸半径大，易误伤 candle/paper 推流） | 中 | 较难 |

## 六、推荐方案（Recommended fix）

**采用方案 A：在前端新增一个「自适应节流器」，把高频 WS 事件合并后按节奏刷入 React state。**

理由：
1. **直击症状**：本应用是本地桌面端，后端在本机，真正昂贵的是「每秒数十次整页 React 重渲染」，而它发生在前端 state 层。前端节流是对这一开销最直接、最小爆炸半径的处理。
2. **最小侵入、低风险**：只改 `app/page.tsx` 的 WS 回调 + 一个独立纯函数 helper，不动后端、不动 WS 协议、不影响 candle/paper/写链路；缓存里始终保留「最新一帧」，刷新时取最新值，不丢真实数据。
3. **可测**：判定/合并逻辑抽成纯函数，符合仓库既有约定（`apps/web/lib/marketDepth.ts` + `marketDepth.test.ts`），易补单测；组件测试注意 `NODE_ENV=test`（见记忆 `aragon-web-vitest-node-env`）。
4. 方案 B/C 改后端虽能省带宽，但本机带宽不是瓶颈，且 B/C 触碰 candle/paper 共享链路，风险与工作量更高，收益不匹配。若后续确有带宽诉求，可在 A 之上再叠加 B 作为增强，但本轮不必。

### 推荐实现要点（供 Subtask #2 落地参考）

新增 `apps/web/lib/marketThrottle.ts`，并在 `app/page.tsx` 用 ref 管理「待刷入帧」：

- **缓存（ref，不触发渲染）**：`pendingBook`、`pendingTicker`、`pendingTrades[]`、`pendingLastPrice`、`pendingTs`；以及上次刷新基准 `lastFlushedPrice`、`lastFlushTs`。
- **入帧**：每个 WS 事件只更新对应 ref（trades 入累积队列），**不**直接 `setState`；若当前没有挂起的 flush，则 `setTimeout` 安排一次。
- **自适应间隔**：`interval = isHighChange ? 500 : 1000`。
  - `isHighChange` 建议：自上次刷新以来，最新价（或盘口顶档 mid）相对变动 `|p - lastFlushedPrice| / lastFlushedPrice >= 阈值`（如 `5e-4` ≈ 5bp），或最优买/卖价档位价格发生跳变。阈值设为具名常量便于调参。
- **flush**：把 `pendingBook/Ticker/lastPrice` 写入 state，`pendingTrades` 经 `mergeTradeTape`（`apps/web/lib/marketDepth.ts:3-15`，自带去重+排序+截断 40）折叠后写入 `tradeTape`；更新 `lastFlushedPrice/lastFlushTs`，清空挂起标志与缓存。
- **清理**：`instId/bar` 变化或组件卸载时清除定时器（与现有 `useEffect` 清理一并处理，`app/page.tsx:338-342`）。
- candle 分支可维持现状（低频、`confirm` 语义不同），节流只针对 book/ticker/trade/lastPrice 这股高频流。

如此即可把刷新稳定在常规 1Hz、剧烈行情 2Hz，满足本轮需求，且不回滚第 1 轮的推流修复。

---

### Sources（OKX 推送频率佐证）

- OKX API guide — <https://www.okx.com/docs-v5/trick_en/>
- OKX WebSocket API Docs（books5 ~100ms 快照说明）— <https://voiceofchain.com/academy/okx-websocket-api-documentation>
- OKX WebSocket API Documentation（GitHub 整理）— <https://github.com/lhzk377/okx-websocket-api-docs>
- OKX V5 官方文档 / 子订阅规则公告（books5 改为每 100ms 推送）— <https://www.okx.com/docs-v5/en/>、<https://supdljgzdhc.okx.com/hc/en-us/articles/5399923178253>

---

## 评审补充证据（Reviewer-added evidence）

> 本节由评审节点（Subtask #1）补入，用于把第三方频率说法替换为官方口径、补全未被节流的旁路证据，并排除可替代解释。原作者结论不变，仅加固证据链。

### R.1 上游频率：以 OKX 官方口径复核（替换第三方佐证）

- **books5**：官方将五档深度频道的推送从 200ms 调整为「**每 100ms** 推一次」（≈10 Hz）。佐证：OKX 子订阅规则变更公告（见上 Sources）。
- **tickers**：「**有价格变动时每 100ms** 推一次，无变动时约每分钟一次」。活跃合约价格几乎持续变动，故实测 ≈10 Hz。
- **trades**：**tick-by-tick，逐笔成交各推一条**（"Every single trade that executes ... gets pushed here"）。活跃时段每秒数十条。

三条叠加 ≈ 20~40+ Hz，与第二节实测的 `console.count` 量级一致。即第 3.1 节「~10Hz + 逐笔」的外部事实成立，且现已落到官方/一手口径，证据不再依赖单一第三方页面。

### R.2 关键修正：candle 频道并非「低频」，且它是 lastPrice 的第二个未节流写入口

第 3.4 / 五 / 六节默认 candle 分支「低频、可维持现状」。但官方对 candlestick 频道的描述是「**每个新 tick 都推一次当前未完成 K 线（forming candle），收盘时再推一次已完成 K 线**」——即 candle 同样是 **tick 驱动**，活跃合约下并不低频。

这带来一条原报告未点明的旁路：`app/page.tsx:322-323` 的 candle 分支**也**直接 `setLastPrice(candle.c)`（且 `setForming(candle)`），完全不经任何节流：

```ts
const candle = event.data;                                   // page.tsx:322
if ((lastMarketTsRef.current ?? 0) <= candle.ts) setLastPrice(candle.c); // page.tsx:323
```

含义：方案 A 只节流 book/ticker/trade 时，
- 左侧 `MarketDepthPanel` 的**五档/Spread/Pct/Trades 成交带**（数据源是 book/ticker/trade）会被正确降频——**这正是用户投诉的对象**（「Waiting for OKX asks/bids/trades」那一片）；
- 但面板顶部的**最新价**（`MarketDepthPanel.tsx:28-30` 渲染 `market.lastPrice`）以及 K 线实时价线（`ChartWorkspace.tsx:242`）、`markPrice`（`ChartWorkspace.tsx:141`）仍可能随 candle tick 抖动，因为 lastPrice 有 candle 这个未节流的并行写入口。

因此 candle 分支**可以保持即时**（它只影响右侧 K 线，且 `confirm` 语义需要即时落定，不应延迟），但实现节点必须**显式决定 lastPrice 的取数策略**（见评审结论第 4 条），不能默认「只节流那三条就万事大吉」。

### R.3 lastMarketTsRef 的同步性约束（一处易被节流改坏的隐藏耦合）

`recordMarketTs`（`page.tsx:289-292`）同时维护 `lastMarketTsRef.current`（ref，不触发渲染）与 `lastMarketTs`（state，触发渲染）：

```ts
const recordMarketTs = (ts: number) => {
  lastMarketTsRef.current = Math.max(lastMarketTsRef.current ?? 0, ts); // ref：被 candle 分支用于门控
  setLastMarketTs((prev) => Math.max(prev ?? 0, ts));                   // state：驱动面板「updated」时间
};
```

`lastMarketTsRef.current` 被 candle 分支（`page.tsx:323`）用作「是否允许用 candle 收盘价覆盖 lastPrice」的门控。若实现把整个 `recordMarketTs` 一并塞进定时 flush，则两个 flush 之间 ref 会**滞后**，candle 事件就可能用较旧的收盘价盖掉缓冲区里更新的成交价，造成 lastPrice 倒退/闪烁。结论：**ref 的推进必须随入帧同步发生（ref 无渲染成本），只有 `setLastMarketTs` 这个 state 写入才纳入节流**。这是把节流做对、不引入回归的硬约束。

### R.4 排除可替代解释（counter-examples checked）

- **不是重连风暴**：`ws.ts:57-61` 的重连是指数退避（起始 1000ms、上限 15s），不会形成每秒数十次的 onopen/onmessage；观测到的 20~40 Hz 与「正常在线时的频道叠加推送速率」吻合，而非断线抖动。
- **不是 React StrictMode 双调用**：StrictMode 只在开发期对 effect/render 双调用一次性翻倍，无法解释「持续每秒数十次」的稳态刷新。
- **不是其它定时刷新**：`health`（`page.tsx:104`）15s、`latestCandlesQ` 的 REST 兜底（`liveCandleRefreshIntervalMs` 最低 15s）都是秒级以上，量级对不上。
  
故「每条 WS 消息直接一次 setState」是唯一与现象量级吻合的解释，根因定位排他性成立。

### R.5 复用面核对：无现成节流工具，可证「新建 marketThrottle.ts」不是造孤岛

`apps/web/lib` 全量检索无 throttle/debounce 工具；仅 `chartSync.ts:25-30` 存在一处 `requestAnimationFrame` 合并重对齐（用途是图表价轴等宽，不是 state 降频）。故方案 A 新建 `marketThrottle.ts` 不与既有工具重复；且 `chartSync.ts` 的「rAF 内合并、逻辑可被确定性单测」（`chartSync.test.ts:155`）正好是本仓库「把合并逻辑抽纯函数 + 注入计时器以可测」的先例，新 helper 应沿用该范式（纯函数 + 可注入 now/timer）。

---

## 评审结论（Review Verdict）

### 一、根因复核：通过

报告定位的根因——**OKX → ws_client → ws_market → ws.ts → page.tsx 整条链路无任何节流/合并，每条 WS 消息直接触发一次 React `setState` → 整页重渲染**——经逐行核对成立，所有引用行号与现网源码一致：

- 前端逐事件 setState：`app/page.tsx:305-321`（trade/ticker/book 三个高频分支各自 setState），未完成 K 线旁路 `:322-330`；market 组装 `:407-416`；下游消费 `ChartWorkspace.tsx:141/193/199/242/276`；纯渲染面板 `MarketDepthPanel.tsx:12-71`。
- WS 客户端无缓冲：`apps/web/lib/ws.ts:41-47`（解析即回调）。
- 后端逐条透传：`ws_client.py:157-181`、`ws_market.py:43-60/90-105`，全链无合并。
- 上游频率以官方口径复核为 ≈20~40+ Hz（见 R.1）。
- 可替代解释（重连风暴/StrictMode/其它定时器）已逐一排除（见 R.4）。

证据链判定为 **airtight**。

### 二、最终推荐修复（单一方案）

**采用方案 A：在前端新增纯函数节流器 `apps/web/lib/marketThrottle.ts`，用 ref 缓存最近一帧 book/ticker、累积 trades 与 lastPrice，由「基线 1000ms / 剧烈变化 500ms」的自适应定时器把缓存一次性刷入 React state。**

理由（与原报告一致并经评审确认）：
1. **直击主成本**：本机桌面端，瓶颈是「每秒数十次整页 React 重渲染」，发生在前端 state 层；前端合并是对该成本最直接、爆炸半径最小的处理。解析+ref 写入是 O(小)，被节流掉的 React 协调才是大头。
2. **最小侵入、可回退**：只动 `page.tsx` 的 WS 回调 + 一个独立纯 helper，不碰后端/协议/candle/paper/写链路；缓存恒留最新帧，不丢真值。
3. **可测**：合并/判定抽为纯函数，契合 `marketDepth.ts` + `marketDepth.test.ts`、`chartSync.ts` 既有范式；组件测试记得 `NODE_ENV=test`（记忆 `aragon-web-vitest-node-env`）。
4. **拒绝 B/C（本轮）**：B（后端按 socket 合并）、C（共享 OKX 客户端底层节流）改的是 candle/paper 也复用的共享链路（`ws_client.py:184-205` 的 candle-only 包装即复用证据），爆炸半径与工作量更高，而本机带宽并非瓶颈，收益不匹配。若日后确有带宽诉求，可在 A 之上叠加 B，二者不冲突。

### 三、实现节点（Subtask #2）必须满足的边界约束（验收口径）

以下为评审在审「候选修复是否覆盖并发/错误/边界」时识别出的硬约束，逐条作为下游交付验收项：

1. **trade tape 必须基于既有 tape 增量合并，禁止整段替换**：flush 时应 `setTradeTape(prev => fold(prev, pendingTrades))`（复用 `mergeTradeTape` 自带去重/降序/截断 40，`marketDepth.ts:3-15`）；若只用本窗口 `pendingTrades` 构造再写入，会让成交带每个周期被清空重建——这是最易踩的回归。建议一次性折叠（prev+pending 入一个 Map 再排序截断），避免每条 pending 调一次 O(40)。
2. **`lastMarketTsRef.current` 随入帧同步推进，仅 `setLastMarketTs` 纳入节流**（见 R.3）。否则 candle 分支门控（`page.tsx:323`）读到滞后 ts，导致 lastPrice 倒退/闪烁。
3. **lastPrice 取数策略须显式决定**（见 R.2）：candle 分支保持即时（影响右侧 K 线、`confirm` 需即时落定）；但因 candle 是 lastPrice 的并行未节流写入口，若要让面板顶部最新价也稳定在 1~2Hz，应把 candle 的价格也并入 `pendingLastPrice` 由 flush 统一落库。鉴于用户投诉对象是五档/成交带，**最低验收只需 book/ticker/trade 被节流即可消除症状**；lastPrice 是否一并降频列为可选增强，但实现必须二选一并在代码注释写明，不得留下「两个写入口互相打架」的隐式行为。
4. **`isHighChange` 首样本守卫**：`lastFlushedPrice == null` 时不得做相对变动除法，应按常规处置（或首帧即时刷新）；阈值（如 5bp）须为具名常量便于调参。判定信号取「自上次刷新以来最新价或盘口顶档 mid 的相对变动」。
5. **错误事件保持即时**：`error` 分支（`page.tsx:300-303`）不进节流，须立即 `setMarketError`，避免故障提示被延迟。
6. **生命周期清理**：`instId/bar` 变化或卸载时，清除定时器并**重置全部节流 ref（含 `lastFlushedPrice` 基线）**，与既有重置 effect（`page.tsx:183-194`）和 WS 清理（`page.tsx:338-342`）对齐；否则上一合约的缓存帧/高变化基线会泄漏到新合约，触发一次错数据 flush。
7. **首帧延迟（leading edge）**：纯 trailing `setTimeout(1000)` 会让面板在连上后最长 1s 仍显示「Waiting for OKX …」。建议首帧 leading 立即刷新或用较短初始延迟，兼顾「不闪」与「不空等」。
8. **合并逻辑落为纯函数 helper 并补单测**（正常路径 + 至少一条异常/边界，如空 pending、首样本无基线、>40 笔成交折叠）。

### 四、回归面提示

方案 A 会把 K 线实时价线与 `markPrice` 的更新降到 ≤2Hz——符合本轮需求且对盈亏计算无实质影响（仍是最新价的近实时值）；不影响下单/风控/账户写链路。只要满足上述 8 条约束，无新增回归。下一节点据此实现并补测即可。
