# Backtest Trade Risk Snapshots Implementation Plan (spec v2)

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.
> Document version: **v2**. Subtask #1 design review is complete; all P0/P1 review concerns are fixed in this document body.

**Goal:** Make every backtest trade record show the stop-loss and take-profit settings that were active for that individual order, including the calculated trigger prices when they are known.

**Architecture:** Persist a risk snapshot on each simulated trade at the moment the broker creates the trade record, then expose that immutable snapshot through the existing runs/trades API, frontend `Trade` type, trade table, chart tooltip, and CSV export. The source of truth is the backtest execution state, not current strategy parameters, so old runs keep displaying the exact settings that existed when the run was produced.

**Tech Stack:** Python 3.11, FastAPI, SQLAlchemy async, SQLite, pandas backtester, Next.js 14 App Router, React 18, TypeScript, TanStack Query, Tailwind, Vitest, pytest.

---

## 评审记录 / Review Notes (v2)

Review scope: section-by-section validation against feasibility, completeness, consistency, and right-sizing. The review was checked against `CLAUDE.md` and the current code paths in `services/quant/app/engine/simulator.py`, `engine/backtester.py`, `engine/combo.py`, `paper/session.py`, `db/models.py`, `db/session.py`, `db/repositories.py`, `schemas.py`, `api/runs.py`, `apps/web/lib/types.ts`, `TradesTable.tsx`, `TradeTooltip.tsx`, and `apps/web/lib/export.ts`.

### P0

- None. The design is feasible with the current FastAPI + SQLAlchemy async + SQLite + Next.js stack and does not require a new service, migration framework, or frontend state architecture.

### P1

| ID | Dimension | Concern | Resolution in v2 |
|---|---|---|---|
| P1-1 | Completeness / consistency | Adding fields to `TradeRecord` without defaults would break existing tests and helper call sites that construct `TradeRecord` with the old eight fields, such as agent run-tool tests and any future manual `append_trade` calls. This conflicts with the document's backward-compatibility goal. | The simulator and data-model sections now require all new `TradeRecord` fields to be appended after existing fields with `= None` defaults. Tests now include old-shape construction coverage. |
| P1-2 | Completeness | The original broker plan only called `broker.set_risk(risk)` in `run_backtest`, but `PaperSession` also reuses `SimBroker` and `append_trade`. Once `_open` snapshots from broker state and `apply_risk` uses stored prices, paper/replay trades could persist or stream null risk snapshots even when the strategy has risk settings. | The technical design and file plan now require `PaperSession._process_confirmed` to call `broker.set_risk(risk)` before `fill_pending`, and `_trade_record_out` to emit the same optional risk fields for paper WS snapshots/events. |
| P1-3 | Correctness / completeness | `set_risk` said "normalized snapshot" but did not define normalization. Current `BaseStrategy.risk_params()` passes raw param values through, so implementers could accidentally treat `0`, negative, NaN, or stringy values inconsistently between display and risk execution. `max_hold_bars` also needs to be snapshot-consistent, not recomputed from a later `risk` object. | The broker section now defines finite positive normalization for percentages and positive integer normalization for `max_hold_bars`, and requires `apply_risk` to use stored trigger prices and stored `max_hold_bars` from the open position. |

### P2

- `TradeTooltip` currently uses a fixed bottom clamp sized for the shorter tooltip. Adding risk rows may require increasing the clamp or using a bounded `max-height` so expanded tooltips do not clip near the bottom edge.
- The trade table is already dense in the right panel. The body now keeps the main table limited to two compact `SL` / `TP` columns and prefers `title` details for percentages and hold bars when horizontal space is tight.
- Stop/take-profit snapshot prices are trigger/reference prices. The actual close `TradeRecord.price` can still include existing slippage handling; the document now calls out this distinction.

## Overview

