# Multi-strategy paper start fails with "active paper sessions exceed limit"

> Bug slug: `multi-strategy-paper-start-session-limit`
> Diagnosis node: Subtask #0 (问题定位与分析). This node does NOT write fix code.

## Problem description

In the trading UI the user switches a strategy to realtime paper simulation
(实时模拟), multi-selects several strategies, and clicks start to run them as one
synchronized batch. The panel shows a red failure box at the bottom:

```
操作失败：active paper sessions exceed limit <N>
```

(The user paraphrased this as "Active paper sections existed limited"; the real
string is `active paper sessions exceed limit N`.)

The batch never starts. The user's expectation is that multi-selecting strategies
and starting them together should succeed.

## Where the message comes from

The literal string is produced in exactly one place:

- `services/quant/app/paper/manager.py:176-181` — `PaperManager._ensure_capacity`:

```python
def _ensure_capacity(self, requested_count: int) -> None:
    settings = get_settings()
    active_count = sum(1 for s in self._sessions.values() if s.state == "running")
    if active_count + requested_count > settings.max_paper_sessions:
        raise PaperSessionLimitError(
            f"active paper sessions exceed limit {settings.max_paper_sessions}")
```

It is raised from both `start()` (`manager.py:56`) and `start_batch()`
(`manager.py:98`), then converted to **HTTP 429 `{"code": "SESSION_LIMIT"}`** at
`services/quant/app/api/paper.py:46-47` (single) and `paper.py:92-93` (batch).
The frontend renders any `paperStart` error into the "操作失败：" box at
`apps/web/components/Strategy/StrategyPanel.tsx:246-253`.

Note there is a **second, different** guard at the route layer,
`paper.py:56-60`, whose message is `requested sessions exceed limit ...`. The
user saw the word **active**, so the failing check is the *manager* one — which
can only fire when `active_count > 0`, i.e. **sessions were already running**
before this start attempt.

## Reproduction steps

### A. Unit reproduction (deterministic, no live data) — already in the repo

The shipped test suite already encodes this exact scenario:

```bash
cd services/quant
./.venv/Scripts/python.exe -m pytest tests/test_paper_batch.py \
  -k "respects_existing_running_sessions or respects_session_limit" -v
```

Observed (this diagnosis run):

```
tests/test_paper_batch.py::test_batch_start_respects_session_limit PASSED
tests/test_paper_batch.py::test_batch_start_respects_existing_running_sessions PASSED
```

`test_batch_start_respects_existing_running_sessions` (`tests/test_paper_batch.py:54-85`)
starts one single session, then submits a 2-id batch with `MAX_PAPER_SESSIONS=2`
and asserts the batch is rejected with **429** — i.e. a pre-existing running
session makes the next multi-select batch fail. That is the bug, in unit form.

### B. Minimal direct reproduction of the exact user-facing string

```bash
cd services/quant
MAX_PAPER_SESSIONS=2 ./.venv/Scripts/python.exe -c "
from app.config import get_settings
get_settings.cache_clear()
from app.paper.manager import PaperManager, PaperSessionLimitError
class FakeSession:
    def __init__(self): self.state='running'
m = PaperManager()
m._sessions['orphan-a'] = FakeSession()   # leftover sessions still in the registry
m._sessions['orphan-b'] = FakeSession()
try:
    m._ensure_capacity(2)                  # user multi-selects 2 strategies, clicks start
except PaperSessionLimitError as exc:
    print('429 SESSION_LIMIT ->', exc)
"
```

Output (this diagnosis run):

```
429 SESSION_LIMIT -> active paper sessions exceed limit 2
```

### C. End-to-end reproduction (how the user actually hits it)

1. Open the desktop app, switch to 实时模拟 (paper), multi-select 2+ strategies,
   click start. The batch starts successfully (UI shows the running list).
2. Do **not** click 停止模拟. Instead reload the renderer / re-open the app
   window (or refresh the web page). The Python backend process keeps running.
3. The strategy panel now shows **no running sessions** (selection is re-enabled),
   because the frontend forgot the handles on reload (see root cause below).
4. Multi-select strategies again and click start.
5. Result: `操作失败：active paper sessions exceed limit N` — even though the UI
   shows nothing running, because the **old** sessions are still alive in the
   backend and still count toward the cap.

