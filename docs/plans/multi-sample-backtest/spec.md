# 多样本回测（Multi-Sample Backtest）开发方案

> 版本：**v2**（评审节点修订；P0/P1 已就地修复，详见「评审记录」）
> 作者：方案设计 Agent（Subtask #0）｜评审：方案评审 Agent（Subtask #1）
> 适用范围：`apps/web`（Next.js 前端）+ `services/quant`（FastAPI 后端）
> 关联索引：`.claude-index/index.md`（Strategy Panel / Backtest flow / `agent/services.py`）

---

## 评审记录（Review Notes，v2 评审节点）

> 评审人：方案评审 Agent（Subtask #1，资深评审视角）。
> 评审方法：逐节将 spec 与真实代码对照核验——后端 `services/quant/app/agent/services.py`·`schemas.py`·`api/strategies.py`·`okx/rest_client.py`；前端 `apps/web/lib/store.ts`·`components/Strategy/StrategyDetail.tsx`·`StrategyPanel.tsx`·`components/ui/index.tsx`·`lib/api.ts`·`lib/types.ts`·`lib/instruments.ts`·`lib/export.ts`。
> 维度：可行性 / 完整性 / 一致性 / 规模适配。
> 结论摘要：**核心可行性成立**——`run_single_backtest(session, rest, *, strategy_id, inst_id, bar, ..., params=None)` 确实支持按标的覆盖（services.py:201-205），返回 `{runId, metrics, effectiveCandleCount, requestedBars, truncated}`；`Modal`/`Tabs(underline)`/`MetricsCards{metrics?,loading?}`/`inferInstType`/`equityToCsv`+`downloadCsv`/`ApiRequestError.code` 等原语全部在位。**无 P0**；下列 4 项 P1 已就地修复，6 项 P2 部分修复、其余记录在案。

### P1（已就地修复）

- **R-P1-1｜可行性·一致性｜“复用 `run_compare` 的逐项错误隔离”表述失实，per-symbol 异常契约欠精确。** 经核：`run_compare`（services.py:378-453）的标的内循环**没有** per-item `try/except`——它只在 prepare 阶段静默跳过坏策略（`_prepare_compare*` 的 `continue`）+ 协作式 `cancel_check`（`break`），任何运行期异常会中止整批。若实现者照搬其循环，会得到“一个标的失败即 500 整批”，直接违反验收 #4。**修复**：表述改为“多样本**新增** compare 所不具备的 per-symbol 隔离”，并钉死异常契约——`services.py` 顶部 `from ..okx.rest_client import OKXError`；循环内 `except HTTPException as e`（`error = e.detail["message"] if isinstance(e.detail, dict) else str(e.detail)`）与 `except OKXError as e`（`error = e.message`）分别落 `items[].status="error"`。见 §一、§2.1、§2.2、§八。
- **R-P1-2｜可行性·完整性｜工作量护栏的网络画像与 compare 不同，150k 阈值对本端点偏松。** `_COMPARE_SYNC_WORK_UNIT_LIMIT=150_000`（services.py:38）为 compare 标定：compare **只 `load_range_candles` 一次**并在多策略间共享（services.py:401）；多样本则**每个标的各自分页拉取 OKX K 线**，同样 150k 预算 ≈ `symbols` 倍网络往返与墙钟，叠加已知多标的 429 限频风险。**修复**：保留 `len(symbols) × effective_bars ≤ 150_000` 作**计算**护栏；补网络维度对策——严格顺序执行；已核实前端 `rawRequest`（api.ts:61-92）用裸 `fetch`、**无 AbortController/超时**，不会提前掐断长请求；单标的 429/超时被 per-symbol `OKXError` 捕获为该标的错误而非拖垮整批；文案提示大批量时收窄区间。标的上限维持 20 并在风险表标注墙钟上界。见 §4.1、§八。
- **R-P1-3｜可行性·完整性｜接口示例的错误消息与真实 `raise` 不符，`error` 抽取契约缺失。** 真实 `EMPTY_RANGE` 为 `HTTPException(400, detail={"code":"EMPTY_RANGE","message":"No confirmed candles in requested range"})`（英文；中文“区间内无已确认 K 线”仅在 docstring）。spec §4.1 示例把 `error` 写成中文消息，且未定义 `items[].error` 如何从被捕获异常抽取。**修复**：示例改为真实消息，并明确 `error` 抽取规则（同 R-P1-1），保证 `error` 确定且非空。见 §4.1。
- **R-P1-4｜一致性｜`StrategyDetail` 直接读 store 破坏其“纯 props”范式与 spec 自述的单一真相源原则。** 现状：`StrategyDetail` 主组件**全部数据经 props**由 `StrategyPanel`（已读 store、已下发约 12 个 props）注入，仅嵌套子件 `FactorStrategyBanner` 用了两个 setter。spec §2.2 让其直接 `useAppStore` 读 `watchlist/instId/bar`，既与既有 props 范式冲突，也与 spec 自己“`startTs/endTs` 由 `StrategyPanel` 组装以避免双份”自相矛盾，并抬高组件测试的 store mock 成本。**修复**：`watchlist/instId/bar` 由 `StrategyPanel` 作 props 传入，组件保持表现层、可纯 props 测试。见 §2.2、§三。

