# 实时模拟：当前持仓与模拟资金未按策略显示 / 不更新

> Diagnosis only — this node does not change code and contains no commits.
> Symptom slug: `multi-strategy-paper-position-and-funds-not-shown`.

## Problem description

用户报告（实时模拟 / paper 模式）：

> 启动实时模拟以后，真实情况下它模拟已经发生了相应的交易操作，然后**成交记录**对应
> 的页签中已经有了对应的成交记录，但是它右边的**当前持仓**却没有显示对应的不同交易
> 策略的当前持仓；并且其**模拟资金**也没有对应发生变化——模拟资金应当根据不同交易
> 策略分别显示各自的资金变动情况。

Translation: after starting the live paper simulation, trades do happen and the
**成交记录 (trades)** tab is populated, but the adjacent **当前持仓 (current
position)** view does not show the running strategies' positions, and the
**模拟资金 (simulated funds / equity)** never changes — the user expects funds to
be shown and to move *per strategy*.

Expected behaviour: while one or more paper strategies run, the user should be
able to see, **per strategy**, the current open position (side / size / entry /
mark / unrealized PnL) and the live account equity (净值 / 资金) as it changes.

Actual behaviour: trade rows appear, but the position card frequently reads
「当前无持仓」 and the only "模拟资金" figure on screen never moves, so the feature
looks broken even though the simulation engine is trading correctly.

## Scope established up front (backend is correct)

The backend paper engine is **not** the cause. It computes and streams per-strategy
trades, *changing* equity, and a live mark-priced position. This was reproduced
live against real OKX data (see *Reproduction* → B). The defect is entirely in how
the **frontend surfaces** that per-strategy data.

## Reproduction steps

All commands below were actually run on Windows from `M:/takoAI/AragonTrading`
against the already-running backend (`/api/health` → `{"okx":"up", ...,
"maxPaperSessions":12}`). Scripts live under `tmps/`.

### A. UI reproduction

1. Launch the app (`pnpm dev` → `http://localhost:18761`, or the packaged desktop
   app). The store defaults to `bottomTab = "trades"` and `chartPane = "price"`
   (`apps/web/lib/store.ts:97`).
2. Header → switch account to 「模拟盘」 (`AccountModeSwitch` → `setMode("paper")`).
3. In the right panel select several strategies (e.g. via 全选) and click
   「开始模拟」.
4. Watch the bottom tab row 「成交记录 | 当前持仓」 and the header 「模拟资金」 chip:
   - 「成交记录」 fills with trade rows (and the right-panel 「成交明细」 also fills via
     a 3 s DB poll, `StrategyPanel.tsx:155-160`).
   - 「当前持仓」 frequently shows 「当前无持仓」 for the selected strategy, and there is
     no view that lists *all* running strategies' positions at once.
   - 「模拟资金」 in the header stays at the starting number (e.g. `10,000`) and never
     moves. The live net-value curve only exists on the non-default 「净值」 chart
     sub-tab.

### B. Backend stream observation — the data the UI needs is all present

`tmps/paper_ws_observe.py` starts a live paper session
(`strategyId=3 ema_cross_scalper`, `BTC-USDT-SWAP`, `1m`, priming on), connects to
`/api/ws/paper/{sid}` (exactly what `page.tsx` does), and tallies events for 18 s:

```bash
cd services/quant && .venv/Scripts/python.exe -u ../../tmps/paper_ws_observe.py
```

Output (real OKX data):

```text
=== event type counts over 18.0 s ===
{'status': 1, 'snapshot': 1, 'candle': 28, 'equity': 11, 'position': 11}

=== snapshot ===
  trades: 35 | sides: ['close', 'long', 'short']
  equity points: 300
  first equity: {'ts': 1781954760, 'equity': 10000.0, 'drawdown': 0.0}
  last equity : {'ts': 1781972700, 'equity': 9804.657787598533, 'drawdown': -0.0195...}
  position: {'side': 'long', 'size': 0.1225..., 'avgEntry': 64047.10686,
             'markPrice': 64034.3, 'unrealizedPnl': -1.5693...}
  status: {'state': 'running', 'warmedBars': 300, 'seededTrades': 35, ...}

=== live (post-snapshot) ===
  live position events: 11 | sample: [{'side': 'long', ... 'markPrice': 64037.5,
                                        'unrealizedPnl': -1.177...}, ...]
  live equity events: 11 | transient: 11 | sample: [{'ts': ..., 'equity': 9805.04..., 'transient': True}, ...]
```

