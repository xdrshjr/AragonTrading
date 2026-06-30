# 自动交易收益记录（auto-trade-pnl-records）开发方案

> 版本：**v2**（方案评审与修复，Subtask #1 产出；基线 v1 由 Subtask #0 设计）
> 作者：solution architect（Anthropic 工程团队）｜评审：senior reviewer（Anthropic 工程团队）
> 适用仓库：`M:/takoAI/AragonTrading`（monorepo：`apps/web` Next.js 14 + `services/quant` FastAPI）
> 关联既有方案：`docs/plans/live-auto-trading/spec.md`、`docs/plans/watchlist-auto-trade-group/spec.md`、`docs/plans/auto-trade-explainability/spec.md`

---

## 评审记录（Review Notes，v2）

> 本节由 Subtask #1（方案评审与修复）填写。评审已逐节对照真实源码核对 v1 的全部代码断言（行号、函数签名、数据结构），结论：**v1 接地扎实，绝大多数断言与源码一致**（详见下「已核实正确的关键断言」）。下表列出发现的问题与处置；所有 **P0/P1 已就地修复**，文档升版 v2。

### 评审维度小结

- **可行性（Feasibility）**：核心改动均可在现有栈落地。后端 `pnlPct = _close_pnl / 开仓保证金 = 方向化价格变动% × 杠杆`，输入（`entry_qty_sz`/`ct_val`/`entry_price`/`leverage`）在复位前有效，已核实；前端复用 `buildTradeTrailSegments`、store `liveAutoGroup` 形状、`Modal`/格式化器均存在。仅 §3.2 的除零守卫写反（见 P1-2）。
- **完整性（Completeness）**：主要缺口在「时间维度筛选 vs 瞬态数据范围」的矛盾（P1-1）与「当前浮盈来源」的口径含混（P1-3）。
- **一致性（Consistency）**：与 CLAUDE.md「不伪造数据 / 单一数据源 / 显式降级」一致；个别处需对齐着色与单位标签（P2-4、P2-5）；`_make_trade` 形参既已远超 ≤5 阈值（P2-3，既有债，沿用 keyword-only 命名参数模式）。
- **合理规模（Right-sizing）**：范围与需求匹配，未过度设计；时间筛选的真实价值受瞬态数据范围制约，已在 P1-1 显式标注为已知限制（不扩范围建后端）。

### 问题清单与处置

| 编号 | 维度 | 严重度 | 问题 | 处置 |
|---|---|---|---|---|
| P1-1 | 完整性/诚实性 | **P1** | 「今日/近7日/近30日/全部」时间筛选依赖 `liveAutoGroup`，但其为**瞬态**、整组停止 `clearLiveAutoGroup` 即清。对刚启动的组，「近30日/全部」实际只覆盖**本组启动以来**的成交，时间筛选名不副实、易误导用户以为是跨会话历史。 | **已修复**：§3.4/§3.6/§7.3/§8 明确数据覆盖边界——面板顶部显式展示「数据自 `group.startedTs` 起」，早于 `startedTs` 的窗口给出明确空态说明；历史跨会话回填（`/runs/{id}/trades`）由「后续增量」升格为**已知限制**并保留为范围外（不在本期扩后端）。 |
| P1-2 | 可行性/代码正确性 | **P1** | §3.2 的 `_close_return_pct` **先做除法再判 `margin<=0`**；若 `self.leverage==0` 则在除法处抛 `ZeroDivisionError`。该计算位于 `_settle_close`（**未被 try/except 包裹**）调用栈，异常会冒泡污染会话 tick。虽 leverage≥1 由启动校验保证，但守卫写法本身无效。 | **已修复**：§3.2 重写为**先校验后相除**（`leverage<=0` 或名义价值非正 → 直接 `return None`），使守卫真正生效；不新增多余防御，仅修正既有意图。 |
| P1-3 | 完整性/正确性 | **P1** | §3.4「当前持仓浮盈」用减法式 `Σ sessionPnlUsd − Σ 已实现`，「Σ 已实现」口径含混：若误用**窗口筛选后**的已实现，则非「全部」窗口下浮盈数被算错；与 R3「避免已实现/浮盈误读」目标自相矛盾。 | **已修复**：§3.4/§6.3 改为**直接** `Σ member.position?.unrealizedPnl ?? 0`（`executor.position_payload` 已提供 `unrealizedPnl`，store `member.position` 承载），与时间窗口解耦，口径无歧义。 |
| P2-1 | 一致性 | P2 | 成员**部分停止**后，总收益率分母仍用 `totalBudgetUsd`（原始投入），属「对投入资本的回报」，可能被误读为「当前在投资本回报」。 | 维持口径（与 `_finalize_run` 同源），§6.3 注明分母为「原始投入预算」，UI 标注。 |
| P2-2 | 正确性 | P2 | `summarizeRoundTrips` 在 `count==0` 时 `winRate=wins/count=NaN`（`fmtPct` 已兜底显示「—」，不崩）。 | §3.3 显式 `count===0 → winRate=0`，避免 NaN 外泄。 |
| P2-3 | 一致性 | P2 | `_make_trade` 形参已 12 个、远超 CLAUDE.md ≤5 阈值；新增 `pnl_pct` 为 13。 | 沿用既有 **keyword-only 命名参数**模式（CLAUDE.md 认可的逃逸方式），不新增位置参数债；此既有越阈在本评审显式记录。 |
| P2-4 | 一致性 | P2 | §3.5 称底部 `TradesTable` 盈亏单元「复用 `colorForReturn`」，但现状为**内联三元着色**。 | §3.5 明确：实现时统一改用 `colorForReturn(t.pnl)`，与新面板着色一致。 |
| P2-5 | 一致性 | P2 | 头部 `aggregatePnl` 用 `fmtMoney`（渲染 `¥` 符号）展示 USD 量，新面板用 `USDT/signed`，单位标签不一致。 | §3.4 注明：新面板统一 **USDT** 口径，**不**向新面板传播 `fmtMoney` 的 `¥` 符号；头部既有 `¥` 显示属既有问题、不在本期范围。 |
| P2-6 | 完整性 | P2 | `nowSec` 渲染期取一次，模态长开跨午夜/窗口边界不刷新；原文「如需可加 30s tick」措辞含混。 | §3.4 明确**不加 tick** 为有意决策（模态生命周期短），去掉模糊措辞。 |

### 已核实正确的关键断言（无需修改）

