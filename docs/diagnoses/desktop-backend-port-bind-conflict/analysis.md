# Desktop Backend Port-Bind Conflict (Errno 10048)

> Diagnosis node output (Subtask #0). This node does **not** write fix code and
> does **not** commit. Slug chosen from the symptom:
> `desktop-backend-port-bind-conflict`.

## Problem description

After the most recent changes, launching the packaged desktop app
(`M:/takoAI/AragonTrading/desktop/dist/win-unpacked/Aragon Trading.exe`) ends in
a broken/erroring startup. The user attributes it to "最近的修改" (the new
trend-pullback strategies, commit `6f47cca`).

What actually happens at startup: the packaged backend (`aragon-quant.exe`)
finishes its application startup (DB init + strategy seeding) and then **fails to
bind its TCP port and exits with code 1**:

```text
ERROR:    [Errno 10048] error while attempting to bind on address ('127.0.0.1', 18760): ... 只能使用一次。
[process-manager] backend exited (code=1, signal=null)
```

`Errno 10048` is the Windows socket error `WSAEADDRINUSE` — the address
`127.0.0.1:18760` is already in use. Despite this, the Electron shell logs
`后端就绪` (backend ready) and continues, because the readiness probe is being
answered by a **different, stale process** that is squatting on the port.

**Headline finding:** The new strategy commit (`6f47cca`) is **not** the cause.
The packaged backend boots cleanly on a free port and exposes all 7 strategies
(including the 2 new ones). The real defect is a **port-bind conflict** caused by
an orphaned dev backend holding `127.0.0.1:18760`, which the desktop's port
pre-check **fails to detect** because it probes a different address family than
the backend actually binds.

## Reproduction steps

All commands run from `M:/takoAI/AragonTrading`.

### 0. Observe the failure in the shipped logs

```powershell
Get-Content "$env:APPDATA/aragon-desktop/logs/backend.log" -Tail 8
Get-Content "$env:APPDATA/aragon-desktop/logs/main.log"     -Tail 8
```

`backend.log` (latest packaged runs, 2026-06-16) shows, twice:

```text
INFO:     Started server process [112200]
INFO:     Waiting for application startup.
2026-06-16 06:24:01,944 INFO aragon: DB initialized; builtin strategies seeded.
INFO:     Application startup complete.
ERROR:    [Errno 10048] error while attempting to bind on address ('127.0.0.1', 18760): ...
INFO:     Waiting for application shutdown.
INFO:     Application shutdown complete.
[process-manager] backend exited (code=1, signal=null)
```

`main.log` for the *same* run nonetheless reports success:

```text
2026-06-16T10:23:59Z 启动后端 …
2026-06-16T10:24:00Z 后端就绪.          <-- false positive (answered by the orphan)
2026-06-16T10:24:00Z 启动前端 …
2026-06-16T10:24:01Z 前端就绪.
```

(Backend log timestamps are local time `06:24`; main log is UTC `10:24` — same
event, +4h offset.)

### 1. Identify the squatter on port 18760

```bash
netstat -ano | grep "127.0.0.1:18760"
#  TCP    127.0.0.1:18760   0.0.0.0:0   LISTENING   71620

wmic process where "ProcessId=71620" get Name,ExecutablePath,CreationDate
#  D:\ProgramData\anaconda3\python.exe   20260614133759 (created 2026-06-14 13:37)
```

It is an orphaned **dev** uvicorn (anaconda `python.exe`), started 2026-06-14 and
never reaped. It still answers the health probe and reports the **old** strategy
set:

```bash
curl.exe -s http://127.0.0.1:18760/api/health        # {"status":"ok",...}
curl.exe -s http://127.0.0.1:18760/api/strategies     # count = 5  (pre-commit builtins)
```

### 2. Reproduce the exact bind error with the packaged backend

```bash
# Orphan still holds 127.0.0.1:18760, so the packaged exe collides:
DB_PATH=M:/takoAI/AragonTrading/tmps/repro_diag.sqlite3 \
  "desktop/dist/win-unpacked/resources/backend/aragon-quant.exe" --port 18760
# -> "ERROR: [Errno 10048] ... bind on address ('127.0.0.1', 18760)"  ; exit code 1
```

### 3. Prove the backend code itself is healthy (new strategies included)

```bash
# Same exe, a free port -> starts cleanly:
DB_PATH=M:/takoAI/AragonTrading/tmps/repro_diag.sqlite3 \
  "desktop/dist/win-unpacked/resources/backend/aragon-quant.exe" --port 18770 &
curl.exe -s http://127.0.0.1:18770/api/health      # {"status":"ok","okx":"up",...}
curl.exe -s http://127.0.0.1:18770/api/strategies   # count = 7, incl. 顺势回踩做多 / 顺势反弹做空
```

### 4. Prove the port pre-check blind spot

```bash
node -e '
const net=require("net");
const t=(p,h)=>new Promise(r=>{const s=net.createServer();
  s.once("error",e=>r("FREE=false ("+e.code+")"));
  s.once("listening",()=>s.close(()=>r("FREE=true")));
  h?s.listen(p,h):s.listen(p);});
(async()=>{
  console.log("wildcard 0.0.0.0  ->", await t(18760,null));        // FREE=true   (MISS)
  console.log("host=127.0.0.1     ->", await t(18760,"127.0.0.1")); // FREE=false (correct)
})();'
```

Output (while the orphan holds `127.0.0.1:18760`):

```text
wildcard 0.0.0.0  -> FREE=true
host=127.0.0.1     -> FREE=false (EADDRINUSE)
```

## Root cause analysis

There are two cooperating defects. The trigger is environmental (an orphan
process); the reason the failure is **silent and confusing** is a code defect in
the desktop launcher.

### Mechanism, step by step

1. A leftover dev backend (PID 71620, anaconda `python.exe`, started 2026-06-14)
   is still listening on the **specific** address `127.0.0.1:18760`. Evidence:
   `netstat` line `TCP 127.0.0.1:18760 ... LISTENING 71620` and `wmic`
   creation date `2026-06-14 13:37`.

2. The packaged backend binds the **specific** loopback address. It does not bind
   the wildcard:
   - [desktop/scripts/backend_entry.py](../../../desktop/scripts/backend_entry.py:22)
     defaults `--host` to `"127.0.0.1"`.
   - [desktop/scripts/backend_entry.py](../../../desktop/scripts/backend_entry.py:31)
     calls `uvicorn.run(app, host=args.host, port=args.port, ...)`.
   - [desktop/src/lib/config.js](../../../desktop/src/lib/config.js:6) sets
     `BACKEND_HOST = '127.0.0.1'` and
     [config.js](../../../desktop/src/lib/config.js:8) `BACKEND_PORT = 18760`.

3. The Electron shell's pre-flight port check probes the **wildcard** address,
   not `127.0.0.1`:
   - [desktop/src/main.js](../../../desktop/src/main.js:262) calls
     `await portFree(p)` — **without a host argument**.
   - [desktop/src/lib/process-manager.js](../../../desktop/src/lib/process-manager.js:19)
     `portFree(port, host = null)` and
     [process-manager.js](../../../desktop/src/lib/process-manager.js:25)
     `if (host) srv.listen(port, host); else srv.listen(port);` — with no host,
     `net.Server.listen(port)` binds the **unspecified/wildcard** address
     (`0.0.0.0` / `::`).

   On Windows, binding the wildcard `0.0.0.0:18760` succeeds even when another
   socket already holds the **specific** `127.0.0.1:18760`. So `portFree(18760)`
   returns `true` ("free") — a **false negative for the conflict**. Empirically
   verified in repro step 4: wildcard probe = `FREE=true`, `127.0.0.1` probe =
   `FREE=false (EADDRINUSE)`.

   Because the check passes, the guard at
   [main.js](../../../desktop/src/main.js:264) (the `'端口被占用'` dialog +
   `app.quit()`) **never fires**, so the user gets no actionable message.

4. The shell starts the new backend, which seeds the DB and then tries to bind
   `127.0.0.1:18760` → collides with the orphan → `Errno 10048` → exits code 1.
   Evidence: `backend.log` lines `3837-3844` and the deterministic repro in
   step 2.

5. Meanwhile the readiness probe treats **any** HTTP response as "ready":
   - [main.js](../../../desktop/src/main.js:208) `waitForHttp(cfg.BACKEND_HEALTH_URL, ...)`.
   - [process-manager.js](../../../desktop/src/lib/process-manager.js:39)
     `http.get(url, ...)` → [process-manager.js](../../../desktop/src/lib/process-manager.js:41)
     `done(true)` on the first response. The probe does not check *which* process
     answered.

   The **orphan** answers `GET /api/health` with `200`, so
   [main.js](../../../desktop/src/main.js:222) logs `后端就绪` even though the
   managed backend just died. The app then proceeds to load the UI against the
   orphan, which runs **old code (5 strategies)** — confirmed by
   `curl .../18760/api/strategies` → `count=5`, vs the fresh exe → `count=7`.

6. The unexpected-exit dialog is suppressed during startup:
   - [main.js](../../../desktop/src/main.js:195) `if (!enginesReady) return;`.
   The managed backend exits *before* `enginesReady` is set
   ([main.js](../../../desktop/src/main.js:291)), so no `'Aragon 引擎已停止'`
   dialog appears either. Net result: a silent, half-broken startup.

### Why the new strategy commit is exonerated

- The packaged `aragon-quant.exe` is dated **2026-06-16 04:49**, i.e. built
  *after* commit `6f47cca` (`2026-06-16 03:13`), and on a free port it returns
  **7** strategies including `顺势回踩做多` / `顺势反弹做空` (repro step 3).
- `backend.log` shows `builtin strategies seeded` with **no** import/registration
  error on every 2026-06-16 run — the only error is the bind failure.
- Commit `6f47cca` touched only `services/quant/app/strategies/*` and tests
  (`git show --stat 6f47cca`); it does not touch ports, the Electron shell, or
  `backend_entry.py`.

The conflict pre-exists the strategy change; the orphan dev process happens to
have been running since 2026-06-14. The new build simply re-exposed it.

### Reviewer-added evidence (Subtask #1 audit)

The diagnosis was re-verified against the current source tree. Four points were
under-cited; closing them strengthens — and does not weaken — the root cause.

1. **The failing path really does bind the *specific* loopback address.** In
   packaged (prod) mode the backend is spawned with only `--port` and **no
   `--host`**: [main.js](../../../desktop/src/main.js:45)
   `args: ['--port', String(cfg.BACKEND_PORT)]`. `--host` therefore falls through
   to its default at
   [backend_entry.py](../../../desktop/scripts/backend_entry.py:22)
   (`default="127.0.0.1"`) and uvicorn binds `127.0.0.1:18760`
   ([backend_entry.py](../../../desktop/scripts/backend_entry.py:31)). This closes
   the "is the cited code on the actual failing path?" question: the prod spawn
   never overrides the host, so the wildcard-vs-specific mismatch is exercised on
   every packaged launch.

2. **The same pre-check blind spot exists a second time — in the dist verifier.**
   `portFree(p)` is also called *without* a host at
   [verify-dist.mjs](../../../desktop/scripts/verify-dist.mjs:207) (pre-check) and
   [verify-dist.mjs](../../../desktop/scripts/verify-dist.mjs:150) (post-teardown
   "ports released?" poll). A fix that edits only the `main.js` call site leaves
   the verifier probing the wildcard too — so the most complete minimal fix is at
   the shared helper
   [process-manager.js](../../../desktop/src/lib/process-manager.js:19), which
   corrects both call paths at once.

3. **Fix B is not speculative — the verifier already implements it; `main.js`
   omits it.** `verify-dist.mjs` records child early-exit at
   [verify-dist.mjs](../../../desktop/scripts/verify-dist.mjs:159-167)
   (`earlyExit` set by `onUnexpectedExit`) and feeds it into readiness:
   [verify-dist.mjs](../../../desktop/scripts/verify-dist.mjs:226)
   `waitForHttp(..., { isAborted: () => earlyExit !== null })`, so it **aborts**
   the poll the instant the managed backend dies. The shipped launcher does not:
   its only abort signal is [main.js](../../../desktop/src/main.js:209)
   `isAborted: () => quitting`, and its `onUnexpectedExit` handler is gated off
   during startup by [main.js](../../../desktop/src/main.js:195)
   `if (!enginesReady) return;`. With nothing watching the child's death,
   `waitForHttp` keeps polling and is satisfied by the orphan's `200`
   ([process-manager.js](../../../desktop/src/lib/process-manager.js:39-41)). This
   is the exact masking mechanism, and it pinpoints Fix B as "give `main.js` the
   liveness guard the verifier already has."

4. **`6f47cca` scope re-confirmed.** `git show --stat 6f47cca` touches only
   `docs/plans/trend-pullback-dual-strategies/spec.md`,
   `services/quant/app/strategies/*` (incl. `__init__.py`) and two test files — no
   `desktop/`, no port/host code. The exoneration holds.

## Impact surface

- **Packaged desktop app on Windows**, whenever any process already holds
  `127.0.0.1:18760` (or `127.0.0.1:18761`) — most commonly a leftover `npm run
  dev` backend/frontend, or a previous app instance whose children were not
  reaped.
- **Silent failure mode** (worst part): the `'端口被占用'` guard is bypassed, the
  app reports `后端就绪`, and the UI silently runs against a **stale backend**
  (old code, missing the new strategies / any recent API change). Users see
  "missing features" or inconsistent behavior with no error pointing at the real
  cause.
- The same wildcard-vs-specific gap applies to the **web** port (18761) check at
  [main.js](../../../desktop/src/main.js:261-262), so the frontend can be shadowed
  the same way.
- Not reproducible in `next dev`/dev mode the same way (dev uses `--reload` and
  different lifecycle), which is why it slips past dev testing.

## Candidate fixes

| # | Fix | Summary | Invasiveness | Risk | Effort |
|---|-----|---------|:---:|:---:|:---:|
| A | **Probe the real bind host in the pre-check** — pass the host to `portFree` so it tests `127.0.0.1:port` exactly as the backend binds. | Make `main.js` call `portFree(p, cfg.BACKEND_HOST)` / `portFree(p, cfg.WEB_HOST)` (or default `host` to `127.0.0.1` in `process-manager.js`). The existing `'端口被占用'` dialog then fires with actionable guidance. | Low | Low | Low |
| B | **Make readiness verify the managed child** — require the managed backend process to still be alive (`!child.exited`) *and* answering health, so an orphan can't satisfy `waitForHttp`. | Add an `isAborted`/liveness check tied to the spawned child in `startEngines` / `waitForHttp`. | Low-Med | Low | Low-Med |
| C | **Reap/own the port before binding** — on conflict, identify and stop stale Aragon backends, or have uvicorn bind with exclusive-address semantics and fail fast with a clear message. | Add an orphan-reaper or `SO_EXCLUSIVEADDRUSE`-style startup. | Med-High | Med (kills processes) | Med |

## Recommended fix

**Adopt Candidate A as the primary fix, and add Candidate B as defense in depth.**

Justification:

- **A targets the exact defect proven by evidence.** The pre-check probes
  `0.0.0.0` while the backend binds `127.0.0.1`; repro step 4 shows the wildcard
  probe returns `FREE=true` for the very port that is occupied. Passing the bind
  host (`cfg.BACKEND_HOST` / `cfg.WEB_HOST`) into `portFree` makes the check match
  reality, so the **already-existing** `'端口被占用'` dialog at
  [main.js](../../../desktop/src/main.js:264) fires with the remediation command
  it already contains (`Get-NetTCPConnection -LocalPort ... | Stop-Process`).
  This converts a silent half-broken startup into a clear, actionable failure.
  It is a one-line-class change, no new dependencies, no process-killing risk.

- **B closes the remaining masking hole.** Even with A, a race (orphan appears
  after the pre-check) could let an orphan answer the health probe. Requiring the
  managed child to be alive when `waitForHttp` succeeds prevents the desktop from
  ever silently adopting a stranger backend, which is what hid the real error
  this time.

- **C is not recommended as the first move.** Auto-killing processes from a
  packaged app is invasive and risky (it may target the wrong PID), and it is
  unnecessary once A surfaces the conflict honestly and the user frees the port.

Suggested scope for the fix node (Subtask #2): change the two pre-check calls in
[desktop/src/main.js](../../../desktop/src/main.js:261-262) to pass the host (or
default the host in [process-manager.js](../../../desktop/src/lib/process-manager.js:19)),
then re-run the repro: with the orphan present the app must now show the
`'端口被占用'` dialog and quit cleanly; after freeing port 18760 (stop PID 71620)
it must start and serve **7** strategies. No changes to `apps/web`,
`services/quant`, the DB schema, or the strategy code are required.

## Appendix — evidence index

- Shipped log: `%APPDATA%/aragon-desktop/logs/backend.log:3837-3844` (Errno 10048,
  exit 1) and `web.log` (separate, pre-existing `sharp` warning — out of scope
  here; tracked in `docs/diagnoses/desktop-packaged-client-exception/`).
- `netstat -ano | grep 127.0.0.1:18760` → PID 71620 LISTENING.
- `wmic` → PID 71620 = `D:\ProgramData\anaconda3\python.exe`, created 2026-06-14.
- Orphan `/api/strategies` → 5 (old) vs packaged exe on free port → 7 (new).
- `git show --stat 6f47cca` → only `services/quant/app/strategies/*` + tests.
- Code: `desktop/src/main.js:261-273` (pre-check), `:208,222` (readiness),
  `:195,291` (gated crash dialog); `desktop/src/lib/process-manager.js:19-27`
  (`portFree` wildcard), `:39-41` (`probeHttp` any-response);
  `desktop/src/lib/config.js:6,8`; `desktop/scripts/backend_entry.py:22,31`.
- Reviewer-added: prod spawn omits `--host` → `desktop/src/main.js:45`; same
  wildcard pre-check blind spot also at `desktop/scripts/verify-dist.mjs:150,207`;
  liveness guard already present in the verifier (`verify-dist.mjs:159-167,226`)
  but absent in `main.js` (only `:209` `quitting`, startup gated by `:195`).

## 评审结论

**Final recommended fix — Candidate A: make the port pre-check probe the real
bind address (`127.0.0.1`), applied at the shared `portFree` helper. Ship
Candidate B (managed-child liveness in readiness) in the same change as low-risk
defense in depth. Do not adopt C.**

The evidence chain is airtight: the prod backend binds the *specific*
`127.0.0.1:18760` (main.js:45 → backend_entry.py:22,31), the pre-check binds the
*wildcard* (process-manager.js:19,24-25 via main.js:262), and on Windows the two
coexist — directly demonstrated by repro step 4 (`wildcard → FREE=true`,
`127.0.0.1 → FREE=false`). Every link was re-checked against source; no
counter-example or alternative root cause survives (the orphan answering with
`count=5` vs the fresh exe's `count=7`, plus the `6f47cca` scope, exonerate the
strategy commit).

### Why A is the single primary fix

- **It removes the one defect that turns a recoverable conflict into a *silent*,
  half-broken launch.** Because the wildcard probe reports an occupied port as
  free, the existing `'端口被占用'` guard (main.js:264-272) never fires. Making the
  probe match the bind address makes that already-present dialog — which already
  carries the `Get-NetTCPConnection … | Stop-Process` remediation — fire and quit
  cleanly. The bug becomes a clear, actionable failure instead of a UI silently
  bound to a stale backend.
- **It is the minimum change:** no new dependency, no process-killing, no schema /
  strategy / web edits. Recommended placement is the shared helper
  `portFree(port, host = '127.0.0.1')`
  ([process-manager.js](../../../desktop/src/lib/process-manager.js:19)) rather
  than only the call site, because the identical blind spot also lives in
  `verify-dist.mjs:150,207`; one helper edit corrects the launcher and the
  verifier together. (Equivalent alternative: pass `cfg.BACKEND_HOST` /
  `cfg.WEB_HOST` per port at each call site — but then `verify-dist.mjs` must be
  updated too, or it stays blind.)
- **No regression:** every real socket in this app binds loopback-specifically
  (config.js:6-7 both `127.0.0.1`), so a loopback-specific probe can only become
  *more* accurate, and no caller depends on the wildcard behavior.

### Why B ships alongside (A remains the named primary)

A alone leaves a narrow race: an orphan that appears *after* the pre-check passes
can still answer the readiness poll. B closes it by aborting `waitForHttp` once
the managed child has exited — the exact guard the verifier already uses
(`earlyExit` + `isAborted`). Concretely for the next node: capture the child
returned by `pm.startChild(backendSpec())` (main.js:207 currently discards it) and
pass `isAborted: () => quitting || child.exited` (main.js:208-210). The pattern is
already proven in-repo, so risk is low.

### Why not C

Auto-reaping processes from a packaged app is invasive and can target the wrong
PID; it is unnecessary once A surfaces the conflict honestly and the user frees
the port. Keep C only as a future opt-in convenience, not part of this fix.

### Acceptance criteria for the fix node (Subtask #2)

1. Pre-check probes `127.0.0.1`; with the orphan on 18760 the app shows
   `'端口被占用'` and quits cleanly — **no** false `后端就绪`.
2. After freeing 18760 (stop PID 71620) the app starts and `/api/strategies`
   serves **7** strategies incl. `顺势回踩做多` / `顺势反弹做空`.
3. (B) Killing the managed backend mid-startup makes readiness fail fast with
   `'后端启动失败'` instead of silently adopting a stranger backend.
4. Scope stays within `desktop/` (`src/lib/process-manager.js` and/or `src/main.js`,
   optionally `scripts/verify-dist.mjs`); no `apps/web`, `services/quant`, DB, or
   strategy changes.

## 实施过程发现的方案缺陷

**The recommended fix as literally stated — "default `portFree(port, host =
'127.0.0.1')`" — is flawed: reverting the default to `127.0.0.1` re-opens a bug
that a prior, already-committed fix closed.**

- The wildcard default is **not** an oversight. Commit `6e00191`
  (`fix: package desktop static assets reliably`) **deliberately** changed
  `portFree(port, host = '127.0.0.1')` → `portFree(port, host = null)` (verified
  with `git log -p -- desktop/src/lib/process-manager.js`). Its rationale is
  recorded in `docs/diagnoses/desktop-packaged-static-assets/analysis.md`
  (`## Issues Found During Implementation`): a stale `next start -p 18761` process
  listening on IPv6 `::` answered the probe with old HTML; the **then-`127.0.0.1`
  default missed that IPv6/dual-stack listener**, so the default was changed to
  bind without a host (Node's no-host `listen(port)` binds `::`, catching the
  IPv6 squatter).

- Networking reason the two probes are complementary (Windows, the impacted OS):
  - `tryBind(port, '127.0.0.1')` fails (→ occupied) when something holds
    `127.0.0.1:port` **or** `0.0.0.0:port`, but **misses** an IPv6 `::`-only
    listener. ← what this bug needs.
  - `tryBind(port, null)` (Node binds `::`) fails when something holds `::port`,
    but on Windows it **coexists** with a specific `127.0.0.1:port` holder, so it
    **misses** the IPv4-loopback squatter. ← the current blind spot;
    `desktop-packaged-static-assets` needs this one.
  - Neither single probe is a superset of the other, so the analysis claim that a
    loopback-specific probe "can only become *more* accurate" does not hold for
    IPv6.

**Corrected approach (implemented):** keep the public signature
`portFree(port, host = null)`, but when no host is given, probe **both**
`127.0.0.1` **and** the wildcard/`::` **sequentially** (a small internal
`tryBind(port, host)` helper), returning free only if both bind. Sequential
(not parallel) avoids a false "occupied" on dual-stack Linux where `::`
(dual-stack) and `127.0.0.1` would otherwise self-conflict. This strictly
dominates both single-probe variants — it detects the `127.0.0.1`-specific
orphan from *this* bug **and** keeps detecting the IPv6 `::` listener from
`desktop-packaged-static-assets`. Both call sites (`main.js:262`,
`verify-dist.mjs:150,207`) inherit the combined check with no call-site change.
Candidate B (managed-child liveness in readiness) is implemented as recommended,
for both the backend and the web waits.