Key facts: the snapshot already carries a **non-flat position**, **35 trades**, and
**300 equity points whose value moved from 10000 → 9804.66**; and after the
snapshot the session pushes **live `position` events** (mark-priced, updating
unrealizedPnl) and **live transient `equity` events** (changing every tick). So
everything the UI needs to render per-strategy position and changing funds is on
the wire.

### C. The frontend position math is correct — and a strategy can be flat *with* trades

`tmps/derive_position_check.py` reads each session's WS snapshot and runs a faithful
port of `apps/web/lib/position.ts` `deriveOpenPosition()` over `snapshot.trades`,
comparing to the backend broker truth (`snapshot.position`):

```bash
cd services/quant && .venv/Scripts/python.exe -u ../../tmps/derive_position_check.py
```

Output:

```text
strategy 1: 0 trades   backend pos: None                                   frontend derived: None                       MATCH: True
strategy 2: 0 trades   backend pos: None                                   frontend derived: None                       MATCH: True
strategy 3: 37 trades  backend pos: {short, size 0.12247446, @63953.5067}  frontend derived: {short, 0.12247446, @63953.5067}  MATCH: True
strategy 4: 10 trades  backend pos: None                                   frontend derived: None                       MATCH: True
```

Two conclusions:
- `deriveOpenPosition()` is **correct** — it exactly reproduces the backend broker
  position (strategy 3: short 0.1225 @ 63953.5).
- **Strategy 4 has 10 trade records but a flat current position** (`None`). This is
  the normal state of the engine's whole-open / whole-close strategies *between
  round-trips* (`simulator.py:211-246`: each `_open` is later fully closed by
  `_close_at`, netting to flat). So 「成交记录」 is populated while 「当前持仓」 honestly
  shows 「当前无持仓」 — exactly the reported symptom, and it is data-correct, not a bug
  in the math.

## Root cause analysis

The backend is correct (B) and `deriveOpenPosition` is correct (C). The symptoms
come from **three frontend presentation/wiring gaps** that prevent the
already-available per-strategy position and equity from being seen.

### Root cause 1 — the bottom 「当前持仓」 shows only the *active* strategy, derived from trades, so it is empty whenever that strategy is momentarily flat

The bottom area renders one panel for the active strategy only, and feeds the
position card the **same trade array** as the trades tab:

`apps/web/components/Chart/ChartWorkspace.tsx:233-237`

```tsx
{bottomTab === "trades" ? (
  <TradesTable trades={bottomTrades ?? overlayTrades} />
) : (
  <PositionPanel trades={bottomTrades ?? overlayTrades} markPrice={markPrice} instId={instId} />
)}
```

`bottomTrades` is the **active** strategy's trades only
(`apps/web/app/page.tsx:341-345`,`:370`), and `PositionPanel` re-derives the
position from them (`apps/web/components/Strategy/PositionPanel.tsx:18`), returning
the 「当前无持仓」 empty state when the net is flat
(`PositionPanel.tsx:20-25`; `apps/web/lib/position.ts:69` returns `null` when
`|size| <= EPS`).

Consequences, all confirmed in C:
- If the active strategy is currently flat (strategy 4: 10 trades, flat), the panel
  reads 「当前无持仓」 even though trades exist.
- A strategy that *is* holding (strategy 3: short) is invisible unless it is the
  selected active tab. There is **no consolidated "positions for all running
  strategies" view**, so the user cannot see the different strategies' positions
  "分别" / side-by-side, which is precisely what was requested.

### Root cause 2 — the authoritative live per-strategy position from the backend is collected but never rendered

The backend's live `position` events and `snapshot.position` (mark-priced, with
`unrealizedPnl` — see B and `services/quant/app/paper/session.py:361-370`
[`_position_payload` → `{side, size, avgEntry, markPrice, unrealizedPnl}`],
`:342-344` [pushes `{"type":"position", ...}`], `:372-382` [`snapshot()` embeds
`"position": self._position_payload()`]) are merged into frontend state:

`apps/web/app/page.tsx:94-96` (declared), written at `:516-519` (snapshot) and
`:547-552` (live `position` event):

