# Live Market Depth Panel Implementation Plan

> **Version:** v2

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Make the live price line, latest price label, trade tape, volume, and order-book depth update in real time for the currently selected OKX instrument and bar, while keeping the chart-side market panel at 10% width.

**Architecture:** Extend the existing public market WebSocket path instead of adding polling loops. The backend will subscribe to OKX public candle, trade, ticker, and `books5` channels for exactly the active instrument, normalize the payloads into typed events, and stream them through `/api/ws/market`. The frontend will consume one reconnecting market stream, update the K-line candle, price line, latest price label, volume, and a compact 10% market-depth panel without touching backtest or simulator correctness.

**Tech Stack:** FastAPI, Python 3.11, websockets, Pydantic, Next.js 14, React 18, TypeScript, Zustand, React Query, Tailwind, lightweight-charts v4, Vitest, pytest.

---

## 璇勫璁板綍 (评审记录)

- **P1 - 已解决：实时市场流与历史图表区间的边界不够明确。**
  原设计要求在所有 K 线周期下实时更新价格线、价格数值、盘口和成交，
  但没有明确禁止 public candle 在 `run-range` / `compare-range` 历史视图中
  改写图表。v2 在 Technical Design 中拆分
  `shouldStreamMarket()` 与 `shouldApplyPublicCandle()`，要求实时交易/盘口流
  始终可用，而 candle 只在 `live-follow` 且未由 paper stream 驱动时改写 K 线。
- **P1 - 已解决：现有 `latestCandlesQ` REST 轮询与新 WebSocket 流可能并行写入
  `lastPrice` / `forming`。** v2 明确将 REST 最新 K 线轮询降级为 WebSocket
  未连接时的低频兜底，WS 打开后不得继续用轮询覆盖实时状态。
- **P1 - 已解决：后端入参和事件 JSON 契约缺少硬边界。** v2 补充 `bar` 必须
  在 `BAR_TO_WS_CHANNEL` 中、`instId` 必须是 OKX 合约/现货格式且订阅失败要
  发送 typed `error` 后关闭浏览器 WS；同时要求 Python snake_case 字段通过
  alias/serializer 输出为前端文档中的 camelCase。
- **P2 - 条件项：10% 面板宽度在窄桌面下仍需保留可用下限。** 规格允许
  `basis: 10%` 配合 `min-width` / `max-width`，因此不是阻塞项；实现时需要
  视觉验收确认没有文本溢出。
- **P2 - 条件项：真实 OKX payload 示例仍需在实现阶段用官方文档或手工抓包
  固化到 parser tests。** 这不阻塞设计通过，但不能用臆造字段完成测试。

## Overview

The current workstation already displays real OKX K-line data and can keep the latest candle moving through `/api/ws/market`, but the live experience is still too candle-centric. Users need the visual price line and numeric latest price to change whenever the market trades, independent of the selected K-line interval. They also need a narrow right-side market panel inside the chart area that shows current trade activity, live volume, and the top order-book levels. This is especially important when the user switches to long intervals such as `1H`, `4H`, or `1D`: the candle body may not visibly change often, but the price, tape, and order book must still update continuously.

This feature adds a market microstructure layer to the existing left chart workspace. The K-line chart remains the primary surface, and the strategy panel on the far right remains available for backtest, compare, and paper-trading workflows. Inside the chart workspace, the current instrument list area should be replaced or resized into a 10% width live market panel on desktop. That panel will show latest price, session volume, top bids and asks, spread, and recent public trades. The chart itself will draw a horizontal real-time price line and last-price label on the candlestick series. The line must update from trade or ticker events, not only from candle close events.

The implementation must preserve the project's hard rule: never fake market data. If OKX is unreachable, the backend should close or emit an explicit error state; the frontend should show disconnected/stale status rather than synthetic trades or order-book rows. The OKX public documentation states that public WebSocket channels do not require authentication and include K-line, ticker, order-book, and related market-data channels. OKX also documents `books5` as a public snapshot depth channel published at approximately 100 ms when the book changes. This design therefore uses public channels only and does not introduce API keys or private account permissions.

## Technical Design

