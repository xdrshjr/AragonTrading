# Strategy Tabs And Combo Walk-forward Spec

> Version: v2

## 评审记录

- P0: 未发现。方案不需要新增数据库结构或改变 REST path，现有 Next.js 14 / FastAPI / Zustand / TanStack Query 栈可以承载。
- P1: 无组合策略可见时只把 `strategyId` 置为 `null` 不够。当前 `StrategyPanel` 的结果区可能继续显示上一条普通策略或组合策略的旧 `metrics` / `walkForwardResult`，需要在正文中明确隐藏并清理运行结果状态。
- P1: 后端草案里的 `_run_combo_walk_forward(...)` 和 `_run_combo_wf_windows(...)` 参数过多，违反 `CLAUDE.md` 中函数参数不超过 5 个的项目约定。正文已改为使用小型上下文对象 / dataclass 承载 Walk-forward 输入。
- P1: 组合 Walk-forward 窗口循环最初允许复用同一组 `ComboMember` 实例，正文只在风险表里把“后续 code review 确认是否无状态”作为条件，无法保证窗口独立性。正文已改为每个窗口从 `members_spec` 重建成员和 policy。
- P2: `strategyListTab` 的 v15 迁移如果只给 `"regular"` 默认值，会丢失旧版 `mode: "combo"` 用户的意图。正文已补充在 v14 兼容迁移时把旧组合视图落到 `strategyListTab: "combo"`。
- P2: `Tabs` 当前 underline 变体没有活动态 ARIA 属性。正文保留“只在可无视觉回归地修改 reusable Tabs 时添加 `aria-current`”的约束，不阻塞本次开发。

## Overview

本设计为交易策略右侧面板增加一个靠上、靠左的策略分类页签，并补齐组合策略的高级回测能力。用户需求可以拆成两个产品目标：第一，当前“交易策略”列表不再把单个常规策略和用户创建的组合策略混在同一列表里，而是在策略列表内容区域顶部显示两个页签：“常规策略”和“组合策略”。用户选中“常规策略”时，只看到一个一个独立运行的原子策略；用户选中“组合策略”时，只看到自己创建并保存的组合策略。第二，组合策略不能只支持普通回测，还要能像常规策略一样进入“高级回测 / Walk-forward”流程。

当前项目已经具备大部分底层能力。`apps/web/components/Strategy/StrategyPanel.tsx` 会加载 `/api/strategies` 并把原子策略与用户组合合并到同一个回测列表中；`StrategyList.tsx` 通过 `kind === "combo"` 显示组合徽标；`BacktestModeSwitch.tsx` 和 `WalkForwardForm.tsx` 已经提供普通回测与高级回测的 UI；后端 `POST /api/strategies/{id}/walk-forward` 已经能对原子策略执行滑动窗口高级回测。缺口也很明确：前端在选中组合时用 `effectiveKind = "simple"` 强制绕过高级回测，后端 `services/quant/app/agent/services.py::_resolve_strategy_spec` 对组合策略抛出 `WF_COMBO_UNSUPPORTED`。

本方案保持现有架构和接口形状，优先做最小但完整的演进。前端复用现有 `Tabs` 的 `underline` 变体实现靠左页签，新增一个持久化的策略分类状态，并把列表选择收敛逻辑改为“当前模式 + 当前分类”驱动。后端复用现有组合回测核心 `run_combo_backtest`、窗口规划 `plan_walk_forward_windows`、汇总 `summarize_windows` 和落库 `persist_walk_forward_result`，让保存后的组合策略 id 可以走同一个 `/walk-forward` REST path。数据库不新增表和列；新增状态只落在前端 Zustand `aragon-ui`，高级回测结果仍写入 `strategy_runs.config_json`。

## Technical Design

### Current architecture context

The project is a Next.js 14 App Router frontend with React 18, TypeScript, Tailwind, TanStack Query, and Zustand persist. The frontend talks to a FastAPI backend through the Next rewrite prefix `/api/py/*`. Strategy domain contracts live in `apps/web/lib/types.ts`; REST calls live in `apps/web/lib/api.ts`; UI state lives in `apps/web/lib/store.ts`; the right panel strategy workflow is composed in `apps/web/components/Strategy/StrategyPanel.tsx`.