### P2（部分修复 / 记录在案）

- **R-P2-1｜一致性（已修复文案）｜`mode==="backtest" && backtestKind==="simple"` 条件无字面形式。** `StrategyDetail` 的 `mode` 仅 `"backtest" | "paper"`；simple 是 `backtestKind==="walkforward" ? null : (...)` 的 else 分支。多样本按钮渲染条件应表述为 `mode==="backtest" && backtestKind!=="walkforward"`。已在 §2.2 修正。
- **R-P2-2｜完整性（已修复）｜默认勾选 `currentInstId` 未约束其须在自选内。** 若当前标的不在 `watchlist`，默认勾选会选中列表外标的（或与空自选态冲突）。已改为“仅当 `currentInstId ∈ watchlist` 时默认勾选，否则不预选”。见 §4.3、§6.1。
- **R-P2-3｜完整性·一致性（已补要求）｜自绘页签条丢失 `Tabs` 原语本会提供的 a11y。** 不复用 `Tabs`（其 `index.tsx:353` 注释「R-2」明确不加 `overflow-x` 以免裁掉下划线）以支持横向滚动是**合理**的；但自绘 tab 条须补回 `role="tablist"/role="tab"/aria-selected` 与方向键导航。见 §6.2。
- **R-P2-4｜完整性（已补要求）｜前端无全局 vitest setup，jest-dom 须逐文件引入。** `vitest.config.ts` 无 `setupFiles`，既有测试逐文件 `import "@testing-library/jest-dom/vitest"`。新 `*.test.tsx` 须照此引入；§7.3 保留的 `NODE_ENV=test` 提示与既有约定一致，予以保留。见 §7.3。
- **R-P2-5｜完整性（已修复文案）｜互清范围需与现实对齐。** `clearBacktestResult`（StrategyPanel:95-102）现已清 `runId/metrics/coverage/comboResult/walkForwardResult` 并 `setChartView("live-follow")`；接入时把 `multiResult` 纳入其中即可；跑多样本成功时除清单回测态外亦应清 `walkForwardResult` 以免残留。见 §2.2、§八。
- **R-P2-6｜完整性（记录在案）｜per-symbol 捕获会把“系统性错误”放大为 N 个相同错误页签。** 例如策略本身不可实例化时，每个标的都落同样的 error。本期可接受（错误隔离优先），记录为已知局限，未来可加“全部相同错误→上抛请求级”聚合。

### 核对为真、可放心复用（无需改动）

`run_single_backtest` 的 `inst_id` 覆盖与返回形状为真；combo 经 `_is_combo` 分流 `_run_saved_combo`（返回 `{runId, metrics, combo}`，无覆盖字段），factor_score（`kind="factor"`）走通用原子撮合链，三类策略零额外适配成立；`BacktestReq/CompareReq` 用字面 camelCase 字段（**无** `alias_generator`/`model_config`）+ `@model_validator(mode="after")` 校验 `startTs<endTs`；路由 `POST /strategies/{id}/backtest` 路径范式与 `HTTPException(detail={code,message})` 约定一致；`watchlist:string[]`、`chartView` 含 `"live-follow"`、store 持久化键 `aragon-ui` 均为真（注：`chartView` 不在 `partialize`，属瞬态，不影响本设计）。

---

## 一、Overview（概述）

当前回测能力只覆盖「单标的、单策略、单次」场景：用户在右侧「交易策略」面板（`StrategyPanel`）选中某个策略、在 `StrategyDetail` 中编辑参数后点击「运行回测」，系统对**图表当前选中的那个标的**（`store.instId`）、当前周期（`store.bar`）、当前回测区间（`store.backtestStartTs ~ backtestEndTs`）跑一次回测，结果落库为一条 run，并在下方「绩效指标」页签用 `MetricsCards` 呈现。这导致用户要评估「同一个策略在 BTC / ETH / SOL 等多个标的上是否同样稳健」时，必须反复切换图表标的、逐个手动重跑，效率低且无法横向对照——这正是需求中所说「不完善」的地方。

本方案在**不改变现有单标的回测路径**的前提下，新增「多样本回测」能力：在 `StrategyDetail` 的「运行回测」按钮右侧、「另存为」按钮左侧加入一个「多样本回测」按钮；点击后弹出一个美观、符合人机交互最佳实践的弹窗面板（`MultiSampleBacktestModal`），罗列用户的全部自选标的（`store.watchlist`），用户勾选需要回测的标的、点「确定」后，对**同一策略、同一参数、同一周期、同一区间**逐个标的运行回测；回测完成后，结果回填到下方「绩效指标」区域，并**按标的分页签**展示，方便逐个标的查看其绩效卡片与净值导出。

