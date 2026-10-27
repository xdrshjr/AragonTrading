# Multi-strategy paper start still fails with "active paper sessions exceed limit" (round-2 re-diagnosis)

> Bug slug: `multi-strategy-paper-session-limit-recurrence`
> Diagnosis node: Subtask #0 (问题定位与分析). This node does **not** write fix code.
> Supersedes / extends: `docs/diagnoses/multi-strategy-paper-start-session-limit/analysis.md` (round 1).
> Trigger for this round: 用户反馈「没修改好，继续修改，可以看桌面端日志」——
> 第 1 轮的前端修复（提交 `a53f450`、`ff20361`）未真正消除报错。

---

## Problem description

In the desktop app (Electron) the user switches a strategy to 实时模拟 (paper),
multi-selects several strategies, and clicks start to run them as one synchronized
batch. The panel still shows the red failure box:

```
操作失败：active paper sessions exceed limit <N>
```

(User paraphrase: "Active paper sections existed limited".)

Round 1 shipped a **frontend-only** fix (reconcile on mount + a "停止全部并重试"
button). The user reports it is still broken. This round determines **why the
round-1 fix does not actually clear the condition**, backed by the desktop logs
the user pointed at.

Desktop logs read for this diagnosis (Electron `userData`):
`C:\Users\user\AppData\Roaming\aragon-desktop\logs\{backend,main,web}.log`
and the live DB `…\aragon-desktop\aragon.db` (8 strategies, all enabled).

---

## Reproduction steps

### A. From the real desktop logs (what actually happened)

Two independent backend processes returned `429` on the **first** batch start,
with **zero** paper sessions started beforehand in that process:

- Process `[71920]` started fresh at `2026-06-18 06:05:29`
  (`backend.log:9842-9846`, "DB initialized"). The very first
  `POST /api/paper/batch/start` at `06:06` returns **429** and keeps returning
  429 (`backend.log:9879-9913`). Between process start and the first 429 there is
  **no** `POST /api/paper/start` or successful batch start (`backend.log:9847-9878`
  is only health/candles/`PUT /api/strategies/4`).
- Process `[79476]` started fresh at `2026-06-20 01:54:20`
  (`backend.log:13892-13896`). First `POST /api/paper/batch/start` at `02:11`
  returns **429** (`backend.log:14678`); immediately after, a **single**
  `POST /api/paper/start` returns **200** (`backend.log:14695`).

Each batch-start attempt is preceded by `PUT /api/strategies/<id>` — that is the
frontend saving params first, per `startPaperSelection` (`StrategyPanel.tsx:489`),
so the repeated `PUT … 200` + `batch/start 429` pairs are repeated user retries,
not a backend loop.

### B. Deterministic structural reproduction (cap=12, current build)

This is the path that still fails after round 1, on the **current** cap=12 build:

1. Paper mode → multi-select N strategies (N ≤ 12) → start. Batch starts, N
   sessions run on the backend.
2. **Reload the renderer / reopen the window** (Ctrl+R, or close+reopen). The
   Electron main process keeps the Python backend alive, so the N sessions keep
   running; the Zustand store drops its runtime handles.
3. Repeat step 1–2 once or twice **without** clicking 停止模拟 (very common during
   normal use), or start a few **single** sessions in between. Orphaned running
   sessions accumulate on the backend.
4. Multi-select again and start. `active_count + requested > 12` →
   `429 active paper sessions exceed limit 12`.
5. Click "停止全部并重试". It does **not** reliably free the cap (see Root cause
   §3), so the retry 429s again. → "没修改好".

### C. Minimal backend reproduction of the exact string

```bash
cd services/quant
MAX_PAPER_SESSIONS=3 ./.venv/Scripts/python.exe -c "
from app.config import get_settings; get_settings.cache_clear()
from app.paper.manager import PaperManager, PaperSessionLimitError
class S:  # a still-running orphan from a previous renderer load
    state='running'
m = PaperManager()
m._sessions = {f'orphan-{i}': S() for i in range(3)}   # cap already consumed
try:
    m._ensure_capacity(2)                              # user multi-selects 2, clicks start
except PaperSessionLimitError as e:
    print('429 SESSION_LIMIT ->', e)
"
# -> 429 SESSION_LIMIT -> active paper sessions exceed limit 3
```

