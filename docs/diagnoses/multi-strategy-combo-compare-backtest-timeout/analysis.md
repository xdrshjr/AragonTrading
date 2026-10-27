# Multi-strategy Combo Compare Backtest Timeout

## Problem description

用户反馈：运行回测时速度慢且容易卡顿；当同时选择很多策略，尤其混入多个组合策略后，再执行回测/对比时，界面会显示 `Server error` 或表现为回测失败。期望是回测更稳定、流畅，不影响主界面，也不造成卡顿。

本次节点只做定位分析，不修改业务代码。

## Reproduction steps

### Baseline: existing test only covers a small case

```powershell
services/quant/.venv/Scripts/python -m pytest services/quant/tests/test_combo_api.py::test_combo_in_compare -q
```

Observed:

```text
1 passed in 7.32s
```

This confirms the current regression test covers only `1 combo + 1 atomic` and does not cover the high-selection scenario reported by the user.

### Repro A: valid 12-combo request is already slow

Run from `services/quant`:

```powershell
@'
import asyncio, os, tempfile, time, uuid
from pathlib import Path

os.environ["DB_PATH"] = str(Path(tempfile.gettempdir()) / f"aragon_repro_{uuid.uuid4().hex}.db")
os.environ["ALLOW_CACHED_FALLBACK"] = "false"

from httpx import ASGITransport, AsyncClient
from app.db.session import dispose_engine, init_db
from app.main import app
from app.okx.types import Candle

class FakeRest:
    def __init__(self, total=1200, start_ts=1_700_000_000, step=3600):
        self.calls = []
        self.rows = []
        price = 100.0
        for i in range(total):
            import math
            drift = math.sin(i / 8.0) * 5.0
            o = price
            c = 100.0 + drift + i * 0.05
            h = max(o, c) + 1.5
            l = min(o, c) - 1.5
            vol = 1000 + (i % 10) * 50
            self.rows.append(Candle(ts=start_ts + i * step, o=round(o, 2), h=round(h, 2), l=round(l, 2), c=round(c, 2), vol=float(vol)))
            price = c
    async def health(self): return True
    async def get_candles(self, inst_id, bar, limit=300, before=None, after=None):
        self.calls.append({"bar": bar, "limit": limit, "before": before, "after": after})
        rows = [r for r in self.rows if before is None or r.ts < before]
        return list(reversed(rows[-min(limit, len(rows)):]))

async def main():
    import app.api.strategies as strategy_api
    fake = FakeRest(total=1200)
    strategy_api.get_rest_client = lambda: fake
    await init_db()
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    member_keys = [
        "long_shadow_reversal", "momentum_breakout", "ema_cross_scalper",
        "bollinger_reversion", "vwap_rsi_momentum", "trend_pullback_long",
        "trend_pullback_short", "range_scalping_strategy",
    ]
    start_ts = 1_700_000_000
    end_ts = start_ts + 1199 * 3600
    async with AsyncClient(transport=transport, base_url="http://test", timeout=120) as client:
        ids = []
        for n in range(12):
            r = await client.post("/api/strategies", json={
                "templateKey": "regime_rotation",
                "name": f"repro-combo-{n}",
                "params": {"members": [{"templateKey": k, "params": {}} for k in member_keys]},
            })
            ids.append(r.json()["id"])
        t0 = time.perf_counter()
        r = await client.post("/api/strategies/compare", json={
            "strategyIds": ids,
            "instId": "BTC-USDT-SWAP",
            "bar": "1H",
            "limit": 2000,
            "startTs": start_ts,
            "endTs": end_ts,
        })
        print("status", r.status_code)
        print("elapsed_sec", round(time.perf_counter() - t0, 3))
        print("rest_calls", len(fake.calls))
        if r.status_code == 200:
            body = r.json()
            print("items", len(body["items"]))
            print("equity_lengths_sample", [len(it["equity"]) for it in body["items"][:3]])
    await dispose_engine()

asyncio.run(main())
'@ | .venv/Scripts/python -
```

Observed in this workspace:

```text
status 200
elapsed_sec 23.458
rest_calls 2
items 12
equity_lengths_sample [1200, 1200, 1200]
command timed out after 31078 milliseconds
```

The FastAPI handler completed, but the surrounding command exceeded a 30s interaction window after JSON parsing/log flushing/cleanup. With the production safety ceiling of `BACKTEST_MAX_BARS=20000`, this synchronous request shape is large enough to become a visible timeout or apparent server failure.

### Repro B: selecting more than 12 is possible in UI but rejected by backend

The frontend checkbox list allows more than 12 selections. A 13-strategy request returns:

```text
selected_count 13
status 422
body {"detail":[{"type":"too_long","loc":["body","strategyIds"],"msg":"List should have at most 12 items after validation, not 13", ... }]}
```

