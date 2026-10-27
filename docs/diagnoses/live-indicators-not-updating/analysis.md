# Live Technical Indicators Not Updating in Real Time

> Diagnosis only. This document does not change code and contains no commits.
> Symptom slug: `live-indicators-not-updating`.

## Problem description

User report (round 4):

> 打开软件以后，虽然K线之前的相关技术指标的图形有，但放置一段时间，新的技术
> 指标图形和数据，就没有实时更新，随K线增加一起更新。

Translation: after opening the app, the technical indicators (MA / BOLL / MACD …)
are drawn for the candles that loaded initially, but after the app sits for a
while the indicators do **not** keep updating in real time as new candles arrive —
they should grow together with the K-line.

Expected behavior: while the chart is in its default *live-follow* view, the
rightmost candle should tick in real time and new bars should append as time
passes, and every enabled indicator (main-chart overlays MA/BOLL and the sub-pane
MACD/RSI/KDJ/VOL) should extend in lock-step with the candles.

Actual behavior: the real-time candle stream never reaches the chart. The only
thing that advances the chart is the bounded REST poll, which for the default
`1H` bar refreshes at most once per minute and yields a new bar only once per
hour. With no live websocket feed, neither the candle nor its indicators update
in real time, so the chart looks frozen "after a while".

## Reproduction steps

All commands below were actually run on Windows from the repo root
`M:/takoAI/AragonTrading`; the captured artifacts live under `tmps/` and are
quoted in the evidence section.

### A. Start the backend

```bash
cd services/quant
.venv/Scripts/python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 18760 --log-level info
```

Confirm OKX is reachable (the proxy must be up — Clash on 127.0.0.1:7897):

```bash
python -c "import urllib.request,json;print(json.loads(urllib.request.urlopen('http://127.0.0.1:18760/api/health',timeout=5).read()))"
# -> {... "okx": "up", "okxLatencyMs": ~1700, "wsConfigured": true ...}
```

### B. Observe the live chart websocket — it delivers nothing

Connect a raw client to the exact route the chart uses
(`/api/ws/market?instId=BTC-USDT-SWAP&bar=1m`) and listen for 80 s. Script:
`tmps/ws_observe.py`.

```bash
cd services/quant
.venv/Scripts/python.exe ../../tmps/ws_observe.py   # writes tmps/ws_observe.log
```

Result (`tmps/ws_observe.log`):

```text
connected at 1781458742
[ 11.9s] (no message in 10s)
[ 21.9s] (no message in 10s)
... (repeats every 10s) ...
--- SUMMARY ---
distinct bar timestamps seen: 0
new_bars_rolled_over=0
```

Zero candle messages in 80 s for `BTC-USDT-SWAP 1m`, one of the most active
markets on OKX — the live feed is dead.

### C. Read the backend log — OKX rejects 3 of the 4 subscribed channels

`tmps/backend.log` (emitted while step B was running):

```text
INFO: ('127.0.0.1', 50803) - "WebSocket /api/ws/market?instId=BTC-USDT-SWAP&bar=1m" [accepted]
WARNING aragon.okx.ws: OKX WS subscription error: {'event': 'error',
  'msg': "Wrong URL or channel:trades,instId:BTC-USDT-SWAP doesn't exist...", 'code': '60018'}
WARNING aragon.okx.ws: OKX WS subscription error: {'event': 'error',
  'msg': "Wrong URL or channel:tickers,instId:BTC-USDT-SWAP doesn't exist...", 'code': '60018'}
WARNING aragon.okx.ws: OKX WS subscription error: {'event': 'error',
  'msg': "Wrong URL or channel:books5,instId:BTC-USDT-SWAP doesn't exist...", 'code': '60018'}
INFO: connection closed
```

The connection subscribed to `candle1m`, `trades`, `tickers`, `books5`; OKX
returned `60018 "channel doesn't exist"` for `trades/tickers/books5`, then the
backend closed the connection.

### D. Prove OKX is NOT the problem — it keeps sending candles

Subscribe to the OKX **business** endpoint directly (script `tmps/okx_direct.py`):

```bash
cd services/quant
.venv/Scripts/python.exe ../../tmps/okx_direct.py
```

Result:

```text
[A candle-only]  channels=['candle1m']          -> candle_msgs=11  errors=[]
[B candle+trades] channels=['candle1m','trades'] -> candle_msgs=14  errors=["Wrong URL or channel:trades... doesn't exist..."]
```