The strategy panel already shows a trade record table after a backtest, and the chart layer can render buy, sell, and close events. The missing piece is risk context. A user can currently see that a position opened at one price and closed at another, but the table does not show the stop-loss and take-profit configuration that governed that position. This makes it difficult to audit why a close happened, whether the configured stop/take-profit levels were sensible, and how those levels related to the actual entry price. The problem is especially visible when the close reason is `stop_loss` or `take_profit`, because the user sees the outcome but not the per-order thresholds that produced it.

This feature records and displays an immutable per-trade risk snapshot. For each opening trade, the snapshot contains the configured stop-loss percentage, take-profit percentage, maximum holding bars, and the calculated stop-loss and take-profit trigger prices for that position direction. For each closing trade, the same snapshot is copied from the currently open position, so the close row can be inspected without searching for its paired entry row. If a strategy does not define a stop or take-profit, the value stays `null`; the UI must render this as an empty dash rather than fabricating a level. Existing historical runs created before this change will also return `null` risk fields, and the UI must remain backward-compatible.

The correct design is to capture the data in `SimBroker`, not to infer it later in the browser. Current strategy parameters are mutable, built-in defaults can change, and combo backtests copy member trades from several independent member runs. Recomputing from the current strategy row would make historical records unreliable. Instead, the downstream implementation should extend `RiskParams`, `_Position`, and `TradeRecord` so the simulator writes a snapshot at execution time, persist the fields in the `trades` table, and serialize them through the existing `/api/runs/{run_id}/trades` endpoint.

Because `SimBroker`, `TradeRecord`, `append_trade`, and the frontend `Trade` type are also used by paper trading sessions, the implementation must keep paper/replay behavior compatible. The user-facing requirement is backtest trade history, but the shared simulator contract should not leave paper trades with missing risk data or break paper WebSocket snapshots.

## Technical Design

The current backtest flow is:

1. `apps/web/components/Strategy/StrategyPanel.tsx` calls `api.backtest`.
2. `services/quant/app/api/strategies.py` delegates to `services/quant/app/agent/services.py`.
3. `services.py` loads real OKX candles and calls `engine/backtester.py::run_backtest`.
4. `run_backtest` asks the strategy for `risk_params()` once, then drives `SimBroker`.
5. `SimBroker` appends `TradeRecord` objects to `broker.trades`.
6. `db/repositories.py::persist_backtest_result` saves those records to `Trade`.
7. `api/runs.py` returns rows through `schemas.py::trade_out`.
8. The frontend `api.trades` query feeds `TradesTable`, chart markers/tooltips, and CSV export.

The implementation should preserve this flow and add fields at each boundary.

In `services/quant/app/engine/simulator.py`, add risk snapshot fields to `TradeRecord`:

- `stop_loss_pct: float | None = None`
- `take_profit_pct: float | None = None`
- `max_hold_bars: int | None = None`
- `stop_loss_price: float | None = None`
- `take_profit_price: float | None = None`

Keep the existing field names in Python snake case. Do not rename `pnl`, `position_after`, or `reason`. Append the new dataclass fields after the existing fields with `None` defaults so old construction sites remain valid. Extend `_Position` with the same snapshot fields so the broker can copy them from the entry into the close. The broker should compute trigger prices only when opening a position, after the slipped fill price is known. For a long entry, `stop_loss_price = entry_price * (1 - stop_loss_pct)` and `take_profit_price = entry_price * (1 + take_profit_pct)`. For a short entry, `stop_loss_price = entry_price * (1 + stop_loss_pct)` and `take_profit_price = entry_price * (1 - take_profit_pct)`. The calculation must use the actual filled entry price, not the raw candle open price, because slippage changes the fill used by `apply_risk`.

`SimBroker._open` currently receives no risk argument, while `apply_risk` receives `RiskParams` on every bar. Add a broker field such as `self._active_risk = RiskParams()` or pass the current `risk` into `_open` from `fill_pending`. The least invasive path is:

