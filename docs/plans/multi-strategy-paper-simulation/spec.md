# Multi-Strategy Paper Simulation Spec

Version: v2

## 评审记录

- P1（已修复）Backend feasibility: the v1 design said batch start can call
  `PaperManager.start()` repeatedly, but existing `start()` enforces the session
  cap one session at a time. The design now requires a single preflight check of
  `active_running_sessions + requested_sessions <= max_paper_sessions` before
  any run row or session is created.
- P1（已修复）Failure consistency: the v1 `PaperGroup` shape used parallel
  `session_ids` and `strategy_ids`, which is fragile for stop finalization,
  rollback, and UI metadata. The design now uses explicit per-strategy group
  entries containing `strategy_id`, `session_id`, `run_id`, `name`, and state.
- P1（已修复）Operational cleanup: stop and mode-change behavior did not cover
  stale or partially missing batch groups. The design now requires idempotent
  batch stop, per-session fallback stop from the client metadata, WebSocket
  closure in `finally`, and visible errors if stop fails.
- P1（已修复）Frontend/backend contract: `maxPaperSessions` was referenced from
  health but current `Health` does not expose it. The design now requires
  adding the optional health field, keeping a local fallback, and raising the
  default session cap to cover the seven built-in strategies.
- P2（保留条件）UX density: with all built-in strategies visible, marker labels
  and second-row tabs can become crowded. The implementation must keep the
  filter control compact and verify desktop/mobile layout during development.
- P2（保留条件）Resource scaling: one browser WebSocket and one paper session per
  strategy is acceptable for v2, but group WebSocket and shared backend market
  fan-out remain the next optimization if OKX or desktop resource limits appear.

## Overview

This feature upgrades the live paper-trading workflow from a single selected
strategy to a multi-strategy simulation workspace. In the current product, the
right strategy panel owns one `strategyId`, the user starts one paper session,
and the left chart receives one stream of trades, equity points, and position
state through one `/api/ws/paper/{sid}` connection. The requested behavior is
that the user can select several strategies, or choose all available
strategies, start paper simulation once, and have every selected strategy run
against the same instrument, bar, capital, and live or replay data source at the
same time.
This concurrency is bounded by `settings.max_paper_sessions`, exposed to the
frontend as `maxPaperSessions`, so "select all" means all enabled strategies up
to the configured guardrail rather than unbounded session creation.

The user-facing result is a side-by-side strategy observation mode inside the
existing workstation, not a separate comparison page. The right panel remains
the strategy selection and control surface. The left chart becomes the shared
market canvas: buy and sell markers from different strategies must be visually
distinguishable by color and label, the equity view must support multiple live
equity curves, and the bottom trade / position area must gain a second tab row
under the existing `成交记录 / 当前持仓` tabs so users can switch between each
running strategy's detailed trades and current position. A filter control on the
right side of the `K线价格走势 / 净值` header row controls which strategies'
markers and equity curves are shown.

The implementation should preserve the project hard rules: no fake market data,
all timestamps remain seconds-level UTC, strategies stay isolated from each
other, and runtime paper session handles are never persisted to localStorage.
The design deliberately reuses the existing `PaperSession`, `SimBroker`,
strategy registry, REST client, and WebSocket client rather than inventing a new
simulation engine. The new behavior is primarily orchestration, state modeling,
and UI presentation.

## Technical Design

### Current Flow

The current paper flow is:

1. `StrategyPanel` renders `StrategyList` with one active `strategyId` from
   `apps/web/lib/store.ts`.
2. `StrategyDetail` calls `onStartPaper(params, opts)`.
3. `StrategyPanel.applyThenStartPaper()` updates the selected strategy params
   and calls `api.paperStart()`.
4. `services/quant/app/api/paper.py` creates one `PaperSession` through
   `PaperManager.start()`.
5. `apps/web/app/page.tsx` watches `paperSessionId`, opens one `paperWS()`,
   and stores `liveTrades` plus `liveEquity`.
6. `ChartWorkspace` passes one `overlayTrades` array to `CandleChart`; the
   chart uses `buildTradeMarkerEvents()` and `buildTradeTrailSegments()` to draw
   markers and entry-to-exit trails.
7. `ChartWorkspace` bottom tabs show `TradesTable` or `PositionPanel` for the
   same single `overlayTrades` array.

This flow assumes one session id and one run id. The new design keeps that
single-strategy path working for backward compatibility but adds a multi-session
path used when paper mode has more than one selected strategy.

