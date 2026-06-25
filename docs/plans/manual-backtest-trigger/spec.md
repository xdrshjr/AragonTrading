# Manual Backtest Trigger Design

> Version: v2

## 评审记录

- P0: None.
- P1 (resolved): The v1 design scoped passive selection cleanup mostly to
  `StrategyList.onSelect` and the selected-id legality effect. That leaves
  other in-panel passive selection paths, especially `saveAs.onSuccess` and
  saved-combo create/delete callbacks from `useComboPanel`, able to change the
  selected strategy while stale metrics, trades, combo output, or Walk-forward
  output remain visible. The technical design, file plan, and tests now require
  the same passive selection helper for every non-run selection change owned by
  `StrategyPanel`, while keeping Run History and paper reconciliation as
  explicit inspect/session flows.
- P1 (resolved): The v1 test plan focused on suppressing `api.backtest`, but
  the behavior contract also says selection and parameter edits must not persist
  strategy params. The testing section now requires `api.updateStrategy`
  assertions and complete query mocks for the result queries that become active
  after an explicit run.
- P2: The implementation should keep the helper small and local to
  `StrategyPanel.tsx`; if the file approaches the project 800-line threshold,
  prefer extracting only test helpers, not production abstractions for this
  narrow change.

## Overview

The requested behavior change is to make strategy selection a passive UI
operation. In the current Strategy panel, selecting a strategy, changing the
instrument, changing the bar, or changing the selected backtest date range can
cause a simple backtest to start automatically. That behavior is expensive and
surprising: the user may only be browsing strategies or inspecting parameters,
but the UI still posts to the backtest endpoint and creates a persisted run.

This design changes the interaction contract so that the backtest engine runs
only after an explicit user command. For atomic strategies, the explicit command
is the primary "Run Backtest" button rendered by `StrategyDetail`. For saved
combo strategies, it is the equivalent button rendered by `ComboStrategyDetail`.
For Walk-forward, the current design is already explicit through
`WalkForwardForm`, and it should stay that way. Selecting a row in the strategy
list must update only the selected strategy and local form state. It must not
call `api.backtest`, must not update strategy parameters, and must not create a
new `runId`.

The user-facing result should also avoid stale data. If the user changes the
selected strategy after a previous backtest, the panel should not keep showing
the previous run as if it belongs to the newly selected strategy. The selected
strategy and the displayed run result should be treated as separate states:
selection describes what the user is editing, and result state describes the
last explicit run or history item the user chose to inspect.

## Technical design

The change is entirely in the web client. The backend API already exposes an
explicit command endpoint, `POST /api/py/strategies/{id}/backtest`, and there is
no backend behavior that auto-runs a backtest when a strategy is read or
selected. The unwanted trigger is a React effect in
`apps/web/components/Strategy/StrategyPanel.tsx`. That effect watches
`strategyId`, `instId`, `bar`, `mode`, `effectiveKind`, `strategies`,
`backtestStartTs`, and `backtestEndTs`, builds a `lastAutoKey`, and then calls
`backtest.mutate({ id: strategyId })`. Removing this effect removes the
automatic POST and persisted run creation.

The implementation should preserve the existing explicit mutation path. The
`backtest` mutation stays in `StrategyPanel.tsx`. Its mutation function should
continue to call `runBacktest(vars.id, vars.params, instId, bar,
backtestStartTs, backtestEndTs)`. Its `onSuccess` should continue setting
`runId`, optional `chartView: "run-range"`, `metrics`, `coverage`,
`comboResult`, and `returnsById`. The only call sites that should invoke
`backtest.mutate` are explicit command handlers:

1. Atomic simple backtest: `StrategyDetail` receives
   `onApplyAndBacktest`, and its primary button calls that callback with the
   current local `params`.
2. Saved combo backtest: `ComboStrategyDetail` receives `onRunBacktest`, and
   its primary button calls that callback with the combo row.
3. Existing modal preview behavior in `ComboDetailModal` is already lazy and
   should not be changed.

The automatic effect should be deleted together with the `lastAutoKey` ref,
because there is no longer an automatic key to dedupe. The import list can drop
`useRef` only if no other refs remain. `reconciledRef` still uses `useRef`, so
the React import should remain `useCallback, useEffect, useRef, useState` unless
the implementation also replaces `reconciledRef`, which this design does not
recommend.

Add a small local helper in `StrategyPanel.tsx` to make selection passive and to
clear stale run output in backtest mode. The helper should be owned by
`StrategyPanel` because the stale state is local to that component plus a small
piece of persisted app state:

- `setStrategyId(id)` updates the selected row.
- If the current `mode` is `"backtest"` and the new id differs from the current
  `strategyId`, clear the displayed run state by setting `runId` to `null`,
  `metrics` to `undefined`, `coverage` to `undefined`, `comboResult` to `null`,
  `walkForwardResult` to `null`, and `chartView` to `"live-follow"`.
- If the current `mode` is `"paper"`, do not clear `runId`; paper mode uses
  `runId` as the active simulated session handle and should not be disrupted by
  browsing rows.

Use this helper for every passive selection change that is owned by
`StrategyPanel`, not only direct list clicks:

- `StrategyList.onSelect` should call the helper instead of raw
  `setStrategyId`.
- The selected-id legality effect should call the helper, or a
  backtest-mode-only variant of its clearing logic, when it falls back to the
  first available strategy.
- `saveAs.onSuccess` should call the helper when it selects the newly created
  strategy copy.
- `useComboPanel` should receive the helper in its `setStrategyId` slot so that
  saved-combo creation, deletion of the currently selected combo, and fallback
  from `null` selection use the same stale-result cleanup without changing the
  hook implementation.

This ensures that switching from a saved combo to paper mode, saving an atomic
strategy as a new row, creating a saved combo, or deleting the currently selected
combo cannot leave old results displayed against a different selected strategy.
Keep the existing legality effect's responsibility limited to choosing a valid
id; it must not run a backtest.

Do not replace all global `setStrategyId` calls blindly. `RunHistory.open`
should remain a direct explicit-inspect flow: it intentionally sets `instId`,
`bar`, `strategyId`, `runId`, `mode: "backtest"`, and `chartView:
"run-range"` to show a historical run. Paper reconciliation and paper start
success should also keep their current runtime/session updates because those are
explicit session-management flows, not passive browsing.

There is one related stale-state path independent of strategy selection:
`setInst`, `setBar`, and `setBacktestRange` already clear `runId` in the global
store, but after removing the auto-run effect the local `metrics`, `coverage`,
and `comboResult` can remain populated. Add a passive cleanup effect in
`StrategyPanel.tsx` that watches `runId` and, when `mode === "backtest"` and
`runId === null`, clears those local result fields and the stored
`walkForwardResult`. This effect must not call `backtest.mutate` and must not
invalidate queries. It only keeps the visible result panel honest after
selection, instrument, bar, or range changes.

The intended sequence after this change is:

1. Strategy panel mounts.
2. `useQuery(["strategies"])` loads the list.
3. If the persisted `strategyId` is missing or invalid, the legality effect
   selects the first valid strategy for the mode.
4. No POST request is sent.
5. User selects another strategy.
6. The selected id changes, local parameter state in `StrategyDetail` resets
   from `strategy.params`, displayed run results clear, and no POST request is
   sent.
7. User edits parameters.
8. Only local component state changes; no strategy update and no backtest is
   sent.
9. User clicks "Run Backtest".
10. The existing mutation updates params when provided, posts to
    `/strategies/{id}/backtest`, receives the persisted run, and renders the new
    metrics/equity/trades state.

Run history stays intentionally different. Clicking a row in `RunHistory` is an
explicit inspect action, not a backtest action. It should continue setting
`instId`, `bar`, `strategyId`, `runId`, `mode: "backtest"`, and
`chartView: "run-range"` so the user can review a historical run without
creating a new one.

## File / module change plan

| File | Change type | Intent |
| --- | --- | --- |
| `apps/web/components/Strategy/StrategyPanel.tsx` | Modify | Remove the auto-backtest `useEffect` and `lastAutoKey`; add passive selection/result clearing; route `StrategyList`, selected-id fallback, `saveAs.onSuccess`, and `useComboPanel` selection callbacks through the helper; keep explicit button-triggered `backtest.mutate` and `walkForward.mutate` paths. |
| `apps/web/components/Strategy/StrategyPanel.test.tsx` | Create | Add regression coverage proving mount, strategy selection, parameter edits, save-as / saved-combo selection changes, instrument/range state changes, and combo selection do not call `api.backtest` or `api.updateStrategy`; clicking the explicit buttons does call the intended APIs. |
| `apps/web/components/Strategy/StrategyDetail.tsx` | No change expected | The atomic "Run Backtest" button already invokes `onApplyAndBacktest`; keep this as the command boundary. |
| `apps/web/components/Strategy/ComboStrategyDetail.tsx` | No change expected | The saved-combo "Run Backtest" button already invokes `onRunBacktest`; keep this as the combo command boundary. |
| `apps/web/lib/api.ts` | No change expected | `api.backtest` remains the explicit REST client for `POST /strategies/{id}/backtest`; no API contract change is required. |
| `services/quant/app/api/strategies.py` | No change expected | Backend already requires an explicit POST to run a backtest; no route behavior should change. |

## Interface design

There is no REST, WebSocket, CLI, or database API change. The public network
interface remains:

`POST /api/py/strategies/{id}/backtest`

Request body continues to include `instId`, `bar`, `startTs`, `endTs`, and
optional risk/capital fields. Response continues to be `BacktestResult` with
`runId`, `metrics`, optional `combo`, and coverage metadata.

The changed interface is the internal UI event contract:

- `StrategyList.onSelect(id)` is a selection event only. It must never call
  `api.backtest`, `api.updateStrategy`, `api.walkForward`, `api.compare`, or any
  paper-start endpoint.
- `StrategyDetail.onApplyAndBacktest(params)` is the atomic simple-backtest
  command. It may update params and post the backtest.
- `ComboStrategyDetail.onRunBacktest(combo)` is the saved-combo simple-backtest
  command. It may post the backtest for the combo id.