1. Add `self._risk = RiskParams()` to `SimBroker.__init__`.
2. Add `set_risk(self, risk: RiskParams) -> None` that stores a normalized snapshot.
3. In `run_backtest`, call `broker.set_risk(risk)` immediately after `risk = strategy.risk_params()` and before the loop.
4. In `PaperSession._process_confirmed`, call `broker.set_risk(risk)` after `risk = self.strategy.risk_params()` and before `broker.fill_pending(...)`, because pending orders are filled at the start of the current confirmed candle.
5. In `_open`, read `self._risk` and compute the snapshot for the new position.
6. In `_close_at`, copy snapshot values from `self.pos`.

For backtests, this is enough because the current `BaseStrategy.risk_params()` is static per strategy instance. Do not call `strategy.risk_params()` inside the backtest loop because that would imply dynamic semantics that existing strategies do not implement. Paper sessions already evaluate strategy state per confirmed candle, so setting broker risk there preserves the existing live/replay execution cadence and keeps the shared `TradeRecord` contract consistent. If a future strategy supports dynamic per-signal risk, the broker API can be extended to carry signal-level overrides, but that is outside this task.

`set_risk` must normalize inputs once at the simulator boundary. Treat finite positive `stop_loss_pct` and `take_profit_pct` values as configured values; treat `None`, `0`, negative values, NaN, and non-finite values as absent. Treat `max_hold_bars` as configured only when it can be converted to a positive integer. This keeps display semantics aligned with current `apply_risk` truthiness behavior while making the rule explicit.

For `apply_risk`, stop using ad hoc recalculation from the current `risk` argument and `entry`. The method should use `self.pos.stop_loss_price` and `self.pos.take_profit_price` for trigger checks, and `self.pos.max_hold_bars` for max-hold checks. This guarantees the displayed stop/take-profit trigger prices and hold setting are the same snapshot the broker uses for that position. Keep the existing conservative precedence: stop-loss checks before take-profit checks when both are hit in the same candle. The stored stop/take-profit prices are trigger/reference prices; the resulting close row's `price` can still differ because `_close_at` applies the existing slippage model to the close fill.

For combo backtests in `services/quant/app/engine/combo.py`, no extra combo-specific calculation is needed. `_combo_trades` already copies member `TradeRecord` instances with `dataclasses.replace` to prefix `reason`. Once member backtests emit richer `TradeRecord` objects, combo rows inherit the risk snapshot automatically. The implementation should add a test for this because it is an important integration path.

For persistence, extend `services/quant/app/db/models.py::Trade` with nullable columns:

- `stop_loss_pct`
- `take_profit_pct`
- `max_hold_bars`
- `stop_loss_price`
- `take_profit_price`

SQLite `Base.metadata.create_all` does not alter existing tables, so `services/quant/app/db/session.py::init_db` needs a small idempotent schema upgrade after `create_all`. Add a helper such as `_ensure_trade_risk_columns(conn)` that runs `PRAGMA table_info(trades)`, compares existing column names, and issues `ALTER TABLE trades ADD COLUMN ...` for missing columns. Use native SQLAlchemy `text()` calls inside the existing `engine.begin()` block. This project does not currently use Alembic, so an idempotent SQLite upgrade helper is the lowest-risk approach and keeps packaged desktop installs compatible with existing local databases.

Update `db/repositories.py::persist_backtest_result` and `append_trade` to populate the new columns. Update `schemas.py::trade_out` to emit camelCase fields:

- `stopLossPct`
- `takeProfitPct`
- `maxHoldBars`
- `stopLossPrice`
- `takeProfitPrice`

The API must return these keys for all trade rows, using `null` when the DB row is from an old run or the strategy did not configure that field.

Paper WebSocket trade events and paper snapshots should include the same optional camelCase keys from `_trade_record_out`. This is not a new WebSocket endpoint or command; it is a backward-compatible shape extension for an existing payload that already carries `Trade` objects.