The response is FastAPI's default validation body, not the app's `{error:{code,message}}` shape, so `apps/web/lib/api.ts` falls back to the HTTP status text rather than a product-specific message.

### Repro C: combo compare repeats HTF loads per combo

With 4 saved combos at `15m`, each containing members that need `1H` higher-timeframe data:

```text
status 200
elapsed_sec 2.151
calls_by_bar {'15m': 1, '1H': 8}
```

Expected for a compare run would be one shared base fetch plus a shared HTF union. Instead, the combo branch reloads HTF frames per combo. The two `1H` calls per combo are from paging/warmup in `load_htf_frames`.

## Root cause analysis

Primary root cause: `/api/strategies/compare` is a synchronous request whose work scales as:

```text
atomic_count * backtest_cost
+ combo_count * member_count_per_combo * backtest_cost
+ per-run DB persistence of all trades and all equity points
+ response serialization of all equity points for every selected item
```

The route does not have a background job/progress model, no server-side cancellation is wired for UI calls, and no response downsampling or paging is used.

Evidence:

- `services/quant/app/api/strategies.py:117-123` exposes compare as one synchronous POST handler, returning only after `services.run_compare(...)` completes.
- `services/quant/app/agent/services.py:270-294` is the executed service path: it loads base candles once, prepares non-combo strategies, runs them serially, then loops over selected combo IDs and calls `_compare_combo_item(...)` one combo at a time.
- `services/quant/app/agent/services.py:363-384` loads member HTF frames inside `_run_combo(...)` and persists the result for each combo item.
- `services/quant/app/engine/combo.py:91-105` loops over every combo member and calls `run_backtest(...)` independently for each member.
- `services/quant/app/db/repositories.py:146-160` inserts every trade and every equity point row one by one, then commits once per run.
- `services/quant/app/agent/services.py:312` and `services/quant/app/agent/services.py:443` return full `equity` arrays for every compare item.
- `services/quant/app/agent/services.py:274-320` has an optional `cancel_check`, but `services/quant/app/api/strategies.py:120-123` does not pass one. A browser-side cancel/navigation therefore does not stop the backend loop.
- `services/quant/app/agent/services.py:175` and `services/quant/app/agent/services.py:380` already offload CPU backtests to `run_in_threadpool`; the latency problem is the accumulated serial request work plus DB/JSON volume, not a single missing event-loop offload.
- `services/quant/app/db/session.py:35` and `services/quant/app/db/session.py:45` configure SQLite write wait limits at 5 seconds. Any candidate that adds parallel result persistence must avoid new write-lock contention.

Secondary root cause: frontend selection limits do not match backend request validation.

Evidence:

- `services/quant/app/schemas.py:46` defines `CompareReq`.
- `services/quant/app/schemas.py:48` enforces `strategyIds` length `2..12`.
- `apps/web/components/Strategy/CompareView.tsx:32-43` defaults to built-in strategies and submits `Array.from(selected)` directly.
- `apps/web/components/Strategy/CompareView.tsx:93-118` renders every strategy as a selectable checkbox and disables the run button only when fewer than 2 are selected or the request is pending; it does not disable or warn at more than 12.
- `services/quant/app/strategies/__init__.py:22-31` includes the built-in combo template in the built-in order.
- `services/quant/app/main.py:51-70` registers handlers for `OKXError` and Starlette `HTTPException`, but not `RequestValidationError`; Pydantic request-body failures keep FastAPI's default `{"detail":[...]}` shape.
- `apps/web/lib/api.ts:48-56` only extracts `body.error.code/message`, so default 422 bodies fall back to `res.statusText`.

This means users can create saved combos, select many rows, and send invalid requests. When the request is valid but combo-heavy, the backend still performs enough synchronous CPU/DB/JSON work to freeze the workflow or hit timeouts.

### Reviewer audit notes

- The root-cause path is executed for the reported UI flow: `CompareView` calls `api.compare(...)`, `apps/web/lib/api.ts` sends `/api/py/strategies/compare`, `apps/web/next.config.mjs:10-16` rewrites that path to the FastAPI `/api/strategies/compare` route, and the route awaits `run_compare(...)`.
- The 13-selection symptom is not a backend crash. It is a validation mismatch plus error-shape mismatch. This explains a product-visible failure message, but it does not explain valid 12-item combo timeouts by itself.
- Repro A returning `200` in 23.458s is a counter-example to "every large combo compare throws". The stronger conclusion is that the current request shape has no predictable latency bound: it can succeed slowly, exceed the caller/proxy interaction window, or surface as a generic server error depending on data size and environment.
- The HTF evidence is specific to combo compare. Atomic compare already loads an HTF union once in `run_compare`; selected saved combos are skipped by `_prepare_compare(...)` and then handled by `_compare_combo_item(...)`, which re-enters `_run_combo(...)` per combo.
- Adding CPU concurrency is not proven as the minimum safe fix. Since CPU work already runs in a threadpool and SQLite writes have a 5s busy timeout, concurrency could trade latency for CPU saturation or write-lock failures unless persistence remains serialized/batched.

