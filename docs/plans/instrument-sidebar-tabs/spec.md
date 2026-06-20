# 多页签标的侧边栏（自选 / 现货 / 合约）开发方案

- **Feature**: `instrument-sidebar-tabs`
- **Version**: v2（已评审 / 已修复 P0·P1）
- **Author**: 方案设计节点（Subtask #0）
- **Reviewer**: 方案评审节点（Subtask #1）
- **Date**: 2026-06-20
- **Status**: Reviewed — 有条件通过（见文末「评审结论」）
- **关联文档**: `.claude-index/index.md`、`README.md`、`docs/plans/aragon-trading-platform/spec.md`

---

## 0. 评审记录（Review Notes）

> 评审节点（Subtask #1）逐节对照「可行性 / 完整性 / 一致性 / 适度性」四维，并将每条结论与项目实际代码（`apps/web/lib/store.ts`、`components/Chart/InstrumentList.tsx`、`services/quant/app/okx/*`、`api/market.py`、`main.py`）核对后产出。**所有 P0 / P1 已在本 v2 文档正文就地修复**；P2 为建议项（部分已顺手修订，余者留作下游可选优化）。本方案无 P0 级问题。

| # | 维度 | 严重度 | 问题 | 处置（已在 v2 正文修复 / 建议） |
|---|---|---|---|---|
| RV-1 | 完整性 | **P1** | §7 仅强调 `migrate` 注入 `watchlist`/`instSidebarTab`。但**全新安装**（无 `aragon-ui` localStorage）只走 store 初始化器、**不触发 migrate**，会导致首启自选为空、`instSidebarTab` 为 `undefined`。 | **已修**：在 store **初始化器**显式给出 `watchlist=[...FALLBACK_INSTRUMENTS]`、`instSidebarTab="watch"` 默认值；migrate 仅为旧版（v5→v6）补齐。见 §6.4 / §7。 |
| RV-2 | 一致性 | **P1** | 自选数据流自相矛盾：§4.1/§4.2 描述「三类型 Map 归并」，而 §6.3 `resolveWatchlistRows(watchlist, byInstId: Map)` 只收单一 Map，下游易实现错。 | **已修**：明确「调用方先把相关类型的 query 结果归并为单一 `Map<instId, Instrument>` 再传入」，签名维持单 Map；§4.1 数据流改写一致。 |
| RV-3 | 完整性 / 核心需求 | **P1** | 合约页签「永续/交割」子筛选若为**硬二选一且无「全部」**，与 G3/R1「合约覆盖 SWAP+FUTURES 全量、任意合约可被搜到」矛盾——用户须预先知道目标是永续还是交割才能搜到。 | **已修**：合约页签**同时拉取并归并 SWAP+FUTURES 两条 query**；子筛选改为「全部 / 永续 / 交割」且**默认「全部」**，搜索默认横跨两类。见 §4.1 / §4.5 / §6.3（新增 `instTypes` 选项）。 |
| RV-4 | 适度性 / 一致性 | **P1** | §5 对 `buildInstOptions`「删除或保留兼容」、`InstrumentList`「降级或并入后删除」留有歧义；新模型把行情字段并入 `Instrument` 后，旧 `Ticker` 类型与 `InstrumentList.tickers` prop 沦为孤儿，违反 CLAUDE.md §8「不制造新孤岛」。 | **已修**：经核查 `buildInstOptions` 唯一调用方是 `InstrumentList`、`Ticker` 唯一引用方也是它。决定为：**删除 `buildInstOptions`、删除 `InstrumentList.tsx`、移除随之失效的 `Ticker` 接口**。见 §5。 |
| RV-5 | 性能 | P2 | 自选页签首屏并发 3 条 tickers（即便自选全为单一类型），首屏取数偏重。 | **已采纳**：自选页签按自选项经 `inferInstType` 推断的**实际涉及类型子集**取数（最多 3 条，与现货/合约页签共享 `queryKey` 缓存）。见 §4.1。 |
| RV-6 | 一致性 | P2 | `filterAndRankInstruments` 签名 §4.1 写作 `(list, query, opts)`、§6.3 为 `(list, opts)`。 | **已统一**为 `(list, opts)`。 |
| RV-7 | 可行性 / 明确性 | P2 | §6.1 的 `400` 未点明如何产出 `{error:{code,message}}` 响应体。 | **已注明**：沿用既有约定 `raise HTTPException(400, detail={"code":"BAD_INST_TYPE","message":...})`（参 `api/strategies.py`），由 `main.py` 全局处理器包成 `{error:{...}}`。见 §6.1。 |
| RV-8 | 准确性 | P2 | §10 步骤 2「补 `_optional_float` 复用」措辞不准——该助手已存在于 `okx/types.py`。 | **已改**为「复用 `okx.types._optional_float`，无需新增」。见 §10。 |
| RV-9 | 准确性 | P2 | `volCcy24h` 在衍生品与现货口径不同，跨类型不可直接比较。 | **已注明**：仅作**同类型内**成交额排序启发式（主流币仍靠前），非跨类型可比。见 §4.3。 |
| RV-10 | 一致性 | P2 | 第三页签键名 `instSidebarTab:"swap"` 实际承载「合约（SWAP+FUTURES）」，键名易误解。 | 保留键名（避免无谓迁移），在 §6.4 注明其语义即「合约页签」；可选后续重命名为 `"contract"`（留作下游决定）。 |

