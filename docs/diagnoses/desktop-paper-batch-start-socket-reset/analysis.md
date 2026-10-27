# Desktop packaged multi-strategy paper start resets the proxy socket

## Problem description

User report for this round: in the packaged desktop app, switching to realtime
paper simulation and selecting multiple strategies makes startup fail. The user
explicitly asked to inspect the desktop logs under
`M:/takoAI/AragonTrading/desktop/dist/win-unpacked`.

This diagnosis reviews the evidence available for the failing path after commit
`ba057ce11067bd8eca0e26a8814bde3e7dd11bb0` (`feat: support multi-strategy paper
simulation`). That commit added the multi-select paper UI and the backend
`/api/paper/batch/start` contract in:

- `apps/web/components/Strategy/StrategyPanel.tsx`
- `apps/web/lib/api.ts`
- `services/quant/app/api/paper.py`
- `services/quant/app/paper/manager.py`
- `services/quant/app/schemas.py`
- `services/quant/tests/test_paper_batch.py`

## Observed desktop evidence

The packaged frontend log shows the exact user-visible failure path:

```text
%APPDATA%/aragon-desktop/logs/web.log:84
Failed to proxy http://127.0.0.1:18760/api/paper/batch/start Error: socket hang up
  code: 'ECONNRESET'

%APPDATA%/aragon-desktop/logs/web.log:96
Failed to proxy http://127.0.0.1:18760/api/paper/batch/start Error: socket hang up
  code: 'ECONNRESET'
```

The matching backend log window shows the packaged backend was alive and serving
health, strategies, instruments, and candle endpoints immediately before the
frontend saw the reset:

```text
%APPDATA%/aragon-desktop/logs/backend.log:9792
2026-06-18 05:18:20,844 INFO aragon: DB initialized; builtin strategies seeded.
INFO:     Application startup complete.
INFO:     Uvicorn running on http://127.0.0.1:18760

%APPDATA%/aragon-desktop/logs/backend.log:9796-9812
GET /api/health 200
GET /api/strategies 200
GET /api/instruments 200
GET /api/candles ... 200
PUT /api/strategies/4 200

%APPDATA%/aragon-desktop/logs/backend.log:9840-9841
INFO:     connection closed
[process-manager] backend exited (code=1, signal=null)
```

Important negative evidence: the backend log segment contains no
`POST /api/paper/batch/start` access line and no FastAPI traceback for that
endpoint. Therefore the log proves a desktop proxy/upstream socket reset, but it
does **not** by itself prove that `paper_batch_start()` threw.

## Executed code path

The frontend multi-select path is real and is the path used by the desktop UI:

- `StrategyPanel.tsx:114` defines `paperStart`.
- `StrategyPanel.tsx:373` enters `startPaperSelection(...)`.
- `StrategyPanel.tsx:394` keeps the old single-session path only when
  `uniqueIds.length <= 1`.
- `StrategyPanel.tsx:418` calls `api.paperBatchStart(...)` when the selection has
  multiple unique strategy ids.
- `api.ts:175-187` sends `POST /api/py/paper/batch/start`; the desktop Next
  rewrite maps `/api/py/*` to the Python backend `/api/*`.

The backend route and manager also exist in the current source tree:

- `schemas.py:77` declares `PaperBatchStartReq` with `strategyIds`,
  `instId`, `bar`, capital, source, speed, replay, and priming fields.
- `paper.py:52-96` implements `paper_batch_start`, resolves each strategy,
  constructs `PaperBatchStrategy` objects, calls `get_paper_manager().start_batch`,
  and returns grouped session metadata.
- `manager.py:92-128` starts each session, records group metadata, and rolls back
  already-started sessions on partial failure.
- `paper.py:179-186` serializes each returned session.

## Counter-evidence against a backend business-logic root cause

The current source backend was exercised directly during diagnosis with isolated
temporary databases. Both batch runs succeeded:

```text
tmps/paper-batch-diag3/backend-stdout.log:
INFO: 127.0.0.1:65008 - "POST /api/paper/batch/start HTTP/1.1" 200 OK
INFO: 127.0.0.1:65008 - "GET /api/paper/sessions HTTP/1.1" 200 OK
```

