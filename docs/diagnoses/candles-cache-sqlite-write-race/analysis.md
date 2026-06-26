# Candles Cache SQLite Write Race Diagnosis

## Problem description

`GET /api/candles` intermittently returns HTTP 500 during Aragon runtime. The
reported backend errors are:

```text
UNIQUE constraint failed: candles_cache.inst_id, candles_cache.bar, candles_cache.ts
database is locked
```

These failures affect K-line related requests. The live OKX fetch can succeed,
but the request still fails because the endpoint writes the fetched candles to
SQLite before returning the response.

This diagnosis only localizes the issue and proposes fixes. No business code
was changed.

## Reproduction steps

### Minimal API repro for `/api/candles` 500

Run this from the repository root on Windows PowerShell. It uses a temporary
SQLite database under `tmps/`, replaces OKX with a fake client that returns the
same candles to every request, and sends 20 concurrent `/api/candles` calls.

```powershell
cd services/quant
@'
import asyncio
import os
from pathlib import Path
from types import SimpleNamespace

db_path = Path("../../tmps/candles-api-race-repro.db").resolve()
db_path.parent.mkdir(parents=True, exist_ok=True)
if db_path.exists():
    db_path.unlink()
os.environ["DB_PATH"] = str(db_path)

from httpx import ASGITransport, AsyncClient

from app.config import get_settings
from app.db.session import dispose_engine, init_db
import app.api.market as market

get_settings.cache_clear()

class FakeRest:
    async def get_candles(self, inst_id, bar, limit, before=None, after=None):
        await asyncio.sleep(0.05)
        return [
            SimpleNamespace(
                ts=1710000000 + i * 60,
                o=1.0 + i,
                h=2.0 + i,
                l=0.5 + i,
                c=1.5 + i,
                vol=10.0 + i,
            )
            for i in range(limit)
        ]

async def main():
    market.get_rest_client = lambda: FakeRest()
    from app.main import app

    await init_db()
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        responses = await asyncio.gather(*[
            client.get(
                "/api/candles",
                params={"instId": "BTC-USDT-SWAP", "bar": "1m", "limit": 20},
            )
            for _ in range(20)
        ])

    counts = {}
    for response in responses:
        counts[response.status_code] = counts.get(response.status_code, 0) + 1
    print("status_counts", counts)
    failures = [r for r in responses if r.status_code >= 500]
    if failures:
        print("first_failure", failures[0].status_code, failures[0].text[:120])
    await dispose_engine()

asyncio.run(main())
'@ | .venv/Scripts/python -
```

Observed output from this diagnosis run:

```text
status_counts {200: 1, 500: 19}
first_failure 500 Internal Server Error
```

The same race reproduced at repository level with 20 concurrent direct
`repo.upsert_candles(...)` calls:

```text
IntegrityError (sqlite3.IntegrityError) UNIQUE constraint failed: candles_cache.inst_id, candles_cache.bar, candles_cache.ts
IntegrityError (sqlite3.IntegrityError) UNIQUE constraint failed: candles_cache.inst_id, candles_cache.bar, candles_cache.ts
IntegrityError (sqlite3.IntegrityError) UNIQUE constraint failed: candles_cache.inst_id, candles_cache.bar, candles_cache.ts
IntegrityError (sqlite3.IntegrityError) UNIQUE constraint failed: candles_cache.inst_id, candles_cache.bar, candles_cache.ts
IntegrityError (sqlite3.IntegrityError) UNIQUE constraint failed: candles_cache.inst_id, candles_cache.bar, candles_cache.ts
writers 20 failures 19
rows 20
```

### Runtime repro path

1. Start the app:

```powershell
pnpm dev
```

2. Open `http://localhost:18761`.
3. Trigger overlapping market-data workflows for the same instrument/bar, for
   example initial chart load plus latest-candle refresh, history load,
   compare/backtest operations, or Agent market tools.
4. Watch the backend log for `/api/candles` 500 responses and SQLite errors:

```text
UNIQUE constraint failed: candles_cache.inst_id, candles_cache.bar, candles_cache.ts
database is locked
```

The static code evidence below explains why these workflows converge on the
same foreground SQLite write path.

## Root cause analysis

### Primary root cause: `upsert_candles` is a non-atomic check-then-insert

`/api/candles` fetches live OKX candles, then writes them to the local cache
before returning the live response:

- `services/quant/app/api/market.py:79` calls
  `rest.get_candles(...)`.
- `services/quant/app/api/market.py:80` awaits
  `repo.upsert_candles(...)`.
- `services/quant/app/api/market.py:81` to
  `services/quant/app/api/market.py:82` set the live header and return only
  after the cache write succeeds.

`upsert_candles` is not a database-level upsert:

- `services/quant/app/db/repositories.py:244` defines
  `upsert_candles`.
- `services/quant/app/db/repositories.py:248` to
  `services/quant/app/db/repositories.py:252` select existing timestamps.
- `services/quant/app/db/repositories.py:253` to
  `services/quant/app/db/repositories.py:257` add rows whose timestamps were
  absent from that earlier select.
- `services/quant/app/db/repositories.py:258` commits the batch.

The table enforces uniqueness at the database layer:

- `services/quant/app/db/models.py:206` defines the `candles_cache` table.
- `services/quant/app/db/models.py:208` defines
  `UniqueConstraint("inst_id", "bar", "ts", name="uq_candle")`.

Two sessions can both execute the select before either commits. Both see no
existing rows, both add the same `(inst_id, bar, ts)` values, and the later
commit fails with the reproduced `IntegrityError`.

### Secondary root cause: cache persistence is on the user-facing request path

The cache is an optimization, but its write failure aborts the primary live-data
response. `app/main.py` handles OKX and HTTP exceptions, but there is no local
rollback or response-preserving path for SQLAlchemy `IntegrityError` or
`OperationalError`:

- `services/quant/app/main.py:51` to `services/quant/app/main.py:57` handle
  `OKXError`.
- `services/quant/app/main.py:65` to `services/quant/app/main.py:71` handle
  HTTP exceptions.

Therefore cache-layer database failures become unhandled 500 responses.

### Lock contention is expected with the current SQLite setup

SQLite permits only one writer at a time. The async engine is created without
WAL mode, an explicit busy timeout, or an application-level single-writer
policy:

- `services/quant/app/db/session.py:28` defines `get_engine`.
- `services/quant/app/db/session.py:31` calls
  `create_async_engine(_db_url(), echo=False, future=True)`.
- `services/quant/app/db/session.py:32` creates the async sessionmaker.

The candle cache is written by multiple workflows, not only the chart endpoint:

- `services/quant/app/api/market.py:80` writes for `/api/candles`.
- `services/quant/app/agent/services.py:221` and
  `services/quant/app/agent/services.py:231` write base and higher-timeframe
  candles for single backtests.
- `services/quant/app/agent/services.py:284`,
  `services/quant/app/agent/services.py:297`,
  `services/quant/app/agent/services.py:358`, and
  `services/quant/app/agent/services.py:372` write candles for compare and
  combo paths.
- `services/quant/app/agent/services.py:616`,
  `services/quant/app/agent/services.py:631`, and
  `services/quant/app/agent/services.py:693` write candles for walk-forward
  paths.

So chart refresh, history load, Agent tools, backtests, compare runs, and
walk-forward runs can all contend for the same SQLite database. This explains
the reported `database is locked` symptom as the same foreground cache-write
design under heavier write overlap.

### Frontend can naturally create overlapping candle requests

The workstation page has more than one route to `/api/candles`:

- `apps/web/app/page.tsx:170` to `apps/web/app/page.tsx:183` define the main
  `candlesQ` query.
- `apps/web/app/page.tsx:185` to `apps/web/app/page.tsx:190` define the latest
  candle refresh query.
- `apps/web/app/page.tsx:230` loads older history through `api.candles(...)`.

These requests can overlap for the same `(instId, bar)` and return candles with
shared timestamps, especially on initial load, live fallback refresh, or manual
history loading.

### Existing tests miss the race

`services/quant/tests/test_market_headers.py:53` to
`services/quant/tests/test_market_headers.py:69` test the live header path
serially. `services/quant/tests/test_market_headers.py:74` to
`services/quant/tests/test_market_headers.py:101` pre-seed cache serially and
then test fallback headers. These tests cover response semantics, but they do
not start concurrent `/api/candles` requests against identical
`(instId, bar, ts)` keys.

## Impact surface

- K-line chart loading and refresh: affected because `/api/candles` can return
  500 even when OKX returned valid live candles.
- Cached fallback: affected because failed cache writes reduce future fallback
  coverage.
- Backtest, compare, walk-forward, and Agent market tools: affected because
  shared service code also calls `repo.upsert_candles`.
- Desktop packaged app: affected because the local backend and frontend can
  issue clustered startup requests against one SQLite runtime database.
- Data correctness: the unique constraint prevents duplicate candle rows, so
  the main risk is availability and request reliability rather than silent data
  corruption.

## Candidate fixes

| Option | Summary | Invasiveness | Risk | Effort |
|---|---|---:|---:|---:|
| A | Replace check-then-insert with SQLite atomic upsert and make live `/api/candles` cache-write failures best-effort. Add a concurrent regression test. | Low-Medium | Low | Medium |
| B | Serialize candle-cache writes through a process-level async lock or a single-writer queue while keeping the current insert logic. | Medium | Medium | Medium |
| C | Move candle cache persistence to a separate cache store or background cache service. | High | Medium-High | High |

