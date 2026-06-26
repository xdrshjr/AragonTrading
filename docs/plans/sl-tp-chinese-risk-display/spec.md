# SL/TP Chinese Risk Display Implementation Plan v2

**Version:** v2

## 评审记录

- P1 - File / Module Change Plan 把当前已存在的
  `docs/plans/sl-tp-chinese-risk-display/spec.md` 标为 `Create`，这会误导
  下游节点重新创建或覆盖方案文件。已在正文中改为 `Modify`，并说明该文件是
  v2 方案与评审记录的维护位置。
- P1 - 验证命令在 `pnpm --prefix apps/web` 后仍使用 `apps/web/...`
  路径过滤。该命令运行脚本时工作目录是 `apps/web`，路径应相对该目录，否则
  Vitest 过滤可能不匹配目标文件。已在正文中改为 `lib/...`、
  `components/...` 的相对路径，并保留全量测试兜底。
- P2 - Data Model 示例使用对象形状的 `type RiskDisplay = { ... }`，
  与 `CLAUDE.md` 中公共对象 API 优先使用 `interface` 的约定不完全一致。
  已改为 `interface RiskDisplay`；`RiskKind` 仍保留字符串联合类型，因为它
  表达的是有限枚举值而不是对象 API。
- P2 - 单测清单覆盖了主要展示分支，但没有明确要求非有限数值和非正
  `maxHoldBars` 的回归用例。已补充 `NaN`、`Infinity`、`0` 等边界断言，
  避免下游实现只覆盖正常样例。

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Make every visible stop-loss and take-profit risk snapshot display its Chinese meaning and concrete numeric values in the backtest trade record experience.

**Architecture:** Keep the previous execution-time risk snapshot pipeline unchanged. Add one frontend display helper for localized risk labels and value formatting, then use it from the trade table, chart tooltip, and trade CSV export so the same `stopLoss*` and `takeProfit*` fields are rendered consistently everywhere.

**Tech Stack:** Next.js 14 App Router, React 18, TypeScript, Tailwind, Vitest, Python 3.11, FastAPI, SQLAlchemy async, SQLite.

---

## Overview

The previous round, committed as `e19b177b9e7530afc4496738bc086cb7fc4841d2`, added immutable risk snapshots to each trade. Backend records now carry `stopLossPct`, `stopLossPrice`, `takeProfitPct`, `takeProfitPrice`, and `maxHoldBars`, and the frontend already receives these fields in the `Trade` interface. That solved the data availability problem. The remaining user-facing problem is presentation: the current table and tooltip still expose the risk concepts as English abbreviations, primarily `SL`, `TP`, and `Hold`. A Chinese-speaking user can see values such as `2.00% @ 98.00`, but the labels do not explain that `SL` means stop-loss and `TP` means take-profit.

This change localizes the risk display without touching the simulator, database, REST API, or WebSocket contracts. The user should see both the Chinese term and the industry abbreviation together, for example `止损(SL)` and `止盈(TP)`, and each non-null risk setting should show the concrete numeric value already stored on the trade. A stop-loss with both percentage and trigger price should render as `止损(SL) 2.00% @ 98.00`; a take-profit should render as `止盈(TP) 4.00% @ 104.00`. When only one numeric field is available, the UI must still display that concrete field rather than hiding the row. When both fields are absent, the UI should show `-` with the localized label still present where space allows.

The scope is deliberately small. The previous round already established that the source of truth is the per-trade snapshot captured by `SimBroker`, not current strategy parameters. This plan preserves that architecture. Downstream implementation should only improve the display surface and tests around existing fields. It must not recalculate risk levels, alter persistence, or change the backend response shape.

## Technical Design

Current relevant code paths are:

1. `services/quant/app/engine/simulator.py` records risk snapshots.
2. `services/quant/app/schemas.py` serializes those snapshots as camelCase fields.
3. `apps/web/lib/types.ts` defines the optional nullable `Trade` fields.
4. `apps/web/components/Strategy/TradesTable.tsx` renders table columns titled `SL` and `TP`.
5. `apps/web/components/Chart/TradeTooltip.tsx` renders tooltip lines titled `SL`, `TP`, and `Hold`.
6. `apps/web/lib/export.ts` writes CSV columns named `stopLossPct`, `stopLossPrice`, `takeProfitPct`, `takeProfitPrice`, and `maxHoldBars`.

