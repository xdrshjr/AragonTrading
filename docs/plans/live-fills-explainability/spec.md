# 实盘成交记录可解释性（live-fills-explainability）开发方案

> 版本：**v2**（方案评审与修复 · Subtask #1；基于 v1 方案设计就地修复 P0/P1）
> 适用范围：**实盘账户模式**下方面板的「成交记录」页签（`apps/web/components/Live/LiveFillsTable.tsx`，数据源为真实 OKX `GET /api/v5/trade/fills`）。
> 关联模块 / 既有方案：`auto-trade-explainability`（已落地，commit `c7420ca`）、`live-trading-mode`、`live-auto-trading`、`watchlist-auto-trade-group`（commit `360c1cb`）、`factor-layer`。
> 一句话目标：让**实盘成交记录里的每一条买卖**都能逐行展开，看到「这笔交易为什么被自动交易触发」。

---

## 评审记录（Review Notes · Subtask #1）

> 评审视角：Anthropic 工程团队资深评审，对 v1 **逐节核对源码**后形成（核对面：`api/trading.py::trading_fills`、`okx/trading.py::_normalize_fill`/`_gen_cl_ord_id`/`close_live_position`、`live_auto/session.py::_on_confirmed`/`_inject_explain_meta`/`_persist_trade`、`live_auto/executor.py::_make_trade`、`db/models.py::Trade`、`db/repositories.py::append_live_trade`/`list_trades`、`schemas.py::LiveFill`/`_parse_explain`/`trade_out`、`lib/tradeExplain.ts::resolveExplain`、`TradeExplainDetail.tsx`、`tradingTypes.ts`、`types.ts::TriggerExplain`、`okx/types.py::BAR_SECONDS`、`tests/test_trading_api.py`）。
> 维度：可行性 / 完备性 / 一致性 / 适度性。**P0/P1 已就地修复**（正文相应小节带 `RVx` 标注），P2 为实现期建议（不阻塞设计）。
> 关联链路两侧已核实就位 → 方案**可行**：`_normalize_fill` 确在丢弃 OKX 回带的 `clOrdId`（346-356，可补回）；`executor._make_trade`（404-407）best-effort 写 `trade["clOrdId"]/["ordId"]` → `session._inject_explain_meta`（134-137）注入 `explain.meta` → `append_live_trade` 持久化；`resolveExplain` 恒返回非空（合成回落），故 `TradeExplainDetail` 签名扩展向后兼容成立；`append_live_trade` 仅 `LiveAutoSession` 调用，解释索引天然只含自动交易行。

| # | 维度 | 严重度 | 问题 | 处置 |
|---|---|---|---|---|
| RV1 | 可行性 / 正确性 | **P0** | **关联时间窗 slack 远小于 bar 周期**。自动交易 trade 落库的 `ts` 是**确认根开盘时间**（`session._on_confirmed`：`ts=int(r["ts"])`，OKX K 线 ts 为开盘时刻），而真实成交在该根**收线之后** ≈ 一个 bar 周期才发生；故 `fill.ts − trade.ts ≈ 一个 bar 周期`（5m=300s / 15m=900s / 1H=3600s / 最大 1D=86400s，见 `BAR_SECONDS`）。v1 `since_ts=min(fill.ts)-120` 会令 `Trade.ts >= since_ts` 把除 1m 外所有周期的命中行整段筛掉 → 全部回落「手动」，**特性静默失效**。 | 已修复：§2.4 将 `_FILL_JOIN_SLACK_SEC` 提升至覆盖最大 bar 周期 + 余量（`90000`），并申明 `limit` 才是扫描硬上限；§六补「确认根开盘 ts 早于成交 ts 一个周期仍命中」回归用例；R2 同步。 |
| RV2 | 正确性 / HCI | **P1** | **行 key 与展开态身份**。v1 §2.5 建议「clOrdId / ordId 优先」、R6 称「行以 OKX 维度键唯一」——但**部分成交（partial fill）**多行共享同一 clOrdId / ordId（甚至 ts），作 key 必撞键；叠加 8s 轮询与可展开行，React key 冲突会令展开态错挂到他行或轮询时塌陷，违背「顶级 HCI」目标。 | 已修复：`_normalize_fill` 增 OKX 每行唯一 `tradeId`，作 React 行 key 与展开态 `Set<string>` 的稳定身份（缺失回落 `ordId-ts-index`）；同步纳入 `LiveFill`（后端 / 前端）与 §四 wire；订正 §2.5 / R6 表述。 |
| RV3 | 完备性 | P2 | **空 fills 仍触发解释查询**。v1 §2.4 在 `fills` 为空时把 `since_ts` 置 0 并照常查解释行，徒增一次扫描。 | 已修复：§2.4 `if not fills: return {"fills": fills}` 提前返回。 |
| RV4 | 一致性 | P2 | `index_trade_explains` 复用 `schemas._parse_explain` 会形成 `live_auto → schemas` 反向耦合（`api → live_auto` 边已存在于 `api/live_auto.py`，方向可接受；但纯函数模块不宜再反依赖 schemas）。 | 已注明：`fills_explain.py` 内联等价的 ~6 行坏 JSON 安全解析，保持零外部依赖（§三 / §八）。 |
| RV5 | 可行性 | P2 | **测试桩缺口**：`tests/test_trading_api.py::_FakeTradeClient` 无 `get_fills`，端点富化测试需在 `app.api.trading.fetch_live_fills` 边界打桩（或为桩补 `get_fills`）；`api/trading.py` 现无 `logger`，需补 `logging.getLogger(__name__)`。 | 已注明于 §2.4 / §六 / §八，作为实现期落地细节。 |
| RV6 | 适度性 | 通过 | 复用既有 explain 契约与组件、零 DB 迁移、字段增量向后兼容、best-effort 异常隔离，scope 合理无过度设计；备选（独立端点 / 索引列）恰当延后。 | 维持。 |

