# 实盘挂单可视化（K 线虚线 + 持仓内委托明细）开发方案

- **Feature**: `live-pending-orders-visualization`
- **Version**: v2（评审定稿 · Subtask #1 已评审并修订全部 P1，P2 列为落地条件）
- **作者**: Subtask #0 · 方案设计
- **轮次**: 第 2 轮（在第 1 轮「实盘平仓 / 一键市价全平」commit `5e57e43` 之上继续）

---

## 评审记录（Review Notes · Subtask #1）

> 评审维度：可行性（Feasibility）/ 完整性（Completeness）/ 一致性（Consistency）/ 规模适配（Right-sizing）。
> 评审基线已逐一核对真实代码：`services/quant/app/okx/trade_client.py`、`private_client.py`、
> `app/api/trading.py`、`app/schemas.py`、`apps/web/components/Chart/CandleChart.tsx`、`ChartWorkspace.tsx`、
> `apps/web/components/Live/LivePositionsTable.tsx`、`LiveBottomPanel.tsx`、`apps/web/lib/tradingApi.ts`。
> **整体结论：方案在现有技术栈上可落地、与既有约定一致、规模适中**；下表 P1 已在正文（v2）直接修订，P2 作为
> 落地条件收敛在文末「评审结论」。

| # | 维度 | 严重度 | 问题 | 处置 |
|---|---|---|---|---|
| C1 | 可行性 | **P1** | 持仓子行涨跌幅参考价文档写的是 `markPx`，但 `OkxAccountPosition` / `LivePosition` **无 `markPx` 字段**——每仓现价是 `last`（「标记价」列即渲染 `pos.last`，见 `LivePositionsTable.tsx:152`）。照原文实现会取到 `undefined`、TS 报错或算出 `NaN%`。 | 正文 §2.3(e)/§4.3/§5/§一 统一改为 `pos.last`；**图表线**参考价仍取 ChartWorkspace 的 `markPrice`（= `lastPrice ?? candles.at(-1)?.c`，真实存在）。 |
| C2 | 一致性 | **P1** | §2.1 宣称两个消费方用「同一查询键」由 React Query 自动合并为一次请求，但 §2.3(d) 图表硬编码键 `["trading","openOrders", null]`、§2.3(e) 持仓表用 `["trading","openOrders", accountId ?? null]`——两处键的派生不一致。今天 `ChartWorkspace` 渲染 `<LiveBottomPanel>` 未透传 `accountId`（两侧都回落 `null`）故暂时去重生效；一旦接入账户切换即**静默分裂为两次 OKX 私有轮询**，违背 R7 限频缓解，且与文档「一次请求」的承诺直接矛盾。 | 正文新增共享键工厂 `openOrdersKey(accountId)`，两个消费方同源同参；并要求 `ChartWorkspace` 透传与持仓表一致的 `accountId`（当前同为 `undefined`→`null`）。 |
| C3 | 完整性 | P2 | 第 1 轮 TP/SL 经 `attachAlgoOrds`（`tpOrdPx/slOrdPx="-1"` 触发后市价）随**开仓单**附挂（`order_builder.py:115-127`）。OKX 语义下**附挂 algo 单在父单成交前一般不进入 `orders-algo-pending`**——未成交的限价开仓单，其 TP/SL 存在短暂不可见窗口。验收 #2 隐含「持仓已开」。归一按 `tpTriggerPx`/`slTriggerPx` 字段展开（不依赖 ordType 名）已对 oco/conditional 命名差异稳健，方向正确。 | 正文 §2.4 / §七补注该时序边界，列为落地条件。 |
| C4 | 规模适配 | P2 | `CandleChart.tsx` 现 620 行；新增价格线 reconcile effect + hover 命中 + 内联 `OrderLineTag` 预计 +~100 行至 ~720 行，逼近 CLAUDE.md 800 行上限，且 `onCross` 复杂度上行。 | 正文 §3 建议把价格线 reconcile + hover 收敛进自定义 hook `useOrderPriceLines`，保持文件 ≤800 行 / 单函数 ≤50 行 / 圈复杂度 ≤10，列为落地条件。 |
| C5 | 一致性 | P2 | §2.2(c) 路由保留 `instId` 服务端过滤入参并透传给 `fetch_live_open_orders`，但 §2.1 叙述「后端不按 instId 过滤」、前端从不传该参——入参为未用路径且无测试覆盖。 | 正文 §4.1 标注：保留则补 instId passthrough 单测，否则落地时删除该入参，列为落地条件。 |
| C6 | 完整性 | P2 | `open-orders` 查询可独立于持仓查询失败（502）；文档仅覆盖「OKX 整体不可达」，未规定「持仓正常但委托查询失败」时子行 / 图表的隔离降级。 | 正文 §六新增验收项：委托查询失败不得阻断持仓表与图表，静默降级为「无委托」+ 可选轻提示。 |

> 复核确认（无需改动，记录为通过项）：`get_pending_orders` 已存在（`trade_client.py:112`）；`OKXPrivateError`
> 子类化 `OKXError` 且全局 `okx_error_handler` 统一回 502（`private_client.py:27` / `main.py:51`）；
> `createPriceLine({price,color,lineStyle,axisLabelVisible,title})` 与 `subscribeCrosshairMove` / `candleSeriesRef`
> 均已在用（`CandleChart.tsx:300`/`240`/`104`）；`request`/`qs` 客户端契约匹配（`tradingApi.ts:19-50`）；
> `_resolve_account`/`_creds_from_row` 与读类不守门约定一致（`api/trading.py:60-99`）；`LiveFill`/`LiveFillsOut`
> 提供了 `LiveOpenOrder`/`LiveOpenOrdersOut` 的现成范式（`schemas.py:573-586`）。