The existing `services/quant/app/api/ws_market.py` route accepts `instId` and `bar`, creates `OKXWSClient(inst_id, bar, on_candle)`, and forwards only `{"type":"candle"}` messages. Replace this single-purpose client with a multi-channel market stream while keeping the same route path for compatibility. The route should still accept `instId` and `bar`, validate `bar` against the same supported bar set used by `BAR_TO_WS_CHANNEL`, validate `instId` as a non-empty OKX instrument identifier matching the existing instrument-selector format, and start exactly one backend OKX connection per browser WebSocket. Invalid params should emit a typed `error` event and close the browser WebSocket instead of silently falling back to another instrument or interval. That connection must subscribe to:

- `candle{bar}` for the selected K-line interval.
- `trades` for latest transaction price and trade tape.
- `tickers` for last price and 24h/session-style volume fields.
- `books5` for top five bid and ask levels.

The OKX docs list public WebSocket channels as not requiring authentication; the implementation must keep this flow unauthenticated. `books5` should be preferred over incremental `books` for v1 because it is a bounded snapshot suitable for a compact panel and avoids local sequence reconciliation logic. If later work needs deeper ladders, it can add `books` with checksum handling, but that is not needed for a 10% panel.

Create normalized backend models in `services/quant/app/okx/types.py`:

```python
class MarketTrade(BaseModel):
    ts: int
    price: float
    qty: float
    side: Literal["buy", "sell"]
    trade_id: str | None = None

class OrderBookLevel(BaseModel):
    price: float
    qty: float
    orders: int | None = None

class OrderBookSnapshot(BaseModel):
    ts: int
    bids: list[OrderBookLevel]
    asks: list[OrderBookLevel]

class MarketTicker(BaseModel):
    ts: int
    last: float
    open24h: float | None
    vol24h: float | None
    vol_ccy24h: float | None
```

All timestamps remain seconds-level UTC after the OKX adapter boundary. OKX raw `ts` values are milliseconds strings, so each parser should divide by 1000 exactly once, matching the existing candle normalization rule. Add parsing helpers such as `parse_okx_trade(row)`, `parse_okx_books5(row)`, and `parse_okx_ticker(row)` next to `parse_okx_candle()`. Treat malformed rows as dropped rows and log at debug level; do not send partial invalid objects to the frontend. When serializing events to the browser, keep the documented JSON field names stable: `trade_id` must be emitted as `tradeId`, and `vol_ccy24h` must be emitted as `volCcy24h`. Use Pydantic aliases or an explicit serializer at the route boundary; do not rely on frontend-side renaming.

Refactor `services/quant/app/okx/ws_client.py` into a channel-aware client without creating a large framework. A practical shape is `OKXMarketWSClient(inst_id, bar, handlers)` where `handlers` is a small dataclass or protocol with async callbacks for `on_candle`, `on_trade`, `on_book`, `on_ticker`, and `on_error`. The subscribe payload should include all four channel objects in one `args` array. `_handle_message()` should inspect `msg["arg"]["channel"]` and dispatch to the proper parser. Keep the existing heartbeat, reconnect backoff, and stop semantics. On reconnect, the client resubscribes to the same selected instrument and bar only; it must not fan out to all instruments.

Update `/api/ws/market` to forward these normalized events:

```json
{"type":"candle","data":{"ts":1710000000,"o":1,"h":1,"l":1,"c":1,"vol":1,"confirm":false}}
{"type":"trade","data":{"ts":1710000001,"price":65123.4,"qty":0.01,"side":"buy","tradeId":"..."}}
{"type":"ticker","data":{"ts":1710000001,"last":65123.4,"open24h":64000,"vol24h":1234.5,"volCcy24h":80000000}}
{"type":"book","data":{"ts":1710000001,"bids":[{"price":65123.3,"qty":2.4,"orders":5}],"asks":[...]}}
{"type":"error","data":{"code":"OKX_WS_ERROR","message":"..."}}
```

The route should keep accepting client heartbeat messages, but it should not require the frontend to send subscription updates after connection. Instrument and bar changes already recreate the frontend `marketWS(instId, bar, ...)`; continue that pattern so old streams are closed cleanly. If OKX emits subscription errors, forward one `error` event and close the browser WebSocket after setting the closed flag. Network reconnects inside `OKXMarketWSClient` should remain internal and not close the browser WebSocket unless the browser disconnects.

