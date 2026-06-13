# Backtest Time Range and Chart Alignment Diagnosis

## Problem description

The reported symptom is that backtests should run over a user-selected time range and strategy parameters, without seeing candles after the current simulated candle. After the run, the equity line and trade markers shown on the K-line chart should correspond to the same backtested candle window. The current behavior appears incorrect: the displayed post-backtest line does not reliably match the visible K-line trend or the concrete candle values.

## Reproduction steps

### UI reproduction

1. Start the app from the repository root:

```powershell
pnpm dev
```

2. Open `http://localhost:18761`.
3. In the top chart toolbar, use the date input to jump to an older date. This only scrolls/loads candles; it is not stored as a backtest range.
4. In backtest mode, choose any strategy and click the run-backtest button.
5. Observe that the run uses a fixed recent-window request, not the chart date or any selected date range. The chart continues to display its own independently loaded candle set plus live candles, while the run equity is loaded by `runId`.

### Minimal API reproduction

This copy-pasteable script demonstrates that extra time-range fields are accepted by the route but not propagated into the candle fetch. It uses the existing test fixtures and does not hit the network.

```powershell
$env:PYTHONPATH='services/quant'
$env:PYTHONUTF8='1'
$env:DB_PATH=([System.IO.Path]::GetTempPath() + 'aragon_repro_' + [guid]::NewGuid().ToString('N') + '.db')
@'
import asyncio
from httpx import ASGITransport, AsyncClient
from app.db.session import init_db, dispose_engine
from app.okx.types import Candle, Instrument
from tests.conftest import make_synthetic_candles
import app.api.strategies as strategies_api
import app.api.market as market_api

class FakeRest:
    def __init__(self):
        self.calls = []
    async def health(self):
        return True
    async def get_instruments(self, inst_type='SWAP'):
        return [Instrument(instId='BTC-USDT-SWAP', instType='SWAP', base='BTC', quote='USDT')]
    async def get_candles(self, inst_id, bar, limit=300, before=None, after=None):
        self.calls.append({'inst_id': inst_id, 'bar': bar, 'limit': limit, 'before': before, 'after': after})
        rows = make_synthetic_candles(min(limit, 300), start_ts=1700000000, step=3600)
        return [Candle(ts=r['ts'], o=r['o'], h=r['h'], l=r['l'], c=r['c'], vol=r['vol']) for r in rows]
    async def aclose(self):
        pass

async def main():
    fake = FakeRest()
    strategies_api.get_rest_client = lambda: fake
    market_api.get_rest_client = lambda: fake
    await init_db()
    from app.main import app
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
            strategies = (await client.get('/api/strategies')).json()
            sid = strategies[0]['id']
            payload = {
                'instId': 'BTC-USDT-SWAP',
                'bar': '1H',
                'limit': 200,
                'startTs': 1700500000,
                'endTs': 1700800000,
                'initialCapital': 10000,
            }
            resp = await client.post(f'/api/strategies/{sid}/backtest', json=payload)
            body = resp.json()
            run = (await client.get(f"/api/runs/{body['runId']}")).json()
            equity = (await client.get(f"/api/runs/{body['runId']}/equity")).json()
            print('status=', resp.status_code)
            print('fake_get_candles_calls=', fake.calls)
            print('requested_start_end=', payload['startTs'], payload['endTs'])
            print('run_start_end=', run['startTs'], run['endTs'])
            print('equity_first_last=', equity[0]['ts'], equity[-1]['ts'], 'count=', len(equity))
    finally:
        await dispose_engine()

asyncio.run(main())
'@ | services/quant/.venv/Scripts/python.exe -
```

Observed output:

```text
status= 200
fake_get_candles_calls= [{'inst_id': 'BTC-USDT-SWAP', 'bar': '1H', 'limit': 200, 'before': None, 'after': None}]
requested_start_end= 1700500000 1700800000
run_start_end= 1700000000 1700716400
equity_first_last= 1700000000 1700716400 count= 200
```

The requested time range is ignored. The run range is determined only by the latest candles returned by the data client.

## Root cause analysis

Primary root cause: there is no end-to-end time-range contract for backtests.