### Target Architecture

Backend orchestration adds batch endpoints:

- `POST /api/paper/batch/start` validates the selected strategy ids, starts one
  independent `PaperSession` per strategy, and returns a group id plus the
  per-strategy `sessionId` / `runId` metadata.
- `POST /api/paper/batch/{group_id}/stop` stops all sessions in the group and
  returns one final run summary per strategy.
- Existing `POST /api/paper/start`, `POST /api/paper/{sid}/stop`, `GET
  /api/paper/sessions`, and `WS /api/ws/paper/{sid}` remain valid.

The backend does not need a new shared broker or a new strategy engine. Each
strategy must get its own `BaseStrategy` instance and its own `SimBroker` inside
an existing `PaperSession`, so risk state, pending orders, position, trades,
equity, seeded trades, and final metrics are isolated. Starting sessions in a
batch is an orchestration concern in `PaperManager`, not a modification to
`PaperSession.feed_candle()`.
`PaperManager.start_batch()` must not rely on per-session limit checks alone:
it first dedupes the ordered strategy ids, loads all strategy rows, resolves all
templates, parses params, instantiates strategy objects, and verifies
`running_count + requested_count <= max_paper_sessions`. Only after that
preflight succeeds may it create run rows and sessions. If a runtime failure
still happens during startup, every session created in the batch must be stopped
and removed from the batch group before the API returns.

Frontend state changes from scalar paper state to keyed paper state:

- Keep `strategyId` as the active strategy for editing parameters and
  single-strategy backtests.
- Add runtime-only `paperSelectionIds: number[]` for the multi-select control.
- Add runtime-only `paperSessionsByStrategyId` keyed by strategy id.
- Add runtime-only `paperActiveStrategyId` for the second tab row in the left
  bottom panel.
- Add runtime-only `paperVisibleStrategyIds` for chart marker and equity
  filtering.

The health response adds optional `maxPaperSessions`. The frontend should treat
missing `maxPaperSessions` as a compatibility fallback of `3`, matching the old
backend default, while new backend builds should return `12` by default so all
seven built-in strategies can be selected together.

`page.tsx` opens one `paperWS(sessionId)` per active strategy session. Each
WebSocket event is enriched on the client with `strategyId`, `strategyName`,
`runId`, and `color` from the session metadata before it is merged into local
state. This avoids changing every backend event payload immediately and keeps
the WebSocket contract compatible. The frontend stores:

- `paperTradesByStrategyId: Record<number, StrategyTrade[]>`
- `paperEquityByStrategyId: Record<number, StrategyEquityPoint[]>`
- `paperStatusByStrategyId: Record<number, PaperStatus>`
- `paperPositionsByStrategyId: Record<number, PaperPositionEvent | null>`

The chart filter derives display arrays from these maps. In price mode,
`CandleChart` receives only trades for `paperVisibleStrategyIds`. In equity
mode, `EquityChart` receives multiple `Overlay` curves built from the visible
strategy ids. The bottom panel always shows one strategy at a time; changing the
second tab row updates `paperActiveStrategyId`, and the `TradesTable` /
`PositionPanel` receive that strategy's trades only.

### Sequence of Operations

Start all selected strategies:

1. User switches account mode to simulation / paper mode.
2. `StrategyList` renders checkboxes in paper mode and supports row click for
   active edit plus checkbox click for inclusion.
3. User clicks individual strategy checkboxes or the `全选` control.
4. `StrategyPanel` disables start when `paperSelectionIds` is empty or when the
   number exceeds backend `maxPaperSessions` from health or a local fallback.
   Selection beyond the cap is prevented at the checkbox level and surfaced as
   a concise disabled reason beside the start action.
5. User clicks `开始模拟`.
6. `StrategyPanel` gathers current params for each selected strategy. For v1,
   only the active strategy's edited form is updated before start; all other
   selected strategies use persisted DB params. The UI must state this by
   placement, not by long instructional copy: the edit form is visually tied to
   the active strategy row.
7. `api.paperBatchStart()` sends `strategyIds`, `instId`, `bar`,
   `initialCapital`, `source`, `speed`, `replayBars`, `priming`, and
   `primingBars`.
8. `paper.py` loads all rows and validates all templates before calling
   `PaperManager.start_batch()`. `start_batch()` performs the aggregate limit
   preflight against currently running sessions, creates a group only after
   metadata is consistent, and rolls back already-started sessions if any later
   strategy fails.