---

## 一、概述（Overview）

项目已经具备一条端到端的「自动交易可解释性」链路（`auto-trade-explainability`，已合入）：在决策时刻捕获结构化的 `TriggerExplain`，随 trade 持久化进 `trades.explain_json`、随 `/ws/live-auto/{sid}` 流到前端，并在**策略面板（StrategyPanel）的自动交易区**用一张可逐行展开的 `AutoTradeTradesTable` 呈现，展开后可看因子归因、复合得分对阈值、风控快照等。但这条链路只覆盖了「策略面板里那张自动交易内部成交表」。

用户当前看到「买卖记录」的另一个、也更**贴近真实资金**的入口，是切到**实盘账户模式**后下方面板的「成交记录」页签——`LiveBottomPanel` 渲染的 `LiveFillsTable`。它每 8 秒轮询真实 OKX `GET /trading/fills`（近三日成交），逐行展示 `时间 / 方向(买入·卖出) / 成交价 / 数量 / 手续费`（`LiveFillsTable.tsx:39-72`）。这张表才是用户口中「实盘里的成交记录」：自动交易下的真单成交也会出现在这里。但它目前**完全没有触发原因**——每行只是冷冰冰的价量，用户无法判断某笔成交是策略哪个信号触发、是开仓还是平仓、是时间止盈还是会话止损、当时复合得分多少、哪些因子在推动。

本方案在**不重写既有 explain 数据结构、不新增下单路径**的前提下，把已经存在于 `trades.explain_json` 里的触发解释，**关联（join）到 OKX 真实成交行上**，并把 `LiveFillsTable` 升级为「**逐行可展开**」的成交审计表：自动交易产生的成交行显示一个触发类别角标（因子组合 / 策略信号 / 时间止盈 / 反手 / 会话止盈止损 / 外部平仓），点击整行即渐进披露展开，复用既有 `TradeExplainDetail` 组件呈现一句话原因、关键指标对照、风控快照与（factor_score 策略的）因子归因图；手动下单 / 无解释的历史成交则诚实标注「手动」且不提供空洞的展开。整体「美观优雅、顶级设计、符合人机交互最佳实践」：复用既有设计 token 与已验证的展开组件，保证与策略面板里那张表**视觉与交互一致**。

关联的关键在于一个**连接键（join key）**：自动交易下单时由系统生成确定性的 `clOrdId`（`aragon…` / `aragonc…` 前缀，见 `okx/trading.py:69-73,168-172`），OKX 会在该订单的成交回报里原样回显这个 `clOrdId`；而 explain 落库时 `clOrdId`/`ordId` 已 best-effort 写入 `explain.meta`（`live_auto/session.py::_inject_explain_meta`）。因此「成交 → 解释」的关联以 **`clOrdId` 为主键、`ordId` 为副键**即可稳定建立。难点是：(1) OKX 原始成交里本就有 `clOrdId`，但现有 `_normalize_fill` 把它丢弃了（`okx/trading.py:346-356` 只取了 `ordId`）；(2) `Trade` 表没有 `clOrdId`/`ordId` 独立列，连接键埋在 `explain_json.meta` 里。本方案据此补齐两侧的连接键并做服务端关联，使前端拿到的每条 fill 自带可空的 `explain`。

---

## 二、技术设计（Technical Design）

### 2.1 架构与数据流

```text
[已存在] 自动交易决策 → LiveExecutor 产出 trade dict（含 explain，meta.clOrdId/ordId）
         → session._persist_trade → repo.append_live_trade → trades.explain_json（含 meta.clOrdId/ordId）
                                                                  （历史数据，无需重跑）
        │
        ▼
[本期新增的关联路径]
  浏览器（实盘成交记录页签，每 8s 轮询）
    → GET /api/py/trading/fills?instId&accountId&limit
      → api/trading.py::trading_fills
         ① fills = await fetch_live_fills(creds, instId, limit)         （真实 OKX，既有；_normalize_fill 现额外回带 clOrdId）
         ② explained = await repo.list_explained_trades(session, since_ts=…, limit=…)   （只取 explain_json 非空、按 ts 倒序、限量）
         ③ index = index_trade_explains(explained)                       （纯函数：按 clOrdId / ordId 建索引）
         ④ fills = attach_explains(fills, index)                          （纯函数：逐 fill 命中即挂 explain；未命中留 None）
         ⑤ 整段 ②③④ best-effort try/except：任何异常 → 原样返回未富化 fills（成交永不因解释失败而不可见）
      → LiveFillsOut{ fills: [LiveFill{ …, clOrdId?, explain? }] }
    → LiveFillsTable：有 explain 的行 = 可展开行（chevron + 类别角标）；点开 → <TradeExplainDetail explain={fill.explain}/>
                      无 explain 的行 = 朴素行（标「手动」，不可展开，杜绝空洞披露）
```

关键设计取舍：

