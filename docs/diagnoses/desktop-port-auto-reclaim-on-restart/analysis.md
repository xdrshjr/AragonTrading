# Desktop Port Stays Occupied — No Auto-Reclaim on Restart

> Diagnosis node output (Subtask #0). This node does **not** write fix code and
> does **not** commit. Slug chosen from the symptom:
> `desktop-port-auto-reclaim-on-restart`.

## Problem description

This is round 3 of the iterative task. The user reports that **launching still
errors**, with this exact dialog:

```text
端口被占用
端口 18760 已被占用，无法启动 Aragon。请释放后重试：
PowerShell> Get-NetTCPConnection -LocalPort 18760 | Select OwningProcess ; Stop-Process -Id <PID> -Force
[确定]
```

The user's two requirements for this round:

1. **On close**, the desktop app should free the port(s) it occupied
   (「关闭的时候，应该把这个项目桌面端启动占用的端口关闭掉」).
2. **On restart**, if the pre-check finds the port occupied, the app should
   **automatically kill the occupier and then start** — instead of telling the
   user to run a PowerShell command by hand
   (「重新开启的时候，如果检查有占用，应该自动关闭，然后启动」).

**Headline finding.** This dialog is *not* a regression — it is the round-2 fix
working as designed. Round 2 (commit `46f8702`) corrected the port pre-check so
it now **detects** a loopback-bound squatter that previously slipped through,
and the existing guard at
[desktop/src/main.js](../../../desktop/src/main.js:268-277) fires the
`'端口被占用'` dialog and quits. The dialog text in the report is, character for
character, that guard. The *underlying* problem is that **a foreign process is
still squatting on `127.0.0.1:18760` and the app has no mechanism to reclaim the
port** — round 2 explicitly deferred the auto-reaper (it was "Candidate C — not
recommended as the first move"). Round 3 is now an explicit request to implement
that auto-reaper, plus to harden the close path.

The squatter is concrete and **currently live**: PID `71620`, a leftover dev
uvicorn started 2026-06-14 (see evidence below). The packaged app reaps only the
children **it itself** spawned (proven below), so a foreign orphan like this one
is invisible to its cleanup and blocks every launch.

## Reproduction steps

All commands run from `M:/takoAI/AragonTrading`.

### 1. Observe the live squatter on port 18760

```bash
netstat -ano | grep -E "18760|18761"
#  TCP    127.0.0.1:18760   0.0.0.0:0   LISTENING   71620
```

```powershell
Get-CimInstance Win32_Process -Filter 'ProcessId=71620' |
  Select-Object ProcessId,Name,CreationDate,CommandLine | Format-List
```

Output (captured during this diagnosis, 2026-06-16):

```text
ProcessId    : 71620
Name         : python.exe
CreationDate : 2026/6/14 13:37:59
Cmd          : "D:\ProgramData\anaconda3\python.exe" -m uvicorn app.main:app
               --host 127.0.0.1 --port 18760 --log-level info
```

So a **dev** uvicorn (anaconda `python.exe`), bound to the **specific** loopback
address `127.0.0.1:18760`, has been alive for two days and was never reaped. It
is not a child of any packaged-app instance.

### 2. Reproduce the launch failure

Launch the packaged app while PID 71620 still holds the port:

```text
M:/takoAI/AragonTrading/desktop/dist/win-unpacked/Aragon Trading.exe
```

The `'端口被占用'` dialog (from `main.js:269-276`) appears and the app quits —
exactly the round-3 report. The managed backend that the app spawns also collides
and exits, recorded in `backend.log`:

```text
2026-06-16 06:24:01,944 INFO aragon: DB initialized; builtin strategies seeded.
INFO:     Application startup complete.
ERROR:    [Errno 10048] error while attempting to bind on address ('127.0.0.1', 18760): ... 只能使用一次。
INFO:     Waiting for application shutdown.
INFO:     Application shutdown complete.
[process-manager] backend exited (code=1, signal=null)
```

(`Errno 10048` = Windows `WSAEADDRINUSE`. The garbled bytes in the raw log are the
GBK-encoded Chinese error string; meaning: "only one usage of each socket
address … is permitted".)

### 3. Prove the close path reaps the app's OWN children (and only those)

`%APPDATA%/aragon-desktop/logs/main.log` shows every clean quit pairs a
"收到退出信号" with a "前后端已停止" ~8 s later:

```text
2026-06-16T10:20:24.056Z Aragon Trading 桌面端启动（mode=prod, isPackaged=true）
2026-06-16T10:20:27.627Z 后端就绪。
2026-06-16T10:20:41.327Z 前端就绪。
2026-06-16T10:23:43.465Z 收到退出信号，正在停止前后端 …
2026-06-16T10:23:51.824Z 前后端已停止.          <-- own children reaped OK (~8 s)
```

Yet `netstat` (step 1) shows 18760 is *still* held — by PID 71620, which the app
never spawned, so `pm.stop()` could never target it. This is the crux: **the app
cleans up after itself but cannot reclaim a port held by anyone else.**

### 4. (Optional) Free the port the way the user has to today

```powershell
Get-NetTCPConnection -LocalPort 18760 | Select OwningProcess
Stop-Process -Id 71620 -Force
```

After this, the app launches normally and serves all 7 strategies (confirmed in
the round-2 diagnosis, `desktop-backend-port-bind-conflict`). The round-3 ask is
to do this automatically.

## Root cause analysis

The launch failure is the **intended** behavior of the round-2 detection fix; the
*actual* defect for round 3 is a **missing capability**: the launcher can neither
reclaim a foreign-held port on startup nor guarantee its own children are reaped
when it dies abnormally.

### Mechanism, step by step

1. **A foreign process holds the exact bind address.** PID 71620
   (`python.exe … uvicorn … --host 127.0.0.1 --port 18760`) listens on
   `127.0.0.1:18760` — the same specific address the packaged backend binds.
   Evidence: step 1 `netstat`/`Get-CimInstance` above.

2. **Round-2's pre-check now detects it.** `portFree` probes `127.0.0.1` first
   (then the wildcard) and returns free only if both bind:
   [process-manager.js](../../../desktop/src/lib/process-manager.js:40-45)
   (`portFree`) → [process-manager.js](../../../desktop/src/lib/process-manager.js:19-27)
   (`tryBind`). With 71620 holding `127.0.0.1:18760`, the `127.0.0.1` probe gets
   `EADDRINUSE`, so `portFree(18760)` correctly returns `false`.

3. **The guard fires and the app quits — no reclaim.** The pre-check loop and
   guard:
   - [main.js](../../../desktop/src/main.js:264-267) — `for (const p of [BACKEND_PORT, WEB_PORT]) { if (!(await portFree(p))) busy.push(p); }`
   - [main.js](../../../desktop/src/main.js:268-277) — when `busy.length`, show the
     `'端口被占用'` dialog (with the `Get-NetTCPConnection … | Stop-Process`
     remediation text) and `app.quit()`.

   This dialog string is **identical** to the user's round-3 error. There is **no
   code path that tries to kill the occupier**; the design deliberately deferred
   it. The round-2 analysis lists this as *Candidate C* and says: "Auto-reaping
   processes from a packaged app is invasive … Keep C only as a future opt-in
   convenience, not part of this fix"
   ([desktop-backend-port-bind-conflict/analysis.md](../desktop-backend-port-bind-conflict/analysis.md)).
   Round 3 is the request to build Candidate C.

4. **The close path only targets tracked children.** Quit cleanup walks
   `pm.children`, which contains only the processes this instance spawned:
   - [main.js](../../../desktop/src/main.js:299-307) — `gracefulQuit()` → `await pm.stop()`.
   - [main.js](../../../desktop/src/main.js:338-342) — `before-quit` → `gracefulQuit().finally(() => app.exit(0))`.
   - [process-manager.js](../../../desktop/src/lib/process-manager.js:202-222) —
     `stop()` iterates `this.children`, sending `_gracefulStop()` then
     `_forceKill()` (Windows `taskkill /T` then `taskkill /F /T` **by PID**:
     [process-manager.js](../../../desktop/src/lib/process-manager.js:149-160),
     [:162-174](../../../desktop/src/lib/process-manager.js:162-174)).

   A PID-tree kill can only reach descendants of the captured child PID. It can
   **never** reach a foreign orphan like 71620 (a different process tree) nor an
   orphan left by a *previous* instance that was force-killed before `before-quit`
   ran. So:
   - **Round-3 part 1 gap:** if the app is force-killed / crashes (Task Manager,
     OS shutdown, a crash that bypasses `before-quit`/`uncaughtException` at
     [main.js](../../../desktop/src/main.js:345-348)), its backend/web children are
     orphaned and keep the ports — there is no port-targeted sweep and no
     persisted record (pid-file) for the next launch to clean up.
   - **Round-3 part 2 gap:** on the next launch the held port is simply reported
     as busy and the app quits (step 3 above) — no auto-kill.

5. **Why a foreign squatter exists at all.** The current occupant is a manual /
   `scripts/dev.py` dev uvicorn (`--reload` lifecycles are prone to leaving a
   reloader worker behind; see the in-repo reaper that `process-manager.js` was
   modeled on: [scripts/dev.py](../../../scripts/dev.py:138-148) `_kill_tree`).
   Regardless of provenance, from the packaged app's point of view it is "someone
   else on my port," and today the app has no way to take the port back.

### What is NOT the cause

- **Not the strategy commit `6f47cca`.** Already exonerated in round 2 (touches
  only `services/quant/app/strategies/*` + tests).
- **Not a broken own-cleanup.** `main.log` proves `pm.stop()` reaps the app's own
  children every clean quit (step 3). The own-cleanup path is sound for graceful
  quit; it is simply *out of reach* of foreign / pre-crash orphans.
- **Not a `portFree` false positive.** Round 2 already fixed the wildcard blind
  spot; the pre-check is now *correctly* reporting the port busy. The problem is
  purely the **absence of a reclaim/auto-kill step** after detection.

## Impact surface

- **Packaged desktop app on Windows**, whenever any process already holds
  `127.0.0.1:18760` or `127.0.0.1:18761`. Most common sources:
  - a leftover `scripts/dev.py` / manual `uvicorn`/`next` dev server (the present
    case, PID 71620);
  - a prior packaged instance that was **force-killed / crashed** before
    `before-quit` cleanup ran, orphaning its own backend/web.
- **User-visible effect:** the app refuses to start and shows a dialog demanding a
  manual PowerShell command. For a non-technical desktop user this is effectively
  "the app is broken and I can't fix it." This is the exact complaint.
- **Both ports affected equally** — the same pre-check guards 18760 (backend) and
  18761 (web) at [main.js](../../../desktop/src/main.js:264-267).
- **Abnormal-exit residue** compounds it: each crash/force-kill can leave a fresh
  orphan, so the failure can recur even after a manual cleanup, until the orphan
  source is itself addressed.
- Not reproducible in `dev` mode the same way (different lifecycle, console
  visible), which is why it surfaces only for packaged users.

## Candidate fixes

| # | Fix | Summary | Invasiveness | Risk | Effort |
|---|-----|---------|:---:|:---:|:---:|
| A | **Auto-reclaim on startup (port-owner reaper).** When the pre-check finds a port busy, resolve the owning PID(s) and terminate them, re-check, then proceed. Only fall back to the `'端口被占用'` dialog if the port is *still* busy after the reclaim attempt. | New helper, e.g. `reclaimPort(port)` in `process-manager.js`: Windows `Get-NetTCPConnection -LocalPort <p> -State Listen \| Select -Expand OwningProcess` (fallback `netstat -ano`) → `taskkill /F /PID <pid>`; POSIX `lsof -ti :<p>` → `kill -9`. Call it from `bootstrap()` before re-running `portFree`. This *automates the exact remediation command already printed in the dialog.* | Low–Med | **Med** (kills a process by port; could hit an unrelated process if something else ever grabs 18760/18761) | Low–Med |
| B | **Harden the close path so the app frees its own ports reliably.** (1) Add a port-targeted sweep at quit: after `pm.stop()`, if 18760/18761 are still held, reclaim them (reuse A's helper) to catch escaped grandchildren. (2) Persist spawned PIDs to a pid-file under `userData` on start, and on next launch reap any still-alive recorded PIDs that are not us — so an orphan from a *force-killed* prior instance is cleaned even though `before-quit` never ran. | `process-manager.js` writes/reads `userData/aragon-pids.json`; `main.js` calls a reaper on startup and an extra sweep in `gracefulQuit()`. | Med | Low–Med | Med |
| C | **Targeted reaper guarded by identity (safer A).** Same as A, but before killing, verify the owner is "ours": image name in `{aragon-quant.exe, python.exe, node.exe, electron}` **or** the listener answers `GET /api/health` with our app signature. Kill only on a match; otherwise show the dialog. | A + an allow-list / health-signature check. | Med | **Low** | Med |
| D | **Keep round-2 behavior (detection + manual dialog only).** Do nothing new; rely on the user running the PowerShell command. | none | None | Low | None |

## Recommended fix

**Adopt Candidate A (auto-reclaim on startup) as the primary fix, layered with the
identity guard from Candidate C, and ship Candidate B's close-path hardening as
defense in depth.** Reject D (it is the current, rejected status quo).

Justification:

- **A is exactly what round 3 asks for and what the evidence demands.** The port
  is held by a foreign PID (71620) that the app's own `pm.stop()` can never reach
  (root-cause step 4). The only way to satisfy "重新开启的时候…自动关闭，然后启动"
  is to resolve the listener's PID and kill it on startup — which is precisely the
  `Get-NetTCPConnection … | Stop-Process` command the dialog
  ([main.js](../../../desktop/src/main.js:272-273)) already tells the user to run
  by hand. Automating it converts a dead-end dialog into a self-healing launch.

- **Fold in C's identity guard to bound the risk.** A's only real risk is killing
  an unrelated process that happens to hold 18760/18761. These are app-private high
  ports (collisions are unlikely), but a cheap guard — kill only when the owner's
  image name is one of ours *or* it answers `/api/health` like our backend —
  removes the "nuke a stranger" failure mode while still reaping the present
  squatter (`python.exe` running `uvicorn app.main:app`, which both matches the
  image allow-list and answers `/api/health`). Log every PID killed so the action
  is auditable. If the port is still busy after the guarded kill, fall back to the
  existing `'端口被占用'` dialog (no behavior lost).

- **B addresses round-3 part 1 (free ports on close) at its real root.** Graceful
  quit already reaps own children (proven in repro step 3), so the residual gap is
  *abnormal* termination. A pid-file + next-launch reap closes that, and a
  port-targeted sweep in `gracefulQuit()` catches any grandchild that escaped the
  PID tree. B reuses A's `reclaimPort` helper, so the added surface is small.

- **Why not D:** it is literally the current behavior the user is rejecting.

### Suggested scope for the fix node (Subtask #2)

- Add a cross-platform `reclaimPort(port, { ownerGuard })` helper in
  [desktop/src/lib/process-manager.js](../../../desktop/src/lib/process-manager.js)
  (Windows: `Get-NetTCPConnection`/`netstat -ano` → `taskkill /F /PID`; POSIX:
  `lsof -ti` → `kill`), with an image-name/health allow-list guard and per-PID
  logging.
- In [desktop/src/main.js](../../../desktop/src/main.js:264-277): when a port is
  busy, call `reclaimPort` for each busy port, re-run `portFree`, and only show the
  `'端口被占用'` dialog if it is *still* busy.
- Harden close: persist spawned PIDs to `userData/aragon-pids.json` on start; reap
  stale recorded PIDs on next launch; add a final port-free sweep at the end of
  `gracefulQuit()` ([main.js](../../../desktop/src/main.js:299-307)).
- **Scope stays within `desktop/`** (`src/lib/process-manager.js`, `src/main.js`,
  optionally `scripts/verify-dist.mjs` for parity). No `apps/web`,
  `services/quant`, DB, or strategy changes.

### Acceptance criteria for the fix node

1. With PID 71620 (or any process) holding `127.0.0.1:18760`, launching the app
   **kills the occupier, frees the port, and starts normally** — no manual
   PowerShell step. `main.log` records the reclaimed PID.
2. If reclaim fails (e.g., access denied), the app falls back to the existing
   `'端口被占用'` dialog (no regression).
3. After a normal quit, `netstat -ano | grep 18760` shows the port free
   (unchanged from today).
4. After a **force-kill** of the app (Task Manager) leaving an orphan backend, the
   **next launch reaps the orphan** and starts cleanly.
5. The reaper does not kill a process that is neither one of ours nor answering our
   health endpoint (identity guard honored).

## Appendix — evidence index

- Live squatter: `netstat -ano` → `TCP 127.0.0.1:18760 … LISTENING 71620`;
  `Get-CimInstance Win32_Process -Filter 'ProcessId=71620'` →
  `python.exe … uvicorn app.main:app --host 127.0.0.1 --port 18760`, created
  `2026/6/14 13:37:59`.
- Launch failure dialog = code: [main.js](../../../desktop/src/main.js:268-277)
  (`'端口被占用'` + `Get-NetTCPConnection … | Stop-Process` text) — matches the
  user's report verbatim.
- Bind collision in `%APPDATA%/aragon-desktop/logs/backend.log` (Errno 10048,
  exit 1, twice on 2026-06-16 runs).
- Own-cleanup works: `%APPDATA%/aragon-desktop/logs/main.log` — each
  "收到退出信号" → "前后端已停止" (~8 s).
- Code: pre-check `main.js:264-267`; busy guard `main.js:268-277`; quit path
  `main.js:299-307`, `:338-342`, `:345-348`; `pm.stop()`
  `process-manager.js:202-222`; PID-tree kills `:149-160`, `:162-174`; `portFree`
  `:40-45`; ports `config.js:6-9`.
- Prior round: [desktop-backend-port-bind-conflict/analysis.md](../desktop-backend-port-bind-conflict/analysis.md)
  (round 2 added detection + child-liveness; deferred the auto-reaper "Candidate
  C" that round 3 now requests). In-repo reaper precedent:
  [scripts/dev.py](../../../scripts/dev.py:138-148).

## 评审结论 (Review Verdict)

> Appended by Subtask #1 (analysis review). This edit **only adds** this section
> and the supplementary citations within it; no prior content was altered.

### 1. Evidence audit — verdict: PASS (chain is airtight)

Every code/log citation was re-verified against the current working tree:

| Claim | Cited location | Verified |
|---|---|:---:|
| Busy-port dialog text == user's round-3 error, verbatim | [main.js:268-277](../../../desktop/src/main.js:268-277) | ✅ exact, incl. `Get-NetTCPConnection … \| Stop-Process` |
| Pre-check loops over `[18760, 18761]` before any spawn | [main.js:264-267](../../../desktop/src/main.js:264-267) | ✅ |
| `portFree` probes `127.0.0.1` then wildcard (round-2 fix) | [process-manager.js:40-45](../../../desktop/src/lib/process-manager.js:40-45) | ✅ |
| `pm.stop()` iterates **only** `this.children` | [process-manager.js:202-222](../../../desktop/src/lib/process-manager.js:202-222) | ✅ |
| Force-kill is **by captured PID tree** (`taskkill /F /T /PID`) | [process-manager.js:149-174](../../../desktop/src/lib/process-manager.js:149-174) | ✅ cannot reach a foreign tree |
| Quit path: gracefulQuit / before-quit / uncaughtException | [main.js:299-307,338-342,345-348](../../../desktop/src/main.js:299-348) | ✅ |
| Port & host constants | [config.js:6-9](../../../desktop/src/lib/config.js:6-9) | ✅ `127.0.0.1:18760/18761` |
| In-repo reaper precedent | [dev.py:138-150](../../../scripts/dev.py:138-150) `_kill_tree` | ✅ |

The cited code **is** on the failing path: `bootstrap()` runs the pre-check
(264-267) and guard (268-277) *before* any window or child is spawned, so a busy
18760 deterministically yields the reported dialog and `app.quit()`. No
counter-example or alternative explanation survives scrutiny — the report's three
exclusions ("not `6f47cca`", "not broken own-cleanup", "not a `portFree` false
positive") are each independently supported by the verified citations above. The
headline root cause — **detection works, reclaim capability is missing** — holds.

**Supplementary evidence added in review (tightens the chain):**

- **The single-instance lock makes startup reclaim provably safe.**
  `app.requestSingleInstanceLock()` ([main.js:310](../../../desktop/src/main.js:310))
  gates `bootstrap`: a second launch is denied the lock and quits at
  [main.js:311-312](../../../desktop/src/main.js:311-312) **before** the port
  pre-check runs (`whenReady → bootstrap` is wired only inside the lock-holder
  branch, [main.js:327](../../../desktop/src/main.js:327)). Therefore the reclaim
  code can execute **only when no other healthy Aragon instance owns the lock** —
  i.e. the port holder it finds is always an *orphan* or a *foreign* process,
  never a live sibling's backend. This eliminates the worst auto-killer failure
  mode ("kill my own running instance") and materially de-risks Candidate A/C.
- **Dialog text and the dev hint share one source.** The exact
  `Get-NetTCPConnection … | Stop-Process` string also lives in
  [scripts/dev.py:119-120](../../../scripts/dev.py:119-120) (`_port_hint`),
  confirming the report's framing that the fix merely **automates the remediation
  the app already prints**.

### 2. Fix audit — boundary cases the fix node MUST handle

Adopting A+C+B is directionally correct, but four boundary gaps would make a
naïve implementation flaky or unsafe:

- **R1 — Identity guard must not green-light on a generic image name alone.** The
  proposed guard *"image ∈ {aragon-quant.exe, python.exe, node.exe, electron}
  **OR** answers `/api/health`"* is too loose: `python.exe` / `node.exe` are
  generic, so the `OR` could kill an unrelated interpreter that grabbed the port.
  Make the **`/api/health` signature the authoritative discriminator** and treat
  generic image names as necessary-but-not-sufficient: kill iff *(health endpoint
  answers with our app signature)* **OR** *(image is the non-generic
  `aragon-quant.exe`)*. Use a short health timeout (reuse `probeHttp`'s 4 s —
  `/api/health` can be slow, [config.js:13-19](../../../desktop/src/lib/config.js:13-19))
  and never block startup on it. This still reaps the present squatter (its
  `uvicorn app.main:app` answers `/api/health`) while closing the "nuke a stranger
  named python.exe" hole.
- **R2 — Kill→re-check is a TOCTOU race.** The existing kills are fire-and-forget
  `spawn` ([process-manager.js:153,167](../../../desktop/src/lib/process-manager.js:149-174))
  and return *before* Windows releases the LISTEN socket. `reclaimPort` must
  (a) await taskkill completion (`execFile`, not detached `spawn`) and (b) **poll
  `portFree(port)` with a bounded retry** (~2 s, 150-200 ms interval) before
  re-running the pre-check or falling back to the dialog. Otherwise acceptance
  criterion #1 is non-deterministic.
- **R3 — PID-reuse hazard in Candidate B's pid-file reap.** "Reap still-alive
  recorded PIDs that are not us" **by PID alone** is unsafe: the OS may recycle a
  force-killed PID onto an innocent process before the next launch. Gate every
  pid-file kill behind the **same identity check as R1**, or — better — prefer
  *port-targeted* reclaim and use `aragon-pids.json` only as a corroborating hint.
- **R4 (minor) — Quit-time sweep must be identity-guarded too**, so a foreign
  holder a user legitimately started is never collaterally killed at quit. Low
  likelihood (we hold the port the whole time we run) but cheap to guard.

**Regression check.** With R1-R3 honored, the fall-back path (criterion #2)
preserves round-2 behavior exactly — on still-busy / access-denied, the original
`'端口被占用'` dialog still fires, so nothing is lost. Scope stays inside
`desktop/` (`src/lib/process-manager.js`, `src/main.js`); no `apps/web`,
`services/quant`, DB, or strategy surface is touched.

### 3. Final recommended fix (single)

**Implement Candidate A (startup auto-reclaim) hardened with Candidate C's
identity guard as the core fix; add Candidate B's close-path hardening (pid-file +
quit sweep) as a separable defense-in-depth layer. Reject D.**

> On `bootstrap()` detecting a busy 18760/18761, resolve the listener's PID
> (`Get-NetTCPConnection -LocalPort <p> -State Listen` → OwningProcess; `netstat
> -ano` fallback; POSIX `lsof -ti`), and **kill it only when the identity guard
> passes** (R1: `/api/health` signature authoritative, or non-generic
> `aragon-quant.exe`), **awaiting the kill and polling `portFree` until released**
> (R2). Re-run the pre-check; only if *still* busy, show the existing
> `'端口被占用'` dialog (zero behavior lost). Log every reclaimed PID to
> `main.log`. Then layer B — persist spawned PIDs to `userData/aragon-pids.json`,
> reap recorded residue on next launch **gated by the R3 identity check**, and add
> a final port-free sweep in `gracefulQuit()` guarded per R4 — to satisfy
> round-3 part 1 for the *abnormal-exit* case.

**Why this, and not less or more:**

- **A+C is the irreducible minimum that clears the reported dialog and satisfies
  round-3 part 2** ("自动关闭，然后启动"). It automates the very PowerShell command
  the dialog already prints ([main.js:272-273](../../../desktop/src/main.js:272-273)),
  and the single-instance lock (§1) bounds its blast radius to orphan/foreign
  holders. C's identity guard is **not** gold-plating — it is the cheap insurance
  that keeps A from ever killing an innocent process.
- **B is required by round-3 part 1** ("关闭的时候…把端口关闭掉") **only for the
  abnormal-exit path** — the *normal* graceful quit already frees its own ports
  (proven in repro step 3 / `main.log`). So B is genuinely scoped to crash /
  force-kill residue, not redundant work, but it is **separable**: if scope must
  be cut, ship A+C first (it fully resolves the present, foreign-orphan symptom)
  and B as a fast follow.
- **D is rejected** — it is the exact status quo the user is complaining about.

This is the **minimum change that resolves the bug and meets both stated
requirements**, with the regression surface bounded to a guarded, logged,
fall-back-safe reclaim. Hand-off to Subtask #2 (code fix) is unblocked.