---

## 1. 概述（Overview）

Aragon Trading 是一套「左图右策略」的量化模拟交易工作台：前端 Next.js 14 + React 18 + Tailwind + lightweight-charts，后端 FastAPI 对接 OKX 公共 REST/WS 真实行情。当前界面最左侧是一条竖向「标的列表」（`apps/web/components/Chart/InstrumentList.tsx`），用户点击其中一行即切换图表标的（写入 `store.instId`）。

现状的核心缺陷：这条列表**只能查到 A 开头的标的以及 BTC/ETH/SOL 等少数几个**，无法浏览/检索 OKX 上完整的、多类型（现货 SPOT、永续/交割合约 SWAP/FUTURES）的标的。根因明确（见 §3）：前端 `buildInstOptions()` 把后端按字母升序返回的标的列表 `.filter(quote==="USDT").slice(0, 40)`，于是在 3 个回退标的（BTC/ETH/SOL-USDT-SWAP）之外，只截到字母表最前面的一批（AAVE、ACE、ACH、ADA……即「A 开头」），其余标的根本不进入候选；同时列表既不可搜索，也无法按类型切换、无法管理自选。

本方案把这条单一列表**升级为三页签的标的侧边栏**：①「自选」面板（用户收藏，可增删、跨类型）、②「现货」面板（SPOT）、③「合约」面板（SWAP 永续 / FUTURES 交割，含 USDT/USDC/USD 计价）。每个页签内置**实时搜索框**，按子串模糊匹配 instId / 币种，能正确检索并展示对应类型的全部标的（合约页签确保覆盖欧易平台上各类合约，含用户提到的特定合约品种）；每一行展示标的名、最新价、24h 涨跌幅，并带一个**收藏星标**用于一键加入/移出自选。后端 `/instruments` 接口扩展为按类型返回完整标的并携带行情字段（最新价 / 24h 涨跌 / 成交额）、按成交额降序排序（让主流币天然靠前，彻底取代「字母序 + 截断」），从根上修复检索不全的问题。

设计目标：美观、优雅、符合人机交互最佳实践——复用既有设计 token 与 `Tabs` 原子组件保证视觉一致；搜索、空态、加载骨架、错误态、键盘可达性一应俱全；严守项目「绝不伪造行情数据」的硬约束（OKX 不可达时显式报错，不静默兜底）。

---

## 2. 目标与非目标（Goals / Non-Goals）

**目标**
- G1 左侧标的栏改为「自选 / 现货 / 合约」三页签。
- G2 三页签都能**正确检索并展示对应类型**的标的（搜索覆盖该类型完整宇宙，而非前 40 个）。
- G3 合约页签覆盖 OKX 全部合约（SWAP 永续 + FUTURES 交割；USDT/USDC/USD 计价），支持子类型与计价筛选，确保任意目标合约可被搜到。
- G4 用户可在任意页签对标的执行「加入自选 / 移出自选」，自选持久化到 `localStorage`。
- G5 每行展示最新价 + 24h 涨跌幅；当前选中标的高亮，并能反映实时价。
- G6 视觉与交互达到「顶级设计」水准并满足无障碍（键盘、ARIA、可见焦点）。

**非目标**
- N1 不做真实下单 / 真实资金（仍是 Phase 1 模拟）。
- N2 不引入新的前端运行时依赖（不加图标库 / 虚拟列表库；用内联 SVG + 渲染上限策略）。
- N3 不改造 K 线 / 策略面板 / 撮合引擎等无关模块。
- N4 不接入 OKX 私有接口（仍只用公共行情，无需密钥）。

---

## 3. 现状与根因分析（Root Cause）

调用链：`ChartWorkspace` → `InstrumentList`（`useQuery(["instruments"]) → api.instruments("SWAP")`）→ `buildInstOptions(instruments)`。

