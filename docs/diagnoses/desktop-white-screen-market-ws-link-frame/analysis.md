# 打包桌面端开屏白屏：市场 WS 的 link 帧被当成 K 线

## 现象

打开 `desktop/dist/win-unpacked/Aragon Trading.exe`，窗口开出后整页白屏。

关键的迷惑点：**桌面外壳一切正常**。`%APPDATA%/aragon-desktop/logs/main.log` 显示

```text
2026-08-13T04:03:19.073Z Aragon Trading 桌面端启动（mode=prod, isPackaged=true）
2026-08-13T04:03:30.745Z 后端就绪。
2026-08-13T04:03:39.434Z 前端就绪。
```

前后端都就绪、窗口已 `loadURL(http://127.0.0.1:18761)`，没有任何一个既有的失败对话框被触发。
白屏发生在**渲染层**：React 树在 hydration 之后崩溃并整棵卸载，Next 的默认根错误边界
接管，页面只剩一行黑字（`Application error: a client-side exception has occurred`），
在暗色窗口里肉眼近似白屏。

## 复现

不需要重新打包，直接跑打包产物里的两个进程即可复现：

```bash
# 后端（用生产库的**副本**，且不注入任何 LIVE_* / AI_TRADE_*，杜绝复现动作触发下单）
cd desktop/dist/win-unpacked/resources/backend
DB_PATH=<某处>/aragon-repro.db ./aragon-quant.exe --port 18760

# 前端（standalone 的 rewrite 目标 127.0.0.1:18760 是构建期烤死的，故后端必须占 18760）
cd desktop/dist/win-unpacked/resources/web
PORT=18871 HOSTNAME=127.0.0.1 NODE_ENV=production node server.js
```

用无头 Chromium 打开 `http://127.0.0.1:18871/`，控制台第一条错误即：

```text
TypeError: Cannot read properties of undefined (reading 'year')
    at eN (chunks/203-*.js)            # lightweight-charts 的 convertTime
    at Array.map
    at sx.Kb
    at sQ.setData
    at chunks/526-*.js:1:3915          # ← IndicatorPane 的锚系列 setData
```

**只有后端在跑时才会白屏**：后端不在时页面完整渲染（各面板显示 500 错误态）。这解释了
为什么「前端就绪」的日志与白屏可以同时成立——崩溃的触发物是后端推来的一帧数据。