## Impact surface

- Multi-strategy compare mode in the right Strategy panel.
- Saved combo strategies using `regime_rotation`.
- Lower timeframes such as `15m`, where member HTF loading is repeated per combo.
- Long date ranges, especially near the `backtest_max_bars` ceiling.
- Desktop and browser users both see the same API behavior because the web client calls `/api/py/strategies/compare`, which rewrites to the FastAPI backend.
- Main UI responsiveness is at risk after the response arrives because the client receives and parses large JSON payloads containing every equity point for every selected strategy.

## Candidate fixes

| Fix | Summary | Invasiveness | Risk | Effort |
|---|---|---:|---:|---:|
| A | Add frontend compare guards: max 12 selections, clearer 422/validation messages, and disable extra checkboxes once 12 are selected. | Low | Low | Low |
| B | Optimize synchronous compare: pre-resolve all selected combos, share HTF union across combo members, run CPU backtests with bounded concurrency, bulk insert trades/equity, and downsample compare equity in the response. | Medium | Medium | Medium |
| C | Convert compare/backtest to background jobs with progress, cancellation, and result polling; return `runGroupId` immediately. | High | Medium-High | High |

Candidate audit:

- A is necessary but insufficient. It covers the invalid >12 request and improves the displayed message, but a valid <=12 combo-heavy request still does too much work in one response.
- B covers the observed valid-request bottlenecks only if it is narrowed. Shared combo HTF loading, response downsampling, batched DB writes, and a server-side workload guard directly target the evidence. Bounded CPU concurrency is optional and should not be part of the first fix unless a test demonstrates it does not create SQLite/threadpool contention.
- C is the most robust long-term architecture for arbitrarily long runs, but it is not the minimum fix for this bug. It changes the API contract and UI state model, so it belongs after the synchronous path is bounded.

## Recommended fix

Recommend shipping one bounded synchronous compare fix: A plus the safe subset of B.

Specifically:

1. Align the UI with `CompareReq`: cap selection at 12, show `已达 12 个策略上限`, and normalize 422 validation errors into a readable message.
2. In `run_compare`, resolve combo rows before execution and load the union of member HTF frames once per `(instId, bar, startTs, endTs)` instead of inside each `_run_combo`.
3. Keep compare synchronous for now and keep strategy execution serialized until there is a dedicated concurrency test. Batch DB inserts for trades/equity so persistence cost no longer scales as thousands of ORM `add(...)` calls per run.
4. Return downsampled equity for compare overlays while keeping full equity available via `/runs/{id}/equity` when a user opens one strategy detail.
5. Add a backend workload guard based on expanded work, not just selected row count: reject early with a structured 400/413 when `(atomic_count + sum(combo_member_count)) * effective_bar_count` exceeds the synchronous compare budget.

This directly addresses the reproduced failure modes with lower blast radius than a full job system. A background job model is still the right long-term architecture for very long, multi-combo backtests, but it should follow after the current synchronous path is made bounded and predictable.

## 璇勫缁撹 (Review Verdict)

Final recommended fix: implement a bounded synchronous compare path. This is one coherent fix made of UI alignment plus backend bounding/optimization; do not implement the full background-job architecture in the next node.

Required changes for that fix:

1. Frontend: enforce the backend's 12-strategy cap in `CompareView`, disable extra unchecked boxes at the cap, show a local max-selection message, and normalize validation errors so FastAPI 422 bodies do not surface as a generic server error.
2. Backend validation/errors: add a structured `RequestValidationError` handler or equivalent client parsing so request-body validation failures use the same readable error shape as app `HTTPException`s.
3. Backend compare execution: pre-resolve combo rows and members in `run_compare`, compute one shared HTF union for all selected combo members, and pass the per-member HTF slices into combo execution instead of calling `_load_member_htf(...)` once per combo.
4. Backend bounds: add an explicit synchronous workload budget using expanded member count times effective bar count. If the request exceeds that budget, fail fast with a clear product error rather than letting the request run until an external timeout.
5. Backend payload/persistence: batch insert trades/equity and return downsampled compare equity; keep full persisted equity available through `/runs/{id}/equity` for detail view.

Justification: A alone fixes only invalid requests. The narrowed B fixes the proven valid-request hot path: repeated combo HTF loads, per-row persistence overhead, and full-equity JSON payloads. Leaving CPU concurrency out of the first fix is intentional because the current code already uses `run_in_threadpool`, while SQLite has short write-lock waits; parallelizing execution before batching/guarding persistence could introduce new lock or CPU-starvation regressions. C remains the right later architecture for very long backtests, but it is extra work beyond the minimum stable fix for this task.