设计上遵循项目两条既有约定：（1）后端「一次编排、逐项落库 + 工作量护栏」的 `run_compare` 范式——把「多策略 × 单标的」转置为「单策略 × 多标的」，复用 `run_single_backtest` 以零成本兼容原子 / 因子 / 组合策略。**注（评审 R-P1-1）**：`run_compare` 本身**并不**对运行期异常做 per-item 隔离（它只在 prepare 阶段静默跳过坏策略 + 协作式取消），因此多样本**新增** compare 所不具备的「每个标的 `try/except (HTTPException, OKXError)` 错误隔离」（一个标的失败不拖垮整批，落 `items[].status="error"`）——这是本设计相对 compare 的增量，不可照搬 compare 的裸循环；（2）前端「纯函数下沉到 `lib/`、UI 原语复用 `components/ui`」——弹窗与结果页签复用 `Modal`/`Button`/`Input`/`Tabs`/`MetricsCards`/`Skeleton`，逻辑（去重、排序、汇总、标签）下沉到可测的 `lib/multiSample.ts`。多样本回测**不劫持主图表**（不切换 `instId` / 不改 `chartView`），只在绩效区内自洽展示，避免触碰 `chartSync` 与图表叠加的复杂度（详见非目标）。

---

## 二、Technical Design（技术设计）

### 2.1 架构与数据流

```text
StrategyDetail（按钮「多样本回测」+ 弹窗 MultiSampleBacktestModal）
  └─(确定, instIds[], params)→ StrategyPanel.multiBacktest.mutate
        └─ runMultiSampleBacktest(id, params, instIds, bar, startTs, endTs)
              ├─ 若 params 改动：await api.updateStrategy(id, {params})   // 与单回测同口径
              └─ await api.multiBacktest(id, {instIds, bar, startTs, endTs})
                    └─ POST /api/py/strategies/{id}/multi-backtest（Next rewrite → FastAPI）
                          └─ services.run_multi_sample_backtest(...)
                                ├─ get_strategy_row(id)  // 不存在 → 404（整请求级）
                                ├─ dedupe(instIds) + 工作量护栏（symbols × bars）
                                └─ for inst_id in symbols:                          // 严格顺序执行（受护栏约束）
                                      try: run_single_backtest(...,inst_id=inst_id)  // 复用既有：原子/因子/组合都走得通，各落一条 run
                                      except HTTPException as e: 记 {status:"error", error: e.detail["message"] if dict else str(e.detail)}
                                      except OKXError as e:      记 {status:"error", error: e.message}   // OKXError 须 from ..okx.rest_client import
                                → {strategyId, bar, startTs, endTs, items:[{instId,status,runId,metrics,coverage...}|{instId,status:"error",error}]}
        └─ onSuccess(data): setMultiResult(data) + 清空单回测态(runId/metrics/comboResult) + setTab("metrics")
              └─ 绩效区渲染 MultiSampleResults（按标的分页签：MetricsCards + 覆盖横幅 + 该标的净值导出）
```

### 2.2 关键代码路径

- **触发点**：`apps/web/components/Strategy/StrategyDetail.tsx` 第 239–270 行的按钮行。当前结构为 `[运行回测 | 另存为]`，改造为 `[运行回测 | 多样本回测 | 另存为]`。多样本回测按钮的渲染条件为 `mode === "backtest" && backtestKind !== "walkforward"`（评审 R-P2-1：`StrategyDetail` 的 `mode` prop 仅 `"backtest" | "paper"`，simple 是 `backtestKind === "walkforward" ? null : (...)` 的 else 分支，代码中**无** `=== "simple"` 字面比较；walk-forward 模式无单次回测语义，不展示）。按钮 `onClick` 打开本组件内自管的弹窗 `open` 状态。
- **弹窗数据源（评审 R-P1-4，改为 props 注入）**：`watchlist`（`string[]`，有序去重，形如 `"BTC-USDT-SWAP"`）、`instId`（当前标的，用于默认勾选）、`bar`（展示上下文）由 `StrategyPanel`（已读 store）作为**新增 props** 传给 `StrategyDetail`，`StrategyDetail` **不直接 `useAppStore`**，保持其现有「纯 props 表现层」范式与可纯 props 测试性。回测区间 `backtestStartTs/backtestEndTs` 同理仅用于展示上下文文案，真正发请求时由 `StrategyPanel` 用其 store 值组装（单一真相源，避免双份）。
- **参数透传**：弹窗「确定」回调 `onMultiBacktest(instIds, params)` 把 `StrategyDetail` 本地 `params`（用户可能刚编辑过）一并上抛；`StrategyPanel` 的 `runMultiSampleBacktest` 先 `api.updateStrategy(id,{params})` 持久化、再调多样本端点——与单回测 `runBacktest` 完全同口径，保证「编辑参数后多样本回测」生效。
- **后端编排**：`services/quant/app/agent/services.py` 新增 `run_multi_sample_backtest`，内部对每个标的调用既有 `run_single_backtest`。后者已自动分流：组合策略（`kind=="combo"`）经 `_is_combo` 走 `_run_saved_combo`、因子策略（`factor_score`，`kind=="factor"` 但本质 atomic）走通用撮合链，故多样本对三类策略**零额外适配**。每个标的各自 `load_range_candles` + 落一条 run（`runId` 各不相同）。**异常隔离（评审 R-P1-1）**：`services.py` 需 `from ..okx.rest_client import OKXError`（该类不在 services.py，而在 `okx/rest_client.py:21`），循环内自带 per-symbol `try/except`——这是 `run_compare` 所无的增量能力。
- **结果渲染**：`StrategyPanel` 新增 `multiResult` 状态；绩效页签内当 `multiResult || multiBacktest.isPending` 为真时渲染 `MultiSampleResults`，否则走既有 `ComboResult`/`MetricsCards` 分支；多样本成功 `onSuccess` 内 `setTab("metrics")`（`setTab` 即绩效/历史页签的 `useState` setter，现默认 `"metrics"`，新增显式切换以防用户停在「历史」页签）。两类结果互斥（评审 R-P2-5，与真实 `clearBacktestResult` 对齐）：把 `setMultiResult(null)` 并入既有 `clearBacktestResult`（其现已清 `runId/metrics/coverage/comboResult/walkForwardResult` 并 `setChartView("live-follow")`）；跑单回测的 `onSuccess` 清 `multiResult`；跑多样本的 `onSuccess` 复用 `clearBacktestResult` 清单回测态（含 `walkForwardResult` 残留、并 `setChartView("live-follow")` 收起旧 run 叠加）后再写 `multiResult`。

