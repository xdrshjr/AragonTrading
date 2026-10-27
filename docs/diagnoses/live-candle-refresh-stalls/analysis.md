# Live Candle Refresh Stalls Diagnosis

## Problem description

The reported symptom is that the K-line chart does not advance with time and
does not keep refreshing the latest candle data for the currently selected
stock/contract. The expected behavior is:

- The chart refreshes the latest K-line data at a reasonable frequency.
- Refresh work is scoped to the currently selected `instId` and `bar`.
- Other instruments/contracts are not queried unless the user switches to them.
- API usage is bounded so the app does not create avoidable network pressure.

## Reproduction steps

### UI reproduction

1. Start the app from the repository root:

```powershell
pnpm dev
```

2. Open `http://localhost:18761`.
3. Stay in the default backtest mode. The strategy panel auto-runs a backtest
   after strategies load.
4. Observe that the chart switches to the completed run's fixed candle range.
   The live price chip disappears and the market WebSocket is not active for
   the chart.
5. Wait long enough for the selected bar to receive fresh data, or switch to a
   short bar such as `1m` and wait a few minutes.
6. The chart remains bound to the fixed backtest/run range instead of appending
   or updating the latest candle for the selected `instId` and `bar`.
7. Switch to compare mode. The chart is again bound to `backtestStartTs` and
   `backtestEndTs`, so it does not subscribe to live market candles.

### Static minimal repro

These commands show the failing path without hitting OKX:

```powershell
rg -n "setRunId|chartRange|marketWS|refetchInterval|candlesWithMeta" apps/web/app/page.tsx apps/web/components/Strategy/StrategyPanel.tsx
```

Relevant output:

```text
apps/web/components/Strategy/StrategyPanel.tsx:62:      setRunId(data.runId);
apps/web/app/page.tsx:54:  const runRange = useMemo(() => {
apps/web/app/page.tsx:59:  const chartRange = useMemo(() => {
apps/web/app/page.tsx:93:        ? api.candlesWithMeta(chartRange.instId, chartRange.bar, 1000, chartRange.endTs, chartRange.startTs)
apps/web/app/page.tsx:115:    () => (chartRange ? baseCandles ?? [] : mergeCandles(older, baseCandles ?? [], live, forming)),
apps/web/app/page.tsx:167:    if (mode === "paper" || okxUp === false || chartRange) return;
```

The code path is enough to reproduce the control-flow bug: a successful
backtest writes `runId`; `runId` produces `runRange`; `runRange` produces
`chartRange`; `chartRange` disables live merge and exits before `marketWS()`
is created.

## Root cause analysis

### Primary root cause: `chartRange` freezes the chart and disables live market updates

After a backtest succeeds, `StrategyPanel` stores the returned run id:
`apps/web/components/Strategy/StrategyPanel.tsx:57` to
`apps/web/components/Strategy/StrategyPanel.tsx:64`.

`page.tsx` then loads that run and derives a fixed run range:
`apps/web/app/page.tsx:45` to `apps/web/app/page.tsx:54`.

That fixed range becomes `chartRange`:
`apps/web/app/page.tsx:55` to `apps/web/app/page.tsx:59`.

Once `chartRange` exists, the candle series is rendered only from the fixed
historical result:

```text
apps/web/app/page.tsx:106: const candles = useMemo(
apps/web/app/page.tsx:107:   () => (chartRange ? baseCandles ?? [] : mergeCandles(...)),
```

The live `forming` candle and confirmed `live` candles are merged only when
`chartRange` is absent. The market WebSocket effect also exits when
`chartRange` exists:

```text
apps/web/app/page.tsx:156: useEffect(() => {
apps/web/app/page.tsx:157:   if (mode === "paper" || okxUp === false || chartRange) return;
```

This explains why the chart does not keep moving after the previous
backtest-range fix: the earlier alignment work correctly pinned historical
backtest display to the run range, but it also made the main chart stop
receiving live market updates whenever a backtest/compare range is active.

### Secondary root cause: there is no periodic REST refresh fallback

The main candle query fetches historical candles once for the current query
key:
`apps/web/app/page.tsx:77` to `apps/web/app/page.tsx:91`.