- **服务端关联（单次往返）而非前端二次取数**：`LiveFillsTable` 既有的「单数据源轮询、直接渲染」范式保持不变，前端拿到的 fill 直接自带 `explain`，无需第二个请求、无需在前端拼装运行期/历史多源数据。代价是 `/trading/fills` 端点多读一次本地 `trades`，以 best-effort + 异常隔离消解（见 R1）。备选「独立查询端点 `POST /trading/fills/explain`」在 §七 讨论并说明为何不取。
- **连接键 clOrdId 优先、ordId 兜底**：开仓 trade 的 `clOrdId` 必有（系统生成）、`ordId` 多数有；平仓（市价整仓平）`ordId` 可能为空但 `clOrdId` 必有（`okx/trading.py:208-253` 市价平仓 `ord_id=""`）。故必须支持 clOrdId 关联，否则平仓成交无法命中。
- **零数据迁移**：解释及其 `meta.clOrdId/ordId` 已存在于历史 `explain_json` 中；关联读 `explain_json` 即可，**不**给 `Trade` 加列、**不**做回填，把改动面收到最小（适度性）。
- **复用既有解释组件**：展开详情复用 `TradeExplainDetail` / `FactorContributionBars` / `tradeExplain.ts`，保证两处成交表观感一致、维护单点。

### 2.2 连接键补齐（两侧对齐）

1. **OKX 成交回带 clOrdId + tradeId**：`okx/trading.py::_normalize_fill`（346-356）当前只取 `ordId`。新增 `"clOrdId": str(r.get("clOrdId") or "")`（关联主键）与 `"tradeId": str(r.get("tradeId") or "")`（OKX 每条成交唯一 id，作前端行 key 与展开态稳定身份用，RV2）。OKX `/api/v5/trade/fills` 单行本就含 `clOrdId` / `tradeId`（clOrdId 下单时我方写入、成交时回显；tradeId 由 OKX 逐笔成交生成），故只是「停止丢弃」，不改变任何外部调用。
2. **解释侧连接键已就位**：`explain.meta.clOrdId` / `explain.meta.ordId` 在 `session._inject_explain_meta` 已 best-effort 注入（开仓取 `place_live_order` 回执的 `clOrdId/ordId`，平仓取 `close_live_position` 回执的 `clOrdId`，见 §一）。`external_close`（交易所侧 OCO / 外部干预的合成成交）**无下单调用、无连接键**——此类 fill 一般不会命中我方解释（其 OKX 侧 clOrdId 非 `aragon` 前缀），UI 回落「手动 / 外部」诚实标注，符合预期（R5）。

### 2.3 服务端关联：仓储查询 + 纯函数 join

- `db/repositories.py` 新增 `list_explained_trades(session, *, since_ts: int | None = None, limit: int = 1000) -> list[Trade]`：
  - `select(Trade).where(Trade.explain_json.is_not(None))`；`since_ts` 给定时再 `.where(Trade.ts >= since_ts)`；`.order_by(Trade.ts.desc()).limit(limit)`。
  - 只回带「带解释」的 trade（自动交易行），自然排除回测 / 无解释行；`since_ts` 把扫描收敛到当前 fills 的时间窗内（见 §2.4 端点用法），单查询、小结果集。
- 新建 `live_auto/fills_explain.py`（**纯函数、无凭证、无 IO**，归口在解释特性家族下，便于单测）：
  - `index_trade_explains(trades: list[Trade]) -> dict[str, dict]`：遍历 `trades`，对每个 `t` 解析 `explain = _parse_explain(t.explain_json)`（复用 `schemas._parse_explain` 的坏 JSON 安全回 None 语义，或在本模块内联同等实现以免反向依赖 schemas）；取 `meta = explain.get("meta") or {}`，把 `meta.get("clOrdId")` 与 `meta.get("ordId")` 作为键映射到该 `explain`。**同键去重**：按 `trades` 已是 ts 倒序，遇到已存在键则跳过（保留最新一笔），避免历史复用 clOrdId 的极小概率覆盖。键为假值（空串/None）跳过。
  - `attach_explains(fills: list[dict], index: dict[str, dict]) -> list[dict]`：逐 fill，按 `fill["clOrdId"]`（主）命中则挂 `fill["explain"] = index[clOrdId]`；否则按 `fill["ordId"]`（副）命中再挂；都不中则 `fill["explain"] = None`。**不可变风格**：返回新 dict，不就地改输入（便于测试断言）。
- 关联结果是「成交行 → 触发解释」的一对多自然映射：同一订单可能有多条**部分成交（partial fill）**，它们共享同一 `clOrdId/ordId`，因而共享同一 explain（语义正确：同一订单的多笔成交触发原因相同，R6）。

### 2.4 端点富化：`api/trading.py::trading_fills`

```python
@router.get("/fills", response_model=LiveFillsOut)
async def trading_fills(accountId, instId, limit, session=Depends(get_session)) -> dict:
    row = await _resolve_account(session, accountId)
    fills = await fetch_live_fills(_creds_from_row(row), instId, limit)   # 既有真实 OKX 取数
    if not fills:
        return {"fills": fills}                                          # 无成交：免去多余 DB 读（RV3）
    try:                                                                  # 富化 best-effort（R1）
        since_ts = min(f["ts"] for f in fills) - _FILL_JOIN_SLACK_SEC    # slack ≥ 最大 bar 周期（见下，RV1）
        explained = await repo.list_explained_trades(session, since_ts=since_ts, limit=_FILL_JOIN_MAX)
        index = index_trade_explains(explained)
        fills = attach_explains(fills, index)
    except Exception as exc:                                              # noqa: BLE001 —— 解释失败绝不阻断成交可见
        logger.warning("attach fills explain failed (account=%s): %s", row.id, exc)
    return {"fills": fills}
```