On the frontend, extend `apps/web/lib/types.ts` with `MarketTrade`, `OrderBookLevel`, `OrderBookSnapshot`, `MarketTicker`, and a discriminated `MarketWsEvent` union. Replace `WsEvent`'s loose `any` usage for market paths where possible, while keeping `paperWS()` compatible with existing paper events. Add small pure helpers in a new `apps/web/lib/marketDepth.ts`:

- `mergeTradeTape(prev, trade, limit = 40)` appends new trades, dedupes by `tradeId` when present, sorts descending by timestamp for display, and caps memory.
- `resolveLivePrice(candle, trade, ticker)` returns the newest reliable numeric price based on event timestamp, preferring trade/ticker over candle when newer.
- `bookSpread(snapshot)` returns absolute spread and spread percentage, or `null` when one side is missing.
- `bookSideTotals(levels)` returns cumulative quantities for horizontal depth bars.

`apps/web/app/page.tsx` currently has `forming`, `lastPrice`, `liveWsOpen`, and one `marketWS()` consumer that only handles candles. Expand this state to include `marketTicker`, `orderBook`, `tradeTape`, and `lastMarketTs`. The event handler should:

1. For `candle`, keep the current confirmed/forming candle behavior and update `lastPrice` from `c.c` only if no newer trade/ticker exists.
2. For `trade`, append to the tape, set `lastPrice` to `trade.price`, and update `lastMarketTs`.
3. For `ticker`, set ticker state, set `lastPrice` to `ticker.last`, and update 24h volume display fields.
4. For `book`, replace the current snapshot wholesale and update `lastMarketTs`.
5. For `error`, set a visible `marketError` but keep existing candles on screen.

This one stream must be active whenever live market data is expected. The current `shouldFollowLive()` only enables market WS for `chartView === "live-follow"` and non-paper mode. The new requirement says real-time price line, live price, and order book must update regardless of the displayed K-line interval. It also says they must update even when the user switches K-line timeframe. Therefore, replace the frontend gating with two explicit helpers:

- `shouldStreamMarket(mode, okxUp)` returns true whenever OKX is not known down. It controls trade, ticker, and book subscriptions for all modes.
- `shouldApplyPublicCandle(chartView, mode, paperSessionId)` returns true only for live-follow chart views that are not already driven by `paperWS`.

In paper mode, keep the paper simulation stream for strategy trades/equity, but still allow the public market stream for price line, ticker, and book unless product owners explicitly want paper to be isolated. To avoid double candle mutation in paper mode, only use public market candle events for the chart when `shouldApplyPublicCandle()` is true; still use public trade/ticker/book for the panel. In backtest `run-range` and compare `compare-range`, never append public candle events to the chart data because those views represent a fixed historical range. The live price line and market-depth panel may still update from trade/ticker/book over the static historical chart.

The existing `latestCandlesQ` REST refetch loop should not compete with the WebSocket stream. Keep it only as a low-frequency fallback when the market WebSocket is not open and the chart is in `live-follow`; when the WS is connected, `lastPrice`, `forming`, and live candle updates must come from WS events only.

Update `apps/web/components/Chart/CandleChart.tsx` to accept `livePrice?: number | null`. On chart creation, create a `priceLineRef` with `candleSeries.createPriceLine()` or update an existing price line if the installed lightweight-charts version requires recreation. The line should be horizontal, use the accent color or a neutral market color, show its axis label, and update on every `livePrice` change. Hide the line when `livePrice == null`. Do not use `setData()` just to move the price line; the existing candle set/update path should remain responsible for bars.

Update `apps/web/components/Chart/ChartWorkspace.tsx` layout. On desktop, the upper chart area should use a three-column grid or flex layout: left live market panel at exactly `10%` basis, main chart at `90%` of the chart workspace's upper row, and the separate strategy panel remains outside this component in `page.tsx`. The current `InstrumentList` consumes `lg:w-1/5`; change that area to `lg:basis-[10%] lg:min-w-[128px] lg:max-w-[180px]`. Because 10% can become too narrow on medium screens, define a fallback where screens below `lg` stack the panel above or collapse it into a compact tab. The requirement is desktop-width 10%, not unusable mobile compression.