Backend strategy execution is centralized in `services/quant/app/agent/services.py`, even for UI calls. FastAPI routes in `services/quant/app/api/strategies.py` only unpack request bodies and delegate into the service layer. Atomic backtests use `engine.backtester.run_backtest`; combo backtests use `engine.combo.run_combo_backtest`; Walk-forward planning uses `engine.backtester.plan_walk_forward_windows`; summary metrics use `engine.metrics.summarize_windows`; persistence uses `db.repositories.create_run` and `persist_walk_forward_result`.

### Frontend category tabs

Add a new frontend type:

```ts
export type StrategyListTab = "regular" | "combo";
```

Store it in Zustand as `strategyListTab` with setter `setStrategyListTab(tab)`. Default is `"regular"`. Persist it in `aragon-ui` and bump the persist version from `14` to `15`; in the migration path `if (version < 15)`, add `strategyListTab`. If the persisted payload is an older `mode: "combo"` state, migrate to `{ mode: "backtest", strategyListTab: "combo" }`; otherwise use `strategyListTab: "regular"`. Capture that legacy combo-mode intent before the existing v14 migration normalizes `mode` to `"backtest"`. This keeps refresh behavior predictable without touching runtime handles.

Add a small pure helper module `apps/web/lib/strategyCatalog.ts` so the filtering and fallback selection rules can be unit-tested without rendering the whole right panel. The helper should export:

```ts
export const STRATEGY_LIST_TABS = [
  { key: "regular", label: "常规策略" },
  { key: "combo", label: "组合策略" },
] as const;

export function splitStrategyCatalog(strategies: Strategy[]): {
  regular: Strategy[];
  combos: Strategy[];
};

export function visibleStrategiesForStrategyPanel(args: {
  strategies: Strategy[];
  mode: Mode;
  strategyListTab: StrategyListTab;
}): Strategy[];

export function resolveVisibleStrategyId(args: {
  strategies: Strategy[];
  currentId: number | null;
  mode: Mode;
  strategyListTab: StrategyListTab;
}): number | null;
```

The exact rules are:

1. `regular` is `strategies.filter((s) => s.kind !== "combo")`.
2. `combos` is `strategies.filter(isUserCombo)`, not every `kind === "combo"`. The builtin `regime_rotation` template is not a user-created strategy and must not appear in the “组合策略” tab.
3. In `mode === "paper"`, always return `regular`; paper mode still excludes combos.
4. In `mode === "backtest"`, return `regular` or `combos` based on `strategyListTab`.
5. In `mode === "compare"`, the helper is not used by the rendered strategy list because `CompareView` remains its own workflow.
6. `resolveVisibleStrategyId` returns `currentId` if that id is in the visible list; otherwise it returns the first visible id; otherwise `null`.

Modify `StrategyPanel.tsx` to use those helpers. Replace the current inline `atoms`, `userCombos`, `listStrategies`, and selection guard with helper-driven values. The selection effect should depend on `strategies`, `mode`, `strategyListTab`, `strategyId`, `setStrategyId`, `setRunId`, and `setWalkForwardResult`. It should call `resolveVisibleStrategyId(...)` and update only when the resolved id differs from the current id. This is important when the user switches from “常规策略” to “组合策略”: if the previous atomic id is no longer visible, the first saved combo becomes selected; if no combo exists, selection becomes `null`.

When the resolved id is `null`, the panel must also clear result-scoped state that can otherwise show stale data: `setRunId(null)`, `setMetrics(undefined)`, `setCoverage(undefined)`, `setComboResult(null)`, and `setWalkForwardResult(null)`. Render the detail panel, ordinary metrics/trades/history block, and Walk-forward results only when `selected` is truthy. This prevents an empty combo tab from showing the previous regular strategy's metrics or the previous combo's Walk-forward result.

Render the category tabs immediately above `StrategyList` and below `PaperSelectionBar`:

```tsx
{mode === "backtest" && (
  <div className="mb-3">
    <Tabs
      tabs={STRATEGY_LIST_TABS}
      active={strategyListTab}
      onChange={(key) => setStrategyListTab(key as StrategyListTab)}
      variant="underline"
    />
  </div>
)}
```

Use the existing `Tabs` `underline` variant because it is already left-aligned, content-width, and visually consistent with other in-workspace tabs. Do not create a new tab component. Add `aria-pressed` or `aria-current="page"` only if it can be done inside the reusable `Tabs` component without changing visual output for existing callers; otherwise rely on the existing button semantics and visible active state.

When `mode === "backtest" && strategyListTab === "combo" && listStrategies.length === 0`, render a compact empty state in the list area:

```tsx
<div className="rounded-[8px] border border-[var(--border)] bg-[var(--surface)] px-3 py-4 text-xs text-[var(--muted)]">
  暂无组合策略
  <Button size="sm" variant="default" onClick={openNewCombo}>新建组合</Button>
</div>
```

The exact layout can use `flex flex-col gap-2` and must not be a nested card inside another card. Render this empty state in place of `StrategyList` for the empty combo tab, and do not render detail or result panels until a combo exists and becomes selected. The header-level `+ 新建组合` button remains unchanged for backtest mode.

### Combo advanced backtest UI

Remove the frontend limitation that forces combo selections into simple backtest:

```ts
const selectedCombo = selected && isCombo(selected) ? selected : null;
const effectiveKind = selectedCombo ? "simple" : backtestKind;
```

Replace all result gating with `backtestKind` directly:

```tsx
{mode === "backtest" && backtestKind === "walkforward" ? (
  <WalkForwardResults ... />
) : (
  ordinary metrics / trades / history tabs
)}
```

Render `BacktestModeSwitch` for every selected item in backtest mode, including selected combos. The simplest implementation is to move the existing switch above the `selectedCombo ? ... : ...` branch:

```tsx
{mode === "backtest" && selected && (
  <div className="mb-3">
    <BacktestModeSwitch />
  </div>
)}
```

Update `ComboStrategyDetail.tsx` so it accepts `backtestKind?: BacktestKind` and `onRunWalkForward?: (combo: Strategy) => void`. In simple mode, keep the current “运行回测 / 查看详情 / 编辑 / 删除” buttons. In walk-forward mode, render `<WalkForwardForm busy={busy} onRun={() => onRunWalkForward?.(combo)} />`, keep “查看详情 / 编辑 / 删除” available, and hide the simple “运行回测” button. This mirrors `StrategyDetail.tsx`, avoids duplicate window parameter controls, and keeps combo management actions available while advanced mode is selected.

In `StrategyPanel.tsx`, pass:

```tsx
<ComboStrategyDetail
  combo={selectedCombo}
  backtestKind={backtestKind}
  busy={actionBusy}
  onRunBacktest={(c) => backtest.mutate({ id: c.id, showRunRange: true })}
  onRunWalkForward={(c) => walkForward.mutate({ id: c.id })}
  onView={openComboDetail}
  onEdit={openEditCombo}
  onDelete={openDeleteCombo}
/>
```

The existing `runWalkForward` helper already accepts `params?: Params` and skips `api.updateStrategy` when params are absent, so a saved combo can call it without changes. For atomic strategies, `StrategyDetail` still passes edited params and `runWalkForward` persists the parameter changes before running.

The ordinary automatic backtest effect should continue to run only for simple mode:

```ts
if (mode !== "backtest" || backtestKind !== "simple" || !strategyId || !strategies) return;
```

This preserves the existing “advanced backtest is explicit only” behavior for both regular and combo strategies.

### Backend combo Walk-forward

Keep the route signature unchanged:

```http
POST /api/strategies/{strategy_id}/walk-forward
```

The route still delegates to `services.run_walk_forward(...)`. The service function becomes a dispatcher:

1. Load `row = await repo.get_strategy_row(session, strategy_id)`.
2. If missing, raise `404 NOT_FOUND`.
3. If `_is_combo(row.template_key)` is true, call `_run_combo_walk_forward(...)`.
4. Otherwise call the existing atomic Walk-forward path.

Refactor the current atomic code into `_run_atomic_walk_forward(...)` or keep it inline after the combo branch. The important part is to remove the `WF_COMBO_UNSUPPORTED` rejection and keep the existing atomic behavior byte-for-byte where possible.

Add `_run_combo_walk_forward` in `services/quant/app/agent/services.py`:

```py
@dataclass(frozen=True)
class WalkForwardRunContext:
    inst_id: str
    bar: str
    start_ts: int
    end_ts: int
    window_bars: int
    step_bars: int
    reset_capital: bool
    initial_capital: float | None = None
    fees: float | None = None
    slippage: float | None = None
    leverage: float | None = None
    max_bars: int | None = None


async def _run_combo_walk_forward(
    session: AsyncSession,
    rest,
    row: Strategy,
    ctx: WalkForwardRunContext,
) -> dict:
    ...
```

The public `run_walk_forward(...)` signature stays unchanged for the route, but private helpers must not add new long parameter lists. `run_walk_forward` should construct `WalkForwardRunContext` once and pass it into the atomic or combo branch.

Implementation sequence:

1. `members_spec, policy_spec = _combo_spec_from_row(row)`.
2. `notes: list[str] = []`.
3. `validation_members = _build_members(members_spec, notes)`. If members are invalid, the existing `COMBO_MEMBERS` error should propagate before any parent run is created.
4. `policy = _policy_from_spec(policy_spec)`.
5. `icap, fee, slip, lev = _resolve_costs(get_settings(), ctx.initial_capital, ctx.fees, ctx.slippage, ctx.leverage)`.
6. Load base candles with `load_range_candles(rest, inst_id=ctx.inst_id, bar=ctx.bar, start_ts=ctx.start_ts, end_ts=ctx.end_ts, max_bars=ctx.max_bars)` and upsert them.
7. Load member higher-timeframe frames with `_load_member_htf(session, rest, validation_members, inst_id=ctx.inst_id, bar=ctx.bar, start_ts=ctx.start_ts, end_ts=ctx.end_ts)`.
8. Build `plan = plan_walk_forward_windows(len(candles), ctx.window_bars, ctx.step_bars)`.
9. Preserve existing errors: no plan -> `WF_RANGE_TOO_SHORT`; too many windows -> `WF_TOO_MANY_WINDOWS`.
10. Create parent run with `mode="walkforward"` and config containing all existing Walk-forward keys plus combo metadata:

```py
config={
    "kind": "walkforward",
    "subjectKind": "combo",
    "combo": True,
    "members": members_spec,
    "policy": policy_spec,
    "windowBars": ctx.window_bars,
    "stepBars": ctx.step_bars,
    "resetCapital": ctx.reset_capital,
    "initialCapital": icap,
    "fees": fee,
    "slippage": slip,
    "leverage": lev,
    "startTs": ctx.start_ts,
    "endTs": ctx.end_ts,
    "effectiveCandleCount": len(candles),
}
```

11. Run windows with `_run_combo_wf_windows(...)`, passing `members_spec`, `policy_spec`, the validated warmup estimate, the candle frames, and the context object. Do not pass reusable `ComboMember` instances into the window loop.
12. Compute `uncovered = max(0, len(candles) - plan[-1][1])`.
13. Add the existing single-window note if `len(plan) == 1 and len(candles) < ctx.window_bars`.
14. Combine notes from validation and window results with `list(dict.fromkeys(...))`.
15. `summary = summarize_windows(windows, uncovered_bars=uncovered, notes=all_notes)`.
16. Persist with `repo.persist_walk_forward_result(session, parent, summary=summary, windows=windows, start_ts=int(candles[0].ts), end_ts=int(candles[-1].ts))`.
17. Return `{"runId": parent.id, "summary": summary, "windows": windows}`.

Add `_run_combo_wf_windows` next to `_run_wf_windows`:

```py
async def _run_combo_wf_windows(
    members_spec: list[dict],
    policy_spec: dict,
    candles: list[Candle],
    plan: list[tuple[int, int]],
    ctx: WalkForwardRunContext,
    costs: tuple[float, float, float, float],
    htf_by_member: dict,
    warmup: int,
) -> tuple[list[dict], list[str]]:
    windows = []
    notes = []
    icap, fee, slip, lev = costs
    rolling = icap
    short_flagged = False
    for i0, i1 in plan:
        sub = candles[i0:i1]
        cap = icap if ctx.reset_capital else rolling
        window_htf = _slice_member_htf_for_window(
            htf_by_member, int(sub[0].ts), int(sub[-1].ts)
        )
        window_notes: list[str] = []
        members = _build_members(members_spec, window_notes)
        policy = _policy_from_spec(policy_spec)
        result = await run_in_threadpool(
            run_combo_backtest,
            members,
            sub,
            ctx.bar,
            initial_capital=cap,
            taker_fee_bps=fee,
            slippage_bps=slip,
            leverage=lev,
            htf_frames_by_member=window_htf,
            policy=policy,
        )
        notes.extend(window_notes)
        ...
```

Each window rebuilds `members` and `policy` from the saved specs before calling `run_combo_backtest`. This intentionally spends a small amount of CPU to preserve Walk-forward window independence if any current or future strategy object gains mutable runtime state. For each window, append:

```py
{
    "startTs": int(sub[0].ts),
    "endTs": int(sub[-1].ts),
    "metrics": result.metrics,
    "tradeCount": result.metrics["tradeCount"],
}
```