### 2.3 操作时序（happy path）

1. 用户在 backtest/simple 模式选中策略 → 编辑参数 → 点「多样本回测」。
2. 弹窗打开：默认勾选当前 `instId`（仅当其 ∈ `watchlist`，见 R-P2-2）；用户增选若干自选标的；点「确定（开始回测）」。
3. 弹窗即时关闭（乐观关闭）；绩效区进入 loading（`MultiSampleResults loading`）。
4. 前端 `updateStrategy`（若参数变更）→ `POST /strategies/{id}/multi-backtest`。
5. 后端逐标的回测、各自落库，聚合返回 `items[]`。
6. `onSuccess` 写入 `multiResult`、清单回测态、`setTab("metrics")`。
7. 绩效区渲染标的页签条；激活页签展示该标的的 `MetricsCards`（+ `truncated` 覆盖横幅 + 「导出净值 CSV」）。错误标的页签展示错误卡片。

---

## 三、File / Module Change Plan（文件 / 模块变更计划）

| 文件 | 新建/修改 | 一句话意图 |
|---|---|---|
| `services/quant/app/schemas.py` | 修改 | 新增 `MultiSampleBacktestReq`（`instIds: list[str] = Field(min_length=1, max_length=20)` + bar/区间/成本字段；用**字面 camelCase 字段名**对齐前端，**不**加 `alias_generator/model_config`；`@model_validator(mode="after")` 校验 `startTs < endTs`），紧邻 `CompareReq`。 |
| `services/quant/app/agent/services.py` | 修改 | 新增 `run_multi_sample_backtest()` 与常量 `_MULTI_SAMPLE_MAX_SYMBOLS=20`、字符串去重 `_dedupe_strs()`；逐标的复用 `run_single_backtest`，**自带 per-symbol `try/except`（compare 所无）** + 工作量护栏；顶部 `from ..okx.rest_client import OKXError`（R-P1-1）。 |
| `services/quant/app/api/strategies.py` | 修改 | 新增路由 `POST /strategies/{strategy_id}/multi-backtest`，薄封装转调 service（对齐既有 `backtest`/`compare` 路由写法）。 |
| `apps/web/lib/types.ts` | 修改 | 新增 `MultiSampleItem` / `MultiSampleResult` 接口（紧邻 `CompareItem`/`CompareResult`）。 |
| `apps/web/lib/api.ts` | 修改 | 新增 `multiBacktest(id, body)` 客户端方法（`request<MultiSampleResult>`），紧邻 `compare`。 |
| `apps/web/lib/multiSample.ts` | 新建 | 纯函数：`MULTI_SAMPLE_MAX_SYMBOLS`、`dedupeInstIds`、`splitInstId`（base/quote）、`summarizeMultiSample`（best/worst/成功失败计数）、`multiSampleTabTone`。 |
| `apps/web/lib/multiSample.test.ts` | 新建 | 覆盖上述纯函数正常/边界（空、全失败、去重、单标的）。 |
| `apps/web/components/Strategy/MultiSampleBacktestModal.tsx` | 新建 | 自选标的勾选弹窗（复用 `Modal`/`Input`/`Button`；搜索/全选/清空/计数/上限提示/空态；默认勾选当前标的）。 |
| `apps/web/components/Strategy/MultiSampleBacktestModal.test.tsx` | 新建 | 组件测试：渲染自选、勾选/全选/清空、上限禁用、空态、确定回调入参、Esc/遮罩关闭。 |
| `apps/web/components/Strategy/MultiSampleResults.tsx` | 新建 | 按标的分页签的结果区（标的 tab 条 + 该标的 `MetricsCards`/覆盖横幅/净值导出；错误页签错误卡片；loading 骨架）。 |
| `apps/web/components/Strategy/MultiSampleResults.test.tsx` | 新建 | 组件测试：多标的页签切换、ok/error 混合、loading、空 items。 |
| `apps/web/components/Strategy/StrategyDetail.tsx` | 修改 | 按钮行插入「多样本回测」按钮（运行回测右、另存为左，条件 `mode==="backtest" && backtestKind!=="walkforward"`）+ 自管弹窗开关 + **新增 props `watchlist/instId/bar`（由 StrategyPanel 注入，不直接读 store——R-P1-4）** + 新增 prop `onMultiBacktest`。 |
| `apps/web/components/Strategy/StrategyPanel.tsx` | 修改 | 新增 `multiResult` 状态与 `multiBacktest` mutation；**向 StrategyDetail 下发 `watchlist/instId/bar` props**；绩效区按 `multiResult` 分支渲染；`actionBusy` 纳入 `multiBacktest.isPending`；把 `setMultiResult(null)` 并入 `clearBacktestResult`，单回测/多样本 onSuccess 互清（多样本 onSuccess 复用 `clearBacktestResult`）。 |
| `services/quant/tests/test_multi_sample_backtest.py` | 新建 | 后端测试：多标的成功聚合、单标的失败隔离、404 整请求级、上限/工作量 413、去重。 |
| `docs/plans/multi-sample-backtest/spec.md` | 新建（本文件） | 本开发方案。 |