- **`_FILL_JOIN_SLACK_SEC`（取 `90000`，约 25h）—— RV1/P0 关键修复**：自动交易 trade 落库的 `ts` 是**确认根开盘时间**（`session._on_confirmed`：`ts=int(r["ts"])`；OKX K 线 ts 即开盘时刻），而真实成交发生在该根**收线之后** ≈ 一个 bar 周期，故 `fill.ts − trade.ts ≈ 一个 bar 周期`（5m=300s / 15m=900s / 1H=3600s / 最大 1D=86400s，见 `okx/types.py::BAR_SECONDS`）。若 slack 仅 120s，`Trade.ts >= since_ts` 会把除 1m 外所有周期的命中行整段筛掉 → 全部回落「手动」、特性静默失效。故 slack **必须 ≥ 最大支持 bar 周期 + 余量**。
- `_FILL_JOIN_MAX`（如 `1000`）：解释行查询的硬上限，**才是真正的扫描硬边界**。OKX `/trade/fills` 仅回近 ~3 日、≤100 行，自动交易解释行本就低频；时间窗只作粗筛、`limit` 兜底，二者叠加即小结果集（即便 slack 放宽到天级也不会放大结果集）。
- 端点需补模块级 `logger = logging.getLogger(__name__)`（`api/trading.py` 现无 logger，RV5）；`index_trade_explains` / `attach_explains` 从 `..live_auto.fills_explain` import（`api → live_auto` 边已存在）。
- 端点仍为**只读、不受 `LIVE_TRADING_ENABLED` 守门**（与既有 fills 语义一致，`api/trading.py:5` 注释「仅查不写」）；富化只读 `trades`，不触下单 / 护栏 / `live_order_audit`。

### 2.5 前端：可展开的实盘成交表

- `LiveFill` 类型增可空 `clOrdId?: string`、`explain?: TriggerExplain`（与既有 `auto-trade-explainability` 的 `TriggerExplain` 同构，直接复用 `lib/types.ts` 的接口）。
- `TradeExplainDetail` 增一个**可选 `explain` 入参**（向后兼容）：`{ trade?: Trade; explain?: TriggerExplain }`，内部 `const explain = explainProp ?? (trade ? resolveExplain(trade) : null); if (!explain) return null;`。既有两个调用方（`AutoTradeTradesTable` / `TradesTable`）仍传 `trade`，行为与既有测试不变；本表传 `explain`，省去合成 `Trade` 的中间层。
- `LiveFillsTable` 重构为「可展开表」：
  - 列：在既有 `时间 / 方向 / 成交价 / 数量 / 手续费` 基础上，左侧加 **chevron 列**（仅有 explain 的行显示）、方向列旁加 **触发类别角标**（`explainKindLabel(fill.explain?.kind)`；无 explain → 朴素「手动」淡色标签）。
  - 交互（HCI）：**有 explain** 的行为可点控件（`role` + `tabIndex` + `aria-expanded`，整行可点、键盘可达、`focus-visible` 高亮），点击切换展开；展开行为紧随其后的一条 `<tr><td colSpan={n}>`（`n` = 含 chevron 列在内的总列数）内渲染 `<TradeExplainDetail explain={fill.explain} />`，高度 / 透明度过渡，避免布局跳动；chevron 旋转指示。**无 explain** 的行不渲染 chevron、不可展开（渐进披露：无内容不诱导展开）。展开态由父组件 `LiveFillsTable` 持有为一个 `Set<string>`（按下方稳定行键索引），使 8s 轮询刷新数据时**已展开行保持展开、不闪烁**。
  - 行 key 与展开态身份（RV2/P1）：用 `fill.tradeId`（OKX 每行唯一）作 React 行 key **与**展开态 `Set` 的键，缺失再回落 `${ordId}-${ts}-${index}`。**不可**用 clOrdId / ordId 单独作键——partial fill 多行共享同一 clOrdId / ordId（甚至 ts），会撞键致轮询时展开态错挂他行或塌陷。
  - 表头加一行 hint：「点击展开查看自动交易触发原因」。空 / 加载 / 错误态文案与既有一致，绝不假数据（C1）。
- 配色：复用既有 token `--long/--short/--muted/--accent/--accent-soft/--surface/--surface-2/--border/--text`，不新增全局 CSS 变量；多空/中性/告警色调走 `explainKindTone`，与策略面板表一致。

### 2.6 序列：一笔自动交易开多的成交在实盘表展开

1. 自动交易于确认根触发开多 → 既有链路落 `trades.explain_json`（`meta.clOrdId="aragon…"`、`kind="factor_score"`、含 factors 断面）。
2. 数秒后 OKX `/trade/fills` 出现该订单成交（含 `clOrdId="aragon…"`、`ordId`）。
3. 前端 8s 轮询 `GET /trading/fills` → 端点取 OKX fills（现含 clOrdId）→ 查近窗解释行 → `index_trade_explains` 建索引 → `attach_explains` 按 clOrdId 命中 → fill 自带 `explain`。
4. `LiveFillsTable` 该行显示「买入 · 因子组合」角标 + chevron；用户点开 → `TradeExplainDetail` 渲染一句话原因 + 复合得分对阈值 + 因子归因条 + 风控快照 + 单号。

---

## 三、文件 / 模块变更计划（File / Module Change Plan）