Do not persist per-window trades or equity points. That matches atomic Walk-forward semantics: windows are independent robustness samples, not one continuous run. If `ctx.resetCapital=False`, update `rolling` from `result.equity_points[-1].equity` just like atomic Walk-forward. Add result notes into the returned notes list, de-duplicated later. Use a combo warmup estimate for the short-window warning. Prefer a small service-local helper or a new public helper from `engine.combo` that accounts for member strategy warmup, combo policy lookback, and regime warmup defaults; do not import the private `_warmup_floor`.

Add `_slice_member_htf_for_window(htf_by_member, start_ts, end_ts)` as a small pure helper. It should keep all high-timeframe candles with `c.ts <= end_ts`; it does not need to drop candles before `start_ts`, because pre-window history is useful for indicator warmup and merge-as-of remains causal. It must never include rows after `end_ts`, which makes the no-future-leakage guarantee obvious during review.

### Sequence of operations

Regular tab simple backtest:

1. User opens right panel -> `StrategyPanel` queries `/strategies`.
2. `strategyListTab === "regular"` -> `visibleStrategiesForStrategyPanel` returns atomic strategies.
3. Selection effect picks the current atomic id or first atomic id.
4. `backtestKind === "simple"` -> existing automatic `backtest.mutate` runs.
5. Metrics/trades/history render exactly as today.

Combo tab simple backtest:

1. User clicks “组合策略” tab.
2. Store sets `strategyListTab = "combo"` and persists it.
3. Visible list becomes saved user combos only.
4. Selection effect chooses first visible combo or `null`.
5. If the visible combo list is empty, render only the compact empty state and clear/hide stale result state.
6. Simple mode renders `ComboStrategyDetail` and automatic backtest runs through existing `run_single_backtest` combo branch.
7. Metrics tab renders `ComboResult` when `data.combo` exists.

Combo tab advanced backtest:

1. User selects a saved combo and switches `BacktestModeSwitch` to “高级回测”.
2. Automatic backtest stops because `backtestKind !== "simple"`.
3. `ComboStrategyDetail` renders `WalkForwardForm`.
4. User clicks “运行高级回测”.
5. Frontend calls `POST /api/py/strategies/{comboId}/walk-forward`.
6. Backend detects the strategy row is combo, builds members and policy from saved `params_json`, loads the real candle range and member HTF frames, runs each Walk-forward window through `run_combo_backtest`, persists a parent `strategy_runs` row with `mode="walkforward"`, and returns the normal `WalkForwardResult`.
7. `WalkForwardResults` renders the same summary cards, chart, notes, and window table used for regular strategies.

## File / Module Change Plan

| File | Create / Modify | Intent |
|---|---|---|
| `docs/plans/strategy-tabs-combo-walkforward/spec.md` | Create | This design document. |
| `apps/web/lib/types.ts` | Modify | Add `StrategyListTab = "regular" \| "combo"`; optionally document that Walk-forward now accepts combo strategies through the same result shape. |
| `apps/web/lib/strategyCatalog.ts` | Create | Pure strategy filtering and selection helpers for regular/combo tabs, paper mode, and empty states. |
| `apps/web/lib/strategyCatalog.test.ts` | Create | Unit tests for filtering atomic strategies, hiding builtin combo template, selecting saved combos, paper fallback, and empty combo list behavior. |
| `apps/web/lib/store.ts` | Modify | Add persisted `strategyListTab`, setter, partialize entry, version `15`, migration default, and legacy `mode:"combo"` -> `{ mode:"backtest", strategyListTab:"combo" }` preservation. |
| `apps/web/lib/store.test.ts` | Modify | Assert `strategyListTab` persists, defaults to `"regular"`, and migrates from version `14`. |
| `apps/web/components/Strategy/StrategyPanel.tsx` | Modify | Render left-aligned category tabs above `StrategyList`, use helper-driven visible list and selection fallback, clear stale result state when no visible strategy is selected, remove combo forced-simple logic, render BacktestModeSwitch for combos, and wire combo Walk-forward mutation. |
| `apps/web/components/Strategy/StrategyList.tsx` | Modify | Optional: add `aria-pressed={active}` to strategy row buttons and keep visual output unchanged; no filtering logic should live here. |
| `apps/web/components/Strategy/ComboStrategyDetail.tsx` | Modify | Accept `backtestKind` and `onRunWalkForward`, render `WalkForwardForm` for advanced mode, hide the simple run button in advanced mode, keep view/edit/delete actions. |
| `apps/web/components/Strategy/ComboStrategyDetail.test.tsx` | Modify | Update simple callback tests and add an advanced-mode test that clicks “运行高级回测” and calls `onRunWalkForward(combo)`. |
| `apps/web/components/Strategy/BacktestModeSwitch.tsx` | Modify | Update comments only if needed; behavior stays reusable for regular and combo selections. |
| `services/quant/app/agent/services.py` | Modify | Dispatch `run_walk_forward` by strategy kind, remove `WF_COMBO_UNSUPPORTED`, add context-object based combo Walk-forward helpers, rebuild combo members per window, preserve atomic behavior and validation errors. |
| `services/quant/app/schemas.py` | Modify | Update `WalkForwardReq` docstring and `walk_forward_out` comments to state saved combo ids are supported; response shape remains unchanged. |
| `services/quant/app/api/strategies.py` | Modify | Comment-only update for `/walk-forward`; route signature and delegate call stay unchanged. |
| `services/quant/tests/test_walk_forward.py` | Modify | Replace combo rejection tests with service/API success tests for saved combo Walk-forward, including config metadata and history restore. |
| `services/quant/tests/test_combo_api.py` | Modify | Optional targeted API test if the combo Walk-forward case is clearer near existing combo CRUD tests. Do not duplicate broad coverage if `test_walk_forward.py` already covers it. |