It has `retry: 0` but no `refetchInterval`, and the non-range path fetches a
full latest window:

```text
apps/web/app/page.tsx:88: : api.candlesWithMeta(instId, bar, 300),
```

The only periodic market request in `page.tsx` is health polling:
`apps/web/app/page.tsx:40`.

The backend already has a scoped candle endpoint that accepts the selected
instrument and bar:
`services/quant/app/api/market.py:41` to
`services/quant/app/api/market.py:60`.
The frontend API wrapper also scopes requests by `instId` and `bar`:
`apps/web/lib/api.ts:63` to `apps/web/lib/api.ts:68`.

So the application has the backend capability to refresh only the current
selection, but the main chart does not schedule a bounded latest-candle poll.
If the WebSocket does not run or is intentionally disabled by `chartRange`,
the chart has no mechanism to advance with time.

### Supporting evidence: WebSocket is correctly scoped but conditionally skipped

When it is used, `marketWS()` constructs a single subscription path for the
selected `instId` and `bar`:
`apps/web/lib/ws.ts:72` to `apps/web/lib/ws.ts:80`.

The backend `/ws/market` route reads only those query parameters and creates
one OKX subscription:
`services/quant/app/api/ws_market.py:15` to
`services/quant/app/api/ws_market.py:35`.

The scope requirement is therefore not blocked by the backend design. The
problem is that the frontend disables the scoped subscription in important
chart states and lacks a scoped polling fallback.

### Review audit notes: execution path and counter-examples

The cited path is executed in the failing UI state. `StrategyPanel` auto-runs a
backtest in backtest mode when `strategyId`, `instId`, `bar`, or the selected
range changes, and then writes the successful `runId`:
`apps/web/components/Strategy/StrategyPanel.tsx:68` to
`apps/web/components/Strategy/StrategyPanel.tsx:76`. That makes the
`runQ -> runRange -> chartRange` path in `page.tsx` active after both the
initial auto-run and later manual/automatic runs.

One possible counter-example is user switching. It does not invalidate the
root cause. `setInst` and `setBar` clear `runId`, and `setBacktestRange` also
clears `runId`: `apps/web/lib/store.ts:53` to
`apps/web/lib/store.ts:60`. This prevents a stale previous run from remaining
bound after the selection changes. However, the same `StrategyPanel` effect
then auto-runs for the new `instId`/`bar` and writes a new `runId`, so the
fixed `chartRange` and skipped live subscription return for the currently
selected instrument.

Compare mode is not an alternative explanation either. It does not require a
loaded run to freeze the chart: `chartRange` is derived directly from
`instId`, `bar`, `backtestStartTs`, and `backtestEndTs` when
`mode === "compare"`: `apps/web/app/page.tsx:59` to
`apps/web/app/page.tsx:63`. The same `chartRange` then disables live candle
merge, load-more, WebSocket connection, and toolbar live price display at
`apps/web/app/page.tsx:114` to `apps/web/app/page.tsx:120`,
`apps/web/app/page.tsx:166` to `apps/web/app/page.tsx:190`, and
`apps/web/app/page.tsx:238` to `apps/web/app/page.tsx:244`.

The current code already has the right scoping shape for the user's
"selected stock/contract only" requirement. The candle query key and query
function use either `chartRange.instId/chartRange.bar` or the active
`instId/bar`: `apps/web/app/page.tsx:83` to `apps/web/app/page.tsx:96`.
`marketWS(instId, bar, ...)` also creates one scoped URL, and React effect
cleanup closes the previous connection before the next dependency instance
settles: `apps/web/app/page.tsx:166` to `apps/web/app/page.tsx:190`. The
missing piece is therefore not broad subscription support; it is an explicit
live-following chart state plus a bounded latest-candle reconciliation path.

## Impact surface

- Backtest mode after an auto-run or manual run: affected, because `runId`
  creates `chartRange`.
- Compare mode: affected, because `chartRange` is derived from the selected
  compare/backtest range.
- Default non-range chart before a run: partially protected by `marketWS()`,
  but still has no REST refresh fallback if the WebSocket fails or is not
  reachable in the user's environment.
- Paper mode: mostly separate. It uses `paperWS()` and should not be changed
  as part of the market-chart refresh fix except for avoiding duplicate
  subscriptions.