| 文件 | 新建/修改 | 一句话意图 |
|---|---|---|
| `services/quant/app/okx/trading.py` | 修改 | `_normalize_fill` 增 `"clOrdId": str(r.get("clOrdId") or "")`（关联键，停止丢弃）与 `"tradeId": str(r.get("tradeId") or "")`（每行唯一 id，行 key 用，RV2）；其余字段不变。 |
| `services/quant/app/live_auto/fills_explain.py` | 新建 | 纯函数：`index_trade_explains(trades)`（按 clOrdId/ordId 建解释索引、同键保留最新、坏 JSON 安全跳过）、`attach_explains(fills, index)`（逐 fill clOrdId 主、ordId 副命中挂 explain，未中留 None，不可变返回）。坏 JSON 安全解析在本模块**内联**（~6 行），不反依赖 `schemas`（RV4）。 |
| `services/quant/app/db/repositories.py` | 修改 | 新增 `list_explained_trades(session, *, since_ts=None, limit=1000)`：取 `explain_json` 非空、`ts>=since_ts`、按 ts 倒序限量的 trade 行（关联用，单查询小结果集）。 |
| `services/quant/app/api/trading.py` | 修改 | `trading_fills` 取 OKX fills 后 best-effort 富化：按 fills 时间窗查解释行 → `index_trade_explains` → `attach_explains`；异常隔离（失败原样返回未富化 fills）。引入 `_FILL_JOIN_SLACK_SEC`/`_FILL_JOIN_MAX` 常量。 |
| `services/quant/app/schemas.py` | 修改 | `LiveFill` 增可空 `clOrdId: str \| None = None`、`tradeId: str \| None = None`、`explain: dict \| None = None`（向后兼容字段增量；camelCase。注意：未声明的字段会被 `response_model` 序列化丢弃，故 `attach_explains` 挂的 `explain` / `clOrdId` / `tradeId` 必须在此声明）。 |
| `apps/web/lib/tradingTypes.ts` | 修改 | `LiveFill` 增 `clOrdId?: string`、`tradeId?: string`、`explain?: TriggerExplain`（`import type { TriggerExplain } from "./types"`）。 |
| `apps/web/components/Strategy/TradeExplainDetail.tsx` | 修改 | 入参改为 `{ trade?: Trade; explain?: TriggerExplain }`，`explain` 优先；无任一时返回 `null`。向后兼容既有 `trade` 调用方与测试。 |
| `apps/web/components/Live/LiveFillsTable.tsx` | 修改 | 重构为逐行可展开：有 explain 行 = chevron + 类别角标 + 可点 `aria-expanded` + 展开 `<TradeExplainDetail explain=…/>`；无 explain 行 = 朴素「手动」不可展开。复用既有 token / `tradeExplain` 标签。 |
| `apps/web/components/Live/LiveFillsTable.test.tsx` | 新建 | 展开/收起切换、类别角标渲染、有/无 explain 分支、factor_score 因子行、空态（`NODE_ENV=test`，原生 DOM 断言）。 |
| `services/quant/tests/test_fills_explain.py` | 新建 | `index_trade_explains` 同键去重/坏 JSON 跳过/空键跳过；`attach_explains` clOrdId 主命中、ordId 副命中、partial-fill 共享 explain、未命中 None；解释不含凭证子串；端点富化 best-effort（注入抛错的桩仍返回未富化 fills）。 |

> 不改：`live_auto/*`（解释产生 / 落库链路不动）、`api/runs.py`、下单 / 平仓 / 护栏 / `live_order_audit` 调用链、`okx/trade_client.py`（`get_fills` 已回带含 clOrdId 的原始行）、既有 `AutoTradeTradesTable` / `tradeExplain.ts` / `FactorContributionBars`（直接复用）。

---

## 四、接口设计（Interface Design）

本期**不新增** REST / WS 端点，全部为向后兼容的字段增量。

### 4.1 REST `GET /api/py/trading/fills`（既有，元素扩展）

请求参数不变：`accountId?`、`instId?`、`limit=100`（`1..100`）。响应 `LiveFillsOut.fills[]` 的每个 `LiveFill` 元素新增两个可空字段：

```jsonc
// GET /api/py/trading/fills?instId=BTC-USDT-SWAP&limit=100
{
  "fills": [
    {
      "ts": 1751200000, "instId": "BTC-USDT-SWAP", "side": "buy",
      "fillPx": 64210.5, "fillSz": 0.03, "fee": -0.012,
      "ordId": "1234567890",
      "tradeId": "557923648",                   // 新增：OKX 每条成交唯一 id（前端行 key / 展开态身份，RV2）
      "clOrdId": "aragon18f3a2b1c004",          // 新增：OKX 回带（自动交易为 aragon/aragonc 前缀；手动/外部可能为空或他源）
      "explain": { /* TriggerExplain（见 §五）；无命中为 null */ }
    }
  ]
}
```

- 旧客户端忽略新增字段即可，无破坏性。
- `explain` 为 `null` 表示该成交未命中我方自动交易解释（手动下单、外部 / OCO 平仓、或早于解释链路的历史成交）。

### 4.2 前端纯函数 / 组件签名

```ts
// apps/web/lib/tradingTypes.ts
export interface LiveFill {
  ts: number; instId: string; side: "buy" | "sell";
  fillPx: number; fillSz: number; fee: number; ordId: string;
  tradeId?: string;                 // 新增（OKX 每行唯一 id；行 key / 展开态身份，RV2）
  clOrdId?: string;                 // 新增
  explain?: TriggerExplain;         // 新增（命中自动交易解释时存在）
}

// apps/web/components/Strategy/TradeExplainDetail.tsx（签名扩展，向后兼容）
export default function TradeExplainDetail(
  props: { trade?: Trade; explain?: TriggerExplain }
): JSX.Element | null;
```

复用既有：`explainKindLabel(kind)` / `explainKindTone(kind)`（`lib/tradeExplain.ts`）、`FactorContributionBars`、`contributionBars`。

### 4.3 后端纯函数签名（`services/quant/app/live_auto/fills_explain.py`）

