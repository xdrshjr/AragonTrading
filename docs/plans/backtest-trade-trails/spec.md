# Backtest Trade Trails Implementation Plan

> **Version:** v2
> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a clear Backtest view that shows each strategy's buy/sell/close trail on the K-line chart, with visible entry/exit markers and hover details for every trade event.

**Architecture:** Reuse the existing backtest run, trades, equity, chart pane, and tab infrastructure. Build a frontend-only visualization layer that derives trade trail segments and marker tooltip data from the existing `Trade[]` records returned by `/api/runs/{id}/trades`, then renders those derived objects in `CandleChart` without changing the simulator or persistence schema.

**Tech Stack:** Next.js 14 App Router, React 18, TypeScript, Zustand, React Query, Tailwind, lightweight-charts v4, Vitest.

## 璇勫璁板綍

Reviewed sections: Overview, Technical Design, File / Module Change Plan,
Interface Design, Data Model, Testing & Acceptance Criteria, Risks &
Mitigations, plus the current `CLAUDE.md`, project index, and relevant chart /
run / simulator files.

- **P1 - fixed:** The original line-rendering plan did not address completed
  trades whose entry and close share the same candle timestamp. `SimBroker`
  can open at the bar open and close via stop/take-profit in the same bar, and
  lightweight-charts line series require unique ascending time points. The
  design now marks these segments as non-line-renderable and keeps them visible
  through paired markers plus tooltip details.
- **P1 - fixed:** The original plan accepted the API client's default
  `api.trades(runId, 500)` limit, which can truncate active strategy trails and
  violate the requirement to inspect every buy/sell/close in a run. The design
  now requires the Backtest run-range view to request the backend maximum
  `api.trades(runId, 5000)` and surface the cap if it is reached.
- **P2 - accepted:** Crosshair-time tooltip matching is not pixel-perfect marker
  hit testing. This is acceptable for v2 because lightweight-charts v4 does not
  expose supported marker hover events; the tooltip lists all trade events on
  the hovered candle.
- **P2 - accepted:** One lightweight line series per renderable completed trade
  is right-sized for the current 5000-trade cap, but may need batching or a
  canvas/custom-series optimization later if very dense strategies become
  common.

---

## Overview

The current application can run a strategy backtest, load the selected run's K-line range, display buy/sell/close markers, and show equity in a separate chart pane. That covers the basic data path, but it does not yet make the trade path visually obvious on the K-line itself. Users need to understand how a strategy moved through the market: where it opened long or short, where it closed, whether a trade was profitable, and how each open-close pair relates to the surrounding candles.

This feature adds a dedicated Backtest tab/view behavior to the existing chart workspace. When the user is in backtest mode and chooses to display the run range, the price chart will render a "trade trail" layer derived from the run's trades. Each completed position becomes a line segment connecting the entry trade to its closing trade. The segment color communicates direction and result: long/short direction remains visible, while the close marker and tooltip expose realized PnL. Entry, exit, and forced session-end closes all remain explicit markers. Hovering near a marker or trail point shows a compact tooltip with side, timestamp, fill price, quantity, fee, PnL, reason, and position-after details.

The implementation should not rewrite the backtester. Previous iterations already fixed time-range correctness, no-lookahead execution, real K-line alignment, and desktop packaging stability. This design therefore treats backend `Trade` and `EquityPoint` records as the source of truth and only adds deterministic frontend projection code, chart rendering, and tests. If future work adds richer order IDs or position IDs, this layer can consume them, but the first version must work with the current `Trade` shape. The run-range Backtest view must explicitly request `api.trades(runId, 5000)`, matching the backend route's current maximum, so the visualization is not silently capped at the API client's default 500 rows.

## Technical Design

The central idea is to derive a small chart-view model from `Trade[]` before rendering. Current `Trade` records are ordered by `ts` and `id`; opening events have `side` equal to `long` or `short`, and closing events have `side` equal to `close`. The existing `SimBroker` is single-position: it can hold one long or short position, can close it, and can reverse by emitting a close followed by a new open. That means the frontend can pair trades with a simple stack-like state machine:

1. Sort trades by `(ts, id ?? originalIndex)`.
2. Keep `openTrade: Trade | null`.
3. When a `long` or `short` trade arrives, close any malformed existing open segment as `unclosed`, then set the new open.
4. When a `close` trade arrives and an `openTrade` exists, emit a `TradeTrailSegment` from open to close and clear the open.
5. If the loop ends with an open trade, emit no line segment, but leave its marker visible and label it as an open position in tooltip data.