Create `apps/web/components/Chart/MarketDepthPanel.tsx`. It should be a dense operational panel, not a marketing card. Suggested sections:

- Header with `instId`, live/disconnected dot, latest price, and small timestamp age.
- Volume row using ticker `vol24h` and current candle `vol`.
- Spread row with `ask[0] - bid[0]` and percentage.
- Order book table with asks above bids, price, quantity, cumulative depth bar, and side colors.
- Recent trades tape with time, side, price, and quantity.

Keep text short because the panel is only 10% wide. Use tabular numbers and existing CSS variables. Do not put cards inside cards; this is a panel section inside the chart workspace. If data is not yet available, show explicit muted empty states such as "Waiting for OKX book" instead of placeholder fake levels.

Pass the new panel data from `Page` into `ChartWorkspace` as a single `market` prop:

```ts
interface LiveMarketState {
  instId: string;
  lastPrice: number | null;
  ticker: MarketTicker | null;
  book: OrderBookSnapshot | null;
  trades: MarketTrade[];
  connected: boolean;
  error: string | null;
  updatedTs: number | null;
}
```

`ChartWorkspace` passes `market.lastPrice` to both `ChartToolbar` and `CandleChart`, and passes the whole state to `MarketDepthPanel`. `ChartToolbar` can keep its existing live-price chip, but the source should now be the real market state rather than candle-only state. `InstrumentList` should either move into the market panel as a compact selector at the top or be retained only in responsive/mobile mode. If retained on desktop, it cannot exceed the 10% panel width.

## File / Module Change Plan

| File | Intent |
|---|---|
| `services/quant/app/okx/types.py` | Add normalized `MarketTrade`, `MarketTicker`, `OrderBookLevel`, `OrderBookSnapshot` models and parser helpers for OKX `trades`, `tickers`, and `books5` rows. |
| `services/quant/app/okx/ws_client.py` | Replace candle-only dispatch with a multi-channel `OKXMarketWSClient` that subscribes to candle, trades, ticker, and books5 for one active instrument. |
| `services/quant/app/api/ws_market.py` | Forward normalized candle/trade/ticker/book/error events through the existing `/api/ws/market` route and preserve clean browser disconnect handling. |
| `services/quant/tests/test_okx_normalize.py` | Add parser tests for trade, ticker, and books5 payloads, including ms-to-second conversion and malformed row rejection. |
| `services/quant/tests/test_api.py` or `test_ws_market.py` | Add backend WebSocket tests using a fake OKX client to assert event shapes and selected instrument/bar subscription behavior. |
| `apps/web/lib/types.ts` | Add market data types and a typed market WebSocket event union. |
| `apps/web/lib/ws.ts` | Keep `marketWS(instId, bar, ...)` API but allow typed market callbacks; avoid breaking `paperWS()`. |
| `apps/web/lib/marketDepth.ts` | Add pure helpers for trade-tape capping/dedupe, latest price resolution, spread calculation, and cumulative depth totals. |
| `apps/web/lib/marketDepth.test.ts` | Cover the pure market-depth helpers with Vitest. |
| `apps/web/app/page.tsx` | Own live market state, process new WS event types, stream whenever OKX is available, and pass a `market` prop to `ChartWorkspace`. |
| `apps/web/components/Chart/ChartWorkspace.tsx` | Change desktop layout to a 10% market panel plus main chart; pass `livePrice` to `CandleChart`. |
| `apps/web/components/Chart/MarketDepthPanel.tsx` | New compact UI for latest price, volume, spread, top book levels, and recent trades. |
| `apps/web/components/Chart/CandleChart.tsx` | Add/update/remove a lightweight-charts horizontal price line from `livePrice` without forcing candle data resets. |
| `apps/web/components/Chart/ChartToolbar.tsx` | Keep live price status chip but source it from the new market state and show clearer disconnected/stale status. |
| `apps/web/lib/chartLive.ts` | Replace or add `shouldStreamMarket()` and `shouldApplyPublicCandle()` helpers so trade/ticker/book streaming is not tied only to `live-follow`, while public candles cannot mutate fixed historical ranges. |

## Interface Design

### Backend WebSocket

`WS /api/ws/market?instId=BTC-USDT-SWAP&bar=1m`