Evidence:

- The backend `BacktestReq` schema supports `instId`, `bar`, `limit`, capital, fees, slippage, and leverage, but no `startTs`/`endTs` or equivalent range fields: `services/quant/app/schemas.py:25`.
- Because the FastAPI route binds the request body to `BacktestReq`, any user-supplied `startTs`/`endTs` fields are outside the typed contract before the route logic runs. The reproduction's ignored fields are therefore explained first by the schema gap, then by the route fetch below.
- The frontend backtest API body type also has no time-range fields: `apps/web/lib/api.ts:96`.
- The UI's date input only calls `onJumpDate(ts)`, so it scrolls the chart rather than configuring a backtest range: `apps/web/components/Chart/ChartToolbar.tsx:76`.
- The strategy panel always starts a backtest with `{ instId, bar, limit: 500 }`: `apps/web/components/Strategy/StrategyPanel.tsx:227`.
- The backend route fetches candles as `get_candles(body.instId, body.bar, body.limit)` and never passes `before`/`after`: `services/quant/app/api/strategies.py:87`.
- The same contract hole exists in compare mode: `CompareReq` also only has `instId`, `bar`, `limit`, and execution-cost fields (`services/quant/app/schemas.py:35`), the frontend sends `api.compare({ strategyIds, instId, bar, limit: 500 })` (`apps/web/components/Strategy/CompareView.tsx:40`), and the backend compare route fetches one shared latest-window candle list with `get_candles(body.instId, body.bar, body.limit)` (`services/quant/app/api/strategies.py:128`).

Secondary root cause: the chart candle window and the backtest window are loaded independently.

Evidence:

- The visible chart initially requests the latest 300 candles: `apps/web/app/page.tsx:57`.
- The backtest requests 500 candles from the strategy panel: `apps/web/components/Strategy/StrategyPanel.tsx:227`.
- In non-paper modes, including backtest mode, the page also subscribes to live market candles and merges them into the chart candle series: `apps/web/app/page.tsx:125`.
- The chart renders the loaded equity series solely by equity timestamps and values; it does not validate or constrain the equity points against the current candle array: `apps/web/components/Chart/CandleChart.tsx:173`.
- Opening an old run from history only writes `strategyId`, `runId`, and `mode="backtest"` to the store (`apps/web/components/Strategy/RunHistory.tsx:31`); it does not reload candles for that run's stored `startTs/endTs`. Run serialization exposes the stored `startTs/endTs` (`services/quant/app/schemas.py:81`), but the page does not use those fields to bind the chart window.

This means the equity line may be mathematically correct for the backend run, but it is not guaranteed to be displayed against the same candle range the user selected or is currently viewing. After a run, additional live candles can appear on the chart without corresponding equity points, making the overlay look detached from the K-line data.

Lookahead check:

- The core backtester fills queued orders at the current open, marks current close equity, then calls `strategy.on_candle` and queues signals for the next open: `services/quant/app/engine/backtester.py:63` and `services/quant/app/engine/simulator.py:120`.
- The backtester explicitly skips opening new orders on the last candle because there is no next candle to fill: `services/quant/app/engine/backtester.py:66`.
- Strategy search found no obvious `shift(-1)` future access. For example, momentum breakout derives prior window values with `shift(1)`: `services/quant/app/strategies/momentum_breakout.py:43`.
- Boundary caveat: the engine passes the full prepared DataFrame into `strategy.on_candle(df, i, ctx)` (`services/quant/app/engine/backtester.py:68`), so the no-lookahead property is currently a strategy implementation convention, not a hard engine-enforced isolation boundary. That does not explain the observed fixed-window/chart-mismatch symptom for the built-in strategies reviewed, but it should be covered by tests or an explicit strategy contract when the backtest range work is implemented.

So the stronger evidence points to window/range mismatch, not to the broker loop itself using future candles.

## Impact surface

- Single-strategy backtest runs use the latest `limit` candles instead of the intended user-selected historical period.
- Compare mode has the same problem because it uses the same fixed-window request shape and backend candle fetch pattern.
- Run history reopens stored equity/trades by `runId`, but the chart still loads candles from the current instrument/bar query, not the run's exact candle snapshot.
- Users can misread performance because equity/trades may be visually overlaid on a different chart window than the data used for the run.
- The issue affects trust in all metrics derived from an intended historical range, even if the calculation over the actually fetched candle list is internally consistent.