```ts
// apps/web/lib/instruments.ts（现状）
export const FALLBACK_INSTRUMENTS = ["BTC-USDT-SWAP", "ETH-USDT-SWAP", "SOL-USDT-SWAP"];
export function buildInstOptions(instruments) {
  const set = new Set(FALLBACK_INSTRUMENTS);
  instruments?.filter(i => i.quote === "USDT").slice(0, 40).forEach(i => set.add(i.instId));
  return Array.from(set);
}
```

后端 `OKXRestClient.get_instruments()` 用 `/api/v5/market/tickers?instType=SWAP` 取全量，但**按 `(quote != "USDT", instId)` 字母升序排序**且只返回 `instId/instType/base/quote` 四个字段（丢弃价格与成交量）。前端再 `.filter(USDT).slice(0,40)`，于是：

1. **截断 + 字母序** ⇒ 只保留 A 开头的 40 个（AAVE/ACE/ACH/ADA…），主流币（除回退的 BTC/ETH/SOL）被挤出 ⇒ 「只查得到 A 开头」。
2. **仅 USDT** ⇒ USDC/USD 计价合约不可见。
3. **只取 SWAP** ⇒ 现货 SPOT 完全不可选。
4. **无搜索 / 无分类 / 无自选** ⇒ 用户无法主动找到任意标的。

修复方向：后端按**成交额降序**返回**完整列表**并带行情字段；前端改为**按类型分页签 + 客户端模糊搜索 + 渲染上限**，不再字母截断。

---

## 4. 技术设计（Technical Design）

### 4.1 架构与数据流

```
InstrumentSidebar (新, 智能容器)
  ├─ Tabs(自选/现货/合约)  ← store.instSidebarTab ("watch"|"spot"|"swap"，持久化)
  ├─ 搜索框 query (本地 state)
  ├─ 合约子筛选: 子类型(全部/永续 SWAP/交割 FUTURES，默认「全部」) + 计价(全部/USDT/USDC/USD)  (本地 state)
  ├─ 各页签取数 (queryKey=["instruments", instType], staleTime 5min, React Query 去重缓存):
  │      · 现货页签 → useQuery(SPOT)
  │      · 合约页签 → useQuery(SWAP) + useQuery(FUTURES) 两条并发, 客户端归并为一份候选          (RV-3)
  │                   子类型筛选在归并集合上做; 默认「全部」=SWAP∪FUTURES ⇒ 单次搜索横跨永续+交割 (G3/R1)
  │      → GET /api/py/instruments?instType=...  → FastAPI market.get_instruments
  │      → OKXRestClient.get_instruments → OKX GET /api/v5/market/tickers?instType=...
  │      → 解析 last/open24h/vol24h/volCcy24h + 计算 changePct, 按 volCcy24h 降序
  ├─ 纯函数 filterAndRankInstruments(list, opts)  (lib/instruments.ts, 可测)               (RV-6)
  └─ InstrumentRow[]  (选中高亮 + 实时价 + 涨跌 + 收藏星标)
         onSelect → store.setInst(instId)
         onToggleStar → store.toggleWatchlist(instId)
自选页签:
  并发 useQuery(["instruments", t]) for t ∈ {自选项经 inferInstType 推断的实际类型集}        (RV-5)
  (最多 SPOT/SWAP/FUTURES 三条; 与现货/合约页签共享同一 queryKey 缓存, 不重复打 OKX)
  → 归并成单一 Map<instId, Instrument>                                                     (RV-2)
  → resolveWatchlistRows(store.watchlist, byInstId) 按收藏顺序解析 (缺失行情则降级仅显示符号)
```

要点：
- 三个类型列表各自 `useQuery`（`queryKey: ["instruments", instType]`，`staleTime: 5min`），React Query 自动去重缓存；自选页签复用同样的 key 读缓存。
- 当前选中行的「实时价」继续由既有 WS 市场流（`market.lastPrice`，仅当前标的）覆盖；其余行用 `/instruments` 返回的 24h 快照价，不新增逐标的轮询（性能与限频友好）。
- 严守硬约束：`/instruments` 在 OKX 不可达时由后端抛 `OKXError` → main 统一转 5xx；前端展示错误态，**不伪造**任何标的/价格。

### 4.2 关键代码路径

