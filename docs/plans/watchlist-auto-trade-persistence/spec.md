# Spec: Durable Persistence for Watchlist & Auto-Trade Members

> Feature slug: `watchlist-auto-trade-persistence`
> Status: **v2 (reviewed)** · Owner: 方案设计 (Subtask #0) · Reviewer: 方案评审与修复 (Subtask #1) · Date: 2026-06-30
> Downstream: 方案评审与修复 (Subtask #1) → 代码开发 (Subtask #2) → Review & 提交 (Subtask #3)
> Changelog: v2 — 评审就地修复 P1-A..D（schema 边界与 sanitize 职责切分、写穿 fire-and-forget、水合门控机制落地、订阅按 slice 过滤），并补全 P2 说明。详见「评审记录」。

## 评审记录

> 本节由 Subtask #1（方案评审与修复）补充。逐节核对方案与**真实代码**（`apps/web/lib/store.ts`、
> `services/quant/app/db/repositories.py`、`schemas.py`、`api/app_settings.py`、`main.py`、
> `apps/web/app/page.tsx`、`next.config.mjs`、`services/quant/tests/`），四个维度：Feasibility /
> Completeness / Consistency / Right-sizing。结论：方案根因定位准确、复用既有 KV 持久化与代理链路、
> 选型恰当（**未过度设计**）；但落地细节有 4 处 P1 会让「文档承诺」与「真实实现 / 测试」对不上，需就地钉死。
> 所有 P0/P1 已在 v2 正文修复，P2 已记录并在正文点明取舍。

| # | 维度 | 严重度 | 问题 | 处置 |
|---|------|:---:|------|------|
| P1-A | Completeness / Feasibility | **P1** | §2.3/§3/§7 要求 `allocationPct=150/-5` 被**夹取到 [0,100]**、`watchlist` 超长被**截断**，但若 Pydantic 模型照搬既有 `LiveAutoGroupMemberIn` 的 `Field(gt=0, le=100)`（`schemas.py:267`）做区间约束，越界值会先抛 **422**，clamp 分支与对应测试**永远走不到**。schema 的区间约束与「sanitize 夹取」语义互斥。 | 已修：§2.3 显式切分「schema 只做**类型**校验（422 仅留给类型错误），区间 / 去重 / 截断 / `members ⊆ watchlist` 一律由 repo sanitizer 负责」；§3 PUT 同步澄清。 |
| P1-B | Completeness / Correctness | **P1** | §3 称「返回 sanitized 文档供 client **reconcile**」。若把 PUT 响应**写回 store**，则 (1) 触发与 GET 同源的 echo 写穿环；(2) 多个写穿 PUT 并发时**响应乱序**，旧 sanitized 文档回写会**覆盖更新的本地编辑**——正是 no-clobber 想防的同类回退。 | 已修：§2.2/§3 钉死「client 与 server **同套 sanitize 规则**（store 编辑期已 clamp + 维不变量），故响应**无需回写**；写穿 PUT 为 **fire-and-forget**，仅成功 / 失败用于日志与重试，**绝不**写回 store。唯一把后端数据写进 store 的路径是首次 GET 水合」。 |
| P1-C | Feasibility | **P1** | §2.2/§6 说 hook「await **同一个 rehydrate promise**」，但 `page.tsx:489` 既有 effect 只是独立调用 `useAppStore.persist.rehydrate()`，**并不存在被共享的 promise**；且 `reactStrictMode:true`（`next.config.mjs:8`）会令开发期 effect 双触发。水合门控机制不落地，seed/overwrite 时序无保证。 | 已修：§2.2/§6 给出**具体机制**——hook 自行 `await useAppStore.persist.rehydrate()`（幂等重读，既有 page effect 可保留）或注册 `persist.onFinishHydration`；并加 **mount-once ref 守卫**避免 StrictMode 双触发导致重复 seed / 重复订阅。 |
| P1-D | Feasibility | **P1** | §2.2/§6 说「`useAppStore.subscribe` 监听 slice」，但本仓 store **未挂 `subscribeWithSelector` 中间件**（已核 `store.ts:283`），三参选择器订阅形态不可用；裸 `subscribe(listener)` 会在**任意** store 变更（含 `liveAutoTrades`/`liveAutoStatus`/`liveAutoEvents` 等高频运行期态）时触发，自动交易跑起来后会**持续误发 PUT**。 | 已修：§2.2/§6 钉死「订阅用裸 `subscribe((state, prev) => …)`，**先比对** `{watchlist, autoTradeMembers, autoTradeAllocationMode}` 三字段是否变化（store 走不可变更新，引用比较即可），仅在该 slice 真变化时才 debounce-PUT」，不引入新中间件以免改动 persist 组合顺序。 |
| P2-1 | Completeness | P2 | `visibilitychange`/`pagehide` 用 `fetch` 发 PUT 在文档卸载期可能被取消。 | 正文 §2.2 注明用 `fetch(..., { keepalive: true })`；即便丢失，下次启动后端权威 reconcile 兜底（≥500ms 前的编辑已被 debounce PUT 落库）。 |
| P2-2 | Completeness | P2 | §1「**exact** … even after force-kill」略过度：用户编辑后 <500ms 内被 SIGKILL，debounce 尚未发出且 `pagehide` 不会在 SIGKILL 时触发，该窗口编辑可能丢。 | 正文 §1/§7 软化为「恢复到最后一次落库态」，并点明残留窗口由「下次启动 reconcile」兜底（相较今日 localStorage 失效是质的改善）。 |
| P2-3 | Consistency（测试） | P2 | §7 仅说「real async SQLite per test isolation」，未提本仓**铁律**：用例间须清 `app_settings` 行（见 `test_app_settings.py` 的 `_fresh()`），否则共享临时 DB 跨用例串扰致 flaky。 | 正文 §7 补：后端测试复用既有 `_fresh()`（`init_db` + `delete(AppSetting)`）夹具，逐用例隔离。 |
| P2-4 | Consistency（CLAUDE.md §五） | P2 | §2.3「defensively on read」是轻度重复校验，CLAUDE.md §五 禁层层重复校验。 | 保留读时 sanitize，但正文改述其理由为「DB 是**跨版本 / 手改**信任边界」（非内部冗余校验），符合规约精神。 |
| P2-5 | Right-sizing（命名） | P2 | 端点名 `/workspace/watchlist` 比载荷窄（同时含 members + allocationMode）。 | 不改（路由文件 `workspace.py`、key `workspace.watchlist_prefs` 已表达「workspace 偏好」语义），正文 §3 一句话点明这是「watchlist 工作区偏好」整文档。 |

## 1. Overview

The user reports that their **watchlist instruments**（自选标的）and the instruments
they added to the **auto-trade panel**（自动交易面板成员）disappear after they close
and reopen the desktop application. They expect these selections to survive long-term
(「长期存在，而不是就消失了」).

Today both data sets live **only in the renderer's `localStorage`**. The Zustand store
(`apps/web/lib/store.ts`) persists, under the key `aragon-ui` (version 22), the fields
`watchlist: string[]`, `autoTradeMembers: AutoTradeMember[]`, and
`autoTradeAllocationMode: "even" | "max_ratio"` via the `persist` middleware backed by
`localStorage`. There is **no backend record** of these preferences. The project's own
convention (see `.claude-index/index.md`, "Architecture / Data Flow") draws a clear line:
durable, important user data (providers, accounts, app settings, saved factors) lives in
the **backend SQLite DB** and is fetched through TanStack Query, while only ephemeral UI
state lives in `localStorage`. The watchlist and auto-trade roster are durable user data
sitting on the wrong side of that line.

Why does `localStorage` lose them in practice? The desktop shell loads the renderer from a
**fixed origin** `http://127.0.0.1:18761`, so the origin itself is stable and is not the
cause. The real exposure is that the packaged Electron app is documented (see
`docs/diagnoses/desktop-port-auto-reclaim-on-restart/analysis.md` and sibling desktop
diagnoses) to suffer **force-kills, crashes, and orphaned child processes**. Chromium
flushes its `localStorage` LevelDB **asynchronously**; a hard kill before a flush — or
LevelDB corruption accumulated across repeated unclean shutdowns — drops the most recent
writes or, in the corruption case, wipes the store. A SQLite row written by the backend
with an explicit `commit()` on every mutation does not share this failure mode, and it
lives under `userData` (`DB_PATH`, injected by the desktop in prod) where it is decoupled
from renderer storage entirely.

This feature therefore makes the **backend SQLite DB the source of truth** for the
watchlist, the auto-trade members, and the allocation mode, while keeping `localStorage`
as a fast-paint optimistic cache. On startup the renderer hydrates from the backend
("backend wins"); existing users whose data is currently only in `localStorage` are
migrated once via a "seed-if-empty" path; and every subsequent mutation is written through
to the backend (debounced). The result: closing and reopening the app — even after a crash
or force-kill — restores the **last persisted** watchlist and auto-trade roster (P2-2: an
edit made within the ~500 ms debounce window immediately before a hard `SIGKILL` may not
have been flushed; everything older is durable, and the next launch reconciles to the
backend regardless of `localStorage` state — a categorical improvement over today, where
the whole store can vanish).

## 2. Technical Design

### 2.1 Architecture

We reuse the existing generic key-value persistence already proven for auto-trade budget
settings: the `AppSetting` table (`services/quant/app/db/models.py:256`) plus the generic
`get_app_setting` / `set_app_setting` helpers (`services/quant/app/db/repositories.py`).
A single new KV key `workspace.watchlist_prefs` stores one JSON object holding all three
fields. No schema migration is required — `app_settings` is created by `create_all` on
startup and already exists in shipped databases.

```text
Renderer (Zustand store, persist:aragon-ui in localStorage)
  │  on mount: rehydrate(localStorage) → GET /api/py/workspace/watchlist
  │  on mutate: debounced PUT /api/py/workspace/watchlist
  ▼
Next.js BFF rewrite /api/py/* ──► FastAPI /api/workspace/watchlist (new router)
  ▼
repositories.get/set_workspace_watchlist  ──►  AppSetting row key="workspace.watchlist_prefs"
  ▼                                                (value = JSON, value lives under userData DB_PATH)
SQLite (durable, synchronous commit, crash-safe)
```

### 2.2 Source-of-truth & reconciliation rules

- **Hydration gating (P1-C — concrete mechanism).** The store sets `skipHydration: true`
  (`store.ts:764`), so rehydration is manual. `useWorkspaceSync()` does **not** rely on any
  externally-shared promise; it gates on hydration with one of two concrete, supported APIs:
  either `await useAppStore.persist.rehydrate()` (Zustand returns a promise; calling it again
  is an idempotent re-read of `localStorage`, so the existing `rehydrate()` effect in
  `page.tsx:489` may stay untouched) **or** `if (useAppStore.persist.hasHydrated()) run();
  else persist.onFinishHydration(run)`. A **mount-once `ref` guard** wraps the whole GET /
  seed / subscribe sequence so React StrictMode / dev double-invocation
  (`reactStrictMode: true`, `next.config.mjs`) cannot double-seed or double-subscribe.
- **Backend is authoritative.** Once hydration has resolved, the renderer issues
  `GET /api/workspace/watchlist`.
  - If the response has `present: true`, the store's `{ watchlist, autoTradeMembers,
    autoTradeAllocationMode }` slice is **overwritten** with the backend values via a new
    atomic store action `hydrateWorkspace(...)`. A backend-saved **empty** watchlist is a
    valid state and is respected (this is why we use an explicit `present` flag rather than
    "is the list empty").
  - If the response has `present: false` (no row yet — existing localStorage-only users or
    a brand-new install), the renderer **seeds** the backend once by `PUT`-ing the current
    store slice. This is the one-time `localStorage → DB` migration.
- **Write-through (P1-D — slice-filtered subscription, fire-and-forget PUT).** The store is
  **not** wrapped in `subscribeWithSelector` (verified at `store.ts:283`), so the three-arg
  selector-subscription form is unavailable and a bare `useAppStore.subscribe(listener)`
  fires on **every** state change — including high-frequency runtime fields
  (`liveAutoTrades` / `liveAutoStatus` / `liveAutoEvents` / paper sessions). The listener
  therefore **must** compare only the persisted slice: `subscribe((state, prev) => { if
  (state.watchlist === prev.watchlist && state.autoTradeMembers === prev.autoTradeMembers &&
  state.autoTradeAllocationMode === prev.autoTradeAllocationMode) return; … })` (the store
  uses immutable updates, so reference equality is a correct change test). Only on a real
  slice change does it **debounce ~500 ms** then `PUT` the latest values. The subscription is
  armed **only after** hydration/seed finishes, so the hydrating `set()` never echoes a
  spurious PUT. The PUT is **fire-and-forget (P1-B)**: its sanitized response is used solely
  for error/logging — it is **never written back into the store** (client and server apply
  the **same** sanitize rules per §2.3, so the store is already canonical; writing the
  response back would re-open an echo loop and, under out-of-order responses, let a stale
  doc clobber a newer local edit). A best-effort flush is also issued on
  `visibilitychange === "hidden"` / `pagehide`, using `fetch(..., { keepalive: true })`
  (P2-1) so the unloading document does not cancel it; the flush **cancels the pending
  debounce timer** first so no late duplicate PUT races behind it.
- **No-clobber guard.** If the backend `GET` is slow and resolves **after** the user has
  already edited the slice, the hook skips the overwrite (a ref records whether any slice
  mutation happened post-mount) and lets the local edit stand; the pending write-through
  then persists it. This prevents a late hydrate from reverting a fresh user action.
- **Offline / backend-down tolerance.** If `GET` fails, the renderer keeps the
  `localStorage`-rehydrated values (no crash, no data loss); hydration is **not** retried in
  the same session — the next launch re-hydrates. If a `PUT` fails it is logged and retried
  on the next mutation or on the visibility/pagehide flush; the local value remains correct
  in the meantime.

### 2.3 Server-side sanitization (boundary validation, CLAUDE.md §五)

**Schema vs. sanitizer — strict division of labor (P1-A).** The Pydantic models do **type**
validation only; they must **not** carry range/length constraints. Concretely, do **not**
copy `LiveAutoGroupMemberIn`'s `allocationPct: float = Field(gt=0, le=100)`
(`schemas.py:267`) — that would `422` on `150`/`-5` and make the clamp path and its §7 tests
unreachable. Instead the schema declares permissive types (`allocationPct: float` with no
range `Field`, `watchlist: list[str]` with no per-item length constraint, `instId: str`),
and a `422` is reserved **only** for genuine type errors (e.g. `allocationPct` is a string).
**All** range clamping, length capping, trimming, dedupe, mode coercion, and the
`members ⊆ watchlist` invariant live in the repo sanitizer below — the single enforcement
point.

The sanitizer runs **once on write** and again **on read** — the read pass is **not**
redundant inner re-validation (which CLAUDE.md §五 forbids) but defense across a real trust
boundary: a row may have been written by an **older app version** or **hand-edited** on disk.
After sanitization the value is trusted internally.

1. `watchlist`: coerce to `list[str]`; strip empty/whitespace; trim each id to ≤ 64 chars;
   dedupe preserving first-seen order; cap length at `WATCHLIST_MAX = 500`.
2. `autoTradeMembers`: coerce to a list of `{instId, allocationPct}`; **drop any member
   whose `instId` is not in the sanitized `watchlist`** (enforces the invariant
   `autoTradeMembers ⊆ watchlist`, mirroring `removeFromWatchlist` in the store); dedupe by
   `instId`; clamp `allocationPct` to `[0, 100]`; coerce non-finite to `0`.
3. `autoTradeAllocationMode`: must be `"even"` or `"max_ratio"`, else fall back to `"even"`.

> Note (P1-B): because the client store applies these **same** rules at edit time
> (`addAutoTradeMember`/`setAutoTradeAllocation`/`removeFromWatchlist` already clamp and keep
> `members ⊆ watchlist`), the client and server converge to identical documents — so the PUT
> response need not (and must not) be written back into the store (see §2.2 write-through).

## 3. Interface Design (REST)

New router `services/quant/app/api/workspace.py`, mounted under `/api`, reached from the web
client through the existing `/api/py/*` rewrite. Error bodies follow the project-wide
`{ "error": { "code", "message" } }` shape produced by the global handlers in `main.py`.

### GET `/api/workspace/watchlist`
Read the persisted workspace preferences.

Response `200` (`WorkspaceWatchlistOut`):
```json
{
  "present": true,
  "watchlist": ["BTC-USDT-SWAP", "ETH-USDT-SWAP"],
  "autoTradeMembers": [{ "instId": "BTC-USDT-SWAP", "allocationPct": 50 }],
  "autoTradeAllocationMode": "even"
}
```
When no row exists yet: `{ "present": false, "watchlist": [], "autoTradeMembers": [],
"autoTradeAllocationMode": "even" }`.

### PUT `/api/workspace/watchlist`
Replace the persisted workspace preferences (full-document upsert).

Request body (`WorkspaceWatchlistIn`):
```json
{
  "watchlist": ["BTC-USDT-SWAP", "ETH-USDT-SWAP"],
  "autoTradeMembers": [{ "instId": "BTC-USDT-SWAP", "allocationPct": 50 }],
  "autoTradeAllocationMode": "even"
}
```
Response `200`: the **sanitized** document with `present: true`. The cleaned values are
returned for **diagnostics/logging only** — the client treats the write-through PUT as
fire-and-forget and does **not** apply the response back into the store (§2.2 / P1-B). Only
**type** errors (e.g. `allocationPct` is a string, `watchlist` is not an array) yield `422`
from Pydantic; **out-of-range but well-typed** values (`allocationPct = 150`, an over-long
`watchlist`) are accepted and **clamped/capped by the repo sanitizer**, never rejected
(§2.3 / P1-A).

> Naming (P2-5): the endpoint is `/workspace/watchlist`, but the document it persists is the
> whole **watchlist workspace preference** — the watchlist plus its auto-trade member roster
> and allocation mode. The router file `workspace.py` and key `workspace.watchlist_prefs`
> carry that broader intent.

### Frontend client (`apps/web/lib/workspaceApi.ts`)
```ts
export const workspaceApi = {
  get: () => request<WorkspaceWatchlistOut>("/workspace/watchlist"),
  update: (prefs: WorkspaceWatchlistIn) =>
    request<WorkspaceWatchlistOut>("/workspace/watchlist", {
      method: "PUT",
      body: JSON.stringify(prefs),
    }),
};
```
(`request` mirrors the error-parsing helper already in `autoTradeSettingsApi.ts`.)

## 4. Data Model

- **DB table:** existing `app_settings` (`AppSetting`), no migration.
- **Row:** `key = "workspace.watchlist_prefs"`, `value = JSON string`:
  ```json
  {
    "watchlist": ["BTC-USDT-SWAP", "ETH-USDT-SWAP"],
    "autoTradeMembers": [{ "instId": "BTC-USDT-SWAP", "allocationPct": 50 }],
    "autoTradeAllocationMode": "even"
  }
  ```
- **Repository helpers** (`repositories.py`), next to `get/set_auto_trade_settings`:
  - `_WORKSPACE_WATCHLIST_KEY = "workspace.watchlist_prefs"`
  - `get_workspace_watchlist(session) -> dict | None` — returns the sanitized document, or
    `None` when no row exists (drives `present`).
  - `set_workspace_watchlist(session, *, watchlist, members, allocation_mode) -> dict` —
    sanitize per §2.3, `set_app_setting`, return the sanitized document.
- **Frontend in-memory shape** (`apps/web/lib/workspaceTypes.ts`):
  ```ts
  export interface WorkspacePrefs {
    watchlist: string[];
    autoTradeMembers: AutoTradeMember[];           // reused from liveAutoTypes
    autoTradeAllocationMode: AutoTradeAllocationMode;
  }
  export interface WorkspaceWatchlistOut extends WorkspacePrefs { present: boolean }
  export type WorkspaceWatchlistIn = WorkspacePrefs;
  ```

## 5. File / Module Change Plan

| File | C/M | Intent (one line) |
|------|:---:|-------------------|
| `services/quant/app/db/repositories.py` | M | Add `_WORKSPACE_WATCHLIST_KEY`, `get_workspace_watchlist`, `set_workspace_watchlist` (sanitize + `members ⊆ watchlist` invariant) using existing `get/set_app_setting`. |
| `services/quant/app/schemas.py` | M | Add `AutoTradeMemberIo`, `WorkspaceWatchlistIn`, `WorkspaceWatchlistOut` Pydantic models. |
| `services/quant/app/api/workspace.py` | C | New router exposing `GET`/`PUT /workspace/watchlist`. |
| `services/quant/app/main.py` | M | Import and `include_router(workspace.router, prefix="/api")` as the 12th router. |
| `services/quant/tests/test_workspace_api.py` | C | Backend tests: default-empty GET, PUT→GET roundtrip, invariant drop, sanitize/clamp, persistence across a fresh session. |
| `apps/web/lib/workspaceTypes.ts` | C | `WorkspacePrefs` / `WorkspaceWatchlistOut` / `WorkspaceWatchlistIn` types. |
| `apps/web/lib/workspaceApi.ts` | C | REST client (`get` / `update`) mirroring `autoTradeSettingsApi.ts`. |
| `apps/web/lib/store.ts` | M | Add `hydrateWorkspace(prefs)` action: atomically set the slice, re-assert `members ⊆ watchlist`; declare it in `AppState`. |
| `apps/web/lib/useWorkspaceSync.ts` | C | Hook: mount-once `ref` guard (StrictMode-safe) → await `persist.rehydrate()`/`onFinishHydration` → GET (backend-wins / seed-if-empty / no-clobber) → **slice-filtered** `subscribe((s,prev)=>…)` write-through (debounced, **fire-and-forget**, response not written back) + `keepalive` visibility/pagehide flush that cancels the pending debounce. |
| `apps/web/app/page.tsx` | M | Call `useWorkspaceSync()` once in the root client component (alongside the existing `rehydrate()` effect). |
| `apps/web/lib/workspaceApi.test.ts` | C | Client unit tests (request shape, error parsing). |
| `apps/web/lib/useWorkspaceSync.test.ts` | C | Hook tests: backend-wins overwrite, seed-if-empty PUT, debounced single write on rapid edits, no-echo after hydrate, no-clobber on late resolve. |
| `apps/web/lib/store.test.ts` | M | Add a `hydrateWorkspace` invariant test. |

No changes to desktop packaging, OKX adapters, live-auto/paper engines, or strategy code.

## 6. Sequence of Operations

1. Root client mounts. A **mount-once `ref` guard** (StrictMode-safe, P1-C) gates the
   sequence. The hook awaits hydration concretely: `await useAppStore.persist.rehydrate()`
   (idempotent re-read; the existing `page.tsx:489` effect may remain) **or**
   `persist.onFinishHydration(run)` if `!persist.hasHydrated()`.
2. `workspaceApi.get()` runs. `present: true` → `hydrateWorkspace(backend)` (unless the
   no-clobber ref shows a post-mount edit already happened); `present: false` →
   `workspaceApi.update(currentSlice)` (one-time seed). Either way, set `hydrated = true`.
3. Arm **one** `useAppStore.subscribe((state, prev) => …)`. The listener **early-returns
   unless** one of `{ watchlist, autoTradeMembers, autoTradeAllocationMode }` changed by
   reference (P1-D — the store is not `subscribeWithSelector`-wrapped, so it must filter
   itself); on a real slice change it schedules a 500 ms debounced `workspaceApi.update`.
4. User edits watchlist / members (existing store actions `addToWatchlist`,
   `toggleWatchlist`, `addAutoTradeMember`, `setAutoTradeAllocation`,
   `setAutoTradeAllocationMode`, …) → slice changes → debounced PUT → DB `commit()`. The
   PUT is fire-and-forget; its response is **not** written back to the store (P1-B).
5. `visibilitychange === "hidden"` / `pagehide` → **cancel the pending debounce timer**,
   then immediate best-effort `fetch(..., { keepalive: true })` flush of the latest slice.
6. App closed (graceful or force-kill) → the last-flushed DB row is already persisted. Next
   launch: steps 1–2 restore from backend regardless of `localStorage` state. (A sub-500 ms
   edit immediately before a hard `SIGKILL` is the only loss window — P2-2.)

## 7. Testing & Acceptance Criteria

**Backend (pytest, real async SQLite per the project's test isolation):** reuse the
established `_fresh()` fixture (`init_db()` + `delete(AppSetting)`) from `test_app_settings.py`
to clear the shared temp DB between cases — the project's "铁律" against cross-test
contamination (P2-3).
- GET with no row → `present:false`, empty defaults.
- PUT a full doc, then GET → values returned verbatim after sanitization; `present:true`.
- PUT with a member whose `instId` is absent from `watchlist` → that member is dropped
  (invariant), other members preserved.
- PUT with a well-typed but **out-of-range** `allocationPct` = `150` / `-5` → **accepted
  (200) and clamped** to `[0,100]`, **not** `422` (P1-A); non-finite coerced to `0`.
- PUT with a **wrong-typed** field (`allocationPct: "abc"`, `watchlist: 5`) → **`422`** from
  Pydantic (type-only validation boundary, P1-A).
- PUT with `autoTradeAllocationMode = "bogus"` → coerced to `"even"`.
- Persistence: write, dispose session, open a new session, GET → same values.

**Frontend (vitest; component/hook tests run with `NODE_ENV=test`):**
- `useWorkspaceSync`: backend `present:true` overwrites store slice exactly once.
- `present:false` triggers a single seeding `PUT` of the current slice and no overwrite.
- Rapid sequential edits collapse into a single debounced `PUT` with the final value.
- The hydrating `set()` does **not** itself trigger a `PUT` (no echo).
- A non-slice store change (e.g. `pushLiveAutoEvent` / `setLiveAutoStatus`) does **not**
  schedule a `PUT` — the listener filters to the persisted slice (P1-D).
- The fire-and-forget `PUT` response is **not** applied back to the store; a later/stale
  sanitized response cannot clobber a newer local edit (P1-B).
- A `GET` resolving after a user edit does **not** clobber the edit (no-clobber guard).
- `store.hydrateWorkspace`: dropping members not in the incoming watchlist.

**End-to-end acceptance:**
1. Add several instruments to the watchlist and to the auto-trade panel; fully quit the
   desktop app; relaunch → both lists are exactly as left.
2. Repeat, then **force-kill** the app (Task Manager) instead of a clean quit; relaunch →
   both lists still restored (the localStorage failure mode no longer loses data).
3. An existing user upgrading (data only in `localStorage`, no DB row) → first launch seeds
   the DB; the lists are unchanged and now durable.
4. Clearing the watchlist entirely and relaunching keeps it cleared (empty state respected,
   not reset to `FALLBACK_INSTRUMENTS`).

## 8. Risks & Mitigations

| Risk | Mitigation |
|------|------------|
| **Echo loop** — hydration `set()` triggers a write-through PUT, fighting the GET. | Arm the write-through subscription only **after** hydration/seed completes (`hydrated` flag). |
| **Late-GET clobbers a fresh user edit.** | No-clobber guard: track post-mount edits via a ref; skip overwrite if the user already edited. |
| **Backend empty vs. deliberately-empty watchlist confusion.** | Explicit `present` flag distinguishes "never saved" (seed) from "saved empty" (respect). |
| **Backend down at startup** (OKX-independent, but DB could be mid-init). | GET failure falls back to `localStorage` values; no crash; next launch re-hydrates. |
| **PUT failure loses a write.** | Local store + `localStorage` keep the value; retried on next mutation and on visibility/pagehide flush; failures logged, never thrown to UI. |
| **Unbounded growth** of watchlist. | Server caps `watchlist` at 500 and trims ids to ≤ 64 chars. |
| **Two windows diverging.** | Desktop holds a single-instance lock (`main.js`), so only one renderer writes; web-dev multi-tab is out of scope (last-writer-wins is acceptable). |
| **Invariant drift between client and server.** | Server re-asserts `members ⊆ watchlist` on every write; the **store applies the same rules at edit time**, so both converge without the client writing the response back (P1-B). |
| **Write-through over-fires on every store change** (store is not `subscribeWithSelector`-wrapped → bare `subscribe` fires on `liveAutoTrades`/status/events too). | Listener compares `{ watchlist, autoTradeMembers, autoTradeAllocationMode }` by reference and early-returns when unchanged; only a real slice change debounces a PUT (P1-D). |
| **Stale / out-of-order PUT response clobbers a newer edit.** | Write-through PUT is **fire-and-forget**; the response is never written into the store; the visibility/pagehide flush cancels the pending debounce so no late duplicate races behind it (P1-B). |
| **Schema range-constraints make the clamp path unreachable** (copying `Field(gt=0, le=100)`). | Pydantic models do **type-only** validation; range/length normalization is the repo sanitizer's sole job, so `150`/`-5` clamp instead of `422` (P1-A). |
| **Scope creep into desktop/storage internals.** | Fix is purely web + backend DB; no desktop packaging or Chromium-storage tweaks, honoring the desktop "no special-casing" convention. |

## 9. Out of Scope

- Multi-device sync / cloud accounts (single local SQLite only).
- Changing the existing `localStorage` persist contract or its version/migrations (it stays
  as the fast cache; only a new backend layer is added on top).
- Persisting other transient runtime state (paper/live-auto session handles remain
  transient by design).

## 评审结论

**结论：有条件通过（approved with conditions）。**

方案在四个维度上整体扎实：

- **Feasibility（可行）**：根因定位准确（localStorage LevelDB 异步刷盘 + 强杀 / 损坏丢最近写），
  且**完全复用**仓内已验证的链路——`AppSetting` 通用 KV（`models.py:256`）+ `get/set_app_setting`
  （`repositories.py`）+ `app_settings.py` 路由范式 + `autoTradeSettingsApi` 的 `request` 助手 +
  `/api/py/*` 重写（`next.config.mjs`）。所有被引用的文件 / 符号经核**真实存在且一致**。
- **Completeness（完备）**：覆盖 backend-wins / seed-if-empty / no-clobber / 写穿 / 关窗 flush /
  空表语义 / 离线容错，并有 backend + hook + store 三层测试规划。
- **Consistency（一致）**：与 CLAUDE.md「边界一次性校验后内部信任」「修 bug 先写复现测试」「component
  vitest 需 `NODE_ENV=test`」一致；选用 KV 而非新表，沿用 `auto_trade.max_budget_pct` 先例。
- **Right-sizing（适度）**：纯 web + 后端 DB，不碰桌面打包 / Chromium 存储，**未过度设计**，亦不欠规格。

**放行条件（代码开发 Subtask #2 必须落实，均已在 v2 正文钉死）：**

1. **P1-A**：`WorkspaceWatchlistIn` / `AutoTradeMemberIo` 仅做**类型**校验，**禁用** `Field(gt/ge/le/…)`
   区间约束；区间夹取 / 截断 / 去重 / `members ⊆ watchlist` 全部在 repo sanitizer 内完成；`422` 仅留给类型错误。
2. **P1-B**：写穿 PUT 为 **fire-and-forget**，响应**绝不**回写 store；仅首次 GET 水合写 store；
   关窗 flush 先取消挂起的 debounce。
3. **P1-C**：水合门控用 `await persist.rehydrate()` 或 `persist.onFinishHydration`（不依赖虚构的「共享
   promise」），并加 **mount-once `ref` 守卫**抗 StrictMode / 开发期双触发。
4. **P1-D**：写穿订阅用裸 `subscribe((s,prev)=>…)` 且**先按 slice 三字段引用比对再决定是否 PUT**
   （store 未挂 `subscribeWithSelector`，否则运行期高频态会持续误发 PUT）。

P2-1..P2-5 为非阻塞改进（`keepalive` flush、force-kill 残留窗口措辞、`_fresh()` 测试隔离、读时
sanitize 理由、端点命名），已在正文注明取舍，**可在开发中顺手落实，不单独 gating**。

满足以上 4 条放行条件即视为通过，可进入 Subtask #2（代码开发）。

> 评审人：方案评审与修复（Subtask #1） · 日期：2026-06-30 · 文档版本：v2