### D. Runtime cap of the shipped backend (verified this round)

The packaged backend the user runs is `…/win-unpacked/resources/backend/aragon-quant.exe`
(built `2026-06-20 03:52`). Launched on a scratch port, it reports the live cap:

```bash
# run the shipped exe on a spare port, then:
curl -s http://127.0.0.1:18795/api/health
# -> {"status":"ok", ... ,"maxPaperSessions":12}
```

So the **latest** build ships cap=12; older builds shipped cap=3 (see Root cause §4).

---

## Root cause analysis

The cap enforcement itself is correct. The defect is structural: **paper sessions
live in a process-global registry that is never reclaimed, so they pile up as
orphans and permanently consume the cap, and round 1's frontend reconcile cannot
reliably see or stop them.** Five linked facts:

### 1. Sessions are process-global and are never auto-reclaimed

`PaperManager` is a module singleton (`manager.py:208-216`, `get_paper_manager()`).
A started session stays in `self._sessions` with `state == "running"` until an
explicit `stop` / `stop_batch` / `shutdown` (`manager.py:142-174`). The WS
endpoint's only disconnect cleanup is `session.unsubscribe(queue)` — it never
stops the session (`api/paper.py:162-165`). So closing/reloading the renderer
leaves every session running on the backend indefinitely. The cap counts exactly
these:

```python
# manager.py:176-181
def _ensure_capacity(self, requested_count: int) -> None:
    settings = get_settings()
    active_count = sum(1 for s in self._sessions.values() if s.state == "running")
    if active_count + requested_count > settings.max_paper_sessions:
        raise PaperSessionLimitError(
            f"active paper sessions exceed limit {settings.max_paper_sessions}")
```

Raised from `start()` (`manager.py:56`) and `start_batch()` (`manager.py:98`),
turned into `429 {"code":"SESSION_LIMIT"}` at `api/paper.py:46-47` / `92-93`, and
rendered in the "操作失败：" box (`StrategyPanel.tsx`).

### 2. The backend allows duplicate running sessions per strategy

`paper_start` / `manager.start()` mint a brand-new `session_id`+`run_id` every
call (`manager.py:69-74`) and **never** dedupe against an already-running session
for the same `strategyId`. So repeated single starts, or batch+single overlaps
across reloads, produce **multiple `running` rows with the same `strategyId`**.
This matters for §3.

### 3. Round 1's reconcile/stop cannot reclaim a full cap

This is the core reason "没修改好". The round-1 helper keys the rehydrated map by
`strategyId` and folds it with `Object.fromEntries`:

```ts
// paperMulti.ts:42-62 (sessionsFromReconcile)
const entries = running.map((row, index) => [ row.strategyId, { … } ]);
const sessions = Object.fromEntries(entries);            // <-- collapses duplicates
const groupIds = new Set(running.map((row) => row.groupId));
const groupId = groupIds.size === 1 && !groupIds.has(null) ? running[0].groupId : null;
```

- **Duplicate-collapse:** when several `running` rows share a `strategyId` (§2),
  all but the last are dropped from `sessions`. Those orphans stay running on the
  backend, are invisible in the UI, and are **unstoppable** through the
  reconciled handles — so they keep consuming the cap.
- **`groupId` is usually null:** a realistic orphan population accumulated across
  reloads is a *mix* (single starts → `groupId == null`, plus one or more batch
  groups). Then `groupIds.size !== 1` (or it contains `null`), so `groupId`
  resolves to `null`.
- **Stop only touches what the store knows:** "停止全部并重试" calls `paperStop`
  → `stopPaperSessions(paperBatchGroupId, runningSessions)`
  (`StrategyPanel.tsx:194-203`, `526-543`). With `groupId == null` it loops over
  `runningSessions = Object.values(paperSessionsByStrategyId)`
  (`StrategyPanel.tsx:130`) — the **deduped** set — calling `paperStop(sessionId)`
  per entry. It never enumerates the real backend list, so collapsed duplicates
  and any unsurfaced sessions are **never stopped**. The retry sees the same full
  cap → 429 again.
