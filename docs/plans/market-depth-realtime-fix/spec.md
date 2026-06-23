# Live Market Depth Panel — Real-Time Refresh Fix (Dual OKX WS Endpoint)

> **Version:** v2 (reviewed & repaired — see `## 评审记录` below)
> **Feature slug:** `market-depth-realtime-fix`
> **Scope:** Backend + dev-config template only (no frontend / source-logic changes; `.env.example` is config docs, not source).
> **Related prior spec:** `docs/plans/live-market-depth-panel/spec.md` (the original feature this repairs).
> **v2 changes:** Added `.env.example` to the change plan (P1-1), added a config-migration / rollout note and a rollback note, and folded the proxy-inheritance and test-rename items in. Original v1 design (dual-endpoint client) is sound and retained.

> **For Claude (downstream dev node):** REQUIRED SUB-SKILL — use
> `superpowers:executing-plans` to implement task-by-task. Implement strictly
> against the "File / Module Change Plan" below; do not refactor beyond it.

**用户需求（原文）：** K 线图左侧面板的买卖盘口（asks/bids）、成交（trades）、
以及 24h Vol / Spread / Pct 没有正确显示、没有实时刷新，一直停在
"Waiting for OKX asks / bids / trades"。需要定位原因并修复。

---

## 评审记录 (Review Notes)

> 评审人：Anthropic 工程团队资深评审（设计评审节点）。评审维度：可行性 /
> 完整性 / 一致性 / 合理性。已对照真实代码逐节核验，核验范围见下方"评审取证"。
> 所有 **P0 / P1** 问题已在本文档正文就地修复并标注，**P2** 为非阻塞的后续建议。

### 评审取证（核验过的事实，非推测）

- RCA 准确：`config.py:27` 确将 `okx_ws_public` 设为 `…/ws/v5/business`；
  `ws_client.py:87` 单连接读取该值；`ws_market.py:75` 硬编码
  `market_channels=False`；`market.py:36` 为 `bool(settings.okx_ws_public)`。✔
- 前端确已接线：`apps/web/app/page.tsx:305/311/317` 已分别处理
  `trade`/`ticker`/`book` 事件并写入 `LiveMarketState`，故"仅后端改动"的范围判断成立。✔
- 唯一读取 `okx_ws_public` 的源码点是 `ws_client.py:87`；无其他模块依赖该键取 K 线，
  故改键后纸面交易（经 `OKXWSClient` → `market_channels=False`）只要 `okx_ws_business`
  默认仍指向 business URL 即行为不变。✔
- 本机 `services/quant/.env` 存在但**未**覆盖任何 `OKX_WS_*`，因此修正后的代码默认值生效，
  本机无需改 `.env` 即可修复（这把 P1-1 从 P0 降为 P1：源码默认正确、全新检出可用）。✔

### 问题清单