```ts
const [paperPositionsByStrategyId, setPaperPositionsByStrategyId] = useState<
  Record<number, PaperPositionEvent | null>
>({});
```

But this state is **never read anywhere** — a repo-wide search returns only the
declaration plus the design spec:

```text
apps/web/app/page.tsx:94:  const [paperPositionsByStrategyId, setPaperPositionsByStrategyId] = useState<
docs/plans/multi-strategy-paper-simulation/spec.md:141: - paperPositionsByStrategyId: Record<number, PaperPositionEvent | null>
```

So the live, mark-priced, per-strategy position (which exists for every running
strategy, flat or not, and is what the user actually wants to "分别显示") is thrown
away in favour of the active-only, trade-derived reconstruction of Root cause 1.

### Root cause 3 — the only on-screen "模拟资金" is the static starting-capital *input*; live equity is hidden on a non-default sub-tab

The one element labelled 「模拟资金」 is the editable starting-capital field, bound to
the persisted `simBalance` input — by design it never reflects live equity (its own
tooltip says 「下次启动生效」):

`apps/web/components/Header/SimBalanceField.tsx:80-81`

```tsx
<span className="text-[var(--muted)]">模拟资金</span>
<span className="tabular font-semibold ...">{fmtMoney(simBalance)}</span>
```

`simBalance` is the start-capital input, clamped on edit only
(`apps/web/lib/store.ts:101`,`:134-139`); it is passed to the engine as
`initialCapital` at start and is never updated from the stream. So "模拟资金" is
structurally incapable of moving during a session.

The live per-strategy equity *does* arrive and *does* change (B: 10000 → 9804.66)
and is stored in `paperEquityByStrategyId`
(`apps/web/app/page.tsx:88-90`,`:510-515`,`:528-533`), but it is only rendered as a
净值 line on the **「净值」 chart sub-tab**, which is not the default view:
`overlayEquity`/`overlays` feed `EquityChart`/`ChartHeaderBar` only when
`chartPane === "equity"` (`apps/web/components/Chart/ChartWorkspace.tsx:166-189`,
`:111-117`), while the store default is `chartPane === "price"`
(`apps/web/lib/store.ts:97`). There is **no per-strategy "current funds / 净值"
figure** anywhere in the always-visible UI.

### Why the symptom is robust (not a connectivity fluke)

These three gaps make positions look missing and funds look static **even when the
paper WebSocket is working perfectly** (as it does — B). The bug therefore does not
depend on any transport problem; it is purely a presentation/wiring gap.

### Secondary observation (not the primary cause) — WebSocket host default

Independently, the browser WebSocket base defaults to `ws://localhost:18760`
(`apps/web/lib/ws.ts:6`) because `NEXT_PUBLIC_PY_WS_URL` is never set anywhere in
the repo (grep: only a comment in `services/quant/app/main.py:75`), whereas the
backend binds **IPv4 `127.0.0.1` only** (`desktop/scripts/backend_entry.py:5`,`:31`;
default uvicorn host) and the HTTP proxy is deliberately pinned to `127.0.0.1`
(`desktop/src/lib/config.js:62`, `webEnv.PY_BACKEND_URL`). On Windows `localhost`
can resolve to IPv6 `::1` first; Chromium/Electron normally falls back to
`127.0.0.1`, and both prior live diagnoses show the WS connecting, so this is **not**
the proven cause here. It is recorded only as a hardening note: if a desktop/Windows
environment ever fails the `::1`→`127.0.0.1` fallback, the paper WS (and market WS)
would silently fail while the HTTP DB poll still works, which would *also* empty the
bottom panel — worth pinning to `127.0.0.1` for parity, but out of scope for this
bug.

## Impact surface

- **当前持仓 (bottom tab), paper mode** — shows only the active strategy and only when
  it is non-flat; no all-strategies view; the authoritative live position is unused.
  Affects every paper session, single- or multi-strategy.
- **模拟资金 (header) / live equity** — never changes during a session; live
  per-strategy equity is only a curve on the non-default 净值 sub-tab; no per-strategy
  funds figure. Affects every paper session.
- **Multi-strategy paper especially** — the explicit "分别显示不同策略" expectation is
  unmet: positions are one-at-a-time and funds are not surfaced per strategy.
- **Not affected**: backend trade/equity/position computation and streaming (proven
  correct, B/C); the 成交记录 / 成交明细 trade tables; backtest/compare views; the
  净值 chart curves themselves (which do update on their sub-tab).