- Network usage: a naive fix that refetches all instruments or full history on
  a tight interval would violate the requirement. Refresh must stay scoped to
  the active `instId`/`bar` and should fetch only a small latest window.

## Candidate fixes

| Option | Summary | Invasiveness | Risk | Effort |
|---|---|---:|---:|---:|
| A | Add a scoped latest-candle refresh loop for the current `instId`/`bar`, independent of backtest overlay state, and merge only the newest 1-3 candles into the active non-historical market series. Keep historical run candles frozen for overlay correctness. | Medium | Medium | Medium |
| B | Remove `chartRange` from the WebSocket guard and always merge live candles into the displayed chart. | Low | High | Low |
| C | Split chart data into two explicit modes: historical analysis range and live market range. Add a user-visible "follow live" state that clears run range binding, uses WebSocket plus bounded REST fallback, and never mixes live candles into a historical backtest overlay unless the user opts in. | Medium-High | Low | Medium |

## Recommended fix

Choose Option C, with Option A's bounded refresh mechanism as the implementation
detail.

Justification:

- It preserves the previous fix's important invariant: backtest equity/trades
  stay aligned with the exact historical candle range used by the run.
- It satisfies the new requirement by giving the chart a true live-following
  path for the currently selected stock/contract.
- It avoids unnecessary API load by polling only the selected `instId`/`bar`
  and only a tiny latest window, while continuing to prefer WebSocket updates
  when available.
- It avoids silently mixing live candles into a historical backtest chart,
  which would reintroduce the visual misalignment fixed in the previous round.

Recommended implementation outline:

1. Model chart intent explicitly, for example `chartView = "run-range" |
   "compare-range" | "live-follow"`.
2. Keep `run-range` and `compare-range` frozen for historical correctness.
3. When the user changes `instId` or `bar`, or chooses live-follow mode, clear
   `runId`/range binding as appropriate and subscribe only to
   `marketWS(instId, bar, ...)`.
4. Add a conservative React Query refresh for latest candles, such as
   `api.candlesWithMeta(instId, bar, 2 or 3)`, with a bar-aware interval
   (`1m`: 15-30s, `5m+`: 30-60s, larger bars: 60-120s). Use this as a
   fallback and reconciliation path, not as a full-history refetch.
5. Merge latest REST/WS candles by `ts`, replacing the forming candle when the
   same timestamp arrives and appending only confirmed/new timestamps.
6. Ensure cleanup on `instId`/`bar` changes closes the old WebSocket before
   opening the new one. The current effect dependency on `instId` and `bar`
   already gives the right shape; the guard and range state need to be refined.
7. Add frontend regression tests for:
   - backtest `runId` creates a frozen historical chart with no live merge;
   - live-follow mode subscribes only to the selected `instId`/`bar`;
   - switching instruments closes/replaces the previous subscription;
   - bounded latest-candle polling does not request all instruments.

## 评审结论

The root-cause evidence is sufficient after the added citations: the chart is
not failing because the backend cannot serve scoped data, nor because the app
keeps querying the wrong instrument after a switch. The failing path is the
frontend state model: once a backtest or compare range creates `chartRange`,
the chart renders only fixed historical candles, hides live price state, and
skips the scoped market WebSocket. Because the candle query has no
`refetchInterval` and only health polling is periodic, there is no fallback
that can advance the latest candle.

The final recommended fix is Option C with Option A's bounded polling as a
strict implementation detail: introduce an explicit chart intent such as
`run-range`, `compare-range`, and `live-follow`; keep historical run/compare
views frozen; and make only `live-follow` subscribe to
`marketWS(instId, bar, ...)` plus a conservative latest-candle REST refresh
for the active `instId`/`bar`.

This is the minimum safe fix because simply removing the `chartRange` guard
does not fully solve the data path and would risk appending live candles onto
a historical backtest/compare chart, breaking the prior alignment fix from
commit `50a0d42481bef76d692c831b508f240d234bc063`. The next implementation
node should avoid broad refetches, avoid polling inactive instruments, and
avoid clearing or mutating historical overlay data except when the user
explicitly chooses the live-following chart state.