---

## 一、Overview（概述）

第 1 轮已经为实盘账户实现了「单仓平仓（限价挂单 / 市价整仓）」与「一键市价全平」，相关 UI 收敛在
`apps/web/components/Live/`（`LivePositionsTable` 行内「平仓」、表头「一键市价全平」、`ClosePositionModal`、
`MarketCloseAllConfirm`），后端走 `services/quant/app/api/trading.py` →
`services/quant/app/okx/trading.py`，真实调用 OKX V5。但是「挂出去的平仓委托」与「随单的止盈止损（TP/SL）」
当前**在界面上完全不可见**：用户提交一笔限价平仓后，挂单只存在于 OKX，前端既不在 K 线图上标注其价格，
也不在持仓记录里列出，用户无法直观判断「我的平仓单挂在哪个价位、离现价多远、是否还在挂着」。

本轮要补齐这一可视化闭环，共两件事：

1. **K 线图标注平仓挂单价位**：当某标的存在未成交的「平仓 / 止盈 / 止损」委托时，在主图上以**不同色彩的
   水平虚线**画出每一笔委托的价格位置；该虚线在**最右侧价格轴**常显其价格标签，鼠标移动到虚线附近时，
   在右侧价格区弹出该委托的**价格与相对最新价（`last`，图表线取 ChartWorkspace `markPrice`）的涨跌百分比**。

2. **持仓记录下方折叠展示委托明细**：在「当前持仓」表里，每一行持仓下方挂接一个可折叠区域，列出该持仓
   对应的**止盈、止损、限价平仓**委托单（触发价 / 委托价、数量、方向、状态）。**默认展开**，点击行首的折叠
   箭头可收起，再点击重新展开。

为支撑以上两点，需要新增一个**只读**后端接口 `GET /api/trading/open-orders`，聚合 OKX 的「未成交普通挂单」
（`/api/v5/trade/orders-pending`）与「未成交策略委托」（`/api/v5/trade/orders-algo-pending`，OCO/conditional
即随单 TP/SL），归一为统一的「未结委托」模型；前端在实盘模式下轮询该接口，分别驱动图表虚线与持仓子行。
本轮**不改动任何下单 / 平仓写路径**，遵循项目铁律：只读接口不受 `LIVE_TRADING_ENABLED` 守门、绝不伪造数据
（OKX 不可达即显式报错），密钥永不出接口。整体保持「美观、优雅、符合人机交互最佳实践」的顶级设计基调，
颜色 / 间距 / 折叠动效复用既有 `--long/--short/--accent/--muted` 主题变量与 `ui/` 原子组件。

---

## 二、Technical design（技术设计）

### 2.1 架构与数据流

```text
OKX V5 私有 REST
  GET /api/v5/trade/orders-pending          (普通挂单：限价平仓 / 限价开仓等)
  GET /api/v5/trade/orders-algo-pending     (策略委托：oco / conditional，即随单 TP/SL)
        │
        ▼  services/quant/app/okx/trade_client.py
  OKXTradeClient.get_pending_orders(instId?)        ← 已存在（第 1 轮预留，未被使用）
  OKXTradeClient.get_algo_pending_orders(ordType, instId?)   ← 新增
        │
        ▼  services/quant/app/okx/trading.py
  fetch_live_open_orders(creds, instId?) → list[dict]        ← 新增（归一 + 分类）
        │
        ▼  services/quant/app/api/trading.py
  GET /trading/open-orders  →  {"orders":[LiveOpenOrder...]}  ← 新增（只读，不守门）
        │ (Next rewrite /api/py/* )
        ▼  apps/web/lib/tradingApi.ts
  tradingApi.openOrders(accountId?)                          ← 新增
        │
        ├──▶ apps/web/components/Live/LivePositionsTable.tsx
        │       useQuery(["trading","openOrders",accountId])  → 按 (instId,posSide) 分组
        │       每行持仓下方渲染 <PositionOrdersRow>（默认展开 / 可折叠）
        │
        └──▶ apps/web/components/Chart/ChartWorkspace.tsx
                useQuery(同键，命中去重) → 过滤当前 instId → buildOrderPriceLines()
                透传 orderLines 给 <CandleChart>，由其 createPriceLine 画虚线 + 悬停 tooltip
```

要点：

- **单一数据源 + React Query 去重（C2 修订）**：`LivePositionsTable` 与 `ChartWorkspace` **必须经同一键工厂**
  `openOrdersKey(accountId)`（见 §2.3b）派生查询键 `["trading","openOrders", accountId ?? null]`，且两侧**喂入同一
  `accountId`**——只有键完全相同，TanStack Query 才会合并为一次网络请求。**铁律**：禁止任一消费方硬编码 `null`
  或自行拼键（否则账户切换后键分裂 → 两次 OKX 私有轮询，违背 R7 限频缓解）。当前 `ChartWorkspace` 渲染
  `<LiveBottomPanel>` 未透传 `accountId`，本轮需把与持仓表一致的 `accountId` 一并下传（缺省同为 `undefined`→`null`，
  保持单请求）。前者需要全标的（匹配每个持仓），后者只取当前 `instId`，均**客户端过滤**，因此**前端不传 `instId`、
  后端默认返回全量未结委托**（服务端 `instId` 过滤入参保留但前端不用，见 §4.1/C5）。