On the frontend, extend `apps/web/lib/types.ts::Trade` with optional nullable fields matching the API names. Update `apps/web/components/Strategy/TradesTable.tsx` to add a compact risk column group. The table should remain readable in the right panel, so use two concise columns: `SL` and `TP`, each rendering both percentage and trigger price when present. Example display:

- `2.00% @ 98.00`
- `-` when absent

If both columns make the right panel too dense, keep the main table to `SL/TP` trigger prices and put the percentage in a `title` tooltip. Avoid changing the row height significantly; the trade table is a scanning surface, not a form. Add `maxHoldBars` as a `title` tooltip or a compact `Hold` column only if it fits without horizontal overflow.

Update `apps/web/components/Chart/TradeTooltip.tsx` so chart hover details also show risk levels for each marker event. This is important because users may inspect the trade from the chart rather than the table. Update `apps/web/lib/export.ts::tradesToCsv` to include the new columns after `pnl` and before `positionAfter`, so exported historical analysis keeps risk settings next to trade outcomes.

## File / Module Change Plan

| File | Change | Intent |
|---|---|---|
| `services/quant/app/engine/simulator.py` | Extend `TradeRecord` and `_Position` with risk snapshot fields; add broker-level risk snapshot storage; compute stop/take-profit prices on open; copy fields on close; use stored prices in `apply_risk`. | Make execution-time risk data the canonical source and ensure displayed trigger prices match actual close checks. |
| `services/quant/app/engine/backtester.py` | Call `broker.set_risk(risk)` after `strategy.risk_params()`. | Provide static strategy risk settings to the broker before any order fills. |
| `services/quant/app/paper/session.py` | Call `broker.set_risk(risk)` before `fill_pending`; include optional risk fields in `_trade_record_out`. | Keep shared `SimBroker`/`TradeRecord` paper and replay paths compatible after the simulator contract expands. |
| `services/quant/app/db/models.py` | Add nullable risk columns to `Trade`. | Persist risk snapshots for completed runs and paper/backtest trade rows. |
| `services/quant/app/db/session.py` | Add idempotent SQLite column upgrade for existing `trades` tables. | Keep existing local and packaged desktop databases usable after the schema expands. |
| `services/quant/app/db/repositories.py` | Save new fields in `persist_backtest_result` and `append_trade`. | Preserve risk snapshot data across all run persistence paths. |
| `services/quant/app/schemas.py` | Emit camelCase risk fields from `trade_out`. | Extend the existing REST contract without a new endpoint. |
| `services/quant/tests/test_simulator.py` | Add unit tests for old-shape `TradeRecord` construction, normalized risk snapshots, long/short stop/take-profit snapshots, stored max-hold behavior, and close-row copying. | Prove the simulator records the same levels it uses for risk execution without breaking existing construction sites. |
| `services/quant/tests/test_api.py` | Assert `/api/runs/{run_id}/trades` includes risk keys and non-null values for a strategy with configured risk. | Lock down API serialization and persistence. |
| `services/quant/tests/test_combo_api.py` or `test_combo_engine.py` | Assert combo trade rows keep member risk snapshot fields after `_combo_trades`. | Prevent combo reason-prefix copying from dropping new fields. |
| `services/quant/tests/test_paper_session.py` or existing paper tests | Assert paper/replay trade payloads and persisted rows include risk fields when the strategy defines them. | Prevent the shared broker change from creating backtest-only risk snapshots. |
| `apps/web/lib/types.ts` | Add optional nullable risk fields to `Trade`. | Type the expanded API contract. |
| `apps/web/components/Strategy/TradesTable.tsx` | Render stop-loss and take-profit settings/prices in the trade table. | Satisfy the user-facing requirement in the strategy backtest trade record panel. |
| `apps/web/components/Chart/TradeTooltip.tsx` | Add risk details to marker tooltip rows. | Keep chart inspection consistent with table inspection. |
| `apps/web/lib/export.ts` | Add risk columns to trade CSV output. | Preserve the new audit data in offline analysis. |
| `apps/web/lib/export.test.ts` | Update CSV header and row assertions for risk columns. | Lock down export compatibility. |
| `apps/web/components/Strategy/StrategyPanel.test.tsx` or a new `TradesTable.test.tsx` | Cover risk rendering for populated and null risk fields. | Prevent regressions in right-panel display. |