1. **后端取数与归一**：`OKXRestClient.get_instruments(inst_type)` 解析 tickers 行 → `Instrument(instId, instType, base, quote, last, open24h, vol24h, volCcy24h, changePct)`；`changePct = (last-open24h)/open24h`（`open24h` 为 0/缺失则 `None`）；按 `volCcy24h` 降序（缺失视为 0）。
2. **接口边界校验**：`market.get_instruments` 对 `instType` 做白名单校验（`SPOT|SWAP|FUTURES`），非法值 → 400 显式错误。
3. **前端搜索/排序纯函数**：`filterAndRankInstruments(list, { query, quote })`：大小写不敏感子串匹配 `instId` 或 `base`；按 `quote` 过滤（`"ALL"` 不过滤）；保持后端的成交额降序；返回前对**渲染上限**截断（见 §4.4 性能）。
4. **自选解析**：`resolveWatchlistRows(watchlist, mapsByType)`：按 `inferInstType(instId)` 命中对应类型 Map 取行情，缺失则构造 `{instId, base, quote, last:null,...}` 占位，保证收藏项始终可见、可移除。
5. **类型推断**：`inferInstType(instId)`：`endsWith("-SWAP")→"SWAP"`；`/-\d{6,8}$/.test→"FUTURES"`；否则 `"SPOT"`。

### 4.3 OKX 接口选型与字段映射

沿用现有 `GET /api/v5/market/tickers?instType={SPOT|SWAP|FUTURES}`（公共、单次返回该类型全量，且**自带行情**，无需 N 次取价）。字段映射（OKX → 本系统）：

| OKX 字段 | 本系统字段 | 备注 |
|---|---|---|
| `instId` | `instId` | 如 `BTC-USDT`、`BTC-USDT-SWAP`、`BTC-USDT-250328` |
| `instType` | `instType` | 由请求参数回填（tickers 行也含） |
| `instId.split("-")[0]` | `base` | 基础币 |
| `instId.split("-")[1]` | `quote` | 计价/结算币（USDT/USDC/USD） |
| `last` | `last` | 最新成交价 |
| `open24h` | `open24h` | 24h 前价（算涨跌） |
| `vol24h` | `vol24h` | 24h 成交量（基础币） |
| `volCcy24h` | `volCcy24h` | 24h 成交额（计价币）→ 排序键 |
| —（计算） | `changePct` | `(last-open24h)/open24h` |

> 注：合约/期货 instId 仍按 `-` 切分可正确取到 base/quote（交割合约第三段为到期日，不影响前两段）。如后续需展示「合约面值/到期日/张/币」等元信息，可再叠加 `GET /api/v5/public/instruments`（含 `ctVal/ctType/settleCcy/expTime`），本期非必需，列为后续增强。

> 注（RV-9）：`volCcy24h` 在衍生品与现货的计量口径不同（SPOT 为计价币、SWAP/FUTURES 因合约面值/张币方向而异），故它**仅用作同一 `instType` 列表内的成交额排序启发式**（保证主流币靠前），**不跨类型直接比较**。本方案每次请求都是单一 `instType`、排序也只在该类型内进行，口径差异不影响正确性。

### 4.4 性能与限频

- 每个类型列表一次 REST 调用，`staleTime 5min` + React Query 去重；切页签不重复打 OKX。
- SPOT/SWAP 可达数百上千行：**不引入虚拟列表依赖**（N2），改用「过滤后渲染上限」策略——默认渲染前 `MAX_VISIBLE_ROWS = 200` 行（已按成交额降序，主流币必在内），底部提示「仅显示前 200 条，输入关键字以精确查找」；用户一旦搜索，匹配集通常远小于上限。该策略 O(n) 过滤 + 截断，常规规模下流畅。
- 行情快照随列表一次取回，列表行**不**逐个轮询价格；当前选中行的跳动价来自既有 WS。

### 4.5 UI / UX 设计

布局（`ChartWorkspace` 左列由「单 scroll 容器」改为 flex-col）：
```
左列 (lg:basis-[16%] min-w-[220px] max-w-[300px])
 ├─ MarketDepthPanel        (shrink-0, 现状保留)
 └─ InstrumentSidebar       (flex-1 min-h-0, 内部独立滚动)
       ├─ Tabs[自选|现货|合约]      (sticky 顶部)
       ├─ 搜索输入框 + 清除按钮       (sticky)
       ├─ (合约) 子段: 子类型筹码(全部/永续/交割, 默认「全部」) · 计价筹码(全部/USDT/USDC/USD)
       ├─ 列表区 (overflow-auto):
       │     InstrumentRow: [★] SYMBOL        last
       │                        base/quote     +1.23%
       └─ 空态/加载骨架/错误态
```