```python
def index_trade_explains(trades: list[Trade]) -> dict[str, dict]:
    """按 clOrdId / ordId 把「带解释的 trade」建成 {key: explain} 索引。
    同键保留最新（输入按 ts 倒序）；空键 / 坏 JSON 安全跳过。"""

def attach_explains(fills: list[dict], index: dict[str, dict]) -> list[dict]:
    """逐 fill：clOrdId 主、ordId 副命中则挂 explain，未中置 None；返回新列表（不就地改）。"""
```

---

## 五、数据模型（Data Model）

### 5.1 DB：无新增列、无迁移

复用既有 `trades.explain_json`（`auto-trade-explainability §5.1` 已加；`TEXT` nullable，序列化的 `TriggerExplain`，`meta.clOrdId/ordId` 已在内）。本期**不**给 `Trade` 加 `cl_ord_id`/`ord_id` 列，连接键直接从 `explain_json.meta` 读取，零迁移、覆盖全部历史行（适度性，见 §七备选）。

### 5.2 关联使用的 `TriggerExplain` 子结构（既有，不改形状）

关联与展开依赖既有 `TriggerExplain`（`lib/types.ts:223-255` / 后端 `live_auto/explain.py`，`schema:1`），本期只**消费**，不改其字段：

```ts
interface TriggerExplain {
  schema: number;
  kind: "factor_score" | "atomic" | "risk_exit" | "reverse" | "session_stop" | "external_close";
  side: "long" | "short" | "close";
  headline: string; summary?: string;
  metrics: { label: string; value: string; tone?: "long"|"short"|"neutral"|"warn"; hint?: string }[];
  composite?: { score: number; preSquash: number; squash: boolean; enterLong?: number; enterShort?: number; exitBand?: number };
  factors?: { factorKey: string; name: string; direction: "long"|"short"; value: number; zscore: number; weight: number; contribution: number; share: number; isTopDriver: boolean }[];
  risk?: { stopLossPrice?: number|null; takeProfitPrice?: number|null; stopLossPct?: number|null; takeProfitPct?: number|null; maxHoldBars?: number|null };
  meta?: { strategyName?: string; strategyKind?: string; instId?: string; bar?: string; barTs?: number; clOrdId?: string; ordId?: string };  // ← 关联键来源
}
```

### 5.3 关联键语义与可靠性

| 成交类型 | trade 侧 clOrdId | trade 侧 ordId | OKX fill 侧 | 关联结论 |
|---|---|---|---|---|
| 自动开仓（factor_score / atomic） | 必有（生成 `aragon…`） | 多数有 | 含 clOrdId + ordId | clOrdId 主命中，归因完整 |
| 信号 / 时间止盈(max_hold) / 反手 平仓 | 必有 | 限价平多有、市价平可能空 | 含 clOrdId | clOrdId 命中（不依赖 ordId） |
| 会话止盈止损（flatten 整仓平） | 必有（`aragonc…`） | 市价平为空 | 含 clOrdId | clOrdId 命中 |
| external_close（OCO / 外部干预合成行） | 无（未下单） | 无 | clOrdId 为 OKX 算法单 / 他源 | 一般不命中 → UI 标「手动 / 外部」（R5） |
| 纯手动下单 / 早于解释链路的历史 | — | — | 任意 | 不命中 → 朴素行 |

---

## 六、测试与验收标准（Testing & Acceptance Criteria）

### 6.1 后端单测（pytest）—— `test_fills_explain.py`

- `index_trade_explains`：
  - 一笔含 `meta.clOrdId/ordId` 的解释 trade → 索引含这两个键且都指向同一 explain。
  - 同一 clOrdId 出现两次（ts 倒序输入）→ 只保留**最新**一笔。
  - `explain_json` 为坏 JSON / 空 / 无 `meta` / 空键 → **安全跳过**，不抛异常、不进索引。
- `attach_explains`：
  - fill 的 `clOrdId` 命中 → 挂对应 explain；`clOrdId` 不中但 `ordId` 中 → 挂（副键路径）；都不中 → `explain is None`。
  - **partial fill**：同 `clOrdId` 的两条 fill → **都**挂同一 explain。
  - 不就地修改输入 fills（断言原 dict 无 `explain` 键 / 原值不变）。
- 端点富化（在 `app.api.trading.fetch_live_fills` 边界打桩注入 fills；既有 `_FakeTradeClient` 暂无 `get_fills`，按需补一个，RV5）：
  - `list_explained_trades` 抛异常注入 → `trading_fills` **仍返回未富化 fills**（`explain` 缺省 / None），不 500。
  - 富化后任何 fill 的 `explain` 序列化字符串里**不含** `api_key/secret/passphrase` 子串（沿用 explain 不含凭证不变量）。
  - **时间窗回归（RV1/P0，防回归铁律）**：构造一笔带 `meta.clOrdId` 的 explain trade，其 `ts` 比对应 fill 的 `ts` **早一个典型 bar 周期**（如 900s，模拟 15m 确认根开盘 ts 早于成交 ts）→ 仍按 clOrdId 命中并挂上 explain（即断言 `_FILL_JOIN_SLACK_SEC` 足够覆盖 bar 周期）。
- `_normalize_fill`：原始行含 `clOrdId` / `tradeId` → 归一结果含之；缺失 → 空串（容错）。

### 6.2 前端测试（vitest，`NODE_ENV=test`，原生 DOM 断言）—— `LiveFillsTable.test.tsx`