- **轮询节奏**：`refetchInterval: 5_000`（与持仓表一致），`refetchOnWindowFocus: true`；平仓 / 下单成功后
  复用既有 `invalidate()`，把 `["trading","openOrders"]` 一并失效，做到「挂单后秒级出现 / 撤单后秒级消失」。
- **lightweight-charts v4 约束**：v4 无原生「带 hover 事件的价格线」。价格线用
  `series.createPriceLine({ price, color, lineStyle, axisLabelVisible:true, title })` 实现——
  `axisLabelVisible` 即「最右侧价格轴常显价格」；涨跌百分比的 hover 通过既有
  `chart.subscribeCrosshairMove` 回调里把每条线价格 `series.priceToCoordinate()` 转为 Y 像素、与
  `param.point.y` 比对（阈值 ~6px）来判定命中，命中则用与 `TradeTooltip` 同款的绝对定位浮层在右侧渲染
  「委托价 + 涨跌幅」。

### 2.2 后端关键路径

**(a) 新增策略委托查询** — `services/quant/app/okx/trade_client.py`：

```python
async def get_algo_pending_orders(self, ord_type: str, inst_id: str | None = None) -> list:
    """GET /api/v5/trade/orders-algo-pending：未触发的策略委托（签名 GET）。

    ordType 为必填，常用 "oco"（随单止盈止损）/ "conditional"（单向条件单）。
    """
    params: dict[str, str] = {"ordType": ord_type}
    if inst_id:
        params["instId"] = inst_id
    return await self._get("/api/v5/trade/orders-algo-pending", params)
```

**(b) 服务层聚合 + 归一 + 分类** — `services/quant/app/okx/trading.py` 新增：

```python
async def fetch_live_open_orders(creds: AccountCredentials,
                                 inst_id: str | None = None) -> list[dict]:
    """未结委托（普通挂单 + 策略委托）。OKX 不可达 → OKXPrivateError（绝不假数据，C1）。"""
    client = OKXTradeClient(creds.api_key, creds.secret_key, creds.passphrase)
    try:
        pending, oco, cond = await asyncio.gather(
            client.get_pending_orders(inst_id),
            client.get_algo_pending_orders("oco", inst_id),
            client.get_algo_pending_orders("conditional", inst_id),
        )
    except OKXError as exc:
        raise OKXPrivateError(_map_trade_code(exc.code),
                              _redact(exc.message, creds.secret_key, creds.passphrase)) from exc
    finally:
        await client.aclose()
    out: list[dict] = [_normalize_pending_order(r) for r in pending]
    for r in (*oco, *cond):
        out.extend(_normalize_algo_order(r))   # 一条 OCO 可展开成 tp + sl 两条
    return out
```

- `_normalize_pending_order(r)`：把普通挂单归一为统一形态，并用 `_classify_pending_kind(r)` 判定 `kind`：
  - `reduceOnly == true` → `"limit_close"`；
  - 否则按对冲语义判：`posSide=="long" and side=="sell"` 或 `posSide=="short" and side=="buy"` → `"limit_close"`；
  - 其余 → `"limit_open"`（开仓挂单；图表与持仓子行**不展示**，但接口诚实返回，前端按 kind 过滤）。
- `_normalize_algo_order(r)`：一条策略委托可能同时含 `tpTriggerPx` 与 `slTriggerPx`（OCO），分别产出
  `kind="tp"`（`triggerPx=tpTriggerPx`）与 `kind="sl"`（`triggerPx=slTriggerPx`）两条归一记录，共享 `algoId`；
  只含其一则产出一条。`px` 取 `tpOrdPx/slOrdPx`（`-1` 表示触发后市价，归一为 `None`）。
- 所有数值经 `_optional_float` 容错；时间 `cTime`(ms) → 秒。**绝不**返回任何密钥字段。

**(c) 路由** — `services/quant/app/api/trading.py` 新增（紧随 `/fills` 之后，读类不守门）：

```python
@router.get("/open-orders", response_model=LiveOpenOrdersOut)
async def trading_open_orders(accountId: int | None = Query(None),
                              instId: str | None = Query(None),
                              session: AsyncSession = Depends(get_session)) -> dict:
    """未结委托（普通挂单 + 策略委托）：真实 OKX，轮询源，鉴权失败→502。"""
    row = await _resolve_account(session, accountId)
    return {"orders": await fetch_live_open_orders(_creds_from_row(row), instId)}
```

### 2.3 前端关键路径

**(a) 类型 + 客户端**：`tradingTypes.ts` 增 `LiveOpenOrder` / `LiveOpenOrdersResponse`；`tradingApi.ts` 增
`openOrders: (accountId?) => request<LiveOpenOrdersResponse>(\`/trading/open-orders${qs({ accountId })}\`)`。

**(b) 纯函数 helper**：新增 `apps/web/lib/openOrders.ts`（无 IO，配套单测）：