This logic belongs in a pure helper module so chart rendering remains thin and testable. Create `apps/web/lib/tradeTrails.ts` with `buildTradeTrailSegments(trades: Trade[]): TradeTrailSegment[]` and `buildTradeMarkerEvents(trades: Trade[]): TradeMarkerEvent[]`. `TradeTrailSegment` should include a stable `id`, `entry`, `exit`, `direction`, `pnl`, `isWin`, `canRenderLine`, `lineColor`, and `lineStyle` hint. `canRenderLine` must be `false` when `entry.ts >= exit.ts`, because lightweight-charts line data cannot safely contain two points with the same timestamp or non-ascending timestamps. `TradeMarkerEvent` should include a stable key and formatted classification, but not preformatted display strings; formatting stays in components through existing `fmtTime`, `fmtPrice`, `fmtNum`, and `signed`.

`CandleChart` already owns the lightweight-charts candlestick series and sets trade markers with `series.setMarkers()`. Extend it to also manage a `trailSeriesRef` map keyed by segment id. Each segment with `canRenderLine === true` should render as a two-point line series on the right price scale, using `{ time: entry.ts, value: entry.price }` and `{ time: exit.ts, value: exit.price }`. Segments with `canRenderLine === false` must not create a line series; they remain represented by entry/close markers at that candle and by tooltip rows that show the pair, PnL, and reason. Use `lineWidth: 2`, hide price lines and last values, and apply a dashed style for losing segments if supported by the installed lightweight-charts version. If the version does not expose a line-style enum cleanly, keep all segments solid and rely on marker/tooltip PnL; do not add a new dependency.

Markers should remain on the candlestick series because lightweight-charts handles them well and they are anchored to candle timestamps. Replace the current marker text logic with labels that distinguish `BUY`, `SELL`, and `CLOSE`. For this app's semantics, `side: "long"` means an opening buy, `side: "short"` means an opening sell/short, and `side: "close"` means flat. Marker colors should use existing CSS variables: `--long` for long entries, `--short` for short entries, and `--muted` or a PnL-based long/short color for close markers. The close marker shape should remain a circle unless an existing icon-shaped marker is available without custom drawing.

Hover detail has two viable sources. Lightweight-charts crosshair events provide the active time and series data, but not a direct marker-hover event in v4. Implement a robust first version by using crosshair time plus the chart container pointer position. `CandleChart` should compute all trade events at the hovered `param.time`; if multiple events share the same timestamp, show them as a short list in a positioned tooltip. The tooltip should sit inside the chart container, clamp to container bounds, and disappear when the crosshair leaves or when no trade exists at the hovered timestamp. This satisfies the requirement that moving the mouse over a buy/sell/close area reveals concrete details, while avoiding unsupported marker hit-testing.

The sequence when a user runs a backtest is:

1. `StrategyPanel` calls `api.backtest(...)` with the selected instrument, bar, and time range.
2. On success it stores `runId`, optionally switches `chartView` to `run-range`, and receives metrics.
3. `Page` loads `api.run(runId)`, `api.equity(runId)`, and `api.trades(runId, 5000)` through React Query for Backtest run-range display.
4. `Page` passes `tradesQ.data` to `ChartWorkspace` as `overlayTrades`.
5. `ChartWorkspace` passes those trades to `CandleChart` when `chartPane === "price"`.
6. `CandleChart` derives markers and trail segments from `overlayTrades`, renders them over the candles, and shows tooltip details when the crosshair is on a trade timestamp.

The Backtest tab requirement should be implemented using the existing mode and tab structure instead of a new route. The app already has `mode: "backtest" | "compare" | "paper"` and `StrategyPanel` renders backtest controls when mode is not compare/paper. The user-facing label should make "Backtest" explicit in the mode selector or strategy panel header if the current UI does not already expose it clearly. This keeps state persistence compatible and avoids adding a route that would duplicate the workstation.

If `api.trades(runId, 5000)` returns exactly 5000 rows, the UI should show a compact note in the Backtest trade panel or tooltip area that the returned backend cap was reached. Do not add pagination in this iteration unless implementation discovers a concrete run that exceeds the cap; the cap warning is enough to avoid presenting a truncated trail as complete.

## File / Module Change Plan