## Candidate fixes

| # | Fix | Invasiveness | Risk | Effort | Resolves positions | Resolves funds |
|---|-----|---|---|---|---|---|
| A | In the bottom 「当前持仓」 panel, render the already-collected `paperPositionsByStrategyId` for the active strategy (live, mark-priced) instead of the trade-derived value; keep `deriveOpenPosition` only as a fallback for the pre-first-event / seeded window. | Low | Low | Low | Partially (active only) | No |
| B | Replace the active-only 「当前持仓」 with a **per-strategy list** (one row per running strategy: name/color, side, size, avgEntry, markPrice, unrealizedPnl **and** current 净值/funds), sourced from `paperPositionsByStrategyId` + last point of `paperEquityByStrategyId`, with `deriveOpenPosition` fallback. Keep `SimBalanceField` as the start-capital input. | Medium | Low | Medium | Yes (all strategies) | Yes (per strategy) |
| C | Add a separate per-strategy "资金/净值" summary (e.g. in the right `PaperRunningList`) driven by `paperEquityByStrategyId`, and leave 「当前持仓」 as-is but switch it to `paperPositionsByStrategyId`. | Medium | Low–Med | Medium | Partially (active only) | Yes (per strategy) |

Notes:
- All three reuse data **already in `page.tsx` state**; none requires backend or
  WebSocket changes.
- Fix A is the minimal correctness step (use the authoritative live position) but
  does not satisfy the "分别显示不同策略" requirement on its own.
- The static `SimBalanceField` should remain an input; do **not** repurpose it as a
  live readout (it would conflate the start-capital control with account equity).

## Recommended fix

Adopt **Fix B**, with **Fix A's** "use the authoritative `paperPositionsByStrategyId`,
fall back to `deriveOpenPosition`" rule as its per-row data source.

Concretely: turn the bottom 「当前持仓」 tab (paper mode) into a compact per-strategy
table — one row per running strategy showing the strategy name/color, position
(side / size / avgEntry / markPrice / unrealizedPnl) from
`paperPositionsByStrategyId[id]` (falling back to
`deriveOpenPosition(paperTradesByStrategyId[id], markPrice)` until the first
`position`/snapshot event arrives), and the strategy's current funds/净值 from the
last point of `paperEquityByStrategyId[id]`. Keep the single-strategy backtest path
(`overlayTrades` → `PositionPanel`) unchanged. Leave `SimBalanceField` as the
editable starting capital.

Justification:

1. **Directly matches the request.** The user asks to see, "分别"/per strategy, both
   the current positions and the changing funds. A per-strategy row list is exactly
   that, and it removes the "only the active, often-flat strategy is visible"
   limitation (Root cause 1).
2. **Uses data already present and proven.** `paperPositionsByStrategyId` and
   `paperEquityByStrategyId` are already populated from the backend stream (B); the
   fix is pure presentation — no backend, schema, or WebSocket change, lowest risk.
3. **Stops discarding the authoritative position.** Rendering
   `paperPositionsByStrategyId` (mark-priced, correct `unrealizedPnl`) is strictly
   better than the trade-derived reconstruction, while the `deriveOpenPosition`
   fallback preserves the honest "当前无持仓" semantics for genuinely-flat strategies
   and the brief seeded window before the first live event.
4. **Funds become visible and live without misusing the input.** Surfacing each
   strategy's latest equity as its 净值/funds figure makes "模拟资金…根据不同交易策略分别
   显示其变动" true, while `SimBalanceField` stays the start-capital control it is
   designed to be.

### Suggested regression tests (for the implementing node)

- Frontend (pure): given `paperPositionsByStrategyId` with a non-flat entry, the
  bottom panel renders that strategy's side/size/avgEntry/unrealizedPnl; given a
  `null`/missing entry but non-empty trades, it falls back to `deriveOpenPosition`;
  given a flat net, it honestly shows 「当前无持仓」.
- Frontend (pure): the per-strategy funds figure equals the last
  `paperEquityByStrategyId[id]` point and updates when a new equity point is merged
  (reuse `mergePaperEquity` from `apps/web/lib/paperMulti.ts`).
- Backend (already green, keep as guardrails): `test_paper_priming.py`
  `test_snapshot_matches_broker_state` (snapshot position/equity consistency) and the
  intrabar transient-equity test continue to pass.