## Interface Design

No new REST, WebSocket, or CLI endpoint is required. The existing endpoint changes shape by adding nullable fields to each trade row:

```http
GET /api/runs/{run_id}/trades?limit=500
```

Response item shape after this feature:

```json
{
  "id": 123,
  "runId": 45,
  "ts": 1700003600,
  "side": "long",
  "price": 100.2,
  "qty": 9.98,
  "fee": 5.0,
  "pnl": 0.0,
  "stopLossPct": 0.02,
  "takeProfitPct": 0.04,
  "maxHoldBars": 12,
  "stopLossPrice": 98.196,
  "takeProfitPrice": 104.208,
  "positionAfter": 9.98,
  "reason": "signal_open"
}
```

Backward compatibility rules:

- Existing clients that ignore unknown fields continue to work.
- Existing database rows return these fields as `null`.
- A strategy with no configured take-profit returns `takeProfitPct: null` and `takeProfitPrice: null`.
- `maxHoldBars` can be non-null even when stop/take-profit fields are null.

Frontend API method signatures do not need to change:

```ts
api.trades(runId: number, limit = 500): Promise<Trade[]>
```

Only the `Trade` interface expands.

## Data Model

The normalized backend trade row should become:

```python
TradeRecord(
    ts: int,
    side: str,
    price: float,
    qty: float,
    fee: float,
    pnl: float,
    position_after: float,
    reason: str,
    stop_loss_pct: float | None = None,
    take_profit_pct: float | None = None,
    max_hold_bars: int | None = None,
    stop_loss_price: float | None = None,
    take_profit_price: float | None = None,
)
```

The new frontend fields should be:

```ts
interface Trade {
  stopLossPct?: number | null;
  takeProfitPct?: number | null;
  maxHoldBars?: number | null;
  stopLossPrice?: number | null;
  takeProfitPrice?: number | null;
}
```

Percentages stay decimal-based, matching strategy params and `RiskParams`: `0.02` means `2%`. The UI should format them as percentages with two decimal places. Prices use existing `fmtPrice`. Do not store preformatted display strings in the database or API.

For close rows, `stopLossPrice` and `takeProfitPrice` refer to the position that was closed, not a new calculation from the close price. This makes close rows self-contained for audit and CSV export.

For close rows caused by `stop_loss` or `take_profit`, the trigger price should match the relevant stored snapshot field. The close row's execution `price` remains the slipped fill price under the existing simulator model, so tests and UI copy should avoid implying the trigger and fill are always identical.

## Testing & Acceptance Criteria

Backend unit tests:

1. `TradeRecord` can still be constructed with only the pre-v2 fields and defaults all risk fields to `None`.
2. `set_risk` normalizes absent, zero, negative, NaN, and non-finite percentages to `None`, and normalizes positive `max_hold_bars` to an integer.
3. A long entry with `stop_loss_pct=0.05` and `take_profit_pct=0.10` at filled entry price `100` records `stop_loss_price=95` and `take_profit_price=110`.
4. A short entry with the same percentages records `stop_loss_price=105` and `take_profit_price=90`.
5. A close caused by `stop_loss` copies the entry row risk snapshot onto the close row.
6. A close caused by `take_profit` copies the same fields.
7. `apply_risk` uses the stored trigger prices, so slippage-adjusted entries display the same risk trigger prices used for trigger evaluation.
8. `apply_risk` uses stored `max_hold_bars` from the open position rather than a later `risk` argument.
9. A strategy without take-profit records `take_profit_pct` and `take_profit_price` as `None`, not `0`.