## Interface Design

### Frontend component interface

`ComboStrategyDetail` becomes:

```ts
export default function ComboStrategyDetail(props: {
  combo: Strategy;
  backtestKind?: BacktestKind;
  busy: boolean;
  onRunBacktest: (combo: Strategy) => void;
  onRunWalkForward?: (combo: Strategy) => void;
  onView: (combo: Strategy) => void;
  onEdit: (combo: Strategy) => void;
  onDelete: (combo: Strategy) => void;
})
```

`StrategyPanel` consumes these store fields:

```ts
const {
  strategyListTab,
  setStrategyListTab,
  backtestKind,
  ...
} = useAppStore();
```

The category tabs are UI-only and do not change backend API calls. The labels are exactly:

```ts
"常规策略"
"组合策略"
```

### REST interface

No new REST route is introduced. The existing endpoint expands its accepted strategy id domain:

```http
POST /api/strategies/{strategy_id}/walk-forward
Content-Type: application/json

{
  "instId": "BTC-USDT-SWAP",
  "bar": "1H",
  "startTs": 1700000000,
  "endTs": 1708640000,
  "windowBars": 1000,
  "stepBars": 200,
  "resetCapital": true,
  "initialCapital": 10000,
  "fees": 5,
  "slippage": 2,
  "leverage": 1,
  "maxBars": 200000
}
```

For an atomic strategy id, behavior is unchanged. For a saved combo strategy id (`kind === "combo"` and user row id), the response is the same shape:

```json
{
  "runId": 123,
  "summary": {
    "windowCount": 8,
    "profitableWindows": 5,
    "profitableRate": 0.625,
    "medianReturn": 0.0123,
    "avgReturn": 0.0101,
    "worstReturn": -0.044,
    "bestReturn": 0.071,
    "avgMaxDrawdown": -0.031,
    "stabilityScore": 0.66,
    "uncoveredBars": 80,
    "notes": []
  },
  "windows": [
    {
      "startTs": 1700000000,
      "endTs": 1703596400,
      "metrics": {
        "totalReturn": 0.02,
        "winRate": 0.5,
        "profitFactor": 1.2,
        "maxDrawdown": -0.03,
        "sharpe": 0.8,
        "tradeCount": 12,
        "avgPnl": 4.2
      },
      "tradeCount": 12
    }
  ]
}
```

Deprecated behavior: `WF_COMBO_UNSUPPORTED` should no longer be emitted for a valid saved combo. Keep the error code only in historical notes or remove related tests; do not preserve dead branches that make the new requirement fail.

### WebSocket / CLI interface

No WebSocket or CLI changes are required. Paper trading remains atomic-only; compare mode remains handled by `CompareView`.

## Data Model

No database migration is required.

Existing backend data remains authoritative:

```py
Strategy:
  id: int
  template_key: str
  name: str
  params_json: str
  enabled: bool
  builtin: bool
```

The strategy `kind` is still derived from `STRATEGY_REGISTRY[template_key].meta.kind` in `repo.merge_strategy`. The builtin `regime_rotation` template remains a combo template but should not appear in the “组合策略” tab because that tab represents user-created saved combinations.

Saved combo strategy params continue to use the current shape:

```json
{
  "members": [
    { "templateKey": "trend_pullback_long", "params": {}, "name": "顺势回踩做多" },
    { "templateKey": "range_scalping_strategy", "params": {}, "name": "区间剥头皮" }
  ],
  "rebalanceBars": 24,
  "lookbackBars": 168,
  "selectionMetric": "sharpe",
  "minDwellBars": 24,
  "switchMarginPct": 0.1,
  "regimeGating": true,
  "turnoverBps": 5
}
```

Walk-forward parent runs continue to use:

```py
strategy_runs.mode = "walkforward"
strategy_runs.strategy_id = selected strategy id
strategy_runs.config_json = json.dumps(config, ensure_ascii=False)
```

For combo Walk-forward, `config_json` adds combo metadata:

```json
{
  "kind": "walkforward",
  "subjectKind": "combo",
  "combo": true,
  "members": [],
  "policy": {},
  "windowBars": 1000,
  "stepBars": 200,
  "resetCapital": true,
  "initialCapital": 10000,
  "fees": 5,
  "slippage": 2,
  "leverage": 1,
  "startTs": 1700000000,
  "endTs": 1708640000,
  "effectiveCandleCount": 2500,
  "summary": {},
  "windows": []
}
```

Frontend persisted state gains one field:

```ts
interface AppState {
  strategyListTab: StrategyListTab;
  setStrategyListTab: (tab: StrategyListTab) => void;
}
```

This field is safe to persist because it is a display preference, not a runtime handle. It should not reset `runId` merely because the tab changes when a visible strategy is immediately resolved. If the resolved strategy id is `null`, the panel must clear `runId`, ordinary metrics state, combo result state, coverage state, and `walkForwardResult` so an empty tab cannot display stale run output.

## Testing & Acceptance Criteria

### Backend tests

Update `services/quant/tests/test_walk_forward.py`.

Replace `test_run_walk_forward_rejects_combo` with `test_run_walk_forward_supports_combo`. The test should:

1. Initialize DB.
2. Pick or create a saved combo strategy with at least two valid atomic members.
3. Call `services.run_walk_forward(...)` with the combo id and fake paged candles.
4. Assert `out["runId"]` exists.
5. Assert `out["summary"]["windowCount"] > 0`.
6. Assert each window has `startTs`, `endTs`, `metrics`, and `tradeCount`.
7. Load the parent run and assert `run.mode == "walkforward"`, `run.strategy_id == combo_id`, `config_json["combo"] is True`, `config_json["subjectKind"] == "combo"`, and `config_json["members"]` is present.

Replace `test_walk_forward_api_rejects_combo` with `test_walk_forward_api_accepts_combo`. The test should:

1. Use `ASGITransport`.
2. Create a user combo through `POST /api/strategies` or use a valid saved combo row.
3. Call `POST /api/strategies/{combo_id}/walk-forward`.
4. Assert HTTP `200`.
5. Assert response shape matches the existing atomic Walk-forward contract.
6. Assert `GET /api/runs/{runId}/walk-forward` returns the same `summary.windowCount`.

Keep all existing validation tests for `startTs >= endTs`, `stepBars > windowBars`, `windowBars < 100`, and `WF_TOO_MANY_WINDOWS`. These validation rules apply equally to atomic and combo strategies.

### Frontend tests

Create `apps/web/lib/strategyCatalog.test.ts`:

1. `splitStrategyCatalog` returns atomic strategies under `regular`.
2. It returns only non-builtin combo rows under `combos`.
3. `visibleStrategiesForStrategyPanel` returns regular strategies for paper mode even when `strategyListTab === "combo"`.
4. `resolveVisibleStrategyId` preserves an id that remains visible.
5. It falls back to the first combo when switching to combo tab.
6. It returns `null` when combo tab has no saved combos.

Update `apps/web/lib/store.test.ts`:

1. `setStrategyListTab("combo")` updates state.
2. Persisted `aragon-ui` includes `strategyListTab`.
3. Version `14` migration gets `strategyListTab: "regular"`.
4. Legacy version `13` payloads with `mode: "combo"` migrate to `mode: "backtest"` and `strategyListTab: "combo"`.

Update `apps/web/components/Strategy/ComboStrategyDetail.test.tsx`:

1. Existing simple-mode tests still pass after adding `backtestKind="simple"`.
2. Add a test that renders with `backtestKind="walkforward"` and `onRunWalkForward`.
3. Assert the simple “运行回测” button is absent in advanced mode.
4. Click “运行高级回测” and assert `onRunWalkForward` receives the combo.
5. Assert “查看详情”, “编辑”, and “删除” remain visible in advanced mode.