- 后端 `_close_pnl`（`executor.py:361-367`）、`_make_trade`（`:390-408`，`*` 后 keyword-only，加 `pnl_pct` 不破坏调用）、`_settle_close`（`:212-224`）、`_synthesize_external_close`（`:442-456`）行号与形态与 v1 描述**完全一致**；`_settle_benign_close`/`_on_reconcile_ok` 经 `_synthesize_external_close` 自动覆盖，无需单独改点。
- R1 落库安全：`append_live_trade`（`repositories.py:268-286`）**逐字段 `trade.get(...)` 显式映射、无 splat**，新增 `pnlPct` 键被静默忽略，**不破坏落库**，已核实。
- 前端 `buildTradeTrailSegments`（`tradeTrails.ts:25`）返回 `{id,direction,entry,exit,pnl,isWin,...}`，且**逐流配对、不跨 instId**（`buildLedger` 逐成员调用 → R5 成立）；`Trade`/`TradeTrailSegment` 接口（`types.ts:257/282`）、`fmtPct`/`signed`/`colorForReturn`（`format.ts`）、`Modal`（`ui/index.tsx:249`）、store 形状（`store.ts:244-264`，`liveAutoGroup` 瞬态、不入 partialize）、`_finalize_run.totalReturn=realized/budget`（`session.py:482`）与 `snapshot()`（`:459-464`）均与 v1 描述一致。

---

## 1. 概述（Overview）

本项目的「自动交易」由 AI/策略驱动，对自选分组（watchlist auto-trade group）中的每个标的各开一条 `LiveAutoSession`，经 `LiveExecutor` 下真实 OKX 订单并产生**成交记录**（trade，含开仓 long/short 与平仓 close）。当前每一笔**平仓**成交已携带绝对已实现盈亏 `pnl`（USDT），并在底部「成交记录」`TradesTable` 的「盈亏」列以 `signed(pnl)` 展示。但用户反馈的两个缺口至今**在任何层都不存在**：

1. **缺少每笔完整交易的收益百分比**。一次完整交易 = 一对「开仓 → 平仓」往返（round-trip）。用户希望一眼看到「这一次交易赚/亏了多少钱 + 收益率百分比」，而现有记录只有绝对金额、没有百分比，且开仓行与平仓行割裂、不直观成「一次完整交易」。
2. **缺少按时间维度的总收益 / 总收益率汇总**。用户希望在收益记录的**最上方**用时间维度（今日 / 近 7 日 / 近 30 日 / 全部 / 自定义）筛选，查看该窗口内的**总收益（USDT）**与**总收益率（%）**，对整组自动交易的盈利情况一目了然。

本方案以**最小且向后兼容**的改动补齐上述能力：在后端 `LiveExecutor` 计算每笔平仓的收益率 `pnlPct`（已实现盈亏 / 该笔开仓所占用保证金，等价于「价格变动% × 杠杆」），通过既有 WS 通道无缝透出（snapshot 重放 + 实时 trade 事件）；在前端新增一个**纯函数收益聚合库** + 一个**「自动交易收益」记录面板（Modal）**，把分组各成员的成交流配对为「完整交易」往返、按时间窗口筛选并汇总出总收益与总收益率，同时在既有底部 `TradesTable` 的「盈亏」列追加百分比展示。设计严格遵循项目「绝不伪造数据、因果时序、单一数据源、显式降级」的既有约定：百分比与汇总均由真实成交（真实 OKX 下单回执 + 真实标记价）派生，不引入任何模拟/估算的「看起来像收益」的数字。

整改后用户在自动交易区点击「收益记录」即可打开一个美观、信息密度合理、符合 HCI 最佳实践（顶部时间筛选 segmented control + KPI 概览卡 + 可展开的完整交易明细 + 当前持仓浮盈条 + 空态引导）的面板，既能看到**每一次完整交易的盈亏金额与收益率**，也能在所选时间窗口看到**整组的总收益与总收益率**。

---

## 2. 现状与关键代码路径（Grounding）

> 本节为下游工程师实现前必须理解的真实代码现状，所有结论均经源码核对。

### 2.1 后端成交链路

- `LiveExecutor`（`services/quant/app/live_auto/executor.py`）：
  - 自管持仓状态机字段：`entry_price`、`entry_qty_sz`（张数 contracts）、`leverage`、`ct_val`（合约面值）、`projected`（long/short/flat）、`realized_pnl_usd`。
  - 已实现盈亏：`_close_pnl(exit_px)`（`executor.py:361-367`）= `qty_coin * (exit-entry)`（多）/ `qty_coin * (entry-exit)`（空），`qty_coin = entry_qty_sz * ct_val`。
  - 成交字典构造：`_make_trade(...)`（`executor.py:390-408`），返回 camelCase dict：`{ts, side, price, qty, fee, pnl, positionAfter, reason, stopLossPct, takeProfitPct, maxHoldBars, stopLossPrice, takeProfitPrice, seeded, explain}`（+ 可选 `clOrdId`/`ordId`）。**开仓 `pnl=0.0`，平仓 `pnl=_close_pnl(...)`。无任何百分比字段。**
  - 平仓产出点（均在复位 `entry_*`/`projected` 前算好 pnl 并传入 `_make_trade`）：`_settle_close`（`:212-224`，信号/反手/时间止盈/会话停止）、`_synthesize_external_close`（`:442-456`，交易所侧 OCO/外部平仓的只读补记）、`_settle_flatten_ok`（`:327-335`，经 `_settle_close`）。
- `LiveAutoSession`（`services/quant/app/live_auto/session.py`）：
  - **无自管 trade 列表**；成交存于 `executor.trades: list[dict]`（`executor.py:73`）。
  - `_dispatch`（`session.py:110-121`）：对 `{"type":"trade"}` 事件先 `_inject_explain_meta` 注入会话级元信息，再 `_persist_trade`（best-effort 落库），最后 `_push` WS 扇出。
  - `snapshot()`（`session.py:459-464`）：订阅即重放 `{"trades": list(executor.trades), "equity": [...], "position": ..., "status": ...}`。**executor 字典新增的任何键都会随 snapshot 与实时 trade 事件一并透出，无需改 WS 协议结构。**
  - `_persist_trade` → `repo.append_live_trade`（`db/repositories.py:268-286`）：**逐字段显式映射**到 `Trade` ORM（不 splat 整个 dict），故给 trade dict 增加新键**绝不会**破坏落库（新键被静默忽略，不入库）。
  - `_finalize_run`（`session.py:478-490`）：会话停止时落终态指标，其中 `totalReturn = realized_pnl_usd / budget_usd`（`:482`）——**这正是本方案「总收益率 = 已实现盈亏 / 投入预算」的口径来源，前后端一致。**