Add a new frontend-only helper module at `apps/web/lib/riskDisplay.ts`. This module should own all localized risk labels and formatting rules. Keeping the helper outside the components avoids duplicated string rules and makes the tests precise. It should export a small set of pure functions:

```ts
export type RiskKind = "stopLoss" | "takeProfit";

export function riskColumnLabel(kind: RiskKind): string;
export function riskInlineLabel(kind: RiskKind): string;
export function formatRiskPct(value: number | null | undefined): string | null;
export function formatRiskValue(
  pct: number | null | undefined,
  price: number | null | undefined,
): string;
export function formatRiskLine(
  kind: RiskKind,
  pct: number | null | undefined,
  price: number | null | undefined,
): string;
export function formatMaxHoldBars(value: number | null | undefined): string | null;
```

The required label outputs are exact:

```ts
riskColumnLabel("stopLoss") === "止损(SL)"
riskColumnLabel("takeProfit") === "止盈(TP)"
riskInlineLabel("stopLoss") === "止损(SL)"
riskInlineLabel("takeProfit") === "止盈(TP)"
```

`formatRiskPct` should preserve the previous numeric behavior: a finite decimal `0.02` becomes `2.00%`; `null`, `undefined`, `NaN`, and non-finite values return `null`. `formatRiskValue` should preserve the previous value behavior: percentage plus finite price renders as `2.00% @ 98.00`; price only renders as `98.00`; percentage only renders as `2.00%`; no usable numeric field renders as `-`. Use the existing `fmtPrice` from `apps/web/lib/format.ts` for price rendering so decimal precision remains consistent with the rest of the trading UI. `formatRiskLine` should prefix the localized label, for example `止损(SL) 2.00% @ 98.00` or `止盈(TP) -`. `formatMaxHoldBars` should return `最长持有 12 根K线` for finite positive values and `null` otherwise.

Update `TradesTable.tsx` to import the helper and remove its local `formatRiskPct`, `formatRiskLevel`, and English `riskTitle` implementations. The table headers should change from `SL` and `TP` to `止损(SL)` and `止盈(TP)`. The cell values should continue to show concrete numbers using `formatRiskValue`, not only the header. This gives a compact table:

```text
止损(SL)     止盈(TP)
2.00% @ 98.00 4.00% @ 104.00
```

The `title` for each cell should use `formatMaxHoldBars`, so the hover text becomes `最长持有 12 根K线` instead of `Hold 12 bars`. If `maxHoldBars` is absent, keep `title=""` to match the current missing-value behavior. Do not add a third visible hold column in this task; the table is already dense and the user only asked for SL/TP Chinese mapping and numeric display.

Update `TradeTooltip.tsx` to import the same helper and remove its local risk formatting functions. The tooltip lines should render `止损(SL) 2.00% @ 98.00`, `止盈(TP) 4.00% @ 104.00`, and `最长持有 12 根K线`. The tooltip title `Backtest trades` is adjacent to the same trade-inspection surface and should be localized to `回测成交`. Keep the existing `BUY`, `SELL`, and `CLOSE` marker labels unchanged unless a later requirement asks for full trade action localization; changing them now would broaden the task beyond SL/TP risk display.

Update `apps/web/lib/export.ts` because CSV is also a user-facing trade record surface and the rest of the CSV already uses Chinese labels for time, direction, price, quantity, fee, PnL, position, and reason. Replace the five English risk headers with Chinese headers:

```text
止损比例(SL%)
止损价格(SL)
止盈比例(TP%)
止盈价格(TP)
最长持有K线
```

Keep the underlying row keys and numeric values unchanged. In particular, do not preformat `0.02` to `2.00%` in CSV unless a later requirement explicitly requests presentation-formatted export values. Raw decimal percentages are better for Excel and Pandas analysis, and the localized header tells users what the numbers mean.

## File / Module Change Plan