9. Response metadata is saved into runtime store. The first returned strategy
   becomes `paperActiveStrategyId`; all returned strategies become visible.
10. `page.tsx` creates one WebSocket per session and starts merging events into
    keyed state maps.
11. `ChartHeaderBar` filter and the bottom strategy tab row render from the
    same session metadata, so labels and colors stay consistent.

Stop all running strategies:

1. User clicks `停止模拟`.
2. If a batch group exists, call `api.paperBatchStop(groupId)`. If the batch
   group is missing because the backend restarted or old UI state is present,
   fall back to stopping each known `sessionId` individually.
3. Backend stops each running session id in that group and returns final `Run`
   summaries. Stopping an already-stopped session in a known group is
   idempotent; an unknown group returns `404 NOT_FOUND`.
4. Frontend closes all WebSocket clients in a `finally` path, clears
   `paperSessionId` scalar, clears `paperSessionsByStrategyId`, and leaves final
   keyed trades/equity visible until the user changes instrument, bar, mode, or
   starts a new simulation. If stop fails, show the error and do not claim that
   the batch is stopped, but still close broken WebSocket clients to prevent
   duplicate event streams.
5. Final metrics are surfaced in the right panel as a compact per-strategy
   status list, not forced into the old single `metrics` cards.

Instrument, bar, mode, and account changes:

- Changing `instId` or `bar` while paper sessions are running must require an
  explicit stop action in UI. The implementation should disable the symbol/bar
  controls or show a concise blocking message, because switching market data
  under running sessions would make trades and markers ambiguous.
- Changing away from `paper` mode closes active WebSocket clients and must
  attempt stop for active sessions before clearing runtime state. If stop fails,
  keep enough session metadata to retry from the right panel and display the
  failure instead of silently discarding the handles.
- Refreshing the browser must not restore old session ids. The persisted store
  must omit all new runtime session fields.

## File / Module Change Plan

| File | Change intent |
| --- | --- |
| `services/quant/app/schemas.py` | Add `PaperBatchStartReq`, `PaperBatchSessionOut`, `PaperBatchStartOut`, and `PaperBatchStopOut` Pydantic models aligned to camelCase frontend fields. |
| `services/quant/app/config.py` | Raise default `max_paper_sessions` from 3 to 12 while keeping the existing env override, so the UI can support selecting all seven built-ins while still enforcing a guardrail. |
| `services/quant/app/paper/manager.py` | Add group registry and `start_batch()`, `stop_batch()`, `get_group()`, `list_sessions()` metadata with `strategyId`, `runId`, and optional `groupId`. `start_batch()` performs aggregate preflight before creating runs, records explicit per-strategy group entries, rolls back partial runtime failures, and keeps existing `start()` / `stop()` behavior intact. |
| `services/quant/app/api/paper.py` | Add `POST /paper/batch/start` and `POST /paper/batch/{group_id}/stop`; validate ids up front; roll back partial starts; return structured batch metadata. |
| `services/quant/app/api/market.py` | Include `maxPaperSessions` in `/api/health` so the frontend can cap checkbox selection before calling the batch endpoint. |
| `services/quant/app/schemas.py` and `apps/web/lib/types.ts` | Align health response and batch paper response shapes. `Health.maxPaperSessions` is optional on the TypeScript side for compatibility with older backend builds. |
| `apps/web/lib/api.ts` | Add typed `paperBatchStart()` and `paperBatchStop()` methods. Keep existing `paperStart()` and `paperStop()` for single-session compatibility. |
| `apps/web/lib/ws.ts` | No protocol change required; optionally export a helper type for closing multiple `ReconnectingWS` clients safely. |
| `apps/web/lib/store.ts` | Add runtime-only paper multi-selection, visible filter, active bottom strategy tab, and batch group state. Exclude all session ids and group ids from `partialize`; bump persist version only if persisted selectable ids are introduced. |
| `apps/web/lib/paperMulti.ts` | New pure helpers: color assignment, keyed trade/equity merging, visible strategy filtering, first-active fallback, and conversion to `Overlay[]`. |
| `apps/web/lib/paperMulti.test.ts` | Unit tests for the new pure helpers, especially duplicate ts equity upsert per strategy and active tab fallback. |
| `apps/web/components/Strategy/StrategyList.tsx` | Add optional multi-select props and render checkbox controls in paper mode; preserve single active row behavior for backtest and parameter editing. |
| `apps/web/components/Strategy/StrategyPanel.tsx` | Switch paper start/stop mutations to batch mode when more than one strategy is selected; render select-all, selected count, per-strategy running status, cap-disabled states, retryable stop errors, and per-session fallback stop when group stop returns `404`. |
| `apps/web/app/page.tsx` | Replace scalar live paper arrays with keyed maps; open/close multiple paper WebSockets; derive `overlayTrades`, paper equity overlays, and bottom-panel data from visible/active strategy ids. |
| `apps/web/components/Chart/ChartWorkspace.tsx` | Accept `paperStrategies`, `activePaperStrategyId`, `visiblePaperStrategyIds`, and callbacks; render a second strategy tab row under the existing bottom tabs when paper mode has multiple active strategies. |
| `apps/web/components/Chart/ChartHeaderBar.tsx` | Add a compact right-aligned strategy marker/equity filter next to the price/equity tabs, visible only in paper mode with multiple sessions. |
| `apps/web/components/Chart/CandleChart.tsx` | Update marker rendering to read optional strategy color/name from enriched trades while retaining existing long/short defaults for backtest. |
| `apps/web/components/Chart/TradeTooltip.tsx` | Include strategy name/color in tooltip rows when present so overlapping same-time markers are understandable. |
| `apps/web/lib/tradeTrails.ts` | Preserve existing behavior; extend marker ids to include `strategyId` when present to prevent duplicate ids across sessions with identical trade timestamps. |
| `apps/web/lib/types.ts` | Add `StrategyTrade`, `StrategyEquityPoint`, `PaperStrategySession`, `PaperBatchStartResult`, and `PaperChartFilter` interfaces. |
| `services/quant/tests/test_paper_batch.py` | Add backend tests for batch validation, rollback, aggregate active+requested limit enforcement, idempotent stop, unknown group 404, and stop finalization. |
| `apps/web/lib/store.test.ts` | Verify new runtime paper session fields are not persisted. |