Key fact: in trial **B**, OKX still streamed **14 candle messages** even though
`trades` errored — OKX does **not** reject the whole batch; the valid `candle1m`
channel keeps delivering. Therefore the 0-candle result in step B is caused by
the backend tearing its own connection down, not by OKX.

### E. UI reproduction

1. `pnpm dev`, open `http://localhost:18761` (or open the packaged desktop app).
2. Stay in the default view (`chartView = "live-follow"`, `bar = 1H`) — the
   store default, see `apps/web/lib/store.ts:54`.
3. Watch the rightmost candle: it never ticks in real time (no forming-candle
   jitter), because the websocket forming updates never arrive.
4. Leave it for several minutes. The candle and all indicators only inch forward
   when the bounded REST poll fires (`apps/web/lib/chartLive.ts:45-50`: `1H` =
   every 60 s), and a brand-new bar appears only once per hour. The chart looks
   like it stopped updating — exactly the reported symptom.

## Root cause analysis

### Primary root cause: the live market websocket subscribes public-only channels to the OKX *business* endpoint, and a single per-channel error tears down the whole feed

The chart's live route builds one `OKXMarketWSClient` and subscribes to four
channels in a single request:

`services/quant/app/okx/ws_client.py:88`–`services/quant/app/okx/ws_client.py:96`

```python
await ws.send(json.dumps({
    "op": "subscribe",
    "args": [
        {"channel": self._channel, "instId": self.inst_id},  # candle{bar}
        {"channel": "trades",  "instId": self.inst_id},
        {"channel": "tickers", "instId": self.inst_id},
        {"channel": "books5",  "instId": self.inst_id},
    ],
}))
```

…over the connection at `services/quant/app/okx/ws_client.py:85`, i.e.
`self._settings.okx_ws_public`, which is deliberately set to the **business**
endpoint so candle channels work:

`services/quant/app/config.py:24`–`services/quant/app/config.py:26`

```python
# ⚠️ candle{bar} 频道由 OKX 的 **business** WS 端点提供，public 端点会返回
# "channel ... doesn't exist"。实测确认：/ws/v5/business 才能订阅到 K 线。
okx_ws_public: str = "wss://ws.okx.com:8443/ws/v5/business"
```

But `trades`, `tickers`, and `books5` are **public** channels that exist only on
`/ws/v5/public`, not on `/ws/v5/business`. So OKX rejects those three with
`60018` (step C log). The endpoint split is real and confirmed both ways by the
direct test in step D (candles flow on business; the public channels error).

The error itself would be harmless — OKX keeps streaming the valid `candle1m`
(step D, trial B) — except the chart route makes the error **fatal**. Every OKX
error event is forwarded:

`services/quant/app/okx/ws_client.py:128`–`services/quant/app/okx/ws_client.py:134`

```python
if "event" in msg:
    if msg.get("event") == "error":
        logger.warning("OKX WS subscription error: %s", msg)
        await self._handlers.on_error(str(msg.get("code") or ...), str(msg.get("msg") or ...))
    return
```

…and the chart route's `on_error` handler closes the whole connection:

`services/quant/app/api/ws_market.py:55`–`services/quant/app/api/ws_market.py:57`

```python
async def on_error(code: str, message: str) -> None:
    await _send_if_open(closed, websocket, "error", {"code": code, "message": message})
    closed.set()                       # <-- kills the entire market WS
```

`closed.set()` ends the receive loop and stops the client, dropping the live
candle stream that OKX is actively sending:

`services/quant/app/api/ws_market.py:72`–`services/quant/app/api/ws_market.py:81`

```python
while not closed.is_set():
    try:
        await asyncio.wait_for(websocket.receive_text(), timeout=30)
    except asyncio.TimeoutError:
        continue
...
finally:
    closed.set()
    await client.stop()
```

So the sequence is: connect → subscribe 4 channels on business → OKX errors on
`trades/tickers/books5` within ~1 s → `on_error` → `closed.set()` → the candle
stream (which OKX would have delivered) is torn down before any candle is
forwarded. The browser sees the socket close and `ReconnectingWS` reconnects
(`apps/web/lib/ws.ts:46`–`apps/web/lib/ws.ts:59`), which repeats the same failure
— a permanent reconnect loop that never yields a single live candle. This is why
step B saw 0 candles.