- 有 `explain` 的行：默认收起；存在 `aria-expanded=false` 控件与类别角标文案（如「因子组合」）；点击后 `aria-expanded=true` 且渲染 `TradeExplainDetail` 内容（headline / metric）。
- factor_score 的 explain：展开渲染因子归因条（命中 `FactorContributionBars` 行）。
- 无 `explain` 的行：渲染「手动」标签且**无**展开控件（断言无 `aria-expanded` 按钮）。
- 展开态稳定性（RV2）：先展开某行，再以「列表头部新增一行」重渲染（模拟 8s 轮询），断言原行**仍展开**（验证按 `tradeId` 稳定键持有展开态，而非数组下标）。
- partial fill（两条同 `clOrdId`/`ordId`、不同 `tradeId` 的 fill）：渲染两行且各自独立可展开，无 React duplicate-key 警告。
- 加载态 / 错误态 / 空态文案保持（沿用既有断言）。
- `TradeExplainDetail` 既有测试不回归（仍以 `trade` 入参通过）。

### 6.3 验收清单（Definition of Done 对照）

1. `GET /api/py/trading/fills` 每个 fill 多回 `clOrdId`（OKX 回带）与可空 `explain`（命中自动交易解释时存在），旧字段与契约不变。
2. 实盘账户模式「成交记录」表中，**自动交易产生的每一条成交可逐行展开**查看触发原因；手动 / 外部成交诚实标注且不空洞展开。
3. `factor_score` 策略触发的成交展开后显示**因子归因图**（复用既有 `TradeExplainDetail`/`FactorContributionBars`），与策略面板表观感一致。
4. 关联以 **clOrdId 为主、ordId 为副**，覆盖市价平仓（ordId 为空）场景；partial fill 共享同一解释。
5. 解释富化失败 / 解释行查询异常时，**成交记录照常显示**（best-effort，端点不 500）；不新增任何下单 / 平仓 / 护栏调用；不泄露凭证。
6. 全量 `pytest`（services/quant）与 `vitest`（apps/web）通过，新测覆盖正常 + 异常 + 边界路径。

---

## 七、风险与缓解（Risks & Mitigations）

| # | 风险 | 影响 | 缓解 |
|---|---|---|---|
| R1 | 富化读本地 `trades` 失败 / 慢，拖累「成交记录」可见性（真实资金视图） | 中 | §2.4 整段富化 `try/except` 隔离，失败原样返回未富化 fills；解释行查询带 `since_ts` 时间窗 + `limit` 上限，结果集小；端点本就只读。 |
| R2 | clOrdId 关联落空（trade 侧 best-effort 未采到 / 历史早期行无 meta 键 / **时间窗筛错**） | 中 | clOrdId 主、ordId 副双键关联；两者皆缺则 UI 回落「手动」诚实标注（不臆造解释）；不影响成交本身显示。**时间窗 slack 已按最大 bar 周期取值（RV1/P0 修复）**，杜绝「确认根开盘 ts 早于成交 ts 一个周期」导致的整段漏命中。 |
| R3 | OKX `/trade/fills` 实际未回 `clOrdId`（与 API 文档不符的边缘情况） | 低 | `_normalize_fill` 取值容错为空串；空 clOrdId 不进关联，自动回落 ordId 副键；仍命中开仓类成交。实现期以一笔真实自动开仓端到端复核（见 §八）。 |
| R4 | 同 clOrdId 被历史复用导致错配 | 极低 | clOrdId 由 `ms(hex)+seq` 生成、进程内单调，复用概率极低；`index_trade_explains` 同键保留最新；副键 ordId 为 OKX 全局唯一进一步纠偏。 |
| R5 | external_close / 手动成交无解释，展开「空洞」 | 低 | 无 explain 行不提供展开控件（渐进披露），仅以「手动 / 外部」淡色标注；杜绝空白详情。 |
| R6 | partial fill 多行共享同一 clOrdId / ordId / 解释，疑似「重复」且键冲突 | 低 | 语义正确（同单多成交同因）；**行身份用 OKX 每行唯一的 `tradeId`**（非 clOrdId / ordId——后者在 partial fill 下不唯一，RV2/P1），杜绝 React 撞键与展开态错挂；如需可副标注「部分成交」（实现期可选，不阻塞验收）。 |
| R7 | 每 8s 轮询新增一次 DB 读，热路径压力 | 低 | 查询 `explain_json IS NOT NULL` + ts 窗 + limit，命中小表；与既有 positions/fills 轮询同量级；必要时后续加部分索引（本期不需）。 |
| R8 | `LiveFill.explain` 用 `dict`（后端）/ `TriggerExplain`（前端）形状漂移 | 低 | 复用既有 `auto-trade-explainability` 同构契约与 `schema` 版本号；前端 `explainKindLabel` 对未知 kind 回落「自动交易」，不空白。 |
| R9 | 跨账户错配（trade 表非账户维度） | 低 | clOrdId/ordId 与「下出该成交的订单」一一对应，天然只匹配同账户成交；解释不含账户敏感信息。 |
| R10 | `TradeExplainDetail` 签名扩展破坏既有两处调用 / 测试 | 低 | 新增入参为**可选**且 `explain` 优先、回落既有 `resolveExplain(trade)`；既有调用方仍传 `trade`，行为不变；测试不回归。 |

### 备选方案讨论（供 Subtask #1 评审）

- **独立查询端点 `POST /trading/fills/explain`**（body 传 clOrdIds/ordIds，返回 `{key: explain}`）：解耦 OKX 透传与 DB 关联，但每 8s 轮询多一次往返、前端需自行合并多源；本方案取「服务端单次富化」以贴合既有「单数据源直渲」范式与最小前端改动。若评审更看重端点职责纯粹，可平移到该端点（关联纯函数与仓储查询可原样复用）。
- **给 `Trade` 加 `cl_ord_id`/`ord_id` 索引列 + 回填**：可获索引化精确 join，但需 additive 迁移 + 历史回填（`json_extract`）。鉴于自动交易 trade 量小、fills ≤100、`explain_json` 已含连接键，本期取「读 JSON.meta + 时间窗限量查询」的零迁移路径；若未来成交量显著增长，可平滑升级为索引列。