## Interface Design

### REST: Batch Start

`POST /api/paper/batch/start`

Request:

```json
{
  "strategyIds": [1, 2, 3],
  "instId": "BTC-USDT-SWAP",
  "bar": "1H",
  "initialCapital": 10000,
  "source": "live",
  "speed": 1,
  "replayBars": 300,
  "priming": true,
  "primingBars": 300
}
```

Validation:

- `strategyIds`: unique after order-preserving dedupe, min 1, max
  `settings.max_paper_sessions`.
- `len(current_running_sessions) + len(strategyIds)` must be less than or
  equal to `settings.max_paper_sessions`; this check happens before any run row
  or `PaperSession` is created.
- Every strategy id must exist before any session is started.
- Every strategy template must resolve through `get_strategy_class()`.
- `startTs` is not accepted; paper sessions use live or replay source exactly
  as the current single-session endpoint does.

Response:

```json
{
  "groupId": "paper_4f12...",
  "instId": "BTC-USDT-SWAP",
  "bar": "1H",
  "sessions": [
    {
      "strategyId": 1,
      "name": "EMA Cross Scalper",
      "sessionId": "a1b2",
      "runId": 101,
      "color": "#2f80ed",
      "state": "running"
    }
  ]
}
```

Colors can be assigned on the frontend, but returning stable color slots from
the backend is acceptable if `PaperManager` keeps group order. Prefer frontend
color assignment using existing `colorForIndex()` to avoid backend UI coupling.
The response order must match the deduped request order so frontend colors,
bottom tabs, and status rows remain stable across reconnects during the same
runtime.

Errors:

- `400 UNAVAILABLE`: at least one strategy template is not registered.
- `404 NOT_FOUND`: at least one strategy id does not exist.
- `429 SESSION_LIMIT`: requested active sessions exceed the configured limit.
- `500 PAPER_BATCH_START_FAILED`: a later start failed after validation; already
  started sessions have been stopped before returning the error.

### REST: Batch Stop

`POST /api/paper/batch/{groupId}/stop`

Response:

```json
{
  "groupId": "paper_4f12...",
  "runs": [
    {
      "strategyId": 1,
      "sessionId": "a1b2",
      "run": {
        "id": 101,
        "strategyId": 1,
        "instId": "BTC-USDT-SWAP",
        "bar": "1H",
        "mode": "paper",
        "status": "stopped",
        "startTs": 1781720000,
        "endTs": 1781723600,
        "metrics": {
          "totalReturn": 0.012,
          "winRate": 0.5,
          "profitFactor": 1.2,
          "maxDrawdown": -0.01,
          "sharpe": 0.8,
          "tradeCount": 4,
          "avgPnl": 3.2
        }
      }
    }
  ]
}
```