## 评审结论

> 评审节点（Subtask #1）：对上游分析做独立证据复核，逐条核对引用代码，审计候选修复，
> 敲定单一推荐方案。本节只读代码、不改业务代码、不提交。

### 一、根因证据复核（结论：证据链成立，已就地补强/修正引用）

逐条复核了报告引用的每一处代码，结论是三条根因均成立、可被独立验证；仅有少量行号/路径
不精确，已在文内修正或在此固定：

- **根因 1（底部「当前持仓」仅显示活动策略、由成交反推、瞬时平仓即空）—— 成立。**
  独立核对：`ChartWorkspace.tsx:233-237`（`PositionPanel` 收到 `bottomTrades ?? overlayTrades`）、
  `page.tsx:341-345`（`activePaperTrades` 取 `paperTradesByStrategyId[effectiveActivePaperStrategyId]`，
  即**仅活动策略**）、`page.tsx:370`（`bottomTrades = mode==="paper" ? activePaperTrades : overlayTrades`）、
  `PositionPanel.tsx:18,20-25`（由 `deriveOpenPosition(trades)` 反推，平仓时返回「当前无持仓」）、
  `position.ts:69`（`|size| <= EPS` 返回 `null`）。全部逐字吻合。

- **根因 2（权威 `paperPositionsByStrategyId` 被采集但从不渲染）—— 成立且最关键，证据已加固。**
  本评审独立用「排除 setter」的检索确认：**读取侧 `paperPositionsByStrategyId`（非 `setPaperPositionsByStrategyId`）
  在整个 `apps/web` 中除 `page.tsx:94` 声明外出现 0 次** → 确为「写而不读」。写入点已精确固定为
  `page.tsx:516-519`（snapshot 分支）与 **`page.tsx:548-552`（live `position` 事件分支，setter 在第 548 行）**；
  报告原文「`:547-552`」中 setter 实际落在 548 行，已据此精确化。后端发射侧引用原为
  `session.py:288-297/267-271/516`（路径缺目录且行号指向 K 线处理，非持仓），已就地更正为
  `services/quant/app/paper/session.py:361-370`（`_position_payload` 产出
  `{side,size,avgEntry,markPrice,unrealizedPnl}`，其中 `unrealizedPnl = broker.equity_at(mark) - broker.cash`）、
  `:342-344`（推送 `{"type":"position"}`）、`:372-382`（`snapshot()` 内嵌 `"position"`）。

- **根因 3（唯一在屏「模拟资金」是静态初始本金输入；实时净值藏于非默认子页签）—— 成立。**
  `SimBalanceField.tsx:80-81` 绑定 `fmtMoney(simBalance)`，第 83 行 tooltip「下次启动生效」自证其为
  *输入控件*而非实时读数；`store.ts:101` `simBalance: DEFAULT_SIM_BALANCE`、`:134-139` 仅在编辑时夹取，
  从不被流更新。实时净值 `overlayEquity` 仅在 `chartPane === "equity"` 时喂给 `EquityChart`
  （`ChartWorkspace.tsx:182-189`），而 store 默认 `chartPane: "price"`（`store.ts:96`），故默认视图下不可见。
  注：`paperEquityByStrategyId` **确被读取**（`page.tsx:351-354,363`），但只用于净值曲线，无任一处把它渲染成
  「按策略的当前资金读数」——与报告判断一致。

- **后端正确性前提 —— 成立（实测 + 代码双重支撑）。** 复现 B/C 已实证 WS 含快照持仓、逐笔 `position`、
  变动 `equity`（10000→9804.66）；代码侧 `simulator.py:211-246` 证实「整开整平、平仓即 `self.pos=_Position()`、
  `position_after=0.0`」，这正解释了 C 中「策略 4 有 10 笔成交但当前平仓」是**数据正确**而非缺陷。

### 二、反例与替代解释审查

- **WS 连接故障假说**：已被 B 排除（WS 正常推流）；报告将 `localhost`→IPv6 仅列为硬化备注、非本 bug 根因，判断正确，保持出范围。
- **遗留证据缺口（不影响结论，供实现节点注意）**：复现 B 仅对**单条**会话（`strategyId=3`）抓流；
  多条并发会话各自填充 `paperPositionsByStrategyId[id]` 是由按会话键写入（`page.tsx:516/548` 的 `[session.strategyId]`）
  *结构性推断*得出，未对 N>1 直接抓流复现。该路径结构上成立（每会话一条 WS 订阅、各写各槽），但实现/验收时应实测多策略并发下每条策略槽位均被填充。