| 编号 | 维度 | 严重度 | 位置 | 问题 | 处置 |
|---|---|---|---|---|---|
| P1-1 | 完整性 / 一致性 | **P1** | 文件变更计划 / `.env.example:30` | `.env.example` 仍把 `OKX_WS_PUBLIC` 钉死为 `…/ws/v5/business`，且**未列入变更计划**。任何照文档把 `.env.example` 复制成 `.env` 的人，会用 env 覆盖回 business URL → market 环（trades/tickers/books5）再次落到 business 端点 → `60018` → 同一 bug 复发。字段名即环境变量名（大小写不敏感），覆盖必然生效。 | **已修复**：把 `.env.example` 加入"文件 / 模块变更计划"；新增"配置迁移与回滚"小节，要求把 `OKX_WS_PUBLIC` 改为 public、新增 `OKX_WS_BUSINESS`，并在验收中显式检查现存 `.env` 无 `OKX_WS_PUBLIC=…/business` 残留。 |
| P2-1 | 完整性 | P2 | 实施顺序 / 全文 | 缺少显式回滚说明。 | **已补**："配置迁移与回滚"小节给出一行回滚路径（单次提交可整体 revert；两端点同主机:端口，无网络面变化）。非阻塞。 |
| P2-2 | 合理性 / 一致性 | P2 | 测试计划 / `test_ws_market.py:186` | 计划把 `test_ws_market_subscribes_candle_only` 的断言反转为 `market_channels is True`，但若保留旧函数名，测试名会与行为相悖（名曰 candle-only 实测 enabled）。 | **已修复**：变更计划改为"**重命名**为 `test_ws_market_subscribes_market_channels` 并断言 `is True`"。 |
| P2-3 | 可行性（潜在） | P2 | `ws_client.py` `start()`/`stop()` | `_stop` 事件一旦在 `stop()` 置位永不清除，`stop()` 后再 `start()` 的环会立即退出——即该客户端不可重启。 | **不在本次范围**（既有行为；客户端每条 ws 连接一次性创建/启动/停止，无重启路径）。仅记录：若未来需要重启，须在 `start()` 清 `_stop`。重构须保持单次使用语义不退化。 |
| P2-4 | 完整性 | P2 | 风险与缓解 | 代理（`HTTP(S)_PROXY`）对两条连接的适用性未单独点明（本机 `.env` 配了 Clash 代理）。 | **已补**：风险小节明确两条连接经同一 `websockets.connect` 路径、共享同一代理/可达性——business K 线今能连，public 即同样可连。 |

> **结论摘要：** 无 P0；唯一 P1（`.env.example` 漏改）已就地修复；P2 均为非阻塞后续项。
> 设计核心（双端点客户端，仅后端 + 配置模板）经核验**可行、范围合理、与现有约定一致**。

---

## Overview

The left-side market panel (`MarketDepthPanel`) is meant to show, for the
selected OKX instrument, the top-5 asks/bids ladder, a live trade tape, the 24h
volume, the bid/ask spread, and the spread percentage. Today every one of those
fields is stuck on an empty placeholder: `24h Vol —`, `Spread —`, `Pct —`,
`Waiting for OKX asks`, `Waiting for OKX bids`, `Waiting for OKX trades`. Only
`Bar Vol` shows a value, because it is derived from the candle stream
(`activeCandle.vol`), not from the order-book/trade/ticker stream. The candle
chart itself works, which is the key clue: the candle path is healthy while the
order-book/trade/ticker path is completely silent.

