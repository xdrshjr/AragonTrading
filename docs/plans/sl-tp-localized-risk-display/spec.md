# Localized SL/TP Risk Display Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Make every visible SL/TP risk display in backtest trade history include Chinese labels and directly visible numeric values, without changing the existing risk snapshot data pipeline.

**Architecture:** Keep the previous `backtest-trade-risk-snapshots` backend and API contract intact. Implement this as a frontend presentation change over the existing `Trade.stopLossPct`, `Trade.stopLossPrice`, `Trade.takeProfitPct`, `Trade.takeProfitPrice`, and `Trade.maxHoldBars` fields, with test coverage for table, chart tooltip, and CSV export labels.

**Tech Stack:** Next.js 14 App Router, React 18, TypeScript, Tailwind, Vitest, FastAPI and SQLite only as unchanged upstream providers of the existing trade risk fields.

---

## Overview

The previous iteration completed the hard part of the feature: each simulated trade now carries an immutable risk snapshot from the execution engine through persistence, REST serialization, frontend types, trade table rows, chart hover details, and CSV export. The current user request is a continuation of that result, but the target is narrower. The trade history already has `SL` and `TP` fields, yet those English abbreviations are not self-explanatory for Chinese users. The display should make the mapping explicit: `SL` means stop loss, shown as `止损`; `TP` means take profit, shown as `止盈`.

This task should not redesign the risk calculation, database schema, or REST payload. It should only change user-facing labels and formatting in the places where risk snapshots are currently presented. The implementation must preserve the existing concrete numeric values: configured percentage, calculated trigger price, and max holding bars when present. In other words, it is not enough to rename columns to Chinese; the table and tooltip must still show values such as `2.00% @ 98.00` and `4.00% @ 104.00` directly in the visible UI, not only in hover titles or hidden attributes.

The expected end state is consistent language across the right-panel trade table, chart marker tooltip, and exported CSV. The main table can stay compact because it is a dense scanning surface, but the column headers should be Chinese-first with the abbreviation retained for continuity: `止损(SL)` and `止盈(TP)`. Tooltip rows should include the Chinese label next to the abbreviation: `止损(SL) 2.00% @ 98.00` and `止盈(TP) 4.00% @ 104.00`. CSV headers should be localized so exported files are understandable in Excel without remembering the internal camelCase field names.

## Technical Design

The current data path is already correct and should remain unchanged:

1. The Python simulator records stop-loss and take-profit snapshots on each `TradeRecord`.
2. The backend persists those snapshots in nullable trade columns.
3. `schemas.py::trade_out` exposes camelCase fields through `GET /api/runs/{run_id}/trades`.
4. `apps/web/lib/types.ts::Trade` already includes optional nullable risk fields.
5. `TradesTable`, `TradeTooltip`, and `tradesToCsv` consume those fields.

Implementation should start at the frontend presentation boundary. In `apps/web/components/Strategy/TradesTable.tsx`, change the two risk table headers from `SL` and `TP` to `止损(SL)` and `止盈(TP)`. Keep the body values visible in each cell by continuing to render the formatted percentage and trigger price together. The current helper `formatRiskLevel(pct, price)` already produces the right numeric shape: it returns `2.00% @ 98.00` when both values exist, price-only when only price exists, percentage-only when only percentage exists, and `-` when neither exists. That fallback behavior should remain because older runs and strategies without take-profit must not display misleading zero values.

The table's `title` should also become localized. Current `riskTitle` returns `Hold 12 bars`; replace it with `最大持仓 12 根K线` or `最大持仓 12 K`. Prefer `最大持仓 12 根K线` because it is readable and explicit. If no `maxHoldBars` exists, continue returning an empty title. Do not move the only visible risk number into the title. The title is supplemental detail; the main percentage and trigger price must stay in the cell text.

In `apps/web/components/Chart/TradeTooltip.tsx`, update `formatRiskLine` call sites so the labels passed into the formatter are Chinese-first. The target rendered strings are:

```text
止损(SL) 2.00% @ 98.00
止盈(TP) 4.00% @ 104.00
最大持仓 12 根K线
```