```text
tmps/paper-batch-live-diag/backend-stdout.log:
INFO: 127.0.0.1:58720 - "POST /api/paper/batch/start HTTP/1.1" 200 OK
INFO: 127.0.0.1:58720 - "GET /api/paper/sessions HTTP/1.1" 200 OK
```

This is strong counter-evidence to a claim that the source implementation of
`PaperBatchStartReq`, `paper_batch_start`, or `PaperManager.start_batch` is
intrinsically broken. If those functions were the direct root cause, the isolated
source backend should have reproduced the failure.

Older desktop backend log tracebacks around candle requests show a separate
`candles_cache` unique constraint race:

```text
sqlite3.IntegrityError: UNIQUE constraint failed:
candles_cache.inst_id, candles_cache.bar, candles_cache.ts
...
app/api/market.py, line 63, in get_candles
app/db/repositories.py, line 211, in upsert_candles
```

That explains intermittent `GET /api/candles ... 500` entries, but it does not
explain the observed `/api/paper/batch/start` socket reset because:

- the current reset appears in `web.log` for the paper batch route;
- the backend log does not show a batch route traceback;
- the source batch route succeeds with both replay-style and live-style
  diagnostic runs.

## Candidate fixes audit

| Candidate | Description | Coverage | Risk | Verdict |
|---|---|---|---|---|
| A | Change `paper.py` / `manager.py` batch business logic now. | Does not fit the strongest evidence: source batch start already returns 200. | Medium: may regress working tests and rollback behavior. | Reject for this node. |
| B | Rebuild the real desktop backend/web resources from `ba057ce` and verify the packaged app, not just source. | Directly targets the desktop-only failure and the package/source mismatch risk. | Low. | Accept. |
| C | Add a release smoke to `desktop/scripts/verify-dist.mjs` that starts the real `dist/win-unpacked/resources/backend/aragon-quant.exe` plus packaged web server, calls the same Next proxy path `/api/py/paper/batch/start` with two enabled strategy ids, then asserts `/api/paper/sessions` returns those sessions. | Prevents this exact regression from escaping again; exercises the same BFF/proxy/package boundary that failed. | Low-Medium: smoke needs cleanup of started sessions and temp DB. | Accept. |
| D | Treat candle-cache unique constraint as the primary fix. | It is a real bug, but current evidence does not tie it to batch startup failure. | Medium: distracts from reported route and may leave desktop batch failure untouched. | Defer unless packaged smoke reproduces it as the actual blocker. |

Boundary cases the accepted fix must cover:

- Use a temp DB for smoke so user data and existing desktop history are not
  mutated.
- Stop any sessions created by the smoke via `/api/paper/batch/{groupId}/stop`
  or process shutdown cleanup.
- Run through the packaged Next proxy path, not only direct FastAPI, because
  the observed error is the proxy seeing `ECONNRESET`.
- If the packaged smoke still fails after rebuilding, preserve backend stdout and
  stderr around the batch request before changing business logic.
- Do not loosen the session limit behavior: `config.py` currently sets
  `max_paper_sessions = 12`, and `manager.py:_ensure_capacity` remains the
  correct enforcement point.

## Recommended fix

The minimum recommended fix for the next node is:

1. Rebuild the desktop backend and web resources from the current `ba057ce`
   source, then regenerate `desktop/dist/win-unpacked` through the normal desktop
   packaging path.
2. Extend `desktop/scripts/verify-dist.mjs` so release verification includes a
   multi-strategy paper batch start through the packaged app boundary:
   `POST /api/py/paper/batch/start` with at least two enabled strategies, followed
   by `GET /api/paper/sessions` and cleanup.
3. Only if that packaged smoke reproduces a backend exception should the next
   node change `services/quant/app/api/paper.py` or
   `services/quant/app/paper/manager.py`; the current evidence says those source
   paths already work.

## 评审结论

Final verdict: the evidence chain supports a **desktop packaged artifact /
release-verification gap**, not a proven backend batch business-logic defect.

The single recommended fix is Candidate B plus Candidate C: rebuild the packaged
desktop resources from the multi-strategy commit and add a `verify-dist` smoke
that exercises the real packaged Next proxy and backend with
`/api/paper/batch/start`. This is the smallest change that addresses the observed
desktop-only `ECONNRESET` while avoiding unnecessary edits to source batch logic
that has already passed direct diagnostics. The candle-cache uniqueness error is
a valid separate bug, but it is not the primary recommended fix for this report.