Existing single-session stop remains available for old UI paths and tests.
Batch stop is idempotent for sessions that are already stopped but still known
to the group. An unknown `groupId` returns `404 NOT_FOUND`, allowing the
frontend to fall back to stopping the individual session ids it still has in
runtime metadata.

### WebSocket

No new backend WebSocket path is required for v1. The frontend opens one
`/api/ws/paper/{sessionId}` connection per running strategy. The callback
closure enriches each event:

```ts
const enrichedTrade: StrategyTrade = {
  ...trade,
  strategyId,
  strategyName,
  runId,
  color,
};
```

A future optimization may add `/api/ws/paper/group/{groupId}` to multiplex
events server-side, but it is not necessary for correctness and should not block
this feature.

## Data Model

Backend persistent schema does not need a migration. Existing tables already
model one run per strategy:

- `strategy_runs`: one new row per selected strategy with `mode="paper"`.
- `trades`: trades remain keyed by `run_id`.
- `equity_points`: equity remains keyed by `run_id`.

The batch group is process-local runtime metadata in `PaperManager`, matching
the existing process-local nature of `PaperSession`. Suggested in-memory shape:

```python
@dataclass
class PaperGroupSession:
    strategy_id: int
    strategy_name: str
    session_id: str
    run_id: int
    state: str

@dataclass
class PaperGroup:
    group_id: str
    inst_id: str
    bar: str
    sessions: list[PaperGroupSession]
    created_at: float
```

`PaperManager._groups: dict[str, PaperGroup]` is enough. This avoids relying on
parallel arrays when finalizing runs or building UI metadata. The group is not
restored after process restart; the browser also must not persist `groupId`.
Historical inspection continues through `/api/runs`, `/api/runs/{id}/trades`,
and `/api/runs/{id}/equity`.

Frontend runtime types:

```ts
export interface PaperStrategySession {
  strategyId: number;
  strategyName: string;
  sessionId: string;
  runId: number;
  color: string;
  state: "starting" | "running" | "stopped" | "error";
}

export interface StrategyTrade extends Trade {
  strategyId: number;
  strategyName: string;
  color: string;
}

export interface StrategyEquityPoint extends EquityPoint {
  strategyId: number;
}
```

Store fields are runtime-only unless explicitly listed in `partialize`:

```ts
paperSelectionIds: number[];
paperVisibleStrategyIds: number[];
paperActiveStrategyId: number | null;
paperBatchGroupId: string | null;
paperSessionsByStrategyId: Record<number, PaperStrategySession>;
```

Do not persist `paperBatchGroupId`, `paperSessionsByStrategyId`, any `sessionId`,
or any live trade/equity arrays.
Do not bump the persist version solely for runtime-only fields. Bump it only if
a new field is included in `partialize`.

## Testing & Acceptance Criteria

Backend tests:

1. `test_batch_start_rejects_unknown_strategy_before_starting_any_session`: mock
   two ids where one is missing, assert 404 and no new active sessions.
2. `test_batch_start_respects_session_limit`: set `max_paper_sessions=2`, submit
   three unique ids, assert 429.
3. `test_batch_start_respects_existing_running_sessions`: start one existing
   session, set `max_paper_sessions=2`, submit two more ids, assert 429 and no
   new run rows.
4. `test_batch_start_creates_one_run_per_strategy`: start two strategies and
   assert two `strategy_runs` rows with `mode="paper"`, same `inst_id`, same
   `bar`, and different `strategy_id`.
5. `test_batch_start_rolls_back_partial_failure`: monkeypatch
   `PaperManager.start()` to fail on the second strategy, assert the first
   session is stopped and no running session remains.
6. `test_batch_stop_finalizes_all_runs`: start two replay sessions, stop group,
   assert both returned runs are `stopped`.
7. `test_batch_stop_unknown_group_returns_404`: assert stale group ids fail
   explicitly so the frontend can fall back to individual stop.
8. `test_batch_stop_is_idempotent_for_known_group`: stopping a known group twice
   returns stopped metadata without leaving active sessions.

Frontend pure tests:

1. `mergePaperTrade()` appends trades under the correct strategy id and does not
   mutate previous arrays.