- `openOrdersKey(accountId?)`（**C2 修订，唯一键工厂**）：返回 `["trading","openOrders", accountId ?? null] as const`。
  `LivePositionsTable` 与 `ChartWorkspace` **均只能经此函数取键**，杜绝任一方硬编码 `null`（见 §2.1）。
- `isPositionOrder(kind)`：`kind` ∈ `{"limit_close","tp","sl"}` 为持仓相关委托（过滤掉 `limit_open`）。
- `positionKey(instId, posSide)` 与 `groupOrdersByPosition(orders, positions)`：把委托按 `(instId, posSide)`
  归到对应持仓；net 模式 `posSide` 统一为 `"net"`。返回 `Map<string, LiveOpenOrder[]>`。
- `orderKindMeta(kind)`：返回 `{ label, color, lineStyle }` —— `limit_close`→「限价平仓」/`--accent`/虚线，
  `tp`→「止盈」/`--long`/点划线，`sl`→「止损」/`--short`/点划线（颜色互不相同，满足「不同色彩虚线」）。
- `pctChange(price, refPx)`：`refPx>0 ? (price-refPx)/refPx : null`（供图表 hover 与子行展示涨跌幅；
  参考价由调用方注入——图表线用 ChartWorkspace `markPrice`，持仓子行用该持仓 `last`，见 C1）。
- `buildOrderPriceLines(orders, instId, refPx)`：过滤当前 `instId` 且 `isPositionOrder` 的委托，映射为
  `OrderPriceLine[] = { id, price, kind, color, lineStyle, label, changePct }`（`price` 取 `triggerPx ?? px`；
  `refPx` 即 ChartWorkspace `markPrice`）。`id` 用 `` `${ordId ?? algoId}:${kind}` ``（显式括号定界，
  保证 OCO 展开的 tp/sl 同 `algoId` 但 `kind` 不同 → 主键不撞，reconcile 不抖动）。

**(c) 图表虚线 + hover** — `CandleChart.tsx`：

- 新增可选 prop `orderLines?: OrderPriceLine[]`。新增 `orderLineRef = useRef<Map<string, IPriceLine>>`。
- 新 effect（依赖 `[orderLines, theme]`）：增量 reconcile —— 删除已不存在的线（`series.removePriceLine`），
  新增 / 更新现存线（`createPriceLine`/`applyOptions`，`lineStyle` 取 helper 给的虚线样式，
  `axisLabelVisible:true`，`title` 用短标签如「平仓」「止盈」「止损」）。复用 `livePrice` 价格线的既有写法。
- hover：扩展既有 `onCross` 回调——遍历 `orderLines`，用 `candleSeriesRef.current.priceToCoordinate(price)`
  得 Y，若 `|y - param.point.y| <= HOVER_PX(6)` 命中，则 `setOrderHover({ line, y })`；新增绝对定位浮层
  `<OrderLineTag>`（贴右价轴，`right:0; top:y`）显示 `委托价 + (pctChange 着色±%)`。卸载时清空 ref。

**(d) 图表接线** — `ChartWorkspace.tsx`：

- 新增 `useQuery`（`enabled: accountMode === "live"`，键 **`openOrdersKey(accountId)`**——与持仓表同源 `accountId`，
  C2）取全量未结委托。本轮需把 `accountId` 一并下传到 `<LiveBottomPanel>`/`<LivePositionsTable>`，保证两侧键一致。
- `const orderLines = useMemo(() => buildOrderPriceLines(openOrdersQ.data?.orders ?? [], instId, markPrice), [...])`
  （`markPrice` = `lastPrice ?? candles.at(-1)?.c`，已存在于 `ChartWorkspace`）。
- `<CandleChart ... orderLines={accountMode === "live" ? orderLines : undefined} />`。
- **错误隔离（C6）**：`openOrdersQ` 失败（502）时 `orderLines` 退化为空数组——图表只是不画委托线，**不得**因委托查询
  失败而影响 K 线 / 既有 `livePrice` 线渲染。

**(e) 持仓子行折叠** — `LivePositionsTable.tsx`：

- 新增 `useQuery`（**键 `openOrdersKey(accountId)`，与图表同源同参 → 命中去重**，C2）取全量未结委托；
  `const grouped = groupOrdersByPosition(orders, open)`。
- `PositionRow` 拆为两个 `<tr>`：主行（在「合约」单元格前加折叠箭头按钮，`aria-expanded`）+ 明细行
  （`<tr><td colSpan={7}>` 内渲染 `<PositionOrdersRow orders={grouped.get(key) ?? []} refPx={pos.last} />`）。
  **C1 修订**：每仓参考价取 `pos.last`（即「标记价」列现值，`OkxAccountPosition` **无 `markPx` 字段**），
  子行涨跌幅 = `pctChange(triggerPx ?? px, pos.last)`。
- 折叠状态：`const [expanded, setExpanded] = useState<Set<string>>(() => new Set(allKeys))`（**默认全展开**）。
  点击箭头 toggle 该 `positionKey`。明细行用 `grid` 列表展示每条委托：`类型徽标 | 触发/委托价 | 张数 | 涨跌幅`。
  无委托时显示「该持仓暂无止盈止损 / 平仓挂单」。
- **错误隔离（C6）**：委托查询失败（502）时该子行退化为空态文案（同「暂无」），**不得**阻断持仓主行渲染或
  顶层持仓表的错误态判定（持仓表 `isError` 只看 `positions` 查询）。`invalidate()` 仍把 `openOrders` 一并失效。