When a value is absent, the tooltip should render `止损(SL) -` and `止盈(TP) -`. This keeps the old null behavior while making the abbreviation understandable. The tooltip currently uses a two-column grid and a fixed width of 280px. Chinese labels are longer than `SL` and `TP`, so the implementation should check that the strings do not overflow. A low-risk adjustment is to keep the tooltip width at 280px, use `min-w-0` and `truncate` on each risk span if necessary, or increase width modestly to 300px only if tests or screenshots show wrapping problems. Avoid a large layout redesign.

In `apps/web/lib/export.ts`, localize CSV labels for the five risk columns. Keep the row keys unchanged, but change labels from internal names to Chinese labels:

| Current key | New CSV label |
|---|---|
| `stopLossPct` | `止损比例` |
| `stopLossPrice` | `止损触发价` |
| `takeProfitPct` | `止盈比例` |
| `takeProfitPrice` | `止盈触发价` |
| `maxHoldBars` | `最大持仓K线` |

Do not convert stored decimal percentages to display percentages in CSV in this task unless the downstream implementer also updates tests and documents it. The previous export intentionally writes raw numeric fields for analysis. The user's requirement is that concrete numbers are visible and labels are understandable, not that CSV units change. Raw `0.02` remains machine-friendly and matches the API contract. If a product decision later requires `2.00%`, that should be a separate export-format change because it affects spreadsheet formulas and historical analysis.

No backend changes are required. Avoid touching `services/quant/app/engine/simulator.py`, `db/models.py`, `repositories.py`, `schemas.py`, and the backend tests unless an implementation-time discovery shows the previous commit is missing from the working tree. The current task is a localization and presentation pass over fields that already exist.

## File / Module Change Plan

| File | Create / Modify | Intent |
|---|---|---|
| `apps/web/components/Strategy/TradesTable.tsx` | Modify | Rename risk headers to `止损(SL)` and `止盈(TP)`; keep percentage and trigger price visible in each row; localize max-hold title text. |
| `apps/web/components/Strategy/TradesTable.test.tsx` | Modify | Assert Chinese headers are rendered and rows still contain concrete values such as `2.00% @ 98.00` and `4.00% @ 104.00`; keep null fallback coverage. |
| `apps/web/components/Chart/TradeTooltip.tsx` | Modify | Render tooltip risk lines as `止损(SL) ...`, `止盈(TP) ...`, and `最大持仓 ... 根K线`; preserve absent-value dashes. |
| `apps/web/components/Chart/TradeTooltip.test.tsx` | Modify | Update expectations from English `SL` / `TP` / `Hold` strings to Chinese-first labels with the same concrete numbers. |
| `apps/web/lib/export.ts` | Modify | Change CSV column labels for risk fields to Chinese while keeping field keys and raw numeric values unchanged. |
| `apps/web/lib/export.test.ts` | Modify | Assert CSV headers contain `止损比例`, `止损触发价`, `止盈比例`, `止盈触发价`, and `最大持仓K线` in the same deterministic column positions. |
| `apps/web/lib/types.ts` | No change expected | Existing optional nullable risk fields already match the REST payload and should not be renamed. |
| `services/quant/**` | No change expected | Backend risk snapshot capture and serialization were completed in commit `e19b177b9e7530afc4496738bc086cb7fc4841d2`. |

## Interface Design

There are no new REST, WebSocket, or CLI interfaces. The existing REST contract remains:

```http
GET /api/runs/{run_id}/trades?limit=500
```

The response item keeps the same camelCase field names:

```json
{
  "stopLossPct": 0.02,
  "stopLossPrice": 98.0,
  "takeProfitPct": 0.04,
  "takeProfitPrice": 104.0,
  "maxHoldBars": 12
}
```

Frontend TypeScript interfaces also keep the same names. Do not rename fields to Chinese identifiers and do not introduce alternate API keys. Localization belongs at render/export label boundaries. This keeps existing consumers, chart marker derivation, paper session events, and CSV row generation compatible with the previous iteration.

The user-facing interface changes are:

- Trade table headers: `止损(SL)` and `止盈(TP)`.
- Trade table body examples: `2.00% @ 98.00`, `4.00% @ 104.00`, or `-`.
- Trade table title for hold bars: `最大持仓 12 根K线`.
- Chart tooltip examples: `止损(SL) 2.00% @ 98.00`, `止盈(TP) 4.00% @ 104.00`, `最大持仓 12 根K线`.
- CSV risk headers: `止损比例`, `止损触发价`, `止盈比例`, `止盈触发价`, `最大持仓K线`.