2. `mergePaperEquity()` upserts by `(strategyId, ts)` so transient and confirmed
   points with the same timestamp do not violate lightweight-charts ordering.
3. `selectFirstAvailablePaperStrategy()` returns the current active id if still
   available, otherwise the first session id, otherwise `null`.
4. `paperSessionsToOverlays()` returns only visible strategy ids and uses stable
   colors.
5. `store.partialize` omits `paperBatchGroupId` and all session ids.
6. Health parsing accepts missing `maxPaperSessions` and falls back to `3`, and
   uses the backend value when present.

Manual acceptance criteria:

1. In paper mode, each strategy row has a checkbox and the panel has a
   select-all control. Selecting all built-in strategies is possible when the
   configured session limit is at least the number of enabled built-ins.
2. Starting paper simulation with three selected strategies creates three
   running sessions and three run ids without changing source code or strategy
   registry behavior.
3. The price chart shows buy/sell/close markers from multiple strategies with
   distinguishable colors or labels. Hover tooltip identifies the strategy.
4. The chart header filter can show all strategies or a subset. Toggling a
   strategy immediately updates price markers and equity curves.
5. The equity chart shows multiple live equity curves for visible strategies.
6. The bottom panel shows `成交记录 / 当前持仓` as the first tab row and a second
   strategy tab row below it when multiple strategies are running.
7. Switching the second strategy tab changes both trade table and position card
   to that strategy only.
8. Stopping the batch stops all sessions, finalizes all runs, closes all
   WebSockets, and keeps final results visible until the user changes context.
9. Refreshing the browser does not reconnect stale session ids and does not show
   fake running state.
10. `pnpm test` passes, including backend pytest and frontend Vitest.

## Risks & Mitigations

The main resource risk is that v1 opens one OKX-backed `PaperSession` and one
browser WebSocket per strategy. With seven built-in strategies this is still
reasonable for a desktop simulation workstation, but the implementation must
honor `max_paper_sessions` and surface a clear disabled start state before the
request is sent. If live OKX WebSocket limits become a real problem, the next
iteration should introduce a shared market-feed fan-out inside the backend and a
group WebSocket, but that is an optimization rather than a requirement for this
feature.
The default cap should be raised to `12` for this feature, but the cap remains
env-configurable for constrained desktops or remote test environments.

The main UX risk is confusing active editing with selected-for-run state. The
row click should continue to select the active strategy for parameter editing,
while the checkbox controls inclusion in the next paper batch. Keep these
targets visually distinct and do not make row click toggle the checkbox. This
matches common list behavior and avoids accidental parameter edits on the wrong
strategy.

The main data consistency risk is mixing trades from different strategies
without identity. The frontend must enrich every live trade and equity point
with `strategyId` as soon as it leaves the WebSocket callback. Marker ids,
tooltip rows, position derivation, CSV export, and bottom tabs must use the
strategy-aware data shape. Existing single-strategy backtest arrays should keep
working without `strategyId`.

The persistence risk is stale runtime handles. The store `partialize` function
must explicitly omit all new session fields, and tests must verify that. It is
acceptable to persist the user's selected strategy ids only if they are treated
like ordinary UI selection and validated against the latest strategy list on
hydration; for v1, prefer runtime-only selection to reduce migration risk.

The implementation risk is partial batch failure. Backend batch start must
validate all strategy ids and templates before starting anything. If session
startup still fails mid-batch because of runtime conditions, already-started
sessions must be stopped before returning an error. This keeps the UI and the
manager registry from drifting apart.

There is also a cleanup risk around stale groups after backend restart or manual
session stop. Batch stop must return explicit `404 NOT_FOUND` for unknown groups
and the frontend must retry known session ids individually before clearing
runtime state.

Finally, there is a chart rendering risk from overlapping markers at identical
timestamps. Marker ids must include `strategyId`, marker text should include a
short strategy label when multiple strategies are visible, and the tooltip must
group all marker events at the timestamp. The filter control is the escape hatch
for dense signal periods.

## 评审结论

有条件通过。

P0/P1 concerns identified during review have been resolved in this v2 document.
Implementation may proceed with the following P2 conditions:

- Verify compact strategy filter and second-row strategy tabs on desktop and
  mobile widths so dense labels do not overlap.
- Keep the one-WebSocket-per-strategy design for v2, but revisit shared market
  fan-out and a group WebSocket if session count or OKX limits become visible
  during implementation testing.