| File | Change | Intent |
|---|---|---|
| `apps/web/app/page.tsx` | Request `api.trades(runId, 5000)` for Backtest run-range data and continue passing `undefined` outside `mode === "backtest"` plus `chartView === "run-range"`. | Avoid stale overlays and avoid the default 500-trade truncation for the main visualization. |
| `apps/web/lib/types.ts` | Add `TradeDirection`, `TradeTrailSegment`, and `TradeMarkerEvent` interfaces. | Share typed derived chart data between helper tests and chart components. |
| `apps/web/lib/tradeTrails.ts` | Create pure functions `sortTradesForTrail`, `buildTradeTrailSegments`, `buildTradeMarkerEvents`, and `tradeToneForEvent`. | Convert backend `Trade[]` into deterministic display models without side effects. |
| `apps/web/lib/tradeTrails.test.ts` | Create Vitest coverage for long wins, short wins, losses, same-timestamp reverse trades, same-candle open/close pairs, orphan closes, and unclosed opens. | Lock down the pairing algorithm before touching chart rendering. |
| `apps/web/components/Chart/CandleChart.tsx` | Replace inline marker mapping with helper output; add trail line-series lifecycle; add crosshair tooltip state and rendering. | Render connected trade trails and hover details on the K-line chart. |
| `apps/web/components/Chart/TradeTooltip.tsx` | Create a small presentational tooltip component. | Keep `CandleChart` below the file-size threshold and isolate display formatting. |
| `apps/web/components/Chart/ChartWorkspace.tsx` | Ensure the price pane receives backtest trades only when the run range is active; keep bottom trades table unchanged. | Avoid showing stale trails outside the selected run's candle range. |
| `apps/web/components/Strategy/StrategyPanel.tsx` | Clarify the backtest tab/header label if needed; no new data fetching. | Make the "Backtest" mode discoverable while preserving existing run behavior. |
| `apps/web/components/Strategy/TradesTable.tsx` | Optionally add `reason` as a compact final column if space allows. | Match tooltip details with tabular trade inspection. |
| `apps/web/lib/format.ts` | Add `fmtQty` only if repeated quantity formatting appears in tooltip/table code. | Avoid duplicating `qty.toFixed(4)` in multiple components. |

No backend file is required for the first implementation. Do not modify `services/quant/app/engine/backtester.py`, `services/quant/app/engine/simulator.py`, `services/quant/app/db/models.py`, or `services/quant/app/schemas.py` unless a downstream implementation discovers a confirmed data defect. Current fields are sufficient: `ts`, `side`, `price`, `qty`, `fee`, `pnl`, `positionAfter`, and `reason`.

## Interface Design

There is no new REST, WebSocket, or CLI interface. The feature uses these existing frontend API client methods:

```ts
api.trades(runId: number, limit = 500): Promise<Trade[]>
api.equity(runId: number): Promise<EquityPoint[]>
api.run(runId: number): Promise<Run>
api.backtest(id: number, body: BacktestRequest): Promise<{ runId: number; metrics: Metrics }>
```

For this feature's Backtest run-range view, callers must use
`api.trades(runId, 5000)`. The function default remains unchanged for
backward-compatible table callers that do not need the full visual trail.

The effective UI contract is:

```ts
type CandleChartProps = {
  candles: Candle[];
  trades?: Trade[];
  equity?: EquityPoint[];
  overlays?: Overlay[];
  indicators: Record<IndicatorKey, boolean>;
  theme: "light" | "dark";
  loading?: boolean;
  error?: string | null;
  onLoadMore?: (beforeTs: number) => void;
  jumpToTs?: number | null;
  onCrosshairChange?: (ts: number | null) => void;
};
```

Do not add server state to Zustand. `runId`, `chartView`, and existing React Query keys already determine which run data is shown. Tooltip visibility should be local `CandleChart` state because it is transient pointer state and must not persist to `localStorage`.

## Data Model

Existing backend model:

```ts
type Trade = {
  id?: number;
  runId?: number;
  ts: number;
  side: "long" | "short" | "close";
  price: number;
  qty: number;
  fee: number;
  pnl: number;
  positionAfter: number;
  reason: string;
};
```

New derived frontend-only model:

```ts
type TradeDirection = "long" | "short";

interface TradeTrailSegment {
  id: string;
  direction: TradeDirection;
  entry: Trade;
  exit: Trade;
  pnl: number;
  isWin: boolean;
  canRenderLine: boolean;
}

interface TradeMarkerEvent {
  id: string;
  trade: Trade;
  kind: "entry-long" | "entry-short" | "close" | "orphan-close";
  direction?: TradeDirection;
  pairedSegmentId?: string;
}
```