### Why the symptom presents as "indicators don't update"

The indicators are **not** independently broken; they are strictly downstream of
the candle array. On the frontend the live sources are the websocket
(`apps/web/app/page.tsx:197`–`apps/web/app/page.tsx:221`) and the bounded REST
poll (`apps/web/app/page.tsx:113`–`apps/web/app/page.tsx:143`), both of which
feed `live`/`forming`, which are folded into `candles`
(`apps/web/app/page.tsx:145`–`apps/web/app/page.tsx:148`). Every indicator path
recomputes whenever `candles` changes:

- main-chart overlays (MA/EMA/BOLL): `apps/web/components/Chart/CandleChart.tsx:427`–`apps/web/components/Chart/CandleChart.tsx:440` (effect deps `[candles, theme, indicators]`),
- sub-panes (MACD/RSI/KDJ/VOL): `apps/web/components/Chart/IndicatorPane.tsx:110`–`apps/web/components/Chart/IndicatorPane.tsx:152` (effect deps `[candles, theme, def]`),
- both apply via full `setData`: `apps/web/components/Chart/plots.ts:74`–`apps/web/components/Chart/plots.ts:79`.

An isolated render test (`tmps/lwc-setdata-repro.html` + `tmps/run_repro.py`)
confirms the render layer is correct: with the app's exact full-`setData`
pattern, lightweight-charts 4.2 auto-follows the live tail and keeps the candle
series, the overlay line, and the synced sub-pane advancing together
(`mainRange.to` and `paneRange.to` both went 99 → 140 as bars grew 100 → 141).

So the indicators cannot diverge from the candles in the data/render layer — if
candles update, indicators update. They appear "stuck" only because their data
source, the live candle websocket, is dead. With the websocket gone, the chart
advances solely via the slow REST poll, which is not real time and (for `1H`)
adds a bar only hourly; the user reads that as "the indicators stopped updating
even though I expect them to grow with the K-line".

### Why paper trading is unaffected (scoping evidence)

Paper trading uses the same `OKXMarketWSClient` through a candle-only wrapper,
but with the **default no-op** error handler
(`services/quant/app/okx/ws_client.py:44` `on_error: ErrorHandler = _noop_error`;
wrapper at `services/quant/app/okx/ws_client.py:177`–`services/quant/app/okx/ws_client.py:187`).
Because its `on_error` does not call `closed.set()`, the `trades/tickers/books5`
errors are ignored and its candle stream survives. This is independent proof
that (a) the candle channel works on the business endpoint and (b) the chart-only
breakage is caused by the fatal `on_error` in `ws_market.py`, not by the
subscription itself.

## Impact surface

- **Live chart real-time updates (default view)** — broken. In `live-follow`
  (the default and the target after every `setInst`/`setBar`,
  `apps/web/lib/store.ts:54`,`:65`,`:66`), the chart never receives live candles;
  candles/indicators only advance via the bounded REST poll. Affects every
  instrument and bar.
- **Technical indicators** — appear frozen as a direct consequence of the dead
  candle feed (they are correctly coupled to `candles`).
- **Live market depth panel** (`docs/plans/live-market-depth-panel`) — fully
  broken: `trades/tickers/books5` do not exist on the business endpoint, so
  trade/ticker/book events never arrive on any path.
- **Paper trading** — functionally OK for candles (no-op `on_error`), but it
  still subscribes to 3 invalid channels every connect, producing repeated
  `60018` log noise and wasted work.
- **Network/health** — the chart's `ReconnectingWS` enters a tight
  connect → error → close → reconnect loop, repeatedly opening OKX business
  connections that are immediately discarded.
- Not affected: historical candle loading, backtest/compare frozen views, and
  the REST `/api/candles` path.

## Candidate fixes

| # | Fix | Invasiveness | Risk | Effort | Resolves chart feed | Resolves depth panel |
|---|-----|---|---|---|---|---|
| A | Make per-channel subscription errors non-fatal: in `ws_market.py` `on_error`, forward/log the error but do **not** `closed.set()` for per-channel errors (e.g. `60018`). | Low | Low | Low | Yes | No (depth still errors) |
| B | Stop subscribing public-only channels on the business connection: subscribe `candle{bar}` only on `/ws/v5/business`, and open a **second** connection to `/ws/v5/public` for `trades/tickers/books5`. Keep per-channel errors non-fatal. | Medium | Low–Med | Medium | Yes | Yes |
| C | Revert the chart route to a candle-only client (the pre-depth-panel behavior) on the business endpoint, and build the depth panel as a separate, independently-failing public-endpoint subscription. | Medium | Low | Medium | Yes | Yes (separately) |