### 三、候选修复审计

- **最小性**：用户明确要求「分别」按策略展示**持仓与资金**。Fix A（仅活动策略）与 Fix C（持仓仍只活动策略）
  均无法满足「持仓按策略并列」，故不足。**Fix B 是满足完整需求的最小改动**——推荐成立。
- **边界覆盖**：① 真平仓 → 回退 `deriveOpenPosition` 返回 `null` →「当前无持仓」诚实显示 ✓；
  ② 首个 `position`/snapshot 事件前的窗口 → 回退成交反推 ✓；③ 净值取 `paperEquityByStrategyId[id]` 末点，
  注意 live equity 为 `transient`（见 B），应经 `mergePaperEquity` 取末点 ✓。
- **本评审新增的关键边界——多合约**：成交反推回退路径用的是**图表当前 instId 的单一 `markPrice`**
  （`ChartWorkspace.tsx:103`）。若某策略交易的合约 ≠ 图表合约，回退路径算出的 `markPrice/unrealizedPnl` 会**错配**。
  而权威 `paperPositionsByStrategyId[id]` 自带后端各策略自身的 `markPrice`（`session.py:369-370`），跨合约天然正确——
  **这是优先采用 Fix B 主数据源、而非成交反推的又一硬理由**。遗留小注意点：对非图表合约策略，在其首个 `position` 事件
  到达前短暂回退期内 mark/PnL 可能偏差（side/size/avgEntry 仍正确），首个事件到达即自愈，可接受。
- **回归面**：纯前端呈现改动，零后端/WS/schema 改动 → 风险最低；须保留回测单策略路径
  （`overlayTrades → PositionPanel` 不动）。`PositionPanel` 现仅接收 `trades` 而非 position 对象，
  Fix B 需新建按策略列表组件（非简单换 prop），故「Medium 侵入性」评级准确。监视项：保留底部成交页签的
  按策略 tab 选择器（`ChartWorkspace.tsx:208-231`）行为；单策略 paper 应渲染为干净的单行表。

### 四、最终推荐修复（单一）

**采用 Fix B：把底部「当前持仓」页签（paper 模式）改造为「按策略列表」**——每条运行中策略一行，
展示 策略名/色、方向、持仓量、开仓均价、标记价、浮动盈亏，以及该策略**当前净值/资金**；
并叠加 **Fix A 的数据源规则**：每行持仓**优先取权威 `paperPositionsByStrategyId[id]`，仅在其为空（首个事件前）
回退 `deriveOpenPosition(paperTradesByStrategyId[id], markPrice)`**；资金取 `paperEquityByStrategyId[id]` 末点
（经 `mergePaperEquity`）。保持回测路径 `overlayTrades → PositionPanel` 与 `SimBalanceField`（初始本金输入）不变。

**理由（单一方案的充分性）**：
1. **直接命中诉求**：按策略行列表正是「分别显示不同策略的持仓与资金」，并消除「只看到活动且常平仓策略」的根因 1。
2. **复用已有且已证的数据**：`paperPositionsByStrategyId` / `paperEquityByStrategyId` 已由后端流填充（B），纯呈现改动、风险最低。
3. **停止丢弃权威持仓且跨合约正确**：渲染后端 mark-priced 持仓严格优于成交反推（尤其多合约场景，见三·多合约），
   `deriveOpenPosition` 回退保留真平仓的诚实「当前无持仓」语义与首事件前窗口。
4. **资金可见且实时、且不滥用输入**：把各策略最新净值作为其资金读数，使「模拟资金按策略分别显示其变动」成真，
   而 `SimBalanceField` 仍是其设计本职——初始本金控件。

**移交实现节点的硬约束**：只改前端；新增按策略持仓/资金列表组件，主数据源 `paperPositionsByStrategyId`、
回退 `deriveOpenPosition`、资金取 `paperEquityByStrategyId` 末点；不动后端/WS/schema、不动回测 `PositionPanel` 路径；
并补「报告建议的回归测试」+ 多策略并发下各策略槽位均被填充的实测。