| File | Create / Modify | Intent |
|---|---|---|
| `docs/plans/sl-tp-chinese-risk-display/spec.md` | Modify | Maintain this v2 implementation plan, review notes, and review verdict; downstream code work should not recreate or overwrite it. |
| `apps/web/lib/riskDisplay.ts` | Create | Centralize Chinese labels and concrete risk value formatting for stop-loss, take-profit, and max-hold display. |
| `apps/web/lib/riskDisplay.test.ts` | Create | Unit-test all label and formatting branches without rendering React components. |
| `apps/web/components/Strategy/TradesTable.tsx` | Modify | Replace `SL` / `TP` headers with `止损(SL)` / `止盈(TP)`, keep concrete numeric value cells, and localize max-hold title text. |
| `apps/web/components/Strategy/TradesTable.test.tsx` | Modify | Assert Chinese headers, numeric risk cells, localized max-hold title, and dash rendering for missing fields. |
| `apps/web/components/Chart/TradeTooltip.tsx` | Modify | Replace English risk lines with Chinese `止损(SL)` / `止盈(TP)` labels and localized max-hold text while keeping the existing marker layout. |
| `apps/web/components/Chart/TradeTooltip.test.tsx` | Modify | Assert tooltip output contains localized risk labels, concrete values, and no max-hold text when the field is absent. |
| `apps/web/lib/export.ts` | Modify | Localize trade CSV risk headers while preserving the existing data keys, column order, and raw numeric values. |
| `apps/web/lib/export.test.ts` | Modify | Update header expectations to the Chinese risk labels and preserve value-position assertions after `pnl` and before `positionAfter`. |

No backend files should be changed for this requirement. The previous commit already added and tested the backend risk snapshot pipeline.

## Interface Design

There are no REST, WebSocket, database, or CLI interface changes. The existing trade API remains:

```http
GET /api/runs/{run_id}/trades?limit=500
```

The response item shape continues to include the same optional nullable fields:

```ts
interface Trade {
  stopLossPct?: number | null;
  takeProfitPct?: number | null;
  maxHoldBars?: number | null;
  stopLossPrice?: number | null;
  takeProfitPrice?: number | null;
}
```

The user-visible display interface changes as follows:

| Surface | Before | After |
|---|---|---|
| Trade table stop-loss header | `SL` | `止损(SL)` |
| Trade table take-profit header | `TP` | `止盈(TP)` |
| Tooltip stop-loss line | `SL 2.00% @ 98.00` | `止损(SL) 2.00% @ 98.00` |
| Tooltip take-profit line | `TP 4.00% @ 104.00` | `止盈(TP) 4.00% @ 104.00` |
| Hold title / line | `Hold 12 bars`, `Hold 12` | `最长持有 12 根K线` |
| CSV risk headers | `stopLossPct`, `stopLossPrice`, `takeProfitPct`, `takeProfitPrice`, `maxHoldBars` | `止损比例(SL%)`, `止损价格(SL)`, `止盈比例(TP%)`, `止盈价格(TP)`, `最长持有K线` |

CSV values remain raw numbers. React table and tooltip values remain formatted display strings.

## Data Model

The persistent and API data model does not change. The relevant in-memory display model is derived from existing `Trade` fields:

```ts
interface RiskDisplay {
  label: "止损(SL)" | "止盈(TP)";
  value: string;
  maxHoldTitle: string | null;
}
```

The helper should derive `RiskDisplay.value` from existing nullable fields only. It must not infer a stop-loss price from `price` and `stopLossPct`, and it must not infer a take-profit price from `price` and `takeProfitPct`. If a row has a percentage but no trigger price, show the percentage. If a row has a trigger price but no percentage, show the trigger price. If both are absent or invalid, show `-`. This distinction matters for backward compatibility with old runs and for partial future data.

Valid numeric checks should be consistent across all surfaces. A value is displayable only when it is a JavaScript `number` and `Number.isFinite(value)` is true. `0` is allowed for prices if such a value ever appears, but percentages should simply follow the existing snapshot field rather than normalizing again in the frontend. Backend normalization already treats missing risk as `null`; frontend formatting only decides how to present the received value.

## Testing & Acceptance Criteria

Unit tests for `apps/web/lib/riskDisplay.test.ts`:

1. `riskColumnLabel("stopLoss")` returns `止损(SL)`.
2. `riskColumnLabel("takeProfit")` returns `止盈(TP)`.
3. `formatRiskValue(0.02, 98)` returns `2.00% @ 98.00`.
4. `formatRiskValue(null, 98)` returns `98.00`.
5. `formatRiskValue(0.02, null)` returns `2.00%`.
6. `formatRiskValue(null, null)` returns `-`.
7. `formatRiskLine("stopLoss", 0.02, 98)` returns `止损(SL) 2.00% @ 98.00`.
8. `formatRiskLine("takeProfit", 0.04, 104)` returns `止盈(TP) 4.00% @ 104.00`.
9. `formatMaxHoldBars(12)` returns `最长持有 12 根K线`.
10. `formatMaxHoldBars(null)` returns `null`.
11. `formatRiskPct(Number.NaN)` and `formatRiskPct(Number.POSITIVE_INFINITY)` return `null`.
12. `formatRiskValue(Number.NaN, Number.POSITIVE_INFINITY)` returns `-`.
13. `formatMaxHoldBars(0)`, `formatMaxHoldBars(-1)`, and `formatMaxHoldBars(Number.POSITIVE_INFINITY)` return `null`.

React component tests:

1. `TradesTable.test.tsx` should assert the rendered markup contains `止损(SL)`, `止盈(TP)`, `2.00% @ 98.00`, and `4.00% @ 104.00`.
2. `TradesTable.test.tsx` should assert a row with `maxHoldBars: 12` has `title="最长持有 12 根K线"`.
3. `TradesTable.test.tsx` should keep the missing-risk assertion for two `-` cells.
4. `TradeTooltip.test.tsx` should assert the rendered markup contains `止损(SL) 2.00% @ 98.00`, `止盈(TP) 4.00% @ 104.00`, and `最长持有 12 根K线`.
5. `TradeTooltip.test.tsx` should assert missing fields render `止损(SL) -` and `止盈(TP) -`, and do not render `最长持有`.

Export tests:

1. `export.test.ts` should keep the risk columns after `pnl` and before `positionAfter`.
2. The header row should contain `止损比例(SL%)`, `止损价格(SL)`, `止盈比例(TP%)`, `止盈价格(TP)`, and `最长持有K线`.
3. The data row should still contain raw values such as `0.02,98,0.04,104,12`.

Verification commands for the downstream implementation:

```powershell
pnpm --prefix apps/web test -- lib/riskDisplay.test.ts components/Strategy/TradesTable.test.tsx components/Chart/TradeTooltip.test.tsx lib/export.test.ts
```

The filtered paths above are relative to `apps/web` because `pnpm --prefix apps/web`
runs the package script from that directory.

If Vitest does not accept the path filter in this environment, run:

```powershell
pnpm --prefix apps/web test
```

Manual acceptance:

1. Start the app with `pnpm dev`.
2. Run a backtest for a strategy that has stop-loss and take-profit settings.
3. Open the trade detail table and confirm the risk columns are labeled `止损(SL)` and `止盈(TP)`.
4. Confirm each populated risk cell shows concrete numbers, such as `2.00% @ 98.00`, not only the abbreviation.
5. Hover a chart trade marker and confirm the tooltip shows localized stop-loss and take-profit lines with concrete values.
6. Export trades to CSV and confirm the risk headers are Chinese while numeric values remain analyzable.

## Risks & Mitigations

The main risk is layout pressure in the right-side trade table. `止损(SL)` and `止盈(TP)` are wider than `SL` and `TP`, but the table already displays the concrete value strings, so the header width is not the dominant factor. Keep the compact two-column layout and do not add a visible hold column in this task. If the implementation sees horizontal clipping, use existing table overflow behavior rather than reducing the text to English-only abbreviations.

Another risk is inconsistent formatting between table, tooltip, and CSV. The mitigation is the new `riskDisplay.ts` helper. Components should not keep their own copies of `formatRiskPct` or `formatRiskLine`; tests should exercise the helper directly and also verify the rendered surfaces.

CSV localization can surprise automated consumers that parse headers by English names. In this project, the exported trade CSV already uses Chinese headers for most columns, and the previous risk headers were the outliers. Preserve raw numeric values and column order so human and Pandas users can still analyze the export. If strict machine-readable exports become a product requirement later, add a separate export mode rather than mixing that concern into this localization task.

Finally, do not modify backend risk semantics while doing this work. Recomputing stop-loss or take-profit values in the browser would reintroduce the historical-audit problem that the previous round solved. The frontend must display only the snapshot values it receives.

## 评审结论

通过。P1 问题已在 v2 正文中修复，剩余 P2 建议也已并入数据模型与测试验收标准；下游实现可按本方案继续。