- `WalkForwardForm.onRun()` remains the advanced-backtest command and is not
  triggered by parameter edits or strategy selection.

## Data model

No persistent schema changes are required. The relevant in-memory model is:

```text
selection state:
  strategyId: number | null
  mode: "backtest" | "compare" | "paper"
  backtestKind: "simple" | "walkforward"

displayed result state:
  runId: number | null
  chartView: "run-range" | "compare-range" | "live-follow"
  metrics: Metrics | undefined
  coverage: CoverageMeta | undefined
  comboResult: ComboPreviewResult["combo"] | null
  walkForwardResult: WalkForwardResult | null
```

The implementation should maintain this invariant in backtest mode: a selected
strategy without an explicit run has `runId === null` and no visible run metrics
for a different strategy. `returnsById` is not part of displayed result state;
it is a historical badge cache used by the strategy list and may keep values
from previous explicit runs.

## Testing & acceptance criteria

Add `apps/web/components/Strategy/StrategyPanel.test.tsx` using Vitest,
Testing Library, `QueryClientProvider`, and a mocked `@/lib/api`. The test
should provide at least two atomic strategies and one saved combo. Mock
`api.strategies`, `api.health`, `api.trades`, `api.equity`, `api.runsList`,
`api.paperSessions`, and any other routes queried by enabled tabs with resolved
values. Mock `api.backtest` with a resolved `BacktestResult`, and mock
`api.updateStrategy` separately so tests can prove it is called only by explicit
apply/run commands.

Required test cases:

1. Rendering `StrategyPanel` in default backtest/simple mode loads strategies
   but does not call `api.backtest` or `api.updateStrategy`.
2. Clicking another atomic strategy row changes the active selection but does
   not call `api.backtest` or `api.updateStrategy`.
3. Editing atomic strategy parameters changes only local form state and does not
   call `api.updateStrategy` or `api.backtest` until the user clicks the atomic
   "Run Backtest" button.
4. Clicking the atomic "Run Backtest" button calls `api.updateStrategy` once
   with the selected strategy id and local params, then calls `api.backtest`
   exactly once with the selected strategy id and the current `instId`, `bar`,
   `startTs`, and `endTs`.
5. Selecting a saved combo row does not call `api.backtest` or
   `api.updateStrategy`; clicking the combo "Run Backtest" button calls
   `api.backtest` exactly once for the combo id and does not update strategy
   params.
6. Creating a saved combo through `useComboPanel`'s `onSaved` path, saving an
   atomic strategy as a new row, or deleting the currently selected combo may
   change `strategyId`, but must not call `api.backtest` and must clear stale
   displayed result state in backtest mode.
7. With `runId` and metrics already populated, selecting a different strategy
   in backtest mode clears the displayed result state enough that export
   buttons are disabled and `MetricsCards` receives no stale metrics.
8. Changing `backtestStartTs`/`backtestEndTs`, `instId`, or `bar` through the
   store does not call `api.backtest`. It may clear displayed results.

The implementation test setup should reset `useAppStore` and create a fresh
`QueryClient` per test so persisted state from one case cannot make another
case pass or fail accidentally.

Run the focused test command:

```bash
cd apps/web
pnpm test -- StrategyPanel
```

Run the broader web regression suite before handoff:

```bash
cd apps/web
pnpm test
```

Manual acceptance:

- Open the app in backtest mode.
- Select several strategy rows quickly. The Network panel must show no
  `POST /api/py/strategies/*/backtest` requests.
- Edit numeric parameters. No backtest request should be sent.
- Change the backtest date range. No backtest request should be sent.
- Click "Run Backtest". Exactly one backtest POST should be sent, and the
  result should render as before.
- Repeat with a saved combo. Selection is passive; combo run button sends one
  backtest POST.
- Open a row from History. Existing run review still works and does not create
  a new run.

## Risks & mitigations

The first risk is stale output after passive selection. Removing the auto-run
effect alone would stop network traffic but could leave old metrics, trades, or
combo timelines visible. Mitigate this by clearing displayed result state when a
strategy selection changes in backtest mode and when `runId` becomes `null`.

The second risk is breaking paper mode. Paper mode uses `runId` and paper
session state for the active simulated session, so the selection cleanup must be
guarded to backtest mode only. Do not clear paper runtime state from the new
selection helper.

The third risk is accidentally disabling explicit run flows. The mutation and
helper functions should remain in `StrategyPanel.tsx`, and the tests must prove
that clicking the atomic and combo run buttons still invokes `api.backtest`.

The fourth risk is over-scoping the change into backend or API layers. The
backend already has the right command boundary, so backend edits would increase
regression risk without addressing the bug. Keep this implementation limited to
the Strategy panel and its regression tests.

The fifth risk is changing compare behavior. Compare mode has its own explicit
run button and endpoint. Do not modify `CompareView` unless a failing test shows
that it relies on the removed auto-backtest side effect, which should not be the
case.

## 评审结论

**通过**. The design is feasible with the current Next.js / React Query / Zustand
stack, stays scoped to the Strategy panel and tests, and its P1 gaps have been
resolved in this v2 document. No P0 or unresolved P1 concerns remain.