The root cause is an OKX WebSocket topology constraint. OKX V5 splits its public
market channels across two endpoints: **candle** channels (`candle1m` …
`candle1D`) are served only on the **business** endpoint
(`wss://ws.okx.com:8443/ws/v5/business`), while **`trades` / `tickers` /
`books5`** are served only on the **public** endpoint
(`wss://ws.okx.com:8443/ws/v5/public`). The backend opens exactly one OKX
connection per browser stream (`OKXMarketWSClient`) and points it at
`config.okx_ws_public`, which is currently set to the *business* URL
(`services/quant/app/config.py:27`). Subscribing `trades`/`tickers`/`books5` on
the business endpoint makes OKX reply with error `60018` ("channel doesn't
exist"). To stop that error from polluting the chart feed, a previous change
hard-wired the chart route to `market_channels=False`
(`services/quant/app/api/ws_market.py:75`), which means those three channels are
never subscribed at all. The depth panel therefore never receives a single
`book`, `trade`, or `ticker` event. This is confirmed by the standalone probe
`tmps/okx_direct.py` (trial A "candle-only on business" works; trial B
"candle+trades on business" gets `60018`) and is even pinned by an existing
regression test (`test_ws_market_subscribes_candle_only` asserts
`market_channels is False`).

The fix is to stop trying to multiplex incompatible channels onto one endpoint
and instead open **two** OKX connections per browser market stream: one to the
**business** endpoint for the candle channel (unchanged behavior), and one to
the **public** endpoint for `trades` / `tickers` / `books5`. Both connections
feed the same `MarketWSHandlers`, so `ws_market.py` forwards the existing
`candle` / `trade` / `ticker` / `book` / `error` event union to the browser
exactly as the frontend already expects. The entire frontend stack
(`apps/web/app/page.tsx` event handler, `LiveMarketState`, `MarketDepthPanel`,
the `marketDepth.ts` pure helpers, and the `types.ts` `MarketWsEvent` union) is
already implemented and correct; it just never receives data. Consequently this
change is backend-only and low-risk.

## Root Cause Analysis (evidence)

| Symptom | Mechanism | Evidence |
|---|---|---|
| `Waiting for OKX asks/bids/trades` | `book`/`trade` events never sent | `MarketDepthPanel.tsx:49,86` render these strings when `market.trades`/`book` is empty. |
| `24h Vol —`, `Spread —`, `Pct —` | `ticker` and `book` never arrive | `MarketDepthPanel.tsx:38-41` read `market.ticker.vol24h` and `bookSpread(market.book)`. |
| `Bar Vol 377.3` works | candle path is healthy | derived from `activeCandle.vol`, fed by the working business-endpoint candle stream. |
| Market channels disabled | route forces candle-only | `ws_market.py:74-76` passes `market_channels=False` with comment about `60018`. |
| Single endpoint, wrong split | one connection to business URL | `ws_client.py:87` connects `self._settings.okx_ws_public`; `config.py:27` sets it to `…/ws/v5/business`. |
| Candle ≠ public-channel topology | OKX serves them on different URLs | `tmps/okx_direct.py` probe; OKX docs `https://www.okx.com/docs-v5/zh/` (WebSocket → business vs public channels). |

## Technical Design

### Endpoint split (config)

Restore honest endpoint naming and add the missing one:

- `okx_ws_public` → `wss://ws.okx.com:8443/ws/v5/public` (true public channels:
  `trades`, `tickers`, `books5`).
- `okx_ws_business` → `wss://ws.okx.com:8443/ws/v5/business` (candle channels).

Both endpoints are on the same host/port as today, so no new network reachability
or proxy is introduced — if the business candle stream already connects from this
machine, the public stream connects identically.

### Two-connection client (`OKXMarketWSClient`)

Refactor `OKXMarketWSClient` so a single client object manages **one or two**
connection loops, dispatching every channel to the same handlers:

1. Replace the single `_task: asyncio.Task | None` with a list
   `_tasks: list[asyncio.Task]`.
2. Extract the existing `_run()` body into a generic
   `async def _run_endpoint(self, url: str, args: list[dict]) -> None` that takes
   the endpoint URL and the subscription `args` (keep the existing backoff,
   heartbeat, reconnect, and stop semantics verbatim; just parameterize the URL
   and the subscribe payload).
3. Split `_subscription_args()` into:
   - `_candle_args()` → `[{"channel": self._channel, "instId": self.inst_id}]`
   - `_market_args()` → `[{"channel": "trades", "instId": …},
     {"channel": "tickers", "instId": …}, {"channel": "books5", "instId": …}]`
4. `start()` creates the business (candle) task always, and the public (market)
   task only when `self._market_channels` is true:

```python
def start(self) -> None:
    if self._tasks:
        return
    self._tasks.append(asyncio.create_task(
        self._run_endpoint(self._settings.okx_ws_business, self._candle_args())
    ))
    if self._market_channels:
        self._tasks.append(asyncio.create_task(
            self._run_endpoint(self._settings.okx_ws_public, self._market_args())
        ))
```

5. `stop()` sets `_stop`, cancels every task in `_tasks`, awaits each guarded by
   `except (asyncio.CancelledError, Exception)`, and clears the list.

`_handle_message()` / `_dispatch_row()` are unchanged: they already branch on
`msg["arg"]["channel"]` and route candle vs trades/tickers/books5 to the right
parser and handler. Because each endpoint only carries channels it actually
serves, no `60018` error is generated in normal operation. The heartbeat (raw
`"ping"` every 20 s, reply `"pong"`) is created per connection inside
`_run_endpoint`, so both sockets stay alive.

The candle-only compatibility wrapper `OKXWSClient` (used by paper trading) keeps
passing `market_channels=False`; with the new `start()`, that spawns only the
business candle loop — behavior is unchanged for paper trading.

### Chart route (`ws_market.py`)

Stop forcing candle-only. Construct the client with the default
`market_channels=True` (i.e. remove the `market_channels=False` argument and its
now-obsolete comment). The `on_book` / `on_trade` / `on_ticker` handlers are
already wired and forward `book` / `trade` / `ticker` events to the browser; they
simply start firing once the public connection exists. The existing `on_error`
behavior (forward advisory error, never tear down the candle stream) is retained
as defense-in-depth.

### Sequence of operations (after fix)

```
Browser opens /api/ws/market?instId=BTC-USDT-SWAP&bar=1m
  -> ws_market.py validates instId/bar
  -> OKXMarketWSClient(market_channels=True).start()
       -> task A: connect business, subscribe candle1m
       -> task B: connect public,   subscribe trades + tickers + books5
  -> task A candle rows  -> on_candle  -> {"type":"candle", ...}  (chart)
  -> task B trade rows   -> on_trade   -> {"type":"trade",  ...}  (tape, lastPrice)
  -> task B ticker rows  -> on_ticker  -> {"type":"ticker", ...}  (24h Vol)
  -> task B books5 rows  -> on_book    -> {"type":"book",   ...}  (asks/bids, spread, pct)
Frontend page.tsx already routes each event into LiveMarketState
  -> MarketDepthPanel renders ladders, tape, 24h Vol, Spread, Pct
```

### Why no frontend change is needed

`apps/web/app/page.tsx:296-342` already handles `trade`, `ticker`, `book`, and
`error` and assembles `LiveMarketState` (`page.tsx:407-416`).
`apps/web/lib/marketDepth.ts` already provides `bookSpread`, `bookSideTotals`,
and `mergeTradeTape`. `apps/web/lib/types.ts` already declares the
`MarketWsEvent` union. `MarketDepthPanel.tsx` already renders all fields. The
panel is data-starved, not logic-broken.

## File / Module Change Plan

| File | Action | Intent (one line) |
|---|---|---|
| `services/quant/app/config.py` | modify | Set `okx_ws_public` to the real public URL and add `okx_ws_business` (business URL for candles); update the explanatory comment. |
| `.env.example` | modify | **(P1-1)** Change `OKX_WS_PUBLIC` to the real public URL, add `OKX_WS_BUSINESS=wss://ws.okx.com:8443/ws/v5/business`, and fix the comment. Leaving the old `OKX_WS_PUBLIC=…/business` here re-introduces the exact bug for anyone who copies the template into `.env`. |
| `services/quant/app/okx/ws_client.py` | modify | Refactor `OKXMarketWSClient` to run two endpoint loops (`_run_endpoint`), split args into `_candle_args()`/`_market_args()`, gate the public loop on `market_channels`, and cancel all tasks in `stop()`. |
| `services/quant/app/api/ws_market.py` | modify | Drop `market_channels=False` so the chart route subscribes market channels (now via the public endpoint); remove the obsolete `60018` workaround comment. |
| `services/quant/app/api/market.py` | modify | `wsConfigured` reflects both endpoints: `bool(settings.okx_ws_public and settings.okx_ws_business)`. |
| `services/quant/tests/test_ws_market.py` | modify | Update `test_market_client_subscription_scope` for `_candle_args()`/`_market_args()`; **rename** `test_ws_market_subscribes_candle_only` → `test_ws_market_subscribes_market_channels` and invert its assertion to `market_channels is True` **(P2-2: rename so the test name matches the new behavior — do not keep a `candle_only` name asserting the opposite)**; keep the 60018-advisory test. |
| `services/quant/tests/test_market_headers.py` | modify | Add `okx_ws_business` to `_FakeSettings` so monkeypatched settings stay complete. |
| `services/quant/tests/test_okx_ws_endpoints.py` | create (optional but recommended) | Unit-test that `start()` opens two tasks when `market_channels=True` and one when `False`, and that candle args target the business URL while market args target the public URL (use a fake `websockets.connect` / capture, no real network). |

> The only file outside `services/quant/` is the repo-root **`.env.example`**
> config template (P1-1). The web client, desktop shell, and DB are untouched —
> no source logic outside `services/quant/` changes.

### 配置迁移与回滚 (Config migration & rollback)

**Migration (P1-1).** `okx_ws_public` / `okx_ws_business` are env-overridable
(field name == env var name, case-insensitive). The dev node must, in addition
to `config.py`:

1. Update `.env.example` (see change plan) so the documented template no longer
   pins `OKX_WS_PUBLIC` to the business URL.
2. Note in the acceptance step: **verify no existing `services/quant/.env` (or
   `apps/web/.env.local`) overrides `OKX_WS_PUBLIC` to a `…/ws/v5/business` URL.**
   On the current machine `services/quant/.env` exists but does **not** set any
   `OKX_WS_*`, so the corrected defaults apply with no `.env` edit; this check
   protects other machines / future copies from silently re-breaking the panel.
3. If anyone previously customized `OKX_WS_PUBLIC` to route candles through a
   proxy/mirror, that custom value must now be moved to `OKX_WS_BUSINESS`
   (candles read the business key after this change).

**Rollback (P2-1).** All changes land in a single commit; rolling it back
restores the prior single-endpoint behavior wholesale. Both endpoints share the
same host:port (`ws.okx.com:8443`), so the change opens no new network surface
and there is nothing to undo beyond the revert (no schema, no migration, no
persisted state).

## Interface Design

No public interface changes. The `/api/ws/market?instId=…&bar=…` route, its query
params, and the browser-facing event union are unchanged:

```ts
type MarketWsEvent =
  | { type: "candle"; data: Candle }
  | { type: "trade";  data: MarketTrade }      // emits camelCase tradeId
  | { type: "ticker"; data: MarketTicker }     // emits camelCase volCcy24h
  | { type: "book";   data: OrderBookSnapshot }
  | { type: "error";  data: { code: string; message: string } };
```

Internal (non-public) signature changes in `OKXMarketWSClient`:

- `_run_endpoint(url: str, args: list[dict]) -> None` (new; replaces `_run`).
- `_candle_args() -> list[dict]`, `_market_args() -> list[dict]` (replace
  `_subscription_args`).
- `start()` / `stop()` keep the same signature but manage a task list.

Config keys (env-overridable, names == env var names, case-insensitive):

- `OKX_WS_PUBLIC` (default `wss://ws.okx.com:8443/ws/v5/public`).
- `OKX_WS_BUSINESS` (default `wss://ws.okx.com:8443/ws/v5/business`).

## Data Model

No database or schema change. All affected data is transient: OKX rows are
normalized into the existing Pydantic models (`Candle`, `MarketTrade`,
`MarketTicker`, `OrderBookLevel`, `OrderBookSnapshot` in
`services/quant/app/okx/types.py`) and streamed; the frontend holds them in React
state (`marketTicker`, `orderBook`, `tradeTape`, `lastPrice`, `lastMarketTs`).
Order-book updates remain whole-snapshot replacements (`books5`), and the trade
tape stays bounded (40 rows via `mergeTradeTape`). Nothing is persisted to SQLite.

## Testing & Acceptance Criteria

**Regression test that reproduces the bug first (per project rule):** add/adjust
a backend unit test asserting that the chart route (`ws_market.py`) constructs
the OKX client with market channels **enabled**, and that the client subscribes
`trades`/`tickers`/`books5` on the public endpoint and `candle{bar}` on the
business endpoint. Before the fix this test fails (channels disabled / wrong
endpoint); after the fix it passes.

Backend unit tests (no live network):

- `start()` creates two connection tasks when `market_channels=True`, one when
  `False`; `stop()` cancels all and is idempotent.
- `_candle_args()` targets the candle channel only; `_market_args()` returns
  exactly `trades`, `tickers`, `books5` for `inst_id`.
- The candle loop uses `settings.okx_ws_business`; the market loop uses
  `settings.okx_ws_public` (assert via a captured/fake `websockets.connect`).
- `_dispatch_row` still routes each channel to the correct parser/handler
  (existing coverage; keep green).
- `ws_market.py` forwards `candle`/`trade`/`ticker`/`book` with the documented
  camelCase JSON shape (existing `test_ws_market_forwards_typed_market_events`
  stays green) and rejects bad `instId`/`bar` with a typed `error`
  (`test_ws_market_rejects_invalid_params` stays green).
- A per-channel `60018`-style error is still forwarded as advisory info and does
  not close the browser socket (`test_ws_market_keeps_candle_stream_after_non_candle_error`
  stays green).

Run commands:

```bash
cd services/quant
.venv/Scripts/python -m pytest tests/test_ws_market.py tests/test_okx_normalize.py tests/test_market_headers.py -q
```

Manual acceptance (opt-in, depends on OKX reachability):

1. `pnpm dev`, select `BTC-USDT-SWAP`, bar `1m`.
2. Within a few seconds the left panel shows a live asks ladder, a live bids
   ladder, a moving trade tape, a numeric `24h Vol`, and numeric `Spread` / `Pct`
   — no more `Waiting for OKX …` placeholders.
3. Switch bar to `1H`/`4H`/`1D`: the ladder, tape, spread, and 24h volume keep
   updating even though the candle body changes rarely.
4. Switch instrument: the old ladder/tape clear and the new instrument's data
   appears; only the selected instrument streams.
5. Kill network / OKX unreachable: panel shows disconnected/empty state, never
   fabricated levels (honors the "never fake market data" rule).
6. **(P1-1) Config check:** confirm no `services/quant/.env` /
   `apps/web/.env.local` sets `OKX_WS_PUBLIC` to a `…/ws/v5/business` URL, and
   that `.env.example` now documents the public URL plus `OKX_WS_BUSINESS`.

Acceptance is met when items 1–6 hold and the backend test suite is green.

## Risks & Mitigations

- **Two OKX connections per browser stream.** Doubles connection count vs today
  (one). Acceptable for local/desktop single-user use. Mitigate by scoping each
  connection to the active instrument only; a shared backend fan-out cache is a
  documented future option if hosted multi-user is targeted.
- **Public endpoint geo/availability differs from business.** Both are the same
  host:port (`ws.okx.com:8443`); if candles connect today, the public path
  should too. Both connections go through the **same** `websockets.connect`
  call path and therefore inherit the **same proxy** (`HTTP_PROXY` /
  `HTTPS_PROXY` / `ALL_PROXY`, e.g. the Clash proxy configured on this machine)
  and the same reachability — there is no new proxy/egress configuration to add
  (P2-4). Mitigate: the `on_error`/reconnect path already keeps the candle
  stream alive if the public socket flaps, and the panel degrades to empty states
  rather than faking data.
- **Stale `.env` overriding the endpoint (P1-1).** A `services/quant/.env` (or
  copied `.env.example`) that still sets `OKX_WS_PUBLIC=…/ws/v5/business` will
  silently re-route the public/market loop onto the business endpoint and
  resurrect the `60018` failure even after the code fix. Mitigate: the change
  plan updates `.env.example`; the acceptance step verifies no stale override
  (see "配置迁移与回滚").
- **`books5` update frequency (~100 ms) causing render pressure.** Snapshot
  replacement is cheap and the panel renders 5 levels per side; if profiling shows
  pressure, throttle frontend state to one update per animation frame. Not part of
  this fix; flagged for follow-up only.
- **Existing tests encode the old (broken) workaround.** `test_ws_market_subscribes_candle_only`
  asserts `market_channels is False` and must be updated, or CI will "pass" while
  the bug persists. The change plan explicitly updates it.
- **Stale monkeypatched settings missing the new key.** Any `_FakeSettings`/
  fixture that mirrors `Settings` must add `okx_ws_business`. The change plan
  updates `test_market_headers.py`; the dev node must grep for other fakes that
  read ws endpoints before finishing.
- **Reconnect storm on repeated OKX disconnects.** Preserved per-loop exponential
  backoff (1 s → 30 s cap) bounds reconnect rate independently on each endpoint.

## Implementation Sequence

1. Add the failing/inverted regression test (channels enabled, correct
   endpoints) — red.
2. `config.py`: fix `okx_ws_public`, add `okx_ws_business`.
3. `.env.example`: fix `OKX_WS_PUBLIC`, add `OKX_WS_BUSINESS`, fix comment (P1-1).
4. `ws_client.py`: refactor to `_run_endpoint` + `_candle_args`/`_market_args` +
   task-list `start`/`stop`.
5. `ws_market.py`: remove `market_channels=False`.
6. `market.py`: update `wsConfigured`.
7. Update `test_ws_market.py` (incl. the P2-2 rename) and `test_market_headers.py`.
8. Run the backend test subset, then the full `services/quant` suite; optionally
   run `tmps/okx_direct.py`-style live probe to confirm the public endpoint
   streams (opt-in). Then run acceptance item 6 (stale-`.env` check).

## 评审结论 (Review Verdict)

**结论：通过（Approved）。**

依据：
- **可行性** — 双端点客户端重构（`_run_endpoint` + `_candle_args`/`_market_args`
  + 任务列表式 `start`/`stop`）完全落在现有技术栈（`asyncio` + `websockets`）内，
  逐节核验过的源码与方案一致，无技术阻塞。
- **完整性** — RCA 有真实代码取证；测试覆盖了"先红后绿"的回归用例、两端点路由、
  幂等 `stop()`、既有事件转发与错误降级。原方案唯一缺口（`.env.example` 漏改，P1-1）
  已就地补入变更计划，并新增配置迁移、回滚与 stale-`.env` 验收检查。
- **一致性** — 仅后端 + 配置模板改动，前端确已接线（已核验 `page.tsx` 事件处理）；
  保留"绝不伪造行情"红线与既有 camelCase 事件契约；与 CLAUDE.md 的"修 bug 先写复现
  测试""文件/函数尺寸"约定一致。
- **合理性** — 范围恰当，未过度设计；新增端点路由单测标为可选；共享后端扇出缓存
  等仅作为多用户场景的后续选项被记录，不纳入本次。

**P0 / P1 状态：全部已就地修复，无遗留。** 具体：P1-1（`.env.example`）已加入
"文件 / 模块变更计划"+"配置迁移与回滚"+验收第 6 项。

**非阻塞后续项（P2，不影响通过）：**
- P2-1 回滚说明 —— 已补。
- P2-2 测试重命名（`…subscribes_market_channels`）—— 已写入变更计划，开发节点执行即可。
- P2-3 `_stop` 不可重启 —— 既有行为、客户端单次使用，本次不处理，仅留记录；重构须保持
  单次使用语义不退化。
- P2-4 代理继承说明 —— 已补入风险小节。

**移交开发节点（Subtask #2）的硬性约束：** 严格按 v2 的"文件 / 模块变更计划"逐项落地，
**务必包含 `.env.example`（P1-1）与 `test_ws_market.py` 的重命名（P2-2）**，不要在方案之外
追加重构。