`chunks/526-*.js:1:3915` 精确落在
[apps/web/components/Chart/IndicatorPane.tsx](../../../apps/web/components/Chart/IndicatorPane.tsx#L151)
的锚系列 `setData` 上。把该 chunk 用 Playwright 的 route 拦截打个补丁，把喂进去的数组打出来，
拿到了真凶：

```text
[PROBE] candles len=1 bad=1
  first={"key":"ws:business:BTC-USDT-SWAP:1H:chart","state":"connecting",
         "attempt":0,"downtimeS":0,"detail":"connecting"}
```

candles 数组里躺着一个**链路状态对象**，不是 K 线。

## 根因

`okx-link-resilience`（commit `3c2155b`）给市场 WS 加了一支链路状态帧：

- [services/quant/app/api/ws_market.py](../../../services/quant/app/api/ws_market.py#L63)
  的 `on_stream` 每次建流都会先发 `{"type":"link","data":{key,state,attempt,downtimeS,detail}}`
  （`state=connecting` → `subscribing` → `live`），与 candle / trade / ticker / book **共用同一条流**。
- 那里的注释写着它的正确性前提：「链路状态是**加法**事件类型：前端遇到未知 type 本就忽略，
  故老前端零影响。」

这个前提当时**不成立**。消费端只有一个（[apps/web/app/page.tsx](../../../apps/web/app/page.tsx)
的 `marketWS` 回调），它的分支是：

```ts
if (event.type === "error")  { ...; return; }
if (event.type === "trade")  { ...; return; }
if (event.type === "ticker") { ...; return; }
if (event.type === "book")   { ...; return; }
// 剩下的一律当 K 线 ← 兜底方向错了
const candle = event.data;
if (candle.confirm === true) { setLive(...); } else { setForming(candle); }
```

于是 `link` 帧走进 K 线分支：它没有 `confirm`，被 `setForming` 存成"正在形成的 K 线"，
再经 `mergeCandles` 进入 `candles`。之后：

1. `IndicatorPane` 把 `candles.map(c => ({ time: c.ts }))` 喂给 lightweight-charts；
2. `c.ts` 是 `undefined`；
3. lightweight-charts 的 `isBusinessDay(time)` 判据是「既不是 number 也不是 string」——
   `undefined` 因此被判为 BusinessDay，走进 `Date.UTC(time.year, ...)`，读 `undefined.year` 抛错；
4. 该异常发生在 effect 里，React 无对应 error boundary，整棵树卸载 → 白屏。

`link` 帧在**每次**建流时都会发，所以这是必现的，与行情内容无关。

前端的 `MarketWsEvent` 联合类型里也没有 `link` 这一支——wire 上新增的事件类型没有在类型
层留下任何痕迹，是这次漏改始终没有被编译器逮到的原因。

时间线也对得上：`3c2155b` 之后的打包产物（`resources/web/web-server.json` 的
`builtAt=2026-08-13T01:08Z`）首次把这条链路带进桌面端，白屏正是从那一版开始。

## 修复

把兜底方向反过来——**只有显式认得的 type 才被消费**，并且**进图表的 K 线必须先过形状闸**。

新模块 [apps/web/lib/marketFrames.ts](../../../apps/web/lib/marketFrames.ts) 是这条流的唯一路由判据：

- `routeMarketFrame(event, sinks)`：`switch` 白名单路由，`default` 分支里的
  `const unrouted: never = event` 让「给 `MarketWsEvent` 加一支却忘了在此路由」变成**编译错误**，
  运行期则静默忽略未建模的 type。落点全部由调用方注入，故不依赖 React 即可单测。
- `isUsableCandle(data)`：`ts` / `c` 必须是有限数才准进图表。判据取自图表库真正的要求，
  而不是「后端应该会给对」的信任——链路帧靠它也会被拦下，与它被标成什么 type 无关。
  三个 K 线入口（市场流 / 模拟盘流 / 自动交易流）共用这道闸。

改动点：

- [apps/web/app/page.tsx](../../../apps/web/app/page.tsx)：`marketWS` 回调改为调 `routeMarketFrame`；
  `handlePaperEvent` / `handleLiveAutoEvent` 的 K 线分支接上 `isUsableCandle`。
- [apps/web/lib/okxLinkTypes.ts](../../../apps/web/lib/okxLinkTypes.ts)：新增 `MarketLinkEvent` 与
  `OkxWsStreamState`，与顶栏芯片的 `OkxLinkState` 同处一个 wire 模块并写明**两套词表不互转**
  （`connecting/subscribing/live` 是一条 socket 的状态机，`up/idle/degraded/down` 是通道健康度聚合）。
  [types.ts](../../../apps/web/lib/types.ts) 的 `MarketWsEvent` 引用它补上 `link` 一支。
- [apps/web/app/error.tsx](../../../apps/web/app/error.tsx)：段级错误边界。本次事故能把整个应用
  打没，正因为 `app/` 下没有任何边界，Next 的根兜底只留一行英文黑字（暗色窗口里即"白屏"）。
  现在会给出中文说明、错误编号与「重试」。
- `page.tsx` 的 `advanceMarketTs` 加 `Number.isFinite` 闸：`Math.max` 一旦吃进 `NaN` 就永久锁死
  行情时钟（`??` 拦不住 `NaN`），此后 K 线分支的 `lastMarketTs <= candle.ts` 恒假，顶部最新价
  会**静默**冻结到下次重挂——与本次同一类「无异常的坏值传播」。

回归用例 [apps/web/lib/marketFrames.test.ts](../../../apps/web/lib/marketFrames.test.ts) 直接驱动
路由函数，喂后端真实的 link 载荷断言它到不了任何 K 线落点、也不许清掉故障横幅（重连时的
connecting/subscribing/live 三帧都试）。**已验证它确实能挡住回归**：把 `case "link"` 改回按 K 线
处理，该用例立刻变红。

注：`marketWS` 全仓只有这一个消费端。

## 验证

1. `apps/web` 全量 vitest 通过；`tsc --noEmit` 除既有的 `WeightBatchArena.test.tsx` 报错外无新增。
2. `npm run build:web` 重建 standalone 后按上面的复现步骤重跑：控制台零错误，
   `link` 的 connecting / subscribing / live 三帧均被忽略，candle / trade / ticker / book 照常消费。
3. 把重建产物换进 `desktop/dist/win-unpacked/resources/web/`，启动真实打包 exe，
   经 `--remote-debugging-port` 附着渲染层：页面完整渲染（K 线 + MA/BOLL/MACD 副图 +
   盘口 + 成交带 + 持仓 + AI 面板），顶栏显示「OKX 已连接」。

## 遗留

- `dist/win-unpacked` 里的 `resources/web` 已用修复后的产物就地替换；`app.asar` 与
  `resources/backend` 未动。要出正式安装包仍需重跑 `npm run dist`。
  **注意**：当前工作树的 `desktop/src/lib/config.js` 有未提交改动（prod 默认注入
  `AI_TRADE_ENABLED=true`），重打包会把它一并烤进 asar——那是另一件事的在制品，
  不应作为本次修复的副作用带出去。
- **`link` 帧目前没有任何前端消费者**。它每次状态迁移都在 wire 上跑一趟却无人读；要么把它
  接进连接面板 / 故障横幅（那才兑现后端注释里「不必等 15s 轮询就能显示正在重连」的承诺），
  要么连同前端的 `link` 分支一起删掉。本次两者都没做，只把现状写进了后端注释。
- `applyPublicCandle` 在市场 WS 的 effect 依赖数组里，但它只在 K 线分支被读。切图表视图 /
  起停模拟盘都会因此拆掉重建整条 socket（丢弃节流挂起帧、重置基线，并让后端重订 OKX 两条流）。
  改成走 ref 即可保住连接。属既有行为、非本次引入，未动——它牵动实时链路，需要单独验证。