> 阈值自查（CLAUDE.md §二）：`StrategyDetail.tsx` 现 273 行，新增约 30 行（按钮 + 弹窗开关 + 新增 props 透传），远低于 800；弹窗/结果各拆独立文件，单文件均 < 300 行；新增方法均 < 50 行；`run_multi_sample_backtest` 主体为「校验 + 单层 for + try/except」，圈复杂度 < 10、嵌套 < 4。

---

## 四、Interface Design（接口设计）

### 4.1 新增 REST 端点

```
POST /api/py/strategies/{strategy_id}/multi-backtest
```

请求体（`MultiSampleBacktestReq`，camelCase，对齐 `BacktestReq`/`CompareReq`）：

```jsonc
{
  "instIds": ["BTC-USDT-SWAP", "ETH-USDT-SWAP"],  // min 1, max 20；服务端再 dedupe
  "bar": "1H",
  "limit": null,                                   // 可选；仅作 max_bars 安全上限（同 BacktestReq）
  "startTs": 1700000000,                           // ge 0；校验 startTs < endTs
  "endTs": 1710000000,
  "initialCapital": null,                          // 可选成本覆盖
  "fees": null,
  "slippage": null,
  "leverage": null
}
```

成功响应（200，`MultiSampleResult`）：

```jsonc
{
  "strategyId": 7,
  "bar": "1H",
  "startTs": 1700000000,
  "endTs": 1710000000,
  "items": [
    { "instId": "BTC-USDT-SWAP", "status": "ok",
      "runId": 1201, "metrics": { "totalReturn": 0.12, "winRate": 0.55, "profitFactor": 1.4,
        "maxDrawdown": -0.08, "sharpe": 1.1, "tradeCount": 23, "avgPnl": 5.2 },
      "effectiveCandleCount": 2400, "requestedBars": 2400, "truncated": false },
    { "instId": "DOGE-USDT-SWAP", "status": "error",
      "error": "No confirmed candles in requested range" }  // 真实 EMPTY_RANGE message（英文，见 R-P1-3）
  ]
}
```

错误响应（沿用项目 `HTTPException(detail={code,message})` 约定，前端 `ApiRequestError.code` 可读）：

- `404 NOT_FOUND`：策略不存在（整请求级，循环前 `get_strategy_row(id)` 显式抛出；区别于 compare 对未知 id 的静默跳过）。
- `413 MULTI_SAMPLE_TOO_LARGE`：`len(symbols) * effective_bars > _COMPARE_SYNC_WORK_UNIT_LIMIT(150_000)`，提示缩小区间或减少标的。`effective_bars` 复用 `_compare_effective_bars`（由区间 + bar 推算，**无需预先 `load_range_candles`**，故护栏可在循环前求值）。
- `422`：`instIds` 为空或超过 20（Pydantic `Field(min_length=1, max_length=20)`）/ `startTs >= endTs`（`@model_validator(mode="after")`）。

> **单标的级失败**不走 HTTP 错误，而是落到 `items[].status="error"`，保证错误隔离与部分成功可见。`error` 字段抽取契约（评审 R-P1-1/R-P1-3）：`HTTPException` → `e.detail["message"] if isinstance(e.detail, dict) else str(e.detail)`（真实 `EMPTY_RANGE` 消息为英文 `"No confirmed candles in requested range"`，非 docstring 中的中文）；`OKXError` → `e.message`。务必保证 `error` 非空。
>
> **413 护栏的网络维度补充（评审 R-P1-2）**：`_COMPARE_SYNC_WORK_UNIT_LIMIT` 原为 compare 标定，compare **只拉一次** K 线并在多策略间共享；多样本对**每个标的各自分页拉取**，同等 150k 计算预算 ≈ `symbols` 倍网络往返与墙钟，并放大 OKX 429 限频概率。对策：① `symbols × effective_bars ≤ 150_000` 仅作**计算**护栏并保留 20 标的硬上限；② **严格顺序执行**（不并发，避免叠加限频）；③ 前端 `rawRequest`（api.ts:61-92）为裸 `fetch`、**无超时**，不会提前掐断长请求；④ 单标的 429/超时由 per-symbol `OKXError` 捕获为该标的错误，不拖垮整批；⑤ 弹窗文案提示大批量时收窄区间。流式逐标的进度为本期非目标。

### 4.2 前端 API 客户端（`lib/api.ts`）

```ts
multiBacktest: (
  id: number,
  body: { instIds: string[]; bar: string; limit?: number; startTs: number; endTs: number;
          initialCapital?: number; fees?: number; slippage?: number; leverage?: number },
) =>
  request<MultiSampleResult>(`/strategies/${id}/multi-backtest`, {
    method: "POST",
    body: JSON.stringify(body),
  }),
```