Backend API tests:

1. After a backtest, `GET /api/runs/{run_id}/trades` returns the five new camelCase keys for every trade.
2. At least one entry row from a built-in strategy with risk has non-null stop fields.
3. The response remains valid for rows where risk fields are null.
4. Re-running `init_db()` on a database that already has `trades` does not fail and leaves the new columns present.
5. Combo preview or saved combo backtest trade rows keep risk fields after reason prefixing.
6. Paper/replay trade events and persisted `append_trade` rows keep risk fields when the strategy defines them.

Frontend tests:

1. `TradesTable` renders `SL` and `TP` values when `stopLossPrice` and `takeProfitPrice` are present.
2. `TradesTable` renders `-` for null or missing risk fields.
3. `tradesToCsv` includes `stopLossPct`, `stopLossPrice`, `takeProfitPct`, `takeProfitPrice`, and `maxHoldBars` in deterministic column order.
4. `TradeTooltip` shows risk values for marker events without throwing when fields are absent.
5. Expanded tooltip content remains inside the chart container near the bottom edge.

Manual acceptance:

1. Run a backtest for `long_shadow_reversal`, `ema_cross_scalper`, or `vwap_rsi_momentum`, all of which define both stop-loss and take-profit.
2. Open the right-panel `成交明细` tab.
3. Confirm each entry and close row shows the configured stop-loss and take-profit context.
4. Confirm close rows caused by `stop_loss` or `take_profit` show a trigger price matching the close reason.
5. Export CSV and confirm the same risk fields appear in the file.
6. Open a historical run created before this feature and confirm the table still renders with dashes rather than errors.

Verification commands for implementation:

```powershell
cd services/quant
.venv/Scripts/python -m pytest tests/test_simulator.py tests/test_api.py tests/test_combo_api.py tests/test_paper_replay.py tests/test_paper_priming.py -q
```

```powershell
cd apps/web
pnpm test -- TradesTable export
pnpm test
pnpm build
```

## Risks & Mitigations

The largest correctness risk is showing risk settings derived from the wrong point in time. The mitigation is to snapshot risk inside `SimBroker` at order fill time and persist it with the trade, never deriving it from current strategy params in the frontend.

SQLite schema drift is another risk because `create_all` will not update existing local databases. The mitigation is an idempotent `ALTER TABLE` helper in `init_db`, covered by a test that initializes an old-style `trades` table and then runs the upgrade.

Slippage can make risk prices confusing if the UI computes them from raw candle opens. The mitigation is to compute trigger prices from the actual filled `entry_price`, after slippage, in the broker.

Some strategies only define a hard stop and `max_hold_bars`, not a take-profit. The UI must treat missing fields as an expected state. It should render `-`, not `0%`, because `0%` implies a configured level.

The trade table is already dense. The mitigation is to use concise `SL` and `TP` columns with compact formatting and tooltips, and keep more verbose detail in the chart hover tooltip and CSV export.

Combo trades are copied with `dataclasses.replace`. Adding fields to `TradeRecord` should naturally preserve them, but this should still be tested because combo trade rows are produced through a different engine path.

Paper trading uses the same simulator and trade dataclass as backtests. The mitigation is to set the broker risk snapshot before each paper/replay fill phase and serialize the new fields in paper trade events, even though the primary product request is the backtest trade table.

## 评审结论 / Review Verdict (v2)

**有条件通过**

The design is feasible and correctly sized after the v2 fixes above. There are no unresolved P0 or P1 concerns. Implementation may proceed if it follows the v2 requirements exactly: keep new `TradeRecord` fields defaulted, normalize broker risk snapshots, update the paper session path alongside backtest persistence, and run the backend/frontend tests listed in this document. Remaining P2 items are non-blocking but should be handled during implementation when touching the affected UI code.