### 2.4 操作时序（用户视角）

1. 用户在 `ClosePositionModal` 提交一笔限价平仓 → 第 1 轮写路径不变，OKX 接受挂单。
2. `onClosed → invalidate()`（本轮把 `openOrders` 也纳入失效）→ 5s 内（或立即）`open-orders` 刷新。
3. 「当前持仓」对应持仓行下方明细区出现「限价平仓 @ px ×sz」一条；主图同时出现一条 `--accent` 虚线，
   右价轴常显该价格；鼠标移到虚线上，右侧弹出「63,250.0  +1.24%」。
4. 该挂单成交 / 撤销后，下一次轮询 `open-orders` 不再返回 → 子行与虚线同步消失。

> **C3 时序边界（随单 TP/SL）**：第 1 轮 TP/SL 经 `attachAlgoOrds` 随**开仓单**附挂（`order_builder.py:115`）。
> OKX 语义下附挂 algo 单**在父开仓单成交前一般不进入 `orders-algo-pending`**，故对「挂着未成交的限价开仓单」其
> TP/SL 有一段不可见窗口，待父单成交、持仓建立后方稳定出现。故 §六 验收 #2 默认「持仓已开」场景；附挂单可能被
> OKX 归类为 `oco` 或 `conditional`，本方案并发查两类并按 `tpTriggerPx`/`slTriggerPx` 字段展开，**不依赖 ordType 名**，
> 对命名差异稳健。

---

## 三、File / module change plan（文件 / 模块变更计划）

| 文件 | 新建/修改 | 一句话意图 |
|---|---|---|
| `services/quant/app/okx/trade_client.py` | 修改 | 新增 `get_algo_pending_orders(ordType, instId?)` 查询未触发策略委托（TP/SL）。 |
| `services/quant/app/okx/trading.py` | 修改 | 新增 `fetch_live_open_orders` + `_normalize_pending_order` / `_normalize_algo_order` / `_classify_pending_kind`，聚合归一未结委托。 |
| `services/quant/app/api/trading.py` | 修改 | 新增只读路由 `GET /trading/open-orders`（不受 `LIVE_TRADING_ENABLED` 守门）。 |
| `services/quant/app/schemas.py` | 修改 | 新增 `LiveOpenOrder` / `LiveOpenOrdersOut` Pydantic 契约。 |
| `apps/web/lib/tradingTypes.ts` | 修改 | 新增 `LiveOpenOrder` / `LiveOpenOrdersResponse` 前端类型（camelCase 对齐后端）。 |
| `apps/web/lib/tradingApi.ts` | 修改 | 新增 `openOrders(accountId?)` REST 客户端方法。 |
| `apps/web/lib/openOrders.ts` | 新建 | 纯函数：分类 / 分组 / 颜色映射 / 涨跌幅 / 构造图表价格线（无 IO，可测）。 |
| `apps/web/lib/openOrders.test.ts` | 新建 | 覆盖分类、分组、过滤、涨跌幅、价格线构造的正常 + 边界路径。 |
| `apps/web/components/Chart/CandleChart.tsx` | 修改 | 新增 `orderLines` prop：increment reconcile 价格线（虚线 + 轴标签）+ 悬停涨跌幅浮层。 |
| `apps/web/components/Chart/ChartWorkspace.tsx` | 修改 | 实盘模式下查询 `open-orders`，过滤当前 instId 构造 `orderLines` 透传给 `CandleChart`。 |
| `apps/web/components/Live/LivePositionsTable.tsx` | 修改 | 每行持仓下方挂接可折叠委托明细（默认展开）；`invalidate()` 纳入 `openOrders`。 |
| `apps/web/components/Live/PositionOrdersRow.tsx` | 新建 | 单个持仓的 TP/SL/限价平仓委托明细列表展示组件。 |
| `apps/web/components/Live/PositionOrdersRow.test.tsx` | 新建 | 渲染：有委托列表 / 空态 / 涨跌幅着色。 |
| `services/quant/tests/test_trading_open_orders.py` | 新建 | 打桩 OKXTradeClient，覆盖归一 / 分类 / OCO 展开 / 鉴权失败 502。 |
| `docs/plans/live-pending-orders-visualization/spec.md` | 新建 | 本设计文档。 |

> 约束（**C4 修订**）：`CandleChart.tsx` 现 620 行，新增价格线 reconcile effect + hover 命中 + 内联 `OrderLineTag`
> 预计 +~100 行至 ~720 行，逼近 CLAUDE.md 800 行上限且 `onCross` 圈复杂度上行。**落地条件**：把价格线 reconcile +
> hover 命中逻辑抽进自定义 hook `useOrderPriceLines(series, orderLines)`（置于 `apps/web/components/Chart/` 或
> `apps/web/lib/`），`OrderLineTag` 作纯展示子组件（与 `TradeTooltip` 同目录约定）；据此把 `CandleChart.tsx` 控制在
> ≤800 行、单函数 ≤50 行、圈复杂度 ≤10。若落地时实测仍 < 750 行且复杂度达标，可内联实现，但须在 PR 说明实测行数。

---

## 四、Interface design（接口设计）

### 4.1 REST：`GET /api/trading/open-orders`（经 Next rewrite，前端调 `/api/py/trading/open-orders`）