- **Reconcile runs at most once and only when the store is empty**
  (`StrategyPanel.tsx:120-126`, guarded by `reconciledRef` + an early return when
  `paperSessionsByStrategyId` is non-empty), so a later accumulation is not
  re-surfaced.

In short: round 1 modeled "one session per strategy", but the backend produces
"many sessions across strategies and reloads". The reconcile is lossy and the
stop path is partial, so it cannot guarantee the cap is freed — the user stays
stuck on the same 429.

### 4. The historically-shipped backend cap was 3 (dominant trigger in the logs)

The decisive log fact in §A — a **fresh** backend (`active_count == 0`) rejecting
the **first** batch start while the DB has only **8** strategies — is impossible
at cap=12: both guards (`api/paper.py:56-60` route guard `len > max`, and
`_ensure_capacity` `0 + n > max`) require the selection to exceed the cap. With 8
strategies that needs `max < 8`. The only place the cap is set is the config
default (`config.py:48`), and `git` shows it was **`3`** originally
(`414c28c`, 2026-06-12) and only raised to **`12`** in `ba057ce`
(2026-06-18 03:28). `desktop/src/lib/config.js` injects **no** `MAX_PAPER_SESSIONS`
override (`backendEnv`, `config.js:44-55`), so the running value is purely the
compiled default. Any desktop build whose PyInstaller backend predates `ba057ce`
ships cap=3, so on a fresh backend it rejects any selection of >3 strategies —
exactly the fresh-process 429s at `backend.log:9879` and `:14678`. Round 1 was
frontend-only and explicitly left `_ensure_capacity`/`config.py` untouched, so on
a stale cap=3 build it changes nothing → "没修改好".

The current build (`aragon-quant.exe`, 2026-06-20 03:52) does ship cap=12
(verified at runtime, §D) because `build-backend.py` does a clean PyInstaller
rebuild from source (`build-backend.py:100-108`). So going forward the dominant
trigger is the §1–§3 accumulation, not the cap value — but if the user is still
running an older install, the cap-3 build alone reproduces the symptom.

### 5. Frontend fallback cap of 3 (minor, UI-only)

If `/api/health` has not resolved when the panel mounts,
`resolveMaxPaperSessions(undefined)` returns `FALLBACK_MAX_PAPER_SESSIONS = 3`
(`paperMulti.ts:11,17-19`), used only to gate selection
(`capReached`, `StrategyPanel.tsx:132`). This makes the UI *more* restrictive
than the backend (3 ≤ 12); it cannot itself cause a backend 429, but it can
confuse the user about how many they may select. Not a primary cause.

---

## Impact surface

- **Feature:** multi-strategy synchronized paper simulation
  (`POST /api/paper/batch/start`) and single paper start (`POST /api/paper/start`)
  — both funnel through `_ensure_capacity`.
- **Users:** anyone who reloads/reopens the desktop app (routine in the Electron
  renderer) while paper sessions run, or who starts/stops repeatedly — i.e. the
  normal workflow. Capacity looks permanently consumed even when the UI shows
  nothing running.
- **Why round 1 didn't help:** frontend-only; the reconcile is lossy
  (dedup-by-strategyId) and the stop path is partial, so it cannot free a full
  cap; and it does not touch the backend accumulation or the (historically small)
  cap at all.
- **Resource leak:** orphaned live sessions keep consuming OKX WS bandwidth/CPU,
  and their DB `run.status` stays `"running"` forever because `_persist_final`
  (`session.py:336-349`) only runs on an explicit `stop()` — a persistence leak
  independent of the cap.
- **Data safety:** no corruption; partial-batch rollback already works
  (`manager.py:123-127`).

---

## Candidate fixes