### 4.3 前端组件契约

- `MultiSampleBacktestModal`：props = `{ open, onClose, watchlist: string[], currentInstId: string, bar: string, startTs: number, endTs: number, strategyName: string, busy?: boolean, onConfirm: (instIds: string[]) => void }`。内部维护 `selected: Set<string>`（默认含 `currentInstId`**仅当 `currentInstId ∈ watchlist`**，否则不预选——评审 R-P2-2）与 `query`；「确定」时 `onConfirm([...selected])` 后由调用方关闭。
- `MultiSampleResults`：props = `{ result: MultiSampleResult | null, loading?: boolean }`。内部维护 `activeInstId`；激活项 `status==="ok"` 时用 `useQuery(["equity", runId], api.equity)` 懒取净值供「导出净值 CSV」。

---

## 五、Data Model（数据模型）

无新增数据库表 / 迁移。复用既有 `strategy_runs` / `trades` / `equity`：每个标的的回测各落一条 run（`run_single_backtest` 既有行为），因此多样本结果天然可在「历史」页签按标的回看。

新增内存 / 传输形状（`apps/web/lib/types.ts`）：

```ts
export interface MultiSampleItem {
  instId: string;
  status: "ok" | "error";
  runId?: number;               // status==="ok"
  metrics?: Metrics;            // status==="ok"，复用既有 Metrics
  effectiveCandleCount?: number;
  requestedBars?: number;
  truncated?: boolean;
  combo?: ComboPreviewResult["combo"]; // 组合策略多样本时透传（可选）
  error?: string;              // status==="error"
}

export interface MultiSampleResult {
  strategyId: number;
  bar: string;
  startTs: number;
  endTs: number;
  items: MultiSampleItem[];     // 顺序与请求 instIds 去重后一致
}
```

后端 `items[]` 元素构造：`run_single_backtest` 成功返回 `{runId, metrics, effectiveCandleCount?, requestedBars?, truncated?, combo?}`，直接 `{"instId": inst_id, "status": "ok", **res}` 展开；失败构造 `{"instId": inst_id, "status": "error", "error": <message>}`。

---

## 六、UI / UX 设计（弹窗与结果页签）

### 6.1 `MultiSampleBacktestModal`（顶级设计 + HCI 最佳实践）

- **载体**：复用 `components/ui` 的 `Modal`（Portal 居中、遮罩、焦点陷阱、Esc/点遮罩关闭、`body` 滚动锁、`aria-modal`/`aria-labelledby`、卸载焦点回填）——无障碍与明暗主题零额外适配。
- **结构**（从上到下）：
  1. 头部：标题「多样本回测」（`id` 供 `aria-labelledby`）+ 副标题上下文「{strategyName} · {bar} · {区间日期}」+ 右上关闭按钮。
  2. 工具条：搜索 `Input`（按 instId/base 过滤自选）+「全选」「清空」`Button` + 右侧计数徽标「已选 N / M」。
  3. 列表：自选标的逐行——左 `checkbox`（`accent-[var(--accent)]`，可聚焦）、中 `BASE / QUOTE` + 类型徽标（现货/永续/交割，由 `inferInstType` 推断）、当前标的加「当前」`Badge`。整行可点切换选中；`max-h` 限高 + `overflow-y-auto`。
  4. 空态：`watchlist` 为空时展示「自选列表为空，请先在左侧标的栏添加自选」引导文案，确定禁用。
  5. 底部：左「已选 N 个标的」（超过上限时红字提示「最多 {MAX} 个」）+ 右「取消 / 确定（开始回测）」。确定在 `选中数===0 || 选中数>MAX || busy` 时禁用；`busy` 时文案「回测中…」。
- **交互细则**：默认勾选 `currentInstId`（**仅当其在 `watchlist` 内**，使其常在结果中可见，降低空选；不在自选时不预选——评审 R-P2-2）；点击行与点击 checkbox 等价；键盘可达（Tab 进入、Space 切换、Esc 关闭）；遵循「确认即关闭、loading 在面板内」减少弹窗驻留焦虑。

### 6.2 `MultiSampleResults`（绩效区按标的分页签）

- **页签条**：自绘横向 tab 条（`flex gap overflow-x-auto`，下划线高亮，复刻 `Tabs underline` 视觉但允许横向滚动以容纳多标的——`components/ui` 的 `Tabs` 在 `index.tsx:353` 有「评审 R-2」注释明确不加 `overflow-x`（否则 `overflow-y` 被算成 auto 会裁掉 `-mb-px` 下划线），故此处自绘是合理取舍）。**a11y（评审 R-P2-3）**：自绘需补回 `Tabs` 原语本会提供的语义——容器 `role="tablist"`、每个 tab `role="tab"` + `aria-selected`、内容区 `role="tabpanel"`，并支持 `←/→` 方向键切换。每个 tab 标签为 `instId`，附微缩状态：`ok` 显示总收益率着色徽标（涨绿跌红）、`error` 显示「!」红点。
- **激活内容**：`ok` → `truncated` 覆盖横幅（复用单回测文案）+ `MetricsCards metrics` + 「导出净值 CSV」（懒取该标的 `runId` 净值，文件名 `aragon-equity-{instId}-run{runId}.csv`）；`error` → 错误卡片（`var(--short)` 边框 + 该标的 `error` 文案 + 「该标的回测失败，可缩小区间或确认标的有效后重试」）。
- **loading**：`loading && !result` 时展示页签骨架 + `MetricsCards loading`（6 张骨架卡），与单回测加载视觉一致。