## Data Model

No persisted data model changes are required. The implementation consumes the existing frontend `Trade` shape:

```ts
interface Trade {
  stopLossPct?: number | null;
  stopLossPrice?: number | null;
  takeProfitPct?: number | null;
  takeProfitPrice?: number | null;
  maxHoldBars?: number | null;
}
```

Percent fields remain decimal values from the backend, where `0.02` means `2%`. UI display functions convert those decimals to percentage strings with two decimal places. Price fields remain numeric trigger/reference prices and should continue to use `fmtPrice`. Missing values remain `null` or `undefined`; render them as `-`, not `0`, `0%`, or an empty string in visible UI. CSV export can keep empty cells for missing fields because CSV has no visual dash convention and the existing export already serializes nullish values as empty strings.

If the implementer decides to extract duplicate formatting from `TradesTable` and `TradeTooltip`, create a small frontend-only helper such as `apps/web/lib/riskDisplay.ts`. This is optional. If done, it must be covered by tests and should not become a broad formatting abstraction. The safer minimal path is to update the two existing helper call sites and tests.

## Testing & Acceptance Criteria

Frontend unit tests should cover the user-facing requirement directly:

1. `TradesTable` renders `止损(SL)` and `止盈(TP)` headers.
2. `TradesTable` still renders `2.00% @ 98.00` and `4.00% @ 104.00` for populated risk fields.
3. `TradesTable` renders `-` for missing stop-loss or take-profit values.
4. `TradesTable` uses a localized max-hold title when `maxHoldBars` exists.
5. `TradeTooltip` renders `止损(SL) 2.00% @ 98.00`, `止盈(TP) 4.00% @ 104.00`, and `最大持仓 12 根K线`.
6. `TradeTooltip` renders `止损(SL) -` and `止盈(TP) -` when fields are absent.
7. `tradesToCsv` keeps the same risk column positions after `盈亏` / `pnl`, but the header labels are localized.
8. `tradesToCsv` keeps the same raw numeric row values for risk fields.

Recommended verification commands for the implementation node:

```powershell
cd apps/web
pnpm test -- TradesTable TradeTooltip export
pnpm test
```

Manual acceptance should be done in a running app after implementation:

1. Run any backtest for a strategy with both stop-loss and take-profit, such as EMA cross scalper or long-shadow reversal.
2. Open the trade detail table in the strategy panel.
3. Confirm the risk columns read `止损(SL)` and `止盈(TP)`.
4. Confirm populated rows show specific numeric values, not only labels.
5. Hover a trade marker on the chart and confirm the tooltip uses Chinese risk labels with the same numbers.
6. Export trades to CSV and confirm the risk columns use Chinese headers while numeric cells remain present.
7. Open or simulate a trade row with null risk fields and confirm the UI shows `-` without throwing.

## Risks & Mitigations

The main product risk is accidentally solving only the label problem while hiding the numbers. The mitigation is to keep explicit test assertions for full rendered strings that include both labels and values. Do not accept tests that only check `止损` and `止盈` headers.

The main compatibility risk is renaming API fields or TypeScript properties to Chinese. That would break the backend contract and all existing consumers. The mitigation is to keep camelCase data keys unchanged and localize only display labels.

CSV export has a unit ambiguity risk. Changing `0.02` to `2.00%` may look nicer to users but would be a behavior change for downstream spreadsheet and pandas analysis. For this iteration, localize headers only and preserve raw values. If percentages are later changed to formatted strings, the export tests must be updated intentionally.

The table and tooltip have density constraints. Chinese labels are longer than `SL` and `TP`, so there is a small overflow risk in the right panel. The mitigation is to keep headers concise as `止损(SL)` and `止盈(TP)`, avoid adding extra explanatory copy inside every table cell, and verify with the existing rendered tests plus a quick manual viewport check during implementation.

There is also a mojibake risk in terminal output on Windows, visible in existing `Get-Content` output. Source files are UTF-8 and already contain Chinese UI strings. The implementation should edit files as UTF-8 and avoid using shell redirection that could write with a legacy code page.