交互与视觉细则：
- 复用 `Tabs`（segmented 变体）做三页签；复用设计 token：`--surface/--surface-2/--border/--text/--muted/--accent/--accent-soft/--long/--short`、`shadow-soft`、`ease-smooth`。
- 行：左侧星标（实心=已收藏 `--accent`/空心=未收藏 `--muted`，`hover` 高亮）；中部符号（主行）+ `base-quote` 次行（`--muted`）；右侧最新价（`tabular`）+ 涨跌幅（`colorForReturn`）。选中行：左 2px `--accent` 边 + `--surface-2` 底（与现状一致）。
- 搜索框：`输入即过滤`（无防抖必要，纯前端），带放大镜图标与清除（×）；占位「搜索币种 / 合约，如 BTC、SOL-USDT-SWAP」。
- 合约子类型筹码（RV-3）：`全部 / 永续 / 交割`，**默认「全部」= SWAP∪FUTURES**，确保用户无需预知品种即可一次搜遍所有合约（满足 G3/R1）；选「永续」「交割」时仅以 `inferInstType` 客户端收敛，不额外打 OKX。
- 自选空态：图标 + 「还没有自选标的，在现货/合约页签点 ★ 收藏」。
- 加载：3–6 行 `Skeleton`；错误：红字「无法连接 OKX，请检查网络/代理」+ 重试按钮（`refetch`）。
- 无障碍：星标 `button[aria-pressed]` + `aria-label`「收藏/取消收藏 X」；行 `button[aria-current]`；搜索 `aria-label`；Tab 可达、焦点环可见；星标点击 `stopPropagation` 不触发选中。
- 收藏/取消提供轻微过渡（`transition-colors`），不打断滚动。

---

## 5. 文件 / 模块变更计划（File Change Plan）

| 文件 | 动作 | 一句话意图 |
|---|---|---|
| `services/quant/app/okx/types.py` | 改 | `Instrument` 增可选行情字段 `last/open24h/vol24h/volCcy24h/changePct`（向后兼容，默认 `None`）。 |
| `services/quant/app/okx/rest_client.py` | 改 | `get_instruments` 解析 tickers 行的价格/量、计算 `changePct`、改为按 `volCcy24h` 降序。 |
| `services/quant/app/api/market.py` | 改 | `/instruments` 对 `instType` 做白名单校验（SPOT/SWAP/FUTURES），非法→400。 |
| `apps/web/lib/types.ts` | 改 | `Instrument` 接口补可选行情字段；`instType` 增 `"FUTURES"`；**移除孤儿 `Ticker` 接口**（经核查唯一引用方 `InstrumentList` 同批删除，RV-4）。 |
| `apps/web/lib/store.ts` | 改 | 新增 `watchlist:string[]`、`instSidebarTab`；增 `toggleWatchlist/addToWatchlist/removeFromWatchlist/setInstSidebarTab`；持久化并 `migrate` 到 v6（含默认自选种子）。 |
| `apps/web/lib/instruments.ts` | 改 | 增 `filterAndRankInstruments`、`inferInstType`、`resolveWatchlistRows`、`MAX_VISIBLE_ROWS`、`InstType`；保留 `FALLBACK_INSTRUMENTS` 作自选种子；**删除 `buildInstOptions`**（唯一调用方 `InstrumentList` 同批移除，不留死代码——CLAUDE.md §8「不制造新孤岛」，RV-4）。 |
| `apps/web/components/Chart/InstrumentSidebar.tsx` | 新增 | 三页签智能容器：页签/搜索/合约子筛选/取数/渲染列表/空·载·错态。 |
| `apps/web/components/Chart/InstrumentRow.tsx` | 新增 | 单行展示组件（符号/价/涨跌/星标），三页签复用。 |
| `apps/web/components/Chart/InstrumentList.tsx` | 删除 | 由 `InstrumentSidebar` + `InstrumentRow` 取代（RV-4）；其 `tickers?: Record<string, Ticker>` prop 随之消失，`buildInstOptions` 导入一并清除。 |
| `apps/web/components/Chart/ChartWorkspace.tsx` | 改 | 左列改 flex-col：`MarketDepthPanel`(shrink-0) + `InstrumentSidebar`(flex-1)；适度加宽左列。 |
| `apps/web/lib/instruments.test.ts` | 新增 | 覆盖 `filterAndRankInstruments`/`inferInstType`/`resolveWatchlistRows`。 |
| `apps/web/lib/store.test.ts` | 改 | 增自选增删/去重/持久化、`instSidebarTab` 持久化、v5→v6 迁移用例。 |
| `services/quant/tests/test_okx_normalize.py` | 改 | 增 `get_instruments` 解析价格/算涨跌/按额降序/instType 校验用例（用桩 client，不打真网）。 |

---

## 6. 接口设计（Interfaces）

### 6.1 REST：`GET /api/py/instruments`