- **Query**：`accountId?: int`（缺省回落默认账户）、`instId?: string`（缺省返回全量；前端不传，客户端过滤）。
  > **C5 落地条件**：`instId` 服务端过滤入参当前前端从不使用（两个消费方都要全量再客户端按 instId 过滤）。
  > 落地时二选一：① 保留该入参并补一条「传 instId 时仅返回该标的委托」的后端 passthrough 单测；
  > ② 直接删除入参，路由签名只留 `accountId`。**禁止**保留未测的死入参。
- **守门**：读类接口，**不**受 `LIVE_TRADING_ENABLED` 限制（与 `/positions`、`/fills` 一致）。
- **鉴权失败 / OKX 不可达**：经全局 `okx_error_handler` 回 `502 {error:{code,message}}`（绝不假数据）。
- **成功响应** `200`：

```json
{
  "orders": [
    {
      "kind": "limit_close",
      "instId": "BTC-USDT-SWAP",
      "posSide": "long",
      "side": "sell",
      "px": 64000.0,
      "triggerPx": null,
      "sz": 3.0,
      "state": "live",
      "reduceOnly": true,
      "ordId": "1234567890",
      "algoId": null,
      "createdTs": 1719500000
    },
    {
      "kind": "tp", "instId": "BTC-USDT-SWAP", "posSide": "long", "side": "sell",
      "px": null, "triggerPx": 70000.0, "sz": 5.0, "state": "live",
      "reduceOnly": false, "ordId": null, "algoId": "987", "createdTs": 1719500001
    }
  ]
}
```

### 4.2 前端类型 / 客户端签名

```ts
// tradingTypes.ts
export type OpenOrderKind = "limit_close" | "tp" | "sl" | "limit_open";
export interface LiveOpenOrder {
  kind: OpenOrderKind;
  instId: string;
  posSide: "long" | "short" | "net";
  side: "buy" | "sell";
  px: number | null;          // 限价委托价（tp/sl 触发后市价时为 null）
  triggerPx: number | null;   // 策略委托触发价（普通挂单为 null）
  sz: number | null;
  state: string;              // live | partially_filled ...
  reduceOnly: boolean;
  ordId: string | null;
  algoId: string | null;
  createdTs: number;          // 秒级 UTC
}
export interface LiveOpenOrdersResponse { orders: LiveOpenOrder[]; }

// tradingApi.ts
openOrders: (accountId?: number) =>
  request<LiveOpenOrdersResponse>(`/trading/open-orders${qs({ accountId })}`),
```

### 4.3 图表价格线视图模型（前端内部）

```ts
export interface OrderPriceLine {
  id: string;                 // `${ordId ?? algoId}:${kind}`（显式定界；reconcile 主键，OCO 的 tp/sl 不撞）
  price: number;              // triggerPx ?? px
  kind: OpenOrderKind;
  color: string;             // 来自 orderKindMeta
  lineStyle: number;         // lightweight-charts LineStyle（虚线/点划线）
  label: string;             // "平仓" | "止盈" | "止损"
  changePct: number | null;  // 相对 ChartWorkspace markPrice 的涨跌幅（持仓子行另用 pos.last，见 C1）
}
```

---

## 五、Data model（数据模型）

- **无新增数据库表 / 迁移**：未结委托的真源在 OKX，前后端均**不落库**（与持仓 / 成交一致，C1）。审计仍只在
  写路径（下单 / 平仓）落 `live_order_audit`，本轮不涉及。
- **后端归一形态 `LiveOpenOrder`**（见 §4.1）：字段全部来自 OKX 原始 `orders-pending` /
  `orders-algo-pending`，经 `_optional_float` / 字符串化容错；`kind` 由 `_classify_pending_kind` 派生；
  OCO 在 `_normalize_algo_order` 内展开为 `tp` + `sl`。
- **持仓 ↔ 委托关联键**：`(instId, posSide)`。对冲模式 `posSide ∈ {long, short}` 精确匹配；net 模式
  `posSide=="net"`。`limit_open` 不参与图表 / 子行展示，但接口诚实返回。
- **前端视图模型**：`OrderPriceLine`（§4.3）用于图表；`PositionOrdersRow` 直接消费分组后的
  `LiveOpenOrder[]`，临时计算每条的 `pctChange(triggerPx ?? px, pos.last)` 用于着色（**C1**：`OkxAccountPosition`
  无 `markPx` 字段，每仓参考价取 `last`，即「标记价」列现值；图表线参考价则取 ChartWorkspace `markPrice`）。

---

## 六、Testing & acceptance criteria（测试与验收）

### 6.1 后端单测 `services/quant/tests/test_trading_open_orders.py`（打桩，不连网，沿用 `test_trading_close.py` 规约）

- `_FakeTradeClient` 注入 `pending` / `oco` / `conditional` 三组返回；断言：
  - 普通 `reduceOnly` 挂单归一为 `kind="limit_close"`；对冲 `posSide=long & side=sell` 亦判 `limit_close`；
    普通买开单判 `limit_open`。
  - 一条含 `tpTriggerPx` + `slTriggerPx` 的 OCO 展开为两条（`tp` + `sl`），`tpOrdPx="-1"` 归一为 `px=None`。
  - `GET /trading/open-orders` 不受 `LIVE_TRADING_ENABLED` 影响（开关关闭仍 200）。
  - OKX 抛 `OKXError` → 502 且 `error.code` 经 `_map_trade_code` 归一；响应体不含任何密钥（`_redact`）。