---

## 七、Testing & Acceptance Criteria（测试与验收）

### 7.1 后端（pytest，`services/quant/tests/test_multi_sample_backtest.py`，复用既有 fixtures / 临时 DB；注意 services/quant 测试共享一份临时 DB 的隔离约定）

- `test_multi_sample_aggregates_per_symbol`：2 个有效标的 → `items` 长度 2、均 `status=="ok"`、各有不同 `runId`、`metrics` 字段齐全。
- `test_multi_sample_isolates_failing_symbol`：1 有效 + 1 触发 `run_single_backtest` 抛 `HTTPException(EMPTY_RANGE)` → 前者 ok、后者 `status=="error"` 且 `error` 非空，请求整体 200。
- `test_multi_sample_strategy_not_found_404`：不存在 `strategy_id` → 整请求 404 `NOT_FOUND`。
- `test_multi_sample_dedup_preserves_order`：传入含重复 instIds → 去重后顺序稳定，`items` 无重复。
- `test_multi_sample_workload_guard_413`：标的数 × 区间根数超阈 → 413 `MULTI_SAMPLE_TOO_LARGE`（可用极小 `_COMPARE_SYNC_WORK_UNIT_LIMIT` monkeypatch 或大区间构造）。
- `test_multi_sample_req_validation`：空 `instIds` / 超 20 / `startTs>=endTs` → 422。

### 7.2 前端纯函数（vitest，`apps/web/lib/multiSample.test.ts`）

- `dedupeInstIds` 去重保序；`splitInstId("BTC-USDT-SWAP")` → `{base:"BTC", quote:"USDT"}`；`summarizeMultiSample` 正确给出 best/worst（按 `totalReturn`）与成功/失败计数；全失败 / 空 items 不抛错。

### 7.3 前端组件（vitest + Testing Library，**须 `NODE_ENV=test`** 以免 React act 报错）

> 测试基建注意（评审 R-P2-4）：`apps/web/vitest.config.ts` **无 `setupFiles`**，jest-dom 匹配器须**逐文件** `import "@testing-library/jest-dom/vitest";`（参照 `NavRail.test.tsx` 等）。新建的 `MultiSampleBacktestModal.test.tsx` / `MultiSampleResults.test.tsx` 须各自引入。

- `MultiSampleBacktestModal.test.tsx`：渲染自选行数；默认勾选当前标的；全选/清空改变计数；选中 0 时确定禁用；空 watchlist 显示空态；点确定回调收到去重后的 `instIds`；Esc/遮罩触发 `onClose`。
- `MultiSampleResults.test.tsx`：渲染多标的页签；切换页签切换内容；`ok`/`error` 混合分别渲染卡片/错误；`loading` 显示骨架；空 items 安全降级。
- `StrategyPanel.test.tsx`（增量）：多样本回测按钮存在且仅 backtest/simple 出现；点击打开弹窗；mutation onSuccess 后绩效区出现标的页签。

### 7.4 验收标准（Definition of Done，对应需求逐条）

1. 「多样本回测」按钮出现在「运行回测」右侧、「另存为」左侧（backtest/simple 模式）。
2. 点击弹出面板，内含**全部自选标的**且可勾选；面板美观、可键盘操作、Esc/遮罩可关。
3. 勾选 + 确定后，对所选每个标的用**同一策略/参数/周期/区间**各跑一次回测。
4. 结果显示在下方绩效指标区域，**按标的分页签**分别展示其绩效卡片；单个标的失败不影响其余标的结果展示。
5. 既有单标的「运行回测」/「另存为」/walk-forward/组合/对比路径行为不变（回归通过）。
6. `pnpm test`（web）与 `pytest`（quant）相关用例全绿；项目可编译启动。

---

## 八、Risks & Mitigations（风险与对策）

