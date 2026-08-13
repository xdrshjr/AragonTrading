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

把兜底方向反过来——**只有显式认得的 type 才被消费**：

- [apps/web/lib/chartLive.ts](../../../apps/web/lib/chartLive.ts)：新增类型守卫 `isCandleFrame`。
- [apps/web/app/page.tsx](../../../apps/web/app/page.tsx)：K 线分支前置 `if (!isCandleFrame(event)) return;`，
  取代原来的 else 兜底。未知 / 后端将来再加的事件类型一律忽略，后端注释里的「加法」前提这才真正成立。
- [apps/web/lib/types.ts](../../../apps/web/lib/types.ts)：`MarketWsEvent` 补上 `link` 一支
  （`MarketLinkEvent`），让 wire 的真实形状在类型层可见——下一次后端加事件类型时，
  漏改会在 `tsc` 阶段而不是在用户的白屏里暴露。

回归用例：[apps/web/lib/chartLive.test.ts](../../../apps/web/lib/chartLive.test.ts) 的
`market frame routing`，直接喂一帧真实的 `link`（`state=connecting`）断言它不被当作 K 线，
并断言它确实没有 `ts`——即「一旦被当 K 线就会打崩图表库」的那个属性。

注：`handlePaperEvent` / `handleLiveAutoEvent` 两个同构的 WS 消费端本来就是
`if (event.type === "candle")` 的正向判据，不受影响；`marketWS` 全仓也只有这一个消费端。

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
- 本次白屏能把整个应用打没，是因为渲染层没有任何 error boundary。加一个
  `app/error.tsx`（或 `global-error.tsx`）可把这一类图表库异常降级为局部可恢复错误，
  而不是整页消失。本次未做，属独立改动。