| # | Fix | Layer | Invasiveness | Risk | Effort | Notes |
|---|-----|-------|--------------|------|--------|-------|
| A | **Backend auto-reclaim of abandoned sessions.** Track WS subscriber count per session; when a live session has had **zero** subscribers for a grace window (e.g. 30–60s, longer than a reload/reconnect), auto-`stop()` it. Capacity self-heals after reloads. | Backend | Med | Med | Med–High | The only thing that *bounds* the unbounded accumulation. Must use a grace window so a brief reload doesn't kill a session the user is watching; respect the late-subscribe/reconnect-replay design (`api/paper.py:151-158`, `session.py:299-309`). Keep `_ensure_capacity` + the two session-limit tests green. |
| B | **Reliable "stop all" reclaim.** Add `POST /api/paper/stop-all` (stop every `running` session in the registry) — or have the client fetch `GET /api/paper/sessions` and stop **every** `sessionId**, not the deduped store set — and wire "停止全部并重试" to it. Also key `sessionsFromReconcile` by `sessionId` (not `strategyId`) so duplicates are visible/stoppable. | Backend (+ small FE) | Low–Med | Low | Low–Med | Directly fixes the §3 gap that makes the round-1 button ineffective. Deterministic, easy to test. Reactive: clears the jam on demand rather than preventing it. |
| C | **Replace-on-restart per strategy.** In `manager.start()`, before creating a session, stop any existing `running` session with the same `strategyId`. Prevents duplicate-per-strategy orphans at the source and aligns the backend with the FE one-session-per-strategy model. | Backend | Low–Med | Med | Med | Removes the §2 root of the §3 collapse. Changes start semantics (a new start supersedes an old one for that strategy) — must be intentional and documented; doesn't reclaim *cross-strategy* orphans on its own. |
| D | **Ship the raised cap / build hygiene.** Ensure the packaged backend is rebuilt so the shipped cap matches source (latest build already = 12, verified). | Build/Packaging | Low | Low | Low | Necessary if the user is on an old cap-3 install; insufficient alone — any finite cap still wedges via §1–§3. |

Boundary cases any fix must respect:
- Do **not** loosen or remove the cap; keep `test_batch_start_respects_session_limit`
  and `test_batch_start_respects_existing_running_sessions` green.
- `GET /api/paper/sessions` returns `stopped` rows too (`stop()` does not remove
  from `_sessions`; only `shutdown()` clears it) — any enumeration/reconcile must
  filter to `state == "running"`.
- Reclaim must not kill a session during a brief reload/reconnect — use a grace
  window, since the WS endpoint is explicitly designed for late-subscribe/replay.
- A legitimately full backend (12 real running sessions) must still report clearly;
  the goal is reclaimable/visible capacity, not forcing a start.

---

## Recommended fix

**Primary: Candidate A (backend auto-reclaim) + Candidate B (reliable stop-all),
with Candidate D as build hygiene.** Optionally C to harden against
duplicate-per-strategy orphans.

Justification:
- Round 1 already tried the frontend-only route (round-1 Candidate A "reconcile" +
  C "429 recovery") and it failed precisely because, as proven in Root cause §3,
  a frontend reconcile is lossy (dedup-by-`strategyId` collapse + conditional
  `groupId`) and the stop path only touches the store's deduped set. The fix must
  therefore move to the **backend**, which owns the authoritative session list and
  the cap.
- **A** removes the actual mechanism: orphans from renderer reloads are reclaimed
  automatically, so the cap stops filling with invisible sessions — this is the
  only candidate that bounds the unbounded growth in §1, and it also closes the
  WS-bandwidth/DB-`running` leak. Round 1 deferred this exact candidate ("auto
  auto-reclaim") as "heavier"; the recurrence shows it is now warranted.
- **B** makes the existing "停止全部并重试" actually work by enumerating and
  stopping the real backend list (and surfacing duplicates), giving the user a
  deterministic one-click escape even for pre-existing orphans — directly fixing
  the §3 gap that defeated round 1.
- **D** guarantees the shipped backend carries cap=12 (the latest build does);
  required if the user is still on a cap-3 install, but insufficient alone.

The downstream code-fix node (Subtask #2) should implement **A + B** (server-side,
keeping `_ensure_capacity` and the session-limit tests intact), add tests for
abandoned-session reclaim and stop-all, and confirm the desktop package is rebuilt
so the runtime cap matches source.

---

## 评审结论 (Review Verdict)

> Reviewer node: Subtask #1 (分析报告评审与修复). Senior-review audit of the
> root-cause evidence and the candidate fixes above. Every code/git/log citation
> in this document was re-verified against the working tree at review time; the
> findings and corrections below supersede the "Recommended fix" section where
> they differ.

### TL;DR (中文)

分析整体扎实、引用全部属实，但**根因权重需要纠偏**：用户日志真正证明的是**打包后端
仍是旧的 cap=3 构建（§4）**，而非「孤儿会话堆积（§1–§3）」——后者是成立的*潜在*缺陷，
但日志里并未真正发生。因此**最小且可落地的修复**是：**D（确保用户实际运行 cap=12 的
重打包后端）＋ B（后端 `POST /api/paper/stop-all`，接到「停止全部并重试」）**。
**不建议**按原文把 **A（自动回收）列为主修**：本仓库前端只在 paper 模式下开 WS
（`page.tsx:261-284`），切到回测页/最小化即关闭全部 paper WS，而后端会话仍在跑——
朴素的「零订阅 + 30–60s 宽限即回收」会误杀用户故意后台运行的会话。A 降级为可选加固，
且若实现必须改用生命周期/租约触发，而非 WS 订阅计数。

### 1. Root-cause evidence audit — verified, with one weighting correction

**Verified solid (re-checked at review time):**

- §1 process-global, never auto-reclaimed: confirmed. `get_paper_manager()` is a
  module singleton (`manager.py:211-215`); the WS endpoint's only disconnect
  cleanup is `session.unsubscribe(queue)` and it never calls `stop()`
  (`api/paper.py:162-165`); `_ensure_capacity` counts every `state=="running"`
  session (`manager.py:176-181`). Airtight.
- §2 duplicate-per-strategy sessions: confirmed. `start()` mints a fresh
  `session_id` each call with **no** per-strategy dedup (`manager.py:69-74`);
  `start_batch` dedupes only *within* one batch via `dedupe_batch_strategies`
  (`manager.py:97`), so single-starts and cross-reload/cross-batch overlaps
  produce multiple `running` rows for one `strategyId`. Airtight.
- §3 round-1 reconcile/stop is lossy: confirmed. `sessionsFromReconcile` keys by
  `row.strategyId` and folds with `Object.fromEntries` (`paperMulti.ts:47-58`);
  `groupId` collapses to `null` for a mixed orphan set (`paperMulti.ts:59-60`);
  `paperStop` passes `runningSessions = Object.values(paperSessionsByStrategyId)`
  — the *deduped store set* — into `stopPaperSessions`, which with `groupId==null`
  loops only that set and never enumerates the backend (`StrategyPanel.tsx:130,
  194-203, 526-543`); reconcile runs at most once and only when the store is empty
  (`StrategyPanel.tsx:120-126`). Airtight — this fully explains "没修改好".
- §4 historically-shipped cap=3: confirmed by git. `git show ba057ce -- config.py`
  shows the line changed `max_paper_sessions: int = 3` → `12`; `git log -S` proves
  `=3` was introduced in `414c28c` (initial) and only raised in `ba057ce`
  (2026-06-18). `desktop/src/lib/config.js:44-55` (`backendEnv`) injects no
  `MAX_PAPER_SESSIONS`, so the runtime value is purely the compiled default.
- §A logs: confirmed line-for-line. `backend.log` has 19 `batch/start … 429`
  rows; process `[71920]` (`:9842`, DB init 06-18 06:05:29) shows **no** single
  or successful start between startup and the first `429` at `:9879` (verified by
  grepping `9847-9879`), i.e. `active_count == 0`; process `[79476]` (`:13892`,
  06-20 01:54:20) `429`s on `batch/start` at `:14678` and then a single
  `POST /api/paper/start` returns **200** at `:14695`. A fresh backend rejecting a
  ≤8-strategy batch while a single start succeeds is **only** possible when
  `cap < selection ≤ 8` and `cap ≥ 1` — uniquely consistent with the stale cap=3
  build. The cap-3 hypothesis (§4) is therefore the airtight, log-proven cause.

**Weighting correction (the one substantive evidence gap):** the report's
headline root cause is the §1–§3 orphan accumulation, but **the cited logs do not
actually show it happening** — both 429 episodes are fresh processes with
`active_count == 0`. The logs prove §4 (stale cap=3); §1–§3 is a *sound structural*
hypothesis (proven from code, reproduced deterministically in §B/§C) but a
*latent* defect, not the observed failure in these logs. This matters for fix
selection: the change that resolves what the user actually hit is **D**, while the
§1–§3 reclaim work is durability hardening against a recurrence that the current
cap=12 build has not yet exhibited. The report should not present A as if the logs
demanded it.

### 2. Candidate-fix audit — minimality, boundaries, regressions

- **D (ship/install cap=12) is mis-labeled as mere "build hygiene"; it is the
  primary fix for the observed logs.** On a fresh cap-3 backend the first
  `batch/start` of >3 strategies 429s with `active_count==0` — there is nothing to
  reclaim and nothing to stop, so **A, B and C all do nothing** for this case; only
  D does. `build-backend.py:100-108` already does a `--clean` PyInstaller rebuild,
  and `config.py:48` is now `12`, so the *next* package is correct — the open risk
  is purely that the user is still running a stale install. D must therefore be a
  **release gate** (rebuild + confirm the running `aragon-quant.exe` reports
  `maxPaperSessions:12`), not an afterthought.

- **B (reliable stop-all) is the right durable fix and is low-risk — but prefer the
  server-side endpoint form.** Re-keying `sessionsFromReconcile`/the store by
  `sessionId` is more invasive than it reads: the entire frontend paper model
  (`paperSessionsByStrategyId`, overlays, trades, equity in `page.tsx`/`store.ts`)
  is keyed by `strategyId`. The minimal, deterministic fix is a backend
  `POST /api/paper/stop-all` that stops **every** `state=="running"` session in the
  registry; wire 「停止全部并重试」 to call it and then refetch
  `GET /api/paper/sessions`. This sidesteps the §3 dedup/`groupId` problem entirely
  and needs no store re-architecture. Boundary: `list_sessions()` returns `stopped`
  rows too (`manager.py:162-166` has no filter; `stop()` keeps the entry, only
  `shutdown()` clears it), so stop-all must iterate `state=="running"` and the
  client refetch must filter likewise. Keeps `_ensure_capacity` and both
  session-limit tests untouched.

- **A (auto-reclaim by WS-subscriber count) carries a real regression the report
  underweights — recommend demoting it to optional.** New evidence:
  `app/page.tsx:261-284` opens a paper WS for **every** running session, but the
  effect early-returns unless `mode === "paper"` and its cleanup closes all clients
  on dependency change. So whenever the user is on the **backtest tab**, has the
  paper view unmounted, or the window is minimized, **all** running paper sessions
  have **zero** WS subscribers while still running on the backend (which is the
  intended "keep running across renderer changes" design). A naive
  "0 subscribers for 30–60s → stop()" reclaim would therefore kill sessions the
  user deliberately left running in the background — a behavior regression a short
  grace window cannot prevent. A also interacts awkwardly with the test suite:
  `test_batch_start_respects_existing_running_sessions` starts a session over HTTP
  with no WS subscriber at all, so any reclaim trigger must be careful not to make
  that (or a real headless session) collectible. If auto-reclaim is ever pursued it
  must key off **renderer/app lifecycle or a per-session lease the renderer renews
  regardless of active tab**, not raw WS-subscriber count — which is materially more
  design than "Med" invasiveness implies. Not worth it for this round.

- **C (replace-on-restart per strategy) is optional, low-risk hardening.** Stopping
  a same-`strategyId` `running` session inside `start()` before minting a new one
  removes the §2 source of duplicate orphans and aligns the backend with the FE
  one-session-per-strategy model. It changes start semantics (a new start
  supersedes the old run for that strategy) so it must be intentional/documented,
  and it does not reclaim cross-strategy orphans — so it complements, not replaces,
  B. Include only if cheap; otherwise defer.

### 3. Final recommended fix (single, with justification)

**Implement Candidate B as a server-side `POST /api/paper/stop-all`
(stop every `state=="running"` session; wire 「停止全部并重试」 to it + refetch
`GET /api/paper/sessions`), gated behind the mandatory release step D
(rebuild the desktop package and confirm the running backend reports
`maxPaperSessions:12`). Keep `_ensure_capacity` and the two session-limit tests
green. Do NOT implement Candidate A this round; C is optional low-risk hardening.**

Why this is the minimum change that resolves the bug without new regressions:

1. **It covers both real failure modes with the least surface.** D eliminates the
   log-proven cause (fresh-backend cap-3 429, §4/§A) — the only thing that fixes
   what the user actually hit. B makes the existing 「停止全部并重试」 deterministically
   free the cap for the latent orphan-accumulation mode (§1–§3) that defeated
   round 1, by stopping the authoritative backend list instead of the lossy,
   dedup-by-`strategyId` store set. Together they make multi-select paper start
   reliably succeed both on a clean backend and after reload/stop churn.
2. **It is strictly less risky than the report's A+B recommendation.** A's
   WS-subscriber reclaim would, as shown in §2, kill legitimately-running
   background sessions whenever the user leaves the paper tab — a new regression in
   normal use. B+D introduces no automatic session termination: nothing is stopped
   except on an explicit user action or an explicit cap check, so it cannot
   surprise-kill a session and cannot loosen the cap.
3. **It preserves the invariants the report itself flagged.** No change to
   `_ensure_capacity` or `config.py` cap logic; `test_batch_start_respects_session_limit`
   and `test_batch_start_respects_existing_running_sessions` stay green; stop-all
   only touches `state=="running"` rows so it cannot revive stopped sessions.
4. **It leaves a clean upgrade path.** If real cap-12 saturation by abandoned
   sessions is later observed in logs (it is not today), auto-reclaim can be added
   deliberately with a lifecycle/lease trigger — but that is a separate, evidenced
   round, not a speculative addition now.

Tests the code-fix node (Subtask #2) must add: (a) `stop-all` stops all running
sessions and frees capacity for a subsequent `batch/start`; (b) `stop-all` is a
no-op-safe call when nothing is running and does not touch already-`stopped` rows;
(c) the cap and existing session-limit tests remain green. Plus the D release
check (runtime `maxPaperSessions == 12`).

### 4. 评审再核验 — against the current working tree (2026-06-21)

> 二次评审（Subtask #1 复核）。前述 §1–§3 的行号是「评审时」的快照；此后
> Candidate B 已落地（`stop_all` + 端点），文件发生位移，故此节用**当前工作树**的
> 行号重核每一条承重证据，并补入新的运行期日志证据，使证据链对今天的代码状态保持
> 严丝合缝。**结论不变**：推荐方案仍为 **B（服务端 `POST /api/paper/stop-all`，接到
> 「停止全部并重试」+ 重新拉取 `GET /api/paper/sessions`）＋ D（重打包并确认运行期
> cap=12）；不采用 A；C 可选**。

**(a) 承重代码引用——以当前行号逐条复核，结论全部成立：**

- 进程级单例、永不自动回收：`get_paper_manager()` 仍是模块单例
  (`services/quant/app/paper/manager.py:234-238`)；WS 端点 `finally` 仅
  `session.unsubscribe(queue)`、从不 `stop()`（`services/quant/app/api/paper.py:165-188`，
  关键行 `:188`）；`_ensure_capacity` 仍只数 `state=="running"`（`manager.py:199-204`）。**成立。**
- 同一 `strategyId` 可并存多个 `running`：`start()` 每次 `uuid.uuid4().hex` 新建、对同策略
  **无去重**（`manager.py:74-84`）；`start_batch` 仅在单次批内 `dedupe_batch_strategies`
  去重（`manager.py:102`，定义 `:220-228`）。**成立。**
- 前端对账有损：`sessionsFromReconcile` 仍以 `row.strategyId` 为键、`Object.fromEntries`
  折叠重复（`apps/web/lib/paperMulti.ts:44-64`，键 `:49-60`、`groupId` 混合集塌成 `null`
  `:61-62`）；前端兜底上限 `FALLBACK_MAX_PAPER_SESSIONS = 3`（`paperMulti.ts:13,19-21`）。**成立。**
- `list_sessions()` 不过滤、会带回 `stopped` 行（`manager.py:185-189`）——任何枚举/对账须
  自行筛 `state=="running"`。**成立。**
- 配额默认值已是 12：`services/quant/app/config.py:48` `max_paper_sessions: int = 12`；
  `desktop/src/lib/config.js` 的 `backendEnv` 不注入 `MAX_PAPER_SESSIONS`，运行期即编译默认。**成立。**

**(b) 否决 A 的承重论据——再次确认：** 仅在 paper 模式下开 paper-WS、离开该页即全部关闭，
故「后端会话仍在跑、却零 WS 订阅」是**正常且刻意**的后台态：
`apps/web/app/page.tsx:261-284`——副作用首行 `if (mode !== "paper" || ... ) return;`（`:262`），
清理 `return () => clients.forEach((client) => client.close());`（`:283`）。
因此「零订阅 + 宽限即回收」会误杀用户故意后台运行的会话。**A 的回归风险确认存在，维持否决。**

**(c) 推荐方案已落地，且运行期日志证明其消除症状（新证据）：**

- Candidate B 端到端已实现：服务端 `stop_all()`（`manager.py:167-183`，仅遍历
  `state=="running"`、不复活 `stopped`、空集时安全 no-op）＋ 端点
  `POST /paper/stop-all`（`api/paper.py:126-146`）；API 客户端 `apps/web/lib/api.ts:197`
  `paperStopAll`（类型 `apps/web/lib/types.ts:206`）；UI 连线
  `StrategyPanel.tsx:208-209`（`paperStopAll` mutation）→ `:246-249`（`stopAllAndRetry`）
  → `:356-359`（「停止全部并重试」按钮）。`_ensure_capacity` 与两条 session-limit
  测试未被改动。**与本评审推荐完全一致。**
- Candidate D 已满足：打包后端 `desktop/dist/win-unpacked/resources/backend/aragon-quant.exe`
  重建于 **2026-06-20 13:37**（晚于 cap 由 3→12 的 `ba057ce`）。
- **运行期实证**（用户本轮指向的实时日志
  `C:\Users\user\AppData\Roaming\aragon-desktop\logs\backend.log`，2026-06-21 03:51）：
  `POST /api/paper/stop-all → 200 OK`（`:16220/:16247/:16270/:16399/:16544`）之后，
  曾经 429 的 `POST /api/paper/batch/start` 现 **`→ 200 OK`**（`:17387`，发生在
  `:16544` 的 stop-all 之后）；19 条 `batch/start … 429` 全部位于更早处（修复前症状）。
  即「一键停止全部 → 再次批量启动成功」的恢复链在真实运行中已闭合。

**再核验结论：** 证据链对当前工作树成立，且新增运行期日志把「推荐修复消除观测症状」
补成端到端闭环。**最终推荐修复维持不变：B（服务端 stop-all，接「停止全部并重试」+ 重新
拉取 sessions）＋ D（重打包确认 cap=12）；不采用 A；C 可选低风险加固。** 下游
Subtask #2/#3 只需保证上述实现的测试齐备（stop-all 释放配额、空集 no-op、不触 `stopped` 行、
cap 与 session-limit 测试保持绿）并完成 D 的发布门禁校验。