Notes:
- Fix A is the minimum change that restores the user's reported symptom: the
  valid `candle1m` channel already streams (step D, trial B), so simply not
  killing the connection on the `trades/tickers/books5` errors lets candles —
  and therefore all indicators — update in real time again.
- A `60018`-style channel error should be treated as "this one channel is
  unavailable", surfaced to the relevant consumer, and must never tear down the
  shared candle stream.

## Recommended fix

Adopt **B**, layered on **A** as the safety invariant.

1. Route channels to the endpoint that actually serves them: `candle{bar}` on
   `/ws/v5/business`; `trades`/`tickers`/`books5` on `/ws/v5/public`. This is an
   OKX architectural requirement, already documented at `config.py:24-26` for
   candles and proven for the public channels by step D. Add an explicit
   business/public endpoint pair to `Settings` instead of overloading a single
   `okx_ws_public`.
2. Make a subscription error scoped, never fatal: in `ws_market.py`'s `on_error`
   (and anywhere `MarketWSHandlers.on_error` is wired) forward the error to the
   feature that needs that channel and keep the candle stream alive. Reserve
   connection teardown for transport-level failures, not per-channel `60018`s.

Justification:

- It directly fixes the reported bug. Once the candle websocket survives, the
  existing, already-correct indicator pipeline (CandleChart.tsx:427-440,
  IndicatorPane.tsx:110-152) updates in real time with no frontend change.
- It also fixes the live market depth panel, which is broken for the same
  root reason (wrong endpoint), rather than leaving a second latent failure.
- It removes the reconnect-loop churn and the repeated `60018` log noise that
  paper trading currently produces.
- Fix A alone is an acceptable hotfix if only the chart symptom must be resolved
  immediately, but it leaves the depth panel and the wasted invalid
  subscriptions unaddressed, so it should be treated as a stepping stone to B,
  not the final state.

### Suggested regression tests (for the implementing node)

- Backend: a `/ws/market` test where the upstream emits a `60018` error for a
  non-candle channel asserts the candle stream keeps flowing and the client
  connection stays open.
- Backend: assert `candle{bar}` is subscribed on the business endpoint and
  `trades/tickers/books5` on the public endpoint (no public channel is ever sent
  to business).
- Frontend (already partially covered by `apps/web/lib/chartLive.test.ts`): a
  test that, in `live-follow`, a live candle event appends to the series and an
  indicator series gains the corresponding point.

---

## 评审补充：证据核验、勘误与加固（Reviewer Addendum, Subtask #1）

> 本节由评审节点追加：仅审阅、勘误、补证，不改业务代码、不提交。上方原始诊断结论予以保留；下方为对**当前代码**（非历史版本）的逐条核验。

### R1. 后端根因证据：逐行核验通过

- 单连接一次性订阅 4 频道：`ws_client.py:88–96`（`candle{bar}` + `trades` + `tickers` + `books5`）。✔
- 订阅走 `self._settings.okx_ws_public`：`ws_client.py:84–85`。✔
- 该端点被刻意设为 **business**：`config.py:24–26`（`wss://ws.okx.com:8443/ws/v5/business`）。✔
- 错误事件转发 `on_error`：`ws_client.py:128–135`。✔
- 图表路由 `on_error` 调 `closed.set()`（致命）：`ws_market.py:55–57`。✔
- 收尾把 candle 流拆掉：`ws_market.py:72–81`（receive loop + `finally` 的 `closed.set()`/`client.stop()`）。✔
- paper 走默认 `_noop_error`（不致命）：`ws_client.py:44`、wrapper `ws_client.py:167–187`。✔

结论：后端根因链与原文一致，证据成立。

### R2. 机制加固：错误是「即时屏蔽转发 + 延迟物理拆链」双阶段（补强 0-candle 证据）