- **入参**：`instType: string`（`SPOT | SWAP | FUTURES`，默认 `SWAP`；非法→`400 {error:{code:"BAD_INST_TYPE",message}}`）。
  - 实现约定（RV-7）：在 `market.get_instruments` 内 `raise HTTPException(400, detail={"code":"BAD_INST_TYPE","message":...})`，沿用 `api/strategies.py` 既有写法；`main.py` 的全局 `HTTPException` 处理器会把 `dict` detail 自动包成 `{error:{code,message}}`，前端 `ApiRequestError` 可正常解析。
- **出参**：`Instrument[]`，按 24h 成交额（`volCcy24h`）降序。
- **错误**：OKX 不可达→沿用 main 的 5xx 显式错误态（硬约束，不回退假数据）。

`Instrument`（扩展后，向后兼容）：
```jsonc
{
  "instId": "BTC-USDT-SWAP",
  "instType": "SWAP",
  "base": "BTC",
  "quote": "USDT",
  "last": 64210.5,        // 可空
  "open24h": 63010.0,     // 可空
  "vol24h": 123456.0,     // 可空, 基础币
  "volCcy24h": 7.9e9,     // 可空, 计价币(排序键)
  "changePct": 0.019      // 可空, (last-open24h)/open24h
}
```

### 6.2 前端 API 客户端（`lib/api.ts`）

`api.instruments(instType="SWAP")` 签名不变，仅返回类型随 `Instrument` 扩展自动增强；如需类型收紧可定义 `InstType = "SPOT"|"SWAP"|"FUTURES"`。

### 6.3 前端纯函数（`lib/instruments.ts`）

```ts
export type InstType = "SPOT" | "SWAP" | "FUTURES";
export const MAX_VISIBLE_ROWS = 200;
export function inferInstType(instId: string): InstType;
export interface InstFilterOpts {
  query: string;
  quote?: "ALL" | "USDT" | "USDC" | "USD";
  instTypes?: InstType[]; // RV-3 合约页签子类型筛选: 省略/空=不限(=「全部」); ["SWAP"]=永续; ["FUTURES"]=交割
  limit?: number;
}
export function filterAndRankInstruments(list: Instrument[] | undefined, opts: InstFilterOpts): { rows: Instrument[]; total: number; truncated: boolean };
// RV-2: byInstId 由调用方归并而成的单一映射——现货页签=SPOT 一份; 合约页签=SWAP∪FUTURES; 自选页签=涉及类型并集。
export function resolveWatchlistRows(watchlist: string[], byInstId: Map<string, Instrument>): Instrument[];
```

### 6.4 状态（`lib/store.ts`）

```ts
watchlist: string[];                       // 收藏的 instId(有序, 去重)
instSidebarTab: "watch" | "spot" | "swap"; // 当前页签; "swap" 即「合约页签」(承载 SWAP+FUTURES, RV-10)
addToWatchlist(id): void; removeFromWatchlist(id): void; toggleWatchlist(id): void;
setInstSidebarTab(tab): void;
// 初始化器默认 (RV-1, 全新安装兜底, 见 §7): watchlist = [...FALLBACK_INSTRUMENTS], instSidebarTab = "watch"
```

---

## 7. 数据模型（Data Model）

- **后端 `Instrument`（Pydantic）**：在原 4 字段基础上追加 5 个 `Optional[float]` 字段，默认 `None`；不破坏既有取用（`market.get_instruments` 仍 `model_dump()`）。
- **前端 `Instrument`（TS interface）**：对应追加可选字段；`instType: "SPOT"|"SWAP"|"FUTURES"`。
- **持久化（`aragon-ui` localStorage）**：
  - **初始化器默认（RV-1，关键）**：在 `create(persist((set)=>({...})))` 的初始 state 中显式给出 `watchlist = [...FALLBACK_INSTRUMENTS]`、`instSidebarTab = "watch"`。**全新安装无 localStorage 时只走初始化器、不触发 `migrate`**，必须在此兜底，否则首启自选为空、`instSidebarTab` 为 `undefined`。
  - `partialize` 增 `watchlist`、`instSidebarTab`；`version: 6`。
  - `migrate`：`version<6` 的旧持久化补 `watchlist = [...FALLBACK_INSTRUMENTS]`、`instSidebarTab = "watch"`（与现有 v2–v5 迁移风格一致；zustand 默认浅合并亦会回落初值，migrate 仅为显式稳妥）。
  - **不**持久化运行期句柄（沿用现约）。
- **自选语义**：`watchlist` 存「跨类型」instId 数组；展示顺序 = 收藏顺序（新收藏置尾或置首，二选一，建议**置首**便于即时可见）；去重由 store 动作保证。