## Root cause analysis

The cap itself is working as designed. The defect is that **backend paper
sessions become orphaned and keep counting toward `max_paper_sessions`, while the
frontend has no way to see or reclaim them** — so the next multi-select start is
rejected with a message that looks wrong to the user ("nothing is running, why is
it full?"). Three independent gaps combine:

1. **Backend sessions outlive the frontend and are never auto-reclaimed.**
   `PaperManager` is a process-global singleton (`manager.py:208-215`,
   `get_paper_manager()`). A live session stays in `self._sessions` with
   `state == "running"` until an explicit `stop` / `stop_batch` / `shutdown`
   (`manager.py:142-174`). The websocket endpoint's only cleanup on disconnect is
   `session.unsubscribe(queue)` — it does **not** stop the session
   (`api/paper.py:164-165`). So closing/reloading the page leaves the session
   running on the backend indefinitely.

2. **The frontend deliberately drops the runtime handles on reload.**
   The Zustand store's `partialize` persists only selection/theme/account state
   and explicitly **excludes** the paper runtime handles
   (`apps/web/lib/store.ts:184-198`); the comment at `store.ts:183` says
   "绝不持久化运行期句柄". This is verified by `apps/web/lib/store.test.ts:67`
   (`expect(raw).not.toContain("paperSessionsByStrategyId")`). After a reload,
   `paperSessionsByStrategyId` resets to `{}`.

3. **The frontend never reconciles against the live backend session list.**
   `StrategyPanel.tsx:76-78` derives `runningSessions` purely from the (now empty)
   store and re-enables selection (`StrategyPanel.tsx:177-182`). There is a ready
   endpoint `GET /api/paper/sessions` (`api/paper.py:137-139`) and even a client
   wrapper `api.paperSessions()` (`apps/web/lib/api.ts:194`), but **nothing calls
   it** — a repo-wide search for `.paperSessions(` returns no usages. So the UI
   has no chance to learn that sessions are still alive.

Putting it together: the start attempt reaches `_ensure_capacity`, which counts
the orphaned sessions (`active_count > 0`) and raises
`active paper sessions exceed limit N` (`manager.py:179-181`) → 429
(`api/paper.py:92-93`) → "操作失败" box (`StrategyPanel.tsx:246-253`). The unit
test in §A and the direct repro in §B both confirm a non-empty registry is what
trips the cap.

### The closing link: the symptom is reachable *only* after a store/backend desync (reviewer-added)

The three gaps above explain *why* orphans persist, but the airtight question is:
how does the user even reach the screen state "multi-select several strategies and
click start" while sessions are already alive? Within a **single, uninterrupted**
page load this is **impossible by construction**, which is exactly what proves the
desync is the cause:

- Any paper start — even a *single* one — populates the store handle map:
  `paperStart.onSuccess` calls `setPaperSessionsByStrategyId(sessions)`
  (`StrategyPanel.tsx:128`), and the single-start branch returns a one-entry
  `sessions` map (`StrategyPanel.tsx:404-416`). So after *any* start,
  `runningSessions = Object.values(paperSessionsByStrategyId)` is non-empty
  (`StrategyPanel.tsx:76`).
- Once `runningSessions.length > 0`, the selection list is **fully locked**:
  `disabledIds` disables *every* enabled strategy
  (`StrategyPanel.tsx:177-179`), and the 全选/清空 bar is disabled too
  (`StrategyPanel.tsx:203`). The user therefore **cannot** multi-select-and-start
  a second batch without first stopping — so a same-session "start while one is
  already running" can never reach `_ensure_capacity` with `active_count > 0`.

The only way the list re-enables (`disabledIds → []`, `StrategyPanel.tsx:182`) while
the backend still holds running sessions is when `paperSessionsByStrategyId` is
**empty but the backend registry is not** — i.e. the store was reset independently
of the backend. That reset happens on exactly one event: reload/reopen, where
`partialize` drops the runtime handles (`store.ts:184-198`, comment `store.ts:183`,
proven by `store.test.ts:67`) while the process-global `PaperManager`
(`manager.py:208-215`) keeps the sessions. This single chain rules out the two
naive alternatives — "the user just selected too many at once" (blocked by the
selection cap, see next subsection) and "the user added more while one was
running" (blocked by `disabledIds`) — leaving the desync as the *sole* reachable
cause of the observed `active …` message. The unused reconciliation endpoint
(`GET /api/paper/sessions`) is what would have closed this gap: a repo-wide grep
for the call-site `\.paperSessions\(` over `apps/web/**/*.{ts,tsx}` returns **zero
matches** (the only hits for the substring are the store field
`paperSessionsByStrategyId` and the helper `paperSessionsToOverlays`), confirming
the endpoint is dead code today.

### Why a *fresh* batch does not fail (rules out other hypotheses)

On a clean backend the frontend caps the selection to the backend value:
`maxPaperSessions = resolveMaxPaperSessions(health.data?.maxPaperSessions)`
(`StrategyPanel.tsx:61`, `apps/web/lib/paperMulti.ts:16-17`), which is fed from
`/api/health` → `settings.max_paper_sessions` (`api/market.py:36`), default `12`
(`config.py:48`). Selecting up to 12 strategies on an empty backend passes both
the route guard and `_ensure_capacity` (`0 + n <= 12`). So the failure is **not**
"too many selected at once"; it requires pre-existing sessions.

### Secondary / contributing factor (not the primary cause)

If `/api/health` is unavailable when the panel mounts, the frontend falls back to
`FALLBACK_MAX_PAPER_SESSIONS = 3` (`paperMulti.ts:10`). That makes the UI *more*
restrictive than the backend (3 ≤ 12), so it cannot by itself cause the backend
429. A stale desktop package where the bundled frontend and the compiled backend
disagree on the cap could contribute, but the evidence above points to orphaned
sessions as the dominant cause; the cap mismatch is at most aggravating.

## Impact surface

- **Feature:** multi-strategy synchronized paper simulation
  (`POST /api/paper/batch/start`) and single paper start
  (`POST /api/paper/start`) — both funnel through `_ensure_capacity`.
- **Users:** anyone who reloads or re-opens the app (very common in the Electron
  desktop renderer) while paper sessions are running, then tries to start again.
- **Symptom:** "操作失败：active paper sessions exceed limit N" with **no running
  sessions visible** in the UI. Capacity looks permanently consumed.
- **Recovery today:** only by restarting the backend process or manually calling
  the stop endpoints; there is no in-app path.
- **Resource leak:** orphaned live sessions keep consuming OKX websocket
  bandwidth / CPU until the backend restarts.
- **Data safety:** no corruption; rollback on partial batch failure already works
  (`manager.py:123-127`, `test_batch_start_rolls_back_partial_failure`).

## Candidate fixes

| # | Fix | Layer | Invasiveness | Risk | Effort | Notes |
|---|-----|-------|--------------|------|--------|-------|
| A | **Reconcile on load**: on panel/app mount, call the existing `GET /api/paper/sessions` and rehydrate `paperSessionsByStrategyId` / group state so the UI shows the live sessions (user can resume or stop them) instead of silently re-enabling selection. | Frontend only | Low–Med | Low | Med | Reuses already-present-but-unused `api.paperSessions()` (`api.ts:194`). Must **filter `state == "running"`** (see boundary cases), map backend rows → store shape (color/name) and respect inst/bar. No backend semantics change. |
| B | **Backend auto-reclaim**: stop a live session after it has had no websocket subscribers for a grace period (or stop on ws disconnect with a short reconnection grace). Capacity self-heals. | Backend | Med | Med | Med–High | Changes the meaning of a "live" session; risk of killing an intentional long-running sim or a session during a brief reload; needs a timer/GC + reconnection handling; harder to test deterministically. |
| C | **Graceful 429 recovery**: on a `SESSION_LIMIT` 429 from batch start, the frontend fetches `/api/paper/sessions`, shows what is occupying capacity, and offers "停止全部并重试". Keeps the cap untouched. | Frontend | Low | Low | Low–Med | Reactive: only helps *after* the confusing error. Good safety net, not a full fix on its own. |

Boundary cases any fix must respect:
- Do **not** loosen or remove the cap; `max_paper_sessions` enforcement at
  `_ensure_capacity` is correct and is covered by
  `test_batch_start_respects_session_limit` /
  `test_batch_start_respects_existing_running_sessions` — keep those green.
- Reconciled/abandoned sessions may belong to a different `instId`/`bar` than the
  current selection; the UI must handle that (show them and allow stop, rather
  than assume they match the current chart).
- A legitimately full backend (e.g. 12 real running sessions) must still be
  reported clearly — the goal is to make the state visible and recoverable, not
  to always force a start.
- **`GET /api/paper/sessions` returns stopped sessions too** (reviewer-added):
  `stop()` does **not** remove a session from `_sessions` — only `shutdown()`
  clears the registry (`manager.py:142-148` vs `manager.py:168-174`), so
  `list_sessions()` (`manager.py:162-166`) emits rows with `state == "stopped"`
  as well. `_ensure_capacity` only counts `state == "running"`
  (`manager.py:178`), so the reconcile **must filter to running**; rehydrating
  stopped rows would resurrect dead sessions in the UI and wrongly lock
  selection.
- **The session row has no name/color** (reviewer-added): `session.info()`
  exposes only `sessionId/runId/instId/bar/state/equity/positionSide`
  (`session.py:351-357`). `list_sessions()` merges `_session_meta`, which carries
  `strategyName` **only for batch sessions** (set at `manager.py:110-115`);
  **single**-start sessions (`manager.py:75-79`) carry no name. So the reconcile
  must resolve the display name from the `strategies` query by `strategyId` and
  assign `color` by index (as `sessionsFromBatch` already does,
  `paperMulti.ts:20-35`) — it cannot rely on the backend row alone.
- **Group rehydration for batch-stop** (reviewer-added): to let the user stop a
  reconciled batch via `POST /api/paper/batch/{group}/stop`, rebuild
  `paperBatchGroupId` from the `groupId` in each row's meta
  (`manager.py:110-115`); rows with `groupId == null` are single sessions and
  fall back to per-session stop, already handled at `StrategyPanel.tsx:429-446`.

## Recommended fix

**Primary: Candidate A (reconcile on load), with Candidate C as a safety net.**

Justification:
- It removes the actual root cause for the user — the frontend/backend desync —
  by making the UI reflect the sessions that truly exist, so the user either sees
  them (and can stop them) or knowingly works within the remaining capacity. The
  surprise 429 disappears.
- It is the smallest, lowest-risk change: frontend-only, and it activates code
  that already ships but is unused (`GET /api/paper/sessions` +
  `api.paperSessions()`), with no change to the proven cap semantics or to the
  existing passing tests.
- Candidate C complements it cheaply: if capacity is genuinely occupied, give the
  user one-click "stop all & retry" instead of a dead-end error.
- Candidate B is deferred: auto-killing live sessions is heavier and risks
  terminating sessions a user intends to keep running; revisit only if product
  wants sessions to die when the UI is closed.

The downstream code-fix node should implement A (+ C) in the frontend and must
keep `_ensure_capacity` and the two session-limit tests intact.

## 评审结论

**评审人**：Subtask #1（分析报告评审与修复）。**结论：分析成立，证据链经核对后已闭合，采纳其推荐方案。**

### 证据核验结果（逐条复核，全部通过）

逐一打开被引用文件核对，**所有行号与代码内容精确无误**：

- 报错串与抛出点：`manager.py:176-181` `_ensure_capacity`（串为 `active paper sessions exceed limit {N}`，且只统计 `state == "running"`）；分别由 `start()`（`manager.py:56`，`_ensure_capacity(1)`）与 `start_batch()`（`manager.py:98`，`_ensure_capacity(len)`）触发。
- 转 HTTP：单条 `paper.py:46-47`、批量 `paper.py:92-93`，均为 `429 {"code":"SESSION_LIMIT"}`；前端渲染于 `StrategyPanel.tsx:246-253` 的「操作失败：」框。
- 路由层另一道守卫 `paper.py:56-60`（串为 `requested sessions exceed limit`）确与 manager 守卫不同；用户所述「active」对应 manager 守卫，方向判断正确。
- 孤儿成因三要素：进程级单例 `manager.py:208-215`；ws 断开仅 `unsubscribe` 不 `stop`（`paper.py:164-165`）；store `partialize` 排除运行期句柄（`store.ts:183-198`，`store.test.ts:67` 佐证）；前端从不对账 `GET /api/paper/sessions`（`paper.py:137-139` 端点存在、`api.ts:194` 封装存在，但 `\.paperSessions\(` 在 `apps/web/**/*.{ts,tsx}` **零调用点**，已用精确 grep 复核）。
- 容量上限链路：`market.py:36` health 下发 `maxPaperSessions` ← `config.py:48` 默认 12；前端 `paperMulti.ts:10/16-17` 回退值 3、解析函数；选择上限 `StrategyPanel.tsx:61`。
- 复现：`test_paper_batch.py:54-85`（`test_batch_start_respects_existing_running_sessions`，先单条后批量、`MAX_PAPER_SESSIONS=2`、断言 429）确为本 Bug 的单元形态。

**是否落在失败路径上**：是。`start_batch → _ensure_capacity` 即真正抛出行，向上转 429 并渲染到「操作失败」框，§A 单测与 §B 最小脚本均复现出完全一致的报错串。

**反例排查**：原报告已排除「一次选太多」（`0+n ≤ 12`）与 health 不可达回退（3 ≤ 12，只会更严不会更松）。本节新增的**闭环证据**进一步排除了「同一页内已有会话再批量」这一路径——`disabledIds`（`StrategyPanel.tsx:177-179`）在 `runningSessions.length > 0` 时锁死整张选择列表，而任意一次 start（含单条）都会写入 `paperSessionsByStrategyId`，故同会话内根本无法二次多选启动。由此，可观测到「界面无运行项却报 active 满额」的状态**有且仅有** store/backend 失同步（刷新/重开）一条可达路径。**证据链已闭合，无遗漏的替代解释。**

### 推荐修复（单一方案）

**采纳候选 A（挂载即对账）为主修复，候选 C（429 优雅恢复）为安全网；候选 B（后端自动回收）暂缓。**

理由：

1. **直击根因且改动最小**：根因是「前端视图与后端真实会话失同步」，A 通过在面板/应用挂载时调用既有但闲置的 `GET /api/paper/sessions` 把真实会话拉回 UI，让用户「看得见、可停止、可在剩余配额内继续」，意外 429 随之消失。改动**仅前端**，激活已随包发布的死代码（`paper.py:137-139` + `api.ts:194`），**不触碰** `_ensure_capacity` 语义，不改动既有通过的两条 session-limit 测试 → 回归面最小。
2. **C 作为低成本兜底**：当配额确为真实占满时，给出「显示占用情况 + 停止全部并重试」，把死胡同错误变为可操作路径；它是 A 的补充而非替代（仅在错误发生后反应）。
3. **B 暂缓的硬理由**：按 ws 断开即停会话，会与该端点显式支持的「迟订阅 / 断线重连即重放」设计（`paper.py:151-158` 注释、`session.py:299-309` snapshot 重放）直接冲突，且可能误杀用户有意长跑的会话；需定时器 + 重连宽限，难以确定性测试。仅当产品明确要求「关界面即停会话」时再启用。

### 实施节点（Subtask #2）必须遵守的硬约束

1. **仅前端改动**，不得修改 `_ensure_capacity` 与 `config.py` 的上限语义；`test_batch_start_respects_session_limit` 与 `test_batch_start_respects_existing_running_sessions` 必须保持绿色。
2. **对账必须过滤 `state == "running"`**：`list_sessions()` 会返回已 `stopped` 的残留行（`stop()` 不从 `_sessions` 移除），rehydrate 前务必滤掉，否则会把死会话复活进 UI 并误锁选择。
3. **名称/颜色自行解析**：后端行无 `strategyName`（单条会话）与 `color`，需按 `strategyId` 从 `strategies` 查询取名、按序号配色（沿用 `paperMulti.ts:20-35` 的 `sessionsFromBatch` 口径）。
4. **重建批次句柄**：从行内 `groupId` 还原 `paperBatchGroupId` 以支持批量停止；`groupId == null` 走单条停止（`StrategyPanel.tsx:429-446` 已支持）。
5. **不得把运行期句柄写回持久化 store**：对账数据来自服务端实时拉取，`partialize` 的排除（`store.ts:184-198`）必须维持。
6. **inst/bar 可能与当前图表不一致**：对账出的会话要照常展示并允许停止，不可假设其与当前选择匹配。

**最终推荐：候选 A（挂载即对账，过滤 running）+ 候选 C（429 优雅恢复）作为安全网。** 这是能消除用户可见 Bug、且破坏面最小的单一方案。