## Candidate fixes

| Option | Summary | Invasiveness | Risk | Effort |
|---|---|---:|---:|---:|
| A | Add explicit `startTs`/`endTs` to backtest and compare contracts, fetch/filter candles server-side to that closed range, and bind the chart to the run window after completion. | Medium | Medium | Medium |
| B | Minimal alignment only: keep latest-500 backtests but make the chart request the same 500 candles and set the visible range to the run's `startTs/endTs`. | Low | Low | Low |
| C | Persist an immutable candle snapshot per run and reopen runs from that snapshot for exact auditability. | High | Low after implemented | High |

## Recommended fix

Choose Option A.

Justification:

- It directly addresses the user requirement: backtests must run inside the selected period.
- It avoids expanding persistence scope before the product contract is corrected.
- It is safer than Option B because Option B only hides the visible mismatch while still leaving users unable to choose a historical backtest range.

Recommended implementation outline:

1. Extend `BacktestReq` and `CompareReq` with `startTs` and `endTs` in seconds UTC. Require `startTs < endTs`; decide whether `limit` becomes a cap or is derived from the range.
2. Extend `apps/web/lib/api.ts` types and the UI state to pass the selected range into backtest and compare requests. The current date input should not be reused silently as a range; add explicit start/end controls.
3. In `services/quant/app/api/strategies.py`, fetch candles with the existing `OKXRestClient.get_candles(..., before=endTs, after=startTs)` path, then apply a server-side filter `startTs <= c.ts <= endTs` before running the engine. Reject empty ranges.
4. Exclude unconfirmed candles from historical backtests, using `Candle.confirm`, or clamp `endTs` to the latest confirmed candle for the selected bar.
5. Persist `startTs`, `endTs`, and the effective candle count in the run config, alongside the existing `start_ts/end_ts` result fields.
6. After a run completes or history opens a run, render candles for the run's effective range instead of the default latest-300/live-merged chart range. Live market candles should not be merged into a historical run view unless visually separated from the backtest overlay.
7. Add tests:
   - API test proving `startTs/endTs` are passed to the rest client and persisted in run config.
   - Engine or API test proving equity timestamps stay within the effective candle range.
   - Frontend unit test proving `runBacktest` sends the selected range instead of only `limit: 500`.

## 评审结论 (Review Verdict)

Verdict: the diagnosis is sound after the added caveats. The final recommended fix is Option A, with one important constraint: implement the explicit range contract end to end and bind the chart to the run's effective candle range after completion/history open. Do not choose Option B as the primary fix, because it only aligns the chart to the current latest-window implementation and still leaves users unable to run a historical selected period.

The minimum sufficient change is:

1. Add validated `startTs`/`endTs` fields to `BacktestReq`, `CompareReq`, and the frontend API request types.
2. Add explicit range controls/state in the UI and pass those values from single backtest and compare mode.
3. In both backend routes, call `get_candles` with the existing cursor arguments, then server-filter to the closed requested range before `run_backtest`. This filter is required because the OKX wrapper's cursor logic is directional and capped by `limit`, so the API call alone is not a reliable closed-range guarantee.
4. Persist the requested range and effective candle count in run config, and use the run's stored/effective range to reload or constrain chart candles when showing backtest equity/trades. Disable live candle merging for historical backtest display unless the live segment is explicitly separated.
5. Add regression coverage for single backtest, compare mode, and history-open chart binding. Include a no-lookahead contract test for built-in strategies or document/enforce that strategies must only use data at or before `i`.

This recommendation explains the observed symptom because the failing path actually executes the cited fixed-window calls: `StrategyPanel`/`CompareView` send only `limit: 500`, `strategies.py` fetches candles without `before`/`after`, and `page.tsx` independently renders latest-300 plus live candles while overlaying run equity by timestamp only. The broker loop should not be changed as part of this fix unless future tests reveal a concrete strategy-level lookahead failure.