Client sends no required messages after connection. Optional text heartbeats may be ignored. The backend subscribes to OKX public channels for the requested instrument and bar. Response event types are:

```ts
interface MarketCandleEvent {
  type: "candle";
  data: Candle;
}

interface MarketTradeEvent {
  type: "trade";
  data: MarketTrade;
}

interface MarketTickerEvent {
  type: "ticker";
  data: MarketTicker;
}

interface MarketBookEvent {
  type: "book";
  data: OrderBookSnapshot;
}

interface MarketErrorEvent {
  type: "error";
  data: { code: string; message: string };
}
```

No new REST API is required for v1. REST `/api/candles` remains the initial history loader and cache source; WebSocket market events only maintain live state after the initial load.

### Frontend Component Interfaces

`ChartWorkspace` receives:

```ts
interface ChartWorkspaceProps {
  // existing props...
  market: LiveMarketState;
}
```

`CandleChart` receives:

```ts
type Props = {
  // existing props...
  livePrice?: number | null;
};
```

`MarketDepthPanel` receives:

```ts
interface MarketDepthPanelProps {
  market: LiveMarketState;
  activeCandle: Candle | null;
  onSelectInstrument?: (instId: string) => void;
}
```

`onSelectInstrument` is optional. If the implementation folds the existing `InstrumentList` into the new panel, pass `setInst`; otherwise omit it and keep selection outside the panel for mobile only.

## Data Model

No database schema change is required. This feature is live display state only. The backend normalization models are transient Pydantic objects, and the frontend state is in-memory React state. Do not persist order-book snapshots, trade tape rows, or ticker values in SQLite.

Frontend state shape in `Page`:

```ts
const [marketTicker, setMarketTicker] = useState<MarketTicker | null>(null);
const [orderBook, setOrderBook] = useState<OrderBookSnapshot | null>(null);
const [tradeTape, setTradeTape] = useState<MarketTrade[]>([]);
const [lastPrice, setLastPrice] = useState<number | null>(null);
const [lastMarketTs, setLastMarketTs] = useState<number | null>(null);
const [marketError, setMarketError] = useState<string | null>(null);
```

Reset these fields when `instId` changes. When only `bar` changes, reset candle/forming state but keep ticker/book/trade state only if the instrument is unchanged and the old stream has not yet closed; once the new stream opens, all future state must come from the new subscription. The simplest implementation can reset all market state on either `instId` or `bar` for consistency.

Order-book levels are displayed as snapshots, not incremental patches. Each `book` event replaces the whole visible depth. Trade tape is bounded to 40 rows to avoid long-session memory growth. The price line uses `lastPrice` and is not persisted.

## Testing & Acceptance Criteria

Backend unit tests:

- `parse_okx_trade()` converts OKX trade rows into seconds-level UTC, numeric price/quantity, side, and trade ID.
- `parse_okx_ticker()` extracts last price and volume fields and handles missing optional values as `None`.
- `parse_okx_books5()` converts bid/ask arrays into numeric levels and preserves order counts where supplied.
- `OKXMarketWSClient` dispatches mixed channel messages to the right callbacks and ignores subscription ack messages.
- `/api/ws/market` forwards `candle`, `trade`, `ticker`, and `book` events with the documented JSON shape when driven by a fake client.
- `/api/ws/market` rejects unsupported bars and malformed instrument IDs with a typed `error` event instead of silently subscribing to a fallback.
- Serialized trade and ticker events use the documented camelCase browser fields, including `tradeId` and `volCcy24h`.

Frontend unit tests:

- `mergeTradeTape()` dedupes repeated trades and caps to 40 newest rows.
- `bookSpread()` returns correct absolute and percentage spread.
- `resolveLivePrice()` prefers newer trade/ticker prices over older candle close prices.
- `shouldStreamMarket()` allows trade/ticker/book updates outside `live-follow`, while `shouldApplyPublicCandle()` prevents public candle mutation in fixed backtest and compare ranges.
- `MarketDepthPanel` renders empty, connected, disconnected, and populated book states without overflowing narrow width.

Manual acceptance:

- Start the app with `pnpm dev`, select `BTC-USDT-SWAP`, and open the live chart.
- Latest price chip and horizontal price line update from public trade/ticker events even before the current K-line closes.
- Switch bars between `1m`, `15m`, `1H`, and `1D`; the price line, latest price, order book, and trade tape continue updating for the selected instrument.
- Switch instruments; only the newly selected instrument receives live updates, and old trade/book rows disappear or are clearly replaced.
- Desktop chart workspace shows the live market panel at 10% width, with the main chart occupying the remaining upper chart width.
- If OKX is unreachable, the UI shows a disconnected/error state and does not display fake order-book or trade data.

Run commands expected after implementation:

```bash
cd services/quant
.venv/Scripts/python -m pytest tests/test_okx_normalize.py tests/test_api.py -q
```

```bash
cd apps/web
pnpm test -- marketDepth
pnpm test
```

```bash
pnpm test
```

Network smoke validation remains opt-in because it depends on OKX reachability:

```bash
services/quant/.venv/Scripts/python scripts/smoke_e2e.py
```

## Risks & Mitigations

**Risk: Too many OKX WebSocket subscriptions.** Each browser client will create one OKX connection with four public channels. This is acceptable for local desktop use, but a hosted multi-user deployment could multiply connections. Mitigate by keeping subscriptions scoped to the active instrument only and by documenting a future shared backend fan-out cache if multi-user hosting becomes a requirement.

**Risk: `books5` updates are too frequent for React rendering.** OKX can publish book snapshots frequently. Mitigate in the frontend by replacing state at most once per animation frame or with a 100 ms throttle if profiling shows render pressure. Do not throttle the backend unless needed; dropping frontend renders is safer than blocking the WS receive loop.

**Risk: Price line flicker when trade, ticker, and candle events arrive out of order.** Mitigate with `lastMarketTs` and `resolveLivePrice()`, only allowing older events to overwrite price if no newer price exists. Candle events should not overwrite a newer trade/ticker price.

**Risk: Narrow 10% panel becomes unreadable.** Use compact labels, tabular numbers, five book levels per side, and responsive fallback below desktop breakpoints. Enforce `min-width` and `max-width` so 10% is honored on normal desktop widths without making medium screens unusable.

**Risk: Public trade side semantics differ by OKX field naming.** Keep parser tests based on real OKX sample payloads and map only documented side values to `"buy"` or `"sell"`. Unknown sides should drop the row and log debug output.

**Risk: Existing paper mode uses its own WS candle stream.** Keep public market stream for price/book/tape in paper mode, but do not let it mutate paper-specific equity/trade overlays. If duplicated candle updates cause confusing behavior, gate public candle application in paper mode while retaining ticker/trade/book updates.

**Risk: Desktop packaged app misses the new component or WS behavior.** Because this feature changes only web and Python source under existing build paths, desktop packaging should pick it up automatically. Include `npm run release:check` or `desktop` verification in a later implementation node if this feature is targeted for packaged release.

## Implementation Sequence

1. Write backend parser tests for trade, ticker, and books5 sample rows.
2. Add normalized models and parsers in `okx/types.py`.
3. Refactor `OKXWSClient` into `OKXMarketWSClient` with channel dispatch while preserving candle behavior.
4. Add route-level WS forwarding tests with a fake market client.
5. Update `ws_market.py` to emit the new event union.
6. Add frontend market types and pure `marketDepth` helpers with Vitest coverage.
7. Expand `Page` market state and event handling, including new stream gating.
8. Add `MarketDepthPanel` and wire it into a 10% `ChartWorkspace` layout.
9. Add `livePrice` price-line rendering to `CandleChart`.
10. Run targeted backend and frontend tests, then the project-level `pnpm test`.

## 璇勫缁撹 (评审结论)

**鏈夋潯浠堕€氳繃 (有条件通过).**

All P1 review concerns have been resolved in v2. No P0 concerns were found.
The remaining conditions are implementation-stage P2 checks:

- Use official OKX documentation samples or captured public payloads for parser
  tests; do not invent fixture fields.
- Visually verify the 10% market-depth panel at desktop and responsive widths so
  compact labels, numbers, and order-book rows do not overflow.
- Keep the one-OKX-connection-per-browser design scoped to desktop/local usage;
  add shared backend fan-out only if the product later targets hosted multi-user
  deployment.