If adding a `StrategyPanel` component test is practical with the current TanStack Query setup, add one focused test with mocked `api.strategies` to assert the two labels “常规策略” and “组合策略” render above the list and that clicking “组合策略” hides atomic names and shows the saved combo name. Add a second focused case, or extend the same test, where no saved combos exist: the empty state appears, the detail panel is absent, and prior metrics / Walk-forward result output is not rendered. If this requires excessive provider mocking, keep the pure helper tests and make the empty-state stale-output check a mandatory manual acceptance item.

### Manual acceptance

1. Start the app with `pnpm dev`.
2. Open `http://localhost:18761`.
3. Switch the right panel to “交易策略”.
4. In 回测 mode, verify the list area top shows left-aligned “常规策略 / 组合策略” tabs.
5. On “常规策略”, verify only atomic strategies are listed and combo badges are absent.
6. On “组合策略”, verify only user-created saved combos are listed. The builtin combo template must not be listed as a user combo.
7. If no saved combo exists, verify the empty state appears, the detail panel is absent, and no previous metrics / Walk-forward result remains visible; then click “新建组合”, save a combo, return to the tab, and verify it appears.
8. Select a saved combo, keep “普通回测”, verify simple combo backtest still renders combo metrics and timeline.
9. Switch to “高级回测”, verify the Walk-forward form appears for the combo.
10. Run advanced backtest and verify `WalkForwardResults` shows summary cards, chart, and window table.
11. Open run history and verify the generated run is mode `walkforward` and can be restored through `/runs/{id}/walk-forward`.
12. Switch to 实时 / paper mode and verify no combo strategy is selectable there.

The feature is accepted only if `pnpm test` passes, backend `pytest` passes for non-network tests, and manual checks show no overlap or layout clipping in the right panel at widths from 320px to 640px.

## Risks & Mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| Persisted selected strategy id points to an item hidden by the new tab. | Detail panel or results could show the wrong category, a stale combo, or stale metrics. | Use `resolveVisibleStrategyId` whenever `mode`, `strategyListTab`, or `strategies` changes. Set `strategyId` to first visible id or `null`; when it resolves to `null`, clear `runId`, metrics, combo result, coverage, and Walk-forward result state. |
| Builtin `regime_rotation` template appears in the user combo tab. | User sees an implementation template rather than a saved strategy. | Filter combo tab with `isUserCombo`, not `kind === "combo"`. Cover with `strategyCatalog.test.ts`. |
| Combo advanced backtest accidentally leaks future HTF data into a window. | Backtest results become invalid. | Slice member HTF frames to `ts <= window_end` before each window and rely on causal merge behavior. Add comments in `_slice_member_htf_for_window`. |
| Combo Walk-forward becomes too slow because each window runs every member. | UI appears stuck and backend load increases. | Keep explicit click-only advanced execution, reuse existing `walk_forward_max_windows`, and surface `WF_TOO_MANY_WINDOWS`. Do not auto-run advanced mode. |
| Reusing `members` instances across windows leaks strategy object state. | Window independence is compromised if a strategy later stores mutable state. | Pass `members_spec` and `policy_spec` into `_run_combo_wf_windows`; call `_build_members` and `_policy_from_spec` inside each window before `run_combo_backtest`. |
| `resetCapital=False` semantics are less intuitive for combo windows. | Rolling capital affects later windows and can confuse stability interpretation. | Match atomic Walk-forward semantics exactly: previous window final equity becomes next window initial capital. Keep the form hint unchanged. |
| Result shape omits combo timeline per window. | Users cannot inspect per-window rotation details from advanced results. | Preserve the stable Walk-forward result shape for this feature. Add a later enhancement for optional per-window combo drilldown only if needed. |
| Empty combo tab looks broken. | Users may not know how to create a combo. | Add a compact empty state with a “新建组合” action wired to `openNewCombo`. |
| Store version migration breaks older persisted state. | App may hydrate with missing fields or lose legacy combo-mode intent. | Bump to version `15`, add explicit migration, test version `14 -> 15`, and test legacy version `13` `mode:"combo"` -> `mode:"backtest", strategyListTab:"combo"`. |

## 评审结论

通过。P1 问题已在正文中修正：空组合页签不再允许旧结果继续显示，后端新增 helper 不再引入超长参数列表，组合 Walk-forward 每个窗口必须重建成员以保持窗口独立性。剩余 P2 均已转化为迁移和可访问性实现约束，不阻塞后续代码开发。