- **铁律**：一律占位假凭证，严禁写真实开发凭证。

### 6.2 前端单测

- `apps/web/lib/openOrders.test.ts`：`openOrdersKey(accountId)` 对相同 `accountId` 返回深相等键（含
  `undefined`/`null` 归一为同键——锁住 C2 去重契约）；`isPositionOrder` 过滤 `limit_open`；`groupOrdersByPosition`
  正确按 `(instId,posSide)` 分组（含 net）；`pctChange` 在 `refPx<=0`/`null` 时返回 `null`；`buildOrderPriceLines`
  仅取当前 instId、`price` 取 `triggerPx ?? px`、`id`（`${ordId ?? algoId}:${kind}`）稳定且 OCO 的 tp/sl 不撞。
- `apps/web/components/Live/PositionOrdersRow.test.tsx`（`NODE_ENV=test`，见记忆库 vitest 约定）：有委托
  渲染各行 + 涨跌幅着色；空委托渲染空态文案。

### 6.3 验收标准（Definition of Done，针对最终落地，非本设计节点）

1. 实盘模式提交限价平仓后，「当前持仓」对应持仓行下方**默认展开**显示该「限价平仓」委托；点击箭头可折叠 /
   再展开；主图出现对应**虚线**，右价轴常显价格，hover 弹出价格 + 涨跌幅。
2. 随单 TP/SL 同样以**不同颜色虚线**呈现，并在持仓子行列出（止盈 / 止损分行）。
3. 撤单 / 成交后下一轮询（≤5s）虚线与子行同步消失。
4. 切换标的：图表只展示当前 instId 的委托线；切回非实盘模式不发起 `open-orders` 请求。
5. OKX 不可达：图表不画线、持仓表沿用既有错误态，**绝不**画出假线（C1）。
6. **错误隔离（C6）**：`open-orders` 查询单独失败（持仓查询仍正常）时，持仓主行与 K 线**照常渲染**，委托子行 /
   图表委托线静默降级为空，不把持仓表整体打成错误态。
7. **去重（C2）**：实盘下持仓表与图表对 `open-orders` 每轮**只发一次**网络请求（DevTools/Network 校验同键命中）。
8. `pnpm --filter web test` 与 `pytest services/quant/tests/test_trading_open_orders.py` 全绿；
   `apps/web/lib/chartSync.test.ts` 等既有图表测试不回归。

---

## 七、Risks & mitigations（风险与缓解）

| # | 风险 | 缓解 |
|---|---|---|
| R1 | lightweight-charts v4 价格线无 hover 事件，涨跌幅无法原生展示。 | 复用 `subscribeCrosshairMove` + `priceToCoordinate` 做命中判定，绝对定位浮层贴右轴渲染；阈值 6px 可调。 |
| R2 | `orders-algo-pending` 必须带 `ordType`，单次只取一类，可能漏掉某类 TP/SL。 | 并发查询 `oco` + `conditional` 两类合并；如后续发现 `trigger` 类也需展示，扩展为可配置 ordType 列表。 |
| R3 | 全量未结委托无 instId 过滤，账户挂单极多时返回偏大。 | OKX 单次 ≤100，零售账户足够；前端按 instId 客户端过滤；如需可在 §4.1 透传 instId 做服务端过滤。 |
| R4 | 对冲 / net 模式下「是否平仓单」判定易错，错判会污染图表与子行。 | 分类逻辑收敛到纯函数 `_classify_pending_kind` 并单测覆盖三种情形（reduceOnly / 对冲反向 / 普通开仓）。 |
| R5 | 价格线增量 reconcile 未清理已撤销线会残留。 | 以 `id`（ordId/algoId+kind）为主键 diff，删除集合外的线并清空 ref；卸载 effect 统一清理（复用既有模式）。 |
| R6 | 多条委托价位接近导致虚线 / 标签重叠，影响美观。 | hover 仅高亮最近一条；轴标签由库自身错位；同价位极端情形 v2 再做聚合标签。 |
| R7 | 轮询 `open-orders` 增加 OKX 私有接口频率，可能触发限频。 | 与持仓表共用 5s 节奏 + React Query 去重；**去重以共享键工厂 `openOrdersKey(accountId)` + 两侧同源 `accountId` 为前提（C2）**，否则键分裂为两次请求；仅实盘模式 `enabled`。 |
| R8 | 默认全展开在持仓极多时纵向占用过高。 | 子行紧凑（每条一行 grid）；默认展开符合需求；如需可在 v2 增「全部折叠」表头开关。 |
| R9 | 附挂 TP/SL 在父开仓单成交前不进入 `orders-algo-pending`，限价开仓挂单的 TP/SL 有不可见窗口（C3）。 | 验收 #2 限定「持仓已开」场景；并发查 `oco`+`conditional` 且按 `tpTriggerPx`/`slTriggerPx` 字段展开，不依赖 ordType 名；待父单成交后稳定出现，符合「未结委托」语义。 |
| R10 | `open-orders` 查询独立失败会污染持仓表 / 图表（C6）。 | 子行与图表线对该查询失败做局部降级（空态 / 不画线）；持仓表 `isError` 只看 `positions` 查询，K 线只看自身数据源。 |