---

## 8. 测试与验收标准（Testing & Acceptance）

### 8.1 单元测试（先红后绿，覆盖正常 + 异常路径）

- **前端 `lib/instruments.test.ts`**
  - `filterAndRankInstruments`：空 query 返回按额降序的全集（截断到 `limit`，`truncated/total` 正确）；query 大小写不敏感匹配 `instId` 与 `base`；`quote` 过滤；`instTypes` 过滤（`undefined`/`[]` 不限、`["SWAP"]` 仅永续、`["FUTURES"]` 仅交割——RV-3）；空 list 返回空且不抛。
  - `inferInstType`：`BTC-USDT→SPOT`、`BTC-USDT-SWAP→SWAP`、`BTC-USDT-250328→FUTURES`。
  - `resolveWatchlistRows`：命中 Map 取行情；缺失项降级占位且可见；顺序 = watchlist 顺序。
- **前端 `lib/store.test.ts`**
  - `toggleWatchlist` 增/删幂等、去重；`add/remove` 边界；`watchlist`/`instSidebarTab` 落 `localStorage`；v5→v6 迁移补默认自选；运行期句柄仍不持久化。
- **后端 `tests/test_okx_normalize.py`**（桩 client，不联网）
  - `get_instruments` 解析 `last/open24h/vol*`、`changePct` 计算（`open24h=0/缺失→None`）、按 `volCcy24h` 降序；`/instruments` 非法 `instType` → 400。

### 8.2 验收标准（手动 / 端到端）

- A1 左侧出现「自选 / 现货 / 合约」三页签，切换流畅、视觉与全站一致。
- A2 现货页签可搜到任意 SPOT（如搜 `PEPE`、`DOGE`、`OP` 均命中），不再局限 A 开头。
- A3 合约页签可搜到 OKX 各类合约（永续/交割、USDT/USDC/USD），用户目标合约可被检索到；子筛选生效。
- A4 在现货/合约页签点 ★ 可加入自选，再点取消；自选页签即时反映；刷新后保持（持久化）。
- A5 选中某标的后，图表正确切换并加载真实 K 线；选中行实时价随行情跳动。
- A6 OKX 不可达时列表显示明确错误态与重试，**绝无伪造数据**。
- A7 `pnpm test`（前端 vitest）与 `pytest -q`（后端，不含 network）全绿；`pnpm build`/`tsc` 通过。

---

## 9. 风险与缓解（Risks & Mitigations）

| # | 风险 | 影响 | 缓解 |
|---|---|---|---|
| R1 | 「TRA DFI 类型合约」需求表述含糊（疑为口述转写） | 可能漏覆盖目标合约类型 | 合约页签覆盖 SWAP+FUTURES 全量并支持搜索，**任意**合约可被搜到；实现/评审阶段如能明确具体品种再加针对性默认筛选。 |
| R2 | SPOT/SWAP 列表行数多导致渲染卡顿 | 滚动掉帧 | 成交额降序 + `MAX_VISIBLE_ROWS=200` 截断 + 搜索收敛；不引入虚拟列表依赖（N2）。如后续仍需，再评估 `@tanstack/react-virtual`。 |
| R3 | OKX tickers 限频 / 偶发不可达 | 取数失败 | 沿用 `OKXRestClient` 节流+重试；`staleTime 5min` 去重；失败显式错误态，不静默兜底（C1）。 |
| R4 | 自选含已下市/拼写错误 instId | 自选行无行情或选中报错 | `resolveWatchlistRows` 占位可见且可一键移除；选中失败由图表区既有错误态兜底。 |
| R5 | 跨类型自选无法定位行情 | 自选价缺失 | 自选页签并发拉取三类型并按 `inferInstType` 归并；仍缺则降级显示符号。 |
| R6 | 持久化迁移破坏旧 `aragon-ui` | 首启状态异常 | `migrate` 逐版补默认（v6 补 `watchlist`/`instSidebarTab`），并加迁移单测。 |
| R7 | 选中 SPOT 后下游（WS/回测/模拟）兼容性 | 流程异常 | OKX candles/WS 对 SPOT/SWAP 同构，`setInst` 已重置 `runId` 并切 live-follow；回归冒烟覆盖。 |
| R8 | 文件膨胀超阈（CLAUDE.md ≤800 行/方法 ≤50 行） | 触发规约 | 拆分 `InstrumentSidebar`（容器）/`InstrumentRow`（行）/`instruments.ts`（纯逻辑），逻辑下沉纯函数。 |

---

## 10. 实施步骤（供下游逐行落地）