## Recommended fix

Choose Option A.

The schema already has the correct uniqueness invariant. The bug is that the
application performs dedupe with a separate select before insert, which is not
atomic under concurrency. The least invasive robust fix is to let SQLite enforce
the invariant atomically.

Recommended implementation shape:

1. In `repo.upsert_candles`, use SQLAlchemy's SQLite dialect insert with
   `on_conflict_do_update(...)` for `(inst_id, bar, ts)` so repeated live
   candles refresh OHLCV values. Use `on_conflict_do_nothing(...)` only if the
   team wants cached historical candles to be immutable after first write.
2. Add a focused regression test that sends concurrent `/api/candles` requests
   with a fake OKX client returning identical timestamps and asserts every
   response is 200.
3. In `/api/candles`, treat cache persistence as best-effort once live OKX
   candles have been fetched: on SQLAlchemy `OperationalError` caused by SQLite
   lock contention, rollback, log `instId/bar/limit`, and return the live data.
4. Configure SQLite for local-app contention where appropriate, especially WAL
   mode and an explicit busy timeout, to reduce transient `database is locked`
   failures.

This fix is small, preserves the current SQLite schema, directly addresses the
proven unique-key race, and prevents an optimization-layer failure from taking
down the primary live market-data endpoint.

## 评审结论

The evidence chain is sufficient and points to the reported root cause. The
failing `/api/candles` path really executes the cited code: live OKX data is
fetched at `services/quant/app/api/market.py:79`, the endpoint awaits
`repo.upsert_candles(...)` at `services/quant/app/api/market.py:80`, and the
live response is returned only after that write succeeds at
`services/quant/app/api/market.py:81`. Therefore an exception from the cache
write explains a foreground HTTP 500 even when OKX returned valid candles.

The unique-key failure is also directly explained by the repository code.
`services/quant/app/db/repositories.py:248` to
`services/quant/app/db/repositories.py:257` perform dedupe in application code
with a select followed by ORM inserts, then commit at
`services/quant/app/db/repositories.py:258`. The table-level invariant is
`UniqueConstraint("inst_id", "bar", "ts", name="uq_candle")` at
`services/quant/app/db/models.py:208`. Two concurrent sessions can both read
"missing" before either commits, then one commit wins and the other raises the
observed `UNIQUE constraint failed` error. A duplicate timestamp inside one
input batch would hit the same weakness, so the fix should rely on database
conflict handling rather than only pre-filtering Python objects.

The `database is locked` symptom is a related SQLite writer-contention failure,
not a separate frontend or OKX-data bug. The current engine is created at
`services/quant/app/db/session.py:31` without an explicit busy timeout, WAL
setup, or a single-writer policy, and the same shared `repo.upsert_candles`
helper is called by chart, backtest, compare, combo, and walk-forward paths
(`services/quant/app/agent/services.py:221`, `:284`, `:372`, `:616`, `:693`,
plus higher-timeframe writes). This makes clustered local-app writes a credible
source of transient lock errors.

The final recommended fix is Option A, with tight boundaries:

1. Replace `repo.upsert_candles` with a SQLite atomic upsert using the existing
   `(inst_id, bar, ts)` unique constraint. Prefer `on_conflict_do_update` for
   OHLCV fields so repeated live candles refresh cached values; use
   `do_nothing` only if the product explicitly wants immutable cached candles.
2. In `/api/candles` only, wrap the cache write as best-effort after live
   candles are fetched. Catch SQLAlchemy `IntegrityError` and SQLite lock
   `OperationalError`, call `await session.rollback()`, log `instId`, `bar`,
   `limit`, `before`, and `after`, set `X-Aragon-Data-Source: live`, and return
   the live data. Do not catch broad `Exception`, and do not swallow OKX
   failures or strategy/backtest persistence failures.
3. Add a focused regression test that issues concurrent `/api/candles` requests
   with a fake OKX client returning identical timestamps and asserts all
   responses are 200 with one deduplicated cache row per timestamp.
4. Add SQLite contention hardening only as support for the same fix: configure
   a bounded busy timeout and, if compatible with the packaged app lifecycle,
   WAL mode during engine setup. This reduces lock frequency but is not a
   substitute for the atomic upsert or the endpoint-local best-effort response
   path.

Option B is weaker because a process-local lock serializes only writers inside
one Python process and can introduce head-of-line blocking while leaving the
database invariant dependent on application discipline. Option C is unnecessary
for this bug: moving cache persistence to a separate store or service is a
larger architectural change than the evidence requires. The next node should
implement the bounded Option A above and avoid unrelated refactors.