- 状态/分组接口（`services/quant/app/api/live_auto.py` + `live_auto/manager.py`）：
  - `GET /api/live-auto/group/status` → `{groupId, strategyId, strategyName, strategyKind, bar, totalBudgetUsd, leverage, createdTs, members[]}`，成员行 `{instId, sessionId, runId, allocationPct, budgetUsd, state, positionSide, sessionPnlUsd, degraded, flattenFailed}`。
  - 成员行**不含**逐成员 `leverage`/`startedTs`（仅分组级有 `leverage`/`createdTs`）。本方案的总收益率分母用**分组级 `totalBudgetUsd`**，per-trade 百分比由后端 `pnlPct` 直供，故无需补成员级字段。

### 2.2 前端成交展示链路（用户真正看到的界面）

- 自动交易的真实交互入口是**自选页签的分组面板**：`WatchlistTab`（`apps/web/components/Chart/WatchlistTab.tsx`）→ `AutoTradePanel`（`apps/web/components/Chart/AutoTradePanel.tsx`）。分组运行期态在 `store.liveAutoGroup`（瞬态，刷新由 `GET /live-auto/group/status` 重发现 + 各成员 WS snapshot 重放回填 `members[instId].trades`）。
- `AutoTradePanel` 头部已展示**会话级聚合盈亏** `aggregatePnl = Σ members.status.sessionPnlUsd`（`WatchlistTab.tsx:129-135`，含浮盈、非时间筛选、非 per-trade）。
- 用户看到逐笔成交的唯一界面是**底部「成交记录」`TradesTable`**（`apps/web/components/Strategy/TradesTable.tsx`），由 `ChartWorkspace` 渲染，数据 `overlayTrades = liveAutoGroup.members[当前选中 instId].trades`（`page.tsx:474-475`）——**仅显示当前选中标的的成交**。`TradesTable` 列：时间/方向/价格/数量/**盈亏**(仅 close 显示 `signed(pnl)`)/止损/止盈，含 explain 时整行可展开 `TradeExplainDetail`。**无任何百分比列，无时间汇总。**
- **死代码确认**：单会话组件 `AutoTradeStatusBar`/`AutoTradeTradesTable`/`AutoTradeEventLog`/`AutoTradeToggle` 仅被各自 `.test.tsx` 引用，**生产代码无渲染**；`page.tsx` 的单会话 effects（`:330-377`）仍执行但其写入的 `liveAutoStatus`/`liveAutoEvents`/`liveAutoTrades` 无人渲染或被分组分支遮蔽。**本方案不复活这些死件，新建独立面板。**
- 既有纯函数 `buildTradeTrailSegments(trades)`（`apps/web/lib/tradeTrails.ts:25`）已实现「开仓→平仓」配对（按 ts + id 因果排序、单持仓配对），本方案的往返聚合**直接复用**它。
- 既有格式化器（`apps/web/lib/format.ts`）：`fmtPct(v, digits=2)`、`signed(v, digits=2)`、`colorForReturn(v)`、`fmtPrice`、`fmtTime`、`fmtQty` 均已存在，可直接复用。
- `Modal`（`apps/web/components/ui/index.tsx:249`）：已含焦点陷阱 / Esc / body 滚动锁 / 焦点回还，签名 `{open, onClose, labelledBy?, className?, children}`。
- store 持久化 `version = 22`（`store.ts:620`）；`liveAutoGroup` 为**瞬态**（不入 partialize）。本方案的面板开关 + 选中时间窗口均为**组件局部瞬态 state**，**不改 store、不 bump version**。

---

## 3. 技术设计（Technical Design）

### 3.1 架构总览

```
后端（真实数据源）
  LiveExecutor._make_trade(..., pnl_pct=...)  ← 新增：平仓时算 pnlPct = 已实现盈亏 / 开仓保证金
        │  trade dict 增加可选键 "pnlPct"（仅 close 携带；open 省略）
        ▼
  LiveAutoSession._dispatch → _persist_trade(忽略 pnlPct，不入库) + _push（WS 实时 trade 事件）
  LiveAutoSession.snapshot()（订阅/重连重放 executor.trades，含 pnlPct）
        │  WS：/api/live-auto/ws/{sessionId}（协议结构不变，仅 trade.data 多一个可选字段）
        ▼
前端
  useLiveAutoGroupStream → store.liveAutoGroup.members[instId].trades（含 pnlPct）
        │
        ├─ 既有底部 TradesTable：盈亏列追加「+x.xx (+y.yy%)」（读 t.pnlPct，缺失回退「—」）
        │
        └─ 新增「自动交易收益」面板（Modal）
              lib/autoTradePnl.ts（纯函数）：
                buildRoundTrips(memberTrades) → 完整交易往返（复用 buildTradeTrailSegments）
                resolveRangeBounds(range, nowSec, custom?) → {startTs, endTs}
                filterRoundTrips(rts, bounds)（按 closedTs）
                summarizeRoundTrips(rts, capitalBaseUsd) → {总收益, 总收益率, 笔数, 胜率, 最佳, 最差}
              组件：AutoTradePnlPanel（壳+编排）/ AutoTradePnlSummary（时间筛选+KPI 卡）/ AutoTradeRoundTripRow（一行完整交易，可展开 explain）
```

### 3.2 后端：per-trade 收益率 `pnlPct`

**定义**：一笔平仓的收益率 = 该笔已实现盈亏 / 该笔**开仓时占用的保证金**：

```
entry_notional_usd = entry_qty_sz * ct_val * entry_price          # 名义价值（USDT）
entry_margin_usd   = entry_notional_usd / leverage                # 占用保证金（leverage ≥ 1）
pnlPct             = _close_pnl(exit_px) / entry_margin_usd        # 等价于 方向化价格变动% × 杠杆
```

选此口径的理由：(a) 与会话级 `totalReturn = realized/budget`（`session.py:482`）口径一致——均为「相对投入资本的回报」；(b) 杠杆敏感，符合合约用户对「这笔赚了百分之几」的直觉（如 5x 下 +1% 价格波动 = +5% 保证金回报）；(c) 全部输入（`entry_qty_sz`/`ct_val`/`entry_price`/`leverage`）在 `_make_trade` 调用前的复位点之前都仍有效。

**实现要点（`executor.py`）**：

1. 新增纯计算方法（≤8 行）。**v2（P1-2）：先校验输入再相除**——`leverage<=0` 或名义价值非正时直接 `return None`，避免在 `_settle_close`（未被 try/except 包裹）调用栈抛 `ZeroDivisionError`：
   ```python
   def _close_return_pct(self, exit_px: float) -> float | None:
       """平仓收益率：已实现盈亏 / 开仓占用保证金；杠杆或名义价值非正（异常断面）返回 None。"""
       notional = self.entry_qty_sz * self.ct_val * self.entry_price
       if self.leverage <= 0 or notional <= 0:   # 先判后除：杜绝除零 / 负保证金
           return None
       return self._close_pnl(exit_px) / (notional / self.leverage)
   ```
2. `_make_trade` 增形参 `pnl_pct: float | None = None`（不超 5 个有效位置参数约束：现有为 keyword-only 段，沿用 `*` 之后的 keyword 形式，不破坏调用）；仅当 `pnl_pct is not None` 时写入 `trade["pnlPct"] = float(pnl_pct)`（与 `clOrdId`/`ordId` 同样的「缺失即省略键」风格）。
3. 各平仓产出点在**复位前**算 `pnl_pct = self._close_return_pct(ref_px)` 并传入 `_make_trade`：
   - `_settle_close`（`:212-224`）：在 `qty = self.entry_qty_sz` 之后、复位之前计算并透传。
   - `_synthesize_external_close`（`:442-456`）：在 `pnl = self._close_pnl(ref_px)` 之后同点计算并透传。
   - `_settle_flatten_ok` 经 `_settle_close`，自动覆盖。
4. **开仓**调用 `_make_trade` 不传 `pnl_pct`（保持 open 无 `pnlPct` 键）。

**不改动**：WS 协议结构、`schemas.py`、`append_live_trade`、`Trade` ORM、DB schema（**无迁移**）。`pnlPct` 是派生量，仅存在于运行期 WS/内存成交字典；落库的 DB 行不含该列（显式字段映射天然忽略），历史 `runs/{id}/trades` 路径不受影响、不展示百分比（见 §8 范围边界）。

### 3.3 前端：纯函数收益聚合库 `lib/autoTradePnl.ts`

全部为无副作用纯函数，单测覆盖（`autoTradePnl.test.ts`），不依赖 React/store：

```ts
import type { Trade } from "./types";
import { buildTradeTrailSegments } from "./tradeTrails";

export type AutoTradeRange = "today" | "7d" | "30d" | "all" | "custom";

export interface RoundTrip {
  instId: string;
  direction: "long" | "short";
  entry: Trade;
  exit: Trade;
  pnlUsd: number;       // = exit.pnl（后端真实已实现）
  pnlPct: number | null;// = exit.pnlPct ?? 回退（见下）
  openedTs: number;     // entry.ts
  closedTs: number;     // exit.ts（用于时间窗口筛选 + 排序）
  isWin: boolean;       // pnlUsd > 0
}

export interface RangeBounds { startTs: number; endTs: number; } // 秒级 UTC，闭区间

export interface PnlSummary {
  totalPnlUsd: number;     // Σ window 内 round-trip pnlUsd
  totalReturnPct: number;  // totalPnlUsd / capitalBaseUsd（capitalBaseUsd<=0 → 0）
  count: number;           // window 内完整交易笔数
  wins: number; losses: number; winRate: number; // [0,1]
  bestUsd: number; worstUsd: number;             // 单笔最大盈 / 最大亏（无则 0）
}

// 单个标的成交流 → 完整交易往返。复用 buildTradeTrailSegments 的因果配对；
// seeded（历史预热）成交在配对前过滤（诚实：非真实成交不计入收益，live-auto 一般无 seeded）。
export function buildRoundTrips(
  instId: string, trades: Trade[], leverage: number,
): RoundTrip[];

// 跨成员合并：传入 [{instId, trades, leverage}]，返回扁平 RoundTrip[]（不跨标的配对）。
export function buildLedger(
  members: { instId: string; trades: Trade[]; leverage: number }[],
): RoundTrip[];

export function resolveRangeBounds(
  range: AutoTradeRange, nowSec: number, custom?: { startTs?: number; endTs?: number },
): RangeBounds;

export function filterRoundTrips(rts: RoundTrip[], bounds: RangeBounds): RoundTrip[];

export function summarizeRoundTrips(rts: RoundTrip[], capitalBaseUsd: number): PnlSummary;
```

实现要点：
- `buildRoundTrips`：先 `trades.filter(t => !t.seeded)`；再 `buildTradeTrailSegments(...)` 得到 `{entry, exit, pnl, isWin, direction}` 段；映射为 `RoundTrip`，`pnlUsd = seg.exit.pnl`，`pnlPct = seg.exit.pnlPct ?? derivePct(seg, leverage)`。
- `derivePct`（**回退**，仅当后端未给 `pnlPct` 时）：`sign = direction==="long" ? 1 : -1`；`pct = sign * (exit.price/entry.price - 1) * leverage`；`entry.price<=0 → null`。优先用后端真实值，回退值用于极少数旧 snapshot/边界情形，保证 UI 不空白。
- `resolveRangeBounds`：`today` = 本地零点（用 `new Date(nowSec*1000)` 归零时分秒再转秒）→ now；`7d`/`30d` = now-N 天 → now；`all` = `{startTs:0, endTs: now}`；`custom` = 传入边界（缺省回退 all）。**`Date.now()` 由调用方（组件）注入 `nowSec`，纯函数不直接读时钟，便于单测。**
- `filterRoundTrips`：`startTs <= closedTs <= endTs`。一笔完整交易在**平仓时刻**归入窗口（开仓可能更早）。
- `summarizeRoundTrips`：单遍归约；`totalReturnPct = capitalBaseUsd > 0 ? totalPnlUsd / capitalBaseUsd : 0`；**v2（P2-2）`winRate = count > 0 ? wins / count : 0`**（显式守卫 `count===0` 的 0/0，避免 NaN 外泄）；空输入返回全零汇总（`bestUsd/worstUsd` 无样本时取 0）。

### 3.4 前端：UI 组件

放置于 `apps/web/components/Chart/`（与自动交易分组簇聚合），跨目录复用 `components/Strategy/TradeExplainDetail`：

1. **`AutoTradePnlPanel.tsx`**（壳 + 编排，~120 行）
   - props：`{ open: boolean; onClose: () => void }`。
   - 读 store：`liveAutoGroup`。构造 `members = Object.values(liveAutoGroup.members).map(m => ({instId, trades, leverage: liveAutoGroup.leverage}))`；`capitalBaseUsd = liveAutoGroup.totalBudgetUsd`。
   - **v2（P1-3）当前浮盈来源**：`unrealizedUsd = Σ member.position?.unrealizedPnl ?? 0`（`executor.position_payload` 已提供 `unrealizedPnl`，store 经 `member.position` 承载）。**直接求和持仓未实现，与时间窗口完全解耦**；**禁止**用 `Σ sessionPnlUsd − 窗口内已实现` 的减法式（会随筛选窗口算错浮盈，违背 R3）。
   - **v2（P2-5）单位**：面板内金额统一以 **USDT** 标注（复用 `signed`/`fmtPct`/`colorForReturn`），**不**使用头部 `aggregatePnl` 的 `fmtMoney`（其渲染 `¥` 符号，与本面板 USDT 口径不符）。
   - 局部 state：`range`（默认 `"all"`）、`custom`（自定义起止）。
   - `nowSec = Math.floor(Date.now()/1000)`（**v2（P2-6）：渲染期取一次即可，面板为短生命周期模态，明确不加定时 tick**）。
   - 计算：`ledger = buildLedger(members)` → `bounds = resolveRangeBounds(range, nowSec, custom)` → `visible = filterRoundTrips(ledger, bounds)`（用 `useMemo` 依赖 `[liveAutoGroup, range, custom, nowSec]`）→ `summary = summarizeRoundTrips(visible, capitalBaseUsd)`。
   - 渲染 `<Modal>`：标题「自动交易收益」+ **数据覆盖提示**（**v2（P1-1）**：「数据自 `fmtTime(liveAutoGroup.startedTs)` 起（运行期记录，停止后清空）」，让用户理解「近30日/全部」实为本组启动以来）+ `<AutoTradePnlSummary>`（顶部时间筛选 + KPI 卡）+ 当前持仓浮盈条（小字，明确标注「未实现」并与「已实现总收益」并列，避免与会话聚合盈亏混淆）+ `visible` 倒序（按 closedTs desc）映射 `<AutoTradeRoundTripRow>` 列表 + 空态引导（未运行/无完整交易/窗口早于启动三种文案）。
   - **v2（P1-1）窗口早于覆盖范围的空态**：当 `bounds.startTs < startedTs` 且窗口内 `visible.length===0` 时，空态文案应说明「所选区间早于本组启动时间，暂无记录；跨会话历史收益将在后续版本（基于 `/runs/{id}/trades`）提供」，而非笼统「无数据」。
   - `liveAutoGroup == null`（未运行）时渲染明确空态：「尚未启动自动交易，启动后这里按时间维度汇总每次完整交易的收益与总收益率」。
2. **`AutoTradePnlSummary.tsx`**（~90 行）
   - props：`{ range, onRangeChange, custom, onCustomChange, summary: PnlSummary }`。
   - 顶部 segmented control（`role="group"`，5 段：今日/近7日/近30日/全部/自定义；选中态高亮，键盘可达；参照 `AutoTradePanel` 的 `ModeToggle` 视觉）；选「自定义」时显示两个 `type="date"` 输入。
   - KPI 卡（响应式 grid）：**总收益**（`signed(totalPnlUsd)` USDT，`colorForReturn`）、**总收益率**（`fmtPct(totalReturnPct)`，`colorForReturn`）、**完整交易笔数**、**胜率**（`fmtPct(winRate)`）、**最大单笔盈 / 亏**（`signed`）。主指标（总收益 / 总收益率）字号更大、置左，符合视觉层级。
3. **`AutoTradeRoundTripRow.tsx`**（~80 行）
   - props：`{ rt: RoundTrip; open: boolean; onToggle: () => void }`。
   - 折叠态：标的 + 方向 Badge（开多/开空）+ 开仓价→平仓价 + 平仓时间 + 持仓时长（`closedTs-openedTs` 格式化）+ **已实现盈亏**（`signed(pnlUsd)` USDT，着色）+ **收益率**（`fmtPct(pnlPct)`，着色）。盈亏与收益率为该行视觉焦点。
   - 整行 `button`/`aria-expanded`、右侧 chevron、键盘可达（复用既有渐进披露交互模式）；展开渲染 `<TradeExplainDetail trade={rt.exit} />`（平仓解释含触发原因/风控断面）。

### 3.5 既有 `TradesTable` 的百分比增强

`TradesTable.tsx` 盈亏单元（`:115-120`）从「`t.side==="close" ? signed(t.pnl) : "—"`」升级为两行：主行 `signed(t.pnl)`，副行（仅 `close` 且 `t.pnlPct != null`）`fmtPct(t.pnlPct)`。**v2（P2-4）着色对齐**：该单元现状为内联三元（`t.pnl>0 ? var(--long) : ...`），实现时统一替换为 `colorForReturn(t.pnl)`，使底部表与新面板着色同源（`colorForReturn` 与内联三元等价，纯一致性收敛，不改观感）。回测/模拟成交无 `pnlPct` → 仅显示金额（行为不变）。这样用户在底部逐笔「成交记录」也能看到每笔平仓的收益率，与新面板口径一致。

### 3.6 入口接线

- `AutoTradePanel.tsx` 头部（`:87-115`，紧邻 `aggregatePnl`）新增「收益记录」按钮（ghost、含图标、`title="按时间维度查看每次完整交易的收益与总收益率"`），新增 prop `onOpenRecords: () => void`。无运行分组时按钮可点但面板内为空态引导（不禁用，避免「按钮坏了」误解）。
- `WatchlistTab.tsx` 新增局部 state `recordsOpen`，把 `onOpenRecords={() => setRecordsOpen(true)}` 传入 `AutoTradePanel`，在容器末尾渲染 `<AutoTradePnlPanel open={recordsOpen} onClose={() => setRecordsOpen(false)} />`（与既有 `AutoTradeGroupConfirmModal` 并列）。

### 3.7 关键时序（一次完整交易的收益呈现）

```
1. 策略信号 → LiveExecutor._do_open：真实下单成功 → trade(open, pnl=0, 无 pnlPct) → WS
2. 平仓触发（信号/反手/时间止盈/会话止盈止损/OCO） → _settle_close/_synthesize_external_close：
     pnl = _close_pnl(exit_px)；pnl_pct = _close_return_pct(exit_px)
     → trade(close, pnl, pnlPct) → _dispatch（注入 meta + best-effort 落库[忽略 pnlPct] + WS push）
3. 前端 useLiveAutoGroupStream 收到 trade 事件 → store.liveAutoGroup.members[instId].trades 追加
4. 用户点「收益记录」→ AutoTradePnlPanel：
     buildLedger → 配对 (1) 与 (2) 为一条 RoundTrip（pnlUsd=close.pnl, pnlPct=close.pnlPct）
     → 按选中时间窗口 filter（closedTs）→ summarize（÷ totalBudgetUsd）
     → 顶部 KPI 显示总收益/总收益率；列表逐条显示完整交易盈亏与收益率
5. 刷新页面：liveAutoGroup 经 group/status 重发现 + 各成员 WS snapshot 重放 executor.trades（含 pnlPct）→ 记录恢复
```

---

## 4. 文件 / 模块变更计划（File / Module Change Plan）

| 文件 | 动作 | 一句话意图 |
|---|---|---|
| `services/quant/app/live_auto/executor.py` | 改 | 新增 `_close_return_pct`，`_make_trade` 增 `pnl_pct` keyword 形参并写入 `trade["pnlPct"]`，`_settle_close`/`_synthesize_external_close` 复位前透传 |
| `services/quant/tests/test_live_auto_session.py` | 改 | 新增用例：平仓成交含 `pnlPct`（多头盈利为正、空头、杠杆缩放正确）；开仓成交无 `pnlPct`；保证金为 0 退化为 None/省略 |
| `apps/web/lib/types.ts` | 改 | `Trade` 接口新增可选 `pnlPct?: number | null` |
| `apps/web/lib/autoTradePnl.ts` | 新增 | 纯函数收益聚合：RoundTrip 配对（复用 `tradeTrails`）、时间窗口解析/筛选、总收益与总收益率汇总、pnlPct 回退派生 |
| `apps/web/lib/autoTradePnl.test.ts` | 新增 | 覆盖配对、pnlPct 透传/回退、按 closedTs 的窗口筛选、汇总（总收益/总收益率/胜率/最佳最差）、seeded 过滤、空输入 |
| `apps/web/components/Chart/AutoTradePnlPanel.tsx` | 新增 | 收益记录面板（Modal）：编排 store→ledger→筛选→汇总，渲染概览+列表+持仓浮盈条+空态 |
| `apps/web/components/Chart/AutoTradePnlSummary.tsx` | 新增 | 顶部时间维度 segmented control（含自定义日期）+ 总收益/总收益率等 KPI 概览卡 |
| `apps/web/components/Chart/AutoTradeRoundTripRow.tsx` | 新增 | 单条「完整交易」行：方向/开平价/时长/已实现盈亏/收益率，可展开 `TradeExplainDetail` |
| `apps/web/components/Chart/AutoTradePnlPanel.test.tsx` | 新增 | 组件测试：空态、KPI 汇总数值、时间窗口切换改变汇总与列表、行展开 |
| `apps/web/components/Chart/AutoTradePanel.tsx` | 改 | 头部新增「收益记录」按钮 + `onOpenRecords` prop |
| `apps/web/components/Chart/WatchlistTab.tsx` | 改 | 持有 `recordsOpen` 局部 state，接线按钮并渲染 `AutoTradePnlPanel` |
| `apps/web/components/Strategy/TradesTable.tsx` | 改 | 盈亏列在 close 行追加 `fmtPct(pnlPct)` 副行（缺失则仅金额，回测/模拟行为不变） |
| `apps/web/components/Strategy/TradesTable.test.tsx` | 改 | 新增用例：close 行带 `pnlPct` 时渲染百分比；无 `pnlPct` 时不渲染 |

> 说明：不新增任何后端路由 / Pydantic 模型 / DB 列 / 迁移；不修改 store 形状或持久化 version；不触碰回测 `SimBroker`。所有新文件均控制在 ≤800 行 / 方法 ≤50 行 / 形参 ≤5 / 嵌套 ≤4 的项目阈值内。

---

## 5. 接口设计（Interface Design）

### 5.1 WebSocket（`/api/live-auto/ws/{sessionId}` 与分组各成员 WS）

协议**结构不变**，仅 `trade` 事件与 `snapshot.trades[]` 内的成交对象在**平仓**时多一个可选字段：

```jsonc
// trade 事件 data（平仓示例，新增 pnlPct）
{
  "ts": 1751200600, "side": "close", "price": 64850.0, "qty": 0.03,
  "fee": 0.0, "pnl": 19.05, "pnlPct": 0.0148,   // ← 新增：已实现盈亏 / 开仓保证金
  "positionAfter": 0.0, "reason": "signal_close",
  "stopLossPct": 0.01, "takeProfitPct": 0.02, "explain": { /* ... */ }
}
// 开仓事件不含 pnlPct 键（向后兼容；旧前端忽略未知字段）
```

### 5.2 前端纯函数库 `lib/autoTradePnl.ts`

签名见 §3.3。无网络/副作用。`nowSec` 由调用方注入（可测）。

### 5.3 组件 props 契约

- `AutoTradePnlPanel`：`{ open: boolean; onClose: () => void }`
- `AutoTradePnlSummary`：`{ range; onRangeChange; custom?; onCustomChange; summary: PnlSummary }`
- `AutoTradeRoundTripRow`：`{ rt: RoundTrip; open: boolean; onToggle: () => void }`
- `AutoTradePanel` 新增 `onOpenRecords: () => void`

### 5.4 REST / CLI

无新增（沿用既有 `GET /api/live-auto/group/status` 提供 `totalBudgetUsd`/`leverage`/`members[]`）。

---

## 6. 数据模型（Data Model）

### 6.1 后端（运行期内存，无 DB 变更）

- `LiveExecutor` 成交字典新增可选键 `pnlPct: float`（仅 close）。
- DB `Trade` ORM 与 `trades` 表**不变**；`append_live_trade` 显式字段映射，`pnlPct` 不入库（派生量，可由 close 行 + 开仓价 + 杠杆重算，但本方案不依赖落库）。

### 6.2 前端（运行期，无 store 变更）

- `Trade.pnlPct?: number | null`（types.ts）。
- `RoundTrip` / `RangeBounds` / `PnlSummary` / `AutoTradeRange`（autoTradePnl.ts 局部类型）。
- 面板开关 `recordsOpen` 与时间窗口 `range`/`custom`：组件局部 `useState`（瞬态，不持久化）。

### 6.3 计算口径（单一来源，前后端一致）

- 每笔收益率 `pnlPct = 已实现盈亏 / 开仓占用保证金`（后端权威）。
- 窗口总收益 `totalPnlUsd = Σ(closedTs∈窗口 的 round-trip pnlUsd)`（仅已实现，不含浮盈）。
- 窗口总收益率 `totalReturnPct = totalPnlUsd / totalBudgetUsd`（与 `_finalize_run.totalReturn=realized/budget` **同源口径**：均为「相对投入资本的回报」；本面板分母用**分组级 `totalBudgetUsd`**=对整组聚合的对应物）。**v2（P2-1）**：分母为**原始投入预算**，成员部分停止后**不**随在投资本缩减，故 UI 应注明「基于投入预算的回报率」，避免被误读为「当前在投资本回报」。
- **v2（P1-3）当前浮盈**：单独成条展示，**直接** `unrealizedUsd = Σ member.position?.unrealizedPnl ?? 0`（执行器 `position_payload.unrealizedPnl`），**与窗口无关**；明确区分「已实现总收益（窗口内）」与「当前浮盈（持仓未实现，全局）」。**不**用 `member.status.sessionPnlUsd`（= 已实现+未实现，会与已实现总收益重复计入而误读）。

---

## 7. 测试与验收标准（Testing & Acceptance Criteria）

### 7.1 后端单测（`test_live_auto_session.py` / 必要时新增执行器用例）

- 多头平仓盈利：`pnlPct > 0` 且约等于 `(exit/entry-1)*leverage`（容差内）。
- 空头平仓：`pnlPct` 符号正确（价跌为盈）。
- 杠杆缩放：同价差下 `leverage=5` 的 `pnlPct` ≈ `leverage=1` 的 5 倍。
- 开仓成交**不含** `pnlPct` 键。
- 退化断面（`entry_qty_sz`/`entry_price` 为 0，**或 v2（P1-2）`leverage<=0`**）：`_close_return_pct` **先判后除**返回 `None`，trade 省略 `pnlPct`，**绝不抛 `ZeroDivisionError`**。
- 落库不回归：含 `pnlPct` 的 trade dict 经 `append_live_trade` 正常入库（`pnlPct` 被忽略，无 `TypeError`）。
- 既有 461+ 后端用例零回归。

### 7.2 前端单测

- `autoTradePnl.test.ts`：开/平配对成 RoundTrip；`pnlPct` 优先取后端值、缺失走回退派生；按 `closedTs` 的窗口筛选边界（含/不含端点）；`summarize` 的总收益/总收益率/胜率/最佳最差正确；`capitalBase<=0` 时 `totalReturnPct=0`；**v2（P2-2）`count===0` 时 `winRate=0`（不为 NaN）**；`seeded` 成交被排除；空输入返回零汇总。
- `AutoTradePnlPanel.test.tsx`：未运行→空态；给定分组成交→KPI 数值正确；切换时间窗口→汇总与列表随之变化；行点击展开 `TradeExplainDetail`；**v2（P1-1）所选区间早于 `startedTs` → 专用空态文案**；**v2（P1-3）当前浮盈条数值 = `Σ position.unrealizedPnl`、切换窗口时浮盈不变（与窗口解耦）**。
- `TradesTable.test.tsx`：close 行带 `pnlPct` 渲染百分比；无 `pnlPct`（回测）不渲染百分比、金额照常。
- 既有 395+ 前端用例零回归；`npx tsc --noEmit` 通过；`next build` 成功（注：`npm run lint` 在 apps/web 不可用，以 `tsc --noEmit` 为类型门）。

### 7.3 验收标准（Definition of Done）

1. 自动交易运行后，底部「成交记录」每笔**平仓**显示**金额 + 收益率百分比**；开仓行不显示百分比。
2. 自动交易区点击「收益记录」打开面板，顶部可按**今日/近7日/近30日/全部/自定义**筛选；KPI 区显示该窗口**总收益（USDT）**与**总收益率（%）**，并附笔数/胜率/最佳最差。
3. 面板列表逐条展示**一次完整交易**（开→平往返）的方向、开平价、持仓时长、已实现盈亏与收益率，可展开查看触发原因。
4. 切换时间窗口即时改变汇总与列表；未运行/无完整交易/所选区间早于本组启动时三种情形均显示**明确且诚实**的空态引导。
5. **v2（P1-1）数据覆盖诚实**：面板显式展示「数据自 `startedTs` 起（运行期记录，停止后清空）」；「近30日/全部」对刚启动的组不谎称跨会话历史。
6. **v2（P1-3）浮盈口径**：面板「当前持仓浮盈」直接来自 `Σ position.unrealizedPnl`，与所选时间窗口无关，且与「已实现总收益」明确区分、不重复计入。
7. 百分比与汇总均来自真实成交派生，无任何伪造数据；后端无 DB 迁移、前端无 store version bump。
8. 全量前后端测试通过，类型检查与构建通过。

---

## 8. 范围边界（Scope）

- **范围内**：分组自动交易（watchlist auto-trade group）**运行期**的收益记录与汇总；底部 `TradesTable` 的 per-trade 百分比。
- **⚠️ v2（P1-1）已知限制（必须对用户诚实）**：`liveAutoGroup` 为**瞬态**，整组停止即清。因此时间筛选「近7日/近30日/全部」对一个刚启动的组**实际只覆盖本组启动（`startedTs`）以来**的成交，并非跨会话历史。本期**不**扩后端、**不**伪造历史数据；改以**显式标注**化解（§3.4 数据覆盖提示 + 早于启动窗口的专用空态），符合项目「不伪造数据 / 显式降级」约定。跨会话历史收益（基于 `/runs/{id}/trades` 的「历史」视图）作为**后续增量**，是补全该限制的正式路径。
- **范围外（明确不做，留作后续）**：
  - 已停止/历史会话的跨刷新持久化收益记录（运行期态瞬态，停止 `clearLiveAutoGroup` 即清；可经 `/runs/{id}/trades` 重建为「历史」标签页，作为后续增量——见上「已知限制」）。
  - 回测 / 模拟（paper）成交的百分比（需改 `SimBroker`，本期不动；其 `TradesTable` 百分比列保持「—」）。
  - 单会话死件（`AutoTradeTradesTable` 等）的复活。
  - 后端聚合接口 / 新 DB 列（本期汇总在前端纯函数完成）。

---

## 9. 风险与缓解（Risks & Mitigations）

| 风险 | 说明 | 缓解 |
|---|---|---|
| R1 `pnlPct` 破坏落库 | 给 trade dict 加键可能让 `append_live_trade` 报未知字段 | 已核实 `append_live_trade`（`repositories.py:268-286`）**逐字段显式映射**、不 splat，新键被忽略；验收 §7.1 加「落库不回归」用例 |
| R2 百分比口径歧义 | 「收益率」可解读为价格变动% 或保证金回报% | 明确采用**保证金回报%**（= 价格变动% × 杠杆），与会话级 `totalReturn=realized/budget` 同源；面板/表格/文档统一口径并在 UI 注明 |
| R3 已实现 vs 浮盈混淆 | 头部 `aggregatePnl` 含浮盈，面板总收益仅已实现，数值不等易误解 | 面板单设「当前持仓浮盈」条与「已实现总收益」并列、文案区分；总收益率分母统一为 `totalBudgetUsd` |
| R4 刷新/重连数据完整性 | `liveAutoGroup` 瞬态，刷新后记录是否齐全 | 经 `group/status` 重发现 + 成员 WS `snapshot` 重放 `executor.trades`（含 pnlPct），运行期内完整恢复；停止后清空属既有行为，已在 §8 标注 |
| R5 跨标的错误配对 | 多成员往返若跨标的配对会算错盈亏 | `buildLedger` **逐成员**调用 `buildRoundTrips`，绝不跨 instId 配对；复用经测的 `buildTradeTrailSegments` 因果配对 |
| R6 时区/窗口边界 | 「今日」用本地零点，跨时区/夏令时易错 | `resolveRangeBounds` 用 `Date` 本地零点、`nowSec` 由调用方注入，纯函数可测；窗口闭区间按 `closedTs` |
| R7 seeded/非真实成交计入收益 | 若未来引入 warm-start 预热成交，混入会虚增收益 | `buildRoundTrips` 配对前过滤 `seeded`，诚实只计真实成交（当前 live-auto 无 seeded，前瞻保护） |
| R8 pnlPct 回退失真 | 旧 snapshot 无 pnlPct 时回退派生忽略手续费 | 优先用后端真实值；回退仅极少数边界，且口径一致（价格变动%×杠杆），误差有界并可接受 |
| R9 面板信息过载 | KPI + 列表 + 浮盈条 + 筛选可能拥挤 | 视觉层级（主指标大字置左、次指标弱化）、列表可展开渐进披露、响应式 grid，遵循 HCI 最佳实践 |

---

## 10. 评审记录（Review Log）

> 已于 v2 完成。**完整的问题清单、严重度与就地修复结论见本文档顶部《评审记录（Review Notes，v2）》**；评审结论见文末《评审结论（Review Verdict，v2）》。本节不再重复。

---

## 11. 实施过程发现的方案缺陷（Implementation Deviations）

> 预留给 Subtask #2（代码开发）。如实现中发现方案缺陷或需偏离，在此记录原因与回退条件。

### D1. 后端测试落点（§4 变更计划表 vs §7.1 正文）

- **方案原文**：§4「文件 / 模块变更计划」表把后端 pnlPct 用例落点写为 `test_live_auto_session.py`。
- **实际落点**：executor 级 pnlPct 用例落在 `services/quant/tests/test_live_executor.py`（4 例：多头盈利、空头盈利符号、杠杆缩放、退化断面 `leverage<=0` 返回 None 且不抛 `ZeroDivisionError`）；「落库不回归」用例落在 `services/quant/tests/test_live_auto_explain.py`（含 `pnlPct` 的 trade dict 经 `append_live_trade` 静默忽略、无 `TypeError`）。
- **原因/依据**：§7.1 标题本身已写明「`test_live_auto_session.py` / **必要时新增执行器用例**」，明确授权新增执行器测试。`test_live_executor.py` 直接以 `apply()` 驱动开→平并可精确控制 `ref_px`，是 pnlPct 断言的天然宿主（会话测试的桩 K 线同价、不利于价差断言）；`append_live_trade` 的既有覆盖在 `test_live_auto_explain.py`，落库回归用例与其同处最一致。**覆盖无缺口**：§7.1 列举的全部后端用例均已实现，仅落点更贴近既有测试组织。未在 `test_live_auto_session.py` 新增用例。

### D2. 「窗口早于启动」空态判据由 `startTs` 改为 `endTs`

- **方案原文**：§3.4 把「窗口早于覆盖范围」专用空态的判据写为 `bounds.startTs < startedTs`。
- **实际实现**：改用 `bounds.endTs < startedTs`（即整个窗口都落在本组启动之前）作为「所选区间早于本组启动时间」文案的判据；否则（窗口与覆盖区间有交叠但无往返）回落通用「当前时间窗口内暂无完整交易」文案。
- **原因**：对一个刚启动的组，「全部 / 近7日 / 近30日」窗口的 `startTs` 必然 `< startedTs`（`all` 的 startTs 恒为 0），若按原判据，则「刚启动、尚无平仓」这一最常见的空态会被误贴成「区间早于启动」，反而误导。改用 `endTs < startedTs` 仅在窗口确实整体早于覆盖区间时才显示该专用文案，更准确。P1-1 的诚实性已由**常驻数据覆盖提示条**（「数据自 `startedTs` 起…」，对所有窗口可见）兜底，不依赖该专用空态，故此细化不削弱 P1-1。

---

## 评审结论（Review Verdict，v2）

**结论：有条件通过（Approved with Conditions）。**

v1 方案接地扎实——评审已逐节对照真实源码核对其全部代码断言（行号、函数签名、数据结构、落库路径），仅发现 1 处真实代码缺陷（除零守卫写反）、2 处完整性/正确性问题（浮盈口径、时间筛选与瞬态数据的诚实性），**3 项 P0/P1 已全部在 v2 文档体内就地修复，无 P0/P1 遗留**。技术路线（后端派生 `pnlPct` + 前端纯函数聚合 + 复用既有配对/格式化器/Modal，零 DB 迁移、零 store version bump）正确、最小、向后兼容，符合 CLAUDE.md「不伪造数据 / 单一数据源 / 显式降级」约定，可进入代码开发（Subtask #2）。

**放行的前置条件（开发节点必须逐条落实，已在对应章节标 `v2`）：**

1. **（P1-2）** `_close_return_pct` 必须**先校验 `leverage>0` 且名义价值>0 再相除**（§3.2），杜绝 `_settle_close` 路径的 `ZeroDivisionError`；后端测试覆盖退化断面（§7.1）。
2. **（P1-3）** 面板「当前持仓浮盈」必须**直接取 `Σ member.position?.unrealizedPnl ?? 0`**（§3.4/§6.3），与时间窗口解耦；**禁止**用 `sessionPnlUsd` 减法式；测试断言切窗口浮盈不变（§7.2）。
3. **（P1-1）** 面板必须**显式标注数据覆盖起点 `startedTs`** 并为「早于启动时间」的窗口给出专用空态（§3.4/§7.3/§8）；不得让时间筛选谎称跨会话历史。跨会话历史收益为后续增量，本期范围外。
4. **（P2-2/P2-4/P2-5/P2-6）** `summarizeRoundTrips` 守卫 `count===0 → winRate=0`；`TradesTable` 盈亏着色统一 `colorForReturn`；新面板金额统一 **USDT** 标注（不传播头部 `fmtMoney` 的 `¥`）；模态 `nowSec` 取一次、明确不加 tick。
5. **（P2-3）** `_make_trade` 既已超 ≤5 形参阈值，新增 `pnl_pct` **仅以 keyword-only 形式**追加，不得新增位置参数，不引入额外重构。
6. 维持 v1 的范围边界：不动 `SimBroker`/回测百分比、不复活单会话死件、不新增后端聚合接口/DB 列。

满足上述条件即视为完全通过。