原文「在任何 candle 被转发前就被拆掉」表述略粗，应更精确：`on_error` 的 `closed.set()` 一触发，转发被**立即**屏蔽——`_send_if_open` 首行 `if closed.is_set(): return`（`ws_market.py:90–91`），此后即便 OKX 仍在推 candle，`on_candle → _send_if_open` 也直接返回、不下发；而**物理连接**要等 `ws_market.py` receive loop 的 `asyncio.wait_for(..., timeout=30)`（`ws_market.py:73–76`）超时后才经 `client.stop()` 真正断开。`tmps/backend.log` 时间戳正是此双阶段铁证：错误 `13:39:03` → 连接 `closed` `13:39:31`（间隔 ≈28s ≈ 30s 超时）。两条机制叠加 ⇒ 整段窗口 0 根 candle 抵达浏览器，与 `tmps/ws_observe.log`（82s，`distinct bars=0`）吻合。此加固使「0 candle」不依赖任何拆链时序假设而成立。

### R3. 前端引用勘误（行号漂移；实质结论不变，已重新定位到当前代码）

原文「症状解释」段的前端行号与当前代码不符，更正如下（实质结论——指标严格下游于 `candles`——核验为真）：

- 活动「实时 WS 源」实为 `page.tsx:225–273`（deps `[streamMarket, applyPublicCandle, instId, bar]`，candle 经 `:255–261` 落 `setLive/setForming`）。原文 `page.tsx:197–221` 指向的是 **paper** WS effect（`:202–218`）。另注：`page.tsx:277–301` 是一处**死代码** market WS effect（第 278 行首即 `return;`），不在活动路径。
- 「REST 轮询源」实为 `latestCandlesQ`：定义 `page.tsx:132–138`（`refetchInterval = liveCandleRefreshIntervalMs(bar)`），应用 `page.tsx:161–168`；其 `enabled = latestFallback`（见 R5）。
- `candles` 折叠：`page.tsx:170–173` `mergeCandles(older, baseCandles ?? [], live, forming)`。
- 主图 MA/EMA/BOLL 叠加 effect：`CandleChart.tsx:450–463`（deps `[candles, theme, indicators]`）。原文 `CandleChart.tsx:427–440` 指向的是 `setVisibleRange`/markers effect。
- 副图 MACD/RSI/KDJ/VOL：`IndicatorPane.tsx:110–152`（deps `[candles, theme, def]`）——原文准确。✔
- `1H` 轮询 60s 之源：`chartLive.ts:57–62`（`liveCandleRefreshIntervalMs`，`1H → 60_000`）。原文 `chartLive.ts:45–50` 实为 `shouldStreamMarket`/`shouldApplyPublicCandle`。

### R4. 反例 / 替代解释审查（均已排除）

- 「OKX 整批拒绝 candle」？否。`tmps/okx_direct.py` trial B 实测 `candle_msgs=14` 与 `trades` 报错并存（脚本与日志 artifact 真实存在、时间戳新鲜）。
- 「前端指标管线自身故障 / 与 K 线解耦」？否。指标 effect 严格以 `candles` 为依赖重算（`CandleChart.tsx:450–463` / `IndicatorPane.tsx:110–152`），`tmps/lwc-setdata-repro.html` 复现 `setData` 自动跟随尾部、保持主图/副图同步。
- 「paper 为何不受影响」？`_noop_error` 不调 `closed.set()`，candle 流存活——反证致命 `on_error` 才是症结，而非订阅或端点本身。

### R5. 候选修复审查（边界 / 最小化 / 回归）

- **Fix A 边界正确**：`on_error` 仅由 OKX `event:error` 触发；**传输级**断连走 `_run` 的 `except Exception → 退避重连`（`ws_client.py:106–111`），不经 `on_error`。故「`on_error` 不再 `closed.set()`」不会吞掉传输故障，反而消除 close→重连环（环的**唯一**成因即 `closed.set()`）。
- **Fix A 残留**：若仍向 business 订阅 3 个 public-only 频道——(a) 每连一条 `60018` 日志噪声；(b) `on_error` 转发的错误被前端 `page.tsx:231–235` 写入 `marketError`，并在 `MarketDepthPanel.tsx:34` 以红字显示「channel ... doesn't exist」，即便 candle 正常也会**闪现错误条**（下一根 candle 经 `page.tsx:235` `setMarketError(null)` 清除，故为亚秒级闪烁、非持久，但不洁）。
- **Fix A 潜在脆弱点（非本 bug，仅记录）**：`latestFallback = applyPublicCandle && !liveWsOpen`（`page.tsx:96`）——回退轮询以「WS 是否 open」为条件，而非「是否真有 candle 到达」。若将来 candle 频道自身在 business 失效，WS 仍判 open ⇒ 轮询被禁用 ⇒ 彻底冻结。当前 candle 在 business 正常（step D 证），不触发；属后续健壮性优化，不在本轮范围。
- **Fix B 审查**：确为深度面板正解，但需第二条 `/ws/v5/public` 连接（双 receive loop / 双心跳 / 双重连态 + 合流），中等侵入并新增失效面；且把「深度面板特性补全」混入一次 bug 修复，违反 subtask #2「改动尽量小」与 subtask #3「staged 仅与本次修复相关」。`MarketDepthPanel` 虽已挂载（`ChartWorkspace.tsx:124`，由 `tradeTape/orderBook/marketTicker` 供数）今日即为空，Fix A / 收敛订阅不使其更坏（无回归）。
- **回归测试缺口属实**：现有 `tests/test_ws_market.py` 仅覆盖「正常转发」与「非法参数拒绝」，**无**「非 candle 频道 `60018` 时 candle 流应存活、连接不被拆」用例——正是本 bug 的护栏，须补。