---

## 八、与既有架构的一致性（自查）

- 复用 `--long/--short/--accent/--muted` 主题变量与 `ui/` 原子组件（Badge/Button），不引入新依赖。
- 只读接口不守门、写路径零改动；密钥永不出接口（`_redact`），OKX 不可达显式 502（C1/C7）。
- 纯展示 / 计算逻辑下沉到 `apps/web/lib/openOrders.ts` 并配单测；IO 收敛在 OKX 客户端与 API 路由。
- 图表改动遵守索引「Key Conventions」：触碰图表面板时保持 `chartSync.ts` 行为不变（本轮只加价格线，不动
  range/crosshair 同步与价轴等宽逻辑），既有 `chartSync.test.ts` 不回归。

---

## 评审结论（Review Verdict · Subtask #1）

**结论：有条件通过（Approved with conditions）**。

方案的核心判断已逐条对照真实代码核验：①后端 `OKXTradeClient.get_pending_orders` 已存在、`OKXPrivateError`
子类化 `OKXError` 经全局 `okx_error_handler` 统一回 502、只读接口不守门与 `/positions`·`/fills` 一致，新接口可
照 `fetch_live_fills` 范式落地；②前端 `createPriceLine`/`subscribeCrosshairMove`/`priceToCoordinate`/`candleSeriesRef`
均为现役 API，lightweight-charts `^4.2.0`，虚线 + 轴标签 + hover 浮层路线可行；③`request`/`qs`、React Query 同键去重、
`--long/--short/--accent` 主题变量、`PositionRow` 七列表结构均与现状吻合。**可行性 / 一致性 / 规模适配整体达标，无
P0。** 两个 **P1（C1 `markPx` 字段不存在 → 改 `pos.last`；C2 去重键自相矛盾 → 共享 `openOrdersKey` 工厂）已在
v2 正文直接修订完毕**，文档不再遗留 P0/P1。

放行条件（落地 PR 必须满足，对应 P2）：

1. **C3**：UI / 文案需体现「随单 TP/SL 在父开仓单成交后方稳定显示」的时序边界；验收 #2 以「持仓已开」为前提。
2. **C4**：`CandleChart.tsx` 落地后实测 ≤800 行、单函数 ≤50 行、圈复杂度 ≤10；逼近阈值则按 §3 抽
   `useOrderPriceLines` hook，PR 注明实测行数。
3. **C5**：`GET /open-orders` 的 `instId` 入参——保留则补后端 passthrough 单测，否则删除；不得保留未测死入参。
4. **C6**：`open-orders` 查询独立失败时，持仓主行 / K 线照常渲染，委托子行 / 委托线静默降级（验收 #6 覆盖）。
5. 落地需满足 §六 全部验收，含 `pnpm --filter web test`、`pytest .../test_trading_open_orders.py` 全绿且
   `chartSync.test.ts` 不回归；后端测试一律占位假凭证。

满足以上条件即视为完成评审闭环；P2 均为实现期可验证项，不阻塞进入开发节点。

---

## 实施过程发现的方案缺陷（Issues Found During Implementation · Subtask #2）

落地时对方案做了三处必要的细化（均为实现细节修正，不改变架构 / 数据流 / 验收口径），记录如下：

1. **`OrderPriceLine.color` → `colorVar`（CSS 变量不能进 canvas）**：§4.3 让 `OrderPriceLine.color`
   直接喂给 `series.createPriceLine({color})`，但 `orderKindMeta` 给的是 `var(--accent)` 这类 CSS 变量，
   lightweight-charts 在 canvas 上**不解析 CSS 变量**。改为 `orderKindMeta` 返回 `colorVar`（变量名，如
   `--accent`）：图表层（`useOrderPriceLines`）用 `getComputedStyle` resolve 为真实色，组件层（`OrderLineTag`
   / `PositionOrdersRow`）用 `var(${colorVar})` 引用。`OrderPriceLine` 字段相应改为 `colorVar`。

2. **C4 的 `useOrderPriceLines` 签名扩展为 `(chartRef, seriesRef, orderLines, theme)`**：§3 建议
   `useOrderPriceLines(series, orderLines)`。实际 hover 命中需订阅 `chart.subscribeCrosshairMove`（要 `chartRef`），
   reconcile 需在主题切换时重着色（要 `theme`），故签名扩展。命中浮层状态（`OrderLineHover`）由 hook 自身
   `useState` 持有并返回，`CandleChart` 仅渲染 `<OrderLineTag>`，CandleChart 实测 **631 行**（远低于 800 上限）。

3. **悬停命中改为 hook 内独立订阅，而非扩展 `CandleChart.onCross`**：§2.3(c) 设想在既有 `onCross` 里追加命中
   逻辑。为隔离改动、避免抬高 `onCross` 圈复杂度（C4 精神），`useOrderPriceLines` 自持一个 `subscribeCrosshairMove`
   订阅（lightweight-charts 支持多订阅者，与既有回调互不干扰），挂载于创建图表 effect 之后以保证 ref 就绪。

> 以上均不影响 §六 验收：去重（C2，两侧同源 `openOrdersKey()` → `["trading","openOrders",null]`）、错误隔离
> （C6，`openOrdersQ` 失败 → 空数组降级）、instId passthrough（C5，已补后端单测）均按方案落地并测试通过。