---

## 八、实现注意事项（给下游工程师）

- 遵循 CLAUDE.md 阈值：新文件 < 800 行、方法 < 50 行、嵌套 ≤ 4、布尔条件 ≤ 3、单行 ≤ 80 列；`fills_explain.py` 与前端表逻辑拆小纯函数。
- 命名：关联相关统一 `explain` / `index` / `clOrdId` / `ordId`，避免 `data/info/temp`。
- 错误处理：边界（OKX / DB）一次校验；富化失败显式 `logger.warning` 并回落，不吞语义、不阻断成交可见。
- 安全：`explain` 只含行情 / 因子数值与公开元信息，沿用「不含凭证」不变量；端点不返回任何密钥，`clOrdId` 非敏感（系统自造单号）。
- HCI / 顶级设计：整行可点 + 右侧 chevron 指示 + 键盘可达（`role`/`tabIndex`/`aria-expanded`/`focus-visible`）；展开用高度 / 透明度过渡，避免布局跳动；类别角标用既有 `explainKindTone` 配色（风控 / 会话 / 外部用 warn，多空中性）；无解释行不诱导展开（渐进披露：先概览价量、需要时再下钻原因）。
- 复用优先：详情、因子图、标签 / 色调一律复用 `auto-trade-explainability` 已落地组件，保证两处成交表**像素级一致**，不另起炉灶。
- 后端落地细节（RV4/RV5）：`api/trading.py` 补模块级 `logger = logging.getLogger(__name__)`；`fills_explain.py` **内联**坏 JSON 安全解析（不 import `schemas`），仅依赖标准库与（仅类型标注用的）`Trade`；从 `..live_auto.fills_explain` import 两个纯函数到端点。
- 前端行身份（RV2）：React 行 key 与展开态 `Set<string>` 一律以 `fill.tradeId` 为键（缺失回落 `${ordId}-${ts}-${index}`），切勿用 clOrdId / ordId 单独作键。
- 实现期端到端复核（真实数据硬约束）：开一笔真实 / 沙盒自动交易（**用 5m / 15m 等非 1m 周期**以覆盖 RV1），确认 OKX `/trade/fills` 回带的 `clOrdId` / `tradeId` 与 `explain.meta.clOrdId` 一致，且在「确认根开盘 ts 早于成交 ts 约一个周期」下仍成功命中并展开归因（验证 R3 + RV1/P0 修复在真实数据上生效）。

---

*本文档 v2（Subtask #1 评审与修复；v1 为 Subtask #0 方案设计）：基于对 `LiveFillsTable`/`LiveBottomPanel`/`tradingTypes`/`tradingApi`、`api/trading.py`、`okx/trading.py`(`_normalize_fill`/`fetch_live_fills`)、`okx/trade_client.py`(`get_fills`)、`db/models.py`(`Trade`)、`db/repositories.py`(`append_live_trade`/`list_trades`)、`schemas.py`(`trade_out`/`LiveFill`)、`live_auto/explain.py`/`session.py`/`executor.py`、以及既有 `auto-trade-explainability` 方案与已落地复用件（`tradeExplain.ts`/`TradeExplainDetail.tsx`/`FactorContributionBars.tsx`/`types.ts`）逐一核对源码后编写。实现由 Subtask #2 依本方案（经 Subtask #1 评审为 v2 后）落地。*

---

## 评审结论（Review Verdict · Subtask #1）

**有条件通过（Approved with conditions）。**

v1 技术路线扎实——以 `clOrdId` 为主键、`ordId` 为副键，把既有 `trades.explain_json` 的触发解释 best-effort 关联到真实 OKX 成交行，**零 DB 迁移、复用既成解释组件、字段增量向后兼容、解释失败不阻断成交可见**——方向正确、适度、与现有架构一致。逐节核对源码后，关联链路两侧均已就位、可行性成立：`_normalize_fill` 确在丢弃可补回的 `clOrdId`；`executor._make_trade` → `session._inject_explain_meta` → `append_live_trade` 把 `meta.clOrdId/ordId` 持久化；`resolveExplain` 恒返回非空，故 `TradeExplainDetail` 签名扩展向后兼容；`append_live_trade` 仅 `LiveAutoSession` 调用，解释索引天然纯净。

评审发现并已就地修复：一处会令**特性静默失效**的 **P0（RV1：关联时间窗 slack 远小于 bar 周期）**，与一处影响 partial-fill 展开正确性的 **P1（RV2：行身份 / 展开态）**。文档升版 **v2**，**无遗留 P0/P1**。

**放行条件（实现期须满足，对应 P2）：**
1. **RV4**：`fills_explain.py` 保持纯函数零外部耦合（内联坏 JSON 安全解析，不反依赖 `schemas`）。
2. **RV5**：端点富化测试在 `app.api.trading.fetch_live_fills` 边界打桩（或为 `_FakeTradeClient` 补 `get_fills`）；`api/trading.py` 补模块级 `logger`。
3. **RV1 真实数据验证**：落地后按 §八做一次真实 / 沙盒自动交易端到端复核——在 5m / 15m 等非 1m 周期下，确认根开盘 `barTs` 早于成交 `ts` 约一个周期时，`clOrdId` 仍命中并展开归因（即 P0 修复在真实数据上确实生效）。

满足上述条件即可进入 **Subtask #2 代码开发**。

---

*评审签署：资深评审 · Subtask #1 · 文档状态 v2 · 无遗留 P0/P1。*