---

## 评审结论（Review Verdict）

**最终推荐修复（单一方案）：隔离 candle 流——在 `ws_market.py` 把 per-channel 错误改为「转发但非致命」（删除 `on_error` 中的 `closed.set()`），并将图表路由在 business 连接上的订阅收敛为 candle-only（移除 `trades`/`tickers`/`books5`）。** 这是同一处「让非 candle 频道不再影响共享 candle 流」的最小变更，等价于「Fix A 的非致命不变式 + Fix C 的 candle-only 化」，但**不**引入第二条连接、**不**提前补全深度面板。

**理由：**

1. **命中真正近因。**「错端点订阅 public 频道」本身无害——OKX 仍持续推送 candle（`tmps/okx_direct.py` trial B 实测 14 条）。把一次性、咨询性的 per-channel `60018` 升级为整条共享传输拆链的，是 `ws_market.py:55–57` 的致命 `on_error → closed.set()`。删掉这一句即精确移除缺陷。

2. **最小变更、最低回归面。**仅改图表路由这一条订阅 + 一个 handler；不新增连接 / receive loop / 心跳 / 重连态。candle 流（step D 证其在 business 正常）随即存活，前端既有指标管线（`CandleChart.tsx:450–463` / `IndicatorPane.tsx:110–152`）**零改动**即恢复实时——直接修复 round-4 报告的「指标不随 K 线实时更新」。

3. **顺带消除三项残留：**重连环（成因 `closed.set()` 已去）、每连一条 `60018` 日志噪声（不再订阅无效频道，含 paper 侧）、以及 `MarketDepthPanel.tsx:34` 的错误条闪现（不再产生 `60018`，前端 `marketError` 不被置位）。

4. **守住不变式：**per-channel 错误必须 scoped、advisory、never fatal；连接级拆链只保留给传输故障（已由 `ws_client.py:106–111` 的 except→重连覆盖）。即便将来某路由确需多频道，非致命 `on_error` 也保证单频道错误不再拖垮 candle 流。

**实施约束（交给 Subtask #2，务必最小 diff）：**

- 必需①：`ws_market.py` 的 `on_error` 删除 `closed.set()`；错误可继续经 `_send_if_open` 透传（供 UI 标注「该频道不可用」）或仅记日志，二选一，但**不得**拆连接。
- 必需②：图表路由对应的 OKX 订阅收敛为 candle-only（移除 `trades`/`tickers`/`books5`）。若不愿改动共享的 `OKXMarketWSClient`（paper 亦经其转发并忽略这三类），可加一个「订阅频道集」入参，图表路由传 candle-only、paper 维持现状——避免牵动无关代码。
- 回归测试（红→绿）：补 `test_ws_market.py` 用例——上游对非 candle 频道发 `60018` 时，断言 candle 仍被转发、`closed` 未被 set、连接保持；并断言图表路由不再向 business 发送 public-only 频道。

**明确排除（为何本轮不选 Fix B）：**Fix B（business/public 双端点 + 第二条连接）是**深度面板**（`docs/plans/live-market-depth-panel`）的正确架构，但它中等侵入、引入新失效面，并把一项特性补全混入 bug 修复，超出 round-4「指标不实时」范围，违反 subtask #2/#3 的最小化与单一职责约束。应作为**独立后续任务**承接，不阻塞、不混入本 bug 的最小修复。