1. **后端模型**：`okx/types.py` 给 `Instrument` 加 5 个 `Optional[float] = None` 字段。
2. **后端取数**：`rest_client.get_instruments` 改为解析行情、算 `changePct`、按 `volCcy24h` 降序；空值用 `okx.types._optional_float` 处理（该助手已存在，直接复用，无需新增——RV-8）。
3. **后端接口**：`market.get_instruments` 加 `instType` 白名单校验（非法→400，错误码 `BAD_INST_TYPE`）。
4. **前端类型**：`types.ts` 扩展 `Instrument` 与 `instType`。
5. **前端纯函数 + 测试**：写 `instruments.ts` 的 `inferInstType/filterAndRankInstruments/resolveWatchlistRows/MAX_VISIBLE_ROWS`，先补 `instruments.test.ts`（红→绿）。
6. **前端 store + 测试**：加 `watchlist/instSidebarTab` 与动作、`partialize`、`migrate` v6；补 `store.test.ts`。
7. **前端组件**：写 `InstrumentRow.tsx`；写 `InstrumentSidebar.tsx`（页签/搜索/合约 SWAP+FUTURES 双取数归并/子类型默认「全部」/自选归并/空·载·错态）；**删除 `InstrumentList.tsx` 及 `types.ts` 中孤儿 `Ticker` 接口**（RV-4）。
8. **接线**：`ChartWorkspace.tsx` 左列改 flex-col 并以 `InstrumentSidebar` 取代 `InstrumentList`，适度加宽。
9. **验证**：`pnpm test` + `pytest -q` 全绿；本地 `pnpm dev` 走 A1–A7 验收；`pnpm build` 通过。

---

## 11. 兼容性与约束对齐

- 遵循 CLAUDE.md：单文件 ≤800 行 / 方法 ≤50 行 / 形参 ≤5 / 嵌套 ≤4；命名自解释；错误显式传播；公开行为有测试。
- 遵循项目铁律：**绝不伪造行情**；外部 IO 留在 OKX 适配层 / 路由边界并带显式超时与错误；纯逻辑放 `apps/web/lib` 并配 `.test.ts`。
- 不特化桌面端源码（桌面复用同一 web/后端）。
- 向后兼容：`/instruments` 出参为「新增可选字段」，旧调用方（若有）不受影响。

---

## 12. 评审结论（Review Verdict）

**结论：有条件通过（Approved with conditions）**

本方案根因定位准确（已对照 `apps/web/lib/instruments.ts:7` 的 `filter(USDT).slice(0,40)` 字母截断逐字核实），技术路线与项目现状高度契合：`Tabs`（segmented 变体，`components/ui/index.tsx:129`）、设计 token（`--accent/--long/--short/--surface-2`）、`colorForReturn/fmtPrice/fmtPct`（`lib/format.ts`）、React Query、zustand persist、OKX 错误信封（`HTTPException` dict detail → `main.py` 包装）均为既有可复用资产，无需引入新运行时依赖（符合 N2）；测试基建（vitest 多份 `lib/*.test.ts` 与 `tests/test_okx_normalize.py`）齐备，测试计划可落地。**四维评审中无 P0 级问题；4 项 P1（RV-1~RV-4）与 6 项 P2（RV-5~RV-10）已全部在 v2 正文就地修复或采纳**，复核确认无遗留 P0/P1。

放行条件（下游代码开发节点须遵守，否则视为未达 v2 设计）：

1. **C-1（对应 RV-1）**：`watchlist`/`instSidebarTab` 默认值必须写进 **store 初始化器**（不能只放在 `migrate`），保证全新安装也有自选种子与有效页签。
2. **C-2（对应 RV-3）**：合约页签必须**同时取 SWAP+FUTURES 并归并**，子类型筛选默认「全部」；验收 A3 须实测「不切子类型也能搜到交割合约」。
3. **C-3（对应 RV-4）**：`buildInstOptions`、`InstrumentList.tsx`、孤儿 `Ticker` 接口必须一并删除，不得留下双路径死代码（CLAUDE.md §8）。
4. **C-4（对应 RV-2）**：自选页签把涉及类型的 query 结果归并为**单一 `Map<instId, Instrument>`** 后再调用 `resolveWatchlistRows`，与 §6.3 签名保持一致。
5. **C-5（铁律复核）**：严守「绝不伪造行情」——三页签取数失败一律显式错误态 + 重试，禁止任何兜底假数据；外部 IO 仅留在 OKX 适配层与路由边界。

满足上述条件后即可进入 Subtask #2 代码开发。

— 评审节点（Subtask #1），2026-06-20