The segment id should be stable across rerenders. Prefer `runId-entryId-exitId` when both ids exist; otherwise fall back to `entry.ts-entryIndex-exit.ts-exitIndex`. The helper must not mutate input trades. It should preserve every input trade as a marker event, even if an orphan close cannot be paired into a segment. `canRenderLine` is a chart-safety flag, not a pairing flag: same-candle pairs still count as completed segments and must remain visible through markers and tooltip content.

## Testing & Acceptance Criteria

Unit tests:

1. `buildTradeTrailSegments` pairs one long entry and one close into a segment with `direction === "long"` and `pnl` copied from the close trade.
2. It pairs one short entry and one close into `direction === "short"`.
3. It sorts trades by timestamp and stable fallback index before pairing.
4. It handles a reverse sequence at the same timestamp: close old position, then open new position, without connecting the new entry to the old entry.
5. It ignores orphan close trades for segments but still returns a marker event for them.
6. It does not create a segment for an unclosed final open trade.
7. It marks same-candle entry/close segments with `canRenderLine === false`
   while still returning marker events for both trades.
8. It does not mutate the original `Trade[]`.

Component/manual acceptance:

1. In backtest mode, run any built-in strategy over a selected candle range.
2. After completion, the chart switches to or can be switched to run-range view.
3. The price K-line chart shows all entry and close markers from the run.
4. Completed trades with `entry.ts < exit.ts` show line segments connecting
   entry fill price to close fill price.
5. Long and short entries are visually distinguishable.
6. Close markers expose realized PnL and reason in the hover tooltip.
7. If several trade events share the same candle timestamp, one tooltip lists all of them.
8. Same-candle open/close pairs do not crash the chart; they appear as
   colocated markers and tooltip rows rather than invalid duplicate-time line
   data.
9. Switching instrument, bar, run, mode, or chart view clears stale trails immediately.
10. Compare mode still shows compare overlays and does not render single-run trade trails.
11. Paper mode continues to show live paper trades without requiring a completed close segment.
12. If 5000 trades are returned for a run, the UI indicates that the backend
   trade cap was reached.

Verification commands:

```powershell
cd apps/web
pnpm test -- tradeTrails
pnpm test
pnpm build
```

If the downstream engineer has a running backend and OKX access, also verify with:

```powershell
pnpm dev
```

Then open `http://localhost:18761`, run a backtest, and inspect the Backtest price chart.

## Risks & Mitigations

`lightweight-charts` marker hover support is limited in v4. The mitigation is to use crosshair time matching and show details for all trade events on the hovered candle. This is deterministic, works with keyboard/pointer crosshair movement, and does not rely on undocumented marker hit-testing.

Multiple trades can share the same timestamp, especially reversals. The pairing helper must sort by timestamp and original order/id, and the tooltip must render a list rather than assuming one event per candle.

Line-series proliferation can become expensive if a strategy creates hundreds or thousands of trades. The first version should render one lightweight two-point line per completed segment that has `canRenderLine === true` and clean up removed series through a ref map. If performance becomes poor with very active strategies, a later optimization can bucket segments into fewer custom series or draw on a canvas overlay, but that is not needed for the current 5000-trade visualization cap.

The API client default `api.trades(runId, 500)` can truncate active runs. This design keeps the function default for backward compatibility, but the Backtest run-range visualization must call `api.trades(runId, 5000)`, which matches the backend route's current maximum. If a run reaches that cap, show a compact warning and treat full pagination as future work after measuring response size and chart performance.

Stale visualization is a real risk because `runId`, `chartView`, and mode interact. The implementation must gate trails to `mode === "backtest"` and `chartView === "run-range"` in `Page` or `ChartWorkspace`, and must pass `undefined` trades otherwise. `CandleChart` must remove all trail line series when `trades` becomes empty or undefined.

Desktop package stability was recently fixed. This feature should not use Next image optimization, dynamic server-only imports, or browser globals outside client components/effects. New code should remain compatible with the packaged static asset path and existing dynamic import pattern for chart components.

## 璇勫缁撹

**鏈夋潯浠堕€氳繃**

P0/P1 concerns have been resolved in this v2 design. Implementation may proceed
with these conditions:

- Keep the feature frontend-only unless implementation uncovers a confirmed
  backend data defect.
- Do not render line-series data for same-timestamp entry/exit pairs.
- Fetch Backtest run-range trades with the explicit 5000-row cap and indicate
  when that cap is reached.