| 风险 | 影响 | 对策 |
|---|---|---|
| 多标的 × 长区间同步回测耗时长（**评审 R-P1-2：网络画像比 compare 更重**） | 请求长时间挂起、墙钟随标的数线性增长、放大 OKX 429 限频 | **计算**护栏 `symbols × effective_bars ≤ 150_000`（超则 413）+ 标的上限 20 + 前端常量 `MULTI_SAMPLE_MAX_SYMBOLS=20` 禁用超额勾选。**网络维度补充**：compare 只拉一次 K 线共享，多样本每标的各自分页拉取（≈`symbols` 倍往返），故①严格顺序执行不并发；②已核实前端 `rawRequest` 裸 `fetch` 无超时、不会提前掐断；③单标的 429/超时落 per-symbol 错误不拖垮整批；④面板内 loading + 文案提示大批量收窄区间。WS 流式进度为非目标。 |
| 单标的 OKX 无数据 / 标的失效导致整批失败 | 部分可用结果丢失 | 每标的自带 `try/except`（**注意 R-P1-1：`run_compare` 无此能力，须新写**）：`HTTPException`/`OKXError` 分别捕获，落 `items[].status="error"`（`error` 按 R-P1-1 抽取规则取非空消息）；`OKXError` 须 `from ..okx.rest_client import`；其余标的照常返回。 |
| 用户在 `StrategyDetail` 改了参数但多样本用旧参数 | 结果与预期不符 | 与单回测同口径：确定前 `api.updateStrategy(id,{params})` 持久化再回测。 |
| 多样本结果与单回测结果状态串台（图表叠加/指标残留） | 误读绩效 | 两态互斥（评审 R-P2-5）：把 `setMultiResult(null)` 并入既有 `clearBacktestResult`（其已清 `runId/metrics/coverage/comboResult/walkForwardResult` 并 `setChartView("live-follow")`）；跑多样本 onSuccess 先调 `clearBacktestResult` 再写 `multiResult`；跑单回测 onSuccess 清 `multiResult`；切换策略 `clearBacktestResult` 同时清两者。 |
| 标的页签过多横向溢出 | 页签不可见 | `MultiSampleResults` 自绘可横向滚动 tab 条（不复用受限的 `Tabs` 原语）；上限 20 控制规模。 |
| 自选为空时无法多样本 | 功能不可用 | 弹窗空态文案引导用户先添加自选；确定禁用。 |
| 组合策略多样本是否支持 | 范围争议 | 本期入口仅在 `StrategyDetail`（原子/因子，需求锚点「另存为左侧」也只在此处存在）。后端 `run_single_backtest` 已兼容组合，故若未来在 `ComboStrategyDetail` 加入口可零后端改动复用——本期非目标，spec 留出扩展点。 |

### 非目标（Non-Goals，明确划界）

- 不劫持主图表：多样本**不**切换 `store.instId`、不设 `runId`/`chartView`（避免触碰 `chartSync` 与 run 叠加）。各标的净值仅在结果页签内按需懒取展示/导出。
- 不做流式逐标的进度（一次性返回整批），不做并发回测（顺序执行，受护栏约束）。
- 不新增持久化偏好（勾选态不入 `aragon-ui` persist；每次打开按当前 `instId` 默认勾选）。
- 不改动 walk-forward / compare / paper / 因子层既有路径。

---

## 九、实施顺序建议（供代码开发节点参考）

1. 后端：`schemas.MultiSampleBacktestReq` → `services.run_multi_sample_backtest` → `api` 路由 → `tests/test_multi_sample_backtest.py`。
2. 前端数据层：`types.ts`（新增接口）→ `api.ts`（`multiBacktest`）→ `lib/multiSample.ts` + 测试。
3. 前端 UI：`MultiSampleBacktestModal` + 测试 → `MultiSampleResults` + 测试。
4. 接线：`StrategyDetail`（按钮 + 弹窗 + prop）→ `StrategyPanel`（state + mutation + 绩效分支 + 互清）→ `StrategyPanel.test.tsx` 增量。
5. 自查 CLAUDE.md §八 8 条评审清单 + 跑 `pnpm test` / `pytest`。

---

## 评审结论（Review Verdict）

**有条件通过（Approved with conditions）。**

整体评估：方案的核心技术路径**经真实代码核验成立且实现成本低**——`run_single_backtest(*, inst_id=...)` 原生支持按标的覆盖并返回所需字段；combo/factor/atomic 三类策略零额外适配为真；前端 `Modal`（八项无障碍特性实装）/`MetricsCards`/`Tabs`/`inferInstType`/`equityToCsv`+`downloadCsv` 等原语全部在位；后端 schema/路由/错误约定一致。规模适配恰当（15 项变更含测试，纯逻辑下沉 `lib/`，无新表无迁移），未见过度设计。**无 P0**；4 项 P1 均已在 v2 正文就地修复，6 项 P2 已修复 4 项、补充要求 2 项。

放行需代码开发节点（Subtask #2）落实以下条件：

1. **（R-P1-1/R-P1-3）异常隔离须自写**：不可照搬 `run_compare` 裸循环；`run_multi_sample_backtest` 必须自带 per-symbol `try/except`，`import OKXError`，并按抽取规则保证 `items[].error` 非空。后端测试须含「单标的失败隔离、整体 200」用例（§7.1 已列）。
2. **（R-P1-2）墙钟与限频**：严格顺序执行；保留 20 标的硬上限 + 150k 计算护栏；不得给请求加激进客户端超时。
3. **（R-P1-4）`StrategyDetail` 保持纯 props**：`watchlist/instId/bar` 由 `StrategyPanel` 注入，组件内不新增 `useAppStore` 取值。
4. **（R-P2-3）自绘页签条补 a11y**：`role="tablist"/tab/tabpanel"` + `aria-selected` + 方向键。
5. **（R-P2-6，已知局限）**：per-symbol 捕获会把系统性错误放大为 N 个相同错误页签，本期接受，留作后续聚合优化。
6. **人验门**：弹窗「顶级设计 + HCI」与按标的页签切换属交互体验项，须在真实 Windows 环境人工点验后方可视为达成（参照本仓既有 UI 改动的真机点击约定）。

满足上述条件即视为方案评审通过，可进入代码开发。
